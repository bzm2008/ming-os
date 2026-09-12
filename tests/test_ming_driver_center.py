import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "assets" / "ming-toolbox.py"
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")


class MingDriverCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ming_toolbox_driver_center", TOOLBOX)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_driver_center_is_a_compatible_section_with_fixed_profiles(self):
        self.assertIn("drivers", self.module.ALL_SECTIONS)
        self.assertEqual("驱动中心", self.module.SECTION_LABELS["drivers"])
        self.assertEqual(
            {"broadcom_install", "broadcom_restore", "surface_install"},
            set(self.module.DRIVER_ACTIONS),
        )

    def test_status_readback_reports_available_and_unavailable_tools(self):
        calls = []

        def runner(command, timeout=60):
            calls.append(tuple(command))
            if command[0] == "ming-hardware-status":
                return 0, '{"devices": {"graphics": {"state": "ready"}}}', ""
            if command[0] == "/usr/local/sbin/ming-broadcom-driver":
                return 127, "", "驱动管理器不可用"
            return 0, '{"state":"unsupported","action":"none","message":"未检测到 Surface 设备。"}', ""

        controller = self.module.ToolboxController(runner=runner)
        status = controller.driver_center_status()

        self.assertEqual("ready", status["hardware"]["state"])
        self.assertEqual("unavailable", status["broadcom"]["state"])
        self.assertEqual("unsupported", status["surface"]["state"])
        self.assertEqual(
            [
                ("ming-hardware-status", "status", "--json"),
                ("/usr/local/sbin/ming-broadcom-driver", "status", "--json"),
                ("/usr/local/bin/ming-surface-support", "status", "--json"),
            ],
            calls,
        )

    def test_driver_action_rejects_arbitrary_command_and_reports_failure(self):
        calls = []

        def runner(command, timeout=60):
            calls.append(tuple(command))
            return 1, "", "授权失败"

        controller = self.module.ToolboxController(runner=runner)
        result = controller.run_driver_action("broadcom_install")

        self.assertFalse(result["ok"])
        self.assertEqual("action_failed", result["state"])
        self.assertIn("授权失败", result["error"])
        self.assertEqual(
            [("/usr/local/bin/ming-authorized-action", "broadcom", "install")],
            calls,
        )
        with self.assertRaises(ValueError):
            controller.run_driver_action("echo rm -rf /")

    def test_surface_action_uses_the_same_authorization_bridge(self):
        calls = []

        def runner(command, timeout=60):
            calls.append(tuple(command))
            return 1, "", "授权失败"

        controller = self.module.ToolboxController(runner=runner)
        result = controller.run_driver_action("surface_install")

        self.assertFalse(result["ok"])
        self.assertEqual(
            [("/usr/local/bin/ming-authorized-action", "surface", "install")],
            calls,
        )

    def test_surface_authorized_route_and_helper_propagate_install_failures(self):
        bridge = DESKTOP.split(
            "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'", 1
        )[1].split("\nMINGAUTHORIZE", 1)[0]
        self.assertIn("surface)", bridge)
        self.assertIn('"$1" == install', bridge)
        surface = BASE.split(
            "cat > /usr/local/bin/ming-surface-support << 'SURFACE'", 1
        )[1].split("\nSURFACE\n", 1)[0]
        install_body = surface.split("install_support()", 1)[1]
        self.assertNotIn("|| true", install_body)
        self.assertIn('chmod 0600 "${LOG}"', surface)
        self.assertIn("rollback", surface)
        self.assertIn("KEY_FINGERPRINT=\"87DEFA4AB94A99A4C8C3112556C464BAAC421453\"", surface)
        self.assertIn("PREF_FILE=\"/etc/apt/preferences.d/linux-surface\"", surface)
        self.assertIn("Pin-Priority: 100", surface)
        self.assertNotIn("trap 'rm -rf \"${work}\"' RETURN", surface)
        self.assertIn("trap cleanup_support EXIT", surface)

    def test_surface_ready_requires_kernel_headers_and_iptsd_readback(self):
        surface = BASE.split(
            "cat > /usr/local/bin/ming-surface-support << 'SURFACE'", 1
        )[1].split("\nSURFACE\n", 1)[0]
        status_body = surface.split("status_json() {", 1)[1].split(
            "\n}\n\nrequire_root()", 1
        )[0]
        normalized = " ".join(status_body.replace("\\", " ").split())
        self.assertIn(
            '"${trusted}" == true && "${source}" == true && "${kernel}" == true '
            '&& "${headers}" == true && "${ipts}" == true',
            normalized,
        )

    def test_surface_install_rechecks_all_core_packages_after_apt(self):
        surface = BASE.split(
            "cat > /usr/local/bin/ming-surface-support << 'SURFACE'", 1
        )[1].split("\nSURFACE\n", 1)[0]
        install_body = surface.split("install_support()", 1)[1]
        normalized = " ".join(install_body.replace("\\", " ").split())
        self.assertIn(
            "if ! package_installed linux-image-surface || ! package_installed "
            "linux-headers-surface || ! package_installed iptsd; then",
            normalized,
        )

    def test_surface_status_json_does_not_depend_on_optional_jq(self):
        surface = BASE.split(
            "cat > /usr/local/bin/ming-surface-support << 'SURFACE'", 1
        )[1].split("\nSURFACE\n", 1)[0]
        status_body = surface.split("status_json() {", 1)[1].split(
            "\n}\n\nrequire_root()", 1
        )[0]
        self.assertIn("python3", status_body)
        self.assertNotIn("detected=%s\\nsource_configured=%s", status_body)

    def test_build_gate_validates_surface_helper_and_pinned_source(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn('"usr/local/bin/ming-surface-support"', build)
        self.assertIn('"status --json"', build)
        self.assertIn("KEY_FINGERPRINT", build)
        self.assertIn("Pin-Priority: 100", build)

    def test_settings_surface_button_uses_the_authorization_bridge(self):
        start = SETTINGS.index('surface = Gtk.Button(label="安装 Surface 支持")')
        section = SETTINGS[start:SETTINGS.index("        input_repair =", start)]
        self.assertIn("on_surface_support", section)
        handler = SETTINGS.split("    def on_surface_support", 1)[1].split(
            "    def button_row", 1
        )[0]
        self.assertIn('"/usr/local/bin/ming-authorized-action", "surface", "install"', handler)
        self.assertNotIn('self.pkexec_cmd("ming-surface-support")', section)


if __name__ == "__main__":
    unittest.main()
