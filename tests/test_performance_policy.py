import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "assets" / "ming-performance-policy.py"
STATUS = ROOT / "assets" / "ming-performance-status.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
OTA = (ROOT / "modules" / "06_ota_update.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")


def load_policy():
    spec = importlib.util.spec_from_file_location("ming_performance_policy", POLICY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_status():
    spec = importlib.util.spec_from_file_location("ming_performance_status", STATUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proc_stat(pid, name, *, nice=0, starttime="1"):
    fields = ["S"] + ["0"] * 15 + [str(nice), "0", "1", str(starttime)]
    return f"{pid} ({name}) {' '.join(fields)}\n"


class PerformancePolicyPureTests(unittest.TestCase):
    def test_policy_import_does_not_require_posix_geteuid(self):
        missing = object()
        original = getattr(os, "geteuid", missing)
        if original is not missing:
            delattr(os, "geteuid")
        try:
            try:
                module = load_policy()
            except AttributeError as exc:
                self.fail(f"policy import must not require os.geteuid: {exc}")
        finally:
            if original is not missing:
                setattr(os, "geteuid", original)

        self.assertTrue(hasattr(module, "PerformancePolicy"))

    def test_interaction_boost_rejects_pid_starttime_mismatch_before_changes(self):
        module = load_policy()
        with tempfile.TemporaryDirectory() as directory:
            proc = pathlib.Path(directory) / "proc" / "123"
            proc.mkdir(parents=True)
            (proc / "stat").write_text(proc_stat(123, "demo", nice=20, starttime="555"), encoding="ascii")
            (proc / "status").write_text("Name:\tdemo\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii")
            service = module.PerformancePolicy(
                proc_root=pathlib.Path(directory) / "proc",
                runtime_dir=pathlib.Path(directory) / "run",
                command_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("must not change scheduling on stale PID")),
                uid_getter=lambda: 1000,
                clock=lambda: 10.0,
            )

            result = service.begin(123, "999", "launch")

        self.assertFalse(result["ok"])
        self.assertEqual("E_PID_STALE", result["error_code"])

    def test_background_policy_restores_saved_state_for_the_same_pid_starttime(self):
        module = load_policy()
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            proc = pathlib.Path(directory) / "proc" / "321"
            proc.mkdir(parents=True)
            (proc / "stat").write_text(proc_stat(321, "app", nice=7, starttime="888"), encoding="ascii")
            (proc / "status").write_text("Name:\tapp\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii")
            service = module.PerformancePolicy(
                proc_root=pathlib.Path(directory) / "proc",
                runtime_dir=pathlib.Path(directory) / "run",
                command_runner=lambda command, timeout=1: commands.append(tuple(command)) or (0, "", ""),
                uid_getter=lambda: 0,
                clock=lambda: 20.0,
            )

            hidden = service.apply_background(321, "888", "/usr/share/applications/demo.desktop", False)
            visible = service.apply_background(321, "888", "/usr/share/applications/demo.desktop", True)

        self.assertTrue(hidden["ok"])
        self.assertTrue(visible["ok"])
        self.assertIn(("renice", "-n", "+10", "-p", "321"), commands)
        self.assertIn(("renice", "-n", "7", "-p", "321"), commands)

    def test_unprivileged_background_policy_is_a_reversible_noop(self):
        module = load_policy()
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            proc = pathlib.Path(directory) / "proc" / "654"
            proc.mkdir(parents=True)
            (proc / "stat").write_text(proc_stat(654, "app", nice=0, starttime="999"), encoding="ascii")
            (proc / "status").write_text("Name:\tapp\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii")
            service = module.PerformancePolicy(
                proc_root=pathlib.Path(directory) / "proc",
                runtime_dir=pathlib.Path(directory) / "run",
                command_runner=lambda command, timeout=1: commands.append(tuple(command)) or (0, "", ""),
                uid_getter=lambda: 1000,
                clock=lambda: 20.0,
            )

            result = service.apply_background(
                654, "999", "/usr/share/applications/demo.desktop", False)

        self.assertTrue(result["ok"])
        self.assertEqual([], commands)
        self.assertIn("privileged-policy-unavailable", result["degraded"])

    def test_status_json_reports_degraded_when_cgroup_is_unavailable(self):
        module = load_policy()
        with tempfile.TemporaryDirectory() as directory:
            service = module.PerformancePolicy(
                proc_root=pathlib.Path(directory) / "proc",
                runtime_dir=pathlib.Path(directory) / "run",
                cgroup_root=pathlib.Path(directory) / "cgroup",
            )
            output = io.StringIO()
            rc = module.main(["status", "--json"], service=service, stdout=output)

        self.assertEqual(0, rc)
        payload = json.loads(output.getvalue())
        self.assertIn("cgroup-unavailable", payload["policy"]["degraded"])

    def test_policy_publishes_current_state_and_prunes_expired_interaction_leases(self):
        module = load_policy()
        commands = []
        now = [10.0]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            proc = root / "proc" / "741"
            proc.mkdir(parents=True)
            (proc / "stat").write_text(
                proc_stat(741, "app", nice=0, starttime="444"), encoding="ascii")
            (proc / "status").write_text(
                "Name:\tapp\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii")
            snapshot = root / "resource-policy.json"
            service = module.PerformancePolicy(
                proc_root=root / "proc",
                runtime_dir=root / "run",
                status_path=snapshot,
                command_runner=lambda command, timeout=1: commands.append(tuple(command)) or (0, "", ""),
                uid_getter=lambda: 0,
                clock=lambda: now[0],
            )

            started = service.begin(741, "444", "launch")
            active = json.loads(snapshot.read_text(encoding="utf-8"))
            now[0] = 12.0
            expired = service.status()
            cleared = json.loads(snapshot.read_text(encoding="utf-8"))

        self.assertTrue(started["ok"])
        self.assertEqual(1, active["policy"]["active_leases"])
        self.assertEqual("adaptive", active["mode"])
        self.assertEqual(1, active["active_leases"])
        self.assertEqual(0, expired["policy"]["active_leases"])
        self.assertEqual(0, cleared["policy"]["active_leases"])
        self.assertIn(("renice", "-n", "0", "-p", "741"), commands)

    def test_status_reader_consumes_the_published_policy_snapshot(self):
        module = load_status()
        state = json.dumps({
            "ok": True,
            "mode": "adaptive",
            "active_leases": 2,
            "background_throttled": 1,
            "degraded": ["renice-unavailable"],
            "policy": {
                "mode": "adaptive",
                "active_leases": 2,
                "background_throttled": 1,
                "degraded": ["renice-unavailable"],
            },
        })
        service = module.PerformanceStatus(
            read_text=lambda path: state if str(path) == "/run/ming-os/resource-policy.json" else None,
            runner=lambda *_args, **_kwargs: module.CommandResult(127, "", "", missing=True),
            globber=lambda _pattern: [],
        )

        payload = service.policy_status()

        self.assertEqual("adaptive", payload["mode"])
        self.assertEqual(2, payload["active_leases"])
        self.assertEqual(1, payload["background_throttled"])
        self.assertIn("renice-unavailable", payload["degraded"])
        self.assertNotIn("resource-policy-inactive", payload["degraded"])


class PerformancePolicyBuildContracts(unittest.TestCase):
    def test_ota_wrapper_does_not_repeat_a_failed_wrapped_command(self):
        git_bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not git_bash.is_file():
            self.skipTest("Git Bash is unavailable")

        def git_path(path):
            value = str(path.resolve()).replace("\\", "/")
            return "/%s%s" % (value[0].lower(), value[2:])

        match = re.search(
            r"cat > /usr/local/bin/ming-ota-run << 'MINGOTARUN'\n(.*?)\nMINGOTARUN",
            BASE,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        wrapper = match.group(1).replace("/usr/local/bin/ming-update", "ming-update-test")
        wrapper = wrapper.replace(
            'if [[ -r /sys/fs/cgroup/cgroup.controllers ]] \\\n    && command -v systemd-run >/dev/null 2>&1 \\\n    && systemd-run',
            'if command -v systemd-run >/dev/null 2>&1 && systemd-run',
        )

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            count_file = root / "count.txt"
            wrapper_path = root / "ming-ota-run"
            wrapper_path.write_text(wrapper, encoding="utf-8", newline="\n")
            (bin_dir / "ming-update-test").write_text(
                "#!/usr/bin/env bash\n"
                "echo run >> count.txt\n"
                "exit 42\n",
                encoding="utf-8",
                newline="\n",
            )
            (bin_dir / "systemd-run").write_text(
                "#!/usr/bin/env bash\n"
                "for argument in \"$@\"; do\n"
                "  if [[ \"${argument}\" == \"/bin/sh\" ]]; then\n"
                "    while [[ \"$1\" != \"/bin/sh\" ]]; do shift; done\n"
                "    exec \"$@\"\n"
                "  fi\n"
                "done\n"
                "while [[ $# -gt 0 && $1 != ming-update-test ]]; do shift; done\n"
                'exec "$@"\n',
                encoding="utf-8",
                newline="\n",
            )
            (bin_dir / "nice").write_text(
                "#!/usr/bin/env bash\nshift 2\nexec \"$@\"\n",
                encoding="utf-8",
                newline="\n",
            )
            (bin_dir / "ionice").write_text(
                "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
                encoding="utf-8",
                newline="\n",
            )
            for path in bin_dir.iterdir():
                path.chmod(0o755)
            wrapper_path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{git_path(bin_dir)}:/usr/bin:/bin"

            completed = subprocess.run(
                [str(git_bash), git_path(wrapper_path), "check"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                cwd=root,
                timeout=10,
            )

            runs = count_file.read_text(encoding="utf-8").splitlines()

        self.assertEqual(42, completed.returncode, completed.stderr)
        self.assertEqual(["run"], runs)

    def test_policy_helpers_are_deployed_and_build_validated(self):
        for marker in (
            "ming-performance-policy.py",
            "/usr/local/sbin/ming-interaction-boost",
            "/usr/local/sbin/ming-background-policy",
            "/usr/local/sbin/ming-performance-policy",
            "/usr/local/bin/ming-prefetch",
            "/usr/local/bin/ming-ota-run",
            "ming-ota.slice",
        ):
            self.assertIn(marker, BASE)
        for marker in (
            "usr/local/sbin/ming-performance-policy",
            "usr/local/sbin/ming-interaction-boost",
            "usr/local/sbin/ming-background-policy",
            "usr/local/bin/ming-prefetch",
            "usr/local/bin/ming-ota-run",
            "etc/systemd/system/ming-ota.slice",
        ):
            self.assertIn(marker, BUILD)

    def test_ota_wrapper_is_low_priority_and_does_not_set_memorymax(self):
        self.assertIn("CPUWeight=20", BASE)
        self.assertIn("IOWeight=20", BASE)
        self.assertIn("Nice=10", BASE)
        self.assertIn("IOSchedulingClass=idle", BASE)
        self.assertIn("systemd-run", BASE)
        self.assertIn("ionice -c3", BASE)
        self.assertNotIn("MemoryMax=", BASE)

    def test_tlp_defaults_prioritize_old_hardware_responsiveness_and_radio_stability(self):
        tlp = BASE.split("cat > /etc/tlp.d/ming-laptop.conf << TLPCONF", 1)[1].split(
            "TLPCONF", 1)[0]

        self.assertIn("CPU_SCALING_GOVERNOR_ON_AC=schedutil", tlp)
        self.assertIn("CPU_SCALING_GOVERNOR_ON_BAT=schedutil", tlp)
        self.assertIn("WIFI_PWR_ON_BAT=off", tlp)
        self.assertIn("RUNTIME_PM_ON_BAT=on", tlp)
        self.assertNotIn("CPU_SCALING_GOVERNOR_ON_BAT=powersave", tlp)
        self.assertNotIn("WIFI_PWR_ON_BAT=on", tlp)
        self.assertNotIn("RUNTIME_PM_ON_BAT=auto", tlp)

    def test_ota_checks_use_wrapper_without_modifying_transaction_engine(self):
        self.assertGreaterEqual(OTA.count("/usr/local/bin/ming-ota-run check"), 2)
        service = OTA.split("cat > /etc/systemd/system/ming-update-check.service", 1)[1].split(
            "SYSTEMDSERVICE", 2)[1]
        boot_check = OTA.split("cat > /usr/local/bin/ming-boot-update-check", 1)[1].split(
            "BOOTCHECKSCRIPT", 2)[1]
        self.assertNotIn("/usr/local/bin/ming-update check", service)
        self.assertNotIn("/usr/local/bin/ming-update check", boot_check)
        self.assertIn(
            'exec /usr/local/bin/ming-control-center --page update "$@"', OTA)
        self.assertIn("/usr/local/bin/ming-ota-run", BASE)

    def test_launch_does_not_spawn_an_unprivileged_scheduler_boost_helper(self):
        self.assertNotIn("request_interaction_boost", LAUNCH)
        self.assertNotIn("ming-interaction-boost", LAUNCH)
        self.assertNotIn("process_starttime", LAUNCH)


if __name__ == "__main__":
    unittest.main()
