import importlib.util
import json
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "assets" / "ming-installer-verify.py"
DESKTOP_MODULE = ROOT / "modules" / "03_desktop.sh"
BASE_MODULE = ROOT / "modules" / "01_base.sh"
BUILD = ROOT / "build_onion_os.sh"


def load_verifier():
    if not VERIFY_PATH.is_file():
        raise AssertionError(
            "Ming installer must ship assets/ming-installer-verify.py for "
            "Calamares and installed-system verification"
        )
    spec = importlib.util.spec_from_file_location("ming_installer_verify", VERIFY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write(root, relative, content="", executable=False):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)
    return path


def create_live_root(root):
    write(
        root,
        "etc/calamares/settings.conf",
        """---
sequence:
- show:
  - welcome
  - partition
  - summary
""",
    )
    write(
        root,
        "etc/calamares/modules/partition.conf",
        """---
initialPartitioningChoice: none
allowManualPartitioning: true
""",
    )
    write(
        root,
        "etc/calamares/modules/unpackfs.conf",
        """---
unpack:
  - source: \"/run/ming-installer/filesystem.squashfs\"
    sourcefs: \"squashfs\"
    destination: \"\"
""",
    )


def create_installed_root(root):
    write(root, "etc/fstab", "UUID=target-root / ext4 defaults 0 1\n")
    write(root, "etc/systemd/system/default.target", "/lib/systemd/system/graphical.target\n")
    write(root, "etc/systemd/system/display-manager.service", "/lib/systemd/system/lightdm.service\n")
    write(
        root,
        "etc/lightdm/lightdm.conf.d/60-ming-autologin.conf",
        "[Seat:*]\nautologin-session=xfce\n",
    )
    for path in (
        "usr/sbin/lightdm",
        "usr/bin/startxfce4",
        "usr/bin/xfce4-session",
        "usr/local/bin/ming-phone-desktop",
        "usr/local/bin/ming-session-healthcheck",
    ):
        write(root, path, "#!/bin/sh\nexit 0\n", executable=True)
    write(root, "usr/share/xsessions/xfce.desktop", "[Desktop Entry]\nName=Xfce\n")
    write(
        root,
        "home/user/.config/autostart/ming-session-healthcheck.desktop",
        "[Desktop Entry]\nExec=/usr/local/bin/ming-session-healthcheck --session\n",
    )


def target_mount_info(target, *, uuid="790ec0ef-1111-2222-3333-444444444444"):
    """Return an injectable authoritative mount record without a Calamares runtime."""
    canonical = str(target.resolve())
    return {
        "target": canonical,
        "canonical_target": canonical,
        "source": "/dev/mock-ming-root",
        "canonical_source": "/dev/mock-ming-root",
        "fstype": "ext4",
        "uuid": uuid,
        "is_block": True,
    }


def write_target_receipt(
    path,
    target,
    *,
    uuid="790ec0ef-1111-2222-3333-444444444444",
    attempt_nonce="test-target-attempt-nonce-00000000000000000000",
    create_attempt=True,
):
    mount = target_mount_info(target, uuid=uuid)
    path.parent.mkdir(parents=True, exist_ok=True)
    if create_attempt:
        path.with_name("target-receipt-attempt.json").write_text(
            json.dumps(
                {
                    "schema": "ming-installer-target-receipt-attempt/v1",
                    "version": 1,
                    "nonce": attempt_nonce,
                }
            ),
            encoding="utf-8",
        )
    path.write_text(
        json.dumps(
            {
                "schema": "ming-installer-target-receipt/v1",
                "version": 1,
                "attempt_nonce": attempt_nonce,
                "target": mount["target"],
                "canonical_target": mount["canonical_target"],
                "source": mount["source"],
                "canonical_source": mount["canonical_source"],
                "fstype": mount["fstype"],
                "uuid": mount["uuid"],
            }
        ),
        encoding="utf-8",
    )
    return mount


def write_ming_grub_template(root, uuid):
    return write(
        root,
        "etc/grub.d/09_ming_os",
        "#!/bin/sh\n"
        "menuentry 'Ming OS' {\n"
        f"    linux /vmlinuz root=UUID={uuid} ro\n"
        "}\n",
        executable=True,
    )


def receipt_lstat(*, mode=0o600, uid=0):
    """Portable root-owned regular-file stat fixture for Windows test hosts."""
    return lambda _path: types.SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid)


def shell_executable():
    """Use Git Bash on the Windows builder and bash directly in Linux CI."""
    git_bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return shutil.which("bash")


class InstallerReliabilityContracts(unittest.TestCase):
    def test_verifier_is_deployed_and_runs_before_calamares_bootloader(self):
        """Live and installed-system checks must be in the generated ISO path."""
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        build = BUILD.read_text(encoding="utf-8")

        self.assertIn("ming-installer-verify.py", desktop)
        self.assertIn("/usr/local/sbin/ming-installer-verify", desktop)
        self.assertIn("ming-installer-verify live --source", desktop)
        self.assertIn("ming-installed-desktop-gate", desktop)
        self.assertIn("ming-installer-verify installed --receipt", desktop)
        self.assertIn("ming-installer-target-receipt", desktop)
        self.assertIn("dontChroot: true", desktop)
        self.assertIn("shellprocess@ming-installed-desktop-gate", desktop)
        self.assertIn("ming-installer-target-receipt@ming-installer-target-receipt", desktop)
        self.assertIn(
            "mount\n  - ming-installer-target-receipt@ming-installer-target-receipt\n  - unpackfs",
            desktop,
        )
        self.assertIn("settings.conf missing ming-installed-desktop-gate instance", build)
        self.assertIn("ming-installer-verify", build)

    def test_every_generated_calamares_settings_path_binds_the_same_receipt_gate(self):
        """Base, runtime-preflight and static fallback configs must not diverge."""
        base = BASE_MODULE.read_text(encoding="utf-8")
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        settings_blocks = [
            base.split("cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'", 1)[1]
            .split("CALAMARESSETTINGS", 1)[0],
            desktop.split("cat > /etc/calamares/settings.conf <<'SETTINGS'", 1)[1]
            .split("SETTINGS", 1)[0],
            desktop.split("cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'", 1)[1]
            .split("STATICCALASETTINGS", 1)[0],
        ]
        for settings in settings_blocks:
            with self.subTest(settings=settings[:40]):
                self.assertIn("ming-installer-target-receipt", settings)
                self.assertIn("ming-installed-desktop-gate", settings)
                self.assertLess(
                    settings.index("ming-installer-target-receipt@ming-installer-target-receipt"),
                    settings.index("shellprocess@ming-identity"),
                )
                self.assertLess(
                    settings.index("shellprocess@ming-identity"),
                    settings.index("shellprocess@ming-installed-desktop-gate"),
                )
                self.assertLess(
                    settings.index("shellprocess@ming-installed-desktop-gate"),
                    settings.index("shellprocess@ming-bootloader"),
                )
                self.assertLess(
                    settings.index("shellprocess@ming-installer-target-receipt-reset"),
                    settings.index("mount"),
                )
                self.assertLess(
                    settings.index("mount"),
                    settings.index("ming-installer-target-receipt@ming-installer-target-receipt"),
                )

        preflight = desktop.split(
            "cat > /usr/local/sbin/ming-calamares-preflight << 'CALAMARESPREFLIGHT'\n", 1
        )[1].split("\nCALAMARESPREFLIGHT", 1)[0]
        self.assertIn("ming-installer-verify receipt --begin-attempt", preflight)

        ota_preflight = base.split("cat > /usr/local/sbin/ming-ota-preflight << 'MINGOTAPREFLIGHT'\n", 1)[1]
        ota_preflight = ota_preflight.split("\nMINGOTAPREFLIGHT", 1)[0]
        self.assertIn("ming-installer-verify receipt --begin-attempt", ota_preflight)
        self.assertLess(
            ota_preflight.index("ming-installer-verify receipt --begin-attempt"),
            ota_preflight.index("grep -qw 'ming.ota=1'"),
        )

    def test_receipt_job_imports_the_suffixless_deployed_verifier(self):
        """Calamares must not depend on a .py suffix when loading the deployed helper."""
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        receipt_job = desktop.split(
            "cat > \"${receipt_module}/main.py\" << 'TARGETRECEIPTPY'\n", 1
        )[1].split("\nTARGETRECEIPTPY", 1)[0]
        self.assertIn("import importlib.machinery", receipt_job)
        self.assertIn("SourceFileLoader", receipt_job)

        with tempfile.TemporaryDirectory() as directory:
            module_dir = pathlib.Path(directory)
            deployed = module_dir / "ming-installer-verify"
            deployed.write_bytes(VERIFY_PATH.read_bytes())
            job_path = module_dir / "main.py"
            job_path.write_text(
                receipt_job.replace("/usr/local/sbin/ming-installer-verify", deployed.as_posix()),
                encoding="utf-8",
            )
            write(
                module_dir,
                "libcalamares.py",
                "class _Storage:\n"
                "    def value(self, _key):\n"
                "        return None\n"
                "globalstorage = _Storage()\n",
            )
            probe = "\n".join(
                (
                    "import importlib.util, pathlib, sys",
                    "path = pathlib.Path(sys.argv[1])",
                    "sys.path.insert(0, str(path.parent))",
                    "spec = importlib.util.spec_from_file_location('ming_installer_target_receipt_probe', path)",
                    "module = importlib.util.module_from_spec(spec)",
                    "spec.loader.exec_module(module)",
                    "assert callable(module.VERIFIER.capture_target_receipt)",
                    "assert callable(module.run)",
                )
            )
            completed = subprocess.run(
                [sys.executable, "-c", probe, str(job_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_base_and_live_partition_configs_keep_both_install_modes(self):
        base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        for source in (base, desktop):
            partition = source.split("cat > /etc/calamares/modules/partition.conf", 1)[1]
            self.assertIn("initialPartitioningChoice: none", partition)
            self.assertIn("allowManualPartitioning: true", partition)
            self.assertNotIn("initialPartitioningChoice: erase", partition)
        self.assertNotIn("关闭手动分区入口", base)

    def test_live_validation_reports_both_manual_and_full_disk_choices(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "live"
            create_live_root(root)
            source = pathlib.Path(directory) / "ventoy" / "live" / "filesystem.squashfs"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"MING")

            result = verifier.verify_live(root, source)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual("enabled", result["manual_partitioning"])
        self.assertEqual("available", result["full_disk_install"])
        self.assertEqual("ventoy", result["source_kind"])

    def test_live_validation_rejects_a_hidden_manual_partitioning_choice(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "live"
            create_live_root(root)
            partition = root / "etc/calamares/modules/partition.conf"
            partition.write_text(
                "initialPartitioningChoice: erase\nallowManualPartitioning: false\n",
                encoding="utf-8",
            )
            source = pathlib.Path(directory) / "filesystem.squashfs"
            source.write_bytes(b"MING")

            result = verifier.verify_live(root, source)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("manual partitioning" in error.lower() for error in result["errors"]),
            result["errors"],
        )

    def test_installed_validation_accepts_a_complete_graphical_desktop_chain(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_installed_root(root)

            result = verifier.verify_installed(root)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual("graphical", result["default_target"])
        self.assertEqual("lightdm", result["display_manager"])
        self.assertEqual("ready", result["desktop_session"])

    def test_authoritative_receipt_uses_globalstorage_target_with_competing_complete_candidates(self):
        """A receipt must bind validation to Calamares's actual mount, never scoring stale roots."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            stale_target = root / "target"
            stale_other = root / "calamares-root-old"
            actual_target = root / "calamares-root-authoritative"
            create_installed_root(stale_target)
            create_installed_root(stale_other)
            create_installed_root(actual_target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, actual_target)
            write(
                actual_target,
                "etc/fstab",
                f"UUID={expected_mount['uuid']} / ext4 defaults 0 1\n",
            )
            write_ming_grub_template(actual_target, expected_mount["uuid"])

            def mount_info_provider(path):
                self.assertEqual(str(actual_target.resolve()), str(path))
                return expected_mount

            reader = getattr(verifier, "verify_installed_from_receipt", None)
            self.assertIsNotNone(reader, "installer verifier must expose receipt-bound validation")
            result = reader(
                receipt,
                mount_info_provider=mount_info_provider,
                lstat_func=receipt_lstat(),
                fstat_func=receipt_lstat(),
            )

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(str(actual_target.resolve()), result["target"])
        self.assertEqual("receipt", result["target_mode"])

    def test_receipt_reader_rejects_insecure_or_mismatched_receipts(self):
        """Receipt trust is rejected before the gate can touch any candidate target."""
        verifier = load_verifier()
        reader = getattr(verifier, "read_target_receipt", None)
        self.assertIsNotNone(reader, "installer verifier must expose a receipt reader")

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, target)
            write_ming_grub_template(target, expected_mount["uuid"])

            def matching_mount(_path):
                return expected_mount

            cases = (
                ("group-readable", receipt_lstat(mode=0o640), None),
                ("non-root-owner", receipt_lstat(uid=1000), None),
                (
                    "path-traversal",
                    receipt_lstat(),
                    {"target": str(target / "../escaped")},
                ),
                ("uuid-mismatch", receipt_lstat(), {"uuid": "wrong-uuid"}),
            )
            for name, lstat_func, override in cases:
                with self.subTest(name=name):
                    write_target_receipt(receipt, target)
                    if override:
                        payload = json.loads(receipt.read_text(encoding="utf-8"))
                        payload.update(override)
                        receipt.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(Exception):
                        reader(
                            receipt,
                            mount_info_provider=matching_mount,
                            lstat_func=lstat_func,
                            fstat_func=receipt_lstat(),
                        )

    def test_receipt_bound_gate_rejects_a_previous_mount_attempt_even_on_the_same_target(self):
        """A failed retry must not reuse a valid receipt captured by an earlier mount attempt."""
        verifier = load_verifier()
        self.assertTrue(
            hasattr(verifier, "begin_target_receipt_attempt"),
            "receipt verifier must rotate a per-mount-attempt nonce before Calamares mount",
        )

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, target)
            write(target, "etc/fstab", f"UUID={expected_mount['uuid']} / ext4 defaults 0 1\n")
            write_ming_grub_template(target, expected_mount["uuid"])

            previous_attempt = "previous-attempt-" + ("a" * 40)
            current_attempt = "current-attempt-" + ("b" * 40)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["attempt_nonce"] = previous_attempt
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            attempt_path = receipt.with_name("target-receipt-attempt.json")
            attempt_path.write_text(
                json.dumps(
                    {
                        "schema": "ming-installer-target-receipt-attempt/v1",
                        "version": 1,
                        "nonce": current_attempt,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(verifier.TargetReceiptError, "previous mount attempt"):
                verifier.verify_installed_from_receipt(
                    receipt,
                    mount_info_provider=lambda _path: expected_mount,
                    lstat_func=receipt_lstat(),
                    fstat_func=receipt_lstat(),
                )

    def test_receipt_bound_gate_rejects_a_receipt_without_a_fresh_attempt_marker(self):
        """A target receipt alone is never enough after a failed/retried Calamares mount."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, target, create_attempt=False)
            write(target, "etc/fstab", f"UUID={expected_mount['uuid']} / ext4 defaults 0 1\n")
            write_ming_grub_template(target, expected_mount["uuid"])

            with self.assertRaisesRegex(verifier.TargetReceiptError, "target receipt attempt"):
                verifier.verify_installed_from_receipt(
                    receipt,
                    mount_info_provider=lambda _path: expected_mount,
                    lstat_func=receipt_lstat(),
                    fstat_func=receipt_lstat(),
                )

    def test_begin_target_receipt_attempt_clears_the_previous_receipt_before_mount(self):
        """The reset wrapper must invalidate an old valid receipt before a retry can mount."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            receipt = pathlib.Path(directory) / "run/ming-installer/target-receipt.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}", encoding="utf-8")
            original_ensure = verifier._ensure_receipt_directory
            verifier._ensure_receipt_directory = lambda _path: None
            try:
                attempt = verifier.begin_target_receipt_attempt(
                    receipt_path=receipt,
                    nonce_factory=lambda _size: "fresh-attempt-" + ("c" * 40),
                    lstat_func=receipt_lstat(),
                )
            finally:
                verifier._ensure_receipt_directory = original_ensure

            self.assertFalse(receipt.exists())
            self.assertEqual("fresh-attempt-" + ("c" * 40), attempt["nonce"])
            marker = json.loads(receipt.with_name("target-receipt-attempt.json").read_text(encoding="utf-8"))
            self.assertEqual(attempt, marker)

    def test_receipt_bound_gate_rejects_an_fstab_root_uuid_that_differs_from_the_mount(self):
        """The bootloader must never accept a target whose fstab points at another root."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            write(target, "etc/fstab", "UUID=stale-root / ext4 defaults 0 1\n")
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, target)
            write_ming_grub_template(target, expected_mount["uuid"])

            result = verifier.verify_installed_from_receipt(
                receipt,
                mount_info_provider=lambda _path: expected_mount,
                lstat_func=receipt_lstat(),
                fstat_func=receipt_lstat(),
            )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("authoritative root UUID" in error for error in result["errors"]),
            result["errors"],
        )

    def test_receipt_bound_gate_rejects_an_unfinalized_ming_grub_template(self):
        """Identity must finish every root UUID placeholder before GRUB can run."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected_mount = write_target_receipt(receipt, target)
            write(target, "etc/fstab", f"UUID={expected_mount['uuid']} / ext4 defaults 0 1\n")
            write_ming_grub_template(target, "__MING_ROOT_UUID__")

            result = verifier.verify_installed_from_receipt(
                receipt,
                mount_info_provider=lambda _path: expected_mount,
                lstat_func=receipt_lstat(),
                fstat_func=receipt_lstat(),
            )

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("Ming GRUB template" in error for error in result["errors"]),
            result["errors"],
        )

    def test_identity_fstab_rewrite_preserves_non_root_entries_and_fails_atomically(self):
        """Replacing a stale root must retain /home, EFI and local mounts on awk failure."""
        shell = shell_executable()
        self.assertIsNotNone(shell, "installer fstab rewrite requires a POSIX shell")
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base[
            base.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            base.index("MINGIDENTITY\n", base.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]
        helpers = identity[
            identity.index("has_authoritative_root_fstab()"):
            identity.index("\nensure_persistent_root_fstab || exit 30")
        ]
        expected_uuid = "790ec0ef-1111-2222-3333-444444444444"
        original = (
            "# retained installer comment\n"
            "UUID=stale-root / ext4 defaults 0 1\n"
            "UUID=home-data /home ext4 defaults 0 2\n"
            "UUID=efi-data /boot/efi vfat umask=0077 0 1\n"
            "tmpfs /tmp tmpfs defaults 0 0\n"
        )

        def run_helper(target, *, fail_awk=False):
            prefix = "\n".join(
                (
                    "set -uo pipefail",
                    f"target={shlex.quote(target.as_posix())}",
                    f"root_uuid={shlex.quote(expected_uuid)}",
                    "root_fstype=ext4",
                    helpers,
                )
            )
            if fail_awk:
                prefix += "\nawk() { return 17; }"
            prefix += "\nensure_persistent_root_fstab"
            return subprocess.run(
                [shell, "-c", prefix],
                capture_output=True,
                text=True,
                timeout=15,
            )

        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "target"
            write(target, "etc/fstab", original)

            result = run_helper(target)
            self.assertEqual(0, result.returncode, result.stderr)
            rewritten = (target / "etc/fstab").read_text(encoding="utf-8")
            self.assertIn("# retained installer comment", rewritten)
            self.assertIn("UUID=home-data /home ext4 defaults 0 2", rewritten)
            self.assertIn("UUID=efi-data /boot/efi vfat umask=0077 0 1", rewritten)
            self.assertIn("tmpfs /tmp tmpfs defaults 0 0", rewritten)
            self.assertNotIn("UUID=stale-root / ext4", rewritten)
            self.assertIn(f"UUID={expected_uuid} / ext4 defaults 0 1", rewritten)

            write(target, "etc/fstab", original)
            failed = run_helper(target, fail_awk=True)
            self.assertNotEqual(0, failed.returncode)
            self.assertEqual(original, (target / "etc/fstab").read_text(encoding="utf-8"))

    def test_final_bootloader_rejects_any_ming_linux_stanza_with_a_wrong_root_uuid(self):
        """One valid stanza cannot mask another Ming entry that boots a stale root."""
        shell = shell_executable()
        self.assertIsNotNone(shell, "final GRUB validation requires a POSIX shell")
        base = BASE_MODULE.read_text(encoding="utf-8")
        bootloader = base[
            base.index("cat > /usr/local/sbin/ming-install-bootloader"):
            base.index("MINGBOOTLOADER\n", base.index("cat > /usr/local/sbin/ming-install-bootloader"))
        ]

        self.assertIn("validate_final_grub_root_uuid()", bootloader)
        helper = bootloader[
            bootloader.index("validate_final_grub_root_uuid()"):
            bootloader.index("\nif [ ! -s \"${root}/boot/grub/grub.cfg\"", bootloader.index("validate_final_grub_root_uuid()"))
        ]
        expected_uuid = "790ec0ef-1111-2222-3333-444444444444"
        with tempfile.TemporaryDirectory() as directory:
            grub_cfg = pathlib.Path(directory) / "grub.cfg"
            grub_cfg.write_text(
                "linux /vmlinuz root=UUID=" + expected_uuid + " ro\n"
                "linux /vmlinuz root=UUID=" + expected_uuid + " ro ming.safe_graphics=1\n",
                encoding="utf-8",
            )
            good = subprocess.run(
                [
                    shell,
                    "-c",
                    "\n".join(
                        (
                            "set -uo pipefail",
                            f"root_uuid={shlex.quote(expected_uuid)}",
                            helper,
                            f"validate_final_grub_root_uuid {shlex.quote(grub_cfg.as_posix())}",
                        )
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(0, good.returncode, good.stderr)

            grub_cfg.write_text(
                "linux /vmlinuz root=UUID=" + expected_uuid + " ro\n"
                "linux /vmlinuz root=UUID=stale-root ro\n",
                encoding="utf-8",
            )
            stale = subprocess.run(
                [
                    shell,
                    "-c",
                    "\n".join(
                        (
                            "set -uo pipefail",
                            f"root_uuid={shlex.quote(expected_uuid)}",
                            helper,
                            f"validate_final_grub_root_uuid {shlex.quote(grub_cfg.as_posix())}",
                        )
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertNotEqual(0, stale.returncode)

    def test_identity_defers_grub_generation_to_the_strict_final_bootloader(self):
        """A failed preliminary update-grub must not be ignored before the final hard gate."""
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base[
            base.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            base.index("MINGIDENTITY\n", base.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]

        self.assertNotIn("/tmp/ming-update-grub.log", identity)
        self.assertNotIn('chroot "${target}" /usr/sbin/update-grub', identity)

    def test_bootloader_revalidates_the_preinstalled_ota_runtime_after_grub_is_final(self):
        """A fresh 26.4 target must retain its embedded updater, not ask for bootstrap."""
        base = BASE_MODULE.read_text(encoding="utf-8")
        bootloader = base[
            base.index("cat > /usr/local/sbin/ming-install-bootloader"):
            base.index("MINGBOOTLOADER\n", base.index("cat > /usr/local/sbin/ming-install-bootloader"))
        ]

        self.assertIn("verify_embedded_ota_runtime()", bootloader)
        self.assertIn('"${capability}" --write-marker', bootloader)
        self.assertIn("grub-editenv /boot/grub/grubenv set saved_entry=ming-legacy", bootloader)
        self.assertIn("MING_OTA_RUN_IN_SLICE=1", bootloader)
        self.assertIn("/usr/local/bin/ming-update status --json", bootloader)
        self.assertGreater(
            bootloader.rindex("verify_embedded_ota_runtime || exit 24"),
            bootloader.index("update-grub"),
        )

    def test_installed_validation_keeps_an_explicit_target_explicit(self):
        """Explicit diagnostics must not silently validate a different target."""
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            requested_target = root / "requested"
            actual_target = root / "calamares-root-7f4a"
            create_installed_root(requested_target)
            create_installed_root(actual_target)

            result = verifier.verify_installed(requested_target)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(str(requested_target), result["target"])
        self.assertEqual("explicit", result["target_mode"])

    def test_installed_validation_rejects_ventoy_live_media_in_fstab(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_installed_root(root)
            write(
                root,
                "etc/fstab",
                "UUID=target-root / ext4 defaults 0 1\n"
                "/run/ventoy/iso/live/filesystem.squashfs /mnt/live squashfs ro 0 0\n",
            )

            result = verifier.verify_installed(root)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("temporary live-media" in error.lower() for error in result["errors"]),
            result["errors"],
        )

    def test_installed_validation_detects_a_missing_ming_desktop_entrypoint(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            create_installed_root(root)
            (root / "usr/local/bin/ming-phone-desktop").unlink()

            result = verifier.verify_installed(root)

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("ming-phone-desktop" in error for error in result["errors"]),
            result["errors"],
        )

    def test_identity_and_bootloader_share_safe_target_boundaries(self):
        """All post-mount helpers must use the same signed-in-memory target receipt."""
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base[
            base.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            base.index("MINGIDENTITY\n", base.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]
        bootloader = base[
            base.index("cat > /usr/local/sbin/ming-install-bootloader"):
            base.index("MINGBOOTLOADER\n", base.index("cat > /usr/local/sbin/ming-install-bootloader"))
        ]

        self.assertIn("ming-installer-verify receipt --field target", identity)
        self.assertIn("ming-installer-verify receipt --field uuid", identity)
        self.assertNotIn("calamares-root-*", identity)
        self.assertNotIn(' /target ', identity)
        self.assertIn("ensure_persistent_root_fstab()", identity)
        self.assertIn("ensure_graphical_boot_chain()", identity)
        self.assertIn("ming-installer-verify installed --receipt", bootloader)
        self.assertIn("ming-installer-verify receipt --field source", bootloader)
        self.assertNotIn("--auto-target", bootloader)

    def test_identity_hard_fails_when_the_grub_uuid_template_is_not_finalized(self):
        """A missing UUID must fail, never remove root= and leave an unbootable config."""
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base[
            base.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            base.index("MINGIDENTITY\n", base.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]

        self.assertIn("__MING_ROOT_UUID__", identity)
        self.assertIn("root UUID receipt is missing or invalid", identity)
        self.assertIn("still contains __MING_ROOT_UUID__", identity)
        self.assertNotIn("s/root=UUID=__MING_ROOT_UUID__ //", identity)


if __name__ == "__main__":
    unittest.main()
