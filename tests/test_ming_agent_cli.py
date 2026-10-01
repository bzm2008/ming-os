import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CORE = load("ming_agent_core", ROOT / "assets" / "ming-agent-core.py")
CLI = load("ming_agent_cli", ROOT / "assets" / "ming-agent.py")


class MingAgentCliContracts(unittest.TestCase):
    def test_cli_and_shared_core_are_deployed_by_desktop_module(self):
        self.assertIn("ming-agent-core.py", DESKTOP)
        self.assertIn("install -m 0755 \"${asset_dir}/ming-agent.py\" /usr/local/bin/ming-agent", DESKTOP)
        self.assertIn('"${lib_dir}/ming-agent-core.py"', DESKTOP)

    def test_capabilities_expose_unified_v1_surface(self):
        result = CLI.dispatch(["capabilities"])
        self.assertTrue(result["ok"])
        self.assertEqual("ming.agent.v1", result["protocol"])
        for group in ("session", "screen", "settings", "device", "apps", "files", "store", "system", "foreground"):
            self.assertIn(group, result["capabilities"])

    def test_unknown_command_returns_json_error_envelope(self):
        result = CLI.dispatch(["shell", "uname", "-a"])
        self.assertFalse(result["ok"])
        self.assertEqual("action_not_allowed", result["state"])
        self.assertIn("request_id", result)

    def test_foreground_screen_requires_an_active_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            result = CLI.dispatch(
                ["screen", "status", "--target", "foreground"],
                runtime_root=pathlib.Path(directory),
                environ={"DISPLAY": ":0", "XDG_SESSION_ID": "session-a"},
            )
        self.assertFalse(result["ok"])
        self.assertEqual("foreground_not_authorized", result["state"])


class ForegroundGrantContracts(unittest.TestCase):
    def test_grant_is_session_bound_and_revocable(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CORE.ForegroundGrantManager(pathlib.Path(directory), uid=os.getuid())
            granted = manager.grant(
                {"screen.read", "screen.input"},
                session_id="session-a", display=":0", confirmed=True,
            )
            self.assertTrue(granted["ok"])
            grant_path = pathlib.Path(directory) / "foreground-grant.json"
            self.assertEqual(0o600, grant_path.stat().st_mode & 0o777)
            self.assertTrue(manager.require("screen.read", "session-a", ":0")["ok"])
            self.assertFalse(manager.require("screen.read", "session-b", ":0")["ok"])
            revoked = manager.revoke(session_id="session-a")
            self.assertTrue(revoked["ok"])
            self.assertFalse(manager.require("screen.read", "session-a", ":0")["ok"])

    def test_grant_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CORE.ForegroundGrantManager(pathlib.Path(directory), uid=os.getuid())
            result = manager.grant({"screen.read"}, "session-a", ":0", confirmed=False)
        self.assertFalse(result["ok"])
        self.assertEqual("confirmation_required", result["state"])


class ControlledFileContracts(unittest.TestCase):
    def test_rejects_path_escape_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            allowed = root / "Documents"
            allowed.mkdir()
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            (allowed / "escape").symlink_to(outside)
            files = CORE.ControlledFiles((allowed,))
            self.assertFalse(files.read(pathlib.Path(directory) / "outside.txt")["ok"])
            self.assertEqual("path_rejected", files.read(allowed / "escape")["state"])

    def test_write_requires_confirmation_and_stays_in_allowed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            allowed = pathlib.Path(directory) / "Documents"
            allowed.mkdir()
            files = CORE.ControlledFiles((allowed,))
            denied = files.write(allowed / "note.txt", "hello", confirmed=False)
            self.assertEqual("confirmation_required", denied["state"])
            written = files.write(allowed / "note.txt", "hello", confirmed=True)
            self.assertTrue(written["ok"])
            self.assertEqual("hello", (allowed / "note.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
