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
        return False
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


def _safe_directory(path, expected_uid):
    info = path.lstat()
    return bool(
        stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == expected_uid
        and (os.name == "nt" or not info.st_mode & 0o022))


def _marker_context(user, marker_path=None, expected_uid=None):
    identity = None
    if pwd is not None:
        try:
            identity = pwd.getpwnam(user)
        except KeyError:
            identity = None
    marker = pathlib.Path(marker_path) if marker_path is not None else oobe_marker_for_user(user)
    if marker is None:
        return None
    if expected_uid is None:
        expected_uid = identity.pw_uid if identity is not None else marker.parent.stat().st_uid
    directories = [marker.parent]
    if marker_path is None and identity is not None:
        home = pathlib.Path(identity.pw_dir)
        directories = [home, home / ".config", marker.parent]
    if not all(_safe_directory(path, expected_uid) for path in directories):
        raise OSError("unsafe marker directory")
    parent_info = marker.parent.lstat()
    parent_fd = None
    if os.name != "nt":
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0)
        parent_fd = os.open(marker.parent, directory_flags)
        opened_parent = os.fstat(parent_fd)
        if (opened_parent.st_ino != parent_info.st_ino
                or opened_parent.st_dev != parent_info.st_dev):
            os.close(parent_fd)
            raise OSError("marker directory changed while opening")
        try:
            marker_info = os.stat(marker.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            os.close(parent_fd)
            raise
    else:
        marker_info = marker.lstat()
    if (stat.S_ISLNK(marker_info.st_mode) or not stat.S_ISREG(marker_info.st_mode)
            or marker_info.st_uid != expected_uid
            or (os.name != "nt" and marker_info.st_mode & 0o022)):
        if parent_fd is not None:
            os.close(parent_fd)
        raise OSError("unsafe marker file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            marker.name if parent_fd is not None else marker, flags,
            **({"dir_fd": parent_fd} if parent_fd is not None else {}))
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            opened_info = os.fstat(handle.fileno())
            if (opened_info.st_ino != marker_info.st_ino
                    or opened_info.st_dev != marker_info.st_dev):
                raise OSError("marker changed while opening")
            value = handle.read(128).strip()
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    return marker, marker_info, expected_uid, parent_info, value


def _replace_marker_state(user, replacement, marker_path=None, expected_uid=None,
                          require_skipped=False):
    context = _marker_context(user, marker_path, expected_uid)
    if context is None:
        return True
    marker, marker_info, _uid, parent_info, marker_value = context
    if marker_value == replacement:
        return True
    if marker_value != "skipped":
        return not require_skipped
    marker = pathlib.Path(marker_path) if marker_path is not None else oobe_marker_for_user(user)
    temporary = None
    parent_fd = None
    temporary_name = None
    try:
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
                os, "O_NOFOLLOW", 0)
            parent_fd = os.open(marker.parent, directory_flags)
            opened_parent = os.fstat(parent_fd)
            if (opened_parent.st_ino != parent_info.st_ino
                    or opened_parent.st_dev != parent_info.st_dev):
                return False
        temporary_directory = (
            "/proc/self/fd/%d" % parent_fd if parent_fd is not None else str(marker.parent))
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".oobe-account-done.", dir=temporary_directory)
        temporary = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(replacement + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), marker_info.st_mode & 0o777)
            if hasattr(os, "fchown"):
                os.fchown(handle.fileno(), marker_info.st_uid, marker_info.st_gid)
        current_parent = marker.parent.lstat()
        if (current_parent.st_ino != parent_info.st_ino
                or current_parent.st_dev != parent_info.st_dev):
            return False
        current_marker = (
            os.stat(marker.name, dir_fd=parent_fd, follow_symlinks=False)
            if parent_fd is not None else marker.lstat())
        if (current_marker.st_ino != marker_info.st_ino
                or current_marker.st_dev != marker_info.st_dev):
            return False
        if parent_fd is not None:
            os.replace(
                temporary.name, marker.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        else:
            os.replace(temporary, marker)
        temporary = None
        if parent_fd is not None:
            os.fsync(parent_fd)
            descriptor = os.open(marker.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                 dir_fd=parent_fd)
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                return handle.read(128).strip() == replacement
        return marker.read_text(encoding="utf-8").strip() == replacement
    except FileNotFoundError:
        return False
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                if parent_fd is not None:
                    os.unlink(temporary.name, dir_fd=parent_fd)
                else:
                    temporary.unlink()
            except OSError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)


def retire_skipped_marker(user, marker_path=None, expected_uid=None):
    try:
        return _replace_marker_state(
            user, "configured", marker_path=marker_path, expected_uid=expected_uid)
    except OSError:
        return False


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


def migrate_skipped(user, runner=run_command, marker_path=None, expected_uid=None):
    validate_user(user)
    try:
        context = _marker_context(user, marker_path, expected_uid)
    except (FileNotFoundError, OSError) as exc:
        return {"ok": False, "migrated": False, "user": user,
                "error": str(exc) or "unsafe OOBE marker"}
    if context is None:
        return {"ok": True, "migrated": False, "user": user, "error": ""}
    marker, _info, uid, _parent, value = context
    if value == "migrated-passwordless":
        return {"ok": True, "migrated": False, "user": user, "error": ""}
    if value != "skipped":
        return {"ok": False, "migrated": False, "user": user,
                "error": "OOBE marker is not skipped"}
    result = clear_password(user, runner=runner)
    if not result["ok"]:
        result["migrated"] = False
        return result
    try:
        replaced = _replace_marker_state(
            user, "migrated-passwordless", marker_path=marker_path,
            expected_uid=uid, require_skipped=True)
    except OSError:
        replaced = False
    if not replaced:
        return {"ok": False, "migrated": False, "user": user,
                "error": "password cleared but OOBE marker migration failed"}
    return {"ok": True, "migrated": True, "user": user, "error": ""}


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-account-control")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "set-password", "clear-password", "migrate-skipped"):
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
            elif args.action == "migrate-skipped":
                result = migrate_skipped(args.user)
            else:
                source = stdin or sys.stdin
                result = set_password(args.user, source.readline(1026))
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
