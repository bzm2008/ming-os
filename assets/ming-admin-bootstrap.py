#!/usr/bin/env python3
"""One-shot local administrator bootstrap for installed Ming OS systems."""

import argparse
import contextlib
import json
import os
import re
import stat
import subprocess
import sys

try:
    import fcntl
except ImportError:  # Windows contract tests
    fcntl = None

try:
    import pwd
except ImportError:  # Windows contract tests
    pwd = None


USER_PATTERN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
LOCK_PATH = "/run/lock/ming-admin-bootstrap.lock"
ZENITY = "/usr/bin/zenity"
PASSWORD_PROMPT_TIMEOUT_SECONDS = 300


def run_command(command, input_text=None):
    try:
        result = subprocess.run(
            command, input=input_text, capture_output=True, text=True,
            errors="replace", timeout=PASSWORD_PROMPT_TIMEOUT_SECONDS)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def caller_matches_user(user, environ=None, uid_lookup=None):
    environ = os.environ if environ is None else environ
    value = environ.get("PKEXEC_UID")
    if not value:
        return False
    uid_lookup = uid_lookup or (pwd.getpwuid if pwd is not None else None)
    if uid_lookup is None:
        return False
    try:
        uid = int(value)
        account = uid_lookup(uid)
        return uid >= 1000 and account.pw_uid == uid and account.pw_name == user
    except (KeyError, TypeError, ValueError):
        return False


def primary_local_user(user):
    if pwd is None:
        return False
    try:
        account = pwd.getpwnam(user)
        regular = sorted(
            item.pw_uid for item in pwd.getpwall()
            if 1000 <= item.pw_uid < 60000 and item.pw_name != "nobody")
        return bool(regular and account.pw_uid == regular[0])
    except (KeyError, TypeError, ValueError):
        return False


def usable_administrator_exists(runner=run_command):
    rc, output, _error = runner(["getent", "group", "sudo"])
    if rc != 0:
        return False
    fields = output.split(":", 3)
    members = fields[3].split(",") if len(fields) == 4 else []
    for member in filter(None, members):
        status_rc, status, _status_error = runner(["passwd", "-S", member])
        values = status.split()
        if status_rc == 0 and len(values) >= 2 and values[1] == "P":
            return True
    return False


def administrator_status(user, runner=run_command):
    if not USER_PATTERN.fullmatch(user or "") or user == "root":
        return {"ok": False, "ready": False, "user": user, "error": "invalid account"}
    status_rc, status, status_error = runner(["passwd", "-S", user])
    groups_rc, groups, groups_error = runner(["id", "-nG", user])
    password_set = status_rc == 0 and len(status.split()) >= 2 and status.split()[1] == "P"
    administrator = groups_rc == 0 and "sudo" in set(groups.split())
    return {"ok": status_rc == 0 and groups_rc == 0,
            "ready": password_set and administrator, "password_set": password_set,
            "administrator": administrator, "user": user,
            "error": "" if status_rc == 0 and groups_rc == 0 else (status_error or groups_error)}


def collect_interactive_password(runner=run_command, environ=None, executable=os.path.isfile):
    """Collect the password inside the privileged process, never over caller stdin.

    This blocks silent background submission. X11 cannot protect keystrokes once
    the entire desktop session is compromised; first boot must still run only
    trusted image code until administrator setup completes.
    """
    environ = os.environ if environ is None else environ
    if not environ.get("DISPLAY") or not executable(ZENITY):
        raise ValueError("a visible local password dialog is required")
    prompts = (
        ("Ming OS 管理员初始化（1/2）", "请输入新的管理员密码（第 1 次）"),
        ("Ming OS 管理员初始化（2/2）", "请再次输入同一密码（第 2 次）"),
    )
    values = []
    for title, prompt in prompts:
        rc, output, error = runner([
            ZENITY, "--password", "--title=%s" % title,
            "--text=%s" % prompt, "--width=440",
        ])
        if rc != 0:
            raise ValueError(error or "password setup was cancelled")
        values.append(output.rstrip("\r\n"))
    if not values[0] or values[0] != values[1]:
        raise ValueError("passwords do not match")
    return values[0]


@contextlib.contextmanager
def administrator_lock(path=LOCK_PATH):
    if fcntl is None:
        raise RuntimeError("administrator lock is unavailable")
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise RuntimeError("administrator lock is not root-owned and regular")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def bootstrap_administrator(user, runner=run_command, password_reader=collect_interactive_password):
    if not USER_PATTERN.fullmatch(user or "") or user == "root":
        return {"ok": False, "error": "invalid account", "user": user}
    if usable_administrator_exists(runner=runner):
        return {"ok": False, "error": "a usable administrator already exists", "user": user}
    try:
        password = password_reader()
    except (OSError, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "user": user}
    password = (password or "").rstrip("\r\n")
    if (not password or "\n" in password or "\r" in password
            or ":" in password or "\x00" in password
            or len(password.encode("utf-8")) > 1024):
        return {"ok": False, "error": "password must be non-empty", "user": user}

    rc, _output, error = runner(["chpasswd"], "%s:%s\n" % (user, password))
    password = ""
    if rc != 0:
        return {"ok": False, "error": error or "password update failed", "user": user}
    for command in (
            ["gpasswd", "-d", user, "nopasswdlogin"],
            ["usermod", "-aG", "sudo", user]):
        rc, _output, error = runner(command)
        if rc != 0 and command[1] != "-d":
            return {"ok": False, "error": error or "administrator group update failed",
                    "user": user}
    status_rc, status, status_error = runner(["passwd", "-S", user])
    groups_rc, groups, groups_error = runner(["id", "-nG", user])
    password_set = status_rc == 0 and len(status.split()) >= 2 and status.split()[1] == "P"
    is_admin = groups_rc == 0 and "sudo" in set(groups.split())
    if not password_set or not is_admin:
        return {"ok": False,
                "error": status_error or groups_error or "administrator readback failed",
                "user": user}
    return {"ok": True, "password_set": True, "administrator": True,
            "user": user, "error": ""}


def main(argv=None, stdout=None, lock_factory=administrator_lock, effective_uid=None):
    parser = argparse.ArgumentParser(prog="ming-admin-bootstrap")
    parser.add_argument("action", nargs="?", choices=("bootstrap", "status"), default="bootstrap")
    parser.add_argument("--user", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    stdout = stdout or sys.stdout
    if args.action == "status":
        result = administrator_status(args.user)
    elif (os.geteuid() if effective_uid is None else effective_uid) != 0:
        result = {"ok": False, "error": "authorization required"}
    elif not caller_matches_user(args.user) or not primary_local_user(args.user):
        result = {"ok": False, "error": "only the primary active local user may initialize administration"}
    else:
        try:
            with lock_factory():
                result = bootstrap_administrator(args.user)
        except (BlockingIOError, OSError, RuntimeError) as exc:
            result = {"ok": False, "error": "administrator setup is already running: %s" % exc}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
