import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "assets" / "ming-toolbox.py"


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
            return 1, "", "文件不存在"

        controller = self.module.ToolboxController(runner=runner)
        status = controller.driver_center_status()

        self.assertEqual("ready", status["hardware"]["state"])
        self.assertEqual("unavailable", status["broadcom"]["state"])
        self.assertEqual("unavailable", status["surface"]["state"])
        self.assertEqual(
            [
                ("ming-hardware-status", "status", "--json"),
                ("/usr/local/sbin/ming-broadcom-driver", "status", "--json"),
                ("test", "-x", "/usr/local/bin/ming-surface-support"),
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


if __name__ == "__main__":
    unittest.main()
