import importlib.util
import io
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
import inspect
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset(name, module_name):
    path = ROOT / "assets" / name
    if not path.is_file():
        raise AssertionError("missing runtime asset: %s" % name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AccountControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset("ming-account-control.py", "ming_account_control_contract")

    def test_pkexec_caller_can_only_change_its_own_account(self):
        class Record:
            pw_name = "alice"
            pw_uid = 1000

        lookup = lambda _uid: Record()
        self.assertTrue(self.api.caller_may_change("alice", {"PKEXEC_UID": "1000"}, lookup))
        self.assertFalse(self.api.caller_may_change("bob", {"PKEXEC_UID": "1000"}, lookup))
        self.assertFalse(self.api.caller_may_change("bob", {}, lookup))

        class SystemRecord:
            pw_name = "daemon"
            pw_uid = 999

        self.assertFalse(self.api.caller_may_change(
            "daemon", {"PKEXEC_UID": "999"}, lambda _uid: SystemRecord()))

    def test_validate_user_rejects_root_options_and_unknown_accounts(self):
        known = lambda name: object() if name == "alice" else (_ for _ in ()).throw(KeyError(name))
        self.assertEqual("alice", self.api.validate_user("alice", lookup=known))
        for invalid in ("", "root", "-R", "alice:root", "../alice", "ALICE"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.api.validate_user(invalid, lookup=known)

    def test_password_status_parses_passwd_readback(self):
        def runner(command, input_text=None):
            self.assertEqual(["passwd", "-S", "alice"], command)
            self.assertIsNone(input_text)
            return 0, "alice NP 2026-07-14 0 99999 7 -1", ""

        status = self.api.password_status("alice", runner=runner)
        self.assertTrue(status["ok"])
        self.assertFalse(status["password_set"])
        self.assertEqual("NP", status["status"])

    def test_set_password_uses_stdin_and_reads_back(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            if command[:2] == ["passwd", "-S"]:
                return 0, "alice P 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        result = self.api.set_password("alice", "secret\n", runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(("chpasswd",), calls[0][0])
        self.assertEqual("alice:secret\n", calls[0][1])
        self.assertNotIn("secret", " ".join(calls[0][0]))
        self.assertEqual(("passwd", "-S", "alice"), calls[-1][0])

    def test_first_password_setup_succeeds_without_a_skipped_marker(self):
        def runner(command, input_text=None):
            if command[:2] == ["passwd", "-S"]:
                return 0, "alice P 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            missing = pathlib.Path(tempdir) / "ming-os" / "oobe-account-done"
            result = self.api.set_password(
                "alice", "secret\n", runner=runner, marker_path=missing)
        self.assertTrue(result["ok"])

    def test_set_password_rejects_newline_in_secret(self):
        result = self.api.set_password("alice", "first\nsecond\n", runner=lambda *_args, **_kwargs: None)
        self.assertFalse(result["ok"])

    def test_clear_password_reads_back_passwordless_status(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            if command[:2] == ["passwd", "-S"]:
                return 0, "alice NP 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        result = self.api.clear_password("alice", runner=runner)
        self.assertTrue(result["ok"])
        self.assertFalse(result["password_set"])
        self.assertEqual(("passwd", "-d", "alice"), calls[0][0])
        self.assertEqual(("passwd", "-S", "alice"), calls[-1][0])

    def test_marker_rejects_symlink_fifo_and_unsafe_permissions(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            config = root / "ming-os"
            config.mkdir(mode=0o700)
            marker = config / "oobe-account-done"
            victim = root / "victim"
            victim.write_text("skipped\n", encoding="utf-8")
            try:
                marker.symlink_to(victim)
            except OSError:
                self.skipTest("symlink creation unavailable")
            uid = config.stat().st_uid
            self.assertFalse(self.api.retire_skipped_marker("alice", marker_path=marker, expected_uid=uid))
            self.assertEqual("skipped", victim.read_text(encoding="utf-8").strip())
            marker.unlink()
            marker.write_text("skipped\n", encoding="utf-8")
            config.chmod(0o777)
            self.assertFalse(self.api.retire_skipped_marker("alice", marker_path=marker, expected_uid=uid))
            config.chmod(0o700)
            marker.unlink()
            try:
                os_mkfifo = __import__("os").mkfifo
            except AttributeError:
                self.skipTest("fifo creation unavailable")
            os_mkfifo(marker)
            self.assertFalse(self.api.retire_skipped_marker("alice", marker_path=marker, expected_uid=uid))

    def test_marker_rejects_unexpected_owner_and_unsafe_file_mode(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config = pathlib.Path(tempdir) / "ming-os"
            config.mkdir(mode=0o700)
            marker = config / "oobe-account-done"
            marker.write_text("skipped\n", encoding="utf-8")
            actual_uid = marker.stat().st_uid
            self.assertFalse(self.api.retire_skipped_marker(
                "alice", marker_path=marker, expected_uid=actual_uid + 1))
            if os.name != "nt":
                marker.chmod(0o666)
                self.assertFalse(self.api.retire_skipped_marker(
                    "alice", marker_path=marker, expected_uid=actual_uid))

    def test_marker_update_is_private_atomic_and_regular(self):
        if os.name == "nt":
            self.skipTest("POSIX owner and mode semantics are unavailable")
        with tempfile.TemporaryDirectory() as tempdir:
            config = pathlib.Path(tempdir) / "ming-os"
            config.mkdir(mode=0o700)
            marker = config / "oobe-account-done"
            marker.write_text("skipped\n", encoding="utf-8")
            marker.chmod(0o600)
            uid = marker.stat().st_uid
            self.assertTrue(self.api.retire_skipped_marker(
                "alice", marker_path=marker, expected_uid=uid))
            self.assertFalse(marker.is_symlink())
            self.assertTrue(stat.S_ISREG(marker.stat().st_mode))
            self.assertEqual("configured", marker.read_text(encoding="utf-8").strip())
            self.assertFalse(any(config.glob(".oobe-account-done.*")))

    def test_migrate_skipped_is_one_shot(self):
        if os.name == "nt":
            self.skipTest("POSIX owner and mode semantics are unavailable")
        calls = []

        def runner(command, input_text=None):
            calls.append(tuple(command))
            if command[:2] == ["passwd", "-S"]:
                return 0, "alice NP 2026-07-14 0 99999 7 -1", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tempdir:
            config = pathlib.Path(tempdir) / "ming-os"
            config.mkdir(mode=0o700)
            marker = config / "oobe-account-done"
            marker.write_text("skipped\n", encoding="utf-8")
            marker.chmod(0o600)
            uid = marker.stat().st_uid
            first = self.api.migrate_skipped(
                "alice", runner=runner, marker_path=marker, expected_uid=uid)
            call_count = len(calls)
            second = self.api.migrate_skipped(
                "alice", runner=runner, marker_path=marker, expected_uid=uid)
            self.assertEqual("migrated-passwordless", marker.read_text(encoding="utf-8").strip())
        self.assertTrue(first["ok"])
        self.assertTrue(first["migrated"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["migrated"])
        self.assertEqual(call_count, len(calls))


class AdministratorBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_asset(
            "ming-admin-bootstrap.py", "ming_admin_bootstrap_contract")

    def test_bootstrap_is_limited_to_the_active_local_user(self):
        class Record:
            pw_name = "alice"
            pw_uid = 1000

        lookup = lambda _uid: Record()
        self.assertTrue(self.api.caller_matches_user(
            "alice", {"PKEXEC_UID": "1000"}, lookup))
        self.assertFalse(self.api.caller_matches_user(
            "bob", {"PKEXEC_UID": "1000"}, lookup))
        self.assertFalse(self.api.caller_matches_user(
            "alice", {}, lookup))

    def test_bootstrap_refuses_when_an_usable_administrator_exists(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            if command == ["getent", "group", "sudo"]:
                return 0, "sudo:x:27:admin", ""
            if command == ["passwd", "-S", "admin"]:
                return 0, "admin P 2026-07-28 0 99999 7 -1", ""
            return 0, "", ""

        prompts = []
        result = self.api.bootstrap_administrator(
            "alice", runner=runner,
            password_reader=lambda: prompts.append(True) or "secret")
        self.assertFalse(result["ok"])
        self.assertIn("already exists", result["error"])
        self.assertFalse(any(command == ("chpasswd",) for command, _ in calls))
        self.assertEqual([], prompts)

    def test_bootstrap_sets_password_and_admin_group_then_reads_back(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            if command == ["getent", "group", "sudo"]:
                return 0, "sudo:x:27:", ""
            if command == ["passwd", "-S", "alice"]:
                return 0, "alice P 2026-07-28 0 99999 7 -1", ""
            if command == ["id", "-nG", "alice"]:
                return 0, "alice sudo adm", ""
            return 0, "", ""

        result = self.api.bootstrap_administrator(
            "alice", runner=runner, password_reader=lambda: "secret")
        self.assertTrue(result["ok"])
        self.assertIn((("chpasswd",), "alice:secret\n"), calls)
        self.assertIn((("usermod", "-aG", "sudo", "alice"), None), calls)
        self.assertNotIn("secret", " ".join(
            argument for command, _ in calls for argument in command))

    def test_privileged_helper_collects_and_confirms_password_in_a_visible_dialog(self):
        calls = []
        answers = iter(((0, "secret", ""), (0, "secret", "")))

        def runner(command, input_text=None):
            calls.append((tuple(command), input_text))
            return next(answers)

        password = self.api.collect_interactive_password(
            runner=runner, environ={"DISPLAY": ":0"}, executable=lambda _path: True)

        self.assertEqual("secret", password)
        self.assertEqual(2, len(calls))
        self.assertTrue(all(call[0][0] == "/usr/bin/zenity" for call in calls))
        self.assertTrue(all("--password" in call[0] for call in calls))
        self.assertTrue(all(call[1] is None for call in calls))

    def test_bootstrap_entrypoint_no_longer_accepts_caller_stdin(self):
        parameters = inspect.signature(self.api.main).parameters
        self.assertNotIn("stdin", parameters)
        source = pathlib.Path(self.api.__file__).read_text(encoding="utf-8")
        self.assertNotIn("sys.stdin", source)
        self.assertNotIn("readline", source)

    def test_main_holds_root_lock_while_bootstrap_runs(self):
        events = []

        class Lock:
            def __enter__(self):
                events.append("lock-enter")
            def __exit__(self, *_args):
                events.append("lock-exit")

        def bootstrap(_user):
            events.append("bootstrap")
            return {"ok": True}

        output = io.StringIO()
        with mock.patch.object(self.api, "caller_matches_user", return_value=True), \
                mock.patch.object(self.api, "primary_local_user", return_value=True), \
                mock.patch.object(self.api, "bootstrap_administrator", side_effect=bootstrap):
            rc = self.api.main(
                ["--user", "alice"], stdout=output,
                lock_factory=lambda: Lock(), effective_uid=0)

        self.assertEqual(0, rc)
        self.assertEqual(["lock-enter", "bootstrap", "lock-exit"], events)

    def test_status_requires_password_and_sudo_membership(self):
        def ready_runner(command, input_text=None):
            if command == ["passwd", "-S", "alice"]:
                return 0, "alice P 2026-07-28 0 99999 7 -1", ""
            if command == ["id", "-nG", "alice"]:
                return 0, "alice sudo", ""
            return 1, "", "missing"

        ready = self.api.administrator_status("alice", runner=ready_runner)
        self.assertTrue(ready["ready"])

        def passwordless_runner(command, input_text=None):
            if command == ["passwd", "-S", "alice"]:
                return 0, "alice NP 2026-07-28 0 99999 7 -1", ""
            if command == ["id", "-nG", "alice"]:
                return 0, "alice sudo", ""
            return 1, "", "missing"

        self.assertFalse(self.api.administrator_status(
            "alice", runner=passwordless_runner)["ready"])


class BuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        cls.desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
        cls.settings = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")

    def test_no_known_password_or_oobe_shell_concat(self):
        base = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
        self.assertNotIn('echo "${MING_USER}:${MING_USER_PASS}" | chpasswd', base)
        self.assertNotIn('echo "root:${ROOT_PASS}" | chpasswd', base)
        self.assertIn('passwd -l root', base)
        self.assertIn('passwd -d "${MING_USER}"', base)
        self.assertNotIn('pkexec /bin/bash -c', self.desktop)
        self.assertNotIn("chpasswd", self.desktop)
        for build_entry in ("build_onion_os.sh", "continue_build.sh", "incremental_upgrade.sh"):
            source = (ROOT / build_entry).read_text(encoding="utf-8")
            self.assertNotIn('MING_USER_PASS="user"', source)
            self.assertNotIn('ROOT_PASS="root"', source)

    def test_installed_identity_does_not_publish_factory_credentials(self):
        self.assertNotIn('echo "user:user"', self.base)
        self.assertNotIn('> /etc/ming-os/identity', self.base.split('user:user')[0] if 'user:user' in self.base else '')

    def test_welcome_waits_for_account_oobe_before_presenting(self):
        welcome = self.desktop.split("cat > /usr/local/bin/ming-welcome << 'WELCOMEPY'", 1)[1].split(
            "\nWELCOMEPY", 1
        )[0]
        self.assertIn("oobe-account-done", welcome)
        self.assertIn("wait_for_account_oobe", welcome)
        self.assertLess(welcome.index("wait_for_account_oobe"), welcome.index("win.present"))

    def test_first_boot_locks_are_deferred_until_oobe_is_configured(self):
        self.assertIn("ming-screensaver-after-oobe", self.desktop)
        self.assertIn("oobe-account-done", self.desktop)

    def test_base_deploys_account_helper_and_polkit_policy(self):
        for marker in (
                "ming-account-control.py", "/usr/local/sbin/ming-account-control",
                "org.ming.account.control.policy"):
            self.assertIn(marker, self.base)
        self.assertIn("allow_active", self.base)
        for marker in (
                "ming-admin-bootstrap.py", "/usr/local/sbin/ming-admin-bootstrap",
                "org.ming.account.bootstrap.policy"):
            self.assertIn(marker, self.base)
        bootstrap_policy = self.base.split(
            "cat > /usr/share/polkit-1/actions/org.ming.account.bootstrap.policy", 1)[1].split(
                "ACCOUNT_BOOTSTRAP_POLICY", 2)[1]
        self.assertIn("<allow_active>yes</allow_active>", bootstrap_policy)
        self.assertIn(
            '<annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>',
            bootstrap_policy)
        account_policy = self.base.split(
            "cat > /usr/share/polkit-1/actions/org.ming.account.control.policy", 1)[1].split(
                "ACCOUNT_CONTROL_POLICY", 2)[1]
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", account_policy)

    def test_account_helper_install_command_has_one_source_and_one_destination(self):
        line = next(
            line.strip() for line in self.base.splitlines()
            if "install -m 0755" in line and "ming-account-control.py" in line)
        argv = shlex.split(line)
        self.assertEqual(
            ["install", "-m", "0755",
             "/tmp/ming-build/assets/ming-account-control.py",
             "/usr/local/sbin/ming-account-control"],
            argv,
        )
        if os.name == "nt":
            self.skipTest("install command execution runs on POSIX")
        install_executable = shutil.which("install")
        if not install_executable:
            self.skipTest("install executable is unavailable")
        with tempfile.TemporaryDirectory() as tempdir:
            source = pathlib.Path(tempdir) / "source"
            destination = pathlib.Path(tempdir) / "destination"
            source.write_text("#!/bin/sh\n", encoding="utf-8")
            subprocess.run(
                [install_executable] + argv[1:3] + [str(source), str(destination)],
                check=True, capture_output=True, text=True)
            self.assertEqual(source.read_bytes(), destination.read_bytes())

    def test_settings_uses_fixed_account_helper_with_stdin(self):
        self.assertIn('command = ["pkexec", "/usr/local/sbin/ming-account-control"]', self.settings)
        self.assertIn("run_capture_stdin_async", self.settings)
        self.assertNotIn("ming-account-password", self.settings)
        self.assertNotIn("chpasswd", self.settings)
        self.assertNotIn("/bin/bash", self.settings)
        self.assertNotIn("password],", self.settings)

    def test_oobe_does_not_accept_legacy_skipped_marker_as_complete(self):
        script = self.desktop.split("cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'", 1)[1].split(
            "OOBEACCOUNT", 1)[0]
        self.assertIn('== "configured"', script)
        self.assertNotIn('== "skipped"', script)
        self.assertIn("/usr/local/sbin/ming-admin-bootstrap", script)

    def test_oobe_requires_local_administrator_bootstrap(self):
        script = self.desktop.split(
            "cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'", 1)[1].split(
                "OOBEACCOUNT", 1)[0]
        self.assertIn("/usr/local/sbin/ming-admin-bootstrap", script)
        self.assertNotIn('button="跳过', script.lower())
        self.assertNotIn('echo "skipped"', script)
        self.assertIn("OOBE_MAX_ATTEMPTS=3", script)
        self.assertIn('log_oobe_event "retry_exhausted"', script)
        self.assertNotIn("exec /usr/local/bin/ming-oobe-account", script)

    def test_oobe_does_not_collect_or_pipe_the_bootstrap_password(self):
        script = self.desktop.split(
            "cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'", 1)[1].split(
                "OOBEACCOUNT", 1)[0]
        self.assertNotIn('PW1=', script)
        self.assertNotIn('PW2=', script)
        self.assertNotIn("| pkexec /usr/local/sbin/ming-admin-bootstrap", script)
        self.assertIn("pkexec /usr/local/sbin/ming-admin-bootstrap", script)

    def test_settings_parses_structured_account_result(self):
        self.assertIn("def on_password_saved", self.settings)
        handler = self.settings.split("def on_password_saved", 1)[1].split(
            "# ---- 2.", 1)[0]
        self.assertIn("json.loads", handler)
        self.assertIn('result.get("ok")', handler)

    def test_security_page_blocks_mutations_until_administrator_is_ready(self):
        self.assertIn(
            '["/usr/local/sbin/ming-admin-bootstrap", "status", "--user", USER, "--json"]',
            self.settings)
        self.assertIn("security_admin_ready", self.settings)
        self.assertIn("请先在账户页面设置本机管理员密码", self.settings)
        refresh = self.settings.split("def refresh_security_status", 1)[1].split(
            "def on_security_toggle", 1)[0]
        admin_handler = refresh.split("def admin_done", 1)[1].split("def load_security_status", 1)[0]
        self.assertIn("load_security_status()", admin_handler)
        self.assertIn("self.security_admin_button", self.settings)
        self.assertIn("设置本机管理员", self.settings)
        self.assertIn("/usr/local/bin/ming-oobe-account", self.settings)
        self.assertIn("self.security_admin_button.set_visible(not self.security_admin_ready)", self.settings)

    def test_rootfs_gate_requires_bootstrap_helper_and_policy(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn('require_file("usr/local/sbin/ming-admin-bootstrap"', build)
        self.assertIn(
            'require_file("usr/share/polkit-1/actions/org.ming.account.bootstrap.policy"',
            build)
        self.assertIn(
            'validate_generated_executable("usr/local/sbin/ming-admin-bootstrap", "python")',
            build)


if __name__ == "__main__":
    unittest.main()
