#!/usr/bin/env python3
import pathlib
import subprocess


def physical_disks_for_device(device, runner=subprocess.run):
    if not isinstance(device, str) or not device.startswith("/dev/"):
        return set()
    result = runner(
        ["lsblk", "-s", "-nrpo", "NAME,TYPE", device],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return set()
    disks = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "disk" and fields[0].startswith("/dev/"):
            disks.add(fields[0])
    return disks


def validate_target(partitions, preservation_device, disk_resolver=physical_disks_for_device):
    if not isinstance(partitions, list):
        return False, "Calamares did not provide a partition plan"
    root_devices = {
        item.get("device") or item.get("partitionPath")
        for item in partitions
        if isinstance(item, dict) and item.get("mountPoint") == "/"
    }
    root_devices.discard(None)
    if not root_devices:
        return False, "Calamares partition plan has no root target"

    preservation_disks = set(disk_resolver(preservation_device))
    if not preservation_disks:
        return False, "cannot determine preservation media physical disk"

    target_disks = set()
    for device in root_devices:
        resolved = set(disk_resolver(device))
        if not resolved:
            return False, f"cannot determine target physical disk for {device}"
        target_disks.update(resolved)

    overlap = target_disks & preservation_disks
    if overlap:
        return False, "root target is on the same physical disk as OTA preservation media"
    return True, "OTA target disk is separate from preservation media"


def read_marker(path="/run/ming-ota-preflight.ok"):
    values = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"uuid", "manifest", "strategy"}:
            values[key] = value
    return values


def device_for_uuid(uuid, runner=subprocess.run):
    if not uuid or any(character not in "0123456789abcdefABCDEF-" for character in uuid):
        return ""
    result = runner(
        ["blkid", "-U", uuid],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    device = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    return device if device.startswith("/dev/") else ""


def validate_from_marker(partitions, marker="/run/ming-ota-preflight.ok"):
    try:
        values = read_marker(marker)
    except OSError as exc:
        return False, f"OTA preservation marker is unavailable: {exc}"
    device = device_for_uuid(values.get("uuid", ""))
    if not device:
        return False, "OTA preservation device is unavailable"
    return validate_target(partitions, device)
