import hashlib
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
VENDOR = ROOT / "assets" / "vendor" / "papyrus"


def function_body(source, name):
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start:end]


class PapyrusIntegrationContracts(unittest.TestCase):
    def test_papyrus_release_assets_are_bundled_with_machine_readable_receipt(self):
        integration = VENDOR / "Papyrus-Debian13-Integration_1.1.0.tar.gz"
        deb = VENDOR / "Papyrus_1.1.0_amd64.deb"
        sig = VENDOR / "Papyrus_1.1.0_amd64.deb.sig"
        sums = VENDOR / "SHA256SUMS"
        receipt = VENDOR / "receipt.json"
        for path in (integration, deb, sig, sums, receipt):
            self.assertTrue(path.is_file(), f"missing Papyrus vendor asset: {path}")
            self.assertGreater(path.stat().st_size, 0, f"empty Papyrus vendor asset: {path}")

        metadata = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual("1.1.0", metadata["version"])
        self.assertEqual("Papyrus_1.1.0_amd64.deb", metadata["deb_asset"])
        self.assertEqual("Papyrus-Debian13-Integration_1.1.0.tar.gz", metadata["integration_asset"])
        self.assertEqual(
            "e6e8a2ae7f023d0f8bfc9d1da2c3d6b279ea0982e74b3f77d4ddb1027b573972",
            hashlib.sha256(deb.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            "c03ea2fed4fb81f54465623a4753ecfdea76343b0bb964abbfd9ab836ad47fe9",
            hashlib.sha256(integration.read_bytes()).hexdigest(),
        )
        self.assertIn(metadata["deb_sha256"], sums.read_text(encoding="utf-8"))

    def test_apps_module_installs_verified_papyrus_into_the_build_rootfs(self):
        installer = function_body(APPS, "install_papyrus")
        self.assertIn("Papyrus-Debian13-Integration_1.1.0.tar.gz", installer)
        self.assertIn("Papyrus_1.1.0_amd64.deb", installer)
        self.assertIn("e6e8a2ae7f023d0f8bfc9d1da2c3d6b279ea0982e74b3f77d4ddb1027b573972", installer)
        self.assertIn("c03ea2fed4fb81f54465623a4753ecfdea76343b0bb964abbfd9ab836ad47fe9", installer)
        self.assertIn("sha256sum -c", installer)
        self.assertIn("dpkg-deb -f", installer)
        self.assertIn('"Package"', installer)
        self.assertIn('"Version"', installer)
        self.assertIn('"Architecture"', installer)
        self.assertIn("tar -xzf", installer)
        self.assertIn("PAPYRUS_ROOTFS=", installer)
        self.assertIn("bash ./install-papyrus.sh", installer)
        self.assertIn("./install-papyrus.sh", installer)
        self.assertIn("/opt/papyrus/launch-papyrus", installer)
        self.assertIn("/usr/share/applications/papyrus.desktop", installer)
        self.assertNotIn("wget", installer)
        self.assertNotIn("curl", installer)

    def test_papyrus_preinstall_is_a_required_apps_phase(self):
        main = function_body(APPS, "main")
        self.assertIn("run_required_step install_papyrus || return 1", main)
        self.assertLess(main.index("install_required_desktop_runtime"), main.index("install_papyrus"))
        self.assertLess(main.index("install_papyrus"), main.index("install_app_store"))

    def test_rootfs_gate_requires_papyrus_runtime_entrypoints(self):
        for marker in (
            "opt/papyrus/launch-papyrus",
            "usr/bin/papyrus",
            "usr/share/applications/papyrus.desktop",
            "usr/share/icons/hicolor/128x128/apps/papyrus.png",
            "Exec=/usr/bin/papyrus",
            "StartupWMClass=uno.scallion.papyrus",
        ):
            self.assertIn(marker, BUILD)
        self.assertNotIn("tmp/ming-build/assets/vendor/papyrus", BUILD)

    def test_rootfs_gate_does_not_follow_absolute_papyrus_symlink_on_the_build_host(self):
        gate = BUILD[
            BUILD.index('papyrus_command = root / "usr/bin/papyrus"'):
            BUILD.index('papyrus_desktop = require_file', BUILD.index('papyrus_command = root / "usr/bin/papyrus"'))
        ]
        self.assertIn("papyrus_command.is_symlink()", gate)
        self.assertIn("os.readlink(papyrus_command)", gate)
        self.assertNotIn("papyrus_command.exists()", gate)

    def test_papyrus_replaces_garlic_as_the_visible_agent_entrypoint(self):
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        finalizer = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
        phone = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
        for source in (desktop, finalizer, phone, BUILD):
            self.assertIn("papyrus.desktop", source)
            self.assertNotIn("garlic-claw.desktop", source)
            self.assertNotIn("openclaw", source.lower())

    def test_papyrus_install_normalizes_permissions_for_desktop_users(self):
        installer = function_body(APPS, "install_papyrus")
        for marker in (
            "chmod -R a+rX /opt/papyrus",
            "chmod 0755 /opt/papyrus /opt/papyrus/launch-papyrus",
            "readlink -f /usr/bin/papyrus",
            "desktop-file-validate /usr/share/applications/papyrus.desktop",
        ):
            self.assertIn(marker, installer)


if __name__ == "__main__":
    unittest.main()
