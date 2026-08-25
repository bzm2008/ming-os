import pathlib
import re
import os
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")


class HistoricalRegressionContracts(unittest.TestCase):
    def test_spark_pass_auth_always_uses_authorization_bridge(self):
        caller = APPS.split(
            "cat > /opt/durapps/spark-store/bin/store-helper/pass-auth.sh << 'MINGSPARKPASSAUTH'",
            1,
        )[1].split("MINGSPARKPASSAUTH", 1)[0]
        self.assertIn("ming-authorized-action spark", caller)
        self.assertNotIn("exec /usr/local/sbin/ming-spark-package-control", caller)

    def test_spark_refresh_preserves_the_graphical_session_environment(self):
        control = APPS.split(
            "cat > /usr/local/sbin/ming-spark-package-control << 'MINGSPARKCONTROL'",
            1,
        )[1].split("MINGSPARKCONTROL", 1)[0]
        refresh = control.split("refresh_desktop() {", 1)[1].split("\n}\n\nverify_packages_installed", 1)[0]
        self.assertIn('runuser -u "$target_user" -- env', refresh)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=', refresh)

    def test_spark_installs_a_scoped_aria2_download_proxy(self):
        self.assertIn("ming-spark-aria2c", APPS)
        self.assertIn("failure_class", APPS)
        self.assertIn("MING_SPARK_ARIA2C", APPS)
        self.assertIn("mirror_count", APPS)

    def test_spark_download_proxy_is_used_by_vendor_download_path(self):
        self.assertIn("MING_SPARK_ARIA2C", APPS)
        self.assertIn("/usr/local/libexec/ming-spark-aria2c", APPS)
        self.assertIn("PATH=", APPS)

    def test_spark_download_proxy_retries_and_records_failure_class(self):
        marker = "cat > /usr/local/libexec/ming-spark-aria2c << 'MINGSPARKARIA2C'"
        wrapper = APPS.split(marker, 1)[1].split("MINGSPARKARIA2C", 1)[0]
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        self.assertTrue(git_bash.is_file())
        with tempfile.TemporaryDirectory(prefix="ming-spark-aria2c-test-") as tempdir:
            root = pathlib.Path(tempdir)
            fake = root / "aria2c"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'TLS handshake failed' >&2\n"
                "exit 22\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            log = root / "download.jsonl"
            script = root / "ming-spark-aria2c"
            script.write_text(
                wrapper.replace("real=/usr/bin/aria2c", f"real={str(fake).replace(chr(92), '/')}")
                .replace("log=/var/log/ming-spark-download.jsonl", f"log={str(log).replace(chr(92), '/') }"),
                encoding="utf-8",
                newline="\n",
            )
            script.chmod(0o755)
            result = subprocess.run(
                [str(git_bash), str(script).replace("\\", "/"), "https://mirror.example/app.deb"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env={**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", "")},
            )
            self.assertNotEqual(0, result.returncode)
            record = log.read_text(encoding="utf-8").strip()
            self.assertIn('"attempts": 2', record)
            self.assertIn('"failure_class": "tls"', record)

    def test_collapsed_widget_hides_and_zeroes_expanded_content(self):
        state = PHONE.split("def apply_collapsed_state", 1)[1].split(
            "    def on_resource_clicked", 1
        )[0]
        self.assertIn("set_reveal_child(False)", state)
        self.assertIn("set_visible(False)", state)
        self.assertIn("set_size_request(-1, 0)", state)
        self.assertIn("content_height", PHONE)

    def test_drawer_has_no_large_bottom_dead_zone(self):
        source = (ROOT / "assets" / "ming-app-drawer.py").read_text(encoding="utf-8")
        match = re.search(r"DRAWER_BOTTOM_MARGIN\s*=\s*(\d+)", source)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 4)

    def test_wifi_connect_button_does_not_require_optional_bssid_display(self):
        settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
        self.assertIn('network["network_id"] and network["ifname"]', settings)
        connect = settings.split("    def on_wifi_connect", 1)[1].split("    def show_wifi_recovery_actions", 1)[0]
        self.assertNotIn('required = ("network_id", "ssid", "bssid", "ifname")', connect)

    def test_dock_runtime_uses_zoom_animation_and_responsive_geometry(self):
        watchdog = DESKTOP.split(
            "cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'", 1
        )[1].split("PLANKWATCH", 1)[0]
        self.assertIn("ZoomEnabled=true", watchdog)
        self.assertIn("ZoomPercent=148", watchdog)
        self.assertIn("offset=12", watchdog.lower())
        self.assertIn("responsive", watchdog.lower())
        self.assertNotIn("zoom_percent=125\n", watchdog)
        self.assertNotIn('theme="Ming-Mint"', watchdog)

    def test_dock_build_profile_starts_with_legacy_ming_geometry(self):
        profile = DESKTOP.split("configure_ming_mint_dock_profile() {", 1)[1].split(
            "configure_ming_mint_desktop_icons()", 1)[0]
        self.assertIn("IconSize=40", profile)
        self.assertIn("ZoomPercent=148", profile)
        self.assertIn("Theme=Ming", profile)
        self.assertNotIn("Theme=Ming-Mint", profile)

    def test_xiahai_runtime_is_readable_and_garlic_is_removed(self):
        self.assertIn("chmod 0755 /opt/xiahai-xiaoming", APPS)
        self.assertIn("-iname '*claw*.desktop'", DESKTOP)
        self.assertIn("-iname '*claw*.dockitem'", DESKTOP)
        self.assertIn("rm -f", APPS + DESKTOP)

    def test_install_completion_mentions_removing_media_before_reboot(self):
        self.assertRegex(
            BASE + DESKTOP,
            r"(?s)(拔出|移除).{0,80}(U盘|安装介质|ISO).{0,80}(重启|重新启动)",
        )

    def test_live_session_keeps_desktop_stack_when_installer_is_minimized(self):
        session = DESKTOP.split(
            "cat > /usr/local/bin/ming-installer-session << 'KIOSK'", 1
        )[1].split("KIOSK", 1)[0]
        self.assertIn("ming-session-healthcheck", session)
        self.assertIn("ming-session-healthcheck", DESKTOP)
        self.assertIn("ming-phone-desktop-watchdog", DESKTOP)
        self.assertIn("ming-plank-watchdog", DESKTOP)


if __name__ == "__main__":
    unittest.main()
