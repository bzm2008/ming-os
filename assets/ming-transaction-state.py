#!/usr/bin/env python3
"""Durable state machine for Ming OS directory-slot transactions."""

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import threading
import time
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


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
COMMIT_RECEIPT_SCHEMA = "ming.commit-receipt.v1"
COMMIT_PENDING_SCHEMA = "ming.commit-pending.v1"
_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class TransactionStateError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@contextlib.contextmanager
def transaction_lock(root):
    """Serialize staging after read-only preflight has completed.

    The lock is advisory but mandatory for every transaction writer.  It is
    retained as a regular file so a process crash releases the kernel lock
    without leaving an ambiguous staging reservation behind.
    """
    root = pathlib.Path(root)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise TransactionStateError("E_STATE_DURABILITY", "transaction state root is unsafe")
    try:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise TransactionStateError("E_LOCK_UNAVAILABLE", "cannot create transaction state root") from exc
    lock_path = root / ".transaction.lock"
    if lock_path.is_symlink():
        raise TransactionStateError("E_STATE_DURABILITY", "transaction lock path is unsafe")
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(str(lock_path.resolve()), threading.Lock())
    if not process_lock.acquire(blocking=False):
        raise TransactionStateError("E_BUSY", "another transaction is staging")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        process_lock.release()
        raise TransactionStateError("E_LOCK_UNAVAILABLE", "cannot open transaction lock") from exc
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TransactionStateError("E_STATE_DURABILITY", "transaction lock is not a regular file")
        if fcntl is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TransactionStateError("E_BUSY", "another transaction is staging") from exc
            locked = True
        elif msvcrt is not None:
            if metadata.st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise TransactionStateError("E_BUSY", "another transaction is staging") from exc
            locked = True
        else:
            raise TransactionStateError("E_LOCK_UNAVAILABLE", "platform does not provide file locking")
        yield
    finally:
        try:
            if locked:
                try:
                    if fcntl is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    elif msvcrt is not None:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            try:
                os.close(descriptor)
            finally:
                process_lock.release()


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


def _create_json_exclusive(path, value, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise TransactionStateError("E_STATE_DURABILITY", f"state path is a symlink: {path}")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


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

    @staticmethod
    def _current_pointer(state):
        return {
            "schema": "ming.current-slot.v1",
            "slot": state["candidate_slot"],
            "transaction_id": state["transaction_id"],
            "release_id": state["release_id"],
            "generation": state["generation"],
        }

    def _commit_receipt_path(self, transaction_id):
        return self._transaction_dir(transaction_id) / "commit-receipt.json"

    def _commit_pending_path(self):
        return self.root / "boot" / "commit-pending.json"

    def _remove_active_for(self, transaction_id):
        active = self.root / "active-transaction.json"
        if not active.exists():
            return
        value = self._load_json(active)
        if value.get("transaction_id") != transaction_id:
            raise TransactionStateError("E_STATE_RECONCILE", "active transaction pointer changed during commit")
        self._remove_active()

    def _remove_commit_pending(self):
        path = self._commit_pending_path()
        try:
            if path.is_symlink():
                raise TransactionStateError("E_STATE_DURABILITY", "commit reconciliation receipt is unsafe")
            path.unlink()
            _fsync_directory(path.parent)
        except FileNotFoundError:
            return

    def _load_commit_receipt(self, transaction_id):
        return self._load_json(self._commit_receipt_path(transaction_id), "E_STATE_RECONCILE")

    def _validate_commit_receipt(self, state, receipt):
        required = {
            "schema",
            "transaction_id",
            "release_id",
            "previous_slot",
            "candidate_slot",
            "generation",
            "health_token_sha256",
            "saved_entry",
            "timestamp",
        }
        if set(receipt) != required or receipt.get("schema") != COMMIT_RECEIPT_SCHEMA:
            raise TransactionStateError("E_STATE_RECONCILE", "commit receipt schema is invalid")
        for field in ("transaction_id", "release_id", "previous_slot", "candidate_slot", "generation"):
            if receipt.get(field) != state.get(field):
                raise TransactionStateError("E_STATE_RECONCILE", "commit receipt does not match transaction state")
        if not isinstance(receipt.get("health_token_sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", receipt["health_token_sha256"]) is None:
            raise TransactionStateError("E_STATE_RECONCILE", "commit receipt health token is invalid")
        expected_entry = {"legacy": "ming-legacy", "A": "ming-slot-a", "B": "ming-slot-b"}[state["candidate_slot"]]
        if receipt.get("saved_entry") != expected_entry or not isinstance(receipt.get("timestamp"), str):
            raise TransactionStateError("E_STATE_RECONCILE", "commit receipt boot evidence is invalid")

    def write_commit_receipt(self, transaction_id, *, health_token_sha256, saved_entry):
        state = self.load(transaction_id)
        if state["state"] != "committing":
            raise TransactionStateError("E_STATE_TRANSITION", "commit receipt requires a committing transaction")
        receipt = {
            "schema": COMMIT_RECEIPT_SCHEMA,
            "transaction_id": transaction_id,
            "release_id": state["release_id"],
            "previous_slot": state["previous_slot"],
            "candidate_slot": state["candidate_slot"],
            "generation": state["generation"],
            "health_token_sha256": health_token_sha256,
            "saved_entry": saved_entry,
            "timestamp": _timestamp(),
        }
        self._validate_commit_receipt(state, receipt)
        _atomic_json(self._commit_receipt_path(transaction_id), receipt)
        pending = dict(receipt)
        pending["schema"] = COMMIT_PENDING_SCHEMA
        _atomic_json(self._commit_pending_path(), pending)
        return receipt

    def commit_transaction(self, transaction_id, *, expected_generation, fault_hook=None, reconciled=False):
        state = self.load(transaction_id)
        if state["state"] == "committed":
            receipt = self._load_commit_receipt(transaction_id)
            self._validate_commit_receipt(state, receipt)
            _atomic_json(self.root / "current.json", self._current_pointer(state))
            self._remove_active_for(transaction_id)
            self._remove_commit_pending()
            return state
        if state["state"] != "committing" or state["generation"] != expected_generation:
            raise TransactionStateError("E_STATE_TRANSITION", "transaction is not ready for durable commit")
        receipt = self._load_commit_receipt(transaction_id)
        self._validate_commit_receipt(state, receipt)
        updated = dict(state)
        updated["state"] = "committed"
        updated["generation"] += 1
        updated["updated_at"] = _timestamp()
        updated["evidence"] = {
            "health_token_sha256": receipt["health_token_sha256"],
            "saved_entry": receipt["saved_entry"],
            "commit_receipt": hashlib.sha256(
                self._commit_receipt_path(transaction_id).read_bytes()
            ).hexdigest(),
        }
        _atomic_json(self.root / "current.json", self._current_pointer(updated))
        if fault_hook:
            fault_hook("after-current-pointer")
        transaction_dir = self._transaction_dir(transaction_id)
        _atomic_json(transaction_dir / "state.json", updated)
        if fault_hook:
            fault_hook("after-committed-state")
        _append_jsonl(
            transaction_dir / "events.jsonl",
            self._event(updated, state["state"], "commit-coordinator", updated["evidence"], reconciled=reconciled),
        )
        self._remove_active_for(transaction_id)
        self._remove_commit_pending()
        return updated

    def reconcile_pending_commit(self):
        pending_path = self._commit_pending_path()
        if not pending_path.exists():
            return None
        pending = self._load_json(pending_path, "E_STATE_RECONCILE")
        if pending.get("schema") != COMMIT_PENDING_SCHEMA:
            raise TransactionStateError("E_STATE_RECONCILE", "commit reconciliation receipt schema is invalid")
        transaction_id = pending.get("transaction_id")
        state = self.load(transaction_id)
        receipt = self._load_commit_receipt(transaction_id)
        expected_pending = dict(receipt)
        expected_pending["schema"] = COMMIT_PENDING_SCHEMA
        if pending != expected_pending:
            raise TransactionStateError("E_STATE_RECONCILE", "commit reconciliation receipt does not match transaction receipt")
        self._validate_commit_receipt(state, receipt)
        if state["state"] == "committing":
            return self.commit_transaction(
                transaction_id,
                expected_generation=state["generation"],
                reconciled=True,
            )
        if state["state"] == "committed":
            self.reconcile(transaction_id)
            _atomic_json(self.root / "current.json", self._current_pointer(state))
            self._remove_active_for(transaction_id)
            self._remove_commit_pending()
            return state
        raise TransactionStateError("E_STATE_RECONCILE", "commit receipt points to a non-committing transaction")

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
        try:
            _create_json_exclusive(active_path, self._active_pointer(state))
        except FileExistsError as exc:
            shutil.rmtree(transaction_dir, ignore_errors=True)
            raise TransactionStateError("E_BUSY", "another transaction is active") from exc
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
            _atomic_json(self.root / "current.json", self._current_pointer(updated))
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
        else:
            active = self.root / "active-transaction.json"
            if active.exists():
                pointer = self._load_json(active)
                if pointer.get("transaction_id") == transaction_id:
                    self._remove_active()
        return state


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
