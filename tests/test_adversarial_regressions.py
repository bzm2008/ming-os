import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = (ROOT / "assets" / "ming-toolbox.py").read_text(encoding="utf-8")
STORE_CORE = (ROOT / "assets" / "ming-store-core.py").read_text(encoding="utf-8")
STORE_CONTROL = (ROOT / "assets" / "ming-store-control.py").read_text(encoding="utf-8")
STORE_UI = (ROOT / "assets" / "ming-store.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


class AdversarialRegressionContracts(unittest.TestCase):
    def test_lab_switches_must_have_a_real_confirmation_path(self):
        self.assertIn("confirm", TOOLBOX.lower())
        self.assertIn("取消", TOOLBOX)

    def test_store_catalog_validates_identity_dependencies_and_architecture(self):
        for marker in (
            '"app_id"', '"package_name"', '"architectures"',
            '"dependencies"', '"identity"', "SAFE_ID.fullmatch",
        ):
            self.assertIn(marker, STORE_CORE)
        self.assertIn('identity_type != "apt-repository-signature"', STORE_CORE)
        self.assertIn('identity_type != "sha256"', STORE_CORE)

    def test_wine_launch_and_uninstall_expose_non_success_states(self):
        self.assertIn("launch_failed", TOOLBOX)
        self.assertIn("残留", TOOLBOX)

    def test_store_refresh_warning_is_not_silent_or_reported_as_failure(self):
        self.assertIn('"state": "refresh_warning"', STORE_CONTROL)
        self.assertIn("软件操作已完成，但桌面入口刷新失败", STORE_CONTROL)
        self.assertIn('"retry_action": "refresh"', STORE_UI)
        self.assertIn("软件操作完成，但桌面入口刷新失败", STORE_UI)

    def test_retired_spark_runtime_is_not_reintroduced_by_apps_module(self):
        for marker in (
            "ming-spark-store", "ming-spark-package-control",
            "ming-spark-backend-status", "ming-spark-aria2c",
        ):
            self.assertNotIn(marker, APPS)

    def test_android_runtime_never_uses_shell_eval_or_network_adb(self):
        android = (ROOT / "assets" / "ming-android-runtime.py")
        if not android.exists():
            self.fail("Android runtime must be added before implementation is complete")
        source = android.read_text(encoding="utf-8")
        self.assertNotIn("eval ", source)
        self.assertNotIn("sh -c", source)
        self.assertNotIn("adb tcpip", source)


if __name__ == "__main__":
    unittest.main()
