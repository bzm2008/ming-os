#!/usr/bin/env python3
"""Shared, dependency-free primitives for Ming shell helpers."""

import configparser
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import socket
import stat
import struct
import subprocess


MAX_DESKTOP_BYTES = 256 * 1024
MAX_JSON_LINE_BYTES = 64 * 1024
MAX_ICON_BYTES = 8 * 1024 * 1024
ICON_EXTENSIONS = {".png", ".svg"}
_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")
_SHELL_NAME = {"sh", "bash", "dash", "zsh", "ksh"}


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


def _diagnostic_entry_from_raw(path, locale_name, reason):
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
    if section.getboolean("Hidden", fallback=False) or section.getboolean("NoDisplay", fallback=False):
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


def parse_desktop_file(path, locale_name=None):
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
    if section.getboolean("Hidden", fallback=False) or section.getboolean("NoDisplay", fallback=False):
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


def diagnose_desktop_file(path, locale_name=None):
    """Describe a launcher without ever turning an unsafe Exec into an argv.

    A normal catalog should keep a broken third-party launcher visible so the
    user gets an actionable reason.  The normal parser remains strict for
    actual launch paths; only this diagnostic view supplies an empty argv.
    """
    try:
        entry = parse_desktop_file(path, locale_name=locale_name)
    except ValueError as exc:
        return _diagnostic_entry_from_raw(path, locale_name, str(exc))
    if entry is None:
        return _diagnostic_entry_from_raw(path, locale_name, "TryExec dependency is unavailable")
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


def send_launch_request(desktop_file, source="unknown", rect=None, timeout=0.4):
    """Send one launch request to the running broker and report acceptance."""
    if source not in {"desktop", "drawer", "dock", "unknown"}:
        source = "unknown"
    try:
        path = pathlib.Path(desktop_file).expanduser()
        if not path.is_file() or path.suffix != ".desktop":
            return False
        message = {
            "version": 1,
            "action": "launch",
            "source": source,
            "rect": Rect.from_mapping(rect).to_dict() if rect is not None else None,
            "desktop_file": str(path),
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(float(timeout))
            client.connect(str(runtime_socket_path("launch")))
            client.sendall(encode_json_line(message))
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


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
    if timeout <= 0 or timeout > 5:
        raise ValueError("IPC timeout is out of range")
    connection.settimeout(timeout)
    payload = bytearray()
    while True:
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
