import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DIAGNOSTICS_PATH = ROOT / "assets" / "ming-transaction-diagnostics.py"


class TransactionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DIAGNOSTICS_PATH.is_file(), "transaction diagnostics command is not implemented")
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-diagnostics-")
        self.root = pathlib.Path(self.tmp.name)
        self.transaction = self.root / "transactions" / "tx-001"
        self.transaction.mkdir(parents=True)
        (self.root / "current.json").write_text(
            json.dumps({"schema": "ming.current-slot.v1", "slot": "A", "transaction_id": "tx-000"}),
            encoding="utf-8",
        )
        (self.root / "active-transaction.json").write_text(
            json.dumps({"schema": "ming.active-transaction.v1", "transaction_id": "tx-001", "state": "rollback_armed"}),
            encoding="utf-8",
        )
        (self.transaction / "state.json").write_text(
            json.dumps(
                {
                    "schema": "ming.transaction-state.v1",
                    "transaction_id": "tx-001",
                    "release_id": "ming-os-26.3.3-amd64-1",
                    "state": "rollback_armed",
                    "generation": 7,
                    "previous_slot": "legacy",
                    "candidate_slot": "A",
                }
            ),
            encoding="utf-8",
        )
        (self.transaction / "events.jsonl").write_text(
            "\n".join(
                (
                    json.dumps({"generation": 6, "to_state": "pending_health"}),
                    json.dumps({"generation": 7, "to_state": "rollback_armed"}),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (self.transaction / "failure.json").write_text(
            json.dumps(
                {
                    "error_code": "E_HEALTH_PACKAGES",
                    "reason": "dpkg audit failed",
                    "password": "must-not-export",
                    "token": "must-not-export",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, *arguments):
        return subprocess.run(
            [sys.executable, str(DIAGNOSTICS_PATH), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_status_reports_frozen_state_and_failure_without_sensitive_values(self):
        result = self.run_command("status", "--state-root", str(self.root), "--transaction", "tx-001")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["schema"], "ming.transaction-diagnostics.v1")
        self.assertTrue(value["ok"])
        self.assertEqual(value["transaction"]["state"], "rollback_armed")
        self.assertEqual(value["events"]["count"], 2)
        self.assertEqual(value["failure"]["error_code"], "E_HEALTH_PACKAGES")
        self.assertNotIn("must-not-export", result.stdout)

    def test_export_creates_a_redacted_json_bundle(self):
        output = self.root / "export.json"
        result = self.run_command(
            "export",
            "--state-root",
            str(self.root),
            "--transaction",
            "tx-001",
            "--output",
            str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["transaction"]["id"], "tx-001")
        self.assertEqual(exported["failure"]["password"], "[REDACTED]")
        self.assertEqual(exported["failure"]["token"], "[REDACTED]")
        self.assertNotIn("must-not-export", output.read_text(encoding="utf-8"))

    def test_rejects_traversal_and_reports_malformed_logs(self):
        traversal = self.run_command("status", "--state-root", str(self.root), "--transaction", "../tx-001")
        self.assertNotEqual(traversal.returncode, 0)
        self.assertEqual(json.loads(traversal.stdout)["error_code"], "E_DIAGNOSTIC_ARGUMENT")

        (self.transaction / "events.jsonl").write_text("not-json\n", encoding="utf-8")
        malformed = self.run_command("status", "--state-root", str(self.root), "--transaction", "tx-001")
        self.assertNotEqual(malformed.returncode, 0)
        self.assertEqual(json.loads(malformed.stdout)["error_code"], "E_DIAGNOSTIC_LOG")


if __name__ == "__main__":
    unittest.main()
