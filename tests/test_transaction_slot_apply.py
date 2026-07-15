import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest


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
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.active / "usr" / "share" / "ming-os" / "version").write_text("26.3.2", encoding="utf-8")
        (self.active / "etc" / "machine-id").write_text("machine-one\n", encoding="ascii")
        (self.active / "etc" / "NetworkManager" / "system-connections" / "wifi.nmconnection").write_text("secret", encoding="utf-8")
        (self.active / "boot" / "vmlinuz").write_text("kernel", encoding="ascii")
        (self.home / "user" / "document.txt").write_text("user data", encoding="utf-8")
        (self.active / "var" / "lib" / "ming-update" / "must-not-copy").write_text("state", encoding="ascii")
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
        self.assertEqual((candidate / "usr" / "share" / "ming-os" / "version").read_bytes(), self.new_content)

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

    def test_stage_writes_structured_engine_log_with_failure_reason(self):
        self.prepare()
        log_path = self.state_root / "transactions" / "tx-001" / "engine.jsonl"
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["event"], "preflight-complete")
        self.assertEqual(events[-1]["event"], "candidate-staged")
        self.assertTrue(all("timestamp" in event and "transaction_id" in event for event in events))


if __name__ == "__main__":
    unittest.main()
