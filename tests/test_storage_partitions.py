import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-storage-status.py"
SETTINGS_PATH = ROOT / "assets" / "ming-settings.py"
DESKTOP_MODULE_PATH = ROOT / "modules" / "03_desktop.sh"
BUILD_PATH = ROOT / "build_onion_os.sh"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_storage_status", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoragePartitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_parser_keeps_mounted_and_unmounted_local_partitions(self):
        snapshot = {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "path": "/dev/nvme0n1",
                    "type": "disk",
                    "size": 512110190592,
                    "fstype": None,
                    "label": None,
                    "uuid": None,
                    "mountpoints": [],
                    "children": [
                        {
                            "name": "nvme0n1p1",
                            "path": "/dev/nvme0n1p1",
                            "type": "part",
                            "size": 272629760,
                            "fstype": "vfat",
                            "label": "EFI",
                            "uuid": "A1B2-C3D4",
                            "mountpoints": ["/boot/efi"],
                        },
                        {
                            "name": "nvme0n1p2",
                            "path": "/dev/nvme0n1p2",
                            "type": "part",
                            "size": 200000000000,
                            "fstype": "ntfs",
                            "label": "DATA",
                            "uuid": "data-uuid",
                            "mountpoints": [],
                        },
                        {
                            "name": "nvme0n1p3",
                            "path": "/dev/nvme0n1p3",
                            "type": "part",
                            "size": 300000000000,
                            "fstype": "ext4",
                            "label": None,
                            "uuid": "root-uuid",
                            "mountpoints": ["/"],
                        },
                    ],
                },
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "size": 1000000000,
                    "fstype": "ext4",
                    "label": "External data",
                    "uuid": "whole-disk-uuid",
                    "mountpoints": ["/media/user/data"],
                },
                {
                    "name": "loop0",
                    "path": "/dev/loop0",
                    "type": "loop",
                    "size": 10000000,
                    "fstype": "squashfs",
                    "label": None,
                    "uuid": None,
                    "mountpoints": ["/snap/example"],
                },
            ]
        }

        partitions = self.module.parse_partitions(snapshot)

        self.assertEqual(
            ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3", "/dev/sda"],
            [item["path"] for item in partitions],
        )
        by_path = {item["path"]: item for item in partitions}
        self.assertEqual("mounted", by_path["/dev/nvme0n1p1"]["state"])
        self.assertEqual("unmounted", by_path["/dev/nvme0n1p2"]["state"])
        self.assertEqual(["/"], by_path["/dev/nvme0n1p3"]["mountpoints"])
        self.assertEqual("External data", by_path["/dev/sda"]["label"])

    def test_snapshot_uses_bounded_lsblk_json_and_returns_a_readable_error(self):
        commands = []

        def runner(command, timeout):
            commands.append((command, timeout))
            return 1, "", "lsblk unavailable"

        result = self.module.partition_snapshot(runner=runner)

        self.assertFalse(result["ok"])
        self.assertEqual([], result["partitions"])
        self.assertIn("lsblk unavailable", result["error"])
        self.assertEqual(
            [
                "lsblk", "--json", "--bytes", "--output",
                "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS",
            ],
            commands[0][0],
        )
        self.assertLessEqual(commands[0][1], 3)


class StoragePartitionUiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = SETTINGS_PATH.read_text(encoding="utf-8")
        cls.desktop_module = DESKTOP_MODULE_PATH.read_text(encoding="utf-8")
        cls.build = BUILD_PATH.read_text(encoding="utf-8")

    def test_storage_page_uses_the_structured_partition_helper_off_the_gtk_thread(self):
        storage_page = self.settings[
            self.settings.index("    def build_storage(self):"):
            self.settings.index("    def _hsize", self.settings.index("    def build_storage(self):"))
        ]
        self.assertIn("本机分区", storage_page)
        self.assertIn("self.refresh_storage_partitions()", storage_page)
        self.assertIn("def storage_partition_snapshot", self.settings)
        refresh = self.settings[
            self.settings.index("    def refresh_storage_partitions"):
            self.settings.index("    def _hsize", self.settings.index("    def refresh_storage_partitions"))
        ]
        self.assertIn("run_task_async(storage_partition_snapshot, done)", refresh)
        self.assertIn("self.storage_partition_probe_state.begin()", refresh)

    def test_storage_status_helper_is_deployed_and_rootfs_validated(self):
        self.assertIn("ming-storage-status.py", self.desktop_module)
        self.assertIn("/usr/local/bin/ming-storage-status", self.desktop_module)
        self.assertIn("usr/local/bin/ming-storage-status", self.build)

    def test_rootfs_gate_accepts_a_no_sysfs_diagnostic_but_still_validates_json(self):
        """The build chroot intentionally lacks sysfs after cleanup."""
        start = 'storage_status="$(chroot_exec /usr/local/bin/ming-storage-status partitions --json)"'
        self.assertIn(start, self.build)
        gate = self.build[self.build.index(start):self.build.index(
            'if ! chroot_exec /usr/local/bin/ming-files --check-runtime',
            self.build.index(start))]
        self.assertIn("storage_status_rc=0", gate)
        self.assertIn("storage_status_rc=$?", gate)
        self.assertIn('"${storage_status_rc}" -ne 0 && "${storage_status_rc}" -ne 2', gate)
        self.assertIn("printf '%s\\n' \"${storage_status}\" | python3 -c", gate)


if __name__ == "__main__":
    unittest.main()
