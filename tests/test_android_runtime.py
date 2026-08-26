import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_android_runtime", ROOT / "assets" / "ming-android-runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
if SPEC.loader is not None:
    SPEC.loader.exec_module(MODULE)


class AndroidRuntimeContracts(unittest.TestCase):
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
            for package in ("com.example.one", "com.example.two"):
                (apps / package).mkdir(parents=True)
                (apps / package / "metadata.json").write_text(json.dumps({"package_id": package}), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
