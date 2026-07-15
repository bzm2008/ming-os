#!/usr/bin/env python3
"""Single security boundary for staging a Ming OS transaction."""

import argparse
import importlib.util
import json
import os
import pathlib
import platform
import sys


HERE = pathlib.Path(__file__).resolve().parent
KEYRING = "/usr/share/ming-update/trust/release-keyring.gpg"
KEY_POLICY = "/usr/share/ming-update/trust/key-policy.json"
STATE_ROOT = "/var/lib/ming-update"


def _load_sibling(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_module = _load_sibling("ming-transaction-verify.py", "ming_transaction_verify_engine")
apply_module = _load_sibling("ming-transaction-apply.py", "ming_transaction_apply_engine")


class EngineError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def stage_release(
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
    active_root,
    state_root,
    transaction_id,
    available_bytes=None,
    key_policy=None,
    verifier=verify_module.verify_release,
    applicator=apply_module.prepare_candidate,
):
    try:
        verifier_arguments = {
            "manifest_path": manifest_path,
            "manifest_signature": manifest_signature,
            "index_path": index_path,
            "index_signature": index_signature,
            "payload_path": payload_path,
            "payload_signature": payload_signature,
            "keyring": keyring,
            "current_version": current_version,
            "architecture": architecture,
            "kernel_release": kernel_release,
            "bootstrap_version": bootstrap_version,
        }
        if key_policy is not None:
            verifier_arguments["key_policy"] = key_policy
        plan = verifier(
            **verifier_arguments,
        )
    except EngineError:
        raise
    except Exception as exc:
        raise EngineError(
            getattr(exc, "code", "E_MANIFEST_SCHEMA"),
            getattr(exc, "message", str(exc)),
            getattr(exc, "details", {}),
        ) from exc
    try:
        return applicator(
            plan=plan,
            payload_path=payload_path,
            active_root=active_root,
            state_root=state_root,
            transaction_id=transaction_id,
            available_bytes=available_bytes,
        )
    except EngineError:
        raise
    except Exception as exc:
        raise EngineError(
            getattr(exc, "code", "E_PACKAGE_APPLY"),
            getattr(exc, "message", str(exc)),
            getattr(exc, "details", {}),
        ) from exc


def _read_version():
    for path in (pathlib.Path("/etc/ming-os-version"), pathlib.Path("/etc/os-release")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.name == "ming-os-version":
            return content.strip()
        for line in content.splitlines():
            if line.startswith("VERSION_ID="):
                return line.partition("=")[2].strip().strip('"')
    raise EngineError("E_SOURCE_UNSUPPORTED", "installed Ming OS version is unavailable")


def _read_bootstrap_version():
    marker = pathlib.Path(STATE_ROOT) / "capability.json"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EngineError("E_BOOTSTRAP_REQUIRED", "transaction bootstrap capability is unavailable") from exc
    version = value.get("bootstrap_version")
    if value.get("capability") != "transactional-slot-v1" or not isinstance(version, str):
        raise EngineError("E_BOOTSTRAP_REQUIRED", "transaction bootstrap capability is incomplete")
    return version


def _architecture():
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    return machine


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("stage",))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-signature", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--index-signature", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--payload-signature", required=True)
    parser.add_argument("--transaction-id", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = stage_release(
            manifest_path=arguments.manifest,
            manifest_signature=arguments.manifest_signature,
            index_path=arguments.index,
            index_signature=arguments.index_signature,
            payload_path=arguments.payload,
            payload_signature=arguments.payload_signature,
            keyring=KEYRING,
            key_policy=KEY_POLICY,
            current_version=_read_version(),
            architecture=_architecture(),
            kernel_release=os.uname().release,
            bootstrap_version=_read_bootstrap_version(),
            active_root="/",
            state_root=STATE_ROOT,
            transaction_id=arguments.transaction_id,
        )
        print(json.dumps({"ok": True, "state": result}, ensure_ascii=True, separators=(",", ":")))
        return 0
    except EngineError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        return {
            "E_MANIFEST_SIGNATURE": 4,
            "E_ARTIFACT_SIGNATURE": 4,
            "E_ARTIFACT_HASH": 4,
            "E_CONTENT_POLICY": 4,
            "E_SPACE": 3,
        }.get(exc.code, 5)


if __name__ == "__main__":
    sys.exit(main())
