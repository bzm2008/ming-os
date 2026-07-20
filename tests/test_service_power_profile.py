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

    def test_build_gate_verifies_resource_and_ota_units_with_systemd_analyze(self):
        for marker in (
            "/etc/systemd/system/ming-resource-policy.service",
            "/etc/systemd/system/ming-oom-profile.service",
            "/etc/systemd/system/ming-ota.slice",
            'chroot_exec /usr/bin/systemd-analyze verify "${unit}"',
        ):
            self.assertIn(marker, BUILD)

    def test_service_profile_unit_is_local_fs_ordered_and_non_network_blocking(self):
        unit = BASE.split(
            "cat > /etc/systemd/system/ming-service-profile.service << 'MINGSERVICEPROFILESVC'",
            1,
        )[1].split("MINGSERVICEPROFILESVC", 1)[0]
        self.assertIn("After=local-fs.target", unit)
        self.assertIn("Before=display-manager.service", unit)
        self.assertNotIn("network-online.target", unit)

    def test_bluetooth_enablement_happens_after_verified_bluez_runtime(self):
        desktop = BASE  # base must keep its own post-package BlueZ guard
        apps = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
        xfce = apps.split("install_xfce_desktop() {", 1)[1].split(
            "\n# ======================== VirtualBox", 1
        )[0]
        main = apps.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("systemctl enable bluetooth", xfce)
        self.assertIn("enable_bluetooth_after_runtime", apps)
        self.assertIn("dpkg-query -W -f='${db:Status-Abbrev}' bluez", apps)
        self.assertLess(
            main.index("run_required_step install_required_desktop_runtime"),
            main.index("run_required_step enable_bluetooth_after_runtime"),
        )
        self.assertIn("systemctl enable bluetooth.service", apps)
        self.assertIn("bluez", desktop)

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

    def test_spark_readiness_does_not_create_a_login_timer_or_service(self):
        self.assertNotIn("ming-appstore-ready.timer", APPS)
        self.assertNotIn("ming-appstore-ready.service", APPS)
        self.assertNotIn("OnBootSec=90s", APPS)

    def test_vendor_spark_notifier_is_masked_after_every_store_install(self):
        """The vendor notifier is not allowed into the graphical boot chain."""
        installer = APPS.split(
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            1,
        )[1].split("SPARKINSTALL", 1)[0]
        self.assertIn("ming-spark-security-converge prepare --deb", installer)
        self.assertIn("ming-spark-security-converge enforce", installer)
        self.assertNotIn("mask_spark_update_notifier", installer)
        self.assertLess(installer.index("ming-spark-security-converge enforce"), installer.index("target_user"))
        self.assertIn("spark-update-notifier.service", BUILD)

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
    def test_oom_backend_is_selected_once_and_memory_cache_pressure_is_bounded(self):
        for marker in (
            "systemd-oomd",
            "ming-oom-profile",
            "systemctl disable --now earlyoom.service",
            "systemctl disable --now systemd-oomd.service",
            "backend=systemd-oomd",
            "backend=earlyoom",
        ):
            self.assertIn(marker, BASE)
        self.assertNotIn("vfs_cache_pressure=120", BASE)
        self.assertIn("vfs_cache_pressure=80", BASE)
        self.assertIn("vfs_cache_pressure=100", BASE)

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

    def test_tlp_autosuspend_excludes_active_radio_audio_and_input_devices(self):
        tlp = BASE.split("cat > /etc/tlp.d/ming-laptop.conf << TLPCONF", 1)[1].split(
            "TLPCONF", 1
        )[0]
        for marker in (
            "USB_AUTOSUSPEND=0",
            "USB_EXCLUDE_BTUSB=1",
            "USB_EXCLUDE_AUDIO=1",
            "USB_EXCLUDE_WWAN=1",
        ):
            self.assertIn(marker, tlp)
        self.assertNotIn("USB_EXCLUDE_WLAN", tlp)
        self.assertNotIn("USB_EXCLUDE_HID", tlp)
        self.assertNotIn("USB_BLACKLIST_BTUSB", tlp)

    def test_seamless_storage_is_not_a_default_boot_or_udev_mutator(self):
        storage = BASE.split("configure_seamless_storage() {", 1)[1].split(
            "# ======================== Live / 已安装系统共同兜底", 1
        )[0]
        self.assertIn("EUID", storage)
        self.assertNotIn("systemctl enable ming-storage.service", storage)
        self.assertIn("systemctl disable --now ming-storage.service", storage)
        self.assertIn("multi-user.target.wants/ming-storage.service", storage)
        self.assertIn("udev/rules.d/99-ming-storage.rules", storage)
        self.assertNotIn("cat > /etc/udev/rules.d/99-ming-storage.rules", storage)
        self.assertNotIn("ACTION==\"add\", SUBSYSTEM==\"block\"", storage)
        self.assertNotIn("Before=lightdm.service display-manager.service", storage)
        self.assertIn("explicit authorization", storage)

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
            "USB_EXCLUDE_BTUSB=1",
            "ming-storage.service",
            "99-ming-storage.rules",
            "USB_BLACKLIST_BTUSB=1",
            "kernel.sched_latency_ns",
        ):
            self.assertIn(marker, BUILD)


if __name__ == "__main__":
    unittest.main()
