#!/usr/bin/env python3
"""Safely auto-mount Ming OS user data partitions."""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys


SUPPORTED_FILESYSTEMS = {"ntfs", "ntfs3", "exfat", "vfat", "fat32", "ext2", "ext3", "ext4"}
LOCKED_FILESYSTEMS = {"crypto_luks", "luks", "bitlocker"}
SYSTEM_PARTTYPE_PREFIXES = (
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",  # EFI system partition
    "e3c9e316-0b5c-4db8-817d-f92df00215ae",  # Microsoft reserved
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac",  # Windows recovery
)
SYSTEM_LABEL_MARKERS = ("efi", "esp", "msr", "system reserved", "recovery")
INSTALLER_LABEL_MARKERS = ("ming os", "ming_os", "onion os", "live", "installer", "iso")


def _text(row, key):
    value = row.get(key, "")
    if value is None:
        return ""
    if isinstance(value, list):
        return next((str(item) for item in value if item), "")
    return str(value).strip()


def device_path(row):
    name = _text(row, "name")
    if not name:
        return ""
    return name if name.startswith("/dev/") else "/dev/" + name


def _normal_fstype(row):
    fstype = _text(row, "fstype").casefold()
    return "vfat" if fstype == "fat32" else fstype


def _is_mounted(row):
    if _text(row, "mountpoint"):
        return True
    mountpoints = row.get("mountpoints")
    return isinstance(mountpoints, list) and any(bool(item) for item in mountpoints)


def _label(row):
    return (_text(row, "label") or _text(row, "partlabel")).strip()


def _is_system_partition(row):
    partlabel = _text(row, "partlabel").casefold()
    parttype = _text(row, "parttype").casefold()
    label = _text(row, "label").casefold()
    if any(parttype.startswith(prefix) for prefix in SYSTEM_PARTTYPE_PREFIXES):
        return True
    combined = " ".join(value for value in (partlabel, label) if value)
    return any(marker in combined for marker in SYSTEM_LABEL_MARKERS)


def _is_installer_or_recovery(row):
    label = " ".join(value for value in (_text(row, "label"), _text(row, "partlabel")) if value).casefold()
    fstype = _normal_fstype(row)
    return fstype == "iso9660" or any(marker in label for marker in INSTALLER_LABEL_MARKERS)


def classify_partition(row):
    if _text(row, "type") and _text(row, "type") != "part":
        return False, "not-partition"
    fstype = _normal_fstype(row)
    if _is_mounted(row):
        return False, "already-mounted"
    if not fstype:
        return False, "missing-filesystem"
    if fstype in LOCKED_FILESYSTEMS:
        return False, "locked-or-encrypted"
    if _is_system_partition(row):
        return False, "system-partition"
    if _is_installer_or_recovery(row):
        return False, "installer-or-recovery-media"
    if fstype not in SUPPORTED_FILESYSTEMS:
        return False, "unsupported-filesystem"
    if not _text(row, "uuid") and not _label(row):
        return False, "missing-stable-identity"
    return True, "eligible"


def safe_mount_name(row):
    raw = _label(row) or _text(row, "uuid") or pathlib.PurePath(device_path(row)).name
    value = re.sub(r"[\\/\0\r\n\t]+", "_", raw).strip(" .")
    return value[:80] or pathlib.PurePath(device_path(row)).name


def target_for(row, user):
    user = re.sub(r"[^A-Za-z0-9._-]+", "_", str(user or "user")).strip("._-") or "user"
    return "/" + "/".join(("media", user, safe_mount_name(row)))


def build_mount_plan(rows, user):
    plan = {"ok": True, "mount": [], "skip": []}
    for row in rows:
        device = device_path(row)
        if not device:
            continue
        eligible, reason = classify_partition(row)
        record = {
            "device": device,
            "fstype": _normal_fstype(row),
            "uuid": _text(row, "uuid"),
            "label": _label(row),
        }
        if eligible:
            record["target"] = target_for(row, user)
            plan["mount"].append(record)
        else:
            record["reason"] = reason
            plan["skip"].append(record)
    return plan


def _flatten_lsblk(nodes):
    rows = []
    for node in nodes or []:
        row = dict(node)
        children = row.pop("children", None)
        rows.append(row)
        rows.extend(_flatten_lsblk(children))
    return rows


def read_partitions():
    command = [
        "lsblk", "-J", "-o",
        "NAME,TYPE,FSTYPE,UUID,LABEL,MOUNTPOINT,PARTLABEL,PARTTYPE,RM",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "lsblk failed")
    return _flatten_lsblk(json.loads(completed.stdout).get("blockdevices", []))


def _mount_options(fstype):
    if fstype in {"ntfs", "ntfs3", "exfat", "vfat"}:
        return "rw,nosuid,nodev,nofail,uid={uid},gid={gid},umask=022".format(
            uid=os.getuid() if hasattr(os, "getuid") else 0,
            gid=os.getgid() if hasattr(os, "getgid") else 0,
        )
    return "rw,nosuid,nodev,nofail"


def mount_record(record, runner=None):
    runner = runner or subprocess.run
    target = pathlib.Path(record["target"])
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    if subprocess.run(["findmnt", "-rn", str(target)], check=False, capture_output=True).returncode == 0:
        record["mounted"] = True
        record["changed"] = False
        return record
    command = [
        "mount",
        "-t", record["fstype"],
        "-o", _mount_options(record["fstype"]),
        record["device"],
        str(target),
    ]
    completed = runner(command, check=False, capture_output=True, text=True, timeout=15)
    record["mounted"] = completed.returncode == 0
    record["changed"] = completed.returncode == 0
    if completed.returncode != 0:
        record["error"] = (completed.stderr or completed.stdout or "mount failed").strip()[:500]
    return record


def default_user():
    for key in ("SUDO_USER", "LOGNAME", "USER"):
        value = os.environ.get(key)
        if value and value != "root":
            return value
    return "user"


def run(user=None, dry_run=False, rows=None):
    user = user or default_user()
    plan = build_mount_plan(rows if rows is not None else read_partitions(), user=user)
    if not dry_run:
        plan["mount"] = [mount_record(dict(record)) for record in plan["mount"]]
        plan["ok"] = all(record.get("mounted") for record in plan["mount"])
    return plan


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-volume-automount")
    parser.add_argument("--user", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None, stdout=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    stdout = stdout or sys.stdout
    try:
        result = run(user=args.user, dry_run=args.dry_run)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        result = {"ok": False, "mount": [], "skip": [], "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
