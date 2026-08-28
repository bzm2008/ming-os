import hashlib
import importlib.util
import inspect
import json
import pathlib
import tempfile
import types
import unittest
from contextlib import contextmanager


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "assets" / "ming-store-control.py"


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingStoreControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.control_module = load_script("ming_store_control_tests", CONTROL_PATH)

    def make_control(self, runner=None, claim_base=None):
        return self.control_module.StoreControl(
            environ={"PKEXEC_UID": "1000"},
            runner=runner or (lambda _command, timeout=300: (0, "", "")),
            catalog_root=ROOT / "assets/ming-store-catalog",
            claim_base=claim_base or pathlib.Path(tempfile.gettempdir()) / "ming-store-control-tests",
            euid_getter=lambda: 0,
        )

    def request(self, action="install"):
        return types.SimpleNamespace(
            request_id="a" * 32,
            uid=1000,
            action=action,
            provider="debian-apt",
            app_id="vlc",
            expected_version="3.0.21",
        )

    def prepare(self, control, request, temp_path):
        account = types.SimpleNamespace(pw_name="alice")
        control._live_mode = lambda: False
        control._caller = lambda: (1000, account)
        control._load_request = lambda uid, action, request_id: (temp_path, request)
        control._journal = lambda *_args: None
        control._refresh_desktop = lambda *_args: {
            "desktop_database": True, "icon_cache": True,
        }
        @contextmanager
        def claim(_uid, _request_id):
            yield
        control._claim_request = claim

    @staticmethod
    def enable_spark_policy(control):
        control._spark_policy = lambda: {
            "schema": "ming.store.spark-public.v1",
            "provider": "spark-public",
            "installation_enabled": True,
            "key_fingerprint": "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
        }

    def test_install_uses_exact_resolved_apt_candidate_and_reads_back(self):
        calls = []

        def runner(command, timeout=300):
            calls.append(tuple(command))
            return 0, "", ""

        control = self.make_control(runner)
        request = self.request()
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "vlc"},
            resolve=lambda _app_id: {
                "package_name": "vlc", "resolved_version": "3.0.21",
                "apt_target": "vlc=3.0.21",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            control._installed = lambda _package: {
                "installed": True, "version": "3.0.21", "architecture": "amd64",
            }
            result = control.execute("install", "a" * 32)
        self.assertTrue(result["ok"])
        apt = next(command for command in calls if command and command[0] == "apt-get")
        self.assertIn("vlc=3.0.21", apt)
        self.assertIn("Acquire::Retries=3", apt)
        self.assertNotIn("sh", apt)
        self.assertNotIn("-c", apt)

    def test_changed_candidate_is_rejected_before_apt(self):
        calls = []
        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command and command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\n", ""
            return 0, "", ""

        control = self.make_control(runner)
        request = self.request()
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "vlc"},
            resolve=lambda _app_id: {
                "package_name": "vlc", "resolved_version": "3.0.22",
                "apt_target": "vlc=3.0.22",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("install", "a" * 32)
        self.assertEqual("candidate_changed", caught.exception.state)
        self.assertFalse(any(command and command[0] == "apt-get" for command in calls))

    def test_protected_package_remove_is_rejected_without_apt(self):
        calls = []
        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command and command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\nDepends: libc6\n", ""
            return 0, "", ""

        control = self.make_control(runner)
        request = self.request(action="remove")
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "ming-store"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("remove", "a" * 32)
        self.assertEqual("protected_package", caught.exception.state)
        self.assertFalse(any(command and command[0] == "apt-get" for command in calls))

    def test_catalog_protected_flag_blocks_remove_without_apt(self):
        calls = []
        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command and command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\nDepends: libc6\n", ""
            return 0, "", ""

        control = self.make_control(runner)
        request = self.request(action="remove")
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "vlc", "protected": True},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            control._is_protected = lambda _package: False
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("remove", "a" * 32)
        self.assertEqual("protected_package", caught.exception.state)
        self.assertFalse(any(command and command[0] == "apt-get" for command in calls))

    def test_refresh_failure_is_a_warning_after_verified_install(self):
        control = self.make_control()
        request = self.request()
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "vlc"},
            resolve=lambda _app_id: {
                "package_name": "vlc", "resolved_version": "3.0.21",
                "apt_target": "vlc=3.0.21",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            control._installed = lambda _package: {
                "installed": True, "version": "3.0.21", "architecture": "amd64",
            }
            control._refresh_desktop = lambda *_args: {
                "desktop_database": False, "icon_cache": True,
            }
            result = control.execute("install", "a" * 32)
        self.assertEqual("refresh_warning", result["state"])
        self.assertIn("桌面入口刷新失败", result["message"])

    def test_request_id_is_exactly_lowercase_hex(self):
        control = self.make_control()
        with self.assertRaises(self.control_module.StoreControlError):
            control._load_request(1000, "install", "../" + "a" * 32)
        with self.assertRaises(self.control_module.StoreControlError):
            control._load_request(1000, "install", "A" * 32)

    def test_request_is_opened_once_without_following_links(self):
        source = inspect.getsource(self.control_module.StoreControl._load_request)
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertIn("os.fstat", source)
        self.assertNotIn("path.read_text", source)

    def test_request_claim_rejects_concurrent_reuse_and_releases_afterward(self):
        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(claim_base=pathlib.Path(directory) / "claims")
            with control._claim_request(1000, "a" * 32):
                with self.assertRaises(self.control_module.StoreControlError) as caught:
                    with control._claim_request(1000, "a" * 32):
                        pass
            self.assertEqual("request_in_progress", caught.exception.state)
            with control._claim_request(1000, "a" * 32):
                pass

    def test_claimed_request_is_consumed_before_the_package_operation(self):
        source = inspect.getsource(self.control_module.StoreControl._execute_request)
        self.assertNotIn("path.unlink", source)
        loader = inspect.getsource(self.control_module.StoreControl._load_request)
        self.assertIn("os.replace", loader)
        self.assertIn("claimed", loader)
        self.assertLess(loader.index("os.replace"), loader.index("os.open"))

    def test_live_mode_blocks_before_loading_request(self):
        control = self.make_control()
        control._live_mode = lambda: True
        with self.assertRaises(self.control_module.StoreControlError) as caught:
            control.execute("install", "a" * 32)
        self.assertEqual("live_blocked", caught.exception.state)

    def test_spark_public_package_downloads_verified_artifact_without_url_in_apt_command(self):
        calls = []
        digest = hashlib.sha256(b"verified deb").hexdigest()

        class Downloader:
            def download(self, url, destination, expected_sha256):
                calls.append(("download", url, expected_sha256))
                pathlib.Path(destination).write_bytes(b"verified deb")
                return {"ok": True, "sha256": expected_sha256}

        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command and command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\nDepends: libc6\n", ""
            return 0, "", ""

        control = self.make_control(runner)
        self.enable_spark_policy(control)
        control.downloader = Downloader()
        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        request.expected_version = "1.2.3"
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "demo", "install_method": "spark-deb"},
            resolve=lambda _app_id: {
                "package_name": "demo", "resolved_version": "1.2.3",
                "install_method": "spark-deb",
                "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                "artifact_filename": "demo_1.2.3_amd64.deb",
                "resolved_architecture": "amd64",
                "identity": {"sha256": digest},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            control._is_protected = lambda _package: False
            control._installed = lambda _package: {
                "installed": True, "version": "1.2.3", "architecture": "amd64",
            }
            result = control.execute("install", "a" * 32)

        self.assertTrue(result["ok"])
        self.assertEqual("spark-public", result["provider"])
        download = next(call for call in calls if call[0] == "download")
        self.assertTrue(download[1].startswith("https://"))
        apt = next(command for command in calls if command and command[0] == "apt-get")
        self.assertTrue(any("demo" in argument for argument in apt))
        self.assertNotIn("http", apt)

    def test_spark_public_rejects_download_result_when_file_hash_is_wrong(self):
        calls = []

        class Downloader:
            def download(self, url, destination, expected_sha256):
                calls.append(("download", url, expected_sha256))
                pathlib.Path(destination).write_bytes(b"tampered deb")
                return {"ok": True, "sha256": expected_sha256}

        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command and command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\nDepends: libc6\n", ""
            return 0, "", ""

        control = self.make_control(runner)
        self.enable_spark_policy(control)
        control.downloader = Downloader()
        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        request.expected_version = "1.2.3"
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "demo", "install_method": "spark-deb"},
            resolve=lambda _app_id: {
                "package_name": "demo", "resolved_version": "1.2.3",
                "install_method": "spark-deb",
                "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                "artifact_filename": "demo_1.2.3_amd64.deb",
                "resolved_architecture": "amd64",
                "identity": {"sha256": "a" * 64},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            control._is_protected = lambda _package: False
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("install", "a" * 32)
        self.assertEqual("integrity_failed", caught.exception.state)
        self.assertFalse(any(command and command[0] == "apt-get" for command in calls))

    def test_spark_public_rejects_resolved_package_identity_change(self):
        calls = []

        class Downloader:
            def download(self, url, destination, expected_sha256):
                calls.append(("download", url, expected_sha256))
                pathlib.Path(destination).write_bytes(b"verified deb")
                return {"ok": True, "sha256": expected_sha256}

        control = self.make_control(lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
        self.enable_spark_policy(control)
        control.downloader = Downloader()
        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        request.expected_version = "1.2.3"
        provider = types.SimpleNamespace(
            get=lambda _app_id: {"package_name": "demo", "install_method": "spark-deb"},
            resolve=lambda _app_id: {
                "package_name": "other", "resolved_version": "1.2.3",
                "install_method": "spark-deb",
                "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                "artifact_filename": "demo_1.2.3_amd64.deb",
                "resolved_architecture": "amd64",
                "identity": {"sha256": "a" * 64},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("install", "a" * 32)
        self.assertEqual("identity_mismatch", caught.exception.state)
        self.assertFalse(any(call[0] == "download" for call in calls))

    def test_policy_false_blocks_root_install_before_refresh_download_or_apt(self):
        calls = []

        class Downloader:
            def download(self, *_args):
                calls.append(("download",))
                raise AssertionError("policy-disabled install reached downloader")

        provider = types.SimpleNamespace(
            catalog_state="ready", cache_trusted=True,
            refresh_catalog=lambda: calls.append(("refresh",)) or [],
            get=lambda _app_id: {"package_name": "demo", "install_method": "spark-deb"},
            resolve=lambda _app_id: {
                "package_name": "demo", "resolved_version": "1.2.3",
                "resolved_architecture": "amd64", "install_method": "spark-deb",
                "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                "artifact_filename": "demo_1.2.3_amd64.deb",
                "identity": {"sha256": "a" * 64},
            },
        )
        control = self.make_control(
            lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
        control.downloader = Downloader()
        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        request.expected_version = "1.2.3"
        with tempfile.TemporaryDirectory() as directory:
            control.catalog_root = pathlib.Path(directory)
            control.catalog_root.mkdir(parents=True, exist_ok=True)
            (control.catalog_root / "spark-public.json").write_text(json.dumps({
                "schema": "ming.store.spark-public.v1",
                "provider": "spark-public",
                "key_fingerprint": "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
                "installation_enabled": False,
                "installation_disabled_reason": "测试：未启用可信公钥。",
            }), encoding="utf-8")
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("install", "a" * 32)

        self.assertEqual("installation_disabled", caught.exception.state)
        self.assertNotIn(("refresh",), calls)
        self.assertNotIn(("download",), calls)
        self.assertFalse(any(call and call[0] == "apt-get" for call in calls))

    def test_spark_install_requires_online_trusted_ready_refresh(self):
        calls = []

        def resolve(_app_id):
            calls.append(("resolve",))
            return {"package_name": "demo", "resolved_version": "1.2.3"}

        provider = types.SimpleNamespace(
            catalog_state="stale", cache_trusted=False,
            refresh_catalog=lambda: [{"app_id": "spark-demo"}],
            get=lambda _app_id: {"package_name": "demo", "install_method": "spark-deb"},
            resolve=resolve,
        )
        control = self.make_control(
            lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
        self.enable_spark_policy(control)
        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        request.expected_version = "1.2.3"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "request.json"
            path.write_text("{}", encoding="utf-8")
            self.prepare(control, request, path)
            control._provider = lambda _request: provider
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control.execute("install", "a" * 32)

        self.assertEqual("provider_unavailable", caught.exception.state)
        self.assertNotIn(("resolve",), calls)
        self.assertFalse(any(call and call[0] == "apt-get" for call in calls))

    def test_legacy_package_name_and_spark_wine_method_are_blocked_before_download(self):
        calls = []

        class Downloader:
            def download(self, *_args):
                calls.append(("download",))
                raise AssertionError("blocked package reached downloader")

        for package_name, install_method, expected_state in (
                ("spark-store-helper", "spark-deb", "legacy_dependency"),
                ("demo", "spark-wine-deb", "toolbox_required")):
            with self.subTest(package_name=package_name, install_method=install_method):
                calls.clear()
                control = self.make_control(
                    lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
                self.enable_spark_policy(control)
                control.downloader = Downloader()
                request = self.request()
                request.provider = "spark-public"
                request.app_id = "spark-demo"
                request.expected_version = "1.2.3"
                item = {"package_name": package_name, "install_method": install_method}
                resolved = dict(item,
                    resolved_version="1.2.3", resolved_architecture="amd64",
                    download_url="https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                    artifact_filename="demo_1.2.3_amd64.deb",
                    identity={"sha256": "a" * 64},
                )
                provider = types.SimpleNamespace(
                    catalog_state="ready", cache_trusted=True,
                    refresh_catalog=lambda: [item],
                    get=lambda _app_id: item,
                    resolve=lambda _app_id: resolved,
                )
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "request.json"
                    path.write_text("{}", encoding="utf-8")
                    self.prepare(control, request, path)
                    control._provider = lambda _request: provider
                    with self.assertRaises(self.control_module.StoreControlError) as caught:
                        control.execute("install", "a" * 32)
                self.assertEqual(expected_state, caught.exception.state)
                self.assertNotIn(("download",), calls)

    def test_legacy_dependency_parser_uses_debian_package_tokens_not_substrings(self):
        control = self.make_control()
        self.assertTrue(control._has_legacy_spark_dependency(
            "libc6 (>= 2.34), aptss:any (>= 1) | helper"))
        self.assertTrue(control._has_legacy_spark_dependency(
            "amber-ce-runtime, bookworm-run"))
        self.assertFalse(control._has_legacy_spark_dependency(
            "libaptss-helper, mapm, spark-storefront"))
        self.assertFalse(control._has_legacy_spark_dependency(
            "Package: demo\nDescription: spark-store is only mentioned in prose\n"
            " This text must not be parsed as a dependency.\n"))

    def test_signed_architecture_must_match_deb_and_installed_readback_exactly(self):
        payload = b"verified deb"
        digest = hashlib.sha256(payload).hexdigest()

        class Downloader:
            def download(self, _url, destination, expected_sha256):
                pathlib.Path(destination).write_bytes(payload)
                return {"ok": True, "sha256": expected_sha256}

        request = self.request()
        request.provider = "spark-public"
        request.app_id = "spark-demo"
        item = {"package_name": "demo", "install_method": "spark-deb"}
        resolved = {
            "package_name": "demo", "resolved_version": "1.2.3",
            "resolved_architecture": "amd64", "install_method": "spark-deb",
            "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
            "artifact_filename": "demo_1.2.3_amd64.deb",
            "identity": {"sha256": digest},
        }

        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def mismatched_deb(command, timeout=300):
                calls.append(tuple(command))
                if command[0] == "dpkg-deb":
                    return 0, "demo\n1.2.3\nall\nDepends: libc6\nPre-Depends:\n", ""
                return 0, "", ""

            control = self.make_control(mismatched_deb)
            control.artifact_root = pathlib.Path(directory) / "artifacts-one"
            control.downloader = Downloader()
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control._spark_install(request, item, resolved, "install")
            self.assertEqual("identity_mismatch", caught.exception.state)
            self.assertFalse(any(call and call[0] == "apt-get" for call in calls))

            calls.clear()

            def matched_deb(command, timeout=300):
                calls.append(tuple(command))
                if command[0] == "dpkg-deb":
                    return 0, "demo\n1.2.3\namd64\nDepends: libc6\nPre-Depends:\n", ""
                return 0, "", ""

            control = self.make_control(matched_deb)
            control.artifact_root = pathlib.Path(directory) / "artifacts-two"
            control.downloader = Downloader()
            control._installed = lambda _package: {
                "installed": True, "version": "1.2.3", "architecture": "all",
            }
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control._spark_install(request, item, resolved, "install")
            self.assertEqual("readback_failed", caught.exception.state)

    def test_pre_depends_on_retired_runtime_is_rejected_before_apt(self):
        payload = b"verified deb"
        digest = hashlib.sha256(payload).hexdigest()
        calls = []

        class Downloader:
            def download(self, _url, destination, expected_sha256):
                pathlib.Path(destination).write_bytes(payload)
                return {"ok": True, "sha256": expected_sha256}

        def runner(command, timeout=300):
            calls.append(tuple(command))
            if command[0] == "dpkg-deb":
                return 0, "demo\n1.2.3\namd64\nDepends: libc6\nPre-Depends: trixie-run:any (>= 1)\n", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as directory:
            control = self.make_control(runner)
            control.artifact_root = pathlib.Path(directory) / "artifacts"
            control.downloader = Downloader()
            request = self.request()
            request.provider = "spark-public"
            item = {"package_name": "demo", "install_method": "spark-deb"}
            resolved = {
                "package_name": "demo", "resolved_version": "1.2.3",
                "resolved_architecture": "amd64", "install_method": "spark-deb",
                "download_url": "https://cdn.d.store.deepinos.org.cn/store/tools/demo/demo_1.2.3_amd64.deb",
                "artifact_filename": "demo_1.2.3_amd64.deb",
                "identity": {"sha256": digest},
            }
            with self.assertRaises(self.control_module.StoreControlError) as caught:
                control._spark_install(request, item, resolved, "install")
        self.assertEqual("legacy_dependency", caught.exception.state)
        self.assertFalse(any(call and call[0] == "apt-get" for call in calls))


if __name__ == "__main__":
    unittest.main()
