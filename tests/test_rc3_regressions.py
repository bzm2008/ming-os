import ast
import os
import pathlib
import tempfile
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
APPEARANCE = (ROOT / "assets" / "ming-appearance-control.py").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")


class Rc3DockContracts(unittest.TestCase):
    def test_dock_uses_legacy_2640_geometry_and_does_not_reserve_fullscreen_workarea(self):
        settings = DESKTOP.split("cat > \"${plank_dir}/settings\" << 'PLANKSETTINGS'", 1)[1].split(
            "PLANKSETTINGS", 1
        )[0]
        self.assertIn("IconSize=40", settings)
        self.assertIn("ZoomPercent=148", settings)
        self.assertIn("MingDockProfile=2640-legacy-centered", settings)
        self.assertNotIn("VisualBottomGap=18", settings)
        self.assertNotIn("reserve_bottom_workarea", DESKTOP)


class Rc3AppearanceContracts(unittest.TestCase):
    @staticmethod
    def wallpaper_discovery():
        tree = ast.parse(SETTINGS)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "discover_builtin_wallpapers"
        )
        namespace = {
            "os": os,
            "BUILTIN_WALLPAPERS": {
                "default": "/built-in/default.png",
                "light": "/built-in/default-light.png",
            },
            "WALLPAPER_IMAGE_SUFFIXES": (".png", ".jpg", ".jpeg"),
            "WALLPAPER_DIR": "/missing",
        }
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        exec(compile(module, "<settings>", "exec"), namespace)
        return namespace["discover_builtin_wallpapers"]

    def test_settings_does_not_force_light_and_wallpaper_page_renders_thumbnails(self):
        install_css = SETTINGS[SETTINGS.index("    def install_css(self):"):
                               SETTINGS.index("    def on_nav_selected", SETTINGS.index("    def install_css(self):"))]
        self.assertNotIn("Adw.ColorScheme.FORCE_LIGHT", install_css)
        self.assertIn("Gtk.FlowBox", SETTINGS)
        self.assertIn("Gtk.Picture", SETTINGS)
        self.assertIn("BUILTIN_WALLPAPERS", SETTINGS)

    def test_settings_dark_mode_has_real_window_palette_not_only_adwaita_preference(self):
        self.assertIn("ming-settings-dark", SETTINGS)
        self.assertIn("def apply_settings_theme(self, theme):", SETTINGS)
        dark_css = SETTINGS.split(
            ".ming-settings-window.ming-settings-dark", 1
        )[1].split(".ming-feedback-dialog", 1)[0]
        self.assertIn("#151A18", dark_css)
        self.assertIn("#E7EEE9", dark_css)
        self.assertIn("self.apply_settings_theme(values[0])", SETTINGS)

    def test_wallpaper_page_discovers_every_supported_image_from_the_installed_directory(self):
        self.assertIn("def discover_builtin_wallpapers(", SETTINGS)
        discovery = SETTINGS.split("def discover_builtin_wallpapers(", 1)[1].split(
            "\n\n", 1
        )[0]
        for suffix in (".png", ".jpg", ".jpeg"):
            self.assertIn(suffix, SETTINGS)
        self.assertIn("discover_builtin_wallpapers()", SETTINGS)
        self.assertIn('apply_value = key if key in BUILTIN_WALLPAPERS else path', SETTINGS)

    def test_wallpaper_discovery_does_not_duplicate_named_builtin_images(self):
        discover = self.wallpaper_discovery()
        with tempfile.TemporaryDirectory() as directory:
            for name in ("default.png", "default-light.png", "summer.jpg"):
                pathlib.Path(directory, name).touch()
            found = discover(directory)
        self.assertEqual({"default", "light", "summer"}, set(found))

    def test_appearance_applies_terminal_and_notification_theme(self):
        self.assertIn(".config/xfce4/terminal/terminalrc", APPEARANCE)
        self.assertIn("BackgroundOpacity", APPEARANCE)
        self.assertIn("xfce4-notifyd", APPEARANCE)
        self.assertIn('xfconf_set("xfwm4", "/general/theme"', APPEARANCE)
        self.assertIn('"dock_icon_size": 40', APPEARANCE)
        self.assertIn("/usr/share/themes/Ming-Dark/gtk-3.0/gtk.css", DESKTOP)
        self.assertIn("/usr/share/themes/Ming-Dark/xfce-notify-4.0/gtk.css", DESKTOP)
        self.assertNotIn("gtk-3.0 +        /usr/share/themes/Ming-Dark", DESKTOP)

    def test_terminal_background_is_opaque(self):
        terminal = DESKTOP.split(
            "cat > \"/home/${MING_USER}/.config/xfce4/terminal/terminalrc\" << 'TERMINALRC'", 1
        )[1].split("TERMINALRC", 1)[0]
        self.assertIn("ColorBackground=", terminal)
        self.assertNotIn("background-opacity=88", terminal)


class Rc3SparkContracts(unittest.TestCase):
    def test_live_spark_install_has_explicit_installed_system_message(self):
        self.assertIn("Live 模式", APPS)
        self.assertIn("请先安装 Ming OS", APPS)

    def test_spark_install_reads_back_package_state_and_refreshes_result(self):
        self.assertIn("dpkg-query", APPS)
        self.assertIn("installed", APPS)
        self.assertIn("refresh", APPS)

    def test_apm_install_uses_apm_readback_without_host_dpkg_query(self):
        start = APPS.index("    apm)\n")
        branch = APPS[start:APPS.index("\n    *)\n", start)]
        install = branch.split('/usr/bin/apm "$subaction" "$@"', 1)[1]
        self.assertIn("verify_apm_installed", branch)
        self.assertIn('/usr/bin/apm list --installed', APPS)
        self.assertIn("sed 's/\\x1b\\[[0-9;]*m//g'", APPS)
        self.assertIn("printf '%s\\n'", APPS)
        self.assertNotIn("sed 's/\\\\x1b\\\\[[0-9;]*m//g'", APPS)
        self.assertNotIn("printf '%s\\\\n'", APPS)
        self.assertNotIn("verify_packages_installed", branch)
        self.assertNotIn("dpkg-query", install)


class Rc3StatusWidgetContracts(unittest.TestCase):
    def test_status_widget_follows_the_shared_dark_theme_without_a_new_polling_loop(self):
        self.assertIn("ming-desktop-dark", PHONE)
        self.assertIn("apply_desktop_theme", PHONE)
        self.assertIn("appearance.json", PHONE)
        self.assertIn("monitor_file", PHONE)
        self.assertIn(".ming-desktop-dark .status-widget", PHONE)

    def test_expanded_widget_has_small_top_gap_and_low_frequency_resource_sampling(self):
        widget = PHONE[PHONE.index("class StatusWidget"):PHONE.index("class WallpaperCanvas")]
        self.assertIn("CLOCK_MARGIN_Y = 8", PHONE)
        self.assertIn("STATUS_WIDGET_EXPANDED_HEIGHT = 220", PHONE)
        self.assertIn("padding: 8px 16px", PHONE)
        self.assertIn("STATUS_RESOURCE_REFRESH_SECONDS = 30", PHONE)
        self.assertIn("MemAvailable", PHONE)

    def test_volume_control_repairs_default_sink_before_writing(self):
        widget = PHONE[PHONE.index("def set_control_value"):PHONE.index("class WallpaperCanvas")]
        self.assertIn("audio_repair_playback", widget)
        self.assertIn("confirmed_value", widget)
        repair_branch = widget.split('elif kind == "volume":', 1)[1].split("else:", 1)[0]
        self.assertIn('if repair.get("ok")', repair_branch)
        self.assertIn("set_volume(value)", repair_branch)


if __name__ == "__main__":
    unittest.main()
