import hashlib
import importlib.util
import inspect
import io
import json
import pathlib
import tempfile
import types
import unittest
import urllib.parse

from jsonschema import Draft202012Validator, FormatChecker


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "assets" / "ming-update-cli.py"
POLICY_PATH = ROOT / "assets" / "polkit" / "org.mingos.update.policy"
CLI_SCHEMA = ROOT / "contracts" / "ota" / "cli-v1.schema.json"


def load_cli():
    spec = importlib.util.spec_from_file_location("ming_update_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdateCliContractTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CLI_PATH.is_file(), "public transactional OTA CLI is missing")
        self.cli = load_cli()
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-update-cli-")
        self.root = pathlib.Path(self.tmp.name)
        self.keyring = self.root / "release-keyring.gpg"
        self.keyring.write_bytes(b"test public keyring")
        self.policy = self.root / "key-policy.json"
        self.policy.write_text(json.dumps({
            "schema": "ming.ota-key-policy.v1",
            "allowed_primary_fingerprints": ["A" * 40],
            "allowed_signing_fingerprints": ["B" * 40],
            "channels": ["stable"],
            "minimum_bootstrap": "1.0.0",
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def controller(self, *, capability, discovery, active_root="/"):
        return self.cli.UpdateController(
            state_root=self.root / "state",
            cache_root=self.root / "cache",
            keyring=self.keyring,
            key_policy=self.policy,
            current_version="26.3.2",
            architecture="amd64",
            kernel_release="6.12.0-amd64",
            capability_loader=lambda: capability,
            discovery_fetcher=lambda: discovery,
            active_root=active_root,
        )

    def assert_envelope(self, value, command):
        self.assertEqual(value["schema"], "ming.update.cli.v1")
        self.assertEqual(value["command"], command)
        self.assertIsInstance(value["ok"], bool)
        self.assertIn("state", value)
        self.assertIn("action", value)
        self.assertIn("progress", value)
        self.assertIn("timestamp", value)

    def assert_schema(self, value):
        schema = json.loads(CLI_SCHEMA.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_unbootstrapped_2632_returns_only_signed_bootstrap_guidance(self):
        controller = self.controller(
            capability={"available": False, "capability": None},
            discovery={
                "schema": "ming.update.discovery.v1",
                "available": True,
                "current_version": "26.3.2",
                "architecture": "amd64",
                "capability": None,
                "delivery": "bootstrap",
                "bootstrap": {
                    "url": "https://updates.example/objects/bootstrap.deb",
                    "sha256": "a" * 64,
                    "signature_url": "https://updates.example/objects/bootstrap.deb.sig",
                    "fingerprint": "A" * 40,
                },
            },
        )
        value = controller.check()
        self.assert_envelope(value, "check")
        self.assertFalse(value["ok"])
        self.assertEqual(value["error_code"], "E_BOOTSTRAP_REQUIRED")
        self.assertEqual(value["state"], "bootstrap-required")
        self.assertEqual(value["action"], "bootstrap")
        self.assertEqual(value["update"]["delivery"], "bootstrap")
        self.assertIn("bootstrap", value)
        self.assertNotIn("recovery", json.dumps(value).lower())
        self.assertNotIn("iso", json.dumps(value).lower())
        self.assert_schema(value)

    def test_bootstrapped_2632_accepts_only_a_transactional_locator_response(self):
        manifest_hash = hashlib.sha256(b"manifest").hexdigest()
        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={
                "schema": "ming.update.discovery.v1",
                "available": True,
                "current_version": "26.3.2",
                "release_id": "ming-os-26.3.3-amd64-20260715.1",
                "version": "26.3.3",
                "architecture": "amd64",
                "capability": "transactional-slot-v1",
                "delivery": "transactional-slot-v1",
                "minimum_bootstrap": "1.0.0",
                "manifest_url": "https://updates.example/objects/" + manifest_hash,
                "manifest_signature_url": "https://updates.example/objects/" + ("c" * 64),
                "manifest_sha256": manifest_hash,
            },
        )
        value = controller.check()
        self.assert_envelope(value, "check")
        self.assertTrue(value["ok"])
        self.assertEqual(value["state"], "available")
        self.assertEqual(value["action"], "apply")
        self.assertEqual(value["update"]["delivery"], "transactional-slot-v1")
        self.assertEqual(value["update"]["release_id"], "ming-os-26.3.3-amd64-20260715.1")
        self.assertEqual(value["update"]["manifest_sha256"], manifest_hash)
        self.assertTrue((self.root / "cache" / "discovery.json").is_file())
        self.assert_schema(value)

    def test_discovery_runtime_rejects_fields_and_locators_outside_the_frozen_schema(self):
        valid = {
            "schema": "ming.update.discovery.v1",
            "available": True,
            "current_version": "26.3.2",
            "architecture": "amd64",
            "capability": "transactional-slot-v1",
            "delivery": "transactional-slot-v1",
            "release_id": "ming-os-26.3.3-amd64-20260715.1",
            "version": "26.3.3",
            "minimum_bootstrap": "1.0.0",
            "manifest_url": "https://updates.example/objects/" + ("a" * 64),
            "manifest_signature_url": "https://updates.example/objects/" + ("c" * 64),
            "manifest_sha256": "a" * 64,
        }
        cases = (
            ("unknown field", lambda value: value.__setitem__("release_notes", "not in v1")),
            ("query locator", lambda value: value.__setitem__("manifest_url", value["manifest_url"] + "?unsafe=1")),
            ("mutable detached signature locator", lambda value: value.__setitem__(
                "manifest_signature_url", "https://updates.example/releases/26.3.3/manifest.sig",
            )),
            ("manifest locator hash differs from its declared hash", lambda value: value.__setitem__(
                "manifest_url", "https://updates.example/objects/" + ("b" * 64),
            )),
            ("none has bootstrap", lambda value: value.update({
                "available": False,
                "delivery": "none",
                "bootstrap": {
                    "url": "https://updates.example/bootstrap.deb",
                    "sha256": "b" * 64,
                    "signature_url": "https://updates.example/bootstrap.deb.sig",
                    "fingerprint": "A" * 40,
                },
            })),
            ("bootstrap uses legacy fingerprint alias", lambda value: value.clear() or value.update({
                "schema": "ming.update.discovery.v1",
                "available": True,
                "current_version": "26.3.2",
                "architecture": "amd64",
                "capability": None,
                "delivery": "bootstrap",
                "bootstrap": {
                    "url": "https://updates.example/bootstrap.deb",
                    "sha256": "b" * 64,
                    "signature_url": "https://updates.example/bootstrap.deb.sig",
                    "key_fingerprint": "A" * 40,
                },
            })),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                value = json.loads(json.dumps(valid))
                mutate(value)
                with self.assertRaises(self.cli.UpdateError) as caught:
                    self.cli.validate_discovery(value, "amd64", "26.3.2")
                self.assertEqual("E_PROTOCOL_UNSUPPORTED", caught.exception.code)

    def test_bootstrapped_client_accepts_a_signed_contract_no_update_response(self):
        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={
                "schema": "ming.update.discovery.v1",
                "available": False,
                "current_version": "26.3.2",
                "architecture": "amd64",
                "capability": "transactional-slot-v1",
                "delivery": "none",
            },
        )
        value = controller.check()
        self.assert_envelope(value, "check")
        self.assertTrue(value["ok"])
        self.assertEqual(value["state"], "idle")
        self.assertEqual(value["action"], "none")
        self.assertEqual(value["update"]["delivery"], "none")
        self.assertIsNone(value["update"]["available_version"])
        self.assert_schema(value)

    def test_discovery_query_advertises_only_detected_bootstrap_capability(self):
        captured_urls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"schema":"ming.update.discovery.v1"}'

        original_urlopen = self.cli.urllib.request.urlopen
        self.cli.urllib.request.urlopen = lambda url, timeout: (captured_urls.append((url, timeout)) or Response())
        try:
            bootstrapped = self.cli.UpdateController(
                current_version="26.3.2",
                architecture="amd64",
                kernel_release="6.12.0-amd64",
                capability_loader=lambda: {
                    "available": True,
                    "capability": "transactional-slot-v1",
                    "bootstrap_version": "1.0.0",
                },
            )
            bootstrapped._fetch_discovery()
            unbootstrapped = self.cli.UpdateController(
                current_version="26.3.2",
                architecture="amd64",
                kernel_release="6.12.0-amd64",
                capability_loader=lambda: {
                    "available": False,
                    "capability": None,
                    "bootstrap_version": None,
                },
            )
            unbootstrapped._fetch_discovery()
        finally:
            self.cli.urllib.request.urlopen = original_urlopen

        bootstrapped_query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured_urls[0][0]).query)
        self.assertEqual(["transactional-slot-v1"], bootstrapped_query.get("capabilities"))
        self.assertEqual(["1.0.0"], bootstrapped_query.get("bootstrap_version"))
        unbootstrapped_query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured_urls[1][0]).query)
        self.assertNotIn("capabilities", unbootstrapped_query)
        self.assertNotIn("bootstrap_version", unbootstrapped_query)

    def test_discovery_fallback_domain_is_disabled_by_default(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"schema":"ming.update.discovery.v1"}'

        original_urlopen = self.cli.urllib.request.urlopen

        def urlopen(url, timeout):
            calls.append(url)
            raise self.cli.urllib.error.URLError("primary unavailable")

        self.cli.urllib.request.urlopen = urlopen
        try:
            controller = self.cli.UpdateController(
                current_version="26.3.2",
                architecture="amd64",
                kernel_release="6.12.0-amd64",
                capability_loader=lambda: {
                    "available": True,
                    "capability": "transactional-slot-v1",
                    "bootstrap_version": "1.0.0",
                },
            )
            with self.assertRaises(self.cli.UpdateError) as caught:
                controller._fetch_discovery()
        finally:
            self.cli.urllib.request.urlopen = original_urlopen

        self.assertEqual("E_NETWORK", caught.exception.code)
        self.assertEqual(1, len(calls))
        self.assertEqual("ming.scallion.uno", urllib.parse.urlsplit(calls[0]).netloc)

    def test_enabled_discovery_fallback_is_used_only_after_primary_network_failure(self):
        calls = []
        response_body = b'{"schema":"ming.update.discovery.v1"}'

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return response_body

        original_urlopen = self.cli.urllib.request.urlopen

        def urlopen(url, timeout):
            calls.append(url)
            if urllib.parse.urlsplit(url).netloc == "ming.scallion.uno":
                raise self.cli.urllib.error.URLError("primary unavailable")
            return Response()

        self.cli.urllib.request.urlopen = urlopen
        try:
            controller = self.cli.UpdateController(
                current_version="26.3.2",
                architecture="amd64",
                kernel_release="6.12.0-amd64",
                capability_loader=lambda: {
                    "available": True,
                    "capability": "transactional-slot-v1",
                    "bootstrap_version": "1.0.0",
                },
                discovery_fallback_enabled=True,
            )
            value = controller._fetch_discovery()
        finally:
            self.cli.urllib.request.urlopen = original_urlopen

        self.assertEqual({"schema": "ming.update.discovery.v1"}, value)
        self.assertEqual(2, len(calls))
        self.assertEqual("ming.scallion.uno", urllib.parse.urlsplit(calls[0]).netloc)
        self.assertEqual("ming.sca-hub.cn", urllib.parse.urlsplit(calls[1]).netloc)
        self.assertTrue(all(urllib.parse.urlsplit(url).scheme == "https" for url in calls))

    def test_discovery_fallback_is_not_used_for_primary_protocol_failure(self):
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"not-json"

        original_urlopen = self.cli.urllib.request.urlopen
        self.cli.urllib.request.urlopen = lambda url, timeout: (calls.append(url) or Response())
        try:
            controller = self.cli.UpdateController(
                current_version="26.3.2",
                architecture="amd64",
                kernel_release="6.12.0-amd64",
                capability_loader=lambda: {
                    "available": True,
                    "capability": "transactional-slot-v1",
                    "bootstrap_version": "1.0.0",
                },
                discovery_fallback_enabled=True,
            )
            with self.assertRaises(self.cli.UpdateError) as caught:
                controller._fetch_discovery()
        finally:
            self.cli.urllib.request.urlopen = original_urlopen

        self.assertEqual("E_PROTOCOL_UNSUPPORTED", caught.exception.code)
        self.assertEqual(1, len(calls))

    def test_status_returns_most_recent_terminal_transaction_without_active_pointer(self):
        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={},
        )

        def terminal(transaction_id, release_id, transitions):
            state = controller.state_store.create_transaction(
                transaction_id=transaction_id,
                release_id=release_id,
                previous_slot="legacy",
                candidate_slot="A",
            )
            for target, writer in transitions:
                state = controller.state_store.transition(
                    transaction_id,
                    target,
                    writer=writer,
                    expected_generation=state["generation"],
                )
            return state

        old = terminal(
            "tx-old",
            "ming-os-26.3.3-amd64-20260715.1",
            (("aborting", "engine"), ("aborted", "engine")),
        )
        latest = terminal(
            "tx-latest",
            "ming-os-26.3.4-amd64-20260715.1",
            (
                ("verified", "verifier"),
                ("staging", "slot-manager"),
                ("staged", "candidate-applicator"),
                ("armed", "boot-coordinator"),
                ("rollback_armed", "rollback-service"),
                ("rolling_back", "initramfs"),
                ("rolled_back", "rollback-service"),
            ),
        )
        self.assertEqual("aborted", old["state"])
        self.assertEqual("rolled_back", latest["state"])
        self.assertFalse((self.root / "state" / "active-transaction.json").exists())

        value = controller.status()
        self.assert_envelope(value, "status")
        self.assertFalse(value["ok"])
        self.assertEqual("rolled_back", value["state"])
        self.assertEqual("E_ROLLBACK_SLOT", value["error_code"])
        self.assertEqual("tx-latest", value["transaction"]["id"])
        self.assertEqual("none", value["action"])
        self.assert_schema(value)

    def test_status_preserves_each_terminal_state_after_active_pointer_cleanup(self):
        cases = (
            (
                "committed",
                (
                    ("verified", "verifier"),
                    ("staging", "slot-manager"),
                    ("staged", "candidate-applicator"),
                    ("armed", "boot-coordinator"),
                    ("booting", "initramfs"),
                    ("pending_health", "health-service"),
                    ("committing", "health-confirmer"),
                    ("committed", "commit-coordinator"),
                ),
                True,
                None,
            ),
            ("aborted", (("aborting", "engine"), ("aborted", "engine")), True, None),
            (
                "rolled_back",
                (
                    ("verified", "verifier"),
                    ("staging", "slot-manager"),
                    ("staged", "candidate-applicator"),
                    ("armed", "boot-coordinator"),
                    ("rollback_armed", "rollback-service"),
                    ("rolling_back", "initramfs"),
                    ("rolled_back", "rollback-service"),
                ),
                False,
                "E_ROLLBACK_SLOT",
            ),
        )
        for state_name, transitions, expected_ok, expected_error in cases:
            with self.subTest(state=state_name):
                state_root = self.root / "terminal" / state_name
                controller = self.cli.UpdateController(
                    state_root=state_root,
                    cache_root=state_root / "cache",
                    keyring=self.keyring,
                    key_policy=self.policy,
                    current_version="26.3.2",
                    architecture="amd64",
                    kernel_release="6.12.0-amd64",
                    capability_loader=lambda: {
                        "available": True,
                        "capability": "transactional-slot-v1",
                        "bootstrap_version": "1.0.0",
                    },
                    discovery_fetcher=lambda: {},
                )
                transaction_id = "tx-" + state_name
                state = controller.state_store.create_transaction(
                    transaction_id=transaction_id,
                    release_id="ming-os-26.3.3-amd64-20260715.1",
                    previous_slot="legacy",
                    candidate_slot="A",
                )
                for target, writer in transitions:
                    state = controller.state_store.transition(
                        transaction_id,
                        target,
                        writer=writer,
                        expected_generation=state["generation"],
                    )
                self.assertFalse((state_root / "active-transaction.json").exists())
                value = controller.status()
                self.assertEqual(state_name, value["state"])
                self.assertEqual(expected_ok, value["ok"])
                self.assertEqual(expected_error, value["error_code"])
                self.assertEqual(transaction_id, value["transaction"]["id"])
                self.assert_schema(value)

    def test_main_argument_failures_emit_a_schema_valid_json_envelope(self):
        def invoke(arguments):
            stdout = io.StringIO()
            stderr = io.StringIO()
            original_stdout = self.cli.sys.stdout
            original_stderr = self.cli.sys.stderr
            self.cli.sys.stdout = stdout
            self.cli.sys.stderr = stderr
            try:
                exit_code = self.cli.main(arguments)
            finally:
                self.cli.sys.stdout = original_stdout
                self.cli.sys.stderr = original_stderr
            return exit_code, json.loads(stdout.getvalue()), stderr.getvalue()

        missing_command = invoke([])
        missing_apply_value = invoke(["apply", "--json"])
        for exit_code, value, stderr in (missing_command, missing_apply_value):
            self.assertEqual(2, exit_code)
            self.assertEqual("", stderr)
            self.assert_envelope(value, value["command"])
            self.assertFalse(value["ok"])
            self.assertEqual("E_ARGUMENT", value["error_code"])
            self.assert_schema(value)
        self.assertEqual("status", missing_command[1]["command"])
        self.assertEqual("apply", missing_apply_value[1]["command"])

    def test_apply_interface_rejects_caller_paths_and_recovery_dispatch(self):
        arguments = inspect.signature(self.cli.UpdateController.apply).parameters
        for forbidden in ("manifest_path", "manifest_url", "payload_path", "recovery", "iso_path"):
            self.assertNotIn(forbidden, arguments)
        self.assertEqual(tuple(arguments), ("self", "release_id", "manifest_sha256"))
        source = CLI_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("calamares", source)
        self.assertNotIn("mkfs", source)
        self.assertNotIn("resize2fs", source)
        self.assertNotIn("ming-recovery-update", source)

    def test_apply_refuses_space_before_downloading_content_or_payload(self):
        active = self.root / "active"
        (active / "usr" / "share" / "ming-os").mkdir(parents=True)
        (active / "usr" / "share" / "ming-os" / "version").write_text("26.3.2\n", encoding="utf-8")
        release_id = "ming-os-26.3.3-amd64-20260715.4"
        manifest = {
            "schema": "ming.transaction-manifest.v1",
            "release_id": release_id,
            "version": "26.3.3",
            "channel": "stable",
            "architecture": "amd64",
            "delivery": "transactional-slot-v1",
            "from_versions": ["26.3.2"],
            "minimum_bootstrap": "1.0.0",
            "created_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "kernel_release": "6.12.0-amd64",
            "payload": {
                "url": "https://updates.example/objects/payload",
                "sha256": "b" * 64,
                "size": 4096,
                "signature_url": "https://updates.example/objects/payload.sig",
            },
            "content_index": {
                "url": "https://updates.example/objects/index",
                "sha256": "c" * 64,
                "size": 1024,
                "signature_url": "https://updates.example/objects/index.sig",
            },
            "space": {"minimum_free_bytes": 8192, "reserve_bytes": 0},
            "slot_policy": {"maximum_uncommitted_boots": 1, "retain_previous_committed_slots": 1},
            "preserve_paths": ["/home"],
            "health_profile": "ming-core-v1",
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        downloads = []

        def fetcher(url, destination, *_args):
            downloads.append(pathlib.Path(destination).name)
            if pathlib.Path(destination).name == "manifest.json":
                pathlib.Path(destination).write_bytes(manifest_bytes)
            elif pathlib.Path(destination).name == "manifest.json.sig":
                pathlib.Path(destination).write_bytes(b"signature")
            else:
                raise self.cli.UpdateError("E_SPACE", "download must not start before the space gate")
            return pathlib.Path(destination)

        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={
                "schema": "ming.update.discovery.v1",
                "available": True,
                "current_version": "26.3.2",
                "architecture": "amd64",
                "capability": "transactional-slot-v1",
                "delivery": "transactional-slot-v1",
                "release_id": release_id,
                "version": "26.3.3",
                "minimum_bootstrap": "1.0.0",
                "manifest_url": "https://updates.example/objects/" + manifest_hash,
                "manifest_signature_url": "https://updates.example/objects/" + ("c" * 64),
                "manifest_sha256": manifest_hash,
            },
            active_root=active,
        )
        controller.artifact_fetcher = fetcher
        signature_verifier = self.cli.verify_module.verify_detached_signature
        disk_usage = self.cli.shutil.disk_usage
        self.cli.verify_module.verify_detached_signature = lambda *_args, **_kwargs: None
        self.cli.shutil.disk_usage = lambda _path: types.SimpleNamespace(free=1)
        try:
            value = controller.apply(release_id, manifest_hash)
        finally:
            self.cli.verify_module.verify_detached_signature = signature_verifier
            self.cli.shutil.disk_usage = disk_usage
        self.assertFalse(value["ok"])
        self.assertEqual(value["error_code"], "E_SPACE")
        self.assertEqual(downloads, ["manifest.json", "manifest.json.sig"])
        transactions = self.root / "state" / "transactions"
        self.assertFalse(any(transactions.iterdir()))

    def test_apply_binds_the_signed_manifest_to_the_displayed_discovery_identity(self):
        displayed_release = "ming-os-26.3.3-amd64-20260715.5"
        signed_release = "ming-os-26.3.4-amd64-20260715.1"
        manifest_bytes = b"{}"
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        downloads = []

        def fetcher(_url, destination, *_args):
            downloads.append(pathlib.Path(destination).name)
            if pathlib.Path(destination).name == "manifest.json":
                pathlib.Path(destination).write_bytes(manifest_bytes)
            elif pathlib.Path(destination).name == "manifest.json.sig":
                pathlib.Path(destination).write_bytes(b"signature")
            else:
                raise self.cli.UpdateError("E_SPACE", "identity mismatch must stop before artifact download")
            return pathlib.Path(destination)

        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={
                "schema": "ming.update.discovery.v1",
                "available": True,
                "current_version": "26.3.2",
                "architecture": "amd64",
                "capability": "transactional-slot-v1",
                "delivery": "transactional-slot-v1",
                "release_id": displayed_release,
                "version": "26.3.3",
                "minimum_bootstrap": "1.0.0",
                "manifest_url": "https://updates.example/objects/" + manifest_hash,
                "manifest_signature_url": "https://updates.example/objects/" + ("c" * 64),
                "manifest_sha256": manifest_hash,
            },
        )
        controller.artifact_fetcher = fetcher
        signature_verifier = self.cli.verify_module.verify_detached_signature
        manifest_validator = self.cli.verify_module.validate_manifest
        preflight = controller._preflight_download_space
        self.cli.verify_module.verify_detached_signature = lambda *_args, **_kwargs: None
        self.cli.verify_module.validate_manifest = lambda *_args, **_kwargs: {
            "release_id": signed_release,
            "version": "26.3.4",
            "minimum_bootstrap": "1.0.0",
            "payload": {"url": "https://updates.example/objects/payload", "sha256": "b" * 64, "size": 1, "signature_url": "https://updates.example/objects/payload.sig"},
            "content_index": {"url": "https://updates.example/objects/index", "sha256": "c" * 64, "size": 1, "signature_url": "https://updates.example/objects/index.sig"},
            "space": {"minimum_free_bytes": 1, "reserve_bytes": 0},
        }
        controller._preflight_download_space = lambda _manifest: {}
        try:
            value = controller.apply(displayed_release, manifest_hash)
        finally:
            self.cli.verify_module.verify_detached_signature = signature_verifier
            self.cli.verify_module.validate_manifest = manifest_validator
            controller._preflight_download_space = preflight
        self.assertFalse(value["ok"])
        self.assertEqual(value["error_code"], "E_PROTOCOL_UNSUPPORTED")
        self.assertEqual(downloads, ["manifest.json", "manifest.json.sig"])

    def test_apply_uses_signed_artifact_sizes_as_download_hard_limits(self):
        source = CLI_PATH.read_text(encoding="utf-8")
        index_download = source[source.index("index = self._fetch_to("):source.index("payload = self._fetch_to(")]
        payload_start = source.index("payload = self._fetch_to(")
        payload_download = source[payload_start:source.index("available_bytes = shutil.disk_usage", payload_start)]
        self.assertIn('max_bytes=validated_manifest["content_index"]["size"]', index_download)
        self.assertIn('max_bytes=validated_manifest["payload"]["size"]', payload_download)

    def test_cancel_refuses_armed_state_and_logs_returns_only_approved_metadata(self):
        controller = self.controller(capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"}, discovery={})
        transaction = controller.state_store.create_transaction(
            transaction_id="tx-001",
            release_id="ming-os-26.3.3-amd64-20260715.1",
            previous_slot="legacy",
            candidate_slot="A",
        )
        transaction = controller.state_store.transition(
            "tx-001", "verified", writer="verifier", expected_generation=transaction["generation"])
        transaction = controller.state_store.transition(
            "tx-001", "staging", writer="slot-manager", expected_generation=transaction["generation"])
        transaction = controller.state_store.transition(
            "tx-001", "staged", writer="candidate-applicator", expected_generation=transaction["generation"])
        cancelled = controller.cancel("tx-001")
        self.assert_envelope(cancelled, "cancel")
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["state"], "aborted")
        self.assert_schema(cancelled)

        state = controller.state_store.create_transaction(
            transaction_id="tx-002",
            release_id="ming-os-26.3.3-amd64-20260715.2",
            previous_slot="legacy",
            candidate_slot="B",
        )
        for target, writer in (("verified", "verifier"), ("staging", "slot-manager"), ("staged", "candidate-applicator"), ("armed", "boot-coordinator")):
            state = controller.state_store.transition("tx-002", target, writer=writer, expected_generation=state["generation"])
        refused = controller.cancel("tx-002")
        self.assert_envelope(refused, "cancel")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error_code"], "E_NOT_CANCELABLE")

        logs = controller.logs("tx-002")
        self.assert_envelope(logs, "logs")
        self.assertTrue(logs["ok"])
        self.assertNotIn("events.jsonl", json.dumps(logs))
        self.assertIn("log_path", logs)

    def test_doctor_returns_the_same_json_envelope_for_missing_bootstrap(self):
        controller = self.controller(capability={"available": False, "capability": None}, discovery={})
        value = controller.doctor()
        self.assert_envelope(value, "doctor")
        self.assertFalse(value["ok"])
        self.assertEqual(value["error_code"], "E_BOOTSTRAP_REQUIRED")
        self.assertEqual(value["action"], "bootstrap")
        self.assert_schema(value)

    def test_public_envelope_normalizes_engine_sentinels_and_log_paths(self):
        controller = self.controller(
            capability={"available": True, "capability": "transactional-slot-v1", "bootstrap_version": "1.0.0"},
            discovery={},
        )
        available = controller._response("status", ok=True, state="available")
        self.assertIsNone(available["transaction"])
        self.assertEqual(available["update"]["available_version"], None)
        self.assertEqual(available["update"]["release_id"], None)
        self.assertEqual(available["update"]["manifest_sha256"], None)
        self.assertEqual(available["update"]["delivery"], "none")
        self.assertEqual(available["progress"], {"phase": "idle", "percent": 100})
        self.assertIsNone(available["log_path"])
        self.assert_schema(available)

        transaction = controller.state_store.create_transaction(
            transaction_id="tx-003",
            release_id="ming-os-26.3.3-amd64-20260715.3",
            previous_slot="legacy",
            candidate_slot="A",
        )
        rolled_back = controller._response("status", ok=False, state="rolled_back", error_code="E_ROLLBACK_SLOT", transaction={
            "id": transaction["transaction_id"],
            "release_id": transaction["release_id"],
            "previous_slot": transaction["previous_slot"],
            "candidate_slot": transaction["candidate_slot"],
            "generation": transaction["generation"],
        })
        self.assertEqual(rolled_back["progress"], {"phase": "rolled-back", "percent": 100})
        self.assertEqual(rolled_back["log_path"], "/var/log/ming-update/transactions/tx-003/engine.log")
        self.assert_schema(rolled_back)

    def test_privileged_actions_use_a_dedicated_polkit_policy_not_general_sudo(self):
        self.assertTrue(POLICY_PATH.is_file(), "transaction polkit policy is missing")
        policy = POLICY_PATH.read_text(encoding="utf-8")
        self.assertIn('action id="org.mingos.update.transaction"', policy)
        self.assertIn("/usr/local/bin/ming-update", policy)
        self.assertNotIn("NOPASSWD", policy)


if __name__ == "__main__":
    unittest.main()
