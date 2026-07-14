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
import sys
from pathlib import Path
from typing import Any, Iterable


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


def verify_installed(root: Path | str) -> dict[str, Any]:
    """Validate the installed target before its bootloader is written."""
    root_path = Path(root)
    errors: list[str] = []
    fstab = _read_text(root_path / "etc/fstab")
    root_entry_found = False
    for fields in _fstab_entries(fstab):
        if _is_temporary_live_media(fields):
            errors.append("Installed fstab contains a temporary live-media or Ventoy path")
        if fields[1] == "/" and fields[2] not in {"tmpfs", "squashfs"}:
            root_entry_found = True
    if not root_entry_found:
        errors.append("Installed fstab has no persistent root filesystem entry")

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
    installed.add_argument("target", nargs="?", default="/target", help="Calamares target root")
    installed.add_argument("--root", dest="root", help="explicit target root")
    args = parser.parse_args(argv)

    if args.command == "live":
        result = verify_live(args.root, args.source)
    else:
        result = verify_installed(args.root or args.target)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
