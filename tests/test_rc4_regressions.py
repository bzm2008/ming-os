import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class SparkAuthorizationContracts(unittest.TestCase):
    def test_both_vendor_actions_use_the_ming_package_control_helper(self):
        policies = re.findall(
            r"cat > /usr/share/polkit-1/actions/(?:store\.spark-app\.[^ ]+)\.policy.*?\n(.*?)\n[A-Z]+POLICY",
            APPS,
            re.S,
        )
        self.assertGreaterEqual(len(policies), 2)
        for policy in policies:
            self.assertIn("org.freedesktop.policykit.exec.path", policy)
            self.assertIn(
                "/usr/local/sbin/ming-spark-package-control",
                policy,
            )
            self.assertNotIn("<allow_any>yes</allow_any>", policy)

    def test_vendor_pass_auth_never_executes_arbitrary_pkexec_arguments(self):
        pass_auth = APPS.split(
            "cat > /opt/durapps/spark-store/bin/store-helper/pass-auth.sh << 'MINGSPARKPASSAUTH'",
            1,
        )[1].split("MINGSPARKPASSAUTH", 1)[0]
        self.assertNotIn('exec pkexec "$@"', pass_auth)
        self.assertIn("ming-authorized-action spark", pass_auth)
        self.assertNotIn("exec /usr/local/sbin/ming-spark-package-control", pass_auth)


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
