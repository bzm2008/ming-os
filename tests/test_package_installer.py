import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


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


def successful_install_runner(package_file, package_name, desktop_paths=()):
    metadata = (
        "dpkg-deb", "--field", str(package_file),
        "Package", "Version", "Architecture",
    )
    apt_install = (
        "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "install", str(package_file),
    )
    verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", package_name)
    list_files = ("dpkg-query", "-L", package_name)
    refresh_desktops = ("update-desktop-database", "/usr/share/applications")
    refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
    return FakeRunner({
        metadata: (0, "{}\n1.2.3\namd64\n".format(package_name), ""),
        apt_install: (0, "", ""),
        verify: (0, "ii ", ""),
        refresh_desktops: (0, "", ""),
        refresh_icons: (0, "", ""),
        list_files: (0, "".join("{}\n".format(path) for path in desktop_paths), ""),
    })


class PackageInstallerInspectTests(unittest.TestCase):
    def test_inspect_returns_verified_amd64_deb_metadata(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            package.write_bytes(b"not-a-real-deb-but-a-regular-file")
            command = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({command: (0, "sample-app\n1.2.3\namd64\n", "")})

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

    def test_install_rejects_wrong_architecture_before_apt_or_privilege_use(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "foreign.deb"
            package.write_bytes(b"local package")
            metadata = (
                "dpkg-deb", "--field", str(package),
                "Package", "Version", "Architecture",
            )
            runner = FakeRunner({metadata: (0, "foreign-app\n1.0\ni386\n", "")})
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
                runner=FakeRunner({metadata: (0, "foreign-app\n1.0\ni386\n", "")}),
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
                runner=FakeRunner({metadata: (0, "shared-data\n1.0\nall\n", "")}),
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


class PackageInstallerInstallTests(unittest.TestCase):
    def test_install_reports_a_readable_launcher_warning_when_exec_is_missing(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "sample-app.deb"
            desktop = pathlib.Path(directory) / "sample-app.desktop"
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
            apt_install = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "install", str(package),
            )
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                metadata: (0, "sample-app\n1.2.3\namd64\n", ""),
                apt_install: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
                list_files: (0, str(desktop) + "\n", ""),
            })

            result = installer.PackageInstaller(
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
            desktop = pathlib.Path(directory) / "store-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store App\n"
                "Exec=sh -c 'exec /opt/store-app/run'\n",
                encoding="utf-8",
            )

            result = installer.PackageInstaller(
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

    def test_install_ignores_a_hidden_broken_launcher_for_readiness(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "hidden-app.deb"
            desktop = pathlib.Path(directory) / "hidden-app.desktop"
            package.write_bytes(b"local package")
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nNoDisplay=true\n"
                "Name=Hidden App\nExec=/opt/hidden-app/missing\n",
                encoding="utf-8",
            )

            result = installer.PackageInstaller(
                runner=successful_install_runner(package, "hidden-app", (desktop,)),
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual("installed", result["state"])
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
            apt_install = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "install", str(package),
            )
            fix_dependencies = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "-f", "install",
            )
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                metadata: (0, "sample-app\n1.2.3\namd64\n", ""),
                apt_install: [(100, "", "unmet dependencies"), (0, "", "")],
                fix_dependencies: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).install(package)

        self.assertTrue(result["ok"])
        self.assertEqual("installed", result["state"])
        self.assertTrue(result["launch_ready"])
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
            runner = FakeRunner({metadata: (0, "sample-app\n1.2.3\namd64\n", "")})

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
            apt_install = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "install", str(package),
            )
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            runner = FakeRunner({
                metadata: (0, "sample-app\n1.2.3\namd64\n", ""),
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


class PackageInstallerRepairTests(unittest.TestCase):
    def test_repair_reinstalls_package_and_refreshes_desktop_entries(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            reinstall = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "--reinstall",
                "install", "sample-app",
            )
            verify = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "sample-app")
            list_files = ("dpkg-query", "-L", "sample-app")
            refresh_desktops = ("update-desktop-database", "/usr/share/applications")
            refresh_icons = ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")
            runner = FakeRunner({
                reinstall: (0, "", ""),
                verify: (0, "ii ", ""),
                refresh_desktops: (0, "", ""),
                refresh_icons: (0, "", ""),
            })

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).repair("sample-app")

        self.assertTrue(result["ok"])
        self.assertEqual("repaired", result["state"])
        self.assertTrue(result["launch_ready"])
        self.assertEqual("sample-app", result["package"])
        self.assertEqual(
            [reinstall, verify, refresh_desktops, refresh_icons, list_files], runner.commands)

    def test_repair_keeps_an_installed_package_but_reports_a_broken_visible_launcher(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "sample-app.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Sample App\n"
                "Exec=/opt/sample-app/missing\n",
                encoding="utf-8",
            )
            reinstall = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "--reinstall",
                "install", "sample-app",
            )
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

            result = installer.PackageInstaller(
                runner=runner,
                uid_getter=lambda: 0,
                log_path=pathlib.Path(directory) / "installer.log",
            ).repair("sample-app")

        self.assertTrue(result["ok"])
        self.assertFalse(result["launch_ready"])
        self.assertEqual("repaired_with_launch_problem", result["state"])
        self.assertIn("无法启动", result["error"])


class PackageInstallerLauncherTests(unittest.TestCase):
    def test_common_loader_finds_library_copy_for_the_installed_sbin_layout(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as directory:
            prefix = pathlib.Path(directory) / "usr" / "local"
            program = prefix / "sbin" / "ming-package-installer"
            common = prefix / "lib" / "ming-os" / "ming-shell-common.py"
            program.parent.mkdir(parents=True)
            common.parent.mkdir(parents=True)
            program.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            common.write_text("loaded_from_installed_library = True\n", encoding="utf-8")

            loaded = installer._load_common(program_path=program, install_prefix=prefix)

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.loaded_from_installed_library)

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
                runner=FakeRunner({metadata: (0, "sample-app\n1.2.3\namd64\n", "")}),
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
