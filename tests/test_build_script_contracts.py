import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
RESUME = (ROOT / "resume_build.sh").read_text(encoding="utf-8")


class BuildScriptContractTests(unittest.TestCase):
    def test_main_build_exposes_staged_resume_interface(self):
        for marker in (
            "host-preflight",
            "debootstrap",
            "prepare-chroot",
            "modules",
            "initramfs",
            "clean-rootfs",
            "squashfs",
            "boot-assets",
            "iso",
            "publish-artifacts",
            "--fresh",
            "--resume",
            "--from",
            "--profile",
            "MING_BUILD_PROFILE",
            "MING_BUILD_FROM",
            "scripts/ming_build_state.py",
            "last-failure.json",
        ):
            self.assertIn(marker, BUILD)

    def test_release_and_fast_profiles_have_explicit_compression_contracts(self):
        for marker in (
            'PROFILE_RELEASE="release"',
            'PROFILE_FAST_TEST="fast-test"',
            'PROFILE_LEGACY_LOWRAM="legacy-lowram"',
            'PROFILE_COMPAT_HWE="compat-hwe"',
            'release:xz',
            'fast-test:zstd',
            'legacy-lowram:zstd',
            'compat-hwe:xz',
            "compression",
            "profile",
        ):
            self.assertIn(marker, BUILD)

    def test_resume_never_sources_or_reimplements_the_main_script(self):
        self.assertNotIn('eval "$(grep', RESUME)
        self.assertNotIn("local modules=(", RESUME)
        self.assertIn('exec "${SCRIPT_DIR}/build_onion_os.sh" --resume', RESUME)
        self.assertIn('MING_BUILD_FROM', RESUME)

    def test_chroot_reuse_is_state_checked_and_fresh_is_explicit(self):
        for marker in (
            "MING_REUSE_CHROOT",
            "MING_BUILD_FRESH",
            "state_is_complete",
            "input_hash",
            "invalidate_from",
            "run_debootstrap",
            "--fresh",
        ):
            self.assertIn(marker, BUILD)
        self.assertNotIn(
            'if [[ "${MING_REUSE_CHROOT}" == "1" && -f "${CHROOT_DIR}/etc/ming-version" ]]',
            BUILD,
        )

    def test_apt_cache_and_chroot_cache_are_explicitly_reusable(self):
        for marker in (
            "APT_ARCHIVES_CACHE",
            "CHROOT_CACHE_DIR",
            "bind",
            "apt-archives",
            "cache-manifest.json",
            "cache invalid",
            "Acquire::Retries=5",
        ):
            self.assertIn(marker, BUILD)

    def test_profile_checkpoints_are_isolated(self):
        self.assertIn("BUILD_STATE_ROOT", BUILD)
        self.assertIn("${BUILD_STATE_ROOT}/${MING_BUILD_PROFILE}", BUILD)

    def test_apt_cache_manifest_is_written_atomically(self):
        self.assertIn("cache-manifest.json.partial", BUILD)

    def test_successful_build_preserves_checkpoint_artifacts_for_resume(self):
        body = BUILD.split("build_iso() {", 1)[1].split(
            "build_iso_manual() {", 1
        )[0]
        self.assertNotIn('rm -rf "${ISO_DIR}"', body)

    def test_legacy_reuse_flag_cannot_bypass_checkpoint_validation(self):
        body = BUILD.split("stage_debootstrap() {", 1)[1].split(
            "stage_prepare_chroot() {", 1
        )[0]
        self.assertNotIn("MING_REUSE_CHROOT", body)

    def test_iso_is_built_to_a_partial_path_before_publish(self):
        body = BUILD.split("stage_iso() {", 1)[1].split(
            "stage_publish_artifacts() {", 1
        )[0]
        self.assertIn(".partial", body)
        self.assertIn("mv -f", body)

    def test_stage_artifacts_are_passed_without_word_splitting(self):
        self.assertIn("declare -a STAGE_ARTIFACTS", BUILD)
        self.assertIn(
            'state_mark_completed "${stage}" "${STAGE_ARTIFACTS[@]}"',
            BUILD,
        )

    def test_apt_cache_manifest_uses_structured_json_io(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        writer = BUILD.split("write_apt_cache_manifest() {", 1)[1].split(
            "write_stage_marker() {", 1
        )[0]
        self.assertIn("json.load", validation)
        self.assertIn("json.dump", writer)

    def test_rerunning_an_intermediate_stage_invalidates_successors(self):
        self.assertIn("invalidate_successors", BUILD)
        run_stage = BUILD.split("run_stage() {", 1)[1].split(
            "# 检查命令是否存在", 1
        )[0]
        self.assertIn("invalidate_successors", run_stage)

    def test_failure_trap_records_stage_and_command_before_exit(self):
        for marker in (
            "CURRENT_STAGE",
            "CURRENT_COMMAND",
            "record_build_failure",
            "BASH_LINENO",
            "PIPESTATUS",
            "resume_hint",
        ):
            self.assertIn(marker, BUILD)

    def test_old_entrypoints_delegate_to_the_rc_builder(self):
        for name in (
            "continue_build.sh",
            "fast_build_iso.sh",
            "final_build_iso.sh",
            "rebuild_iso.sh",
            "generate_final_iso.sh",
        ):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("build_onion_os.sh", source, name)
            self.assertNotIn("grub-mkrescue", source, name)
            self.assertNotIn("ONION_OS_VERSION=\"26.0.0\"", source, name)

    def test_profile_wrappers_cannot_be_overridden_by_later_arguments(self):
        fast = (ROOT / "fast_build_iso.sh").read_text(encoding="utf-8")
        release = (ROOT / "final_build_iso.sh").read_text(encoding="utf-8")
        self.assertIn('"$@" --profile fast-test', fast)
        self.assertIn('"$@" --profile release', release)

    def test_host_apt_dependency_install_has_timeout_and_retry_policy(self):
        install = BUILD.split("install_build_deps() {", 1)[1].split(
            "verify_debootstrap_keyring() {", 1
        )[0]
        for marker in (
            "DEBIAN_FRONTEND=noninteractive",
            "Acquire::Retries=5",
            "Acquire::http::Timeout=15",
            "Acquire::https::Timeout=15",
        ):
            self.assertIn(marker, install)

    def test_apt_cache_manifest_validates_mirror_identity(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        self.assertIn('payload.get("mirror")', validation)
        self.assertIn('payload.get("security_mirror")', validation)
        self.assertIn('payload.get("keyring_sha256")', validation)
        self.assertIn('"keyring_sha256": sys.argv[6]', BUILD)

    def test_apt_cache_identity_mismatch_removes_reusable_deb_archives(self):
        validation = BUILD.split("validate_apt_cache_manifest() {", 1)[1].split(
            "write_apt_cache_manifest() {", 1
        )[0]
        self.assertIn('find "${APT_ARCHIVES_CACHE}"', validation)
        self.assertIn("-name '*.deb'", validation)
        self.assertIn("cache identity", validation.lower())

    def test_release_image_contains_ota_minisign_tool_and_public_key_gate(self):
        install = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
        dependencies = install.split("install_ota_dependencies() {", 1)[1].split(
            "deploy_ota_backup_engine() {", 1
        )[0]
        self.assertIn("minisign", dependencies)
        self.assertIn("deploy_ota_release_trust", install)
        self.assertIn("ota-release.minisign.pub", BUILD)
        self.assertIn('"etc/ming-update/ota-release.minisign.pub"', BUILD)

    def test_release_profile_rejects_skipping_xiahai_and_sidecar_records_it(self):
        profile = BUILD.split("configure_build_profile() {", 1)[1].split(
            "acquire_build_lock() {", 1
        )[0]
        self.assertIn("MING_SKIP_XIAHAI", profile)
        self.assertIn("release", profile)
        self.assertIn("release_eligible", BUILD)
        self.assertIn("skip_xiahai", BUILD)

    def test_rootfs_gate_resolves_symlinks_without_escaping_target(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        for marker in (
            "def _rootfs_path(relative_path):",
            "path.lstat()",
            "os.readlink(path)",
            "resolves outside target rootfs",
            "contains an unresolved or looping symlink",
        ):
            self.assertIn(marker, validator)

    def test_rootfs_gate_detects_dangling_forbidden_symlink(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        absent = validator.split("def require_absent(", 1)[1].split(
            "def validate_generated_executable", 1
        )[0]
        self.assertIn("path.lstat()", absent)
        self.assertIn("path.is_symlink()", absent)


if __name__ == "__main__":
    unittest.main()
