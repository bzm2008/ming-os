#!/usr/bin/env python3
"""Read-only diagnostics and sanitized export for transactional OTA state."""

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import uuid


TRANSACTION_ID = re.compile(r"^[A-Za-z0-9._-]{3,128}$")
SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "authorization", "cookie", "private_key")
LOG_NAMES = ("events.jsonl", "engine.jsonl", "health.jsonl", "rollback.jsonl")


class DiagnosticError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def validate_transaction_id(value):
    if not isinstance(value, str) or TRANSACTION_ID.fullmatch(value) is None:
        raise DiagnosticError("E_DIAGNOSTIC_ARGUMENT", "transaction ID is invalid")
    return value


def read_json(path, *, code="E_DIAGNOSTIC_STATE"):
    path = pathlib.Path(path)
    if not path.is_file() or path.is_symlink():
        raise DiagnosticError(code, f"diagnostic file is missing or unsafe: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError(code, f"diagnostic JSON is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise DiagnosticError(code, f"diagnostic JSON is not an object: {path.name}")
    return value


def read_jsonl(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    if not path.is_file() or path.is_symlink():
        raise DiagnosticError("E_DIAGNOSTIC_LOG", f"diagnostic log is unsafe: {path.name}")
    values = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("log record is not an object")
            values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DiagnosticError("E_DIAGNOSTIC_LOG", f"diagnostic log is invalid: {path.name}") from exc
    return values


def redact(value, key=""):
    lower_key = key.lower()
    if any(part in lower_key for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _transaction_dir(state_root, transaction_id):
    validate_transaction_id(transaction_id)
    state_root = pathlib.Path(state_root)
    if state_root.is_symlink():
        raise DiagnosticError("E_DIAGNOSTIC_STATE", "state root is unsafe")
    directory = state_root / "transactions" / transaction_id
    if not directory.is_dir() or directory.is_symlink():
        raise DiagnosticError("E_DIAGNOSTIC_NOT_FOUND", "transaction diagnostics are unavailable")
    return state_root, directory


def collect_status(state_root, transaction_id):
    state_root, directory = _transaction_dir(state_root, transaction_id)
    state = read_json(directory / "state.json")
    if state.get("transaction_id") != transaction_id:
        raise DiagnosticError("E_DIAGNOSTIC_STATE", "transaction state identity differs")
    events = read_jsonl(directory / "events.jsonl")
    logs = {}
    for name in LOG_NAMES:
        records = read_jsonl(directory / name)
        if records:
            logs[name] = {"entries": len(records), "last_event": records[-1].get("event") or records[-1].get("to_state")}
    failure_path = directory / "failure.json"
    failure = redact(read_json(failure_path, code="E_DIAGNOSTIC_LOG")) if failure_path.exists() else None
    current_path = state_root / "current.json"
    current = redact(read_json(current_path)) if current_path.exists() else None
    active_path = state_root / "active-transaction.json"
    active = redact(read_json(active_path)) if active_path.exists() else None
    transaction = {
        "id": transaction_id,
        "release_id": state.get("release_id"),
        "state": state.get("state"),
        "generation": state.get("generation"),
        "previous_slot": state.get("previous_slot"),
        "candidate_slot": state.get("candidate_slot"),
    }
    return {
        "schema": "ming.transaction-diagnostics.v1",
        "ok": True,
        "timestamp": timestamp(),
        "transaction": transaction,
        "current": current,
        "active": active,
        "events": {"count": len(events), "last_state": events[-1].get("to_state") if events else None},
        "failure": failure,
        "logs": logs,
    }


def write_export(path, value):
    path = pathlib.Path(path)
    if path.name in {"", ".", ".."} or path.is_symlink() or path.parent.is_symlink():
        raise DiagnosticError("E_DIAGNOSTIC_ARGUMENT", "export path is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "export"):
        item = subparsers.add_parser(command)
        item.add_argument("--state-root", default="/var/lib/ming-update")
        item.add_argument("--transaction", required=True)
        if command == "export":
            item.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    try:
        result = collect_status(arguments.state_root, arguments.transaction)
        if arguments.command == "export":
            write_export(arguments.output, result)
            result = {"schema": "ming.transaction-diagnostics.v1", "ok": True, "transaction": result["transaction"], "output": str(arguments.output)}
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except DiagnosticError as exc:
        print(json.dumps({"schema": "ming.transaction-diagnostics.v1", "ok": False, "error_code": exc.code, "message": exc.message}, ensure_ascii=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    sys.exit(main())
