#!/usr/bin/env python3
"""Repair Ming OS Fcitx5 user input-method configuration."""

import argparse
import json
import os
import pathlib
import re
import shutil
import sys

try:
    import pwd
except ImportError:  # pragma: no cover - Windows test host
    pwd = None


XINPUTRC = """# Compatibility environment only. The Fcitx5 daemon is started by ~/.config/autostart/fcitx5.desktop.
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export GLFW_IM_MODULE=fcitx
"""

AUTOSTART = """[Desktop Entry]
Type=Application
Name=Fcitx 5
Comment=Start Chinese input method
Exec=sh -c 'sleep 2; fcitx5 -d --replace'
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""

PROFILE = """[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[Groups/0/Items/2]
Name=rime
Layout=

[GroupOrder]
0=Default
"""

CLASSICUI = """Theme=Ming-Candidate
Font=Noto Sans CJK SC 15
MenuFont=Noto Sans CJK SC 16
Vertical Candidate List=True
"""

CONFIG = """[Behavior]
DefaultPageSize=7
"""


def _is_ming_xinputrc(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    required = (
        "export GTK_IM_MODULE=fcitx",
        "export QT_IM_MODULE=fcitx",
        "export XMODIFIERS=@im=fcitx",
    )
    forbidden = ("run_im fcitx5", "fcitx5 -d --replace")
    return all(item in text for item in required) and not any(item in text for item in forbidden)


def _backup(path):
    path = pathlib.Path(path)
    backup = path.with_name(path.name + ".ming-legacy-backup")
    if path.is_file() and not backup.exists():
        shutil.copy2(path, backup)
        return str(backup)
    return ""


def _write_if_changed(path, content, backup_existing=False):
    path = pathlib.Path(path)
    current = ""
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        pass
    if current == content:
        return False, ""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    backup = _backup(path) if backup_existing and path.exists() else ""
    path.write_text(content, encoding="utf-8")
    return True, backup


def _chown(path, owner):
    if owner is None:
        return
    if pwd is None:
        return
    try:
        info = pwd.getpwnam(owner)
    except KeyError:
        return
    root = pathlib.Path(path)
    targets = [root]
    if root.is_dir():
        targets.extend(root.rglob("*"))
    for target in targets:
        try:
            os.chown(target, info.pw_uid, info.pw_gid)
        except OSError:
            pass


def repair_home(home, owner=None):
    home = pathlib.Path(home)
    result = {"ok": True, "home": str(home), "changed": False, "backups": [], "files": []}
    if not home.is_dir():
        return {**result, "ok": False, "error": "home directory does not exist"}

    xinputrc = home / ".xinputrc"
    changed, backup = _write_if_changed(xinputrc, XINPUTRC, backup_existing=not _is_ming_xinputrc(xinputrc))
    result["changed"] = result["changed"] or changed
    if backup:
        result["backups"].append(backup)
    result["files"].append(str(xinputrc))

    for relative, content in (
        (".config/autostart/fcitx5.desktop", AUTOSTART),
        (".config/fcitx5/profile", PROFILE),
        (".config/fcitx5/conf/classicui.conf", CLASSICUI),
        (".config/fcitx5/config", CONFIG),
    ):
        path = home / relative
        changed, backup = _write_if_changed(path, content, backup_existing=path.exists())
        result["changed"] = result["changed"] or changed
        if backup:
            result["backups"].append(backup)
        result["files"].append(str(path))

    _chown(home / ".xinputrc", owner)
    _chown(home / ".config" / "autostart" / "fcitx5.desktop", owner)
    _chown(home / ".config" / "fcitx5", owner)
    return result


def _valid_user(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", value))


def homes_for(args):
    if args.current_user:
        return [(pathlib.Path.home(), os.environ.get("USER") or None)]
    if args.user:
        if not _valid_user(args.user):
            raise ValueError("invalid user")
        return [(pathlib.Path(args.home_root) / args.user, args.user)]
    root = pathlib.Path(args.home_root)
    homes = []
    for child in sorted(root.iterdir() if root.is_dir() else []):
        if child.is_dir() and _valid_user(child.name):
            homes.append((child, child.name))
    return homes


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-input-repair")
    parser.add_argument("--user", default="")
    parser.add_argument("--current-user", action="store_true")
    parser.add_argument("--home-root", default="/home")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None, stdout=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    stdout = stdout or sys.stdout
    try:
        repairs = [repair_home(home, owner=owner) for home, owner in homes_for(args)]
        result = {"ok": all(item.get("ok") for item in repairs), "repairs": repairs}
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        result = {"ok": False, "repairs": [], "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
