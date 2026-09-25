#!/usr/bin/env python3
"""Local JSON-lines IPC service for the Ming Tea native desktop app."""

import argparse
import importlib.util
import json
import os
import pathlib
import signal
import socket
import sys
import threading


CORE_PATH = pathlib.Path("/usr/local/lib/ming-os/ming-tea/ming-tea-core.py")
if not CORE_PATH.is_file():
    CORE_PATH = pathlib.Path(__file__).with_name("ming-tea-core.py")
SPEC = importlib.util.spec_from_file_location("ming_tea_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)

# MING_TEA_SOCKET is the stable environment override used by the native shell.
# The runtime emits the shared session.created / approval.requested protocol.
MING_TEA_SOCKET = "MING_TEA_SOCKET"


class MingTeaIPCServer:
    def __init__(self, socket_path):
        self.socket_path = pathlib.Path(socket_path)
        self.runtime = CORE.MingTeaRuntime()
        self._server = None
        self._stopping = threading.Event()

    def handle_request(self, request):
        if not isinstance(request, dict):
            raise CORE.ProtocolError("请求必须是 JSON 对象")
        action = request.get("action")
        if action == "ping":
            return {
                "ok": True,
                "service": "ming-tea",
                "dsh_available": self.runtime.adapter.available(),
                "protocol": "ming.tea.ipc.v1",
            }
        if action == "create_session":
            return {"ok": True, "session": self.runtime.create_session(request.get("scene", "learning"))}
        if action == "tool_request":
            events = self.runtime.submit_tool_request(
                request.get("session_id", ""),
                request.get("tool_id", ""),
                request.get("arguments", {}),
            )
            return {"ok": True, "events": events}
        if action == "cancel_session":
            return {"ok": self.runtime.cancel_session(request.get("session_id", ""))}
        if action == "status":
            return {
                "ok": True,
                "service": "ming-tea",
                "sessions": len(self.runtime.sessions),
                "dsh_available": self.runtime.adapter.available(),
            }
        raise CORE.ProtocolError("未知 IPC 操作：%s" % action)

    def _handle_client(self, client):
        with client:
            reader = client.makefile("r", encoding="utf-8")
            writer = client.makefile("w", encoding="utf-8")
            for line in reader:
                if not line.strip():
                    continue
                try:
                    response = self.handle_request(json.loads(line))
                except (CORE.ProtocolError, ValueError, json.JSONDecodeError) as error:
                    response = {"ok": False, "error": str(error)}
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                writer.flush()

    def serve_forever(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.socket_path))
        os.chmod(str(self.socket_path), 0o600)
        self._server.listen(8)
        self._server.settimeout(0.5)
        try:
            while not self._stopping.is_set():
                try:
                    client, _ = self._server.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()
        finally:
            self.close()

    def close(self):
        self._stopping.set()
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


def default_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return pathlib.Path(runtime_dir) / "ming-tea.sock"


def main(argv=None):
    parser = argparse.ArgumentParser(description="铭荼本地 DSH 适配服务")
    parser.add_argument("--socket", default=os.environ.get(MING_TEA_SOCKET, str(default_socket_path())))
    args = parser.parse_args(argv)
    server = MingTeaIPCServer(args.socket)
    signal.signal(signal.SIGTERM, lambda *_: server.close())
    signal.signal(signal.SIGINT, lambda *_: server.close())
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
