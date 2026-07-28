import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset():
    path = ROOT / "assets" / "ming-appearance-control.py"
    spec = importlib.util.spec_from_file_location("ming_appearance_2641", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppearanceControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset()

    def test_config_normalization_keeps_current_wallpaper_and_bounds_sizes(self):
        config = self.api.normalize_config({
            "wallpaper": "default", "font_size": 99,
            "desktop_icon_scale": 5, "theme": "dark",
        })
        self.assertEqual("default", config["wallpaper"])
        self.assertEqual(self.api.DEFAULTS["font_size"], config["font_size"])
        self.assertEqual(self.api.DEFAULTS["desktop_icon_scale"], config["desktop_icon_scale"])
        self.assertEqual("dark", config["theme"])

    def test_invalid_primary_config_uses_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            primary = root / "appearance.json"
            last_good = root / "appearance.last-good.json"
            primary.write_text("{broken", encoding="utf-8")
            last_good.write_text(
                '{"theme":"light","wallpaper":"default","font_size":12}',
                encoding="utf-8")
            config = self.api.load_config(primary, last_good_path=last_good)
        self.assertEqual("light", config["theme"])
        self.assertEqual(12, config["font_size"])


class AppearanceDeploymentContracts(unittest.TestCase):
    def test_settings_and_login_enforcer_use_one_appearance_controller(self):
        settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("外观与指针", settings)
        self.assertIn("ming-appearance-control", settings)
        self.assertIn("ming-appearance-control.py", desktop)
        self.assertIn("ming-appearance-control reapply", desktop)
        self.assertIn("usr/local/bin/ming-appearance-control", build)


if __name__ == "__main__":
    unittest.main()
