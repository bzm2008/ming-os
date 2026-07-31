import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
AB_PATH = ROOT / "assets" / "ming-ota-ab.py"
STAGE_PATH = ROOT / "assets" / "ming-ota-ab-stage.sh"
OTA = ROOT / "modules" / "06_ota_update.sh"
GIT_BASH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")


def git_path(path):
    value = str(path.resolve()).replace("\\", "/")
    return "/%s%s" % (value[0].lower(), value[2:])


def generated_cli_prefix():
    module = OTA.read_text(encoding="utf-8")
    marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
    cli = module.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]
    return cli.split('case "${1:-help}" in', 1)[0]


def load_ab():
    spec = importlib.util.spec_from_file_location("ming_ota_ab", AB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def layout():
    return {
        "schema": 1,
        "layout": "ming-ab-v1",
        "slots": {
            "A": {
                "device": "/dev/disk/by-uuid/root-a",
                "uuid": "11111111-1111-1111-1111-111111111111",
                "grub_entry": "Ming OS 高级启动>Ming OS slot A",
            },
            "B": {
                "device": "/dev/disk/by-uuid/root-b",
                "uuid": "22222222-2222-2222-2222-222222222222",
                "grub_entry": "Ming OS 高级启动>Ming OS slot B",
            },
        },
        "boot": {
            "device": "/dev/disk/by-uuid/boot",
            "uuid": "44444444-4444-4444-4444-444444444444",
        },
        "home": {
            "device": "/dev/disk/by-uuid/home",
            "uuid": "33333333-3333-3333-3333-333333333333",
        },
    }


class OtaAbContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ab = load_ab()

    def test_detects_active_and_inactive_slot_from_mounted_root_uuid(self):
        status = self.ab.layout_status(
            layout(),
            root_uuid="11111111-1111-1111-1111-111111111111",
            home_uuid="33333333-3333-3333-3333-333333333333",
            boot_uuid="44444444-4444-4444-4444-444444444444",
        )

        self.assertTrue(status["ready"])
        self.assertEqual("A", status["active_slot"])
        self.assertEqual("B", status["inactive_slot"])

    def test_rejects_single_partition_or_ambiguous_layout(self):
        for bad in (
            {},
            {**layout(), "home": {"device": "/dev/sda2", "uuid": layout()["slots"]["A"]["uuid"]}},
            {**layout(), "slots": {"A": layout()["slots"]["A"]}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(self.ab.ContractError):
                    self.ab.layout_status(
                        bad,
                        root_uuid="11111111-1111-1111-1111-111111111111",
                        home_uuid="33333333-3333-3333-3333-333333333333",
                        boot_uuid="44444444-4444-4444-4444-444444444444",
                    )

    def test_rejects_missing_or_mismatched_shared_boot_filesystem(self):
        missing = dict(layout())
        missing.pop("boot")
        for bad, boot_uuid in ((missing, ""), (layout(), "55555555-5555-5555-5555-555555555555")):
            with self.subTest(boot_uuid=boot_uuid):
                with self.assertRaises(self.ab.ContractError):
                    self.ab.layout_status(
                        bad,
                        root_uuid="11111111-1111-1111-1111-111111111111",
                        home_uuid="33333333-3333-3333-3333-333333333333",
                        boot_uuid=boot_uuid,
                    )

    def test_pending_transaction_confirms_only_on_target_slot_after_health_passes(self):
        transaction = self.ab.begin_transaction(
            layout(), active_slot="A", target_slot="B", version="26.4.1", checksum="a" * 64
        )

        failed = self.ab.observe_boot(transaction, current_slot="B", healthy=False)
        confirmed = self.ab.observe_boot(transaction, current_slot="B", healthy=True)

        self.assertEqual("rollback_required", failed["status"])
        self.assertEqual("A", failed["boot_target"])
        self.assertEqual("confirmed", confirmed["status"])
        self.assertEqual("B", confirmed["boot_target"])

    def test_return_to_previous_slot_is_recorded_as_automatic_rollback(self):
        transaction = self.ab.begin_transaction(
            layout(), active_slot="A", target_slot="B", version="26.4.1", checksum="a" * 64
        )

        observed = self.ab.observe_boot(transaction, current_slot="A", healthy=True)

        self.assertEqual("rolled_back", observed["status"])
        self.assertEqual("A", observed["boot_target"])

    def test_failed_target_is_marked_rolled_back_only_after_previous_slot_boots(self):
        transaction = self.ab.begin_transaction(
            layout(), active_slot="A", target_slot="B", version="26.4.1", checksum="a" * 64
        )
        failed = self.ab.observe_boot(transaction, current_slot="B", healthy=False)

        rolled_back = self.ab.observe_boot(failed, current_slot="A", healthy=True)

        self.assertEqual("rolled_back", rolled_back["status"])

    def test_force_rollback_preserves_canonical_nested_entry(self):
        transaction = self.ab.begin_transaction(
            layout(), active_slot="A", target_slot="B", version="26.4.1", checksum="a" * 64
        )

        rolled_back = self.ab.force_rollback(transaction, "health check failed")

        self.assertEqual("Ming OS 高级启动>Ming OS slot A", rolled_back["previous_entry"])
        self.assertEqual("A", rolled_back["boot_target"])

    def test_force_rollback_normalizes_legacy_entry_for_read_only_compatibility(self):
        transaction = self.ab.begin_transaction(
            layout(), active_slot="A", target_slot="B", version="26.4.1", checksum="a" * 64
        )
        transaction["previous_entry"] = "Ming OS slot A"

        rolled_back = self.ab.force_rollback(transaction, "legacy receipt")

        self.assertEqual("Ming OS 高级启动>Ming OS slot A", rolled_back["previous_entry"])

    def test_prepares_inactive_root_with_target_root_and_shared_home_fstab(self):
        with tempfile.TemporaryDirectory(prefix="ming-ab-root-") as directory:
            root = pathlib.Path(directory)
            (root / "etc").mkdir()
            (root / "etc" / "fstab").write_text(
                "UUID=old / ext4 defaults 0 1\nUUID=oldhome /home ext4 defaults 0 2\n",
                encoding="utf-8",
            )

            self.ab.prepare_slot_root(root, layout(), "B")

            fstab = (root / "etc" / "fstab").read_text(encoding="utf-8")
            self.assertIn("UUID=22222222-2222-2222-2222-222222222222 / ", fstab)
            self.assertIn("UUID=33333333-3333-3333-3333-333333333333 /home ", fstab)
            self.assertIn("UUID=44444444-4444-4444-4444-444444444444 /boot ", fstab)
            self.assertNotIn("UUID=old ", fstab)
            self.assertEqual("B\n", (root / "etc" / "ming-ota-slot").read_text(encoding="ascii"))
            deployed = json.loads(
                (root / "etc" / "ming-update" / "slots.json").read_text(encoding="ascii"))
            self.assertEqual(layout(), deployed)
            self.assertEqual(
                "ming-ab-v1\n",
                (root / "etc" / "ming-update" / "ota-ready").read_text(encoding="ascii"),
            )
            installed_layout = json.loads(
                (root / "etc" / "ming-update" / "slots.json").read_text(encoding="utf-8")
            )
            self.assertEqual(layout(), installed_layout)
            grub = (root / "etc" / "grub.d" / "09_ming_os").read_text(encoding="utf-8")
            defaults = (root / "etc" / "default" / "grub.d" / "10-ming-os.cfg").read_text(
                encoding="utf-8")
            self.assertIn("Ming OS slot A", grub)
            self.assertIn("Ming OS slot B", grub)
            self.assertIn("submenu 'Ming OS 高级启动'", grub)
            self.assertLess(grub.index("submenu 'Ming OS 高级启动'"), grub.index("menuentry 'Ming OS slot A'"))
            self.assertIn("linux /ming-slots/B/vmlinuz root=UUID=22222222-2222-2222-2222-222222222222", grub)
            self.assertIn("root=UUID=11111111-1111-1111-1111-111111111111", grub)
            self.assertIn("root=UUID=22222222-2222-2222-2222-222222222222", grub)
            self.assertNotIn("__MING_", grub)
            self.assertIn("GRUB_DEFAULT=saved", defaults)

    def test_stage_engine_has_destructive_guards_and_one_shot_grub_contract(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        for required in (
            "ming-ota-ab status",
            "findmnt",
            "blkid",
            "blockdev --getsize64",
            '[[ -b "${target_device}" ]]',
            "unsquashfs",
            "grub-reboot",
            "grub-editenv list",
            "next_entry=",
            "ming-ota-ab begin",
            "prepare-root --root",
            "grub.cfg",
        ):
            self.assertIn(required, stage)
        self.assertNotIn("mkfs", stage)
        self.assertNotIn("parted", stage)
        self.assertIn("Ming OS 高级启动>", stage)

    def test_stage_engine_keeps_slot_specific_kernels_on_shared_boot(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('/boot/ming-slots/${target}', stage)
        self.assertIn('vmlinuz', stage)
        self.assertIn('initrd.img', stage)
        self.assertIn('/ming-slots/${target}/vmlinuz', stage)
        self.assertIn('/ming-slots/${target}/initrd.img', stage)

    def test_boot_health_requires_desktop_and_confirms_rollback_after_old_slot_boot(self):
        module = OTA.read_text(encoding="utf-8")
        health = module.split("cat > /usr/local/sbin/ming-ota-ab-health << 'ABHEALTH'\n", 1)[1].split("\nABHEALTH\n", 1)[0]
        self.assertIn("display-manager.service", health)
        self.assertIn("rollback_required", health)

    def test_boot_health_waits_for_display_manager_before_fail_closed_rollback(self):
        """A freshly staged slot must not roll back merely because LightDM is still starting."""
        module = OTA.read_text(encoding="utf-8")
        health = module.split("cat > /usr/local/sbin/ming-ota-ab-health << 'ABHEALTH'\n", 1)[1].split(
            "\nABHEALTH\n", 1
        )[0]
        unit = module.split(
            "cat > /etc/systemd/system/ming-ota-ab-health.service << 'ABHEALTHSERVICE'\n", 1
        )[1].split("\nABHEALTHSERVICE\n", 1)[0]

        self.assertIn("After=multi-user.target graphical.target display-manager.service", unit)
        self.assertIn("Wants=display-manager.service", unit)
        self.assertIn("HEALTH_READY_TIMEOUT_SECONDS=", health)
        self.assertIn("while (( SECONDS < health_deadline )); do", health)
        self.assertIn("sleep 2", health)

    def test_stage_engine_unmounts_iso_on_failure_and_checks_target_uuid_mounts(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        cleanup = stage.split("cleanup() {", 1)[1].split("}\ntrap cleanup", 1)[0]
        self.assertIn("ISO_MOUNT", cleanup)
        self.assertIn('findmnt -nro TARGET -S "UUID=${target_uuid}"', stage)

    def test_stage_engine_checks_expanded_filesystem_size_before_clearing_slot(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        self.assertIn("filesystem.size", stage)
        self.assertIn("expanded_bytes", stage)
        self.assertIn("unsquashfs -lln", stage)
        self.assertIn("df --output=size -B1", stage)
        self.assertLess(stage.index("expanded_bytes"), stage.index('find "${MOUNT_ROOT}"'))
        self.assertLess(stage.index("df --output=size -B1"), stage.index('find "${MOUNT_ROOT}"'))

    def test_stage_engine_mounts_only_root_owned_private_staging_copy(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        self.assertIn("/var/lib/ming-update/ab-staging", stage)
        self.assertIn("stat -c '%u:%g:%a'", stage)
        self.assertIn('readlink -f -- "${ISO}"', stage)
        self.assertLess(stage.index("trusted ISO staging"), stage.index('mount -o loop,ro "${ISO}"'))

    def test_stage_engine_syncs_kernel_to_shared_boot_before_grub_reboot(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('/boot/ming-slots/${target}', stage)
        self.assertIn('/ming-slots/${target}/vmlinuz', stage)
        self.assertIn('/ming-slots/${target}/initrd.img', stage)
        self.assertLess(stage.index('slot_boot="/boot/ming-slots/${target}"'),
                        stage.index('grub-reboot "${target_entry}"'))

    def test_module_deploys_ab_contract_but_preserves_legacy_backup_path(self):
        module = OTA.read_text(encoding="utf-8")
        self.assertIn("deploy_ota_ab_engine", module)
        self.assertIn("ota_ab_status_json", module)
        self.assertIn("ab_slot", module)
        self.assertIn("completed_backup", module)
        self.assertIn("backup_required", module)
        self.assertIn("ming-ota-ab-health.service", module)

    def test_ab_install_revalidates_signed_authoritative_identity_before_stage(self):
        module = OTA.read_text(encoding="utf-8")
        install = module.split("major_install_to_inactive_slot() {", 1)[1].split(
            "major_install_with_home_backup() {", 1)[0]
        self.assertIn("prepare_authoritative_ab_iso", install)
        helper = module.split("prepare_authoritative_ab_iso() {", 1)[1].split(
            "major_install_to_inactive_slot() {", 1)[0]
        for marker in (
            "fetch_authoritative_major_manifest",
            "validate_ota_manifest_schema",
            "verify_signed_ota_manifest",
            "authoritative_version",
            "authoritative_filename",
            "authoritative_checksum",
            "AB_STAGING_DIR",
            "install -d -o root -g root -m 0700",
            "sync -f",
        ):
            self.assertIn(marker, helper)
        self.assertLess(helper.index("install -o root -g root -m 0600"), helper.index("sync -f"))
        self.assertLess(helper.index("sync -f"), helper.index("verify_signed_ota_manifest"))

    def test_ab_private_iso_is_removed_when_authoritative_fetch_fails(self):
        module = OTA.read_text(encoding="utf-8")
        helper = module.split("prepare_authoritative_ab_iso() {", 1)[1].split(
            "major_install_to_inactive_slot() {", 1
        )[0]
        self.assertIn(
            'authoritative="$(fetch_authoritative_major_manifest)" || {\n'
            '        rm -f "${trusted_tmp}"\n'
            "        return 1\n"
            "    }",
            helper,
        )

    def test_legacy_staging_authoritative_fetch_failure_has_no_unbound_cleanup(self):
        if not GIT_BASH.is_file():
            self.skipTest("Git Bash is unavailable")

        checksum = "a" * 64
        backup_uuid = "11111111-1111-1111-1111-111111111111"
        with tempfile.TemporaryDirectory(prefix="ming-ota-staging-") as directory:
            root = pathlib.Path(directory)
            iso = root / "ming-os-26.4.1.iso"
            backup_manifest = root / "manifest.json"
            state_path = root / "state.json"
            script = root / "validate-staging.sh"
            iso.write_text("fake iso payload\n", encoding="utf-8")
            backup_manifest.write_text("{}", encoding="utf-8")
            state_path.write_text(
                json.dumps({
                    "status": "downloaded",
                    "version": "26.4.1",
                    "iso_path": git_path(iso),
                    "checksum": checksum,
                    "backup_uuid": backup_uuid,
                    "backup_manifest": git_path(backup_manifest),
                    "backup_manifest_relative": "/manifest.json",
                    "home_preservation": {"strategy": "completed_backup"},
                }),
                encoding="utf-8",
            )
            script.write_text(
                generated_cli_prefix() + r'''
findmnt() {
    case "$*" in
        *UUID*) printf '%s\n' "${MING_TEST_BACKUP_UUID}" ;;
        *TARGET*) printf '%s\n' "${MING_TEST_MOUNT_TARGET}" ;;
        *) return 1 ;;
    esac
}
paths_share_physical_disk() { return 1; }
sha256sum() { printf '%s  %s\n' "${MING_TEST_SHA256}" "$1"; }
fetch_authoritative_major_manifest() {
    log_error "authoritative fetch failed"
    return 1
}
validate_staging_inputs "${MING_TEST_STATE}"
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
                    "MING_TEST_BACKUP_UUID": backup_uuid,
                    "MING_TEST_MOUNT_TARGET": git_path(root),
                    "MING_TEST_SHA256": checksum,
                    "MING_TEST_STATE": git_path(state_path),
                },
            )

        combined = result.stdout + result.stderr
        self.assertNotEqual(0, result.returncode)
        self.assertIn("authoritative fetch failed", combined)
        self.assertNotIn("trusted_tmp", combined)
        self.assertNotIn("unbound variable", combined)

    def test_health_failures_use_one_fail_closed_rollback_path(self):
        module = OTA.read_text(encoding="utf-8")
        health = module.split("cat > /usr/local/sbin/ming-ota-ab-health << 'ABHEALTH'\n", 1)[1].split(
            "\nABHEALTH\n", 1)[0]
        self.assertIn("rollback_ab_boot()", health)
        self.assertIn("force-rollback", health)
        self.assertIn("saved_entry=${previous_entry}", health)
        self.assertIn("next_entry=${previous_entry}", health)
        self.assertNotIn('grub-set-default "${previous_entry}" || true', health)
        self.assertNotIn('grub-reboot "${previous_entry}" || true', health)
        self.assertNotIn('status)" || exit 1', health)
        self.assertNotIn('[[ "${current}" == "${target}" ]] || exit 1', health)

    def test_health_rollback_verifies_saved_and_next_grub_entries(self):
        module = OTA.read_text(encoding="utf-8")
        health = module.split("cat > /usr/local/sbin/ming-ota-ab-health << 'ABHEALTH'\n", 1)[1].split(
            "\nABHEALTH\n", 1)[0]
        self.assertIn("saved_entry=${previous_entry}", health)
        self.assertIn("next_entry=${previous_entry}", health)
        self.assertIn("grub-editenv list", health)
        self.assertNotIn('grub-set-default "${previous_entry}" || true', health)
        self.assertNotIn('grub-reboot "${previous_entry}" || true', health)

    def test_stage_checks_grub_commands_before_starting_transaction(self):
        stage = STAGE_PATH.read_text(encoding="utf-8")
        self.assertIn("command -v grub-reboot", stage)
        self.assertIn("command -v grub-editenv", stage)
        self.assertLess(stage.index("command -v grub-reboot"), stage.index("ming-ota-ab --layout", stage.index("ISO_MOUNT=\"\"")))

    def test_patch_apply_revalidates_manifest_schema_signature_and_digest(self):
        module = OTA.read_text(encoding="utf-8")
        apply = module.split("apply_manifest_apt_update() {", 1)[1].split(
            "# ======================== major ISO 升级", 1
        )[0]
        self.assertIn("validate_ota_manifest_schema", apply)
        self.assertIn("verify_signed_ota_manifest", apply)
        self.assertIn("sha256sum", apply)

    def test_legacy_patch_command_reuses_signed_main_manifest(self):
        module = OTA.read_text(encoding="utf-8")
        patch = module.split("patch_update() {", 1)[1].split("apply_manifest_apt_update() {", 1)[0]
        self.assertIn("check_update", patch)
        self.assertIn("apply_manifest_apt_update", patch)
        self.assertNotIn("/api/onion-patch", patch)

    def test_patch_apply_binds_the_revalidated_manifest_fingerprint(self):
        module = OTA.read_text(encoding="utf-8")
        apply = module.split("apply_manifest_apt_update() {", 1)[1].split(
            "# ======================== major ISO 升级", 1
        )[0]
        self.assertIn('expected_sha256="${2:-}"', apply)
        self.assertIn('"${manifest_digest,,}" != "${expected_sha256,,}"', apply)

    def test_major_apply_blocks_dual_boot_mode_before_writing_a_slot(self):
        module = OTA.read_text(encoding="utf-8")
        self.assertIn("disabled_dual_boot", module)
        self.assertIn("保留双系统模式，大版本 A/B OTA 已禁用", module)
        apply = module.split("apply_update() {", 1)[1].split("# 用途：夜间挂机维护", 1)[0]
        self.assertIn("major_ota_allowed", apply)
        self.assertLess(apply.index("major_ota_allowed"), apply.index("major_install_with_home_backup"))

    def test_layout_manifest_example_documents_installer_handoff(self):
        manifest = ROOT / "docs" / "ota" / "ming-ab-layout-v1.json"
        contents = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("ming-ab-v1", contents["layout"])
        self.assertEqual({"A", "B"}, set(contents["slots"]))
        self.assertIn("boot", contents)
        self.assertIn("home", contents)


if __name__ == "__main__":
    unittest.main()
