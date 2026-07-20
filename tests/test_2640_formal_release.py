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
        self.assertIn("verify_resume_release_identity", RESUME)
        skip = RESUME.split('if [[ "${MING_RESUME_SKIP_MODULES:-0}" == "1" ]]', 1)[1]
        self.assertLess(
            skip.index("verify_resume_release_identity"),
            skip.index("build_iso"),
        )
        self.assertIn('MING_RELEASE_MODE:-development', skip)
        self.assertIn("正式发布禁止跳过模块重放", skip)

    def test_settings_uses_the_public_version_label(self):
        self.assertIn("def public_release_version(value):", SETTINGS)
        self.assertIn('line.startswith("MING_DISPLAY_VERSION=")', SETTINGS)
        self.assertIn("version_formatter(current_version)", SETTINGS)

    def test_lsb_release_keeps_the_public_version(self):
        self.assertIn("DISTRIB_RELEASE=${MING_OS_VERSION}", BASE)
        self.assertIn("DISTRIB_RELEASE=${version}", BASE)
        self.assertNotIn("DISTRIB_RELEASE=${MING_OS_UPDATE_VERSION}", BASE)
        self.assertNotIn("DISTRIB_RELEASE=${update_version}", BASE)

    def test_formal_iso_identity_is_immutable_and_distinct_from_preview(self):
        self.assertIn('readonly MING_OS_BUILD_SUFFIX="formal"', BUILD)
        self.assertIn('readonly ISO_VOLUME_ID="MING_OS_2640"', BUILD)
        self.assertIn('MING_OS_RELEASE_STAGE', BUILD)
        self.assertIn('local suffix="${MING_OS_BUILD_SUFFIX}"', RESUME)
        self.assertIn('amd64-${suffix}.iso', RESUME)


if __name__ == "__main__":
    unittest.main()
