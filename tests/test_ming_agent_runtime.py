import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_agent_runtime", ROOT / "assets" / "ming-agent-runtime.py"
)
RUNTIME = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RUNTIME)


class FakeProcess:
    _next_pid = 9000

    def __init__(self, command, env=None):
        self.command = list(command)
        self.env = dict(env or {})
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class FakeRunner:
    def __init__(self):
        self.processes = []

    def popen(self, command, env=None):
        process = FakeProcess(command, env)
        self.processes.append(process)
        return process

    def start_dbus(self, address, env):
        process = FakeProcess(["dbus-daemon", "--session", address], env)
        self.processes.append(process)
        return process


class AgentRuntimeContracts(unittest.TestCase):
    def test_create_isolates_display_and_writes_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            manager = RUNTIME.AgentSessionManager(
                runtime_root=pathlib.Path(directory), runner=runner,
                foreground_display=":0"
            )
            result = manager.create("desk-a", display=":90")
            self.assertTrue(result["ok"])
            self.assertEqual("ming.agent.v1", result["protocol"])
            self.assertNotEqual(":0", result["session"]["display"])
            state_path = pathlib.Path(directory) / "sessions" / "desk-a" / "state.json"
            self.assertEqual(0o600, state_path.stat().st_mode & 0o777)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(":90", state["display"])
            self.assertTrue(all(process.env["DISPLAY"] == ":90" for process in runner.processes))

    def test_foreground_display_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RUNTIME.AgentSessionManager(
                runtime_root=pathlib.Path(directory), runner=FakeRunner(),
                foreground_display=":0"
            )
            result = manager.create("desk-a", display=":0")
            self.assertFalse(result["ok"])
            self.assertEqual("foreground_display_rejected", result["state"])

    def test_requested_display_collision_is_rejected_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            manager = RUNTIME.AgentSessionManager(
                runtime_root=pathlib.Path(directory), runner=runner,
                foreground_display=":0"
            )
            manager._display_available = lambda _display: False
            result = manager.create("desk-a", display=":90")
            self.assertFalse(result["ok"])
            self.assertEqual("display_unavailable", result["state"])
            self.assertEqual([], runner.processes)

    def test_invalid_session_id_and_idempotent_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RUNTIME.AgentSessionManager(
                runtime_root=pathlib.Path(directory), runner=FakeRunner()
            )
            invalid = manager.create("../desktop", display=":90")
            self.assertFalse(invalid["ok"])
            self.assertEqual("invalid_session", invalid["state"])
            first = manager.stop("missing")
            second = manager.stop("missing")
            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual("not_found", first["state"])


if __name__ == "__main__":
    unittest.main()
