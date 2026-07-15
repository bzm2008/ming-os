import importlib.util
import pathlib
import stat
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "assets" / "ming-shell-common.py"


def load_common():
    spec = importlib.util.spec_from_file_location("ming_shell_common", COMMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def root_owned_regular_file(mode=0o644):
    return types.SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=0)


class TrustedDesktopActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load_common()

    def test_accepts_protected_system_desktop_wrapper_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertTrue(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=lambda _path: root_owned_regular_file(),
            ))

    def test_rejects_user_desktop_entry_even_when_stat_looks_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            system_dir = root / "usr-share-applications"
            user_dir = root / "user-applications"
            system_dir.mkdir()
            user_dir.mkdir()
            desktop = user_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=lambda _path: root_owned_regular_file(),
            ))

    def test_rejects_candidate_when_resolution_indicates_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            target = system_dir / "target.desktop"
            source = system_dir / "store-wrapper.desktop"
            target.write_text("[Desktop Entry]\n", encoding="utf-8")
            source.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(path):
                path = pathlib.Path(path)
                if path == source:
                    return target
                return path.resolve(strict=True)

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                source,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=lambda _path: root_owned_regular_file(),
            ))

    def test_rejects_group_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=lambda _path: root_owned_regular_file(0o664),
            ))

    def test_rejects_other_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=lambda _path: root_owned_regular_file(0o646),
            ))

    def test_rejects_nonstandard_desktop_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.DESKTOP"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=lambda _path: root_owned_regular_file(),
            ))


if __name__ == "__main__":
    unittest.main()
