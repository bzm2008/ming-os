import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


class EdgeVideoContracts(unittest.TestCase):
    def test_edge_enables_gpu_only_after_structured_hardware_validation(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge << 'MINGEDGE'")
        end = APPS.index("\nMINGEDGE", start + len("cat > /usr/local/bin/ming-edge << 'MINGEDGE'"))
        wrapper = APPS[start:end]
        self.assertIn("ming-hardware-status status --json", wrapper)
        self.assertIn('"edge_hardware_video": true', wrapper)
        self.assertIn("--disable-gpu", wrapper)
        self.assertIn("nomodeset", wrapper)
