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


class RunningAppsContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESKTOP.read_text(encoding="utf-8")
        cls.running_apps = generated_script(
            cls.source,
            "cat > /usr/local/bin/ming-running-apps << 'MINGRUNNINGAPPS'",
            "MINGRUNNINGAPPS",
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

    def test_dock_has_a_running_apps_item_with_non_pin_only_runtime_settings(self):
        self.assertIn("ming-running-apps.dockitem", self.source)
        self.assertIn("ming-running-apps.desktop", self.source)
        self.assertIn("Exec=/usr/local/bin/ming-running-apps menu", self.source)
        self.assertIn("PinOnly=false", self.watchdog)
        self.assertIn("ensure_bamfdaemon", self.watchdog)

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
        ):
            self.assertIn(marker, self.healthcheck)

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
            self.assertEqual("launcher", windows["0x01000002"]["mapping"]["kind"])
            self.assertEqual("launcher", windows["0x01000003"]["mapping"]["kind"])
            self.assertEqual("launcher", windows["0x01000004"]["mapping"]["kind"])
            self.assertEqual("known-class", windows["0x01000002"]["mapping"]["source"])
            self.assertEqual("known-class", windows["0x01000003"]["mapping"]["source"])
            self.assertEqual("known-class", windows["0x01000004"]["mapping"]["source"])
            self.assertEqual("unmapped", windows["0x01000005"]["mapping"]["kind"])
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
            self.assertEqual(["0x01000005"], [window["id"] for window in payload["unmapped_minimized"]])
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
            "[PlankDockItemPreferences]\nLauncher=file:///usr/share/applications/ming-running-apps.desktop\n",
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
  0x01000002) class='"wps", "WPS"'; state='' ;;
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

        script = root / "ming-running-apps"
        self.write_executable(script, self.running_apps)
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": str(home),
                "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
                "MING_RUNNING_APPS_DESKTOP_DIRS": str(applications),
                "MING_RUNNING_APPS_MAPPING_FILE": str(mapping_file),
                "MING_WINDOW_CONTROL_BIN": str(fake_bin / "ming-window-control"),
                "MING_ACTION_LOG": str(root / "actions.log"),
            }
        )
        return script, environment


if __name__ == "__main__":
    unittest.main()
