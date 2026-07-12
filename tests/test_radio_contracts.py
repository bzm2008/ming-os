import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def shell_function(source, name):
    match = re.search(r"^%s\(\) \{" % re.escape(name), source, re.MULTILINE)
    if not match:
        raise AssertionError("missing shell function: %s" % name)
    next_header = source.find("\n# ========================", match.end())
    return source[match.start():next_header if next_header >= 0 else len(source)]


class RadioBuildContracts(unittest.TestCase):
    def test_required_radio_firmware_is_mandatory_and_keeps_existing_vendors(self):
        installer = shell_function(BASE, "install_required_radio_firmware")
        for package in [
            "wireless-regdb",
            "bluez-firmware",
            "firmware-mediatek",
            "firmware-libertas",
            "firmware-misc-nonfree",
            "firmware-iwlwifi",
            "firmware-realtek",
            "firmware-atheros",
            "firmware-brcm80211",
        ]:
            with self.subTest(package=package):
                self.assertIn(package, installer)
        self.assertNotIn("firmware-ralink", installer)
        self.assertIn("required radio firmware package is not installed", installer)

    def test_cn_regulatory_domain_precedes_networkmanager(self):
        network = shell_function(BASE, "configure_network")
        self.assertIn("ming-regdom.service", network)
        self.assertIn("iw reg set CN", network)
        self.assertIn("Before=NetworkManager.service", network)
        self.assertIn("systemctl enable ming-regdom.service", network)

    def test_wifi_backends_are_mutually_exclusive_and_default_to_wpa(self):
        network = BASE.split("configure_network() {", 1)[1].split(
            "\ndeploy_hardware_diagnostics()", 1
        )[0]
        repair = BASE.split("cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'", 1)[1].split(
            "NETREPAIR", 1
        )[0]
        self.assertIn("systemctl enable --now wpa_supplicant.service", network)
        self.assertIn("old_service=wpa_supplicant.service", repair)
        self.assertIn("old_service=iwd.service", repair)
        self.assertIn("new_service=iwd.service", repair)
        self.assertIn("new_service=wpa_supplicant.service", repair)
        self.assertIn('systemctl disable --now "${old_service}"', repair)
        self.assertIn('systemctl enable --now "${new_service}"', repair)
        self.assertNotIn("systemctl restart iwd", repair)
        self.assertNotIn("systemctl restart wpa_supplicant", repair)

    def test_default_wpa_configuration_explicitly_disables_iwd_service(self):
        network = BASE.split("configure_network() {", 1)[1].split(
            "\ndeploy_hardware_diagnostics()", 1
        )[0]
        self.assertIn("systemctl disable --now iwd.service", network)

    def test_runtime_backend_switch_fails_closed_and_never_leaves_iwd_and_wpa_active(self):
        repair = BASE.split("cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'", 1)[1].split(
            "NETREPAIR", 1
        )[0]
        self.assertIn("switch_backend()", repair)
        self.assertIn('systemctl disable --now "${old_service}"', repair)
        self.assertIn('systemctl enable --now "${new_service}"', repair)
        self.assertIn('mv -f "${config_tmp}" "${config_path}"', repair)
        self.assertIn('exit 2', repair)
        self.assertNotIn("systemctl disable --now wpa_supplicant.service 2>/dev/null || true", repair)
        self.assertNotIn("systemctl enable --now iwd.service 2>/dev/null || true", repair)

    def test_runtime_backend_switch_restores_config_and_services_when_networkmanager_restart_fails(self):
        repair = BASE.split("cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'", 1)[1].split(
            "NETREPAIR", 1
        )[0]
        self.assertIn("config_backup", repair)
        self.assertIn("rollback_backend()", repair)
        restart_failure = repair.index("NetworkManager restart failed")
        rollback_before_restart = repair.rfind("rollback_backend", 0, restart_failure)
        self.assertGreater(rollback_before_restart, 0)

    def test_backend_rollback_stops_new_service_before_starting_old_service(self):
        repair = BASE.split("cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'", 1)[1].split(
            "NETREPAIR", 1
        )[0]
        start = repair.index("rollback_backend() {")
        end = repair.index('\n    if ! systemctl disable --now "${old_service}"', start)
        rollback = repair[start:end]

        stop_new = 'if ! systemctl disable --now "${new_service}"; then'
        start_old = 'if ! systemctl enable --now "${old_service}"; then'
        self.assertIn(stop_new, rollback)
        self.assertIn(start_old, rollback)
        self.assertLess(rollback.index(stop_new), rollback.index(start_old))
        self.assertNotIn('systemctl disable --now "${new_service}" || true', rollback)

    def test_new_backend_start_failure_uses_the_full_rollback(self):
        repair = BASE.split("cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'", 1)[1].split(
            "NETREPAIR", 1
        )[0]
        start = repair.index('if ! systemctl enable --now "${new_service}"; then')
        end = repair.index("\n    fi", start) + len("\n    fi")
        start_failure = repair[start:end]

        self.assertIn("rollback_backend", start_failure)

    def test_main_propagates_required_radio_firmware_install_failure(self):
        main = shell_function(BASE, "main")
        self.assertIn("install_base_packages || return 1", main)

    def test_intel_gen9_iHD_vaapi_driver_is_installed_and_build_validated(self):
        installer = shell_function(BASE, "install_base_packages")
        self.assertIn("intel-media-va-driver", installer)
        self.assertIn("intel-media-va-driver", BUILD)
        self.assertIn("usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so", BUILD)

    def test_macbook_initramfs_validation_reads_full_listing_before_matching_modules(self):
        macbook = shell_function(BASE, "configure_macbook_input_modules")

        self.assertIn('initrd_modules="$(lsinitramfs "${initrd}" 2>/dev/null)"', macbook)
        self.assertIn('grep -Eq "/${module_file}\\.ko(\\.|$)" <<< "${initrd_modules}"', macbook)
        self.assertNotIn('lsinitramfs "${initrd}" 2>/dev/null | grep -Eq', macbook)
        self.assertIn('if modinfo -k "${kernel_version}" "${module_file//-/_}"', macbook)
        self.assertIn('echo "[ERROR] ${module_file} 未进入 ${initrd}"', macbook)
        self.assertIn('echo "[WARN] ${module_file} 未出现在 ${initrd}', macbook)

    def test_r4_rootfs_validator_imports_os_before_using_os_access(self):
        r4_validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split(
            "\n# ========================", 1
        )[0]
        python = r4_validator.split("python3 - \"${CHROOT_DIR}\" <<'PY'\n", 1)[1].split(
            "\nPY\n", 1
        )[0]

        self.assertIn("os.access(", python)
        self.assertRegex(python, re.compile(r"^import os$", re.MULTILINE))

    def test_bluetooth_is_enabled_only_after_bluez_and_has_no_graphical_delay(self):
        network = shell_function(BASE, "configure_network")
        boot_speed = shell_function(BASE, "configure_boot_speed")
        self.assertIn("dpkg-query -W -f='${db:Status-Abbrev}' bluez", network)
        self.assertIn("systemctl enable bluetooth.service", network)
        self.assertIn("rm -f /etc/systemd/system/bluetooth.service.d/delay.conf", boot_speed)
        self.assertNotIn("bluetooth.service.d/delay.conf <<", boot_speed)

    def test_bluetooth_preload_and_privileged_repair_are_deployed(self):
        preload = BASE.split("cat > /usr/local/sbin/ming-hardware-preload << 'HWPRELOAD'", 1)[1].split(
            "HWPRELOAD", 1
        )[0]
        opener = "cat > /usr/local/sbin/ming-radio-repair << 'RADIOREPAIR'"
        self.assertIn(opener, BASE)
        repair = BASE.split(opener, 1)[1].split(
            "RADIOREPAIR", 1
        )[0]
        for module in ["btusb", "btintel", "btrtl", "btbcm", "ath3k"]:
            self.assertIn(module, preload)
            self.assertIn(module, repair)
        self.assertIn("exec pkexec /usr/local/sbin/ming-radio-repair bluetooth", repair)
        self.assertIn("rfkill unblock bluetooth", repair)
        self.assertIn("systemctl enable bluetooth.service", repair)
        self.assertIn("systemctl start bluetooth.service", repair)
        self.assertIn("ming-device-control bluetooth-status --json", repair)
        self.assertIn("no_hardware", repair)
        self.assertIn("/var/log/ming-radio-repair.log", repair)

    def test_bluetooth_repair_refuses_hard_block_and_untrusted_diagnostics_before_module_reload(self):
        opener = "cat > /usr/local/sbin/ming-radio-repair << 'RADIOREPAIR'"
        repair = BASE.split(opener, 1)[1].split("RADIOREPAIR", 1)[0]
        hard_guard = repair.index('[[ "${before_state}" == "diagnostic_unavailable" ]]')
        module_reload = repair.index("for module in ath3k btbcm btrtl btintel btusb")
        self.assertIn("before_hard_blocked", repair)
        self.assertIn('[[ "${before_state}" == "diagnostic_unavailable" ]]', repair)
        self.assertIn('[[ "${before_hard_blocked}" == "true" ]]', repair)
        self.assertLess(hard_guard, module_reload)

    def test_build_gate_verifies_radio_packages_services_and_repair_helper(self):
        for marker in [
            "wireless-regdb",
            "bluez-firmware",
            "firmware-mediatek",
            "firmware-libertas",
            "firmware-misc-nonfree",
            "etc/systemd/system/ming-regdom.service",
            "Before=NetworkManager.service",
            "etc/systemd/system/bluetooth.service.d/ming-radio-unblock.conf",
            "usr/local/sbin/ming-radio-repair",
            '"btintel"',
            '"btrtl"',
            '"btbcm"',
            '"ath3k"',
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, BUILD)
        runtime_gate = BUILD.split("validate_required_desktop_runtime() {", 1)[1].split(
            "\nvalidate_r4_compatibility()", 1
        )[0]
        self.assertIn("dpkg-query -W -f='${Status}'", runtime_gate)
        self.assertIn("install ok installed", runtime_gate)
        for package in [
            "wireless-regdb",
            "bluez-firmware",
            "firmware-mediatek",
            "firmware-libertas",
            "firmware-misc-nonfree",
            "firmware-iwlwifi",
            "firmware-realtek",
            "firmware-atheros",
            "firmware-brcm80211",
        ]:
            with self.subTest(package=package):
                self.assertIn(package, runtime_gate)
        self.assertNotIn("firmware-ralink", runtime_gate)

    def test_rootfs_firmware_gate_uses_trixie_available_misc_nonfree_package(self):
        rootfs_gate = BUILD.split("for firmware_package in [", 1)[1].split("]:", 1)[0]

        self.assertIn('"firmware-misc-nonfree"', rootfs_gate)
        self.assertNotIn('"firmware-ralink"', rootfs_gate)


if __name__ == "__main__":
    unittest.main()
