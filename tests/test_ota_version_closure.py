import importlib.util
import json
import pathlib
import types
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "assets" / "ming-update-cli.py"
ENGINE_PATH = ROOT / "assets" / "ming-transaction-engine.py"
FIXTURES = ROOT / "contracts" / "ota" / "fixtures"
MANIFEST_SCHEMA = ROOT / "contracts" / "ota" / "transaction-manifest-v1.schema.json"
DISCOVERY_SCHEMA = ROOT / "contracts" / "ota" / "discovery-v1.schema.json"
FORMAL_MANIFEST = FIXTURES / "formal-26.4.0.1.manifest.json"
FORMAL_NO_UPDATE = FIXTURES / "formal-26.4.0.1.no-update.discovery.json"
DESKTOP = ROOT / "modules" / "03_desktop.sh"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionPath:
    def __init__(self, path, files):
        self.path = str(path)
        self.files = files

    @property
    def name(self):
        return pathlib.PurePosixPath(self.path).name

    def read_text(self, encoding=None):
        del encoding
        if self.path not in self.files:
            raise FileNotFoundError(self.path)
        return self.files[self.path]


class LocalVersionPriorityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_module("ming_update_cli_version_contract", CLI_PATH)
        cls.engine = load_module("ming_transaction_engine_version_contract", ENGINE_PATH)

    def assert_readers_return(self, expected, files):
        for label, module, reader in (
            ("update CLI", self.cli, self.cli._version),
            ("transaction engine", self.engine, self.engine._read_version),
        ):
            with self.subTest(reader=label):
                original_pathlib = module.pathlib
                module.pathlib = types.SimpleNamespace(
                    Path=lambda path: VersionPath(path, files),
                )
                try:
                    self.assertEqual(expected, reader())
                finally:
                    module.pathlib = original_pathlib

    def test_canonical_marker_wins_when_all_markers_conflict(self):
        self.assert_readers_return("26.4.0.1", {
            "/etc/ming-version": "26.4.0.1\n",
            "/etc/ming-os-version": "26.3.3\n",
            "/etc/os-release": 'VERSION_ID="26.3.2"\n',
        })

    def test_legacy_marker_is_used_when_canonical_marker_is_missing(self):
        self.assert_readers_return("26.3.3", {
            "/etc/ming-os-version": "26.3.3\n",
            "/etc/os-release": 'VERSION_ID="26.3.2"\n',
        })

    def test_os_release_is_the_final_fallback(self):
        self.assert_readers_return("26.3.2", {
            "/etc/os-release": 'NAME="Ming OS"\nVERSION_ID="26.3.2"\n',
        })


class FormalOtaFixtureContracts(unittest.TestCase):
    def load_and_validate(self, fixture, schema):
        value = json.loads(fixture.read_text(encoding="utf-8"))
        contract = json.loads(schema.read_text(encoding="utf-8"))
        errors = list(
            Draft202012Validator(
                contract,
                format_checker=FormatChecker(),
            ).iter_errors(value)
        )
        self.assertEqual([], errors, "\n".join(error.message for error in errors))
        return value

    def test_formal_manifest_freezes_transaction_version_and_sources(self):
        manifest = self.load_and_validate(FORMAL_MANIFEST, MANIFEST_SCHEMA)
        self.assertEqual("26.4.0.1", manifest["version"])
        self.assertEqual("stable", manifest["channel"])
        self.assertEqual("transactional-slot-v1", manifest["delivery"])
        self.assertEqual(
            ["26.3.2", "26.3.3", "26.4.0", "26.4.0.1-development"],
            manifest["from_versions"],
        )

    def test_formal_current_version_remains_no_update(self):
        discovery = self.load_and_validate(FORMAL_NO_UPDATE, DISCOVERY_SCHEMA)
        self.assertEqual("26.4.0.1", discovery["current_version"])
        self.assertFalse(discovery["available"])
        self.assertEqual("transactional-slot-v1", discovery["capability"])
        self.assertEqual("none", discovery["delivery"])
        for forbidden in (
            "release_id",
            "version",
            "manifest_url",
            "manifest_signature_url",
            "manifest_sha256",
            "bootstrap",
        ):
            self.assertNotIn(forbidden, discovery)


class DesktopOtaVersionContracts(unittest.TestCase):
    def test_installed_release_handoff_uses_the_formal_manifest_contract(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn(
            '`from_versions: ["26.3.2", "26.3.3", "26.4.0", "26.4.0.1-development"]`',
            desktop,
        )
        self.assertIn('`version: "26.4.0.1"`', desktop)
        self.assertNotIn(
            '`from_versions: ["26.3.2"]` and `version: "26.4.0"`',
            desktop,
        )


if __name__ == "__main__":
    unittest.main()
