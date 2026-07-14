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

    def test_spark_download_uses_verified_local_deb_install_and_refreshes_launchers(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("ming-phone-desktop --sync", installer)
        self.assertIn("update-desktop-database", installer)
        self.assertIn("target_user=", installer)
        self.assertIn("getent passwd", installer)

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
        # The rootfs validator contains embedded Python, so its first `}` is
        # not the end of the shell function.  Check the full generated source
        # instead of truncating valid gate entries at an inner Python block.
        self.assertIn("usr/local/bin/ming-audio-session", BUILD)
        self.assertIn("usr/local/sbin/ming-package-installer", BUILD)


if __name__ == "__main__":
    unittest.main()
