import importlib.util
import io
import json
import pathlib
import subprocess
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
        self.assertEqual("installed_with_launch_warning", result["state"])
        self.assertEqual(1, len(result["launcher_warnings"]))
        self.assertIn("找不到启动程序", result["launcher_warnings"][0]["error"])

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
        self.assertEqual("sample-app", result["package"])
        self.assertEqual(
            [reinstall, verify, refresh_desktops, refresh_icons, list_files], runner.commands)


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
