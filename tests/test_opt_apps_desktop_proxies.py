import importlib.util
import json
import os
import pathlib
import shlex
import stat
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "assets" / "ming-package-installer.py"
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Runner:
    def __init__(self, package, source):
        self.package = package
        self.source = str(source)
        self.commands = []

    def __call__(self, command, timeout=20):
        command = tuple(command)
        if not command or command[0] != "dpkg-query":
            command = ("dpkg-query",) + command
        self.commands.append(command)
        if command == ("dpkg-query", "-L", self.package):
            return 0, self.source + "\n", ""
        if command == ("dpkg-query", "-S", "--", self.source):
            return 0, "%s: %s\n" % (self.package, self.source), ""
        if command == (
                "dpkg-query", "-W", "-f=${db:Status-Abbrev}\\t${binary:Package}\\n",
                "--", self.package):
            return 0, "ii \t%s\n" % self.package, ""
        return 1, "", "unexpected command: %r" % (command,)


class InstallRunner(Runner):
    def __init__(self, package, source, deb):
        super().__init__(package, source)
        self.deb = str(pathlib.Path(deb).resolve())

    def __call__(self, command, timeout=20):
        command = tuple(command)
        if command == ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", self.package):
            return 0, "ii ", ""
        if command and command[0] != "dpkg-query":
            if command == (
                    "dpkg-deb", "--field", self.deb,
                    "Package", "Version", "Architecture"):
                return 0, "Package: %s\nVersion: 1.0\nArchitecture: amd64\n" % self.package, ""
            if command == (
                    "flock", "--exclusive", "--timeout", "30",
                    "--conflict-exit-code", "75", "/run/lock/ming-package-manager.lock",
                    "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
                    "-o", "DPkg::Lock::Timeout=60", "install", self.deb):
                return 0, "", ""
            if command in {
                    ("update-desktop-database", "/usr/share/applications"),
                    ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")}:
                return 0, "", ""
        return super().__call__(command, timeout=timeout)


def make_opt_app(root, package="com.example.app", name="example.desktop"):
    opt_root = pathlib.Path(root) / "opt" / "apps"
    source_dir = opt_root / package / "entries" / "applications"
    source_dir.mkdir(parents=True)
    executable = opt_root / package / "files" / "bin" / "example-app"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    source = source_dir / name
    source.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Example App\n"
        "Icon=example\n"
        "Categories=Utility;\n"
        "Exec=%s --open %%U\n" % shlex.quote(str(executable)),
        encoding="utf-8",
    )
    source.chmod(0o644)
    for path in (opt_root, opt_root / package, opt_root / package / "entries",
                 source_dir, executable.parent):
        path.chmod(0o755)
    return opt_root, source, executable


class OptAppsProxyInstallerTests(unittest.TestCase):
    def test_discovery_treats_the_opt_app_id_as_a_bounded_path_component(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_app_id")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root, package="Vendor_App")
            service = installer.PackageInstaller(runner=Runner("vendor-package", source))
            service.opt_apps_root = opt_root

            self.assertEqual(source, service._opt_apps_desktop_path(source))
            self.assertIsNone(service._opt_apps_desktop_path(
                source.with_name("bad\nname.desktop")))

    def test_installer_discovers_only_owned_direct_opt_apps_entries_and_writes_manifest_proxy(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_proxy")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            proxy_dir = root / "usr" / "local" / "share" / "applications"
            manifest = root / "var" / "lib" / "ming-os" / "desktop-proxies" / "manifest-v1.json"
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = proxy_dir
            service.proxy_manifest = manifest

            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertEqual(1, len(launchers))
            record = launchers[0]
            self.assertTrue(record["ok"])
            self.assertEqual(str(source), record["source_path"])
            proxy = pathlib.Path(record["proxy_path"])
            self.assertEqual(proxy, pathlib.Path(record["path"]))
            self.assertRegex(proxy.name, r"^ming-opt-[0-9a-f]{64}\.desktop$")
            self.assertTrue(proxy.is_file())
            self.assertEqual(0, proxy.stat().st_uid)
            if os.name != "nt":
                self.assertEqual(0, proxy.stat().st_mode & 0o022)
            self.assertIn("Exec=/usr/local/bin/ming-launch", proxy.read_text(encoding="utf-8"))

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["schema_version"])
            self.assertEqual(1, len(payload["entries"]))
            self.assertEqual(str(source), payload["entries"][0]["source_path"])
            self.assertEqual(str(proxy), payload["entries"][0]["proxy_path"])
            self.assertEqual(64, len(payload["entries"][0]["source_sha256"]))
            self.assertEqual(64, len(payload["entries"][0]["proxy_sha256"]))

    def test_installer_rejects_invalid_opt_apps_exec_without_marking_launch_ready(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_invalid")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken\nExec=/no/such/program\n",
                encoding="utf-8",
            )
            source.chmod(0o644)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"

            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertEqual(1, len(launchers))
            self.assertFalse(launchers[0]["ok"])
            self.assertEqual("", launchers[0].get("proxy_path", ""))
            self.assertIn("启动", launchers[0]["error"])

    def test_installer_ignores_nested_entries_but_reports_a_direct_symlink_source(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_paths")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            nested = opt_root / "com.example.app" / "entries" / "applications" / "nested" / "bad.desktop"
            nested.parent.mkdir()
            nested.write_text("[Desktop Entry]\nType=Application\nName=Bad\nExec=python\n", encoding="utf-8")
            nested.chmod(0o644)
            direct_link = opt_root / "com.example.app" / "entries" / "applications" / "link.desktop"
            target = opt_root / "com.example.app" / "files" / "bin" / "linked.desktop"
            target.write_text("[Desktop Entry]\nType=Application\nName=Link\nExec=python\n", encoding="utf-8")
            target.chmod(0o644)
            try:
                direct_link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create symlinks")

            class MultiRunner(Runner):
                def __call__(self, command, timeout=20):
                    command = tuple(command)
                    if command and command[0] != "dpkg-query":
                        command = ("dpkg-query",) + command
                    if command == ("dpkg-query", "-L", self.package):
                        return 0, "\n".join((self.source, str(nested), str(direct_link))) + "\n", ""
                    return super().__call__(command, timeout=timeout)

            runner = MultiRunner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertEqual(2, len(launchers))
            self.assertEqual(str(source), launchers[0]["source_path"])
            self.assertFalse(launchers[1]["ok"])
            self.assertIn("不安全", launchers[1]["error"])

    def test_installer_reports_package_owner_mismatch_as_not_launch_ready(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_owner")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)

            class WrongOwner(Runner):
                def __call__(self, command, timeout=20):
                    command = tuple(command)
                    if command and command[0] != "dpkg-query":
                        command = ("dpkg-query",) + command
                    if command == ("dpkg-query", "-S", "--", self.source):
                        return 0, "different.package: %s\n" % self.source, ""
                    return super().__call__(command, timeout=timeout)

            service = installer.PackageInstaller(
                runner=WrongOwner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertEqual(1, len(launchers))
            self.assertFalse(launchers[0]["ok"])
            self.assertIn("所有权", launchers[0]["error"])

    def test_installer_does_not_proxy_a_hidden_opt_apps_entry(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_hidden")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            source.write_text(source.read_text(encoding="utf-8") + "Hidden=true\n", encoding="utf-8")
            source.chmod(0o644)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"

            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertEqual(1, len(launchers))
            self.assertFalse(launchers[0]["visible"])
            self.assertFalse(launchers[0]["ok"])

    def test_install_result_exposes_proxy_and_source_paths(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_result")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            deb = root / "com.example.app.deb"
            deb.write_bytes(b"deb")
            service = installer.PackageInstaller(
                runner=InstallRunner("com.example.app", source, deb),
                uid_getter=lambda: 0,
            )
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"

            result = service.install(deb)

            self.assertTrue(result["ok"])
            self.assertTrue(result["launch_ready"])
            self.assertEqual([result["launchers"][0]["proxy_path"]], result.get("proxy_paths"))
            self.assertEqual([str(source)], result.get("source_paths"))
            self.assertEqual([{
                "proxy_path": result["launchers"][0]["proxy_path"],
                "source_path": str(source),
            }], result.get("desktop_proxies"))

    def test_install_result_reports_launch_not_ready_for_invalid_proxy_source(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_not_ready")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken\nExec=/no/such/program\n",
                encoding="utf-8",
            )
            source.chmod(0o644)
            deb = root / "com.example.app.deb"
            deb.write_bytes(b"deb")
            service = installer.PackageInstaller(
                runner=InstallRunner("com.example.app", source, deb),
                uid_getter=lambda: 0,
            )
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"

            result = service.install(deb)

            self.assertTrue(result["ok"])
            self.assertTrue(result["installed"])
            self.assertFalse(result["launch_ready"])
            self.assertEqual(installer.E_LAUNCH_NOT_READY, result["error_code"])

    def test_manifest_rejects_proxy_paths_that_escape_the_managed_directory(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_manifest_path")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            launchers, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            payload["entries"][0]["proxy_path"] = str(service.proxy_dir / ".." / "outside.desktop")
            service.proxy_manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                service._read_proxy_manifest()

    def test_manifest_requires_the_current_generation_contract(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_generation")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service._package_launchers("com.example.app")
            payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            payload.pop("generation")
            service.proxy_manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                service._read_proxy_manifest()

    def test_invalid_package_update_retires_the_old_proxy_and_manifest_record(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_retire")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            first, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            old_proxy = pathlib.Path(first[0]["proxy_path"])
            self.assertTrue(old_proxy.exists())

            source.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken\nExec=/no/such/program\n",
                encoding="utf-8",
            )
            source.chmod(0o644)
            second, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertFalse(second[0]["ok"])
            self.assertFalse(old_proxy.exists())
            payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual([], payload["entries"])

    def test_manifest_write_failure_rolls_back_new_proxy_files(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_rollback")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            original = service._atomic_write

            def fail_manifest(path, content, mode=0o644):
                if pathlib.Path(path) == service.proxy_manifest:
                    raise OSError("manifest write failed")
                return original(path, content, mode)

            service._atomic_write = fail_manifest
            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertFalse(launchers[0]["ok"])
            self.assertFalse(service.proxy_manifest.exists())
            self.assertEqual([], list(service.proxy_dir.glob("ming-opt-*.desktop")))

    def test_unreferenced_reserved_proxy_files_are_removed_on_convergence(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_orphan")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_dir.mkdir(parents=True)
            orphan = service.proxy_dir / "ming-opt-dead.desktop"
            orphan.write_text("[Desktop Entry]\nType=Application\nName=Orphan\n", encoding="utf-8")
            orphan.chmod(0o644)

            service._package_launchers("com.example.app")

            self.assertFalse(orphan.exists())

    def test_stale_proxy_unlink_failure_cannot_leave_a_visible_launcher(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_stale_unlink")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            first, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            old_proxy = pathlib.Path(first[0]["proxy_path"])
            native_unlink = pathlib.Path.unlink

            def fail_visible_unlink(path, *args, **kwargs):
                if pathlib.Path(path) == old_proxy:
                    raise OSError("stale unlink failed")
                return native_unlink(path, *args, **kwargs)

            runner.source = ""
            with mock.patch.object(pathlib.Path, "unlink", new=fail_visible_unlink):
                launchers, error = service._package_launchers("com.example.app")

            self.assertEqual([], launchers)
            self.assertEqual("", error)
            self.assertFalse(old_proxy.exists())
            payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            self.assertEqual([], payload["entries"])


class OptAppsProxyLaunchTests(unittest.TestCase):
    def _install_proxy(self, root):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_launch")
        opt_root, source, executable = make_opt_app(root)
        proxy_dir = pathlib.Path(root) / "usr" / "local" / "share" / "applications"
        manifest = pathlib.Path(root) / "var" / "lib" / "ming-os" / "desktop-proxies" / "manifest-v1.json"
        runner = Runner("com.example.app", source)
        service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
        service.opt_apps_root = opt_root
        service.proxy_dir = proxy_dir
        service.proxy_manifest = manifest
        launchers, error = service._package_launchers("com.example.app")
        self.assertEqual("", error)
        self.assertEqual(1, len(launchers))
        self.assertTrue(launchers[0]["ok"])
        return opt_root, source, executable, pathlib.Path(launchers[0]["proxy_path"]), manifest

    def test_launcher_accepts_manifest_proxy_without_allowlisting_opt_apps(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_proxy")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            request = launch.request_from_desktop_file(
                proxy,
                allowed_dirs=(proxy.parent,),
                proxy_manifest=manifest,
                opt_apps_root=opt_root,
                proxy_dir=proxy.parent,
                command_runner=Runner("com.example.app", source),
            )
            self.assertEqual("desktop_proxy", request.mode)
            self.assertEqual((str(_executable), "--open"), request.argv)
            self.assertNotIn(opt_root, launch.allowed_application_dirs())

    def test_launcher_rechecks_source_desktop_visibility(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_visibility")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            source.write_text(source.read_text(encoding="utf-8") + "OnlyShowIn=XFCE;\n", encoding="utf-8")
            source.chmod(0o644)
            installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_visibility")
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.current_desktops = {"xfce"}
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            launchers, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            self.assertTrue(launchers[0]["ok"])
            proxy = pathlib.Path(launchers[0]["proxy_path"])

            with mock.patch.dict(
                    os.environ,
                    {"XDG_CURRENT_DESKTOP": "GNOME", "DESKTOP_SESSION": "gnome"},
                    clear=False):
                with self.assertRaises(ValueError):
                    launch.request_from_desktop_file(
                        proxy,
                        allowed_dirs=(proxy.parent,),
                        proxy_manifest=service.proxy_manifest,
                        opt_apps_root=opt_root,
                        proxy_dir=proxy.parent,
                        command_runner=runner,
                    )

    def test_launcher_rejects_tampered_source_before_activation(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_tamper")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            runner = Runner("com.example.app", source)
            request = launch.request_from_desktop_file(
                proxy, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                opt_apps_root=opt_root, proxy_dir=proxy.parent, command_runner=runner,
            )
            source.write_text(source.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
            calls = []
            broker = launch.LaunchBroker(
                spawn=lambda argv: calls.append(argv) or object(),
                animate=lambda *_args: None,
                reduced_motion=lambda: True,
                probe=lambda *_args, **_kwargs: None,
                report_error=lambda *_args: None,
                proxy_manifest=manifest,
                opt_apps_root=opt_root,
                proxy_dir=proxy.parent,
                command_runner=runner,
            )
            self.assertFalse(broker.launch(request))
            self.assertEqual([], calls)

    def test_broker_preflight_returns_false_for_a_stale_proxy(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_preflight")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            request = launch.request_from_desktop_file(
                proxy, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                opt_apps_root=opt_root, proxy_dir=proxy.parent,
                command_runner=Runner("com.example.app", source),
            )
            source.write_text(source.read_text(encoding="utf-8") + "# stale\n", encoding="utf-8")
            broker = launch.LaunchBroker(
                proxy_manifest=manifest,
                opt_apps_root=opt_root,
                proxy_dir=proxy.parent,
                command_runner=Runner("com.example.app", source),
            )

            self.assertFalse(broker.preflight(request))

    def test_broker_revalidates_proxy_with_a_real_launch_origin_rectangle(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_rect")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            runner = Runner("com.example.app", source)
            request = launch.request_from_desktop_file(
                proxy,
                source="drawer",
                rect={"x": 10, "y": 20, "width": 32, "height": 32},
                allowed_dirs=(proxy.parent,),
                proxy_manifest=manifest,
                opt_apps_root=opt_root,
                proxy_dir=proxy.parent,
                command_runner=runner,
            )
            calls = []
            broker = launch.LaunchBroker(
                spawn=lambda argv: calls.append(tuple(argv)) or object(),
                animate=lambda *_args: None,
                reduced_motion=lambda: True,
                probe=lambda *_args, **_kwargs: None,
                report_error=lambda *_args: None,
                proxy_manifest=manifest,
                opt_apps_root=opt_root,
                proxy_dir=proxy.parent,
                command_runner=runner,
            )

            self.assertTrue(broker.launch(request))
            self.assertEqual([(str(_executable), "--open")], calls)

    def test_launcher_rejects_tampered_proxy_and_manifest_source_mismatch(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_manifest")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            proxy.write_text(proxy.read_text(encoding="utf-8").replace("Example App", "Changed"), encoding="utf-8")
            with self.assertRaises(ValueError):
                launch.request_from_desktop_file(
                    proxy, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                    opt_apps_root=opt_root, proxy_dir=proxy.parent,
                    command_runner=Runner("com.example.app", source),
                )

    def test_launcher_rejects_a_manifest_backed_nonconforming_proxy_name(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_proxy_name")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            renamed = proxy.with_name("ming-opt-example-%s.desktop" % ("a" * 16))
            renamed.write_text(
                proxy.read_text(encoding="utf-8").replace(str(proxy), str(renamed)),
                encoding="utf-8",
            )
            renamed.chmod(0o644)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["entries"][0]["proxy_path"] = str(renamed)
            payload["entries"][0]["proxy_sha256"] = launch._sha256_path(renamed)
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                launch.request_from_desktop_file(
                    renamed, allowed_dirs=(renamed.parent,), proxy_manifest=manifest,
                    opt_apps_root=opt_root, proxy_dir=renamed.parent,
                    command_runner=Runner("com.example.app", source),
                )

    def test_launcher_rejects_stale_manifest_hash_and_proxy_symlink(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_stale")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["entries"][0]["source_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                launch.request_from_desktop_file(
                    proxy, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                    opt_apps_root=opt_root, proxy_dir=proxy.parent,
                    command_runner=Runner("com.example.app", source),
                )

            payload["entries"][0]["source_sha256"] = launch._sha256_path(source)
            payload["entries"][0]["proxy_sha256"] = launch._sha256_path(proxy)
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            linked = proxy.with_name("ming-opt-link.desktop")
            try:
                linked.symlink_to(proxy)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create symlinks")
            with self.assertRaises(ValueError):
                launch.request_from_desktop_file(
                    linked, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                    opt_apps_root=opt_root, proxy_dir=proxy.parent,
                    command_runner=Runner("com.example.app", source),
                )

    def test_launcher_rejects_a_proxy_symlink_before_resolution(self):
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_link_resolution")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable, proxy, manifest = self._install_proxy(root)
            alias = proxy.with_name("ming-opt-alias.desktop")
            alias.write_text(proxy.read_text(encoding="utf-8"), encoding="utf-8")
            native_resolve = pathlib.Path.resolve
            native_is_symlink = pathlib.Path.is_symlink

            def resolve(path, strict=False):
                if pathlib.Path(path) == alias:
                    return proxy
                return native_resolve(path, strict=strict)

            def is_symlink(path):
                if pathlib.Path(path) == alias:
                    return True
                return native_is_symlink(path)

            with mock.patch.object(pathlib.Path, "resolve", new=resolve):
                with mock.patch.object(pathlib.Path, "is_symlink", new=is_symlink):
                    with self.assertRaises(ValueError):
                        launch.request_from_desktop_file(
                            alias, allowed_dirs=(proxy.parent,), proxy_manifest=manifest,
                            opt_apps_root=opt_root, proxy_dir=proxy.parent,
                            command_runner=Runner("com.example.app", source),
                        )



if __name__ == "__main__":
    unittest.main()
