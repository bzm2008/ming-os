import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


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


if __name__ == "__main__":
    unittest.main()
