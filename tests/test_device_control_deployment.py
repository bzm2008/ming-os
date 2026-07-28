import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"
BACKLIGHT_RULES = ROOT / "assets" / "90-ming-backlight.rules"


class DeviceControlDeploymentContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")

    def test_device_control_is_installed_as_library_and_cli(self):
        self.assertIn('"${asset_dir}/ming-device-control.py"', self.desktop)
        self.assertIn(
            'install -m 0644 "${asset_dir}/ming-device-control.py" '
            '"${lib_dir}/ming-device-control.py"',
            self.desktop,
        )
        self.assertIn(
            'install -m 0755 "${asset_dir}/ming-device-control.py" '
            '/usr/local/bin/ming-device-control',
            self.desktop,
        )

    def test_build_gate_requires_device_control_cli(self):
        self.assertIn('usr/local/bin/ming-device-control', self.build)
        self.assertIn('status --json', self.build)

    def test_software_brightness_state_is_restored_after_x11_is_ready(self):
        self.assertIn('ming-software-brightness.desktop', self.desktop)
        self.assertIn(
            'ming-device-control reapply-brightness --wait-seconds 10 --json',
            self.desktop)

    def test_physical_backlight_has_logind_acl_and_video_group_write_access(self):
        rules = BACKLIGHT_RULES.read_text(encoding="utf-8")
        self.assertIn('SUBSYSTEM=="backlight"', rules)
        self.assertIn('TAG+="uaccess"', rules)
        self.assertIn('/bin/chgrp video /sys/class/backlight/%k/brightness', rules)
        self.assertIn('/bin/chmod g+w /sys/class/backlight/%k/brightness', rules)
        self.assertIn(
            'install -m 0644 "${asset_dir}/90-ming-backlight.rules" '
            '/etc/udev/rules.d/90-ming-backlight.rules',
            self.desktop)


if __name__ == "__main__":
    unittest.main()
