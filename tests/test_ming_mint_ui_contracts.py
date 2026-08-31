import importlib.util
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
DRAWER = ROOT / "assets" / "ming-app-drawer.py"
SETTINGS_HUB = ROOT / "modules" / "08_settings_hub.sh"
ICON_ROOT = ROOT / "assets" / "icons" / "ming-mint"


def load_drawer():
    spec = importlib.util.spec_from_file_location("ming_mint_drawer", DRAWER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingMintThemeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.drawer = DRAWER.read_text(encoding="utf-8")

    def test_ming_mint_theme_is_active_and_uses_compact_typography(self):
        self.assertIn('local theme_root="/usr/share/themes/Ming-Mint"', self.desktop)
        self.assertIn("gtk-theme-name=Ming-Mint", self.desktop)
        self.assertIn('xfconf-query -c xfwm4 -p /general/theme -s "Ming-Mint"', self.desktop)
        self.assertIn("Noto Sans CJK SC 14", self.desktop)
        self.assertIn("font-size: 14px", self.desktop)
        self.assertIn("line-height: 1.45", self.desktop)
        self.assertIn("border-radius: 10px", self.desktop)

    def test_window_and_notification_surfaces_are_opaque(self):
        self.assertIn("background-color: #F7FBF9", self.desktop)
        self.assertIn("background-color: #FFFFFF", self.desktop)
        self.assertIn("initial-opacity\" type=\"double\" value=\"1.0\"", self.desktop)
        self.assertIn("background-opacity=100", self.desktop)

    def test_responsive_dock_profile_is_applied_at_runtime(self):
        self.assertIn("MingDockProfile=2641-responsive-centered", self.desktop)
        self.assertIn("IconSize=40", self.desktop)
        self.assertIn("ZoomPercent=148", self.desktop)
        self.assertIn("Offset=12", self.desktop)
        self.assertIn("short_side <= 720", self.desktop)
        self.assertIn("short_side <= 900", self.desktop)
        self.assertIn("Theme=Ming", self.desktop)
        self.assertIn("alignment center", self.desktop)
        self.assertIn("items-alignment center", self.desktop)

    def test_drawer_geometry_only_reserves_a_visible_dock(self):
        drawer = load_drawer()
        workarea = {"x": 10, "y": 20, "width": 1000, "height": 800}
        geometry = drawer.drawer_geometry(workarea, dock_visible=False)
        self.assertEqual(576.0, geometry.height)
        self.assertEqual(240.0, geometry.y)
        self.assertEqual(198.0, drawer.drawer_geometry(workarea, dock_visible=True).y)
        self.assertEqual(10, drawer.DRAWER_DOCK_GAP)
        self.assertEqual(32, drawer.DOCK_RESERVED_HEIGHT)
        self.assertEqual(4, drawer.DRAWER_BOTTOM_MARGIN)
        self.assertIn("DRAWER_DOCK_GAP", self.drawer)
        self.assertIn("DOCK_RESERVED_HEIGHT", self.drawer)

    def test_drawer_window_is_not_tied_to_dock_top(self):
        show = self.drawer[self.drawer.index("    def show(self):"):self.drawer.index("    def hide(self):")]
        self.assertIn("drawer_geometry", show)
        self.assertNotIn("dock_window", show)
        self.assertNotIn("_NET_WM_STRUT", show)

    def test_ming_mint_icon_set_is_transparent_and_complete(self):
        names = ("settings", "files", "terminal", "app-library", "update", "control", "store", "toolbox", "papyrus", "xiahai", "mark")
        for name in names:
            icon = ICON_ROOT / f"{name}.svg"
            self.assertTrue(icon.is_file(), name)
            data = icon.read_text(encoding="utf-8")
            self.assertIn('xmlns="http://www.w3.org/2000/svg"', data)
            self.assertIn('fill="none"', data)
            self.assertNotIn("linearGradient", data)
            self.assertNotIn("radialGradient", data)
        index = (ICON_ROOT / "index.theme").read_text(encoding="utf-8")
        self.assertIn("Name=Ming Mint", index)
        self.assertIn("Directories=24x24/apps,32x32/apps,48x48/apps,scalable/apps", index)

    def test_desktop_files_reference_shared_ming_mint_icon_names(self):
        for desktop_name, icon_name in (
            ("ming-settings.desktop", "ming-settings"),
            ("ming-files.desktop", "ming-files"),
            ("ming-terminal.desktop", "ming-terminal"),
            ("ming-app-library.desktop", "ming-app-library"),
            ("ming-update.desktop", "ming-update"),
            ("ming-store.desktop", "ming-store"),
        ):
            self.assertIn(f"[{desktop_name}]={icon_name}", self.desktop)

    def test_settings_hub_does_not_reintroduce_legacy_settings_icon(self):
        """The later settings module must preserve the canonical Mint icon."""
        settings_hub = SETTINGS_HUB.read_text(encoding="utf-8")
        self.assertIn("Icon=ming-settings", settings_hub)
        self.assertNotIn("Icon=ming-control-center", settings_hub)

    def test_drawer_prefers_the_canonical_ming_settings_desktop_id(self):
        drawer = self.drawer
        self.assertIn('"settings": "ming-settings.desktop"', drawer)
        self.assertNotIn('"settings": "ming-control-center.desktop"', drawer)

    def test_settings_hub_user_launcher_copy_is_symlink_safe_and_atomic(self):
        settings_hub = SETTINGS_HUB.read_text(encoding="utf-8")
        self.assertIn('if [[ -L "${user_app_dir}" ]]', settings_hub)
        self.assertIn('[[ -d "${user_app_dir}" && ! -L "${user_app_dir}" ]]', settings_hub)
        self.assertIn('mktemp "${user_target}.tmp.XXXXXX"', settings_hub)
        self.assertIn('mv -f -- "${temporary}" "${user_target}"', settings_hub)
        self.assertIn('chown --no-dereference', settings_hub)

    def test_active_entries_never_reference_legacy_icon_names(self):
        self.assertNotIn("Icon=files-icon", self.desktop)
        self.assertNotIn("Icon=ming-control-center", self.desktop)
        self.assertNotIn("Icon=ming-update-icon", self.desktop)
        self.assertIn('"${user_home}/.config/ming-os"', self.desktop)

    def test_user_icon_migration_rejects_symlinked_destination_and_uses_atomic_copy(self):
        helper = self.desktop.split(
            "configure_ming_mint_desktop_icons() {", 1
        )[1].split("\n}\n\n# ======================== 主题与图标", 1)[0]
        self.assertIn('[[ -f "${app_dir}/${desktop_file}" && ! -L "${app_dir}/${desktop_file}" ]]', helper)
        self.assertIn('[[ ! -L "${user_app_dir}" ]]', helper)
        self.assertIn('[[ ! -L "${user_target}" ]]', helper)
        self.assertIn('mktemp "${user_target}.tmp.XXXXXX"', helper)
        self.assertIn('mv -f -- "${temporary}" "${user_target}"', helper)

    def test_login_enforcer_does_not_skip_ming_mint_readback_after_reapply(self):
        enforcer = self.desktop.split(
            "cat > /usr/local/bin/ming-apply-appearance << 'APPLYAPPEARANCE'", 1
        )[1].split("APPLYAPPEARANCE", 1)[0]
        self.assertNotIn("ming-appearance-control reapply --json \\\n        >>\"${appearance_log}\" 2>&1 || true\n    exit 0", enforcer)
        self.assertIn('xfconf-query -c xsettings -p /Net/IconThemeName -s "Ming-Mint"', enforcer)

    def test_login_enforcer_preserves_a_user_selected_dark_theme(self):
        """A successful reapply must not be overwritten by the login fallback."""
        enforcer = self.desktop.split(
            "cat > /usr/local/bin/ming-apply-appearance << 'APPLYAPPEARANCE'", 1
        )[1].split("APPLYAPPEARANCE", 1)[0]
        self.assertIn("appearance_reapply_ok=false", enforcer)
        self.assertIn("if timeout --foreground 8s ming-appearance-control reapply --json", enforcer)
        self.assertIn("appearance_reapply_ok=true", enforcer)
        self.assertIn('fallback_theme="Ming-Dark"', enforcer)
        self.assertIn('if [[ "${appearance_reapply_ok}" != "true" ]]; then', enforcer)
        fallback = enforcer.split('if [[ "${appearance_reapply_ok}" != "true" ]]; then', 1)[1].split(
            "fi", 1
        )[0]
        self.assertIn('xfconf-query -c xsettings -p /Net/ThemeName -s "${fallback_theme}"', fallback)
        self.assertIn('xfconf-query -c xfwm4 -p /general/theme -s "${fallback_theme}"', fallback)
        self.assertNotIn('xfconf-query -c xsettings -p /Net/ThemeName -s "Ming-Mint" 2>/dev/null || true', fallback)

    def test_default_login_and_light_notifications_use_ming_mint(self):
        apps = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
        appearance = (ROOT / "assets" / "ming-appearance-control.py").read_text(encoding="utf-8")
        self.assertIn("theme-name = Ming-Mint", apps)
        notification = appearance.split("def sync_notification_theme", 1)[1].split(
            "def sync_dock_runtime", 1
        )[0]
        self.assertIn('else "Ming-Mint"', notification)


if __name__ == "__main__":
    unittest.main()
