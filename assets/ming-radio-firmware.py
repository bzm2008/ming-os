#!/usr/bin/env python3
"""Validate and deploy audited offline radio firmware bundles."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath


class FirmwareValidationError(RuntimeError):
    pass


def _fail(code, detail):
    raise FirmwareValidationError("%s: %s" % (code, detail))


def _relative_path(value, code="E_FIRMWARE_PATH"):
    path = PurePosixPath(str(value or ""))
    if not str(path) or path.is_absolute() or ".." in path.parts or "." in path.parts:
        _fail(code, "unsafe relative path: %s" % value)
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_and_validate(manifest_path):
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("E_FIRMWARE_MANIFEST", str(exc))
    if manifest.get("schema") != 1 or not isinstance(manifest.get("entries"), list):
        _fail("E_FIRMWARE_MANIFEST", "unsupported schema or missing entries")

    root = manifest_path.parent
    checked = []
    targets_seen = set()
    for raw in manifest["entries"]:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            _fail("E_FIRMWARE_MANIFEST", "entry id is required")
        receipt = raw.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("redistribution_permitted") is not True:
            _fail("E_FIRMWARE_LICENSE", "%s lacks redistribution approval" % raw["id"])
        license_path = root.joinpath(*_relative_path(receipt.get("license"), "E_FIRMWARE_LICENSE").parts)
        if not license_path.is_file() or license_path.is_symlink():
            _fail("E_FIRMWARE_LICENSE", "%s license receipt is missing or unsafe" % raw["id"])
        try:
            license_text = license_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            _fail("E_FIRMWARE_LICENSE", "%s: %s" % (raw["id"], exc))
        if not license_text or not str(raw.get("source_url") or "").startswith("https://"):
            _fail("E_FIRMWARE_LICENSE", "%s has an incomplete source receipt" % raw["id"])

        asset = root.joinpath(*_relative_path(raw.get("asset")).parts)
        if not asset.is_file() or asset.is_symlink():
            _fail("E_FIRMWARE_ASSET", "%s payload is missing or unsafe" % raw["id"])
        expected = str(raw.get("sha256") or "").lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            _fail("E_FIRMWARE_MANIFEST", "%s has an invalid SHA256" % raw["id"])
        actual = _sha256(asset)
        if actual != expected:
            _fail("E_FIRMWARE_HASH", "%s expected %s, got %s" % (raw["id"], expected, actual))

        targets = [_relative_path(item) for item in raw.get("targets") or []]
        if not targets:
            _fail("E_FIRMWARE_MANIFEST", "%s has no target paths" % raw["id"])
        for target in targets:
            key = target.as_posix()
            if key in targets_seen:
                _fail("E_FIRMWARE_PATH", "duplicate target: %s" % key)
            targets_seen.add(key)
        checked.append((raw, asset, targets))
    return checked


def initramfs_files(manifest_path):
    files = []
    for raw, _asset, targets in _load_and_validate(manifest_path):
        if raw.get("include_in_initramfs") is True:
            files.extend("/usr/lib/firmware/" + target.as_posix() for target in targets)
    return files


def verify_deployed(manifest_path, firmware_root="/usr/lib/firmware"):
    checked = _load_and_validate(manifest_path)
    firmware_root = Path(firmware_root)
    verified = 0
    for raw, _asset, targets in checked:
        expected = str(raw["sha256"]).lower()
        for target in targets:
            deployed = firmware_root.joinpath(*target.parts)
            if not deployed.is_file() or deployed.is_symlink():
                _fail("E_FIRMWARE_DEPLOYED_MISSING", target.as_posix())
            actual = _sha256(deployed)
            if actual != expected:
                _fail("E_FIRMWARE_DEPLOYED_HASH", "%s expected %s, got %s" % (
                    target.as_posix(), expected, actual))
            verified += 1
    return {"ok": True, "verified_files": verified}


def validate_and_deploy(manifest_path, firmware_root="/usr/lib/firmware"):
    checked = _load_and_validate(manifest_path)
    firmware_root = Path(firmware_root)
    for _raw, _asset, targets in checked:
        for target in targets:
            current = firmware_root
            for part in target.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    _fail("E_FIRMWARE_PATH", "target parent is a symlink: %s" % current)
            final = firmware_root.joinpath(*target.parts)
            if final.is_symlink():
                _fail("E_FIRMWARE_PATH", "target is a symlink: %s" % final)

    entries = []
    deployed = 0
    for raw, asset, targets in checked:
        for target in targets:
            destination = firmware_root.joinpath(*target.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".%s." % destination.name,
                                             dir=str(destination.parent))
            try:
                with os.fdopen(fd, "wb") as output, asset.open("rb") as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o644)
                os.replace(temporary, destination)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            deployed += 1
        entries.append(raw["id"])
    return {"ok": True, "entries": entries, "deployed_files": deployed}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("deploy", "verify", "initramfs-files"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--firmware-root", default="/usr/lib/firmware")
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy":
            result = validate_and_deploy(args.manifest, args.firmware_root)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.command == "verify":
            result = verify_deployed(args.manifest, args.firmware_root)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            for path in initramfs_files(args.manifest):
                print(path)
    except FirmwareValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
