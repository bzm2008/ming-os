#!/usr/bin/env python3
"""Apply Ming OS' first-boot and last-known-healthy GRUB policy."""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import time
import uuid


STATE_RELATIVE = pathlib.Path("var/lib/ming-boot-policy")
POLICY_RELATIVE = pathlib.Path("etc/default/grub.d/99-ming-boot-policy.cfg")
GRUBENV_RELATIVE = pathlib.Path("boot/grub/grubenv")
ALLOWED_ENTRIES = {
    "ming-normal",
    "ming-safe-graphics",
    "ming-old-intel",
    "ming-radeon-legacy",
    "ming-radeon-gcn",
    "ming-legacy",
    "ming-slot-a",
    "ming-slot-b",
}


class BootPolicyError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_text(path, content, mode=0o644):
    path = pathlib.Path(path)
    if path.exists() and path.is_symlink():
        raise BootPolicyError("E_BOOT_POLICY_PATH", "boot policy path is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _read_grubenv(path):
    result = subprocess.run(
        ["grub-editenv", str(path), "list"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return {}
    return {
        key: value
        for line in result.stdout.splitlines()
        for key, separator, value in (line.partition("="),)
        if separator
    }


def _cmdline_entry(cmdline):
    for token in str(cmdline).split():
        if token.startswith("ming.entry="):
            entry = token.partition("=")[2]
            return entry if entry in ALLOWED_ENTRIES else ""
    return ""


def _boot_id(root):
    path = pathlib.Path(root) / "proc/sys/kernel/random/boot_id"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _healthy_receipt(root, boot_id=""):
    root = pathlib.Path(root)
    candidates = sorted((root / "home").glob("*/.cache/ming-os/session-startup.json"))
    stale = False
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if value.get("healthy") is not True or value.get("phase") not in {
            "startup", "supervisor", "reload-dock",
        }:
            continue
        if boot_id and value.get("boot_id") != boot_id:
            stale = True
            continue
        if value.get("healthy") is True:
            return path, value
    if stale:
        raise BootPolicyError("E_DESKTOP_STALE", "desktop receipt belongs to another boot")
    raise BootPolicyError("E_DESKTOP_NOT_HEALTHY", "healthy desktop receipt is unavailable")


def status(root="/", *, cmdline=None, grubenv_reader=_read_grubenv):
    root = pathlib.Path(root)
    if cmdline is None:
        try:
            cmdline = (root / "proc/cmdline").read_text(encoding="utf-8")
        except OSError:
            cmdline = ""
    grubenv = root / GRUBENV_RELATIVE
    values = grubenv_reader(grubenv) if grubenv.is_file() and not grubenv.is_symlink() else {}
    confirmed = root / STATE_RELATIVE / "confirmed.json"
    policy = root / POLICY_RELATIVE
    return {
        "schema": "ming.boot-policy.v1",
        "available": True,
        "current_entry": _cmdline_entry(cmdline),
        "saved_entry": values.get("saved_entry", ""),
        "next_entry": values.get("next_entry", ""),
        "recordfail": values.get("recordfail") == "1",
        "first_boot": not confirmed.is_file() or confirmed.is_symlink(),
        "menu_mode": "hidden" if policy.is_file() and confirmed.is_file() else "menu",
        "timestamp": _timestamp(),
    }


def _run(command, runner=subprocess.run):
    result = runner(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise BootPolicyError("E_BOOT_POLICY_APPLY", f"command failed: {command[0]}")
    return result


def confirm(root="/", *, cmdline=None, grubenv_reader=_read_grubenv, runner=subprocess.run):
    root = pathlib.Path(root)
    receipt_path, _receipt = _healthy_receipt(root, _boot_id(root))
    if cmdline is None:
        try:
            cmdline = (root / "proc/cmdline").read_text(encoding="utf-8")
        except OSError as exc:
            raise BootPolicyError("E_BOOT_ENTRY", "kernel command line is unavailable") from exc
    entry = _cmdline_entry(cmdline)
    if not entry:
        raise BootPolicyError("E_BOOT_ENTRY", "current Ming boot entry is unavailable")
    policy_path = root / POLICY_RELATIVE
    policy_content = (
        "# Managed by ming-boot-policy after a healthy desktop start.\n"
        "GRUB_DEFAULT=saved\n"
        "GRUB_SAVEDEFAULT=false\n"
        "GRUB_TIMEOUT_STYLE=hidden\n"
        "GRUB_TIMEOUT=0\n"
        "GRUB_RECORDFAIL_TIMEOUT=8\n"
    )
    command_root = [] if root == pathlib.Path("/") else ["chroot", str(root)]
    values = grubenv_reader(root / GRUBENV_RELATIVE)
    policy_changed = not policy_path.is_file() or policy_path.read_text(
        encoding="utf-8", errors="replace") != policy_content
    if policy_changed:
        _atomic_text(policy_path, policy_content)
    if values.get("saved_entry") != entry:
        _run(command_root + ["grub-set-default", entry], runner)
        values = grubenv_reader(root / GRUBENV_RELATIVE)
        if values.get("saved_entry") != entry:
            raise BootPolicyError("E_GRUB_READBACK", "saved boot entry readback differs")
    if policy_changed:
        _run(command_root + ["update-grub"], runner)
    receipt = {
        "schema": "ming.boot-policy-confirmation.v1",
        "entry": entry,
        "desktop_receipt": str(receipt_path.relative_to(root)),
        "confirmed_at": _timestamp(),
    }
    _atomic_text(root / STATE_RELATIVE / "confirmed.json", json.dumps(receipt, sort_keys=True) + "\n")
    return status(root=root, cmdline=cmdline, grubenv_reader=grubenv_reader)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "confirm"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--wait", type=int, default=0)
    parser.add_argument("--root", default="/")
    arguments = parser.parse_args(argv)
    deadline = time.monotonic() + max(0, min(arguments.wait, 120))
    while True:
        try:
            value = status(arguments.root) if arguments.command == "status" else confirm(arguments.root)
            print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
            return 0
        except BootPolicyError as exc:
            if (
                arguments.command == "confirm"
                and exc.code in {"E_DESKTOP_NOT_HEALTHY", "E_DESKTOP_STALE"}
                and time.monotonic() < deadline
            ):
                time.sleep(2)
                continue
            print(json.dumps({
                "schema": "ming.boot-policy.v1",
                "available": False,
                "error_code": exc.code,
                "reason": exc.message,
            }, ensure_ascii=True, separators=(",", ":")))
            # A missing desktop receipt leaves the visible recovery menu in
            # place and must not make graphical.target fail.
            return 0 if arguments.wait else 10


if __name__ == "__main__":
    raise SystemExit(main())
