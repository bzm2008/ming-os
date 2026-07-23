import hashlib
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build_onion_os.sh"
PAPYRUS = ROOT / "modules" / "04_papyrus.sh"
APPS = ROOT / "modules" / "02_apps.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
RESUME = ROOT / "resume_build.sh"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
PAPYRUS_ASSET = ROOT / "assets" / "papyrus-assets" / "Papyrus_1.0.0_amd64.deb"
PAPYRUS_DEB_SHA256 = "993A100E4F88190EAF833BEA3456E38C60322E24A3A553B4935E5B2550C9D368"
PAPYRUS_APPIMAGE_SHA256 = "8B86F8CB1F9E6E39F0A3FEF9E7B36C57EB8700F7899AD4FEBD8344D0D05531B4"


class PapyrusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.papyrus = PAPYRUS.read_text(encoding="utf-8") if PAPYRUS.exists() else ""
        cls.apps = APPS.read_text(encoding="utf-8")
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

    def test_release_asset_is_pinned_and_post_install_refreshes_phone_desktop(self):
        """A release must never silently omit Papyrus or trust an arbitrary payload."""
        self.assertIn(PAPYRUS_DEB_SHA256, self.papyrus)
        self.assertIn(PAPYRUS_APPIMAGE_SHA256, self.papyrus)
        self.assertIn(PAPYRUS_DEB_SHA256, self.build)
        self.assertIn("sha256sum", self.papyrus)
        self.assertIn("ming-phone-desktop --sync", self.papyrus)
        self.assertIn("MING_RELEASE_MODE", self.build)
        self.assertIn("发布构建必须提供已校验的 Papyrus 1.0.0 资产", self.build)

    def test_pinned_deb_asset_matches_the_release_hash(self):
        """The staged binary must be the exact payload the installer verifies."""
        self.assertTrue(PAPYRUS_ASSET.is_file())
        digest = hashlib.sha256(PAPYRUS_ASSET.read_bytes()).hexdigest().upper()
        self.assertEqual(PAPYRUS_DEB_SHA256, digest)

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

    def test_papyrus_payload_is_searchable_and_executable_by_the_desktop_user(self):
        """mktemp creates mode 0700; moving it to /opt must not keep Papyrus inaccessible."""
        self.assertIn('chmod 0755 "${stage}"', self.papyrus)
        self.assertIn('find "${stage}" -type d -exec chmod 0755 {} +', self.papyrus)

    def test_deb_payload_requires_webkit_runtime_and_rejects_missing_linkage(self):
        """A visible Papyrus launcher is not sufficient when its ELF dependencies are absent."""
        packages = self.apps.split("REQUIRED_DESKTOP_RUNTIME_PACKAGES=(", 1)[1].split(")", 1)[0]
        self.assertIn("libwebkit2gtk-4.1-0", packages)
        self.assertIn("verify_papyrus_runtime() {", self.papyrus)
        self.assertIn("ldd", self.papyrus)
        self.assertIn("not found", self.papyrus)
        self.assertIn("verify_papyrus_runtime", self.papyrus[
            self.papyrus.index("install_papyrus_asset() {"):self.papyrus.index("main() {")
        ])

    def test_papyrus_launcher_bounds_only_an_unmaximized_first_x11_window_to_the_workarea(self):
        """The first launch fits small displays without undoing a user's window state."""
        self.assertIn("ming-papyrus-window", self.papyrus)
        self.assertIn("--fit-pid", self.papyrus)
        self.assertIn("wmctrl -lp", self.papyrus)
        self.assertIn("wmctrl -lG", self.papyrus)
        self.assertIn("wmctrl -i -r", self.papyrus)
        self.assertIn("xrandr --current", self.papyrus)
        self.assertIn("preserves_user_window_state", self.papyrus)
        self.assertIn("FULLSCREEN|MAXIMIZED_VERT|MAXIMIZED_HORZ", self.papyrus)
        self.assertIn("(( current_width > width", self.papyrus)
        self.assertNotIn("remove,maximized_vert,maximized_horz", self.papyrus)
        self.assertIn("wait \"${child}\"", self.papyrus)

    def test_papyrus_launcher_remains_posix_sh_compatible(self):
        launcher = self.papyrus.split("cat > /usr/bin/papyrus <<'PAPYRUSLAUNCHER'", 1)[1].split(
            "PAPYRUSLAUNCHER", 1
        )[0]
        self.assertIn("#!/bin/sh", launcher)
        self.assertIn("shift", launcher)
        self.assertNotIn("${@:2}", launcher)


if __name__ == "__main__":
    unittest.main()
