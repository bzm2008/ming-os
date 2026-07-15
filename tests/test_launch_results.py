import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest


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
