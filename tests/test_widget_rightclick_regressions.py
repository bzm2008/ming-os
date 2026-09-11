import ast
import pathlib
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = ROOT / "assets" / "ming-phone-desktop.py"


class StatusWidgetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PHONE.read_text(encoding="utf-8")
        cls.status = cls.source[
            cls.source.index("class StatusWidget"):
            cls.source.index("class WallpaperCanvas")
        ]

    def test_expanded_panel_has_one_header_and_never_reuses_compact_header(self):
        """The popup must not render a second clock/date/collapse row."""
        self.assertIn("expanded_header = Gtk.Box", self.status)
        self.assertIn("expanded.pack_start(expanded_header", self.status)
        self.assertNotIn("expanded.pack_start(header", self.status)

    def test_compact_capsule_has_a_fixed_nonexpanding_allocation(self):
        self.assertIn("self.compact_button.set_hexpand(False)", self.status)
        self.assertIn("self.compact_button.set_vexpand(False)", self.status)
        self.assertIn("self.compact_button.set_size_request", self.status)

    def test_popup_position_is_reapplied_after_realize_to_prevent_drift(self):
        self.assertIn(
            'self.expanded_window.connect("realize", self._on_expanded_window_realize)',
            self.status,
        )
        self.assertIn("def _on_expanded_window_realize", self.status)
        self.assertIn("GLib.idle_add(self.position_expanded_window)", self.status)


class DesktopContextMenuRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PHONE.read_text(encoding="utf-8")

    def test_context_menu_is_retained_until_deactivation(self):
        menu = cls_slice(
            self.source,
            "    def show_desktop_context_menu",
            "    def refresh_desktop",
        )
        self.assertIn("self._context_menu = menu", menu)
        self.assertIn('menu.connect("deactivate"', menu)
        self.assertIn("self._context_menu", self.source)

    def test_file_operations_return_structured_results_and_show_failures(self):
        self.assertIn("def _desktop_action_result", self.source)
        self.assertIn('"ok": False', self.source)
        self.assertIn("def _show_context_error", self.source)
        self.assertIn("self.last_context_result", self.source)

    def test_creation_callbacks_refresh_only_after_success(self):
        blank = cls_slice(
            self.source,
            "    def _create_blank_desktop_file",
            "    def _create_desktop_folder",
        )
        folder = cls_slice(
            self.source,
            "    def _create_desktop_folder",
            "    def refresh_desktop",
        )
        for method in (blank, folder):
            self.assertIn("result = self._desktop_action_result", method)
            self.assertIn("if result[\"ok\"]:", method)

    def test_right_click_does_not_move_tile_before_opening_context_menu(self):
        tree = ast.parse(self.source)
        desktop_node = next(node for node in tree.body
                            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop")
        names = {"on_fixed_button_press", "on_fixed_motion", "on_fixed_button_release"}
        methods = [node for node in desktop_node.body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"DRAG_THRESHOLD": 12}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(PHONE), "exec"), namespace)
        item = {"id": "terminal", "x": 0, "y": 0}
        previews, menus = [], []
        desktop = types.SimpleNamespace(
            fixed_press_item=None, fixed_press_origin=None, fixed_press_button=None,
            fixed_press_moved=False, drag_positions={},
            fixed_event_coords=lambda event: (event.x, event.y),
            item_at=lambda *_args: item, item_position=lambda _item: (0, 0),
            item_by_id=lambda item_id: item if item_id == item["id"] else None,
            preview_drag=lambda *args: previews.append(args),
            show_context_menu=lambda *args: menus.append(args),
        )
        event = types.SimpleNamespace(button=3, x=5, y=5, time=100)
        namespace["on_fixed_button_press"](desktop, None, event)
        event.x = 40
        namespace["on_fixed_motion"](desktop, None, event)
        namespace["on_fixed_button_release"](desktop, None, event)
        self.assertEqual([], previews, "right-click movement must not drag the tile")
        self.assertEqual([(item, event)], menus)


def cls_slice(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


if __name__ == "__main__":
    unittest.main()
