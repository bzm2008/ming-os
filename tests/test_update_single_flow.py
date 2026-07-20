import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "assets" / "ming-settings.py"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"
OTA = ROOT / "modules" / "06_ota_update.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
PAPYRUS = ROOT / "modules" / "04_papyrus.sh"
FINALIZER = ROOT / "modules" / "07_finalize.sh"


def method_block(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def standalone_function(source, name, namespace=None):
    namespace = {} if namespace is None else namespace
    start = source.index("def %s(" % name)
    next_function = source.find("\ndef ", start + 1)
    if next_function < 0:
        next_function = len(source)
    exec(source[start:next_function], namespace)
    return namespace[name]


def ota_presenter(source):
    start = source.index("OTA_STATE_MESSAGES = {")
    end = source.index("\ndef pci_driver_summary", start)
    namespace = {"re": re}
    exec(source[start:end], namespace)
    return namespace["ota_status_presentation"]


class UpdateSingleFlowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.phone = PHONE.read_text(encoding="utf-8")
        cls.ota = OTA.read_text(encoding="utf-8")
        cls.desktop = DESKTOP.read_text(encoding="utf-8")
        cls.papyrus = PAPYRUS.read_text(encoding="utf-8")
        cls.finalizer = FINALIZER.read_text(encoding="utf-8")

    def test_settings_starts_with_one_check_action_and_promotes_it_after_detection(self):
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")

        self.assertIn('Gtk.Button(label="检查更新")', update)
        self.assertIn("self.update_action_button", update)
        self.assertIn('"立即更新"', self.settings)
        self.assertIn('"已下载并完成校验，等待重启确认"', self.settings)
        self.assertIn('["ming-update", "status", "--json"]', update)
        self.assertIn('"pkexec", "ming-update", "apply"', update)
        for retired_label in ("应用小修复", "大版本升级", "更新并关机"):
            self.assertNotIn(retired_label, update)

    def test_frozen_transaction_states_have_truthful_chinese_presentation(self):
        presenter = standalone_function(
            self.settings,
            "ota_status_presentation",
            {
                "OTA_STATE_MESSAGES": {
                    "new": ("准备更新", "check"),
                    "verified": ("已完成签名校验，准备暂存", "wait"),
                    "staging": ("正在暂存更新", "wait"),
                    "staged": ("已下载并完成校验，等待重启确认", "wait"),
                    "armed": ("已安排下一次启动应用更新", "wait"),
                    "booting": ("正在启动候选系统，等待健康检查", "wait"),
                    "pending_health": ("正在启动候选系统，等待健康检查", "wait"),
                    "committing": ("正在确认更新结果", "wait"),
                    "committed": ("更新已完成", "check"),
                    "aborting": ("正在取消更新", "wait"),
                    "aborted": ("更新已取消", "check"),
                    "rollback_armed": ("健康检查未通过，已安排自动回滚", "wait"),
                    "rolling_back": ("正在自动回滚到上一版本", "wait"),
                    "rolled_back": ("更新未通过健康检查，已自动回滚", "check"),
                },
                "OTA_ERROR_MESSAGES": {},
                "OTA_MESSAGE_KEYS": {
                    "update.status.committed": "committed",
                },
            },
        )
        for state, expected in {
            "new": "准备更新",
            "verified": "已完成签名校验，准备暂存",
            "staging": "正在暂存更新",
            "staged": "已下载并完成校验，等待重启确认",
            "armed": "已安排下一次启动应用更新",
            "booting": "正在启动候选系统，等待健康检查",
            "pending_health": "正在启动候选系统，等待健康检查",
            "committing": "正在确认更新结果",
            "committed": "更新已完成",
            "aborting": "正在取消更新",
            "aborted": "更新已取消",
            "rollback_armed": "健康检查未通过，已安排自动回滚",
            "rolling_back": "正在自动回滚到上一版本",
            "rolled_back": "更新未通过健康检查，已自动回滚",
        }.items():
            result = presenter({"schema": "ming.update.cli.v1", "state": state})
            self.assertEqual(expected, result["title"])

    def test_formal_build_revision_is_not_exposed_as_a_product_version(self):
        presenter = ota_presenter(self.settings)
        result = presenter({
            "schema": "ming.update.cli.v1",
            "ok": True,
            "state": "new",
            "current_version": "26.4.0.1",
            "available_version": "26.4.0.2",
            "available": True,
            "ready": False,
        })

        self.assertIn("当前版本：Ming OS 26.4.0", result["detail"])
        self.assertIn("目标版本：Ming OS 26.4.0", result["detail"])
        self.assertNotIn("26.4.0.1", result["detail"])
        self.assertNotIn("26.4.0.2", result["detail"])
        self.assertNotEqual(
            "更新已完成",
            presenter({"schema": "ming.update.cli.v1", "state": "staged"})["title"],
        )
        self.assertNotEqual(
            "更新已完成",
            presenter({"schema": "ming.update.cli.v1", "state": "pending_health"})["title"],
        )
        self.assertEqual(
            "更新已完成",
            presenter({
                "schema": "ming.update.cli.v1",
                "message_key": "update.status.committed",
            })["title"],
        )

    def test_settings_uses_frozen_actions_and_message_keys_without_overclaiming(self):
        presenter = ota_presenter(self.settings)
        fixture_root = ROOT / "contracts" / "ota" / "fixtures"
        available = json.loads((fixture_root / "transactional-available.cli.json").read_text(encoding="utf-8"))
        no_update = json.loads((fixture_root / "no-update.cli.json").read_text(encoding="utf-8"))
        rollback = json.loads((fixture_root / "rollback.cli.json").read_text(encoding="utf-8"))

        actionable = presenter(available)
        self.assertEqual("apply", actionable["button_state"])
        self.assertEqual("立即更新", actionable["button_label"])

        latest = presenter(no_update)
        self.assertIn("最新", latest["title"])
        self.assertNotEqual("更新已完成", latest["title"])

        rollback_state = presenter(rollback)
        self.assertIn("回滚", rollback_state["title"])
        self.assertNotIn("健康检查未通过", rollback_state["title"])

        staged = presenter({
            "schema": "ming.update.cli.v1",
            "state": "staged",
            "action": "cancel",
            "transaction": {"id": "tx-001"},
        })
        armed = presenter({
            "schema": "ming.update.cli.v1",
            "state": "armed",
            "action": "reboot",
            "transaction": {"id": "tx-001"},
        })
        self.assertEqual("cancel", staged["button_state"])
        self.assertEqual("reboot", armed["button_state"])
        self.assertIn("on_update_cancel", self.settings)
        self.assertIn("on_update_reboot", self.settings)

    def test_settings_maps_frozen_error_codes_without_terminal_text(self):
        presenter = standalone_function(
            self.settings,
            "ota_status_presentation",
            {
                "OTA_STATE_MESSAGES": {},
                "OTA_ERROR_MESSAGES": {
                    "E_BOOTSTRAP_REQUIRED": "此系统需要先安装官方 OTA 更新组件。",
                    "E_SPACE": "可用空间不足，更新已安全拒绝。",
                    "E_MANIFEST_SIGNATURE": "更新清单签名校验失败，更新已安全拒绝。",
                    "E_ROLLBACK_STATE": "更新失败，系统已回滚到上一版本。",
                    "E_HEALTH_ROOT": "新系统健康检查失败，系统正在回滚。",
                },
                "OTA_MESSAGE_KEYS": {},
            },
        )
        for code, expected in (
            ("E_BOOTSTRAP_REQUIRED", "此系统需要先安装官方 OTA 更新组件。"),
            ("E_SPACE", "可用空间不足，更新已安全拒绝。"),
            ("E_MANIFEST_SIGNATURE", "更新清单签名校验失败，更新已安全拒绝。"),
            ("E_ROLLBACK_STATE", "更新失败，系统已回滚到上一版本。"),
        ):
            self.assertIn(expected, presenter({
                "schema": "ming.update.cli.v1", "ok": False, "error_code": code,
            })["detail"])
        rollback = presenter({
            "schema": "ming.update.cli.v1",
            "state": "rolled_back",
            "error_code": "E_HEALTH_ROOT",
        })["detail"]
        self.assertIn("已自动回滚", rollback)
        self.assertNotIn("正在回滚", rollback)
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")
        self.assertNotIn('run_async(["ming-update", "check"]', update)
        self.assertIn('["ming-update", "check", "--json"]', update)
        self.assertNotIn('"ming-transaction-diagnostics"', update)

    def test_2640_embedded_runtime_failure_never_looks_like_a_bootstrap_download(self):
        presenter = ota_presenter(self.settings)
        result = presenter({
            "schema": "ming.update.cli.v1",
            "ok": False,
            "state": "failed",
            "action": "none",
            "error_code": "E_BOOTSTRAP_VERSION",
            "update": {"current_version": "26.4.0", "delivery": "transactional-slot-v1"},
        })

        self.assertEqual("内置 OTA 更新组件异常", result["title"])
        self.assertIn("不会下载额外组件", result["detail"])
        self.assertEqual("check", result["button_state"])

    def test_settings_refuses_missing_or_unknown_cli_schema(self):
        presenter = ota_presenter(self.settings)
        actionable = {
            "ok": True,
            "state": "new",
            "action": "apply",
            "release_id": "ming-os-26.3.3-amd64-1",
            "manifest_sha256": "a" * 64,
        }
        for schema in (None, "ming.update.cli.v2", "legacy.update.v1"):
            payload = dict(actionable)
            if schema is not None:
                payload["schema"] = schema
            result = presenter(payload)
            self.assertEqual("check", result["button_state"])
            self.assertEqual("error", result["severity"])
            self.assertIn("更新协议不受支持", result["title"])
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")
        self.assertNotIn('"error_code": "E_COMMAND"', update)
        self.assertIn('"error_code": "E_PROTOCOL_UNSUPPORTED"', update)

    def test_settings_maps_every_frozen_stable_error_code(self):
        for code in (
            "E_ARGUMENT", "E_TRANSACTION_NOT_FOUND", "E_BUSY", "E_NOT_CANCELABLE",
            "E_SPACE", "E_SOURCE_UNSUPPORTED", "E_BOOTSTRAP_REQUIRED",
            "E_MANIFEST_SIGNATURE", "E_MANIFEST_SCHEMA", "E_MANIFEST_EXPIRED",
            "E_ARTIFACT_SIGNATURE", "E_ARTIFACT_HASH", "E_CONTENT_POLICY",
            "E_CLONE", "E_PACKAGE_STATE", "E_PACKAGE_APPLY",
            "E_PROTECTED_PATH_CHANGED", "E_CANDIDATE_SEAL", "E_GRUB_WRITE",
            "E_GRUB_READBACK", "E_INITRAMFS_CONTRACT", "E_SLOT_MOUNT",
            "E_SLOT_MISMATCH", "E_HEALTH_TIMEOUT", "E_HEALTH_ROOT",
            "E_HEALTH_PACKAGES", "E_HEALTH_SERVICE", "E_HEALTH_DESKTOP_PROBE",
            "E_ROLLBACK_GRUB", "E_ROLLBACK_STATE", "E_ROLLBACK_SLOT",
            "E_STATE_SCHEMA", "E_STATE_TRANSITION", "E_STATE_DURABILITY",
            "E_STATE_RECONCILE", "E_BOOTSTRAP_SIGNATURE", "E_BOOTSTRAP_VERSION",
            "E_PROTOCOL_UNSUPPORTED", "E_KEY_POLICY",
        ):
            self.assertIn('"%s"' % code, self.settings)

    def test_settings_exposes_sanitized_transaction_diagnostics_export(self):
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")
        self.assertIn("导出更新诊断", update)
        self.assertIn("self.update_diagnostics_button", update)
        export = method_block(self.settings, "    def export_update_diagnostics", "    # ---- 5. 显示与无障碍")
        self.assertIn('"ming-update", "logs"', export)
        self.assertIn('"--transaction"', export)
        self.assertIn('"--json"', export)
        self.assertNotIn("ming-transaction-diagnostics", export)
        self.assertNotIn("--state-root", export)
        self.assertNotIn("/var/lib/ming-update", export)

    def test_settings_polls_wait_states_without_overlapping_timers(self):
        update = method_block(self.settings, "    def build_update(self):", "    def build_display(self):")
        self.assertIn("self.update_poll_source", update)
        self.assertIn("GLib.timeout_add_seconds(2", update)
        self.assertIn('timeout=10', update)
        self.assertIn('presentation["button_state"] == "wait"', update)
        self.assertIn("GLib.source_remove", update)

    def test_update_progress_and_severity_are_reset_from_each_status(self):
        presenter = ota_presenter(self.settings)
        waiting = presenter({
            "schema": "ming.update.cli.v1",
            "state": "staging",
            "progress": {"phase": "clone", "percent": 0},
        })
        complete = presenter({
            "schema": "ming.update.cli.v1",
            "state": "committed",
            "progress": {"phase": "idle", "percent": 100},
        })
        self.assertTrue(waiting["show_progress"])
        self.assertFalse(complete["show_progress"])
        status = method_block(
            self.settings, "    def apply_update_status(self, status):",
            "    def on_update_action(self, _btn):")
        self.assertIn('remove_css_class("error")', status)
        self.assertIn('remove_css_class("warning")', status)
        self.assertIn('set_visible(presentation["show_progress"])', status)

    def test_settings_binds_the_shown_update_to_the_privileged_apply_request(self):
        """A root-side cache must not silently replace the version shown in Settings."""
        status = method_block(self.settings, "    def apply_update_status(self, status):", "    def on_update_action(self, _btn):")
        apply = method_block(self.settings, "    def on_update_apply(self):", "    # ---- 5. 显示与无障碍")

        self.assertIn('self.update_release_id', status)
        self.assertIn('self.update_manifest_sha256', status)
        self.assertIn('"--release-id", self.update_release_id', apply)
        self.assertIn('"--manifest-sha256", self.update_manifest_sha256', apply)
        self.assertNotIn('"--manifest", self.update_manifest_path', apply)

    def test_settings_preserves_the_actionable_ota_failure_reason(self):
        presenter = standalone_function(
            self.settings,
            "ota_status_presentation",
            {"OTA_STATE_MESSAGES": {}, "OTA_ERROR_MESSAGES": {
                "E_SPACE": "可用空间不足，更新已安全拒绝。",
            }, "OTA_MESSAGE_KEYS": {}},
        )
        message = presenter({
            "schema": "ming.update.cli.v1", "ok": False, "error_code": "E_SPACE",
        })["detail"]
        apply = method_block(self.settings, "    def on_update_apply(self):", "    # ---- 5. 显示与无障碍")

        self.assertIn("可用空间不足", message)
        self.assertNotIn("update_output", apply)
        self.assertNotIn("on_line", apply)

    def test_deployment_uses_the_frozen_public_json_adapter(self):
        wrapper_start = self.ota.index("cat > /usr/local/bin/ming-update << 'UPDATECLI'")
        wrapper_end = self.ota.index("\nUPDATECLI\n", wrapper_start)
        wrapper = self.ota[wrapper_start:wrapper_end]
        service_start = self.ota.index("cat > /etc/systemd/system/ming-update-check.service")
        service_end = self.ota.index("\nSYSTEMDSERVICE\n", service_start)
        service = self.ota[service_start:service_end]

        self.assertIn("ming-update-cli.py", wrapper)
        self.assertNotIn("ming-recovery-update", wrapper)
        self.assertIn("ExecStart=/usr/local/bin/ming-update check --json", service)
        self.assertNotIn("MING_UPDATE_BACKGROUND_CHECK", service)

    def test_legacy_update_launcher_only_redirects_to_settings_and_is_not_deployed(self):
        start = self.ota.index("deploy_gui_tool() {")
        end = self.ota.index("return 0", start)
        deployed = self.ota[start:end]

        self.assertIn('exec /usr/local/bin/ming-control-center --page update "$@"', deployed)
        self.assertIn("rm -f -- /usr/share/applications/ming-update.desktop", deployed)
        self.assertIn('rm -f -- "/home/${MING_USER}/Desktop/ming-update.desktop"', deployed)
        self.assertNotIn("cat > /usr/share/applications/ming-update.desktop", deployed)

    def test_retired_update_paths_cannot_dispatch_an_unsupported_public_command(self):
        """Only the Settings JSON flow may expose transactional OTA actions."""
        self.assertNotIn("auto-shutdown", self.phone)
        self.assertNotIn("更新并关机", self.phone)
        for line in self.desktop.splitlines():
            if line.startswith("DockItems="):
                self.assertNotIn("ming-update.dockitem", line)
        self.assertNotIn('"ming-update:ming-update.desktop"', self.desktop)
        self.assertNotIn("('ming-update.desktop'", self.desktop)
        self.assertIn('rm -f -- "${plank_dir}/launchers/ming-update.dockitem"', self.desktop)
        self.assertNotIn("检查系统更新", self.papyrus)
        self.assertNotIn('"ming-update.desktop"', self.finalizer)


if __name__ == "__main__":
    unittest.main()
