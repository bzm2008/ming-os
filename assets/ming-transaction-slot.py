#!/usr/bin/env python3
"""Inactive directory-slot planning and cloning for Ming OS transactions."""

import hashlib
import json
import os
import pathlib
import shutil
import stat


EXCLUDED = (
    "boot",
    "dev",
    "home",
    "lost+found",
    "media",
    "mnt",
    "proc",
    "run",
    "sys",
    "tmp",
    "var/lib/ming-update",
    "var/tmp",
)


class SlotError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _is_excluded(relative):
    value = relative.as_posix()
    return any(value == prefix or value.startswith(prefix + "/") for prefix in EXCLUDED)


def tree_allocated_bytes(root):
    total = 0
    root = pathlib.Path(root)
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _is_excluded(relative):
            continue
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SlotError("E_CLONE", f"cannot stat active root entry: {relative}") from exc
        if stat.S_ISREG(metadata.st_mode):
            total += getattr(metadata, "st_blocks", 0) * 512 or metadata.st_size
        else:
            total += 4096
    return total


def tree_digest(root):
    digest = hashlib.sha256()
    root = pathlib.Path(root)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        metadata = path.lstat()
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


def select_slots(state_root):
    current_path = pathlib.Path(state_root) / "current.json"
    previous = "legacy"
    if current_path.exists():
        if current_path.is_symlink():
            raise SlotError("E_STATE_SCHEMA", "current slot pointer is unsafe")
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SlotError("E_STATE_SCHEMA", f"current slot pointer is invalid: {exc}") from exc
        previous = current.get("slot")
    if previous not in {"legacy", "A", "B"}:
        raise SlotError("E_STATE_SCHEMA", "current slot is invalid")
    candidate = "A" if previous == "B" else "B"
    return previous, candidate


def validate_space(*, active_root, state_root, payload_size, reserve_bytes, minimum_free_bytes, available_bytes=None):
    active_bytes = tree_allocated_bytes(active_root)
    required = active_bytes + int(payload_size) + int(reserve_bytes)
    if available_bytes is None:
        probe = pathlib.Path(state_root)
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        available_bytes = shutil.disk_usage(probe).free
    threshold = max(required, int(minimum_free_bytes))
    if int(available_bytes) < threshold:
        raise SlotError(
            "E_SPACE",
            "insufficient free space for inactive root",
            {"required_bytes": threshold, "available_bytes": int(available_bytes)},
        )
    return {"active_bytes": active_bytes, "required_bytes": threshold, "available_bytes": int(available_bytes)}


def _copy_entry(source, target, active_root):
    relative = source.relative_to(active_root)
    if _is_excluded(relative):
        return
    metadata = source.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        target.mkdir(exist_ok=True)
        shutil.copystat(source, target, follow_symlinks=False)
        for child in source.iterdir():
            _copy_entry(child, target / child.name, active_root)
    elif stat.S_ISLNK(metadata.st_mode):
        target.symlink_to(os.readlink(source), target_is_directory=False)
    elif stat.S_ISREG(metadata.st_mode):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    else:
        raise SlotError("E_CLONE", f"unsupported special file in active root: {relative}")


def clone_active_root(*, active_root, state_root, candidate_slot, transaction_id):
    active_root = pathlib.Path(active_root).resolve()
    state_root = pathlib.Path(state_root).resolve()
    slot_dir = state_root / "slots" / candidate_slot
    candidate = slot_dir / "root"
    staging = slot_dir / f".root-staging-{transaction_id}"
    if candidate.exists() or candidate.is_symlink() or staging.exists() or staging.is_symlink():
        raise SlotError("E_BUSY", "inactive slot is not empty")
    staging.mkdir(parents=True, mode=0o700)
    try:
        for child in active_root.iterdir():
            _copy_entry(child, staging / child.name, active_root)
        os.replace(staging, candidate)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    slot_record = {
        "schema": "ming.slot.v1",
        "slot": candidate_slot,
        "transaction_id": transaction_id,
        "base_digest": tree_digest(candidate),
    }
    temporary = slot_dir / ".slot.json.tmp"
    temporary.write_text(json.dumps(slot_record, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, slot_dir / "slot.json")
    return candidate


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
