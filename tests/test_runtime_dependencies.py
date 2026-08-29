import pathlib
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
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
    "pipewire",
    "pipewire-pulse",
    "pipewire-alsa",
    "wireplumber",
    "libspa-0.2-bluetooth",
    "pulseaudio-utils",
    "alsa-utils",
    "bluez",
    "upower",
    "pkexec",
    "polkitd",
    "lxpolkit",
    "libnotify-bin",
    "zenity",
    "x11-utils",
    "x11-xserver-utils",
    "desktop-file-utils",
    "dbus-user-session",
    "dbus-x11",
    "libpam-systemd",
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
        "ming-firefox.desktop": "/usr/local/bin/ming-firefox",
        "ming-store.desktop": "/usr/local/bin/ming-store",
        "ming-toolbox.desktop": "/usr/local/bin/ming-toolbox",
        "xiahai-xiaoming.desktop": "/opt/xiahai-xiaoming/xiahai-xiaoming",
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
    def test_package_installer_is_discoverable_from_a_normal_user_path(self):
        self.assertIn(
            'cat > /usr/local/bin/ming-package-installer <<',
            DESKTOP,
        )
        self.assertIn(
            'exec /usr/local/sbin/ming-package-installer "$@"',
            DESKTOP,
        )

    def test_package_installer_is_deployed_and_final_thunar_menu_offers_deb_install(self):
        self.assertIn("ming-package-installer.py", DESKTOP)
        self.assertIn("/usr/local/sbin/ming-package-installer", DESKTOP)
        self.assertNotIn("/usr/local/bin/ming-package-install-gui", DESKTOP)
        self.assertIn("ming-store --local-deb", DESKTOP)
        self.assertIn("ming-phone-desktop --sync", DESKTOP)
        final_menu = DESKTOP.split("configure_simplified_menus() {", 1)[1].split(
            "\n# ========================", 1)[0]
        self.assertIn("安装 DEB 软件包", final_menu)
        self.assertIn("<patterns>*.deb</patterns>", final_menu)
        self.assertIn("/usr/local/bin/ming-store --local-deb \"%f\"", final_menu)
        self.assertIn("以管理员身份编辑", final_menu)
        self.assertIn("以管理员身份打开", final_menu)
        self.assertNotIn("Garlic Claw", final_menu)
        self.assertNotIn("garlic-claw", final_menu)

    def test_downloaded_debs_have_a_default_mime_handler_not_just_a_context_menu(self):
        self.assertIn("ming-package-installer.desktop", DESKTOP)
        self.assertIn("MimeType=application/vnd.debian.binary-package;", DESKTOP)
        self.assertIn("Exec=/usr/local/bin/ming-store --local-deb %f", DESKTOP)
        self.assertIn(
            'config["Default Applications"]["application/vnd.debian.binary-package"]',
            DESKTOP,
        )

    def test_downloaded_appimages_have_a_context_menu_and_mime_handler(self):
        self.assertIn("ming-appimage-installer.desktop", DESKTOP)
        appimage_desktop = DESKTOP.split(
            "cat > /usr/share/applications/ming-appimage-installer.desktop << 'MINGAPPIMAGEINSTALLERDESKTOP'",
            1,
        )[1].split("MINGAPPIMAGEINSTALLERDESKTOP", 1)[0]
        self.assertIn("MimeType=application/x-appimage;", appimage_desktop)
        self.assertNotIn("application/x-executable", appimage_desktop)
        self.assertIn("Exec=/usr/local/bin/ming-appimage-install-gui %f", DESKTOP)
        mime_apps = DESKTOP.split("python3 - \"/home/${MING_USER}/.config/mimeapps.list\"", 1)[1]
        self.assertNotIn(
            'config["Default Applications"]["application/x-executable"]', mime_apps
        )
        self.assertIn(
            'remove_handler("Default Applications", "application/x-executable", '
            '"ming-appimage-installer.desktop")',
            mime_apps,
        )
        self.assertIn(
            'remove_handler("Added Associations", "application/x-executable", '
            '"ming-appimage-installer.desktop")',
            mime_apps,
        )
        final_menu = DESKTOP.split("configure_simplified_menus() {", 1)[1].split(
            "\n# ========================", 1)[0]
        self.assertIn("安装 AppImage", final_menu)
        self.assertIn("<patterns>*.AppImage;*.appimage</patterns>", final_menu)
        self.assertIn("/usr/local/bin/ming-appimage-install-gui \"%f\"", final_menu)

    def test_appimage_install_refreshes_desktop_catalog_and_dock(self):
        appimage_gui = DESKTOP.split(
            "cat > /usr/local/bin/ming-appimage-install-gui << 'MINGAPPIMAGEGUI'", 1
        )[1].split("MINGAPPIMAGEGUI", 1)[0]
        self.assertIn("update-desktop-database", appimage_gui)
        self.assertIn("ming-phone-desktop --sync", appimage_gui)
        self.assertIn("ming-refresh-dock-launchers", appimage_gui)

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
        self.assertIn('run_required_step install_firefox_esr', main)
        self.assertNotIn('run_required_step install_app_store', main)

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
        self.assertIn('exec "${SCRIPT_DIR}/build_onion_os.sh" --resume', RESUME)
        self.assertIn('MING_BUILD_FROM', RESUME)
        for package in REQUIRED_PACKAGES:
            self.assertIn(package, APPS + BUILD)

    def test_build_and_target_apt_sources_do_not_depend_on_single_tuna_mirror(self):
        self.assertIn(
            'readonly DEBIAN_MIRROR="${MING_DEBIAN_MIRROR:-https://deb.debian.org/debian/}"',
            BUILD,
        )
        self.assertIn(
            'readonly DEBIAN_SECURITY_MIRROR="${MING_DEBIAN_SECURITY_MIRROR:-https://security.debian.org/debian-security}"',
            BUILD,
        )
        self.assertIn('local debian_mirror="${MING_DEBIAN_MIRROR:-https://deb.debian.org/debian/}"', BASE)
        self.assertIn(
            'local security_mirror="${MING_DEBIAN_SECURITY_MIRROR:-https://security.debian.org/debian-security}"',
            BASE,
        )
        self.assertIn("deb ${debian_mirror} trixie main", BASE)
        self.assertIn("deb ${security_mirror} trixie-security main", BASE)
        self.assertNotIn("mirrors.tuna.tsinghua.edu.cn", BUILD)
        self.assertIn("mirrors.tuna.tsinghua.edu.cn/debian", BASE)
        self.assertIn("deb.debian.org/debian", BASE)

    def test_build_uses_reusable_debootstrap_cache_for_fast_rebuilds(self):
        self.assertIn('readonly APT_ARCHIVES_CACHE="${MING_APT_ARCHIVES_CACHE:-${LINUX_WORKDIR}/apt-archives}"', BUILD)
        self.assertIn('mkdir -p "${APT_ARCHIVES_CACHE}"', BUILD)
        self.assertIn('--cache-dir="${APT_ARCHIVES_CACHE}"', BUILD)

    def test_resume_rewrites_chroot_sources_to_official_debian_and_retries_update(self):
        self.assertIn("MING_DEBIAN_MIRROR", BUILD)
        self.assertIn("MING_DEBIAN_SECURITY_MIRROR", BUILD)
        self.assertIn('exec "${SCRIPT_DIR}/build_onion_os.sh" --resume', RESUME)
        self.assertIn("Acquire::Retries=5", BUILD)

    def test_ming_store_runtime_is_deployed_without_spark_or_apm_dependencies(self):
        for asset in ("ming-store.py", "ming-store-core.py", "ming-store-control.py"):
            self.assertIn(asset, DESKTOP)
        for retired in ("install_spark_store_dependencies", "ming-spark-aria2c", "ming-spark-backend-status"):
            self.assertNotIn(retired, APPS)

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
            "zenity",
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
            "ming-firefox.desktop",
            "ming-store.desktop",
            "ming-toolbox.desktop",
            "xiahai-xiaoming.desktop",
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

    def test_firefox_wrapper_without_a_real_browser_backend_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            result = run_backend_validator(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("missing Firefox ESR browser backend", result.stderr)

    def test_ming_store_desktop_requires_an_executable_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_core_desktops(root)
            write_executable(root, "usr/bin/firefox-esr")
            (root / "usr/local/bin/ming-store").unlink()
            result = run_backend_validator(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("unresolved Exec target in ming-store.desktop", result.stderr)

            write_executable(root, "usr/local/bin/ming-store")
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
