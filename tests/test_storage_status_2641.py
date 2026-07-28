import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset():
    path = ROOT / "assets" / "ming-storage-status.py"
    spec = importlib.util.spec_from_file_location("ming_storage_status_2641", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset()

    def test_parser_flattens_local_disks_and_partitions_but_excludes_virtual_devices(self):
        snapshot = {
            "blockdevices": [{
                "name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk",
                "size": 1000, "fstype": None, "label": None, "uuid": None,
                "mountpoints": [None], "children": [
                    {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part",
                     "size": 500, "fstype": "ext4", "label": "root", "uuid": "u1",
                     "mountpoints": ["/"]},
                ],
            }, {
                "name": "loop0", "path": "/dev/loop0", "type": "loop",
                "size": 10, "fstype": "squashfs", "mountpoints": ["/snap/x"],
            }, {
                "name": "zram0", "path": "/dev/zram0", "type": "disk",
                "size": 20, "mountpoints": [None],
            }]
        }
        result = self.api.parse_lsblk(snapshot)
        self.assertEqual(["/dev/nvme0n1", "/dev/nvme0n1p1"], [item["path"] for item in result])
        self.assertEqual("mounted", result[1]["state"])

    def test_partition_snapshot_uses_bounded_read_only_lsblk_and_reports_bad_json(self):
        commands = []

        def runner(command, timeout):
            commands.append((command, timeout))
            return 0, json.dumps({"blockdevices": []}), ""

        result = self.api.partition_snapshot(runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual([], result["partitions"])
        self.assertLessEqual(commands[0][1], 3)
        self.assertEqual("lsblk", commands[0][0][0])
        self.assertNotIn("mount", " ".join(commands[0][0]))

        bad = self.api.partition_snapshot(
            runner=lambda _command, timeout: (0, "{broken", ""))
        self.assertFalse(bad["ok"])
        self.assertIn("JSON", bad["error"])


class StorageDeploymentContracts(unittest.TestCase):
    def test_helper_is_deployed_and_settings_refreshes_asynchronously(self):
        helper = (ROOT / "assets" / "ming-storage-status.py").read_text(encoding="utf-8")
        settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("lsblk", helper)
        self.assertIn("run_task_async", settings)
        self.assertIn("ming-storage-status.py", desktop)
        self.assertIn("usr/local/bin/ming-storage-status", build)


if __name__ == "__main__":
    unittest.main()
