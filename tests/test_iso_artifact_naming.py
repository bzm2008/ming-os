import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA_README = ROOT / "docs" / "ota" / "README.md"
BUILD = ROOT / "build_onion_os.sh"


class IsoArtifactNamingTests(unittest.TestCase):
    def test_live_initrd_name_is_documented_separately_from_installed_slot_initrd(self):
        text = OTA_README.read_text(encoding="utf-8")
        self.assertIn("Live ISO uses `/live/initrd`", text)
        self.assertIn("installed A/B slots use", text)
        self.assertIn("`initrd.img` and are a separate artifact naming contract", text)

    def test_build_and_iso_validation_use_the_live_initrd_name(self):
        text = BUILD.read_text(encoding="utf-8")
        self.assertIn("/live/initrd", text)
        self.assertNotIn("/live/initrd.img", text)


if __name__ == "__main__":
    unittest.main()
