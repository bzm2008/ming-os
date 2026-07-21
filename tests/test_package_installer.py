import importlib.util
import hashlib
import inspect
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "assets" / "ming-package-installer.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("ming_package_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, timeout=20):
        command = tuple(command)
        self.commands.append(command)
        response = self.responses.get(command, (1, "", "unexpected command"))
        return response.pop(0) if isinstance(response, list) else response


def metadata_output(package_name, version="1.2.3", architecture="amd64"):
    return (
        "Package: {}\nVersion: {}\nArchitecture: {}\n".format(
            package_name, version, architecture))


def locked_apt_command(*arguments):
    return (
        "flock", "--exclusive", "--timeout", "30",
        "--conflict-exit-code", "75",
        "/run/lock/ming-package-manager.lock",
        "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
        "-o", "DPkg::Lock::Timeout=60",
        *tuple(str(value) for value in arguments),
    )


def successful_install_runner(package_file, package_name, desktop_paths=()):
    metadata = (
        "dpkg-deb", "--field", str(package_file),
        "Package", "Version", "Architecture",
    )
    apt_install = locked_apt_command("install", package_file)
    verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", package_name)
    list_files = ("dpkg-query", "-L", package_name)
    refresh_desktops = ("update-desktop-database", "/usr/share/applications")
    refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
    responses = {
        metadata: (0, metadata_output(package_name), ""),
        apt_install: (0, "", ""),
        verify: (0, "ii ", ""),
        refresh_desktops: (0, "", ""),
        refresh_icons: (0, "", ""),
        list_files: (0, "".join("{}\n".format(path) for path in desktop_paths), ""),
    }
    for desktop in desktop_paths:
        responses[("dpkg-query", "-S", "--", str(desktop))] = (
            0, "{}: {}\n".format(package_name, desktop), "")
    responses[(
        "dpkg-query", "-W", "-f=${db:Status-Abbrev}\\t${binary:Package}\\n",
        "--", package_name,
    )] = (0, "ii \t{}\n".format(package_name), "")
    return FakeRunner(responses)


def package_application_dir(directory):
    applications = pathlib.Path(directory) / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    return applications


def configured_package_installer(module, application_dir, **kwargs):
    kwargs.setdefault(
        "desktop_candidate_verifier",
        lambda candidate: pathlib.Path(candidate).parent == pathlib.Path(application_dir),
    )
    service = module.PackageInstaller(**kwargs)
    service.application_dir = pathlib.Path(application_dir)
    service.current_desktops = {"xfce"}
    return service


class PackageInstallerInspectTests(unittest.TestCase):
    def test_inspect_accepts_standard_labeled_dpkg_deb_metadata(self):
        """dpkg-deb labels every line when multiple fields are requested."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"not-a-real-deb-but-a-regular-file")
            command = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({command: (
                0,
                "Package: sample-app\nVersion: 1:2.3\nArchitecture: amd64\n",
                "",
            )})

            result = installer.PackageInstaller(
                runner=runner,
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertTrue(result["ok"])
        self.assertEqual("sample-app", result["package"])
        self.assertEqual("1:2.3", result["version"])
        self.assertEqual("amd64", result["architecture"])
        self.assertEqual([command], runner.commands)

    def test_inspect_returns_verified_amd64_deb_metadata(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"not-a-real-deb-but-a-regular-file")
            command = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({command: (0, metadata_output("sample-app"), "")})

            result = installer.PackageInstaller(
                runner=runner,
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertTrue(result["ok"])
        self.assertEqual("sample-app", result["package"])
        self.assertEqual("1.2.3", result["version"])
        self.assertEqual("amd64", result["architecture"])
        self.assertEqual(str(package), result["file"])
        self.assertIn("log_path", result)
        self.assertEqual([command], runner.commands)

    def test_verify_installed_is_non_mutating_and_reports_launcher_truth(self):
        installer = load_installer()
        self.assertTrue(
            callable(getattr(installer.PackageInstaller, "verify_installed", None)),
            "PackageInstaller must expose a non-mutating verify_installed method",
        )
        with tempfile.TemporaryDirectory() as directory:
            applications = package_application_dir(directory)
            executable = pathlib.Path(directory) / "usr" / "bin" / "sample-app"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            desktop = applications / "sample-app.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample\n"
                "Exec=\"%s\"\n" % executable.as_posix(),
                encoding="utf-8",
            )
            exact_status = ("dpkg-query", "-W", "-f=${Status}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            owner = ("dpkg-query", "-S", "--", str(desktop))
            installed_owners = (
                "dpkg-query", "-W",
                "-f=${db:Status-Abbrev}\\t${binary:Package}\\n",
                "--", "sample-app",
            )
            runner = FakeRunner({
                exact_status: (0, "install ok installed\n", ""),
                list_files: (0, str(desktop) + "\n", ""),
                owner: (0, "sample-app: %s\n" % desktop, ""),
                installed_owners: (0, "ii \tsample-app\n", ""),
            })
            service = configured_package_installer(
                installer, applications, runner=runner,
                log_path=pathlib.Path(directory) / "installer.log")

            result = service.verify_installed("sample-app")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["installed"])
        self.assertTrue(result["launch_ready"], result)
        self.assertEqual([str(desktop)], [item["path"] for item in result["launchers"]])
        flattened = " ".join(" ".join(command) for command in runner.commands)
        self.assertNotIn("apt-get", flattened)
        self.assertNotIn("flock", flattened)

    def test_verify_installed_rejects_non_exact_dpkg_status_before_launcher_scan(self):
        installer = load_installer()
        self.assertTrue(
            callable(getattr(installer.PackageInstaller, "verify_installed", None)),
            "PackageInstaller must expose a non-mutating verify_installed method",
        )
        exact_status = ("dpkg-query", "-W", "-f=${Status}", "sample-app")
        runner = FakeRunner({exact_status: (0, "deinstall ok config-files\n", "")})

        result = installer.PackageInstaller(runner=runner).verify_installed("sample-app")

        self.assertFalse(result["ok"])
        self.assertFalse(result["installed"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("E_PACKAGE_FAILED", result["error_code"])
        self.assertEqual([exact_status], runner.commands)

    def test_install_rejects_wrong_architecture_before_apt_or_privilege_use(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "foreign.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({metadata: (0, metadata_output("foreign-app", "1.0", "i386"), "")})
            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: (_ for _ in ()).throw(AssertionError("must not need root")),
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertFalse(result["ok"])
        self.assertEqual("validation_failed", result["state"])
        self.assertEqual("i386", result["architecture"])
        self.assertEqual([metadata], runner.commands)

    def test_inspect_labels_unsupported_architecture_as_validation_failure(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "foreign.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            result = installer.PackageInstaller(
                runner=FakeRunner({metadata: (0, metadata_output("foreign-app", "1.0", "i386"), "")}),
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertFalse(result["ok"])
        self.assertEqual("validation_failed", result["state"])
        self.assertEqual("i386", result["architecture"])

    def test_inspect_accepts_architecture_independent_debs(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "shared-data.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            result = installer.PackageInstaller(
                runner=FakeRunner({metadata: (0, metadata_output("shared-data", "1.0", "all"), "")}),
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertTrue(result["ok"])
        self.assertEqual("all", result["architecture"])

    def test_inspect_refuses_a_directory_named_like_a_deb_before_metadata_probe(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "not-a-package.deb"
            package.mkdir()
            runner = FakeRunner({})

            result = installer.PackageInstaller(
                runner=runner,
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertFalse(result["ok"])
        self.assertIn("普通", result["error"])
        self.assertEqual([], runner.commands)

    def test_inspect_reports_metadata_timeout_as_a_structured_error(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "slow.deb"
            package.write_bytes(b"local package")

            def timeout_runner(command, timeout=20):
                raise subprocess.TimeoutExpired(command, timeout)

            result = installer.PackageInstaller(
                runner=timeout_runner,
                log_path=pathlib.Path(directory) / "installer.log",
            ).inspect(package)

        self.assertFalse(result["ok"])
        self.assertEqual("validation_failed", result["state"])
        self.assertIn("超时", result["error"])


class PackageInstallerTransactionTests(unittest.TestCase):
    def test_spark_resolver_uses_the_pinned_source_and_shared_lock(self):
        installer = load_installer()
        command = installer.PackageInstaller._apt_install_command(
            "/var/lib/ming-package-installer/incoming/sample.deb",
            resolver="spark",
        )

        self.assertEqual("flock", command[0])
        self.assertIn("/run/lock/ming-package-manager.lock", command)
        self.assertIn("DPkg::Lock::Timeout=60", command)
        self.assertIn(
            "Dir::Etc::sourcelist=/etc/apt/sources.list.d/ming-spark-store.list",
            command,
        )
        self.assertIn("Dir::Etc::sourceparts=-", command)
        self.assertEqual("ming-package-installer-26.4.0-v4", installer.PACKAGE_INSTALLER_CONTRACT)

    def test_cli_passes_spark_resolver_and_json_contract(self):
        installer = load_installer()

        class SpyInstaller:
            def __init__(self):
                self.calls = []

            def install(self, package_file, resolver="apt"):
                self.calls.append((package_file, resolver))
                return {
                    "ok": True,
                    "installed": True,
                    "launch_ready": True,
                    "resolver": resolver,
                }

        spy = SpyInstaller()
        stdout = io.StringIO()
        returncode = installer.main(
            [
                "install", "/tmp/sample.deb", "--resolver", "spark", "--json",
            ],
            installer=spy,
            stdout=stdout,
        )

        self.assertEqual(0, returncode)
        self.assertEqual([("/tmp/sample.deb", "spark")], spy.calls)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("spark", payload["resolver"])
        self.assertTrue(payload["installed"])

    def test_default_log_path_is_structured_package_installer_jsonl(self):
        installer = load_installer()
        service = installer.PackageInstaller()
        self.assertEqual(
            "/var/log/ming-os/package-installer.jsonl",
            service.log_path.as_posix(),
        )

    def test_all_apt_commands_share_the_ming_package_manager_lock(self):
        installer = load_installer()
        commands = (
            installer.PackageInstaller._apt_install_command("/tmp/sample.deb"),
            installer.PackageInstaller._apt_fix_command(),
            installer.PackageInstaller._apt_reinstall_command("sample-app"),
        )

        for command in commands:
            self.assertEqual("flock", command[0])
            self.assertIn("/run/lock/ming-package-manager.lock", command)
            self.assertIn("DPkg::Lock::Timeout=60", command)
            self.assertIn("apt-get", command)

    def test_launch_problem_has_a_stable_machine_error_code(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "data-only-app.deb"
            package.write_bytes(b"local package")
            result = installer.PackageInstaller(
                runner=successful_install_runner(package, "data-only-app"),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("E_LAUNCH_NOT_READY", result["error_code"])

    def test_package_lock_conflict_is_not_reported_as_a_resolver_failure(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "busy-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = installer.PackageInstaller._apt_install_command(package)
            runner = FakeRunner({
                metadata: (0, metadata_output("busy-app"), ""),
                apt_install: (75, "", "package manager lock is busy"),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertFalse(result["ok"])
        self.assertEqual("E_PACKAGE_BUSY", result["error_code"])
        self.assertNotIn(installer.PackageInstaller._apt_fix_command(), runner.commands)

    def test_apt_failure_codes_distinguish_lock_resolver_and_package_failures(self):
        installer = load_installer()
        classify = installer.PackageInstaller._apt_failure_code

        self.assertEqual(
            "E_PACKAGE_BUSY",
            classify(100, "", "Could not get lock /var/lib/dpkg/lock-frontend"),
        )
        self.assertEqual(
            "E_RESOLVER_FAILED",
            classify(100, "", "unmet dependencies; held broken packages"),
        )
        for returncode, detail in (
            (124, "command timed out"),
            (100, "No space left on device"),
            (100, "dpkg-deb: error: archive is truncated"),
            (100, "installed post-installation script subprocess returned error"),
            (100, "mirror worker: Resource temporarily unavailable"),
        ):
            self.assertEqual("E_PACKAGE_FAILED", classify(returncode, "", detail))

    def test_non_resolver_package_failure_does_not_run_dependency_repair(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "full-disk.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = installer.PackageInstaller._apt_install_command(package)
            runner = FakeRunner({
                metadata: (0, metadata_output("full-disk"), ""),
                apt_install: (100, "", "No space left on device"),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertEqual("E_PACKAGE_FAILED", result["error_code"])
        self.assertFalse(result["dependency_repair_attempted"])
        self.assertNotIn(installer.PackageInstaller._apt_fix_command(), runner.commands)

    def test_command_boundary_redacts_stdout_and_stderr_secrets(self):
        installer = load_installer()
        secret_output = (
            "fetch https://alice:s3cr3t@example.invalid/repo?token=abc123 "
            "Password=hunter2 access_token=refresh-secret"
        )
        service = installer.PackageInstaller(
            runner=FakeRunner({("probe",): (1, secret_output, secret_output)}),
        )

        _returncode, output, error = service._call(("probe",), timeout=20)

        for secret in ("alice", "s3cr3t", "abc123", "hunter2", "refresh-secret"):
            self.assertNotIn(secret, output)
            self.assertNotIn(secret, error)
        self.assertNotIn("?", output)

    def test_apt_failure_json_and_log_only_expose_a_short_redacted_reason(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "busy-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = installer.PackageInstaller._apt_install_command(package)
            secret_error = (
                "Could not get lock /var/lib/dpkg/lock-frontend; "
                "https://alice:s3cr3t@example.invalid/repo?token=abc123 "
                "Password=hunter2"
            )
            logs = []
            service = installer.PackageInstaller(
                runner=FakeRunner({
                    metadata: (0, metadata_output("busy-app"), ""),
                    apt_install: (75, "", secret_error),
                }),
                uid_getter=lambda: 0,
                logger=logs.append,
            )
            stdout = io.StringIO()

            returncode = installer.main(
                ["install", str(package)], installer=service, stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(4, returncode)
        self.assertEqual("E_PACKAGE_BUSY", payload["error_code"])
        self.assertEqual("软件包管理器正忙，请稍后重试。", payload["error"])
        boundary = stdout.getvalue() + "\n" + "\n".join(logs)
        for secret in ("alice", "s3cr3t", "abc123", "hunter2"):
            self.assertNotIn(secret, boundary)


class PackageInstallerInstallTests(unittest.TestCase):
    def test_install_reports_a_readable_launcher_warning_when_exec_is_missing(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "sample-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample App\n"
                "Exec=/opt/sample-app/bin/sample-app\n",
                encoding="utf-8",
            )
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = locked_apt_command("install", package)
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                metadata: (0, metadata_output("sample-app"), ""),
                apt_install: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
                list_files: (0, str(desktop) + "\n", ""),
            })

            result = configured_package_installer(
                installer, applications,
                runner=runner, uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertEqual(1, len(result["launcher_warnings"]))
        self.assertIn("找不到启动程序", result["launcher_warnings"][0]["error"])
        self.assertIn("无法启动", result["error"])

    def test_install_classifies_a_protected_package_shell_wrapper_for_broker_activation(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "store-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec=sh -c 'exec /opt/store-app/run'\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "store-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual("installed_with_desktop_activation", result["state"])
        self.assertTrue(result["launchers"][0]["ok"])
        self.assertEqual("desktop_app_info", result["launchers"][0]["activation"])

    def test_install_requires_exact_owner_for_direct_package_launcher(self):
        """A regular package launcher is not launch-ready on an ownership mismatch."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "direct-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "direct-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Direct App\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )
            runner = successful_install_runner(package, "direct-app", (desktop,))
            runner.responses[("dpkg-query", "-S", "--", str(desktop))] = (
                0, "other-package: {}\n".format(desktop), "")

            result = configured_package_installer(
                installer, applications,
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["ok"])
        self.assertIn("所有权", result["launchers"][0]["error"])

    def test_install_classifies_protected_ksh_wrapper_using_shared_rejection(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "ksh-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "ksh-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Ksh App\n"
                "Exec=ksh -c 'exec /opt/ksh-app/run'\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "ksh-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual("installed_with_desktop_activation", result["state"])
        self.assertEqual("desktop_app_info", result["launchers"][0]["activation"])

    def test_install_rejects_shell_wrapper_when_broker_owner_is_not_installed(self):
        """A catalog result must not promise a launch the broker will reject."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "store-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec=sh -c 'exec /opt/store-app/run'\n",
                encoding="utf-8",
            )
            runner = successful_install_runner(package, "store-app", (desktop,))
            runner.responses[(
                "dpkg-query", "-W", "-f=${db:Status-Abbrev}\\t${binary:Package}\\n",
                "--", "store-app",
            )] = (0, "hi \tstore-app\n", "")

            result = configured_package_installer(
                installer, applications,
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertNotEqual("desktop_app_info", result["launchers"][0]["activation"])

    def test_install_rejects_shell_wrapper_replaced_by_a_different_installed_package(self):
        """`dpkg -L` membership cannot override the broker's exact owner check."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "store-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec=sh -c 'exec /opt/store-app/run'\n",
                encoding="utf-8",
            )
            runner = successful_install_runner(package, "store-app", (desktop,))
            with mock.patch.object(
                    installer.COMMON, "installed_package_owner",
                    return_value="replacement-app") as owner_lookup:
                result = configured_package_installer(
                    installer, applications,
                    runner=runner,
                    uid_getter=lambda: 0,
                    log_path=pathlib.Path(directory) / "installer.log",
                    desktop_candidate_verifier=lambda candidate: candidate == desktop,
                ).install(package)

        self.assertEqual("store-app", owner_lookup.call_args.kwargs["expected_package"])

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertNotEqual("desktop_app_info", result["launchers"][0]["activation"])

    def test_install_accepts_arch_qualified_exact_owner_for_the_installed_package(self):
        """A package-qualified owner remains the same Debian binary package."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "store-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec=sh -c 'exec /opt/store-app/run'\n",
                encoding="utf-8",
            )
            runner = successful_install_runner(package, "store-app", (desktop,))
            with mock.patch.object(
                    installer.COMMON, "installed_package_owner",
                    return_value="store-app:amd64") as owner_lookup:
                result = configured_package_installer(
                    installer, applications,
                    runner=runner,
                    uid_getter=lambda: 0,
                    log_path=pathlib.Path(directory) / "installer.log",
                    desktop_candidate_verifier=lambda candidate: candidate == desktop,
                ).install(package)

        self.assertEqual("store-app", owner_lookup.call_args.kwargs["expected_package"])

        self.assertTrue(result["ok"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual("installed_with_desktop_activation", result["state"])

    def test_install_rejects_a_direct_launcher_without_protected_descriptor_state(self):
        """Direct entries need the same protected descriptor proof as the broker."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "store-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )
            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "store-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda _path: False,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertIn("保护", result["launchers"][0]["error"])

    def test_launcher_visibility_combines_xdg_and_desktop_session_identifiers(self):
        """Store launchers must see desktop IDs supplied through either standard variable."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "desktop-session-only.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Desktop Session App\n"
                "OnlyShowIn=GNOME;\nExec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {
                    "XDG_CURRENT_DESKTOP": "MING:XFCE",
                    "DESKTOP_SESSION": "GNOME",
            }, clear=False):
                record = installer.PackageInstaller(
                    log_path=pathlib.Path(directory) / "installer.log",
                )._launcher_record(desktop)

        self.assertTrue(record["visible"])
        self.assertTrue(record["ok"])
        self.assertEqual("direct", record["activation"])

    def test_install_does_not_activate_protected_wrapper_with_other_shell_syntax(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "unsafe-wrapper.deb"
            applications = package_application_dir(directory)
            desktop = applications / "unsafe-wrapper.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Unsafe Wrapper\n"
                "Exec=sh -c 'exec /opt/unsafe-wrapper/run' ; /bin/true\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "unsafe-wrapper", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertNotEqual("desktop_app_info", result["launchers"][0]["activation"])

    def test_install_reports_a_hidden_only_launcher_as_not_ready(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "hidden-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "hidden-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nNoDisplay=true\n"
                "Name=Hidden App\nExec=/opt/hidden-app/missing\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "hidden-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["visible"])
        self.assertEqual(1, len(result["launcher_warnings"]))

    def test_install_repairs_dependencies_once_then_verifies_and_refreshes(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = locked_apt_command("install", package)
            fix_dependencies = locked_apt_command("-f", "install")
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                metadata: (0, metadata_output("sample-app"), ""),
                apt_install: [(100, "", "unmet dependencies"), (0, "", "")],
                fix_dependencies: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
                list_files: (0, "", ""),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launch_ready"])
        self.assertIn("图形启动器", result["error"])
        self.assertTrue(result["dependency_repair_attempted"])
        self.assertEqual("sample-app", result["package"])
        self.assertEqual(
            [metadata, apt_install, fix_dependencies, apt_install, verify,
             refresh_desktops, refresh_icons, list_files],
            runner.commands,
        )

    def test_install_requires_administrator_after_safe_inspection(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({metadata: (0, metadata_output("sample-app"), "")})

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 1000,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertFalse(result["ok"])
        self.assertEqual("permission_denied", result["state"])
        self.assertEqual([metadata], runner.commands)

    def test_install_does_not_refresh_caches_when_package_verification_fails(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            apt_install = locked_apt_command("install", package)
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            runner = FakeRunner({
                metadata: (0, metadata_output("sample-app"), ""),
                apt_install: (0, "", ""),
                verify: (0, "hi ", ""),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertFalse(result["ok"])
        self.assertEqual("verification_failed", result["state"])
        self.assertEqual([metadata, apt_install, verify], runner.commands)

    def test_install_rejects_shared_parser_exec_operator_before_direct_readiness(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "operator-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "operator-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Operator App\n"
                "Exec='{}' | /bin/true\n".format(sys.executable),
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "operator-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertIn("shell", result["launchers"][0]["error"])

    def test_install_rejects_non_application_desktop_entry(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "link-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "link-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Link\nName=Link App\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "link-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["ok"])

    def test_install_rejects_missing_required_desktop_name(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "unnamed-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "unnamed-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "unnamed-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["ok"])

    def test_install_rejects_unavailable_tryexec(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "tryexec-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "tryexec-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=TryExec App\n"
                "Exec='{}' -c pass\nTryExec=missing-ming-test-command\n".format(sys.executable),
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(package, "tryexec-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["ok"])

    def test_install_reports_catalog_entry_hidden_by_onlyshowin_as_not_ready(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "desktop-filtered-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "desktop-filtered-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Desktop Filtered App\n"
                "OnlyShowIn=GNOME;\nExec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(
                    package, "desktop-filtered-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["visible"])
        self.assertIn("OnlyShowIn", result["launchers"][0]["error"])

    def test_install_does_not_activate_protected_wrapper_hidden_by_onlyshowin(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "onlyshow-wrapper.deb"
            applications = package_application_dir(directory)
            desktop = applications / "onlyshow-wrapper.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=OnlyShow Wrapper\n"
                "OnlyShowIn=GNOME;\nExec=sh -c 'exec /opt/onlyshow-wrapper/run'\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(
                    package, "onlyshow-wrapper", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["visible"])
        self.assertNotEqual("desktop_app_info", result["launchers"][0]["activation"])
        self.assertIn("OnlyShowIn", result["launchers"][0]["error"])

    def test_install_does_not_activate_protected_wrapper_excluded_by_notshowin(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "notshow-wrapper.deb"
            applications = package_application_dir(directory)
            desktop = applications / "notshow-wrapper.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=NotShow Wrapper\n"
                "NotShowIn=XFCE;\nExec=sh -c 'exec /opt/notshow-wrapper/run'\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(
                    package, "notshow-wrapper", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
                desktop_candidate_verifier=lambda candidate: candidate == desktop,
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertFalse(result["launchers"][0]["visible"])
        self.assertNotEqual("desktop_app_info", result["launchers"][0]["activation"])
        self.assertIn("NotShowIn", result["launchers"][0]["error"])

    def test_install_ignores_package_desktop_files_outside_system_catalog(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "catalog-app.deb"
            applications = package_application_dir(directory)
            desktop = applications / "catalog-app.desktop"
            documentation = pathlib.Path(directory) / "usr" / "share" / "doc" / "catalog-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Catalog App\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )
            documentation.parent.mkdir(parents=True)
            documentation.write_text(
                "[Desktop Entry]\nType=Application\nName=Documentation\n"
                "Exec=/definitely/missing\n",
                encoding="utf-8",
            )

            result = configured_package_installer(
                installer, applications,
                runner=successful_install_runner(
                    package, "catalog-app", (desktop, documentation)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual([str(desktop)], [record["path"] for record in result["launchers"]])

    def test_install_reports_launcher_enumeration_failure_with_safe_repair_action(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "enumeration-app.deb"
            package.write_bytes(b"local package")
            log_path = pathlib.Path(directory) / "installer.log"
            runner = successful_install_runner(package, "enumeration-app")
            runner.responses[("dpkg-query", "-L", "enumeration-app")] = (
                1, "", "database unavailable")

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=log_path,
            ).install(package)

            log_text = log_path.read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertIn("枚举", result["error"])
        self.assertEqual(
            ["/usr/local/sbin/ming-package-installer", "repair", "enumeration-app"],
            result["repair_argv"],
        )
        repair_args = installer.build_parser().parse_args(result["repair_argv"][1:])
        self.assertEqual("repair", repair_args.action)
        self.assertEqual("enumeration-app", repair_args.package)
        self.assertIn("database unavailable", log_text)

    def test_install_without_a_system_launcher_is_not_launch_ready(self):
        """Installed data-only packages need an explicit repair path, not a false ready state."""
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "data-only-app.deb"
            package.write_bytes(b"local package")
            result = installer.PackageInstaller(
                runner=successful_install_runner(package, "data-only-app"),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("installed_with_launch_problem", result["state"])
        self.assertIn("图形启动器", result["error"])
        self.assertEqual(
            ["/usr/local/sbin/ming-package-installer", "repair", "data-only-app"],
            result["repair_argv"],
        )


class PackageInstallerRepairTests(unittest.TestCase):
    def test_repair_reinstalls_package_and_refreshes_desktop_entries(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            reinstall = locked_apt_command("--reinstall", "install", "sample-app")
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                reinstall: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
                list_files: (0, "", ""),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).repair("sample-app")

        self.assertTrue(result["ok"])
        self.assertEqual("repaired_with_launch_problem", result["state"])
        self.assertFalse(result["launch_ready"])
        self.assertIn("图形启动器", result["error"])
        self.assertEqual("sample-app", result["package"])
        self.assertEqual(
            [reinstall, verify, refresh_desktops, refresh_icons, list_files], runner.commands)

    def test_repair_keeps_an_installed_package_but_reports_a_broken_visible_launcher(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            applications = package_application_dir(directory)
            desktop = applications / "sample-app.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample App\n"
                "Exec=/opt/sample-app/missing\n",
                encoding="utf-8",
            )
            reinstall = locked_apt_command("--reinstall", "install", "sample-app")
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                reinstall: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
                list_files: (0, str(desktop) + "\n", ""),
            })

            result = configured_package_installer(
                installer, applications,
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).repair("sample-app")

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("repaired_with_launch_problem", result["state"])
        self.assertIn("无法启动", result["error"])


class PackageInstallerLauncherTests(unittest.TestCase):
    def test_common_integrity_pin_matches_the_deployed_shared_library(self):
        installer = load_installer()
        expected = hashlib.sha256(
            (ROOT / "assets" / "ming-shell-common.py").read_bytes()).hexdigest()

        self.assertEqual(expected, installer.REQUIRED_COMMON_SHA256)

    def test_common_loader_finds_library_copy_for_the_installed_sbin_layout(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            prefix = pathlib.Path(directory) / "usr" / "local"
            program = prefix / "sbin" / "ming-package-installer"
            common = prefix / "lib" / "ming-os" / "ming-shell-common.py"
            program.parent.mkdir(parents=True)
            common.parent.mkdir(parents=True)
            program.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            common.write_bytes((ROOT / "assets" / "ming-shell-common.py").read_bytes())

            loaded = installer._load_common(program_path=program, install_prefix=prefix)

        self.assertIsNotNone(loaded)
        self.assertTrue(callable(loaded.parse_desktop_file))

    def test_common_loader_rejects_an_old_unmatched_library_copy(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            prefix = pathlib.Path(directory) / "usr" / "local"
            program = prefix / "sbin" / "ming-package-installer"
            common = prefix / "lib" / "ming-os" / "ming-shell-common.py"
            program.parent.mkdir(parents=True)
            common.parent.mkdir(parents=True)
            program.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            common.write_text("old_contract = True\n", encoding="utf-8")

            loaded = installer._load_common(program_path=program, install_prefix=prefix)

        self.assertIsNone(loaded)

    def test_normal_direct_launcher_is_marked_ready_for_direct_activation(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "sample-app.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample App\n"
                "Exec='{}' -c pass\n".format(sys.executable),
                encoding="utf-8",
            )

            record = installer.PackageInstaller(
                log_path=pathlib.Path(directory) / "installer.log",
            )._launcher_record(desktop)

        self.assertTrue(record["ok"])
        self.assertTrue(record["visible"])
        self.assertEqual("direct", record["activation"])

    def test_missing_elf_library_remains_a_launch_problem(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            executable = pathlib.Path(directory) / "sample-app"
            desktop = pathlib.Path(directory) / "sample-app.desktop"
            executable.write_bytes(b"\x7fELFnot-a-real-elf")
            executable.chmod(0o755)
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample App\n"
                "Exec='{}'\n".format(executable),
                encoding="utf-8",
            )
            runner = FakeRunner({
                ("ldd", str(executable)): (0, "libmissing.so => not found\n", ""),
            })

            record = installer.PackageInstaller(
                runner=runner,
                log_path=pathlib.Path(directory) / "installer.log",
            )._launcher_record(desktop)

        self.assertFalse(record["ok"])
        self.assertTrue(record["visible"])
        self.assertEqual("direct", record["activation"])
        self.assertIn("缺少运行库", record["error"])


class PackageInstallerCliTests(unittest.TestCase):
    def test_inspect_json_cli_emits_structured_package_result(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            service = installer.PackageInstaller(
                runner=FakeRunner({metadata: (0, metadata_output("sample-app"), "")}),
                log_path=pathlib.Path(directory) / "installer.log",
            )
            stdout = io.StringIO()

            returncode = installer.main(
                ["inspect", str(package), "--json"], installer=service, stdout=stdout)

        self.assertEqual(0, returncode)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual("sample-app", payload["package"])
        self.assertEqual("amd64", payload["architecture"])


if __name__ == "__main__":
    unittest.main()
