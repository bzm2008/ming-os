import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
STORE = (ROOT / "assets" / "ming-store.py").read_text(encoding="utf-8")
STORE_CORE = (ROOT / "assets" / "ming-store-core.py").read_text(encoding="utf-8")
STORE_CONTROL = (ROOT / "assets" / "ming-store-control.py").read_text(encoding="utf-8")


class HistoricalRegressionContracts(unittest.TestCase):
    def test_store_operations_always_use_the_authorization_bridge(self):
        self.assertIn(
            '"/usr/local/bin/ming-authorized-action", "store", action',
            STORE,
        )
        bridge = DESKTOP.split(
            "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'", 1
        )[1].split("MINGAUTHORIZE", 1)[0]
        self.assertIn("store)", bridge)
        self.assertIn('command=(/usr/local/sbin/ming-store-control "$1" "$2")', bridge)
        self.assertNotIn("eval ", bridge)

    def test_store_refresh_preserves_the_graphical_session_environment(self):
        refresh = STORE_CONTROL.split("    def _refresh_desktop", 1)[1].split(
            "    @staticmethod", 1
        )[0]
        self.assertIn('"runuser", "-u", user_name, "--", "env"', refresh)
        self.assertIn('"XDG_RUNTIME_DIR=" + str(runtime)', refresh)

    def test_store_downloader_is_https_only_retriable_and_hash_verified(self):
        downloader = STORE_CORE.split("class SecureDownloader", 1)[1].split(
            "def default_catalog", 1
        )[0]
        self.assertIn("attempts=3", downloader)
        self.assertIn('parsed.scheme != "https"', downloader)
        self.assertIn("hashlib.sha256", downloader)
        self.assertIn("IntegrityError", downloader)
        self.assertIn("Range", downloader)

    def test_store_root_helper_resolves_provider_instead_of_accepting_a_url(self):
        execute = STORE_CONTROL.split("    def _execute_request", 1)[1].split(
            "    def execute", 1
        )[0]
        self.assertIn("provider = self._provider(request)", execute)
        self.assertIn("resolved = provider.resolve(request.app_id)", execute)
        self.assertNotIn("request.url", execute)
        self.assertNotIn("shell=True", execute)

    def test_spark_upgrade_cleanup_keeps_historical_installed_apps(self):
        cleanup = FINALIZE.split("retire_legacy_store_runtime() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("apt-get remove --no-auto-remove", cleanup)
        self.assertNotRegex(cleanup, r"apt-get\s+(?:-\S+\s+)*autoremove")
        self.assertNotRegex(cleanup, r"(?m)^[ \t]*(?:rm|find)\b[^\n]*/opt/apps")
        self.assertIn("do not touch /opt/apps", cleanup.lower())
        self.assertIn('require_absent(residue, "Spark/APM residue")', BUILD)

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

    def test_dock_build_profile_starts_with_responsive_ming_geometry(self):
        profile = DESKTOP.split("configure_ming_mint_dock_profile() {", 1)[1].split(
            "configure_ming_mint_desktop_icons()", 1)[0]
        self.assertIn("IconSize=40", profile)
        self.assertIn("ZoomPercent=148", profile)
        self.assertIn("Offset=12", profile)
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
