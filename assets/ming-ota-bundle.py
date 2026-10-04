#!/usr/bin/env python3
"""Read-only validation for signed Ming offline OTA bundles."""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import tarfile
import tempfile


VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._-]*)?$")
BUILD_ID_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}-rc[0-9]+-[A-Fa-f0-9]{7,40}-[0-9]{8}T[0-9]{6}Z$")


def _version(value):
    match = re.match(r"^([0-9]+(?:\.[0-9]+){0,3})", str(value or ""))
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def _safe_member(member):
    name = pathlib.PurePosixPath(member.name)
    return (
        not name.is_absolute()
        and ".." not in name.parts
        and member.isfile()
        and not member.issym()
        and not member.islnk()
        and not member.isdev()
    )


def _error(reason, error=None):
    result = {"ok": False, "reason": reason}
    if error:
        result["error"] = str(error)[:500]
    return result


def _sha256_stream(source):
    digest = hashlib.sha256()
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def scan_bundle(path, current_version=""):
    path = pathlib.Path(path)
    if path.suffix != ".ming-ota" or path.is_symlink() or not path.is_file():
        return _error("not_bundle")
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) != 2 or any(not _safe_member(member) for member in members):
                return _error("unsafe_archive")
            by_name = {member.name: member for member in members}
            manifest_member = by_name.get("manifest.json")
            if manifest_member is None or manifest_member.size > 1024 * 1024:
                return _error("missing_manifest")
            manifest_file = archive.extractfile(manifest_member)
            manifest = json.load(manifest_file) if manifest_file else None
            if not isinstance(manifest, dict):
                return _error("invalid_manifest")
            required = ("schema", "version", "build_id", "update_type", "base_version",
                        "payload", "payload_size", "payload_sha256")
            if manifest.get("schema") != "ming.update.bundle.v1" or any(key not in manifest for key in required):
                return _error("invalid_manifest")
            version = str(manifest.get("version"))
            build_id = str(manifest.get("build_id"))
            payload = str(manifest.get("payload"))
            if not VERSION_RE.fullmatch(version) or not BUILD_ID_RE.fullmatch(build_id):
                return _error("invalid_manifest")
            version_prefix = re.match(r"^[0-9]+(?:\.[0-9]+){1,3}", version).group(0).replace(".", "")
            if build_id.split("-", 1)[0] != version_prefix:
                return _error("build_version_mismatch")
            if current_version and _version(version) <= _version(current_version):
                return _error("version_not_forward")
            if manifest.get("update_type") != "major" or payload.lower().endswith(".iso") is False:
                return _error("unsupported_update_type")
            if pathlib.PurePosixPath(payload).name != payload or payload == "manifest.json":
                return _error("unsafe_archive")
            if str(manifest.get("base_version")) != str(current_version):
                return _error("incompatible_base_version")
            payload_member = by_name.get(payload)
            if payload_member is None or payload_member.name == "manifest.json" or not _safe_member(payload_member):
                return _error("missing_payload")
            if int(manifest.get("payload_size", -1)) != payload_member.size:
                return _error("payload_size_mismatch")
            payload_file = archive.extractfile(payload_member)
            digest = _sha256_stream(payload_file) if payload_file else ""
            if digest.lower() != str(manifest.get("payload_sha256", "")).lower():
                return _error("payload_sha256_mismatch")
            if not str(manifest.get("signature", "")) or not str(manifest.get("trusted_comment", "")).startswith("Ming OS OTA"):
                return _error("signature_missing")
            return {"ok": True, "path": str(path), "version": version, "build_id": build_id,
                    "update_type": str(manifest.get("update_type")),
                    "base_version": str(manifest.get("base_version")), "payload": payload,
                    "payload_size": payload_member.size, "payload_sha256": digest,
                    "trusted_comment": str(manifest.get("trusted_comment"))}
    except (OSError, tarfile.TarError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _error("scan_failed", exc)


def extract_payload(path, destination, current_version=""):
    result = scan_bundle(path, current_version=current_version)
    if not result.get("ok"):
        return result
    destination = pathlib.Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with tarfile.open(path, "r:*") as archive:
            member = archive.getmember(result["payload"])
            source = archive.extractfile(member)
            target = destination / pathlib.PurePosixPath(result["payload"]).name
            with target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
        return {**result, "payload_path": str(target)}
    except (OSError, tarfile.TarError) as exc:
        return _error("extract_failed", exc)


def extract_manifest(path, destination, current_version=""):
    result = scan_bundle(path, current_version=current_version)
    if not result.get("ok"):
        return result
    destination = pathlib.Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = destination / "manifest.json"
    try:
        with tarfile.open(path, "r:*") as archive:
            member = archive.getmember("manifest.json")
            source = archive.extractfile(member)
            with target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
        return {**result, "manifest_path": str(target)}
    except (OSError, tarfile.TarError) as exc:
        return _error("extract_failed", exc)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-ota-bundle")
    parser.add_argument("bundle")
    parser.add_argument("--current-version", default="")
    parser.add_argument("--extract", default="")
    parser.add_argument("--manifest-out", default="")
    args = parser.parse_args(argv)
    if args.manifest_out:
        result = extract_manifest(args.bundle, args.manifest_out, args.current_version)
    elif args.extract:
        result = extract_payload(args.bundle, args.extract, args.current_version)
    else:
        result = scan_bundle(args.bundle, args.current_version)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
