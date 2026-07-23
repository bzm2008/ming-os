import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"
INSTALLER_PATH = ROOT / "assets" / "ming-package-installer.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def heredoc(source, target, marker):
    start = source.index("cat > {} << '{}'".format(target, marker))
    body_start = source.index("\n", start) + 1
    body_end = source.index("\n{}".format(marker), body_start)
    return source[body_start:body_end]


class EdgeWrapperContracts(unittest.TestCase):
    def test_missing_edge_never_recurses_through_the_default_browser(self):
        wrapper = heredoc(APPS, "/usr/local/bin/ming-edge", "MINGEDGE")

        self.assertNotIn("exec xdg-open", wrapper)
        self.assertNotIn("command -v xdg-open", wrapper)
        self.assertIn("edge_diagnostic", wrapper)
        self.assertIn("E_EDGE_BINARY_MISSING", wrapper)
        self.assertIn("E_EDGE_DEPENDENCY_MISSING", wrapper)
        self.assertIn("notify-send", wrapper)


class LaunchDiagnosticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_module(LAUNCH_PATH, "ming_launch_live_reliability")

    def test_check_desktop_reports_a_structured_missing_binary_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "broken.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken\nExec=/missing/ming-test-app\n",
                encoding="utf-8",
            )

            payload = self.launch.check_desktop(desktop, allowed_dirs=(pathlib.Path(directory),))

        self.assertFalse(payload["launch_ready"])
        self.assertEqual("E_LAUNCH_BINARY_MISSING", payload["error_code"])
        self.assertIn("启动", payload["error"])

    def test_check_desktop_cli_keeps_json_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop = pathlib.Path(directory) / "broken.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken\nExec=/missing/ming-test-app\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch.object(
                    self.launch, "allowed_application_dirs", return_value=(pathlib.Path(directory),)):
                result = self.launch.main(
                    ["check-desktop", str(desktop), "--json"], stdout=output)

        self.assertEqual(1, result)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["launch_ready"])
        self.assertEqual("E_LAUNCH_BINARY_MISSING", payload["error_code"])


class LauncherReconciliationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_module(INSTALLER_PATH, "ming_package_installer_reliability")

    def test_reconcile_launchers_cli_is_a_root_only_structured_action(self):
        parsed = self.installer.build_parser().parse_args(["reconcile-launchers", "--json"])
        self.assertEqual("reconcile-launchers", parsed.action)
        self.assertTrue(parsed.json)

    def test_reconcile_keeps_only_packages_discovered_through_owned_opt_entries(self):
        service = self.installer.PackageInstaller(uid_getter=lambda: 0)
        launchers = [{
            "path": "/usr/local/share/applications/ming-opt-good.desktop",
            "name": "Good", "ok": True, "visible": True,
            "activation": "desktop_proxy", "proxy_path": "", "source_path": "",
        }]
        with mock.patch.object(
                service, "_discover_reconcile_packages", return_value=(("good-app",), ("unowned.desktop",))):
            with mock.patch.object(service, "_package_launchers", return_value=(launchers, "")) as checked:
                with mock.patch.object(service, "_refresh_caches", return_value={"desktop": True}):
                    result = service.reconcile_launchers()

        self.assertTrue(result["ok"])
        self.assertEqual("reconciled_with_warnings", result["state"])
        self.assertEqual(["good-app"], result["packages"])
        self.assertEqual([mock.call("good-app")], checked.call_args_list)
        self.assertIn("unowned.desktop", result["launcher_warnings"][0]["error"])

    def test_boot_reconciliation_uses_the_existing_fail_closed_installer(self):
        self.assertIn("ming-launcher-reconcile.service", DESKTOP)
        self.assertIn(
            "ExecStart=/usr/local/sbin/ming-package-installer reconcile-launchers --json",
            DESKTOP,
        )
        self.assertIn("ConditionPathIsDirectory=/opt/apps", DESKTOP)


class DesktopIconContracts(unittest.TestCase):
    def test_app_icons_have_no_opaque_card_and_labels_keep_an_outline(self):
        draw = PHONE[
            PHONE.index("    def draw_icon_fallback"):
            PHONE.index("    def item_at", PHONE.index("    def draw_icon_fallback"))
        ]

        self.assertNotIn('cr.set_source_rgba(1, 1, 1, profile["surface_alpha"])', draw)
        self.assertIn("for shadow_x, shadow_y in", draw)
        self.assertIn("PangoCairo.show_layout", draw)


class LiveInstallerContracts(unittest.TestCase):
    def test_live_desktop_autostarts_installer_once_after_shell_readiness(self):
        installer = DESKTOP.split("deploy_live_installer() {", 1)[1].split("# ======================== Xfce", 1)[0]
        autostart = DESKTOP.split("configure_autostart() {", 1)[1].split("# ======================== 首次启动", 1)[0]

        self.assertIn("ming-live-installer-autostart", installer)
        self.assertIn("live-installer-autostart.done", installer)
        self.assertIn("ming-phone-desktop", installer)
        self.assertIn("MING_LIVE_INSTALL_REQUEST=1", installer)
        self.assertIn("Exec=/usr/local/bin/ming-live-installer-autostart", autostart)
        self.assertIn("X-GNOME-Autostart-enabled=true", autostart)
