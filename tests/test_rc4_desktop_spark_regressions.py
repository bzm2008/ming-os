import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class Rc4DesktopSparkRegressionTests(unittest.TestCase):
    def test_phone_desktop_uses_xdg_desktop_but_never_scans_it_as_app_catalog(self):
        """Desktop copies are presentation only; app discovery uses canonical roots."""
        self.assertIn("xdg-user-dir", PHONE)
        self.assertIn("def desktop_directory", PHONE)
        self.assertNotRegex(
            PHONE,
            r"APP_DIRS\s*=\s*\[\s*DESKTOP_DIR\s*,",
        )
        app_dirs = PHONE.split("APP_DIRS = [", 1)[1].split("]", 1)[0]
        self.assertNotIn("DESKTOP_DIR", app_dirs)
        self.assertIn("SYSTEM_APPLICATION_DIR", app_dirs)

    def test_desktop_migration_marks_generated_copies_and_preserves_unmanaged_files(self):
        """Only X-Ming-Managed launchers may be replaced during image finalization."""
        self.assertIn("X-Ming-Managed=true", FINALIZE)
        self.assertIn("X-Ming-Source-Desktop=", FINALIZE)
        marker_check = FINALIZE.split("is_managed_desktop_file() {", 1)[1].split(
            "\n}", 1
        )[0]
        reset = FINALIZE.split("reset_desktop_dir() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("X-Ming-Managed", marker_check)
        self.assertIn("is_managed_desktop_file", reset)
        self.assertIn("preserve", reset.lower())
        self.assertNotIn("-name '*.desktop' -delete", reset)

    def test_desktop_copies_resolve_to_canonical_system_entries(self):
        self.assertIn("/usr/share/applications/${launcher}", FINALIZE)
        self.assertIn("X-Ming-Source-Desktop", FINALIZE)
        self.assertIn("canonical", PHONE.lower())

    def test_old_spark_gui_and_launcher_are_not_emitted(self):
        combined = "\n".join((APPS, DESKTOP))
        self.assertNotIn("cat > /usr/local/bin/ming-package-install-gui", combined)
        self.assertNotIn("/usr/local/bin/ming-package-install-gui", combined)
        self.assertNotIn("cat > /usr/local/bin/ming-spark-store", combined)
        self.assertNotIn("/usr/local/bin/ming-spark-store", combined)
        self.assertNotIn("Exec=pkexec /usr/local/bin/ming-package-install-gui", combined)

    def test_local_deb_context_action_enters_ming_store(self):
        menu = DESKTOP.split("configure_simplified_menus() {", 1)[1]
        menu = menu.split("\n# ========================", 1)[0]
        self.assertIn("ming-store --local-deb", menu)
        self.assertNotIn("ming-package-install-gui", menu)

    def test_public_spark_keyring_is_isolated_from_client_paths(self):
        self.assertIn("spark-archive-keyring.gpg", DESKTOP)
        self.assertNotIn("/usr/local/bin/ming-spark-store", DESKTOP)
        self.assertNotIn("/usr/local/sbin/ming-spark-package-control", DESKTOP)

    def test_build_gate_rejects_retired_package_gui_and_history_entries(self):
        start = BUILD.index("for residue in [")
        residue = BUILD[start:BUILD.index("]:", start) + 2]
        for retired in (
            '"usr/local/bin/ming-package-install-gui"',
            '"usr/local/bin/ming-spark-store"',
            '"usr/share/applications/spark-store.desktop"',
        ):
            self.assertIn(retired, residue)
        self.assertIn("appfinder", BUILD)

    def test_spark_cleanup_does_not_delete_installed_wps_launcher(self):
        self.assertNotIn(
            "/usr/share/applications/wps-office.desktop", DESKTOP + FINALIZE
        )
        self.assertNotIn(
            "/usr/share/applications/wps-office.desktop", FINALIZE
        )
        self.assertIn("ming-install-wps.desktop", FINALIZE)

    def test_finalize_removes_legacy_appfinder_commands_without_broad_home_delete(self):
        cleanup = FINALIZE.split("retire_legacy_store_runtime() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("ming-spark-store", cleanup)
        self.assertIn("ming-package-install-gui", cleanup)
        self.assertIn("xfce4-appfinder", cleanup)
        self.assertIn("sed -i", cleanup)
        self.assertNotRegex(cleanup, r"find\s+\"?\$\{USER_HOME\}\"?.*-delete")
        self.assertNotRegex(cleanup, r"find\s+/home\s+/etc/skel.*-delete")
        self.assertIn("X-Ming-Managed", cleanup)


if __name__ == "__main__":
    unittest.main()
