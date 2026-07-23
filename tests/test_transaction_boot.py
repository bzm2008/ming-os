import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOT_PATH = ROOT / "assets" / "ming-transaction-boot.py"
STATE_PATH = ROOT / "assets" / "ming-transaction-state.py"
HOOK_PATH = ROOT / "assets" / "initramfs" / "ming-transaction-hook"
SELECTOR_PATH = ROOT / "assets" / "initramfs" / "ming-transaction-local-premount"
GRUB_PATH = ROOT / "assets" / "grub" / "40_ming_transaction"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GrubEnvironment:
    def __init__(self, fail_on=None):
        self.values = {"saved_entry": "ming-legacy"}
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.fail_on and self.fail_on in command:
            return FakeResult(returncode=1, stderr="simulated grub failure")
        if command[0] == "grub-editenv" and "set" in command:
            index = command.index("set")
            for assignment in command[index + 1 :]:
                key, value = assignment.split("=", 1)
                self.values[key] = value
            return FakeResult()
        if command[0] == "grub-editenv" and "list" in command:
            return FakeResult(stdout="".join(f"{key}={value}\n" for key, value in self.values.items()))
        if command[0] == "grub-reboot":
            self.values["next_entry"] = command[1]
            return FakeResult()
        if command[0] == "grub-set-default":
            self.values["saved_entry"] = command[1]
            return FakeResult()
        return FakeResult(returncode=127, stderr="unexpected command")


class TransactionBootTests(unittest.TestCase):
    def setUp(self):
        for path in (BOOT_PATH, STATE_PATH, HOOK_PATH, SELECTOR_PATH, GRUB_PATH):
            self.assertTrue(path.exists(), f"transaction boot asset is not implemented: {path.name}")
        self.boot = load_module(BOOT_PATH, "ming_transaction_boot")
        self.state = load_module(STATE_PATH, "ming_transaction_state_for_boot_test")
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-boot-")
        self.root = pathlib.Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.physical = self.root / "physical"
        self.physical.mkdir()
        (self.physical / "home").mkdir()
        (self.physical / "boot").mkdir()
        (self.physical / "etc").mkdir()
        (self.physical / "etc" / "machine-id").write_text("machine-one\n", encoding="ascii")
        self.store = self.state.TransactionStore(self.state_root)

    def tearDown(self):
        self.tmp.cleanup()

    def staged(self, transaction_id="tx-001"):
        state = self.store.create_transaction(
            transaction_id=transaction_id,
            release_id="ming-os-26.3.3-amd64-1",
            previous_slot="legacy",
            candidate_slot="B",
        )
        for target, writer in (
            ("verified", "verifier"),
            ("staging", "slot-manager"),
            ("staged", "candidate-applicator"),
        ):
            state = self.store.transition(transaction_id, target, writer=writer, expected_generation=state["generation"])
        slot = self.state_root / "slots" / "B"
        candidate = slot / "root"
        candidate.mkdir(parents=True)
        (candidate / "etc").mkdir()
        (candidate / "etc" / "os-release").write_text("VERSION_ID=26.3.3\n", encoding="ascii")
        (candidate / "etc" / "machine-id").write_text(
            (self.physical / "etc" / "machine-id").read_text(encoding="ascii"), encoding="ascii"
        )
        (slot / "slot.json").write_text(
            json.dumps({"schema": "ming.slot.v1", "slot": "B", "transaction_id": transaction_id}),
            encoding="utf-8",
        )
        (self.state_root / "transactions" / transaction_id / "candidate-seal.json").write_text(
            json.dumps({
                "schema": "ming.candidate-seal.v1",
                "sha256": self.boot.slot_module.tree_digest(candidate),
            }),
            encoding="utf-8",
        )
        (self.state_root / "transactions" / transaction_id / "protected-seal.json").write_text(
            json.dumps(
                {
                    "schema": "ming.protected-state-seal.v1",
                    "active_sha256": self.boot.slot_module.protected_state_digest(self.physical),
                    "candidate_sha256": self.boot.slot_module.protected_state_digest(candidate),
                }
            ),
            encoding="utf-8",
        )
        return state

    def test_arm_sets_one_shot_and_reads_back_before_entering_armed(self):
        state = self.staged()
        grub = GrubEnvironment()
        armed = self.boot.arm_transaction(self.state_root, "tx-001", runner=grub, active_root=self.physical)

        self.assertEqual(armed["state"], "armed")
        commands = [call[0] for call in grub.calls]
        self.assertEqual(commands[0][0], "grub-editenv")
        self.assertIn("ming_transaction_id=tx-001", commands[0])
        self.assertEqual(commands[1][-1], "list")
        self.assertEqual(commands[2], ["grub-reboot", "ming-slot-b"])
        self.assertEqual(commands[3][-1], "list")
        events = [json.loads(line) for line in (self.state_root / "transactions" / "tx-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["to_state"], "armed")
        self.assertEqual(events[-1]["generation"], state["generation"] + 1)
        self.assertEqual(grub.values["saved_entry"], "ming-legacy")

    def test_arm_failure_leaves_staged_and_saved_default_unchanged(self):
        self.staged()
        grub = GrubEnvironment(fail_on="grub-reboot")
        with self.assertRaises(self.boot.BootError) as caught:
            self.boot.arm_transaction(self.state_root, "tx-001", runner=grub, active_root=self.physical)
        self.assertEqual(caught.exception.code, "E_GRUB_WRITE")
        self.assertEqual(self.store.load("tx-001")["state"], "staged")
        self.assertEqual(grub.values["saved_entry"], "ming-legacy")

    def test_arm_refuses_when_machine_configuration_changed_after_staging(self):
        self.staged()
        (self.physical / "etc" / "machine-id").write_text("changed-after-stage\n", encoding="ascii")
        grub = GrubEnvironment()

        with self.assertRaises(self.boot.BootError) as caught:
            self.boot.arm_transaction(self.state_root, "tx-001", runner=grub, active_root=self.physical)

        self.assertEqual(caught.exception.code, "E_PROTECTED_PATH_CHANGED")
        self.assertEqual(self.store.load("tx-001")["state"], "staged")
        self.assertEqual(grub.values["saved_entry"], "ming-legacy")

    def test_committed_slot_boots_without_an_active_transaction(self):
        (self.state_root / "current.json").write_text(
            json.dumps({"schema": "ming.current-slot.v1", "slot": "legacy"}),
            encoding="utf-8",
        )
        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        self.assertEqual(pathlib.Path(selected["selected_root"]), self.physical)
        self.assertEqual(selected["action"], "boot-committed")

    def test_manual_recovery_selects_the_previous_committed_slot(self):
        state = self.staged()
        for target, writer in (
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
            ("pending_health", "health-service"),
            ("committing", "health-confirmer"),
            ("committed", "commit-coordinator"),
        ):
            state = self.store.transition("tx-001", target, writer=writer, expected_generation=state["generation"])

        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
            manual_recovery=True,
        )

        self.assertEqual(pathlib.Path(selected["selected_root"]), self.physical)
        self.assertEqual(selected["selected_slot"], "legacy")
        self.assertEqual(selected["action"], "boot-manual-recovery")

    def test_exact_armed_candidate_boots_once_and_enters_booting(self):
        state = self.staged()
        state = self.store.transition("tx-001", "armed", writer="boot-coordinator", expected_generation=state["generation"])
        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )
        self.assertEqual(pathlib.Path(selected["selected_root"]), self.state_root / "slots" / "B" / "root")
        self.assertEqual(selected["action"], "boot-candidate")
        self.assertEqual(self.store.load("tx-001")["state"], "booting")
        attempts = [json.loads(line) for line in (self.state_root / "boot" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["transaction_id"], "tx-001")

    def test_candidate_boot_requires_a_post_mount_receipt(self):
        state = self.staged()
        state = self.store.transition("tx-001", "armed", writer="boot-coordinator", expected_generation=state["generation"])
        self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )
        receipt = self.boot.record_candidate_mount(self.state_root, "tx-001", "B")
        self.assertEqual(receipt["transaction_id"], "tx-001")
        self.assertEqual(receipt["candidate_slot"], "B")
        on_disk = json.loads((self.state_root / "boot" / "mounted.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, receipt)

    def test_candidate_boot_refuses_a_tree_that_differs_from_its_seal(self):
        state = self.staged()
        candidate = self.state_root / "slots" / "B" / "root"
        (candidate / "etc" / "os-release").write_text("VERSION_ID=tampered\n", encoding="ascii")
        self.store.transition("tx-001", "armed", writer="boot-coordinator", expected_generation=state["generation"])

        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )

        self.assertEqual(pathlib.Path(selected["selected_root"]), self.physical)
        self.assertEqual(selected["action"], "fallback-previous")
        self.assertEqual(self.store.load("tx-001")["state"], "rolled_back")

    def test_unarmed_or_interrupted_candidate_falls_back_and_rolls_back(self):
        self.staged()
        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )
        self.assertEqual(pathlib.Path(selected["selected_root"]), self.physical)
        self.assertEqual(selected["action"], "fallback-previous")
        self.assertEqual(self.store.load("tx-001")["state"], "aborted")

        second_root = self.root / "state-two"
        store = self.state.TransactionStore(second_root)
        state = store.create_transaction(
            transaction_id="tx-002",
            release_id="ming-os-26.3.3-amd64-1",
            previous_slot="legacy",
            candidate_slot="B",
        )
        for target, writer in (
            ("verified", "verifier"),
            ("staging", "slot-manager"),
            ("staged", "candidate-applicator"),
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
        ):
            state = store.transition("tx-002", target, writer=writer, expected_generation=state["generation"])
        selected = self.boot.select_root(
            state_root=second_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        self.assertEqual(selected["action"], "rollback-interrupted")
        self.assertEqual(store.load("tx-002")["state"], "rolled_back")

    def test_prearm_interruption_is_aborted_on_the_next_normal_boot(self):
        state = self.store.create_transaction(
            transaction_id="tx-003",
            release_id="ming-os-26.3.3-amd64-3",
            previous_slot="legacy",
            candidate_slot="A",
        )
        state = self.store.transition("tx-003", "verified", writer="verifier", expected_generation=state["generation"])
        state = self.store.transition("tx-003", "staging", writer="slot-manager", expected_generation=state["generation"])

        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        self.assertEqual(selected["action"], "rollback-interrupted")
        self.assertEqual(self.store.load("tx-003")["state"], "aborted")
        self.assertFalse((self.state_root / "active-transaction.json").exists())

    def test_interrupted_candidate_writes_a_one_shot_rollback_receipt(self):
        state = self.staged()
        state = self.store.transition("tx-001", "armed", writer="boot-coordinator", expected_generation=state["generation"])
        self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )

        self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )

        receipt = json.loads((self.state_root / "boot" / "rollback-pending.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "ming.rollback-pending.v1")
        self.assertEqual(receipt["transaction_id"], "tx-001")
        self.assertEqual(receipt["previous_slot"], "legacy")
        self.assertEqual(receipt["candidate_slot"], "B")
        self.assertEqual(receipt["generation"], self.store.load("tx-001")["generation"])

    def test_boot_assets_are_fixed_bounded_and_never_invoke_installers(self):
        grub = GRUB_PATH.read_text(encoding="utf-8")
        selector = SELECTOR_PATH.read_text(encoding="utf-8")
        hook = HOOK_PATH.read_text(encoding="utf-8")
        for entry_id, slot in (("ming-legacy", "legacy"), ("ming-slot-a", "A"), ("ming-slot-b", "B")):
            self.assertIn(entry_id, grub)
            self.assertIn(f"ming.slot={slot}", grub)
        self.assertIn("ming-recovery-manual", grub)
        self.assertIn("ming.recovery=manual", grub)
        self.assertIn("manual-recovery", selector)
        self.assertIn("timeout 15", selector)
        self.assertIn("mount --bind", selector)
        for path in ("/home", "/boot", "/var/lib/ming-update"):
            self.assertIn(path, selector)
        self.assertIn("ming-transaction-boot.py", hook)
        self.assertIn("copy_exec /usr/bin/timeout", hook)
        combined = "\n".join((grub, selector, hook, BOOT_PATH.read_text(encoding="utf-8"))).lower()
        for forbidden in ("calamares", "parted", "sfdisk", "mkfs", "resize2fs", "dkms", "linux-image"):
            self.assertNotIn(forbidden, combined)

    def test_selector_reverts_to_the_physical_root_after_a_late_bind_failure(self):
        selector = SELECTOR_PATH.read_text(encoding="utf-8")
        self.assertIn("rollback_candidate_root()", selector)
        self.assertIn('candidate_bound=1', selector)
        for target in ("${rootmnt}/home", "${rootmnt}/boot", "${rootmnt}/var/lib/ming-update"):
            self.assertIn(f'umount "{target}"', selector)
        self.assertIn('umount "${rootmnt}"', selector)
        self.assertNotIn('mount --bind "${physical}/home" "${rootmnt}/home" || exit 0', selector)
        self.assertNotIn('mount --bind "${physical}/boot" "${rootmnt}/boot" || exit 0', selector)

    def test_selector_persists_boot_state_only_after_remounting_and_mounting_candidate(self):
        selector = SELECTOR_PATH.read_text(encoding="utf-8")
        remount = selector.index('mount -o remount,rw "${rootmnt}"')
        select = selector.index("ming-transaction-boot.py select")
        receipt = selector.index("record-mounted")
        self.assertLess(remount, select)
        self.assertGreater(receipt, selector.index('mount --bind "${state_root}" "${rootmnt}/var/lib/ming-update"'))


if __name__ == "__main__":
    unittest.main()
