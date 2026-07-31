import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
COMMON_PATH = ROOT / "assets" / "ming-shell-common.py"
APPIMAGE_PATH = ROOT / "assets" / "ming-appimage-installer.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FirefoxDefaultContracts(unittest.TestCase):
    def test_firefox_esr_is_the_only_ming_browser_runtime_and_default_launcher(self):
        self.assertIn("install_firefox_esr()", APPS)
        self.assertIn("        firefox-esr \\", APPS)
        self.assertIn("/usr/local/bin/ming-firefox", APPS)
        self.assertIn("x-scheme-handler/https=ming-firefox.desktop", APPS)
        self.assertNotIn("packages.microsoft.com/repos/edge", APPS)
        self.assertIn("ming-firefox.dockitem", DESKTOP)
        self.assertNotIn("ming-edge.dockitem", DESKTOP)
        self.assertIn("ming-firefox.desktop", BUILD)
        self.assertIn("usr/bin/firefox-esr", BUILD)
        self.assertNotIn("microsoft-edge-stable", BUILD)


class AppImageInstallerContracts(unittest.TestCase):
    def test_user_appimage_install_creates_a_user_owned_desktop_launcher(self):
        installer = load_module("ming_appimage_installer", APPIMAGE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "Example.AppImage"
            # A minimal x86_64 type-2 AppImage header is enough for validation;
            # it is never executed by this unit test.
            source.write_bytes(
                b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 5
                + b"\x02\x00\x3e\x00"
            )
            home = root / "home"
            result = installer.AppImageInstaller(home=home, uid_getter=lambda: 1000).install(source)

            self.assertTrue(result["ok"], result)
            target = pathlib.Path(result["installed_file"])
            launcher = pathlib.Path(result["desktop_file"])
            self.assertTrue(target.is_file())
            if __import__("os").name != "nt":
                self.assertTrue(target.stat().st_mode & 0o100)
            self.assertTrue(launcher.is_file())
            desktop = launcher.read_text(encoding="utf-8")
            self.assertIn("Exec=/usr/local/bin/ming-appimage-run ", desktop)
            self.assertIn(target.name, desktop)
            self.assertIn("Icon=application-x-executable", desktop)

    def test_appimage_validation_reads_a_fixed_header_and_requires_type2_magic(self):
        source = APPIMAGE_PATH.read_text(encoding="utf-8")
        self.assertIn("APPIMAGE_MAGIC = b\"AI\\x02\"", source)
        self.assertIn("handle.read(APPIMAGE_HEADER_SIZE)", source)
        self.assertNotIn("resolved.read_bytes()", source)

    def test_appimage_installer_rejects_an_elf_without_the_appimage_magic(self):
        installer = load_module("ming_appimage_installer_magic", APPIMAGE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "NotAnAppImage.AppImage"
            source.write_bytes(
                b"\x7fELF\x02\x01\x01\x00BAD" + b"\x00" * 5
                + b"\x02\x00\x3e\x00"
            )
            result = installer.AppImageInstaller(
                home=root / "home", uid_getter=lambda: 1000
            ).install(source)
            self.assertFalse(result["ok"])
            self.assertEqual("validation_failed", result["state"])
            self.assertIn("AppImage", result["error"])

    def test_appimage_install_reports_extract_and_run_fallback_without_fuse(self):
        installer = load_module("ming_appimage_installer_fallback", APPIMAGE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "Fallback.AppImage"
            source.write_bytes(
                b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 5
                + b"\x02\x00\x3e\x00"
            )
            result = installer.AppImageInstaller(
                home=root / "home", uid_getter=lambda: 1000
            ).install(source)
            self.assertIn(result["run_mode"], {"direct", "extract-and-run"})
            if not result["fuse_ready"]:
                self.assertEqual("extract-and-run", result["run_mode"])

    def test_appimage_installer_refuses_root_and_wrong_architecture_without_copying(self):
        installer = load_module("ming_appimage_installer_root", APPIMAGE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "Bad.AppImage"
            source.write_bytes(b"not an executable")
            home = root / "home"
            result = installer.AppImageInstaller(home=home, uid_getter=lambda: 0).install(source)

            self.assertFalse(result["ok"])
            self.assertEqual("permission_denied", result["state"])
            self.assertFalse((home / ".local" / "opt" / "appimages").exists())


class SharedIconResolverContracts(unittest.TestCase):
    def test_resolves_absolute_and_desktop_relative_icon_paths_before_theme_fallback(self):
        common = load_module("ming_shell_common_icons", COMMON_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop_dir = root / "applications"
            desktop_dir.mkdir()
            desktop = desktop_dir / "sample.desktop"
            desktop.write_text("[Desktop Entry]\nName=Sample\n", encoding="utf-8")
            absolute = root / "absolute.png"
            absolute.write_bytes(b"png")
            relative = desktop_dir / "icons" / "sample.svg"
            relative.parent.mkdir()
            relative.write_text("svg", encoding="utf-8")

            self.assertEqual(str(absolute), common.resolve_icon_path(str(absolute), desktop))
            self.assertEqual(str(relative), common.resolve_icon_path("icons/sample.svg", desktop))
            self.assertIsNone(common.resolve_icon_path("network-workgroup", desktop))


if __name__ == "__main__":
    unittest.main()
