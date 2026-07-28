import importlib.util
import json
import pathlib
import tempfile
import threading
import types
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

    def test_broker_does_not_run_a_scheduler_boost_after_spawn(self):
        calls = []

        class Process:
            pid = 1234

            @staticmethod
            def poll():
                return None

        broker = self.launch.LaunchBroker(
            spawn=lambda _argv: Process(),
            animate=lambda *_args: None,
            reduced_motion=lambda: True,
            probe=lambda *_args, **_kwargs: None,
            record_event=lambda *_args, **_kwargs: None,
            report_error=lambda *_args, **_kwargs: None,
        )
        request = self.launch.LaunchRequest(("sample-app",), desktop_file="sample.desktop")

        with mock.patch.object(
                self.launch, "request_interaction_boost",
                lambda *_args, **_kwargs: calls.append(True), create=True):
            self.assertTrue(broker.launch(request))

        self.assertEqual([], calls)

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

    def test_window_probe_accepts_delayed_window_after_clean_daemon_exit(self):
        ready = threading.Event()
        timed_out = threading.Event()

        class Process:
            pid = 4242

            @staticmethod
            def poll():
                return 0

        class Completed:
            stdout = "0x001  0 777 sample-app.SampleApp Sample App\n"

        original_run = self.launch.subprocess.run
        self.launch.subprocess.run = lambda *_args, **_kwargs: Completed()
        try:
            self.launch.probe_window_async(
                Process(),
                desktop_file="/usr/share/applications/sample-app.desktop",
                attempts=1,
                interval=0,
                on_ready=ready.set,
                on_timeout=timed_out.set,
            )
            self.assertTrue(ready.wait(2))
            self.assertFalse(timed_out.is_set())
        finally:
            self.launch.subprocess.run = original_run

    def test_broker_allows_retry_after_window_timeout(self):
        events = []
        launches = []
        now = iter([1.0, 1.0])

        class Process:
            pid = 999999

            @staticmethod
            def poll():
                return None

        def spawn(argv):
            launches.append(tuple(argv))
            return Process()

        def probe(_process, _desktop_file, on_ready=None, on_failure=None, on_timeout=None):
            if on_timeout:
                on_timeout()

        broker = self.launch.LaunchBroker(
            spawn=spawn,
            animate=lambda *_args: None,
            now=lambda: next(now),
            probe=probe,
            record_event=lambda _request, status, detail="": events.append(status),
            report_error=lambda *_args: None,
        )
        request = self.launch.LaunchRequest(("sample-app",), desktop_file="sample.desktop")

        self.assertTrue(broker.launch(request))
        self.assertTrue(broker.launch(request))
        self.assertEqual([("sample-app",), ("sample-app",)], launches)
        self.assertEqual(["spawned", "window_timeout", "spawned", "window_timeout"], events)

    def test_window_probe_treats_detached_startup_wm_class_as_ready(self):
        ready = threading.Event()
        failed = threading.Event()
        timed_out = threading.Event()

        class Process:
            pid = 4242

            @staticmethod
            def poll():
                return 0

        with tempfile.TemporaryDirectory() as tempdir:
            desktop = pathlib.Path(tempdir) / "spark-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Spark App\n"
                "Exec=spark-wrapper\nStartupWMClass=spark.real.App\n",
                encoding="utf-8",
            )
            wmctrl = types.SimpleNamespace(
                stdout="0x02400007  0 9999 spark.real.App  ming-os Spark App\n")
            with mock.patch.object(self.launch.subprocess, "run", return_value=wmctrl):
                self.launch.probe_window_async(
                    Process(),
                    desktop_file=str(desktop),
                    attempts=1,
                    interval=0,
                    on_ready=ready.set,
                    on_failure=lambda _error: failed.set(),
                    on_timeout=timed_out.set,
                )

        self.assertTrue(ready.wait(2))
        self.assertFalse(failed.is_set())
        self.assertFalse(timed_out.is_set())

    def test_window_probe_prefers_visible_window_over_nonzero_wrapper_exit(self):
        ready = threading.Event()
        failed = threading.Event()

        class Process:
            pid = 5151

            @staticmethod
            def poll():
                return 1

        wmctrl = types.SimpleNamespace(
            stdout="0x02600007  0 5151 sample-app.Sample  ming-os Sample\n")
        with mock.patch.object(self.launch.subprocess, "run", return_value=wmctrl):
            self.launch.probe_window_async(
                Process(),
                desktop_file="/usr/share/applications/sample-app.desktop",
                attempts=1,
                interval=0,
                on_ready=ready.set,
                on_failure=lambda _error: failed.set(),
            )

        self.assertTrue(ready.wait(2))
        self.assertFalse(failed.is_set())

    def test_window_probe_waits_after_zero_exit_for_detached_window(self):
        ready = threading.Event()
        timed_out = threading.Event()
        calls = []
        original_run = self.launch.subprocess.run

        class Process:
            pid = 999999

            @staticmethod
            def poll():
                return 0

        def fake_run(command, **_kwargs):
            calls.append(tuple(command))

            class Result:
                stdout = "0x01  0 7777 detached-app Ming Detached App\n"

            return Result()

        self.launch.subprocess.run = fake_run
        try:
            self.launch.probe_window_async(
                Process(),
                desktop_file="/usr/share/applications/detached-app.desktop",
                attempts=1,
                interval=0,
                on_ready=ready.set,
                on_timeout=timed_out.set,
            )
            self.assertTrue(ready.wait(2))
            self.assertFalse(timed_out.is_set())
            self.assertEqual([("wmctrl", "-lx", "-p")], calls)
        finally:
            self.launch.subprocess.run = original_run


if __name__ == "__main__":
    unittest.main()
