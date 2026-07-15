#!/usr/bin/env python3
"""Durable state machine for Ming OS directory-slot transactions."""

import datetime
import json
import os
import pathlib
import re
import time
import uuid


TRANSITIONS = {
    "new": {"verified", "aborting"},
    "verified": {"staging", "aborting"},
    "staging": {"staged", "aborting"},
    "staged": {"armed", "aborting"},
    "armed": {"booting", "rollback_armed"},
    "booting": {"pending_health", "rollback_armed"},
    "pending_health": {"committing", "rollback_armed"},
    "committing": {"committed", "rollback_armed"},
    "aborting": {"aborted"},
    "rollback_armed": {"rolling_back"},
    "rolling_back": {"rolled_back"},
    "aborted": set(),
    "rolled_back": set(),
    "committed": set(),
}
WRITERS = {
    "verified": "verifier",
    "staging": "slot-manager",
    "staged": "candidate-applicator",
    "armed": "boot-coordinator",
    "booting": "initramfs",
    "pending_health": "health-service",
    "committing": "health-confirmer",
    "committed": "commit-coordinator",
    "aborting": "engine",
    "aborted": "engine",
    "rollback_armed": "rollback-service",
    "rolling_back": "initramfs",
    "rolled_back": "rollback-service",
}
TERMINAL = {"aborted", "rolled_back", "committed"}


class TransactionStateError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_directory(path):
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path, value, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise TransactionStateError("E_STATE_DURABILITY", f"state path is a symlink: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _append_jsonl(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise TransactionStateError("E_STATE_DURABILITY", f"event path is a symlink: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


class TransactionStore:
    def __init__(self, root="/var/lib/ming-update"):
        self.root = pathlib.Path(root)
        self.transactions = self.root / "transactions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.transactions.mkdir(parents=True, exist_ok=True)

    def _transaction_dir(self, transaction_id):
        if not isinstance(transaction_id, str) or re.fullmatch(r"[A-Za-z0-9._-]{3,128}", transaction_id) is None:
            raise TransactionStateError("E_ARGUMENT", "transaction ID is invalid")
        return self.transactions / transaction_id

    def _load_json(self, path, code="E_STATE_SCHEMA"):
        path = pathlib.Path(path)
        if path.is_symlink():
            raise TransactionStateError(code, f"unsafe state path: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TransactionStateError(code, f"cannot read transaction state: {exc}") from exc
        if not isinstance(value, dict):
            raise TransactionStateError(code, "transaction state is not an object")
        return value

    def load(self, transaction_id):
        state = self._load_json(self._transaction_dir(transaction_id) / "state.json")
        if state.get("schema") != "ming.transaction-state.v1" or state.get("transaction_id") != transaction_id:
            raise TransactionStateError("E_STATE_SCHEMA", "transaction state identity is invalid")
        if state.get("state") not in TRANSITIONS or not isinstance(state.get("generation"), int):
            raise TransactionStateError("E_STATE_SCHEMA", "transaction state fields are invalid")
        return state

    def _event(self, state, from_state, writer, evidence=None, reconciled=False):
        return {
            "schema": "ming.transaction-event.v1",
            "transaction_id": state["transaction_id"],
            "release_id": state["release_id"],
            "generation": state["generation"],
            "from_state": from_state,
            "to_state": state["state"],
            "writer": writer,
            "evidence": evidence or {},
            "reconciled": bool(reconciled),
            "timestamp": _timestamp(),
            "monotonic_ns": time.monotonic_ns(),
        }

    def _active_pointer(self, state):
        return {
            "schema": "ming.active-transaction.v1",
            "transaction_id": state["transaction_id"],
            "candidate_slot": state["candidate_slot"],
            "previous_slot": state["previous_slot"],
            "generation": state["generation"],
            "state": state["state"],
        }

    def _remove_active(self):
        active = self.root / "active-transaction.json"
        try:
            if active.is_symlink():
                raise TransactionStateError("E_STATE_DURABILITY", "active transaction pointer is unsafe")
            active.unlink()
            _fsync_directory(active.parent)
        except FileNotFoundError:
            pass

    def create_transaction(self, *, transaction_id, release_id, previous_slot, candidate_slot):
        active_path = self.root / "active-transaction.json"
        if active_path.exists() or active_path.is_symlink():
            active = self._load_json(active_path)
            try:
                active_state = self.load(active.get("transaction_id", ""))
            except TransactionStateError:
                raise TransactionStateError("E_BUSY", "an unresolved transaction exists")
            if active_state["state"] not in TERMINAL:
                raise TransactionStateError("E_BUSY", "another transaction is active")
            self._remove_active()
        if previous_slot not in {"legacy", "A", "B"} or candidate_slot not in {"A", "B"} or previous_slot == candidate_slot:
            raise TransactionStateError("E_ARGUMENT", "slot relationship is invalid")
        transaction_dir = self._transaction_dir(transaction_id)
        try:
            transaction_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise TransactionStateError("E_BUSY", "transaction ID already exists") from exc
        state = {
            "schema": "ming.transaction-state.v1",
            "transaction_id": transaction_id,
            "release_id": release_id,
            "previous_slot": previous_slot,
            "candidate_slot": candidate_slot,
            "state": "new",
            "generation": 1,
            "created_at": _timestamp(),
            "updated_at": _timestamp(),
            "evidence": {},
        }
        _atomic_json(transaction_dir / "state.json", state)
        _append_jsonl(transaction_dir / "events.jsonl", self._event(state, None, "engine"))
        _atomic_json(active_path, self._active_pointer(state))
        return state

    def transition(
        self,
        transaction_id,
        new_state,
        *,
        writer,
        expected_generation,
        evidence=None,
        fault_hook=None,
    ):
        state = self.load(transaction_id)
        if state["generation"] != expected_generation:
            raise TransactionStateError("E_STATE_STALE", "transaction generation changed")
        if new_state == state["state"]:
            return state
        if new_state not in TRANSITIONS[state["state"]]:
            raise TransactionStateError("E_STATE_TRANSITION", f"illegal transition {state['state']} -> {new_state}")
        if WRITERS.get(new_state) != writer:
            raise TransactionStateError("E_STATE_WRITER", f"writer {writer} cannot enter {new_state}")
        previous = state["state"]
        updated = dict(state)
        updated["state"] = new_state
        updated["generation"] += 1
        updated["updated_at"] = _timestamp()
        updated["evidence"] = evidence or {}
        transaction_dir = self._transaction_dir(transaction_id)
        _atomic_json(transaction_dir / "state.json", updated)
        if fault_hook:
            fault_hook("after-state-replace")
        _append_jsonl(transaction_dir / "events.jsonl", self._event(updated, previous, writer, evidence))
        if new_state == "committed":
            _atomic_json(
                self.root / "current.json",
                {
                    "schema": "ming.current-slot.v1",
                    "slot": updated["candidate_slot"],
                    "transaction_id": transaction_id,
                    "release_id": updated["release_id"],
                    "generation": updated["generation"],
                },
            )
        if new_state in TERMINAL:
            self._remove_active()
        else:
            _atomic_json(self.root / "active-transaction.json", self._active_pointer(updated))
        return updated

    def reconcile(self, transaction_id):
        state = self.load(transaction_id)
        event_path = self._transaction_dir(transaction_id) / "events.jsonl"
        generations = []
        try:
            for line in event_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                generations.append(event.get("generation"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TransactionStateError("E_STATE_RECONCILE", f"event log is invalid: {exc}") from exc
        if not generations or any(not isinstance(value, int) for value in generations):
            raise TransactionStateError("E_STATE_RECONCILE", "event generations are invalid")
        if generations != list(range(1, max(generations) + 1)):
            raise TransactionStateError("E_STATE_RECONCILE", "event generations are not contiguous")
        if generations[-1] > state["generation"]:
            raise TransactionStateError("E_STATE_RECONCILE", "event log is ahead of state")
        if generations[-1] < state["generation"]:
            if generations[-1] != state["generation"] - 1:
                raise TransactionStateError("E_STATE_RECONCILE", "more than one event is missing")
            _append_jsonl(
                event_path,
                self._event(state, None, "reconciler", state.get("evidence"), reconciled=True),
            )
        if state["state"] not in TERMINAL:
            _atomic_json(self.root / "active-transaction.json", self._active_pointer(state))
        return state


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
