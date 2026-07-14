#!/usr/bin/env python3
"""Deduplicated user-session NetworkManager and BlueZ connection notifications."""

import re
import subprocess
import threading
import time


SENSITIVE = re.compile(r"(?i)(password|passwd|psk|pin|secret|token)\s*[:=]\s*\S+")


class NotificationDeduplicator:
    def __init__(self, window_seconds=8):
        self.window_seconds = window_seconds
        self.seen = {}

    def accept(self, key, now=None):
        now = time.monotonic() if now is None else now
        previous = self.seen.get(key)
        self.seen = {item: stamp for item, stamp in self.seen.items()
                     if now - stamp <= self.window_seconds}
        accepted = previous is None or now - previous > self.window_seconds
        if accepted:
            self.seen[key] = now
        return accepted


def sanitize(value):
    value = " ".join(str(value or "").split())[:160]
    return SENSITIVE.sub("", value).strip(" -:;")


def build_notification(event, cache=None, now=None):
    cache = cache or NotificationDeduplicator()
    kind = event.get("kind") if isinstance(event, dict) else ""
    state = event.get("state") if isinstance(event, dict) else ""
    label = sanitize(event.get("label", "")) if isinstance(event, dict) else ""
    if kind not in {"network", "bluetooth"} or state not in {"connected", "disconnected"}:
        return None
    key = (kind, state, label)
    if not cache.accept(key, now=now):
        return None
    title = "网络已连接" if kind == "network" and state == "connected" else (
        "网络连接已断开" if kind == "network" else
        "蓝牙设备已连接" if state == "connected" else "蓝牙设备已断开")
    body = label if label else ("连接可用" if state == "connected" else "连接已结束")
    return {"title": title, "body": body, "icon": "network-wired" if kind == "network" else "bluetooth"}


def emit(event, cache):
    notification = build_notification(event, cache=cache)
    if notification:
        subprocess.run(
            ["notify-send", "--app-name=Ming OS", "--icon=" + notification["icon"],
             notification["title"], notification["body"]],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def monitor(command, parser, cache):
    while True:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, errors="replace")
            for line in process.stdout:
                event = parser(line)
                if event:
                    emit(event, cache)
        except OSError:
            pass
        time.sleep(3)


def parse_network(line):
    lower = line.lower()
    if "connected" in lower and "disconnected" not in lower:
        return {"kind": "network", "state": "connected", "label": "有线或无线网络"}
    if "disconnected" in lower:
        return {"kind": "network", "state": "disconnected", "label": "有线或无线网络"}
    return None


def parse_bluetooth(line):
    match = re.search(r"(?i)Connected:\s*(yes|no)", line)
    if match:
        return {"kind": "bluetooth", "state": "connected" if match.group(1).lower() == "yes" else "disconnected",
                "label": "蓝牙设备"}
    return None


def main():
    cache = NotificationDeduplicator()
    threads = [
        threading.Thread(target=monitor, args=(["nmcli", "monitor"], parse_network, cache), daemon=True),
        threading.Thread(target=monitor, args=(["bluetoothctl", "--monitor"], parse_bluetooth, cache), daemon=True),
    ]
    for thread in threads:
        thread.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
