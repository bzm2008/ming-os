import importlib.util
import pathlib
import re
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset(filename, module_name):
    path = ROOT / "assets" / filename
    if not path.is_file():
        raise AssertionError("missing runtime asset: %s" % filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityBuildContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        cls.resume = (ROOT / "resume_build.sh").read_text(encoding="utf-8")
        cls.security_module = (ROOT / "modules" / "05_security_tools.sh").read_text(
            encoding="utf-8")
        cls.base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        cls.desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        cls.ota = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
        cls.settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")

    @staticmethod
    def module_sequence(source):
        block = source.split("local modules=(", 1)[1].split(")", 1)[0]
        return re.findall(r'"(\d\d_[^"]+\.sh)"', block)

    def test_full_and_resume_builds_run_identical_security_sequence(self):
        expected = [
            "01_base.sh", "02_apps.sh", "03_desktop.sh", "04_garlic_claw.sh",
            "05_security_tools.sh", "06_ota_update.sh", "08_settings_hub.sh",
            "07_finalize.sh",
        ]
        self.assertEqual(expected, self.module_sequence(self.build))
        self.assertEqual(expected, self.module_sequence(self.resume))

    def test_security_module_is_strict_and_uses_debian_nftables_service(self):
        self.assertIn("set -euo pipefail", self.security_module)
        self.assertIn("systemctl enable nftables.service", self.security_module)
        self.assertNotIn("ming-firewall.service", self.security_module)
        self.assertNotIn("rkhunter", self.security_module.lower())
        self.assertNotIn("lynis", self.security_module.lower())

    def test_security_helpers_and_polkit_policy_are_deployed(self):
        for marker in [
            "ming-security-control", "ming-account-control",
            "org.ming.security.control.policy", "org.ming.account.control.policy",
        ]:
            self.assertIn(marker, self.security_module)
        self.assertIn("allow_active", self.security_module)
        self.assertNotIn("sudo -n", self.settings)
        self.assertIn('["pkexec", "/usr/local/sbin/ming-security-control"', self.settings)
        self.assertIn('["pkexec", "/usr/local/sbin/ming-account-control"', self.settings)

    def test_rootfs_gate_requires_new_security_and_connection_runtime(self):
        for marker in [
            'require_file("usr/local/sbin/ming-security-control"',
            'require_file("usr/local/sbin/ming-account-control"',
            'require_file("etc/nftables.conf"',
            'require_file("usr/share/polkit-1/actions/org.ming.security.control.policy"',
            'require_file("usr/share/polkit-1/actions/org.ming.account.control.policy"',
            'require_file("usr/local/bin/ming-connection-notify"',
            'require_file("home/user/.config/autostart/ming-connection-notify.desktop"',
        ]:
            self.assertIn(marker, self.build)

    def test_new_install_has_no_known_password_or_unrestricted_nopasswd(self):
        self.assertNotIn('echo "${MING_USER}:${MING_USER_PASS}" | chpasswd', self.base)
        self.assertNotIn("NOPASSWD: ALL", self.base)
        self.assertIn('passwd -l root', self.base)
        self.assertIn('passwd -d "${MING_USER}"', self.base)
        for build_entry in ("build_onion_os.sh", "continue_build.sh", "incremental_upgrade.sh"):
            source = (ROOT / build_entry).read_text(encoding="utf-8")
            self.assertNotIn('ROOT_PASS="root"', source)
            self.assertNotIn('MING_USER_PASS="user"', source)

    def test_oobe_and_ota_clear_only_skipped_passwords_with_readback(self):
        self.assertIn("ming-account-control clear-password", self.desktop)
        self.assertIn("passwd -S", self.desktop)
        self.assertIn('== "skipped"', self.ota)
        self.assertIn("ming-account-control clear-password", self.ota)
        self.assertNotIn("pkexec /bin/bash", self.desktop)

    def test_passwordless_lock_bypasses_authentication(self):
        lock = self.desktop.split("cat > /usr/local/bin/ming-lock", 1)[1].split(
            "MINGLOCK", 2)[1]
        self.assertIn("ming-account-control status --json", lock)
        self.assertIn('"password_set": false', lock)

    def test_networkmanager_is_the_only_network_owner_and_no_r816x_preload(self):
        self.assertIn("systemctl enable NetworkManager.service", self.base)
        for service in ("networking.service", "systemd-networkd.service"):
            self.assertIn("systemctl disable --now %s" % service, self.base)
        network_modules = self.base.split(
            "cat > /etc/modules-load.d/ming-network.conf", 1)[1].split("STATICNETMOD", 2)[1]
        self.assertNotRegex(network_modules, r"(?m)^r816[89]$")
        self.assertIn("connection.zone=public", self.security_module)

    def test_settings_has_plain_language_security_and_wired_status(self):
        self.assertIn('"安全"', self.settings)
        self.assertIn("build_security", self.settings)
        self.assertIn("ethernet-status", self.settings)
        self.assertNotIn("nft list ruleset", self.settings)


class SecurityControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset("ming-security-control.py", "ming_security_control")

    def test_atomic_firewall_apply_rolls_back_after_commit_failure(self):
        commands = []

        def runner(command, input_text=None):
            commands.append((tuple(command), input_text))
            if command[:3] == ["nft", "list", "ruleset"]:
                return 0, "table inet old {}", ""
            if command[:3] == ["nft", "-c", "-f"]:
                return 0, "", ""
            if command[:2] == ["nft", "-f"] and len(commands) == 3:
                return 1, "", "commit failed"
            return 0, "", ""

        result = self.api.apply_firewall_atomic("table inet ming {}", runner=runner)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual("flush ruleset\ntable inet old {}", commands[-1][1])

    def test_failed_ssh_service_change_restores_previous_service_state(self):
        commands = []

        def runner(command, input_text=None):
            commands.append(tuple(command))
            if command[:3] == ["nft", "list", "ruleset"]:
                return 0, "table inet old {}", ""
            if command == ["systemctl", "enable", "--now", "ssh.service"]:
                return 1, "", "start failed"
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            result = self.api.mutate(
                "ssh", "on", path=pathlib.Path(tempdir) / "state.json",
                rules_path=pathlib.Path(tempdir) / "nftables.conf", runner=runner)
        self.assertFalse(result["ok"])
        self.assertIn(("systemctl", "disable", "--now", "ssh.service"), commands)

    def test_status_reports_four_independent_ssh_layers(self):
        status = self.api.build_status(
            state={"firewall": True, "profile": "public", "ssh": False,
                   "security_updates": True},
            probes={"ssh_installed": True, "ssh_enabled": False,
                    "ssh_active": False, "ssh_firewall_allowed": False},
        )
        self.assertEqual(
            {"installed": True, "enabled": False, "active": False,
             "firewall_allowed": False}, status["ssh"])
        self.assertEqual("public", status["profile"])

    def test_firewall_mutation_persists_validated_rules_for_reboot(self):
        def runner(command, input_text=None):
            if command[:3] == ["nft", "list", "ruleset"]:
                return 0, "table inet old {}", ""
            if command[:2] == ["systemctl", "list-unit-files"]:
                return 1, "", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            state_path = pathlib.Path(tempdir) / "control.json"
            rules_path = pathlib.Path(tempdir) / "nftables.conf"
            result = self.api.mutate(
                "firewall", "off", path=state_path, rules_path=rules_path,
                runner=runner)
            self.assertTrue(result["ok"])
            self.assertIn("policy accept", rules_path.read_text(encoding="utf-8"))
            self.assertFalse(self.api.load_state(state_path)["firewall"])

    def test_home_profile_has_lan_discovery_rules_public_does_not(self):
        public = self.api.firewall_rules(dict(self.api.DEFAULT_STATE, profile="public"))
        home = self.api.firewall_rules(dict(self.api.DEFAULT_STATE, profile="home"))
        self.assertNotIn("udp dport 5353", public)
        self.assertIn("udp dport 5353", home)

    def test_quick_check_uses_real_ssh_and_root_account_probes(self):
        def runner(command, input_text=None):
            if command == ["sshd", "-T"]:
                return 0, "permitrootlogin yes\npermitemptypasswords yes", ""
            if command == ["passwd", "-S", "root"]:
                return 0, "root P 2026-07-14 0 99999 7 -1", ""
            return 1, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            result = self.api.quick_check(
                pathlib.Path(tempdir) / "missing.json", runner=runner)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["root_login_disabled"])
        self.assertFalse(result["checks"]["empty_passwords_disabled"])
        self.assertFalse(result["checks"]["root_account_locked"])


class AccountControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset("ming-account-control.py", "ming_account_control")

    def test_set_password_passes_secret_only_on_stdin_and_reads_back(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            if command[:2] == ["passwd", "-S"]:
                return 0, "user P 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        result = self.api.set_password("user", "secret\n", runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(("chpasswd",), calls[0][0])
        self.assertEqual("user:secret\n", calls[0][1])
        self.assertNotIn("secret", " ".join(calls[0][0]))
        self.assertEqual(("passwd", "-S", "user"), calls[-1][0])

    def test_clear_password_verifies_passwordless_status(self):
        calls = []

        def runner(command, input_text=None):
            calls.append(tuple(command))
            if command[:2] == ["passwd", "-S"]:
                return 0, "user NP 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        result = self.api.clear_password("user", runner=runner)
        self.assertTrue(result["ok"])
        self.assertFalse(result["password_set"])
        self.assertEqual(("passwd", "-d", "user"), calls[0])

    def test_pkexec_caller_can_only_change_its_own_account(self):
        class Record:
            pw_name = "alice"

        lookup = lambda uid: Record()
        self.assertTrue(self.api.caller_may_change("alice", {"PKEXEC_UID": "1000"}, lookup))
        self.assertFalse(self.api.caller_may_change("bob", {"PKEXEC_UID": "1000"}, lookup))
        self.assertTrue(self.api.caller_may_change("bob", {}, lookup))


class EthernetAndNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.devices = load_asset("ming-device-control.py", "ming_device_control_security")
        cls.bridge = load_asset("ming-connection-notify.py", "ming_connection_notify")

    def test_ethernet_status_is_structured_and_does_not_expose_secrets(self):
        outputs = {
            ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"):
                (0, "enp2s0:ethernet:connected", ""),
            ("nmcli", "-t", "-f", "GENERAL.DRIVER,WIRED-PROPERTIES.CARRIER,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,IP4.DHCP4.OPTION",
             "device", "show", "enp2s0"):
                (0, "GENERAL.DRIVER:r8169\nWIRED-PROPERTIES.CARRIER:yes\n"
                    "IP4.ADDRESS[1]:192.0.2.5/24\nIP4.GATEWAY:192.0.2.1\n"
                    "IP4.DNS[1]:192.0.2.53\nIP4.DHCP4.OPTION[1]:dhcp_lease_time = 3600", ""),
        }

        controller = self.devices.DeviceController(
            runner=lambda command, timeout=8: outputs.get(tuple(command), (1, "", "missing")),
            executable=lambda name: "/usr/bin/" + name,
        )
        result = controller.ethernet_status()
        self.assertEqual("enp2s0", result["devices"][0]["device"])
        self.assertEqual("r8169", result["devices"][0]["driver"])
        self.assertTrue(result["devices"][0]["carrier"])
        self.assertEqual("192.0.2.1", result["devices"][0]["route"])
        self.assertIn("dhcp", result["devices"][0])

    def test_connection_notifications_are_deduplicated_and_sanitized(self):
        cache = self.bridge.NotificationDeduplicator(window_seconds=10)
        event = {"kind": "network", "state": "connected", "label": "Home\npassword=secret"}
        first = self.bridge.build_notification(event, cache=cache, now=100)
        second = self.bridge.build_notification(event, cache=cache, now=105)
        third = self.bridge.build_notification(event, cache=cache, now=111)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        self.assertNotIn("password", first["body"].lower())
        self.assertNotIn("secret", first["body"].lower())

    def test_notification_bridge_deployment_is_user_session_only(self):
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        self.assertIn("ming-connection-notify", desktop)
        self.assertIn("X-GNOME-Autostart-enabled=true", desktop)
        self.assertIn("NetworkManager", self.bridge.__doc__ or "")
        self.assertIn("BlueZ", self.bridge.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
