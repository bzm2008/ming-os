#!/usr/bin/env python3
"""Single-instance GTK3 application drawer for Ming OS."""

import argparse
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading


ANIMATION_DURATION_MS = 200
DRAWER_HEIGHT_RATIO = 0.72
IPC_VERSION = 1
CATEGORIES = ("全部", "最近", "网络", "办公", "影音", "游戏", "工具", "系统")
_CATEGORY_RULES = (
    ("网络", {"Network", "WebBrowser", "Email", "Chat"}),
    ("办公", {"Office", "WordProcessor", "Spreadsheet", "Presentation"}),
    ("影音", {"Audio", "Video", "AudioVideo", "Graphics", "Photography"}),
    ("游戏", {"Game"}),
    ("系统", {"System", "Settings", "Security"}),
    ("工具", {"Utility", "Development", "FileManager", "TerminalEmulator"}),
)
CANONICAL_LAUNCHERS = {
    "ming-control-center.desktop": "settings",
    "ming-settings.desktop": "settings",
    "xfce4-settings-manager.desktop": "settings",
    "ming-files.desktop": "files",
    "thunar.desktop": "files",
    "ming-terminal.desktop": "terminal",
    "xfce4-terminal.desktop": "terminal",
    "ming-edge.desktop": "edge",
    "microsoft-edge.desktop": "edge",
    "microsoft-edge-stable.desktop": "edge",
}
CANONICAL_PREFERENCE = {
    "settings": "ming-control-center.desktop",
    "files": "ming-files.desktop",
    "terminal": "ming-terminal.desktop",
    "edge": "ming-edge.desktop",
}


def _load_common():
    path = pathlib.Path(__file__).with_name("ming-shell-common.py")
    spec = importlib.util.spec_from_file_location("ming_shell_common_for_drawer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = _load_common()


def gtk_loaded():
    return "gi" in sys.modules


def category_for(app):
    categories = set(getattr(app, "categories", ()))
    for label, matches in _CATEGORY_RULES:
        if categories.intersection(matches):
            return label
    return "工具"


def filter_apps(apps, query="", category="全部", recent_paths=()):
    query = (query or "").strip().casefold()
    recent_order = {str(path): index for index, path in enumerate(recent_paths)}
    filtered = []
    for app in apps:
        if category == "最近" and str(app.path) not in recent_order:
            continue
        if category not in ("全部", "最近") and category_for(app) != category:
            continue
        haystack = " ".join((app.name, getattr(app, "comment", ""), " ".join(app.categories))).casefold()
        if query and query not in haystack:
            continue
        filtered.append(app)
    if category == "最近":
        return sorted(filtered, key=lambda app: recent_order[str(app.path)])
    return sorted(filtered, key=lambda app: app.name.casefold())


def canonical_identity(app):
    return CANONICAL_LAUNCHERS.get(pathlib.Path(app.path).name, pathlib.Path(app.path).name)


def deduplicate_apps(apps):
    selected = {}
    for app in apps:
        basename = pathlib.Path(app.path).name
        if basename == "ming-update.desktop":
            continue
        identity = canonical_identity(app)
        preferred = CANONICAL_PREFERENCE.get(identity)
        current = selected.get(identity)
        if current is None or basename == preferred:
            selected[identity] = app
    return list(selected.values())


def drawer_geometry(workarea):
    workarea = COMMON.Rect.from_mapping(workarea)
    height = round(workarea.height * DRAWER_HEIGHT_RATIO)
    return COMMON.Rect(workarea.x, workarea.y + workarea.height - height, workarea.width, height)


def reduced_motion_enabled(path=None):
    path = pathlib.Path(path or pathlib.Path.home() / ".config/ming-os/settings.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("reduced_motion", False)) if isinstance(data, dict) else False


def drawer_transition(reduced_motion):
    return {
        "duration_ms": 0 if reduced_motion else ANIMATION_DURATION_MS,
        "start_opacity": 1.0,
    }


class DrawerAnimation:
    """A reversible progress clock; the controller owns exactly one GLib source."""

    def __init__(self, duration_ms=ANIMATION_DURATION_MS):
        self.duration_ms = max(1, int(duration_ms))
        self.progress = 0.0
        self.target = 0.0
        self.last_tick_ms = None

    @property
    def active(self):
        return abs(self.target - self.progress) > 0.0001

    def set_target(self, target, now_ms):
        self.advance(now_ms)
        self.target = max(0.0, min(1.0, float(target)))
        self.last_tick_ms = float(now_ms)
        return self.progress

    def advance(self, now_ms):
        now_ms = float(now_ms)
        if self.last_tick_ms is None:
            self.last_tick_ms = now_ms
            return self.progress
        elapsed = max(0.0, now_ms - self.last_tick_ms)
        direction = 1.0 if self.target > self.progress else -1.0
        if self.active:
            self.progress = max(0.0, min(1.0, self.progress + direction * elapsed / self.duration_ms))
        self.last_tick_ms = now_ms
        return self.progress


def add_to_desktop_argv(app):
    path = app.path.as_posix() if isinstance(app.path, pathlib.Path) else str(app.path)
    return "ming-phone-desktop", "--add", path


def toggle_message(rect=None):
    message = {"version": IPC_VERSION, "action": "toggle", "source": "drawer"}
    if rect is not None:
        message["rect"] = COMMON.Rect.from_mapping(rect).to_dict()
    return message


class RecentStore:
    def __init__(self, path=None, limit=12):
        self.path = pathlib.Path(path or pathlib.Path.home() / ".local/state/ming-os/recent-apps.json")
        self.limit = max(1, min(50, int(limit)))

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item.endswith(".desktop")][:self.limit]

    def touch(self, desktop_path):
        desktop_path = str(desktop_path)
        values = [desktop_path] + [item for item in self.load() if item != desktop_path]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="recent-", dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(values[:self.limit], stream, ensure_ascii=False)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def discover_apps(paths=None):
    paths = paths or (
        pathlib.Path.home() / ".local/share/applications",
        pathlib.Path("/usr/local/share/applications"),
        pathlib.Path("/usr/share/applications"),
    )
    found = {}
    seen = set()
    for directory in paths:
        try:
            candidates = directory.glob("*.desktop")
        except OSError:
            continue
        for path in candidates:
            if path.name in seen:
                continue
            seen.add(path.name)
            try:
                entry = COMMON.diagnose_desktop_file(path)
            except (OSError, ValueError):
                continue
            if entry is not None:
                found[path.name] = entry
    return deduplicate_apps(list(found.values()))


def widget_source_rect(origin, allocation):
    if not isinstance(origin, (tuple, list)) or len(origin) not in (2, 3):
        raise ValueError("invalid window origin")
    offset = 1 if len(origin) == 3 else 0
    x = float(origin[offset]) + float(getattr(allocation, "x", 0))
    y = float(origin[offset + 1]) + float(getattr(allocation, "y", 0))
    return COMMON.Rect(x, y, allocation.width, allocation.height).to_dict()


def send_toggle(rect=None):
    path = COMMON.runtime_socket_path("app-drawer")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.4)
            client.connect(str(path))
            client.sendall(COMMON.encode_json_line(toggle_message(rect)))
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _load_gtk():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    return Gdk, GLib, Gtk


class DrawerController:
    def __init__(self):
        self.Gdk, self.GLib, self.Gtk = _load_gtk()
        self.apps = discover_apps()
        self.recent = RecentStore()
        self.category = "全部"
        self.window = self._build_window()
        self._server = None
        self._animation = DrawerAnimation()
        self._animation_source = 0
        self._animation_geometry = None

    def _workarea(self):
        display = self.Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        return {"x": area.x, "y": area.y, "width": area.width, "height": area.height}

    def _build_window(self):
        Gtk = self.Gtk
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("Ming 应用抽屉")
        window.set_decorated(False)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_type_hint(self.Gdk.WindowTypeHint.DIALOG)
        window.set_accept_focus(True)
        window.set_focus_on_map(True)
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        window#ming-app-drawer { background: #F8FBF9; }
        .drawer-root {
          background: #F8FBF9;
          border-top: 1px solid rgba(47, 138, 125, 0.16);
          padding: 20px;
        }
        .drawer-header { padding-bottom: 2px; }
        .drawer-close { border-radius: 9px; padding: 7px 14px; }
        .drawer-category { border-radius: 8px; padding: 6px 12px; }
        .drawer-category:checked { background: #2F8A7D; color: #ffffff; }
        .drawer-tile {
          border-radius: 10px;
          padding: 10px 8px;
          background: transparent;
          border: 1px solid transparent;
        }
        .drawer-tile:hover { background: rgba(47, 138, 125, 0.09); border-color: rgba(47, 138, 125, 0.13); }
        .drawer-label { color: #1D2924; font-weight: 700; font-size: 11px; }
        .drawer-diagnostic { color: #A33A32; font-size: 9px; font-weight: 700; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        window.set_name("ming-app-drawer")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.get_style_context().add_class("drawer-root")
        window.add(root)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.get_style_context().add_class("drawer-header")
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索应用")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda _entry: self.refresh())
        header.pack_start(self.search, True, True, 0)
        close = Gtk.Button(label="关闭")
        close.get_style_context().add_class("drawer-close")
        close.connect("clicked", lambda _button: self.hide())
        header.pack_start(close, False, False, 0)
        root.pack_start(header, False, False, 0)
        categories = Gtk.Box(spacing=6)
        for category in CATEGORIES:
            button = Gtk.ToggleButton(label=category)
            button.get_style_context().add_class("drawer-category")
            button.set_active(category == self.category)
            button.connect("clicked", self._select_category, category)
            categories.pack_start(button, False, False, 0)
        root.pack_start(categories, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        self.grid = Gtk.FlowBox()
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self.grid.set_max_children_per_line(8)
        self.grid.set_row_spacing(12)
        self.grid.set_column_spacing(12)
        scroller.add(self.grid)
        root.pack_start(scroller, True, True, 0)
        window.connect("key-press-event", self._on_key)
        window.connect("delete-event", self._on_delete)
        self.refresh()
        return window

    def _on_delete(self, *_args):
        self.hide()
        return True

    def _select_category(self, button, category):
        if button.get_active():
            self.category = category
            self.refresh()

    def _on_key(self, _window, event):
        if event.keyval == self.Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def refresh(self):
        for child in self.grid.get_children():
            self.grid.remove(child)
        visible = filter_apps(self.apps, self.search.get_text(), self.category, self.recent.load())
        for app in visible:
            button = self.Gtk.Button()
            button.set_size_request(116, 100)
            button.get_style_context().add_class("drawer-tile")
            content = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=7)
            image = self.Gtk.Image.new_from_icon_name(app.icon or "application-x-executable", self.Gtk.IconSize.DIALOG)
            image.set_pixel_size(42)
            label = self.Gtk.Label(label=app.name)
            label.set_justify(self.Gtk.Justification.CENTER)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(2)
            label.set_ellipsize(3)
            label.set_lines(2)
            label.set_max_width_chars(11)
            label.get_style_context().add_class("drawer-label")
            content.pack_start(image, True, True, 0)
            content.pack_start(label, False, False, 0)
            if getattr(app, "diagnostic", ""):
                diagnostic = self.Gtk.Label(label="启动器需修复")
                diagnostic.set_ellipsize(3)
                diagnostic.get_style_context().add_class("drawer-diagnostic")
                content.pack_start(diagnostic, False, False, 0)
            button.add(content)
            button.add_events(self.Gdk.EventMask.BUTTON_RELEASE_MASK)
            button.connect("button-release-event", self._activate_button, app)
            button.connect("button-press-event", self._context_menu, app)
            self.grid.add(button)
        self.grid.show_all()

    def _activate_button(self, button, event, app):
        if getattr(event, "button", 0) != 1:
            return False
        self.launch(app, button)
        return True

    def _context_menu(self, _button, event, app):
        if event.button != 3:
            return False
        menu = self.Gtk.Menu()
        launch = self.Gtk.MenuItem(label="打开")
        launch.connect("activate", lambda _item: self.launch(app, None))
        desktop = self.Gtk.MenuItem(label="添加到桌面")
        desktop.connect("activate", lambda _item: subprocess.Popen(add_to_desktop_argv(app), shell=False))
        menu.append(launch)
        menu.append(desktop)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def launch(self, app, widget):
        diagnostic = getattr(app, "diagnostic", "")
        if diagnostic:
            dialog = self.Gtk.MessageDialog(
                transient_for=self.window,
                flags=0,
                message_type=self.Gtk.MessageType.ERROR,
                buttons=self.Gtk.ButtonsType.CLOSE,
                text="此应用暂时无法启动",
            )
            dialog.format_secondary_text(diagnostic + "\n请重新安装该软件后再试。")
            dialog.run()
            dialog.destroy()
            return False
        rect = None
        if widget and widget.get_window():
            origin = widget.get_window().get_origin()
            allocation = widget.get_allocation()
            rect = widget_source_rect(origin, allocation)
        started = COMMON.send_launch_request(str(app.path), "drawer", rect)
        if not started:
            try:
                subprocess.Popen(list(app.argv), shell=False)
                started = True
            except (OSError, ValueError, subprocess.SubprocessError):
                started = False
        if not started:
            dialog = self.Gtk.MessageDialog(
                transient_for=self.window,
                flags=0,
                message_type=self.Gtk.MessageType.ERROR,
                buttons=self.Gtk.ButtonsType.CLOSE,
                text="无法打开此应用",
            )
            dialog.format_secondary_text("启动命令不可用，请查看桌面启动日志。")
            dialog.run()
            dialog.destroy()
            return False
        self.recent.touch(app.path)
        self.hide()
        return True

    def show(self):
        # Always rebuild the catalog before presentation.  This is intentionally
        # above the reduced-motion branch so an install is visible even when
        # animations are disabled.
        self.apps = discover_apps()
        self.refresh()
        geometry = drawer_geometry(self._workarea())
        transition = drawer_transition(reduced_motion_enabled())
        self.window.resize(int(geometry.width), int(geometry.height))
        if transition["duration_ms"] == 0:
            self.window.move(int(geometry.x), int(geometry.y))
            self.window.show_all()
            self.window.present()
            self.search.grab_focus()
            return
        self._animation_geometry = geometry
        if not self.window.get_visible():
            self.window.move(int(geometry.x), int(geometry.y + geometry.height))
            self.window.show_all()
            self.window.present()
        self.window.set_opacity(transition["start_opacity"])
        self._animate_to(1.0, geometry)
        self.search.grab_focus()

    def hide(self):
        if not self.window.get_visible():
            return
        transition = drawer_transition(reduced_motion_enabled())
        if transition["duration_ms"] == 0:
            self.window.hide()
            return
        self._animate_to(0.0, drawer_geometry(self._workarea()))

    def _animate_to(self, target, geometry):
        self._animation_geometry = geometry
        self._animation.set_target(target, self.GLib.get_monotonic_time() / 1000.0)
        if self._animation_source:
            return

        def step():
            current = self._animation.advance(self.GLib.get_monotonic_time() / 1000.0)
            active_geometry = self._animation_geometry or geometry
            # Ease the physical motion but preserve linear progress internally,
            # allowing a second toggle to reverse from the exact current point.
            eased = COMMON.ease_out_cubic(current)
            y = active_geometry.y + active_geometry.height * (1.0 - eased)
            self.window.move(int(active_geometry.x), int(y))
            if self._animation.active:
                return True
            self._animation_source = 0
            if self._animation.target <= 0.0:
                self.window.hide()
            return False

        self._animation_source = self.GLib.timeout_add(16, step)

    def toggle(self):
        if self.window.get_visible() and self._animation.target > 0.0:
            self.hide()
        else:
            self.show()

    def serve(self):
        self._server = COMMON.claim_runtime_socket("app-drawer", backlog=4)

        def loop():
            while True:
                try:
                    connection, _address = self._server.accept()
                    with connection:
                        message = COMMON.recv_json_line(connection, timeout=0.5)
                    if (
                        message.get("version") == IPC_VERSION
                        and message.get("action") == "toggle"
                        and set(message).issubset({"version", "action", "source", "rect"})
                    ):
                        self.GLib.idle_add(self.toggle)
                except (OSError, ValueError):
                    continue
        threading.Thread(target=loop, name="ming-drawer-ipc", daemon=True).start()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--toggle", action="store_true")
    parser.add_argument("--rect")
    args = parser.parse_args(argv)
    rect = json.loads(args.rect) if args.rect else None
    if send_toggle(rect):
        return 0
    controller = DrawerController()
    try:
        controller.serve()
    except COMMON.InstanceAlreadyRunning:
        for _attempt in range(5):
            if send_toggle(rect):
                return 0
        return 1
    controller.show()
    controller.Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
