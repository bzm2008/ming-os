import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONVERGE = ROOT / "assets" / "ming-spark-security-converge.py"
POLICY = ROOT / "assets" / "polkit" / "store.spark-app.spark-store.policy"
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
RESUME = (ROOT / "resume_build.sh").read_text(encoding="utf-8")


def load_converger():
    name = "ming_spark_security_converge_under_test"
    spec = importlib.util.spec_from_file_location(name, CONVERGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ConvergenceRunner:
    def __init__(self, fingerprint="9D9AA859F75024B1A1ECE16E0E41D354A29A440C"):
        self.fingerprint = fingerprint
        self.commands = []

    def __call__(self, command, timeout=20):
        command = tuple(str(value) for value in command)
        self.commands.append(command)
        if command[0] == "/usr/bin/gpg" and "--show-keys" in command:
            return 0, "pub:-:4096:1:ABC:0:::-:::scESC::::::23:\nfpr:::::::::%s:\n" % self.fingerprint, ""
        if command[0] == "/usr/bin/gpg" and "--dearmor" in command:
            output = pathlib.Path(command[command.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"dearmored-key")
            return 0, "", ""
        if command[0] in {"/usr/bin/dpkg-divert", "/usr/bin/systemctl"}:
            return 0, "", ""
        raise AssertionError("unexpected command: %r" % (command,))


def write_file(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class SparkSecurityAssetTests(unittest.TestCase):
    def test_converge_and_narrow_policy_assets_exist(self):
        self.assertTrue(CONVERGE.is_file(), "Spark security converge asset is missing")
        self.assertTrue(POLICY.is_file(), "Spark narrow polkit policy asset is missing")

    def test_policy_requires_authentication_and_binds_only_the_electron_shim(self):
        policy = POLICY.read_text(encoding="utf-8")
        for marker in (
            'action id="store.spark-app.spark-store"',
            "<allow_any>no</allow_any>",
            "<allow_inactive>no</allow_inactive>",
            "<allow_active>auth_admin_keep</allow_active>",
            "/opt/spark-store/extras/shell-caller.sh",
        ):
            self.assertIn(marker, policy)
        self.assertNotIn("allow_gui", policy)
        self.assertNotIn("<allow_any>yes</allow_any>", policy)


class SparkConvergenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_converger()

    def test_enforce_replaces_vendor_policy_and_shell_surfaces_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            asc = write_file(root, "opt/durapps/spark-store/bin/spark-store.asc", "vendor key")
            unsafe = (
                "<allow_any>yes</allow_any> <allow_inactive>yes</allow_inactive> "
                "eval /usr/bin/ssinstall"
            )
            for relative in (
                "opt/spark-store/extras/store.spark-app.spark-store.policy",
                "opt/spark-store/bin/extras/store.spark-app.spark-store.policy",
                "usr/share/polkit-1/actions/store.spark-app.spark-store.policy",
                "usr/share/polkit-1/actions/store.spark-app.ssinstall.policy",
                "usr/share/polkit-1/actions/store.spark-update-tool.policy",
            ):
                write_file(root, relative, unsafe)
            for relative in (
                "opt/spark-store/extras/shell-caller.sh",
                "opt/spark-store/bin/extras/shell-caller.sh",
            ):
                write_file(root, relative, "#!/bin/sh\neval \"$@\"\n")
            write_file(root, "etc/apt/trusted.gpg.d/spark-store.gpg", "legacy")
            write_file(root, "usr/lib/systemd/system/spark-update-notifier.service", "[Unit]\n")
            write_file(
                root,
                "etc/systemd/system/multi-user.target.wants/spark-update-notifier.service",
                "enabled",
            )
            runner = ConvergenceRunner()
            links = []
            converger = self.module.SparkSecurityConverger(
                root=root,
                runner=runner,
                euid_getter=lambda: 0,
                symlink_creator=lambda target, link: links.append((target, pathlib.Path(link))),
            )

            converger.enforce()

            active_policy = root / "usr/share/polkit-1/actions/store.spark-app.spark-store.policy"
            policy = active_policy.read_text(encoding="utf-8")
            self.assertIn("<allow_any>no</allow_any>", policy)
            self.assertNotIn("allow_gui", policy)
            self.assertNotIn("<allow_any>yes</allow_any>", policy)
            for relative in (
                "opt/spark-store/extras/shell-caller.sh",
                "opt/spark-store/bin/extras/shell-caller.sh",
            ):
                shim = (root / relative).read_text(encoding="utf-8")
                self.assertIn("exec /usr/local/sbin/ming-spark-package-helper", shim)
                self.assertNotIn("eval", shim)
            self.assertIn(("/dev/null", root / "etc/systemd/system/spark-update-notifier.service"), links)
            self.assertEqual("deb [signed-by=/etc/apt/keyrings/ming-spark-store.gpg] https://d.spark-app.store/store /\n",
                             (root / "etc/apt/sources.list.d/ming-spark-store.list").read_text(encoding="utf-8"))
            self.assertFalse((root / "etc/apt/trusted.gpg.d/spark-store.gpg").exists())
            self.assertTrue(any(
                command[0] == "/usr/bin/dpkg-divert"
                and path in command
                for command in runner.commands
                for path in (
                    "/opt/spark-store/extras/shell-caller.sh",
                    "/opt/spark-store/bin/extras/shell-caller.sh",
                    "/opt/spark-store/extras/store.spark-app.spark-store.policy",
                    "/opt/spark-store/bin/extras/store.spark-app.spark-store.policy",
                    "/usr/share/polkit-1/actions/store.spark-app.ssinstall.policy",
                    "/usr/share/polkit-1/actions/store.spark-update-tool.policy",
                )
            ))

    def test_enforce_rejects_a_vendor_key_with_the_wrong_primary_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_file(root, "opt/durapps/spark-store/bin/spark-store.asc", "vendor key")
            runner = ConvergenceRunner(fingerprint="0" * 40)
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
                symlink_creator=lambda _target, _link: None,
            )
            with self.assertRaises(self.module.ConvergenceError) as raised:
                converger.enforce()
            self.assertEqual("E_VENDOR_KEY_INVALID", raised.exception.code)

    def test_postinst_policy_reintroduction_is_converged_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_file(root, "opt/durapps/spark-store/bin/spark-store.asc", "vendor key")
            runner = ConvergenceRunner()
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
                symlink_creator=lambda _target, _link: None,
            )
            converger.enforce()
            source = root / "opt/spark-store/extras/store.spark-app.spark-store.policy"
            source.write_text("<allow_any>yes</allow_any>", encoding="utf-8")
            active = root / "usr/share/polkit-1/actions/store.spark-app.spark-store.policy"
            active.write_text("<allow_inactive>yes</allow_inactive>", encoding="utf-8")
            shim = root / "opt/spark-store/extras/shell-caller.sh"
            shim.write_text("#!/bin/sh\neval \"$@\"\n", encoding="utf-8")
            converger.enforce()
            self.assertIn("<allow_any>no</allow_any>", source.read_text(encoding="utf-8"))
            self.assertNotIn("<allow_inactive>yes</allow_inactive>", active.read_text(encoding="utf-8"))
            self.assertNotIn("eval", shim.read_text(encoding="utf-8"))

    def test_existing_diversion_does_not_remove_the_live_safe_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            relative = "opt/spark-store/extras/shell-caller.sh"
            source = write_file(root, relative, "safe shim")
            vendor = write_file(root, relative + ".vendor", "vendor shell")
            converger = self.module.SparkSecurityConverger(
                root=root, runner=ConvergenceRunner(), euid_getter=lambda: 0,
            )

            converger._move_to_vendor("/" + relative)

            self.assertEqual("safe shim", source.read_text(encoding="utf-8"))
            self.assertEqual("vendor shell", vendor.read_text(encoding="utf-8"))

    def test_notifier_mask_failure_is_a_structured_convergence_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_file(root, "opt/durapps/spark-store/bin/spark-store.asc", "vendor key")

            def fail_symlink(_target, _link):
                raise OSError("mask denied")

            converger = self.module.SparkSecurityConverger(
                root=root, runner=ConvergenceRunner(), euid_getter=lambda: 0,
                symlink_creator=fail_symlink,
            )
            with self.assertRaises(self.module.ConvergenceError) as raised:
                converger.enforce()
            self.assertEqual("E_CONVERGENCE_FAILED", raised.exception.code)


class SparkSecurityBuildContractTests(unittest.TestCase):
    def test_apps_deploys_security_assets_and_converges_before_install(self):
        for marker in (
            "ming-spark-package-helper.py",
            "ming-spark-security-converge.py",
            "ming-spark-security-converge prepare --deb",
            "ming-spark-security-converge enforce",
            "--resolver spark --json",
        ):
            self.assertIn(marker, APPS)
        converge_source = CONVERGE.read_text(encoding="utf-8")
        for marker in (
            "dpkg-divert",
            "/opt/spark-store/extras/shell-caller.sh",
            "/opt/spark-store/bin/extras/shell-caller.sh",
            "/opt/spark-store/extras/store.spark-app.spark-store.policy",
            "/opt/spark-store/bin/extras/store.spark-app.spark-store.policy",
            "/usr/share/polkit-1/actions/store.spark-app.ssinstall.policy",
            "/usr/share/polkit-1/actions/store.spark-update-tool.policy",
        ):
            self.assertIn(marker, converge_source)
        self.assertLess(
            APPS.index("ming-spark-security-converge prepare --deb"),
            APPS.index("ming-package-installer install \"${deb}\" --resolver spark"),
        )

    def test_no_delayed_appstore_timer_or_service_is_generated(self):
        self.assertNotIn("cat > /etc/systemd/system/ming-appstore-ready.service", APPS)
        self.assertNotIn("cat > /etc/systemd/system/ming-appstore-ready.timer", APPS)
        self.assertNotIn("enable ming-appstore-ready.timer", APPS)

    def test_desktop_and_resume_assets_include_the_security_boundary(self):
        for marker in (
            "ming-spark-package-helper.py",
            "ming-spark-security-converge.py",
            "/usr/local/sbin/ming-spark-package-helper",
            "/usr/local/sbin/ming-spark-security-converge",
        ):
            self.assertIn(marker, DESKTOP)
        for marker in (
            "ming-spark-package-helper.py",
            "ming-spark-security-converge.py",
            "seed_resume_spark_security",
        ):
            self.assertIn(marker, RESUME)

    def test_rootfs_gate_checks_helper_policy_shims_key_source_and_notifier(self):
        gate = BUILD.split("# Spark Store security convergence is validated", 1)[1].split("\nPY", 1)[0]
        for marker in (
            "ming-spark-package-helper",
            "ming-spark-security-converge",
            "store.spark-app.spark-store.policy",
            "store.spark-app.ssinstall.policy.vendor",
            "store.spark-update-tool.policy.vendor",
            "unsafe Spark policy metadata",
            "unsafe Spark shell shim metadata",
            "multiple active Spark polkit actions",
            "ming-spark-store.gpg",
            "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
            "d.spark-app.store/store",
            "allow_any",
            "allow_inactive",
            "ming-appstore-ready.timer",
        ):
            self.assertIn(marker, gate)
        self.assertIn("active Spark ssinstall policy remains", gate)


if __name__ == "__main__":
    unittest.main()
