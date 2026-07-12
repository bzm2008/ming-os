import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "assets" / "ming-ota-target-guard.py"


def load_guard():
    spec = importlib.util.spec_from_file_location("ming_ota_target_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OtaTargetGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_guard()

    def test_rejects_root_target_on_preservation_disk(self):
        disks = {
            "/dev/sda2": {"/dev/sda"},
            "/dev/sda3": {"/dev/sda"},
        }
        ok, message = self.guard.validate_target(
            [{"mountPoint": "/", "device": "/dev/sda2"}],
            "/dev/sda3",
            disk_resolver=lambda device: disks.get(device, set()),
        )
        self.assertFalse(ok)
        self.assertIn("same physical disk", message)

    def test_accepts_root_target_on_another_physical_disk(self):
        disks = {
            "/dev/sda2": {"/dev/sda"},
            "/dev/sdb1": {"/dev/sdb"},
        }
        ok, message = self.guard.validate_target(
            [{"mountPoint": "/", "device": "/dev/sda2"}],
            "/dev/sdb1",
            disk_resolver=lambda device: disks.get(device, set()),
        )
        self.assertTrue(ok, message)

    def test_rejects_missing_or_ambiguous_disk_ancestry(self):
        ok, _message = self.guard.validate_target(
            [{"mountPoint": "/", "device": "/dev/mapper/root"}],
            "/dev/sdb1",
            disk_resolver=lambda _device: set(),
        )
        self.assertFalse(ok)

    def test_rejects_partition_plan_without_root_mount(self):
        ok, message = self.guard.validate_target(
            [{"mountPoint": "/home", "device": "/dev/sda2"}],
            "/dev/sdb1",
            disk_resolver=lambda device: {device},
        )
        self.assertFalse(ok)
        self.assertIn("root target", message)


if __name__ == "__main__":
    unittest.main()
