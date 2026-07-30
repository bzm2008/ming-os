import pathlib
import os
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
DETECTOR = ROOT / "assets" / "ming-detect-other-os"


class DualBootGrubContracts(unittest.TestCase):
    def test_installed_grub_has_a_conservative_other_os_detector(self):
        self.assertIn("ming-detect-other-os", BASE)
        detector = DETECTOR.read_text(encoding="utf-8")
        self.assertIn("EFI/Microsoft/Boot/bootmgfw.efi", detector)
        self.assertIn("EFI/Linux", detector)
        self.assertIn("chainloader ($esp)", detector)

    def test_installed_grub_keeps_ab_entries_in_one_advanced_submenu(self):
        start = BASE.index('cat > "${target}/etc/grub.d/09_ming_os"')
        section = BASE[start:BASE.index(
            'chmod 0755 "${target}/etc/grub.d/09_ming_os"', start
        )]
        self.assertIn("submenu 'Ming OS 高级启动'", section)
        self.assertIn("menuentry 'Ming OS slot A'", section)
        self.assertIn("menuentry 'Ming OS slot B'", section)
        self.assertEqual(1, len(re.findall(r"^menuentry ", section, re.M)))
        self.assertEqual(1, len(re.findall(r"^submenu ", section, re.M)))

    def test_installed_grub_disables_debian_kernel_fanout(self):
        self.assertIn('"10_linux"', BASE)
        self.assertIn('chmod 0644 "${target}/etc/grub.d/${noisy_grub}"', BASE)

    def test_live_grub_has_at_most_three_top_level_entries(self):
        start = BUILD.index("write_grub_config()")
        section = BUILD[start:BUILD.index("build_iso()", start)]
        top_level = re.findall(r'^menuentry "', section, re.M)
        self.assertLessEqual(len(top_level), 3)
        self.assertIn('submenu "高级兼容启动"', section)

    def test_finalize_keeps_other_os_detector_receipt_outside_user_state(self):
        self.assertIn("ming-detect-other-os", FINALIZE)

    def run_detector(self, root, out, uuid="ESP-1234"):
        env = {
            **dict(os.environ),
            "MING_OTHER_OS_ROOT": str(root),
            "MING_OTHER_OS_OUT": str(out),
            "MING_OTHER_OS_LOG": str(out.with_suffix(".log")),
            "MING_OTHER_OS_UUID": uuid,
        }
        return subprocess.run(
            [sys.executable, str(DETECTOR)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_detector_does_not_create_entry_without_other_os(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "boot/efi/EFI").mkdir(parents=True)
            out = root / "etc/grub.d/11_ming_other_os"
            result = self.run_detector(root, out)
            self.assertEqual(0, result.returncode)
            self.assertEqual("", out.read_text(encoding="utf-8"))

    def test_detector_creates_one_windows_chainloader_for_real_efi_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            loader = root / "boot/efi/EFI/Microsoft/Boot/bootmgfw.efi"
            loader.parent.mkdir(parents=True)
            loader.write_bytes(b"MZ")
            out = root / "etc/grub.d/11_ming_other_os"
            self.assertEqual(0, self.run_detector(root, out).returncode)
            text = out.read_text(encoding="utf-8")
            self.assertEqual(1, text.count("menuentry '启动另一个系统'"))
            self.assertIn("search --no-floppy --fs-uuid --set=esp ESP-1234", text)
            self.assertIn("chainloader ($esp)/EFI/Microsoft/Boot/bootmgfw.efi", text)

    def test_detector_accepts_linux_efi_loader_but_rejects_unknown_or_symlink_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            linux_loader = root / "efi/EFI/Linux/ming-other.efi"
            linux_loader.parent.mkdir(parents=True)
            linux_loader.write_bytes(b"MZ")
            out = root / "etc/grub.d/11_ming_other_os"
            self.assertEqual(0, self.run_detector(root, out).returncode)
            self.assertIn("chainloader ($esp)/EFI/Linux/ming-other.efi", out.read_text(encoding="utf-8"))

            import shutil
            shutil.rmtree(root / "efi")
            unsafe = root / "boot/efi/EFI/Unknown/bootmgfw.efi"
            unsafe.parent.mkdir(parents=True)
            unsafe.write_bytes(b"MZ")
            out.unlink()
            self.assertEqual(0, self.run_detector(root, out).returncode)
            self.assertEqual("", out.read_text(encoding="utf-8"))

    def test_detector_rejects_a_non_fat_esp_when_mount_metadata_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            loader = root / "boot/efi/EFI/Microsoft/Boot/bootmgfw.efi"
            loader.parent.mkdir(parents=True)
            loader.write_bytes(b"MZ")
            out = root / "etc/grub.d/11_ming_other_os"
            env = {
                **dict(os.environ),
                "MING_OTHER_OS_ROOT": str(root),
                "MING_OTHER_OS_OUT": str(out),
                "MING_OTHER_OS_LOG": str(out.with_suffix(".log")),
                "MING_OTHER_OS_UUID": "ESP-1234",
                "MING_OTHER_OS_FSTYPE": "ext4",
            }
            result = subprocess.run(
                [sys.executable, str(DETECTOR)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual("", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
