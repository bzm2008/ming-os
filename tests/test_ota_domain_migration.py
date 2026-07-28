import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA = ROOT / "modules" / "06_ota_update.sh"
GIT_BASH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")


def git_path(path):
    value = str(path.resolve()).replace("\\", "/")
    return "/%s%s" % (value[0].lower(), value[2:])


def generated_cli():
    module = OTA.read_text(encoding="utf-8")
    marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
    return module.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]


class OtaDomainMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GIT_BASH.is_file():
            raise unittest.SkipTest("Git Bash is unavailable")

    def run_migration(self, response, signature_ok=True,
                      current_server="https://ming.scallion.uno", curl_ok=True):
        with tempfile.TemporaryDirectory(prefix="ming-ota-domain-") as directory:
            root = pathlib.Path(directory)
            config = root / "config"
            cache = root / "cache"
            config.mkdir()
            cache.mkdir()
            public_key = root / "ota.pub"
            public_key.write_text("untrusted comment: test\nRWQtest\n", encoding="utf-8")
            (config / "config.json").write_text(
                json.dumps({
                    "update_server": current_server,
                    "api_endpoint": "/api/onion-update",
                    "channel": "stable",
                }),
                encoding="utf-8",
            )

            cli = generated_cli().replace(
                'readonly CONFIG_DIR="/etc/ming-update"',
                'readonly CONFIG_DIR="%s"' % git_path(config),
            ).replace(
                'readonly CACHE_DIR="/var/cache/ming-update"',
                'readonly CACHE_DIR="%s"' % git_path(cache),
            ).replace(
                'readonly OTA_RELEASE_PUBLIC_KEY="/etc/ming-update/ota-release.minisign.pub"',
                'readonly OTA_RELEASE_PUBLIC_KEY="%s"' % git_path(public_key),
            )
            prefix = cli.split('case "${1:-help}" in', 1)[0]
            injected = r'''
current_version() { printf '%s\n' '26.4.0'; }
curl() {
    printf '%s\n' "$*" > "${MING_TEST_CURL_ARGS}"
    [[ "${MING_TEST_CURL_RC}" == 0 ]] || return "${MING_TEST_CURL_RC}"
    printf '%s' "${MING_DOMAIN_RESPONSE}"
}
minisign() { return "${MING_TEST_SIGNATURE_RC}"; }
maybe_migrate_update_server
get_config '.update_server'
'''
            script = root / "migration.sh"
            script.write_text(prefix + injected, encoding="utf-8", newline="\n")
            curl_args = root / "curl-args.txt"
            result = subprocess.run(
                [str(GIT_BASH), git_path(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env={
                    **os.environ,
                    "MING_DOMAIN_RESPONSE": json.dumps(response),
                    "MING_TEST_SIGNATURE_RC": "0" if signature_ok else "1",
                    "MING_TEST_CURL_RC": "0" if curl_ok else "22",
                    "MING_TEST_CURL_ARGS": git_path(curl_args),
                },
            )
            saved = json.loads((config / "config.json").read_text(encoding="utf-8"))
            args = curl_args.read_text(encoding="utf-8") if curl_args.exists() else ""
            return result, saved, args

    @staticmethod
    def signed_manifest(**overrides):
        manifest = {
            "has_update": True,
            "ready": True,
            "version": "26.4.1",
            "update_type": "major",
            "download_url": "https://ming.sca-hub.cn/download/test.iso",
            "checksum": "a" * 64,
            "signature": "RWQtestsignature",
            "trusted_comment": "Ming OS OTA test",
        }
        manifest.update(overrides)
        return manifest

    def test_migrates_saved_legacy_domain_only_after_tls_schema_and_signature_pass(self):
        result, saved, curl_args = self.run_migration(self.signed_manifest())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("https://ming.sca-hub.cn", saved["update_server"])
        self.assertIn("--proto =https", curl_args)
        self.assertIn("--tlsv1.2", curl_args)

    def test_keeps_legacy_domain_when_candidate_signature_fails(self):
        result, saved, _args = self.run_migration(self.signed_manifest(), signature_ok=False)

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("https://ming.scallion.uno", saved["update_server"])

    def test_keeps_legacy_domain_when_candidate_schema_is_incomplete(self):
        result, saved, _args = self.run_migration(
            self.signed_manifest(checksum="not-a-sha256")
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("https://ming.scallion.uno", saved["update_server"])

    def test_live_discovery_no_update_schema_is_recognized_but_not_migrated_unsigned(self):
        discovery = {
            "schema": "ming.update.discovery.v1",
            "available": False,
            "current_version": "26.4.0",
            "architecture": "amd64",
            "capability": "transactional-slot-v1",
            "delivery": "none",
        }

        result, saved, _args = self.run_migration(discovery, signature_ok=False)

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("structure", result.stderr.lower())
        self.assertEqual("https://ming.scallion.uno", saved["update_server"])

    def test_default_new_domain_uses_same_preflight_and_falls_back_on_invalid_schema(self):
        result, saved, _args = self.run_migration(
            {"available": False}, current_server="https://ming.sca-hub.cn"
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("https://ming.scallion.uno", saved["update_server"])

    def test_default_new_domain_falls_back_when_tls_probe_fails(self):
        result, saved, _args = self.run_migration(
            {}, current_server="https://ming.sca-hub.cn", curl_ok=False
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("https://ming.scallion.uno", saved["update_server"])

    def test_module_keeps_online_discovery_closed_until_candidate_validation(self):
        module = OTA.read_text(encoding="utf-8")
        self.assertIn('readonly UPDATE_SERVER="https://ming.sca-hub.cn"', module)
        self.assertIn('readonly LEGACY_UPDATE_SERVER="https://ming.scallion.uno"', module)
        self.assertIn("maybe_migrate_update_server", module)


if __name__ == "__main__":
    unittest.main()
