#!/usr/bin/env python3
"""Optional per-user D-Bus facade for the Ming DSH agent bridge."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from typing import Any


BUS_NAME = "org.mingos.Agent1"
OBJECT_PATH = "/org/mingos/Agent1"
PROTOCOL = "ming.agent.v1"
FOREGROUND_ASSIST = False
ROOT = pathlib.Path(__file__).resolve().parent


def _load_bridge():
    path = ROOT / "ming-agent-bridge.py"
    spec = importlib.util.spec_from_file_location("ming_agent_bridge_service", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ming-agent-bridge.py is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_cli():
    path = ROOT / "ming-agent.py"
    spec = importlib.util.spec_from_file_location("ming_agent_cli_service", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ming-agent.py is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def service_metadata() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "bus_name": BUS_NAME,
        "object_path": OBJECT_PATH,
        "foreground_assist": FOREGROUND_ASSIST,
        "foreground_supported": True,
        "user_scoped": True,
        "enabled_by_default": False,
    }


def dispatch_json(payload: str) -> dict[str, Any]:
    request_id = "agent-service"
    try:
        document = json.loads(payload)
    except (TypeError, ValueError):
        return {
            "protocol": PROTOCOL, "request_id": request_id, "ok": False,
            "state": "invalid_request", "message": "JSON 请求无效。",
        }
    argv = document.get("argv") if isinstance(document, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        return {
            "protocol": PROTOCOL, "request_id": request_id, "ok": False,
            "state": "invalid_request", "message": "请求必须包含字符串 argv 数组。",
        }
    return _load_cli().dispatch(argv)


try:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
except ImportError:  # Allows metadata/tests on systems without python3-dbus.
    dbus = None
    DBusGMainLoop = None
    GLib = None


if dbus is not None:
    class AgentService(dbus.service.Object):
        def __init__(self, bus):
            super().__init__(bus, OBJECT_PATH)
            self.bus = bus

        @dbus.service.method(BUS_NAME, in_signature="", out_signature="s")
        def Capabilities(self):
            return json.dumps(_load_cli().dispatch(["capabilities"]), ensure_ascii=False)

        @dbus.service.method(BUS_NAME, in_signature="s", out_signature="s")
        def DispatchJson(self, payload):
            return json.dumps(dispatch_json(str(payload)), ensure_ascii=False)

        @dbus.service.method(BUS_NAME, in_signature="s", out_signature="s")
        def CreateSession(self, session_id):
            runtime = _load_bridge()._runtime.AgentSessionManager()
            return json.dumps(runtime.create(str(session_id)), ensure_ascii=False)

        @dbus.service.method(BUS_NAME, in_signature="s", out_signature="s")
        def SessionStatus(self, session_id):
            runtime = _load_bridge()._runtime.AgentSessionManager()
            return json.dumps(runtime.status(str(session_id)), ensure_ascii=False)

        @dbus.service.method(BUS_NAME, in_signature="s", out_signature="s")
        def StopSession(self, session_id):
            runtime = _load_bridge()._runtime.AgentSessionManager()
            return json.dumps(runtime.stop(str(session_id)), ensure_ascii=False)


def main() -> int:
    if dbus is None or DBusGMainLoop is None or GLib is None:
        print(json.dumps({"protocol": PROTOCOL, "ok": False, "state": "runtime_missing", "message": "python3-dbus/GLib 不可用。"}, ensure_ascii=False))
        return 1
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(BUS_NAME, bus=bus)
    AgentService(bus)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
