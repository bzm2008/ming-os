import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"


class CompositorStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = DESKTOP.read_text(encoding="utf-8")
        cls.coordinator = source.split(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1
        )[1].split("MINGSESSIONHEALTH", 1)[0]

    def test_dock_immersive_tick_is_idempotent_when_window_state_matches(self):
        self.assertIn("dock_state_matches", self.coordinator)
        body = re.search(
            r"apply_dock_immersive_state\(\) \{(.*?)\n\}",
            self.coordinator,
            re.S,
        ).group(1)
        self.assertRegex(body, r"same_window[\s\S]*dock_state_matches")
        self.assertIn("return 0", body)

    def test_session_does_not_start_a_second_compositor_path(self):
        autostart = DESKTOP.read_text(encoding="utf-8").split(
            "configure_autostart() {", 1
        )[1].split("# ======================== 统一会话启动/健康协调器", 1)[0]
        self.assertIn("X-GNOME-Autostart-enabled=false", autostart)
        self.assertIn("Exec=/usr/bin/true", autostart)
        self.assertIn("ming-session-healthcheck", self.coordinator)


if __name__ == "__main__":
    unittest.main()
