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

    def test_volume_request_keeps_sink_snapshot_across_status_refresh(self):
        state = self.state_type()
        current_sink = {"sink_id": "sink-a", "sink_name": "A"}
        first = state.begin(35, target=current_sink)
        current_sink.update({"sink_id": "sink-b", "sink_name": "B"})

        self.assertEqual("sink-a", state.target_for(first)["sink_id"])
        self.assertEqual("A", state.target_for(first)["sink_name"])
        self.assertEqual("sink-b", current_sink["sink_id"])

        second = state.begin(45, target=current_sink)
        self.assertIsNone(state.target_for(first))
        self.assertEqual("sink-b", state.target_for(second)["sink_id"])

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

    def test_volume_control_targets_current_sink_and_displays_its_name(self):
        self.assertIn("self.volume_sink_id = default_sink", self.source)
        self.assertIn('selected_output.get("display_name")', self.source)
        self.assertIn('"sink_id": self.volume_sink_id', self.source)

    def test_volume_worker_and_result_use_request_sink_snapshot(self):
        status = self.source[self.source.index("class StatusWidget"):
                             self.source.index("class WallpaperCanvas")]
        self.assertIn("target_snapshot", status)
        self.assertIn("sink_id = target_snapshot.get(\"sink_id\")", status)
        self.assertIn("target_snapshot.get(\"sink_name\")", status)

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

    def test_metric_loader_uses_importable_runtime_and_primes_cpu_and_network(self):
        source = self.source
        paths = source[source.index("PERFORMANCE_STATUS_PATHS = ["):
                       source.index("]\n", source.index("PERFORMANCE_STATUS_PATHS = ["))]
        self.assertIn('Path("/usr/local/lib/ming-os/ming-performance-status.py")', paths)
        self.assertNotIn('Path("/usr/local/sbin/ming-performance-status")', paths)
        status = source[source.index("class StatusWidget"):
                        source.index("class WallpaperCanvas")]
        self.assertIn("METRIC_PRIME_INTERVAL_MS = 250", source)
        for marker in (
            "metric_generation",
            "prime_metric_sample",
            "等待下一次",
            "采样中",
        ):
            self.assertIn(marker, status)


if __name__ == "__main__":
    unittest.main()
