#!/usr/bin/env python3
"""Bounded health confirmation and rollback reconciliation."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import uuid


HERE = pathlib.Path(__file__).resolve().parent


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "ming_transaction_state_health_runtime", HERE / "ming-transaction-state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state_module = _load_state_module()
DEFAULT_CHECKS = (
    ("packages", ["dpkg", "--audit"]),
    ("services", ["systemctl", "is-active", "dbus.service"]),
    ("services", ["systemctl", "is-active", "systemd-logind.service"]),
    ("services", ["systemctl", "is-active", "NetworkManager.service"]),
    ("desktop", ["/usr/local/sbin/ming-desktop-healthcheck", "--transaction-probe"]),
)
CHECK_ERRORS = {
    "root": "E_HEALTH_ROOT",
    "packages": "E_HEALTH_PACKAGES",
    "services": "E_HEALTH_SERVICE",
    "desktop": "E_HEALTH_DESKTOP_PROBE",
}


class HealthError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


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


def _atomic_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HealthError("E_STATE_DURABILITY", "health state path is unsafe")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _append_log(path, transaction_id, event, **fields):
    value = {
        "schema": "ming.health-log.v1",
        "transaction_id": transaction_id,
        "event": event,
        "timestamp": _timestamp(),
        **fields,
    }
    path = pathlib.Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _run(command, runner, timeout=10):
    try:
        return runner(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HealthError("E_HEALTH_TIMEOUT", "health check timed out") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HealthError("E_HEALTH_SERVICE", f"health command failed: {exc}") from exc


def _grub_entry(slot):
    return {"legacy": "ming-legacy", "A": "ming-slot-a", "B": "ming-slot-b"}[slot]


def _read_saved_entry(runner, grubenv):
    result = _run(["grub-editenv", str(grubenv), "list"], runner, timeout=5)
    if result.returncode != 0:
        raise HealthError("E_GRUB_READBACK", "cannot read bootloader environment")
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "saved_entry":
            return value
    return ""


def _set_saved_entry(slot, runner, grubenv):
    entry = _grub_entry(slot)
    result = _run(["grub-set-default", entry], runner, timeout=5)
    if result.returncode != 0:
        raise HealthError("E_GRUB_WRITE", "cannot set saved boot entry")
    if _read_saved_entry(runner, grubenv) != entry:
        raise HealthError("E_GRUB_READBACK", "saved boot entry readback differs")
    return entry


def _failure(transaction_id, state, code, check, message):
    return {
        "schema": "ming.transaction-failure.v1",
        "transaction_id": transaction_id,
        "release_id": state["release_id"],
        "previous_slot": state["previous_slot"],
        "candidate_slot": state["candidate_slot"],
        "generation": state["generation"],
        "error_code": code,
        "check": check,
        "reason": str(message)[:512],
        "timestamp": _timestamp(),
    }


def confirm_transaction(
    state_root,
    transaction_id,
    *,
    runner=subprocess.run,
    checks=DEFAULT_CHECKS,
    grubenv="/boot/grub/grubenv",
    fault_hook=None,
):
    state_root = pathlib.Path(state_root)
    store = state_module.TransactionStore(state_root)
    state = store.load(transaction_id)
    if state["state"] != "booting":
        raise HealthError("E_STATE_TRANSITION", "health requires a booting candidate")
    transaction_dir = state_root / "transactions" / transaction_id
    log_path = transaction_dir / "health.jsonl"
    state = store.transition(
        transaction_id,
        "pending_health",
        writer="health-service",
        expected_generation=state["generation"],
        evidence={"profile": "ming-core-v1"},
    )
    for check, command in checks:
        _append_log(log_path, transaction_id, "check-start", check=check, command=command[0])
        try:
            result = _run(command, runner)
            if result.returncode != 0:
                raise HealthError(
                    CHECK_ERRORS.get(check, "E_HEALTH_SERVICE"),
                    f"health check failed: {check}",
                    {"stderr": (result.stderr or "")[-512:]},
                )
            _append_log(log_path, transaction_id, "check-pass", check=check)
        except HealthError as exc:
            failure = _failure(transaction_id, state, exc.code, check, exc.message)
            _atomic_json(transaction_dir / "failure.json", failure)
            _append_log(log_path, transaction_id, "check-fail", check=check, error_code=exc.code, reason=exc.message)
            _set_saved_entry(state["previous_slot"], runner, grubenv)
            store.transition(
                transaction_id,
                "rollback_armed",
                writer="rollback-service",
                expected_generation=state["generation"],
                evidence={"error_code": exc.code, "check": check},
            )
            raise

    seal = json.loads((transaction_dir / "candidate-seal.json").read_text(encoding="utf-8"))
    token = {
        "schema": "ming.health-token.v1",
        "transaction_id": transaction_id,
        "release_id": state["release_id"],
        "candidate_slot": state["candidate_slot"],
        "candidate_sha256": seal["sha256"],
        "generation": state["generation"],
        "timestamp": _timestamp(),
    }
    _atomic_json(transaction_dir / "health-token.json", token)
    token_hash = hashlib.sha256(
        (transaction_dir / "health-token.json").read_bytes()
    ).hexdigest()
    state = store.transition(
        transaction_id,
        "committing",
        writer="health-confirmer",
        expected_generation=state["generation"],
        evidence={"health_token_sha256": token_hash},
    )
    entry = _set_saved_entry(state["candidate_slot"], runner, grubenv)
    if fault_hook:
        fault_hook("after-saved-entry-readback")
    state = store.transition(
        transaction_id,
        "committed",
        writer="commit-coordinator",
        expected_generation=state["generation"],
        evidence={"health_token_sha256": token_hash, "saved_entry": entry},
    )
    _append_log(log_path, transaction_id, "commit-complete", saved_entry=entry)
    return state


def reconcile_rollback(state_root, *, runner=subprocess.run, grubenv="/boot/grub/grubenv"):
    state_root = pathlib.Path(state_root)
    transactions = state_root / "transactions"
    candidates = []
    if transactions.is_dir():
        for directory in transactions.iterdir():
            state_path = directory / "state.json"
            if not state_path.is_file() or state_path.is_symlink():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if state.get("state") in {"rollback_armed", "rolling_back", "rolled_back"}:
                candidates.append((state_path.stat().st_mtime_ns, state))
    if not candidates:
        return {"reconciled": False, "reason": "no rollback transaction"}
    state = max(candidates, key=lambda item: item[0])[1]
    entry = _set_saved_entry(state["previous_slot"], runner, grubenv)
    log_path = transactions / state["transaction_id"] / "health.jsonl"
    _append_log(log_path, state["transaction_id"], "rollback-reconciled", saved_entry=entry)
    return {"reconciled": True, "transaction_id": state["transaction_id"], "saved_entry": entry}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("confirm-active", "reconcile"))
    parser.add_argument("--state-root", default="/var/lib/ming-update")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "reconcile":
            result = reconcile_rollback(arguments.state_root)
        else:
            pointer = json.loads(
                (pathlib.Path(arguments.state_root) / "active-transaction.json").read_text(encoding="utf-8")
            )
            result = confirm_transaction(arguments.state_root, pointer["transaction_id"])
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True, default=str))
        return 0
    except (HealthError, state_module.TransactionStateError, OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {"ok": False, "error_code": getattr(exc, "code", "E_HEALTH_SERVICE"), "message": str(exc)[:512]},
                ensure_ascii=True,
            )
        )
        return 7


if __name__ == "__main__":
    sys.exit(main())
