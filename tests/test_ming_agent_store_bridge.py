import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_agent_bridge", ROOT / "assets" / "ming-agent-bridge.py"
)
BRIDGE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BRIDGE)


class FakeStore:
    def __init__(self):
        self.calls = []

    def search(self, query="", source_id=None):
        self.calls.append(("search", query, source_id))
        return [{"app_id": "demo", "name": "Demo", "source_id": source_id or "debian-apt"}]

    def available_updates(self, section=None):
        self.calls.append(("updates", section))
        return [{"app_id": "demo", "version": "2.0"}]

    def run_transaction(self, action, source_id, app_id, progress_callback=None):
        self.calls.append(("transaction", action, source_id, app_id))
        return {"ok": True, "state": "succeeded", "action": action, "provider": source_id, "app_id": app_id}

    def refresh_provider(self, source_id):
        self.calls.append(("refresh_provider", source_id))
        return {"source_id": source_id, "ok": True, "count": 1}


class StoreBridgeContracts(unittest.TestCase):
    def test_read_only_search_uses_store_controller_without_shell(self):
        store = FakeStore()
        result = BRIDGE.dispatch(
            ["store", "search", "--query", "demo", "--source", "spark-public"],
            store_controller_factory=lambda: store,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertEqual("search", store.calls[0][0])
        self.assertEqual("spark-public", store.calls[0][2])
        self.assertEqual("demo", result["result"][0]["app_id"])

    def test_mutation_requires_explicit_source_and_app_identity(self):
        store = FakeStore()
        result = BRIDGE.dispatch(
            ["store", "install", "--source", "spark-public"],
            store_controller_factory=lambda: store,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("invalid_request", result["state"])
        self.assertEqual([], store.calls)

    def test_mutation_delegates_to_existing_transaction_controller(self):
        store = FakeStore()
        result = BRIDGE.dispatch(
            ["store", "update", "--source", "debian-apt", "--app-id", "demo"],
            store_controller_factory=lambda: store,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("succeeded", result["state"])
        self.assertEqual(("transaction", "update", "debian-apt", "demo"), store.calls[0])

    def test_refresh_delegates_to_provider_refresh_without_app_id(self):
        store = FakeStore()
        result = BRIDGE.dispatch(
            ["store", "refresh", "--source", "spark-public"],
            store_controller_factory=lambda: store,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertEqual(("refresh_provider", "spark-public"), store.calls[0])


if __name__ == "__main__":
    unittest.main()
