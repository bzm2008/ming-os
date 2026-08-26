#!/usr/bin/env python3
"""Atomic checkpoint state for resumable Ming OS image builds."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tempfile
import argparse
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Mapping


STAGES = (
    "host-preflight",
    "debootstrap",
    "prepare-chroot",
    "modules",
    "initramfs",
    "clean-rootfs",
    "squashfs",
    "boot-assets",
    "iso",
    "publish-artifacts",
)

METADATA_FIELDS = (
    "version",
    "source_commit",
    "source_tree_sha256",
    "modules_sha256",
    "profile",
    "suite",
    "arch",
    "debian_mirror",
    "security_mirror",
    "squashfs_compression",
)

OPTIONAL_METADATA_FIELDS = (
    "build_suffix",
    "iso_volume_id",
    "skip_xiahai",
    "xiahai_sha256",
    "keyring_sha256",
    "apt_snapshot_sha256",
    "tools_fingerprint",
)


class StateError(RuntimeError):
    pass


class StateMismatch(StateError):
    def __init__(self, changed_fields: list[str]):
        self.changed_fields = changed_fields
        super().__init__(
            "build inputs changed: " + ", ".join(changed_fields)
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def make_metadata(**values: str) -> dict[str, str]:
    missing = [field for field in METADATA_FIELDS if not values.get(field)]
    if missing:
        raise StateError("missing metadata fields: " + ", ".join(missing))
    metadata = {field: str(values[field]) for field in METADATA_FIELDS}
    for field in OPTIONAL_METADATA_FIELDS:
        if values.get(field):
            metadata[field] = str(values[field])
    metadata["input_hash"] = hashlib.sha256(_canonical_bytes(metadata)).hexdigest()
    return metadata


def _state_path(state_dir: os.PathLike[str] | str, name: str) -> pathlib.Path:
    return pathlib.Path(state_dir) / name


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StateError(f"cannot read build state {path}: {error}") from error
    if not isinstance(payload, dict):
        raise StateError(f"build state is not an object: {path}")
    return payload


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def initialize(
    state_dir: os.PathLike[str] | str,
    metadata: Mapping[str, str],
    *,
    build_id: str,
    build_time_utc: str,
) -> dict[str, Any]:
    root = pathlib.Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "build.json"
    if path.exists():
        raise StateError(f"build state already exists: {path}")
    payload: dict[str, Any] = {
        "schema_version": 1,
        **dict(metadata),
        "build_id": build_id,
        "build_time_utc": build_time_utc,
        "status": "in_progress",
        "updated_at": build_time_utc,
    }
    _write_json_atomic(path, payload)
    return payload


def validate(
    state_dir: os.PathLike[str] | str,
    expected_metadata: Mapping[str, str],
) -> dict[str, Any]:
    payload = _read_json(_state_path(state_dir, "build.json"))
    if payload.get("schema_version") != 1:
        raise StateError("unsupported build state schema")
    changed = [
        field
        for field in (
            *METADATA_FIELDS,
            *OPTIONAL_METADATA_FIELDS,
            "input_hash",
        )
        if payload.get(field) != expected_metadata.get(field)
    ]
    if changed:
        raise StateMismatch(changed)
    return payload


def _validate_stage(stage: str) -> None:
    if stage not in STAGES:
        raise StateError(f"unknown build stage: {stage}")


def _stage_path(state_dir: os.PathLike[str] | str, stage: str) -> pathlib.Path:
    _validate_stage(stage)
    return _state_path(state_dir, f"{stage}.json")


def mark_started(
    state_dir: os.PathLike[str] | str,
    stage: str,
    *,
    input_hash: str,
    now: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    payload: dict[str, Any] = {
        "stage": stage,
        "status": "running",
        "input_hash": input_hash,
        "started_at": timestamp,
        "updated_at": timestamp,
        "pid": os.getpid() if pid is None else pid,
    }
    _write_json_atomic(_stage_path(state_dir, stage), payload)
    return payload


def _duration_seconds(started_at: str, completed_at: str) -> int:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    try:
        return max(0, int((parse(completed_at) - parse(started_at)).total_seconds()))
    except (TypeError, ValueError):
        return 0


def mark_completed(
    state_dir: os.PathLike[str] | str,
    stage: str,
    *,
    input_hash: str,
    now: str | None = None,
    artifacts: list[os.PathLike[str] | str] | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    path = _stage_path(state_dir, stage)
    existing = _read_json(path) if path.exists() else {}
    if existing.get("status") != "running":
        raise StateError(f"stage is not running: {stage}")
    if existing.get("input_hash") != input_hash:
        raise StateError(f"stage input hash changed: {stage}")
    artifact_records = []
    for artifact in artifacts or []:
        artifact_path = pathlib.Path(artifact)
        if not artifact_path.is_file():
            raise StateError(f"stage artifact is missing: {artifact_path}")
        digest = _sha256_file(artifact_path)
        artifact_records.append({
            "path": str(artifact_path),
            "size": artifact_path.stat().st_size,
            "sha256": digest,
        })
    started_at = str(existing.get("started_at") or timestamp)
    payload: dict[str, Any] = {
        "stage": stage,
        "status": "completed",
        "input_hash": input_hash,
        "started_at": started_at,
        "completed_at": timestamp,
        "updated_at": timestamp,
        "duration_seconds": _duration_seconds(started_at, timestamp),
    }
    if artifact_records:
        payload["artifacts"] = artifact_records
    _write_json_atomic(path, payload)
    return payload


def artifacts_valid(payload: Mapping[str, Any]) -> bool:
    records = payload.get("artifacts") or []
    if not isinstance(records, list):
        return False
    for record in records:
        if not isinstance(record, Mapping):
            return False
        path = pathlib.Path(str(record.get("path", "")))
        try:
            if not path.is_file() or path.stat().st_size != int(record["size"]):
                return False
            if _sha256_file(path) != record["sha256"]:
                return False
        except (KeyError, OSError, TypeError, ValueError):
            return False
    return True


def is_complete(
    state_dir: os.PathLike[str] | str,
    stage: str,
    input_hash: str,
) -> bool:
    path = _stage_path(state_dir, stage)
    if not path.is_file():
        return False
    try:
        payload = _read_json(path)
    except StateError:
        return False
    return (
        payload.get("status") == "completed"
        and payload.get("input_hash") == input_hash
        and artifacts_valid(payload)
    )


_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(ROOT_PASS|MING_USER_PASS|PASSWORD|PASSWD|TOKEN|API_KEY|SECRET)="
    r"(?:'[^']*'|\"[^\"]*\"|[^\s]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+")
_CURL_SECRET_RE = re.compile(
    r"(?i)(--(?:user|password|oauth2-bearer)|-u)\s+(?:'[^']*'|\"[^\"]*\"|[^\s]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|key|signature|token)=)[^&\s]+"
)


def redact_command(command: str) -> str:
    redacted = _ASSIGNMENT_SECRET_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", command
    )
    redacted = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _CURL_SECRET_RE.sub(
        lambda match: f"{match.group(1)} [REDACTED]", redacted
    )
    redacted = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    return redacted[:2048]


def mark_failed(
    state_dir: os.PathLike[str] | str,
    stage: str,
    *,
    input_hash: str,
    exit_code: int,
    command: str,
    line: int,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    stage_path = _stage_path(state_dir, stage)
    existing = _read_json(stage_path) if stage_path.exists() else {}
    payload: dict[str, Any] = {
        "stage": stage,
        "status": "failed",
        "input_hash": input_hash,
        "started_at": existing.get("started_at", timestamp),
        "failed_at": timestamp,
        "updated_at": timestamp,
        "exit_code": int(exit_code),
        "line": int(line),
        "command": redact_command(command),
        "resume_hint": f"./resume_build.sh --from {stage}",
    }
    _write_json_atomic(stage_path, payload)
    _write_json_atomic(_state_path(state_dir, "last-failure.json"), payload)
    return payload


def invalidate_from(
    state_dir: os.PathLike[str] | str,
    stage: str,
) -> list[str]:
    _validate_stage(stage)
    start = STAGES.index(stage)
    removed: list[str] = []
    for candidate in STAGES[start:]:
        path = _stage_path(state_dir, candidate)
        if path.exists():
            path.unlink()
            removed.append(candidate)
    failure = _state_path(state_dir, "last-failure.json")
    if failure.exists():
        failure.unlink()
    return removed


def _metadata_from_args(args: argparse.Namespace) -> dict[str, str]:
    return make_metadata(**{
        field: getattr(args, field)
        for field in (*METADATA_FIELDS, *OPTIONAL_METADATA_FIELDS)
        if getattr(args, field, None) is not None
    })


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _add_metadata_arguments(parser: argparse.ArgumentParser) -> None:
    for field in METADATA_FIELDS:
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field, required=True)
    for field in OPTIONAL_METADATA_FIELDS:
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--state-dir", required=True)
    init_parser.add_argument("--build-id", required=True)
    init_parser.add_argument("--build-time-utc", required=True)
    init_parser.add_argument("--resume", action="store_true")
    init_parser.add_argument("--fresh", action="store_true")
    init_parser.add_argument("--from", dest="from_stage")
    _add_metadata_arguments(init_parser)

    is_complete_parser = subparsers.add_parser("is-complete")
    is_complete_parser.add_argument("--state-dir", required=True)
    is_complete_parser.add_argument("--stage", required=True)
    is_complete_parser.add_argument("--input-hash", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--state-dir", required=True)
    start_parser.add_argument("--stage", required=True)
    start_parser.add_argument("--input-hash", required=True)

    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("--state-dir", required=True)
    complete_parser.add_argument("--stage", required=True)
    complete_parser.add_argument("--input-hash", required=True)
    complete_parser.add_argument("--artifact", action="append", default=[])

    fail_parser = subparsers.add_parser("fail")
    fail_parser.add_argument("--state-dir", required=True)
    fail_parser.add_argument("--stage", required=True)
    fail_parser.add_argument("--input-hash", required=True)
    fail_parser.add_argument("--exit-code", type=int, required=True)
    fail_parser.add_argument("--command", dest="action_command", required=True)
    fail_parser.add_argument("--line", type=int, required=True)

    invalidate_parser = subparsers.add_parser("invalidate-from")
    invalidate_parser.add_argument("--state-dir", required=True)
    invalidate_parser.add_argument("--stage", required=True)

    return parser


def _safe_cli_state_dir(value: str) -> pathlib.Path:
    """Restrict destructive CLI operations to the known build workspace."""
    candidate = pathlib.Path(value)
    if candidate.is_symlink():
        raise StateError("state directory must not be a symlink")
    resolved = candidate.resolve(strict=False)
    allowed_root = pathlib.Path("/var/tmp/ming-os-build").resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as error:
        raise StateError(
            f"state directory must be inside {allowed_root}: {resolved}"
        ) from error
    if resolved == allowed_root:
        raise StateError("state directory must not be the build workspace root")
    return resolved


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init":
        if args.resume and args.fresh:
            raise StateError("--resume and --fresh are mutually exclusive")
        state_dir = _safe_cli_state_dir(args.state_dir)
        metadata = _metadata_from_args(args)
        if args.fresh and state_dir.exists():
            shutil.rmtree(state_dir)
        if (state_dir / "build.json").exists():
            validate(state_dir, metadata)
            if args.from_stage:
                invalidate_from(state_dir, args.from_stage)
            payload = _read_json(state_dir / "build.json")
        else:
            if args.resume or args.from_stage:
                raise StateError(f"cannot resume without existing state: {state_dir}")
            payload = initialize(
                state_dir,
                metadata,
                build_id=args.build_id,
                build_time_utc=args.build_time_utc,
            )
            if args.from_stage:
                invalidate_from(state_dir, args.from_stage)
        _print_json(payload)
        return 0
    if args.command == "is-complete":
        return 0 if is_complete(args.state_dir, args.stage, args.input_hash) else 1
    if args.command == "start":
        _print_json(mark_started(args.state_dir, args.stage, input_hash=args.input_hash))
        return 0
    if args.command == "complete":
        _print_json(mark_completed(
            args.state_dir,
            args.stage,
            input_hash=args.input_hash,
            artifacts=args.artifact,
        ))
        return 0
    if args.command == "fail":
        _print_json(mark_failed(
            args.state_dir,
            args.stage,
            input_hash=args.input_hash,
            exit_code=args.exit_code,
            command=args.action_command,
            line=args.line,
        ))
        return 0
    if args.command == "invalidate-from":
        _print_json({"removed": invalidate_from(args.state_dir, args.stage)})
        return 0
    raise StateError(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StateError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
