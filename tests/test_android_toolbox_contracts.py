import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLBOX = ROOT / "assets" / "ming-toolbox.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
APPS = ROOT / "modules" / "02_apps.sh"
BUILD = ROOT / "build_onion_os.sh"


class AndroidToolboxContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ming_toolbox", TOOLBOX)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.desktop_source = DESKTOP.read_text(encoding="utf-8")
        cls.apps_source = APPS.read_text(encoding="utf-8")

    def test_toolbox_exposes_android_actions_but_does_not_autostart_runtime(self):
        actions = self.module.toolbox_actions()
        self.assertIn("android_runtime_status", actions)
        self.assertIn("install_android_apk", actions)
        self.assertIn("manage_android_apps", actions)
        self.assertIn("ming-android", self.desktop_source)
        self.assertNotIn("waydroid session start", self.desktop_source)

    def test_android_lab_flags_are_off_and_application_scoped(self):
        state = self.module.default_android_lab_state()
        self.assertEqual("application", state["scope"])
        for key in ("arm_translation", "software_rendering", "window_compat", "clipboard_share", "directory_share", "debug_logs"):
            self.assertFalse(state[key])

    def test_authorized_android_route_is_narrow_and_polkit_is_not_any_user(self):
        self.assertIn('"android"', self.desktop_source)
        self.assertIn("install-deps", self.desktop_source)
        self.assertIn("start-container", self.desktop_source)
        self.assertIn("stop-container", self.desktop_source)
        self.assertIn("repair", self.desktop_source)
        self.assertNotIn("allow_any>auth_admin", self.apps_source)

    def test_build_gate_validates_android_runtime_and_root_helper(self):
        build_source = BUILD.read_text(encoding="utf-8")
        self.assertIn("usr/local/bin/ming-android-runtime", build_source)
        self.assertIn("usr/local/sbin/ming-android-runtime", build_source)
        self.assertIn("org.ming.android.runtime.policy", build_source)
        self.assertIn("AndroidRuntime", build_source)
        self.assertIn("install-deps", build_source)


if __name__ == "__main__":
    unittest.main()
