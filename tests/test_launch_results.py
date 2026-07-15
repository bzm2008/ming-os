import importlib.util
import json
import pathlib
import socket
import tempfile
import threading
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"


def load_launch():
    spec = importlib.util.spec_from_file_location("ming_launch_results", LAUNCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LaunchResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_launch()

    def test_structured_event_records_desktop_file_and_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "events.jsonl"
            request = self.launch.LaunchRequest(
                ("missing-app",), source="desktop",
                desktop_file="/usr/share/applications/missing.desktop",
            )
            self.launch.record_launch_event(request, "command_missing", "not found", path=path)
            event = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("command_missing", event["status"])
            self.assertEqual(request.desktop_file, event["desktop_file"])
            self.assertEqual("desktop", event["source"])

    def test_broker_records_spawn_failure_and_allows_retry(self):
        events = []

        def fail(_argv):
            raise FileNotFoundError("missing")

        broker = self.launch.LaunchBroker(
            spawn=fail,
            animate=lambda *_args: None,
            now=lambda: 1.0,
            record_event=lambda _request, status, detail="": events.append((status, detail)),
            report_error=lambda *_args: None,
        )
        request = self.launch.LaunchRequest(("missing",), desktop_file="missing.desktop")
        self.assertFalse(broker.launch(request))
        self.assertFalse(broker.launch(request))
        self.assertEqual(["command_missing", "command_missing"], [item[0] for item in events])

    def test_verified_desktop_wrapper_uses_only_gio_and_retries_after_activation_failure(self):
        events = []
        calls = []
        outcomes = iter((False, RuntimeError("GIO unavailable"), True))
        desktop = pathlib.Path(tempfile.gettempdir()).resolve() / "store-wrapper.desktop"

        def forbidden_spawn(_argv):
            self.fail("desktop app info must not spawn an argv")

        def verify(path):
            calls.append(("verify", path))
            return True

        def activate(path):
            calls.append(("activate", path))
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        broker = self.launch.LaunchBroker(
            spawn=forbidden_spawn,
            desktop_activator=activate,
            trusted_verifier=verify,
            animate=lambda *_args: None,
            reduced_motion=lambda: True,
            probe=lambda *_args, **_kwargs: None,
            now=lambda: 1.0,
            record_event=lambda _request, status, detail="": events.append((status, str(detail))),
            report_error=lambda *_args: None,
        )
        request = self.launch.LaunchRequest(
            (),
            desktop_file=str(desktop),
            mode="desktop_app_info",
        )

        self.assertFalse(broker.launch(request))
        self.assertFalse(broker.launch(request))
        self.assertTrue(broker.launch(request))
        self.assertEqual(
            ["verify", "activate", "verify", "activate", "verify", "activate"],
            [name for name, _path in calls],
        )
        self.assertEqual(
            ["activation_failed", "activation_failed", "activated"],
            [status for status, _detail in events],
        )

    def test_non_reduced_wrapper_activation_uses_a_safe_feedback_icon(self):
        events = []
        icons = []
        desktop = pathlib.Path(tempfile.gettempdir()).resolve() / "store-wrapper.desktop"

        def animate(request, _origin):
            icons.append(self.launch.feedback_icon_name(request))

        broker = self.launch.LaunchBroker(
            spawn=lambda _argv: self.fail("desktop app info must not spawn"),
            desktop_activator=lambda _path: True,
            trusted_verifier=lambda _path: True,
            animate=animate,
            reduced_motion=lambda: False,
            probe=lambda *_args, **_kwargs: None,
            record_event=lambda _request, status, detail="": events.append((status, str(detail))),
            report_error=lambda *_args: None,
        )
        request = self.launch.LaunchRequest(
            (), desktop_file=str(desktop), mode="desktop_app_info",
        )

        self.assertTrue(broker.launch(request))
        self.assertEqual(["application-x-executable"], icons)
        self.assertEqual(["activated"], [status for status, _detail in events])

    def test_rejected_wrapper_activation_is_reported_as_failed_and_is_retryable(self):
        events = []
        activations = []
        verifications = []
        desktop = pathlib.Path(tempfile.gettempdir()).resolve() / "untrusted-wrapper.desktop"

        def verify(path):
            verifications.append(path)
            return False

        broker = self.launch.LaunchBroker(
            spawn=lambda _argv: self.fail("desktop app info must not spawn"),
            desktop_activator=lambda path: activations.append(path) or True,
            trusted_verifier=verify,
            animate=lambda *_args: self.fail("must not animate"),
            reduced_motion=lambda: True,
            probe=lambda *_args, **_kwargs: None,
            now=lambda: 1.0,
            record_event=lambda _request, status, detail="": events.append((status, str(detail))),
            report_error=lambda *_args: None,
        )
        request = self.launch.LaunchRequest(
            (), desktop_file=str(desktop), mode="desktop_app_info",
        )

        self.assertFalse(broker.launch(request))
        self.assertFalse(broker.launch(request))
        self.assertEqual([str(desktop), str(desktop)], verifications)
        self.assertEqual([], activations)
        self.assertEqual(
            ["activation_failed", "activation_failed"],
            [status for status, _detail in events],
        )
        self.assertTrue(all("verification failed" in detail for _status, detail in events))

    def test_final_verification_rejection_returns_correlated_ipc_result(self):
        """The UI must receive rejection before it hides a wrapper launch request."""
        broker = self.launch.LaunchBroker(
            trusted_verifier=lambda _path: False,
            spawn=lambda _argv: self.fail("rejected wrapper must not spawn"),
            animate=lambda *_args: None,
            reduced_motion=lambda: True,
            report_error=lambda *_args: None,
        )
        server = self.launch.LaunchServer(broker=broker)
        handler = getattr(server, "_handle_connection", None)
        self.assertIsNotNone(handler, "LaunchServer must return an IPC result for each request")
        if handler is None:
            return
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )
            client, connection = socket.socketpair()
            self.addCleanup(client.close)
            request_id = "a" * 32
            client.sendall(self.launch.COMMON.encode_json_line({
                "version": 1,
                "action": "launch",
                "request_id": request_id,
                "desktop_file": str(desktop),
                "source": "drawer",
                "rect": None,
            }))
            preflighted = []

            def idle_add(callback, value):
                preflighted.append(value)
                callback(value)
                return 1

            def dispatch(value):
                return self.launch.schedule_launch_after_preflight(
                    idle_add, server.broker, value, timeout=0.1)

            with mock.patch.object(self.launch, "allowed_application_dirs", return_value=(applications,)):
                with mock.patch.object(
                        self.launch.COMMON, "is_system_desktop_activation_candidate", return_value=True):
                    with mock.patch.object(
                            self.launch, "verify_package_owned_system_desktop",
                            side_effect=AssertionError("final verification must run in GTK preflight")):
                        self.assertFalse(handler(connection, dispatch))
            response = self.launch.COMMON.recv_json_line(client, timeout=0.5)

        self.assertEqual({
            "version": 1,
            "action": "launch-result",
            "request_id": request_id,
            "accepted": False,
            "error": "system desktop wrapper is not verified",
        }, response)
        self.assertEqual(1, len(preflighted))

    def test_accepted_ipc_request_is_dispatched_exactly_once(self):
        """Moving acknowledgement after preflight must not retain the old dispatch."""
        server = self.launch.LaunchServer()
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "single-launch.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Single Launch\nExec=single-launch\n",
                encoding="utf-8",
            )
            client, connection = socket.socketpair()
            self.addCleanup(client.close)
            request_id = "b" * 32
            client.sendall(self.launch.COMMON.encode_json_line({
                "version": 1,
                "action": "launch",
                "request_id": request_id,
                "desktop_file": str(desktop),
                "source": "drawer",
                "rect": None,
            }))
            dispatched = []
            with mock.patch.object(self.launch, "allowed_application_dirs", return_value=(applications,)):
                self.assertTrue(server._handle_connection(connection, dispatched.append))
            response = self.launch.COMMON.recv_json_line(client, timeout=0.5)

        self.assertTrue(response["accepted"])
        self.assertEqual(1, len(dispatched))

    def test_gtk_schedule_ack_waits_for_final_preflight(self):
        """The socket worker cannot acknowledge a wrapper before GTK verifies it."""
        request = self.launch.LaunchRequest(
            (), desktop_file=str(pathlib.Path(tempfile.gettempdir()).resolve() / "store-wrapper.desktop"),
            mode="desktop_app_info",
        )
        preflight_started = threading.Event()
        allow_preflight = threading.Event()
        returned = threading.Event()
        events = []

        class Broker:
            @staticmethod
            def preflight(value):
                events.append(("preflight", value))
                preflight_started.set()
                allow_preflight.wait(1)
                return True

            @staticmethod
            def launch(value):
                events.append(("launch", value))
                return True

        def idle_add(callback, value):
            threading.Thread(target=callback, args=(value,), daemon=True).start()
            return 1

        results = []

        def wait_for_schedule():
            results.append(
                self.launch.schedule_launch_after_preflight(
                    idle_add, Broker(), request, timeout=0.5))
            returned.set()

        worker = threading.Thread(target=wait_for_schedule, daemon=True)
        worker.start()
        self.assertTrue(preflight_started.wait(0.5))
        self.assertFalse(returned.is_set())
        allow_preflight.set()
        self.assertTrue(returned.wait(0.5))
        worker.join(1)

        self.assertTrue(results[0].accepted)
        self.assertEqual(["preflight", "launch"], [name for name, _value in events])

    def test_descriptor_revalidation_is_the_last_check_before_desktop_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            order = []

            def query(argv, timeout):
                del timeout
                order.append("query")
                if "-S" in argv:
                    return type("Result", (), {
                        "returncode": 0,
                        "stdout": "store-wrapper: {}\n".format(desktop.resolve()),
                    })()
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": "ii \tstore-wrapper\n",
                })()

            def descriptor_hook(path, parent):
                self.assertEqual(desktop.resolve(), path)
                self.assertEqual(applications.resolve(), parent)
                order.append("descriptor")
                return True

            def verify(path):
                return self.launch.verify_package_owned_system_desktop(
                    path,
                    system_dir=applications,
                    command_runner=query,
                    descriptor_revalidator=descriptor_hook,
                )

            broker = self.launch.LaunchBroker(
                spawn=lambda _argv: self.fail("desktop app info must not spawn"),
                desktop_activator=lambda path: order.append("activate") or True,
                trusted_verifier=verify,
                animate=lambda *_args: None,
                reduced_motion=lambda: True,
                probe=lambda *_args, **_kwargs: None,
                report_error=lambda *_args: None,
            )
            request = self.launch.LaunchRequest(
                (), desktop_file=str(desktop.resolve()), mode="desktop_app_info",
            )

            self.assertTrue(broker.launch(request))
            self.assertEqual(["descriptor", "activate"], order[-2:])

    def test_window_probe_reports_timeout_for_running_process(self):
        timed_out = threading.Event()

        class Process:
            pid = 999999

            @staticmethod
            def poll():
                return None

        self.launch.probe_window_async(
            Process(),
            desktop_file="never.desktop",
            attempts=1,
            interval=0,
            on_timeout=timed_out.set,
        )
        self.assertTrue(timed_out.wait(2))

    def test_window_probe_reports_timeout_when_process_exits_zero_without_window(self):
        timed_out = threading.Event()

        class Process:
            pid = 999999

            @staticmethod
            def poll():
                return 0

        self.launch.probe_window_async(
            Process(),
            desktop_file="no-window.desktop",
            attempts=1,
            interval=0,
            on_timeout=timed_out.set,
        )
        self.assertTrue(timed_out.wait(2))


if __name__ == "__main__":
    unittest.main()
