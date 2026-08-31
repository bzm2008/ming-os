import pathlib
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


def cls_slice(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


if __name__ == "__main__":
    unittest.main()
