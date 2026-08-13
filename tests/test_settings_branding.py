import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "assets" / "ming-settings.py"
SETTINGS_HUB = ROOT / "modules" / "08_settings_hub.sh"
BUILD = ROOT / "build_onion_os.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"


class SettingsBrandingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.settings_hub = SETTINGS_HUB.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.desktop = DESKTOP.read_text(encoding="utf-8")

    def test_update_page_places_logo_slogan_then_check_action(self):
        update = self.settings.split("    def build_update(self):", 1)[1].split(
            "    def build_display(self):", 1
        )[0]
        logo = 'Gtk.Picture.new_for_filename(MING_LOGO_PATH)'
        self.assertIn('MING_LOGO_PATH = "/usr/share/pixmaps/ming-os-logo.png"', self.settings)
        self.assertIn(logo, update)
        self.assertIn("草木不争高，争的是生生不息。", update)
        self.assertLess(update.index(logo), update.index("草木不争高，争的是生生不息。"))
        self.assertLess(update.index("草木不争高，争的是生生不息。"), update.index('Gtk.Button(label="检查更新")'))

    def test_settings_switches_pages_without_expensive_crossfade(self):
        self.assertIn("Gtk.StackTransitionType.NONE", self.settings)
        self.assertNotIn("Gtk.StackTransitionType.CROSSFADE", self.settings)

    def test_settings_css_declares_consistent_type_scale_and_spacing(self):
        self.assertIn("font-size: 15px;", self.settings)
        self.assertIn("line-height: 1.35;", self.settings)
        self.assertIn("content_box.set_spacing(12)", self.settings)
        self.assertIn("min-height: 40px;", self.settings)

    def test_common_actions_home_simplifies_frequent_paths(self):
        self.assertIn('"home": "常用操作"', self.settings)
        self.assertIn('(\"view-grid-symbolic\", \"常用操作\", self.build_home)', self.settings)
        self.assertIn('def build_home(self):', self.settings)
        for label in ["连接 Wi-Fi", "检查更新", "调节屏幕", "换壁纸", "修复声音", "重设密码"]:
            self.assertIn(label, self.settings)
        self.assertIn('self.navigate_to_page("网络与蓝牙")', self.settings)
        self.assertIn('self.navigate_to_page("系统更新")', self.settings)
        self.assertNotIn("xfce4-settings-manager", self.settings)

    def test_familiar_windows_shortcuts_open_files_and_settings(self):
        self.assertIn("&lt;Super&gt;e", self.desktop)
        self.assertIn('value="ming-files"', self.desktop)
        self.assertIn("&lt;Super&gt;i", self.desktop)
        self.assertIn('value="ming-control-center"', self.desktop)

    def test_logo_is_installed_and_release_gate_requires_it(self):
        self.assertTrue((ROOT / "assets" / "ming-os-logo.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("ming-os-logo.png", self.settings_hub)
        self.assertIn("/usr/share/pixmaps/ming-os-logo.png", self.settings_hub)
        self.assertIn('require_file("usr/share/pixmaps/ming-os-logo.png"', self.build)
        self.assertIn("deploy_settings_hub || return 1", self.settings_hub)

    def test_legacy_desktop_theme_uses_static_effects_and_disables_animations(self):
        theme = self.desktop.split("cat > /usr/share/themes/Ming-Glass/gtk-3.0/gtk.css << 'MINGGLASSCSS'", 1)[1].split(
            "MINGGLASSCSS", 1
        )[0]
        self.assertNotIn("transition:", theme)
        self.assertNotIn("animation:", theme)
        self.assertNotIn("box-shadow:", theme)
        self.assertIn("gtk-enable-animations=0", self.desktop)


if __name__ == "__main__":
    unittest.main()
