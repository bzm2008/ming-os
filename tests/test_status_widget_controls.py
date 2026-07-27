"""Pure tests for the status-widget control request state machine."""

import ast
import json
import pathlib
import tempfile
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


def load_metric_functions():
    source = PHONE.read_text(encoding="utf-8")
    prefix = source.split("\nimport gi\n", 1)[0]
    namespace = {"__file__": str(PHONE)}
    exec(prefix, namespace)
    return namespace


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

    def test_resource_metric_button_and_modes_are_persistent(self):
        for marker in (
            "metric_mode",
            "resource_button",
            "read_resource_metric",
            "memory",
            "cpu",
            "network",
        ):
            self.assertIn(marker, self.source)

    def test_memory_metric_reads_proc_without_shell_commands(self):
        namespace = load_metric_functions()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "meminfo").write_text(
                "MemTotal:       1000 kB\nMemAvailable:    600 kB\n", encoding="ascii")
            result = namespace["read_resource_metric"]("memory", proc_root=root, now=10)
        self.assertTrue(result["available"])
        self.assertEqual(40.0, result["value"])
        self.assertEqual("%", result["unit"])

    def test_cpu_and_network_metrics_use_second_sample(self):
        namespace = load_metric_functions()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "stat").write_text("cpu 10 0 10 80 0 0 0 0\n", encoding="ascii")
            first = namespace["read_resource_metric"]("cpu", proc_root=root, now=1)
            (root / "stat").write_text("cpu 20 0 20 90 0 0 0 0\n", encoding="ascii")
            second = namespace["read_resource_metric"](
                "cpu", {"counters": [10, 0, 10, 80, 0, 0, 0, 0]}, proc_root=root, now=2)
            self.assertFalse(first["available"])
            self.assertTrue(second["available"])
            self.assertGreater(second["value"], 0)

            (root / "net").mkdir()
            (root / "net" / "route").write_text(
                "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
                "eth0 00000000 0101A8C0 0001 0 0 0 00000000 0 0 0\n", encoding="ascii")
            (root / "net" / "dev").write_text(
                "Inter-| Receive | Transmit\n"
                " eth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n", encoding="ascii")
            first_net = namespace["read_resource_metric"]("network", proc_root=root, now=3)
            (root / "net" / "dev").write_text(
                "Inter-| Receive | Transmit\n"
                " eth0: 300 0 0 0 0 0 0 0 500 0 0 0 0 0 0 0\n", encoding="ascii")
            second_net = namespace["read_resource_metric"](
                "network", {"interface": "eth0", "bytes": (100, 200), "sample_time": 3},
                proc_root=root, now=4)
        self.assertFalse(first_net["available"])
        self.assertTrue(second_net["available"])
        self.assertEqual("eth0", second_net["interface"])

    def test_collapsed_refresh_does_not_start_full_device_status_collection(self):
        refresh = self.source[self.source.index("    def refresh(self):"):
                         self.source.index("    def collect_status(self):")]
        self.assertIn("if self.collapsed:", refresh)
        self.assertNotIn("collect_status", refresh.split("if self.collapsed:", 1)[1].split("return True", 1)[0])

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

    def test_status_widget_geometry_stays_top_aligned_and_non_expanding(self):
        status = self.source[self.source.index("class StatusWidget"):
                             self.source.index("class WallpaperCanvas")]
        init = status[status.index("    def __init__(self):"):
                      status.index("    def preferred_height(self):")]
        for marker in (
            "self.set_valign(Gtk.Align.START)",
            "self.set_vexpand(False)",
            "box.set_valign(Gtk.Align.START)",
            "box.set_vexpand(False)",
            "expanded.set_valign(Gtk.Align.START)",
            "expanded.set_vexpand(False)",
            "box.pack_start(self.content_revealer, False, False, 0)",
            "self.pack_start(box, False, False, 0)",
        ):
            self.assertIn(marker, init)
        self.assertNotIn("self.add(box)", init)

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
