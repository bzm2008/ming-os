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


def generated_cli_prefix():
    return generated_cli().split('case "${1:-help}" in', 1)[0]


class Ota2641CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GIT_BASH.is_file():
            raise unittest.SkipTest("Git Bash is unavailable")
        cls.cli = generated_cli()
        cls.cli_prefix = generated_cli_prefix()

    def route(self, source, target):
        with tempfile.TemporaryDirectory(prefix="ming-ota-2641-") as directory:
            script = pathlib.Path(directory) / "route.sh"
            script.write_text(
                self.cli_prefix + '\nvalidate_update_route "$@"\n',
                encoding="utf-8",
                newline="\n",
            )
            return subprocess.run(
                [str(GIT_BASH), git_path(script), source, target],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )

    def check(self, source):
        response = json.dumps({
            "has_update": True,
            "ready": True,
            "version": "26.4.1",
            "update_type": "major",
            "release_notes": "26.4.1 compatibility test",
        })
        with tempfile.TemporaryDirectory(prefix="ming-ota-check-") as directory:
            root = pathlib.Path(directory)
            config = root / "config"
            cache = root / "cache"
            config.mkdir()
            cache.mkdir()
            cli = self.cli.replace(
                'readonly CONFIG_DIR="/etc/ming-update"',
                'readonly CONFIG_DIR="%s"' % git_path(config),
            ).replace(
                'readonly CACHE_DIR="/var/cache/ming-update"',
                'readonly CACHE_DIR="%s"' % git_path(cache),
            ).replace(
                'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
                'current_version() { printf "%s\\n" "%s"; }' % ("%s", source),
            )
            injected = '''
check_network() { return 0; }
api_url() { printf '%s\\n' "https://test.invalid"; }
curl() { printf '%s' "${MING_TEST_RESPONSE}"; }
'''
            cli = cli.replace('case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), git_path(script), "check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env={**os.environ, "MING_TEST_RESPONSE": response},
            )
            cached = cache / "update_info.json"
            contents = cached.read_text(encoding="utf-8") if cached.exists() else ""
        return result, contents

    def test_accepts_every_supported_2641_source_family(self):
        for source in (
            "26.3",
            "26.3.0-r1",
            "26.3.99",
            "26.4-preview.1",
            "26.4",
            "26.4.0",
            "26.4.0-r1",
            "26.4.1-rc1",
        ):
            with self.subTest(source=source):
                result = self.route(source, "26.4.1")
                self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_unsupported_or_non_advancing_2641_routes(self):
        for source, target in (
            ("26.2.99", "26.4.1"),
            ("26.3evil", "26.4.1"),
            ("26.4.1", "26.4.1"),
            ("26.4.2", "26.4.1"),
            ("26.3.2", "26.4.2"),
        ):
            with self.subTest(source=source, target=target):
                result = self.route(source, target)
                self.assertNotEqual(0, result.returncode)

    def test_check_caches_a_2641_manifest_for_eligible_sources(self):
        for source in ("26.3.0-r1", "26.4-preview.1", "26.4.0"):
            with self.subTest(source=source):
                result, cached = self.check(source)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("26.4.1", json.loads(cached)["version"])

    def test_check_rejects_an_unsupported_2641_source_before_caching(self):
        result, cached = self.check("26.2.99")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", cached)

    def test_route_guard_is_rechecked_before_cache_or_privileged_mutation(self):
        module = OTA.read_text(encoding="utf-8")
        check = module.split("check_update() {", 1)[1].split("download_update() {", 1)[0]
        download = module.split("download_update() {", 1)[1].split("install_update() {", 1)[0]
        install = module.split("install_update() {", 1)[1].split("manifest_apply_identity() {", 1)[0]
        apply = module.split("apply_update() {", 1)[1].split("auto_shutdown_update() {", 1)[0]

        self.assertIn('validate_update_route "${version}" "${new_version}"', check)
        self.assertIn('validate_update_route "$(current_version)" "${version}"', download)
        self.assertIn('validate_update_route "$(current_version)" "${version}"', install)
        self.assertIn('validate_update_route "$(current_version)" "${target_version}"', apply)


if __name__ == "__main__":
    unittest.main()
