#!/usr/bin/env python3
"""Read-only local disk and partition status for Ming Settings."""

import argparse
import json
import subprocess
import sys


LSBLK_FIELDS = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS"
EXCLUDED_TYPES = {"loop", "rom", "zram", "ram", "squashfs"}


def run_command(command, timeout=3):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
            shell=False)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _mountpoints(value):
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return [str(item) for item in values if isinstance(item, str) and item.strip()]


def parse_lsblk(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
        raise ValueError("lsblk JSON 缺少 blockdevices")
    result = []

    def visit(item):
        if not isinstance(item, dict):
            return
        device_type = str(item.get("type") or "").lower()
        path = item.get("path")
        name = str(item.get("name") or "")
        if (device_type in {"disk", "part"} and not name.startswith(("zram", "ram"))
                and isinstance(path, str) and path.startswith("/dev/")):
            mountpoints = _mountpoints(item.get("mountpoints"))
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            result.append({
                "name": name or path.rsplit("/", 1)[-1],
                "path": path,
                "type": device_type,
                "size": max(0, size),
                "fstype": item.get("fstype") if isinstance(item.get("fstype"), str) else "",
                "label": item.get("label") if isinstance(item.get("label"), str) else "",
                "uuid": item.get("uuid") if isinstance(item.get("uuid"), str) else "",
                "mountpoints": mountpoints,
                "state": "mounted" if mountpoints else "unmounted",
            })
        for child in item.get("children") or []:
            visit(child)

    for device in payload["blockdevices"]:
        if isinstance(device, dict) and str(device.get("type") or "").lower() not in EXCLUDED_TYPES:
            visit(device)
    return result


def partition_snapshot(runner=run_command):
    command = ["lsblk", "--json", "--bytes", "--output", LSBLK_FIELDS]
    rc, output, error = runner(command, 3)
    if rc not in (0, 2):
        return {"ok": False, "partitions": [], "error": error or "lsblk 读取失败"}
    try:
        payload = json.loads(output or "{}")
        partitions = parse_lsblk(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "partitions": [], "error": "lsblk JSON 无效：%s" % exc}
    return {
        "ok": True,
        "diagnostic": rc == 2,
        "partitions": partitions,
        "error": error if rc == 2 else "",
    }


def main(argv=None, stdout=None):
    parser = argparse.ArgumentParser(prog="ming-storage-status")
    sub = parser.add_subparsers(dest="action", required=True)
    partitions = sub.add_parser("partitions")
    partitions.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = partition_snapshot()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout or sys.stdout)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
