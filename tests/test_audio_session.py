import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIO_SESSION_PATH = ROOT / "assets" / "ming-audio-session.py"
BACKEND_PATH = ROOT / "assets" / "ming-settings-backend.py"
SETTINGS_PATH = ROOT / "assets" / "ming-settings.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=8):
        self.calls.append((tuple(argv), timeout))
        return 0, "", ""


def status(**overrides):
    result = {
        "available": True,
        "backend": "pactl",
        "server_available": True,
        "playback_ready": True,
        "default_sink": "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "default_sink_present": True,
        "playback_profile_valid": True,
        "output_muted": False,
        "playback_devices": [{
            "id": "alsa_output.pci-0000_00_1f.3.analog-stereo",
            "kind": "internal", "available": True, "active": True,
        }],
        "error": "",
    }
    result.update(overrides)
    return result


class AudioSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio = load_module(AUDIO_SESSION_PATH, "ming_audio_session")

    def test_ensure_starts_pulseaudio_then_repairs_a_missing_output(self):
        statuses = iter((
            status(server_available=False, playback_ready=False, default_sink="",
                   default_sink_present=False, playback_profile_valid=None,
                   playback_devices=[], error="PulseAudio 服务没有运行。"),
            status(playback_ready=False, default_sink="", default_sink_present=False,
                   playback_profile_valid=None, playback_devices=[],
                   error="没有默认输出。"),
            status(),
        ))
        repairs = []
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as directory:
            session = self.audio.AudioSession(
                status_reader=lambda: next(statuses),
                repairer=lambda: repairs.append(True) or {"ok": True, "changed": True},
                runner=runner,
                log_path=pathlib.Path(directory) / "audio-session.log",
            )

            result = session.ensure()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual([True], repairs)
        self.assertIn(("pulseaudio", "--start"), [call[0] for call in runner.calls])
        self.assertTrue(all(timeout <= self.audio.COMMAND_TIMEOUT for _, timeout in runner.calls))

    def test_ensure_preserves_a_valid_manually_selected_hdmi_output(self):
        hdmi = "alsa_output.pci-0000_01_00.1.hdmi-stereo"
        runner = RecordingRunner()
        repairs = []
        session = self.audio.AudioSession(
            status_reader=lambda: status(default_sink=hdmi, playback_devices=[{
                "id": hdmi, "kind": "hdmi", "available": True, "active": True,
            }]),
            repairer=lambda: repairs.append(True) or {"ok": True},
            runner=runner,
        )

        result = session.ensure()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual([], repairs)
        self.assertEqual([], runner.calls)
        self.assertEqual(hdmi, result["status"]["default_sink"])

    def test_status_cli_returns_structured_status_without_repairing(self):
        session = self.audio.AudioSession(status_reader=lambda: status())
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            rc = self.audio.main(["status", "--json"], session=session)

        self.assertEqual(0, rc)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual("ready", payload["state"])


class SettingsAudioOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend_module = load_module(BACKEND_PATH, "ming_settings_backend_audio")
        cls.settings_source = SETTINGS_PATH.read_text(encoding="utf-8")

    def test_remembered_output_is_persisted_without_direct_pactl_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend_module.SettingsBackend(
                runner=RecordingRunner(), home=pathlib.Path(directory),
                system_autostart_dirs=(), application_dirs=(),
            )
            output_id = "alsa_output.pci-0000_00_1f.3.analog-stereo"

            result = backend.remember_audio_output(output_id)

            self.assertTrue(result["ok"])
            saved = json.loads(backend.local_path.read_text(encoding="utf-8"))
            self.assertEqual(output_id, saved["audio_output_selection"])

    def test_remembered_output_rejects_unsafe_sink_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend_module.SettingsBackend(
                runner=RecordingRunner(), home=pathlib.Path(directory),
                system_autostart_dirs=(), application_dirs=(),
            )

            result = backend.remember_audio_output("sink;rm -rf /")

            self.assertFalse(result["ok"])
            self.assertFalse(backend.local_path.exists())

    def test_settings_uses_device_control_for_output_selection_and_playback_repair(self):
        source = self.settings_source
        self.assertIn("def audio_output_row", source)
        self.assertIn('"audio-select-output", "--id"', source)
        self.assertIn("def audio_session_cli_command", source)
        self.assertIn('audio_session_cli_command("ensure", "--json")', source)
        self.assertIn("修复声音播放", source)
        self.assertIn("主板模拟输出（内置扬声器，例如 ALC887）", source)
        self.assertIn("audio", self.backend_module.__dict__["main"].__code__.co_consts)


if __name__ == "__main__":
    unittest.main()
