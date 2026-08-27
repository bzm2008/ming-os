import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
ICON_THEME = (ROOT / "assets" / "icons" / "ming-mint" / "index.theme").read_text(
    encoding="utf-8")


class SettingsResponsiveContracts(unittest.TestCase):
    def test_settings_content_has_no_760_pixel_clamp_and_tracks_window_width(self):
        page_scroller = SETTINGS.split("    def page_scroller(self):", 1)[1].split(
            "    def toast", 1)[0]
        self.assertNotIn("maximum_size=760", page_scroller)
        self.assertIn("box.set_hexpand(True)", page_scroller)
        self.assertIn("update_responsive_layout", SETTINGS)
        self.assertIn('notify::width', SETTINGS)

    def test_page_builder_exception_renders_a_readable_placeholder(self):
        navigation = SETTINGS.split("    def on_nav_selected", 1)[1].split(
            "    def navigate_to_page", 1)[0]
        self.assertIn("try:", navigation)
        self.assertIn("except Exception", navigation)
        self.assertIn("build_page_error_placeholder", navigation)
        self.assertIn("此设置页面暂时无法显示", SETTINGS)

    def test_custom_wallpaper_uses_file_dialog_fallback_and_readback(self):
        appearance = SETTINGS.split("    def build_appearance_pointer", 1)[1].split(
            "    def refresh_pointer_status", 1)[0]
        self.assertIn("Gtk.FileDialog", appearance)
        self.assertIn("Gtk.FileChooserNative", appearance)
        self.assertIn('"import-wallpaper"', appearance)
        self.assertIn("refresh_appearance_status", appearance)
        self.assertIn(".png", appearance.lower())
        self.assertIn(".jpeg", appearance.lower())

    def test_time_sync_ui_consumes_the_explicit_helper_states(self):
        snapshot = SETTINGS.split("def time_sync_snapshot", 1)[1].split(
            "def display_status_snapshot", 1)[0]
        status = SETTINGS.split("    def apply_time_sync_status", 1)[1].split(
            "    def refresh_time_sync_status", 1)[0]
        for state in (
                "synced", "waiting_network", "service_inactive",
                "dbus_unavailable", "failed"):
            self.assertIn(state, snapshot + status)
        self.assertIn("等待网络", status)
        self.assertIn("系统通信服务", status)


class MingMintIconThemeContracts(unittest.TestCase):
    def test_icon_theme_inherits_complete_system_fallbacks(self):
        self.assertIn("Inherits=Adwaita,Papirus,hicolor", ICON_THEME)


if __name__ == "__main__":
    unittest.main()
