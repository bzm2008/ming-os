import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
BUILD_TOOL = ROOT / "tools" / "build-ming-official-debs.sh"
CATALOG_TOOL = ROOT / "tools" / "generate-ming-official-catalog.py"


def load_core():
    spec = importlib.util.spec_from_file_location("ming_store_core_official_tests", CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingOfficialProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_core()

    def test_signed_catalog_requires_release_identity_and_installable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            catalog = {
                "schema": "ming.store.catalog.v1",
                "source": {
                    "id": "ming-official",
                    "name": "Ming 官方软件",
                    "priority": 1,
                    "trust": "minisign-required",
                    "release_tag": "v26.4.1-rc4",
                    "repository": "bzm2008/ming-os",
                    "public_key_fingerprint": "A" * 40,
                },
                "applications": [{
                    "app_id": "ming-store",
                    "name": "Ming 应用商店",
                    "package_name": "ming-store",
                    "version": "26.4.1~rc4",
                    "architectures": ["amd64"],
                    "install_method": "deb",
                    "dependencies": [],
                    "license": "GPL-3.0-or-later",
                    "identity": {"type": "minisign", "signature": "signed", "sha256": "b" * 64},
                    "download_url": "https://github.com/bzm2008/ming-os/releases/download/v26.4.1-rc4/ming-store_26.4.1~rc4_amd64.deb",
                    "sha256": "b" * 64,
                    "release_tag": "v26.4.1-rc4",
                    "enabled": True,
                    "protected": False,
                }],
            }
            (root / "ming-official.json").write_text(json.dumps(catalog), encoding="utf-8")
            (root / "ming-official.json.minisig").write_text("signature", encoding="utf-8")
            (root / "public.key").write_bytes(b"trusted public key")
            (root / "public.key.sha256").write_text(
                __import__("hashlib").sha256(b"trusted public key").hexdigest(), encoding="ascii")
            provider = self.core.MingOfficialProvider(
                catalog_root=root,
                public_key_path=root / "public.key",
                verifier=lambda *_args: True,
            )
            provider.runner = lambda command, timeout=15: (0, "ii \t26.4.1~rc4\tamd64\n", "")
            item = provider.resolve("ming-store")
            self.assertEqual("v26.4.1-rc4", item["release_tag"])
            self.assertEqual("amd64", item["resolved_architecture"])
            self.assertEqual("b" * 64, item["sha256"])
            self.assertEqual("ming-store=26.4.1~rc4", item["apt_target"])
            state = provider.installed_state("ming-store")
            self.assertEqual({
                "installed": True, "version": "26.4.1~rc4", "architecture": "amd64",
            }, state)

    def test_signed_catalog_rejects_non_github_release_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {
                    "id": "ming-official", "name": "Ming", "priority": 1,
                    "trust": "minisign-required", "release_tag": "v26.4.1-rc4",
                    "repository": "bzm2008/ming-os", "public_key_fingerprint": "A" * 40,
                },
                "applications": [{
                    "app_id": "ming-store", "name": "Ming Store", "package_name": "ming-store",
                    "version": "1.0", "architectures": ["amd64"], "install_method": "deb",
                    "dependencies": [], "license": "GPL-3.0-or-later",
                    "identity": {"type": "minisign", "signature": "signed", "sha256": "b" * 64},
                    "download_url": "https://evil.example/ming-store.deb", "sha256": "b" * 64,
                    "release_tag": "v26.4.1-rc4", "enabled": True, "protected": False,
                }],
            }
            (root / "ming-official.json").write_text(json.dumps(document), encoding="utf-8")
            (root / "ming-official.json.minisig").write_text("signature", encoding="utf-8")
            provider = self.core.MingOfficialProvider(
                catalog_root=root,
                public_key_path=root / "public.key",
                verifier=lambda *_args: True,
            )
            with self.assertRaises(self.core.InvalidCatalog):
                provider.refresh_catalog()

    def test_nonempty_catalog_requires_a_public_key_hash_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = {
                "id": "ming-official", "name": "Ming", "priority": 1,
                "trust": "minisign-required", "release_tag": "v26.4.1-rc4",
                "repository": "bzm2008/ming-os", "public_key_fingerprint": "A" * 40,
            }
            item = {
                "app_id": "ming-store", "name": "Ming Store", "package_name": "ming-store",
                "version": "26.4.1~rc4", "architectures": ["amd64"], "install_method": "deb",
                "dependencies": [], "license": "GPL-3.0-or-later",
                "identity": {"type": "minisign", "signature": "signed", "sha256": "b" * 64},
                "download_url": "https://github.com/bzm2008/ming-os/releases/download/v26.4.1-rc4/ming-store_26.4.1~rc4_amd64.deb",
                "sha256": "b" * 64, "release_tag": "v26.4.1-rc4",
                "enabled": True, "protected": False,
            }
            (root / "ming-official.json").write_text(
                json.dumps({"schema": "ming.store.catalog.v1", "source": source, "applications": [item]}),
                encoding="utf-8")
            (root / "ming-official.json.minisig").write_text("signature", encoding="utf-8")
            (root / "public.key").write_bytes(b"unanchored key")
            provider = self.core.MingOfficialProvider(
                catalog_root=root, public_key_path=root / "public.key", verifier=lambda *_args: True)
            with self.assertRaises(self.core.InvalidCatalog):
                provider.refresh_catalog()

    def test_signed_catalog_rejects_filename_not_bound_to_version_and_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            document = {
                "schema": "ming.store.catalog.v1",
                "source": {
                    "id": "ming-official", "name": "Ming", "priority": 1,
                    "trust": "minisign-required", "release_tag": "v26.4.1-rc4",
                    "repository": "bzm2008/ming-os", "public_key_fingerprint": "A" * 40,
                },
                "applications": [{
                    "app_id": "ming-store", "name": "Ming Store", "package_name": "ming-store",
                    "version": "26.4.1~rc4", "architectures": ["amd64"], "install_method": "deb",
                    "dependencies": [], "license": "GPL-3.0-or-later",
                    "identity": {"type": "minisign", "signature": "signed", "sha256": "b" * 64},
                    "download_url": "https://github.com/bzm2008/ming-os/releases/download/v26.4.1-rc4/ming-store_latest_amd64.deb",
                    "sha256": "b" * 64, "release_tag": "v26.4.1-rc4",
                    "enabled": True, "protected": False,
                }],
            }
            (root / "ming-official.json").write_text(json.dumps(document), encoding="utf-8")
            (root / "ming-official.json.minisig").write_text("signature", encoding="utf-8")
            provider = self.core.MingOfficialProvider(
                catalog_root=root, public_key_path=root / "public.key", verifier=lambda *_args: True)
            with self.assertRaises(self.core.InvalidCatalog):
                provider.refresh_catalog()


class MingOfficialToolingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_core()

    def test_package_and_catalog_tools_exist_and_require_explicit_inputs(self):
        self.assertTrue(BUILD_TOOL.is_file())
        self.assertTrue(CATALOG_TOOL.is_file())
        self.assertIn("dpkg-deb", BUILD_TOOL.read_text(encoding="utf-8"))
        self.assertIn("release_tag", CATALOG_TOOL.read_text(encoding="utf-8"))
        self.assertIn('separators=(",", ":")', CATALOG_TOOL.read_text(encoding="utf-8"))

    def test_release_build_requires_external_signed_catalog_inputs(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        for marker in (
            "MING_OFFICIAL_CATALOG_SOURCE",
            "MING_OFFICIAL_CATALOG_SIGNATURE_SOURCE",
            "MING_OFFICIAL_PUBLIC_KEY_SOURCE",
            "ming-official.json.minisig",
        ):
            self.assertIn(marker, build)
        self.assertIn("MING_OFFICIAL_PUBLIC_KEY_SHA256", build)

    def test_runtime_provider_can_bind_the_catalog_key_to_a_ci_supplied_sha256(self):
        self.assertIn("public_key_sha256", self.core.MingOfficialProvider.__init__.__code__.co_varnames)

    def test_release_gate_requires_nonempty_signed_official_catalog(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("Ming official catalog must contain at least one application", build)
        self.assertIn("ming-official-catalog.minisign.pub", build)
        self.assertIn("official_public_key_hash_path.read_text", build)
        self.assertIn("hashlib.sha256(official_public_key_path.read_bytes())", build)


if __name__ == "__main__":
    unittest.main()
