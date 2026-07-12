import importlib.util
import ast
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "assets" / "ming-settings-backend.py"
SETTINGS_PATH = ROOT / "assets" / "ming-settings.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("ming_settings_backend", BACKEND_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.picom_command = ""
        self.default_sink = "alsa_output.pci"
        self.default_source = "alsa_input.pci"
        self.sinks = "0\talsa_output.pci\tmodule\ts16le\tRUNNING"
        self.default_apps = {
            "default-web-browser": "browser.desktop",
            "x-scheme-handler/mailto": "mail.desktop",
            "inode/directory": "files.desktop",
        }
        self.ignore_pkill = False

    def __call__(self, argv, timeout=8):
        self.calls.append(list(argv))
        if argv[0] == "xfconf-query":
            channel = argv[argv.index("-c") + 1]
            prop = argv[argv.index("-p") + 1]
            key = (channel, prop)
            if "-r" in argv:
                self.values.pop(key, None)
                return 0, "", ""
            if "-s" in argv:
                self.values[key] = argv[argv.index("-s") + 1]
                return 0, "", ""
            return 0, str(self.values.get(key, "")), ""
        if argv[:3] == ["pgrep", "-a", "-x"]:
            return ((0, "123 " + self.picom_command, "") if self.picom_command
                    else (1, "", ""))
        if argv[:2] == ["pkill", "-x"]:
            if not self.ignore_pkill:
                self.picom_command = ""
            return 0, "", ""
        if argv[:3] == ["pactl", "list", "short"]:
            if argv[3] == "sinks":
                return 0, self.sinks, ""
            return 0, (
                "0\talsa_input.pci\tmodule\ts16le\tRUNNING\n"
                "1\talsa_output.pci.monitor\tmodule\ts16le\tIDLE"
            ), ""
        if argv[:2] == ["pactl", "get-default-sink"]:
            return 0, self.default_sink, ""
        if argv[:2] == ["pactl", "get-default-source"]:
            return 0, self.default_source, ""
        if argv[:2] == ["pactl", "set-default-sink"]:
            self.default_sink = argv[2]
            return 0, "", ""
        if argv[:2] == ["pactl", "set-default-source"]:
            self.default_source = argv[2]
            return 0, "", ""
        if argv[:3] == ["xdg-settings", "get", "default-web-browser"]:
            return 0, self.default_apps["default-web-browser"], ""
        if argv[:3] == ["xdg-settings", "set", "default-web-browser"]:
            self.default_apps["default-web-browser"] = argv[3]
            return 0, "", ""
        if argv[:3] == ["xdg-mime", "query", "default"]:
            return 0, self.default_apps[argv[3]], ""
        if argv[:2] == ["xdg-mime", "default"]:
            self.default_apps[argv[3]] = argv[2]
            return 0, "", ""
        if argv[:2] == ["gsettings", "get"]:
            return 0, str(self.values.get(tuple(argv[1:3]), "1.0")), ""
        if argv[:2] == ["gsettings", "set"]:
            self.values[tuple(argv[1:3])] = argv[3]
            return 0, "", ""
        return 127, "", "missing"


class SettingsBackendTests(unittest.TestCase):
    def setUp(self):
        self.module = load_backend()
        self.runner = FakeRunner()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.backend = self.module.SettingsBackend(
            runner=self.runner,
            spawner=self._spawn,
            home=pathlib.Path(self.tempdir.name),
            system_autostart_dirs=(),
            application_dirs=(),
        )

    def _spawn(self, argv):
        self.runner.picom_command = " ".join(argv)
        return object()

    def test_rejects_out_of_range_value_without_running_command(self):
        result = self.backend.set_value("dock_icon_size", 200)
        self.assertFalse(result["ok"])
        self.assertIn("32", result["error"])
        self.assertEqual([], self.runner.calls)

    def test_xfconf_setting_is_written_and_read_back(self):
        result = self.backend.set_value("focus_mode", "click")
        self.assertTrue(result["ok"])
        self.assertEqual("click", result["value"])
        self.assertTrue(all(isinstance(call, list) for call in self.runner.calls))

    def test_focus_mode_uses_boolean_xfconf_property_type(self):
        result = self.backend.set_value("focus_mode", "click")
        self.assertTrue(result["ok"])
        write = next(call for call in self.runner.calls if "-s" in call)
        self.assertEqual("bool", write[write.index("-t") + 1])
        self.assertEqual("true", write[write.index("-s") + 1])

    def test_plank_setting_uses_structured_keyfile_update(self):
        settings = pathlib.Path(self.tempdir.name) / ".config/plank/dock1/settings"
        settings.parent.mkdir(parents=True)
        settings.write_text("[PlankDockPreferences]\nIconSize=48\nZoomPercent=126\n", encoding="utf-8")
        result = self.backend.set_value("dock_icon_size", 64)
        self.assertTrue(result["ok"])
        self.assertEqual(64, result["value"])
        self.assertIn("IconSize=64", settings.read_text(encoding="utf-8"))

    def test_protected_autostart_entry_cannot_be_disabled(self):
        result = self.backend.set_autostart("ming-phone-desktop.desktop", False)
        self.assertFalse(result["ok"])
        self.assertIn("系统必需", result["error"])

    def test_compositor_autostart_is_managed_only_by_compositor_profile(self):
        autostart = pathlib.Path(self.tempdir.name) / ".config/autostart/picom.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text(
            "[Desktop Entry]\nType=Application\nName=Picom\nExec=/usr/local/bin/ming-picom\n",
            encoding="utf-8")
        result = self.backend.set_autostart("picom.desktop", False)
        self.assertFalse(result["ok"])
        self.assertIn("系统必需", result["error"])

    def test_reduced_motion_is_persisted_and_read_back(self):
        result = self.backend.set_value("reduced_motion", True)
        self.assertTrue(result["ok"])
        self.assertIs(True, self.backend.get_value("reduced_motion")["value"])

    def test_software_compositor_profile_restarts_picom_and_persists_autostart(self):
        result = self.backend.set_value("compositor_profile", "software")
        self.assertTrue(result["ok"])
        self.assertIn("picom --config /etc/xdg/picom/picom-fallback.conf", self.runner.picom_command)
        autostart = pathlib.Path(self.tempdir.name) / ".config/autostart/picom.desktop"
        content = autostart.read_text(encoding="utf-8")
        self.assertIn("Exec=picom --config /etc/xdg/picom/picom-fallback.conf", content)
        self.assertIn("X-GNOME-Autostart-enabled=true", content)
        self.assertEqual("software", self.backend.get_value("compositor_profile")["value"])

    def test_disabled_compositor_stops_picom_and_disables_autostart(self):
        self.backend.set_value("compositor_profile", "auto")
        result = self.backend.set_value("compositor_profile", "off")
        self.assertTrue(result["ok"])
        self.assertEqual("", self.runner.picom_command)
        content = (pathlib.Path(self.tempdir.name) / ".config/autostart/picom.desktop").read_text(
            encoding="utf-8")
        self.assertIn("X-GNOME-Autostart-enabled=false", content)
        self.assertEqual("off", self.backend.get_value("compositor_profile")["value"])

    def test_failed_compositor_start_restores_previous_runtime_and_autostart(self):
        self.assertTrue(self.backend.set_value("compositor_profile", "auto")["ok"])
        autostart = pathlib.Path(self.tempdir.name) / ".config/autostart/picom.desktop"
        previous_autostart = autostart.read_bytes()

        def fail_software(argv):
            if any("picom-fallback.conf" in item for item in argv):
                raise OSError("software compositor failed")
            return self._spawn(argv)

        self.backend.spawner = fail_software
        result = self.backend.set_value("compositor_profile", "software")

        self.assertFalse(result["ok"])
        self.assertEqual(previous_autostart, autostart.read_bytes())
        self.assertEqual("/usr/local/bin/ming-picom", self.runner.picom_command)
        self.assertEqual(
            "false", self.runner.values[("xfwm4", "/general/use_compositing")])
        readback = self.backend.get_value("compositor_profile")
        self.assertTrue(readback["ok"])
        self.assertEqual("auto", readback["value"])

    def test_compositor_state_write_failure_rolls_back_new_runtime(self):
        self.assertTrue(self.backend.set_value("compositor_profile", "auto")["ok"])
        original_write = self.backend._write_local

        def fail_write(_data):
            raise OSError("settings disk full")

        self.backend._write_local = fail_write
        result = self.backend.set_value("compositor_profile", "software")
        self.backend._write_local = original_write

        self.assertFalse(result["ok"])
        self.assertEqual("/usr/local/bin/ming-picom", self.runner.picom_command)
        self.assertEqual("auto", self.backend.get_value("compositor_profile")["value"])

    def test_failed_switch_restores_builtin_xfwm_compositor_without_picom(self):
        key = ("xfwm4", "/general/use_compositing")
        self.runner.values[key] = "true"

        def fail_start(_argv):
            raise OSError("picom unavailable")

        self.backend.spawner = fail_start
        result = self.backend.set_value("compositor_profile", "software")

        self.assertFalse(result["ok"])
        self.assertEqual("true", self.runner.values[key])
        self.assertEqual("", self.runner.picom_command)
        self.assertFalse((pathlib.Path(self.tempdir.name) / ".config/autostart/picom.desktop").exists())
        self.assertFalse((pathlib.Path(self.tempdir.name) / ".config/ming-os/settings.json").exists())

    def test_rollback_reports_residual_new_picom_and_does_not_mask_it(self):
        self.assertTrue(self.backend.set_value("compositor_profile", "auto")["ok"])
        original_write = self.backend._write_local
        spawn_calls = []

        def spawn_then_make_pkill_sticky(argv):
            spawn_calls.append(tuple(argv))
            self._spawn(argv)
            if any("picom-fallback.conf" in item for item in argv):
                self.runner.ignore_pkill = True
            return object()

        self.backend.spawner = spawn_then_make_pkill_sticky
        self.backend._write_local = lambda _data: (_ for _ in ()).throw(OSError("disk full"))
        result = self.backend.set_value("compositor_profile", "software")
        self.backend._write_local = original_write

        self.assertFalse(result["ok"])
        self.assertIn("未能停止", result["error"])
        self.assertIn("picom-fallback.conf", self.runner.picom_command)
        self.assertEqual(1, len(spawn_calls), "rollback must not launch old Picom over a residual process")

    def test_rollback_verifies_complete_restored_picom_arguments(self):
        self.assertTrue(self.backend.set_value("compositor_profile", "software")["ok"])
        original_write = self.backend._write_local

        def lossy_restore(argv):
            command = list(argv)
            if any("picom-fallback.conf" in item for item in command):
                command = command[:-1]
            self.runner.picom_command = " ".join(command)
            return object()

        self.backend.spawner = lossy_restore
        self.backend._write_local = lambda _data: (_ for _ in ()).throw(OSError("disk full"))
        result = self.backend.set_value("compositor_profile", "auto")
        self.backend._write_local = original_write

        self.assertFalse(result["ok"])
        self.assertIn("命令", result["error"])

    def test_audio_devices_filter_monitors_and_verify_default_device(self):
        inputs = self.backend.list_audio_devices("input")
        self.assertEqual(["alsa_input.pci"], [item["id"] for item in inputs["items"]])
        result = self.backend.set_audio_device("output", "alsa_output.pci")
        self.assertTrue(result["ok"])
        self.assertEqual("alsa_output.pci", result["value"])

    def test_audio_output_selection_labels_internal_and_hdmi_and_persists_manual_choice(self):
        internal = "alsa_output.pci-0000_00_1b.0.analog-stereo"
        hdmi = "alsa_output.pci-0000_01_00.1.hdmi-stereo"
        self.runner.sinks = "\n".join((
            "0\t%s\tmodule\ts16le\tRUNNING" % internal,
            "1\t%s\tmodule\ts16le\tIDLE" % hdmi,
        ))
        self.runner.default_sink = hdmi

        devices = self.backend.list_audio_devices("output")

        self.assertEqual("主板模拟输出（内置扬声器，例如 ALC887）", devices["items"][0]["label"])
        self.assertEqual("HDMI / 显卡音频", devices["items"][1]["label"])
        self.assertTrue(devices["items"][1]["selected"])
        self.assertTrue(self.backend.set_audio_device("output", internal)["ok"])
        stored = json.loads(self.backend.local_path.read_text(encoding="utf-8"))
        self.assertEqual(internal, stored["audio_output_selection"])

    def test_lid_power_policy_updates_both_ac_and_battery_and_reads_back(self):
        result = self.backend.set_value("lid_close_action", "suspend")
        self.assertTrue(result["ok"])
        writes = [call for call in self.runner.calls if "-s" in call]
        properties = {call[call.index("-p") + 1] for call in writes}
        self.assertIn("/xfce4-power-manager/lid-action-on-ac", properties)
        self.assertIn("/xfce4-power-manager/lid-action-on-battery", properties)
        self.assertEqual("suspend", self.backend.get_value("lid_close_action")["value"])

    def test_default_browser_candidates_are_allowlisted_and_read_back(self):
        app_dir = pathlib.Path(self.tempdir.name) / "applications"
        app_dir.mkdir()
        (app_dir / "browser.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Browser\nCategories=Network;WebBrowser;\n",
            encoding="utf-8")
        self.backend.application_dirs = (app_dir,)
        listed = self.backend.list_default_apps("browser")
        self.assertEqual(["browser.desktop"], [item["id"] for item in listed["items"]])
        self.assertTrue(self.backend.set_default_app("browser", "browser.desktop")["ok"])
        self.assertFalse(self.backend.set_default_app("browser", "evil.desktop")["ok"])

    def test_hidden_user_application_shadows_same_named_system_candidate(self):
        user_dir = pathlib.Path(self.tempdir.name) / "user-applications"
        system_dir = pathlib.Path(self.tempdir.name) / "system-applications"
        user_dir.mkdir()
        system_dir.mkdir()
        desktop = "[Desktop Entry]\nType=Application\nName=Browser\nCategories=WebBrowser;\n"
        (system_dir / "browser.desktop").write_text(desktop, encoding="utf-8")
        (user_dir / "browser.desktop").write_text(
            desktop + "Hidden=true\n", encoding="utf-8")
        self.backend.application_dirs = (user_dir, system_dir)
        self.assertEqual([], self.backend.list_default_apps("browser")["items"])

    def test_system_autostart_entry_can_be_managed_through_user_override(self):
        system_dir = pathlib.Path(self.tempdir.name) / "etc-autostart"
        system_dir.mkdir()
        (system_dir / "helper.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Helper\nExec=helper\n",
            encoding="utf-8")
        self.backend.system_autostart_dirs = (system_dir,)
        items = self.backend.list_autostart()["items"]
        self.assertEqual("Helper", items[0]["label"])
        result = self.backend.set_autostart("helper.desktop", False)
        self.assertTrue(result["ok"])
        override = pathlib.Path(self.tempdir.name) / ".config/autostart/helper.desktop"
        self.assertIn("Hidden=true", override.read_text(encoding="utf-8"))

    def test_unknown_setting_is_rejected(self):
        result = self.backend.set_value("arbitrary_shell", "rm -rf /")
        self.assertFalse(result["ok"])
        self.assertEqual([], self.runner.calls)


class AdvancedSettingsSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETTINGS_PATH.read_text(encoding="utf-8")

    def test_advanced_page_uses_typed_backend(self):
        for marker in [
            '"高级设置", self.build_advanced',
            "ming-settings-backend",
            "backend_get",
            "backend_set_async",
            '"focus_mode"',
            '"dock_icon_size"',
            '"dock_zoom_percent"',
            '"dock_hide_mode"',
            '"reduced_motion"',
            '"compositor_profile"',
            '"notification_dnd"',
            '"lid_close_action"',
            '"音频输出"',
            '"音频输入"',
            '"默认应用"',
            '"登录时自动启动"',
        ]:
            self.assertIn(marker, self.source)

    def test_audio_output_row_uses_a_generic_internal_output_description(self):
        self.assertIn("主板模拟输出（内置扬声器，例如 ALC887）", self.source)
        self.assertIn("选择声音从主板模拟输出、HDMI、蓝牙或 USB 输出", self.source)
        self.assertNotIn("主板 ALC887、", self.source)

    def test_pages_are_built_lazily_and_backend_values_load_asynchronously(self):
        self.assertIn("self.page_builders", self.source)
        self.assertIn("self.page_built", self.source)
        self.assertIn("def backend_get_async", self.source)
        self.assertIn("run_capture_async", self.source)

    def test_wifi_scan_and_connect_do_not_run_commands_on_gtk_thread(self):
        scan = self.source[self.source.index("    def on_wifi_scan"):
                           self.source.index("    def on_wifi_connect")]
        connect = self.source[self.source.index("    def on_wifi_connect"):
                              self.source.index("    # ---- 3. 存储")]
        self.assertIn("run_task_async", scan)
        self.assertNotIn("run([", scan)
        self.assertIn("run_capture_async", connect)
        self.assertNotIn("run(cmd", connect)

    def test_generation_state_rejects_stale_and_destroyed_page_results(self):
        tree = ast.parse(self.source)
        nodes = [
            item for item in tree.body
            if isinstance(item, ast.ClassDef) and item.name == "GenerationState"]
        self.assertTrue(nodes, "GenerationState is required")
        node = nodes[0]
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), SETTINGS_PATH, "exec"), namespace)
        state = namespace["GenerationState"]()
        first = state.begin()
        second = state.begin()
        self.assertFalse(state.accept(first))
        self.assertTrue(state.accept(second))
        state.invalidate()
        self.assertFalse(state.accept(second))

    def test_hardware_page_build_and_refresh_never_run_probes_on_gtk_thread(self):
        self.assertIn("    def refresh_hardware_status", self.source)
        build = self.source[self.source.index("    def build_hardware"):
                            self.source.index("    def read_broadcom_status")]
        refresh = self.source[self.source.index("    def refresh_hardware_status"):
                              self.source.index("    def read_broadcom_status")]
        self.assertNotIn("run([", build)
        self.assertNotIn("pci_driver_summary(", build)
        self.assertIn("refresh_hardware_status", build)
        self.assertIn("run_task_async", refresh)
        self.assertIn("self.hardware_probe_state.begin()", refresh)
        self.assertIn("self.hardware_probe_state.accept(generation)", refresh)
        self.assertIn("get_root()", refresh)

    def test_hardware_probe_collects_commands_only_in_background_task(self):
        self.assertIn("def hardware_probe_snapshot", self.source)
        probe = self.source[self.source.index("def hardware_probe_snapshot"):
                            self.source.index("PAGE_ALIASES")]
        for marker in ["lscpu", "uname", "pci_driver_summary", "read_broadcom_status_snapshot"]:
            self.assertIn(marker, probe)

    def test_compositor_failure_parses_backend_error_and_reconciles_combo(self):
        combo = self.source[self.source.index("    def backend_combo_row"):
                            self.source.index("    def backend_scale_row")]
        setter = self.source[self.source.index("    def backend_set_async"):
                             self.source.index("    def schedule_backend_value")]
        self.assertIn("run_capture_async", setter)
        self.assertIn('result.get("error")', setter)
        self.assertIn("on_complete", setter)
        self.assertIn('key != "compositor_profile"', combo)
        self.assertIn("backend_get_async", combo)

    def test_compositor_combo_serializes_async_profile_changes(self):
        combo = self.source[self.source.index("    def backend_combo_row"):
                            self.source.index("    def backend_scale_row")]
        changed = combo[combo.index("            def changed"):
                        combo.index("            row.connect")]
        self.assertIn("control.set_sensitive(False)", changed)
        self.assertGreaterEqual(changed.count("control.set_sensitive(True)"), 3)
        self.assertLess(
            changed.index("control.set_sensitive(False)"),
            changed.index("self.backend_set_async"),
        )
        self.assertIn("set_selected", combo)

    def test_broadcom_completion_checks_operation_generation_and_page_root(self):
        action = self.source[self.source.index("    def on_broadcom_action"):
                             self.source.index("    def button_row")]
        self.assertIn("operation_generation", action)
        accept = action.index("self.hardware_probe_state.accept(operation_generation)")
        root = action.index("self.hardware_page.get_root()")
        refresh = action.index("self.refresh_broadcom_status()")
        toast = action.index("self.toast(")
        self.assertLess(accept, refresh)
        self.assertLess(root, refresh)
        self.assertLess(accept, toast)

    def test_advanced_page_does_not_launch_xfce_settings_manager(self):
        self.assertNotIn("xfce4-settings-manager", self.source)


if __name__ == "__main__":
    unittest.main()
