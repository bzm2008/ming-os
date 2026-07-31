import importlib.util
import hashlib
import json
import pathlib
import tempfile
import threading
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

    def make_variant(self, root, app_name, **entry_values):
        source = root / "opt" / "apps" / app_name / "entries" / "applications" / (app_name + ".desktop")
        executable = root / "opt" / "apps" / app_name / "files" / app_name
        source.parent.mkdir(parents=True)
        executable.parent.mkdir(parents=True)
        executable.write_text("binary", encoding="utf-8")
        executable.chmod(0o755)
        values = {"Hidden": "false", "NoDisplay": "false", "Type": "Application"}
        values.update(entry_values)
        lines = ["[Desktop Entry]", "Name=%s" % app_name, "Exec=%s" % executable.as_posix()]
        lines.extend("%s=%s" % item for item in values.items())
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
            executable.chmod(0o755)
            second.write_text(
                "[Desktop Entry]\nType=Application\nName=Second\nExec=%s\n" % executable.as_posix(),
                encoding="utf-8")

            service.sync_opt_app_proxies("second-package", [second])

            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual({"first-package", "second-package"},
                             {entry["package"] for entry in manifest["entries"]})
            self.assertTrue(pathlib.Path(records[0]["path"]).is_file())

    def test_auxiliary_desktop_entries_are_not_published_or_counted_as_launchers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            visible = self.make_variant(root, "visible")
            hidden = self.make_variant(root, "hidden", Hidden="true")
            nodisplay = self.make_variant(root, "nodisplay", NoDisplay="true")
            link = self.make_variant(root, "link", Type="Link")
            service = self.make_installer(root)
            service.runner = lambda command, timeout=20: (
                (0, "\n".join(map(str, (visible, hidden, nodisplay, link))) + "\n", "")
                if tuple(command[:2]) == ("dpkg-query", "-L") else (0, "", "")
            )

            records = service._package_launchers("example-package")

            self.assertEqual(["visible"], [pathlib.Path(item["source_path"]).stem for item in records])
            self.assertTrue(records[0]["ok"])
            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual([str(visible)], [entry["source_path"] for entry in manifest["entries"]])

    def test_same_package_update_removes_only_stale_managed_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            old_source = self.make_variant(root, "old")
            other_source = self.make_variant(root, "other")
            service = self.make_installer(root)
            old_records, error = service.sync_opt_app_proxies("example-package", [old_source])
            self.assertEqual("", error)
            old_proxy = pathlib.Path(old_records[0]["path"])
            other_records, error = service.sync_opt_app_proxies("other-package", [other_source])
            self.assertEqual("", error)
            other_proxy = pathlib.Path(other_records[0]["path"])
            user_file = service.proxy_dir / "user.desktop"
            user_file.parent.mkdir(parents=True, exist_ok=True)
            user_file.write_text("user file", encoding="utf-8")
            external_file = root / "user-owned.desktop"
            external_file.write_text("external file", encoding="utf-8")

            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            manifest["entries"].append({
                "proxy_path": str(external_file),
                "source_path": str(old_source),
                "package": "example-package",
                "source_sha256": "old",
                "proxy_sha256": "old",
            })
            service.proxy_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            # Recreate a valid receipt for the intentionally injected stale entry.
            receipt = json.loads(service.proxy_receipt_path().read_text(encoding="utf-8"))
            receipt["manifest_sha256"] = hashlib.sha256(service.proxy_manifest.read_bytes()).hexdigest()
            service.proxy_receipt_path().write_text(json.dumps(receipt), encoding="utf-8")
            old_source.unlink()
            new_source = self.make_variant(root, "new")

            records, error = service.sync_opt_app_proxies("example-package", [new_source])

            self.assertEqual("", error)
            self.assertFalse(old_proxy.exists())
            self.assertTrue(other_proxy.exists())
            self.assertTrue(user_file.exists())
            self.assertTrue(external_file.exists())
            manifest = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual({"example-package", "other-package"},
                             {entry["package"] for entry in manifest["entries"]})
            self.assertNotIn(str(external_file), {entry["proxy_path"] for entry in manifest["entries"]})

    def test_concurrent_proxy_publication_keeps_entries_from_both_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = self.make_variant(root, "first")
            second = self.make_variant(root, "second")
            barrier = threading.Barrier(2)
            services = [self.make_installer(root), self.make_installer(root)]

            def publish(service, package, source):
                barrier.wait()
                return service.sync_opt_app_proxies(package, [source])

            thread_a = threading.Thread(target=publish, args=(services[0], "first-package", first))
            thread_b = threading.Thread(target=publish, args=(services[1], "second-package", second))
            thread_a.start()
            thread_b.start()
            thread_a.join()
            thread_b.join()

            manifest = json.loads(services[0].proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual({"first-package", "second-package"},
                             {entry["package"] for entry in manifest["entries"]})
            self.assertTrue(services[0].proxy_manifest.with_name(
                services[0].proxy_manifest.name + ".lock").exists())


if __name__ == "__main__":
    unittest.main()
