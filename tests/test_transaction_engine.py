import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "assets" / "ming-transaction-engine.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_transaction_engine", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionEngineTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ENGINE_PATH.exists(), "transaction engine entrypoint is not implemented")
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-engine-")
        self.root = pathlib.Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.artifact = self.root / "artifact"
        self.artifact.write_bytes(b"fixture")

    def tearDown(self):
        self.tmp.cleanup()

    def arguments(self):
        return {
            "manifest_path": self.artifact,
            "manifest_signature": self.artifact,
            "index_path": self.artifact,
            "index_signature": self.artifact,
            "payload_path": self.artifact,
            "payload_signature": self.artifact,
            "keyring": self.artifact,
            "current_version": "26.3.2",
            "architecture": "amd64",
            "kernel_release": "6.12.0-amd64",
            "bootstrap_version": "1.0.0",
            "active_root": self.root / "active",
            "state_root": self.state_root,
            "transaction_id": "tx-001",
            "available_bytes": 1024 * 1024,
        }

    def test_signature_failure_prevents_all_state_and_candidate_writes(self):
        calls = []

        def verifier(**kwargs):
            calls.append("verify")
            raise self.module.EngineError("E_MANIFEST_SIGNATURE", "bad signature")

        def applicator(**kwargs):
            calls.append("apply")

        with self.assertRaises(self.module.EngineError) as caught:
            self.module.stage_release(
                **self.arguments(),
                verifier=verifier,
                applicator=applicator,
            )
        self.assertEqual(caught.exception.code, "E_MANIFEST_SIGNATURE")
        self.assertEqual(calls, ["verify"])
        self.assertFalse(self.state_root.exists())

    def test_verified_plan_is_the_only_value_passed_to_the_applicator(self):
        calls = []
        plan = {
            "release_id": "ming-os-26.3.3-amd64-1",
            "verified_artifacts": {"payload_sha256": "a" * 64},
        }

        def verifier(**kwargs):
            calls.append(("verify", kwargs))
            return plan

        def applicator(**kwargs):
            calls.append(("apply", kwargs))
            return {"state": "staged"}

        result = self.module.stage_release(
            **self.arguments(),
            verifier=verifier,
            applicator=applicator,
        )
        self.assertEqual(result["state"], "staged")
        self.assertEqual([call[0] for call in calls], ["verify", "apply"])
        self.assertIs(calls[1][1]["plan"], plan)
        self.assertNotIn("manifest_path", calls[1][1])
        self.assertNotIn("keyring", calls[1][1])

    def test_cli_has_fixed_trust_and_state_paths_and_structured_errors(self):
        source = ENGINE_PATH.read_text(encoding="utf-8")
        self.assertIn("/usr/share/ming-update/trust/release-keyring.gpg", source)
        self.assertIn("/var/lib/ming-update", source)
        self.assertNotIn("--keyring", source)
        self.assertNotIn("--state-root", source)
        self.assertIn('"error_code"', source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
