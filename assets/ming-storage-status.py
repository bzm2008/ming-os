#!/usr/bin/env python3
"""Read-only local block-device inventory for Ming Settings.

This helper intentionally reports partitions without mounting or otherwise
changing them.  Gio/GVfs only exposes mounted volumes on some systems, while
lsblk is the authoritative kernel view needed by the storage settings page.
"""

import argparse
import json
import subprocess


LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS"
_VISIBLE_TYPES = {"part", "crypt", "lvm", "raid", "md"}
_HIDDEN_TYPES = {"loop", "rom", "zram"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _size(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _mountpoints(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in (_text(item) for item in value) if item]


def _is_visible(node):
    node_type = _text(node.get("type")).lower()
    path = _text(node.get("path"))
    if not path.startswith("/dev/") or node_type in _HIDDEN_TYPES:
        return False
    if node_type in _VISIBLE_TYPES or node_type.startswith("raid"):
        return True
    # Whole-disk filesystems are valid on removable media and older installs
    # that deliberately do not use a partition table.
    return node_type == "disk" and bool(
        _text(node.get("fstype")) or _mountpoints(node.get("mountpoints"))
    )


def _partition_state(fstype, mountpoints):
    if fstype.lower() == "swap" or "[SWAP]" in mountpoints:
        return "swap"
    return "mounted" if mountpoints else "unmounted"


def parse_partitions(document):
    """Flatten an lsblk JSON document into display-safe local partitions."""
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, dict):
        raise ValueError("lsblk JSON root must be an object")
    devices = document.get("blockdevices")
    if not isinstance(devices, list):
        raise ValueError("lsblk JSON does not contain blockdevices")

    partitions = []

    def visit(node):
        if not isinstance(node, dict):
            return
        if _is_visible(node):
            fstype = _text(node.get("fstype"))
            mountpoints = _mountpoints(node.get("mountpoints"))
            partitions.append({
                "name": _text(node.get("name")),
                "path": _text(node.get("path")),
                "type": _text(node.get("type")).lower(),
                "size": _size(node.get("size")),
                "fstype": fstype,
                "label": _text(node.get("label")),
                "uuid": _text(node.get("uuid")),
                "mountpoints": mountpoints,
                "state": _partition_state(fstype, mountpoints),
            })
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child)

    for device in devices:
        visit(device)
    return partitions


def _run(command, timeout):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "lsblk timed out"
    except OSError as error:
        return 1, "", str(error)


def partition_snapshot(runner=_run):
    """Return a bounded, structured read-only partition snapshot."""
    command = ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS]
    rc, output, error = runner(command, timeout=3)
    if rc != 0:
        return {
            "ok": False,
            "partitions": [],
            "error": error or output or "lsblk could not read local storage",
        }
    try:
        partitions = parse_partitions(output)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            "ok": False,
            "partitions": [],
            "error": "invalid lsblk data: %s" % error,
        }
    return {"ok": True, "partitions": partitions, "error": ""}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ming OS storage inventory")
    parser.add_argument("command", choices=("partitions", "status"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = partition_snapshot()
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for item in result["partitions"]:
            print("{path}\t{fstype}\t{state}".format(**item))
        if not result["ok"]:
            print(result["error"])
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
