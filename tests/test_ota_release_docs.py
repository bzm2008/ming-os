import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASE_NOTES = ROOT / "docs" / "releases" / "26.3.3-release-notes.md"
RELEASE_COPY = ROOT / "docs" / "releases" / "26.3.3-release-copy.md"
BOOTSTRAP = ROOT / "docs" / "bootstrap" / "26.3.2-transactional-ota.md"


class OtaReleaseDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notes = RELEASE_NOTES.read_text(encoding="utf-8")
        cls.copy = RELEASE_COPY.read_text(encoding="utf-8")
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")

    def test_release_notes_identify_single_update_entry_and_transaction_truth(self):
        self.assertIn("Ming OS 26.3.3", self.notes)
        self.assertIn("检查更新", self.notes)
        self.assertIn("立即更新", self.notes)
        self.assertIn("已下载并完成校验，等待重启确认", self.notes)
        self.assertIn("已自动回滚", self.notes)
        self.assertIn("绕过", self.notes)

    def test_release_copy_has_website_and_github_sections_without_fake_artifacts(self):
        self.assertIn("官网说明", self.copy)
        self.assertIn("GitHub Release", self.copy)
        self.assertIn("签名校验", self.copy)
        self.assertIn("26.3.2", self.copy)
        self.assertNotIn("example.invalid", self.copy)

    def test_bootstrap_page_requires_real_checksum_signature_and_fingerprint(self):
        for marker in ("下载地址", "SHA256", "签名文件", "公钥指纹", "sha256sum -c", "gpgv"):
            self.assertIn(marker, self.bootstrap)
        self.assertIn("发布负责人必须替换", self.bootstrap)
        self.assertIn("占位值不可用于安装", self.bootstrap)
        self.assertIn("不跳过签名", self.bootstrap)


if __name__ == "__main__":
    unittest.main()
