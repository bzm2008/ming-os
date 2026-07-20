#!/usr/bin/env python3
"""Small, dependency-free checks for Ming's Calamares install path.

The tool is deliberately usable from the Live session before Calamares starts
and from a Calamares shellprocess after the target filesystem is unpacked.
It reports diagnostics as JSON so the caller can add them to its own log.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


STABLE_SQUASHFS_SOURCE = "/run/ming-installer/filesystem.squashfs"
TEMPORARY_LIVE_MEDIA_MARKERS = (
    "/run/live/",
    "/lib/live/",
    "/run/ming-installer/",
    "/run/ventoy/",
    "/media/ventoy/",
    "filesystem.squashfs",
    "iso-scan/",
    "ventoy",
    "/dev/loop",
)
REQUIRED_DESKTOP_EXECUTABLES = (
    "usr/sbin/lightdm",
    "usr/bin/startxfce4",
    "usr/bin/xfce4-session",
    "usr/local/bin/ming-phone-desktop",
    "usr/local/bin/ming-session-healthcheck",
)
REQUIRED_DESKTOP_FILES = (
    "usr/share/xsessions/xfce.desktop",
    "home/user/.config/autostart/ming-session-healthcheck.desktop",
)
TARGET_RECEIPT_PATH = Path("/run/ming-installer/target-receipt.json")
TARGET_RECEIPT_SCHEMA = "ming-installer-target-receipt/v1"
TARGET_RECEIPT_VERSION = 1
TARGET_RECEIPT_ATTEMPT_PATH = Path("/run/ming-installer/target-receipt-attempt.json")
TARGET_RECEIPT_ATTEMPT_SCHEMA = "ming-installer-target-receipt-attempt/v1"
TARGET_RECEIPT_ATTEMPT_VERSION = 1
TEMPORARY_ROOT_SOURCES = (
    "overlay",
    "tmpfs",
    "squashfs",
    "/dev/loop",
    "/run/live/",
    "/run/ventoy/",
    "filesystem.squashfs",
)
UUID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class TargetReceiptError(RuntimeError):
    """The Calamares authoritative root-target receipt is unsafe or stale."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _yaml_scalar(text: str, key: str) -> str | None:
    """Read the simple top-level scalar fields used by Calamares configs."""
    pattern = re.compile(r"^\s*(?:-\s+)?%s\s*:\s*(.*?)\s*(?:#.*)?$" % re.escape(key))
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        return value
    return None


def _yaml_bool(text: str, key: str) -> bool | None:
    value = _yaml_scalar(text, key)
    if value is None:
        return None
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    return None


def _calamares_show_steps(settings: str) -> set[str]:
    """Extract entries from Calamares's small ``sequence: show`` list."""
    steps: set[str] = set()
    in_show = False
    show_indent = -1
    for line in settings.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if re.match(r"-\s+show\s*:\s*$", stripped):
            in_show = True
            show_indent = indent
            continue
        if in_show and indent <= show_indent and stripped.startswith("-"):
            in_show = False
        if in_show:
            match = re.match(r"-\s+([^\s#]+)", stripped)
            if match:
                steps.add(match.group(1))
    return steps


def _source_kind(source: Path) -> str:
    parts = [part.casefold() for part in source.parts]
    return "ventoy" if any("ventoy" in part for part in parts) else "live"


def _result(errors: Iterable[str], **values: Any) -> dict[str, Any]:
    messages = list(errors)
    return {"ok": not messages, "errors": messages, **values}


def verify_live(root: Path | str = "/", source: Path | str | None = None) -> dict[str, Any]:
    """Validate the Live Calamares contract before the installer is shown."""
    root_path = Path(root)
    errors: list[str] = []
    settings = _read_text(root_path / "etc/calamares/settings.conf")
    partition = _read_text(root_path / "etc/calamares/modules/partition.conf")
    unpackfs = _read_text(root_path / "etc/calamares/modules/unpackfs.conf")

    if "partition" not in _calamares_show_steps(settings):
        errors.append("Calamares show sequence does not expose the partition page")

    initial_choice = _yaml_scalar(partition, "initialPartitioningChoice")
    manual_enabled = _yaml_bool(partition, "allowManualPartitioning")
    if initial_choice != "none":
        errors.append(
            "Calamares must leave initialPartitioningChoice at none so both install choices are visible"
        )
    if manual_enabled is not True:
        errors.append("Calamares manual partitioning is disabled")

    unpack_source = _yaml_scalar(unpackfs, "source")
    source_path = Path(source) if source is not None else Path(unpack_source or "")
    if not unpack_source:
        errors.append("Calamares unpackfs source is missing")
    elif unpack_source != STABLE_SQUASHFS_SOURCE and source is None:
        errors.append("Calamares unpackfs must use the stable Ming squashfs source")
    if not source_path or not source_path.is_file():
        errors.append("Live filesystem.squashfs source is unavailable")
    elif source_path.name != "filesystem.squashfs":
        errors.append("Live installer source must be live/filesystem.squashfs")

    return _result(
        errors,
        manual_partitioning="enabled" if manual_enabled is True else "disabled",
        full_disk_install="available" if initial_choice == "none" else "unknown",
        source_kind=_source_kind(source_path),
        source=str(source_path),
        unpackfs_source=unpack_source or "",
    )


def _fstab_entries(text: str) -> Iterable[list[str]]:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 3:
            yield fields


def _is_temporary_live_media(fields: Iterable[str]) -> bool:
    joined = " ".join(fields).casefold()
    return any(marker in joined for marker in TEMPORARY_LIVE_MEDIA_MARKERS)


def _system_target(path: Path) -> str:
    if path.is_symlink():
        try:
            return os.readlink(path)
        except OSError:
            return ""
    return _read_text(path).strip()


def _safe_path(value: object, field: str) -> Path:
    """Reject non-canonical and traversal-capable receipt paths."""
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise TargetReceiptError(f"{field} is not a safe non-empty path")
    path = Path(value)
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise TargetReceiptError(f"{field} is not an absolute traversal-free path")
    try:
        canonical = path.resolve(strict=False)
    except OSError as exc:
        raise TargetReceiptError(f"{field} cannot be canonicalized: {exc}") from exc
    if canonical == Path("/"):
        raise TargetReceiptError(f"{field} must not point at the Live root")
    return canonical


def _command_value(*command: str) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetReceiptError(f"cannot inspect mounted target: {exc}") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value or "\n" in value:
        raise TargetReceiptError(f"mount inspection failed for {' '.join(command)}")
    return value


def _system_mount_info(target: Path) -> dict[str, Any]:
    """Read the one mounted filesystem Calamares gave us, without candidate scans."""
    mount_target = _safe_path(_command_value("findmnt", "-nro", "TARGET", "--target", str(target)), "mount target")
    if mount_target != target:
        raise TargetReceiptError("rootMountPoint does not resolve to its own mounted target")
    source = _command_value("findmnt", "-nro", "SOURCE", "--target", str(target))
    fstype = _command_value("findmnt", "-nro", "FSTYPE", "--target", str(target))
    if not source.startswith("/dev/") or any(marker in source for marker in TEMPORARY_ROOT_SOURCES):
        raise TargetReceiptError(f"mounted target source is not a persistent block device: {source}")
    if fstype in {"", "overlay", "tmpfs", "squashfs"}:
        raise TargetReceiptError(f"mounted target filesystem is not persistent: {fstype or 'unknown'}")
    try:
        source_stat = os.stat(source)
    except OSError as exc:
        raise TargetReceiptError(f"mounted target source is unavailable: {source}") from exc
    if not stat.S_ISBLK(source_stat.st_mode):
        raise TargetReceiptError(f"mounted target source is not a block device: {source}")
    uuid = _command_value("blkid", "-s", "UUID", "-o", "value", source)
    return {
        "target": str(target),
        "canonical_target": str(target),
        "source": source,
        "canonical_source": os.path.realpath(source),
        "fstype": fstype,
        "uuid": uuid,
        "is_block": True,
    }


def _validate_mount_info(target: Path, mount_info: Mapping[str, Any]) -> dict[str, str]:
    """Validate live mount facts before they can become trusted installer state."""
    fields = ("target", "canonical_target", "source", "canonical_source", "fstype", "uuid")
    if not isinstance(mount_info, Mapping) or any(not isinstance(mount_info.get(key), str) for key in fields):
        raise TargetReceiptError("mounted target record is incomplete")
    mount_target = _safe_path(mount_info["target"], "mount target")
    canonical_target = _safe_path(mount_info["canonical_target"], "canonical mount target")
    if mount_target != target or canonical_target != target:
        raise TargetReceiptError("mounted target does not match Calamares rootMountPoint")
    source = mount_info["source"]
    canonical_source = mount_info["canonical_source"]
    fstype = mount_info["fstype"]
    uuid = mount_info["uuid"]
    if (
        not source.startswith("/dev/")
        or not canonical_source.startswith("/dev/")
        or any(marker in source for marker in TEMPORARY_ROOT_SOURCES)
        or any(marker in canonical_source for marker in TEMPORARY_ROOT_SOURCES)
    ):
        raise TargetReceiptError("mounted target source is not a persistent block device")
    if mount_info.get("is_block") is not True:
        raise TargetReceiptError("mounted target source is not a block device")
    if fstype in {"", "overlay", "tmpfs", "squashfs"}:
        raise TargetReceiptError("mounted target filesystem is not persistent")
    if not UUID_PATTERN.fullmatch(uuid):
        raise TargetReceiptError("mounted target UUID is invalid")
    if (target / ".disk/info").exists():
        raise TargetReceiptError("mounted target is Live media")
    return {
        "target": str(target),
        "canonical_target": str(target),
        "source": source,
        "canonical_source": canonical_source,
        "fstype": fstype,
        "uuid": uuid,
    }


def _validate_receipt_stat(receipt_path: Path, receipt_stat: object) -> None:
    mode = getattr(receipt_stat, "st_mode", 0)
    uid = getattr(receipt_stat, "st_uid", -1)
    if not stat.S_ISREG(mode):
        raise TargetReceiptError(f"receipt is not a regular file: {receipt_path}")
    if uid != 0:
        raise TargetReceiptError(f"receipt is not root-owned: {receipt_path}")
    if mode & 0o077:
        raise TargetReceiptError(f"receipt permissions are broader than 0600: {receipt_path}")


def _ensure_receipt_directory(receipt_path: Path) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory_stat = os.lstat(receipt_path.parent)
    except OSError as exc:
        raise TargetReceiptError(f"cannot inspect receipt directory: {exc}") from exc
    if not stat.S_ISDIR(directory_stat.st_mode) or stat.S_ISLNK(directory_stat.st_mode):
        raise TargetReceiptError("receipt directory is not a real directory")
    if directory_stat.st_uid != 0:
        raise TargetReceiptError("receipt directory is not root-owned")
    if directory_stat.st_mode & 0o077:
        os.chmod(receipt_path.parent, 0o700)
    if os.lstat(receipt_path.parent).st_mode & 0o077:
        raise TargetReceiptError("receipt directory permissions are unsafe")


def _attempt_path_for(receipt_path: Path | str, attempt_path: Path | str | None) -> Path:
    return Path(attempt_path) if attempt_path is not None else Path(receipt_path).with_name(
        TARGET_RECEIPT_ATTEMPT_PATH.name
    )


def _write_secure_json(path: Path, payload: Mapping[str, Any], *, label: str) -> None:
    """Atomically write a root-only JSON record in the trusted installer directory."""
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}.",
            suffix=".json",
            dir=path.parent,
            text=True,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = ""
        os.chmod(path, 0o600)
    except OSError as exc:
        raise TargetReceiptError(f"cannot atomically write {label}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _read_secure_json(
    path: Path,
    *,
    label: str,
    lstat_func: Callable[[Path], object],
    fstat_func: Callable[[int], object],
) -> dict[str, Any]:
    try:
        _validate_receipt_stat(path, lstat_func(path))
    except OSError as exc:
        raise TargetReceiptError(f"cannot inspect {label}: {exc}") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TargetReceiptError(f"cannot securely open {label}: {exc}") from exc
    try:
        _validate_receipt_stat(path, fstat_func(descriptor))
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise TargetReceiptError(f"{label} is malformed: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise TargetReceiptError(f"{label} must be a JSON object")
    return payload


def _remove_current_target_receipt(
    path: Path,
    *,
    lstat_func: Callable[[Path], object] = os.lstat,
) -> None:
    try:
        receipt_stat = lstat_func(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TargetReceiptError(f"cannot inspect previous target receipt: {exc}") from exc
    _validate_receipt_stat(path, receipt_stat)
    try:
        os.unlink(path)
    except OSError as exc:
        raise TargetReceiptError(f"cannot clear previous target receipt: {exc}") from exc


def begin_target_receipt_attempt(
    *,
    receipt_path: Path | str = TARGET_RECEIPT_PATH,
    attempt_path: Path | str | None = None,
    nonce_factory: Callable[[int], str] = secrets.token_urlsafe,
    lstat_func: Callable[[Path], object] = os.lstat,
) -> dict[str, Any]:
    """Invalidate prior target state before every Calamares mount attempt."""
    path = Path(receipt_path)
    attempt = _attempt_path_for(path, attempt_path)
    if attempt.parent != path.parent:
        raise TargetReceiptError("target receipt attempt must stay in the receipt directory")
    _ensure_receipt_directory(path)
    _remove_current_target_receipt(path, lstat_func=lstat_func)
    nonce = nonce_factory(32)
    if not isinstance(nonce, str) or not NONCE_PATTERN.fullmatch(nonce):
        raise TargetReceiptError("target receipt attempt nonce is invalid")
    payload = {
        "schema": TARGET_RECEIPT_ATTEMPT_SCHEMA,
        "version": TARGET_RECEIPT_ATTEMPT_VERSION,
        "nonce": nonce,
    }
    _write_secure_json(attempt, payload, label="target receipt attempt")
    return payload


def _read_target_receipt_attempt(
    receipt_path: Path | str,
    *,
    attempt_path: Path | str | None,
    lstat_func: Callable[[Path], object],
    fstat_func: Callable[[int], object],
) -> dict[str, Any]:
    attempt = _attempt_path_for(receipt_path, attempt_path)
    payload = _read_secure_json(
        attempt,
        label="target receipt attempt",
        lstat_func=lstat_func,
        fstat_func=fstat_func,
    )
    if set(payload) != {"schema", "version", "nonce"}:
        raise TargetReceiptError("target receipt attempt fields are incomplete or unexpected")
    if (
        payload.get("schema") != TARGET_RECEIPT_ATTEMPT_SCHEMA
        or payload.get("version") != TARGET_RECEIPT_ATTEMPT_VERSION
        or not isinstance(payload.get("nonce"), str)
        or not NONCE_PATTERN.fullmatch(payload["nonce"])
    ):
        raise TargetReceiptError("target receipt attempt is invalid")
    return payload


def capture_target_receipt(
    root_mount_point: Path | str,
    *,
    receipt_path: Path | str = TARGET_RECEIPT_PATH,
    attempt_path: Path | str | None = None,
    mount_info_provider: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically persist Calamares's authoritative rootMountPoint after mount."""
    target = _safe_path(root_mount_point, "rootMountPoint")
    provider = mount_info_provider or _system_mount_info
    path = Path(receipt_path)
    attempt = _read_target_receipt_attempt(
        path,
        attempt_path=attempt_path,
        lstat_func=os.lstat,
        fstat_func=os.fstat,
    )
    payload = {
        "schema": TARGET_RECEIPT_SCHEMA,
        "version": TARGET_RECEIPT_VERSION,
        "attempt_nonce": attempt["nonce"],
        **_validate_mount_info(target, provider(target)),
    }
    _ensure_receipt_directory(path)
    _write_secure_json(path, payload, label="target receipt")
    return payload


def read_target_receipt(
    receipt_path: Path | str = TARGET_RECEIPT_PATH,
    *,
    attempt_path: Path | str | None = None,
    mount_info_provider: Callable[[Path], Mapping[str, Any]] | None = None,
    lstat_func: Callable[[Path], object] = os.lstat,
    fstat_func: Callable[[int], object] = os.fstat,
) -> dict[str, Any]:
    """Read only a root-owned receipt and re-check it against the mounted target."""
    path = Path(receipt_path)
    payload = _read_secure_json(
        path,
        label="target receipt",
        lstat_func=lstat_func,
        fstat_func=fstat_func,
    )
    expected_keys = {
        "schema",
        "version",
        "attempt_nonce",
        "target",
        "canonical_target",
        "source",
        "canonical_source",
        "fstype",
        "uuid",
    }
    if set(payload) != expected_keys:
        raise TargetReceiptError("target receipt fields are incomplete or unexpected")
    if payload.get("schema") != TARGET_RECEIPT_SCHEMA or payload.get("version") != TARGET_RECEIPT_VERSION:
        raise TargetReceiptError("target receipt schema is unsupported")
    attempt = _read_target_receipt_attempt(
        path,
        attempt_path=attempt_path,
        lstat_func=lstat_func,
        fstat_func=fstat_func,
    )
    if payload.get("attempt_nonce") != attempt["nonce"]:
        raise TargetReceiptError("target receipt belongs to a previous mount attempt")
    target = _safe_path(payload.get("target"), "receipt target")
    canonical_target = _safe_path(payload.get("canonical_target"), "receipt canonical target")
    if target != canonical_target:
        raise TargetReceiptError("receipt target and canonical target differ")
    provider = mount_info_provider or _system_mount_info
    current = _validate_mount_info(target, provider(target))
    for field in ("target", "canonical_target", "source", "canonical_source", "fstype", "uuid"):
        if payload.get(field) != current[field]:
            raise TargetReceiptError(f"target receipt no longer matches mounted {field}")
    return {key: payload[key] for key in expected_keys}


def verify_installed_from_receipt(
    receipt_path: Path | str = TARGET_RECEIPT_PATH,
    *,
    attempt_path: Path | str | None = None,
    mount_info_provider: Callable[[Path], Mapping[str, Any]] | None = None,
    lstat_func: Callable[[Path], object] = os.lstat,
    fstat_func: Callable[[int], object] = os.fstat,
) -> dict[str, Any]:
    """Run the installed-system gate only against Calamares's stored root target."""
    receipt = read_target_receipt(
        receipt_path,
        attempt_path=attempt_path,
        mount_info_provider=mount_info_provider,
        lstat_func=lstat_func,
        fstat_func=fstat_func,
    )
    return verify_installed(
        Path(receipt["canonical_target"]),
        target_mode="receipt",
        expected_root_uuid=receipt["uuid"],
        expected_root_fstype=receipt["fstype"],
    )


def verify_installed(
    root: Path | str | None,
    *,
    target_mode: str = "explicit",
    expected_root_uuid: str | None = None,
    expected_root_fstype: str | None = None,
) -> dict[str, Any]:
    """Validate the installed target before its bootloader is written."""
    requested_target = str(root or "")
    if root is None:
        return _result(
            ["Installed Calamares target root was not provided"],
            requested_target=requested_target,
            target="",
            target_mode=target_mode,
            default_target="non-graphical",
            display_manager="missing",
            desktop_session="incomplete",
        )
    try:
        root_path = Path(root).resolve(strict=False)
    except OSError as exc:
        return _result(
            [f"Installed Calamares target root cannot be resolved: {exc}"],
            requested_target=requested_target,
            target="",
            target_mode=target_mode,
            default_target="non-graphical",
            display_manager="missing",
            desktop_session="incomplete",
        )
    errors: list[str] = []
    fstab = _read_text(root_path / "etc/fstab")
    root_entry_found = False
    authoritative_root_entry_found = False
    for fields in _fstab_entries(fstab):
        if _is_temporary_live_media(fields):
            errors.append("Installed fstab contains a temporary live-media or Ventoy path")
        if fields[1] == "/" and fields[2] not in {"tmpfs", "squashfs"}:
            root_entry_found = True
            if (
                expected_root_uuid
                and fields[0] == f"UUID={expected_root_uuid}"
                and (expected_root_fstype is None or fields[2] == expected_root_fstype)
            ):
                authoritative_root_entry_found = True
    if not root_entry_found:
        errors.append("Installed fstab has no persistent root filesystem entry")
    elif expected_root_uuid and not authoritative_root_entry_found:
        errors.append("Installed fstab root entry does not match the authoritative root UUID")
    if expected_root_uuid:
        grub_template = _read_text(root_path / "etc/grub.d/09_ming_os")
        if not grub_template:
            errors.append("Installed Ming GRUB template is missing after identity repair")
        elif "__MING_ROOT_UUID__" in grub_template:
            errors.append("Installed Ming GRUB template still contains an unresolved root UUID")
        elif f"root=UUID={expected_root_uuid}" not in grub_template:
            errors.append("Installed Ming GRUB template does not match the authoritative root UUID")

    default_target = _system_target(root_path / "etc/systemd/system/default.target")
    if "graphical.target" not in default_target:
        errors.append("Installed system default.target is not graphical.target")

    display_manager = _system_target(root_path / "etc/systemd/system/display-manager.service")
    if "lightdm.service" not in display_manager:
        errors.append("Installed system does not enable LightDM as display-manager.service")

    for relative in REQUIRED_DESKTOP_EXECUTABLES:
        path = root_path / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            errors.append("Installed desktop executable is missing: %s" % relative)
    for relative in REQUIRED_DESKTOP_FILES:
        if not (root_path / relative).is_file():
            errors.append("Installed desktop file is missing: %s" % relative)

    autologin = _read_text(root_path / "etc/lightdm/lightdm.conf.d/60-ming-autologin.conf")
    if "autologin-session=xfce" not in autologin:
        errors.append("Installed LightDM configuration does not select the Xfce session")

    return _result(
        errors,
        requested_target=requested_target,
        target=str(root_path),
        target_mode=target_mode,
        default_target="graphical" if "graphical.target" in default_target else "non-graphical",
        display_manager="lightdm" if "lightdm.service" in display_manager else "missing",
        desktop_session="ready" if not errors else "incomplete",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Ming Calamares install contracts")
    subcommands = parser.add_subparsers(dest="command", required=True)
    live = subcommands.add_parser("live", help="check the Live installer configuration")
    live.add_argument("--root", default="/", help="Live root filesystem")
    live.add_argument("--source", help="resolved live/filesystem.squashfs source")
    installed = subcommands.add_parser("installed", help="check an unpacked target system")
    installed.add_argument("target", nargs="?", help="Calamares target root")
    installed.add_argument("--root", dest="root", help="explicit target root")
    installed.add_argument(
        "--receipt",
        action="store_true",
        help="validate only the root target captured by the Calamares receipt",
    )
    installed.add_argument(
        "--receipt-path",
        default=str(TARGET_RECEIPT_PATH),
        help="authoritative root-target receipt path",
    )
    receipt = subcommands.add_parser("receipt", help="read a validated Calamares root-target receipt")
    receipt.add_argument("--path", default=str(TARGET_RECEIPT_PATH), help="receipt path")
    receipt.add_argument(
        "--begin-attempt",
        action="store_true",
        help="clear any prior receipt and bind the next mounted target to a fresh attempt nonce",
    )
    receipt.add_argument(
        "--field",
        choices=("target", "canonical_target", "source", "canonical_source", "fstype", "uuid"),
        help="print one validated receipt field",
    )
    args = parser.parse_args(argv)

    if args.command == "live":
        result = verify_live(args.root, args.source)
    elif args.command == "receipt":
        if args.begin_attempt:
            if args.field:
                print("ERROR: --begin-attempt cannot be combined with --field", file=sys.stderr)
                return 2
            try:
                payload = begin_target_receipt_attempt(receipt_path=args.path)
            except TargetReceiptError as exc:
                print(f"ERROR: cannot begin Calamares target receipt attempt: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        try:
            payload = read_target_receipt(args.path)
        except TargetReceiptError as exc:
            print(f"ERROR: authoritative Calamares target receipt rejected: {exc}", file=sys.stderr)
            return 1
        if args.field:
            print(payload[args.field])
        else:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    elif args.receipt:
        try:
            result = verify_installed_from_receipt(args.receipt_path)
        except TargetReceiptError as exc:
            result = _result(
                [f"Authoritative Calamares target receipt rejected: {exc}"],
                requested_target="",
                target="",
                target_mode="receipt",
                default_target="non-graphical",
                display_manager="missing",
                desktop_session="incomplete",
            )
    else:
        result = verify_installed(args.root or args.target)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
