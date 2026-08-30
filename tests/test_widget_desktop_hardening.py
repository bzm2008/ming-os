import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


class WidgetGeometryContracts(unittest.TestCase):
    def test_compact_capsule_has_a_stable_responsive_geometry_contract(self):
        self.assertIn("STATUS_WIDGET_COMPACT_WIDTH", PHONE)
        self.assertIn("def status_widget_compact_geometry", PHONE)
        tree = ast.parse(PHONE)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "status_widget_compact_geometry"
        )
        namespace = {
            "max": max,
            "min": min,
            "int": int,
            "STATUS_WIDGET_COMPACT_WIDTH": 252,
            "STATUS_WIDGET_COMPACT_NARROW_WIDTH": 242,
            "STATUS_WIDGET_COMPACT_HEIGHT": 58,
        }
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        exec(compile(module, "<phone-layout>", "exec"), namespace)
        geometry = namespace["status_widget_compact_geometry"]
        self.assertEqual(
            geometry({"width": 1024, "height": 768}),
            geometry({"width": 1024, "height": 600}),
        )
        self.assertLessEqual(geometry({"width": 1024, "height": 768})["width"], 260)
        self.assertGreaterEqual(geometry({"width": 320, "height": 240})["width"], 220)

    def test_win_key_dedup_window_covers_shortcut_and_signal_delivery(self):
        self.assertIn("STATUS_TOGGLE_DEDUP_SECONDS = 0.65", PHONE)
        toggle = PHONE.split("    def toggle_status_widget", 1)[1].split(
            "    def _on_toggle_signal", 1
        )[0]
        self.assertIn("STATUS_TOGGLE_DEDUP_SECONDS", toggle)

    def test_collapsing_widget_zeroes_popup_and_hides_all_expanded_children(self):
        state = PHONE.split("    def apply_collapsed_state", 1)[1].split(
            "    def on_resource_clicked", 1
        )[0]
        self.assertIn("set_reveal_child(False)", state)
        self.assertIn("set_visible(False)", state)
        self.assertIn("set_size_request(-1, 0)", state)
        self.assertIn("expanded_window.hide()", state)


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
