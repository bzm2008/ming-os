"""Pure tests for the status-widget control request state machine."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = ROOT / "assets" / "ming-phone-desktop.py"


def load_control_state():
    tree = ast.parse(PHONE.read_text(encoding="utf-8"))
    node = next((item for item in tree.body
                 if isinstance(item, ast.ClassDef) and item.name == "ControlRequestState"), None)
    if node is None:
        raise AssertionError("ming-phone-desktop.py must define ControlRequestState")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(PHONE), "exec"), namespace)
    return namespace["ControlRequestState"]


class ControlRequestStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PHONE.read_text(encoding="utf-8")
        cls.state_type = load_control_state() if "class ControlRequestState" in cls.source else None

    def test_control_state_class_is_declared(self):
        self.assertIn("class ControlRequestState", self.source)

    def test_new_request_supersedes_old_response(self):
        if self.state_type is None:
            self.skipTest("ControlRequestState is not implemented yet")
        state = self.state_type()
        first = state.begin(30)
        second = state.begin(70)

        self.assertNotEqual(first, second)
        self.assertFalse(state.accepts(first))
        self.assertTrue(state.accepts(second))
        self.assertTrue(state.pending)
        self.assertEqual(70, state.optimistic_value)

    def test_latest_response_clears_pending_and_keeps_readback(self):
        if self.state_type is None:
            self.skipTest("ControlRequestState is not implemented yet")
        state = self.state_type()
        generation = state.begin(70)

        self.assertTrue(state.settle(generation, 68))
        self.assertFalse(state.pending)
        self.assertEqual(68, state.optimistic_value)
        self.assertFalse(state.should_hold_status())

    def test_stale_response_cannot_clear_pending_or_change_value(self):
        if self.state_type is None:
            self.skipTest("ControlRequestState is not implemented yet")
        state = self.state_type()
        first = state.begin(30)
        state.begin(70)

        self.assertFalse(state.settle(first, 31))
        self.assertTrue(state.pending)
        self.assertEqual(70, state.optimistic_value)
        self.assertTrue(state.should_hold_status())

    def test_status_widget_uses_generation_guard_and_repaints_readback(self):
        source = self.source
        for marker in (
            "self.control_states",
            "ControlRequestState()",
            "generation",
            "should_hold_status()",
            "queue_draw()",
        ):
            self.assertIn(marker, source)

    def test_scale_styles_include_visible_trough_slider_and_highlight(self):
        source = self.source
        for marker in (
            ".status-scale trough",
            ".status-scale highlight",
            ".status-scale fill",
            ".status-scale progress",
            ".status-scale slider",
        ):
            self.assertIn(marker, source)

    def test_status_container_does_not_capture_scale_pointer_events(self):
        status = self.source[self.source.index("class StatusWidget"):
                             self.source.index("class WallpaperCanvas")]
        self.assertIn("class StatusWidget(Gtk.Box):", status)
        self.assertNotIn("class StatusWidget(Gtk.EventBox):", status)
        self.assertNotIn("set_visible_window(False)", status)

    def test_scales_have_renderer_independent_value_indicator(self):
        self.assertIn("class StatusSlider(Gtk.EventBox):", self.source)
        slider = self.source[self.source.index("class StatusSlider"):
                             self.source.index("class ControlRequestState")]
        for marker in (
            "set_above_child(True)",
            "Gdk.EventMask.TOUCH_MASK",
            'connect("button-press-event"',
            'connect("motion-notify-event"',
            'connect("button-release-event"',
            'connect("touch-event"',
            "def on_draw",
            'emit("value-changed")',
        ):
            self.assertIn(marker, slider)
        status = self.source[self.source.index("class StatusWidget"):
                             self.source.index("class WallpaperCanvas")]
        self.assertIn("self.volume_scale = StatusSlider(0, 100)", status)
        self.assertIn("self.brightness_scale = StatusSlider(1, 100)", status)


if __name__ == "__main__":
    unittest.main()
