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

    def test_ia32_uefi_generation_reports_failure_instead_of_swallowing_it(self):
        ia32 = BUILD.split("# 32位UEFI", 1)[1].split(
            "if [[ ! -f \"${iso_workdir}/EFI/BOOT/BOOTX64.EFI\" ]]", 1
        )[0]
        self.assertNotIn("2>/dev/null || true", ia32)
        self.assertIn("BOOTIA32.EFI", ia32)
        self.assertIn("log_error", ia32)
        self.assertIn("return 1", ia32)


if __name__ == "__main__":
    unittest.main()
