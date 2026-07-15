import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POSTINST = ROOT / "assets" / "bootstrap" / "ming-ota-bootstrap.postinst"
PRERM = ROOT / "assets" / "bootstrap" / "ming-ota-bootstrap.prerm"
BUILDER = ROOT / "tools" / "build-ming-ota-bootstrap.sh"
CAPABILITY = ROOT / "assets" / "ming-ota-bootstrap-capability.py"


class TransactionBootstrapTests(unittest.TestCase):
    def setUp(self):
        for path in (POSTINST, PRERM, BUILDER, CAPABILITY):
            self.assertTrue(path.is_file(), f"transaction bootstrap asset is missing: {path.name}")

    def test_postinst_installs_boot_runtime_then_reads_back_before_advertising_capability(self):
        source = POSTINST.read_text(encoding="utf-8")
        marker = source.index("--write-marker")
        for required in (
            "update-initramfs -u -k all",
            "update-grub",
            "lsinitramfs",
            "grub-editenv",
            "/usr/local/bin/ming-update",
            "/etc/initramfs-tools/hooks/ming-transaction",
            "/etc/grub.d/40_ming_transaction",
        ):
            self.assertIn(required, source)
            self.assertLess(source.index(required), marker)
        for forbidden in ("calamares", "parted", "fdisk", "mkfs", "resize2fs", "sfdisk"):
            self.assertNotIn(forbidden, source.lower())

    def test_postinst_enables_health_and_rollback_before_marking_bootstrap_available(self):
        source = POSTINST.read_text(encoding="utf-8")
        marker = source.index("--write-marker")
        for required in (
            "GRUB_DEFAULT=saved",
            "grub-editenv /boot/grub/grubenv create",
            "saved_entry=ming-legacy",
            "systemctl enable ming-transaction-health.service ming-transaction-reconcile.service",
            "systemctl is-enabled ming-transaction-health.service",
            "systemctl is-enabled ming-transaction-reconcile.service",
        ):
            self.assertIn(required, source)
            self.assertLess(source.index(required), marker)

    def test_postinst_reads_back_the_display_manager_rollback_guard_before_capability(self):
        source = POSTINST.read_text(encoding="utf-8")
        marker = source.index("--write-marker")
        for required in (
            "/etc/systemd/system/ming-transaction-rollback-reboot.service",
            "/etc/systemd/system/display-manager.service.d/20-ming-transaction-health.conf",
            "ming-transaction-rollback-reboot.service",
        ):
            self.assertIn(required, source)
            self.assertLess(source.index(required), marker)

    def test_postinst_reads_back_every_generated_transaction_grub_entry_before_capability(self):
        source = POSTINST.read_text(encoding="utf-8")
        marker = source.index("--write-marker")
        generated = source.index("update-grub")
        for entry in ("ming-legacy", "ming-slot-a", "ming-slot-b", "ming-recovery-manual"):
            check = 'grep -Fq -- "--id \'%s\'" /boot/grub/grub.cfg' % entry
            self.assertIn(check, source)
            self.assertGreater(source.index(check), generated)
            self.assertLess(source.index(check), marker)

    def test_capability_requires_a_postinstall_receipt_and_enabled_boot_path(self):
        source = CAPABILITY.read_text(encoding="utf-8")
        for required in (
            "capability.json",
            "ming-transaction-local-premount",
            "ming-transaction-apply.py",
            "ming-transaction-rollback.py",
            "ming-transaction-allowlist.txt",
            "multi-user.target.wants",
            "GRUB_DEFAULT=saved",
            "saved_entry",
        ):
            self.assertIn(required, source)

    def test_prerm_refuses_removal_after_an_armed_transaction(self):
        source = PRERM.read_text(encoding="utf-8")
        self.assertIn("active-transaction.json", source)
        self.assertIn("armed", source)
        self.assertIn("rollback_armed", source)
        self.assertIn("exit 1", source)

    def test_builder_requires_explicit_public_key_policy_and_offline_signature(self):
        source = BUILDER.read_text(encoding="utf-8")
        for required in ("--keyring", "--policy", "--signing-key", "dpkg-deb --build", "--detach-sign", "gpgv"):
            self.assertIn(required, source)
        self.assertNotIn("--batch --yes --gen-key", source)
        self.assertNotIn("private", source.lower().replace("private key material is not accepted", ""))

    def test_capability_marker_cli_is_explicit_and_not_written_by_detection(self):
        source = CAPABILITY.read_text(encoding="utf-8")
        self.assertIn("--write-marker", source)
        self.assertIn("write_capability_marker", source)
        self.assertIn("detect_capability", source)


if __name__ == "__main__":
    unittest.main()
