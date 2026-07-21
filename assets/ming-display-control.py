#!/usr/bin/env python3
"""Safe, confirmed X11 display changes for Ming OS.

The helper deliberately accepts only modes and refresh rates returned by the
current ``xrandr --query`` output.  A requested mode is staged privately before
it is applied and is restored after 15 seconds unless the caller confirms that
the new mode is actually active.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time

try:  # Linux images have fcntl; keeping this optional makes pure tests portable.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts.
    fcntl = None


CONFIRM_SECONDS = 15
ROTATIONS = {"normal", "left", "inverted", "right"}
SAFE_OUTPUT = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_MODE = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
SAFE_RATE = re.compile(r"^[0-9]{1,3}(?:\.[0-9]{1,3})?$")
SAFE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
SOFTWARE_BRIGHTNESS_MIN = 20
SOFTWARE_BRIGHTNESS_MAX = 100
SOFTWARE_BRIGHTNESS_BACKEND = "xrandr-software"


def _state_dir():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path.home() / ".cache"
    return base / "ming-os" / "display-control"


def _format_rate(rate):
    try:
        value = float(str(rate))
    except (TypeError, ValueError):
        return str(rate)
    return ("%.3f" % value).rstrip("0").rstrip(".")


def mode_label(mode, rate):
    """Render an xrandr mode as a user-facing resolution and refresh label."""
    match = SAFE_MODE.fullmatch(str(mode))
    if not match:
        return "%s · %s Hz" % (mode, _format_rate(rate))
    width, height = str(mode).split("x", 1)
    return "%s × %s · %s Hz" % (width, height, _format_rate(rate))


def parse_xrandr_snapshot(text):
    """Return connected outputs, supported mode/rate pairs and current state."""
    outputs = []
    current = None
    for raw_line in (text or "").splitlines():
        header = re.match(r"^(\S+)\s+(connected|disconnected)\b(.*)$", raw_line)
        if header:
            name, connection, tail = header.groups()
            geometry = re.search(r"\b([1-9][0-9]{1,4}x[1-9][0-9]{1,4})\+[0-9]+\+[0-9]+\b", tail)
            rotation = re.search(r"\((normal|left|inverted|right)\b", tail)
            current = {
                "name": name,
                "connected": connection == "connected",
                "mode": geometry.group(1) if geometry else None,
                "rate": None,
                "rotation": rotation.group(1) if rotation else "normal",
                "modes": [],
            }
            outputs.append(current)
            continue

        if not current or not current["connected"]:
            continue
        mode_line = re.match(r"^\s+([1-9][0-9]{1,4}x[1-9][0-9]{1,4})\s+(.+)$", raw_line)
        if not mode_line:
            continue
        mode, rates_text = mode_line.groups()
        rates = []
        selected_rate = None
        for token in rates_text.split():
            selected = "*" in token
            rate = token.replace("*", "").replace("+", "")
            if not SAFE_RATE.fullmatch(rate):
                continue
            rates.append(rate)
            if selected:
                selected_rate = rate
        if not rates:
            continue
        current["modes"].append({"mode": mode, "rates": rates})
        if mode == current["mode"]:
            current["rate"] = selected_rate or rates[0]
    return {"outputs": outputs}


def parse_xrandr_brightness(text):
    """Read brightness for connected, active outputs from ``xrandr --verbose``.

    The output names are accepted only from xrandr's own connected-output
    headers.  This keeps the value passed back to xrandr free of shell or
    option injection and excludes disconnected or inactive outputs.
    """
    snapshot = parse_xrandr_snapshot(text)
    active = {
        item["name"]: item
        for item in snapshot.get("outputs", [])
        if item.get("connected") and item.get("mode") and SAFE_OUTPUT.fullmatch(str(item.get("name", "")))
    }
    current = None
    for raw_line in (text or "").splitlines():
        header = re.match(r"^(\S+)\s+(connected|disconnected)\b", raw_line)
        if header:
            current = header.group(1) if header.group(1) in active else None
            continue
        if not current:
            continue
        match = re.match(r"^\s*Brightness:\s*([0-9]+(?:\.[0-9]+)?)\s*$", raw_line)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0.0 <= value <= 1.0:
            active[current]["brightness"] = value
    return list(active.values())


def request_is_supported(snapshot, output, mode, rate, rotation):
    """Validate a request against one freshly parsed, connected xrandr output."""
    if not (
        SAFE_OUTPUT.fullmatch(str(output or ""))
        and SAFE_MODE.fullmatch(str(mode or ""))
        and SAFE_RATE.fullmatch(str(rate or ""))
        and rotation in ROTATIONS
    ):
        return False
    for item in snapshot.get("outputs", []):
        if item.get("name") != output or not item.get("connected"):
            continue
        return any(
            candidate.get("mode") == mode and rate in candidate.get("rates", [])
            for candidate in item.get("modes", [])
        )
    return False


def request_is_active(snapshot, request):
    for item in snapshot.get("outputs", []):
        if item.get("name") != request.get("output"):
            continue
        return all(
            item.get(key) == request.get(key)
            for key in ("mode", "rate", "rotation")
        )
    return False


class DisplayController:
    """Stateful controller with injectable command and timer operations."""

    def __init__(self, runner=None, state_dir=None, timer_factory=None, timer_canceller=None,
                 software_state_path=None):
        self.runner = runner or self._default_runner
        self.state_dir = Path(state_dir) if state_dir else _state_dir()
        self.timer_factory = timer_factory or self._start_timer
        self.timer_canceller = timer_canceller or self._cancel_timer
        self.software_state_path = Path(software_state_path) if software_state_path else None

    @staticmethod
    def _default_runner(argv):
        try:
            return subprocess.run(argv, text=True, capture_output=True, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(argv, 127, "", str(exc))

    def _run(self, argv):
        result = self.runner(argv)
        return (
            int(getattr(result, "returncode", 1)),
            str(getattr(result, "stdout", "") or ""),
            str(getattr(result, "stderr", "") or ""),
        )

    def _ensure_state_dir(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass

    @contextlib.contextmanager
    def _lock(self):
        self._ensure_state_dir()
        lock_path = self.state_dir / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _state_path_for(directory, token):
        if not SAFE_TOKEN.fullmatch(str(token or "")):
            return None
        return directory / (str(token) + ".json")

    def _write_state(self, state):
        token = state["token"]
        path = self._state_path_for(self.state_dir, token)
        if not path:
            raise ValueError("invalid staged display token")
        temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _read_state(self, token):
        path = self._state_path_for(self.state_dir, token)
        if not path or not path.is_file():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(state, dict) or state.get("token") != token:
            return None
        return state

    def _remove_state(self, token):
        path = self._state_path_for(self.state_dir, token)
        if not path:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def snapshot(self):
        rc, output, error = self._run(["xrandr", "--query"])
        if rc != 0:
            return {
                "ok": False,
                "error": "无法读取当前显示器状态：%s" % (error.strip() or "xrandr 失败"),
                "outputs": [],
            }
        snapshot = parse_xrandr_snapshot(output)
        return {"ok": True, **snapshot}

    def status(self):
        result = self.snapshot()
        result["confirm_seconds"] = CONFIRM_SECONDS
        return result

    def _software_preference_path(self):
        if self.software_state_path:
            return self.software_state_path
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else Path.home() / ".config"
        return base / "ming-os" / "software-brightness.json"

    def _read_software_preference(self):
        path = self._software_preference_path()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        try:
            brightness = int(value.get("value"))
        except (TypeError, ValueError):
            return None
        if not SOFTWARE_BRIGHTNESS_MIN <= brightness <= SOFTWARE_BRIGHTNESS_MAX:
            return None
        return brightness

    def _write_software_preference(self, value):
        path = self._software_preference_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
        payload = {
            "version": 1,
            "backend": SOFTWARE_BRIGHTNESS_BACKEND,
            "value": int(value),
        }
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _software_result(ok, requested=None, value=None, error="", state=None,
                         outputs=None, preference=None):
        result = {
            "ok": bool(ok),
            "available": bool(ok),
            "state": state or ("ready" if ok else "unavailable"),
            "backend": SOFTWARE_BRIGHTNESS_BACKEND,
            "requested": requested,
            "value": value,
            "error": error or "",
            "outputs": list(outputs or []),
        }
        if preference is not None:
            result["preference"] = preference
        return result

    def _software_snapshot(self):
        if not os.environ.get("DISPLAY"):
            return self._software_result(
                False, error="没有可用的 X11 DISPLAY，软件调暗仅支持 X11 会话。",
                state="unavailable")
        rc, output, error = self._run(["xrandr", "--verbose"])
        if rc != 0:
            return self._software_result(
                False, error="无法读取 X11 显示器状态：%s" % (error.strip() or "xrandr 失败"),
                state="unavailable")
        outputs = parse_xrandr_brightness(output)
        if not outputs:
            return self._software_result(
                False, error="没有检测到可调暗的已连接活动显示器。", state="unavailable")
        missing = [item.get("name", "unknown") for item in outputs if "brightness" not in item]
        if missing:
            return self._software_result(
                False, error="无法读取显示器亮度：%s" % ", ".join(missing), state="error")
        return {
            "ok": True,
            "available": True,
            "state": "ready",
            "backend": SOFTWARE_BRIGHTNESS_BACKEND,
            "outputs": outputs,
        }

    @staticmethod
    def _software_percent(value):
        return int(round(float(value) * 100.0))

    @staticmethod
    def _software_factor(value):
        return ("%.3f" % (float(value) / 100.0)).rstrip("0").rstrip(".")

    def software_status(self):
        result = self._software_snapshot()
        if not result.get("ok"):
            result["preference"] = self._read_software_preference()
            return result
        values = [self._software_percent(item["brightness"]) for item in result["outputs"]]
        result["value"] = values[0] if values and all(value == values[0] for value in values) else None
        result["preference"] = self._read_software_preference()
        result["output_values"] = {
            item["name"]: self._software_percent(item["brightness"])
            for item in result["outputs"]
        }
        result["outputs"] = [item["name"] for item in result["outputs"]]
        return result

    def _rollback_software_outputs(self, outputs):
        failures = []
        for name, brightness in reversed(list(outputs.items())):
            if not SAFE_OUTPUT.fullmatch(str(name)):
                failures.append(name)
                continue
            rc, _output, error = self._run([
                "xrandr", "--output", name, "--brightness", self._software_factor(brightness * 100)
            ])
            if rc != 0:
                failures.append(name)
        return failures

    def software_set(self, value):
        try:
            requested = int(value)
        except (TypeError, ValueError):
            return self._software_result(
                False, error="亮度必须是整数百分比。", state="invalid")
        if requested < 1 or requested > 100:
            return self._software_result(
                False, requested=requested, error="亮度必须在 1 到 100 之间。", state="invalid")
        target = max(SOFTWARE_BRIGHTNESS_MIN, min(SOFTWARE_BRIGHTNESS_MAX, requested))
        before = self._software_snapshot()
        if not before.get("ok"):
            before["requested"] = requested
            return before
        previous = {
            item["name"]: float(item["brightness"])
            for item in before["outputs"]
        }
        changed = {}
        factor = self._software_factor(target)
        for name in previous:
            if not SAFE_OUTPUT.fullmatch(str(name)):
                continue
            rc, output, error = self._run(
                ["xrandr", "--output", name, "--brightness", factor])
            if rc != 0:
                rollback_failures = self._rollback_software_outputs(changed)
                message = "设置显示器 %s 失败：%s" % (name, error.strip() or output or "xrandr 失败")
                if rollback_failures:
                    message += "；恢复失败：%s" % ", ".join(rollback_failures)
                return self._software_result(
                    False, requested=requested, error=message, state="error",
                    outputs=list(previous))
            changed[name] = previous[name]
        after = self._software_snapshot()
        target_outputs = [item["name"] for item in after.get("outputs", [])]
        readback_ok = after.get("ok") and target_outputs == list(previous)
        if readback_ok:
            readback_ok = all(
                abs(float(item["brightness"]) - target / 100.0) <= 0.02
                for item in after["outputs"]
            )
        if not readback_ok:
            rollback_failures = self._rollback_software_outputs(changed)
            message = after.get("error") or "显示器亮度读回与请求不一致。"
            if rollback_failures:
                message += "；恢复失败：%s" % ", ".join(rollback_failures)
            return self._software_result(
                False, requested=requested, error=message, state="error",
                outputs=list(previous))
        try:
            self._write_software_preference(target)
        except (OSError, ValueError) as exc:
            rollback_failures = self._rollback_software_outputs(changed)
            message = "保存软件亮度偏好失败：%s" % exc
            if rollback_failures:
                message += "；恢复失败：%s" % ", ".join(rollback_failures)
            return self._software_result(
                False, requested=requested, error=message, state="error",
                outputs=list(previous))
        return self._software_result(
            True, requested=requested, value=target, state="ready", outputs=list(previous))

    def software_reapply(self):
        value = self._read_software_preference()
        if value is None:
            return self._software_result(
                False, error="没有保存的软件亮度偏好。", state="unavailable")
        return self.software_set(value)

    def _start_timer(self, token):
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_timeout-rollback",
            token,
            "--state-dir",
            str(self.state_dir),
        ]
        try:
            process = subprocess.Popen(
                command,
                close_fds=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"pid": process.pid}
        except OSError:
            return {}

    @staticmethod
    def _cancel_timer(timer):
        if not isinstance(timer, dict):
            return
        pid = timer.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _restore_command(output):
        name = output.get("name")
        if not SAFE_OUTPUT.fullmatch(str(name or "")):
            return None
        mode, rate, rotation = output.get("mode"), output.get("rate"), output.get("rotation")
        if mode is None:
            return ["xrandr", "--output", name, "--off"]
        if not (SAFE_MODE.fullmatch(str(mode)) and SAFE_RATE.fullmatch(str(rate)) and rotation in ROTATIONS):
            return None
        return ["xrandr", "--output", name, "--mode", mode, "--rate", rate, "--rotate", rotation]

    def restore_snapshot(self, snapshot):
        failures = []
        for output in snapshot.get("outputs", []):
            if not output.get("connected"):
                continue
            command = self._restore_command(output)
            if not command:
                failures.append(output.get("name", "unknown"))
                continue
            rc, _out, _err = self._run(command)
            if rc != 0:
                failures.append(output.get("name", "unknown"))
        return {"ok": not failures, "failed_outputs": failures}

    def apply(self, output, mode, rate, rotation):
        with self._lock():
            status = self.snapshot()
            if not status.get("ok"):
                return status
            snapshot = {"outputs": status["outputs"]}
            if not request_is_supported(snapshot, output, mode, rate, rotation):
                return {"ok": False, "error": "该分辨率或刷新率不在当前显示器支持列表中"}
            token = secrets.token_hex(16)
            state = {
                "token": token,
                "snapshot": snapshot,
                "request": {"output": output, "mode": mode, "rate": rate, "rotation": rotation},
                "timer": {},
            }
            self._write_state(state)
            command = ["xrandr", "--output", output, "--mode", mode, "--rate", rate, "--rotate", rotation]
            rc, _out, error = self._run(command)
            if rc != 0:
                restored = self.restore_snapshot(snapshot)
                self._remove_state(token)
                return {
                    "ok": False,
                    "error": "应用显示设置失败，已尝试恢复原设置：%s" % (error.strip() or "xrandr 失败"),
                    "restored": restored["ok"],
                }
            timer = self.timer_factory(token) or {}
            timer_ready = isinstance(timer, dict) and isinstance(timer.get("pid"), int) and timer["pid"] > 1
            if not timer_ready:
                restored = self.restore_snapshot(snapshot)
                self._remove_state(token)
                return {
                    "ok": False,
                    "error": "无法启动显示设置自动恢复，已恢复原设置",
                    "restored": restored["ok"],
                }
            state["timer"] = timer
            self._write_state(state)
            return {"ok": True, "token": token, "expires_in": CONFIRM_SECONDS}

    def confirm(self, token):
        with self._lock():
            state = self._read_state(token)
            if not state:
                return {"ok": False, "error": "没有待确认的显示设置"}
            current = self.snapshot()
            if not current.get("ok"):
                return current
            if not request_is_active(current, state.get("request", {})):
                return {"ok": False, "error": "显示器尚未切换到待确认设置，已保留自动恢复"}
            self.timer_canceller(state.get("timer", {}))
            self._remove_state(token)
            return {"ok": True, "message": "显示设置已保留"}

    def rollback(self, token, cancel_timer=True):
        with self._lock():
            state = self._read_state(token)
            if not state:
                return {"ok": False, "error": "没有待恢复的显示设置"}
            if cancel_timer:
                self.timer_canceller(state.get("timer", {}))
            result = self.restore_snapshot(state.get("snapshot", {}))
            if result["ok"]:
                self._remove_state(token)
                return {"ok": True, "message": "已恢复原显示设置"}
            return {"ok": False, "error": "恢复原显示设置失败", "failed_outputs": result["failed_outputs"]}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _emit(payload, stdout):
    stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv=None, controller=None, stdout=None):
    parser = JsonArgumentParser(prog="ming-display-control", add_help=False)
    subcommands = parser.add_subparsers(dest="command", required=True)
    status = subcommands.add_parser("status")
    status.add_argument("--json", action="store_true")
    software_status = subcommands.add_parser("software-status")
    software_status.add_argument("--json", action="store_true")
    software_set = subcommands.add_parser("software-set")
    software_set.add_argument("value", type=int, nargs="?")
    software_set.add_argument("--value", dest="value_option", type=int)
    software_set.add_argument("--json", action="store_true")
    software_reapply = subcommands.add_parser("software-reapply")
    software_reapply.add_argument("--json", action="store_true")
    apply = subcommands.add_parser("apply")
    apply.add_argument("--output", required=True)
    apply.add_argument("--mode", required=True)
    apply.add_argument("--rate", required=True)
    apply.add_argument("--rotation", required=True)
    confirm = subcommands.add_parser("confirm")
    confirm.add_argument("token")
    rollback = subcommands.add_parser("rollback")
    rollback.add_argument("token")
    timeout = subcommands.add_parser("_timeout-rollback")
    timeout.add_argument("token")
    timeout.add_argument("--state-dir", required=True)
    stdout = stdout or sys.stdout
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        _emit({"ok": False, "error": str(exc)}, stdout)
        return 2
    control = controller or DisplayController(
        state_dir=Path(args.state_dir) if args.command == "_timeout-rollback" else None)
    if args.command == "status":
        result = control.status()
    elif args.command == "software-status":
        result = control.software_status()
    elif args.command == "software-set":
        value = args.value_option if args.value_option is not None else args.value
        if value is None:
            result = {"ok": False, "error": "缺少亮度百分比。"}
        else:
            result = control.software_set(value)
    elif args.command == "software-reapply":
        result = control.software_reapply()
    elif args.command == "apply":
        result = control.apply(args.output, args.mode, args.rate, args.rotation)
    elif args.command == "confirm":
        result = control.confirm(args.token)
    elif args.command == "rollback":
        result = control.rollback(args.token)
    else:
        time.sleep(CONFIRM_SECONDS)
        result = control.rollback(args.token, cancel_timer=False)
    _emit(result, stdout)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
