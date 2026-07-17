import importlib.util
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
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


def run_cli(*args, env=None):
    child_env = os.environ.copy()
    if env is not None:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        timeout=10,
        env=child_env,
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

    def test_receipt_rejects_oversized_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "receipt.json"
            path.write_text(
                json.dumps(self.receipt)
                + " " * (getattr(self.tool, "MAX_RECEIPT_BYTES", 1024 * 1024) + 1),
                encoding="utf-8",
            )
            result = run_cli("verify-receipt", "--receipt", str(path))
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_receipt_rejects_symlink_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_receipt(temp, self.receipt)
            real_stat = self.tool.os.stat
            regular = real_stat(path, follow_symlinks=False)
            values = list(regular)
            values[0] = stat.S_IFLNK | 0o777
            symlink_stat = os.stat_result(values)

            def pretend_symlink(candidate, *args, **kwargs):
                if pathlib.Path(candidate) == path:
                    return symlink_stat
                return real_stat(candidate, *args, **kwargs)

            with mock.patch.object(self.tool.os, "stat", side_effect=pretend_symlink):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool._load_json(path)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_receipt_rejects_initial_to_open_metadata_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_receipt(temp, self.receipt)
            real_fstat = self.tool.os.fstat
            calls = 0

            def changed_fstat(descriptor):
                nonlocal calls
                calls += 1
                result = real_fstat(descriptor)
                changed = mock.Mock(wraps=result)
                for field in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns"):
                    setattr(changed, field, getattr(result, field))
                birthtime = getattr(result, "st_birthtime_ns", None)
                if birthtime is not None:
                    changed.st_birthtime_ns = birthtime + 1
                else:
                    changed.st_ctime_ns = result.st_ctime_ns + 1
                return changed

            with mock.patch.object(self.tool.os, "fstat", side_effect=changed_fstat):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool._load_json(path)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertGreaterEqual(calls, 1)

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

    def test_public_scan_rechecks_directory_after_children_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            target = public / "release.json"
            target.write_text("{}", encoding="utf-8")
            changed = {"value": False}

            def mutate_directory(entry, **kwargs):
                if not changed["value"]:
                    changed["value"] = True
                    before = os.stat(public)
                    (public / "late.json").write_text("{}", encoding="utf-8")
                    os.utime(
                        public,
                        ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000),
                    )
                return None

            with mock.patch.object(
                self.tool, "_scan_public_file", side_effect=mutate_directory
            ):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertTrue(changed["value"])

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

    def test_public_scan_rejects_initial_to_open_metadata_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            public = pathlib.Path(temp) / "public"
            public.mkdir()
            target = public / "release.json"
            target.write_text("{}", encoding="utf-8")
            real_fstat = self.tool.os.fstat
            calls = 0

            def changed_fstat(descriptor):
                nonlocal calls
                calls += 1
                result = real_fstat(descriptor)
                changed = mock.Mock(wraps=result)
                for field in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns"):
                    setattr(changed, field, getattr(result, field))
                birthtime = getattr(result, "st_birthtime_ns", None)
                if birthtime is not None:
                    changed.st_birthtime_ns = birthtime + 1
                else:
                    changed.st_ctime_ns = result.st_ctime_ns + 1
                return changed

            with mock.patch.object(self.tool.os, "fstat", side_effect=changed_fstat):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.scan_public_tree(public)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertGreaterEqual(calls, 1)

    def test_scan_public_root_help_states_trust_material_boundary(self):
        result = run_cli("scan-public", "--help")
        self.assertEqual(result.returncode, self.tool.EXIT_OK)
        self.assertEqual(result.stderr, "")
        decoder = json.JSONDecoder()
        output, end = decoder.raw_decode(result.stdout)
        self.assertEqual(result.stdout[end:].strip(), "")
        self.assertEqual(output["status"], "ok")
        self.assertIn("trust-material", output["help"])
        self.assertIn("payload", output["help"])
        self.assertIn("marker-free", TOOL.read_text(encoding="utf-8"))

    def test_top_level_and_receipt_help_are_json_objects(self):
        for args in (("--help",), ("verify-receipt", "--help")):
            with self.subTest(args=args):
                result = run_cli(*args)
                self.assertEqual(result.returncode, self.tool.EXIT_OK)
                self.assertEqual(result.stderr, "")
                output, end = json.JSONDecoder().raw_decode(result.stdout)
                self.assertEqual(result.stdout[end:].strip(), "")
                self.assertEqual(output["status"], "ok")
                self.assertIn("usage:", output["help"])

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


class ReleaseVaultBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.vault = self.root / "release-vault"
        (self.vault / "encrypted").mkdir(parents=True)
        (self.vault / "receipts").mkdir()
        (self.vault / "public").mkdir()
        self.private_input = self.root / "private-input"
        self.private_input.mkdir()
        (self.private_input / "secret.key").write_bytes(b"private recovery material\n")
        (self.private_input / "revocation.crt").write_bytes(b"revocation certificate\n")
        (self.private_input / "nested").mkdir()
        (self.private_input / "nested" / "manifest.txt").write_text(
            "recovery manifest\n", encoding="utf-8"
        )
        self.recipient = self.root / "recipient.txt"
        self.recipient.write_text("age1example-recipient\n", encoding="utf-8")
        self.keyring = self.vault / "public" / "release-keyring.gpg"
        self.policy = self.vault / "public" / "key-policy.json"
        self.keyring.write_bytes(b"public keyring\n")
        self.policy.write_text('{"allowed_primary_fingerprints": []}\n', encoding="utf-8")
        self.output = self.vault / "encrypted" / "recovery-bundle-1.age"

    def tearDown(self):
        self.temp_dir.cleanup()

    def cli(self, *args, env=None):
        child_env = {"MING_RELEASE_VAULT": str(self.vault)}
        if env:
            child_env.update(env)
        return run_cli(*args, env=child_env)

    def fake_age_runner(self, calls=None):
        if calls is None:
            calls = []

        def runner(*, argv, input_bytes, output_path, environment):
            calls.append(
                {
                    "argv": list(argv),
                    "input": bytes(input_bytes),
                    "environment": dict(environment),
                }
            )
            output_path.write_bytes(b"age-test-v1\n" + input_bytes)

        return runner

    def test_create_bundle_requires_explicit_vault_and_rejects_output_outside_it(self):
        missing = run_cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_VAULT": ""},
        )
        self.assertEqual(missing.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(missing.stdout)["error_code"], "E_VAULT_NOT_CONFIGURED")

        outside = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.root / "outside.age"),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(outside.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(outside.stdout)["error_code"], "E_VAULT_PERMISSION")

    def test_create_bundle_rejects_input_symlink(self):
        try:
            os.symlink(self.private_input / "secret.key", self.private_input / "link.key")
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink support is unavailable: %s" % exc)
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_create_bundle_rejects_repository_output(self):
        repository_output = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(ROOT / ".release-bundle.age"),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(repository_output.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(repository_output.stdout)["error_code"], "E_VAULT_PERMISSION")

    def test_cli_test_age_environment_never_creates_a_bundle(self):
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            env={
                "MING_RELEASE_TEST_AGE": "1",
                "MING_RELEASE_AGE": "age-does-not-exist",
                "PATH": "",
            },
        )
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".sha256").exists())
        self.assertFalse((self.vault / "receipts" / "recovery-bundle-1.json").exists())

    def test_create_bundle_rejects_password_option_and_environment(self):
        option = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            "--password",
            "do-not-echo-this",
        )
        self.assertEqual(option.returncode, self.tool.EXIT_USAGE)
        self.assertNotIn("do-not-echo-this", option.stdout)

        password_env = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_PASSWORD": "do-not-echo-this", "MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(password_env.returncode, self.tool.EXIT_NOT_READY)
        self.assertNotIn("do-not-echo-this", password_env.stdout)

    def test_create_bundle_password_tty_requires_a_tty(self):
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--password-tty",
            env={"MING_RELEASE_AGE": "age-does-not-exist", "PATH": ""},
        )
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())

    def test_create_bundle_password_tty_uses_only_tty_prompt_mode(self):
        calls = []
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool.sys.stdin, "isatty", return_value=True):
                self.tool.create_bundle(
                    self.private_input,
                    self.output,
                    None,
                    age_runner=self.fake_age_runner(calls),
                    password_tty=True,
                )
        self.assertEqual(calls[0]["argv"][0:2], ["age", "-p"])
        self.assertNotIn("password", " ".join(calls[0]["argv"]).lower())
        self.assertNotIn("MING_RELEASE_PASSWORD", calls[0]["environment"])

    def test_create_bundle_rejects_absent_age(self):
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            env={"PATH": "", "MING_RELEASE_AGE": "age-not-installed"},
        )
        self.assertEqual(result.returncode, self.tool.EXIT_NOT_READY)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

    def test_create_bundle_uses_recipient_file_and_deterministic_tar_stream(self):
        calls = []
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            payload = self.tool.create_bundle(
                self.private_input,
                self.output,
                self.recipient,
                age_runner=self.fake_age_runner(calls),
                public_keyring=self.keyring,
                policy=self.policy,
                fingerprints=("A" * 40, "B" * 40),
            )
        invocation = calls[0]
        self.assertIn("-R", invocation["argv"])
        self.assertNotIn("password", " ".join(invocation["argv"]).lower())
        self.assertNotIn("MING_RELEASE_PASSWORD", invocation["environment"])
        self.assertNotIn("AGE_PASSPHRASE", invocation["environment"])
        self.assertTrue(self.output.is_file())
        sidecar = self.output.with_suffix(".sha256")
        self.assertTrue(sidecar.is_file())
        expected_hash = __import__("hashlib").sha256(self.output.read_bytes()).hexdigest()
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "%s  %s\n" % (expected_hash, self.output.name))
        self.assertEqual(payload["bundle_sha256"], expected_hash)

        second = self.vault / "encrypted" / "recovery-bundle-2.age"
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            self.tool.create_bundle(
                self.private_input,
                second,
                self.recipient,
                age_runner=self.fake_age_runner(),
            )
        self.assertEqual(self.output.read_bytes(), second.read_bytes())

    def test_create_bundle_receipt_is_atomic_validated_and_public_only(self):
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            self.tool.create_bundle(
                self.private_input,
                self.output,
                self.recipient,
                age_runner=self.fake_age_runner(),
                public_keyring=self.keyring,
                policy=self.policy,
                fingerprints=("A" * 40, "B" * 40),
            )
        receipt_path = self.vault / "receipts" / "recovery-bundle-1.json"
        self.assertTrue(receipt_path.is_file())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(self.tool.validate_receipt(receipt), receipt)
        serialized = json.dumps(receipt)
        for forbidden in (str(self.vault), str(self.private_input), "ming.sca-hub.cn", "ssh://"):
            self.assertNotIn(forbidden, serialized)

    def test_create_bundle_rejects_input_symlink_race_after_open(self):
        target = self.private_input / "secret.key"
        regular = self.tool.os.lstat(target)
        values = list(regular)
        values[0] = stat.S_IFLNK | 0o777
        symlink_stat = os.stat_result(values)
        calls = 0
        real_lstat = self.tool.os.lstat

        def race_lstat(candidate, *args, **kwargs):
            nonlocal calls
            result = real_lstat(candidate, *args, **kwargs)
            if pathlib.Path(candidate) == target:
                calls += 1
                if calls >= 2:
                    return symlink_stat
            return result

        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool.os, "lstat", side_effect=race_lstat):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.create_bundle(
                        self.private_input,
                        self.output,
                        self.recipient,
                        age_runner=self.fake_age_runner(),
                    )
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())

    def test_create_bundle_rejects_queued_directory_symlink_race(self):
        nested = self.private_input / "nested"
        outside = self.root / "outside-input"
        outside.mkdir()
        (outside / "escaped.key").write_bytes(b"outside tree\n")
        try:
            probe = self.root / "symlink-probe"
            os.symlink(outside, probe, target_is_directory=True)
            probe.unlink()
            real_nested = self.root / "nested-real"
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink support is unavailable: %s" % exc)
        real_scandir = self.tool.os.scandir
        swapped = False

        # Replace the queued directory immediately before scandir opens it.
        def replacing_scandir(candidate):
            nonlocal swapped
            if pathlib.Path(candidate) == nested and not swapped:
                nested.rename(real_nested)
                os.symlink(outside, nested, target_is_directory=True)
                swapped = True
            return real_scandir(candidate)

        try:
            with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
                with mock.patch.object(self.tool.os, "scandir", side_effect=replacing_scandir):
                    with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                        self.tool.create_bundle(
                            self.private_input,
                            self.output,
                            self.recipient,
                            age_runner=self.fake_age_runner(),
                        )
            self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
            self.assertFalse(self.output.exists())
        finally:
            if nested.is_symlink():
                nested.unlink()
            if real_nested.exists():
                real_nested.rename(nested)

    def test_create_bundle_rejects_unbounded_bundle_enumeration(self):
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool, "MAX_BUNDLE_ENTRIES", 1):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.create_bundle(
                        self.private_input,
                        self.output,
                        self.recipient,
                        age_runner=self.fake_age_runner(),
                    )
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())

    def test_create_bundle_rejects_scandir_entry_outside_input_root(self):
        outside = self.root / "outside-entry.key"
        outside.write_bytes(b"outside\n")

        class Entry:
            name = "escaped.key"
            path = str(outside)

        class EntryStream:
            def __init__(self):
                self.done = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                if self.done:
                    raise StopIteration
                self.done = True
                return Entry()

        real_scandir = self.tool.os.scandir

        def fake_scandir(candidate):
            if pathlib.Path(candidate) == self.private_input:
                return EntryStream()
            return real_scandir(candidate)

        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool.os, "scandir", side_effect=fake_scandir):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.create_bundle(
                        self.private_input,
                        self.output,
                        self.recipient,
                        age_runner=self.fake_age_runner(),
                    )
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())

    def test_cli_rejects_invalid_bundle_id_without_receipt_metadata(self):
        output = io.StringIO()
        fake = self.fake_age_runner()
        arguments = [
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            "--bundle-id",
            "C:/Users/secret/recovery",
        ]
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool, "_resolve_age_runner", return_value=(fake, None)):
                with redirect_stdout(output):
                    code = self.tool.main(arguments)
        self.assertEqual(code, self.tool.EXIT_NOT_READY)
        emitted = json.loads(output.getvalue())
        self.assertEqual(emitted["error_code"], "E_RELEASE_NOT_READY")
        self.assertNotIn("C:/Users/secret/recovery", output.getvalue())
        self.assertFalse(self.output.exists())

    def test_hash_regular_file_enforces_deadline_and_size_limit(self):
        target = self.root / "bounded.bin"
        target.write_bytes(b"0123456789")
        with self.assertRaises(self.tool.ReleaseVaultError) as size_error:
            self.tool._hash_regular_file(target, max_bytes=4)
        self.assertEqual(size_error.exception.error_code, "E_RELEASE_NOT_READY")
        with self.assertRaises(self.tool.ReleaseVaultError) as deadline_error:
            self.tool._hash_regular_file(target, deadline=0)
        self.assertEqual(deadline_error.exception.error_code, "E_RELEASE_NOT_READY")

    def test_tar_copy_checks_global_deadline_after_enumeration(self):
        root, entries = self.tool._bundle_entries(self.private_input)
        failure = self.tool.ReleaseVaultError("E_RELEASE_NOT_READY", "bundle deadline exceeded")
        with mock.patch.object(self.tool, "_bundle_entries", return_value=(root, entries)):
            with mock.patch.object(self.tool, "_check_bundle_deadline", side_effect=failure):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool._build_deterministic_tar(self.private_input)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_tar_stream_enforces_total_archive_size_limit(self):
        empty = self.root / "empty-input"
        empty.mkdir()
        with mock.patch.object(self.tool, "MAX_BUNDLE_BYTES", 1):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._build_deterministic_tar(empty)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_atomic_write_fails_closed_when_output_parent_is_replaced(self):
        parent = self.vault / "encrypted"
        parent_real = self.vault / "encrypted-real"
        outside = self.root / "outside-output"
        outside.mkdir()
        target = parent / "race.txt"
        try:
            os.symlink(outside, self.root / "parent-link", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink support is unavailable: %s" % exc)
        real_link = self.tool.os.link
        swapped = False

        def racing_link(source, destination, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                parent.rename(parent_real)
                os.symlink(outside, parent, target_is_directory=True)
                swapped = True
            return real_link(source, destination, *args, **kwargs)

        try:
            with mock.patch.object(self.tool.os, "link", side_effect=racing_link):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool._atomic_write(target, b"race", "output")
            self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
            self.assertFalse(outside.joinpath("race.txt").exists())
        finally:
            if parent.is_symlink():
                parent.unlink()
            if parent_real.exists():
                parent_real.rename(parent)

    def test_atomic_write_rejects_destination_inserted_after_precheck(self):
        target = self.vault / "encrypted" / "inserted.txt"
        real_link = self.tool.os.link

        def insert_then_link(source, destination, *args, **kwargs):
            target.write_bytes(b"attacker-original\n")
            return real_link(source, destination, *args, **kwargs)

        with mock.patch.object(self.tool.os, "link", side_effect=insert_then_link):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._atomic_write(target, b"trusted\n", "output")
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        self.assertEqual(target.read_bytes(), b"attacker-original\n")

    def test_atomic_write_preserves_replaced_destination_when_source_cleanup_fails(self):
        target = self.vault / "encrypted" / "unlink-race.txt"
        real_unlink = self.tool.os.unlink
        triggered = False

        def fail_source_unlink(path, *args, **kwargs):
            nonlocal triggered
            if not triggered:
                attacker = self.root / "attacker-unlink.txt"
                attacker.write_bytes(b"attacker-after-link\n")
                os.replace(attacker, target)
                triggered = True
                raise OSError("simulated source unlink failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(self.tool.os, "unlink", side_effect=fail_source_unlink):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._atomic_write(target, b"trusted\n", "output")
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        self.assertEqual(target.read_bytes(), b"attacker-after-link\n")

    def test_atomic_write_preserves_trusted_destination_when_source_cleanup_fails(self):
        target = self.vault / "encrypted" / "unlink-failure.txt"
        real_unlink = self.tool.os.unlink
        triggered = False

        def fail_source_unlink(path, *args, **kwargs):
            nonlocal triggered
            if not triggered:
                triggered = True
                raise OSError("simulated source unlink failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(self.tool.os, "unlink", side_effect=fail_source_unlink):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._atomic_write(target, b"trusted\n", "output")
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        self.assertEqual(target.read_bytes(), b"trusted\n")

    def test_create_bundle_cleans_new_artifacts_when_sidecar_write_fails(self):
        original_atomic_write = self.tool._atomic_write

        def fail_sidecar(path, data, field):
            if field == "sidecar":
                raise self.tool.ReleaseVaultError("E_VAULT_PERMISSION", "sidecar write failed")
            return original_atomic_write(path, data, field)

        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool, "_atomic_write", side_effect=fail_sidecar):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.create_bundle(
                        self.private_input,
                        self.output,
                        self.recipient,
                        age_runner=self.fake_age_runner(),
                    )
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".sha256").exists())

    def test_create_bundle_cleanup_preserves_replaced_bundle(self):
        original_atomic_write = self.tool._atomic_write

        def replace_then_fail(path, data, field):
            if field == "sidecar":
                attacker = self.root / "attacker.age"
                attacker.write_bytes(b"attacker-bundle\n")
                os.replace(attacker, self.output)
                raise self.tool.ReleaseVaultError("E_VAULT_PERMISSION", "sidecar write failed")
            return original_atomic_write(path, data, field)

        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool, "_atomic_write", side_effect=replace_then_fail):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.create_bundle(
                        self.private_input,
                        self.output,
                        self.recipient,
                        age_runner=self.fake_age_runner(),
                    )
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        self.assertEqual(self.output.read_bytes(), b"attacker-bundle\n")

    def test_write_receipt_readback_cleanup_preserves_replaced_receipt(self):
        bundle = self.vault / "encrypted" / "recovery-bundle-1.age"
        sidecar = bundle.with_suffix(".sha256")
        bundle.write_bytes(b"encrypted\n")
        digest = __import__("hashlib").sha256(bundle.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {bundle.name}\n", encoding="ascii")
        receipt = self.vault / "receipts" / "recovery-bundle-1.json"
        original_atomic_write = self.tool._atomic_write

        def replace_receipt_after_write(path, data, field):
            identity = original_atomic_write(path, data, field)
            if field == "receipt":
                path.write_text('{"attacker":true}\n', encoding="ascii")
            return identity

        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with mock.patch.object(self.tool, "_atomic_write", side_effect=replace_receipt_after_write):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.write_receipt(
                        bundle,
                        sidecar,
                        self.keyring,
                        self.policy,
                        "recovery-bundle-1",
                        1,
                        ("A" * 40, "B" * 40),
                    )
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertEqual(receipt.read_text(encoding="ascii"), '{"attacker":true}\n')

    def test_receipt_metadata_is_prevalidated_before_bundle_commit(self):
        missing_keyring = self.root / "missing-keyring.gpg"
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.create_bundle(
                    self.private_input,
                    self.output,
                    self.recipient,
                    age_runner=self.fake_age_runner(),
                    public_keyring=missing_keyring,
                    policy=self.policy,
                    fingerprints=("A" * 40, "B" * 40),
                )
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix(".sha256").exists())

    def test_hash_regular_file_rejects_initial_path_replacement(self):
        target = self.root / "hash-target.bin"
        target.write_bytes(b"hash me\n")
        regular = self.tool.os.lstat(target)
        values = list(regular)
        values[0] = stat.S_IFLNK | 0o777
        symlink_stat = os.stat_result(values)
        calls = 0
        real_lstat = self.tool.os.lstat

        def race_lstat(candidate, *args, **kwargs):
            nonlocal calls
            result = real_lstat(candidate, *args, **kwargs)
            if pathlib.Path(candidate) == target:
                calls += 1
                if calls >= 3:
                    return symlink_stat
            return result

        with mock.patch.object(self.tool.os, "lstat", side_effect=race_lstat):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._hash_regular_file(target)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")

    def test_sidecar_read_is_bounded_and_rechecks_file_identity(self):
        target = self.root / "bundle.age"
        sidecar = target.with_suffix(".sha256")
        target.write_bytes(b"bundle\n")
        digest = __import__("hashlib").sha256(target.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {target.name}\n", encoding="ascii")
        with mock.patch.object(pathlib.Path, "read_text", side_effect=AssertionError("unbounded read")):
            self.assertEqual(self.tool._read_sidecar(sidecar, target.name), digest)

    def test_sidecar_read_rejects_open_to_final_metadata_change(self):
        target = self.root / "bundle.age"
        sidecar = target.with_suffix(".sha256")
        target.write_bytes(b"bundle\n")
        digest = __import__("hashlib").sha256(target.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {target.name}\n", encoding="ascii")
        real_fstat = self.tool.os.fstat
        calls = 0

        def changed_fstat(descriptor):
            nonlocal calls
            calls += 1
            result = real_fstat(descriptor)
            if calls < 2:
                return result
            changed = mock.Mock(wraps=result)
            for field in ("st_mode", "st_ino", "st_dev", "st_size", "st_ctime_ns"):
                setattr(changed, field, getattr(result, field))
            changed.st_mtime_ns = result.st_mtime_ns + 1
            return changed

        with mock.patch.object(self.tool.os, "fstat", side_effect=changed_fstat):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool._read_sidecar(sidecar, target.name)
        self.assertEqual(caught.exception.error_code, "E_RELEASE_NOT_READY")


class ReleaseVaultNasTests(unittest.TestCase):
    """The NAS verifier must stay inside the fixed read-only SSH boundary."""

    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.vault = self.root / "release-vault"
        self._vault_env = mock.patch.dict(
            os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False
        )
        self._vault_env.start()
        (self.vault / "encrypted").mkdir(parents=True)
        (self.vault / "receipts").mkdir()
        self.bundle = self.vault / "encrypted" / "recovery-bundle-1.age"
        self.bundle.write_bytes(b"encrypted recovery bundle\n")
        self.bundle_hash = __import__("hashlib").sha256(self.bundle.read_bytes()).hexdigest()
        self.sidecar = self.vault / "encrypted" / "recovery-bundle-1.sha256"
        self.sidecar.write_text(
            f"{self.bundle_hash}  {self.bundle.name}\n", encoding="ascii"
        )
        receipt = json.loads(GOOD_RECEIPT.read_text(encoding="utf-8"))
        receipt.update(
            {
                "bundle_sha256": self.bundle_hash,
                "bundle_bytes": self.bundle.stat().st_size,
                "nas_object": "recovery-bundle-1",
            }
        )
        self.receipt = self.vault / "receipts" / "recovery-bundle-1.json"
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_text(
            "nas-tunnel ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEPIN\n",
            encoding="ascii",
        )
        self.config_path = self.root / "nas-config.json"
        self.config = {
            "host_alias": "nas-tunnel",
            "port": 2222,
            "remote_dir": "/srv/ming-os/release-vault/v1",
            "known_hosts": str(self.known_hosts),
            "object": self.bundle.name,
            "sidecar": self.sidecar.name,
            "receipt": self.receipt.name,
        }
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        self._vault_env.stop()
        self.temp_dir.cleanup()

    def fake_ssh(
        self,
        calls=None,
        *,
        symlink=False,
        missing_sidecar=False,
        hash_value=None,
        sidecar_stat_delta=0,
        receipt_stat_delta=0,
        object_stat_delta_after_hash=0,
    ):
        if calls is None:
            calls = []
        remote_hash = hash_value or self.bundle_hash
        remote_sidecar = f"{remote_hash}  {self.bundle.name}\n"
        remote_receipt = self.receipt.read_text(encoding="utf-8")

        object_stat_calls = 0

        def runner(*, argv, command, operation, path, timeout=None):
            nonlocal object_stat_calls
            calls.append({"argv": list(argv), "command": list(command), "operation": operation, "path": path})
            if operation == "stat":
                if path == self.bundle.name:
                    object_stat_calls += 1
                    delta = object_stat_delta_after_hash if object_stat_calls > 1 else 0
                    size = self.bundle.stat().st_size + delta
                elif path == self.sidecar.name:
                    size = len(remote_sidecar.encode("ascii")) + sidecar_stat_delta
                else:
                    size = len(remote_receipt.encode("utf-8")) + receipt_stat_delta
                return {"mode": "symlink" if symlink else "regular", "size": size}
            if operation == "sha256sum":
                return f"{remote_hash}  /srv/ming-os/release-vault/v1/{self.bundle.name}\n"
            if operation == "cat" and path == self.sidecar.name:
                if missing_sidecar:
                    raise FileNotFoundError(path)
                return remote_sidecar
            if operation == "cat" and path == self.receipt.name:
                return remote_receipt
            raise AssertionError((operation, path))

        return runner

    def test_verify_nas_uses_only_fixed_read_commands(self):
        calls = []
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            result = self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(calls))
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(len(calls), 4)
        for call in calls:
            self.assertIn(call["operation"], ("stat", "sha256sum", "cat"))
            joined = " ".join(call["command"])
            self.assertNotRegex(joined, r"(?:\brm\b|\bmv\b|\bchmod\b|\bsh -c\b|;|&&|\$\(|\.\.)")

    def test_verify_nas_rechecks_stats_after_hash_and_reads(self):
        calls = []
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(calls))
        object_stats = [
            index
            for index, call in enumerate(calls)
            if call["operation"] == "stat" and call["path"] == self.bundle.name
        ]
        sidecar_stats = [
            index
            for index, call in enumerate(calls)
            if call["operation"] == "stat" and call["path"] == self.sidecar.name
        ]
        receipt_stats = [
            index
            for index, call in enumerate(calls)
            if call["operation"] == "stat" and call["path"] == self.receipt.name
        ]
        self.assertGreaterEqual(len(object_stats), 2)
        self.assertGreaterEqual(len(sidecar_stats), 2)
        self.assertGreaterEqual(len(receipt_stats), 2)
        self.assertGreater(object_stats[-1], next(index for index, call in enumerate(calls) if call["operation"] == "sha256sum"))
        self.assertGreater(sidecar_stats[-1], next(index for index, call in enumerate(calls) if call["operation"] == "cat" and call["path"] == self.sidecar.name))
        self.assertGreater(receipt_stats[-1], next(index for index, call in enumerate(calls) if call["operation"] == "cat" and call["path"] == self.receipt.name))

    def test_verify_nas_rejects_metadata_length_changes(self):
        for mutation in (
            {"sidecar_stat_delta": 1},
            {"receipt_stat_delta": 1},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(**mutation))
                self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_rejects_object_size_change_after_hash(self):
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(
                self.config,
                ssh_runner=self.fake_ssh(object_stat_delta_after_hash=1),
            )
        self.assertEqual(caught.exception.error_code, "E_VAULT_HASH_MISMATCH")

    def test_verify_nas_rejects_remote_path_traversal(self):
        for mutation in ("../outside.age", "recovery-bundle-1.age/child"):
            candidate = dict(self.config)
            candidate["object"] = mutation
            with self.subTest(mutation=mutation):
                with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                    self.tool.verify_nas(candidate, ssh_runner=self.fake_ssh())
                self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_rejects_unknown_config_fields_and_unapproved_ip_aliases(self):
        unknown = dict(self.config)
        unknown["unexpected"] = True
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(unknown, ssh_runner=self.fake_ssh())
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

        ip_alias = dict(self.config)
        ip_alias["host_alias"] = "127.0.0.1"
        with mock.patch.dict(os.environ, {"MING_RELEASE_APPROVED_TUNNEL_ENDPOINTS": ""}, clear=False):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.verify_nas(ip_alias, ssh_runner=self.fake_ssh())
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_rejects_symlink_target(self):
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(symlink=True))
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_reports_missing_sidecar_as_permission_failure(self):
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(missing_sidecar=True))
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_reports_remote_hash_mismatch(self):
        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(hash_value="f" * 64))
        self.assertEqual(caught.exception.error_code, "E_VAULT_HASH_MISMATCH")

    def test_verify_nas_maps_tunnel_and_host_key_failures_to_unreachable(self):
        def unavailable(**kwargs):
            raise subprocess.CalledProcessError(255, kwargs["argv"], stderr=b"connection refused")

        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=unavailable)
        self.assertEqual(caught.exception.error_code, "E_VAULT_UNREACHABLE")

        def host_key_mismatch(**kwargs):
            raise subprocess.CalledProcessError(255, kwargs["argv"], stderr=b"host key verification failed")

        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=host_key_mismatch)
        self.assertEqual(caught.exception.error_code, "E_VAULT_UNREACHABLE")

    def test_verify_nas_pins_host_key_and_disables_forwarding(self):
        calls = []
        with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
            self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh(calls))
        argv = calls[0]["argv"]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ConnectTimeout=10", argv)
        self.assertIn("ForwardAgent=no", argv)
        self.assertIn("UserKnownHostsFile=" + str(self.known_hosts), argv)

    def test_verify_nas_uses_one_bounded_deadline_for_all_ssh_calls(self):
        calls = []
        base = self.fake_ssh()

        def timed_runner(**kwargs):
            calls.append(kwargs["timeout"])
            return base(**kwargs)

        with mock.patch.object(self.tool, "NAS_VERIFY_TIMEOUT_SECONDS", 25.0, create=True):
            with mock.patch.dict(os.environ, {"MING_RELEASE_VAULT": str(self.vault)}, clear=False):
                self.tool.verify_nas(self.config, ssh_runner=timed_runner)
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(0 < timeout <= 25.0 for timeout in calls))
        self.assertLessEqual(max(calls), 25.0)

    def test_verify_nas_rejects_known_hosts_replacement_between_calls(self):
        calls = []
        base = self.fake_ssh(calls)

        def replacing_runner(**kwargs):
            result = base(**kwargs)
            if len(calls) == 1:
                replacement = self.root / "known_hosts.replacement"
                replacement.write_text("nas-tunnel ssh-ed25519 AAAAChanged\n", encoding="ascii")
                os.replace(replacement, self.known_hosts)
            return result

        with self.assertRaises(self.tool.ReleaseVaultError) as caught:
            self.tool.verify_nas(self.config, ssh_runner=replacing_runner)
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_verify_nas_accepts_normal_known_hosts_fixture(self):
        validated = self.tool._nas_validate_config(self.config)
        self.assertEqual(validated["host_alias"], "nas-tunnel")

    def test_verify_nas_rejects_known_hosts_content_change_between_calls(self):
        calls = []
        base = self.fake_ssh(calls)
        original = self.known_hosts.read_bytes()
        original_stat = self.known_hosts.stat()

        def mutating_runner(**kwargs):
            result = base(**kwargs)
            if len(calls) == 1:
                changed = bytearray(original)
                changed[-2] = ord("Q") if changed[-2] != ord("Q") else ord("R")
                self.known_hosts.write_bytes(bytes(changed))
                os.utime(
                    self.known_hosts,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
            return result

        try:
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.verify_nas(self.config, ssh_runner=mutating_runner)
            self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        finally:
            self.known_hosts.write_bytes(original)

    def test_verify_nas_rejects_wildcard_hashed_or_unrelated_known_hosts(self):
        invalid_lines = (
            "* ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEPIN\n",
            "|1|hashed-host|salt ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEPIN\n",
            "other-host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEPIN\n",
            "nas-tunnel\n",
        )
        original = self.known_hosts.read_text(encoding="ascii")
        try:
            for line in invalid_lines:
                with self.subTest(line=line):
                    self.known_hosts.write_text(line, encoding="ascii")
                    with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                        self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh())
                    self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        finally:
            self.known_hosts.write_text(original, encoding="ascii")

    def test_verify_nas_rejects_unrelated_known_hosts_even_with_pinned_alias(self):
        original = self.known_hosts.read_text(encoding="ascii")
        try:
            self.known_hosts.write_text(
                original + "other-host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEPIN\n",
                encoding="ascii",
            )
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh())
            self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        finally:
            self.known_hosts.write_text(original, encoding="ascii")

    def test_verify_nas_keeps_remote_stdout_bounded_and_stderr_silent(self):
        source = (ROOT / "tools" / "ming-release-vault.py").read_text(encoding="utf-8")
        self.assertNotIn("capture_output=True", source)
        self.assertIn("stderr=subprocess.DEVNULL", source)
        self.assertIn("MAX_REMOTE_OUTPUT_BYTES", source)

    def test_verify_nas_rejects_oversized_known_hosts_before_ssh(self):
        original = self.known_hosts.read_text(encoding="ascii")
        try:
            self.known_hosts.write_text(
                original + ("x" * (self.tool.MAX_KNOWN_HOSTS_BYTES + 1)),
                encoding="ascii",
            )
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.verify_nas(self.config, ssh_runner=self.fake_ssh())
            self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")
        finally:
            self.known_hosts.write_text(original, encoding="ascii")

    def test_verify_nas_rejects_oversized_remote_output(self):
        oversized_output = b"x" * (self.tool.MAX_REMOTE_OUTPUT_BYTES + 1)

        class OversizedProcess:
            def __init__(self):
                self.stdout = io.BytesIO(oversized_output)

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

        with mock.patch.object(self.tool.subprocess, "Popen", return_value=OversizedProcess()):
            with self.assertRaises(self.tool.ReleaseVaultError) as caught:
                self.tool.verify_nas(self.config)
        self.assertEqual(caught.exception.error_code, "E_VAULT_PERMISSION")

    def test_nas_bounded_runner_does_not_wait_unbounded_for_reader_after_timeout(self):
        joins = []

        class FakeReader:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def join(self, *args, **kwargs):
                joins.append(kwargs.get("timeout", args[0] if args else None))

            def is_alive(self):
                return False

        class FakeProcess:
            stdout = io.BytesIO()
            pid = 31337

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(["ssh"], timeout)

            def kill(self):
                pass

            def poll(self):
                return None

        def fake_popen(*args, **kwargs):
            return FakeProcess()

        with mock.patch.object(self.tool.threading, "Thread", FakeReader):
            with mock.patch.object(self.tool.subprocess, "Popen", side_effect=fake_popen):
                with mock.patch.object(
                    self.tool.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["taskkill"], 0),
                ):
                    with self.assertRaises(self.tool._NasRemoteFailure) as caught:
                        self.tool._nas_run_bounded(("ssh",), 0.01)
        self.assertEqual(caught.exception.error_code, "E_VAULT_UNREACHABLE")
        self.assertTrue(joins)
        self.assertIsNotNone(joins[0])
        self.assertLessEqual(joins[0], 0.01)

    def test_nas_bounded_runner_does_not_block_on_windows_pipe_close(self):
        class FakeReader:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def join(self, *args, **kwargs):
                pass

            def is_alive(self):
                return True

        class BlockingStream:
            def read(self, size):
                return b""

            def close(self):
                time.sleep(0.5)

        class FakeProcess:
            stdout = BlockingStream()
            pid = 31338

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(["ssh"], timeout)

            def kill(self):
                pass

            def send_signal(self, signal_number):
                pass

        started = time.monotonic()
        with mock.patch.object(self.tool.os, "name", "nt"):
            with mock.patch.object(self.tool.threading, "Thread", FakeReader):
                with mock.patch.object(self.tool.subprocess, "Popen", return_value=FakeProcess()):
                    with mock.patch.object(
                        self.tool.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(["taskkill"], 0),
                    ):
                        with self.assertRaises(self.tool._NasRemoteFailure) as caught:
                            self.tool._nas_run_bounded(("ssh",), 0.01)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.error_code, "E_VAULT_UNREACHABLE")
        self.assertLess(elapsed, 0.2)

    def test_test_ssh_environment_does_not_bypass_real_runner(self):
        calls = []
        receipt_bytes = self.receipt.read_bytes()

        class FakeProcess:
            def __init__(self, output):
                self.stdout = io.BytesIO(output)

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

        def fake_popen(argv, **kwargs):
            command = argv[argv.index(self.config["host_alias"]) + 1 :]
            if command[0] == "stat":
                target = command[-1]
                if target.endswith(self.bundle.name):
                    size = self.bundle.stat().st_size
                elif target.endswith(self.sidecar.name):
                    size = self.sidecar.stat().st_size
                elif target.endswith(self.receipt.name):
                    size = self.receipt.stat().st_size
                else:
                    raise AssertionError(target)
                output = f"regular file:{size}\n".encode("ascii")
            elif command[0] == "sha256sum":
                output = f"{self.bundle_hash}  /srv/ming-os/release-vault/v1/{self.bundle.name}\n".encode("ascii")
            elif command[0] == "cat" and command[-1].endswith(self.sidecar.name):
                output = self.sidecar.read_bytes()
            else:
                output = receipt_bytes
            calls.append(list(argv))
            return FakeProcess(output)

        with mock.patch.dict(os.environ, {"MING_RELEASE_TEST_SSH": "1"}, clear=False):
            with mock.patch.object(self.tool.subprocess, "Popen", side_effect=fake_popen):
                result = self.tool.verify_nas(self.config)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("test_commands", result)
        self.assertEqual(len(calls), 9)

    def test_remote_command_helper_is_a_fixed_read_only_allowlist(self):
        helper = ROOT / "tools" / "ming-release-vault-remote-command.sh"
        self.assertTrue(helper.is_file())
        text = helper.read_text(encoding="utf-8")
        self.assertIn("stat", text)
        self.assertIn("sha256sum", text)
        self.assertIn("cat", text)
        self.assertIn("/proc/self/fd/", text)
        self.assertIn("readlink -f", text)
        self.assertIn("exec {vault_fd}<", text)
        self.assertIn("stat -L", text)
        self.assertNotRegex(text, r"(?:\brm\b|\bmv\b|\bchmod\b|\bsh -c\b|\beval\b|\$\(|\.\./)")


class ReleaseVaultReceiptSchemaTests(unittest.TestCase):
    def test_release_receipt_schema_matches_validator_contract(self):
        schema_path = ROOT / "docs" / "releases" / "26.4.0-release-receipt.schema.json"
        self.assertTrue(schema_path.is_file())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["format"]["const"], "ming-release-vault-receipt-v1")
        self.assertEqual(
            set(schema["required"]),
            set(load_tool().REQUIRED_RECEIPT_FIELDS),
        )


if __name__ == "__main__":
    unittest.main()
