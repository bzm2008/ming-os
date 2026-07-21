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


def load_rootfs_ast_contract_helpers():
    build = BUILD.read_text(encoding="utf-8")
    gate = build[
        build.index("validate_r4_compatibility() {"):
        build.index("write_grub_config() {")
    ]
    source = gate.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    tree = ast.parse(source, filename=str(BUILD))
    helper_names = {"require_ast_class", "require_ast_method"}
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    namespace = {"ast": ast, "errors": []}
    module = ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[]))
    exec(compile(module, str(BUILD), "exec"), namespace)
    return namespace


def load_phone_subset(function_names=(), method_names=()):
    tree = ast.parse(PHONE.read_text(encoding="utf-8"))
    wanted_functions = set(function_names)
    body = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        or (isinstance(node, ast.Import)
            and all(alias.name != "gi" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module != "gi.repository")
        or (isinstance(node, ast.FunctionDef) and node.name in wanted_functions)
    ]
    if method_names:
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        body.extend(
            node for node in phone_class.body
            if isinstance(node, ast.FunctionDef) and node.name in set(method_names)
        )
    namespace = {
        "Path": pathlib.Path,
        "load_shell_common": lambda: None,
        "__file__": str(PHONE),
    }
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, str(PHONE), "exec"), namespace)
    return namespace


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

    def test_load_apps_bounds_catalog_enumeration_without_losing_core_discovery(self):
        namespace = load_phone_subset({"load_apps"})
        inspected_names = []
        scanned_roots = []
        closed_roots = []
        parsed = []
        core_calls = []
        logs = []

        class FakeEntry:
            def __init__(self, root, name):
                self._root = root
                self._name = name

            @property
            def name(self):
                inspected_names.append(self._name)
                return self._name

            @property
            def path(self):
                return str(self._root / self._name)

        class FakeScan:
            def __init__(self, root, names):
                self._root = root
                self._entries = iter(FakeEntry(root, name) for name in names)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._entries)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()
                return False

            def close(self):
                if self._root not in closed_roots:
                    closed_roots.append(self._root)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            roots = [root / ("root-%d" % index) for index in range(4)]
            entries_by_root = {
                roots[0]: ["z.desktop", "shared.desktop", "ignored.txt"],
                roots[1]: ["shared.desktop", "c.desktop", "later.desktop"],
                roots[2]: ["y.desktop", "x.desktop", "late.desktop"],
                roots[3]: ["never.desktop"],
            }
            for catalog_root in roots:
                catalog_root.mkdir()
                for name in entries_by_root[catalog_root]:
                    (catalog_root / name).write_text("entry\n", encoding="utf-8")

            def scandir(path):
                catalog_root = pathlib.Path(path)
                scanned_roots.append(catalog_root)
                return FakeScan(catalog_root, entries_by_root[catalog_root])

            def add_core_app(apps_by_basename, basename):
                core_calls.append(basename)
                apps_by_basename[basename] = {
                    "basename": basename,
                    "name": basename,
                    "path": "/core/%s" % basename,
                }
                return True

            def add_app_from_path(apps_by_basename, path, default_only=False):
                path = pathlib.Path(path)
                parsed.append((path, default_only))
                if path.name in apps_by_basename:
                    return False
                apps_by_basename[path.name] = {
                    "basename": path.name,
                    "name": path.stem,
                    "path": str(path),
                }
                return True

            namespace.update({
                "APP_DIRS": roots,
                "APP_CATALOG_MAX_ROOTS": 3,
                "APP_CATALOG_MAX_DIRECTORY_ENTRIES": 6,
                "APP_CATALOG_MAX_LAUNCHERS": 6,
                "CORE_NAMES": {"core-a.desktop", "core-b.desktop"},
                "DESKTOP_ORDER": {"core-b.desktop": 0, "core-a.desktop": 1},
                "os": types.SimpleNamespace(scandir=scandir),
                "add_core_app": add_core_app,
                "add_app_from_path": add_app_from_path,
                "log": logs.append,
            })
            apps = namespace["load_apps"](default_only=True)

        expected_paths = [
            roots[0] / "shared.desktop",
            roots[0] / "z.desktop",
            roots[1] / "c.desktop",
            roots[1] / "shared.desktop",
            roots[2] / "x.desktop",
            roots[2] / "y.desktop",
        ]
        self.assertEqual(["core-b.desktop", "core-a.desktop"], core_calls)
        self.assertEqual(roots[:3], scanned_roots)
        self.assertEqual(roots[:3], closed_roots)
        self.assertEqual(6, len(inspected_names))
        self.assertEqual(expected_paths, [path for path, _default_only in parsed])
        self.assertTrue(all(default_only for _path, default_only in parsed))
        self.assertEqual(str(roots[0] / "shared.desktop"), next(
            app["path"] for app in apps if app["basename"] == "shared.desktop"
        ))
        self.assertTrue({"core-a.desktop", "core-b.desktop"}.issubset(
            {app["basename"] for app in apps}
        ))
        self.assertEqual([
            "application catalog enumeration budget reached: root-limit=3, "
            "directory-entry-limit=6, launcher-limit=6",
        ], logs)

    def test_nondefault_catalog_discovers_core_before_budgeted_roots(self):
        namespace = load_phone_subset({"load_apps"})
        core_calls = []
        parsed = []
        logs = []

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "Desktop"
            system = root / "applications"
            desktop.mkdir()
            system.mkdir()
            user_duplicate = desktop / "ming-settings.desktop"
            user_duplicate.write_text("user duplicate\n", encoding="utf-8")
            (system / "later.desktop").write_text("later app\n", encoding="utf-8")

            def add_core_app(apps_by_basename, basename):
                core_calls.append(basename)
                apps_by_basename[basename] = {
                    "basename": basename,
                    "name": basename,
                    "path": "/protected/%s" % basename,
                }
                return True

            def add_app_from_path(apps_by_basename, path, default_only=False):
                path = pathlib.Path(path)
                parsed.append((path, default_only))
                if path.name in apps_by_basename:
                    return False
                apps_by_basename[path.name] = {
                    "basename": path.name,
                    "name": path.stem,
                    "path": str(path),
                }
                return True

            namespace.update({
                "APP_DIRS": [desktop, system],
                "APP_CATALOG_MAX_ROOTS": 2,
                "APP_CATALOG_MAX_DIRECTORY_ENTRIES": 1,
                "APP_CATALOG_MAX_LAUNCHERS": 1,
                "CORE_NAMES": {"ming-settings.desktop", "ming-files.desktop"},
                "DESKTOP_ORDER": {"ming-settings.desktop": 0, "ming-files.desktop": 1},
                "add_core_app": add_core_app,
                "add_app_from_path": add_app_from_path,
                "log": logs.append,
            })
            apps = namespace["load_apps"](default_only=False)

        apps_by_basename = {app["basename"]: app for app in apps}
        self.assertEqual(["ming-settings.desktop", "ming-files.desktop"], core_calls)
        self.assertEqual([(user_duplicate, False)], parsed)
        self.assertEqual(
            "/protected/ming-settings.desktop",
            apps_by_basename["ming-settings.desktop"]["path"],
        )
        self.assertEqual(
            "/protected/ming-files.desktop",
            apps_by_basename["ming-files.desktop"]["path"],
        )
        self.assertNotIn("later.desktop", apps_by_basename)
        self.assertEqual([
            "application catalog enumeration budget reached: "
            "directory-entry-limit=1, launcher-limit=1",
        ], logs)

    def test_exact_catalog_budget_reports_reached_without_probe(self):
        namespace = load_phone_subset({"load_apps"})
        next_calls = []
        closed = []
        logs = []

        with tempfile.TemporaryDirectory() as directory:
            catalog = pathlib.Path(directory) / "applications"
            catalog.mkdir()
            launcher = catalog / "only.desktop"

            class ExactScan:
                def __init__(self):
                    self._entries = iter((types.SimpleNamespace(
                        name=launcher.name,
                        path=str(launcher),
                    ),))

                def __iter__(self):
                    return self

                def __next__(self):
                    next_calls.append(True)
                    return next(self._entries)

                def close(self):
                    closed.append(True)

            def add_app_from_path(apps_by_basename, path, default_only=False):
                path = pathlib.Path(path)
                apps_by_basename[path.name] = {
                    "basename": path.name,
                    "name": path.stem,
                    "path": str(path),
                }
                return True

            scan = ExactScan()
            namespace.update({
                "APP_DIRS": [catalog],
                "APP_CATALOG_MAX_ROOTS": 1,
                "APP_CATALOG_MAX_DIRECTORY_ENTRIES": 1,
                "APP_CATALOG_MAX_LAUNCHERS": 1,
                "CORE_NAMES": set(),
                "DESKTOP_ORDER": {},
                "os": types.SimpleNamespace(scandir=lambda _path: scan),
                "add_core_app": lambda _apps, _basename: False,
                "add_app_from_path": add_app_from_path,
                "log": logs.append,
            })
            apps = namespace["load_apps"]()

        self.assertEqual(["only.desktop"], [app["basename"] for app in apps])
        self.assertEqual(1, len(next_calls))
        self.assertEqual([True], closed)
        self.assertEqual([
            "application catalog enumeration budget reached: "
            "directory-entry-limit=1, launcher-limit=1",
        ], logs)

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

    def test_external_catalog_event_with_stable_fingerprint_forces_one_sync(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        methods = {
            node.name: node for node in phone_class.body
            if isinstance(node, ast.FunctionDef)
        }
        selected = [
            methods["on_catalog_file_changed"],
            methods["_run_catalog_refresh"],
            methods["_schedule_catalog_sync_retry"],
            methods["_run_catalog_sync_retry"],
            methods["_clear_catalog_sync_retry"],
            methods["refresh_if_apps_changed"],
        ]
        timers = []

        class FakeGLib:
            @staticmethod
            def timeout_add(delay, callback, *args):
                timers.append((delay, callback, args))
                return len(timers)

            @staticmethod
            def source_remove(_source):
                return None

        stable_catalog = (("version", 4), ("external", "stable"))
        sync_calls = []
        layout = {"version": 4, "items": []}
        def sync_layout(width, report_status=False):
            sync_calls.append(width)
            updated = dict(layout)
            return (updated, True) if report_status else updated

        namespace = {
            "GLib": FakeGLib,
            "app_catalog_fingerprint": lambda: stable_catalog,
            "sync_layout": sync_layout,
            "load_layout": lambda: dict(layout),
            "layout_is_valid": lambda _layout, require_items=False: True,
            "LAYOUT_VERSION": 4,
            "CATALOG_SYNC_RETRY_DELAYS_MS": (1_000, 5_000, 30_000),
            "log": lambda _message: None,
        }
        exec(compile(
            ast.Module(body=selected, type_ignores=[]),
            str(PHONE),
            "exec",
        ), namespace)
        fake_screen = types.SimpleNamespace(get_width=lambda: 1366)
        desktop = types.SimpleNamespace(
            _catalog_dirty=False,
            _catalog_debounce_source=0,
            _catalog_sync_retry_source=0,
            _catalog_sync_retry_attempt=0,
            catalog_stamp=stable_catalog,
            appearance_stamp=(0, 0),
            layout_stamp=0,
            layout=dict(layout),
            current_layout_stamp=lambda: 0,
            current_appearance_stamp=lambda: (0, 0),
            get_screen=lambda: fake_screen,
            render=lambda: None,
        )
        for name in (
            "on_catalog_file_changed",
            "_run_catalog_refresh",
            "_schedule_catalog_sync_retry",
            "_run_catalog_sync_retry",
            "_clear_catalog_sync_retry",
            "refresh_if_apps_changed",
        ):
            setattr(desktop, name, types.MethodType(namespace[name], desktop))

        desktop.on_catalog_file_changed(None, None, None, None, True)
        _delay, callback, args = timers.pop(0)
        self.assertFalse(callback(*args))
        self.assertEqual(
            [1366],
            sync_calls,
            "a genuine Gio event is authoritative even when the bounded fingerprint is unchanged",
        )
        self.assertFalse(desktop._catalog_dirty)
        self.assertFalse(desktop.refresh_if_apps_changed())
        self.assertEqual([1366], sync_calls, "one external event must force exactly one sync")
        self.assertEqual([], timers)

    def test_idempotent_managed_proxy_write_does_not_queue_repeat_catalog_sync(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        refresh_method = next(
            node for node in phone_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "refresh_if_apps_changed"
        )
        clear_retry_method = next(
            node for node in phone_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_clear_catalog_sync_retry"
        )
        wanted = {
            "safe_name",
            "_desktop_has_marker",
            "_mark_desktop_file",
            "_confirm_file_durable",
            "_durable_replace",
            "write_managed_wrapper_proxy",
            "copy_desktop",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import)
            and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        )
        body.extend((clear_retry_method, refresh_method))
        namespace = {
            "Path": pathlib.Path,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE),
        }
        exec(compile(
            ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
            str(PHONE),
            "exec",
        ), namespace)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            applications = root / "applications"
            desktop_dir = root / "Desktop"
            applications.mkdir()
            desktop_dir.mkdir()
            source = applications / "store-wrapper.desktop"
            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=/usr/local/bin/store-wrapper\nIcon=store-wrapper\n",
                encoding="utf-8",
            )
            namespace["trusted_wrapper_source_path"] = (
                lambda path: pathlib.Path(path).resolve()
            )
            target = desktop_dir / "Store Wrapper.desktop"
            catalog_before = (("version", 4), ("external", "before"))
            catalog_after = (("version", 4), ("external", "after"))
            sync_calls = []
            pending_catalog_events = []
            target_replacements = []
            layout = {"version": 4, "items": []}
            original_replace = os.replace

            def observed_replace(source_path, target_path):
                original_replace(source_path, target_path)
                if pathlib.Path(target_path) == target:
                    target_replacements.append(target)

            def sync_layout(width, report_status=False):
                sync_calls.append(width)
                replacements_before = len(target_replacements)
                copied = namespace["copy_desktop"](
                    source, desktop_dir, name="Store Wrapper", managed=True)
                self.assertEqual(target, copied)
                if len(target_replacements) != replacements_before:
                    pending_catalog_events.append(True)
                updated = dict(layout)
                return (updated, True) if report_status else updated

            namespace.update({
                "app_catalog_fingerprint": lambda: catalog_after,
                "sync_layout": sync_layout,
                "load_layout": lambda: dict(layout),
                "layout_is_valid": lambda _layout, require_items=False: True,
                "LAYOUT_VERSION": 4,
            })
            fake_screen = types.SimpleNamespace(get_width=lambda: 1366)
            desktop = types.SimpleNamespace(
                _catalog_dirty=False,
                _catalog_debounce_source=0,
                _catalog_sync_retry_source=0,
                _catalog_sync_retry_attempt=0,
                catalog_stamp=catalog_before,
                appearance_stamp=(0, 0),
                layout_stamp=0,
                layout=dict(layout),
                current_layout_stamp=lambda: 0,
                current_appearance_stamp=lambda: (0, 0),
                get_screen=lambda: fake_screen,
                render=lambda: None,
            )
            desktop.refresh_if_apps_changed = types.MethodType(
                namespace["refresh_if_apps_changed"], desktop)
            desktop._clear_catalog_sync_retry = types.MethodType(
                namespace["_clear_catalog_sync_retry"], desktop)

            with mock.patch.object(os, "replace", side_effect=observed_replace):
                self.assertFalse(desktop.refresh_if_apps_changed())
                self.assertEqual([True], pending_catalog_events)
                pending_catalog_events.pop()
                desktop._catalog_dirty = True
                self.assertFalse(desktop.refresh_if_apps_changed())
                self.assertTrue(
                    namespace["write_managed_wrapper_proxy"](target, source))

        self.assertEqual(
            [1366, 1366],
            sync_calls,
            "the first write event needs one idempotent sync and no third pass",
        )
        self.assertEqual([target], target_replacements)
        self.assertEqual([], pending_catalog_events)

    def test_failed_catalog_reconciliation_retries_until_marker_write_succeeds(self):
        namespace = load_phone_subset({
            "safe_name",
            "_desktop_has_marker",
            "_mark_desktop_file",
            "_confirm_file_durable",
            "_manifest_relative",
            "empty_desktop_manifest",
            "write_managed_wrapper_proxy",
            "copy_desktop",
            "sync_files",
            "_durable_replace",
        }, {
            "_schedule_catalog_sync_retry",
            "_run_catalog_sync_retry",
            "_clear_catalog_sync_retry",
            "refresh_if_apps_changed",
        })
        retry_methods = {
            "_schedule_catalog_sync_retry",
            "_run_catalog_sync_retry",
            "_clear_catalog_sync_retry",
            "refresh_if_apps_changed",
        }
        timers = []
        removed_sources = []
        logs = []

        class FakeGLib:
            @staticmethod
            def timeout_add(delay, callback, *args):
                timers.append((delay, callback, args))
                return len(timers)

            @staticmethod
            def source_remove(source):
                removed_sources.append(source)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop_dir = root / "Desktop"
            source_dir = root / "applications"
            desktop_dir.mkdir()
            source_dir.mkdir()
            source = source_dir / "transient.desktop"
            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Transient\nExec=transient\n",
                encoding="utf-8",
            )
            target = desktop_dir / "Transient.desktop"
            original_target = (
                b"[Desktop Entry]\nX-Ming-Managed=true\n"
                b"Name=Last Good\nExec=last-good\n"
            )
            target.write_bytes(original_target)
            layout = {
                "version": 4,
                "items": [{
                    "type": "app",
                    "path": str(source),
                    "name": "Transient",
                    "pinned": True,
                }],
            }
            manifest = namespace["empty_desktop_manifest"]()
            manifest["managed_files"] = [target.name]
            manifest["managed"] = [target.name]
            real_marker = namespace["_mark_desktop_file"]
            saved_manifests = []

            def save_manifest(value):
                saved_manifests.append(value)
                return True

            namespace.update({
                "DESKTOP_DIR": desktop_dir,
                "GLib": FakeGLib,
                "_mark_desktop_file": lambda _path: False,
                "load_desktop_manifest": lambda: manifest,
                "save_desktop_manifest": save_manifest,
                "log": logs.append,
            })
            first_status = namespace["sync_files"](layout)
            self.assertIs(False, first_status)
            self.assertEqual(original_target, target.read_bytes())
            self.assertEqual([], saved_manifests)
            self.assertTrue(any("mark managed desktop launcher" in entry for entry in logs))
            self.assertTrue(retry_methods.issubset(namespace))
            self.assertEqual((1_000, 5_000, 30_000), namespace["CATALOG_SYNC_RETRY_DELAYS_MS"])

            def sync_layout(_width, report_status=False):
                nonlocal first_status
                status = first_status
                first_status = True
                if status is True:
                    namespace["_mark_desktop_file"] = real_marker
                    status = namespace["sync_files"](layout)
                result = dict(layout)
                return (result, status) if report_status else result

            namespace.update({
                "app_catalog_fingerprint": lambda: (("version", 4),),
                "sync_layout": sync_layout,
                "load_layout": lambda: dict(layout),
                "layout_is_valid": lambda _layout, require_items=False: True,
                "LAYOUT_VERSION": 4,
            })
            desktop = types.SimpleNamespace(
                _catalog_dirty=True,
                _catalog_sync_retry_source=0,
                _catalog_sync_retry_attempt=0,
                catalog_stamp=(("version", 4),),
                appearance_stamp=(0, 0),
                layout_stamp=0,
                layout=dict(layout),
                current_layout_stamp=lambda: 0,
                current_appearance_stamp=lambda: (0, 0),
                get_screen=lambda: types.SimpleNamespace(get_width=lambda: 1366),
                render=lambda: None,
            )
            for name in retry_methods:
                setattr(desktop, name, types.MethodType(namespace[name], desktop))

            self.assertFalse(desktop.refresh_if_apps_changed())
            self.assertTrue(desktop._catalog_dirty)
            self.assertEqual(1_000, timers[0][0])
            desktop._schedule_catalog_sync_retry()
            self.assertEqual(1, len(timers), "only one sync retry source may be pending")
            _delay, callback, args = timers.pop()
            self.assertFalse(callback(*args))

            self.assertFalse(desktop._catalog_dirty)
            self.assertEqual(0, desktop._catalog_sync_retry_attempt)
            self.assertEqual(0, desktop._catalog_sync_retry_source)
            self.assertEqual([], timers)
            self.assertEqual(1, len(saved_manifests))
            self.assertNotEqual(original_target, target.read_bytes())

            desktop._catalog_sync_retry_attempt = 99
            desktop._schedule_catalog_sync_retry()
            self.assertEqual(30_000, timers[0][0])
            desktop._schedule_catalog_sync_retry()
            self.assertEqual(1, len(timers), "capped retry must still use one source")
            desktop._clear_catalog_sync_retry()
            self.assertEqual([1], removed_sources)

    def test_layout_save_failure_does_not_reconcile_desktop_files(self):
        namespace = load_phone_subset({"sync_layout"})
        app_path = "/applications/alpha.desktop"
        layout = {
            "version": namespace["LAYOUT_VERSION"],
            "catalog_paths": [app_path],
            "items": [{
                "id": "alpha",
                "type": "app",
                "path": app_path,
                "x": 10,
                "y": 20,
                "pinned": True,
            }],
        }
        app = {
            "id": "alpha",
            "path": app_path,
            "basename": "alpha.desktop",
            "name": "Alpha",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            launcher = root / "Alpha.desktop"
            manifest = root / "desktop-generated-manifest.json"
            launcher.write_bytes(b"last-good-launcher")
            manifest.write_bytes(b"last-good-manifest")
            sync_calls = []

            def sync_files(_layout):
                sync_calls.append(True)
                launcher.write_bytes(b"mutated-launcher")
                manifest.write_bytes(b"mutated-manifest")
                return True

            namespace.update({
                "load_apps": lambda default_only=False: [dict(app)],
                "read_layout": lambda _path: dict(layout),
                "load_layout": lambda: dict(layout),
                "migrate_layout": lambda value: dict(value),
                "canonicalize_core_layout_item": lambda item, _apps, _seen: dict(item),
                "save_layout": lambda _layout: False,
                "sync_files": sync_files,
                "log": lambda _message: None,
            })

            updated, status = namespace["sync_layout"](report_status=True)

            self.assertEqual(app_path, updated["items"][0]["path"])
            self.assertIs(False, status)
            self.assertEqual([], sync_calls)
            self.assertEqual(b"last-good-launcher", launcher.read_bytes())
            self.assertEqual(b"last-good-manifest", manifest.read_bytes())

    def test_desktop_replacement_fsyncs_file_and_parent_directory(self):
        tree = ast.parse(PHONE.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_durable_replace", functions)
        for caller in ("write_managed_wrapper_proxy", "copy_desktop"):
            self.assertIn("_durable_replace", ast.unparse(functions[caller]))

        namespace = load_phone_subset({"_confirm_file_durable", "_durable_replace"})
        events = []
        descriptors = iter((41, 42))
        fake_os = types.SimpleNamespace(
            O_RDONLY=os.O_RDONLY,
            O_RDWR=os.O_RDWR,
            O_DIRECTORY=getattr(os, "O_DIRECTORY", 0),
            name="posix",
            open=lambda path, flags: (
                events.append(("open", pathlib.Path(path), flags))
                or next(descriptors)
            ),
            fsync=lambda descriptor: events.append(("fsync", descriptor)),
            close=lambda descriptor: events.append(("close", descriptor)),
            replace=lambda source, target: events.append((
                "replace", pathlib.Path(source), pathlib.Path(target))),
        )
        namespace["os"] = fake_os
        staged = pathlib.Path("/catalog/.app.desktop.stage")
        target = pathlib.Path("/catalog/app.desktop")

        namespace["_durable_replace"](staged, target)

        self.assertEqual([
            ("open", staged, os.O_RDWR),
            ("fsync", 41),
            ("close", 41),
            ("replace", staged, target),
            ("open", target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
            ("fsync", 42),
            ("close", 42),
        ], events)

    def test_unchanged_retry_confirms_durability_after_parent_fsync_failure(self):
        namespace = load_phone_subset({
            "safe_name",
            "_desktop_has_marker",
            "_mark_desktop_file",
            "_confirm_file_durable",
            "_durable_replace",
            "write_managed_wrapper_proxy",
            "copy_desktop",
        })
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.desktop"
            desktop_dir = root / "Desktop"
            desktop_dir.mkdir()
            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Durable\nExec=durable\n",
                encoding="utf-8",
            )
            target = desktop_dir / "Durable.desktop"
            real_confirm = namespace["_confirm_file_durable"]
            pending = namespace["_DESKTOP_DURABILITY_PENDING"]
            confirmations = []
            fail_target_once = True

            def flaky_confirm(path, data_synced=False):
                nonlocal fail_target_once
                resolved = pathlib.Path(path)
                confirmations.append((resolved, data_synced))
                if resolved == target and fail_target_once:
                    fail_target_once = False
                    raise OSError("parent directory fsync failed")
                return real_confirm(path, data_synced=data_synced)

            namespace.update({
                "_confirm_file_durable": flaky_confirm,
                "log": lambda _message: None,
            })

            self.assertIsNone(namespace["copy_desktop"](
                source, desktop_dir, name="Durable", managed=True))
            self.assertTrue(target.exists(), "replace completed before parent fsync failed")
            self.assertIn(str(target.absolute()), pending)
            self.assertEqual(target, namespace["copy_desktop"](
                source, desktop_dir, name="Durable", managed=True))
            self.assertNotIn(str(target.absolute()), pending)
            self.assertEqual([(target, True), (target, False)], confirmations)

            proxy = desktop_dir / "proxy.desktop"
            __import__("shutil").copy2(source, proxy)
            self.assertTrue(namespace["write_managed_wrapper_proxy"](proxy, source))
            pending.add(str(proxy.absolute()))
            confirmations.clear()
            self.assertTrue(namespace["write_managed_wrapper_proxy"](proxy, source))
            self.assertEqual([(proxy, False)], confirmations)
            self.assertNotIn(str(proxy.absolute()), pending)

    def test_durable_replace_prunes_missing_pending_targets_at_capacity(self):
        namespace = load_phone_subset({"_confirm_file_durable", "_durable_replace"})
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            staged = root / "staged.desktop"
            target = root / "target.desktop"
            staged.write_bytes(b"durable")
            pending = namespace["_DESKTOP_DURABILITY_PENDING"]
            namespace["DESKTOP_DURABILITY_PENDING_MAX"] = 2
            pending.update({
                str((root / "missing-a.desktop").absolute()),
                str((root / "missing-b.desktop").absolute()),
            })

            try:
                namespace["_durable_replace"](staged, target)
            except OSError as exc:
                self.fail("missing pending targets must be pruned: %s" % exc)

            self.assertEqual(b"durable", target.read_bytes())
            self.assertEqual(set(), pending)

    def test_unsupported_directory_fsync_clears_pending_but_eio_retries(self):
        errno = __import__("errno")
        namespace = load_phone_subset({"_confirm_file_durable"})
        target = pathlib.Path("/catalog/app.desktop")
        key = str(target.absolute())
        pending = namespace["_DESKTOP_DURABILITY_PENDING"]
        failure = OSError(errno.EINVAL, "directory fsync unsupported")
        logs = []

        def fsync(_descriptor):
            raise failure

        namespace.update({
            "os": types.SimpleNamespace(
                O_RDONLY=os.O_RDONLY,
                O_DIRECTORY=getattr(os, "O_DIRECTORY", 0),
                name="posix",
                open=lambda _path, _flags: 42,
                fsync=fsync,
                close=lambda _descriptor: None,
            ),
            "log": logs.append,
        })
        pending.add(key)
        try:
            unsupported_result = namespace["_confirm_file_durable"](
                target, data_synced=True)
        except OSError:
            unsupported_result = False
        self.assertTrue(unsupported_result)
        self.assertNotIn(key, pending)
        self.assertTrue(logs)

        failure = OSError(errno.EIO, "real storage error")
        pending.add(key)
        with self.assertRaises(OSError):
            namespace["_confirm_file_durable"](target, data_synced=True)
        self.assertIn(key, pending)

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

    def test_rootfs_ast_contract_requires_positional_parameters(self):
        helpers = load_rootfs_ast_contract_helpers()
        require_method = helpers["require_ast_method"]
        required = ("pid", "starttime", "reason")
        positional = ast.parse(
            "class ResourcePolicy:\n"
            "    def begin(self, pid, starttime, reason):\n"
            "        pass\n"
        )
        self.assertIsNotNone(
            require_method(positional, "ResourcePolicy", "begin", required))
        self.assertEqual([], helpers["errors"])

        keyword_only = ast.parse(
            "class ResourcePolicy:\n"
            "    def begin(self, *, pid, starttime, reason):\n"
            "        pass\n"
        )
        require_method(keyword_only, "ResourcePolicy", "begin", required)
        self.assertEqual(
            ["Python AST ResourcePolicy.begin missing parameters: "
             "pid, starttime, reason"],
            helpers["errors"],
        )

    def test_rootfs_ast_contract_uses_final_class_and_method_definitions(self):
        fixtures = {
            "class": (
                "class ResourcePolicy:\n"
                "    def begin(self, pid, starttime, reason):\n"
                "        pass\n"
                "class ResourcePolicy:\n"
                "    def begin(self, pid):\n"
                "        pass\n"
            ),
            "method": (
                "class ResourcePolicy:\n"
                "    def begin(self, pid, starttime, reason):\n"
                "        pass\n"
                "    def begin(self, pid):\n"
                "        pass\n"
            ),
        }
        for binding, source in fixtures.items():
            with self.subTest(binding=binding):
                helpers = load_rootfs_ast_contract_helpers()
                helpers["require_ast_method"](
                    ast.parse(source),
                    "ResourcePolicy",
                    "begin",
                    ("pid", "starttime", "reason"),
                )
                self.assertEqual(
                    ["Python AST ResourcePolicy.begin missing parameters: "
                     "starttime, reason"],
                    helpers["errors"],
                )

    def test_rootfs_performance_contract_gate_is_ast_only(self):
        build = BUILD.read_text(encoding="utf-8")
        gate = build[
            build.index("validate_r4_compatibility() {"):
            build.index("write_grub_config() {")
        ]
        for forbidden in (
            "importlib",
            "import inspect",
            "load_python_runtime",
            "spec_from_file_location",
            "exec_module",
        ):
            self.assertNotIn(forbidden, gate)
        for marker in (
            "def load_python_ast",
            "def require_ast_class",
            "def require_ast_method",
            "required_parameters",
            "ast.parse",
            'environment["PYTHONPYCACHEPREFIX"] = pycache',
            "performance_policy_tree = load_python_ast(performance_policy_path)",
            'performance_policy_tree, "ResourcePolicy", "begin",\n'
            '    ("pid", "starttime", "reason"),',
            'performance_policy_tree, "ResourcePolicy", "end", ("token",)',
            'performance_policy_tree, "ResourcePolicy", "apply_background", (\n'
            '        "pid", "starttime", "desktop_file", "visible", "generation",',
            'performance_policy_tree, "ResourcePolicy", "status"',
            'performance_policy_tree, "ResourcePolicy", "_prune_background_state"',
            "window_resource_monitor_tree = load_python_ast(window_resource_monitor_path)",
            'window_resource_monitor_tree, "EventState", "next_background_generation",\n'
            '    ("pid", "starttime"),',
            'window_resource_monitor_tree, "PolicyClient", "background",\n'
            '    ("pid", "starttime", "visible"),',
            'window_resource_monitor_tree, "WnckResourceMonitor", "on_window_workspace_changed",\n'
            '    ("window",),',
            'window_resource_monitor_tree, "WnckResourceMonitor", "on_window_manager_changed"',
        ):
            self.assertIn(marker, gate)

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
            "def load_python_ast",
            "def require_ast_class",
            "def require_ast_method",
            "required_parameters",
            "ast.parse",
            "performance_policy_tree = load_python_ast(performance_policy_path)",
            'performance_policy_tree, "ResourcePolicy", "apply_background"',
            "window_resource_monitor_tree = load_python_ast(window_resource_monitor_path)",
            'window_resource_monitor_tree, "WnckResourceMonitor", "on_window_manager_changed"',
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
        client.state.remember_window_identity(
            monitor.window_id(hidden), os.getpid(), "100")
        client.state.remember_window_identity(
            monitor.window_id(visible), os.getpid(), "100")
        key = monitor.window_id(hidden)
        generation = client.state.mark_hidden(
            key, os.getpid(), "100", module.time.monotonic() - 11.0)

        with mock.patch.object(module, "process_starttime", return_value="100"):
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
        client.state.remember_window_identity(
            monitor.window_id(hidden), os.getpid(), "100")
        client.state.remember_window_identity(
            monitor.window_id(visible), os.getpid(), "100")
        client.state.mark_backgrounded(os.getpid(), "100")

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.reconcile(visible)

        self.assertEqual([(os.getpid(), "100", True)], client.calls)

    @staticmethod
    def _window_identity_fixture(module):
        scheduled = []
        removed = []
        source_id = [0]

        class FakeGLib:
            @staticmethod
            def timeout_add(delay, callback, *args):
                source_id[0] += 1
                scheduled.append((source_id[0], delay, callback, args))
                return source_id[0]

            @staticmethod
            def source_remove(source):
                removed.append(source)

        class FakeClient:
            def __init__(self):
                self.state = module.EventState()
                self.calls = []

            def background(self, pid, starttime, visible):
                self.calls.append((pid, starttime, visible))

            @staticmethod
            def boost(_pid, _reason):
                return None

        screen = types.SimpleNamespace(get_active_workspace=lambda: None)
        client = FakeClient()
        monitor = module.WnckResourceMonitor(FakeGLib, screen, client)

        def make_window(xid, pid, minimized):
            pid_value = [pid]
            minimized_value = [minimized]
            return types.SimpleNamespace(
                pid_value=pid_value,
                minimized_value=minimized_value,
                get_xid=lambda: xid,
                get_pid=lambda: pid_value[0],
                is_minimized=lambda: minimized_value[0],
                connect=lambda *_args: 1,
            )

        return monitor, client, scheduled, removed, make_window

    def test_detach_after_process_exit_reclaims_old_background_identity_without_restore(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_exit_identity_test",
        )
        monitor, client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        pid = 4242
        identity = (pid, "100")
        window = make_window(1, pid, True)

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.attach(window)
        client.state.mark_backgrounded(*identity)
        client.state.next_background_generation(*identity)

        with mock.patch.object(module, "process_starttime", return_value=None):
            monitor.detach(window)

        self.assertEqual([], client.calls)
        self.assertFalse(client.state.is_backgrounded(*identity))
        self.assertNotIn(identity, client.state.background_generations)

    def test_detach_after_pid_reuse_never_restores_new_identity(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_reused_pid_test",
        )
        monitor, client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        pid = 4242
        old_identity = (pid, "100")
        new_identity = (pid, "200")
        window = make_window(1, pid, True)

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.attach(window)
        client.state.backgrounded.update((old_identity, new_identity))
        client.state.background_generations.update({old_identity: 10, new_identity: 20})

        with mock.patch.object(module, "process_starttime", return_value="200"):
            monitor.detach(window)

        self.assertEqual([], client.calls)
        self.assertNotIn(old_identity, client.state.backgrounded)
        self.assertNotIn(old_identity, client.state.background_generations)
        self.assertIn(new_identity, client.state.backgrounded)
        self.assertIn(new_identity, client.state.background_generations)

    def test_same_identity_window_close_restores_and_rearms_remaining_window(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_multiwindow_identity_test",
        )
        monitor, client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        pid = 4242
        identity = (pid, "100")
        closing = make_window(1, pid, True)
        remaining = make_window(2, pid, True)

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.attach(closing)
            monitor.attach(remaining)
        remaining_key = monitor.window_id(remaining)
        monitor.cancel_timer(remaining_key)
        client.state.mark_backgrounded(*identity)
        client.state.next_background_generation(*identity)
        closing.pid_value[0] = 0

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.detach(closing)

        self.assertEqual([(pid, "100", True)], client.calls)
        self.assertIn(remaining_key, monitor.windows)
        self.assertIn(remaining_key, monitor.timers)
        self.assertEqual(pid, client.state.hidden[remaining_key]["pid"])
        self.assertEqual("100", client.state.hidden[remaining_key]["starttime"])

    def test_visible_window_matching_requires_same_process_starttime(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_visible_identity_test",
        )
        monitor, _client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        pid = 4242
        visible = make_window(2, pid, False)

        with mock.patch.object(module, "process_starttime", return_value="200"):
            monitor.attach(visible)
        with mock.patch.object(module, "process_starttime", return_value="100"):
            self.assertFalse(monitor.process_has_visible_window(pid))
        with mock.patch.object(module, "process_starttime", return_value="200"):
            self.assertTrue(monitor.process_has_visible_window(pid))

    def test_window_identity_state_tables_have_explicit_bounds(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_identity_bounds_test",
        )
        self.assertTrue(hasattr(module, "MAX_TRACKED_WINDOWS"))
        self.assertTrue(hasattr(module, "MAX_TRACKED_PROCESS_IDENTITIES"))
        monitor, client, _scheduled, _removed, _make_window = (
            self._window_identity_fixture(module)
        )

        with mock.patch.object(module, "MAX_TRACKED_WINDOWS", 2), \
                mock.patch.object(module, "MAX_TRACKED_PROCESS_IDENTITIES", 2):
            for index in range(5):
                client.state.allow_boost(index, str(index), 10.0)
                client.state.mark_hidden(str(index), index, str(index), float(index))
                client.state.mark_backgrounded(index, str(index))
                client.state.next_background_generation(index, str(index))
                client.state.remember_window_identity(str(index), index, str(index))

        process_identities = (
            set(client.state.backgrounded)
            | set(client.state.background_generations)
            | set(client.state.boosted)
        )
        self.assertLessEqual(len(client.state.hidden), 2)
        self.assertLessEqual(len(process_identities), 2)
        self.assertLessEqual(len(client.state.window_identities), 2)

    def test_detach_without_confirmed_identity_never_restores_current_pid(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_missing_identity_test",
        )
        monitor, client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        pid = 4242
        window = make_window(1, pid, True)
        monitor.windows[monitor.window_id(window)] = window
        client.state.mark_backgrounded(pid, "200")
        with mock.patch.object(module, "process_starttime", return_value="200"):
            monitor.detach(window)
        self.assertEqual([], client.calls)
        self.assertTrue(client.state.is_backgrounded(pid, "200"))

    def test_unconfirmed_window_identity_is_not_counted_as_visible(self):
        module = load_module(
            ROOT / "assets" / "ming-window-resource-monitor.py",
            "ming_window_resource_monitor_unconfirmed_visibility_test",
        )
        monitor, _client, _scheduled, _removed, make_window = (
            self._window_identity_fixture(module)
        )
        window = make_window(1, 4242, False)
        monitor.windows[monitor.window_id(window)] = window
        self.assertFalse(monitor.process_has_visible_window(4242, "200"))

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
        client.state.remember_window_identity(
            monitor.window_id(hidden), os.getpid(), "100")
        client.state.remember_window_identity(
            monitor.window_id(visible), os.getpid(), "100")
        client.state.mark_backgrounded(os.getpid(), "100")

        with mock.patch.object(module, "process_starttime", return_value="100"):
            monitor.detach(visible)

        self.assertEqual([(os.getpid(), "100", True)], client.calls)
        self.assertIn(monitor.window_id(hidden), monitor.timers)


if __name__ == "__main__":
    unittest.main()
