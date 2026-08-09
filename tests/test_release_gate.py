import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BASE = ROOT / "modules" / "01_base.sh"
BUILD = ROOT / "build_onion_os.sh"
GITIGNORE = ROOT / ".gitignore"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
SMOKE = ROOT / "scratch" / "ming-release-smoke.sh"
RESUME = ROOT / "resume_build.sh"
SETTINGS_HUB = ROOT / "modules" / "08_settings_hub.sh"
WALLPAPER_2640 = ROOT / "assets" / "wallpaper-ming-2640-abstract.png"


class ReleaseGateContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.gitignore = GITIGNORE.read_text(encoding="utf-8")
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
        self.assertIn('DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-files.dockitem', self.desktop)
        self.assertIn('rm -f "${desktop}/Ming 应用库.desktop"', self.desktop)
        self.assertNotIn('"ming-app-library.desktop",\n    "ming-files.desktop"', self.phone)

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
            "ming-firefox": "Firefox-esr",
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
        self.assertNotIn("ming-edge.desktop", launcher_block)
        self.assertNotIn("garlic-claw.desktop", launcher_block)
        self.assertNotIn("ming-update.desktop", launcher_block)
        self.assertIn("papyrus.desktop", launcher_block)

    def test_update_is_only_exposed_inside_settings(self):
        self.assertNotIn("ming-update.dockitem", self.desktop)
        self.assertNotIn("('ming-update.desktop'", self.desktop)
        self.assertNotIn("favorites=ming-control-center.desktop,ming-files.desktop,ming-firefox.desktop,spark-store.desktop,papyrus.desktop,ming-update.desktop", self.desktop)
        self.assertIn("def build_update(self):", (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8"))
        self.assertIn(
            'require_file("usr/local/bin/ming-update-gui", "exec /usr/local/bin/ming-control-center --page update")',
            self.build,
        )

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

    def test_live_installer_session_warns_that_live_data_is_temporary(self):
        for marker in (
            "Live 模式",
            "尚未安装",
            "不会保留任何数据",
            "继续安装 Ming OS",
        ):
            self.assertIn(marker, self.desktop)

    def test_build_validates_new_release_surface(self):
        for marker in [
            "ming-app-drawer",
            "ming-launch",
            "ming-files-model",
            "ming-ota-backup",
            "boot/grub/themes/ming/theme.txt",
        ]:
            self.assertIn(marker, self.build)

    def test_final_2640_wallpaper_is_packaged_and_cached_at_supported_sizes(self):
        self.assertTrue(WALLPAPER_2640.is_file())
        self.assertGreater(WALLPAPER_2640.stat().st_size, 0)
        for marker in (
            'asset_2640="/tmp/ming-build/assets/wallpaper-ming-2640-abstract.png"',
            '/usr/share/backgrounds/ming-os/default-2640.png',
            '/usr/share/backgrounds/ming-os/default.png',
            'for geometry in 3840x2160 1920x1080 1366x768',
            'output="/usr/share/backgrounds/ming-os/default-${geometry}.png"',
        ):
            self.assertIn(marker, self.desktop)

    def test_wallpaper_cache_generation_fails_instead_of_copying_wrong_dimensions(self):
        wallpaper = self.desktop.split("setup_wallpaper() {", 1)[1].split(
            "# ======================== Xfce", 1
        )[0]
        self.assertIn('command -v convert >/dev/null 2>&1 || {', wallpaper)
        self.assertIn('if ! convert /usr/share/backgrounds/ming-os/default.png', wallpaper)
        self.assertIn('return 1', wallpaper)
        self.assertNotIn('2>/dev/null || \\\n                cp /usr/share/backgrounds/ming-os/default.png "${output}"', wallpaper)
        self.assertNotIn('cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-1920x1080.png', wallpaper)

        main = self.desktop.split("main() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertIn("setup_wallpaper || return 1", main)

    def test_build_gate_requires_source_and_every_installed_wallpaper_variant(self):
        for marker in (
            'assets/wallpaper-ming-2640-abstract.png',
            'usr/share/backgrounds/ming-os/default-2640.png',
            'usr/share/backgrounds/ming-os/default.png',
            'usr/share/backgrounds/ming-os/default-3840x2160.png',
            'usr/share/backgrounds/ming-os/default-1920x1080.png',
            'usr/share/backgrounds/ming-os/default-1366x768.png',
        ):
            self.assertIn(marker, self.build)
        for marker in (
            "import struct",
            'data[:8] != b"\\x89PNG\\r\\n\\x1a\\n"',
            'data[12:16] != b"IHDR"',
            'struct.unpack(">II", data[16:24])',
            'expected_wallpaper_sizes = {',
            'wallpaper dimensions mismatch',
        ):
            self.assertIn(marker, self.build)

    def test_settings_hub_never_overwrites_the_control_center_wrapper(self):
        settings_hub = SETTINGS_HUB.read_text(encoding="utf-8")
        self.assertNotIn("cat > /usr/local/bin/ming-control-center", settings_hub)
        self.assertNotIn("install -m 0755 ${src} /usr/local/bin/ming-control-center", settings_hub)

    def test_build_identity_targets_2641(self):
        self.assertIn('readonly MING_OS_VERSION="26.4.1"', self.build)
        self.assertIn('readonly ISO_VOLUME_ID="MING_OS_2641"', self.build)

    def test_debootstrap_requires_and_uses_debian_archive_keyring(self):
        for marker in (
            'DEBIAN_ARCHIVE_KEYRING',
            'debian-archive-keyring',
            'verify_debootstrap_keyring',
            '--keyring="${DEBIAN_ARCHIVE_KEYRING}"',
        ):
            self.assertIn(marker, self.build)
        main = self.build.split("main() {", 1)[1]
        self.assertLess(main.index("install_build_deps"),
                        main.index("verify_debootstrap_keyring"))
        self.assertLess(main.index("verify_debootstrap_keyring"),
                        main.index("run_debootstrap"))

    def test_build_locks_clean_source_identity_until_packaging_finishes(self):
        for marker in (
            "capture_build_identity",
            "assert_clean_source_tree",
            "git_build rev-parse HEAD",
            "git_build diff --ignore-cr-at-eol --quiet",
            "git_build ls-files --others --exclude-standard",
            "verify_build_identity",
            'BUILD_SOURCE_COMMIT',
        ):
            self.assertIn(marker, self.build)
        main = self.build.split("main() {", 1)[1]
        self.assertLess(main.index("capture_build_identity"), main.index("run_debootstrap"))
        self.assertLess(main.index("verify_build_identity"), main.index("build_iso"))
        self.assertGreater(main.rindex("verify_build_identity"), main.index("build_iso"))

    def test_rootfs_and_sidecar_share_verifiable_build_identity(self):
        for marker in (
            "/etc/ming-os-build.json", "build_id", "source_commit",
            "build_time_utc", "source_tree_sha256_prefix", "iso_sha256",
            "SHA256SUMS",
        ):
            self.assertIn(marker, self.build)
        self.assertIn("git_build ls-tree -r --full-tree HEAD", self.build)
        self.assertNotIn("xargs -0 sha256sum", self.build)

    def test_windows_handoff_copies_iso_checksum_and_build_identity_together(self):
        handoff = self.build[self.build.index('if [[ "${SCRIPT_DIR}" == /mnt/* ]]'):
                             self.build.index("\n}", self.build.index('if [[ "${SCRIPT_DIR}" == /mnt/* ]]'))]
        self.assertIn('"${OUTPUT_DIR}/SHA256SUMS"', handoff)
        self.assertIn('"${build_sidecar}"', handoff)
        self.assertIn("verify_build_identity", handoff)

    def test_rootfs_gate_rejects_root_owned_default_user_state(self):
        for marker in (
            'root / "home/user/.config/ming-os"',
            "must be owned by uid/gid 1000",
            "state_root.rglob",
        ):
            self.assertIn(marker, self.build)
        self.assertIn('root / "home/user"', self.build)
        self.assertIn("home/user ownership mismatch", self.build)

    def test_runtime_release_handoff_does_not_advertise_2632(self):
        release_doc = self.desktop.split("deploy_release_readme() {", 1)[1].split("deploy_xfce_modern_style() {", 1)[0]
        self.assertIn("26.4.1", release_doc)
        self.assertNotIn("26.3.2", release_doc)
        self.assertNotIn("MING_OS_2632", release_doc)

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

    def test_spark_notifier_unit_is_normalized_and_release_gated(self):
        """The vendor unit must not ship invalid retry directives or executable bits."""
        finalizer = FINALIZE.read_text(encoding="utf-8")
        for marker in (
            "normalize_spark_update_notifier_unit",
            "StartLimitIntervalSec=1h",
            "StartLimitBurst=3",
            "RestartSec=15",
            'chmod 0644 "${unit}"',
        ):
            self.assertIn(marker, finalizer)
        for marker in (
            "spark-update-notifier.service",
            "systemd-analyze verify",
            "must not be executable",
        ):
            self.assertIn(marker, self.build)

    def test_rootfs_gate_classifies_generated_helpers_by_interpreter(self):
        """Shell helpers must not be sent through Python bytecode validation."""
        for marker in [
            "bash_generated_helpers = [",
            '"usr/local/sbin/ming-oom-policy"',
            '"usr/local/sbin/ming-timer-policy"',
            '"usr/local/bin/ming-ota-run"',
            "for relative_path in bash_generated_helpers:",
            "python_generated_helpers = [",
            "for relative_path in python_generated_helpers:",
        ]:
            self.assertIn(marker, self.build)

    def test_rootfs_gate_validates_slice_units_with_slice_schema(self):
        """A systemd slice has a [Slice] section, not a service ExecStart."""
        for marker in [
            'if relative_path.endswith(".slice"):',
            '"[Slice]" not in text',
            "has no slice directive",
        ]:
            self.assertIn(marker, self.build)

    def test_rootfs_gate_matches_the_shipped_dock_theme_indicator_size(self):
        self.assertIn(
            'require_file("usr/share/plank/themes/Ming/dock.theme", "IndicatorSize=4")',
            self.build,
        )
        self.assertIn("OuterStrokeColor=255;;255;;255;;210", self.build)
        self.assertIn("FillStartColor=255;;255;;255;;238", self.build)
        self.assertIn("FillEndColor=246;;250;;249;;230", self.build)
        self.assertIn("[PlankDockTheme]", self.build)
        self.assertIn("BottomPadding=20", self.build)

    def test_rootfs_gate_requires_a_maintainable_installed_administrator_chain(self):
        for marker in (
            'require_file("usr/bin/sudo")',
            'require_file("usr/bin/pkexec")',
            'require_file("etc/sudoers", "%sudo")',
            "installed primary user is not in the sudo group",
            "installed identity repair must keep the primary user in sudo",
            "ensure_ming_user || exit 30",
        ):
            self.assertIn(marker, self.build)
        self.assertIn('gpasswd -d "${user_name}" sudo', self.build)
        self.assertIn("must not remove the primary user from sudo", self.build)

    def test_rootfs_gate_requires_keyboard_accessible_install_mode_chooser(self):
        self.assertIn('chooser_path = root / "usr/local/bin/ming-install-mode-chooser"', self.build)
        self.assertIn('launcher_path = root / "usr/local/bin/ming-calamares-launcher"', self.build)
        self.assertIn("keyboard-hostile Zenity radiolist chooser", self.build)
        calamares_validator = self.build.split("validate_calamares_config() {", 1)[1].split(
            "validate_iso_grub_config() {", 1
        )[0]
        self.assertNotIn('require_file("usr/local/bin/ming-install-mode-chooser"', calamares_validator)
        self.assertNotIn('require_file("usr/local/bin/ming-calamares-launcher"', calamares_validator)

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

    def test_build_gate_accepts_mode_selected_blank_ab_erase_flow(self):
        self.assertIn("ming-install-mode", self.build)
        self.assertIn("ming-calamares-launcher", self.build)
        self.assertNotIn(
            "partition.conf must not force one-click erase; initialPartitioningChoice must be none",
            self.build,
        )

    def test_build_identity_handles_windows_worktree_gitdir_under_wsl(self):
        self.assertIn("resolve_git_invocation", self.build)
        self.assertIn("gitdir:", self.build)
        self.assertIn("tr -d", self.build)
        self.assertIn("\\r", self.build)
        self.assertIn("/mnt/", self.build)
        self.assertIn("--work-tree=${SCRIPT_DIR}", self.build)
        self.assertIn("core.filemode=false", self.build)
        self.assertIn("assert_clean_source_tree", self.build)
        self.assertIn("diff --ignore-cr-at-eol --quiet", self.build)
        self.assertIn("ls-files --others --exclude-standard", self.build)
        self.assertIn("git_build rev-parse HEAD", self.build)
        self.assertIn("git_build ls-tree -r --full-tree HEAD", self.build)
        self.assertNotIn('git -C "${SCRIPT_DIR}" status --porcelain', self.build)

    def test_local_evidence_logs_do_not_dirty_release_builds(self):
        self.assertIn("test-evidence/", self.gitignore)

    def test_build_gate_structurally_validates_every_blank_ab_partition(self):
        gate = self.build[
            self.build.index('partition = load_yaml("etc/calamares/modules/partition.conf")'):
            self.build.index('desktop_gate = load_yaml', self.build.index('partition = load_yaml'))
        ]
        self.assertIn("defaultPartitionTableType", gate)
        self.assertIn("requiredPartitionTableType", gate)
        self.assertIn("MING-BIOSBOOT", gate)
        self.assertIn("21686148-6449-6E6F-744E-656564454649", gate)
        self.assertIn("C12A7328-F81F-11D2-BA4B-00A0C93EC93B", gate)
        for label, mountpoint in (
            ("MING-BIOSBOOT", ""),
            ("MING-ESP", "/boot/efi"),
            ("MING-BOOT", "/boot"),
            ("MING-ROOT-A", "/"),
            ("MING-ROOT-B", ""),
            ("MING-HOME", "/home"),
        ):
            self.assertIn(label, gate)
            if mountpoint:
                self.assertIn(mountpoint, gate)
        self.assertIn("expected_layout", gate)
        self.assertIn("exactly one", gate)

    def test_build_gate_rejects_blank_ab_without_gpt_and_bios_boot_contract(self):
        for marker in (
            "partition.conf blank_ab layout must require GPT",
            "partition.conf must create explicit MING-ESP FAT EFI partition",
            "partition.conf MING-BIOSBOOT must be an unformatted BIOS Boot Partition",
            "partition type normalizer must require explicit MING-ESP",
        ):
            self.assertIn(marker, self.build)

    def test_build_gate_requires_partition_type_normalizer_before_mount(self):
        for marker in (
            "ming-fix-partition-types",
            "settings.conf missing ming-fix-partition-types instance",
            "partition type normalizer must run after partition and before mount",
            "MING-BIOSBOOT:ef02",
            "MING-ESP:ef00",
            "MING-BOOT:8300",
        ):
            self.assertIn(marker, self.build)
        gate = self.build[
            self.build.index('partition = load_yaml("etc/calamares/modules/partition.conf")'):
            self.build.index('desktop_gate = load_yaml', self.build.index('partition = load_yaml'))
        ]
        self.assertNotIn("require_file(", gate)

    def test_build_gate_rejects_live_run_bind_before_unpackfs(self):
        for marker in (
            'mount = load_yaml("etc/calamares/modules/mount.conf")',
            "mount.conf must not bind the Live /run into the target before unpackfs",
            "mountPoint",
            "/run",
        ):
            self.assertIn(marker, self.build)

    def test_build_installs_noninteractive_apt_wrapper_for_modules(self):
        self.assertIn("/usr/local/sbin/apt-build", self.build)
    def test_build_installs_noninteractive_apt_wrapper_for_modules(self):
        self.assertIn("/usr/local/sbin/apt-build", self.build)
        self.assertIn("/usr/local/sbin/apt\" <<", self.build)
        self.assertIn("Acquire::Retries=3", self.build)
        self.assertIn("Acquire::http::Timeout=45", self.build)
        self.assertIn("Acquire::https::Timeout=45", self.build)
        self.assertIn("exec /usr/local/sbin/apt-build", self.build)

    def test_build_gate_requires_root_helper_for_live_calamares(self):
        self.assertIn("ming-live-installer-root", self.build)
        self.assertIn("org.ming.live.installer.policy", self.build)

    def test_build_gate_rejects_privileged_installer_logs_under_tmp(self):
        self.assertIn("must not write privileged logs under /tmp", self.build)
        self.assertIn("/run/ming-installer", self.build)

    def test_build_gate_requires_unified_final_installed_verification(self):
        self.assertIn("installed --receipt --final-boot", self.build)
        self.assertIn("unified final installed-system verification", self.build)

    def test_build_gate_follows_the_root_helper_installer_ownership(self):
        helper_gate = self.build.split(
            'if relative_path.endswith("ming-live-installer-root"):', 1
        )[1].split(
            'if relative_path.endswith("ming-calamares-preflight"):', 1
        )[0]
        for marker in ("ming-install-mode write", "ming-calamares-preflight", "calamares -d"):
            self.assertIn(marker, helper_gate)

        launcher_gate = self.build.split(
            'if relative_path.endswith("ming-calamares-launcher"):', 1
        )[1].split(
            'if relative_path.endswith(("ming-live-installer.sh", "ming-installer-session"))', 1
        )[0]
        self.assertIn("choose_install_mode", launcher_gate)
        self.assertIn("ming-live-installer-root", launcher_gate)
        self.assertNotIn('"ming-calamares-preflight"', launcher_gate)
        self.assertNotIn('"calamares -d"', launcher_gate)

    def test_build_gate_requires_unified_session_coordinator_autostart(self):
        """The image must not re-enable retired per-component session watchdogs."""
        self.assertIn('home/user/.config/autostart/ming-session-healthcheck.desktop', self.build)
        self.assertIn('ming-session-healthcheck --session', self.build)
        self.assertNotIn(
            'require_file("home/user/.config/autostart/ming-dock.desktop", "ming-plank-watchdog --session")',
            self.build,
        )
        self.assertNotIn(
            'require_file("home/user/.config/autostart/ming-phone-desktop.desktop", "ming-phone-desktop-watchdog --session")',
            self.build,
        )
        self.assertIn("legacy_exec = next(", self.build)
        self.assertIn('line.startswith("Exec=")', self.build)
        self.assertNotIn('if "ming-plank-watchdog --session" in legacy_entry', self.build)

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

    def test_installed_identity_contains_ota_restore_gate(self):
        for marker in [
            "ming.ota=1",
            "cmdline_value ming.ota_backup_uuid",
            "cmdline_value ming.ota_manifest",
            "ming-ota-restore.log",
            '"${engine}" restore',
        ]:
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
        self.assertIn('"ming-settings:ming-settings.desktop"', self.desktop)
        self.assertNotIn('"ming-update:ming-update.desktop"', self.desktop)


if __name__ == "__main__":
    unittest.main()
