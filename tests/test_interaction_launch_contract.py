import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")
SUPERVISOR = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
MONITOR = (ROOT / "assets" / "ming-window-resource-monitor.py").read_text(encoding="utf-8")


class InteractionLaunchContracts(unittest.TestCase):
    def test_launch_boost_is_async_and_monitor_tracks_window_events(self):
        for marker in (
            "INTERACTION_BOOST",
            "_request_interaction_boost",
            "start_new_session=True",
            "_process_starttime",
        ):
            self.assertIn(marker, LAUNCH)
        self.assertIn("start_resource_monitor", SUPERVISOR)
        for marker in (
            "active-window-changed",
            "window-opened",
            "state-changed",
            "ming-background-policy",
            "HIDDEN_DELAY_MS = 10_000",
        ):
            self.assertIn(marker, MONITOR)
        self.assertNotIn("xprop", MONITOR)
        self.assertNotIn("wmctrl", MONITOR)


if __name__ == "__main__":
    unittest.main()
