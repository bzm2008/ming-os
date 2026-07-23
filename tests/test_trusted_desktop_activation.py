import ast
import importlib.util
import os
import pathlib
import socket
import shutil
import shlex
import stat
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "assets" / "ming-shell-common.py"
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"
PHONE_PATH = ROOT / "assets" / "ming-phone-desktop.py"
FINALIZE_PATH = ROOT / "modules" / "07_finalize.sh"
BUILD_PATH = ROOT / "build_onion_os.sh"


def load_common():
    spec = importlib.util.spec_from_file_location("ming_shell_common", COMMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_launch():
    spec = importlib.util.spec_from_file_location("ming_launch_trusted", LAUNCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata(kind, mode=0o644, uid=0):
    return types.SimpleNamespace(st_mode=kind | mode, st_uid=uid)


def protected_directory(mode=0o755, uid=0):
    return metadata(stat.S_IFDIR, mode, uid)


def protected_regular_file(mode=0o644, uid=0):
    return metadata(stat.S_IFREG, mode, uid)


def stat_reader_for(entries):
    expected = {pathlib.Path(path): value for path, value in entries.items()}

    def reader(path):
        return expected[pathlib.Path(path)]

    return reader


class TrustedDesktopActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load_common()

    def test_user_desktop_parsing_keeps_system_environment_visibility_keys(self):
        """User catalog parsing retains its historical OnlyShowIn/NotShowIn behavior."""
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / ".local" / "share" / "applications"
            applications.mkdir(parents=True)
            for key, value in (("OnlyShowIn", "GNOME;"), ("NotShowIn", "XFCE;")):
                with self.subTest(key=key):
                    desktop = applications / (key.lower() + ".desktop")
                    desktop.write_text(
                        "[Desktop Entry]\nType=Application\nName=User App\n"
                        "{}={}\nExec=user-app\n".format(key, value),
                        encoding="utf-8",
                    )
                    with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}, clear=False):
                        entry = self.common.parse_desktop_file(desktop)
                        diagnostic = self.common.diagnose_desktop_file(desktop)

                    self.assertIsNotNone(entry)
                    self.assertIsNotNone(diagnostic)
                    self.assertEqual("User App", entry.name)

    def test_broker_recovery_retries_only_unavailable_requests_until_a_correlated_reply(self):
        """A cold broker may bind late, but a sent request is never duplicated."""
        helper = getattr(self.common, "retry_launch_request_after_broker_start", None)
        self.assertTrue(callable(helper), "bounded broker recovery helper is required")
        unavailable = self.common.LaunchRequestResult("unavailable", "socket missing")
        accepted = self.common.LaunchRequestResult("accepted")
        outcomes = [unavailable, unavailable, accepted]
        calls = []
        sleeps = []

        def sender(path, source, rect, timeout):
            calls.append((path, source, rect, timeout))
            return outcomes.pop(0)

        with mock.patch.object(self.common, "send_launch_request", side_effect=sender):
            result = helper(
                "/usr/share/applications/store-wrapper.desktop",
                "drawer",
                {"x": 1, "y": 1, "width": 10, "height": 10},
                request_timeout=4.0,
                recovery_timeout=1.0,
                retry_interval=0.05,
                sleeper=sleeps.append,
                clock=lambda: 0.0,
            )

        self.assertTrue(result.accepted)
        self.assertEqual(3, len(calls))
        self.assertEqual([0.05, 0.05], sleeps)

    def test_recv_json_line_accepts_the_async_launch_reply_timeout(self):
        """The shared IPC reader must accept the launch client's advertised wait."""
        client, server = socket.socketpair()
        try:
            server.sendall(self.common.encode_json_line({"ready": True}))
            self.assertEqual(
                {"ready": True},
                self.common.recv_json_line(
                    client, timeout=self.common.ASYNC_LAUNCH_REQUEST_TIMEOUT),
            )
        finally:
            client.close()
            server.close()

    def test_recv_json_line_uses_one_total_deadline_across_partial_reads(self):
        """A peer cannot extend a caller's IPC wait by dribbling fragments."""
        chunks = [b'{"ready":', b'true}\n']
        timeouts = []

        class Connection:
            @staticmethod
            def settimeout(value):
                timeouts.append(value)

            @staticmethod
            def recv(_size):
                return chunks.pop(0)

        with mock.patch.object(self.common.time, "monotonic", side_effect=(0.0, 0.0, 0.4)):
            result = self.common.recv_json_line(Connection(), timeout=1.0)

        self.assertEqual({"ready": True}, result)
        self.assertEqual([1.0, 0.6], timeouts)

    def test_shared_installed_package_owner_requires_exact_single_owner_and_state(self):
        """Shell catalog checks must use the same strict Debian ownership proof as launch."""
        helper = getattr(self.common, "installed_package_owner", None)
        self.assertTrue(callable(helper), "shared installed package owner helper is required")
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            cases = {
                "exact": (
                    "store-wrapper: {}\n".format(desktop),
                    "ii \tstore-wrapper\n",
                    "store-wrapper",
                ),
                "ambiguous": (
                    "store-wrapper, other: {}\n".format(desktop),
                    "ii \tstore-wrapper\n",
                    "",
                ),
                "mismatched-path": (
                    "store-wrapper: /usr/share/applications/other.desktop\n",
                    "ii \tstore-wrapper\n",
                    "",
                ),
                "not-installed": (
                    "store-wrapper: {}\n".format(desktop),
                    "hi \tstore-wrapper\n",
                    "",
                ),
            }
            for name, (ownership, installation, expected) in cases.items():
                with self.subTest(name=name):
                    def query(argv, timeout, ownership=ownership, installation=installation):
                        self.assertLessEqual(timeout, 2)
                        return types.SimpleNamespace(
                            returncode=0,
                            stdout=ownership if "-S" in argv else installation,
                            stderr="",
                        )

                    self.assertEqual(
                        expected,
                        helper(desktop, command_runner=query),
                    )

    def test_accepts_protected_system_desktop_wrapper_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertTrue(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_finalizer_trusts_only_fixed_ming_core_system_launchers(self):
        """Generated core launchers need a receipt before the broker may run them."""
        finalizer = FINALIZE_PATH.read_text(encoding="utf-8")
        self.assertIn("seed_trusted_core_desktop_receipts()", finalizer)
        for desktop in (
                "ming-settings.desktop", "ming-edge.desktop",
                "ming-files.desktop", "ming-terminal.desktop"):
            self.assertIn('"{}"'.format(desktop), finalizer)
        self.assertIn("/var/lib/ming-os/trusted-desktops", finalizer)
        self.assertIn("chown root:root", finalizer)
        self.assertIn("chmod 0644", finalizer)
        self.assertIn("seed_trusted_core_desktop_receipts", finalizer)

    def test_rootfs_gate_requires_core_launch_broker_receipts(self):
        build = BUILD_PATH.read_text(encoding="utf-8")
        self.assertIn("# MING_TRUSTED_CORE_DESKTOP_RECEIPTS_VALIDATOR_BEGIN", build)
        self.assertIn("trusted_core_launchers", build)
        for desktop in (
                "ming-settings.desktop", "ming-edge.desktop",
                "ming-files.desktop", "ming-terminal.desktop"):
            self.assertIn('"{}"'.format(desktop), build)
        self.assertIn('root / "var/lib/ming-os/trusted-desktops" / name', build)
        self.assertIn('expected = f"/usr/share/applications/{name}"', build)

    def test_rejects_user_desktop_entry_even_when_stat_looks_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            system_dir = root / "usr-share-applications"
            user_dir = root / "user-applications"
            system_dir.mkdir()
            user_dir.mkdir()
            desktop = user_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_protected_system_shell_wrapper_has_a_broker_only_catalog_entry(self):
        """The catalog may surface a protected wrapper without exposing argv."""
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                    self.common, "is_system_desktop_activation_candidate", return_value=True):
                entry = self.common.diagnose_desktop_file(desktop)

        self.assertIsNotNone(entry)
        self.assertEqual((), entry.argv)
        self.assertEqual("", entry.diagnostic)

    def test_user_shell_wrapper_remains_a_diagnostic_catalog_entry(self):
        """A non-system copy must never inherit the protected wrapper exception."""
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                    self.common, "is_system_desktop_activation_candidate", return_value=False):
                entry = self.common.diagnose_desktop_file(desktop)

        self.assertIsNotNone(entry)
        self.assertEqual((), entry.argv)
        self.assertIn("不支持", entry.diagnostic)

    def test_broker_fallback_argv_allows_only_known_desktop_surfaces(self):
        desktop = "/usr/share/applications/store-wrapper.desktop"
        self.assertEqual(
            ("/usr/local/bin/ming-launch", "--server"),
            self.common.broker_fallback_argv(desktop, "drawer"),
        )
        for source in ("unknown", "ipc", "desktop-copy", ""):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    self.common.broker_fallback_argv(desktop, source)

    def test_broker_fallback_argv_is_independent_of_a_shadowed_path(self):
        desktop = "/usr/share/applications/store-wrapper.desktop"
        with mock.patch.dict(os.environ, {"PATH": "/tmp/ming-launch-shadow"}, clear=False):
            argv = self.common.broker_fallback_argv(desktop, "desktop")
        self.assertEqual("/usr/local/bin/ming-launch", argv[0])
        self.assertEqual(("--server",), argv[1:])

    def test_send_launch_request_returns_false_for_correlated_rejection(self):
        """A response rejection must not be mistaken for a broker outage."""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\nExec=store-wrapper\n",
                encoding="utf-8",
            )
            client, server = socket.socketpair()
            received = {}

            class ConnectedClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    client.close()

                @staticmethod
                def connect(_path):
                    return None

                @staticmethod
                def settimeout(value):
                    client.settimeout(value)

                @staticmethod
                def sendall(payload):
                    client.sendall(payload)

                @staticmethod
                def recv(size):
                    return client.recv(size)

            def reject_once():
                try:
                    message = self.common.recv_json_line(server, timeout=0.5)
                    received.update(message)
                    try:
                        server.sendall(self.common.encode_json_line({
                            "version": 1,
                            "action": "launch-result",
                            "request_id": message.get("request_id", "0" * 32),
                            "accepted": False,
                            "error": "system desktop wrapper is not verified",
                        }))
                    except OSError:
                        pass
                finally:
                    server.close()

            worker = threading.Thread(target=reject_once, daemon=True)
            worker.start()
            with mock.patch.object(self.common, "runtime_socket_path", return_value=root / "launch.sock"):
                with mock.patch.object(self.common.socket, "AF_UNIX", 0, create=True):
                    with mock.patch.object(self.common.socket, "socket", return_value=ConnectedClient()):
                        result = self.common.send_launch_request(desktop, "drawer", timeout=0.5)
            worker.join(1)

        self.assertFalse(result)
        self.assertRegex(received.get("request_id", ""), r"^[a-f0-9]{32}$")

    def test_send_launch_request_rejects_malformed_response_after_connection(self):
        """A reachable broker with a bad reply must never trigger local fallback."""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\nExec=store-wrapper\n",
                encoding="utf-8",
            )
            client, server = socket.socketpair()

            class ConnectedClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    client.close()

                @staticmethod
                def connect(_path):
                    return None

                @staticmethod
                def settimeout(value):
                    client.settimeout(value)

                @staticmethod
                def sendall(payload):
                    client.sendall(payload)

                @staticmethod
                def recv(size):
                    return client.recv(size)

            def respond_once():
                try:
                    self.common.recv_json_line(server, timeout=0.5)
                    server.sendall(b"not-json\n")
                finally:
                    server.close()

            worker = threading.Thread(target=respond_once, daemon=True)
            worker.start()
            with mock.patch.object(self.common, "runtime_socket_path", return_value=root / "launch.sock"):
                with mock.patch.object(self.common.socket, "AF_UNIX", 0, create=True):
                    with mock.patch.object(self.common.socket, "socket", return_value=ConnectedClient()):
                        result = self.common.send_launch_request(desktop, "drawer", timeout=0.5)
            worker.join(1)

        self.assertFalse(result)
        self.assertTrue(result.rejected)
        self.assertFalse(result.unavailable)
        self.assertEqual("invalid broker response", result.error)

    def test_send_launch_request_rejects_post_send_connection_reset(self):
        """A reset after send is not evidence that no broker received the request."""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\nExec=store-wrapper\n",
                encoding="utf-8",
            )

            class ConnectedClient:
                def __enter__(self):
                    return self

                @staticmethod
                def __exit__(*_args):
                    return None

                @staticmethod
                def connect(_path):
                    return None

                @staticmethod
                def settimeout(_value):
                    return None

                @staticmethod
                def sendall(_payload):
                    return None

                @staticmethod
                def recv(_size):
                    raise ConnectionResetError("peer reset after accepting request")

            with mock.patch.object(self.common, "runtime_socket_path", return_value=root / "launch.sock"):
                with mock.patch.object(self.common.socket, "AF_UNIX", 0, create=True):
                    with mock.patch.object(self.common.socket, "socket", return_value=ConnectedClient()):
                        result = self.common.send_launch_request(desktop, "drawer", timeout=0.5)

        self.assertFalse(result)
        self.assertTrue(result.rejected)
        self.assertFalse(result.unavailable)
        self.assertEqual("invalid broker response", result.error)

    def test_async_launch_request_returns_before_a_slow_broker_reply(self):
        """GTK callers can wait for a verified result without blocking their event handler."""
        async_sender = getattr(self.common, "send_launch_request_async", None)
        self.assertTrue(callable(async_sender), "shared async launch helper is required")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\nExec=store-wrapper\n",
                encoding="utf-8",
            )
            client, server = socket.socketpair()
            completed = threading.Event()
            received = []

            class ConnectedClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    client.close()

                @staticmethod
                def connect(_path):
                    return None

                @staticmethod
                def settimeout(value):
                    client.settimeout(value)

                @staticmethod
                def sendall(payload):
                    client.sendall(payload)

                @staticmethod
                def recv(size):
                    return client.recv(size)

            def delayed_reply():
                try:
                    message = self.common.recv_json_line(server, timeout=0.5)
                    time.sleep(0.30)
                    server.sendall(self.common.encode_json_line({
                        "version": 1,
                        "action": "launch-result",
                        "request_id": message["request_id"],
                        "accepted": True,
                    }))
                finally:
                    server.close()

            worker = threading.Thread(target=delayed_reply, daemon=True)
            worker.start()
            started = time.monotonic()
            with mock.patch.object(self.common, "runtime_socket_path", return_value=root / "launch.sock"):
                with mock.patch.object(self.common.socket, "AF_UNIX", 0, create=True):
                    with mock.patch.object(self.common.socket, "socket", return_value=ConnectedClient()):
                        self.assertTrue(async_sender(
                            desktop,
                            "drawer",
                            callback=lambda result: (received.append(result), completed.set()),
                            timeout=1.0,
                        ))
                        self.assertLess(time.monotonic() - started, 0.15)
                        self.assertTrue(completed.wait(1.0))
            worker.join(1)

        self.assertEqual(1, len(received))
        self.assertTrue(received[0].accepted)

    def test_managed_package_wrapper_copy_retains_its_canonical_broker_source(self):
        """A friendly Desktop filename must not turn a system launcher into a broken duplicate."""
        source = PHONE_PATH.read_text(encoding="utf-8")
        self.assertIn("X-Ming-Source-Desktop", source)
        self.assertIn("def trusted_wrapper_source_path", source)
        self.assertIn("def managed_desktop_source_path", source)

        tree = ast.parse(source)
        wanted = {
            "app_id", "safe_name", "_desktop_has_marker", "_mark_desktop_file",
            "trusted_wrapper_source_path", "managed_desktop_source_path", "write_managed_wrapper_proxy",
            "_confirm_file_durable", "_durable_replace", "copy_desktop",
            "read_app", "add_app_from_path", "load_apps",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        )
        namespace = {
            "Path": pathlib.Path,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_PATH),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_PATH), "exec"), namespace)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            applications = root / "applications"
            desktop_dir = root / "Desktop"
            applications.mkdir()
            desktop_dir.mkdir()
            system_wrapper = applications / "store-wrapper.desktop"
            system_wrapper.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=/usr/local/bin/store-wrapper\nIcon=store-wrapper\n",
                encoding="utf-8",
            )

            class Entry:
                name = "Store Wrapper"
                icon = "store-wrapper"
                categories = ("Utility",)
                argv = ("/usr/local/bin/store-wrapper",)
                diagnostic = ""

            class Common:
                @staticmethod
                def diagnose_desktop_file(path):
                    self.assertEqual(system_wrapper.resolve(), pathlib.Path(path).resolve())
                    return Entry()

                @staticmethod
                def is_system_desktop_activation_candidate(path):
                    return pathlib.Path(path).resolve() == system_wrapper.resolve()

            namespace["COMMON"] = Common()
            self.assertEqual(
                system_wrapper.resolve(),
                namespace["trusted_wrapper_source_path"](system_wrapper),
            )
            probe = desktop_dir / "proxy-probe.desktop"
            shutil.copy2(system_wrapper, probe)
            self.assertTrue(namespace["write_managed_wrapper_proxy"](probe, system_wrapper))
            probe.unlink()
            copied = namespace["copy_desktop"](
                system_wrapper, desktop_dir, name="Store Wrapper", managed=True)
            self.assertEqual("Store Wrapper.desktop", copied.name)
            copy_text = copied.read_text(encoding="utf-8")
            self.assertIn("X-Ming-Source-Desktop={}".format(system_wrapper.resolve()), copy_text)
            self.assertIn(
                "Exec=/usr/local/bin/ming-launch --desktop-file {} --source desktop".format(
                    shlex.quote(str(system_wrapper.resolve()))),
                copy_text,
            )
            self.assertEqual(str(system_wrapper.resolve()), namespace["read_app"](copied)["path"])

            namespace["APP_DIRS"] = [desktop_dir, applications]
            namespace["add_core_app"] = lambda _apps, _basename: False
            apps = namespace["load_apps"]()
            self.assertEqual([str(system_wrapper.resolve())], [app["path"] for app in apps])
            self.assertEqual([""], [app["diagnostic"] for app in apps])

    def test_managed_proxy_rejects_a_symlink_source_before_resolution(self):
        """A marker must be checked as written, before canonicalisation erases a link."""
        source = PHONE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "trusted_wrapper_source_path"
        )
        namespace = {"Path": pathlib.Path}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                str(PHONE_PATH),
                "exec",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            alias = root / "wrapper-link.desktop"
            target = root / "trusted-wrapper.desktop"
            alias.write_text("[Desktop Entry]\n", encoding="utf-8")
            target.write_text("[Desktop Entry]\n", encoding="utf-8")
            original_resolve = pathlib.Path.resolve

            def resolve_with_alias(path, strict=False):
                if pathlib.Path(path) == alias:
                    return target
                return original_resolve(path, strict=strict)

            class Entry:
                argv = ()
                diagnostic = ""

            class Common:
                @staticmethod
                def is_system_desktop_activation_candidate(path):
                    return pathlib.Path(path) == target

                @staticmethod
                def diagnose_desktop_file(path):
                    self.assertEqual(target, pathlib.Path(path))
                    return Entry()

            namespace["COMMON"] = Common()
            with mock.patch.object(pathlib.Path, "resolve", new=resolve_with_alias):
                self.assertIsNone(namespace["trusted_wrapper_source_path"](alias))

    def test_rejects_candidate_when_resolution_indicates_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            target = system_dir / "target.desktop"
            source = system_dir / "store-wrapper.desktop"
            target.write_text("[Desktop Entry]\n", encoding="utf-8")
            source.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(path):
                path = pathlib.Path(path)
                if path == source:
                    return target
                return path.resolve(strict=True)

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                source,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    target: protected_regular_file(),
                }),
            ))

    def test_rejects_group_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(0o664),
                }),
            ))

    def test_rejects_other_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(0o646),
                }),
            ))

    def test_rejects_nonstandard_desktop_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.DESKTOP"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_writable_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(0o775),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_other_writable_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(0o757),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_non_directory_system_path(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_regular_file(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_non_root_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(uid=1000),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_symlinked_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            system_dir = root / "applications"
            target_dir = root / "resolved-applications"
            system_dir.mkdir()
            target_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            target = target_dir / desktop.name
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            target.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(path):
                path = pathlib.Path(path)
                if path == system_dir:
                    return target_dir
                if path == desktop:
                    return target
                return path.resolve(strict=True)

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    target_dir: protected_directory(),
                    target: protected_regular_file(),
                }),
            ))

    def test_rejects_non_root_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(uid=1000),
                }),
            ))

    def test_rejects_non_regular_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: metadata(stat.S_IFDIR),
                }),
            ))

    def test_rejects_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "missing.desktop"

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({system_dir: protected_directory()}),
            ))

    def test_rejects_nested_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            nested_dir = system_dir / "nested"
            nested_dir.mkdir(parents=True)
            desktop = nested_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_returns_false_when_path_resolution_hits_a_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(_path):
                raise RuntimeError("symlink loop")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))


class TrustedLaunchRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_launch()

    def test_protected_system_shell_wrapper_selects_internal_app_info_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            request = self.launch.request_from_desktop_file(
                desktop,
                allowed_dirs=(applications,),
                candidate_verifier=lambda path: path == desktop.resolve(),
                trusted_verifier=lambda path: path == desktop.resolve(),
            )

            self.assertEqual("desktop_app_info", request.mode)
            self.assertEqual((), request.argv)
            self.assertEqual(str(desktop.resolve()), request.desktop_file)

    def test_shell_wrapper_does_not_select_internal_mode_without_final_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_desktop_file(
                    desktop,
                    allowed_dirs=(applications,),
                    candidate_verifier=lambda _path: True,
                    trusted_verifier=lambda _path: False,
                )

    def test_shell_wrapper_hidden_by_desktop_visibility_cannot_select_internal_mode(self):
        """The broker must match installer visibility before granting GIO activation."""
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            for key, value in (("OnlyShowIn", "GNOME;"), ("NotShowIn", "XFCE;")):
                with self.subTest(key=key):
                    desktop = applications / (key.lower() + ".desktop")
                    desktop.write_text(
                        "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                        "{}={}\nExec=sh -c 'exec /opt/store-wrapper/run'\n".format(key, value),
                        encoding="utf-8",
                    )
                    with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}, clear=False):
                        with self.assertRaises(ValueError):
                            self.launch.request_from_desktop_file(
                                desktop,
                                allowed_dirs=(applications,),
                                candidate_verifier=lambda _path: True,
                                trusted_verifier=lambda _path: True,
                            )

    def test_final_verifier_requires_an_exact_installed_package_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            calls = []

            def query(argv, timeout):
                calls.append((tuple(argv), timeout))
                if "-S" in argv:
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="ii \tstore-wrapper\n",
                    stderr="",
                )

            self.assertTrue(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=query,
                descriptor_revalidator=lambda path, parent: (
                    path == desktop.resolve() and parent == applications.resolve()
                ),
            ))
            self.assertEqual(2, len(calls))
            self.assertTrue(all(timeout <= 2 for _argv, timeout in calls))

    def test_final_verifier_uses_the_shared_installed_owner_contract(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        verifier = source.split("def verify_package_owned_system_desktop(", 1)[1].split(
            "\ndef _is_shell_wrapper_error", 1
        )[0]

        self.assertIn('"installed_package_owner"', verifier)
        self.assertIn("descriptor_revalidate_system_desktop", verifier)

    def test_final_verifier_rejects_unowned_ambiguous_mismatched_and_noninstalled_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            cases = {
                "unowned": (
                    types.SimpleNamespace(returncode=1, stdout="", stderr="not found"),
                    None,
                ),
                "ambiguous": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="first, second: {}\n".format(desktop.resolve()),
                        stderr="",
                    ),
                    None,
                ),
                "mismatched": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: /usr/share/applications/other.desktop\n",
                        stderr="",
                    ),
                    None,
                ),
                "not-installed": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    ),
                    types.SimpleNamespace(returncode=0, stdout="hi \tstore-wrapper\n", stderr=""),
                ),
            }
            for name, (ownership, installation) in cases.items():
                with self.subTest(name=name):
                    def query(argv, timeout, ownership=ownership, installation=installation):
                        del timeout
                        return ownership if "-S" in argv else installation

                    self.assertFalse(self.launch.verify_package_owned_system_desktop(
                        desktop,
                        system_dir=applications,
                        command_runner=query,
                        descriptor_revalidator=lambda *_args: True,
                    ))

    def test_shell_wrapper_ipc_cannot_select_an_internal_launch_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_message({
                    "version": 1,
                    "action": "launch",
                    "desktop_file": str(desktop),
                    "source": "drawer",
                    "rect": None,
                    "mode": "desktop_app_info",
                }, allowed_dirs=(applications,))

    def test_non_shell_parse_failure_never_selects_desktop_app_info_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "broken-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken Wrapper\n"
                "Exec=sh -c 'unterminated\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_desktop_file(
                    desktop,
                    allowed_dirs=(applications,),
                    candidate_verifier=lambda _path: True,
                )

    def test_final_verifier_rejects_query_timeouts_and_descriptor_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            def timeout(_argv, timeout):
                self.assertLessEqual(timeout, 2)
                raise self.launch.subprocess.TimeoutExpired("dpkg-query", timeout)

            self.assertFalse(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=timeout,
                descriptor_revalidator=lambda *_args: self.fail("must not revalidate"),
            ))

            def installed_query(argv, timeout):
                del timeout
                if "-S" in argv:
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="ii \tstore-wrapper\n",
                    stderr="",
                )

            self.assertFalse(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=installed_query,
                descriptor_revalidator=lambda *_args: False,
            ))

    def test_descriptor_revalidation_accepts_an_injected_metadata_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            result = self.launch.descriptor_revalidate_system_desktop(
                desktop,
                system_dir=applications,
                fstat_reader=lambda _fd: protected_regular_file(),
            )

            self.assertIsInstance(result, bool)

    @unittest.skipUnless(os.name == "posix", "descriptor-relative open is POSIX-specific")
    def test_descriptor_revalidation_uses_real_dirfds_without_blocking_on_fifo(self):
        required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")
        if not all(isinstance(getattr(os, flag, None), int) and getattr(os, flag) > 0 for flag in required_flags):
            self.skipTest("host lacks required no-follow descriptor flags")
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            regular = applications / "regular.desktop"
            regular.write_text("[Desktop Entry]\n", encoding="utf-8")
            linked = applications / "linked.desktop"
            os.symlink(regular.name, linked)
            fifo = applications / "fifo.desktop"
            os.mkfifo(fifo)

            def root_owned_fstat(fd):
                metadata = os.fstat(fd)
                return types.SimpleNamespace(st_mode=metadata.st_mode, st_uid=0)

            close_calls = []
            native_close = os.close

            def observed_close(fd):
                close_calls.append(fd)
                native_close(fd)

            with mock.patch.object(self.launch.os, "close", side_effect=observed_close):
                self.assertTrue(self.launch.descriptor_revalidate_system_desktop(
                    regular,
                    system_dir=applications,
                    fstat_reader=root_owned_fstat,
                ))
            self.assertEqual(2, len(close_calls))
            self.assertFalse(self.launch.descriptor_revalidate_system_desktop(
                linked,
                system_dir=applications,
                fstat_reader=root_owned_fstat,
            ))

            result = {}

            def verify_fifo():
                result["accepted"] = self.launch.descriptor_revalidate_system_desktop(
                    fifo,
                    system_dir=applications,
                    fstat_reader=root_owned_fstat,
                )

            started = time.monotonic()
            worker = threading.Thread(target=verify_fifo, daemon=True)
            worker.start()
            worker.join(0.5)
            self.assertFalse(worker.is_alive(), "FIFO verification must not block the launch broker")
            self.assertFalse(result.get("accepted"))
            self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
