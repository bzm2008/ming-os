import json
import os
import pathlib
import subprocess
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "assets" / "ming-settings.py"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
OTA = ROOT / "modules" / "06_ota_update.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"


def method_block(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def load_status_probe(phone_source, subprocess_module):
    namespace = {
        "json": json,
        "log": lambda _message: None,
        "subprocess": subprocess_module,
    }
    block = method_block(
        phone_source,
        "    def background_update_status",
        "    def background_update_available",
    )
    exec("class Probe:\n" + block, namespace)
    return namespace["Probe"]()


class FakeStatusSubprocess:
    SubprocessError = subprocess.SubprocessError

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.result


class UpdateSingleFlowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.phone = PHONE.read_text(encoding="utf-8")
        cls.ota = OTA.read_text(encoding="utf-8")
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.finalizer = FINALIZE.read_text(encoding="utf-8")

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

    def test_settings_explains_major_ota_data_preservation_before_enabling_update(self):
        update = method_block(self.settings, "    def apply_update_status(self, status):", "    def on_update_action(self, _btn):")

        self.assertIn('status.get("home_preservation")', update)
        self.assertIn('update_type == "major"', update)
        self.assertIn("preservation_ready", update)
        self.assertIn("升级准备", update)
        self.assertIn("连接备份盘后重新检查", update)

    def test_settings_one_click_apply_requests_restart_after_a_successful_stage(self):
        apply = method_block(self.settings, "    def on_update_apply(self):", "    # ---- 5. 显示与无障碍")

        self.assertIn('"--restart-after-stage"', apply)
        self.assertIn("完成后会自动重启", apply)

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

    def test_cli_status_exposes_major_ota_preservation_preflight(self):
        status = method_block(self.ota, "show_status_json() {", "show_status() {")

        self.assertIn("home_preservation_status_json", self.ota)
        self.assertIn("home_preservation", status)
        self.assertIn("preservation_ready", status)
        self.assertIn("backup_required", self.ota)

    def test_dual_boot_major_ota_is_visible_as_a_status_block(self):
        status = method_block(self.ota, "show_status_json() {", "show_status() {")
        self.assertIn("dual_boot_major_ota_blocked", self.ota)
        self.assertIn('action="blocked"', status)
        self.assertIn("disabled_dual_boot", status)
        self.assertIn("保留双系统模式，大版本 A/B OTA 已禁用", self.ota)

    def test_installed_install_mode_policy_is_readable_by_unprivileged_ota_status(self):
        policy = method_block(self.ota, "dual_boot_major_ota_blocked() {", "ensure_dirs() {")
        self.assertIn('[[ -r "${INSTALL_MODE_FILE}"', policy)
        base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        self.assertIn('install -m 0644 "${install_mode_state}"', base)

    def test_cli_help_advertises_the_one_click_restart_command_and_legacy_alias(self):
        help_block = method_block(self.ota, "show_help() {", "validate_staging_record_local() {")

        self.assertIn("auto-restart", help_block)
        self.assertIn("兼容别名", help_block)
        self.assertIn("auto-shutdown", help_block)

    def test_power_menu_only_offers_update_restart_after_a_background_confirmation(self):
        self.assertIn("def background_update_available", self.phone)
        self.assertIn('status.get("background_available")', self.phone)
        power = method_block(self.phone, "    def show_confirmed_update_power_menu", "    def refresh(self):")
        entry = method_block(self.phone, "    def open_power_menu(self, _button):", "    def refresh(self):")
        self.assertIn("background_update_available()", entry)
        self.assertIn("更新并重启", power)
        self.assertIn('["pkexec", "ming-update", "auto-restart"]', self.phone)
        self.assertIn("MING_UPDATE_BACKGROUND_CHECK", self.ota)
        self.assertIn("BACKGROUND_AVAILABILITY_FILE", self.ota)

    def test_power_menu_uses_update_restart_for_a_fully_automatic_major_upgrade(self):
        power = method_block(self.phone, "    def show_confirmed_update_power_menu", "    def refresh(self):")

        self.assertIn("更新并重启", power)
        self.assertIn('["pkexec", "ming-update", "auto-restart"]', self.phone)
        self.assertNotIn("auto-shutdown", power)

    def test_power_menu_keeps_update_progress_and_failure_reason_visible(self):
        update = method_block(
            self.phone,
            "    def open_update_and_restart_dialog",
            "    def open_update_and_shutdown_dialog",
        )

        self.assertIn("start_update_restart_progress", update)
        self.assertIn("stdout=subprocess.PIPE", self.phone)
        self.assertIn("stderr=subprocess.STDOUT", self.phone)
        self.assertIn("Gtk.Spinner", self.phone)
        self.assertIn("更新未完成", self.phone)
        self.assertNotIn("stdout=subprocess.DEVNULL", update)
        self.assertNotIn("stderr=subprocess.DEVNULL", update)

    def test_power_menu_blocks_major_ota_when_user_data_preflight_is_not_ready(self):
        update = method_block(
            self.phone,
            "    def open_update_and_restart_dialog",
            "    def open_update_and_shutdown_dialog",
        )

        self.assertIn('home_preservation.get("ready")', update)
        self.assertIn("前往系统更新", update)
        self.assertIn('self.open_command(["ming-control-center", "--page", "update"])', update)

    def test_power_menu_fails_closed_when_it_cannot_refresh_update_status(self):
        update = method_block(
            self.phone,
            "    def open_update_and_restart_dialog",
            "    def open_update_and_shutdown_dialog",
        )

        self.assertIn("if not isinstance(status, dict)", update)
        self.assertIn("无法确认更新状态", update)
        self.assertIn("前往系统更新", update)

    def test_power_menu_treats_a_nonzero_status_command_as_unavailable(self):
        status = method_block(
            self.phone,
            "    def background_update_status",
            "    def background_update_available",
        )

        self.assertIn("if result.returncode != 0", status)
        self.assertIn("return None", status)

    def test_background_update_status_returns_none_for_untrusted_status_output(self):
        cases = [
            FakeStatusSubprocess(types.SimpleNamespace(returncode=1, stdout='{\"background_available\": true}')),
            FakeStatusSubprocess(types.SimpleNamespace(returncode=0, stdout="{not json")),
            FakeStatusSubprocess(exc=OSError("missing ming-update")),
        ]

        for fake_subprocess in cases:
            with self.subTest(fake_subprocess=fake_subprocess):
                probe = load_status_probe(self.phone, fake_subprocess)
                self.assertIsNone(probe.background_update_status())
                self.assertEqual(["ming-update", "status", "--json"], fake_subprocess.calls[0][0])

    def test_background_update_status_returns_successful_dict_unchanged(self):
        fake_subprocess = FakeStatusSubprocess(
            types.SimpleNamespace(
                returncode=0,
                stdout='{\"background_available\": true, \"update_type\": \"minor\"}',
            )
        )
        probe = load_status_probe(self.phone, fake_subprocess)

        self.assertEqual(
            {"background_available": True, "update_type": "minor"},
            probe.background_update_status(),
        )

    def test_power_menu_distinguishes_no_update_from_a_staged_reboot(self):
        automatic = method_block(self.ota, "auto_shutdown_update() {", 'case "${1:-help}" in')

        self.assertIn("MING_UPDATE_RESULT=no_update", automatic)
        self.assertIn("MING_UPDATE_RESULT=staged", automatic)
        self.assertIn('state["result"]', self.phone)
        self.assertIn("当前已是最新版本，不会重启。", self.phone)

    def test_settings_keeps_the_last_unbracketed_authorization_error(self):
        update = method_block(self.settings, "    def on_update_check(self):", "    def on_update_apply(self):")

        self.assertIn("self.update_last_output", update)
        self.assertIn("authorization", update.lower())

    def test_default_power_button_shows_ming_menu_before_session_action(self):
        entry = method_block(self.phone, "    def open_power_menu(self, _button):", "    def refresh(self):")

        self.assertIn("show_basic_power_menu", entry)
        self.assertNotIn('["xfce4-session-logout"]', entry)
        self.assertNotIn('["gnome-session-quit", "--logout"]', entry)
        self.assertNotIn('["mate-session-save", "--logout-dialog"]', entry)

    def test_legacy_update_launcher_redirects_to_the_settings_page(self):
        self.assertEqual(1, self.ota.count("cat > /usr/local/bin/ming-update-gui"))
        redirect = method_block(
            self.ota,
            "cat > /usr/local/bin/ming-update-gui << 'OTAGUIREDIRECT'",
            "    chmod +x /usr/local/bin/ming-update-gui",
        )
        self.assertIn('exec /usr/local/bin/ming-control-center --page update "$@"', redirect)
        self.assertNotIn("zenity", redirect)
        self.assertNotIn("ming-update.desktop", redirect)

    def test_standalone_update_launcher_is_removed_from_new_and_existing_users(self):
        self.assertNotIn("cat > /usr/share/applications/ming-update.desktop", self.ota)
        self.assertNotIn("ming-update.dockitem", self.desktop)
        self.assertNotIn("ming-update.desktop", self.finalizer)
        for retired in (
            "rm -f /usr/share/applications/ming-update.desktop",
            'rm -f "/home/${MING_USER}/Desktop/ming-update.desktop"',
            'rm -f /home/*/Desktop/ming-update.desktop',
            'rm -f /home/*/.config/plank/dock1/launchers/ming-update.dockitem',
        ):
            self.assertIn(retired, self.ota)

    def test_control_center_final_writer_is_ming_settings_wrapper(self):
        main = self.desktop.split("main() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertLess(main.index("install_ming_shell_components"), main.index("install_ming_settings"))

        wrapper_writer = method_block(
            self.desktop,
            "install_ming_settings() {",
            "cleanup_retired_ming_entries() {",
        )
        legacy_writer = method_block(
            self.desktop,
            "install_ming_shell_components() {",
            "ensure_wps_office() {",
        )
        self.assertIn("cat > /usr/local/bin/ming-control-center", wrapper_writer)
        self.assertIn("MINGCONTROLWRAPPER", wrapper_writer)
        self.assertIn('exec /usr/local/bin/ming-settings "$@"', wrapper_writer)
        self.assertNotIn("TASKS = [", wrapper_writer)
        self.assertIn("TASKS = [", legacy_writer)
        self.assertIn("('检查系统更新', 'ming-update'", legacy_writer)

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

    def test_apply_restart_after_stage_is_explicit_and_runs_only_after_successful_stage(self):
        apply = method_block(self.ota, "apply_update() {", "auto_shutdown_update() {")
        automatic = method_block(self.ota, "auto_shutdown_update() {", 'case "${1:-help}" in')

        self.assertIn("--restart-after-stage)", apply)
        self.assertIn("restart_after_stage=false", apply)
        self.assertIn("schedule_update_restart", apply)
        self.assertIn("apply_update --checked --restart-after-stage", automatic)
        self.assertIn("systemctl reboot", self.ota)

    def test_major_stage_sets_and_verifies_a_one_shot_grub_entry_before_restart(self):
        install = method_block(
            self.ota,
            "install_update() {",
            "manifest_apply_identity() {",
        )
        self.assertIn("configure_ota_next_boot() {", self.ota)
        helper = method_block(
            self.ota,
            "configure_ota_next_boot() {",
            "manifest_apply_identity() {",
        )

        self.assertIn('configure_ota_next_boot "Ming OS ${version} OTA Installer"', install)
        self.assertIn("grub-reboot", helper)
        self.assertIn("grub-editenv", helper)
        self.assertIn("next_entry", helper)
        self.assertIn("return 1", helper)

    def test_online_ota_requires_a_distinct_signed_release_manifest(self):
        check = method_block(self.ota, "check_update() {", "download_update() {")
        self.assertIn("verify_signed_ota_manifest() {", self.ota)
        verifier = method_block(
            self.ota,
            "verify_signed_ota_manifest() {",
            "download_update() {",
        )

        self.assertIn("OTA_RELEASE_PUBLIC_KEY", verifier)
        self.assertIn("minisign", verifier)
        self.assertIn("signature", verifier)
        self.assertIn("verify_signed_ota_manifest", check)

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

    def test_generated_cli_blocks_a_major_update_until_user_data_can_be_preserved(self):
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")
        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.ota.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        with tempfile.TemporaryDirectory(prefix="ming-update-major-preflight-") as tempdir:
            root = pathlib.Path(tempdir)
            root_posix = git_path(root)
            cli = cli.replace('readonly CONFIG_DIR="/etc/ming-update"',
                              'readonly CONFIG_DIR="%s/config"' % root_posix)
            cli = cli.replace('readonly CACHE_DIR="/var/cache/ming-update"',
                              'readonly CACHE_DIR="%s/cache"' % root_posix)
            cli = cli.replace(
                'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
                'current_version() { printf "%s\\n" "26.4.0"; }')
            injected = r'''
home_is_independent_device() { return 1; }
ota_backup_destination() { return 1; }
'''
            cli = cli.replace('case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            (root / "config").mkdir()
            (cache / "update_info.json").write_text(json.dumps({
                "has_update": True, "ready": True, "version": "26.4.1",
                "release_notes": "重要更新", "update_type": "major",
            }), encoding="utf-8")
            result = subprocess.run(
                [str(git_bash), git_path(script), "status", "--json"],
                capture_output=True, timeout=20,
                env={**os.environ, "HOME": root_posix},
            )
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        status = json.loads(result.stdout.decode("utf-8", errors="replace"))
        self.assertEqual("major", status["update_type"])
        self.assertFalse(status["preservation_ready"])
        self.assertEqual("backup_required", status["home_preservation"]["strategy"])
        self.assertIn("备份盘", status["home_preservation"]["message"])

    def test_generated_auto_restart_reports_no_update_without_claiming_a_reboot(self):
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")
        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.ota.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        with tempfile.TemporaryDirectory(prefix="ming-update-no-update-") as tempdir:
            root = pathlib.Path(tempdir)
            root_posix = git_path(root)
            cli = cli.replace('readonly CONFIG_DIR="/etc/ming-update"',
                              'readonly CONFIG_DIR="%s/config"' % root_posix)
            cli = cli.replace('readonly CACHE_DIR="/var/cache/ming-update"',
                              'readonly CACHE_DIR="%s/cache"' % root_posix)
            injected = r'''
check_update() {
    rm -f -- "${CACHE_DIR}/update_info.json"
    return 0
}
'''
            cli = cli.replace('case "${1:-help}" in', injected + '\ncase "${1:-help}" in')
            script = root / "ming-update"
            script.write_text(cli, encoding="utf-8")
            (root / "cache").mkdir()
            (root / "config").mkdir()
            result = subprocess.run(
                [str(git_bash), git_path(script), "auto-restart"],
                capture_output=True, timeout=20,
                env={**os.environ, "HOME": root_posix},
            )
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        output = result.stdout.decode("utf-8", errors="replace")
        self.assertIn("MING_UPDATE_RESULT=no_update", output)
        self.assertIn("无需更新", output)

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
            base_cli = base_cli.replace(
                'current_version() {\n    cat /etc/ming-version 2>/dev/null || echo "unknown"\n}',
                'current_version() { printf "%s\\n" "26.3.2"; }',
            )

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
verify_signed_ota_manifest() {
    return 0
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
