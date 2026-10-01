#!/usr/bin/env python3
"""Ming OS GTK3/X11 bottom taskbar.

The taskbar is deliberately a small user-session process.  It owns only its
window and launches existing Ming entry points; privileged device changes stay
behind the established settings/device-control backends.
"""

import argparse
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gdk, GdkX11, GLib, Gtk


APP_DIR = pathlib.Path("/usr/share/applications")
RUNTIME_DIR = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
LOCK_PATH = RUNTIME_DIR / "ming-taskbar.lock"
LOG_PATH = pathlib.Path.home() / ".cache" / "ming-os" / "ming-taskbar.log"
TASKBAR_WIDTH = 920
TASKBAR_HEIGHT = 64
TASKBAR_MARGIN = 8
REFRESH_MS = 1500
STATUS_REFRESH_MS = 8000

FIXED_APPS = (
    ("应用库", "ming-app-library.desktop", "view-grid-symbolic", "ming-app-drawer --toggle"),
    ("设置", "ming-settings.desktop", "preferences-system-symbolic", "ming-control-center"),
    ("文件", "ming-files.desktop", "system-file-manager-symbolic", "ming-files"),
    ("Firefox", "ming-firefox.desktop", "web-browser-symbolic", "ming-firefox"),
    ("商店", "ming-store.desktop", "software-store-symbolic", "ming-store"),
    ("终端", "ming-terminal.desktop", "utilities-terminal-symbolic", "ming-terminal"),
)


def low_resource_mode():
    return any(os.environ.get(name, "0") == "1" for name in (
        "MING_TASKBAR_LOW_RESOURCE", "MING_LOW_RESOURCE", "MING_REDUCED_MOTION",
    ))


def log(message):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def run_command(command, timeout=2):
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            check=False, env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def run_json(command, timeout=3):
    result = run_command(command, timeout=timeout)
    if result is None or result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def launch(command, *args):
    argv = list(command) if isinstance(command, (tuple, list)) else [command]
    argv.extend(args)
    try:
        subprocess.Popen(argv, start_new_session=True, close_fds=True)
        return True
    except OSError as exc:
        log("launch failed %r: %s" % (argv, exc))
        return False


def desktop_entry_path(name):
    path = APP_DIR / name
    return path if path.is_file() else None


def x11_window_id(window):
    try:
        return GdkX11.X11Window.get_xid(window)
    except (ImportError, AttributeError, TypeError):
        return None


def set_workarea_strut(window, screen_width, screen_height):
    """Reserve the bottom edge using the EWMH partial strut property."""
    xid = x11_window_id(window)
    if xid is None:
        return False
    bottom = TASKBAR_HEIGHT + TASKBAR_MARGIN * 2
    values = [0, 0, 0, bottom, 0, 0, 0, 0, 0, 0, 0, max(0, screen_width - 1)]
    result = run_command([
        "xprop", "-id", "0x%x" % xid, "-f", "_NET_WM_STRUT_PARTIAL", "32c",
        "-set", "_NET_WM_STRUT_PARTIAL", ",".join(str(value) for value in values),
    ])
    return bool(result and result.returncode == 0)


def clear_workarea_strut(window):
    xid = x11_window_id(window)
    if xid is None:
        return
    run_command(["xprop", "-id", "0x%x" % xid, "-remove", "_NET_WM_STRUT_PARTIAL"])


def monitor_geometry():
    screen = Gdk.Screen.get_default()
    if screen is None:
        return 0, 0, 1024, 768
    monitor = screen.get_primary_monitor()
    geometry = screen.get_monitor_geometry(monitor)
    return geometry.x, geometry.y, geometry.width, geometry.height


def window_rows():
    result = run_command(["wmctrl", "-lGx"], timeout=2)
    if result is None or result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 7)
        if len(fields) < 5 or not fields[0].startswith("0x"):
            continue
        window_id, desktop, x, y, width = fields[:5]
        title = fields[7] if len(fields) > 7 else ""
        wm_class = fields[6] if len(fields) > 6 else ""
        if not title or "ming-taskbar" in wm_class.lower():
            continue
        if any(token in wm_class.lower() for token in ("desktop", "plank", "xfce4-panel")):
            continue
        rows.append({"id": window_id, "desktop": desktop, "title": title, "class": wm_class})
    return rows


def activate_window(window_id):
    return bool(run_command(["wmctrl", "-i", "-a", window_id], timeout=2))


def window_icon_name(wm_class):
    name = (wm_class or "").lower()
    if "firefox" in name:
        return "web-browser-symbolic"
    if "terminal" in name:
        return "utilities-terminal-symbolic"
    if "thunar" in name or "file" in name:
        return "system-file-manager-symbolic"
    if "settings" in name:
        return "preferences-system-symbolic"
    return "application-x-executable-symbolic"


class SingleInstance:
    def __init__(self):
        self.handle = None

    def acquire(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.handle = LOCK_PATH.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            self.handle.close()
            self.handle = None
            return False
        self.handle.write("%s\n" % os.getpid())
        self.handle.flush()
        return True

    def close(self):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
            except OSError:
                pass
            self.handle = None


class MingTaskbar(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, type=Gtk.WindowType.TOPLEVEL)
        self.low_resource = low_resource_mode()
        self._buttons = {}
        self._status_labels = {}
        self._strut_width = 0
        self._build_window()
        self._build_ui()
        self._position_window()
        self.connect("destroy", self._on_destroy)
        GLib.timeout_add(REFRESH_MS, self.refresh_windows)
        GLib.timeout_add(STATUS_REFRESH_MS, self.refresh_status)

    def _build_window(self):
        self.set_title("Ming Taskbar")
        self.set_name("ming-taskbar")
        try:
            self.set_wmclass("ming-taskbar", "MingTaskbar")
        except (AttributeError, TypeError):
            pass
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_app_paintable(True)
        self.set_size_request(TASKBAR_WIDTH, TASKBAR_HEIGHT)
        self.connect("realize", self._on_realize)
        provider = Gtk.CssProvider()
        provider.load_from_data(self._css().encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _css(self):
        if self.low_resource:
            surface = "#eef7f2"
            shadow = "0 2px 8px rgba(24, 74, 65, 0.18)"
        else:
            surface = "rgba(246, 252, 249, 0.94)"
            shadow = "0 5px 22px rgba(24, 74, 65, 0.22)"
        return """
        #ming-taskbar {{ background-color: {surface}; border: 1px solid rgba(47,138,125,0.42);
            border-radius: 18px; box-shadow: {shadow}; padding: 6px 10px; }}
        #ming-taskbar button {{ color: #17443d; background: transparent; border: 0; border-radius: 12px;
            padding: 7px 9px; min-width: 34px; min-height: 34px; }}
        #ming-taskbar button:hover, #ming-taskbar button:focus {{ background: rgba(47,138,125,0.16); }}
        #ming-taskbar button:checked {{ background: rgba(47,138,125,0.23); }}
        #ming-taskbar .taskbar-label {{ color: #17443d; font-size: 12px; }}
        #ming-taskbar .taskbar-status {{ color: #24584f; font-size: 11px; }}
        #ming-taskbar .taskbar-divider {{ color: rgba(47,138,125,0.44); }}
        """.format(surface=surface, shadow=shadow)

    def _on_realize(self, _widget):
        _x, _y, width, height = monitor_geometry()
        self._strut_width = width
        set_workarea_strut(self.get_window(), width, height)

    def _position_window(self):
        x, y, width, height = monitor_geometry()
        taskbar_width = min(TASKBAR_WIDTH, max(560, width - 32))
        self.resize(taskbar_width, TASKBAR_HEIGHT)
        self.move(x + max(0, (width - taskbar_width) // 2), y + height - TASKBAR_HEIGHT - TASKBAR_MARGIN)

    def _button(self, label, icon, callback, tooltip=None):
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(tooltip or label)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        box.pack_start(image, True, True, 0)
        if label:
            text = Gtk.Label(label=label)
            text.get_style_context().add_class("taskbar-label")
            box.pack_start(text, False, False, 0)
        button.add(box)
        button.connect("clicked", callback)
        return button

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(root)

        menu_button = self._button("", "ming-os-menu", self._show_menu, "Ming 菜单")
        root.pack_start(menu_button, False, False, 0)
        search_button = self._button("", "system-search-symbolic", self._open_drawer, "搜索应用")
        root.pack_start(search_button, False, False, 0)

        divider = Gtk.Label(label="·")
        divider.get_style_context().add_class("taskbar-divider")
        root.pack_start(divider, False, False, 2)

        self.app_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        root.pack_start(self.app_box, False, False, 0)
        self.window_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        root.pack_start(self.window_box, True, True, 4)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        root.pack_end(self.status_box, False, False, 0)
        quick_button = self._button("", "preferences-system-symbolic", self._show_quick_settings, "快速设置")
        self.status_box.pack_start(quick_button, False, False, 0)
        self._status_labels["network"] = self._status_label("网络")
        self._status_labels["audio"] = self._status_label("音量")
        self._status_labels["battery"] = self._status_label("电源")
        for key in ("network", "audio", "battery"):
            self.status_box.pack_start(self._status_labels[key], False, False, 0)
        self.clock = Gtk.Label(label="--:--")
        self.clock.get_style_context().add_class("taskbar-status")
        self.status_box.pack_end(self.clock, False, False, 4)
        self.refresh_apps()
        self.refresh_windows()
        self.refresh_status()

    @staticmethod
    def _status_label(text):
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("taskbar-status")
        label.set_tooltip_text(text)
        return label

    def _clear(self, box):
        for child in box.get_children():
            box.remove(child)

    def refresh_apps(self):
        self._clear(self.app_box)
        for label, desktop, icon, command in FIXED_APPS:
            if desktop_entry_path(desktop) is None and not command:
                continue
            button = self._button("", icon, lambda _button, cmd=command: self._launch_command(cmd), label)
            self.app_box.pack_start(button, False, False, 0)
        self.app_box.show_all()

    def refresh_windows(self):
        self._clear(self.window_box)
        current_id = None
        try:
            current_id = "0x%x" % x11_window_id(self.get_window())
        except (TypeError, ValueError):
            pass
        rows = [row for row in window_rows() if row["id"].lower() != str(current_id).lower()]
        for row in rows[:8]:
            title = row["title"][:20]
            button = self._button("", window_icon_name(row["class"]),
                                  lambda _button, wid=row["id"]: activate_window(wid), title)
            button.get_style_context().add_class("taskbar-window")
            self.window_box.pack_start(button, False, False, 0)
        self.window_box.show_all()
        return True

    def refresh_status(self):
        status = run_json(["ming-device-control", "status", "--json"], timeout=4)
        ethernet = status.get("ethernet", {})
        wifi = status.get("wifi", {})
        audio = status.get("audio", {})
        battery = status.get("battery", {})
        network_state = ethernet.get("state") or wifi.get("state") or "unknown"
        network_text = "网络" if network_state in ("connected", "ready") else "离线"
        audio_value = audio.get("value")
        audio_text = "音量 %s%%" % audio_value if isinstance(audio_value, int) else "音量"
        battery_value = battery.get("value")
        battery_text = "电源 %s%%" % battery_value if isinstance(battery_value, int) else "电源"
        self._status_labels["network"].set_text(network_text)
        self._status_labels["audio"].set_text(audio_text)
        self._status_labels["battery"].set_text(battery_text)
        self.clock.set_text(time.strftime("%H:%M"))
        return True

    def _launch_command(self, command):
        argv = command.split()
        if argv and argv[0] == "ming-app-drawer":
            launch(argv)
        elif argv:
            launch(argv)

    def _open_drawer(self, _button):
        self._launch_command("ming-app-drawer --toggle")

    def _show_menu(self, button):
        menu = Gtk.Menu()
        for label, command in (("应用库", "ming-app-drawer --toggle"), ("设置", "ming-control-center"),
                               ("文件", "ming-files"), ("更新", "ming-control-center --page update"),
                               ("电源", "ming-power-action menu")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, cmd=command: self._launch_command(cmd))
            menu.append(item)
        menu.show_all()
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def _show_quick_settings(self, button):
        menu = Gtk.Menu()
        status = run_json(["ming-device-control", "status", "--json"], timeout=4)
        for label, page in (("网络设置", "network"), ("声音设置", "sound"), ("显示与亮度", "display")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, p=page: self._launch_command("ming-control-center --page " + p))
            menu.append(item)
        summary = Gtk.MenuItem(label="状态已更新" if status else "状态暂不可用")
        summary.set_sensitive(False)
        menu.append(summary)
        menu.show_all()
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def _on_destroy(self, _widget):
        clear_workarea_strut(self.get_window())
        Gtk.main_quit()


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-taskbar")
    parser.add_argument("--check", action="store_true", help="检查是否能连接 X11")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.check:
        return 0 if os.environ.get("DISPLAY") else 1
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    try:
        Gtk.init([])
        taskbar = MingTaskbar()
        signal.signal(signal.SIGTERM, lambda *_args: taskbar.destroy())
        signal.signal(signal.SIGINT, lambda *_args: taskbar.destroy())
        taskbar.show_all()
        Gtk.main()
    except Exception as exc:
        log("taskbar exited with error: %s" % exc)
        return 1
    finally:
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
