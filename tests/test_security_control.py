import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset():
    path = ROOT / "assets" / "ming-security-control.py"
    if not path.is_file():
        raise AssertionError("missing runtime asset: ming-security-control.py")
    spec = importlib.util.spec_from_file_location("ming_security_control_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecurityControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset()

    def test_firewall_only_manages_ming_owned_table(self):
        rules = self.api.firewall_rules(self.api.DEFAULT_STATE)
        self.assertNotIn("destroy table inet ming_filter", rules)
        self.assertIn("table inet ming_filter", rules)
        self.assertNotIn("flush ruleset", rules)

    def test_firewall_can_create_owned_table_when_it_does_not_exist(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((command, input_text))
            if command == ["nft", "list", "table", "inet", "ming_filter"]:
                return 1, "", "table is absent"
            if command == ["nft", "list", "tables"]:
                return 0, "table inet another_product", ""
            return 0, "", ""

        candidate = self.api.firewall_rules(self.api.DEFAULT_STATE)
        result = self.api.apply_firewall_atomic(candidate, runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(candidate, calls[-1][1])

    def test_firewall_snapshot_error_is_not_treated_as_an_empty_ruleset(self):
        def runner(command, input_text=None):
            if command == ["nft", "list", "table", "inet", "ming_filter"]:
                return 1, "", "netlink permission denied"
            if command == ["nft", "list", "tables"]:
                return 1, "", "netlink permission denied"
            self.fail("must not apply rules without establishing prior state")

        result = self.api.apply_firewall_atomic(
            self.api.firewall_rules(self.api.DEFAULT_STATE), runner=runner)
        self.assertFalse(result["ok"])
        self.assertIn("permission denied", result["error"])

    def test_firewall_commit_failure_restores_exact_owned_snapshot(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((command, input_text))
            if command == ["nft", "list", "table", "inet", "ming_filter"]:
                return 0, "table inet old {}", ""
            if command[:3] == ["nft", "-c", "-f"]:
                return 0, "", ""
            if command[:2] == ["nft", "-f"] and len(calls) == 3:
                return 1, "", "commit failed"
            return 0, "", ""

        result = self.api.apply_firewall_atomic("table inet ming {}", runner=runner)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(
            "destroy table inet ming_filter\ntable inet ming {}", calls[1][1])
        self.assertEqual("destroy table inet ming_filter\ntable inet old {}", calls[-1][1])

    def test_ssh_status_requires_service_config_listener_and_firewall(self):
        status = self.api.build_status(
            state=dict(self.api.DEFAULT_STATE, ssh=True),
            probes={"ssh_installed": True, "ssh_enabled": True, "ssh_active": True,
                    "ssh_firewall_allowed": True, "nftables_enabled": True,
                    "nftables_active": True, "nft_rules_loaded": True,
                    "nft_policy": "drop", "effective_profile": "public",
                    "updates_enabled": True, "updates_active": True},
        )
        self.assertEqual(
            {"installed": True, "enabled": True, "active": True,
             "firewall_allowed": True}, status["ssh"])

    def test_security_updates_write_and_readback(self):
        def runner(command, input_text=None):
            if command == ["systemctl", "is-enabled", "apt-daily-upgrade.timer"]:
                return 0, "enabled", ""
            if command == ["systemctl", "is-active", "apt-daily-upgrade.timer"]:
                return 0, "active", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            result = self.api.mutate(
                "security-updates", "on", path=root / "state.json",
                apt_path=root / "20auto-upgrades", runner=runner)
            self.assertTrue(result["ok"])
            self.assertIn('APT::Periodic::Enable "1";',
                          (root / "20auto-upgrades").read_text(encoding="utf-8"))

    def test_firewall_readback_mismatch_rolls_back_and_fails(self):
        def runner(command, input_text=None):
            if command == ["nft", "list", "table", "inet", "ming_filter"]:
                return 0, "chain input { policy accept; }", ""
            if command[:3] == ["nft", "-c", "-f"] or command[:2] == ["nft", "-f"]:
                return 0, "", ""
            if command == ["systemctl", "is-enabled", "nftables.service"]:
                return 0, "enabled", ""
            if command == ["systemctl", "is-active", "nftables.service"]:
                return 0, "active", ""
            return 1, "", "missing"

        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            result = self.api.mutate(
                "firewall", "on", path=root / "state.json",
                rules_path=root / "nftables.conf",
                apt_path=root / "20auto-upgrades",
                runner=runner)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertIn("readback mismatch", result["error"])

    def test_ssh_readback_requires_service_and_firewall_layers(self):
        observed = self.api.build_status(
            state=dict(self.api.DEFAULT_STATE, ssh=True),
            probes={"ssh_enabled": True, "ssh_active": False,
                    "ssh_firewall_allowed": True})
        self.assertFalse(self.api.desired_matches("ssh", True, observed))


class SecurityDeploymentContracts(unittest.TestCase):
    def test_factory_firewall_config_only_owns_ming_table(self):
        source = (ROOT / "config" / "security" / "nftables.conf").read_text(
            encoding="utf-8")
        self.assertIn("table inet ming_filter", source)
        self.assertNotIn("flush ruleset", source)
        self.assertNotIn("destroy table inet ming_filter", source)

    def test_security_module_deploys_control_and_polkit_policy(self):
        source = (ROOT / "modules" / "05_security_tools.sh").read_text(encoding="utf-8")
        for marker in (
            "ming-security-control.py",
            "/usr/local/sbin/ming-security-control",
            "org.ming.security.control.policy",
            "py_compile",
        ):
            self.assertIn(marker, source)

    def test_settings_uses_security_control_for_mutations(self):
        settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
        self.assertIn('["pkexec", "/usr/local/sbin/ming-security-control"', settings)
        self.assertIn('["/usr/local/sbin/ming-security-control", "status", "--json"]', settings)

    def test_rootfs_gate_requires_security_control_and_policy(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn('"usr/local/sbin/ming-security-control"', build)
        self.assertIn('"usr/share/polkit-1/actions/org.ming.security.control.policy"', build)
        self.assertIn(
            'validate_generated_executable("usr/local/sbin/ming-security-control", "python")',
            build)
