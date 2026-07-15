#!/usr/bin/env python3
"""GRUB arming and initramfs slot selection for Ming OS transactions."""

import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import subprocess
import sys


HERE = pathlib.Path(__file__).resolve().parent


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "ming_transaction_state_boot_runtime", HERE / "ming-transaction-state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state_module = _load_state_module()


class BootError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _append_jsonl(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _run(command, runner):
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootError("E_GRUB_WRITE", f"bootloader command failed: {exc}") from exc
    if result.returncode != 0:
        raise BootError(
            "E_GRUB_WRITE",
            "bootloader command returned an error",
            {"stderr": (result.stderr or "")[-512:]},
        )
    return result


def _parse_grubenv(value):
    result = {}
    for line in value.splitlines():
        key, separator, item = line.partition("=")
        if separator:
            result[key] = item
    return result


def _read_grubenv(runner, grubenv):
    result = _run(["grub-editenv", str(grubenv), "list"], runner)
    return _parse_grubenv(result.stdout)


def arm_transaction(state_root, transaction_id, *, runner=subprocess.run, grubenv="/boot/grub/grubenv"):
    store = state_module.TransactionStore(state_root)
    state = store.load(transaction_id)
    if state["state"] != "staged":
        raise BootError("E_STATE_TRANSITION", "only a staged transaction can be armed")
    entry = {"A": "ming-slot-a", "B": "ming-slot-b"}[state["candidate_slot"]]
    assignments = [
        f"ming_transaction_id={transaction_id}",
        f"ming_candidate_slot={state['candidate_slot']}",
        f"ming_previous_slot={state['previous_slot']}",
    ]
    _run(["grub-editenv", str(grubenv), "set", *assignments], runner)
    values = _read_grubenv(runner, grubenv)
    expected = {
        "ming_transaction_id": transaction_id,
        "ming_candidate_slot": state["candidate_slot"],
        "ming_previous_slot": state["previous_slot"],
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise BootError("E_GRUB_READBACK", "transaction metadata readback differs")
    _run(["grub-reboot", entry], runner)
    values = _read_grubenv(runner, grubenv)
    if values.get("next_entry") != entry:
        raise BootError("E_GRUB_READBACK", "one-shot boot entry readback differs")
    return store.transition(
        transaction_id,
        "armed",
        writer="boot-coordinator",
        expected_generation=state["generation"],
        evidence={"entry": entry, "grubenv": str(grubenv)},
    )


def _load_json(path):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise BootError("E_STATE_SCHEMA", f"unsafe boot state path: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootError("E_STATE_SCHEMA", f"cannot read boot state: {exc}") from exc
    if not isinstance(value, dict):
        raise BootError("E_STATE_SCHEMA", "boot state is not an object")
    return value


def _slot_root(state_root, physical_root, slot):
    if slot == "legacy":
        return pathlib.Path(physical_root).resolve()
    return pathlib.Path(state_root).resolve() / "slots" / slot / "root"


def _active_transaction_id(state_root):
    pointer = pathlib.Path(state_root) / "active-transaction.json"
    if not pointer.exists():
        return None
    return _load_json(pointer).get("transaction_id")


def _committed_slot(state_root):
    current = pathlib.Path(state_root) / "current.json"
    if not current.exists():
        return "legacy"
    slot = _load_json(current).get("slot")
    if slot not in {"legacy", "A", "B"}:
        raise BootError("E_STATE_SCHEMA", "committed slot is invalid")
    return slot


def _rollback(store, state):
    if state["state"] in {"new", "verified", "staging", "staged"}:
        state = store.transition(
            state["transaction_id"],
            "aborting",
            writer="engine",
            expected_generation=state["generation"],
            evidence={"reason": "unarmed candidate boot"},
        )
        return store.transition(
            state["transaction_id"],
            "aborted",
            writer="engine",
            expected_generation=state["generation"],
            evidence={"reason": "unarmed candidate boot"},
        )
    if state["state"] in {"armed", "booting", "pending_health", "committing"}:
        state = store.transition(
            state["transaction_id"],
            "rollback_armed",
            writer="rollback-service",
            expected_generation=state["generation"],
            evidence={"reason": "candidate boot interrupted or rejected"},
        )
        state = store.transition(
            state["transaction_id"],
            "rolling_back",
            writer="initramfs",
            expected_generation=state["generation"],
            evidence={"reason": "previous slot selected"},
        )
        return store.transition(
            state["transaction_id"],
            "rolled_back",
            writer="rollback-service",
            expected_generation=state["generation"],
            evidence={"reason": "previous slot selected"},
        )
    return state


def _validate_candidate(state_root, state):
    slot_dir = pathlib.Path(state_root) / "slots" / state["candidate_slot"]
    slot = _load_json(slot_dir / "slot.json")
    seal = _load_json(pathlib.Path(state_root) / "transactions" / state["transaction_id"] / "candidate-seal.json")
    if slot.get("schema") != "ming.slot.v1" or slot.get("slot") != state["candidate_slot"] or slot.get("transaction_id") != state["transaction_id"]:
        raise BootError("E_SLOT_MISMATCH", "candidate slot sentinel differs")
    if seal.get("schema") != "ming.candidate-seal.v1" or not isinstance(seal.get("sha256"), str) or len(seal["sha256"]) != 64:
        raise BootError("E_CANDIDATE_SEAL", "candidate seal is invalid")
    root = slot_dir / "root"
    if not root.is_dir() or root.is_symlink():
        raise BootError("E_SLOT_MOUNT", "candidate root is unavailable")
    return root


def _attempt_count(state_root, transaction_id):
    path = pathlib.Path(state_root) / "boot" / "attempts.jsonl"
    if not path.exists():
        return 0
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("transaction_id") == transaction_id:
                count += 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootError("E_STATE_RECONCILE", f"boot attempt log is invalid: {exc}") from exc
    return count


def _record_boot(state_root, event, **fields):
    _append_jsonl(
        pathlib.Path(state_root) / "boot" / "initramfs.jsonl",
        {"schema": "ming.initramfs-log.v1", "event": event, "timestamp": _timestamp(), **fields},
    )


def select_root(*, state_root, physical_root, requested_slot):
    if requested_slot not in {"legacy", "A", "B"}:
        raise BootError("E_SLOT_MISMATCH", "requested slot is invalid")
    state_root = pathlib.Path(state_root)
    committed = _committed_slot(state_root)
    transaction_id = _active_transaction_id(state_root)
    store = state_module.TransactionStore(state_root)
    state = store.load(transaction_id) if transaction_id else None

    if requested_slot == committed:
        action = "boot-committed"
        if state and state["state"] in {"booting", "pending_health", "committing"}:
            _rollback(store, state)
            action = "rollback-interrupted"
        selected = _slot_root(state_root, physical_root, committed)
        _record_boot(state_root, action, requested_slot=requested_slot, selected_slot=committed, transaction_id=transaction_id)
        return {"selected_root": str(selected), "selected_slot": committed, "action": action, "transaction_id": transaction_id}

    if state and state["state"] == "armed" and requested_slot == state["candidate_slot"]:
        try:
            candidate = _validate_candidate(state_root, state)
            if _attempt_count(state_root, transaction_id) >= 1:
                raise BootError("E_SLOT_MISMATCH", "candidate boot attempt is exhausted")
            _append_jsonl(
                state_root / "boot" / "attempts.jsonl",
                {
                    "schema": "ming.boot-attempt.v1",
                    "transaction_id": transaction_id,
                    "slot": requested_slot,
                    "timestamp": _timestamp(),
                },
            )
            state = store.transition(
                transaction_id,
                "booting",
                writer="initramfs",
                expected_generation=state["generation"],
                evidence={"requested_slot": requested_slot, "attempt": 1},
            )
            _record_boot(state_root, "boot-candidate", requested_slot=requested_slot, selected_slot=requested_slot, transaction_id=transaction_id)
            return {"selected_root": str(candidate), "selected_slot": requested_slot, "action": "boot-candidate", "transaction_id": transaction_id}
        except (BootError, state_module.TransactionStateError):
            state = store.load(transaction_id)
            _rollback(store, state)
    elif state:
        _rollback(store, state)

    selected = _slot_root(state_root, physical_root, committed)
    _record_boot(state_root, "fallback-previous", requested_slot=requested_slot, selected_slot=committed, transaction_id=transaction_id)
    return {"selected_root": str(selected), "selected_slot": committed, "action": "fallback-previous", "transaction_id": transaction_id}


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select")
    select.add_argument("--state-root", required=True)
    select.add_argument("--physical-root", required=True)
    select.add_argument("--requested-slot", required=True, choices=("legacy", "A", "B"))
    arguments = parser.parse_args(argv)
    try:
        result = select_root(
            state_root=arguments.state_root,
            physical_root=arguments.physical_root,
            requested_slot=arguments.requested_slot,
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=True, separators=(",", ":")))
        return 0
    except BootError as exc:
        print(json.dumps({"ok": False, "error_code": exc.code, "message": exc.message}, ensure_ascii=True, separators=(",", ":")))
        return 6


if __name__ == "__main__":
    sys.exit(main())
