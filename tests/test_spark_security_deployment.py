import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONVERGE = ROOT / "assets" / "ming-spark-security-converge.py"
POLICY = ROOT / "assets" / "polkit" / "store.spark-app.spark-store.policy"
REPAIR_POLICY = ROOT / "assets" / "polkit" / "org.ming.spark-store.repair.policy"
REPAIR_HELPER = ROOT / "assets" / "ming-spark-store-repair-helper.py"
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
    def __init__(
            self, fingerprint="9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
            disable_code=0, is_active_code=3, is_active_output="inactive\n"):
        self.fingerprint = fingerprint
        self.disable_code = disable_code
        self.is_active_code = is_active_code
        self.is_active_output = is_active_output
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
        if command[:3] == (
                "/usr/bin/systemctl", "disable", "--now"):
            return self.disable_code, "", "disable failed" if self.disable_code else ""
        if command[:2] == ("/usr/bin/systemctl", "is-active"):
            return self.is_active_code, self.is_active_output, ""
        if command[0] == "/usr/bin/dpkg-divert":
            return 0, "", ""
        raise AssertionError("unexpected command: %r" % (command,))


def write_file(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class SparkSecurityAssetTests(unittest.TestCase):
    def test_vendor_fingerprint_probe_uses_an_isolated_gnupg_home(self):
        module = load_converger()
        runner = ConvergenceRunner()
        with tempfile.TemporaryDirectory() as directory:
            key = write_file(
                pathlib.Path(directory),
                "opt/durapps/spark-store/bin/spark-store.asc",
                "vendor key",
            )
            converger = module.SparkSecurityConverger(
                root=pathlib.Path(directory), runner=runner, euid_getter=lambda: 0)
            converger._primary_fingerprint(key)

        command = next(command for command in runner.commands if "--show-keys" in command)
        self.assertIn("--homedir", command)
        self.assertTrue(command[command.index("--homedir") + 1])

    def test_converge_and_narrow_policy_assets_exist(self):
        self.assertTrue(CONVERGE.is_file(), "Spark security converge asset is missing")
        self.assertTrue(POLICY.is_file(), "Spark narrow polkit policy asset is missing")
        self.assertTrue(REPAIR_HELPER.is_file(), "Spark repair helper asset is missing")
        self.assertTrue(REPAIR_POLICY.is_file(), "Spark repair policy asset is missing")

    def test_each_vendor_shim_has_one_unique_exact_path_action(self):
        self.assertTrue(POLICY.is_file(), "Spark narrow polkit policy asset is missing")
        tree = ET.parse(POLICY)
        actions = tree.findall(".//action")
        bindings = {}
        action_ids = []
        for action in actions:
            action_ids.append(action.attrib.get("id"))
            paths = [
                node.text
                for node in action.findall("annotate")
                if node.attrib.get("key") == "org.freedesktop.policykit.exec.path"
            ]
            self.assertEqual(1, len(paths))
            bindings[paths[0]] = bindings.get(paths[0], 0) + 1
            self.assertEqual("no", action.findtext("./defaults/allow_any"))
            self.assertEqual("no", action.findtext("./defaults/allow_inactive"))
            self.assertEqual("auth_admin_keep", action.findtext("./defaults/allow_active"))
        self.assertEqual(len(action_ids), len(set(action_ids)))
        self.assertEqual({
            "/opt/spark-store/extras/shell-caller.sh": 1,
            "/opt/spark-store/bin/extras/shell-caller.sh": 1,
        }, bindings)
        policy = POLICY.read_text(encoding="utf-8")
        self.assertNotIn("allow_gui", policy)
        self.assertNotIn("ssinstall", policy)
        self.assertEqual(
            policy,
            load_converger().SAFE_POLICY,
            "checked-in and converged vendor policies must remain identical",
        )

    def test_repair_policy_binds_only_the_zero_argument_helper(self):
        self.assertTrue(REPAIR_POLICY.is_file(), "Spark repair policy asset is missing")
        tree = ET.parse(REPAIR_POLICY)
        actions = tree.findall(".//action")
        self.assertEqual(1, len(actions))
        action = actions[0]
        self.assertEqual("org.ming.spark-store.repair", action.attrib.get("id"))
        self.assertEqual("no", action.findtext("./defaults/allow_any"))
        self.assertEqual("no", action.findtext("./defaults/allow_inactive"))
        self.assertEqual("auth_admin_keep", action.findtext("./defaults/allow_active"))
        paths = [
            node.text
            for node in action.findall("annotate")
            if node.attrib.get("key") == "org.freedesktop.policykit.exec.path"
        ]
        self.assertEqual(["/usr/local/sbin/ming-spark-store-repair-helper"], paths)


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
            original_is_symlink = pathlib.Path.is_symlink
            original_readlink = os.readlink

            def is_symlink(path):
                return any(link == path for _target, link in links) or original_is_symlink(path)

            def readlink(path):
                for target, link in links:
                    if link == pathlib.Path(path):
                        return target
                return original_readlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", readlink):
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
            links = []
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
                symlink_creator=lambda target, link: links.append(
                    (target, pathlib.Path(link))),
            )
            original_is_symlink = pathlib.Path.is_symlink
            original_readlink = os.readlink

            def is_symlink(path):
                return any(link == path for _target, link in links) or original_is_symlink(path)

            def readlink(path):
                for target, link in links:
                    if link == pathlib.Path(path):
                        return target
                return original_readlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", readlink):
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

    def test_notifier_disable_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runner = ConvergenceRunner(disable_code=1)
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
            )
            converger.live_systemd_getter = lambda: True
            with self.assertRaises(self.module.ConvergenceError) as raised:
                converger._mask_notifier()
            self.assertEqual("E_CONVERGENCE_FAILED", raised.exception.code)

    def test_notifier_active_readback_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mask = root / "etc/systemd/system/spark-update-notifier.service"
            mask.parent.mkdir(parents=True)
            runner = ConvergenceRunner(is_active_code=0, is_active_output="active\n")
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
            )
            converger.live_systemd_getter = lambda: True
            original_is_symlink = pathlib.Path.is_symlink

            def is_symlink(path):
                return path == mask or original_is_symlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", lambda path: "/dev/null"):
                with self.assertRaises(self.module.ConvergenceError) as raised:
                    converger._mask_notifier()
            self.assertEqual("E_CONVERGENCE_FAILED", raised.exception.code)

    def test_existing_mask_still_removes_wants_and_reads_back_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mask = root / "etc/systemd/system/spark-update-notifier.service"
            mask.parent.mkdir(parents=True)
            wants = write_file(
                root,
                "etc/systemd/system/multi-user.target.wants/spark-update-notifier.service",
                "enabled",
            )
            runner = ConvergenceRunner(is_active_code=3, is_active_output="inactive\n")
            converger = self.module.SparkSecurityConverger(
                root=root, runner=runner, euid_getter=lambda: 0,
            )
            converger.live_systemd_getter = lambda: True
            original_is_symlink = pathlib.Path.is_symlink

            def is_symlink(path):
                return path == mask or original_is_symlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", lambda path: "/dev/null"):
                converger._mask_notifier()
            self.assertFalse(wants.exists())
            self.assertIn(
                ("/usr/bin/systemctl", "is-active", "spark-update-notifier.service"),
                runner.commands,
            )

    def test_offline_root_never_contacts_host_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mask = root / "etc/systemd/system/spark-update-notifier.service"
            wants = write_file(
                root,
                "etc/systemd/system/multi-user.target.wants/spark-update-notifier.service",
                "enabled",
            )
            links = []
            systemctl_commands = []

            def record_commands(command, timeout=20):
                if command[0] == "/usr/bin/systemctl":
                    systemctl_commands.append(tuple(command))
                    if command[1] == "is-active":
                        return 3, "inactive\n", ""
                    return 0, "", ""
                return ConvergenceRunner()(command, timeout=timeout)

            converger = self.module.SparkSecurityConverger(
                root=root,
                runner=record_commands,
                euid_getter=lambda: 0,
                symlink_creator=lambda target, link: links.append(
                    (target, pathlib.Path(link))),
            )
            converger.live_systemd_getter = lambda: False
            original_is_symlink = pathlib.Path.is_symlink
            original_readlink = os.readlink

            def is_symlink(path):
                return any(link == path for _target, link in links) or original_is_symlink(path)

            def readlink(path):
                for target, link in links:
                    if link == pathlib.Path(path):
                        return target
                return original_readlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", readlink):
                converger._mask_notifier()
            self.assertFalse(wants.exists())
            self.assertIn(("/dev/null", mask), links)
            self.assertEqual([], systemctl_commands)

    def test_notifier_mask_is_read_back_after_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            converger = self.module.SparkSecurityConverger(
                root=root,
                runner=ConvergenceRunner(),
                euid_getter=lambda: 0,
                symlink_creator=lambda _target, _link: None,
            )
            with self.assertRaises(self.module.ConvergenceError) as raised:
                converger._mask_notifier()
            self.assertEqual("E_CONVERGENCE_FAILED", raised.exception.code)

    def test_notifier_wants_removal_is_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mask = root / "etc/systemd/system/spark-update-notifier.service"
            wants = write_file(
                root,
                "etc/systemd/system/multi-user.target.wants/spark-update-notifier.service",
                "enabled",
            )
            converger = self.module.SparkSecurityConverger(
                root=root, runner=ConvergenceRunner(), euid_getter=lambda: 0,
            )
            converger.live_systemd_getter = lambda: False
            original_remove = converger._remove_path

            def refuse_wants_removal(path):
                if path != self.module.NOTIFIER_WANTS:
                    original_remove(path)

            converger._remove_path = refuse_wants_removal
            original_is_symlink = pathlib.Path.is_symlink

            def is_symlink(path):
                return path == mask or original_is_symlink(path)

            with mock.patch.object(pathlib.Path, "is_symlink", is_symlink), \
                    mock.patch.object(os, "readlink", lambda _path: "/dev/null"):
                with self.assertRaises(self.module.ConvergenceError) as raised:
                    converger._mask_notifier()
            self.assertTrue(wants.exists())
            self.assertEqual("E_CONVERGENCE_FAILED", raised.exception.code)


class SparkSecurityBuildContractTests(unittest.TestCase):
    def test_security_asset_install_failures_abort_full_and_resume_paths(self):
        required_markers = (
            "/usr/local/sbin/ming-spark-store-repair-helper || return 1",
            "/usr/share/polkit-1/actions/store.spark-app.spark-store.policy || return 1",
            "/usr/share/polkit-1/actions/org.ming.spark-store.repair.policy || return 1",
        )
        for label, source in (("apps", APPS), ("desktop", DESKTOP), ("resume", RESUME)):
            for marker in required_markers:
                with self.subTest(label=label, marker=marker):
                    self.assertIn(marker, source)

    def test_all_repair_ui_paths_use_only_the_zero_argument_helper(self):
        repair_block = DESKTOP.split("repair-store)", 1)[1].split(";;", 1)[0]
        self.assertIn(
            "pkexec /usr/local/sbin/ming-spark-store-repair-helper",
            repair_block,
        )
        self.assertNotIn("sudo", repair_block)
        self.assertNotIn("ming-install-spark-store", repair_block)
        self.assertIn("/tmp/ming-spark.log", repair_block)
        self.assertIn("商店修复没有完成", repair_block)

        wrapper = APPS.split(
            "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'", 1
        )[1].split("MINGSPARK", 1)[0]
        self.assertIn(
            "exec pkexec /usr/local/sbin/ming-spark-store-repair-helper",
            wrapper,
        )
        self.assertNotIn('ming-spark-store-repair-helper "$@"', wrapper)
        repair_desktop = APPS.split(
            "cat > /usr/share/applications/ming-install-spark-store.desktop", 1
        )[1].split("SPARKINSTALLDESKTOP", 2)[1]
        self.assertIn(
            "Exec=pkexec /usr/local/sbin/ming-spark-store-repair-helper",
            repair_desktop,
        )

    def test_apps_deploys_security_assets_and_converges_before_install(self):
        for marker in (
            "ming-spark-package-helper.py",
            "ming-spark-security-converge.py",
            "ming-spark-store-repair-helper.py",
            "org.ming.spark-store.repair.policy",
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
            "ming-spark-store-repair-helper.py",
            "org.ming.spark-store.repair.policy",
            "/usr/local/sbin/ming-spark-package-helper",
            "/usr/local/sbin/ming-spark-security-converge",
            "/usr/local/sbin/ming-spark-store-repair-helper",
        ):
            self.assertIn(marker, DESKTOP)
        for marker in (
            "ming-spark-package-helper.py",
            "ming-spark-security-converge.py",
            "ming-spark-store-repair-helper.py",
            "org.ming.spark-store.repair.policy",
            "seed_resume_spark_security",
        ):
            self.assertIn(marker, RESUME)

    def test_rootfs_gate_checks_helper_policy_shims_key_source_and_notifier(self):
        gate = BUILD.split("# Spark Store security convergence is validated", 1)[1].split("\nPY", 1)[0]
        for marker in (
            "ming-spark-package-helper",
            "ming-spark-security-converge",
            "ming-spark-store-repair-helper",
            "org.ming.spark-store.repair.policy",
            "store.spark-app.spark-store.policy",
            "/opt/spark-store/bin/extras/shell-caller.sh",
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
        self.assertIn("len(_annotations) != 1", gate)


if __name__ == "__main__":
    unittest.main()
