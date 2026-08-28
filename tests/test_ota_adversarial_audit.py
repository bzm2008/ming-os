import hashlib
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


def signed_major_manifest(**overrides):
    manifest = {
        "schema": "ming.update.discovery.v1",
        "available": True,
        "delivery": "iso",
        "capability": "transactional-slot-v1",
        "has_update": True,
        "ready": True,
        "version": "26.4.1",
        "build_id": "2641-rc4-550d0b83a3a2-20260826T103412Z",
        "update_type": "major",
        "download_url": "https://ming.sca-hub.cn/download/ming-os-26.4.1.iso",
        "filename": "ming-os-26.4.1.iso",
        "checksum": "a" * 64,
        "signature": "RWQtestsignature",
        "trusted_comment": "Ming OS OTA release 26.4.1 RC4",
    }
    manifest.update(overrides)
    return manifest


class OtaAdversarialAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GIT_BASH.is_file():
            raise unittest.SkipTest("Git Bash is unavailable")
        cls.cli = generated_cli()

    def isolated_cli(self, root, *, source_version="26.4.0", source_build_id=""):
        config = root / "config"
        cache = root / "cache"
        public_key = root / "ota-release.pub"
        config.mkdir()
        cache.mkdir()
        public_key.write_text("untrusted comment: test\nRWQtest\n", encoding="utf-8")
        cli = self.cli.replace(
            'readonly CONFIG_DIR="/etc/ming-update"',
            'readonly CONFIG_DIR="%s"' % git_path(config),
        ).replace(
            'readonly CACHE_DIR="/var/cache/ming-update"',
            'readonly CACHE_DIR="%s"' % git_path(cache),
        ).replace(
            'readonly OTA_RELEASE_PUBLIC_KEY="/etc/ming-update/ota-release.minisign.pub"',
            'readonly OTA_RELEASE_PUBLIC_KEY="%s"' % git_path(public_key),
        ).replace(
            'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
            'current_version() { printf "%s\\n" "%s"; }' % ("%s", source_version),
        ).replace(
            "current_build_id() {\n    jq -r '.build_id // empty' /etc/ming-os-build.json 2>/dev/null || true\n}",
            'current_build_id() { printf "%s\\n" "%s"; }' % ("%s", source_build_id),
        )
        return cli, config, cache, public_key

    def run_cli(self, root, cli, *args, env=None):
        script = root / "ming-update"
        script.write_text(cli, encoding="utf-8", newline="\n")
        return subprocess.run(
            [str(GIT_BASH), git_path(script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            env={**os.environ, **(env or {})},
        )

    def test_trusted_comment_is_inside_the_verified_manifest_payload(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-comment-") as directory:
            root = pathlib.Path(directory)
            cli, _config, _cache, _key = self.isolated_cli(root)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(signed_major_manifest()), encoding="utf-8")
            prefix = cli.split('case "${1:-help}" in', 1)[0]
            prefix = prefix.replace(
                'if ! command -v grub-reboot >/dev/null 2>&1 || ! command -v grub-editenv >/dev/null 2>&1; then',
                'if false; then',
            )
            script = root / "verify.sh"
            script.write_text(
                prefix
                + r'''
minisign() {
    local message=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -m) message="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    [[ "$(jq -r '.trusted_comment // empty' "${message}")" == "${MING_EXPECTED_COMMENT}" ]]
}
verify_signed_ota_manifest "${MING_TEST_MANIFEST}"
''',
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                [str(GIT_BASH), git_path(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                env={
                    **os.environ,
                    "MING_TEST_MANIFEST": git_path(manifest),
                    "MING_EXPECTED_COMMENT": "Ming OS OTA release 26.4.1 RC4",
                },
            )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_trusted_comment_must_start_with_the_ming_ota_namespace(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-comment-prefix-") as directory:
            root = pathlib.Path(directory)
            cli, _config, _cache, _key = self.isolated_cli(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(signed_major_manifest(trusted_comment="Not a Ming OS OTA release")),
                encoding="utf-8",
            )
            prefix = cli.split('case "${1:-help}" in', 1)[0]
            script = root / "verify.sh"
            script.write_text(
                prefix + "\nminisign() { return 0; }\n"
                + 'verify_signed_ota_manifest "${MING_TEST_MANIFEST}"\n',
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                [str(GIT_BASH), git_path(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                env={**os.environ, "MING_TEST_MANIFEST": git_path(manifest)},
            )

        self.assertNotEqual(0, result.returncode)

    def test_api_url_rejects_untrusted_servers_and_unsafe_endpoints(self):
        cases = (
            ("http://ming.sca-hub.cn", "/api/onion-update"),
            ("https://ming.sca-hub.cn.evil.example", "/api/onion-update"),
            ("https://user:password@ming.sca-hub.cn", "/api/onion-update"),
            ("file:///tmp/fake-ota", "/api/onion-update"),
            ("https://ming.sca-hub.cn", "/api/onion-update/../../other"),
        )
        for server, endpoint in cases:
            with self.subTest(server=server, endpoint=endpoint):
                with tempfile.TemporaryDirectory(prefix="ming-ota-url-") as directory:
                    root = pathlib.Path(directory)
                    cli, config, _cache, _key = self.isolated_cli(root)
                    (config / "config.json").write_text(
                        json.dumps({
                            "update_server": server,
                            "api_endpoint": endpoint,
                            "channel": "stable",
                        }),
                        encoding="utf-8",
                    )
                    prefix = cli.split('case "${1:-help}" in', 1)[0]
                    script = root / "url.sh"
                    script.write_text(prefix + "\napi_url\n", encoding="utf-8", newline="\n")
                    result = subprocess.run(
                        [str(GIT_BASH), git_path(script)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=15,
                    )
                self.assertNotEqual(0, result.returncode, result.stdout)

    def test_api_url_rejects_an_injection_prone_current_version(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-version-") as directory:
            root = pathlib.Path(directory)
            cli, _config, _cache, _key = self.isolated_cli(
                root, source_version="26.4.0&channel=evil")
            prefix = cli.split('case "${1:-help}" in', 1)[0]
            script = root / "url.sh"
            script.write_text(prefix + "\napi_url\n", encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), git_path(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        self.assertNotEqual(0, result.returncode)

    def test_failed_check_invalidates_a_previously_actionable_cache(self):
        failure_modes = ("network", "transport", "invalid_json")
        for mode in failure_modes:
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(prefix="ming-ota-stale-") as directory:
                    root = pathlib.Path(directory)
                    cli, _config, cache, _key = self.isolated_cli(root)
                    (cache / "update_info.json").write_text(
                        json.dumps(signed_major_manifest()), encoding="utf-8")
                    (cache / "background-availability.json").write_text(
                        json.dumps({"available": True, "version": "26.4.1"}),
                        encoding="utf-8",
                    )
                    injected = r'''
maybe_migrate_update_server() { return 0; }
check_network() { [[ "${MING_FAILURE_MODE}" != network ]]; }
curl() {
    [[ "${MING_FAILURE_MODE}" != transport ]] || return 22
    if [[ "${MING_FAILURE_MODE}" == invalid_json ]]; then
        printf '%s' '<html>bad gateway</html>'
    else
        printf '%s' '{}'
    fi
}
'''
                    cli = cli.replace(
                        'case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
                    result = self.run_cli(
                        root, cli, "check", env={"MING_FAILURE_MODE": mode})

                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse((cache / "update_info.json").exists())
                    self.assertFalse((cache / "background-availability.json").exists())
                    recorded = json.loads((cache / "check-result.json").read_text(encoding="utf-8"))
                    self.assertFalse(recorded["available"])

    def run_download(self, manifest, *, wget_mode="success", existing_state=False):
        directory = tempfile.TemporaryDirectory(prefix="ming-ota-download-")
        root = pathlib.Path(directory.name)
        cli, _config, cache, _key = self.isolated_cli(root)
        payload = root / "payload.iso"
        payload.write_bytes(b"Ming OTA payload\n")
        manifest = dict(manifest)
        manifest.setdefault("checksum", hashlib.sha256(payload.read_bytes()).hexdigest())
        (cache / "update_info.json").write_text(json.dumps(manifest), encoding="utf-8")
        marker = root / "wget-called"
        if existing_state:
            (root / "config" / "state.json").write_text(
                json.dumps({"status": "downloaded", "iso_path": "/tmp/old.iso"}),
                encoding="utf-8",
            )
        injected = r'''
minisign() { return 0; }
wget() {
    touch "${MING_TEST_WGET_MARKER}"
    local output=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -O) output="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    if [[ "${MING_TEST_WGET_MODE}" == interrupted ]]; then
        printf '%s' partial > "${output}"
        return 4
    fi
    cp -- "${MING_TEST_PAYLOAD}" "${output}"
}
df() { printf 'Avail\n9999999999\n'; }
'''
        cli = cli.replace('case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
        result = self.run_cli(
            root,
            cli,
            "download",
            env={
                "MING_TEST_PAYLOAD": git_path(payload),
                "MING_TEST_WGET_MARKER": git_path(marker),
                "MING_TEST_WGET_MODE": wget_mode,
            },
        )
        return directory, root, cache, marker, result

    def test_download_revalidates_schema_and_signature_before_network_io(self):
        cases = (
            signed_major_manifest(schema=None),
            signed_major_manifest(signature=None),
        )
        for manifest in cases:
            with self.subTest(manifest=manifest):
                holder, root, _cache, marker, result = self.run_download(manifest)
                try:
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse(marker.exists(), result.stdout + result.stderr)
                    self.assertFalse((root / "config" / "state.json").exists())
                finally:
                    holder.cleanup()

    def test_interrupted_download_cannot_leave_a_downloaded_state(self):
        holder, root, cache, marker, result = self.run_download(
            signed_major_manifest(), wget_mode="interrupted", existing_state=True)
        try:
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(marker.exists())
            state_path = root / "config" / "state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertNotEqual("downloaded", state.get("status"))
            self.assertFalse((cache / "ming-os-26.4.1.iso").exists())
        finally:
            holder.cleanup()

    def test_size_or_checksum_failure_cannot_reuse_an_old_downloaded_state(self):
        cases = (
            signed_major_manifest(size=999),
            signed_major_manifest(checksum="0" * 64),
        )
        for manifest in cases:
            with self.subTest(manifest=manifest):
                holder, root, cache, _marker, result = self.run_download(
                    manifest, existing_state=True)
                try:
                    self.assertNotEqual(0, result.returncode)
                    state_path = root / "config" / "state.json"
                    if state_path.exists():
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                        self.assertNotEqual("downloaded", state.get("status"))
                    self.assertFalse((cache / "ming-os-26.4.1.iso").exists())
                finally:
                    holder.cleanup()

    def test_download_rejects_version_and_rc_build_replay_before_network_io(self):
        holder, _root, _cache, marker, result = self.run_download(
            signed_major_manifest(
                build_id="2641-rc3-054c2a2355ed-20260824T095743Z"),
        )
        holder.cleanup()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(marker.exists())

    def test_apply_checked_rejects_a_manifest_changed_after_check(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-apply-race-") as directory:
            root = pathlib.Path(directory)
            cli, _config, cache, _key = self.isolated_cli(root)
            manifest = signed_major_manifest(
                update_type="patch",
                apt_packages=["ming-desktop"],
            )
            manifest_path = cache / "update_info.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            digest_path = cache / "checked-manifest.sha256"
            digest_path.write_text(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n",
                encoding="ascii",
            )
            manifest["apt_packages"] = ["ming-settings"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            marker = root / "apply-called"
            injected = r'''
verify_signed_ota_manifest() { return 0; }
apply_manifest_apt_update() { touch "${MING_TEST_APPLY_MARKER}"; return 0; }
'''
            cli = cli.replace('${EUID:-$(id -u)}', '0')
            cli = cli.replace(
                'case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
            result = self.run_cli(
                root,
                cli,
                "apply",
                "--checked",
                env={"MING_TEST_APPLY_MARKER": git_path(marker)},
            )

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(marker.exists())
        self.assertIn("清单已变化", result.stderr + result.stdout)

    def test_rollback_never_reboots_when_grub_readback_fails(self):
        module = OTA.read_text(encoding="utf-8")
        health = module.split(
            "cat > /usr/local/sbin/ming-ota-ab-health << 'ABHEALTH'\n", 1
        )[1].split("\nABHEALTH\n", 1)[0]
        rollback = health.split("rollback_ab_boot() {", 1)[1].split(
            'status="$(ming-ota-ab', 1)[0]
        self.assertIn("if ! restore_previous_grub_entries", rollback)
        self.assertLess(rollback.index("restore_previous_grub_entries"), rollback.index("systemctl reboot"))
        self.assertIn("return 1", rollback.split("restore_previous_grub_entries", 1)[1].split("sync", 1)[0])

    def test_non_ab_grub_readback_failure_clears_one_shot_entry(self):
        module = OTA.read_text(encoding="utf-8")
        cli = generated_cli()
        configure = cli.split("configure_ota_next_boot() {", 1)[1].split(
            "manifest_apply_identity() {", 1
        )[0]
        self.assertIn("grub-editenv unset next_entry", configure)
        self.assertIn("next_entry", configure)

    def test_non_ab_grub_readback_failure_executes_next_entry_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="ming-ota-grub-cleanup-") as directory:
            root = pathlib.Path(directory)
            cli, _config, _cache, _key = self.isolated_cli(root)
            prefix = cli.split('case "${1:-help}" in', 1)[0]
            marker = root / "cleanup-called"
            script = root / "grub-cleanup.sh"
            script.write_text(
                prefix + r'''
grub-reboot() { return 0; }
grub-editenv() {
    if [[ "$1" == list ]]; then
        printf '%s\n' 'saved_entry=Ming OS'
        return 0
    fi
    if [[ "$1" == unset && "$2" == next_entry ]]; then
        : > "${MING_TEST_CLEANUP_MARKER}"
        return 0
    fi
    return 1
}
configure_ota_next_boot 'Ming OS test OTA'
''',
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                [str(GIT_BASH), git_path(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                env={**os.environ, "MING_TEST_CLEANUP_MARKER": git_path(marker)},
            )
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(marker.exists(), result.stdout + result.stderr)

    def test_ab_stage_binds_build_id_to_transaction(self):
        stage = (ROOT / "assets" / "ming-ota-ab-stage.sh").read_text(encoding="utf-8")
        self.assertIn("--build-id", stage)
        self.assertIn('BUILD_ID=""', stage)
        self.assertIn('begin \\\n    --target "${target}" --version "${VERSION}" --build-id "${BUILD_ID}"', stage)


if __name__ == "__main__":
    unittest.main()
