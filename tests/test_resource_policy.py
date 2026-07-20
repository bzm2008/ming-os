import importlib.util
import pathlib
import tempfile
import threading
import time
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

    def test_policy_daemon_uses_one_independent_timer_expiry_path(self):
        source = self.module.SOURCE
        self.assertIn("threading.Timer(delay, self._expire_lease", source)
        self.assertNotIn("for token in policy.leases.reap()", source)

    def test_protected_processes_never_enter_background_policy(self):
        for name in ("ming-phone-desktop", "picom", "fcitx5", "pulseaudio", "NetworkManager", "ming-update"):
            self.assertTrue(self.module.is_protected_process(name))

    def test_python_wrapped_ming_processes_are_protected_by_cmdline(self):
        def read_proc(path, default=""):
            value = str(path)
            if value.endswith("/comm"):
                return "python3"
            if value.endswith("/cmdline"):
                return "/usr/bin/python3\0/usr/local/bin/ming-phone-desktop\0"
            return default

        with mock.patch.object(self.module, "_read", side_effect=read_proc):
            self.assertTrue(self.module.process_is_protected(123))

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
        self.assertIn("ming-window-resource-monitor", DESKTOP)
        self.assertNotIn("ming-resource-supervisor()", DESKTOP)
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

    def test_visible_background_request_without_snapshot_is_a_noop(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        with mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "desktop_file_is_trusted", return_value=True), \
                mock.patch.object(self.module, "_move_pid") as move_pid, \
                mock.patch.object(self.module, "_restore_ionice") as restore_ionice:
            result = policy.apply_background(
                123, "100", "/usr/share/applications/app.desktop", True)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual("not-backgrounded", result.get("reason"))
        move_pid.assert_not_called()
        restore_ionice.assert_not_called()

    def test_opt_in_background_policy_only_changes_cgroup_weight(self):
        source = self.module.SOURCE
        start = source.index("    def apply_background(")
        end = source.index("    def status(", start)
        background = source[start:end]
        self.assertNotIn("_apply_nice(pid, 10)", background)
        self.assertNotIn("_apply_ionice(pid, idle=True)", background)
        self.assertNotIn("_set_timer_slack(pid, 50_000_000)", background)
        self.assertIn('"ming-background.slice"', background)

    def test_begin_restores_expired_snapshot_before_new_lease(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        old_token = policy.leases.begin(123, "100", now=0.0, duration=0.1)
        policy.snapshots[old_token] = {
            "pid": 123, "starttime": "100", "nice": 0, "ionice": None,
            "timer_slack": 50_000, "cgroup_path": None,
            "governors": {}, "governor_gate": {}, "cgroup": False,
        }
        with mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "desktop_file_is_trusted", return_value=True), \
                mock.patch.object(self.module, "process_starttime", return_value="100"), \
                mock.patch.object(self.module.time, "monotonic", return_value=1.0), \
                mock.patch.object(self.module, "_ionice_snapshot", return_value=None), \
                mock.patch.object(self.module, "_timer_slack", return_value=50_000), \
                mock.patch.object(self.module, "_cgroup_relative_path", return_value=None), \
                mock.patch.object(self.module, "_apply_nice", return_value=(True, "nice")), \
                mock.patch.object(self.module, "_apply_ionice", return_value=True), \
                mock.patch.object(self.module, "_move_pid", return_value=False), \
                mock.patch.object(self.module, "_governor_snapshot", return_value={}), \
                mock.patch.object(self.module, "_restore_governors") as restore_governors:
            result = policy.begin(123, "100", "activate")
        self.assertTrue(result["ok"])
        self.assertNotIn(old_token, policy.snapshots)
        self.assertEqual(1, len(policy.snapshots))
        restore_governors.assert_called_once_with({})

    def test_background_snapshot_is_scoped_to_pid_and_starttime(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        with mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "desktop_file_is_trusted", return_value=True), \
                mock.patch.object(policy, "_settings", return_value={"background_throttle": True}), \
                mock.patch.object(self.module, "background_throttle_exemption", return_value=(False, "")), \
                mock.patch.object(self.module, "_cgroup_relative_path", return_value="/user.slice/user-1000.slice/app.slice"), \
                mock.patch.object(self.module, "_move_pid", return_value=True), \
                mock.patch.object(self.module, "_restore_cgroup") as restore_cgroup:
            hidden = policy.apply_background(
                123, "100", "/usr/share/applications/app.desktop", False)
            visible = policy.apply_background(
                123, "200", "/usr/share/applications/app.desktop", True)
        self.assertTrue(hidden["ok"])
        self.assertTrue(visible.get("skipped"))
        self.assertEqual("not-backgrounded", visible.get("reason"))
        restore_cgroup.assert_not_called()

    def test_lease_timer_restores_without_waiting_for_socket_reaper(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        with mock.patch.object(self.module, "LEASE_SECONDS", 0.05), \
                mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "process_starttime", return_value="100"), \
                mock.patch.object(self.module, "_ionice_snapshot", return_value=None), \
                mock.patch.object(self.module, "_timer_slack", return_value=50_000), \
                mock.patch.object(self.module, "_cgroup_relative_path", return_value=None), \
                mock.patch.object(self.module, "_apply_nice", return_value=(True, "nice")), \
                mock.patch.object(self.module, "_apply_ionice", return_value=True), \
                mock.patch.object(self.module, "_move_pid", return_value=False), \
                mock.patch.object(self.module, "_governor_snapshot", return_value={}), \
                mock.patch.object(self.module, "_restore_governors") as restore_governors:
            result = policy.begin(123, "100", "activate")
            time.sleep(0.12)
        self.assertTrue(result["ok"])
        self.assertEqual({}, policy.snapshots)
        restore_governors.assert_called_once_with({})

    def test_near_simultaneous_lease_completion_is_idempotent(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        barrier = threading.Barrier(3)
        results = []

        def finish():
            barrier.wait()
            results.append(policy.end(token))

        with mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "process_starttime", return_value="100"), \
                mock.patch.object(self.module, "_ionice_snapshot", return_value=None), \
                mock.patch.object(self.module, "_timer_slack", return_value=50_000), \
                mock.patch.object(self.module, "_cgroup_relative_path", return_value=None), \
                mock.patch.object(self.module, "_apply_nice", return_value=(True, "nice")), \
                mock.patch.object(self.module, "_apply_ionice", return_value=True), \
                mock.patch.object(self.module, "_move_pid", return_value=False), \
                mock.patch.object(self.module, "_governor_snapshot", return_value=[]), \
                mock.patch.object(policy, "_schedule_lease_timer"), \
                mock.patch.object(self.module, "_policy_log") as policy_log, \
                mock.patch.object(self.module, "_restore_governors") as restore_governors:
            token = policy.begin(123, "100", "activate")["token"]
            workers = [threading.Thread(target=finish) for _ in range(2)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=2)

        self.assertEqual(2, len(results))
        self.assertTrue(all(result.get("ok") for result in results))
        self.assertEqual(1, sum(result.get("restored") is True for result in results))
        restore_governors.assert_called_once_with([])
        rejected = [
            call for call in policy_log.call_args_list
            if call.args and call.args[0] == "lease_rejected"
            and call.kwargs.get("reason") == "unknown-token"
        ]
        self.assertEqual([], rejected)

    def test_stale_timer_callback_does_not_expire_an_extended_lease(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        token = policy.leases.begin(123, "100", now=10.0, duration=1.5)
        policy.snapshots[token] = {
            "pid": 123, "starttime": "100", "nice": None, "ionice": None,
            "timer_slack": 50_000, "cgroup_path": None,
            "governor_gate": {}, "cgroup": False,
        }
        policy.governor_tokens.add(token)
        policy.governor_base_snapshot = []
        with mock.patch.object(self.module.time, "monotonic", return_value=10.5), \
                mock.patch.object(policy, "_schedule_lease_timer") as reschedule, \
                mock.patch.object(self.module, "_restore_governors") as restore_governors:
            result = policy._expire_lease(token)

        self.assertTrue(result["ok"])
        self.assertTrue(result.get("rescheduled"))
        self.assertIn(token, policy.snapshots)
        reschedule.assert_called_once_with(token)
        restore_governors.assert_not_called()

    def test_overlapping_pids_restore_the_original_governor_after_the_final_lease(self):
        policy = self.module.ResourcePolicy(session_uid=1000)
        governor_base = [(
            "/sys/devices/system/cpu/cpufreq/policy0/scaling_governor",
            "powersave",
        )]
        with mock.patch.object(self.module, "_validate_pid", return_value=(True, "")), \
                mock.patch.object(self.module, "_ionice_snapshot", return_value=None), \
                mock.patch.object(self.module, "_timer_slack", return_value=50_000), \
                mock.patch.object(self.module, "_cgroup_relative_path", return_value=None), \
                mock.patch.object(self.module, "_apply_nice", return_value=(True, "nice")), \
                mock.patch.object(self.module, "_apply_ionice", return_value=True), \
                mock.patch.object(self.module, "_move_pid", return_value=False), \
                mock.patch.object(self.module, "_governor_snapshot", return_value=governor_base) as snapshot, \
                mock.patch.object(self.module, "_restore_governors") as restore_governors, \
                mock.patch.object(policy, "_schedule_lease_timer"):
            first = policy.begin(123, "100", "activate")
            second = policy.begin(456, "200", "launch")
            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(1, snapshot.call_count)

            policy.end(first["token"])
            restore_governors.assert_not_called()
            policy.end(second["token"])

        restore_governors.assert_called_once_with(governor_base)


if __name__ == "__main__":
    unittest.main()
