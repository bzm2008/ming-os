import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-tea-core.py"
PLUGIN_PATH = ROOT / "assets" / "ming-tea-plugins.json"
COMMUNITY_PATH = ROOT / "assets" / "ming-tea-community-candidates.json"


def load_core():
    spec = importlib.util.spec_from_file_location("ming_tea_core", CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingTeaCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_core()

    def test_exposes_three_product_scenes_with_shared_tool_contract(self):
        self.assertEqual(
            set(self.api.SCENES),
            {"office", "development", "learning"},
        )
        for scene in self.api.SCENES.values():
            self.assertTrue(scene["label"])
            self.assertTrue(scene["prompt"])
            self.assertTrue(scene["tools"])
            self.assertTrue(scene["risk_profile"])

    def test_permission_policy_allows_reading_but_confirms_privileged_actions(self):
        policy = self.api.PermissionPolicy()
        self.assertEqual(policy.decide("file.read"), "allow")
        self.assertEqual(policy.decide("terminal.run", {"command": "ls"}), "allow")
        self.assertEqual(
            policy.decide("terminal.run", {"command": "sudo apt install demo"}),
            "confirm",
        )
        self.assertEqual(
            policy.decide("file.delete", {"path": "/tmp/demo"}),
            "confirm",
        )

    def test_approval_token_is_single_use(self):
        policy = self.api.PermissionPolicy()
        token = policy.issue_approval("terminal.run", {"command": "sudo true"})
        self.assertTrue(policy.consume_approval(token, "terminal.run"))
        self.assertFalse(policy.consume_approval(token, "terminal.run"))

    def test_plugin_catalog_rejects_missing_license_or_permissions(self):
        catalog = self.api.PluginCatalog(PLUGIN_PATH)
        self.assertGreaterEqual(len(catalog.plugins), 3)
        self.assertTrue(all(plugin["license"] for plugin in catalog.plugins))
        self.assertTrue(all(plugin["permissions"] for plugin in catalog.plugins))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as handle:
            json.dump({"plugins": [{"id": "broken"}]}, handle)
            handle.flush()
            with self.assertRaises(self.api.PluginCatalogError):
                self.api.PluginCatalog(pathlib.Path(handle.name))

    def test_community_candidates_are_audited_without_auto_installing_external_plugins(self):
        candidates = json.loads(COMMUNITY_PATH.read_text(encoding="utf-8"))["candidates"]
        statuses = {candidate["id"]: candidate["status"] for candidate in candidates}
        by_id = {candidate["id"]: candidate for candidate in candidates}
        for plugin_id in ("dsh-agent-identity", "dsh-session-workbench", "cleverer-dsh"):
            self.assertEqual(by_id[plugin_id]["status"], "adapted-local")
            self.assertIn("community-adapters.ts#", by_id[plugin_id]["adapter"])
        self.assertEqual(by_id["everos-memory"]["status"], "optional-adapter-ready")
        self.assertEqual(statuses["honcho-memory"], "external-connector-only")
        self.assertEqual(statuses["dsh-data-agent"], "blocked-version")
        self.assertEqual(statuses["unresolved-user-names"], "needs-source")
        installed = {plugin["id"] for plugin in json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))["plugins"]}
        self.assertNotIn("dsh-data-agent", installed)

    def test_event_protocol_rejects_unknown_events_and_redacts_api_keys(self):
        runtime = self.api.MingTeaRuntime(audit_path=pathlib.Path(tempfile.mktemp()))
        event = runtime.normalize_event(
            {
                "type": "tool.completed",
                "session_id": "demo",
                "detail": "api_key=sk-secret should not be persisted",
            }
        )
        self.assertEqual(event["type"], "tool.completed")
        self.assertNotIn("sk-secret", json.dumps(event))
        with self.assertRaises(self.api.ProtocolError):
            runtime.normalize_event({"type": "made.up.event"})

    def test_session_routes_scene_and_returns_approval_event_for_privileged_tool(self):
        runtime = self.api.MingTeaRuntime(audit_path=pathlib.Path(tempfile.mktemp()))
        session = runtime.create_session("development")
        self.assertEqual(session["scene"], "development")
        events = runtime.submit_tool_request(
            session["session_id"],
            "terminal.run",
            {"command": "sudo apt install demo"},
        )
        self.assertEqual(events[0]["type"], "tool.requested")
        self.assertEqual(events[1]["type"], "approval.requested")

    def test_dsh_adapter_is_optional_and_reports_runtime_status(self):
        adapter = self.api.DshAdapter(command=["definitely-missing-dsh"])
        self.assertFalse(adapter.available())
        result = adapter.start()
        self.assertFalse(result["ok"])
        self.assertIn("DSH", result["error"])


if __name__ == "__main__":
    unittest.main()
