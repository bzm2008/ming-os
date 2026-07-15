import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = (ROOT / "assets" / "ming-settings-backend.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")


class PerformanceSettingsContracts(unittest.TestCase):
    def test_performance_policy_keys_are_typed_and_ui_is_json_backed(self):
        for key in ("interaction_policy", "background_throttle", "disk_prefetch"):
            self.assertIn('"%s"' % key, BACKEND)
        for marker in ("性能策略", "自适应性能", "后台应用节流", "HDD 应用预读", "ming-performance-policy"):
            self.assertIn(marker, SETTINGS)


if __name__ == "__main__":
    unittest.main()
