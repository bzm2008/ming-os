import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
DISPLAY_CONTROL = ROOT / "assets" / "ming-display-control.py"
DEVICE_CONTROL = ROOT / "assets" / "ming-device-control.py"
PHONE_SOURCE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
DESKTOP_SOURCE = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD_SOURCE = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


XRANDR_VERBOSE = """Screen 0: minimum 8 x 8, current 3200 x 1080, maximum 32767 x 32767
DP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis)
\tBrightness: 1.000
HDMI-1 connected 1280x720+1920+0 (normal left inverted right x axis y axis)
\tBrightness: 0.800
DP-2 disconnected (normal left inverted right x axis y axis)
"""

XRANDR_WRONG_READBACK = XRANDR_VERBOSE.replace("1.000", "0.700").replace("0.800", "0.700")
XRANDR_APPLIED = XRANDR_VERBOSE.replace("1.000", "0.500").replace("0.800", "0.500")
XRANDR_REORDERED_APPLIED = """Screen 0: minimum 8 x 8, current 3200 x 1080, maximum 32767 x 32767
HDMI-1 connected 1280x720+1920+0 (normal left inverted right x axis y axis)
\tBrightness: 0.500
DP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis)
\tBrightness: 0.500
"""


class SequenceRunner:
    def __init__(self, verbose_outputs, failed_output=None):
        self.verbose_outputs = list(verbose_outputs)
        self.failed_output = failed_output
        self.commands = []

    def __call__(self, argv):
        argv = list(argv)
        self.commands.append(argv)
        if argv == ["xrandr", "--verbose"]:
            output = self.verbose_outputs.pop(0) if self.verbose_outputs else XRANDR_VERBOSE
            if isinstance(output, tuple):
                return subprocess.CompletedProcess(argv, *output)
            return subprocess.CompletedProcess(argv, 0, output, "")
        if argv[:2] == ["xrandr", "--output"]:
            if self.failed_output and argv[2] == self.failed_output:
                return subprocess.CompletedProcess(argv, 1, "", "set failed")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 127, "", "not available")


class SoftwareBrightnessDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = load_module(DISPLAY_CONTROL, "ming_display_control_software_test")

    def test_no_x11_returns_explicit_unavailable_reason(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.display.DisplayController(runner=SequenceRunner([]))
            with mock.patch.dict(os.environ, {"DISPLAY": "", "HOME": temporary}, clear=False):
                exit_code = self.display.main(
                    ["software-status", "--json"], controller=controller, stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("xrandr-software", payload.get("backend"))
        self.assertEqual("unavailable", payload.get("state"))
        self.assertIn("DISPLAY", payload.get("error", ""))

    def test_set_updates_all_active_outputs_and_persists_readback(self):
        applied = XRANDR_VERBOSE.replace("1.000", "0.500").replace("0.800", "0.500")
        runner = SequenceRunner([XRANDR_VERBOSE, applied])
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / ".config" / "ming-os" / "software-brightness.json"
            controller = self.display.DisplayController(runner=runner)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":99", "HOME": temporary,
                "XDG_CONFIG_HOME": str(pathlib.Path(temporary) / ".config"),
            }, clear=False):
                result = controller.software_set(50)
                saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(50, result["value"])
        self.assertEqual(["DP-1", "HDMI-1"], result["outputs"])
        self.assertEqual(50, saved["value"])
        self.assertEqual(2, runner.commands.count(["xrandr", "--verbose"]))

    def test_status_reports_mixed_output_values_with_safe_numeric_value(self):
        controller = self.display.DisplayController(runner=SequenceRunner([XRANDR_VERBOSE]))
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
            result = controller.software_status()
        self.assertTrue(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertTrue(result["mixed"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])

    def test_parser_accepts_comma_decimal_brightness_defensively(self):
        parsed = self.display.parse_xrandr_brightness(
            XRANDR_VERBOSE.replace("1.000", "1,000"))
        values = {item["name"]: item.get("brightness") for item in parsed}
        self.assertEqual(1.0, values["DP-1"])

    def test_parser_excludes_connected_but_inactive_outputs(self):
        inactive = XRANDR_VERBOSE + "VGA-1 connected (normal left inverted right)\n\tBrightness: 0.300\n"
        parsed = self.display.parse_xrandr_brightness(inactive)
        self.assertNotIn("VGA-1", [item["name"] for item in parsed])

    def test_default_runner_forces_c_locale_for_xrandr_parsing(self):
        completed = subprocess.CompletedProcess(["xrandr", "--verbose"], 0, "", "")
        with mock.patch.object(self.display.subprocess, "run", return_value=completed) as run:
            self.display.DisplayController._default_runner(["xrandr", "--verbose"])
        self.assertEqual("C", run.call_args.kwargs["env"]["LC_ALL"])

    def test_partial_output_failure_rolls_back_each_output_to_its_own_value(self):
        runner = SequenceRunner([XRANDR_VERBOSE], failed_output="HDMI-1")
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / ".config" / "ming-os" / "software-brightness.json"
            controller = self.display.DisplayController(runner=runner)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":99", "HOME": temporary,
                "XDG_CONFIG_HOME": str(pathlib.Path(temporary) / ".config"),
            }, clear=False):
                result = controller.software_set(40)
        self.assertFalse(result["ok"])
        self.assertEqual("error", result["state"])
        self.assertIn("HDMI-1", result["error"])
        self.assertIn(["xrandr", "--output", "DP-1", "--brightness", "1"], runner.commands)
        self.assertFalse(state_path.exists())

    def test_partial_set_failure_returns_confirmed_post_rollback_values(self):
        restored = XRANDR_VERBOSE.replace("1.000", "0.900").replace("0.800", "0.600")
        runner = SequenceRunner([XRANDR_VERBOSE, restored], failed_output="HDMI-1")
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
            result = self.display.DisplayController(runner=runner).software_set(40)
        self.assertFalse(result["ok"])
        self.assertEqual(60, result["value"])
        self.assertEqual({"DP-1": 90, "HDMI-1": 60}, result["output_values"])
        self.assertEqual(2, runner.commands.count(["xrandr", "--verbose"]))

    def test_partial_set_failure_with_failed_rollback_readback_uses_before_values(self):
        runner = SequenceRunner([
            XRANDR_VERBOSE,
            (1, "", "post-rollback readback failed"),
        ], failed_output="HDMI-1")
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
            result = self.display.DisplayController(runner=runner).software_set(40)
        self.assertFalse(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])
        self.assertIn("无法确认回滚后的实际亮度", result["error"])
        self.assertEqual(2, runner.commands.count(["xrandr", "--verbose"]))

    def test_readback_mismatch_returns_values_read_after_rollback_for_active_outputs(self):
        runner = SequenceRunner([XRANDR_VERBOSE, XRANDR_WRONG_READBACK, XRANDR_VERBOSE])
        with tempfile.TemporaryDirectory() as temporary:
            state_path = pathlib.Path(temporary) / ".config" / "ming-os" / "software-brightness.json"
            controller = self.display.DisplayController(runner=runner)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":99", "HOME": temporary,
                "XDG_CONFIG_HOME": str(pathlib.Path(temporary) / ".config"),
            }, clear=False):
                result = controller.software_set(40)
        self.assertFalse(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])
        self.assertEqual(3, runner.commands.count(["xrandr", "--verbose"]))
        self.assertIn(["xrandr", "--output", "DP-1", "--brightness", "1"], runner.commands)
        self.assertIn(["xrandr", "--output", "HDMI-1", "--brightness", "0.8"], runner.commands)
        self.assertFalse(state_path.exists())

    def test_failed_post_rollback_readback_returns_safe_before_values(self):
        runner = SequenceRunner([
            XRANDR_VERBOSE,
            XRANDR_WRONG_READBACK,
            (1, "", "post-rollback readback failed"),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.display.DisplayController(runner=runner)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":99", "HOME": temporary,
                "XDG_CONFIG_HOME": str(pathlib.Path(temporary) / ".config"),
            }, clear=False):
                result = controller.software_set(40)
        self.assertFalse(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])
        self.assertIn("无法确认回滚后的实际亮度", result["error"])
        self.assertEqual(3, runner.commands.count(["xrandr", "--verbose"]))

    def test_save_failure_with_failed_rollback_readback_uses_before_values(self):
        runner = SequenceRunner([
            XRANDR_VERBOSE,
            XRANDR_APPLIED,
            (1, "", "post-save-rollback readback failed"),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.display.DisplayController(
                runner=runner, software_state_path=pathlib.Path(temporary))
            with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
                result = controller.software_set(50)
        self.assertFalse(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])
        self.assertIn("无法确认回滚后的实际亮度", result["error"])
        self.assertEqual(3, runner.commands.count(["xrandr", "--verbose"]))

    def test_output_reordering_does_not_turn_successful_readback_into_failure(self):
        runner = SequenceRunner([XRANDR_VERBOSE, XRANDR_REORDERED_APPLIED])
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.display.DisplayController(
                runner=runner,
                software_state_path=pathlib.Path(temporary) / "brightness.json")
            with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
                result = controller.software_set(50)
        self.assertTrue(result["ok"])
        self.assertEqual(50, result["value"])

    def test_reapply_wait_is_bounded_and_stops_as_soon_as_x11_is_ready(self):
        class Clock:
            def __init__(self):
                self.now = 0.0
                self.sleeps = []

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.sleeps.append(seconds)
                self.now += seconds

        clock = Clock()
        runner = SequenceRunner([
            (1, "", "X11 not ready"),
            (1, "", "X11 not ready"),
            XRANDR_VERBOSE,
            XRANDR_APPLIED,
        ])
        with tempfile.TemporaryDirectory() as temporary:
            preference = pathlib.Path(temporary) / "brightness.json"
            preference.write_text('{"value":50}', encoding="utf-8")
            controller = self.display.DisplayController(
                runner=runner, software_state_path=preference)
            controller.monotonic = clock.monotonic
            controller.sleeper = clock.sleep
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
                rc = self.display.main(
                    ["software-reapply", "--wait-seconds", "10", "--json"],
                    controller=controller, stdout=output)
        self.assertEqual(0, rc)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertLess(sum(clock.sleeps), 10)
        self.assertEqual(2, len(clock.sleeps))

    def test_reapply_wait_timeout_is_explicit_and_bounded(self):
        class Clock:
            def __init__(self):
                self.now = 0.0
                self.total = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.total += seconds
                self.now += seconds

        clock = Clock()
        runner = SequenceRunner([(1, "", "X11 not ready")] * 20)
        with tempfile.TemporaryDirectory() as temporary:
            preference = pathlib.Path(temporary) / "brightness.json"
            preference.write_text('{"value":50}', encoding="utf-8")
            controller = self.display.DisplayController(
                runner=runner, software_state_path=preference)
            controller.monotonic = clock.monotonic
            controller.sleeper = clock.sleep
            with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
                result = controller.software_reapply(wait_seconds=2)
        self.assertFalse(result["ok"])
        self.assertIn("等待 X11 就绪超时", result["error"])
        self.assertLessEqual(clock.total, 2)


class HardwareBrightnessPrecedenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_module(DEVICE_CONTROL, "ming_device_control_brightness_test")

    def test_hardware_failure_never_invokes_software_fallback(self):
        class Runner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv, timeout=8):
                self.commands.append(list(argv))
                if argv == ["brightnessctl", "-m"]:
                    return 1, "", "brightnessctl failed"
                return 1, "", "unexpected command"

        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            pathlib.Path(temporary, "intel_backlight").mkdir()
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name in {"brightnessctl", "ming-display-control"},
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertFalse(result.get("ok", result.get("available")))
        self.assertEqual("brightnessctl", result["backend"])
        self.assertNotIn("ming-display-control", " ".join(" ".join(c) for c in runner.commands))

    def test_no_hardware_delegates_status_to_user_session_display_helper(self):
        class Runner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv, timeout=8):
                self.commands.append(list(argv))
                if argv == ["/usr/local/bin/ming-display-control", "software-status", "--json"]:
                    return 0, json.dumps({
                        "ok": True, "available": True, "state": "ready",
                        "backend": "xrandr-software", "value": 75,
                        "outputs": ["DP-1"], "error": "",
                    }), ""
                return 1, "", "unexpected command"

        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "/usr/local/bin/ming-display-control",
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertTrue(result["available"])
        self.assertEqual("xrandr-software", result["backend"])
        self.assertEqual(75, result["value"])

    def test_reapply_brightness_cli_delegates_to_display_helper(self):
        class Runner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv, timeout=8):
                self.commands.append(list(argv))
                if argv == ["/usr/local/bin/ming-display-control", "software-reapply", "--json"]:
                    return 0, json.dumps({
                        "ok": True, "available": True, "state": "ready",
                        "backend": "xrandr-software", "value": 62, "error": "",
                    }), ""
                return 1, "", "unexpected command"

        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "/usr/local/bin/ming-display-control",
                backlight_root=pathlib.Path(temporary),
            )
            output = io.StringIO()
            rc = self.device.main(
                ["reapply-brightness", "--json"], controller=controller, stdout=output)
        self.assertEqual(0, rc)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(62, payload["value"])

    def test_reapply_wait_budget_is_forwarded_with_a_bounded_process_timeout(self):
        class Runner:
            def __init__(self):
                self.calls = []

            def __call__(self, argv, timeout=8):
                self.calls.append((list(argv), timeout))
                return 0, json.dumps({
                    "ok": True, "available": True, "state": "ready",
                    "backend": "xrandr-software", "value": 62, "error": "",
                }), ""

        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "/usr/local/bin/ming-display-control",
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.reapply_brightness(wait_seconds=10)
        self.assertTrue(result["ok"])
        self.assertEqual(([
            "/usr/local/bin/ming-display-control", "software-reapply",
            "--wait-seconds", "10", "--json",
        ], 12), runner.calls[0])

    def test_nonzero_helper_exit_cannot_claim_success_and_checks_exact_path(self):
        exact_path = "/custom/bin/ming-display-control"

        class Runner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv, timeout=8):
                self.commands.append(list(argv))
                return 2, json.dumps({
                    "ok": True, "available": True, "state": "ready", "value": 70,
                }), "helper failed"

        checked = []
        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda path: checked.append(path) or path == exact_path,
                display_control=exact_path,
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertFalse(result["ok"])
        self.assertIsNone(result["value"])
        self.assertEqual({}, result.get("output_values", {}))
        self.assertEqual([exact_path], checked)
        self.assertEqual([exact_path, "software-status", "--json"], runner.commands[0])

    def test_helper_success_requires_boolean_ok_and_typed_ready_fields(self):
        exact_path = "/custom/bin/ming-display-control"

        class Runner:
            def __call__(self, argv, timeout=8):
                return 0, json.dumps({
                    "ok": "true", "available": True, "state": "ready", "value": "70",
                }), ""

        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=Runner(), executable=lambda path: path == exact_path,
                display_control=exact_path,
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertIs(result["ok"], False)
        self.assertEqual("error", result["state"])


class SoftwareBrightnessIntegrationContracts(unittest.TestCase):
    def test_widget_labels_software_backend_and_keeps_confirmed_fallback(self):
        self.assertIn('self.brightness_backend = brightness.get("backend", "")', PHONE_SOURCE)
        self.assertIn('"软件亮度" if self.brightness_backend == "xrandr-software"', PHONE_SOURCE)
        self.assertIn("self.confirmed_value = None", PHONE_SOURCE)
        self.assertIn("fallback_value = value if value is not None else state.confirmed_value", PHONE_SOURCE)
        self.assertIn('"%s %d%%（设置失败）"', PHONE_SOURCE)

    def test_autostart_reapplies_after_the_x11_session_is_ready(self):
        self.assertIn("ming-software-brightness.desktop", DESKTOP_SOURCE)
        self.assertIn(
            "ming-device-control reapply-brightness --wait-seconds 10 --json",
            DESKTOP_SOURCE)
        self.assertIn("X-GNOME-Autostart-Delay=3", DESKTOP_SOURCE)

    def test_build_gate_requires_all_software_brightness_interfaces(self):
        gate = BUILD_SOURCE[BUILD_SOURCE.index('display_control = require_file('):]
        for marker in ("software-status", "software-set", "software-reapply",
                       "parse_xrandr_brightness"):
            self.assertIn(marker, gate)
        self.assertIn(
            '"software-set", "software-reapply", "--wait-seconds"', gate)

    def test_device_controller_no_longer_owns_software_brightness_state(self):
        device_source = DEVICE_CONTROL.read_text(encoding="utf-8")
        self.assertNotIn("self.software_brightness_path", device_source)
        self.assertNotIn("self.environment =", device_source)


if __name__ == "__main__":
    unittest.main()
