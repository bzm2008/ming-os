#!/usr/bin/env python3
"""HDD-only, bounded application warm-cache helper."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from typing import Callable, Iterable


ALLOWED_PREFIXES = ("/usr/bin/", "/usr/sbin/", "/usr/lib/", "/lib/", "/opt/")
DEFAULT_MAX_FILES = 128
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_BUDGET_SECONDS = 0.4
INDEX_VERSION = 1
MAX_INDEX_APPS = 64


def _index_path(home: str | os.PathLike[str] | None = None) -> pathlib.Path:
    base = pathlib.Path(home).expanduser() if home is not None else pathlib.Path.home()
    return base / ".cache" / "ming-os" / "prefetch" / "index.json"


def _safe_app_id(app_id: str) -> str | None:
    value = pathlib.Path(str(app_id or "")).name
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        return None
    return value[:128]


def _read_index(path: pathlib.Path) -> dict[str, object]:
    try:
        if path.stat().st_size > 1024 * 1024:
            return {"version": INDEX_VERSION, "applications": {}}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != INDEX_VERSION:
            return {"version": INDEX_VERSION, "applications": {}}
        applications = value.get("applications")
        return {
            "version": INDEX_VERSION,
            "applications": applications if isinstance(applications, dict) else {},
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": INDEX_VERSION, "applications": {}}


def _write_index(path: pathlib.Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def load_application_index(app_id: str, home: str | os.PathLike[str] | None = None) -> list[str]:
    key = _safe_app_id(app_id)
    if key is None:
        return []
    path = _index_path(home)
    value = _read_index(path)
    applications = value["applications"]
    record = applications.get(key) if isinstance(applications, dict) else None
    raw_paths = record.get("files", []) if isinstance(record, dict) else []
    if not isinstance(raw_paths, list):
        raw_paths = []
    selected = filter_prefetch_paths(raw_paths)
    if isinstance(applications, dict) and isinstance(record, dict) and selected != raw_paths:
        record["files"] = selected
        try:
            _write_index(path, value)
        except OSError:
            pass
    return selected


def record_application_index(app_id: str, paths: Iterable[str],
                             home: str | os.PathLike[str] | None = None) -> list[str]:
    key = _safe_app_id(app_id)
    if key is None:
        return []
    selected = filter_prefetch_paths(paths)
    path = _index_path(home)
    value = _read_index(path)
    applications = value.setdefault("applications", {})
    if not isinstance(applications, dict):
        applications = {}
        value["applications"] = applications
    applications[key] = {"files": selected, "updated": int(time.time())}
    if len(applications) > MAX_INDEX_APPS:
        ordered = sorted(
            applications.items(),
            key=lambda item: int(item[1].get("updated", 0)) if isinstance(item[1], dict) else 0,
            reverse=True,
        )[:MAX_INDEX_APPS]
        value["applications"] = dict(ordered)
    _write_index(path, value)
    return selected


def should_prefetch(rotational: bool, low_memory: bool, read_only: bool) -> bool:
    return bool(rotational and not low_memory and not read_only)


def filter_prefetch_paths(paths: Iterable[str], sizes: dict[str, int] | None = None,
                          max_files: int = DEFAULT_MAX_FILES,
                          max_bytes: int = DEFAULT_MAX_BYTES,
                          prefixes: tuple[str, ...] | None = None) -> list[str]:
    sizes = sizes or {}
    prefixes = ALLOWED_PREFIXES if prefixes is None else prefixes
    result: list[str] = []
    total = 0
    for raw in paths:
        path = os.path.realpath(str(raw))
        if not path.startswith(prefixes) or not os.path.isfile(path):
            continue
        try:
            size = int(sizes.get(str(raw), os.path.getsize(path)))
        except OSError:
            continue
        if size < 0 or total + size > max_bytes:
            continue
        result.append(path)
        total += size
        if len(result) >= max(0, int(max_files)):
            break
    return result


def warm_files(paths: Iterable[str], fadvise: Callable[[int], None] | None = None,
               max_files: int = DEFAULT_MAX_FILES, max_bytes: int = DEFAULT_MAX_BYTES,
               budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict[str, object]:
    selected = filter_prefetch_paths(paths, max_files=max_files, max_bytes=max_bytes)
    started = time.monotonic()
    warmed = 0
    bytes_read = 0
    errors: list[str] = []
    for path in selected:
        if time.monotonic() - started >= max(0.05, float(budget_seconds)):
            break
        try:
            with open(path, "rb") as handle:
                if fadvise:
                    fadvise(handle.fileno())
                else:
                    os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_WILLNEED)
            bytes_read += os.path.getsize(path)
            warmed += 1
        except (AttributeError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    return {"ok": True, "files": warmed, "bytes": bytes_read, "errors": errors[:8]}


def runtime_should_prefetch() -> tuple[bool, str]:
    rotational = False
    for path in pathlib.Path("/sys/block").glob("*/queue/rotational"):
        try:
            if path.read_text(encoding="ascii").strip() == "1":
                rotational = True
                break
        except OSError:
            continue
    if not rotational:
        return False, "未检测到机械硬盘"
    try:
        meminfo = pathlib.Path("/proc/meminfo").read_text(encoding="ascii")
        total_kb = next(int(line.split()[1]) for line in meminfo.splitlines()
                        if line.startswith("MemTotal:"))
        if total_kb <= 2_600_000:
            return False, "低内存设备跳过预读"
    except (OSError, StopIteration, ValueError):
        return False, "无法确认内存容量"
    return True, "HDD 按需预读"


def process_paths(pid: int) -> list[str]:
    try:
        text = pathlib.Path(f"/proc/{int(pid)}/maps").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    paths = []
    seen = set()
    for line in text.splitlines():
        candidate = line.rsplit(None, 1)[-1] if line.split() else ""
        if not candidate.startswith(ALLOWED_PREFIXES) or candidate in seen:
            continue
        seen.add(candidate)
        paths.append(candidate)
        if len(paths) >= DEFAULT_MAX_FILES:
            break
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "warm"))
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--app-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "status":
        result = {
            "ok": True,
            "enabled": False,
            "reason": "HDD 启动时按需调用",
            "index": str(_index_path()),
        }
    else:
        enabled, reason = runtime_should_prefetch()
        if not enabled:
            result = {"ok": True, "enabled": False, "reason": reason, "files": 0, "bytes": 0}
        else:
            paths = list(args.paths)
            if args.pid:
                paths.extend(process_paths(args.pid))
            if args.app_id and not paths:
                paths = load_application_index(args.app_id)
            result = warm_files(paths)
            if args.app_id:
                try:
                    result["indexed"] = record_application_index(args.app_id, paths)
                except OSError as exc:
                    result["index_error"] = str(exc)
            result["enabled"] = True
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
