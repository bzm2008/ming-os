import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
UI_PATH = ROOT / "assets" / "ming-store.py"
CATALOG_ROOT = ROOT / "assets" / "ming-store-catalog"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingStoreAssetPresenceTests(unittest.TestCase):
    def test_store_core_asset_exists(self):
        self.assertTrue(CORE_PATH.is_file())


@unittest.skipUnless(CORE_PATH.is_file(), "Ming Store core is not implemented yet")
class MingStoreCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_module("ming_store_core", CORE_PATH)

    def test_all_catalog_documents_use_v1_schema(self):
        documents = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(CATALOG_ROOT.glob("*.json"))
        ]
        self.assertEqual(3, len(documents))
        self.assertEqual(
            {"ming-official", "debian-apt", "vendor-official"},
            {document["source"]["id"] for document in documents},
        )
        self.assertTrue(all(document["schema"] == "ming.store.catalog.v1" for document in documents))

    def test_official_catalog_is_intentionally_empty(self):
        provider = self.core.MingOfficialProvider(catalog_root=CATALOG_ROOT)
        self.assertEqual([], provider.refresh_catalog())

    def test_debian_catalog_contains_only_curated_graphical_apps(self):
        provider = self.core.DebianAptProvider(catalog_root=CATALOG_ROOT)
        packages = {item["package_name"] for item in provider.refresh_catalog()}
        self.assertEqual(
            {
                "vlc", "libreoffice", "gimp", "inkscape", "audacity",
                "thunderbird", "filezilla", "remmina", "obs-studio",
                "qbittorrent", "keepassxc", "flameshot", "simple-scan",
            },
            packages,
        )
        for item in provider.refresh_catalog():
            self.assertEqual("apt", item["install_method"])
            self.assertEqual("candidate", item["version"])
            self.assertEqual(["amd64"], item["architectures"])
            self.assertEqual("apt-repository-signature", item["identity"]["type"])

    def test_vendor_entries_stay_disabled_without_pinned_identity(self):
        provider = self.core.VendorOfficialProvider(catalog_root=CATALOG_ROOT)
        entries = provider.refresh_catalog()
        self.assertEqual({"wechat", "wps", "qq", "dingtalk"}, {item["app_id"] for item in entries})
        for item in entries:
            self.assertFalse(item["enabled"])
            self.assertEqual("identity_not_pinned", item["disabled_reason"])
            self.assertIsNone(item["version"])
            self.assertIsNone(item["identity"]["sha256"])
            self.assertTrue(item["vendor_homepage"].startswith("https://"))
            with self.assertRaises(self.core.ProviderUnavailable):
                provider.resolve(item["app_id"])

    def test_provider_interface_has_required_operations(self):
        required = {"refresh_catalog", "search", "get", "resolve", "installed_state"}
        self.assertTrue(required.issubset(set(dir(self.core.Provider))))

    def test_registry_rejects_spark_provider(self):
        with self.assertRaises(ValueError):
            self.core.ProviderRegistry({"spark": object()})

    def test_catalog_search_merges_and_deduplicates_by_package(self):
        first = self.core.MemoryProvider(
            "ming-official",
            [{"app_id": "vlc-official", "package_name": "vlc", "name": "VLC Official", "enabled": True}],
        )
        second = self.core.MemoryProvider(
            "debian-apt",
            [
                {"app_id": "vlc", "package_name": "vlc", "name": "VLC", "enabled": True},
                {"app_id": "gimp", "package_name": "gimp", "name": "GIMP", "enabled": True},
            ],
        )
        catalog = self.core.StoreCatalog(self.core.ProviderRegistry({
            "ming-official": first,
            "debian-apt": second,
        }))
        results = catalog.search("")
        self.assertEqual(["vlc", "gimp"], [item["package_name"] for item in results])
        self.assertEqual("ming-official", results[0]["source_id"])

    def test_catalog_search_keeps_other_sources_when_one_source_is_unavailable(self):
        class BrokenProvider(self.core.Provider):
            source_id = "ming-official"

            def search(self, query):
                raise self_error

        self_error = self.core.InvalidCatalog("broken signed catalog")
        healthy = self.core.MemoryProvider(
            "debian-apt",
            [{"app_id": "vlc", "package_name": "vlc", "name": "VLC", "enabled": True}],
        )
        catalog = self.core.StoreCatalog(self.core.ProviderRegistry({
            "ming-official": BrokenProvider(),
            "debian-apt": healthy,
        }))
        self.assertEqual(["vlc"], [item["app_id"] for item in catalog.search("")])


@unittest.skipUnless(CORE_PATH.is_file(), "Ming Store core is not implemented yet")
class MingStoreProviderRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_module("ming_store_core_provider", CORE_PATH)

    def test_debian_resolve_reads_exact_candidate_and_architecture(self):
        calls = []

        def runner(command, timeout=15):
            calls.append(tuple(command))
            if command[:2] == ["apt-cache", "policy"]:
                return 0, "  Candidate: 3.0.21-0+deb13u1\n", ""
            return 0, "amd64\n", ""

        provider = self.core.DebianAptProvider(catalog_root=CATALOG_ROOT, runner=runner)
        resolved = provider.resolve("vlc")
        self.assertEqual("3.0.21-0+deb13u1", resolved["resolved_version"])
        self.assertEqual("amd64", resolved["resolved_architecture"])
        self.assertEqual("vlc=3.0.21-0+deb13u1", resolved["apt_target"])
        self.assertEqual(("apt-cache", "policy", "vlc"), calls[0])

    def test_installed_state_reads_dpkg_status_and_version(self):
        def runner(command, timeout=15):
            return 0, "ii \t3.0.21\tamd64\n", ""

        provider = self.core.DebianAptProvider(catalog_root=CATALOG_ROOT, runner=runner)
        state = provider.installed_state("vlc")
        self.assertTrue(state["installed"])
        self.assertEqual("3.0.21", state["version"])
        self.assertEqual("amd64", state["architecture"])

    def test_missing_candidate_is_reported_as_unavailable(self):
        provider = self.core.DebianAptProvider(
            catalog_root=CATALOG_ROOT,
            runner=lambda command, timeout=15: (0, "  Candidate: (none)\n", ""),
        )
        with self.assertRaises(self.core.ProviderUnavailable):
            provider.resolve("vlc")


@unittest.skipUnless(CORE_PATH.is_file(), "Ming Store core is not implemented yet")
class MingStoreTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_module("ming_store_core_tx", CORE_PATH)

    def valid_request(self):
        return {
            "schema": "ming.store.transaction.v1",
            "request_id": "3dd63d98baf54ec0a0f44fdab623bde5",
            "uid": 1000,
            "action": "install",
            "provider": "debian-apt",
            "app_id": "vlc",
            "expected_version": "3.0.21-0+deb13u1",
            "created_at": "2026-08-27T12:00:00Z",
        }

    def test_transaction_request_accepts_only_structured_v1_fields(self):
        request = self.core.StoreTransactionRequest.from_dict(self.valid_request())
        self.assertEqual("vlc", request.app_id)
        self.assertEqual("install", request.action)

    def test_transaction_request_rejects_urls_commands_and_extra_fields(self):
        for key, value in (
            ("url", "https://evil.invalid/a.deb"),
            ("command", "sh -c id"),
            ("arguments", ["--anything"]),
        ):
            payload = self.valid_request()
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(self.core.InvalidTransaction):
                self.core.StoreTransactionRequest.from_dict(payload)

    def test_transaction_request_rejects_spark_and_unsafe_identifiers(self):
        payload = self.valid_request()
        payload["provider"] = "spark"
        with self.assertRaises(self.core.InvalidTransaction):
            self.core.StoreTransactionRequest.from_dict(payload)
        payload = self.valid_request()
        payload["app_id"] = "vlc; sh -c id"
        with self.assertRaises(self.core.InvalidTransaction):
            self.core.StoreTransactionRequest.from_dict(payload)

    def test_state_machine_rejects_skipping_install_readback(self):
        machine = self.core.TransactionStateMachine()
        machine.transition("resolving")
        with self.assertRaises(self.core.InvalidTransition):
            machine.transition("succeeded")

    def test_refresh_warning_is_terminal_but_distinct_from_failure(self):
        machine = self.core.TransactionStateMachine()
        for state in (
            "resolving", "downloading", "verifying", "awaiting_authorization",
            "installing", "readback", "refreshing", "refresh_warning",
        ):
            machine.transition(state)
        self.assertTrue(machine.terminal)
        self.assertEqual("refresh_warning", machine.state)

    def test_jsonl_journal_redacts_secrets_and_machine_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "store.jsonl"
            journal = self.core.TransactionJournal(path)
            journal.write({
                "state": "failed",
                "detail": (
                    "password=hunter2 token=abc123 /home/alice/file "
                    "192.168.1.20 aa:bb:cc:dd:ee:ff"
                ),
            })
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("alice", text)
        self.assertNotIn("192.168.1.20", text)
        self.assertNotIn("aa:bb:cc:dd:ee:ff", text.lower())
        self.assertIn("[REDACTED]", text)

    def test_jsonl_journal_redacts_values_under_sensitive_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "store.jsonl"
            self.core.TransactionJournal(path).write({
                "password": "hunter2",
                "authorization": "Bearer private-token",
                "nested": {"cookie": "session-private"},
            })
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("private-token", text)
        self.assertNotIn("session-private", text)
        self.assertEqual("[REDACTED]", json.loads(text)["password"])


@unittest.skipUnless(CORE_PATH.is_file(), "Ming Store core is not implemented yet")
class MingStoreDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_module("ming_store_core_download", CORE_PATH)

    class Response:
        def __init__(self, payload, status=200, final_url=None):
            self.payload = payload
            self.status = status
            self.offset = 0
            self.final_url = final_url

        def read(self, size=-1):
            if self.offset >= len(self.payload):
                return b""
            if size < 0:
                size = len(self.payload) - self.offset
            chunk = self.payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

        def getcode(self):
            return self.status

        def geturl(self):
            return self.final_url or "https://download.example.invalid/app.deb"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def test_download_retries_three_times_and_verifies_sha256(self):
        payload = b"trusted package bytes"
        attempts = []

        def opener(request, timeout=30):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise OSError("temporary network failure")
            return self.Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "app.deb"
            result = self.core.SecureDownloader(opener=opener, sleeper=lambda _delay: None).download(
                "https://download.example.invalid/app.deb",
                destination,
                hashlib.sha256(payload).hexdigest(),
            )
            self.assertEqual(payload, destination.read_bytes())
        self.assertEqual(3, len(attempts))
        self.assertEqual("verified", result["state"])

    def test_download_resumes_from_partial_file_with_range_header(self):
        payload = b"0123456789"
        seen_range = []

        def opener(request, timeout=30):
            seen_range.append(request.get_header("Range"))
            return self.Response(payload[4:], status=206)

        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "app.deb"
            destination.with_suffix(".deb.part").write_bytes(payload[:4])
            self.core.SecureDownloader(opener=opener).download(
                "https://download.example.invalid/app.deb",
                destination,
                hashlib.sha256(payload).hexdigest(),
            )
            self.assertEqual(payload, destination.read_bytes())
        self.assertEqual(["bytes=4-"], seen_range)

    def test_download_rejects_http_and_hash_mismatch(self):
        downloader = self.core.SecureDownloader(
            opener=lambda request, timeout=30: self.Response(b"wrong")
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "app.deb"
            with self.assertRaises(self.core.DownloadRejected):
                downloader.download(
                    "http://download.example.invalid/app.deb",
                    destination,
                    "0" * 64,
                )
            with self.assertRaises(self.core.IntegrityError):
                downloader.download(
                    "https://download.example.invalid/app.deb",
                    destination,
                    "0" * 64,
                )
            self.assertFalse(destination.exists())

    def test_download_rejects_https_redirected_to_http(self):
        payload = b"package"
        downloader = self.core.SecureDownloader(
            opener=lambda request, timeout=30: self.Response(
                payload, final_url="http://mirror.example.invalid/app.deb"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.core.DownloadRejected):
                downloader.download(
                    "https://download.example.invalid/app.deb",
                    pathlib.Path(directory) / "app.deb",
                    hashlib.sha256(payload).hexdigest(),
                )

    def test_download_rejects_a_preexisting_partial_symlink(self):
        payload = b"package"
        downloader = self.core.SecureDownloader(
            opener=lambda request, timeout=30: self.Response(payload)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            destination = root / "app.deb"
            outside = root / "outside"
            outside.write_bytes(b"do not overwrite")
            partial = destination.with_suffix(".deb.part")
            try:
                partial.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(self.core.DownloadRejected):
                downloader.download(
                    "https://download.example.invalid/app.deb",
                    destination,
                    hashlib.sha256(payload).hexdigest(),
                )
            self.assertEqual(b"do not overwrite", outside.read_bytes())


class MingStoreUiPresenceTests(unittest.TestCase):
    def test_store_ui_asset_exists(self):
        self.assertTrue(UI_PATH.is_file())


@unittest.skipUnless(UI_PATH.is_file() and CORE_PATH.is_file(), "Ming Store UI is not implemented yet")
class MingStoreUiLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load_module("ming_store_ui", UI_PATH)

    def test_navigation_and_ming_mint_style_are_explicit(self):
        self.assertEqual(
            ("home", "categories", "installed", "updates", "failures"),
            self.ui.NAVIGATION,
        )
        self.assertIn("Ming 应用商店", self.ui.APP_NAME)
        self.assertIn("#1f8a70", self.ui.MING_MINT_CSS.lower())

    def test_controller_search_and_source_filter(self):
        class Catalog:
            def search(self, query, source_id=None):
                return [{"name": query, "source_id": source_id}]

        result = self.ui.StoreController(catalog=Catalog()).search("VLC", "debian-apt")
        self.assertEqual("VLC", result[0]["name"])
        self.assertEqual("debian-apt", result[0]["source_id"])

    def test_controller_groups_categories_and_reads_installed_state(self):
        class Catalog:
            def search(self, query, source_id=None):
                return [
                    {
                        "app_id": "vlc", "name": "VLC", "source_id": "debian-apt",
                        "categories": ["影音"],
                    },
                    {
                        "app_id": "gimp", "name": "GIMP", "source_id": "debian-apt",
                        "categories": ["图像"],
                    },
                ]

            def installed_state(self, source_id, app_id):
                return {
                    "installed": app_id == "vlc",
                    "version": "3.0" if app_id == "vlc" else None,
                    "architecture": "amd64" if app_id == "vlc" else None,
                }

        controller = self.ui.StoreController(catalog=Catalog())
        self.assertEqual({"影音", "图像"}, set(controller.categories()))
        installed = controller.installed_apps()
        self.assertEqual(["vlc"], [item["app_id"] for item in installed])
        self.assertEqual("3.0", installed[0]["installed_version"])

    def test_inventory_attaches_real_state_without_losing_unavailable_items(self):
        class Catalog:
            def search(self, query, source_id=None):
                return [
                    {"app_id": "vlc", "source_id": "debian-apt"},
                    {"app_id": "gimp", "source_id": "debian-apt"},
                ]

            def installed_state(self, source_id, app_id):
                if app_id == "gimp":
                    raise RuntimeError("dpkg busy")
                return {"installed": True, "version": "3.0", "architecture": "amd64"}

        inventory = self.ui.StoreController(catalog=Catalog()).inventory("")
        self.assertTrue(inventory[0]["_installed_state"]["installed"])
        self.assertEqual("3.0", inventory[0]["_installed_state"]["version"])
        self.assertFalse(inventory[1]["_installed_state"]["installed"])
        self.assertEqual("status_unavailable", inventory[1]["_installed_state"]["state"])

    def test_controller_reads_only_failed_journal_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "store.jsonl"
            path.write_text(
                '{"state":"succeeded","app_id":"vlc"}\n'
                '{"state":"failed","app_id":"gimp","error":"network"}\n'
                'not-json\n',
                encoding="utf-8",
            )
            failures = self.ui.StoreController.failure_records(path)
        self.assertEqual(1, len(failures))
        self.assertEqual("gimp", failures[0]["app_id"])

    def test_local_deb_is_inspected_but_never_executed(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "demo.deb"
            source.write_bytes(b"local deb")
            controller = self.ui.StoreController(
                catalog=object(),
                deb_inspector=lambda path: calls.append(path) or {
                    "package": "demo", "version": "1.0", "architecture": "amd64"
                },
            )
            result = controller.local_deb_detail(source)
        self.assertEqual("review_required", result["state"])
        self.assertEqual("demo", result["package"])
        self.assertEqual(1, len(calls))
        self.assertNotIn("command", result)

    def test_local_deb_rejects_urls_symlinks_and_non_deb_files(self):
        controller = self.ui.StoreController(catalog=object(), deb_inspector=lambda path: {})
        with self.assertRaises(ValueError):
            controller.local_deb_detail("https://example.invalid/app.deb")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            text = root / "app.txt"
            text.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                controller.local_deb_detail(text)
            target = root / "target.deb"
            target.write_bytes(b"x")
            link = root / "link.deb"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(ValueError):
                controller.local_deb_detail(link)

    def test_authorization_command_is_structured_and_contains_no_shell(self):
        command = self.ui.StoreController.authorization_command(
            "install", "3dd63d98baf54ec0a0f44fdab623bde5"
        )
        self.assertEqual(
            ["/usr/local/bin/ming-authorized-action", "store", "install", "3dd63d98baf54ec0a0f44fdab623bde5"],
            command,
        )
        self.assertNotIn("sh", command)

    def test_missing_gtk_has_a_readable_status(self):
        status = self.ui.gtk_dependency_status(importer=lambda _name: (_ for _ in ()).throw(ImportError()))
        self.assertFalse(status["ok"])
        self.assertEqual("gtk_unavailable", status["state"])
        self.assertIn("图形", status["error"])

    def test_transaction_phase_labels_cover_each_user_visible_stage(self):
        self.assertEqual(
            {
                "resolving", "downloading", "verifying", "awaiting_authorization",
                "installing", "readback", "refreshing", "succeeded",
                "refresh_warning", "failed",
            },
            set(self.ui.TRANSACTION_PHASE_LABELS),
        )
        self.assertIn("校验", self.ui.TRANSACTION_PHASE_LABELS["verifying"])
        self.assertIn("授权", self.ui.TRANSACTION_PHASE_LABELS["awaiting_authorization"])

    def test_operation_presentation_never_turns_refresh_warning_into_full_success(self):
        warning = self.ui.operation_presentation({
            "ok": True, "state": "refresh_warning",
            "message": "软件已安装，但桌面入口刷新失败。",
        })
        self.assertEqual("warning", warning["tone"])
        self.assertEqual("重试刷新", warning["retry_label"])
        self.assertEqual("refresh", warning["retry_action"])
        failed = self.ui.operation_presentation({"ok": False, "state": "network_failed"})
        self.assertEqual("error", failed["tone"])
        self.assertEqual("重试", failed["retry_label"])

    def test_responsive_layout_collapses_sidebar_below_700_pixels(self):
        self.assertEqual("compact", self.ui.layout_mode(699))
        self.assertEqual("wide", self.ui.layout_mode(700))

    def test_packagekit_is_query_only_and_requires_daemon_and_aptcc(self):
        existing = {
            "/usr/libexec/packagekitd",
            "/usr/lib/packagekit-backend/libpk_backend_aptcc.so",
        }
        ready = self.ui.packagekit_capability(
            which=lambda name: "/usr/bin/pkcon" if name == "pkcon" else None,
            exists=lambda path: path in existing,
        )
        self.assertTrue(ready["available"])
        self.assertEqual("query_only", ready["mode"])
        self.assertTrue(ready["root_re_resolve_required"])
        missing = self.ui.packagekit_capability(
            which=lambda _name: "/usr/bin/pkcon",
            exists=lambda _path: False,
        )
        self.assertFalse(missing["available"])
        self.assertEqual("apt_provider", missing["fallback"])
        wrong_backend = self.ui.packagekit_capability(
            which=lambda _name: "/usr/bin/pkcon",
            exists=lambda path: path in {
                "/usr/libexec/packagekitd",
                "/usr/lib/packagekit-backend/libpk_backend_apt.so",
            },
        )
        self.assertFalse(wrong_backend["available"])

    def test_controller_lists_updates_from_exact_provider_candidates(self):
        class Provider:
            def get(self, app_id):
                return {"app_id": app_id, "package_name": app_id}

            def resolve(self, app_id):
                return {"resolved_version": "2.0" if app_id == "vlc" else "1.0"}

        class Registry:
            def get(self, _source_id):
                return Provider()

        class Catalog:
            registry = Registry()

            def search(self, query, source_id=None):
                return [
                    {"app_id": "vlc", "source_id": "debian-apt", "enabled": True},
                    {"app_id": "gimp", "source_id": "debian-apt", "enabled": True},
                ]

            def installed_state(self, source_id, app_id):
                return {"installed": True, "version": "1.0", "architecture": "amd64"}

        controller = self.ui.StoreController(
            catalog=Catalog(), version_comparator=lambda installed, candidate: installed != candidate,
        )
        updates = controller.available_updates()
        self.assertEqual(["vlc"], [item["app_id"] for item in updates])
        self.assertEqual("1.0", updates[0]["installed_version"])
        self.assertEqual("2.0", updates[0]["available_version"])

    def test_failure_retry_uses_refresh_for_refresh_warning(self):
        controller = self.ui.StoreController(catalog=object())
        calls = []
        controller.run_transaction = lambda action, source, app: (
            calls.append((action, source, app)) or {"ok": True, "state": "succeeded"}
        )
        result = controller.retry_failure({
            "state": "refresh_warning", "action": "install",
            "provider": "debian-apt", "app_id": "vlc",
        })
        self.assertTrue(result["ok"])
        self.assertEqual([("refresh", "debian-apt", "vlc")], calls)
        with self.assertRaises(ValueError):
            controller.retry_failure({
                "state": "failed", "action": "sh -c", "provider": "debian-apt", "app_id": "vlc",
            })

    def test_diagnostic_upload_requires_explicit_confirmation(self):
        controller = self.ui.StoreController(catalog=object())
        self.assertEqual(
            ["/usr/local/bin/ming-diagnostic-bundle"],
            controller.diagnostic_command(upload=False),
        )
        with self.assertRaises(PermissionError):
            controller.diagnostic_command(upload=True, confirmed=False)
        self.assertEqual(
            ["/usr/local/bin/ming-diagnostic-upload"],
            controller.diagnostic_command(upload=True, confirmed=True),
        )

    def test_diagnostic_result_is_redacted_before_display(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": "bundle ready password=hunter2 token=private",
            "stderr": "",
        })()
        controller = self.ui.StoreController(
            catalog=object(), command_runner=lambda *args, **kwargs: completed,
        )
        result = controller.run_diagnostic()
        self.assertTrue(result["ok"])
        self.assertNotIn("hunter2", result["message"])
        self.assertNotIn("private", result["message"])

    def test_log_reader_limits_records_and_uses_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "store.jsonl"
            path.write_text(
                "".join(
                    json.dumps({"state": "failed", "detail": "password=secret", "index": index}) + "\n"
                    for index in range(130)
                ),
                encoding="utf-8",
            )
            records = self.ui.StoreController.read_log(path, limit=20)
        self.assertEqual(20, len(records))
        self.assertEqual(129, records[-1]["index"])
        self.assertNotIn("secret", json.dumps(records))

    def test_background_transaction_reports_progress_and_runtime_failure(self):
        class Catalog:
            class Registry:
                def get(self, _source_id):
                    return type("Provider", (), {
                        "get": lambda self, _app: {"version": "1.0"},
                        "resolve": lambda self, _app: {"resolved_version": "1.0"},
                    })()
            registry = Registry()

        progress = []
        controller = self.ui.StoreController(
            catalog=Catalog(),
            command_runner=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing helper")),
        )
        controller.live_mode = lambda: False
        with tempfile.TemporaryDirectory() as directory:
            controller.create_transaction = lambda *args, **kwargs: {
                "request_id": "a" * 32,
            }
            controller.result_journal_path = lambda: pathlib.Path(directory) / "result.jsonl"
            result = controller.run_transaction(
                "install", "debian-apt", "vlc", progress_callback=progress.append,
            )
        self.assertFalse(result["ok"])
        self.assertEqual("runtime_missing", result["state"])
        self.assertEqual("awaiting_authorization", progress[0]["state"])
        self.assertEqual("failed", progress[-1]["state"])

    def test_journal_write_failure_does_not_hide_successful_transaction(self):
        class Catalog:
            class Registry:
                def get(self, _source_id):
                    return type("Provider", (), {
                        "get": lambda self, _app: {"version": "1.0"},
                        "resolve": lambda self, _app: {"resolved_version": "1.0"},
                    })()
            registry = Registry()

        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": '{"ok":true,"state":"succeeded","message":"done"}',
            "stderr": "",
        })()
        controller = self.ui.StoreController(
            catalog=Catalog(), command_runner=lambda *args, **kwargs: completed,
        )
        controller.live_mode = lambda: False
        controller.create_transaction = lambda *args, **kwargs: {"request_id": "a" * 32}

        class BrokenJournal:
            def write(self, _result):
                raise OSError("disk full")

        controller.core.TransactionJournal = lambda _path: BrokenJournal()
        result = controller.run_transaction("install", "debian-apt", "vlc")
        self.assertTrue(result["ok"])
        self.assertEqual("succeeded", result["state"])

    def test_gtk_source_wires_navigation_breakpoint_detail_and_diagnostics(self):
        source = UI_PATH.read_text(encoding="utf-8")
        self.assertIn('sidebar.connect("row-selected"', source)
        self.assertIn("Adw.Breakpoint", source)
        self.assertIn("GLib.idle_add", source)
        self.assertIn("build_detail", source)
        self.assertIn("show_failures", source)
        self.assertIn("confirm_diagnostic_upload", source)
        self.assertIn("load_page_async", source)
        self.assertIn("load_local_deb_async", source)
        self.assertIn("page_generation", source)


if __name__ == "__main__":
    unittest.main()
