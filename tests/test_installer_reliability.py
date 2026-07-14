import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "assets" / "ming-installer-verify.py"


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


class InstallerReliabilityContracts(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
