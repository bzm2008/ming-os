import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class SparkResidueContracts(unittest.TestCase):
    def test_wps_legacy_pkexec_launcher_is_not_deployed(self):
        """WPS must be installed through Ming Store, never a root desktop hook."""
        self.assertNotIn("cat > /usr/local/bin/ming-install-wps", APPS)
        self.assertNotIn("Exec=pkexec /usr/local/bin/ming-install-wps", APPS)
        self.assertNotIn("run_optional_step install_wps_office", APPS)

    def test_runtime_surfaces_do_not_leave_vendor_store_helpers(self):
        combined = "\n".join((APPS, DESKTOP))
        for marker in (
            "ming-spark-package-control",
            "ming-spark-backend-status",
            "ming-spark-aria2c",
            "MINGSPARKPASSAUTH",
            "aptss",
            "store.spark-app",
        ):
            self.assertNotIn(marker, combined)

    def test_build_gate_checks_wps_and_vendor_residue_paths(self):
        for marker in (
            "Spark/APM residue",
            "usr/local/bin/ming-install-wps",
            "usr/share/applications/ming-install-wps.desktop",
            "etc/apt/preferences.d/90-ming-spark-store",
        ):
            self.assertIn(marker, BUILD)

    def test_finalize_removes_legacy_wps_entry_without_touching_user_apps(self):
        cleanup = FINALIZE.split("retire_legacy_store_runtime() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("ming-install-wps.desktop", cleanup)
        # Keep the explicit migration contract while ensuring no destructive
        # rm/find command targets the managed application data directory.
        self.assertIn("do not touch /opt/apps", cleanup.lower())
        self.assertNotRegex(cleanup, r"(?m)^[ \t]*(?:rm|find)\\b[^\n]*/opt/apps")


if __name__ == "__main__":
    unittest.main()
