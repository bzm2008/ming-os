#!/usr/bin/env python3
"""Privileged Ming OS account password control."""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

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


def retire_skipped_marker(user, marker_path=None):
    marker = pathlib.Path(marker_path) if marker_path is not None else oobe_marker_for_user(user)
    if marker is None:
        return True
    try:
        if marker.read_text(encoding="utf-8").strip() != "skipped":
            return True
        stat_result = marker.stat()
        temporary = marker.with_name(marker.name + ".configured")
        temporary.write_text("configured\n", encoding="utf-8")
        try:
            os.chmod(temporary, stat_result.st_mode & 0o777)
            os.chown(temporary, stat_result.st_uid, stat_result.st_gid)
        except (AttributeError, OSError):
            pass
        os.replace(temporary, marker)
        return marker.read_text(encoding="utf-8").strip() == "configured"
    except FileNotFoundError:
        return True
    except OSError:
        try:
            marker.unlink()
            return not marker.exists()
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
