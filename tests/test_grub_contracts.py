import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
THEME = ROOT / "assets" / "grub-theme" / "theme.txt"
BUILD = ROOT / "build_onion_os.sh"
BASE = ROOT / "modules" / "01_base.sh"


class GrubThemeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = THEME.read_text(encoding="utf-8") if THEME.exists() else ""
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def test_theme_is_text_first_and_high_contrast(self):
        self.assertTrue(THEME.is_file())
        for marker in [
            'title-text: "Ming OS"',
            'desktop-color: "#07110f"',
            'item_color = "#eaf7f2"',
            'selected_item_color = "#43d19e"',
        ]:
            self.assertIn(marker, self.theme)
        self.assertNotIn(".png", self.theme.lower())
        self.assertNotIn("desktop-image:", self.theme)

    def test_live_and_installed_grub_install_the_same_theme(self):
        self.assertIn('assets/grub-theme/theme.txt', self.build)
        self.assertIn('${ISO_DIR}/boot/grub/themes/ming/theme.txt', self.build)
        self.assertIn('/boot/grub/themes/ming/theme.txt', self.base)
        self.assertIn(r'set theme=(\$root)/boot/grub/themes/ming/theme.txt', self.build)
        self.assertIn('cmp -s "${SCRIPT_DIR}/assets/grub-theme/theme.txt"', self.build)

    def test_fallback_is_black_not_debian_blue(self):
        for marker in [
            'set color_normal=white/black',
            'set menu_color_normal=white/black',
            'set menu_color_highlight=black/light-gray',
        ]:
            self.assertIn(marker, self.build)
        self.assertNotIn('desktop-color: "#0000aa"', self.theme.lower())

    def test_final_iso_validation_requires_theme_and_font(self):
        self.assertIn("/boot/grub/themes/ming/theme.txt", self.build)
        self.assertIn("/boot/grub/fonts/unicode.pf2", self.build)
        self.assertIn("required GRUB unicode font is missing", self.build)


if __name__ == "__main__":
    unittest.main()
