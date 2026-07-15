import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
DRAWER_PATH = ROOT / "assets" / "ming-app-drawer.py"
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApp:
    def __init__(self, name, categories=(), comment="", path="/apps/fake.desktop", argv=("fake",)):
        self.name = name
        self.categories = tuple(categories)
        self.comment = comment
        self.path = pathlib.Path(path)
        self.argv = tuple(argv)
        self.icon = "fake"


class AppDrawerCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drawer = load_script("ming_app_drawer", DRAWER_PATH)

    def test_module_imports_without_gi(self):
        self.assertFalse(self.drawer.gtk_loaded())

    def test_category_search_and_all_filtering(self):
        browser = FakeApp("Edge 浏览器", ("Network", "WebBrowser"), "上网")
        editor = FakeApp("文本编辑器", ("Utility",), "写字")
        self.assertEqual("网络", self.drawer.category_for(browser))
        self.assertEqual([browser], self.drawer.filter_apps([editor, browser], "edge", "全部"))
        self.assertEqual([editor], self.drawer.filter_apps([browser, editor], "", "工具"))

    def test_recent_store_deduplicates_and_bounds_entries(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "recent.json"
            store = self.drawer.RecentStore(path, limit=3)
            for item in ("a.desktop", "b.desktop", "a.desktop", "c.desktop", "d.desktop"):
                store.touch(item)
            self.assertEqual(["d.desktop", "c.desktop", "a.desktop"], store.load())

    def test_drawer_uses_bottom_seventy_two_percent_of_workarea(self):
        geometry = self.drawer.drawer_geometry({"x": 10, "y": 20, "width": 1000, "height": 800})
        self.assertEqual(576.0, geometry.height)
        self.assertEqual(244.0, geometry.y)
        self.assertEqual(200, self.drawer.ANIMATION_DURATION_MS)
        self.assertGreaterEqual(self.drawer.ANIMATION_DURATION_MS, 180)
        self.assertLessEqual(self.drawer.ANIMATION_DURATION_MS, 220)

    def test_reduced_motion_setting_disables_drawer_slide_animation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            settings = pathlib.Path(tempdir) / "settings.json"
            settings.write_text(json.dumps({"reduced_motion": True}), encoding="utf-8")
            self.assertTrue(self.drawer.reduced_motion_enabled(settings))
            transition = self.drawer.drawer_transition(True)
            self.assertEqual(0, transition["duration_ms"])
            self.assertEqual(1.0, transition["start_opacity"])

    def test_drawer_animation_reverses_from_its_current_progress_without_stacking(self):
        animation = self.drawer.DrawerAnimation(duration_ms=200)
        animation.set_target(1.0, 0)
        self.assertAlmostEqual(0.5, animation.advance(100))
        animation.set_target(0.0, 100)
        self.assertAlmostEqual(0.25, animation.advance(150))
        self.assertAlmostEqual(0.0, animation.advance(200))
        self.assertFalse(animation.active)

    def test_desktop_context_action_is_structured(self):
        app = FakeApp("Browser", path="/usr/share/applications/browser.desktop")
        self.assertEqual(
            ("ming-phone-desktop", "--add", "/usr/share/applications/browser.desktop"),
            self.drawer.add_to_desktop_argv(app),
        )
        message = self.drawer.toggle_message({"x": 1, "y": 2, "width": 3, "height": 4})
        self.assertEqual("toggle", message["action"])
        self.assertEqual("drawer", message["source"])
        self.assertEqual(1, message["version"])

    def test_user_desktop_entry_overrides_or_hides_system_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            user = root / "user"
            system = root / "system"
            user.mkdir()
            system.mkdir()
            (system / "same.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=System\nExec=system-app\n",
                encoding="utf-8",
            )
            (user / "same.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=User\nExec=user-app\nHidden=true\n",
                encoding="utf-8",
            )
            self.assertEqual([], self.drawer.discover_apps((user, system)))
            (user / "same.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=User\nExec=user-app\n",
                encoding="utf-8",
            )
            self.assertEqual("User", self.drawer.discover_apps((user, system))[0].name)

    def test_missing_desktop_executable_stays_visible_with_a_readable_diagnostic(self):
        """A broken store launcher must be actionable instead of disappearing."""
        with tempfile.TemporaryDirectory() as tempdir:
            applications = pathlib.Path(tempdir) / "applications"
            applications.mkdir()
            (applications / "broken-store-app.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Broken Store App\n"
                "Exec=/opt/broken-store-app/bin/launch\n",
                encoding="utf-8",
            )
            apps = self.drawer.discover_apps((applications,))
            self.assertEqual(1, len(apps))
            self.assertEqual("Broken Store App", apps[0].name)
            self.assertEqual((), apps[0].argv)
            self.assertIn("找不到启动程序", apps[0].diagnostic)

    def test_user_shell_wrapper_is_reported_without_becoming_executable(self):
        """A user launcher remains diagnostic-only even after system trust work."""
        with tempfile.TemporaryDirectory() as tempdir:
            applications = pathlib.Path(tempdir) / ".local" / "share" / "applications"
            applications.mkdir(parents=True)
            (applications / "unsafe-store-app.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Unsafe Store App\n"
                "Exec=sh -c 'touch /tmp/ming-unsafe'\n",
                encoding="utf-8",
            )
            apps = self.drawer.discover_apps((applications,))
            self.assertEqual(1, len(apps))
            self.assertEqual("Unsafe Store App", apps[0].name)
            self.assertEqual((), apps[0].argv)
            self.assertIn("不支持", apps[0].diagnostic)

    def test_protected_system_wrapper_catalog_entry_routes_only_to_broker(self):
        """The drawer must let the broker make the final wrapper trust decision."""
        with tempfile.TemporaryDirectory() as tempdir:
            applications = pathlib.Path(tempdir) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                    self.drawer.COMMON, "is_system_desktop_activation_candidate", return_value=True):
                apps = self.drawer.discover_apps((applications,))

        self.assertEqual(1, len(apps))
        self.assertEqual((), apps[0].argv)
        self.assertEqual("", apps[0].diagnostic)
        events = []
        controller = types.SimpleNamespace(
            recent=types.SimpleNamespace(touch=lambda path: events.append(("recent", str(path)))),
            hide=lambda: events.append(("hide",)),
        )
        with mock.patch.object(self.drawer.COMMON, "send_launch_request", return_value=True) as send:
            with mock.patch.object(self.drawer.subprocess, "Popen") as popen:
                self.assertTrue(self.drawer.DrawerController.launch(controller, apps[0], None))

        send.assert_called_once_with(str(desktop), "drawer", None)
        popen.assert_not_called()
        self.assertEqual([("recent", str(desktop)), ("hide",)], events)

    def test_drawer_keeps_open_when_broker_explicitly_rejects_launch(self):
        """A verified-socket rejection is not a broker outage or a successful launch."""
        app = FakeApp("Store Wrapper", path="/usr/share/applications/store-wrapper.desktop")
        app.diagnostic = ""
        events = []

        class Dialog:
            def __init__(self, **kwargs):
                events.append(("dialog", kwargs.get("text")))

            def format_secondary_text(self, _text):
                return None

            def run(self):
                return None

            def destroy(self):
                return None

        fake_gtk = types.SimpleNamespace(
            MessageDialog=Dialog,
            MessageType=types.SimpleNamespace(ERROR="error"),
            ButtonsType=types.SimpleNamespace(CLOSE="close"),
        )
        controller = types.SimpleNamespace(
            recent=types.SimpleNamespace(touch=lambda path: events.append(("recent", str(path)))),
            hide=lambda: events.append(("hide",)),
            window=None,
            Gtk=fake_gtk,
        )

        rejection = types.SimpleNamespace(rejected=True)
        with mock.patch.object(self.drawer.COMMON, "send_launch_request", return_value=rejection):
            with mock.patch.object(self.drawer.subprocess, "Popen") as popen:
                self.assertFalse(self.drawer.DrawerController.launch(controller, app, None))

        popen.assert_not_called()
        self.assertEqual([("dialog", "无法打开此应用")], events)

    def test_drawer_local_fallback_uses_only_the_trusted_broker_argv(self):
        """A stopped socket may start the broker, but never execute the catalog argv."""
        app = FakeApp(
            "Store Wrapper",
            path="/usr/share/applications/store-wrapper.desktop",
            argv=("untrusted-direct-app", "--should-not-run"),
        )
        app.diagnostic = ""
        events = []
        controller = types.SimpleNamespace(
            recent=types.SimpleNamespace(touch=lambda path: events.append(("recent", str(path)))),
            hide=lambda: events.append(("hide",)),
        )
        expected = (
            "/usr/local/bin/ming-launch", "--desktop-file", str(app.path), "--source", "drawer",
        )
        with mock.patch.object(self.drawer.COMMON, "send_launch_request", return_value=False):
            with mock.patch.object(
                    self.drawer.COMMON, "broker_fallback_argv", return_value=expected, create=True) as fallback:
                with mock.patch.object(self.drawer.subprocess, "Popen", return_value=object()) as popen:
                    self.assertTrue(self.drawer.DrawerController.launch(controller, app, None))

        fallback.assert_called_once_with(str(app.path), "drawer")
        popen.assert_called_once_with(expected, shell=False)
        self.assertEqual([("recent", str(app.path)), ("hide",)], events)

    def test_widget_source_rect_includes_no_window_widget_allocation(self):
        allocation = type("Allocation", (), {"x": 12, "y": 18, "width": 80, "height": 40})()
        rect = self.drawer.widget_source_rect((True, 100, 200), allocation)
        self.assertEqual({"x": 112.0, "y": 218.0, "width": 80.0, "height": 40.0}, rect)

    def test_drawer_rescans_before_every_show_including_reduced_motion(self):
        source = DRAWER_PATH.read_text(encoding="utf-8")
        show = source[source.index("    def show(self):"):source.index("    def hide(self):")]
        self.assertIn("self.apps = discover_apps()", show)
        self.assertLess(
            show.index("self.apps = discover_apps()"),
            show.index("transition = drawer_transition(reduced_motion_enabled())"),
        )


class LaunchBrokerCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_script("ming_launch", LAUNCH_PATH)

    def test_process_starts_before_animation(self):
        events = []
        broker = self.launch.LaunchBroker(
            spawn=lambda argv: events.append(("spawn", tuple(argv))) or object(),
            animate=lambda request, origin: events.append(("animate", origin.to_dict())),
            now=lambda: 1.0,
        )
        request = self.launch.LaunchRequest(("browser",), source="desktop", rect={
            "x": 1, "y": 2, "width": 30, "height": 40,
        })
        self.assertTrue(broker.launch(request))
        self.assertEqual(["spawn", "animate"], [event[0] for event in events])

    def test_workarea_falls_back_when_gdk_typelib_is_missing(self):
        fake_gi = types.SimpleNamespace(
            require_version=mock.Mock(side_effect=ValueError("Namespace Gdk not available")),
        )
        with mock.patch.dict(sys.modules, {"gi": fake_gi}):
            self.assertEqual(
                {"x": 0, "y": 0, "width": 1280, "height": 720},
                self.launch._default_workarea(),
            )

    def test_duplicate_launch_is_suppressed_for_short_window(self):
        calls = []
        times = iter((1.0, 1.1, 2.0))
        broker = self.launch.LaunchBroker(spawn=lambda argv: calls.append(argv), animate=lambda *_: None, now=lambda: next(times))
        request = self.launch.LaunchRequest(("browser",), desktop_file="browser.desktop")
        self.assertTrue(broker.launch(request))
        self.assertFalse(broker.launch(request))
        self.assertTrue(broker.launch(request))
        self.assertEqual(2, len(calls))

    def test_origin_uses_source_rect_or_dock_bottom_center_fallback(self):
        direct = self.launch.resolve_origin(
            self.launch.LaunchRequest(("app",), source="drawer", rect={"x": 5, "y": 6, "width": 20, "height": 30}),
            {"x": 0, "y": 0, "width": 1000, "height": 700},
        )
        fallback = self.launch.resolve_origin(
            self.launch.LaunchRequest(("app",), source="unknown"),
            {"x": 0, "y": 0, "width": 1000, "height": 700},
        )
        self.assertEqual((15.0, 36.0), direct.bottom_center)
        self.assertEqual((500.0, 700.0), fallback.bottom_center)

    def test_launch_feedback_geometry_expands_from_icon_toward_workarea(self):
        origin = self.launch.COMMON.Rect(20, 30, 64, 64)
        workarea = {"x": 0, "y": 0, "width": 1000, "height": 700}
        start = self.launch.feedback_geometry(origin, workarea, 0.0)
        finish = self.launch.feedback_geometry(origin, workarea, 1.0)
        self.assertEqual(origin.bottom_center, start.bottom_center)
        self.assertGreater(finish.width, start.width)
        self.assertGreater(finish.height, start.height)
        self.assertAlmostEqual(500.0, finish.x + finish.width / 2.0)
        self.assertLess(finish.y + finish.height, 700)

    def test_reduced_motion_disables_animation_but_not_process(self):
        events = []
        broker = self.launch.LaunchBroker(
            spawn=lambda argv: events.append("spawn"),
            animate=lambda *_: events.append("animate"),
            now=lambda: 1.0,
            reduced_motion=lambda: True,
        )
        self.assertTrue(broker.launch(self.launch.LaunchRequest(("files",))))
        self.assertEqual(["spawn"], events)

    def test_spawn_failure_is_reported_and_not_deduplicated(self):
        errors = []
        attempts = []

        def fail(argv):
            attempts.append(tuple(argv))
            raise OSError("missing executable")

        broker = self.launch.LaunchBroker(
            spawn=fail, animate=lambda *_: self.fail("must not animate"), now=lambda: 1.0,
            report_error=lambda request, error: errors.append((request, str(error))),
        )
        request = self.launch.LaunchRequest(("missing",), desktop_file="missing.desktop")
        self.assertFalse(broker.launch(request))
        self.assertFalse(broker.launch(request))
        self.assertEqual(2, len(attempts))
        self.assertEqual(2, len(errors))

    def test_ipc_message_is_versioned_and_never_carries_argv(self):
        request = self.launch.LaunchRequest(
            ("browser", "--safe"), source="drawer",
            desktop_file="/usr/share/applications/browser.desktop",
        )
        message = request.to_message()
        self.assertEqual(1, message["version"])
        self.assertNotIn("argv", message)
        with self.assertRaises(ValueError):
            self.launch.LaunchRequest.from_message({
                "version": 1, "action": "launch", "argv": ["evil"],
                "desktop_file": "/tmp/evil.desktop",
            })

    def test_desktop_file_ipc_is_limited_to_application_directories(self):
        with tempfile.TemporaryDirectory() as tempdir:
            allowed = pathlib.Path(tempdir) / "applications"
            allowed.mkdir()
            good = allowed / "good.desktop"
            good.write_text(
                "[Desktop Entry]\nType=Application\nName=Good\nExec=good-app\n",
                encoding="utf-8",
            )
            request = self.launch.request_from_message({
                "version": 1, "action": "launch", "desktop_file": str(good),
                "source": "drawer", "rect": None,
            }, allowed_dirs=(allowed,))
            self.assertEqual(("good-app",), request.argv)
            outside = pathlib.Path(tempdir) / "evil.desktop"
            outside.write_text(
                "[Desktop Entry]\nType=Application\nName=Evil\nExec=evil\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.launch.request_from_message({
                    "version": 1, "action": "launch", "desktop_file": str(outside),
                    "source": "drawer", "rect": None,
                }, allowed_dirs=(allowed,))

    def test_desktop_copy_resolves_to_same_named_trusted_launcher(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            allowed = root / "applications"
            desktop = root / "Desktop"
            allowed.mkdir()
            desktop.mkdir()
            trusted = allowed / "browser.desktop"
            trusted.write_text(
                "[Desktop Entry]\nType=Application\nName=Browser\nExec=trusted-browser\n",
                encoding="utf-8",
            )
            copied = desktop / trusted.name
            copied.write_text(
                "[Desktop Entry]\nType=Application\nName=Browser\nExec=untrusted-copy\n",
                encoding="utf-8",
            )

            request = self.launch.request_from_message({
                "version": 1, "action": "launch", "desktop_file": str(copied),
                "source": "desktop", "rect": None,
            }, allowed_dirs=(allowed,))

            self.assertEqual(("trusted-browser",), request.argv)
            self.assertEqual(str(trusted.resolve()), request.desktop_file)

    def test_feedback_finishes_on_window_probe_and_has_bounded_timeout(self):
        events = []
        ready = []

        def animate(_request, _origin):
            return lambda: events.append("finish")

        def probe(_process, _desktop, on_ready=None, on_failure=None):
            ready.append(on_ready)

        broker = self.launch.LaunchBroker(
            spawn=lambda _argv: object(), animate=animate, probe=probe,
            reduced_motion=lambda: False, now=lambda: 1.0,
        )
        broker.launch(self.launch.LaunchRequest(("browser",)))
        self.assertEqual([], events)
        ready[0]()
        self.assertEqual(["finish"], events)
        self.assertGreaterEqual(self.launch.FEEDBACK_TIMEOUT_MS, 1000)
        self.assertLessEqual(self.launch.FEEDBACK_TIMEOUT_MS, 5000)

    def test_nonzero_exit_is_reported_and_launch_can_be_retried(self):
        callbacks = {}
        errors = []
        calls = []

        def probe(_process, _desktop, on_ready=None, on_failure=None):
            callbacks["failure"] = on_failure

        broker = self.launch.LaunchBroker(
            spawn=lambda argv: calls.append(tuple(argv)) or object(),
            animate=lambda *_args: None,
            probe=probe,
            report_error=lambda _request, error: errors.append(str(error)),
            now=lambda: 1.0,
        )
        request = self.launch.LaunchRequest(("spark-store",), desktop_file="spark-store.desktop")
        self.assertTrue(broker.launch(request))
        callbacks["failure"](RuntimeError("exited with status 7"))
        self.assertIn("status 7", errors[0])
        self.assertTrue(broker.launch(request))
        self.assertEqual(2, len(calls))

    def test_window_probe_reports_nonzero_process_exit(self):
        failed = []
        ready = threading.Event()

        class Process:
            pid = 42

            @staticmethod
            def poll():
                return 9

        self.launch.probe_window_async(
            Process(),
            on_failure=lambda error: (failed.append(str(error)), ready.set()),
        )
        self.assertTrue(ready.wait(2))
        self.assertIn("status 9", failed[0])

    def test_socket_request_is_scheduled_on_ui_thread(self):
        events = []
        request = self.launch.LaunchRequest(("files",))
        broker = type("Broker", (), {"launch": lambda _self, value: events.append(("launch", value))})()

        def idle_add(callback, value):
            events.append(("scheduled", callback, value))
            return 1

        self.launch.schedule_launch(idle_add, broker, request)
        self.assertEqual("scheduled", events[0][0])
        self.assertEqual(request, events[0][2])
        self.assertEqual(1, len(events))


if __name__ == "__main__":
    unittest.main()
