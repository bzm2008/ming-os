#!/usr/bin/env python3
"""Shared, dependency-free primitives for Ming shell helpers."""

import configparser
import json
import io
import math
import os
import pathlib
import re
import secrets
import shlex
import shutil
import socket
import stat
import struct
import subprocess
import threading
import time


MAX_DESKTOP_BYTES = 256 * 1024
MAX_JSON_LINE_BYTES = 64 * 1024
MAX_ICON_BYTES = 8 * 1024 * 1024
ICON_EXTENSIONS = {".png", ".svg"}
_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")
_SHELL_NAME = {"sh", "bash", "dash", "zsh", "ksh"}
_LAUNCH_REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
_DEBIAN_PACKAGE_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9+.-]*(?::[A-Za-z0-9][A-Za-z0-9+.-]*)?")
DEFAULT_LAUNCH_REQUEST_TIMEOUT = 0.4
ASYNC_LAUNCH_REQUEST_TIMEOUT = 12.0
MAX_LAUNCH_REQUEST_TIMEOUT = 15.0
BROKER_RECOVERY_TIMEOUT = 3.0
BROKER_RECOVERY_INTERVAL = 0.05


class Rect:
    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x, y, width, height):
        values = tuple(float(value) for value in (x, y, width, height))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("rectangle values must be finite")
        if values[2] <= 0 or values[3] <= 0:
            raise ValueError("rectangle dimensions must be positive")
        if values[2] > 100000 or values[3] > 100000:
            raise ValueError("rectangle dimensions are unreasonable")
        self.x, self.y, self.width, self.height = values

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict):
            raise ValueError("rectangle must be an object")
        try:
            return cls(value["x"], value["y"], value["width"], value["height"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid rectangle") from exc

    @property
    def bottom_center(self):
        return self.x + self.width / 2.0, self.y + self.height

    def to_dict(self):
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


class DesktopEntry:
    __slots__ = ("path", "name", "comment", "icon", "argv", "categories", "diagnostic")

    def __init__(self, path, name, comment, icon, argv, categories, diagnostic=""):
        self.path = pathlib.Path(path)
        self.name = name
        self.comment = comment
        self.icon = icon
        self.argv = tuple(argv)
        self.categories = tuple(categories)
        self.diagnostic = str(diagnostic or "")


class CommandResult:
    __slots__ = ("argv", "returncode", "stdout", "stderr", "timed_out")

    def __init__(self, argv, returncode, stdout="", stderr="", timed_out=False):
        self.argv = tuple(argv)
        self.returncode = int(returncode)
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.timed_out = bool(timed_out)


class InstanceAlreadyRunning(RuntimeError):
    pass


class LaunchRequestResult:
    """The bounded broker result, with legacy boolean compatibility."""

    __slots__ = ("state", "error")

    def __init__(self, state, error=""):
        if state not in {"accepted", "rejected", "unavailable"}:
            raise ValueError("invalid launch request state")
        self.state = state
        self.error = str(error or "")[:512]

    @property
    def accepted(self):
        return self.state == "accepted"

    @property
    def rejected(self):
        return self.state == "rejected"

    @property
    def unavailable(self):
        return self.state == "unavailable"

    def __bool__(self):
        return self.accepted


def new_launch_request_id():
    return secrets.token_hex(16)


def is_launch_request_id(value):
    return isinstance(value, str) and bool(_LAUNCH_REQUEST_ID.fullmatch(value))


def launch_result_message(request_id, accepted, error=""):
    if not is_launch_request_id(request_id) or not isinstance(accepted, bool):
        raise ValueError("invalid launch result")
    message = {
        "version": 1,
        "action": "launch-result",
        "request_id": request_id,
        "accepted": accepted,
    }
    if not accepted:
        message["error"] = str(error or "launch request was rejected").replace("\x00", " ")[:512]
    return message


def parse_launch_result(message, request_id):
    if not is_launch_request_id(request_id) or not isinstance(message, dict):
        return LaunchRequestResult("rejected", "invalid broker response")
    if not set(message).issubset({"version", "action", "request_id", "accepted", "error"}):
        return LaunchRequestResult("rejected", "invalid broker response")
    if (
            message.get("version") != 1
            or message.get("action") != "launch-result"
            or message.get("request_id") != request_id
            or not isinstance(message.get("accepted"), bool)):
        return LaunchRequestResult("rejected", "invalid broker response")
    if message["accepted"]:
        return LaunchRequestResult("accepted")
    error = message.get("error", "launch request was rejected")
    if not isinstance(error, str):
        error = "launch request was rejected"
    return LaunchRequestResult("rejected", error)


def _safe_icon_dimensions(path, head, max_dimension=4096, max_pixels=16 * 1024 * 1024):
    suffix = path.suffix.lower()
    if suffix == ".png":
        if len(head) < 24 or not head.startswith(b"\x89PNG\r\n\x1a\n") or head[12:16] != b"IHDR":
            return False
        width, height = struct.unpack(">II", head[16:24])
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")[:65536]
        except (OSError, UnicodeError):
            return False
        root = re.search(r"<svg\b([^>]*)>", text, re.I | re.S)
        if not root:
            return False
        attrs = root.group(1)
        width_match = re.search(r"\bwidth\s*=\s*['\"]\s*([0-9]+(?:\.[0-9]+)?)", attrs, re.I)
        height_match = re.search(r"\bheight\s*=\s*['\"]\s*([0-9]+(?:\.[0-9]+)?)", attrs, re.I)
        viewbox = re.search(
            r"\bviewBox\s*=\s*['\"]\s*[-+0-9.eE]+[ ,]+[-+0-9.eE]+[ ,]+([0-9.eE+]+)[ ,]+([0-9.eE+]+)",
            attrs, re.I,
        )
        try:
            width = float(width_match.group(1)) if width_match else float(viewbox.group(1))
            height = float(height_match.group(1)) if height_match else float(viewbox.group(2))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False
    return 0 < width <= max_dimension and 0 < height <= max_dimension and width * height <= max_pixels


def resolve_icon(icon, fallback="application-x-executable", pixmap_dirs=None, max_bytes=MAX_ICON_BYTES):
    """Return a safe icon file or theme name; malformed input always falls back."""
    value = str(icon or "").strip()
    fallback = str(fallback or "application-x-executable").strip() or "application-x-executable"
    if not value or "\x00" in value or len(value) > 4096:
        return fallback
    suffix = pathlib.Path(value).suffix.lower()
    directories = tuple(pathlib.Path(item) for item in (pixmap_dirs or ("/usr/share/pixmaps",)))

    def safe_file(candidate):
        try:
            candidate = pathlib.Path(candidate).expanduser()
            if candidate.suffix.lower() not in ICON_EXTENSIONS or not candidate.is_file():
                return None
            size = candidate.stat().st_size
            if size <= 0 or size > int(max_bytes):
                return None
            with candidate.open("rb") as stream:
                head = stream.read(256).lstrip()
            if not _safe_icon_dimensions(candidate, head):
                return None
            return str(candidate.resolve())
        except (OSError, TypeError, ValueError, OverflowError):
            return None

    candidate = pathlib.Path(value).expanduser()
    if candidate.is_absolute():
        return safe_file(candidate) or fallback
    if suffix:
        if suffix not in ICON_EXTENSIONS or candidate.name != value:
            return fallback
        for directory in directories:
            resolved = safe_file(directory / candidate.name)
            if resolved:
                return resolved
        # Theme APIs expect extension-free names.
        return fallback
    if "/" in value or "\\" in value or not re.fullmatch(r"[A-Za-z0-9_.+-]+", value):
        return fallback
    return value


def autostart_exec(content, current_desktop="XFCE"):
    """Return an applicable autostart Exec, ignoring disabled desktop entries."""
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string(content)
        section = parser["Desktop Entry"]
        if section.get("Type", "Application") != "Application":
            return None
        if section.getboolean("Hidden", fallback=False):
            return None
        if not section.getboolean("X-GNOME-Autostart-enabled", fallback=True):
            return None
        desktop = str(current_desktop or "").casefold()
        only = {item.casefold() for item in section.get("OnlyShowIn", "").split(";") if item}
        excluded = {item.casefold() for item in section.get("NotShowIn", "").split(";") if item}
        if (only and desktop not in only) or desktop in excluded:
            return None
        return desktop_exec_program(desktop_exec_argv(section.get("Exec", "")))
    except (KeyError, ValueError, configparser.Error):
        return None


def autostart_processes(content, current_desktop="XFCE"):
    """Return duplicate-shell executables actually launched by an active entry."""
    duplicate_shell = {"xfce4-panel", "xfce4-appfinder", "whiskermenu", "volumeicon", "nm-applet", "xfdesktop", "xfce4-power-manager"}
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string(content)
        section = parser["Desktop Entry"]
        if section.getboolean("Hidden", fallback=False) or not section.getboolean(
                "X-GNOME-Autostart-enabled", fallback=True):
            return ()
        desktop = str(current_desktop or "").casefold()
        only = {item.casefold() for item in section.get("OnlyShowIn", "").split(";") if item}
        excluded = {item.casefold() for item in section.get("NotShowIn", "").split(";") if item}
        if (only and desktop not in only) or desktop in excluded:
            return ()
        argv = shlex.split(section.get("Exec", ""), posix=True)
        if not argv:
            return ()
        offset = 0
        if pathlib.PurePath(argv[0]).name == "env":
            offset = 1
            while offset < len(argv) and (argv[offset].startswith("-") or "=" in argv[offset]):
                offset += 1
        program = pathlib.PurePath(argv[offset]).name if offset < len(argv) else ""
        programs = []
        if program in _SHELL_NAME and "-c" in argv[offset + 1:]:
            script_index = argv.index("-c", offset + 1) + 1
            lexer = shlex.shlex(io.StringIO(argv[script_index]), posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""
            segment = []
            for token in list(lexer) + [";"]:
                if token and all(char in ";&|" for char in token):
                    candidate = _unwrapped_program(segment)
                    if candidate:
                        programs.append(candidate)
                    segment = []
                else:
                    segment.append(token)
        else:
            programs.append(_unwrapped_program(argv[offset:]))
        return tuple(dict.fromkeys(item for item in programs if item in duplicate_shell))
    except (KeyError, ValueError, configparser.Error):
        return ()


def _unwrapped_program(tokens):
    tokens = list(tokens)
    while tokens:
        token = pathlib.PurePath(tokens[0]).name
        if tokens[0] == "exec" or token == "nohup" or "=" in tokens[0] and not tokens[0].startswith("-"):
            tokens.pop(0)
            continue
        if token == "env":
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                tokens.pop(0)
            continue
        if token == "timeout":
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            if tokens:
                tokens.pop(0)
            continue
        return token
    return ""


def load_icon_pixbuf(icon_theme, icon, size, fallback="application-x-executable"):
    """Load an icon through GdkPixbuf/Gtk while containing all decoder errors."""
    resolved = resolve_icon(icon, fallback=fallback)
    size = max(16, min(512, int(size)))
    try:
        if pathlib.Path(resolved).is_absolute():
            from gi.repository import GdkPixbuf
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(resolved, size, size, True)
        return icon_theme.load_icon(resolved, size, 0)
    except Exception:
        if resolved != fallback:
            try:
                return icon_theme.load_icon(fallback, size, 0)
            except Exception:
                pass
        return None


class RuntimeSocket:
    def __init__(self, server, path, lock_file, inode):
        self.server = server
        self.path = path
        self.lock_file = lock_file
        self.inode = inode

    def __getattr__(self, name):
        return getattr(self.server, name)

    def close(self):
        try:
            self.server.close()
        finally:
            try:
                if self.path.stat().st_ino == self.inode:
                    self.path.unlink()
            except (FileNotFoundError, OSError):
                pass
            try:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self.lock_file.close()


def ease_out_cubic(progress):
    progress = min(1.0, max(0.0, float(progress)))
    return 1.0 - (1.0 - progress) ** 3


# The shell uses fixed surfaces rather than sampled/blurred backgrounds.  Keep
# the palette and profile calculation dependency-free so the GTK3 desktop,
# drawer and other small shell helpers make the same low-cost decision.
SHELL_PALETTE = {
    "surface_base_light": "#F7FAF8",
    "surface_raised_light": "#FFFFFF",
    "surface_sunken_light": "#EEF3F0",
    "surface_base_dark": "#202522",
    "surface_raised_dark": "#292F2B",
    "surface_sunken_dark": "#181C1A",
    "border_soft_light": "#D8E2DD",
    "border_soft_dark": "#3D4742",
    "text_primary_light": "#17201C",
    "text_secondary_light": "#53615A",
    "text_primary_dark": "#F2F6F4",
    "text_secondary_dark": "#B7C2BC",
    "accent": "#238673",
    "warning": "#B56A18",
    "error": "#B63E3E",
}


def shell_runtime_profile_path():
    """Return the small session-owned cache that records the effective renderer.

    Appearance preferences deliberately describe the user's requested mode.
    The session coordinator writes this separate cache after detecting a VM,
    software renderer, or low-memory machine.  Keeping it separate prevents a
    temporary compatibility fallback from overwriting the user's ``auto``
    choice.
    """
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        base = pathlib.Path(cache_home).expanduser()
    else:
        base = pathlib.Path(os.environ.get("HOME") or pathlib.Path.home()).expanduser() / ".cache"
    return base / "ming-os" / "shell-visual.json"


def apply_runtime_shell_profile(appearance, runtime_path=None):
    """Overlay a verified compatibility fallback without mutating preferences.

    This function intentionally performs disk I/O only when a shell consumer
    reloads its appearance.  Rendering paths use ``shell_visual_profile`` on
    the already-normalized result and therefore never read this file per frame.
    """
    result = dict(appearance) if isinstance(appearance, dict) else {}
    requested = result.get("compositor_profile", "auto")
    if requested == "software":
        requested = "compat"
        result["compositor_profile"] = requested
    if requested != "auto":
        return result
    path = pathlib.Path(runtime_path or shell_runtime_profile_path())
    try:
        runtime = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(runtime, dict):
        return result
    effective = runtime.get("effective_profile")
    if effective in {"compat", "off"}:
        result["compositor_profile"] = effective
    return result


def shell_visual_profile(appearance=None):
    """Normalize appearance state for low-cost shell surfaces and animation."""
    data = appearance if isinstance(appearance, dict) else {}
    theme = data.get("theme", "system")
    theme = theme if theme in {"light", "dark", "system"} else "system"
    compositor = data.get("compositor_profile", "auto")
    # 26.3.2 persisted this name.  Treat it as a migration alias, not a third
    # active profile, so old settings safely become the documented compat mode.
    if compositor == "software":
        compositor = "compat"
    compositor = compositor if compositor in {"auto", "compat", "off"} else "auto"
    motion = data.get("motion", "")
    if motion not in {"normal", "reduced"}:
        motion = "reduced" if data.get("reduced_motion") else "normal"
    dark = theme == "dark"
    prefix = "dark" if dark else "light"
    # `system` deliberately uses the light neutral shell until GTK informs the
    # application otherwise; this avoids transient dark/light flashes at login.
    alpha = 0.96 if compositor == "auto" else 1.0
    return {
        "theme": theme,
        "compositor_profile": compositor,
        "motion": motion,
        "surface_alpha": alpha,
        "surface_base": SHELL_PALETTE["surface_base_" + prefix],
        "surface_raised": SHELL_PALETTE["surface_raised_" + prefix],
        "surface_sunken": SHELL_PALETTE["surface_sunken_" + prefix],
        "border_soft": SHELL_PALETTE["border_soft_" + prefix],
        "text_primary": SHELL_PALETTE["text_primary_" + prefix],
        "text_secondary": SHELL_PALETTE["text_secondary_" + prefix],
        "accent": SHELL_PALETTE["accent"],
        "interval_ms": 16 if compositor == "auto" else 33,
    }


def shell_animation_timing(appearance=None):
    """Return the only animation cadence allowed for the Ming shell."""
    profile = shell_visual_profile(appearance)
    if profile["motion"] == "reduced":
        return {"duration_ms": 0, "interval_ms": 0}
    return {
        "duration_ms": 200 if profile["compositor_profile"] == "auto" else 180,
        "interval_ms": profile["interval_ms"],
    }


def _localized(section, key, locale_name):
    locale_name = (locale_name or "").split(".", 1)[0]
    candidates = []
    if locale_name:
        candidates.append("{}[{}]".format(key, locale_name))
        language = locale_name.split("_", 1)[0]
        if language != locale_name:
            candidates.append("{}[{}]".format(key, language))
    candidates.append(key)
    for candidate in candidates:
        value = section.get(candidate, "").strip()
        if value:
            return value
    return ""


def current_desktop_ids(current_desktops=None):
    """Return normalised desktop identifiers with the Ming Xfce baseline."""
    if current_desktops is None:
        current_desktops = (
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("DESKTOP_SESSION", ""),
        )
    if isinstance(current_desktops, str):
        values = re.split(r"[:;]", current_desktops)
    else:
        try:
            values = tuple(
                identifier
                for value in current_desktops
                for identifier in re.split(r"[:;]", str(value))
            )
        except TypeError:
            values = ()
    desktop_ids = {
        str(value).strip().casefold()
        for value in values
        if str(value).strip()
    }
    return desktop_ids or {"xfce"}


def installed_package_owner(path, command_runner, timeout=2, expected_package=None):
    """Return the exact installed Debian package owning *path*, or an empty string.

    ``command_runner`` receives dpkg-query arguments and returns a completed
    process-like object.  Callers can adapt their own command execution layer
    without importing the launch broker or relaxing its ownership contract.
    """
    try:
        requested_path = str(path)
        ownership = command_runner(("-S", "--", requested_path), timeout=timeout)
        if getattr(ownership, "returncode", 1) != 0:
            return ""
        lines = [line for line in str(getattr(ownership, "stdout", "") or "").splitlines() if line]
        if len(lines) != 1:
            return ""
        owner, marker, reported_path = lines[0].rpartition(": ")
        if (
                marker != ": "
                or reported_path != requested_path
                or owner.strip() != owner
                or "," in owner
                or not _DEBIAN_PACKAGE_NAME.fullmatch(owner)):
            return ""
        installation = command_runner(
            ("-W", "-f=${db:Status-Abbrev}\\t${binary:Package}\\n", "--", owner),
            timeout=timeout,
        )
        if getattr(installation, "returncode", 1) != 0:
            return ""
        lines = [line for line in str(getattr(installation, "stdout", "") or "").splitlines() if line]
        if len(lines) != 1:
            return ""
        status, marker, installed_owner = lines[0].partition("\t")
        if marker != "\t" or status != "ii " or installed_owner != owner:
            return ""
        if expected_package is not None:
            expected = str(expected_package).strip()
            if not _DEBIAN_PACKAGE_NAME.fullmatch(expected):
                return ""
            # dpkg may qualify the installed owner with an architecture while
            # DEB metadata supplies the binary package name.  This image only
            # accepts amd64/all DEBs, so the binary portion is the stable
            # identity for this readiness check.
            if owner.split(":", 1)[0] != expected.split(":", 1)[0]:
                return ""
        return owner
    except (AttributeError, OSError, TypeError, ValueError, subprocess.TimeoutExpired):
        return ""


def desktop_entry_is_visible(
        section, current_desktops=None, respect_desktop_environment=False):
    """Apply catalog visibility rules, optionally including desktop environment keys."""
    try:
        if (
                section.getboolean("Hidden", fallback=False)
                or section.getboolean("NoDisplay", fallback=False)):
            return False
        if not respect_desktop_environment:
            return True
        desktops = current_desktop_ids(current_desktops)
        only = {
            item.strip().casefold()
            for item in section.get("OnlyShowIn", "").split(";")
            if item.strip()
        }
        excluded = {
            item.strip().casefold()
            for item in section.get("NotShowIn", "").split(";")
            if item.strip()
        }
        return (not only or bool(desktops.intersection(only))) and not bool(desktops.intersection(excluded))
    except (AttributeError, TypeError, ValueError):
        return False


def is_system_desktop_activation_candidate(
        path, system_dir=pathlib.Path("/usr/share/applications"),
        path_resolver=None, stat_reader=None):
    """Return whether a package-owned system desktop file may be considered.

    This is deliberately a static candidate filter only.  It does not parse
    ``Exec`` or perform activation; the broker must revalidate this boundary
    immediately before activation and ordinary entries still pass through
    ``desktop_exec_argv`` unchanged.
    """
    resolver = path_resolver or (lambda value: pathlib.Path(value).resolve(strict=True))
    reader = stat_reader or os.stat
    try:
        requested = pathlib.Path(path)
        requested_dir = pathlib.Path(system_dir)
        lexical_path = pathlib.Path(os.path.abspath(os.fspath(requested)))
        lexical_dir = pathlib.Path(os.path.abspath(os.fspath(requested_dir)))
        resolved_path = pathlib.Path(resolver(requested))
        resolved_dir = pathlib.Path(resolver(requested_dir))
        # A difference means an entry or one of its parents traversed a link.
        if resolved_path != lexical_path or resolved_dir != lexical_dir:
            return False
        if (
                lexical_path.parent != lexical_dir
                or resolved_path.parent != resolved_dir
                or resolved_path.suffix != ".desktop"):
            return False
        directory_metadata = reader(resolved_dir)
        if not (
                stat.S_ISDIR(directory_metadata.st_mode)
                and directory_metadata.st_uid == 0
                and not (directory_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))):
            return False
        leaf_metadata = reader(resolved_path)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return (
        stat.S_ISREG(leaf_metadata.st_mode)
        and leaf_metadata.st_uid == 0
        and not (leaf_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    )


def desktop_exec_argv(command):
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ValueError("desktop Exec is empty or invalid")
    if any(marker in command for marker in (";", "`", "$(", "\n", "\r")):
        raise ValueError("shell syntax is not allowed in desktop Exec")
    try:
        raw = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("desktop Exec cannot be parsed") from exc
    if not raw or any(token in {"|", "||", "&&", ">", ">>", "<"} for token in raw):
        raise ValueError("shell operators are not allowed in desktop Exec")
    probe = raw
    if pathlib.PurePath(raw[0]).name == "env":
        offset = 1
        while offset < len(raw) and (raw[offset].startswith("-") or "=" in raw[offset]):
            offset += 1
        probe = raw[offset:]
    if probe and pathlib.PurePath(probe[0]).name in _SHELL_NAME and "-c" in probe[1:]:
        raise ValueError("shell command wrappers are not allowed")
    argv = []
    for token in raw:
        token = token.replace("%%", "\x00")
        token = _FIELD_CODE.sub("", token).replace("\x00", "%")
        if token:
            argv.append(token)
    if not argv:
        raise ValueError("desktop Exec has no executable")
    return tuple(argv)


def desktop_exec_program(argv):
    """Return the actual program from an already-sanitised desktop argv."""
    if not isinstance(argv, (tuple, list)) or not argv:
        raise ValueError("desktop Exec has no executable")
    probe = tuple(argv)
    if pathlib.PurePath(probe[0]).name == "env":
        offset = 1
        while offset < len(probe) and (probe[offset].startswith("-") or "=" in probe[offset]):
            offset += 1
        probe = probe[offset:]
    if not probe:
        raise ValueError("desktop Exec has no executable")
    return probe[0]


def desktop_launch_diagnostic(argv):
    """Return a user-facing reason when a validated launcher cannot start."""
    try:
        executable = desktop_exec_program(argv)
    except ValueError:
        return "启动命令不完整，无法确定要运行的程序。"
    candidate = pathlib.Path(executable)
    if candidate.is_absolute() or executable.startswith("/"):
        if not candidate.is_file():
            return "找不到启动程序：{}".format(executable)
        if not os.access(str(candidate), os.X_OK):
            return "启动程序没有执行权限：{}".format(executable)
        return ""
    if "/" in executable or "\\" in executable:
        return "启动程序路径必须是绝对路径或系统命令：{}".format(executable)
    if shutil.which(executable) is None:
        return "找不到启动程序：{}".format(executable)
    return ""


def _diagnostic_message(reason):
    reason = str(reason or "")
    if "shell command wrappers" in reason:
        return "为保护系统安全，不支持通过 shell -c 启动的第三方入口。"
    if "shell syntax" in reason or "shell operators" in reason:
        return "为保护系统安全，不支持带有 shell 语法的第三方入口。"
    if "cannot be parsed" in reason:
        return "启动命令格式无法解析，请重新安装该软件。"
    if "empty" in reason or "no executable" in reason:
        return "启动器没有有效的启动命令，请重新安装该软件。"
    if "TryExec" in reason:
        return "启动器依赖的程序不可用，请重新安装该软件。"
    return "启动器配置无效：{}".format(reason or "未知错误")


def _diagnostic_entry_from_raw(
        path, locale_name, reason, respect_desktop_environment=False):
    """Read only display metadata for an unlaunchable application launcher."""
    path = pathlib.Path(path)
    try:
        if path.suffix != ".desktop" or not path.is_file() or path.stat().st_size > MAX_DESKTOP_BYTES:
            return None
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    if section.get("Type", "Application") != "Application":
        return None
    if not desktop_entry_is_visible(
            section, respect_desktop_environment=respect_desktop_environment):
        return None
    name = _localized(section, "Name", locale_name or os.environ.get("LANG", "")) or path.stem
    categories = tuple(item for item in section.get("Categories", "").split(";") if item)
    return DesktopEntry(
        path=path,
        name=name,
        comment=_localized(section, "Comment", locale_name or os.environ.get("LANG", "")),
        icon=section.get("Icon", "").strip(),
        argv=(),
        categories=categories,
        diagnostic=_diagnostic_message(reason),
    )


def parse_desktop_file(path, locale_name=None, respect_desktop_environment=False):
    path = pathlib.Path(path)
    if path.suffix != ".desktop" or not path.is_file():
        raise ValueError("not a desktop file")
    if path.stat().st_size > MAX_DESKTOP_BYTES:
        raise ValueError("desktop file is too large")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise ValueError("desktop file cannot be read") from exc
    if not parser.has_section("Desktop Entry"):
        raise ValueError("desktop entry section is missing")
    section = parser["Desktop Entry"]
    if section.get("Type", "Application") != "Application":
        return None
    if not desktop_entry_is_visible(
            section, respect_desktop_environment=respect_desktop_environment):
        return None
    try_exec = section.get("TryExec", "").strip()
    if try_exec:
        try:
            executable = shlex.split(try_exec, posix=True)[0]
        except (IndexError, ValueError):
            return None
        if pathlib.Path(executable).is_absolute():
            if not pathlib.Path(executable).is_file() or not os.access(executable, os.X_OK):
                return None
        elif shutil.which(executable) is None:
            return None
    name = _localized(section, "Name", locale_name or os.environ.get("LANG", ""))
    if not name:
        raise ValueError("desktop entry name is missing")
    argv = desktop_exec_argv(section.get("Exec", ""))
    categories = tuple(item for item in section.get("Categories", "").split(";") if item)
    return DesktopEntry(
        path=path,
        name=name,
        comment=_localized(section, "Comment", locale_name or os.environ.get("LANG", "")),
        icon=section.get("Icon", "").strip(),
        argv=argv,
        categories=categories,
    )


def diagnose_desktop_file(
        path, locale_name=None, respect_desktop_environment=False):
    """Describe a launcher without ever turning an unsafe Exec into an argv.

    A normal catalog should keep a broken third-party launcher visible so the
    user gets an actionable reason.  The normal parser remains strict for
    actual launch paths; only this diagnostic view supplies an empty argv.
    """
    try:
        entry = parse_desktop_file(
            path, locale_name=locale_name,
            respect_desktop_environment=respect_desktop_environment)
    except ValueError as exc:
        reason = str(exc)
        diagnostic_entry = _diagnostic_entry_from_raw(
            path, locale_name, reason,
            respect_desktop_environment=respect_desktop_environment)
        # This is only a catalog representation.  The broker revalidates the
        # package owner and descriptor before it activates the desktop entry.
        if (
                reason == "shell command wrappers are not allowed"
                and diagnostic_entry is not None
                and is_system_desktop_activation_candidate(path)):
            return DesktopEntry(
                path=diagnostic_entry.path,
                name=diagnostic_entry.name,
                comment=diagnostic_entry.comment,
                icon=diagnostic_entry.icon,
                argv=(),
                categories=diagnostic_entry.categories,
            )
        return diagnostic_entry
    if entry is None:
        return _diagnostic_entry_from_raw(
            path, locale_name, "TryExec dependency is unavailable",
            respect_desktop_environment=respect_desktop_environment)
    diagnostic = desktop_launch_diagnostic(entry.argv)
    if not diagnostic:
        return entry
    return DesktopEntry(
        path=entry.path,
        name=entry.name,
        comment=entry.comment,
        icon=entry.icon,
        argv=(),
        categories=entry.categories,
        diagnostic=diagnostic,
    )


def runtime_path(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", name):
        raise ValueError("invalid runtime name")
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not pathlib.Path(base).is_absolute():
        uid = getattr(os, "getuid", lambda: 0)()
        base = os.path.join("/tmp", "ming-runtime-{}".format(uid))
    directory = pathlib.Path(base) / "ming-os"
    if directory.is_symlink():
        raise ValueError("runtime directory cannot be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory / name


def runtime_socket_path(service):
    if not isinstance(service, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", service):
        raise ValueError("invalid service name")
    return runtime_path(service + ".sock")


def send_launch_request(
        desktop_file, source="unknown", rect=None,
        timeout=DEFAULT_LAUNCH_REQUEST_TIMEOUT):
    """Return a bounded accepted, rejected, or unavailable broker result."""
    if source not in {"desktop", "drawer", "dock", "unknown"}:
        source = "unknown"
    try:
        path = pathlib.Path(desktop_file).expanduser()
        if not path.is_file() or path.suffix != ".desktop":
            return LaunchRequestResult("rejected", "desktop file is unavailable")
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_LAUNCH_REQUEST_TIMEOUT:
            return LaunchRequestResult("rejected", "invalid launch request")
        request_id = new_launch_request_id()
        message = {
            "version": 1,
            "action": "launch",
            "request_id": request_id,
            "source": source,
            "rect": Rect.from_mapping(rect).to_dict() if rect is not None else None,
            "desktop_file": str(path),
        }
    except (AttributeError, TypeError, ValueError):
        return LaunchRequestResult("rejected", "invalid launch request")
    sent = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(runtime_socket_path("launch")))
            client.sendall(encode_json_line(message))
            sent = True
            try:
                response = recv_json_line(client, timeout=timeout)
            except (AttributeError, OSError, TypeError, ValueError):
                # The socket was reachable, but the peer did not provide a
                # valid correlated reply.  Falling back here could execute a
                # second launch after a compromised or stale broker response.
                return LaunchRequestResult("rejected", "invalid broker response")
    except (AttributeError, OSError, TypeError, ValueError):
        if sent:
            # A peer can reset after consuming the request.  Treat that as a
            # terminal rejection so desktop surfaces never dispatch a second
            # broker process for an uncertain request.
            return LaunchRequestResult("rejected", "invalid broker response")
        return LaunchRequestResult("unavailable", "launch broker is unavailable")
    return parse_launch_result(response, request_id)


def send_launch_request_async(
        desktop_file, source="unknown", rect=None, callback=None,
        timeout=ASYNC_LAUNCH_REQUEST_TIMEOUT):
    """Run a bounded correlated broker request away from a GTK event handler."""
    if not callable(callback):
        return False

    def request_worker():
        try:
            result = send_launch_request(desktop_file, source, rect, timeout=timeout)
        except Exception as exc:
            result = LaunchRequestResult("rejected", str(exc) or "launch request failed")
        try:
            callback(result)
        except Exception:
            pass

    try:
        threading.Thread(
            target=request_worker,
            name="ming-launch-request",
            daemon=True,
        ).start()
    except (RuntimeError, TypeError):
        return False
    return True


def retry_launch_request_after_broker_start(
        desktop_file, source="unknown", rect=None,
        request_timeout=ASYNC_LAUNCH_REQUEST_TIMEOUT,
        recovery_timeout=BROKER_RECOVERY_TIMEOUT,
        retry_interval=BROKER_RECOVERY_INTERVAL,
        sleeper=time.sleep, clock=time.monotonic):
    """Retry only requests that never reached a newly started launch broker."""
    try:
        request_timeout = float(request_timeout)
        recovery_timeout = float(recovery_timeout)
        retry_interval = float(retry_interval)
        if (
                not math.isfinite(request_timeout)
                or not math.isfinite(recovery_timeout)
                or not math.isfinite(retry_interval)
                or request_timeout <= 0
                or request_timeout > MAX_LAUNCH_REQUEST_TIMEOUT
                or recovery_timeout <= 0
                or recovery_timeout > 5
                or retry_interval <= 0
                or retry_interval > 1):
            return LaunchRequestResult("rejected", "invalid broker recovery timeout")
        deadline = float(clock()) + recovery_timeout
    except (AttributeError, TypeError, ValueError):
        return LaunchRequestResult("rejected", "invalid broker recovery timeout")

    last = LaunchRequestResult("unavailable", "launch broker is unavailable")
    while True:
        try:
            last = send_launch_request(
                desktop_file, source, rect, timeout=request_timeout)
        except Exception as exc:
            return LaunchRequestResult("rejected", str(exc) or "launch broker recovery failed")
        rejected = bool(getattr(last, "rejected", False))
        unavailable = not rejected and (
            bool(getattr(last, "unavailable", False)) or not bool(last))
        if not unavailable:
            return last
        try:
            remaining = deadline - float(clock())
        except (TypeError, ValueError):
            return LaunchRequestResult("rejected", "invalid broker recovery clock")
        if remaining <= 0:
            return last
        try:
            sleeper(min(retry_interval, remaining))
        except (AttributeError, OSError, TypeError, ValueError):
            return last


def broker_fallback_argv(desktop_file, source):
    """Return the sole safe local fallback: start a broker, never an app."""
    if source not in {"desktop", "drawer", "dock"}:
        raise ValueError("unsupported desktop launch source")
    try:
        path = os.fspath(desktop_file)
    except TypeError as exc:
        raise ValueError("desktop file path is invalid") from exc
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("desktop file path is invalid")
    return ("/usr/local/bin/ming-launch", "--server")


def run_command(argv, timeout=8):
    if not isinstance(argv, (list, tuple)) or not argv or not all(
        isinstance(item, str) and item and "\x00" not in item for item in argv
    ):
        raise ValueError("command must be a non-empty argv list")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be numeric") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise ValueError("timeout is out of range")
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            check=False, shell=False,
        )
        return CommandResult(argv, completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(argv, 124, exc.stdout or "", exc.stderr or "", timed_out=True)
    except OSError as exc:
        return CommandResult(argv, 127, "", str(exc))


def claim_runtime_socket(service, backlog=8):
    path = runtime_socket_path(service)
    lock_path = runtime_path(service + ".lock")
    lock_file = lock_path.open("a+b")
    try:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            fcntl = None
        except OSError as exc:
            raise InstanceAlreadyRunning(service) from exc
        if path.exists():
            active = False
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(path))
                active = True
            except OSError:
                active = False
            finally:
                probe.close()
            if active:
                raise InstanceAlreadyRunning(service)
            status = path.lstat()
            if not stat.S_ISSOCK(status.st_mode):
                raise OSError("runtime socket path is not a socket")
            getuid = getattr(os, "getuid", None)
            if getuid is not None and status.st_uid != getuid():
                raise PermissionError("stale socket belongs to another user")
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(path))
            os.chmod(path, 0o600)
            server.listen(max(1, min(32, int(backlog))))
            return RuntimeSocket(server, path, lock_file, path.stat().st_ino)
        except BaseException:
            server.close()
            raise
    except BaseException:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        lock_file.close()
        raise


def encode_json_line(message):
    if not isinstance(message, dict):
        raise ValueError("IPC message must be an object")
    try:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("IPC message is not JSON serializable") from exc
    if len(payload) + 1 > MAX_JSON_LINE_BYTES:
        raise ValueError("IPC message is too large")
    return payload + b"\n"


def decode_json_line(payload):
    if not isinstance(payload, bytes) or len(payload) > MAX_JSON_LINE_BYTES:
        raise ValueError("IPC line is invalid or too large")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("IPC line is not valid JSON") from exc
    if not isinstance(message, dict):
        raise ValueError("IPC message must be an object")
    return message


def recv_json_line(connection, timeout=0.5):
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be numeric") from exc
    if timeout <= 0 or timeout > MAX_LAUNCH_REQUEST_TIMEOUT:
        raise ValueError("IPC timeout is out of range")
    deadline = time.monotonic() + timeout
    payload = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("IPC line timed out")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(min(4096, MAX_JSON_LINE_BYTES + 1 - len(payload)))
        except (socket.timeout, TimeoutError) as exc:
            raise ValueError("IPC line timed out") from exc
        if not chunk:
            raise ValueError("IPC line ended before newline")
        payload.extend(chunk)
        if len(payload) > MAX_JSON_LINE_BYTES:
            raise ValueError("IPC line is too large")
        newline = payload.find(b"\n")
        if newline >= 0:
            return decode_json_line(bytes(payload[:newline + 1]))
