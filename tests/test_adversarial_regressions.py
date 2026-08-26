import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = (ROOT / "assets" / "ming-toolbox.py").read_text(encoding="utf-8")
SPARK_WINE = (ROOT / "assets" / "ming-spark-wine-package.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


class AdversarialRegressionContracts(unittest.TestCase):
    def test_lab_switches_must_have_a_real_confirmation_path(self):
        self.assertIn("confirm", TOOLBOX.lower())
        self.assertIn("取消", TOOLBOX)

    def test_spark_manifest_validates_identity_and_dependencies(self):
        self.assertIn("app_id", SPARK_WINE)
        self.assertIn("launch_file", SPARK_WINE)
        self.assertIn("dependencies", SPARK_WINE)
        self.assertIn("re.fullmatch", SPARK_WINE)

    def test_wine_launch_and_uninstall_expose_non_success_states(self):
        self.assertIn("launch_failed", TOOLBOX)
        self.assertIn("残留", TOOLBOX)

    def test_spark_refresh_warning_is_not_silent(self):
        self.assertIn("installed_with_refresh_warning", APPS)
        self.assertIn("桌面刷新", APPS)

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
