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

    def test_live_bios_grub_has_one_visible_entry_and_one_second_timeout(self):
        generated = self.build.split(
            'cat > "${ISO_DIR}/boot/grub/grub.cfg" << GRUBCFG', 1
        )[1].split("\nGRUBCFG", 1)[0]
        self.assertIn("set timeout=1", generated)
        self.assertIn('menuentry "启动/安装 Ming OS ${MING_OS_VERSION}"', generated)
        self.assertEqual(1, generated.count("menuentry \"启动/安装 Ming OS"))
        self.assertIn("if keystatus --shift; then", generated)
        visible = generated.split("if keystatus --shift; then", 1)[0]
        self.assertNotIn("安全显卡", visible)
        self.assertNotIn("高级兼容启动", visible)

    def test_isolinux_fallback_has_one_default_entry_and_one_second_timeout(self):
        generated = self.build.split(
            'cat > "${iso_workdir}/isolinux/isolinux.cfg" << \'ISOLINUXCFG\'', 1
        )[1].split("\nISOLINUXCFG", 1)[0]
        self.assertIn("DEFAULT ming", generated)
        self.assertIn("ONTIMEOUT ming", generated)
        self.assertIn("TIMEOUT 10", generated)
        self.assertEqual(1, generated.count("\nLABEL "))
        self.assertIn("\nLABEL ming", generated)
        self.assertIn("MENU LABEL Boot / Install Ming OS", generated)
        self.assertNotIn("LABEL safe", generated)
        self.assertNotIn("LABEL oldpc", generated)

    def test_installed_grub_records_boot_mode_mismatch_diagnostics(self):
        for marker in [
            "ming-installer-boot-mode.json",
            '"firmware_mode"',
            '"uefi_fallback"',
            '"bios_grub_verified"',
            "启动模式不一致",
        ]:
            self.assertIn(marker, self.base)

    def test_bios_grub_install_failure_is_a_hard_gate(self):
        self.assertIn("install_bios_grub || {", self.base)
        self.assertIn("BIOS bootloader installation failed", self.base)
        self.assertIn("exit 23", self.base)


if __name__ == "__main__":
    unittest.main()
