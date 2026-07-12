#!/usr/bin/env python3
"""Bounded PulseAudio playback session health helper for Ming OS.

It deliberately delegates output policy to ``ming-device-control``.  The
session layer only starts a missing user daemon and asks the controller to
repair an output when its structured status says the current output is absent,
muted, or has no usable playback profile.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


COMMAND_TIMEOUT = 6
DEFAULT_LOG_PATH = Path.home() / ".cache" / "ming-os" / "audio-session.log"


def default_runner(argv, timeout=COMMAND_TIMEOUT):
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "音频会话检查超时。"
    except OSError as exc:
        return 1, "", str(exc)


def device_control_command(*args):
    installed = "/usr/local/bin/ming-device-control"
    if os.path.isfile(installed):
        return [installed] + list(args)
    local = Path(__file__).resolve().with_name("ming-device-control.py")
    return [sys.executable, str(local)] + list(args)


class AudioSession:
    """Recover only the minimal playback state needed by user applications."""

    def __init__(self, runner=default_runner, status_reader=None, repairer=None,
                 log_path=DEFAULT_LOG_PATH, clock=time.strftime):
        self.runner = runner
        self.status_reader = status_reader
        self.repairer = repairer
        self.log_path = Path(log_path) if log_path else None
        self.clock = clock

    def _log(self, message):
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write("[%s] %s\n" % (self.clock("%F %T"), message))
        except OSError:
            # A full home directory must not prevent users from hearing sound.
            pass

    def _command_json(self, *args):
        rc, output, error = self.runner(device_control_command(*args), timeout=COMMAND_TIMEOUT)
        if rc != 0:
            return None, error or output or "音频控制组件未返回状态。"
        try:
            value = json.loads(output)
        except (TypeError, ValueError):
            return None, "音频控制组件返回了无效数据。"
        if not isinstance(value, dict):
            return None, "音频控制组件返回了无效数据。"
        return value, ""

    def _read_status(self):
        if self.status_reader is not None:
            try:
                value = self.status_reader()
            except Exception as exc:
                return {"available": False, "server_available": False,
                        "playback_ready": False, "error": str(exc)}
            if isinstance(value, dict):
                return dict(value)
            return {"available": False, "server_available": False,
                    "playback_ready": False, "error": "音频控制组件返回了无效数据。"}
        value, error = self._command_json("audio-status", "--json")
        if value is None:
            return {"available": False, "server_available": False,
                    "playback_ready": False, "error": error}
        return value

    def _repair_playback(self):
        if self.repairer is not None:
            try:
                value = self.repairer()
            except Exception as exc:
                return {"ok": False, "changed": False, "error": str(exc)}
            return value if isinstance(value, dict) else {
                "ok": False, "changed": False,
                "error": "声音修复组件返回了无效数据。",
            }
        value, error = self._command_json("audio-repair-playback")
        if value is None:
            return {"ok": False, "changed": False, "error": error}
        return value

    @staticmethod
    def _requires_playback_repair(status):
        """Keep valid manual HDMI, Bluetooth and USB output selections intact."""
        if (status or {}).get("backend") != "pactl":
            return False
        if not (status or {}).get("server_available"):
            return False
        return bool(
            not status.get("default_sink")
            or status.get("default_sink_present") is False
            or status.get("output_muted") is True
            or status.get("playback_profile_valid") is False
        )

    @staticmethod
    def _playback_ready(status):
        return bool(
            (status or {}).get("server_available")
            and (status or {}).get("playback_ready")
            and (status or {}).get("default_sink")
            and (status or {}).get("default_sink_present") is not False
            and (status or {}).get("output_muted") is not True
            and (status or {}).get("playback_profile_valid") is not False
        )

    @staticmethod
    def _state(status):
        if not (status or {}).get("server_available"):
            return "no_server"
        if AudioSession._requires_playback_repair(status):
            return "needs_repair"
        if AudioSession._playback_ready(status):
            return "ready"
        return "unavailable"

    def status(self):
        current = self._read_status()
        result = {
            "ok": self._playback_ready(current),
            "state": self._state(current),
            "changed": False,
            "status": current,
            "error": current.get("error", "") or "",
            "log_path": str(self.log_path) if self.log_path else "",
        }
        self._log("状态：%s" % result["state"])
        return result

    def _start_pulseaudio(self):
        rc, output, error = self.runner(["pulseaudio", "--start"], timeout=COMMAND_TIMEOUT)
        if rc == 0:
            self._log("已请求启动 PulseAudio 用户会话。")
            return True, ""
        message = error or output or "无法启动 PulseAudio 用户会话。"
        self._log("启动 PulseAudio 失败：%s" % message)
        return False, message

    def ensure(self):
        """Ensure a usable playback path without replacing valid user intent."""
        current = self._read_status()
        changed = False
        actions = []

        if not current.get("server_available"):
            started, start_error = self._start_pulseaudio()
            changed = started
            actions.append("started_pulseaudio" if started else "start_failed")
            current = self._read_status()
            if not current.get("server_available"):
                error = current.get("error") or start_error or "PulseAudio 用户会话仍不可用。"
                result = {
                    "ok": False, "changed": changed, "action": actions[-1],
                    "state": "no_server", "status": current, "error": error,
                    "log_path": str(self.log_path) if self.log_path else "",
                }
                self._log("声音播放未恢复：%s" % error)
                return result

        if self._requires_playback_repair(current):
            repair = self._repair_playback()
            changed = bool(repair.get("changed")) or changed
            actions.append(repair.get("action") or "repaired_playback")
            current = self._read_status()
            if not repair.get("ok"):
                error = repair.get("error") or current.get("error") or "声音播放修复失败。"
                result = {
                    "ok": False, "changed": changed, "action": actions[-1],
                    "state": self._state(current), "status": current, "error": error,
                    "log_path": str(self.log_path) if self.log_path else "",
                }
                self._log("声音播放修复失败：%s" % error)
                return result

        ready = self._playback_ready(current)
        action = "+".join(actions) if actions else "ready"
        error = "" if ready else (current.get("error") or "未检测到可用的声音播放输出。")
        result = {
            "ok": ready, "changed": changed, "action": action,
            "state": self._state(current), "status": current, "error": error,
            "log_path": str(self.log_path) if self.log_path else "",
        }
        self._log("声音播放%s：%s" % ("已就绪" if ready else "未恢复", action))
        return result


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-audio-session")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("ensure", "status"):
        action = actions.add_parser(name)
        action.add_argument("--json", action="store_true", help="输出 JSON 状态")
    return parser


def main(argv=None, session=None, stdout=None):
    args = build_parser().parse_args(argv)
    session = session or AudioSession()
    result = session.ensure() if args.action == "ensure" else session.status()
    print(json.dumps(result, ensure_ascii=False), file=stdout or sys.stdout)
    # ``status`` is a diagnostic query and remains consumable when audio is absent.
    return 0 if args.action == "status" or result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
