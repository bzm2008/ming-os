import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "tests" / "fixtures" / "transaction_fault_matrix.json"


class TransactionFaultMatrixTests(unittest.TestCase):
    def test_matrix_runs_each_frozen_failure_contract(self):
        self.assertTrue(MATRIX_PATH.is_file(), "transaction fault matrix fixture is missing")
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(matrix.get("schema"), "ming.transaction-fault-matrix.v1")
        cases = matrix.get("cases")
        self.assertIsInstance(cases, list)
        self.assertGreaterEqual(len(cases), 9)
        identifiers = set()
        for case in cases:
            with self.subTest(case=case.get("id")):
                self.assertIsInstance(case.get("id"), str)
                self.assertNotIn(case["id"], identifiers)
                identifiers.add(case["id"])
                self.assertRegex(case.get("error_code", ""), r"^(E_[A-Z_]+|recovery-guard)$")
                suite = unittest.defaultTestLoader.loadTestsFromName(case.get("test_id", ""))
                result = unittest.TestResult()
                suite.run(result)
                self.assertEqual(result.testsRun, 1, case["test_id"])
                self.assertFalse(result.failures, result.failures)
                self.assertFalse(result.errors, result.errors)


if __name__ == "__main__":
    unittest.main()
