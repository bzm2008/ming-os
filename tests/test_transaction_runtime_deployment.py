import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA_MODULE = ROOT / "modules" / "06_ota_update.sh"
BUILD = ROOT / "build_onion_os.sh"
BOOTSTRAP_BUILDER = ROOT / "tools" / "build-ming-ota-bootstrap.sh"
REGRESSION_GATE = ROOT / "tools" / "run-transaction-ota-regression.sh"


class TransactionRuntimeDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.ota = OTA_MODULE.read_text(encoding="utf-8")
        self.build = BUILD.read_text(encoding="utf-8")

    def test_module_deploys_the_full_transaction_runtime_and_public_cli(self):
        self.assertIn("deploy_transaction_runtime()", self.ota)
        for asset in (
            "ming-update-cli.py",
            "ming-transaction-verify.py",
            "ming-transaction-state.py",
            "ming-transaction-slot.py",
            "ming-transaction-apply.py",
            "ming-transaction-boot.py",
            "ming-transaction-health.py",
            "ming-transaction-diagnostics.py",
            "ming-ota-bootstrap-capability.py",
            "ming-transaction-allowlist.txt",
            "ming-transaction-local-premount",
            "40_ming_transaction",
            "org.mingos.update.policy",
        ):
            self.assertIn(asset, self.ota)
        self.assertIn("/usr/local/lib/ming-update/ming-recovery-update", self.ota)
        self.assertIn("exec /usr/bin/python3 /usr/local/lib/ming-update/ming-update-cli.py", self.ota)
        self.assertIn("gpgv", self.ota)
        self.assertIn("zstd", self.ota)

    def test_missing_reviewed_trust_material_fails_the_ota_module(self):
        """A keyring or policy deployment failure must abort the enclosing build module."""
        main_start = self.ota.index("main() {")
        main_end = self.ota.index("\n}\n\nmain", main_start)
        main = self.ota[main_start:main_end]
        self.assertIn("deploy_transaction_runtime || return 1", main)

    def test_build_gate_checks_transaction_runtime_after_initramfs_generation(self):
        self.assertIn("validate_transactional_ota_runtime", self.build)
        self.assertIn("lsinitramfs", self.build)
        self.assertIn("ming-transaction", self.build)
        self.assertIn("ming-slot-a", self.build)
        self.assertIn("ming-slot-b", self.build)
        self.assertIn("ming-recovery-manual", self.build)
        self.assertIn("generate_initramfs", self.build)

    def test_build_gate_requires_the_display_manager_health_and_rollback_guard(self):
        start = self.build.index("validate_transactional_ota_runtime() {")
        end = self.build.index("# ======================== ISO", start)
        runtime_gate = self.build[start:end]
        for required in (
            "/etc/systemd/system/ming-transaction-rollback-reboot.service",
            "/etc/systemd/system/display-manager.service.d/20-ming-transaction-health.conf",
            "ming-transaction-rollback-reboot.service",
            "display-manager.service.d/20-ming-transaction-health.conf",
        ):
            self.assertIn(required, runtime_gate)

    def test_build_gate_treats_grub_entry_ids_as_data_not_grep_options(self):
        start = self.build.index("validate_transactional_ota_runtime() {")
        end = self.build.index("# ======================== ISO", start)
        runtime_gate = self.build[start:end]
        self.assertIn('grep -Fq -- "--id', runtime_gate)

    def test_transaction_regression_gate_covers_deployment_and_bootstrap_contracts(self):
        gate = REGRESSION_GATE.read_text(encoding="utf-8")
        for test_module in (
            "tests.test_transaction_runtime_deployment",
            "tests.test_transaction_bootstrap",
            "tests.test_update_single_flow",
        ):
            self.assertIn(test_module, gate)
        self.assertIn("tools/build-ming-ota-bootstrap.sh", gate)

    def test_legacy_recovery_iso_guard_remains_separate_from_transaction_delivery(self):
        start = self.ota.index("major_install_with_home_backup()")
        end = self.ota.index("apply_update()", start)
        recovery = self.ota[start:end]
        self.assertIn("未检测到独立物理备份盘", recovery)
        self.assertIn("ming-ota-backup backup", recovery)
        self.assertNotIn("transactional-slot-v1", recovery)
        self.assertNotIn("ming-transaction-engine", recovery)

    def test_legacy_recovery_helper_has_no_unattended_shutdown_command(self):
        start = self.ota.index("cat > /usr/local/lib/ming-update/ming-recovery-update << 'OTACLI'")
        end = self.ota.index("\nOTACLI\n", start)
        recovery_helper = self.ota[start:end]
        self.assertNotIn("auto-shutdown", recovery_helper)
        self.assertNotIn("auto_shutdown_update", recovery_helper)
        self.assertNotIn("systemctl poweroff", recovery_helper)

    def test_rootfs_validator_checks_the_new_public_cli_and_keeps_recovery_helper_separate(self):
        self.assertIn('ota_client = require_file("usr/local/bin/ming-update", "ming-update-cli.py")', self.build)
        self.assertIn('recovery_client = require_file("usr/local/lib/ming-update/ming-recovery-update"', self.build)
        start = self.build.index('ota_client = require_file("usr/local/bin/ming-update", "ming-update-cli.py")')
        end = self.build.index("for retired_path", start)
        validator = self.build[start:end]
        self.assertIn("ming.update.cli.v1", validator)
        self.assertIn("transactional-slot-v1", validator)
        self.assertNotIn("resolve_home()", validator)

    def test_scheduled_checks_only_call_the_frozen_json_public_cli(self):
        """The timer must not revive the retired Bash updater or recovery flow."""
        service_start = self.ota.index("cat > /etc/systemd/system/ming-update-check.service")
        service_end = self.ota.index("\nSYSTEMDSERVICE\n", service_start)
        service = self.ota[service_start:service_end]
        deployment_start = self.ota.index("deploy_systemd_services() {")
        deployment_end = self.ota.index("return 0", deployment_start)
        deployment = self.ota[deployment_start:deployment_end]
        wrapper_start = self.ota.index("cat > /usr/local/bin/ming-update << 'UPDATECLI'")
        wrapper_end = self.ota.index("\nUPDATECLI\n", wrapper_start)
        wrapper = self.ota[wrapper_start:wrapper_end]

        self.assertIn("ExecStart=/usr/local/bin/ming-update check --json", service)
        self.assertNotIn("MING_UPDATE_BACKGROUND_CHECK", service)
        self.assertIn("systemctl disable --now ming-update-boot-check.service", deployment)
        self.assertIn("/usr/local/bin/ming-boot-update-check", deployment)
        self.assertNotIn("ming-recovery-update", wrapper)

    def test_deployment_removes_the_standalone_update_desktop_launcher(self):
        """Settings is the only visible update UI; old compatibility redirects stay hidden."""
        start = self.ota.index("deploy_gui_tool() {")
        end = self.ota.index("return 0", start)
        deployed = self.ota[start:end]
        self.assertIn("exec /usr/local/bin/ming-control-center --page update \"$@\"", deployed)
        self.assertIn("rm -f -- /usr/share/applications/ming-update.desktop", deployed)
        self.assertIn('rm -f -- "/home/${MING_USER}/Desktop/ming-update.desktop"', deployed)
        self.assertNotIn("cat > /usr/share/applications/ming-update.desktop", deployed)
        self.assertNotIn("cp /usr/share/applications/ming-update.desktop", deployed)

    def test_bootstrap_builder_requires_policy_pinned_signature_identities(self):
        """A locally trusted but policy-unapproved signing key must fail closed."""
        source = BOOTSTRAP_BUILDER.read_text(encoding="utf-8")
        for marker in (
            "verify_bootstrap_signature_policy",
            "allowed_primary_fingerprints",
            "allowed_signing_fingerprints",
            "_validated_signature_fingerprints",
            "--status-fd=1",
        ):
            self.assertIn(marker, source)

    def test_bootstrap_builder_rejects_a_validsig_from_an_unapproved_primary_key(self):
        """Exercise the post-signing policy gate without creating permanent test keys."""
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        def executable(path, content):
            path.write_text(content, encoding="utf-8", newline="\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        signing = "A" * 40
        approved_primary = "B" * 40
        unexpected_primary = "C" * 40
        with tempfile.TemporaryDirectory(prefix="ming-bootstrap-policy-") as tempdir:
            temp = pathlib.Path(tempdir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            keyring = temp / "release-keyring.gpg"
            keyring.write_bytes(b"test-keyring")
            executable(
                fake_bin / "python3",
                "#!/usr/bin/env bash\nexec '%s' \"$@\"\n" % git_path(pathlib.Path(sys.executable)),
            )
            policy = temp / "policy.json"
            policy.write_text(json.dumps({
                "schema": "ming.ota-key-policy.v1",
                "allowed_primary_fingerprints": [approved_primary],
                "allowed_signing_fingerprints": [signing],
                "channels": ["stable"],
                "minimum_bootstrap": "1.0.0",
            }), encoding="utf-8")
            executable(fake_bin / "dpkg-deb", "#!/usr/bin/env bash\nset -eu\nprintf deb > \"${!#}\"\n")
            executable(fake_bin / "gpg", "#!/usr/bin/env bash\nset -eu\nout=\nwhile [[ $# -gt 0 ]]; do\n  case \"$1\" in\n    --output) out=\"$2\"; shift 2 ;;\n    *) shift ;;\n  esac\ndone\ntest -n \"$out\"\nprintf sig > \"$out\"\n")
            executable(
                fake_bin / "gpgv",
                "#!/usr/bin/env bash\nset -eu\nprintf '[GNUPG:] VALIDSIG %s 0 0 0 0 0 0 0 %s\\n' \"${MING_TEST_SIGNING}\" \"${MING_TEST_PRIMARY}\"\n",
            )
            runner = temp / "run-builder"
            executable(
                runner,
                "#!/usr/bin/env bash\nset -eu\nexport PATH=\"$(dirname \"$0\")/bin:/usr/local/bin:/usr/bin:/bin\"\nexec \"$@\"\n",
            )
            output = temp / "out"
            environment = dict(os.environ)
            environment.update({
                "MING_TEST_SIGNING": signing,
                "MING_TEST_PRIMARY": unexpected_primary,
            })
            result = subprocess.run(
                [
                    str(git_bash), git_path(runner), git_path(BOOTSTRAP_BUILDER),
                    "--version", "1.0.0",
                    "--keyring", git_path(keyring),
                    "--policy", git_path(policy),
                    "--signing-key", signing,
                    "--out", git_path(output),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                env=environment,
            )
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("primary", (result.stdout + result.stderr).lower())


if __name__ == "__main__":
    unittest.main()
