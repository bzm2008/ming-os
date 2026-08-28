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
    def test_expanded_panel_is_independent_from_fixed_compact_pill_geometry(self):
        source = PHONE.read_text(encoding="utf-8")
        self.assertIn("def status_widget_overlay_geometry", source)
        self.assertIn("Gtk.WindowTypeHint.POPUP_MENU", source)
        self.assertIn("expanded_window", source)
        self.assertIn("set_transient_for", source)
        prefix = source.split("\nimport gi\n", 1)[0]
        namespace = {"__file__": str(PHONE)}
        exec(compile(prefix, str(PHONE), "exec"), namespace)
        geometry = namespace["status_widget_overlay_geometry"](
            {"x": 1500, "y": 8, "width": 380, "height": 58},
            {"width": 320, "height": 220},
            {"width": 1920, "height": 1080},
        )
        self.assertEqual(
            {"x": 1500, "y": 8, "width": 380, "height": 58},
            geometry["pill"],
        )
        self.assertGreaterEqual(geometry["panel"]["y"], 0)

    def test_revealer_animation_is_visible_before_expand_and_finishes_before_hide(self):
        source = PHONE.read_text(encoding="utf-8")
        self.assertIn("def _complete_collapse", source)
        expanded = source[source.index("        else:\n            self.content_revealer.set_visible(True)"):source.index("        target_height =", source.index("        else:\n            self.content_revealer.set_visible(True)"))]
        self.assertLess(expanded.index("self.expanded_window.show_all()"), expanded.index("self.content_revealer.set_reveal_child(True)"))
        method = source[source.index("    def apply_collapsed_state"):source.index("    def position_expanded_window", source.index("    def apply_collapsed_state"))]
        self.assertIn("set_reveal_child(False)", method)
        self.assertIn("self.expanded_window.hide()", source[source.index("def _complete_collapse"):source.index("def position_expanded_window")])

    def test_compact_pill_keeps_only_required_status_fields(self):
        source = PHONE.read_text(encoding="utf-8")
        compact = source[source.index("self.compact_button"):source.index("header = Gtk.Box")]
        for marker in (
            "compact_time_label",
            "compact_date_label",
            "compact_wifi_icon",
            "compact_battery_icon",
            "compact_logo_image",
            "compact_arrow_label",
        ):
            self.assertIn(marker, compact)
        self.assertNotIn("compact_network_label", compact)

    def test_expanded_panel_matches_the_approved_three_column_preview(self):
        source = PHONE.read_text(encoding="utf-8")
        status = source[source.index("class StatusWidget"):source.index("class WallpaperCanvas")]
        self.assertIn(".status-expanded-panel {", source)
        expanded_css = source[
            source.index(".status-expanded-panel {"):
            source.index("}", source.index(".status-expanded-panel {"))
        ]
        for marker in ("background: #FFFFFF", "border-radius: 14px", "padding: 11px"):
            self.assertIn(marker, expanded_css)
        self.assertIn('expanded_panel.get_style_context().add_class("status-expanded-panel")', status)
        self.assertIn("self.expanded_window.add(expanded_panel)", status)
        self.assertIn('Gtk.Label(label="快速控制 · 资源状态")', status)
        for marker in (
            'actions.attach(self.wifi_button, 0, 0, 1, 1)',
            'actions.attach(self.bluetooth_button, 1, 0, 1, 1)',
            'actions.attach(self.notification_button, 2, 0, 1, 1)',
            'actions.attach(self.settings_button, 0, 1, 1, 1)',
            'actions.attach(self.display_button, 1, 1, 1, 1)',
            'actions.attach(self.power_button, 2, 1, 1, 1)',
        ):
            self.assertIn(marker, status)

    def test_expanded_panel_displays_all_three_resource_metrics_together(self):
        source = PHONE.read_text(encoding="utf-8")
        status = source[source.index("class StatusWidget"):source.index("class WallpaperCanvas")]
        self.assertIn("def sample_all(self):", source)
        self.assertIn("self.resource_labels = {}", status)
        for mode in ("memory", "cpu", "network"):
            self.assertIn('("%s",' % mode, status)
        self.assertIn("results = self.metric_sampler.sample_all()", status)
        self.assertIn("def apply_resource_metrics", status)
        self.assertIn("if self.collapsed or self.metric_refreshing:", status)

    def test_expanding_requests_an_immediate_resource_sample(self):
        source = PHONE.read_text(encoding="utf-8")
        method = source[source.index("    def set_collapsed"):source.index("    def apply_collapsed_state", source.index("    def set_collapsed"))]
        self.assertIn("if not self.collapsed:", method)
        self.assertIn("self.refresh_resource_metric()", method)

    def test_expanded_state_never_changes_compact_widget_allocation(self):
        source = PHONE.read_text(encoding="utf-8")
        start = source.index("    def apply_collapsed_state")
        method = source[start:source.index("    def position_expanded_window", start)]
        self.assertNotIn("self.set_size_request(-1, target_height)", method)
        self.assertIn(
            "self.set_size_request(-1, STATUS_WIDGET_COMPACT_HEIGHT)", method
        )

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
