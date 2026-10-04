import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


class WinKeyRoutingContracts(unittest.TestCase):
    def test_win_key_dedup_window_covers_shortcut_and_signal_delivery(self):
        # The retired widget's dedup window is gone with its implementation; the
        # surviving contract is only that the Win key routes into a no-op entry
        # point instead of mutating a widget.
        self.assertNotIn("STATUS_TOGGLE_DEDUP_SECONDS", PHONE)
        toggle = PHONE.split("    def toggle_status_widget", 1)[1].split(
            "    def _on_toggle_signal", 1
        )[0]
        self.assertIn("return False", toggle)
        self.assertNotIn("self.status.set_collapsed", toggle)

class DesktopLayerContracts(unittest.TestCase):
    def test_feedback_surface_uses_an_opaque_non_rgba_window_on_xrender(self):
        feedback = LAUNCH.split("def _launch_feedback_window", 1)[1].split(
            "def schedule_launch", 1
        )[0]
        self.assertIn("set_app_paintable(False)", feedback)
        self.assertNotIn("set_rgba_visual", feedback)
        self.assertIn("window.set_opacity(1.0)", feedback)

    def test_dock_uses_one_compositor_path_and_disables_animations_when_policy_disables_it(self):
        self.assertIn("stop_duplicate_picom", DESKTOP)
        self.assertIn("start_picom", DESKTOP)
        self.assertIn("disabled-by-policy", DESKTOP)
        self.assertIn("fading = false", DESKTOP)

    def test_ming_picom_uses_a_session_lock_before_exec(self):
        script = DESKTOP.split(
            "cat > /usr/local/bin/ming-picom << 'MINGPICOM'", 1
        )[1].split("MINGPICOM", 1)[0]
        self.assertIn("ming-picom.lock", script)
        self.assertIn("flock -n", script)
        self.assertLess(script.index("flock -n"), script.index("exec picom"))


class SettingsLayoutContracts(unittest.TestCase):
    def test_settings_content_and_split_fill_a_maximized_monitor(self):
        init = SETTINGS.split("class MingSettings", 1)[1].split(
            "    def install_css", 1
        )[0]
        for marker in (
            "self.split.set_hexpand(True)",
            "self.split.set_vexpand(True)",
            "content_box.set_hexpand(True)",
            "self.content_stack.set_hexpand(True)",
        ):
            self.assertIn(marker, init)

    def test_wifi_password_dialog_has_a_native_present_fallback(self):
        connect = SETTINGS.split("    def on_wifi_connect", 1)[1].split(
            "    def show_wifi_recovery_actions", 1
        )[0]
        self.assertIn("dlg.present()", connect)
        self.assertIn("show_wifi_recovery_actions", connect)
        self.assertIn("打开网络设置", connect)

    def test_time_sync_status_keeps_explicit_non_network_failure_states(self):
        self.assertIn('"dbus_unavailable"', SETTINGS)
        self.assertIn('"service_inactive"', SETTINGS)
        self.assertIn('"waiting_network"', SETTINGS)


if __name__ == "__main__":
    unittest.main()
