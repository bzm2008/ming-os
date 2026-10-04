import ast
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
DRAWER = ROOT / "assets" / "ming-app-drawer.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"


class StatusWidgetLayoutV2Tests(unittest.TestCase):
    def test_compact_geometry_respects_screen_margins_on_very_narrow_displays(self):
        source = PHONE.read_text(encoding="utf-8")
        prefix = source.split("\nimport gi\n", 1)[0]
        namespace = {"__file__": str(PHONE)}
        exec(compile(prefix, str(PHONE), "exec"), namespace)

        geometry = namespace["status_widget_compact_geometry"](
            {"width": 240, "height": 180})

        self.assertLessEqual(
            geometry["width"], 240 - 2 * 26)

    def test_expanded_panel_never_overlaps_capsule_when_screen_is_short(self):
        source = PHONE.read_text(encoding="utf-8")
        prefix = source.split("\nimport gi\n", 1)[0]
        namespace = {"__file__": str(PHONE)}
        exec(compile(prefix, str(PHONE), "exec"), namespace)

        pill = {"x": 24, "y": 8, "width": 240, "height": 58}
        screen = {"width": 320, "height": 180}
        geometry = namespace["status_widget_overlay_geometry"](
            pill, {"width": 330, "height": 220}, screen)
        panel = geometry["panel"]

        self.assertLessEqual(panel["width"], screen["width"])
        self.assertTrue(
            panel["y"] >= pill["y"] + pill["height"]
            or panel["y"] + panel["height"] <= pill["y"])
        self.assertGreaterEqual(panel["y"], 0)
        self.assertLessEqual(panel["y"] + panel["height"], screen["height"])

    def test_desktop_creation_avoids_collisions(self):
        source = PHONE.read_text(encoding="utf-8")
        prefix = source.split("\nimport gi\n", 1)[0]
        namespace = {"__file__": str(PHONE)}
        exec(compile(prefix, str(PHONE), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temporary:
            namespace["DESKTOP_DIR"] = pathlib.Path(temporary)
            first_file = namespace["create_blank_desktop_file"]()
            second_file = namespace["create_blank_desktop_file"]()
            folder = namespace["create_desktop_folder"]()
            self.assertEqual(first_file.name, "新建文件.txt")
            self.assertEqual(second_file.name, "新建文件 (2).txt")
            self.assertTrue(folder.is_dir())


class DrawerAndDesktopContracts(unittest.TestCase):
    def test_drawer_refreshes_all_visible_installed_entries_before_filtering(self):
        source = DRAWER.read_text(encoding="utf-8")
        start = source.index("    def refresh(self):")
        refresh = source[start:source.index("    def _activate_button", start)]
        self.assertIn("self.apps = discover_apps()", refresh)
        self.assertIn("filter_apps(self.apps", refresh)
        self.assertIn("dock_visible=False", source)

    def test_drawer_immersive_geometry_uses_monitor_bottom_when_dock_strut_lingers(self):
        source = DRAWER.read_text(encoding="utf-8")
        workarea = source[source.index("    def _workarea"):source.index("    def _build_window", source.index("    def _workarea"))]
        show = source[source.index("    def show(self):"):source.index("    def hide(self):")]
        self.assertIn("immersive=False", workarea)
        self.assertIn("monitor.get_geometry()", workarea)
        self.assertIn("drawer_geometry(self._workarea(immersive=True), dock_visible=False)", show)

    def test_blank_desktop_menu_has_add_and_create_actions(self):
        source = PHONE.read_text(encoding="utf-8")
        menu = source[source.index("    def show_desktop_context_menu"):source.index("    def refresh_desktop", source.index("    def show_desktop_context_menu"))]
        for marker in ("添加到桌面", "新建空白文件", "新建文件夹", "ming-app-drawer --toggle"):
            self.assertIn(marker, menu)
        for marker in ("create_blank_desktop_file", "create_desktop_folder"):
            self.assertIn(marker, source)

    def test_desktop_deploys_same_phone_desktop_for_live_skel_and_existing_user(self):
        source = DESKTOP.read_text(encoding="utf-8")
        finalize = FINALIZE.read_text(encoding="utf-8")
        self.assertIn("install -m 0755 \"${phone_desktop_src}\" /usr/local/bin/ming-phone-desktop", source)
        self.assertIn("seed_skel", finalize)
        self.assertIn("ming-phone-desktop --sync", source)
        self.assertIn("/usr/local/bin/ming-phone-desktop --sync", source)
        self.assertIn("/etc/skel/.config/autostart/ming-session-healthcheck.desktop", source)
        self.assertIn("${autostart_dir}/ming-desktop-sync-once.desktop", source)
        self.assertIn("/etc/skel/.config/autostart/ming-desktop-sync-once.desktop", source)


if __name__ == "__main__":
    unittest.main()
