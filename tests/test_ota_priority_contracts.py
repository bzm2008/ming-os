import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class OTAPriorityContracts(unittest.TestCase):
    def test_ota_slice_is_low_priority_without_memory_cap(self):
        for marker in (
            "ming-ota.slice",
            "CPUWeight=20",
            "IOWeight=20",
            "Nice=10",
            "IOSchedulingClass=idle",
            "Do not set MemoryMax",
        ):
            self.assertIn(marker, OTA)
        self.assertNotIn("MemoryMax=", OTA)

    def test_interaction_policy_can_yield_ota_for_a_bounded_window(self):
        for marker in ("ming-ota.slice", "yield", "1000ms", "CPUWeight=1"):
            self.assertIn(marker, BASE + OTA)

    def test_ota_engine_files_are_not_rewritten_by_priority_wrapper(self):
        self.assertIn("ming-ota-run", OTA)
        self.assertIn("exec /usr/local/bin/ming-update", OTA)
        self.assertNotIn("ming-transaction-engine.py <<", OTA)
        self.assertNotIn("ming-transaction-verify.py <<", OTA)

    def test_ota_priority_wrappers_fail_closed_when_generation_is_empty(self):
        for marker in (
            '[[ -s /usr/local/bin/ming-ota-run ]]',
            '[[ -s /usr/local/bin/ming-ota-yield ]]',
        ):
            self.assertIn(marker, OTA)

    def test_build_gate_checks_policy_assets(self):
        for marker in ("ming-performance-policy.py", "ming-ota.slice", "ming-ota-run", "ming-ota-yield"):
            self.assertIn(marker, BUILD + BASE + OTA)
        self.assertIn('validate_generated_executable("usr/local/bin/ming-ota-run", "bash")', BUILD)
        self.assertIn('validate_generated_executable("usr/local/bin/ming-ota-yield", "bash")', BUILD)


if __name__ == "__main__":
    unittest.main()
