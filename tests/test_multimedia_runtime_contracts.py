import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
BASH = (
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and pathlib.Path(r"C:\Program Files\Git\bin\bash.exe").is_file()
    else "bash"
)


def heredoc(source, declaration, marker):
    start = source.index(declaration)
    end = source.index("\n" + marker, start + len(declaration))
    return source[start:end]


def spark_installer_source():
    return APPS.split(
        "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'", 1
    )[1].split("\nSPARKINSTALL", 1)[0]


def bash_path(path):
    value = str(path.resolve()).replace("\\", "/")
    if os.name == "nt":
        return "/%s%s" % (value[0].lower(), value[2:])
    return value


def write_shell_executable(path, content):
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | 0o111)


def spark_asset_gate_source(root):
    source = spark_installer_source().split("mkdir -p /root/.config", 1)[0]
    root_guard = """if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Administrator privileges are required to install Spark Store." >&2
    exit 1
fi

"""
    source = source.replace(root_guard, "")
    root_path = bash_path(root)
    replacements = {
        'readonly deb="/tmp/${deb_name}"':
            'readonly deb="%s/tmp/${deb_name}"' % root_path,
        'log="/var/log/ming-spark-store-install.log"':
            'log="%s/var/log/ming-spark-store-install.log"' % root_path,
        "/tmp/ming-build": root_path + "/tmp/ming-build",
        "/var/cache/ming-os": root_path + "/var/cache/ming-os",
        "/etc/os-release": root_path + "/etc/os-release",
    }
    for original, replacement in replacements.items():
        source = source.replace(original, replacement)
    return source


def run_spark_asset_gate(os_release, *, mode=None, cache_content=None):
    with tempfile.TemporaryDirectory(prefix="ming-spark-gate-") as directory:
        root = pathlib.Path(directory)
        (root / "etc").mkdir(parents=True)
        (root / "etc/os-release").write_text(os_release, encoding="utf-8")
        (root / "tmp").mkdir()
        (root / "var/log").mkdir(parents=True)
        if cache_content is not None:
            cache = root / "var/cache/ming-os/spark-store_5.2.1.0_amd64.deb"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(cache_content)

        fake_bin = root / "bin"
        fake_bin.mkdir()
        wget_log = root / "wget.log"
        write_shell_executable(
            fake_bin / "timeout",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "while [[ $# -gt 0 && \"$1\" == --* ]]; do shift; done\n"
            "[[ $# -gt 0 ]] && shift\n"
            "exec \"$@\"\n",
        )
        write_shell_executable(
            fake_bin / "wget",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf 'wget\\n' >>\"${MING_TEST_WGET_LOG}\"\n"
            "output=\n"
            "for argument in \"$@\"; do\n"
            "  case \"${argument}\" in --output-document=*) output=${argument#*=} ;; esac\n"
            "done\n"
            "[[ -n \"${output}\" ]]\n"
            "printf 'downloaded-but-untrusted' >\"${output}\"\n",
        )
        environment = dict(os.environ)
        environment.pop("MING_RELEASE_MODE", None)
        if mode is not None:
            environment["MING_RELEASE_MODE"] = mode
        script = (
            'export PATH="%s:/usr/local/bin:/usr/bin:/bin"\n' % bash_path(fake_bin)
            + 'export MING_TEST_WGET_LOG="%s"\n' % bash_path(wget_log)
            + spark_asset_gate_source(root)
        )
        completed = subprocess.run(
            [BASH],
            input=script,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        calls = wget_log.read_text(encoding="utf-8") if wget_log.exists() else ""
        return completed, calls


def apps_main_source():
    start = APPS.index("main() {")
    end = APPS.index("\n}\n\nmain", start) + 2
    return APPS[start:end]


class MultimediaRuntimeContracts(unittest.TestCase):
    def test_audio_session_helper_is_deployed_with_login_recovery(self):
        install = DESKTOP.split("install_ming_shell_components() {", 1)[1].split(
            "\n}\n\ninstall_ming_files", 1
        )[0]
        self.assertIn("ming-audio-session.py", install)
        self.assertIn("/usr/local/bin/ming-audio-session", install)
        self.assertIn("ming-audio-session.desktop", DESKTOP)
        self.assertIn("ming-audio-session ensure", DESKTOP)

    def test_session_supervisor_rechecks_audio_after_resume_or_device_changes(self):
        supervisor = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'", 1
        )[1].split("MINGSESSIONHEALTH", 1)[0]
        self.assertIn("AUDIO_CHECK_INTERVAL", supervisor)
        self.assertIn("ensure_audio_session", supervisor)
        self.assertIn("ming-audio-session ensure --json", supervisor)
        self.assertIn("run_bounded 6", supervisor)
        self.assertIn("ensure_audio_session", supervisor.split("startup_once()", 1)[1])
        self.assertIn("ensure_audio_session", supervisor.split("supervise_once()", 1)[1])

    def test_edge_and_wechat_preflight_audio_without_forcing_a_valid_output(self):
        edge = heredoc(APPS, "cat > /usr/local/bin/ming-edge << 'MINGEDGE'", "MINGEDGE")
        wechat = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("ming-audio-session ensure", edge)
        self.assertIn("ming-audio-session ensure", wechat)
        self.assertIn("ming-device-control audio-repair-playback", wechat)
        self.assertLess(
            wechat.index("audio-repair-playback"),
            wechat.index("audio-repair-call"),
        )

    def test_spark_download_uses_verified_local_deb_install_and_refreshes_launchers(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("ming-phone-desktop --sync", installer)
        self.assertIn("update-desktop-database", installer)

    def test_spark_install_is_pinned_verified_bounded_and_cache_gated(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )

        for marker in (
            "5.2.1.0",
            "spark-store_5.2.1.0_amd64.deb",
            "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55",
            "/releases/download/5.2.1.0/",
            "MING_SPARK_STORE_ASSET",
            "/tmp/ming-build/assets/",
            "MING_RELEASE_MODE",
            "sha256sum",
            "timeout --foreground 300s",
            "--connect-timeout=10",
            "--read-timeout=60",
        ):
            self.assertIn(marker, installer)
        self.assertNotIn("/releases/latest", installer)
        self.assertNotIn("browser_download_url", installer)
        self.assertNotIn('Downloading Spark Store: ${url}', installer)

    def test_release_build_stages_only_the_verified_pinned_spark_asset(self):
        for marker in (
            "MING_SPARK_STORE_ASSET",
            "spark-store_5.2.1.0_amd64.deb",
            "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55",
            'MING_RELEASE_MODE="${MING_RELEASE_MODE}"',
            '[[ "${MING_RELEASE_MODE}" == "release" ]]',
        ):
            self.assertIn(marker, BUILD)
        staging = BUILD.split("# Formal builds consume a locally controlled Spark asset", 1)[1]
        staging = staging.split("# Stage only the pinned Papyrus release asset", 1)[0]
        self.assertIn('"${CHROOT_DIR}/var/cache/ming-os"', staging)
        self.assertIn(
            '"${CHROOT_DIR}/var/cache/ming-os/${SPARK_STORE_DEB_NAME}"',
            staging,
        )

    def test_release_identity_without_environment_never_downloads_when_cache_is_missing(self):
        completed, wget_calls = run_spark_asset_gate(
            "MING_RELEASE_STAGE=stable\nVERSION_ID=26.4.0.1\n"
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("", wget_calls)
        self.assertIn("E_PACKAGE_FAILED", completed.stderr)

    def test_release_rejects_a_bad_cached_digest_without_downloading(self):
        completed, wget_calls = run_spark_asset_gate(
            "MING_RELEASE_STAGE=stable\nVERSION_ID=26.4.0.1\n",
            cache_content=b"tampered release asset",
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("", wget_calls)
        self.assertIn("E_PACKAGE_FAILED", completed.stderr)

    def test_unknown_or_conflicting_release_identity_fails_before_download(self):
        cases = (
            ("MING_RELEASE_STAGE=preview\nVERSION_ID=26.4.0.1\n", None),
            ("MING_RELEASE_STAGE=stable\nVERSION_ID=26.4.0.1\n", "development"),
            ("MING_RELEASE_STAGE=development\nVERSION_ID=26.4.0.1-development\n", "release"),
        )
        for os_release, mode in cases:
            with self.subTest(os_release=os_release, mode=mode):
                completed, wget_calls = run_spark_asset_gate(os_release, mode=mode)
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("", wget_calls)
                self.assertIn("E_PACKAGE_FAILED", completed.stderr)

    def test_explicit_development_download_rejects_a_bad_digest(self):
        completed, wget_calls = run_spark_asset_gate(
            "MING_RELEASE_STAGE=development\nVERSION_ID=26.4.0.1-development\n",
            mode="development",
        )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("wget\n", wget_calls)
        self.assertIn("E_PACKAGE_FAILED", completed.stderr)

    def test_release_app_store_install_failure_propagates_from_main(self):
        harness = """
run_required_step() { "$@"; }
run_optional_step() { "$@" || true; }
install_xfce_desktop() { return 0; }
install_fonts() { return 0; }
install_required_desktop_runtime() { return 0; }
generate_edge_video_samples() { return 0; }
enable_bluetooth_after_runtime() { return 0; }
install_fcitx5() { return 0; }
deploy_eyecare() { return 0; }
install_edge() { return 0; }
install_wps_office() { return 0; }
install_wechat() { return 0; }
install_app_store() { return 42; }
install_utilities() { return 0; }
"""
        source = harness + apps_main_source() + "\nmain\n"
        for mode, expected_success in (("release", False), ("development", True)):
            with self.subTest(mode=mode):
                environment = dict(os.environ)
                environment["MING_RELEASE_MODE"] = mode
                completed = subprocess.run(
                    [BASH],
                    input=source,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )
                self.assertEqual(expected_success, completed.returncode == 0)

    def test_installer_requires_the_exact_installed_package_version(self):
        installer = spark_installer_source()
        self.assertIn("verify_installed_spark_package() {", installer)
        function = installer.split("verify_installed_spark_package() {", 1)[1]
        function = "verify_installed_spark_package() {" + function.split("\n}\n", 1)[0] + "\n}\n"
        harness = "dpkg-query() { printf '%s' \"${MING_TEST_DPKG_RESULT}\"; }\n" + function
        cases = (
            ("ii \t5.2.1.0", True),
            ("ii \t5.2.1.1", False),
            ("rc \t5.2.1.0", False),
            ("", False),
        )
        for result, expected_success in cases:
            with self.subTest(result=result):
                environment = dict(os.environ)
                environment["MING_TEST_DPKG_RESULT"] = result
                completed = subprocess.run(
                    [BASH],
                    input=harness + "\nverify_installed_spark_package\n",
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )
                self.assertEqual(expected_success, completed.returncode == 0)

    def test_invalid_installer_json_is_a_package_failure_without_traceback(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        validator = installer.split("<<'SPARKRESULTPY'\n", 1)[1].split(
            "\nSPARKRESULTPY", 1
        )[0]

        with tempfile.TemporaryDirectory() as directory:
            result_file = pathlib.Path(directory) / "result.json"
            for raw in ("", "[]", "{broken", "{}"):
                with self.subTest(raw=raw):
                    result_file.write_text(raw, encoding="utf-8")
                    completed = subprocess.run(
                        [sys.executable, "-c", validator, str(result_file)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(1, completed.returncode)
                    self.assertEqual("E_PACKAGE_FAILED", completed.stderr.strip())
                    self.assertNotIn("Traceback", completed.stderr)

    def test_spark_launcher_uses_only_the_vendor_wrapper(self):
        wrapper = APPS.split(
            "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'", 1
        )[1].split("MINGSPARK", 1)[0]

        self.assertIn("/usr/local/bin/spark-store", wrapper)
        self.assertNotIn("/usr/bin/spark-store", wrapper)
        self.assertNotIn("/opt/spark-store", wrapper)

    def test_spark_install_rejects_json_without_launch_readiness(self):
        installer = APPS.split(
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'", 1
        )[1].split("SPARKINSTALL", 1)[0]

        self.assertIn('result.get("launch_ready") is True', installer)
        self.assertIn("E_LAUNCH_NOT_READY", installer)
        self.assertLess(
            installer.index('result.get("launch_ready") is True'),
            installer.index('echo "Spark Store installed."'),
        )
        self.assertIn("target_user=", installer)
        self.assertIn("getent passwd", installer)

    def test_clean_build_spark_fallback_uses_locked_apt_and_requires_vendor_wrapper(self):
        installer = APPS.split(
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'", 1
        )[1].split("SPARKINSTALL", 1)[0]
        fallback = installer.split("\nelse\n", 1)[1].split(
            "\n\n# Spark Store currently", 1
        )[0]

        self.assertIn("flock", fallback)
        self.assertIn("/run/lock/ming-package-manager.lock", fallback)
        self.assertIn("DPkg::Lock::Timeout=60", fallback)
        self.assertIn("/usr/local/bin/spark-store", fallback)
        self.assertIn("E_LAUNCH_NOT_READY", fallback)
        self.assertIn("apt_output", fallback)
        self.assertIn('if run_locked_apt install "${deb}"', fallback)
        self.assertNotIn("if ! run_locked_apt", fallback)
        self.assertNotRegex(fallback, r"(?m)^ {4}apt-get\b")
        self.assertNotIn('cat "${apt_output}" >>"${log}"', fallback)

    def test_official_wechat_download_uses_the_same_verified_local_deb_path(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'",
            "WECHATINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("Administrator privileges are required", installer)
        self.assertNotIn("sudo apt install", installer)

    def test_wechat_launcher_uses_only_dpkg_owned_strict_desktop_entries(self):
        wrapper = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("find_wechat_argv", wrapper)
        self.assertIn("dpkg-query -S --", wrapper)
        self.assertIn("parse_desktop_file", wrapper)
        self.assertIn("desktop_launch_diagnostic", wrapper)
        self.assertIn("mapfile -d '' -t wechat_argv", wrapper)
        self.assertIn('exec "${wechat_argv[@]}" "$@"', wrapper)
        self.assertNotIn("eval ", wrapper)

    def test_build_gate_requires_audio_and_local_package_helpers(self):
        # The rootfs validator contains embedded Python, so its first `}` is
        # not the end of the shell function.  Check the full generated source
        # instead of truncating valid gate entries at an inner Python block.
        self.assertIn("usr/local/bin/ming-audio-session", BUILD)
        self.assertIn("usr/local/sbin/ming-package-installer", BUILD)


if __name__ == "__main__":
    unittest.main()
