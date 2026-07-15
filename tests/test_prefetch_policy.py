import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "assets" / "ming-prefetch.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_prefetch", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrefetchPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_hdd_only_policy_and_safe_path_allowlist(self):
        self.assertTrue(self.module.should_prefetch(rotational=True, low_memory=False, read_only=False))
        self.assertFalse(self.module.should_prefetch(rotational=False, low_memory=False, read_only=False))
        self.assertFalse(self.module.should_prefetch(rotational=True, low_memory=True, read_only=False))
        with tempfile.TemporaryDirectory() as temporary:
            allowed = pathlib.Path(temporary) / "usr" / "lib"
            allowed.mkdir(parents=True)
            first = allowed / "libgtk.so"
            second = allowed / "ming-settings"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            paths = self.module.filter_prefetch_paths(
                [str(first), str(second), "/home/user/private.key", "/tmp/secret", "/proc/self/maps"],
                prefixes=(os.path.realpath(temporary) + os.sep,),
            )
            self.assertEqual([str(first.resolve()), str(second.resolve())], paths)

    def test_file_and_byte_limits_are_enforced(self):
        paths = [f"/usr/lib/lib{index}.so" for index in range(200)]
        sizes = {path: 1024 for path in paths}
        selected = self.module.filter_prefetch_paths(paths, sizes=sizes, max_files=128, max_bytes=64 * 1024)
        self.assertLessEqual(len(selected), 128)
        self.assertLessEqual(sum(sizes[path] for path in selected), 64 * 1024)

    def test_application_index_round_trips_atomically_and_prunes_missing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            allowed = root / "lib"
            allowed.mkdir()
            library = allowed / "libhot.so"
            library.write_bytes(b"hot")
            with mock.patch.object(self.module, "ALLOWED_PREFIXES", (str(root) + os.sep,)):
                self.module.record_application_index(
                    "ming-settings.desktop",
                    [str(library), "/home/user/private.key", "/tmp/secret"],
                    home=root,
                )
                index_file = root / ".cache" / "ming-os" / "prefetch" / "index.json"
                self.assertTrue(index_file.exists())
                self.assertEqual(
                    [str(library.resolve())],
                    self.module.load_application_index("ming-settings.desktop", home=root),
                )
                library.unlink()
                self.assertEqual([], self.module.load_application_index("ming-settings.desktop", home=root))
                self.assertFalse(list(index_file.parent.glob("*.tmp-*")))


if __name__ == "__main__":
    unittest.main()
