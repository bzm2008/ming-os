import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKBAR = ROOT / "assets" / "ming-taskbar.py"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
PHONE = ROOT / "assets" / "ming-phone-desktop.py"


class MingTaskbarContracts(unittest.TestCase):
    def test_taskbar_asset_is_gtk3_x11_and_has_required_surfaces(self):
        source = TASKBAR.read_text(encoding="utf-8")
        self.assertIn("gi.require_version('Gtk', '3.0')", source)
        self.assertIn("gi.require_version('Gdk', '3.0')", source)
        self.assertIn("Gdk.WindowTypeHint.DOCK", source)
        for marker in ("Ming 菜单", "搜索", "快速设置", "wmctrl", "MING_TASKBAR_LOW_RESOURCE"):
            self.assertIn(marker, source)
        ast.parse(source)

    def test_taskbar_has_plank_fallback_and_autostart(self):
        source = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("ming-taskbar.py", source)
        self.assertIn("MING_TASKBAR_MODE", source)
        self.assertIn("MING_TASKBAR_MODE=1", source)
        self.assertIn("Exec=/usr/local/bin/ming-taskbar-watchdog", source)
        self.assertIn("X-Ming-Managed-By=ming-session-healthcheck", source)
        self.assertIn("ming-plank-watchdog", source)
        self.assertIn("Ming Taskbar", source)
        self.assertIn("session healthcheck owns the Plank fallback", source)

    def test_taskbar_uses_workarea_strut_and_does_not_touch_grub(self):
        source = TASKBAR.read_text(encoding="utf-8")
        self.assertIn("_NET_WM_STRUT_PARTIAL", source)
        self.assertIn("set_skip_taskbar_hint", source)
        self.assertNotIn("grub.cfg", source)
        self.assertNotIn("apt-get", source)

    def test_taskbar_keeps_launches_structured_and_has_low_resource_surface(self):
        source = TASKBAR.read_text(encoding="utf-8")
        for marker in (
            "subprocess.Popen(argv, start_new_session=True, close_fds=True)",
            "MING_TASKBAR_LOW_RESOURCE",
            'surface = "#eef7f2"',
            'surface = "#f5fbf7"',
            "int(width * 0.78)",
            "TASKBAR_HEIGHT = 56",
            "native opaque window visual",
            "ming-taskbar.ready",
            "mark_ready()",
            "override_background_color",
            "window_color.parse",
            "Gdk.WindowTypeHint.DOCK",
            "refresh_windows",
            "refresh_status",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("shell=True", source)

    def test_phone_desktop_does_not_render_the_floating_status_widget_in_taskbar_mode(self):
        source = PHONE.read_text(encoding="utf-8")
        for marker in (
            'self.taskbar_mode = os.environ.get("MING_TASKBAR_MODE", "0") == "1"',
            "if self.taskbar_mode:",
            "self.status.hide()",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
