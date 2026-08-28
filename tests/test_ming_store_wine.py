import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
UI_PATH = ROOT / "assets" / "ming-store.py"
TOOLBOX_PATH = ROOT / "assets" / "ming-toolbox.py"
CATALOG_PATH = ROOT / "assets" / "ming-store-catalog" / "wine-official.json"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingStoreWineProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load("ming_store_core_wine", CORE_PATH)

    def valid_item(self, **overrides):
        item = {
            "app_id": "notepad-plus-plus-wine",
            "name": "Notepad++（Wine）",
            "package_name": "notepad-plus-plus-wine",
            "version": "8.7.9",
            "architectures": ["amd64"],
            "install_method": "wine-managed",
            "dependencies": [],
            "license": "厂商许可协议",
            "categories": ["办公"],
            "desktop_ids": [],
            "wine_app_id": "notepad-plus-plus-wine",
            "artifact": {
                "url": "https://downloads.example.invalid/notepad.exe",
                "sha256": "a" * 64,
                "filename": "notepad.exe",
                "executable": "drive_c/Program Files/Notepad++/notepad++.exe",
            },
            "identity": {
                "type": "minisign",
                "signature": (
                    "untrusted comment: signature from Ming Wine catalog\n"
                    "RWSAMPLE\n"
                    "trusted comment: Ming Wine manifest notepad-plus-plus-wine 8.7.9\n"
                    "RWSAMPLEGLOBAL\n"
                ),
                "trusted_comment": "Ming Wine manifest notepad-plus-plus-wine 8.7.9",
            },
            "enabled": True,
            "protected": False,
        }
        item.update(overrides)
        return item

    def test_wine_provider_is_registered_and_catalog_has_disabled_safe_entries(self):
        self.assertIn("wine-official", self.core.ALLOWED_PROVIDERS)
        self.assertTrue(CATALOG_PATH.is_file())
        document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual("ming.store.catalog.v1", document["schema"])
        self.assertEqual("wine-official", document["source"]["id"])
        self.assertGreaterEqual(len(document["applications"]), 3)
        provider = self.core.WineOfficialProvider(catalog_root=CATALOG_PATH.parent)
        entries = provider.refresh_catalog()
        self.assertGreaterEqual(len(entries), 3)
        self.assertTrue(all(item["install_method"] == "wine-managed" for item in entries))
        self.assertTrue(any(not item["enabled"] for item in entries))

    def test_wine_provider_requires_fixed_https_artifact_and_manifest_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [self.valid_item()],
            }
            (root / "wine-official.json").write_text(json.dumps(document), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=root, verifier=lambda *_args: True)
            item = provider.resolve("notepad-plus-plus-wine")
            self.assertEqual("wine-managed", item["install_method"])
            self.assertEqual("notepad-plus-plus-wine", item["wine_app_id"])
            self.assertEqual("https", urllib.parse.urlsplit(item["artifact"]["url"]).scheme)
            self.assertEqual("a" * 64, item["artifact"]["sha256"])
            for field, value in (
                ("artifact", {"url": "http://evil.invalid/a.exe", "sha256": "a" * 64,
                              "filename": "a.exe", "executable": "a.exe"}),
                ("identity", {"type": "minisign", "signature": "", "trusted_comment": "x"}),
            ):
                invalid = self.valid_item(**{field: value})
                (root / "wine-official.json").write_text(
                    json.dumps(dict(document, applications=[invalid])), encoding="utf-8")
                with self.assertRaises(self.core.InvalidCatalog):
                    self.core.WineOfficialProvider(
                        catalog_root=root, verifier=lambda *_args: True).refresh_catalog()

    def test_wine_provider_allows_msi_installer_but_requires_exe_launch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [self.valid_item(artifact={
                    "url": "https://downloads.example.invalid/setup.msi",
                    "sha256": "a" * 64,
                    "filename": "setup.msi",
                    "executable": "drive_c/Program Files/Demo/demo.msi",
                })],
            }
            (root / "wine-official.json").write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(self.core.InvalidCatalog):
                self.core.WineOfficialProvider(
                    catalog_root=root, verifier=lambda *_args: True).refresh_catalog()

            document["applications"][0]["artifact"]["executable"] = (
                "drive_c/Program Files/Demo/demo.exe")
            (root / "wine-official.json").write_text(json.dumps(document), encoding="utf-8")
            resolved = self.core.WineOfficialProvider(
                catalog_root=root, verifier=lambda *_args: True).resolve(
                    "notepad-plus-plus-wine")
            self.assertEqual("setup.msi", resolved["artifact"]["filename"])
            self.assertTrue(resolved["artifact"]["executable"].endswith(".exe"))

    def test_wine_provider_rejects_a_forged_signature_even_when_text_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [self.valid_item()],
            }
            (root / "wine-official.json").write_text(json.dumps(document), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=root, verifier=lambda *_args: False)
            with self.assertRaises(self.core.InvalidCatalog):
                provider.refresh_catalog()

    def test_wine_provider_passes_canonical_item_to_the_signature_verifier(self):
        captured = []

        def verifier(payload, signature, trusted_comment, public_key):
            captured.append((payload, signature, trusted_comment, public_key))
            return True

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            item = self.valid_item()
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [item],
            }
            (root / "wine-official.json").write_text(json.dumps(document), encoding="utf-8")
            self.core.WineOfficialProvider(catalog_root=root, verifier=verifier).refresh_catalog()

        self.assertEqual(1, len(captured))
        payload, signature, trusted_comment, public_key = captured[0]
        self.assertNotIn('"signature"', payload)
        self.assertIn(item["wine_app_id"], payload)
        self.assertEqual(item["identity"]["signature"].strip(), signature)
        self.assertEqual(item["identity"]["trusted_comment"], trusted_comment)
        self.assertTrue(str(public_key).endswith("ming-wine-catalog.minisign.pub"))

    def test_minisign_verifier_requires_signed_trusted_comment_to_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            public_key = root / "catalog.pub"
            public_key.write_text("untrusted comment: test\nRWKEY\n", encoding="ascii")

            class Completed:
                returncode = 0
                stdout = "Signature and comment signature verified\nTrusted comment: Ming Wine manifest demo 1.0\n"
                stderr = ""

            provider = self.core.WineOfficialProvider(catalog_root=CATALOG_PATH.parent)
            with mock.patch.object(self.core.subprocess, "run", return_value=Completed()) as run:
                self.assertTrue(provider._verify_signature(
                    "{}", "RWSIG", "Ming Wine manifest demo 1.0", public_key))
                run.assert_called_once()

            class Mismatch:
                returncode = 0
                stdout = "Signature and comment signature verified\nTrusted comment: Ming Wine manifest other 1.0\n"
                stderr = ""

            with mock.patch.object(self.core.subprocess, "run", return_value=Mismatch()):
                self.assertFalse(provider._verify_signature(
                    "{}", "RWSIG", "Ming Wine manifest demo 1.0", public_key))

    def test_disabled_wine_entry_cannot_be_resolved_or_installed(self):
        provider = self.core.WineOfficialProvider(catalog_root=CATALOG_PATH.parent)
        disabled = next(item for item in provider.refresh_catalog() if not item["enabled"])
        with self.assertRaises(self.core.ProviderUnavailable):
            provider.resolve(disabled["app_id"])

    def test_installed_state_does_not_trust_metadata_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            app_dir = home / ".local/share/ming-wine/apps/notepad-plus-plus-wine"
            app_dir.mkdir(parents=True)
            (app_dir / "metadata.json").write_text(json.dumps({
                "app_id": "notepad-plus-plus-wine", "state": "installed",
                "architecture": "win64",
            }), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=CATALOG_PATH.parent, home=home)
            state = provider.installed_state("notepad-plus-plus-wine")
            self.assertFalse(state["installed"])
            self.assertIsNone(state["architecture"])
            self.assertIsNone(state["version"])

    def test_installed_state_requires_controlled_prefix_target_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            app_dir = home / ".local" / "share" / "ming-wine" / "apps" / "notepad-plus-plus-wine"
            prefix = app_dir / "prefix"
            target = prefix / "drive_c" / "Program Files" / "Notepad++" / "notepad++.exe"
            target.parent.mkdir(parents=True)
            prefix.mkdir(exist_ok=True)
            target.write_bytes(b"MZ")
            metadata = {
                "schema": "ming.wine.app.v1", "app_id": "notepad-plus-plus-wine",
                "state": "installed", "version": "8.7.9", "architecture": "win64",
                "prefix": str(prefix), "launch_target": str(target),
            }
            (app_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=CATALOG_PATH.parent, home=home)
            state = provider.installed_state("notepad-plus-plus-wine")
            self.assertTrue(state["installed"])
            self.assertEqual("8.7.9", state["version"])

            metadata["launch_target"] = str(home / "outside.exe")
            (app_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            damaged = provider.installed_state("notepad-plus-plus-wine")
            self.assertFalse(damaged["installed"])
            self.assertEqual("damaged", damaged["state"])

            metadata["launch_target"] = str(target)
            metadata["version"] = "9.9.9"
            (app_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            forged = provider.installed_state("notepad-plus-plus-wine")
            self.assertFalse(forged["installed"])
            self.assertEqual("damaged", forged["state"])

    def test_installed_state_binds_metadata_to_catalog_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            catalog_root = home / "catalog"
            catalog_root.mkdir()
            item = self.valid_item()
            (catalog_root / "wine-official.json").write_text(json.dumps({
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [item],
            }), encoding="utf-8")
            app_dir = home / ".local/share/ming-wine/apps/notepad-plus-plus-wine"
            prefix = app_dir / "prefix"
            target = prefix / "drive_c" / "Program Files" / "Notepad++" / "notepad++.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ")
            metadata = {
                "schema": "ming.wine.app.v1", "app_id": "notepad-plus-plus-wine",
                "catalog_app_id": "notepad-plus-plus-wine", "state": "installed",
                "version": "8.7.9", "architecture": "win64", "prefix": str(prefix),
                "launch_target": str(target), "source_sha256": "a" * 64,
            }
            (app_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=catalog_root, home=home, verifier=lambda *_args: True)
            self.assertTrue(provider.installed_state("notepad-plus-plus-wine")["installed"])

            metadata["source_sha256"] = "b" * 64
            (app_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            forged = provider.installed_state("notepad-plus-plus-wine")
            self.assertFalse(forged["installed"])
            self.assertEqual("damaged", forged["state"])

    def test_installed_state_rejects_symlinked_prefix_metadata_or_launch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            app_root = home / ".local" / "share" / "ming-wine" / "apps"
            app_dir = app_root / "notepad-plus-plus-wine"
            app_dir.mkdir(parents=True)
            outside = home / "outside"
            outside.mkdir()
            try:
                (app_dir / "prefix").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            (app_dir / "metadata.json").write_text(json.dumps({
                "schema": "ming.wine.app.v1", "app_id": "notepad-plus-plus-wine",
                "state": "installed", "version": "8.7.9", "architecture": "win64",
                "prefix": str(app_dir / "prefix"),
                "launch_target": str(app_dir / "prefix" / "demo.exe"),
            }), encoding="utf-8")
            provider = self.core.WineOfficialProvider(
                catalog_root=CATALOG_PATH.parent, home=home)
            state = provider.installed_state("notepad-plus-plus-wine")
            self.assertFalse(state["installed"])
            self.assertEqual("damaged", state["state"])


class MingStoreWineHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load("ming_store_ui_wine", UI_PATH)
        cls.toolbox_source = TOOLBOX_PATH.read_text(encoding="utf-8")
        cls.desktop_source = DESKTOP.read_text(encoding="utf-8")
        cls.build_source = BUILD.read_text(encoding="utf-8")

    def test_store_exposes_wine_source_and_hands_install_to_toolbox(self):
        self.assertIn("wine-official", self.ui.SOURCES)
        self.assertTrue(any("ming-toolbox" in part for part in self.ui.WINE_HANDOFF_COMMAND))
        self.assertIn("--install-wine", self.ui.WINE_HANDOFF_COMMAND)
        self.assertIn('ming-store-catalog/*.json', self.desktop_source)

    def test_wine_handoff_never_places_url_or_command_in_store_transaction(self):
        controller = self.ui.StoreController()
        command = controller.wine_handoff_command("demo-wine")
        self.assertEqual(("/usr/local/bin/ming-toolbox", "--install-wine", "demo-wine"), command)
        self.assertFalse(any("http" in argument or "sh -c" in argument for argument in command))

    def test_wine_handoff_is_pending_and_keeps_a_retry_action(self):
        item = MingStoreWineProviderTests().valid_item()

        class Provider:
            def resolve(self, _app_id):
                return item

        class Registry:
            def get(self, _source_id):
                return Provider()

        class Catalog:
            registry = Registry()

        controller = self.ui.StoreController(
            catalog=Catalog(), process_spawner=lambda *_args, **_kwargs: object())
        controller.live_mode = lambda: False
        result = controller.run_transaction(
            "install", "wine-official", "notepad-plus-plus-wine")
        self.assertFalse(result["ok"])
        self.assertEqual("toolbox_handoff_pending", result["state"])
        presentation = self.ui.operation_presentation(result)
        self.assertEqual("pending", presentation["tone"])
        self.assertEqual("install", presentation["retry_action"])
        self.assertIn("尚未确认", presentation["message"])

    def test_wine_handoff_reads_terminal_receipt_refreshes_catalog_and_desktop(self):
        item = MingStoreWineProviderTests().valid_item()
        refreshes = []

        class Provider:
            def resolve(self, _app_id):
                return item

            def installed_state(self, _app_id):
                return {
                    "installed": True, "version": item["version"],
                    "architecture": "win64", "state": "installed",
                }

            def refresh_catalog(self):
                refreshes.append("catalog")
                return [item]

        class Registry:
            def get(self, _source_id):
                return Provider()

        class Catalog:
            registry = Registry()

        events = []
        completed = threading.Event()

        def spawn(command, **_kwargs):
            self.assertIn("--store-request", command)
            request_id = command[command.index("--store-request") + 1]
            receipt = pathlib.Path(handoff_root) / (request_id + ".json")
            receipt.write_text(json.dumps({
                "schema": "ming.store.wine-handoff-result.v1",
                "request_id": request_id,
                "provider": "wine-official",
                "app_id": item["app_id"],
                "state": "installed",
                "ok": True,
                "refresh_ok": True,
            }), encoding="utf-8")
            return object()

        with tempfile.TemporaryDirectory() as directory:
            handoff_root = pathlib.Path(directory) / "handoffs"
            controller = self.ui.StoreController(
                catalog=Catalog(), process_spawner=spawn,
                handoff_root=handoff_root, handoff_timeout=1,
                handoff_poll_interval=0.01,
                desktop_refresher=lambda: True,
            )
            controller.live_mode = lambda: False

            def progress(event):
                events.append(event)
                if event.get("result", {}).get("state") in {
                        "succeeded", "installed", "refresh_warning", "failed"}:
                    completed.set()

            result = controller.run_transaction(
                "install", "wine-official", item["app_id"],
                progress_callback=progress,
            )
            self.assertEqual("toolbox_handoff_pending", result["state"])
            self.assertTrue(completed.wait(2), events)

        terminal = [event["result"] for event in events if event.get("result")]
        self.assertTrue(any(result.get("ok") for result in terminal), terminal)
        self.assertIn("catalog", refreshes)
        self.assertEqual("succeeded", terminal[-1]["state"])

    def test_wine_handoff_timeout_is_terminal_and_not_permanent_pending(self):
        item = MingStoreWineProviderTests().valid_item()

        class Provider:
            def resolve(self, _app_id):
                return item

        class Registry:
            def get(self, _source_id):
                return Provider()

        class Catalog:
            registry = Registry()

        done = threading.Event()
        terminal = []

        def progress(event):
            if event.get("result", {}).get("state") == "toolbox_handoff_timeout":
                terminal.append(event["result"])
                done.set()

        with tempfile.TemporaryDirectory() as directory:
            controller = self.ui.StoreController(
                catalog=Catalog(), process_spawner=lambda *_args, **_kwargs: object(),
                handoff_root=pathlib.Path(directory), handoff_timeout=0.03,
                handoff_poll_interval=0.005,
            )
            controller.live_mode = lambda: False
            result = controller.run_transaction(
                "install", "wine-official", item["app_id"],
                progress_callback=progress,
            )
            self.assertEqual("toolbox_handoff_pending", result["state"])
            self.assertTrue(done.wait(1), terminal)
        self.assertEqual("toolbox_handoff_timeout", terminal[0]["state"])
        self.assertFalse(terminal[0]["ok"])

    def test_toolbox_wine_receipt_writer_is_atomic_and_identity_bound(self):
        toolbox = load("ming_toolbox_wine_receipt", TOOLBOX_PATH)
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            controller = toolbox.ToolboxController(home=home)
            result = controller.write_wine_handoff_result(
                "a" * 32, "demo-wine", {
                    "ok": True, "state": "installed",
                    "source_id": "wine-official", "refresh_ok": True,
                },
            )
            self.assertTrue(result["ok"])
            receipt = home / ".local/state/ming-os/store/wine-handoffs" / ("a" * 32 + ".json")
            self.assertTrue(receipt.is_file())
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("ming.store.wine-handoff-result.v1", payload["schema"])
            self.assertEqual("demo-wine", payload["app_id"])
            self.assertEqual("installed", payload["state"])
            self.assertTrue(receipt.stat().st_mode & 0o400)
            self.assertFalse(receipt.is_symlink())
            self.assertFalse(any(path.name.startswith(".") for path in receipt.parent.iterdir()))

    def test_toolbox_manifest_rejects_missing_store_handoff_request(self):
        toolbox = load("ming_toolbox_missing_handoff", TOOLBOX_PATH)
        downloader = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            result = toolbox.ToolboxController(
                home=directory, downloader=downloader,
                catalog_root=CATALOG_PATH.parent,
                catalog_verifier=lambda *_args: True,
            ).install_wine_manifest(
                "notepad-plus-plus-wine", request_id="a" * 32)

        self.assertFalse(result["ok"])
        self.assertEqual("handoff_rejected", result["state"])
        downloader.download.assert_not_called()

    def test_toolbox_manifest_consumes_handoff_once_and_binds_version(self):
        toolbox = load("ming_toolbox_handoff_consumption", TOOLBOX_PATH)
        core = load("ming_store_core_handoff_consumption", CORE_PATH)
        item = MingStoreWineProviderTests().valid_item()
        request_id = "b" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "wine-official.json").write_text(json.dumps({
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [item],
            }), encoding="utf-8")
            handoff = root / ".local/state/ming-os/store/wine-handoffs"
            handoff.mkdir(parents=True)
            (handoff / (request_id + ".request.json")).write_text(json.dumps({
                "schema": "ming.store.wine-handoff.v1", "request_id": request_id,
                "uid": int(getattr(os, "getuid", lambda: 1000)()), "action": "install",
                "provider": "wine-official", "app_id": item["app_id"],
                "expected_version": item["version"], "created_at": "2026-08-28T12:00:00Z",
            }), encoding="utf-8")

            class Downloader:
                def __init__(self):
                    self.calls = 0

                def download(self, _url, destination, digest):
                    self.calls += 1
                    pathlib.Path(destination).write_bytes(b"MZ verified")
                    return {"ok": True, "sha256": digest}

            class Installer:
                def __init__(self, home=None):
                    self.home = home

                def install_managed(self, *_args, **_kwargs):
                    return {"ok": True, "state": "installed", "app_id": item["app_id"]}

            downloader = Downloader()
            with mock.patch.object(toolbox.os, "getuid", lambda: 1000, create=True), \
                    mock.patch.object(toolbox, "_load_store_core", return_value=core), \
                    mock.patch.object(toolbox, "_load_wine_module", return_value=type(
                        "Wine", (), {"WineInstaller": Installer})):
                controller = toolbox.ToolboxController(
                    home=root, downloader=downloader, catalog_root=root,
                    catalog_verifier=lambda *_args: True)
                first = controller.install_wine_manifest(
                    item["app_id"], request_id=request_id)
                second = controller.install_wine_manifest(
                    item["app_id"], request_id=request_id)

            self.assertTrue(first["ok"])
            self.assertEqual("installed", first["state"])
            self.assertFalse(second["ok"])
            self.assertEqual("handoff_rejected", second["state"])
            self.assertEqual(1, downloader.calls)
            self.assertTrue((handoff / (request_id + ".request.json.consumed")).is_file())

    def test_toolbox_manifest_rejects_handoff_version_mismatch(self):
        toolbox = load("ming_toolbox_handoff_version", TOOLBOX_PATH)
        item = MingStoreWineProviderTests().valid_item()
        request_id = "c" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "wine-official.json").write_text(json.dumps({
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [item],
            }), encoding="utf-8")
            handoff = root / ".local/state/ming-os/store/wine-handoffs"
            handoff.mkdir(parents=True)
            (handoff / (request_id + ".request.json")).write_text(json.dumps({
                "schema": "ming.store.wine-handoff.v1", "request_id": request_id,
                "uid": 1000, "action": "install",
                "provider": "wine-official", "app_id": item["app_id"],
                "expected_version": "9.9.9", "created_at": "2026-08-28T12:00:00Z",
            }), encoding="utf-8")
            downloader = mock.Mock()
            with mock.patch.object(toolbox.os, "getuid", lambda: 1000, create=True):
                result = toolbox.ToolboxController(
                    home=root, downloader=downloader, catalog_root=root,
                    catalog_verifier=lambda *_args: True,
                ).install_wine_manifest(item["app_id"], request_id=request_id)

        self.assertFalse(result["ok"])
        self.assertEqual("handoff_rejected", result["state"])
        downloader.download.assert_not_called()

    def test_toolbox_manifest_install_uses_secure_downloader_and_wine_installer(self):
        for marker in ("install_wine_manifest", "SecureDownloader", "wine-official", "sha256"):
            self.assertIn(marker, self.toolbox_source)
        self.assertNotIn("shell=True", self.toolbox_source)
        self.assertNotIn("eval ", self.toolbox_source)

    def test_toolbox_downloads_only_catalog_artifact_then_calls_wine_installer(self):
        core = load("ming_store_core_wine_toolbox", CORE_PATH)
        provider_test = MingStoreWineProviderTests()
        item = provider_test.valid_item()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "wine-official.json").write_text(json.dumps({
                "schema": "ming.store.catalog.v1",
                "source": {"id": "wine-official", "name": "Ming Wine", "trust": "minisign"},
                "applications": [item],
            }), encoding="utf-8")
            calls = []

            class Downloader:
                def download(self, url, destination, digest):
                    calls.append((url, pathlib.Path(destination), digest))
                    pathlib.Path(destination).write_bytes(b"MZ verified")
                    return {"ok": True, "sha256": digest}

            class Installer:
                def __init__(self, home=None):
                    self.home = home

                def install(self, source):
                    calls.append(("install", pathlib.Path(source)))
                    return {"ok": True, "state": "installed", "app_id": item["wine_app_id"]}

                def install_managed(self, source, managed_id, launch_target, display_name=None,
                                    version=None, artifact_sha256=None):
                    calls.append(("install", pathlib.Path(source), managed_id, launch_target,
                                  version, artifact_sha256))
                    return {"ok": True, "state": "installed", "app_id": managed_id}

            toolbox = load("ming_toolbox_wine_install", TOOLBOX_PATH)
            with mock.patch.object(toolbox, "_load_wine_module", return_value=type("Wine", (), {"WineInstaller": Installer})):
                result = toolbox.ToolboxController(
                    home=root / "home", downloader=Downloader(), catalog_root=root,
                    catalog_verifier=lambda *_args: True,
                ).install_wine_manifest(item["app_id"])
        self.assertTrue(result["ok"])
        self.assertEqual("wine-official", result["source_id"])
        self.assertEqual(item["artifact"]["url"], calls[0][0])
        self.assertEqual(item["artifact"]["sha256"], calls[0][2])
        self.assertEqual("install", calls[1][0])

    def test_toolbox_never_downloads_disabled_wine_entry(self):
        toolbox = load("ming_toolbox_wine_disabled", TOOLBOX_PATH)
        downloader = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            result = toolbox.ToolboxController(
                home=directory, downloader=downloader,
                catalog_root=CATALOG_PATH.parent,
            ).install_wine_manifest("wechat-wine")
        self.assertFalse(result["ok"])
        self.assertEqual("provider_unavailable", result["state"])
        downloader.download.assert_not_called()

    def test_build_gate_requires_wine_catalog_and_rejects_spark_provider(self):
        self.assertIn('"wine-official"', self.build_source)
        self.assertIn("Wine manifest", self.build_source)
        self.assertIn("WineOfficialProvider", self.build_source)
        self.assertIn("provider.refresh_catalog()", self.build_source)
        self.assertIn("public_key_path", self.build_source)
        self.assertNotIn('for source_id in ["ming-official", "debian-apt", "vendor-official"]:', self.build_source)


if __name__ == "__main__":
    unittest.main()
