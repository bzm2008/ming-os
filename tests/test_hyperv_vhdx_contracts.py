import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build-hyperv-gen2-vhdx.sh"
INSPECTOR = ROOT / "scripts" / "inspect-hyperv-gen2-vhdx.sh"
GUIDE = ROOT / "docs" / "hyperv-gen2-vhdx.md"


class HyperVGen2VhdxContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = BUILDER.read_text(encoding="utf-8") if BUILDER.exists() else ""
        cls.inspector = INSPECTOR.read_text(encoding="utf-8") if INSPECTOR.exists() else ""
        cls.guide = GUIDE.read_text(encoding="utf-8") if GUIDE.exists() else ""

    def test_dedicated_gen2_builder_and_inspector_exist(self):
        self.assertTrue(BUILDER.is_file())
        self.assertTrue(INSPECTOR.is_file())
        self.assertTrue(GUIDE.is_file())

    def test_builder_accepts_rootfs_or_iso_and_requires_an_output(self):
        for marker in [
            "--rootfs",
            "--iso",
            "--output",
            "exactly one of --rootfs or --iso is required",
            "must end in .vhdx",
        ]:
            self.assertIn(marker, self.builder)

    def test_builder_is_explicitly_hyperv_gen2_uefi_only(self):
        for marker in [
            "Hyper-V Generation 2",
            "GEN2_ONLY",
            "--target=x86_64-efi",
            "--removable",
            "--no-nvram",
            "EFI/BOOT/BOOTX64.EFI",
        ]:
            self.assertIn(marker, self.builder)
        self.assertNotIn("--target=i386-pc", self.builder)

    def test_builder_creates_gpt_esp_and_ext4_root(self):
        for marker in [
            "qemu-img",
            "sgdisk",
            "--zap-all",
            "ef00",
            "8300",
            "mkfs.vfat",
            "mkfs.ext4",
            "MING_EFI",
            "MING_OS_2640",
            "wait_for_partition",
            "partition device did not appear before the timeout",
        ]:
            self.assertIn(marker, self.builder)

    def test_builder_injects_and_verifies_hyperv_initramfs_modules(self):
        for module in ["hv_vmbus", "hv_storvsc", "hv_netvsc", "hid_hyperv"]:
            self.assertIn(module, self.builder)
        for marker in [
            "/etc/initramfs-tools/modules",
            "update-initramfs -u -k all",
            "lsinitramfs",
            "initramfs is missing required Hyper-V module",
        ]:
            self.assertIn(marker, self.builder)

    def test_builder_finalizes_the_calamares_grub_root_uuid_placeholder(self):
        main = self.builder.split("main() {", 1)[1].split('main "$@"', 1)[0]
        for marker in [
            "replace_calamares_root_uuid_placeholder",
            "__MING_ROOT_UUID__",
            "ROOT_UUID",
            "verify_generated_grub",
            "final GRUB configuration still contains __MING_ROOT_UUID__",
        ]:
            self.assertIn(marker, self.builder)
        self.assertLess(
            main.index("write_target_fstab"),
            main.index("replace_calamares_root_uuid_placeholder"),
        )
        self.assertLess(
            main.index("replace_calamares_root_uuid_placeholder"),
            main.index("install_uefi_bootloader"),
        )

    def test_builder_converts_live_persona_to_an_installed_desktop_before_grub(self):
        main = self.builder.split("main() {", 1)[1].split('main "$@"', 1)[0]
        for marker in [
            "prepare_installed_desktop_identity",
            "validate_installed_desktop_identity",
            "autologin-session=xfce",
            "user-session=xfce",
            "graphical.target",
            "display-manager.service",
            "lightdm.service",
            "ming-live-installer.service",
            "calamares-live.desktop",
            '"installed_desktop_identity"',
        ]:
            self.assertIn(marker, self.builder)
        self.assertLess(
            main.index("prepare_installed_desktop_identity"),
            main.index("install_uefi_bootloader"),
        )
        self.assertLess(
            main.index("validate_installed_desktop_identity"),
            main.index("install_uefi_bootloader"),
        )

    def test_builder_fails_before_writing_when_host_prerequisites_are_missing(self):
        preflight = self.builder.split("preflight() {", 1)[1].split(
            "validate_source_rootfs", 1
        )[0]
        for marker in [
            "require_root",
            "require_cmd",
            "qemu-img",
            "losetup",
            "partx",
            "mount",
            "grub-install",
            "missing required host command",
        ]:
            self.assertIn(marker, self.builder)
        self.assertIn("chroot", preflight)

    def test_builder_writes_checksum_and_machine_readable_manifest(self):
        for marker in [
            ".sha256",
            ".manifest.json",
            '"format": "vhdx"',
            '"firmware": "uefi-gen2"',
            '"partition_table": "gpt"',
            '"efi_fallback": "/EFI/BOOT/BOOTX64.EFI"',
            '"secure_boot": "disabled-required"',
        ]:
            self.assertIn(marker, self.builder)

    def test_inspector_checks_vhdx_gpt_esp_fallback_label_and_initramfs(self):
        for marker in [
            "qemu-img info --output=json",
            "format",
            "vhdx",
            "sgdisk --print",
            "ef00",
            "8300",
            "BOOTX64.EFI",
            "MING_OS_2640",
            "lsinitramfs",
            "sha256sum -c",
        ]:
            self.assertIn(marker, self.inspector)

    def test_inspector_waits_for_loop_partitions_with_a_fixed_deadline(self):
        for marker in [
            "wait_for_partition",
            "partition device did not appear before the timeout",
        ]:
            self.assertIn(marker, self.inspector)

    def test_inspector_rejects_live_persona_and_unresolved_grub_root_uuid(self):
        for marker in [
            "__MING_ROOT_UUID__",
            "autologin-session=xfce",
            "user-session=xfce",
            "ming-live-installer.service",
            "installed_desktop_identity",
        ]:
            self.assertIn(marker, self.inspector)

    def test_inspector_validates_systemd_links_without_following_host_paths(self):
        preflight = self.inspector.split("preflight() {", 1)[1].split(
            "verify_manifest_and_checksum", 1
        )[0]
        self.assertIn("readlink", preflight)
        self.assertIn('[[ -L "${ROOT_MOUNT}/etc/systemd/system/default.target" ]]', self.inspector)
        self.assertIn('[[ -L "${ROOT_MOUNT}/etc/systemd/system/display-manager.service" ]]', self.inspector)

    def test_inspector_preflights_its_temporary_workspace_command(self):
        preflight = self.inspector.split("preflight() {", 1)[1].split(
            "verify_manifest_and_checksum", 1
        )[0]
        self.assertIn("mktemp", preflight)

    def test_inspector_resolves_custom_checksum_before_changing_directory(self):
        self.assertIn('CHECKSUM="$(realpath -e "${CHECKSUM}")"', self.inspector)
        self.assertIn('sha256sum -c "${CHECKSUM}"', self.inspector)

    def test_guide_sets_clear_hyperv_and_secure_boot_boundaries(self):
        for marker in [
            "Hyper-V Generation 2",
            "UEFI",
            "Secure Boot",
            "disabled",
            "Generation 1",
            "not supported",
            "Linux host",
        ]:
            self.assertIn(marker, self.guide)


if __name__ == "__main__":
    unittest.main()
