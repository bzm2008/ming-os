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
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo


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


def resolved_icon_image(icon, pixel_size):
    resolved = COMMON.resolve_icon(icon)
    if Path(resolved).is_absolute():
        image = Gtk.Image.new_from_file(resolved)
    else:
        image = Gtk.Image.new_from_icon_name(resolved, Gtk.IconSize.DIALOG)
    image.set_pixel_size(pixel_size)
    return image

HOME = Path.home()
STATE_DIR = HOME / ".config" / "ming-os"
LAYOUT_PATH = STATE_DIR / "desktop-layout.json"
LAST_GOOD_LAYOUT_PATH = STATE_DIR / "desktop-layout.last-good.json"
DESKTOP_MANIFEST_PATH = STATE_DIR / "desktop-generated-manifest.json"
DESKTOP_MANIFEST_VERSION = 1
DESKTOP_MANAGED_MARKER = "X-Ming-Managed"
DESKTOP_MANAGED_MARKER_LINE = "X-Ming-Managed=true"
READY_MARKER = HOME / ".cache" / "ming-os" / "ming-phone-desktop.ready"
DESKTOP_DIR = HOME / "Desktop"
APP_DIRS = [DESKTOP_DIR, Path("/usr/share/applications"), HOME / ".local/share/applications"]
APP_CATALOG_FINGERPRINT_VERSION = 1
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
LAYOUT_VERSION = 7
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
APPEARANCE_PATH = STATE_DIR / "appearance.json"


def load_appearance(path=None):
    defaults = {"theme": "system", "font_family": "Noto Sans", "font_size": 11,
                "desktop_icon_scale": 1.0, "dock_icon_size": 48, "wallpaper": "default"}
    try:
        data = json.loads(Path(path or APPEARANCE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if isinstance(data, dict):
        defaults.update({key: data[key] for key in defaults if key in data})
    return defaults


def appearance_wallpaper_paths(appearance, fallbacks=None):
    """Prefer a validated copied wallpaper and retain built-in fallbacks."""
    fallbacks = list(fallbacks or WALLPAPER_PATHS)
    wallpaper = str((appearance or {}).get("wallpaper", ""))
    named = {
        "default": Path("/usr/share/backgrounds/ming-os/default.png"),
        "light": Path("/usr/share/backgrounds/ming-os/default-light.png"),
        "dark": Path("/usr/share/backgrounds/ming-os/default-dark.png"),
    }
    if wallpaper in named:
        return [named[wallpaper]] + [path for path in fallbacks if path != named[wallpaper]]
    candidate = Path(wallpaper).expanduser()
    try:
        if candidate.is_absolute() and candidate.is_file() and 0 < candidate.stat().st_size <= 32 * 1024 * 1024:
            with candidate.open("rb") as stream:
                head = stream.read(12)
            if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")) \
                    or (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
                return [candidate] + fallbacks
    except OSError:
        pass
    return fallbacks


def reflow_layout_for_icon_scale(layout, old_scale, new_scale, width, height):
    """Re-grid positions by normalized coordinates without changing item order/content."""
    result = json.loads(json.dumps(layout))
    old_scale = max(0.5, float(old_scale or 1.0))
    new_scale = max(0.5, float(new_scale or 1.0))
    pad_x, pad_y = 34, 92
    old_w, old_h = 92 * old_scale, 108 * old_scale
    new_w, new_h = 92 * new_scale, 108 * new_scale
    max_x = max(pad_x, float(width) - 82 * new_scale - pad_x)
    max_y = max(pad_y, float(height) - 96 * new_scale - pad_x)
    for item in result.get("items", []):
        column = max(0, round((float(item.get("x", pad_x)) - pad_x) / old_w))
        row = max(0, round((float(item.get("y", pad_y)) - pad_y) / old_h))
        item["x"] = int(min(max_x, pad_x + column * new_w))
        item["y"] = int(min(max_y, pad_y + row * new_h))
    result["desktop_icon_scale"] = new_scale
    return result


def appearance_icon_size(appearance):
    try:
        return max(24, min(64, int(round(34 * float(appearance.get("desktop_icon_scale", 1.0))))))
    except (AttributeError, TypeError, ValueError):
        return 34

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
.status-scale trough {
  min-height: 7px;
  border-radius: 4px;
  background: transparent;
  border: 0;
}
.status-scale highlight {
  min-height: 7px;
  border-radius: 4px;
  background: transparent;
}
.status-scale fill,
.status-scale progress {
  min-height: 7px;
  border-radius: 4px;
  background: transparent;
}
.status-scale trough > highlight,
.status-scale trough > fill,
.status-scale trough > progress {
  min-height: 7px;
  border-radius: 4px;
  background: transparent;
}
.status-scale slider {
  min-width: 1px;
  min-height: 1px;
  margin: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
}
.status-scale:disabled trough { background: transparent; }
.status-scale:disabled highlight,
.status-scale:disabled fill,
.status-scale:disabled progress { background: rgba(47, 138, 125, 0.34); }
.status-scale:disabled slider { background: transparent; }
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


def app_catalog_fingerprint(paths=None):
    """Return a bounded, cheap stamp for application-directory changes.

    Directory metadata changes whenever dpkg or a store adds/removes a desktop
    entry.  Deliberately do not recursively walk app directories from the GTK
    timer; only the three trusted roots are inspected.
    """
    entries = [("version", APP_CATALOG_FINGERPRINT_VERSION)]
    for raw_path in tuple(paths or APP_DIRS)[:8]:
        path = Path(raw_path)
        try:
            stat_result = path.stat()
        except OSError:
            entries.append((str(path), "missing"))
            continue
        entries.append((
            str(path),
            getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1000000000)),
            getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1000000000)),
        ))
        # Some virtual/shared filesystems do not advance a directory mtime
        # promptly.  Include a bounded list of direct launcher names so a
        # package installed during this session still becomes visible.
        launchers = []
        try:
            with os.scandir(path) as directory:
                for candidate in directory:
                    if not candidate.name.endswith(".desktop"):
                        continue
                    if len(launchers) >= 512:
                        entries.append((str(path), "launcher-limit"))
                        break
                    launchers.append(candidate.name)
        except OSError:
            continue
        entries.extend((str(path), "launcher", name) for name in sorted(launchers))
    return tuple(entries)


def legacy_desktop_entry(path):
    """Parse a launcher safely when an older shared runtime is still loaded.

    This is deliberately a narrow compatibility path for an interrupted hot
    deployment or an older recovery image.  It never invokes a shell: field
    codes are removed, the executable is resolved first, and the returned
    argv is passed directly to ``subprocess.Popen``.
    """
    target = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        if target.stat().st_size > 256 * 1024:
            return None
        parser.read(target, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    if section.get("Type", "Application") != "Application":
        return None
    if section.get("NoDisplay", "").lower() == "true" or section.get("Hidden", "").lower() == "true":
        return None
    exec_line = section.get("Exec", "").strip()
    if not exec_line:
        return None
    try:
        raw_argv = shlex.split(exec_line, posix=True)
    except ValueError:
        raw_argv = []
    argv = []
    for raw_arg in raw_argv:
        arg = re.sub(r"%[fFuUdDnNickvm]", "", raw_arg)
        if arg:
            argv.append(arg)
    diagnostic = ""
    if not argv:
        diagnostic = "启动器缺少可执行命令。"
    else:
        executable = argv[0]
        if os.path.isabs(executable):
            executable_ok = Path(executable).is_file() and os.access(executable, os.X_OK)
        else:
            executable_ok = bool(shutil.which(executable))
        if not executable_ok:
            diagnostic = "应用的启动命令不存在或不可执行。"
    return {
        "name": section.get("Name[zh_CN]") or section.get("Name") or target.stem,
        "icon": section.get("Icon") or "application-x-executable",
        "categories": section.get("Categories", ""),
        "argv": argv,
        "diagnostic": diagnostic,
    }


def read_app(path):
    diagnose = getattr(COMMON, "diagnose_desktop_file", None)
    if not callable(diagnose):
        legacy = legacy_desktop_entry(path)
        if legacy is None:
            return None
        return {
            "id": app_id(path),
            "type": "app",
            "path": str(path),
            "basename": Path(path).name,
            "name": legacy["name"],
            "icon": legacy["icon"],
            "categories": legacy["categories"],
            "legacy_argv": legacy["argv"],
            "diagnostic": legacy["diagnostic"],
        }
    try:
        entry = diagnose(path)
    except (OSError, ValueError):
        return None
    if entry is None:
        return None
    return {
        "id": app_id(path),
        "type": "app",
        "path": str(path),
        "basename": Path(path).name,
        "name": entry.name or Path(path).stem,
        "icon": entry.icon or "application-x-executable",
        "categories": ";".join(entry.categories),
        "diagnostic": entry.diagnostic,
    }


def launch_item(item, source_rect=None):
    path = item.get("path")
    if not path:
        return False
    diagnostic = str(item.get("diagnostic") or "")
    if diagnostic:
        log(f"launch blocked for {path}: {diagnostic}")
        return False
    legacy_argv = item.get("legacy_argv")
    if legacy_argv:
        log(f"shared launcher validation unavailable; using safe legacy argv for {path}")
        try:
            subprocess.Popen(list(legacy_argv), shell=False)
            return True
        except Exception as exc:
            log(f"legacy exec fallback failed for {path}: {exc}")
            return False
    parser = getattr(COMMON, "parse_desktop_file", None)
    validator = getattr(COMMON, "desktop_launch_diagnostic", None)
    sender = getattr(COMMON, "send_launch_request", None)
    if not callable(parser) or not callable(validator) or not callable(sender):
        log(f"launch validation unavailable for {path}")
        return False
    try:
        entry = parser(path)
    except (OSError, ValueError) as exc:
        log(f"launch validation failed for {path}: {exc}")
        return False
    if entry is None:
        log(f"launch validation failed for {path}: hidden or unavailable entry")
        return False
    diagnostic = validator(entry.argv)
    if diagnostic:
        log(f"launch validation failed for {path}: {diagnostic}")
        return False
    if COMMON.send_launch_request(path, "desktop", source_rect):
        return True
    log(f"launch broker unavailable; using direct fallback for {path}")
    try:
        subprocess.Popen(list(entry.argv), shell=False)
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
            "StartupNotify=true\n"
            f"{DESKTOP_MANAGED_MARKER_LINE}\n",
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


def _item_id(item):
    """Return a stable id while keeping legacy layout entries addressable."""
    existing = item.get("id")
    if existing:
        return str(existing)
    if item.get("type") == "folder" or item.get("children") is not None:
        children = []
        for child in item.get("children", []):
            if isinstance(child, dict):
                child = child.get("path")
            if child:
                children.append(str(child))
        seed = "|".join(children) + "|" + str(item.get("name", "文件夹"))
        return "folder-" + app_id(seed)
    path = item.get("path")
    return app_id(path) if path else None


def migrate_layout(layout):
    """Upgrade a legacy layout without discarding its positions or folders."""
    if not isinstance(layout, dict) or not isinstance(layout.get("items"), list):
        return None
    raw_version = layout.get("version", 0)
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = 0
    # A layout written by a newer desktop is not safe to interpret.  Returning
    # None lets load_layout fall back to the last-good snapshot without
    # rewriting the future file.
    if version > LAYOUT_VERSION:
        return None
    legacy = version < LAYOUT_VERSION
    migrated = dict(layout)
    migrated["version"] = LAYOUT_VERSION
    items = []
    for raw_item in layout.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        is_folder = item.get("type") == "folder" or item.get("children") is not None
        if is_folder:
            children = []
            for child in item.get("children", []):
                if isinstance(child, dict):
                    child = child.get("path")
                if child and str(child) not in children:
                    children.append(str(child))
            item["type"] = "folder"
            item["children"] = children
            item["name"] = str(item.get("name") or "文件夹")
            item["id"] = _item_id(item)
        elif item.get("path"):
            item["type"] = "app"
            item["path"] = str(item["path"])
            item["id"] = _item_id(item)
        else:
            continue
        # Position is intentionally copied verbatim.  Clamping/snapping is a
        # drag-time concern; migration must not move a user's icons.
        item["x"] = item.get("x", PAD_X)
        item["y"] = item.get("y", PAD_Y)
        if legacy:
            # Older layouts represented the desktop as an explicit list.  Keep
            # every existing entry visible after migration, including folders.
            item["pinned"] = bool(item.get("pinned", True))
        else:
            item["pinned"] = bool(item.get("pinned", False))
        items.append(item)
    migrated["items"] = items
    return migrated


def read_layout(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_layout():
    data = read_layout(LAYOUT_PATH)
    if layout_is_valid(data):
        migrated = migrate_layout(data)
        if migrated is not None:
            return migrated
    last_good = read_layout(LAST_GOOD_LAYOUT_PATH)
    if layout_is_valid(last_good, require_items=True):
        log("primary layout invalid; restoring last known-good layout")
        migrated = migrate_layout(last_good)
        if migrated is not None:
            return migrated
    return empty_layout()


def _atomic_write_json(path, payload):
    """Write JSON durably, then replace the destination in one operation."""
    target = Path(path)
    temporary = target.with_name(
        ".%s.%s.%s.tmp" % (target.name, os.getpid(), threading.get_ident())
    )
    descriptor = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
        try:
            target.chmod(0o600)
        except OSError:
            pass
        try:
            directory_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return True
    except Exception as exc:
        log(f"atomic desktop state write failed for {target}: {exc}")
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def save_layout(layout):
    migrated = migrate_layout(layout)
    if migrated is None or migrated.get("version", 0) > LAYOUT_VERSION:
        return False
    if not layout_is_valid(migrated):
        return False
    if not _atomic_write_json(LAYOUT_PATH, migrated):
        return False
    if layout_is_valid(migrated, require_items=True):
        # Keep the previous last-good snapshot if this secondary write fails;
        # it is deliberately never replaced by an empty or malformed layout.
        _atomic_write_json(LAST_GOOD_LAYOUT_PATH, migrated)
    return True


def next_position(index, width=1366):
    cols = min(6, max(3, int((width - PAD_X * 2) / GRID_W)))
    row = index // cols
    col = index % cols
    return PAD_X + col * GRID_W, PAD_Y + row * GRID_H


def clamp_grid_position(x, y, width=1366, height=768):
    """Snap a dragged tile to the desktop grid and keep it in the workarea."""
    width = max(320, int(width or 320))
    height = max(240, int(height or 240))
    max_x = max(PAD_X, width - TILE_W - PAD_X)
    max_y = max(PAD_Y, height - TILE_H - PAD_Y)
    try:
        grid_x = int((float(x) - PAD_X + GRID_W / 2) // GRID_W)
        grid_y = int((float(y) - PAD_Y + GRID_H / 2) // GRID_H)
    except (TypeError, ValueError):
        grid_x = grid_y = 0
    snapped_x = PAD_X + grid_x * GRID_W
    snapped_y = PAD_Y + grid_y * GRID_H
    return max(PAD_X, min(max_x, snapped_x)), max(PAD_Y, min(max_y, snapped_y))


def sync_layout(width=1366):
    apps = load_apps(default_only=False)
    primary = read_layout(LAYOUT_PATH)
    try:
        primary_version = int(primary.get("version", 0)) if isinstance(primary, dict) else 0
    except (TypeError, ValueError):
        primary_version = 0
    if primary_version > LAYOUT_VERSION:
        # Do not let this older binary overwrite a future layout.  load_layout
        # will select the last-good snapshot when one is available.
        log("desktop layout was written by a newer Ming OS; leaving it untouched")
        return load_layout()
    layout = load_layout()
    if not apps and layout_is_valid(layout, require_items=True):
        log("app discovery was transiently empty; keeping last known-good layout")
        return layout
    migrated = migrate_layout(layout)
    if migrated is not None:
        layout = migrated
    catalog_paths = {str(app["path"]) for app in apps}
    previous_catalog = layout.get("catalog_paths")
    catalog_is_initialized = isinstance(previous_catalog, list)
    previous_catalog = {
        str(path) for path in previous_catalog
        if isinstance(path, (str, os.PathLike))
    } if catalog_is_initialized else set()
    apps_by_path = {str(app["path"]): app for app in apps}
    # First-run layouts should remain deliberately compact.  Subsequent
    # catalog changes append only newly installed applications, while all
    # existing app tiles keep their saved coordinates and folders.
    visible_apps = [
        app for app in apps
        if app["basename"] in CORE_NAMES
        or (catalog_is_initialized and str(app["path"]) not in previous_catalog)
    ]
    items = []
    known = set()
    for item in layout.get("items", []):
        if item.get("type") == "folder":
            if item.get("pinned"):
                folder = dict(item)
                children = []
                for child_path in item.get("children", []):
                    child = read_app(child_path)
                    if child:
                        children.append(child["path"])
                folder["children"] = children
                folder["pinned"] = True
                items.append(folder)
                known.update(children)
        elif item.get("path"):
            path = str(item["path"])
            basename = Path(path).name
            fresh = apps_by_path.get(path)
            if fresh or basename in CORE_NAMES or item.get("pinned"):
                restored = dict(fresh or item)
                restored["id"] = item.get("id") or restored.get("id") or app_id(path)
                restored["x"] = item.get("x", PAD_X)
                restored["y"] = item.get("y", PAD_Y)
                restored["pinned"] = bool(item.get("pinned", False))
                items.append(restored)
                known.add(path)
    index = len(items)
    for app in visible_apps:
        if app["path"] in known:
            continue
        app["x"], app["y"] = next_position(index, width)
        app["pinned"] = False
        items.append(app)
        index += 1
    layout["version"] = LAYOUT_VERSION
    layout["items"] = items
    layout["catalog_paths"] = sorted(catalog_paths)
    if items:
        save_layout(layout)
        sync_files(layout)
    return layout


def safe_name(name):
    cleaned = "".join("-" if ch in '/\\:*?"<>|' else ch for ch in name).strip()
    return cleaned or "应用"


def _desktop_has_marker(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return bool(re.search(r"(?im)^\s*X-Ming-Managed\s*=\s*true\s*$", text))


def _mark_desktop_file(path):
    """Mark a generated launcher without changing a user-owned source file."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if _desktop_has_marker(target):
        return True
    match = re.search(r"(?im)^\s*\[Desktop Entry\]\s*$", text)
    if not match:
        return False
    insert_at = match.end()
    if text.startswith("\r\n", insert_at):
        insert_at += 2
    elif insert_at < len(text) and text[insert_at] == "\n":
        insert_at += 1
    else:
        text = text[:insert_at] + "\n" + text[insert_at:]
        insert_at += 1
    text = text[:insert_at] + DESKTOP_MANAGED_MARKER_LINE + "\n" + text[insert_at:]
    try:
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)
        return True
    except OSError:
        return False


def _manifest_relative(path):
    try:
        return Path(path).resolve().relative_to(DESKTOP_DIR.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def empty_desktop_manifest():
    return {
        "version": DESKTOP_MANIFEST_VERSION,
        "marker": DESKTOP_MANAGED_MARKER,
        DESKTOP_MANAGED_MARKER: True,
        "managed_files": [],
        "managed": [],
        "managed_dirs": [],
    }


def load_desktop_manifest(path=None):
    target = Path(path) if path else DESKTOP_MANIFEST_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_desktop_manifest()
    if not isinstance(data, dict):
        return empty_desktop_manifest()
    files = data.get("managed_files", data.get("files", data.get("managed", [])))
    dirs = data.get("managed_dirs", data.get("directories", []))
    if not isinstance(files, list):
        files = []
    if not isinstance(dirs, list):
        dirs = []
    result = empty_desktop_manifest()
    result["managed_files"] = sorted({str(value).replace("\\", "/") for value in files if value})
    result["managed"] = list(result["managed_files"])
    result["managed_dirs"] = sorted({str(value).replace("\\", "/") for value in dirs if value})
    return result


def save_desktop_manifest(manifest, path=None):
    target = Path(path) if path else DESKTOP_MANIFEST_PATH
    payload = empty_desktop_manifest()
    if isinstance(manifest, dict):
        payload["managed_files"] = sorted({
            str(value).replace("\\", "/")
            for value in manifest.get("managed_files", manifest.get("files", []))
            if value
        })
        payload["managed_dirs"] = sorted({
            str(value).replace("\\", "/")
            for value in manifest.get("managed_dirs", manifest.get("directories", []))
            if value
        })
    payload["managed"] = list(payload["managed_files"])
    return _atomic_write_json(target, payload)


def copy_desktop(path, target_dir, name=None, preserve_basename=False, managed=False):
    src = Path(path)
    if not src.is_file():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = src.name if preserve_basename else f"{safe_name(name or src.stem)}.desktop"
    target = target_dir / target_name
    try:
        if src.resolve() == target.resolve():
            if managed and _desktop_has_marker(target):
                target.chmod(0o755)
            return target
        if target.exists() and not _desktop_has_marker(target):
            # Never overwrite an unrelated launcher with the same display
            # name.  Keep the generated copy discoverable under a safe suffix.
            stem = target.stem
            suffix = 1
            candidate = target
            while candidate.exists() and not _desktop_has_marker(candidate):
                candidate = target.with_name(f"{stem}-ming{suffix if suffix > 1 else ''}.desktop")
                suffix += 1
            target = candidate
        shutil.copy2(src, target)
        if managed:
            _mark_desktop_file(target)
        target.chmod(0o755)
        return target
    except Exception:
        return None


def sync_files(layout):
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    previous_manifest = load_desktop_manifest()
    managed_before = set(previous_manifest.get("managed_files", []))
    managed_dirs_before = set(previous_manifest.get("managed_dirs", []))
    # Marker-managed files are authoritative even if an older manifest was
    # interrupted before it could be written.
    for candidate in DESKTOP_DIR.rglob("*.desktop"):
        if _desktop_has_marker(candidate):
            relative = _manifest_relative(candidate)
            if relative:
                managed_before.add(relative)
    source_paths = set()
    for source_item in layout.get("items", []):
        if source_item.get("path"):
            source_paths.add(Path(source_item["path"]).resolve())
        source_paths.update(
            Path(child).resolve()
            for child in source_item.get("children", [])
            if isinstance(child, (str, os.PathLike))
        )
    for relative in sorted(managed_before):
        target = DESKTOP_DIR / Path(relative)
        try:
            target.relative_to(DESKTOP_DIR)
        except ValueError:
            continue
        if target.is_file() and target.resolve() not in source_paths:
            try:
                target.unlink()
            except OSError:
                log(f"could not remove managed desktop launcher: {target}")
    folders_seen = set()
    managed_files = set()
    managed_dirs = set()
    for item in layout.get("items", []):
        if item.get("type") == "folder":
            folder_dir = DESKTOP_DIR / safe_name(item.get("name", "folder"))
            folders_seen.add(folder_dir)
            was_present = folder_dir.exists()
            folder_dir.mkdir(parents=True, exist_ok=True)
            relative_dir = _manifest_relative(folder_dir)
            if relative_dir and (not was_present or relative_dir in managed_dirs_before):
                managed_dirs.add(relative_dir)
            for child_path in item.get("children", []):
                child = read_app(child_path)
                if child:
                    copied = copy_desktop(child_path, folder_dir, child["name"], managed=True)
                    relative = _manifest_relative(copied) if copied else None
                    if relative and copied != Path(child_path).resolve():
                        managed_files.add(relative)
        elif item.get("path") and (Path(item["path"]).name in CORE_NAMES or item.get("pinned")):
            is_core = Path(item["path"]).name in CORE_NAMES
            copied = copy_desktop(
                item["path"],
                DESKTOP_DIR,
                item.get("name"),
                preserve_basename=is_core,
                managed=True,
            )
            if copied:
                relative = _manifest_relative(copied)
                if relative and (copied != Path(item["path"]).resolve() or _desktop_has_marker(copied)):
                    managed_files.add(relative)
    for relative in sorted(managed_dirs_before - managed_dirs):
        old = DESKTOP_DIR / Path(relative)
        try:
            old.relative_to(DESKTOP_DIR)
        except ValueError:
            continue
        if old.is_dir() and not any(old.iterdir()):
            try:
                old.rmdir()
            except Exception:
                pass
    save_desktop_manifest({"managed_files": sorted(managed_files), "managed_dirs": sorted(managed_dirs)})


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
        self.desktop.preview_drag(self.item, x, y)

    def finish_interaction(self, action, root_x, root_y, timestamp=0):
        self.box.get_style_context().remove_class("dragging")
        if action == "drag":
            win_x, win_y = self.desktop.window_origin
            x = int(root_x - win_x - self.offset[0])
            y = int(root_y - win_y - self.offset[1])
            self.desktop.finish_drag(self.item, x, y)
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


class StatusSlider(Gtk.EventBox):
    """Theme-independent slider with its own mouse/touch input window."""

    __gsignals__ = {
        "value-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, lower, upper):
        super().__init__()
        self.lower = float(lower)
        self.upper = float(upper)
        self.value = float(lower)
        self.dragging = False
        self.touch_sequence = None
        self.suppress_mouse_until = 0
        self.set_visible_window(False)
        self.set_above_child(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(-1, 22)
        self.canvas.connect("draw", self.on_draw)
        self.add(self.canvas)
        self.connect("button-press-event", self.on_button_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_button_release)
        self.connect("touch-event", self.on_touch)
        self.connect("notify::sensitive", lambda *_args: self.canvas.queue_draw())

    def set_value(self, value):
        value = max(self.lower, min(self.upper, float(value)))
        if abs(value - self.value) < 0.001:
            self.canvas.queue_draw()
            return
        self.value = value
        self.canvas.queue_draw()
        self.emit("value-changed")

    def get_value(self):
        return self.value

    def value_from_x(self, x):
        width = max(1.0, float(self.get_allocated_width()))
        track_x = 8.0
        track_width = max(1.0, width - 16.0)
        fraction = max(0.0, min(1.0, (float(x) - track_x) / track_width))
        return self.lower + (self.upper - self.lower) * fraction

    def update_from_event(self, event):
        self.set_value(self.value_from_x(getattr(event, "x", 0.0)))

    def on_button_press(self, _widget, event):
        if not self.get_sensitive() or getattr(event, "button", 0) != 1:
            return False
        if GLib.get_monotonic_time() < self.suppress_mouse_until:
            return True
        self.dragging = True
        self.update_from_event(event)
        return True

    def on_motion(self, _widget, event):
        if not self.dragging or not self.get_sensitive():
            return False
        self.update_from_event(event)
        return True

    def on_button_release(self, _widget, event):
        if not self.dragging or getattr(event, "button", 0) != 1:
            return False
        self.update_from_event(event)
        self.dragging = False
        return True

    @staticmethod
    def event_sequence(event):
        try:
            return event.get_event_sequence()
        except Exception:
            return getattr(event, "sequence", None)

    def on_touch(self, _widget, event):
        if not self.get_sensitive():
            return False
        event_type = event.type
        sequence = self.event_sequence(event)
        if event_type == Gdk.EventType.TOUCH_BEGIN:
            if self.touch_sequence is not None and sequence != self.touch_sequence:
                return True
            self.touch_sequence = sequence
            self.update_from_event(event)
            return True
        if sequence != self.touch_sequence:
            return True
        if event_type == Gdk.EventType.TOUCH_UPDATE:
            self.update_from_event(event)
            return True
        if event_type == Gdk.EventType.TOUCH_END:
            self.update_from_event(event)
            self.touch_sequence = None
            self.suppress_mouse_until = GLib.get_monotonic_time() + 650000
            return True
        if event_type == Gdk.EventType.TOUCH_CANCEL:
            self.touch_sequence = None
            self.suppress_mouse_until = GLib.get_monotonic_time() + 650000
            return True
        return False

    @staticmethod
    def rounded_rect(cr, x, y, width, height, radius):
        radius = max(0.0, min(radius, width / 2.0, height / 2.0))
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -1.5708, 0)
        cr.arc(x + width - radius, y + height - radius, radius, 0, 1.5708)
        cr.arc(x + radius, y + height - radius, radius, 1.5708, 3.1416)
        cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
        cr.close_path()

    def on_draw(self, _widget, cr):
        allocation = self.canvas.get_allocation()
        width = max(1.0, float(allocation.width))
        height = max(1.0, float(allocation.height))
        fraction = 0.0 if self.upper <= self.lower else (
            (self.value - self.lower) / (self.upper - self.lower))
        fraction = max(0.0, min(1.0, fraction))
        track_x = 8.0
        track_width = max(1.0, width - 16.0)
        center_y = height / 2.0
        radius = 4.0
        marker_radius = 7.0
        enabled = self.get_sensitive()

        cr.set_source_rgba(0.16, 0.27, 0.23, 0.18 if enabled else 0.08)
        self.rounded_rect(
            cr, track_x, center_y - radius, track_width, radius * 2, radius)
        cr.fill()
        if enabled and fraction > 0:
            cr.set_source_rgb(0.184, 0.541, 0.490)
            self.rounded_rect(
                cr, track_x, center_y - radius,
                max(radius * 2, track_width * fraction), radius * 2, radius)
            cr.fill()
        marker_x = track_x + track_width * fraction
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.arc(marker_x, center_y, marker_radius, 0, 6.2832)
        cr.fill_preserve()
        cr.set_source_rgba(0.184, 0.541, 0.490, 1.0 if enabled else 0.42)
        cr.set_line_width(2.0)
        cr.stroke()
        return False


class ControlRequestState:
    """Track one debounced hardware-control request without stale readbacks."""

    def __init__(self):
        self.generation = 0
        self.pending = False
        self.optimistic_value = None

    def begin(self, value):
        self.generation += 1
        self.pending = True
        self.optimistic_value = value
        return self.generation

    def accepts(self, generation):
        return generation == self.generation

    def settle(self, generation, value):
        if not self.accepts(generation):
            return False
        self.pending = False
        self.optimistic_value = value
        return True

    def should_hold_status(self):
        return self.pending


class StatusWidget(Gtk.Box):
    def __init__(self):
        # Keep the status container windowless so Gtk.Scale remains the event
        # target.  Gtk.EventBox creates an input window around the whole card,
        # which prevents GtkRange's native drag handling from seeing motion.
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.collapsed = load_widget_state()["collapsed"]
        self.refreshing = False
        self.notifications = load_notifications_helper()
        device_module = load_device_control()
        self.device_controller = device_module.DeviceController() if device_module else None
        self.volume_timer = None
        self.brightness_timer = None
        self.updating_controls = False
        self.control_states = {
            "volume": ControlRequestState(),
            "brightness": ControlRequestState(),
        }
        self.updating_dnd = False
        self.action_starts = {}
        self._height_animation = None
        self._height_animation_source = 0
        self._display_height = 54 if self.collapsed else 286
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
        self.volume_label.set_hexpand(True)
        self.volume_label.set_width_chars(8)
        self.volume_scale = StatusSlider(0, 100)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.get_style_context().add_class("status-scale")
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        self.brightness_label = Gtk.Label(label="亮度")
        self.brightness_label.set_halign(Gtk.Align.START)
        self.brightness_label.set_hexpand(True)
        self.brightness_label.set_width_chars(8)
        self.brightness_scale = StatusSlider(1, 100)
        self.brightness_scale.set_hexpand(True)
        self.brightness_scale.get_style_context().add_class("status-scale")
        self.brightness_scale.connect("value-changed", self.on_brightness_changed)
        self.audio_button = self.action_button(
            "声音", ["ming-control-center", "--page", "advanced"])
        self.display_button = self.action_button(
            "显示", ["ming-control-center", "--page", "display"])
        # These two buttons share a row with the labels.  Let the labels keep
        # a readable allocation instead of allowing a generic action button
        # to consume the entire third grid column.
        self.audio_button.set_hexpand(False)
        self.display_button.set_hexpand(False)
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
        self.apply_collapsed_state(animate=False)
        self.refresh()
        GLib.timeout_add_seconds(15, self.refresh)

    def preferred_height(self):
        return int(self._display_height)

    def set_collapsed(self, collapsed):
        self.collapsed = bool(collapsed)
        try:
            save_widget_state(self.collapsed)
        except OSError as exc:
            log("could not save status widget state: %s" % exc)
        self.apply_collapsed_state(animate=True)

    def apply_collapsed_state(self, animate=False):
        style = self.widget_box.get_style_context()
        if self.collapsed:
            style.add_class("status-widget-compact")
        else:
            style.remove_class("status-widget-compact")
        self.compact_button.set_visible(self.collapsed)
        self.content_revealer.set_reveal_child(not self.collapsed)
        target_height = 54 if self.collapsed else 286
        if animate:
            self.animate_collapsed_state(target_height)
        else:
            self._height_animation = None
            self._display_height = target_height
            self.set_size_request(-1, target_height)
            desktop = self.get_toplevel()
            if hasattr(desktop, "place_overlays"):
                desktop.place_overlays()

    def animate_collapsed_state(self, target_height):
        """Match the Revealer transition with a bounded outer-card resize."""
        reduced_motion = False
        try:
            settings = Path.home() / ".config/ming-os/settings.json"
            reduced_motion = bool(json.loads(settings.read_text(encoding="utf-8")).get("reduced_motion"))
        except (OSError, ValueError, AttributeError):
            pass
        if reduced_motion:
            self._height_animation = None
            self._display_height = target_height
            self.set_size_request(-1, target_height)
            desktop = self.get_toplevel()
            if hasattr(desktop, "place_overlays"):
                desktop.place_overlays()
            return
        now = GLib.get_monotonic_time()
        self._height_animation = {
            "start": float(self._display_height), "target": float(target_height), "started": now,
        }
        if self._height_animation_source:
            return

        def step():
            animation = self._height_animation
            if not animation:
                self._height_animation_source = 0
                return False
            progress = min(1.0, (GLib.get_monotonic_time() - animation["started"]) / 180000.0)
            eased = COMMON.ease_out_cubic(progress)
            self._display_height = animation["start"] + (
                animation["target"] - animation["start"]) * eased
            self.set_size_request(-1, int(round(self._display_height)))
            desktop = self.get_toplevel()
            if hasattr(desktop, "place_overlays"):
                desktop.place_overlays()
            if progress < 1.0:
                return True
            self._display_height = animation["target"]
            self._height_animation = None
            self._height_animation_source = 0
            return False

        self._height_animation_source = GLib.timeout_add(16, step)

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

    def schedule_control(self, kind, value, generation):
        attr = "%s_timer" % kind
        timer = getattr(self, attr)
        if timer:
            GLib.source_remove(timer)

        def apply_value():
            setattr(self, attr, None)
            state = self.control_states[kind]
            if not state.accepts(generation):
                return False
            threading.Thread(
                target=self.set_control_value,
                args=(kind, value, generation),
                daemon=True,
            ).start()
            return False

        setattr(self, attr, GLib.timeout_add(120, apply_value))

    def on_volume_changed(self, control):
        if not self.updating_controls:
            value = max(0, min(100, int(round(control.get_value()))))
            generation = self.control_states["volume"].begin(value)
            self.volume_label.set_text("音量 %d%%" % value)
            self.schedule_control("volume", value, generation)
            self.volume_scale.queue_draw()

    def on_brightness_changed(self, control):
        if not self.updating_controls:
            value = max(1, min(100, int(round(control.get_value()))))
            generation = self.control_states["brightness"].begin(value)
            self.brightness_label.set_text("亮度 %d%%" % value)
            self.schedule_control("brightness", value, generation)
            self.brightness_scale.queue_draw()

    def set_control_value(self, kind, value, generation):
        try:
            if not self.device_controller:
                result = {"ok": False, "error": "设备控制服务不可用", "value": None}
            elif kind == "volume":
                result = self.device_controller.set_volume(value)
            else:
                result = self.device_controller.set_brightness(value)
            GLib.idle_add(self.apply_control_result, kind, generation, result)
        except Exception as exc:
            log(f"{kind} control failed: {exc}")
            GLib.idle_add(
                self.apply_control_result,
                kind,
                generation,
                {"ok": False, "error": str(exc), "value": None},
            )

    def apply_control_result(self, kind, generation, result):
        state = self.control_states[kind]
        if not state.accepts(generation):
            log("ignored stale %s control response generation=%s" % (kind, generation))
            return False
        self.updating_controls = True
        value = result.get("value")
        if result.get("ok") and value is not None:
            state.settle(generation, value)
            if kind == "volume":
                self.volume_scale.set_value(value)
                self.volume_label.set_text("音量 %d%%" % value)
                self.volume_scale.queue_draw()
            else:
                self.brightness_scale.set_value(value)
                self.brightness_label.set_text("亮度 %d%%" % value)
                self.brightness_scale.queue_draw()
        else:
            state.pending = False
            message = result.get("error") or "控制失败"
            log("%s control rejected: %s" % (kind, message))
            if kind == "volume":
                self.volume_label.set_text("音量设置失败，点击重试")
            else:
                self.brightness_label.set_text("亮度设置失败，点击重试")
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

    def background_update_available(self):
        """Trust only the root-owned result written by an automatic check."""
        try:
            result = subprocess.run(
                ["ming-update", "status", "--json"],
                capture_output=True, text=True, timeout=3,
            )
            status = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log("power menu update status failed: %s" % exc)
            return False
        return bool(isinstance(status, dict) and status.get("background_available"))

    def open_update_and_shutdown_dialog(self, _item=None):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(), flags=0,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
            text="确认更新并关机？",
        )
        dialog.format_secondary_text(
            "系统会自动完成已确认更新，完成后关机。没有可用更新时不会关机。")
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        dialog.add_button("更新并关机", Gtk.ResponseType.OK)

        def respond(current, response):
            current.destroy()
            if response != Gtk.ResponseType.OK:
                return
            try:
                subprocess.Popen(
                    ["pkexec", "ming-update", "auto-shutdown"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                log("update and shutdown launch failed: %s" % exc)

        dialog.connect("response", respond)
        dialog.show_all()

    def show_confirmed_update_power_menu(self, button):
        """Keep the usual session actions while adding the gated update action."""
        menu = Gtk.Menu()

        def add_item(label, callback):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)

        def launch(command):
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                log("power action failed %s: %s" % (command[0], exc))

        add_item("锁定屏幕", lambda _item: launch(["ming-lock"]))
        add_item("更新并关机", self.open_update_and_shutdown_dialog)
        menu.append(Gtk.SeparatorMenuItem())
        add_item("注销", lambda _item: launch(["xfce4-session-logout", "--logout"]))
        add_item("重新启动", lambda _item: launch(["xfce4-session-logout", "--reboot"]))
        add_item("关机", lambda _item: launch(["xfce4-session-logout", "--halt"]))
        menu.show_all()
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def open_power_menu(self, _button):
        if self.background_update_available():
            self.show_confirmed_update_power_menu(_button)
            return
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
        volume_state = self.control_states["volume"]
        if not volume_state.should_hold_status():
            self.volume_scale.set_value(max(0, min(100, volume or 0)))
            self.volume_label.set_text(
                "音量 %d%%" % volume if audio_available else "未检测到输出设备")
        elif volume_state.optimistic_value is not None:
            self.volume_scale.set_value(volume_state.optimistic_value)
            self.volume_label.set_text("音量 %d%%" % volume_state.optimistic_value)
        brightness_available = bool(brightness.get("available"))
        brightness_value = brightness.get("value") if brightness_available else 1
        self.brightness_scale.set_sensitive(brightness_available)
        brightness_state = self.control_states["brightness"]
        if not brightness_state.should_hold_status():
            self.brightness_scale.set_value(max(1, min(100, brightness_value or 1)))
            self.brightness_label.set_text(
                "亮度 %d%%" % brightness_value if brightness_available else "当前设备不支持")
        elif brightness_state.optimistic_value is not None:
            self.brightness_scale.set_value(brightness_state.optimistic_value)
            self.brightness_label.set_text("亮度 %d%%" % brightness_state.optimistic_value)
        self.brightness_label.set_visible(True)
        self.brightness_scale.set_visible(True)
        self.display_button.set_visible(True)
        self.updating_controls = False
        self.volume_scale.queue_draw()
        self.brightness_scale.queue_draw()
        self.refreshing = False
        return False


class WallpaperCanvas(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.pixbuf = self.load_wallpaper()
        self.connect("draw", self.on_draw)

    def load_wallpaper(self):
        for path in appearance_wallpaper_paths(load_appearance()):
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
        # Gtk.Fixed and transparent EventBox children can be no-window widgets
        # on the VirtualBox/Xrender path.  Receive root-window events as the
        # final, renderer-independent desktop input route.
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.connect("button-press-event", self.on_window_button_press)
        self.connect("motion-notify-event", self.on_window_motion)
        self.connect("button-release-event", self.on_window_button_release)
        self.connect("touch-event", self.on_window_touch)

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
        self.fixed_press_offset = (0, 0)
        self.fixed_press_moved = False
        self.fixed_touch_item = None
        self.fixed_touch_offset = (0, 0)
        self.fixed_touch_state = InteractionState()
        self.drag_positions = {}
        self.layer_enforcement_pending = False
        self.status = StatusWidget()
        self.launch_feedback = LaunchFeedbackOverlay()
        self.launch_feedback.set_sensitive(False)
        self.connect("map-event", lambda *_args: self.enforce_desktop_layer())
        self.connect("size-allocate", lambda *_args: self.place_overlays())
        self.layout = sync_layout(screen_w)
        self.appearance = load_appearance()
        old_scale = self.layout.get("desktop_icon_scale", self.appearance["desktop_icon_scale"])
        if old_scale != self.appearance["desktop_icon_scale"]:
            self.layout = reflow_layout_for_icon_scale(
                self.layout, old_scale, self.appearance["desktop_icon_scale"], screen_w, screen_h)
            save_layout(self.layout)
        self.layout_stamp = self.current_layout_stamp()
        self.appearance_stamp = self.current_appearance_stamp()
        self.catalog_stamp = app_catalog_fingerprint()
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
            is_folder = item.get("type") == "folder"
            if not is_folder and item.get("type") != "app":
                continue
            x, y = self.item_position(item)
            self.rounded_rect(cr, x, y, TILE_W - 4, TILE_H - 4, 12)
            if is_folder:
                cr.set_source_rgba(0.91, 0.97, 0.95, 0.68)
            else:
                cr.set_source_rgba(1, 1, 1, 0.48)
            cr.fill_preserve()
            cr.set_source_rgba(0.18, 0.54, 0.49, 0.26 if is_folder else 0.18)
            cr.set_line_width(1)
            cr.stroke()
            icon_name = "folder" if is_folder else (item.get("icon") or "application-x-executable")
            try:
                icon_size = appearance_icon_size(self.appearance)
                pixbuf = COMMON.load_icon_pixbuf(icon_theme, icon_name, icon_size)
            except Exception:
                pixbuf = None
            if pixbuf:
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, x + int((TILE_W - icon_size) / 2), y + 7)
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
            ix, iy = self.item_position(item)
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

    def event_targets_root_canvas(self, event):
        """Return whether a toplevel-window event belongs to the desktop.

        On the VirtualBox/Xrender baseline the no-window Fixed container and
        transparent tiles share the toplevel GdkWindow.  Gtk does not route
        that event back through Fixed, so the toplevel must do it explicitly.
        Events from real child windows still belong to their own controls.
        """
        event_window = getattr(event, "window", None)
        root_window = self.get_window()
        fixed_window = self.fixed.get_window()
        if event_window is not None and event_window not in (root_window, fixed_window):
            return False
        x, y = self.fixed_event_coords(event)
        for overlay in (self.status, self.launch_feedback):
            if not overlay.get_visible():
                continue
            allocation = overlay.get_allocation()
            if (
                allocation.x <= x < allocation.x + allocation.width
                and allocation.y <= y < allocation.y + allocation.height
            ):
                return False
        return True

    def on_window_button_press(self, _widget, event):
        if not self.event_targets_root_canvas(event):
            return False
        return self.on_fixed_button_press(self.fixed, event)

    def on_window_motion(self, _widget, event):
        if not self.event_targets_root_canvas(event):
            return False
        return self.on_fixed_motion(self.fixed, event)

    def on_window_button_release(self, _widget, event):
        if not self.event_targets_root_canvas(event):
            return False
        return self.on_fixed_button_release(self.fixed, event)

    def on_window_touch(self, _widget, event):
        if not self.event_targets_root_canvas(event):
            return False
        return self.on_fixed_touch(self.fixed, event)

    def item_by_id(self, item_id):
        for item in self.layout.get("items", []):
            if item.get("id") == item_id:
                return item
        return None

    def item_position(self, item):
        preview = self.drag_positions.get(item.get("id"))
        if preview is not None:
            return preview
        return int(item.get("x", PAD_X)), int(item.get("y", PAD_Y))

    def preview_drag(self, item, x, y):
        screen = self.get_screen()
        x, y = clamp_grid_position(x, y, screen.get_width(), screen.get_height())
        self.drag_positions[item.get("id")] = (x, y)
        tile = self.tiles.get(item.get("id"))
        if tile is not None:
            self.fixed.move(tile, x, y)
        self.fixed.queue_draw()
        return x, y

    def on_fixed_button_press(self, _widget, event):
        if getattr(event, "button", 0) not in (1, 3):
            return False
        x, y = self.fixed_event_coords(event)
        item = self.item_at(x, y)
        self.fixed_press_item = item.get("id") if item else None
        self.fixed_press_origin = (x, y)
        if item:
            item_x, item_y = self.item_position(item)
            self.fixed_press_offset = (x - item_x, y - item_y)
        else:
            self.fixed_press_offset = (0, 0)
        self.fixed_press_moved = False
        return bool(item)

    def on_fixed_motion(self, _widget, event):
        if not self.fixed_press_origin:
            return False
        x, y = self.fixed_event_coords(event)
        dx = x - self.fixed_press_origin[0]
        dy = y - self.fixed_press_origin[1]
        if dx * dx + dy * dy >= DRAG_THRESHOLD * DRAG_THRESHOLD:
            self.fixed_press_moved = True
            item = self.item_by_id(self.fixed_press_item)
            if item:
                self.preview_drag(
                    item,
                    int(x - self.fixed_press_offset[0]),
                    int(y - self.fixed_press_offset[1]),
                )
        return bool(self.fixed_press_item)

    def on_fixed_button_release(self, _widget, event):
        x, y = self.fixed_event_coords(event)
        press_item = self.fixed_press_item
        moved = self.fixed_press_moved
        source = self.item_by_id(press_item)
        item = self.item_at(x, y)
        preview = self.drag_positions.pop(press_item, None) if press_item else None
        self.fixed_press_item = None
        self.fixed_press_origin = None
        self.fixed_press_offset = (0, 0)
        self.fixed_press_moved = False
        if getattr(event, "button", 0) == 1 and moved:
            if source and preview:
                self.finish_drag(source, *preview)
                return True
            return False
        if not item:
            if getattr(event, "button", 0) == 3:
                self.show_desktop_context_menu(event)
                return True
            return False
        if getattr(event, "button", 0) == 3:
            self.show_context_menu(item, event)
            return True
        if getattr(event, "button", 0) == 1:
            if press_item is not None and press_item != item.get("id"):
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
                item_x, item_y = self.item_position(item)
                self.fixed_touch_offset = (x - item_x, y - item_y)
            return bool(item)
        if event_type == Gdk.EventType.TOUCH_UPDATE:
            if self.fixed_touch_item:
                if self.fixed_touch_state.update(x, y):
                    self.preview_drag(
                        self.fixed_touch_item,
                        int(x - self.fixed_touch_offset[0]),
                        int(y - self.fixed_touch_offset[1]),
                    )
                return True
            return False
        if event_type == Gdk.EventType.TOUCH_END:
            item = self.fixed_touch_item
            self.fixed_touch_item = None
            action = self.fixed_touch_state.finish(x, y, timestamp)
            preview = self.drag_positions.pop(item.get("id"), None) if item else None
            self.fixed_touch_offset = (0, 0)
            if item and action == "drag" and preview:
                self.finish_drag(item, *preview)
                return True
            if item and action == "activate" and self.item_at(x, y) is item:
                return self.dispatch_activation(item, timestamp)
            return bool(item)
        if event_type == Gdk.EventType.TOUCH_CANCEL:
            if self.fixed_touch_item:
                self.drag_positions.pop(self.fixed_touch_item.get("id"), None)
            self.fixed_touch_item = None
            self.fixed_touch_offset = (0, 0)
            self.fixed_touch_state.cancel()
            self.fixed.queue_draw()
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
            self.catalog_stamp = app_catalog_fingerprint()
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
        self.drag_positions = {}
        log(f"render desktop items={len(self.layout.get('items', []))} screen={screen_w}x{screen_h}")
        # Cairo remains the single visual source.  Transparent DesktopTile
        # widgets provide precise mouse/touch hit targets without painting a
        # second, potentially divergent copy of each icon.
        for item in self.layout.get("items", []):
            tile = DesktopTile(self, item)
            self.tiles[item.get("id")] = tile
            self.fixed.put(tile, int(item.get("x", PAD_X)), int(item.get("y", PAD_Y)))
        self.place_overlays()
        self.show_all()
        # Gtk.Widget.show_all() re-shows explicitly hidden children.  Reapply
        # the persisted widget state after rendering so compact mode never
        # leaves both the compact row and expanded controls visible.
        self.status.apply_collapsed_state()
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

    @staticmethod
    def current_appearance_stamp():
        try:
            return APPEARANCE_PATH.stat().st_mtime_ns
        except OSError:
            return 0

    def refresh_if_apps_changed(self):
        stamp = self.current_layout_stamp()
        catalog_stamp = app_catalog_fingerprint()
        appearance_stamp = self.current_appearance_stamp()
        appearance_changed = appearance_stamp != self.appearance_stamp
        layout_changed = stamp != self.layout_stamp
        catalog_changed = catalog_stamp != self.catalog_stamp
        if not layout_changed and not catalog_changed and not appearance_changed:
            return True
        if appearance_changed:
            updated_appearance = load_appearance()
            old_scale = self.appearance.get("desktop_icon_scale", 1.0)
            new_scale = updated_appearance.get("desktop_icon_scale", 1.0)
            if old_scale != new_scale:
                screen = self.get_screen()
                self.layout = reflow_layout_for_icon_scale(
                    self.layout, old_scale, new_scale, screen.get_width(), screen.get_height())
                save_layout(self.layout)
            self.appearance = updated_appearance
            self.appearance_stamp = appearance_stamp
            self.wallpaper.pixbuf = self.wallpaper.load_wallpaper()
            self.wallpaper.queue_draw()
            self.render()
        if catalog_changed:
            updated = sync_layout(self.get_screen().get_width())
        else:
            updated = load_layout()
        self.layout_stamp = self.current_layout_stamp()
        self.catalog_stamp = app_catalog_fingerprint()
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
            item_x, item_y = self.item_position(item)
            dx = item_x - x
            dy = item_y - y
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < best_distance:
                best = item
                best_distance = distance
        return best

    def finish_drag(self, item, x, y):
        screen = self.get_screen()
        x, y = clamp_grid_position(x, y, screen.get_width(), screen.get_height())
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
            target["pinned"] = True
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
            "pinned": True,
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
            detail = str(item.get("diagnostic") or "启动失败，详细信息已写入桌面日志。")
            dialog.format_secondary_text(detail)
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
            child_image = resolved_icon_image(child.get("icon"), appearance_icon_size(self.appearance))
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
        app["pinned"] = True
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
