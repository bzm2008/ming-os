#!/usr/bin/env python3
"""Validate public release receipts and scan public release material.

This tool intentionally has no decryption, signing, upload, or remote execution
surface.  It is the small public-boundary checker used before a release is
published. Unknown regular binaries are accepted only when they are
marker-free; payload and ISO blobs do not belong under the scan root. The root
is local trust material, and scan deadlines are cooperative between bounded
system calls rather than hard interruption of a blocking OS call. Bundle
replacement uses POSIX directory descriptors when available; Windows uses a
validated path fallback and fails closed when a parent swap is observed, but
cannot eliminate every race between path checks and the OS rename primitive.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Mapping


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_READY = 78
EXIT_UNREACHABLE = 69

RECEIPT_FORMAT = "ming-release-vault-receipt-v1"
REQUIRED_RECEIPT_FIELDS = frozenset(
    {
        "format",
        "bundle_id",
        "generation",
        "primary_fingerprint",
        "signing_fingerprint",
        "bundle_sha256",
        "bundle_bytes",
        "public_keyring_sha256",
        "key_policy_sha256",
        "encryption_format",
        "created_at",
        "nas_object",
        "status",
    }
)

_SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")
_FINGERPRINT_RE = re.compile(r"(?:[0-9A-F]{40}|[0-9A-F]{64})\Z")
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)

# The public scan root contains trust material, receipts, signatures, and
# hashes; payload and ISO blobs are outside this boundary.
MAX_FILE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
MARKER_TAIL_BYTES = 256
MAX_READ_SECONDS = 5.0
MAX_SCAN_SECONDS = 30.0
MAX_SCAN_ENTRIES = 10_000
MAX_SCAN_DEPTH = 32
MAX_RECEIPT_BYTES = 1 * 1024 * 1024
MAX_RECEIPT_READ_SECONDS = 5.0
MAX_SIDECAR_BYTES = 4 * 1024
MAX_BUNDLE_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_ENTRIES = 10_000
MAX_BUNDLE_DEPTH = 32
MAX_BUNDLE_SECONDS = 30.0
AGE_RUN_TIMEOUT_SECONDS = 60

# Passwords are intentionally not an input channel for this command.  These
# names are rejected before spawning age and are removed from the child env.
_PASSWORD_ENV_NAMES = frozenset(
    {
        "MING_RELEASE_PASSWORD",
        "MING_RELEASE_PASSPHRASE",
        "AGE_PASSWORD",
        "AGE_PASSPHRASE",
        "PASSPHRASE",
        "PASSWORD",
        "PASSWD",
    }
)

_PATH_MARKERS = (
    "secret",
    "password",
    "passwd",
    "token",
    "known_hosts",
    "known-hosts",
    "private",
)
_SSH_PRIVATE_NAMES = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_ed448",
        "id_x25519",
        "id_xmss",
        "identity",
        "ssh_host_rsa_key",
        "ssh_host_dsa_key",
        "ssh_host_ecdsa_key",
        "ssh_host_ed25519_key",
    }
)
_CONTENT_MARKERS = (
    b"private key",
    b".env",
    b".age",
    b"secret",
    b"password",
    b"passwd",
    b"token",
    b"known_hosts",
)
_PRIVATE_KEY_HEADER_RE = re.compile(
    rb"-----BEGIN[ -]+(?:[A-Z0-9][A-Z0-9 -]*[ -]+)?"
    rb"(?:PRIVATE|SECRET) KEY(?:[ -]+BLOCK)?-----",
    re.IGNORECASE,
)
_PRIVATE_KEY_TEXT_RE = re.compile(rb"private[ _-]+key", re.IGNORECASE)
_PRIVATE_PATH_RE = re.compile(rb"(?:^|[/\\])private(?:[/\\]|$)")
_SSH_PRIVATE_NAME_RE = re.compile(
    rb"(?<![A-Za-z0-9])id_(?:rsa|dsa|ecdsa|ed25519|ed448|x25519|xmss)(?!(?:[A-Za-z0-9]|\.pub))"
)


class ReleaseVaultError(ValueError):
    """A sanitized, user-facing release-boundary failure."""

    def __init__(self, error_code: str, message: str, details=None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details


def _json_dump(value: Mapping) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _exit_code_for(error_code: str) -> int:
    if error_code == "E_VAULT_UNREACHABLE":
        return EXIT_UNREACHABLE
    if error_code == "E_USAGE":
        return EXIT_USAGE
    return EXIT_NOT_READY


def emit_ok(value=None, **fields) -> int:
    """Emit one stable JSON success object and return its process code."""

    payload = {"status": "ok"}
    if value is not None:
        if not isinstance(value, Mapping):
            raise TypeError("emit_ok payload must be a mapping")
        payload.update(value)
    payload.update(fields)
    _json_dump(payload)
    return EXIT_OK


def emit_error(error_code: str, message: str | None = None, details=None) -> int:
    """Emit one sanitized JSON error object and return its process code."""

    payload = {"status": "error", "error_code": error_code}
    if message:
        payload["message"] = message
    if details is not None:
        payload["details"] = details
    _json_dump(payload)
    return _exit_code_for(error_code)


def _require_string(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    if any(ord(char) < 0x20 for char in value):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    return value


def _require_sha256(value, field: str) -> str:
    value = _require_string(value, field)
    if _SHA256_RE.fullmatch(value) is None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    return value


def _require_fingerprint(value, field: str) -> str:
    value = _require_string(value, field)
    if _FINGERPRINT_RE.fullmatch(value) is None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    return value


def _require_opaque_id(value, field: str) -> str:
    value = _require_string(value, field)
    if _OPAQUE_ID_RE.fullmatch(value) is None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    return value


def _require_rfc3339(value, field: str) -> str:
    value = _require_string(value, field)
    if _RFC3339_RE.fullmatch(value) is None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"receipt field {field} is invalid")
    return value


def validate_receipt(value: dict) -> dict:
    """Validate and return a copy of the public release receipt.

    The exact top-level field set is deliberate: a receipt is public metadata,
    not an extensible carrier for host, NAS, credential, or key material.
    """

    if not isinstance(value, dict):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt must be a JSON object")
    fields = set(value)
    if fields != REQUIRED_RECEIPT_FIELDS:
        unknown = sorted(fields - REQUIRED_RECEIPT_FIELDS)
        missing = sorted(REQUIRED_RECEIPT_FIELDS - fields)
        detail = {"unknown": unknown, "missing": missing}
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt fields are invalid", detail)

    if value["format"] != RECEIPT_FORMAT:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt format is invalid")
    bundle_id = _require_opaque_id(value["bundle_id"], "bundle_id")
    generation = value["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt generation is invalid")
    primary = _require_fingerprint(value["primary_fingerprint"], "primary_fingerprint")
    signing = _require_fingerprint(value["signing_fingerprint"], "signing_fingerprint")
    bundle_sha = _require_sha256(value["bundle_sha256"], "bundle_sha256")
    bundle_bytes = value["bundle_bytes"]
    if isinstance(bundle_bytes, bool) or not isinstance(bundle_bytes, int) or bundle_bytes < 0:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt bundle_bytes is invalid")
    keyring_sha = _require_sha256(value["public_keyring_sha256"], "public_keyring_sha256")
    policy_sha = _require_sha256(value["key_policy_sha256"], "key_policy_sha256")
    if value["encryption_format"] != "age-v1":
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt encryption_format is invalid")
    created_at = _require_rfc3339(value["created_at"], "created_at")
    nas_object = _require_opaque_id(value["nas_object"], "nas_object")
    if value["status"] != "verified":
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt status is invalid")

    return {
        "format": RECEIPT_FORMAT,
        "bundle_id": bundle_id,
        "generation": generation,
        "primary_fingerprint": primary,
        "signing_fingerprint": signing,
        "bundle_sha256": bundle_sha,
        "bundle_bytes": bundle_bytes,
        "public_keyring_sha256": keyring_sha,
        "key_policy_sha256": policy_sha,
        "encryption_format": "age-v1",
        "created_at": created_at,
        "nas_object": nas_object,
        "status": "verified",
    }


def _strict_object_pairs(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = item
    return result


def _load_json(path: pathlib.Path) -> dict:
    path = pathlib.Path(path)
    try:
        before = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt path is unsafe")
    if before.st_size > MAX_RECEIPT_BYTES:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt is too large")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
    try:
        try:
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
        if (
            stat.S_ISLNK(opened.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(before, opened)
            or opened.st_size != before.st_size
            or opened.st_size > MAX_RECEIPT_BYTES
            or _metadata_changed(before, opened)
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt changed during read")

        started = time.monotonic()
        chunks = []
        total = 0
        while True:
            if time.monotonic() - started > MAX_RECEIPT_READ_SECONDS:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt read timed out")
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RECEIPT_BYTES:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt is too large")
            chunks.append(chunk)

        try:
            final = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
        if (
            stat.S_ISLNK(final.st_mode)
            or not stat.S_ISREG(final.st_mode)
            or not os.path.samestat(opened, final)
            or final.st_size != opened.st_size
            or final.st_size != total
            or _metadata_changed(opened, final)
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt changed during read")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt JSON is invalid") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt JSON is invalid") from exc
    return value


def _sensitive_component(component: str) -> str | None:
    lowered = component.lower()
    if ".env" in lowered:
        return ".env path"
    if ".age" in lowered:
        return ".age path"
    if lowered in _SSH_PRIVATE_NAMES or (
        lowered.startswith("id_") and not lowered.endswith(".pub")
    ):
        return "SSH private-key path"
    for marker in _PATH_MARKERS:
        if marker in lowered:
            return f"sensitive {marker} path"
    return None


def _sensitive_content(content: bytes) -> str | None:
    """Return a marker reason for text and binary bytes alike."""

    lowered = content.lower()
    if _PRIVATE_KEY_HEADER_RE.search(content):
        return "private-key marker"
    if _PRIVATE_KEY_TEXT_RE.search(content):
        return "private-key marker"
    if _SSH_PRIVATE_NAME_RE.search(content):
        return "SSH private-key marker"
    for marker in _CONTENT_MARKERS:
        if marker in lowered:
            return "sensitive content marker"
    if _PRIVATE_PATH_RE.search(lowered):
        return "private path marker"
    return None


def _not_ready(message: str):
    raise ReleaseVaultError("E_RELEASE_NOT_READY", message)


def _check_deadline(deadline: float | None):
    if deadline is not None and time.monotonic() >= deadline:
        _not_ready("public scan deadline exceeded")


def _same_directory_snapshot(before, after) -> bool:
    return (
        os.path.samestat(before, after)
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _metadata_changed(before, after) -> bool:
    if before.st_mtime_ns != after.st_mtime_ns:
        return True
    before_birth = getattr(before, "st_birthtime_ns", None)
    after_birth = getattr(after, "st_birthtime_ns", None)
    if before_birth is not None and after_birth is not None:
        if before_birth != after_birth:
            return True
        # Windows fstat may expose creation time through ctime while path
        # stat exposes a different copy timestamp. Birthtime is the stable
        # equivalent there; POSIX ctime remains a metadata-change signal.
        if os.name == "nt":
            return False
    return before.st_ctime_ns != after.st_ctime_ns


def _iter_public_entries(root: pathlib.Path, *, deadline=None, budget=None):
    """Yield regular-file entries without following symlinked directories."""

    root_path = os.fspath(root)
    if budget is None:
        budget = {"entries": 0}
    _check_deadline(deadline)
    try:
        root_stat = os.lstat(root_path)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "public tree is missing or invalid") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        _not_ready("public tree is missing or invalid")

    def walk(directory, relative_prefix):
        _check_deadline(deadline)
        try:
            before = os.stat(directory, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                _not_ready("public tree changed during scan")
            with os.scandir(directory) as iterator:
                entries = []
                while True:
                    _check_deadline(deadline)
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    except OSError as exc:
                        raise ReleaseVaultError(
                            "E_RELEASE_NOT_READY", "public tree could not be enumerated"
                        ) from exc
                    budget["entries"] += 1
                    if budget["entries"] > MAX_SCAN_ENTRIES:
                        _not_ready("public tree has too many entries")
                    entries.append(entry)
            after = os.stat(directory, follow_symlinks=False)
            if not _same_directory_snapshot(before, after):
                _not_ready("public tree changed during scan")
        except ReleaseVaultError:
            raise
        except OSError as exc:
            raise ReleaseVaultError(
                "E_RELEASE_NOT_READY", "public tree could not be enumerated"
            ) from exc

        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            _check_deadline(deadline)
            relative = relative_prefix / entry.name
            if len(relative.parts) > MAX_SCAN_DEPTH:
                _not_ready("public tree exceeds depth limit")
            try:
                if entry.is_symlink():
                    yield relative, entry, True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    yield from walk(entry.path, relative)
                    continue
                if entry.is_file(follow_symlinks=False):
                    yield relative, entry, False
                    continue
            except OSError as exc:
                raise ReleaseVaultError(
                    "E_RELEASE_NOT_READY", "public tree could not be enumerated"
                ) from exc
            _not_ready("public tree contains unsupported file")

        _check_deadline(deadline)
        try:
            final = os.stat(directory, follow_symlinks=False)
        except OSError as exc:
            raise ReleaseVaultError(
                "E_RELEASE_NOT_READY", "public tree could not be enumerated"
            ) from exc
        if not _same_directory_snapshot(before, final):
            _not_ready("public tree changed during scan")

    yield from walk(root_path, pathlib.Path())


def _scan_public_file(entry, *, deadline=None) -> str | None:
    """Stream one regular file with size and identity checks."""

    _check_deadline(deadline)
    try:
        before = os.stat(entry.path, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "public file could not be read") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _not_ready("public file changed during scan")
    if before.st_size > MAX_FILE_BYTES:
        _not_ready("public file is too large")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entry.path, flags)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "public file could not be read") from exc

    try:
        try:
            after = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "public file could not be read") from exc
        if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
            _not_ready("public file changed during scan")
        if (
            not os.path.samestat(before, after)
            or after.st_size != before.st_size
            or _metadata_changed(before, after)
        ):
            _not_ready("public file changed during scan")
        if after.st_size > MAX_FILE_BYTES:
            _not_ready("public file is too large")

        started = time.monotonic()
        tail = b""
        total = 0
        detected_reason = None
        while True:
            _check_deadline(deadline)
            if time.monotonic() - started > MAX_READ_SECONDS:
                _not_ready("public file read timed out")
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "public file could not be read") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                _not_ready("public file is too large")
            combined = tail + chunk
            reason = _sensitive_content(combined)
            if reason and detected_reason is None:
                detected_reason = reason
            tail = combined[-MARKER_TAIL_BYTES:]
        _check_deadline(deadline)
        try:
            final = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "public file could not be read") from exc
        if (
            stat.S_ISLNK(final.st_mode)
            or not stat.S_ISREG(final.st_mode)
            or not os.path.samestat(before, final)
            or final.st_size != after.st_size
            or final.st_size != total
            or _metadata_changed(after, final)
        ):
            _not_ready("public file changed during scan")
        return detected_reason
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def scan_public_tree(root: pathlib.Path) -> dict:
    """Scan a public-material tree and return a sanitized summary.

    Enumeration and file reads fail closed. Regular files are opened without
    following symlinks where supported and compared with their directory-entry
    identity to catch replacement races on platforms without ``O_NOFOLLOW``.
    """

    started = time.monotonic()
    deadline = started + MAX_SCAN_SECONDS
    budget = {"entries": 0}
    findings = []
    files_scanned = 0
    for relative, entry, is_symlink in _iter_public_entries(
        pathlib.Path(root), deadline=deadline, budget=budget
    ):
        _check_deadline(deadline)
        relative_parts = relative.parts
        relative_name = relative.as_posix()
        if is_symlink:
            findings.append({"path": relative_name, "reason": "symlink"})
            continue
        files_scanned += 1
        reason = next(
            (reason for component in relative_parts if (reason := _sensitive_component(component))),
            None,
        )
        if reason is None:
            reason = _scan_public_file(entry, deadline=deadline)
        if reason:
            findings.append({"path": relative_name, "reason": reason})

    if findings:
        raise ReleaseVaultError(
            "E_SECRET_EXPOSURE",
            "public tree contains sensitive material",
            {"findings": findings},
        )
    return {"files_scanned": files_scanned}


# ---------------------------------------------------------------------------
# Local encrypted recovery bundle

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _absolute_path(value, field: str) -> pathlib.Path:
    """Return a lexical absolute path without following symlinks."""

    try:
        path = pathlib.Path(value)
    except (TypeError, ValueError) as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} is invalid") from exc
    if not path.is_absolute():
        path = pathlib.Path.cwd() / path
    return pathlib.Path(os.path.abspath(os.fspath(path)))


def _path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Compare normalized paths without resolving symlink targets."""

    try:
        common = os.path.commonpath([os.fspath(path), os.fspath(root)])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _symlink_component(path: pathlib.Path) -> pathlib.Path | None:
    """Return the first symlink component, if one exists."""

    path = _absolute_path(path, "path")
    current = pathlib.Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            # Missing descendants cannot be symlinks yet.  Existing parents
            # have already been checked, and callers may create descendants.
            continue
        except OSError as exc:
            raise ReleaseVaultError("E_VAULT_PERMISSION", "path could not be checked") from exc
        if stat.S_ISLNK(info.st_mode):
            return current
    return None


def _require_directory(path: pathlib.Path, field: str) -> pathlib.Path:
    path = _absolute_path(path, field)
    if _symlink_component(path) is not None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} contains a symlink")
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} is not a directory")
    return path


def _require_regular_file(path: pathlib.Path, field: str) -> pathlib.Path:
    path = _absolute_path(path, field)
    if _symlink_component(path) is not None:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} contains a symlink")
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", f"{field} is not a regular file")
    return path


def _configured_vault() -> pathlib.Path:
    raw = os.environ.get("MING_RELEASE_VAULT")
    if not raw:
        raise ReleaseVaultError("E_VAULT_NOT_CONFIGURED", "MING_RELEASE_VAULT is required")
    path = _absolute_path(raw, "MING_RELEASE_VAULT")
    if _symlink_component(path) is not None:
        raise ReleaseVaultError("E_VAULT_PERMISSION", "MING_RELEASE_VAULT contains a symlink")
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_NOT_CONFIGURED", "MING_RELEASE_VAULT is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ReleaseVaultError("E_VAULT_NOT_CONFIGURED", "MING_RELEASE_VAULT is not a directory")
    return path


def _reject_repository_path(path: pathlib.Path, field: str):
    if _path_within(path, REPO_ROOT):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} must be outside the Git worktree")


def _prepare_output_path(value, vault: pathlib.Path, field: str) -> pathlib.Path:
    path = _absolute_path(value, field)
    if not _path_within(path, vault):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} must be under MING_RELEASE_VAULT")
    _reject_repository_path(path, field)
    if _symlink_component(path) is not None:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} contains a symlink")
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} could not be checked") from exc
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} is a symlink")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unavailable") from exc
    if not _path_within(parent, vault) or _symlink_component(parent) is not None:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unsafe")
    return path


def _check_bundle_deadline(deadline: float | None):
    if deadline is not None and time.monotonic() >= deadline:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "input enumeration deadline exceeded")


def _bundle_directory_snapshot(before, after) -> bool:
    return _same_file_metadata(before, after)


def _same_directory_identity(before, after) -> bool:
    """Compare directory identity without treating our rename's mtime as a race."""

    if not os.path.samestat(before, after):
        return False
    before_birth = getattr(before, "st_birthtime_ns", None)
    after_birth = getattr(after, "st_birthtime_ns", None)
    if before_birth is not None and after_birth is not None:
        return before_birth == after_birth
    return True


def _bundle_entries(root: pathlib.Path, *, deadline=None, budget=None):
    """Collect regular files/directories with deterministic relative names."""

    root = _require_directory(root, "input")
    if deadline is None:
        deadline = time.monotonic() + MAX_BUNDLE_SECONDS
    if budget is None:
        budget = {"entries": 0}
    entries = []
    pending = [(root, pathlib.PurePosixPath())]
    while pending:
        _check_bundle_deadline(deadline)
        directory, relative_prefix = pending.pop()
        try:
            before = os.lstat(directory)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input directory changed during enumeration")
            children = []
            with os.scandir(directory) as iterator:
                while True:
                    _check_bundle_deadline(deadline)
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    except OSError as exc:
                        raise ReleaseVaultError(
                            "E_RELEASE_NOT_READY", "input could not be enumerated"
                        ) from exc
                    budget["entries"] += 1
                    if budget["entries"] > MAX_BUNDLE_ENTRIES:
                        raise ReleaseVaultError("E_RELEASE_NOT_READY", "input contains too many entries")
                    children.append(entry)
            after = os.lstat(directory)
            if not _bundle_directory_snapshot(before, after):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input directory changed during enumeration")
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "input could not be enumerated") from exc
        children.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        for entry in children:
            _check_bundle_deadline(deadline)
            relative = relative_prefix / entry.name
            if len(relative.parts) > MAX_BUNDLE_DEPTH:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input exceeds depth limit")
            if any(part in ("", ".", "..") for part in relative.parts):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input contains an unsafe path")
            path = pathlib.Path(entry.path)
            if not _path_within(_absolute_path(path, "input entry"), root):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input entry is outside the input root")
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input changed during enumeration") from exc
            if stat.S_ISLNK(info.st_mode):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input contains a symlink")
            if stat.S_ISDIR(info.st_mode):
                entries.append((relative, path, "directory", info))
                pending.append((path, relative))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input contains an unsupported file")
            if info.st_size > MAX_BUNDLE_FILE_BYTES:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file is too large")
            entries.append((relative, path, "file", info))
        try:
            final = os.lstat(directory)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "input could not be enumerated") from exc
        if not _bundle_directory_snapshot(before, final):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "input directory changed during enumeration")
    entries.sort(key=lambda item: (item[0].as_posix().casefold(), item[0].as_posix()))
    return root, entries


def _same_file_metadata(before, after) -> bool:
    return (
        os.path.samestat(before, after)
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        # Windows may assign a fresh ctime while opening a file; birthtime is
        # the stable identity signal there. POSIX retains ctime semantics via
        # the shared metadata helper.
        and not _metadata_changed(before, after)
    )


class _DeadlineReader:
    def __init__(self, source, deadline, state, limit):
        self.source = source
        self.deadline = deadline
        self.state = state
        self.limit = limit

    def read(self, size=-1):
        _check_bundle_deadline(self.deadline)
        data = self.source.read(size)
        self.state["bytes"] += len(data)
        if self.state["bytes"] > self.limit:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "input bundle is too large")
        _check_bundle_deadline(self.deadline)
        return data


def _build_deterministic_tar(root: pathlib.Path) -> bytes:
    """Build a normalized tar stream; no source metadata leaks into it."""

    deadline = time.monotonic() + MAX_BUNDLE_SECONDS
    root, entries = _bundle_entries(root, deadline=deadline)
    stream = io.BytesIO()
    total_bytes = 0
    try:
        archive = tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "bundle archive could not be created") from exc
    try:
        for relative, path, kind, initial in entries:
            _check_bundle_deadline(deadline)
            name = relative.as_posix()
            info = tarfile.TarInfo(name)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.pax_headers = {}
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                info.size = 0
                archive.addfile(info)
                continue

            info.type = tarfile.REGTYPE
            info.mode = 0o600
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(os.fspath(path), flags)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file could not be opened") from exc
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or not _same_file_metadata(initial, opened):
                    raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file changed during archive")
                path_opened = os.lstat(path)
                if stat.S_ISLNK(path_opened.st_mode) or not _same_file_metadata(initial, path_opened):
                    raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file changed during archive")
                if opened.st_size > MAX_BUNDLE_FILE_BYTES:
                    raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file is too large")
                info.size = opened.st_size
                total_bytes += opened.st_size
                if total_bytes > MAX_BUNDLE_BYTES:
                    raise ReleaseVaultError("E_RELEASE_NOT_READY", "input bundle is too large")
                state = {"bytes": 0}
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    archive.addfile(info, _DeadlineReader(source, deadline, state, MAX_BUNDLE_BYTES))
                _check_bundle_deadline(deadline)
                final = os.fstat(descriptor)
                path_final = os.lstat(path)
                if (
                    not _same_file_metadata(opened, final)
                    or stat.S_ISLNK(path_final.st_mode)
                    or not _same_file_metadata(final, path_final)
                ):
                    raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file changed during archive")
            except ReleaseVaultError:
                raise
            except (OSError, tarfile.TarError) as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "input file could not be archived") from exc
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    finally:
        try:
            archive.close()
        except (OSError, tarfile.TarError) as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "bundle archive could not be finalized") from exc
    return stream.getvalue()


def _hash_regular_file(
    path: pathlib.Path,
    *,
    deadline: float | None = None,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    if max_bytes is None:
        max_bytes = MAX_BUNDLE_BYTES
    if max_bytes <= 0:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file size limit is invalid")
    if deadline is not None and time.monotonic() >= deadline:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file hash deadline exceeded")
    path = _require_regular_file(path, "file")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file could not be read") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file is not a regular file")
    if before.st_size > max_bytes:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file is too large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "file could not be read") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        initial = os.fstat(descriptor)
        path_opened = os.lstat(path)
        if (
            stat.S_ISLNK(path_opened.st_mode)
            or not _same_file_metadata(before, initial)
            or not _same_file_metadata(before, path_opened)
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "file changed during read")
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "file hash deadline exceeded")
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "file could not be read") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "file is too large")
            digest.update(chunk)
        if deadline is not None and time.monotonic() >= deadline:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "file hash deadline exceeded")
        final = os.fstat(descriptor)
        path_final = os.lstat(path)
        if (
            not _same_file_metadata(initial, final)
            or final.st_size != total
            or stat.S_ISLNK(path_final.st_mode)
            or not _same_file_metadata(final, path_final)
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "file changed during read")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    return digest.hexdigest(), total


def _mkstemp_in_parent(parent: pathlib.Path, prefix: str, suffix: str, field: str):
    """Create a mode-0600 temp file and fail if its parent was replaced."""

    try:
        before = os.lstat(parent)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unsafe")
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=prefix, suffix=suffix, dir=os.fspath(parent)
        )
        temporary = pathlib.Path(temporary_name)
        after = os.lstat(parent)
        if stat.S_ISLNK(after.st_mode) or not _same_directory_identity(before, after):
            raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed")
        return descriptor, temporary
    except ReleaseVaultError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None and _symlink_component(parent) is None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None and _symlink_component(parent) is None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} temporary file unavailable") from exc


def _atomic_write(path: pathlib.Path, data: bytes, field: str):
    path = _absolute_path(path, field)
    if _symlink_component(path) is not None:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} contains a symlink")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unavailable") from exc
    if _symlink_component(parent) is not None:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unsafe")
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = _mkstemp_in_parent(
            parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            field=field,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, path, field)
        temporary = None
        try:
            directory_fd = os.open(os.fspath(parent), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} could not be written") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_replace(source: pathlib.Path, destination: pathlib.Path, field: str):
    """Replace within a validated parent directory, failing closed on swaps."""

    parent = destination.parent
    try:
        before_path = os.lstat(parent)
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed") from exc
    if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISDIR(before_path.st_mode):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory is unsafe")
    directory_fd = None
    try:
        try:
            directory_fd = os.open(
                os.fspath(parent),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            before_fd = os.fstat(directory_fd)
            if not _bundle_directory_snapshot(before_path, before_fd):
                raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed")
            os.replace(
                source.name,
                destination.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            after_path = os.lstat(parent)
            after_fd = os.fstat(directory_fd)
            if (
                stat.S_ISLNK(after_path.st_mode)
                or not _same_directory_identity(before_path, after_path)
                or not _same_directory_identity(before_fd, after_fd)
            ):
                try:
                    os.unlink(destination.name, dir_fd=directory_fd)
                except OSError:
                    pass
                raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed")
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            return

        # Windows does not expose directory handles for renameat-style calls.
        # Validate immediately before and after the path-based replacement;
        # any detected parent swap is a release failure.
        before_again = os.lstat(parent)
        if stat.S_ISLNK(before_again.st_mode) or not _bundle_directory_snapshot(before_path, before_again):
            raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed")
        os.replace(source, destination)
        after_path = os.lstat(parent)
        if stat.S_ISLNK(after_path.st_mode) or not _same_directory_identity(before_again, after_path):
            raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} directory changed")
        try:
            directory_fd = os.open(os.fspath(parent), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
    except ReleaseVaultError:
        raise
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} could not be committed") from exc
    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _sidecar_path(bundle: pathlib.Path) -> pathlib.Path:
    return bundle.with_suffix(".sha256")


def _reject_existing_artifact(path: pathlib.Path, field: str) -> bool:
    """Refuse replacement of an existing artifact and report that it is absent."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} could not be checked") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} is not a regular artifact")
    raise ReleaseVaultError("E_VAULT_PERMISSION", f"{field} already exists")


def _remove_new_artifact(path: pathlib.Path, was_absent: bool):
    if not was_absent:
        return
    try:
        if _symlink_component(path.parent) is not None:
            return
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode):
            path.unlink()
    except (FileNotFoundError, OSError, ReleaseVaultError):
        pass


def _read_sidecar(path: pathlib.Path, bundle_name: str) -> str:
    path = _require_regular_file(path, "sidecar")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar could not be read") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar could not be read") from exc
    chunks = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        path_opened = os.lstat(path)
        if (
            stat.S_ISLNK(opened.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(path_opened.st_mode)
            or not _same_file_metadata(before, opened)
            or not _same_file_metadata(before, path_opened)
            or opened.st_size > MAX_SIDECAR_BYTES
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar changed during read")
        started = time.monotonic()
        while True:
            if time.monotonic() - started > MAX_READ_SECONDS:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar read timed out")
            try:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar could not be read") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SIDECAR_BYTES:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar is too large")
            chunks.append(chunk)
        final = os.fstat(descriptor)
        path_final = os.lstat(path)
        if (
            stat.S_ISLNK(final.st_mode)
            or not stat.S_ISREG(final.st_mode)
            or stat.S_ISLNK(path_final.st_mode)
            or not _same_file_metadata(opened, final)
            or not _same_file_metadata(final, path_final)
            or final.st_size != total
        ):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar changed during read")
        try:
            text = b"".join(chunks).decode("ascii")
        except UnicodeError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar is invalid") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    expected = re.fullmatch(r"([a-f0-9]{64})  ([^\r\n]+)\n?", text)
    if expected is None or expected.group(2) != bundle_name:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "sidecar is invalid")
    return expected.group(1)


def _child_environment() -> dict[str, str]:
    for name in _PASSWORD_ENV_NAMES:
        if name in os.environ:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "password environment is not accepted")
    environment = os.environ.copy()
    for name in list(environment):
        if name in _PASSWORD_ENV_NAMES or "PASSWORD" in name.upper() or "PASSPHRASE" in name.upper():
            environment.pop(name, None)
    # This is never a production age control channel. Tests inject a callable
    # directly into create_bundle instead of changing a subprocess environment.
    environment.pop("MING_RELEASE_TEST_AGE", None)
    return environment


def _resolve_age_runner(age_runner=None):
    if callable(age_runner):
        return age_runner, None
    configured = age_runner or os.environ.get("MING_RELEASE_AGE", "age")
    candidate = os.fspath(configured)
    if any(separator in candidate for separator in (os.sep, os.altsep) if separator):
        resolved = pathlib.Path(candidate)
        if not resolved.is_absolute():
            resolved = _absolute_path(resolved, "age")
        try:
            info = os.lstat(resolved)
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "age is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "age is unavailable")
        return os.fspath(resolved), None
    resolved = shutil.which(candidate)
    if not resolved:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "age is unavailable")
    return resolved, None


def _invoke_age(runner, argv, tar_bytes: bytes, output: pathlib.Path, environment):
    if callable(runner):
        try:
            result = runner(argv=tuple(argv), input_bytes=tar_bytes, output_path=output, environment=environment)
        except TypeError:
            result = runner(tuple(argv), tar_bytes, output, environment)
        if isinstance(result, (bytes, bytearray)):
            try:
                output.write_bytes(bytes(result))
            except OSError as exc:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "age output could not be written") from exc
            result = 0
        if result not in (None, 0, True):
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "age encryption failed")
        return None
    try:
        completed = subprocess.run(
            list(argv),
            input=tar_bytes,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            check=False,
            timeout=AGE_RUN_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "age encryption failed") from exc
    if completed.returncode != 0:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "age encryption failed")
    return None


def _coerce_fingerprints(fingerprints):
    if isinstance(fingerprints, Mapping):
        primary = fingerprints.get("primary") or fingerprints.get("primary_fingerprint")
        signing = fingerprints.get("signing") or fingerprints.get("signing_fingerprint")
    elif isinstance(fingerprints, (tuple, list)) and len(fingerprints) == 2:
        primary, signing = fingerprints
    else:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "fingerprints are required")
    return _require_fingerprint(primary, "primary_fingerprint"), _require_fingerprint(signing, "signing_fingerprint")


def _receipt_destination(bundle: pathlib.Path, receipt_path=None) -> pathlib.Path:
    if receipt_path is not None:
        return pathlib.Path(receipt_path)
    raw_vault = os.environ.get("MING_RELEASE_VAULT")
    if raw_vault:
        vault = _absolute_path(raw_vault, "MING_RELEASE_VAULT")
        return vault / "receipts" / f"{bundle.stem}.json"
    return bundle.with_suffix(".json")


def write_receipt(
    bundle,
    sidecar,
    public_keyring,
    policy,
    bundle_id,
    generation,
    fingerprints,
    receipt_path=None,
):
    """Write a public receipt atomically and validate the bytes read back."""

    bundle = _require_regular_file(bundle, "bundle")
    sidecar = _require_regular_file(sidecar, "sidecar")
    public_keyring = _require_regular_file(public_keyring, "public keyring")
    policy = _require_regular_file(policy, "policy")
    bundle_id = _require_opaque_id(bundle_id, "bundle_id")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "generation is invalid")
    primary, signing = _coerce_fingerprints(fingerprints)
    bundle_hash, bundle_bytes = _hash_regular_file(bundle, max_bytes=MAX_BUNDLE_BYTES)
    sidecar_hash = _read_sidecar(sidecar, bundle.name)
    if sidecar_hash != bundle_hash:
        raise ReleaseVaultError("E_VAULT_HASH_MISMATCH", "sidecar does not match bundle")
    keyring_hash, _ = _hash_regular_file(public_keyring, max_bytes=MAX_RECEIPT_BYTES)
    policy_hash, _ = _hash_regular_file(policy, max_bytes=MAX_RECEIPT_BYTES)
    receipt = {
        "format": RECEIPT_FORMAT,
        "bundle_id": bundle_id,
        "generation": generation,
        "primary_fingerprint": primary,
        "signing_fingerprint": signing,
        "bundle_sha256": bundle_hash,
        "bundle_bytes": bundle_bytes,
        "public_keyring_sha256": keyring_hash,
        "key_policy_sha256": policy_hash,
        "encryption_format": "age-v1",
        "created_at": _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "nas_object": bundle_id,
        "status": "verified",
    }
    validated = validate_receipt(receipt)
    destination = _receipt_destination(bundle, receipt_path)
    vault = os.environ.get("MING_RELEASE_VAULT")
    if vault:
        vault_path = _configured_vault()
        destination = _prepare_output_path(destination, vault_path, "receipt")
    else:
        destination = _absolute_path(destination, "receipt")
        _reject_repository_path(destination, "receipt")
    encoded = (json.dumps(validated, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")
    _atomic_write(destination, encoded, "receipt")
    try:
        read_back = validate_receipt(_load_json(destination))
    except ReleaseVaultError:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    if read_back != validated:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt read-back validation failed")
    return read_back


def create_bundle(
    input_dir,
    output,
    recipient_file,
    age_runner=None,
    *,
    password_tty=False,
    sidecar=None,
    receipt=None,
    public_keyring=None,
    policy=None,
    bundle_id=None,
    generation=None,
    fingerprints=None,
):
    """Create an age-encrypted deterministic recovery bundle."""

    vault = _configured_vault()
    if password_tty and recipient_file is not None:
        raise ReleaseVaultError("E_USAGE", "recipient-file and password-tty are mutually exclusive")
    if not password_tty and recipient_file is None:
        raise ReleaseVaultError("E_USAGE", "recipient-file or password-tty is required")
    if password_tty and not sys.stdin.isatty():
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "password-tty requires an interactive TTY")
    input_path = _require_directory(input_dir, "input")
    output_path = _prepare_output_path(output, vault, "output")
    if _path_within(output_path, input_path):
        raise ReleaseVaultError("E_VAULT_PERMISSION", "output must not be inside input")
    recipient_path = None
    if recipient_file is not None:
        recipient_path = _require_regular_file(recipient_file, "recipient file")
        try:
            if os.stat(recipient_path, follow_symlinks=False).st_size > MAX_RECEIPT_BYTES:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "recipient file is too large")
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "recipient file is unavailable") from exc
    if any(name in os.environ for name in _PASSWORD_ENV_NAMES):
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "password environment is not accepted")
    if output_path.suffix.lower() != ".age":
        raise ReleaseVaultError("E_VAULT_PERMISSION", "output must be an .age bundle")
    if input_path == REPO_ROOT or _path_within(input_path, REPO_ROOT):
        # A repository-local source could accidentally archive an ignored .age
        # object. Private input belongs in the explicitly configured vault.
        raise ReleaseVaultError("E_VAULT_PERMISSION", "input must be outside the Git worktree")
    bundle_id_value = bundle_id or output_path.stem
    if bundle_id is not None:
        bundle_id_value = _require_opaque_id(bundle_id, "bundle_id")
    runner, _ = _resolve_age_runner(age_runner)
    sidecar_path = _prepare_output_path(sidecar, vault, "sidecar") if sidecar else _sidecar_path(output_path)
    sidecar_path = _prepare_output_path(sidecar_path, vault, "sidecar")
    output_absent = _reject_existing_artifact(output_path, "output")
    sidecar_absent = _reject_existing_artifact(sidecar_path, "sidecar")

    metadata_requested = any(
        item is not None for item in (receipt, public_keyring, policy, generation, fingerprints)
    )
    receipt_candidate = None
    public_keyring_hash_before = None
    policy_hash_before = None
    if metadata_requested:
        if public_keyring is None:
            public_keyring = vault / "public" / "release-keyring.gpg"
        if policy is None:
            policy = vault / "public" / "key-policy.json"
        public_keyring = _require_regular_file(public_keyring, "public keyring")
        policy = _require_regular_file(policy, "policy")
        # Read and fingerprint public metadata before any encrypted output is
        # produced. write_receipt hashes again after commit to catch changes.
        public_keyring_hash_before, _ = _hash_regular_file(
            public_keyring, max_bytes=MAX_RECEIPT_BYTES
        )
        policy_hash_before, _ = _hash_regular_file(policy, max_bytes=MAX_RECEIPT_BYTES)
        bundle_id_value = _require_opaque_id(bundle_id_value, "bundle_id")
        if generation is None:
            match = re.fullmatch(r"recovery-bundle-([0-9]+)", output_path.stem)
            if match is None:
                raise ReleaseVaultError("E_RELEASE_NOT_READY", "generation is required")
            generation = int(match.group(1))
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "generation is invalid")
        fingerprints = _coerce_fingerprints(fingerprints)
        receipt_candidate = _prepare_output_path(
            receipt if receipt is not None else _receipt_destination(output_path),
            vault,
            "receipt",
        )
        _reject_existing_artifact(receipt_candidate, "receipt")

    tar_bytes = _build_deterministic_tar(input_path)
    environment = _child_environment()
    temporary = None
    invocation = None
    try:
        try:
            descriptor, temporary = _mkstemp_in_parent(
                output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".age.tmp",
                field="output",
            )
            os.close(descriptor)
            temporary.unlink()
            runner_argv = "age" if callable(runner) else os.fspath(runner)
            if password_tty:
                argv = [runner_argv, "-p", "-o", os.fspath(temporary)]
            else:
                argv = [runner_argv, "-R", os.fspath(recipient_path), "-o", os.fspath(temporary)]
            invocation = _invoke_age(runner, argv, tar_bytes, temporary, environment)
            encrypted = _require_regular_file(temporary, "age output")
            encrypted_hash, encrypted_bytes = _hash_regular_file(encrypted)
            _atomic_replace(encrypted, output_path, "output")
            try:
                os.chmod(output_path, 0o600)
            except OSError as exc:
                raise ReleaseVaultError("E_VAULT_PERMISSION", "bundle permissions could not be set") from exc
            temporary = None
        except ReleaseVaultError:
            raise
        except OSError as exc:
            raise ReleaseVaultError("E_RELEASE_NOT_READY", "bundle output could not be committed") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

        # Hash the committed path, not only the temporary path, before making
        # a public sidecar. This closes a replacement race at os.replace.
        committed_hash, committed_bytes = _hash_regular_file(output_path)
        if committed_hash != encrypted_hash or committed_bytes != encrypted_bytes:
            raise ReleaseVaultError("E_VAULT_HASH_MISMATCH", "bundle changed after commit")
        encrypted_hash, encrypted_bytes = committed_hash, committed_bytes
        _atomic_write(sidecar_path, f"{encrypted_hash}  {output_path.name}\n".encode("ascii"), "sidecar")
        if _read_sidecar(sidecar_path, output_path.name) != encrypted_hash:
            raise ReleaseVaultError("E_VAULT_HASH_MISMATCH", "sidecar read-back validation failed")

        result = {
            "bundle_id": bundle_id_value,
            "bundle_sha256": encrypted_hash,
            "bundle_bytes": encrypted_bytes,
            "sidecar": sidecar_path.name,
        }
        if metadata_requested:
            receipt_value = write_receipt(
                output_path,
                sidecar_path,
                public_keyring,
                policy,
                bundle_id_value,
                generation,
                fingerprints,
                receipt_path=receipt_candidate,
            )
            if (
                receipt_value["public_keyring_sha256"] != public_keyring_hash_before
                or receipt_value["key_policy_sha256"] != policy_hash_before
            ):
                raise ReleaseVaultError("E_VAULT_HASH_MISMATCH", "public metadata changed during bundle creation")
            result["receipt"] = receipt_value
        if invocation is not None:
            result["test_invocation"] = invocation
        return result
    except Exception:
        _remove_new_artifact(output_path, output_absent)
        _remove_new_artifact(sidecar_path, sidecar_absent)
        if receipt_candidate is not None:
            _remove_new_artifact(receipt_candidate, True)
        raise


class ArgumentParseError(ValueError):
    """Raised so CLI argument failures remain JSON, like other failures."""


class HelpRequested(ValueError):
    """Carries formatter output for the JSON help response."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class JsonHelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs):
        kwargs.setdefault("nargs", 0)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        raise HelpRequested(parser.format_help())


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentParseError(message)

    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument("-h", "--help", action=JsonHelpAction, help=argparse.SUPPRESS)

    def exit(self, status=0, message=None):
        if status:
            raise ArgumentParseError(message or "invalid release-vault command")
        raise ArgumentParseError(message or "release-vault help is unavailable")


def _build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="ming-release-vault.py",
        description="Validate public release receipts, scan public release material, and prepare encrypted recovery bundles.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=JsonArgumentParser
    )

    scan = commands.add_parser("scan-public", help="scan a public release tree")
    scan.add_argument(
        "--root",
        required=True,
        type=pathlib.Path,
        help="public trust-material directory only; exclude payload/ISO blobs",
    )

    receipt = commands.add_parser("verify-receipt", help="validate a public receipt")
    receipt.add_argument("--receipt", required=True, type=pathlib.Path)

    bundle = commands.add_parser(
        "create-bundle",
        help="encrypt a local recovery bundle",
        description=(
            "Encrypt a deterministic recovery bundle with age recipient-file mode or an interactive TTY prompt. "
            "Password options, password environment variables, and redirected password stdin are rejected."
        ),
    )
    bundle.add_argument("--input", required=True, type=pathlib.Path)
    bundle.add_argument("--output", required=True, type=pathlib.Path)
    credentials = bundle.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--recipient-file", type=pathlib.Path)
    credentials.add_argument("--password-tty", action="store_true")
    bundle.add_argument("--sidecar", type=pathlib.Path)
    bundle.add_argument("--receipt", type=pathlib.Path)
    bundle.add_argument("--public-keyring", type=pathlib.Path)
    bundle.add_argument("--policy", type=pathlib.Path)
    bundle.add_argument("--bundle-id")
    bundle.add_argument("--generation", type=int)
    bundle.add_argument("--primary-fingerprint")
    bundle.add_argument("--signing-fingerprint")
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except HelpRequested as exc:
        return emit_ok({"help": exc.text})
    except ArgumentParseError:
        return emit_error("E_USAGE", "invalid release-vault arguments")
    try:
        if args.command == "scan-public":
            return emit_ok(scan_public_tree(args.root))
        if args.command == "verify-receipt":
            receipt = validate_receipt(_load_json(args.receipt))
            return emit_ok({"receipt": receipt})
        if args.command == "create-bundle":
            fingerprints = None
            if args.primary_fingerprint is not None or args.signing_fingerprint is not None:
                fingerprints = (args.primary_fingerprint, args.signing_fingerprint)
            return emit_ok(
                create_bundle(
                    args.input,
                    args.output,
                    args.recipient_file,
                    password_tty=args.password_tty,
                    sidecar=args.sidecar,
                    receipt=args.receipt,
                    public_keyring=args.public_keyring,
                    policy=args.policy,
                    bundle_id=args.bundle_id,
                    generation=args.generation,
                    fingerprints=fingerprints,
                )
            )
        return emit_error("E_USAGE", "unsupported command")
    except ReleaseVaultError as exc:
        return emit_error(exc.error_code, exc.message, exc.details)
    except Exception:
        # Keep unexpected local I/O/parser details out of release logs.
        return emit_error("E_RELEASE_NOT_READY", "release trust check failed")


if __name__ == "__main__":
    sys.exit(main())
