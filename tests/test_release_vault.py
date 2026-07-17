import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ming-release-vault.py"
FIXTURES = ROOT / "tests" / "fixtures" / "release-vault"
GOOD_RECEIPT = FIXTURES / "good-receipt.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("ming_release_vault", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
    )


class ReleaseVaultReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.receipt = json.loads(GOOD_RECEIPT.read_text(encoding="utf-8"))

    def write_receipt(self, root, value):
        path = pathlib.Path(root) / "receipt.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_good_receipt_verifies_as_json(self):
        result = run_cli("verify-receipt", "--receipt", str(GOOD_RECEIPT))
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_receipt_rejects_unknown_fields_missing_hashes_and_non_hex_fingerprints(self):
        cases = []

        unknown = dict(self.receipt)
        unknown["unexpected"] = "not part of the public contract"
        cases.append(unknown)

        missing_hash = dict(self.receipt)
        del missing_hash["bundle_sha256"]
        cases.append(missing_hash)

        bad_fingerprint = dict(self.receipt)
        bad_fingerprint["primary_fingerprint"] = "not-a-fingerprint"
        cases.append(bad_fingerprint)

        for candidate in cases:
            with self.subTest(candidate=candidate):
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_receipt_requires_lowercase_sha256_and_uppercase_fingerprints(self):
        invalid = {
            "bundle_sha256": "A" * 64,
            "public_keyring_sha256": "b" * 63,
            "key_policy_sha256": "C" * 64,
            "primary_fingerprint": "a" * 40,
            "signing_fingerprint": "B" * 39,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                candidate = dict(self.receipt)
                candidate[field] = value
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_receipt_requires_generation_status_and_age_format(self):
        invalid = {
            "generation": 0,
            "status": "prepared",
            "encryption_format": "age-v2",
            "created_at": "not-a-timestamp",
            "bundle_bytes": -1,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                candidate = dict(self.receipt)
                candidate[field] = value
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_direct_validator_returns_a_sanitized_copy(self):
        validated = self.tool.validate_receipt(self.receipt)
        self.assertEqual(validated, self.receipt)
        self.assertIsNot(validated, self.receipt)


class ReleaseVaultPublicScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def make_public_tree(self, root):
        root = pathlib.Path(root)
        (root / "keyring").mkdir(parents=True)
        (root / "policy").mkdir()
        (root / "signatures").mkdir()
        (root / "hashes").mkdir()
        (root / "keyring" / "ming-ota-release-keyring.asc").write_text(
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\npublic material\n-----END PGP PUBLIC KEY BLOCK-----\n",
            encoding="utf-8",
        )
        (root / "policy" / "ming-ota-key-policy.json").write_text(
            '{"allowed_primary_fingerprints": ["%s"]}\n' % ("A" * 40),
            encoding="utf-8",
        )
        (root / "signatures" / "manifest.json.sig").write_text(
            "detached public signature\n", encoding="utf-8"
        )
        (root / "hashes" / "manifest.sha256").write_text(
            "%s  manifest.json\n" % ("a" * 64), encoding="utf-8"
        )
        shutil.copy2(FIXTURES / "good-receipt.json", root / "release-receipt.json")
        return root

    def test_public_scan_accepts_public_keyring_policy_signature_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            public = self.make_public_tree(pathlib.Path(temp) / "public")
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "ok")
        self.assertGreaterEqual(output["files_scanned"], 5)

    def test_public_scan_rejects_secret_key_dotenv_private_path_and_age_bundle(self):
        malicious = [
            "private-key-marker.txt",
            "dotenv-marker.txt",
            "age-bundle-marker.txt",
            "secret-marker.txt",
            "password-marker.txt",
            "token-marker.txt",
            "known-hosts-marker.txt",
            "private-path-marker.txt",
            "id_rsa",
            "id_ed25519",
        ]
        for name in malicious:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                public = pathlib.Path(temp) / "public"
                public.mkdir()
                shutil.copy2(FIXTURES / name, public / name)
                result = run_cli("scan-public", "--root", str(public))
            self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
            self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")

    def test_public_scan_rejects_sensitive_content_even_with_a_safe_basename(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            shutil.copy2(FIXTURES / "private-key-content.txt", public / "notes.txt")
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")


if __name__ == "__main__":
    unittest.main()
