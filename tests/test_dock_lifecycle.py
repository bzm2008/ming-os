import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


class DockLifecycleContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESKTOP.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")
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
        self.assertIn('ensure_plank_settings', self.watchdog)
        self.assertIn('^HideMode=', self.watchdog)

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

    def test_light_dock_defaults_and_low_memory_zoom_policy_stay_consistent(self):
        self.assertIn("IconSize=38", self.plank_settings)
        self.assertIn("ZoomEnabled=true", self.plank_settings)
        self.assertIn("ZoomPercent=112", self.plank_settings)
        self.assertIn("ZoomPercent=112", self.watchdog)
        self.assertIn('sed -i "s/^ZoomEnabled=.*/ZoomEnabled=false/"', self.source)
        self.assertIn('sed -i "s/^ZoomPercent=.*/ZoomPercent=100/"', self.source)
        self.assertIn("dock_zoom=false", self.source)
        self.assertIn('"ZoomPercent=112"', self.build)
        self.assertIn('"LaunchBounceTime=150"', self.build)
        self.assertIn('"ItemMoveTime=130"', self.build)

    def test_compact_rail_theme_is_visibly_distinct_and_low_cost(self):
        for marker in (
            "TopRoundness=6",
            "BottomRoundness=6",
            "HorizPadding=8",
            "ItemPadding=3",
            "IndicatorSize=4",
            "LaunchBounceHeight=0.20",
        ):
            self.assertIn(marker, self.source)

    def test_compact_rail_profile_migrates_existing_2640_users_once(self):
        for marker in (
            "MingDockProfile=2641-compact-rail-1",
            "migrate_compact_rail_profile",
            "DockItems=ming-settings.dockitem;;ming-app-library.dockitem",
            "s/^IconSize=.*/IconSize=38/",
            "s/^ZoomPercent=.*/ZoomPercent=112/",
        ):
            self.assertIn(marker, self.watchdog)
        self.assertIn("migrate_compact_rail_profile", self.watchdog.split("ensure_plank_settings() {", 1)[1])

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
            "duplicate-processes",
            "window-not-visible",
            "wrong-window-type",
            "not-above",
            "out-of-bounds",
            "wrong-position",
            "recovery succeeded",
        ):
            self.assertIn(marker, self.watchdog)

    def test_dock_and_compositor_health_require_exactly_one_process(self):
        self.assertIn("plank_process_count", self.watchdog)
        self.assertIn('[[ "${processes}" -eq 1 ]]', self.watchdog)
        for marker in (
            '[[ "$(process_count phone)" -eq 1 ]]',
            '[[ "$(process_count plank)" -eq 1 ]]',
            '[[ "$(process_count picom)" -eq 1 ]]',
            "stop_duplicate_phone_desktops",
            "stop_duplicate_picom",
        ):
            self.assertIn(marker, self.session_healthcheck)

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

    def test_oobe_repairs_session_only_after_required_admin_setup_completes(self):
        self.assertIn("repair_desktop_session()", self.oobe)
        self.assertNotIn('echo "skipped"', self.oobe)
        marker = 'echo "configured"'
        self.assertLess(
            self.oobe.index(marker),
            self.oobe.index("repair_desktop_session", self.oobe.index(marker)),
        )

    def test_oobe_failures_are_structured_and_never_mark_ready(self):
        for marker in (
            "log_oobe_event",
            '"bootstrap_failed"',
            '"status_not_ready"',
            '"retry"',
            "oobe-account.jsonl",
        ):
            self.assertIn(marker, self.oobe)
        self.assertLess(
            self.oobe.index('"status_not_ready"'),
            self.oobe.index('echo "configured"'),
        )
        self.assertIn("OOBE_MAX_ATTEMPTS=3", self.oobe)
        self.assertIn('"retry_exhausted"', self.oobe)
        self.assertNotIn("exec /usr/local/bin/ming-oobe-account", self.oobe)

    def test_generated_runtime_scripts_are_valid_bash(self):
        for script in (self.watchdog, self.healthcheck, self.session_healthcheck, self.oobe):
            result = subprocess.run(
                ["bash", "-n"], input=script.replace("\r", "").encode("utf-8")
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
