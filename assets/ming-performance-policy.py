#!/usr/bin/env python3
"""Bounded foreground/background resource policy for the Ming shell.

The daemon is the only path that may use root capabilities.  User-facing
commands exchange small JSON requests over a Unix socket and fall back to a
non-privileged no-op/degraded result when the socket or kernel feature is
unavailable.  No policy is permanent: every change is represented by a lease
or a background snapshot and is restored on expiry or visibility changes.
"""

# Public aliases installed by the image: ming-interaction-boost,
# ming-background-policy and ming-performance-policy.  The cgroup v2 policy
# uses SO_PEERCRED and process starttime checks before touching any PID.
# cgroup v2 controls include CPUWeight/IOWeight and timer_slack_ns. Background
# applications never share a hard CPU quota because one busy process must not
# stall every other minimized application in the same slice.

from __future__ import annotations

import argparse
import configparser
import errno
import json
import os
import pathlib
try:
    import pwd
except ImportError:  # pragma: no cover - Windows-only source validation
    pwd = None
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from typing import Any


SOURCE = pathlib.Path(__file__).read_text(encoding="utf-8", errors="replace")
DEFAULT_SOCKET = pathlib.Path("/run/ming-os/resource-policy.sock")
USER_SOCKET = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "ming-os" / "resource-policy.sock"
CURRENT_UID = int(getattr(os, "getuid", lambda: 0)())
CURRENT_EUID = int(getattr(os, "geteuid", lambda: CURRENT_UID)())
TRUSTED_DESKTOP_ROOTS = (
    pathlib.Path("/usr/share/applications"),
    pathlib.Path("/usr/local/share/applications"),
)
if pwd is not None:
    try:
        TRUSTED_DESKTOP_ROOTS += (
            pathlib.Path(pwd.getpwuid(CURRENT_UID).pw_dir) / ".local/share/applications",
        )
    except (KeyError, OSError):
        pass
LEASE_SECONDS = 1.5
MAX_LEASE_SECONDS = 3.0
COMPLETED_TOKEN_TTL_SECONDS = 30.0
COMPLETED_TOKEN_LIMIT = 256
PROTECTED_PROCESSES = {
    "ming-phone-desktop", "ming-dock", "ming-launch", "plank", "picom",
    "fcitx5", "fcitx5-qt", "pulseaudio", "pipewire", "wireplumber",
    "networkmanager", "bluetoothd", "xfwm4", "xfce4-session", "lightdm",
    "xorg", "xwayland", "ming-settings", "ming-files", "ming-terminal",
    "xfdesktop", "ming-update", "ming-transaction-health",
    "ming-transaction-reconcile", "ming-transaction-rollback-reboot",
}
ALLOWED_REASONS = {"launch", "activate"}


def is_protected_process(name: str) -> bool:
    normalized = pathlib.Path(str(name or "")).name.lower()
    return normalized in PROTECTED_PROCESSES or normalized.startswith("ming-transaction-")


class LeaseState:
    """Small deterministic lease registry used by the daemon and unit tests."""

    def __init__(self):
        self.leases: dict[str, dict[str, Any]] = {}

    def begin(self, pid: int, starttime: str, now: float | None = None,
              duration: float = LEASE_SECONDS) -> str:
        now = time.monotonic() if now is None else float(now)
        duration = min(MAX_LEASE_SECONDS, max(0.1, float(duration)))
        for token, lease in self.leases.items():
            if (int(lease.get("pid", -1)) == int(pid)
                    and str(lease.get("starttime", "")) == str(starttime)
                    and now < float(lease.get("expires", 0.0))):
                # One process may hold at most one lease.  A repeated launch
                # acknowledgement reuses it and never stacks a second
                # governor/nice restoration timer.
                lease["expires"] = min(
                    max(float(lease["expires"]), now + duration),
                    float(lease.get("created", now)) + MAX_LEASE_SECONDS,
                )
                return token
        token = secrets.token_urlsafe(18)
        self.leases[token] = {
            "pid": int(pid), "starttime": str(starttime),
            "created": now, "expires": now + duration,
        }
        return token

    def active(self, token: str, now: float | None = None) -> bool:
        lease = self.leases.get(str(token))
        if not lease:
            return False
        current = time.monotonic() if now is None else float(now)
        return current < float(lease["expires"])

    def reap(self, now: float | None = None) -> list[str]:
        current = time.monotonic() if now is None else float(now)
        expired = [token for token, lease in self.leases.items()
                   if current >= float(lease["expires"])]
        for token in expired:
            self.leases.pop(token, None)
        return expired


def _json(ok: bool, **values: Any) -> dict[str, Any]:
    return {"ok": bool(ok), **values}


def _policy_log(event: str, uid: int | None = None, **fields: Any) -> None:
    """Append bounded structured JSONL diagnostics without logging secrets."""
    record: dict[str, Any] = {"timestamp": time.time(), "event": str(event)}
    for key, value in fields.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            record[str(key)] = value
        elif isinstance(value, (list, tuple)):
            record[str(key)] = [str(item)[:80] for item in value[:16]]
        else:
            record[str(key)] = str(value)[:160]
    paths: list[pathlib.Path] = []
    if pwd is not None:
        try:
            home = pathlib.Path(pwd.getpwuid(int(uid if uid is not None else CURRENT_UID)).pw_dir)
            paths.append(home / ".cache/ming-os/resource-policy.jsonl")
        except (KeyError, OSError):
            pass
    paths.append(pathlib.Path("/var/log/ming-os/resource-policy.jsonl"))
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            for ancestor in path.parents:
                ancestor_stat = ancestor.lstat()
                if ancestor.is_symlink() or (ancestor_stat.st_mode & 0o022):
                    raise OSError("unsafe log directory")
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(path), flags | nofollow, 0o600)
            try:
                file_stat = os.fstat(fd)
                owners = {0}
                if uid is not None:
                    owners.add(int(uid))
                if (file_stat.st_nlink != 1 or (file_stat.st_mode & 0o022)
                        or (getattr(file_stat, "st_uid", 0) not in owners)):
                    continue
                with os.fdopen(fd, "a", encoding="utf-8") as stream:
                    fd = -1
                    stream.write(line)
            finally:
                if fd >= 0:
                    os.close(fd)
        except OSError:
            continue


def _read(path: str | os.PathLike[str], default: str = "") -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def _proc_name(pid: int) -> str:
    return _read(f"/proc/{int(pid)}/comm") or _read(f"/proc/{int(pid)}/cmdline").split("\x00", 1)[0]


def process_is_protected(pid: int) -> bool:
    names = [_read(f"/proc/{int(pid)}/comm")]
    names.extend(_read(f"/proc/{int(pid)}/cmdline").split("\x00"))
    return any(is_protected_process(name) for name in names if name)


def _proc_uid(pid: int) -> int | None:
    text = _read(f"/proc/{int(pid)}/status")
    for line in text.splitlines():
        if line.startswith("Uid:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def process_starttime(pid: int) -> str | None:
    text = _read(f"/proc/{int(pid)}/stat")
    if not text:
        return None
    try:
        # The comm field may contain spaces and parentheses; the final ')' is
        # the stable delimiter before the numeric fields.
        fields = text.rsplit(")", 1)[1].split()
        return fields[19]  # field 22 in procfs, after pid/comm/state
    except (IndexError, ValueError):
        return None


def _run(argv: list[str], timeout: float = 1.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=max(0.1, timeout), check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 126, "", str(exc)


def cgroup_v2_root() -> pathlib.Path | None:
    mounts = _read("/proc/self/mountinfo")
    for line in mounts.splitlines():
        if " - cgroup2 " in line:
            fields = line.split()
            try:
                separator = fields.index("-")
                mount_point = fields[4]
                if separator > 0:
                    return pathlib.Path(mount_point)
            except (ValueError, IndexError):
                continue
    return pathlib.Path("/sys/fs/cgroup") if pathlib.Path("/sys/fs/cgroup/cgroup.controllers").exists() else None


def _slice_path(name: str, uid: int | None = None) -> pathlib.Path | None:
    root = cgroup_v2_root()
    if root is None:
        return None
    user_root = root / "user.slice" / f"user-{int(uid)}.slice" if uid is not None else None
    if user_root is not None and user_root.exists():
        path = user_root / name
    else:
        path = root / "ming-os" / name
    try:
        path.mkdir(parents=True, exist_ok=True)
        if name == "ming-background.slice":
            (path / "cpu.weight").write_text("20\n", encoding="ascii")
            (path / "io.weight").write_text("default 20\n", encoding="ascii")
        elif name == "ming-foreground.slice":
            (path / "cpu.weight").write_text("100\n", encoding="ascii")
            (path / "io.weight").write_text("default 100\n", encoding="ascii")
        (path / "cgroup.procs").exists()
        return path
    except OSError:
        return None


def _move_pid(pid: int, slice_name: str, uid: int | None = None) -> bool:
    path = _slice_path(slice_name, uid=uid)
    if path is None:
        return False
    try:
        (path / "cgroup.procs").write_text(f"{int(pid)}\n", encoding="ascii")
        return True
    except OSError:
        return False


def _cgroup_relative_path(pid: int) -> str | None:
    text = _read(f"/proc/{int(pid)}/cgroup")
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0":
            value = fields[2].strip()
            return value if value.startswith("/") else None
    return None


def cgroup_path_is_safe(relative_path: str, uid: int) -> bool:
    value = str(relative_path or "")
    prefix = f"/user.slice/user-{int(uid)}.slice/"
    return value.startswith(prefix) and ".." not in pathlib.PurePosixPath(value).parts


def _restore_cgroup(pid: int, relative_path: str | None, uid: int) -> bool:
    if not relative_path or not cgroup_path_is_safe(relative_path, uid):
        return False
    root = cgroup_v2_root()
    if root is None:
        return False
    try:
        target = (root / relative_path.lstrip("/")).resolve()
        if root.resolve() not in target.parents or not target.is_dir():
            return False
        (target / "cgroup.procs").write_text(f"{int(pid)}\n", encoding="ascii")
        return True
    except OSError:
        return False


def _set_timer_slack(pid: int, value: int) -> bool:
    try:
        pathlib.Path(f"/proc/{int(pid)}/timerslack_ns").write_text(str(int(value)), encoding="ascii")
        return True
    except OSError:
        return False


def _apply_nice(pid: int, value: int) -> tuple[bool, str]:
    setpriority = getattr(os, "setpriority", None)
    prio_process = getattr(os, "PRIO_PROCESS", None)
    if not callable(setpriority) or prio_process is None:
        return False, "nice-unavailable"
    try:
        setpriority(prio_process, int(pid), int(value))
        return True, "nice"
    except (PermissionError, ProcessLookupError, OSError):
        return False, "nice-unavailable"


def _apply_ionice(pid: int, idle: bool = False) -> bool:
    command = ["ionice", "-c", "3" if idle else "2"]
    if not idle:
        command += ["-n", "0"]
    command += ["-p", str(int(pid))]
    return _run(command, timeout=0.2)[0] == 0


def _ionice_snapshot(pid: int) -> dict[str, int] | None:
    rc, output, _ = _run(["ionice", "-p", str(int(pid))], timeout=0.2)
    if rc != 0:
        return None
    import re
    class_match = re.search(r"class\s+(\d+)", output)
    priority_match = re.search(r"priority\s+(\d+)", output)
    if not class_match:
        return None
    return {"class": int(class_match.group(1)), "priority": int(priority_match.group(1)) if priority_match else 0}


def _restore_ionice(pid: int, snapshot: dict[str, int] | None) -> bool:
    if not isinstance(snapshot, dict):
        return _apply_ionice(pid, idle=False)
    class_id = int(snapshot.get("class", 0))
    command = ["ionice", "-c", str(class_id)]
    if class_id in {1, 2}:
        command += ["-n", str(int(snapshot.get("priority", 0)))]
    command += ["-p", str(int(pid))]
    return _run(command, timeout=0.2)[0] == 0


def _pulse_ota_yield() -> bool:
    helper = pathlib.Path("/usr/local/bin/ming-ota-yield")
    if not helper.exists():
        return True
    try:
        subprocess.Popen(
            [str(helper), "pulse", "--duration-ms", "1000", "--json"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        )
        return True
    except (OSError, ValueError):
        return False


def _timer_slack(pid: int, default: int = 50_000) -> int:
    try:
        return int(_read(f"/proc/{int(pid)}/timerslack_ns", str(default)))
    except ValueError:
        return default


def _governor_snapshot() -> list[tuple[str, str, str]]:
    global LAST_GOVERNOR_GATE
    power = _power_snapshot()
    thermal = _thermal_snapshot()
    allowed, reason = governor_boost_allowed(power, thermal)
    LAST_GOVERNOR_GATE = {
        "allowed": bool(allowed),
        "reason": reason,
        "power": power,
        "thermal": thermal,
    }
    if not allowed:
        return []
    snapshots = []
    for path in pathlib.Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_governor"):
        try:
            available = path.with_name("scaling_available_governors").read_text(encoding="ascii").split()
            current = path.read_text(encoding="ascii").strip()
            if "performance" in available and current != "performance":
                path.write_text("performance\n", encoding="ascii")
                snapshots.append((str(path), current, "performance"))
        except OSError:
            continue
    return snapshots


LAST_GOVERNOR_GATE: dict[str, Any] = {
    "allowed": False,
    "reason": "not-checked",
    "power": {},
    "thermal": {},
}


def _power_snapshot() -> dict[str, Any]:
    root = pathlib.Path("/sys/class/power_supply")
    if not root.is_dir():
        return {"ac_online": False, "battery_present": False, "available": False,
                "reason": "power-source-unavailable"}
    battery_present = False
    ac_online = False
    ac_seen = False
    for device in root.iterdir():
        if not device.is_dir():
            continue
        kind = _read(device / "type").lower()
        name = device.name.upper()
        if kind == "battery" or name.startswith("BAT"):
            battery_present = True
        if kind in {"mains", "ac", "usb", "usb_c"} or name.startswith(("AC", "ADP")):
            ac_seen = True
            if _read(device / "online") == "1":
                ac_online = True
    return {
        "ac_online": ac_online,
        "battery_present": battery_present,
        "available": bool(ac_seen or battery_present),
        "reason": "ac" if ac_online else ("battery" if battery_present else "unknown"),
    }


def _thermal_snapshot() -> dict[str, Any]:
    root = pathlib.Path("/sys/class/thermal")
    temperatures: list[float] = []
    margins: list[float] = []
    if root.is_dir():
        for zone in root.glob("thermal_zone*"):
            try:
                temp_c = float(_read(zone / "temp")) / 1000.0
            except (TypeError, ValueError):
                continue
            if not -50.0 <= temp_c <= 200.0:
                continue
            temperatures.append(temp_c)
            criticals: list[float] = []
            for trip in zone.glob("trip_point_*_temp"):
                try:
                    value = float(_read(trip)) / 1000.0
                except (TypeError, ValueError):
                    continue
                if 40.0 <= value <= 200.0:
                    criticals.append(value)
            critical = min(criticals) if criticals else 95.0
            margins.append(critical - temp_c)
    if not temperatures:
        return {"available": False, "reason": "temperature-unavailable"}
    margin = min(margins) if margins else 0.0
    return {
        "available": True,
        "temperature_c": max(temperatures),
        "critical_margin_c": margin,
        "reason": "ok" if margin >= 10.0 else "thermal-margin-low",
    }


def governor_boost_allowed(power: dict[str, Any], thermal: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(power, dict) or not power.get("ac_online"):
        return False, "ac-power-required"
    if not isinstance(thermal, dict) or not thermal.get("available"):
        return False, "temperature-unavailable"
    try:
        margin = float(thermal.get("critical_margin_c"))
    except (TypeError, ValueError):
        return False, "temperature-unavailable"
    if margin < 10.0:
        return False, "thermal-margin-low"
    return True, "ok"


def desktop_file_is_trusted(desktop_file: str, uid: int | None = None) -> bool:
    """Validate a launcher before allowing background policy changes."""
    raw = pathlib.Path(str(desktop_file or ""))
    if not raw.is_absolute() or raw.suffix != ".desktop" or raw.is_symlink():
        return False
    try:
        if any(parent.is_symlink() for parent in raw.parents if parent.exists()):
            return False
    except OSError:
        return False
    try:
        resolved = raw.resolve(strict=True)
        stat_result = resolved.stat()
    except OSError:
        return False
    # Windows source validation has no POSIX mode bits; the installed Linux
    # daemon still rejects group/world-writable launchers.
    if not resolved.is_file() or (getattr(os, "name", "posix") != "nt" and stat_result.st_mode & 0o022):
        return False
    roots = list(pathlib.Path(root).resolve() for root in TRUSTED_DESKTOP_ROOTS)
    if uid is not None and pwd is not None:
        try:
            user_root = pathlib.Path(pwd.getpwuid(int(uid)).pw_dir) / ".local/share/applications"
            if user_root.resolve() not in roots:
                roots.append(user_root.resolve())
        except (KeyError, OSError):
            pass
    matched_root = next((root for root in roots if resolved.parent == root), None)
    if matched_root is None:
        return False
    owner_uid = getattr(stat_result, "st_uid", None)
    system_roots = {
        pathlib.Path("/usr/share/applications").resolve(),
        pathlib.Path("/usr/local/share/applications").resolve(),
    }
    if matched_root not in system_roots:
        allowed_uids = {int(CURRENT_UID)}
        if uid is not None:
            allowed_uids.add(int(uid))
        if owner_uid is not None and int(owner_uid) not in allowed_uids:
            return False
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        with resolved.open("r", encoding="utf-8", errors="strict") as stream:
            parser.read_file(stream)
        if parser.get("Desktop Entry", "Type", fallback="") != "Application":
            return False
        exec_line = parser.get("Desktop Entry", "Exec", fallback="").strip()
        if not exec_line or any(token in exec_line for token in (";", "|", "&", ">", "<")):
            return False
        shlex.split(exec_line, posix=True)
    except (OSError, ValueError, configparser.Error):
        return False
    return True


def background_throttle_exemption(pid: int) -> tuple[bool, str]:
    """Keep explicit no-throttle processes and active PulseAudio streams responsive."""
    environment = _read(f"/proc/{int(pid)}/environ")
    if "MING_NO_BACKGROUND_THROTTLE=1" in environment.split("\x00"):
        return True, "explicit"
    rc, output, _ = _run(["pactl", "list", "sink-inputs"], timeout=1.0)
    if rc == 0:
        lowered = output.lower()
        process_id = f'application.process.id = "{int(pid)}"'.lower()
        if process_id in lowered and ("corked: no" in lowered or "corked = no" in lowered):
            return True, "active-audio"
    return False, ""


def _restore_governors(snapshots: list[tuple[str, ...]] | None) -> None:
    for snapshot in snapshots or []:
        if len(snapshot) == 2:
            path, value = snapshot
            lease_value = None
        elif len(snapshot) == 3:
            path, value, lease_value = snapshot
        else:
            continue
        try:
            target = pathlib.Path(path)
            if lease_value is not None:
                current = target.read_text(encoding="ascii").strip()
                if current != str(lease_value):
                    continue
            target.write_text(str(value) + "\n", encoding="ascii")
        except OSError:
            pass


def _validate_pid(pid: int, starttime: str | None, expected_uid: int | None = None) -> tuple[bool, str]:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False, "invalid-pid"
    if pid <= 1 or not pathlib.Path(f"/proc/{pid}").is_dir():
        return False, "process-not-found"
    if starttime and process_starttime(pid) != str(starttime):
        return False, "starttime-mismatch"
    if expected_uid is not None and _proc_uid(pid) != int(expected_uid):
        return False, "uid-mismatch"
    if process_is_protected(pid):
        return False, "protected-process"
    return True, ""


class ResourcePolicy:
    def __init__(self, session_uid: int | None = None):
        self.session_uid = int(session_uid) if session_uid is not None else CURRENT_UID
        self.leases = LeaseState()
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.background: dict[tuple[int, str], dict[str, Any]] = {}
        self.background_generations: dict[tuple[int, str], int] = {}
        self.lease_timers: dict[str, threading.Timer] = {}
        self.state_lock = threading.RLock()
        self.completed_tokens: dict[str, float] = {}
        self.governor_tokens: set[str] = set()
        self.governor_base_snapshot: Any = None

    def _schedule_lease_timer(self, token: str) -> None:
        with self.state_lock:
            lease = self.leases.leases.get(str(token))
            if not lease:
                return
            previous = self.lease_timers.pop(str(token), None)
            if previous is not None:
                previous.cancel()
            delay = max(0.01, float(lease["expires"]) - time.monotonic())
            timer = threading.Timer(delay, self._expire_lease, args=(str(token),))
            timer.daemon = True
            self.lease_timers[str(token)] = timer
            timer.start()

    def _expire_lease(self, token: str) -> dict[str, Any]:
        with self.state_lock:
            if self.leases.active(str(token)):
                self._schedule_lease_timer(str(token))
                return _json(
                    True, token=str(token), restored=False, rescheduled=True)
            return self.end(str(token))

    def _prune_completed_tokens(self) -> None:
        cutoff = time.monotonic() - COMPLETED_TOKEN_TTL_SECONDS
        stale = [token for token, stamp in self.completed_tokens.items() if stamp < cutoff]
        for token in stale:
            self.completed_tokens.pop(token, None)
        if len(self.completed_tokens) > COMPLETED_TOKEN_LIMIT:
            ordered = sorted(self.completed_tokens.items(), key=lambda item: item[1])
            for token, _stamp in ordered[:-COMPLETED_TOKEN_LIMIT]:
                self.completed_tokens.pop(token, None)

    def _acquire_governor(self, token: str) -> dict[str, Any]:
        with self.state_lock:
            if not self.governor_tokens:
                self.governor_base_snapshot = _governor_snapshot()
            self.governor_tokens.add(str(token))
            return dict(LAST_GOVERNOR_GATE)

    def _release_governor(self, token: str, snapshot: dict[str, Any]) -> tuple[bool, Any]:
        if str(token) in self.governor_tokens:
            self.governor_tokens.remove(str(token))
            if self.governor_tokens:
                return False, None
            base = self.governor_base_snapshot
            self.governor_base_snapshot = None
            return True, base
        if "governors" in snapshot:
            return True, snapshot.get("governors")
        return False, None

    def _log(self, event: str, **fields: Any) -> None:
        _policy_log(event, uid=self.session_uid, **fields)

    def _settings(self) -> dict[str, Any]:
        if pwd is None:
            return {}
        try:
            home = pathlib.Path(pwd.getpwuid(self.session_uid).pw_dir)
            value = json.loads((home / ".config/ming-os/settings.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (KeyError, OSError, ValueError):
            return {}

    def _reject_background_identity(
            self, key: tuple[int, str], error: str,
            rollback_path: str | None = None, moved: bool = False) -> dict[str, Any]:
        rollback_ok = False
        if moved:
            # This is only an undo of the move made by this request.  It is
            # never a business restore for an unverified/reused identity.
            rollback_ok = _restore_cgroup(
                key[0], rollback_path, self.session_uid)
        with self.state_lock:
            self.background.pop(key, None)
            self.background_generations.pop(key, None)
        self._log(
            "background_rejected", pid=int(key[0]), reason=str(error),
            rollback=bool(moved and rollback_ok),
        )
        return _json(
            False, pid=int(key[0]), error=str(error),
            rolled_back=bool(moved and rollback_ok),
        )

    def _revalidate_background_identity(
            self, key: tuple[int, str], rollback_path: str | None = None,
            moved: bool = False) -> dict[str, Any] | None:
        ok, error = _validate_pid(key[0], key[1], self.session_uid)
        if ok:
            return None
        return self._reject_background_identity(
            key, error, rollback_path=rollback_path, moved=moved)

    def _prune_background_state(self, preserve: tuple[int, str] | None = None) -> None:
        keys = set(self.background) | set(self.background_generations)
        for key in keys:
            if preserve is not None and key == preserve:
                continue
            pid, starttime = key
            if process_starttime(pid) == str(starttime):
                continue
            self.background.pop(key, None)
            self.background_generations.pop(key, None)

    def begin(self, pid: int, starttime: str, reason: str) -> dict[str, Any]:
        if reason not in ALLOWED_REASONS:
            self._log("lease_rejected", reason="invalid-reason")
            return _json(False, error="invalid-reason")
        if self._settings().get("interaction_policy", "adaptive") == "off":
            self._log("lease_rejected", reason="policy-disabled")
            return _json(False, error="policy-disabled", degraded=True)
        ok, error = _validate_pid(pid, starttime, self.session_uid)
        if not ok:
            self._log("lease_rejected", reason=error)
            return _json(False, error=error)
        with self.state_lock:
            for token, lease in tuple(self.leases.leases.items()):
                if (int(lease.get("pid", -1)) == int(pid)
                        and str(lease.get("starttime", "")) == str(starttime)
                        and not self.leases.active(token)):
                    self.end(token)
            existing = next(
                (token for token, lease in self.leases.leases.items()
                 if int(lease.get("pid", -1)) == int(pid)
                 and str(lease.get("starttime", "")) == str(starttime)
                 and self.leases.active(token)),
                None,
            )
            if existing and existing in self.snapshots:
                self.leases.begin(pid, starttime, duration=LEASE_SECONDS)
                lease = self.leases.leases[existing]
                self._schedule_lease_timer(existing)
                self._log("lease_reused", pid=int(pid))
                return _json(
                    True,
                    token=existing,
                    pid=int(pid),
                    expires_in=max(0.0, float(lease["expires"]) - time.monotonic()),
                    duplicate=True,
                    actions={},
                    degraded=[],
                )
            token = self.leases.begin(pid, starttime, duration=LEASE_SECONDS)
            old_nice = None
            try:
                getpriority = getattr(os, "getpriority", None)
                prio_process = getattr(os, "PRIO_PROCESS", None)
                if callable(getpriority) and prio_process is not None:
                    old_nice = getpriority(prio_process, int(pid))
            except (OSError, AttributeError):
                pass
            old_ionice = _ionice_snapshot(pid)
            old_slack = _timer_slack(pid)
            nice_ok, nice_detail = _apply_nice(pid, -10)
            if not nice_ok:
                nice_ok, nice_detail = _apply_nice(pid, -5)
            io_ok = _apply_ionice(pid, idle=False)
            old_cgroup = _cgroup_relative_path(pid)
            cgroup_ok = _move_pid(pid, "ming-foreground.slice", self.session_uid)
            ota_yield = _pulse_ota_yield()
            governor_gate = self._acquire_governor(token)
            self.snapshots[token] = {
                "pid": int(pid), "starttime": str(starttime), "nice": old_nice,
                "ionice": old_ionice, "timer_slack": old_slack,
                "cgroup_path": old_cgroup,
                "governor_gate": governor_gate,
                "cgroup": cgroup_ok,
            }
            self._schedule_lease_timer(token)
            result = _json(
                True, token=token, pid=int(pid), expires_in=LEASE_SECONDS,
                actions={"nice": nice_detail, "ionice": io_ok, "cgroup": cgroup_ok, "ota_yield": ota_yield},
                degraded=[name for name, value in (("nice", nice_ok), ("ionice", io_ok), ("cgroup", cgroup_ok), ("ota_yield", ota_yield)) if not value],
            )
            self._log("lease_started", pid=int(pid),
                      degraded=result.get("degraded", []), governor=governor_gate.get("reason"))
            return result

    def end(self, token: str) -> dict[str, Any]:
        with self.state_lock:
            self._prune_completed_tokens()
            snapshot = self.snapshots.pop(str(token), None)
            self.leases.leases.pop(str(token), None)
            timer = self.lease_timers.pop(str(token), None)
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            if not snapshot:
                if str(token) in self.completed_tokens:
                    return _json(
                        True, token=str(token), restored=False, already_ended=True)
                self._log("lease_rejected", reason="unknown-token")
                return _json(False, error="unknown-token")
            pid = int(snapshot["pid"])
            if pathlib.Path(f"/proc/{pid}").is_dir() and process_starttime(pid) == snapshot["starttime"]:
                if snapshot.get("nice") is not None:
                    try:
                        setpriority = getattr(os, "setpriority", None)
                        prio_process = getattr(os, "PRIO_PROCESS", None)
                        if callable(setpriority) and prio_process is not None:
                            setpriority(prio_process, pid, int(snapshot["nice"]))
                    except (OSError, AttributeError):
                        pass
                _restore_ionice(pid, snapshot.get("ionice"))
                _set_timer_slack(pid, int(snapshot.get("timer_slack", 50_000)))
                _restore_cgroup(pid, snapshot.get("cgroup_path"), self.session_uid)
            restore_governor, governor_snapshot = self._release_governor(
                str(token), snapshot)
            if restore_governor:
                _restore_governors(governor_snapshot)
            self.completed_tokens[str(token)] = time.monotonic()
            self._log("lease_restored", pid=pid)
            return _json(True, token=str(token), restored=True)

    def apply_background(self, pid: int, starttime: str, desktop_file: str,
                         visible: bool, generation: int | None = None) -> dict[str, Any]:
        ok, error = _validate_pid(pid, starttime, self.session_uid)
        if not ok:
            self._log("background_rejected", reason=error)
            return _json(False, error=error)
        if not desktop_file_is_trusted(desktop_file, uid=self.session_uid):
            self._log("background_rejected", reason="desktop-file-not-allowlisted")
            return _json(False, error="desktop-file-not-allowlisted")
        key = (int(pid), str(starttime))
        with self.state_lock:
            rejected = self._revalidate_background_identity(key)
            if rejected:
                return rejected
            self._prune_background_state(preserve=key)
            for stale_key in tuple(self.background_generations):
                if stale_key[0] == key[0] and stale_key != key:
                    self.background_generations.pop(stale_key, None)
                    self.background.pop(stale_key, None)
            latest = self.background_generations.get(key, 0)
            try:
                requested_generation = int(generation or 0)
            except (TypeError, ValueError):
                return _json(False, error="invalid-generation")
            if requested_generation <= 0:
                requested_generation = latest + 1
            if requested_generation <= latest:
                rejected = self._revalidate_background_identity(key)
                if rejected:
                    return rejected
                self._log(
                    "background_stale", pid=int(pid), generation=requested_generation,
                    latest_generation=latest,
                )
                return _json(
                    True, pid=int(pid), visible=bool(visible), stale=True,
                    applied=False, generation=requested_generation,
                    latest_generation=latest,
                )
            self.background_generations[key] = requested_generation
        settings = self._settings()
        if not visible and settings.get("background_throttle", False) is False:
            rejected = self._revalidate_background_identity(key)
            if rejected:
                return rejected
            self._log("background_skipped", pid=int(pid), reason="policy-disabled")
            return _json(True, pid=int(pid), visible=False, skipped=True, reason="policy-disabled")
        if not visible:
            exempt, exemption_reason = background_throttle_exemption(pid)
            if exempt:
                rejected = self._revalidate_background_identity(key)
                if rejected:
                    return rejected
                self._log("background_skipped", pid=int(pid), reason=exemption_reason)
                return _json(
                    True, pid=int(pid), visible=False, skipped=True, reason=exemption_reason
                )
        stale_keys = [item for item in self.background if item[0] == int(pid) and item != key]
        for stale_key in stale_keys:
            self.background.pop(stale_key, None)
        rollback_path = _cgroup_relative_path(pid)
        rejected = self._revalidate_background_identity(key, rollback_path=rollback_path)
        if rejected:
            return rejected
        if visible:
            snapshot = self.background.get(key)
            if not snapshot:
                self._log("background_skipped", pid=int(pid), reason="not-backgrounded")
                return _json(
                    True, pid=int(pid), visible=True, skipped=True,
                    restored=False, reason="not-backgrounded",
                )
            self.background.pop(key, None)
            restored_cgroup = _restore_cgroup(
                pid, snapshot.get("cgroup_path") if snapshot else None, self.session_uid
            )
            moved = bool(restored_cgroup)
            if not restored_cgroup:
                moved = bool(_move_pid(pid, "ming-foreground.slice", self.session_uid))
            rejected = self._revalidate_background_identity(
                key, rollback_path=rollback_path, moved=moved)
            if rejected:
                return rejected
            self._log("background_restored", pid=int(pid))
            return _json(True, pid=int(pid), visible=True, restored=True)
        if key not in self.background:
            self.background[key] = {
                "cgroup_path": rollback_path,
                "desktop_file": desktop_file,
            }
        cgroup_ok = _move_pid(pid, "ming-background.slice", self.session_uid)
        rejected = self._revalidate_background_identity(
            key, rollback_path=rollback_path, moved=bool(cgroup_ok))
        if rejected:
            return rejected
        result = _json(True, pid=int(pid), visible=False,
                       actions={"cgroup": cgroup_ok},
                       degraded=["cgroup"] if not cgroup_ok else [])
        self._log("background_throttled", pid=int(pid), degraded=result.get("degraded", []))
        return result

    def status(self) -> dict[str, Any]:
        with self.state_lock:
            self._prune_background_state()
        cgroup = cgroup_v2_root()
        if LAST_GOVERNOR_GATE.get("reason") == "not-checked":
            power = _power_snapshot()
            thermal = _thermal_snapshot()
            allowed, reason = governor_boost_allowed(power, thermal)
            LAST_GOVERNOR_GATE.update(
                {"allowed": bool(allowed), "reason": reason, "power": power, "thermal": thermal}
            )
        degraded = [] if cgroup else ["cgroup-v2-unavailable"]
        if not LAST_GOVERNOR_GATE.get("allowed"):
            degraded.append("governor-%s" % LAST_GOVERNOR_GATE.get("reason", "unavailable"))
        return _json(
            True,
            mode="adaptive",
            cgroup_v2=bool(cgroup),
            active_leases=len(self.leases.leases),
            background_throttled=len(self.background),
            socket_peer_check=True,
            governor=dict(LAST_GOVERNOR_GATE),
            degraded=degraded,
        )


def _socket_path() -> pathlib.Path:
    return DEFAULT_SOCKET if CURRENT_EUID == 0 else USER_SOCKET


def _send_request(request: dict[str, Any], path: pathlib.Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_SOCKET
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(target))
            client.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
            data = client.recv(65536)
        return json.loads(data.decode("utf-8"))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError) as exc:
        return _json(False, error="policy-daemon-unavailable", detail=str(exc), degraded=True)


def _peer_uid(connection: socket.socket) -> int | None:
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return int.from_bytes(raw[4:8], byteorder=sys.byteorder)
    except (AttributeError, OSError):
        return None


def serve(uid: int, path: pathlib.Path = DEFAULT_SOCKET) -> int:
    if not hasattr(socket, "AF_UNIX"):
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    policy = ResourcePolicy(uid)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, 0o660)
        try:
            os.chown(path, int(uid), 0)
        except OSError:
            pass
        server.listen(8)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2.0)
                peer_uid = _peer_uid(connection)
                if peer_uid not in {int(uid), 0}:
                    connection.sendall(b'{"ok":false,"error":"peer-uid-rejected"}\n')
                    continue
                try:
                    request = json.loads(connection.recv(65536).decode("utf-8"))
                    command = request.get("command")
                    if command == "begin":
                        result = policy.begin(request.get("pid"), request.get("starttime", ""), request.get("reason", "launch"))
                    elif command == "end":
                        result = policy.end(request.get("token", ""))
                    elif command == "background":
                        result = policy.apply_background(request.get("pid"), request.get("starttime", ""), request.get("desktop_file", ""), bool(request.get("visible")), request.get("generation"))
                    elif command == "status":
                        result = policy.status()
                    else:
                        result = _json(False, error="unsupported-command")
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    result = _json(False, error="invalid-request", detail=str(exc))
                connection.sendall((json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("begin", "end", "background", "status", "daemon"))
    parser.add_argument("--pid", type=int)
    parser.add_argument("--starttime", default="")
    parser.add_argument("--reason", choices=sorted(ALLOWED_REASONS), default="launch")
    parser.add_argument("--token", default="")
    parser.add_argument("--desktop-file", default="")
    parser.add_argument("--visible", choices=("true", "false"), default="false")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--uid", type=int, default=CURRENT_UID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "daemon":
        return serve(args.uid)
    request = {"command": args.command}
    if args.command == "begin":
        request.update(pid=args.pid, starttime=args.starttime, reason=args.reason)
    elif args.command == "end":
        request["token"] = args.token
    elif args.command == "background":
        request.update(
            pid=args.pid, starttime=args.starttime, desktop_file=args.desktop_file,
            visible=args.visible == "true", generation=args.generation,
        )
    result = _send_request(request)
    if result.get("error") == "policy-daemon-unavailable" and args.command == "status":
        result = ResourcePolicy().status()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
