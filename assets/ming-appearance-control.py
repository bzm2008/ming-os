#!/usr/bin/env python3
"""Persist and apply Ming OS appearance preferences."""

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import struct


DEFAULTS = {
    "version": 2,
    "theme": "system",
    "font_family": "Noto Sans CJK SC",
    "font_size": 11,
    "desktop_icon_scale": 1.0,
    "desktop_icon_size": 48,
    "dock_icon_size": 48,
    "wallpaper": "default",
    "motion": "normal",
    "compositor_profile": "auto",
}
MAX_WALLPAPER_BYTES = 32 * 1024 * 1024
MAX_WALLPAPER_PIXELS = 16 * 1024 * 1024
FONT_FAMILIES = ("Noto Sans", "Noto Serif", "Noto Sans CJK SC", "Noto Mono")
ICON_SCALES = (0.75, 1.0, 1.25, 1.5)
ICON_SIZES = tuple(range(32, 73, 4))
MOTION_VALUES = ("normal", "reduced")
COMPOSITOR_PROFILES = ("auto", "compat", "off")
BUILTIN_WALLPAPERS = {
    "default": pathlib.Path("/usr/share/backgrounds/ming-os/default.png"),
    "light": pathlib.Path("/usr/share/backgrounds/ming-os/default-light.png"),
    "dark": pathlib.Path("/usr/share/backgrounds/ming-os/default-dark.png"),
}


def config_path():
    return home_path() / ".config/ming-os/appearance.json"


def last_good_config_path():
    return home_path() / ".config/ming-os/appearance.last-good.json"


def home_path():
    # `Path.home()` ignores HOME under some Windows test hosts.  The deployed
    # Linux session still resolves to the same user directory, while honoring
    # HOME keeps the command hermetic for recovery and test environments.
    return pathlib.Path(os.environ.get("HOME") or pathlib.Path.home()).expanduser()


def legacy_settings_path():
    return home_path() / ".config/ming-os/settings.json"


def fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(path), flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Directory handles are not fsync-able on every supported platform.
        pass


def normalize_config(raw):
    """Read current and 26.3.2 appearance formats without losing preferences."""
    result = dict(DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    theme = raw.get("theme")
    if theme in {"light", "dark", "system"}:
        result["theme"] = theme
    family = raw.get("font_family")
    if family in FONT_FAMILIES:
        result["font_family"] = family
    try:
        size = int(raw.get("font_size", result["font_size"]))
        if 9 <= size <= 18:
            result["font_size"] = size
    except (TypeError, ValueError):
        pass
    try:
        scale = float(raw.get("desktop_icon_scale", result["desktop_icon_scale"]))
        if scale in ICON_SCALES:
            result["desktop_icon_scale"] = scale
    except (TypeError, ValueError):
        pass
    try:
        icon_size = int(raw.get("desktop_icon_size", result["desktop_icon_size"]))
        if icon_size in ICON_SIZES:
            result["desktop_icon_size"] = icon_size
    except (TypeError, ValueError):
        pass
    try:
        dock_size = int(raw.get("dock_icon_size", result["dock_icon_size"]))
        if 32 <= dock_size <= 64 and dock_size % 4 == 0:
            result["dock_icon_size"] = dock_size
    except (TypeError, ValueError):
        pass
    wallpaper = raw.get("wallpaper")
    if isinstance(wallpaper, str) and wallpaper:
        result["wallpaper"] = wallpaper
    motion = raw.get("motion")
    if motion not in MOTION_VALUES:
        motion = "reduced" if raw.get("reduced_motion") else "normal"
    result["motion"] = motion
    profile = raw.get("compositor_profile", "auto")
    if profile == "software":
        profile = "compat"
    if profile in COMPOSITOR_PROFILES:
        result["compositor_profile"] = profile
    return result


def _read_config(path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return normalize_config(raw)


def load_config(path=None):
    path = pathlib.Path(path or config_path())
    config = _read_config(path)
    if config is not None:
        return config
    # A torn write or bad manual edit must not reset a working desktop to
    # defaults.  Only the canonical per-user file has this recovery sibling;
    # explicit test/import paths remain self-contained.
    if path == config_path():
        fallback = _read_config(last_good_config_path())
        if fallback is not None:
            return fallback
    return dict(DEFAULTS)


def atomic_write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".appearance-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def save_config(value, path=None, save_last_good=False):
    path = pathlib.Path(path or config_path())
    normalized = normalize_config(value)
    atomic_write(path, normalized)
    if load_config(path) != normalized:
        raise OSError("appearance configuration could not be read back")
    if save_last_good:
        backup = last_good_config_path()
        atomic_write(backup, normalized)
        if _read_config(backup) != normalized:
            raise OSError("last known good appearance configuration could not be read back")
    return normalized


def apply_and_commit(config, previous=None):
    """Persist one requested change, verify it, and retain a recoverable copy."""
    previous = normalize_config(previous if isinstance(previous, dict) else load_config())
    config = save_config(config)
    dock_changed = config["dock_icon_size"] != previous["dock_icon_size"]
    try:
        apply_runtime(config, reload_dock=dock_changed)
    except OSError:
        # Keep a desktop that was known to work rather than a half-applied
        # appearance choice.  Runtime rollback is best effort because a broken
        # Xfconf daemon cannot be repaired by this command alone.
        save_config(previous, save_last_good=True)
        try:
            apply_runtime(previous, reload_dock=dock_changed)
        except OSError:
            pass
        raise
    return save_config(config, save_last_good=True)


def sync_legacy_shell_state(config):
    """Keep the pre-26.3.3 controls coherent when a user changes shell mode."""
    path = legacy_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw["reduced_motion"] = config.get("motion") == "reduced"
    raw["compositor_profile"] = config.get("compositor_profile", "auto")
    atomic_write(path, raw)


def wallpaper_thumbnail_path():
    return home_path() / ".cache/ming-os/wallpaper/custom-wallpaper-thumb.png"


def write_wallpaper_thumbnail(source):
    """Create one import-time preview; desktop resolution caches remain in GTK."""
    source = pathlib.Path(source)
    target = wallpaper_thumbnail_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".wallpaper-thumb-", suffix=".png", dir=str(target.parent))
    os.close(descriptor)
    try:
        converted = False
        try:
            completed = subprocess.run(
                ["convert", str(source), "-thumbnail", "480x270^", "-gravity", "center",
                 "-extent", "480x270", temporary],
                timeout=8, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            converted = completed.returncode == 0 and os.path.getsize(temporary) > 0
        except (OSError, subprocess.TimeoutExpired):
            converted = False
        if not converted:
            shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        fsync_directory(target.parent)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return str(target)


def copy_wallpaper(source):
    source = pathlib.Path(source).expanduser()
    try:
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_WALLPAPER_BYTES:
            raise ValueError("wallpaper is missing or too large")
        safe_wallpaper_dimensions(source)
    except OSError as exc:
        raise ValueError("wallpaper cannot be read") from exc
    target_dir = home_path() / ".local/share/backgrounds/ming-os"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} else ".img"
    target = target_dir / ("custom-wallpaper" + suffix)
    descriptor, temporary = tempfile.mkstemp(prefix=".wallpaper-", suffix=suffix, dir=str(target_dir))
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        fsync_directory(target_dir)
        write_wallpaper_thumbnail(target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return str(target)


def safe_wallpaper_dimensions(path):
    path = pathlib.Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("wallpaper must be a static PNG or JPEG")
    with path.open("rb") as stream:
        if suffix == ".png":
            head = stream.read(24)
            if len(head) < 24 or not head.startswith(b"\x89PNG\r\n\x1a\n") or head[12:16] != b"IHDR":
                raise ValueError("invalid PNG wallpaper")
            width, height = struct.unpack(">II", head[16:24])
        else:
            if stream.read(2) != b"\xff\xd8":
                raise ValueError("invalid JPEG wallpaper")
            width = height = 0
            while True:
                marker = stream.read(1)
                if not marker:
                    break
                if marker != b"\xff":
                    continue
                code = stream.read(1)
                while code == b"\xff":
                    code = stream.read(1)
                if code in {bytes([value]) for value in range(0xC0, 0xC4)} | {b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"}:
                    length_data = stream.read(2)
                    if len(length_data) != 2:
                        raise ValueError("invalid JPEG wallpaper")
                    length = struct.unpack(">H", length_data)[0]
                    data = stream.read(length - 2)
                    if len(data) < 5:
                        raise ValueError("invalid JPEG wallpaper")
                    height, width = struct.unpack(">HH", data[1:5])
                    break
                length_data = stream.read(2)
                if len(length_data) != 2:
                    break
                length = struct.unpack(">H", length_data)[0]
                stream.seek(max(0, length - 2), 1)
    if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= MAX_WALLPAPER_PIXELS):
        raise ValueError("wallpaper dimensions are unsafe")
    return width, height


def atomic_write_text(path, value):
    """Atomically replace a small per-user text configuration file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s-" % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _replace_setting_line(text, key, value):
    lines = text.splitlines()
    replacement = "%s=%s" % (key, value)
    pattern = re.compile(r"^\s*%s\s*=.*$" % re.escape(key))
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    return "\n".join(lines).rstrip("\n") + "\n"


def sync_gtk_font_settings(font_name):
    """Update toolkit chrome defaults without touching document/web content fonts."""
    gtk3_path = home_path() / ".config/gtk-3.0/settings.ini"
    try:
        gtk3_text = gtk3_path.read_text(encoding="utf-8")
    except OSError:
        gtk3_text = "[Settings]\n"
    if "[Settings]" not in gtk3_text:
        gtk3_text = "[Settings]\n" + gtk3_text
    atomic_write_text(gtk3_path, _replace_setting_line(gtk3_text, "gtk-font-name", font_name))

    gtk4_path = home_path() / ".config/gtk-4.0/settings.ini"
    try:
        gtk4_text = gtk4_path.read_text(encoding="utf-8")
    except OSError:
        gtk4_text = "[Settings]\n"
    if "[Settings]" not in gtk4_text:
        gtk4_text = "[Settings]\n" + gtk4_text
    atomic_write_text(gtk4_path, _replace_setting_line(gtk4_text, "gtk-font-name", font_name))

    gtk2_path = home_path() / ".gtkrc-2.0"
    try:
        gtk2_text = gtk2_path.read_text(encoding="utf-8")
    except OSError:
        gtk2_text = ""
    atomic_write_text(gtk2_path, _replace_setting_line(
        gtk2_text, "gtk-font-name", '"%s"' % font_name))


def sync_dock_runtime(config, reload_dock=False):
    """Keep Plank's dconf and keyfile in sync, then request one bounded reload."""
    dock_size = int(config["dock_icon_size"])
    dconf_key = "/net/launchpad/plank/docks/dock1/icon-size"
    if shutil.which("dconf"):
        checked = lambda command, label: _run_checked(command, label)
        checked(["dconf", "write", dconf_key, str(dock_size)], "Dock dconf")
        actual = checked(["dconf", "read", dconf_key], "Dock dconf readback")
        match = re.search(r"\b(\d+)\b", actual)
        if not match or int(match.group(1)) != dock_size:
            raise OSError("Dock dconf readback did not match the requested value")

    plank = home_path() / ".config/plank/dock1/settings"
    try:
        lines = plank.read_text(encoding="utf-8").splitlines() if plank.is_file() else []
        lines = [line for line in lines if not line.startswith("IconSize=")]
        lines.append("IconSize=%s" % dock_size)
        atomic_write_text(plank, "\n".join(lines) + "\n")
        actual_lines = plank.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OSError("Dock icon size could not be applied") from exc
    if "IconSize=%s" % dock_size not in actual_lines:
        raise OSError("Dock icon size readback did not match the requested value")

    if reload_dock and shutil.which("ming-session-healthcheck"):
        try:
            subprocess.run(
                ["ming-session-healthcheck", "--reload-dock"],
                timeout=9, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_checked(command, label, timeout=4):
    try:
        completed = subprocess.run(
            command, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("%s could not be applied" % label) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OSError("%s could not be applied%s" % (
            label, ": " + detail[:160] if detail else ""))
    return completed.stdout.strip()


def apply_runtime(config, reload_dock=False):
    if os.environ.get("MING_APPEARANCE_NO_APPLY") == "1":
        return

    checked = _run_checked

    def xfconf_set(channel, property_name, value, label):
        command = ["xfconf-query", "-c", channel, "-p", property_name, "-s", value]
        try:
            checked(command, label)
        except OSError:
            # Existing channels accept the short form.  New channels need a
            # typed property, so retry that documented creation form once.
            checked(
                ["xfconf-query", "-c", channel, "-p", property_name,
                 "-n", "-t", "string", "-s", value], label)
        actual = checked(
            ["xfconf-query", "-c", channel, "-p", property_name], label + " readback")
        if actual != value:
            raise OSError("%s readback did not match the requested value" % label)

    theme = config["theme"]
    gtk_theme = "Adwaita-dark" if theme == "dark" else "Ming-Glass"
    xfconf_set("xsettings", "/Net/ThemeName", gtk_theme, "GTK theme")
    xfconf_set(
        "xsettings", "/Gtk/FontName", "%s %s" % (config["font_family"], config["font_size"]),
        "GTK font")
    xfconf_set(
        "xfwm4", "/general/title_font",
        "%s Bold %s" % (config["font_family"], config["font_size"]),
        "window title font")
    color_scheme = "prefer-dark" if theme == "dark" else "default"
    checked(
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", color_scheme],
        "GTK4 color scheme")
    gsettings_actual = checked(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
        "GTK4 color scheme readback")
    if color_scheme not in gsettings_actual:
        raise OSError("GTK4 color scheme readback did not match the requested value")
    font_name = "%s %s" % (config["font_family"], config["font_size"])
    checked(
        ["gsettings", "set", "org.gnome.desktop.interface", "font-name", font_name],
        "GTK4 font")
    gsettings_font_actual = checked(
        ["gsettings", "get", "org.gnome.desktop.interface", "font-name"],
        "GTK4 font readback")
    if font_name not in gsettings_font_actual:
        raise OSError("GTK4 font readback did not match the requested value")
    sync_gtk_font_settings(font_name)

    wallpaper = BUILTIN_WALLPAPERS.get(config["wallpaper"], pathlib.Path(config["wallpaper"]))
    if wallpaper.is_file():
        xfconf_set(
            "xfce4-desktop", "/backdrop/screen0/monitor0/workspace0/last-image", str(wallpaper),
            "desktop wallpaper")
    sync_dock_runtime(config, reload_dock=reload_dock)


def parser():
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    apply = sub.add_parser("apply")
    apply.add_argument("--theme", choices=("light", "dark", "system"))
    apply.add_argument("--font-family", choices=FONT_FAMILIES)
    apply.add_argument("--font-size", type=int, choices=range(9, 19))
    apply.add_argument("--desktop-icon-scale", type=float, choices=ICON_SCALES)
    apply.add_argument("--desktop-icon-size", type=int, choices=ICON_SIZES)
    apply.add_argument("--dock-icon-size", type=int, choices=range(32, 65, 4))
    apply.add_argument("--wallpaper")
    apply.add_argument("--motion", choices=MOTION_VALUES)
    apply.add_argument("--compositor-profile", choices=COMPOSITOR_PROFILES)
    apply.add_argument("--json", action="store_true")
    imported = sub.add_parser("import-wallpaper")
    imported.add_argument("file")
    imported.add_argument("--json", action="store_true")
    reset = sub.add_parser("reset")
    reset.add_argument("--json", action="store_true")
    reapply = sub.add_parser("reapply")
    reapply.add_argument("--json", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    config = load_config()
    if args.command == "apply":
        previous = dict(config)
        shell_state_requested = args.motion is not None or args.compositor_profile is not None
        for key in (
                "theme", "font_family", "font_size", "desktop_icon_scale", "desktop_icon_size",
                "dock_icon_size", "motion", "compositor_profile"):
            value = getattr(args, key)
            if value is not None:
                config[key] = value
        if args.wallpaper:
            if args.wallpaper in BUILTIN_WALLPAPERS:
                config["wallpaper"] = args.wallpaper
            else:
                try:
                    config["wallpaper"] = copy_wallpaper(args.wallpaper)
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
        try:
            config = apply_and_commit(config, previous)
        except OSError as exc:
            print("外观设置未生效，已恢复到上一次可用配置：%s" % exc, file=sys.stderr)
            return 1
        if shell_state_requested:
            sync_legacy_shell_state(config)
    elif args.command == "import-wallpaper":
        previous = dict(config)
        try:
            config["wallpaper"] = copy_wallpaper(args.file)
            config = apply_and_commit(config, previous)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif args.command == "reset":
        try:
            config = apply_and_commit(DEFAULTS, config)
        except OSError as exc:
            print("外观设置未生效，已恢复到上一次可用配置：%s" % exc, file=sys.stderr)
            return 1
        sync_legacy_shell_state(config)
    elif args.command == "reapply":
        try:
            config = apply_and_commit(config, config)
        except OSError as exc:
            print("外观配置无法重新应用：%s" % exc, file=sys.stderr)
            return 1
    if getattr(args, "json", False):
        print(json.dumps(config, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in config.items():
            print("%s=%s" % (key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
