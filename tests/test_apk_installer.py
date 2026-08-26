import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_android_runtime", ROOT / "assets" / "ming-android-runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ApkInstallerContracts(unittest.TestCase):
    def test_aapt_native_code_output_is_parsed_without_tuple_unpacking_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            completed = mock.Mock(returncode=0, stdout="package: name='org.example.demo' versionCode='1' versionName='1.0'\nnative-code: 'x86_64' 'x86'\n", stderr="")
            with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/aapt"), mock.patch.object(MODULE.subprocess, "run", return_value=completed):
                metadata = MODULE._default_apk_metadata(apk)
            self.assertEqual("org.example.demo", metadata["package"])
            self.assertIn("x86_64", metadata["abis"])

    def test_rejects_urls_symlinks_traversal_and_non_apk_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            runtime = MODULE.AndroidRuntime(home=root, metadata_reader=lambda _path: {
                "package": "org.example.demo", "version_code": "1", "version_name": "1.0",
                "abis": ["x86_64"],
            })
            self.assertEqual("invalid_source", runtime.validate_apk("https://example.invalid/demo.apk")["state"])
            self.assertEqual("invalid_source", runtime.validate_apk("../demo.apk")["state"])
            self.assertEqual("invalid_source", runtime.validate_apk(root / "demo.txt")["state"])
            link = root / "link.apk"
            try:
                link.symlink_to(apk)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertEqual("invalid_source", runtime.validate_apk(link)["state"])

    def test_rejects_invalid_package_and_arm_only_stable_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            metadata = {"package": "not a package", "version_code": "1", "version_name": "1.0", "abis": ["arm64-v8a"]}
            runtime = MODULE.AndroidRuntime(home=root, metadata_reader=lambda _path: metadata)
            invalid_package = runtime.validate_apk(apk)
            self.assertEqual("invalid_package", invalid_package["state"])
            metadata["package"] = "org.example.demo"
            arm_only = runtime.validate_apk(apk)
            self.assertEqual("unsupported_architecture", arm_only["state"])
            self.assertIn("实验室", arm_only["error"])

    def test_accepts_x86_64_and_universal_and_records_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            payload = b"apk payload"
            apk.write_bytes(payload)
            runtime = MODULE.AndroidRuntime(
                home=root,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "7", "version_name": "1.2",
                    "abis": ["x86_64", "x86"],
                },
            )
            result = runtime.validate_apk(apk)
            self.assertTrue(result["ok"])
            self.assertEqual("x86_64", result["architecture"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])
            runtime.metadata_reader = lambda _path: {
                "package": "org.example.universal", "version_code": "1", "version_name": "1.0",
                "abis": ["universal"],
            }
            self.assertEqual("universal", runtime.validate_apk(apk)["architecture"])

    def test_install_requires_waydroid_readback_and_creates_private_metadata_log_and_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            calls = []

            def runner(command, **_kwargs):
                calls.append(tuple(command))
                if tuple(command)[:3] == ("waydroid", "app", "list"):
                    return 0, "org.example.demo 7", ""
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=runner,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "7", "version_name": "1.2",
                    "abis": ["x86_64"],
                },
            )
            result = runtime.install_apk(apk)
            self.assertTrue(result["ok"])
            self.assertEqual("installed", result["state"])
            app_dir = root / ".local/share/ming-android/apps/org.example.demo"
            metadata = json.loads((app_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual("installed", metadata["state"])
            self.assertEqual("org.example.demo", metadata["package"])
            self.assertTrue((app_dir / "artifact.apk").is_file())
            self.assertTrue((root / ".local/state/ming-os/android/org.example.demo/install.jsonl").is_file())
            desktop = pathlib.Path(result["desktop_file"])
            self.assertIn("ming-launch", desktop.read_text(encoding="utf-8"))
            self.assertTrue(any(call[:3] == ("waydroid", "app", "install") for call in calls))
            self.assertTrue(any(call[:3] == ("waydroid", "app", "list") for call in calls))

    def test_install_does_not_mark_success_when_waydroid_readback_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            runtime = MODULE.AndroidRuntime(
                home=root,
                runner=lambda command, **_kwargs: (0, "", "") if tuple(command)[:3] == ("waydroid", "app", "install") else (0, "", ""),
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1", "version_name": "1.0",
                    "abis": ["x86_64"],
                },
            )
            result = runtime.install_apk(apk)
            self.assertFalse(result["ok"])
            self.assertEqual("install_unconfirmed", result["state"])

    def test_uninstall_removes_only_after_package_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            apk = root / "demo.apk"
            apk.write_bytes(b"apk")
            listed = {"org.example.demo"}

            def runner(command, **_kwargs):
                if tuple(command)[:3] == ("waydroid", "app", "list"):
                    return 0, "\n".join(sorted(listed)), ""
                if tuple(command)[:3] == ("waydroid", "app", "remove"):
                    listed.clear()
                return 0, "Success", ""

            runtime = MODULE.AndroidRuntime(
                home=root, runner=runner,
                metadata_reader=lambda _path: {
                    "package": "org.example.demo", "version_code": "1", "version_name": "1.0",
                    "abis": ["universal"],
                },
            )
            self.assertTrue(runtime.install_apk(apk)["ok"])
            result = runtime.uninstall("org.example.demo")
            self.assertTrue(result["ok"])
            self.assertFalse((root / ".local/share/ming-android/apps/org.example.demo").exists())


if __name__ == "__main__":
    unittest.main()
