#!/usr/bin/env python3
"""Ming OS user-session device status and control backend."""

import argparse
import base64
import configparser
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


RFKILL = "/usr/sbin/rfkill"
BACKLIGHT_ROOT = Path("/sys/class/backlight")
MING_DISPLAY_CONTROL = "/usr/local/bin/ming-display-control"
BLUETOOTH_MODULES = {"btusb", "btintel", "btrtl", "btbcm", "ath3k"}
BLUETOOTH_USB_VENDOR_IDS = {
    "8087",  # Intel
    "0a5c",  # Broadcom
    "0cf3",  # Qualcomm Atheros
    "0e8d",  # MediaTek
}
FIRMWARE_QUERY = (
    "firmware.*(failed|missing|not found)|failed to load.*firmware"
)
# nmcli is only a compatibility fallback.  Keep its parser independent from
# the translated desktop while retaining UTF-8 bytes for displayable SSIDs.
C_LOCALE_PREFIX = ("env", "LC_ALL=C.UTF-8")
LEGACY_C_LOCALE_PREFIX = ("env", "LC_ALL=C")
C_LOCALE_VALUE = "C.UTF-8"
BSSID_PATTERN = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\Z")
IFNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}\Z")
NETWORK_ID_PATTERN = re.compile(r"ming-net-[0-9a-f]{32}\Z")


def encode_ssid_bytes(value):
    """Return a lossless SSID payload and a separate human display value."""
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogateescape")
    if not isinstance(value, (bytes, bytearray)):
        value = bytes(value or b"")
    raw = bytes(value)
    try:
        display = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "binary"
        display_parts = []
        for byte in raw:
            if 0x20 <= byte <= 0x7E:
                display_parts.append(chr(byte))
            else:
                display_parts.append("\\x%02x" % byte)
        display = "".join(display_parts)
    return {
        "display": display,
        "encoding": encoding,
        "ssid_bytes_b64": base64.b64encode(raw).decode("ascii"),
    }


def make_network_id(ifname, bssid, ssid_bytes):
    """Create an opaque, stable identity for one scanned AP."""
    payload = (
        str(ifname).encode("utf-8", "strict") + b"\0" +
        str(bssid).strip().lower().encode("ascii", "strict") + b"\0" +
        bytes(ssid_bytes)
    )
    return "ming-net-" + hashlib.sha256(payload).hexdigest()[:32]


def network_result(ok, state, reason_code, reason_text, retryable, **extra):
    """Build the stable network JSON envelope used by settings and diagnostics."""
    result = {
        "ok": bool(ok),
        "state": str(state),
        "reason_code": str(reason_code),
        "reason_text": str(reason_text),
        "retryable": bool(retryable),
    }
    result.update(extra)
    return result


def _managed_plain_dhcp_profile(parser, filename):
    if not parser.has_section("connection"):
        return False, "missing_connection"
    connection_id = parser.get("connection", "id", fallback="").strip()
    connection_type = parser.get("connection", "type", fallback="").strip().lower()
    managed = (
        connection_id.lower().startswith(("ming ", "calamares ")) or
        filename.lower().startswith(("ming-", "calamares-"))
    )
    if not managed:
        return False, "user_owned"
    if connection_type not in {"802-3-ethernet", "ethernet"}:
        return False, "not_ethernet"
    if parser.has_section("802-1x"):
        return False, "enterprise_profile"
    if parser.get("ipv4", "method", fallback="auto").strip().lower() != "auto":
        return False, "not_dhcp"
    for section_name in ("ipv4", "ipv6"):
        if not parser.has_section(section_name):
            continue
        for key, value in parser.items(section_name):
            lowered = key.lower()
            if lowered.startswith(("route", "address")) or lowered in {"gateway", "never-default"}:
                if value.strip() and value.strip().lower() not in {"false", "no", "0", "[]", "{}"}:
                    return False, "custom_route"
    return True, "managed_dhcp"


def migrate_network_profiles(directory="/etc/NetworkManager/system-connections",
                             expected_uid=0):
    """Safely loosen stale device bindings on Ming/Calamares DHCP profiles."""
    root = Path(directory)
    migrated = []
    skipped = []
    errors = []
    if not root.is_dir():
        return network_result(
            True, "ready", "directory_missing", "没有需要迁移的网络配置。", False,
            migrated=[], skipped=[], errors=[])
    for path in sorted(root.glob("*.nmconnection")):
        try:
            if path.is_symlink():
                skipped.append({"file": path.name, "reason": "symlink"})
                continue
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                skipped.append({"file": path.name, "reason": "not_regular"})
                continue
            if expected_uid is not None and metadata.st_uid != expected_uid:
                skipped.append({"file": path.name, "reason": "owner"})
                continue
            if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o022:
                skipped.append({"file": path.name, "reason": "writable"})
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str.lower
            with path.open("r", encoding="utf-8", errors="strict") as source:
                parser.read_file(source)
            allowed, reason = _managed_plain_dhcp_profile(parser, path.name)
            if not allowed:
                skipped.append({"file": path.name, "reason": reason})
                continue
            changed = False
            if parser.get("connection", "autoconnect", fallback="").strip().lower() != "true":
                parser.set("connection", "autoconnect", "true")
                changed = True
            if parser.has_option("connection", "interface-name"):
                parser.remove_option("connection", "interface-name")
                changed = True
            if parser.has_section("802-3-ethernet"):
                for option in ("mac-address", "assigned-mac-address"):
                    if parser.has_option("802-3-ethernet", option):
                        parser.remove_option("802-3-ethernet", option)
                        changed = True
            if not changed:
                skipped.append({"file": path.name, "reason": "already_current"})
                continue
            descriptor, temporary = tempfile.mkstemp(
                prefix=".%s." % path.name, suffix=".tmp", dir=str(root))
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                    parser.write(target)
                    target.flush()
                    os.fsync(target.fileno())
                os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
                if hasattr(os, "chown"):
                    os.chown(temporary, metadata.st_uid, metadata.st_gid)
                os.replace(temporary, path)
                try:
                    directory_fd = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            migrated.append(path.name)
        except (OSError, UnicodeError, configparser.Error) as exc:
            errors.append({"file": path.name, "error": str(exc)})
    if errors:
        return network_result(
            False, "error", "profile_migration_failed", "部分网络配置无法安全迁移。", True,
            migrated=migrated, skipped=skipped, errors=errors)
    return network_result(
        True, "ready", "profiles_migrated" if migrated else "no_changes",
        "网络配置迁移完成。" if migrated else "网络配置无需迁移。", False,
        migrated=migrated, skipped=skipped, errors=[])


def run_command(command, timeout=8):
    try:
        environment = None
        if len(command) >= 2 and tuple(command[:2]) in {
                ("env", "LC_ALL=C"), ("env", "LC_ALL=C.UTF-8")}:
            command = list(command[2:])
            environment = os.environ.copy()
            environment["LC_ALL"] = C_LOCALE_VALUE
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=timeout,
            env=environment,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def run_command_with_input(command, input_text, timeout=8):
    """Run a command with a secret on stdin, never in its argv or output."""
    try:
        environment = None
        if len(command) >= 2 and tuple(command[:2]) in {
                ("env", "LC_ALL=C"), ("env", "LC_ALL=C.UTF-8")}:
            command = list(command[2:])
            environment = os.environ.copy()
            environment["LC_ALL"] = C_LOCALE_VALUE
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=timeout,
            env=environment,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def clamp_percent(value, minimum=0):
    value = int(value)
    if value < minimum or value > 100:
        raise ValueError("percentage must be between %d and 100" % minimum)
    return value


def parse_percent(output):
    matches = re.findall(r"(?:^|[^\d])(\d{1,3})%", output or "")
    if not matches:
        return None
    return max(0, min(100, int(matches[-1])))


def split_nmcli_terse(line):
    """Split NetworkManager's colon-delimited output without losing escapes."""
    fields = []
    current = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def parse_integer(value):
    match = re.search(r"-?\d+", value or "")
    return int(match.group()) if match else None


def frequency_mhz(value):
    match = re.search(r"(\d+(?:\.\d+)?)\s*(MHz|GHz)?", value or "", re.I)
    if not match:
        return None
    frequency = float(match.group(1))
    if (match.group(2) or "").lower() == "ghz":
        frequency *= 1000
    return int(round(frequency))


def wifi_band(frequency):
    if frequency is None:
        return "unknown"
    if 2400 <= frequency <= 2500:
        return "2.4GHz"
    if 4900 <= frequency < 5925:
        return "5GHz"
    if 5925 <= frequency <= 7125:
        return "6GHz"
    return "unknown"


def wifi_channel(frequency):
    if frequency == 2484:
        return 14
    if frequency is None:
        return None
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency < 5925:
        return (frequency - 5000) // 5
    if 5955 <= frequency <= 7115:
        return (frequency - 5950) // 5
    return None


def c_locale_command(command):
    return [*C_LOCALE_PREFIX, *command]


def classify_wifi(
        wifi_devices, pci_output, usb_output, rfkill_output,
        firmware_output, network_error="", hardware_probes_ok=True,
        suspicious_usb_output=""):
    hardware_found = bool(wifi_devices or pci_output.strip() or usb_output.strip())
    blocked = bool(re.search(
        r"(?:Soft|Hard) blocked:\s*yes", rfkill_output or "", re.I))

    if not hardware_found:
        if not hardware_probes_ok:
            state = "diagnostic_unavailable"
            title = "无线硬件诊断不可用"
            detail = "无法完成无线 PCI/USB 硬件探测，因此不能确认没有无线网卡。"
        elif suspicious_usb_output.strip():
            state = "diagnostic_unavailable"
            title = "无线硬件需要进一步诊断"
            detail = "检测到疑似网络 USB 设备，尚不能安全确认其是否为无线网卡：%s" % (
                suspicious_usb_output.strip())
        else:
            state = "no_hardware"
            title = "未检测到无线网卡"
            detail = "当前设备没有可供 NetworkManager 使用的无线硬件。"
    elif blocked:
        state = "rfkill_blocked"
        title = "无线网卡已被禁用"
        detail = "请检查硬件无线开关或 BIOS，或解除 rfkill 阻止。"
    elif wifi_devices:
        state = "ready"
        title = "无线网络可用"
        detail = "，".join("%s (%s)" % item for item in wifi_devices)
    elif firmware_output.strip():
        state = "firmware_missing"
        title = "无线硬件缺少固件"
        detail = firmware_output.strip()
    else:
        state = "driver_missing"
        title = "无线硬件未绑定可用驱动"
        detail = (pci_output.strip() or usb_output.strip())

    if network_error:
        detail = "%s NetworkManager: %s" % (detail, network_error)
    reason_codes = {
        "ready": "ready",
        "rfkill_blocked": "rfkill_blocked",
        "firmware_missing": "firmware_missing",
        "driver_missing": "driver_missing",
        "no_hardware": "no_hardware",
        "diagnostic_unavailable": "diagnostic_unavailable",
    }
    return {
        "state": state,
        "reason_code": reason_codes.get(state, state),
        "reason_text": detail,
        "retryable": state in {"ready", "rfkill_blocked", "diagnostic_unavailable"},
        "present": state in {"ready", "rfkill_blocked"},
        "available": state == "ready",
        "title": title,
        "detail": detail,
        "devices": [name for name, _state in wifi_devices],
    }


class NetworkManagerBackend:
    """Lazy libnm/D-Bus adapter.

    Importing GI is deliberately deferred: diagnostics still work in a rescue
    shell or a minimal VM where the introspection package is absent.  The
    controller then uses its bounded C-locale nmcli fallback.
    """

    def __init__(self, sysfs_root="/sys"):
        self.sysfs_root = Path(sysfs_root)
        self.NM = None
        self.GLib = None
        self.Gio = None
        self.client = None
        self._carrier_snapshots = {}
        try:
            import gi
            gi.require_version("NM", "1.0")
            from gi.repository import Gio, GLib, NM
            self.NM = NM
            self.GLib = GLib
            self.Gio = Gio
            self.client = NM.Client.new(None)
        except Exception:
            self.NM = None
            self.GLib = None
            self.Gio = None
            self.client = None

    def available(self):
        if self.client is None:
            return False
        try:
            running = getattr(self.client, "get_manager_running", None)
            return bool(running()) if running is not None else True
        except Exception:
            return False

    @staticmethod
    def _enum_name(value):
        if value is None:
            return ""
        for attr in ("value_nick", "value_name"):
            name = getattr(value, attr, None)
            if name:
                return str(name).lower()
        text = str(value).rsplit(".", 1)[-1]
        return text.lower().replace("_", "-")

    @staticmethod
    def _ssid_bytes(ap):
        value = ap.get_ssid()
        if value is None:
            return b""
        for method in ("get_data", "get_bytes"):
            getter = getattr(value, method, None)
            if getter:
                try:
                    return bytes(getter())
                except Exception:
                    pass
        try:
            return bytes(value)
        except Exception:
            return str(value).encode("utf-8", "replace")

    @staticmethod
    def _bssid(ap):
        try:
            return (ap.get_bssid() or "").upper()
        except Exception:
            return ""

    def _wifi_devices(self):
        if not self.available():
            return []
        device_type = getattr(self.NM, "DeviceType", None)
        wifi_type = getattr(device_type, "WIFI", None) if device_type else None
        devices = []
        for device in self.client.get_devices() or []:
            try:
                if wifi_type is not None and device.get_device_type() != wifi_type:
                    continue
                if wifi_type is None and not hasattr(device, "get_access_points"):
                    continue
                devices.append(device)
            except Exception:
                continue
        return devices

    def wifi_radio_status(self):
        if not self.available():
            return network_result(False, "unavailable", "libnm_unavailable",
                                  "NetworkManager D-Bus 不可用。", True, enabled=False)
        try:
            getter = getattr(self.client, "wireless_get_enabled", None)
            if getter is None:
                getter = getattr(self.client, "get_wireless_enabled", None)
            enabled = bool(getter())
            return network_result(True, "enabled" if enabled else "disabled",
                                  "enabled" if enabled else "disabled",
                                  "无线网络已开启。" if enabled else "无线网络已关闭。",
                                  False, enabled=enabled)
        except Exception as exc:
            return network_result(False, "error", "networkmanager_error", str(exc), True,
                                  enabled=False)

    def wifi_radio(self, enabled):
        if not self.available():
            return network_result(False, "unavailable", "libnm_unavailable",
                                  "NetworkManager D-Bus 不可用。", True, enabled=False)
        try:
            setter = getattr(self.client, "wireless_set_enabled", None)
            if setter is None:
                setter = getattr(self.client, "set_wireless_enabled", None)
            setter(bool(enabled))
            return self.wifi_radio_status()
        except Exception as exc:
            return network_result(False, "error", "networkmanager_error", str(exc), True,
                                  enabled=not bool(enabled))

    def _ethernet_devices(self):
        if not self.available():
            return []
        device_type = getattr(self.NM, "DeviceType", None)
        ethernet_type = getattr(device_type, "ETHERNET", None) if device_type else None
        devices = []
        for device in self.client.get_devices() or []:
            try:
                if ethernet_type is not None and device.get_device_type() != ethernet_type:
                    continue
                if ethernet_type is None and not hasattr(device, "get_carrier"):
                    continue
                devices.append(device)
            except Exception:
                continue
        return devices

    def _security(self, ap):
        try:
            flags = int(ap.get_flags())
            wpa = int(ap.get_wpa_flags())
            rsn = int(ap.get_rsn_flags())
            security_type = getattr(self.NM, "80211ApSecurityFlags", None)
            def flag_value(*names):
                for name in names:
                    value = getattr(security_type, name, None) if security_type else None
                    if value is not None:
                        return int(value)
                return 0
            if rsn & flag_value("KEY_MGMT_SAE"):
                return "WPA3-SAE", "sae"
            if rsn & flag_value("KEY_MGMT_OWE"):
                return "OWE", "owe"
            enterprise = flag_value("KEY_MGMT_802_1X", "KEY_MGMT_802_1_X")
            if (wpa | rsn) & enterprise:
                return "802.1x", "enterprise"
            if wpa or rsn:
                return "WPA2", "wpa-psk"
            if flags:
                return "WEP", "wep"
            return "open", "open"
        except Exception:
            return "unknown", "unknown"

    def wifi_scan(self):
        if not self.available():
            return []
        # Ask NetworkManager for a fresh scan when the binding exposes the
        # async API.  A driver that cannot scan still returns its cached APs;
        # failure here is diagnostic, never a desktop blocker.
        for device in self._wifi_devices():
            requester = getattr(device, "request_scan_async", None)
            finisher = getattr(device, "request_scan_finish", None)
            if requester is None or finisher is None:
                continue
            try:
                self._run_async(
                    lambda done, cancellable, requester=requester: requester(
                        {}, cancellable, done, None),
                    lambda result, finisher=finisher: finisher(result), timeout=5)
            except Exception:
                pass
        rows = []
        for device in self._wifi_devices():
            ifname = str(device.get_iface() or "")
            try:
                access_points = device.get_access_points() or []
            except Exception:
                access_points = []
            active = None
            try:
                active = device.get_active_access_point()
            except Exception:
                pass
            active_path = active.get_path() if active is not None else ""
            for ap in access_points:
                raw = self._ssid_bytes(ap)
                bssid = self._bssid(ap)
                frequency = None
                signal = None
                try:
                    frequency = int(ap.get_frequency() or 0) or None
                except Exception:
                    pass
                try:
                    signal = int(ap.get_strength())
                except Exception:
                    pass
                security, key_mgmt = self._security(ap)
                rows.append({
                    "ifname": ifname,
                    "bssid": bssid,
                    "ssid_bytes": raw,
                    "channel": None,
                    "frequency_mhz": frequency,
                    "signal": signal,
                    "security": security,
                    "_key_mgmt": key_mgmt,
                    "active": bool(active_path and ap.get_path() == active_path),
                    "_ap": ap,
                    "_device": device,
                })
        return rows

    @staticmethod
    def _reason_from_text(text, default="network_error"):
        text = str(text or "").lower()
        if any(token in text for token in ("secret", "password", "auth")):
            return "authentication_failed", "认证失败", True
        if "rfkill" in text or "blocked" in text:
            return "rfkill_blocked", "无线设备被阻止", False
        if "dhcp" in text or "address" in text:
            return "dhcp_failed", "DHCP 未完成", True
        if "firmware" in text or "driver" in text:
            return "driver_or_firmware", "驱动或固件不可用", False
        if "ap" in text or "ssid" in text or "not found" in text:
            return "network_gone", "网络已消失", True
        return default, "NetworkManager 未返回可读原因", True

    def _run_async(self, starter, finisher, timeout=30):
        if not self.available() or self.GLib is None or self.Gio is None:
            return False, "libnm unavailable"
        try:
            cancellable = self.Gio.Cancellable.new()
            loop = self.GLib.MainLoop()
        except Exception as exc:
            return False, str(exc)
        result = {"terminal": False, "value": None, "error": ""}
        timeout_source = None
        timeout_fired = False

        def cancel():
            try:
                cancellable.cancel()
            except Exception:
                pass

        def finish(_source, async_result, _user_data=None):
            if result["terminal"]:
                return
            try:
                result["value"] = finisher(async_result)
            except Exception as exc:  # pragma: no cover - GI version dependent
                result["error"] = str(exc)
            finally:
                result["terminal"] = True
                loop.quit()

        def expire():
            nonlocal timeout_fired
            timeout_fired = True
            if result["terminal"]:
                return False
            result["terminal"] = True
            result["error"] = "NetworkManager 操作超时"
            cancel()
            loop.quit()
            return False

        try:
            timeout_source = self.GLib.timeout_add(int(timeout * 1000), expire)
            starter(finish, cancellable)
            if not result["terminal"]:
                loop.run()
            if not result["terminal"]:
                result["terminal"] = True
                result["error"] = "NetworkManager 操作未完成"
                cancel()
        except Exception as exc:
            if not result["terminal"]:
                result["terminal"] = True
                result["error"] = str(exc)
                cancel()
        finally:
            if timeout_source is not None and not timeout_fired:
                try:
                    self.GLib.source_remove(timeout_source)
                except Exception:
                    pass
        if result["error"]:
            return False, result["error"]
        return True, result["value"]

    def _wait_device_connected(self, device, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._state_name(device)
            if state in {"activated", "connected"}:
                return True, ""
            if state in {"failed", "unavailable", "unmanaged"}:
                return False, self._state_reason(device)
            try:
                context = self.GLib.MainContext.default()
                while context.pending():
                    context.iteration(False)
            except Exception:
                pass
            time.sleep(0.1)
        return False, "NetworkManager activation timeout"

    def wifi_connect(self, network_id, ifname, password=None):
        rows = self.wifi_scan()
        target = next((row for row in rows if
                       row.get("ifname") == ifname and
                       make_network_id(ifname, row.get("bssid", ""), row.get("ssid_bytes", b"")) == network_id), None)
        if target is None:
            return network_result(False, "unavailable", "network_gone", "扫描结果中未找到该无线网络。", True,
                                  network_id=network_id, ifname=ifname)
        if not self.available():
            return network_result(False, "unavailable", "libnm_unavailable", "NetworkManager D-Bus 不可用。", True,
                                  network_id=network_id, ifname=ifname)
        try:
            connection = self.NM.SimpleConnection.new()
            setting = self.NM.SettingConnection.new()
            setting.set_property("id", "Ming Wi-Fi %s" % network_id[-8:])
            setting.set_property("type", "802-11-wireless")
            connection.add_setting(setting)
            wireless = self.NM.SettingWireless.new()
            raw = bytes(target["ssid_bytes"])
            try:
                ssid = self.GLib.Bytes.new(raw)
            except Exception:
                ssid = raw
            wireless.set_property("ssid", ssid)
            wireless.set_property(
                "bssid", bytes.fromhex(str(target["bssid"]).replace(":", "")))
            connection.add_setting(wireless)
            key_mgmt = target.get("_key_mgmt", "unknown")
            if key_mgmt in {"enterprise", "wep"}:
                return network_result(
                    False, "unsupported", "security_unsupported",
                    "该网络需要在高级网络管理器中配置企业认证或 WEP。", False,
                    network_id=network_id, ifname=ifname)
            if password or key_mgmt == "owe":
                security = self.NM.SettingWirelessSecurity.new()
                security.set_property("key-mgmt", key_mgmt if key_mgmt in {"sae", "owe"} else "wpa-psk")
                if password:
                    security.set_property("psk", password)
                connection.add_setting(security)
            ipv4 = self.NM.SettingIP4Config.new()
            ipv4.set_property("method", "auto")
            connection.add_setting(ipv4)
            ipv6 = self.NM.SettingIP6Config.new()
            ipv6.set_property("method", "auto")
            connection.add_setting(ipv6)
            device = target["_device"]
            ap = target["_ap"]

            def start(done, cancellable):
                self.client.add_and_activate_connection_async(
                    connection, device, ap.get_path(), cancellable, done, None)

            def finish(async_result):
                return self.client.add_and_activate_connection_finish(async_result)

            ok, value = self._run_async(start, finish, timeout=10)
            if ok:
                ok, value = self._wait_device_connected(device, timeout=20)
                if ok:
                    return network_result(True, "connected", "connected", "无线网络已连接。", False,
                                          network_id=network_id, ifname=ifname)
            code, text, retryable = self._reason_from_text(value)
            return network_result(False, "error", code, text, retryable,
                                  network_id=network_id, ifname=ifname)
        except Exception as exc:
            code, text, retryable = self._reason_from_text(exc)
            return network_result(False, "error", code, text, retryable,
                                  network_id=network_id, ifname=ifname)

    def _state_name(self, device):
        try:
            return self._enum_name(device.get_state())
        except Exception:
            return "unknown"

    def _state_reason(self, device):
        try:
            reason = device.get_state_reason()
            if isinstance(reason, tuple):
                reason = reason[-1]
            name = self._enum_name(reason)
            return name or "unknown"
        except Exception:
            return "unknown"

    def _ip_info(self, device):
        ip = ""
        gateway = ""
        dns = []
        try:
            config = device.get_ip4_config()
            if config:
                addresses = config.get_addresses() or []
                if addresses:
                    address = addresses[0]
                    ip = "%s/%s" % (address.get_address(), address.get_prefix())
                gateway = str(config.get_gateway() or "")
                dns = [str(value) for value in (config.get_nameservers() or [])]
        except Exception:
            pass
        return ip, gateway, dns

    def _link_evidence(self, ifname):
        path = self.sysfs_root / "class" / "net" / ifname
        values = {}
        for name in ("carrier_changes", "carrier_up_count", "carrier_down_count"):
            try:
                values[name] = int((path / name).read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                values[name] = None
        previous = self._carrier_snapshots.get(ifname)
        current = values.get("carrier_changes")
        delta = (current - previous) if current is not None and previous is not None else 0
        values["delta"] = max(0, delta)
        values["recent"] = bool(delta > 0)
        values["flapping"] = bool(values.get("carrier_changes") and values["carrier_changes"] > 8)
        if current is not None:
            self._carrier_snapshots[ifname] = current
        return values

    def ethernet_status(self):
        if not self.available():
            return network_result(False, "unavailable", "libnm_unavailable", "NetworkManager D-Bus 不可用。", True,
                                  devices=[])
        devices = []
        for device in self._ethernet_devices():
            try:
                ifname = str(device.get_iface() or "")
                state = self._state_name(device)
                carrier = bool(device.get_carrier())
                ip, gateway, dns = self._ip_info(device)
                active = device.get_active_connection()
                profile = str(active.get_id() or "") if active else ""
                autoconnect = None
                if active:
                    remote = active.get_connection()
                    if remote:
                        settings = remote.get_setting_connection()
                        autoconnect = bool(settings.get_autoconnect()) if settings else None
                reason = self._state_reason(device)
                dhcp = "bound" if ip and state in {"activated", "connected"} else "none"
                speed = None
                try:
                    speed = int(device.get_speed() or 0) or None
                except Exception:
                    pass
                devices.append({
                    "device": ifname,
                    "driver": str(device.get_driver() or ""),
                    "carrier": carrier,
                    "speed_mbps": speed,
                    "state": "connected" if state in {"activated", "connected"} else state,
                    "nm_state": state,
                    "nm_reason": reason,
                    "profile": profile,
                    "autoconnect": autoconnect,
                    "dhcp": dhcp,
                    "ip": ip,
                    "route": gateway,
                    "dns": dns,
                    "link_flap": self._link_evidence(ifname),
                    "error": "",
                })
            except Exception as exc:  # pragma: no cover - device version dependent
                devices.append({"device": "", "state": "error", "error": str(exc)})
        connected = any(item.get("state") == "connected" for item in devices)
        if connected:
            return network_result(True, "connected", "connected", "有线网络已连接。", False, devices=devices,
                                  available=bool(devices))
        reason_code = "no_carrier" if devices and not any(item.get("carrier") for item in devices) else "disconnected"
        text = "网线未接入。" if reason_code == "no_carrier" else "有线网络未连接。"
        return network_result(bool(devices), "disconnected" if devices else "no_hardware",
                              reason_code, text, bool(devices), devices=devices, available=bool(devices))

    def ethernet_repair(self, ifname):
        if not IFNAME_PATTERN.fullmatch(ifname or ""):
            return network_result(False, "invalid", "invalid_interface", "网络接口名称格式无效。", False,
                                  devices=[])
        if not self.available():
            return network_result(False, "unavailable", "libnm_unavailable", "NetworkManager D-Bus 不可用。", True,
                                  devices=[])
        device = next((item for item in self._ethernet_devices()
                       if str(item.get_iface() or "") == ifname), None)
        if device is None:
            return network_result(False, "no_hardware", "interface_missing", "未找到指定有线网卡。", False,
                                  devices=[])
        active = device.get_active_connection()
        remote = active.get_connection() if active is not None else None
        if remote is None:
            # A disconnected device may still have a saved DHCP profile. Pick
            # only an ethernet profile compatible with this interface.
            for candidate in self.client.get_connections() or []:
                try:
                    setting = candidate.get_setting_connection()
                    if not setting or str(setting.get_connection_type() or "") not in {
                        "802-3-ethernet", "ethernet",
                    }:
                        continue
                    bound = str(setting.get_interface_name() or "")
                    if bound and bound != ifname:
                        continue
                    if setting.get_autoconnect() is False:
                        continue
                    remote = candidate
                    break
                except Exception:
                    continue
        if remote is None:
            return network_result(False, "disconnected", "profile_missing", "该接口没有可自动连接的网络配置。", True,
                                  devices=[])
        try:
            def start(done, cancellable):
                self.client.activate_connection_async(
                    remote, device, None, cancellable, done, None)
            def finish(async_result):
                return self.client.activate_connection_finish(async_result)
            ok, value = self._run_async(start, finish, timeout=8)
            if not ok:
                code, text, retryable = self._reason_from_text(value)
                return network_result(False, "error", code, text, retryable, devices=[])
            ok, value = self._wait_device_connected(device, timeout=12)
            if not ok:
                code, text, retryable = self._reason_from_text(value)
                return network_result(False, "error", code, text, retryable, devices=[])
            return self.ethernet_status()
        except Exception as exc:
            code, text, retryable = self._reason_from_text(exc)
            return network_result(False, "error", code, text, retryable, devices=[])


class DeviceController:
    def __init__(self, runner=run_command, executable=shutil.which,
                 backlight_root=BACKLIGHT_ROOT, input_runner=run_command_with_input,
                 settings_path=None, network_backend=None, sysfs_root="/sys",
                 display_control=MING_DISPLAY_CONTROL):
        self.runner = runner
        self.input_runner = input_runner
        self.executable = executable
        self.backlight_root = Path(backlight_root)
        self.sysfs_root = Path(sysfs_root)
        self.settings_path = Path(settings_path) if settings_path else (
            Path.home() / ".config" / "ming-os" / "settings.json")
        self.network_backend = network_backend
        self.display_control = str(display_control)
        self._network_backend_checked = network_backend is not None
        self._carrier_snapshots = {}

    def _run(self, command, timeout=8):
        return self.runner(command, timeout=timeout)

    def _run_with_input(self, command, input_text, timeout=8):
        return self.input_runner(command, input_text, timeout=timeout)

    def _run_c(self, command, timeout=8):
        primary = self._run(c_locale_command(command), timeout=timeout)
        # Older test doubles and third-party wrappers only recognise the
        # historical C-locale prefix.  Production subprocesses always use the
        # UTF-8 C locale above; the compatibility retry is never sent by the
        # default runner after a real command failure.
        if primary[0] != 0 and self.runner is not run_command:
            legacy = self._run([*LEGACY_C_LOCALE_PREFIX, *command], timeout=timeout)
            if legacy[0] == 0:
                return legacy
        return primary

    def _can_run(self, command):
        return bool(self.executable(command))

    def _software_brightness(self, action, value=None):
        """Delegate software dimming to the bounded user-session X11 helper."""
        command_name = Path(self.display_control).name
        if not self._can_run(command_name):
            return self._control_result(
                False, requested=value, error="ming-display-control 不可用",
                backend="xrandr-software", state="unavailable")
        command = [self.display_control, action, "--json"]
        if value is not None and action == "software-set":
            command.insert(2, str(int(value)))
        rc, output, error = self._run(command, timeout=8)
        try:
            payload = json.loads(output or "")
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            return self._control_result(
                False, requested=value,
                error=error or output or "软件亮度助手返回无效结果",
                backend="xrandr-software", state="unavailable")
        payload.setdefault("ok", rc == 0)
        payload.setdefault("available", bool(payload.get("ok")))
        payload.setdefault("state", "ready" if payload.get("ok") else "error")
        payload.setdefault("backend", "xrandr-software")
        payload.setdefault("requested", value)
        payload.setdefault("value", None)
        payload.setdefault("error", error if rc != 0 else "")
        payload["backend"] = "xrandr-software"
        return payload

    def _get_network_backend(self):
        if not self._network_backend_checked:
            candidate = NetworkManagerBackend(sysfs_root=self.sysfs_root)
            self.network_backend = candidate if candidate.available() else None
            self._network_backend_checked = True
        backend = self.network_backend
        if backend is None:
            return None
        try:
            return backend if backend.available() else None
        except Exception:
            return None

    def _read_volume(self, backend):
        if backend == "pactl":
            rc, output, error = self._run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            value = parse_percent(output)
        else:
            rc, output, error = self._run(["amixer", "sget", "Master"])
            value = parse_percent(output)
        return rc == 0 and value is not None, value, error or output

    @staticmethod
    def _control_result(ok, requested=None, value=None, error="", backend="",
                        available=False, state=None):
        if value is not None:
            try:
                value = max(0, min(100, int(round(float(value)))))
            except (TypeError, ValueError):
                value = None
        if state is None:
            state = "ready" if ok else (
                "unavailable" if not available else "error")
        return {
            "ok": bool(ok),
            "available": bool(available),
            "state": state,
            "backend": backend or "",
            "requested": requested,
            "value": value,
            "error": error or "",
        }

    @staticmethod
    def _pactl_info_defaults(output):
        defaults = {"sink": "", "source": ""}
        for line in (output or "").splitlines():
            if line.startswith("Default Sink:"):
                defaults["sink"] = line.partition(":")[2].strip()
            elif line.startswith("Default Source:"):
                defaults["source"] = line.partition(":")[2].strip()
        return defaults

    @staticmethod
    def _pactl_source_names(output):
        sources = []
        for line in (output or "").splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            name = fields[1].strip()
            if name and not name.endswith(".monitor"):
                sources.append(name)
        return sources

    @staticmethod
    def _audio_kind(name):
        value = (name or "").lower()
        if "bluez" in value or "bluetooth" in value:
            return "bluetooth"
        if "hdmi" in value or "displayport" in value or ".dp-" in value:
            return "hdmi"
        if re.search(r"(?:^|[._-])usb(?:[._-]|$)", value):
            return "usb"
        return "internal"

    @classmethod
    def _audio_device_display_name(cls, name):
        kind = cls._audio_kind(name)
        labels = {
            "internal": "内置扬声器",
            "hdmi": "HDMI / 显示器音频",
            "bluetooth": "蓝牙音频",
            "usb": "USB 音频",
        }
        return "%s（%s）" % (labels[kind], name)

    @classmethod
    def _pactl_sink_records(cls, output, default_sink=""):
        records = []
        for line in (output or "").splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            sink_id = fields[1].strip()
            if not sink_id:
                continue
            state = fields[-1].strip().upper()
            records.append({
                "id": sink_id,
                "display_name": cls._audio_device_display_name(sink_id),
                "kind": cls._audio_kind(sink_id),
                "available": state != "UNAVAILABLE",
                "active": sink_id == default_sink,
            })
        return records

    @staticmethod
    def _pactl_cards(output):
        cards = []
        current = None
        in_profiles = False
        for line in (output or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("Card #"):
                if current:
                    cards.append(current)
                current = {"name": "", "active_profile": "", "profiles": []}
                in_profiles = False
                continue
            if current is None:
                continue
            if stripped.startswith("Name:"):
                current["name"] = stripped.partition(":")[2].strip()
                continue
            if stripped == "Profiles:":
                in_profiles = True
                continue
            if stripped.startswith("Active Profile:"):
                current["active_profile"] = stripped.partition(":")[2].strip()
                in_profiles = False
                continue
            if stripped and not line[0].isspace():
                in_profiles = False
                continue
            if in_profiles:
                match = re.match(
                    r"\s*(.+?):\s+.*\(.*available:\s*(yes|no|unknown)\s*\)",
                    stripped, re.I)
                if match:
                    current["profiles"].append({
                        "name": match.group(1).strip(),
                        "available": match.group(2).lower() == "yes",
                    })
        if current:
            cards.append(current)
        return cards

    @staticmethod
    def _is_duplex_profile(profile):
        return "input:" in (profile or "") and "output:" in (profile or "")

    @staticmethod
    def _is_playback_profile(profile):
        value = (profile or "").strip().lower()
        if not value or value == "off":
            return False
        return not value.startswith("input:")

    @staticmethod
    def _is_external_audio_name(name):
        return bool(re.search(r"(?:^|[._-])(usb|bluez|hdmi)(?:[._-]|$)", name or "", re.I))

    @staticmethod
    def _card_for_sink(cards, sink_name):
        for card in cards:
            card_name = card.get("name", "")
            output_prefix = card_name.replace("_card.", "_output.", 1)
            if output_prefix and (sink_name == output_prefix or
                                  sink_name.startswith(output_prefix + ".")):
                return card
        return None

    @staticmethod
    def _audio_status_result(
            available=False, state="unavailable", backend="", value=None, error="",
            server_available=False, playback_ready=False, default_sink="",
            default_sink_present=False, playback_profile_valid=None,
            playback_devices=None, call_ready=False, default_source="",
            physical_input_present=False, input_muted=None, output_muted=None,
            duplex_profile_active=False, cards=None):
        return {
            "available": bool(available),
            "state": state,
            "backend": backend,
            "value": value,
            "error": error or "",
            "server_available": bool(server_available),
            "playback_ready": bool(playback_ready),
            "default_sink": default_sink or "",
            "default_sink_present": bool(default_sink_present),
            "playback_profile_valid": playback_profile_valid,
            "playback_devices": list(playback_devices or []),
            "call_ready": bool(call_ready),
            "default_source": default_source or "",
            "physical_input_present": bool(physical_input_present),
            "input_muted": input_muted,
            "output_muted": output_muted,
            "duplex_profile_active": bool(duplex_profile_active),
            "cards": list(cards or []),
        }

    def _pactl_call_snapshot(self):
        _info_rc, info, info_error = self._run(["pactl", "info"])
        _sources_rc, sources, sources_error = self._run(["pactl", "list", "short", "sources"])
        _source_mute_rc, source_mute, source_mute_error = self._run(
            ["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
        _sink_mute_rc, sink_mute, sink_mute_error = self._run(
            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        _cards_rc, cards, cards_error = self._run(["pactl", "list", "cards"])
        defaults = self._pactl_info_defaults(info)
        physical_sources = self._pactl_source_names(sources)
        parsed_cards = self._pactl_cards(cards)
        return {
            "defaults": defaults,
            "physical_sources": physical_sources,
            "input_muted": bool(re.search(r"Mute:\s*yes", source_mute or "", re.I)),
            "output_muted": bool(re.search(r"Mute:\s*yes", sink_mute or "", re.I)),
            "cards": parsed_cards,
            "errors": [value for value in (
                info_error, sources_error, source_mute_error, sink_mute_error, cards_error
            ) if value],
        }

    def audio_status(self):
        if self._can_run("pactl"):
            info_rc, info, info_error = self._run(["pactl", "info"])
            if info_rc != 0:
                return self._audio_status_result(
                    state="no_server", backend="pactl", error=(
                        info_error or info or "PulseAudio 服务没有运行。"))

            defaults = self._pactl_info_defaults(info)
            default_sink = defaults["sink"]
            if not default_sink or default_sink.lower() == "auto_null":
                return self._audio_status_result(
                    state="no_default_sink", backend="pactl", server_available=True,
                    default_source=defaults["source"],
                    error="PulseAudio 没有可用的默认输出设备。")

            volume_rc, volume_output, volume_error = self._run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            value = parse_percent(volume_output)
            if volume_rc != 0 or value is None:
                return self._audio_status_result(
                    state="no_default_sink", backend="pactl", server_available=True,
                    default_sink=default_sink, default_source=defaults["source"],
                    error=volume_error or volume_output or "默认输出设备不可用。")

            _sinks_rc, sinks_output, _sinks_error = self._run(
                ["pactl", "list", "short", "sinks"])
            playback_devices = self._pactl_sink_records(sinks_output, default_sink)
            default_device = next(
                (item for item in playback_devices if item["id"] == default_sink), None)
            if default_device is None:
                if _sinks_rc == 0:
                    return self._audio_status_result(
                        available=True, state="no_default_sink", backend="pactl",
                        server_available=True, default_sink=default_sink,
                        default_source=defaults["source"], playback_devices=playback_devices,
                        error="默认输出设备未出现在 PulseAudio 可用设备列表中。")
                default_device = {
                    "id": default_sink,
                    "display_name": self._audio_device_display_name(default_sink),
                    "kind": self._audio_kind(default_sink),
                    "available": True,
                    "active": True,
                }
                playback_devices.append(default_device)
            if not default_device.get("available"):
                return self._audio_status_result(
                    available=True, state="no_default_sink", backend="pactl", value=value,
                    server_available=True, default_sink=default_sink,
                    default_source=defaults["source"], playback_devices=playback_devices,
                    error="当前默认音频输出不可用，请选择内置扬声器或其他可用设备。")

            _sources_rc, sources, _sources_error = self._run(
                ["pactl", "list", "short", "sources"])
            source_mute_rc, source_mute, _source_mute_error = self._run(
                ["pactl", "get-source-mute", "@DEFAULT_SOURCE@"])
            sink_mute_rc, sink_mute, _sink_mute_error = self._run(
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            _cards_rc, cards_output, _cards_error = self._run(["pactl", "list", "cards"])
            cards = self._pactl_cards(cards_output)
            source_names = self._pactl_source_names(sources)
            source_present = defaults["source"] in source_names
            input_muted = (
                bool(re.search(r"Mute:\s*yes", source_mute or "", re.I))
                if source_mute_rc == 0 else None)
            output_muted = (
                bool(re.search(r"Mute:\s*yes", sink_mute or "", re.I))
                if sink_mute_rc == 0 else None)
            active_profiles = [card["active_profile"] for card in cards]
            duplex_active = any(
                self._is_duplex_profile(profile) for profile in active_profiles)
            matching_card = self._card_for_sink(cards, default_sink)
            playback_profile_valid = (
                self._is_playback_profile(matching_card.get("active_profile"))
                if matching_card else True)
            if output_muted:
                state = "muted"
            elif not playback_profile_valid:
                state = "invalid_profile"
            else:
                state = "ready"
            playback_ready = bool(
                default_device and playback_profile_valid and output_muted is not True)
            call_ready = bool(
                playback_ready and source_present and duplex_active
                and input_muted is False and output_muted is False)
            return self._audio_status_result(
                available=True, state=state, backend="pactl", value=value,
                server_available=True, playback_ready=playback_ready,
                default_sink=default_sink, default_sink_present=True,
                playback_profile_valid=playback_profile_valid,
                playback_devices=playback_devices, call_ready=call_ready,
                default_source=defaults["source"],
                physical_input_present=source_present, input_muted=input_muted,
                output_muted=output_muted, duplex_profile_active=duplex_active,
                cards=cards)

        if self._can_run("amixer"):
            ok, value, error = self._read_volume("amixer")
            if ok:
                return self._audio_status_result(
                    available=True, state="ready", backend="amixer", value=value,
                    playback_ready=True, playback_profile_valid=True)
            return self._audio_status_result(error=error)
        return self._audio_status_result(error="未检测到音频输出设备")

    @staticmethod
    def _active_playback_device(status):
        default_sink = (status or {}).get("default_sink", "")
        for device in (status or {}).get("playback_devices", []):
            if (device.get("id") == default_sink and device.get("available") and
                    device.get("active")):
                return device
        return None

    @staticmethod
    def _internal_analog_output(status):
        devices = (status or {}).get("playback_devices", [])
        internal = [
            device for device in devices
            if device.get("available") and device.get("kind") == "internal"
        ]
        for device in internal:
            if "analog" in (device.get("id") or "").lower():
                return device
        return internal[0] if internal else None

    @classmethod
    def _playback_profile_candidate(cls, card):
        """Choose an available playback profile on the currently selected card.

        This repairs a card left in ``off`` or an input-only profile without
        changing the user's HDMI, Bluetooth, USB or internal output choice.
        Prefer output-only profiles before duplex profiles, because a playback
        repair must not unexpectedly take ownership of a working microphone.
        """
        if not isinstance(card, dict):
            return ""
        candidates = [
            str(profile.get("name") or "")
            for profile in card.get("profiles", [])
            if isinstance(profile, dict) and profile.get("available")
            and cls._is_playback_profile(profile.get("name"))
        ]
        for profile in candidates:
            if profile.startswith("output:") and not cls._is_duplex_profile(profile):
                return profile
        return candidates[0] if candidates else ""

    def _saved_audio_output_selection(self):
        """Read Settings' user choice without creating or changing its file."""
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return ""
        selected = settings.get("audio_output_selection") if isinstance(settings, dict) else ""
        return selected.strip() if isinstance(selected, str) else ""

    def audio_select_output(self, output_id):
        """Honor an explicit user choice from the current PulseAudio sink list."""
        status = self.audio_status()
        if status.get("backend") != "pactl" or not status.get("server_available"):
            return {
                "ok": False, "selected": "", "changed": False,
                "action": "unavailable",
                "error": "PulseAudio 会话不可用，无法切换音频输出。",
                "status": status,
            }
        device = next(
            (item for item in status.get("playback_devices", [])
             if item.get("id") == output_id and item.get("available")),
            None)
        if not device:
            return {
                "ok": False, "selected": "", "changed": False,
                "action": "invalid_output",
                "error": "所选音频输出已不可用，请刷新设备列表后重试。",
                "status": status,
            }
        if device.get("active"):
            if not status.get("playback_ready"):
                repaired = self.audio_repair_playback()
                repaired_status = repaired.get("status") or status
                selected = (
                    repaired.get("ok") and repaired_status.get("default_sink") == output_id)
                return {
                    "ok": bool(selected), "selected": output_id if selected else "",
                    "changed": bool(repaired.get("changed")),
                    "action": "repaired_active_output" if selected else (
                        repaired.get("action") or "active_output_repair_failed"),
                    "error": "" if selected else (
                        repaired.get("error") or "无法恢复当前音频输出。"),
                    "status": repaired_status,
                }
            return {
                "ok": True, "selected": output_id, "changed": False,
                "action": "already_selected", "error": "", "status": status,
            }
        rc, output, error = self._run(["pactl", "set-default-sink", output_id])
        if rc != 0:
            return {
                "ok": False, "selected": "", "changed": False,
                "action": "select_failed",
                "error": error or output or "无法切换音频输出。",
                "status": status,
            }
        repaired = self.audio_status()
        selected = repaired.get("default_sink") == output_id
        return {
            "ok": selected, "selected": output_id if selected else "", "changed": selected,
            "action": "selected" if selected else "select_not_applied",
            "error": "" if selected else "音频输出切换后未能确认当前默认设备。",
            "status": repaired,
        }

    def audio_repair_playback(self):
        """Repair a missing output without replacing a valid user selection."""
        status = self.audio_status()
        if status.get("backend") != "pactl" or not status.get("server_available"):
            return {
                "ok": False, "changed": False, "action": "unavailable",
                "error": "PulseAudio 会话不可用，无法修复声音播放。",
                "status": status,
            }
        active = self._active_playback_device(status)
        if active:
            if status.get("playback_profile_valid") is False:
                card = self._card_for_sink(status.get("cards", []), active["id"])
                profile = self._playback_profile_candidate(card)
                if not card or not profile:
                    return {
                        "ok": False, "changed": False, "action": "profile_unavailable",
                        "error": "当前音频输出的播放配置无效，且没有可用的播放 profile。",
                        "status": status,
                    }
                rc, output, error = self._run(
                    ["pactl", "set-card-profile", card["name"], profile])
                if rc != 0:
                    return {
                        "ok": False, "changed": False, "action": "profile_failed",
                        "error": error or output or "无法恢复当前音频输出的播放配置。",
                        "status": status,
                    }
                repaired = self.audio_status()
                ready = bool(
                    repaired.get("playback_ready")
                    and repaired.get("default_sink") == active["id"])
                return {
                    "ok": ready, "changed": True,
                    "action": "repaired_active_profile",
                    "error": "" if ready else (
                        repaired.get("error") or "播放 profile 已切换，但输出仍未就绪。"),
                    "status": repaired,
                }
            if status.get("output_muted") is True:
                rc, output, error = self._run(
                    ["pactl", "set-sink-mute", active["id"], "0"])
                if rc != 0:
                    return {
                        "ok": False, "changed": False, "action": "unmute_failed",
                        "error": error or output or "无法解除当前音频输出静音。",
                        "status": status,
                    }
                repaired = self.audio_status()
                return {
                    "ok": repaired.get("output_muted") is False,
                    "changed": True, "action": "unmuted_selected_output",
                    "error": "" if repaired.get("output_muted") is False else "无法确认输出静音状态。",
                    "status": repaired,
                }
            return {
                "ok": True, "changed": False,
                "action": "preserved_selected_output", "error": "", "status": status,
            }

        saved_output = self._saved_audio_output_selection()
        candidate = next(
            (device for device in status.get("playback_devices", [])
             if device.get("id") == saved_output and device.get("available")),
            None)
        action = "restored_saved_output" if candidate else "selected_internal_output"
        if candidate is None:
            candidate = self._internal_analog_output(status)
        if not candidate:
            return {
                "ok": False, "changed": False, "action": "no_internal_output",
                "error": "未找到可安全恢复的内置模拟音频输出。",
                "status": status,
            }
        output_id = candidate["id"]
        rc, output, error = self._run(["pactl", "set-default-sink", output_id])
        if rc != 0:
            return {
                "ok": False, "changed": False, "action": "select_failed",
                "error": error or output or "无法恢复内置音频输出。",
                "status": status,
            }
        rc, output, error = self._run(["pactl", "set-sink-mute", output_id, "0"])
        if rc != 0:
            return {
                "ok": False, "changed": True, "action": "unmute_failed",
                "error": error or output or "内置音频输出已选择，但无法解除静音。",
                "status": self.audio_status(),
            }
        repaired = self.audio_status()
        selected = repaired.get("default_sink") == output_id
        return {
            "ok": selected, "changed": True, "action": action,
            "error": "" if selected else "未能确认内置音频输出已经恢复。",
            "status": repaired,
        }

    @classmethod
    def _internal_duplex_candidate(cls, cards):
        for card in cards:
            name = card.get("name", "")
            if not name or cls._is_external_audio_name(name):
                continue
            for profile in card.get("profiles", []):
                if profile.get("available") and cls._is_duplex_profile(profile.get("name")):
                    return card, profile["name"]
        return None, ""

    @staticmethod
    def _source_for_card(card_name, source_names):
        expected = re.sub(r"^alsa_card", "alsa_input", card_name or "")
        for source in source_names:
            if expected and source.startswith(expected):
                return source
        for source in source_names:
            if source.startswith("alsa_input") and not DeviceController._is_external_audio_name(source):
                return source
        return ""

    def audio_repair_call(self):
        """Restore an internal duplex source only when PulseAudio has none.

        External USB, HDMI and Bluetooth paths are deliberately left unchanged:
        a call repair button must never steal a working headset or display audio.
        """
        status = self.audio_status()
        if status["backend"] != "pactl":
            return {
                "ok": False,
                "changed": False,
                "action": "unavailable",
                "error": "PulseAudio 会话不可用，无法修复通话音频。",
                "status": status,
            }
        if status["physical_input_present"]:
            return {
                "ok": True,
                "changed": False,
                "action": "preserved_existing_input",
                "error": "",
                "status": status,
            }
        if self._is_external_audio_name(status["default_sink"]):
            return {
                "ok": False,
                "changed": False,
                "action": "external_output_preserved",
                "error": "当前默认输出是外接蓝牙、USB 或 HDMI 设备；不会覆盖其通话设置。",
                "status": status,
            }

        card, profile = self._internal_duplex_candidate(status["cards"])
        if not card:
            return {
                "ok": False,
                "changed": False,
                "action": "no_duplex_profile",
                "error": "未找到可用的内置全双工声卡配置。",
                "status": status,
            }
        rc, output, error = self._run(
            ["pactl", "set-card-profile", card["name"], profile])
        if rc != 0:
            return {
                "ok": False,
                "changed": False,
                "action": "profile_failed",
                "error": error or output or "无法切换内置声卡到全双工模式。",
                "status": status,
            }

        _sources_rc, sources_output, sources_error = self._run(
            ["pactl", "list", "short", "sources"])
        source = self._source_for_card(
            card["name"], self._pactl_source_names(sources_output))
        if not source:
            return {
                "ok": False,
                "changed": True,
                "action": "source_missing_after_profile",
                "error": sources_error or "全双工配置已切换，但没有检测到内置麦克风。",
                "status": self.audio_status(),
            }
        rc, output, error = self._run(["pactl", "set-default-source", source])
        if rc != 0:
            return {
                "ok": False,
                "changed": True,
                "action": "default_source_failed",
                "error": error or output or "无法设置内置麦克风为默认输入。",
                "status": self.audio_status(),
            }
        rc, output, error = self._run(
            ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"])
        if rc != 0:
            return {
                "ok": False,
                "changed": True,
                "action": "unmute_failed",
                "error": error or output or "无法解除内置麦克风静音。",
                "status": self.audio_status(),
            }
        repaired = self.audio_status()
        return {
            "ok": repaired["physical_input_present"],
            "changed": True,
            "action": "set_duplex_profile",
            "error": repaired["error"] if not repaired["physical_input_present"] else "",
            "status": repaired,
        }

    def audio_test_input(self):
        """Record three seconds through PulseAudio and report whether bytes arrive."""
        status = self.audio_status()
        if status["backend"] != "pactl" or not status["physical_input_present"]:
            return {
                "ok": False,
                "seconds": 3,
                "error": "未检测到可用的物理麦克风输入。",
                "status": status,
            }
        if status["input_muted"]:
            return {
                "ok": False,
                "seconds": 3,
                "error": "默认麦克风已静音。",
                "status": status,
            }
        if not self._can_run("parecord"):
            return {
                "ok": False,
                "seconds": 3,
                "error": "缺少 PulseAudio 录音工具 parecord。",
                "status": status,
            }
        capture_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="ming-mic-", suffix=".pcm", delete=False) as capture:
                capture_path = capture.name
            rc, output, error = self._run([
                "timeout", "3", "parecord", "--raw", "--format=s16le", "--rate=16000",
                "--channels=1", "--device=@DEFAULT_SOURCE@", capture_path,
            ], timeout=6)
            try:
                captured = Path(capture_path).stat().st_size
            except OSError:
                captured = 0
        finally:
            if capture_path:
                try:
                    Path(capture_path).unlink()
                except OSError:
                    pass
        # GNU timeout ends the otherwise continuous parecord stream with 124.
        ok = rc in {0, 124} and captured >= 4096
        return {
            "ok": ok,
            "seconds": 3,
            "bytes": captured,
            "error": "" if ok else (error or "未从麦克风捕获到有效音频。"),
            "status": self.audio_status(),
        }

    def set_volume(self, value, sink_id=None):
        try:
            value = clamp_percent(value)
        except (TypeError, ValueError) as exc:
            try:
                requested = int(value)
            except (TypeError, ValueError):
                requested = None
            return self._control_result(
                False, requested=requested, error=str(exc), state="invalid")
        if sink_id is not None:
            status = self.audio_status()
            valid = next((device for device in status.get("playback_devices", [])
                          if device.get("id") == sink_id and device.get("available")), None)
            if (status.get("backend") != "pactl" or
                    not status.get("server_available") or valid is None):
                result = self._control_result(
                    False, requested=value, error="所选音频输出已不可用，请刷新后重试。",
                    backend="pactl", available=bool(status.get("server_available")),
                    state="invalid_sink")
                result.update({"sink_id": sink_id or "", "muted": None})
                return result
            for command in (
                    ["pactl", "set-sink-volume", sink_id, "%d%%" % value],
                    ["pactl", "set-sink-mute", sink_id, "0"]):
                rc, output, error = self._run(command)
                if rc != 0:
                    result = self._control_result(
                        False, requested=value, error=error or output or "音量设置失败。",
                        backend="pactl", available=True)
                    result.update({"sink_id": sink_id, "muted": None})
                    return result
            volume_rc, volume_output, volume_error = self._run(
                ["pactl", "get-sink-volume", sink_id])
            mute_rc, mute_output, mute_error = self._run(
                ["pactl", "get-sink-mute", sink_id])
            effective = parse_percent(volume_output)
            muted_match = re.search(r"Mute:\s*(yes|no)", mute_output or "", re.I)
            muted = muted_match.group(1).lower() == "yes" if muted_match else None
            ok = volume_rc == 0 and effective is not None and mute_rc == 0 and muted is False
            result = self._control_result(
                ok, requested=value, value=effective, backend="pactl", available=True,
                error="" if ok else (volume_error or mute_error or "无法确认所选输出的音量和静音状态。"))
            result.update({"sink_id": sink_id, "muted": muted})
            return result
        errors = []
        write_succeeded = False
        last_backend = ""
        commands = (
            ("pactl", ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % value]),
            ("amixer", ["amixer", "sset", "Master", "%d%%" % value]),
        )
        for backend, command in commands:
            if not self._can_run(backend):
                continue
            rc, output, error = self._run(command)
            if rc != 0:
                errors.append(error or output or "%s 设置失败" % backend)
                continue
            write_succeeded = True
            last_backend = backend
            ok, effective, read_error = self._read_volume(backend)
            if ok:
                return self._control_result(
                    True, requested=value, value=effective, backend=backend,
                    available=True)
            errors.append(read_error or "%s 读回失败" % backend)
        return self._control_result(
            False,
            requested=value,
            error="；".join(errors) or "未检测到音频输出设备",
            backend=last_backend if write_succeeded else "",
            available=write_succeeded,
            state="error" if write_succeeded else "unavailable",
        )

    def _has_backlight(self):
        try:
            return self.backlight_root.is_dir() and any(self.backlight_root.iterdir())
        except OSError:
            return False

    def brightness_status(self):
        if not self._has_backlight():
            return self._software_brightness("software-status")
        if not self._can_run("brightnessctl"):
            return {
                "available": False,
                "value": None,
                "error": "brightnessctl 不可用，无法读取物理背光。",
                "backend": "brightnessctl",
                "state": "unavailable",
            }
        rc, output, error = self._run(["brightnessctl", "-m"])
        value = parse_percent(output)
        if rc == 0 and value is not None:
            return {
                "available": True,
                "value": value,
                "error": "",
                "backend": "brightnessctl",
                "state": "ready",
            }
        return {
            "available": False,
            "value": None,
            "error": error or "读取亮度失败",
            "backend": "brightnessctl",
            "state": "error",
        }

    def set_brightness(self, value):
        try:
            value = clamp_percent(value, minimum=1)
        except (TypeError, ValueError) as exc:
            try:
                requested = int(value)
            except (TypeError, ValueError):
                requested = None
            return self._control_result(
                False, requested=requested, error=str(exc), state="invalid")
        if not self._has_backlight():
            return self._software_brightness("software-set", value=value)
        if not self._can_run("brightnessctl"):
            return self._control_result(
                False, requested=value,
                error="brightnessctl 不可用，无法设置物理背光。",
                backend="brightnessctl", state="unavailable")
        rc, output, error = self._run(["brightnessctl", "set", "%d%%" % value])
        if rc != 0:
            return self._control_result(
                False, requested=value,
                error=error or output or "设置亮度失败",
                backend="brightnessctl", available=True, state="error")
        status = self.brightness_status()
        return self._control_result(
            bool(status["available"] and status["value"] is not None),
            requested=value,
            value=status["value"],
            error=status["error"],
            backend="brightnessctl",
            available=bool(status["available"]),
            state=("ready" if status["available"] and status["value"] is not None
                   else "error"),
        )

    def reapply_brightness(self):
        """Restore only saved software brightness after an X11 session starts."""
        if self._has_backlight():
            return self.brightness_status()
        return self._software_brightness("software-reapply")

    @staticmethod
    def _wireless_pci(output):
        lines = (output or "").splitlines()
        blocks = []
        current = []
        for line in lines:
            if line and not line[0].isspace():
                if current:
                    blocks.append("\n".join(current))
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append("\n".join(current))
        return "\n".join(
            block for block in blocks
            if re.search(r"Network controller|Wireless controller|802\.11", block, re.I)
        )

    @staticmethod
    def _wireless_usb(output):
        description = re.compile(r"wireless|wi-?fi|802\.11|\bwlan\b", re.I)
        trusted_ids = {"2357:011e"}
        wireless = []
        for line in (output or "").splitlines():
            device_id = re.search(r"\bID\s+([0-9a-f]{4}:[0-9a-f]{4})\b", line, re.I)
            if description.search(line) or (
                    device_id and device_id.group(1).lower() in trusted_ids):
                wireless.append(line)
        return "\n".join(wireless)

    @staticmethod
    def _suspicious_wireless_usb(output):
        evidence = re.compile(
            r"\b(?:network\s+(?:adapter|controller)|ethernet\s+(?:adapter|controller)|"
            r"usb\s+nic)\b|\brtl\d+(?:au|bu|cu|eu)\b",
            re.I,
        )
        return "\n".join(
            line for line in (output or "").splitlines() if evidence.search(line))

    @staticmethod
    def _wireless_firmware(output):
        pattern = re.compile(
            r"iwlwifi|iwlmvm|rtw[0-9_]*|rtl8|brcm|brcmfmac|b43|bcma|"
            r"ath[0-9a-z_]*|mt76|cfg80211|mac80211|wlan|wireless",
            re.I,
        )
        return "\n".join(
            line for line in (output or "").splitlines() if pattern.search(line))

    def wifi_radio_status(self):
        backend = self._get_network_backend()
        if backend is not None:
            return backend.wifi_radio_status()
        rc, output, error = self._run_c(["nmcli", "radio", "wifi"])
        enabled = output.strip().lower() == "enabled"
        if rc != 0:
            reason = error or output or "NetworkManager 不可用。"
            return network_result(False, "unavailable", "networkmanager_unavailable",
                                  reason, True, enabled=False, error=reason)
        return network_result(True, "enabled" if enabled else "disabled",
                              "enabled" if enabled else "disabled",
                              "无线网络已开启。" if enabled else "无线网络已关闭。",
                              False, enabled=enabled, error="")

    def wifi_radio(self, enabled):
        backend = self._get_network_backend()
        if backend is not None:
            return backend.wifi_radio(bool(enabled))
        target = "on" if enabled else "off"
        rc, output, error = self._run_c(["nmcli", "radio", "wifi", target])
        if rc != 0:
            reason = error or output or "NetworkManager 不可用。"
            return network_result(False, "error", "radio_change_failed", reason, True,
                                  enabled=not bool(enabled), error=reason)
        status = self.wifi_radio_status()
        if status.get("enabled") != bool(enabled):
            return network_result(False, "error", "radio_readback_failed",
                                  "无线开关状态未能确认。", True,
                                  enabled=status.get("enabled", False), error="无线开关状态未能确认。")
        return status

    def wifi_status(self):
        backend = self._get_network_backend()
        if backend is not None:
            try:
                devices = []
                for device in backend._wifi_devices():
                    ifname = str(device.get_iface() or "")
                    state = backend._state_name(device)
                    devices.append((ifname, state))
                if devices:
                    return classify_wifi(
                        wifi_devices=devices, pci_output="", usb_output="",
                        rfkill_output="", firmware_output="",
                        hardware_probes_ok=True)
            except Exception:
                # Keep the bounded diagnostics fallback available on old libnm.
                pass
        nm_rc, nm_output, nm_error = self._run_c(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
        devices = []
        for line in nm_output.splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[1] == "wifi":
                devices.append((fields[0], fields[2]))

        pci_rc, pci_all, _pci_error = self._run_c(["lspci", "-nnk"])
        usb_rc, usb_all, _usb_error = self._run_c(["lsusb"])
        _rfkill_rc, rfkill_output, _rfkill_error = self._run_c(
            [RFKILL, "list", "wifi"])
        _fw_rc, firmware_output, _fw_error = self._run_c([
            "journalctl", "-k", "-b", "--no-pager", "-g",
            "firmware.*(failed|missing|not found)|failed to load.*firmware",
            "-n", "8",
        ])
        return classify_wifi(
            wifi_devices=devices,
            pci_output=self._wireless_pci(pci_all),
            usb_output=self._wireless_usb(usb_all),
            rfkill_output=rfkill_output,
            firmware_output=self._wireless_firmware(firmware_output),
            network_error=(nm_error if nm_rc != 0 else ""),
            hardware_probes_ok=nm_rc == 0 and pci_rc == 0 and usb_rc == 0,
            suspicious_usb_output=self._suspicious_wireless_usb(usb_all),
        )

    def wifi_scan(self):
        backend = self._get_network_backend()
        if backend is not None:
            try:
                rows = backend.wifi_scan()
                networks = self._normalise_wifi_rows(rows)
                if networks:
                    return network_result(
                        True, "ready", "scan_complete", "无线扫描已完成。", False,
                        error="", networks=networks, backend="libnm")
                status = self.wifi_status()
                if status.get("state") in {"no_hardware", "diagnostic_unavailable"}:
                    return network_result(
                        False, status["state"], status["state"], status.get("detail", ""),
                        False, error=status.get("detail", ""), networks=[], backend="libnm")
                return network_result(
                    True, "ready", "no_networks", "本次扫描未发现可用网络。", True,
                    error="", networks=[], backend="libnm")
            except Exception as exc:
                backend_error = str(exc)
            else:
                backend_error = ""
        else:
            backend_error = ""
        command = [
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        ]
        rc, output, error = self._run_c(command)
        if rc != 0:
            reason = error or backend_error or "Wi-Fi 扫描诊断不可用。"
            return network_result(
                False, "diagnostic_unavailable", "networkmanager_unavailable", reason, True,
                error=reason, networks=[], backend="nmcli")

        rows = []
        for line in output.splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) != 8:
                continue
            frequency = frequency_mhz(fields[4])
            rows.append({
                "ifname": fields[7],
                "bssid": fields[1],
                "ssid_bytes": fields[2].encode("utf-8", "surrogateescape"),
                "channel": parse_integer(fields[3]),
                "frequency_mhz": frequency,
                "signal": parse_integer(fields[5]),
                "security": fields[6],
                "active": fields[0].strip().lower() in {"*", "yes"},
            })
        networks = self._normalise_wifi_rows(rows)
        if not networks:
            status = self.wifi_status()
            if status["state"] in {"no_hardware", "diagnostic_unavailable"}:
                return network_result(
                    False, status["state"], status["state"], status["detail"], False,
                    error=status["detail"], networks=[], backend="nmcli")
        return network_result(
            True, "ready", "scan_complete" if networks else "no_networks",
            "无线扫描已完成。" if networks else "本次扫描未发现可用网络。",
            not bool(networks), error="", networks=networks, backend="nmcli")

    @staticmethod
    def _normalise_wifi_rows(rows):
        networks = []
        for row in rows or []:
            ifname = str(row.get("ifname") or "")
            bssid = str(row.get("bssid") or "").upper()
            raw = row.get("ssid_bytes", b"")
            if isinstance(raw, str):
                raw = raw.encode("utf-8", "surrogateescape")
            if len(raw) > 32:
                continue
            if not IFNAME_PATTERN.fullmatch(ifname) or not BSSID_PATTERN.fullmatch(bssid):
                continue
            encoded = encode_ssid_bytes(raw)
            frequency = row.get("frequency_mhz")
            network = {
                "network_id": make_network_id(ifname, bssid, raw),
                "ifname": ifname,
                "bssid": bssid,
                # ssid remains a display-only compatibility key.  Callers must
                # connect with network_id and never feed it back to nmcli.
                "ssid": encoded["display"],
                "display": encoded["display"],
                "ssid_bytes_b64": encoded["ssid_bytes_b64"],
                "encoding": encoded["encoding"],
                "channel": row.get("channel") or wifi_channel(frequency),
                "frequency_mhz": frequency,
                "band": wifi_band(frequency),
                "signal": row.get("signal"),
                "security": str(row.get("security") or ""),
                "active": bool(row.get("active")),
            }
            networks.append(network)
        networks.sort(key=lambda network: (
            not network["active"],
            -(network["signal"] if network["signal"] is not None else -1),
            network["bssid"],
            network["ifname"],
        ))
        return networks

    @staticmethod
    def _wifi_connect_error(ssid, bssid, ifname):
        if not isinstance(ssid, str) or not ssid or ssid.startswith("-"):
            return "SSID 格式无效。"
        if len(ssid.encode("utf-8")) > 32 or any(ord(char) < 32 for char in ssid):
            return "SSID 格式无效。"
        if not isinstance(bssid, str) or not BSSID_PATTERN.fullmatch(bssid):
            return "BSSID 格式无效。"
        if not isinstance(ifname, str) or not IFNAME_PATTERN.fullmatch(ifname):
            return "网络接口名称格式无效。"
        return ""

    def wifi_connect(self, ssid=None, bssid=None, ifname=None, password=None,
                     network_id=None):
        if network_id is not None:
            if not NETWORK_ID_PATTERN.fullmatch(str(network_id)):
                return network_result(
                    False, "invalid", "invalid_network_id", "无线网络标识格式无效。", False,
                    network_id=network_id, ifname=ifname or "", error="无线网络标识格式无效。")
            if not isinstance(ifname, str) or not IFNAME_PATTERN.fullmatch(ifname):
                return network_result(
                    False, "invalid", "invalid_interface", "网络接口名称格式无效。", False,
                    network_id=network_id, ifname=ifname or "", error="网络接口名称格式无效。")
            backend = self._get_network_backend()
            if backend is not None:
                result = backend.wifi_connect(network_id, ifname, password=password)
                result.setdefault("error", "" if result.get("ok") else result.get("reason_text", ""))
                return result
            snapshot = self.wifi_scan()
            target = next((item for item in snapshot.get("networks", [])
                           if item.get("network_id") == network_id and item.get("ifname") == ifname), None)
            if target is None:
                return network_result(
                    False, "unavailable", "network_gone", "扫描结果中未找到该无线网络。", True,
                    network_id=network_id, ifname=ifname, error="扫描结果中未找到该无线网络。")
            command = [
                "nmcli", "--wait", "30", "device", "wifi", "connect", target["bssid"],
                "bssid", target["bssid"], "ifname", ifname,
            ]
            if password is not None:
                command.insert(1, "--ask")
                rc, _output, error = self._run_with_input(
                    c_locale_command(command), password + "\n", timeout=35)
            else:
                rc, _output, error = self._run_c(command, timeout=35)
            if rc == 0:
                return network_result(
                    True, "connected", "connected", "无线网络已连接。", False,
                    network_id=network_id, ifname=ifname, bssid=target["bssid"], error="")
            code, text, retryable = NetworkManagerBackend._reason_from_text(error)
            return network_result(
                False, "error", code, text, retryable,
                network_id=network_id, ifname=ifname, bssid=target["bssid"], error=text)

        # Transitional compatibility for old callers.  The formal settings UI
        # never uses this display-string path.
        validation_error = self._wifi_connect_error(ssid, bssid, ifname)
        if validation_error:
            return network_result(
                False, "invalid", "invalid_target", validation_error, False,
                ssid=ssid, bssid=bssid, ifname=ifname, error=validation_error)
        command = [
            "nmcli", "--wait", "30", "device", "wifi", "connect", ssid,
            "bssid", bssid, "ifname", ifname,
        ]
        if password is not None:
            command.insert(1, "--ask")
            rc, output, error = self._run_with_input(command, password + "\n", timeout=35)
        else:
            rc, output, error = self._run(command, timeout=35)
        if rc == 0:
            return network_result(
                True, "connected", "connected", "无线网络已连接。", False,
                ssid=ssid, bssid=bssid, ifname=ifname, error="")
        code, text, retryable = NetworkManagerBackend._reason_from_text(error)
        return network_result(
            False, "error", code, text, retryable,
            ssid=ssid, bssid=bssid, ifname=ifname,
            error="NetworkManager: %s" % text)

    @staticmethod
    def _bluetooth_usb_records(output):
        records = []
        for line in (output or "").splitlines():
            if not re.search(r"\bbluetooth\b", line, re.I):
                continue
            match = re.search(r"\bID\s+([0-9a-f]{4}:[0-9a-f]{4})\s+(.+)$", line, re.I)
            if match:
                records.append({
                    "bus": "usb",
                    "id": match.group(1).lower(),
                    "model": match.group(2).strip(),
                })
        return records

    @staticmethod
    def _bluetooth_usb_suspects(output):
        """Keep vendor-only combo-radio evidence out of the false no-hardware path."""
        suspects = []
        for line in (output or "").splitlines():
            match = re.search(r"\bID\s+([0-9a-f]{4}):([0-9a-f]{4})\s+(.+)$", line, re.I)
            if not match or match.group(1).lower() not in BLUETOOTH_USB_VENDOR_IDS:
                continue
            if re.search(r"\bbluetooth\b", line, re.I):
                continue
            suspects.append({
                "bus": "usb",
                "id": "%s:%s" % (match.group(1).lower(), match.group(2).lower()),
                "model": match.group(3).strip(),
            })
        return suspects

    @staticmethod
    def _bluetooth_pci_records(output):
        records = []
        lines = (output or "").splitlines()
        blocks = []
        current = []
        for line in lines:
            if line and not line[0].isspace():
                if current:
                    blocks.append(current)
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append(current)

        for block in blocks:
            text = "\n".join(block)
            if not re.search(r"\bbluetooth\b", text, re.I):
                continue
            header = block[0]
            address = header.split()[0]
            identity = re.search(r"\[([0-9a-f]{4}:[0-9a-f]{4})\]", header, re.I)
            model = re.sub(r"^[^:]+:\s*", "", header).strip()
            records.append({
                "bus": "pci",
                "id": (identity.group(1).lower() if identity else address),
                "model": model,
            })
        return records

    @staticmethod
    def _bluetooth_modules(output):
        modules = []
        for line in (output or "").splitlines():
            name = line.split(None, 1)[0].strip().lower() if line.strip() else ""
            if name in BLUETOOTH_MODULES:
                modules.append(name)
        return sorted(set(modules))

    @staticmethod
    def _bluetooth_firmware(output):
        pattern = re.compile(r"bluetooth|btusb|btintel|btrtl|btbcm|ath3k|\bhci\d*\b", re.I)
        return [
            line.strip() for line in (output or "").splitlines()
            if pattern.search(line)
        ]

    @staticmethod
    def _bluetooth_controller(list_output, show_output):
        pattern = re.compile(r"^Controller\s+([0-9A-F:]+)(?:\s+(.*))?$", re.I | re.M)
        match = pattern.search(list_output or "") or pattern.search(show_output or "")
        present = match is not None
        return {
            "present": present,
            "powered": bool(present and re.search(r"^\s*Powered:\s*yes\s*$", show_output or "", re.I | re.M)),
            "id": match.group(1) if match else "",
            "model": (match.group(2) or "").strip() if match else "",
        }

    def bluetooth_status(self):
        pci_rc, pci_output, _pci_error = self._run_c(["lspci", "-nnk"])
        usb_rc, usb_output, _usb_error = self._run_c(["lsusb"])
        _modules_rc, modules_output, _modules_error = self._run_c(["lsmod"])
        _rfkill_rc, rfkill_output, _rfkill_error = self._run_c([RFKILL, "list", "bluetooth"])
        active_rc, active_output, _active_error = self._run_c(
            ["systemctl", "is-active", "bluetooth.service"])
        enabled_rc, enabled_output, _enabled_error = self._run_c(
            ["systemctl", "is-enabled", "bluetooth.service"])
        _list_rc, list_output, _list_error = self._run_c(["bluetoothctl", "list"])
        _show_rc, show_output, _show_error = self._run_c(["bluetoothctl", "show"])
        _firmware_rc, firmware_output, _firmware_error = self._run_c([
            "journalctl", "-k", "-b", "--no-pager", "-g", FIRMWARE_QUERY,
            "-n", "16",
        ])

        hardware = self._bluetooth_pci_records(pci_output)
        hardware.extend(self._bluetooth_usb_records(usb_output))
        suspected_hardware = self._bluetooth_usb_suspects(usb_output)
        modules = self._bluetooth_modules(modules_output)
        firmware_evidence = self._bluetooth_firmware(firmware_output)
        rfkill = {
            "soft_blocked": bool(re.search(r"Soft blocked:\s*yes", rfkill_output or "", re.I)),
            "hard_blocked": bool(re.search(r"Hard blocked:\s*yes", rfkill_output or "", re.I)),
        }
        service = {
            "active": active_rc == 0 and active_output.strip() == "active",
            "enabled": enabled_rc == 0 and enabled_output.strip() in {
                "enabled", "static", "indirect", "generated",
            },
        }
        controller = self._bluetooth_controller(list_output, show_output)
        if controller["present"]:
            hardware.append({
                "bus": "controller",
                "id": controller["id"],
                "model": controller["model"],
            })

        if not hardware and (pci_rc != 0 or usb_rc != 0):
            state = "diagnostic_unavailable"
            title = "蓝牙硬件诊断不可用"
            detail = "无法完成蓝牙 PCI/USB 硬件探测，因此不能确认没有蓝牙硬件。"
            action = "retry_diagnostic"
        elif not hardware and (suspected_hardware or modules):
            state = "diagnostic_unavailable"
            title = "蓝牙硬件需要进一步诊断"
            evidence = suspected_hardware or [{"bus": "kernel", "id": module, "model": module}
                                              for module in modules]
            detail = "检测到未确认的蓝牙硬件或内核模块：%s。不会误报为无硬件。" % (
                "；".join(item["model"] for item in evidence))
            action = "retry_diagnostic"
        elif not hardware:
            state = "no_hardware"
            title = "未检测到蓝牙硬件"
            detail = "当前设备没有可用的蓝牙 USB、PCI 或控制器记录。"
            action = "none"
        elif rfkill["soft_blocked"] or rfkill["hard_blocked"]:
            state = "rfkill_blocked"
            title = "蓝牙已被禁用"
            detail = "请解除蓝牙的 rfkill 软件或硬件阻止。"
            action = "unblock_rfkill"
        elif not modules:
            state = "driver_missing"
            title = "蓝牙硬件未绑定驱动"
            detail = "未检测到 btusb、btintel、btrtl、btbcm 或 ath3k 驱动模块。"
            action = "install_driver"
        elif firmware_evidence:
            state = "firmware_missing"
            title = "蓝牙硬件缺少固件"
            detail = "；".join(firmware_evidence)
            action = "install_firmware"
        elif not service["active"]:
            state = "service_stopped"
            title = "蓝牙服务未运行"
            detail = "bluetooth.service 未处于 active 状态。"
            action = "start_service"
        elif not controller["present"] or not controller["powered"]:
            state = "controller_off"
            title = "蓝牙控制器已关闭"
            detail = "蓝牙服务已运行，但没有已开启的蓝牙控制器。"
            action = "power_on"
        else:
            state = "ready"
            title = "蓝牙可用"
            detail = "蓝牙控制器已开启，可以连接设备。"
            action = "none"

        return {
            "state": state,
            "hardware": hardware,
            "suspected_hardware": suspected_hardware,
            "modules": modules,
            "firmware_evidence": firmware_evidence,
            "rfkill": rfkill,
            "service": service,
            "controller": controller,
            "action": action,
            "title": title,
            "detail": detail,
            # Retained for existing status widgets while callers move to state/title.
            "available": state == "ready",
            "powered": controller["powered"],
            "text": "已开启" if state == "ready" else (
                "已关闭" if state in {"rfkill_blocked", "controller_off"} else "不可用"),
        }

    def battery_status(self):
        if not self._can_run("upower"):
            return {"available": False, "value": None, "text": ""}
        rc, output, _error = self._run(["upower", "-e"])
        if rc != 0:
            return {"available": False, "value": None, "text": ""}
        devices = [line.strip() for line in output.splitlines() if line.strip()]
        display_devices = [line for line in devices if line.rsplit("/", 1)[-1] == "DisplayDevice"]
        native_batteries = [
            line for line in devices
            if re.search(r"/(?:battery_)?BAT[0-9A-Z_-]*$", line, re.I)
        ]
        candidates = display_devices + native_batteries
        if not candidates:
            return {"available": False, "value": None, "text": ""}
        value = None
        for battery in candidates:
            rc, info, _error = self._run(["upower", "-i", battery])
            match = re.search(r"percentage:\s*(\d{1,3})%", info, re.I)
            if rc == 0 and match:
                value = int(match.group(1))
                break
        return {
            "available": value is not None,
            "value": value,
            "text": "%d%%" % value if value is not None else "--",
        }

    def ethernet_status(self):
        """Return wired state with link, profile and NetworkManager reasons."""
        backend = self._get_network_backend()
        if backend is not None:
            try:
                return backend.ethernet_status()
            except Exception as exc:
                backend_error = str(exc)
        else:
            backend_error = ""
        result = network_result(
            False, "no_hardware", "no_hardware", "未检测到有线网卡。", False,
            available=False, devices=[], error="")
        if not self._can_run("nmcli"):
            result.update(state="unavailable", reason_code="networkmanager_unavailable",
                          reason_text="NetworkManager 不可用。", retryable=True,
                          error="NetworkManager 不可用。")
            return result
        run_nmcli = self._run_c if self.runner is run_command else self._run
        rc, output, error = run_nmcli(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        if rc != 0:
            # Older NetworkManager releases and long-lived test/runtime
            # wrappers may not expose CONNECTION in device status.
            rc, output, error = run_nmcli(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
        if rc != 0:
            reason = error or backend_error or output or "NetworkManager 状态不可用。"
            result.update(ok=False, state="diagnostic_unavailable",
                          reason_code="networkmanager_unavailable", reason_text=reason,
                          retryable=True, error=reason)
            return result
        for line in output.splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) < 3 or fields[1] != "ethernet":
                continue
            device, connection_state = fields[0], fields[2]
            if not IFNAME_PATTERN.fullmatch(device):
                continue
            detail_command = [
                "nmcli", "-t", "-f",
                "GENERAL.DRIVER,GENERAL.SPEED,GENERAL.REASON,GENERAL.CONNECTION,"
                "WIRED-PROPERTIES.CARRIER,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,"
                "IP4.DHCP4.OPTION,GENERAL.STATE",
                "device", "show", device,
            ]
            detail_rc, detail, detail_error = run_nmcli(detail_command)
            if detail_rc != 0:
                detail_rc, detail, detail_error = run_nmcli([
                    "nmcli", "-t", "-f",
                    "GENERAL.DRIVER,WIRED-PROPERTIES.CARRIER,IP4.ADDRESS,IP4.GATEWAY,"
                    "IP4.DNS,IP4.DHCP4.OPTION", "device", "show", device,
                ])
            properties = {}
            if detail_rc == 0:
                for property_line in detail.splitlines():
                    property_fields = split_nmcli_terse(property_line)
                    if len(property_fields) < 2:
                        continue
                    key = re.sub(r"\[\d+\]$", "", property_fields[0])
                    properties.setdefault(key, []).append(":".join(property_fields[1:]))
            address = (properties.get("IP4.ADDRESS") or [""])[0]
            carrier = (properties.get("WIRED-PROPERTIES.CARRIER") or [""])[0]
            state = connection_state.strip().lower()
            if state in {"activated", "connected"}:
                state = "connected"
            elif state in {"disconnected", "unavailable", "connecting"}:
                state = "disconnected"
            profile = fields[3] if len(fields) > 3 else ""
            if not profile or profile == "--":
                profile = (properties.get("GENERAL.CONNECTION") or [""])[0]
            autoconnect = None
            ipv4_method = ""
            if profile and profile != "--":
                profile_rc, profile_output, _profile_error = run_nmcli([
                    "nmcli", "-g", "connection.autoconnect,ipv4.method",
                    "connection", "show", profile,
                ])
                if profile_rc == 0:
                    profile_values = profile_output.splitlines()
                    if profile_values:
                        autoconnect = profile_values[0].strip().lower() in {"yes", "true", "on"}
                    if len(profile_values) > 1:
                        ipv4_method = profile_values[1].strip().lower()
            item = {
                "device": device,
                "driver": (properties.get("GENERAL.DRIVER") or [""])[0],
                "carrier": carrier.strip().lower() in {"yes", "true", "on"},
                "speed_mbps": parse_integer((properties.get("GENERAL.SPEED") or [""])[0]),
                "dhcp": "bound" if properties.get("IP4.DHCP4.OPTION") or ipv4_method == "auto" else "none",
                "ip": address,
                "route": (properties.get("IP4.GATEWAY") or [""])[0],
                "dns": [value for value in properties.get("IP4.DNS", []) if value],
                "state": state,
                "nm_state": (properties.get("GENERAL.STATE") or [connection_state])[0],
                "nm_reason": (properties.get("GENERAL.REASON") or [""])[0],
                "profile": profile,
                "autoconnect": autoconnect,
                "link_flap": self._link_evidence(device),
                "error": "" if detail_rc == 0 else detail_error,
            }
            result["devices"].append(item)
        if result["devices"]:
            result.update(
                ok=True, available=True,
                state=("connected" if any(item["state"] == "connected"
                                          for item in result["devices"])
                       else "disconnected"),
                reason_code=("connected" if any(item["state"] == "connected"
                                                for item in result["devices"])
                             else "disconnected"),
                reason_text=("有线网络已连接。" if any(item["state"] == "connected"
                                                  for item in result["devices"])
                             else "有线网络未连接。"),
                retryable=True,
            )
        return result

    def _link_evidence(self, ifname):
        path = self.sysfs_root / "class" / "net" / ifname
        evidence = {}
        for name in ("carrier_changes", "carrier_up_count", "carrier_down_count"):
            try:
                evidence[name] = int((path / name).read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                evidence[name] = None
        previous = self._carrier_snapshots.get(ifname)
        current = evidence.get("carrier_changes")
        delta = (current - previous) if current is not None and previous is not None else 0
        evidence["delta"] = max(0, delta)
        evidence["recent"] = bool(delta > 0)
        evidence["flapping"] = bool(
            evidence.get("carrier_changes") is not None and
            evidence.get("carrier_changes") > 8)
        if current is not None:
            self._carrier_snapshots[ifname] = current
        return evidence

    def ethernet_repair(self, ifname=None):
        """Reconnect only the requested interface/profile, never all Ethernet."""
        if ifname is None:
            # API compatibility for pre-26.4 callers.  The installed CLI
            # requires --ifname, so new UI paths cannot accidentally repair a
            # different interface.
            before = self.ethernet_status()
            if not before.get("devices"):
                return network_result(
                    False, "no_hardware", "no_hardware", before.get("error") or "未检测到有线网卡。", True,
                    changed=False, action="no_hardware", error=before.get("error") or "未检测到有线网卡。",
                    status=before)
            errors = []
            changed = False
            run = getattr(self, "_run_c", None) or getattr(self, "_run", None)
            for item in before["devices"]:
                if item.get("state") == "connected":
                    continue
                device = item.get("device", "")
                if not device or run is None:
                    continue
                command = ["nmcli", "device", "connect", device]
                try:
                    rc, output, error = run(command, timeout=20)
                except TypeError:
                    rc, output, error = run(command)
                if rc == 0:
                    changed = True
                else:
                    errors.append(error or output or "%s 连接失败" % device)
            after = self.ethernet_status()
            connected = any(item.get("state") == "connected"
                            for item in after.get("devices", []))
            message = "；".join(errors) or after.get("error", "")
            return network_result(
                connected, "connected" if connected else "error",
                "connected" if connected else "repair_failed",
                "有线网络已连接。" if connected else message or "有线网络修复失败。",
                not connected, changed=changed, action="reconnected" if connected else "repair_failed",
                error=message, status=after)
        if not isinstance(ifname, str) or not IFNAME_PATTERN.fullmatch(ifname):
            return network_result(False, "invalid", "invalid_interface", "网络接口名称格式无效。", False,
                                  changed=False, action="invalid", error="网络接口名称格式无效。", status={})
        backend = self._get_network_backend()
        if backend is not None:
            result = backend.ethernet_repair(ifname)
            result.setdefault("changed", bool(result.get("ok")))
            result.setdefault("action", "reconnected" if result.get("ok") else "repair_failed")
            result.setdefault("error", "" if result.get("ok") else result.get("reason_text", ""))
            result.setdefault("status", result)
            return result
        before = self.ethernet_status()
        selected = next((item for item in before.get("devices", [])
                         if item.get("device") == ifname), None)
        if selected is None:
            return network_result(False, "no_hardware", "interface_missing", "未找到指定有线网卡。", False,
                                  changed=False, action="no_hardware", error="未找到指定有线网卡。", status=before)
        if selected.get("state") == "connected":
            return network_result(True, "connected", "already_connected", "有线网络已经连接。", False,
                                  changed=False, action="already_connected", error="", status=before)
        command = ["nmcli", "device", "connect", ifname]
        rc, output, error = self._run_c(command, timeout=20)
        after = self.ethernet_status()
        connected = any(item.get("device") == ifname and item.get("state") == "connected"
                        for item in after.get("devices", []))
        if connected:
            return network_result(True, "connected", "connected", "有线网络已连接。", False,
                                  changed=rc == 0, action="reconnected", error="", status=after)
        code, text, retryable = NetworkManagerBackend._reason_from_text(error or output)
        return network_result(False, "error", code, text, retryable,
                              changed=rc == 0, action="repair_failed", error=text, status=after)

    def audio_widget_status(self):
        """Read only the fields needed by the always-visible status widget."""
        if not self._can_run("pactl"):
            return self._audio_status_result(state="unavailable", error="pactl 不可用")
        info_rc, info, info_error = self._run(["pactl", "info"])
        if info_rc != 0:
            return self._audio_status_result(
                state="no_server", backend="pactl", error=info_error or info or "PulseAudio 服务没有运行。")
        defaults = self._pactl_info_defaults(info)
        default_sink = defaults.get("sink", "")
        if not default_sink or default_sink.lower() == "auto_null":
            return self._audio_status_result(
                state="no_default_sink", backend="pactl", server_available=True,
                default_source=defaults.get("source", ""), error="没有可用的默认输出设备。")
        volume_rc, volume_output, volume_error = self._run(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        value = parse_percent(volume_output)
        sinks_rc, sinks_output, sinks_error = self._run(
            ["pactl", "list", "short", "sinks"])
        playback_devices = self._pactl_sink_records(sinks_output, default_sink)
        sink_present = any(item.get("id") == default_sink for item in playback_devices)
        mute_rc, mute_output, mute_error = self._run(
            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if volume_rc != 0 or value is None:
            return self._audio_status_result(
                state="no_default_sink", backend="pactl", server_available=True,
                default_sink=default_sink, default_sink_present=sink_present,
                playback_devices=playback_devices,
                error=volume_error or volume_output or "无法读取默认输出音量。")
        return self._audio_status_result(
            available=True, state="ready", backend="pactl", value=value,
            server_available=True, playback_ready=bool(sinks_rc == 0 and sink_present),
            default_sink=default_sink, default_sink_present=sink_present,
            playback_devices=playback_devices,
            output_muted=(bool(re.search(r"Mute:\s*yes", mute_output or "", re.I))
                          if mute_rc == 0 else None),
            error=sinks_error if sinks_rc != 0 else (mute_error if mute_rc != 0 else ""),
        )

    def wifi_widget_status(self):
        """Return a cheap Wi-Fi state without PCI/USB or journal diagnostics."""
        backend = self._get_network_backend()
        if backend is not None:
            try:
                devices = [
                    (str(device.get_iface() or ""), backend._state_name(device))
                    for device in backend._wifi_devices()
                ]
                if devices:
                    return classify_wifi(
                        wifi_devices=devices, pci_output="", usb_output="",
                        rfkill_output="", firmware_output="", hardware_probes_ok=True)
            except Exception:
                pass
        rc, output, error = self._run_c(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
        devices = []
        for line in output.splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[1] == "wifi":
                devices.append((fields[0], fields[2]))
        return classify_wifi(
            wifi_devices=devices, pci_output="", usb_output="", rfkill_output="",
            firmware_output="", network_error=error if rc != 0 else "",
            hardware_probes_ok=rc == 0)

    def bluetooth_widget_status(self):
        """Use one bounded BlueZ query for the compact widget."""
        if not self._can_run("bluetoothctl"):
            return {"available": False, "text": "不可用", "state": "diagnostic_unavailable"}
        rc, output, error = self._run(["bluetoothctl", "show"])
        if rc != 0:
            return {"available": False, "text": "不可用", "state": "diagnostic_unavailable", "error": error}
        powered = bool(re.search(r"Powered:\s*yes", output, re.I))
        return {
            "available": True,
            "text": "已开启" if powered else "已关闭",
            "state": "ready" if powered else "controller_off",
            "powered": powered,
        }

    def widget_status(self):
        """Lightweight status snapshot for the expanded widget only."""
        return {
            "audio": self.audio_widget_status(),
            "brightness": self.brightness_status(),
            "wifi": self.wifi_widget_status(),
            "bluetooth": self.bluetooth_widget_status(),
            "battery": self.battery_status(),
        }

    def status(self):
        return {
            "audio": self.audio_status(),
            "brightness": self.brightness_status(),
            "wifi": self.wifi_status(),
            "bluetooth": self.bluetooth_status(),
            "ethernet": self.ethernet_status(),
            "battery": self.battery_status(),
        }


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-device-control")
    subparsers = parser.add_subparsers(dest="action", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    wifi_scan = subparsers.add_parser("wifi-scan")
    wifi_scan.add_argument("--json", action="store_true")
    wifi_radio_status = subparsers.add_parser("wifi-radio-status")
    wifi_radio_status.add_argument("--json", action="store_true")
    wifi_radio = subparsers.add_parser("wifi-radio")
    wifi_radio.add_argument("state", choices=("on", "off"))
    wifi_radio.add_argument("--json", action="store_true")
    wifi_connect = subparsers.add_parser("wifi-connect")
    wifi_connect.add_argument("--network-id")
    # Kept as a migration-only compatibility path for older Settings builds;
    # the current UI always sends network-id and never sends the display SSID.
    wifi_connect.add_argument("--ssid")
    wifi_connect.add_argument("--bssid")
    wifi_connect.add_argument("--ifname", required=True)
    wifi_connect.add_argument("--password-stdin", action="store_true")
    bluetooth_status = subparsers.add_parser("bluetooth-status")
    bluetooth_status.add_argument("--json", action="store_true")
    ethernet_status = subparsers.add_parser("ethernet-status")
    ethernet_status.add_argument("--json", action="store_true")
    ethernet_repair = subparsers.add_parser("ethernet-repair")
    ethernet_repair.add_argument("--ifname", required=True)
    ethernet_repair.add_argument("--json", action="store_true")
    migrate_profiles = subparsers.add_parser("migrate-network-profiles")
    migrate_profiles.add_argument("--directory", default="/etc/NetworkManager/system-connections")
    migrate_profiles.add_argument("--json", action="store_true")
    audio_status = subparsers.add_parser("audio-status")
    audio_status.add_argument("--json", action="store_true")
    subparsers.add_parser("audio-repair-call")
    subparsers.add_parser("audio-repair-playback")
    subparsers.add_parser("audio-test-input")
    audio_output = subparsers.add_parser("audio-select-output")
    audio_output.add_argument("--id", dest="output_id", required=True)
    volume = subparsers.add_parser("set-volume")
    volume.add_argument("value", type=int)
    volume.add_argument("--sink", dest="sink_id")
    brightness = subparsers.add_parser("set-brightness")
    brightness.add_argument("value", type=int)
    reapply_brightness = subparsers.add_parser("reapply-brightness")
    reapply_brightness.add_argument("--json", action="store_true")
    return parser


def main(argv=None, controller=None, stdout=None, stdin=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    if any(argument == "--password" or argument.startswith("--password=") for argument in argv):
        print(json.dumps({
            "ok": False,
            "error": "此接口不接受密码；请通过 NetworkManager 密钥管理界面提供。",
        }, ensure_ascii=False, sort_keys=True), file=stdout)
        return 2
    args = build_parser().parse_args(argv)
    controller = controller or DeviceController()
    if args.action == "status":
        result = controller.status()
    elif args.action == "wifi-scan":
        result = controller.wifi_scan()
    elif args.action == "wifi-radio-status":
        result = controller.wifi_radio_status()
    elif args.action == "wifi-radio":
        result = controller.wifi_radio(args.state == "on")
    elif args.action == "wifi-connect":
        password = None
        if args.password_stdin:
            source = stdin or sys.stdin
            password = source.readline(257).rstrip("\r\n")
            if not password:
                password = None
        if args.network_id:
            result = controller.wifi_connect(
                network_id=args.network_id, ifname=args.ifname, password=password)
        else:
            result = controller.wifi_connect(
                args.ssid, args.bssid, args.ifname, password=password)
    elif args.action == "bluetooth-status":
        result = controller.bluetooth_status()
    elif args.action == "ethernet-status":
        result = controller.ethernet_status()
    elif args.action == "ethernet-repair":
        result = controller.ethernet_repair(ifname=args.ifname)
    elif args.action == "migrate-network-profiles":
        result = migrate_network_profiles(args.directory, expected_uid=0)
    elif args.action == "audio-status":
        result = controller.audio_status()
    elif args.action == "audio-repair-call":
        result = controller.audio_repair_call()
    elif args.action == "audio-repair-playback":
        result = controller.audio_repair_playback()
    elif args.action == "audio-test-input":
        result = controller.audio_test_input()
    elif args.action == "audio-select-output":
        result = controller.audio_select_output(args.output_id)
    elif args.action == "set-volume":
        result = (controller.set_volume(args.value, sink_id=args.sink_id)
                  if args.sink_id else controller.set_volume(args.value))
    elif args.action == "reapply-brightness":
        result = controller.reapply_brightness()
    else:
        result = controller.set_brightness(args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if args.action in {
        "status", "wifi-radio-status", "bluetooth-status", "ethernet-status", "audio-status"
    } or result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
