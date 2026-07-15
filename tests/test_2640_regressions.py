import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def load_asset(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "assets" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PasswordScreenLockRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.account = load_asset("ming-account-control.py", "ming_account_control_2640")

    def test_setting_password_removes_nopasswdlogin_before_readback(self):
        calls = []

        def runner(command, input_text=None):
            calls.append(tuple(command))
            if command[:2] == ["passwd", "-S"]:
                return 0, "user P 2026-07-16 0 99999 7 -1", ""
            return 0, "", ""

        result = self.account.set_password("user", "secret\n", runner=runner)
        self.assertTrue(result["ok"])
        self.assertIn(("gpasswd", "-d", "user", "nopasswdlogin"), calls)
        self.assertEqual(("passwd", "-S", "user"), calls[-1])

    def test_clearing_password_restores_nopasswdlogin_for_passwordless_unlock(self):
        calls = []

        def runner(command, input_text=None):
            calls.append(tuple(command))
            if command[:2] == ["passwd", "-S"]:
                return 0, "user NP 2026-07-16 0 99999 7 -1", ""
            return 0, "", ""

        result = self.account.clear_password("user", runner=runner)
        self.assertTrue(result["ok"])
        self.assertIn(("gpasswd", "-a", "user", "nopasswdlogin"), calls)


class TimeSyncRegressionTests(unittest.TestCase):
    def test_timesyncd_is_a_required_runtime_and_has_a_retry_timer(self):
        self.assertIn("systemd-timesyncd \\", BASE)
        self.assertIn("ming-time-sync.timer", BASE)
        self.assertIn("OnBootSec=", BASE)
        time_sync = BASE.split("deploy_time_sync()", 1)[1].split(
            "deploy_performance_status()", 1
        )[0]
        self.assertNotIn("After=network-online.target", time_sync)
        self.assertNotIn("Wants=network-online.target", time_sync)


class MenuAndVersionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.menu_sync = load_asset("ming-thunar-menu-sync.py", "ming_thunar_menu_sync_2640")

    def test_preserved_thunar_menu_gets_an_additive_deb_action(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "Thunar" / "uca.xml"
            path.parent.mkdir()
            path.write_text(
                '<?xml version="1.0"?><actions><action><name>User action</name>'
                '<command>user-command %f</command></action></actions>',
                encoding="utf-8",
            )
            path.chmod(0o600)
            result = self.menu_sync.sync_menu(path)
            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            xml = path.read_text(encoding="utf-8")
            self.assertIn("User action", xml)
            self.assertIn("ming-deb-installer", xml)
            second = self.menu_sync.sync_menu(path)
            self.assertTrue(second["ok"])
            self.assertFalse(second["changed"])

    def test_final_thunar_menu_matches_deb_case_insensitively(self):
        menu = DESKTOP.split("configure_simplified_menus() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        self.assertIn("安装 DEB 软件包", menu)
        self.assertIn("<patterns>*.deb;*.DEB</patterns>", menu)
        self.assertIn("ming-package-install-gui", menu)
        self.assertIn("application/vnd.debian.binary-package", menu)

    def test_release_identity_skips_preview_and_targets_2640(self):
        self.assertIn('readonly MING_OS_VERSION="26.4.0"', BUILD)
        self.assertIn('readonly ISO_VOLUME_ID="MING_OS_2640"', BUILD)
        self.assertNotIn('readonly MING_OS_VERSION="26.3.3"', BUILD)

    def test_upgrade_document_is_installed_on_the_desktop(self):
        self.assertIn("Ming OS 26.3.2 到 26.4.0 升级说明.md", DESKTOP)
        self.assertIn("26.3.2", DESKTOP)
        self.assertIn("官方签名 bootstrap", DESKTOP)
        self.assertIn("事务型 OTA", DESKTOP)


if __name__ == "__main__":
    unittest.main()
