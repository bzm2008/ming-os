import importlib.util
import json
import multiprocessing
import os
import pathlib
import shlex
import stat
import tempfile
import threading
import types
import unittest
from contextlib import contextmanager
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


class MultiPackageRunner:
    def __init__(self, package_sources):
        self.package_sources = {
            str(package): str(source) for package, source in package_sources.items()}
        self.installed = set(self.package_sources)
        self.owner_overrides = {}
        self.commands = []

    def __call__(self, command, timeout=20):
        command = tuple(command)
        if not command or command[0] != "dpkg-query":
            command = ("dpkg-query",) + command
        self.commands.append(command)
        if len(command) == 3 and command[:2] == ("dpkg-query", "-L"):
            package = command[2]
            source = self.package_sources.get(package, "")
            return (0, source + "\n", "") if source else (1, "", "not installed")
        if len(command) == 4 and command[:3] == ("dpkg-query", "-S", "--"):
            source = command[3]
            owner = self.owner_overrides.get(source)
            if owner is None:
                owner = next((
                    package for package, candidate in self.package_sources.items()
                    if candidate == source), "")
            if owner:
                return 0, "%s: %s\n" % (owner, source), ""
            return 1, "", "not owned"
        if (
                len(command) == 5
                and command[:4] == (
                    "dpkg-query", "-W",
                    "-f=${db:Status-Abbrev}\\t${binary:Package}\\n", "--")):
            package = command[4]
            if package in self.installed:
                return 0, "ii \t%s\n" % package, ""
            return 1, "", "not installed"
        return 1, "", "unexpected command: %r" % (command,)


class MultiLauncherRunner(Runner):
    def __init__(self, package, sources):
        sources = tuple(str(source) for source in sources)
        super().__init__(package, sources[0])
        self.sources = sources

    def __call__(self, command, timeout=20):
        command = tuple(command)
        if not command or command[0] != "dpkg-query":
            command = ("dpkg-query",) + command
        if command == ("dpkg-query", "-L", self.package):
            return 0, "\n".join(self.sources) + "\n", ""
        if len(command) == 4 and command[:3] == ("dpkg-query", "-S", "--"):
            source = command[3]
            if source in self.sources:
                return 0, "%s: %s\n" % (self.package, source), ""
        return super().__call__(command, timeout=timeout)


def _lock_child(installer_path, lock_path, result_queue):
    module = load_module(installer_path, "ming_package_installer_lock_child")
    try:
        with module._DesktopProxyTransactionLock(lock_path, 0.25):
            result_queue.put("acquired")
    except module.DesktopProxyLockTimeout:
        result_queue.put("timeout")
    except Exception as error:  # pragma: no cover - diagnostic for CI failures
        result_queue.put("error:%s" % error)


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
    def test_proxy_lock_timeout_preserves_manifest_and_visible_proxies(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_lock_timeout")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            proxy_dir = root / "proxies"
            manifest = root / "manifest.json"

            @contextmanager
            def available_lock(_path, _timeout):
                yield

            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = proxy_dir
            service.proxy_manifest = manifest
            service.proxy_lock_factory = available_lock
            first, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            proxy = pathlib.Path(first[0]["proxy_path"])
            manifest_before = manifest.read_bytes()
            proxy_before = proxy.read_bytes()

            @contextmanager
            def timed_out_lock(_path, _timeout):
                raise TimeoutError("desktop proxy lock busy")
                yield

            service.proxy_lock_factory = timed_out_lock
            source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual([], launchers)
            self.assertTrue(error)
            self.assertIn("lock", error.casefold())
            self.assertEqual(manifest_before, manifest.read_bytes())
            self.assertEqual(proxy_before, proxy.read_bytes())

    def test_proxy_lock_rejects_normalized_and_same_inode_package_lock_aliases(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_lock_alias")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            locks = root / "locks"
            locks.mkdir()
            package_lock = locks / "ming-package-manager.lock"
            package_lock.write_bytes(b"lock")
            service = installer.PackageInstaller(uid_getter=lambda: 0)
            with mock.patch.object(installer, "PACKAGE_MANAGER_LOCK", str(package_lock)):
                service.proxy_lock_path = locks / "nested" / ".." / package_lock.name
                with self.assertRaises(OSError):
                    service._effective_proxy_lock_path()

                hardlink = locks / "same-inode.lock"
                try:
                    hardlink.hardlink_to(package_lock)
                except (OSError, NotImplementedError):
                    self.skipTest("host cannot create hard links")
                service.proxy_lock_path = hardlink
                with self.assertRaises(OSError):
                    service._effective_proxy_lock_path()

    @unittest.skipIf(os.name == "nt", "POSIX flock required")
    def test_real_proxy_lock_excludes_a_second_process(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_lock_process")
        if installer.fcntl is None:
            self.skipTest("fcntl is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            lock_path = pathlib.Path(directory) / "desktop-proxies.lock"
            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            first = installer._DesktopProxyTransactionLock(lock_path, 1)
            with first:
                worker = context.Process(
                    target=_lock_child,
                    args=(str(INSTALLER_PATH), str(lock_path), result_queue),
                )
                worker.start()
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual("timeout", result_queue.get(timeout=2))

    def test_proxy_publication_uses_external_staging_after_manifest_commit(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_order")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            proxy_dir = root / "catalog" / "applications"
            manifest = root / "state" / "manifest.json"
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = proxy_dir
            service.proxy_manifest = manifest
            service.proxy_staging_dir = root / "staging"
            observed = []

            def publish(staged, destination):
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                observed.append((pathlib.Path(staged), pathlib.Path(destination), payload))
                self.assertNotEqual(proxy_dir, pathlib.Path(staged).parent)
                self.assertFalse(pathlib.Path(destination).exists())
                self.assertIn(str(destination), {
                    entry["proxy_path"] for entry in payload["entries"]})
                os.replace(staged, destination)
                return None

            service._publish_staged_proxy = publish
            launchers, error = service._package_launchers("com.example.app")

            self.assertEqual("", error)
            self.assertTrue(launchers[0]["ok"])
            self.assertEqual(1, len(observed))
            self.assertTrue(pathlib.Path(launchers[0]["proxy_path"]).is_file())
            self.assertEqual([], list(proxy_dir.glob(".*.desktop.*")))

    def test_proxy_publication_hides_the_previous_completion_receipt(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_receipt_order")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            first, first_error = service._package_launchers("com.example.app")
            self.assertEqual("", first_error)
            self.assertTrue(first[0]["ok"])
            receipt = service.proxy_manifest.with_name(
                service.proxy_manifest.name + ".receipt.json")
            self.assertTrue(receipt.exists())
            native_publish = service._publish_staged_proxy
            observed = []

            def publish(staged, destination):
                observed.append(receipt.exists() or receipt.is_symlink())
                return native_publish(staged, destination)

            service._publish_staged_proxy = publish
            second, second_error = service._package_launchers("com.example.app")

            self.assertEqual("", second_error)
            self.assertTrue(second[0]["ok"])
            self.assertEqual([False], observed)
            self.assertTrue(receipt.exists())

    def test_crash_before_manifest_commit_never_publishes_a_visible_proxy(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_precommit_crash")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            proxy_dir = root / "catalog" / "applications"
            manifest = root / "state" / "manifest.json"
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = proxy_dir
            service.proxy_manifest = manifest
            service.proxy_staging_dir = root / "staging"
            native_write = service._atomic_write

            def crash_on_manifest(path, content, mode=0o644):
                if pathlib.Path(path) == manifest:
                    raise SystemExit("simulated power loss before manifest commit")
                return native_write(path, content, mode)

            service._atomic_write = crash_on_manifest
            with self.assertRaises(SystemExit):
                service._package_launchers("com.example.app")

            self.assertFalse(manifest.exists())
            self.assertEqual([], list(proxy_dir.glob("ming-opt-*.desktop")))

    def test_empty_convergence_removes_orphan_without_existing_manifest(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_empty_orphan")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            proxy_dir = root / "proxies"
            proxy_dir.mkdir(parents=True)
            orphan = proxy_dir / ("ming-opt-%s.desktop" % ("d" * 64))
            orphan.write_text("[Desktop Entry]\nType=Application\nName=Orphan\n", encoding="utf-8")
            orphan.chmod(0o644)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", ""), uid_getter=lambda: 0)
            service.opt_apps_root = root / "opt" / "apps"
            service.proxy_dir = proxy_dir
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"

            records, error = service._sync_desktop_proxies("com.example.app", set())

            self.assertEqual([], records)
            self.assertEqual("", error)
            self.assertFalse(orphan.exists())

    def test_concurrent_package_sync_serializes_and_preserves_both_records(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_concurrency")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source_a, _executable_a = make_opt_app(root, package="package-a")
            _opt_root, source_b, _executable_b = make_opt_app(root, package="package-b")
            runner = MultiPackageRunner({"package-a": source_a, "package-b": source_b})
            proxy_dir = root / "proxies"
            manifest = root / "manifest.json"
            mutex = threading.Lock()
            rendezvous = threading.Barrier(2)
            lock_entries = []

            @contextmanager
            def serialized_lock(_path, _timeout):
                rendezvous.wait(timeout=5)
                with mutex:
                    lock_entries.append(threading.current_thread().name)
                    yield

            services = []
            for package in ("package-a", "package-b"):
                service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
                service.opt_apps_root = opt_root
                service.proxy_dir = proxy_dir
                service.proxy_manifest = manifest
                service.proxy_staging_dir = root / "staging"
                service.proxy_lock_factory = serialized_lock
                services.append((package, service))
            results = {}

            def synchronize(package, service):
                results[package] = service._package_launchers(package)

            workers = [
                threading.Thread(target=synchronize, args=item, name=item[0])
                for item in services
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)

            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(2, len(lock_entries))
            self.assertEqual({"package-a", "package-b"}, set(results))
            self.assertTrue(all(not error for _records, error in results.values()))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                {"package-a", "package-b"},
                {entry["package"] for entry in payload["entries"]},
            )

    def test_publish_failure_after_manifest_is_reported_and_next_sync_recovers(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_publish_failure")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"

            def fail_publish(_staged, _destination):
                raise OSError("simulated final publication failure")

            service._publish_staged_proxy = fail_publish
            launchers, error = service._package_launchers("com.example.app")

            self.assertTrue(error)
            self.assertFalse(launchers[0]["ok"])
            self.assertFalse(service.proxy_manifest.exists())
            self.assertEqual([], list(service.proxy_dir.glob("ming-opt-*.desktop")))

            del service.__dict__["_publish_staged_proxy"]
            recovered, recovery_error = service._package_launchers("com.example.app")
            self.assertEqual("", recovery_error)
            self.assertTrue(recovered[0]["ok"])
            self.assertTrue(pathlib.Path(recovered[0]["proxy_path"]).is_file())

    def test_crash_after_manifest_is_fail_closed_and_recoverable(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_postcommit_crash")
        launch = load_module(LAUNCH_PATH, "ming_launch_proxy_postcommit_crash")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"

            def crash_publish(_staged, _destination):
                raise SystemExit("simulated power loss after manifest commit")

            service._publish_staged_proxy = crash_publish
            with self.assertRaises(SystemExit):
                service._package_launchers("com.example.app")

            payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
            proxy = pathlib.Path(payload["entries"][0]["proxy_path"])
            self.assertFalse(proxy.exists())
            with self.assertRaises(ValueError):
                launch.verify_desktop_proxy(
                    proxy,
                    manifest_path=service.proxy_manifest,
                    opt_apps_root=opt_root,
                    proxy_dir=service.proxy_dir,
                    command_runner=Runner("com.example.app", source),
                )

            del service.__dict__["_publish_staged_proxy"]
            recovered, recovery_error = service._package_launchers("com.example.app")
            self.assertEqual("", recovery_error)
            self.assertTrue(recovered[0]["ok"])
            self.assertTrue(proxy.is_file())

    def test_partial_multi_launcher_publication_is_not_launchable(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_batch_crash")
        launch = load_module(LAUNCH_PATH, "ming_launch_proxy_batch_crash")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source_a, _executable = make_opt_app(root)
            source_b = source_a.with_name("second.desktop")
            source_b.write_text(
                source_a.read_text(encoding="utf-8").replace("Example App", "Second App"),
                encoding="utf-8",
            )
            source_b.chmod(0o644)
            runner = MultiLauncherRunner("com.example.app", (source_a, source_b))
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            native_publish = service._publish_staged_proxy
            calls = []

            def crash_on_second_publish(staged, destination):
                calls.append(pathlib.Path(destination))
                if len(calls) == 2:
                    raise SystemExit("simulated power loss during batch publication")
                return native_publish(staged, destination)

            service._publish_staged_proxy = crash_on_second_publish
            with self.assertRaises(SystemExit):
                service._package_launchers("com.example.app")

            self.assertEqual(2, len(calls))
            first_proxy = service._opt_proxy_path(source_a)
            second_proxy = service._opt_proxy_path(source_b)
            self.assertTrue(first_proxy.exists())
            self.assertFalse(second_proxy.exists())
            receipt = service.proxy_manifest.with_name(
                service.proxy_manifest.name + ".receipt.json")
            self.assertFalse(receipt.exists())
            for proxy in (first_proxy, second_proxy):
                with self.assertRaises(ValueError):
                    launch.verify_desktop_proxy(
                        proxy,
                        manifest_path=service.proxy_manifest,
                        opt_apps_root=opt_root,
                        proxy_dir=service.proxy_dir,
                        command_runner=runner,
                    )

    def test_failed_rollback_after_publication_leaves_disk_state_unlaunchable(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_failed_rollback")
        launch = load_module(LAUNCH_PATH, "ming_launch_proxy_failed_rollback")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            initial, initial_error = service._package_launchers("com.example.app")
            self.assertEqual("", initial_error)
            proxy = pathlib.Path(initial[0]["proxy_path"])
            source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            cleanup_calls = []

            def fail_cleanup(transaction_dir):
                cleanup_calls.append(transaction_dir)
                return ["simulated cleanup failure"]

            service._cleanup_proxy_transaction_dir = fail_cleanup
            service._rollback_proxy_transaction = lambda *_args, **_kwargs: [
                "simulated rollback failure"]
            launchers, error = service._package_launchers("com.example.app")

            self.assertTrue(error)
            self.assertFalse(launchers[0]["ok"])
            self.assertGreaterEqual(len(cleanup_calls), 2)
            receipt = service.proxy_manifest.with_name(
                service.proxy_manifest.name + ".receipt.json")
            self.assertFalse(receipt.exists())
            with self.assertRaises(ValueError):
                launch.verify_desktop_proxy(
                    proxy,
                    manifest_path=service.proxy_manifest,
                    opt_apps_root=opt_root,
                    proxy_dir=service.proxy_dir,
                    command_runner=runner,
                )

    def test_rollback_failure_is_reported_and_logged(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_rollback_error")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            logged = []
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source),
                uid_getter=lambda: 0,
                logger=logged.append,
            )
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            service._publish_staged_proxy = lambda *_args: (_ for _ in ()).throw(
                OSError("publish failed"))
            service._rollback_proxy_transaction = lambda *_args, **_kwargs: [
                "manifest rollback verification failed"]

            launchers, error = service._package_launchers("com.example.app")

            self.assertTrue(error)
            self.assertIn("rollback", error.casefold())
            self.assertFalse(launchers[0]["ok"])
            self.assertTrue(any("rollback" in line.casefold() for line in logged))

    def test_visible_cleanup_failure_is_reported_and_marks_records_not_ready(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_cleanup_error")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            proxy_dir = root / "proxies"
            proxy_dir.mkdir(parents=True)
            orphan = proxy_dir / ("ming-opt-%s.desktop" % ("e" * 64))
            orphan.write_text("[Desktop Entry]\nType=Application\nName=Orphan\n", encoding="utf-8")
            orphan.chmod(0o644)
            service = installer.PackageInstaller(
                runner=Runner("com.example.app", source), uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = proxy_dir
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            service._quarantine_proxy = lambda *_args: (_ for _ in ()).throw(
                OSError("visible cleanup failed"))

            launchers, error = service._package_launchers("com.example.app")

            self.assertTrue(error)
            self.assertIn("cleanup", error.casefold())
            self.assertFalse(launchers[0]["ok"])
            self.assertTrue(orphan.exists())

    def test_orphan_symlink_without_manifest_is_unlinked_without_following(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_orphan_symlink")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            proxy_dir = root / "proxies"
            proxy_dir.mkdir(parents=True)
            target = root / "target.desktop"
            target.write_text("[Desktop Entry]\nType=Application\nName=Target\n", encoding="utf-8")
            orphan = proxy_dir / ("ming-opt-%s.desktop" % ("f" * 64))
            try:
                orphan.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create symlinks")
            service = installer.PackageInstaller(uid_getter=lambda: 0)
            service.opt_apps_root = root / "opt" / "apps"
            service.proxy_dir = proxy_dir
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"

            records, error = service._sync_desktop_proxies("com.example.app", set())

            self.assertEqual([], records)
            self.assertFalse(orphan.exists() or orphan.is_symlink())
            self.assertTrue(target.exists())
            self.assertEqual("", error)

    def test_orphan_symlink_unlink_failure_is_reported(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_orphan_link_error")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            proxy_dir = root / "proxies"
            proxy_dir.mkdir(parents=True)
            target = root / "target.desktop"
            target.write_text("[Desktop Entry]\nType=Application\nName=Target\n", encoding="utf-8")
            orphan = proxy_dir / ("ming-opt-%s.desktop" % ("a" * 64))
            try:
                orphan.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create symlinks")
            service = installer.PackageInstaller(uid_getter=lambda: 0)
            service.opt_apps_root = root / "opt" / "apps"
            service.proxy_dir = proxy_dir
            service.proxy_manifest = root / "manifest.json"
            service.proxy_staging_dir = root / "staging"
            native_unlink = os.unlink

            def fail_orphan_unlink(path, *args, **kwargs):
                if pathlib.Path(path) == orphan:
                    raise OSError("simulated orphan unlink failure")
                return native_unlink(path, *args, **kwargs)

            with mock.patch.object(os, "unlink", side_effect=fail_orphan_unlink):
                records, error = service._sync_desktop_proxies("com.example.app", set())

            self.assertEqual([], records)
            self.assertTrue(error)
            self.assertIn("symlink", error.casefold())
            self.assertTrue(orphan.is_symlink())
            self.assertTrue(target.exists())

    def test_installer_and_launcher_reject_a_symlinked_opt_ancestor(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_ancestor_link")
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_ancestor_link")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            installed, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            proxy = pathlib.Path(installed[0]["proxy_path"])

            original_opt = opt_root.parent
            real_opt = root / "real-opt"
            os.replace(original_opt, real_opt)
            try:
                original_opt.symlink_to(real_opt, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create directory symlinks")

            self.assertFalse(service._safe_opt_apps_source(source))
            with self.assertRaises(ValueError):
                launch.verify_desktop_proxy(
                    proxy,
                    manifest_path=service.proxy_manifest,
                    opt_apps_root=opt_root,
                    proxy_dir=service.proxy_dir,
                    command_runner=runner,
                )

    @unittest.skipIf(os.name == "nt", "POSIX mode bits required")
    def test_installer_and_launcher_reject_a_writable_opt_ancestor(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_opt_ancestor_mode")
        launch = load_module(LAUNCH_PATH, "ming_launch_opt_ancestor_mode")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            opt_root, source, _executable = make_opt_app(root)
            runner = Runner("com.example.app", source)
            service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
            service.opt_apps_root = opt_root
            service.proxy_dir = root / "proxies"
            service.proxy_manifest = root / "manifest.json"
            installed, error = service._package_launchers("com.example.app")
            self.assertEqual("", error)
            proxy = pathlib.Path(installed[0]["proxy_path"])
            opt_root.parent.chmod(0o777)

            self.assertFalse(service._safe_opt_apps_source(source))
            with self.assertRaises(ValueError):
                launch.verify_desktop_proxy(
                    proxy,
                    manifest_path=service.proxy_manifest,
                    opt_apps_root=opt_root,
                    proxy_dir=service.proxy_dir,
                    command_runner=runner,
                )

    def test_cross_package_invalid_manifest_records_are_dropped(self):
        installer = load_module(INSTALLER_PATH, "ming_package_installer_proxy_cross_package")
        cases = (
            "uninstalled", "wrong-owner", "source-tampered",
            "proxy-tampered", "source-missing", "proxy-missing",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                opt_root, source_a, _executable_a = make_opt_app(root, package="package-a")
                _opt_root, source_b, _executable_b = make_opt_app(root, package="package-b")
                runner = MultiPackageRunner({"package-a": source_a, "package-b": source_b})
                service = installer.PackageInstaller(runner=runner, uid_getter=lambda: 0)
                service.opt_apps_root = opt_root
                service.proxy_dir = root / "proxies"
                service.proxy_manifest = root / "manifest.json"
                service.proxy_staging_dir = root / "staging"
                first, first_error = service._package_launchers("package-a")
                self.assertEqual("", first_error)
                proxy_a = pathlib.Path(first[0]["proxy_path"])

                if case == "uninstalled":
                    runner.installed.remove("package-a")
                elif case == "wrong-owner":
                    runner.owner_overrides[str(source_a)] = "different-package"
                elif case == "source-tampered":
                    source_a.write_text(
                        source_a.read_text(encoding="utf-8") + "# changed\n",
                        encoding="utf-8",
                    )
                elif case == "proxy-tampered":
                    proxy_a.write_text(
                        proxy_a.read_text(encoding="utf-8") + "# changed\n",
                        encoding="utf-8",
                    )
                elif case == "source-missing":
                    source_a.unlink()
                elif case == "proxy-missing":
                    proxy_a.unlink()

                second, second_error = service._package_launchers("package-b")

                self.assertEqual("", second_error)
                self.assertTrue(second[0]["ok"])
                payload = json.loads(service.proxy_manifest.read_text(encoding="utf-8"))
                self.assertEqual(["package-b"], [
                    entry["package"] for entry in payload["entries"]])
                self.assertFalse(proxy_a.exists())

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

            self.assertTrue(error)
            self.assertIn("manifest", error.casefold())
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
