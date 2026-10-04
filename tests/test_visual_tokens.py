import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKENS = ROOT / "assets" / "ming-ui-tokens.py"
SURFACES = (
    ROOT / "assets" / "ming-store.py",
    ROOT / "assets" / "ming-settings.py",
    ROOT / "assets" / "ming-phone-desktop.py",
)
DESKTOP_MODULE = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


def load_tokens():
    spec = importlib.util.spec_from_file_location("ming_ui_tokens", TOKENS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingVisualTokenTests(unittest.TestCase):
    def test_shared_token_module_exposes_mint_palette_and_state_colors(self):
        module = load_tokens()
        for name in (
            "canvas", "surface", "surface_elevated", "accent", "accent_strong",
            "text", "muted", "success", "warning", "danger", "focus",
        ):
            with self.subTest(name=name):
                self.assertRegex(module.TOKENS[name], r"^#[0-9A-Fa-f]{6}$")

    def test_each_surface_loads_shared_tokens_and_declares_focus_state(self):
        for surface in SURFACES:
            source = surface.read_text(encoding="utf-8")
            with self.subTest(surface=surface.name):
                self.assertIn("ming-ui-tokens.py", source)
                self.assertIn("TOKENS", source)
                self.assertIn(":focus", source)

    def test_store_and_settings_keep_stable_card_or_group_geometry(self):
        store = SURFACES[0].read_text(encoding="utf-8")
        settings = SURFACES[1].read_text(encoding="utf-8")
        self.assertIn("min-height: 218px", store)
        self.assertIn("min-height: 40px", settings)

    def test_shared_token_asset_is_deployed_and_build_validated(self):
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn("ming-ui-tokens.py", desktop)
        self.assertIn('"usr/local/lib/ming-os/ming-ui-tokens.py", "TOKENS"', build)


if __name__ == "__main__":
    unittest.main()
