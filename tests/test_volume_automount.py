import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VOLUME = ROOT / "assets" / "ming-volume-automount.py"


def load_volume():
    if not VOLUME.exists():
        raise AssertionError("ming-volume-automount.py is missing")
    spec = importlib.util.spec_from_file_location("ming_volume_automount", VOLUME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VolumeAutomountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.volume = load_volume()

    def test_mounts_safe_data_partitions_on_same_disk_as_root(self):
        rows = [
            {"name": "sda1", "type": "part", "fstype": "ext4", "uuid": "root", "label": "", "mountpoint": "/"},
            {"name": "sda2", "type": "part", "fstype": "", "uuid": "", "label": "", "mountpoint": ""},
            {"name": "sda5", "type": "part", "fstype": "ntfs", "uuid": "soft-uuid", "label": "软件", "mountpoint": ""},
            {"name": "sda6", "type": "part", "fstype": "ntfs", "uuid": "docs-uuid", "label": "文档", "mountpoint": ""},
        ]

        plan = self.volume.build_mount_plan(rows, user="zero")

        self.assertEqual(["/dev/sda5", "/dev/sda6"], [item["device"] for item in plan["mount"]])
        self.assertEqual("/media/zero/软件", plan["mount"][0]["target"])
        self.assertEqual("/media/zero/文档", plan["mount"][1]["target"])
        skipped = {item["device"]: item["reason"] for item in plan["skip"]}
        self.assertEqual("already-mounted", skipped["/dev/sda1"])
        self.assertEqual("missing-filesystem", skipped["/dev/sda2"])

    def test_rejects_system_crypto_swap_and_unknown_partitions(self):
        rows = [
            {"name": "nvme0n1p1", "type": "part", "fstype": "vfat", "uuid": "efi", "label": "EFI", "mountpoint": "", "partlabel": "EFI System"},
            {"name": "sdb1", "type": "part", "fstype": "swap", "uuid": "swap", "label": "", "mountpoint": ""},
            {"name": "sdb2", "type": "part", "fstype": "crypto_LUKS", "uuid": "crypt", "label": "secure", "mountpoint": ""},
            {"name": "sdb3", "type": "part", "fstype": "iso9660", "uuid": "iso", "label": "Ming OS", "mountpoint": ""},
        ]

        plan = self.volume.build_mount_plan(rows, user="zero")

        self.assertEqual([], plan["mount"])
        reasons = {item["device"]: item["reason"] for item in plan["skip"]}
        self.assertEqual("system-partition", reasons["/dev/nvme0n1p1"])
        self.assertEqual("unsupported-filesystem", reasons["/dev/sdb1"])
        self.assertEqual("locked-or-encrypted", reasons["/dev/sdb2"])
        self.assertEqual("installer-or-recovery-media", reasons["/dev/sdb3"])


if __name__ == "__main__":
    unittest.main()
