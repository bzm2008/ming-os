import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")


def heredoc(source, declaration, marker):
    start = source.index(declaration)
    end = source.index("\n" + marker, start + len(declaration))
    return source[start:end]


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

    def test_firefox_and_wechat_preflight_audio_without_forcing_a_valid_output(self):
        firefox = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'",
            "MINGFIREFOX",
        )
        wechat = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("ming-audio-session ensure", firefox)
        self.assertIn("ming-audio-session ensure", wechat)
        self.assertIn("ming-device-control audio-repair-playback", wechat)
        self.assertLess(
            wechat.index("audio-repair-playback"),
            wechat.index("audio-repair-call"),
        )

    def test_spark_repair_uses_the_verified_local_deb_and_refreshes_launchers(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("ming-phone-desktop --sync", installer)
        self.assertIn("sha256sum -c", installer)
        self.assertIn("/usr/share/ming-os/vendor/spark-store", installer)
        self.assertIn("target_user=", installer)
        self.assertIn("getent passwd", installer)
        self.assertNotIn("gitee.com/api", installer)
        self.assertNotIn("wget", installer)
        self.assertNotIn("curl", installer)

    def test_spark_system_launcher_is_trusted_for_the_launch_broker(self):
        finalize = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
        receipt = finalize[finalize.index("seed_trusted_desktop_receipts"):
                           finalize.index("# Keep the shipped desktop intentional")]
        self.assertIn('"spark-store.desktop"', receipt)

    def test_spark_desktop_and_dock_use_the_same_verified_launch_path(self):
        self.assertIn("Exec=/usr/local/bin/ming-spark-store", APPS)
        self.assertIn('"spark-store:spark-store.desktop"', DESKTOP)
        self.assertIn("exec_line=\"/usr/local/bin/ming-launch --desktop-file", DESKTOP)

    def test_spark_wrapper_requires_process_or_window_readiness_before_success(self):
        wrapper = heredoc(APPS, "cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'", "MINGSPARK")
        self.assertIn("wait_for_spark_ready", wrapper)
        self.assertIn("spark_window_visible", wrapper)
        self.assertIn("[[ \"${rc}\" -ne 0 ]] || rc=1", wrapper)
        self.assertNotIn("daemonized successfully", wrapper)

    def test_spark_is_a_verified_build_asset_and_runtime_repair_never_downloads_it(self):
        app_store = APPS.split("install_app_store() {", 1)[1].split("\n}", 1)[0]
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'",
            "SPARKINSTALL",
        )
        expected_sha256 = "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55"
        self.assertIn("spark-store_5.2.1.0_amd64.deb", app_store)
        self.assertIn(expected_sha256, app_store)
        self.assertIn("sha256sum -c", app_store)
        self.assertIn("/usr/share/ming-os/vendor/spark-store", installer)
        self.assertIn(expected_sha256, installer)
        self.assertNotIn("gitee.com/api", installer)
        self.assertNotIn("wget", installer)
        self.assertNotIn("curl", installer)
        self.assertIn(
            "Exec=/usr/local/bin/ming-package-install-gui /usr/share/ming-os/vendor/spark-store/spark-store_5.2.1.0_amd64.deb",
            APPS,
        )

    def test_spark_bundle_has_a_machine_readable_source_receipt_and_expected_hash(self):
        bundle = ROOT / "assets" / "vendor" / "spark-store" / "spark-store_5.2.1.0_amd64.deb"
        receipt = ROOT / "assets" / "vendor" / "spark-store" / "receipt.json"
        self.assertTrue(bundle.is_file())
        self.assertTrue(receipt.is_file())
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual("5.2.1.0", metadata["version"])
        self.assertEqual("spark-store_5.2.1.0_amd64.deb", metadata["asset"])
        self.assertEqual(
            "https://gitee.com/spark-store-project/spark-store/releases/download/5.2.1.0/spark-store_5.2.1.0_amd64.deb",
            metadata["source_url"],
        )
        self.assertEqual(
            "88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55",
            hashlib.sha256(bundle.read_bytes()).hexdigest().upper(),
        )

    def test_spark_privileged_actions_use_the_ming_allowlisted_helper(self):
        self.assertIn("ming-spark-package-control", APPS)
        self.assertIn("org.ming.spark.package-control", APPS)
        self.assertIn("auth_admin_keep", APPS)
        self.assertIn("MING_SPARK_PACKAGE_PATTERN", APPS)
        self.assertNotIn("eval ", heredoc(
            APPS,
            "cat > /usr/local/sbin/ming-spark-package-control << 'MINGSPARKCONTROL'",
            "MINGSPARKCONTROL",
        ))

    def test_spark_vendor_root_policies_are_replaced_with_ming_policy(self):
        install = APPS.split("install_app_store() {", 1)[1].split(
            "\n# ======================== 附加实用工具", 1)[0]
        self.assertIn("store.spark-app.spark-store.policy", install)
        self.assertIn("store.spark-app.ssinstall.policy", install)
        self.assertIn("ming-spark-package-control", install)
        self.assertNotIn("<allow_active>yes</allow_active>", install)

    def test_apm_backend_is_disabled_when_upstream_version_is_too_old(self):
        status = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-spark-backend-status << 'MINGSPARKSTATUS'",
            "MINGSPARKSTATUS",
        )
        self.assertIn("APM_MIN_VERSION=1.2.2", status)
        self.assertIn("dpkg --compare-versions", status)
        self.assertIn("APM 后端版本过低", status)
        self.assertIn("--json", status)

    def test_spark_control_rejects_shell_metacharacters_and_unknown_actions(self):
        control = heredoc(
            APPS,
            "cat > /usr/local/sbin/ming-spark-package-control << 'MINGSPARKCONTROL'",
            "MINGSPARKCONTROL",
        )
        self.assertRegex(control, r"\[A-Za-z0-9\]\[A-Za-z0-9.+-\]\{0,127\}")
        self.assertIn("install|remove|refresh|status", control)
        for token in ("eval ", "bash -c", "sh -c"):
            self.assertNotIn(token, control)

    def test_apm_actions_require_the_ace_runtime_as_well_as_apm(self):
        control = heredoc(
            APPS,
            "cat > /usr/local/sbin/ming-spark-package-control << 'MINGSPARKCONTROL'",
            "MINGSPARKCONTROL",
        )
        apm_branch = control.split("    apm)", 1)[1].split("    *)", 1)[0]
        self.assertIn("ace_backend_ready", control)
        self.assertIn("ace_backend_ready", apm_branch)
        self.assertIn("APM/ACE 后端未就绪", apm_branch)

    def test_apm_root_helper_allows_only_mutating_package_actions(self):
        control = heredoc(
            APPS,
            "cat > /usr/local/sbin/ming-spark-package-control << 'MINGSPARKCONTROL'",
            "MINGSPARKCONTROL",
        )
        apm_branch = control.split("    apm)", 1)[1].split("    *)", 1)[0]
        self.assertIn("install|remove|ssinstall", apm_branch)
        for action in ("start", "launch", "list", "search", "show"):
            self.assertNotIn(action, apm_branch)
        caller = heredoc(
            APPS,
            "cat > \"$spark_shell_caller\" << 'MINGSPARKCALLER'",
            "MINGSPARKCALLER",
        )
        self.assertIn("list|search|show|start|launch", caller)
        self.assertIn('exec /usr/bin/apm "$subaction" "$@"', caller)

    def test_spark_pass_auth_never_executes_an_arbitrary_vendor_command(self):
        pass_auth = heredoc(
            APPS,
            "cat > /opt/durapps/spark-store/bin/store-helper/pass-auth.sh << 'MINGSPARKPASSAUTH'",
            "MINGSPARKPASSAUTH",
        )
        self.assertIn("/opt/spark-store/bin/extras/shell-caller.sh", pass_auth)
        self.assertIn("/opt/spark-store/extras/shell-caller.sh", pass_auth)
        self.assertNotIn('exec "$@"', pass_auth)
        self.assertNotIn('exec pkexec "$@"', pass_auth)

    def test_official_wechat_download_uses_the_same_verified_local_deb_path(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'",
            "WECHATINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("Administrator privileges are required", installer)
        self.assertNotIn("sudo apt install", installer)

    def test_wechat_on_demand_install_uses_ming_gui_without_sudo_fallback(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'",
            "WECHATINSTALL",
        )
        wrapper = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        desktop = heredoc(
            APPS,
            "cat > /usr/share/applications/ming-install-wechat.desktop << WECHATDESKTOPSYS",
            "WECHATDESKTOPSYS",
        )
        self.assertIn("ming-package-install-gui", installer)
        self.assertIn("ming-install-wechat", wrapper)
        self.assertIn("/usr/local/bin/ming-install-wechat", desktop)
        self.assertNotIn("sudo /usr/local/bin/ming-install-wechat", wrapper)
        self.assertNotIn("Exec=pkexec /usr/local/bin/ming-install-wechat", desktop)

    def test_wechat_install_script_has_no_runtime_apt_get_fallback(self):
        installer = heredoc(
            APPS,
            "cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'",
            "WECHATINSTALL",
        )
        self.assertIn("ming-package-installer install", installer)
        self.assertIn("ming-package-install-gui", installer)
        self.assertNotIn("apt-get -y", installer)

    def test_ming_helper_install_actions_do_not_use_sudo_fallbacks(self):
        helper = heredoc(DESKTOP, "cat > /usr/local/bin/ming-helper << 'MINGHELPER'", "MINGHELPER")
        self.assertIn("/usr/local/bin/ming-install-wechat", helper)
        self.assertIn("/usr/local/bin/ming-package-install-gui", helper)
        self.assertIn("spark-store_5.2.1.0_amd64.deb", helper)
        self.assertNotIn("sudo /usr/local/bin/ming-install-wechat", helper)
        self.assertNotIn("sudo /usr/local/bin/ming-install-spark-store", helper)

    def test_wechat_launcher_uses_only_dpkg_owned_strict_desktop_entries(self):
        wrapper = heredoc(APPS, "cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'", "WECHATWRAP")
        self.assertIn("find_wechat_argv", wrapper)
        self.assertIn("dpkg-query -S --", wrapper)
        self.assertIn('dpkg-query -L "${package}"', wrapper)
        self.assertIn("parse_desktop_file", wrapper)
        self.assertIn("desktop_launch_diagnostic", wrapper)
        self.assertIn("mapfile -d '' -t wechat_argv", wrapper)
        self.assertIn('exec "${wechat_argv[@]}" "$@"', wrapper)
        self.assertNotIn("eval ", wrapper)
        self.assertNotIn("/opt/wechat/wechat", wrapper)
        self.assertNotIn("/opt/weixin/weixin", wrapper)
        self.assertNotIn("command -v wechat", wrapper)
        self.assertNotIn("command -v weixin", wrapper)

    def test_build_gate_requires_audio_and_local_package_helpers(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("usr/local/bin/ming-audio-session", validator)
        self.assertIn("usr/local/sbin/ming-package-installer", validator)
        self.assertIn("usr/share/ming-os/vendor/spark-store/spark-store_5.2.1.0_amd64.deb", validator)
        self.assertIn("88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55", validator)
        self.assertNotIn("spark_asset.read_bytes()", validator)
        self.assertIn("usr/local/sbin/ming-spark-package-control", validator)
        self.assertIn("usr/local/bin/ming-spark-backend-status", validator)
        self.assertIn("org.ming.spark.package-control.policy", validator)


if __name__ == "__main__":
    unittest.main()
