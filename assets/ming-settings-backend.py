#!/usr/bin/env python3
"""Typed settings backend for Ming Settings.

Only keys in SETTING_SPECS can reach system configuration commands. The UI never
passes a shell fragment to this process.
"""

import configparser
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time


PROTECTED_AUTOSTART = {
    "ming-phone-desktop.desktop",
    "ming-dock.desktop",
    "ming-plank.desktop",
    "ming-shell-service.desktop",
    "ming-input-method.desktop",
    "picom.desktop",
}


def default_runner(argv, timeout=8):
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def default_spawner(argv):
    return subprocess.Popen(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def parse_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("需要布尔值")


SETTING_SPECS = {
    "focus_mode": {
        "kind": "enum", "choices": ("click", "follows"),
        "backend": "xfconf", "channel": "xfwm4", "property": "/general/click_to_focus",
        "xfconf_type": "bool",
        "encode": lambda value: value == "click",
        "decode": lambda value: "click" if parse_bool(value) else "follows",
    },
    "window_raise_delay": {
        "kind": "int", "min": 0, "max": 2000,
        "backend": "xfconf", "channel": "xfwm4", "property": "/general/raise_delay",
    },
    "cursor_size": {
        "kind": "int", "min": 16, "max": 64,
        "backend": "xfconf", "channel": "xsettings", "property": "/Gtk/CursorThemeSize",
    },
    "text_scale": {
        "kind": "float", "min": 0.8, "max": 2.0,
        "backend": "gsettings", "schema": "org.gnome.desktop.interface",
        "property": "text-scaling-factor",
    },
    "notification_dnd": {
        "kind": "bool", "backend": "xfconf", "channel": "xfce4-notifyd",
        "property": "/do-not-disturb",
    },
    "notification_history_size": {
        "kind": "int", "min": 10, "max": 200, "backend": "xfconf",
        "channel": "xfce4-notifyd", "property": "/log-max-size",
    },
    "dock_icon_size": {
        "kind": "int", "min": 32, "max": 96, "backend": "appearance",
    },
    "dock_zoom_percent": {
        "kind": "int", "min": 100, "max": 180, "backend": "plank", "key": "ZoomPercent",
    },
    "dock_hide_mode": {
        "kind": "enum", "choices": ("never", "intellihide", "autohide"),
        "backend": "plank", "key": "HideMode",
        "encode": lambda value: {"never": 0, "intellihide": 1, "autohide": 2}[value],
        "decode": lambda value: {0: "never", 1: "intellihide", 2: "autohide"}.get(int(value), "never"),
    },
    "reduced_motion": {"kind": "bool", "backend": "local"},
    "compositor_profile": {
        "kind": "enum", "choices": ("auto", "compat", "off"), "backend": "local",
    },
    "interaction_policy": {
        "kind": "enum", "choices": ("adaptive", "compat", "off"), "backend": "local",
        "default": "adaptive",
    },
    "background_throttle": {
        "kind": "bool", "backend": "local", "default": False,
    },
    "disk_prefetch": {
        "kind": "enum", "choices": ("auto", "off"), "backend": "local",
        "default": "auto",
    },
    "lid_close_action": {
        "kind": "enum", "choices": ("nothing", "suspend", "hibernate"),
        "backend": "lid_action",
        "encode": lambda value: {"nothing": 0, "suspend": 1, "hibernate": 2}[value],
        "decode": lambda value: {0: "nothing", 1: "suspend", 2: "hibernate"}[int(value)],
    },
}


class SettingsBackend:
    def __init__(self, runner=default_runner, spawner=default_spawner, home=None,
                 system_autostart_dirs=None, application_dirs=None, waiter=time.sleep):
        self.runner = runner
        self.spawner = spawner
        self.waiter = waiter
        self.home = pathlib.Path(home or os.path.expanduser("~"))
        self.local_path = self.home / ".config/ming-os/settings.json"
        self.appearance_path = self.home / ".config/ming-os/appearance.json"
        self.runtime_profile_path = self.home / ".cache/ming-os/shell-visual.json"
        self.plank_path = self.home / ".config/plank/dock1/settings"
        self.autostart_dir = self.home / ".config/autostart"
        self.picom_autostart_path = self.autostart_dir / "picom.desktop"
        self._migrate_performance_defaults()
        self.system_autostart_dirs = tuple(
            pathlib.Path(item) for item in (
                ("/etc/xdg/autostart",) if system_autostart_dirs is None
                else system_autostart_dirs))
        self.application_dirs = tuple(
            pathlib.Path(item) for item in (
                (self.home / ".local/share/applications", "/usr/local/share/applications",
                 "/usr/share/applications") if application_dirs is None
                else application_dirs))

    def _result(self, ok, key=None, value=None, error=""):
        return {"ok": bool(ok), "key": key, "value": value, "error": error}

    def _validate(self, spec, value):
        kind = spec["kind"]
        if kind == "bool":
            return parse_bool(value)
        if kind == "int":
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                raise ValueError("需要整数")
            if not spec["min"] <= parsed <= spec["max"]:
                raise ValueError("有效范围是 %s-%s" % (spec["min"], spec["max"]))
            return parsed
        if kind == "float":
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                raise ValueError("需要数字")
            if not spec["min"] <= parsed <= spec["max"]:
                raise ValueError("有效范围是 %s-%s" % (spec["min"], spec["max"]))
            return parsed
        if kind == "enum":
            if value not in spec["choices"]:
                raise ValueError("有效选项：%s" % "、".join(spec["choices"]))
            return value
        raise ValueError("未知设置类型")

    def _read_local(self, strict=False):
        try:
            data = json.loads(self.local_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return None if strict else {}

    def _migrate_performance_defaults(self):
        """Move preview installs to the formal release's conservative default once."""
        if not self.local_path.is_file():
            return
        data = self._read_local(strict=True)
        if data is None:
            return
        try:
            version = int(data.get("_ming_performance_policy_version", 0) or 0)
        except (TypeError, ValueError):
            version = 0
        if version >= 2:
            return
        data["background_throttle"] = False
        data["_ming_performance_policy_version"] = 2
        self._write_local(data)

    def _write_local(self, data):
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        data.setdefault("_ming_performance_policy_version", 2)
        fd, temporary = tempfile.mkstemp(prefix="settings-", dir=str(self.local_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.local_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read_appearance(self):
        try:
            data = json.loads(self.appearance_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_appearance(self, data):
        self.appearance_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="appearance-", dir=str(self.appearance_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.appearance_path)
            try:
                directory_fd = os.open(str(self.appearance_path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            readback = json.loads(self.appearance_path.read_text(encoding="utf-8"))
            if readback != data:
                raise OSError("appearance configuration readback did not match")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write_runtime_shell_profile(self, requested, effective, reason):
        self.runtime_profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "requested_profile": self._canonical_compositor_profile(requested),
            "effective_profile": self._canonical_compositor_profile(effective),
            "reason": str(reason),
        }
        fd, temporary = tempfile.mkstemp(prefix="shell-visual-", dir=str(self.runtime_profile_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.runtime_profile_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _canonical_compositor_profile(value):
        if value == "software":
            return "compat"
        return value if value in {"auto", "compat", "off"} else "auto"

    def _write_appearance_values(self, **values):
        data = self._read_appearance()
        try:
            version = int(data.get("version", 2) or 2)
        except (TypeError, ValueError):
            version = 2
        data["version"] = max(2, version)
        data.update(values)
        self._write_appearance(data)

    def _plank_config(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        if self.plank_path.exists():
            parser.read(self.plank_path, encoding="utf-8")
        if not parser.has_section("PlankDockPreferences"):
            parser.add_section("PlankDockPreferences")
        return parser

    @staticmethod
    def _desktop_parser(path):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(path, encoding="utf-8")
        if not parser.has_section("Desktop Entry"):
            raise ValueError("启动项格式无效")
        return parser

    def _write_desktop_parser(self, path, parser):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            parser.write(handle, space_around_delimiters=False)

    @staticmethod
    def _snapshot_file(path):
        try:
            return True, path.read_bytes()
        except FileNotFoundError:
            return False, b""

    @staticmethod
    def _restore_file(path, snapshot):
        existed, content = snapshot
        if not existed:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="restore-", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read_xfconf(self, channel, prop):
        rc, raw, error = self.runner(["xfconf-query", "-c", channel, "-p", prop])
        if rc != 0:
            raise RuntimeError(error or "无法读取 Xfconf")
        return raw

    def _write_xfconf(self, channel, prop, value, value_type):
        encoded = str(value).lower() if isinstance(value, bool) else str(value)
        rc, _out, error = self.runner([
            "xfconf-query", "-c", channel, "-p", prop,
            "-n", "-t", value_type, "-s", encoded])
        if rc != 0:
            raise RuntimeError(error or "写入 Xfconf 失败")

    def _picom_process(self):
        rc, output, _error = self.runner(["pgrep", "-a", "-x", "picom"])
        return rc == 0, output

    def _wait_for_picom(self, expected_running, attempts=30):
        running, output = self._picom_process()
        for _attempt in range(attempts - 1):
            if running == expected_running:
                break
            self.waiter(0.1)
            running, output = self._picom_process()
        return running, output

    def _running_picom_command(self, process_output, saved_profile):
        first_line = next((line.strip() for line in process_output.splitlines() if line.strip()), "")
        command_text = first_line.split(None, 1)[1] if " " in first_line else ""
        try:
            command = shlex.split(command_text)
        except ValueError:
            command = []
        if command and pathlib.Path(command[0]).name in {"picom", "ming-picom"}:
            return command
        return self._picom_command(saved_profile)

    @staticmethod
    def _picom_commands_match(expected, actual):
        if not expected or not actual:
            return expected == actual
        expected_exe = pathlib.Path(expected[0]).name
        actual_exe = pathlib.Path(actual[0]).name
        if expected_exe == "ming-picom":
            return actual_exe in {"ming-picom", "picom"}
        return expected_exe == actual_exe and tuple(expected[1:]) == tuple(actual[1:])

    def _picom_command(self, profile):
        profile = self._canonical_compositor_profile(profile)
        if profile == "auto":
            return ["/usr/local/bin/ming-picom"]
        if profile == "compat":
            return [
                "picom", "--config", "/etc/xdg/picom/picom-fallback.conf",
                "--log-level=warn"]
        return []

    def _write_picom_autostart(self, profile):
        try:
            parser = self._desktop_parser(self.picom_autostart_path)
        except (OSError, ValueError):
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.add_section("Desktop Entry")
            parser.set("Desktop Entry", "Type", "Application")
            parser.set("Desktop Entry", "Name", "Picom Compositor")
        command = self._picom_command(profile)
        parser.set("Desktop Entry", "Exec", " ".join(command) or "/usr/local/bin/ming-picom")
        enabled = profile != "off"
        parser.set("Desktop Entry", "Hidden", "false" if enabled else "true")
        parser.set("Desktop Entry", "X-GNOME-Autostart-enabled", "true" if enabled else "false")
        self._write_desktop_parser(self.picom_autostart_path, parser)

    def _capture_compositor_state(self):
        property_name = "/general/use_compositing"
        rc, xfwm_value, xfwm_error = self.runner([
            "xfconf-query", "-c", "xfwm4", "-p", property_name])
        if rc not in (0, 1):
            raise RuntimeError(xfwm_error or "无法读取 Xfwm 合成状态")
        running, process_output = self._picom_process()
        saved_profile = self._canonical_compositor_profile(
            self._read_local().get(
                "compositor_profile", self._read_appearance().get("compositor_profile", "auto")))
        return {
            "autostart": self._snapshot_file(self.picom_autostart_path),
            "local": self._snapshot_file(self.local_path),
            "appearance": self._snapshot_file(self.appearance_path),
            "runtime_profile": self._snapshot_file(self.runtime_profile_path),
            "xfwm_exists": rc == 0 and xfwm_value != "",
            "xfwm_value": xfwm_value,
            "picom_running": running,
            "picom_output": process_output,
            "picom_command": (
                self._running_picom_command(process_output, saved_profile) if running else []),
        }

    def _restore_compositor_state(self, snapshot):
        errors = []
        rc, _output, error = self.runner(["pkill", "-x", "picom"])
        if rc not in (0, 1):
            errors.append(error or "无法停止切换后的 Picom")
        residual_running, _residual_output = self._wait_for_picom(False)
        stopped_cleanly = not residual_running
        if residual_running:
            errors.append("切换后的 Picom 未能停止")
        try:
            self._restore_file(self.picom_autostart_path, snapshot["autostart"])
            self._restore_file(self.local_path, snapshot["local"])
            self._restore_file(self.appearance_path, snapshot["appearance"])
            self._restore_file(self.runtime_profile_path, snapshot["runtime_profile"])
        except OSError as exc:
            errors.append("无法恢复配置文件：%s" % exc)
        try:
            if snapshot["xfwm_exists"]:
                self._write_xfconf(
                    "xfwm4", "/general/use_compositing",
                    parse_bool(snapshot["xfwm_value"]), "bool")
            else:
                rc, _output, error = self.runner([
                    "xfconf-query", "-c", "xfwm4", "-p", "/general/use_compositing", "-r"])
                if rc not in (0, 1):
                    raise RuntimeError(error or "无法恢复 Xfwm 属性")
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
        if snapshot["picom_running"] and stopped_cleanly:
            try:
                self.spawner(snapshot["picom_command"])
            except OSError as exc:
                errors.append("无法重启原 Picom：%s" % exc)

        running, process_output = self._wait_for_picom(snapshot["picom_running"])
        if not stopped_cleanly:
            pass
        elif running != snapshot["picom_running"]:
            errors.append("原 Picom 运行状态未恢复")
        elif running:
            actual_command = self._running_picom_command(process_output, "auto")
            if not self._picom_commands_match(snapshot["picom_command"], actual_command):
                errors.append("原 Picom 命令参数未恢复")
        rc, xfwm_value, _error = self.runner([
            "xfconf-query", "-c", "xfwm4", "-p", "/general/use_compositing"])
        if snapshot["xfwm_exists"]:
            if rc != 0 or xfwm_value != snapshot["xfwm_value"]:
                errors.append("原 Xfwm 合成状态未恢复")
        elif rc == 0 and xfwm_value:
            errors.append("原 Xfwm 属性缺失状态未恢复")
        for path, key in (
            (self.picom_autostart_path, "autostart"), (self.local_path, "local"),
            (self.appearance_path, "appearance"),
            (self.runtime_profile_path, "runtime_profile")):
            try:
                current = self._snapshot_file(path)
            except OSError as exc:
                errors.append("无法验证配置文件：%s" % exc)
                continue
            if current != snapshot[key]:
                errors.append("%s 文件未恢复" % path.name)
        return errors

    def _apply_compositor_profile(self, profile):
        profile = self._canonical_compositor_profile(profile)
        try:
            snapshot = self._capture_compositor_state()
        except (OSError, RuntimeError) as exc:
            return self._result(False, "compositor_profile", error=str(exc))
        try:
            self._write_picom_autostart(profile)
            self._write_xfconf("xfwm4", "/general/use_compositing", False, "bool")
            rc, _output, error = self.runner(["pkill", "-x", "picom"])
            if rc not in (0, 1):
                raise RuntimeError(error or "无法停止现有合成器")
            running, _output = self._wait_for_picom(False)
            if running:
                raise RuntimeError("现有 Picom 未能停止")
            if profile != "off":
                self.spawner(self._picom_command(profile))
                running, output = self._wait_for_picom(True)
                if not running:
                    raise RuntimeError("Picom 未能启动")
                if profile == "compat" and "picom-fallback.conf" not in output:
                    raise RuntimeError("Picom 未使用软件兼容配置")
            data = self._read_local()
            data["compositor_profile"] = profile
            self._write_local(data)
            self._write_appearance_values(compositor_profile=profile)
            self._write_runtime_shell_profile(profile, profile, "settings-selection")
            readback = self._compositor_readback()
            if not readback["ok"] or readback["value"] != profile:
                raise RuntimeError(readback.get("error") or "合成器设置写入后未生效")
            return readback
        except (OSError, RuntimeError) as exc:
            rollback_errors = self._restore_compositor_state(snapshot)
            message = str(exc)
            if rollback_errors:
                message += "；回滚异常：" + "；".join(rollback_errors)
            return self._result(False, "compositor_profile", error=message)

    def _compositor_readback(self):
        profile = self._canonical_compositor_profile(
            self._read_local().get(
                "compositor_profile", self._read_appearance().get("compositor_profile", "auto")))
        running, command = self._picom_process()
        if profile == "off":
            if running:
                return self._result(False, "compositor_profile", error="Picom 仍在运行")
            return self._result(True, "compositor_profile", "off")
        if not running:
            return self._result(False, "compositor_profile", error="Picom 未运行")
        if profile == "compat" and "picom-fallback.conf" not in command:
            return self._result(False, "compositor_profile", error="软件兼容配置未生效")
        return self._result(True, "compositor_profile", profile)

    def get_value(self, key):
        spec = SETTING_SPECS.get(key)
        if not spec:
            return self._result(False, key, error="不支持的设置项")
        backend = spec["backend"]
        if backend == "local":
            if key == "compositor_profile":
                return self._compositor_readback()
            if key == "reduced_motion":
                local = self._read_local()
                appearance = self._read_appearance()
                if "motion" in appearance:
                    value = appearance.get("motion") == "reduced"
                else:
                    value = bool(local.get("reduced_motion", False))
                return self._result(True, key, value)
            return self._result(True, key, self._read_local().get(key, spec.get("default")))
        if backend == "appearance":
            raw = self._read_appearance().get("dock_icon_size", 48)
        elif backend == "plank":
            parser = self._plank_config()
            raw = parser.get("PlankDockPreferences", spec["key"], fallback="0")
        elif backend == "xfconf":
            rc, raw, error = self.runner([
                "xfconf-query", "-c", spec["channel"], "-p", spec["property"]])
            if rc != 0:
                return self._result(False, key, error=error or "无法读取 Xfconf")
        elif backend == "lid_action":
            try:
                ac = self._read_xfconf(
                    "xfce4-power-manager", "/xfce4-power-manager/lid-action-on-ac")
                battery = self._read_xfconf(
                    "xfce4-power-manager", "/xfce4-power-manager/lid-action-on-battery")
            except RuntimeError as exc:
                return self._result(False, key, error=str(exc))
            if ac != battery:
                return self._result(False, key, error="交流电与电池合盖策略不一致")
            raw = ac
        elif backend == "gsettings":
            rc, raw, error = self.runner([
                "gsettings", "get", spec["schema"], spec["property"]])
            if rc != 0:
                return self._result(False, key, error=error or "无法读取 GSettings")
        else:
            return self._result(False, key, error="未知设置后端")
        try:
            value = spec.get("decode", lambda item: self._validate(spec, item))(raw)
        except (TypeError, ValueError) as exc:
            return self._result(False, key, error="读取值无效：%s" % exc)
        return self._result(True, key, value)

    def set_value(self, key, value):
        spec = SETTING_SPECS.get(key)
        if not spec:
            return self._result(False, key, error="不支持的设置项")
        if key == "compositor_profile" and value == "software":
            value = "compat"
        try:
            validated = self._validate(spec, value)
        except ValueError as exc:
            return self._result(False, key, error=str(exc))
        encoded = spec.get("encode", lambda item: item)(validated)
        backend = spec["backend"]
        if backend == "local":
            if key == "compositor_profile":
                return self._apply_compositor_profile(validated)
            data = self._read_local()
            data[key] = validated
            self._write_local(data)
            if key == "reduced_motion":
                self._write_appearance_values(
                    motion="reduced" if validated else "normal")
        elif backend == "appearance":
            rc, output, error = self.runner([
                "/usr/local/bin/ming-appearance-control", "apply",
                "--dock-icon-size", str(validated), "--json",
            ])
            if rc != 0:
                return self._result(False, key, error=error or output or "Dock 外观设置未生效")
            try:
                applied = json.loads(output)
            except (TypeError, ValueError):
                return self._result(False, key, error="Dock 外观控制返回了无效结果")
            if not isinstance(applied, dict) or applied.get("dock_icon_size") != validated:
                return self._result(False, key, error="Dock 图标大小没有通过外观控制读回")
            self._write_appearance_values(dock_icon_size=validated)
        elif backend == "plank":
            parser = self._plank_config()
            parser.set("PlankDockPreferences", spec["key"], str(encoded))
            self.plank_path.parent.mkdir(parents=True, exist_ok=True)
            with self.plank_path.open("w", encoding="utf-8") as handle:
                parser.write(handle, space_around_delimiters=False)
        elif backend == "xfconf":
            value_type = spec.get(
                "xfconf_type",
                {"bool": "bool", "int": "int", "float": "double"}.get(spec["kind"], "string"))
            rc, _out, error = self.runner([
                "xfconf-query", "-c", spec["channel"], "-p", spec["property"],
                "-n", "-t", value_type, "-s", str(encoded).lower() if isinstance(encoded, bool) else str(encoded)])
            if rc != 0:
                return self._result(False, key, error=error or "写入 Xfconf 失败")
        elif backend == "lid_action":
            try:
                for prop in (
                    "/xfce4-power-manager/lid-action-on-ac",
                    "/xfce4-power-manager/lid-action-on-battery",
                ):
                    self._write_xfconf("xfce4-power-manager", prop, encoded, "uint")
            except RuntimeError as exc:
                return self._result(False, key, error=str(exc))
        elif backend == "gsettings":
            rc, _out, error = self.runner([
                "gsettings", "set", spec["schema"], spec["property"], str(encoded)])
            if rc != 0:
                return self._result(False, key, error=error or "写入 GSettings 失败")
        readback = self.get_value(key)
        if not readback["ok"]:
            return readback
        if readback["value"] != validated:
            return self._result(False, key, readback["value"], "设置写入后未生效")
        return readback

    def list_audio_devices(self, kind):
        if kind not in ("input", "output"):
            return self._result(False, kind, error="音频设备类型无效")
        collection = "sources" if kind == "input" else "sinks"
        rc, output, error = self.runner(["pactl", "list", "short", collection])
        if rc != 0:
            return self._result(False, kind, error=error or "无法读取音频设备")
        current = self.get_audio_device(kind)
        if not current["ok"]:
            return current
        items = []
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            name = fields[1].strip()
            if not name or (kind == "input" and name.endswith(".monitor")):
                continue
            items.append({
                "id": name,
                "label": self._audio_device_label(kind, name),
                "selected": name == current["value"],
            })
        return {"ok": True, "kind": kind, "value": current["value"], "items": items, "error": ""}

    @staticmethod
    def _audio_device_label(kind, name):
        """Turn PulseAudio implementation names into safe, actionable labels."""
        lowered = name.lower()
        if kind == "output":
            if "hdmi" in lowered or "displayport" in lowered or "dp-" in lowered:
                return "HDMI / 显卡音频"
            if lowered.startswith("bluez_output"):
                return "蓝牙音频"
            if ".usb" in lowered or "usb_" in lowered:
                return "USB 音频设备"
            if "analog" in lowered and ("alsa_output.pci" in lowered or "platform" in lowered):
                return "主板模拟输出（内置扬声器，例如 ALC887）"
            return "其他音频输出"
        if lowered.startswith("bluez_input"):
            return "蓝牙麦克风"
        if ".usb" in lowered or "usb_" in lowered:
            return "USB 麦克风"
        if "analog" in lowered and ("alsa_input.pci" in lowered or "platform" in lowered):
            return "主板模拟输入（内置麦克风）"
        return "其他音频输入"

    def get_audio_device(self, kind):
        command = {
            "input": ["pactl", "get-default-source"],
            "output": ["pactl", "get-default-sink"],
        }.get(kind)
        if not command:
            return self._result(False, kind, error="音频设备类型无效")
        rc, output, error = self.runner(command)
        return self._result(rc == 0, kind, output if rc == 0 else None,
                            error if rc != 0 else "")

    def set_audio_device(self, kind, name):
        listed = self.list_audio_devices(kind)
        if not listed["ok"]:
            return listed
        allowed = {item["id"] for item in listed["items"]}
        if name not in allowed:
            return self._result(False, kind, error="音频设备不存在")
        command = ["pactl", "set-default-source" if kind == "input" else "set-default-sink", name]
        rc, _output, error = self.runner(command)
        if rc != 0:
            return self._result(False, kind, error=error or "无法切换音频设备")
        readback = self.get_audio_device(kind)
        if not readback["ok"] or readback["value"] != name:
            return self._result(False, kind, readback.get("value"), "音频设备写入后未生效")
        if kind == "output":
            try:
                data = self._read_local()
                data["audio_output_selection"] = name
                self._write_local(data)
            except OSError as exc:
                return self._result(False, kind, name, "已切换输出但无法保存选择：%s" % exc)
        return readback

    @staticmethod
    def _valid_audio_output_id(name):
        """Persist only a PulseAudio sink identifier already validated by the UI."""
        return bool(
            isinstance(name, str) and 1 <= len(name) <= 512 and
            re.fullmatch(r"[A-Za-z0-9_.:+@=-]+", name))

    def remember_audio_output(self, name):
        """Persist an explicit output choice after DeviceControl has read it back."""
        if not self._valid_audio_output_id(name):
            return self._result(False, "audio_output_selection",
                                error="音频输出标识无效，未保存选择。")
        try:
            data = self._read_local()
            data["audio_output_selection"] = name
            self._write_local(data)
        except OSError as exc:
            return self._result(False, "audio_output_selection", name,
                                "无法保存音频输出选择：%s" % exc)
        return self._result(True, "audio_output_selection", name)

    def _application_entries(self):
        entries = {}
        seen = set()
        for directory in self.application_dirs:
            try:
                paths = sorted(directory.glob("*.desktop"))
            except OSError:
                continue
            for path in paths:
                if path.name in seen:
                    continue
                seen.add(path.name)
                try:
                    parser = self._desktop_parser(path)
                except (OSError, ValueError):
                    continue
                section = parser["Desktop Entry"]
                if (parse_bool(section.get("Hidden", "false")) or
                        parse_bool(section.get("NoDisplay", "false"))):
                    continue
                entries[path.name] = parser
        return entries

    @staticmethod
    def _app_matches_role(parser, role):
        section = parser["Desktop Entry"]
        categories = set(filter(None, section.get("Categories", "").split(";")))
        mime_types = set(filter(None, section.get("MimeType", "").split(";")))
        return {
            "browser": "WebBrowser" in categories or "x-scheme-handler/http" in mime_types,
            "mail": "Email" in categories or "x-scheme-handler/mailto" in mime_types,
            "files": "FileManager" in categories or "inode/directory" in mime_types,
        }.get(role, False)

    def get_default_app(self, role):
        if role == "browser":
            command = ["xdg-settings", "get", "default-web-browser"]
        elif role in ("mail", "files"):
            mime = "x-scheme-handler/mailto" if role == "mail" else "inode/directory"
            command = ["xdg-mime", "query", "default", mime]
        else:
            return self._result(False, role, error="默认应用类型无效")
        rc, output, error = self.runner(command)
        return self._result(rc == 0, role, output if rc == 0 else None,
                            error if rc != 0 else "")

    def list_default_apps(self, role):
        current = self.get_default_app(role)
        if not current["ok"]:
            return current
        items = []
        for name, parser in self._application_entries().items():
            if not self._app_matches_role(parser, role):
                continue
            section = parser["Desktop Entry"]
            label = section.get("Name[zh_CN]", section.get("Name", name))
            items.append({"id": name, "label": label, "selected": name == current["value"]})
        items.sort(key=lambda item: item["label"].casefold())
        return {"ok": True, "kind": role, "value": current["value"], "items": items, "error": ""}

    def set_default_app(self, role, desktop_name):
        desktop_name = pathlib.Path(desktop_name).name
        listed = self.list_default_apps(role)
        if not listed["ok"]:
            return listed
        if desktop_name not in {item["id"] for item in listed["items"]}:
            return self._result(False, role, error="应用未安装或不支持该用途")
        if role == "browser":
            command = ["xdg-settings", "set", "default-web-browser", desktop_name]
        else:
            mime = "x-scheme-handler/mailto" if role == "mail" else "inode/directory"
            command = ["xdg-mime", "default", desktop_name, mime]
        rc, _output, error = self.runner(command)
        if rc != 0:
            return self._result(False, role, error=error or "无法设置默认应用")
        readback = self.get_default_app(role)
        if not readback["ok"] or readback["value"] != desktop_name:
            return self._result(False, role, readback.get("value"), "默认应用写入后未生效")
        return readback

    def _autostart_paths(self):
        result = {}
        directories = (self.autostart_dir,) + self.system_autostart_dirs
        for directory in directories:
            try:
                paths = sorted(directory.glob("*.desktop"))
            except OSError:
                continue
            for path in paths:
                result.setdefault(path.name, path)
        return result

    def list_autostart(self):
        items = []
        for name, path in sorted(self._autostart_paths().items()):
            try:
                parser = self._desktop_parser(path)
            except (OSError, ValueError):
                continue
            section = parser["Desktop Entry"]
            hidden = parse_bool(section.get("Hidden", "false"))
            enabled = parse_bool(section.get("X-GNOME-Autostart-enabled", "true")) and not hidden
            items.append({
                "id": name,
                "label": section.get("Name[zh_CN]", section.get("Name", name)),
                "enabled": enabled,
                "protected": name in PROTECTED_AUTOSTART,
            })
        return {"ok": True, "items": items, "error": ""}

    def set_autostart(self, name, enabled):
        name = pathlib.Path(name).name
        if name in PROTECTED_AUTOSTART and not parse_bool(enabled):
            return self._result(False, name, error="该项目是 Ming 系统必需服务，不能禁用")
        source = self._autostart_paths().get(name)
        if source is None:
            return self._result(False, name, error="未找到启动项")
        try:
            parser = self._desktop_parser(source)
        except (OSError, ValueError):
            return self._result(False, name, error="启动项格式无效")
        enabled = parse_bool(enabled)
        parser.set("Desktop Entry", "Hidden", "false" if enabled else "true")
        parser.set("Desktop Entry", "X-GNOME-Autostart-enabled", "true" if enabled else "false")
        self._write_desktop_parser(self.autostart_dir / name, parser)
        current = next(
            (item for item in self.list_autostart()["items"] if item["id"] == name), None)
        if current is None or current["enabled"] != enabled:
            return self._result(False, name, error="启动项写入后未生效")
        return self._result(True, name, enabled)

    def list_settings(self):
        return {key: self.get_value(key) for key in sorted(SETTING_SPECS)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    backend = SettingsBackend()
    if not argv or argv[0] == "list":
        result = {"ok": True, "settings": backend.list_settings()}
    elif argv[0] == "get" and len(argv) == 2:
        result = backend.get_value(argv[1])
    elif argv[0] == "set" and len(argv) == 3:
        try:
            value = json.loads(argv[2])
        except ValueError:
            value = argv[2]
        result = backend.set_value(argv[1], value)
    elif argv[0] == "audio" and len(argv) == 3 and argv[1] == "list":
        result = backend.list_audio_devices(argv[2])
    elif argv[0] == "audio" and len(argv) == 3 and argv[1] == "get":
        result = backend.get_audio_device(argv[2])
    elif argv[0] == "audio" and len(argv) == 4 and argv[1] == "set":
        result = backend.set_audio_device(argv[2], argv[3])
    elif (argv[0] == "audio" and len(argv) == 4 and argv[1] == "remember" and
          argv[2] == "output"):
        result = backend.remember_audio_output(argv[3])
    elif argv[0] == "default-app" and len(argv) == 3 and argv[1] == "list":
        result = backend.list_default_apps(argv[2])
    elif argv[0] == "default-app" and len(argv) == 3 and argv[1] == "get":
        result = backend.get_default_app(argv[2])
    elif argv[0] == "default-app" and len(argv) == 4 and argv[1] == "set":
        result = backend.set_default_app(argv[2], argv[3])
    elif argv[0] == "autostart" and len(argv) == 2 and argv[1] == "list":
        result = backend.list_autostart()
    elif argv[0] == "autostart" and len(argv) == 3:
        result = backend.set_autostart(argv[1], parse_bool(argv[2]))
    else:
        result = {"ok": False, "error": "用法：ming-settings-backend [list|get|set|audio|default-app|autostart]"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
