import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "assets" / "ming-ota-bundle.py"


def load_bundle():
    spec = importlib.util.spec_from_file_location("ming_ota_bundle", BUNDLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineUpdateBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle()

    def write_bundle(self, directory, manifest=None, payload=b"payload", member_name="ming-os.iso"):
        manifest = manifest or {
            "schema": "ming.update.bundle.v1",
            "version": "26.4.2",
            "build_id": "2642-rc1-0123456789ab-20260926T120000Z",
            "update_type": "major",
            "base_version": "26.4.1",
            "payload": member_name,
            "payload_size": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "signature": "RWQtestsignature",
            "trusted_comment": "Ming OS OTA 26.4.2",
        }
        path = pathlib.Path(directory) / "update.ming-ota"
        with tarfile.open(path, "w") as archive:
            data = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return path

    def test_scan_accepts_signed_bundle_and_reports_payload_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory)
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertTrue(result["ok"], result)
        self.assertEqual("26.4.2", result["version"])
        self.assertEqual("ming-os.iso", result["payload"])

    def test_scan_rejects_path_traversal_member(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory, member_name="../payload.bin")
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertFalse(result["ok"])
        self.assertEqual("unsafe_archive", result["reason"])

    def test_scan_rejects_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory, manifest={
                "schema": "ming.update.bundle.v1", "version": "26.4.0",
                "build_id": "2640-rc1-0123456789ab-20260926T120000Z",
                "update_type": "major", "base_version": "26.4.1",
                "payload": "ming-os.iso",
                "payload_size": 7, "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
                "signature": "RWQtestsignature", "trusted_comment": "Ming OS OTA 26.4.0",
            })
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertFalse(result["ok"])
        self.assertEqual("version_not_forward", result["reason"])

    def test_scan_rejects_bundle_for_a_different_base_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory, manifest={
                "schema": "ming.update.bundle.v1", "version": "26.4.2",
                "build_id": "2642-rc1-0123456789ab-20260926T120000Z",
                "update_type": "major", "base_version": "26.4.0",
                "payload": "ming-os.iso", "payload_size": 7,
                "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
                "signature": "RWQtestsignature", "trusted_comment": "Ming OS OTA 26.4.2",
            })
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertFalse(result["ok"])
        self.assertEqual("incompatible_base_version", result["reason"])

    def test_scan_rejects_non_iso_patch_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory, member_name="payload.deb")
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertFalse(result["ok"])
        self.assertEqual("unsupported_update_type", result["reason"])

    def test_scan_rejects_build_id_that_names_a_different_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_bundle(directory, manifest={
                "schema": "ming.update.bundle.v1", "version": "26.4.2",
                "build_id": "2641-rc1-0123456789ab-20260926T120000Z",
                "update_type": "major", "base_version": "26.4.1",
                "payload": "ming-os.iso", "payload_size": 7,
                "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
                "signature": "RWQtestsignature", "trusted_comment": "Ming OS OTA 26.4.2",
            })
            result = self.bundle.scan_bundle(path, current_version="26.4.1")
        self.assertFalse(result["ok"])
        self.assertEqual("build_version_mismatch", result["reason"])


if __name__ == "__main__":
    unittest.main()
