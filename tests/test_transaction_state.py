import concurrent.futures
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-transaction-state.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_transaction_state", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionStateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE_PATH.exists(), "transaction state engine is not implemented")
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-state-")
        self.root = pathlib.Path(self.tmp.name)
        self.store = self.module.TransactionStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, transaction_id="tx-001"):
        return self.store.create_transaction(
            transaction_id=transaction_id,
            release_id="ming-os-26.3.3-amd64-1",
            previous_slot="legacy",
            candidate_slot="B",
        )

    def test_create_writes_atomic_state_active_pointer_and_structured_event(self):
        state = self.create()
        self.assertEqual(state["state"], "new")
        self.assertEqual(state["generation"], 1)

        transaction_dir = self.root / "transactions" / "tx-001"
        on_disk = json.loads((transaction_dir / "state.json").read_text(encoding="utf-8"))
        active = json.loads((self.root / "active-transaction.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (transaction_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(on_disk, state)
        self.assertEqual(active["transaction_id"], "tx-001")
        self.assertEqual(active["generation"], 1)
        self.assertEqual(events[0]["to_state"], "new")
        self.assertEqual(events[0]["generation"], 1)
        self.assertIn("timestamp", events[0])
        self.assertIn("monotonic_ns", events[0])
        self.assertFalse(list(transaction_dir.glob("*.tmp-*")))

    def test_accepts_the_frozen_happy_path(self):
        state = self.create()
        transitions = (
            ("verified", "verifier"),
            ("staging", "slot-manager"),
            ("staged", "candidate-applicator"),
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
            ("pending_health", "health-service"),
            ("committing", "health-confirmer"),
            ("committed", "commit-coordinator"),
        )
        for target, writer in transitions:
            state = self.store.transition(
                "tx-001",
                target,
                writer=writer,
                expected_generation=state["generation"],
                evidence={"check": target},
            )
        self.assertEqual(state["state"], "committed")
        self.assertEqual(state["generation"], 9)
        self.assertFalse((self.root / "active-transaction.json").exists())
        current = json.loads((self.root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["slot"], "B")

    def test_rejects_illegal_transition_wrong_writer_and_stale_generation(self):
        self.create()
        cases = (
            (lambda: self.store.transition("tx-001", "armed", writer="boot-coordinator", expected_generation=1), "E_STATE_TRANSITION"),
            (lambda: self.store.transition("tx-001", "verified", writer="health-service", expected_generation=1), "E_STATE_WRITER"),
            (lambda: self.store.transition("tx-001", "verified", writer="verifier", expected_generation=99), "E_STATE_STALE"),
        )
        for action, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(self.module.TransactionStateError) as caught:
                    action()
                self.assertEqual(caught.exception.code, code)

    def test_allows_only_one_nonterminal_transaction(self):
        self.create()
        with self.assertRaises(self.module.TransactionStateError) as caught:
            self.create("tx-002")
        self.assertEqual(caught.exception.code, "E_BUSY")

    def test_concurrent_creators_atomically_claim_one_active_transaction(self):
        original_atomic_json = self.module._atomic_json
        barrier = threading.Barrier(6)

        def delayed_atomic_json(path, value, mode=0o600):
            if pathlib.Path(path).name == "active-transaction.json":
                barrier.wait(timeout=5)
            return original_atomic_json(path, value, mode)

        self.module._atomic_json = delayed_atomic_json

        def create(index):
            store = self.module.TransactionStore(self.root)
            try:
                return ("ok", store.create_transaction(
                    transaction_id=f"tx-race-{index}",
                    release_id="ming-os-26.3.3-amd64-race",
                    previous_slot="legacy",
                    candidate_slot="B",
                )["transaction_id"])
            except self.module.TransactionStateError as exc:
                return ("error", exc.code)
            except OSError as exc:
                return ("error", type(exc).__name__)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(create, range(6)))
        finally:
            self.module._atomic_json = original_atomic_json

        winners = [value for kind, value in results if kind == "ok"]
        losers = [value for kind, value in results if kind == "error"]
        self.assertEqual(len(winners), 1)
        self.assertEqual(losers, ["E_BUSY"] * 5)
        active = json.loads((self.root / "active-transaction.json").read_text(encoding="utf-8"))
        self.assertEqual(active["transaction_id"], winners[0])
        state_files = list((self.root / "transactions").glob("*/state.json"))
        self.assertEqual(len(state_files), 1)

    def test_pre_arm_cancel_and_post_arm_rollback_paths(self):
        state = self.create()
        state = self.store.transition("tx-001", "verified", writer="verifier", expected_generation=state["generation"])
        state = self.store.transition("tx-001", "aborting", writer="engine", expected_generation=state["generation"])
        state = self.store.transition("tx-001", "aborted", writer="engine", expected_generation=state["generation"])
        self.assertEqual(state["state"], "aborted")

        state = self.create("tx-002")
        for target, writer in (
            ("verified", "verifier"),
            ("staging", "slot-manager"),
            ("staged", "candidate-applicator"),
            ("armed", "boot-coordinator"),
            ("rollback_armed", "rollback-service"),
            ("rolling_back", "initramfs"),
            ("rolled_back", "rollback-service"),
        ):
            state = self.store.transition("tx-002", target, writer=writer, expected_generation=state["generation"])
        self.assertEqual(state["state"], "rolled_back")
        self.assertFalse((self.root / "active-transaction.json").exists())

    def test_reconcile_repairs_event_missing_after_atomic_state_replace(self):
        self.create()

        def fault(point):
            if point == "after-state-replace":
                raise RuntimeError("simulated power loss")

        with self.assertRaises(RuntimeError):
            self.store.transition(
                "tx-001",
                "verified",
                writer="verifier",
                expected_generation=1,
                fault_hook=fault,
            )

        repaired = self.store.reconcile("tx-001")
        self.assertEqual(repaired["state"], "verified")
        events = [json.loads(line) for line in (self.root / "transactions" / "tx-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["generation"] for event in events], [1, 2])
        self.assertTrue(events[-1]["reconciled"])


if __name__ == "__main__":
    unittest.main()
