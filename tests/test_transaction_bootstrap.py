import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POSTINST = ROOT / "assets" / "bootstrap" / "ming-ota-bootstrap.postinst"
PRERM = ROOT / "assets" / "bootstrap" / "ming-ota-bootstrap.prerm"
BUILDER = ROOT / "tools" / "build-ming-ota-bootstrap.sh"
CAPABILITY = ROOT / "assets" / "ming-ota-bootstrap-capability.py"


def load_capability():
    spec = importlib.util.spec_from_file_location("ming_ota_capability_test", CAPABILITY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_image_deploys_root_capability_refresh_oneshot(self):
        module = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
        unit = ROOT / "assets" / "systemd" / "ming-ota-capability-refresh.service"
        self.assertTrue(unit.is_file())
        source = unit.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", source)
        self.assertIn("ming-ota-bootstrap-capability.py --write-marker", source)
        self.assertIn("Before=ming-update-check.service", source)
        self.assertIn("ming-ota-capability-refresh.service", module)
        self.assertIn("multi-user.target.wants/ming-ota-capability-refresh.service", module)

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX file-mode semantics")
    def test_capability_marker_is_readable_by_the_unprivileged_json_client(self):
        """The marker is integrity metadata, not a secret; `ming-update status` runs as the desktop user."""
        capability = load_capability()
        original_detect = capability.detect_capability
        capability.detect_capability = lambda *_args, **_kwargs: {"available": True}
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                capability.write_capability_marker(root)
                marker = root / "var" / "lib" / "ming-update" / "capability.json"
                self.assertTrue(marker.is_file(), "capability.json must be written")
                mode = stat.S_IMODE(os.stat(marker).st_mode)
                self.assertEqual(mode, 0o644)
                # The desktop user must be able to read the marker.
                with open(marker, encoding="utf-8") as handle:
                    data = json.load(handle)
                self.assertEqual(data["schema"], "ming.bootstrap-capability.v1")
        finally:
            capability.detect_capability = original_detect


if __name__ == "__main__":
    unittest.main()
