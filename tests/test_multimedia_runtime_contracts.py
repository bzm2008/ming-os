import hashlib
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def heredoc(source, declaration, marker):
    start = source.index(declaration)
    end = source.index("\n" + marker, start + len(declaration))
    return source[start:end]


class MultimediaRuntimeContracts(unittest.TestCase):
    def test_audio_session_helper_is_deployed_with_login_recovery(self):
        install = DESKTOP.split("install_ming_shell_components() {", 1)[1].split(
            "\n}\n\ninstall_ming_files", 1
        )[0]
        self.assertIn("ming-audio-session.py", install)
        self.assertIn("/usr/local/bin/ming-audio-session", install)
        self.assertIn("ming-audio-session.desktop", DESKTOP)
        self.assertIn("ming-audio-session ensure", DESKTOP)

    def test_session_supervisor_rechecks_audio_after_resume_or_device_changes(self):
        supervisor = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1
        )[1].split("MINGSESSIONHEALTH", 1)[0]
        self.assertIn("AUDIO_CHECK_INTERVAL", supervisor)
        self.assertIn("ensure_audio_session", supervisor)
        self.assertIn("ming-audio-session ensure --json", supervisor)
        self.assertIn("run_bounded 6", supervisor)
        self.assertIn("ensure_audio_session", supervisor.split("startup_once()", 1)[1])
        self.assertIn("ensure_audio_session", supervisor.split("supervise_once()", 1)[1])

    def test_edge_and_wechat_preflight_audio_without_forcing_a_valid_output(self):
        edge = heredoc(APPS, "cat > /usr/local/bin/ming-edge << 'MINGEDGE'", "MINGEDGE")
        wechat = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("ming-audio-session ensure", edge)
        self.assertIn("ming-audio-session ensure", wechat)
        self.assertIn("ming-device-control audio-repair-playback", wechat)
        self.assertLess(
            wechat.index("audio-repair-playback"),
            wechat.index("audio-repair-call"),
        )

    def test_spark_repair_uses_the_verified_local_deb_and_refreshes_launchers(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("ming-phone-desktop --sync", installer)
        self.assertIn("sha256sum -c", installer)
        self.assertIn("/usr/share/ming-os/vendor/spark-store", installer)
        self.assertIn("target_user=", installer)
        self.assertIn("getent passwd", installer)
        self.assertNotIn("gitee.com/api", installer)
        self.assertNotIn("wget", installer)
        self.assertNotIn("curl", installer)

    def test_spark_is_a_verified_build_asset_and_runtime_repair_never_downloads_it(self):
        app_store = APPS.split("install_app_store() {", 1)[1].split("\n}", 1)[0]
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        expected_sha256 = "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55"
        self.assertIn("spark-store_5.2.1.0_amd64.deb", app_store)
        self.assertIn(expected_sha256, app_store)
        self.assertIn("sha256sum -c", app_store)
        self.assertIn("/usr/share/ming-os/vendor/spark-store", installer)
        self.assertIn(expected_sha256, installer)
        self.assertNotIn("gitee.com/api", installer)
        self.assertNotIn("wget", installer)
        self.assertNotIn("curl", installer)
        self.assertIn(
            "Exec=/usr/local/bin/ming-package-install-gui /usr/share/ming-os/vendor/spark-store/spark-store_5.2.1.0_amd64.deb",
            APPS,
        )

    def test_spark_bundle_has_a_machine_readable_source_receipt_and_expected_hash(self):
        bundle = ROOT / "assets" / "vendor" / "spark-store" / "spark-store_5.2.1.0_amd64.deb"
        receipt = ROOT / "assets" / "vendor" / "spark-store" / "receipt.json"
        self.assertTrue(bundle.is_file())
        self.assertTrue(receipt.is_file())
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual("5.2.1.0", metadata["version"])
        self.assertEqual("spark-store_5.2.1.0_amd64.deb", metadata["asset"])
        self.assertEqual(
            "https://gitee.com/spark-store-project/spark-store/releases/download/5.2.1.0/spark-store_5.2.1.0_amd64.deb",
            metadata["source_url"],
        )
        self.assertEqual(
            "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55",
            hashlib.sha256(bundle.read_bytes()).hexdigest().upper(),
        )

    def test_official_wechat_download_uses_the_same_verified_local_deb_path(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'",
            "WECHATINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("Administrator privileges are required", installer)
        self.assertNotIn("sudo apt install", installer)

    def test_wechat_launcher_uses_only_dpkg_owned_strict_desktop_entries(self):
        wrapper = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("find_wechat_argv", wrapper)
        self.assertIn("dpkg-query -S --", wrapper)
        self.assertIn("parse_desktop_file", wrapper)
        self.assertIn("desktop_launch_diagnostic", wrapper)
        self.assertIn("mapfile -d '' -t wechat_argv", wrapper)
        self.assertIn('exec "${wechat_argv[@]}" "$@"', wrapper)
        self.assertNotIn("eval ", wrapper)

    def test_build_gate_requires_audio_and_local_package_helpers(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("usr/local/bin/ming-audio-session", validator)
        self.assertIn("usr/local/sbin/ming-package-installer", validator)
        self.assertIn("usr/share/ming-os/vendor/spark-store/spark-store_5.2.1.0_amd64.deb", validator)
        self.assertIn("88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55", validator)
        self.assertNotIn("spark_asset.read_bytes()", validator)


if __name__ == "__main__":
    unittest.main()
