import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
APPIMAGE = (ROOT / "assets" / "ming-appimage-installer.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
PACKAGE = (ROOT / "assets" / "ming-package-installer.py").read_text(encoding="utf-8")
GIT_BASH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")


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

    def test_spark_wrapper_does_not_accept_a_short_lived_process_as_ready(self):
        self.assertTrue(GIT_BASH.is_file(), "Git Bash is required for wrapper regression")
        marker = "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'\n"
        wrapper = APPS.split(marker, 1)[1].split("\nMINGSPARK\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="ming-spark-wrapper-") as directory:
            root = pathlib.Path(directory)
            fake = root / "spark-store"
            fake.write_text("#!/usr/bin/env bash\nsleep 0.2\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            wrapper = wrapper.replace(
                "for candidate in /usr/bin/spark-store /opt/spark-store/bin/spark-store; do",
                'for candidate in "${MING_TEST_SPARK_BIN}"; do',
            )
            script = root / "ming-spark-store"
            script.write_text(wrapper, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), str(script).replace("\\", "/")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20,
                env={
                    **os.environ,
                    "HOME": str(root),
                    "MING_TEST_SPARK_BIN": str(fake).replace("\\", "/"),
                },
            )
        self.assertNotEqual(0, result.returncode)

    def test_package_gui_explains_refresh_warning_after_successful_install(self):
        self.assertIn("installed_with_refresh_warning", DESKTOP)
        self.assertIn("桌面刷新失败", DESKTOP)
        self.assertIn("刷新/重试", DESKTOP)


if __name__ == "__main__":
    unittest.main()
