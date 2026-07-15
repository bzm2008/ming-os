import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "assets" / "ming-performance-policy.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


def load_policy():
    spec = importlib.util.spec_from_file_location("ming_performance_policy", POLICY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResourcePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_policy()

    def test_lease_expiry_restores_process_policy(self):
        state = self.module.LeaseState()
        token = state.begin(pid=42, starttime="100", now=10.0, duration=1.5)
        self.assertEqual(token, state.begin(pid=42, starttime="100", now=10.5, duration=1.5))
        self.assertEqual(1, len(state.leases))
        self.assertTrue(state.active(token, now=10.5))
        self.assertFalse(state.active(token, now=12.0))
        self.assertEqual([token], state.reap(now=12.0))

    def test_protected_processes_never_enter_background_policy(self):
        for name in ("ming-phone-desktop", "picom", "fcitx5", "pulseaudio", "NetworkManager", "ming-update"):
            self.assertTrue(self.module.is_protected_process(name))

    def test_policy_cli_and_deployment_contract_exist(self):
        for marker in (
            "ming-interaction-boost",
            "ming-background-policy",
            "SO_PEERCRED",
            "starttime",
            "CPUWeight",
            "timer_slack_ns",
            "cgroup v2",
        ):
            self.assertIn(marker, self.module.SOURCE)
        self.assertIn("ming-performance-policy.py", BASE)
        self.assertIn("ming-resource-policy.service", BASE)
        self.assertIn("ming-resource-supervisor", DESKTOP)
        self.assertIn("resource-policy.jsonl", self.module.SOURCE)
        self.assertIn("structured", self.module.SOURCE.lower())

    def test_governor_boost_requires_ac_power_and_thermal_headroom(self):
        """A foreground lease must not force performance on battery or hot CPUs."""
        safe_power = {"ac_online": True, "battery_present": True}
        cool_thermal = {"available": True, "critical_margin_c": 25.0}
        hot_thermal = {"available": True, "critical_margin_c": 3.0}
        self.assertTrue(self.module.governor_boost_allowed(safe_power, cool_thermal)[0])
        self.assertFalse(
            self.module.governor_boost_allowed(
                {"ac_online": False, "battery_present": True}, cool_thermal
            )[0]
        )
        self.assertFalse(self.module.governor_boost_allowed(safe_power, hot_thermal)[0])

    def test_background_policy_accepts_only_trusted_desktop_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            trusted = root / "trusted.desktop"
            trusted.write_text(
                "[Desktop Entry]\nType=Application\nName=Trusted\nExec=/usr/bin/trusted\n",
                encoding="utf-8",
            )
            trusted.chmod(0o644)
            with mock.patch.object(self.module, "TRUSTED_DESKTOP_ROOTS", (root,)):
                self.assertTrue(self.module.desktop_file_is_trusted(str(trusted)))
                self.assertFalse(
                    self.module.desktop_file_is_trusted(str(root / "missing.desktop"))
                )
                outside = root.parent / "outside.desktop"
                outside.write_text(
                    "[Desktop Entry]\nType=Application\nName=Outside\nExec=/usr/bin/x\n",
                    encoding="utf-8",
                )
                outside.chmod(0o644)
                self.assertFalse(self.module.desktop_file_is_trusted(str(outside)))

    def test_cgroup_restore_is_limited_to_the_session_slice(self):
        self.assertTrue(
            self.module.cgroup_path_is_safe(
                "/user.slice/user-1000.slice/app.slice", 1000
            )
        )
        self.assertFalse(self.module.cgroup_path_is_safe("/system.slice", 1000))
        self.assertFalse(self.module.cgroup_path_is_safe("/user.slice/user-2000.slice/app", 1000))

    def test_background_throttle_exempts_explicit_and_active_audio_processes(self):
        with mock.patch.object(
            self.module, "_read",
            side_effect=lambda path, default="": "MING_NO_BACKGROUND_THROTTLE=1\0"
            if "environ" in str(path) else default,
        ):
            self.assertEqual((True, "explicit"), self.module.background_throttle_exemption(123))
        with mock.patch.object(self.module, "_read", return_value=""), mock.patch.object(
            self.module, "_run",
            return_value=(0, 'application.process.id = "123"\nCorked: no', ""),
        ):
            self.assertEqual((True, "active-audio"), self.module.background_throttle_exemption(123))


if __name__ == "__main__":
    unittest.main()
