import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")


class XiahaiIntegrationContracts(unittest.TestCase):
    def test_internal_build_can_explicitly_skip_missing_xiahai_without_weakening_default_gate(self):
        self.assertIn("MING_SKIP_XIAHAI", BUILD)
        self.assertIn("skip_xiahai", APPS)
        self.assertIn('if [[ "${MING_SKIP_XIAHAI}" != "1" ]]', BUILD)
        self.assertIn('os.environ.get("MING_SKIP_XIAHAI") != "1" and "xiahai-xiaoming.dockitem"', BUILD)

    def test_internal_build_reuse_flag_requires_checkpoint_validation(self):
        self.assertIn('MING_REUSE_CHROOT="${MING_REUSE_CHROOT:-0}"', BUILD)
        self.assertIn('"${MING_REUSE_CHROOT}" == "1"', BUILD)
        self.assertIn("resume is checkpoint validated", BUILD)
        self.assertNotIn(
            'if [[ "${MING_REUSE_CHROOT}" == "1" ]]; then\n'
            '        if [[ -f "${CHROOT_DIR}/etc/ming-version" ]]; then',
            BUILD,
        )

    def test_xiahai_vendor_asset_is_present_and_named(self):
        receipt = ROOT / "assets" / "vendor" / "xiahai-xiaoming" / "receipt.json"
        self.assertTrue(receipt.is_file())
        self.assertIn("xiahai-xiaoming_0.0.2-beta_amd64.deb", APPS)

    def test_apps_module_installs_xiahai_and_validates_the_debian_archive(self):
        self.assertIn("install_xiahai_xiaoming()", APPS)
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        for marker in (
            "xiahai-xiaoming_0.0.2-beta_amd64.deb",
            "dpkg-deb --info",
            "dpkg-deb --contents",
            "Package",
            "Version",
            "Architecture",
            "xiahai-xiaoming.desktop",
            "/opt/xiahai-xiaoming/xiahai-xiaoming",
        ):
            self.assertIn(marker, installer)
        self.assertNotIn("Papyrus", installer)

    def test_xiahai_install_is_idempotent_after_apt_configures_it(self):
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("dpkg-query -W -f='${Status}'", installer)
        self.assertIn("install ok installed", installer)
        self.assertIn("did not reach install ok installed", installer)

    def test_xiahai_payload_permissions_are_repaired_for_execution(self):
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("chmod 0755 /opt/xiahai-xiaoming/xiahai-xiaoming", installer)
        self.assertIn("chown root:root /opt/xiahai-xiaoming/chrome-sandbox", installer)
        self.assertIn("chmod 4755 /opt/xiahai-xiaoming/chrome-sandbox", installer)
        self.assertIn("chrome_crashpad_handler", installer)
        self.assertIn("chmod 0755 /opt/xiahai-xiaoming/chrome_crashpad_handler", installer)

    def test_xiahai_desktop_metadata_is_normalized_before_validation(self):
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("sed -i 's/\\r$//' \"${desktop}\"", installer)
        self.assertIn("Categories=Office;", installer)
        self.assertIn("desktop-file-validate \"${desktop}\"", installer)

    def test_build_preflights_xiahai_archive_before_chroot_work(self):
        prepare = BUILD.split("prepare_chroot_scripts() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('dpkg-deb --info "${xiahai_asset}"', prepare)
        self.assertIn('dpkg-deb --contents "${xiahai_asset}"', prepare)
        self.assertIn("corrupt or incomplete", prepare)

    def test_default_desktop_and_dock_use_xiahai_instead_of_papyrus(self):
        for source in (DESKTOP, FINALIZE, BUILD):
            self.assertIn("xiahai-xiaoming.desktop", source)
            self.assertNotIn("papyrus.desktop", source)
        self.assertIn("xiahai-xiaoming", DESKTOP)

    def test_launch_feedback_starts_opaque_for_xrender_compatibility(self):
        self.assertIn("window.set_opacity(1.0)", LAUNCH)
        self.assertNotIn("window.set_opacity(0.0 if animated else 1.0)", LAUNCH)

    def test_xiahai_desktop_marks_brokered_non_desktop_entrypoints_without_recursing(self):
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("X-Ming-Launch-Broker=true", installer)
        self.assertIn("Exec=/opt/xiahai-xiaoming/xiahai-xiaoming", installer)


if __name__ == "__main__":
    unittest.main()
