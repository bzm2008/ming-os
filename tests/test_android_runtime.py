import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_android_runtime", ROOT / "assets" / "ming-android-runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
if SPEC.loader is not None:
    SPEC.loader.exec_module(MODULE)


class AndroidRuntimeContracts(unittest.TestCase):
    def test_waydroid_package_name_readback_has_true_false_and_unknown_states(self):
        responses = {
            "list": (0, "packageName: org.example.demo\npackageName: org.example.other\n", ""),
        }

        def runner(command, **_kwargs):
            if tuple(command)[:3] == ("waydroid", "app", "list"):
                return responses["list"]
            return 0, "", ""

        with tempfile.TemporaryDirectory() as directory:
            runtime = MODULE.AndroidRuntime(home=pathlib.Path(directory), runner=runner)
            self.assertIs(True, runtime._list_readback("org.example.demo"))
            self.assertIs(False, runtime._list_readback("org.example.missing"))

            def unavailable(_command, **_kwargs):
                raise OSError("waydroid unavailable")

            runtime.runner = unavailable
            self.assertIsNone(runtime._list_readback("org.example.demo"))

    def test_x86_only_apk_is_rejected_by_stable_abi_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            runtime = MODULE.AndroidRuntime(
                home=root,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86"],
                },
            )
            result = runtime.validate_apk(apk)
            self.assertEqual("unsupported_architecture", result["state"])

    def test_apk_validation_rejects_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside"
            outside.mkdir()
            apk = outside / "demo.apk"
            apk.write_bytes(b"apk")
            parent = root / "imports"
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(
                home=root,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            result = runtime.validate_apk(parent / "demo.apk")
            self.assertEqual("invalid_source", result["state"])

    def test_production_launch_uses_async_popen_and_runtime_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            calls = []

            class Process:
                pid = 1234

                def poll(self):
                    return None

            def spawner(command, **kwargs):
                calls.append((tuple(command), kwargs))
                return Process()

            runtime = MODULE.AndroidRuntime(
                home=root,
                production=True,
                spawner=spawner,
                executable=lambda name: "/usr/bin/" + name,
                probes={
                    "ram_mb": 8192,
                    "render_nodes": ["/dev/dri/renderD128"],
                    "binderfs": True,
                    "lxc": True,
                    "dbus": True,
                    "wayland": True,
                },
            )
            result = runtime.launch(
                "org.example.demo", session_probe=lambda: True, process_probe=lambda: True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(("cage", "--", "waydroid", "app", "launch", "org.example.demo"), calls[0][0])

    def test_stale_lock_is_recovered_only_when_pid_is_known_dead(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            (state_dir / "cage.lock").write_text(
                json.dumps({
                    "package": "org.example.demo", "pid": 4321,
                    "argv": ["cage", "--", "waydroid", "app", "launch", "org.example.demo"],
                    "start_id": "old",
                }), encoding="utf-8"
            )
            (state_dir / "cage.lock").chmod(0o600)
            calls = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                if tuple(command[:3]) == ("ps", "-p", "4321"):
                    return 1, "", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    return 1, "", ""
                return 0, "", ""

            class Process:
                pid = 4322

                def poll(self):
                    return None

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                spawner=lambda *_args, **_kwargs: Process(),
                sleeper=lambda _seconds: None,
            )
            result = runtime.launch("org.example.demo")
            self.assertTrue(result["ok"])
            self.assertNotEqual("already_running", result["state"])

    def test_stop_with_pidless_lock_fails_closed_without_waydroid_app_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(json.dumps({"package": "org.example.demo", "pid": None}), encoding="utf-8")
            lock.chmod(0o600)
            calls = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(home=root, runner=runner)
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertFalse(any(call[:3] == ("waydroid", "app", "stop") for call in calls))

    def test_uninstall_unknown_list_readback_keeps_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            metadata = app_dir / "metadata.json"
            metadata.write_text('{"package":"org.example.demo","state":"installed"}', encoding="utf-8")
            metadata.chmod(0o600)

            def runner(command, **_kwargs):
                if tuple(command)[:3] == ("waydroid", "app", "remove"):
                    return 0, "Success", ""
                raise OSError("Waydroid list unavailable")

            runtime = MODULE.AndroidRuntime(home=root, runner=runner)
            result = runtime.uninstall("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("uninstall_unconfirmed", result["state"])
            self.assertTrue(metadata.exists())

    def test_uninstall_stops_active_process_before_removing_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            metadata = app_dir / "metadata.json"
            metadata.write_text('{"package":"org.example.demo","state":"installed"}', encoding="utf-8")
            metadata.chmod(0o600)
            listed = {"org.example.demo"}
            events = []

            def runner(command, **_kwargs):
                command = tuple(command)
                events.append(command[:3])
                if command[:3] == ("waydroid", "app", "remove"):
                    listed.clear()
                    return 0, "Success", ""
                if command[:3] == ("waydroid", "app", "list"):
                    return 0, "\n".join("packageName: " + item for item in listed), ""
                return 0, "", ""

            class Process:
                pid = 1234

                def __init__(self):
                    self.terminated = False

                def poll(self):
                    return 0 if self.terminated else None

                def terminate(self):
                    events.append(("process", "terminate"))
                    self.terminated = True

                def wait(self, timeout=None):
                    self.terminated = True
                    return 0

            process = Process()
            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                spawner=lambda *_args, **_kwargs: process,
                sleeper=lambda _seconds: None,
            )
            self.assertTrue(runtime.launch("org.example.demo")["ok"])
            result = runtime.uninstall("org.example.demo")
            self.assertTrue(result["ok"])
            self.assertTrue(process.terminated)
            self.assertLess(events.index(("process", "terminate")), events.index(("waydroid", "app", "remove")))

    def test_stop_rejects_pid_reuse_when_start_identity_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(json.dumps({
                "package": "org.example.demo", "pid": 4321,
                "argv": ["cage", "--", "waydroid", "app", "launch", "org.example.demo"],
                "start_id": "old-start",
            }), encoding="utf-8")
            lock.chmod(0o600)
            killed = []

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=lambda command, **_kwargs: (
                    (0, "cage -- waydroid app launch org.example.demo", "")
                    if tuple(command[:3]) == ("ps", "-p", "4321") else (1, "", "")
                ),
                terminator=lambda pid: killed.append(pid) or True,
            )
            runtime._proc_start_identity = lambda _pid: "new-start"
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(killed)

    def test_install_rolls_back_waydroid_when_local_metadata_commit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk payload")
            calls = []

            def runner(command, **_kwargs):
                command = tuple(command)
                calls.append(command)
                if command[:3] == ("waydroid", "app", "list"):
                    return 0, "packageName: org.example.demo", ""
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            with mock.patch.object(runtime, "_desktop_file", side_effect=MODULE.AndroidStorageError("commit failed")):
                result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertIn(("waydroid", "app", "remove", "org.example.demo"), calls)

    def test_failed_new_install_removes_desktop_entries_and_confirms_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk payload")
            listed = {"org.example.demo"}

            def runner(command, **_kwargs):
                command = tuple(command)
                if command[:3] == ("waydroid", "app", "list"):
                    return 0, "\n".join("packageName: " + item for item in listed), ""
                if command[:3] == ("waydroid", "app", "remove"):
                    listed.clear()
                    return 0, "Success", ""
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            with mock.patch.object(runtime, "_atomic_json", side_effect=MODULE.AndroidStorageError("commit failed")):
                result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("storage_insecure", result["state"])
            self.assertFalse((root / ".local/share/applications/ming-android-org-example-demo.desktop").exists())
            self.assertFalse((root / ".local/share/ming-android/targets/ming-android-target-org-example-demo.desktop").exists())

    def test_failed_rollback_is_reported_after_remove_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk payload")

            def runner(command, **_kwargs):
                command = tuple(command)
                if command[:3] == ("waydroid", "app", "list"):
                    return 0, "packageName: org.example.demo", ""
                if command[:3] == ("waydroid", "app", "remove"):
                    return 1, "", "remove failed"
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            with mock.patch.object(runtime, "_atomic_json", side_effect=MODULE.AndroidStorageError("commit failed")):
                result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("rollback_failed", result["state"])
            self.assertIn("回滚未确认", result["error"])

    def test_uninstall_rejects_symlinked_desktop_parent_before_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            apps = home / ".local/share/ming-android/apps/org.example.demo"
            apps.mkdir(parents=True)
            apps.parent.chmod(0o700)
            apps.parent.parent.chmod(0o700)
            apps.chmod(0o700)
            metadata = apps / "metadata.json"
            metadata.write_text('{"package":"org.example.demo","state":"installed"}', encoding="utf-8")
            metadata.chmod(0o600)
            outside = root / "outside"
            outside.mkdir()
            applications = home / ".local/share/applications"
            applications.parent.mkdir(parents=True, exist_ok=True)
            try:
                applications.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(
                home=home,
                runner=lambda command, **_kwargs: (
                    (0, "Success", "") if tuple(command)[:3] == ("waydroid", "app", "remove")
                    else (0, "", "")
                ),
            )
            result = runtime.uninstall("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("cleanup_failed", result["state"])

    def test_production_stop_requires_lock_start_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(json.dumps({
                "package": "org.example.demo", "pid": 4321,
                "argv": ["cage", "--", "waydroid", "app", "launch", "org.example.demo"],
            }), encoding="utf-8")
            lock.chmod(0o600)
            killed = []
            runtime = MODULE.AndroidRuntime(
                home=root,
                production=True,
                runner=lambda command, **_kwargs: (
                    (0, "cage -- waydroid app launch org.example.demo", "")
                    if tuple(command[:3]) == ("ps", "-p", "4321") else (1, "", "")
                ),
                terminator=lambda pid: killed.append(pid) or True,
            )
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(killed)
    def test_missing_kernel_or_display_prerequisites_is_unavailable(self):
        runtime = MODULE.AndroidRuntime(
            executable=lambda _name: None,
            probes={
                "ram_mb": 8192,
                "render_nodes": [],
                "binderfs": False,
                "lxc": False,
                "dbus": False,
                "wayland": False,
            },
        )
        result = runtime.status()
        self.assertFalse(result["ok"])
        self.assertEqual("unavailable", result["state"])
        self.assertIn("binderfs", " ".join(result["reasons"]))

    def test_ready_requires_gpu_and_four_gigabytes(self):
        runtime = MODULE.AndroidRuntime(
            executable=lambda name: "/usr/bin/" + name,
            probes={
                "ram_mb": 8192,
                "render_nodes": ["/dev/dri/renderD128"],
                "binderfs": True,
                "lxc": True,
                "dbus": True,
                "wayland": True,
            },
        )
        result = runtime.status()
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])

    def test_explicit_not_initialized_container_cannot_report_ready(self):
        runtime = MODULE.AndroidRuntime(
            executable=lambda name: "/usr/bin/" + name,
            probes={
                "ram_mb": 8192,
                "render_nodes": ["/dev/dri/renderD128"],
                "binderfs": True,
                "lxc": True,
                "dbus": True,
                "wayland": True,
                "container": "not_initialized",
            },
        )
        result = runtime.status()
        self.assertFalse(result["ok"])
        self.assertEqual("unavailable", result["state"])
        self.assertEqual("not_initialized", result["container"])

    def test_not_running_status_is_not_mistaken_for_running(self):
        def runner(command, **_kwargs):
            if tuple(command) == ("waydroid", "status"):
                return 0, "Session: NOT RUNNING\nContainer: STOPPED\n", ""
            return 0, "", ""

        runtime = MODULE.AndroidRuntime(
            runner=runner,
            executable=lambda name: "/usr/bin/" + name,
            probes=None,
        )
        result = runtime.status()
        self.assertFalse(result["ok"])
        self.assertEqual("not_initialized", result["container"])

    def test_low_memory_is_blocked_in_stable_mode(self):
        runtime = MODULE.AndroidRuntime(
            executable=lambda name: "/usr/bin/" + name,
            probes={
                "ram_mb": 3072,
                "render_nodes": ["/dev/dri/renderD128"],
                "binderfs": True,
                "lxc": True,
                "dbus": True,
                "wayland": True,
            },
        )
        result = runtime.status()
        self.assertFalse(result["ok"])
        self.assertEqual("unavailable", result["state"])
        self.assertIn("memory", " ".join(result["reasons"]))

    def test_apk_validation_rejects_remote_symlink_and_arm_only_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            reader = lambda _path: {
                "package": "com.example.demo",
                "version_code": 1,
                "version_name": "1.0",
                "abis": ["arm64-v8a"],
            }
            with self.assertRaises(MODULE.ApkValidationError):
                MODULE.validate_apk("https://example.invalid/demo.apk", metadata_reader=reader)
            link = root / "link.apk"
            try:
                link.symlink_to(apk)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(MODULE.ApkValidationError):
                MODULE.validate_apk(link, metadata_reader=reader)
            with self.assertRaises(MODULE.ApkValidationError):
                MODULE.validate_apk(apk, metadata_reader=reader)

    def test_apk_install_is_read_back_and_creates_ming_launch_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk payload")
            calls = []

            def runner(command, **kwargs):
                calls.append((tuple(command), kwargs))
                if tuple(command[:3]) == ("waydroid", "app", "install"):
                    return 0, "Success", ""
                if tuple(command[:3]) == ("waydroid", "app", "list"):
                    return 0, "com.example.demo 1.0", ""
                return 0, "", ""

            reader = lambda _path: {
                "package": "com.example.demo",
                "version_code": 1,
                "version_name": "1.0",
                "abis": ["x86_64"],
            }
            manager = MODULE.AndroidAppManager(
                home=root / "home", runner=runner, metadata_reader=reader,
                runtime=MODULE.AndroidRuntime(
                    executable=lambda name: "/usr/bin/" + name,
                    probes={
                        "ram_mb": 8192,
                        "render_nodes": ["/dev/dri/renderD128"],
                        "binderfs": True,
                        "lxc": True,
                        "dbus": True,
                        "wayland": True,
                    },
                ),
            )
            result = manager.install(apk)
            self.assertTrue(result["ok"])
            self.assertEqual("installed", result["state"])
            self.assertEqual("com.example.demo", result["package_id"])
            self.assertIn("waydroid", calls[0][0])
            self.assertNotIn("sh", calls[0][0])
            desktop = pathlib.Path(result["desktop_file"])
            self.assertIn("ming-launch", desktop.read_text(encoding="utf-8"))
            metadata = json.loads(pathlib.Path(result["metadata_file"]).read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(b"apk payload").hexdigest(), metadata["sha256"])

    def test_uninstall_does_not_delete_another_android_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apps = root / "home/.local/share/ming-android/apps"
            apps.parent.mkdir(parents=True, exist_ok=True)
            apps.parent.chmod(0o700)
            apps.mkdir(parents=True, exist_ok=True)
            apps.chmod(0o700)
            for package in ("com.example.one", "com.example.two"):
                (apps / package).mkdir(parents=True)
                (apps / package).chmod(0o700)
                (apps / package / "metadata.json").write_text(json.dumps({"package_id": package}), encoding="utf-8")
                (apps / package / "metadata.json").chmod(0o600)
            calls = []
            manager = MODULE.AndroidAppManager(
                home=root / "home",
                runner=lambda command, **kwargs: calls.append(tuple(command)) or (0, "Success", ""),
                runtime=MODULE.AndroidRuntime(
                    executable=lambda name: "/usr/bin/" + name,
                    probes={"ram_mb": 8192, "render_nodes": ["/dev/dri/renderD128"], "binderfs": True, "lxc": True, "dbus": True, "wayland": True},
                ),
            )
            result = manager.uninstall("com.example.one")
            self.assertTrue(result["ok"])
            self.assertFalse((apps / "com.example.one").exists())
            self.assertTrue((apps / "com.example.two").exists())
            self.assertIn(("waydroid", "app", "remove", "com.example.one"), calls)

    def test_install_rejects_symlinked_package_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            home = root / "home"
            apps = home / ".local/share/ming-android/apps"
            apps.mkdir(parents=True)
            apps.chmod(0o700)
            outside = root / "outside"
            outside.mkdir()
            package_dir = apps / "com.example.demo"
            try:
                package_dir.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(
                home=home,
                runner=lambda *_args, **_kwargs: (0, "com.example.demo 1", ""),
                metadata_reader=lambda _path: {
                    "package": "com.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("storage_insecure", result["state"])
            self.assertIn("符号链接", result["error"])
            self.assertFalse((outside / "artifact.apk").exists())

    def test_install_rejects_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            home = root / "home"
            outside = root / "outside"
            outside.mkdir()
            home.mkdir()
            try:
                (home / ".local").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(
                home=home,
                runner=lambda *_args, **_kwargs: (0, "com.example.demo 1", ""),
                metadata_reader=lambda _path: {
                    "package": "com.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("storage_insecure", result["state"])
            self.assertIn("符号链接", result["error"])

    def test_install_rejects_symlinked_metadata_and_prefix(self):
        for symlink_name in ("metadata.json", "prefix"):
            with self.subTest(symlink_name=symlink_name), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                apk = root / "demo.apk"
                apk.write_bytes(b"apk")
                home = root / "home"
                app_dir = home / ".local/share/ming-android/apps/com.example.demo"
                app_dir.mkdir(parents=True)
                app_dir.chmod(0o700)
                outside = root / ("outside-" + symlink_name.replace(".", "-"))
                if symlink_name == "prefix":
                    outside.mkdir()
                else:
                    outside.write_text("{}", encoding="utf-8")
                try:
                    (app_dir / symlink_name).symlink_to(outside, target_is_directory=symlink_name == "prefix")
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks unavailable")
                runtime = MODULE.AndroidRuntime(
                    home=home,
                    runner=lambda *_args, **_kwargs: (0, "com.example.demo 1", ""),
                    metadata_reader=lambda _path: {
                        "package": "com.example.demo", "version_code": "1",
                        "version_name": "1.0", "abis": ["x86_64"],
                    },
                )
                result = runtime.install_apk(apk)
                self.assertFalse(result["ok"])
                self.assertEqual("storage_insecure", result["state"])
                self.assertIn("符号链接", result["error"])

    def test_install_rejects_existing_package_directory_without_private_mode(self):
        if os.name != "posix":
            self.skipTest("POSIX file modes are enforced in the Linux image")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            home = root / "home"
            app_dir = home / ".local/share/ming-android/apps/com.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.chmod(0o755)
            runtime = MODULE.AndroidRuntime(
                home=home,
                runner=lambda *_args, **_kwargs: (0, "com.example.demo 1", ""),
                metadata_reader=lambda _path: {
                    "package": "com.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("storage_insecure", result["state"])
            self.assertIn("0700", result["error"])

    def test_launch_rc_zero_without_session_or_process_readback_is_not_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            calls = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                if tuple(command[:3]) == ("waydroid", "app", "list"):
                    return 0, "org.example.demo 1", ""
                if tuple(command[:2]) == ("waydroid", "status"):
                    return 0, "Session: STOPPED", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    return 0, "", ""
                return 0, "", ""

            runtime = MODULE.AndroidRuntime(home=root, runner=runner, sleeper=lambda _seconds: None)
            result = runtime.launch("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("launch_unconfirmed", result["state"])
            self.assertFalse((root / ".local/state/ming-os/android/org.example.demo/cage.lock").exists())
            self.assertIn(("waydroid", "status"), calls)
            self.assertTrue(any(call[:2] == ("pgrep", "-af") for call in calls))

    def test_launch_rejects_not_running_session_even_if_stale_window_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            calls = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                if tuple(command[:3]) == ("waydroid", "app", "list"):
                    return 0, "org.example.demo 1", ""
                if tuple(command[:2]) == ("waydroid", "status"):
                    return 0, "Session: NOT RUNNING", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    return 1, "", ""
                if tuple(command[:2]) == ("xdotool", "search"):
                    return 0, "12345\n", ""
                return 0, "", ""

            runtime = MODULE.AndroidRuntime(
                home=root, runner=runner, executable=lambda name: "/usr/bin/xdotool" if name == "xdotool" else None,
                sleeper=lambda _seconds: None,
            )
            result = runtime.launch("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("launch_unconfirmed", result["state"])

    def test_stop_terminates_cage_process_and_reads_back_stopped_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            class Process:
                pid = 54321

                def __init__(self):
                    self.terminated = False

                def poll(self):
                    return 0 if self.terminated else None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    self.terminated = True
                    return 0

            process = Process()
            runtime = MODULE.AndroidRuntime(
                home=root,
                spawner=lambda *_args, **_kwargs: process,
                sleeper=lambda _seconds: None,
            )
            self.assertTrue(runtime.launch("org.example.demo")["ok"])
            result = runtime.stop("org.example.demo")
            self.assertTrue(result["ok"])
            self.assertEqual("stopped", result["state"])
            self.assertTrue(process.terminated)
            self.assertFalse((root / ".local/state/ming-os/android/org.example.demo/cage.lock").exists())

    def test_install_copies_apk_without_following_path_copy_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk payload")
            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=lambda command, **_kwargs: (
                    (0, "org.example.demo 1", "")
                    if tuple(command)[:3] == ("waydroid", "app", "list")
                    else (0, "Success", "")
                ),
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1",
                    "version_name": "1.0", "abis": ["x86_64"],
                },
            )
            calls = []

            def forbidden_copy(*_args, **_kwargs):
                calls.append(True)
                raise OSError("path-based copy is unsafe")

            with mock.patch.object(MODULE.shutil, "copyfile", side_effect=forbidden_copy):
                result = runtime.install_apk(apk)
            self.assertTrue(result["ok"])
            self.assertFalse(calls)

    def test_sha256_rejects_symlink_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.apk"
            source.write_bytes(b"apk")
            link = root / "link.apk"
            try:
                link.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(OSError):
                MODULE._sha256(link)

    def test_log_rejects_symlinked_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            outside = root / "outside"
            outside.mkdir()
            state_root = home / ".local/state/ming-os/android"
            state_root.parent.mkdir(parents=True)
            try:
                state_root.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(home=home)
            with self.assertRaises(MODULE.AndroidStorageError):
                runtime._log("org.example.demo", "launch", "failed", "test")

    def test_log_rejects_path_traversal_package(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = MODULE.AndroidRuntime(home=pathlib.Path(directory) / "home")
            with self.assertRaises(MODULE.AndroidStorageError):
                runtime._log("../escape", "launch", "failed", "test")

    def test_desktop_file_rejects_symlinked_android_target_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            outside = root / "outside"
            outside.mkdir()
            targets = home / ".local/share/ming-android/targets"
            targets.parent.mkdir(parents=True)
            try:
                targets.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(home=home)
            with self.assertRaises(MODULE.AndroidStorageError):
                runtime._desktop_file("org.example.demo")

    def test_lab_state_rejects_existing_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            root.chmod(0o700)
            outside = pathlib.Path(directory) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            target = root / "org.example.demo.json"
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(MODULE.AndroidStorageError):
                MODULE.AndroidLabStateStore(root).set("org.example.demo", "debug_logs", True)

    def test_stop_rejects_lock_pid_mismatch_before_terminating_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)

            class Process:
                pid = 54321

                def __init__(self):
                    self.terminated = False

                def poll(self):
                    return 0 if self.terminated else None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    self.terminated = True
                    return 0

            process = Process()
            runtime = MODULE.AndroidRuntime(
                home=root,
                spawner=lambda *_args, **_kwargs: process,
                sleeper=lambda _seconds: None,
            )
            self.assertTrue(runtime.launch("org.example.demo")["ok"])
            lock = root / ".local/state/ming-os/android/org.example.demo/cage.lock"
            lock.write_text(json.dumps({"package": "org.example.demo", "pid": 99999}), encoding="utf-8")
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(process.terminated)

    def test_stop_rejects_pid_without_cage_or_waydroid_command_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(json.dumps({"package": "org.example.demo", "pid": 4321}), encoding="utf-8")
            lock.chmod(0o600)
            calls = []
            killed = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                if tuple(command[:3]) == ("ps", "-p", "4321"):
                    return 0, "/usr/bin/unrelated --package org.example.demo", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    return 1, "", ""
                return 0, "", ""

            runtime = MODULE.AndroidRuntime(
                home=root, runner=runner, terminator=lambda pid: killed.append(pid) or True,
            )
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(killed)
            self.assertIn(("ps", "-p", "4321", "-o", "args="), calls)

    def test_stop_rejects_lock_without_exact_package_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(json.dumps({"pid": 4321}), encoding="utf-8")
            lock.chmod(0o600)
            killed = []

            def runner(command, **_kwargs):
                if tuple(command[:3]) == ("ps", "-p", "4321"):
                    return 0, "4321 cage waydroid app launch org.example.demo", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    return 1, "", ""
                return 0, "", ""

            runtime = MODULE.AndroidRuntime(
                home=root, runner=runner, terminator=lambda pid: killed.append(pid) or True,
            )
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(killed)

    def test_stop_rejects_in_memory_process_with_non_android_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)

            class Process:
                pid = 54321
                args = ("/usr/bin/sleep", "100")

                def __init__(self):
                    self.terminated = False

                def poll(self):
                    return 0 if self.terminated else None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    self.terminated = True
                    return 0

            process = Process()
            runtime = MODULE.AndroidRuntime(
                home=root, spawner=lambda *_args, **_kwargs: process,
                sleeper=lambda _seconds: None,
            )
            self.assertTrue(runtime.launch("org.example.demo")["ok"])
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertFalse(process.terminated)

    def test_stop_fails_closed_when_process_readback_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            lock = state_dir / "cage.lock"
            lock.write_text(
                json.dumps({"package": "org.example.demo", "pid": 4321}), encoding="utf-8"
            )
            lock.chmod(0o600)
            killed = []

            def runner(command, **_kwargs):
                if tuple(command[:3]) == ("ps", "-p", "4321"):
                    return 0, "4321 cage waydroid app launch org.example.demo", ""
                if tuple(command[:2]) == ("pgrep", "-af"):
                    raise OSError("pgrep unavailable")
                return 0, "", ""

            runtime = MODULE.AndroidRuntime(
                home=root, runner=runner, terminator=lambda pid: killed.append(pid) or True,
            )
            result = runtime.stop("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("stop_unconfirmed", result["state"])
            self.assertEqual([4321], killed)
            self.assertTrue(lock.exists())

    def test_launch_requires_explicit_session_readback_even_when_cage_process_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)

            class Process:
                pid = 1234

                def poll(self):
                    return None

                def terminate(self):
                    return None

                def wait(self, timeout=None):
                    return 0

            runtime = MODULE.AndroidRuntime(
                home=root, spawner=lambda *_args, **_kwargs: Process(),
                sleeper=lambda _seconds: None,
            )
            result = runtime.launch(
                "org.example.demo", session_probe=lambda: False,
            )
            self.assertFalse(result["ok"])
            self.assertEqual("launch_unconfirmed", result["state"])

    def test_launch_rejects_existing_symlinked_lock_as_insecure_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            app_dir.mkdir(parents=True)
            app_dir.parent.chmod(0o700)
            app_dir.parent.parent.chmod(0o700)
            app_dir.chmod(0o700)
            (app_dir / "metadata.json").write_text(
                '{"package":"org.example.demo","state":"installed"}', encoding="utf-8"
            )
            (app_dir / "metadata.json").chmod(0o600)
            state_dir = root / ".local/state/ming-os/android/org.example.demo"
            state_dir.mkdir(parents=True)
            state_dir.parent.chmod(0o700)
            state_dir.parent.parent.chmod(0o700)
            state_dir.chmod(0o700)
            outside = root / "outside.lock"
            outside.write_text("lock", encoding="utf-8")
            try:
                (state_dir / "cage.lock").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            runtime = MODULE.AndroidRuntime(
                home=root,
                spawner=lambda *_args, **_kwargs: object(),
                sleeper=lambda _seconds: None,
            )
            result = runtime.launch("org.example.demo")
            self.assertFalse(result["ok"])
            self.assertEqual("storage_insecure", result["state"])
            self.assertIn("符号链接", result["error"])


if __name__ == "__main__":
    unittest.main()
