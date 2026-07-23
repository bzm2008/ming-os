import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
THEME = ROOT / "assets" / "grub-theme" / "theme.txt"
TRANSACTION_GRUB = ROOT / "assets" / "grub" / "40_ming_transaction"
BASH = (
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and pathlib.Path(r"C:\Program Files\Git\bin\bash.exe").is_file()
    else "bash"
)
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

    def test_installer_bootloader_finalizes_target_root_uuid_before_generating_grub(self):
        """The installed disk must never retain the Live-image UUID placeholder."""
        bootloader = self.base.split(
            "cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'\n", 1
        )[1].split("\nMINGBOOTLOADER\n", 1)[0]
        self.assertIn('root_uuid="$(blkid -s UUID -o value "${root_source}"', bootloader)
        self.assertIn('grub_custom_entry="${root}/etc/grub.d/09_ming_os"', bootloader)
        self.assertIn('sed -i "s/__MING_ROOT_UUID__/${root_uuid}/g" "${grub_custom_entry}"', bootloader)
        self.assertIn(
            'grep -Fq \'__MING_ROOT_UUID__\' "${grub_custom_entry}"',
            bootloader,
        )
        self.assertLess(
            bootloader.index('root_uuid="$(blkid -s UUID -o value "${root_source}"'),
            bootloader.index('chroot "${root}" /usr/sbin/update-grub'),
        )

    def test_transaction_grub_does_not_enable_nounset_inside_grub_mkconfig(self):
        """A fresh target has no GRUB_DEVICE; the generator must still commit grub.cfg."""
        transaction = TRANSACTION_GRUB.read_text(encoding="utf-8")
        self.assertIn("set -e\n", transaction)
        self.assertNotIn("set -eu", transaction)

    def test_installer_rejects_an_uncommitted_generated_grub_file(self):
        """A correct grub.cfg.new must never leave an old placeholder config bootable."""
        bootloader = self.base.split(
            "cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'\n", 1
        )[1].split("\nMINGBOOTLOADER\n", 1)[0]
        self.assertIn('"${root}/boot/grub/grub.cfg.new"', bootloader)
        self.assertIn("uncommitted grub.cfg.new", bootloader)

    def test_installed_bootloader_removes_and_rejects_live_grub_entries(self):
        """An installed disk must never inherit the ISO's Live or installer menu paths."""
        bootloader = self.base.split(
            "cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'\n", 1
        )[1].split("\nMINGBOOTLOADER\n", 1)[0]
        self.assertIn("remove_live_grub_fragments", bootloader)
        self.assertIn("reject_live_grub_entries", bootloader)
        for marker in ("boot=live", "ming\\.installer=1", "Live Mode", "Install Ming OS"):
            self.assertIn(marker, bootloader)
        self.assertLess(
            bootloader.index("remove_live_grub_fragments"),
            bootloader.index('chroot "${root}" /usr/sbin/update-grub'),
        )
        self.assertGreater(
            bootloader.rindex("reject_live_grub_entries"),
            bootloader.index('chroot "${root}" /usr/sbin/update-grub'),
        )

    def test_live_grub_fragment_cleanup_preserves_only_installed_ming_generators(self):
        bootloader = "remove_live_grub_fragments() {" + self.base.split(
            "remove_live_grub_fragments() {", 1
        )[1].split("reject_live_grub_entries() {", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            grub_dir = root / "etc" / "grub.d"
            grub_dir.mkdir(parents=True)
            (grub_dir / "08_live").write_text("linux /live/vmlinuz boot=live\n", encoding="utf-8")
            (grub_dir / "30_installer").write_text("menuentry 'Install Ming OS' {}\n", encoding="utf-8")
            (grub_dir / "09_ming_os").write_text("menuentry 'Ming OS' {}\n", encoding="utf-8")
            (grub_dir / "40_ming_transaction").write_text(
                "menuentry 'Ming OS Slot A' {}\n", encoding="utf-8")
            (root / "boot" / "grub").mkdir(parents=True)
            (root / "boot" / "grub" / "loopback.cfg").write_text("live\n", encoding="utf-8")
            script = "\n".join((
                "root=%s" % root.as_posix(),
                bootloader,
                "remove_live_grub_fragments",
                "test ! -e \"${root}/etc/grub.d/08_live\"",
                "test ! -e \"${root}/etc/grub.d/30_installer\"",
                "test ! -e \"${root}/boot/grub/loopback.cfg\"",
                "test -e \"${root}/etc/grub.d/09_ming_os\"",
                "test -e \"${root}/etc/grub.d/40_ming_transaction\"",
            ))
            result = subprocess.run(
                [BASH], input=script.encode("utf-8"), capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
