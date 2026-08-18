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


def extract_heredoc_bytes(path, marker, delimiter):
    data = path.read_bytes()
    start = data.index(marker)
    start = data.index(b"\n", start) + 1
    end = data.index(b"\n" + delimiter, start)
    return data[start:end]


def write_installed_desktop(root, uuid):
    def write(relative, content="", executable=False):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(path.stat().st_mode | 0o111)
        return path

    write("etc/fstab", f"UUID={uuid} / ext4 defaults 0 1\n")
    write(
        "etc/passwd",
        "root:x:0:0:root:/root:/bin/bash\n"
        "user:x:1000:1000:Ming OS User:/home/user:/bin/bash\n",
    )
    write(
        "etc/group",
        "root:x:0:\nuser:x:1000:\nsudo:x:27:user\n",
    )
    write("etc/sudoers", "root ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n")
    write("etc/systemd/system/default.target", "/lib/systemd/system/graphical.target\n")
    write("etc/systemd/system/display-manager.service", "/lib/systemd/system/lightdm.service\n")
    write("etc/lightdm/lightdm.conf.d/60-ming-autologin.conf", "autologin-session=xfce\n")
    for relative in (
        "usr/sbin/lightdm",
        "usr/bin/sudo",
        "usr/bin/pkexec",
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
    def test_calamares_show_sequence_starts_at_partition_not_welcome(self):
        """The native welcome module can stall at "Remaining modules: welcome" in Live VMs."""
        base_settings = BASE.split("cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'", 1)[1].split(
            "CALAMARESSETTINGS", 1
        )[0]
        desktop_settings = DESKTOP.split("cat > /etc/calamares/settings.conf <<'SETTINGS'", 1)[1].split(
            "\nSETTINGS", 1
        )[0]
        static_settings = DESKTOP.split("cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'", 1)[1].split(
            "\nSTATICCALASETTINGS", 1
        )[0]
        for settings in (base_settings, desktop_settings, static_settings):
            show_block = settings.split("- show:", 1)[1].split("- exec:", 1)[0]
            self.assertIn("  - partition", show_block)
            self.assertIn("  - summary", show_block)
            self.assertNotIn("  - welcome", show_block)

    def test_blank_ab_payload_declares_ota_ready_layout(self):
        mode = load_mode()
        payload = mode.build_mode_payload("blank_ab")
        self.assertEqual("blank_ab", payload["mode"])
        self.assertEqual("ab_slot", payload["major_ota"])
        self.assertIn("A/B", payload["message"])
        partition = mode.partition_config("blank_ab")
        self.assertIn("defaultPartitionTableType: gpt", partition)
        self.assertIn("requiredPartitionTableType: gpt", partition)
        self.assertIn('name: "MING-BIOSBOOT"', partition)
        self.assertIn('filesystem: "unformatted"', partition)
        self.assertIn('type: "21686148-6449-6E6F-744E-656564454649"', partition)
        self.assertIn('name: "MING-ESP"', partition)
        self.assertIn('filesystem: "fat32"', partition)
        self.assertIn('type: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"', partition)
        self.assertIn('mountPoint: "/boot/efi"', partition)
        self.assertIn('name: "MING-ROOT-A"', partition)
        self.assertIn('name: "MING-ROOT-B"', partition)
        self.assertIn("requiredStorage: 48", partition)
        self.assertIn("allowManualPartitioning: false", partition)
        self.assertLess(partition.index('name: "MING-BIOSBOOT"'), partition.index('name: "MING-ESP"'))
        self.assertLess(partition.index('name: "MING-ESP"'), partition.index('name: "MING-BOOT"'))

    def test_blank_ab_bios_uses_explicit_ming_esp_without_auto_esp_helper(self):
        mode = load_mode()
        partition = mode.partition_config("blank_ab", firmware="bios")
        self.assertNotIn("efiSystemPartition", partition)
        self.assertIn('mountPoint: "/boot/efi"', partition)
        self.assertIn('name: "MING-ESP"', partition)

        for source in (BASE, DESKTOP):
            with self.subTest(source="base" if source is BASE else "desktop"):
                tail = source.split(
                    "cat > /etc/calamares/modules/partition.conf << '",
                    1,
                )[1]
                delimiter = tail.split("'", 1)[0]
                template = tail.split("\n", 1)[1].split("\n" + delimiter, 1)[0]
                self.assertNotIn("efiSystemPartition", template)
                self.assertIn('mountPoint: "/boot/efi"', template)
                self.assertIn('name: "MING-ESP"', template)

    def test_blank_ab_uefi_uses_explicit_ming_esp_layout(self):
        mode = load_mode()
        partition = mode.partition_config("blank_ab", firmware="uefi")
        self.assertNotIn("efi:", partition)
        self.assertNotIn("efiSystemPartition", partition)
        self.assertIn('name: "MING-ESP"', partition)
        self.assertIn('mountPoint: "/boot/efi"', partition)
        self.assertIn('filesystem: "fat32"', partition)
        self.assertIn('type: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"', partition)
        self.assertLess(partition.index('name: "MING-BIOSBOOT"'), partition.index('name: "MING-ESP"'))
        self.assertLess(partition.index('name: "MING-ESP"'), partition.index('name: "MING-BOOT"'))

    def test_partition_type_normalizer_requires_explicit_ming_esp(self):
        normalizer = BASE.split(
            "cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'", 1
        )[1].split("\nMINGFIXPARTTYPES", 1)[0]
        self.assertIn("MING-ESP:ef00", normalizer)
        self.assertIn("missing partition label ${label}", normalizer)
        self.assertNotIn("claim_auto_esp_as_ming_esp", normalizer)
        self.assertNotIn("find_auto_esp_partition", normalizer)
        self.assertNotIn("sgdisk --change-name=", normalizer)

    def test_partition_type_normalizer_is_lf_only(self):
        normalizer = extract_heredoc_bytes(
            ROOT / "modules" / "01_base.sh",
            b"cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'",
            b"MINGFIXPARTTYPES",
        )
        self.assertNotIn(
            b"\r",
            normalizer,
            "CRLF in ming-fix-partition-types breaks Calamares shellprocess with $'\\r'",
        )

    def test_blank_ab_templates_normalize_real_partition_types_after_partitioning(self):
        for source in (BASE, DESKTOP):
            with self.subTest(source="base" if source is BASE else "desktop"):
                self.assertIn("ming-fix-partition-types", source)
                self.assertIn("MING-BIOSBOOT:ef02", source)
                self.assertIn("MING-ESP:ef00", source)
                self.assertIn("MING-BOOT:8300", source)
                self.assertIn("MING-ROOT-A:8300", source)
                self.assertIn("MING-ROOT-B:8300", source)
                self.assertIn("MING-HOME:8300", source)
                settings = source.split("cat > /etc/calamares/settings.conf", 1)[1]
                self.assertIn("shellprocess@ming-fix-partition-types", settings)
                self.assertLess(settings.index("  - partition"), settings.index("  - shellprocess@ming-fix-partition-types"))
                self.assertLess(settings.index("  - shellprocess@ming-fix-partition-types"), settings.index("  - shellprocess@ming-installer-target-receipt-reset"))
                self.assertLess(settings.index("  - shellprocess@ming-fix-partition-types"), settings.index("  - mount"))

    def test_partition_type_normalizer_waits_for_stable_partlabel_devices(self):
        normalizer = BASE.split(
            "cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'", 1
        )[1].split("\nMINGFIXPARTTYPES", 1)[0]
        self.assertIn("settle_partitions()", normalizer)
        self.assertIn("wait_for_part_label()", normalizer)
        self.assertIn("/dev/disk/by-partlabel", normalizer)
        self.assertIn("readlink -f", normalizer)
        self.assertIn("lsblk_snapshot", normalizer)
        self.assertNotIn('$2 == \"part\" && $3 == label', normalizer)

    def test_partition_type_normalizer_falls_back_to_lsblk_when_udev_link_is_late(self):
        normalizer = BASE.split(
            "cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'", 1
        )[1].split("\nMINGFIXPARTTYPES", 1)[0]
        self.assertIn("lsblk_parts_by_label()", normalizer)
        self.assertIn("lsblk -nrpo NAME,TYPE,PARTLABEL", normalizer)
        self.assertIn('mapfile -t part_matches < <(lsblk_parts_by_label "${label}")', normalizer)
        self.assertIn('[[ -b "${part}" ]]', normalizer)
        self.assertLess(
            normalizer.index('mapfile -t part_matches < <(lsblk_parts_by_label "${label}")'),
            normalizer.index('link="/dev/disk/by-partlabel/${label}"'),
        )
        self.assertIn('((${#part_matches[@]} > 1))', normalizer)
        self.assertIn("duplicate partition label", normalizer)
        self.assertIn("Ming OS 安装分区检查失败", normalizer)
        self.assertIn("详细日志：%s", normalizer)

    def test_partition_type_normalizer_retries_final_parttype_after_kernel_reread(self):
        normalizer = BASE.split(
            "cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'", 1
        )[1].split("\nMINGFIXPARTTYPES", 1)[0]
        self.assertIn("wait_for_part_type()", normalizer)
        self.assertIn("sgdisk --typecode", normalizer)
        self.assertIn("partprobe", normalizer)
        self.assertLess(
            normalizer.index('set_part_type "${disk}" "${number}"'),
            normalizer.index('wait_for_part_type "${part}" "${guid}"'),
        )
        self.assertIn("udevadm settle", normalizer)

    def test_generated_blank_ab_templates_create_a_real_fat32_esp(self):
        for source in (BASE, DESKTOP):
            with self.subTest(source="base" if source is BASE else "desktop"):
                tail = source.split(
                    "cat > /etc/calamares/modules/partition.conf << '",
                    1,
                )[1]
                delimiter = tail.split("'", 1)[0]
                template = tail.split("\n", 1)[1].split("\n" + delimiter, 1)[0]
                self.assertIn("defaultPartitionTableType: gpt", template)
                self.assertIn("requiredPartitionTableType: gpt", template)
                self.assertIn('name: "MING-BIOSBOOT"', template)
                self.assertIn('filesystem: "unformatted"', template)
                self.assertIn('type: "21686148-6449-6E6F-744E-656564454649"', template)
                self.assertIn('name: "MING-ESP"', template)
                self.assertIn('filesystem: "fat32"', template)
                self.assertIn('type: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"', template)
                self.assertIn('mountPoint: "/boot/efi"', template)
                self.assertLess(template.index('name: "MING-BIOSBOOT"'), template.index('name: "MING-ESP"'))
                self.assertLess(template.index('name: "MING-ESP"'), template.index('name: "MING-BOOT"'))

    def test_blank_ab_payload_defaults_to_erase_disk_flow(self):
        mode = load_mode()
        partition = mode.partition_config("blank_ab")
        self.assertIn("initialPartitioningChoice: erase", partition)
        self.assertNotIn("initialPartitioningChoice: none", partition)

    def test_chroot_apt_network_has_bounded_retries(self):
        self.assertIn('Acquire::Retries "5";', BASE)
        self.assertIn('Acquire::ForceIPv4 "true";', BASE)
        self.assertIn('Acquire::http::Timeout "15";', BASE)
        self.assertIn('Acquire::https::Timeout "15";', BASE)
        self.assertIn('Acquire::Queue-Mode "access";', BASE)

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
        self.assertIn("ming-install-mode-chooser", DESKTOP)
        launcher = DESKTOP.split(
            "cat > /usr/local/bin/ming-calamares-launcher << 'CALAMARESLAUNCHER'", 1
        )[1].split("\nCALAMARESLAUNCHER", 1)[0]
        self.assertIn("选择安装模式", launcher)
        self.assertIn("/usr/local/bin/ming-install-mode-chooser", launcher)
        self.assertIn("ming-live-installer-root", launcher)
        helper = DESKTOP.split(
            "cat > /usr/local/sbin/ming-live-installer-root << 'LIVEINSTALLERROOT'", 1
        )[1].split("\nLIVEINSTALLERROOT", 1)[0]
        self.assertIn("install-mode.json", helper)
        self.assertLess(helper.index("ming-install-mode write"), helper.index("calamares -d"))
        self.assertIn("calamares -d -c /etc/calamares", helper)
        self.assertIn("settings.conf", helper)
        self.assertIn("/usr/share/calamares/qml", helper)
        self.assertIn("/etc/calamares/qml", helper)

    def test_launcher_uses_keyboard_accessible_mode_chooser(self):
        launcher = DESKTOP.split(
            "cat > /usr/local/bin/ming-calamares-launcher << 'CALAMARESLAUNCHER'", 1
        )[1].split("\nCALAMARESLAUNCHER", 1)[0]
        chooser = DESKTOP.split(
            "cat > /usr/local/bin/ming-install-mode-chooser << 'INSTALLMODECHOOSER'", 1
        )[1].split("\nINSTALLMODECHOOSER", 1)[0]
        self.assertNotIn("--radiolist", launcher)
        self.assertNotIn("zenity_status", launcher)
        self.assertIn('choice="$(/usr/local/bin/ming-install-mode-chooser', launcher)
        for marker in (
            "gi.require_version('Gtk', '3.0')",
            "gi.require_version('Gdk', '3.0')",
            "self.blank_button.grab_focus()",
            "connect('key-press-event'",
            "Gdk.KEY_Return",
            "Gdk.KEY_space",
            "Gtk.ResponseType.OK",
            "print(dialog.selected_mode)",
        ):
            self.assertIn(marker, chooser)
        cancelled = launcher[
            launcher.index('if ! choice="$(/usr/local/bin/ming-install-mode-chooser'):
            launcher.index('selected_mode="${choice}"')
        ]
        self.assertIn("return 1", cancelled)
        self.assertIn("未选择安装方式", cancelled)

    def test_install_mode_chooser_realizes_buttons_before_focus_and_handles_both_modes(self):
        chooser = DESKTOP.split(
            "cat > /usr/local/bin/ming-install-mode-chooser << 'INSTALLMODECHOOSER'", 1
        )[1].split("\nINSTALLMODECHOOSER", 1)[0]
        self.assertLess(
            chooser.index("self.show_all()"),
            chooser.index("self.blank_button.grab_focus()"),
        )
        self.assertIn("self.blank_button = self.mode_button(", chooser)
        self.assertIn("self.dual_button = self.mode_button(", chooser)
        mode_button = chooser.split("    def mode_button", 1)[1]
        self.assertIn("button.set_can_default(True)", mode_button)
        self.assertIn("button.set_receives_default(True)", mode_button)
        self.assertIn("button.connect(\n            'key-press-event'", mode_button)
        self.assertIn("self.choose(mode)", chooser)

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

    def test_live_verifier_accepts_blank_ab_uefi_explicit_esp_layout(self):
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
            partition.write_text(
                mode.partition_config("blank_ab", firmware="uefi"), encoding="utf-8"
            )
            unpack = root / "etc/calamares/modules/unpackfs.conf"
            unpack.write_text(
                "source: /run/ming-installer/filesystem.squashfs\n", encoding="utf-8"
            )
            mode.write_mode(root / "run/ming-installer/install-mode.json", "blank_ab")

            result = verifier.verify_live(root=root, source=source)

        self.assertTrue(result["ok"], result)

    def test_live_verifier_rejects_blank_ab_uefi_auto_esp_helper(self):
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
            auto_esp_partition = (
                mode.BLANK_AB_COMMON
                + """efi:
  mountPoint: "/boot/efi"
  recommendedSize: 512M
  minimumSize: 300M
  label: "MING-ESP"
partitionLayout:
  - name: "MING-BIOSBOOT"
    filesystem: "unformatted"
    noEncrypt: true
    type: "21686148-6449-6E6F-744E-656564454649"
    size: 8M
    minSize: 8M
"""
                + mode.BLANK_AB_LAYOUT_TAIL
            )
            partition.write_text(auto_esp_partition, encoding="utf-8")
            unpack = root / "etc/calamares/modules/unpackfs.conf"
            unpack.write_text(
                "source: /run/ming-installer/filesystem.squashfs\n", encoding="utf-8"
            )
            mode.write_mode(root / "run/ming-installer/install-mode.json", "blank_ab")

            result = verifier.verify_live(root=root, source=source)

        self.assertFalse(result["ok"], result)
        self.assertTrue(any("explicit MING-ESP" in error for error in result["errors"]), result)

    def test_live_verifier_rejects_blank_ab_partition_attribute_drift(self):
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
            malformed = mode.partition_config("blank_ab").replace(
                'name: "MING-BOOT"\n    filesystem: "ext4"\n    noEncrypt: true\n    mountPoint: "/boot"',
                'name: "MING-BOOT"\n    filesystem: "fat32"\n    noEncrypt: true\n    mountPoint: "/wrong-boot"',
            )
            partition.write_text(malformed, encoding="utf-8")
            unpack = root / "etc/calamares/modules/unpackfs.conf"
            unpack.write_text(
                "source: /run/ming-installer/filesystem.squashfs\n", encoding="utf-8"
            )
            mode.write_mode(root / "run/ming-installer/install-mode.json", "blank_ab")

            result = verifier.verify_live(root=root, source=source)

        self.assertFalse(result["ok"], result)
        self.assertTrue(any("MING-BOOT" in error for error in result["errors"]), result)

    def test_live_verifier_rejects_blank_ab_without_gpt_bios_boot_partition(self):
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
            malformed = mode.partition_config("blank_ab")
            malformed = malformed.replace("defaultPartitionTableType: gpt\n", "")
            malformed = malformed.replace("requiredPartitionTableType: gpt\n", "")
            malformed = malformed.replace(
                '  - name: "MING-BIOSBOOT"\n'
                '    filesystem: "unformatted"\n'
                '    noEncrypt: true\n'
                '    type: "21686148-6449-6E6F-744E-656564454649"\n'
                '    size: 8M\n'
                '    minSize: 8M\n',
                "",
            )
            partition.write_text(malformed, encoding="utf-8")
            unpack = root / "etc/calamares/modules/unpackfs.conf"
            unpack.write_text("source: /run/ming-installer/filesystem.squashfs\n", encoding="utf-8")
            mode.write_mode(root / "run/ming-installer/install-mode.json", "blank_ab")

            result = verifier.verify_live(root=root, source=source)

        self.assertFalse(result["ok"], result)
        self.assertTrue(any("GPT" in error or "MING-BIOSBOOT" in error for error in result["errors"]), result)

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
