import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODE_PATH = ROOT / "assets" / "ming-install-mode.py"
VERIFY_PATH = ROOT / "assets" / "ming-installer-verify.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
OTA = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")


def load_mode():
    spec = importlib.util.spec_from_file_location("ming_install_mode", MODE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_verifier():
    spec = importlib.util.spec_from_file_location("ming_installer_verify_modes", VERIFY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_installed_desktop(root, uuid):
    def write(relative, content="", executable=False):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(path.stat().st_mode | 0o111)
        return path

    write("etc/fstab", f"UUID={uuid} / ext4 defaults 0 1\n")
    write("etc/systemd/system/default.target", "/lib/systemd/system/graphical.target\n")
    write("etc/systemd/system/display-manager.service", "/lib/systemd/system/lightdm.service\n")
    write("etc/lightdm/lightdm.conf.d/60-ming-autologin.conf", "autologin-session=xfce\n")
    for relative in (
        "usr/sbin/lightdm",
        "usr/bin/startxfce4",
        "usr/bin/xfce4-session",
        "usr/local/bin/ming-phone-desktop",
        "usr/local/bin/ming-session-healthcheck",
    ):
        write(relative, "#!/bin/sh\nexit 0\n", executable=True)
    write("usr/share/xsessions/xfce.desktop", "[Desktop Entry]\nName=Xfce\n")
    write(
        "home/user/.config/autostart/ming-session-healthcheck.desktop",
        "[Desktop Entry]\nExec=/usr/local/bin/ming-session-healthcheck --session\n",
    )
    write(
        "etc/grub.d/09_ming_os",
        "#!/bin/sh\nmenuentry 'Ming OS' {\n"
        f"    linux /vmlinuz root=UUID={uuid} ro\n"
        "}\n",
        executable=True,
    )
    write("boot/grub/grub.cfg", f"linux /vmlinuz root=UUID={uuid} ro\n")


class InstallerModeTests(unittest.TestCase):
    def test_blank_ab_payload_declares_ota_ready_layout(self):
        mode = load_mode()
        payload = mode.build_mode_payload("blank_ab")
        self.assertEqual("blank_ab", payload["mode"])
        self.assertEqual("ab_slot", payload["major_ota"])
        self.assertIn("A/B", payload["message"])
        partition = mode.partition_config("blank_ab")
        self.assertIn('name: "MING-ROOT-A"', partition)
        self.assertIn('name: "MING-ROOT-B"', partition)
        self.assertIn("requiredStorage: 48", partition)
        self.assertIn("allowManualPartitioning: false", partition)

    def test_dual_boot_payload_disables_major_ota_and_requires_manual_partitioning(self):
        mode = load_mode()
        payload = mode.build_mode_payload("dual_boot_preserve")
        self.assertEqual("dual_boot_preserve", payload["mode"])
        self.assertEqual("disabled_dual_boot", payload["major_ota"])
        self.assertIn("patch/minor", payload["message"])
        partition = mode.partition_config("dual_boot_preserve")
        self.assertIn("allowManualPartitioning: true", partition)
        self.assertNotIn('name: "MING-ROOT-B"', partition)

    def test_mode_file_is_atomic_and_rejects_unknown_modes(self):
        mode = load_mode()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "install-mode.json"
            mode.write_mode(path, "dual_boot_preserve")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("dual_boot_preserve", payload["mode"])
            with self.assertRaises(ValueError):
                mode.build_mode_payload("unsafe")

    def test_cli_accepts_explicit_mode_option_used_by_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory) / "install-mode.json"
            partition = pathlib.Path(directory) / "partition.conf"
            result = subprocess.run(
                [
                    "python", str(MODE_PATH), "write", "--mode", "blank_ab",
                    "--state", str(state), "--partition", str(partition),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("blank_ab", json.loads(state.read_text(encoding="utf-8"))["mode"])

    def test_launcher_requires_a_mode_before_starting_calamares(self):
        self.assertIn("ming-install-mode", DESKTOP)
        launcher = DESKTOP.split(
            "cat > /usr/local/bin/ming-calamares-launcher << 'CALAMARESLAUNCHER'", 1
        )[1].split("\nCALAMARESLAUNCHER", 1)[0]
        self.assertIn("install-mode.json", launcher)
        self.assertIn("选择安装模式", launcher)
        self.assertLess(launcher.index("install-mode.json"), launcher.index("calamares -d"))

    def test_identity_branches_and_marks_dual_boot_install(self):
        identity = BASE.split(
            "cat > /usr/local/sbin/ming-fix-installed-identity", 1
        )[1].split("\nMINGIDENTITY", 1)[0]
        self.assertIn("dual_boot_preserve", identity)
        self.assertIn("install-mode.json", identity)
        self.assertIn("disabled_dual_boot", identity)
        self.assertIn("slots.json", identity)

    def test_major_ota_is_blocked_for_dual_boot_mode(self):
        self.assertIn("disabled_dual_boot", OTA)
        self.assertIn("保留双系统模式，大版本 A/B OTA 已禁用", OTA)

    def test_live_verifier_accepts_selected_dual_boot_manual_mode(self):
        mode = load_mode()
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "run/ming-installer/filesystem.squashfs"
            source.parent.mkdir(parents=True)
            source.write_text("rootfs", encoding="utf-8")
            settings = root / "etc/calamares/settings.conf"
            settings.parent.mkdir(parents=True)
            settings.write_text("sequence:\n  - show:\n      - partition\n", encoding="utf-8")
            partition = root / "etc/calamares/modules/partition.conf"
            partition.parent.mkdir(parents=True)
            partition.write_text(mode.partition_config("dual_boot_preserve"), encoding="utf-8")
            unpack = root / "etc/calamares/modules/unpackfs.conf"
            unpack.write_text(
                "source: /run/ming-installer/filesystem.squashfs\n", encoding="utf-8"
            )
            mode.write_mode(root / "run/ming-installer/install-mode.json", "dual_boot_preserve")

            result = verifier.verify_live(root=root, source=source)

        self.assertTrue(result["ok"], result)
        self.assertEqual("dual_boot_preserve", result["install_mode"])
        self.assertEqual("enabled", result["manual_partitioning"])

    def test_installed_dual_boot_mode_requires_no_ab_receipts(self):
        mode = load_mode()
        verifier = load_verifier()
        uuid = "790ec0ef-1111-2222-3333-444444444444"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "target"
            write_installed_desktop(root, uuid)
            mode.write_mode(
                root / "etc/ming-update/install-mode.json", "dual_boot_preserve"
            )

            accepted = verifier.verify_installed(root, expected_root_uuid=uuid)
            (root / "etc/ming-update/slots.json").write_text(
                '{"schema":1,"layout":"ming-ab-v1","slots":{}}\n',
                encoding="utf-8",
            )
            rejected = verifier.verify_installed(root, expected_root_uuid=uuid)

        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual("dual_boot_preserve", accepted["install_mode"])
        self.assertFalse(rejected["ok"], rejected)
        self.assertTrue(any("must not contain A/B" in item for item in rejected["errors"]))

    def test_installed_dual_boot_mode_rejects_a_directory_named_like_ab_state(self):
        mode = load_mode()
        verifier = load_verifier()
        uuid = "790ec0ef-1111-2222-3333-444444444444"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "target"
            write_installed_desktop(root, uuid)
            mode.write_mode(root / "etc/ming-update/install-mode.json", "dual_boot_preserve")
            (root / "etc/ming-update/slots.json").mkdir()
            result = verifier.verify_installed(root, expected_root_uuid=uuid)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("must not contain A/B" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
