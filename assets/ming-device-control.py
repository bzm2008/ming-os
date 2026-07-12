#!/usr/bin/env python3
"""Ming OS user-session device status and control backend."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


RFKILL = "/usr/sbin/rfkill"
BACKLIGHT_ROOT = Path("/sys/class/backlight")
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
    return {
        "state": state,
        "present": state in {"ready", "rfkill_blocked"},
        "available": state == "ready",
        "title": title,
        "detail": detail,
        "devices": [name for name, _state in wifi_devices],
    }


class DeviceController:
    def __init__(self, runner=run_command, executable=shutil.which,
                 backlight_root=BACKLIGHT_ROOT, input_runner=run_command_with_input):
        self.runner = runner
        self.input_runner = input_runner
        self.executable = executable
        self.backlight_root = Path(backlight_root)

    def _run(self, command, timeout=8):
        return self.runner(command, timeout=timeout)

    def _run_with_input(self, command, input_text, timeout=8):
        return self.input_runner(command, input_text, timeout=timeout)

    def _run_c(self, command, timeout=8):
        return self._run(c_locale_command(command), timeout=timeout)

    def _can_run(self, command):
        return bool(self.executable(command))

    def _read_volume(self, backend):
        if backend == "pactl":
            rc, output, error = self._run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        else:
            rc, output, error = self._run(["amixer", "sget", "Master"])
        value = parse_percent(output)
        return rc == 0 and value is not None, value, error or output

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
    def _pactl_cards(output):
        cards = []
        current = None
        in_profiles = False
        for line in (output or "").splitlines():
            if line.startswith("Card #"):
                if current:
                    cards.append(current)
                current = {"name": "", "active_profile": "", "profiles": []}
                in_profiles = False
                continue
            if current is None:
                continue
            if line.startswith("Name:"):
                current["name"] = line.partition(":")[2].strip()
                continue
            if line.startswith("Profiles:"):
                in_profiles = True
                continue
            if line.startswith("Active Profile:"):
                current["active_profile"] = line.partition(":")[2].strip()
                in_profiles = False
                continue
            if line and not line[0].isspace():
                in_profiles = False
                continue
            if in_profiles:
                match = re.match(
                    r"\s*(.+?):\s+.*\(.*available:\s*(yes|no|unknown)\s*\)",
                    line, re.I)
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
    def _is_external_audio_name(name):
        return bool(re.search(r"(?:^|[._-])(usb|bluez|hdmi)(?:[._-]|$)", name or "", re.I))

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
        errors = []
        for backend in ("pactl", "amixer"):
            if not self._can_run(backend):
                continue
            ok, value, error = self._read_volume(backend)
            if ok:
                result = {
                    "available": True,
                    "backend": backend,
                    "value": value,
                    "error": "",
                }
                if backend != "pactl":
                    result.update({
                        "call_ready": False,
                        "default_sink": "",
                        "default_source": "",
                        "physical_input_present": False,
                        "input_muted": None,
                        "output_muted": None,
                        "duplex_profile_active": False,
                        "cards": [],
                    })
                    return result

                snapshot = self._pactl_call_snapshot()
                active_profiles = [card["active_profile"] for card in snapshot["cards"]]
                source_present = snapshot["defaults"]["source"] in snapshot["physical_sources"]
                duplex_active = any(
                    self._is_duplex_profile(profile) for profile in active_profiles)
                call_ready = bool(
                    snapshot["defaults"]["sink"] and source_present and duplex_active
                    and not snapshot["input_muted"] and not snapshot["output_muted"])
                result.update({
                    "call_ready": call_ready,
                    "default_sink": snapshot["defaults"]["sink"],
                    "default_source": snapshot["defaults"]["source"],
                    "physical_input_present": source_present,
                    "input_muted": snapshot["input_muted"],
                    "output_muted": snapshot["output_muted"],
                    "duplex_profile_active": duplex_active,
                    "cards": snapshot["cards"],
                })
                if snapshot["errors"]:
                    result["error"] = "；".join(snapshot["errors"])
                return result
            if error:
                errors.append(error)
        return {
            "available": False,
            "backend": "",
            "value": None,
            "error": "；".join(errors) or "未检测到音频输出设备",
            "call_ready": False,
            "default_sink": "",
            "default_source": "",
            "physical_input_present": False,
            "input_muted": None,
            "output_muted": None,
            "duplex_profile_active": False,
            "cards": [],
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
        rc, output, error = self._run([
            "timeout", "3", "parecord", "--raw", "--format=s16le", "--rate=16000",
            "--channels=1", "--device=@DEFAULT_SOURCE@",
        ], timeout=6)
        captured = len(output.encode("utf-8", errors="replace"))
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
            return {"ok": False, "error": str(exc), "value": None, "backend": ""}
        errors = []
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
            ok, effective, read_error = self._read_volume(backend)
            if ok:
                return {"ok": True, "error": "", "value": effective, "backend": backend}
            errors.append(read_error or "%s 读回失败" % backend)
        return {
            "ok": False,
            "error": "；".join(errors) or "未检测到音频输出设备",
            "value": None,
            "backend": "",
        }

    def _has_backlight(self):
        try:
            return self.backlight_root.is_dir() and any(self.backlight_root.iterdir())
        except OSError:
            return False

    def brightness_status(self):
        if not self._can_run("brightnessctl") or not self._has_backlight():
            return {"available": False, "value": None, "error": "unavailable"}
        rc, output, error = self._run(["brightnessctl", "-m"])
        value = parse_percent(output)
        if rc == 0 and value is not None:
            return {"available": True, "value": value, "error": ""}
        return {"available": False, "value": None, "error": error or "读取亮度失败"}

    def set_brightness(self, value):
        try:
            value = clamp_percent(value, minimum=1)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "value": None}
        if not self._can_run("brightnessctl") or not self._has_backlight():
            return {"ok": False, "error": "unavailable", "value": None}
        rc, output, error = self._run(["brightnessctl", "set", "%d%%" % value])
        if rc != 0:
            return {"ok": False, "error": error or output or "设置亮度失败", "value": None}
        status = self.brightness_status()
        return {
            "ok": status["available"],
            "error": status["error"],
            "value": status["value"],
        }

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
            networks.append({
                "ifname": fields[7],
                "bssid": fields[1],
                "ssid": fields[2],
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

    def wifi_connect(self, ssid, bssid, ifname, password=None):
        validation_error = self._wifi_connect_error(ssid, bssid, ifname)
        if validation_error:
            return {
                "ok": False,
                "ssid": ssid,
                "bssid": bssid,
                "ifname": ifname,
                "error": validation_error,
            }
        command = [
            "nmcli", "--wait", "30", "device", "wifi", "connect", ssid,
            "bssid", bssid, "ifname", ifname,
        ]
        if password is not None:
            command.insert(1, "--ask")
            rc, output, error = self._run_with_input(command, password + "\n", timeout=35)
        else:
            rc, output, error = self._run(command, timeout=35)
        return {
            "ok": rc == 0,
            "ssid": ssid,
            "bssid": bssid,
            "ifname": ifname,
            "error": "" if rc == 0 else (
                "连接失败；如需密码，请通过 NetworkManager 密钥管理界面提供。"),
        }

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

    def status(self):
        return {
            "audio": self.audio_status(),
            "brightness": self.brightness_status(),
            "wifi": self.wifi_status(),
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
    wifi_connect.add_argument("--ssid", required=True)
    wifi_connect.add_argument("--bssid", required=True)
    wifi_connect.add_argument("--ifname", required=True)
    wifi_connect.add_argument("--password-stdin", action="store_true")
    bluetooth_status = subparsers.add_parser("bluetooth-status")
    bluetooth_status.add_argument("--json", action="store_true")
    audio_status = subparsers.add_parser("audio-status")
    audio_status.add_argument("--json", action="store_true")
    subparsers.add_parser("audio-repair-call")
    subparsers.add_parser("audio-test-input")
    volume = subparsers.add_parser("set-volume")
    volume.add_argument("value", type=int)
    brightness = subparsers.add_parser("set-brightness")
    brightness.add_argument("value", type=int)
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
        result = controller.wifi_connect(args.ssid, args.bssid, args.ifname, password=password)
    elif args.action == "bluetooth-status":
        result = controller.bluetooth_status()
    elif args.action == "audio-status":
        result = controller.audio_status()
    elif args.action == "audio-repair-call":
        result = controller.audio_repair_call()
    elif args.action == "audio-test-input":
        result = controller.audio_test_input()
    elif args.action == "set-volume":
        result = controller.set_volume(args.value)
    else:
        result = controller.set_brightness(args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if args.action in {"status", "bluetooth-status", "audio-status"} or result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
