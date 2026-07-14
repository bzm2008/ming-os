"""Contracts for the Dock's safe running-window fallback entry."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"


def generated_script(source, start_marker, end_marker):
    parts = source.split(start_marker, 1)
    if len(parts) != 2:
        return ""
    return parts[1].split(end_marker, 1)[0]


def generated_function(source, name, next_name):
    start_marker = "%s() {" % name
    start = source.index(start_marker)
    end = source.index("\n%s()" % next_name, start)
    return source[start:end]


class RunningAppsContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESKTOP.read_text(encoding="utf-8")
        cls.running_apps = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-running-apps << 'MINGRUNNINGAPPS'",
            "MINGRUNNINGAPPS",
        )
        cls.window_control = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'",
            "MINGWINDOWCONTROL",
        )
        cls.healthcheck = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'",
            "MINGDESKHEALTH",
        )
        cls.watchdog = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'",
            "PLANKWATCH",
        )
        cls.session_healthcheck = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'",
            "MINGSESSIONHEALTH",
        )

    @staticmethod
    def write_executable(path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def test_running_apps_entry_validates_ids_and_delegates_actions(self):
        self.assertTrue(self.running_apps, "ming-running-apps entry is missing")
        for marker in (
            "x11_id_is_valid()",
            "require_manageable_window",
            "ming-window-control",
            "restore|focus|close",
            "--window-id",
            "window ID",
        ):
            self.assertIn(marker, self.running_apps)
        self.assertNotIn("pkill", self.running_apps)

    def test_running_apps_covers_known_and_unknown_window_classes(self):
        self.assertTrue(self.running_apps, "ming-running-apps entry is missing")
        for marker in (
            "StartupWMClass",
            "known_launcher_for_class",
            "wps",
            "wechat",
            "wine",
            "electron",
            "SKIP_TASKBAR",
            "_NET_WM_STATE_HIDDEN",
            "unmapped",
            "seen_window_ids",
        ):
            self.assertIn(marker, self.running_apps)

    def test_known_class_fallback_is_not_reported_as_a_confirmed_plank_mapping(self):
        """A filename guess is usable, but must not hide Dock degradation."""
        self.assertIn('mapping_kind="candidate"', self.running_apps)
        self.assertIn('mapping_source="known-class"', self.running_apps)
        self.assertIn('window["mapping"]["kind"] != "launcher"', self.running_apps)

    def test_final_window_control_revalidates_a_manageable_window_before_action(self):
        """The downstream helper must reject Dock/notification IDs too."""
        self.assertIn("window_is_manageable()", self.window_control)
        require_window = self.window_control.split("require_window() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn('window_is_manageable "${properties}"', require_window)
        self.assertIn("可管理的应用窗口", require_window)

    def test_dock_has_a_running_apps_item_with_non_pin_only_runtime_settings(self):
        self.assertIn("ming-running-apps.dockitem", self.source)
        self.assertIn("ming-running-apps.desktop", self.source)
        self.assertIn("Exec=/usr/local/bin/ming-running-apps menu", self.source)
        self.assertIn("PinOnly=false", self.watchdog)
        self.assertIn("ensure_bamfdaemon", self.watchdog)

    def test_hidden_running_apps_entry_bypasses_the_desktop_launch_broker(self):
        """A Dock-only hidden entry must not be rejected by ming-launch."""
        refresh = generated_script(
            self.source,
            "cat > /usr/local/sbin/ming-refresh-dock-launchers << 'MINGREFRESHDOCK'",
            "MINGREFRESHDOCK",
        )
        self.assertIn("NoDisplay=true", self.source)
        self.assertRegex(
            refresh,
            r'ming-running-apps\)\s+exec_line="/usr/local/bin/ming-running-apps menu"',
        )

    def test_running_apps_dock_health_requires_the_managed_proxy_chain(self):
        """A stray dockitem file must not make the recovery entry look usable."""
        for marker in (
            "DockItems=",
            "ming-running-apps.dockitem",
            "ming-dock-ming-running-apps.desktop",
            "Launcher=file://",
            "Exec=/usr/local/bin/ming-running-apps menu",
        ):
            self.assertIn(marker, self.running_apps)

    def test_watchdog_repairs_a_missing_or_corrupt_running_apps_entry(self):
        """Existing settings must be repaired even when their list already names the item."""
        settings = self.watchdog.split("ensure_plank_settings() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("running_apps_dock_item_healthy", self.watchdog)
        self.assertIn("running_item_repaired", settings)
        self.assertIn("ming-refresh-dock-launchers", settings)

    def test_session_coordinator_checks_real_dock_health_with_a_bounded_probe(self):
        """A Plank WM_CLASS alone must not suppress recovery of a hidden Dock."""
        visible = self.session_healthcheck.split("plank_window_visible() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("ming-plank-watchdog --check", visible)
        self.assertIn("run_bounded", visible)
        self.assertNotIn("wmctrl -lx", visible)

    def test_plank_watchdog_bounds_every_one_shot_recovery(self):
        """The declared eight-second startup budget must wrap the actual recovery."""
        self.assertIn("PLANK_STARTUP_TIMEOUT=8", self.watchdog)
        self.assertIn("start_plank_bounded()", self.watchdog)
        self.assertIn('"$0" --start-internal', self.watchdog)

    def test_health_json_reports_window_mapping_and_degradation_without_dock_restart(self):
        for marker in (
            '"windows"',
            '"unmapped_minimized"',
            '"running_apps"',
            '"bamfdaemon"',
            '"pin_only"',
            '"degraded"',
            "ming-running-apps",
            "unmapped-minimized",
            "running-apps-dock-item-unavailable",
        ):
            self.assertIn(marker, self.healthcheck)
        self.assertIn("running_apps_entry_available()", self.running_apps)

        unmapped_block = self.healthcheck.split("unmapped_minimized", 1)[-1]
        self.assertNotIn("stop_plank", unmapped_block)
        self.assertNotIn("start_plank", unmapped_block)

    def test_generated_running_window_scripts_are_valid_bash(self):
        self.assertTrue(self.running_apps, "ming-running-apps entry is missing")
        for script in (self.running_apps, self.healthcheck, self.watchdog):
            result = subprocess.run(
                ["bash", "-n"], input=script.replace("\r", "").encode("utf-8")
            )
            self.assertEqual(0, result.returncode)

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_lists_normal_wine_electron_unknown_minimized_and_skip_taskbar_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script, environment = self.runtime_fixture(root)
            result = subprocess.run(
                ["bash", str(script), "list", "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            windows = {window["id"]: window for window in payload["windows"]}

            self.assertEqual(
                {
                    "0x01000001",
                    "0x01000002",
                    "0x01000003",
                    "0x01000004",
                    "0x01000005",
                    "0x01000006",
                },
                set(windows),
            )
            self.assertFalse(windows["0x01000001"]["minimized"])
            self.assertEqual("candidate", windows["0x01000002"]["mapping"]["kind"])
            self.assertEqual("candidate", windows["0x01000003"]["mapping"]["kind"])
            self.assertEqual("candidate", windows["0x01000004"]["mapping"]["kind"])
            self.assertEqual("known-class", windows["0x01000002"]["mapping"]["source"])
            self.assertEqual("known-class", windows["0x01000003"]["mapping"]["source"])
            self.assertEqual("known-class", windows["0x01000004"]["mapping"]["source"])
            self.assertEqual("unmapped", windows["0x01000005"]["mapping"]["kind"])
            self.assertTrue(windows["0x01000002"]["minimized"])
            self.assertTrue(windows["0x01000005"]["minimized"])
            self.assertTrue(windows["0x01000006"]["skip_taskbar"])
            self.assertTrue(all(window["actionable"] for window in windows.values()))

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_does_not_duplicate_a_window_when_multiple_launchers_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script, environment = self.runtime_fixture(root)
            duplicate = root / "applications" / "duplicate-electron.desktop"
            duplicate.write_text(
                "[Desktop Entry]\nName=Duplicate Electron\nStartupWMClass=Electron\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(script), "list", "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            ids = [window["id"] for window in json.loads(result.stdout)["windows"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(1, ids.count("0x01000004"))

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_restore_focus_and_close_delegate_only_after_id_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script, environment = self.runtime_fixture(root)
            action_log = pathlib.Path(environment["MING_ACTION_LOG"])

            invalid = subprocess.run(
                ["bash", str(script), "focus", "--window-id", "not-an-x11-id"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertNotEqual(0, invalid.returncode)
            self.assertFalse(action_log.exists())

            for action in ("restore", "focus", "close"):
                result = subprocess.run(
                    ["bash", str(script), action, "--window-id", "0x01000005"],
                    capture_output=True,
                    text=True,
                    env=environment,
                    timeout=10,
                )
                self.assertEqual(0, result.returncode, result.stderr)

            self.assertEqual(
                [
                    "restore --window-id 0x01000005",
                    "focus --window-id 0x01000005",
                    "close --window-id 0x01000005",
                ],
                action_log.read_text(encoding="utf-8").splitlines(),
            )

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_dock_item_health_rejects_unlisted_and_corrupt_proxy_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script, environment = self.runtime_fixture(root)
            settings = root / "home" / ".config" / "plank" / "dock1" / "settings"
            proxy = root / "ming-dock-ming-running-apps.desktop"

            settings.write_text(
                "[PlankDockPreferences]\nDockItems=ming-settings.dockitem\nPinOnly=false\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(script), "list", "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(json.loads(result.stdout)["entry"]["dock_item"])

            settings.write_text(
                "[PlankDockPreferences]\nDockItems=ming-running-apps.dockitem\nPinOnly=false\n",
                encoding="utf-8",
            )
            proxy.write_text("[Desktop Entry]\nExec=/usr/bin/false\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(script), "list", "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(json.loads(result.stdout)["entry"]["dock_item"])

            proxy.write_text(
                "[Desktop Entry]\nExec=/usr/local/bin/ming-running-apps menu\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(script), "list", "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["entry"]["dock_item"])

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_watchdog_refreshes_a_missing_managed_running_apps_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            home = root / "home"
            dock_dir = home / ".config" / "plank" / "dock1"
            launchers = dock_dir / "launchers"
            launchers.mkdir(parents=True)
            settings = dock_dir / "settings"
            settings.write_text(
                "[PlankDockPreferences]\nDockItems=ming-running-apps.dockitem\nPinOnly=false\n",
                encoding="utf-8",
            )
            personal = launchers / "personal.dockitem"
            personal.write_text("personal launcher\n", encoding="utf-8")
            proxy = root / "ming-dock-ming-running-apps.desktop"
            refresher = root / "ming-refresh-dock-launchers"
            self.write_executable(
                refresher,
                """#!/usr/bin/env bash
mkdir -p "${MING_PLANK_LAUNCHERS_DIR}"
printf '[PlankDockItemPreferences]\\nLauncher=file://%s\\n' "${MING_RUNNING_APPS_DOCK_PROXY_FILE}" > "${MING_PLANK_LAUNCHERS_DIR}/ming-running-apps.dockitem"
printf '[Desktop Entry]\\nExec=/usr/local/bin/ming-running-apps menu\\n' > "${MING_RUNNING_APPS_DOCK_PROXY_FILE}"
""",
            )
            settings_helper = generated_function(
                self.watchdog,
                "plank_settings_include_running_apps_item",
                "running_apps_dock_item_healthy",
            )
            item_helper = generated_function(
                self.watchdog,
                "running_apps_dock_item_healthy",
                "ensure_plank_settings",
            )
            ensure_settings = generated_function(
                self.watchdog,
                "ensure_plank_settings",
                "ensure_bamfdaemon",
            )
            harness = "set -u\nlog() { :; }\nlog_file=/dev/null\n%s\n%s\n%s\nensure_plank_settings\n" % (
                settings_helper,
                item_helper,
                ensure_settings,
            )
            harness = harness.replace(
                "/usr/local/sbin/ming-refresh-dock-launchers", str(refresher)
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(home),
                    "MING_PLANK_SETTINGS_FILE": str(settings),
                    "MING_PLANK_LAUNCHERS_DIR": str(launchers),
                    "MING_RUNNING_APPS_DOCK_PROXY_FILE": str(proxy),
                }
            )
            result = subprocess.run(
                ["bash"],
                input=harness,
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(
                "Launcher=file://%s" % proxy,
                (launchers / "ming-running-apps.dockitem").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Exec=/usr/local/bin/ming-running-apps menu",
                proxy.read_text(encoding="utf-8"),
            )
            self.assertEqual("personal launcher\n", personal.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_final_window_control_refuses_dock_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            control, environment = self.window_control_fixture(root)
            action_log = pathlib.Path(environment["MING_CONTROL_ACTION_LOG"])

            rejected = subprocess.run(
                ["bash", str(control), "close", "--window-id", "0x01000007"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertFalse(action_log.exists())

            accepted = subprocess.run(
                ["bash", str(control), "close", "--window-id", "0x01000001"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            self.assertEqual("-i -c 0x01000001\n", action_log.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "runtime X11 helper execution is validated by the Linux rootfs gate")
    def test_runtime_health_json_keeps_unmapped_minimized_windows_as_a_degradation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script, environment = self.runtime_fixture(root)
            health_path = root / "ming-desktop-healthcheck"
            self.write_executable(health_path, self.healthcheck)
            environment["MING_RUNNING_APPS_BIN"] = str(script)
            environment["MING_WINDOW_CONTROL_BIN"] = str(root / "bin" / "ming-window-control")

            result = subprocess.run(
                ["bash", str(health_path), "--json"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )
            payload = json.loads(result.stdout)
            self.assertIn("windows", payload)
            self.assertEqual(
                ["0x01000002", "0x01000005"],
                [window["id"] for window in payload["unmapped_minimized"]],
            )
            self.assertTrue(payload["running_apps"]["available"])
            self.assertTrue(payload["dock"]["degraded"])
            self.assertEqual("unmapped-minimized", payload["dock"]["degradation"])

    def runtime_fixture(self, root):
        fake_bin = root / "bin"
        fake_bin.mkdir()
        applications = root / "applications"
        applications.mkdir()
        home = root / "home"
        dock_dir = home / ".config" / "plank" / "dock1" / "launchers"
        dock_dir.mkdir(parents=True)
        (dock_dir.parent / "settings").write_text(
            "[PlankDockPreferences]\nDockItems=ming-running-apps.dockitem\nPinOnly=false\n",
            encoding="utf-8",
        )
        (dock_dir / "ming-running-apps.dockitem").write_text(
            "[PlankDockItemPreferences]\nLauncher=file://%s\n" % (root / "ming-dock-ming-running-apps.desktop"),
            encoding="utf-8",
        )

        desktop_entries = {
            "normal.desktop": ("Normal", "Normal"),
            "wps-office.desktop": (None, "WPS Office"),
            "wine.desktop": (None, "Wine"),
            "electron.desktop": (None, "Electron"),
            "wechat.desktop": (None, "WeChat"),
        }
        for filename, (wm_class, name) in desktop_entries.items():
            startup = "StartupWMClass=%s\n" % wm_class if wm_class else ""
            (applications / filename).write_text(
                "[Desktop Entry]\nName=%s\n%s" % (name, startup),
                encoding="utf-8",
            )
        mapping_file = root / "known-launchers.conf"
        mapping_file.write_text(
            "wps|wps-office.desktop\nwechat|wechat.desktop\nwine|wine.desktop\nelectron|electron.desktop\n",
            encoding="utf-8",
        )
        desktop_entry = root / "ming-running-apps.desktop"
        desktop_entry.write_text(
            "[Desktop Entry]\nExec=/usr/local/bin/ming-running-apps menu\nNoDisplay=true\n",
            encoding="utf-8",
        )
        (root / "ming-dock-ming-running-apps.desktop").write_text(
            "[Desktop Entry]\nExec=/usr/local/bin/ming-running-apps menu\nNoDisplay=true\n",
            encoding="utf-8",
        )

        self.write_executable(
            fake_bin / "timeout",
            "#!/usr/bin/env bash\nshift\nshift\nexec \"$@\"\n",
        )
        self.write_executable(
            fake_bin / "wmctrl",
            """#!/usr/bin/env bash
case "${1:-}" in
  -lx)
    cat <<'WINDOWS'
0x01000001 0 host normal.Normal Normal window
0x01000002 0 host wps.WPS WPS document
0x01000003 0 host wine.Wine Wine document
0x01000004 0 host electron.Electron Electron chat
0x01000005 0 host unknown.Unknown Unknown minimized window
0x01000006 0 host hidden.Hidden Skip taskbar window
0x01000007 0 host plank.Plank Plank
WINDOWS
    ;;
esac
""",
        )
        self.write_executable(
            fake_bin / "xprop",
            """#!/usr/bin/env bash
id="${2:-}"
case "${id}" in
  0x01000001) class='"normal", "Normal"'; state='' ;;
  0x01000002) class='"wps", "WPS"'; state='_NET_WM_STATE_HIDDEN' ;;
  0x01000003) class='"wine", "Wine"'; state='' ;;
  0x01000004) class='"electron", "Electron"'; state='' ;;
  0x01000005) class='"unknown", "Unknown"'; state='_NET_WM_STATE_HIDDEN' ;;
  0x01000006) class='"hidden", "Hidden"'; state='_NET_WM_STATE_SKIP_TASKBAR' ;;
  0x01000007) class='"plank", "Plank"'; state='_NET_WM_WINDOW_TYPE_DOCK' ;;
  *) exit 1 ;;
esac
printf 'WM_CLASS(STRING) = %s\\n_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_NORMAL\\n%s\\n' "${class}" "${state}"
""",
        )
        self.write_executable(
            fake_bin / "ming-window-control",
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"${MING_ACTION_LOG}\"\n",
        )
        self.write_executable(fake_bin / "bamfdaemon", "#!/usr/bin/env bash\nexit 0\n")
        self.write_executable(
            fake_bin / "pgrep",
            "#!/usr/bin/env bash\n[[ \"$*\" == *bamfdaemon* ]] && exit 0\nexit 1\n",
        )
        self.write_executable(fake_bin / "zenity", "#!/usr/bin/env bash\nexit 0\n")

        script = root / "ming-running-apps"
        self.write_executable(script, self.running_apps)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(home),
                "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
                "MING_RUNNING_APPS_DESKTOP_DIRS": str(applications),
                "MING_RUNNING_APPS_MAPPING_FILE": str(mapping_file),
                "MING_RUNNING_APPS_DESKTOP_FILE": str(desktop_entry),
                "MING_RUNNING_APPS_DOCK_PROXY_FILE": str(root / "ming-dock-ming-running-apps.desktop"),
                "MING_WINDOW_CONTROL_BIN": str(fake_bin / "ming-window-control"),
                "MING_ACTION_LOG": str(root / "actions.log"),
            }
        )
        return script, environment

    def window_control_fixture(self, root):
        fake_bin = root / "bin"
        fake_bin.mkdir()
        action_log = root / "window-actions.log"
        self.write_executable(
            fake_bin / "timeout",
            "#!/usr/bin/env bash\nshift\nshift\nexec \"$@\"\n",
        )
        self.write_executable(
            fake_bin / "xprop",
            """#!/usr/bin/env bash
id="${2:-}"
case "${id}" in
  0x01000001) type='_NET_WM_WINDOW_TYPE_NORMAL' ;;
  0x01000007) type='_NET_WM_WINDOW_TYPE_DOCK' ;;
  *) exit 1 ;;
esac
printf '_NET_WM_WINDOW_TYPE(ATOM) = %s\\n' "${type}"
""",
        )
        self.write_executable(
            fake_bin / "wmctrl",
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"${MING_CONTROL_ACTION_LOG}\"\n",
        )
        control = root / "ming-window-control"
        self.write_executable(control, self.window_control)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(root / "home"),
                "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
                "MING_CONTROL_ACTION_LOG": str(action_log),
            }
        )
        return control, environment


if __name__ == "__main__":
    unittest.main()
