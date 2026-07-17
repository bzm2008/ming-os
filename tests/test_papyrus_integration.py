import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build_onion_os.sh"
PAPYRUS = ROOT / "modules" / "04_papyrus.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
RESUME = ROOT / "resume_build.sh"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"


class PapyrusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.papyrus = PAPYRUS.read_text(encoding="utf-8") if PAPYRUS.exists() else ""
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.finalize = FINALIZE.read_text(encoding="utf-8")
        cls.resume = RESUME.read_text(encoding="utf-8")
        cls.phone = PHONE.read_text(encoding="utf-8")

    def test_build_uses_papyrus_module_instead_of_garlic_claw(self):
        self.assertIn('"04_papyrus.sh"', self.build)
        self.assertNotIn('"04_garlic_claw.sh"', self.build)
        self.assertIn('"04_papyrus.sh"', self.resume)
        self.assertNotIn('"04_garlic_claw.sh"', self.resume)
        self.assertIn("papyrus-assets", self.build)

    def test_normal_build_has_no_garlic_or_openclaw_artifacts(self):
        for source in (self.build, self.resume, self.desktop, self.finalize, self.phone):
            self.assertNotRegex(source, r"garlic-claw|openclaw-gateway|/usr/local/bin/openclaw")

    def test_papyrus_module_embeds_stable_desktop_and_launcher(self):
        self.assertIn("/usr/bin/papyrus", self.papyrus)
        self.assertIn("/usr/share/applications/papyrus.desktop", self.papyrus)
        self.assertIn("Exec=/usr/bin/papyrus %U", self.papyrus)
        self.assertRegex(self.papyrus, r"/opt/papyrus")
        self.assertIn("XDG_CONFIG_HOME", self.papyrus)
        self.assertIn("XDG_DATA_HOME", self.papyrus)

    def test_missing_asset_skips_install_without_fake_executable(self):
        self.assertRegex(self.papyrus, r"No Papyrus.*asset|asset.*not found|skip", re.IGNORECASE)
        self.assertIn("PAPYRUS_ASSET", self.papyrus)
        self.assertIn("No Papyrus asset found", self.papyrus)
        self.assertNotIn("curl", self.papyrus)

    def test_asset_validation_requires_exact_package_name_and_install_is_atomic(self):
        self.assertIn("grep -Fxq 'papyrus'", self.papyrus)
        self.assertIn("mv", self.papyrus)
        self.assertIn('mv "${PAPYRUS_ROOT}" "${backup}"', self.papyrus)
        self.assertIn('PAPYRUS_ASSET', self.papyrus)
        self.assertIn('*.deb|*.DEB', self.papyrus)
        self.assertIn('*.AppImage|*.appimage', self.papyrus)
        self.assertIn('rollback_papyrus', self.papyrus)
        self.assertIn('install -d -m 0755 "$(dirname "${PAPYRUS_MARKER}")"', self.papyrus)
        self.assertIn('rollback_papyrus "${backup}" "${artifact_backup}"', self.papyrus)

    def test_optional_ui_entries_are_gated_on_installed_desktop(self):
        self.assertIn("papyrus.dockitem", self.desktop)
        self.assertIn("papyrus.desktop", self.desktop)
        self.assertIn("ming-refresh-dock-launchers", self.papyrus)
        self.assertIn("uca.xml", self.papyrus)
        self.assertIn("papyrus.dockitem", self.desktop)

    def test_phone_desktop_knows_papyrus_without_fallback_generation(self):
        self.assertIn('"papyrus.desktop"', self.phone)
        self.assertNotIn('"papyrus.desktop": (', self.phone)

    def test_papyrus_has_root_owned_launcher_trust_marker(self):
        launch = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")
        self.assertIn("trusted-desktops", launch)
        self.assertIn("papyrus.desktop", self.papyrus)
        self.assertIn("trusted-desktops", self.papyrus)


if __name__ == "__main__":
    unittest.main()
