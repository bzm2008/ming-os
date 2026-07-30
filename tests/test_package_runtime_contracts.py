import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
APPIMAGE = (ROOT / "assets" / "ming-appimage-installer.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
PACKAGE = (ROOT / "assets" / "ming-package-installer.py").read_text(encoding="utf-8")


class PackageRuntimeContracts(unittest.TestCase):
    def test_desktop_catalog_scans_verified_opt_app_proxies(self):
        self.assertIn('Path("/usr/local/share/applications")', PHONE)

    def test_appimage_launcher_is_readable_by_desktop_catalog(self):
        self.assertIn("os.chmod(desktop, 0o644)", APPIMAGE)

    def test_thunar_accepts_both_appimage_filename_cases(self):
        self.assertIn("<patterns>*.AppImage;*.appimage</patterns>", DESKTOP)

    def test_spark_desktop_copy_uses_the_shared_launch_broker(self):
        self.assertIn(
            "Exec=/usr/local/bin/ming-launch --desktop-file /usr/share/applications/spark-store.desktop --source desktop",
            APPS,
        )

    def test_package_install_uses_the_shared_desktop_refresh_hook(self):
        self.assertIn("ming-refresh-desktop-state", PACKAGE)
        self.assertIn("ming-refresh-desktop-state", APPS)

    def test_privileged_desktop_refresh_drops_to_user_without_literal_patch_tokens(self):
        refresh = DESKTOP.split(
            "cat > /usr/local/bin/ming-refresh-desktop-state << 'MINGREFRESHDESKTOP'",
            1,
        )[1].split("MINGREFRESHDESKTOP", 1)[0]
        self.assertIn("runuser -u \"${target_user}\" -- env \\", refresh)
        self.assertNotIn("env +", refresh)


if __name__ == "__main__":
    unittest.main()
