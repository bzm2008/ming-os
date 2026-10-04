#!/usr/bin/env python3
"""Manage isolated per-user X11 sessions for Ming's local agent bridge."""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import pathlib
import signal
import socket
import subprocess
import time
import uuid
from typing import Any


PROTOCOL = "ming.agent.v1"
SESSION_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
DISPLAY_MIN = 90
DISPLAY_MAX = 109


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _json_result(request_id: str, ok: bool, state: str, message: str, **data: Any) -> dict[str, Any]:
    result = {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "ok": bool(ok),
        "state": state,
        "message": message,
    }
    result.update(data)
    return result


class _ProcessRef:
    def __init__(self, pid: int):
        self.pid = int(pid)

    def poll(self) -> int | None:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0
        except PermissionError:
            return None
        return None

    def terminate(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class _SubprocessRunner:
    def popen(self, command: list[str], env: dict[str, str]) -> subprocess.Popen[Any]:
        return subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def start_dbus(self, address: str, env: dict[str, str]) -> _ProcessRef:
        completed = subprocess.run(
            [
                "dbus-daemon",
                "--session",
                f"--address={address}",
                "--fork",
                "--print-pid=1",
            ],
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        pid_text = completed.stdout.strip().splitlines()[-1]
        return _ProcessRef(int(pid_text))

    def terminate(self, pid: int) -> None:
        _ProcessRef(pid).terminate()


class AgentSessionManager:
    def __init__(
        self,
        runtime_root: pathlib.Path | None = None,
        runner: Any | None = None,
        foreground_display: str | None = None,
    ):
        default_root = pathlib.Path(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        ) / "ming-os" / "agent"
        self.runtime_root = pathlib.Path(runtime_root or default_root)
        self.sessions_root = self.runtime_root / "sessions"
        self.runner = runner or _SubprocessRunner()
        self.foreground_display = foreground_display or os.environ.get("DISPLAY", "")

    def _valid_session(self, session_id: str) -> bool:
        return bool(SESSION_RE.fullmatch(session_id or ""))

    def _state_path(self, session_id: str) -> pathlib.Path:
        return self.sessions_root / session_id / "state.json"

    def _display_available(self, display: str) -> bool:
        number = display.removeprefix(":").split(".", 1)[0]
        if not number.isdigit():
            return False
        socket_path = pathlib.Path("/tmp/.X11-unix") / f"X{number}"
        return not socket_path.exists()

    def _allocate_display(self, requested: str | None) -> str:
        if requested:
            if requested == self.foreground_display or not self._display_available(requested):
                raise RuntimeError("display_unavailable")
            return requested
        for number in range(DISPLAY_MIN, DISPLAY_MAX + 1):
            display = f":{number}"
            if display != self.foreground_display and self._display_available(display):
                return display
        raise RuntimeError("no isolated X11 display is available")

    def _session_env(self, display: str, session_dir: pathlib.Path) -> dict[str, str]:
        bus_path = session_dir / "bus"
        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": display,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_path}",
                "XDG_RUNTIME_DIR": str(self.runtime_root),
                "MING_AGENT_SESSION": session_dir.name,
            }
        )
        return env

    def _terminate(self, pid: int) -> None:
        terminate = getattr(self.runner, "terminate", None)
        if terminate is not None:
            terminate(pid)
            return
        _ProcessRef(pid).terminate()

    def _cleanup_processes(self, processes: list[Any]) -> None:
        for process in reversed(processes):
            try:
                process.terminate()
            except (AttributeError, OSError):
                self._terminate(int(process.pid))

    def create(self, session_id: str, display: str | None = None) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        if not self._valid_session(session_id):
            return _json_result(request_id, False, "invalid_session", "会话名称不合法。")
        if os.geteuid() == 0:
            return _json_result(request_id, False, "root_rejected", "agent 会话不能以 root 运行。")
        if display and display == self.foreground_display:
            return _json_result(request_id, False, "foreground_display_rejected", "不能复用前台显示会话。")

        session_dir = self.sessions_root / session_id
        state_path = session_dir / "state.json"
        if state_path.exists():
            return _json_result(request_id, False, "already_exists", "agent 会话已存在。")
        processes: list[Any] = []
        try:
            selected_display = self._allocate_display(display)
            self.sessions_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            session_dir.mkdir(mode=0o700)
            os.chmod(session_dir, 0o700)
            env = self._session_env(selected_display, session_dir)
            xvfb = self.runner.popen(
                [
                    "Xvfb",
                    selected_display,
                    "-screen",
                    "0",
                    "1280x800x24",
                    "-nolisten",
                    "tcp",
                    "-noreset",
                ],
                env=env,
            )
            processes.append(xvfb)
            time.sleep(0.05)
            if xvfb.poll() is not None:
                raise RuntimeError("Xvfb 未能启动。")
            dbus = self.runner.start_dbus(env["DBUS_SESSION_BUS_ADDRESS"], env)
            processes.append(dbus)
            wm = self.runner.popen(["xfwm4", "--replace"], env=env)
            processes.append(wm)
            state = {
                "protocol": PROTOCOL,
                "session_id": session_id,
                "uid": os.getuid(),
                "display": selected_display,
                "dbus_session_bus_address": env["DBUS_SESSION_BUS_ADDRESS"],
                "runtime_dir": str(self.runtime_root),
                "pids": {"xvfb": int(xvfb.pid), "dbus": int(dbus.pid), "wm": int(wm.pid)},
                "created_at": _now(),
            }
            state_path.write_text(json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n", encoding="ascii")
            os.chmod(state_path, 0o600)
            return _json_result(request_id, True, "created", "后台 agent 会话已创建。", session=state)
        except Exception as exc:
            self._cleanup_processes(processes)
            try:
                state_path.unlink(missing_ok=True)
                session_dir.rmdir()
            except OSError:
                pass
            message = str(exc)
            state = "display_unavailable" if message == "display_unavailable" else "startup_failed"
            return _json_result(request_id, False, state, message)

    def status(self, session_id: str) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        if not self._valid_session(session_id):
            return _json_result(request_id, False, "invalid_session", "会话名称不合法。")
        path = self._state_path(session_id)
        if not path.is_file():
            return _json_result(request_id, True, "not_found", "后台 agent 会话不存在。")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if int(state.get("uid", -1)) != os.getuid():
                return _json_result(request_id, False, "owner_mismatch", "会话不属于当前用户。")
            alive = {}
            for name, pid in state.get("pids", {}).items():
                alive[name] = _ProcessRef(int(pid)).poll() is None
            state["alive"] = alive
            state["running"] = all(alive.values())
            return _json_result(request_id, True, "running" if state["running"] else "degraded", "已读取后台会话状态。", session=state)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return _json_result(request_id, False, "state_invalid", str(exc))

    def stop(self, session_id: str) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        if not self._valid_session(session_id):
            return _json_result(request_id, False, "invalid_session", "会话名称不合法。")
        path = self._state_path(session_id)
        if not path.is_file():
            return _json_result(request_id, True, "not_found", "后台 agent 会话不存在。")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if int(state.get("uid", -1)) != os.getuid():
                return _json_result(request_id, False, "owner_mismatch", "会话不属于当前用户。")
            for pid in state.get("pids", {}).values():
                self._terminate(int(pid))
            session_dir = path.parent
            path.unlink(missing_ok=True)
            session_dir.rmdir()
            return _json_result(request_id, True, "stopped", "后台 agent 会话已停止。")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return _json_result(request_id, False, "stop_failed", str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ming-agent-runtime")
    parser.add_argument("action", choices=("create", "status", "stop"))
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("--display")
    args = parser.parse_args(argv)
    manager = AgentSessionManager()
    if args.action == "create":
        if not args.session_id:
            parser.error("create requires session_id")
        result = manager.create(args.session_id, display=args.display)
    elif args.action == "status":
        if not args.session_id:
            parser.error("status requires session_id")
        result = manager.status(args.session_id)
    else:
        if not args.session_id:
            parser.error("stop requires session_id")
        result = manager.stop(args.session_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
