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
            controller = self.display.DisplayController(
                runner=SequenceRunner([]))
            with mock.patch.dict(os.environ, {"DISPLAY": "", "HOME": temporary}, clear=False):
                exit_code = self.display.main(
                    ["software-status", "--json"], controller=controller, stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("xrandr-software", payload.get("backend"))
        self.assertEqual("unavailable", payload.get("state"))
        self.assertIn("DISPLAY", payload.get("error", ""))

    def test_set_updates_all_active_outputs_and_persists_preference(self):
        runner = SequenceRunner([XRANDR_VERBOSE, XRANDR_VERBOSE.replace("1.000", "0.500").replace("0.800", "0.500")])
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
        self.assertEqual("xrandr-software", result["backend"])
        self.assertEqual(50, result["value"])
        self.assertEqual(["DP-1", "HDMI-1"], result["outputs"])
        self.assertEqual(
            ["xrandr", "--output", "DP-1", "--brightness", "0.5"], runner.commands[1])
        self.assertEqual(
            ["xrandr", "--output", "HDMI-1", "--brightness", "0.5"], runner.commands[2])
        self.assertEqual(50, saved["value"])

    def test_status_keeps_a_numeric_value_when_active_outputs_are_mixed(self):
        runner = SequenceRunner([XRANDR_VERBOSE])
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.display.DisplayController(runner=runner)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":99", "HOME": temporary,
                "XDG_CONFIG_HOME": str(pathlib.Path(temporary) / ".config"),
            }, clear=False):
                result = controller.software_status()
        self.assertTrue(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertTrue(result["mixed"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])

    def test_status_keeps_a_slider_safe_value_when_outputs_are_initially_mixed(self):
        controller = self.display.DisplayController(runner=SequenceRunner([XRANDR_VERBOSE]))
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=False):
            result = controller.software_status()
        self.assertTrue(result["ok"])
        self.assertEqual(80, result["value"])
        self.assertTrue(result["mixed"])
        self.assertEqual({"DP-1": 100, "HDMI-1": 80}, result["output_values"])

    def test_partial_output_failure_rolls_back_outputs_already_changed(self):
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
        self.assertEqual("xrandr-software", result["backend"])
        self.assertEqual("error", result["state"])
        self.assertIn("HDMI-1", result["error"])
        self.assertIn(
            ["xrandr", "--output", "DP-1", "--brightness", "1"], runner.commands)
        self.assertFalse(state_path.exists())


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
                runner=runner, executable=lambda name: name in {"brightnessctl", "ming-display-control"},
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertFalse(result["ok"] if "ok" in result else result["available"])
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
                        "ok": True,
                        "available": True,
                        "state": "ready",
                        "backend": "xrandr-software",
                        "value": 75,
                        "outputs": ["DP-1"],
                        "error": "",
                    }), ""
                return 1, "", "unexpected command"

        runner = Runner()
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name in {"ming-display-control"},
                backlight_root=pathlib.Path(temporary),
            )
            result = controller.brightness_status()
        self.assertTrue(result["available"])
        self.assertEqual("xrandr-software", result["backend"])
        self.assertEqual(75, result["value"])
        self.assertEqual(
            ["/usr/local/bin/ming-display-control", "software-status", "--json"], runner.commands[0])

    def test_reapply_brightness_cli_delegates_without_touching_hardware(self):
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
                executable=lambda name: name in {"ming-display-control"},
                backlight_root=pathlib.Path(temporary),
            )
            output = io.StringIO()
            rc = self.device.main(
                ["reapply-brightness", "--json"], controller=controller, stdout=output)
        self.assertEqual(0, rc)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual("xrandr-software", payload["backend"])
        self.assertEqual(62, payload["value"])
        self.assertEqual(
            ["/usr/local/bin/ming-display-control", "software-reapply", "--json"], runner.commands[0])


if __name__ == "__main__":
    unittest.main()
