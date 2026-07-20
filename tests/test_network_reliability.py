import base64
import importlib.util
import json
import pathlib
import os
import unittest
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEVICE_CONTROL = ROOT / "assets" / "ming-device-control.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")


def load_device_control():
    spec = importlib.util.spec_from_file_location("ming_device_control_network", DEVICE_CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NetworkReliabilityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = load_device_control()

    def test_ssid_encoding_keeps_raw_bytes_and_human_display_separate(self):
        result = self.device.encode_ssid_bytes(b"\xe4\xb8\xad\xe6\x96\x87")
        self.assertEqual("中文", result["display"])
        self.assertEqual("utf-8", result["encoding"])
        self.assertEqual(
            base64.b64encode(b"\xe4\xb8\xad\xe6\x96\x87").decode("ascii"),
            result["ssid_bytes_b64"],
        )

    def test_invalid_ssid_bytes_use_escape_display_without_loss(self):
        result = self.device.encode_ssid_bytes(b"A\xff\\B")
        self.assertEqual("binary", result["encoding"])
        self.assertEqual("A\\xff\\B", result["display"])
        self.assertEqual(base64.b64encode(b"A\xff\\B").decode("ascii"), result["ssid_bytes_b64"])

    def test_network_id_is_stable_and_not_the_display_ssid(self):
        ssid = "中文".encode("utf-8")
        first = self.device.make_network_id("wlan0", "AA:BB:CC:DD:EE:FF", ssid)
        second = self.device.make_network_id("wlan0", "aa:bb:cc:dd:ee:ff", ssid)
        self.assertEqual(first, second)
        self.assertNotEqual(first, "中文")
        self.assertRegex(first, r"^ming-net-[0-9a-f]{32}$")

    def test_wifi_scan_has_lossless_identity_and_uniform_result_fields(self):
        class Backend:
            def wifi_scan(self):
                return [{
                    "ifname": "wlan0", "bssid": "AA:BB:CC:DD:EE:FF",
                    "ssid_bytes": b"\xffCafe", "frequency_mhz": 2412,
                    "channel": 1, "signal": 71, "security": "WPA2",
                    "active": False,
                }]

            def available(self):
                return True

        controller = self.device.DeviceController(
            network_backend=Backend(), executable=lambda _name: False)
        result = controller.wifi_scan()
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertIn("reason_code", result)
        self.assertIn("reason_text", result)
        self.assertIn("retryable", result)
        network = result["networks"][0]
        self.assertEqual("binary", network["encoding"])
        self.assertEqual("/wlan0" not in network["ssid_bytes_b64"], True)
        self.assertIn("network_id", network)
        self.assertNotIn("ssid_bytes", network)

    def test_scan_accepts_32_byte_ssid_and_rejects_impossible_long_record(self):
        rows = [
            {"ifname": "wlan0", "bssid": "AA:BB:CC:DD:EE:01",
             "ssid_bytes": b"a" * 32, "frequency_mhz": 2412},
            {"ifname": "wlan0", "bssid": "AA:BB:CC:DD:EE:02",
             "ssid_bytes": b"b" * 33, "frequency_mhz": 2412},
        ]
        networks = self.device.DeviceController._normalise_wifi_rows(rows)
        self.assertEqual(1, len(networks))
        self.assertEqual(base64.b64encode(b"a" * 32).decode("ascii"),
                         networks[0]["ssid_bytes_b64"])

    def test_libnm_frequency_derives_channel_without_localized_tools(self):
        rows = [{"ifname": "wlan0", "bssid": "AA:BB:CC:DD:EE:01",
                 "ssid_bytes": b"Cafe", "frequency_mhz": 5180,
                 "channel": None}]
        networks = self.device.DeviceController._normalise_wifi_rows(rows)
        self.assertEqual(36, networks[0]["channel"])

    def test_wifi_connect_uses_network_id_and_secret_only_on_stdin(self):
        class Backend:
            def __init__(self):
                self.calls = []

            def available(self):
                return True

            def wifi_connect(self, network_id, ifname, password=None):
                self.calls.append((network_id, ifname, password))
                return {"ok": True, "state": "connected", "reason_code": "connected",
                        "reason_text": "连接成功", "retryable": False,
                        "network_id": network_id, "ifname": ifname}

        backend = Backend()
        controller = self.device.DeviceController(network_backend=backend, executable=lambda _name: False)
        result = controller.wifi_connect(network_id="ming-net-" + "a" * 32,
                                         ifname="wlan0", password="secret")
        self.assertTrue(result["ok"])
        self.assertEqual(
            [("ming-net-" + "a" * 32, "wlan0", "secret")], backend.calls)

    def test_ethernet_repair_targets_one_interface_without_networkmanager_restart(self):
        class Backend:
            def __init__(self):
                self.repair_calls = []

            def available(self):
                return True

            def ethernet_status(self):
                return {"ok": True, "state": "disconnected", "reason_code": "carrier_down",
                        "reason_text": "网线未接入", "retryable": True,
                        "devices": [{"device": "enp2s0", "state": "disconnected"},
                                    {"device": "enp3s0", "state": "connected"}]}

            def ethernet_repair(self, ifname):
                self.repair_calls.append(ifname)
                return {"ok": True, "state": "connected", "reason_code": "connected",
                        "reason_text": "已连接", "retryable": False,
                        "devices": [{"device": ifname, "state": "connected"}]}

        backend = Backend()
        controller = self.device.DeviceController(network_backend=backend, executable=lambda _name: False)
        result = controller.ethernet_repair(ifname="enp2s0")
        self.assertTrue(result["ok"])
        self.assertEqual(["enp2s0"], backend.repair_calls)

    def test_nmcli_ethernet_fallback_reports_profile_speed_reason_and_autoconnect(self):
        responses = {
            ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
                (0, "enp2s0:ethernet:connected:Ming Wired", ""),
            ("nmcli", "-t", "-f",
             "GENERAL.DRIVER,GENERAL.SPEED,GENERAL.REASON,GENERAL.CONNECTION,"
             "WIRED-PROPERTIES.CARRIER,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,"
             "IP4.DHCP4.OPTION,GENERAL.STATE", "device", "show", "enp2s0"):
                (0, "GENERAL.DRIVER:r8169\nGENERAL.SPEED:1000 Mb/s\n"
                    "GENERAL.REASON:0 (No reason given)\nGENERAL.CONNECTION:Ming Wired\n"
                    "WIRED-PROPERTIES.CARRIER:yes\nIP4.ADDRESS[1]:192.0.2.9/24\n"
                    "IP4.GATEWAY:192.0.2.1\nIP4.DNS[1]:192.0.2.53\n"
                    "IP4.DHCP4.OPTION[1]:lease_time = 3600\nGENERAL.STATE:100 (connected)", ""),
            ("nmcli", "-g", "connection.autoconnect,ipv4.method", "connection", "show", "Ming Wired"):
                (0, "yes\nauto", ""),
        }

        def runner(command, timeout=8):
            return responses.get(tuple(command), (1, "", "missing"))

        controller = self.device.DeviceController(
            runner=runner, executable=lambda name: name == "nmcli")
        result = controller.ethernet_status()
        device = result["devices"][0]
        self.assertEqual(1000, device["speed_mbps"])
        self.assertEqual("0 (No reason given)", device["nm_reason"])
        self.assertEqual("Ming Wired", device["profile"])
        self.assertTrue(device["autoconnect"])
        self.assertEqual("bound", device["dhcp"])
        self.assertIn("link_flap", device)

    def test_link_flap_evidence_marks_recent_carrier_counter_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            carrier = pathlib.Path(directory) / "class" / "net" / "enp2s0"
            carrier.mkdir(parents=True)
            (carrier / "carrier_changes").write_text("2\n", encoding="ascii")
            controller = self.device.DeviceController(
                runner=lambda *_args, **_kwargs: (1, "", ""),
                executable=lambda _name: False, sysfs_root=directory)
            first = controller._link_evidence("enp2s0")
            (carrier / "carrier_changes").write_text("3\n", encoding="ascii")
            second = controller._link_evidence("enp2s0")
            self.assertFalse(first["recent"])
            self.assertTrue(second["recent"])
            self.assertEqual(1, second["delta"])

    def test_base_does_not_unconditionally_preload_network_drivers(self):
        marker = BASE.split("cat > \"${target}/etc/modules-load.d/ming-network.conf\"", 1)
        self.assertEqual(1, len(marker), "installed target must not force-load network modules")
        self.assertNotIn("iwlwifi\nath9k\ne1000e", BASE)
        preload = BASE.split("cat > /usr/local/sbin/ming-hardware-preload << 'HWPRELOAD'", 1)[1].split(
            "HWPRELOAD", 1)[0]
        for module in ("btusb", "btintel", "btrtl", "btbcm", "ath3k"):
            self.assertNotRegex(preload, rf"(?m)^\s*{module}\s*$")

    def test_build_and_resume_install_libnm_and_allow_udev_driver_selection(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        resume = (ROOT / "resume_build.sh").read_text(encoding="utf-8")
        self.assertIn("gir1.2-nm-1.0", build)
        self.assertIn("gir1.2-nm-1.0", resume)
        self.assertNotIn('require_file("etc/modules-load.d/ming-network.conf", "iwlwifi")', build)

    def test_profile_migration_only_changes_managed_plain_dhcp_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            managed = root / "ming-wired.nmconnection"
            managed.write_text(
                "[connection]\nid=Ming Wired\ntype=802-3-ethernet\n"
                "interface-name=eno1\nautoconnect=false\n"
                "[802-3-ethernet]\nmac-address=00:11:22:33:44:55\n"
                "[ipv4]\nmethod=auto\n[ipv6]\nmethod=auto\n",
                encoding="utf-8",
            )
            os.chmod(managed, 0o600)
            result = self.device.migrate_network_profiles(
                root, expected_uid=managed.stat().st_uid)
            self.assertTrue(result["ok"])
            self.assertEqual(["ming-wired.nmconnection"], result["migrated"])
            migrated = managed.read_text(encoding="utf-8")
            self.assertIn("autoconnect = true", migrated)
            self.assertNotIn("interface-name", migrated)
            self.assertNotIn("mac-address", migrated)

    def test_profile_migration_preserves_user_static_and_8021x_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            profiles = {
                "user-static.nmconnection": (
                    "[connection]\nid=Office Static\ntype=802-3-ethernet\ninterface-name=eno1\n"
                    "[ipv4]\nmethod=manual\naddresses=192.0.2.4/24\n"),
                "ming-enterprise.nmconnection": (
                    "[connection]\nid=Ming Enterprise\ntype=802-3-ethernet\ninterface-name=eno1\n"
                    "[802-1x]\neap=peap;\n[ipv4]\nmethod=auto\n"),
                "ming-route.nmconnection": (
                    "[connection]\nid=Ming Routed\ntype=802-3-ethernet\ninterface-name=eno1\n"
                    "[ipv4]\nmethod=auto\nroute1=198.51.100.0/24,192.0.2.1\n"),
            }
            before = {}
            for name, content in profiles.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                os.chmod(path, 0o600)
                before[name] = path.read_bytes()
            uid = (root / "user-static.nmconnection").stat().st_uid
            result = self.device.migrate_network_profiles(root, expected_uid=uid)
            self.assertTrue(result["ok"])
            self.assertEqual([], result["migrated"])
            for name, content in before.items():
                self.assertEqual(content, (root / name).read_bytes())

    def test_wifi_drop_policy_is_connection_scoped_and_never_restarts_nm(self):
        self.assertIn("80-ming-wifi-reliability", BASE)
        dispatcher = BASE.split(
            "cat > /etc/NetworkManager/dispatcher.d/80-ming-wifi-reliability", 1
        )[1].split("MINGWIFIRELIABILITY", 2)[1]
        self.assertIn('connection modify uuid "${CONNECTION_UUID}" 802-11-wireless.powersave 2', dispatcher)
        self.assertNotIn("systemctl restart NetworkManager", dispatcher)

    def test_network_profile_migration_is_in_boot_order_and_rootfs_gated(self):
        self.assertIn("ming-network-profile-migrate.service", BASE)
        self.assertIn("Before=NetworkManager.service", BASE)
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("ming-network-profile-migrate.service", build)
        self.assertIn("80-ming-wifi-reliability", build)

    def test_network_notification_nmcli_fallback_uses_utf8_c_locale(self):
        source = (ROOT / "assets" / "ming-connection-notify.py").read_text(encoding="utf-8")
        self.assertIn('["env", "LC_ALL=C.UTF-8", "nmcli", "monitor"]', source)


if __name__ == "__main__":
    unittest.main()
