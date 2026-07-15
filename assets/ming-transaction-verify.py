#!/usr/bin/env python3
"""Fail-closed verification for Ming OS transactional update artifacts."""

import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import urllib.parse


HEX64 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^[A-F0-9]{40,64}$")
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
VERSION = re.compile(r"^(?P<numeric>[0-9]+(?:\.[0-9]+){1,3})(?P<suffix>[A-Za-z0-9._-]+)?$")
HTTPS_URL = re.compile(r"^https://[^/?#@]+(?:/[^?#]*)?$")
RELATIVE_PATH = re.compile(r"^[A-Za-z0-9._+:-]+(?:/[A-Za-z0-9._+:-]+)*$")
SYMLINK_TARGET = re.compile(r"^(?!/)[A-Za-z0-9._+:-]+(?:/[A-Za-z0-9._+:-]+)*$")
FORBIDDEN_PATHS = (
    "boot",
    "home",
    "lib/modules",
    "usr/lib/modules",
    "var/lib/ming-update",
    "etc/machine-id",
    "etc/passwd",
    "etc/group",
    "etc/shadow",
    "etc/gshadow",
    "etc/NetworkManager/system-connections",
    "etc/ssh",
    "var/lib/NetworkManager",
    "var/lib/bluetooth",
    "usr/local/bin/ming-update",
    "usr/local/sbin/ming-transaction-health",
)
DEFAULT_ALLOWLIST = pathlib.Path(__file__).with_name("ming-transaction-allowlist.txt")


class TransactionError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransactionError("E_MANIFEST_SCHEMA", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_strict(path, error_code="E_MANIFEST_SCHEMA"):
    try:
        with pathlib.Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except TransactionError as exc:
        if exc.code == error_code:
            raise
        raise TransactionError(error_code, exc.message) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TransactionError(error_code, f"invalid JSON artifact: {exc}") from exc


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with pathlib.Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise TransactionError("E_ARTIFACT_HASH", f"cannot read artifact: {exc}") from exc
    return digest.hexdigest()


def load_key_policy(path):
    policy = load_json_strict(path, error_code="E_KEY_POLICY")
    _require(policy.get("schema") == "ming.ota-key-policy.v1", "E_KEY_POLICY", "release key policy schema is invalid")
    for key in ("allowed_primary_fingerprints", "allowed_signing_fingerprints"):
        values = policy.get(key)
        _require(isinstance(values, list) and values, "E_KEY_POLICY", f"release key policy {key} is empty")
        _require(
            all(isinstance(value, str) and FINGERPRINT.fullmatch(value.upper()) for value in values),
            "E_KEY_POLICY",
            f"release key policy {key} is invalid",
        )
    _require(
        isinstance(policy.get("channels"), list) and policy["channels"]
        and all(isinstance(channel, str) and channel for channel in policy["channels"]),
        "E_KEY_POLICY",
        "release key policy channels are invalid",
    )
    _version_tuple(policy.get("minimum_bootstrap"))
    return {
        **policy,
        "allowed_primary_fingerprints": {value.upper() for value in policy["allowed_primary_fingerprints"]},
        "allowed_signing_fingerprints": {value.upper() for value in policy["allowed_signing_fingerprints"]},
    }


def _validated_signature_fingerprints(status_output):
    values = []
    for line in str(status_output or "").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] != "[GNUPG:]" or parts[1] != "VALIDSIG":
            continue
        signing = parts[2].upper()
        primary = parts[-1].upper()
        if FINGERPRINT.fullmatch(signing) is None or FINGERPRINT.fullmatch(primary) is None:
            raise TransactionError("E_KEY_POLICY", "gpgv returned an invalid signing fingerprint")
        values.append((primary, signing))
    if len(values) != 1:
        raise TransactionError("E_KEY_POLICY", "gpgv did not return exactly one valid signature")
    return values[0]


def verify_detached_signature(artifact, signature, keyring, key_policy=None, runner=subprocess.run):
    artifact = pathlib.Path(artifact)
    signature = pathlib.Path(signature)
    keyring = pathlib.Path(keyring)
    if not artifact.is_file() or artifact.is_symlink():
        raise TransactionError("E_ARTIFACT_SIGNATURE", "artifact is missing or unsafe")
    if not signature.is_file() or signature.is_symlink():
        raise TransactionError("E_ARTIFACT_SIGNATURE", "detached signature is missing or unsafe")
    if not keyring.is_file() or keyring.is_symlink():
        raise TransactionError("E_KEY_POLICY", "release keyring is missing or unsafe")
    try:
        result = runner(
            ["gpgv", "--status-fd=1", "--keyring", str(keyring), str(signature), str(artifact)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TransactionError("E_ARTIFACT_SIGNATURE", f"gpgv failed: {exc}") from exc
    if result.returncode != 0:
        raise TransactionError(
            "E_ARTIFACT_SIGNATURE",
            "detached signature verification failed",
            {"gpgv": (result.stderr or "")[-512:]},
        )
    if key_policy is not None:
        policy = load_key_policy(key_policy)
        primary, signing = _validated_signature_fingerprints(getattr(result, "stdout", ""))
        if primary not in policy["allowed_primary_fingerprints"] or signing not in policy["allowed_signing_fingerprints"]:
            raise TransactionError("E_KEY_POLICY", "signature key is not permitted by the release policy")


def _require(condition, code, message):
    if not condition:
        raise TransactionError(code, message)


def _parse_time(value):
    _require(isinstance(value, str), "E_MANIFEST_SCHEMA", "timestamp must be a string")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TransactionError("E_MANIFEST_SCHEMA", "timestamp is invalid") from exc
    _require(parsed.tzinfo is not None, "E_MANIFEST_SCHEMA", "timestamp must include timezone")
    return parsed.astimezone(datetime.timezone.utc)


def _version_tuple(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    _require(
        match is not None,
        "E_BOOTSTRAP_VERSION",
        "bootstrap version is invalid",
    )
    return tuple(int(part) for part in match.group("numeric").split("."))


def _compare_versions(left, right, *, error_code="E_MANIFEST_SCHEMA"):
    try:
        left_numeric = _version_tuple(left)
        right_numeric = _version_tuple(right)
    except TransactionError as exc:
        raise TransactionError(error_code, "release version is invalid") from exc
    width = max(len(left_numeric), len(right_numeric))
    left_key = left_numeric + (0,) * (width - len(left_numeric))
    right_key = right_numeric + (0,) * (width - len(right_numeric))
    if left_key != right_key:
        return 1 if left_key > right_key else -1
    left_suffix = VERSION.fullmatch(left).group("suffix") or ""
    right_suffix = VERSION.fullmatch(right).group("suffix") or ""
    if left_suffix == right_suffix:
        return 0
    if not left_suffix:
        return 1
    if not right_suffix:
        return -1
    return 1 if left_suffix > right_suffix else -1


def _require_exact_object(value, expected, label, code="E_MANIFEST_SCHEMA"):
    _require(isinstance(value, dict), code, f"{label} must be an object")
    _require(set(value) == set(expected), code, f"{label} fields are invalid")


def _require_object_fields(value, *, required, allowed, label, code):
    _require(isinstance(value, dict), code, f"{label} must be an object")
    _require(set(value).issubset(allowed) and required.issubset(value), code, f"{label} fields are invalid")


def _validate_https_url(value):
    _require(isinstance(value, str) and HTTPS_URL.fullmatch(value) is not None, "E_MANIFEST_SCHEMA", "artifact URL is invalid")
    parsed = urllib.parse.urlsplit(value)
    _require(
        parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
        and not parsed.query and not parsed.fragment,
        "E_MANIFEST_SCHEMA",
        "artifact URL must be credential-free HTTPS",
    )


def _validate_artifact_descriptor(value):
    _require_exact_object(value, {"url", "sha256", "size", "signature_url"}, "artifact descriptor")
    _validate_https_url(value.get("url"))
    _validate_https_url(value.get("signature_url"))
    _require(HEX64.fullmatch(str(value.get("sha256", ""))) is not None, "E_MANIFEST_SCHEMA", "artifact SHA256 is invalid")
    _require(type(value.get("size")) is int and value["size"] > 0, "E_MANIFEST_SCHEMA", "artifact size is invalid")


def validate_manifest(
    manifest,
    *,
    current_version,
    architecture,
    kernel_release,
    bootstrap_version,
    now,
):
    _require_exact_object(
        manifest,
        {
            "schema", "release_id", "version", "channel", "architecture", "delivery",
            "from_versions", "minimum_bootstrap", "created_at", "expires_at", "kernel_release",
            "payload", "content_index", "space", "slot_policy", "preserve_paths", "health_profile",
        },
        "manifest",
    )
    _require(manifest.get("schema") == "ming.transaction-manifest.v1", "E_MANIFEST_SCHEMA", "manifest schema is unsupported")
    release_id = manifest.get("release_id")
    _require(isinstance(release_id, str) and re.fullmatch(r"[A-Za-z0-9._-]{8,128}", release_id), "E_MANIFEST_SCHEMA", "release ID is invalid")
    version = manifest.get("version")
    _require(isinstance(version, str), "E_MANIFEST_SCHEMA", "target version is invalid")
    _require(
        _compare_versions(version, current_version) > 0,
        "E_MANIFEST_SCHEMA",
        "target version must advance the installed version",
    )
    _require(manifest.get("channel") == "stable", "E_MANIFEST_SCHEMA", "release channel is unsupported")
    _require(manifest.get("delivery") == "transactional-slot-v1", "E_MANIFEST_SCHEMA", "delivery is unsupported")
    _require(manifest.get("architecture") == architecture == "amd64", "E_MANIFEST_SCHEMA", "architecture does not match")
    sources = manifest.get("from_versions")
    _require(
        isinstance(sources, list) and sources and all(isinstance(item, str) for item in sources),
        "E_MANIFEST_SCHEMA",
        "source versions are invalid",
    )
    _require(len(sources) == len(set(sources)), "E_MANIFEST_SCHEMA", "source versions must be unique")
    for source in sources:
        _compare_versions(source, source)
    _require(current_version in sources, "E_SOURCE_UNSUPPORTED", "source version is not supported")
    _require(manifest.get("kernel_release") == kernel_release, "E_MANIFEST_SCHEMA", "kernel replacement is not permitted")
    minimum_bootstrap = manifest.get("minimum_bootstrap")
    if _compare_versions(bootstrap_version, minimum_bootstrap, error_code="E_BOOTSTRAP_VERSION") < 0:
        raise TransactionError("E_BOOTSTRAP_VERSION", "transaction bootstrap is too old")
    created = _parse_time(manifest.get("created_at"))
    expires = _parse_time(manifest.get("expires_at"))
    _require(created <= now < expires, "E_MANIFEST_EXPIRED", "manifest is not currently valid")
    _validate_artifact_descriptor(manifest.get("payload"))
    _validate_artifact_descriptor(manifest.get("content_index"))
    space = manifest.get("space")
    _require_exact_object(space, {"minimum_free_bytes", "reserve_bytes"}, "space policy")
    _require(
        type(space["minimum_free_bytes"]) is int and space["minimum_free_bytes"] > 0,
        "E_MANIFEST_SCHEMA",
        "space.minimum_free_bytes is invalid",
    )
    _require(
        type(space["reserve_bytes"]) is int and space["reserve_bytes"] >= 0,
        "E_MANIFEST_SCHEMA",
        "space.reserve_bytes is invalid",
    )
    policy = manifest.get("slot_policy")
    _require_exact_object(policy, {"maximum_uncommitted_boots", "retain_previous_committed_slots"}, "slot policy")
    _require(type(policy.get("maximum_uncommitted_boots")) is int and policy["maximum_uncommitted_boots"] == 1, "E_MANIFEST_SCHEMA", "v1 permits one candidate boot")
    _require(type(policy.get("retain_previous_committed_slots")) is int and policy["retain_previous_committed_slots"] == 1, "E_MANIFEST_SCHEMA", "v1 retains one committed slot")
    _require(manifest.get("preserve_paths") == ["/home"], "E_MANIFEST_SCHEMA", "/home preservation policy is required")
    _require(manifest.get("health_profile") == "ming-core-v1", "E_MANIFEST_SCHEMA", "health profile is unsupported")
    return dict(manifest)


def _normalize_path(value):
    _require(isinstance(value, str) and value, "E_CONTENT_POLICY", "content path is missing")
    _require(not value.startswith("/") and "\\" not in value, "E_CONTENT_POLICY", "content path must be relative POSIX")
    _require("//" not in value and "/./" not in f"/{value}/", "E_CONTENT_POLICY", "content path is not canonical")
    parts = value.split("/")
    _require(all(part not in ("", ".", "..") for part in parts), "E_CONTENT_POLICY", "content path escapes root")
    _require(RELATIVE_PATH.fullmatch(value) is not None, "E_CONTENT_POLICY", "content path characters are invalid")
    normalized = "/".join(parts)
    for forbidden in FORBIDDEN_PATHS:
        if normalized == forbidden or normalized.startswith(forbidden + "/"):
            raise TransactionError("E_CONTENT_POLICY", f"content path is protected: {normalized}")
    return normalized


def _load_allowlist(path=DEFAULT_ALLOWLIST):
    try:
        values = [
            line.strip()
            for line in pathlib.Path(path).read_text(encoding="ascii").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except (OSError, UnicodeError) as exc:
        raise TransactionError("E_CONTENT_POLICY", f"transaction allowlist is unavailable: {exc}") from exc
    normalized = []
    for value in values:
        if value.startswith("/") or ".." in value.split("/") or "\\" in value:
            raise TransactionError("E_CONTENT_POLICY", "transaction allowlist contains an unsafe prefix")
        normalized.append(value.rstrip("/"))
    _require(bool(normalized), "E_CONTENT_POLICY", "transaction allowlist is empty")
    return tuple(normalized)


def _require_allowlisted(path, allowlist):
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in allowlist):
        raise TransactionError("E_CONTENT_POLICY", f"content path is not allowlisted: {path}")


def _forbidden_package(name):
    return (
        name.startswith(("linux-image", "linux-headers", "linux-modules", "grub", "initramfs-tools"))
        or name.endswith("-dkms")
        or "-dkms-" in name
    )


def _validate_symlink(path, target):
    _require(isinstance(target, str) and SYMLINK_TARGET.fullmatch(target) is not None, "E_CONTENT_POLICY", "symlink target is unsafe")
    stack = path.split("/")[:-1]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            _require(bool(stack), "E_CONTENT_POLICY", "symlink escapes candidate root")
            stack.pop()
        else:
            stack.append(part)
    _normalize_path("/".join(stack))


def validate_content_index(index, release_id, architecture="amd64", allowlist_path=DEFAULT_ALLOWLIST):
    _require_exact_object(
        index,
        {"schema", "release_id", "entries", "deletions", "packages"},
        "content index",
        "E_CONTENT_POLICY",
    )
    _require(index.get("schema") == "ming.content-index.v1", "E_CONTENT_POLICY", "content index schema is unsupported")
    _require(
        isinstance(index.get("release_id"), str)
        and re.fullmatch(r"[A-Za-z0-9._-]{8,128}", index["release_id"])
        and index["release_id"] == release_id,
        "E_CONTENT_POLICY",
        "content index release ID differs",
    )
    entries = index.get("entries")
    deletions = index.get("deletions")
    packages = index.get("packages")
    _require(isinstance(entries, list) and isinstance(deletions, list) and isinstance(packages, list), "E_CONTENT_POLICY", "content index lists are invalid")
    _require(not packages, "E_CONTENT_POLICY", "offline package entries are not enabled in transaction payload v1")
    allowlist = _load_allowlist(allowlist_path)
    seen = set()
    normalized_entries = []
    for item in entries:
        _require_object_fields(
            item,
            required={"path", "type", "mode", "uid", "gid", "config_policy"},
            allowed={"path", "type", "blob", "target", "mode", "uid", "gid", "config_policy", "base_sha256"},
            label="content entry",
            code="E_CONTENT_POLICY",
        )
        path = _normalize_path(item.get("path"))
        _require_allowlisted(path, allowlist)
        _require(path not in seen, "E_CONTENT_POLICY", "duplicate content path")
        seen.add(path)
        kind = item.get("type")
        _require(kind in {"file", "directory", "symlink"}, "E_CONTENT_POLICY", "content type is unsafe")
        _require(type(item.get("mode")) is int and 0 <= item["mode"] <= 0o1777, "E_CONTENT_POLICY", "content mode is invalid")
        _require(type(item.get("uid")) is int and item["uid"] >= 0, "E_CONTENT_POLICY", "content uid is invalid")
        _require(type(item.get("gid")) is int and item["gid"] >= 0, "E_CONTENT_POLICY", "content gid is invalid")
        _require(item.get("config_policy") in {"replace", "replace-if-unmodified", "preserve"}, "E_CONTENT_POLICY", "config policy is invalid")
        if "base_sha256" in item:
            _require(HEX64.fullmatch(item["base_sha256"]) is not None, "E_CONTENT_POLICY", "base SHA256 is invalid")
        if kind == "file":
            _require("blob" in item and "target" not in item, "E_CONTENT_POLICY", "file entry fields are invalid")
            blob = str(item.get("blob", ""))
            _require(blob.startswith("sha256:") and HEX64.fullmatch(blob[7:]) is not None, "E_CONTENT_POLICY", "file blob is invalid")
        elif kind == "symlink":
            _require("target" in item and "blob" not in item, "E_CONTENT_POLICY", "symlink entry fields are invalid")
            _validate_symlink(path, item.get("target"))
        else:
            _require("blob" not in item and "target" not in item, "E_CONTENT_POLICY", "directory entry fields are invalid")
        normalized = dict(item)
        normalized["path"] = path
        normalized_entries.append(normalized)
    normalized_deletions = []
    for value in deletions:
        path = _normalize_path(value)
        _require_allowlisted(path, allowlist)
        _require(path not in seen, "E_CONTENT_POLICY", "duplicate content/deletion path")
        seen.add(path)
        normalized_deletions.append(path)
    normalized_packages = []
    for package in packages:
        _require_exact_object(package, {"name", "version", "architecture", "blob"}, "package entry", "E_CONTENT_POLICY")
        name = package.get("name")
        _require(isinstance(name, str) and PACKAGE_NAME.fullmatch(name) is not None, "E_CONTENT_POLICY", "package name is invalid")
        _require(not _forbidden_package(name), "E_CONTENT_POLICY", "kernel, boot, initramfs, and DKMS packages are forbidden")
        _require(package.get("architecture") in {architecture, "all"}, "E_CONTENT_POLICY", "package architecture is invalid")
        _require(isinstance(package.get("version"), str) and 0 < len(package["version"]) <= 256, "E_CONTENT_POLICY", "package version is invalid")
        blob = str(package.get("blob", ""))
        _require(blob.startswith("sha256:") and HEX64.fullmatch(blob[7:]) is not None, "E_CONTENT_POLICY", "package blob is invalid")
        normalized_packages.append(dict(package))
    return {
        "schema": "ming.content-index.v1",
        "release_id": release_id,
        "entries": normalized_entries,
        "deletions": normalized_deletions,
        "packages": normalized_packages,
    }


def _verify_descriptor(path, descriptor):
    path = pathlib.Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TransactionError("E_ARTIFACT_HASH", f"artifact is unavailable: {exc}") from exc
    if size != descriptor["size"] or _sha256(path) != descriptor["sha256"]:
        raise TransactionError("E_ARTIFACT_HASH", "artifact size or SHA256 differs from manifest")


def verify_release(
    *,
    manifest_path,
    manifest_signature,
    index_path,
    index_signature,
    payload_path,
    payload_signature,
    keyring,
    current_version,
    architecture,
    kernel_release,
    bootstrap_version,
    now=None,
    key_policy=None,
    signature_verifier=verify_detached_signature,
):
    now = now or datetime.datetime.now(datetime.timezone.utc)

    def verify_signature(artifact, signature):
        if key_policy is None:
            return signature_verifier(artifact, signature, keyring)
        return signature_verifier(artifact, signature, keyring, key_policy=key_policy)

    try:
        verify_signature(manifest_path, manifest_signature)
    except TransactionError as exc:
        raise TransactionError("E_MANIFEST_SIGNATURE", exc.message, exc.details) from exc
    manifest = validate_manifest(
        load_json_strict(manifest_path),
        current_version=current_version,
        architecture=architecture,
        kernel_release=kernel_release,
        bootstrap_version=bootstrap_version,
        now=now,
    )
    verify_signature(index_path, index_signature)
    verify_signature(payload_path, payload_signature)
    _verify_descriptor(index_path, manifest["content_index"])
    _verify_descriptor(payload_path, manifest["payload"])
    index = load_json_strict(index_path, error_code="E_CONTENT_POLICY")
    normalized_index = validate_content_index(index, manifest["release_id"], architecture)
    if key_policy is not None:
        policy = load_key_policy(key_policy)
        _require(manifest.get("channel") in policy["channels"], "E_KEY_POLICY", "release channel is not permitted")
        if _version_tuple(manifest["minimum_bootstrap"]) < _version_tuple(policy["minimum_bootstrap"]):
            raise TransactionError("E_KEY_POLICY", "release bootstrap policy is weaker than the installed policy")
    result = dict(manifest)
    result["content_index"] = normalized_index
    result["verified_artifacts"] = {
        "manifest_sha256": _sha256(manifest_path),
        "index_sha256": _sha256(index_path),
        "payload_sha256": _sha256(payload_path),
    }
    return result


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
