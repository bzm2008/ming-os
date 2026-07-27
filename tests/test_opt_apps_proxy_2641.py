import importlib.util
import hashlib
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, filename):
    path = ROOT / "assets" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OptAppsProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load("ming_package_installer_opt_2641", "ming-package-installer.py")
        cls.launch = load("ming_launch_opt_2641", "ming-launch.py")

    def make_installer(self, root):
        return self.installer.PackageInstaller(
            uid_getter=lambda: 0,
            opt_apps_root=root / "opt" / "apps",
            proxy_dir=root / "usr" / "local" / "share" / "applications",
            proxy_manifest=root / "var" / "lib" / "ming-os" / "desktop-proxies" / "manifest-v1.json",
        )

    def make_source(self, root):
        source = root / "opt" / "apps" / "example" / "entries" / "applications" / "example.desktop"
        executable = root / "opt" / "apps" / "example" / "files" / "example"
        source.parent.mkdir(parents=True)
        executable.parent.mkdir(parents=True)
        executable.write_text("binary", encoding="utf-8")
        executable.chmod(0o755)
        source.write_text(
            "[Desktop Entry]\nType=Application\nName=Example\nExec=%s\nIcon=example\n" % executable.as_posix(),
            encoding="utf-8")
        source.chmod(0o644)
        return source

    def test_installer_publishes_proxy_manifest_and_completion_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = self.make_source(root)
            service = self.make_installer(root)

            records, error = service.sync_opt_app_proxies("example-package", [source])

            self.assertEqual("", error)
            self.assertTrue(records[0]["ok"])
            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            receipt = json.loads(service.proxy_receipt_path().read_text(encoding="utf-8"))
            self.assertEqual(1, len(manifest["entries"]))
            self.assertEqual(
                hashlib.sha256(service.proxy_manifest.read_bytes()).hexdigest(),
                receipt["manifest_sha256"])
            proxy = pathlib.Path(manifest["entries"][0]["proxy_path"])
            self.assertTrue(proxy.is_file())

    def test_launcher_rejects_proxy_after_source_hash_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = self.make_source(root)
            service = self.make_installer(root)
            records, error = service.sync_opt_app_proxies("example-package", [source])
            self.assertEqual("", error)
            proxy = pathlib.Path(records[0]["path"])
            source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

            self.assertFalse(self.launch.verify_desktop_proxy(
                proxy, manifest_path=service.proxy_manifest,
                receipt_path=service.proxy_receipt_path(),
                opt_apps_root=service.opt_apps_root, proxy_dir=service.proxy_dir))

    def test_package_launcher_discovery_publishes_opt_apps_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = self.make_source(root)
            service = self.make_installer(root)
            service.runner = lambda command, timeout=20: (
                (0, str(source) + "\n", "")
                if tuple(command[:2]) == ("dpkg-query", "-L") else (0, "", ""))

            records = service._package_launchers("example-package")

            self.assertEqual("desktop_proxy", records[0]["activation"])
            self.assertTrue(pathlib.Path(records[0]["path"]).is_file())

    def test_publishing_another_package_preserves_existing_proxy_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = self.make_source(root)
            service = self.make_installer(root)
            records, error = service.sync_opt_app_proxies("first-package", [first])
            self.assertEqual("", error)
            second = root / "opt" / "apps" / "second" / "entries" / "applications" / "second.desktop"
            executable = root / "opt" / "apps" / "second" / "files" / "second"
            second.parent.mkdir(parents=True)
            executable.parent.mkdir(parents=True)
            executable.write_text("binary", encoding="utf-8")
            second.write_text(
                "[Desktop Entry]\nType=Application\nName=Second\nExec=%s\n" % executable.as_posix(),
                encoding="utf-8")

            service.sync_opt_app_proxies("second-package", [second])

            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual({"first-package", "second-package"},
                             {entry["package"] for entry in manifest["entries"]})
            self.assertTrue(pathlib.Path(records[0]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
