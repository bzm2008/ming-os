import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDE = ROOT / "EXECUTION_GUIDE.md"
APP_INSTALLER = ROOT / "chroot_install_apps.sh"


class CurrentReleaseDocumentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide = GUIDE.read_text(encoding="utf-8")
        cls.installer = APP_INSTALLER.read_text(encoding="utf-8")

    def test_guide_identifies_rc4_source_and_build_entry(self):
        self.assertIn("Ming OS 26.4.1 RC4", self.guide)
        self.assertIn("a5b9295", self.guide)
        self.assertIn("./build_onion_os.sh", self.guide)
        self.assertIn("BUILD_SOURCE_COMMIT", self.guide)

    def test_guide_marks_2632_as_historical_only(self):
        history = self.guide.lower()
        self.assertIn("26.3.2", history)
        self.assertIn("历史", self.guide)
        self.assertIn("historical", history)
        self.assertNotIn("Current recommended release: Ming OS 26.3.2", self.guide)

    def test_guide_separates_build_from_install_and_ota_acceptance(self):
        for marker in ("构建入口", "安装版验收", "Live 启动", "安装后首次启动", "OTA"):
            self.assertIn(marker, self.guide)
        self.assertIn("Live 启动或构建成功都不能替代安装版验收", self.guide)

    def test_app_installer_refuses_direct_download_and_install(self):
        for forbidden in ("wget", "curl", "apt install", "qq.deb", "listen1.deb"):
            self.assertNotIn(forbidden, self.installer)
        self.assertIn("拒绝执行", self.installer)
        self.assertIn("Ming Store", self.installer)
        self.assertIn("本地受控安装", self.installer)

if __name__ == "__main__":
    unittest.main()
