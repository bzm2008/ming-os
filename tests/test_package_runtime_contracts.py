import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
APPIMAGE = (ROOT / "assets" / "ming-appimage-installer.py").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
PACKAGE = (ROOT / "assets" / "ming-package-installer.py").read_text(encoding="utf-8")
GIT_BASH = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")


class PackageRuntimeContracts(unittest.TestCase):
    def test_desktop_catalog_scans_verified_opt_app_proxies(self):
        self.assertIn('Path("/usr/local/share/applications")', PHONE)

    def test_appimage_launcher_is_readable_by_desktop_catalog(self):
        self.assertIn("os.chmod(desktop, 0o644)", APPIMAGE)

    def test_appimage_runner_has_a_no_fuse_extract_fallback(self):
        self.assertIn("ming-appimage-run", DESKTOP)
        self.assertIn("--appimage-extract-and-run", DESKTOP)
        self.assertIn("/dev/fuse", DESKTOP)

    def test_thunar_accepts_both_appimage_filename_cases(self):
        self.assertIn("<patterns>*.AppImage;*.appimage</patterns>", DESKTOP)

    def test_spark_desktop_copy_uses_the_shared_launch_broker(self):
        self.assertIn(
            "Exec=/usr/local/bin/ming-launch --desktop-file /usr/share/applications/spark-store.desktop --source desktop",
            APPS,
        )

    def test_app_library_launches_apps_only_through_the_shared_broker(self):
        library = DESKTOP.split(
            "cat > /usr/local/bin/ming-app-library << 'APPLIB'", 1
        )[1].split("\nAPPLIB", 1)[0]
        library = library.split("    def launch(self, app):", 1)[1]
        self.assertIn("ming-launch", library)
        self.assertIn("--desktop-file", library)
        self.assertNotIn("shell=True", library)
        self.assertNotIn("info.launch([], None)", library)

    def test_package_install_uses_the_shared_desktop_refresh_hook(self):
        self.assertIn("ming-refresh-desktop-state", PACKAGE)
        self.assertIn("ming-refresh-desktop-state", APPS)

    def test_privileged_desktop_refresh_drops_to_user_without_literal_patch_tokens(self):
        refresh = DESKTOP.split(
            "cat > /usr/local/bin/ming-refresh-desktop-state << 'MINGREFRESHDESKTOP'",
            1,
        )[1].split("MINGREFRESHDESKTOP", 1)[0]
        self.assertIn("runuser -u \"${target_user}\" -- env \\", refresh)
        self.assertNotIn("env +", refresh)

    def test_privileged_desktop_refresh_fails_when_no_session_user_can_be_resolved(self):
        refresh = DESKTOP.split(
            "cat > /usr/local/bin/ming-refresh-desktop-state << 'MINGREFRESHDESKTOP'",
            1,
        )[1].split("MINGREFRESHDESKTOP", 1)[0]
        unresolved = refresh.split(
            'if [[ -z "${target_user}" ]] || ! id "${target_user}" >/dev/null 2>&1; then',
            1,
        )[1].split("fi", 1)[0]
        self.assertIn("exit 1", unresolved)
        self.assertNotIn("exit 0", unresolved)

    def test_spark_wrapper_does_not_accept_a_short_lived_process_as_ready(self):
        self.assertTrue(GIT_BASH.is_file(), "Git Bash is required for wrapper regression")
        marker = "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'\n"
        wrapper = APPS.split(marker, 1)[1].split("\nMINGSPARK\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="ming-spark-wrapper-") as directory:
            root = pathlib.Path(directory)
            fake = root / "spark-store"
            fake.write_text("#!/usr/bin/env bash\nsleep 0.2\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            wrapper = wrapper.replace(
                "for candidate in /usr/bin/spark-store /opt/spark-store/bin/spark-store; do",
                'for candidate in "${MING_TEST_SPARK_BIN}"; do',
            )
            script = root / "ming-spark-store"
            script.write_text(wrapper, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), str(script).replace("\\", "/")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20,
                env={
                    **os.environ,
                    "HOME": str(root),
                    "MING_TEST_SPARK_BIN": str(fake).replace("\\", "/"),
                },
            )
        self.assertNotEqual(0, result.returncode)

    def test_spark_wrapper_does_not_accept_a_process_that_exits_after_one_second(self):
        self.assertTrue(GIT_BASH.is_file(), "Git Bash is required for wrapper regression")
        marker = "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'\n"
        wrapper = APPS.split(marker, 1)[1].split("\nMINGSPARK\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="ming-spark-wrapper-1s-") as directory:
            root = pathlib.Path(directory)
            fake = root / "spark-store"
            fake.write_text("#!/usr/bin/env bash\nsleep 1.2\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            wrapper = wrapper.replace(
                "for candidate in /usr/bin/spark-store /opt/spark-store/bin/spark-store; do",
                'for candidate in "${MING_TEST_SPARK_BIN}"; do',
            )
            script = root / "ming-spark-store"
            script.write_text(wrapper, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), str(script).replace("\\", "/")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20,
                env={
                    **os.environ,
                    "HOME": str(root),
                    "MING_TEST_SPARK_BIN": str(fake).replace("\\", "/"),
                },
            )
        self.assertNotEqual(0, result.returncode)

    def test_spark_wrapper_requires_a_stable_owned_process_or_window(self):
        marker = "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'\n"
        wrapper = APPS.split(marker, 1)[1].split("\nMINGSPARK\n", 1)[0]
        self.assertIn("stable_checks >= 8", wrapper)
        self.assertIn("timeout --foreground 2s wmctrl", wrapper)
        self.assertNotIn("pgrep -f '[/](spark-store)( |$)'", wrapper)

    def test_package_gui_explains_refresh_warning_after_successful_install(self):
        self.assertIn("installed_with_refresh_warning", DESKTOP)
        self.assertIn("桌面刷新失败", DESKTOP)
        self.assertIn("刷新/重试", DESKTOP)

    def test_privileged_gui_actions_use_one_whitelisted_polkit_bridge(self):
        opener = "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'"
        self.assertIn(opener, DESKTOP)
        bridge = DESKTOP.split(opener, 1)[1].split("\nMINGAUTHORIZE", 1)[0]
        for route in ("package", "spark", "broadcom", "radio"):
            self.assertIn(route + ")", bridge)
        self.assertIn("Error creating textual authentication agent", bridge)
        self.assertIn("/dev/tty", bridge)
        self.assertIn("当前无可用图形授权代理", bridge)
        self.assertNotIn("eval ", bridge)
        self.assertNotIn("sudo ", bridge)
        self.assertNotIn("su ", bridge)

    def test_build_gate_requires_the_authorization_bridge(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn('"usr/local/bin/ming-authorized-action"', build)

    def test_package_and_spark_callers_use_the_ming_authorization_bridge(self):
        package_gui = DESKTOP.split(
            "cat > /usr/local/bin/ming-package-install-gui << 'MINGPACKAGEGUI'", 1
        )[1].split("\nMINGPACKAGEGUI", 1)[0]
        self.assertIn("ming-authorized-action package install", package_gui)
        self.assertNotIn("pkexec /usr/local/sbin/ming-package-installer", package_gui)

        spark_caller = APPS.split(
            "cat > \"$spark_shell_caller\" << 'MINGSPARKCALLER'", 1
        )[1].split("\nMINGSPARKCALLER", 1)[0]
        self.assertIn("ming-authorized-action spark", spark_caller)
        self.assertNotIn("exec pkexec /usr/local/sbin/ming-spark-package-control", spark_caller)

    def test_authorization_bridge_translates_headless_pkexec_failure(self):
        self.assertTrue(GIT_BASH.is_file(), "Git Bash is required for authorization regression")
        opener = "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'"
        bridge = DESKTOP.split(opener, 1)[1].split("\nMINGAUTHORIZE", 1)[0]
        with tempfile.TemporaryDirectory(prefix="ming-authorize-") as directory:
            root = pathlib.Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            driver = root / "ming-broadcom-driver"
            driver.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            driver.chmod(0o755)
            pkexec = bin_dir / "pkexec"
            pkexec.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'Error creating textual authentication agent: /dev/tty: No such device or address' >&2\n"
                "exit 127\n",
                encoding="utf-8",
            )
            pkexec.chmod(0o755)
            bridge = bridge.replace(
                "/usr/local/sbin/ming-broadcom-driver", str(driver).replace("\\", "/")
            )
            script = root / "ming-authorized-action"
            script.write_text(bridge, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [str(GIT_BASH), str(script).replace("\\", "/"), "broadcom", "install"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")},
            )
        self.assertEqual(127, result.returncode)
        self.assertIn("当前无可用图形授权代理", result.stderr)
        self.assertNotIn("Error creating textual authentication agent", result.stderr)


if __name__ == "__main__":
    unittest.main()
