import datetime
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-transaction-verify.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_transaction_verify", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionVerifyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE_PATH.exists(), "transaction verifier is not implemented")
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-verify-")
        self.root = pathlib.Path(self.tmp.name)
        self.now = datetime.datetime(2026, 7, 15, tzinfo=datetime.timezone.utc)
        self.payload = self.root / "payload.tar.zst"
        self.payload.write_bytes(b"signed payload bytes")
        self.index = self.root / "content-index.json"
        self.index_data = {
            "schema": "ming.content-index.v1",
            "release_id": "ming-os-26.3.3-amd64-1",
            "entries": [
                {
                    "path": "usr/share/ming-os/version",
                    "type": "file",
                    "blob": "sha256:" + ("1" * 64),
                    "mode": 0o644,
                    "uid": 0,
                    "gid": 0,
                    "config_policy": "replace",
                }
            ],
            "deletions": ["usr/share/ming-os/obsolete"],
            "packages": [],
        }
        self._write_json(self.index, self.index_data)
        self.manifest = self.root / "manifest.json"
        self.manifest_data = {
            "schema": "ming.transaction-manifest.v1",
            "release_id": self.index_data["release_id"],
            "version": "26.3.3",
            "channel": "stable",
            "architecture": "amd64",
            "delivery": "transactional-slot-v1",
            "from_versions": ["26.3.2"],
            "minimum_bootstrap": "1.0.0",
            "created_at": "2026-07-14T00:00:00Z",
            "expires_at": "2026-08-15T00:00:00Z",
            "kernel_release": "6.12.0-amd64",
            "payload": {
                "url": "https://updates.example/objects/payload",
                "sha256": self._sha256(self.payload),
                "size": self.payload.stat().st_size,
                "signature_url": "https://updates.example/objects/payload.sig",
            },
            "content_index": {
                "url": "https://updates.example/objects/index",
                "sha256": self._sha256(self.index),
                "size": self.index.stat().st_size,
                "signature_url": "https://updates.example/objects/index.sig",
            },
            "space": {"minimum_free_bytes": 4096, "reserve_bytes": 1024},
            "slot_policy": {
                "maximum_uncommitted_boots": 1,
                "retain_previous_committed_slots": 1,
            },
            "preserve_paths": ["/home"],
            "health_profile": "ming-core-v1",
        }
        self._write_json(self.manifest, self.manifest_data)
        self.signatures = {}
        for name, artifact in (
            ("manifest", self.manifest),
            ("index", self.index),
            ("payload", self.payload),
        ):
            signature = self.root / f"{name}.sig"
            signature.write_text("good", encoding="ascii")
            self.signatures[artifact] = signature
        self.keyring = self.root / "release-keyring.gpg"
        self.keyring.write_bytes(b"public keyring fixture")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write_json(path, value):
        path.write_text(
            json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )

    @staticmethod
    def _sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _rewrite_manifest(self):
        self._write_json(self.manifest, self.manifest_data)

    def _rewrite_index(self):
        self._write_json(self.index, self.index_data)
        self.manifest_data["content_index"]["sha256"] = self._sha256(self.index)
        self.manifest_data["content_index"]["size"] = self.index.stat().st_size
        self._rewrite_manifest()

    def _signature_verifier(self, artifact, signature, keyring):
        self.assertEqual(pathlib.Path(keyring), self.keyring)
        if pathlib.Path(signature).read_text(encoding="ascii") != "good":
            raise self.module.TransactionError("E_ARTIFACT_SIGNATURE", "bad signature")

    def _verify(self):
        return self.module.verify_release(
            manifest_path=self.manifest,
            manifest_signature=self.signatures[self.manifest],
            index_path=self.index,
            index_signature=self.signatures[self.index],
            payload_path=self.payload,
            payload_signature=self.signatures[self.payload],
            keyring=self.keyring,
            current_version="26.3.2",
            architecture="amd64",
            kernel_release="6.12.0-amd64",
            bootstrap_version="1.0.0",
            now=self.now,
            signature_verifier=self._signature_verifier,
        )

    def assert_error(self, code, callback):
        with self.assertRaises(self.module.TransactionError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_accepts_a_fully_verified_release_without_writing(self):
        before = {path.name for path in self.root.iterdir()}
        plan = self._verify()
        after = {path.name for path in self.root.iterdir()}

        self.assertEqual(plan["release_id"], self.index_data["release_id"])
        self.assertEqual(plan["delivery"], "transactional-slot-v1")
        self.assertEqual(plan["content_index"]["entries"][0]["path"], "usr/share/ming-os/version")
        self.assertEqual(before, after)

    def test_verifies_manifest_index_and_payload_signatures_independently(self):
        expected = {
            self.manifest: "E_MANIFEST_SIGNATURE",
            self.index: "E_ARTIFACT_SIGNATURE",
            self.payload: "E_ARTIFACT_SIGNATURE",
        }
        for artifact, error_code in expected.items():
            with self.subTest(artifact=artifact.name):
                signature = self.signatures[artifact]
                signature.write_text("bad", encoding="ascii")
                self.assert_error(error_code, self._verify)
                signature.write_text("good", encoding="ascii")

    def test_rejects_version_architecture_kernel_and_bootstrap_mismatch(self):
        cases = (
            ("current_version", "26.3.1", "E_SOURCE_UNSUPPORTED"),
            ("architecture", "arm64", "E_MANIFEST_SCHEMA"),
            ("kernel_release", "6.11.0-amd64", "E_MANIFEST_SCHEMA"),
            ("bootstrap_version", "0.9.0", "E_BOOTSTRAP_VERSION"),
        )
        for argument, value, code in cases:
            with self.subTest(argument=argument):
                kwargs = {
                    "manifest_path": self.manifest,
                    "manifest_signature": self.signatures[self.manifest],
                    "index_path": self.index,
                    "index_signature": self.signatures[self.index],
                    "payload_path": self.payload,
                    "payload_signature": self.signatures[self.payload],
                    "keyring": self.keyring,
                    "current_version": "26.3.2",
                    "architecture": "amd64",
                    "kernel_release": "6.12.0-amd64",
                    "bootstrap_version": "1.0.0",
                    "now": self.now,
                    "signature_verifier": self._signature_verifier,
                }
                kwargs[argument] = value
                self.assert_error(code, lambda: self.module.verify_release(**kwargs))

    def test_rejects_payload_and_index_hash_or_size_mismatch(self):
        for section in ("payload", "content_index"):
            with self.subTest(section=section):
                original = self.manifest_data[section]["sha256"]
                self.manifest_data[section]["sha256"] = "0" * 64
                self._rewrite_manifest()
                self.assert_error("E_ARTIFACT_HASH", self._verify)
                self.manifest_data[section]["sha256"] = original
                self._rewrite_manifest()

    def test_rejects_home_boot_kernel_and_transaction_paths(self):
        forbidden = (
            "home/user/file",
            "boot/vmlinuz",
            "var/lib/ming-update/state.json",
            "usr/lib/modules/6.12/driver.ko",
            "etc/machine-id",
            "etc/NetworkManager/system-connections/secret.nmconnection",
        )
        for path in forbidden:
            with self.subTest(path=path):
                self.index_data["entries"][0]["path"] = path
                self._rewrite_index()
                self.assert_error("E_CONTENT_POLICY", self._verify)
        self.index_data["entries"][0]["path"] = "usr/share/ming-os/version"
        self._rewrite_index()

    def test_rejects_traversal_absolute_duplicate_and_unsafe_symlink_paths(self):
        for path in ("../etc/shadow", "/etc/shadow", "usr//share/file", "usr/./share/file"):
            with self.subTest(path=path):
                self.index_data["entries"][0]["path"] = path
                self._rewrite_index()
                self.assert_error("E_CONTENT_POLICY", self._verify)

        self.index_data["entries"] = [
            {
                "path": "usr/share/link",
                "type": "symlink",
                "target": "../../../etc/shadow",
                "mode": 0o777,
                "uid": 0,
                "gid": 0,
                "config_policy": "replace",
            }
        ]
        self._rewrite_index()
        self.assert_error("E_CONTENT_POLICY", self._verify)

    def test_rejects_kernel_bootloader_and_dkms_packages(self):
        for package in ("linux-image-amd64", "grub-pc", "example-dkms"):
            with self.subTest(package=package):
                self.index_data["packages"] = [
                    {
                        "name": package,
                        "version": "1.0",
                        "architecture": "amd64",
                        "blob": "sha256:" + ("2" * 64),
                    }
                ]
                self._rewrite_index()
                self.assert_error("E_CONTENT_POLICY", self._verify)

    def test_gpgv_uses_pinned_keyring_timeout_and_no_shell(self):
        calls = []

        class Result:
            returncode = 0
            stderr = ""

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        self.module.verify_detached_signature(
            self.payload,
            self.signatures[self.payload],
            self.keyring,
            runner=runner,
        )
        command, kwargs = calls[0]
        self.assertEqual(command[:3], ["gpgv", "--keyring", str(self.keyring)])
        self.assertEqual(command[-2:], [str(self.signatures[self.payload]), str(self.payload)])
        self.assertEqual(kwargs["timeout"], 15)
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
