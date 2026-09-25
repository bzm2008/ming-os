import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_agent_service", ROOT / "assets" / "ming-agent-service.py"
)
SERVICE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SERVICE)


class AgentServiceContracts(unittest.TestCase):
    def test_service_metadata_is_stable_and_foreground_assist_is_disabled(self):
        metadata = SERVICE.service_metadata()
        self.assertEqual("org.mingos.Agent1", metadata["bus_name"])
        self.assertEqual("/org/mingos/Agent1", metadata["object_path"])
        self.assertFalse(metadata["foreground_assist"])

    def test_dispatch_json_returns_bridge_error_without_session(self):
        result = SERVICE.dispatch_json('{"screen": "status"}')
        self.assertFalse(result["ok"])
        self.assertEqual("invalid_request", result["state"])


if __name__ == "__main__":
    unittest.main()
