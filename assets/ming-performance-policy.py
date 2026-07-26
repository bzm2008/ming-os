#!/usr/bin/env python3
"""Safe, best-effort performance policy controls for Ming OS.

The helper is intentionally conservative: every mutating operation validates
PID, starttime and UID before attempting a scheduling change.  Missing cgroup,
cpufreq, ionice or renice support is reported as a degradation rather than a
desktop startup failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Sequence


PROTECTED_NAMES = {
    "Xorg", "X", "xfwm4", "xfce4-session", "lightdm", "NetworkManager",
    "pulseaudio", "pipewire", "wireplumber", "bluetoothd", "fcitx5",
    "picom", "plank", "ming-phone-desktop", "ming-update",
}


def _effective_uid() -> int:
    getter = getattr(os, "geteuid", None)
    if callable(getter):
        try:
            return int(getter())
        except OSError:
            return -1
    return -1


def _run(command: Sequence[str], timeout: float = 1.0) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(command), check=False, capture_output=True, text=True,
            timeout=max(0.1, float(timeout)),
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 126, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


class PerformancePolicy:
    def __init__(
        self,
        *,
        proc_root: pathlib.Path | str = "/proc",
        runtime_dir: pathlib.Path | str = "/run/ming-os/resource-policy",
        cgroup_root: pathlib.Path | str = "/sys/fs/cgroup",
        status_path: pathlib.Path | str = "/run/ming-os/resource-policy.json",
        command_runner: Callable[[Sequence[str], float], tuple[int, str, str]] = _run,
        uid_getter: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.proc_root = pathlib.Path(proc_root)
        self.runtime_dir = pathlib.Path(runtime_dir)
        self.cgroup_root = pathlib.Path(cgroup_root)
        self.status_path = pathlib.Path(status_path)
        self.command_runner = command_runner
        self.uid_getter = uid_getter or _effective_uid
        self.clock = clock

    def _has_privileged_scheduler(self) -> bool:
        try:
            return int(self.uid_getter()) == 0
        except Exception:
            return False

    def _proc_dir(self, pid: int) -> pathlib.Path:
        return self.proc_root / str(pid)

    def _read_text(self, path: pathlib.Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _proc_info(self, pid: int) -> dict[str, Any]:
        stat = self._read_text(self._proc_dir(pid) / "stat")
        status = self._read_text(self._proc_dir(pid) / "status")
        if not stat or ")" not in stat:
            return {"exists": False}
        before, after = stat.rsplit(")", 1)
        name = before.split("(", 1)[-1]
        fields = after.strip().split()
        uid = None
        for line in status.splitlines():
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        uid = int(parts[1])
                    except ValueError:
                        uid = None
                break
        try:
            nice = int(fields[16])
            starttime = fields[19]
        except (IndexError, ValueError):
            nice = 0
            starttime = ""
        return {"exists": True, "name": name, "uid": uid, "nice": nice, "starttime": starttime}

    def _validate_pid(self, pid: int, starttime: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        info = self._proc_info(pid)
        if not info.get("exists"):
            return None, self._result(False, "E_PID_MISSING", "进程不存在。")
        if str(info.get("starttime")) != str(starttime):
            return None, self._result(False, "E_PID_STALE", "进程启动时间不匹配，已拒绝修改。")
        current_uid = self.uid_getter()
        if current_uid != 0 and info.get("uid") != current_uid:
            return None, self._result(False, "E_UID_MISMATCH", "只能调整当前登录用户自己的进程。")
        if info.get("name") in PROTECTED_NAMES:
            return None, self._result(False, "E_PROTECTED_PROCESS", "系统关键进程不参与性能策略。")
        return info, None

    @staticmethod
    def _result(ok: bool, error_code: str = "", reason: str = "", **values: Any) -> dict[str, Any]:
        payload = {"ok": bool(ok), "error_code": error_code, "reason": reason}
        payload.update(values)
        return payload

    def _load_json(self, name: str) -> dict[str, Any]:
        path = self.runtime_dir / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _write_json(target: pathlib.Path, value: dict[str, Any]) -> bool:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp.%s" % os.getpid())
            tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            return False
        return True

    def _save_json(self, name: str, value: dict[str, Any]) -> bool:
        target = self.runtime_dir / name
        return self._write_json(target, value)

    def _prune_expired_leases(self, leases: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        now = self.clock()
        changed = False
        for token, lease in list(leases.items()):
            if not isinstance(lease, dict):
                leases.pop(token, None)
                changed = True
                continue
            try:
                expires_at = float(lease.get("expires_at", 0))
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at > now:
                continue
            info, error = self._validate_pid(
                int(lease.get("pid", 0)), str(lease.get("starttime", "")))
            if not error and info and self._has_privileged_scheduler():
                self._call((
                    "renice", "-n", str(lease.get("original_nice", info.get("nice", 0))),
                    "-p", str(lease.get("pid"))))
            leases.pop(token, None)
            changed = True
        return leases, changed

    def _publish_status(
        self,
        leases: dict[str, Any] | None = None,
        backgrounds: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        leases = leases if isinstance(leases, dict) else self._load_json("leases.json")
        backgrounds = backgrounds if isinstance(backgrounds, dict) else self._load_json("background.json")
        cgroup = self.cgroup_status()
        payload = {
            "ok": True,
            "cgroup": cgroup,
            "mode": "adaptive",
            "active_leases": len(leases),
            "background_throttled": len(backgrounds),
            "degraded": cgroup["degraded"],
            "policy": {
                "mode": "adaptive",
                "active_leases": len(leases),
                "background_throttled": len(backgrounds),
                "degraded": cgroup["degraded"],
            },
        }
        self._write_json(self.status_path, payload)
        return payload

    def _call(self, command: Sequence[str], timeout: float = 1.0) -> bool:
        try:
            rc, _out, _err = self.command_runner(tuple(command), timeout)
        except TypeError:
            rc, _out, _err = self.command_runner(tuple(command), timeout=timeout)  # type: ignore[call-arg]
        except Exception:
            return False
        return int(rc) == 0

    def begin(self, pid: int, starttime: str, reason: str) -> dict[str, Any]:
        info, error = self._validate_pid(pid, starttime)
        if error:
            return error
        if not self._has_privileged_scheduler():
            return self._result(True, degraded=["privileged-policy-unavailable"], expires_in=0)
        token = uuid.uuid4().hex
        leases = self._load_json("leases.json")
        leases, _changed = self._prune_expired_leases(leases)
        leases[token] = {
            "pid": pid,
            "starttime": str(starttime),
            "reason": reason,
            "original_nice": info.get("nice", 0) if info else 0,
            "expires_at": self.clock() + 1.5,
        }
        degraded = []
        if not self._call(("renice", "-n", "-5", "-p", str(pid))):
            degraded.append("renice-unavailable")
        if not self._call(("ionice", "-c2", "-n0", "-p", str(pid))):
            degraded.append("ionice-unavailable")
        self._save_json("leases.json", leases)
        self._publish_status(leases=leases)
        return self._result(True, token=token, degraded=degraded, expires_in=1.5)

    def end(self, token: str) -> dict[str, Any]:
        leases = self._load_json("leases.json")
        lease = leases.pop(token, None)
        if not isinstance(lease, dict):
            return self._result(False, "E_TOKEN_MISSING", "租约不存在或已结束。")
        info, error = self._validate_pid(int(lease.get("pid", 0)), str(lease.get("starttime", "")))
        if error:
            self._save_json("leases.json", leases)
            return error
        self._call(("renice", "-n", str(lease.get("original_nice", 0)), "-p", str(lease.get("pid"))))
        self._save_json("leases.json", leases)
        self._publish_status(leases=leases)
        return self._result(True, restored=True)

    def apply_background(self, pid: int, starttime: str, desktop_file: str, visible: bool) -> dict[str, Any]:
        info, error = self._validate_pid(pid, starttime)
        if error:
            return error
        if not self._has_privileged_scheduler():
            return self._result(
                True, visible=bool(visible), restored=bool(visible),
                degraded=["privileged-policy-unavailable"])
        backgrounds = self._load_json("background.json")
        key = str(pid)
        if visible:
            record = backgrounds.pop(key, {}) if isinstance(backgrounds.get(key), dict) else {}
            original = record.get("original_nice", info.get("nice", 0) if info else 0)
            self._call(("renice", "-n", str(original), "-p", str(pid)))
            self._call(("ionice", "-c2", "-n4", "-p", str(pid)))
            self._save_json("background.json", backgrounds)
            self._publish_status(backgrounds=backgrounds)
            return self._result(True, visible=True, restored=True)
        backgrounds[key] = {
            "pid": pid,
            "starttime": str(starttime),
            "desktop_file": str(desktop_file),
            "original_nice": info.get("nice", 0) if info else 0,
            "updated_at": self.clock(),
        }
        degraded = []
        if not self._call(("renice", "-n", "+10", "-p", str(pid))):
            degraded.append("renice-unavailable")
        if not self._call(("ionice", "-c3", "-p", str(pid))):
            degraded.append("ionice-unavailable")
        self._save_json("background.json", backgrounds)
        self._publish_status(backgrounds=backgrounds)
        return self._result(True, visible=False, degraded=degraded)

    def cgroup_status(self) -> dict[str, Any]:
        controllers_file = self.cgroup_root / "cgroup.controllers"
        try:
            controllers = sorted(set(controllers_file.read_text(encoding="ascii").split()))
            version = 2
        except OSError:
            controllers = []
            version = 0
        degraded = []
        if version != 2:
            degraded.append("cgroup-unavailable")
        for controller in ("cpu", "io"):
            if controller not in controllers:
                degraded.append("missing-%s-controller" % controller)
        return {"version": version, "controllers": controllers, "degraded": list(dict.fromkeys(degraded))}

    def status(self) -> dict[str, Any]:
        leases = self._load_json("leases.json")
        leases, changed = self._prune_expired_leases(leases)
        if changed:
            self._save_json("leases.json", leases)
        backgrounds = self._load_json("background.json")
        return self._publish_status(leases=leases, backgrounds=backgrounds)

    def prefetch_status(self) -> dict[str, Any]:
        rotational = []
        for path in pathlib.Path("/sys/block").glob("*/queue/rotational"):
            try:
                if path.read_text(encoding="ascii").strip() == "1":
                    rotational.append(path.parts[-3])
            except OSError:
                continue
        return {"ok": True, "enabled": bool(rotational), "backend": "posix_fadvise", "hdd_devices": rotational}


def _print(payload: dict[str, Any], stdout: Any) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if payload.get("ok") else 2


def main(argv: Sequence[str] | None = None, *, service: PerformancePolicy | None = None, stdout: Any = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    service = service or PerformancePolicy()
    invoked = pathlib.Path(sys.argv[0]).name

    if invoked == "ming-prefetch":
        if argv == ["status", "--json"]:
            return _print(service.prefetch_status(), stdout)
        print("Usage: ming-prefetch status --json", file=sys.stderr)
        return 2

    if invoked == "ming-interaction-boost":
        parser = argparse.ArgumentParser(prog=invoked)
        sub = parser.add_subparsers(dest="action", required=True)
        begin = sub.add_parser("begin")
        begin.add_argument("--pid", type=int, required=True)
        begin.add_argument("--starttime", required=True)
        begin.add_argument("--reason", choices=("launch", "activate"), required=True)
        begin.add_argument("--json", action="store_true")
        end = sub.add_parser("end")
        end.add_argument("--token", required=True)
        end.add_argument("--json", action="store_true")
        args = parser.parse_args(argv)
        payload = service.begin(args.pid, args.starttime, args.reason) if args.action == "begin" else service.end(args.token)
        return _print(payload, stdout)

    if invoked == "ming-background-policy":
        parser = argparse.ArgumentParser(prog=invoked)
        sub = parser.add_subparsers(dest="action", required=True)
        apply = sub.add_parser("apply")
        apply.add_argument("--pid", type=int, required=True)
        apply.add_argument("--starttime", required=True)
        apply.add_argument("--desktop-file", required=True)
        apply.add_argument("--visible", choices=("true", "false"), required=True)
        apply.add_argument("--json", action="store_true")
        status = sub.add_parser("status")
        status.add_argument("--json", action="store_true")
        args = parser.parse_args(argv)
        payload = service.status() if args.action == "status" else service.apply_background(
            args.pid, args.starttime, args.desktop_file, args.visible == "true")
        return _print(payload, stdout)

    if argv == ["status", "--json"]:
        return _print(service.status(), stdout)
    print("Usage: ming-performance-policy status --json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
