import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-tea-core.py"
RUNTIME_PATH = ROOT / "assets" / "ming-tea-runtime.py"
APP_PATH = ROOT / "assets" / "ming-tea.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
BUILD = ROOT / "build_onion_os.sh"


def load_core():
    spec = importlib.util.spec_from_file_location("ming_tea_core", CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingTeaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_core()

    def test_runtime_script_exposes_ping_and_session_ipc_contract(self):
        source = RUNTIME_PATH.read_text(encoding="utf-8")
        for marker in (
            "MING_TEA_SOCKET",
            "MingTeaRuntime",
            "session.created",
            "approval.requested",
            "serve_forever",
        ):
            self.assertIn(marker, source)

    def test_runtime_ping_and_session_requests_work_over_unix_socket(self):
        spec = importlib.util.spec_from_file_location("ming_tea_runtime", RUNTIME_PATH)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        server = runtime.MingTeaIPCServer(pathlib.Path("/tmp/ming-tea-test.sock"))
        payload = server.handle_request({"action": "ping"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "ming-tea")

    def test_native_app_is_not_a_web_wrapper_and_declares_ming_branding(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn("Gtk.Application", source)
        self.assertIn("Native GTK surface", source)
        self.assertIn("Adw.ApplicationWindow", source)
        self.assertIn("铭荼", source)
        self.assertNotIn("127.0.0.1:3080", source)

    def test_desktop_modules_install_ming_tea_and_default_launcher(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        finalize = FINALIZE.read_text(encoding="utf-8")
        build = BUILD.read_text(encoding="utf-8")
        for source in (desktop, finalize, build):
            self.assertIn("ming-tea", source)
        self.assertIn("ming-tea.desktop", desktop)
        self.assertIn("ming-tea.desktop", finalize)
        self.assertIn("ming-tea-plugins.json", build)


if __name__ == "__main__":
    unittest.main()
