import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")
SUPERVISOR = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


class InteractionLaunchContracts(unittest.TestCase):
    def test_launch_boost_is_async_and_supervisor_tracks_active_windows(self):
        for marker in (
            "INTERACTION_BOOST",
            "_request_interaction_boost",
            "start_new_session=True",
            "_process_starttime",
        ):
            self.assertIn(marker, LAUNCH)
        for marker in (
            "ming-resource-supervisor()",
            "_NET_ACTIVE_WINDOW",
            "ming-background-policy apply",
            "hidden_since",
            "now - hidden_since >= 10",
        ):
            self.assertIn(marker, SUPERVISOR)


if __name__ == "__main__":
    unittest.main()
