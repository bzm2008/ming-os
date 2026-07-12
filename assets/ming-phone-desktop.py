#!/usr/bin/env python3
import configparser
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


def widget_state_path():
    return Path.home() / ".config" / "ming-os" / "status-widget.json"


def load_widget_state(path=None):
    """Load only the compact-widget preference; corrupt data means expanded."""
    target = Path(path) if path else widget_state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"collapsed": False}
    if not isinstance(data, dict) or not isinstance(data.get("collapsed"), bool):
        return {"collapsed": False}
    return {"collapsed": data["collapsed"]}


def save_widget_state(collapsed, path=None):
    """Atomically persist the one status-widget setting without touching layouts."""
    target = Path(path) if path else widget_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".%s.%s.tmp" % (target.name, os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"collapsed": bool(collapsed)}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo


def load_shell_common():
    for path in (
        Path("/usr/local/lib/ming-os/ming-shell-common.py"),
        Path(__file__).resolve().with_name("ming-shell-common.py"),
    ):
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_shell_common_for_desktop", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Ming shell common runtime is missing")


COMMON = load_shell_common()

HOME = Path.home()
STATE_DIR = HOME / ".config" / "ming-os"
LAYOUT_PATH = STATE_DIR / "desktop-layout.json"
LAST_GOOD_LAYOUT_PATH = STATE_DIR / "desktop-layout.last-good.json"
READY_MARKER = HOME / ".cache" / "ming-os" / "ming-phone-desktop.ready"
DESKTOP_DIR = HOME / "Desktop"
APP_DIRS = [DESKTOP_DIR, Path("/usr/share/applications"), HOME / ".local/share/applications"]
CORE_NAMES = {
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-terminal.desktop",
    "ming-edge.desktop",
    "spark-store.desktop",
    "garlic-claw.desktop",
}
DESKTOP_ORDER = {name: idx for idx, name in enumerate([
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-edge.desktop",
    "spark-store.desktop",
    "garlic-claw.desktop",
    "ming-terminal.desktop",
])}
CORE_FALLBACKS = {
    "ming-edge.desktop": ["microsoft-edge.desktop", "microsoft-edge-stable.desktop"],
    "spark-store.desktop": ["ming-install-spark-store.desktop"],
}
CORE_GENERATED = {
    "ming-settings.desktop": ("Ming 设置", "ming-control-center", "ming-control-center", "Settings;System;"),
    "ming-files.desktop": ("文件", "ming-files", "files-icon", "System;FileManager;"),
    "ming-terminal.desktop": ("Ming 终端", "ming-terminal", "ming-terminal", "System;TerminalEmulator;"),
    "ming-edge.desktop": ("Microsoft Edge", "ming-edge", "microsoft-edge", "Network;WebBrowser;"),
    "garlic-claw.desktop": ("Garlic Claw", "xfce4-terminal --hide-menubar --title=\"Garlic Claw\" -e garlic-claw", "utilities-terminal", "Utility;"),
}
LOG_PATH = HOME / ".cache" / "ming-os" / "ming-phone-desktop.log"
ACTION_LOG_PATH = HOME / ".cache" / "ming-os" / "status-actions.log"
NOTIFICATIONS_HELPER_PATHS = [
    Path("/usr/local/lib/ming-os/ming-notifications.py"),
    Path("/usr/local/bin/ming-notifications"),
]
DEVICE_CONTROL_PATHS = [
    Path("/usr/local/lib/ming-os/ming-device-control.py"),
    Path("/usr/local/bin/ming-device-control"),
    Path(__file__).resolve().with_name("ming-device-control.py"),
]
NOTIFICATION_LOG_PATHS = [
    HOME / ".cache" / "xfce4" / "notifyd" / "log.sqlite",
    HOME / ".cache" / "xfce4" / "notifyd" / "log",
    HOME / ".cache" / "xfce4" / "notifyd" / "log.xml",
]
LAYOUT_VERSION = 6
GRID_W = 92
GRID_H = 108
PAD_X = 34
PAD_Y = 92
DROP_DISTANCE = 50
ICON_SIZE = 34
TILE_W = 82
TILE_H = 96
LABEL_W = 68
LABEL_H = 32
DRAG_THRESHOLD = 12
ACTIVATION_DEDUP_MS = 650
LAUNCH_FEEDBACK_TIMEOUT_MS = 4000
CLOCK_MARGIN_X = 26
CLOCK_MARGIN_Y = 20
WALLPAPER_PATHS = [
    Path("/usr/share/backgrounds/ming-os/default.png"),
    Path("/usr/share/backgrounds/ming-os/default-1366x768.png"),
    Path("/usr/share/backgrounds/ming-os/default.svg"),
]

CSS = b"""
window.ming-desktop {
  background-color: #EFF7F2;
}
.tile {
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.34);
  border: 1px solid rgba(255, 255, 255, 0.54);
  box-shadow: 0 8px 22px rgba(21, 68, 56, 0.08), inset 0 1px 0 rgba(255,255,255,0.58);
  padding: 7px 6px 6px;
  color: #1D2421;
}
.tile:hover, .tile.dragging {
  background: rgba(255, 255, 255, 0.60);
  border-color: rgba(47, 138, 125, 0.24);
  box-shadow: 0 12px 28px rgba(21, 68, 56, 0.13), inset 0 1px 0 rgba(255,255,255,0.70);
}
.folder {
  background: rgba(232, 248, 242, 0.64);
  border-color: rgba(47, 138, 125, 0.24);
}
.label {
  color: #1D2421;
  font-size: 10.5px;
  font-weight: 700;
  text-shadow: 0 1px 0 rgba(255,255,255,0.82);
}
.folder-title {
  color: #1D2421;
  font-size: 18px;
  font-weight: 700;
}
.folder-panel {
  background: rgba(251, 253, 251, 0.98);
  border: 1px solid rgba(31, 98, 84, 0.10);
  border-radius: 12px;
  padding: 18px;
}
.folder-action {
  border-radius: 9px;
  padding: 7px 10px;
}
.clock-widget {
  border-radius: 14px;
  padding: 9px 13px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.70);
  box-shadow: 0 12px 34px rgba(21, 68, 56, 0.12), inset 0 1px 0 rgba(255,255,255,0.75);
}
.clock-time {
  font-size: 26px;
  font-weight: 800;
  color: #17231F;
}
.clock-date {
  font-size: 11.5px;
  font-weight: 700;
  color: #2D695C;
}
.clock-subdate {
  font-size: 10px;
  font-weight: 700;
  color: #6A7670;
}
.status-widget {
  border-radius: 14px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.78);
  box-shadow: 0 12px 34px rgba(21, 68, 56, 0.12), inset 0 1px 0 rgba(255,255,255,0.78);
}
.status-widget-compact {
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
}
.status-compact-pill {
  min-height: 54px;
  border-radius: 27px;
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 26px rgba(21, 68, 56, 0.14), inset 0 1px 0 rgba(255,255,255,0.84);
  color: #17231F;
}
.status-compact-pill:hover { background: rgba(255, 255, 255, 0.96); }
.status-compact-time { font-size: 19px; font-weight: 800; color: #17231F; }
.status-compact-date { font-size: 10.5px; font-weight: 700; color: #2D695C; }
.status-compact-arrow { font-size: 15px; font-weight: 800; color: #2F8A7D; }
.status-button {
  border-radius: 9px;
  padding: 4px 7px;
  background: rgba(255, 255, 255, 0.54);
  border: 1px solid rgba(47, 138, 125, 0.10);
  color: #21302A;
}
.status-button:hover { background: rgba(255, 255, 255, 0.88); }
.status-scale trough { min-height: 5px; border-radius: 3px; }
.status-scale highlight { background: #2F8A7D; border-radius: 3px; }
.notification-panel { padding: 12px; background: #F9FCFA; }
.notification-title { font-weight: 800; color: #17231F; }
.notification-body { color: #596760; font-size: 10px; }
.launch-feedback {
  border-radius: 14px;
  padding: 14px 18px;
  background: rgba(252, 254, 252, 0.94);
  border: 1px solid rgba(47, 138, 125, 0.16);
  box-shadow: 0 14px 36px rgba(21, 68, 56, 0.16);
}
.launch-title { color: #17231F; font-size: 14px; font-weight: 800; }
.launch-detail { color: #5B6963; font-size: 10.5px; }
"""


def log(msg):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(datetime.datetime.now().strftime("[%F %T] ") + msg + "\n")
    except Exception:
        pass


def load_notifications_helper():
    for path in NOTIFICATIONS_HELPER_PATHS:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("ming_notifications", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            log(f"notification helper load failed: {exc}")
    return None


def load_device_control():
    """Load the backend that owns nmcli, bluetoothctl and upower probes."""
    for path in DEVICE_CONTROL_PATHS:
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("ming_device_control", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            log(f"device control helper load failed: {exc}")
    return None


class InteractionState:
    """Classify one pointer sequence without depending on GTK event objects."""

    def __init__(self, drag_threshold=None, mouse_suppress_ms=650):
        self.drag_threshold = DRAG_THRESHOLD if drag_threshold is None else drag_threshold
        self.mouse_suppress_ms = mouse_suppress_ms
        self.active = False
        self.kind = None
        self.start_x = 0
        self.start_y = 0
        self.moved = False
        self.suppress_mouse_until = 0

    def begin(self, kind, x, y, timestamp):
        if self.active:
            return False
        self.active = True
        self.kind = kind
        self.start_x = x
        self.start_y = y
        self.moved = False
        return True

    def update(self, x, y):
        if not self.active:
            return False
        dx = x - self.start_x
        dy = y - self.start_y
        if dx * dx + dy * dy >= self.drag_threshold * self.drag_threshold:
            self.moved = True
        return self.moved

    def finish(self, x, y, timestamp):
        if not self.active:
            return None
        self.update(x, y)
        result = "drag" if self.moved else "activate"
        if self.kind == "touch":
            self.suppress_mouse_until = timestamp + self.mouse_suppress_ms
        self.active = False
        self.kind = None
        self.moved = False
        return result

    def cancel(self):
        self.active = False
        self.kind = None
        self.moved = False

    def should_ignore_mouse(self, timestamp):
        return timestamp < self.suppress_mouse_until


def app_id(path):
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]


def read_app(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]
    if entry.get("Type", "Application") != "Application":
        return None
    if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
        return None
    exec_line = entry.get("Exec", "")
    if not exec_line:
        return None
    return {
        "id": app_id(path),
        "type": "app",
        "path": str(path),
        "basename": Path(path).name,
        "name": entry.get("Name[zh_CN]") or entry.get("Name") or Path(path).stem,
        "icon": entry.get("Icon") or "application-x-executable",
        "categories": entry.get("Categories", ""),
    }


def launch_item(item, source_rect=None):
    path = item.get("path")
    if not path:
        return False
    if COMMON.send_launch_request(path, "desktop", source_rect):
        return True
    log(f"launch broker unavailable; using direct fallback for {path}")
    try:
        info = Gio.DesktopAppInfo.new_from_filename(path)
        if info and info.launch([], None):
            return True
    except Exception:
        pass
    try:
        subprocess.Popen(["gtk-launch", Path(path).stem])
        return True
    except Exception:
        pass
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(path, encoding="utf-8")
        exec_line = parser["Desktop Entry"].get("Exec", "")
        argv = [part for part in shlex.split(exec_line) if not part.startswith("%")]
        if argv:
            subprocess.Popen(argv)
            return True
    except Exception as exc:
        log(f"exec fallback failed for {path}: {exc}")
    return False


def write_generated_core_launcher(basename):
    data = CORE_GENERATED.get(basename)
    if not data:
        return None
    name, exec_cmd, icon, categories = data
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    path = DESKTOP_DIR / basename
    if not path.exists():
        path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Name[zh_CN]={name}\n"
            f"Exec={exec_cmd}\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            f"Categories={categories}\n"
            "StartupNotify=true\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
    return path


def add_app_from_path(apps_by_basename, path, default_only=False):
    item = read_app(path)
    if not item:
        return False
    basename = item["basename"]
    if default_only and basename not in CORE_NAMES:
        return False
    if basename in apps_by_basename:
        return False
    apps_by_basename[basename] = item
    return True


def add_core_app(apps_by_basename, basename):
    candidates = [DESKTOP_DIR / basename, Path("/usr/share/applications") / basename]
    candidates.extend(Path("/usr/share/applications") / alt for alt in CORE_FALLBACKS.get(basename, []))
    for candidate in candidates:
        if add_app_from_path(apps_by_basename, candidate):
            return True
    generated = write_generated_core_launcher(basename)
    if generated:
        return add_app_from_path(apps_by_basename, generated)
    return False


def load_apps(default_only=False):
    apps_by_basename = {}
    if default_only:
        for basename in sorted(CORE_NAMES, key=lambda name: DESKTOP_ORDER.get(name, 999)):
            add_core_app(apps_by_basename, basename)
    for directory in APP_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            add_app_from_path(apps_by_basename, path, default_only=default_only)
    apps = list(apps_by_basename.values())
    apps.sort(key=lambda item: (DESKTOP_ORDER.get(item["basename"], 999), item["name"].lower()))
    return apps


def empty_layout():
    return {"version": LAYOUT_VERSION, "items": []}


def layout_is_valid(layout, require_items=False):
    if not isinstance(layout, dict) or not isinstance(layout.get("items"), list):
        return False
    if require_items and not layout["items"]:
        return False
    return all(isinstance(item, dict) and item.get("id") for item in layout["items"])


def read_layout(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_layout():
    data = read_layout(LAYOUT_PATH)
    if layout_is_valid(data):
        return data
    last_good = read_layout(LAST_GOOD_LAYOUT_PATH)
    if layout_is_valid(last_good, require_items=True):
        log("primary layout invalid; restoring last known-good layout")
        return last_good
    return empty_layout()


def save_layout(layout):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LAYOUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LAYOUT_PATH)
    if layout_is_valid(layout, require_items=True):
        backup = LAST_GOOD_LAYOUT_PATH.with_suffix(".tmp")
        backup.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        backup.replace(LAST_GOOD_LAYOUT_PATH)


def next_position(index, width=1366):
    cols = min(6, max(3, int((width - PAD_X * 2) / GRID_W)))
    row = index // cols
    col = index % cols
    return PAD_X + col * GRID_W, PAD_Y + row * GRID_H


def sync_layout(width=1366):
    apps = load_apps(default_only=True)
    layout = load_layout()
    if not apps and layout_is_valid(layout, require_items=True):
        log("app discovery was transiently empty; keeping last known-good layout")
        return layout
    if layout.get("version") != LAYOUT_VERSION:
        layout = empty_layout()
    items = []
    known = set()
    for item in layout.get("items", []):
        if item.get("type") == "folder":
            if item.get("pinned"):
                items.append(item)
                known.update(item.get("children", []))
        elif item.get("path"):
            basename = Path(item["path"]).name
            if basename in CORE_NAMES or item.get("pinned"):
                items.append(item)
                known.add(item["path"])
    index = len(items)
    for app in apps:
        if app["path"] in known:
            continue
        app["x"], app["y"] = next_position(index, width)
        app["pinned"] = False
        items.append(app)
        index += 1
    layout["version"] = LAYOUT_VERSION
    layout["items"] = items
    if items:
        save_layout(layout)
        sync_files(layout)
    return layout


def safe_name(name):
    cleaned = "".join("-" if ch in '/\\:*?"<>|' else ch for ch in name).strip()
    return cleaned or "应用"


def copy_desktop(path, target_dir, name=None, preserve_basename=False):
    src = Path(path)
    if not src.is_file():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = src.name if preserve_basename else f"{safe_name(name or src.stem)}.desktop"
    target = target_dir / target_name
    try:
        if src.resolve() == target.resolve():
            target.chmod(0o755)
            return target
        shutil.copy2(src, target)
        target.chmod(0o755)
        return target
    except Exception:
        return None


def sync_files(layout):
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    for retired_name in ("ming-app-library.desktop", "ming-disk-hub.desktop", "Ming 应用库.desktop", "所有磁盘.desktop"):
        retired = DESKTOP_DIR / retired_name
        try:
            retired.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            log(f"could not remove retired desktop launcher: {retired}")
    folders_seen = set()
    launchers_seen = set()
    allowed_dirs = {"Apps", "System", "Internet", "Office", "Media", "Games", "Tools", "Common"}
    for item in layout.get("items", []):
        if item.get("type") == "folder":
            folder_dir = DESKTOP_DIR / safe_name(item.get("name", "folder"))
            folders_seen.add(folder_dir)
            folder_dir.mkdir(parents=True, exist_ok=True)
            for child_path in item.get("children", []):
                child = read_app(child_path)
                if child:
                    copy_desktop(child_path, folder_dir, child["name"])
        elif item.get("path") and (Path(item["path"]).name in CORE_NAMES or item.get("pinned")):
            is_core = Path(item["path"]).name in CORE_NAMES
            copied = copy_desktop(item["path"], DESKTOP_DIR, item.get("name"), preserve_basename=is_core)
            if copied:
                launchers_seen.add(copied)
    for old in DESKTOP_DIR.glob("*.desktop"):
        if old not in launchers_seen:
            try:
                old.unlink()
            except Exception:
                pass
    for old in DESKTOP_DIR.iterdir() if DESKTOP_DIR.exists() else []:
        if old.is_dir() and old.name not in allowed_dirs and old not in folders_seen:
            try:
                if not any(old.iterdir()):
                    old.rmdir()
            except Exception:
                pass


def command_add(path, folder=False):
    item = read_app(path)
    if not item:
        return 1
    layout = sync_layout()
    items = layout["items"]
    if any(x.get("path") == item["path"] for x in items):
        return 0
    if folder:
        folder_item = next((x for x in items if x.get("type") == "folder"), None)
        if not folder_item:
            folder_item = {"id": "folder-" + app_id(item["path"]), "type": "folder", "name": "文件夹", "children": [], "x": PAD_X, "y": PAD_Y, "pinned": True}
            items.insert(0, folder_item)
        folder_item["pinned"] = True
        if item["path"] not in folder_item["children"]:
            folder_item["children"].append(item["path"])
    else:
        item["x"], item["y"] = next_position(len(items))
        item["pinned"] = True
        items.append(item)
    save_layout(layout)
    sync_files(layout)
    return 0


class DesktopTile(Gtk.EventBox):
    def __init__(self, desktop, item):
        super().__init__()
        self.desktop = desktop
        self.item = item
        self.dragging = False
        self.interaction = InteractionState()
        self.offset = (0, 0)
        self.set_size_request(TILE_W, TILE_H)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_release)
        self.connect("touch-event", self.on_touch)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(78, 92)
        box.get_style_context().add_class("tile")
        if item.get("type") == "folder":
            box.get_style_context().add_class("folder")
            image = Gtk.Image.new_from_icon_name("folder", Gtk.IconSize.DIALOG)
        else:
            image = Gtk.Image.new_from_icon_name(item.get("icon") or "application-x-executable", Gtk.IconSize.DIALOG)
        image.set_pixel_size(ICON_SIZE)
        label = Gtk.Label(label=item.get("name", "应用"))
        label.get_style_context().add_class("label")
        label.set_justify(Gtk.Justification.CENTER)
        label.set_size_request(LABEL_W, LABEL_H)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(2)
        label.set_max_width_chars(7)
        box.pack_start(image, True, True, 0)
        box.pack_start(label, False, False, 0)
        self.box = box
        self.add(box)
        self.show_all()
        # GTK child rendering inside a DESKTOP window is not reliable on the
        # VirtualBox/Xrender baseline.  The parent Cairo canvas is therefore
        # the only visual source; this EventBox remains the precise input
        # target for mouse and touch interaction.
        self.set_opacity(0.0)

    def on_press(self, _widget, event):
        if event.button == 3:
            self.desktop.show_context_menu(self.item, event)
            return True
        if event.button != 1:
            return False
        if self.interaction.should_ignore_mouse(getattr(event, "time", 0)):
            return True
        if not self.interaction.begin("mouse", event.x, event.y, getattr(event, "time", 0)):
            return True
        self.dragging = False
        self.offset = (event.x, event.y)
        return True

    def on_motion(self, _widget, event):
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        if not self.interaction.update(event.x, event.y):
            return True
        self.dragging = True
        self.move_tile(event.x_root, event.y_root)
        return True

    def move_tile(self, root_x, root_y):
        self.box.get_style_context().add_class("dragging")
        win_x, win_y = self.desktop.window_origin
        x = int(root_x - win_x - self.offset[0])
        y = int(root_y - win_y - self.offset[1])
        self.desktop.fixed.move(self, max(8, x), max(8, y))

    def finish_interaction(self, action, root_x, root_y, timestamp=0):
        self.box.get_style_context().remove_class("dragging")
        if action == "drag":
            win_x, win_y = self.desktop.window_origin
            x = int(root_x - win_x - self.offset[0])
            y = int(root_y - win_y - self.offset[1])
            self.desktop.finish_drag(self.item, max(8, x), max(8, y))
        elif action == "activate":
            self.desktop.dispatch_activation(self.item, timestamp)
        self.dragging = False

    def reset_interaction(self):
        self.interaction.cancel()
        self.box.get_style_context().remove_class("dragging")
        self.dragging = False

    def pointer_inside(self, x, y):
        allocation = self.get_allocation()
        return 0 <= x < allocation.width and 0 <= y < allocation.height

    def on_release(self, _widget, event):
        if getattr(event, "button", 0) != 1:
            return False
        if self.interaction.should_ignore_mouse(getattr(event, "time", 0)):
            return True
        action = self.interaction.finish(event.x, event.y, getattr(event, "time", 0))
        if action == "activate" and not self.pointer_inside(event.x, event.y):
            action = None
        self.finish_interaction(action, event.x_root, event.y_root, getattr(event, "time", 0))
        return True

    def on_touch(self, _widget, event):
        event_type = event.type
        timestamp = getattr(event, "time", 0)
        if event_type == Gdk.EventType.TOUCH_BEGIN:
            if not self.desktop.begin_touch(self, event):
                self.reset_interaction()
                return True
            if not self.interaction.begin("touch", event.x, event.y, timestamp):
                self.interaction.cancel()
                return True
            self.offset = (event.x, event.y)
            return True
        if event_type == Gdk.EventType.TOUCH_UPDATE:
            if not self.desktop.update_touch(self, event):
                self.reset_interaction()
                return True
            if self.interaction.update(event.x, event.y):
                self.dragging = True
                self.move_tile(event.x_root, event.y_root)
            return True
        if event_type == Gdk.EventType.TOUCH_END:
            if not self.desktop.end_touch(self, event):
                self.reset_interaction()
                return True
            action = self.interaction.finish(event.x, event.y, timestamp)
            if action == "activate" and not self.pointer_inside(event.x, event.y):
                action = None
            self.finish_interaction(action, event.x_root, event.y_root, timestamp)
            return True
        if event_type == Gdk.EventType.TOUCH_CANCEL:
            self.desktop.cancel_touch(self, event)
            self.reset_interaction()
            return True
        return False


def command_text(args, fallback=""):
    try:
        return subprocess.check_output(
            args,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip() or fallback
    except Exception:
        return fallback


def window_is_ready(item):
    """Return true when wmctrl can see a likely window for the launcher."""
    try:
        windows = subprocess.check_output(
            ["wmctrl", "-lx"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).lower()
    except Exception:
        return False
    basename = Path(item.get("path", "")).stem.lower()
    name = item.get("name", "").lower()
    tokens = {
        basename,
        basename.removeprefix("ming-"),
        name,
        name.split()[0] if name else "",
    }
    aliases = {
        "ming-edge": "microsoft-edge",
        "spark-store": "spark-store",
        "ming-files": "thunar",
        "ming-terminal": "xfce4-terminal",
        "ming-settings": "ming-settings",
    }
    tokens.add(aliases.get(basename, ""))
    return any(len(token) >= 3 and token in windows for token in tokens)


class LaunchFeedbackOverlay(Gtk.EventBox):
    def __init__(self):
        super().__init__()
        self.item = None
        self.started_at = 0.0
        self.generation = 0
        self.probe_running = False
        self.probe_generation = 0
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.get_style_context().add_class("launch-feedback")
        self.icon = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(34)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.title = Gtk.Label(label="正在打开")
        self.title.set_halign(Gtk.Align.START)
        self.title.get_style_context().add_class("launch-title")
        self.detail = Gtk.Label(label="请稍候…")
        self.detail.set_halign(Gtk.Align.START)
        self.detail.set_line_wrap(True)
        self.detail.get_style_context().add_class("launch-detail")
        self.spinner = Gtk.Spinner()
        box.pack_start(self.icon, False, False, 0)
        text_box.pack_start(self.title, False, False, 0)
        text_box.pack_start(self.detail, False, False, 0)
        box.pack_start(text_box, True, True, 0)
        box.pack_start(self.spinner, False, False, 0)
        self.add(box)
        self.hide()

    def begin(self, item):
        self.generation += 1
        generation = self.generation
        self.item = item
        self.started_at = time.monotonic()
        self.icon.set_from_icon_name(item.get("icon") or "application-x-executable", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(34)
        self.title.set_text("正在打开 %s" % item.get("name", "应用"))
        self.detail.set_text("正在准备应用窗口…")
        self.spinner.start()
        self.show_all()
        GLib.timeout_add(120, self.poll, generation)

    def poll(self, generation):
        if generation != self.generation or not self.item:
            return False
        elapsed_ms = int((time.monotonic() - self.started_at) * 1000)
        if elapsed_ms >= LAUNCH_FEEDBACK_TIMEOUT_MS:
            self.detail.set_text("启动时间较长，应用会继续在后台打开")
            self.spinner.stop()
            GLib.timeout_add(1100, self.finish, generation)
            return False
        if not self.probe_running:
            self.start_window_probe(generation)
        return True

    def start_window_probe(self, generation):
        self.probe_running = True
        self.probe_generation = generation
        item = dict(self.item or {})
        threading.Thread(target=self.check_window_ready, args=(generation, item), daemon=True).start()

    def check_window_ready(self, generation, item):
        ready = window_is_ready(item)
        GLib.idle_add(self.apply_window_probe, generation, ready)

    def apply_window_probe(self, generation, ready):
        if generation != self.probe_generation:
            return False
        self.probe_running = False
        if generation == self.generation and self.item and ready:
            self.finish(generation)
        return False

    def finish(self, generation=None):
        if generation is not None and generation != self.generation:
            return False
        self.spinner.stop()
        self.item = None
        self.hide()
        return False


class StatusWidget(Gtk.EventBox):
    def __init__(self):
        super().__init__()
        self.set_visible_window(False)
        self.collapsed = load_widget_state()["collapsed"]
        self.refreshing = False
        self.notifications = load_notifications_helper()
        device_module = load_device_control()
        self.device_controller = device_module.DeviceController() if device_module else None
        self.volume_timer = None
        self.brightness_timer = None
        self.updating_controls = False
        self.updating_dnd = False
        self.action_starts = {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.get_style_context().add_class("status-widget")
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)
        self.widget_box = box

        self.compact_button = Gtk.Button()
        self.compact_button.get_style_context().add_class("status-compact-pill")
        compact = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.compact_time_label = Gtk.Label()
        self.compact_time_label.get_style_context().add_class("status-compact-time")
        compact.pack_start(self.compact_time_label, False, False, 0)
        compact.pack_start(Gtk.Label(label="|"), False, False, 0)
        self.compact_date_label = Gtk.Label()
        self.compact_date_label.get_style_context().add_class("status-compact-date")
        compact.pack_start(self.compact_date_label, False, False, 0)
        compact.pack_start(Gtk.Label(label="|"), False, False, 0)
        self.compact_arrow_label = Gtk.Label(label="展开 ▾")
        self.compact_arrow_label.get_style_context().add_class("status-compact-arrow")
        compact.pack_start(self.compact_arrow_label, False, False, 0)
        self.compact_button.add(compact)
        self.compact_button.connect("clicked", lambda _button: self.set_collapsed(False))

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.time_label = Gtk.Label()
        self.time_label.get_style_context().add_class("clock-time")
        self.time_label.set_halign(Gtk.Align.START)
        header.pack_start(self.time_label, True, True, 0)

        self.date_label = Gtk.Label()
        self.date_label.get_style_context().add_class("clock-date")
        self.date_label.set_halign(Gtk.Align.END)
        header.pack_start(self.date_label, False, False, 0)
        self.collapse_button = Gtk.Button(label="收起 ▴")
        self.collapse_button.get_style_context().add_class("status-button")
        self.collapse_button.connect("clicked", lambda _button: self.set_collapsed(True))
        header.pack_start(self.collapse_button, False, False, 0)

        actions = Gtk.Grid()
        actions.set_column_spacing(6)
        actions.set_row_spacing(6)
        actions.set_column_homogeneous(True)
        self.action_commands = {}
        self.wifi_button = self.action_button("Wi-Fi --", "ming-control-center")
        self.action_commands[self.wifi_button] = ["ming-control-center", "--page", "network"]
        self.bluetooth_button = self.action_button("蓝牙 --", "ming-control-center")
        self.action_commands[self.bluetooth_button] = ["ming-control-center", "--page", "network"]
        self.battery_button = self.action_button("电量 --", "xfce4-power-manager-settings")
        self.notification_button = self.action_button("通知", callback=self.open_notifications)
        self.settings_button = self.action_button("设置", "ming-control-center")
        self.action_commands[self.settings_button] = ["ming-control-center", "--page", "advanced"]
        self.power_button = self.action_button("电源", callback=self.open_power_menu)
        self.wifi_label = self.wifi_button.ming_label
        self.bluetooth_label = self.bluetooth_button.ming_label
        self.battery_label = self.battery_button.ming_label
        self.notification_label = self.notification_button.ming_label
        self.settings_label = self.settings_button.ming_label
        self.power_label = self.power_button.ming_label
        self.battery_button.set_no_show_all(True)
        actions.attach(self.wifi_button, 0, 0, 1, 1)
        actions.attach(self.bluetooth_button, 1, 0, 1, 1)
        actions.attach(self.battery_button, 0, 1, 1, 1)
        actions.attach(self.notification_button, 1, 1, 1, 1)
        actions.attach(self.settings_button, 0, 2, 1, 1)
        actions.attach(self.power_button, 1, 2, 1, 1)

        controls = Gtk.Grid()
        controls.set_column_spacing(8)
        controls.set_row_spacing(4)
        self.volume_label = Gtk.Label(label="音量")
        self.volume_label.set_halign(Gtk.Align.START)
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.get_style_context().add_class("status-scale")
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        self.brightness_label = Gtk.Label(label="亮度")
        self.brightness_label.set_halign(Gtk.Align.START)
        self.brightness_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
        self.brightness_scale.set_draw_value(False)
        self.brightness_scale.set_hexpand(True)
        self.brightness_scale.get_style_context().add_class("status-scale")
        self.brightness_scale.connect("value-changed", self.on_brightness_changed)
        self.audio_button = self.action_button(
            "声音", ["ming-control-center", "--page", "advanced"])
        self.display_button = self.action_button(
            "显示", ["ming-control-center", "--page", "display"])
        controls.attach(self.volume_label, 0, 0, 2, 1)
        controls.attach(self.audio_button, 2, 0, 1, 1)
        controls.attach(self.volume_scale, 0, 1, 3, 1)
        controls.attach(self.brightness_label, 0, 2, 2, 1)
        controls.attach(self.display_button, 2, 2, 1, 1)
        controls.attach(self.brightness_scale, 0, 3, 3, 1)

        expanded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        expanded.pack_start(header, False, False, 0)
        expanded.pack_start(controls, False, False, 0)
        expanded.pack_start(actions, False, False, 0)
        self.content_revealer = Gtk.Revealer()
        self.content_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.content_revealer.set_transition_duration(180)
        self.content_revealer.add(expanded)
        box.pack_start(self.compact_button, False, False, 0)
        box.pack_start(self.content_revealer, False, False, 0)
        self.add(box)
        self.apply_collapsed_state()
        self.refresh()
        GLib.timeout_add_seconds(15, self.refresh)

    def preferred_height(self):
        return 54 if self.collapsed else 286

    def set_collapsed(self, collapsed):
        self.collapsed = bool(collapsed)
        try:
            save_widget_state(self.collapsed)
        except OSError as exc:
            log("could not save status widget state: %s" % exc)
        self.apply_collapsed_state()

    def apply_collapsed_state(self):
        style = self.widget_box.get_style_context()
        if self.collapsed:
            style.add_class("status-widget-compact")
        else:
            style.remove_class("status-widget-compact")
        self.compact_button.set_visible(self.collapsed)
        self.content_revealer.set_reveal_child(not self.collapsed)
        self.set_size_request(-1, self.preferred_height())
        desktop = self.get_toplevel()
        if hasattr(desktop, "place_overlays"):
            desktop.place_overlays()

    def action_button(self, label, command=None, callback=None):
        button = Gtk.Button()
        button.set_hexpand(True)
        button.get_style_context().add_class("status-button")
        child = Gtk.Label(label=label)
        child.set_ellipsize(Pango.EllipsizeMode.END)
        child.set_max_width_chars(13)
        button.add(child)
        button.ming_label = child
        if callback:
            button.connect("clicked", callback)
        else:
            button.connect(
                "clicked",
                lambda clicked: self.open_command(
                    self.action_commands.get(clicked, command)))
        return button

    def open_command(self, command):
        argv = list(command) if isinstance(command, (list, tuple)) else [command]
        key = tuple(argv)
        now = time.monotonic()
        previous = self.action_starts.get(key, 0.0)
        if now - previous < ACTIVATION_DEDUP_MS / 1000.0:
            log("status action deduplicated command=%s" % argv)
            return
        self.action_starts[key] = now
        error_log = None
        try:
            ACTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            error_log = ACTION_LOG_PATH.open("a", encoding="utf-8")
            error_log.write("\n[%s] launching %s\n" % (
                datetime.datetime.now().strftime("%F %T"), shlex.join(argv)))
            error_log.flush()
            process = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
            )
            threading.Thread(
                target=self.monitor_action_process,
                args=(process, argv, error_log),
                daemon=True,
            ).start()
        except Exception as exc:
            if error_log:
                error_log.close()
            log("status action failed %s: %s" % (argv, exc))
            self.notify_action_failure()

    def monitor_action_process(self, process, argv, error_log):
        try:
            process.wait(timeout=1.5)
            if process.returncode != 0:
                log("status action exited rc=%s command=%s log=%s" % (
                    process.returncode, argv, ACTION_LOG_PATH))
                GLib.idle_add(self.notify_action_failure)
        except subprocess.TimeoutExpired:
            log("status action remains active command=%s" % argv)
        except Exception as exc:
            log("status action monitor failed %s: %s" % (argv, exc))
        finally:
            error_log.close()

    @staticmethod
    def notify_action_failure():
        try:
            subprocess.Popen(
                ["notify-send", "Ming OS", "设置入口启动失败，日志：status-actions.log"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        return False

    def schedule_control(self, kind, value):
        attr = "%s_timer" % kind
        timer = getattr(self, attr)
        if timer:
            GLib.source_remove(timer)

        def apply_value():
            setattr(self, attr, None)
            threading.Thread(target=self.set_control_value, args=(kind, value), daemon=True).start()
            return False

        setattr(self, attr, GLib.timeout_add(120, apply_value))

    def on_volume_changed(self, control):
        if not self.updating_controls:
            self.schedule_control("volume", int(round(control.get_value())))

    def on_brightness_changed(self, control):
        if not self.updating_controls:
            self.schedule_control("brightness", int(round(control.get_value())))

    def set_control_value(self, kind, value):
        try:
            if not self.device_controller:
                result = {"ok": False, "error": "设备控制服务不可用", "value": None}
            elif kind == "volume":
                result = self.device_controller.set_volume(value)
            else:
                result = self.device_controller.set_brightness(value)
            GLib.idle_add(self.apply_control_result, kind, result)
        except Exception as exc:
            log(f"{kind} control failed: {exc}")
            GLib.idle_add(
                self.apply_control_result,
                kind,
                {"ok": False, "error": str(exc), "value": None},
            )

    def apply_control_result(self, kind, result):
        self.updating_controls = True
        value = result.get("value")
        if result.get("ok") and value is not None:
            if kind == "volume":
                self.volume_scale.set_value(value)
                self.volume_label.set_text("音量 %d%%" % value)
            else:
                self.brightness_scale.set_value(value)
                self.brightness_label.set_text("亮度 %d%%" % value)
        else:
            message = result.get("error") or "控制失败"
            log("%s control rejected: %s" % (kind, message))
            if kind == "volume":
                self.volume_label.set_text("未检测到输出设备")
            else:
                self.brightness_label.set_text("当前设备不支持")
        self.updating_controls = False
        return False

    def notification_log_path(self):
        return next((path for path in NOTIFICATION_LOG_PATHS if path.exists()), NOTIFICATION_LOG_PATHS[0])

    def read_notification_items(self):
        if not self.notifications:
            return []
        try:
            path = self.notification_log_path()
            return self.notifications.load_notification_log(path, limit=50)
        except Exception as exc:
            log(f"notification history read failed: {exc}")
            return []

    def open_notifications(self, button):
        popover = Gtk.Popover.new(button)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_size_request(320, 330)
        panel.get_style_context().add_class("notification-panel")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="最近通知")
        title.set_halign(Gtk.Align.START)
        title.get_style_context().add_class("notification-title")
        clear = Gtk.Button(label="清空通知")
        clear.connect("clicked", lambda _button: self.clear_notifications(popover))
        header.pack_start(title, True, True, 0)
        header.pack_start(clear, False, False, 0)
        panel.pack_start(header, False, False, 0)

        dnd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dnd_row.pack_start(Gtk.Label(label="免打扰"), True, True, 0)
        dnd = Gtk.Switch()
        current = command_text(["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"], "false")
        dnd.set_active(current.strip().lower() == "true")
        dnd.connect("notify::active", self.on_dnd_changed)
        dnd_row.pack_start(dnd, False, False, 0)
        panel.pack_start(dnd_row, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        items = list(reversed(self.read_notification_items()[-50:]))
        if not items:
            empty = Gtk.Label(label="暂无通知")
            empty.set_margin_top(32)
            history.pack_start(empty, False, False, 0)
        for item in items:
            summary = Gtk.Label(label=getattr(item, "summary", "通知") or "通知")
            summary.set_halign(Gtk.Align.START)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            summary.get_style_context().add_class("notification-title")
            meta_text = " · ".join(
                value for value in (
                    getattr(item, "app_name", ""),
                    str(getattr(item, "timestamp", "")),
                ) if value
            )
            meta = Gtk.Label(label=meta_text)
            meta.set_halign(Gtk.Align.START)
            meta.set_ellipsize(Pango.EllipsizeMode.END)
            meta.get_style_context().add_class("notification-body")
            body = Gtk.Label(label=getattr(item, "body", "") or getattr(item, "app_name", ""))
            body.set_halign(Gtk.Align.START)
            body.set_line_wrap(True)
            body.set_lines(2)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.get_style_context().add_class("notification-body")
            history.pack_start(summary, False, False, 0)
            history.pack_start(meta, False, False, 0)
            history.pack_start(body, False, False, 0)
        scroll.add(history)
        panel.pack_start(scroll, True, True, 0)
        popover.add(panel)
        popover.show_all()

    def clear_notifications(self, popover):
        try:
            if self.notifications:
                self.notifications.clear_notification_log_atomic(self.notification_log_path())
            self.notification_label.set_text("通知")
        except Exception as exc:
            log(f"clear notification history failed: {exc}")
        popover.popdown()

    def on_dnd_changed(self, control, _param):
        if self.updating_dnd:
            return
        enabled = control.get_active()
        argv = ["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb", "-s", str(enabled).lower()]
        if self.notifications:
            try:
                command = self.notifications.dnd_command(enabled)
                argv = list(command.argv)
            except Exception as exc:
                log(f"DND helper failed: {exc}")

        def apply_and_readback():
            try:
                subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
                effective = command_text(
                    ["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"],
                    "false",
                ).strip().lower() == "true"
                GLib.idle_add(self.apply_dnd_readback, control, effective)
            except Exception as exc:
                log(f"DND update failed: {exc}")
                GLib.idle_add(self.apply_dnd_readback, control, not enabled)
        threading.Thread(
            target=apply_and_readback,
            daemon=True,
        ).start()

    def apply_dnd_readback(self, control, effective):
        self.updating_dnd = True
        control.set_active(effective)
        self.updating_dnd = False
        return False

    def open_power_menu(self, _button):
        commands = [
            ["xfce4-session-logout"],
            ["gnome-session-quit", "--logout"],
            ["mate-session-save", "--logout-dialog"],
            ["lxqt-leave"],
        ]
        for command in commands:
            if not shutil.which(command[0]):
                continue
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception as exc:
                log("power menu failed %s: %s" % (command[0], exc))
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="未找到系统电源菜单",
        )
        dialog.format_secondary_text("请从系统菜单注销或管理电源。")
        dialog.run()
        dialog.destroy()

    def refresh(self):
        now = datetime.datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        time_text = now.strftime("%H:%M")
        date_text = "%s %s" % (weekdays[now.weekday()], now.strftime("%m/%d"))
        self.time_label.set_text(time_text)
        self.date_label.set_text(date_text)
        self.compact_time_label.set_text(time_text)
        self.compact_date_label.set_text(date_text)
        if not self.refreshing:
            self.refreshing = True
            threading.Thread(target=self.collect_status, daemon=True).start()
        return True

    def collect_status(self):
        try:
            status = self.device_controller.status() if self.device_controller else {}
        except Exception as exc:
            log(f"device status collection failed: {exc}")
            status = {}
        notification_count = len(self.read_notification_items())
        GLib.idle_add(self.apply_status, status, notification_count)

    def apply_status(self, status, notification_count):
        wifi = status.get("wifi", {})
        wifi_text = {
            "ready": "可用",
            "rfkill_blocked": "已禁用",
            "firmware_missing": "缺固件",
            "driver_missing": "缺驱动",
            "no_hardware": "无设备",
        }.get(wifi.get("state"), "不可用")
        bluetooth = status.get("bluetooth", {})
        battery = status.get("battery", {})
        audio = status.get("audio", {})
        brightness = status.get("brightness", {})
        self.wifi_label.set_text("Wi-Fi %s" % wifi_text)
        self.bluetooth_label.set_text("蓝牙 %s" % bluetooth.get("text", "不可用"))
        self.battery_label.set_text("电量 %s" % battery.get("text", "--"))
        self.battery_button.set_visible(bool(battery.get("available")))
        self.notification_label.set_text(
            "通知 %d" % notification_count if notification_count else "通知")
        self.updating_controls = True
        audio_available = bool(audio.get("available"))
        volume = audio.get("value") if audio_available else 0
        self.volume_scale.set_sensitive(audio_available)
        self.volume_scale.set_value(max(0, min(100, volume or 0)))
        self.volume_label.set_text(
            "音量 %d%%" % volume if audio_available else "未检测到输出设备")
        brightness_available = bool(brightness.get("available"))
        brightness_value = brightness.get("value") if brightness_available else 1
        self.brightness_scale.set_sensitive(brightness_available)
        self.brightness_scale.set_value(max(1, min(100, brightness_value or 1)))
        self.brightness_label.set_text(
            "亮度 %d%%" % brightness_value if brightness_available else "当前设备不支持")
        self.brightness_label.set_visible(True)
        self.brightness_scale.set_visible(True)
        self.display_button.set_visible(True)
        self.updating_controls = False
        self.refreshing = False
        return False


class WallpaperCanvas(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.pixbuf = self.load_wallpaper()
        self.connect("draw", self.on_draw)

    def load_wallpaper(self):
        for path in WALLPAPER_PATHS:
            if path.exists():
                try:
                    return GdkPixbuf.Pixbuf.new_from_file(str(path))
                except Exception:
                    pass
        return None

    def on_draw(self, widget, cr):
        width = max(1, widget.get_allocated_width())
        height = max(1, widget.get_allocated_height())
        if not self.pixbuf:
            cr.set_source_rgb(0.937, 0.969, 0.949)
            cr.rectangle(0, 0, width, height)
            cr.fill()
            return False
        src_w = self.pixbuf.get_width()
        src_h = self.pixbuf.get_height()
        scale = max(width / src_w, height / src_h)
        draw_w = int(src_w * scale)
        draw_h = int(src_h * scale)
        scaled = self.pixbuf.scale_simple(draw_w, draw_h, GdkPixbuf.InterpType.BILINEAR)
        x = int((width - draw_w) / 2)
        y = int((height - draw_h) / 2)
        Gdk.cairo_set_source_pixbuf(cr, scaled, x, y)
        cr.paint()
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        return False


class PhoneDesktop(Gtk.Window):
    def __init__(self):
        super().__init__(title="Ming Desktop")
        try:
            READY_MARKER.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        self.set_name("ming-desktop-window")
        self.get_style_context().add_class("ming-desktop")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        try:
            self.set_keep_below(True)
            self.stick()
        except Exception:
            pass
        screen = self.get_screen()
        screen_w = max(320, screen.get_width())
        screen_h = max(240, screen.get_height())
        self.set_default_size(screen_w, screen_h)
        self.resize(screen_w, screen_h)
        self.move(0, 0)
        self.connect("destroy", Gtk.main_quit)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 700)

        self.wallpaper = WallpaperCanvas()
        self.fixed = Gtk.Fixed()
        self.fixed.set_hexpand(True)
        self.fixed.set_vexpand(True)
        self.fixed.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.fixed.connect("draw", self.draw_background)
        self.fixed.connect("button-press-event", self.on_fixed_button_press)
        self.fixed.connect("motion-notify-event", self.on_fixed_motion)
        self.fixed.connect("button-release-event", self.on_fixed_button_release)
        self.fixed.connect("touch-event", self.on_fixed_touch)
        self.add(self.fixed)
        self.tiles = {}
        self.touch_owner = None
        self.touch_sequence = None
        self.touch_sequences = set()
        self.touch_blocked = False
        self.activation_consumed = {}
        self.fixed_press_item = None
        self.fixed_press_origin = None
        self.fixed_press_moved = False
        self.fixed_touch_item = None
        self.fixed_touch_state = InteractionState()
        self.layer_enforcement_pending = False
        self.status = StatusWidget()
        self.launch_feedback = LaunchFeedbackOverlay()
        self.launch_feedback.set_sensitive(False)
        self.connect("map-event", lambda *_args: self.enforce_desktop_layer())
        self.connect("size-allocate", lambda *_args: self.place_overlays())
        self.layout = sync_layout(screen_w)
        self.layout_stamp = self.current_layout_stamp()
        self.render()
        GLib.timeout_add_seconds(2, self.mark_ready)
        GLib.timeout_add_seconds(3, self.refresh_if_apps_changed)

    @property
    def window_origin(self):
        window = self.get_window()
        if window:
            try:
                ok, x, y = window.get_origin()
                if ok:
                    return x, y
            except Exception:
                pass
        return 0, 0

    def mark_ready(self):
        try:
            READY_MARKER.parent.mkdir(parents=True, exist_ok=True)
            READY_MARKER.write_text(datetime.datetime.now().isoformat(), encoding="utf-8")
        except Exception:
            pass
        return False

    @staticmethod
    def event_sequence(event):
        try:
            return event.get_event_sequence()
        except Exception:
            return getattr(event, "sequence", None)

    def begin_touch(self, tile, event):
        sequence = self.event_sequence(event)
        self.touch_sequences.add(sequence)
        if self.touch_owner is None and not self.touch_blocked:
            self.touch_owner = tile
            self.touch_sequence = sequence
            return True
        if self.touch_owner is tile and self.touch_sequence == sequence and not self.touch_blocked:
            return True
        if self.touch_owner is not None:
            self.touch_owner.reset_interaction()
        self.touch_blocked = True
        return False

    def update_touch(self, tile, event):
        return (
            not self.touch_blocked
            and self.touch_owner is tile
            and self.touch_sequence == self.event_sequence(event)
        )

    def end_touch(self, tile, event):
        sequence = self.event_sequence(event)
        allowed = self.update_touch(tile, event)
        self.touch_sequences.discard(sequence)
        if not self.touch_sequences:
            self.touch_owner = None
            self.touch_sequence = None
            self.touch_blocked = False
        return allowed

    def cancel_touch(self, tile, event):
        sequence = self.event_sequence(event)
        self.touch_sequences.discard(sequence)
        if self.touch_owner is tile:
            self.touch_owner = None
            self.touch_sequence = None
        if not self.touch_sequences:
            self.touch_blocked = False

    def enforce_desktop_layer(self):
        try:
            self.set_keep_below(True)
            self.stick()
        except Exception:
            pass
        if not self.layer_enforcement_pending:
            self.layer_enforcement_pending = True
            threading.Thread(target=self.apply_desktop_layer, daemon=True).start()
        return False

    def apply_desktop_layer(self):
        try:
            subprocess.run(
                ["wmctrl", "-r", "Ming Desktop", "-b", "add,below,sticky,skip_taskbar,skip_pager"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
        except Exception:
            pass
        finally:
            GLib.idle_add(self.finish_layer_enforcement)

    def finish_layer_enforcement(self):
        self.layer_enforcement_pending = False
        return False

    def draw_background(self, widget, cr):
        self.wallpaper.on_draw(widget, cr)
        self.draw_icon_fallback(cr)
        return False

    def rounded_rect(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def draw_icon_fallback(self, cr):
        # Keep a single, renderer-independent visual source.  DesktopTile is
        # intentionally transparent and only owns pointer/touch interaction.
        icon_theme = Gtk.IconTheme.get_default()
        for item in self.layout.get("items", []):
            if item.get("type") != "app":
                continue
            x = int(item.get("x", PAD_X))
            y = int(item.get("y", PAD_Y))
            self.rounded_rect(cr, x, y, TILE_W - 4, TILE_H - 4, 12)
            cr.set_source_rgba(1, 1, 1, 0.48)
            cr.fill_preserve()
            cr.set_source_rgba(0.18, 0.54, 0.49, 0.18)
            cr.set_line_width(1)
            cr.stroke()
            icon_name = item.get("icon") or "application-x-executable"
            try:
                pixbuf = icon_theme.load_icon(icon_name, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                pixbuf = None
            if pixbuf:
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, x + int((TILE_W - ICON_SIZE) / 2), y + 7)
                cr.paint()
            layout = PangoCairo.create_layout(cr)
            layout.set_text(item.get("name", "应用"), -1)
            layout.set_width(LABEL_W * Pango.SCALE)
            layout.set_height(-2)
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_font_description(Pango.FontDescription("Sans Bold 10"))
            cr.set_source_rgba(0.11, 0.15, 0.13, 0.96)
            cr.move_to(x + int((TILE_W - LABEL_W) / 2), y + 49)
            PangoCairo.show_layout(cr, layout)

    def item_at(self, x, y):
        for item in reversed(self.layout.get("items", [])):
            ix = int(item.get("x", PAD_X))
            iy = int(item.get("y", PAD_Y))
            if ix <= x <= ix + TILE_W and iy <= y <= iy + TILE_H:
                return item
        return None

    def fixed_event_coords(self, event):
        x = float(getattr(event, "x", 0))
        y = float(getattr(event, "y", 0))
        event_window = getattr(event, "window", None)
        fixed_window = self.fixed.get_window()
        if not event_window or not fixed_window or event_window == fixed_window:
            return x, y
        try:
            event_origin = event_window.get_origin()
            fixed_origin = fixed_window.get_origin()
            event_x, event_y = event_origin[-2:]
            fixed_x, fixed_y = fixed_origin[-2:]
            return x + event_x - fixed_x, y + event_y - fixed_y
        except Exception:
            return x, y

    def on_fixed_button_press(self, _widget, event):
        if getattr(event, "button", 0) not in (1, 3):
            return False
        x, y = self.fixed_event_coords(event)
        item = self.item_at(x, y)
        self.fixed_press_item = item.get("id") if item else None
        self.fixed_press_origin = (x, y)
        self.fixed_press_moved = False
        return False

    def on_fixed_motion(self, _widget, event):
        if not self.fixed_press_origin:
            return False
        x, y = self.fixed_event_coords(event)
        dx = x - self.fixed_press_origin[0]
        dy = y - self.fixed_press_origin[1]
        if dx * dx + dy * dy >= DRAG_THRESHOLD * DRAG_THRESHOLD:
            self.fixed_press_moved = True
        return False

    def on_fixed_button_release(self, _widget, event):
        x, y = self.fixed_event_coords(event)
        item = self.item_at(x, y)
        press_item = self.fixed_press_item
        moved = self.fixed_press_moved
        self.fixed_press_item = None
        self.fixed_press_origin = None
        self.fixed_press_moved = False
        if not item:
            if getattr(event, "button", 0) == 3:
                self.show_desktop_context_menu(event)
                return True
            return False
        if getattr(event, "button", 0) == 3:
            self.show_context_menu(item, event)
            return True
        if getattr(event, "button", 0) == 1:
            if moved or (press_item is not None and press_item != item.get("id")):
                return False
            return self.dispatch_activation(item, getattr(event, "time", 0))
        return False

    def on_fixed_touch(self, _widget, event):
        event_type = event.type
        x, y = self.fixed_event_coords(event)
        timestamp = getattr(event, "time", 0)
        if event_type == Gdk.EventType.TOUCH_BEGIN:
            item = self.item_at(x, y)
            self.fixed_touch_item = item
            if item:
                self.fixed_touch_state.begin("touch", x, y, timestamp)
            return bool(item)
        if event_type == Gdk.EventType.TOUCH_UPDATE:
            if self.fixed_touch_item:
                self.fixed_touch_state.update(x, y)
                return True
            return False
        if event_type == Gdk.EventType.TOUCH_END:
            item = self.fixed_touch_item
            self.fixed_touch_item = None
            action = self.fixed_touch_state.finish(x, y, timestamp)
            if item and action == "activate" and self.item_at(x, y) is item:
                return self.dispatch_activation(item, timestamp)
            return bool(item)
        if event_type == Gdk.EventType.TOUCH_CANCEL:
            self.fixed_touch_item = None
            self.fixed_touch_state.cancel()
            return True
        return False

    def show_desktop_context_menu(self, event):
        menu = Gtk.Menu()
        actions = (
            ("刷新桌面", self.refresh_desktop),
            ("打开应用抽屉", lambda: subprocess.Popen(["ming-app-library"])),
            ("Ming 设置", lambda: subprocess.Popen(["ming-control-center"])),
            ("终端", lambda: subprocess.Popen(["ming-terminal"])),
        )
        for label, callback in actions:
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda _item, action=callback: action())
            menu.append(entry)
        menu.show_all()
        menu.popup_at_pointer(event)

    def refresh_desktop(self):
        updated = sync_layout(self.get_screen().get_width())
        if layout_is_valid(updated, require_items=True):
            self.layout = updated
            self.layout_stamp = self.current_layout_stamp()
            self.render()

    def dispatch_activation(self, item, event_time=0):
        now = GLib.get_monotonic_time()
        item_key = item.get("id", item.get("path", ""))
        self.activation_consumed = {
            key: value
            for key, value in self.activation_consumed.items()
            if now - value[1] < ACTIVATION_DEDUP_MS * 1000
        }
        previous = self.activation_consumed.get(item_key)
        if previous:
            previous_event_time, previous_stamp = previous
            if event_time == previous_event_time or now - previous_stamp < ACTIVATION_DEDUP_MS * 1000:
                return True
        self.activation_consumed[item_key] = (event_time, now)
        self.open_item(item)
        return True

    def render(self):
        screen_w = max(320, self.get_screen().get_width())
        screen_h = max(240, self.get_screen().get_height())
        self.fixed.set_size_request(screen_w, screen_h)
        for child in self.fixed.get_children():
            self.fixed.remove(child)
        self.tiles = {}
        log(f"render desktop items={len(self.layout.get('items', []))} screen={screen_w}x{screen_h}")
        # The canvas is the single visual layer and the Fixed container owns
        # hit testing.  This avoids invisible GTK EventBoxes consuming clicks
        # on VBox/Xrender while preserving one-click and touch activation.
        self.place_overlays()
        self.show_all()
        if not self.launch_feedback.item:
            self.launch_feedback.hide()
        self.enforce_desktop_layer()

    def place_overlays(self):
        screen_w = max(320, self.get_screen().get_width())
        widget_w = 300 if screen_w >= 900 else 260
        self.status.set_size_request(widget_w, self.status.preferred_height())
        x = max(CLOCK_MARGIN_X, screen_w - widget_w - CLOCK_MARGIN_X)
        y = CLOCK_MARGIN_Y
        if self.status.get_parent() is None:
            self.fixed.put(self.status, x, y)
        else:
            self.fixed.move(self.status, x, y)
        feedback_w = 340 if screen_w >= 900 else 250
        self.launch_feedback.set_size_request(feedback_w, 84)
        feedback_x = max(20, int((screen_w - feedback_w) / 2))
        feedback_y = CLOCK_MARGIN_Y if screen_w >= 760 else CLOCK_MARGIN_Y + 150
        if self.launch_feedback.get_parent() is None:
            self.fixed.put(self.launch_feedback, feedback_x, feedback_y)
        else:
            self.fixed.move(self.launch_feedback, feedback_x, feedback_y)

    @staticmethod
    def current_layout_stamp():
        try:
            return LAYOUT_PATH.stat().st_mtime_ns
        except OSError:
            return 0

    def refresh_if_apps_changed(self):
        stamp = self.current_layout_stamp()
        if stamp == self.layout_stamp:
            return True
        updated = load_layout()
        self.layout_stamp = stamp
        if (layout_is_valid(updated, require_items=True)
                and updated.get("version") == LAYOUT_VERSION and updated != self.layout):
            self.layout = updated
            self.render()
        return True

    def find_drop_target(self, source, x, y):
        best = None
        best_distance = DROP_DISTANCE
        for item in self.layout.get("items", []):
            if item.get("id") == source.get("id"):
                continue
            dx = int(item.get("x", 0)) - x
            dy = int(item.get("y", 0)) - y
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < best_distance:
                best = item
                best_distance = distance
        return best

    def finish_drag(self, item, x, y):
        target = self.find_drop_target(item, x, y)
        if target and item.get("type") == "app":
            self.create_or_merge_folder(item, target)
        else:
            item["x"] = x
            item["y"] = y
        save_layout(self.layout)
        sync_files(self.layout)
        self.render()

    def create_or_merge_folder(self, source, target):
        items = self.layout.get("items", [])
        if target.get("type") == "folder":
            if source.get("path") not in target.setdefault("children", []):
                target["children"].append(source.get("path"))
            items[:] = [x for x in items if x.get("id") != source.get("id")]
            return
        if target.get("type") != "app":
            return
        folder = {
            "id": "folder-" + app_id(source.get("path", "") + target.get("path", "")),
            "type": "folder",
            "name": "文件夹",
            "children": [target.get("path"), source.get("path")],
            "x": target.get("x", PAD_X),
            "y": target.get("y", PAD_Y),
        }
        items[:] = [x for x in items if x.get("id") not in {source.get("id"), target.get("id")}]
        items.append(folder)

    def open_item(self, item):
        if item.get("type") == "folder":
            self.show_folder(item)
            return
        self.launch_feedback.begin(item)
        origin_x, origin_y = self.window_origin
        source_rect = {
            "x": origin_x + int(item.get("x", PAD_X)),
            "y": origin_y + int(item.get("y", PAD_Y)),
            "width": TILE_W,
            "height": TILE_H,
        }
        if not launch_item(item, source_rect):
            self.launch_feedback.finish()
            log(f"no launch method worked for {item.get('path')}")
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="无法打开此应用",
            )
            dialog.format_secondary_text("启动失败，详细信息已写入桌面日志。")
            dialog.run()
            dialog.destroy()

    def show_folder(self, folder):
        dialog = Gtk.Dialog(title=folder.get("name", "文件夹"), transient_for=self, flags=0)
        dialog.set_default_size(520, 420)
        area = dialog.get_content_area()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        panel.get_style_context().add_class("folder-panel")
        panel.set_border_width(16)
        area.add(panel)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Entry()
        title.set_text(folder.get("name", "文件夹"))
        title.get_style_context().add_class("folder-title")
        rename = Gtk.Button(label="改名")
        rename.get_style_context().add_class("folder-action")
        rename.connect("clicked", lambda _b: self.rename_folder(folder, title.get_text(), dialog))
        header.pack_start(title, True, True, 0)
        header.pack_start(rename, False, False, 0)
        panel.pack_start(header, False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_row_spacing(10)
        flow.set_column_spacing(10)
        panel.pack_start(flow, True, True, 0)
        for child_path in list(folder.get("children", [])):
            child = read_app(child_path)
            if not child:
                continue
            button = Gtk.Button()
            button.set_size_request(104, 94)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            child_image = Gtk.Image.new_from_icon_name(child.get("icon"), Gtk.IconSize.DIALOG)
            child_image.set_pixel_size(ICON_SIZE)
            box.pack_start(child_image, True, True, 0)
            label = Gtk.Label(label=child.get("name"))
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_lines(2)
            label.set_max_width_chars(8)
            box.pack_start(label, False, False, 0)
            button.add(box)
            button.connect("clicked", lambda _b, app=child: self.open_item(app))
            button.connect("button-press-event", lambda w, e, app=child, f=folder, d=dialog: self.child_menu(w, e, app, f, d))
            flow.add(button)
        close = Gtk.Button(label="关闭")
        close.connect("clicked", lambda _b: dialog.destroy())
        panel.pack_start(close, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def child_menu(self, widget, event, app, folder, dialog):
        if getattr(event, "button", 0) != 3:
            return False
        menu = Gtk.Menu()
        move = Gtk.MenuItem(label="移到桌面")
        move.connect("activate", lambda _i: self.move_child_to_desktop(app, folder, dialog))
        menu.append(move)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def rename_folder(self, folder, name, dialog):
        folder["name"] = safe_name(name)
        save_layout(self.layout)
        sync_files(self.layout)
        dialog.destroy()
        self.render()

    def move_child_to_desktop(self, app, folder, dialog):
        folder["children"] = [x for x in folder.get("children", []) if x != app.get("path")]
        app["x"], app["y"] = next_position(len(self.layout.get("items", [])), self.get_screen().get_width())
        self.layout["items"].append(app)
        if not folder["children"]:
            self.layout["items"] = [x for x in self.layout["items"] if x.get("id") != folder.get("id")]
        save_layout(self.layout)
        sync_files(self.layout)
        dialog.destroy()
        self.render()

    def show_context_menu(self, item, event):
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="打开")
        open_item.connect("activate", lambda _i: self.open_item(item))
        menu.append(open_item)
        if item.get("type") == "folder":
            rename = Gtk.MenuItem(label="重命名文件夹")
            rename.connect("activate", lambda _i: self.show_folder(item))
            menu.append(rename)
            if not item.get("children"):
                delete = Gtk.MenuItem(label="删除空文件夹")
                delete.connect("activate", lambda _i: self.delete_item(item))
                menu.append(delete)
        else:
            remove = Gtk.MenuItem(label="从桌面移除")
            remove.connect("activate", lambda _i: self.delete_item(item))
            menu.append(remove)
        menu.show_all()
        menu.popup_at_pointer(event)

    def delete_item(self, item):
        self.layout["items"] = [x for x in self.layout.get("items", []) if x.get("id") != item.get("id")]
        save_layout(self.layout)
        sync_files(self.layout)
        self.render()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--add":
        raise SystemExit(command_add(sys.argv[2], folder=False))
    if len(sys.argv) >= 3 and sys.argv[1] == "--add-to-folder":
        raise SystemExit(command_add(sys.argv[2], folder=True))
    if len(sys.argv) >= 2 and sys.argv[1] == "--sync":
        sync_layout()
        return
    PhoneDesktop()
    Gtk.main()


if __name__ == "__main__":
    main()
