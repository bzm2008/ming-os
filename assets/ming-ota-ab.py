#!/usr/bin/env python3
"""Fail-closed state contract for Ming OS A/B system updates."""

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


LAYOUT_PATH = pathlib.Path("/etc/ming-update/slots.json")
TRANSACTION_PATH = pathlib.Path("/home/.ming-ota/ab-transaction.json")
UUID_RE = re.compile(r"^[A-Fa-f0-9][A-Fa-f0-9-]{3,127}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._-]*)?$")
BUILD_ID_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+){0,3}-rc[0-9]+-[A-Fa-f0-9]{7,40}-[0-9]{8}T[0-9]{6}Z$"
)
GRUB_SUBMENU = "Ming OS 高级启动"


class ContractError(ValueError):
    pass


def canonical_grub_entry(slot):
    """Return the only GRUB entry spelling written by current OTA state."""
    if slot not in ("A", "B"):
        raise ContractError(f"slot {slot} is invalid")
    return f"{GRUB_SUBMENU}>Ming OS slot {slot}"


def _slot(layout, name):
    value = layout.get("slots", {}).get(name)
    if not isinstance(value, dict):
        raise ContractError(f"slot {name} is missing")
    device = value.get("device")
    uuid = value.get("uuid")
    entry = value.get("grub_entry")
    if not isinstance(device, str) or not device.startswith("/dev/"):
        raise ContractError(f"slot {name} device is unsafe")
    if not isinstance(uuid, str) or not UUID_RE.fullmatch(uuid):
        raise ContractError(f"slot {name} UUID is invalid")
    legacy_entry = f"Ming OS slot {name}"
    canonical_entry = canonical_grub_entry(name)
    if entry == legacy_entry:
        # Read legacy receipts for compatibility, but never write them back.
        entry = canonical_entry
    if not isinstance(entry, str) or entry != canonical_entry or "\n" in entry:
        raise ContractError(f"slot {name} GRUB entry is invalid")
    return {"device": device, "uuid": uuid.lower(), "grub_entry": entry}


def _grub_entry_label(entry):
    return str(entry).rsplit(">", 1)[-1]


def validate_layout(layout):
    if not isinstance(layout, dict):
        raise ContractError("layout must be an object")
    if layout.get("schema") != 1 or layout.get("layout") != "ming-ab-v1":
        raise ContractError("unsupported A/B layout schema")
    if not isinstance(layout.get("slots"), dict) or set(layout["slots"]) != {"A", "B"}:
        raise ContractError("layout must contain exactly root slots A and B")
    slots = {name: _slot(layout, name) for name in ("A", "B")}
    if slots["A"]["uuid"] == slots["B"]["uuid"]:
        raise ContractError("root slots must use distinct filesystems")
    volumes = {}
    for name in ("boot", "home"):
        volume = layout.get(name)
        if not isinstance(volume, dict):
            raise ContractError(f"independent {name} filesystem is missing")
        device = volume.get("device")
        uuid = volume.get("uuid")
        if not isinstance(device, str) or not device.startswith("/dev/"):
            raise ContractError(f"{name} device is unsafe")
        if not isinstance(uuid, str) or not UUID_RE.fullmatch(uuid):
            raise ContractError(f"{name} UUID is invalid")
        volumes[name] = {"device": device, "uuid": uuid.lower()}
    all_uuids = {slots["A"]["uuid"], slots["B"]["uuid"],
                 volumes["boot"]["uuid"], volumes["home"]["uuid"]}
    if len(all_uuids) != 4:
        raise ContractError("root, boot and home filesystems must all be distinct")
    return {"schema": 1, "layout": "ming-ab-v1", "slots": slots,
            "boot": volumes["boot"], "home": volumes["home"]}


def layout_status(layout, root_uuid, home_uuid, boot_uuid):
    checked = validate_layout(layout)
    root_uuid = (root_uuid or "").lower()
    home_uuid = (home_uuid or "").lower()
    boot_uuid = (boot_uuid or "").lower()
    active = [name for name, slot in checked["slots"].items() if slot["uuid"] == root_uuid]
    if len(active) != 1:
        raise ContractError("mounted root does not identify exactly one managed slot")
    if home_uuid != checked["home"]["uuid"]:
        raise ContractError("mounted /home does not match the managed home filesystem")
    if boot_uuid != checked["boot"]["uuid"]:
        raise ContractError("mounted /boot does not match the shared boot filesystem")
    active_slot = active[0]
    inactive_slot = "B" if active_slot == "A" else "A"
    return {
        "ready": True,
        "strategy": "ab_slot",
        "active_slot": active_slot,
        "inactive_slot": inactive_slot,
        "active": checked["slots"][active_slot],
        "inactive": checked["slots"][inactive_slot],
        "boot": checked["boot"],
        "home": checked["home"],
    }


def begin_transaction(layout, active_slot, target_slot, version, checksum, build_id=""):
    checked = validate_layout(layout)
    if active_slot not in ("A", "B") or target_slot not in ("A", "B"):
        raise ContractError("transaction slot is invalid")
    if active_slot == target_slot:
        raise ContractError("target must be the inactive slot")
    if not VERSION_RE.fullmatch(version or ""):
        raise ContractError("target version is invalid")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", checksum or ""):
        raise ContractError("target checksum is invalid")
    if build_id and not BUILD_ID_RE.fullmatch(build_id):
        raise ContractError("target build id is invalid")
    return {
        "schema": 1,
        "status": "pending",
        "previous_slot": active_slot,
        "target_slot": target_slot,
        "previous_entry": checked["slots"][active_slot]["grub_entry"],
        "target_entry": checked["slots"][target_slot]["grub_entry"],
        "version": version,
        "build_id": build_id or "",
        "checksum": checksum.lower(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def observe_boot(transaction, current_slot, healthy, current_build_id=""):
    if not isinstance(transaction, dict) or transaction.get("status") not in ("pending", "rollback_required"):
        raise ContractError("no pending A/B transaction")
    previous = transaction.get("previous_slot")
    target = transaction.get("target_slot")
    if current_slot == previous:
        result = {**transaction, "status": "rolled_back", "boot_target": previous}
    elif current_slot != target:
        raise ContractError("current slot is outside the pending transaction")
    elif transaction.get("status") == "rollback_required":
        result = {**transaction, "status": "rollback_required", "boot_target": previous}
    else:
        if not transaction.get("build_id"):
            raise ContractError("target transaction build id is missing")
        if not current_build_id:
            raise ContractError("current build id is missing")
        if current_build_id != transaction["build_id"]:
            raise ContractError("current build id does not match target transaction")
        if healthy:
            result = {**transaction, "status": "confirmed", "boot_target": target}
        else:
            result = {**transaction, "status": "rollback_required", "boot_target": previous}
    result["observed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def force_rollback(transaction, reason):
    if not isinstance(transaction, dict) or transaction.get("status") not in (
            "pending", "rollback_required"):
        raise ContractError("no rollback-capable A/B transaction")
    previous = transaction.get("previous_slot")
    previous_entry = transaction.get("previous_entry")
    if previous not in ("A", "B"):
        raise ContractError("previous slot identity is invalid")
    canonical_entry = canonical_grub_entry(previous)
    if previous_entry not in (canonical_entry, f"Ming OS slot {previous}"):
        raise ContractError("previous slot identity is invalid")
    return {
        **transaction,
        # Legacy receipts are accepted for reading but never propagated.
        "previous_entry": canonical_entry,
        "status": "rollback_required",
        "boot_target": previous,
        "failure_reason": str(reason or "health check failed")[:512],
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _write_text_atomic(path, text, mode):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise ContractError(f"unsafe target path: {path}")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_slot_root(root, layout, target_slot):
    checked = validate_layout(layout)
    if target_slot not in ("A", "B"):
        raise ContractError("target slot is invalid")
    root = pathlib.Path(root)
    etc = root / "etc"
    fstab = etc / "fstab"
    if root.is_symlink() or etc.is_symlink() or fstab.is_symlink() or not etc.is_dir():
        raise ContractError("inactive root has an unsafe etc/fstab path")
    existing = fstab.read_text(encoding="utf-8") if fstab.exists() else ""
    kept = []
    for line in existing.splitlines():
        fields = line.split()
        if line.lstrip().startswith("#") or len(fields) < 2 or fields[1] not in ("/", "/boot", "/home"):
            kept.append(line)
    kept.extend([
        f'UUID={checked["slots"][target_slot]["uuid"]} / ext4 defaults 0 1',
        f'UUID={checked["boot"]["uuid"]} /boot ext4 defaults 0 2',
        f'UUID={checked["home"]["uuid"]} /home ext4 defaults 0 2',
    ])
    temporary = etc / f".fstab.ming-ab.{os.getpid()}"
    temporary.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, fstab)
    marker = etc / "ming-ota-slot"
    if marker.is_symlink():
        raise ContractError("inactive root slot marker is unsafe")
    marker.write_text(target_slot + "\n", encoding="ascii")
    os.chmod(marker, 0o644)
    update_config = etc / "ming-update"
    if update_config.is_symlink():
        raise ContractError("inactive root OTA config directory is unsafe")
    update_config.mkdir(mode=0o755, exist_ok=True)
    _write_atomic(update_config / "slots.json", checked)
    os.chmod(update_config / "slots.json", 0o600)
    ready = update_config / "ota-ready"
    if ready.is_symlink():
        raise ContractError("inactive root OTA-ready marker is unsafe")
    descriptor, temporary = tempfile.mkstemp(prefix=".ota-ready.", dir=update_config)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write("ming-ab-v1\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, ready)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    grub_dir = etc / "grub.d"
    defaults_dir = etc / "default" / "grub.d"
    for directory in (grub_dir, defaults_dir):
        if directory.is_symlink():
            raise ContractError("inactive root GRUB directory is unsafe")
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)

    slot_lines = []
    for name in ("A", "B"):
        slot = checked["slots"][name]
        slot_lines.extend([
            f"menuentry '{_grub_entry_label(slot['grub_entry'])}' --class ming --class gnu-linux --class os {{",
            f"    search --no-floppy --fs-uuid --set=root {checked['boot']['uuid']}",
            f"    linux /ming-slots/{name}/vmlinuz root=UUID={slot['uuid']} ro quiet loglevel=3 systemd.show_status=false",
            f"    initrd /ming-slots/{name}/initrd.img",
            "}",
        ])
    normal_name = target_slot
    normal = checked["slots"][normal_name]
    grub_lines = [
        "menuentry 'Ming OS' --class ming --class gnu-linux --class os {",
        f"    search --no-floppy --fs-uuid --set=root {checked['boot']['uuid']}",
        f"    linux /ming-slots/{normal_name}/vmlinuz root=UUID={normal['uuid']} ro quiet loglevel=3 systemd.show_status=false",
        f"    initrd /ming-slots/{normal_name}/initrd.img",
        "}",
        "submenu 'Ming OS 高级启动' --class ming --class gnu-linux --class os {",
    ]
    grub_lines.extend("    " + line if line else line for line in slot_lines)
    grub_lines.append("}")
    grub_script = "#!/bin/sh\nset -e\ncat <<'EOF'\n" + "\n".join(grub_lines) + "\nEOF\n"
    _write_text_atomic(grub_dir / "09_ming_os", grub_script, 0o755)

    defaults_path = defaults_dir / "10-ming-os.cfg"
    existing_defaults = defaults_path.read_text(encoding="utf-8") if defaults_path.exists() else ""
    kept_defaults = [
        line for line in existing_defaults.splitlines()
        if not line.startswith("GRUB_DEFAULT=") and not line.startswith("GRUB_SAVEDEFAULT=")
    ]
    kept_defaults = [line for line in kept_defaults if not line.startswith("GRUB_DISABLE_SUBMENU=")]
    kept_defaults.extend(["GRUB_DEFAULT=saved", "GRUB_SAVEDEFAULT=false", "GRUB_DISABLE_SUBMENU=false"])
    _write_text_atomic(defaults_path, "\n".join(kept_defaults).rstrip() + "\n", 0o644)


def _load_regular(path):
    path = pathlib.Path(path)
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"contract file is missing or unsafe: {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_atomic(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _mount_uuid(path):
    result = subprocess.run(
        ["findmnt", "-nro", "UUID", "-T", path], capture_output=True,
        text=True, timeout=5, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def current_status(layout_path=LAYOUT_PATH):
    return layout_status(
        _load_regular(layout_path), _mount_uuid("/"), _mount_uuid("/home"),
        _mount_uuid("/boot"),
    )


def command_status(args):
    print(json.dumps(current_status(args.layout), ensure_ascii=True))


def command_begin(args):
    status = current_status(args.layout)
    if args.target != status["inactive_slot"]:
        raise ContractError("requested target is not the detected inactive slot")
    transaction = begin_transaction(
        _load_regular(args.layout), status["active_slot"], args.target,
        args.version, args.checksum, args.build_id,
    )
    _write_atomic(args.transaction, transaction)
    print(json.dumps(transaction, ensure_ascii=True))


def command_observe(args):
    status = current_status(args.layout)
    transaction = _load_regular(args.transaction)
    current_build_id = args.build_id
    if not current_build_id:
        try:
            payload = _load_regular("/etc/ming-os-build.json")
            current_build_id = str(payload.get("build_id") or "")
        except (ContractError, OSError, json.JSONDecodeError):
            current_build_id = ""
    result = observe_boot(
        transaction, status["active_slot"], args.health == "healthy", current_build_id
    )
    _write_atomic(args.transaction, result)
    print(json.dumps(result, ensure_ascii=True))


def command_prepare_root(args):
    prepare_slot_root(args.root, _load_regular(args.layout), args.target)


def command_force_rollback(args):
    transaction = _load_regular(args.transaction)
    result = force_rollback(transaction, args.reason)
    _write_atomic(args.transaction, result)
    print(json.dumps(result, ensure_ascii=True))


def build_parser():
    parser = argparse.ArgumentParser(description="Ming OS A/B OTA contract")
    parser.add_argument("--layout", default=str(LAYOUT_PATH))
    parser.add_argument("--transaction", default=str(TRANSACTION_PATH))
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.set_defaults(handler=command_status)
    begin = commands.add_parser("begin")
    begin.add_argument("--target", required=True, choices=("A", "B"))
    begin.add_argument("--version", required=True)
    begin.add_argument("--checksum", required=True)
    begin.add_argument("--build-id", default="")
    begin.set_defaults(handler=command_begin)
    observe = commands.add_parser("observe-boot")
    observe.add_argument("--health", required=True, choices=("healthy", "failed"))
    observe.add_argument("--build-id", default="")
    observe.set_defaults(handler=command_observe)
    prepare = commands.add_parser("prepare-root")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--target", required=True, choices=("A", "B"))
    prepare.set_defaults(handler=command_prepare_root)
    rollback = commands.add_parser("force-rollback")
    rollback.add_argument("--reason", default="health check failed")
    rollback.set_defaults(handler=command_force_rollback)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (ContractError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"ming-ota-ab: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
