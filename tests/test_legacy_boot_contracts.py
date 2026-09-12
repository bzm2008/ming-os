import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class LegacyBootContractTests(unittest.TestCase):
    def test_host_preflight_installs_and_verifies_ia32_grub_toolchain(self):
        self.assertIn("grub-efi-ia32-bin", BUILD)
        self.assertIn("verify_i386_efi_toolchain", BUILD)
        preflight = BUILD.split("check_host_environment() {", 1)[1].split(
            "install_build_deps() {", 1
        )[0]
        self.assertIn("verify_i386_efi_toolchain", preflight)
        toolchain = BUILD.split("verify_i386_efi_toolchain() {", 1)[1].split(
            "# ========================", 1
        )[0]
        self.assertIn("/usr/lib/grub/i386-efi", toolchain)
        self.assertIn("dpkg-query", toolchain)
        self.assertIn("install ok installed", toolchain)

    def test_dependency_shortcut_checks_ia32_toolchain_before_skipping_install(self):
        install = BUILD.split("install_build_deps() {", 1)[1].split(
            "\n}\n\nverify_debootstrap_keyring()", 1
        )[0]
        shortcut = install.split(
            "if [[ ${#missing_bins[@]} -eq 0", 1
        )[1].split("; then", 1)[0]
        self.assertIn("grub-efi-ia32-bin", shortcut)
        self.assertIn("dpkg-query", shortcut)
        self.assertIn("/usr/lib/grub/i386-efi", shortcut)

    def test_ia32_uefi_generation_reports_failure_instead_of_swallowing_it(self):
        ia32 = BUILD.split("# 32位UEFI", 1)[1].split(
            "if [[ ! -f \"${iso_workdir}/EFI/BOOT/BOOTX64.EFI\" ]]", 1
        )[0]
        self.assertNotIn("2>/dev/null || true", ia32)
        self.assertIn("BOOTIA32.EFI", ia32)
        self.assertIn("log_error", ia32)
        self.assertIn("return 1", ia32)

    def test_bios_fallback_exposes_visible_legacy_hardware_choices(self):
        isolinux = BUILD.split(
            'cat > "${iso_workdir}/isolinux/isolinux.cfg" << \'ISOLINUXCFG\'', 1
        )[1].split("\nISOLINUXCFG", 1)[0]
        self.assertEqual(6, isolinux.count("\nLABEL "))
        for marker in (
            "LABEL ming",
            "LABEL safe",
            "LABEL radeon",
            "LABEL radeon-gcn",
            "LABEL surface",
            "LABEL mac",
            "MENU LABEL 启动/安装 Ming OS",
            "MENU LABEL 安全显卡模式",
            "MENU LABEL Radeon 传统显卡模式",
            "MENU LABEL Radeon GCN 尝试模式",
            "MENU LABEL Surface Pro 兼容模式",
            "MENU LABEL Mac EFI / MacBook 兼容模式",
        ):
            self.assertIn(marker, isolinux)
        self.assertIn("nomodeset", isolinux)
        self.assertIn("acpi_osi=Darwin", isolinux)

    def test_iso_layout_validation_requires_ia32_uefi_artifact(self):
        validation = BUILD.split("validate_iso_boot_layout() {", 1)[1].split(
            "\n}\n\nvalidate_calamares_config()", 1
        )[0]
        self.assertIn("        /EFI/BOOT/BOOTIA32.EFI", validation)


if __name__ == "__main__":
    unittest.main()
