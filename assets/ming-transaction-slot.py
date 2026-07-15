#!/usr/bin/env python3
"""Inactive directory-slot planning and cloning for Ming OS transactions."""

import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import time
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None


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
    "var/cache/ming-update",
    "var/tmp",
)

DPKG_LOCK_RELATIVE_PATHS = (
    "var/lib/dpkg/lock-frontend",
    "var/lib/dpkg/lock",
)

PROTECTED_RELATIVE_PATHS = (
    "etc/machine-id",
    "etc/passwd",
    "etc/group",
    "etc/shadow",
    "etc/gshadow",
    "etc/NetworkManager/system-connections",
    "etc/ssh",
    "var/lib/NetworkManager",
    "var/lib/bluetooth",
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
    hardlink_ids = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if _is_excluded(pathlib.Path(relative)):
            continue
        digest.update(relative.encode("utf-8"))
        metadata = path.lstat()
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        digest.update(str(getattr(metadata, "st_uid", 0)).encode("ascii"))
        digest.update(str(getattr(metadata, "st_gid", 0)).encode("ascii"))
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            link_key = (getattr(metadata, "st_dev", 0), getattr(metadata, "st_ino", 0))
            link_id = hardlink_ids.setdefault(link_key, len(hardlink_ids) + 1)
            digest.update(b"F" + str(link_id).encode("ascii"))
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        elif path.is_dir():
            digest.update(b"D")
        listxattr = getattr(os, "listxattr", None)
        getxattr = getattr(os, "getxattr", None)
        if listxattr is not None and getxattr is not None:
            try:
                names = sorted(listxattr(path, follow_symlinks=False))
            except OSError:
                names = ()
            for name in names:
                try:
                    value = getxattr(path, name, follow_symlinks=False)
                except OSError as exc:
                    raise SlotError("E_CLONE", f"cannot seal extended attribute: {relative}") from exc
                digest.update(b"X" + name.encode("utf-8") + b"\0" + value)
    return digest.hexdigest()


def protected_state_digest(root):
    """Hash machine state that a transaction must clone but never modify."""
    root = pathlib.Path(root)
    digest = hashlib.sha256()
    hardlink_ids = {}

    def add_path(path, relative):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SlotError("E_PROTECTED_PATH_CHANGED", f"cannot seal protected path: {relative}") from exc
        digest.update(b"P\0" + relative.encode("utf-8") + b"\0")
        digest.update(str(stat.S_IFMT(metadata.st_mode)).encode("ascii"))
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        digest.update(str(getattr(metadata, "st_uid", 0)).encode("ascii"))
        digest.update(str(getattr(metadata, "st_gid", 0)).encode("ascii"))
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"L" + os.readlink(path).encode("utf-8"))
        elif stat.S_ISREG(metadata.st_mode):
            link_key = (getattr(metadata, "st_dev", 0), getattr(metadata, "st_ino", 0))
            link_id = hardlink_ids.setdefault(link_key, len(hardlink_ids) + 1)
            digest.update(b"F" + str(link_id).encode("ascii"))
            try:
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
            except OSError as exc:
                raise SlotError("E_PROTECTED_PATH_CHANGED", f"cannot read protected path: {relative}") from exc
        elif stat.S_ISDIR(metadata.st_mode):
            digest.update(b"D")
        else:
            raise SlotError("E_PROTECTED_PATH_CHANGED", f"protected path has an unsafe type: {relative}")
        listxattr = getattr(os, "listxattr", None)
        getxattr = getattr(os, "getxattr", None)
        if listxattr is not None and getxattr is not None:
            try:
                names = sorted(listxattr(path, follow_symlinks=False))
            except OSError:
                names = ()
            for name in names:
                try:
                    value = getxattr(path, name, follow_symlinks=False)
                except OSError as exc:
                    raise SlotError("E_PROTECTED_PATH_CHANGED", f"cannot seal protected xattr: {relative}") from exc
                digest.update(b"X" + name.encode("utf-8") + b"\0" + value)

    for relative in PROTECTED_RELATIVE_PATHS:
        target = root.joinpath(*relative.split("/"))
        if not target.exists() and not target.is_symlink():
            digest.update(b"M\0" + relative.encode("utf-8") + b"\0")
            continue
        paths = [target]
        if target.is_dir() and not target.is_symlink():
            paths.extend(sorted(target.rglob("*"), key=lambda item: item.relative_to(root).as_posix()))
        for path in paths:
            add_path(path, path.relative_to(root).as_posix())
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


@contextlib.contextmanager
def dpkg_transaction_lock(active_root, *, timeout_seconds=30, clock=time.monotonic, sleeper=time.sleep):
    """Hold Debian's package-manager locks while cloning and final-syncing."""
    active_root = pathlib.Path(active_root)
    if active_root.is_symlink() or not active_root.is_dir():
        raise SlotError("E_PACKAGE_STATE", "active root is unsafe for package locking")
    descriptors = []
    try:
        for relative in DPKG_LOCK_RELATIVE_PATHS:
            path = active_root.joinpath(*relative.split("/"))
            if not path.is_file() or path.is_symlink():
                raise SlotError("E_PACKAGE_STATE", "Debian package lock path is unavailable or unsafe")
            flags = os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(path, flags, 0o640)
            except OSError as exc:
                raise SlotError("E_PACKAGE_STATE", "cannot open Debian package lock") from exc
            descriptors.append(descriptor)
            if fcntl is None:
                continue
            deadline = clock() + timeout_seconds
            while True:
                try:
                    fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        fcntl.lockf(descriptor, fcntl.LOCK_UN)
                        raise
                    break
                except BlockingIOError as exc:
                    if clock() >= deadline:
                        raise SlotError("E_PACKAGE_STATE", "Debian package manager is busy") from exc
                    sleeper(0.05)
        yield
    finally:
        for descriptor in reversed(descriptors):
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    fcntl.lockf(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                os.close(descriptor)


def _active_owner_matches(active_path, transaction_id, candidate_slot):
    if not transaction_id:
        return False
    if active_path.is_symlink():
        raise SlotError("E_STATE_SCHEMA", "active transaction pointer is unsafe")
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SlotError("E_STATE_SCHEMA", "active transaction pointer is invalid") from exc
    return (
        active.get("schema") == "ming.active-transaction.v1"
        and active.get("transaction_id") == transaction_id
        and active.get("candidate_slot") == candidate_slot
        and active.get("state") == "new"
    )


def inactive_slot_reclaim_bytes(*, state_root, previous_slot, candidate_slot, owner_transaction_id=None):
    if previous_slot not in {"legacy", "A", "B"} or candidate_slot not in {"A", "B"} or previous_slot == candidate_slot:
        raise SlotError("E_SLOT_MISMATCH", "inactive slot relationship is invalid")
    state_root = pathlib.Path(state_root)
    active = state_root / "active-transaction.json"
    if active.exists() or active.is_symlink():
        if not _active_owner_matches(active, owner_transaction_id, candidate_slot):
            raise SlotError("E_BUSY", "an unresolved transaction prevents slot retirement")
    slot_dir = state_root / "slots" / candidate_slot
    if not slot_dir.exists():
        return 0
    if slot_dir.is_symlink() or not slot_dir.is_dir():
        raise SlotError("E_SLOT_MISMATCH", "inactive slot directory is unsafe")
    return tree_allocated_bytes(slot_dir)


def retire_inactive_slot(*, state_root, previous_slot, candidate_slot, owner_transaction_id=None):
    reclaim = inactive_slot_reclaim_bytes(
        state_root=state_root,
        previous_slot=previous_slot,
        candidate_slot=candidate_slot,
        owner_transaction_id=owner_transaction_id,
    )
    if reclaim == 0:
        return 0
    slots_root = pathlib.Path(state_root) / "slots"
    slot_dir = slots_root / candidate_slot
    retired = slots_root / f".retired-{candidate_slot}-{uuid.uuid4().hex}"
    try:
        os.replace(slot_dir, retired)
        shutil.rmtree(retired)
    except OSError as exc:
        try:
            if retired.exists() and not slot_dir.exists():
                os.replace(retired, slot_dir)
        except OSError:
            pass
        raise SlotError("E_CLONE", f"inactive slot retirement failed: {exc}") from exc
    return reclaim


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


def _clear_directory(path):
    for child in pathlib.Path(path).iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise SlotError("E_CLONE", "candidate root contains an unsupported entry")


def final_sync_active_root(active_root, candidate_root):
    """Refresh the clone while the dpkg locks are held, before payload writes."""
    active_root = pathlib.Path(active_root)
    candidate_root = pathlib.Path(candidate_root)
    if active_root.is_symlink() or not active_root.is_dir():
        raise SlotError("E_CLONE", "active root is unsafe for final sync")
    if candidate_root.is_symlink() or not candidate_root.is_dir():
        raise SlotError("E_CONTENT_POLICY", "candidate root is unsafe for final sync")
    if shutil.which("rsync"):
        try:
            result = subprocess.run(
                rsync_clone_command(active_root, candidate_root),
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SlotError("E_CLONE", f"final root synchronization failed: {exc}") from exc
        if result.returncode != 0:
            raise SlotError("E_CLONE", "final root synchronization failed", {"stderr": (result.stderr or "")[-512:]})
        return
    _clear_directory(candidate_root)
    for source in sorted(active_root.rglob("*"), key=lambda item: item.as_posix()):
        _copy_entry(source, candidate_root / source.relative_to(active_root), active_root)


def rsync_clone_command(active_root, staging_root):
    source = str(active_root).rstrip("/\\") + "/"
    target = str(staging_root).rstrip("/\\") + "/"
    return [
        "rsync",
        "-aHAX",
        "--numeric-ids",
        "--one-file-system",
        "--delete",
        "--delete-excluded",
        *[f"--exclude=/{prefix}" for prefix in EXCLUDED],
        "--",
        source,
        target,
    ]


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
        rsync = shutil.which("rsync")
        if rsync:
            command = rsync_clone_command(active_root, staging)
            command[0] = rsync
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=7200,
                check=False,
            )
            if result.returncode != 0:
                raise SlotError(
                    "E_CLONE",
                    "metadata-safe root clone failed",
                    {"stderr": (result.stderr or "")[-1024:]},
                )
        elif os.name == "nt":
            for child in active_root.iterdir():
                _copy_entry(child, staging / child.name, active_root)
        else:
            raise SlotError("E_CLONE", "rsync is required for a metadata-safe root clone")
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
    temporary = slot_dir / f".slot.json.tmp-{uuid.uuid4().hex}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(slot_record, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, slot_dir / "slot.json")
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        try:
            directory = os.open(slot_dir, flags)
        except OSError:
            # Windows cannot open a directory descriptor; the atomic rename is
            # still the durable primitive available on that platform.
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return candidate


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
