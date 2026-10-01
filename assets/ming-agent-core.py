#!/usr/bin/env python3
"""Shared policy and tools for the Ming OS Agent CLI.

This module intentionally exposes typed, allowlisted operations.  It never
accepts a shell fragment and it keeps foreground control behind an explicit
per-login grant.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Iterable


PROTOCOL = "ming.agent.v1"
GRANT_SCOPES = {
    "screen.read",
    "screen.input",
    "window.control",
    "files.user",
    "system.settings",
    "system.power",
}
MAX_FILE_BYTES = 1024 * 1024
SENSITIVE_HOME_DIRS = {".ssh", ".gnupg", ".codex", ".config"}


def request_id() -> str:
    return uuid.uuid4().hex


def envelope(request: str | None, ok: bool, state: str, message: str, **data: Any) -> dict[str, Any]:
    result = {
        "protocol": PROTOCOL,
        "request_id": request or request_id(),
        "ok": bool(ok),
        "state": state,
        "message": message,
    }
    result.update(data)
    return result


def _atomic_write(path: pathlib.Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ForegroundGrantManager:
    def __init__(self, runtime_root: pathlib.Path | str | None = None, uid: int | None = None, audit_path=None):
        default_root = pathlib.Path(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        ) / "ming-os" / "agent"
        self.runtime_root = pathlib.Path(runtime_root or default_root)
        self.uid = os.getuid() if uid is None else int(uid)
        self.grant_path = self.runtime_root / "foreground-grant.json"
        self.audit_path = pathlib.Path(audit_path or (self.runtime_root / "foreground-audit.jsonl"))

    def _audit(self, action: str, ok: bool, **data: Any) -> None:
        self.audit_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {"time": time.time(), "uid": self.uid, "action": action, "ok": bool(ok), **data}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        os.chmod(self.audit_path, 0o600)

    def _read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.grant_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(data, dict) or int(data.get("uid", -1)) != self.uid:
            return None
        if data.get("login_session") and data.get("login_session") != os.environ.get("XDG_SESSION_ID", data.get("login_session")):
            return None
        return data

    def grant(self, scopes: Iterable[str], session_id: str, display: str, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            result = envelope(None, False, "confirmation_required", "授予前台控制权需要明确确认。")
            self._audit("foreground.grant", False, state=result["state"])
            return result
        scope_set = {str(scope) for scope in scopes}
        if not scope_set or not scope_set.issubset(GRANT_SCOPES):
            result = envelope(None, False, "invalid_scope", "前台授权范围无效。", scopes=sorted(scope_set))
            self._audit("foreground.grant", False, state=result["state"])
            return result
        if not session_id or not display:
            result = envelope(None, False, "invalid_request", "前台授权需要登录会话和显示器。")
            self._audit("foreground.grant", False, state=result["state"])
            return result
        grant = {
            "uid": self.uid,
            "login_session": str(session_id),
            "display": str(display),
            "scopes": sorted(scope_set),
            "issued_at": time.time(),
            "mode": "session",
        }
        _atomic_write(self.grant_path, grant)
        self._audit("foreground.grant", True, scopes=grant["scopes"], display=display, session_id=session_id)
        return envelope(None, True, "granted", "前台控制权已授予当前登录会话。", grant=grant)

    def status(self) -> dict[str, Any]:
        grant = self._read()
        if grant is None:
            return envelope(None, True, "inactive", "当前没有有效的前台授权。", grant=None)
        return envelope(None, True, "active", "当前登录会话拥有前台授权。", grant=grant)

    def require(self, scope: str, session_id: str, display: str) -> dict[str, Any]:
        grant = self._read()
        valid = bool(
            grant
            and scope in grant.get("scopes", [])
            and grant.get("login_session") == session_id
            and grant.get("display") == display
        )
        if not valid:
            result = envelope(None, False, "foreground_not_authorized", "当前登录会话没有所需的前台授权。", scope=scope)
            self._audit("foreground.require", False, scope=scope, session_id=session_id, display=display)
            return result
        self._audit("foreground.require", True, scope=scope, session_id=session_id, display=display)
        return envelope(None, True, "authorized", "前台授权有效。", scope=scope)

    def revoke(self, session_id: str | None = None) -> dict[str, Any]:
        grant = self._read()
        if session_id and grant and grant.get("login_session") != session_id:
            return envelope(None, False, "session_mismatch", "不能撤销其他登录会话的授权。")
        try:
            self.grant_path.unlink()
        except FileNotFoundError:
            pass
        self._audit("foreground.revoke", True, session_id=session_id or "")
        return envelope(None, True, "revoked", "前台授权已撤销。")


class ControlledFiles:
    def __init__(self, roots: Iterable[pathlib.Path | str] | None = None, max_bytes: int = MAX_FILE_BYTES):
        default_roots = [pathlib.Path.home() / name for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")]
        self.roots = tuple(pathlib.Path(item).expanduser().resolve() for item in (roots or default_roots))
        self.max_bytes = int(max_bytes)

    def _safe(self, path: pathlib.Path | str, allow_missing: bool = False) -> pathlib.Path | None:
        candidate = pathlib.Path(path).expanduser()
        if not candidate.is_absolute():
            return None
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=False)
        if any(resolved == root or root in resolved.parents for root in self.roots):
            if resolved.name in SENSITIVE_HOME_DIRS and resolved.parent == pathlib.Path.home().resolve():
                return None
            if not allow_missing and not resolved.exists():
                return None
            return resolved
        return None

    def read(self, path: pathlib.Path | str) -> dict[str, Any]:
        safe = self._safe(path)
        if safe is None or not safe.is_file():
            return envelope(None, False, "path_rejected", "文件路径不在受控范围内。")
        if safe.stat().st_size > self.max_bytes:
            return envelope(None, False, "size_rejected", "文件超过 Agent 读取大小限制。")
        try:
            return envelope(None, True, "ready", "文件已读取。", path=str(safe), content=safe.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            return envelope(None, False, "read_failed", str(exc))

    def list(self, path: pathlib.Path | str) -> dict[str, Any]:
        safe = self._safe(path)
        if safe is None or not safe.is_dir():
            return envelope(None, False, "path_rejected", "目录路径不在受控范围内。")
        entries = []
        for item in sorted(safe.iterdir(), key=lambda value: value.name.lower()):
            if item.is_symlink():
                continue
            entries.append({"name": item.name, "path": str(item), "directory": item.is_dir(), "size": item.stat().st_size if item.is_file() else None})
        return envelope(None, True, "ready", "目录已读取。", path=str(safe), entries=entries)

    def write(self, path: pathlib.Path | str, content: str, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return envelope(None, False, "confirmation_required", "写入文件需要明确确认。")
        if not isinstance(content, str) or len(content.encode("utf-8")) > self.max_bytes:
            return envelope(None, False, "size_rejected", "文件超过 Agent 写入大小限制。")
        safe = self._safe(path, allow_missing=True)
        if safe is None or safe.parent == safe:
            return envelope(None, False, "path_rejected", "文件路径不在受控范围内。")
        try:
            safe.parent.mkdir(parents=True, exist_ok=True)
            safe.write_text(content, encoding="utf-8")
            return envelope(None, True, "ready", "文件已写入。", path=str(safe))
        except OSError as exc:
            return envelope(None, False, "write_failed", str(exc))

    def copy(self, source, destination, confirmed=False):
        return self._move_or_copy("copy", source, destination, confirmed)

    def move(self, source, destination, confirmed=False):
        return self._move_or_copy("move", source, destination, confirmed)

    def trash(self, path, confirmed=False):
        if not confirmed:
            return envelope(None, False, "confirmation_required", "移入回收站需要明确确认。")
        safe = self._safe(path)
        if safe is None:
            return envelope(None, False, "path_rejected", "文件路径不在受控范围内。")
        result = run_fixed(["gio", "trash", str(safe)])
        if result.get("ok"):
            return envelope(None, True, "ready", "文件已移入回收站。", path=str(safe))
        return envelope(None, False, "trash_failed", result.get("stderr") or "无法移入回收站。")

    def open(self, path):
        safe = self._safe(path)
        if safe is None:
            return envelope(None, False, "path_rejected", "文件路径不在受控范围内。")
        result = run_fixed(["xdg-open", str(safe)])
        return envelope(None, result.get("ok"), result.get("state", "action_failed"), "已打开文件。" if result.get("ok") else (result.get("stderr") or "无法打开文件。"), path=str(safe))

    def _move_or_copy(self, action, source, destination, confirmed):
        if not confirmed:
            return envelope(None, False, "confirmation_required", f"{action} 文件需要明确确认。")
        src = self._safe(source)
        dst = self._safe(destination, allow_missing=True)
        if src is None or dst is None or not src.exists():
            return envelope(None, False, "path_rejected", "文件路径不在受控范围内。")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if action == "copy":
                shutil.copy2(src, dst)
            else:
                shutil.move(str(src), str(dst))
            return envelope(None, True, "ready", f"文件已{action}。", source=str(src), destination=str(dst))
        except OSError as exc:
            return envelope(None, False, f"{action}_failed", str(exc))


def run_fixed(command: list[str], timeout: float = 8, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "state": "runtime_missing", "error": str(exc)}
    return {"ok": completed.returncode == 0, "state": "ready" if completed.returncode == 0 else "action_failed", "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip(), "returncode": completed.returncode}
