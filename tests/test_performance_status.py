import importlib.util
import io
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PERFORMANCE_STATUS = ROOT / "assets" / "ming-performance-status.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def load_performance_status():
    spec = importlib.util.spec_from_file_location("ming_performance_status", PERFORMANCE_STATUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingRunner:
    def __init__(self, module, return_code=127, stderr="command unavailable"):
        self.module = module
        self.return_code = return_code
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, timeout=99):
        self.calls.append((tuple(argv), timeout))
        return self.module.CommandResult(self.return_code, "", self.stderr)


class PerformanceStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_performance_status()

    def test_missing_commands_keep_a_stable_json_schema_and_success_exit(self):
        runner = RecordingRunner(self.module)
        service = self.module.PerformanceStatus(
            runner=runner,
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )

        payload = service.status()

        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["schema_version"])
        self.assertIn("boot", payload)
        self.assertIn("memory", payload)
        self.assertIn("cpu", payload)
        self.assertIn("storage", payload)
        self.assertIn("temperatures", payload)
        self.assertIn("services", payload)
        for name in ("ModemManager", "CUPS", "Avahi", "BlueZ", "Picom", "Dock"):
            self.assertIn(name, payload["services"])
        self.assertTrue(payload["diagnostics"])

    def test_every_external_probe_uses_a_short_timeout(self):
        runner = RecordingRunner(self.module)
        service = self.module.PerformanceStatus(
            runner=runner,
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )

        service.status()

        self.assertTrue(runner.calls)
        self.assertLessEqual(max(timeout for _argv, timeout in runner.calls), 2.0)

    def test_timeout_probe_is_reported_as_diagnostic_not_an_exception(self):
        runner = RecordingRunner(self.module, return_code=124, stderr="timed out")
        service = self.module.PerformanceStatus(
            runner=runner,
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )

        payload = service.status()

        self.assertTrue(payload["ok"])
        self.assertEqual("unavailable", payload["boot"]["state"])
        self.assertTrue(any("timed out" in item for item in payload["diagnostics"]))

    def test_cli_status_json_emits_parseable_output_without_hardware(self):
        runner = RecordingRunner(self.module)
        service = self.module.PerformanceStatus(
            runner=runner,
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )
        output = io.StringIO()

        exit_code = self.module.main(
            ["status", "--json"], service=service, stdout=output
        )

        self.assertEqual(0, exit_code)
        self.assertIsInstance(json.loads(output.getvalue()), dict)

    def test_metrics_cli_emits_three_display_modes(self):
        service = self.module.PerformanceStatus(
            runner=RecordingRunner(self.module),
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )
        output = io.StringIO()
        exit_code = self.module.main(["metrics", "--json"], service=service, stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual({"memory", "cpu", "network"}, set(payload) & {"memory", "cpu", "network"})

    def test_status_reports_policy_cgroup_timer_and_oom_sections(self):
        service = self.module.PerformanceStatus(
            runner=RecordingRunner(self.module),
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )
        payload = service.status()
        for key in ("cgroup", "policy", "timers", "oom"):
            self.assertIn(key, payload)

    def test_cpu_status_classifies_amd_and_zhaoxin_without_intel_assumptions(self):
        values = {
            "/proc/cpuinfo": (
                "vendor_id\t:  Shanghai\n"
                "model name\t: Zhaoxin KaiXian KX-6000\n"
                "microcode\t: 0x123\n"
            ),
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver": "acpi-cpufreq\n",
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor": "schedutil\n",
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors": "schedutil powersave\n",
        }
        service = self.module.PerformanceStatus(
            runner=RecordingRunner(self.module),
            read_text=lambda path: values.get(str(path)),
            globber=lambda pattern: ["/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"]
            if "scaling_governor" in pattern else [],
        )

        status = service.cpu_status()

        self.assertEqual("zhaoxin", status["compatibility_class"])
        self.assertEqual("Shanghai", status["vendor"])
        self.assertEqual("Zhaoxin KaiXian KX-6000", status["model"])
        self.assertEqual("0x123", status["microcode"])
        self.assertEqual("acpi-cpufreq", status["driver"])
        self.assertEqual("kernel-tlp", status["thermal_strategy"])


class PerformanceStatusDeploymentTests(unittest.TestCase):
    def test_base_module_deploys_the_bounded_performance_status_helper(self):
        for marker in (
            "ming-performance-status.py",
            "/usr/local/sbin/ming-performance-status",
            "install -m 0755",
            "deploy_performance_status",
        ):
            self.assertIn(marker, BASE)

    def test_rootfs_gate_validates_and_executes_performance_status_json(self):
        for marker in (
            "usr/local/sbin/ming-performance-status",
            "ming-performance-status status --json",
            "schema_version",
            'json.load(sys.stdin)',
            'validate_generated_executable(relative_path, "python")',
        ):
            self.assertIn(marker, BUILD)

    def test_rootfs_gate_checks_prefetch_index_contract(self):
        for marker in ("record_application_index", "load_application_index", "index.json"):
            self.assertIn(marker, BUILD)

    def test_base_main_fails_fast_when_performance_status_cannot_deploy(self):
        main = BASE.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("deploy_performance_status || return 1", main)

    def test_base_deploys_policy_and_prefetch_without_enabling_preload(self):
        for marker in (
            "deploy_performance_policy",
            "ming-performance-policy.py",
            "ming-prefetch.py",
            "ming-resource-policy.service",
            "ming-oom-profile.service",
        ):
            self.assertIn(marker, BASE)
        self.assertIn("不默认启用 preload", BASE)

    def test_policy_deployment_fails_closed_when_generated_aliases_are_empty(self):
        for marker in (
            "[[ -s /usr/local/bin/ming-prefetch ]]",
            "[[ -s /usr/local/bin/${alias} ]]",
            "[[ -s /etc/systemd/system/ming-resource-policy.service ]]",
            "[[ -s /usr/local/sbin/ming-oom-profile ]]",
            "[[ -s /etc/systemd/system/ming-oom-profile.service ]]",
        ):
            self.assertIn(marker, BASE)


if __name__ == "__main__":
    unittest.main()
