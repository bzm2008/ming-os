#!/usr/bin/env python3
"""Small, read-only desktop resource policy probe for Ming OS."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


class CommandResult:
    def __init__(self, returncode, stdout="", stderr="", timed_out=False, missing=False):
        self.returncode = int(returncode)
        self.stdout = str(stdout or "")
        self.stderr = str(stderr or "")
        self.timed_out = bool(timed_out)
        self.missing = bool(missing)


def run_command(argv, timeout=2):
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            check=False, shell=False,
        )
    except FileNotFoundError:
        return CommandResult(127, missing=True)
    except subprocess.TimeoutExpired:
        return CommandResult(124, timed_out=True)
    except OSError as error:
        return CommandResult(126, stderr=str(error))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class SessionProfile:
    def __init__(self, read_text=None, path_exists=None, runner=run_command):
        self.read_text = read_text or self._read_text
        self.path_exists = path_exists or (lambda path: Path(path).exists())
        self.runner = runner

    @staticmethod
    def _read_text(path):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _memory_mb(self):
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", self.read_text("/proc/meminfo") or "", re.M)
        return int(match.group(1)) // 1024 if match else 0

    def _cpu_flags(self):
        text = self.read_text("/proc/cpuinfo") or ""
        match = re.search(r"^(?:flags|Features)\s*:\s*(.+)$", text, re.M | re.I)
        return set((match.group(1) if match else "").split())

    def _renderer(self):
        result = self.runner(("glxinfo", "-B"), timeout=2)
        match = re.search(r"OpenGL renderer string:\s*(.+)", result.stdout, re.I)
        return (match.group(1).strip() if match else "")

    def collect(self):
        memory_mb = self._memory_mb()
        flags = self._cpu_flags()
        renderer = self._renderer()
        renderer_lower = renderer.casefold()
        dri = bool(self.path_exists("/dev/dri"))
        low_memory = 0 < memory_mb <= 4096
        software_renderer = any(name in renderer_lower for name in ("llvmpipe", "softpipe", "software"))
        compositor_profile = "off" if low_memory or software_renderer else "auto" if dri else "xrender"
        animations = compositor_profile != "off"
        return {
            "schema_version": 1,
            "memory_mb": memory_mb,
            "low_memory": low_memory,
            "avx2": "avx2" in {flag.casefold() for flag in flags},
            "renderer": renderer or "unknown",
            "dri_available": dri,
            "software_renderer": software_renderer,
            "compositor_profile": compositor_profile,
            "animations": animations,
            "dock_zoom": animations,
        }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["status", "--json"]:
        print("Usage: ming-session-profile status --json", file=sys.stderr)
        return 2
    print(json.dumps(SessionProfile().collect(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
