import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


class EdgeVideoContracts(unittest.TestCase):
    def test_edge_always_uses_x11_and_avoids_unstable_forced_features(self):
        self.assertIn("edge_args=(--ozone-platform=x11)", APPS)
        self.assertNotIn("--use-gl=egl", APPS)
        self.assertNotIn("UseMultiPlaneFormatForHardwareVideo", APPS)

    def test_edge_graphics_helper_selects_active_render_node_and_real_decode_gate(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge-graphics")
        end = APPS.index("\nMINGEDGEGRAPHICS", start)
        helper = APPS[start:end]
        self.assertIn("renderD*", helper)
        self.assertNotIn("renderD128", helper)
        self.assertIn("ffmpeg", helper)
        self.assertIn("test-video", helper)
        self.assertIn("set-mode", helper)
        self.assertIn("compat", helper)

    def test_edge_enables_gpu_only_after_structured_hardware_validation(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge << 'MINGEDGE'")
        end = APPS.index("\nMINGEDGE", start + len("cat > /usr/local/bin/ming-edge << 'MINGEDGE'"))
        wrapper = APPS[start:end]
        self.assertIn("ming-hardware-status status --json", wrapper)
        self.assertIn("ming-edge-graphics test-video", wrapper)
        self.assertIn("--disable-gpu", wrapper)
        self.assertIn("nomodeset", wrapper)
