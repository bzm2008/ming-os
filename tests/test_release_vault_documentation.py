import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "releases" / "ming-release-vault-operations.md"
GITIGNORE = ROOT / ".gitignore"


class ReleaseVaultDocumentationTests(unittest.TestCase):
    def test_operations_document_covers_public_boundary_and_recovery(self):
        text = DOC.read_text(encoding="utf-8")
        for marker in (
            "ming.sca-hub.cn",
            "encrypted recovery bundles are not uploaded to GitHub",
            "reverse SSH",
            "known_hosts",
            "monthly",
            "quarterly",
            "E_VAULT_UNREACHABLE",
            "E_VAULT_HASH_MISMATCH",
            "E_SECRET_EXPOSURE",
            "freeze OTA",
        ):
            self.assertIn(marker, text)

    def test_gitignore_covers_only_local_private_artifacts(self):
        text = GITIGNORE.read_text(encoding="utf-8")
        for marker in (".age", "MING_RELEASE_VAULT", "gpg-home", "private-receipt"):
            self.assertIn(marker, text)
        self.assertNotIn("docs/releases/*.json", text)
        self.assertNotIn("assets/trust", text)


if __name__ == "__main__":
    unittest.main()
