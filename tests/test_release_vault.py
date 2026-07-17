import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ming-release-vault.py"
FIXTURES = ROOT / "tests" / "fixtures" / "release-vault"
GOOD_RECEIPT = FIXTURES / "good-receipt.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("ming_release_vault", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
    )


class ReleaseVaultReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.receipt = json.loads(GOOD_RECEIPT.read_text(encoding="utf-8"))

    def write_receipt(self, root, value):
        path = pathlib.Path(root) / "receipt.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_good_receipt_verifies_as_json(self):
        result = run_cli("verify-receipt", "--receipt", str(GOOD_RECEIPT))
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_receipt_rejects_unknown_fields_missing_hashes_and_non_hex_fingerprints(self):
        cases = []

        unknown = dict(self.receipt)
        unknown["unexpected"] = "not part of the public contract"
        cases.append(unknown)

        missing_hash = dict(self.receipt)
        del missing_hash["bundle_sha256"]
        cases.append(missing_hash)

        bad_fingerprint = dict(self.receipt)
        bad_fingerprint["primary_fingerprint"] = "not-a-fingerprint"
        cases.append(bad_fingerprint)

        for candidate in cases:
            with self.subTest(candidate=candidate):
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_receipt_requires_lowercase_sha256_and_uppercase_fingerprints(self):
        invalid = {
            "bundle_sha256": "A" * 64,
            "public_keyring_sha256": "b" * 63,
            "key_policy_sha256": "C" * 64,
            "primary_fingerprint": "a" * 40,
            "signing_fingerprint": "B" * 39,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                candidate = dict(self.receipt)
                candidate[field] = value
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_receipt_requires_generation_status_and_age_format(self):
        invalid = {
            "generation": 0,
            "status": "prepared",
            "encryption_format": "age-v2",
            "created_at": "not-a-timestamp",
            "bundle_bytes": -1,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                candidate = dict(self.receipt)
                candidate[field] = value
                with tempfile.TemporaryDirectory() as temp:
                    receipt = self.write_receipt(temp, candidate)
                    result = run_cli("verify-receipt", "--receipt", str(receipt))
                self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
                self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_direct_validator_returns_a_sanitized_copy(self):
        validated = self.tool.validate_receipt(self.receipt)
        self.assertEqual(validated, self.receipt)
        self.assertIsNot(validated, self.receipt)


class ReleaseVaultPublicScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def make_public_tree(self, root):
        root = pathlib.Path(root)
        (root / "keyring").mkdir(parents=True)
        (root / "policy").mkdir()
        (root / "signatures").mkdir()
        (root / "hashes").mkdir()
        (root / "keyring" / "ming-ota-release-keyring.asc").write_text(
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\npublic material\n-----END PGP PUBLIC KEY BLOCK-----\n",
            encoding="utf-8",
        )
        (root / "policy" / "ming-ota-key-policy.json").write_text(
            '{"allowed_primary_fingerprints": ["%s"]}\n' % ("A" * 40),
            encoding="utf-8",
        )
        (root / "signatures" / "manifest.json.sig").write_text(
            "detached public signature\n", encoding="utf-8"
        )
        (root / "hashes" / "manifest.sha256").write_text(
            "%s  manifest.json\n" % ("a" * 64), encoding="utf-8"
        )
        shutil.copy2(FIXTURES / "good-receipt.json", root / "release-receipt.json")
        return root

    def test_public_scan_accepts_public_keyring_policy_signature_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            public = self.make_public_tree(pathlib.Path(temp) / "public")
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "ok")
        self.assertGreaterEqual(output["files_scanned"], 5)

    def test_public_scan_rejects_secret_key_dotenv_private_path_and_age_bundle(self):
        malicious = [
            ("private-key.txt", "private-key.txt"),
            (".env", ".env"),
            ("recovery-bundle-1.age", "recovery-bundle-1.age"),
            ("secret-marker.txt", "secret-marker.txt"),
            ("password-marker.txt", "password-marker.txt"),
            ("token-marker.txt", "token-marker.txt"),
            ("known-hosts-marker.txt", "known-hosts-marker.txt"),
            ("private-path-log.txt", "private-path-log.txt"),
            ("id_rsa", "id_rsa"),
            ("id_ed25519", "id_ed25519"),
        ]
        for destination, source in malicious:
            with self.subTest(name=destination), tempfile.TemporaryDirectory() as temp:
                public = pathlib.Path(temp) / "public"
                public.mkdir()
                shutil.copy2(FIXTURES / source, public / destination)
                result = run_cli("scan-public", "--root", str(public))
            self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
            self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")

    def test_public_scan_rejects_sensitive_content_even_with_a_safe_basename(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            shutil.copy2(FIXTURES / "private-key-content.txt", public / "notes.txt")
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")

    def test_public_scan_rejects_sensitive_binary_content(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release-material.bin").write_bytes(
                b"\x00binary secret password token material"
            )
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")

    def test_public_scan_accepts_marker_free_binary_keyring_and_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release-keyring.gpg").write_bytes(b"\x00\x01public-key-packet")
            (public / "manifest.sig").write_bytes(b"\x00\x01detached-signature")
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_public_scan_rejects_sensitive_markers_in_binary_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "manifest.sig").write_bytes(
                b"\x00binary secret password token .env .age known_hosts bytes"
            )
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")

    def test_public_scan_rejects_oversized_files(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            large = public / "large.bin"
            with large.open("wb") as handle:
                handle.truncate(getattr(self.tool, "MAX_FILE_BYTES", 8 * 1024 * 1024) + 1)
            result = run_cli("scan-public", "--root", str(public))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_public_scan_fails_closed_when_file_read_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release.json").write_text("{}", encoding="utf-8")
            failure = self.tool.ReleaseVaultError(
                "E_RELEASE_NOT_READY", "public file could not be read"
            )
            with mock.patch.object(
                self.tool, "_scan_public_file", create=True, side_effect=failure
            ):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_fails_closed_when_enumeration_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            failure = self.tool.ReleaseVaultError(
                "E_RELEASE_NOT_READY", "public tree could not be enumerated"
            )
            with mock.patch.object(
                self.tool, "_iter_public_entries", create=True, side_effect=failure
            ):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_fails_closed_when_file_changes_during_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release.json").write_text("{}", encoding="utf-8")
            failure = self.tool.ReleaseVaultError(
                "E_RELEASE_NOT_READY", "public file changed during scan"
            )
            with mock.patch.object(
                self.tool, "_scan_public_file", create=True, side_effect=failure
            ):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_fails_when_final_file_identity_or_size_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release.json").write_text("{}", encoding="utf-8")
            real_fstat = self.tool.os.fstat
            calls = 0

            def changing_fstat(descriptor):
                nonlocal calls
                calls += 1
                result = real_fstat(descriptor)
                if calls < 2:
                    return result
                values = list(result)
                values[6] += 1
                return os.stat_result(values)

            with mock.patch.object(self.tool.os, "fstat", side_effect=changing_fstat):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertGreaterEqual(calls, 2)

    def test_public_scan_enforces_entry_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "one.txt").write_text("safe", encoding="utf-8")
            (public / "two.txt").write_text("safe", encoding="utf-8")
            with mock.patch.object(self.tool, "MAX_SCAN_ENTRIES", 1, create=True):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_caps_scandir_before_materializing_unbounded_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            state = {"count": 0}

            class EntryStream:
                def __iter__(self):
                    return self

                def __next__(self):
                    state["count"] += 1
                    if state["count"] > 3:
                        raise AssertionError("scanner enumerated past the entry cap")
                    entry = mock.Mock()
                    entry.name = "entry-%s.txt" % state["count"]
                    entry.path = str(public / entry.name)
                    return entry

            class ScanDir:
                def __enter__(self):
                    return EntryStream()

                def __exit__(self, exc_type, exc_value, traceback):
                    return False

            with mock.patch.object(self.tool, "MAX_SCAN_ENTRIES", 2):
                with mock.patch.object(self.tool.os, "scandir", return_value=ScanDir()):
                    with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                        self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertEqual(state["count"], 3)

    def test_public_scan_enforces_depth_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            current = public
            for index in range(getattr(self.tool, "MAX_SCAN_DEPTH", 32) + 1):
                current.mkdir(parents=True)
                current = current / ("d%s" % index)
            current.mkdir()
            (current / "release.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_enforces_global_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            (public / "release.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(self.tool, "MAX_SCAN_SECONDS", 0, create=True):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_public_scan_detects_same_size_in_place_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            target = public / "release.json"
            target.write_text("{}", encoding="utf-8")
            real_read = self.tool.os.read
            changed = {"value": False}

            def mutating_read(descriptor, size):
                data = real_read(descriptor, size)
                if data and not changed["value"]:
                    changed["value"] = True
                    before = os.stat(target)
                    with target.open("r+b") as handle:
                        handle.write(b"[]")
                        handle.flush()
                    os.utime(
                        target,
                        ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000),
                    )
                return data

            with mock.patch.object(self.tool.os, "read", side_effect=mutating_read):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertTrue(changed["value"])

    def test_scan_public_root_help_states_trust_material_boundary(self):
        result = run_cli("scan-public", "--help")
        self.assertIn("trust-material", result.stdout)
        self.assertIn("payload", result.stdout)
        self.assertIn("marker-free", TOOL.read_text(encoding="utf-8"))

    def test_parser_usage_errors_do_not_echo_secret_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_cli(
                "scan-public",
                "--root",
                temp,
                "--password=do-not-echo-this",
            )
        self.assertEqual(result.returncode, self.tool.EXIT_USAGE)
        output = json.loads(result.stdout)
        self.assertEqual(output["error_code"], "E_USAGE")
        self.assertNotIn("do-not-echo-this", result.stdout)


if __name__ == "__main__":
    unittest.main()
