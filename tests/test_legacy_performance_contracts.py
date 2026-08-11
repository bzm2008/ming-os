import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")


class LegacyPerformanceContracts(unittest.TestCase):
    def test_picom_is_disabled_by_policy_on_legacy_not_virtual_renderers(self):
        picom = DESKTOP.split("cat > /usr/local/bin/ming-picom << 'MINGPICOM'", 1)[1].split(
            "\nMINGPICOM", 1
        )[0]
        for marker in (
            "disabled-by-policy",
            "llvmpipe",
            "softpipe",
            "nomodeset",
            "/dev/dri",
            "2.6",
        ):
            self.assertIn(marker, picom)
        self.assertIn("VirtualBox", picom)
        self.assertIn("QEMU", picom)
        self.assertIn("VMware", picom)
        self.assertIn('config="${fallback_conf}"', picom)
        self.assertIn('reason="virtual-machine-xrender"', picom)
        self.assertLess(picom.index('config="${fallback_conf}"'), picom.index("llvmpipe"))

    def test_session_health_accepts_compositor_disabled_by_policy(self):
        health = DESKTOP.split("cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1)[1].split(
            "\nMINGSESSIONHEALTH", 1
        )[0]
        self.assertIn("disabled-by-policy", health)
        self.assertIn("compositor_backend", health)
        self.assertIn("picom_policy_disabled", health)

    def test_session_health_respects_user_disabled_picom_and_waits_for_exit(self):
        health = DESKTOP.split("cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1)[1].split(
            "\nMINGSESSIONHEALTH", 1
        )[0]
        self.assertIn("picom_user_disabled", health)
        self.assertIn("settings.json", health)
        self.assertIn("appearance.json", health)
        self.assertIn("disabled-by-user", health)
        self.assertIn("wait_for_picom_exit", health)
        self.assertIn("stop_picom_and_wait", health)

    def test_dock_reload_interface_matches_appearance_control_request(self):
        health = DESKTOP.split("cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1)[1].split(
            "\nMINGSESSIONHEALTH", 1
        )[0]
        plank = DESKTOP.split("cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'", 1)[1].split(
            "\nPLANKWATCH", 1
        )[0]
        self.assertIn("--reload-dock)", health)
        self.assertIn("ming-plank-watchdog --reload", health)
        self.assertIn("--reload)", plank)
        self.assertIn("wait_for_plank_exit", plank)

    def test_plank_low_resource_profile_is_applied_before_launch(self):
        watchdog = DESKTOP.split("cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'", 1)[1].split(
            "\nPLANKWATCH", 1
        )[0]
        self.assertIn("apply_low_resource_plank_profile", watchdog)
        self.assertLess(watchdog.index("apply_low_resource_plank_profile"), watchdog.index("nohup plank"))
        self.assertIn("ZoomEnabled=false", watchdog)
        self.assertIn("IconSize=32", watchdog)
        self.assertIn("Offset=0", watchdog)

    def test_desktop_catalog_uses_event_first_and_slow_fallback_scan(self):
        self.assertIn("timeout_add_seconds(15, self.refresh_if_apps_changed)", PHONE)
        self.assertNotIn("timeout_add_seconds(3, self.refresh_if_apps_changed)", PHONE)
        self.assertIn("refresh_desktop", PHONE)

    def test_hardware_preload_bounds_modprobe_and_defers_noncritical_modules(self):
        preload = BASE.split("cat > /usr/local/sbin/ming-hardware-preload << 'HWPRELOAD'", 1)[1].split(
            "\nHWPRELOAD", 1
        )[0]
        self.assertIn("timeout --foreground 2s modprobe", preload)
        self.assertIn("ming-hardware-preload-late.service", BASE)
        self.assertIn("After=display-manager.service", BASE)

    def test_hardware_preload_has_a_total_graphical_boot_budget(self):
        preload = BASE.split("cat > /usr/local/sbin/ming-hardware-preload << 'HWPRELOAD'", 1)[1].split(
            "\nHWPRELOAD", 1
        )[0]
        service = BASE.split(
            "cat > /etc/systemd/system/ming-hardware-preload.service << 'HWPRELOADSVC'", 1
        )[1].split(
            "\nHWPRELOADSVC", 1
        )[0]
        self.assertIn("PRELOAD_TOTAL_BUDGET_SECONDS", preload)
        self.assertIn("preload deadline reached", preload)
        self.assertIn("if (( SECONDS < preload_deadline )); then", preload)
        self.assertIn("TimeoutStartSec=", service)

    def test_touch_services_are_conditional_on_touch_hardware(self):
        for marker in ("ming-touch-session", "touchscreen", "tablet", "onboard", "touchegg"):
            self.assertIn(marker, DESKTOP)
        self.assertNotIn("Exec=onboard\n", DESKTOP)
        self.assertNotIn("Exec=touchegg\n", DESKTOP)

    def test_status_widget_has_low_frequency_collapsed_sampling(self):
        self.assertIn("STATUS_SUMMARY_REFRESH_SECONDS", PHONE)
        self.assertIn("STATUS_RESOURCE_REFRESH_SECONDS", PHONE)
        self.assertIn("collapsed", PHONE)


if __name__ == "__main__":
    unittest.main()
