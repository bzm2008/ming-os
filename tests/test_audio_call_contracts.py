import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "modules" / "02_apps.sh"
BASE = ROOT / "modules" / "01_base.sh"


class AudioCallBuildContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = APPS.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def test_required_runtime_includes_pulseaudio_call_dependencies(self):
        required_block = self.apps[
            self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=("):
            self.apps.index(")", self.apps.index("readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=(")) + 1]
        for package in ["libasound2-plugins", "pulseaudio-module-bluetooth", "pavucontrol"]:
            self.assertIn(package, required_block)

    def test_wechat_wrapper_repairs_audio_before_launch_without_memory_ceiling(self):
        start = self.apps.index("cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'")
        end = self.apps.index("\nWECHATWRAP", start + len("cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'"))
        wrapper = self.apps[start:end]
        self.assertIn("ming-device-control audio-repair-call", wrapper)
        self.assertNotIn("MemoryMax=", wrapper)
        self.assertNotIn("MemoryHigh=", wrapper)
        self.assertNotIn("nice -n", wrapper)
        self.assertNotIn("ionice", wrapper)

    def test_earlyoom_does_not_prefer_wechat_for_termination(self):
        self.assertNotIn("|wechat|", self.base)
