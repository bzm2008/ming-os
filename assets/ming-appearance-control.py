#!/usr/bin/env python3
"""Persist and apply Ming OS appearance preferences."""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import struct


DEFAULTS = {
    "version": 1,
    "theme": "system",
    "font_family": "Noto Sans",
    "font_size": 11,
    "desktop_icon_scale": 1.0,
    "dock_icon_size": 48,
    "wallpaper": "default",
}
MAX_WALLPAPER_BYTES = 32 * 1024 * 1024
MAX_WALLPAPER_PIXELS = 16 * 1024 * 1024
BUILTIN_WALLPAPERS = {
    "default": pathlib.Path("/usr/share/backgrounds/ming-os/default.png"),
    "light": pathlib.Path("/usr/share/backgrounds/ming-os/default-light.png"),
    "dark": pathlib.Path("/usr/share/backgrounds/ming-os/default-dark.png"),
}


def config_path():
    return pathlib.Path.home() / ".config/ming-os/appearance.json"


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


def load_config(path=None):
    path = pathlib.Path(path or config_path())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    result = dict(DEFAULTS)
    if isinstance(raw, dict):
        result.update({key: raw[key] for key in DEFAULTS if key in raw})
    return result


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


def copy_wallpaper(source):
    source = pathlib.Path(source).expanduser()
    try:
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_WALLPAPER_BYTES:
            raise ValueError("wallpaper is missing or too large")
        safe_wallpaper_dimensions(source)
    except OSError as exc:
        raise ValueError("wallpaper cannot be read") from exc
    target_dir = pathlib.Path.home() / ".local/share/backgrounds/ming-os"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} else ".img"
    target = target_dir / ("custom-wallpaper" + suffix)
    descriptor, temporary = tempfile.mkstemp(prefix=".wallpaper-", suffix=suffix, dir=str(target_dir))
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        fsync_directory(target_dir)
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


def apply_runtime(config):
    if os.environ.get("MING_APPEARANCE_NO_APPLY") == "1":
        return
    theme = config["theme"]
    gtk_theme = "Adwaita-dark" if theme == "dark" else "Adwaita"
    commands = [
        ["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName", "-s", gtk_theme],
        ["xfconf-query", "-c", "xsettings", "-p", "/Gtk/FontName", "-s", "%s %s" % (config["font_family"], config["font_size"])],
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark" if theme == "dark" else "default"],
    ]
    wallpaper = BUILTIN_WALLPAPERS.get(config["wallpaper"], pathlib.Path(config["wallpaper"]))
    if wallpaper.is_file():
        commands.append([
            "xfconf-query", "-c", "xfce4-desktop", "-p",
            "/backdrop/screen0/monitor0/workspace0/last-image", "-n", "-t", "string", "-s", str(wallpaper),
        ])
    for command in commands:
        try:
            subprocess.run(command, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    plank = pathlib.Path.home() / ".config/plank/dock1/settings"
    temporary = None
    try:
        lines = plank.read_text(encoding="utf-8").splitlines() if plank.is_file() else []
        lines = [line for line in lines if not line.startswith("IconSize=")]
        lines.append("IconSize=%s" % config["dock_icon_size"])
        atomic_write_text = "\n".join(lines) + "\n"
        plank.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".plank-", suffix=".tmp", dir=str(plank.parent))
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(atomic_write_text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, plank)
        fsync_directory(plank.parent)
    except OSError:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def parser():
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    apply = sub.add_parser("apply")
    apply.add_argument("--theme", choices=("light", "dark", "system"))
    apply.add_argument("--font-family", choices=("Noto Sans", "Noto Serif", "Noto Sans CJK SC", "Noto Mono"))
    apply.add_argument("--font-size", type=int, choices=range(9, 19))
    apply.add_argument("--desktop-icon-scale", type=float, choices=(0.75, 1.0, 1.25, 1.5))
    apply.add_argument("--dock-icon-size", type=int, choices=range(32, 65, 4))
    apply.add_argument("--wallpaper")
    apply.add_argument("--json", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    config = load_config()
    if args.command == "apply":
        for key in ("theme", "font_family", "font_size", "desktop_icon_scale", "dock_icon_size"):
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
        atomic_write(config_path(), config)
        apply_runtime(config)
    if getattr(args, "json", False):
        print(json.dumps(config, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in config.items():
            print("%s=%s" % (key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
