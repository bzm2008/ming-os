import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
OTA = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
RESUME = (ROOT / "resume_build.sh").read_text(encoding="utf-8")


class FormalReleaseIdentityContracts(unittest.TestCase):
    def test_display_and_transaction_versions_are_separate(self):
        self.assertIn('readonly MING_OS_VERSION="26.4.0"', BUILD)
        self.assertIn('readonly MING_OS_UPDATE_VERSION="26.4.0.1"', BUILD)
        self.assertIn('readonly MING_OS_RELEASE_STAGE="stable"', BUILD)
        self.assertIn('MING_OS_UPDATE_VERSION="${MING_OS_UPDATE_VERSION}"', BUILD)
        self.assertIn('MING_OS_RELEASE_STAGE="${MING_OS_RELEASE_STAGE}"', BUILD)

    def test_os_release_uses_transaction_version_without_exposing_preview_label(self):
        for source in (BASE, OTA):
            self.assertIn('VERSION_ID="${MING_OS_UPDATE_VERSION}"', source)
            self.assertIn('PRETTY_NAME="Ming OS ${MING_OS_VERSION} 正式版"', source)
        self.assertIn('echo "${MING_OS_UPDATE_VERSION}" > /etc/ming-version', OTA)

    def test_resume_and_installed_identity_refresh_every_version_file(self):
        self.assertIn("cat > /etc/os-release << RELEASEOS", OTA)
        self.assertIn(
            'write_file /etc/ming-version <<MINGVERSION', BASE)
        self.assertIn(
            'write_file /etc/ming-display-version <<MINGDISPLAYVERSION', BASE)

    def test_settings_uses_the_public_version_label(self):
        self.assertIn("def public_release_version(value):", SETTINGS)
        self.assertIn('line.startswith("MING_DISPLAY_VERSION=")', SETTINGS)
        self.assertIn("version_formatter(current_version)", SETTINGS)

    def test_formal_iso_identity_is_immutable_and_distinct_from_preview(self):
        self.assertIn('readonly MING_OS_BUILD_SUFFIX="formal"', BUILD)
        self.assertIn('readonly ISO_VOLUME_ID="MING_OS_2640"', BUILD)
        self.assertIn('MING_OS_RELEASE_STAGE', BUILD)
        self.assertIn('local suffix="${MING_OS_BUILD_SUFFIX}"', RESUME)
        self.assertIn('amd64-${suffix}.iso', RESUME)


if __name__ == "__main__":
    unittest.main()
