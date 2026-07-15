import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
HEALTH_PATH = ROOT / "assets" / "ming-transaction-health.py"
BOOTSTRAP_PATH = ROOT / "assets" / "ming-ota-bootstrap-capability.py"
STATE_PATH = ROOT / "assets" / "ming-transaction-state.py"
BOOT_PATH = ROOT / "assets" / "ming-transaction-boot.py"
HEALTH_UNIT = ROOT / "assets" / "systemd" / "ming-transaction-health.service"
RECONCILE_UNIT = ROOT / "assets" / "systemd" / "ming-transaction-reconcile.service"


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


class HealthRunner:
    def __init__(self, failing_check=None):
        self.values = {"saved_entry": "ming-legacy"}
        self.calls = []
        self.failing_check = failing_check

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.failing_check and self.failing_check in " ".join(command):
            return FakeResult(returncode=1, stderr="health fixture failure")
        if command[0] == "grub-set-default":
            self.values["saved_entry"] = command[1]
            return FakeResult()
        if command[0] == "grub-editenv":
            return FakeResult(stdout="".join(f"{key}={value}\n" for key, value in self.values.items()))
        return FakeResult()


class TransactionHealthBootstrapTests(unittest.TestCase):
    def setUp(self):
        for path in (HEALTH_PATH, BOOTSTRAP_PATH, STATE_PATH, BOOT_PATH, HEALTH_UNIT, RECONCILE_UNIT):
            self.assertTrue(path.exists(), f"health/bootstrap asset is not implemented: {path.name}")
        self.health = load_module(HEALTH_PATH, "ming_transaction_health")
        self.bootstrap = load_module(BOOTSTRAP_PATH, "ming_ota_bootstrap_capability")
        self.state = load_module(STATE_PATH, "ming_transaction_state_for_health")
        self.boot = load_module(BOOT_PATH, "ming_transaction_boot_for_health")
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-health-")
        self.root = pathlib.Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.physical = self.root / "physical"
        self.physical.mkdir()
        self.store = self.state.TransactionStore(self.state_root)

    def tearDown(self):
        self.tmp.cleanup()

    def booting(self, transaction_id="tx-001"):
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
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
        ):
            state = self.store.transition(transaction_id, target, writer=writer, expected_generation=state["generation"])
        slot = self.state_root / "slots" / "B"
        candidate = slot / "root"
        (candidate / "home").mkdir(parents=True)
        (slot / "slot.json").write_text(
            json.dumps({"schema": "ming.slot.v1", "slot": "B", "transaction_id": transaction_id}),
            encoding="utf-8",
        )
        transaction = self.state_root / "transactions" / transaction_id
        (transaction / "candidate-seal.json").write_text(
            json.dumps(
                {
                    "schema": "ming.candidate-seal.v1",
                    "sha256": self.boot.slot_module.tree_digest(candidate),
                }
            ),
            encoding="utf-8",
        )
        self.boot.record_candidate_mount(self.state_root, transaction_id, "B")
        return state

    def test_health_token_and_grub_readback_precede_commit(self):
        self.booting()
        runner = HealthRunner()
        result = self.health.confirm_transaction(
            self.state_root,
            "tx-001",
            runner=runner,
            checks=(("root", ["ming-health-root"]), ("packages", ["dpkg", "--audit"])),
        )
        self.assertEqual(result["state"], "committed")
        token = json.loads((self.state_root / "transactions" / "tx-001" / "health-token.json").read_text(encoding="utf-8"))
        self.assertEqual(token["transaction_id"], "tx-001")
        self.assertEqual(token["candidate_slot"], "B")
        self.assertEqual(runner.values["saved_entry"], "ming-slot-b")
        events = [json.loads(line) for line in (self.state_root / "transactions" / "tx-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-2]["to_state"], "committing")
        self.assertEqual(events[-1]["to_state"], "committed")

    def test_health_failure_arms_rollback_and_restores_previous_default(self):
        self.booting()
        runner = HealthRunner(failing_check="dpkg --audit")
        with self.assertRaises(self.health.HealthError) as caught:
            self.health.confirm_transaction(
                self.state_root,
                "tx-001",
                runner=runner,
                checks=(("packages", ["dpkg", "--audit"]),),
            )
        self.assertEqual(caught.exception.code, "E_HEALTH_PACKAGES")
        self.assertEqual(self.store.load("tx-001")["state"], "rollback_armed")
        self.assertEqual(runner.values["saved_entry"], "ming-legacy")
        failure = json.loads((self.state_root / "transactions" / "tx-001" / "failure.json").read_text(encoding="utf-8"))
        self.assertEqual(failure["error_code"], "E_HEALTH_PACKAGES")
        self.assertNotIn("password", json.dumps(failure).lower())

    def test_health_rejects_a_candidate_without_an_initramfs_mount_receipt(self):
        self.booting()
        receipt = self.state_root / "boot" / "mounted.json"
        receipt.unlink(missing_ok=True)
        runner = HealthRunner()
        with self.assertRaises(self.health.HealthError) as caught:
            self.health.confirm_transaction(
                self.state_root,
                "tx-001",
                runner=runner,
                checks=(("packages", ["dpkg", "--audit"]),),
            )
        self.assertEqual(caught.exception.code, "E_HEALTH_ROOT")
        self.assertEqual(self.store.load("tx-001")["state"], "rollback_armed")
        self.assertEqual(runner.values["saved_entry"], "ming-legacy")

    def test_health_failure_becomes_terminal_on_the_next_previous_root_boot(self):
        self.booting()
        runner = HealthRunner(failing_check="dpkg --audit")
        with self.assertRaises(self.health.HealthError):
            self.health.confirm_transaction(
                self.state_root,
                "tx-001",
                runner=runner,
                checks=(("packages", ["dpkg", "--audit"]),),
            )
        self.assertEqual(self.store.load("tx-001")["state"], "rollback_armed")

        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        self.assertEqual(selected["action"], "rollback-interrupted")
        self.assertEqual(self.store.load("tx-001")["state"], "rolled_back")
        self.assertFalse((self.state_root / "active-transaction.json").exists())

    def test_reconcile_restores_grub_for_an_active_rollback_only(self):
        state = self.booting()
        state = self.store.transition(
            "tx-001",
            "rollback_armed",
            writer="rollback-service",
            expected_generation=state["generation"],
        )
        runner = HealthRunner()
        runner.values["saved_entry"] = "ming-slot-b"

        result = self.health.reconcile_rollback(self.state_root, runner=runner)

        self.assertEqual(result["transaction_id"], "tx-001")
        self.assertEqual(result["saved_entry"], "ming-legacy")
        self.assertEqual(runner.values["saved_entry"], "ming-legacy")
        self.assertTrue((self.state_root / "active-transaction.json").is_file())

    def test_reconcile_consumes_a_verified_terminal_rollback_receipt(self):
        self.booting()
        self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        state = self.store.load("tx-001")
        self.assertEqual(state["state"], "rolled_back")
        receipt = self.state_root / "boot" / "rollback-pending.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps(
                {
                    "schema": "ming.rollback-pending.v1",
                    "transaction_id": "tx-001",
                    "previous_slot": state["previous_slot"],
                    "candidate_slot": state["candidate_slot"],
                    "generation": state["generation"],
                    "timestamp": state["updated_at"],
                }
            ),
            encoding="utf-8",
        )
        runner = HealthRunner()
        runner.values["saved_entry"] = "ming-slot-b"

        result = self.health.reconcile_rollback(self.state_root, runner=runner)

        self.assertEqual(result["transaction_id"], "tx-001")
        self.assertEqual(runner.values["saved_entry"], "ming-legacy")
        self.assertFalse(receipt.exists())

    def test_reconcile_ignores_historical_rollback_after_a_later_commit(self):
        self.booting("tx-old")
        self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="legacy",
        )
        self.assertEqual(self.store.load("tx-old")["state"], "rolled_back")

        state = self.store.create_transaction(
            transaction_id="tx-new",
            release_id="ming-os-26.3.3-amd64-2",
            previous_slot="A",
            candidate_slot="B",
        )
        for target, writer in (
            ("verified", "verifier"),
            ("staging", "slot-manager"),
            ("staged", "candidate-applicator"),
            ("armed", "boot-coordinator"),
            ("booting", "initramfs"),
            ("pending_health", "health-service"),
            ("committing", "health-confirmer"),
            ("committed", "commit-coordinator"),
        ):
            state = self.store.transition("tx-new", target, writer=writer, expected_generation=state["generation"])

        runner = HealthRunner()
        runner.values["saved_entry"] = "ming-slot-b"
        result = self.health.reconcile_rollback(self.state_root, runner=runner)

        self.assertEqual(result, {"reconciled": False, "reason": "no rollback transaction"})
        self.assertEqual(runner.values["saved_entry"], "ming-slot-b")
        self.assertFalse((self.state_root / "boot" / "rollback-pending.json").exists())

    def test_power_loss_after_saved_entry_rolls_back_on_previous_root(self):
        self.booting()
        runner = HealthRunner()

        def fault(point):
            if point == "after-saved-entry-readback":
                raise RuntimeError("simulated power loss")

        with self.assertRaises(RuntimeError):
            self.health.confirm_transaction(
                self.state_root,
                "tx-001",
                runner=runner,
                checks=(("root", ["ming-health-root"]),),
                fault_hook=fault,
            )
        self.assertEqual(self.store.load("tx-001")["state"], "committing")
        self.assertEqual(runner.values["saved_entry"], "ming-slot-b")

        selected = self.boot.select_root(
            state_root=self.state_root,
            physical_root=self.physical,
            requested_slot="B",
        )
        self.assertEqual(selected["selected_slot"], "legacy")
        self.assertEqual(self.store.load("tx-001")["state"], "rolled_back")
        self.health.reconcile_rollback(self.state_root, runner=runner)
        self.assertEqual(runner.values["saved_entry"], "ming-legacy")

    def test_health_commands_are_bounded_and_structured(self):
        self.booting()
        runner = HealthRunner()
        self.health.confirm_transaction(
            self.state_root,
            "tx-001",
            runner=runner,
            checks=(("services", ["systemctl", "is-system-running"]),),
        )
        health_calls = [kwargs for command, kwargs in runner.calls if command[0] == "systemctl"]
        self.assertEqual(health_calls[0]["timeout"], 10)
        self.assertFalse(health_calls[0].get("shell", False))
        log = self.state_root / "transactions" / "tx-001" / "health.jsonl"
        values = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(values[0]["event"], "check-start")
        self.assertEqual(values[-1]["event"], "commit-complete")
        self.assertTrue(all("timestamp" in value and "transaction_id" in value for value in values))

    def test_default_desktop_probe_is_available_before_graphical_login(self):
        self.assertIn(
            ("desktop", ["/usr/bin/test", "-x", "/usr/local/bin/ming-desktop-healthcheck"]),
            self.health.DEFAULT_CHECKS,
        )
        serialized = json.dumps(self.health.DEFAULT_CHECKS)
        self.assertNotIn("/usr/local/sbin/ming-desktop-healthcheck", serialized)
        self.assertNotIn("--transaction-probe", serialized)

    def test_bootstrap_capability_requires_every_trusted_component(self):
        image = self.root / "image"
        required = self.bootstrap.required_paths(image)
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        (image / "var" / "lib" / "ming-update").mkdir(parents=True, exist_ok=True)
        (image / "var" / "lib" / "ming-update" / "protocol-version").write_text(
            "transactional-slot-v1\n", encoding="utf-8"
        )
        (image / "etc" / "default" / "grub.d" / "40-ming-transaction.cfg").write_text(
            "GRUB_DEFAULT=saved\nGRUB_SAVEDEFAULT=false\n", encoding="utf-8"
        )

        units_root = image / "etc" / "systemd" / "system"
        enabled_targets = {
            units_root / "multi-user.target.wants" / unit: units_root / unit
            for unit in self.bootstrap.REQUIRED_ENABLED_UNITS
        }
        original_is_symlink = pathlib.Path.is_symlink
        original_resolve = pathlib.Path.resolve

        def fixture_is_symlink(path):
            return path in enabled_targets or original_is_symlink(path)

        def fixture_resolve(path, *args, **kwargs):
            if path in enabled_targets:
                return enabled_targets[path]
            return original_resolve(path, *args, **kwargs)

        def fixture_grubenv_reader(_path):
            return {"saved_entry": "ming-legacy"}

        original_detect = self.bootstrap.detect_capability

        def fixture_detect(root="/", *, require_marker=True, grubenv_reader=None):
            return original_detect(
                root,
                require_marker=require_marker,
                grubenv_reader=fixture_grubenv_reader,
            )

        with mock.patch.object(pathlib.Path, "is_symlink", fixture_is_symlink), \
             mock.patch.object(pathlib.Path, "resolve", fixture_resolve), \
             mock.patch.object(self.bootstrap, "detect_capability", fixture_detect):
            status = original_detect(image, require_marker=False, grubenv_reader=fixture_grubenv_reader)
            self.assertTrue(status["available"])
            self.assertEqual(status["capability"], "transactional-slot-v1")
            self.bootstrap.write_capability_marker(image)
            marker = json.loads((image / "var" / "lib" / "ming-update" / "capability.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["bootstrap_version"], "1.0.0")

            status = original_detect(image, grubenv_reader=fixture_grubenv_reader)
            self.assertTrue(status["available"])

            required[0].unlink()
            status = original_detect(image, grubenv_reader=fixture_grubenv_reader)
            self.assertFalse(status["available"])
            self.assertTrue(status["missing"])

    def test_2632_requires_bootstrap_until_capability_is_complete(self):
        unavailable = {"available": False, "capability": None}
        available = {"available": True, "capability": "transactional-slot-v1"}
        self.assertEqual(self.bootstrap.update_path("26.3.2", unavailable), "bootstrap-required")
        self.assertEqual(self.bootstrap.update_path("26.3.2", available), "transactional-slot-v1")
        self.assertEqual(self.bootstrap.update_path("26.3.1", unavailable), "unsupported")

    def test_systemd_health_and_reconcile_units_have_bounded_ordering(self):
        health = HEALTH_UNIT.read_text(encoding="utf-8")
        reconcile = RECONCILE_UNIT.read_text(encoding="utf-8")
        self.assertIn("Before=display-manager.service", health)
        self.assertIn("TimeoutStartSec=60", health)
        self.assertIn("confirm-active", health)
        self.assertIn("reconcile", reconcile)
        self.assertIn("Before=display-manager.service", reconcile)


if __name__ == "__main__":
    unittest.main()
