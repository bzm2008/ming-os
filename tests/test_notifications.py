import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTIFICATIONS_PATH = ROOT / "assets" / "ming-notifications.py"


def load_notifications():
    spec = importlib.util.spec_from_file_location("ming_notifications", NOTIFICATIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notifications = load_notifications()

    def test_parser_skips_malformed_lines_and_sanitizes_fields(self):
        text = "\n".join([
            '{"timestamp":"2026-07-11T10:00:00","app_name":"Mail","summary":"Hello\\u0000","body":"World"}',
            "not a notification",
            "[2026-07-11 10:01:00] Browser | Download | Finished",
        ])
        items = self.notifications.parse_notification_log(text)
        self.assertEqual(["Hello", "Download"], [item.summary for item in items])
        self.assertEqual("World", items[0].body)
        self.assertEqual("Browser", items[1].app_name)

    def test_parser_keeps_only_newest_fifty(self):
        text = "\n".join(
            '{{"timestamp":"{0:03}","summary":"Item {0}"}}'.format(index)
            for index in range(75)
        )
        items = self.notifications.parse_notification_log(text)
        self.assertEqual(50, len(items))
        self.assertEqual("Item 25", items[0].summary)
        self.assertEqual("Item 74", items[-1].summary)

    def test_parser_supports_xfce_xml_and_rejects_oversized_input(self):
        xml = '<log><entry timestamp="now" app_name="Chat" summary="Hi" body="There" /></log>'
        self.assertEqual("Hi", self.notifications.parse_notification_log(xml)[0].summary)
        self.assertEqual([], self.notifications.parse_notification_log("x" * (2 * 1024 * 1024 + 1)))

    def test_parser_supports_legacy_xfce_keyfile_log(self):
        text = "[2026-07-11T10:00:00]\napp_name=Mail\nsummary=Hello\nbody=World\n"
        item = self.notifications.parse_notification_log(text)[0]
        self.assertEqual(("Mail", "Hello", "World"), (item.app_name, item.summary, item.body))

    def test_atomic_clear_replaces_file_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "log"
            path.write_text("old", encoding="utf-8")
            path.chmod(0o640)
            before = path.stat().st_ino
            effective_mode = path.stat().st_mode & 0o777
            self.notifications.clear_notification_log_atomic(path)
            self.assertEqual("", path.read_text(encoding="utf-8"))
            self.assertEqual(effective_mode, path.stat().st_mode & 0o777)
            self.assertNotEqual(before, path.stat().st_ino)

    def test_dnd_model_returns_structured_argv(self):
        model = self.notifications.dnd_command(True)
        self.assertEqual(
            ("xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb", "-s", "true"),
            model.argv,
        )
        self.assertTrue(model.enabled)

    def test_loads_and_atomically_clears_current_xfce_sqlite_log(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = pathlib.Path(tempdir) / "log.sqlite"
            database = sqlite3.connect(path)
            try:
                database.execute(
                    "CREATE TABLE notifications (timestamp INTEGER, app_name TEXT, "
                    "summary TEXT, body TEXT, icon_id TEXT)"
                )
                database.executemany(
                    "INSERT INTO notifications VALUES (?, ?, ?, ?, ?)",
                    [(index, "App", "Item {}".format(index), "x" * 50000, "icon") for index in range(55)],
                )
                database.commit()
            finally:
                database.close()
            items = self.notifications.load_notification_log(path)
            self.assertEqual(50, len(items))
            self.assertEqual("Item 5", items[0].summary)
            self.assertEqual("Item 54", items[-1].summary)
            self.notifications.clear_notification_log_atomic(path)
            database = sqlite3.connect(path)
            try:
                self.assertEqual(0, database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0])
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
