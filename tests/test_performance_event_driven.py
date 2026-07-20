import ast
import importlib.util
import json
import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
POLICY = ROOT / "assets" / "ming-performance-policy.py"
BACKEND = ROOT / "assets" / "ming-settings-backend.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
DEVICE = ROOT / "assets" / "ming-device-control.py"
BUILD = ROOT / "build_onion_os.sh"
RESUME = ROOT / "resume_build.sh"
PERFORMANCE = ROOT / "assets" / "ming-performance-status.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PerformanceEventDrivenContracts(unittest.TestCase):
    def test_background_policy_has_no_global_cpu_quota_and_defaults_off(self):
        source = POLICY.read_text(encoding="utf-8")
        self.assertNotIn("cpu.max", source)
        self.assertNotIn("CPUQuota", source)
        backend = load_module(BACKEND, "ming_settings_backend_performance_test")
        self.assertFalse(backend.SETTING_SPECS["background_throttle"]["default"])

    def test_preview_background_policy_migrates_to_safe_default_once(self):
        backend = load_module(BACKEND, "ming_settings_backend_migration_test")
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            config = home / ".config/ming-os/settings.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"background_throttle": True}), encoding="utf-8")
            first = backend.SettingsBackend(home=home)
            self.assertFalse(first.get_value("background_throttle")["value"])
            migrated = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(2, migrated["_ming_performance_policy_version"])
            first.set_value("background_throttle", True)
            second = backend.SettingsBackend(home=home)
            self.assertTrue(second.get_value("background_throttle")["value"])

    def test_malformed_settings_migration_preserves_user_file(self):
        backend = load_module(BACKEND, "ming_settings_backend_malformed_migration_test")
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            config = home / ".config/ming-os/settings.json"
            config.parent.mkdir(parents=True)
            original = b'{"background_throttle": true, broken-json\n'
            config.write_bytes(original)

            backend.SettingsBackend(home=home)

            self.assertEqual(original, config.read_bytes())

    def test_unreadable_settings_migration_preserves_user_file(self):
        backend = load_module(BACKEND, "ming_settings_backend_unreadable_migration_test")
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            config = home / ".config/ming-os/settings.json"
            config.parent.mkdir(parents=True)
            original = b'{"background_throttle": true}\n'
            config.write_bytes(original)

            original_read_text = backend.pathlib.Path.read_text

            def deny_settings(path, *args, **kwargs):
                if path == config:
                    raise PermissionError("settings are temporarily unreadable")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(backend.pathlib.Path, "read_text", deny_settings):
                backend.SettingsBackend(home=home)

            self.assertEqual(original, config.read_bytes())

    def test_fresh_user_choice_persists_after_restart(self):
        backend = load_module(BACKEND, "ming_settings_backend_fresh_choice_test")
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            first = backend.SettingsBackend(home=home)
            self.assertTrue(first.set_value("background_throttle", True)["ok"])
            second = backend.SettingsBackend(home=home)
            self.assertTrue(second.get_value("background_throttle")["value"])

    def test_phone_desktop_uses_file_monitor_and_stops_collapsed_hardware_sampling(self):
        source = PHONE.read_text(encoding="utf-8")
        self.assertIn("Gio.FileMonitor", source)
        self.assertIn("monitor_directory", source)
        self.assertIn("GLib.timeout_add(500", source)
        self.assertNotIn("GLib.timeout_add_seconds(3, self.refresh_if_apps_changed)", source)
        refresh = source[
            source.index("    def refresh(self):"):
            source.index("    def refresh_status_once", source.index("    def refresh(self):"))
        ]
        self.assertNotIn("threading.Thread", refresh)
        self.assertNotIn("collect_status", refresh)
        monitor = source[
            source.index("    def start_catalog_monitor(self):"):
            source.index("    def on_catalog_file_changed", source.index("    def start_catalog_monitor(self):"))
        ]
        self.assertIn("mkdir(parents=True, exist_ok=True)", monitor)
        self.assertNotIn("WATCH_CHANGES", monitor)
        self.assertNotIn("WATCH_DELETED", monitor)

    def test_missing_catalog_directory_retries_then_monitors_once(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        method_names = {
            "start_catalog_monitor",
            "_ensure_catalog_monitor",
            "_schedule_catalog_monitor_retry",
            "_run_catalog_monitor_retry",
        }
        methods = [
            node for node in phone_class.body
            if isinstance(node, ast.FunctionDef) and node.name in method_names
        ]
        self.assertEqual(method_names, {node.name for node in methods})

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            missing = root / "applications"
            state_dir = root / "state"
            state_dir.mkdir()
            timers = []
            scheduled_delays = []
            monitored = []

            class FakeMonitor:
                @staticmethod
                def connect(*_args):
                    return 1

            class FakeFile:
                def __init__(self, path):
                    self.path = pathlib.Path(path)

                def monitor_directory(self, _flags, _cancellable):
                    if not self.path.is_dir():
                        raise OSError("missing")
                    monitored.append(str(self.path))
                    return FakeMonitor()

            class FakeGLib:
                Error = OSError

                @staticmethod
                def timeout_add(delay, callback, *args):
                    timers.append((delay, callback, args))
                    scheduled_delays.append(delay)
                    return len(timers)

            namespace = {
                "APP_DIRS": (missing,),
                "HOME": root,
                "STATE_DIR": state_dir,
                "Path": pathlib.Path,
                "CATALOG_MONITOR_RETRY_DELAYS_MS": (250, 1000, 30_000),
                "Gio": types.SimpleNamespace(
                    File=types.SimpleNamespace(new_for_path=lambda path: FakeFile(path)),
                    FileMonitorFlags=types.SimpleNamespace(WATCH_MOVES=1),
                ),
                "GLib": FakeGLib,
                "log": lambda _message: None,
            }
            exec(compile(ast.Module(body=methods, type_ignores=[]), str(PHONE), "exec"), namespace)

            monitor = types.SimpleNamespace(
                _catalog_monitors=[],
                _catalog_monitor_paths=set(),
                _catalog_retry_source=0,
                _catalog_retry_attempt=0,
                on_catalog_file_changed=lambda *_args: None,
            )
            for name in method_names:
                function = namespace[name]
                setattr(monitor, name, types.MethodType(function, monitor))

            monitor.start_catalog_monitor()
            self.assertNotIn(str(missing), monitored)
            self.assertEqual(1, len(timers))
            self.assertLessEqual(timers[0][0], 30_000)

            monitor.start_catalog_monitor()
            self.assertEqual(1, len(timers), "only one retry source may be pending")
            for _attempt in range(len(namespace["CATALOG_MONITOR_RETRY_DELAYS_MS"]) + 2):
                self.assertEqual(1, len(timers), "missing paths must keep a bounded retry alive")
                _delay, callback, args = timers.pop(0)
                self.assertFalse(callback(*args))
            self.assertEqual([30_000, 30_000, 30_000], scheduled_delays[-3:])

            missing.mkdir()
            _delay, callback, args = timers.pop(0)
            self.assertFalse(callback(*args))
            self.assertEqual(1, monitored.count(str(missing)))
            self.assertEqual(0, monitor._catalog_retry_source)

            monitor.start_catalog_monitor()
            self.assertEqual(1, monitored.count(str(missing)))

    def test_shell_install_creates_and_gates_local_application_directory(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        install = desktop[
            desktop.index("install_ming_shell_components() {"):
            desktop.index("\ninstall_ming_files() {", desktop.index("install_ming_shell_components() {"))
        ]
        self.assertIn("/usr/local/share/applications", install)
        build = BUILD.read_text(encoding="utf-8")
        gate = build[
            build.index("validate_r4_compatibility() {"):
            build.index("write_grub_config() {")
        ]
        self.assertIn('require_directory("usr/local/share/applications")', gate)

    def test_catalog_fingerprint_changes_when_existing_launcher_is_edited(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        assignments = [
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in {
                "APP_CATALOG_FINGERPRINT_VERSION",
                "APP_CATALOG_MAX_ROOTS",
                "APP_CATALOG_MAX_DIRECTORY_ENTRIES",
                "APP_CATALOG_MAX_LAUNCHERS",
                "APP_CATALOG_LAUNCHER_HASH_BYTES",
                "APP_CATALOG_TOTAL_HASH_BYTES",
            } for target in node.targets)
        ]
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "app_catalog_fingerprint"
        )
        namespace = {"Path": pathlib.Path, "os": os, "hashlib": __import__("hashlib")}
        exec(compile(
            ast.Module(
                body=[*assignments, functions["launcher_content_stamp"], function],
                type_ignores=[],
            ),
            str(PHONE),
            "exec",
        ), namespace)

        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            launcher = applications / "example.desktop"
            launcher.write_text("[Desktop Entry]\nName=Before\nExec=example\n", encoding="utf-8")
            before = namespace["app_catalog_fingerprint"]((applications,))
            directory_stat = applications.stat()
            launcher_stat = launcher.stat()
            launcher.write_text("[Desktop Entry]\nName=After!\nExec=example\n", encoding="utf-8")
            os.utime(
                launcher,
                ns=(launcher_stat.st_atime_ns, launcher_stat.st_mtime_ns + 1_000_000_000),
            )
            os.utime(
                applications,
                ns=(directory_stat.st_atime_ns, directory_stat.st_mtime_ns),
            )
            after = namespace["app_catalog_fingerprint"]((applications,))

        self.assertNotEqual(before, after)

    def test_catalog_fingerprint_uses_bounded_launcher_content_digest(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("launcher_content_stamp", functions)
        fingerprint = functions["app_catalog_fingerprint"]
        self.assertTrue(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "launcher_content_stamp"
            for node in ast.walk(fingerprint)
        ))
        assignments = [
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "APP_CATALOG_LAUNCHER_HASH_BYTES"
                    for target in node.targets)
        ]
        self.assertEqual(1, len(assignments))
        namespace = {"Path": pathlib.Path, "hashlib": __import__("hashlib")}
        exec(compile(
            ast.Module(
                body=[assignments[0], functions["launcher_content_stamp"]],
                type_ignores=[],
            ),
            str(PHONE),
            "exec",
        ), namespace)

        with tempfile.TemporaryDirectory() as directory:
            launcher = pathlib.Path(directory) / "example.desktop"
            launcher.write_bytes(b"[Desktop Entry]\nName=Before\n")
            original = launcher.stat()
            before = namespace["launcher_content_stamp"](launcher)
            launcher.write_bytes(b"[Desktop Entry]\nName=After!\n")
            os.utime(
                launcher,
                ns=(original.st_atime_ns, original.st_mtime_ns),
            )
            after = namespace["launcher_content_stamp"](launcher)

        self.assertNotEqual(before, after)

    def test_catalog_fingerprint_enforces_global_file_and_byte_budgets(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        assignments = {
            target.id: node
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        required = {
            "APP_CATALOG_MAX_ROOTS",
            "APP_CATALOG_MAX_DIRECTORY_ENTRIES",
            "APP_CATALOG_MAX_LAUNCHERS",
            "APP_CATALOG_TOTAL_HASH_BYTES",
            "APP_CATALOG_LAUNCHER_HASH_BYTES",
        }
        self.assertTrue(required.issubset(assignments))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "app_catalog_fingerprint"
        )
        function_source = ast.unparse(function)
        for name in required:
            self.assertIn(name, function_source)

        namespace = {"Path": pathlib.Path, "os": os}
        exec(compile(
            ast.Module(
                body=[
                    assignments["APP_CATALOG_FINGERPRINT_VERSION"],
                    *(assignments[name] for name in required),
                    function,
                ],
                type_ignores=[],
            ),
            str(PHONE),
            "exec",
        ), namespace)
        namespace["APP_CATALOG_MAX_ROOTS"] = 8
        namespace["APP_CATALOG_MAX_LAUNCHERS"] = 3
        namespace["APP_CATALOG_TOTAL_HASH_BYTES"] = 10
        namespace["APP_CATALOG_LAUNCHER_HASH_BYTES"] = 8
        requested_bytes = []
        namespace["launcher_content_stamp"] = (
            lambda _path, max_bytes: requested_bytes.append(max_bytes) or "digest"
        )

        with tempfile.TemporaryDirectory() as directory:
            roots = []
            for root_index in range(3):
                root = pathlib.Path(directory) / ("root-%d" % root_index)
                root.mkdir()
                roots.append(root)
                for file_index in range(3):
                    (root / ("app-%d.desktop" % file_index)).write_bytes(b"12345678")
            fingerprint = namespace["app_catalog_fingerprint"](roots)

        launcher_entries = [
            entry for entry in fingerprint
            if len(entry) > 1 and entry[1] == "launcher"
        ]
        self.assertLessEqual(len(launcher_entries), 3)
        self.assertLessEqual(sum(requested_bytes), 10)

    def test_catalog_fingerprint_caps_all_directory_entries_and_marks_truncation(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        assignments = {
            target.id: node
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "app_catalog_fingerprint"
        )
        selected_names = {
            "APP_CATALOG_FINGERPRINT_VERSION",
            "APP_CATALOG_MAX_ROOTS",
            "APP_CATALOG_MAX_DIRECTORY_ENTRIES",
            "APP_CATALOG_MAX_LAUNCHERS",
            "APP_CATALOG_TOTAL_HASH_BYTES",
            "APP_CATALOG_LAUNCHER_HASH_BYTES",
        }

        inspected_names = []
        scanned_roots = []

        class FakeEntry:
            def __init__(self, name):
                self._name = name

            @property
            def name(self):
                inspected_names.append(self._name)
                return self._name

        class FakeScan:
            def __init__(self, names):
                self._entries = [FakeEntry(name) for name in names]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter(self._entries)

        with tempfile.TemporaryDirectory() as directory:
            roots = []
            entries_by_root = {}
            for root_index, entry_count in enumerate((2, 8, 1)):
                root = pathlib.Path(directory) / ("root-%d" % root_index)
                root.mkdir()
                roots.append(root)
                entries_by_root[root] = [
                    "ignored-%d.txt" % entry_index
                    for entry_index in range(entry_count)
                ]

            def scandir(path):
                resolved = pathlib.Path(path)
                scanned_roots.append(resolved)
                return FakeScan(entries_by_root[resolved])

            namespace = {
                "Path": pathlib.Path,
                "os": types.SimpleNamespace(scandir=scandir),
                "APP_CATALOG_MAX_DIRECTORY_ENTRIES": 3,
            }
            exec(compile(
                ast.Module(
                    body=[
                        assignments[name]
                        for name in selected_names
                        if name in assignments
                    ] + [function],
                    type_ignores=[],
                ),
                str(PHONE),
                "exec",
            ), namespace)
            namespace["APP_CATALOG_MAX_DIRECTORY_ENTRIES"] = 3
            fingerprint = namespace["app_catalog_fingerprint"](roots)

        marker = ("budget", "directory-entry-limit")
        self.assertLessEqual(len(inspected_names), 3)
        self.assertEqual(1, fingerprint.count(marker))
        self.assertEqual(marker, fingerprint[-1])
        self.assertEqual(roots[:2], scanned_roots)
        self.assertIn("APP_CATALOG_MAX_DIRECTORY_ENTRIES", assignments)

    def test_catalog_gio_event_marks_dirty_before_debounced_refresh(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        methods = {
            node.name: node for node in phone_class.body
            if isinstance(node, ast.FunctionDef)
        }
        event_method = methods["on_catalog_file_changed"]
        refresh_method = methods["refresh_if_apps_changed"]
        self.assertIn("_catalog_dirty", ast.unparse(event_method))
        self.assertIn("_catalog_dirty", ast.unparse(refresh_method))
        self.assertIn("STATE_DIR", ast.unparse(methods["_ensure_catalog_monitor"]))

        timers = []

        class FakeGLib:
            @staticmethod
            def timeout_add(delay, callback, *args):
                timers.append((delay, callback, args))
                return len(timers)

            @staticmethod
            def source_remove(_source):
                return None

        namespace = {"GLib": FakeGLib}
        selected = [event_method, methods["_run_catalog_refresh"]]
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(PHONE), "exec"), namespace)
        observed = []
        desktop = types.SimpleNamespace(
            _catalog_dirty=False,
            _catalog_debounce_source=0,
            refresh_if_apps_changed=lambda: observed.append(desktop._catalog_dirty),
        )
        desktop.on_catalog_file_changed = types.MethodType(
            namespace["on_catalog_file_changed"], desktop)
        desktop._run_catalog_refresh = types.MethodType(
            namespace["_run_catalog_refresh"], desktop)

        desktop.on_catalog_file_changed()
        self.assertTrue(desktop._catalog_dirty)
        self.assertEqual(1, len(timers))
        _delay, callback, args = timers.pop()
        self.assertFalse(callback(*args))
        self.assertEqual([True], observed)

        desktop._catalog_dirty = False
        desktop.on_catalog_file_changed(None, None, None, None, False)
        self.assertFalse(
            desktop._catalog_dirty,
            "layout/appearance state events must not force an application rescan",
        )

        sync_calls = []
        stable_catalog = (("version", 4),)
        refresh_namespace = {
            "app_catalog_fingerprint": lambda: stable_catalog,
            "sync_layout": lambda width: sync_calls.append(width) or {
                "version": 4, "items": []},
            "load_layout": lambda: {"version": 4, "items": []},
            "layout_is_valid": lambda _layout, require_items=False: True,
            "LAYOUT_VERSION": 4,
        }
        exec(compile(
            ast.Module(body=[refresh_method], type_ignores=[]),
            str(PHONE),
            "exec",
        ), refresh_namespace)
        fake_screen = types.SimpleNamespace(get_width=lambda: 1366)
        dirty_desktop = types.SimpleNamespace(
            _catalog_dirty=True,
            catalog_stamp=stable_catalog,
            appearance_stamp=(0, 0),
            layout_stamp=0,
            layout={"version": 4, "items": []},
            current_layout_stamp=lambda: 0,
            current_appearance_stamp=lambda: (0, 0),
            get_screen=lambda: fake_screen,
            render=lambda: None,
        )
        refresh = types.MethodType(
            refresh_namespace["refresh_if_apps_changed"], dirty_desktop)
        self.assertFalse(refresh())
        self.assertEqual([1366], sync_calls)
        self.assertFalse(dirty_desktop._catalog_dirty)

    def test_metric_sampling_reads_default_route_without_subprocesses(self):
        module = load_module(PERFORMANCE, "ming_performance_status_proc_metrics_test")
        commands = []
        values = {
            "/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 500 kB\n",
            "/proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\n",
            "/proc/net/dev": (
                "Inter-| Receive | Transmit\n"
                " eth0: 2000 0 0 0 0 0 0 0 3000 0 0 0 0 0 0 0\n"
                " wlan0: 9000 0 0 0 0 0 0 0 9000 0 0 0 0 0 0 0\n"
            ),
            "/proc/net/route": (
                "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                "eth0\t00000000\t01020304\t0003\t0\t0\t100\t00000000\n"
            ),
        }

        def runner(argv, _timeout):
            commands.append(tuple(argv))
            return module.CommandResult(1, "", "unexpected")

        service = module.PerformanceStatus(
            runner=runner,
            read_text=lambda path: values.get(str(path)),
            globber=lambda _pattern: [],
        )
        result = service.metrics_snapshot(
            previous={
                "cpu": {"total": 900, "idle": 700},
                "network": {
                    "eth0": {"bytes": 4000},
                    "wlan0": {"bytes": 1000},
                },
            },
            interval_seconds=1.0,
        )

        self.assertEqual([], commands)
        self.assertEqual("eth0", result["network"]["interface"])

    def test_expanded_widget_uses_lightweight_device_status_only(self):
        device = DEVICE.read_text(encoding="utf-8")
        self.assertIn("    def widget_status(self):", device)
        quick = device[
            device.index("    def audio_widget_status(self):"):
            device.index("    def status(self):", device.index("    def widget_status(self):"))
        ]
        for forbidden in ("lspci", "lsusb", "journalctl", "pactl\", \"list\", \"cards"):
            self.assertNotIn(forbidden, quick)
        phone = PHONE.read_text(encoding="utf-8")
        collect = phone[
            phone.index("    def collect_status(self):"):
            phone.index("    def apply_status", phone.index("    def collect_status(self):"))
        ]
        self.assertIn("widget_status", collect)

    def test_widget_status_never_runs_hardware_inventory_commands(self):
        module = load_module(DEVICE, "ming_device_control_widget_test")
        commands = []

        class WifiDevice:
            @staticmethod
            def get_iface():
                return "wlan0"

        class NetworkBackend:
            @staticmethod
            def available():
                return True

            @staticmethod
            def _wifi_devices():
                return [WifiDevice()]

            @staticmethod
            def _state_name(_device):
                return "connected"

        def runner(command, timeout=8):
            commands.append(tuple(command))
            key = tuple(command)
            if key == ("pactl", "info"):
                return 0, "Default Sink: alsa_output.pci\nDefault Source: alsa_input.pci", ""
            if key == ("pactl", "get-sink-volume", "@DEFAULT_SINK@"):
                return 0, "Volume: front-left: 50%", ""
            if key == ("pactl", "list", "short", "sinks"):
                return 0, "0\talsa_output.pci\tmodule-alsa-card.c\ts16le\tRUNNING", ""
            if key == ("pactl", "get-sink-mute", "@DEFAULT_SINK@"):
                return 0, "Mute: no", ""
            if key == ("brightnessctl", "-m"):
                return 0, "backlight,intel_backlight,50,100,50%", ""
            if key == ("bluetoothctl", "show"):
                return 0, "Controller 00:11:22:33:44:55\n\tPowered: yes", ""
            if key == ("upower", "-e"):
                return 0, "/org/freedesktop/UPower/devices/DisplayDevice", ""
            if key == ("upower", "-i", "/org/freedesktop/UPower/devices/DisplayDevice"):
                return 0, "percentage: 75%", ""
            return 1, "", "unsupported"

        with tempfile.TemporaryDirectory() as directory:
            backlight = pathlib.Path(directory) / "backlight"
            (backlight / "intel_backlight").mkdir(parents=True)
            controller = module.DeviceController(
                runner=runner, executable=lambda _name: "/usr/bin/tool",
                backlight_root=backlight, network_backend=NetworkBackend())
            status = controller.widget_status()

        self.assertTrue(status["audio"]["available"])
        self.assertEqual("ready", status["wifi"]["state"])
        flattened = " ".join(" ".join(command) for command in commands)
        for forbidden in ("lspci", "lsusb", "journalctl", "pactl list cards"):
            self.assertNotIn(forbidden, flattened)

    def test_session_uses_event_monitor_and_organizer_has_no_periodic_fallback(self):
        source = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("ming-window-resource-monitor", source)
        self.assertIn("start_resource_monitor", source)
        health = source[source.index("cat > /usr/local/bin/ming-session-healthcheck"):source.index("MINGSESSIONHEALTH")]
        self.assertNotIn("xprop -root _NET_ACTIVE_WINDOW", health)
        self.assertNotIn("wmctrl -lp", health)
        organizer_start = source.index("cat > /usr/local/bin/ming-desktop-organizer")
        organizer = source[organizer_start:source.index("\nDESKORG\n", organizer_start)]
        self.assertNotIn("--watch", organizer)
        self.assertNotIn("sleep 20", organizer)
        self.assertNotIn("xdg-desktop-menu forceupdate", organizer)
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn("gir1.2-wnck-3.0", build)
        self.assertIn("usr/local/bin/ming-window-resource-monitor", build)
        self.assertIn("background resource policy must not impose a shared hard CPU quota", build)
        helper_start = build.index(
            'for helper in [\n    "usr/local/bin/ming-network-repair"')
        helper_gate = build[helper_start:build.index("\n]:", helper_start)]
        self.assertEqual(
            1,
            helper_gate.count('"usr/local/bin/ming-window-resource-monitor"'),
        )
        self.assertIn("readonly SUPERVISOR_INTERVAL=30", source)
        self.assertNotIn("sleep 10", source)
        self.assertIn("now >= last_attempt", source)
        self.assertNotIn(
            "Exec=/usr/local/bin/ming-window-manager-watchdog --session",
            source,
        )
        monitor_source = (ROOT / "assets" / "ming-window-resource-monitor.py").read_text(
            encoding="utf-8")
        self.assertIn("window-manager-changed", monitor_source)
        self.assertIn("--repair-if-needed", monitor_source)
        self.assertIn('"on_window_manager_changed"', build)
        self.assertIn("polling window-manager watchdog autostart must be absent", build)

    def test_window_monitor_tracks_events_without_x11_polling(self):
        path = ROOT / "assets" / "ming-window-resource-monitor.py"
        self.assertTrue(path.is_file())
        source = path.read_text(encoding="utf-8")
        for marker in (
            "active-window-changed",
            "window-opened",
            "window-closed",
            "state-changed",
            "HIDDEN_DELAY_MS = 10_000",
            "self.GLib.timeout_add(",
            "subprocess.Popen",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("xprop", source)
        self.assertNotIn("wmctrl", source)

    def test_window_manager_repair_failure_has_one_deduplicated_delayed_retry(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_repair_retry_test",
        )

        class FakeGLib:
            timers = []

            @classmethod
            def timeout_add(cls, delay, callback, *args):
                cls.timers.append((delay, callback, args))
                return len(cls.timers)

        class FakeScreen:
            pass

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()
                self.repairs = 0

            def repair_window_manager(self):
                self.repairs += 1
                return object()

        client = FakeClient()
        monitor = module.WnckResourceMonitor(FakeGLib, FakeScreen(), client)
        monitor._observe_window_manager_repair = lambda _process, _retry: None

        monitor.on_window_manager_changed()
        monitor.on_window_manager_changed()
        self.assertEqual(1, client.repairs, "an active repair must deduplicate event storms")

        monitor._finish_window_manager_repair(False, 1)
        self.assertEqual(1, len(FakeGLib.timers))
        delay, callback, args = FakeGLib.timers.pop()
        self.assertEqual(module.WINDOW_MANAGER_RETRY_DELAY_MS, delay)
        monitor.on_window_manager_changed()
        self.assertEqual(1, client.repairs, "a pending retry must deduplicate new events")

        self.assertFalse(callback(*args))
        self.assertEqual(2, client.repairs)
        monitor._finish_window_manager_repair(True, 124)
        self.assertEqual([], FakeGLib.timers, "the bounded retry must not recurse")
        monitor.on_window_manager_changed()
        self.assertEqual(2, client.repairs, "failed retry must enter monotonic backoff")

    def test_window_manager_repair_observer_is_bounded_off_the_gtk_main_loop(self):
        source = (ROOT / "assets" / "ming-window-resource-monitor.py").read_text(
            encoding="utf-8")
        for marker in (
            "threading.Thread",
            "process.wait(timeout=",
            "subprocess.TimeoutExpired",
            "self.GLib.idle_add",
            "WINDOW_MANAGER_HELPER_TIMEOUT_SECONDS",
        ):
            self.assertIn(marker, source)

    def test_installed_rootfs_gate_validates_performance_runtime_contracts(self):
        build = BUILD.read_text(encoding="utf-8")
        gate = build[
            build.index("validate_r4_compatibility() {"):
            build.index("write_grub_config() {")
        ]
        for marker in (
            'validate_generated_executable("usr/local/bin/ming-window-resource-monitor", "python")',
            'validate_generated_executable("usr/local/bin/ming-session-healthcheck", "bash")',
            'session_healthcheck = require_file("usr/local/bin/ming-session-healthcheck"',
            'window_watchdog = require_file("usr/local/bin/ming-window-manager-watchdog"',
            '"readonly SUPERVISOR_INTERVAL=30"',
            '"monotonic_seconds"',
            "import ast",
            "import inspect",
            "import importlib.util",
            "def load_python_runtime",
            "def require_python_class",
            "def require_python_method",
            "ast.parse",
            'require_python_class(performance_policy_path, "ResourcePolicy")',
            'require_python_method(performance_policy_module.ResourcePolicy, "apply_background"',
            'require_python_class(window_resource_monitor_path, "WnckResourceMonitor")',
        ):
            self.assertIn(marker, gate)
        for fragile_marker in (
            '"threading.Timer(delay, self._expire_lease"',
            '"self.leases.active(str(token))"',
            '"Gio.FileMonitorFlags.WATCH_MOVES"',
        ):
            self.assertNotIn(fragile_marker, gate)
        self.assertNotIn("assets/ming-window-resource-monitor.py", gate)

    def test_policy_client_orders_background_transitions_by_generation(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_generation_test",
        )
        state = module.EventState()
        client = module.PolicyClient(state)
        commands = []
        with mock.patch.object(module.time, "monotonic_ns", return_value=1000), \
                mock.patch.object(client, "_spawn", side_effect=lambda argv, **_kwargs: commands.append(argv)):
            client.background(42, "100", False)
            client.background(42, "100", True)
            client.background(42, "200", False)

        for command in commands:
            self.assertIn("--generation", command)
        generations = [
            command[command.index("--generation") + 1]
            for command in commands
        ]
        self.assertEqual(["1000", "1001", "1000"], generations)

    def test_background_generation_survives_monitor_restart(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_restart_generation_test",
        )
        commands = []

        def issue(state):
            client = module.PolicyClient(state)
            with mock.patch.object(
                client, "_spawn", side_effect=lambda argv, **_kwargs: commands.append(argv)
            ):
                client.background(42, "100", False)

        with mock.patch.object(module.time, "monotonic_ns", side_effect=(1000, 2000)):
            issue(module.EventState())
            issue(module.EventState())

        generations = [
            int(command[command.index("--generation") + 1])
            for command in commands
        ]
        self.assertGreater(generations[1], generations[0])

    def test_each_attached_window_subscribes_to_workspace_changes(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_workspace_test",
        )

        class FakeGLib:
            @staticmethod
            def timeout_add(*_args):
                return 1

        class FakeScreen:
            @staticmethod
            def get_active_workspace():
                return None

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()

        class FakeWindow:
            def __init__(self):
                self.signals = []

            @staticmethod
            def get_xid():
                return 1

            @staticmethod
            def get_pid():
                return 0

            def connect(self, signal, callback):
                self.signals.append((signal, callback))
                return len(self.signals)

        window = FakeWindow()
        monitor = module.WnckResourceMonitor(FakeGLib, FakeScreen(), FakeClient())
        monitor.attach(window)

        self.assertIn("workspace-changed", [signal for signal, _callback in window.signals])

    def test_recovery_build_installs_and_verifies_the_wnck_runtime(self):
        resume = RESUME.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            resume.count("gir1.2-wnck-3.0"),
            2,
            "resume builds must both install and verify the Wnck typelib",
        )

    def test_window_monitor_subscribes_before_enumerating_and_sets_client_type_first(self):
        source = (ROOT / "assets" / "ming-window-resource-monitor.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("Wnck.set_client_type(Wnck.ClientType.PAGER)"),
            source.index("Wnck.Screen.get_default()"),
        )
        self.assertLess(
            source.index('screen.connect("window-opened"'),
            source.index("screen.force_update()"),
        )
        desktop = DESKTOP.read_text(encoding="utf-8")
        startup = desktop[desktop.index("startup_once() {"):desktop.index("supervise_once() {")]
        self.assertLess(startup.index("start_resource_monitor"), startup.index("start_phone_desktop"))
        self.assertIn("RESOURCE_MONITOR_RETRY_SECONDS", desktop)

    def test_window_event_state_deduplicates_boosts_and_cancels_stale_hides(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_test",
        )
        state = module.EventState()
        self.assertTrue(state.allow_boost(42, "100", 10.0))
        self.assertFalse(state.allow_boost(42, "100", 10.5))
        self.assertTrue(state.allow_boost(42, "100", 11.6))
        generation = state.mark_hidden("0x2a", 42, "100", 20.0)
        self.assertFalse(state.hidden_ready("0x2a", generation, 29.9))
        self.assertTrue(state.hidden_ready("0x2a", generation, 30.0))
        repeated = state.mark_hidden("0x2a", 42, "100", 25.0)
        self.assertEqual(5_000, state.remaining_hidden_ms("0x2a", repeated, 25.0))
        self.assertTrue(state.hidden_ready("0x2a", repeated, 30.0))
        state.mark_visible("0x2a")
        self.assertFalse(state.hidden_ready("0x2a", repeated, 31.0))

    def test_hidden_window_does_not_throttle_a_process_with_another_visible_window(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_visibility_test",
        )

        class FakeGLib:
            @staticmethod
            def timeout_add(_delay, _callback, *_args):
                return 1

            @staticmethod
            def source_remove(_source):
                return None

        class FakeScreen:
            @staticmethod
            def get_active_workspace():
                return None

        class FakeWindow:
            def __init__(self, xid, minimized):
                self.xid = xid
                self.minimized = minimized

            def get_xid(self):
                return self.xid

            @staticmethod
            def get_pid():
                return os.getpid()

            def is_minimized(self):
                return self.minimized

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()
                self.calls = []

            def background(self, pid, starttime, visible):
                self.calls.append((pid, starttime, visible))

        client = FakeClient()
        monitor = module.WnckResourceMonitor(FakeGLib, FakeScreen(), client)
        hidden = FakeWindow(1, True)
        visible = FakeWindow(2, False)
        monitor.windows = {
            monitor.window_id(hidden): hidden,
            monitor.window_id(visible): visible,
        }
        key = monitor.window_id(hidden)
        generation = client.state.mark_hidden(
            key, os.getpid(), "100", module.time.monotonic() - 11.0)

        monitor.on_hidden_timeout(key, generation)

        self.assertEqual([], client.calls)

    def test_visible_window_restores_a_process_throttled_by_another_window(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_restore_test",
        )

        class FakeGLib:
            @staticmethod
            def timeout_add(_delay, _callback, *_args):
                return 1

            @staticmethod
            def source_remove(_source):
                return None

        class FakeScreen:
            @staticmethod
            def get_active_workspace():
                return None

        class FakeWindow:
            def __init__(self, xid, minimized):
                self.xid = xid
                self.minimized = minimized

            def get_xid(self):
                return self.xid

            @staticmethod
            def get_pid():
                return os.getpid()

            def is_minimized(self):
                return self.minimized

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()
                self.calls = []

            def background(self, pid, starttime, visible):
                self.calls.append((pid, starttime, visible))

        client = FakeClient()
        monitor = module.WnckResourceMonitor(FakeGLib, FakeScreen(), client)
        hidden = FakeWindow(1, True)
        visible = FakeWindow(2, False)
        monitor.windows = {
            monitor.window_id(hidden): hidden,
            monitor.window_id(visible): visible,
        }
        client.state.mark_backgrounded(os.getpid(), "100")

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.reconcile(visible)

        self.assertEqual([(os.getpid(), "100", True)], client.calls)

    def test_window_close_restores_or_rearms_process_policy(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_close_test",
        )

        class FakeGLib:
            @staticmethod
            def timeout_add(_delay, _callback, *_args):
                return 1

            @staticmethod
            def source_remove(_source):
                return None

        class FakeScreen:
            @staticmethod
            def get_active_workspace():
                return None

        class FakeWindow:
            def __init__(self, xid, minimized):
                self.xid = xid
                self.minimized = minimized

            def get_xid(self):
                return self.xid

            @staticmethod
            def get_pid():
                return os.getpid()

            def is_minimized(self):
                return self.minimized

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()
                self.calls = []

            def background(self, pid, starttime, visible):
                self.calls.append((pid, starttime, visible))

        client = FakeClient()
        monitor = module.WnckResourceMonitor(FakeGLib, FakeScreen(), client)
        hidden = FakeWindow(1, True)
        visible = FakeWindow(2, False)
        monitor.windows = {
            monitor.window_id(hidden): hidden,
            monitor.window_id(visible): visible,
        }
        client.state.mark_backgrounded(os.getpid(), "100")

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.detach(visible)

        self.assertEqual([(os.getpid(), "100", True)], client.calls)
        self.assertIn(monitor.window_id(hidden), monitor.timers)


if __name__ == "__main__":
    unittest.main()
