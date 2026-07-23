import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-transaction-rollback.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ming_transaction_rollback", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE_PATH.exists(), "transaction rollback journal is not implemented")
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="ming-transaction-rollback-")
        self.root = pathlib.Path(self.tmp.name)
        self.candidate = self.root / "candidate"
        self.transaction = self.root / "transaction"
        self.candidate.mkdir()
        self.transaction.mkdir()
        self.journal = self.module.RollbackJournal(self.transaction, self.candidate)

    def tearDown(self):
        self.tmp.cleanup()

    def test_restores_replaced_file_and_removes_new_file(self):
        target = self.candidate / "usr" / "share" / "ming-os" / "value"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")
        self.journal.capture("usr/share/ming-os/value")
        target.write_text("new", encoding="utf-8")

        created = self.candidate / "usr" / "share" / "ming-os" / "created"
        self.journal.capture("usr/share/ming-os/created")
        created.write_text("new", encoding="utf-8")

        self.journal.rollback(reason="simulated interruption")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertFalse(created.exists())

        events = [json.loads(line) for line in (self.transaction / "rollback.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["event"], "rollback-complete")
        self.assertEqual(events[-1]["reason"], "simulated interruption")

    def test_restores_symlink_without_following_it(self):
        real = self.candidate / "usr" / "share" / "real"
        real.parent.mkdir(parents=True)
        real.write_text("real", encoding="utf-8")
        link = self.candidate / "usr" / "share" / "link"
        try:
            link.symlink_to("real")
        except OSError:
            self.skipTest("symlink creation is unavailable")
        self.journal.capture("usr/share/link")
        link.unlink()
        link.write_text("replacement", encoding="utf-8")

        self.journal.rollback(reason="symlink test")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), pathlib.Path("real"))

    def test_rejects_home_traversal_absolute_and_candidate_symlink_parent(self):
        for path in ("home/user/file", "../etc/shadow", "/etc/shadow"):
            with self.subTest(path=path):
                with self.assertRaises(self.module.RollbackError) as caught:
                    self.journal.capture(path)
                self.assertEqual(caught.exception.code, "E_CONTENT_POLICY")

        outside = self.root / "outside"
        outside.mkdir()
        parent = self.candidate / "usr"
        try:
            parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(self.module.RollbackError) as caught:
            self.journal.capture("usr/escaped")
        self.assertEqual(caught.exception.code, "E_CONTENT_POLICY")

    def test_capture_is_idempotent_and_journal_is_structured(self):
        target = self.candidate / "etc" / "example"
        target.parent.mkdir()
        target.write_text("one", encoding="utf-8")
        first = self.journal.capture("etc/example")
        target.write_text("two", encoding="utf-8")
        second = self.journal.capture("etc/example")
        self.assertEqual(first, second)

        records = [json.loads(line) for line in (self.transaction / "rollback.jsonl").read_text(encoding="utf-8").splitlines()]
        captures = [record for record in records if record["event"] == "capture"]
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0]["path"], "etc/example")
        self.assertIn("timestamp", captures[0])
        self.assertIn("backup_sha256", captures[0])


if __name__ == "__main__":
    unittest.main()
