import os
import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BASH = (
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and pathlib.Path(r"C:\Program Files\Git\bin\bash.exe").is_file()
    else "bash"
)


class DockLifecycleContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESKTOP.read_text(encoding="utf-8")
        cls.plank_settings = cls.source.split(
            "cat > \"${plank_dir}/settings\" << 'PLANKSETTINGS'", 1
        )[1].split("PLANKSETTINGS", 1)[0]
        cls.watchdog = cls.source.split(
            "cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'", 1
        )[1].split("PLANKWATCH", 1)[0]
        cls.healthcheck = cls.source.split(
            "cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'", 1
        )[1].split("MINGDESKHEALTH", 1)[0]
        session_parts = cls.source.split(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1
        )
        cls.session_healthcheck = (
            session_parts[1].split("MINGSESSIONHEALTH", 1)[0]
            if len(session_parts) == 2
            else ""
        )
        cls.autostart = cls.source.split(
            "configure_autostart() {", 1
        )[1].split("# ======================== 首次启动欢迎引导", 1)[0]
        cls.oobe = cls.source.split(
            "cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'", 1
        )[1].split("OOBEACCOUNT", 1)[0]

    def test_plank_never_auto_hides(self):
        self.assertIn("HideMode=0", self.plank_settings)
        self.assertIn("PinOnly=false", self.plank_settings)
        self.assertIn("CurrentWorkspaceOnly=false", self.plank_settings)
        self.assertIn('ensure_plank_settings', self.watchdog)
        self.assertIn('^HideMode=', self.watchdog)

    def test_dock_runtime_dependencies_are_required(self):
        self.assertIn("bamfdaemon", APPS)
        self.assertIn("libbamf3-2t64", APPS)

    def test_watchdog_validates_window_type_stacking_and_geometry(self):
        for marker in (
            "plank_window_id",
            "_NET_WM_WINDOW_TYPE_DOCK",
            "_NET_WM_STATE_ABOVE",
            "screen_geometry",
            "window_geometry",
            "geometry_in_bounds",
            "position_is_bottom",
            "wmctrl -i -r",
        ):
            self.assertIn(marker, self.watchdog)

    def test_above_is_diagnostic_not_a_restart_condition(self):
        health = re.search(
            r"plank_health_reason\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertNotIn("_NET_WM_STATE_ABOVE", health)
        self.assertIn("diagnose_and_promote_stacking", self.watchdog)
        self.assertIn("ABOVE state is absent", self.watchdog)

    def test_above_promotion_is_attempted_once_per_plank_window(self):
        self.assertIn('stacking_promotion_attempted_for=""', self.watchdog)
        diagnostic = re.search(
            r"diagnose_and_promote_stacking\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertIn(
            '[[ "${stacking_promotion_attempted_for}" == "${window_id}" ]]',
            diagnostic,
        )
        self.assertIn('stacking_promotion_attempted_for="${window_id}"', diagnostic)

    def test_missing_xprop_accepts_a_visible_plank_window(self):
        selector = re.search(
            r"plank_window_id\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertIn("fallback_id", selector)
        self.assertIn("command -v xprop", selector)
        self.assertIn("geometry_in_bounds", selector)
        self.assertIn("position_is_bottom", selector)
        stacking = re.search(
            r"diagnose_and_promote_stacking\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertIn("command -v xprop", stacking)
        self.assertIn("return 0", stacking)

    def test_session_lock_uses_flock_not_a_directory(self):
        session = self.watchdog.split('--session)', 1)[1]
        self.assertIn("flock -n", session)
        self.assertIn("exec 9>", session)
        self.assertNotIn("mkdir \"${lock_dir}\"", session)

    def test_missing_settings_restore_a_complete_profile_and_launchers(self):
        settings = re.search(
            r"ensure_plank_settings\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertIn("/etc/skel/.config/plank/dock1/settings", settings)
        self.assertIn("write_default_plank_settings", self.watchdog)
        for marker in ("DockItems=", "IconSize=", "ZoomEnabled=", "Theme=Ming"):
            self.assertIn(marker, self.watchdog)
        self.assertIn("ming-refresh-dock-launchers", settings)

    def test_window_selector_prefers_dock_type_over_first_helper_window(self):
        selector = re.search(
            r"plank_window_id\(\) \{(.*?)\n\}", self.watchdog, re.S
        ).group(1)
        self.assertIn("while read -r candidate_id", selector)
        self.assertIn("_NET_WM_WINDOW_TYPE_DOCK", selector)
        self.assertLess(
            selector.index("_NET_WM_WINDOW_TYPE_DOCK"),
            selector.index('printf \'%s\\n\' "${fallback_id}"'),
        )

    def test_healthcheck_uses_the_same_dock_window_selection_policy(self):
        selector = re.search(
            r"window_id\(\) \{(.*?)\n\}", self.healthcheck, re.S
        ).group(1)
        self.assertIn("while read -r candidate_id", selector)
        self.assertIn("_NET_WM_WINDOW_TYPE_DOCK", selector)
        self.assertIn("fallback_id", selector)
        self.assertIn("geometry_is_in_bounds", selector)
        self.assertIn("geometry_is_bottom", selector)

    def test_watchdog_logs_specific_failure_and_recovery_states(self):
        for marker in (
            "not-running",
            "window-not-visible",
            "wrong-window-type",
            "not-above",
            "out-of-bounds",
            "wrong-position",
            "recovery succeeded",
        ):
            self.assertIn(marker, self.watchdog)

    def test_healthcheck_has_json_repair_and_component_state(self):
        for marker in (
            "--json",
            "--repair",
            '"desktop"',
            '"dock"',
            '"launch_broker"',
            '"running"',
            '"visible"',
            '"stacking"',
            '"geometry"',
            "ming-plank-watchdog",
            "ming-phone-desktop-watchdog",
            "ming-launch --server",
            "desktop-health.log",
        ):
            self.assertIn(marker, self.healthcheck)

    def test_healthcheck_visibility_is_based_on_screen_geometry(self):
        for marker in ("screen_geometry", "geometry_is_visible", "geometry_is_in_bounds"):
            self.assertIn(marker, self.healthcheck)
        self.assertIn('geometry_is_visible "${desktop_geometry}"', self.healthcheck)
        self.assertGreaterEqual(self.healthcheck.count("geometry_is_in_bounds"), 3)

    def test_healthcheck_exit_requires_watchdog_equivalent_dock_health(self):
        self.assertIn("geometry_is_in_bounds", self.healthcheck)
        self.assertIn("geometry_is_bottom", self.healthcheck)
        self.assertIn("dock_healthy=false", self.healthcheck)
        self.assertIn('[[ "${dock_stacking}" == "dock" || "${dock_stacking}" == "dock+above" ]]', self.healthcheck)
        final_condition = self.healthcheck.rsplit("\n", 3)[-3:]
        self.assertIn("dock_healthy", "\n".join(final_condition))

    def test_oobe_repairs_session_after_all_user_completion_paths(self):
        self.assertIn("repair_desktop_session()", self.oobe)
        self.assertGreaterEqual(
            self.oobe.count("repair_desktop_session"),
            4,
            "helper plus configured, dialog-cancel and skip completion paths",
        )
        for marker in ('echo "skipped"', 'echo "configured"'):
            self.assertLess(
                self.oobe.index(marker),
                self.oobe.index("repair_desktop_session", self.oobe.index(marker)),
            )

    def test_generated_runtime_scripts_are_valid_bash(self):
        for script in (self.watchdog, self.healthcheck, self.session_healthcheck, self.oobe):
            result = subprocess.run(
                [BASH, "-n"], input=script.replace("\r", "").encode("utf-8")
            )
            self.assertEqual(0, result.returncode)

    def test_session_coordinator_owns_the_long_lived_desktop_stack(self):
        for marker in (
            "PHONE_STARTUP_DEADLINE=8",
            "PLANK_STARTUP_DEADLINE=8",
            "PICOM_STARTUP_DEADLINE=5",
            "PROBE_TIMEOUT=2",
            "SUPERVISOR_INTERVAL=10",
            "flock -n",
            "ming-session-healthcheck.pid",
            "ming-phone-desktop-watchdog",
            "ming-plank-watchdog",
            "ming-picom",
            "while true; do",
            "sleep 10",
        ):
            self.assertIn(marker, self.session_healthcheck)

        self.assertIn(
            "Exec=/usr/local/bin/ming-session-healthcheck --session",
            self.autostart,
        )
        for direct in (
            "Exec=/usr/local/bin/ming-phone-desktop-watchdog --session",
            "Exec=/usr/local/bin/ming-plank-watchdog --session",
            "Exec=/usr/local/bin/ming-picom",
        ):
            self.assertNotIn(direct, self.autostart)

    def test_session_healthcheck_records_json_metrics_and_safe_fallbacks(self):
        for marker in (
            "session-startup.json",
            "session-health.log",
            "start_xfdesktop_fallback",
            "MING_PHONE_DESKTOP",
            "ming-phone-desktop.ready",
            "picom-fallback.conf",
            "xrender",
            "timeout --foreground 2s",
            '"phone_desktop"',
            '"plank"',
            '"picom"',
        ):
            self.assertIn(marker, self.session_healthcheck)

    def test_compositor_profile_controls_wrapper_and_crash_recovery(self):
        self.assertIn("compositor_profile", self.session_healthcheck)
        self.assertIn("appearance.json", self.session_healthcheck)
        self.assertIn("auto|compat|off", self.session_healthcheck)
        self.assertIn("picom-fallback.conf", self.session_healthcheck)
        self.assertIn("compositor_profile", self.source.split(
            "cat > /usr/local/bin/ming-picom << 'MINGPICOM'", 1)[1].split("MINGPICOM", 1)[0])

    def test_picom_wrapper_executes_persisted_software_config(self):
        wrapper = self.source.split(
            "cat > /usr/local/bin/ming-picom << 'MINGPICOM'", 1)[1].split("\nMINGPICOM", 1)[0]
        harness = r'''work="$(mktemp -d)"
printf '{"compositor_profile":"compat"}' >"${work}/settings.json"
for name in main fallback lowmem; do printf 'backend = "xrender";\n' >"${work}/${name}.conf"; done
cat >"${work}/fake-picom" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*"
EOF
chmod +x "${work}/fake-picom"
export MING_COMPOSITOR_SETTINGS="${work}/settings.json"
export MING_PICOM_MAIN_CONF="${work}/main.conf"
export MING_PICOM_FALLBACK_CONF="${work}/fallback.conf"
export MING_PICOM_LOWMEM_CONF="${work}/lowmem.conf"
export MING_PICOM_BIN="${work}/fake-picom"
'''
        result = subprocess.run([BASH], input=(harness + wrapper).encode(), capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        output = result.stdout.decode(errors="replace")
        self.assertIn("--config ", output)
        self.assertIn("fallback.conf", output)

    def test_compat_picom_config_keeps_shell_surfaces_fully_opaque(self):
        fallback = self.source.split(
            "cat > /etc/xdg/picom/picom-fallback.conf << 'PICOMFALLBACK'", 1
        )[1].split("PICOMFALLBACK", 1)[0]
        self.assertIn("dock = { shadow = false; opacity = 1.0; };", fallback)
        self.assertIn("notification = { shadow = false; opacity = 1.0; };", fallback)
        self.assertIn("detect-client-opacity = false;", fallback)

    def test_picom_wrapper_does_not_start_when_persisted_off(self):
        wrapper = self.source.split(
            "cat > /usr/local/bin/ming-picom << 'MINGPICOM'", 1)[1].split("\nMINGPICOM", 1)[0]
        harness = r'''work="$(mktemp -d)"
printf '{"compositor_profile":"off"}' >"${work}/settings.json"
printf '{}' >"${work}/appearance.json"
cat >"${work}/fake-picom" <<'EOF'
#!/usr/bin/env bash
printf 'unexpected-start\n'
EOF
chmod +x "${work}/fake-picom"
export MING_COMPOSITOR_SETTINGS="${work}/settings.json"
export MING_APPEARANCE_SETTINGS="${work}/appearance.json"
export MING_PICOM_BIN="${work}/fake-picom"
'''
        result = subprocess.run([BASH], input=(harness + wrapper).encode(), capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertNotIn("unexpected-start", result.stdout.decode(errors="replace"))

    def test_session_metrics_include_recovery_and_duplicate_counters(self):
        for marker in (
            '"pid_count"',
            '"elapsed_ms"',
            '"restarts"',
            '"recovered"',
            '"duplicates"',
            "process_count()",
            "phone_restarts",
            "plank_restarts",
            "picom_restarts",
        ):
            self.assertIn(marker, self.session_healthcheck)

    def test_phone_watchdog_lock_is_a_one_shot_duplicate_guard(self):
        phone = self.source.split(
            "cat > /usr/local/bin/ming-phone-desktop-watchdog << 'PHONEDESKWATCH'", 1
        )[1].split("PHONEDESKWATCH", 1)[0]
        lock_failure = phone.split('if ! mkdir "${lock_dir}"', 1)[1].split("fi", 1)[0]
        self.assertIn("exit 0", lock_failure)
        self.assertNotIn("start_phone_desktop", lock_failure)

    def test_plank_watchdog_one_shot_repairs_share_the_session_lock(self):
        self.assertIn("run_one_shot()", self.watchdog)
        one_shot = self.watchdog.split("run_one_shot()", 1)[1].split("\n}", 1)[0]
        self.assertIn("ming-plank-watchdog.lock", one_shot)
        self.assertIn("flock -n 9", one_shot)


if __name__ == "__main__":
    unittest.main()
