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
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def load_ui_tokens():
    candidates = (
        Path(__file__).with_name("ming-ui-tokens.py"),
        Path("/usr/local/lib/ming-os/ming-ui-tokens.py"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_ui_tokens", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.TOKENS
    return {
        "canvas": "#F4F7F3", "surface": "#FFFFFF", "surface_elevated": "#FBFDFB",
        "surface_subtle": "#EEF5F1", "accent": "#2F8A7D", "accent_strong": "#1F7668",
        "text": "#1B2320", "muted": "#5B6B64", "success": "#2E8B68",
        "warning": "#B7791F", "danger": "#C24B4B", "focus": "#3AAE99",
        "border": "#D7E4DE", "shadow": "#17483C",
    }


TOKENS = {
    "canvas": "#F4F7F3", "surface": "#FFFFFF", "surface_elevated": "#FBFDFB",
    "surface_subtle": "#EEF5F1", "accent": "#2F8A7D", "accent_strong": "#1F7668",
    "text": "#1B2320", "muted": "#5B6B64", "success": "#2E8B68",
    "warning": "#B7791F", "danger": "#C24B4B", "focus": "#3AAE99",
    "border": "#D7E4DE", "shadow": "#17483C",
}


def widget_state_path():
    return Path.home() / ".config" / "ming-os" / "status-widget.json"


def appearance_config_path():
    return Path.home() / ".config" / "ming-os" / "appearance.json"


METRIC_MODES = ("memory", "cpu", "network")
WIDGET_STATE_SCHEMA_VERSION = 2
COMPACT_BATTERY_REFRESH_SECONDS = 60
STATUS_SUMMARY_REFRESH_SECONDS = 45
STATUS_RESOURCE_REFRESH_SECONDS = 30
LAUNCH_PROXY = "/usr/local/bin/ming-launch"
MING_WIDGET_MARK_ICON = "ming-mark"
# The capsule is deliberately narrower than the expanded control panel.  Keep
# these dimensions stable so battery/network readbacks cannot resize it.
STATUS_WIDGET_COMPACT_WIDTH = 252
STATUS_WIDGET_COMPACT_HEIGHT = 58
STATUS_WIDGET_COMPACT_NARROW_WIDTH = 242
CLOCK_MARGIN_X = 26


def is_status_widget_toggle_key(keyval):
    """Accept the left/right Windows keys without stealing text shortcuts."""
    gdk = globals().get("Gdk")
    if gdk is None:
        return False
    return keyval in {
        getattr(gdk, "KEY_Super_L", 0),
        getattr(gdk, "KEY_Super_R", 0),
        getattr(gdk, "KEY_Meta_L", 0),
        getattr(gdk, "KEY_Meta_R", 0),
        getattr(gdk, "KEY_Win_L", 0),
        getattr(gdk, "KEY_Win_R", 0),
    }


def normalize_metric_mode(value):
    return value if value in METRIC_MODES else "memory"


def load_widget_state(path=None):
    """Load compact state and the resource metric mode with safe defaults."""
    target = Path(path) if path else widget_state_path()
    default = {
        "schema_version": WIDGET_STATE_SCHEMA_VERSION,
        "collapsed": True,
        "metric_mode": "memory",
    }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if (not isinstance(data, dict)
            or data.get("schema_version") != WIDGET_STATE_SCHEMA_VERSION
            or not isinstance(data.get("collapsed"), bool)):
        return default
    return {
        "schema_version": WIDGET_STATE_SCHEMA_VERSION,
        "collapsed": data["collapsed"],
        "metric_mode": normalize_metric_mode(data.get("metric_mode")),
    }


def save_widget_state(collapsed, path=None, metric_mode="memory"):
    """Atomically persist widget state without touching desktop layouts."""
    target = Path(path) if path else widget_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".%s.%s.tmp" % (target.name, os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({
                "schema_version": WIDGET_STATE_SCHEMA_VERSION,
                "collapsed": bool(collapsed),
                "metric_mode": normalize_metric_mode(metric_mode),
            }, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def preserve_confirmed_control_value(previous, value, available, minimum=0):
    """Keep the last trusted control value across a transient readback failure."""
    if available and not isinstance(value, bool):
        try:
            normalized = int(round(float(value)))
        except (TypeError, ValueError):
            normalized = None
        if normalized is not None and minimum <= normalized <= 100:
            return normalized
    if isinstance(previous, bool):
        return None
    try:
        normalized_previous = int(round(float(previous)))
    except (TypeError, ValueError):
        return None
    return normalized_previous if minimum <= normalized_previous <= 100 else None


def system_prefers_dark():
    try:
        completed = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            timeout=1, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "prefer-dark" in (completed.stdout or "")


def status_widget_overlay_geometry(pill_geometry, panel_size, screen_size):
    """Return stable compact-pill and clamped expanded-panel geometry.

    The pill is intentionally returned unchanged: expanding the controls must
    never make the desktop reserve a taller overlay.  The panel is placed
    below the pill when possible and flips above it if the bottom edge would
    leave the screen.
    """
    pill = dict(pill_geometry or {})
    screen_w = max(1, int((screen_size or {}).get("width", 1)))
    screen_h = max(1, int((screen_size or {}).get("height", 1)))
    panel_w = min(screen_w, max(1, int((panel_size or {}).get("width", 1))))
    panel_h = min(screen_h, max(1, int((panel_size or {}).get("height", 1))))
    pill_x = int(pill.get("x", 0))
    pill_y = int(pill.get("y", 0))
    pill_w = int(pill.get("width", 0))
    pill_h = int(pill.get("height", 0))
    panel_x = min(max(0, pill_x + pill_w - panel_w), max(0, screen_w - panel_w))
    pill_top = min(max(0, pill_y), screen_h)
    pill_bottom = min(max(pill_top, pill_y + pill_h), screen_h)
    space_below = max(0, screen_h - pill_bottom)
    space_above = max(0, pill_top)
    if panel_h <= space_below and space_below:
        panel_y = pill_bottom
    elif panel_h <= space_above and space_above:
        panel_y = pill_top - panel_h
    elif space_below >= space_above and space_below:
        panel_h = space_below
        panel_y = pill_bottom
    elif space_above:
        panel_h = space_above
        panel_y = pill_top - panel_h
    else:
        panel_y = 0
        panel_h = screen_h
    return {
        "pill": pill,
        "panel": {"x": panel_x, "y": panel_y, "width": panel_w, "height": panel_h},
    }


def status_widget_compact_geometry(screen_size):
    """Return a fixed-size capsule that fits narrow monitors without growth."""
    screen_w = max(1, int((screen_size or {}).get("width", 1)))
    available_width = max(1, screen_w - 2 * CLOCK_MARGIN_X)
    width = min(
        STATUS_WIDGET_COMPACT_WIDTH,
        STATUS_WIDGET_COMPACT_NARROW_WIDTH,
        available_width,
    )
    return {"width": width, "height": STATUS_WIDGET_COMPACT_HEIGHT}


def load_appearance_theme(path=None):
    target = Path(path) if path else appearance_config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "system"
    theme = data.get("theme") if isinstance(data, dict) else "system"
    return theme if theme in {"system", "light", "dark"} else "system"


def _proc_lines(path):
    try:
        return Path(path).read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return []


def _metric_result(mode, value=None, unit="", available=False, sample_time=0,
                   interface="", reason=""):
    return {
        "mode": mode,
        "value": value,
        "unit": unit,
        "available": bool(available),
        "sample_time": sample_time,
        "interface": interface,
        "reason": reason,
    }


def read_resource_metric(mode, previous=None, now=None, proc_root="/proc"):
    """Read one bounded resource metric without spawning a diagnostic command."""
    mode = normalize_metric_mode(mode)
    previous = previous if isinstance(previous, dict) else {}
    now = time.monotonic() if now is None else float(now)
    root = Path(proc_root)
    if mode == "memory":
        values = {}
        for line in _proc_lines(root / "meminfo"):
            key, separator, value = line.partition(":")
            if separator:
                match = re.search(r"\d+", value)
                if match:
                    values[key] = int(match.group())
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        if total <= 0:
            return _metric_result(mode, reason="无法读取 /proc/meminfo。")
        used = max(0, min(total, total - available))
        return _metric_result(
            mode, round(used * 100.0 / total, 1), "%", True, int(now), reason="")

    if mode == "cpu":
        line = next((line for line in _proc_lines(root / "stat")
                     if line.startswith("cpu ")), "")
        fields = line.split()[1:]
        if len(fields) < 4:
            return _metric_result(mode, reason="无法读取 /proc/stat。")
        try:
            counters = [int(item) for item in fields[:8]]
        except ValueError:
            return _metric_result(mode, reason="CPU 采样数据无效。")
        if not previous or "counters" not in previous:
            return _metric_result(mode, available=False, sample_time=int(now),
                                  reason="正在采样 CPU。")
        total_delta = sum(counters) - sum(previous.get("counters", []))
        idle_delta = sum(counters[3:5]) - sum(previous.get("counters", [0] * 8)[3:5])
        if total_delta <= 0:
            return _metric_result(mode, available=False, sample_time=int(now),
                                  reason="CPU 采样间隔不足。")
        value = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        return _metric_result(mode, round(value, 1), "%", True, int(now), reason="")

    route_interface = ""
    for line in _proc_lines(root / "net" / "route")[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "00000000" and fields[0] != "lo":
            route_interface = fields[0]
            break
    records = {}
    for line in _proc_lines(root / "net" / "dev"):
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        fields = data.split()
        if len(fields) >= 9:
            try:
                records[name] = (int(fields[0]), int(fields[8]))
            except ValueError:
                continue
    interface = route_interface or (next(iter(records), ""))
    if not interface or interface not in records:
        return _metric_result(mode, interface=interface, reason="未检测到可用网络接口。")
    rx, tx = records[interface]
    if previous.get("interface") != interface or "bytes" not in previous:
        return _metric_result(mode, interface=interface, sample_time=int(now),
                              reason="正在采样网络速度。")
    elapsed = max(0.1, now - float(previous.get("sample_time", now)))
    old_rx, old_tx = previous["bytes"]
    value = max(0.0, (rx + tx - old_rx - old_tx) / elapsed / 1024.0)
    return _metric_result(mode, round(value, 1), "KB/s", True, int(now), interface, "")


class ResourceMetricSampler:
    def __init__(self):
        self.previous = {}

    def sample(self, mode):
        mode = normalize_metric_mode(mode)
        now = time.monotonic()
        result = read_resource_metric(mode, self.previous.get(mode), now=now)
        if mode == "cpu":
            line = next((line for line in _proc_lines("/proc/stat")
                         if line.startswith("cpu ")), "")
            try:
                counters = [int(item) for item in line.split()[1:9]]
            except (TypeError, ValueError):
                counters = []
            if counters:
                self.previous[mode] = {"counters": counters, "sample_time": now}
        elif mode == "network":
            interface = result.get("interface")
            if interface:
                records = {}
                for line in _proc_lines("/proc/net/dev"):
                    if ":" not in line:
                        continue
                    name, data = line.split(":", 1)
                    fields = data.split()
                    if name.strip() != "lo" and len(fields) >= 9:
                        try:
                            records[name.strip()] = (int(fields[0]), int(fields[8]))
                        except ValueError:
                            pass
                if interface in records:
                    self.previous[mode] = {
                        "interface": interface, "bytes": records[interface],
                        "sample_time": now,
                    }
        return result

    def sample_all(self):
        """Collect the three preview metrics in one expanded-panel pass."""
        return {mode: self.sample(mode) for mode in METRIC_MODES}


def desktop_directory():
    """Resolve the user's XDG desktop directory for visible copies only.

    Desktop launchers are presentation artifacts and must never be treated as
    an application catalog.  Prefer the desktop directory advertised by
    xdg-user-dirs, while keeping an explicit environment override for tests
    and recovery sessions.
    """
    override = os.environ.get("MING_DESKTOP_DIR") or os.environ.get("XDG_DESKTOP_DIR")
    if override:
        candidate = Path(os.path.expandvars(override)).expanduser()
        if candidate.is_absolute() and candidate != Path("/"):
            return candidate
    try:
        completed = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None:
        value = (completed.stdout or "").strip()
        if completed.returncode == 0 and value:
            candidate = Path(os.path.expandvars(value)).expanduser()
            if candidate.is_absolute() and candidate != Path("/"):
                return candidate
    return Path.home() / "Desktop"


# Keep AST-based migration tests able to load the pure layout helpers without
# importing the optional XDG resolver.  In the real process the resolver is
# defined above; reduced AST fixtures simply use the safe Desktop fallback.
_desktop_resolver = globals().get("desktop_directory")
DESKTOP_DIR = _desktop_resolver() if callable(_desktop_resolver) else Path.home() / "Desktop"


def _unique_desktop_path(stem, suffix=""):
    """Return a collision-free path below the user's Desktop directory."""
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    candidate = DESKTOP_DIR / (stem + suffix)
    index = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = DESKTOP_DIR / (f"{stem} ({index})" + suffix)
        index += 1
    return candidate


def create_blank_desktop_file():
    path = _unique_desktop_path("新建文件", ".txt")
    path.open("x", encoding="utf-8").close()
    return path


def create_desktop_folder():
    path = _unique_desktop_path("新建文件夹")
    path.mkdir()
    return path


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

HOME = Path.home()
STATE_DIR = HOME / ".config" / "ming-os"
LAYOUT_PATH = STATE_DIR / "desktop-layout.json"
LAST_GOOD_LAYOUT_PATH = STATE_DIR / "desktop-layout.last-good.json"
DESKTOP_MANIFEST_PATH = STATE_DIR / "desktop-generated-manifest.json"
DESKTOP_MANIFEST_VERSION = 1
DESKTOP_MANAGED_MARKER = "X-Ming-Managed"
DESKTOP_MANAGED_MARKER_LINE = "X-Ming-Managed=true"
DESKTOP_SOURCE_MARKER = "X-Ming-Source-Desktop"
READY_MARKER = HOME / ".cache" / "ming-os" / "ming-phone-desktop.ready"


def session_ready_file():
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "ming-session-healthcheck.ready"


def session_ready():
    return session_ready_file().is_file()
SYSTEM_APPLICATION_DIR = Path("/usr/share/applications")
LOCAL_APPLICATION_DIR = Path("/usr/local/share/applications")
APP_DIRS = [
    SYSTEM_APPLICATION_DIR,
    LOCAL_APPLICATION_DIR,
    HOME / ".local/share/applications",
]
APP_CATALOG_FINGERPRINT_VERSION = 1
CORE_NAMES = {
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-terminal.desktop",
    "ming-firefox.desktop",
    "ming-store.desktop",
    "ming-toolbox.desktop",
    "xiahai-xiaoming.desktop",
    "Install Ming OS.desktop",
}
DESKTOP_ORDER = {name: idx for idx, name in enumerate([
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-firefox.desktop",
    "ming-store.desktop",
    "ming-toolbox.desktop",
    "xiahai-xiaoming.desktop",
    "Install Ming OS.desktop",
    "ming-terminal.desktop",
])}
CORE_FALLBACKS = {
    "ming-firefox.desktop": ["firefox-esr.desktop", "firefox.desktop"],
}
CANONICAL_LAUNCHERS = {
    "ming-settings.desktop": "settings",
    "ming-control-center.desktop": "settings",
    "ming-dock-ming-settings.desktop": "settings",
    "xfce4-settings-manager.desktop": "settings",
    "ming-files.desktop": "files",
    "ming-dock-ming-files.desktop": "files",
    "thunar.desktop": "files",
    "ming-terminal.desktop": "terminal",
    "ming-dock-ming-terminal.desktop": "terminal",
    "xfce4-terminal.desktop": "terminal",
    "ming-store.desktop": "store",
    "ming-dock-ming-store.desktop": "store",
    "ming-toolbox.desktop": "toolbox",
    "ming-dock-ming-toolbox.desktop": "toolbox",
    "ming-firefox.desktop": "browser",
    "firefox-esr.desktop": "browser",
    "firefox.desktop": "browser",
    "firefox esr.desktop": "browser",
    "firefox esr 浏览器.desktop": "browser",
    "xiahai-xiaoming.desktop": "agent",
    "xiahai.desktop": "agent",
    "papyrus.desktop": "agent",
    "ming 设置.desktop": "settings",
}
CANONICAL_PREFERENCE = {
    "settings": "ming-settings.desktop",
    "files": "ming-files.desktop",
    "terminal": "ming-terminal.desktop",
    "store": "ming-store.desktop",
    "toolbox": "ming-toolbox.desktop",
    "browser": "ming-firefox.desktop",
    "agent": "xiahai-xiaoming.desktop",
}

# Xfce remains the implementation layer, but its individual utility windows
# should not leak into the Ming desktop catalog.  The three launchers below are
# retained only as graceful fallbacks when a minimal image lacks the canonical
# Ming entry; normal images deduplicate them in favor of the Ming launcher.
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
LEGACY_XFCE_PREFIXES = ("xfdesktop", "xfwm", "exo-")


def is_legacy_xfce_entry(path):
    """Return whether a launcher is an Xfce utility hidden by Ming UI."""
    basename = Path(path).name.casefold()
    if basename in {item.casefold() for item in VISIBLE_XFCE_FALLBACKS}:
        return False
    return basename in {item.casefold() for item in LEGACY_XFCE_LAUNCHERS} or (
        basename.startswith(("xfce4-", "xfce-", *LEGACY_XFCE_PREFIXES))
        and basename.endswith(".desktop")
    )

CORE_GENERATED = {
    "ming-settings.desktop": ("Ming 设置", "ming-control-center", "ming-settings", "Settings;System;"),
    "ming-files.desktop": ("文件", "ming-files", "ming-files", "System;FileManager;"),
    "ming-terminal.desktop": ("Ming 终端", "ming-terminal", "ming-terminal", "System;TerminalEmulator;"),
    "ming-firefox.desktop": ("Firefox ESR", "ming-firefox", "firefox-esr", "Network;WebBrowser;"),
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
DESKTOP_LABEL_FONT = "Noto Sans CJK SC Medium 10"
DRAG_THRESHOLD = 12
ACTIVATION_DEDUP_MS = 650
LAUNCH_FEEDBACK_TIMEOUT_MS = 4000
# Keep the status widget close to the top edge in both compact and expanded
# layouts; the desktop coordinator owns the remaining vertical spacing.
CLOCK_MARGIN_Y = 8
STATUS_WIDGET_EXPANDED_HEIGHT = 220
# Keep the expanded surface independent from the capsule allocation.  The
# popup is deliberately bounded so a long status string cannot move the
# desktop overlays or make the capsule grow between refreshes.
STATUS_WIDGET_EXPANDED_WIDTH = 330
STATUS_WIDGET_EXPANDED_PANEL_HEIGHT = STATUS_WIDGET_EXPANDED_HEIGHT
STATUS_WIDGET_TOP_GAP_MAX = 8


def status_widget_pid_file():
    """Return the per-session marker path without probing GTK at import time."""
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "ming-phone-desktop.pid"


def status_widget_top_gap_is_valid(content_y, card_y, max_gap=STATUS_WIDGET_TOP_GAP_MAX):
    """Keep compact and expanded widget content aligned to the card top."""
    try:
        gap = int(content_y) - int(card_y)
        return 0 <= gap <= int(max_gap)
    except (TypeError, ValueError):
        return False


WALLPAPER_PATHS = [
    Path("/usr/share/backgrounds/ming-os/default.png"),
    Path("/usr/share/backgrounds/ming-os/default-1366x768.png"),
    Path("/usr/share/backgrounds/ming-os/default.svg"),
]

CSS = b"""
window.ming-desktop {
  background-color: __MING_CANVAS__;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-weight: 400;
}
.tile {
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.34);
  border: 1px solid rgba(255, 255, 255, 0.54);
  box-shadow: 0 8px 22px rgba(21, 68, 56, 0.08), inset 0 1px 0 rgba(255,255,255,0.58);
  padding: 8px;
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
  font-weight: 500;
  letter-spacing: 0;
  text-shadow: 0 1px 0 rgba(255,255,255,0.82);
}
.folder-title {
  color: #1D2421;
  font-size: 18px;
  font-weight: 700;
}
.folder-panel {
  background: #FBFDFB;
  border: 1px solid rgba(31, 98, 84, 0.10);
  border-radius: 12px;
  padding: 16px;
}
.folder-action {
  border-radius: 9px;
  padding: 8px 12px;
}
.clock-widget {
  border-radius: 14px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.70);
  box-shadow: 0 12px 34px rgba(21, 68, 56, 0.12), inset 0 1px 0 rgba(255,255,255,0.75);
}
.clock-time {
  font-size: 26px;
  font-weight: 700;
  color: #17231F;
}
.clock-date {
  font-size: 11px;
  font-weight: 500;
  color: #2D695C;
}
.clock-battery {
  font-size: 10.5px;
  font-weight: 500;
  color: #517168;
}
.clock-subdate {
  font-size: 10px;
  font-weight: 400;
  color: #6A7670;
}
.status-widget {
  border-radius: 14px;
  padding: 8px 16px;
  background: __MING_SURFACE_ELEVATED__;
  border: 1px solid __MING_BORDER__;
  box-shadow: 0 12px 34px rgba(21, 68, 56, 0.12), inset 0 1px 0 rgba(255,255,255,0.78);
}
.status-widget-compact {
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
}
.status-compact-pill {
  min-height: 38px;
  border-radius: 21px;
  padding: 8px 14px;
  background: __MING_SURFACE__;
  border: 1px solid __MING_BORDER__;
  box-shadow: 0 8px 24px rgba(23, 72, 60, 0.14), inset 0 1px 0 rgba(255,255,255,0.84);
  color: #17231F;
}
.status-compact-pill:hover { background: #F4F8F5; }
.status-compact-time { font-size: 18px; font-weight: 700; color: #17231F; }
.status-compact-date { font-size: 10.5px; font-weight: 500; color: #2D695C; }
.status-compact-battery { font-size: 10.5px; font-weight: 500; color: #517168; }
.status-compact-arrow { font-size: 15px; font-weight: 700; color: #2F8A7D; }
.status-expanded-panel {
  min-width: 0;
  padding: 11px;
  border-radius: 14px;
  /* background: #FFFFFF is the opaque expanded surface before token expansion. */
  background: __MING_SURFACE__;
  border: 1px solid __MING_BORDER__;
  box-shadow: 0 18px 42px rgba(23, 72, 60, 0.22), inset 0 1px 0 rgba(255,255,255,0.88);
}
.status-expanded-title {
  color: __MING_ACCENT_STRONG__;
  font-size: 13px;
  font-weight: 700;
}
.status-resource-grid { margin: 1px 0 2px; }
.status-resource-card {
  min-width: 88px;
  padding: 9px 7px;
  border-radius: 12px;
  background: #EEF5F1;
  border: 1px solid rgba(47, 138, 125, 0.12);
}
.status-resource-name { color: #55766B; font-size: 10px; }
.status-resource-value { color: #245C50; font-size: 14px; font-weight: 700; }
.status-button {
  min-height: 34px;
  border-radius: 10px;
  padding: 5px 9px;
  background: __MING_SURFACE__;
  border: 1px solid __MING_BORDER__;
  color: __MING_TEXT__;
}
.status-button:hover { background: #F4F8F5; }
.status-button:focus, .status-compact-pill:focus {
  outline: 2px solid __MING_FOCUS__;
  outline-offset: 2px;
}
.ming-desktop-dark .clock-widget,
.ming-desktop-dark .status-widget {
  background: #202824;
  border-color: rgba(159, 231, 215, 0.18);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255,255,255,0.08);
}
.ming-desktop-dark .clock-time,
.ming-desktop-dark .status-compact-time {
  color: #E7EEE9;
}
.ming-desktop-dark .clock-date,
.ming-desktop-dark .clock-battery,
.ming-desktop-dark .clock-subdate,
.ming-desktop-dark .status-compact-date,
.ming-desktop-dark .status-compact-battery,
.ming-desktop-dark .launch-detail {
  color: #A9BDB5;
}
.ming-desktop-dark .status-compact-pill,
.ming-desktop-dark .status-button {
  background: #202824;
  border-color: rgba(159, 231, 215, 0.18);
  color: #E7EEE9;
}
.ming-desktop-dark .status-compact-arrow {
  color: #62C9B5;
}
.ming-desktop-dark .status-expanded-panel {
  background: #202824;
  border-color: rgba(159, 231, 215, 0.18);
}
.ming-desktop-dark .status-expanded-title { color: #62C9B5; }
.ming-desktop-dark .status-resource-card { background: #29352F; }
.ming-desktop-dark .status-resource-name { color: #A9BDB5; }
.ming-desktop-dark .status-resource-value { color: #E7EEE9; }
.status-scale trough {
  min-height: 8px;
  border-radius: 5px;
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
.status-scale:focus {
  outline: 2px solid __MING_FOCUS__;
  outline-offset: 2px;
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
.notification-panel { border-radius: 12px; border: 1px solid rgba(47, 138, 125, 0.14); }
.notification-title { font-weight: 700; color: #17231F; }
.notification-body { color: #596760; font-size: 10px; font-weight: 400; }
.ming-desktop-dark .notification-panel {
  background: #202824;
  border-color: rgba(159, 231, 215, 0.18);
}
.ming-desktop-dark .notification-title { color: #E7EEE9; }
.ming-desktop-dark .notification-body { color: #A9BDB5; }
.launch-feedback {
  border-radius: 14px;
  padding: 12px 16px;
  background: #FCFEFC;
  border: 1px solid rgba(47, 138, 125, 0.16);
  box-shadow: 0 14px 36px rgba(21, 68, 56, 0.16);
}
.launch-title { color: #17231F; font-size: 14px; font-weight: 700; }
.launch-detail { color: #5B6963; font-size: 10.5px; font-weight: 400; }
"""


def log(msg):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(datetime.datetime.now().strftime("[%F %T] ") + msg + "\n")
    except Exception:
        pass


def render_ui_css(source):
    """Resolve shared visual tokens only when the GTK session starts."""
    try:
        loaded = load_ui_tokens()
        if isinstance(loaded, dict):
            TOKENS.update(loaded)
    except Exception as exc:
        log("shared UI tokens unavailable: %s" % exc)
    rendered = source
    for name, value in TOKENS.items():
        rendered = rendered.replace(
            ("__MING_%s__" % name.upper()).encode("ascii"),
            str(value).encode("ascii"),
        )
    return rendered


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
    import configparser
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
    source_resolver = globals().get("managed_desktop_source_path")
    source_path = source_resolver(path) if callable(source_resolver) else None
    effective_path = source_path or Path(path)
    diagnose = getattr(COMMON, "diagnose_desktop_file", None)
    if not callable(diagnose):
        legacy = legacy_desktop_entry(effective_path)
        if legacy is None:
            return None
        return {
            "id": app_id(effective_path),
            "type": "app",
            "path": str(effective_path),
            "basename": effective_path.name,
            "name": legacy["name"],
            "icon": legacy["icon"],
            "categories": legacy["categories"],
            "legacy_argv": legacy["argv"],
            "diagnostic": legacy["diagnostic"],
        }
    try:
        entry = diagnose(effective_path)
    except (OSError, ValueError):
        return None
    if entry is None:
        return None
    return {
        "id": app_id(effective_path),
        "type": "app",
        "path": str(effective_path),
        "source_path": str(source_path) if source_path else "",
        "basename": effective_path.name,
        "name": entry.name or effective_path.stem,
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
        log(f"shared launcher validation unavailable; delegating to launch proxy for {path}")
        return launch_through_proxy(path, source_rect)
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
    log(f"launch broker unavailable; delegating restart and diagnostics for {path}")
    return launch_through_proxy(path, source_rect)


def launch_through_proxy(path, source_rect=None):
    command = [LAUNCH_PROXY, "--desktop-file", str(path), "--source", "desktop"]
    if source_rect is not None:
        command.extend(("--rect", json.dumps(source_rect, separators=(",", ":"))))
    try:
        subprocess.Popen(command, shell=False)
        return True
    except Exception as exc:
        log(f"launch proxy failed for {path}: {exc}")
    return False


def write_generated_core_launcher(basename):
    data = CORE_GENERATED.get(basename)
    if not data:
        return None
    name, exec_cmd, icon, categories = data
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    path = DESKTOP_DIR / basename
    managed = False
    if path.exists():
        try:
            managed = DESKTOP_MANAGED_MARKER in path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            managed = False
    if not path.exists() or managed:
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
    key = str(Path(item["path"]))
    if key in apps_by_basename:
        return False
    apps_by_basename[key] = item
    return True


def add_core_app(apps_by_basename, basename):
    # The system desktop file is the canonical source. A managed desktop copy
    # is refreshed from it; user-created launchers remain untouched.
    candidates = [Path("/usr/share/applications") / basename, DESKTOP_DIR / basename]
    candidates.extend(Path("/usr/share/applications") / alt for alt in CORE_FALLBACKS.get(basename, []))
    for candidate in candidates:
        if add_app_from_path(apps_by_basename, candidate):
            return True
    generated = write_generated_core_launcher(basename)
    if generated:
        return add_app_from_path(apps_by_basename, generated)
    return False


def canonical_identity(app):
    return layout_item_identity(app)


def normalized_desktop_exec_program(exec_line):
    """Return a stable main program and fixed arguments for identity checks."""
    if not isinstance(exec_line, str) or not exec_line.strip() or "\x00" in exec_line:
        return "", ()
    if any(marker in exec_line for marker in (";", "`", "$(", "\n", "\r")):
        return "", ()
    try:
        raw = shlex.split(exec_line, posix=True)
    except ValueError:
        return "", ()
    if not raw or any(token in {"|", "||", "&&", ">", ">>", "<"} for token in raw):
        return "", ()
    argv = []
    for token in raw:
        token = token.replace("%%", "\x00")
        token = re.sub(r"%[fFuUdDnNickvm]", "", token).replace("\x00", "%")
        if token:
            argv.append(token)
    if not argv:
        return "", ()
    offset = 0
    if Path(argv[0]).name.casefold() == "env":
        offset = 1
        while offset < len(argv) and (argv[offset].startswith("-") or "=" in argv[offset]):
            offset += 1
    if offset >= len(argv):
        return "", ()
    program = argv[offset]
    if Path(program).name.casefold() in {"sh", "bash", "dash", "zsh", "ksh"}:
        if "-c" in argv[offset + 1:]:
            return "", ()
    normalized = str(Path(program))
    if program.startswith("/"):
        normalized = str(Path(program).resolve(strict=False))
    return normalized.casefold(), tuple(argv[offset + 1:])


def desktop_entry_dedup_fields(path):
    """Read non-display launcher fields; names are intentionally excluded."""
    import configparser
    target = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        if target.stat().st_size > 256 * 1024:
            return None
        parser.read(target, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    if section.get("Type", "Application").strip().casefold() != "application":
        return None
    program, arguments = normalized_desktop_exec_program(section.get("Exec", ""))
    if not program:
        return None
    return {
        "exec_program": program,
        "exec_arguments": arguments,
        "startup_wm_class": section.get("StartupWMClass", "").strip().casefold(),
    }


def desktop_entry_package_owners(paths, command_runner=None):
    """Resolve package ownership in one bounded query for preference/grouping."""
    candidates = tuple(dict.fromkeys(str(Path(path)) for path in paths if path))
    if not candidates:
        return {}
    runner = command_runner or subprocess.run
    try:
        completed = runner(
            ["dpkg-query", "-S", "--", *candidates[:512]],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            shell=False,
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return {}
    owners = {}
    candidate_set = set(candidates)
    for line in (completed.stdout or "").splitlines():
        owner, separator, owned_path = line.partition(": ")
        if separator and owner.strip() and owned_path in candidate_set:
            owners.setdefault(owned_path, set()).add(owner.strip())
    return {
        path: next(iter(found))
        for path, found in owners.items()
        if len(found) == 1
    }


def third_party_app_identity(app, package_owners=None):
    """Group third-party entries only when launch-critical identity agrees."""
    path = str(app.get("path") or "")
    source_path = str(app.get("source_path") or "")
    identity_path = source_path or path
    fields = desktop_entry_dedup_fields(identity_path)
    if not fields:
        return ("path", os.path.normpath(identity_path))
    program = fields["exec_program"]
    arguments = fields["exec_arguments"]
    wm_class = fields["startup_wm_class"]
    if wm_class:
        return ("launcher", program, arguments, wm_class)
    owner = str(app.get("package_owner") or (
        (package_owners or {}).get(identity_path, "")
        or (package_owners or {}).get(path, "")
    )).strip().casefold()
    if owner:
        return ("package-launcher", owner, program, arguments)
    return ("path", os.path.normpath(identity_path))


def app_dedup_preference(app, package_owners=None):
    path = str(app.get("path") or "")
    launchable = not bool(app.get("diagnostic"))
    system_entry = is_system_application_path(path)
    package_owned = bool(str(app.get("package_owner") or (
        (package_owners or {}).get(path, "")
    )).strip())
    return (
        int(system_entry and package_owned and launchable),
        int(system_entry and launchable),
        int(launchable),
        int(system_entry and package_owned),
    )


def deduplicate_apps(apps):
    apps = list(apps)
    owner_paths = [
        app.get("path")
        for app in apps
        if (
            canonical_identity(app) not in CANONICAL_PREFERENCE
            and is_system_application_path(app.get("path"))
            and not app.get("package_owner")
        )
    ]
    package_owners = desktop_entry_package_owners(owner_paths)
    selected = {}
    for app in apps:
        core_identity = canonical_identity(app)
        preferred = CANONICAL_PREFERENCE.get(core_identity)
        if core_identity == "agent" and preferred:
            preferred_path = Path(SYSTEM_APPLICATION_DIR) / preferred
            legacy_path = Path(SYSTEM_APPLICATION_DIR) / "papyrus.desktop"
            if not preferred_path.is_file() and legacy_path.is_file():
                preferred = legacy_path.name
        identity = core_identity if preferred else third_party_app_identity(
            app, package_owners
        )
        current = selected.get(identity)
        if current is None:
            selected[identity] = app
            continue
        if preferred:
            current_is_preferred = current["basename"].casefold() == preferred
            app_is_preferred = app["basename"].casefold() == preferred
            if app_is_preferred and not current_is_preferred:
                selected[identity] = app
            elif app_is_preferred == current_is_preferred and (
                app_dedup_preference(app, package_owners)
                > app_dedup_preference(current, package_owners)
            ):
                selected[identity] = app
        elif app_dedup_preference(app, package_owners) > app_dedup_preference(
            current, package_owners
        ):
            selected[identity] = app
    return list(selected.values())


def load_apps(default_only=False):
    apps_by_basename = {}
    if default_only:
        for basename in sorted(CORE_NAMES, key=lambda name: DESKTOP_ORDER.get(name, 999)):
            add_core_app(apps_by_basename, basename)
    for directory in APP_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            if is_legacy_xfce_entry(path):
                continue
            add_app_from_path(apps_by_basename, path, default_only=default_only)
    apps = deduplicate_apps(list(apps_by_basename.values()))
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


def layout_item_identity(item):
    """Group only known core launchers; unrelated user entries stay distinct."""
    path = str(item.get("path") or "")
    effective_path = layout_effective_path(item)
    # Managed Desktop copies of core launchers intentionally live outside
    # /usr/share/applications.  Their basename is still a closed allowlist,
    # so recognize it before falling back to an individual path identity.
    for candidate in (effective_path, Path(path)):
        trusted_location = (
            is_system_application_path(candidate)
            or candidate.parent == DESKTOP_DIR
            or candidate.parent == HOME / ".local/share/applications"
        )
        if not trusted_location:
            continue
        basename = candidate.name.casefold()
        family = CANONICAL_LAUNCHERS.get(basename)
        if family:
            return family
    return "path:" + os.path.normpath(path)


def is_system_application_path(path):
    try:
        target = Path(path)
        return target.is_absolute() and target.parent == Path(SYSTEM_APPLICATION_DIR)
    except (TypeError, ValueError):
        return False


def layout_effective_path(item):
    path = Path(str(item.get("path") or ""))
    resolver = globals().get("managed_desktop_source_path")
    source = resolver(path) if callable(resolver) else None
    return source or path


def retired_layout_item(item):
    """Identify launchers removed from Ming OS so backups cannot revive them."""
    effective_path = layout_effective_path(item)
    if not is_system_application_path(effective_path):
        return False
    stem = effective_path.stem.casefold()
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", stem)))
    return (
        "edge" in tokens and bool({"microsoft", "ming"} & tokens)
    ) or (
        "claw" in tokens and bool({"garlic", "open"} & tokens)
    ) or (
        stem == "open" + "claw"
    )


def deduplicate_layout_items(items):
    """Prefer managed proxy placement, while storing the current system path."""
    preferred_core = {}
    preferred_noncore = {}
    source_resolver = globals().get("managed_desktop_source_path")
    layout_paths = []

    for item in items:
        if item.get("type") == "folder":
            layout_paths.extend(str(child) for child in item.get("children", []) if child)
        elif item.get("path"):
            layout_paths.append(str(item.get("path")))
    owner_resolver = globals().get("desktop_entry_package_owners")
    identity_resolver = globals().get("third_party_app_identity")
    preference_resolver = globals().get("app_dedup_preference")
    package_owners = owner_resolver(layout_paths) if callable(owner_resolver) else {}

    def noncore_identity(item):
        if callable(identity_resolver):
            return identity_resolver(item, package_owners)
        return ("path", os.path.normpath(str(item.get("path") or "")))

    def noncore_preference(item):
        if callable(preference_resolver):
            return preference_resolver(item, package_owners)
        return (0, 0, int(not bool(item.get("diagnostic"))), 0)

    def consider(item, token):
        if retired_layout_item(item):
            return
        identity = layout_item_identity(item)
        if identity not in CANONICAL_PREFERENCE:
            return
        path = item.get("path")
        managed_source = source_resolver(path) if path and callable(source_resolver) else None
        priority = 1 if managed_source is not None else 0
        current = preferred_core.get(identity)
        if current is None or priority > current[0]:
            preferred_core[identity] = (priority, token)

    def consider_noncore(item, token):
        if retired_layout_item(item):
            return
        if layout_item_identity(item) in CANONICAL_PREFERENCE:
            return
        identity = noncore_identity(item)
        priority = noncore_preference(item)
        current = preferred_noncore.get(identity)
        if current is None or priority > current[0]:
            preferred_noncore[identity] = (priority, token)

    for item_index, item in enumerate(items):
        if item.get("type") == "folder":
            for child_index, child in enumerate(item.get("children", [])):
                child_item = {"type": "app", "path": str(child)}
                token = ("child", item_index, child_index)
                consider(child_item, token)
                consider_noncore(child_item, token)
        else:
            token = ("item", item_index)
            consider(item, token)
            consider_noncore(item, token)

    deduplicated = []
    known_noncore = set()
    for item_index, item in enumerate(items):
        if item.get("type") == "folder":
            folder = dict(item)
            children = []
            for child_index, child in enumerate(item.get("children", [])):
                child_item = {"path": str(child)}
                if retired_layout_item(child_item):
                    continue
                identity = layout_item_identity(child_item)
                if identity in CANONICAL_PREFERENCE:
                    if preferred_core.get(identity, (None, None))[1] != ("child", item_index, child_index):
                        continue
                    managed_source = source_resolver(child) if callable(source_resolver) else None
                    children.append(str(managed_source or child))
                else:
                    identity = noncore_identity(child_item)
                    if preferred_noncore.get(identity, (None, None))[1] != ("child", item_index, child_index):
                        continue
                    if identity in known_noncore:
                        continue
                    known_noncore.add(identity)
                    children.append(str(child))
            folder["children"] = children
            deduplicated.append(folder)
            continue
        if retired_layout_item(item):
            continue
        identity = layout_item_identity(item)
        if identity in CANONICAL_PREFERENCE:
            if preferred_core.get(identity, (None, None))[1] != ("item", item_index):
                continue
            normalized = dict(item)
            path = item.get("path")
            managed_source = source_resolver(path) if path and callable(source_resolver) else None
            if managed_source is not None:
                normalized["path"] = str(managed_source)
            deduplicated.append(normalized)
        else:
            identity = noncore_identity(item)
            if preferred_noncore.get(identity, (None, None))[1] != ("item", item_index):
                continue
            if identity in known_noncore:
                continue
            known_noncore.add(identity)
            deduplicated.append(item)
    return deduplicated


def canonicalize_core_layout_item(item, canonical_by_identity, seen):
    """Move a managed core proxy to its current system launcher and keep placement."""
    if not isinstance(item, dict) or item.get("type") == "folder":
        return dict(item) if isinstance(item, dict) else item
    identity = layout_item_identity(item)
    canonical = canonical_by_identity.get(identity)
    if identity not in CANONICAL_PREFERENCE or not isinstance(canonical, dict):
        return dict(item)
    if identity in seen:
        return None
    seen.add(identity)
    restored = dict(canonical)
    restored["id"] = item.get("id") or restored.get("id") or app_id(restored.get("path"))
    restored["x"] = item.get("x", PAD_X)
    restored["y"] = item.get("y", PAD_Y)
    restored["pinned"] = bool(item.get("pinned", False))
    return restored


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
    migrated["items"] = deduplicate_layout_items(items)
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
    # An empty catalog is the first-run marker, not an initialized snapshot;
    # only a non-empty prior catalog can identify newly installed apps.
    catalog_is_initialized = isinstance(previous_catalog, list) and bool(previous_catalog)
    previous_catalog = {
        str(path) for path in previous_catalog
        if isinstance(path, (str, os.PathLike))
    } if catalog_is_initialized else set()
    apps_by_path = {str(app["path"]): app for app in apps}
    canonical_core_apps = {
        identity: app
        for app in apps
        for identity in (layout_item_identity(app),)
        if identity in CANONICAL_PREFERENCE
    }
    core_seen = set()
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
                    if is_legacy_xfce_entry(child_path):
                        continue
                    canonical_child = canonicalize_core_layout_item(
                        {"type": "app", "path": child_path}, canonical_core_apps, core_seen)
                    if canonical_child is None:
                        continue
                    child = read_app(canonical_child.get("path"))
                    if child:
                        children.append(child["path"])
                folder["children"] = children
                folder["pinned"] = True
                items.append(folder)
                known.update(layout_item_identity({"path": child}) for child in children)
        elif item.get("path"):
            if is_legacy_xfce_entry(item.get("path")):
                continue
            item = canonicalize_core_layout_item(item, canonical_core_apps, core_seen)
            if item is None:
                continue
            path = str(item["path"])
            basename = Path(path).name
            fresh = apps_by_path.get(path)
            if fresh or basename in CORE_NAMES or item.get("pinned"):
                restored = dict(fresh or item)
                restored["id"] = item.get("id") or restored.get("id") or app_id(path)
                restored["x"] = item.get("x", PAD_X)
                restored["y"] = item.get("y", PAD_Y)
                restored["pinned"] = bool(item.get("pinned", False))
                identity = layout_item_identity(restored)
                if identity in known:
                    continue
                items.append(restored)
                known.add(identity)
    index = len(items)
    existing_basenames = {
        Path(str(item.get("path"))).name.casefold()
        for item in items
        if item.get("path")
    }
    for item in items:
        existing_basenames.update(
            Path(str(child)).name.casefold()
            for child in item.get("children", [])
            if isinstance(child, (str, os.PathLike))
        )
    newly_installed_paths = []
    for app in visible_apps:
        identity = layout_item_identity(app)
        if identity in known or (
                (not catalog_is_initialized and items)
                or
                catalog_is_initialized
                and app["basename"].casefold() in existing_basenames):
            continue
        app["x"], app["y"] = next_position(index, width)
        # Keep the user's layout semantics while marking newly discovered
        # applications for a desktop launcher refresh after installation.
        app["pinned"] = False
        if catalog_is_initialized and app["basename"] not in CORE_NAMES:
            newly_installed_paths.append(str(app["path"]))
        items.append(app)
        known.add(identity)
        index += 1
    layout["version"] = LAYOUT_VERSION
    layout["items"] = items
    layout["catalog_paths"] = sorted(catalog_paths)
    layout["newly_installed_paths"] = sorted(newly_installed_paths)
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


def desktop_entry_identity_fields(path):
    """Read launch-critical fields used to verify that a proxy is still unchanged."""
    target = Path(path)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        if target.stat().st_size > 256 * 1024:
            return None
        parser.read(target, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    entry_type = section.get("Type", "Application").strip().casefold()
    exec_line = section.get("Exec", "").strip()
    try_exec = section.get("TryExec", "").strip()
    if not exec_line:
        return None
    return entry_type, exec_line, try_exec


def legacy_managed_source_path(path):
    """Infer the source of an unchanged pre-source-marker Ming desktop copy."""
    target = Path(path)
    family = CANONICAL_LAUNCHERS.get(target.name.casefold())
    canonical_basename = CANONICAL_PREFERENCE.get(family)
    if family == "agent" and canonical_basename:
        preferred_path = Path(SYSTEM_APPLICATION_DIR) / canonical_basename
        legacy_path = Path(SYSTEM_APPLICATION_DIR) / "papyrus.desktop"
        if not preferred_path.is_file() and legacy_path.is_file():
            canonical_basename = legacy_path.name
    if not canonical_basename:
        return None
    source = Path(SYSTEM_APPLICATION_DIR) / canonical_basename
    if not source.is_file():
        return None
    target_fields = desktop_entry_identity_fields(target)
    source_fields = desktop_entry_identity_fields(source)
    if target_fields is None or target_fields != source_fields:
        return None
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(Path(SYSTEM_APPLICATION_DIR).resolve())
    except (OSError, ValueError):
        return None
    return resolved


def managed_desktop_source_path(path):
    """Resolve only a Ming-managed Desktop proxy to a current system launcher."""
    target = Path(path)
    if not _desktop_has_marker(target):
        return None
    try:
        target.resolve().relative_to(Path(DESKTOP_DIR).resolve())
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return None
    match = re.search(
        r"(?im)^\s*{}\s*=\s*(.*?)\s*$".format(re.escape(DESKTOP_SOURCE_MARKER)), text)
    if match is None:
        return legacy_managed_source_path(target)
    value = match.group(1).strip()
    if value and "\x00" not in value:
        source = Path(value)
        if is_system_application_path(source) and source.is_file():
            try:
                resolved = source.resolve(strict=True)
                resolved.relative_to(Path(SYSTEM_APPLICATION_DIR).resolve())
            except (OSError, ValueError):
                return None
            target_fields = desktop_entry_identity_fields(target)
            source_fields = desktop_entry_identity_fields(resolved)
            if target_fields is not None and target_fields == source_fields:
                return resolved
    return None


def write_desktop_source_marker(path, source):
    """Record the protected system launcher represented by a generated copy."""
    target = Path(path)
    source = Path(source)
    if not is_system_application_path(source) or not source.is_file():
        return False
    try:
        source = source.resolve(strict=True)
        source.relative_to(Path(SYSTEM_APPLICATION_DIR).resolve())
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return False
    text = re.sub(
        r"(?im)^\s*{}\s*=.*(?:\r?\n|$)".format(re.escape(DESKTOP_SOURCE_MARKER)),
        "",
        text,
    )
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
    text = text[:insert_at] + f"{DESKTOP_SOURCE_MARKER}={source}\n" + text[insert_at:]
    try:
        target.write_text(text, encoding="utf-8")
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
        if managed and is_system_application_path(src):
            if not write_desktop_source_marker(target, src):
                try:
                    target.unlink()
                except OSError:
                    pass
                return None
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
        elif item.get("path") and (
                Path(item["path"]).name in CORE_NAMES
                or item.get("pinned")
                or str(item["path"]) in set(layout.get("newly_installed_paths", []))):
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
        "ming-firefox": "firefox",
        "ming-store": "ming-store",
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
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.get_style_context().add_class("launch-feedback")
        self.icon = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(34)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = Gtk.Label(label="正在打开")
        self.title.set_halign(Gtk.Align.START)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(20)
        self.title.get_style_context().add_class("launch-title")
        self.detail = Gtk.Label(label="请稍候…")
        self.detail.set_halign(Gtk.Align.START)
        self.detail.set_line_wrap(True)
        self.detail.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.detail.set_lines(2)
        self.detail.set_ellipsize(Pango.EllipsizeMode.END)
        self.detail.set_max_width_chars(20)
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
        icon_path = COMMON.resolve_icon_path(item.get("icon"), item.get("path", ""))
        if icon_path:
            self.icon.set_from_file(icon_path)
        else:
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
            | Gdk.EventMask.KEY_PRESS_MASK
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
        self.confirmed_value = None

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
        self.confirmed_value = value
        return True

    def should_hold_status(self):
        return self.pending


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
        super().__init__(title="Ming 桌面")
        # The GTK3/X11 taskbar owns system status controls in the new shell.
        self.taskbar_mode = os.environ.get("MING_TASKBAR_MODE", "0") == "1"
        try:
            READY_MARKER.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        self.set_name("ming-desktop-window")
        self.get_style_context().add_class("ming-desktop")
        if os.environ.get("MING_LOW_RESOURCE") == "1":
            self.get_style_context().add_class("ming-low-resource")
        if os.environ.get("MING_REDUCED_MOTION") == "1":
            self.get_style_context().add_class("ming-reduced-motion")
        self.appearance_monitor = None
        self.apply_desktop_theme(load_appearance_theme())
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
        self.connect("destroy", self.on_destroy)
        # The taskbar owns system status controls; no floating status widget is created.
        # Gtk.Fixed and transparent EventBox children can be no-window widgets
        # on the VirtualBox/Xrender path.  Receive root-window events as the
        # final, renderer-independent desktop input route.
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("button-press-event", self.on_window_button_press)
        self.connect("motion-notify-event", self.on_window_motion)
        self.connect("button-release-event", self.on_window_button_release)
        self.connect("touch-event", self.on_window_touch)
        self.connect("key-press-event", self.on_key_press)
        try:
            signal.signal(signal.SIGUSR1, self._on_toggle_signal)
        except (AttributeError, ValueError):
            log("status widget signal toggle unavailable")

        provider = Gtk.CssProvider()
        provider.load_from_data(render_ui_css(CSS))
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
        self._context_menu = None
        self.last_context_result = None
        self.launch_feedback = LaunchFeedbackOverlay()
        self.launch_feedback.set_sensitive(False)
        self.connect("map-event", lambda *_args: self.enforce_desktop_layer())
        self.connect("size-allocate", lambda *_args: self.place_overlays())
        self.layout = sync_layout(screen_w)
        self.layout_stamp = self.current_layout_stamp()
        self.catalog_stamp = app_catalog_fingerprint()
        self.render()
        self.watch_appearance_theme()
        GLib.timeout_add_seconds(2, self.mark_ready)
        # Ming Store and package installers trigger refresh_desktop immediately. This
        # timer is only a bounded fallback for changes made outside Ming tools.
        GLib.timeout_add_seconds(15, self.refresh_if_apps_changed)

    def on_destroy(self, *_args):
        pid_file = status_widget_pid_file()
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink()
        except (OSError, ValueError):
            pass
        Gtk.main_quit()

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

    def apply_desktop_theme(self, theme):
        dark = theme == "dark" or (theme == "system" and system_prefers_dark())
        style = self.get_style_context()
        if dark:
            style.add_class("ming-desktop-dark")
        else:
            style.remove_class("ming-desktop-dark")
        return False

    def watch_appearance_theme(self):
        path = appearance_config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            monitor = Gio.File.new_for_path(str(path)).monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self.on_appearance_theme_changed)
            self.appearance_monitor = monitor
        except Exception as exc:
            log("could not watch appearance theme: %s" % exc)

    def on_appearance_theme_changed(self, *_args):
        GLib.idle_add(self.apply_desktop_theme, load_appearance_theme())

    def mark_ready(self):
        try:
            READY_MARKER.parent.mkdir(parents=True, exist_ok=True)
            READY_MARKER.write_text(datetime.datetime.now().isoformat(), encoding="utf-8")
        except Exception:
            pass
        return False

    def toggle_status_widget(self):
        return False

    def _on_toggle_signal(self, _signum, _frame):
        # Python signal handlers run on the GTK thread boundary; schedule the
        # actual widget mutation through GLib so GTK never sees a foreign call.
        GLib.idle_add(self.toggle_status_widget)

    def on_key_press(self, _window, event):
        if self.taskbar_mode:
            return False
        if is_status_widget_toggle_key(getattr(event, "keyval", 0)):
            self.toggle_status_widget()
            return True
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
            fallback_icon = "application-x-executable"
            pixbuf = None
            icon_path = None if is_folder else COMMON.resolve_icon_path(icon_name, item.get("path", ""))
            if icon_path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        icon_path, ICON_SIZE, ICON_SIZE, True)
                except Exception:
                    pixbuf = None
            for candidate in (icon_name, fallback_icon):
                if pixbuf:
                    break
                try:
                    pixbuf = icon_theme.load_icon(candidate, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
                    break
                except Exception:
                    continue
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
            layout.set_font_description(Pango.FontDescription(DESKTOP_LABEL_FONT))
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
        for overlay in (self.launch_feedback,):
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
        self.fixed_press_button = getattr(event, "button", 0)
        self.fixed_press_origin = (x, y)
        if item:
            item_x, item_y = self.item_position(item)
            self.fixed_press_offset = (x - item_x, y - item_y)
        else:
            self.fixed_press_offset = (0, 0)
        self.fixed_press_moved = False
        if not item:
            # Keep the root surface as the event owner so the matching
            # button-release can open the desktop context menu.
            return True
        # Consume the press on the root surface as well as on a tile.  GTK can
        # otherwise route a blank-area right click to the window below us and
        # the release event never reaches the desktop context-menu handler.
        return True

    def on_fixed_motion(self, _widget, event):
        if not self.fixed_press_origin or self.fixed_press_button != 1:
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
        self.fixed_press_button = None
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

    def _desktop_action_result(self, action, callback):
        """Run one desktop action and return a user-visible result record."""
        try:
            value = callback()
            return {
                "ok": True,
                "action": str(action),
                "path": str(value) if value is not None else "",
                "message": "操作已完成。",
            }
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            result = {
                "ok": False,
                "action": str(action),
                "path": "",
                "message": "%s失败：%s" % (action, exc),
            }
            log("desktop action failed: %s" % json.dumps(result, ensure_ascii=False))
            return result

    def _show_context_error(self, result):
        self.last_context_result = result
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="桌面操作未完成",
        )
        dialog.format_secondary_text(str(result.get("message") or "请稍后重试。"))
        dialog.run()
        dialog.destroy()
        return False

    def _on_context_menu_deactivate(self, menu):
        if self._context_menu is menu:
            self._context_menu = None
        return False

    def show_desktop_context_menu(self, event):
        menu = Gtk.Menu()
        self._context_menu = menu
        menu.connect("deactivate", self._on_context_menu_deactivate)
        # The canonical action is ``ming-app-drawer --toggle``; keep argv
        # structured so paths and arguments are never shell-interpreted.
        actions = (
            ("添加到桌面", lambda: subprocess.Popen(["ming-app-drawer", "--toggle"], shell=False)),
            ("新建空白文件", self._create_blank_desktop_file),
            ("新建文件夹", self._create_desktop_folder),
            ("刷新桌面", self.refresh_desktop),
            ("打开应用抽屉", lambda: subprocess.Popen(["ming-app-drawer", "--toggle"], shell=False)),
            ("Ming 设置", lambda: subprocess.Popen(["ming-control-center"])),
            ("终端", lambda: subprocess.Popen(["ming-terminal"])),
        )
        for label, callback in actions:
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda _item, action=callback: action())
            menu.append(entry)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _create_blank_desktop_file(self):
        result = self._desktop_action_result("新建空白文件", create_blank_desktop_file)
        if result["ok"]:
            self.refresh_desktop()
        else:
            self._show_context_error(result)
        return result

    def _create_desktop_folder(self):
        result = self._desktop_action_result("新建文件夹", create_desktop_folder)
        if result["ok"]:
            self.refresh_desktop()
        else:
            self._show_context_error(result)
        return result

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
        if not self.launch_feedback.item:
            self.launch_feedback.hide()
        self.enforce_desktop_layer()

    def place_overlays(self):
        screen_w = max(320, self.get_screen().get_width())
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
        catalog_stamp = app_catalog_fingerprint()
        layout_changed = stamp != self.layout_stamp
        catalog_changed = catalog_stamp != self.catalog_stamp
        if not layout_changed and not catalog_changed:
            return True
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
        origin_x, origin_y = self.window_origin
        source_rect = {
            "x": origin_x + int(item.get("x", PAD_X)),
            "y": origin_y + int(item.get("y", PAD_Y)),
            "width": TILE_W,
            "height": TILE_H,
        }
        if not launch_item(item, source_rect):
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
        self._context_menu = menu
        menu.connect("deactivate", self._on_context_menu_deactivate)
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
