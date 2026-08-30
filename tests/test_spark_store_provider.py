import email.utils
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
import urllib.parse
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
UI_PATH = ROOT / "assets" / "ming-store.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SparkPublicProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load("ming_store_core_spark_public", CORE_PATH)
        cls.ui = load("ming_store_ui_spark_public", UI_PATH)
        # Keep catalog fixtures deterministic.  Production still uses the
        # real clock; only this test module injects a clock after the fixture
        # release date so the expiry policy is exercised reproducibly.
        cls.test_release_date = "Thu, 28 Aug 2026 00:00:00 +0000"
        cls.test_now = email.utils.parsedate_to_datetime(
            cls.test_release_date).timestamp() + 60
        cls._clock_patch = mock.patch.object(
            cls.core.time, "time", return_value=cls.test_now)
        cls._clock_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._clock_patch.stop()
        super().tearDownClass()

    def _packages(self, filename="./tools/demo/demo_1.2.3_amd64.deb"):
        return """Package: demo
Architecture: amd64
Version: 1.2.3
Filename: {filename}
Size: 12
SHA256: {sha256}
SHA512: {sha512}
Depends: libc6 (>= 2.34), wine
Description: Demo package

""".format(
            filename=filename,
            sha256="a" * 64,
            sha512="b" * 128,
        )

    def _inrelease(self, packages, date=None):
        date = date or self.test_release_date
        digest = hashlib.sha256(packages.encode()).hexdigest()
        return """-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Date: {date}
SHA256:
 {digest} {size} Packages
-----BEGIN PGP SIGNATURE-----
signature
-----END PGP SIGNATURE-----
""".format(date=date, digest=digest, size=len(packages.encode()))

    def _policy(self, root, enabled):
        path = pathlib.Path(root) / "spark-public.json"
        path.write_text(json.dumps({
            "schema": "ming.store.spark-public.v1",
            "provider": "spark-public",
            "base_url": "https://cdn.d.store.deepinos.org.cn",
            "keyring": "/etc/ming-os/store/spark-archive-keyring.gpg",
            "key_fingerprint": self.core.SPARK_KEY_FINGERPRINT,
            "installation_enabled": bool(enabled),
            "installation_disabled_reason": (
                "未独立配置可信公钥，当前目录仅供浏览，不能安装。"
            ),
        }), encoding="utf-8")
        return path

    @staticmethod
    def _applist(tags="native", version="1.2.3"):
        return [{
            "Name": "Demo",
            "Version": version,
            "Filename": "demo_%s_amd64.deb" % version,
            "Pkgname": "demo",
            "Tags": tags,
            "Website": "https://example.invalid/demo",
            "More": "description",
            "icons": "https://spk-json.spark-app.store/icons/demo.png",
        }]

    def test_public_provider_maps_spark_json_to_signed_package_record(self):
        packages = self._packages()
        applist = [{
            "Name": "Demo Wine",
            "Version": "1.2.3",
            "Filename": "demo_1.2.3_amd64.deb",
            "Pkgname": "demo",
            "Tags": "native",
            "Website": "https://example.invalid/demo",
            "More": "description",
            "icons": "https://spk-json.spark-app.store/icons/demo.png",
        }]
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(applist),
        }

        def fetch(url, _headers=None):
            return {"status": 200, "headers": {}, "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            provider = self.core.SparkPublicProvider(
                cache_root=pathlib.Path(directory),
                categories=("tools",),
                fetcher=fetch,
                release_verifier=lambda *_args: True,
                keyring_path=pathlib.Path(directory) / "spark.gpg",
                config_path=self._policy(directory, True),
            )
            items = provider.refresh_catalog()

        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("spark-public", item["source_id"])
        self.assertEqual("spark-deb", item["install_method"])
        self.assertEqual("demo", item["package_name"])
        self.assertEqual("a" * 64, item["identity"]["sha256"])
        self.assertTrue(item["enabled"])
        self.assertEqual("amd64", item["resolved_architecture"])
        self.assertEqual(
            "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
            item["download_url"],
        )

    def test_provider_verifies_packages_using_original_bytes_with_invalid_utf8(self):
        """The live index contains a non-UTF-8 byte; hash the wire bytes first."""
        packages = self._packages().replace(
            "Description: Demo package", "Description: Demo package\n .\xb8"
        ).encode("latin-1")
        digest = hashlib.sha256(packages).hexdigest()
        inrelease = """-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Date: {date}
SHA256:
 {digest} {size} Packages
-----BEGIN PGP SIGNATURE-----
signature
-----END PGP SIGNATURE-----
""".format(date=self.test_release_date, digest=digest, size=len(packages))
        responses = {
            "/store/InRelease": inrelease,
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def fetch(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            provider = self.core.SparkPublicProvider(
                cache_root=pathlib.Path(directory), categories=("tools",),
                fetcher=fetch, release_verifier=lambda *_args: True,
                config_path=self._policy(directory, True),
                clock=lambda: self.test_now,
            )
            items = provider.refresh_catalog()

        self.assertEqual(1, len(items))
        self.assertTrue(items[0]["enabled"])
        self.assertEqual(digest, provider.index_digest)

    def test_invalid_utf8_packages_survive_cache_and_offline_browse(self):
        """A successful binary index must remain readable after a network loss."""
        packages = self._packages().replace(
            "Description: Demo package", "Description: Demo package\n .\xb8"
        ).encode("latin-1")
        digest = hashlib.sha256(packages).hexdigest()
        responses = {
            "/store/InRelease": self._inrelease(
                packages.decode("latin-1")
            ).replace(
                hashlib.sha256(packages.decode("latin-1").encode()).hexdigest(), digest
            ),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {"ETag": "v1"},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            writer = self.core.SparkPublicProvider(
                cache_root=root, categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
                clock=lambda: self.test_now,
            )
            writer.refresh_catalog()
            writer.fetcher = lambda *_args: (_ for _ in ()).throw(OSError("offline"))
            cached = writer.refresh_catalog()

        self.assertEqual(1, len(cached))
        self.assertEqual("stale", writer.catalog_state)
        self.assertFalse(cached[0]["enabled"])

    def test_public_provider_rejects_catalog_path_injection_and_unmatched_package(self):
        packages = self._packages(filename="./tools/demo/demo_1.2.3_amd64.deb")
        applist = [{
            "Name": "Bad",
            "Version": "1.2.3",
            "Filename": "../../evil.deb",
            "Pkgname": "demo",
            "Tags": "native",
        }, {
            "Name": "Unknown",
            "Version": "9.9.9",
            "Filename": "unknown_9.9.9_amd64.deb",
            "Pkgname": "unknown",
            "Tags": "native",
        }]
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(applist),
        }

        def fetch(url, _headers=None):
            return {"status": 200, "headers": {}, "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            provider = self.core.SparkPublicProvider(
                cache_root=pathlib.Path(directory), categories=("tools",),
                fetcher=fetch, release_verifier=lambda *_args: True,
                keyring_path=pathlib.Path(directory) / "spark.gpg",
                config_path=self._policy(directory, True),
            )
            items = provider.refresh_catalog()

        self.assertEqual(2, len(items))
        self.assertFalse(items[0]["enabled"])
        self.assertIn("路径", items[0]["disabled_reason"])
        self.assertFalse(items[1]["enabled"])
        self.assertIn("签名索引", items[1]["disabled_reason"])

    def test_signed_package_index_rejects_noncanonical_filenames(self):
        """Packages is also an untrusted input until every path is canonical."""
        records = self.core.parse_spark_packages(self._packages(
            filename="./tools/demo/../demo/demo_1.2.3_amd64.deb"))
        self.assertEqual({}, self.core.SparkPublicProvider._record_index(records))

        for filename in (
                "/tools/demo/demo_1.2.3_amd64.deb",
                "tools//demo/demo_1.2.3_amd64.deb",
                "tools/demo//demo_1.2.3_amd64.deb",
                "tools/demo/./demo_1.2.3_amd64.deb",
                "tools/demo/demo_1.2.3_amd64.deb/"):
            with self.subTest(filename=filename):
                records = self.core.parse_spark_packages(self._packages(filename=filename))
                self.assertEqual({}, self.core.SparkPublicProvider._record_index(records))

    def test_catalog_refresh_retries_transient_directory_request(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }
        attempts = {"catalog": 0}

        def fetch(url, _headers=None):
            path = urllib.parse.urlsplit(url).path
            if path == "/store/tools/applist.json":
                attempts["catalog"] += 1
                if attempts["catalog"] < 3:
                    raise OSError("transient reset")
            return {"status": 200, "headers": {}, "body": responses[path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
            )
            items = provider.refresh_catalog()

        self.assertEqual(3, attempts["catalog"])
        self.assertEqual(1, len(items))
        self.assertEqual("ready", provider.catalog_state)

    def test_catalog_refresh_retries_transient_http_status(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }
        attempts = {"catalog": 0}

        def fetch(url, _headers=None):
            path = urllib.parse.urlsplit(url).path
            if path == "/store/tools/applist.json":
                attempts["catalog"] += 1
                if attempts["catalog"] < 3:
                    return {"status": 503, "headers": {}, "body": "temporary"}
            return {"status": 200, "headers": {}, "body": responses[path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
            )
            items = provider.refresh_catalog()

        self.assertEqual(3, attempts["catalog"])
        self.assertEqual(1, len(items))
        self.assertEqual("ready", provider.catalog_state)

    def test_public_provider_uses_last_good_cache_after_network_failure(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps([{
                "Name": "Demo", "Version": "1.2.3",
                "Filename": "demo_1.2.3_amd64.deb", "Pkgname": "demo",
                "Tags": "native",
            }]),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {"ETag": "v1"}, "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root, categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                keyring_path=root / "spark.gpg",
                config_path=self._policy(root, True),
            )
            self.assertEqual(1, len(provider.refresh_catalog()))

            def offline(_url, _headers=None):
                raise OSError("network down")

            provider.fetcher = offline
            cached = provider.refresh_catalog()

        self.assertEqual(1, len(cached))
        self.assertEqual("stale", provider.catalog_state)

    def test_cached_catalog_is_browsable_but_not_an_install_trust_boundary(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps([{
                "Name": "Demo", "Version": "1.2.3",
                "Filename": "demo_1.2.3_amd64.deb", "Pkgname": "demo",
                "Tags": "native",
            }]),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {}, "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            writer = self.core.SparkPublicProvider(
                cache_root=root, categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                keyring_path=root / "spark.gpg",
                config_path=self._policy(root, True),
            )
            writer.refresh_catalog()
            reader = self.core.SparkPublicProvider(
                cache_root=root, categories=("tools",),
                config_path=root / "spark-public.json",
            )
            cached_items = reader.search("Demo")
            self.assertEqual(1, len(cached_items))
            self.assertFalse(cached_items[0]["enabled"])
            self.assertIn("缓存", cached_items[0]["disabled_reason"])
            with self.assertRaises(self.core.ProviderUnavailable):
                reader.resolve("spark-demo")

    def test_cache_root_symlink_is_rejected_before_reading_cached_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / "outside"
            outside.mkdir()
            cache = root / "cache"
            try:
                cache.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            provider = self.core.SparkPublicProvider(cache_root=cache)
            with self.assertRaises(self.core.ProviderUnavailable) as caught:
                provider._load_cache()
            self.assertIn("符号链接", str(caught.exception))

    def test_release_verifier_uses_only_exact_validsig_status_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            keyring = root / "spark.gpg"
            keyring.write_bytes(b"keyring")
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", keyring_path=keyring,
            )

            class Completed:
                returncode = 0
                stdout = "[GNUPG:] GOODSIG ABC Spark\n"
                stderr = "gpgv: Good signature from %s" % self.core.SPARK_KEY_FINGERPRINT

            with mock.patch.object(self.core.shutil, "which", return_value="gpgv"), \
                    mock.patch.object(self.core.subprocess, "run", return_value=Completed()) as run:
                self.assertFalse(provider._verify_inrelease("signed"))
                command = run.call_args.args[0]
                self.assertIn("--status-fd=1", command)

            class Fingerprinted:
                returncode = 0
                stdout = (
                    "[GNUPG:] VALIDSIG %s 2026-08-28 1787875200 0 4 0 1 10 01\n"
                    % self.core.SPARK_KEY_FINGERPRINT
                )
                stderr = ""

            with mock.patch.object(self.core.shutil, "which", return_value="gpgv"), \
                    mock.patch.object(self.core.subprocess, "run", return_value=Fingerprinted()):
                self.assertTrue(provider._verify_inrelease("signed"))

            class Subkey:
                returncode = 0
                stdout = (
                    "[GNUPG:] VALIDSIG %s 2026-08-28 1787875200 0 4 0 1 10 01 %s\n"
                    % ("A" * 40, self.core.SPARK_KEY_FINGERPRINT)
                )
                stderr = ""

            with mock.patch.object(self.core.shutil, "which", return_value="gpgv"), \
                    mock.patch.object(self.core.subprocess, "run", return_value=Subkey()):
                self.assertTrue(provider._verify_inrelease("signed"))

    def test_policy_false_fetches_public_json_but_forces_browse_only_without_keyring(self):
        requested = []

        def fetch(url, _headers=None):
            path = urllib.parse.urlsplit(url).path
            requested.append(path)
            if path != "/store/tools/applist.json":
                raise AssertionError("browse-only refresh must not fetch signed install indexes")
            return {"status": 200, "headers": {"ETag": "apps-v1"},
                    "body": json.dumps(self._applist())}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                keyring_path=root / "missing.gpg",
                config_path=self._policy(root, False),
            )
            items = provider.refresh_catalog()

        self.assertEqual(["/store/tools/applist.json"], requested)
        self.assertEqual("browse-only", provider.catalog_state)
        self.assertEqual(1, len(items))
        self.assertFalse(items[0]["enabled"])
        self.assertIn("仅供浏览", items[0]["disabled_reason"])
        with self.assertRaises(self.core.ProviderUnavailable):
            provider.resolve("spark-demo")

    def test_spark_wine_tag_has_a_clear_toolbox_compatibility_status(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist(tags="debian;dwine5")),
        }

        def fetch(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
            )
            item = provider.refresh_catalog()[0]

        self.assertEqual("spark-wine-deb", item["install_method"])
        self.assertFalse(item["enabled"])
        self.assertRegex(item["disabled_reason"], "兼容声明|工具箱")
        self.assertEqual("toolbox_required", item["compatibility_state"])
        self.assertIn("工具箱", item["compatibility_reason"])

    def test_etag_304_reuses_only_complete_matching_resource_cache(self):
        packages = self._packages()
        release_date = "Thu, 28 Aug 2026 00:00:00 +0000"
        now = email.utils.parsedate_to_datetime(release_date).timestamp() + 60
        bodies = {
            "/store/InRelease": self._inrelease(packages, date=release_date),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }
        etags = {path: '"%s"' % index for index, path in enumerate(bodies, 1)}

        def initial(url, _headers=None):
            path = urllib.parse.urlsplit(url).path
            return {"status": 200, "headers": {"ETag": etags[path]}, "body": bodies[path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            policy = self._policy(root, True)
            first = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=initial,
                release_verifier=lambda *_args: True, config_path=policy,
                clock=lambda: now,
            )
            first.refresh_catalog()

            observed = {}

            def unchanged(url, headers=None):
                path = urllib.parse.urlsplit(url).path
                observed[path] = dict(headers or {})
                return {"status": 304, "headers": {}}

            second = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=unchanged,
                release_verifier=lambda *_args: True, config_path=policy,
                clock=lambda: now + 60,
            )
            refreshed = second.refresh_catalog()
            self.assertEqual(1, len(refreshed))
            self.assertEqual("ready", second.catalog_state)
            self.assertTrue(second.cache_trusted)
            for path, etag in etags.items():
                self.assertEqual(etag, observed[path]["If-None-Match"])

            resources_path = root / "cache" / "resources.json"
            resources = json.loads(resources_path.read_text(encoding="utf-8"))
            resources["resources"]["store/Packages"].pop("body_sha256")
            resources_path.write_text(json.dumps(resources), encoding="utf-8")

            incomplete = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=unchanged,
                release_verifier=lambda *_args: True, config_path=policy,
                clock=lambda: now + 120,
            )
            fallback = incomplete.refresh_catalog()
            self.assertEqual(1, len(fallback))
            self.assertEqual("stale", incomplete.catalog_state)
            self.assertFalse(incomplete.cache_trusted)
            with self.assertRaises(self.core.ProviderUnavailable):
                incomplete.resolve("spark-demo")

    def test_signed_release_date_cannot_move_backwards(self):
        packages = self._packages()
        newer = "Thu, 28 Aug 2026 12:00:00 +0000"
        older = "Thu, 28 Aug 2026 11:00:00 +0000"
        now = email.utils.parsedate_to_datetime(newer).timestamp() + 60
        current_date = {"value": newer}

        def fetch(url, _headers=None):
            path = urllib.parse.urlsplit(url).path
            body = {
                "/store/InRelease": self._inrelease(packages, current_date["value"]),
                "/store/Packages": packages,
                "/store/tools/applist.json": json.dumps(self._applist()),
            }[path]
            return {"status": 200, "headers": {}, "body": body}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True), clock=lambda: now,
            )
            provider.refresh_catalog()
            current_date["value"] = older
            items = provider.refresh_catalog()

        self.assertEqual(1, len(items))
        self.assertEqual("stale", provider.catalog_state)
        self.assertFalse(provider.cache_trusted)

    def test_real_expired_release_is_rejected(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def fetch(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=fetch,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
                clock=lambda: self.test_now + 31 * 24 * 60 * 60,
            )
            with self.assertRaises(self.core.ProviderUnavailable):
                provider.refresh_catalog()

    def test_default_release_window_is_thirty_days(self):
        provider = self.core.SparkPublicProvider()
        self.assertEqual(30 * 24 * 60 * 60, provider.cache_ttl)

    def test_cached_catalog_within_thirty_days_reports_age_warning(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
                clock=lambda: self.test_now,
            )
            provider.refresh_catalog()
            provider.fetcher = lambda *_args: (_ for _ in ()).throw(OSError("offline"))
            provider.clock = lambda: self.test_now + 2 * 24 * 60 * 60
            cached = provider.refresh_catalog()

        self.assertEqual(1, len(cached))
        self.assertEqual("stale", provider.catalog_state)
        self.assertIn("缓存目录", provider.cache_warning)
        self.assertIn("2.0", provider.cache_warning)

    def test_cached_catalog_older_than_thirty_days_is_not_browsable(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
                clock=lambda: self.test_now,
            )
            provider.refresh_catalog()
            provider.fetcher = lambda *_args: (_ for _ in ()).throw(OSError("offline"))
            provider.clock = lambda: self.test_now + 31 * 24 * 60 * 60
            with self.assertRaises(self.core.ProviderUnavailable) as error:
                provider.refresh_catalog()

        self.assertIn("超过 30 天", str(error.exception))
        self.assertEqual([], provider.search("Demo"))

    def test_tampered_display_catalog_is_not_accepted_as_last_good_cache(self):
        packages = self._packages()
        responses = {
            "/store/InRelease": self._inrelease(packages),
            "/store/Packages": packages,
            "/store/tools/applist.json": json.dumps(self._applist()),
        }

        def healthy(url, _headers=None):
            return {"status": 200, "headers": {},
                    "body": responses[urllib.parse.urlsplit(url).path]}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            provider = self.core.SparkPublicProvider(
                cache_root=root / "cache", categories=("tools",), fetcher=healthy,
                release_verifier=lambda *_args: True,
                config_path=self._policy(root, True),
            )
            provider.refresh_catalog()
            catalog_path = root / "cache" / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog[0]["name"] = "tampered"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            provider.fetcher = lambda *_args: (_ for _ in ()).throw(OSError("offline"))
            with self.assertRaises(self.core.ProviderUnavailable):
                provider.refresh_catalog()

    def test_refresh_status_explains_when_cached_catalog_is_in_use(self):
        class Provider:
            catalog_state = "stale"
            last_error = "network down"

            def refresh_catalog(self):
                return [{"app_id": "spark-demo"}]

        class Registry:
            def get(self, _source_id):
                return Provider()

        controller = self.ui.StoreController(
            catalog=type("Catalog", (), {"registry": Registry()})())
        status = controller.refresh_section("spark")[0]
        self.assertTrue(status["using_cache"])
        self.assertIn("来源暂不可用", status["message"])
        self.assertIn("使用缓存", status["message"])

    def test_store_ui_exposes_two_top_level_source_sections(self):
        self.assertEqual(("spark", "sources"), self.ui.STORE_SECTIONS)
        self.assertEqual("星火应用", self.ui.STORE_SECTION_LABELS["spark"])
        self.assertEqual("源应用", self.ui.STORE_SECTION_LABELS["sources"])
        self.assertIn("spark-public", self.ui.SECTION_PROVIDERS["spark"])
        self.assertNotIn("spark-public", self.ui.SECTION_PROVIDERS["sources"])


if __name__ == "__main__":
    unittest.main()
