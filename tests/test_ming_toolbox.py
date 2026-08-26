import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "assets" / "ming-toolbox.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
APPS = ROOT / "modules" / "02_apps.sh"


class MingToolboxContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ming_toolbox", TOOLBOX)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.desktop_source = DESKTOP.read_text(encoding="utf-8")
        cls.apps_source = APPS.read_text(encoding="utf-8")

    def test_three_sections_and_empty_official_catalog(self):
        self.assertEqual(
            ("official", "toolbox", "lab"),
            self.module.SECTIONS,
        )
        self.assertEqual([], self.module.official_catalog())
        self.assertIn("官方软件", self.module.SECTION_LABELS.values())
        self.assertIn("工具箱", self.module.SECTION_LABELS.values())
        self.assertIn("Ming 实验室", self.module.SECTION_LABELS.values())

    def test_lab_options_are_disabled_by_default_and_scoped_per_app(self):
        state = self.module.default_lab_state()
        self.assertFalse(state["dxvk"])
        self.assertFalse(state["proton"])
        self.assertFalse(state["game_mode"])
        self.assertEqual("application", state["scope"])
        self.assertNotIn("sudo", self.module.LAB_ACTIONS)

    def test_lab_state_is_persisted_per_application(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.module.LabStateStore(root=directory)
            state.set("demo-app", "dxvk", True)
            self.assertTrue(state.get("demo-app")["dxvk"])
            self.assertFalse(state.get("other-app")["dxvk"])

    def test_stable_toolbox_actions_are_explicit(self):
        actions = self.module.toolbox_actions()
        self.assertIn("install_windows_app", actions)
        self.assertIn("repair_desktop_entries", actions)
        self.assertIn("diagnostics", actions)
        self.assertNotIn("run_shell", actions)

    def test_controller_uninstall_uses_structured_wine_manager_command(self):
        calls = []
        controller = self.module.ToolboxController(
            home="C:/Users/demo",
            runner=lambda command, timeout=60: calls.append(tuple(command)) or (
                0, '{"ok":true,"state":"uninstalled"}', ""
            ),
        )
        result = controller.uninstall_windows_app("demo")
        self.assertTrue(result["ok"])
        self.assertEqual(
            ("/usr/local/bin/ming-wine-installer", "uninstall", "demo"),
            calls[0],
        )

    def test_windows_mime_and_toolbox_desktop_entry_are_wired(self):
        self.assertIn("*.exe;*.EXE;*.msi;*.MSI", self.desktop_source)
        self.assertIn("ming-toolbox", self.desktop_source)
        self.assertIn("Ming 工具箱", self.desktop_source)

    def test_spark_routes_local_windows_files_to_toolbox(self):
        self.assertIn("ming-toolbox --install-windows", self.apps_source)
        self.assertIn(".exe", self.apps_source)
        self.assertIn(".msi", self.apps_source)
        self.assertIn("ming-spark-windows-install", self.apps_source)
        self.assertIn('wine-install', self.apps_source)
        self.assertNotIn("eval ", self.apps_source)


if __name__ == "__main__":
    unittest.main()
