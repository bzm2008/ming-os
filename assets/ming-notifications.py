#!/usr/bin/env python3
"""Defensive notification history helpers for xfce4-notifyd."""

import configparser
import json
import os
import pathlib
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET


MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_FIELD_CHARS = 4096
DEFAULT_LIMIT = 50


class Notification:
    __slots__ = ("timestamp", "app_name", "summary", "body", "icon")

    def __init__(self, timestamp="", app_name="", summary="", body="", icon=""):
        self.timestamp = _clean(timestamp)
        self.app_name = _clean(app_name)
        self.summary = _clean(summary)
        self.body = _clean(body)
        self.icon = _clean(icon)


class DndCommand:
    __slots__ = ("enabled", "argv")

    def __init__(self, enabled):
        self.enabled = bool(enabled)
        self.argv = (
            "xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb",
            "-s", "true" if self.enabled else "false",
        )


def _clean(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return "".join(char for char in value if char >= " " or char in "\t\n")[:MAX_FIELD_CHARS].strip()


def _from_mapping(value):
    if not isinstance(value, dict):
        return None
    summary = value.get("summary") or value.get("title") or ""
    if not summary:
        return None
    return Notification(
        timestamp=value.get("timestamp", value.get("time", "")),
        app_name=value.get("app_name", value.get("app", "")),
        summary=summary,
        body=value.get("body", value.get("message", "")),
        icon=value.get("icon", ""),
    )


def _parse_xml(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items = []
    for entry in root.iter("entry"):
        values = dict(entry.attrib)
        for child in entry:
            values[child.tag] = child.text or ""
        item = _from_mapping(values)
        if item:
            items.append(item)
    return items


def _parse_keyfile(text):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    items = []
    for section in parser.sections():
        item = _from_mapping({
            "timestamp": section,
            "app_name": parser.get(section, "app_name", fallback=""),
            "summary": parser.get(section, "summary", fallback=""),
            "body": parser.get(section, "body", fallback=""),
            "icon": parser.get(section, "app_icon", fallback=""),
        })
        if item:
            items.append(item)
    return items


def _parse_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            return _from_mapping(json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    match = re.fullmatch(r"\[([^\]]+)\]\s*([^|\t]+)\s*[|\t]\s*([^|\t]+)(?:\s*[|\t]\s*(.*))?", line)
    if match:
        return Notification(match.group(1), match.group(2), match.group(3), match.group(4) or "")
    return None


def parse_notification_log(text, limit=DEFAULT_LIMIT):
    if not isinstance(text, str) or len(text.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        return []
    try:
        limit = max(0, min(DEFAULT_LIMIT, int(limit)))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    if not limit:
        return []
    stripped = text.lstrip()
    if stripped.startswith("<"):
        items = _parse_xml(text)
    elif stripped.startswith("[") and "\nsummary=" in text:
        items = _parse_keyfile(text)
    else:
        items = [item for item in (_parse_line(line) for line in text.splitlines()) if item]
    return items[-limit:]


def _is_sqlite(path):
    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _load_sqlite(path, limit):
    database = None
    try:
        uri = "file:{}?mode=ro".format(path.resolve().as_posix())
        database = sqlite3.connect(uri, uri=True, timeout=1)
        columns = {row[1] for row in database.execute("PRAGMA table_info(notifications)")}
        required = {"timestamp", "app_name", "summary", "body", "icon_id"}
        if not required.issubset(columns):
            return []
        rows = database.execute(
            "SELECT timestamp, substr(app_name, 1, 4096), substr(summary, 1, 4096), "
            "substr(body, 1, 4096), substr(icon_id, 1, 4096) "
            "FROM notifications ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Notification(*row) for row in reversed(rows)]
    except (OSError, sqlite3.DatabaseError):
        return []
    finally:
        if database is not None:
            database.close()


def load_notification_log(path, limit=DEFAULT_LIMIT):
    path = pathlib.Path(path).expanduser()
    try:
        if _is_sqlite(path):
            return _load_sqlite(path, max(0, min(DEFAULT_LIMIT, int(limit))))
        if path.stat().st_size > MAX_LOG_BYTES:
            return []
        return parse_notification_log(path.read_text(encoding="utf-8", errors="replace"), limit)
    except (OSError, TypeError, ValueError):
        return []


def clear_notification_log_atomic(path):
    path = pathlib.Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_sqlite(path):
        database = sqlite3.connect(path, timeout=2)
        try:
            database.execute("BEGIN IMMEDIATE")
            database.execute("DELETE FROM notifications")
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()
        return
    mode = 0o600
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def dnd_command(enabled):
    return DndCommand(enabled)


def default_log_path():
    directory = pathlib.Path.home() / ".cache" / "xfce4" / "notifyd"
    sqlite_log = directory / "log.sqlite"
    return sqlite_log if sqlite_log.exists() else directory / "log"
