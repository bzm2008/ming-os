import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets" / "ming-ota-backup.sh"
OTA_MODULE = ROOT / "modules" / "06_ota_update.sh"
BASE_MODULE = ROOT / "modules" / "01_base.sh"


class OtaBackupTests(unittest.TestCase):
    @staticmethod
    def shell_path(value):
        value = str(value)
        if os.name == "nt" and len(value) > 2 and value[1] == ":":
            return f"/mnt/{value[0].lower()}{value[2:].replace(os.sep, '/')}"
        return value

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ming-ota-backup-"))
        self.source = self.tmp / "source"
        self.backup = self.tmp / "backup"
        self.restore = self.tmp / "restore"
        self.source.mkdir()
        (self.source / "Documents").mkdir()
        (self.source / "Documents" / "note.txt").write_text("Ming OTA\n", encoding="utf-8")
        (self.source / ".profile").write_text("profile\n", encoding="utf-8")
        link = self.source / "note-link"
        if os.name == "nt":
            subprocess.run(
                ["wsl.exe", "ln", "-s", "Documents/note.txt", self.shell_path(link)],
                check=True,
                capture_output=True,
            )
        else:
            link.symlink_to("Documents/note.txt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, **extra_env):
        overrides = {
            "MING_OTA_TEST_MODE": "1",
            "MING_OTA_MACHINE_ID": "test-machine",
            "MING_OTA_DISK_UUID": "test-disk-uuid",
            "MING_OTA_AVAILABLE_BYTES": str(1024 * 1024 * 1024),
            **extra_env,
        }
        for key in (
            "MING_OTA_SOURCE_ROOT",
            "MING_OTA_DEST_ROOT",
            "MING_OTA_SYSTEM_TARGET",
        ):
            if key in overrides:
                overrides[key] = self.shell_path(overrides[key])
        cli_args = [self.shell_path(value) if pathlib.Path(str(value)).is_absolute() else str(value) for value in args]
        assignments = [f"{key}={value}" for key, value in overrides.items()]
        command = ["bash", self.shell_path(SCRIPT), *cli_args]
        process_env = os.environ.copy()
        if os.name == "nt":
            command = ["wsl.exe", "env", *assignments, *command]
        else:
            process_env.update(overrides)
        return subprocess.run(
            command,
            env=process_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_help_lists_contract(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("backup", "verify", "restore", "doctor"):
            self.assertIn(command, result.stdout)

    def test_backup_verify_restore_roundtrip_and_manifest(self):
        result = self.run_cli("backup", "--source", self.source, "--dest", self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)

        manifest = json.loads((self.backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["machine_id"], "test-machine")
        self.assertEqual(manifest["source"], self.shell_path(self.source))
        self.assertEqual(manifest["dest"], self.shell_path(self.backup))
        self.assertEqual(manifest["destination"], self.shell_path(self.backup))
        self.assertEqual(manifest["disk_uuid"], "test-disk-uuid")
        self.assertEqual(manifest["backup_uuid"], "test-disk-uuid")
        self.assertEqual(manifest["file_count"], 2)
        self.assertGreater(manifest["bytes"], 0)
        self.assertTrue(manifest["completed"])
        self.assertTrue(manifest["complete"])
        entries = {entry["path"]: entry for entry in manifest["entries"]}
        self.assertEqual(entries["Documents/note.txt"]["type"], "file")
        self.assertRegex(entries["Documents/note.txt"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entries["note-link"]["type"], "symlink")
        self.assertEqual(entries["note-link"]["link_target"], "Documents/note.txt")
        self.assertFalse((self.backup / ".incomplete").exists())

        verified = self.run_cli("verify", "--dest", self.backup)
        self.assertEqual(verified.returncode, 0, verified.stderr)

        restored = self.run_cli(
            "restore",
            "--manifest",
            self.backup / "manifest.json",
            "--target",
            self.restore,
            "--system-target",
            self.tmp,
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(
            (self.restore / "Documents" / "note.txt").read_text(encoding="utf-8"),
            "Ming OTA\n",
        )

    def test_verify_and_restore_reject_equal_length_content_tampering(self):
        result = self.run_cli("backup", "--source", self.source, "--dest", self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload_file = self.backup / "data" / "Documents" / "note.txt"
        payload_file.write_text("Xing OTA\n", encoding="utf-8")

        verified = self.run_cli("verify", "--manifest", self.backup / "manifest.json")
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn("inventory", verified.stderr.lower())

        restored = self.run_cli(
            "restore",
            "--manifest",
            self.backup / "manifest.json",
            "--target",
            self.restore,
            "--system-target",
            self.tmp,
        )
        self.assertNotEqual(restored.returncode, 0)
        self.assertFalse((self.restore / "Documents" / "note.txt").exists())

    def test_verify_rejects_mode_and_symlink_target_tampering(self):
        result = self.run_cli("backup", "--source", self.source, "--dest", self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        profile = self.backup / "data" / ".profile"
        manifest_path = self.backup / "manifest.json"
        if os.name == "nt":
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = next(item for item in manifest["entries"] if item["path"] == ".profile")
            original_mode = entry["mode"]
            entry["mode"] = "0600" if original_mode != "0600" else "0644"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        else:
            original_mode = os.stat(profile).st_mode & 0o7777
            os.chmod(profile, 0o600 if original_mode != 0o600 else 0o644)
        mode_result = self.run_cli("verify", "--dest", self.backup)
        self.assertNotEqual(mode_result.returncode, 0)

        if os.name == "nt":
            entry["mode"] = original_mode
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        else:
            os.chmod(profile, original_mode)
        link = self.backup / "data" / "note-link"
        if os.name == "nt":
            subprocess.run(
                ["wsl.exe", "ln", "-snf", ".profile", self.shell_path(link)],
                check=True,
                capture_output=True,
            )
        else:
            link.unlink()
            link.symlink_to(".profile")
        link_result = self.run_cli("verify", "--dest", self.backup)
        self.assertNotEqual(link_result.returncode, 0)

    def test_backup_uses_ten_percent_headroom_instead_of_fixed_reserve(self):
        (self.source / "large.bin").write_bytes(b"x" * (8 * 1024 * 1024))
        result = self.run_cli(
            "backup",
            "--source",
            self.source,
            "--dest",
            self.backup,
            MING_OTA_MIN_RESERVE_BYTES="0",
            MING_OTA_AVAILABLE_BYTES=str(10 * 1024 * 1024),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_rejects_symlink_target_even_inside_system_target(self):
        result = self.run_cli("backup", "--source", self.source, "--dest", self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        real_target = self.tmp / "real-home"
        real_target.mkdir()
        link_target = self.tmp / "linked-home"
        if os.name == "nt":
            subprocess.run(
                ["wsl.exe", "ln", "-s", self.shell_path(real_target), self.shell_path(link_target)],
                check=True,
                capture_output=True,
            )
        else:
            link_target.symlink_to(real_target, target_is_directory=True)
        restored = self.run_cli(
            "restore",
            "--manifest",
            self.backup / "manifest.json",
            "--target",
            link_target,
            "--system-target",
            self.tmp,
        )
        self.assertNotEqual(restored.returncode, 0)
        self.assertIn("symlink", restored.stderr.lower())

    def test_verify_rejects_interrupted_backup_marker(self):
        self.backup.mkdir()
        (self.backup / ".incomplete").write_text("interrupted\n", encoding="utf-8")
        (self.backup / "manifest.json").write_text(
            json.dumps({"completed": False}), encoding="utf-8"
        )
        result = self.run_cli("verify", "--dest", self.backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete", result.stderr.lower())

    def test_backup_rejects_overlapping_paths(self):
        nested = self.source / "backup"
        result = self.run_cli("backup", "--source", self.source, "--dest", nested)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overlap", result.stderr.lower())

    def test_backup_rejects_insufficient_space(self):
        result = self.run_cli(
            "backup",
            "--source",
            self.source,
            "--dest",
            self.backup,
            MING_OTA_AVAILABLE_BYTES="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("space", result.stderr.lower())

    def test_backup_rejects_destination_conflicting_with_system_target(self):
        result = self.run_cli(
            "backup",
            "--source",
            self.source,
            "--dest",
            self.backup,
            MING_OTA_SYSTEM_TARGET=str(self.backup),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("system target", result.stderr.lower())

    def test_environment_path_overrides(self):
        result = self.run_cli(
            "backup",
            MING_OTA_SOURCE_ROOT=str(self.source),
            MING_OTA_DEST_ROOT=str(self.backup),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.backup / "manifest.json").is_file())

    def test_doctor_outputs_machine_readable_json(self):
        result = self.run_cli(
            "doctor",
            "--source",
            self.source,
            "--dest",
            self.backup,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["source"], self.shell_path(self.source))
        self.assertEqual(report["dest"], self.shell_path(self.backup))
        self.assertIn("rsync_available", report)
        self.assertTrue(report["paths_safe"])

    def test_verify_uses_current_destination_disk_uuid(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('current_uuid="$(disk_uuid)"', script)
        self.assertIn('"${uuid}" == "unknown"', script)


class OtaModuleContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = OTA_MODULE.read_text(encoding="utf-8")

    def test_backup_engine_is_installed_at_privileged_cli_path(self):
        self.assertIn("/tmp/ming-build/assets/ming-ota-backup.sh", self.module)
        self.assertIn("/usr/local/sbin/ming-ota-backup", self.module)

    def test_major_staging_requires_preservation_plan_or_completed_backup(self):
        self.assertNotIn("/tmp/ming-major-upgrade.conf", self.module)
        self.assertIn("home_preservation", self.module)
        self.assertIn("ming-ota-backup backup", self.module)
        self.assertIn("ming-ota-backup verify", self.module)
        self.assertIn("/home/.ming-ota/home-preservation.json", self.module)

    def test_home_preservation_requires_a_distinct_block_device(self):
        self.assertIn("MAJ:MIN", self.module)
        self.assertIn("home_is_independent_device", self.module)
        self.assertNotIn('home_src}" != "$(findmnt -rno SOURCE /)', self.module)

    def test_grub_entry_carries_ota_backup_contract(self):
        self.assertIn("ming.ota=1", self.module)
        self.assertIn("ming.ota_backup_uuid=", self.module)
        self.assertIn("ming.ota_manifest=", self.module)

    def test_major_iso_is_staged_on_backup_uuid_for_grub(self):
        self.assertIn("iso_boot_path", self.module)
        self.assertIn('findmnt -nro UUID -T "${iso_path}"', self.module)
        self.assertIn('search --no-floppy --fs-uuid --set=root ${backup_uuid}', self.module)
        self.assertNotIn('search --no-floppy --file --set=root \\${iso_path}', self.module)

    def test_download_rejects_path_filenames_and_missing_sha256(self):
        self.assertIn('basename -- "${iso_name}"', self.module)
        self.assertIn("ISO filename must be a basename", self.module)
        self.assertIn("major update manifest requires a valid SHA256", self.module)

    def test_root_staging_revalidates_untrusted_user_state(self):
        self.assertIn("validate_staging_inputs", self.module)
        self.assertIn("/var/lib/ming-update/staging.json", self.module)
        self.assertIn("sha256sum", self.module)
        self.assertIn("backup_manifest_relative", self.module)

    def test_root_staging_refetches_authoritative_major_manifest(self):
        self.assertIn("fetch_authoritative_major_manifest", self.module)
        self.assertIn("authoritative_checksum", self.module)
        self.assertIn("authoritative ISO metadata mismatch", self.module)

    def test_update_cli_exposes_doctor(self):
        self.assertIn("doctor) ota_doctor", self.module)
        self.assertIn("ming-update doctor", self.module)

    def test_doctor_validates_existing_staging_record(self):
        self.assertIn("validate_staging_record_local", self.module)
        self.assertIn("staging_ok", self.module)
        self.assertIn('sha256sum -- "${iso_path}"', self.module)

    def test_grub_staging_does_not_ignore_update_grub_failure(self):
        self.assertNotIn("update-grub || true", self.module)
        self.assertIn("failed to regenerate GRUB after OTA staging", self.module)

    def test_pkexec_state_update_preserves_original_owner(self):
        self.assertIn("stat -c '%u:%g'", self.module)
        self.assertIn('chown "${owner}" "${tmp}"', self.module)

    def test_root_prefers_a_valid_downloaded_user_state_over_stale_state(self):
        self.assertIn("state_candidate_is_downloaded", self.module)
        self.assertIn("/home/*/.config/ming-update/state.json", self.module)

    def test_patch_packages_are_allowlisted_and_dpkg_recovery_is_bounded(self):
        self.assertIn("is_safe_apt_package", self.module)
        self.assertIn('"${package_spec}" != *[-+]', self.module)
        self.assertIn("timeout 300", self.module)
        self.assertIn("dpkg --configure -a", self.module)

    def test_ota_partition_guard_runs_before_destructive_partitioning(self):
        base = BASE_MODULE.read_text(encoding="utf-8")
        self.assertIn("ming-ota-target-guard", base)
        guard_index = base.index("- id: ming-ota-target-guard")
        partition_index = base.index("  - partition", guard_index)
        self.assertLess(guard_index, partition_index)

    def test_storage_checks_trace_all_physical_disk_ancestors(self):
        backup = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("lsblk -s", backup)
        self.assertIn("physical_disks", backup)

    def test_unsigned_remote_patch_scripts_are_rejected(self):
        self.assertIn("unsigned patch_script_url is not supported", self.module)
        self.assertNotIn('bash "${patch_script}"', self.module)

    def test_module_and_deployed_cli_are_valid_bash(self):
        def shell_path(path):
            value = str(path)
            if os.name == "nt":
                return f"/mnt/{value[0].lower()}{value[2:].replace(os.sep, '/')}"
            return value

        runner = ["wsl.exe"] if os.name == "nt" else []
        module_check = subprocess.run(
            [*runner, "bash", "-n", shell_path(OTA_MODULE)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(module_check.returncode, 0, module_check.stderr)

        marker = "cat > /usr/local/bin/ming-update << 'OTACLI'\n"
        cli = self.module.split(marker, 1)[1].split("\nOTACLI\n", 1)[0]
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as handle:
            handle.write(cli.encode("utf-8"))
            cli_path = pathlib.Path(handle.name)
        try:
            cli_check = subprocess.run(
                [*runner, "bash", "-n", shell_path(cli_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(cli_check.returncode, 0, cli_check.stderr)
            help_check = subprocess.run(
                [*runner, "env", "-u", "HOME", "bash", shell_path(cli_path), "help"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertEqual(help_check.returncode, 0, help_check.stderr)
            self.assertIn("doctor", help_check.stdout)
        finally:
            cli_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
