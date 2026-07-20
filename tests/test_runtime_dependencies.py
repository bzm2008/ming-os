import functools
import json
import os
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
BASH = (
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and pathlib.Path(r"C:\Program Files\Git\bin\bash.exe").is_file()
    else "bash"
)


def control_center_wrapper_source():
    return DESKTOP.split(
        "cat > /usr/local/bin/ming-control-center << 'MINGCONTROLWRAPPER'", 1
    )[1].split("MINGCONTROLWRAPPER", 1)[0]


def generated_script_source(path, marker):
    start = "cat > %s << '%s'" % (path, marker)
    return DESKTOP.split(start, 1)[1].split(marker, 1)[0]


def package_install_gui_source():
    return generated_script_source(
        "/usr/local/bin/ming-package-install-gui", "MINGPACKAGEGUI"
    )


def package_install_gui_python_source():
    gui = package_install_gui_source()
    return gui.split("if python3 -", 1)[1].split("\n", 1)[1].split(
        "\nMINGPACKAGEUIPY", 1
    )[0]


def run_package_install_gui_python(result, installer_rc=0):
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        result_file = root / "result.json"
        result_file.write_text(result, encoding="utf-8")
        environment = dict(os.environ)
        environment["PATH"] = str(root / "empty-bin")
        completed = subprocess.run(
            [sys.executable, "-c", package_install_gui_python_source(),
             str(result_file), str(installer_rc)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        return completed


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
    "desktop-file-utils",
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


SPARK_VENDOR_LINK = "/opt/durapps/spark-store/bin/spark-store"
SPARK_VENDOR_LINK_TARGET = "../../../spark-store/extras/spark-store"
SPARK_VENDOR_TARGET = "/opt/spark-store/extras/spark-store"
SPARK_VENDOR_PATHS = (
    SPARK_VENDOR_LINK,
    SPARK_VENDOR_TARGET,
)


def module_shell_function_source(name):
    module_prefix = APPS.split("install_app_store() {", 1)[0]
    marker = "%s() {" % name
    start = module_prefix.index(marker)
    end = module_prefix.index("\n}", start) + 2
    return module_prefix[start:end]


@functools.lru_cache(maxsize=1)
def spark_mode_helper_source():
    static_marker = "cat > \"${target}\" << 'MINGSPARKMODE'"
    if static_marker in APPS:
        return (
            APPS.split(static_marker, 1)[1].split("\nMINGSPARKMODE", 1)[0].lstrip("\n")
            + "\n"
        )
    functions = "\n\n".join((
        module_shell_function_source("spark_release_field"),
        module_shell_function_source("resolve_spark_build_mode"),
    ))
    completed = subprocess.run(
        [BASH],
        input=(
            functions
            + "\ndeclare -f spark_release_field resolve_spark_build_mode\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n\n"
        + completed.stdout
        + "\nresolve_spark_build_mode\n"
    )


def write_spark_mode_helper(root, content=None):
    helper = root / "usr/local/libexec/ming-spark-build-mode"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(
        content or spark_mode_helper_source(),
        encoding="utf-8",
        newline="\n",
    )
    helper.chmod(helper.stat().st_mode | 0o111)
    return helper


def write_symlink(root, relative_path, target):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows CI may not grant SeCreateSymbolicLinkPrivilege. Store a regular
    # placeholder plus structured link metadata; run_backend_validator patches
    # lstat/readlink in its child process so the production validator still
    # exercises symlink semantics.
    path.write_text(target, encoding="utf-8")
    registry = root / ".ming-test-symlinks.json"
    links = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
    links[relative_path] = target
    registry.write_text(json.dumps(links), encoding="utf-8")
    return path


def write_spark_dpkg_metadata(
        root, version="5.2.1.0", architecture="amd64", owned_paths=SPARK_VENDOR_PATHS):
    dpkg = root / "var/lib/dpkg"
    info = dpkg / "info"
    info.mkdir(parents=True, exist_ok=True)
    (dpkg / "status").write_text(
        "Package: spark-store\n"
        "Status: install ok installed\n"
        "Architecture: %s\n"
        "Version: %s\n\n" % (architecture, version),
        encoding="utf-8",
    )
    (info / "spark-store.list").write_text(
        "\n".join(owned_paths) + "\n",
        encoding="utf-8",
    )


def write_vendor_spark_package(
        root,
        *,
        first_target=SPARK_VENDOR_LINK,
        second_target=SPARK_VENDOR_LINK_TARGET,
        first_is_regular=False,
        final_exists=True,
        final_executable=True,
        version="5.2.1.0",
        architecture="amd64",
        owned_paths=SPARK_VENDOR_PATHS):
    if first_is_regular:
        write_executable(root, "usr/local/bin/spark-store")
    else:
        write_symlink(root, "usr/local/bin/spark-store", first_target)
    write_symlink(root, "opt/durapps/spark-store/bin/spark-store", second_target)
    if final_exists:
        final = root / SPARK_VENDOR_TARGET.lstrip("/")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        if final_executable:
            final.chmod(final.stat().st_mode | 0o111)
    (root / ".ming-test-final-executable").write_text(
        "1" if final_executable else "0",
        encoding="ascii",
    )
    write_spark_dpkg_metadata(
        root,
        version=version,
        architecture=architecture,
        owned_paths=owned_paths,
    )


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
    write_spark_mode_helper(root)


def run_backend_validator(
        root,
        final_uid=0,
        final_gid=0,
        final_write_bits=0,
        first_link_uid=0,
        first_link_gid=0,
        second_link_uid=0,
        second_link_gid=0,
        helper_uid=0,
        helper_gid=0,
        helper_mode=0o755):
    final_path = root / SPARK_VENDOR_TARGET.lstrip("/")
    first_link_path = root / "usr/local/bin/spark-store"
    second_link_path = root / SPARK_VENDOR_LINK.lstrip("/")
    helper_path = root / "usr/local/libexec/ming-spark-build-mode"
    registry = root / ".ming-test-symlinks.json"
    raw_links = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
    links = {str(root / path): target for path, target in raw_links.items()}
    executable_marker = root / ".ming-test-final-executable"
    final_executable = (
        executable_marker.read_text(encoding="ascii") == "1"
        if executable_marker.exists() else True
    )
    ownership_shim = """
import os
import pathlib
import stat

_ming_original_lstat = pathlib.Path.lstat
_ming_original_is_symlink = pathlib.Path.is_symlink
_ming_original_readlink = os.readlink
_ming_test_links = %r

class _MingTestStat:
    def __init__(self, wrapped, *, mode=None, uid=None, gid=None):
        self._wrapped = wrapped
        self.st_mode = wrapped.st_mode if mode is None else mode
        self.st_uid = wrapped.st_uid if uid is None else uid
        self.st_gid = wrapped.st_gid if gid is None else gid

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

def _ming_test_lstat(path, *args, **kwargs):
    metadata = _ming_original_lstat(path, *args, **kwargs)
    path_text = str(path)
    if path_text in _ming_test_links:
        if path_text == %r:
            return _MingTestStat(
                metadata,
                mode=stat.S_IFLNK | 0o777,
                uid=%d,
                gid=%d,
            )
        if path_text == %r:
            return _MingTestStat(
                metadata,
                mode=stat.S_IFLNK | 0o777,
                uid=%d,
                gid=%d,
            )
        return _MingTestStat(metadata, mode=stat.S_IFLNK | 0o777)
    if path_text == %r:
        mode = metadata.st_mode & ~0o777 | %d
        return _MingTestStat(metadata, mode=mode, uid=%d, gid=%d)
    if path_text == %r:
        mode = metadata.st_mode & ~0o022
        mode = mode | 0o111 if %r else mode & ~0o111
        mode |= %d
        return _MingTestStat(metadata, mode=mode, uid=%d, gid=%d)
    return metadata

def _ming_test_readlink(path, *args, **kwargs):
    path_text = str(path)
    if path_text in _ming_test_links:
        return _ming_test_links[path_text]
    return _ming_original_readlink(path, *args, **kwargs)

def _ming_test_is_symlink(path):
    if str(path) in _ming_test_links:
        return True
    return _ming_original_is_symlink(path)

pathlib.Path.lstat = _ming_test_lstat
pathlib.Path.is_symlink = _ming_test_is_symlink
os.readlink = _ming_test_readlink
""" % (
        links,
        str(first_link_path),
        first_link_uid,
        first_link_gid,
        str(second_link_path),
        second_link_uid,
        second_link_gid,
        str(helper_path),
        helper_mode,
        helper_uid,
        helper_gid,
        str(final_path),
        final_executable,
        final_write_bits,
        final_uid,
        final_gid,
    )
    return subprocess.run(
        [sys.executable, "-", str(root)],
        input=ownership_shim + desktop_backend_validator_source(),
        text=True,
        capture_output=True,
        check=False,
    )


class RequiredRuntimeDependencyContracts(unittest.TestCase):
    def test_package_installer_is_deployed_and_final_thunar_menu_offers_deb_install(self):
        self.assertIn("ming-package-installer-26.4.0-v4", DESKTOP)
        self.assertIn("/usr/local/sbin/ming-package-installer", DESKTOP)
        self.assertIn("/usr/local/bin/ming-package-install-gui", DESKTOP)
        self.assertIn("zenity --info", DESKTOP)
        self.assertIn("zenity --error", DESKTOP)
        self.assertIn("ming-phone-desktop --sync", DESKTOP)
        self.assertIn("launcher_warnings", DESKTOP)
        self.assertIn("软件已安装，但暂时无法启动", DESKTOP)
        final_menu = DESKTOP.split("configure_simplified_menus() {", 1)[1].split(
            "\n# ========================", 1)[0]
        self.assertIn("安装 DEB 软件包", final_menu)
        self.assertIn("<patterns>*.deb;*.DEB</patterns>", final_menu)
        self.assertIn("/usr/local/bin/ming-package-install-gui \"%f\"", final_menu)
        self.assertIn("以管理员身份编辑", final_menu)
        self.assertIn("以管理员身份打开", final_menu)
        self.assertNotIn("Garlic Claw", final_menu)

    def test_desktop_module_preserves_the_versioned_package_installer_symlink(self):
        function = DESKTOP.split("install_ming_shell_components() {", 1)[1].split(
            "\n# ========================", 1)[0]

        self.assertNotIn(
            'install -m 0755 "${asset_dir}/ming-package-installer.py" '
            '/usr/local/sbin/ming-package-installer',
            function,
        )
        self.assertNotIn(
            "ming-package-installer.py ming-spark-package-helper.py", function)
        for marker in (
            'installer_contract="ming-package-installer-26.4.0-v4"',
            'installer_target="/usr/local/lib/ming-os/package-installer-runtimes/'
            '${installer_contract}/ming-package-installer"',
            '! -L /usr/local/sbin/ming-package-installer',
            'readlink -f -- /usr/local/sbin/ming-package-installer',
            '"${installer_resolved}" != "${installer_target}"',
            '! -f "${installer_target}" || -L "${installer_target}"',
            'stat -c \'%a:%u:%g\' -- "${installer_target}"',
            '"${installer_meta}" != "755:0:0"',
            'sha256sum -- "${installer_asset}"',
            'sha256sum -- "${installer_target}"',
            '"${installer_actual_sha}" != "${installer_expected_sha}"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, function)
        self.assertIn(
            'install -m 0755 "${asset_dir}/ming-spark-package-helper.py"', function)
        self.assertIn(
            "cat > /usr/local/bin/ming-package-install-gui", function)

    def test_full_build_uses_the_same_immutable_installer_runtime_as_resume(self):
        marker = "deploy_package_installer_runtime() {"
        self.assertIn(marker, APPS)
        full_runtime = APPS.split(marker, 1)[1].split(
            "\ndeploy_spark_security_boundary() {", 1)[0]
        resume_runtime = RESUME.split("seed_resume_package_installer() {", 1)[1].split(
            "\nseed_resume_spark_security() {", 1)[0]
        required_markers = (
            'runtime_root="/usr/local/lib/ming-os/package-installer-runtimes"',
            'current_link="/usr/local/lib/ming-os/package-installer-current"',
            'node.targets[0].id in {"PACKAGE_INSTALLER_CONTRACT", "REQUIRED_COMMON_SHA256"}',
            'target="${runtime_root}/${contract}"',
            'mktemp -d "${runtime_root}/.stage.XXXXXX"',
            '"${stage}/ming-package-installer"',
            '"${stage}/ming-shell-common.py"',
            '"${target}/ming-package-installer"',
            '"${target}/ming-shell-common.py"',
            '"${current_link}/ming-package-installer"',
            '"${current_link}/ming-shell-common.py"',
            '/usr/local/sbin/.ming-package-installer.new',
            '/usr/local/lib/ming-os/.ming-shell-common.py.new',
        )
        for expected in required_markers:
            with self.subTest(marker=expected):
                self.assertIn(expected, full_runtime)
                self.assertIn(expected, resume_runtime)
        boundary = APPS.split("deploy_spark_security_boundary() {", 1)[1].split(
            "\ninstall_app_store() {", 1)[0]
        self.assertNotIn(
            "install -d -m 0755 /usr/local/sbin /usr/local/lib/ming-os",
            boundary,
        )
        self.assertIn("deploy_package_installer_runtime || return 1", boundary)
        self.assertNotIn(
            'install -m 0755 "${asset_dir}/ming-package-installer.py" '
            '/usr/local/sbin/ming-package-installer',
            boundary,
        )

    def test_full_and_resume_guard_runtime_root_before_staging(self):
        full_runtime = APPS.split("deploy_package_installer_runtime() {", 1)[1].split(
            "\ndeploy_spark_security_boundary() {", 1)[0]
        resume_runtime = RESUME.split("seed_resume_package_installer() {", 1)[1].split(
            "\nseed_resume_spark_security() {", 1)[0]
        guard = 'bash "${runtime_guard}" "${runtime_root}"'
        for label, function in (("full", full_runtime), ("resume", resume_runtime)):
            with self.subTest(path=label):
                self.assertIn(guard, function)
                self.assertLess(function.index(guard), function.index("mktemp -d"))
                self.assertNotIn('mkdir -p "${runtime_root}"', function)

    def test_package_install_gui_distinguishes_installation_from_launch_readiness(self):
        gui = package_install_gui_source()

        self.assertIn('result.get("ok") is True', gui)
        self.assertIn('result.get("launch_ready") is True', gui)
        self.assertIn("if not isinstance(result, dict):", gui)
        self.assertIn("if not isinstance(repaired, dict):", gui)
        self.assertIn("软件已安装，但暂时无法启动", gui)
        self.assertIn('"/usr/local/sbin/ming-package-installer", "repair"', gui)
        self.assertIn("/var/log/ming-os/package-installer.jsonl", gui)
        self.assertIn("timeout --foreground 8s ming-phone-desktop --sync", gui)
        embedded = gui.split("if python3 -", 1)[1].split("\n", 1)[1].split(
            "\nMINGPACKAGEUIPY", 1
        )[0]
        compile(embedded, "<ming-package-install-gui>", "exec")

    def test_package_install_gui_treats_non_object_json_as_a_readable_failure(self):
        completed = run_package_install_gui_python("[]")

        self.assertEqual(1, completed.returncode)
        self.assertNotIn("AttributeError", completed.stderr)
        self.assertIn("软件安装失败", completed.stderr)

    def test_package_install_gui_requires_literal_true_for_ok(self):
        completed = run_package_install_gui_python(
            '{"ok": "true", "launch_ready": true, "package": "sample"}'
        )

        self.assertEqual(1, completed.returncode)
        self.assertIn("软件安装失败", completed.stderr)

    def test_package_install_gui_requires_literal_true_for_launch_readiness(self):
        completed = run_package_install_gui_python(
            '{"ok": true, "launch_ready": "false", "package": "sample"}'
        )

        self.assertEqual(0, completed.returncode)
        self.assertIn("软件已安装，但暂时无法启动", completed.stderr)

    def test_package_install_gui_syncs_after_successful_unlaunchable_install(self):
        completed = run_package_install_gui_python(
            '{"ok": true, "launch_ready": false, "package": "sample"}'
        )
        gui = package_install_gui_source()
        success_branch = gui.split("\nMINGPACKAGEUIPY\nthen\n", 1)[1].split(
            "\nfi\nexit 1", 1
        )[0]

        self.assertEqual(0, completed.returncode)
        self.assertIn("软件已安装，但暂时无法启动", completed.stderr)
        self.assertIn("timeout --foreground 8s ming-phone-desktop --sync", success_branch)

    def test_package_install_gui_does_not_sync_after_failed_installation(self):
        completed = run_package_install_gui_python(
            '{"ok": false, "error": "install failed"}', installer_rc=4
        )
        gui = package_install_gui_source()
        failure_branch = gui.split("\nMINGPACKAGEUIPY\nthen\n", 1)[1].split(
            "\nfi\nexit 1", 1
        )[1]

        self.assertEqual(1, completed.returncode)
        self.assertIn("软件安装失败", completed.stderr)
        self.assertNotIn("ming-phone-desktop --sync", failure_branch)

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

    def test_build_apt_wrapper_uses_the_shared_package_manager_lock(self):
        wrapper = BUILD.split(
            "cat > \"${CHROOT_DIR}/usr/local/sbin/apt-build\" << 'APT_BUILD_WRAPPER'",
            1,
        )[1].split("APT_BUILD_WRAPPER", 1)[0]

        self.assertIn("flock", wrapper)
        self.assertIn("/run/lock/ming-package-manager.lock", wrapper)
        self.assertIn("DPkg::Lock::Timeout=60", wrapper)
        self.assertLess(wrapper.index("/usr/bin/flock"), wrapper.index("/usr/bin/apt-get"))

    def test_app_store_dependencies_use_the_locked_build_apt_wrapper(self):
        function = APPS.split("install_app_store() {", 1)[1].split(
            "cat > /usr/local/bin/ming-install-spark-store", 1
        )[0]

        self.assertIn("/usr/local/sbin/apt-build install", function)
        self.assertIn("libnotify-bin || return 1", function)
        self.assertNotIn("apt install", function)

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
                "desktop-base || return 1",
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

    def test_resume_revalidates_an_existing_package_installer_contract_directory(self):
        function = RESUME.split("seed_resume_package_installer() {", 1)[1].split(
            "\n}\n\nensure_resume_runtime_packages", 1
        )[0]

        self.assertIn('test ! -L "${target}"', function)
        self.assertIn('test ! -L "${target}/ming-package-installer"', function)
        self.assertIn('test ! -L "${target}/ming-shell-common.py"', function)
        self.assertIn("stat -c '%a:%u:%g'", function)
        self.assertIn('sha256sum "${target}/ming-package-installer"', function)
        self.assertIn('sha256sum "${target}/ming-shell-common.py"', function)
        self.assertIn("拒绝复用损坏的安装器运行时", function)

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
            "desktop-file-utils",
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
            write_vendor_spark_package(root)
            result = run_backend_validator(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_raw_spark_binaries_cannot_replace_the_vendor_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/bin/spark-store")
            write_executable(root, "opt/spark-store/bin/spark-store")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )

            result = run_backend_validator(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("/usr/local/bin/spark-store", result.stderr)

            write_vendor_spark_package(root)
            result = run_backend_validator(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_accepts_the_exact_package_link_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            write_vendor_spark_package(root)

            result = run_backend_validator(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_rejects_spoofed_or_unsafe_package_chains(self):
        cases = {
            "relative first link": {"first_target": "../../../opt/durapps/spark-store/bin/spark-store"},
            "other first target": {"first_target": SPARK_VENDOR_TARGET},
            "escaping second link": {"second_target": "../../../../../../outside/spark-store"},
            "dangling final target": {"final_exists": False},
            "regular wrapper spoof": {"first_is_regular": True},
            "non executable final target": {"final_executable": False},
            "wrong package version": {"version": "5.2.1.1"},
            "wrong package architecture": {"architecture": "arm64"},
            "wrong dpkg owner": {"owned_paths": (SPARK_VENDOR_TARGET,)},
        }
        for label, options in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                write_core_desktops(root)
                write_executable(root, "usr/bin/microsoft-edge-stable")
                write_executable(root, "usr/local/bin/ming-install-spark-store")
                write_executable(
                    root,
                    "usr/local/bin/ming-spark-store",
                    "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
                )
                write_vendor_spark_package(root, **options)

                result = run_backend_validator(root)
                self.assertNotEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_rejects_a_non_root_final_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            write_vendor_spark_package(root)

            result = run_backend_validator(root, final_uid=1000, final_gid=1000)
            self.assertNotEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_rejects_a_non_root_postinst_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            write_vendor_spark_package(root)

            result = run_backend_validator(
                root,
                first_link_uid=1000,
                first_link_gid=1000,
            )
            self.assertNotEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_rejects_a_non_root_package_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            write_vendor_spark_package(root)

            result = run_backend_validator(
                root,
                second_link_uid=1000,
                second_link_gid=1000,
            )
            self.assertNotEqual(0, result.returncode, result.stderr)

    def test_vendor_spark_gate_rejects_a_writable_final_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/microsoft-edge-stable")
            write_executable(root, "usr/local/bin/ming-install-spark-store")
            write_executable(
                root,
                "usr/local/bin/ming-spark-store",
                "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
            )
            write_vendor_spark_package(root)

            for write_bits in (0o020, 0o002):
                with self.subTest(write_bits=oct(write_bits)):
                    result = run_backend_validator(root, final_write_bits=write_bits)
                    self.assertNotEqual(0, result.returncode, result.stderr)

    def test_spark_mode_helper_gate_rejects_untrusted_runtime_policy(self):
        cases = (
            ("missing", {}, "missing"),
            ("symlink", {}, "symlink"),
            ("nonroot", {"helper_uid": 1000, "helper_gid": 1000}, None),
            ("writable", {"helper_mode": 0o775}, None),
            ("tampered", {}, "tampered"),
        )
        for label, validator_options, mutation in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                write_core_desktops(root)
                write_executable(root, "usr/bin/microsoft-edge-stable")
                write_executable(root, "usr/local/bin/ming-install-spark-store")
                write_executable(
                    root,
                    "usr/local/bin/ming-spark-store",
                    "#!/bin/sh\nexec pkexec /usr/local/bin/ming-install-spark-store \"$@\"\n",
                )
                write_vendor_spark_package(root)
                helper = root / "usr/local/libexec/ming-spark-build-mode"
                if mutation == "missing":
                    helper.unlink()
                elif mutation == "symlink":
                    helper.unlink()
                    write_symlink(
                        root,
                        "usr/local/libexec/ming-spark-build-mode",
                        "/tmp/untrusted-mode-resolver",
                    )
                elif mutation == "tampered":
                    write_spark_mode_helper(
                        root,
                        "#!/usr/bin/env bash\n"
                        "set -uo pipefail\n"
                        "resolve_spark_build_mode() { printf '%s\\n' development; }\n"
                        "curl https://example.invalid/policy\n"
                        "resolve_spark_build_mode\n",
                    )

                result = run_backend_validator(root, **validator_options)
                self.assertNotEqual(0, result.returncode, result.stderr)

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

    def test_control_center_is_not_overwritten_after_the_gtk4_wrapper_is_installed(self):
        self.assertEqual(
            1,
            DESKTOP.count("cat > /usr/local/bin/ming-control-center <<"),
            "a legacy control center must not replace the Ming Settings wrapper",
        )
        self.assertNotIn("nm-connection-editor", DESKTOP)

    def test_status_center_network_action_uses_ming_settings_and_reports_launch_errors(self):
        status = generated_script_source(
            "/usr/local/bin/ming-status-center", "STATUSCENTER"
        )
        self.assertIn(
            "('连接网络', 'network-wireless', ['ming-control-center', '--page', 'network'])",
            status,
        )
        self.assertIn("def launch_action", status)
        self.assertIn("无法打开", status)
        self.assertNotIn("nm-connection-editor", status)
        self.assertNotIn("subprocess.Popen(command, shell=True)", status)

    def test_welcome_network_button_uses_ming_settings_without_silent_failure(self):
        welcome = generated_script_source("/usr/local/bin/ming-welcome", "WELCOMEPY")
        self.assertIn(
            "subprocess.Popen(['ming-control-center', '--page', 'network']",
            welcome,
        )
        self.assertIn("无法打开网络设置", welcome)
        self.assertNotIn("nm-connection-editor", welcome)


if __name__ == "__main__":
    unittest.main()
