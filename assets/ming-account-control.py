#!/usr/bin/env python3
"""Privileged Ming OS account password control."""

import argparse
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile

try:
    import pwd
except ImportError:  # Windows contract tests
    pwd = None


USER_PATTERN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")


def run_command(command, input_text=None):
    try:
        result = subprocess.run(
            command, input=input_text, capture_output=True, text=True,
            errors="replace", timeout=15)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def validate_user(user):
    if not USER_PATTERN.fullmatch(user or "") or user == "root":
        raise ValueError("invalid account")
    return user


def caller_may_change(user, environ=None, uid_lookup=None):
    environ = os.environ if environ is None else environ
    pkexec_uid = environ.get("PKEXEC_UID")
    if not pkexec_uid:
        return True
    uid_lookup = uid_lookup or (pwd.getpwuid if pwd is not None else None)
    if uid_lookup is None:
        return False
    try:
        return uid_lookup(int(pkexec_uid)).pw_name == user
    except (KeyError, TypeError, ValueError):
        return False


def password_status(user, runner=run_command):
    validate_user(user)
    rc, output, error = runner(["passwd", "-S", user])
    fields = output.split()
    code = fields[1] if rc == 0 and len(fields) >= 2 else ""
    return {
        "ok": rc == 0,
        "user": user,
        "password_set": code == "P",
        "password_locked": code in {"L", "LK"},
        "status": code or "unknown",
        "error": "" if rc == 0 else (error or "unable to read account status"),
    }


def oobe_marker_for_user(user):
    if pwd is None:
        return None
    try:
        return pathlib.Path(pwd.getpwnam(user).pw_dir) / ".config" / "ming-os" / "oobe-account-done"
    except KeyError:
        return None


def retire_skipped_marker(user, marker_path=None, expected_uid=None):
    marker = pathlib.Path(marker_path) if marker_path is not None else oobe_marker_for_user(user)
    if marker is None:
        return True
    temporary = None
    try:
        try:
            identity = pwd.getpwnam(user) if pwd is not None else None
        except KeyError:
            identity = None
        if expected_uid is None:
            expected_uid = identity.pw_uid if identity is not None else marker.parent.stat().st_uid
        parent_info = marker.parent.lstat()
        if (stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != expected_uid
                or (os.name != "nt" and parent_info.st_mode & 0o022)):
            return False
        marker_info = marker.lstat()
        if (stat.S_ISLNK(marker_info.st_mode) or not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_uid != expected_uid
                or (os.name != "nt" and marker_info.st_mode & 0o022)):
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(marker, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            opened_info = os.fstat(handle.fileno())
            if (opened_info.st_ino != marker_info.st_ino
                    or opened_info.st_dev != marker_info.st_dev):
                return False
            marker_value = handle.read(128).strip()
        if marker_value != "skipped":
            return True
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".oobe-account-done.", dir=str(marker.parent))
        temporary = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("configured\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), marker_info.st_mode & 0o777)
            if hasattr(os, "fchown"):
                os.fchown(handle.fileno(), marker_info.st_uid, marker_info.st_gid)
        os.replace(temporary, marker)
        temporary = None
        if os.name != "nt":
            directory_flags = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(marker.parent, os.O_RDONLY | directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return marker.read_text(encoding="utf-8").strip() == "configured"
    except FileNotFoundError:
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def set_password(user, password, runner=run_command, marker_path=None):
    validate_user(user)
    password = (password or "").rstrip("\r\n")
    if not password or "\n" in password or "\r" in password or len(password) > 1024:
        return {"ok": False, "error": "password must be non-empty", "user": user}
    rc, _output, error = runner(["chpasswd"], "%s:%s\n" % (user, password))
    password = ""
    if rc != 0:
        return {"ok": False, "error": error or "password update failed", "user": user}
    status = password_status(user, runner=runner)
    status["ok"] = status["ok"] and status["password_set"]
    if not status["ok"] and not status["error"]:
        status["error"] = "password readback did not confirm the update"
    if status["ok"] and not retire_skipped_marker(user, marker_path=marker_path):
        status["ok"] = False
        status["error"] = "password updated but skipped OOBE marker could not be retired"
    return status


def clear_password(user, runner=run_command):
    validate_user(user)
    rc, _output, error = runner(["passwd", "-d", user])
    if rc != 0:
        return {"ok": False, "error": error or "password clear failed", "user": user}
    status = password_status(user, runner=runner)
    status["ok"] = status["ok"] and not status["password_set"]
    if not status["ok"] and not status["error"]:
        status["error"] = "password readback did not confirm passwordless state"
    return status


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-account-control")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "set-password", "clear-password"):
        command = sub.add_parser(action)
        command.add_argument("--user", required=True)
        if action == "status":
            command.add_argument("--json", action="store_true")
    return parser


def main(argv=None, stdin=None, stdout=None):
    args = build_parser().parse_args(argv)
    stdout = stdout or sys.stdout
    try:
        if args.action == "status":
            result = password_status(args.user)
        else:
            if os.geteuid() != 0:
                result = {"ok": False, "error": "authorization required"}
            elif not caller_may_change(args.user):
                result = {"ok": False, "error": "account does not match the active caller"}
            elif args.action == "clear-password":
                result = clear_password(args.user)
            else:
                source = stdin or sys.stdin
                result = set_password(args.user, source.readline(1026))
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
