#!/usr/bin/env python3
"""Versioned local JSON bridge for isolated Ming agent sessions."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time
import uuid
from typing import Any


PROTOCOL = "ming.agent.v1"
ROOT = pathlib.Path(__file__).resolve().parent
_runtime_spec = importlib.util.spec_from_file_location(
    "ming_agent_runtime", ROOT / "ming-agent-runtime.py"
)
if _runtime_spec is None or _runtime_spec.loader is None:  # pragma: no cover
    raise RuntimeError("ming-agent-runtime.py is unavailable")
_runtime = importlib.util.module_from_spec(_runtime_spec)
_runtime_spec.loader.exec_module(_runtime)


def envelope(request_id: str, ok: bool, state: str, message: str, **data: Any) -> dict[str, Any]:
    result = {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "ok": bool(ok),
        "state": state,
        "message": message,
    }
    result.update(data)
    return result


def _request_id() -> str:
    return uuid.uuid4().hex


def _read_state(runtime_root: pathlib.Path, session_id: str) -> dict[str, Any] | None:
    if not _runtime.SESSION_RE.fullmatch(session_id or ""):
        return None
    path = runtime_root / "sessions" / session_id / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if int(state.get("uid", -1)) != os.getuid():
        return None
    return state


def capabilities(runtime_root: pathlib.Path) -> dict[str, Any]:
    return envelope(
        _request_id(), True, "ready", "agent bridge capabilities are available.",
        capabilities={
            "session": ["create", "status", "stop"],
            "screen": ["status", "windows", "screenshot", "activate", "click", "type", "key"],
            "store": ["search", "details", "inventory", "updates", "log", "install", "update", "remove", "refresh"],
        },
        runtime_root=str(runtime_root),
    )


def _session_or_error(runtime_root: pathlib.Path, session_id: str, foreground_display: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    request_id = _request_id()
    state = _read_state(runtime_root, session_id)
    if state is None:
        return None, envelope(request_id, False, "session_unavailable", "后台 agent 会话不存在或不属于当前用户。")
    if foreground_display and state.get("display") == foreground_display:
        return None, envelope(request_id, False, "foreground_display_rejected", "不能通过前台显示会话执行 agent 操作。")
    return state, None


def _run(command: list[str], env: dict[str, str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _session_env(state: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DISPLAY": str(state["display"]),
            "DBUS_SESSION_BUS_ADDRESS": str(state["dbus_session_bus_address"]),
            "XDG_RUNTIME_DIR": str(state["runtime_dir"]),
        }
    )
    return env


def screen_action(args: list[str], runtime_root: pathlib.Path, foreground_display: str | None) -> dict[str, Any]:
    if not args or args[0] not in {"status", "windows", "screenshot", "activate", "click", "type", "key"}:
        return envelope(_request_id(), False, "action_not_allowed", "不允许的屏幕操作。")
    action = args[0]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action")
    parser.add_argument("--session", required=True)
    parser.add_argument("--window")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--text")
    parser.add_argument("--key")
    parser.add_argument("--path")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return envelope(_request_id(), False, "invalid_request", "屏幕操作参数无效。")
    state, error = _session_or_error(runtime_root, parsed.session, foreground_display)
    if error:
        return error
    assert state is not None
    env = _session_env(state)
    request_id = _request_id()
    if action == "status":
        return envelope(request_id, True, "ready", "后台屏幕会话可用。", display=state["display"], session_id=parsed.session)
    if action == "windows":
        completed = _run(["wmctrl", "-lxG", "-p"], env)
        if completed.returncode != 0:
            return envelope(request_id, False, "runtime_missing", completed.stderr.strip() or "wmctrl 不可用。")
        windows = []
        for line in completed.stdout.splitlines():
            fields = line.split(None, 9)
            if len(fields) < 10:
                continue
            windows.append({"id": fields[0], "desktop": fields[1], "pid": fields[2], "geometry": fields[3:7], "class": fields[7], "title": fields[9]})
        return envelope(request_id, True, "ready", "已读取窗口列表。", windows=windows)
    if action == "screenshot":
        output_dir = runtime_root / "sessions" / parsed.session / "screenshots"
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = pathlib.Path(parsed.path) if parsed.path else output_dir / f"{int(time.time())}-{secrets.token_hex(4)}.png"
        try:
            path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            return envelope(request_id, False, "path_rejected", "截图路径必须位于 agent 会话目录。")
        completed = _run(["gnome-screenshot", "-f", str(path)], env, timeout=10)
        if completed.returncode != 0 or not path.is_file():
            return envelope(request_id, False, "screenshot_failed", completed.stderr.strip() or "截图失败。")
        os.chmod(path, 0o600)
        return envelope(request_id, True, "ready", "截图已生成。", path=str(path), display=state["display"])
    if action == "activate":
        if not parsed.window:
            return envelope(request_id, False, "invalid_request", "activate 需要 --window。")
        completed = _run(["wmctrl", "-ia", parsed.window], env)
    elif action == "click":
        if parsed.x is None or parsed.y is None:
            return envelope(request_id, False, "invalid_request", "click 需要 --x 和 --y。")
        completed = _run(["xdotool", "mousemove", str(parsed.x), str(parsed.y), "click", "1"], env)
    elif action == "type":
        if not parsed.text or len(parsed.text) > 4096:
            return envelope(request_id, False, "invalid_request", "type 需要不超过 4096 字符的 --text。")
        completed = _run(["xdotool", "type", "--clearmodifiers", "--", parsed.text], env)
    else:
        if not parsed.key or len(parsed.key) > 128:
            return envelope(request_id, False, "invalid_request", "key 需要有效的 --key。")
        completed = _run(["xdotool", "key", "--clearmodifiers", "--", parsed.key], env)
    if completed.returncode != 0:
        return envelope(request_id, False, "action_failed", completed.stderr.strip() or "屏幕操作失败。")
    return envelope(request_id, True, "ready", "屏幕操作已完成。", display=state["display"], session_id=parsed.session)


def dispatch(argv: list[str], runtime_root: pathlib.Path | None = None, foreground_display: str | None = None) -> dict[str, Any]:
    runtime_root = pathlib.Path(runtime_root or (pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "ming-os" / "agent"))
    foreground_display = os.environ.get("DISPLAY", "") if foreground_display is None else foreground_display
    if not argv:
        return envelope(_request_id(), False, "invalid_request", "缺少 agent bridge 操作。")
    if argv[0] == "capabilities":
        return capabilities(runtime_root)
    if argv[0] == "screen":
        return screen_action(argv[1:], runtime_root, foreground_display)
    if argv[0] == "store":
        return envelope(_request_id(), False, "not_implemented", "应用商店 agent 接口尚未启用。")
    return envelope(_request_id(), False, "action_not_allowed", "不允许的 agent 操作。")


def main(argv: list[str] | None = None) -> int:
    result = dispatch(list(argv if argv is not None else sys.argv[1:]))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
