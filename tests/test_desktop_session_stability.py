import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
DRAWER = (ROOT / "assets" / "ming-app-drawer.py").read_text(encoding="utf-8")


def generated_script(start_marker, end_marker):
    return DESKTOP.split(start_marker, 1)[1].split(end_marker, 1)[0].lstrip("\n")


class DesktopSessionStabilityContracts(unittest.TestCase):
    def test_session_lifecycle_uses_one_ready_marker_and_cleans_stale_state(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        for marker in (
            "session_ready_file",
            "session_pid_file",
            "session_lock_file",
            "cleanup_stale_session_state",
            "trap 'cleanup_session_state' EXIT",
        ):
            self.assertIn(marker, session)
        self.assertIn("session_ready_file=", session)
        self.assertNotIn('rm -f "${HOME}/.cache/ming-os/ming-phone-desktop.ready"', session)

    def test_duplicate_recovery_keeps_one_pid_for_each_managed_component(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        for marker in (
            "keep_one_process",
            "stop_duplicate_phone_desktops",
            "stop_duplicate_plank",
            "stop_duplicate_picom",
            "pgrep -u",
            "kill -TERM",
        ):
            self.assertIn(marker, session)
        for function_name in ("stop_duplicate_phone_desktops", "stop_duplicate_plank", "stop_duplicate_picom"):
            body = session.split(function_name + "() {", 1)[1].split("\n}", 1)[0]
            self.assertIn("keep_one_process", body)
            self.assertNotIn("pkill", body)

    def test_failure_path_explicitly_starts_xfdesktop_without_touching_user_apps(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        self.assertIn("start_xfdesktop_fallback", session)
        self.assertIn("Ming Phone Desktop startup failed; using xfdesktop fallback", session)
        self.assertNotIn("pkill -x wps", session)
        self.assertNotIn("pkill -x quark", session)

    def test_write_metrics_embedded_python_is_syntax_valid(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        python_source = session.split("python3 - <<'PY'", 1)[1].split("\nPY", 1)[0]
        compile(python_source, "ming-session-healthcheck.write_metrics", "exec")

    def test_sample_interface_emits_process_rss_json(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        self.assertIn("sample_processes", session)
        self.assertIn("--sample", session)
        self.assertIn('"rss_kib"', session)
        self.assertIn('"pid"', session)

    def test_phone_and_drawer_share_session_lifecycle_contract(self):
        for source in (PHONE, DRAWER):
            self.assertIn("ming-session-healthcheck", source)
            self.assertIn("session_ready", source)

    def test_taskbar_visibility_failure_path_backs_off_instead_of_retrying_forever(self):
        """The 2026-10-04 VM run retried the taskbar repair every ~25s forever."""
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        self.assertIn("taskbar_in_cooldown() {", session)
        self.assertIn("taskbar_repair_cooldown=60", session)
        start = session.split("start_taskbar_dock() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("taskbar_in_cooldown", start)          # skip while cooling down
        self.assertIn('>"${taskbar_cooldown_file}"', start)  # record the failure
        self.assertIn('rm -f "${taskbar_cooldown_file}"', start)  # clear on success
        self.assertIn("next repair in ${taskbar_repair_cooldown}s", start)
        # A fresh session must still get one real attempt.
        startup = session.split("startup_once() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('rm -f "${taskbar_cooldown_file}"', startup)

    def test_retired_plank_fallback_claim_is_gone(self):
        """Plank is purged by this module, so no dock fallback can exist."""
        self.assertNotIn("Plank fallback remains available", DESKTOP)
        self.assertIn("no dock fallback is installed", DESKTOP)

    def test_backdrop_diagnostics_are_advisory_and_do_not_gate_healthy(self):
        session = generated_script(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )
        self.assertIn('"backdrop": {', session)
        # Probe the X root window, not one particular owner: task-5 phase 2 is
        # still deciding whether xfdesktop stays resident.
        self.assertIn("_XROOTPMAP_ID", session)
        self.assertIn("MING_WALLPAPER_PRESENT", session)
        healthy = session.split('payload["healthy"] = (', 1)[1].split("\n)", 1)[0]
        self.assertNotIn("backdrop", healthy)
        self.assertNotIn("MING_ROOT_PIXMAP", healthy)


if __name__ == "__main__":
    unittest.main()
