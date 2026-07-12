import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "assets" / "ming-settings.py"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
OTA = ROOT / "modules" / "06_ota_update.sh"


def method_block(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


class UpdateSingleFlowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.phone = PHONE.read_text(encoding="utf-8")
        cls.ota = OTA.read_text(encoding="utf-8")

    def test_settings_starts_with_one_check_action_and_promotes_it_after_detection(self):
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")

        self.assertIn('Gtk.Button(label="检查更新")', update)
        self.assertIn("self.update_action_button", update)
        self.assertIn('set_label("立即更新")', update)
        self.assertIn('["ming-update", "status", "--json"]', update)
        self.assertIn('"pkexec", "ming-update", "apply"', update)
        for retired_label in ("应用小修复", "大版本升级", "更新并关机"):
            self.assertNotIn(retired_label, update)

    def test_settings_binds_the_shown_update_to_the_privileged_apply_request(self):
        """A root-side cache must not silently replace the version shown in Settings."""
        status = method_block(self.settings, "    def apply_update_status(self, status):", "    def on_update_action(self, _btn):")
        apply = method_block(self.settings, "    def on_update_apply(self):", "    # ---- 5. 显示与无障碍")

        self.assertIn('status.get("manifest_path")', status)
        self.assertIn('status.get("manifest_sha256")', status)
        self.assertIn('"--manifest", self.update_manifest_path', apply)
        self.assertIn('"--sha256", self.update_manifest_sha256', apply)

    def test_cli_exposes_machine_readable_status_and_a_single_type_aware_apply(self):
        self.assertIn("show_status_json()", self.ota)
        self.assertIn("apply_update()", self.ota)
        self.assertIn('status) show_status "${2:-}"', self.ota)
        self.assertIn("apply_update \"$@\"", self.ota)
        apply = method_block(self.ota, "apply_update() {", "auto_shutdown_update() {")
        self.assertIn("patch|minor", apply)
        self.assertIn("apply_manifest_apt_update", apply)
        self.assertNotIn("patch_update", apply)
        self.assertIn("download_update", apply)
        self.assertIn("major_install_with_home_backup", apply)

    def test_power_menu_only_offers_update_shutdown_after_a_background_confirmation(self):
        self.assertIn("def background_update_available", self.phone)
        self.assertIn('status.get("background_available")', self.phone)
        power = method_block(self.phone, "    def show_confirmed_update_power_menu", "    def refresh(self):")
        entry = method_block(self.phone, "    def open_power_menu(self, _button):", "    def refresh(self):")
        self.assertIn("background_update_available()", entry)
        self.assertIn("更新并关机", power)
        self.assertIn('["pkexec", "ming-update", "auto-shutdown"]', self.phone)
        self.assertIn("MING_UPDATE_BACKGROUND_CHECK", self.ota)
        self.assertIn("BACKGROUND_AVAILABILITY_FILE", self.ota)

    def test_legacy_update_launcher_redirects_to_the_settings_page(self):
        self.assertIn('exec /usr/local/bin/ming-control-center --page update "$@"', self.ota)
        desktop = self.ota[self.ota.index("cat > /usr/share/applications/ming-update.desktop") :]
        self.assertIn("NoDisplay=true", desktop)

    def test_boot_check_reads_the_same_root_cache_used_by_the_cli(self):
        boot = self.ota[self.ota.index("cat > /usr/local/bin/ming-boot-update-check") :]
        self.assertIn('manifest="/var/cache/ming-update/update_info.json"', boot)

    def test_newer_manual_no_update_hides_an_older_background_result(self):
        self.assertIn("record_check_result()", self.ota)
        self.assertIn("check-result.json", self.ota)
        status = method_block(self.ota, "show_status_json() {", "show_status() {")
        self.assertIn("manual_available", status)
        self.assertIn("manual_checked_at_epoch", status)
        self.assertIn("background_checked_at_epoch", status)

    def test_privileged_apply_validates_the_explicit_settings_manifest(self):
        apply = method_block(self.ota, "apply_update() {", "auto_shutdown_update() {")
        self.assertIn("--manifest)", apply)
        self.assertIn("--sha256)", apply)
        self.assertIn("stage_selected_manifest", apply)
        self.assertIn("check_update", apply)
        self.assertIn("clear_applied_update_cache", apply)

    def test_status_exposes_a_fingerprint_only_for_an_actionable_manifest(self):
        status = method_block(self.ota, "show_status_json() {", "show_status() {")
        self.assertIn("manifest_path", status)
        self.assertIn("manifest_sha256", status)
        self.assertIn("sha256sum", status)

    def test_selected_manifest_acceptance_is_path_and_symlink_restricted(self):
        helper = method_block(self.ota, "selected_manifest_path_is_safe() {", "stage_selected_manifest() {")
        self.assertIn('"${path}" == /*', helper)
        self.assertIn('! -L "${path}"', helper)
        self.assertIn('readlink -f', helper)
        self.assertIn('^/home/[^/]+/\\.cache/ming-update/update_info\\.json$', helper)
        self.assertIn('"${CACHE_DIR}/update_info.json"', helper)

    def test_generated_cli_reports_a_clean_json_status_for_a_background_update(self):
        """The real generated CLI must be consumable by UI code without log parsing."""
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")
        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.ota.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        with tempfile.TemporaryDirectory(prefix="ming-update-status-") as tempdir:
            root = pathlib.Path(tempdir)
            root_posix = git_path(root)
            cli = cli.replace('readonly CONFIG_DIR="/etc/ming-update"',
                              'readonly CONFIG_DIR="%s/config"' % root_posix)
            cli = cli.replace('readonly CACHE_DIR="/var/cache/ming-update"',
                              'readonly CACHE_DIR="%s/cache"' % root_posix)
            cli = cli.replace(
                'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
                'current_version() { printf "%s\\n" "26.3.2"; }')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            (root / "config").mkdir()
            (cache / "update_info.json").write_text(json.dumps({
                "has_update": True, "ready": True, "version": "26.3.3",
                "release_notes": "修复桌面和声音", "update_type": "patch",
            }), encoding="utf-8")
            (cache / "background-availability.json").write_text(json.dumps({
                "available": True, "version": "26.3.3", "update_type": "patch",
            }), encoding="utf-8")
            result = subprocess.run(
                [str(git_bash), git_path(script), "status", "--json"],
                capture_output=True, timeout=20,
                env={**os.environ, "HOME": root_posix},
            )
        output = result.stdout.decode("utf-8", errors="replace")
        error = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(0, result.returncode, error)
        status = json.loads(output)
        self.assertEqual("26.3.2", status["current_version"])
        self.assertEqual("26.3.3", status["new_version"])
        self.assertEqual("apply", status["action"])
        self.assertTrue(status["manifest_path"].replace("\\", "/").endswith("/cache/update_info.json"))
        self.assertRegex(status["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(status["background_available"])

    def test_generated_cli_prefers_a_newer_manual_no_update_over_root_cache(self):
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")
        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.ota.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        with tempfile.TemporaryDirectory(prefix="ming-update-manual-") as tempdir:
            root = pathlib.Path(tempdir)
            root_posix = git_path(root)
            root_cache = root / "root-cache"
            root_config = root / "root-config"
            home = root / "user"
            root_cache.mkdir(); root_config.mkdir(); home.mkdir()
            cli = cli.replace('readonly CONFIG_DIR="/etc/ming-update"',
                              'readonly CONFIG_DIR="%s"' % git_path(root_config))
            cli = cli.replace('readonly CACHE_DIR="/var/cache/ming-update"',
                              'readonly CACHE_DIR="%s"' % git_path(root_cache))
            cli = cli.replace(
                'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
                'current_version() { printf "%s\\n" "26.3.2"; }')
            original_cache_dir = '''cache_dir() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\\n' "${CACHE_DIR}"
    else
        printf '%s\\n' "${USER_CACHE_DIR}"
    fi
}'''
            cli = cli.replace(original_cache_dir, '''cache_dir() {
    printf '%s\\n' "${USER_CACHE_DIR}"
}''')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8")
            (root_cache / "update_info.json").write_text(json.dumps({
                "has_update": True, "ready": True, "version": "26.3.3",
                "release_notes": "旧的后台结果", "update_type": "patch",
            }), encoding="utf-8")
            (root_cache / "background-availability.json").write_text(json.dumps({
                "available": True, "version": "26.3.3", "checked_at_epoch": 100,
            }), encoding="utf-8")
            user_cache = home / ".cache" / "ming-update"
            user_cache.mkdir(parents=True)
            (user_cache / "check-result.json").write_text(json.dumps({
                "available": False, "ready": False, "checked_at_epoch": 200,
            }), encoding="utf-8")
            result = subprocess.run(
                [str(git_bash), git_path(script), "status", "--json"],
                capture_output=True, timeout=20,
                env={**os.environ, "HOME": git_path(home)},
            )
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        status = json.loads(result.stdout.decode("utf-8", errors="replace"))
        self.assertFalse(status["available"])
        self.assertEqual("check", status["action"])
        self.assertFalse(status["background_available"])

    def test_generated_apply_uses_the_displayed_manifest_and_clears_pending_caches(self):
        """A completed apply must not leave a user-visible stale update behind."""
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")
        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.ota.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        with tempfile.TemporaryDirectory(prefix="ming-update-apply-") as tempdir:
            root = pathlib.Path(tempdir)
            root_cache = root / "root-cache"
            root_config = root / "root-config"
            home = root / "user"
            root_cache.mkdir(); root_config.mkdir(); home.mkdir()
            user_cache = home / ".cache" / "ming-update"
            user_cache.mkdir(parents=True)
            root_manifest = root_cache / "update_info.json"
            selected_manifest = user_cache / "update_info.json"
            authoritative_manifest = root / "authoritative.json"
            applied_manifest = root / "applied.json"

            stale = {
                "has_update": True, "ready": True, "version": "26.3.3",
                "update_type": "patch", "apt_packages": ["ming-old"],
            }
            shown = {
                "has_update": True, "ready": True, "version": "26.3.4",
                "update_type": "patch", "apt_packages": ["ming-desktop"],
            }
            root_manifest.write_text(json.dumps(stale), encoding="utf-8")
            selected_manifest.write_text(json.dumps(shown), encoding="utf-8")
            authoritative_manifest.write_text(json.dumps(shown), encoding="utf-8")
            (root_cache / "background-availability.json").write_text(
                json.dumps({"available": True, "version": "26.3.3", "checked_at_epoch": 100}),
                encoding="utf-8")

            base_cli = cli.replace('readonly CONFIG_DIR="/etc/ming-update"',
                                   'readonly CONFIG_DIR="%s"' % git_path(root_config))
            base_cli = base_cli.replace('readonly CACHE_DIR="/var/cache/ming-update"',
                                        'readonly CACHE_DIR="%s"' % git_path(root_cache))

            # First exercise the same unprivileged status path used by
            # Settings.  The user's just-checked v2 must win over both the
            # stale root manifest and its stale background availability.
            original_cache_dir = '''cache_dir() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\\n' "${CACHE_DIR}"
    else
        printf '%s\\n' "${USER_CACHE_DIR}"
    fi
}'''
            status_cli = base_cli.replace(original_cache_dir, '''cache_dir() {
    printf '%s\\n' "${USER_CACHE_DIR}"
}''')
            status_injected = r'''
selected_manifest_path_is_safe() {
    local path="$1"
    [[ ( "${path}" == */.cache/ming-update/update_info.json || \
          "${path}" == "${CACHE_DIR}/update_info.json" ) && -f "${path}" && ! -L "${path}" ]]
}
'''
            status_cli = status_cli.replace(
                'case "${1:-help}" in', status_injected + '\ncase "${1:-help}" in')
            status_script = root / "ming-update-status"
            status_script.write_text(status_cli, encoding="utf-8")
            status_result = subprocess.run(
                [str(git_bash), git_path(status_script), "status", "--json"],
                capture_output=True, timeout=20,
                env={**os.environ, "HOME": git_path(home)},
            )
            self.assertEqual(0, status_result.returncode,
                             status_result.stderr.decode("utf-8", errors="replace"))
            status = json.loads(status_result.stdout.decode("utf-8", errors="replace"))
            self.assertEqual("26.3.4", status["new_version"])
            self.assertEqual("apply", status["action"])
            self.assertFalse(status["background_available"])
            self.assertRegex(status["manifest_sha256"], r"^[0-9a-f]{64}$")

            cli = base_cli
            # Git Bash is not root; make the isolated generated script exercise
            # root-only control flow without altering the production source.
            cli = cli.replace('${EUID:-$(id -u)}', '0')
            injected = r'''
selected_manifest_path_is_safe() {
    local path="$1"
    [[ ( "${path}" == */.cache/ming-update/update_info.json || \
          "${path}" == "${CACHE_DIR}/update_info.json" ) && -f "${path}" && ! -L "${path}" ]]
}
check_update() {
    cp -- "${MING_TEST_AUTHORITATIVE_MANIFEST}" "${CACHE_DIR}/update_info.json"
}
apply_manifest_apt_update() {
    cp -- "$1" "${MING_TEST_APPLIED_MANIFEST}"
}
'''
            cli = cli.replace('case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8")
            result = subprocess.run(
                [str(git_bash), git_path(script), "apply", "--manifest",
                 status["manifest_path"], "--sha256", status["manifest_sha256"]],
                capture_output=True, timeout=20,
                env={
                    **os.environ,
                    "HOME": git_path(home),
                    "MING_TEST_AUTHORITATIVE_MANIFEST": git_path(authoritative_manifest),
                    "MING_TEST_APPLIED_MANIFEST": git_path(applied_manifest),
                },
            )
            self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
            self.assertEqual("26.3.4", json.loads(applied_manifest.read_text(encoding="utf-8"))["version"])
            self.assertFalse(root_manifest.exists())
            self.assertFalse(
                selected_manifest.exists(),
                result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace"),
            )
            self.assertFalse((root_cache / "background-availability.json").exists())


if __name__ == "__main__":
    unittest.main()
