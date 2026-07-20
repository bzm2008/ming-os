#!/usr/bin/env python3
"""Detect the complete 26.3.2 transactional OTA bootstrap capability."""

import argparse
import datetime
import json
import os
import pathlib
import stat
import subprocess
import uuid


BOOTSTRAP_VERSION = "1.0.0"
CAPABILITY = "transactional-slot-v1"
REQUIRED_RELATIVE_PATHS = (
    "usr/share/ming-update/trust/release-keyring.gpg",
    "usr/share/ming-update/trust/key-policy.json",
    "usr/share/polkit-1/actions/org.mingos.update.policy",
    "usr/local/lib/ming-update/ming-update-cli.py",
    "usr/local/lib/ming-update/ming-transaction-verify.py",
    "usr/local/lib/ming-update/ming-transaction-state.py",
    "usr/local/lib/ming-update/ming-transaction-slot.py",
    "usr/local/lib/ming-update/ming-transaction-apply.py",
    "usr/local/lib/ming-update/ming-transaction-rollback.py",
    "usr/local/lib/ming-update/ming-transaction-boot.py",
    "usr/local/lib/ming-update/ming-transaction-health.py",
    "usr/local/lib/ming-update/ming-transaction-engine.py",
    "usr/local/lib/ming-update/ming-transaction-diagnostics.py",
    "usr/local/lib/ming-update/ming-transaction-allowlist.txt",
    "usr/local/lib/ming-update/ming-transaction-local-premount",
    "usr/local/bin/ming-update",
    "usr/local/sbin/ming-transaction-health",
    "etc/initramfs-tools/hooks/ming-transaction",
    "etc/grub.d/40_ming_transaction",
    "etc/default/grub.d/40-ming-transaction.cfg",
    "etc/systemd/system/ming-transaction-health.service",
    "etc/systemd/system/ming-transaction-reconcile.service",
    "etc/systemd/system/ming-transaction-rollback-reboot.service",
    "etc/systemd/system/display-manager.service.d/20-ming-transaction-health.conf",
    "boot/grub/grubenv",
    "var/lib/ming-update/protocol-version",
)
REQUIRED_ENABLED_UNITS = (
    "ming-transaction-health.service",
    "ming-transaction-reconcile.service",
)
CAPABILITY_MARKER = "var/lib/ming-update/capability.json"


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


def _read_grubenv(path, runner=subprocess.run):
    try:
        result = runner(
            ["grub-editenv", str(path), "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError("E_BOOTSTRAP_VERSION", "GRUB environment is unavailable") from exc
    if result.returncode != 0:
        raise BootstrapError("E_BOOTSTRAP_VERSION", "GRUB environment cannot be read")
    return {
        key: value
        for line in result.stdout.splitlines()
        for key, separator, value in (line.partition("="),)
        if separator
    }


def _marker_is_valid(path):
    if not path.is_file() or path.is_symlink():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("schema") == "ming.bootstrap-capability.v1"
        and value.get("capability") == CAPABILITY
        and value.get("bootstrap_version") == BOOTSTRAP_VERSION
    )


def detect_capability(root="/", *, require_marker=True, grubenv_reader=_read_grubenv):
    root = pathlib.Path(root)
    missing = []
    unsafe = []
    for path in required_paths(root):
        if not path.is_file():
            missing.append(str(path))
        else:
            try:
                metadata = path.stat()
            except OSError:
                unsafe.append(str(path))
                continue
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                unsafe.append(str(path))
    protocol = root / "var/lib/ming-update/protocol-version"
    if protocol.is_file() and not protocol.is_symlink() and protocol.read_text(encoding="utf-8").strip() != CAPABILITY:
        unsafe.append(str(protocol))
    grub_policy = root / "etc/default/grub.d/40-ming-transaction.cfg"
    if grub_policy.is_file() and not grub_policy.is_symlink():
        try:
            policy = grub_policy.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            unsafe.append(str(grub_policy))
        else:
            if "GRUB_DEFAULT=saved" not in policy or "GRUB_SAVEDEFAULT=false" not in policy:
                unsafe.append(str(grub_policy))
    units_root = root / "etc/systemd/system"
    for unit in REQUIRED_ENABLED_UNITS:
        enabled = units_root / "multi-user.target.wants" / unit
        expected = units_root / unit
        if not enabled.is_symlink():
            missing.append(str(enabled))
            continue
        try:
            if enabled.resolve(strict=True) != expected.resolve(strict=True):
                unsafe.append(str(enabled))
        except OSError:
            unsafe.append(str(enabled))
    grubenv = root / "boot/grub/grubenv"
    if grubenv.is_file() and not grubenv.is_symlink():
        try:
            saved_entry = grubenv_reader(grubenv).get("saved_entry")
        except BootstrapError:
            unsafe.append(str(grubenv))
        else:
            if saved_entry not in {"ming-legacy", "ming-slot-a", "ming-slot-b"}:
                unsafe.append(str(grubenv))
    if require_marker and not _marker_is_valid(root / CAPABILITY_MARKER):
        missing.append(str(root / CAPABILITY_MARKER))
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
    status = detect_capability(root, require_marker=False)
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
    # The marker contains only verified bootstrap metadata.  The desktop-side
    # `ming-update status --json` client must read it without root privileges.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        # Creation mode is filtered by umask; enforce the post-install
        # readability contract explicitly before the atomic replacement.
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise BootstrapError("E_STATE_DURABILITY", "capability marker cannot be made readable") from exc
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/")
    parser.add_argument("--write-marker", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        value = write_capability_marker(arguments.root) if arguments.write_marker else detect_capability(arguments.root)
        print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
        return 0
    except BootstrapError as exc:
        print(json.dumps({"schema": "ming.bootstrap-capability.v1", "available": False, "error_code": exc.code}, ensure_ascii=True, separators=(",", ":")))
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
