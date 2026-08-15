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
            read_text=lambda path: values.get(str(path).replace("\\", "/")),
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

    def test_status_exposes_policy_cgroup_timer_and_oom_sections(self):
        runner = RecordingRunner(self.module)
        service = self.module.PerformanceStatus(
            runner=runner,
            read_text=lambda _path: None,
            globber=lambda _pattern: [],
        )

        payload = service.status()

        self.assertIn("cgroup", payload)
        self.assertIn("policy", payload)
        self.assertIn("timers", payload)
        self.assertIn("oom", payload)
        self.assertIn(payload["oom"]["backend"], {"none", "earlyoom", "systemd-oomd", "conflict"})
        self.assertIn("degraded", payload["policy"])

    def test_status_reads_runtime_oom_and_timer_evidence(self):
        values = {
            "/sys/fs/cgroup/cgroup.controllers": "cpu io memory pids\n",
            "/proc/sys/kernel/timer_migration": "1\n",
            "/boot/config-test": "CONFIG_NO_HZ_IDLE=y\n",
            "/run/ming-os/oom-policy": "backend=earlyoom\nforeground_protected=false\n",
        }
        service = self.module.PerformanceStatus(
            runner=RecordingRunner(self.module),
            read_text=lambda path: values.get(str(path).replace(chr(92), "/")),
            globber=lambda pattern: ["/boot/config-test"] if pattern == "/boot/config-*" else [],
        )

        payload = service.status()

        self.assertEqual(2, payload["cgroup"]["version"])
        self.assertEqual("earlyoom", payload["oom"]["backend"])
        self.assertFalse(payload["oom"]["foreground_protected"])
        self.assertTrue(payload["timers"]["timer_migration"])
        self.assertTrue(payload["timers"]["no_hz_idle"])


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

    def test_base_main_fails_fast_when_performance_status_cannot_deploy(self):
        main = BASE.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("deploy_performance_status || return 1", main)

    def test_low_memory_profile_caps_cache_pressure_and_uses_one_oom_backend(self):
        self.assertNotIn("vfs_cache_pressure=120", BASE)
        self.assertIn("ming-oom-policy", BASE)
        self.assertNotIn("--prefer '^(firefox|chromium|code)$'", BASE)

    def test_oom_policy_is_deployed_as_a_runtime_selector(self):
        for marker in (
            "/usr/local/sbin/ming-oom-policy",
            "systemd-oomd",
            "earlyoom",
            "cgroup.controllers",
            "backend=",
            "ming-oom-policy.service",
        ):
            self.assertIn(marker, BASE)

    def test_oom_policy_keeps_a_real_global_guard_when_no_managed_oom_scope_exists(self):
        """cgroup v2 alone is not an oomd policy and must not disable earlyoom."""
        policy = BASE.split(
            "cat > /usr/local/sbin/ming-oom-policy << 'MINGOOMPOLICY'", 1
        )[1].split("MINGOOMPOLICY", 1)[0]

        self.assertIn('backend=earlyoom', policy)
        self.assertIn('systemctl enable --now earlyoom.service', policy)
        self.assertIn('systemctl disable --now systemd-oomd.service', policy)
        self.assertNotIn('if has_unified_memory && unit_available systemd-oomd.service', policy)
        self.assertIn('EARLYOOM_ARGS="-m 8 -s 12', BASE)


if __name__ == "__main__":
    unittest.main()
