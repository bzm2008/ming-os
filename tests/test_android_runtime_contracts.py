import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_android_runtime_contracts", ROOT / "assets" / "ming-android-runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AndroidRuntimeContracts(unittest.TestCase):
    def test_lab_state_store_serializes_config_updates_with_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = MODULE.AndroidLabStateStore(root, lock_timeout=0.01)
            store.set("com.example.demo", "debug_logs", False)
            held = store._thread_lock("com.example.demo")
            held.acquire()
            try:
                with self.assertRaises(MODULE.AndroidStorageError):
                    store.set("com.example.demo", "debug_logs", True)
            finally:
                held.release()

    def test_lab_state_store_rejects_symlinked_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside"
            outside.mkdir()
            state = root / "state"
            try:
                state.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            store = MODULE.AndroidLabStateStore(state)
            with self.assertRaises(MODULE.AndroidStorageError):
                store.set("com.example.demo", "debug_logs", True)

    def test_fixed_authorized_actions_and_no_shell_or_network_adb(self):
        self.assertEqual(
            {"install-deps", "start-container", "stop-container", "repair"},
            MODULE.AUTHORIZED_ACTIONS,
        )
        self.assertEqual(
            ("/usr/local/bin/ming-authorized-action", "android", "repair"),
            MODULE.authorized_command("repair"),
        )
        with self.assertRaises(ValueError):
            MODULE.authorized_command("sh -c evil")
        source = (ROOT / "assets" / "ming-android-runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("sh -c", source)
        self.assertNotIn("adb connect", source)

    def test_runtime_environment_reports_memory_gpu_binder_lxc_dbus_and_wayland_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime = MODULE.AndroidRuntime(
                home=root,
                environment={
                    "total_memory": 3 * 1024**3,
                    "gpu": False,
                    "binderfs": False,
                    "lxc": False,
                    "dbus": False,
                    "wayland": False,
                },
            )
            result = runtime.check_environment()
            self.assertFalse(result["stable"])
            self.assertIn("memory_below_4gb", result["reasons"])
            self.assertIn("gpu_unavailable", result["reasons"])
            self.assertIn("binderfs_missing", result["reasons"])
            self.assertIn("lxc_missing", result["reasons"])
            self.assertIn("dbus_missing", result["reasons"])
            self.assertIn("wayland_missing", result["reasons"])

    def test_runtime_manifest_requires_trusted_source_and_sha256(self):
        trusted = {"https://updates.example.invalid/waydroid.img": "a" * 64}
        valid = {
            "schema": "ming.android.runtime.v1",
            "url": "https://updates.example.invalid/waydroid.img",
            "sha256": "a" * 64,
        }
        self.assertTrue(MODULE.validate_runtime_manifest(valid, trusted)[0])
        invalid = dict(valid, url="https://evil.example.invalid/waydroid.img")
        self.assertFalse(MODULE.validate_runtime_manifest(invalid, trusted)[0])
        invalid = dict(valid, sha256="not-a-hash")
        self.assertFalse(MODULE.validate_runtime_manifest(invalid, trusted)[0])

    def test_cage_launch_uses_structured_argv_and_stable_process_result(self):
        calls = []

        class Process:
            def __init__(self):
                self.pid = 42
                self.polls = iter([None, None, None])

            def poll(self):
                return next(self.polls, None)

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
            runtime = MODULE.AndroidRuntime(
                home=root,
                spawner=lambda command, **_kwargs: calls.append(tuple(command)) or Process(),
                sleeper=lambda _seconds: None,
            )
            result = runtime.launch("org.example.demo")
            self.assertTrue(result["ok"])
            self.assertEqual("running", result["state"])
            self.assertEqual(
                ("cage", "--", "waydroid", "app", "launch", "org.example.demo"),
                calls[0],
            )
            self.assertFalse(any("--" in arg and "sh" in arg for arg in calls[0]))

    def test_lab_arm_is_explicit_and_does_not_change_stable_state(self):
        self.assertFalse(MODULE.default_android_lab_state()["arm_translation"])
        self.assertFalse(MODULE.default_android_lab_state()["gpu_software_render"])
        with tempfile.TemporaryDirectory() as directory:
            store = MODULE.AndroidLabStateStore(pathlib.Path(directory))
            state = store.set("org.example.demo", "arm_translation", True)
            self.assertTrue(state["arm_translation"])
            self.assertFalse(store.get("org.other.demo")["arm_translation"])


if __name__ == "__main__":
    unittest.main()
