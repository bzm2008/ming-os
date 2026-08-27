import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
STORE = (ROOT / "assets" / "ming-store.py").read_text(encoding="utf-8")
STORE_CORE = (ROOT / "assets" / "ming-store-core.py").read_text(encoding="utf-8")
STORE_CONTROL = (ROOT / "assets" / "ming-store-control.py").read_text(encoding="utf-8")


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

    def test_ming_store_assets_are_deployed_and_catalogs_are_machine_readable(self):
        for asset in ("ming-store.py", "ming-store-core.py", "ming-store-control.py"):
            self.assertIn(asset, DESKTOP)
        for source_id in ("ming-official", "debian-apt", "vendor-official"):
            catalog = json.loads((
                ROOT / "assets" / "ming-store-catalog" / (source_id + ".json")
            ).read_text(encoding="utf-8"))
            self.assertEqual("ming.store.catalog.v1", catalog["schema"])
            self.assertEqual(source_id, catalog["source"]["id"])

    def test_ming_store_system_launcher_is_trusted_for_the_launch_broker(self):
        receipt = FINALIZE[FINALIZE.index("seed_trusted_desktop_receipts"):
                           FINALIZE.index("# Keep the shipped desktop intentional")]
        self.assertIn('"ming-store.desktop"', receipt)

    def test_ming_store_desktop_and_dock_use_the_same_verified_launch_path(self):
        self.assertIn("Exec=/usr/local/bin/ming-store", DESKTOP)
        self.assertIn('"ming-store:ming-store.desktop"', DESKTOP)
        self.assertIn("exec_line=\"/usr/local/bin/ming-launch --desktop-file", DESKTOP)

    def test_store_transaction_requires_install_readback_before_success(self):
        execute = STORE_CONTROL.split("    def _execute_request", 1)[1].split(
            "    def execute", 1
        )[0]
        self.assertIn('self._journal(request, "readback")', execute)
        self.assertIn("state = self._installed(package)", execute)
        self.assertIn('state["version"] != version', execute)
        self.assertIn('self._journal(request, "succeeded")', execute)
        self.assertLess(execute.index('self._journal(request, "readback")'),
                        execute.rindex('self._journal(request, "succeeded")'))

    def test_vendor_catalog_stays_disabled_until_version_url_and_hash_are_pinned(self):
        catalog = json.loads((
            ROOT / "assets" / "ming-store-catalog" / "vendor-official.json"
        ).read_text(encoding="utf-8"))
        for item in catalog["applications"]:
            self.assertFalse(item["enabled"])
            self.assertEqual("identity_not_pinned", item["disabled_reason"])
        resolve = STORE_CORE.split("class VendorOfficialProvider", 1)[1].split(
            "class DebianAptProvider", 1
        )[0]
        self.assertIn('url.startswith("https://")', resolve)
        self.assertIn("SHA256.fullmatch", resolve)

    def test_store_privileged_actions_use_the_ming_allowlisted_helper(self):
        self.assertIn("org.mingos.store.manage", DESKTOP)
        self.assertIn("/usr/local/sbin/ming-store-control", DESKTOP)
        bridge = heredoc(
            DESKTOP,
            "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'",
            "MINGAUTHORIZE",
        )
        self.assertIn("store)", bridge)
        self.assertNotIn("eval ", bridge)
        self.assertNotIn("sh -c", bridge)

    def test_store_polkit_policy_disables_any_and_inactive_callers(self):
        policy = heredoc(
            DESKTOP,
            "cat > /usr/share/polkit-1/actions/org.mingos.store.manage.policy << 'MINGSTOREPOLICY'",
            "MINGSTOREPOLICY",
        )
        self.assertIn("<allow_any>no</allow_any>", policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", policy)
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", policy)

    def test_store_control_rejects_unknown_actions_and_arbitrary_arguments(self):
        self.assertIn('ALLOWED_ACTIONS = ("install", "update", "remove", "refresh")', STORE_CONTROL)
        self.assertIn("REQUEST_ID.fullmatch(request_id)", STORE_CONTROL)
        self.assertIn("parser.add_argument(\"request_id\")", STORE_CONTROL)
        for token in ("eval ", "shell=True", "bash -c", "sh -c"):
            self.assertNotIn(token, STORE_CONTROL)

    def test_retired_spark_apm_and_ace_are_not_deployed(self):
        for marker in (
            "ming-spark-package-control", "ming-spark-backend-status",
            "ming-spark-aria2c", "MINGSPARKPASSAUTH",
        ):
            self.assertNotIn(marker, APPS)
        self.assertIn('require_absent(residue, "Spark/APM residue")', BUILD)

    def test_spark_upgrade_cleanup_does_not_remove_historical_apps_or_data(self):
        cleanup = FINALIZE.split("retire_legacy_store_runtime() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("apt-get remove --no-auto-remove", cleanup)
        self.assertNotRegex(cleanup, r"(?m)^[ \t]*(?:rm|find)\b[^\n]*/opt/apps")
        self.assertIn("do not touch /opt/apps", cleanup.lower())
        self.assertNotRegex(cleanup, r"apt-get\s+(?:-\S+\s+)*autoremove")

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
        self.assertIn("/usr/local/bin/ming-store --status", helper)
        self.assertIn("repair-store", helper)
        self.assertNotIn("sudo /usr/local/bin/ming-install-wechat", helper)
        self.assertNotIn("ming-install-spark-store", helper)

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

    def test_build_gate_requires_audio_local_package_and_store_helpers(self):
        validator = BUILD.split("validate_r4_compatibility() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("usr/local/bin/ming-audio-session", validator)
        self.assertIn("usr/local/sbin/ming-package-installer", validator)
        self.assertIn("usr/local/lib/ming-os/ming-store-core.py", validator)
        self.assertIn("usr/local/sbin/ming-store-control", validator)
        self.assertIn("org.mingos.store.manage.policy", validator)
        self.assertIn('require_absent(residue, "Spark/APM residue")', validator)


if __name__ == "__main__":
    unittest.main()
