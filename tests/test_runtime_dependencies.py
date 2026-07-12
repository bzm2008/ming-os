import pathlib
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
RESUME = (ROOT / "resume_build.sh").read_text(encoding="utf-8")


def control_center_wrapper_source():
    return DESKTOP.split(
        "cat > /usr/local/bin/ming-control-center << 'MINGCONTROLWRAPPER'", 1
    )[1].split("MINGCONTROLWRAPPER", 1)[0]


REQUIRED_PACKAGES = [
    "python3-gi",
    "gir1.2-gtk-4.0",
    "gir1.2-adw-1",
    "libadwaita-1-0",
    "gvfs",
    "gvfs-backends",
    "brightnessctl",
    "xdotool",
    "wmctrl",
    "rfkill",
    "pulseaudio",
    "pulseaudio-utils",
    "alsa-utils",
    "bluez",
    "upower",
    "pkexec",
    "polkitd",
    "lxpolkit",
    "libnotify-bin",
    "x11-utils",
]


def shell_function_source(source, name):
    match = re.search(r"^%s\(\) \{" % re.escape(name), source, re.MULTILINE)
    if not match:
        raise AssertionError("missing shell function: %s" % name)
    end = source.find("\n# ========================", match.end())
    return source[match.start():end if end >= 0 else len(source)]


def desktop_backend_validator_source():
    return BUILD.split("# MING_DESKTOP_BACKEND_VALIDATOR_BEGIN", 1)[1].split(
        "# MING_DESKTOP_BACKEND_VALIDATOR_END", 1
    )[0]


def write_executable(root, relative_path, content="#!/bin/sh\nexit 0\n"):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def write_core_desktops(root):
    targets = {
        "ming-settings.desktop": "/usr/local/bin/ming-control-center",
        "ming-files.desktop": "/usr/local/bin/ming-files",
        "ming-terminal.desktop": "/usr/local/bin/ming-terminal",
        "ming-edge.desktop": "/usr/local/bin/ming-edge",
        "spark-store.desktop": "/usr/local/bin/ming-spark-store",
    }
    applications = root / "usr/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    for desktop, command in targets.items():
        (applications / desktop).write_text(
            "[Desktop Entry]\nType=Application\nExec=%s\n" % command,
            encoding="utf-8",
        )
        write_executable(root, command.lstrip("/"))


def run_backend_validator(root):
    return subprocess.run(
        [sys.executable, "-", str(root)],
        input=desktop_backend_validator_source(),
        text=True,
        capture_output=True,
        check=False,
    )


class RequiredRuntimeDependencyContracts(unittest.TestCase):
    def test_apps_module_has_a_dedicated_required_runtime_package_set(self):
        block = APPS.split("REQUIRED_DESKTOP_RUNTIME_PACKAGES=(", 1)[1].split(")", 1)[0]
        for package in REQUIRED_PACKAGES:
            self.assertIn(package, block)

    def test_required_runtime_install_propagates_apt_and_dpkg_failures(self):
        function = APPS.split("install_required_desktop_runtime() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('if ! apt install', function)
        self.assertIn('return 1', function)
        self.assertIn('dpkg-query -W -f=', function)
        self.assertIn("${db:Status-Abbrev}", function)
        self.assertIn('required desktop runtime package is not installed', function)

    def test_main_explicitly_propagates_required_steps_and_tolerates_optional_apps(self):
        main = APPS.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('run_required_step install_xfce_desktop', main)
        self.assertIn('run_required_step install_required_desktop_runtime', main)
        self.assertIn('run_required_step install_fcitx5', main)
        self.assertIn('run_optional_step install_edge', main)
        self.assertIn('run_optional_step install_app_store', main)

    def test_every_required_install_function_propagates_mandatory_command_failures(self):
        expected_guards = {
            "install_xfce_desktop": (
                "xfce4-power-manager-plugins || return 1",
                "imagemagick || return 1",
                "plymouth-themes || return 1",
            ),
            "install_fonts": (
                "fonts-noto-cjk-extra || return 1",
                "fc-cache -f -v || return 1",
            ),
            "install_fcitx5": ("fcitx5-material-color || return 1",),
        }
        for name, guards in expected_guards.items():
            function = shell_function_source(APPS, name)
            for guard in guards:
                with self.subTest(function=name, guard=guard):
                    self.assertIn(guard, function)

    def test_resume_installs_and_verifies_the_same_required_packages(self):
        function = RESUME.split("ensure_resume_runtime_packages() {", 1)[1].split("\n}", 1)[0]
        for package in REQUIRED_PACKAGES:
            self.assertIn(package, function)
        self.assertIn('if ! chroot_exec apt-get update', function)
        self.assertIn('if ! chroot_exec /usr/local/sbin/apt-build install', function)
        self.assertIn('dpkg-query -W -f=', function)
        self.assertIn('resume required runtime package is not installed', function)

    def test_build_gate_checks_typelibs_commands_and_ming_runtime(self):
        function = BUILD.split("validate_required_desktop_runtime() {", 1)[1].split("\n}", 1)[0]
        for marker in [
            "gi.require_version('Gtk', '4.0')",
            "gi.require_version('Adw', '1')",
            "brightnessctl",
            "xdotool",
            "wmctrl",
            "pactl",
            "bluetoothctl",
            "upower",
            "pkexec",
            "lxpolkit",
            "notify-send",
            "xprop",
            "/usr/sbin/rfkill",
            "runpy.run_path('/usr/local/bin/ming-settings'",
            "/usr/local/bin/ming-files --check-runtime",
        ]:
            self.assertIn(marker, function)

    def test_build_gate_resolves_core_desktop_exec_targets(self):
        function = BUILD.split("validate_required_desktop_runtime() {", 1)[1].split("\n}", 1)[0]
        for desktop in [
            "ming-settings.desktop",
            "ming-files.desktop",
            "ming-terminal.desktop",
            "ming-edge.desktop",
            "spark-store.desktop",
        ]:
            self.assertIn(desktop, function)
        self.assertIn("shlex.split(exec_line)", function)
        self.assertIn("shutil.which(command", function)
        self.assertIn("os.access(target, os.X_OK)", function)

    def test_full_build_settle_rejects_nonempty_dpkg_audit(self):
        function = BUILD.split("settle_chroot_dpkg() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('audit_output="$(chroot_exec dpkg --audit)"', function)
        self.assertIn('[[ -n "${audit_output}" ]]', function)
        self.assertIn("return 1", function)

    def test_edge_wrapper_without_a_real_browser_backend_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            result = run_backend_validator(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("missing Microsoft Edge browser backend", result.stderr)

    def test_spark_wrapper_requires_an_executable_install_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            result = run_backend_validator(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Spark Store repair fallback", result.stderr)

            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            result = run_backend_validator(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_r4_validation_invokes_required_runtime_gate(self):
        validation = BUILD.split("validate_r4_compatibility() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("validate_required_desktop_runtime", validation)

    def test_control_center_wrapper_reports_missing_gtk4_runtime(self):
        wrapper = control_center_wrapper_source()
        for marker in (
            "gi.require_version('Gtk', '4.0')",
            "gi.require_version('Adw', '1')",
            "ming-settings-launch.log",
            "notify-send",
            "zenity",
            "gir1.2-gtk-4.0",
            "gir1.2-adw-1",
            "exit 1",
            "exec /usr/local/bin/ming-settings",
        ):
            self.assertIn(marker, wrapper)


if __name__ == "__main__":
    unittest.main()
