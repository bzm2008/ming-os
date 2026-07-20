#!/usr/bin/env python3
"""Deterministically converge Spark Store's privileged package boundary."""

import argparse
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile


PRIMARY_FINGERPRINT = "9D9AA859F75024B1A1ECE16E0E41D354A29A440C"
SOURCE_LINE = (
    "deb [signed-by=/etc/apt/keyrings/ming-spark-store.gpg] "
    "https://d.spark-app.store/store /"
)
KEYRING_PATH = "/etc/apt/keyrings/ming-spark-store.gpg"
SOURCE_PATH = "/etc/apt/sources.list.d/ming-spark-store.list"
POST_INVOKE_PATH = "/etc/apt/apt.conf.d/99-ming-spark-security-converge"
ACTIVE_POLICY_PATH = "/usr/share/polkit-1/actions/store.spark-app.spark-store.policy"
VENDOR_ASC_PATHS = (
    "/opt/durapps/spark-store/bin/spark-store.asc",
    "/opt/spark-store/bin/spark-store.asc",
)
SHELL_CALLER_PATHS = (
    "/opt/spark-store/extras/shell-caller.sh",
    "/opt/spark-store/bin/extras/shell-caller.sh",
)
POLICY_SOURCE_PATHS = (
    "/opt/spark-store/extras/store.spark-app.spark-store.policy",
    "/opt/spark-store/bin/extras/store.spark-app.spark-store.policy",
)
DISABLED_ACTIVE_POLICIES = (
    "/usr/share/polkit-1/actions/store.spark-app.ssinstall.policy",
    "/usr/share/polkit-1/actions/store.spark-update-tool.policy",
)
DIVERTED_PATHS = SHELL_CALLER_PATHS + POLICY_SOURCE_PATHS + DISABLED_ACTIVE_POLICIES
GLOBAL_TRUSTED_KEYS = (
    "/etc/apt/trusted.gpg.d/spark-store.gpg",
    "/etc/apt/trusted.gpg.d/spark-store.asc",
)
NOTIFIER_MASK = "/etc/systemd/system/spark-update-notifier.service"
NOTIFIER_WANTS = "/etc/systemd/system/multi-user.target.wants/spark-update-notifier.service"

SAFE_POLICY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
          "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
          "https://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Ming OS</vendor>
  <action id="store.spark-app.spark-store">
    <description>Install or remove a Spark Store package</description>
    <message>Administrator authentication is required to change installed software</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/opt/spark-store/extras/shell-caller.sh</annotate>
  </action>
</policyconfig>
"""
SAFE_SHIM = """#!/bin/sh
exec /usr/local/sbin/ming-spark-package-helper "$@"
"""
POST_INVOKE = """// Re-assert the Spark Store boundary after package maintainer scripts.
DPkg::Post-Invoke { "/usr/local/sbin/ming-spark-security-converge enforce"; };
"""


class ConvergenceError(Exception):
    def __init__(self, code, message=""):
        self.code = str(code)
        super().__init__(message or code)


def _run(command, timeout=20):
    completed = subprocess.run(
        [str(value) for value in command],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


class SparkSecurityConverger:
    def __init__(
            self, root="/", runner=None, euid_getter=None,
            symlink_creator=None):
        self.root = pathlib.Path(root)
        self.runner = runner or _run
        self.euid_getter = euid_getter or getattr(os, "geteuid", lambda: 1)
        self.symlink_creator = symlink_creator or os.symlink

    def _path(self, absolute_path):
        return self.root / str(absolute_path).lstrip("/")

    def _call(self, command, timeout=20):
        try:
            code, output, error = self.runner(
                tuple(str(value) for value in command), timeout=timeout)
            return int(code), str(output or ""), str(error or "")
        except subprocess.TimeoutExpired as error:
            raise ConvergenceError("E_CONVERGENCE_FAILED", "command timed out") from error
        except OSError as error:
            raise ConvergenceError("E_CONVERGENCE_FAILED", "command failed") from error

    def _require_root(self):
        if int(self.euid_getter()) != 0:
            raise ConvergenceError("E_AUTHORIZATION_FAILED", "root is required")

    @staticmethod
    def _safe_regular(path):
        try:
            metadata = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)

    @staticmethod
    def _chown_root(path):
        if os.name == "nt" or not hasattr(os, "chown"):
            return
        os.chown(path, 0, 0)

    def _atomic_write(self, absolute_path, content, mode):
        target = self._path(absolute_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_symlink():
            target.unlink()
        fd, temporary_name = tempfile.mkstemp(
            prefix=".%s." % target.name, dir=str(target.parent))
        temporary = pathlib.Path(temporary_name)
        try:
            payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            self._chown_root(temporary)
            os.replace(temporary, target)
            os.chmod(target, mode)
            self._chown_root(target)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def _move_to_vendor(self, absolute_path):
        source = self._path(absolute_path)
        vendor = self._path(absolute_path + ".vendor")
        if not (source.exists() or source.is_symlink()):
            return
        vendor.parent.mkdir(parents=True, exist_ok=True)
        if vendor.exists() or vendor.is_symlink():
            return
        os.replace(source, vendor)

    def _register_diversions(self):
        for path in DIVERTED_PATHS:
            code, _output, error = self._call((
                "/usr/bin/dpkg-divert",
                "--package", "ming-os-spark-security",
                "--add", "--rename",
                "--divert", path + ".vendor",
                path,
            ), timeout=30)
            if code != 0:
                raise ConvergenceError(
                    "E_DIVERSION_FAILED", "dpkg-divert failed: %s" % error.strip())
            # A real dpkg-divert --rename performs this move.  Keeping the
            # idempotent check here also makes partial/manual installations
            # converge if the database entry already existed without rename.
            self._move_to_vendor(path)

    def _primary_fingerprint(self, key_path):
        code, output, _error = self._call((
            "/usr/bin/gpg", "--batch", "--no-options", "--with-colons",
            "--show-keys", str(key_path),
        ), timeout=30)
        if code != 0:
            raise ConvergenceError("E_VENDOR_KEY_INVALID", "unable to inspect vendor key")
        saw_primary = False
        for line in output.splitlines():
            fields = line.split(":")
            if fields[0] == "pub":
                saw_primary = True
                continue
            if fields[0] == "sub":
                saw_primary = False
                continue
            if fields[0] != "fpr" or not saw_primary:
                continue
            for value in fields[1:]:
                if re.fullmatch(r"[0-9A-Fa-f]{40}", value):
                    return value.upper()
        raise ConvergenceError("E_VENDOR_KEY_INVALID", "vendor primary fingerprint is missing")

    def _find_vendor_key(self):
        for candidate in VENDOR_ASC_PATHS:
            path = self._path(candidate)
            if self._safe_regular(path):
                return path, True
        keyring = self._path(KEYRING_PATH)
        if self._safe_regular(keyring):
            return keyring, False
        raise ConvergenceError("E_VENDOR_KEY_INVALID", "vendor ASC key is missing")

    def _install_keyring(self, source, armored):
        fingerprint = self._primary_fingerprint(source)
        if fingerprint != PRIMARY_FINGERPRINT:
            raise ConvergenceError("E_VENDOR_KEY_INVALID", "vendor primary fingerprint mismatch")
        target = self._path(KEYRING_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        if armored:
            temporary = target.with_name(".%s.new" % target.name)
            try:
                temporary.unlink()
            except OSError:
                pass
            code, _output, error = self._call((
                "/usr/bin/gpg", "--batch", "--yes", "--no-options",
                "--dearmor", "--output", str(temporary), str(source),
            ), timeout=30)
            if code != 0 or not self._safe_regular(temporary):
                raise ConvergenceError(
                    "E_VENDOR_KEY_INVALID", "vendor key dearmor failed: %s" % error.strip())
            os.chmod(temporary, 0o644)
            self._chown_root(temporary)
            os.replace(temporary, target)
        os.chmod(target, 0o644)
        self._chown_root(target)

    def _remove_path(self, absolute_path):
        path = self._path(absolute_path)
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            pass

    def _install_policy_boundary(self):
        for path in SHELL_CALLER_PATHS:
            self._atomic_write(path, SAFE_SHIM, 0o755)
        for path in POLICY_SOURCE_PATHS:
            self._atomic_write(path, SAFE_POLICY, 0o644)
        for path in DISABLED_ACTIVE_POLICIES:
            self._remove_path(path)
        self._atomic_write(ACTIVE_POLICY_PATH, SAFE_POLICY, 0o644)

    def _mask_notifier(self):
        self._call((
            "/usr/bin/systemctl", "disable", "--now",
            "spark-update-notifier.service",
        ), timeout=30)
        self._remove_path(NOTIFIER_WANTS)
        mask = self._path(NOTIFIER_MASK)
        if mask.is_symlink():
            try:
                if os.readlink(mask) == "/dev/null":
                    return
            except OSError:
                pass
        self._remove_path(NOTIFIER_MASK)
        mask.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.symlink_creator("/dev/null", mask)
        except FileExistsError:
            pass
        except OSError as error:
            raise ConvergenceError("E_CONVERGENCE_FAILED", "unable to mask notifier") from error

    def _converge(self, key_source=None, armored=None):
        self._require_root()
        if key_source is None:
            key_source, armored = self._find_vendor_key()
        self._install_keyring(pathlib.Path(key_source), bool(armored))
        self._register_diversions()
        self._install_policy_boundary()
        self._atomic_write(SOURCE_PATH, SOURCE_LINE + "\n", 0o644)
        self._atomic_write(POST_INVOKE_PATH, POST_INVOKE, 0o644)
        for path in GLOBAL_TRUSTED_KEYS:
            self._remove_path(path)
        self._mask_notifier()

    def enforce(self):
        self._converge()

    def prepare(self, deb_path):
        self._require_root()
        deb = pathlib.Path(deb_path)
        if not self._safe_regular(deb):
            raise ConvergenceError("E_PACKAGE_FAILED", "Spark Store DEB is unavailable")
        extraction_parent = self._path("/var/lib/ming-package-installer")
        extraction_parent.mkdir(parents=True, exist_ok=True)
        extraction = pathlib.Path(tempfile.mkdtemp(
            prefix="spark-key-", dir=str(extraction_parent)))
        try:
            code, _output, error = self._call((
                "/usr/bin/dpkg-deb", "--extract", str(deb), str(extraction),
            ), timeout=90)
            if code != 0:
                raise ConvergenceError(
                    "E_PACKAGE_FAILED", "unable to extract Spark Store key: %s" % error.strip())
            key_source = None
            for candidate in VENDOR_ASC_PATHS:
                path = extraction / candidate.lstrip("/")
                if self._safe_regular(path):
                    key_source = path
                    break
            if key_source is None:
                raise ConvergenceError("E_VENDOR_KEY_INVALID", "Spark Store DEB has no vendor ASC")
            self._converge(key_source=key_source, armored=True)
        finally:
            shutil.rmtree(extraction, ignore_errors=True)


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-spark-security-converge")
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--deb", required=True)
    actions.add_parser("enforce")
    return parser


def main(argv=None, converger=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    converger = converger or SparkSecurityConverger()
    try:
        if args.action == "prepare":
            converger.prepare(args.deb)
        else:
            converger.enforce()
    except ConvergenceError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
