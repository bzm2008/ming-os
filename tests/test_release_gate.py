import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BASE = ROOT / "modules" / "01_base.sh"
BUILD = ROOT / "build_onion_os.sh"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
SMOKE = ROOT / "tests" / "fixtures" / "ming-release-smoke.sh"
RESUME = ROOT / "resume_build.sh"
README = ROOT / "README.md"


class ReleaseGateContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.phone = PHONE.read_text(encoding="utf-8")

    def test_ming_shell_assets_are_installed(self):
        for name in [
            "ming-shell-common.py",
            "ming-app-drawer.py",
            "ming-launch.py",
            "ming-notifications.py",
            "ming-settings.py",
            "ming-settings-backend.py",
            "ming-files.py",
            "ming-files-model.py",
        ]:
            self.assertIn(name, self.desktop)

    def test_visible_xfce_settings_and_all_disks_are_retired(self):
        self.assertIn("cleanup_retired_ming_entries", self.desktop)
        self.assertIn("rm -f /usr/share/applications/ming-disk-hub.desktop", self.desktop)
        self.assertIn("rm -f /usr/local/bin/ming-disk-hub", self.desktop)
        self.assertIn("ming-migrate-all-disks", self.desktop)
        self.assertNotIn("xfce4-settings-manager'", self.desktop)

    def test_drawer_is_dock_only(self):
        self.assertIn(
            'DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-running-apps.dockitem;;ming-files.dockitem',
            self.desktop,
        )
        self.assertIn('Exec=/usr/local/bin/ming-running-apps menu', self.desktop)
        self.assertIn('rm -f "${desktop}/Ming 应用库.desktop"', self.desktop)
        self.assertNotIn('"ming-app-library.desktop",\n    "ming-files.desktop"', self.phone)

    def test_last_deployed_app_library_wrapper_is_drawer_only(self):
        """No late legacy application library can replace the drawer wrapper."""
        wrappers = re.findall(
            r"cat > /usr/local/bin/ming-app-library << '([^']+)'\n(.*?)\n\1",
            self.desktop,
            flags=re.DOTALL,
        )
        self.assertEqual(1, len(wrappers))
        _marker, wrapper = wrappers[-1]
        self.assertEqual(
            '#!/usr/bin/env bash\nset -euo pipefail\nexec /usr/local/bin/ming-app-drawer --toggle "$@"',
            wrapper.strip(),
        )

    def test_legacy_app_library_desktop_entry_is_not_deployed(self):
        self.assertNotIn("APPLIBAPP", self.desktop)
        self.assertEqual(
            1,
            self.desktop.count("cat > /usr/share/applications/ming-app-library.desktop"),
        )

    def test_rootfs_gate_rejects_a_legacy_app_library_wrapper(self):
        self.assertIn(
            'drawer_wrapper = require_file("usr/local/bin/ming-app-library")',
            self.build,
        )
        self.assertIn("ming-app-library must be the drawer-only compatibility wrapper", self.build)
        self.assertIn("forbidden_drawer_wrapper_markers", self.build)

    def test_dock_uses_launch_broker_proxies(self):
        self.assertIn("ming-launch-broker.desktop", self.desktop)
        self.assertIn("ming-dock-${name}.desktop", self.desktop)
        self.assertIn("--source dock", self.desktop)
        self.assertIn("StartupWMClass", self.desktop)

    def test_dock_proxies_use_real_window_classes(self):
        expected_fallbacks = {
            "ming-terminal": "Xfce4-terminal",
            "ming-settings": "uno.scallion.MingSettings",
            "ming-files": "org.mingos.Files",
            "ming-edge": "microsoft-edge",
        }
        for launcher, wm_class in expected_fallbacks.items():
            self.assertIn(
                '%s) wm_class="${wm_class:-%s}"' % (launcher, wm_class),
                self.desktop,
            )
        self.assertIn(
            "Launcher=file:///usr/share/applications/ming-app-library.desktop",
            self.desktop,
        )
        self.assertNotIn(
            '_plank_launcher "ming-app-library" "ming-app-library.desktop"',
            self.desktop,
        )

    def test_finalize_does_not_restore_retired_desktop_launchers(self):
        finalizer = FINALIZE.read_text(encoding="utf-8")
        launcher_block = finalizer.split("readonly DESKTOP_LAUNCHERS=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("ming-app-library.desktop", launcher_block)
        self.assertNotIn("ming-disk-hub.desktop", launcher_block)

    def test_control_center_executes_ming_settings(self):
        self.assertIn("exec /usr/local/bin/ming-settings", self.desktop)
        self.assertNotIn("'高级设置', 'ming-settings', '给懂电脑的人使用', 'xfce4-settings-manager'", self.desktop)

    def test_status_widget_has_notifications_audio_and_brightness(self):
        for marker in [
            "load_notification_log",
            "音量",
            "亮度",
            "免打扰",
            "清空通知",
        ]:
            self.assertIn(marker, self.phone)

    def test_build_validates_new_release_surface(self):
        for marker in [
            "ming-app-drawer",
            "ming-launch",
            "ming-files-model",
            "ming-ota-backup",
            "boot/grub/themes/ming/theme.txt",
        ]:
            self.assertIn(marker, self.build)

    def test_rootfs_recovery_gate_validates_generated_helpers_and_units(self):
        """A completed image must reject malformed recovery helpers before release."""
        for marker in [
            "def validate_generated_executable",
            'subprocess.run(["bash", "-n", str(path)]',
            '"-m", "py_compile"',
            "def validate_systemd_unit",
            "systemd-analyze verify",
            "earlyoom_config",
            "legacy Intel DDX",
        ]:
            self.assertIn(marker, self.build)

    def test_rootfs_gate_accepts_valid_dock_items_in_a_nonlegacy_order(self):
        """Papyrus may be prepended after its verified installation."""
        strict_prefix = (
            'plank_settings = require_file('
            '"home/user/.config/plank/dock1/settings", '
            '"DockItems=ming-settings.dockitem")'
        )
        self.assertNotIn(strict_prefix, self.build)
        self.assertIn(
            'plank_settings = require_file('
            '"home/user/.config/plank/dock1/settings", "DockItems=")',
            self.build,
        )
        self.assertIn("dock_item_lines", self.build)
        self.assertIn("required_dock_items", self.build)
        for item in (
            "ming-settings.dockitem",
            "ming-app-library.dockitem",
            "ming-running-apps.dockitem",
            "ming-files.dockitem",
            "ming-edge.dockitem",
            "papyrus.dockitem",
        ):
            self.assertIn(item, self.build)

    def test_static_calamares_fallback_contains_installed_desktop_gate(self):
        """The build-time Calamares fallback must be complete before Live preflight runs."""
        fallback = self.desktop.split("cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'", 1)[1]
        fallback = fallback.split("cat > /etc/calamares/modules/partition.conf", 1)[0]
        self.assertIn("cat > /etc/calamares/modules/ming-installed-desktop-gate.conf", fallback)
        self.assertIn('/usr/local/sbin/ming-installer-verify installed --receipt', fallback)
        self.assertIn("ming-installer-target-receipt@ming-installer-target-receipt", fallback)

    def test_build_inputs_survive_debian_tmp_cleanup(self):
        """Module sources live outside /tmp and the legacy path is recreated per module."""
        self.assertIn('readonly CHROOT_BUILD_DIR="/var/lib/ming-os-build"', self.build)
        self.assertIn('ln -s "${CHROOT_BUILD_DIR}" "${CHROOT_DIR}/tmp/ming-build"', self.build)
        self.assertIn('ensure_chroot_build_link', self.build)
        resume = RESUME.read_text(encoding="utf-8")
        self.assertIn('ensure_chroot_build_link', resume)

    def test_rootfs_autostart_validator_imports_configparser(self):
        """The second rootfs validator parses desktop entry files independently."""
        validator = self.build.split('validate_r4_compatibility() {', 1)[1]
        validator = validator.split("PY\n    then", 1)[0]
        self.assertIn('import configparser', validator)

    def test_resume_can_reuse_a_completed_rootfs_for_release_validation(self):
        resume = RESUME.read_text(encoding="utf-8")
        self.assertIn('MING_RESUME_SKIP_MODULES', resume)
        self.assertIn('复用现有 chroot，跳过模块重放', resume)
        self.assertLess(resume.index('MING_RESUME_SKIP_MODULES'), resume.index('mount_chroot'))

    def test_rootfs_gate_requires_every_task6_recovery_contract(self):
        """Release validation must retain every stability recovery surface."""
        for marker in [
            "xserver-xorg-video-modesetting",
            "getent group render",
            "grep -qx render",
            "usr/local/bin/ming-window-control",
            "usr/local/sbin/ming-time-sync",
            "usr/local/bin/ming-display-control",
            "etc/NetworkManager/dispatcher.d/90-ming-time-sync",
            "etc/systemd/system/ming-intel-xorg-migration.service",
            "etc/systemd/system/ming-regdom.service",
            "etc/systemd/system/ming-hardware-preload.service",
            "widget_state_path",
            "save_widget_state",
            "os.replace",
            "Gtk.Revealer",
        ]:
            self.assertIn(marker, self.build)

    def test_display_runtime_gate_accepts_schema_valid_diagnostic_status(self):
        """No X session returns display diagnostics with exit 2, not a bad image."""
        start = 'if display_status="$(chroot_exec /usr/local/bin/ming-display-control status --json)"'
        self.assertIn(start, self.build)
        gate = self.build.split(start, 1)[1].split("\n\n    if ! python3", 1)[0]
        self.assertIn('display_status_rc=0', gate)
        self.assertIn('display_status_rc=$?', gate)
        self.assertIn('"${display_status_rc}" -ne 0 && "${display_status_rc}" -ne 2', gate)
        self.assertIn('printf \'%s\\n\' "${display_status}" | python3 -c', gate)

    def test_release_smoke_exercises_backup_and_ming_files(self):
        smoke = SMOKE.read_text(encoding="utf-8")
        self.assertIn("MING_OTA_TEST_MODE=1", smoke)
        self.assertIn("ming-ota-backup restore", smoke)
        self.assertIn("ming-files --self-test", smoke)
        self.assertIn("--exercise-apps", smoke)

    def test_release_smoke_bounds_launch_broker_and_cleans_owned_process(self):
        smoke = SMOKE.read_text(encoding="utf-8")
        for marker in [
            "ensure_launch_broker",
            "broker_socket_ready",
            "smoke_broker_pid",
            "cleanup_smoke_broker",
            "trap cleanup_smoke_broker",
            "timeout --signal=TERM --kill-after=1s",
        ]:
            self.assertIn(marker, smoke)

    def test_release_identity_is_2640_and_smoke_is_a_tracked_fixture(self):
        """Release-critical checks cannot depend on ignored local scratch files."""
        self.assertTrue(SMOKE.is_file())
        self.assertIn('readonly MING_OS_VERSION="26.4.0"', self.build)
        self.assertIn('_MING_UPDATE_VERSION="26.4.0.1"', self.build)
        self.assertIn('readonly ISO_VOLUME_ID="MING_OS_2640"', self.build)
        readme = README.read_text(encoding="utf-8")
        self.assertIn("# Ming OS 26.4.0 正式版", readme)
        self.assertIn("ming-os-26.4.0-home-amd64-formal.iso", readme)
        self.assertIn("releases/tag/v26.4.0.1", readme)

    def test_release_preflight_is_before_iso_creation(self):
        marker = "ming-release-vault.py preflight --mode release"
        self.assertIn(marker, self.build)
        main_body = self.build.split("main() {", 1)[1]
        self.assertLess(main_body.index("run_release_preflight"), main_body.index("    build_iso"))
        self.assertIn("MING_RELEASE_PREFLIGHT_CONFIG", self.build)
        self.assertNotRegex(self.build, r"(?:--private-key|--password|MING_RELEASE_PASSWORD)")

    def test_readme_ota_example_is_actionable_and_declares_the_transactional_transition(self):
        readme = README.read_text(encoding="utf-8")
        for marker in (
            '"has_update": false',
            '"ready": false',
            '"update_type": "major"',
            "26.3.2",
            '"version": "26.4.0.1"',
            'from_versions: ["26.3.2", "26.3.3", "26.4.0"]',
            "no manual GRUB",
        ):
            self.assertIn(marker, readme)

    def test_installed_identity_contains_ota_restore_gate(self):
        for marker in [
            "ming.ota=1",
            "cmdline_value ming.ota_backup_uuid",
            "cmdline_value ming.ota_manifest",
            "ming-ota-restore.log",
            '"${engine}" restore',
        ]:
            self.assertIn(marker, self.base)

    def test_build_gate_requires_the_safe_graphics_persistence_runtime(self):
        for marker in (
            "/usr/local/sbin/ming-safe-graphics-persist",
            "ming-safe-graphics-persist.service",
        ):
            self.assertIn(marker, self.build)
            self.assertIn(marker, self.base)

    def test_separate_home_ota_is_preserved_without_fake_restore(self):
        for marker in [
            '[[ "${strategy}" == "separate_home" ]]',
            'UUID=${backup_uuid} /home',
            "separate /home preservation plan accepted",
        ]:
            self.assertIn(marker, self.base)

    def test_ota_backup_is_verified_before_partitioning(self):
        self.assertIn("ming-ota-preflight", self.base)
        expected = "  - shellprocess@ming-ota-preflight\n  - ming-ota-target-guard@ming-ota-target-guard\n  - partition"
        self.assertIn(expected, self.base)
        self.assertEqual(2, self.desktop.count(expected))
        self.assertIn("/run/ming-ota-preflight.ok", self.base)

    def test_resume_path_deploys_ota_target_guard(self):
        for marker in [
            "ming-ota-target-guard.py",
            "ming_ota_target_guard.py",
            "calamares/modules/ming-ota-target-guard",
            "validate_from_marker",
        ]:
            self.assertIn(marker, self.desktop)

    def test_resume_replays_apps_before_desktop(self):
        resume = RESUME.read_text(encoding="utf-8")
        modules = resume.split("local modules=(", 1)[1].split(")", 1)[0]
        self.assertIn('"02_apps.sh"', modules)
        self.assertLess(modules.index('"02_apps.sh"'), modules.index('"03_desktop.sh"'))

    def test_resume_seeds_the_current_deb_installer_before_replaying_apps(self):
        """A resumed build must not use a stale installer from an old chroot."""
        resume = RESUME.read_text(encoding="utf-8")
        self.assertIn("seed_resume_package_installer()", resume)
        main = resume.split("resume_main() {", 1)[1].split("resume_main \"$@\"", 1)[0]
        expected_order = (
            "prepare_chroot_scripts\n"
            "    seed_resume_package_installer\n"
            "    ensure_resume_runtime_packages"
        )
        self.assertIn(expected_order, main)

    def test_resume_atomically_seeds_a_matching_installer_and_common_runtime(self):
        resume = RESUME.read_text(encoding="utf-8")
        seed = resume.split("seed_resume_package_installer() {", 1)[1].split(
            "\n}\n\nensure_resume_runtime_packages", 1
        )[0]

        self.assertIn("ming-package-installer.py", seed)
        self.assertIn("ming-shell-common.py", seed)
        self.assertIn("REQUIRED_COMMON_SHA256", seed)
        self.assertIn("mktemp -d", seed)
        self.assertIn("mv -T", seed)
        main = resume.split("resume_main() {", 1)[1].split("resume_main \"$@\"", 1)[0]
        self.assertLess(
            main.index("seed_resume_package_installer"),
            main.index('"02_apps.sh"'),
        )

    def test_resume_settles_every_module_and_rejects_dpkg_audit_output(self):
        resume = RESUME.read_text(encoding="utf-8")
        module_loop = resume.split('for mod in "${modules[@]}"; do', 1)[1].split("done", 1)[0]
        self.assertIn('settle_chroot_dpkg "${mod}"', module_loop)
        self.assertIn('chroot_exec dpkg --audit', resume)
        self.assertIn('resume build has unfinished dpkg packages', resume)

    def test_resume_generates_initramfs_before_cleaning_or_unmounting_chroot(self):
        resume = RESUME.read_text(encoding="utf-8")
        main = resume.split("resume_main() {", 1)[1].split("resume_main \"$@\"", 1)[0]
        self.assertLess(main.index("generate_initramfs"), main.index("clean_chroot"))
        self.assertLess(main.index("generate_initramfs"), main.index("\n    umount_chroot\n"))

    def test_initramfs_generation_updates_resume_and_creates_fresh_images(self):
        generator = self.build.split("generate_initramfs() {", 1)[1].split(
            "# ======================== ISO", 1)[0]
        self.assertIn("/boot/initrd.img-*", generator)
        self.assertIn("update-initramfs -u -k all", generator)
        self.assertIn("update-initramfs -c -k", generator)
        self.assertIn("/lib/modules/*", generator)

    def test_tmpfs_fstab_entry_is_idempotent_and_build_validated(self):
        self.assertIn("ensure_single_tmpfs_fstab_entry()", self.base)
        helper = self.base.split("ensure_single_tmpfs_fstab_entry()", 1)[1].split(
            "optimize_system()", 1)[0]
        self.assertIn('$2 == "/tmp" && $3 == "tmpfs"', helper)
        self.assertIn("ensure_single_tmpfs_fstab_entry", self.base)
        self.assertIn("Live fstab must contain exactly one /tmp tmpfs entry", self.build)

    def test_finalizer_regenerates_late_dock_launchers_after_module_order(self):
        finalizer = FINALIZE.read_text(encoding="utf-8")
        for source in (self.build, RESUME.read_text(encoding="utf-8")):
            modules = source.split("local modules=(", 1)[1].split(")", 1)[0]
            self.assertLess(modules.index('"03_desktop.sh"'), modules.index('"06_ota_update.sh"'))
            self.assertLess(modules.index('"06_ota_update.sh"'), modules.index('"08_settings_hub.sh"'))
            self.assertLess(modules.index('"08_settings_hub.sh"'), modules.index('"07_finalize.sh"'))

        helper = "/usr/local/sbin/ming-refresh-dock-launchers"
        self.assertIn(helper, self.desktop)
        self.assertIn(helper, finalizer)
        final_main = finalizer.index("main() {")
        refresh = finalizer.index("refresh_dock_launchers", final_main)
        seed = finalizer.index("seed_skel", final_main)
        self.assertLess(refresh, seed)
        for launcher in ("ming-settings",):
            self.assertIn('"%s:%s.desktop"' % (launcher, launcher), self.desktop)


if __name__ == "__main__":
    unittest.main()
