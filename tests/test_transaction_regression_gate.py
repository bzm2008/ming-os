import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "run-transaction-ota-regression.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "transaction-ota-regression.yml"
HEALTH_UNIT = ROOT / "assets" / "systemd" / "ming-transaction-health.service"
RECONCILE_UNIT = ROOT / "assets" / "systemd" / "ming-transaction-reconcile.service"


class TransactionRegressionGateTests(unittest.TestCase):
    def test_local_gate_runs_fault_matrix_recovery_guard_and_static_checks_without_building(self):
        self.assertTrue(SCRIPT.is_file(), "local transaction regression script is missing")
        script = SCRIPT.read_text(encoding="utf-8")
        for test_name in (
            "tests.test_transaction_fault_matrix",
            "tests.test_transaction_diagnostics",
            "tests.test_transaction_verify",
            "tests.test_transaction_boot",
            "tests.test_transaction_health_bootstrap",
            "tests.test_ota_target_guard",
            "tests.test_ota_backup",
        ):
            self.assertIn(test_name, script)
        self.assertIn("python3 -m py_compile", script)
        self.assertIn("bash -n", script)
        self.assertIn(
            'systemd-analyze verify --root="${verify_root}" "${verify_root}/etc/systemd/system/ming-transaction-health.service"',
            script,
        )
        self.assertIn('"${verify_root}/etc/systemd/system/ming-transaction-reconcile.service"', script)
        self.assertIn("sysinit.target", script)
        self.assertIn("local-fs.target", script)
        self.assertIn("display-manager.service", script)
        self.assertIn("ExecStart=/bin/true", script)
        self.assertIn("git rev-parse --show-toplevel", script)
        self.assertNotIn("build_onion_os", script)
        self.assertNotIn("resume_build", script)

    def test_ci_workflow_installs_regression_dependencies_and_invokes_local_gate(self):
        self.assertTrue(WORKFLOW.is_file(), "transaction regression workflow is missing")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rsync", workflow)
        self.assertIn("zstd", workflow)
        self.assertIn("tools/run-transaction-ota-regression.sh", workflow)

    def test_systemd_units_remain_bounded_and_do_not_restart_transactional_health_indefinitely(self):
        for unit in (HEALTH_UNIT, RECONCILE_UNIT):
            self.assertTrue(unit.is_file(), f"missing unit: {unit.name}")
            content = unit.read_text(encoding="utf-8")
            self.assertIn("Before=display-manager.service", content)
            self.assertRegex(content, r"TimeoutStartSec=(?:[1-9][0-9]?|[1-9][0-9]?s)")
            self.assertNotIn("Restart=always", content)


if __name__ == "__main__":
    unittest.main()
