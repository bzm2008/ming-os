import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEVICE_CONTROL = ROOT / "assets" / "ming-device-control.py"
PHONE_DESKTOP = ROOT / "assets" / "ming-phone-desktop.py"
SETTINGS = ROOT / "assets" / "ming-settings.py"


def load_device_control():
    spec = importlib.util.spec_from_file_location("ming_device_control", DEVICE_CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def c_command(*command):
    return ("env", "LC_ALL=C", *command)


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, timeout=8):
        command = tuple(command)
        self.commands.append(command)
        response = self.responses.get(command)
        if response is None and command[:2] == ("env", "LC_ALL=C"):
            response = self.responses.get(command[2:])
        if response is None:
            response = (1, "", "not available")
        if isinstance(response, list):
            return response.pop(0)
        return response


class FakeInputRunner(FakeRunner):
    def __init__(self, responses):
        super().__init__(responses)
        self.inputs = []

    def __call__(self, command, input_text, timeout=8):
        self.inputs.append((tuple(command), input_text))
        return super().__call__(command, timeout=timeout)


class BinaryRecordingRunner(FakeRunner):
    """Simulate parecord writing raw PCM to the file supplied in argv."""

    def __call__(self, command, timeout=8):
        command = tuple(command)
        if "parecord" in command:
            self.commands.append(command)
            if command[-1].startswith("--"):
                return 1, "", "missing output file"
            pathlib.Path(command[-1]).write_bytes(b"\x00\xff" * 4096)
            return 124, "", ""
        return super().__call__(command, timeout=timeout)


class WifiClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_device_control()

    def classify(self, **overrides):
        snapshot = {
            "wifi_devices": [],
            "pci_output": "",
            "usb_output": "",
            "rfkill_output": "",
            "firmware_output": "",
            "network_error": "",
        }
        snapshot.update(overrides)
        return self.device.classify_wifi(**snapshot)

    def test_no_wireless_hardware_is_not_reported_as_driver_failure(self):
        status = self.classify()
        self.assertEqual("no_hardware", status["state"])
        self.assertFalse(status["present"])

    def test_detected_hardware_without_interface_is_driver_missing(self):
        status = self.classify(pci_output="02:00.0 Network controller: Intel 7260")
        self.assertEqual("driver_missing", status["state"])

    def test_firmware_error_has_a_distinct_state(self):
        status = self.classify(
            pci_output="02:00.0 Network controller: Intel 7260",
            firmware_output="iwlwifi: failed to load firmware",
        )
        self.assertEqual("firmware_missing", status["state"])

    def test_rfkill_block_wins_over_ready_interface(self):
        status = self.classify(
            wifi_devices=[("wlan0", "disconnected")],
            rfkill_output="Soft blocked: yes\nHard blocked: no",
        )
        self.assertEqual("rfkill_blocked", status["state"])

    def test_unblocked_networkmanager_interface_is_ready(self):
        status = self.classify(wifi_devices=[("wlan0", "disconnected")])
        self.assertEqual("ready", status["state"])
        self.assertTrue(status["present"])

    def test_ready_interface_ignores_unrelated_gpu_firmware_error(self):
        status = self.classify(
            wifi_devices=[("wlan0", "disconnected")],
            firmware_output="amdgpu: failed to load firmware",
        )
        self.assertEqual("ready", status["state"])

    def test_tp_link_rtl8821au_usb_adapter_is_wireless_hardware(self):
        usb = self.device.DeviceController._wireless_usb(
            "Bus 001 Device 004: ID 2357:011e TP-Link RTL8821AU USB Adapter")
        status = self.classify(usb_output=usb)
        self.assertIn("RTL8821AU", usb)
        self.assertEqual("driver_missing", status["state"])

    def test_usb_vendor_names_alone_do_not_imply_wireless_hardware(self):
        usb = self.device.DeviceController._wireless_usb(
            "Bus 001 Device 002: ID 0bda:0129 Realtek Semiconductor Corp. Card Reader\n"
            "Bus 001 Device 003: ID 0a5c:21e8 Broadcom Corp. Bluetooth Controller\n"
            "Bus 001 Device 004: ID 0e8d:2008 MediaTek Inc. Android Phone"
        )
        self.assertEqual("", usb)

    def test_unrelated_gpu_firmware_log_is_filtered_out(self):
        filtered = self.device.DeviceController._wireless_firmware(
            "amdgpu: failed to load firmware\n"
            "iwlwifi 0000:02:00.0: failed to load firmware iwlwifi-7260.ucode")
        self.assertNotIn("amdgpu", filtered)
        self.assertIn("iwlwifi", filtered)

    def test_probe_uses_absolute_rfkill_path(self):
        runner = FakeRunner({})
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        controller.wifi_status()
        self.assertIn(c_command("/usr/sbin/rfkill", "list", "wifi"), runner.commands)

    def test_failed_hardware_probes_are_diagnostic_unavailable_not_no_hardware(self):
        runner = FakeRunner({
            c_command("lspci", "-nnk"): (1, "", "lspci missing"),
            c_command("lsusb"): (1, "", "lsusb missing"),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        status = controller.wifi_status()
        self.assertEqual("diagnostic_unavailable", status["state"])
        self.assertIn("诊断", status["title"])

    def test_wifi_probes_force_c_locale_for_english_hardware_parsing(self):
        runner = FakeRunner({
            c_command("lspci", "-nnk"): (
                0, "02:00.0 Network controller: Intel 7260", ""),
            c_command("lsusb"): (0, "", ""),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        status = controller.wifi_status()
        self.assertEqual("driver_missing", status["state"])
        self.assertIn(c_command("lspci", "-nnk"), runner.commands)

    def test_b43_status_reports_required_firmware_file_and_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            firmware = pathlib.Path(directory)
            runner = FakeRunner({
                c_command("lspci", "-nnk"): (
                    0,
                    "02:00.0 Network controller [0280]: Broadcom BCM4322 [14e4:432b]\n"
                    "\tKernel driver in use: b43\n\tKernel modules: b43",
                    "",
                ),
                c_command("lsusb"): (0, "", ""),
            })
            controller = self.device.DeviceController(
                runner=runner, executable=lambda _name: True,
                firmware_root=firmware)

            missing = controller.wifi_status()
            self.assertEqual("pci:14e4:432b", missing["firmware"]["device_id"])
            self.assertEqual("b43", missing["firmware"]["driver"])
            self.assertEqual("not-bundled-no-redistribution-license",
                             missing["firmware"]["source"])
            self.assertEqual("E_FIRMWARE_MISSING", missing["firmware"]["error_code"])
            self.assertEqual(["b43/ucode30_mimo.fw"],
                             missing["firmware"]["required_files"])
            self.assertEqual("firmware_missing", missing["state"])

            target = firmware / "b43/ucode30_mimo.fw"
            target.parent.mkdir()
            target.write_bytes(b"firmware")
            present = controller.wifi_status()
            self.assertTrue(present["firmware"]["complete"])
            self.assertEqual("", present["firmware"]["error_code"])

    def test_suspicious_unknown_usb_network_adapter_needs_diagnosis(self):
        runner = FakeRunner({
            c_command("lspci", "-nnk"): (0, "", ""),
            c_command("lsusb"): (
                0, "Bus 001 Device 004: ID 1234:5678 Acme USB Network Adapter", ""),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        status = controller.wifi_status()
        self.assertEqual("diagnostic_unavailable", status["state"])
        self.assertIn("USB", status["detail"])


class DeviceControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_device_control()

    def test_volume_prefers_pactl_and_reads_back_effective_value(self):
        runner = FakeRunner({
            ("pactl", "set-sink-volume", "@DEFAULT_SINK@", "63%"): (0, "", ""),
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (
                0, "Volume: front-left: 41287 /  63% / -12.00 dB", ""),
        })
        controller = self.device.DeviceController(
            runner=runner,
            executable=lambda name: name in {"pactl", "amixer"},
        )
        result = controller.set_volume(63)
        self.assertTrue(result["ok"])
        self.assertEqual("pactl", result["backend"])
        self.assertEqual(63, result["value"])
        self.assertTrue(result["available"])
        self.assertEqual("ready", result["state"])
        self.assertEqual(63, result["requested"])
        self.assertTrue(0 <= result["value"] <= 100)

    def test_volume_without_usable_backend_is_explicitly_unavailable(self):
        controller = self.device.DeviceController(
            runner=FakeRunner({}), executable=lambda _name: False)

        result = controller.set_volume(63)

        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertEqual("unavailable", result["state"])
        self.assertEqual("", result["backend"])
        self.assertIsNone(result["value"])
        self.assertEqual(63, result["requested"])

    def test_widget_audio_performs_one_bounded_pulseaudio_recovery_then_reads_sink(self):
        calls = []
        info_calls = [
            (1, "", "Connection refused"),
            (0, "Default Sink: alsa_output.pci.analog-stereo\nDefault Source: alsa_input.pci.analog-stereo", ""),
        ]

        def runner(argv, timeout=8):
            calls.append((tuple(argv), timeout))
            if argv == ["pactl", "info"]:
                return info_calls.pop(0)
            if argv == ["pulseaudio", "--start"]:
                return 0, "", ""
            if argv == ["pactl", "get-sink-volume", "@DEFAULT_SINK@"]:
                return 0, "Volume: front-left: 50%", ""
            if argv == ["pactl", "list", "short", "sinks"]:
                return 0, "0\talsa_output.pci.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tRUNNING", ""
            if argv == ["pactl", "get-sink-mute", "@DEFAULT_SINK@"]:
                return 0, "Mute: no", ""
            return 1, "", "unexpected"

        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "pulseaudio"})
        status = controller.audio_widget_status()
        self.assertTrue(status["available"])
        self.assertEqual(50, status["value"])
        self.assertIn((("pulseaudio", "--start"), 3), calls)

    def test_volume_targets_valid_selected_sink_unmutes_and_reads_back(self):
        sink = "alsa_output.usb-Headset.analog-stereo"
        statuses = [{
            "backend": "pactl", "server_available": True,
            "playback_devices": [{"id": sink, "available": True, "active": False}],
        }]
        runner = FakeRunner({
            ("pactl", "set-sink-volume", sink, "67%"): (0, "", ""),
            ("pactl", "set-sink-mute", sink, "0"): (0, "", ""),
            ("pactl", "get-sink-volume", sink): (0, "Volume: 67%", ""),
            ("pactl", "get-sink-mute", sink): (0, "Mute: no", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: statuses.pop(0)

        result = controller.set_volume(67, sink_id=sink)

        self.assertTrue(result["ok"])
        self.assertEqual(sink, result["sink_id"])
        self.assertEqual(67, result["value"])
        self.assertFalse(result["muted"])
        self.assertIn(("pactl", "set-sink-mute", sink, "0"), runner.commands)

    def test_volume_rejects_sink_not_in_current_playback_devices(self):
        runner = FakeRunner({})
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: {
            "backend": "pactl", "server_available": True,
            "playback_devices": [{"id": "known", "available": True}],
        }

        result = controller.set_volume(20, sink_id="stale-or-injected")

        self.assertFalse(result["ok"])
        self.assertEqual("invalid_sink", result["state"])
        self.assertEqual([], runner.commands)

    def test_set_volume_cli_forwards_structured_sink_identifier(self):
        class VolumeController:
            def set_volume(self, value, sink_id=None):
                return {"ok": True, "value": value, "sink_id": sink_id, "muted": False}

        output = io.StringIO()
        rc = self.device.main(
            ["set-volume", "45", "--sink", "bluez_output.headset"],
            controller=VolumeController(), stdout=output)
        self.assertEqual(0, rc)
        self.assertEqual("bluez_output.headset", json.loads(output.getvalue())["sink_id"])

    def test_volume_falls_back_to_amixer_when_pactl_fails(self):
        runner = FakeRunner({
            ("pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"): (1, "", "no server"),
            ("amixer", "sset", "Master", "40%"): (0, "", ""),
            ("amixer", "sget", "Master"): (0, "Front Left: Playback 26 [40%] [on]", ""),
        })
        controller = self.device.DeviceController(
            runner=runner,
            executable=lambda name: name in {"pactl", "amixer"},
        )
        result = controller.set_volume(40)
        self.assertTrue(result["ok"])
        self.assertEqual("amixer", result["backend"])
        self.assertEqual(40, result["value"])

    def test_audio_status_reports_call_ready_input_and_duplex_profile(self):
        runner = FakeRunner({
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "info"): (
                0,
                "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\n"
                "Default Source: alsa_input.pci-0000_00_1f.3.analog-stereo",
                "",
            ),
            ("pactl", "list", "short", "sources"): (
                0, "42\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (
                0,
                "Card #42\nName: alsa_card.pci-0000_00_1f.3\nProfiles:\n"
                "\toutput:analog-stereo+input:analog-stereo: Analog Stereo Duplex (available: yes)\n"
                "Active Profile: output:analog-stereo+input:analog-stereo\n",
                "",
            ),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertTrue(status["available"])
        self.assertTrue(status["call_ready"])
        self.assertEqual("alsa_input.pci-0000_00_1f.3.analog-stereo", status["default_source"])
        self.assertTrue(status["physical_input_present"])
        self.assertTrue(status["duplex_profile_active"])
        self.assertFalse(status["input_muted"])
        self.assertFalse(status["output_muted"])

    def test_pactl_card_parser_accepts_real_indented_card_fields(self):
        cards = self.device.DeviceController._pactl_cards(
            "Card #42\n"
            "\tName: alsa_card.pci-0000_00_1f.3\n"
            "\tProfiles:\n"
            "\t\toutput:analog-stereo+input:analog-stereo: Analog Stereo Duplex (available: yes)\n"
            "\tActive Profile: output:analog-stereo+input:analog-stereo\n"
        )

        self.assertEqual("alsa_card.pci-0000_00_1f.3", cards[0]["name"])
        self.assertEqual(
            "output:analog-stereo+input:analog-stereo", cards[0]["active_profile"])
        self.assertEqual(
            [{"name": "output:analog-stereo+input:analog-stereo", "available": True}],
            cards[0]["profiles"],
        )

    def test_audio_status_reports_missing_pulseaudio_server_without_amixer_fallback(self):
        runner = FakeRunner({
            ("pactl", "info"): (1, "", "Connection failure: Connection refused"),
            ("amixer", "sget", "Master"): (0, "Front Left: Playback 40 [50%] [on]", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertEqual("no_server", status["state"])
        self.assertFalse(status["server_available"])
        self.assertFalse(status["playback_ready"])
        self.assertEqual("pactl", status["backend"])
        self.assertNotIn(("amixer", "sget", "Master"), runner.commands)

    def test_audio_status_reports_missing_default_sink(self):
        runner = FakeRunner({
            ("pactl", "info"): (0, "Default Sink: \nDefault Source: source", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertEqual("no_default_sink", status["state"])
        self.assertTrue(status["server_available"])
        self.assertFalse(status["default_sink_present"])
        self.assertFalse(status["playback_ready"])

    def test_audio_status_reports_muted_output_as_not_ready(self):
        runner = FakeRunner({
            ("pactl", "info"): (0, "Default Sink: sink\nDefault Source: source", ""),
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "list", "short", "sources"): (0, "1\tsource\tmodule\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: yes", ""),
            ("pactl", "list", "cards"): (
                0, "Card #1\nName: alsa_card.pci\nActive Profile: output:analog-stereo\n", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertEqual("muted", status["state"])
        self.assertTrue(status["default_sink_present"])
        self.assertTrue(status["playback_profile_valid"])
        self.assertFalse(status["playback_ready"])

    def test_audio_status_reports_invalid_active_playback_profile(self):
        runner = FakeRunner({
            ("pactl", "info"): (
                0, "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\nDefault Source: source", ""),
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "list", "short", "sources"): (0, "1\tsource\tmodule\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (
                0, "Card #1\n\tName: alsa_card.pci-0000_00_1f.3\n\tActive Profile: off\n", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertEqual("invalid_profile", status["state"])
        self.assertFalse(status["playback_profile_valid"])
        self.assertFalse(status["playback_ready"])

    def test_audio_status_exposes_selectable_playback_devices(self):
        runner = FakeRunner({
            ("pactl", "info"): (
                0, "Default Sink: bluez_output.00_11_22.a2dp-sink\nDefault Source: source", ""),
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "list", "short", "sinks"): (
                0,
                "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\ts16le\tSUSPENDED\n"
                "1\talsa_output.pci-0000_01_00.1.hdmi-stereo\tmodule-alsa-card.c\ts16le\tIDLE\n"
                "2\tbluez_output.00_11_22.a2dp-sink\tmodule-bluez5-device.c\ts16le\tRUNNING\n"
                "3\talsa_output.usb-Plantronics.analog-stereo\tmodule-alsa-card.c\ts16le\tIDLE",
                "",
            ),
            ("pactl", "list", "short", "sources"): (0, "1\tsource\tmodule\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (0, "Card #1\nName: bluez_card.00_11_22\nActive Profile: a2dp-sink\n", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        devices = controller.audio_status()["playback_devices"]

        self.assertEqual(
            ["internal", "hdmi", "bluetooth", "usb"], [item["kind"] for item in devices])
        self.assertEqual("bluez_output.00_11_22.a2dp-sink", devices[2]["id"])
        self.assertTrue(devices[2]["active"])
        self.assertTrue(all(item["available"] for item in devices))

    def test_audio_status_does_not_treat_an_unavailable_default_hdmi_sink_as_playback_ready(self):
        runner = FakeRunner({
            ("pactl", "info"): (
                0, "Default Sink: alsa_output.pci-0000_01_00.1.hdmi-stereo\nDefault Source: source", ""),
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "list", "short", "sinks"): (
                0, "0\talsa_output.pci-0000_01_00.1.hdmi-stereo\tmodule\ts16le\tUNAVAILABLE\n"
                "1\talsa_output.pci-0000_00_1f.3.analog-stereo\tmodule\ts16le\tIDLE", ""),
            ("pactl", "list", "short", "sources"): (0, "1\tsource\tmodule\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        status = controller.audio_status()

        self.assertFalse(status["playback_ready"])
        default = next(item for item in status["playback_devices"] if item["active"])
        self.assertFalse(default["available"])

    def test_audio_repair_playback_preserves_valid_user_selected_external_output(self):
        runner = FakeRunner({})
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: {
            "backend": "pactl", "server_available": True,
            "playback_ready": True, "default_sink_present": True,
            "default_sink": "bluez_output.00_11_22.a2dp-sink",
            "playback_devices": [{
                "id": "bluez_output.00_11_22.a2dp-sink", "kind": "bluetooth",
                "available": True, "active": True,
            }],
        }

        result = controller.audio_repair_playback()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("preserved_selected_output", result["action"])
        self.assertFalse(any(command[:2] == ("pactl", "set-default-sink") for command in runner.commands))

    def test_audio_repair_playback_restores_an_invalid_profile_on_the_active_sink(self):
        sink = "alsa_output.pci-0000_00_1f.3.analog-stereo"
        card = "alsa_card.pci-0000_00_1f.3"
        before = {
            "backend": "pactl", "server_available": True,
            "playback_ready": False, "default_sink_present": True,
            "playback_profile_valid": False, "output_muted": False,
            "default_sink": sink, "playback_devices": [{
                "id": sink, "kind": "internal", "available": True, "active": True,
            }],
            "cards": [{
                "name": card, "active_profile": "off",
                "profiles": [{"name": "output:analog-stereo", "available": True}],
            }],
        }
        after = dict(before, playback_ready=True, playback_profile_valid=True)
        statuses = [before, after]
        runner = FakeRunner({
            ("pactl", "set-card-profile", card, "output:analog-stereo"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: statuses.pop(0)

        result = controller.audio_repair_playback()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("repaired_active_profile", result["action"])
        self.assertIn(
            ("pactl", "set-card-profile", card, "output:analog-stereo"), runner.commands)

    def test_selecting_an_active_output_repairs_its_invalid_profile(self):
        sink = "alsa_output.pci-0000_00_1f.3.analog-stereo"
        card = "alsa_card.pci-0000_00_1f.3"
        before = {
            "backend": "pactl", "server_available": True,
            "playback_ready": False, "default_sink_present": True,
            "playback_profile_valid": False, "output_muted": False,
            "default_sink": sink, "playback_devices": [{
                "id": sink, "kind": "internal", "available": True, "active": True,
            }],
            "cards": [{
                "name": card, "active_profile": "off",
                "profiles": [{"name": "output:analog-stereo", "available": True}],
            }],
        }
        after = dict(before, playback_ready=True, playback_profile_valid=True)
        statuses = [before, before, after]
        runner = FakeRunner({
            ("pactl", "set-card-profile", card, "output:analog-stereo"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: statuses.pop(0)

        result = controller.audio_select_output(sink)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("repaired_active_output", result["action"])
        self.assertIn(
            ("pactl", "set-card-profile", card, "output:analog-stereo"), runner.commands)

    def test_audio_repair_playback_selects_internal_analog_only_when_no_output_is_valid(self):
        before = {
            "backend": "pactl", "server_available": True,
            "playback_ready": False, "default_sink_present": False,
            "default_sink": "", "playback_devices": [
                {"id": "alsa_output.pci-0000_01_00.1.hdmi-stereo", "kind": "hdmi", "available": True, "active": False},
                {"id": "alsa_output.pci-0000_00_1f.3.analog-stereo", "kind": "internal", "available": True, "active": False},
                {"id": "bluez_output.00_11_22.a2dp-sink", "kind": "bluetooth", "available": True, "active": False},
            ],
        }
        after = dict(before, playback_ready=True, default_sink_present=True,
                     default_sink="alsa_output.pci-0000_00_1f.3.analog-stereo")
        statuses = [before, after]
        runner = FakeRunner({
            ("pactl", "set-default-sink", "alsa_output.pci-0000_00_1f.3.analog-stereo"): (0, "", ""),
            ("pactl", "set-sink-mute", "alsa_output.pci-0000_00_1f.3.analog-stereo", "0"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: statuses.pop(0)

        result = controller.audio_repair_playback()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("selected_internal_output", result["action"])
        self.assertIn(
            ("pactl", "set-default-sink", "alsa_output.pci-0000_00_1f.3.analog-stereo"),
            runner.commands,
        )
        self.assertNotIn(
            ("pactl", "set-default-sink", "alsa_output.pci-0000_01_00.1.hdmi-stereo"),
            runner.commands,
        )

    def test_audio_repair_playback_restores_valid_persisted_user_output_before_internal(self):
        before = {
            "backend": "pactl", "server_available": True,
            "playback_ready": False, "default_sink_present": False,
            "default_sink": "", "playback_devices": [
                {"id": "alsa_output.pci-0000_01_00.1.hdmi-stereo", "kind": "hdmi", "available": True, "active": False},
                {"id": "alsa_output.pci-0000_00_1f.3.analog-stereo", "kind": "internal", "available": True, "active": False},
            ],
        }
        after = dict(before, playback_ready=True, default_sink_present=True,
                     default_sink="alsa_output.pci-0000_01_00.1.hdmi-stereo")
        statuses = [before, after]
        runner = FakeRunner({
            ("pactl", "set-default-sink", "alsa_output.pci-0000_01_00.1.hdmi-stereo"): (0, "", ""),
            ("pactl", "set-sink-mute", "alsa_output.pci-0000_01_00.1.hdmi-stereo", "0"): (0, "", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            settings_path = pathlib.Path(directory, "settings.json")
            settings_path.write_text(json.dumps({
                "audio_output_selection": "alsa_output.pci-0000_01_00.1.hdmi-stereo"}),
                encoding="utf-8")
            controller = self.device.DeviceController(
                runner=runner, executable=lambda name: name == "pactl",
                settings_path=settings_path)
            controller.audio_status = lambda: statuses.pop(0)

            result = controller.audio_repair_playback()

        self.assertTrue(result["ok"])
        self.assertEqual("restored_saved_output", result["action"])
        self.assertIn(
            ("pactl", "set-default-sink", "alsa_output.pci-0000_01_00.1.hdmi-stereo"),
            runner.commands,
        )
        self.assertNotIn(
            ("pactl", "set-default-sink", "alsa_output.pci-0000_00_1f.3.analog-stereo"),
            runner.commands,
        )

    def test_audio_select_output_uses_a_reported_available_sink_and_reads_back(self):
        before = {
            "backend": "pactl", "server_available": True,
            "default_sink": "alsa_output.pci-0000_00_1f.3.analog-stereo",
            "playback_devices": [
                {"id": "alsa_output.pci-0000_00_1f.3.analog-stereo", "kind": "internal", "available": True, "active": True},
                {"id": "alsa_output.pci-0000_01_00.1.hdmi-stereo", "kind": "hdmi", "available": True, "active": False},
            ],
        }
        after = dict(before, default_sink="alsa_output.pci-0000_01_00.1.hdmi-stereo",
                     playback_devices=[
                         dict(before["playback_devices"][0], active=False),
                         dict(before["playback_devices"][1], active=True),
                     ])
        statuses = [before, after]
        runner = FakeRunner({
            ("pactl", "set-default-sink", "alsa_output.pci-0000_01_00.1.hdmi-stereo"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "pactl")
        controller.audio_status = lambda: statuses.pop(0)

        result = controller.audio_select_output("alsa_output.pci-0000_01_00.1.hdmi-stereo")

        self.assertTrue(result["ok"])
        self.assertEqual("alsa_output.pci-0000_01_00.1.hdmi-stereo", result["selected"])
        self.assertIn(
            ("pactl", "set-default-sink", "alsa_output.pci-0000_01_00.1.hdmi-stereo"),
            runner.commands,
        )

    def test_audio_select_output_cli_uses_structured_device_identifier(self):
        class Selector:
            def __init__(self):
                self.selected = ""

            def audio_select_output(self, output_id):
                self.selected = output_id
                return {"ok": True, "selected": output_id}

        selector = Selector()
        output = io.StringIO()

        rc = self.device.main(
            ["audio-select-output", "--id", "alsa_output.pci.internal"],
            controller=selector, stdout=output)

        self.assertEqual(0, rc)
        self.assertEqual("alsa_output.pci.internal", selector.selected)
        self.assertEqual("alsa_output.pci.internal", json.loads(output.getvalue())["selected"])

    def test_call_audio_repair_selects_internal_duplex_only_when_input_is_missing(self):
        card_before = (
            "Card #42\nName: alsa_card.pci-0000_00_1f.3\nProfiles:\n"
            "\toutput:analog-stereo+input:analog-stereo: Analog Stereo Duplex (available: yes)\n"
            "Active Profile: output:analog-stereo\n"
        )
        card_after = card_before.replace(
            "Active Profile: output:analog-stereo",
            "Active Profile: output:analog-stereo+input:analog-stereo")
        source = "42\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\tRUNNING"
        runner = FakeRunner({
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): [(0, "Volume: 50%", "")] * 2,
            ("pactl", "info"): [
                (0, "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\nDefault Source:", ""),
                (0, "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\nDefault Source: alsa_input.pci-0000_00_1f.3.analog-stereo", ""),
            ],
            ("pactl", "list", "short", "sources"): [(0, "", ""), (0, source, ""), (0, source, "")],
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): [(0, "Mute: no", "")] * 2,
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): [(0, "Mute: no", "")] * 2,
            ("pactl", "list", "cards"): [(0, card_before, ""), (0, card_after, "")],
            ("pactl", "set-card-profile", "alsa_card.pci-0000_00_1f.3",
             "output:analog-stereo+input:analog-stereo"): (0, "", ""),
            ("pactl", "set-default-source", "alsa_input.pci-0000_00_1f.3.analog-stereo"): (0, "", ""),
            ("pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"): (0, "", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        result = controller.audio_repair_call()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("set_duplex_profile", result["action"])
        self.assertIn(
            ("pactl", "set-card-profile", "alsa_card.pci-0000_00_1f.3",
             "output:analog-stereo+input:analog-stereo"), runner.commands)
        self.assertIn(
            ("pactl", "set-default-source", "alsa_input.pci-0000_00_1f.3.analog-stereo"),
            runner.commands)

    def test_call_audio_repair_preserves_existing_external_input(self):
        runner = FakeRunner({
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "info"): (
                0, "Default Sink: bluez_output.00_11_22\nDefault Source: alsa_input.usb-Plantronics", ""),
            ("pactl", "list", "short", "sources"): (
                0, "42\talsa_input.usb-Plantronics\tmodule-alsa-card.c\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (0, "Card #1\nName: bluez_card.00_11_22\nActive Profile: a2dp-sink\n", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})

        result = controller.audio_repair_call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("preserved_existing_input", result["action"])
        self.assertFalse(any(command[:2] == ("pactl", "set-card-profile") for command in runner.commands))

    def test_microphone_test_accepts_three_second_pulseaudio_capture(self):
        info = (
            "Default Sink: alsa_output.pci-0000_00_1f.3.analog-stereo\n"
            "Default Source: alsa_input.pci-0000_00_1f.3.analog-stereo")
        source = "42\talsa_input.pci-0000_00_1f.3.analog-stereo\tmodule-alsa-card.c\tRUNNING"
        cards = (
            "Card #42\nName: alsa_card.pci-0000_00_1f.3\nProfiles:\n"
            "\toutput:analog-stereo+input:analog-stereo: Analog Stereo Duplex (available: yes)\n"
            "Active Profile: output:analog-stereo+input:analog-stereo\n")
        record_command = (
            "timeout", "3", "parecord", "--raw", "--format=s16le", "--rate=16000",
            "--channels=1", "--device=@DEFAULT_SOURCE@")
        runner = BinaryRecordingRunner({
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): [(0, "Volume: 50%", "")] * 2,
            ("pactl", "info"): [(0, info, "")] * 2,
            ("pactl", "list", "short", "sources"): [(0, source, "")] * 2,
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): [(0, "Mute: no", "")] * 2,
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): [(0, "Mute: no", "")] * 2,
            ("pactl", "list", "cards"): [(0, cards, "")] * 2,
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer", "parecord"})

        result = controller.audio_test_input()

        self.assertTrue(result["ok"])
        self.assertEqual(3, result["seconds"])
        self.assertGreaterEqual(result["bytes"], 4096)
        record_commands = [command for command in runner.commands if "parecord" in command]
        self.assertEqual(1, len(record_commands))
        self.assertEqual(record_command, record_commands[0][:-1])

    def test_audio_status_cli_emits_json_for_settings(self):
        runner = FakeRunner({
            ("pactl", "get-sink-volume", "@DEFAULT_SINK@"): (0, "Volume: 50%", ""),
            ("pactl", "info"): (0, "Default Sink: sink\nDefault Source: source", ""),
            ("pactl", "list", "short", "sources"): (0, "1\tsource\tmodule\tRUNNING", ""),
            ("pactl", "get-source-mute", "@DEFAULT_SOURCE@"): (0, "Mute: no", ""),
            ("pactl", "get-sink-mute", "@DEFAULT_SINK@"): (0, "Mute: no", ""),
            ("pactl", "list", "cards"): (0, "Card #1\nName: alsa_card.pci\nActive Profile: output:analog-stereo+input:analog-stereo", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name in {"pactl", "amixer"})
        output = io.StringIO()

        rc = self.device.main(["audio-status", "--json"], controller=controller, stdout=output)

        self.assertEqual(0, rc)
        payload = json.loads(output.getvalue())
        self.assertIn("call_ready", payload)
        self.assertEqual("pactl", payload["backend"])

    def test_brightness_without_sysfs_device_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.device.DeviceController(
                runner=FakeRunner({}),
                executable=lambda _name: True,
                backlight_root=pathlib.Path(directory),
            )
            result = controller.set_brightness(50)
        self.assertFalse(result["ok"])
        self.assertEqual("xrandr-software", result["backend"])
        self.assertIn("available", result["error"])

    def test_brightness_rejects_zero_before_running_a_command(self):
        runner = FakeRunner({})
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, "intel_backlight").mkdir()
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "brightnessctl",
                backlight_root=pathlib.Path(directory),
            )
            result = controller.set_brightness(0)
        self.assertFalse(result["ok"])
        self.assertIn("between 1 and 100", result["error"])
        self.assertEqual([], runner.commands)

    def test_brightness_is_read_back_after_setting(self):
        runner = FakeRunner({
            ("brightnessctl", "set", "72%"): (0, "", ""),
            ("brightnessctl", "-m"): (0, "intel_backlight,backlight,720,1000,72%", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, "intel_backlight").mkdir()
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "brightnessctl",
                backlight_root=pathlib.Path(directory),
            )
            result = controller.set_brightness(72)
        self.assertTrue(result["ok"])
        self.assertEqual(72, result["value"])
        self.assertTrue(result["available"])
        self.assertEqual("ready", result["state"])
        self.assertEqual("brightnessctl", result["backend"])
        self.assertEqual(72, result["requested"])

    def test_brightness_readback_is_bounded_and_result_shape_is_stable(self):
        runner = FakeRunner({
            ("brightnessctl", "set", "72%"): (0, "", ""),
            ("brightnessctl", "-m"): (
                0, "intel_backlight,backlight,1200,1000,120%", ""),
        })
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, "intel_backlight").mkdir()
            controller = self.device.DeviceController(
                runner=runner,
                executable=lambda name: name == "brightnessctl",
                backlight_root=pathlib.Path(directory),
            )

            result = controller.set_brightness(72)

        self.assertTrue(result["ok"])
        self.assertEqual(100, result["value"])
        self.assertTrue(0 <= result["value"] <= 100)
        self.assertTrue(result["available"])
        self.assertEqual("ready", result["state"])
        self.assertEqual("brightnessctl", result["backend"])
        self.assertEqual(72, result["requested"])
        self.assertEqual(
            {"ok", "available", "state", "backend", "requested", "value", "error"},
            set(result),
        )

    def test_brightness_without_hardware_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.device.DeviceController(
                runner=FakeRunner({}),
                executable=lambda _name: True,
                backlight_root=pathlib.Path(directory),
            )

            result = controller.set_brightness(50)

        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertIn(result["state"], {"unavailable", "error"})
        self.assertEqual("xrandr-software", result["backend"])
        self.assertIsNone(result["value"])
        self.assertEqual(50, result["requested"])

    def test_status_json_cli_has_stable_sections(self):
        output = io.StringIO()
        controller = self.device.DeviceController(runner=FakeRunner({}), executable=lambda _name: False)
        rc = self.device.main(["status", "--json"], controller=controller, stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(0, rc)
        self.assertEqual(
            {"audio", "brightness", "wifi", "bluetooth", "ethernet", "battery"},
            set(payload))

    def test_battery_prefers_display_device_over_bluetooth_peripheral(self):
        devices = (
            "/org/freedesktop/UPower/devices/headset_dev_AA_BB\n"
            "/org/freedesktop/UPower/devices/battery_BAT0\n"
            "/org/freedesktop/UPower/devices/DisplayDevice"
        )
        runner = FakeRunner({
            ("upower", "-e"): (0, devices, ""),
            ("upower", "-i", "/org/freedesktop/UPower/devices/DisplayDevice"):
                (0, "percentage: 81%", ""),
        })
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "upower")
        status = controller.battery_status()
        self.assertTrue(status["available"])
        self.assertEqual(81, status["value"])
        self.assertNotIn(
            ("upower", "-i", "/org/freedesktop/UPower/devices/headset_dev_AA_BB"),
            runner.commands,
        )


class WifiCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_device_control()

    def test_wifi_radio_cli_forwards_structured_status_and_requested_state(self):
        calls = []

        class RadioController:
            def wifi_radio_status(self):
                calls.append(("status",))
                return self.device_result(True)

            def wifi_radio(self, enabled):
                calls.append(("set", enabled))
                return self.device_result(True, enabled=enabled)

            @staticmethod
            def device_result(ok, enabled=True):
                return {
                    "ok": ok,
                    "state": "enabled" if enabled else "disabled",
                    "reason_code": "enabled" if enabled else "disabled",
                    "reason_text": "ok",
                    "retryable": False,
                    "enabled": enabled,
                }

        status_output = io.StringIO()
        self.assertEqual(
            0,
            self.device.main(
                ["wifi-radio-status", "--json"],
                controller=RadioController(), stdout=status_output),
        )
        on_output = io.StringIO()
        self.assertEqual(
            0,
            self.device.main(
                ["wifi-radio", "on", "--json"],
                controller=RadioController(), stdout=on_output),
        )
        self.assertEqual([("status",), ("set", True)], calls)
        self.assertTrue(json.loads(on_output.getvalue())["enabled"])

    def test_wifi_radio_fallback_uses_utf8_c_locale_and_reads_back(self):
        command = ("env", "LC_ALL=C.UTF-8", "nmcli", "radio", "wifi")
        runner = FakeRunner({command: (0, "enabled\n", "")})
        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "nmcli")
        result = controller.wifi_radio_status()
        self.assertTrue(result["ok"])
        self.assertTrue(result["enabled"])
        self.assertEqual([command], runner.commands)

    def test_scan_keeps_same_ssid_on_two_bssids_and_bands(self):
        scan_command = (
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        )
        runner = FakeRunner({
            scan_command: (
                0,
                "*:AA\\:AA\\:AA\\:AA\\:AA\\:01:Cafe:1:2412 MHz:80:WPA2:wlan0\n"
                ":BB\\:BB\\:BB\\:BB\\:BB\\:02:Cafe:36:5180 MHz:70:WPA2:wlan1",
                "",
            ),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        output = io.StringIO()
        try:
            rc = self.device.main(["wifi-scan", "--json"], controller=controller, stdout=output)
        except SystemExit as exc:
            rc = exc.code
        self.assertEqual(0, rc)
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(result["networks"]))
        self.assertEqual(["2.4GHz", "5GHz"], [item["band"] for item in result["networks"]])
        self.assertEqual(["AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"],
                         [item["bssid"] for item in result["networks"]])

    def test_connect_uses_selected_bssid_and_interface_without_public_password(self):
        command = (
            "nmcli", "--wait", "30", "device", "wifi", "connect", "Office", "bssid",
            "AA:BB:CC:DD:EE:FF", "ifname", "wlan1",
        )
        runner = FakeRunner({command: (0, "Device 'wlan1' successfully activated", "")})
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        output = io.StringIO()
        rc = self.device.main([
            "wifi-connect", "--ssid", "Office", "--bssid", "AA:BB:CC:DD:EE:FF",
            "--ifname", "wlan1",
        ], controller=controller, stdout=output)
        self.assertEqual(0, rc)
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual("AA:BB:CC:DD:EE:FF", result["bssid"])
        self.assertEqual("wlan1", result["ifname"])
        self.assertEqual([command], runner.commands)
        self.assertNotIn("password", output.getvalue().lower())

    def test_network_id_connection_uses_raw_chinese_ssid_not_bssid_as_ssid(self):
        scan_command = (
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        )
        ssid = "办公室网络"
        bssid = "AA:BB:CC:DD:EE:FF"
        connect_command = (
            "env", "LC_ALL=C.UTF-8", "nmcli", "--wait", "30", "device", "wifi",
            "connect", ssid, "bssid", bssid, "ifname", "wlan0",
        )
        runner = FakeRunner({
            scan_command: (0, ":AA\\:BB\\:CC\\:DD\\:EE\\:FF:%s:6:2437 MHz:75:WPA2:wlan0" % ssid, ""),
            connect_command: (0, "Device activated", ""),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        # Exercise the documented nmcli fallback rather than the host's libnm.
        controller._network_backend_checked = True

        scan = controller.wifi_scan()
        result = controller.wifi_connect(
            network_id=scan["networks"][0]["network_id"], ifname="wlan0")

        self.assertTrue(result["ok"])
        self.assertIn(connect_command, runner.commands)

    def test_password_option_is_rejected_without_echoing_the_secret(self):
        password = "correct horse battery staple"
        runner = FakeRunner({})
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        output = io.StringIO()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self.device.main([
                "wifi-connect", "--ssid", "Office", "--bssid", "AA:BB:CC:DD:EE:FF",
                "--ifname", "wlan1", "--password", password,
            ], controller=controller, stdout=output)
        self.assertEqual(2, rc)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(password, stderr.getvalue())
        self.assertEqual([], runner.commands)

    def test_password_stdin_uses_nmcli_ask_without_secret_in_argv_or_json(self):
        command = (
            "nmcli", "--ask", "--wait", "30", "device", "wifi", "connect", "Office",
            "bssid", "AA:BB:CC:DD:EE:FF", "ifname", "wlan0",
        )
        input_runner = FakeInputRunner({command: (0, "Device activated", "")})
        controller = self.device.DeviceController(
            runner=FakeRunner({}), input_runner=input_runner,
            executable=lambda _name: True)
        output = io.StringIO()
        secret = "correct horse battery staple"

        rc = self.device.main(
            ["wifi-connect", "--ssid", "Office", "--bssid", "AA:BB:CC:DD:EE:FF",
             "--ifname", "wlan0", "--password-stdin"],
            controller=controller, stdout=output, stdin=io.StringIO(secret + "\n"))

        self.assertEqual(0, rc)
        self.assertEqual([(command, secret + "\n")], input_runner.inputs)
        self.assertNotIn(secret, repr(input_runner.commands))
        self.assertNotIn(secret, output.getvalue())

    def test_scan_orders_active_then_signal_then_bssid_deterministically(self):
        command = (
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        )
        runner = FakeRunner({
            command: (
                0,
                ":CC\\:CC\\:CC\\:CC\\:CC\\:03:Guest:1:2412 MHz:40:WPA2:wlan0\n"
                ":BB\\:BB\\:BB\\:BB\\:BB\\:02:Guest:36:5180 MHz:80:WPA2:wlan1\n"
                "*:CC\\:CC\\:CC\\:CC\\:CC\\:04:Guest:149:5745 MHz:10:WPA2:wlan2\n"
                ":AA\\:AA\\:AA\\:AA\\:AA\\:01:Guest:11:2462 MHz:80:WPA2:wlan0",
                "",
            ),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        result = controller.wifi_scan()
        self.assertEqual(
            [
                "CC:CC:CC:CC:CC:04",
                "AA:AA:AA:AA:AA:01",
                "BB:BB:BB:BB:BB:02",
                "CC:CC:CC:CC:CC:03",
            ],
            [network["bssid"] for network in result["networks"]],
        )

    def test_connect_failure_emits_json_and_exit_code_two(self):
        command = (
            "nmcli", "--wait", "30", "device", "wifi", "connect", "Office", "bssid",
            "AA:BB:CC:DD:EE:FF", "ifname", "wlan1",
        )
        password = "correct horse battery staple"
        runner = FakeRunner({
            command: (10, "", "password %s was rejected" % password),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        output = io.StringIO()
        rc = self.device.main([
            "wifi-connect", "--ssid", "Office", "--bssid", "AA:BB:CC:DD:EE:FF",
            "--ifname", "wlan1",
        ], controller=controller, stdout=output)
        self.assertEqual(2, rc)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("NetworkManager", payload["error"])
        self.assertNotIn(password, output.getvalue())
        self.assertFalse(any(password in argument for command in runner.commands for argument in command))

    def test_connect_rejects_unsafe_targets_without_running_nmcli(self):
        targets = [
            ("-hidden", "AA:BB:CC:DD:EE:FF", "wlan0"),
            ("x" * 33, "AA:BB:CC:DD:EE:FF", "wlan0"),
            ("Office", "not-a-bssid", "wlan0"),
            ("Office", "AA:BB:CC:DD:EE:FF", "wlan0;rm"),
        ]
        for ssid, bssid, ifname in targets:
            with self.subTest(ssid=ssid, bssid=bssid, ifname=ifname):
                runner = FakeRunner({})
                controller = self.device.DeviceController(
                    runner=runner, executable=lambda _name: True)
                result = controller.wifi_connect(ssid, bssid, ifname)
                self.assertFalse(result["ok"])
                self.assertEqual([], runner.commands)

    def test_invalid_connect_target_emits_json_and_exit_code_two(self):
        runner = FakeRunner({})
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        output = io.StringIO()
        rc = self.device.main([
            "wifi-connect", "--ssid", "Office", "--bssid", "invalid", "--ifname", "wlan0",
        ], controller=controller, stdout=output)
        self.assertEqual(2, rc)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertEqual([], runner.commands)

    def test_empty_scan_reports_hardware_diagnostic_state(self):
        scan = c_command(
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        )
        runner = FakeRunner({
            scan: (0, "", ""),
            c_command("lspci", "-nnk"): (1, "", "lspci missing"),
            c_command("lsusb"): (1, "", "lsusb missing"),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        result = controller.wifi_scan()
        self.assertFalse(result["ok"])
        self.assertIn("state", result)
        self.assertEqual("diagnostic_unavailable", result["state"])
        self.assertEqual([], result["networks"])

    def test_empty_scan_reports_no_hardware_state_after_successful_probes(self):
        scan = c_command(
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        )
        runner = FakeRunner({
            scan: (0, "", ""),
            c_command("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"):
                (0, "", ""),
            c_command("lspci", "-nnk"): (0, "", ""),
            c_command("lsusb"): (0, "", ""),
        })
        controller = self.device.DeviceController(runner=runner, executable=lambda _name: True)
        result = controller.wifi_scan()
        self.assertFalse(result["ok"])
        self.assertIn("state", result)
        self.assertEqual("no_hardware", result["state"])


class BluetoothStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_device_control()

    def controller_for(self, responses):
        return self.device.DeviceController(
            runner=FakeRunner(responses), executable=lambda _name: True)

    @staticmethod
    def bluetooth_usb():
        return "Bus 001 Device 003: ID 0a5c:21e8 Broadcom Corp. Bluetooth Controller"

    def test_service_stopped_is_distinct_from_missing_hardware(self):
        stopped = self.controller_for({
            ("lsusb",): (0, self.bluetooth_usb(), ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
            ("systemctl", "is-active", "bluetooth.service"): (3, "inactive", ""),
            ("systemctl", "is-enabled", "bluetooth.service"): (1, "disabled", ""),
        })
        stopped_status = stopped.bluetooth_status()
        self.assertIn("state", stopped_status)
        self.assertEqual("service_stopped", stopped_status["state"])

        absent = self.controller_for({
            c_command("lspci", "-nnk"): (0, "", ""),
            c_command("lsusb"): (0, "", ""),
        })
        absent_status = absent.bluetooth_status()
        self.assertIn("state", absent_status)
        self.assertEqual("no_hardware", absent_status["state"])

    def test_unidentified_intel_usb_with_btusb_is_diagnostic_not_no_hardware(self):
        controller = self.controller_for({
            ("lspci", "-nnk"): (0, "", ""),
            ("lsusb",): (0, "Bus 001 Device 004: ID 8087:0aaa Intel Corp.", ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
        })

        status = controller.bluetooth_status()

        self.assertEqual("diagnostic_unavailable", status["state"])
        self.assertEqual("retry_diagnostic", status["action"])
        self.assertNotEqual("no_hardware", status["state"])

    def test_failed_bluetooth_hardware_probe_is_diagnostic_unavailable(self):
        controller = self.controller_for({
            c_command("lspci", "-nnk"): (1, "", "lspci missing"),
            c_command("lsusb"): (0, "", ""),
        })
        status = controller.bluetooth_status()
        self.assertEqual("diagnostic_unavailable", status["state"])
        self.assertIn("诊断", status["title"])

    def test_rfkill_block_wins_for_detected_bluetooth_hardware(self):
        controller = self.controller_for({
            ("lsusb",): (0, self.bluetooth_usb(), ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
            ("/usr/sbin/rfkill", "list", "bluetooth"): (
                0, "0: hci0: Bluetooth\n\tSoft blocked: yes\n\tHard blocked: no", ""),
            ("systemctl", "is-active", "bluetooth.service"): (0, "active", ""),
            ("systemctl", "is-enabled", "bluetooth.service"): (0, "enabled", ""),
            ("bluetoothctl", "list"): (0, "Controller AA:BB:CC:DD:EE:FF Built-in", ""),
            ("bluetoothctl", "show"): (0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes", ""),
        })
        status = controller.bluetooth_status()
        self.assertIn("state", status)
        self.assertEqual("rfkill_blocked", status["state"])
        self.assertTrue(status["rfkill"]["soft_blocked"])

    def test_firmware_missing_is_distinct_from_driver_missing(self):
        hardware = ("lsusb",)
        firmware = self.controller_for({
            hardware: (0, self.bluetooth_usb(), ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
            ("journalctl", "-k", "-b", "--no-pager", "-g",
             "firmware.*(failed|missing|not found)|failed to load.*firmware", "-n", "16"):
                (0, "Bluetooth: hci0: failed to load firmware", ""),
        })
        driver = self.controller_for({hardware: (0, self.bluetooth_usb(), "")})
        firmware_status = firmware.bluetooth_status()
        driver_status = driver.bluetooth_status()
        self.assertIn("state", firmware_status)
        self.assertIn("state", driver_status)
        self.assertEqual("firmware_missing", firmware_status["state"])
        self.assertEqual("install_firmware", firmware_status["action"])
        self.assertEqual("driver_missing", driver_status["state"])

    def test_dell_413c_8197_reports_both_kernel_firmware_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            firmware_root = pathlib.Path(directory)
            controller = self.device.DeviceController(
                runner=FakeRunner({
                    ("lsusb",): (
                        0,
                        "Bus 001 Device 003: ID 413c:8197 Dell Computer Corp. Bluetooth",
                        "",
                    ),
                    ("lsmod",): (0, "btusb 65536 0\nbtbcm 24576 1 btusb", ""),
                }),
                executable=lambda _name: True,
                firmware_root=firmware_root,
            )

            missing = controller.bluetooth_status()
            self.assertEqual("usb:413c:8197", missing["firmware"]["device_id"])
            self.assertEqual("btbcm", missing["firmware"]["driver"])
            self.assertEqual("not-bundled-no-redistribution-license",
                             missing["firmware"]["source"])
            self.assertEqual([
                "brcm/BCM-413c-8197.hcd",
                "brcm/BCM20702A1-413c-8197.hcd",
            ], missing["firmware"]["required_files"])
            self.assertEqual("E_FIRMWARE_MISSING", missing["firmware"]["error_code"])
            self.assertEqual("firmware_missing", missing["state"])

            for name in missing["firmware"]["required_files"]:
                target = firmware_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"same reviewed payload")
            complete = controller.bluetooth_status()
            self.assertTrue(complete["firmware"]["complete"])
            self.assertEqual("", complete["firmware"]["error_code"])
            self.assertEqual(
                complete["firmware"]["files"][0]["sha256"],
                complete["firmware"]["files"][1]["sha256"],
            )

            (firmware_root / "brcm/BCM-413c-8197.hcd").write_bytes(b"different")
            mismatch = controller.bluetooth_status()
            self.assertFalse(mismatch["firmware"]["complete"])
            self.assertEqual("E_FIRMWARE_ALIAS_MISMATCH",
                             mismatch["firmware"]["error_code"])
            self.assertEqual("firmware_missing", mismatch["state"])

    def test_controller_off_when_service_is_active_but_not_powered(self):
        controller = self.controller_for({
            ("lsusb",): (0, self.bluetooth_usb(), ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
            ("systemctl", "is-active", "bluetooth.service"): (0, "active", ""),
            ("systemctl", "is-enabled", "bluetooth.service"): (0, "enabled", ""),
            ("bluetoothctl", "list"): (0, "Controller AA:BB:CC:DD:EE:FF Built-in", ""),
            ("bluetoothctl", "show"): (0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: no", ""),
        })
        status = controller.bluetooth_status()
        self.assertEqual("controller_off", status["state"])
        self.assertEqual("power_on", status["action"])
        self.assertTrue(status["controller"]["present"])
        self.assertFalse(status["controller"]["powered"])

    def test_ready_when_service_and_controller_are_powered(self):
        controller = self.controller_for({
            ("lsusb",): (0, self.bluetooth_usb(), ""),
            ("lsmod",): (0, "btusb 65536 0", ""),
            ("systemctl", "is-active", "bluetooth.service"): (0, "active", ""),
            ("systemctl", "is-enabled", "bluetooth.service"): (0, "enabled", ""),
            ("bluetoothctl", "list"): (0, "Controller AA:BB:CC:DD:EE:FF Built-in", ""),
            ("bluetoothctl", "show"): (0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes", ""),
        })
        status = controller.bluetooth_status()
        self.assertEqual("ready", status["state"])
        self.assertEqual("none", status["action"])
        self.assertTrue(status["controller"]["powered"])

    def test_rich_bluetooth_status_is_embedded_in_top_level_status(self):
        controller = self.controller_for({})
        status = controller.status()
        bluetooth = status["bluetooth"]
        self.assertTrue({
            "state", "hardware", "modules", "firmware_evidence", "rfkill", "service",
            "controller", "action", "title", "detail",
        }.issubset(bluetooth))
        self.assertTrue({"present", "powered"}.issubset(bluetooth["controller"]))
        self.assertTrue({"active", "enabled"}.issubset(bluetooth["service"]))
        self.assertTrue({"soft_blocked", "hard_blocked"}.issubset(bluetooth["rfkill"]))


class DesktopWidgetContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.phone = PHONE_DESKTOP.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")

    def test_phone_desktop_uses_desktop_layer_without_fullscreen_state(self):
        window = self.phone[self.phone.index("class PhoneDesktop"):]
        self.assertIn("Gdk.WindowTypeHint.DESKTOP", window)
        self.assertIn("self.set_keep_below(True)", window)
        self.assertIn("self.resize(screen_w, screen_h)", window)
        self.assertNotIn("self.fullscreen()", window)

    def test_status_widget_keeps_persistent_label_children(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        for marker in [
            "self.wifi_label",
            "self.bluetooth_label",
            "self.battery_label",
            "self.notification_label",
            ".set_text(",
            ".set_no_show_all(True)",
        ]:
            self.assertIn(marker, status)
        self.assertNotIn(".set_label(", status)

    def test_widget_actions_open_stable_settings_pages(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        self.assertIn('["ming-control-center", "--page", "network"]', status)
        self.assertIn('["ming-control-center", "--page", "display"]', status)
        self.assertIn('["ming-control-center", "--page", "advanced"]', status)

    def test_widget_monitors_fast_command_failure_and_notifies(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        self.assertIn("monitor_action_process", status)
        self.assertIn("process.wait(timeout=", status)
        self.assertIn("process.returncode", status)
        self.assertIn("notify-send", status)

    def test_no_backlight_keeps_explanation_and_display_entry_visible(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        apply_status = status[status.index("    def apply_status"):]
        self.assertIn('self.brightness_label.set_text(', apply_status)
        self.assertIn('"当前设备不支持"', apply_status)
        self.assertIn("self.brightness_scale.set_sensitive(brightness_available)", apply_status)
        self.assertNotIn(
            "for control in (self.brightness_label, self.brightness_scale, self.display_button)",
            apply_status,
        )

    def test_settings_reuses_device_control_wifi_state(self):
        helper = self.settings[self.settings.index("def wifi_diagnostic_snapshot"):
                               self.settings.index("class MingSettings")]
        self.assertIn("load_device_control", self.settings)
        self.assertIn('snapshot["wifi"]', helper)
        self.assertIn('"state"', helper)

    def test_wifi_refresh_and_scan_reject_stale_or_destroyed_results(self):
        self.assertIn("self.wifi_probe_state = GenerationState()", self.settings)
        refresh = self.settings[self.settings.index("    def on_wifi_status_refresh"):
                                self.settings.index("    def on_bt_toggle")]
        scan = self.settings[self.settings.index("    def on_wifi_scan"):
                             self.settings.index("    def on_wifi_connect")]
        for block in (refresh, scan):
            self.assertIn("generation = self.wifi_probe_state.begin()", block)
            self.assertIn("self.wifi_probe_state.accept(generation)", block)
            self.assertIn("self.network_page.get_root() is not self", block)


if __name__ == "__main__":
    unittest.main()
