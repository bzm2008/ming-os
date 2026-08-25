import importlib.util
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
DRAWER = ROOT / "assets" / "ming-app-drawer.py"
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

    def test_legacy_dock_profile_is_applied_at_runtime(self):
        self.assertIn("MingDockProfile=2640-legacy-centered", self.desktop)
        self.assertIn("IconSize=40", self.desktop)
        self.assertIn("ZoomPercent=148", self.desktop)
        self.assertIn("Offset=12", self.desktop)
        self.assertIn("Theme=Ming", self.desktop)
        self.assertIn("alignment center", self.desktop)
        self.assertIn("items-alignment center", self.desktop)

    def test_drawer_geometry_reserves_dock_and_bottom_gap(self):
        drawer = load_drawer()
        geometry = drawer.drawer_geometry({"x": 10, "y": 20, "width": 1000, "height": 800})
        self.assertEqual(576.0, geometry.height)
        self.assertEqual(198.0, geometry.y)
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
        names = ("settings", "files", "terminal", "app-library", "update", "control", "store", "papyrus", "xiahai")
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
            ("spark-store.desktop", "ming-store"),
        ):
            self.assertIn(f"[{desktop_name}]={icon_name}", self.desktop)


if __name__ == "__main__":
    unittest.main()
