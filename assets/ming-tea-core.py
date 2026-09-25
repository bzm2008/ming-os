#!/usr/bin/env python3
"""Stable Ming Tea contracts around the fast-moving DeepSeek Harness runtime."""

import copy
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import time
import uuid


EVENT_TYPES = frozenset({
    "session.created",
    "plan.updated",
    "tool.requested",
    "approval.requested",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "session.completed",
})

SCENES = {
    "office": {
        "label": "办公模式",
        "prompt": "协助处理文档、表格、演示文稿、网页和文件；涉及发送、上传或删除时先请求确认。",
        "tools": ["browser", "files", "office", "terminal"],
        "risk_profile": "balanced",
    },
    "development": {
        "label": "开发模式",
        "prompt": "读取项目、分析日志、运行测试并展示代码变更；特权命令和系统级修改必须先请求确认。",
        "tools": ["files", "terminal", "browser", "office"],
        "risk_profile": "developer",
    },
    "learning": {
        "label": "辅助学习模式",
        "prompt": "优先解释、拆解和演示；仅在用户明确要求时执行会改变系统或外部状态的动作。",
        "tools": ["browser", "files", "office", "terminal"],
        "risk_profile": "guided",
    },
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
)


class ProtocolError(ValueError):
    pass


class PluginCatalogError(ValueError):
    pass


def redact_secrets(value):
    """Return a deep redacted copy suitable for UI events and local audit logs."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in {
                    "api_key", "apikey", "password", "token", "authorization"}:
                redacted[key] = "[已隐藏]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[已隐藏]", text)
    return text


class PermissionPolicy:
    """Fail-closed, session-local permission policy for Ming Tea tools."""

    AUTO_TOOLS = frozenset({
        "file.read", "file.list", "system.status", "browser.read",
        "browser.open", "office.read", "diagnostic.run", "app.open",
    })
    CONFIRM_TOOLS = frozenset({
        "file.delete", "file.upload", "browser.submit", "browser.upload",
        "office.write", "package.install", "system.modify", "account.modify",
    })
    _DANGEROUS_COMMAND = re.compile(
        r"(^|[;&|]\s*)(sudo|su|doas|pkexec|rm|mv|chmod|chown|dd|mkfs|shutdown|reboot|apt|apt-get|dpkg)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        self._approvals = {}

    def decide(self, tool_id, arguments=None):
        arguments = arguments if isinstance(arguments, dict) else {}
        if tool_id == "terminal.run":
            command = str(arguments.get("command") or "").strip()
            return "confirm" if self._DANGEROUS_COMMAND.search(command) else "allow"
        if tool_id in self.AUTO_TOOLS:
            return "allow"
        if tool_id in self.CONFIRM_TOOLS:
            return "confirm"
        return "deny"

    def issue_approval(self, tool_id, arguments=None):
        token = secrets.token_urlsafe(24)
        self._approvals[token] = {
            "tool_id": str(tool_id),
            "arguments": redact_secrets(arguments or {}),
            "issued_at": int(time.time()),
        }
        return token

    def consume_approval(self, token, tool_id):
        approval = self._approvals.pop(str(token), None)
        return bool(approval and approval["tool_id"] == str(tool_id))

    def clear(self):
        self._approvals.clear()


class PluginCatalog:
    REQUIRED_FIELDS = frozenset({
        "id", "name", "source", "revision", "license", "runtime",
        "permissions", "debian13", "low_resource", "status",
    })

    def __init__(self, path):
        self.path = pathlib.Path(path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PluginCatalogError("无法读取插件清单：%s" % error)
        plugins = payload.get("plugins") if isinstance(payload, dict) else None
        if not isinstance(plugins, list) or not plugins:
            raise PluginCatalogError("插件清单必须包含非空 plugins 数组")
        checked = []
        identifiers = set()
        for plugin in plugins:
            if not isinstance(plugin, dict):
                raise PluginCatalogError("插件记录必须是对象")
            missing = self.REQUIRED_FIELDS.difference(plugin)
            if missing:
                raise PluginCatalogError(
                    "插件 %s 缺少字段：%s" % (
                        plugin.get("id", "<unknown>"), ", ".join(sorted(missing))))
            if not isinstance(plugin["permissions"], list) or not plugin["permissions"]:
                raise PluginCatalogError("插件 %s 必须声明权限" % plugin["id"])
            if plugin["id"] in identifiers:
                raise PluginCatalogError("插件 ID 重复：%s" % plugin["id"])
            identifiers.add(plugin["id"])
            checked.append(copy.deepcopy(plugin))
        self.plugins = tuple(checked)


class DshAdapter:
    """Small process boundary that keeps DSH churn out of the GTK application."""

    def __init__(self, command=None):
        self.command = list(command or ["dsh", "stdio", "--json"])
        self.process = None

    def available(self):
        return bool(self.command and shutil.which(self.command[0]))

    def start(self):
        if not self.available():
            return {
                "ok": False,
                "error": "DSH 运行时不可用；铭荼仍可打开历史记录和设置。",
            }
        if self.process and self.process.poll() is None:
            return {"ok": True, "pid": self.process.pid}
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as error:
            return {"ok": False, "error": "无法启动 DSH：%s" % error}
        return {"ok": True, "pid": self.process.pid}

    def stop(self):
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)


class MingTeaRuntime:
    def __init__(self, audit_path=None, permission_policy=None, adapter=None):
        default_audit = pathlib.Path(
            os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")
        ) / "ming-os" / "ming-tea-audit.jsonl"
        self.audit_path = pathlib.Path(audit_path or default_audit)
        self.permission_policy = permission_policy or PermissionPolicy()
        self.adapter = adapter or DshAdapter()
        self.sessions = {}

    def normalize_event(self, event):
        if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
            raise ProtocolError("未知的铭荼事件类型")
        normalized = redact_secrets(copy.deepcopy(event))
        normalized.setdefault("timestamp", int(time.time()))
        return normalized

    def _write_audit(self, event):
        normalized = self.normalize_event(event)
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
            os.chmod(str(self.audit_path), 0o600)
        except OSError:
            pass
        return normalized

    def create_session(self, scene):
        if scene not in SCENES:
            raise ValueError("未知场景：%s" % scene)
        session_id = uuid.uuid4().hex
        session = {
            "session_id": session_id,
            "scene": scene,
            "created_at": int(time.time()),
            "state": "active",
        }
        self.sessions[session_id] = session
        self._write_audit({
            "type": "session.created",
            "session_id": session_id,
            "scene": scene,
        })
        return copy.deepcopy(session)

    def submit_tool_request(self, session_id, tool_id, arguments=None):
        session = self.sessions.get(session_id)
        if not session or session["state"] != "active":
            raise ProtocolError("会话不存在或已经结束")
        arguments = redact_secrets(arguments or {})
        requested = self._write_audit({
            "type": "tool.requested",
            "session_id": session_id,
            "tool_id": tool_id,
            "arguments": arguments,
        })
        decision = self.permission_policy.decide(tool_id, arguments)
        if decision == "deny":
            failed = self._write_audit({
                "type": "tool.failed",
                "session_id": session_id,
                "tool_id": tool_id,
                "error": "工具未在铭荼权限清单中注册",
            })
            return [requested, failed]
        if decision == "confirm":
            token = self.permission_policy.issue_approval(tool_id, arguments)
            approval = self._write_audit({
                "type": "approval.requested",
                "session_id": session_id,
                "tool_id": tool_id,
                "approval_token": token,
                "arguments": arguments,
            })
            return [requested, approval]
        started = self._write_audit({
            "type": "tool.started",
            "session_id": session_id,
            "tool_id": tool_id,
        })
        return [requested, started]

    def cancel_session(self, session_id):
        session = self.sessions.get(session_id)
        if not session:
            return False
        session["state"] = "cancelled"
        self.permission_policy.clear()
        self._write_audit({
            "type": "session.completed",
            "session_id": session_id,
            "status": "cancelled",
        })
        return True
