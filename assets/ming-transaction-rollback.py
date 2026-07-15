#!/usr/bin/env python3
"""Candidate-root rollback journal for interrupted staging operations."""

import datetime
import hashlib
import json
import os
import pathlib
import shutil
import stat
import uuid


class RollbackError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_dir(path):
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RollbackJournal:
    def __init__(self, transaction_dir, candidate_root):
        transaction_dir = pathlib.Path(transaction_dir)
        candidate_root = pathlib.Path(candidate_root)
        if transaction_dir.is_symlink() or not transaction_dir.is_dir():
            raise RollbackError("E_STATE_DURABILITY", "transaction journal root is unsafe")
        if candidate_root.is_symlink() or not candidate_root.is_dir():
            raise RollbackError("E_CONTENT_POLICY", "candidate root is unsafe")
        self.transaction_dir = transaction_dir.resolve()
        self.candidate_root = candidate_root.resolve()
        self.objects = self.transaction_dir / "journal" / "objects"
        self.events = self.transaction_dir / "rollback.jsonl"
        self.objects.mkdir(parents=True, exist_ok=True)
        self._captured = self._load_captures()

    def _load_captures(self):
        captured = {}
        if not self.events.exists():
            return captured
        if self.events.is_symlink():
            raise RollbackError("E_STATE_DURABILITY", "rollback log is a symlink")
        try:
            for line in self.events.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("event") == "capture":
                    captured[record["path"]] = record
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
            raise RollbackError("E_STATE_RECONCILE", f"rollback journal is invalid: {exc}") from exc
        return captured

    def _append(self, record):
        if self.events.is_symlink():
            raise RollbackError("E_STATE_DURABILITY", "rollback log is a symlink")
        record = dict(record)
        record.setdefault("schema", "ming.rollback-event.v1")
        record.setdefault("timestamp", _timestamp())
        descriptor = os.open(self.events, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
                handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        _fsync_dir(self.events.parent)

    def _relative(self, value):
        if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
            raise RollbackError("E_CONTENT_POLICY", "journal path must be relative POSIX")
        parts = value.split("/")
        if any(part in ("", ".", "..") for part in parts) or parts[0] == "home":
            raise RollbackError("E_CONTENT_POLICY", "journal path escapes or targets /home")
        return "/".join(parts)

    def _target(self, relative, *, create_parents=False):
        current = self.candidate_root
        parts = relative.split("/")
        for part in parts[:-1]:
            next_path = current / part
            try:
                metadata = next_path.lstat()
            except FileNotFoundError:
                if not create_parents:
                    current = next_path
                    continue
                try:
                    next_path.mkdir(mode=0o755)
                    metadata = next_path.lstat()
                except OSError as exc:
                    raise RollbackError("E_CONTENT_POLICY", "journal parent cannot be created safely") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise RollbackError("E_CONTENT_POLICY", "journal path has a symlink parent")
            if not stat.S_ISDIR(metadata.st_mode):
                raise RollbackError("E_CONTENT_POLICY", "journal path has a non-directory parent")
            current = next_path
        return current / parts[-1]

    def target(self, path, *, create_parents=False):
        return self._target(self._relative(path), create_parents=create_parents)

    def capture(self, path):
        relative = self._relative(path)
        if relative in self._captured:
            return self._captured[relative]
        target = self._target(relative)
        record = {
            "event": "capture",
            "path": relative,
            "kind": "missing",
            "mode": None,
            "uid": None,
            "gid": None,
            "link_target": None,
            "backup": None,
            "backup_sha256": None,
        }
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            record["mode"] = stat.S_IMODE(metadata.st_mode)
            record["uid"] = getattr(metadata, "st_uid", 0)
            record["gid"] = getattr(metadata, "st_gid", 0)
            if stat.S_ISREG(metadata.st_mode):
                record["kind"] = "file"
                backup = self.objects / uuid.uuid4().hex
                shutil.copyfile(target, backup, follow_symlinks=False)
                with backup.open("rb+") as handle:
                    os.fsync(handle.fileno())
                record["backup"] = backup.name
                record["backup_sha256"] = _sha256(backup)
            elif stat.S_ISLNK(metadata.st_mode):
                record["kind"] = "symlink"
                record["link_target"] = os.readlink(target)
            elif stat.S_ISDIR(metadata.st_mode):
                record["kind"] = "directory"
            else:
                raise RollbackError("E_CONTENT_POLICY", "special files cannot be journaled")
        self._append(record)
        self._captured[relative] = record
        return record

    @staticmethod
    def _remove(path):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @staticmethod
    def _restore_metadata(path, record, follow_symlinks=True):
        if record.get("mode") is not None and not path.is_symlink():
            os.chmod(path, record["mode"], follow_symlinks=follow_symlinks)
        if hasattr(os, "chown") and record.get("uid") is not None and os.geteuid() == 0:
            os.chown(path, record["uid"], record["gid"], follow_symlinks=follow_symlinks)

    def rollback(self, *, reason):
        for relative, record in reversed(list(self._captured.items())):
            target = self._target(self._relative(relative), create_parents=True)
            self._remove(target)
            kind = record["kind"]
            if kind == "file":
                backup = self.objects / record["backup"]
                if _sha256(backup) != record["backup_sha256"]:
                    raise RollbackError("E_ROLLBACK_STATE", f"journal backup hash mismatch: {relative}")
                shutil.copyfile(backup, target, follow_symlinks=False)
                self._restore_metadata(target, record)
            elif kind == "symlink":
                target.symlink_to(record["link_target"])
            elif kind == "directory":
                target.mkdir(parents=True, exist_ok=True)
                self._restore_metadata(target, record)
            elif kind != "missing":
                raise RollbackError("E_ROLLBACK_STATE", f"unknown journal kind: {kind}")
            _fsync_dir(target.parent)
            self._append({"event": "restore", "path": relative, "kind": kind})
        self._append({"event": "rollback-complete", "reason": str(reason)[:512]})


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
