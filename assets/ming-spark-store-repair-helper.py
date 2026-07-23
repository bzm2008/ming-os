#!/usr/bin/env python3
"""Authorize one local graphical user and run the fixed Spark repair command."""

import os
import re
import subprocess
import sys

try:
    import pwd as _pwd
except ImportError:  # pragma: no cover - Windows test hosts inject a lookup.
    _pwd = None


INSTALLER_PATH = "/usr/local/bin/ming-install-spark-store"
CONTROLLED_ENV = {
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LOGNAME": "root",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "USER": "root",
}


class RepairError(Exception):
    def __init__(self, code, message=""):
        self.code = str(code)
        super().__init__(message or code)


def _run(command, timeout=2):
    completed = subprocess.run(
        [str(value) for value in command],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


class SparkStoreRepairHelper:
    def __init__(
            self, runner=None, euid_getter=None, environ=None,
            passwd_lookup=None, session_reader=None, execve=None):
        self.runner = runner or _run
        self.euid_getter = euid_getter or getattr(os, "geteuid", lambda: 1)
        self.environ = dict(os.environ if environ is None else environ)
        if passwd_lookup is not None:
            self.passwd_lookup = passwd_lookup
        elif _pwd is not None:
            self.passwd_lookup = _pwd.getpwuid
        else:
            self.passwd_lookup = lambda uid: (_ for _ in ()).throw(KeyError(uid))
        self.session_reader = session_reader or self._loginctl_sessions
        self.execve = execve or os.execve

    def _call(self, command):
        try:
            code, output, error = self.runner(
                tuple(str(value) for value in command), timeout=2)
            return int(code), str(output or ""), str(error or "")
        except (OSError, subprocess.TimeoutExpired):
            return 124, "", "session query failed"

    def _loginctl_sessions(self, uid):
        code, output, _error = self._call((
            "/usr/bin/loginctl", "list-sessions", "--no-legend", "--no-pager",
        ))
        if code != 0:
            return []
        sessions = []
        for line in output.splitlines()[:64]:
            fields = line.split()
            if len(fields) < 2 or fields[1] != str(uid):
                continue
            session_id = fields[0]
            if re.fullmatch(r"[A-Za-z0-9_.-]+", session_id) is None:
                continue
            show_code, show_output, _show_error = self._call((
                "/usr/bin/loginctl", "show-session", session_id, "--no-pager",
                "--property=User", "--property=Active", "--property=Remote",
                "--property=Class", "--property=Type", "--property=Seat",
            ))
            if show_code != 0:
                continue
            values = {}
            for item in show_output.splitlines():
                field, separator, value = item.partition("=")
                if separator:
                    values[field] = value.strip()
            sessions.append(values)
        return sessions

    def authorize(self):
        try:
            if int(self.euid_getter()) != 0:
                raise ValueError("root required")
            raw_uid = self.environ.get("PKEXEC_UID", "")
            if not isinstance(raw_uid, str) or re.fullmatch(r"[1-9][0-9]*", raw_uid) is None:
                raise ValueError("invalid PKEXEC_UID")
            uid = int(raw_uid, 10)
            passwd_entry = self.passwd_lookup(uid)
            if int(getattr(passwd_entry, "pw_uid")) != uid:
                raise ValueError("passwd mismatch")
            if any(self.environ.get(name) for name in (
                    "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
                raise ValueError("ssh caller")
            for session in self.session_reader(uid) or ():
                if (
                    str(session.get("User", "")) == str(uid)
                    and str(session.get("Active", "")).casefold() == "yes"
                    and str(session.get("Remote", "")).casefold() == "no"
                    and str(session.get("Class", "")).casefold() == "user"
                    and str(session.get("Type", "")).casefold() in {"x11", "wayland"}
                    and str(session.get("Seat", "")).strip()
                ):
                    return uid
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError, OSError):
            pass
        raise RepairError(
            "E_AUTHORIZATION_FAILED",
            "an active local graphical user session is required",
        )

    def execute(self, argv):
        if list(argv):
            raise RepairError("E_REQUEST_INVALID", "arguments are not accepted")
        self.authorize()
        self.execve(INSTALLER_PATH, (INSTALLER_PATH,), dict(CONTROLLED_ENV))
        return 0


def main(argv=None, helper=None):
    helper = helper or SparkStoreRepairHelper()
    try:
        return helper.execute(sys.argv[1:] if argv is None else list(argv))
    except RepairError as error:
        print(error.code, file=sys.stderr)
        return 1
    except OSError:
        print("E_EXEC_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
