import hashlib
import importlib.util
import inspect
import json
import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "assets" / "ming-spark-package-helper.py"


def load_helper():
    name = "ming_spark_package_helper_under_test"
    spec = importlib.util.spec_from_file_location(name, HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def valid_session(uid=1000, **overrides):
    session = {
        "Active": "yes",
        "Remote": "no",
        "Class": "user",
        "Type": "wayland",
        "Seat": "seat0",
        "User": str(uid),
    }
    session.update(overrides)
    return session


class ProtocolRunner:
    def __init__(
            self, digest="", package="sample-app", version="1.2.3",
            architecture="amd64", installer_result=None, locked_result=None,
            dpkg_status="install ok installed"):
        self.digest = digest
        self.package = package
        self.version = version
        self.architecture = architecture
        self.installer_result = installer_result or {
            "ok": True,
            "installed": True,
            "launch_ready": True,
            "resolver": "spark",
            "error_code": "",
        }
        self.locked_result = locked_result or (0, "", "")
        self.dpkg_status = dpkg_status
        self.commands = []

    def __call__(self, command, timeout=20):
        command = tuple(str(value) for value in command)
        self.commands.append(command)
        if command[0] == "/usr/bin/dpkg-deb":
            return (
                0,
                "Package: %s\nVersion: %s\nArchitecture: %s\n" % (
                    self.package, self.version, self.architecture),
                "",
            )
        if command[0] == "/usr/bin/apt-cache":
            return (
                0,
                "Package: %s\nVersion: %s\nArchitecture: %s\nSHA512: %s\n\n" % (
                    self.package, self.version, self.architecture, self.digest),
                "",
            )
        if command[0] == "/usr/local/sbin/ming-package-installer":
            return 0, json.dumps(self.installer_result), ""
        if command[0] == "/usr/bin/flock":
            return self.locked_result
        if command[0] == "/usr/bin/dpkg-query":
            return 0, self.dpkg_status + "\n", ""
        raise AssertionError("unexpected command: %r" % (command,))


class SparkPackageHelperAssetTests(unittest.TestCase):
    def test_helper_asset_exists(self):
        self.assertTrue(HELPER.is_file(), "Spark package helper asset is missing")


class SparkPackageRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_accepts_only_the_exact_supported_argv_shapes(self):
        cases = (
            (
                [
                    "ssinstall",
                    "/tmp/spark-store/download/sample-app/sample-app_1.2.3_amd64.deb",
                    "--delete-after-install",
                    "--no-create-desktop-entry",
                    "--native",
                ],
                ("install_deb", "sample-app", True),
            ),
            (
                ["ssinstall", "sample-app", "--no-create-desktop-entry", "--native"],
                ("install_package", "sample-app", False),
            ),
            (["aptss", "ssupdate"], ("update", "", False)),
            (["aptss", "remove", "sample-app"], ("remove", "sample-app", False)),
            (["aptss", "remove", "-y", "sample-app"], ("remove", "sample-app", False)),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                request = self.helper.parse_request(argv)
                self.assertEqual(expected[0], request.operation)
                self.assertEqual(expected[1], request.package)
                self.assertEqual(expected[2], request.delete_source)

    def test_rejects_injection_extra_arguments_and_vendor_root_surfaces(self):
        invalid = (
            [],
            ["apm", "ssinstall", "sample-app"],
            ["aptss", "install", "sample-app"],
            ["aptss", "update"],
            ["aptss", "full-upgrade"],
            ["aptss", "download", "sample-app"],
            ["aptss", "remove", "sample-app", "other-app"],
            ["aptss", "remove", "-o", "Dir::Etc::sourcelist=/evil"],
            ["aptss", "remove", "--config-file=/evil", "sample-app"],
            ["ssinstall", "sample-app", "--native", "--no-create-desktop-entry"],
            ["ssinstall", "sample-app", "--no-create-desktop-entry", "--native", "extra"],
            ["ssinstall", "", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "sample app", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "sample:amd64", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "sample@app", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "sample=1", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "../sample", "--no-create-desktop-entry", "--native"],
            ["ssinstall", "Sample-App", "--no-create-desktop-entry", "--native"],
            ["aptss", "remove", "sample;id"],
            ["aptss", "remove", "x" * 129],
        )
        for argv in invalid:
            with self.subTest(argv=argv):
                with self.assertRaises(self.helper.HelperError) as raised:
                    self.helper.parse_request(argv)
                self.assertEqual("E_REQUEST_INVALID", raised.exception.code)


class SparkPackageAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def service(
            self, *, euid=0, pkexec_uid="1000", passwd_uid=1000,
            sessions=None, extra_env=None):
        if sessions is None:
            sessions = [valid_session()]

        def passwd_lookup(uid):
            if passwd_uid is None:
                raise KeyError(uid)
            return types.SimpleNamespace(pw_uid=passwd_uid, pw_name="user")

        environ = {"PKEXEC_UID": pkexec_uid} if pkexec_uid is not None else {}
        environ.update(extra_env or {})
        return self.helper.SparkPackageHelper(
            euid_getter=lambda: euid,
            environ=environ,
            passwd_lookup=passwd_lookup,
            session_reader=lambda _uid: sessions,
        )

    def test_requires_root_and_a_real_nonzero_decimal_pkexec_uid(self):
        cases = (
            ("non-root", {"euid": 1000}),
            ("missing", {"pkexec_uid": None}),
            ("root caller", {"pkexec_uid": "0"}),
            ("signed", {"pkexec_uid": "+1000"}),
            ("whitespace", {"pkexec_uid": " 1000"}),
            ("suffix", {"pkexec_uid": "1000x"}),
            ("missing passwd", {"passwd_uid": None}),
            ("mismatched passwd", {"passwd_uid": 1001}),
            ("ssh", {"extra_env": {"SSH_CONNECTION": "198.51.100.1 1 host 22"}}),
        )
        for label, options in cases:
            with self.subTest(label=label):
                with self.assertRaises(self.helper.HelperError) as raised:
                    self.service(**options).authorize()
                self.assertEqual("E_AUTHORIZATION_FAILED", raised.exception.code)

    def test_requires_a_local_active_graphical_seat_session_for_the_same_uid(self):
        rejected = (
            ("inactive", [valid_session(Active="no")]),
            ("remote", [valid_session(Remote="yes")]),
            ("linger", [valid_session(Class="manager", Type="unspecified", Seat="")]),
            ("tty", [valid_session(Type="tty")]),
            ("seatless", [valid_session(Seat="")]),
            ("different uid", [valid_session(User="1001")]),
            ("no session", []),
        )
        for label, sessions in rejected:
            with self.subTest(label=label):
                with self.assertRaises(self.helper.HelperError):
                    self.service(sessions=sessions).authorize()

        self.assertEqual(
            1000,
            self.service(sessions=[valid_session(Type="x11")]).authorize(),
        )


class SparkPackageFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_rejects_fifo_hardlink_wrong_owner_writable_or_unbounded_files(self):
        regular = stat.S_IFREG | 0o640
        valid = types.SimpleNamespace(
            st_mode=regular,
            st_uid=1000,
            st_nlink=1,
            st_size=4096,
        )
        self.helper.SparkPackageHelper.validate_file_metadata(valid, 1000)

        invalid = (
            ("fifo", {"st_mode": stat.S_IFIFO | 0o600}),
            ("hardlink", {"st_nlink": 2}),
            ("owner", {"st_uid": 1001}),
            ("group writable", {"st_mode": stat.S_IFREG | 0o620}),
            ("other writable", {"st_mode": stat.S_IFREG | 0o602}),
            ("empty", {"st_size": 0}),
            ("too large", {"st_size": self.helper.MAX_DEB_BYTES + 1}),
        )
        for label, overrides in invalid:
            values = vars(valid).copy()
            values.update(overrides)
            with self.subTest(label=label):
                with self.assertRaises(self.helper.HelperError) as raised:
                    self.helper.SparkPackageHelper.validate_file_metadata(
                        types.SimpleNamespace(**values), 1000)
                self.assertEqual("E_FILE_UNSAFE", raised.exception.code)

    def test_stages_from_a_nofollow_fd_and_rejects_bad_layout_or_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            download = base / "download"
            incoming = base / "incoming"
            package_dir = download / "sample-app"
            package_dir.mkdir(parents=True)
            source = package_dir / "sample-app_1.2.3_amd64.deb"
            payload = b"trusted package payload"
            source.write_bytes(payload)
            source.chmod(0o640)
            request_uid = source.stat().st_uid
            service = self.helper.SparkPackageHelper(
                download_root=download,
                staging_root=incoming,
            )

            staged = service.stage_deb(source, request_uid)

            self.assertEqual(payload, staged.path.read_bytes())
            self.assertEqual(hashlib.sha512(payload).hexdigest(), staged.sha512)
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(staged.path.stat().st_mode))
            self.assertNotEqual(source, staged.path)

            bad_paths = (
                base / "outside.deb",
                download / "sample-app" / "nested" / "bad.deb",
                download / "Sample-App" / "bad.deb",
                download / "sample-app" / "bad.txt",
            )
            for bad_path in bad_paths:
                with self.subTest(path=bad_path):
                    with self.assertRaises(self.helper.HelperError):
                        service.stage_deb(bad_path, request_uid)

            leaf_target = package_dir / "leaf-target.deb"
            leaf_target.write_bytes(b"leaf")
            leaf = package_dir / "leaf.deb"
            try:
                leaf.symlink_to(leaf_target)
            except OSError:
                original_lstat = os.lstat

                def fake_lstat(path):
                    if pathlib.Path(path) == leaf:
                        return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                    return original_lstat(path)

                service = self.helper.SparkPackageHelper(
                    download_root=download,
                    staging_root=incoming,
                    lstat_func=fake_lstat,
                )
            with self.assertRaises(self.helper.HelperError):
                service.stage_deb(leaf, leaf_target.stat().st_uid)

    def test_rejects_a_symlink_in_any_download_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            real = base / "real-download"
            package_dir = real / "sample-app"
            package_dir.mkdir(parents=True)
            source = package_dir / "sample.deb"
            source.write_bytes(b"payload")
            link = base / "download"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                original_lstat = os.lstat

                def fake_lstat(path):
                    if pathlib.Path(path) == link:
                        return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                    return original_lstat(path)

                lstat_func = fake_lstat
            else:
                lstat_func = os.lstat
            linked_source = link / "sample-app" / "sample.deb"
            service = self.helper.SparkPackageHelper(
                download_root=link,
                staging_root=base / "incoming",
                lstat_func=lstat_func,
            )

            with self.assertRaises(self.helper.HelperError) as raised:
                service.stage_deb(linked_source, source.stat().st_uid)
            self.assertEqual("E_FILE_UNSAFE", raised.exception.code)

    def test_rejects_same_name_intermediate_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            download = base / "download"
            package_dir = download / "sample.deb"
            package_dir.mkdir(parents=True)
            source = package_dir / "sample.deb"
            source.write_bytes(b"payload")
            checked = []

            def fake_lstat(path):
                candidate = pathlib.Path(path)
                checked.append(candidate)
                if candidate == package_dir:
                    metadata = os.lstat(package_dir)
                    values = {
                        name: getattr(metadata, name)
                        for name in dir(metadata)
                        if name.startswith("st_")
                    }
                    values["st_mode"] = stat.S_IFLNK | 0o777
                    return types.SimpleNamespace(**values)
                return os.lstat(path)

            service = self.helper.SparkPackageHelper(
                download_root=download,
                staging_root=base / "incoming",
                lstat_func=fake_lstat,
            )

            with self.assertRaises(self.helper.HelperError) as raised:
                service.stage_deb(source, source.stat().st_uid)

            self.assertEqual("E_FILE_UNSAFE", raised.exception.code)
            self.assertIn(package_dir, checked)

    def test_delete_after_install_refuses_an_inode_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "sample.deb"
            source.write_bytes(b"original")
            identity = self.helper.SourceIdentity.from_stat(source.stat())
            source.unlink()
            source.write_bytes(b"replacement")

            deleted = self.helper.SparkPackageHelper.delete_source_if_unchanged(
                source, identity)

            self.assertFalse(deleted)
            self.assertEqual(b"replacement", source.read_bytes())


class SparkPackageRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def stage(self, directory):
        download = pathlib.Path(directory) / "download"
        source_dir = download / "sample-app"
        source_dir.mkdir(parents=True)
        source = source_dir / "sample.deb"
        source.write_bytes(b"repository package")
        service = self.helper.SparkPackageHelper(
            download_root=download,
            staging_root=pathlib.Path(directory) / "incoming",
        )
        return service, source, service.stage_deb(source, source.stat().st_uid)

    def test_requires_exact_dpkg_metadata_and_matching_fixed_repo_sha512(self):
        with tempfile.TemporaryDirectory() as directory:
            _service, _source, staged = self.stage(directory)
            runner = ProtocolRunner(digest=staged.sha512)
            service = self.helper.SparkPackageHelper(runner=runner)

            metadata = service.verify_staged_package(staged, "sample-app")

            self.assertEqual("sample-app", metadata["Package"])
            apt_cache = next(command for command in runner.commands
                             if command[0] == "/usr/bin/apt-cache")
            self.assertIn(
                "Dir::Etc::sourcelist=/etc/apt/sources.list.d/ming-spark-store.list",
                apt_cache,
            )
            self.assertIn("Dir::Etc::sourceparts=-", apt_cache)

    def test_rejects_a_tampered_fixed_source_before_resolver_use(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source_list = base / "ming-spark-store.list"
            keyring = base / "ming-spark-store.gpg"
            source_list.write_text("deb http://attacker.invalid/store /\n", encoding="utf-8")
            keyring.write_bytes(b"key")
            staged_path = base / "staged.deb"
            staged_path.write_bytes(b"payload")
            staged = self.helper.StagedDeb(
                path=staged_path,
                sha512=hashlib.sha512(b"payload").hexdigest(),
                source_path=staged_path,
                source_identity=self.helper.SourceIdentity.from_stat(staged_path.stat()),
            )
            service = self.helper.SparkPackageHelper(
                runner=ProtocolRunner(digest=staged.sha512),
                source_list=source_list,
                keyring_path=keyring,
                verify_source=True,
            )

            with self.assertRaises(self.helper.HelperError) as raised:
                service.verify_staged_package(staged, "sample-app")
            self.assertEqual("E_PACKAGE_UNVERIFIED", raised.exception.code)

    def test_rejects_metadata_architecture_hash_and_repository_parse_mismatches(self):
        with tempfile.TemporaryDirectory() as directory:
            _service, _source, staged = self.stage(directory)
            cases = (
                ("directory package", ProtocolRunner(digest=staged.sha512, package="other-app")),
                ("architecture", ProtocolRunner(digest=staged.sha512, architecture="arm64")),
                ("hash", ProtocolRunner(digest="f" * 128)),
            )
            for label, runner in cases:
                with self.subTest(label=label):
                    service = self.helper.SparkPackageHelper(runner=runner)
                    with self.assertRaises(self.helper.HelperError) as raised:
                        service.verify_staged_package(staged, "sample-app")
                    self.assertEqual("E_PACKAGE_UNVERIFIED", raised.exception.code)

    def test_install_uses_only_staging_and_requires_installed_launch_ready_json(self):
        with tempfile.TemporaryDirectory() as directory:
            download = pathlib.Path(directory) / "download"
            incoming = pathlib.Path(directory) / "incoming"
            source_dir = download / "sample-app"
            source_dir.mkdir(parents=True)
            source = source_dir / "sample.deb"
            source.write_bytes(b"repository package")
            digest = hashlib.sha512(source.read_bytes()).hexdigest()
            runner = ProtocolRunner(digest=digest)
            service = self.helper.SparkPackageHelper(
                runner=runner,
                download_root=download,
                staging_root=incoming,
            )
            request = self.helper.parse_request([
                "ssinstall",
                str(source),
                "--delete-after-install",
                "--no-create-desktop-entry",
                "--native",
            ])

            result = service.execute(request, source.stat().st_uid)

            self.assertTrue(result["ok"])
            self.assertFalse(source.exists())
            install = next(command for command in runner.commands
                           if command[0] == "/usr/local/sbin/ming-package-installer")
            self.assertEqual("install", install[1])
            self.assertNotEqual(str(source), install[2])
            self.assertTrue(pathlib.Path(install[2]).parent.samefile(incoming))
            self.assertEqual(("--resolver", "spark", "--json"), install[3:])

            not_ready = ProtocolRunner(
                digest=digest,
                installer_result={
                    "ok": True,
                    "installed": True,
                    "launch_ready": False,
                    "resolver": "spark",
                    "error_code": "E_LAUNCH_NOT_READY",
                },
            )
            replacement = source_dir / "sample.deb"
            replacement.write_bytes(b"repository package")
            failed = self.helper.SparkPackageHelper(
                runner=not_ready,
                download_root=download,
                staging_root=incoming,
            ).execute(request, replacement.stat().st_uid)
            self.assertFalse(failed["ok"])
            self.assertEqual("E_LAUNCH_NOT_READY", failed["error_code"])

    def test_typed_apt_operations_use_the_shared_lock_and_report_busy(self):
        runner = ProtocolRunner(locked_result=(75, "", "lock busy"))
        service = self.helper.SparkPackageHelper(runner=runner)
        request = self.helper.parse_request(["aptss", "remove", "sample-app"])

        result = service.execute(request, 1000)

        self.assertFalse(result["ok"])
        self.assertEqual("E_PACKAGE_BUSY", result["error_code"])
        command = runner.commands[-1]
        self.assertEqual("/usr/bin/flock", command[0])
        self.assertIn("/run/lock/ming-package-manager.lock", command)
        self.assertIn("DPkg::Lock::Timeout=60", command)
        self.assertNotIn("sh", command)
        self.assertNotIn("eval", command)

    def test_package_name_install_requires_exact_status_and_launcher_postflight(self):
        def runtime_for(payload):
            class FakeInstaller:
                def __init__(self, runner=None):
                    self.runner = runner

                def verify_installed(self, _package):
                    return dict(payload)

            return types.SimpleNamespace(
                PACKAGE_INSTALLER_CONTRACT="ming-package-installer-26.4.0-v4",
                PackageInstaller=FakeInstaller,
            )

        not_installed = ProtocolRunner()
        request = self.helper.parse_request([
            "ssinstall", "sample-app", "--no-create-desktop-entry", "--native"])

        missing = self.helper.SparkPackageHelper(
            runner=not_installed,
            package_installer_module=runtime_for({
                "ok": False,
                "package": "sample-app",
                "installed": False,
                "launch_ready": False,
                "launchers": [],
                "error_code": "E_PACKAGE_FAILED",
            }),
        ).execute(request, 1000)

        self.assertFalse(missing["ok"])
        self.assertFalse(missing["installed"])
        self.assertFalse(missing["launch_ready"])
        self.assertEqual("E_PACKAGE_FAILED", missing["error_code"])

        not_ready = ProtocolRunner(installer_result={
            "ok": True,
            "package": "sample-app",
            "installed": True,
            "launch_ready": False,
            "launchers": [{"path": "/usr/share/applications/sample.desktop", "ok": False}],
            "error_code": "E_LAUNCH_NOT_READY",
        })
        failed = self.helper.SparkPackageHelper(
            runner=not_ready,
            package_installer_module=runtime_for(not_ready.installer_result),
        ).execute(request, 1000)
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["installed"])
        self.assertFalse(failed["launch_ready"])
        self.assertEqual("E_LAUNCH_NOT_READY", failed["error_code"])
        self.assertEqual(not_ready.installer_result["launchers"], failed["launchers"])

    def test_package_name_install_loads_matching_installer_contract_without_reinstall(self):
        helper = self.helper
        self.assertIn(
            "package_installer_module",
            inspect.signature(helper.SparkPackageHelper).parameters,
        )

        class FakeInstaller:
            def __init__(self, runner=None):
                self.runner = runner

            def verify_installed(self, package):
                return {
                    "ok": True,
                    "package": package,
                    "installed": True,
                    "launch_ready": True,
                    "launchers": [{"path": "/usr/share/applications/sample.desktop", "ok": True}],
                    "error_code": "",
                }

        runtime = types.SimpleNamespace(
            PACKAGE_INSTALLER_CONTRACT="ming-package-installer-26.4.0-v4",
            PackageInstaller=FakeInstaller,
        )
        runner = ProtocolRunner()
        service = helper.SparkPackageHelper(
            runner=runner, package_installer_module=runtime)
        request = helper.parse_request([
            "ssinstall", "sample-app", "--no-create-desktop-entry", "--native"])

        result = service.execute(request, 1000)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["installed"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual(runtime.PackageInstaller(runner=runner).verify_installed(
            "sample-app")["launchers"], result["launchers"])
        flattened = " ".join(" ".join(command) for command in runner.commands)
        self.assertEqual(1, flattened.count("apt-get"))
        self.assertNotIn("--reinstall", flattened)

        mismatched = types.SimpleNamespace(
            PACKAGE_INSTALLER_CONTRACT="ming-package-installer-26.4.0-v3",
            PackageInstaller=FakeInstaller,
        )
        rejected = helper.SparkPackageHelper(
            runner=ProtocolRunner(), package_installer_module=mismatched,
        ).execute(request, 1000)
        self.assertFalse(rejected["ok"])
        self.assertEqual("E_PACKAGE_FAILED", rejected["error_code"])

    def test_installer_json_must_be_ok_and_use_the_spark_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            download = pathlib.Path(directory) / "download"
            source_dir = download / "sample-app"
            source_dir.mkdir(parents=True)
            source = source_dir / "sample.deb"
            source.write_bytes(b"repository package")
            digest = hashlib.sha512(source.read_bytes()).hexdigest()
            request = self.helper.parse_request([
                "ssinstall", str(source), "--delete-after-install",
                "--no-create-desktop-entry", "--native",
            ])
            for label, result_payload in (
                ("not ok", {"ok": False, "installed": True, "launch_ready": True, "resolver": "spark"}),
                ("wrong resolver", {"ok": True, "installed": True, "launch_ready": True, "resolver": "apt"}),
            ):
                with self.subTest(label=label):
                    source.write_bytes(b"repository package")
                    runner = ProtocolRunner(digest=digest, installer_result=result_payload)
                    service = self.helper.SparkPackageHelper(
                        runner=runner, download_root=download,
                        staging_root=pathlib.Path(directory) / ("incoming-" + label.replace(" ", "-")),
                    )
                    result = service.execute(request, source.stat().st_uid)
                    self.assertFalse(result["ok"])
                    self.assertEqual("E_PACKAGE_FAILED", result["error_code"])

    def test_jsonl_log_redacts_credentials_and_url_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = pathlib.Path(directory) / "package-installer.jsonl"
            service = self.helper.SparkPackageHelper(log_path=log_path)

            service.log_event(
                "failed",
                detail=(
                    "https://alice:secret@example.invalid/pkg?token=abc "
                    "password=hunter2"
                ),
            )

            record = json.loads(log_path.read_text(encoding="utf-8"))
            serialized = json.dumps(record, sort_keys=True)
            for secret in ("alice", "secret", "abc", "hunter2"):
                self.assertNotIn(secret, serialized)
            self.assertNotIn("?", serialized)


if __name__ == "__main__":
    unittest.main()
