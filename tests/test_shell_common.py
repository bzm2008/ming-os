import importlib.util
import json
import os
import pathlib
import socket
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "assets" / "ming-shell-common.py"


def load_common():
    spec = importlib.util.spec_from_file_location("ming_shell_common", COMMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShellCommonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load_common()

    def test_rect_rejects_invalid_or_non_finite_geometry(self):
        with self.assertRaises(ValueError):
            self.common.Rect(0, 0, 0, 10)
        with self.assertRaises(ValueError):
            self.common.Rect(float("nan"), 0, 10, 10)

    def test_rect_accepts_mapping_and_reports_bottom_center(self):
        rect = self.common.Rect.from_mapping({"x": 8, "y": 4, "width": 20, "height": 10})
        self.assertEqual((18.0, 14.0), rect.bottom_center)
        self.assertEqual({"x": 8.0, "y": 4.0, "width": 20.0, "height": 10.0}, rect.to_dict())

    def test_ease_out_cubic_clamps_input(self):
        self.assertEqual(0.0, self.common.ease_out_cubic(-1))
        self.assertAlmostEqual(0.875, self.common.ease_out_cubic(0.5))
        self.assertEqual(1.0, self.common.ease_out_cubic(2))

    def test_desktop_parser_builds_argv_without_a_shell(self):
        with tempfile.TemporaryDirectory() as tempdir:
            desktop = pathlib.Path(tempdir) / "Browser.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Browser\n"
                "Name[zh_CN]=浏览器\nExec=/usr/bin/browser --new-window %U\n"
                "Icon=browser\nCategories=Network;WebBrowser;\n",
                encoding="utf-8",
            )
            entry = self.common.parse_desktop_file(desktop, locale_name="zh_CN.UTF-8")
        self.assertEqual("浏览器", entry.name)
        self.assertEqual(("/usr/bin/browser", "--new-window"), entry.argv)
        self.assertEqual(("Network", "WebBrowser"), entry.categories)

    def test_desktop_parser_rejects_shell_operators_and_wrappers(self):
        with tempfile.TemporaryDirectory() as tempdir:
            desktop = pathlib.Path(tempdir) / "Unsafe.desktop"
            for command in ("viewer; rm -rf /", "sh -c 'viewer file'", "env bash -c viewer"):
                desktop.write_text(
                    "[Desktop Entry]\nType=Application\nName=Unsafe\nExec=" + command + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    self.common.parse_desktop_file(desktop)

    def test_hidden_and_non_application_desktop_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as tempdir:
            desktop = pathlib.Path(tempdir) / "Hidden.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Hidden\nExec=hidden\nNoDisplay=true\n",
                encoding="utf-8",
            )
            self.assertIsNone(self.common.parse_desktop_file(desktop))

    def test_runtime_socket_stays_in_runtime_directory(self):
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": tempdir}, clear=False
        ):
            path = self.common.runtime_socket_path("drawer")
        self.assertEqual(pathlib.Path(tempdir) / "ming-os" / "drawer.sock", path)
        with self.assertRaises(ValueError):
            self.common.runtime_socket_path("../escape")

    def test_runtime_path_rejects_traversal_and_creates_private_parent(self):
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": tempdir}, clear=False
        ):
            path = self.common.runtime_path("launch-errors.log")
            self.assertEqual(pathlib.Path(tempdir) / "ming-os" / "launch-errors.log", path)
            self.assertTrue(path.parent.is_dir())
            with self.assertRaises(ValueError):
                self.common.runtime_path("../outside")

    def test_runtime_socket_claim_does_not_unlink_live_instance(self):
        state = {"active": False}

        class FakeUnixSocket:
            def settimeout(self, _timeout):
                pass

            def bind(self, path):
                pathlib.Path(path).write_bytes(b"socket-placeholder")
                state["active"] = True

            def listen(self, _backlog):
                pass

            def connect(self, _path):
                if not state["active"]:
                    raise OSError("not listening")

            def close(self):
                state["active"] = False

        with tempfile.TemporaryDirectory() as tempdir, mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": tempdir}, clear=False
        ), mock.patch.object(self.common.socket, "AF_UNIX", 1, create=True), mock.patch.object(
            self.common.socket, "socket", side_effect=lambda *_args: FakeUnixSocket()
        ):
            first = self.common.claim_runtime_socket("race")
            try:
                with self.assertRaises(self.common.InstanceAlreadyRunning):
                    self.common.claim_runtime_socket("race")
                self.assertTrue(first.path.exists())
            finally:
                first.close()

    def test_recv_json_line_accumulates_chunks_to_newline(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        right.sendall(b'{"version":1,')
        right.sendall(b'"action":"toggle"}\nignored')
        message = self.common.recv_json_line(left, timeout=0.5)
        self.assertEqual("toggle", message["action"])

    def test_recv_json_line_rejects_missing_newline_and_oversize(self):
        for payload in (b'{"action":"toggle"}', b"x" * (64 * 1024 + 1)):
            left, right = socket.socketpair()
            try:
                right.sendall(payload)
                right.shutdown(socket.SHUT_WR)
                with self.assertRaises(ValueError):
                    self.common.recv_json_line(left, timeout=0.2)
            finally:
                left.close()
                right.close()

    def test_run_command_is_structured_and_has_bounded_timeout(self):
        result = self.common.run_command([sys.executable, "-c", "print('ok')"], timeout=2)
        self.assertEqual(0, result.returncode)
        self.assertEqual("ok", result.stdout.strip())
        timed = self.common.run_command(
            [sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05
        )
        self.assertTrue(timed.timed_out)

    def test_json_line_round_trip_is_bounded_and_object_only(self):
        encoded = self.common.encode_json_line({"action": "toggle", "rect": None})
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual({"action": "toggle", "rect": None}, self.common.decode_json_line(encoded))
        with self.assertRaises(ValueError):
            self.common.decode_json_line(json.dumps(["toggle"]).encode() + b"\n")
        with self.assertRaises(ValueError):
            self.common.decode_json_line(b"{" + b"x" * 70000)

    def test_missing_tryexec_hides_desktop_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            desktop = pathlib.Path(tempdir) / "Missing.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Missing\nExec=missing\n"
                "TryExec=ming-command-that-does-not-exist-anywhere\n",
                encoding="utf-8",
            )
            self.assertIsNone(self.common.parse_desktop_file(desktop))


if __name__ == "__main__":
    unittest.main()
