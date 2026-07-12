#!/usr/bin/env python3
"""Bounded, hardware-aware performance diagnostics for Ming OS.

The command is intentionally read-only.  It is used by the release gate and by
Ming Settings, so a missing sensor, an offline service, or a machine without a
graphics device must produce a useful JSON diagnostic instead of failing boot.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_TIMEOUT = 2.0
MAX_OUTPUT = 12_000


class CommandResult:
    __slots__ = ("returncode", "stdout", "stderr", "timed_out", "missing")

    def __init__(
        self,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        missing: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.missing = missing


def run_command(argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run one probe without a shell and with a hard upper time bound."""

    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout)),
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"{argv[0]}: command not found", missing=True)
    except subprocess.TimeoutExpired as error:
        detail = error.stderr or error.stdout or "probe timed out"
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        return CommandResult(124, "", str(detail), timed_out=True)
    except OSError as error:
        return CommandResult(126, "", str(error))
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _read_text(path: str | os.PathLike[str]) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return None


def _bounded(value: str, limit: int = MAX_OUTPUT) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def _int(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


class PerformanceStatus:
    """Collect all performance evidence through small bounded probes."""

    def __init__(
        self,
        runner: Callable[[Sequence[str], float], CommandResult] = run_command,
        read_text: Callable[[str | os.PathLike[str]], str | None] = _read_text,
        globber: Callable[[str], Iterable[str | os.PathLike[str]]] = glob.glob,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.runner = runner
        self.read_text = read_text
        self.globber = globber
        self.clock = clock
        self.diagnostics: list[str] = []

    def _probe(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        try:
            result = self.runner(tuple(argv), timeout)
        except TypeError:
            # Keep test doubles and older callers that use a keyword compatible.
            result = self.runner(tuple(argv), timeout=timeout)  # type: ignore[call-arg]
        except Exception as error:  # pragma: no cover - defensive startup guard
            result = CommandResult(126, "", str(error))
        if isinstance(result, CommandResult):
            normalized = result
        elif isinstance(result, subprocess.CompletedProcess):
            normalized = CommandResult(
                int(result.returncode), result.stdout or "", result.stderr or ""
            )
        elif isinstance(result, tuple) and len(result) >= 3:
            normalized = CommandResult(int(result[0]), str(result[1]), str(result[2]))
        else:  # pragma: no cover - protects the release gate from a bad adapter
            normalized = CommandResult(126, "", "invalid probe result")
        if normalized.returncode != 0:
            detail = _bounded(normalized.stderr or normalized.stdout, 240)
            reason = "timed out" if normalized.timed_out else detail or f"exit {normalized.returncode}"
            message = f"{' '.join(argv)}: {reason}"
            if message not in self.diagnostics:
                self.diagnostics.append(message)
        return normalized

    def _read(self, path: str | os.PathLike[str]) -> str | None:
        try:
            return self.read_text(path)
        except Exception as error:  # pragma: no cover - defensive startup guard
            message = f"read {path}: {error}"
            if message not in self.diagnostics:
                self.diagnostics.append(message)
            return None

    def _paths(self, pattern: str) -> list[Path]:
        try:
            return [Path(path) for path in self.globber(pattern)]
        except Exception as error:  # pragma: no cover - defensive startup guard
            message = f"glob {pattern}: {error}"
            if message not in self.diagnostics:
                self.diagnostics.append(message)
            return []

    def boot_status(self) -> dict[str, Any]:
        chain = self._probe(("systemd-analyze", "critical-chain"))
        timing = self._probe(("systemd-analyze", "time"))
        available = chain.returncode == 0 or timing.returncode == 0
        duration: float | None = None
        for text in (timing.stdout, chain.stdout):
            match = re.search(r"(?:Startup finished in\s+)?(\d+(?:\.\d+)?)s", text or "")
            if match:
                duration = float(match.group(1))
                break
        return {
            "state": "available" if available else "unavailable",
            "available": available,
            "duration_seconds": duration,
            "critical_chain": _bounded(chain.stdout),
            "timing": _bounded(timing.stdout),
            "error": None if available else _bounded(chain.stderr or timing.stderr, 240),
        }

    def memory_status(self) -> dict[str, Any]:
        text = self._read("/proc/meminfo")
        values: dict[str, int] = {}
        if text:
            for line in text.splitlines():
                match = re.match(r"^(\w+):\s+(\d+)(?:\s+(\w+))?", line)
                if not match:
                    continue
                multiplier = 1024 if (match.group(3) or "").lower() == "kb" else 1
                values[match.group(1)] = int(match.group(2)) * multiplier
        available = "MemTotal" in values
        if not available:
            self.diagnostics.append("/proc/meminfo: unavailable")
        return {
            "state": "available" if available else "unavailable",
            "total_bytes": values.get("MemTotal"),
            "available_bytes": values.get("MemAvailable", values.get("MemFree")),
            "free_bytes": values.get("MemFree"),
            "swap_total_bytes": values.get("SwapTotal", 0) if available else None,
            "swap_free_bytes": values.get("SwapFree", 0) if available else None,
        }

    def cpu_status(self) -> dict[str, Any]:
        cpuinfo = self._read("/proc/cpuinfo") or ""
        vendor_match = re.search(r"^vendor_id\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        model_match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        microcode_match = re.search(r"^microcode\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        vendor = vendor_match.group(1).strip() if vendor_match else "unknown"
        model = model_match.group(1).strip() if model_match else "unknown"
        microcode = microcode_match.group(1).strip() if microcode_match else "unknown"
        cpu_identity = "%s %s" % (vendor, model)
        if re.search(r"AuthenticAMD|AMD", cpu_identity, re.IGNORECASE):
            compatibility_class = "amd"
        elif re.search(r"Zhaoxin|兆芯|Shanghai|Centaur", cpu_identity, re.IGNORECASE):
            compatibility_class = "zhaoxin"
        elif re.search(r"GenuineIntel|Intel", cpu_identity, re.IGNORECASE):
            compatibility_class = "intel"
        else:
            compatibility_class = "generic-x86"
        driver = (self._read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver") or "").strip()
        current_paths = self._paths("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor")
        governors = sorted(
            {
                value.strip()
                for path in current_paths
                if (value := self._read(path)) and value.strip()
            }
        )
        available_governors: list[str] = []
        if current_paths:
            raw = self._read(current_paths[0].with_name("scaling_available_governors")) or ""
            available_governors = sorted(set(raw.split()))
        available = bool(driver or governors or available_governors)
        if not available:
            self.diagnostics.append("cpufreq: unavailable")
        return {
            "state": "available" if available else "unavailable",
            "vendor": vendor,
            "model": model,
            "microcode": microcode,
            "compatibility_class": compatibility_class,
            "thermal_strategy": "intel-thermald" if compatibility_class == "intel" else "kernel-tlp",
            "driver": driver or "unknown",
            "governors": governors,
            "available_governors": available_governors,
            "policy_count": len(current_paths),
        }

    def storage_status(self) -> dict[str, Any]:
        rotational_paths = self._paths("/sys/block/*/queue/rotational")
        devices: list[dict[str, Any]] = []
        for rotational_path in rotational_paths:
            device_dir = rotational_path.parent.parent
            name = device_dir.name
            rotational = _int(self._read(rotational_path))
            scheduler_path = rotational_path.parent / "scheduler"
            scheduler_raw = (self._read(scheduler_path) or "").strip()
            selected = re.search(r"\[([^]]+)\]", scheduler_raw)
            discard = _int(self._read(rotational_path.parent / "discard_max_bytes"))
            devices.append(
                {
                    "name": name,
                    "rotational": rotational == 1 if rotational is not None else None,
                    "scheduler": selected.group(1) if selected else (scheduler_raw or "unknown"),
                    "scheduler_available": scheduler_raw.split(),
                    "discard_supported": bool(discard and discard > 0),
                }
            )

        trim_probe = self._probe(("systemctl", "is-enabled", "fstrim.timer"))
        trim_state = trim_probe.stdout.strip() or (
            "unavailable" if trim_probe.missing or trim_probe.timed_out else "disabled"
        )
        trim_available = bool(devices) or trim_probe.returncode == 0
        if not devices and trim_probe.returncode != 0:
            self.diagnostics.append("storage: no block-device queue evidence")
        return {
            "state": "available" if devices else "unavailable",
            "devices": devices,
            "trim": {
                "available": trim_available,
                "timer_state": trim_state,
                "timer_enabled": trim_state == "enabled",
                "discard_devices": [
                    device["name"] for device in devices if device["discard_supported"]
                ],
                "evidence": "fstrim.timer and queue/discard_max_bytes",
            },
        }

    @staticmethod
    def _flatten_sensor_values(value: Any, prefix: str = "") -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, Mapping):
            for key, child in value.items():
                name = f"{prefix}/{key}" if prefix else str(key)
                found.extend(PerformanceStatus._flatten_sensor_values(child, name))
        elif isinstance(value, (int, float)) and "temp" in prefix.lower():
            celsius = float(value)
            if abs(celsius) > 200:
                celsius /= 1000.0
            found.append({"name": prefix, "celsius": round(celsius, 2)})
        return found

    def temperatures_status(self) -> dict[str, Any]:
        sensors = self._probe(("sensors", "-j"))
        readings: list[dict[str, Any]] = []
        if sensors.returncode == 0 and sensors.stdout.strip():
            try:
                readings = self._flatten_sensor_values(json.loads(sensors.stdout))
            except (TypeError, ValueError):
                self.diagnostics.append("sensors -j: invalid JSON")
        if not readings:
            for temp_path in self._paths("/sys/class/thermal/thermal_zone*/temp"):
                raw = _int(self._read(temp_path))
                if raw is None:
                    continue
                label = (self._read(temp_path.with_name("type")) or temp_path.parent.name).strip()
                readings.append({"name": label, "celsius": round(raw / 1000.0, 2)})
        available = bool(readings)
        if not available:
            self.diagnostics.append("temperature sensors: unavailable")
        return {
            "state": "available" if available else "unavailable",
            "available": available,
            "readings": readings,
            "source": "lm-sensors" if sensors.returncode == 0 and readings else "sysfs",
        }

    def _service(self, label: str, unit: str) -> dict[str, Any]:
        result = self._probe(("systemctl", "is-active", unit))
        state = result.stdout.strip() or (
            "unknown" if result.missing or result.timed_out else "inactive"
        )
        return {
            "state": state,
            "active": state == "active",
            "available": not (result.missing or result.timed_out),
            "unit": unit,
        }

    def _process(self, label: str, process: str) -> dict[str, Any]:
        result = self._probe(("pgrep", "-x", process))
        if result.missing or result.timed_out:
            state = "unknown"
        else:
            state = "active" if result.returncode == 0 else "inactive"
        return {
            "state": state,
            "active": state == "active",
            "available": not (result.missing or result.timed_out),
            "process": process,
        }

    def service_status(self) -> dict[str, Any]:
        return {
            "ModemManager": self._service("ModemManager", "ModemManager.service"),
            "CUPS": self._service("CUPS", "cups.service"),
            "Avahi": self._service("Avahi", "avahi-daemon.service"),
            "BlueZ": self._service("BlueZ", "bluetooth.service"),
            "Picom": self._process("Picom", "picom"),
            "Dock": self._process("Dock", "plank"),
        }

    def graphics_status(self) -> dict[str, Any]:
        vaapi = self._probe(("vainfo", "--display", "drm"))
        render_nodes = [str(path) for path in self._paths("/dev/dri/renderD*")]
        xorg_log = "\n".join(
            text for path in ("/var/log/Xorg.0.log", "/var/log/Xorg.1.log") if (text := self._read(path))
        )
        if re.search(r"modesetting", xorg_log, re.IGNORECASE):
            backend = "modesetting"
        elif re.search(r"xf86-video-intel|intel\(0\)", xorg_log, re.IGNORECASE):
            backend = "legacy-intel-ddx"
        else:
            backend = "unknown"
        return {
            "vaapi": {
                "state": "available" if vaapi.returncode == 0 else "unavailable",
                "available": vaapi.returncode == 0,
                "output": _bounded(vaapi.stdout),
                "error": _bounded(vaapi.stderr, 240) if vaapi.returncode else None,
            },
            "render_nodes": render_nodes,
            "xorg_backend": backend,
            "fallback": "software" if not render_nodes else "hardware-or-modesetting",
        }

    def status(self) -> dict[str, Any]:
        self.diagnostics = []
        payload = {
            "schema_version": 1,
            "ok": True,
            "generated_at": int(self.clock()),
            "probe_timeout_seconds": DEFAULT_TIMEOUT,
            "boot": self.boot_status(),
            "memory": self.memory_status(),
            "cpu": self.cpu_status(),
            "storage": self.storage_status(),
            "temperatures": self.temperatures_status(),
            "services": self.service_status(),
            "graphics": self.graphics_status(),
        }
        payload["diagnostics"] = list(dict.fromkeys(self.diagnostics))
        return payload


def main(
    argv: Sequence[str] | None = None,
    *,
    service: PerformanceStatus | None = None,
    stdout: Any = None,
) -> int:
    """CLI entry point. Supported interface: ``status --json``."""

    args = list(argv if argv is not None else sys.argv[1:])
    if args != ["status", "--json"]:
        print("Usage: ming-performance-status status --json", file=sys.stderr)
        return 2
    output = (service or PerformanceStatus()).status()
    print(json.dumps(output, ensure_ascii=False, sort_keys=True), file=stdout or sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
