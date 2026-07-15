import json
import pathlib
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "ota"
FIXTURES = CONTRACTS / "fixtures"

SCHEMAS = {
    "discovery": "discovery-v1.schema.json",
    "cli": "cli-v1.schema.json",
    "manifest": "transaction-manifest-v1.schema.json",
    "content_index": "content-index-v1.schema.json",
    "state": "state-v1.schema.json",
}

EXPECTED_IDENTITIES = {
    "discovery": "ming.update.discovery.v1",
    "cli": "ming.update.cli.v1",
    "manifest": "ming.transaction-manifest.v1",
    "content_index": "ming.content-index.v1",
    "state": "ming.transaction-state.v1",
}


class DuplicateKeyError(ValueError):
    pass


def load_json_strict(path):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateKeyError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    with pathlib.Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=reject_duplicates)


class TransactionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            name: load_json_strict(CONTRACTS / filename)
            for name, filename in SCHEMAS.items()
        }
        cls.validators = {
            name: Draft202012Validator(schema, format_checker=FormatChecker())
            for name, schema in cls.schemas.items()
        }

    def validate(self, schema_name, fixture_name):
        value = load_json_strict(FIXTURES / fixture_name)
        errors = sorted(self.validators[schema_name].iter_errors(value), key=lambda error: list(error.path))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))
        return value

    def test_frozen_schema_identities_match_v1(self):
        for name, identity in EXPECTED_IDENTITIES.items():
            with self.subTest(schema=name):
                self.assertEqual(identity, self.schemas[name]["x-ming-schema-name"])

    def test_valid_discovery_fixture_matches_v1(self):
        for fixture_name in (
            "bootstrap-required.discovery.json",
            "transactional-available.discovery.json",
            "no-update.discovery.json",
        ):
            with self.subTest(fixture=fixture_name):
                value = self.validate("discovery", fixture_name)
                self.assertEqual("ming.update.discovery.v1", value["schema"])
                serialized = json.dumps(value, sort_keys=True)
                for forbidden in ("command", "path", "grub", "keyring", "calamares"):
                    self.assertNotIn(forbidden, serialized.lower())

    def test_valid_cli_success_and_error_fixtures_match_v1(self):
        for fixture_name in (
            "bootstrap-required.cli.json",
            "transactional-available.cli.json",
            "no-update.cli.json",
            "staging.cli.json",
            "reboot-required.cli.json",
            "committed.cli.json",
            "rollback.cli.json",
            "signature-failure.cli.json",
            "space-refusal.cli.json",
            "unsupported-protocol.cli.json",
        ):
            with self.subTest(fixture=fixture_name):
                value = self.validate("cli", fixture_name)
                self.assertEqual("ming.update.cli.v1", value["schema"])
                self.assertIsInstance(value["ok"], bool)

    def test_valid_manifest_and_content_index_match_v1(self):
        manifest = self.validate("manifest", "transactional-available.manifest.json")
        content_index = self.validate("content_index", "transactional-available.content-index.json")
        self.assertEqual(manifest["release_id"], content_index["release_id"])

    def test_content_index_allows_relative_symlink_targets_for_runtime_root_validation(self):
        content_index = self.validate("content_index", "transactional-available.content-index.json")
        content_index["entries"].append({
            "path": "usr/bin/ming-example",
            "type": "symlink",
            "target": "../lib/ming-os/example",
            "mode": 511,
            "uid": 0,
            "gid": 0,
            "config_policy": "replace",
        })
        errors = list(self.validators["content_index"].iter_errors(content_index))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_state_fixture_rejects_unknown_transition_fields(self):
        value = self.validate("state", "rollback.state.json")
        invalid = dict(value, unexpected_transition="candidate-wins")
        errors = list(self.validators["state"].iter_errors(invalid))
        self.assertTrue(errors)

    def test_duplicate_json_keys_are_rejected_before_schema_validation(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-contract-") as directory:
            path = pathlib.Path(directory) / "duplicate.json"
            path.write_text('{"schema":"ming.update.discovery.v1","schema":"ming.update.discovery.v1"}', encoding="utf-8")
            with self.assertRaises(DuplicateKeyError):
                load_json_strict(path)

    def test_unknown_major_schema_is_rejected(self):
        value = self.validate("discovery", "transactional-available.discovery.json")
        invalid = dict(value, schema="ming.update.discovery.v2")
        self.assertTrue(list(self.validators["discovery"].iter_errors(invalid)))

    def test_cli_v1_allows_additive_response_fields_only(self):
        cli_value = self.validate("cli", "transactional-available.cli.json")
        self.assertEqual([], list(self.validators["cli"].iter_errors(dict(cli_value, extension={"future": True}))))

        manifest_value = self.validate("manifest", "transactional-available.manifest.json")
        self.assertTrue(list(self.validators["manifest"].iter_errors(dict(manifest_value, extension=True))))


if __name__ == "__main__":
    unittest.main()
