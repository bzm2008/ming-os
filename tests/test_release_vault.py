import importlib.util
import json
import os
import pathlib
import shutil
import stat
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

    def test_create_bundle_rejects_input_symlink_and_repository_output(self):
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
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            "--public-keyring",
            str(self.keyring),
            "--policy",
            str(self.policy),
            "--primary-fingerprint",
            "A" * 40,
            "--signing-fingerprint",
            "B" * 40,
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(result.returncode, self.tool.EXIT_OK, result.stderr)
        payload = json.loads(result.stdout)
        invocation = payload["test_invocation"]
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
        result2 = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(second),
            "--recipient-file",
            str(self.recipient),
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(result2.returncode, self.tool.EXIT_OK, result2.stderr)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())

    def test_create_bundle_receipt_is_atomic_validated_and_public_only(self):
        result = self.cli(
            "create-bundle",
            "--input",
            str(self.private_input),
            "--output",
            str(self.output),
            "--recipient-file",
            str(self.recipient),
            "--public-keyring",
            str(self.keyring),
            "--policy",
            str(self.policy),
            "--primary-fingerprint",
            "A" * 40,
            "--signing-fingerprint",
            "B" * 40,
            env={"MING_RELEASE_TEST_AGE": "1"},
        )
        self.assertEqual(result.returncode, self.tool.EXIT_OK, result.stderr)
        receipt_path = self.vault / "receipts" / "recovery-bundle-1.json"
        self.assertTrue(receipt_path.is_file())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(self.tool.validate_receipt(receipt), receipt)
        serialized = json.dumps(receipt)
        for forbidden in (str(self.vault), str(self.private_input), "ming.sca-hub.cn", "ssh://"):
            self.assertNotIn(forbidden, serialized)


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
