"""Pure tests for the status-widget control request state machine."""

import ast
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace


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


def load_status_method(name):
    tree = ast.parse(PHONE.read_text(encoding="utf-8"))
    status = next(item for item in tree.body
                  if isinstance(item, ast.ClassDef) and item.name == "StatusWidget")
    node = next(item for item in status.body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {"log": lambda *_args, **_kwargs: None}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[node], type_ignores=[])), str(PHONE), "exec"), namespace)
    return namespace[name]


class FakeStatusValue:
    def __init__(self):
        self.value = None
        self.draws = 0

    def set_value(self, value):
        self.value = value

    def queue_draw(self):
        self.draws += 1


class FakeStatusLabel:
    def __init__(self):
        self.text = ""

    def set_text(self, text):
        self.text = text


class ControlRequestStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PHONE.read_text(encoding="utf-8")
        cls.state_type = load_control_state() if "class ControlRequestState" in cls.source else None

    def test_control_state_class_is_declared(self):
        self.assertIn("class ControlRequestState", self.source)

    def test_missing_or_legacy_widget_state_defaults_to_collapsed_schema_v2(self):
        namespace = load_metric_functions()
        load_state = namespace["load_widget_state"]
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / "status-widget.json"
            self.assertEqual(
                {"schema_version": 2, "collapsed": True, "metric_mode": "memory"},
                load_state(state_path),
            )
            state_path.write_text(
                json.dumps({"collapsed": False, "metric_mode": "cpu"}),
                encoding="utf-8",
            )
            self.assertEqual(
                {"schema_version": 2, "collapsed": True, "metric_mode": "memory"},
                load_state(state_path),
            )

    def test_schema_v2_widget_state_round_trips_expanded_preference(self):
        namespace = load_metric_functions()
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / "status-widget.json"
            namespace["save_widget_state"](
                False, path=state_path, metric_mode="network")
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(2, persisted["schema_version"])
            self.assertEqual(
                {"schema_version": 2, "collapsed": False, "metric_mode": "network"},
                namespace["load_widget_state"](state_path),
            )

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

    def test_windows_and_ming_key_aliases_toggle_the_widget(self):
        namespace = load_metric_functions()
        namespace["Gdk"] = type("FakeGdk", (), {
            "KEY_Super_L": 1,
            "KEY_Super_R": 2,
            "KEY_Meta_L": 3,
            "KEY_Meta_R": 4,
            "KEY_Win_L": 5,
            "KEY_Win_R": 6,
        })
        is_toggle_key = namespace["is_status_widget_toggle_key"]

        for keyval in (1, 2, 3, 4, 5, 6):
            self.assertTrue(is_toggle_key(keyval))
        self.assertFalse(is_toggle_key(99))

    def test_unavailable_readback_preserves_last_confirmed_control_value(self):
        namespace = load_metric_functions()

        preserve = namespace["preserve_confirmed_control_value"]
        self.assertEqual(68, preserve(68, None, False, minimum=0))
        self.assertEqual(68, preserve(68, "invalid", True, minimum=0))
        self.assertEqual(72, preserve(68, 72, True, minimum=0))

    def test_async_control_readback_settles_and_displays_effective_value(self):
        apply_result = load_status_method("apply_control_result")
        state = self.state_type()
        generation = state.begin(80)
        widget = SimpleNamespace(
            control_states={"volume": state},
            updating_controls=False,
            volume_scale=FakeStatusValue(),
            volume_label=FakeStatusLabel(),
            brightness_scale=FakeStatusValue(),
            brightness_label=FakeStatusLabel(),
            brightness_backend="",
        )

        self.assertFalse(apply_result(
            widget, "volume", generation,
            {"ok": True, "value": 78, "backend": "wpctl"}))
        self.assertFalse(state.pending)
        self.assertEqual(78, state.confirmed_value)
        self.assertEqual(78, widget.volume_scale.value)
        self.assertEqual("音量 78%", widget.volume_label.text)
        self.assertGreater(widget.volume_scale.draws, 0)

    def test_async_control_failure_restores_confirmed_brightness_value(self):
        apply_result = load_status_method("apply_control_result")
        state = self.state_type()
        state.confirmed_value = 64
        generation = state.begin(82)
        widget = SimpleNamespace(
            control_states={"brightness": state},
            updating_controls=False,
            volume_scale=FakeStatusValue(),
            volume_label=FakeStatusLabel(),
            brightness_scale=FakeStatusValue(),
            brightness_label=FakeStatusLabel(),
            brightness_backend="brightnessctl",
        )

        self.assertFalse(apply_result(
            widget, "brightness", generation,
            {"ok": False, "value": 20, "backend": "brightnessctl", "error": "读回失败"}))
        self.assertFalse(state.pending)
        self.assertEqual(64, state.confirmed_value)
        self.assertEqual(64, widget.brightness_scale.value)
        self.assertIn("64%", widget.brightness_label.text)
        self.assertIn("设置失败", widget.brightness_label.text)

    def test_collapsed_resource_timer_stops_and_stale_callback_is_ignored(self):
        timer_method = load_status_method("refresh_resource_metric_timer")
        widget = SimpleNamespace(
            collapsed=True,
            metric_refreshing=True,
            refresh_resource_metric=lambda: True,
        )

        self.assertFalse(timer_method(widget))
        self.assertFalse(widget.metric_refreshing)

    def test_resource_sampling_does_not_overwrite_resource_action_label(self):
        source = self.source
        method = source[
            source.index("    def apply_resource_metrics"):
            source.index("    def animate_collapsed_state")
        ]
        self.assertNotIn("self.resource_label.set_text", method)

    def test_volume_failure_restores_last_confirmed_value(self):
        failure = self.source[
            self.source.index('if kind == "volume":', self.source.index("def apply_control_result")):
            self.source.index("self.updating_controls = False", self.source.index("def apply_control_result"))
        ]
        self.assertIn("fallback_value = state.confirmed_value", failure)
        self.assertIn("self.volume_scale.set_value(fallback_value)", failure)
        self.assertIn("state.settle(generation, fallback_value)", failure)

    def test_brightness_failure_prefers_last_confirmed_value_over_untrusted_result(self):
        failure = self.source[
            self.source.index('else:\n                self.brightness_backend', self.source.index("def apply_control_result")):
            self.source.index("self.updating_controls = False", self.source.index("def apply_control_result"))
        ]
        self.assertIn(
            "fallback_value = (state.confirmed_value if state.confirmed_value is not None else value)",
            failure,
        )

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

    def test_status_widget_destroy_releases_periodic_sources_and_popup(self):
        self.assertIn("def on_destroy", self.source)
        tree = ast.parse(self.source)
        status = next(item for item in tree.body
                      if isinstance(item, ast.ClassDef) and item.name == "StatusWidget")
        node = next(item for item in status.body
                    if isinstance(item, ast.FunctionDef) and item.name == "on_destroy")
        removed = []

        class FakeGLib:
            @staticmethod
            def source_remove(source):
                removed.append(source)

        class Popup:
            def __init__(self):
                self.destroyed = False

            def destroy(self):
                self.destroyed = True

        popup = Popup()
        widget = SimpleNamespace(
            _destroyed=False,
            metric_generation=4,
            metric_refreshing=True,
            _summary_timer_source=11,
            _resource_timer_source=12,
            _collapse_hide_source=13,
            _height_animation_source=14,
            expanded_window=popup,
        )
        namespace = {"GLib": FakeGLib}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[node], type_ignores=[])), str(PHONE), "exec"), namespace)
        self.assertFalse(namespace["on_destroy"](widget))
        self.assertTrue(widget._destroyed)
        self.assertFalse(widget.metric_refreshing)
        self.assertEqual(5, widget.metric_generation)
        self.assertEqual([11, 12, 13, 14], removed)
        self.assertTrue(popup.destroyed)

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
