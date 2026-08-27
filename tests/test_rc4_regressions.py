import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
STORE = (ROOT / "assets" / "ming-store.py").read_text(encoding="utf-8")
STORE_CONTROL = (ROOT / "assets" / "ming-store-control.py").read_text(encoding="utf-8")


class StoreAuthorizationContracts(unittest.TestCase):
    def test_store_policy_uses_one_scoped_helper_and_disables_nonactive_callers(self):
        policy = DESKTOP.split(
            "cat > /usr/share/polkit-1/actions/org.mingos.store.manage.policy << 'MINGSTOREPOLICY'",
            1,
        )[1].split("MINGSTOREPOLICY", 1)[0]
        self.assertIn("/usr/local/sbin/ming-store-control", policy)
        self.assertIn("<allow_any>no</allow_any>", policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", policy)
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", policy)

    def test_store_ui_passes_only_action_and_request_id_to_authorization(self):
        self.assertIn('"/usr/local/bin/ming-authorized-action", "store", action', STORE)
        self.assertIn("REQUEST_ID.fullmatch(request_id)", STORE_CONTROL)
        self.assertNotIn("shell=True", STORE + STORE_CONTROL)
        self.assertNotIn("eval ", STORE + STORE_CONTROL)


class WifiDialogContracts(unittest.TestCase):
    def test_password_dialog_uses_compatibility_constructors_and_fallback_actions(self):
        connect = SETTINGS.split(
            "    def on_wifi_connect(self, _btn, network):", 1
        )[1].split("    def apply_wifi_connect_result", 1)[0]
        self.assertIn("MessageDialog.new", connect)
        self.assertIn("Gtk.PasswordEntry()", connect)
        self.assertIn("set_show_peek_icon", connect)
        self.assertIn("打开网络设置", connect)
        self.assertIn("重新扫描", connect)


class AptSourceContracts(unittest.TestCase):
    def test_runtime_source_selector_and_full_upgrade_convergence_are_deployed(self):
        self.assertIn("ming-apt-source-select", BASE)
        self.assertIn("apt-get full-upgrade", BASE)
        self.assertIn("InRelease", BASE)
        self.assertIn("deb.debian.org", BASE)
        self.assertIn("MING_DEBIAN_MIRROR", BUILD)


class DiagnosticsAndRc4Contracts(unittest.TestCase):
    def test_diagnostic_uploader_and_manual_confirmation_are_present(self):
        self.assertIn("ming-diagnostic-upload", BASE)
        self.assertIn("手动确认", SETTINGS)
        self.assertIn("ming.diagnostic.v1", BASE)

    def test_build_is_rc4_and_requires_verified_xiahai_asset(self):
        self.assertIn('readonly MING_OS_BUILD_SUFFIX="rc4"', BUILD)
        self.assertIn("MING_XIAHAI_DEB_SOURCE", BUILD)
        self.assertIn("xiahai-xiaoming_0.0.2-beta_amd64.deb", BUILD)
        self.assertIn("dpkg-deb --info", BUILD)
        self.assertIn("sha256", BUILD.lower())


if __name__ == "__main__":
    unittest.main()
