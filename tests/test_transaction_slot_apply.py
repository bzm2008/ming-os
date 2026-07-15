import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import shutil
import tarfile
import tempfile
import threading
import types
import unittest
from unittest import mock

try:
    from compression import zstd
except ImportError:
    zstd = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
SLOT_PATH = ROOT / "assets" / "ming-transaction-slot.py"
APPLY_PATH = ROOT / "assets" / "ming-transaction-apply.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree_digest(root):
    digest = hashlib.sha256()
    root = pathlib.Path(root)
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"L" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F" + path.read_bytes())
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


class TransactionSlotApplyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SLOT_PATH.exists(), "slot manager is not implemented")
        self.assertTrue(APPLY_PATH.exists(), "candidate applicator is not implemented")
        self.slot_module = load_module(SLOT_PATH, "ming_transaction_slot")
        self.apply_module = load_module(APPLY_PATH, "ming_transaction_apply")
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-slot-")
        self.root = pathlib.Path(self.tmp.name)
        self.active = self.root / "active"
        self.state_root = self.root / "state"
        self.home = self.active / "home"
        for directory in (
            self.active / "usr" / "share" / "ming-os",
            self.active / "etc" / "NetworkManager" / "system-connections",
            self.active / "boot",
            self.home / "user",
            self.active / "var" / "lib" / "ming-update",
            self.active / "var" / "cache" / "ming-update",
            self.active / "var" / "lib" / "dpkg",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.active / "usr" / "share" / "ming-os" / "version").write_text("26.3.2", encoding="utf-8")
        (self.active / "etc" / "machine-id").write_text("machine-one\n", encoding="ascii")
        (self.active / "etc" / "NetworkManager" / "system-connections" / "wifi.nmconnection").write_text("secret", encoding="utf-8")
        (self.active / "boot" / "vmlinuz").write_text("kernel", encoding="ascii")
        (self.home / "user" / "document.txt").write_text("user data", encoding="utf-8")
        (self.active / "var" / "lib" / "ming-update" / "must-not-copy").write_text("state", encoding="ascii")
        (self.active / "var" / "cache" / "ming-update" / "downloaded-payload").write_text("cache", encoding="ascii")
        (self.active / "var" / "lib" / "dpkg" / "lock-frontend").write_text("", encoding="ascii")
        (self.active / "var" / "lib" / "dpkg" / "lock").write_text("", encoding="ascii")
        self.new_content = b"26.3.3\n"
        self.blob_hash = hashlib.sha256(self.new_content).hexdigest()
        self.payload = self.root / "payload.tar"
        with tarfile.open(self.payload, "w") as archive:
            info = tarfile.TarInfo(f"objects/{self.blob_hash}")
            info.size = len(self.new_content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(self.new_content))
        self.plan = {
            "release_id": "ming-os-26.3.3-amd64-1",
            "version": "26.3.3",
            "delivery": "transactional-slot-v1",
            "space": {"minimum_free_bytes": 1, "reserve_bytes": 1024},
            "verified_artifacts": {"payload_sha256": hashlib.sha256(self.payload.read_bytes()).hexdigest()},
            "content_index": {
                "schema": "ming.content-index.v1",
                "release_id": "ming-os-26.3.3-amd64-1",
                "entries": [
                    {
                        "path": "usr/share/ming-os/version",
                        "type": "file",
                        "blob": f"sha256:{self.blob_hash}",
                        "mode": 0o644,
                        "uid": 0,
                        "gid": 0,
                        "config_policy": "replace",
                    }
                ],
                "deletions": [],
                "packages": [],
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, **kwargs):
        options = {
            "plan": self.plan,
            "payload_path": self.payload,
            "active_root": self.active,
            "state_root": self.state_root,
            "transaction_id": "tx-001",
            "available_bytes": 1024 * 1024 * 1024,
        }
        options.update(kwargs)
        return self.apply_module.prepare_candidate(**options)

    def test_payload_and_space_validation_happen_before_transaction_writes(self):
        corrupted = self.root / "corrupted.tar"
        corrupted.write_bytes(self.payload.read_bytes().replace(self.new_content, b"bad data"))
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.prepare(payload_path=corrupted)
        self.assertEqual(caught.exception.code, "E_ARTIFACT_HASH")
        self.assertFalse(self.state_root.exists())

        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.prepare(available_bytes=1)
        self.assertEqual(caught.exception.code, "E_SPACE")
        self.assertFalse(self.state_root.exists())

    def test_payload_container_rejects_extra_missing_and_nonregular_members(self):
        extra = self.root / "extra.tar"
        with tarfile.open(extra, "w") as archive:
            info = tarfile.TarInfo(f"objects/{self.blob_hash}")
            info.size = len(self.new_content)
            archive.addfile(info, io.BytesIO(self.new_content))
            other = tarfile.TarInfo("objects/" + hashlib.sha256(b"x").hexdigest())
            other.size = 1
            archive.addfile(other, io.BytesIO(b"x"))
        plan = json.loads(json.dumps(self.plan))
        plan["verified_artifacts"]["payload_sha256"] = hashlib.sha256(extra.read_bytes()).hexdigest()
        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.prepare(plan=plan, payload_path=extra)
        self.assertEqual(caught.exception.code, "E_CONTENT_POLICY")
        self.assertFalse(self.state_root.exists())

    @unittest.skipIf(zstd is None, "stdlib Zstandard fixture support is unavailable")
    def test_tar_zst_payload_is_streamed_without_temporary_extraction(self):
        compressed = self.root / "payload.tar.zst"
        compressed.write_bytes(zstd.compress(self.payload.read_bytes()))
        plan = json.loads(json.dumps(self.plan))
        plan["verified_artifacts"]["payload_sha256"] = hashlib.sha256(compressed.read_bytes()).hexdigest()
        result = self.prepare(plan=plan, payload_path=compressed)
        candidate = pathlib.Path(result["candidate_root"])
        self.assertEqual((candidate / "usr" / "share" / "ming-os" / "version").read_bytes(), self.new_content)
        self.assertFalse(list(self.root.rglob("*.decompressed")))

    @unittest.skipIf(zstd is None, "stdlib Zstandard fixture support is unavailable")
    def test_zstd_compatibility_stream_reads_without_tarfile_native_support(self):
        compressed = self.root / "compat.tar.zst"
        compressed.write_bytes(zstd.compress(self.payload.read_bytes()))
        with self.apply_module.open_zstd_stream(compressed) as stream:
            self.assertEqual(stream.read(), self.payload.read_bytes())

    def test_clone_preserves_machine_state_and_excludes_home_boot_and_update_store(self):
        result = self.prepare()
        candidate = pathlib.Path(result["candidate_root"])
        self.assertEqual((candidate / "etc" / "machine-id").read_text(encoding="ascii"), "machine-one\n")
        self.assertEqual(
            (candidate / "etc" / "NetworkManager" / "system-connections" / "wifi.nmconnection").read_text(encoding="utf-8"),
            "secret",
        )
        self.assertFalse((candidate / "home").exists())
        self.assertFalse((candidate / "boot").exists())
        self.assertFalse((candidate / "var" / "lib" / "ming-update").exists())
        self.assertFalse((candidate / "var" / "cache" / "ming-update").exists())
        self.assertEqual((candidate / "usr" / "share" / "ming-os" / "version").read_bytes(), self.new_content)

    def test_staging_writes_matching_protected_machine_state_seals(self):
        self.prepare()
        seal = json.loads(
            (self.state_root / "transactions" / "tx-001" / "protected-seal.json").read_text(encoding="utf-8")
        )

        self.assertEqual(seal["schema"], "ming.protected-state-seal.v1")
        self.assertEqual(seal["active_sha256"], seal["candidate_sha256"])

    def test_staging_refuses_when_machine_configuration_changes_after_final_sync(self):
        original_apply = self.apply_module.apply_payload

        def mutate_active_machine_id(**kwargs):
            result = original_apply(**kwargs)
            (self.active / "etc" / "machine-id").write_text("changed-during-stage\n", encoding="ascii")
            return result

        self.apply_module.apply_payload = mutate_active_machine_id
        try:
            with self.assertRaises(self.apply_module.ApplyError) as caught:
                self.prepare()
        finally:
            self.apply_module.apply_payload = original_apply

        self.assertEqual(caught.exception.code, "E_PROTECTED_PATH_CHANGED")
        state = json.loads((self.state_root / "transactions" / "tx-001" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "aborted")

    def test_linux_clone_command_preserves_hardlinks_acls_xattrs_and_mount_boundary(self):
        command = self.slot_module.rsync_clone_command("/active", "/staging")
        self.assertEqual(command[0], "rsync")
        for option in ("-aHAX", "--numeric-ids", "--one-file-system", "--delete"):
            self.assertIn(option, command)
        for excluded in ("/home", "/boot", "/var/lib/ming-update", "/dev", "/proc", "/sys", "/run"):
            self.assertIn(f"--exclude={excluded}", command)
        self.assertEqual(command[-2:], ["/active/", "/staging/"])

    def test_candidate_seal_includes_ownership_and_hardlink_topology(self):
        seal_root = self.root / "seal-root"
        seal_root.mkdir()
        primary = seal_root / "primary"
        sibling = seal_root / "sibling"
        primary.write_text("same", encoding="utf-8")
        sibling.hardlink_to(primary)
        baseline = self.slot_module.tree_digest(seal_root)

        sibling.unlink()
        sibling.write_text("same", encoding="utf-8")
        self.assertNotEqual(baseline, self.slot_module.tree_digest(seal_root))
        owner_baseline = self.slot_module.tree_digest(seal_root)

        original_lstat = pathlib.Path.lstat

        def altered_owner(path):
            metadata = original_lstat(path)
            if path == primary:
                return types.SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_uid=metadata.st_uid + 1,
                    st_gid=metadata.st_gid,
                    st_dev=metadata.st_dev,
                    st_ino=metadata.st_ino,
                    st_nlink=metadata.st_nlink,
                )
            return metadata

        with mock.patch.object(pathlib.Path, "lstat", altered_owner):
            self.assertNotEqual(owner_baseline, self.slot_module.tree_digest(seal_root))

    def test_success_and_injected_failure_never_modify_active_root_or_home(self):
        active_before = tree_digest(self.active)
        home_before = tree_digest(self.home)
        self.prepare()
        self.assertEqual(tree_digest(self.active), active_before)
        self.assertEqual(tree_digest(self.home), home_before)

        second_state = self.root / "state-second"

        def fail(point):
            if point == "after-first-mutation":
                raise RuntimeError("simulated interruption")

        with self.assertRaises(self.apply_module.ApplyError) as caught:
            self.prepare(state_root=second_state, transaction_id="tx-002", fault_hook=fail)
        self.assertEqual(caught.exception.code, "E_PACKAGE_APPLY")
        self.assertEqual(tree_digest(self.active), active_before)
        self.assertEqual(tree_digest(self.home), home_before)
        state = json.loads((second_state / "transactions" / "tx-002" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "aborted")
        rollback_events = (second_state / "transactions" / "tx-002" / "rollback.jsonl").read_text(encoding="utf-8")
        self.assertIn("rollback-complete", rollback_events)

    def test_payload_rejects_a_symlinked_candidate_root_without_writing_outside_it(self):
        original_clone = self.apply_module.slot_module.clone_active_root
        candidate_root = {"path": None}

        def poisoned_clone(**kwargs):
            candidate = pathlib.Path(original_clone(**kwargs))
            candidate_root["path"] = candidate
            return candidate

        original_is_symlink = pathlib.Path.is_symlink

        def candidate_is_symlink(path):
            return path == candidate_root["path"] or original_is_symlink(path)

        self.apply_module.slot_module.clone_active_root = poisoned_clone
        try:
            with mock.patch.object(pathlib.Path, "is_symlink", candidate_is_symlink):
                with self.assertRaises(self.apply_module.ApplyError) as caught:
                    self.prepare()
        finally:
            self.apply_module.slot_module.clone_active_root = original_clone

        self.assertEqual(caught.exception.code, "E_CONTENT_POLICY")
        self.assertEqual(
            (candidate_root["path"] / "usr" / "share" / "ming-os" / "version").read_text(encoding="utf-8"),
            "26.3.2",
        )

    def test_staging_holds_dpkg_locks_and_final_syncs_before_payload_apply(self):
        events = []
        original_clone = self.apply_module.slot_module.clone_active_root
        original_apply = self.apply_module.apply_payload

        @contextlib.contextmanager
        def dpkg_lock(*_args, **_kwargs):
            events.append("lock-enter")
            try:
                yield
            finally:
                events.append("lock-exit")

        def clone_with_late_configuration(**kwargs):
            candidate = pathlib.Path(original_clone(**kwargs))
            (self.active / "etc" / "late-stage.conf").write_text("late-change", encoding="utf-8")
            events.append("clone")
            return candidate

        def final_sync(active_root, candidate_root, **_kwargs):
            events.append("final-sync")
            source = pathlib.Path(active_root) / "etc" / "late-stage.conf"
            target = pathlib.Path(candidate_root) / "etc" / "late-stage.conf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        def observed_apply(**kwargs):
            events.append("payload")
            candidate = pathlib.Path(kwargs["candidate_root"])
            self.assertEqual((candidate / "etc" / "late-stage.conf").read_text(encoding="utf-8"), "late-change")
            return original_apply(**kwargs)

        self.apply_module.slot_module.clone_active_root = clone_with_late_configuration
        self.apply_module.slot_module.dpkg_transaction_lock = dpkg_lock
        self.apply_module.slot_module.final_sync_active_root = final_sync
        self.apply_module.apply_payload = observed_apply
        try:
            self.prepare()
        finally:
            self.apply_module.slot_module.clone_active_root = original_clone
            self.apply_module.apply_payload = original_apply
            del self.apply_module.slot_module.dpkg_transaction_lock
            del self.apply_module.slot_module.final_sync_active_root

        self.assertEqual(events, ["lock-enter", "clone", "final-sync", "payload", "lock-exit"])

    def test_dpkg_lock_acquires_the_posix_lock_used_by_debian_package_managers(self):
        lock_events = []

        class PosixLocking:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 8

            @staticmethod
            def flock(_descriptor, operation):
                lock_events.append(("flock", operation))

            @staticmethod
            def lockf(_descriptor, operation):
                lock_events.append(("lockf", operation))

        for relative in self.slot_module.DPKG_LOCK_RELATIVE_PATHS:
            lock = self.active.joinpath(*relative.split("/"))
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.touch()

        original_fcntl = self.slot_module.fcntl
        self.slot_module.fcntl = PosixLocking
        try:
            with self.slot_module.dpkg_transaction_lock(self.active, timeout_seconds=0):
                self.assertTrue(any(kind == "lockf" and operation & PosixLocking.LOCK_EX for kind, operation in lock_events))
        finally:
            self.slot_module.fcntl = original_fcntl

        self.assertTrue(any(kind == "lockf" and operation == PosixLocking.LOCK_UN for kind, operation in lock_events))

    def test_committed_slot_rotation_reclaims_only_the_inactive_stale_slot(self):
        store = self.apply_module.state_module.TransactionStore(self.state_root)

        first = self.prepare(transaction_id="tx-001")
        state = store.load("tx-001")
        for target, writer in (
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
            ("pending_health", "health-service"),
            ("committing", "health-confirmer"),
            ("committed", "commit-coordinator"),
        ):
            state = store.transition("tx-001", target, writer=writer, expected_generation=state["generation"])
        self.assertEqual(first["candidate_slot"], "B")

        second = self.prepare(transaction_id="tx-002")
        state = store.load("tx-002")
        for target, writer in (
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
            ("pending_health", "health-service"),
            ("committing", "health-confirmer"),
            ("committed", "commit-coordinator"),
        ):
            state = store.transition("tx-002", target, writer=writer, expected_generation=state["generation"])
        self.assertEqual(second["candidate_slot"], "A")
        self.assertTrue((self.state_root / "slots" / "B" / "root").is_dir())

        third = self.prepare(transaction_id="tx-003")
        self.assertEqual(third["candidate_slot"], "B")
        self.assertEqual((pathlib.Path(third["candidate_root"]) / "usr" / "share" / "ming-os" / "version").read_bytes(), self.new_content)
        self.assertTrue((self.state_root / "slots" / "A" / "root").is_dir())

    def test_stage_writes_structured_engine_log_with_failure_reason(self):
        self.prepare()
        log_path = self.state_root / "transactions" / "tx-001" / "engine.jsonl"
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["event"], "preflight-complete")
        self.assertEqual(events[-1]["event"], "candidate-staged")
        self.assertTrue(all("timestamp" in event and "transaction_id" in event for event in events))

    def test_transaction_is_reserved_before_inactive_slot_retirement(self):
        stale_root = self.state_root / "slots" / "B" / "root"
        stale_root.mkdir(parents=True)
        (stale_root / "old-release").write_text("stale", encoding="utf-8")

        original_retire = self.apply_module.slot_module.retire_inactive_slot
        observed = {}

        def inspect_retirement(**kwargs):
            active = json.loads((self.state_root / "active-transaction.json").read_text(encoding="utf-8"))
            observed.update(kwargs)
            self.assertEqual(active["transaction_id"], "tx-001")
            self.assertEqual(active["candidate_slot"], "B")
            self.assertEqual(active["state"], "new")
            return original_retire(**kwargs)

        self.apply_module.slot_module.retire_inactive_slot = inspect_retirement
        try:
            result = self.prepare()
        finally:
            self.apply_module.slot_module.retire_inactive_slot = original_retire

        self.assertEqual(result["state"], "staged")
        self.assertEqual(observed["owner_transaction_id"], "tx-001")

    def test_concurrent_prepare_uses_one_transaction_lock_and_one_candidate(self):
        original_clone = self.apply_module.slot_module.clone_active_root
        entered_clone = threading.Event()
        release_clone = threading.Event()
        clone_calls = []

        def paused_clone(**kwargs):
            clone_calls.append(kwargs["transaction_id"])
            entered_clone.set()
            self.assertTrue(release_clone.wait(timeout=5))
            return original_clone(**kwargs)

        self.apply_module.slot_module.clone_active_root = paused_clone
        first_result = {}

        def stage_first():
            try:
                first_result["value"] = self.prepare(transaction_id="tx-first")
            except Exception as exc:
                first_result["error"] = exc

        worker = threading.Thread(target=stage_first)
        worker.start()
        self.assertTrue(entered_clone.wait(timeout=5))
        self.assertTrue((self.state_root / ".transaction.lock").is_file())
        try:
            with self.assertRaises(self.apply_module.ApplyError) as caught:
                self.prepare(transaction_id="tx-second")
            self.assertEqual(caught.exception.code, "E_BUSY")
        finally:
            release_clone.set()
            worker.join(timeout=5)
            self.apply_module.slot_module.clone_active_root = original_clone

        self.assertNotIn("error", first_result)
        self.assertEqual(first_result["value"]["state"], "staged")
        self.assertEqual(clone_calls, ["tx-first"])


if __name__ == "__main__":
    unittest.main()
