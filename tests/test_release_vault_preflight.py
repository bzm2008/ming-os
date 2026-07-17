import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ming-release-vault.py"
FIXTURE_RECEIPT = ROOT / "tests" / "fixtures" / "release-vault" / "good-receipt.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("ming_release_vault_preflight", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseVaultPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def make_fixture(self):
        temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(temp.name)
        public = root / "public"
        public.mkdir()
        bundle = root / "recovery-bundle-1.age"
        bundle.write_bytes(b"verified bundle")
        sidecar = root / "recovery-bundle-1.sha256"
        bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
        sidecar.write_text(f"{bundle_hash}  {bundle.name}\n", encoding="ascii")
        keyring = public / "ming-ota-release-keyring.gpg"
        policy = public / "ming-ota-key-policy.json"
        keyring.write_bytes(b"public keyring")
        policy.write_text('{"schema":"ming.ota-key-policy.v1"}\n', encoding="utf-8")
        receipt = json.loads(FIXTURE_RECEIPT.read_text(encoding="utf-8"))
        receipt.update(
            {
                "bundle_sha256": bundle_hash,
                "bundle_bytes": bundle.stat().st_size,
                "public_keyring_sha256": hashlib.sha256(keyring.read_bytes()).hexdigest(),
                "key_policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
                "created_at": "2026-07-17T00:00:00Z",
            }
        )
        receipt_path = public / "release-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        config = {
            "public_root": str(public),
            "public_keyring": str(keyring),
            "policy": str(policy),
            "receipt": str(receipt_path),
            "bundle": str(bundle),
            "sidecar": str(sidecar),
            "nas_config": str(root / "nas.json"),
        }
        (root / "nas.json").write_text("{}", encoding="utf-8")
        return temp, root, config

    def test_preflight_refuses_missing_public_keyring(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        pathlib.Path(config["public_keyring"]).unlink()
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_preflight_refuses_policy_hash_mismatch(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        pathlib.Path(config["policy"]).write_text('{"schema":"wrong"}\n', encoding="utf-8")
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(caught.exception.error_code, "E_PUBLIC_TRUST_MISMATCH")

    def test_preflight_refuses_missing_local_receipt(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        pathlib.Path(config["receipt"]).unlink()
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_preflight_refuses_missing_nas_verification(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: (_ for _ in ()).throw(
                self.tool.ReleaseVaultError("E_VAULT_UNREACHABLE", "unreachable")
            ))
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_preflight_refuses_stale_receipt(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        receipt_path = pathlib.Path(config["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["created_at"] = "2020-01-01T00:00:00Z"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(caught.exception.error_code, "E_RECEIPT_STALE")

    def test_preflight_refuses_secret_scan_finding(self):
        holder, root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        (root / "public" / "private-key.txt").write_text("secret", encoding="utf-8")
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(caught.exception.error_code, "E_SECRET_EXPOSURE")

    def test_preflight_release_returns_sanitized_status(self):
        holder, _root, config = self.make_fixture()
        self.addCleanup(holder.cleanup)
        result = self.tool.preflight(config, nas_verifier=lambda _cfg: {"status": "ok"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "release")
        self.assertTrue(result["checks"]["nas"])


if __name__ == "__main__":
    unittest.main()
