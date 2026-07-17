#!/usr/bin/env python3
"""Validate public release receipts and scan public release material.

This tool intentionally has no decryption, signing, upload, or remote execution
surface.  It is the small public-boundary checker used before a release is
published. Unknown regular binaries are accepted only when they are
marker-free; payload and ISO blobs do not belong under the scan root.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import pathlib
import re
import stat
import sys
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
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseVaultError("E_RELEASE_NOT_READY", "receipt could not be read") from exc
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
            if not os.path.samestat(before, after):
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
        if not os.path.samestat(before, after):
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
            or final.st_mtime_ns != after.st_mtime_ns
            or final.st_ctime_ns != after.st_ctime_ns
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
        description="Validate public release receipts and scan public release material.",
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
        return emit_error("E_USAGE", "unsupported command")
    except ReleaseVaultError as exc:
        return emit_error(exc.error_code, exc.message, exc.details)
    except Exception:
        # Keep unexpected local I/O/parser details out of release logs.
        return emit_error("E_RELEASE_NOT_READY", "release trust check failed")


if __name__ == "__main__":
    sys.exit(main())
