import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


class OfflineUpdateContracts(unittest.TestCase):
    def test_update_cli_installs_bundle_checker_and_exposes_offline_commands(self):
        self.assertIn("deploy_ota_bundle_engine", OTA)
        self.assertIn("offline-scan)", OTA)
        self.assertIn("offline-stage)", OTA)
        self.assertIn("offline_bundle_verify_signature", OTA)
        self.assertIn("ming-ota-ab-stage --iso", OTA)

    def test_settings_scans_in_background_and_uses_polkit_for_staging(self):
        self.assertIn("run_task_async(offline_bundle_candidates", SETTINGS)
        self.assertIn('"offline-scan", "--json"', SETTINGS)
        self.assertIn('"pkexec", "ming-update", "offline-stage", "--json"', SETTINGS)
        self.assertIn("在线更新不可用时", SETTINGS)

    def test_removable_media_notifies_only_after_bundle_verifies(self):
        notify = BASE.split("notify_update_bundle() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("ming-update offline-scan", notify)
        self.assertIn("--action=open=", notify)
        self.assertIn("ming-control-center --page update", notify)

    def test_installer_presents_whole_disk_with_explicit_data_loss_warning(self):
        self.assertIn("使用整块磁盘（自动配置系统与恢复布局）", DESKTOP)
        self.assertIn("会清除所选磁盘", DESKTOP)


if __name__ == "__main__":
    unittest.main()
