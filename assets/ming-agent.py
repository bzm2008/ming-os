#!/usr/bin/env python3
"""Unified JSON CLI for the Ming OS Agent integration."""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import json
import os
import pathlib
import sys
import uuid


ROOT = pathlib.Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{path} 不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load("ming_agent_core", ROOT / "ming-agent-core.py")
BRIDGE = _load("ming_agent_bridge_compat", ROOT / "ming-agent-bridge.py")
RUNTIME = _load("ming_agent_runtime_cli", ROOT / "ming-agent-runtime.py")
_settings_backend_path = next(
    (path for path in (
        ROOT / "ming-settings-backend.py",
        pathlib.Path("/usr/local/lib/ming-os/ming-settings-backend"),
        pathlib.Path("/usr/local/lib/ming-os/ming-settings-backend.py"),
    ) if path.is_file()),
    ROOT / "ming-settings-backend.py",
)
SETTINGS = _load("ming_settings_backend_cli", _settings_backend_path)

PROTOCOL = CORE.PROTOCOL


def _runtime_root(value=None):
    if value:
        return pathlib.Path(value)
    return pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "ming-os" / "agent"


def _env(environ=None):
    value = dict(os.environ)
    if environ:
        value.update(environ)
    return value


def _confirm(args):
    return "--confirm" in args


def _remove_flag(args, flag):
    return [item for item in args if item != flag]


def capabilities(runtime_root):
    return CORE.envelope(
        None, True, "ready", "Ming Agent CLI capabilities are available.",
        capabilities={
            "session": ["create", "status", "stop"],
            "screen": ["status", "windows", "screenshot", "activate", "click", "type", "key"],
            "settings": ["list", "get", "set"],
            "device": ["status", "wifi", "audio", "brightness", "bluetooth", "battery", "ethernet"],
            "apps": ["list", "launch"],
            "files": ["list", "read", "write", "copy", "move", "trash", "open"],
            "store": ["search", "details", "inventory", "updates", "log", "install", "update", "remove", "refresh"],
            "system": ["status", "diagnostics", "power"],
            "foreground": ["status", "grant", "revoke", "stop"],
        },
        permissions={
            "foreground_default": "disabled",
            "foreground_grant": "session",
            "arbitrary_shell": False,
            "polkit_for_system_writes": True,
        },
        runtime_root=str(runtime_root),
    )


def _foreground_manager(runtime_root):
    return CORE.ForegroundGrantManager(runtime_root)


def foreground_action(args, runtime_root, environ):
    action = args[0] if args else ""
    manager = _foreground_manager(runtime_root)
    session_id = environ.get("XDG_SESSION_ID", "unknown")
    display = environ.get("DISPLAY", "")
    if action == "status":
        return manager.status()
    if action == "grant":
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("action")
        parser.add_argument("--scope", required=True)
        parser.add_argument("--confirm", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return CORE.envelope(None, False, "invalid_request", "前台授权参数无效。")
        scopes = {item.strip() for item in parsed.scope.split(",") if item.strip()}
        return manager.grant(scopes, session_id, display, confirmed=parsed.confirm)
    if action in {"revoke", "stop"}:
        result = manager.revoke(session_id=session_id)
        if action == "stop":
            runtime = RUNTIME.AgentSessionManager(runtime_root=runtime_root)
            for session_dir in runtime.sessions_root.iterdir() if runtime.sessions_root.exists() else ():
                if session_dir.is_dir():
                    runtime.stop(session_dir.name)
        return result
    return CORE.envelope(None, False, "action_not_allowed", "不允许的前台授权操作。")


def foreground_screen_action(args, runtime_root, environ):
    action = args[0] if args else ""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action")
    parser.add_argument("--window")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--text")
    parser.add_argument("--key")
    parser.add_argument("--path")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return CORE.envelope(None, False, "invalid_request", "前台屏幕操作参数无效。")
    scope = "screen.read" if action in {"status", "windows", "screenshot"} else (
        "window.control" if action == "activate" else "screen.input")
    auth = _foreground_manager(runtime_root).require(
        scope, environ.get("XDG_SESSION_ID", "unknown"), environ.get("DISPLAY", ""))
    if not auth["ok"]:
        return auth
    env = dict(environ)
    if action == "status":
        return CORE.envelope(None, True, "ready", "前台屏幕会话可用。", display=env.get("DISPLAY", ""))
    if action == "windows":
        result = CORE.run_fixed(["wmctrl", "-lxG", "-p"], env=env)
        if not result["ok"]:
            return CORE.envelope(None, False, result["state"], result.get("stderr") or "窗口列表不可用。")
        return CORE.envelope(None, True, "ready", "已读取前台窗口列表。", output=result.get("stdout", ""))
    if action == "screenshot":
        output_dir = runtime_root / "foreground-screenshots"
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = pathlib.Path(parsed.path) if parsed.path else output_dir / f"{int(__import__('time').time())}-{uuid.uuid4().hex[:8]}.png"
        try:
            path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            return CORE.envelope(None, False, "path_rejected", "前台截图路径必须位于 Agent 运行目录。")
        result = CORE.run_fixed(["gnome-screenshot", "-f", str(path)], timeout=10, env=env)
        if not result["ok"] or not path.is_file():
            return CORE.envelope(None, False, "screenshot_failed", result.get("stderr") or "截图失败。")
        os.chmod(path, 0o600)
        return CORE.envelope(None, True, "ready", "前台截图已生成。", path=str(path), display=env.get("DISPLAY", ""))
    if action == "activate":
        if not parsed.window:
            return CORE.envelope(None, False, "invalid_request", "activate 需要 --window。")
        command = ["wmctrl", "-ia", parsed.window]
    elif action == "click":
        if parsed.x is None or parsed.y is None:
            return CORE.envelope(None, False, "invalid_request", "click 需要 --x 和 --y。")
        command = ["xdotool", "mousemove", str(parsed.x), str(parsed.y), "click", "1"]
    elif action == "type":
        if not parsed.text or len(parsed.text) > 4096:
            return CORE.envelope(None, False, "invalid_request", "type 需要不超过 4096 字符的 --text。")
        command = ["xdotool", "type", "--clearmodifiers", "--", parsed.text]
    elif action == "key":
        if not parsed.key or len(parsed.key) > 128:
            return CORE.envelope(None, False, "invalid_request", "key 需要有效的 --key。")
        command = ["xdotool", "key", "--clearmodifiers", "--", parsed.key]
    else:
        return CORE.envelope(None, False, "action_not_allowed", "不允许的前台屏幕操作。")
    result = CORE.run_fixed(command, env=env)
    return CORE.envelope(None, result["ok"], result["state"], "前台屏幕操作已完成。" if result["ok"] else (result.get("stderr") or "前台屏幕操作失败。"), display=env.get("DISPLAY", ""))


def settings_action(args):
    backend = SETTINGS.SettingsBackend()
    if not args:
        return CORE.envelope(None, False, "invalid_request", "缺少设置操作。")
    action = args[0]
    if action == "list":
        return CORE.envelope(None, True, "ready", "设置列表已读取。", result=backend.list_settings())
    if action == "get" and len(args) == 2:
        return CORE.envelope(None, True, "ready", "设置已读取。", result=backend.get_value(args[1]))
    if action == "set" and len(args) >= 3:
        if "--confirm" not in args:
            return CORE.envelope(None, False, "confirmation_required", "修改系统设置需要明确确认。")
        result = backend.set_value(args[1], args[2])
        return CORE.envelope(None, bool(result.get("ok")), "ready" if result.get("ok") else "action_failed", "设置已更新。" if result.get("ok") else result.get("error", "设置更新失败。"), result=result)
    return CORE.envelope(None, False, "invalid_request", "设置操作参数无效。")


def device_action(args, environ):
    if not args:
        return CORE.envelope(None, False, "invalid_request", "缺少设备操作。")
    action = args[0]
    allowed = {
        "status": ["status", "--json"],
        "wifi": ["wifi-scan", "--json"],
        "audio": ["audio-status", "--json"],
        "bluetooth": ["bluetooth-status", "--json"],
        "ethernet": ["ethernet-status", "--json"],
        "brightness": ["status", "--json"],
        "battery": ["status", "--json"],
    }
    command = allowed.get(action)
    if command is None:
        return CORE.envelope(None, False, "action_not_allowed", "设备操作不在受控清单中。")
    result = CORE.run_fixed(["ming-device-control", *command], env=environ)
    if not result["ok"]:
        return CORE.envelope(None, False, result["state"], result.get("stderr") or "设备状态不可用。")
    try:
        payload = json.loads(result.get("stdout", "{}"))
    except ValueError:
        payload = {"raw": result.get("stdout", "")}
    if action in {"brightness", "battery"} and isinstance(payload, dict):
        payload = payload.get(action, payload)
    return CORE.envelope(None, True, "ready", "设备状态已读取。", result=payload)


def apps_action(args, environ):
    if not args:
        return CORE.envelope(None, False, "invalid_request", "缺少应用操作。")
    roots = [pathlib.Path.home() / ".local/share/applications", pathlib.Path("/usr/local/share/applications"), pathlib.Path("/usr/share/applications")]
    if args[0] == "list":
        entries = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.desktop")):
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.optionxform = str
                try:
                    parser.read(path, encoding="utf-8")
                    section = parser["Desktop Entry"]
                except (OSError, KeyError, configparser.Error):
                    continue
                if section.get("Type", "Application") != "Application" or section.get("Hidden", "false").lower() == "true":
                    continue
                entries.append({"desktop": path.name, "name": section.get("Name[zh_CN]", section.get("Name", path.stem)), "path": str(path), "icon": section.get("Icon", "")})
        return CORE.envelope(None, True, "ready", "应用列表已读取。", result=entries)
    if args[0] == "launch" and len(args) >= 2:
        requested = pathlib.Path(args[1]).expanduser()
        if not requested.is_absolute() and requested.name == args[1]:
            target = next((root / requested.name for root in roots if (root / requested.name).is_file()), requested)
        else:
            target = requested
        if target.name != args[1] and not requested.is_absolute():
            return CORE.envelope(None, False, "path_rejected", "应用入口必须是受信任的 desktop 文件。")
        allowed = [root.resolve() for root in roots if root.exists()]
        try:
            resolved = target.resolve()
            if not resolved.is_file() or not any(resolved == root / resolved.name for root in allowed):
                raise ValueError
        except (OSError, ValueError):
            return CORE.envelope(None, False, "path_rejected", "应用入口不在受信任目录。")
        result = CORE.run_fixed(["ming-launch", "--desktop-file", str(resolved), "--source", "agent"], env=environ)
        return CORE.envelope(None, result["ok"], result["state"], "应用已启动。" if result["ok"] else (result.get("stderr") or "应用启动失败。"), desktop=str(resolved))
    return CORE.envelope(None, False, "invalid_request", "应用操作参数无效。")


def files_action(args, confirmed=False):
    if not args:
        return CORE.envelope(None, False, "invalid_request", "缺少文件操作。")
    action = args[0]
    files = CORE.ControlledFiles()
    if action == "list" and len(args) == 2:
        return files.list(args[1])
    if action == "read" and len(args) == 2:
        return files.read(args[1])
    if action == "write" and len(args) >= 3:
        return files.write(args[1], args[2], confirmed=confirmed)
    if action in {"copy", "move"} and len(args) == 3:
        return getattr(files, action)(args[1], args[2], confirmed=confirmed)
    if action == "trash" and len(args) == 2:
        return files.trash(args[1], confirmed=confirmed)
    if action == "open" and len(args) == 2:
        return files.open(args[1])
    return CORE.envelope(None, False, "invalid_request", "文件操作参数无效。")


def dispatch(argv, runtime_root=None, environ=None):
    argv = list(argv or [])
    runtime_root = _runtime_root(runtime_root)
    environ = _env(environ)
    if not argv:
        return CORE.envelope(None, False, "invalid_request", "缺少 Agent 操作。")
    group = argv[0]
    if group == "capabilities":
        return capabilities(runtime_root)
    if group == "foreground":
        return foreground_action(argv[1:], runtime_root, environ)
    if group == "screen":
        target = "isolated"
        args = list(argv[1:])
        if "--target" in args:
            index = args.index("--target")
            if index + 1 >= len(args):
                return CORE.envelope(None, False, "invalid_request", "--target 需要值。")
            target = args[index + 1]
            del args[index:index + 2]
        if target == "foreground":
            return foreground_screen_action(args, runtime_root, environ)
        if target != "isolated":
            return CORE.envelope(None, False, "invalid_request", "未知屏幕目标。")
        return BRIDGE.dispatch(["screen", *args], runtime_root=runtime_root, foreground_display=environ.get("DISPLAY", ""))
    if group == "session":
        if len(argv) < 2:
            return CORE.envelope(None, False, "invalid_request", "缺少会话操作。")
        manager = RUNTIME.AgentSessionManager(runtime_root=runtime_root)
        if argv[1] == "create" and len(argv) >= 3:
            return manager.create(argv[2])
        if argv[1] == "status" and len(argv) >= 3:
            return manager.status(argv[2])
        if argv[1] == "stop" and len(argv) >= 3:
            return manager.stop(argv[2])
        return CORE.envelope(None, False, "invalid_request", "会话操作参数无效。")
    if group == "settings":
        return settings_action(argv[1:])
    if group == "device":
        return device_action(argv[1:], environ)
    if group == "apps":
        return apps_action(argv[1:], environ)
    if group == "files":
        return files_action([item for item in argv[1:] if item != "--confirm"], confirmed="--confirm" in argv)
    if group == "store":
        return BRIDGE.dispatch(["store", *argv[1:]], runtime_root=runtime_root, foreground_display=environ.get("DISPLAY", ""))
    if group == "system" and len(argv) >= 2:
        if argv[1] == "status":
            return CORE.envelope(None, True, "ready", "系统状态已读取。", result={"display": environ.get("DISPLAY", ""), "session": environ.get("XDG_SESSION_ID", "")})
        if argv[1] == "diagnostics":
            result = CORE.run_fixed(["ming-hardware-status", "status", "--json"], env=environ)
            return CORE.envelope(None, result["ok"], result["state"], "诊断状态已读取。" if result["ok"] else (result.get("stderr") or "诊断不可用。"), result=result.get("stdout", ""))
        if argv[1] == "power" and len(argv) >= 3:
            if "--confirm" not in argv:
                return CORE.envelope(None, False, "confirmation_required", "电源操作需要明确确认。")
            power = argv[2]
            if power not in {"logout", "reboot", "poweroff"}:
                return CORE.envelope(None, False, "action_not_allowed", "电源操作不在受控清单中。")
            result = CORE.run_fixed(["ming-power-action", power], env=environ)
            return CORE.envelope(None, result["ok"], result["state"], "电源操作已发送。" if result["ok"] else (result.get("stderr") or "电源操作失败。"))
    return CORE.envelope(None, False, "action_not_allowed", "不允许的 Agent 操作。")


def main(argv=None):
    result = dispatch(list(argv if argv is not None else sys.argv[1:]))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
