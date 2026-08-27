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
        control = self.make_control(
            lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
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
        control = self.make_control(
            lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
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
        control = self.make_control(
            lambda command, timeout=300: calls.append(tuple(command)) or (0, "", ""))
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
        self.assertLess(source.index("path.unlink"), source.index("self._provider"))

    def test_live_mode_blocks_before_loading_request(self):
        control = self.make_control()
        control._live_mode = lambda: True
        with self.assertRaises(self.control_module.StoreControlError) as caught:
            control.execute("install", "a" * 32)
        self.assertEqual("live_blocked", caught.exception.state)


if __name__ == "__main__":
    unittest.main()
