import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_SPEC = importlib.util.spec_from_file_location(
    "ming_agent_runtime", ROOT / "assets" / "ming-agent-runtime.py"
)
RUNTIME = importlib.util.module_from_spec(RUNTIME_SPEC)
assert RUNTIME_SPEC.loader is not None
RUNTIME_SPEC.loader.exec_module(RUNTIME)
BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "ming_agent_bridge", ROOT / "assets" / "ming-agent-bridge.py"
)
BRIDGE = importlib.util.module_from_spec(BRIDGE_SPEC)
assert BRIDGE_SPEC.loader is not None
BRIDGE_SPEC.loader.exec_module(BRIDGE)


class BridgeContracts(unittest.TestCase):
    def _state(self, root, session_id="desk-a", display=":90"):
        session_dir = pathlib.Path(root) / "sessions" / session_id
        session_dir.mkdir(parents=True)
        state = {
            "protocol": "ming.agent.v1",
            "session_id": session_id,
            "uid": __import__("os").getuid(),
            "display": display,
            "dbus_session_bus_address": "unix:path=/tmp/agent-bus",
            "runtime_dir": str(root),
            "pids": {"xvfb": 9000, "dbus": 9001, "wm": 9002},
        }
        (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def test_capabilities_use_versioned_json_envelope(self):
        result = BRIDGE.dispatch(["capabilities"], runtime_root=pathlib.Path("/tmp/ming-agent-test"))
        self.assertEqual("ming.agent.v1", result["protocol"])
        self.assertTrue(result["ok"])
        self.assertIn("screen", result["capabilities"])
        self.assertIn("store", result["capabilities"])

    def test_bridge_rejects_foreground_display_reuse(self):
        with tempfile.TemporaryDirectory() as root:
            self._state(root, display=":0")
            result = BRIDGE.dispatch(
                ["screen", "status", "--session", "desk-a"],
                runtime_root=pathlib.Path(root), foreground_display=":0",
            )
            self.assertFalse(result["ok"])
            self.assertEqual("foreground_display_rejected", result["state"])

    def test_bridge_rejects_unknown_action_without_running_a_command(self):
        result = BRIDGE.dispatch(["screen", "shell", "--session", "desk-a"])
        self.assertFalse(result["ok"])
        self.assertEqual("action_not_allowed", result["state"])


if __name__ == "__main__":
    unittest.main()
