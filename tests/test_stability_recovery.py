"""Regression contracts for Ming OS graphics, session and device recovery."""

import pathlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import importlib.util


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


def load_display_control():
    path = ROOT / "assets" / "ming-display-control.py"
    spec = importlib.util.spec_from_file_location("ming_display_control_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


XRANDR_INITIAL = """Screen 0: minimum 8 x 8, current 1920 x 1080, maximum 32767 x 32767
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 309mm x 174mm
   1920x1080     60.00*+  59.93
   1280x720      60.00
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""


XRANDR_CHANGED = """Screen 0: minimum 8 x 8, current 1280 x 720, maximum 32767 x 32767
eDP-1 connected primary 1280x720+0+0 (normal left inverted right x axis y axis) 309mm x 174mm
   1920x1080     60.00  59.93
   1280x720      60.00*+
HDMI-1 disconnected (normal left inverted right x axis y axis)
"""


class DisplayControlPureTests(unittest.TestCase):
    def setUp(self):
        self.display = load_display_control()

    def test_mode_label_has_pixels_and_hz_not_a_scaling_factor(self):
        self.assertEqual(
            "1920 × 1080 · 60 Hz",
            self.display.mode_label("1920x1080", "60.00"),
        )
        self.assertEqual(
            "1920 × 1080 · 59.94 Hz",
            self.display.mode_label("1920x1080", "59.940"),
        )

    def test_parse_snapshot_keeps_only_connected_output_mode_rate_pairs(self):
        snapshot = self.display.parse_xrandr_snapshot(XRANDR_INITIAL)
        output = snapshot["outputs"][0]
        self.assertEqual("eDP-1", output["name"])
        self.assertTrue(output["connected"])
        self.assertEqual("1920x1080", output["mode"])
        self.assertEqual("60.00", output["rate"])
        self.assertTrue(
            self.display.request_is_supported(snapshot, "eDP-1", "1280x720", "60.00", "normal")
        )
        self.assertFalse(
            self.display.request_is_supported(snapshot, "HDMI-1", "1280x720", "60.00", "normal")
        )
        self.assertFalse(
            self.display.request_is_supported(snapshot, "eDP-1", "1024x768", "60.00", "normal")
        )

    def test_status_keeps_schema_when_xrandr_is_unavailable(self):
        def runner(argv):
            return subprocess.CompletedProcess(argv, 127, "", "xrandr: not found")

        output = io.StringIO()
        exit_code = self.display.main(
            ["status", "--json"],
            controller=self.display.DisplayController(runner=runner),
            stdout=output,
        )
        status = json.loads(output.getvalue())

        self.assertEqual(2, exit_code)
        self.assertFalse(status["ok"])
        self.assertEqual([], status["outputs"])
        self.assertEqual(self.display.CONFIRM_SECONDS, status["confirm_seconds"])
        self.assertIn("xrandr", status["error"])

    def test_unconfirmed_display_change_restores_the_snapshot(self):
        calls = []
        query_results = iter([XRANDR_INITIAL])

        def runner(argv):
            calls.append(argv)
            if argv == ["xrandr", "--query"]:
                return subprocess.CompletedProcess(argv, 0, next(query_results), "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            control = self.display.DisplayController(
                runner=runner,
                state_dir=pathlib.Path(temporary),
                timer_factory=lambda _token: {"pid": 42},
            )
            result = control.apply("eDP-1", "1280x720", "60.00", "normal")
            self.assertTrue(result["ok"])
            restored = control.rollback(result["token"])
            self.assertTrue(restored["ok"])

        self.assertIn(
            ["xrandr", "--output", "eDP-1", "--mode", "1280x720", "--rate", "60.00", "--rotate", "normal"],
            calls,
        )
        self.assertIn(
            ["xrandr", "--output", "eDP-1", "--mode", "1920x1080", "--rate", "60.00", "--rotate", "normal"],
            calls,
        )

    def test_apply_restores_snapshot_when_the_rollback_timer_cannot_start(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv == ["xrandr", "--query"]:
                return subprocess.CompletedProcess(argv, 0, XRANDR_INITIAL, "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            control = self.display.DisplayController(
                runner=runner,
                state_dir=pathlib.Path(temporary),
                timer_factory=lambda _token: {},
            )
            result = control.apply("eDP-1", "1280x720", "60.00", "normal")
            self.assertFalse(result["ok"])
            self.assertTrue(result["restored"])

        self.assertIn(
            ["xrandr", "--output", "eDP-1", "--mode", "1920x1080", "--rate", "60.00", "--rotate", "normal"],
            calls,
        )

    def test_confirm_requires_current_readback_before_cancelling_timer(self):
        calls = []
        query_results = iter([XRANDR_INITIAL, XRANDR_INITIAL, XRANDR_CHANGED])
        cancelled = []

        def runner(argv):
            calls.append(argv)
            if argv == ["xrandr", "--query"]:
                return subprocess.CompletedProcess(argv, 0, next(query_results), "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            control = self.display.DisplayController(
                runner=runner,
                state_dir=pathlib.Path(temporary),
                timer_factory=lambda token: {"pid": 42, "token": token},
                timer_canceller=lambda timer: cancelled.append(timer),
            )
            staged = control.apply("eDP-1", "1280x720", "60.00", "normal")
            self.assertTrue(staged["ok"])
            mismatch = control.confirm(staged["token"])
            self.assertFalse(mismatch["ok"])
            self.assertEqual([], cancelled)
            confirmed = control.confirm(staged["token"])
            self.assertTrue(confirmed["ok"])
            self.assertEqual(1, len(cancelled))


class StatusWidgetStatePureTests(unittest.TestCase):
    def test_widget_state_is_limited_to_collapsed_and_recovers_from_corruption(self):
        prefix = PHONE.split("\nimport gi\n", 1)[0]
        self.assertIn("def load_widget_state", prefix)
        self.assertIn("def save_widget_state", prefix)
        namespace = {"__file__": str(ROOT / "assets" / "ming-phone-desktop.py")}
        exec(prefix, namespace)
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / "status-widget.json"
            self.assertEqual({"collapsed": False}, namespace["load_widget_state"](state_path))
            state_path.write_text("not json", encoding="utf-8")
            self.assertEqual({"collapsed": False}, namespace["load_widget_state"](state_path))
            namespace["save_widget_state"](True, state_path)
            self.assertEqual({"collapsed": True}, json.loads(state_path.read_text(encoding="utf-8")))
            self.assertEqual({"collapsed": True}, namespace["load_widget_state"](state_path))


class StabilityRecoveryContracts(unittest.TestCase):
    @staticmethod
    def generated_script(start_marker, end_marker):
        return DESKTOP.split(start_marker, 1)[1].split(end_marker, 1)[0].lstrip("\n")

    @staticmethod
    def write_executable(path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def test_intel_xorg_never_forces_legacy_ddx_or_sna(self):
        self.assertNotIn('Driver      "intel"', BASE)
        self.assertNotIn('Option      "AccelMethod" "sna"', BASE)

    def test_xfce_desktop_does_not_require_legacy_intel_ddx(self):
        installer = APPS.split("install_xfce_desktop() {", 1)[1].split(
            "\n# ========================", 1)[0]
        self.assertNotIn("xserver-xorg-video-intel", installer)
        self.assertIn("xserver-xorg-video-modesetting", BASE)

    def test_intel_xorg_migration_recognizes_prior_signature_and_runs_before_display_manager(self):
        for marker in (
            'Identifier[[:space:]]+"Intel Graphics"',
            'Driver[[:space:]]+"intel"',
            '"AccelMethod"[[:space:]]+"sna"',
            '"TripleBuffer"[[:space:]]+"true"',
            "ming-intel-xorg-migration.service",
            "Before=display-manager.service",
        ):
            self.assertIn(marker, BASE)
        self.assertNotIn("cat > /etc/systemd/system/ming-intel-xorg.service", BASE)
        self.assertIn("ming-intel-xorg-migration.service", BUILD)
        self.assertIn("id -nG", BUILD)
        self.assertNotIn("runuser -u", BUILD)

    def test_earlyoom_does_not_prioritize_wps_for_termination(self):
        self.assertNotIn("|wps|", BASE)

    def test_window_manager_health_is_observable_and_repairable_without_killing_apps(self):
        for marker in (
            "ming-window-control",
            "_NET_SUPPORTING_WM_CHECK",
            "_NET_CLOSE_WINDOW",
            '"window_manager"',
            "xfwm4 --replace",
        ):
            self.assertIn(marker, DESKTOP)
        self.assertNotIn("pkill -x wps", DESKTOP)
        self.assertNotIn("pkill -x quark", DESKTOP)

    def test_window_control_validates_ids_and_uses_bounded_ewmh_operations(self):
        helper = DESKTOP.split(
            "cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'", 1)[-1]
        for marker in (
            "status --json",
            "focus|maximize|restore|close",
            "^0[xX][0-9a-fA-F]+$",
            "_NET_ACTIVE_WINDOW",
            "_NET_CLOSE_WINDOW",
            "timeout --foreground",
            "xfwm4 --replace",
        ):
            self.assertIn(marker, helper)

    def test_persistent_x11_health_paths_share_timeout_and_validate_discovered_ids(self):
        control = self.generated_script(
            "cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'",
            "MINGWINDOWCONTROL")
        health = self.generated_script(
            "cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'",
            "MINGDESKHEALTH")
        plank = self.generated_script(
            "cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'",
            "PLANKWATCH")
        for script in (control, health, plank):
            self.assertIn("x11_call()", script)
            self.assertIn("timeout --foreground 2s", script)
        for script in (control, health):
            self.assertIn("x11_id_is_valid()", script)
        self.assertIn("valid_window_id()", plank)
        self.assertIn('[[ "${candidate_id}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue', health)
        self.assertIn('[[ "${candidate_id}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue', plank)

    def test_persistent_screen_geometry_uses_the_bounded_x11_helper(self):
        health = self.generated_script(
            "cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'",
            "MINGDESKHEALTH")
        plank = self.generated_script(
            "cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'",
            "PLANKWATCH")
        health_screen = health.split("screen_geometry() {", 1)[1].split("\n}", 1)[0]
        plank_screen = plank.split("screen_geometry() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("x11_call xrandr --current", health_screen)
        self.assertIn("x11_call xdpyinfo", health_screen)
        self.assertIn("x11_call xrandr --current", plank_screen)

    def test_generated_window_helpers_emit_parseable_json_with_stubbed_x11(self):
        control = self.generated_script(
            "cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'",
            "MINGWINDOWCONTROL")
        health = self.generated_script(
            "cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'",
            "MINGDESKHEALTH")
        self.assertIn("json.dumps", control)
        self.assertIn("json.dumps", health)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            self.write_executable(fake_bin / "timeout", "#!/usr/bin/env bash\nshift; shift; exec \"$@\"\n")
            self.write_executable(fake_bin / "pgrep", "#!/usr/bin/env bash\nexit 0\n")
            self.write_executable(fake_bin / "wmctrl", """#!/usr/bin/env bash
case \"${1:-}\" in
  -m) printf 'Name: Xfwm4\\n' ;;
  -lx) printf '0x01200001 0 host.desktop Ming Desktop\\n0x01200002 0 plank Plank\\n' ;;
  -lGx) printf '0x01200001 0 0 0 1280 720 host.desktop Ming Desktop\\n0x01200002 0 0 680 1280 40 plank Plank\\n' ;;
esac
""")
            self.write_executable(fake_bin / "xprop", """#!/usr/bin/env bash
if [[ \"${1:-}\" == -root ]]; then
  printf '_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x01200001\\n_NET_ACTIVE_WINDOW(WINDOW): window id # 0x01200001\\n'
else
  printf '_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_DOCK\\n_NET_WM_STATE(ATOM) = _NET_WM_STATE_ABOVE\\n'
fi
""")
            self.write_executable(fake_bin / "xrandr", "#!/usr/bin/env bash\nprintf 'Screen 0: minimum 1 x 1, current 1280 x 720, maximum 1280 x 720\\n'\n")
            control_path = root / "ming-window-control"
            health_path = root / "ming-desktop-healthcheck"
            path_prefix = "" if os.name == "nt" else "export PATH='%s':\"$PATH\"\n" % fake_bin.as_posix()
            self.write_executable(control_path, path_prefix + control)
            self.write_executable(
                health_path,
                path_prefix + health.replace(
                    "/usr/local/bin/ming-window-control", control_path.as_posix()),
            )
            if os.name == "nt":
                self.skipTest("runtime helper execution is validated by the Linux rootfs gate")
            environment = dict(os.environ)
            environment["HOME"] = str(root / "home")
            for command in (["bash", str(control_path), "status", "--json"],
                            ["bash", str(health_path), "--json"]):
                result = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr)
                payload = json.loads(result.stdout)
                self.assertIsInstance(payload, dict)
                healthy = payload["healthy"] if "healthy" in payload else payload["window_manager"]["healthy"]
                self.assertIsInstance(healthy, bool)

    def test_window_health_rejects_an_ewmh_owner_other_than_xfwm4(self):
        helper = DESKTOP.split(
            "cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'", 1)[-1]
        for marker in (
            'wm_name_matches=false',
            '[[ "${wm_name,,}" == "xfwm4" ]]',
            '"matches_ewmh"',
            '"mismatch"',
            '&& ${wm_name_matches}',
        ):
            self.assertIn(marker, helper)

    def test_window_watchdog_requires_three_failures_and_deploys_session_autostart(self):
        for marker in (
            "ming-window-manager-watchdog",
            "failure_count >= 3",
            "sleep 10",
            "window-manager.log",
            "ming-window-manager.desktop",
            "ming-window-control repair",
        ):
            self.assertIn(marker, DESKTOP)

    def test_main_and_lowmem_picom_profiles_keep_windows_redirected(self):
        main = DESKTOP.split("cat > /home/${MING_USER}/.config/picom/picom.conf << 'PICOMCFG'", 1)[1].split("PICOMCFG", 1)[0]
        lowmem = DESKTOP.split("cat > /etc/xdg/picom/picom-lowmem.conf << 'PICOMLOWMEM'", 1)[1].split("PICOMLOWMEM", 1)[0]
        self.assertIn("unredir-if-possible = false;", main)
        self.assertIn("unredir-if-possible = false;", lowmem)

    def test_rootfs_gate_requires_redirected_main_and_lowmem_picom_profiles(self):
        self.assertIn('"home/user/.config/picom/picom.conf"', BUILD)
        self.assertIn('"etc/xdg/picom/picom-lowmem.conf"', BUILD)
        self.assertIn('"unredir-if-possible = false;"', BUILD)

    def test_rootfs_gate_json_parses_window_helpers_without_an_x_server(self):
        for marker in (
            "ming-window-control status --json",
            "ming-desktop-healthcheck --json",
            "import json,sys; value=json.load(sys.stdin)",
            '"window_manager"',
        ):
            self.assertIn(marker, BUILD)

    def test_network_events_trigger_bounded_ntp_resynchronization(self):
        for marker in (
            "/etc/NetworkManager/dispatcher.d/90-ming-time-sync",
            "dhcp4-change",
            "connectivity-change",
            'timedatectl "$@"',
            "NTPSynchronized",
            "/var/log/ming-time-sync.log",
        ):
            self.assertIn(marker, BASE)

    def test_time_sync_helper_is_locked_offline_safe_and_bounded(self):
        helper = BASE.partition(
            "cat > /usr/local/sbin/ming-time-sync << 'MINGTIMESYNC'")[2].partition(
                "MINGTIMESYNC")[0]
        for marker in (
            "flock",
            "nm-online -q -t 12",
            "timedatectl set-ntp true",
            "systemctl restart systemd-timesyncd",
            'timedatectl "$@"',
            "show -p NTPSynchronized --value",
            "deadline=$((SECONDS + 45))",
            "/var/log/ming-time-sync.log",
            "status --json",
        ):
            self.assertIn(marker, helper)
        offline_guard = helper.split("nm-online -q -t 12", 1)[1].split(
            "systemctl restart systemd-timesyncd", 1)[0]
        self.assertIn("return 0", offline_guard)

    def test_time_sync_dispatcher_is_event_limited_and_nonblocking(self):
        dispatcher = BASE.partition(
            "cat > /etc/NetworkManager/dispatcher.d/90-ming-time-sync << 'MINGTIMEDISPATCH'")[2].partition(
                "MINGTIMEDISPATCH")[0]
        for marker in ("up|dhcp4-change|dhcp6-change|connectivity-change", "nohup", "&"):
            self.assertIn(marker, dispatcher)
        self.assertIn("exit 0", dispatcher)

    def test_no_networkmanager_wait_online_override_gates_graphical_boot(self):
        self.assertIn("systemctl disable --now NetworkManager-wait-online.service", BASE)
        self.assertNotIn("ExecStart=/usr/bin/nm-online -s -q -t 60", BASE)
        self.assertNotIn("ExecStart=/usr/bin/nm-online -s -q --timeout=5", BASE)

    def test_settings_exposes_nonblocking_time_sync_status_and_retry(self):
        for marker in (
            "def time_sync_snapshot",
            'TIME_SYNC_HELPER = "/usr/local/sbin/ming-time-sync"',
            '"status", "--json"',
            "已自动校时",
            "等待网络校时",
            "校时服务异常",
            "on_time_sync_retry",
            "ming-time-sync",
            '"sync"',
            "run_capture_async",
        ):
            self.assertIn(marker, SETTINGS)

    def test_time_sync_retry_timeout_covers_the_bounded_helper_budget(self):
        retry = SETTINGS.split("def on_time_sync_retry", 1)[1].split(
            "def on_wifi_toggle", 1)[0]
        match = re.search(r"run_capture_async\(.+?timeout=(\d+)", retry, re.DOTALL)
        self.assertIsNotNone(match, "time-sync retry must use an explicit background timeout")
        self.assertGreaterEqual(
            int(match.group(1)), 80,
            "retry timeout must cover 12s network wait, two service operations and 45s poll")

    def test_time_sync_poll_caps_each_probe_and_sleep_at_the_deadline(self):
        helper = BASE.partition(
            "cat > /usr/local/sbin/ming-time-sync << 'MINGTIMESYNC'")[2].partition(
                "MINGTIMESYNC")[0]
        poll = helper.split("deadline=$((SECONDS + 45))", 1)[1].split(
            'log "NTP is still waiting after 45 seconds', 1)[0]
        for marker in ("probe_timeout", "sleep_for", "remaining=$((deadline - SECONDS))"):
            self.assertIn(marker, poll)
        self.assertNotIn("sleep 3", poll)

    def test_display_control_is_a_ming_helper_with_confirmed_rollback(self):
        helper = ROOT / "assets" / "ming-display-control.py"
        self.assertTrue(helper.is_file())
        source = helper.read_text(encoding="utf-8")
        for marker in ("status", "apply", "confirm", "rollback", "15"):
            self.assertIn(marker, source)
        self.assertIn("ming-display-control", SETTINGS)
        self.assertIn("100% 标准", SETTINGS)
        self.assertIn("1920 × 1080", SETTINGS)

    def test_status_widget_compact_state_is_persistent_and_uses_a_revealer(self):
        for marker in (
            "status-widget.json",
            "Gtk.Revealer",
            "collapsed",
            "收起",
            "展开",
        ):
            self.assertIn(marker, PHONE)

    def test_rootfs_gate_requires_recovery_helpers_and_modesetting(self):
        for marker in (
            "ming-window-control",
            "ming-time-sync",
            "ming-display-control",
            "xserver-xorg-video-modesetting",
        ):
            self.assertIn(marker, BUILD)

    def test_legacy_xfce_display_command_is_redirected_to_ming_settings(self):
        self.assertIn('xfce4-display-settings.real', DESKTOP)
        self.assertIn('exec /usr/local/bin/ming-control-center --page display "$@"', DESKTOP)
        self.assertIn('usr/bin/xfce4-display-settings', BUILD)


if __name__ == "__main__":
    unittest.main()
