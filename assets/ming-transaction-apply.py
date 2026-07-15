#!/usr/bin/env python3
"""Preflight and apply a verified payload to an inactive Ming OS slot."""

import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import tarfile
import uuid


HERE = pathlib.Path(__file__).resolve().parent


def _load_sibling(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state_module = _load_sibling("ming-transaction-state.py", "ming_transaction_state_runtime")
rollback_module = _load_sibling("ming-transaction-rollback.py", "ming_transaction_rollback_runtime")
slot_module = _load_sibling("ming-transaction-slot.py", "ming_transaction_slot_runtime")


class ApplyError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _append_log(path, transaction_id, event, **fields):
    value = {
        "schema": "ming.transaction-engine-log.v1",
        "transaction_id": transaction_id,
        "event": event,
        "timestamp": _timestamp(),
        **fields,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


class PayloadArchive:
    def __init__(self, path, index):
        self.path = pathlib.Path(path)
        self.index = index
        self.members = {}

    def required_hashes(self):
        hashes = set()
        for entry in self.index["entries"]:
            if entry["type"] == "file":
                hashes.add(entry["blob"].removeprefix("sha256:"))
        for package in self.index["packages"]:
            hashes.add(package["blob"].removeprefix("sha256:"))
        return hashes

    def validate(self):
        required = self.required_hashes()
        try:
            with tarfile.open(self.path, "r:*") as archive:
                members = archive.getmembers()
                for member in members:
                    if not member.isreg() or not member.name.startswith("objects/"):
                        raise ApplyError("E_CONTENT_POLICY", "payload contains a non-object member")
                    digest = member.name.removeprefix("objects/")
                    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                        raise ApplyError("E_CONTENT_POLICY", "payload object name is invalid")
                    if digest in self.members:
                        raise ApplyError("E_CONTENT_POLICY", "payload contains a duplicate object")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ApplyError("E_CONTENT_POLICY", "payload object cannot be read")
                    actual = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        actual.update(block)
                    if actual.hexdigest() != digest:
                        raise ApplyError("E_ARTIFACT_HASH", "payload object SHA256 mismatch")
                    self.members[digest] = member.name
        except ApplyError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise ApplyError("E_CONTENT_POLICY", f"payload container is invalid: {exc}") from exc
        if set(self.members) != required:
            raise ApplyError("E_CONTENT_POLICY", "payload object inventory differs from content index")
        return self

    def copy_object(self, digest, output):
        name = self.members.get(digest)
        if not name:
            raise ApplyError("E_CONTENT_POLICY", "payload object was not validated")
        with tarfile.open(self.path, "r:*") as archive:
            member = archive.getmember(name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ApplyError("E_CONTENT_POLICY", "payload object cannot be opened")
            with pathlib.Path(output).open("wb") as handle:
                shutil.copyfileobj(stream, handle, 1024 * 1024)
                handle.flush()
                os.fsync(handle.fileno())
        if _sha256(output) != digest:
            raise ApplyError("E_ARTIFACT_HASH", "payload object changed during application")


def _remove_target(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _set_metadata(path, entry):
    if not path.is_symlink():
        os.chmod(path, entry["mode"])
    if hasattr(os, "chown") and os.geteuid() == 0:
        os.chown(path, entry["uid"], entry["gid"], follow_symlinks=not path.is_symlink())


def apply_payload(*, candidate_root, transaction_dir, archive, index, fault_hook=None):
    if index.get("packages"):
        raise ApplyError("E_CONTENT_POLICY", "offline package transactions are not enabled in the minimal engine")
    candidate_root = pathlib.Path(candidate_root)
    journal = rollback_module.RollbackJournal(transaction_dir, candidate_root)
    mutations = 0
    try:
        for entry in index["entries"]:
            relative = entry["path"]
            target = candidate_root.joinpath(*relative.split("/"))
            if entry["config_policy"] == "preserve" and (target.exists() or target.is_symlink()):
                continue
            if entry["config_policy"] == "replace-if-unmodified":
                expected = entry.get("base_sha256")
                if not expected or not target.is_file() or _sha256(target) != expected:
                    continue
            journal.capture(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            kind = entry["type"]
            if kind == "directory":
                if target.exists() and not target.is_dir():
                    _remove_target(target)
                target.mkdir(parents=True, exist_ok=True)
            elif kind == "symlink":
                _remove_target(target)
                target.symlink_to(entry["target"])
            elif kind == "file":
                temporary = target.with_name(f".{target.name}.ming-tmp-{uuid.uuid4().hex}")
                archive.copy_object(entry["blob"].removeprefix("sha256:"), temporary)
                os.replace(temporary, target)
            else:
                raise ApplyError("E_CONTENT_POLICY", "unsupported content type")
            _set_metadata(target, entry)
            mutations += 1
            if fault_hook and mutations == 1:
                fault_hook("after-first-mutation")
        for relative in index["deletions"]:
            target = candidate_root.joinpath(*relative.split("/"))
            journal.capture(relative)
            _remove_target(target)
            mutations += 1
            if fault_hook and mutations == 1:
                fault_hook("after-first-mutation")
    except Exception as exc:
        try:
            journal.rollback(reason=str(exc))
        except Exception as rollback_exc:
            raise ApplyError("E_ROLLBACK_STATE", f"candidate apply and rollback failed: {rollback_exc}") from exc
        if isinstance(exc, ApplyError):
            raise
        raise ApplyError("E_PACKAGE_APPLY", f"candidate application interrupted: {exc}") from exc


def prepare_candidate(
    *,
    plan,
    payload_path,
    active_root,
    state_root,
    transaction_id,
    available_bytes=None,
    fault_hook=None,
):
    payload_path = pathlib.Path(payload_path)
    verified = plan.get("verified_artifacts") if isinstance(plan, dict) else None
    if not isinstance(verified, dict) or verified.get("payload_sha256") != _sha256(payload_path):
        raise ApplyError("E_ARTIFACT_HASH", "payload does not match the verified release plan")
    index = plan.get("content_index")
    if not isinstance(index, dict) or index.get("release_id") != plan.get("release_id"):
        raise ApplyError("E_CONTENT_POLICY", "verified content index is missing or mismatched")
    if index.get("packages"):
        raise ApplyError("E_CONTENT_POLICY", "offline package transactions are not enabled in the minimal engine")
    archive = PayloadArchive(payload_path, index).validate()
    try:
        space = slot_module.validate_space(
            active_root=active_root,
            state_root=state_root,
            payload_size=payload_path.stat().st_size,
            reserve_bytes=plan["space"]["reserve_bytes"],
            minimum_free_bytes=plan["space"]["minimum_free_bytes"],
            available_bytes=available_bytes,
        )
        previous_slot, candidate_slot = slot_module.select_slots(state_root)
    except slot_module.SlotError as exc:
        raise ApplyError(exc.code, exc.message, exc.details) from exc

    store = state_module.TransactionStore(state_root)
    state = store.create_transaction(
        transaction_id=transaction_id,
        release_id=plan["release_id"],
        previous_slot=previous_slot,
        candidate_slot=candidate_slot,
    )
    transaction_dir = pathlib.Path(state_root) / "transactions" / transaction_id
    engine_log = transaction_dir / "engine.jsonl"
    _append_log(engine_log, transaction_id, "preflight-complete", space=space)
    state = store.transition(transaction_id, "verified", writer="verifier", expected_generation=state["generation"], evidence=plan["verified_artifacts"])
    state = store.transition(transaction_id, "staging", writer="slot-manager", expected_generation=state["generation"], evidence=space)
    try:
        candidate = slot_module.clone_active_root(
            active_root=active_root,
            state_root=state_root,
            candidate_slot=candidate_slot,
            transaction_id=transaction_id,
        )
        _append_log(engine_log, transaction_id, "clone-complete", candidate_slot=candidate_slot)
        apply_payload(
            candidate_root=candidate,
            transaction_dir=transaction_dir,
            archive=archive,
            index=index,
            fault_hook=fault_hook,
        )
        candidate_digest = slot_module.tree_digest(candidate)
        seal_path = transaction_dir / "candidate-seal.json"
        seal_path.write_text(
            json.dumps({"schema": "ming.candidate-seal.v1", "sha256": candidate_digest}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        state = store.transition(
            transaction_id,
            "staged",
            writer="candidate-applicator",
            expected_generation=state["generation"],
            evidence={"candidate_sha256": candidate_digest},
        )
        _append_log(engine_log, transaction_id, "candidate-staged", candidate_slot=candidate_slot, candidate_sha256=candidate_digest)
        return {
            "transaction_id": transaction_id,
            "state": state["state"],
            "previous_slot": previous_slot,
            "candidate_slot": candidate_slot,
            "candidate_root": str(candidate),
            "log_path": str(engine_log),
        }
    except Exception as exc:
        _append_log(engine_log, transaction_id, "staging-failed", error_code=getattr(exc, "code", "E_PACKAGE_APPLY"), reason=str(exc)[:512])
        current = store.load(transaction_id)
        if current["state"] in {"new", "verified", "staging", "staged"}:
            current = store.transition(transaction_id, "aborting", writer="engine", expected_generation=current["generation"], evidence={"reason": str(exc)[:512]})
            store.transition(transaction_id, "aborted", writer="engine", expected_generation=current["generation"], evidence={"reason": str(exc)[:512]})
        if isinstance(exc, ApplyError):
            raise
        if isinstance(exc, slot_module.SlotError):
            raise ApplyError(exc.code, exc.message, exc.details) from exc
        raise ApplyError("E_PACKAGE_APPLY", f"candidate staging failed: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit("This module is used by the Ming update engine.")
