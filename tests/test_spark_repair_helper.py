import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "assets" / "ming-spark-store-repair-helper.py"
INSTALLER = "/usr/local/bin/ming-install-spark-store"
EXEC_ENV = {
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LOGNAME": "root",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "USER": "root",
}


def load_helper():
    name = "ming_spark_store_repair_helper_under_test"
    spec = importlib.util.spec_from_file_location(name, HELPER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def valid_session(**overrides):
    values = {
        "User": "1000",
        "Active": "yes",
        "Remote": "no",
        "Class": "user",
        "Type": "x11",
        "Seat": "seat0",
    }
    values.update(overrides)
    return values


class SparkStoreRepairHelperTests(unittest.TestCase):
    def module(self):
        self.assertTrue(HELPER.is_file(), "Spark repair helper asset is missing")
        return load_helper()

    def service(
            self, module, *, euid=0, pkexec_uid="1000", passwd_uid=1000,
            sessions=None, extra_env=None, exec_calls=None):
        if sessions is None:
            sessions = [valid_session()]

        def passwd_lookup(uid):
            if passwd_uid is None:
                raise KeyError(uid)
            return types.SimpleNamespace(pw_uid=passwd_uid, pw_name="user")

        environ = {"PKEXEC_UID": pkexec_uid} if pkexec_uid is not None else {}
        environ.update(extra_env or {})
        calls = exec_calls if exec_calls is not None else []
        return module.SparkStoreRepairHelper(
            euid_getter=lambda: euid,
            environ=environ,
            passwd_lookup=passwd_lookup,
            session_reader=lambda _uid: sessions,
            execve=lambda path, argv, env: calls.append(
                (path, tuple(argv), dict(env))),
        )

    def test_rejects_all_arguments_before_executing_the_installer(self):
        module = self.module()
        calls = []
        with self.assertRaises(module.RepairError) as raised:
            self.service(module, exec_calls=calls).execute(["--force"])
        self.assertEqual("E_REQUEST_INVALID", raised.exception.code)
        self.assertEqual([], calls)

    def test_requires_root_and_a_real_nonzero_decimal_pkexec_uid(self):
        module = self.module()
        cases = (
            ("non-root", {"euid": 1000}),
            ("missing", {"pkexec_uid": None}),
            ("root", {"pkexec_uid": "0"}),
            ("signed", {"pkexec_uid": "+1000"}),
            ("space", {"pkexec_uid": " 1000"}),
            ("suffix", {"pkexec_uid": "1000x"}),
            ("missing passwd", {"passwd_uid": None}),
            ("passwd mismatch", {"passwd_uid": 1001}),
        )
        for label, options in cases:
            with self.subTest(label=label):
                with self.assertRaises(module.RepairError) as raised:
                    self.service(module, **options).execute([])
                self.assertEqual("E_AUTHORIZATION_FAILED", raised.exception.code)

    def test_unrepresentable_decimal_uid_is_a_structured_authorization_failure(self):
        module = self.module()
        service = module.SparkStoreRepairHelper(
            euid_getter=lambda: 0,
            environ={"PKEXEC_UID": "9" * 40},
            passwd_lookup=lambda _uid: (_ for _ in ()).throw(
                OverflowError("uid out of range")),
            session_reader=lambda _uid: [valid_session()],
            execve=lambda _path, _argv, _env: None,
        )
        try:
            service.execute([])
        except Exception as error:
            self.assertIsInstance(error, module.RepairError)
            self.assertEqual("E_AUTHORIZATION_FAILED", error.code)
        else:
            self.fail("an unrepresentable PKEXEC_UID was accepted")

    def test_rejects_ssh_callers(self):
        module = self.module()
        for variable in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
            with self.subTest(variable=variable):
                with self.assertRaises(module.RepairError) as raised:
                    self.service(module, extra_env={variable: "set"}).execute([])
                self.assertEqual("E_AUTHORIZATION_FAILED", raised.exception.code)

    def test_requires_an_active_local_graphical_user_session_with_a_seat(self):
        module = self.module()
        rejected = (
            ("inactive", [valid_session(Active="no")]),
            ("remote", [valid_session(Remote="yes")]),
            ("seatless", [valid_session(Seat="")]),
            ("manager", [valid_session(Class="manager")]),
            ("tty", [valid_session(Type="tty")]),
            ("other uid", [valid_session(User="1001")]),
            ("missing", []),
        )
        for label, sessions in rejected:
            with self.subTest(label=label):
                with self.assertRaises(module.RepairError) as raised:
                    self.service(module, sessions=sessions).execute([])
                self.assertEqual("E_AUTHORIZATION_FAILED", raised.exception.code)

        for session_type in ("x11", "wayland"):
            with self.subTest(session_type=session_type):
                calls = []
                result = self.service(
                    module,
                    sessions=[valid_session(Type=session_type)],
                    exec_calls=calls,
                ).execute([])
                self.assertEqual(0, result)
                self.assertEqual(1, len(calls))

    def test_default_session_reader_uses_bounded_loginctl_queries(self):
        module = self.module()
        commands = []

        def runner(command, timeout=2):
            commands.append((tuple(command), timeout))
            if command[1] == "list-sessions":
                return 0, "7 1000 user seat0 123 user tty2 no -\n", ""
            return 0, (
                "User=1000\nActive=yes\nRemote=no\nClass=user\n"
                "Type=x11\nSeat=seat0\n"
            ), ""

        calls = []
        service = module.SparkStoreRepairHelper(
            runner=runner,
            euid_getter=lambda: 0,
            environ={"PKEXEC_UID": "1000"},
            passwd_lookup=lambda uid: types.SimpleNamespace(pw_uid=uid),
            execve=lambda path, argv, env: calls.append(
                (path, tuple(argv), dict(env))),
        )
        self.assertEqual(0, service.execute([]))
        self.assertEqual(2, len(commands))
        self.assertTrue(all(timeout == 2 for _command, timeout in commands))
        self.assertEqual("/usr/bin/loginctl", commands[0][0][0])
        self.assertEqual("list-sessions", commands[0][0][1])
        self.assertEqual("show-session", commands[1][0][1])
        self.assertEqual("7", commands[1][0][2])
        self.assertEqual(1, len(calls))

    def test_exec_path_argv_and_environment_are_fixed(self):
        module = self.module()
        calls = []
        result = self.service(module, exec_calls=calls).execute([])
        self.assertEqual(0, result)
        self.assertEqual([(INSTALLER, (INSTALLER,), EXEC_ENV)], calls)


if __name__ == "__main__":
    unittest.main()
