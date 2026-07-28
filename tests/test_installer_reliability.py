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
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "assets" / "ming-installer-verify.py"
BASE_MODULE = ROOT / "modules" / "01_base.sh"
DESKTOP_MODULE = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


def load_verifier():
    if not VERIFY_PATH.is_file():
        raise AssertionError(
            "Ming installer must ship assets/ming-installer-verify.py for "
            "target receipt verification"
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


def create_installed_root(root, uuid="790ec0ef-1111-2222-3333-444444444444"):
    write(root, "etc/fstab", f"UUID={uuid} / ext4 defaults 0 1\n")
    write(root, "etc/systemd/system/default.target", "/lib/systemd/system/graphical.target\n")
    write(root, "etc/systemd/system/display-manager.service", "/lib/systemd/system/lightdm.service\n")
    write(
        root,
        "etc/lightdm/lightdm.conf.d/60-ming-autologin.conf",
        "[Seat:*]\nautologin-session=xfce\n",
    )
    for relative in (
        "usr/sbin/lightdm",
        "usr/bin/startxfce4",
        "usr/bin/xfce4-session",
        "usr/local/bin/ming-phone-desktop",
        "usr/local/bin/ming-session-healthcheck",
    ):
        write(root, relative, "#!/bin/sh\nexit 0\n", executable=True)
    write(root, "usr/share/xsessions/xfce.desktop", "[Desktop Entry]\nName=Xfce\n")
    write(
        root,
        "home/user/.config/autostart/ming-session-healthcheck.desktop",
        "[Desktop Entry]\nExec=/usr/local/bin/ming-session-healthcheck --session\n",
    )
    write(
        root,
        "etc/grub.d/09_ming_os",
        "#!/bin/sh\nmenuentry 'Ming OS' {\n"
        f"    linux /vmlinuz root=UUID={uuid} ro\n"
        "}\n",
        executable=True,
    )
    write(
        root,
        "boot/grub/grub.cfg",
        f"linux /vmlinuz root=UUID={uuid} ro\n",
    )


def target_mount_info(target, uuid="790ec0ef-1111-2222-3333-444444444444"):
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


def receipt_stat(mode=0o600, uid=0):
    return lambda _value: types.SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid)


def write_receipt(receipt, target, *, nonce=None, attempt_nonce=None, create_attempt=True):
    nonce = nonce or "receipt-attempt-" + ("a" * 40)
    attempt_nonce = attempt_nonce or nonce
    mount = target_mount_info(target)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if create_attempt:
        receipt.with_name("target-receipt-attempt.json").write_text(
            json.dumps(
                {
                    "schema": "ming-installer-target-receipt-attempt/v1",
                    "version": 1,
                    "nonce": nonce,
                }
            ),
            encoding="utf-8",
        )
    receipt.write_text(
        json.dumps(
            {
                "schema": "ming-installer-target-receipt/v1",
                "version": 1,
                "attempt_nonce": attempt_nonce,
                **{key: mount[key] for key in (
                    "target", "canonical_target", "source", "canonical_source", "fstype", "uuid"
                )},
            }
        ),
        encoding="utf-8",
    )
    return mount


def shell_executable():
    git_bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
    return str(git_bash) if git_bash.is_file() else shutil.which("bash")


class InstallerReceiptContracts(unittest.TestCase):
    def test_begin_target_receipt_attempt_replaces_stale_receipt_with_private_nonce(self):
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
                    nonce_factory=lambda _size: "fresh-attempt-" + ("b" * 40),
                    lstat_func=receipt_stat(),
                )
            finally:
                verifier._ensure_receipt_directory = original_ensure

            attempt_path = receipt.with_name("target-receipt-attempt.json")
            self.assertFalse(receipt.exists())
            self.assertEqual(attempt, json.loads(attempt_path.read_text(encoding="utf-8")))
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(attempt_path.stat().st_mode))

    def test_capture_and_read_bind_exact_globalstorage_target(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            target.mkdir()
            receipt = root / "run/ming-installer/target-receipt.json"
            attempt = receipt.with_name("target-receipt-attempt.json")
            attempt.parent.mkdir(parents=True)
            nonce = "capture-attempt-" + ("c" * 40)
            attempt.write_text(
                json.dumps(
                    {
                        "schema": "ming-installer-target-receipt-attempt/v1",
                        "version": 1,
                        "nonce": nonce,
                    }
                ),
                encoding="utf-8",
            )
            expected = target_mount_info(target)
            original_ensure = verifier._ensure_receipt_directory
            verifier._ensure_receipt_directory = lambda _path: None
            try:
                captured = verifier.capture_target_receipt(
                    target,
                    receipt_path=receipt,
                    mount_info_provider=lambda path: expected,
                    lstat_func=receipt_stat(),
                    fstat_func=receipt_stat(),
                )
            finally:
                verifier._ensure_receipt_directory = original_ensure

            readback = verifier.read_target_receipt(
                receipt,
                mount_info_provider=lambda path: expected,
                lstat_func=receipt_stat(),
                fstat_func=receipt_stat(),
            )

        self.assertEqual(nonce, captured["attempt_nonce"])
        self.assertEqual(str(target.resolve()), readback["target"])
        self.assertEqual(expected["uuid"], readback["uuid"])

    def test_reader_rejects_unsafe_stale_or_changed_receipts(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            target.mkdir()
            receipt = root / "run/ming-installer/target-receipt.json"
            expected = write_receipt(receipt, target)
            cases = (
                ("group-readable", receipt_stat(mode=0o640), None, expected),
                ("non-root-owner", receipt_stat(uid=1000), None, expected),
                ("path-traversal", receipt_stat(), {"target": str(target / "../escaped")}, expected),
                ("source-mismatch", receipt_stat(), None, {**expected, "source": "/dev/other"}),
                ("uuid-mismatch", receipt_stat(), None, {**expected, "uuid": "different-uuid"}),
            )
            for name, lstat_func, override, current in cases:
                with self.subTest(name=name):
                    write_receipt(receipt, target)
                    if override:
                        payload = json.loads(receipt.read_text(encoding="utf-8"))
                        payload.update(override)
                        receipt.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(verifier.TargetReceiptError):
                        verifier.read_target_receipt(
                            receipt,
                            mount_info_provider=lambda _path, value=current: value,
                            lstat_func=lstat_func,
                            fstat_func=receipt_stat(),
                        )

            write_receipt(
                receipt,
                target,
                nonce="current-attempt-" + ("d" * 40),
                attempt_nonce="stale-attempt-" + ("e" * 40),
            )
            with self.assertRaisesRegex(verifier.TargetReceiptError, "previous mount attempt"):
                verifier.read_target_receipt(
                    receipt,
                    mount_info_provider=lambda _path: expected,
                    lstat_func=receipt_stat(),
                    fstat_func=receipt_stat(),
                )

            write_receipt(receipt, target, create_attempt=False)
            receipt.with_name("target-receipt-attempt.json").unlink(missing_ok=True)
            with self.assertRaisesRegex(verifier.TargetReceiptError, "target receipt attempt"):
                verifier.read_target_receipt(
                    receipt,
                    mount_info_provider=lambda _path: expected,
                    lstat_func=receipt_stat(),
                    fstat_func=receipt_stat(),
                )

    def test_receipt_directory_os_errors_are_normalized_to_target_receipt_error(self):
        verifier = load_verifier()
        receipt = pathlib.Path("/run/ming-installer/target-receipt.json")
        private_dir = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        broad_dir = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        with self.subTest(operation="mkdir"), mock.patch.object(
            verifier.Path, "mkdir", side_effect=OSError("mkdir failed")
        ):
            with self.assertRaisesRegex(verifier.TargetReceiptError, "receipt directory"):
                verifier._ensure_receipt_directory(receipt)

        with self.subTest(operation="first-lstat"), mock.patch.object(
            verifier.Path, "mkdir"
        ), mock.patch.object(verifier.os, "lstat", side_effect=OSError("lstat failed")):
            with self.assertRaisesRegex(verifier.TargetReceiptError, "receipt directory"):
                verifier._ensure_receipt_directory(receipt)

        with self.subTest(operation="chmod"), mock.patch.object(
            verifier.Path, "mkdir"
        ), mock.patch.object(verifier.os, "lstat", return_value=broad_dir), mock.patch.object(
            verifier.os, "chmod", side_effect=OSError("chmod failed")
        ):
            with self.assertRaisesRegex(verifier.TargetReceiptError, "receipt directory"):
                verifier._ensure_receipt_directory(receipt)

        with self.subTest(operation="second-lstat"), mock.patch.object(
            verifier.Path, "mkdir"
        ), mock.patch.object(
            verifier.os, "lstat", side_effect=(broad_dir, OSError("second lstat failed"))
        ), mock.patch.object(verifier.os, "chmod"):
            with self.assertRaisesRegex(verifier.TargetReceiptError, "receipt directory"):
                verifier._ensure_receipt_directory(receipt)

        self.assertEqual(0o700, stat.S_IMODE(private_dir.st_mode))

    def test_receipt_bound_installed_gate_rejects_fstab_and_grub_uuid_drift(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "calamares-root-authoritative"
            create_installed_root(target)
            receipt = root / "run/ming-installer/target-receipt.json"
            expected = write_receipt(receipt, target)

            write(target, "etc/fstab", "UUID=stale-root / ext4 defaults 0 1\n")
            result = verifier.verify_installed_from_receipt(
                receipt,
                mount_info_provider=lambda _path: expected,
                lstat_func=receipt_stat(),
                fstat_func=receipt_stat(),
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("authoritative root UUID" in item for item in result["errors"]))

            write(target, "etc/fstab", f"UUID={expected['uuid']} / ext4 defaults 0 1\n")
            write(
                target,
                "etc/grub.d/09_ming_os",
                "linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro\n",
                executable=True,
            )
            result = verifier.verify_installed_from_receipt(
                receipt,
                mount_info_provider=lambda _path: expected,
                lstat_func=receipt_stat(),
                fstat_func=receipt_stat(),
            )
            self.assertFalse(result["ok"])
        self.assertTrue(any("GRUB template" in item for item in result["errors"]))

    @unittest.skipIf(os.name == "nt", "real symlink/FIFO boundary coverage runs on POSIX")
    def test_installed_gate_rejects_target_boundary_escape_paths(self):
        verifier = load_verifier()
        cases = ["absolute-etc-symlink", "relative-etc-symlink"]
        if hasattr(os, "mkfifo"):
            cases.append("fstab-fifo")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                target = root / "target"
                create_installed_root(target)
                outside = root / "outside"
                outside.mkdir()
                if case == "absolute-etc-symlink":
                    shutil.rmtree(target / "etc")
                    os.symlink(str(outside), target / "etc", target_is_directory=True)
                elif case == "relative-etc-symlink":
                    shutil.rmtree(target / "etc")
                    os.symlink("../outside", target / "etc", target_is_directory=True)
                else:
                    (target / "etc/fstab").unlink()
                    os.mkfifo(target / "etc/fstab")

                result = verifier.verify_installed(target)

                self.assertFalse(result["ok"], result)
                self.assertTrue(
                    any("unsafe target path" in error.lower() for error in result["errors"]),
                    result["errors"],
                )

    @unittest.skipIf(os.name == "nt", "real symlink boundary coverage runs on POSIX")
    def test_identity_fstab_rewrite_rejects_symlink_escape_without_modifying_outside_file(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "target"
            create_installed_root(target)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "fstab"
            sentinel.write_text("DO-NOT-TOUCH\n", encoding="utf-8")
            shutil.rmtree(target / "etc")
            os.symlink(str(outside), target / "etc", target_is_directory=True)

            with self.assertRaises(verifier.TargetReceiptError):
                verifier.ensure_target_root_fstab(
                    target,
                    "790ec0ef-1111-2222-3333-444444444444",
                    "ext4",
                )

            self.assertEqual("DO-NOT-TOUCH\n", sentinel.read_text(encoding="utf-8"))

    def test_target_boundary_metadata_rejects_fifo_without_opening_it(self):
        verifier = load_verifier()
        fifo_stat = types.SimpleNamespace(st_mode=stat.S_IFIFO | 0o600)

        with self.assertRaisesRegex(verifier.TargetReceiptError, "unsafe target path"):
            verifier._validate_target_entry_metadata("etc/fstab", fifo_stat, expect_directory=False)

    def test_all_generated_settings_use_receipt_order_and_gate(self):
        base = BASE_MODULE.read_text(encoding="utf-8")
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        settings_blocks = (
            base.split("cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'", 1)[1]
            .split("CALAMARESSETTINGS", 1)[0],
            desktop.split("cat > /etc/calamares/settings.conf <<'SETTINGS'", 1)[1]
            .split("SETTINGS", 1)[0],
            desktop.split("cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'", 1)[1]
            .split("STATICCALASETTINGS", 1)[0],
        )
        ordered = (
            "partition",
            "shellprocess@ming-installer-target-receipt-reset",
            "mount",
            "ming-installer-target-receipt@ming-installer-target-receipt",
            "unpackfs",
            "shellprocess@ming-identity",
            "shellprocess@ming-installed-desktop-gate",
            "shellprocess@ming-bootloader",
        )
        for settings in settings_blocks:
            positions = [settings.index(value) for value in ordered]
            self.assertEqual(sorted(positions), positions)

    def test_generated_receipt_job_executes_globalstorage_success_and_error_contracts(self):
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        job_source = desktop.split(
            "cat > \"${receipt_module}/main.py\" << 'TARGETRECEIPTPY'\n", 1
        )[1].split("\nTARGETRECEIPTPY", 1)[0]
        root_mount_point = "/run/calamares/root-authoritative"
        with tempfile.TemporaryDirectory() as directory:
            module_dir = pathlib.Path(directory)
            capture_log = module_dir / "capture.log"
            verifier_stub = write(
                module_dir,
                "ming-installer-verify",
                "import os\n"
                "import pathlib\n"
                "class TargetReceiptError(RuntimeError):\n"
                "    pass\n"
                "def capture_target_receipt(value):\n"
                "    pathlib.Path(os.environ['CAPTURE_LOG']).write_text(value, encoding='utf-8')\n"
                "    if os.environ.get('STUB_FAIL') == '1':\n"
                "        raise TargetReceiptError('stub target rejected')\n",
            )
            write(
                module_dir,
                "libcalamares.py",
                "import os\n"
                "class Storage:\n"
                "    def value(self, key):\n"
                "        if key != 'rootMountPoint':\n"
                "            raise AssertionError(key)\n"
                "        return os.environ['ROOT_MOUNT_POINT']\n"
                "globalstorage = Storage()\n",
            )
            job_path = write(
                module_dir,
                "main.py",
                job_source.replace(
                    'pathlib.Path("/usr/local/sbin/ming-installer-verify")',
                    f'pathlib.Path({str(verifier_stub)!r})',
                ),
            )
            probe = "\n".join(
                (
                    "import importlib.util, pathlib, sys",
                    "path = pathlib.Path(sys.argv[1])",
                    "sys.path.insert(0, str(path.parent))",
                    "spec = importlib.util.spec_from_file_location('receipt_job_probe', path)",
                    "module = importlib.util.module_from_spec(spec)",
                    "spec.loader.exec_module(module)",
                    "print(repr(module.run()))",
                )
            )
            environment = {
                **os.environ,
                "CAPTURE_LOG": str(capture_log),
                "ROOT_MOUNT_POINT": root_mount_point,
            }
            success = subprocess.run(
                [sys.executable, "-c", probe, str(job_path)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            failure = subprocess.run(
                [sys.executable, "-c", probe, str(job_path)],
                env={**environment, "STUB_FAIL": "1"},
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(0, success.returncode, success.stderr)
            self.assertEqual("None", success.stdout.strip())
            self.assertEqual(0, failure.returncode, failure.stderr)
            self.assertEqual(
                "('Ming installer target receipt failed', 'stub target rejected')",
                failure.stdout.strip(),
            )
            self.assertEqual(root_mount_point, capture_log.read_text(encoding="utf-8"))

    def test_identity_and_bootloader_only_trust_receipt(self):
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1].split(
            "\nMINGIDENTITY", 1
        )[0]
        bootloader = base.split("cat > /usr/local/sbin/ming-install-bootloader", 1)[1].split(
            "\nMINGBOOTLOADER", 1
        )[0]
        self.assertIn("ming-installer-verify receipt --field target", identity)
        self.assertIn("ming-installer-verify receipt --field uuid", identity)
        self.assertNotIn("calamares-root-*", identity)
        self.assertNotIn("candidate in", identity)
        self.assertIn("ming-installer-verify installed --receipt", bootloader)
        self.assertIn("ming-installer-verify receipt --field source", bootloader)
        self.assertNotIn("calamares-root-*", bootloader)

    def test_identity_fstab_rewrite_is_atomic_and_preserves_non_root_entries(self):
        verifier = load_verifier()
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1].split(
            "\nMINGIDENTITY", 1
        )[0]
        self.assertIn("ming-installer-verify boundary --target", identity)
        self.assertIn("ming-installer-verify fstab --target", identity)
        original = (
            "# retained comment\n"
            "UUID=790ec0ef-1111-2222-3333-444444444444 / ext4 defaults 0 1\n"
            "UUID=stale / ext4 defaults 0 1\n"
            "UUID=home /home ext4 defaults 0 2\n"
            "UUID=efi /boot/efi vfat umask=0077 0 1\n"
            "/srv/data /mnt/data none bind 0 0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "target"
            write(target, "etc/fstab", original)
            create_installed_root(target)
            write(target, "etc/fstab", original)
            verifier.ensure_target_root_fstab(
                target,
                "790ec0ef-1111-2222-3333-444444444444",
                "ext4",
            )
            rewritten = (target / "etc/fstab").read_text(encoding="utf-8")

        self.assertIn("# retained comment", rewritten)
        self.assertIn("UUID=home /home", rewritten)
        self.assertIn("UUID=efi /boot/efi", rewritten)
        self.assertIn("/srv/data /mnt/data", rewritten)
        self.assertNotIn("UUID=stale /", rewritten)
        self.assertEqual(1, sum(line.split()[1:2] == ["/"] for line in rewritten.splitlines()))

    def test_final_grub_rejects_any_ming_stanza_with_a_wrong_root_uuid(self):
        shell = shell_executable()
        self.assertIsNotNone(shell)
        base = BASE_MODULE.read_text(encoding="utf-8")
        bootloader = base.split("cat > /usr/local/sbin/ming-install-bootloader", 1)[1].split(
            "\nMINGBOOTLOADER", 1
        )[0]
        helper_start = bootloader.index("validate_final_grub_root_uuid()")
        helper = bootloader[
            helper_start : bootloader.index(
                '\nif [ ! -s "${root}/boot/grub/grub.cfg"', helper_start
            )
        ]
        expected_uuid = "790ec0ef-1111-2222-3333-444444444444"

        def validate(grub_cfg):
            script = "\n".join(
                (
                    "set -uo pipefail",
                    f"root_uuid={shlex.quote(expected_uuid)}",
                    helper,
                    f"validate_final_grub_root_uuid {shlex.quote(grub_cfg.as_posix())}",
                )
            )
            return subprocess.run(
                [shell, "-c", script], capture_output=True, text=True, timeout=15
            )

        with tempfile.TemporaryDirectory() as directory:
            grub_cfg = pathlib.Path(directory) / "grub.cfg"
            valid_lines = (
                f"linux /vmlinuz root=UUID={expected_uuid} ro\n"
                f"linux /boot/vmlinuz-6.12.0-ming root=UUID={expected_uuid} ro\n"
            )
            grub_cfg.write_text(valid_lines, encoding="utf-8")
            valid = validate(grub_cfg)
            self.assertEqual(0, valid.returncode, valid.stderr)

            invalid_cases = {
                "wrong": "linux /boot/vmlinuz-6.12.0-ming root=UUID=stale-root ro\n",
                "missing": "linux /boot/vmlinuz-6.12.0-ming ro quiet\n",
                "duplicate": (
                    f"linux /boot/vmlinuz-6.12.0-ming root=UUID={expected_uuid} "
                    "root=UUID=stale-root ro\n"
                ),
            }
            for name, invalid_line in invalid_cases.items():
                with self.subTest(name=name):
                    grub_cfg.write_text(
                        f"linux /vmlinuz root=UUID={expected_uuid} ro\n" + invalid_line,
                        encoding="utf-8",
                    )
                    rejected = validate(grub_cfg)
                    self.assertNotEqual(0, rejected.returncode, rejected.stderr)

    def test_build_gate_deploys_and_checks_receipt_paths(self):
        build = BUILD.read_text(encoding="utf-8")
        desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        self.assertIn("ming-installer-verify.py", build)
        self.assertIn("usr/local/sbin/ming-installer-verify", build)
        self.assertIn("settings.conf missing ming-installer-target-receipt instance", build)
        self.assertIn("fresh receipt reset must run before mount", build)
        self.assertIn("ming-installer-target-receipt", desktop)
        self.assertIn('globalstorage.value("rootMountPoint")', desktop)

    def test_fresh_erase_install_uses_native_ota_ready_partition_layout(self):
        for source in (BASE_MODULE.read_text(encoding="utf-8"),
                       DESKTOP_MODULE.read_text(encoding="utf-8")):
            self.assertIn("partitionLayout:", source)
            for label in ("MING-BOOT", "MING-ROOT-A", "MING-ROOT-B", "MING-HOME"):
                self.assertIn('name: "%s"' % label, source)
            self.assertIn('mountPoint: "/boot"', source)
            self.assertIn('mountPoint: "/"', source)
            self.assertIn('mountPoint: "/home"', source)
            root_b = source.split('name: "MING-ROOT-B"', 1)[1].split("- name:", 1)[0]
            self.assertNotIn("mountPoint:", root_b)
            self.assertIn("requiredStorage: 48", source)
            self.assertIn("initialPartitioningChoice: none", source)

    def test_installed_identity_writes_fail_closed_ab_layout_and_grub_entries(self):
        base = BASE_MODULE.read_text(encoding="utf-8")
        identity = base.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1].split(
            "\nMINGIDENTITY", 1)[0]
        for marker in (
            "MING-BOOT", "MING-ROOT-A", "MING-ROOT-B", "MING-HOME",
            "/etc/ming-update/slots.json", "Ming OS slot A", "Ming OS slot B",
            "/etc/ming-update/ota-ready", "ming-ota-slot",
        ):
            self.assertIn(marker, identity)
        self.assertIn("sort -u | wc -l", identity)
        self.assertIn("physical_disk_for_device", identity)
        self.assertIn('"${candidate_disk}" == "${target_disk}"', identity)
        self.assertIn("chmod 0600", identity)
        self.assertIn("__MING_BOOT_UUID__", identity)
        self.assertIn("search --no-floppy --fs-uuid --set=root __MING_BOOT_UUID__", identity)
        self.assertIn("linux /ming-slots/A/vmlinuz root=UUID=__MING_ROOT_A_UUID__", identity)
        self.assertIn("linux /ming-slots/B/vmlinuz root=UUID=__MING_ROOT_B_UUID__", identity)
        self.assertIn("/boot/ming-slots/A", identity)
        self.assertNotIn('"${target}/boot/ming-slots/B"', identity)
        self.assertIn("insmod ext2", identity)
        self.assertIn("inactive slot B is intentionally not bootable", identity)


if __name__ == "__main__":
    unittest.main()
