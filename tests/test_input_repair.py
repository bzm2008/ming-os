import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPAIR = ROOT / "assets" / "ming-input-repair.py"


def load_repair():
    if not REPAIR.exists():
        raise AssertionError("ming-input-repair.py is missing")
    spec = importlib.util.spec_from_file_location("ming_input_repair", REPAIR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InputRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repair = load_repair()

    def test_replaces_legacy_xinputrc_with_ming_environment_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "zero"
            home.mkdir()
            xinputrc = home / ".xinputrc"
            xinputrc.write_text("run_im fcitx5\nfcitx5 -d --replace\n", encoding="utf-8")

            result = self.repair.repair_home(home, owner=None)

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertTrue((home / ".xinputrc.ming-legacy-backup").is_file())
            text = xinputrc.read_text(encoding="utf-8")
            self.assertIn("export XMODIFIERS=@im=fcitx", text)
            self.assertNotIn("run_im fcitx5", text)
            self.assertNotIn("fcitx5 -d --replace", text)
            autostart = home / ".config" / "autostart" / "fcitx5.desktop"
            self.assertIn("Exec=/usr/local/bin/ming-fcitx5-watchdog", autostart.read_text(encoding="utf-8"))

    def test_idempotent_when_ming_files_already_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "zero"
            home.mkdir()

            first = self.repair.repair_home(home, owner=None)
            second = self.repair.repair_home(home, owner=None)

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertFalse((home / ".xinputrc.ming-legacy-backup").exists())


if __name__ == "__main__":
    unittest.main()
