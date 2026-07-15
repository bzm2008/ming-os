#!/usr/bin/env python3
"""Detect the complete 26.3.2 transactional OTA bootstrap capability."""

import datetime
import json
import os
import pathlib
import uuid


BOOTSTRAP_VERSION = "1.0.0"
CAPABILITY = "transactional-slot-v1"
REQUIRED_RELATIVE_PATHS = (
    "usr/share/ming-update/trust/release-keyring.gpg",
    "usr/share/ming-update/trust/key-policy.json",
    "usr/local/lib/ming-update/ming-transaction-verify.py",
    "usr/local/lib/ming-update/ming-transaction-state.py",
    "usr/local/lib/ming-update/ming-transaction-boot.py",
    "usr/local/lib/ming-update/ming-transaction-health.py",
    "usr/local/bin/ming-update",
    "etc/initramfs-tools/hooks/ming-transaction",
    "etc/grub.d/40_ming_transaction",
    "etc/systemd/system/ming-transaction-health.service",
    "etc/systemd/system/ming-transaction-reconcile.service",
    "var/lib/ming-update/protocol-version",
)


class BootstrapError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def required_paths(root="/"):
    root = pathlib.Path(root)
    return [root / relative for relative in REQUIRED_RELATIVE_PATHS]


def detect_capability(root="/"):
    missing = []
    unsafe = []
    for path in required_paths(root):
        if not path.is_file():
            missing.append(str(path))
        elif path.is_symlink() or path.stat().st_size <= 0:
            unsafe.append(str(path))
    available = not missing and not unsafe
    return {
        "schema": "ming.bootstrap-capability.v1",
        "available": available,
        "capability": CAPABILITY if available else None,
        "bootstrap_version": BOOTSTRAP_VERSION if available else None,
        "missing": missing,
        "unsafe": unsafe,
        "timestamp": _timestamp(),
    }


def write_capability_marker(root="/"):
    root = pathlib.Path(root)
    status = detect_capability(root)
    if not status["available"]:
        raise BootstrapError("E_BOOTSTRAP_VERSION", "transaction bootstrap is incomplete")
    marker = root / "var" / "lib" / "ming-update" / "capability.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.is_symlink():
        raise BootstrapError("E_STATE_DURABILITY", "capability marker is unsafe")
    value = {
        "schema": "ming.bootstrap-capability.v1",
        "capability": CAPABILITY,
        "bootstrap_version": BOOTSTRAP_VERSION,
        "verified_at": _timestamp(),
    }
    temporary = marker.with_name(f".{marker.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, marker)
    return value


def update_path(current_version, capability_status):
    if current_version == "26.3.2":
        if capability_status.get("available") and capability_status.get("capability") == CAPABILITY:
            return CAPABILITY
        return "bootstrap-required"
    return "unsupported"


if __name__ == "__main__":
    print(json.dumps(detect_capability(), ensure_ascii=True, separators=(",", ":")))
