import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "assets" / "ming-radio-firmware.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("ming_radio_firmware", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RadioFirmwareBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def make_bundle(self, directory, *, targets=None, digest=None,
                    permitted=True, license_text="redistribution permitted\n"):
        root = pathlib.Path(directory)
        assets = root / "payloads"
        receipts = root / "receipts"
        assets.mkdir()
        receipts.mkdir()
        payload = assets / "dell-413c-8197.hcd"
        payload.write_bytes(b"reviewed bluetooth firmware")
        (receipts / "dell-license.txt").write_text(license_text, encoding="utf-8")
        manifest = {
            "schema": 1,
            "entries": [{
                "id": "dell-bcm20702a1-413c-8197",
                "device_ids": ["usb:413c:8197"],
                "asset": "payloads/dell-413c-8197.hcd",
                "sha256": digest or hashlib.sha256(payload.read_bytes()).hexdigest(),
                "targets": targets or [
                    "brcm/BCM-413c-8197.hcd",
                    "brcm/BCM20702A1-413c-8197.hcd",
                ],
                "source": "audited-upstream",
                "source_url": "https://example.invalid/release/dell-413c-8197.hcd",
                "receipt": {
                    "license": "receipts/dell-license.txt",
                    "redistribution_permitted": permitted,
                },
                "include_in_initramfs": True,
            }],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_verified_payload_is_atomically_deployed_to_both_kernel_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory)
            firmware = pathlib.Path(directory, "firmware")

            result = self.helper.validate_and_deploy(manifest, firmware)

            first = firmware / "brcm/BCM-413c-8197.hcd"
            second = firmware / "brcm/BCM20702A1-413c-8197.hcd"
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(2, result["deployed_files"])
            self.assertEqual(["dell-bcm20702a1-413c-8197"], result["entries"])
            self.assertFalse(any(path.name.endswith(".tmp") for path in firmware.rglob("*")))

    def test_sha256_mismatch_refuses_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory, digest="0" * 64)
            firmware = pathlib.Path(directory, "firmware")

            with self.assertRaisesRegex(self.helper.FirmwareValidationError,
                                        "E_FIRMWARE_HASH"):
                self.helper.validate_and_deploy(manifest, firmware)

            self.assertFalse(firmware.exists())

    def test_missing_or_nonredistributable_license_refuses_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory, permitted=False)
            firmware = pathlib.Path(directory, "firmware")

            with self.assertRaisesRegex(self.helper.FirmwareValidationError,
                                        "E_FIRMWARE_LICENSE"):
                self.helper.validate_and_deploy(manifest, firmware)

            self.assertFalse(firmware.exists())

    def test_symlinked_license_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory)
            license_path = pathlib.Path(directory, "receipts/dell-license.txt")
            outside = pathlib.Path(directory, "outside-license.txt")
            outside.write_text("redistribution permitted\n", encoding="utf-8")
            license_path.unlink()
            license_path.symlink_to(outside)

            with self.assertRaisesRegex(self.helper.FirmwareValidationError,
                                        "E_FIRMWARE_LICENSE"):
                self.helper.validate_and_deploy(
                    manifest, pathlib.Path(directory, "firmware"))

    def test_unsafe_target_path_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory, targets=["../escape.hcd"])
            firmware = pathlib.Path(directory, "firmware")

            with self.assertRaisesRegex(self.helper.FirmwareValidationError,
                                        "E_FIRMWARE_PATH"):
                self.helper.validate_and_deploy(manifest, firmware)

            self.assertFalse(pathlib.Path(directory, "escape.hcd").exists())

    def test_manifest_can_emit_an_initramfs_file_list(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory)

            files = self.helper.initramfs_files(manifest)

            self.assertEqual([
                "/usr/lib/firmware/brcm/BCM-413c-8197.hcd",
                "/usr/lib/firmware/brcm/BCM20702A1-413c-8197.hcd",
            ], files)

    def test_deployed_rootfs_verification_detects_a_changed_target(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_bundle(directory)
            firmware = pathlib.Path(directory, "firmware")
            self.helper.validate_and_deploy(manifest, firmware)
            (firmware / "brcm/BCM-413c-8197.hcd").write_bytes(b"tampered")

            with self.assertRaisesRegex(self.helper.FirmwareValidationError,
                                        "E_FIRMWARE_DEPLOYED_HASH"):
                self.helper.verify_deployed(manifest, firmware)

    def test_base_build_fails_closed_and_verifies_final_initramfs(self):
        base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("install_audited_radio_firmware || return 1", base)
        self.assertIn("verify_audited_radio_initramfs || return 1", base)
        self.assertIn("ming-radio-firmware initramfs-files", base)
        self.assertIn("lsinitramfs \"${initrd}\" > \"${listing}\"", base)
        for target in (
            "b43/ucode30_mimo.fw",
            "brcm/BCM-413c-8197.hcd",
            "brcm/BCM20702A1-413c-8197.hcd",
        ):
            self.assertIn(target, base)
            self.assertIn(target, build)


if __name__ == "__main__":
    unittest.main()
