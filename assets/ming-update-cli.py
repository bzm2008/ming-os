#!/usr/bin/env python3
"""Public JSON-only adapter for Ming OS transactional updates."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid


HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_STATE_ROOT = pathlib.Path("/var/lib/ming-update")
DEFAULT_CACHE_ROOT = pathlib.Path("/var/cache/ming-update")
DEFAULT_KEYRING = pathlib.Path("/usr/share/ming-update/trust/release-keyring.gpg")
DEFAULT_KEY_POLICY = pathlib.Path("/usr/share/ming-update/trust/key-policy.json")
DEFAULT_DISCOVERY_URL = "https://ming.scallion.uno/api/onion-update/check"
# Kept disabled until the replacement domain is registered/production ready.  The
# signed discovery response remains the source of truth when this is enabled.
FALLBACK_DISCOVERY_URL = "https://ming.sca-hub.cn/api/onion-update/check"
DISCOVERY_FALLBACK_ENV = "MING_UPDATE_ENABLE_FALLBACK_DOMAIN"
RELEASE_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
TRANSACTION_ID = re.compile(r"^[A-Za-z0-9._-]{3,128}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._-]+)?$")
HTTPS_URL = re.compile(r"^https://[^/?#@]+(?:/[^?#]*)?$")
CONTENT_ADDRESSED_OBJECT = re.compile(r"/([0-9a-f]{64})(\\.sig)?$")
DISCOVERY_BASE_FIELDS = frozenset({
    "schema", "available", "current_version", "architecture", "capability", "delivery",
})

EXIT_CODES = {
    "E_ARGUMENT": 2,
    "E_TRANSACTION_NOT_FOUND": 2,
    "E_BUSY": 3,
    "E_NOT_CANCELABLE": 3,
    "E_SPACE": 3,
    "E_SOURCE_UNSUPPORTED": 3,
    "E_BOOTSTRAP_REQUIRED": 3,
    "E_NETWORK": 3,
    "E_PROTOCOL_UNSUPPORTED": 3,
    "E_PRIVILEGE": 3,
    "E_MANIFEST_SIGNATURE": 4,
    "E_MANIFEST_SCHEMA": 4,
    "E_MANIFEST_EXPIRED": 4,
    "E_ARTIFACT_SIGNATURE": 4,
    "E_ARTIFACT_HASH": 4,
    "E_CONTENT_POLICY": 4,
    "E_KEY_POLICY": 4,
    "E_CLONE": 5,
    "E_PACKAGE_STATE": 5,
    "E_PACKAGE_APPLY": 5,
    "E_PROTECTED_PATH_CHANGED": 5,
    "E_CANDIDATE_SEAL": 5,
    "E_GRUB_WRITE": 6,
    "E_GRUB_READBACK": 6,
    "E_INITRAMFS_CONTRACT": 6,
    "E_SLOT_MOUNT": 6,
    "E_SLOT_MISMATCH": 6,
    "E_HEALTH_TIMEOUT": 7,
    "E_HEALTH_ROOT": 7,
    "E_HEALTH_PACKAGES": 7,
    "E_HEALTH_SERVICE": 7,
    "E_HEALTH_DESKTOP_PROBE": 7,
    "E_ROLLBACK_GRUB": 8,
    "E_ROLLBACK_STATE": 8,
    "E_ROLLBACK_SLOT": 8,
    "E_STATE_SCHEMA": 9,
    "E_STATE_TRANSITION": 9,
    "E_STATE_DURABILITY": 9,
    "E_STATE_RECONCILE": 9,
    "E_BOOTSTRAP_SIGNATURE": 10,
    "E_BOOTSTRAP_VERSION": 10,
}
CLI_COMMANDS = frozenset({"status", "check", "apply", "cancel", "doctor", "logs"})


def _load_sibling(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_module = _load_sibling("ming-transaction-verify.py", "ming_update_cli_verify")
state_module = _load_sibling("ming-transaction-state.py", "ming_update_cli_state")
engine_module = _load_sibling("ming-transaction-engine.py", "ming_update_cli_engine")
boot_module = _load_sibling("ming-transaction-boot.py", "ming_update_cli_boot")
bootstrap_module = _load_sibling("ming-ota-bootstrap-capability.py", "ming_update_cli_bootstrap")
slot_module = _load_sibling("ming-transaction-slot.py", "ming_update_cli_slot")


class UpdateError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ArgumentParseError(Exception):
    """Raised instead of allowing argparse to write a non-JSON error."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentParseError(message)

    def exit(self, status=0, message=None):
        if status:
            raise ArgumentParseError(message or "invalid update command")
        raise ArgumentParseError(message or "update help is unavailable")


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _architecture():
    machine = platform.machine().lower()
    return "amd64" if machine in {"amd64", "x86_64"} else machine


def _version():
    for path in (pathlib.Path("/etc/ming-os-version"), pathlib.Path("/etc/ming-version"), pathlib.Path("/etc/os-release")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.name != "os-release":
            value = content.strip()
            if value:
                return value
        for line in content.splitlines():
            if line.startswith("VERSION_ID="):
                return line.partition("=")[2].strip().strip('"')
    return "unknown"


def _safe_https(value, field):
    if not isinstance(value, str) or HTTPS_URL.fullmatch(value) is None:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} is missing")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} must be credential-free HTTPS")
    return value


def _content_addressed_https(value, field, *, signature=False, expected_sha256=None):
    url = _safe_https(value, field)
    parsed = urllib.parse.urlsplit(url)
    match = CONTENT_ADDRESSED_OBJECT.search(parsed.path)
    if match is None or (not signature and match.group(2) is not None):
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} must be content-addressed")
    if expected_sha256 is not None and match.group(1) != expected_sha256:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} does not match its declared SHA256")
    return url


def _safe_sha256(value, field):
    if not isinstance(value, str) or SHA256.fullmatch(value.lower()) is None:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} is invalid")
    return value.lower()


def _atomic_json(path, value, mode=0o600):
    path = pathlib.Path(path)
    if path.exists() and path.is_symlink():
        raise UpdateError("E_STATE_DURABILITY", "update cache path is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _read_json(path):
    path = pathlib.Path(path)
    if not path.is_file() or path.is_symlink():
        raise UpdateError("E_STATE_SCHEMA", "update cache is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("E_STATE_SCHEMA", "update cache is invalid") from exc
    if not isinstance(value, dict):
        raise UpdateError("E_STATE_SCHEMA", "update cache has an invalid shape")
    return value


def _discovery_bootstrap(value):
    if not isinstance(value, dict) or set(value) != {"url", "sha256", "signature_url", "fingerprint"}:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "bootstrap metadata is missing")
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[A-Fa-f0-9]{40}", fingerprint) is None:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "bootstrap fingerprint is invalid")
    return {
        "url": _safe_https(value.get("url"), "bootstrap.url"),
        "sha256": _safe_sha256(value.get("sha256"), "bootstrap.sha256"),
        "signature_url": _safe_https(value.get("signature_url"), "bootstrap.signature_url"),
        "fingerprint": fingerprint.upper(),
    }


def _discovery_version(value, field):
    if not isinstance(value, str) or VERSION.fullmatch(value) is None:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", f"{field} is invalid")
    return value


def validate_discovery(value, architecture, current_version=None):
    if not isinstance(value, dict) or value.get("schema") != "ming.update.discovery.v1":
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update discovery schema is unsupported")
    available = value.get("available")
    if not isinstance(available, bool):
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update availability is invalid")
    reported_version = _discovery_version(value.get("current_version"), "discovery current version")
    if current_version is not None and reported_version != current_version:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "discovery current version differs from this system")
    if value.get("architecture") != architecture or architecture != "amd64":
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update architecture is incompatible")
    capability = value.get("capability")
    delivery = value.get("delivery")
    if delivery == "bootstrap":
        if set(value) != DISCOVERY_BASE_FIELDS | {"bootstrap"}:
            raise UpdateError("E_PROTOCOL_UNSUPPORTED", "bootstrap discovery fields are invalid")
        if not available or capability is not None:
            raise UpdateError("E_PROTOCOL_UNSUPPORTED", "bootstrap discovery capability is invalid")
        return {"delivery": "bootstrap", "bootstrap": _discovery_bootstrap(value.get("bootstrap"))}
    if delivery == "none":
        if set(value) != DISCOVERY_BASE_FIELDS:
            raise UpdateError("E_PROTOCOL_UNSUPPORTED", "no-update discovery fields are invalid")
        if available or capability != "transactional-slot-v1":
            raise UpdateError("E_PROTOCOL_UNSUPPORTED", "no-update discovery capability is invalid")
        return {"delivery": "none", "available": False, "release_notes": ""}
    if delivery != "transactional-slot-v1" or capability != "transactional-slot-v1":
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update delivery is unsupported")
    if set(value) != DISCOVERY_BASE_FIELDS | {
        "release_id", "version", "minimum_bootstrap", "manifest_url", "manifest_signature_url", "manifest_sha256",
    }:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "transactional discovery fields are invalid")
    if not available:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "transactional availability is invalid")
    release_id = value.get("release_id")
    if not isinstance(release_id, str) or RELEASE_ID.fullmatch(release_id) is None:
        raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update release ID is invalid")
    result = {
        "delivery": delivery,
        "available": True,
        "release_id": release_id,
        "version": _discovery_version(value.get("version"), "update version"),
        "minimum_bootstrap": _discovery_version(value.get("minimum_bootstrap"), "minimum bootstrap version"),
        "release_notes": value.get("release_notes") if isinstance(value.get("release_notes"), str) else "",
    }
    manifest_sha256 = _safe_sha256(value.get("manifest_sha256"), "manifest_sha256")
    result.update({
        "manifest_url": _content_addressed_https(
            value.get("manifest_url"), "manifest_url", expected_sha256=manifest_sha256,
        ),
        "manifest_signature_url": _content_addressed_https(
            value.get("manifest_signature_url"), "manifest_signature_url", signature=True,
        ),
        "manifest_sha256": manifest_sha256,
    })
    return result


class UpdateController:
    def __init__(
        self,
        *,
        state_root=DEFAULT_STATE_ROOT,
        cache_root=DEFAULT_CACHE_ROOT,
        keyring=DEFAULT_KEYRING,
        key_policy=DEFAULT_KEY_POLICY,
        current_version=None,
        architecture=None,
        kernel_release=None,
        capability_loader=None,
        discovery_fetcher=None,
        discovery_fallback_enabled=None,
        artifact_fetcher=None,
        active_root="/",
        log_root=None,
    ):
        self.state_root = pathlib.Path(state_root)
        self.cache_root = pathlib.Path(cache_root)
        self.keyring = pathlib.Path(keyring)
        self.key_policy = pathlib.Path(key_policy)
        self.current_version = current_version or _version()
        self.architecture = architecture or _architecture()
        self.kernel_release = kernel_release or os.uname().release
        self.capability_loader = capability_loader or (lambda: bootstrap_module.detect_capability("/"))
        self.discovery_fetcher = discovery_fetcher or self._fetch_discovery
        if discovery_fallback_enabled is None:
            discovery_fallback_enabled = os.environ.get(DISCOVERY_FALLBACK_ENV, "0").strip().lower() in {
                "1", "true", "yes", "on",
            }
        self.discovery_fallback_enabled = bool(discovery_fallback_enabled)
        self.artifact_fetcher = artifact_fetcher or self._download_artifact
        self.active_root = pathlib.Path(active_root)
        self.log_root = pathlib.Path(log_root) if log_root else (
            pathlib.Path("/var/log/ming-update") if self.state_root == DEFAULT_STATE_ROOT else self.state_root.parent / "log"
        )
        self.state_store = state_module.TransactionStore(self.state_root)

    def _base_update(self, **values):
        update = {
            "current_version": self.current_version,
            "available_version": None,
            "delivery": "none",
            "release_id": None,
            "manifest_sha256": None,
            "release_notes": "",
        }
        update.update(values)
        return update

    def _transaction(self, transaction_id=None):
        if not transaction_id:
            return None
        state = self.state_store.load(transaction_id)
        return {
            "id": state["transaction_id"],
            "release_id": state["release_id"],
            "previous_slot": state["previous_slot"],
            "candidate_slot": state["candidate_slot"],
            "generation": state["generation"],
        }

    @staticmethod
    def _public_transaction_from_state(state):
        if not isinstance(state, dict):
            return None
        transaction_id = state.get("transaction_id")
        release_id = state.get("release_id")
        previous_slot = state.get("previous_slot")
        candidate_slot = state.get("candidate_slot")
        generation = state.get("generation")
        if (
            not isinstance(transaction_id, str)
            or TRANSACTION_ID.fullmatch(transaction_id) is None
            or not isinstance(release_id, str)
            or RELEASE_ID.fullmatch(release_id) is None
            or previous_slot not in {"legacy", "A", "B"}
            or candidate_slot not in {"A", "B"}
            or not isinstance(generation, int)
            or generation < 1
        ):
            return None
        return {
            "id": transaction_id,
            "release_id": release_id,
            "previous_slot": previous_slot,
            "candidate_slot": candidate_slot,
            "generation": generation,
        }

    def _latest_terminal_state(self):
        transactions = self.state_store.transactions
        if not transactions.is_dir() or transactions.is_symlink():
            return None
        candidates = []
        try:
            directories = list(transactions.iterdir())
        except OSError:
            return None
        for directory in directories:
            if directory.is_symlink() or not directory.is_dir() or TRANSACTION_ID.fullmatch(directory.name) is None:
                continue
            try:
                state = self.state_store.load(directory.name)
                updated_at = state.get("updated_at")
                if state.get("state") not in state_module.TERMINAL or self._public_transaction_from_state(state) is None:
                    continue
                if not isinstance(updated_at, str):
                    continue
                updated = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    continue
            except (OSError, ValueError, state_module.TransactionStateError):
                continue
            candidates.append((updated, state["generation"], directory.name, state))
        return max(candidates)[-1] if candidates else None

    def _log_path(self, transaction_id=None):
        if transaction_id and TRANSACTION_ID.fullmatch(transaction_id):
            return str(self.log_root / "transactions" / transaction_id / "engine.log")
        return str(self.log_root / "ming-update.log")

    @staticmethod
    def _public_log_path(transaction_id=None):
        if transaction_id and TRANSACTION_ID.fullmatch(transaction_id):
            return f"/var/log/ming-update/transactions/{transaction_id}/engine.log"
        return None

    @staticmethod
    def _progress_for_state(state):
        phases = {
            "new": ("verifying", 0),
            "verified": ("verifying", 100),
            "staging": ("staging", 50),
            "staged": ("staging", 100),
            "armed": ("armed", 100),
            "booting": ("booting", 0),
            "pending_health": ("pending-health", 0),
            "committing": ("committing", 50),
            "committed": ("complete", 100),
            "aborting": ("rolled-back", 0),
            "aborted": ("rolled-back", 100),
            "rollback_armed": ("rolled-back", 0),
            "rolling_back": ("rolled-back", 50),
            "rolled_back": ("rolled-back", 100),
            "idle": ("idle", 100),
            "available": ("idle", 100),
            "bootstrap-required": ("discovery", 100),
            "failed": ("idle", 100),
        }
        phase, percent = phases.get(state, ("idle", 0))
        return {"phase": phase, "percent": percent}

    def _response(
        self,
        command,
        *,
        ok,
        state,
        action="none",
        error_code=None,
        transaction=None,
        update=None,
        progress=None,
        requires_reboot=False,
        message_key=None,
        message_args=None,
        bootstrap=None,
    ):
        transaction_id = transaction.get("id") if isinstance(transaction, dict) else None
        value = {
            "schema": "ming.update.cli.v1",
            "ok": bool(ok),
            "command": command,
            "exit_code": 0 if ok else EXIT_CODES.get(error_code, 5),
            "error_code": error_code,
            "state": state,
            "transaction": transaction if transaction is not None else self._transaction(),
            "update": update if update is not None else self._base_update(),
            "action": action,
            "progress": progress if progress is not None else self._progress_for_state(state),
            "requires_reboot": bool(requires_reboot),
            "message_key": message_key or ("update.status." + state.replace("-", "_")),
            "message_args": message_args or {},
            "log_path": self._public_log_path(transaction_id),
            "timestamp": timestamp(),
        }
        if bootstrap:
            value["bootstrap"] = bootstrap
            value["bootstrap_required"] = True
        return value

    def _failure(self, command, error, *, state="failed", action="check", update=None, transaction=None, bootstrap=None):
        return self._response(
            command,
            ok=False,
            state=state,
            action=action,
            error_code=error.code,
            update=update,
            transaction=transaction,
            bootstrap=bootstrap,
            message_key="update.error." + error.code.lower(),
        )

    def _capability(self):
        try:
            value = self.capability_loader()
        except Exception as exc:
            raise UpdateError("E_BOOTSTRAP_REQUIRED", "transaction bootstrap capability is unavailable") from exc
        if not isinstance(value, dict):
            raise UpdateError("E_BOOTSTRAP_REQUIRED", "transaction bootstrap capability is unavailable")
        return value

    def _fetch_discovery(self):
        query_values = {
            "version": self.current_version,
            "arch": self.architecture,
            "channel": "stable",
        }
        capability = self._capability()
        bootstrap_version = capability.get("bootstrap_version")
        if (
            capability.get("available") is True
            and capability.get("capability") == "transactional-slot-v1"
            and isinstance(bootstrap_version, str)
            and VERSION.fullmatch(bootstrap_version) is not None
        ):
            query_values["capabilities"] = "transactional-slot-v1"
            query_values["bootstrap_version"] = bootstrap_version
        query = urllib.parse.urlencode(query_values)
        urls = [DEFAULT_DISCOVERY_URL]
        if self.discovery_fallback_enabled:
            urls.append(FALLBACK_DISCOVERY_URL)

        for index, base_url in enumerate(urls):
            # Keep the endpoint fixed and credential-free even when the
            # fallback is explicitly enabled by an administrator.
            parsed = urllib.parse.urlsplit(_safe_https(base_url, "discovery URL"))
            url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    body = response.read(1024 * 1024 + 1)
            except (OSError, urllib.error.URLError) as exc:
                # A fallback is only for an unavailable endpoint.  Do not
                # hide malformed or otherwise invalid primary responses.
                if index + 1 < len(urls):
                    continue
                raise UpdateError("E_NETWORK", "update discovery is unavailable") from exc
            if len(body) > 1024 * 1024:
                raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update discovery is too large")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise UpdateError("E_PROTOCOL_UNSUPPORTED", "update discovery is invalid") from exc
            return value

        # The URL list always contains the primary endpoint; this is only a
        # defensive guard for future changes to that list.
        raise UpdateError("E_NETWORK", "update discovery is unavailable")

    def _read_cached_discovery(self):
        try:
            return validate_discovery(_read_json(self.cache_root / "discovery.json"), self.architecture, self.current_version)
        except UpdateError:
            return None

    def _cache_discovery(self, discovery):
        _atomic_json(self.cache_root / "discovery.json", discovery)

    def _bootstrap_response(self, command, discovery=None):
        # The signed bootstrap is a one-time migration path for 26.3.2 only.
        # Every later image ships the complete transaction runtime.  Offering
        # bootstrap on a later release would conceal an incomplete install and
        # incorrectly direct the user to download a component it already owns.
        if self.current_version != "26.3.2":
            return self._failure(
                command,
                UpdateError("E_BOOTSTRAP_VERSION", "embedded transaction update runtime is incomplete"),
                state="failed",
                action="none",
                update=self._base_update(delivery="transactional-slot-v1"),
            )
        bootstrap = None
        if discovery:
            try:
                parsed = validate_discovery(discovery, self.architecture, self.current_version)
                if parsed["delivery"] == "bootstrap":
                    bootstrap = parsed["bootstrap"]
            except UpdateError:
                pass
        error = UpdateError("E_BOOTSTRAP_REQUIRED", "official transaction bootstrap is required")
        return self._failure(
            command,
            error,
            state="bootstrap-required",
            action="bootstrap",
            update=self._base_update(delivery="bootstrap"),
            bootstrap=bootstrap,
        )

    def check(self):
        capability = self._capability()
        if (
            (not capability.get("available") or capability.get("capability") != "transactional-slot-v1")
            and self.current_version != "26.3.2"
        ):
            return self._bootstrap_response("check")
        discovery = None
        try:
            discovery = self.discovery_fetcher()
        except UpdateError as exc:
            if not capability.get("available"):
                return self._bootstrap_response("check")
            return self._failure("check", exc)
        except Exception as exc:
            if not capability.get("available"):
                return self._bootstrap_response("check")
            return self._failure("check", UpdateError("E_NETWORK", "update discovery is unavailable"))
        if not capability.get("available") or capability.get("capability") != "transactional-slot-v1":
            return self._bootstrap_response("check", discovery)
        try:
            parsed = validate_discovery(discovery, self.architecture, self.current_version)
            if parsed["delivery"] not in {"none", "transactional-slot-v1"}:
                raise UpdateError("E_PROTOCOL_UNSUPPORTED", "bootstrapped systems require transactional discovery")
            self._cache_discovery(parsed)
        except UpdateError as exc:
            return self._failure("check", exc)
        update = self._base_update(
            available_version=parsed.get("version"),
            delivery=parsed["delivery"],
            release_id=parsed.get("release_id"),
            manifest_sha256=parsed.get("manifest_sha256"),
            release_notes=parsed.get("release_notes", ""),
        )
        if not parsed["available"]:
            return self._response("check", ok=True, state="idle", update=update, message_key="update.status.idle")
        return self._response("check", ok=True, state="available", action="apply", update=update, message_key="update.status.available")

    def status(self):
        capability = self._capability()
        if not capability.get("available") or capability.get("capability") != "transactional-slot-v1":
            return self._bootstrap_response("status")
        pointer = self.state_root / "active-transaction.json"
        if pointer.is_file() and not pointer.is_symlink():
            try:
                active = _read_json(pointer)
                transaction = self._transaction(active.get("transaction_id"))
                state = self.state_store.load(transaction["id"])["state"]
                requires_reboot = state in {"staged", "armed", "booting", "pending_health", "committing", "rollback_armed", "rolling_back"}
                action = "cancel" if state == "staged" else "wait"
                return self._response(
                    "status",
                    ok=True,
                    state=state,
                    action=action,
                    transaction=transaction,
                    update=self._base_update(release_id=transaction["release_id"], delivery="transactional-slot-v1"),
                    requires_reboot=requires_reboot,
                    message_key="update.status." + state,
                )
            except (UpdateError, state_module.TransactionStateError) as exc:
                return self._failure("status", UpdateError(getattr(exc, "code", "E_STATE_SCHEMA"), str(exc)))
        terminal_state = self._latest_terminal_state()
        if terminal_state is not None:
            transaction = self._public_transaction_from_state(terminal_state)
            state = terminal_state["state"]
            update = self._base_update(delivery="transactional-slot-v1")
            if state == "rolled_back":
                return self._response(
                    "status",
                    ok=False,
                    state=state,
                    action="none",
                    error_code="E_ROLLBACK_SLOT",
                    transaction=transaction,
                    update=update,
                    message_key="update.rollback.completed",
                )
            return self._response(
                "status",
                ok=True,
                state=state,
                action="none",
                transaction=transaction,
                update=update,
                message_key="update.status." + state,
            )
        cached = self._read_cached_discovery()
        if cached and cached.get("available"):
            update = self._base_update(
                available_version=cached.get("version", ""),
                delivery=cached["delivery"],
                release_id=cached["release_id"],
                manifest_sha256=cached.get("manifest_sha256", ""),
                release_notes=cached.get("release_notes", ""),
            )
            return self._response("status", ok=True, state="available", action="apply", update=update)
        return self._response("status", ok=True, state="idle", message_key="update.status.idle")

    def _download_artifact(self, url, destination, expected_sha256=None, expected_size=None, max_bytes=None):
        url = _safe_https(url, "artifact URL")
        destination = pathlib.Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        digest = hashlib.sha256()
        received = 0
        try:
            with urllib.request.urlopen(url, timeout=30) as response, temporary.open("xb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if max_bytes is not None and received > max_bytes:
                        raise UpdateError("E_ARTIFACT_HASH", "artifact exceeds its allowed size")
                    digest.update(block)
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
            if expected_size is not None and received != expected_size:
                raise UpdateError("E_ARTIFACT_HASH", "artifact size differs from signed metadata")
            if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
                raise UpdateError("E_ARTIFACT_HASH", "artifact SHA256 differs from signed metadata")
            os.replace(temporary, destination)
            return destination
        except UpdateError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            raise UpdateError("E_NETWORK", "artifact download failed") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _fetch_to(self, url, destination, expected_sha256=None, expected_size=None, max_bytes=None):
        try:
            return self.artifact_fetcher(url, destination, expected_sha256, expected_size, max_bytes)
        except TypeError:
            return self.artifact_fetcher(url, destination)

    def _write_engine_log(self, transaction_id, event, **fields):
        if not TRANSACTION_ID.fullmatch(transaction_id):
            return
        path = pathlib.Path(self._log_path(transaction_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"schema": "ming.update.cli-log.v1", "transaction_id": transaction_id, "event": event, "timestamp": timestamp(), **fields}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _transaction_id(self):
        return "tx-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:12]

    def _preflight_download_space(self, manifest):
        # The manifest and signatures are bounded before this point. Reserve enough
        # cache space for all remaining artifacts before starting a large download.
        payload_size = (
            int(manifest["payload"]["size"])
            + int(manifest["content_index"]["size"])
            + 20 * 1024 * 1024
        )
        available_bytes = shutil.disk_usage(self.state_root).free
        try:
            return slot_module.validate_space(
                active_root=self.active_root,
                state_root=self.state_root,
                payload_size=payload_size,
                reserve_bytes=manifest["space"]["reserve_bytes"],
                minimum_free_bytes=manifest["space"]["minimum_free_bytes"],
                available_bytes=available_bytes,
            )
        except slot_module.SlotError as exc:
            raise UpdateError(exc.code, exc.message, exc.details) from exc

    def apply(self, release_id, manifest_sha256):
        if not isinstance(release_id, str) or RELEASE_ID.fullmatch(release_id) is None:
            return self._failure("apply", UpdateError("E_ARGUMENT", "release ID is invalid"))
        if not isinstance(manifest_sha256, str) or SHA256.fullmatch(manifest_sha256.lower()) is None:
            return self._failure("apply", UpdateError("E_ARGUMENT", "manifest SHA256 is invalid"))
        capability = self._capability()
        if not capability.get("available") or capability.get("capability") != "transactional-slot-v1":
            return self._bootstrap_response("apply")
        work = None
        try:
            discovery = validate_discovery(self.discovery_fetcher(), self.architecture, self.current_version)
            if discovery.get("delivery") != "transactional-slot-v1" or not discovery.get("available"):
                raise UpdateError("E_PROTOCOL_UNSUPPORTED", "transactional update is unavailable")
            if discovery["release_id"] != release_id or discovery["manifest_sha256"] != manifest_sha256.lower():
                raise UpdateError("E_ARTIFACT_HASH", "displayed update no longer matches signed discovery")
            transaction_id = self._transaction_id()
            work = self.cache_root / "downloads" / transaction_id
            work.mkdir(parents=True, exist_ok=False)
            manifest = self._fetch_to(discovery["manifest_url"], work / "manifest.json", manifest_sha256.lower(), max_bytes=16 * 1024 * 1024)
            manifest_signature = self._fetch_to(discovery["manifest_signature_url"], work / "manifest.json.sig", max_bytes=1024 * 1024)
            verify_module.verify_detached_signature(manifest, manifest_signature, self.keyring, key_policy=self.key_policy)
            manifest_value = verify_module.load_json_strict(manifest)
            validated_manifest = verify_module.validate_manifest(
                manifest_value,
                current_version=self.current_version,
                architecture=self.architecture,
                kernel_release=self.kernel_release,
                bootstrap_version=capability.get("bootstrap_version", ""),
                now=datetime.datetime.now(datetime.timezone.utc),
            )
            if (
                validated_manifest["release_id"] != discovery["release_id"]
                or validated_manifest["version"] != discovery["version"]
                or validated_manifest["minimum_bootstrap"] != discovery["minimum_bootstrap"]
            ):
                raise UpdateError("E_PROTOCOL_UNSUPPORTED", "signed manifest differs from the displayed update")
            self._preflight_download_space(validated_manifest)
            index = self._fetch_to(
                validated_manifest["content_index"]["url"], work / "content-index.json",
                validated_manifest["content_index"]["sha256"], validated_manifest["content_index"]["size"],
                max_bytes=validated_manifest["content_index"]["size"],
            )
            index_signature = self._fetch_to(validated_manifest["content_index"]["signature_url"], work / "content-index.json.sig", max_bytes=1024 * 1024)
            payload = self._fetch_to(
                validated_manifest["payload"]["url"], work / "payload.tar.zst",
                validated_manifest["payload"]["sha256"], validated_manifest["payload"]["size"],
                max_bytes=validated_manifest["payload"]["size"],
            )
            payload_signature = self._fetch_to(validated_manifest["payload"]["signature_url"], work / "payload.tar.zst.sig", max_bytes=1024 * 1024)
            available_bytes = shutil.disk_usage(self.state_root).free
            staged = engine_module.stage_release(
                manifest_path=manifest,
                manifest_signature=manifest_signature,
                index_path=index,
                index_signature=index_signature,
                payload_path=payload,
                payload_signature=payload_signature,
                keyring=self.keyring,
                key_policy=self.key_policy,
                current_version=self.current_version,
                architecture=self.architecture,
                kernel_release=self.kernel_release,
                bootstrap_version=capability["bootstrap_version"],
                active_root=self.active_root,
                state_root=self.state_root,
                transaction_id=transaction_id,
                available_bytes=available_bytes,
            )
            boot_module.arm_transaction(self.state_root, transaction_id, active_root=self.active_root)
            transaction = self._transaction(transaction_id)
            self._write_engine_log(transaction_id, "armed", candidate_root=str(staged.get("candidate_root", "")))
            return self._response(
                "apply",
                ok=True,
                state="armed",
                action="reboot",
                transaction=transaction,
                update=self._base_update(
                    available_version=validated_manifest.get("version", ""),
                    delivery="transactional-slot-v1",
                    release_id=release_id,
                    manifest_sha256=manifest_sha256.lower(),
                ),
                requires_reboot=True,
                message_key="update.status.armed",
            )
        except (UpdateError, verify_module.TransactionError, engine_module.EngineError, boot_module.BootError, state_module.TransactionStateError) as exc:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            error = UpdateError(getattr(exc, "code", "E_PACKAGE_APPLY"), str(exc), getattr(exc, "details", {}))
            return self._failure("apply", error)
        except (OSError, ValueError) as exc:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            return self._failure("apply", UpdateError("E_PACKAGE_APPLY", "transaction staging failed"))

    def cancel(self, transaction_id):
        if not isinstance(transaction_id, str) or TRANSACTION_ID.fullmatch(transaction_id) is None:
            return self._failure("cancel", UpdateError("E_ARGUMENT", "transaction ID is invalid"))
        try:
            state = self.state_store.load(transaction_id)
            if state["state"] != "staged":
                raise UpdateError("E_NOT_CANCELABLE", "only staged transactions may be cancelled")
            state = self.state_store.transition(transaction_id, "aborting", writer="engine", expected_generation=state["generation"])
            state = self.state_store.transition(transaction_id, "aborted", writer="engine", expected_generation=state["generation"])
            transaction = self._transaction(transaction_id)
            self._write_engine_log(transaction_id, "cancelled")
            return self._response("cancel", ok=True, state="aborted", transaction=transaction, message_key="update.status.aborted")
        except (UpdateError, state_module.TransactionStateError) as exc:
            return self._failure("cancel", UpdateError(getattr(exc, "code", "E_STATE_SCHEMA"), str(exc)))

    def doctor(self):
        capability = self._capability()
        if not capability.get("available") or capability.get("capability") != "transactional-slot-v1":
            return self._bootstrap_response("doctor")
        active = None
        try:
            active = _read_json(self.state_root / "active-transaction.json")
        except UpdateError:
            pass
        return self._response(
            "doctor",
            ok=True,
            state="idle",
            action="none",
            error_code=None,
            transaction=self._transaction(active.get("transaction_id")) if active else None,
            message_key="update.status.doctor",
        )

    def logs(self, transaction_id):
        if not isinstance(transaction_id, str) or TRANSACTION_ID.fullmatch(transaction_id) is None:
            return self._failure("logs", UpdateError("E_ARGUMENT", "transaction ID is invalid"))
        try:
            transaction = self._transaction(transaction_id)
        except state_module.TransactionStateError:
            return self._failure("logs", UpdateError("E_TRANSACTION_NOT_FOUND", "transaction was not found"))
        return self._response("logs", ok=True, state=self.state_store.load(transaction_id)["state"], transaction=transaction, message_key="update.status.logs")


def _controller_from_system():
    return UpdateController()


def _emit(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    return int(value.get("exit_code", 5))


def _argument_command(argv):
    for value in argv:
        if value in CLI_COMMANDS:
            return value
    return "status"


def _argument_response(command):
    current_version = _version()
    if VERSION.fullmatch(current_version) is None:
        current_version = "0.0.0"
    return {
        "schema": "ming.update.cli.v1",
        "ok": False,
        "command": command if command in CLI_COMMANDS else "status",
        "exit_code": EXIT_CODES["E_ARGUMENT"],
        "error_code": "E_ARGUMENT",
        "state": "failed",
        "transaction": None,
        "update": {
            "current_version": current_version,
            "available_version": None,
            "delivery": "none",
            "release_id": None,
            "manifest_sha256": None,
            "release_notes": "",
        },
        "action": "none",
        "progress": {"phase": "idle", "percent": 0},
        "requires_reboot": False,
        "message_key": "update.error.e_argument",
        "message_args": {},
        "log_path": None,
        "timestamp": timestamp(),
    }


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = JsonArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command")
    for command in ("status", "check", "doctor"):
        item = subparsers.add_parser(command)
        item.add_argument("--json", action="store_true")
    apply = subparsers.add_parser("apply")
    apply.add_argument("--release-id", required=True)
    apply.add_argument("--manifest-sha256", required=True)
    apply.add_argument("--json", action="store_true")
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--transaction", required=True)
    cancel.add_argument("--json", action="store_true")
    logs = subparsers.add_parser("logs")
    logs.add_argument("--transaction", required=True)
    logs.add_argument("--json", action="store_true")
    try:
        arguments = parser.parse_args(raw_argv)
    except ArgumentParseError:
        return _emit(_argument_response(_argument_command(raw_argv)))
    if not arguments.command:
        return _emit(_argument_response(_argument_command(raw_argv)))
    controller = _controller_from_system()
    if arguments.command in {"apply", "cancel"} and os.geteuid() != 0:
        return _emit(controller._failure(arguments.command, UpdateError("E_PRIVILEGE", "privileged update action requires the Ming update policy")))
    if arguments.command == "status":
        return _emit(controller.status())
    if arguments.command == "check":
        return _emit(controller.check())
    if arguments.command == "apply":
        return _emit(controller.apply(arguments.release_id, arguments.manifest_sha256))
    if arguments.command == "cancel":
        return _emit(controller.cancel(arguments.transaction))
    if arguments.command == "doctor":
        return _emit(controller.doctor())
    return _emit(controller.logs(arguments.transaction))


if __name__ == "__main__":
    sys.exit(main())
