#!/usr/bin/env python3
"""Single-instance GTK3 application drawer for Ming OS."""

import argparse
import configparser
import importlib.util
import json
import os
import pathlib
import re
import socket
import shlex
import subprocess
import sys
import tempfile
import threading


ANIMATION_DURATION_MS = 160
DRAWER_REVEAL_OFFSET = 32
DRAWER_HEIGHT_RATIO = 0.72
# Keep the application sheet independent from the bottom Dock.  The window
# manager workarea still includes the Dock on some Xfce versions, so reserve a
# small fixed strip explicitly instead of anchoring the sheet to a Dock window.
DOCK_RESERVED_HEIGHT = 32
DRAWER_DOCK_GAP = 10
# Keep the sheet visually close to the Dock; the Dock gap and reserved strip
# already provide separation, so a second large margin creates a dead zone.
DRAWER_BOTTOM_MARGIN = 4
IPC_VERSION = 1
LAUNCH_PROXY = "/usr/local/bin/ming-launch"
DRAWER_STATE_PATH = pathlib.Path(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
) / "ming-app-drawer-open"
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
    "ming-firefox.desktop": "browser",
    "firefox-esr.desktop": "browser",
    "firefox.desktop": "browser",
    "xiahai-xiaoming.desktop": "agent",
    "papyrus.desktop": "agent",
}
CANONICAL_PREFERENCE = {
    "settings": "ming-control-center.desktop",
    "files": "ming-files.desktop",
    "terminal": "ming-terminal.desktop",
    "browser": "ming-firefox.desktop",
    "agent": "xiahai-xiaoming.desktop",
}

# Xfce remains an implementation dependency, not a second user-facing shell.
# Keep only the fallback launchers that are collapsed into a Ming core entry;
# utility/settings surfaces otherwise leak the old visual language into the
# drawer on upgraded systems.
VISIBLE_XFCE_FALLBACKS = frozenset({
    "xfce4-settings-manager.desktop",
    "xfce4-terminal.desktop",
    "thunar.desktop",
})
LEGACY_XFCE_LAUNCHERS = frozenset({
    "xfce4-appfinder.desktop",
    "xfce4-taskmanager.desktop",
    "xfce4-power-manager-settings.desktop",
    "xfce4-power-manager.desktop",
    "xfce4-about.desktop",
    "xfce4-mouse-settings.desktop",
    "xfce4-keyboard-settings.desktop",
    "xfce4-display-settings.desktop",
    "xfce4-appearance-settings.desktop",
    "xfce4-settings-editor.desktop",
    "xfce4-notifyd-config.desktop",
    "xfce4-screensaver-preferences.desktop",
    "xfce4-session-logout.desktop",
    "xfce4-run.desktop",
    "xfdesktop-settings.desktop",
    "xfce4-mime-settings.desktop",
    "exo-preferred-applications.desktop",
})


def is_legacy_system_entry(path):
    """Return whether a desktop file belongs to a retired Xfce utility UI."""
    basename = pathlib.Path(path).name.casefold()
    if basename in {item.casefold() for item in VISIBLE_XFCE_FALLBACKS}:
        return False
    return basename in {item.casefold() for item in LEGACY_XFCE_LAUNCHERS} or (
        basename.startswith(("xfce4-", "xfce-")) and basename.endswith(".desktop")
    )


def _desktop_directories():
    """Return the user's presentation-only Desktop directories.

    A desktop shortcut is not an application catalog entry.  Keep this list
    narrow to the XDG-resolved directory and the two names used by existing
    Ming installations; arbitrary directories named ``Desktop`` elsewhere
    remain valid catalog roots.
    """
    home = pathlib.Path.home()
    candidates = {home / "Desktop", home / "桌面"}
    for value in (
        os.environ.get("MING_DESKTOP_DIR"),
        os.environ.get("XDG_DESKTOP_DIR"),
    ):
        if value:
            candidate = pathlib.Path(os.path.expandvars(value)).expanduser()
            if candidate.is_absolute() and candidate != pathlib.Path("/"):
                candidates.add(candidate)
    try:
        resolved = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True, text=True, timeout=1, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        resolved = None
    if resolved is not None and resolved.returncode == 0:
        candidate = pathlib.Path((resolved.stdout or "").strip()).expanduser()
        if candidate.is_absolute() and candidate != pathlib.Path("/"):
            candidates.add(candidate)
    result = set()
    for candidate in candidates:
        try:
            result.add(candidate.resolve(strict=False))
        except (OSError, RuntimeError):
            continue
    return result


def _is_desktop_directory(path, desktop_dirs=None):
    try:
        candidate = pathlib.Path(path).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return candidate in (desktop_dirs if desktop_dirs is not None else _desktop_directories())


def _normalized_exec_program(exec_line):
    """Extract the executable used to identify duplicate launchers."""
    if not isinstance(exec_line, str) or not exec_line.strip() or "\x00" in exec_line:
        return ""
    if any(marker in exec_line for marker in (";", "`", "$(", "\n", "\r")):
        return ""
    try:
        argv = shlex.split(exec_line, posix=True)
    except ValueError:
        return ""
    if not argv or any(token in {"|", "||", "&&", ">", ">>", "<"} for token in argv):
        return ""
    offset = 0
    if pathlib.Path(argv[0]).name.casefold() == "env":
        offset = 1
        while offset < len(argv) and (argv[offset].startswith("-") or "=" in argv[offset]):
            offset += 1
    if offset >= len(argv):
        return ""
    program = argv[offset]
    if pathlib.Path(program).name.casefold() in {"sh", "bash", "dash", "zsh", "ksh"}:
        if "-c" in argv[offset + 1:]:
            return ""
    try:
        return str(pathlib.Path(program).resolve(strict=False)).casefold()
    except (OSError, RuntimeError):
        return program.casefold()


def _desktop_identity_fields(path):
    """Read launch-critical identity without executing a desktop file."""
    target = pathlib.Path(path)
    try:
        if target.stat().st_size > 256 * 1024:
            return None
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        with target.open("r", encoding="utf-8", errors="replace") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    if section.get("Type", "Application").strip().casefold() != "application":
        return None
    source = section.get("X-Ming-Source-Desktop", "").strip()
    if source.startswith("/") and "\x00" not in source:
        try:
            source_path = pathlib.Path(source).resolve(strict=False)
            if source_path.parent == pathlib.Path("/usr/share/applications"):
                return ("source", str(source_path).casefold())
        except (OSError, RuntimeError):
            pass
    program = _normalized_exec_program(section.get("Exec", ""))
    wm_class = section.get("StartupWMClass", "").strip().casefold()
    if wm_class:
        return ("wmclass", wm_class)
    if program:
        return ("exec", program)
    return None


def _desktop_preference(app):
    path = pathlib.Path(app.path)
    launchable = not bool(getattr(app, "diagnostic", ""))
    canonical = path.parent == pathlib.Path("/usr/share/applications")
    return (int(canonical and launchable), int(launchable), int(canonical))


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
        if preferred is None:
            launch_identity = _desktop_identity_fields(app.path)
            if launch_identity is not None:
                identity = launch_identity
        current = selected.get(identity)
        if current is None or basename == preferred or (
            basename != preferred
            and pathlib.Path(current.path).name != preferred
            and _desktop_preference(app) > _desktop_preference(current)
        ):
            selected[identity] = app
    return list(selected.values())


def drawer_geometry(workarea, dock_visible=True):
    workarea = COMMON.Rect.from_mapping(workarea)
    height = round(workarea.height * DRAWER_HEIGHT_RATIO)
    bottom = workarea.y + workarea.height
    dock_reserve = DOCK_RESERVED_HEIGHT + DRAWER_DOCK_GAP if dock_visible else 0
    y = bottom - height - dock_reserve - DRAWER_BOTTOM_MARGIN
    return COMMON.Rect(workarea.x, max(workarea.y, y), workarea.width, height)


def write_drawer_state(opened):
    try:
        DRAWER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if opened:
            DRAWER_STATE_PATH.write_text(str(os.getpid()) + "\n", encoding="ascii")
        else:
            DRAWER_STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def apply_dock_immersive_state(opened):
    """Publish drawer visibility for the session Dock coordinator."""
    write_drawer_state(bool(opened))
    try:
        subprocess.run(
            ["ming-session-healthcheck", "--immersive"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # The session loop still observes DRAWER_STATE_PATH and repairs the
        # Dock on its next bounded tick when the helper is unavailable.
        pass


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
        "start_opacity": 1.0 if reduced_motion else 0.0,
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
    desktop_dirs = _desktop_directories()
    for directory in paths:
        if _is_desktop_directory(directory, desktop_dirs):
            continue
        try:
            candidates = directory.glob("*.desktop")
        except OSError:
            continue
        for path in candidates:
            # A symlinked Desktop entry can otherwise escape the canonical
            # application roots and reintroduce an entry already shown by the
            # system catalog.  Only regular files are catalog inputs.
            try:
                if not path.is_file() or path.is_symlink():
                    continue
            except OSError:
                continue
            if path.name in seen:
                continue
            if is_legacy_system_entry(path):
                seen.add(path.name)
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

    def _workarea(self, immersive=False):
        display = self.Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        if immersive:
            # Plank's strut can survive briefly after it is hidden.  Use the
            # monitor's physical bottom edge for the drawer while retaining
            # the workarea's top boundary and horizontal placement.
            geometry = monitor.get_geometry()
            bottom = geometry.y + geometry.height
            return {"x": area.x, "y": area.y, "width": area.width,
                    "height": max(1, bottom - area.y)}
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
        window#ming-app-drawer {
          background: #F8FBF9;
          font-family: "Noto Sans CJK SC", sans-serif;
          font-weight: 400;
        }
        .drawer-root {
          background: #F8FBF9;
          border-top: 1px solid rgba(47, 138, 125, 0.16);
          padding: 16px;
        }
        .drawer-header { padding-bottom: 4px; }
        .drawer-close { border-radius: 9px; padding: 6px 12px; }
        .drawer-category { border-radius: 8px; padding: 8px 12px; }
        .drawer-category:checked { background: #2F8A7D; color: #ffffff; }
        .drawer-tile {
          border-radius: 10px;
          padding: 8px;
           background: #F8FBF9;
          border: 1px solid transparent;
        }
        .drawer-tile:hover { background: rgba(47, 138, 125, 0.09); border-color: rgba(47, 138, 125, 0.13); }
        .drawer-label { color: #1D2924; font-weight: 500; font-size: 11px; }
        .drawer-diagnostic { color: #A33A32; font-size: 10px; font-weight: 500; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        window.set_name("ming-app-drawer")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.get_style_context().add_class("drawer-root")
        window.add(root)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
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
        categories = Gtk.FlowBox()
        categories.set_selection_mode(Gtk.SelectionMode.NONE)
        categories.set_homogeneous(True)
        categories.set_min_children_per_line(2)
        categories.set_max_children_per_line(8)
        categories.set_row_spacing(4)
        categories.set_column_spacing(8)
        for category in CATEGORIES:
            button = Gtk.ToggleButton(label=category)
            button.get_style_context().add_class("drawer-category")
            button.set_active(category == self.category)
            button.connect("clicked", self._select_category, category)
            categories.add(button)
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
        # Rebuild the catalog before applying search/category filters so apps
        # installed after the drawer process started appear immediately.
        self.apps = discover_apps()
        for child in self.grid.get_children():
            self.grid.remove(child)
        visible = filter_apps(self.apps, self.search.get_text(), self.category, self.recent.load())
        for app in visible:
            button = self.Gtk.Button()
            button.set_size_request(116, 112)
            button.get_style_context().add_class("drawer-tile")
            content = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=4)
            icon_path = COMMON.resolve_icon_path(app.icon, app.path)
            image = (self.Gtk.Image.new_from_file(icon_path) if icon_path else
                     self.Gtk.Image.new_from_icon_name(
                         app.icon or "application-x-executable", self.Gtk.IconSize.DIALOG))
            image.set_pixel_size(40)
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
                diagnostic.set_lines(1)
                diagnostic.set_max_width_chars(11)
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
        launch_command = [
            LAUNCH_PROXY, "--desktop-file", str(app.path), "--source", "drawer",
        ]
        if rect is not None:
            launch_command.extend(("--rect", json.dumps(rect, separators=(",", ":"))))
        try:
            # The proxy owns trust checks, window probing, error notification and
            # broker fallback.  The drawer only reports that the proxy started.
            subprocess.Popen(launch_command, shell=False)
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
        apply_dock_immersive_state(True)
        self.apps = discover_apps()
        self.refresh()
        geometry = drawer_geometry(self._workarea(immersive=True), dock_visible=False)
        transition = drawer_transition(reduced_motion_enabled())
        self.window.resize(int(geometry.width), int(geometry.height))
        if transition["duration_ms"] == 0:
            self.window.move(int(geometry.x), int(geometry.y))
            self.window.set_opacity(1.0)
            self.window.show_all()
            self.window.present()
            self.search.grab_focus()
            return
        self._animation_geometry = geometry
        if not self.window.get_visible():
            self.window.move(int(geometry.x), int(geometry.y + DRAWER_REVEAL_OFFSET))
            self.window.set_opacity(transition["start_opacity"])
            self.window.show_all()
            self.window.present()
        self._animate_to(1.0, geometry)
        self.search.grab_focus()

    def hide(self):
        apply_dock_immersive_state(False)
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
            # Keep the reveal bounded so old GPUs do not move a full-screen
            # surface on every frame.  Progress stays reversible for toggles.
            eased = COMMON.ease_out_cubic(current)
            y = active_geometry.y + DRAWER_REVEAL_OFFSET * (1.0 - eased)
            self.window.move(int(active_geometry.x), int(y))
            self.window.set_opacity(eased)
            if self._animation.active:
                return True
            self._animation_source = 0
            if self._animation.target <= 0.0:
                self.window.hide()
            return False

        self._animation_source = self.GLib.timeout_add(33, step)

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
