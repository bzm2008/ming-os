import ast
import pathlib
import textwrap
import threading
import time
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "assets" / "ming-settings.py"
SETTINGS_SOURCE = SETTINGS_PATH.read_text(encoding="utf-8")
SETTINGS_TREE = ast.parse(SETTINGS_SOURCE)


def function_source(name, class_name=None):
    nodes = SETTINGS_TREE.body
    if class_name:
        nodes = next(node.body for node in nodes
                     if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next((node for node in nodes
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)
    if node is None:
        raise AssertionError("missing function: %s" % name)
    return ast.get_source_segment(SETTINGS_SOURCE, node)


def executable_function(name, namespace=None, class_name=None):
    namespace = {} if namespace is None else namespace
    exec(textwrap.dedent(function_source(name, class_name)), namespace)
    return namespace[name]


def generation_state_type():
    node = next((node for node in SETTINGS_TREE.body
                 if isinstance(node, ast.ClassDef) and node.name == "GenerationState"), None)
    if node is None:
        raise AssertionError("missing class: GenerationState")
    namespace = {}
    exec(ast.get_source_segment(SETTINGS_SOURCE, node), namespace)
    return namespace["GenerationState"]


class Recorder:
    def __init__(self, root=None):
        self.root = root
        self.calls = []

    def get_root(self):
        return self.root

    def set_title(self, value):
        self.calls.append(("title", value))

    def set_subtitle(self, value):
        self.calls.append(("subtitle", value))

    def set_sensitive(self, value):
        self.calls.append(("sensitive", value))

    def set_active(self, value):
        self.calls.append(("active", value))

    def set_visible(self, value):
        self.calls.append(("visible", value))

    def set_label(self, value):
        self.calls.append(("label", value))


class Page:
    def __init__(self, root=None):
        self.root = root

    def get_root(self):
        return self.root


class SettingsRadioAudioContracts(unittest.TestCase):
    def test_wifi_scan_uses_structured_controller_records_without_ssid_deduplication(self):
        scan = function_source("wifi_scan_snapshot")
        network_ui = function_source("on_wifi_scan", "MingSettings")

        self.assertIn("controller.wifi_scan()", scan)
        self.assertNotIn('"nmcli"', scan)
        self.assertIn('network["bssid"]', network_ui)
        self.assertIn('network["band"]', network_ui)
        self.assertIn('network["channel"]', network_ui)
        self.assertIn('network["security"]', network_ui)
        self.assertNotIn("seen", scan)

    def test_wifi_connection_is_bound_to_bssid_and_sends_password_only_via_stdin(self):
        connect = function_source("on_wifi_connect", "MingSettings")
        command = function_source("wifi_connect_command")
        stdin_runner = function_source("run_capture_stdin_async")

        self.assertIn("Gtk.PasswordEntry", connect)
        self.assertIn("run_capture_stdin_async", connect)
        self.assertIn('"--password-stdin"', command)
        self.assertIn("bssid", command)
        self.assertIn("ifname", command)
        self.assertIn("process.communicate(input_text", stdin_runner)
        self.assertNotIn("password", command.replace("--password-stdin", ""))

    def test_wifi_connection_command_uses_opaque_network_id_not_display_ssid(self):
        command = function_source("wifi_connect_command")
        connect = function_source("on_wifi_connect", "MingSettings")
        self.assertIn('"--network-id"', command)
        self.assertIn('network_id', command)
        self.assertNotIn('"--ssid"', command)
        self.assertIn('network["network_id"]', connect)
        self.assertIn('network.get("display")', connect)

    def test_wifi_connect_button_enters_progress_and_recovers_after_readback(self):
        connect = function_source("on_wifi_connect", "MingSettings")
        self.assertIn('_btn.set_sensitive(False)', connect)
        self.assertIn('_btn.set_label("连接中...")', connect)
        self.assertIn('_btn.set_sensitive(True)', connect)
        self.assertIn('_btn.set_label("连接")', connect)

    def test_bluetooth_uses_structured_status_and_only_repairs_allowed_states(self):
        refresh = function_source("refresh_bluetooth_status", "MingSettings")
        repair = function_source("on_bluetooth_repair", "MingSettings")

        self.assertIn("bluetooth_status_snapshot", refresh)
        self.assertIn("bluetooth_repair_allowed(status)", refresh)
        self.assertIn('["pkexec", "ming-radio-repair", "bluetooth"]', repair)
        self.assertIn("refresh_bluetooth_status()", repair)

    def test_hardware_wifi_repair_is_interface_scoped_without_backend_switch(self):
        page = function_source("build_hardware", "MingSettings")
        repair = function_source("on_wifi_repair", "MingSettings")

        self.assertIn("固定使用 wpa_supplicant", page)
        self.assertNotIn("切换为 iwd", page)
        self.assertNotIn("--use-iwd", page)
        self.assertNotIn("--use-wpa", page)
        self.assertIn("wifi_diagnostic_snapshot", repair)
        self.assertIn('"--ifname", ifname', repair)
        self.assertIn("run_capture_async", repair)
        self.assertIn("wifi_repair_state.accept", repair)
        self.assertIn("self.wifi_repair_state = GenerationState()", SETTINGS_SOURCE)
        self.assertIn("self.wifi_repair_state.invalidate()", SETTINGS_SOURCE)

    def test_audio_actions_use_the_device_controller_and_present_readable_result(self):
        advanced = function_source("build_advanced", "MingSettings")
        status = function_source("refresh_call_audio_status", "MingSettings")
        repair = function_source("on_audio_repair_call", "MingSettings")
        test_input = function_source("on_audio_test_input", "MingSettings")

        self.assertIn("修复通话音频", advanced)
        self.assertIn("三秒麦克风测试", advanced)
        self.assertIn("audio_status_snapshot", status)
        self.assertIn("audio_repair_call_snapshot", repair)
        self.assertIn("audio_test_input_snapshot", test_input)
        self.assertIn("3 秒", test_input)

    def test_hardware_page_consumes_structured_cards_and_keeps_raw_evidence_export_only(self):
        snapshot = function_source("hardware_status_snapshot")
        refresh = function_source("refresh_hardware_status", "MingSettings")
        page = function_source("build_hardware", "MingSettings")
        export = function_source("export_hardware_diagnostics", "MingSettings")

        self.assertIn('"ming-hardware-status", "status", "--json"', snapshot)
        self.assertIn('snapshot["devices"]', refresh)
        self.assertIn("硬件状态", page)
        self.assertIn("导出原始诊断", page)
        self.assertIn("复制原始诊断", page)
        self.assertIn("get_clipboard().set_text(content)", export)
        self.assertNotIn("pci_driver_summary", refresh)

    def test_feedback_uses_high_contrast_specific_headings_instead_of_generic_hint(self):
        toast = function_source("toast", "MingSettings")

        self.assertNotIn('heading="提示"', toast)
        self.assertIn("操作结果", toast)
        self.assertIn("操作失败", toast)
        self.assertIn("ming-feedback-dialog", SETTINGS_SOURCE)
        self.assertIn("#FFFFFF", SETTINGS_SOURCE)


class SettingsAsyncBehaviorTests(unittest.TestCase):
    def test_wifi_connect_dialog_uses_opaque_feedback_style(self):
        class FakeDialog:
            instance = None

            def __init__(self, **_kwargs):
                type(self).instance = self
                self.css_classes = []
                self.responses = []
                self.presented = False

            def set_extra_child(self, _child):
                pass

            def add_response(self, response_id, label):
                self.responses.append((response_id, label))

            def set_response_appearance(self, _response_id, _appearance):
                pass

            def add_css_class(self, css_class):
                self.css_classes.append(css_class)

            def connect(self, _signal, _callback):
                pass

            def present(self):
                self.presented = True

        class FakePasswordEntry:
            def __init__(self, **_kwargs):
                pass

            def set_placeholder_text(self, _text):
                pass

        connect = executable_function("on_wifi_connect", {
            "Adw": types.SimpleNamespace(
                MessageDialog=FakeDialog,
                ResponseAppearance=types.SimpleNamespace(SUGGESTED="suggested"),
            ),
            "Gtk": types.SimpleNamespace(PasswordEntry=FakePasswordEntry),
        }, "MingSettings")

        connect(types.SimpleNamespace(), Recorder(), {
            "display": "Ming Wi-Fi",
            "bssid": "AA:BB:CC:DD:EE:FF",
            "network_id": "ming-net-" + "a" * 32,
            "ifname": "wlan0",
        })

        self.assertTrue(FakeDialog.instance.presented)
        self.assertEqual(
            ["ming-feedback-dialog", "feedback-info"],
            FakeDialog.instance.css_classes,
        )

    def test_concurrent_device_control_load_waits_for_one_completed_module(self):
        entered = threading.Event()
        release = threading.Event()

        class FakeLoader:
            calls = 0

            def exec_module(self, module):
                type(self).calls += 1
                entered.set()
                release.wait(1)
                module.ready = True

        loader = FakeLoader()
        spec = types.SimpleNamespace(loader=loader)
        namespace = {
            "_DEVICE_CONTROL_MODULE": None,
            "_DEVICE_CONTROL_LOADED": False,
            "_DEVICE_CONTROL_LOADING": False,
            "_DEVICE_CONTROL_CONDITION": threading.Condition(),
            "DEVICE_CONTROL_PATHS": ["/fake/device-control.py"],
            "os": types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda _path: True)),
            "importlib": types.SimpleNamespace(util=types.SimpleNamespace(
                spec_from_file_location=lambda _name, _path: spec,
                module_from_spec=lambda _spec: types.SimpleNamespace(),
            )),
        }
        load = executable_function("load_device_control", namespace)
        results = []
        first = threading.Thread(target=lambda: results.append(load()))
        second = threading.Thread(target=lambda: results.append(load()))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        time.sleep(0.05)
        self.assertTrue(second.is_alive(), "second caller must wait for the in-flight import")
        release.set()
        first.join(1)
        second.join(1)

        self.assertEqual(1, loader.calls)
        self.assertEqual(2, len(results))
        self.assertTrue(all(result is results[0] and result.ready for result in results))

    def test_hardware_probe_collects_compatibility_help_off_the_gtk_callback(self):
        calls = []

        def fake_run(command, timeout):
            calls.append(("hardware", tuple(command), timeout))
            return 0, '{"devices": {"graphics": {}, "audio": {}, "network": {}}}', ""

        def fake_compatibility_help():
            calls.append(("compatibility_help",))
            return {"ok": True, "read_only": True, "device_ids": []}

        snapshot = executable_function(
            "hardware_status_snapshot", {"run": fake_run, "json": __import__("json"),
                                         "read_compatibility_help_snapshot": fake_compatibility_help})()

        self.assertTrue(snapshot["ok"])
        self.assertIn("compatibility_help", snapshot)
        self.assertEqual(
            {"ok": True, "read_only": True, "device_ids": []},
            snapshot["compatibility_help"])
        self.assertEqual("compatibility_help", calls[-1][0])

    def test_compatibility_help_preserves_helper_failure_instead_of_forcing_success(self):
        def fake_run(_command, timeout):
            self.assertEqual(8, timeout)
            return 0, '{"ok": false, "error": "设备探测失败"}', ""

        snapshot = executable_function(
            "read_compatibility_help_snapshot",
            {"run": fake_run, "json": __import__("json")})()

        self.assertFalse(snapshot["ok"])
        self.assertEqual("设备探测失败", snapshot["error"])
        self.assertTrue(snapshot["read_only"])

    def test_hardware_renderer_consumes_probe_compatibility_help_without_reading_on_callback(self):
        GenerationState = generation_state_type()
        queued = []

        def enqueue(task, on_done):
            queued.append((task, on_done))

        def unexpected_compatibility_read():
            raise AssertionError("Compatibility help must be read by the worker probe, not the GTK callback")

        refresh = executable_function("refresh_hardware_status", {
            "run_task_async": enqueue,
            "hardware_status_snapshot": lambda: None,
            "read_compatibility_help_snapshot": unexpected_compatibility_read,
        }, "MingSettings")

        window = types.SimpleNamespace()
        window.hardware_probe_state = GenerationState()
        window.hardware_refresh_button = Recorder()
        window.hardware_page = Page(window)
        window.hardware_summary_row = Recorder()
        window.hardware_graphics_row = Recorder()
        window.hardware_audio_row = Recorder()
        window.hardware_network_row = Recorder()
        window.applied_compatibility_help = []
        window.apply_compatibility_help = window.applied_compatibility_help.append

        refresh(window)
        queued[0][1]({
            "ok": True,
            "devices": {"graphics": {}, "audio": {}, "network": {}},
            "compatibility_help": {"ok": True, "read_only": True, "device_ids": []},
        }, None)

        self.assertEqual(
            [{"ok": True, "read_only": True, "device_ids": []}],
            window.applied_compatibility_help)

    def test_bluetooth_repair_refuses_hard_rfkill_after_current_state_recheck(self):
        queued, commands = [], []
        GenerationState = generation_state_type()
        repair_allowed = executable_function("bluetooth_repair_allowed")
        self.assertTrue(repair_allowed({"state": "service_stopped", "rfkill": {}}))
        self.assertTrue(repair_allowed({
            "state": "rfkill_blocked",
            "rfkill": {"soft_blocked": True, "hard_blocked": False},
        }))
        self.assertFalse(repair_allowed({
            "state": "rfkill_blocked",
            "rfkill": {"soft_blocked": False, "hard_blocked": True},
        }))
        self.assertFalse(repair_allowed({"state": "controller_off", "rfkill": {}}))

        def enqueue(task, on_done):
            queued.append((task, on_done))

        def capture(command, timeout, on_done):
            commands.append((command, timeout, on_done))

        repair = executable_function("on_bluetooth_repair", {
            "run_task_async": enqueue,
            "run_capture_async": capture,
            "bluetooth_status_snapshot": lambda: None,
            "bluetooth_repair_allowed": repair_allowed,
        }, "MingSettings")
        window = types.SimpleNamespace(
            bluetooth_probe_state=GenerationState(), network_page=Page(),
            bt_repair_button=Recorder(), toasts=[])
        window.network_page.root = window
        window.toast = lambda text, severity: window.toasts.append((text, severity))

        repair(window, None)
        self.assertEqual(1, len(queued))
        queued[0][1]({
            "state": "rfkill_blocked",
            "title": "蓝牙已被硬件开关阻止",
            "rfkill": {"soft_blocked": False, "hard_blocked": True},
        }, None)

        self.assertEqual([], commands)
        self.assertTrue(any(severity == "warning" for _text, severity in window.toasts))

    def test_stale_bluetooth_and_audio_callbacks_do_not_mutate_rows(self):
        GenerationState = generation_state_type()
        bluetooth_jobs, audio_jobs = [], []
        refresh_bluetooth = executable_function("refresh_bluetooth_status", {
            "run_task_async": lambda task, done: bluetooth_jobs.append((task, done)),
            "bluetooth_status_snapshot": lambda: None,
        }, "MingSettings")
        refresh_audio = executable_function("refresh_call_audio_status", {
            "run_task_async": lambda task, done: audio_jobs.append((task, done)),
            "audio_status_snapshot": lambda: None,
        }, "MingSettings")
        window = types.SimpleNamespace(
            bluetooth_probe_state=GenerationState(), audio_probe_state=GenerationState(),
            network_page=Page(), bt_status_row=Recorder(), bt_detail_row=Recorder(),
            bt_switch=Recorder(), bt_repair_row=Recorder(), loading_bt_state=False,
            call_audio_status_row=Recorder(), audio_repair_button=Recorder(), audio_test_button=Recorder(),
        )
        window.network_page.root = window
        window.call_audio_status_row.root = window
        window.advanced_page = Page(window)

        refresh_bluetooth(window)
        refresh_bluetooth(window)
        refresh_audio(window)
        refresh_audio(window)
        for recorder in (window.bt_status_row, window.bt_detail_row, window.bt_switch,
                         window.bt_repair_row, window.call_audio_status_row,
                         window.audio_repair_button, window.audio_test_button):
            recorder.calls.clear()

        bluetooth_jobs[0][1]({"state": "ready", "title": "蓝牙可用", "detail": "ok",
                               "hardware": [], "modules": [], "service": {"active": True},
                               "rfkill": {}, "controller": {"powered": True}}, None)
        audio_jobs[0][1]({"available": True, "call_ready": True,
                          "physical_input_present": True, "input_muted": False,
                          "duplex_profile_active": True}, None)

        self.assertEqual([], window.bt_status_row.calls)
        self.assertEqual([], window.call_audio_status_row.calls)

    def test_wifi_connect_result_ignores_stale_or_closed_page(self):
        GenerationState = generation_state_type()
        apply_result = executable_function("apply_wifi_connect_result", {}, "MingSettings")
        window = types.SimpleNamespace(
            wifi_connect_state=GenerationState(), network_page=Page(), toasts=[])
        window.network_page.root = window
        window.toast = lambda text, severity: window.toasts.append((text, severity))

        generation = window.wifi_connect_state.begin()
        window.wifi_connect_state.begin()
        apply_result(window, generation, "Cafe", "00:11:22:33:44:55", {"ok": True}, "")
        generation = window.wifi_connect_state.begin()
        window.network_page.root = None
        apply_result(window, generation, "Cafe", "00:11:22:33:44:55", {"ok": True}, "")

        self.assertEqual([], window.toasts)


if __name__ == "__main__":
    unittest.main()
