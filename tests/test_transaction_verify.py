import datetime
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-transaction-verify.py"
ALLOWLIST_PATH = ROOT / "assets" / "ming-transaction-allowlist.txt"


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

    def test_runtime_manifest_validation_matches_frozen_schema_and_refuses_downgrade(self):
        cases = (
            ("invalid target version", {"version": "not-a-version"}),
            ("equal target version", {"version": "26.3.2"}),
            ("older target version", {"version": "26.3.1"}),
            ("unsupported channel", {"channel": "preview"}),
            ("empty source versions", {"from_versions": []}),
            ("duplicate source versions", {"from_versions": ["26.3.2", "26.3.2"]}),
            ("invalid source version", {"from_versions": ["not-a-version", "26.3.2"]}),
            ("zero minimum free space", {"space": {"minimum_free_bytes": 0, "reserve_bytes": 0}}),
            ("boolean minimum free space", {"space": {"minimum_free_bytes": True, "reserve_bytes": 0}}),
            ("boolean slot policy", {"slot_policy": {"maximum_uncommitted_boots": True, "retain_previous_committed_slots": 1}}),
            ("unexpected manifest key", {"unexpected": "accepted"}),
        )
        for name, change in cases:
            with self.subTest(name=name):
                original = json.loads(json.dumps(self.manifest_data))
                try:
                    self.manifest_data.update(change)
                    self._rewrite_manifest()
                    self.assert_error("E_MANIFEST_SCHEMA", self._verify)
                finally:
                    self.manifest_data = original
                    self._rewrite_manifest()

        self.manifest_data["payload"]["unexpected"] = True
        self._rewrite_manifest()
        self.assert_error("E_MANIFEST_SCHEMA", self._verify)
        self.manifest_data["payload"].pop("unexpected")
        self._rewrite_manifest()

        self.manifest_data["payload"]["size"] = True
        self._rewrite_manifest()
        self.assert_error("E_MANIFEST_SCHEMA", self._verify)
        self.manifest_data["payload"]["size"] = self.payload.stat().st_size
        self._rewrite_manifest()

    def test_runtime_content_index_and_artifact_urls_match_the_frozen_schema(self):
        self.manifest_data["payload"]["url"] = "https://updates.example/objects/payload?cache=unsafe"
        self._rewrite_manifest()
        self.assert_error("E_MANIFEST_SCHEMA", self._verify)
        self.manifest_data["payload"]["url"] = "https://updates.example/objects/payload"
        self._rewrite_manifest()

        cases = (
            ("unexpected top-level field", lambda index: index.__setitem__("unexpected", True)),
            ("unexpected entry field", lambda index: index["entries"][0].__setitem__("unexpected", True)),
            ("file target", lambda index: index["entries"][0].__setitem__("target", "version")),
            ("boolean mode", lambda index: index["entries"][0].__setitem__("mode", True)),
            ("invalid base hash", lambda index: index["entries"][0].__setitem__("base_sha256", "not-a-hash")),
            ("invalid path characters", lambda index: index["entries"][0].__setitem__("path", "usr/share/ming-os/bad space")),
            (
                "directory has blob",
                lambda index: index.__setitem__(
                    "entries",
                    [{
                        "path": "usr/share/ming-os/directory",
                        "type": "directory",
                        "blob": "sha256:" + ("3" * 64),
                        "mode": 0o755,
                        "uid": 0,
                        "gid": 0,
                        "config_policy": "replace",
                    }],
                ),
            ),
            (
                "symlink has blob",
                lambda index: index.__setitem__(
                    "entries",
                    [{
                        "path": "usr/share/ming-os/link",
                        "type": "symlink",
                        "target": "version",
                        "blob": "sha256:" + ("4" * 64),
                        "mode": 0o777,
                        "uid": 0,
                        "gid": 0,
                        "config_policy": "replace",
                    }],
                ),
            ),
            (
                "package extra field",
                lambda index: index.__setitem__(
                    "packages",
                    [{
                        "name": "ming-example",
                        "version": "1.0",
                        "architecture": "amd64",
                        "blob": "sha256:" + ("5" * 64),
                        "unexpected": True,
                    }],
                ),
            ),
            (
                "package version too long",
                lambda index: index.__setitem__(
                    "packages",
                    [{
                        "name": "ming-example",
                        "version": "x" * 257,
                        "architecture": "amd64",
                        "blob": "sha256:" + ("5" * 64),
                    }],
                ),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                original = json.loads(json.dumps(self.index_data))
                try:
                    mutate(self.index_data)
                    self._rewrite_index()
                    self.assert_error("E_CONTENT_POLICY", self._verify)
                finally:
                    self.index_data = original
                    self._rewrite_index()

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

    def test_rejects_transactional_runtime_wrappers_even_when_their_parent_is_allowlisted(self):
        for path in ("usr/local/bin/ming-update", "usr/local/sbin/ming-transaction-health"):
            with self.subTest(path=path):
                self.index_data["entries"][0]["path"] = path
                self._rewrite_index()
                self.assert_error("E_CONTENT_POLICY", self._verify)
        self.index_data["entries"][0]["path"] = "usr/share/ming-os/version"
        self._rewrite_index()

    def test_positive_allowlist_rejects_signed_but_unapproved_system_paths(self):
        self.assertTrue(ALLOWLIST_PATH.is_file(), "transaction allowlist is missing")
        allowed = [
            line.strip()
            for line in ALLOWLIST_PATH.read_text(encoding="ascii").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertIn("usr/share/ming-os", allowed)
        self.assertNotIn("home", allowed)
        self.assertNotIn("boot", allowed)

        self.index_data["entries"][0]["path"] = "var/log/signed-but-unapproved"
        self._rewrite_index()
        self.assert_error("E_CONTENT_POLICY", self._verify)

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

    def test_rejects_setuid_and_setgid_content_modes(self):
        for mode in (0o4755, 0o2755, 0o6755):
            with self.subTest(mode=oct(mode)):
                self.index_data["entries"][0]["mode"] = mode
                self._rewrite_index()
                self.assert_error("E_CONTENT_POLICY", self._verify)
        self.index_data["entries"][0]["mode"] = 0o644
        self._rewrite_index()

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

    def test_rejects_all_offline_package_entries_until_package_mode_is_reviewed(self):
        self.index_data["packages"] = [
            {
                "name": "ming-example-runtime",
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
        self.assertEqual(command[:4], ["gpgv", "--status-fd=1", "--keyring", str(self.keyring)])
        self.assertEqual(command[-2:], [str(self.signatures[self.payload]), str(self.payload)])
        self.assertEqual(kwargs["timeout"], 15)
        self.assertNotIn("shell", kwargs)

    def test_signature_policy_pins_the_gpgv_primary_and_signing_fingerprints(self):
        policy = self.root / "key-policy.json"
        primary = "A" * 40
        signing = "B" * 40
        policy.write_text(
            json.dumps(
                {
                    "schema": "ming.ota-key-policy.v1",
                    "allowed_primary_fingerprints": [primary],
                    "allowed_signing_fingerprints": [signing],
                    "channels": ["stable"],
                    "minimum_bootstrap": "1.0.0",
                }
            ),
            encoding="utf-8",
        )

        class Result:
            returncode = 0
            stderr = ""
            stdout = "[GNUPG:] VALIDSIG %s 2026-07-15 0 0 0 0 0 0 0 %s\n" % (signing, primary)

        self.module.verify_detached_signature(
            self.payload,
            self.signatures[self.payload],
            self.keyring,
            key_policy=policy,
            runner=lambda *_args, **_kwargs: Result(),
        )

        policy.write_text(
            json.dumps(
                {
                    "schema": "ming.ota-key-policy.v1",
                    "allowed_primary_fingerprints": [primary],
                    "allowed_signing_fingerprints": ["C" * 40],
                    "channels": ["stable"],
                    "minimum_bootstrap": "1.0.0",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(self.module.TransactionError) as caught:
            self.module.verify_detached_signature(
                self.payload,
                self.signatures[self.payload],
                self.keyring,
                key_policy=policy,
                runner=lambda *_args, **_kwargs: Result(),
            )
        self.assertEqual(caught.exception.code, "E_KEY_POLICY")


if __name__ == "__main__":
    unittest.main()
