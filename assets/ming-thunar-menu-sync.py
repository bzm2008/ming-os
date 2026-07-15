#!/usr/bin/env python3
"""Merge Ming's DEB install action into an existing Thunar custom menu.

This helper is intentionally additive.  A preserved /home from an older OTA
may contain a user-owned uca.xml, so the file is never replaced wholesale and
an invalid or symlinked file is left untouched.
"""

import argparse
import json
import os
import pathlib
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET


ACTION_ID = "ming-deb-installer"
ACTION_COMMAND = "/usr/local/bin/ming-package-install-gui \"%f\""


def _action_is_ming(action):
    return (
        action.findtext("unique-id", "") == ACTION_ID
        or action.findtext("command", "") == ACTION_COMMAND
    )


def _new_action():
    action = ET.Element("action")
    ET.SubElement(action, "icon").text = "package-x-generic"
    ET.SubElement(action, "name").text = "安装 DEB 软件包"
    ET.SubElement(action, "unique-id").text = ACTION_ID
    ET.SubElement(action, "command").text = ACTION_COMMAND
    ET.SubElement(action, "description").text = "验证并安装本地 Debian 软件包"
    ET.SubElement(action, "range").text = "*"
    ET.SubElement(action, "patterns").text = "*.deb;*.DEB"
    ET.SubElement(action, "other-files")
    return action


def sync_menu(path):
    path = pathlib.Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return {"ok": False, "changed": False, "error": "unsafe uca.xml path"}

    mode = 0o600
    if path.exists():
        try:
            info = path.stat()
            if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o022:
                return {"ok": False, "changed": False, "error": "uca.xml is writable by another user"}
            mode = stat.S_IMODE(info.st_mode)
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            return {"ok": False, "changed": False, "error": "cannot read uca.xml: %s" % exc}
    else:
        root = ET.Element("actions")

    if root.tag != "actions":
        return {"ok": False, "changed": False, "error": "uca.xml root is not actions"}
    if any(_action_is_ming(action) for action in root.findall("action")):
        return {"ok": True, "changed": False, "path": str(path)}

    root.append(_new_action())
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".uca.xml.", dir=str(path.parent), text=True)
        temporary = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            ET.ElementTree(root).write(handle, encoding="unicode", xml_declaration=True)
            handle.flush()
            os.fchmod(handle.fileno(), mode or 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return {"ok": True, "changed": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "changed": False, "error": "cannot save uca.xml: %s" % exc}
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-thunar-menu-sync")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--path",
        default=str(pathlib.Path.home() / ".config" / "Thunar" / "uca.xml"),
    )
    args = parser.parse_args(argv)
    result = sync_menu(args.path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif not result.get("ok"):
        print(result.get("error", "menu sync failed"), file=sys.stderr)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
