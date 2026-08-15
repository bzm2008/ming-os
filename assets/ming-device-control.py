#!/usr/bin/env python3
"""Ming OS user-session device status and control backend."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
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
C_LOCALE_PREFIX = ("env", "LC_ALL=C")
BSSID_PATTERN = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\Z")
IFNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}\Z")
NETWORK_ID_PATTERN = re.compile(r"[a-f0-9]{32}\Z")
WIFI_SCAN_CACHE_MAX_AGE = 180
NET_ROOT = Path("/sys/class/net")
ETHERNET_CONNECTIVITY_URLS = (
    "https://connectivitycheck.gstatic.com/generate_204",
    "https://cp.cloudflare.com/generate_204",
    "https://connectivitycheck.platform.hicloud.com/generate_204",
)
ETHERNET_CONNECTIVITY_URL = ETHERNET_CONNECTIVITY_URLS[0]
POWER_PROFILE_PATH = Path("/run/ming-os/power-profile")


def run_command(command, timeout=8):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def run_command_with_input(command, input_text, timeout=8):
    """Run a command with a secret on stdin, never in its argv or output."""
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
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


def brightnessctl_levels(output):
    """Return the current and maximum raw levels from brightnessctl -m."""
    for line in (output or "").splitlines():
        fields = line.rsplit(",", 4)
        if len(fields) != 5:
            continue
        try:
            current = int(fields[-3])
            maximum = int(fields[-2])
        except ValueError:
            continue
        if maximum > 0 and 0 <= current <= maximum:
            return current, maximum
    return None


def brightnessctl_readback_matches_request(requested, actual_percent, levels):
    """Accept only raw levels reachable by rounding the requested percentage."""
    if levels is None:
        return actual_percent == requested
    current, maximum = levels
    lower = requested * maximum // 100
    upper = (requested * maximum + 99) // 100
    return lower <= current <= upper


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


def c_locale_command(command):
    return [*C_LOCALE_PREFIX, *command]


def classify_wifi(
        wifi_devices, pci_output, usb_output, rfkill_output,
        firmware_output, network_error="", hardware_probes_ok=True,
        suspicious_usb_output=""):
    hardware_found = bool(wifi_devices or pci_output.strip() or usb_output.strip())
    blocked = bool(re.search(
        r"(?:Soft|Hard) blocked:\s*yes", rfkill_output or "", re.I))
    firmware_policy = "redistributable_or_unknown"
    action = "none"
    if re.search(r'\bb43(?:-open)?/ucode\d+[^"\s]*\.fw\b', firmware_output or "", re.I):
        firmware_policy = "unredistributable_b43"
        action = "show_b43_help"

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
        if firmware_policy == "unredistributable_b43":
            state = "firmware_external_required"
            title = "Broadcom b43 固件需手动处理"
            detail = (
                "%s\n\n检测到 Broadcom b43 固件缺失。该固件不属于 Ming OS "
                "可随公开 ISO 再分发的固件集合，因此不可内置或伪装为一键修复；"
                "请查看兼容说明，按硬件型号选择官方固件、有线联网或 USB 网卡等方案。"
            ) % detail
    else:
        state = "driver_missing"
        title = "无线硬件未绑定可用驱动"
        detail = (pci_output.strip() or usb_output.strip())

    if network_error:
        detail = "%s NetworkManager: %s" % (detail, network_error)
    return {
        "state": state,
        "present": state in {"ready", "rfkill_blocked"},
        "available": state == "ready",
        "title": title,
        "detail": detail,
        "devices": [name for name, _state in wifi_devices],
        "action": action,
        "firmware_policy": firmware_policy,
        "redistributable_firmware": firmware_policy != "unredistributable_b43",
    }


class DeviceController:
    def __init__(self, runner=run_command, executable=shutil.which,
                 backlight_root=BACKLIGHT_ROOT, input_runner=run_command_with_input,
                 settings_path=None, software_brightness_path=None, environment=None,
                 wifi_scan_cache_path=None, net_root=NET_ROOT,
                 power_profile_path=None, display_control=MING_DISPLAY_CONTROL):
        self.runner = runner
        self.input_runner = input_runner
        self.executable = executable
        self.backlight_root = Path(backlight_root)
        self.settings_path = Path(settings_path) if settings_path else (
            Path.home() / ".config" / "ming-os" / "settings.json")
        # software_brightness_path/environment remain constructor-only
        # compatibility arguments; the user-session display helper owns them.
        self.display_control = str(display_control)
        self.wifi_scan_cache_path = (
            Path(wifi_scan_cache_path) if wifi_scan_cache_path else
            Path.home() / ".cache" / "ming-os" / "wifi-scan.json")
        self.net_root = Path(net_root)
        self.power_profile_path = (
            Path(power_profile_path) if power_profile_path else POWER_PROFILE_PATH)

    def _run(self, command, timeout=8):
        return self.runner(command, timeout=timeout)

    def _run_with_input(self, command, input_text, timeout=8):
        return self.input_runner(command, input_text, timeout=timeout)

    def _run_c(self, command, timeout=8):
        return self._run(c_locale_command(command), timeout=timeout)

    def _can_run(self, command):
        return bool(self.executable(command))

    @staticmethod
    def _wpctl_volume(output):
        match = re.search(
            r"Volume:\s*([0-9]+(?:[.,][0-9]+)?)", output or "", re.I)
        try:
            value = int(round(float(match.group(1).replace(",", ".")) * 100))
        except (AttributeError, TypeError, ValueError):
            return None
        return max(0, min(100, value))

    def _read_volume(self, backend):
        if backend == "wpctl":
            rc, output, error = self._run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            value = self._wpctl_volume(output)
        elif backend == "pactl":
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
            control_backend="",
            server_available=False, playback_ready=False, default_sink="",
            default_sink_present=False, playback_profile_valid=None,
            playback_devices=None, call_ready=False, default_source="",
            physical_input_present=False, input_muted=None, output_muted=None,
            duplex_profile_active=False, cards=None):
        return {
            "available": bool(available),
            "state": state,
            "backend": backend,
            "control_backend": control_backend or backend,
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
        wpctl_error = ""
        wpctl_status = None
        if self._can_run("wpctl"):
            wpctl_rc, output, wpctl_error = self._run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            value = self._wpctl_volume(output)
            if wpctl_rc == 0 and value is not None:
                muted = bool(re.search(r"\[MUTED\]", output or "", re.I))
                sink = "@DEFAULT_AUDIO_SINK@"
                wpctl_status = self._audio_status_result(
                    available=True, state="muted" if muted else "ready",
                    backend="wpctl", value=value, server_available=True,
                    playback_ready=not muted, default_sink=sink,
                    default_sink_present=True, playback_profile_valid=True,
                    playback_devices=[{
                        "id": sink, "display_name": "默认 PipeWire 音频输出",
                        "kind": "internal", "available": True, "active": True,
                    }], output_muted=muted)

        if self._can_run("pactl"):
            info_rc, info, info_error = self._run(["pactl", "info"])
            if info_rc != 0:
                if wpctl_status is not None:
                    return wpctl_status
                pactl_error = info_error or info or "PipeWire 的 PulseAudio 兼容服务没有运行。"
                if self._can_run("amixer"):
                    alsa_ok, alsa_value, alsa_error = self._read_volume("amixer")
                    if alsa_ok:
                        return self._audio_status_result(
                            available=True, state="ready", backend="amixer",
                            value=alsa_value, server_available=True,
                            playback_ready=True, default_sink="Master",
                            default_sink_present=True, playback_profile_valid=True,
                            output_muted=False)
                    pactl_error = "%s；ALSA：%s" % (
                        pactl_error, alsa_error or "默认混音器不可用")
                return self._audio_status_result(
                    state="no_server", backend="pactl", error=pactl_error)
            else:
                pactl_error = ""

            defaults = self._pactl_info_defaults(info)
            default_sink = defaults["sink"]
            if not default_sink or default_sink.lower() == "auto_null":
                # Keep the real sink/card inventory even when the compatibility
                # server has not selected a default yet.  The repair action uses this
                # inventory to recover an internal analog output.
                sinks_rc, sinks_output, _sinks_error = self._run(
                    ["pactl", "list", "short", "sinks"])
                cards_rc, cards_output, _cards_error = self._run(
                    ["pactl", "list", "cards"])
                playback_devices = (
                    self._pactl_sink_records(sinks_output, "")
                    if sinks_rc == 0 else [])
                cards = self._pactl_cards(cards_output) if cards_rc == 0 else []
                if wpctl_status is not None:
                    return wpctl_status
                return self._audio_status_result(
                    state="no_default_sink", backend="pactl", server_available=True,
                    default_source=defaults["source"],
                    playback_devices=playback_devices,
                    cards=cards,
                    error="PipeWire 音频服务没有可用的默认输出设备。")

            volume_rc, volume_output, volume_error = self._run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            value = parse_percent(volume_output)
            if volume_rc != 0 or value is None:
                if wpctl_status is not None:
                    return wpctl_status
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
                    if wpctl_status is not None:
                        return wpctl_status
                    return self._audio_status_result(
                        available=True, state="no_default_sink", backend="pactl",
                        server_available=True, default_sink=default_sink,
                        default_source=defaults["source"], playback_devices=playback_devices,
                        error="默认输出设备未出现在 PipeWire 可用设备列表中。")
                default_device = {
                    "id": default_sink,
                    "display_name": self._audio_device_display_name(default_sink),
                    "kind": self._audio_kind(default_sink),
                    "available": True,
                    "active": True,
                }
                playback_devices.append(default_device)
            if not default_device.get("available"):
                if wpctl_status is not None:
                    return wpctl_status
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
                control_backend="wpctl" if wpctl_status is not None else "pactl",
                server_available=True, playback_ready=playback_ready,
                default_sink=default_sink, default_sink_present=True,
                playback_profile_valid=playback_profile_valid,
                playback_devices=playback_devices, call_ready=call_ready,
                default_source=defaults["source"],
                physical_input_present=source_present, input_muted=input_muted,
                output_muted=output_muted, duplex_profile_active=duplex_active,
                cards=cards)

        if wpctl_status is not None:
            return wpctl_status
        if self._can_run("amixer"):
            ok, value, error = self._read_volume("amixer")
            if ok:
                return self._audio_status_result(
                    available=True, state="ready", backend="amixer", value=value,
                    server_available=True, playback_ready=True, default_sink="Master",
                    default_sink_present=True, playback_profile_valid=True,
                    output_muted=False)
            return self._audio_status_result(error=error)
        if wpctl_error:
            return self._audio_status_result(
                state="no_server", backend="wpctl",
                error=wpctl_error or "PipeWire 服务没有运行。")
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
        """Honor an explicit user choice from the current PipeWire sink list."""
        status = self.audio_status()
        if status.get("backend") != "pactl" or not status.get("server_available"):
            return {
                "ok": False, "selected": "", "changed": False,
                "action": "unavailable",
                "error": "PipeWire 音频会话不可用，无法切换音频输出。",
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
        if status.get("backend") == "wpctl" and status.get("server_available"):
            if status.get("output_muted") is not True:
                return {
                    "ok": bool(status.get("playback_ready")), "changed": False,
                    "action": "preserved_pipewire_output", "error": status.get("error", ""),
                    "status": status,
                }
            rc, output, error = self._run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
            if rc != 0:
                return {
                    "ok": False, "changed": False, "action": "unmute_failed",
                    "error": error or output or "无法解除 PipeWire 输出静音。",
                    "status": status,
                }
            repaired = self.audio_status()
            ready = bool(
                repaired.get("playback_ready")
                and repaired.get("output_muted") is False)
            return {
                "ok": ready, "changed": True, "action": "unmuted_pipewire_output",
                "error": "" if ready else "无法确认 PipeWire 输出静音状态。",
                "status": repaired,
            }
        if status.get("backend") != "pactl" or not status.get("server_available"):
            return {
                "ok": False, "changed": False, "action": "unavailable",
                "error": "PipeWire 音频会话不可用，无法修复声音播放。",
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
        """Restore an internal duplex source only when PipeWire has none.

        External USB, HDMI and Bluetooth paths are deliberately left unchanged:
        a call repair button must never steal a working headset or display audio.
        """
        status = self.audio_status()
        if status["backend"] != "pactl":
            return {
                "ok": False,
                "changed": False,
                "action": "unavailable",
                "error": "PipeWire 音频会话不可用，无法修复通话音频。",
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
        """Record three seconds through the PipeWire compatibility layer and report bytes."""
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
                "error": "缺少 PipeWire 兼容录音工具 parecord。",
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

    def set_volume(self, value):
        try:
            value = clamp_percent(value)
        except (TypeError, ValueError) as exc:
            try:
                requested = int(value)
            except (TypeError, ValueError):
                requested = None
            return self._control_result(
                False, requested=requested, error=str(exc), state="invalid")
        errors = []
        write_succeeded = False
        last_backend = ""
        commands = (
            ("wpctl", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%d%%" % value]),
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
            if backend == "wpctl":
                mute_rc, mute_output, mute_error = self._run(
                    ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
                if mute_rc != 0:
                    errors.append(mute_error or mute_output or "无法解除当前输出静音")
                    continue
            elif backend == "pactl":
                mute_rc, mute_output, mute_error = self._run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
                if mute_rc != 0:
                    errors.append(mute_error or mute_output or "无法解除当前输出静音")
                    continue
            ok, effective, read_error = self._read_volume(backend)
            if ok:
                if backend == "wpctl":
                    mute_rc, mute_output, mute_error = self._run(
                        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
                    if mute_rc != 0 or re.search(r"\[MUTED\]", mute_output or "", re.I):
                        errors.append(mute_error or mute_output or "无法确认当前输出静音状态")
                        continue
                elif backend == "pactl":
                    mute_rc, mute_output, mute_error = self._run(
                        ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
                    if mute_rc != 0 or re.search(r"Mute:\s*yes", mute_output or "", re.I):
                        errors.append(mute_error or mute_output or "无法确认当前输出静音状态")
                        continue
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

    def _software_brightness(self, action, value=None, wait_seconds=0):
        """Delegate software brightness to the X11 user-session helper."""
        if not self._can_run(self.display_control):
            return self._control_result(
                False, requested=value, error="ming-display-control 不可用。",
                backend="xrandr-software", state="unavailable")
        try:
            wait_seconds = max(0.0, min(12.0, float(wait_seconds)))
        except (TypeError, ValueError):
            return self._control_result(
                False, requested=value, error="等待时间必须是 0 到 12 秒。",
                backend="xrandr-software", state="invalid")
        command = [self.display_control, action]
        if value is not None and action == "software-set":
            command.append(str(int(value)))
        if action == "software-reapply" and wait_seconds:
            command.extend(["--wait-seconds", "%g" % float(wait_seconds)])
        command.append("--json")
        process_timeout = max(8.0, wait_seconds + 2.0)
        rc, output, error = self._run(command, timeout=process_timeout)
        try:
            payload = json.loads(output or "")
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            return self._control_result(
                False, requested=value,
                error=error or output or "软件亮度助手返回了无效结果。",
                backend="xrandr-software", state="unavailable")
        readback = payload.get("value")
        value_is_valid = (
            isinstance(readback, (int, float)) and not isinstance(readback, bool)
            and 0 <= float(readback) <= 100
        )
        success = (
            rc == 0
            and payload.get("ok") is True
            and payload.get("available") is True
            and payload.get("state") == "ready"
            and isinstance(payload.get("error"), str)
            and value_is_valid
        )
        if success:
            payload["value"] = int(round(float(readback)))
            payload["backend"] = "xrandr-software"
            payload.setdefault("requested", value)
            return payload

        declared_failure = payload.get("ok") is False
        trusted_failure = (
            declared_failure
            and payload.get("available") is False
            and payload.get("state") in {"invalid", "unavailable", "error"}
            and isinstance(payload.get("error"), str)
            and (readback is None or value_is_valid)
        )
        untrusted_payload = not trusted_failure
        failure_state = payload.get("state") if (
            declared_failure and payload.get("state") in {"invalid", "unavailable", "error"}
        ) else "error"
        if rc != 0 and payload.get("ok") is True:
            failure_error = "软件亮度助手退出码 %d 与成功结果冲突。" % rc
        elif not declared_failure and rc == 0:
            failure_error = "软件亮度助手成功结果字段无效。"
        else:
            failure_error = payload.get("error") if isinstance(payload.get("error"), str) else ""
            failure_error = failure_error or error or "软件亮度助手执行失败。"
        payload.update({
            "ok": False,
            "available": False,
            "state": failure_state,
            "backend": "xrandr-software",
            "requested": payload.get("requested", value),
            "value": (
                int(round(float(readback)))
                if value_is_valid and not untrusted_payload else None),
            "error": failure_error,
        })
        if untrusted_payload:
            payload["output_values"] = {}
        return payload

    def _physical_brightness_status(self):
        if not self._has_backlight():
            return self._software_brightness("software-status"), None
        if not self._can_run("brightnessctl"):
            return {
                "available": False,
                "value": None,
                "error": "物理背光控制不可用。",
                "backend": "brightnessctl",
                "state": "unavailable",
            }, None
        rc, output, error = self._run(["brightnessctl", "-m"])
        value = parse_percent(output)
        if rc == 0 and value is not None:
            return {
                "available": True,
                "value": value,
                "error": "",
                "backend": "brightnessctl",
                "state": "ready",
            }, brightnessctl_levels(output)
        return {
            "available": False,
            "value": None,
            "error": error or "读取亮度失败",
            "backend": "brightnessctl",
            "state": "error",
        }, None

    def brightness_status(self):
        status, _step = self._physical_brightness_status()
        return status

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
                False, requested=value, error="物理背光控制不可用。",
                backend="brightnessctl", state="unavailable")
        rc, output, error = self._run(["brightnessctl", "set", "%d%%" % value])
        if rc != 0:
            actual, _levels = self._physical_brightness_status()
            write_error = error or output or "设置亮度失败"
            if actual.get("error") and not actual.get("available"):
                write_error = "%s；读回失败：%s" % (write_error, actual["error"])
            return self._control_result(
                False, requested=value,
                value=actual.get("value"), error=write_error,
                backend="brightnessctl", available=bool(actual.get("available")),
                state="error")
        status, levels = self._physical_brightness_status()
        readback_matches_request = bool(
            status["available"]
            and status["value"] is not None
            and brightnessctl_readback_matches_request(value, status["value"], levels)
        )
        readback_error = status["error"]
        if status["available"] and status["value"] is not None and not readback_matches_request:
            readback_error = "物理亮度读回与请求不一致：请求 %d%%，实际 %d%%。" % (
                value, status["value"])
        return self._control_result(
            readback_matches_request,
            requested=value,
            value=status["value"],
            error=readback_error,
            backend="brightnessctl",
            available=bool(status["available"]),
            state=("ready" if readback_matches_request else "error"),
        )

    def reapply_brightness(self, wait_seconds=0):
        if self._has_backlight():
            return self.brightness_status()
        return self._software_brightness(
            "software-reapply", wait_seconds=wait_seconds)

    def restore_software_brightness(self):
        """Compatibility alias for older autostart entries."""
        return self.reapply_brightness()

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
            r"iwlwifi|iwlmvm|mwifiex|mrvl|rtw[0-9_]*|rtl8|brcm|brcmfmac|b43|bcma|"
            r"ath[0-9a-z_]*|mt76|cfg80211|mac80211|wlan|wireless",
            re.I,
        )
        return "\n".join(
            line for line in (output or "").splitlines() if pattern.search(line))

    def wifi_status(self):
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
        command = [
            "nmcli", "-t", "-f",
            "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE",
            "dev", "wifi", "list",
        ]
        rc, output, error = self._run_c(command)
        if rc != 0:
            return {
                "ok": False,
                "state": "diagnostic_unavailable",
                "error": "Wi-Fi 扫描诊断不可用。",
                "networks": [],
            }

        networks = []
        for line in output.splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) != 8:
                continue
            frequency = frequency_mhz(fields[4])
            ssid = fields[2]
            ssid_bytes = ssid.encode("utf-8", errors="surrogateescape")
            encoding = "unknown" if "\ufffd" in ssid else "utf-8"
            bssid = fields[1].upper()
            ifname = fields[7]
            network_id = self._wifi_network_id(ssid_bytes, bssid, ifname)
            networks.append({
                "network_id": network_id,
                "ifname": fields[7],
                "bssid": bssid,
                "ssid": ssid,
                "ssid_bytes_b64": base64.b64encode(ssid_bytes).decode("ascii"),
                "encoding": encoding,
                "channel": parse_integer(fields[3]),
                "frequency_mhz": frequency,
                "band": wifi_band(frequency),
                "signal": parse_integer(fields[5]),
                "security": fields[6],
                "active": fields[0].strip().lower() in {"*", "yes"},
            })
        networks.sort(key=lambda network: (
            not network["active"],
            -(network["signal"] if network["signal"] is not None else -1),
            network["bssid"],
            network["ifname"],
        ))
        self._write_wifi_scan_cache(networks)
        if not networks:
            status = self.wifi_status()
            if status["state"] in {"no_hardware", "diagnostic_unavailable"}:
                return {
                    "ok": False,
                    "state": status["state"],
                    "error": status["detail"],
                    "networks": [],
                }
        return {"ok": True, "error": "", "networks": networks}

    @staticmethod
    def _wifi_network_id(ssid_bytes, bssid, ifname):
        identity = b"ming-wifi-v1\0" + ssid_bytes + b"\0" + bssid.lower().encode("ascii")
        identity += b"\0" + ifname.encode("ascii")
        return hashlib.sha256(identity).hexdigest()[:32]

    def _write_wifi_scan_cache(self, networks):
        payload = {
            "schema": 1,
            "generated_at": int(time.time()),
            "networks": [{
                "network_id": network.get("network_id", ""),
                "ssid_bytes_b64": network.get("ssid_bytes_b64", ""),
                "encoding": network.get("encoding", ""),
                "bssid": network.get("bssid", ""),
                "ifname": network.get("ifname", ""),
            } for network in networks],
        }
        temporary = None
        try:
            self.wifi_scan_cache_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".wifi-scan-", dir=str(self.wifi_scan_cache_path.parent))
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.wifi_scan_cache_path)
            return ""
        except OSError as exc:
            return str(exc)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _read_wifi_scan_cache(self):
        try:
            payload = json.loads(self.wifi_scan_cache_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

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

    @staticmethod
    def _wifi_failure_reason(output, error):
        detail = "%s %s" % (output or "", error or "")
        normalized = detail.lower()
        if "rfkill" in normalized or "radio" in normalized and "disabled" in normalized:
            return "E_WIFI_RFKILL", "无线电被禁用，请检查 WLAN 开关或 rfkill。", True
        if ("secret" in normalized or "password" in normalized or
                "authentication" in normalized):
            return "E_WIFI_AUTH_REQUIRED", "需要正确的无线网络密码或认证信息。", True
        if "not found" in normalized or "no network" in normalized:
            return "E_WIFI_NETWORK_GONE", "该无线热点已消失，请重新扫描。", True
        if "timeout" in normalized:
            return "E_WIFI_TIMEOUT", "连接超时，请靠近热点后重试。", True
        if "permission" in normalized or "not authorized" in normalized:
            return "E_WIFI_PERMISSION", "NetworkManager 拒绝了本次连接请求。", False
        return "E_WIFI_CONNECT_FAILED", "NetworkManager 未能完成无线连接。", True

    @staticmethod
    def _wifi_result(ok, state, reason_code, reason_text, retryable, *, ssid="",
                     bssid="", ifname="", network_id=""):
        return {
            "ok": bool(ok),
            "state": state,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "retryable": bool(retryable),
            "ssid": ssid,
            "bssid": bssid,
            "ifname": ifname,
            "network_id": network_id,
            "error": "" if ok else "NetworkManager：%s" % reason_text,
        }

    def _wifi_readback_connected(self, ifname):
        rc, output, _error = self._run_c(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        if rc != 0:
            return False
        for line in output.splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) >= 3 and fields[0] == ifname and fields[1] == "wifi":
                return fields[2].strip().lower() in {"connected", "connected (externally)"}
        return False

    def _wifi_connect_record(self, ssid, bssid, ifname, password=None, network_id="",
                             verify=False, use_c_locale=False):
        validation_error = self._wifi_connect_error(ssid, bssid, ifname)
        if validation_error:
            return self._wifi_result(
                False, "invalid", "E_WIFI_INVALID_TARGET", validation_error, False,
                ssid=ssid, bssid=bssid, ifname=ifname, network_id=network_id)
        command = [
            "nmcli", "--wait", "30", "device", "wifi", "connect", ssid,
            "bssid", bssid, "ifname", ifname,
        ]
        if password is not None:
            command.insert(1, "--ask")
        if use_c_locale:
            command = c_locale_command(command)
        if password is not None:
            rc, output, error = self._run_with_input(command, password + "\n", timeout=35)
        else:
            rc, output, error = self._run(command, timeout=35)
        if rc != 0:
            code, text, retryable = self._wifi_failure_reason(output, error)
            return self._wifi_result(
                False, "failed", code, text, retryable, ssid=ssid, bssid=bssid,
                ifname=ifname, network_id=network_id)
        if verify and not self._wifi_readback_connected(ifname):
            return self._wifi_result(
                False, "verification_pending", "E_WIFI_READBACK_PENDING",
                "连接请求已提交，但尚未确认指定无线接口已连接。", True,
                ssid=ssid, bssid=bssid, ifname=ifname, network_id=network_id)
        return self._wifi_result(
            True, "connected", "OK", "已连接。", False, ssid=ssid, bssid=bssid,
            ifname=ifname, network_id=network_id)

    def wifi_connect(self, ssid, bssid, ifname, password=None):
        """Compatibility path for callers that already hold a validated scan record."""
        return self._wifi_connect_record(ssid, bssid, ifname, password=password)

    def wifi_connect_network_id(self, network_id, ifname, password=None):
        if not isinstance(network_id, str) or not NETWORK_ID_PATTERN.fullmatch(network_id):
            return self._wifi_result(
                False, "invalid", "E_WIFI_NETWORK_ID_INVALID", "无线网络标识无效。", False,
                ifname=ifname, network_id=network_id or "")
        if not isinstance(ifname, str) or not IFNAME_PATTERN.fullmatch(ifname):
            return self._wifi_result(
                False, "invalid", "E_WIFI_INTERFACE_INVALID", "网络接口名称格式无效。", False,
                ifname=ifname or "", network_id=network_id)
        cache = self._read_wifi_scan_cache()
        generated_at = cache.get("generated_at") if cache else None
        if (not cache or cache.get("schema") != 1 or not isinstance(generated_at, int) or
                generated_at < 1 or time.time() - generated_at > WIFI_SCAN_CACHE_MAX_AGE):
            self.wifi_scan()
            cache = self._read_wifi_scan_cache()
            generated_at = cache.get("generated_at") if cache else None
            if (not cache or cache.get("schema") != 1 or not isinstance(generated_at, int) or
                    generated_at < 1 or time.time() - generated_at > WIFI_SCAN_CACHE_MAX_AGE):
                return self._wifi_result(
                    False, "stale", "E_WIFI_NETWORK_STALE", "扫描结果已过期，请重新扫描。", True,
                    ifname=ifname, network_id=network_id)
        record = next((item for item in cache.get("networks", [])
                       if isinstance(item, dict) and item.get("network_id") == network_id), None)
        if not record:
            return self._wifi_result(
                False, "stale", "E_WIFI_NETWORK_UNKNOWN", "未找到该无线网络，请重新扫描。", True,
                ifname=ifname, network_id=network_id)
        if record.get("ifname") != ifname:
            return self._wifi_result(
                False, "invalid", "E_WIFI_INTERFACE_MISMATCH", "无线接口已变化，请重新扫描。", True,
                ifname=ifname, network_id=network_id)
        if record.get("encoding") != "utf-8":
            return self._wifi_result(
                False, "invalid", "E_WIFI_SSID_ENCODING", "该无线网络名称编码无法安全使用。", False,
                ifname=ifname, network_id=network_id)
        try:
            ssid_bytes = base64.b64decode(record.get("ssid_bytes_b64", ""), validate=True)
            ssid = ssid_bytes.decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError):
            return self._wifi_result(
                False, "invalid", "E_WIFI_SSID_ENCODING", "该无线网络名称编码无法安全使用。", False,
                ifname=ifname, network_id=network_id)
        return self._wifi_connect_record(
            ssid, str(record.get("bssid") or ""), ifname, password=password,
            network_id=network_id, verify=True, use_c_locale=True)

    @staticmethod
    def _nmcli_device_rows(output):
        rows = []
        for line in (output or "").splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) < 4:
                continue
            rows.append({
                "ifname": fields[0], "type": fields[1], "state": fields[2],
                "connection": fields[3],
            })
        return rows

    @staticmethod
    def _nmcli_key_values(output):
        values = {}
        for line in (output or "").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
        return values

    def _net_sysfs_value(self, ifname, name):
        try:
            return (self.net_root / ifname / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _net_driver(self, ifname):
        path = self.net_root / ifname / "device" / "driver"
        try:
            resolved = os.path.realpath(path)
        except OSError:
            return ""
        return os.path.basename(resolved) if resolved and os.path.exists(resolved) else ""

    def _ethernet_internet(self, ifname, state, carrier, probe=True, dns_servers=()):
        base = {"interface": ifname, "state": "pending", "reason_code": "E_ETHERNET_PENDING",
                "reason_text": "正在等待有线网络完成配置。",
                "network_manager": {
                    "route_confirmed": False,
                    "dns_confirmed": bool(dns_servers),
                }}
        if carrier is False:
            base.update(state="offline", reason_code="E_ETHERNET_CARRIER_DOWN",
                        reason_text="未检测到网线连接。")
            return base
        if state.lower() not in {"connected", "connected (externally)"}:
            base.update(state="offline", reason_code="E_ETHERNET_NOT_CONNECTED",
                        reason_text="网卡尚未通过 NetworkManager 连接。")
            return base
        route_command = ["ip", "-4", "route", "get", "1.1.1.1", "oif", ifname]
        route_rc, route_output, _route_error = self._run_c(route_command)
        if route_rc != 0 or not re.search(r"\bdev\s+%s\b" % re.escape(ifname), route_output):
            base.update(reason_code="E_ETHERNET_ROUTE_UNCONFIRMED",
                        reason_text="未确认到该有线接口的 IPv4 默认路由。")
            return base
        base["network_manager"]["route_confirmed"] = True
        if not probe:
            base.update(reason_code="E_ETHERNET_ROUTE_CONFIRMED",
                        reason_text="已确认该有线接口路由；未执行互联网探测。")
            return base
        if not self._can_run("curl"):
            base.update(reason_code="E_ETHERNET_PROBE_UNAVAILABLE",
                        reason_text="已确认路由，等待联网探测工具可用。")
            return base
        attempted = []
        for url in ETHERNET_CONNECTIVITY_URLS:
            command = [
                "curl", "--interface", ifname, "--connect-timeout", "2", "--max-time", "5",
                "--silent", "--show-error", "--output", "/dev/null", "--write-out", "%{http_code}",
                url,
            ]
            rc, output, _error = self._run(command, timeout=7)
            attempted.append(url)
            if rc == 0 and re.fullmatch(r"2\d\d", (output or "").strip()):
                base.update(
                    state="online", reason_code="OK", reason_text="有线网络已连接互联网。",
                    probe_endpoint=url, probe_attempts=len(attempted),
                )
                return base
        base.update(
            state="offline", reason_code="E_ETHERNET_INTERNET_UNREACHABLE",
            reason_text="该有线接口无法访问多个联网探测端点，请检查路由器、DHCP 或 DNS。",
            probe_attempts=len(attempted),
        )
        return base

    def ethernet_status(self, probe_internet=True):
        command = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]
        rc, output, _error = self._run_c(command)
        if rc != 0:
            return {
                "ok": False, "state": "diagnostic_unavailable", "devices": [],
                "reason_code": "E_ETHERNET_NM_UNAVAILABLE",
                "reason_text": "NetworkManager 有线网络状态不可用。",
            }
        devices = []
        for row in self._nmcli_device_rows(output):
            if row["type"] != "ethernet" or not IFNAME_PATTERN.fullmatch(row["ifname"]):
                continue
            ifname = row["ifname"]
            detail_command = [
                "nmcli", "-t", "-f",
                "GENERAL.DEVICE,GENERAL.STATE,GENERAL.CONNECTION,WIRED-PROPERTIES.CARRIER,"
                "WIRED-PROPERTIES.SPEED,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,IP6.ADDRESS,IP6.GATEWAY",
                "device", "show", ifname,
            ]
            _detail_rc, detail_output, _detail_error = self._run_c(detail_command)
            details = self._nmcli_key_values(detail_output)
            carrier_text = self._net_sysfs_value(ifname, "carrier")
            if carrier_text in {"0", "1"}:
                carrier = carrier_text == "1"
            elif "WIRED-PROPERTIES.CARRIER" in details:
                carrier = details["WIRED-PROPERTIES.CARRIER"].lower() in {"on", "yes", "1"}
            else:
                carrier = None
            addresses = [value for key, value in details.items() if key.startswith("IP4.ADDRESS")]
            ipv6_addresses = [value for key, value in details.items() if key.startswith("IP6.ADDRESS")]
            dns = [value for key, value in details.items() if key.startswith("IP4.DNS")]
            devices.append({
                "ifname": ifname,
                "state": row["state"],
                "connection": row["connection"],
                "carrier": carrier,
                "speed_mbps": parse_integer(self._net_sysfs_value(ifname, "speed") or
                                            details.get("WIRED-PROPERTIES.SPEED", "")),
                "driver": self._net_driver(ifname),
                "ipv4": {"addresses": addresses, "gateway": details.get("IP4.GATEWAY", ""), "dns": dns},
                "ipv6": {"addresses": ipv6_addresses, "gateway": details.get("IP6.GATEWAY", "")},
                "internet": self._ethernet_internet(
                    ifname, row["state"], carrier, probe=probe_internet, dns_servers=dns),
            })
        return {
            "ok": True, "state": "ready" if devices else "no_ethernet", "devices": devices,
            "reason_code": "OK" if devices else "E_ETHERNET_NOT_FOUND",
            "reason_text": "已检测到有线网卡。" if devices else "未检测到有线网卡。",
        }

    def ethernet_repair(self, ifname):
        if not isinstance(ifname, str) or not IFNAME_PATTERN.fullmatch(ifname):
            return {"ok": False, "ifname": ifname or "", "state": "invalid",
                    "reason_code": "E_ETHERNET_INTERFACE_INVALID",
                    "reason_text": "有线网络接口名称格式无效。"}
        command = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]
        rc, output, _error = self._run_c(command)
        rows = self._nmcli_device_rows(output) if rc == 0 else []
        if not any(row["ifname"] == ifname and row["type"] == "ethernet" for row in rows):
            return {"ok": False, "ifname": ifname, "state": "not_found",
                    "reason_code": "E_ETHERNET_NOT_FOUND",
                    "reason_text": "未找到指定的有线网络接口。"}
        rc, _output, error = self._run_c(["nmcli", "device", "connect", ifname], timeout=20)
        if rc != 0:
            return {"ok": False, "ifname": ifname, "state": "failed",
                    "reason_code": "E_ETHERNET_RECONNECT_FAILED",
                    "reason_text": "无法重新连接指定有线接口。" if not error else
                    "无法重新连接指定有线接口，请检查网线、DHCP 或 802.1X 配置。"}
        return {"ok": True, "ifname": ifname, "state": "reconnect_requested",
                "reason_code": "OK", "reason_text": "已请求重新连接指定有线接口。"}

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

    def _portable_host_from_profile(self):
        """Return the root-owned portable classification, or None when absent."""
        try:
            lines = self.power_profile_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            key, separator, value = line.partition("=")
            if separator and key.strip() == "portable":
                normalized = value.strip().lower()
                if normalized == "true":
                    return True
                if normalized == "false":
                    return False
        return None

    def battery_status(self):
        portable_from_profile = self._portable_host_from_profile()
        if not self._can_run("upower"):
            return {
                "available": False,
                "portable": bool(portable_from_profile),
                "value": None,
                "text": "",
            }
        rc, output, _error = self._run(["upower", "-e"])
        if rc != 0:
            return {
                "available": False,
                "portable": bool(portable_from_profile),
                "value": None,
                "text": "",
            }
        devices = [line.strip() for line in output.splitlines() if line.strip()]
        display_devices = [line for line in devices if line.rsplit("/", 1)[-1] == "DisplayDevice"]
        native_batteries = [
            line for line in devices
            if re.search(r"/(?:battery_)?BAT[0-9A-Z_-]*$", line, re.I)
        ]
        candidates = display_devices + native_batteries
        if not candidates:
            return {
                "available": False,
                "portable": bool(portable_from_profile),
                "value": None,
                "text": "",
            }
        value = None
        for battery in candidates:
            rc, info, _error = self._run(["upower", "-i", battery])
            match = re.search(r"percentage:\s*(\d{1,3})%", info, re.I)
            if rc == 0 and match:
                value = int(match.group(1))
                break
        return {
            "available": value is not None,
            # The profile is authoritative when available.  The BAT-name
            # fallback keeps the indicator useful during early boot or live
            # sessions before the profile service has written its state.
            "portable": (
                portable_from_profile if portable_from_profile is not None
                else bool(native_batteries)),
            "value": value,
            "text": "%d%%" % value if value is not None else "--",
        }

    def status(self):
        return {
            "audio": self.audio_status(),
            "brightness": self.brightness_status(),
            "wifi": self.wifi_status(),
            "ethernet": self.ethernet_status(probe_internet=False),
            "bluetooth": self.bluetooth_status(),
            "battery": self.battery_status(),
        }


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-device-control")
    subparsers = parser.add_subparsers(dest="action", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    wifi_scan = subparsers.add_parser("wifi-scan")
    wifi_scan.add_argument("--json", action="store_true")
    wifi_connect = subparsers.add_parser("wifi-connect")
    wifi_connect.add_argument("--network-id")
    wifi_connect.add_argument("--ssid")
    wifi_connect.add_argument("--bssid")
    wifi_connect.add_argument("--ifname", required=True)
    wifi_connect.add_argument("--password-stdin", action="store_true")
    ethernet_status = subparsers.add_parser("ethernet-status")
    ethernet_status.add_argument("--json", action="store_true")
    ethernet_repair = subparsers.add_parser("ethernet-repair")
    ethernet_repair.add_argument("--ifname", required=True)
    ethernet_repair.add_argument("--json", action="store_true")
    bluetooth_status = subparsers.add_parser("bluetooth-status")
    bluetooth_status.add_argument("--json", action="store_true")
    audio_status = subparsers.add_parser("audio-status")
    audio_status.add_argument("--json", action="store_true")
    subparsers.add_parser("audio-repair-call")
    subparsers.add_parser("audio-repair-playback")
    subparsers.add_parser("audio-test-input")
    audio_output = subparsers.add_parser("audio-select-output")
    audio_output.add_argument("--id", dest="output_id", required=True)
    volume = subparsers.add_parser("set-volume")
    volume.add_argument("value", type=int)
    brightness = subparsers.add_parser("set-brightness")
    brightness.add_argument("value", type=int)
    reapply_brightness = subparsers.add_parser("reapply-brightness")
    reapply_brightness.add_argument("--wait-seconds", type=float, default=0)
    reapply_brightness.add_argument("--json", action="store_true")
    restore_brightness = subparsers.add_parser("restore-brightness")
    restore_brightness.add_argument("--wait-seconds", type=float, default=0)
    restore_brightness.add_argument("--json", action="store_true")
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
    elif args.action == "wifi-connect":
        password = None
        if args.password_stdin:
            source = stdin or sys.stdin
            password = source.readline(257).rstrip("\r\n")
            if not password:
                password = None
        if args.network_id and not args.ssid and not args.bssid:
            result = controller.wifi_connect_network_id(
                args.network_id, args.ifname, password=password)
        elif not args.network_id and args.ssid and args.bssid:
            result = controller.wifi_connect(args.ssid, args.bssid, args.ifname, password=password)
        else:
            result = DeviceController._wifi_result(
                False, "invalid", "E_WIFI_TARGET_AMBIGUOUS",
                "请提供扫描得到的网络标识，或同时提供 SSID 与 BSSID。", False,
                ifname=args.ifname, network_id=args.network_id or "")
    elif args.action == "ethernet-status":
        result = controller.ethernet_status()
    elif args.action == "ethernet-repair":
        result = controller.ethernet_repair(args.ifname)
    elif args.action == "bluetooth-status":
        result = controller.bluetooth_status()
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
        result = controller.set_volume(args.value)
    elif args.action in {"restore-brightness", "reapply-brightness"}:
        result = controller.reapply_brightness(wait_seconds=args.wait_seconds)
    else:
        result = controller.set_brightness(args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if args.action in {
        "status", "bluetooth-status", "audio-status", "ethernet-status",
    } or result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
