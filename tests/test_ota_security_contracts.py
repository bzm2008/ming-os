import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OTA = ROOT / "modules" / "06_ota_update.sh"


class OtaSecurityContracts(unittest.TestCase):
    def test_download_rejects_redirects_outside_the_configured_host_boundary(self):
        text = OTA.read_text(encoding="utf-8")
        self.assertIn("--max-redirect=0", text)
        self.assertIn("DOWNLOAD_SERVER", text)

    def test_interactive_config_is_generated_with_jq_json_encoding(self):
        text = OTA.read_text(encoding="utf-8")
        section = text.split("configure_update() {", 1)[1].split("show_help() {", 1)[0]
        self.assertIn("jq", section)
        self.assertNotIn('cat > "${cfg}" << CONFIGJSON', section)
        self.assertIn("--arg channel", section)
        self.assertIn("--argjson auto_check", section)


if __name__ == "__main__":
    unittest.main()
