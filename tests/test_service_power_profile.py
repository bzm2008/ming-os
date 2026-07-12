"""Regression contracts for boot-service and power-profile tuning."""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


class ServiceProfileContracts(unittest.TestCase):
    def test_service_profile_helper_is_deployed_and_hardware_aware(self):
        for marker in (
            "ming-service-profile",
            "MING_KEEP_MODEMMANAGER",
            "/dev/cdc-wdm",
            "/sys/class/net/wwan*",
            "WWAN",
            "status --json",
            "cups.socket",
            "serial-getty@ttyS0.service",
        ):
            self.assertIn(marker, BASE)

    def test_service_profile_unit_is_local_fs_ordered_and_non_network_blocking(self):
        unit = BASE.split(
            "cat > /etc/systemd/system/ming-service-profile.service << 'MINGSERVICEPROFILESVC'",
            1,
        )[1].split("MINGSERVICEPROFILESVC", 1)[0]
        self.assertIn("After=local-fs.target", unit)
        self.assertIn("Before=display-manager.service", unit)
        self.assertNotIn("network-online.target", unit)

    def test_optional_daemons_are_on_demand_and_not_a_graphical_boot_gate(self):
        for marker in (
            "systemctl disable --now cups.service cups-browsed.service",
            "avahi-daemon.service",
            "saned.service saned.socket",
            "systemctl enable cups.socket",
            "ming-service-profile.service",
            "WantedBy=multi-user.target",
        ):
            self.assertIn(marker, BASE)
        self.assertNotIn("ExecStart=/usr/bin/nm-online -s -q -t 60", BASE)
        self.assertNotIn("ExecStart=/usr/bin/nm-online -s -q --timeout=5", BASE)

    def test_rfkill_service_does_not_order_after_its_own_target(self):
        service = BASE.split(
            "cat > /etc/systemd/system/ming-rfkill.service << RFKILLSVC", 1
        )[1].split("RFKILLSVC", 1)[0]
        self.assertIn("After=NetworkManager.service", service)
        self.assertIn("Before=graphical.target", service)
        self.assertNotIn("After=multi-user.target", service)

    def test_device_tune_does_not_block_on_udev_settle(self):
        service = BASE.split(
            "cat > /etc/systemd/system/ming-device-tune.service << DEVICETUNESVC", 1
        )[1].split("DEVICETUNESVC", 1)[0]
        self.assertNotIn("systemd-udev-settle.service", service)
        self.assertNotIn("Wants=systemd-udev-settle.service", service)

    def test_spark_readiness_is_delayed_by_timer_without_network_online(self):
        service = APPS.split(
            "cat > /etc/systemd/system/ming-appstore-ready.service << 'SVCUNIT'",
            1,
        )[1].split("SVCUNIT", 1)[0]
        self.assertNotIn("network-online.target", service)
        self.assertIn("ming-appstore-ready.timer", APPS)
        self.assertIn("OnBootSec=90s", APPS)
        self.assertIn("After=graphical.target", service)

    def test_modem_manager_is_disabled_by_default_but_has_explicit_opt_in(self):
        network = BASE.split("configure_network() {", 1)[1].split(
            "deploy_time_sync() {", 1
        )[0]
        self.assertNotIn("systemctl enable ModemManager", network)
        profile = BASE.split(
            "cat > /usr/local/sbin/ming-service-profile << 'MINGSERVICEPROFILE'",
            1,
        )[1].split("MINGSERVICEPROFILE", 1)[0]
        self.assertIn("MING_KEEP_MODEMMANAGER", profile)
        self.assertIn("systemctl enable --now ModemManager.service", profile)
        self.assertIn("systemctl disable --now ModemManager.service", profile)

    def test_wwan_detection_probes_are_bounded_before_display_manager(self):
        profile = BASE.split(
            "cat > /usr/local/sbin/ming-service-profile << 'MINGSERVICEPROFILE'",
            1,
        )[1].split("MINGSERVICEPROFILE", 1)[0]
        for marker in (
            "timeout --foreground 2s nmcli",
            "timeout --foreground 2s lspci",
            "timeout --foreground 2s lsusb",
        ):
            self.assertIn(marker, profile)

    def test_serial_getty_requires_explicit_debug_opt_in(self):
        self.assertIn("MING_DEBUG_SERIAL", BASE)
        self.assertIn("serial-getty@ttyS0.service", BASE)
        self.assertNotIn("systemctl enable serial-getty@ttyS0.service 2>/dev/null || true", BASE)


class PowerProfileContracts(unittest.TestCase):
    def test_radio_power_tuning_does_not_break_bluetooth_or_wifi(self):
        self.assertNotIn("USB_BLACKLIST_BTUSB=1", BASE)
        self.assertNotIn("options iwlwifi power_save=0", BASE)
        self.assertIn("bt_coex_active=1", BASE)

    def test_tlp_and_thermald_have_one_hardware_aware_owner(self):
        for marker in (
            "ming-power-profile",
            "has_battery",
            "thermald",
            "tlp.service",
            "systemctl enable --now tlp.service",
            "systemctl disable --now tlp.service",
            "intel",
            "chassis_type",
            "laptop-detect",
        ):
            self.assertIn(marker, BASE)

    def test_sysctl_application_ignores_unsupported_keys_and_avoids_legacy_tuning(self):
        for marker in (
            "ming-sysctl-apply",
            r"/proc/sys/${key//./\/}",
            "unsupported sysctl",
            "sysctl -q",
        ):
            self.assertIn(marker, BASE)
        self.assertNotIn("kernel.sched_latency_ns", BASE)
        self.assertNotIn("kernel.sched_min_granularity_ns", BASE)
        self.assertNotIn("kernel.sched_wakeup_granularity_ns", BASE)
        self.assertNotIn("vm.min_free_kbytes=65536", BASE)

    def test_governor_selection_only_writes_supported_governors(self):
        self.assertIn("scaling_available_governors", BASE)
        self.assertIn("governor-only", BASE)
        self.assertNotIn(
            "echo schedutil > /sys/devices/system/cpu/cpu%n/cpufreq/scaling_governor",
            BASE,
        )

    def test_build_gate_checks_service_profile_and_power_contracts(self):
        for marker in (
            "usr/local/sbin/ming-service-profile",
            "ming-service-profile status --json",
            "ming-service-profile.service",
            "ming-power-profile.service",
            "ming-device-tune.service",
            "ming-rfkill.service",
            "After=NetworkManager.service",
            "USB_BLACKLIST_BTUSB=1",
            "kernel.sched_latency_ns",
        ):
            self.assertIn(marker, BUILD)


if __name__ == "__main__":
    unittest.main()
