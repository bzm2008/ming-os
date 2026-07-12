#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Ming OS 统一设置中心 (Settings Hub) — GTK4 / libadwaita
# 面向数字难民的单窗口全图形设置。零命令行：所有操作封装为按钮/开关/滑块。
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk, Pango
import subprocess
import os
import json
import getpass
import importlib.util
import threading
import shutil
import re
import sys

USER = getpass.getuser()
HOME = os.path.expanduser("~")
SETTINGS_BACKEND = "/usr/local/lib/ming-os/ming-settings-backend"
TIME_SYNC_HELPER = "/usr/local/sbin/ming-time-sync"
DISPLAY_CONTROL_HELPER = "/usr/local/bin/ming-display-control"
SCALE_PREFERENCE_PATH = os.path.join(HOME, ".config", "ming-os", "scale-preference.json")
DEVICE_CONTROL_PATHS = [
    "/usr/local/lib/ming-os/ming-device-control.py",
    "/usr/local/bin/ming-device-control",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ming-device-control.py"),
]
_DEVICE_CONTROL_MODULE = None
_DEVICE_CONTROL_LOADED = False
_DEVICE_CONTROL_LOADING = False
_DEVICE_CONTROL_CONDITION = threading.Condition()


def load_device_control():
    """Load the shared controller once; concurrent callers wait for completion."""
    global _DEVICE_CONTROL_MODULE, _DEVICE_CONTROL_LOADED, _DEVICE_CONTROL_LOADING
    with _DEVICE_CONTROL_CONDITION:
        if _DEVICE_CONTROL_LOADED:
            return _DEVICE_CONTROL_MODULE
        if _DEVICE_CONTROL_LOADING:
            while _DEVICE_CONTROL_LOADING:
                _DEVICE_CONTROL_CONDITION.wait()
            return _DEVICE_CONTROL_MODULE
        _DEVICE_CONTROL_LOADING = True

    module = None
    try:
        for path in DEVICE_CONTROL_PATHS:
            if not os.path.exists(path):
                continue
            try:
                spec = importlib.util.spec_from_file_location("ming_device_control", path)
                candidate = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(candidate)
                module = candidate
                break
            except Exception:
                continue
    finally:
        with _DEVICE_CONTROL_CONDITION:
            _DEVICE_CONTROL_MODULE = module
            _DEVICE_CONTROL_LOADED = True
            _DEVICE_CONTROL_LOADING = False
            _DEVICE_CONTROL_CONDITION.notify_all()
    return module


def responsive_window_size(preferred_width=860, preferred_height=620):
    """Fit the initial window inside the first monitor with usable margins."""
    display = Gdk.Display.get_default()
    if not display:
        return preferred_width, preferred_height
    monitors = display.get_monitors()
    monitor = monitors.get_item(0) if monitors and monitors.get_n_items() else None
    if not monitor:
        return preferred_width, preferred_height
    geometry = monitor.get_geometry()
    return (
        max(520, min(preferred_width, geometry.width - 64)),
        max(420, min(preferred_height, geometry.height - 80)),
    )


def run(cmd, timeout=20):
    """运行命令，返回 (rc, stdout, stderr)。不抛异常。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def run_async(cmd, on_line=None, on_done=None):
    """后台运行命令，按行回调（GLib 主线程），结束回调 rc。"""
    def worker():
        rc = 1
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
            for line in p.stdout:
                if on_line:
                    GLib.idle_add(on_line, line.rstrip())
            p.wait()
            rc = p.returncode
        except Exception as e:
            if on_line:
                GLib.idle_add(on_line, "错误: %s" % e)
        if on_done:
            GLib.idle_add(on_done, rc)
    threading.Thread(target=worker, daemon=True).start()


def run_capture_async(cmd, timeout=20, on_done=None):
    """Run a bounded command off the GTK thread and return all output on it."""
    def worker():
        result = run(cmd, timeout=timeout)
        if on_done:
            GLib.idle_add(on_done, *result)
    threading.Thread(target=worker, daemon=True).start()


def run_capture_stdin_async(cmd, input_text, timeout=20, on_done=None):
    """Run a command with sensitive input on stdin, never in argv or output."""
    def worker():
        try:
            process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            output, error = process.communicate(input_text, timeout=timeout)
            result = (process.returncode, output.strip(), error.strip())
        except subprocess.TimeoutExpired:
            process.kill()
            output, error = process.communicate()
            result = (1, output.strip(), error.strip() or "操作超时。")
        except Exception as exc:
            result = (1, "", str(exc))
        if on_done:
            GLib.idle_add(on_done, *result)
    threading.Thread(target=worker, daemon=True).start()


def run_task_async(task, on_done=None):
    """Run a Python probe off the GTK thread and marshal its result to GTK."""
    def worker():
        try:
            value, error = task(), None
        except Exception as exc:
            value, error = None, str(exc)
        if on_done:
            GLib.idle_add(on_done, value, error)
    threading.Thread(target=worker, daemon=True).start()


def read_text_file(path, fallback="未知"):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            value = handle.read().strip()
            return value or fallback
    except OSError:
        return fallback


def compact_output(text, max_lines=8):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " · ".join(lines[:max_lines]) if lines else "未检测到"


def pci_driver_summary(device_pattern):
    """Summarize lspci blocks, including each Kernel driver in use."""
    command = (
        "LC_ALL=C lspci -nnk | "
        "grep -A3 -Ei '%s' | sed '/^--$/d'" % device_pattern
    )
    _rc, output, _error = run(["bash", "-lc", command], timeout=6)
    return compact_output(output, max_lines=10)


def wifi_diagnostic_snapshot():
    """Use shared lspci/lsusb USB, rfkill and 缺少固件 classification."""
    module = load_device_control()
    if not module:
        return {
            "state": "no_hardware",
            "present": False,
            "available": False,
            "title": "未检测到无线网卡",
            "detail": "无线诊断组件不可用；请检查硬件无线开关或 BIOS。",
        }
    snapshot = {"wifi": module.DeviceController().wifi_status()}
    wifi = dict(snapshot["wifi"])
    wifi.setdefault("state", "no_hardware")
    wifi.setdefault("present", False)
    wifi.setdefault("available", False)
    return wifi


def parse_wifi_scan_output(output):
    seen = set()
    rows = []
    for line in (output or "").splitlines():
        parts = line.rsplit(":", 1)
        ssid = parts[0].replace("\\:", ":").strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        signal = parts[1].strip() if len(parts) > 1 else "?"
        rows.append((ssid, signal))
    return rows


def wifi_scan_snapshot():
    """Return DeviceController's lossless, structured Wi-Fi scan results."""
    module = load_device_control()
    if not module:
        return {
            "ok": False,
            "state": "diagnostic_unavailable",
            "error": "无线诊断组件不可用，无法扫描网络。",
            "networks": [],
        }
    controller = module.DeviceController()
    return controller.wifi_scan()


def device_control_cli_command(*args):
    installed = "/usr/local/bin/ming-device-control"
    if os.path.isfile(installed):
        return [installed] + list(args)
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ming-device-control.py")
    return [sys.executable, local] + list(args)


def wifi_connect_command(ssid, bssid, ifname, with_secret=False):
    command = device_control_cli_command(
        "wifi-connect", "--ssid", ssid, "--bssid", bssid, "--ifname", ifname)
    if with_secret:
        command.append("--password-stdin")
    return command


def bluetooth_status_snapshot():
    module = load_device_control()
    if not module:
        return {
            "state": "diagnostic_unavailable",
            "title": "蓝牙诊断不可用",
            "detail": "蓝牙诊断组件不可用，无法确认硬件状态。",
            "hardware": [], "modules": [],
            "service": {"active": False, "enabled": False},
            "rfkill": {"soft_blocked": False, "hard_blocked": False},
            "controller": {"present": False, "powered": False, "id": "", "model": ""},
        }
    controller = module.DeviceController()
    return controller.bluetooth_status()


def bluetooth_repair_allowed(status):
    """The privileged repair may only clear a soft radio block or start BlueZ."""
    state = (status or {}).get("state")
    if state == "service_stopped":
        return True
    rfkill = (status or {}).get("rfkill") or {}
    return bool(
        state == "rfkill_blocked"
        and rfkill.get("soft_blocked")
        and not rfkill.get("hard_blocked"))


def audio_status_snapshot():
    module = load_device_control()
    if not module:
        return {"available": False, "call_ready": False,
                "error": "声音诊断组件不可用。"}
    controller = module.DeviceController()
    return controller.audio_status()


def audio_repair_call_snapshot():
    module = load_device_control()
    if not module:
        return {"ok": False, "error": "声音修复组件不可用。", "status": audio_status_snapshot()}
    controller = module.DeviceController()
    return controller.audio_repair_call()


def audio_test_input_snapshot():
    module = load_device_control()
    if not module:
        return {"ok": False, "seconds": 3,
                "error": "麦克风测试组件不可用。", "status": audio_status_snapshot()}
    controller = module.DeviceController()
    return controller.audio_test_input()


def hardware_status_snapshot():
    """Read only the structured card contract, never scrape raw PCI logs in the UI."""
    rc, output, error = run(
        ["ming-hardware-status", "status", "--json"], timeout=30)
    broadcom = read_broadcom_status_snapshot()
    if rc != 0:
        return {
            "ok": False, "error": error or output or "硬件状态工具未返回结果。",
            "devices": {}, "broadcom": broadcom,
        }
    try:
        result = json.loads(output)
    except (TypeError, ValueError):
        return {
            "ok": False, "error": "硬件状态工具返回了无效数据。",
            "devices": {}, "broadcom": broadcom,
        }
    devices = result.get("devices") if isinstance(result, dict) else None
    if not isinstance(devices, dict):
        return {
            "ok": False, "error": "硬件状态中缺少设备卡片。",
            "devices": {}, "broadcom": broadcom,
        }
    return {"ok": True, "error": "", "devices": devices, "broadcom": broadcom}


def hardware_export_snapshot():
    rc, output, error = run(["ming-hardware-status", "export"], timeout=35)
    if rc != 0 or not output:
        return {"ok": False, "error": error or output or "无法导出硬件诊断。", "content": ""}
    return {"ok": True, "error": "", "content": output}


def time_sync_snapshot():
    """Return the time helper's structured state without changing timezone."""
    rc, output, error = run([TIME_SYNC_HELPER, "status", "--json"], timeout=12)
    if rc != 0:
        return {"state": "error", "error": error or output or "校时服务没有返回状态。"}
    try:
        status = json.loads(output)
    except (TypeError, ValueError):
        return {"state": "error", "error": "校时服务返回了无效状态。"}
    if not isinstance(status, dict):
        return {"state": "error", "error": "校时服务返回了无效状态。"}
    state = status.get("state")
    if state not in {"synchronized", "waiting", "error"}:
        status["state"] = "error"
        status.setdefault("error", "校时服务返回了未知状态。")
    return status


def display_status_snapshot():
    """Read the display helper's structured xrandr snapshot without GTK work."""
    rc, output, error = run([DISPLAY_CONTROL_HELPER, "status", "--json"], timeout=10)
    if rc != 0:
        return {"ok": False, "error": error or output or "无法读取当前显示器状态。", "outputs": []}
    try:
        status = json.loads(output)
    except (TypeError, ValueError):
        return {"ok": False, "error": "显示服务返回了无效数据。", "outputs": []}
    if not isinstance(status, dict) or not isinstance(status.get("outputs"), list):
        return {"ok": False, "error": "显示服务没有返回显示器列表。", "outputs": []}
    return status


def display_mode_label(mode, rate):
    """Keep resolution wording independent from interface-scale percentages."""
    match = re.fullmatch(r"([1-9][0-9]{1,4})x([1-9][0-9]{1,4})", str(mode))
    try:
        hz = ("%.3f" % float(rate)).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        hz = str(rate)
    if not match:
        return "%s · %s Hz" % (mode, hz)
    return "%s × %s · %s Hz" % (match.group(1), match.group(2), hz)


def load_scale_preference():
    try:
        with open(SCALE_PREFERENCE_PATH, encoding="utf-8") as handle:
            value = json.load(handle).get("percent")
        return value if value in {100, 125, 150, 175, 200} else None
    except (OSError, ValueError, AttributeError):
        return None


def save_scale_preference(percent):
    directory = os.path.dirname(SCALE_PREFERENCE_PATH)
    os.makedirs(directory, exist_ok=True)
    temporary = SCALE_PREFERENCE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"percent": percent}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, SCALE_PREFERENCE_PATH)


class GenerationState:
    """Reject results from superseded probes or a closed settings window."""
    def __init__(self):
        self.generation = 0
        self.active = True

    def begin(self):
        self.generation += 1
        return self.generation

    def accept(self, generation):
        return self.active and generation == self.generation

    def invalidate(self):
        self.active = False
        self.generation += 1


def read_broadcom_status_snapshot():
    manager = "/usr/local/sbin/ming-broadcom-driver"
    rc, output, error = run([manager, "status", "--json"], timeout=8)
    if rc != 0:
        return {"action": "error", "model": "", "error": error or output}
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return {"action": "error", "model": "", "error": "驱动检测返回了无效数据"}


def hardware_probe_snapshot():
    _cpu_rc, cpu, _cpu_error = run(
        ["bash", "-lc", "awk -F: '/model name/{gsub(/^[ \\t]+/,\"\",$2); print $2; exit}' /proc/cpuinfo"],
        timeout=5)
    _flags_rc, flags, _flags_error = run(
        ["bash", "-lc", "lscpu | awk -F: '/Flags|标志/ {print $2; exit}'"], timeout=8)
    avx2 = "未检测到 AVX2，Ming OS r4 会按老 CPU 兼容路径运行。"
    if " avx2 " in (" " + flags + " "):
        avx2 = "检测到 AVX2；系统仍按 Debian amd64 基线运行。"
    vendor = read_text_file("/sys/class/dmi/id/sys_vendor")
    product = read_text_file("/sys/class/dmi/id/product_name")
    product_version = read_text_file("/sys/class/dmi/id/product_version", "")
    _kernel_rc, kernel, _kernel_error = run(["uname", "-r"], timeout=4)
    platform = " ".join(part for part in [vendor, product, product_version] if part)
    return {
        "cpu": "%s · %s" % (cpu or "型号未知", avx2),
        "platform": "%s · Linux %s" % (platform or "设备平台未知", kernel or "未知"),
        "audio": pci_driver_summary("Audio device|Multimedia audio controller"),
        "network": pci_driver_summary(
            "Network controller|Wireless controller|Ethernet controller|802\\.11"),
        "graphics": pci_driver_summary(
            "VGA compatible controller|3D controller|Display controller"),
        "broadcom": read_broadcom_status_snapshot(),
    }


PAGE_ALIASES = {
    "account": "账户",
    "network": "网络与蓝牙",
    "storage": "存储",
    "update": "系统更新",
    "display": "显示与无障碍",
    "advanced": "高级设置",
    "hardware": "硬件与诊断",
    "restore": "系统还原",
}


class MingSettings(Adw.ApplicationWindow):
    def __init__(self, app, initial_page=None):
        super().__init__(application=app, title="Ming 设置")
        window_width, window_height = responsive_window_size()
        self.set_default_size(window_width, window_height)
        self.add_css_class("ming-settings-window")
        self.backend_timers = {}
        self.page_built = set()
        self.page_builders = {}
        self.hardware_probe_state = GenerationState()
        self.wifi_probe_state = GenerationState()
        self.wifi_connect_state = GenerationState()
        self.bluetooth_probe_state = GenerationState()
        self.audio_probe_state = GenerationState()
        self.time_sync_probe_state = GenerationState()
        self.connect("close-request", self.on_close_request)
        self.install_css()

        # Adw.NavigationSplitView：左导航 + 右内容（Android 风格单窗口）
        self.split = Adw.NavigationSplitView()
        self.split.set_collapsed(window_width < 760)
        self.set_content(self.split)

        # 左侧：分类列表
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.add_css_class("ming-settings-sidebar")
        sb_header = Adw.HeaderBar()
        sb_title = Adw.WindowTitle(title="Ming 设置", subtitle="小而美的系统入口")
        sb_header.set_title_widget(sb_title)
        sidebar_box.append(sb_header)

        self.nav_list = Gtk.ListBox()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.connect("row-selected", self.on_nav_selected)
        self.nav_list.set_size_request(212, -1)
        sidebar_box.append(self.nav_list)

        sidebar_page = Adw.NavigationPage(title="Ming 设置", child=sidebar_box)
        self.split.set_sidebar(sidebar_page)

        # 右侧内容容器
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_vexpand(True)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.add_css_class("ming-settings-content")
        c_header = Adw.HeaderBar()
        self.content_title = Adw.WindowTitle(title="", subtitle="")
        c_header.set_title_widget(self.content_title)
        content_box.append(c_header)
        content_box.append(self.content_stack)
        content_page = Adw.NavigationPage(title="设置", child=content_box)
        self.split.set_content(content_page)

        # 注册分类页（图标, 标题, 构建函数）
        self.pages = [
            ("avatar-default-symbolic", "账户", self.build_account),
            ("network-wireless-symbolic", "网络与蓝牙", self.build_network),
            ("drive-harddisk-symbolic", "存储", self.build_storage),
            ("software-update-available-symbolic", "系统更新", self.build_update),
            ("preferences-desktop-display-symbolic", "显示与无障碍", self.build_display),
            ("preferences-other-symbolic", "高级设置", self.build_advanced),
            ("applications-system-symbolic", "硬件与诊断", self.build_hardware),
            ("view-refresh-symbolic", "系统还原", self.build_restore),
        ]
        for icon, title, builder in self.pages:
            row = Gtk.ListBoxRow()
            row.add_css_class("ming-nav-row")
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hb.set_margin_top(9); hb.set_margin_bottom(9)
            hb.set_margin_start(12); hb.set_margin_end(12)
            img = Gtk.Image.new_from_icon_name(icon)
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.add_css_class("ming-nav-label")
            hb.append(img); hb.append(lbl)
            row.set_child(hb)
            row.page_title = title
            self.nav_list.append(row)
            self.page_builders[title] = builder
            placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.content_stack.add_named(placeholder, title)

        initial_title = PAGE_ALIASES.get(initial_page, initial_page)
        initial_index = next(
            (index for index, (_icon, title, _builder) in enumerate(self.pages) if title == initial_title),
            0,
        )
        self.nav_list.select_row(self.nav_list.get_row_at_index(initial_index))

    def install_css(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        css = b"""
        window.ming-settings-window {
            background: #F4F7F3;
            color: #1B2320;
        }

        .ming-settings-sidebar {
            background: alpha(#EEF3EF, 0.98);
            border-right: 1px solid alpha(#2F8A7D, 0.08);
        }

        .ming-settings-content {
            background: linear-gradient(to bottom, #F7FAF6, #F1F5F0);
        }

        .ming-settings-window headerbar {
            background: alpha(#FFFFFF, 0.94);
            border-bottom: 1px solid alpha(#2F8A7D, 0.06);
            min-height: 44px;
        }

        .ming-settings-window list.navigation-sidebar {
            background: transparent;
            margin: 10px;
        }

        .ming-settings-window row.ming-nav-row {
            border-radius: 12px;
            margin: 3px 0;
            min-height: 46px;
        }

        .ming-settings-window row.ming-nav-row:hover {
            background: alpha(#2F8A7D, 0.06);
        }

        .ming-settings-window row.ming-nav-row:selected {
            background: alpha(#2F8A7D, 0.10);
            color: #1B2320;
        }

        .ming-settings-window row.ming-time-sync-ok {
            background: #DDF5EC;
            color: #123B35;
        }

        .ming-settings-window row.ming-time-sync-waiting {
            background: #FFF2CC;
            color: #4A3200;
        }

        .ming-settings-window row.ming-time-sync-error {
            background: #FFE1DF;
            color: #651E1E;
        }

        .ming-settings-window .ming-nav-label {
            font-weight: 500;
        }

        .ming-settings-window preferencespage,
        .ming-settings-window preferencesgroup > box,
        .ming-settings-window clamp {
            background: transparent;
        }

        .ming-settings-window preferencesgroup {
            margin-bottom: 6px;
        }

        .ming-settings-window preferencesgroup > box {
            background: alpha(#FFFFFF, 0.94);
            border-radius: 14px;
            border: 1px solid alpha(#2F8A7D, 0.06);
            padding: 4px;
        }

        .ming-settings-window button {
            border-radius: 10px;
            min-height: 38px;
            padding-left: 15px;
            padding-right: 15px;
        }

        .ming-settings-window button.suggested-action {
            background: #2F8A7D;
            color: #FFFFFF;
        }

        .ming-settings-window button.suggested-action:hover {
            background: #27776C;
        }

        .ming-settings-window entry,
        .ming-settings-window passwordentry {
            border-radius: 10px;
        }

        .ming-settings-window progressbar trough {
            min-height: 8px;
            border-radius: 999px;
            background: alpha(#2F8A7D, 0.08);
        }

        .ming-settings-window progressbar progress {
            border-radius: 999px;
            background: #2F8A7D;
        }

        .ming-settings-window label.dim-label {
            color: alpha(#21302A, 0.66);
        }

        .ming-feedback-dialog {
            background: #10201D;
            color: #FFFFFF;
            border: 2px solid #FFFFFF;
        }

        .ming-feedback-dialog label,
        .ming-feedback-dialog .title,
        .ming-feedback-dialog .heading {
            color: #FFFFFF;
            opacity: 1;
        }

        .ming-feedback-dialog.feedback-info {
            background: #123B35;
            border-color: #7DE2D1;
        }

        .ming-feedback-dialog.feedback-warning {
            background: #5A2F00;
            border-color: #FFD166;
        }

        .ming-feedback-dialog.feedback-error {
            background: #651E1E;
            border-color: #FFB4AB;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def on_nav_selected(self, listbox, row):
        if not row:
            return
        title = row.page_title
        if title not in self.page_built:
            placeholder = self.content_stack.get_child_by_name(title)
            page_widget = self.page_builders[title]()
            if placeholder:
                self.content_stack.remove(placeholder)
            self.content_stack.add_named(page_widget, title)
            self.page_built.add(title)
        self.content_stack.set_visible_child_name(title)
        self.content_title.set_title(title)

    def on_close_request(self, _window):
        self.hardware_probe_state.invalidate()
        self.wifi_probe_state.invalidate()
        self.wifi_connect_state.invalidate()
        self.bluetooth_probe_state.invalidate()
        self.audio_probe_state.invalidate()
        self.time_sync_probe_state.invalidate()
        return False

    # ---- 通用 UI 助手 ----
    def page_scroller(self):
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(22); box.set_margin_bottom(28)
        box.set_margin_start(18); box.set_margin_end(18)
        clamp.set_child(box)
        sc.set_child(clamp)
        return sc, box

    def toast(self, text, severity=None):
        """Show actionable feedback with opaque, WCAG-AA contrast."""
        text = text or "操作未返回可读结果。"
        if severity not in {"info", "warning", "error"}:
            severity = "error" if any(word in text for word in ("失败", "错误", "不可用", "未成功", "拒绝")) else "info"
        headings = {
            "info": "操作结果",
            "warning": "需要注意",
            "error": "操作失败",
        }
        dlg = Adw.MessageDialog(transient_for=self, heading=headings[severity], body=text)
        dlg.add_css_class("ming-feedback-dialog")
        dlg.add_css_class("feedback-%s" % severity)
        dlg.add_response("ok", "好的")
        dlg.present()

    def backend_get(self, key, default=None):
        rc, output, _error = run([SETTINGS_BACKEND, "get", key], timeout=6)
        if rc != 0:
            return default
        try:
            result = json.loads(output)
        except (TypeError, ValueError):
            return default
        return result.get("value", default) if result.get("ok") else default

    def backend_get_async(self, key, default, callback):
        def done(rc, output, _error):
            value = default
            if rc == 0:
                try:
                    result = json.loads(output)
                    if result.get("ok"):
                        value = result.get("value", default)
                except (TypeError, ValueError):
                    pass
            callback(value)

        run_capture_async([SETTINGS_BACKEND, "get", key], timeout=6, on_done=done)

    def backend_set_async(self, key, value, success_text=None, on_complete=None):
        command = [SETTINGS_BACKEND, "set", key, json.dumps(value, ensure_ascii=False)]

        def done(rc, output, error):
            try:
                result = json.loads(output) if output else {}
            except (TypeError, ValueError):
                result = {}
            success = rc == 0 and result.get("ok", False)
            if success:
                if success_text:
                    self.toast(success_text)
            else:
                self.toast(result.get("error") or error or "设置后端未返回有效结果")
            if on_complete:
                on_complete(success, result)

        run_capture_async(command, timeout=12, on_done=done)

    def schedule_backend_value(self, key, value, delay=220):
        previous = self.backend_timers.pop(key, None)
        if previous:
            GLib.source_remove(previous)

        def apply_value():
            self.backend_timers.pop(key, None)
            self.backend_set_async(key, value)
            return False

        self.backend_timers[key] = GLib.timeout_add(delay, apply_value)

    def backend_switch_row(self, title, subtitle, key, default=False):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(bool(default))
        row.set_sensitive(False)

        def loaded(value):
            row.set_active(bool(value))
            row.connect(
                "notify::active",
                lambda control, _prop: self.backend_set_async(key, control.get_active()))
            row.set_sensitive(True)

        self.backend_get_async(key, default, loaded)
        return row

    def backend_combo_row(self, title, subtitle, key, labels, values, default_index=0):
        model = Gtk.StringList.new(labels)
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model)
        row.set_selected(default_index)
        row.set_sensitive(False)
        row.backend_reconciling = False
        row.last_confirmed_value = values[default_index]

        def loaded(current):
            selected = values.index(current) if current in values else default_index
            row.set_selected(selected)
            row.last_confirmed_value = values[selected]

            def changed(control, _prop):
                if control.backend_reconciling:
                    return
                requested = values[min(control.get_selected(), len(values) - 1)]
                control.set_sensitive(False)

                def completed(success, result):
                    if success:
                        control.last_confirmed_value = result.get("value", requested)
                        control.set_sensitive(True)
                        return
                    if key != "compositor_profile":
                        control.set_sensitive(True)
                        return

                    def restore_selection(actual):
                        actual = actual if actual in values else control.last_confirmed_value
                        control.backend_reconciling = True
                        control.set_selected(values.index(actual))
                        control.backend_reconciling = False
                        control.last_confirmed_value = actual
                        control.set_sensitive(True)

                    self.backend_get_async(
                        "compositor_profile", control.last_confirmed_value,
                        restore_selection)

                self.backend_set_async(key, requested, on_complete=completed)

            row.connect(
                "notify::selected", changed)
            row.set_sensitive(True)

        self.backend_get_async(key, values[default_index], loaded)
        return row

    def backend_scale_row(self, title, subtitle, key, lower, upper, step, default):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        adjustment = Gtk.Adjustment(
            value=float(default), lower=lower, upper=upper,
            step_increment=step, page_increment=max(step, (upper - lower) / 5))
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_size_request(240, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_sensitive(False)

        def loaded(value):
            adjustment.set_value(float(value))
            scale.connect(
                "value-changed",
                lambda control: self.schedule_backend_value(
                    key, int(round(control.get_value()))))
            scale.set_sensitive(True)

        self.backend_get_async(key, default, loaded)
        row.add_suffix(scale)
        return row

    def backend_collection_row(self, title, subtitle, list_args, set_args):
        row = Adw.ComboRow(
            title=title, subtitle=subtitle,
            model=Gtk.StringList.new(["正在读取..."]))
        row.set_sensitive(False)

        def loaded(rc, output, error):
            try:
                result = json.loads(output) if rc == 0 else {}
            except (TypeError, ValueError):
                result = {}
            items = result.get("items", []) if result.get("ok") else []
            if not items:
                row.set_model(Gtk.StringList.new(["不可用"]))
                row.set_subtitle(error or result.get("error") or subtitle)
                return
            row.choice_ids = [item["id"] for item in items]
            row.set_model(Gtk.StringList.new([item.get("label", item["id"]) for item in items]))
            selected = next(
                (index for index, item in enumerate(items) if item.get("selected")), 0)
            row.set_selected(selected)

            def changed(control, _prop):
                index = min(control.get_selected(), len(control.choice_ids) - 1)
                self.backend_command_async(set_args + [control.choice_ids[index]])

            row.connect("notify::selected", changed)
            row.set_sensitive(True)

        run_capture_async([SETTINGS_BACKEND] + list_args, timeout=8, on_done=loaded)
        return row

    def backend_command_async(self, args, success_text=None):
        def done(rc, output, error):
            try:
                result = json.loads(output) if output else {}
            except (TypeError, ValueError):
                result = {}
            if rc == 0 and result.get("ok", True):
                if success_text:
                    self.toast(success_text)
                return
            self.toast(result.get("error") or error or "设置未生效")

        run_capture_async([SETTINGS_BACKEND] + list(args), timeout=10, on_done=done)

    def load_autostart_group(self, group):
        loading = Adw.ActionRow(title="正在读取登录启动项...")
        group.add(loading)

        def loaded(rc, output, error):
            group.remove(loading)
            try:
                result = json.loads(output) if rc == 0 else {}
            except (TypeError, ValueError):
                result = {}
            items = result.get("items", []) if result.get("ok") else []
            if not items:
                group.add(Adw.ActionRow(
                    title="没有可管理的启动项",
                    subtitle=error or result.get("error") or "系统未返回启动项"))
                return
            for item in items:
                row = Adw.SwitchRow(
                    title=item["label"], subtitle=item["id"])
                row.set_active(bool(item.get("enabled")))
                row.set_sensitive(not item.get("protected", False))
                row.connect(
                    "notify::active",
                    lambda control, _prop, name=item["id"]: self.backend_command_async(
                        ["autostart", name, "true" if control.get_active() else "false"]))
                group.add(row)

        run_capture_async(
            [SETTINGS_BACKEND, "autostart", "list"], timeout=8, on_done=loaded)

    # ---- 1. 账户管理：重设密码 ----
    def build_account(self):
        sc, box = self.page_scroller()
        grp = Adw.PreferencesGroup(title="当前账户", description="用户名：%s" % USER)
        box.append(grp)

        pw_grp = Adw.PreferencesGroup(title="重设登录密码",
                                      description="留空可保持免密自动登录。设置后开机仍自动进入桌面，密码仅用于授权操作。")
        self.pw1 = Adw.PasswordEntryRow(title="新密码")
        self.pw2 = Adw.PasswordEntryRow(title="确认新密码")
        pw_grp.add(self.pw1)
        pw_grp.add(self.pw2)
        btn = Gtk.Button(label="保存密码")
        btn.add_css_class("suggested-action")
        btn.set_margin_top(12)
        btn.connect("clicked", self.on_set_password)
        pw_grp.add(btn)
        box.append(pw_grp)
        return sc

    def on_set_password(self, _btn):
        p1 = self.pw1.get_text()
        p2 = self.pw2.get_text()
        if p1 != p2:
            self.toast("两次密码不一致。")
            return
        if not p1:
            # 清空密码 = 保持免密
            run(["pkexec", "passwd", "-d", USER])
            self.toast("已设为免密登录。")
            return
        # 通过 pkexec chpasswd 设置
        try:
            proc = subprocess.run(
                ["pkexec", "bash", "-c", "chpasswd"],
                input="%s:%s\n" % (USER, p1), text=True,
                capture_output=True, timeout=20)
            if proc.returncode == 0:
                self.toast("密码已更新。开机仍自动进入桌面。")
                self.pw1.set_text(""); self.pw2.set_text("")
            else:
                self.toast("设置失败：%s" % (proc.stderr or "权限被拒绝"))
        except Exception as e:
            self.toast("设置失败：%s" % e)

    # ---- 2. 网络与蓝牙 ----
    def build_network(self):
        sc, box = self.page_scroller()
        self.network_page = sc

        time_grp = Adw.PreferencesGroup(
            title="时间同步", description="联网后会自动校时；不会改动您选择的时区。")
        self.time_sync_row = Adw.ActionRow(
            title="正在读取校时状态", subtitle="正在确认网络与系统校时服务...")
        self.time_sync_retry_btn = Gtk.Button(label="立即重试")
        self.time_sync_retry_btn.set_valign(Gtk.Align.CENTER)
        self.time_sync_retry_btn.connect("clicked", self.on_time_sync_retry)
        self.time_sync_row.add_suffix(self.time_sync_retry_btn)
        time_grp.add(self.time_sync_row)
        box.append(time_grp)

        # WLAN 开关
        self.wifi_diagnostic = {
            "state": "checking", "present": False, "available": False,
            "title": "正在检测无线网络", "detail": "正在读取硬件与驱动状态..."}
        wifi_grp = Adw.PreferencesGroup(
            title="无线网络 (WLAN)",
            description="没有可用网络时会同时显示硬件、驱动、rfkill 与固件状态。")
        self.wifi_diagnostic_row = Adw.ActionRow(
            title=self.wifi_diagnostic["title"],
            subtitle=self.wifi_diagnostic["detail"])
        refresh_wifi = Gtk.Button(label="刷新状态")
        refresh_wifi.set_valign(Gtk.Align.CENTER)
        refresh_wifi.connect("clicked", self.on_wifi_status_refresh)
        self.wifi_diagnostic_row.add_suffix(refresh_wifi)
        wifi_grp.add(self.wifi_diagnostic_row)
        self.wifi_switch = Adw.SwitchRow(title="启用 WLAN")
        self.wifi_switch.set_active(False)
        self.wifi_switch.set_sensitive(False)
        self.loading_wifi_state = True
        self.wifi_switch.connect("notify::active", self.on_wifi_toggle)
        wifi_grp.add(self.wifi_switch)
        self.wifi_scan_btn = Gtk.Button(label="扫描并显示可用网络")
        self.wifi_scan_btn.set_margin_top(8)
        self.wifi_scan_btn.set_sensitive(self.wifi_diagnostic["available"])
        self.wifi_scan_btn.connect("clicked", self.on_wifi_scan)
        wifi_grp.add(self.wifi_scan_btn)
        box.append(wifi_grp)

        self.wifi_list_grp = Adw.PreferencesGroup(title="可用网络")
        self.wifi_list_state_row = Adw.ActionRow(
            title="正在检测无线网络",
            subtitle="检测完成后会在这里显示网络或明确的不可用原因。")
        self.wifi_list_grp.add(self.wifi_list_state_row)
        box.append(self.wifi_list_grp)

        # 蓝牙状态与修复：全部来自 DeviceController 的结构化状态。
        bt_grp = Adw.PreferencesGroup(
            title="蓝牙", description="显示硬件、驱动、服务、rfkill 与控制器状态。")
        self.bt_status_row = Adw.ActionRow(
            title="正在检测蓝牙", subtitle="正在读取硬件与服务状态...")
        refresh_bluetooth = Gtk.Button(label="刷新状态")
        refresh_bluetooth.set_valign(Gtk.Align.CENTER)
        refresh_bluetooth.connect("clicked", lambda _button: self.refresh_bluetooth_status())
        self.bt_status_row.add_suffix(refresh_bluetooth)
        bt_grp.add(self.bt_status_row)
        self.bt_detail_row = Adw.ActionRow(
            title="蓝牙详细状态", subtitle="正在读取硬件型号、模块与控制器...")
        bt_grp.add(self.bt_detail_row)
        self.bt_switch = Adw.SwitchRow(title="启用蓝牙")
        self.bt_switch.set_active(False)
        self.bt_switch.set_sensitive(False)
        self.loading_bt_state = True
        self.bt_switch.connect("notify::active", self.on_bt_toggle)
        bt_grp.add(self.bt_switch)
        self.bt_repair_button = Gtk.Button(label="修复蓝牙")
        self.bt_repair_button.add_css_class("suggested-action")
        self.bt_repair_button.connect("clicked", self.on_bluetooth_repair)
        self.bt_repair_row = self.button_row(
            "修复蓝牙", "仅在蓝牙服务停止或被 rfkill 阻止时可用。", self.bt_repair_button)
        self.bt_repair_row.set_visible(False)
        bt_grp.add(self.bt_repair_row)
        open_blueman = Gtk.Button(label="打开蓝牙设备管理器")
        open_blueman.set_margin_top(8)
        open_blueman.connect("clicked", lambda _b: subprocess.Popen(["blueman-manager"]))
        bt_grp.add(open_blueman)
        box.append(bt_grp)
        GLib.idle_add(self.on_wifi_status_refresh, None)
        GLib.idle_add(self.refresh_bluetooth_status)
        GLib.idle_add(self.refresh_time_sync_status)

        def wifi_radio_done(rc, output, _error):
            if rc == 0:
                self.wifi_switch.set_active(output.strip() == "enabled")
            self.loading_wifi_state = False

        run_capture_async(["nmcli", "radio", "wifi"], timeout=6, on_done=wifi_radio_done)
        return sc

    def apply_time_sync_status(self, status):
        state = (status or {}).get("state", "error")
        for css_class in ("ming-time-sync-ok", "ming-time-sync-waiting", "ming-time-sync-error"):
            self.time_sync_row.remove_css_class(css_class)
        if state == "synchronized":
            self.time_sync_row.set_title("已自动校时")
            self.time_sync_row.set_subtitle("时间已通过网络同步。时区保持为您当前的选择。")
            self.time_sync_row.add_css_class("ming-time-sync-ok")
        elif state == "waiting":
            self.time_sync_row.set_title("等待网络校时")
            network = (status or {}).get("network")
            self.time_sync_row.set_subtitle(
                "网络连接后会自动重试。" if network != "online" else "网络已连接，正在等待校时服务响应。")
            self.time_sync_row.add_css_class("ming-time-sync-waiting")
        else:
            self.time_sync_row.set_title("校时服务异常")
            self.time_sync_row.set_subtitle(
                (status or {}).get("error") or "无法读取系统校时服务；可尝试重试。")
            self.time_sync_row.add_css_class("ming-time-sync-error")

    def refresh_time_sync_status(self):
        generation = self.time_sync_probe_state.begin()
        self.time_sync_retry_btn.set_sensitive(False)

        def done(status, error):
            if not self.time_sync_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            self.time_sync_retry_btn.set_sensitive(True)
            snapshot = status or {"state": "error", "error": error or "无法读取校时状态。"}
            self.apply_time_sync_status(snapshot)
            return False

        run_task_async(time_sync_snapshot, done)

    def on_time_sync_retry(self, _button):
        self.time_sync_retry_btn.set_sensitive(False)
        self.time_sync_row.set_title("正在请求网络校时")
        self.time_sync_row.set_subtitle("此操作在后台进行，不会阻塞设置或改变时区。")

        def done(rc, _output, error):
            if self.network_page.get_root() is not self:
                return False
            if rc != 0:
                self.time_sync_retry_btn.set_sensitive(True)
                self.apply_time_sync_status({
                    "state": "error", "error": error or "校时服务未能启动。"})
                return False
            self.refresh_time_sync_status()
            return False

        run_capture_async(["pkexec", TIME_SYNC_HELPER, "sync"], timeout=80, on_done=done)

    def on_wifi_toggle(self, sw, _p):
        if self.loading_wifi_state:
            return
        state = "on" if sw.get_active() else "off"
        run_capture_async(
            ["nmcli", "radio", "wifi", state], timeout=8,
            on_done=lambda rc, _output, error: (
                self.toast("无线网络切换失败：%s" % (error or "NetworkManager 不可用"))
                if rc != 0 else None))

    def on_wifi_status_refresh(self, _button):
        generation = self.wifi_probe_state.begin()
        self.wifi_diagnostic_row.set_title("正在检测无线网络")
        self.wifi_diagnostic_row.set_subtitle("正在读取硬件、驱动、rfkill 与固件状态...")
        self.wifi_switch.set_sensitive(False)
        self.wifi_scan_btn.set_sensitive(False)
        self.wifi_scan_btn.set_label("扫描并显示可用网络")

        def done(snapshot, error):
            if not self.wifi_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            self.wifi_diagnostic = snapshot or {
                "state": "no_hardware", "present": False, "available": False,
                "title": "无线网络检测失败", "detail": error or "未知错误"}
            self.wifi_diagnostic_row.set_title(self.wifi_diagnostic["title"])
            self.wifi_diagnostic_row.set_subtitle(self.wifi_diagnostic["detail"])
            self.wifi_switch.set_sensitive(self.wifi_diagnostic["present"])
            self.wifi_scan_btn.set_sensitive(self.wifi_diagnostic["available"])
            if self.wifi_list_state_row:
                self.wifi_list_state_row.set_title(self.wifi_diagnostic["title"])
                self.wifi_list_state_row.set_subtitle(self.wifi_diagnostic["detail"])
            return False

        run_task_async(wifi_diagnostic_snapshot, done)

    def on_bt_toggle(self, sw, _p):
        if self.loading_bt_state:
            return
        generation = self.bluetooth_probe_state.begin()
        state = "on" if sw.get_active() else "off"

        def done(rc, _output, error):
            if not self.bluetooth_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            if rc != 0:
                self.toast("蓝牙切换失败：%s" % (error or "蓝牙服务不可用"), "error")
            else:
                self.refresh_bluetooth_status()
            return False

        run_capture_async(
            ["bluetoothctl", "power", state], timeout=8, on_done=done)

    def refresh_bluetooth_status(self):
        generation = self.bluetooth_probe_state.begin()
        self.bt_status_row.set_title("正在检测蓝牙")
        self.bt_status_row.set_subtitle("正在读取硬件、驱动、服务、rfkill 与控制器状态...")
        self.bt_switch.set_sensitive(False)
        self.bt_repair_row.set_visible(False)

        def done(status, error):
            if not self.bluetooth_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            status = status or {
                "state": "diagnostic_unavailable", "title": "蓝牙诊断不可用",
                "detail": error or "无法读取蓝牙状态。", "hardware": [], "modules": [],
                "service": {}, "rfkill": {}, "controller": {},
            }
            state = status.get("state", "diagnostic_unavailable")
            self.bt_status_row.set_title(status.get("title") or "蓝牙状态未知")
            self.bt_status_row.set_subtitle(status.get("detail") or "蓝牙状态工具未提供原因。")
            hardware = status.get("hardware") or []
            models = [item.get("model") or item.get("id") for item in hardware]
            modules = ", ".join(status.get("modules") or []) or "未加载"
            service = status.get("service") or {}
            rfkill = status.get("rfkill") or {}
            controller = status.get("controller") or {}
            rfkill_text = "已阻止" if (rfkill.get("soft_blocked") or rfkill.get("hard_blocked")) else "未阻止"
            controller_text = controller.get("model") or controller.get("id") or "未发现"
            self.bt_detail_row.set_subtitle(
                "硬件：%s · 模块：%s · 服务：%s · rfkill：%s · 控制器：%s" % (
                    "；".join(filter(None, models)) or "未检测到", modules,
                    "运行中" if service.get("active") else "未运行", rfkill_text, controller_text))
            self.loading_bt_state = True
            self.bt_switch.set_active(bool(controller.get("powered")))
            self.loading_bt_state = False
            self.bt_switch.set_sensitive(state not in {"no_hardware", "diagnostic_unavailable"})
            self.bt_repair_row.set_visible(bluetooth_repair_allowed(status))
            return False

        run_task_async(bluetooth_status_snapshot, done)

    def on_bluetooth_repair(self, _button):
        generation = self.bluetooth_probe_state.begin()
        self.bt_repair_button.set_sensitive(False)
        self.bt_repair_button.set_label("正在修复...")

        def checked(status, error):
            if not self.bluetooth_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            status = status or {"state": "diagnostic_unavailable", "rfkill": {}}
            if not bluetooth_repair_allowed(status):
                self.bt_repair_button.set_label("修复蓝牙")
                self.bt_repair_button.set_sensitive(True)
                self.toast("蓝牙修复未执行：当前状态不允许此修复。", "warning")
                return False

            def done(rc, _output, repair_error):
                if not self.bluetooth_probe_state.accept(generation):
                    return False
                if self.network_page.get_root() is not self:
                    return False
                self.bt_repair_button.set_label("修复蓝牙")
                self.bt_repair_button.set_sensitive(True)
                self.toast(
                    "蓝牙修复已完成，正在重新检测。" if rc == 0
                    else "蓝牙修复未成功：%s" % (
                        repair_error or "授权或修复服务失败。"),
                    "info" if rc == 0 else "error")
                self.refresh_bluetooth_status()
                return False

            run_capture_async(
                ["pkexec", "ming-radio-repair", "bluetooth"], timeout=35, on_done=done)

        run_task_async(bluetooth_status_snapshot, checked)

    def on_wifi_scan(self, _btn):
        generation = self.wifi_probe_state.begin()
        self.wifi_scan_btn.set_sensitive(False)
        self.wifi_scan_btn.set_label("正在扫描...")

        def done(snapshot, task_error):
            if not self.wifi_probe_state.accept(generation):
                return False
            if self.network_page.get_root() is not self:
                return False
            self.wifi_scan_btn.set_label("扫描并显示可用网络")
            snapshot = snapshot or {"ok": False, "error": task_error or "无法扫描无线网络。", "networks": []}
            networks = snapshot.get("networks") or []
            self.wifi_scan_btn.set_sensitive(
                bool(networks) or self.wifi_diagnostic.get("available", False))
            new_grp = Adw.PreferencesGroup(title="可用网络 (%d)" % len(networks))
            if not networks:
                if snapshot.get("ok"):
                    title = "未发现可用无线网络"
                    reason = "无线网卡可用，但本次扫描没有发现可连接的网络。"
                else:
                    title = self.wifi_diagnostic.get("title") or "无线网络不可用"
                    reason = snapshot.get("error") or self.wifi_diagnostic.get("detail") or "无线扫描失败。"
                self.wifi_list_state_row = Adw.ActionRow(title=title, subtitle=reason)
                new_grp.add(self.wifi_list_state_row)
            else:
                self.wifi_list_state_row = None
            for network in networks[:30]:
                ssid = network["ssid"] or "（隐藏网络）"
                bssid = network["bssid"] or "未知"
                signal = network["signal"]
                signal_text = "%s%%" % signal if signal is not None else "未知"
                row = Adw.ActionRow(
                    title=ssid,
                    subtitle="频段 %s · 信道 %s · 信号 %s · 安全 %s · BSSID %s · 接口 %s" % (
                        network["band"] or "未知", network["channel"] or "未知",
                        signal_text, network["security"] or "开放网络", bssid,
                        network["ifname"] or "未知"))
                connect = Gtk.Button(label="连接")
                connect.set_valign(Gtk.Align.CENTER)
                connect.set_sensitive(bool(network["ssid"] and network["bssid"] and network["ifname"]))
                connect.connect("clicked", self.on_wifi_connect, network)
                row.add_suffix(connect)
                new_grp.add(row)
            parent = self.wifi_list_grp.get_parent()
            if not parent:
                return False
            parent.remove(self.wifi_list_grp)
            parent.append(new_grp)
            self.wifi_list_grp = new_grp
            return False

        run_task_async(wifi_scan_snapshot, done)

    def on_wifi_connect(self, _btn, network):
        ssid = network["ssid"]
        bssid = network["bssid"]
        ifname = network["ifname"]
        dlg = Adw.MessageDialog(
            transient_for=self, heading="连接到 %s" % ssid,
            body="将绑定到 BSSID %s（接口 %s）。如需密码，会仅通过标准输入安全传给 NetworkManager，绝不会写入命令参数、日志或诊断数据。" % (bssid, ifname))
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        entry.set_placeholder_text("开放网络可留空")
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "取消")
        dlg.add_response("ok", "连接")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        def on_resp(d, resp):
            if resp == "ok":
                generation = self.wifi_connect_state.begin()
                secret = entry.get_text()
                entry.set_text("")
                def connected(result, error):
                    self.apply_wifi_connect_result(generation, ssid, bssid, result, error)

                def parse_connected(rc, output, error):
                    try:
                        result = json.loads(output) if output else None
                    except (TypeError, ValueError):
                        result = None
                    connected(result, error if rc != 0 else "")

                command = wifi_connect_command(ssid, bssid, ifname, with_secret=bool(secret))
                if secret:
                    run_capture_stdin_async(command, secret + "\n", timeout=40, on_done=parse_connected)
                else:
                    run_capture_async(command, timeout=40, on_done=parse_connected)
        dlg.connect("response", on_resp)
        dlg.present()

    def apply_wifi_connect_result(self, generation, ssid, bssid, result, error):
        if not self.wifi_connect_state.accept(generation):
            return False
        if self.network_page.get_root() is not self:
            return False
        result = result or {"ok": False, "error": error or "无线连接失败。"}
        self.toast(
            "已连接 %s（%s）。" % (ssid, bssid) if result.get("ok")
            else "连接失败：%s" % (
                result.get("error") or "NetworkManager 未返回可读原因。"),
            "info" if result.get("ok") else "error")
        return False

    # ---- 3. 存储可视化（合并后空间使用率） ----
    def build_storage(self):
        sc, box = self.page_scroller()
        grp = Adw.PreferencesGroup(title="存储空间",
                                   description="Ming OS 已把多块硬盘合并为一个空间，您无需关心分区。")
        box.append(grp)

        # 读取 P2 写下的合并盘信息；回退到 / 的 df
        info = {}
        try:
            with open("/run/ming-os/storage-info") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v
        except Exception:
            pass

        targets = []
        if info.get("data_mount"):
            targets.append(("合并数据空间", info["data_mount"]))
        targets.append(("系统空间", "/"))
        targets.append(("主目录", HOME))

        for label, path in targets:
            try:
                st = shutil.disk_usage(path)
                used = st.used; total = st.total
            except Exception:
                continue
            frac = (used / total) if total else 0
            row = Adw.ActionRow(title=label,
                                subtitle="%s / %s 已用" % (self._hsize(used), self._hsize(total)))
            bar = Gtk.ProgressBar()
            bar.set_fraction(frac)
            bar.set_valign(Gtk.Align.CENTER)
            bar.set_size_request(200, -1)
            if frac > 0.9:
                bar.add_css_class("error")
            row.add_suffix(bar)
            grp.add(row)

        refresh = Gtk.Button(label="刷新")
        refresh.set_margin_top(12)
        refresh.connect("clicked", lambda _b: self.toast("已是最新空间使用情况。"))
        box.append(refresh)
        return sc

    def _hsize(self, n):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024:
                return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
            n /= 1024
        return "%.1f PB" % n

    # ---- 4. OTA 更新（封装 ming-update） ----
    def build_update(self):
        sc, box = self.page_scroller()
        cur = "未知"
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("VERSION_ID="):
                        cur = line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        grp = Adw.PreferencesGroup(title="系统更新",
                                   description="当前版本：Ming OS %s\n小修复（驱动/补丁）无需重启；大版本升级会保留用户文件。" % cur)
        box.append(grp)

        self.update_status = Gtk.Label(label="点击下方按钮检查更新。", xalign=0, wrap=True)
        self.update_status.set_margin_top(6)
        grp.add(self.update_status)

        self.update_bar = Gtk.ProgressBar()
        self.update_bar.set_show_text(True)
        self.update_bar.set_visible(False)
        self.update_bar.set_margin_top(10)
        grp.add(self.update_bar)

        btn_box = Gtk.FlowBox()
        btn_box.set_selection_mode(Gtk.SelectionMode.NONE)
        btn_box.set_min_children_per_line(1)
        btn_box.set_max_children_per_line(2)
        btn_box.set_column_spacing(10)
        btn_box.set_row_spacing(8)
        btn_box.set_homogeneous(True)
        btn_box.set_margin_top(12)
        check = Gtk.Button(label="检查更新")
        check.add_css_class("suggested-action")
        check.connect("clicked", self.on_update_check)
        patch_btn = Gtk.Button(label="应用小修复")
        patch_btn.set_tooltip_text("应用 patch 级更新（驱动/配置/安全补丁），通常无需重启。")
        patch_btn.connect("clicked", self.on_patch_update)
        oneclick = Gtk.Button(label="大版本升级")
        oneclick.set_tooltip_text("下载安装 major ISO 大版本，/home 用户文件严格保留。")
        oneclick.connect("clicked", self.on_update_oneclick)
        shutdown_btn = Gtk.Button(label="更新并关机")
        shutdown_btn.add_css_class("destructive-action")
        shutdown_btn.set_tooltip_text("检查→下载→安装更新，完成后1分钟内关机。适合睡前操作。")
        shutdown_btn.connect("clicked", self.on_update_and_shutdown)
        for button in (check, patch_btn, oneclick, shutdown_btn):
            btn_box.insert(button, -1)
        grp.add(btn_box)
        return sc

    def on_update_check(self, _btn):
        self.update_status.set_label("正在检查更新…")
        def line(l): self.update_status.set_label(l)
        def done(rc):
            self.update_status.set_label("检查完成。" if rc == 0 else "未发现可用更新或检查失败。")
        run_async(["ming-update", "check"], on_line=line, on_done=done)

    def on_patch_update(self, _btn):
        """应用 patch 级小修复（无需重启）。"""
        self.update_status.set_label("正在应用 patch 小修复…")
        self.update_bar.set_visible(True)
        self.update_bar.set_fraction(0.1)
        self.update_bar.set_text("patch 更新中…")
        def line(l): self.update_status.set_label(l)
        def done(rc):
            self.update_bar.set_fraction(1.0)
            if rc == 0:
                self.update_bar.set_text("完成")
                self.update_status.set_label("patch 更新完成，无需重启。")
            else:
                self.update_bar.set_text("失败或无更新")
                self.update_status.set_label("没有可用 patch 更新，或当前已是最新。")
                self.update_bar.set_visible(False)
        run_async(["pkexec", "ming-update", "patch"], on_line=line, on_done=done)

    def on_update_oneclick(self, _btn):
        # 检查 -> 下载 -> 安装，进度条粗粒度推进
        self.update_bar.set_visible(True)
        self.update_bar.set_fraction(0.05)
        self.update_bar.set_text("检查中…")

        def after_check(rc):
            if rc != 0:
                self.update_status.set_label("没有可用更新。")
                self.update_bar.set_visible(False)
                return
            self.update_bar.set_fraction(0.3)
            self.update_bar.set_text("下载中…")
            def dl_line(l): self.update_status.set_label(l)
            def after_dl(rc2):
                if rc2 != 0:
                    self.update_status.set_label("下载失败。")
                    self.update_bar.set_visible(False)
                    return
                self.update_bar.set_fraction(0.7)
                self.update_bar.set_text("安装中…")
                def after_install(rc3):
                    self.update_bar.set_fraction(1.0)
                    self.update_bar.set_text("完成" if rc3 == 0 else "安装失败")
                    self.update_status.set_label(
                        "更新已就绪，重启后生效。" if rc3 == 0 else "安装失败，请稍后重试。")
                run_async(["pkexec", "ming-update", "install"], on_line=dl_line, on_done=after_install)
            run_async(["ming-update", "download"], on_line=dl_line, on_done=after_dl)
        run_async(["ming-update", "check"], on_line=lambda l: self.update_status.set_label(l),
                  on_done=after_check)

    def on_update_and_shutdown(self, _btn):
        """更新并关机：先弹确认对话框，再后台运行 ming-update auto-shutdown。"""
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="确认更新并关机？",
            body="系统将自动完成「检查→下载→安装」全过程，完成后 1 分钟内关机。\n\n"
                 "如果当前没有更新，系统不会关机。\n\n"
                 "适合睡前操作，明天开机即可用上新版本。")
        dlg.add_response("cancel", "取消")
        dlg.add_response("ok", "确认，开始更新并关机")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")

        def on_resp(d, resp):
            if resp != "ok":
                return
            self.update_status.set_label("正在执行更新并关机流程…")
            self.update_bar.set_visible(True)
            self.update_bar.set_fraction(0.1)
            self.update_bar.set_text("自动更新中…")

            def line(l):
                self.update_status.set_label(l)

            def done(rc):
                self.update_bar.set_fraction(1.0)
                if rc == 0:
                    self.update_bar.set_text("完成，即将关机")
                    self.update_status.set_label("更新流程完成，系统将在 1 分钟内关机。")
                else:
                    self.update_bar.set_text("失败")
                    self.update_status.set_label("更新或关机流程失败，请查看日志。")
                    self.update_bar.set_visible(False)

            run_async(["pkexec", "ming-update", "auto-shutdown"], on_line=line, on_done=done)

        dlg.connect("response", on_resp)
        dlg.present()

    # ---- 5. 显示与无障碍（真实 xrandr 模式 + 独立界面大小） ----
    def build_display(self):
        sc, box = self.page_scroller()
        screen_grp = Adw.PreferencesGroup(
            title="屏幕",
            description="分辨率与刷新率来自当前显示器；应用后请在 15 秒内确认，否则自动恢复。")
        box.append(screen_grp)
        self.display_summary_row = Adw.ActionRow(
            title="正在读取显示器", subtitle="请稍候…")
        screen_grp.add(self.display_summary_row)
        self.display_output_row = Adw.ComboRow(title="显示器", subtitle="选择要调整的屏幕")
        self.display_output_row.set_model(Gtk.StringList.new(["正在读取…"]))
        self.display_mode_row = Adw.ComboRow(title="屏幕分辨率", subtitle="例如 1920 × 1080 · 60 Hz")
        self.display_mode_row.set_model(Gtk.StringList.new(["正在读取…"]))
        self.display_rotation_row = Adw.ComboRow(title="屏幕方向", subtitle="保持正常方向即可")
        self.display_rotation_row.set_model(Gtk.StringList.new(["正常", "向左旋转", "倒置", "向右旋转"]))
        self.display_output_row.connect("notify::selected", self.on_display_output_changed)
        screen_grp.add(self.display_output_row)
        screen_grp.add(self.display_mode_row)
        screen_grp.add(self.display_rotation_row)
        self.display_apply_button = Gtk.Button(label="应用显示设置")
        self.display_apply_button.add_css_class("suggested-action")
        self.display_apply_button.set_sensitive(False)
        self.display_apply_button.connect("clicked", self.on_display_apply)
        screen_grp.add(self.button_row(
            "确认保护", "变更后会出现倒计时确认；没有确认将自动恢复原设置。",
            self.display_apply_button))

        scale_grp = Adw.PreferencesGroup(
            title="界面大小",
            description="只改变文字、图标与 Dock 大小；不会改变屏幕分辨率。")
        box.append(scale_grp)
        self.scale_options = [
            (100, "100% 标准"), (125, "125% 较大"), (150, "150% 很大"),
            (175, "175% 超大"), (200, "200% 特大"),
        ]
        self.scale_choice_row = Adw.ComboRow(title="界面大小", subtitle="此选择会保留，不会被桌面修复覆盖。")
        self.scale_choice_row.set_model(Gtk.StringList.new([label for _percent, label in self.scale_options]))
        current_percent = load_scale_preference() or 100
        self.scale_choice_row.set_selected(next(
            index for index, (percent, _label) in enumerate(self.scale_options)
            if percent == current_percent))
        self.scale_choice_row.connect("notify::selected", self.on_interface_scale_changed)
        scale_grp.add(self.scale_choice_row)
        self.display_page = sc
        self.display_output_items = []
        self.display_mode_items = []
        self.refresh_display_status()
        return sc

    def refresh_display_status(self):
        self.display_apply_button.set_sensitive(False)

        def done(snapshot, error):
            if self.display_page.get_root() is not self:
                return False
            snapshot = snapshot or {"ok": False, "error": error or "未知错误", "outputs": []}
            outputs = [item for item in snapshot.get("outputs", []) if item.get("connected")]
            if not snapshot.get("ok") or not outputs:
                self.display_output_items = []
                self.display_mode_items = []
                self.display_summary_row.set_title("无法读取显示器")
                self.display_summary_row.set_subtitle(snapshot.get("error") or "未检测到已连接显示器。")
                self.display_apply_button.set_sensitive(False)
                return False
            self.display_output_items = outputs
            labels = [item.get("name") or "未知显示器" for item in outputs]
            self.display_output_row.set_model(Gtk.StringList.new(labels))
            self.display_output_row.set_selected(0)
            self.display_summary_row.set_title("已检测到 %d 个显示器" % len(outputs))
            self.display_summary_row.set_subtitle("选择分辨率后应用；如画面不合适会自动恢复。")
            self.populate_display_modes()
            return False

        run_task_async(display_status_snapshot, done)

    def on_display_output_changed(self, _row, _param):
        if getattr(self, "display_output_items", None):
            self.populate_display_modes()

    def populate_display_modes(self):
        index = min(self.display_output_row.get_selected(), len(self.display_output_items) - 1)
        output = self.display_output_items[index]
        self.display_mode_items = [
            {"mode": mode["mode"], "rate": rate}
            for mode in output.get("modes", [])
            for rate in mode.get("rates", [])
        ]
        labels = ["%s%s" % (
            display_mode_label(item["mode"], item["rate"]),
            "（当前）" if item["mode"] == output.get("mode") and item["rate"] == output.get("rate") else "")
            for item in self.display_mode_items]
        self.display_mode_row.set_model(Gtk.StringList.new(labels or ["未提供可用模式"]))
        selected = next((
            position for position, item in enumerate(self.display_mode_items)
            if item["mode"] == output.get("mode") and item["rate"] == output.get("rate")), 0)
        self.display_mode_row.set_selected(selected)
        rotations = ["normal", "left", "inverted", "right"]
        self.display_rotation_row.set_selected(rotations.index(output.get("rotation"))
                                               if output.get("rotation") in rotations else 0)
        self.display_apply_button.set_sensitive(bool(self.display_mode_items))

    def on_display_apply(self, _button):
        if not self.display_output_items or not self.display_mode_items:
            return
        output = self.display_output_items[min(self.display_output_row.get_selected(), len(self.display_output_items) - 1)]
        choice = self.display_mode_items[min(self.display_mode_row.get_selected(), len(self.display_mode_items) - 1)]
        rotations = ["normal", "left", "inverted", "right"]
        rotation = rotations[min(self.display_rotation_row.get_selected(), len(rotations) - 1)]
        self.display_apply_button.set_sensitive(False)
        command = [
            DISPLAY_CONTROL_HELPER, "apply", "--output", output["name"],
            "--mode", choice["mode"], "--rate", choice["rate"], "--rotation", rotation,
        ]

        def done(rc, text, error):
            self.display_apply_button.set_sensitive(True)
            try:
                result = json.loads(text)
            except (TypeError, ValueError):
                result = {"ok": False, "error": error or text or "显示设置服务返回了无效结果。"}
            if not result.get("ok"):
                self.toast(result.get("error") or "应用显示设置失败，已保留原设置。", "error")
                return False
            self.show_display_confirmation(result["token"], result.get("expires_in", 15))
            return False

        run_capture_async(command, timeout=10, on_done=done)

    def show_display_confirmation(self, token, seconds):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="保留此显示设置？",
            body="请确认画面清晰可用；未确认时系统会自动恢复原设置。")
        dialog.add_response("rollback", "恢复")
        dialog.add_response("confirm", "保留")
        dialog.set_default_response("confirm")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        remaining = {"seconds": int(seconds)}

        def tick():
            remaining["seconds"] -= 1
            if remaining["seconds"] <= 0:
                dialog.close()
                self.toast("未确认显示设置，系统正在自动恢复原设置。", "warning")
                return False
            dialog.set_body("请在 %d 秒内确认；否则自动恢复原设置。" % remaining["seconds"])
            return True

        timer = GLib.timeout_add_seconds(1, tick)

        def respond(_dialog, response):
            GLib.source_remove(timer)
            action = "confirm" if response == "confirm" else "rollback"

            def done(rc, text, error):
                try:
                    result = json.loads(text)
                except (TypeError, ValueError):
                    result = {"ok": False, "error": error or text or "显示设置操作失败。"}
                self.toast(result.get("message") if result.get("ok") else result.get("error"),
                           "info" if result.get("ok") else "error")
                self.refresh_display_status()
                return False

            run_capture_async([DISPLAY_CONTROL_HELPER, action, token], timeout=10, on_done=done)

        dialog.connect("response", respond)
        dialog.present()

    def on_interface_scale_changed(self, row, _param):
        selected = min(row.get_selected(), len(self.scale_options) - 1)
        self.apply_interface_scale(self.scale_options[selected][0])

    def on_scale_changed(self, slider):
        # Compatibility for older callers that still pass a Gtk.Scale.
        self.apply_interface_scale(int(round(slider.get_value() * 100 / 11.0)))

    def apply_interface_scale(self, percent):
        percent = min((100, 125, 150, 175, 200), key=lambda value: abs(value - int(percent)))
        size = int(round(11 * percent / 100.0))
        # 1) 系统字体
        run(["xfconf-query", "-c", "xsettings", "-p", "/Gtk/FontName", "-s", "Sans %d" % size])
        run(["xfconf-query", "-c", "xfwm4", "-p", "/general/title_font", "-s", "Sans Bold %d" % size])
        # 2) 桌面图标随字体等比（xfdesktop icon-size），基准 11→48px
        icon_px = int(round(48 * size / 11.0))
        run(["xfconf-query", "-c", "xfce4-desktop", "-p", "/desktop-icons/icon-size",
             "-n", "-t", "int", "-s", str(icon_px)])
        # 3) Dock (Plank) 图标大小：写入 settings 并重启 plank
        plank = os.path.join(HOME, ".config/plank/dock1/settings")
        dock_px = max(32, min(96, int(round(48 * size / 11.0))))
        if os.path.exists(plank):
            try:
                run(["sed", "-i", "s/^IconSize=.*/IconSize=%d/" % dock_px, plank])
                run(["bash", "-c", "pkill plank 2>/dev/null || true; sleep 1; /usr/local/bin/ming-plank-watchdog"])
            except Exception:
                pass
        # 4) GSettings 文本缩放（GTK4 应用自身也放大）
        factor = size / 11.0
        run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor",
             "%.2f" % factor])
        try:
            save_scale_preference(percent)
        except OSError as exc:
            self.toast("界面大小已应用，但无法保存偏好：%s" % exc, "warning")

    # ---- 6. 高级设置：只展示 Ming 桌面仍然支持的有效选项 ----
    def build_advanced(self):
        sc, box = self.page_scroller()
        self.advanced_page = sc

        intro = Adw.PreferencesGroup(
            title="Ming 高级设置",
            description="这些选项直接控制 Ming 桌面当前使用的后端，并在写入后检查是否真正生效。")
        box.append(intro)
        intro.add(Adw.ActionRow(
            title="兼容模式说明",
            subtitle="原生 Xfce 面板、桌面图标和工作区设置已由 Ming 桌面接管，因此不再显示无效的 Xfce 设置入口。"))

        window_grp = Adw.PreferencesGroup(title="窗口行为")
        box.append(window_grp)
        window_grp.add(self.backend_combo_row(
            "窗口聚焦方式", "单击更适合触屏；跟随鼠标适合传统桌面。",
            "focus_mode", ["单击聚焦", "跟随鼠标"], ["click", "follows"]))
        window_grp.add(self.backend_scale_row(
            "窗口自动置顶延迟", "仅在跟随鼠标模式下生效，单位毫秒。",
            "window_raise_delay", 0, 2000, 100, 250))
        window_grp.add(self.backend_switch_row(
            "减少动态效果", "使用短淡入替代抽屉和应用展开动画。",
            "reduced_motion", False))
        window_grp.add(self.backend_combo_row(
            "合成器模式", "自动模式优先；老显卡或虚拟机可选择软件模式。",
            "compositor_profile", ["自动", "软件兼容", "关闭"],
            ["auto", "software", "off"]))

        power_grp = Adw.PreferencesGroup(title="电源策略")
        box.append(power_grp)
        power_grp.add(self.backend_combo_row(
            "合上笔记本盖子", "交流电与电池使用同一策略，写入后立即读回确认。",
            "lid_close_action", ["不执行操作", "挂起", "休眠"],
            ["nothing", "suspend", "hibernate"], default_index=1))

        audio_grp = Adw.PreferencesGroup(
            title="声音设备", description="切换 PulseAudio 当前默认设备，不影响应用自己的音量。")
        box.append(audio_grp)
        audio_grp.add(self.backend_collection_row(
            "音频输出", "选择扬声器、耳机或 HDMI 输出。",
            ["audio", "list", "output"], ["audio", "set", "output"]))
        audio_grp.add(self.backend_collection_row(
            "音频输入", "选择内置麦克风或外接录音设备。",
            ["audio", "list", "input"], ["audio", "set", "input"]))

        call_audio_grp = Adw.PreferencesGroup(
            title="声音与通话", description="可安全检查内置麦克风；修复不会覆盖蓝牙、USB 或 HDMI 设备。")
        box.append(call_audio_grp)
        self.call_audio_status_row = Adw.ActionRow(
            title="通话音频状态", subtitle="正在读取麦克风、静音与全双工配置...")
        refresh_call_audio = Gtk.Button(label="刷新状态")
        refresh_call_audio.set_valign(Gtk.Align.CENTER)
        refresh_call_audio.connect("clicked", lambda _button: self.refresh_call_audio_status())
        self.call_audio_status_row.add_suffix(refresh_call_audio)
        call_audio_grp.add(self.call_audio_status_row)
        self.audio_repair_button = Gtk.Button(label="修复通话音频")
        self.audio_repair_button.add_css_class("suggested-action")
        self.audio_repair_button.connect("clicked", self.on_audio_repair_call)
        call_audio_grp.add(self.button_row(
            "修复通话音频", "只在内置麦克风缺失时恢复全双工配置。", self.audio_repair_button))
        self.audio_test_button = Gtk.Button(label="三秒麦克风测试")
        self.audio_test_button.connect("clicked", self.on_audio_test_input)
        call_audio_grp.add(self.button_row(
            "三秒麦克风测试", "录制 3 秒并仅报告是否检测到有效声音数据。", self.audio_test_button))
        GLib.idle_add(self.refresh_call_audio_status)

        defaults_grp = Adw.PreferencesGroup(
            title="默认应用", description="仅显示已安装且声明支持对应用途的应用。")
        box.append(defaults_grp)
        defaults_grp.add(self.backend_collection_row(
            "网页浏览器", "用于网页链接。",
            ["default-app", "list", "browser"], ["default-app", "set", "browser"]))
        defaults_grp.add(self.backend_collection_row(
            "邮件应用", "用于电子邮件链接。",
            ["default-app", "list", "mail"], ["default-app", "set", "mail"]))
        defaults_grp.add(self.backend_collection_row(
            "文件管理器", "用于文件夹和磁盘位置。",
            ["default-app", "list", "files"], ["default-app", "set", "files"]))

        dock_grp = Adw.PreferencesGroup(title="Dock")
        box.append(dock_grp)
        dock_grp.add(self.backend_scale_row(
            "图标大小", "调整 Dock 的基础图标尺寸。",
            "dock_icon_size", 32, 96, 4, 48))
        dock_grp.add(self.backend_scale_row(
            "悬停放大", "控制苹果式放大幅度。",
            "dock_zoom_percent", 100, 180, 5, 126))
        dock_grp.add(self.backend_combo_row(
            "隐藏方式", "智能隐藏只在窗口挡住 Dock 时收起。",
            "dock_hide_mode", ["始终显示", "智能隐藏", "自动隐藏"],
            ["never", "intellihide", "autohide"]))

        notify_grp = Adw.PreferencesGroup(title="通知")
        box.append(notify_grp)
        notify_grp.add(self.backend_switch_row(
            "免打扰", "保留通知历史，但暂时不显示弹窗。",
            "notification_dnd", False))
        notify_grp.add(self.backend_scale_row(
            "历史记录数量", "小组件最多保留和显示的最近通知数量。",
            "notification_history_size", 10, 200, 10, 50))

        autostart_grp = Adw.PreferencesGroup(
            title="登录时自动启动",
            description="Ming 桌面必需服务会显示但不能禁用；其他项目可随时开关。")
        box.append(autostart_grp)
        self.load_autostart_group(autostart_grp)

        repair_grp = Adw.PreferencesGroup(
            title="兼容性与修复",
            description="修复操作只恢复 Ming 桌面组件，不重置个人文件。")
        box.append(repair_grp)
        display_repair = Gtk.Button(label="重新应用桌面配置")
        display_repair.connect(
            "clicked",
            lambda _button: self.run_helper(["ming-helper", "repair-display"], "桌面配置修复"))
        repair_grp.add(self.button_row(
            "桌面、Dock 与壁纸", "重新应用当前分辨率、壁纸、Dock 和透明度策略。",
            display_repair))
        self.window_manager_repair_button = Gtk.Button(label="修复窗口控制")
        self.window_manager_repair_button.add_css_class("suggested-action")
        self.window_manager_repair_button.connect("clicked", self.on_window_manager_repair)
        repair_grp.add(self.button_row(
            "窗口最小化、最大化与关闭", "仅检查并恢复 Xfwm 窗口控制，不会关闭正在编辑的文档。",
            self.window_manager_repair_button))
        diagnostics = Gtk.Button(label="打开兼容性诊断")
        diagnostics.connect(
            "clicked",
            lambda _button: self.run_helper(["ming-driver-diagnose"], "兼容性诊断"))
        repair_grp.add(self.button_row(
            "兼容性诊断", "查看底层 Xfconf、显卡、输入、音频和网络状态。",
            diagnostics))
        return sc

    def on_window_manager_repair(self, _button):
        """Run the X11 repair outside GTK and leave every client application intact."""
        button = self.window_manager_repair_button
        button.set_sensitive(False)
        button.set_label("正在检查...")

        def done(rc, output, error):
            if self.advanced_page.get_root() is not self:
                return False
            button.set_sensitive(True)
            button.set_label("修复窗口控制")
            log_path = os.path.join(HOME, ".cache", "ming-os", "window-manager.log")
            if rc == 0:
                self.toast("窗口控制已检查并恢复；不会关闭任何应用。日志：%s" % log_path, "info")
            else:
                reason = compact_output(error or output, max_lines=2)
                self.toast("窗口控制未恢复：%s。日志：%s" % (
                    reason or "请稍后重试", log_path), "error")
            return False

        run_capture_async(["ming-window-control", "repair"], timeout=20, on_done=done)

    def refresh_call_audio_status(self):
        generation = self.audio_probe_state.begin()
        self.call_audio_status_row.set_title("通话音频状态")
        self.call_audio_status_row.set_subtitle("正在读取麦克风、静音与全双工配置...")
        self.audio_repair_button.set_sensitive(False)
        self.audio_test_button.set_sensitive(False)

        def done(status, error):
            if not self.audio_probe_state.accept(generation):
                return False
            if self.advanced_page.get_root() is not self:
                return False
            status = status or {"available": False, "call_ready": False,
                                "error": error or "声音状态读取失败。"}
            available = bool(status.get("available"))
            call_ready = bool(status.get("call_ready"))
            input_state = "已检测到" if status.get("physical_input_present") else "未检测到"
            muted = status.get("input_muted")
            muted_text = "未知" if muted is None else ("已静音" if muted else "未静音")
            self.call_audio_status_row.set_title(
                "通话音频正常" if call_ready else "通话音频需要注意")
            self.call_audio_status_row.set_subtitle(
                "输入：%s · 麦克风：%s · 全双工：%s%s" % (
                    input_state, muted_text,
                    "已启用" if status.get("duplex_profile_active") else "未启用",
                    (" · 原因：%s" % status.get("error")) if status.get("error") else ""))
            self.audio_repair_button.set_sensitive(available)
            self.audio_test_button.set_sensitive(available)
            return False

        run_task_async(audio_status_snapshot, done)

    def on_audio_repair_call(self, _button):
        generation = self.audio_probe_state.begin()
        self.audio_repair_button.set_sensitive(False)
        self.audio_repair_button.set_label("正在修复...")

        def done(result, error):
            if not self.audio_probe_state.accept(generation):
                return False
            if self.advanced_page.get_root() is not self:
                return False
            self.audio_repair_button.set_label("修复通话音频")
            result = result or {"ok": False, "error": error or "通话音频修复失败。"}
            if result.get("ok"):
                message = (
                    "已恢复内置通话音频。" if result.get("changed")
                    else "现有通话输入保持不变，未覆盖外接音频设备。")
                self.toast(message, "info")
            else:
                self.toast("通话音频修复未成功：%s" % (
                    result.get("error") or "未返回可读原因。"), "error")
            self.refresh_call_audio_status()
            return False

        run_task_async(audio_repair_call_snapshot, done)

    def on_audio_test_input(self, _button):
        generation = self.audio_probe_state.begin()
        self.audio_test_button.set_sensitive(False)
        self.audio_test_button.set_label("正在进行 3 秒测试...")

        def done(result, error):
            if not self.audio_probe_state.accept(generation):
                return False
            if self.advanced_page.get_root() is not self:
                return False
            self.audio_test_button.set_label("三秒麦克风测试")
            result = result or {"ok": False, "error": error or "麦克风测试失败。"}
            if result.get("ok"):
                self.toast("3 秒麦克风测试通过，已检测到有效声音数据（%s 字节）。" % (
                    result.get("bytes", 0)), "info")
            else:
                self.toast("3 秒麦克风测试未通过：%s" % (
                    result.get("error") or "未检测到有效声音数据。"), "error")
            self.refresh_call_audio_status()
            return False

        run_task_async(audio_test_input_snapshot, done)

    # ---- 7. 硬件与诊断：老电脑网络、驱动、打印、诊断包 ----
    def build_hardware(self):
        sc, box = self.page_scroller()

        summary = Adw.PreferencesGroup(
            title="硬件状态",
            description="显卡、声卡和网络均显示为正常、注意或失败，并给出型号、驱动与下一步建议。")
        box.append(summary)

        self.hardware_summary_row = Adw.ActionRow(
            title="正在读取硬件状态", subtitle="设备卡片来自 Ming 硬件状态服务...")
        summary.add(self.hardware_summary_row)
        self.hardware_refresh_button = Gtk.Button(label="刷新硬件信息")
        self.hardware_refresh_button.connect(
            "clicked", lambda _button: self.refresh_hardware_status())
        summary.add(self.button_row(
            "重新检测", "重新读取结构化设备卡片和 Broadcom 状态。",
            self.hardware_refresh_button))

        hardware_grp = Adw.PreferencesGroup(
            title="设备卡片",
            description="默认不显示原始日志，避免把难读的底层输出当作故障说明。")
        box.append(hardware_grp)
        self.hardware_graphics_row = Adw.ActionRow(
            title="显卡", subtitle="正在读取显卡与驱动...")
        self.hardware_audio_row = Adw.ActionRow(
            title="声卡", subtitle="正在读取声卡与驱动...")
        self.hardware_network_row = Adw.ActionRow(
            title="网络", subtitle="正在读取网络接口与驱动...")
        for row in (
                self.hardware_graphics_row, self.hardware_audio_row,
                self.hardware_network_row):
            hardware_grp.add(row)

        net_grp = Adw.PreferencesGroup(title="网络修复", description="优先使用更稳的 wpa_supplicant；如果某台机器更适合 iwd，可以一键切换。")
        box.append(net_grp)
        wpa = Gtk.Button(label="修复无线网络（推荐）")
        wpa.add_css_class("suggested-action")
        wpa.connect("clicked", lambda _b: self.run_helper(self.pkexec_cmd("ming-network-repair", "--use-wpa"), "网络修复"))
        iwd = Gtk.Button(label="切换为 iwd 后端")
        iwd.connect("clicked", lambda _b: self.run_helper(self.pkexec_cmd("ming-network-repair", "--use-iwd"), "网络修复"))
        scan = Gtk.Button(label="查看驱动检测")
        scan.connect("clicked", lambda _b: self.run_helper(["ming-driver-diagnose"], "驱动检测"))
        net_grp.add(self.button_row("无线网络修复", "解除 rfkill、重启网络服务、显示缺失固件提示。", wpa))
        net_grp.add(self.button_row("无线后端切换", "少数新机器可尝试 iwd；老机器建议保持推荐模式。", iwd))
        net_grp.add(self.button_row("驱动检测", "查看显卡、声卡、无线网卡和缺失 firmware 线索。", scan))

        broadcom_grp = Adw.PreferencesGroup(
            title="Broadcom 无线兼容",
            description="默认使用内核开源驱动；仅在官方支持的设备没有无线接口时提供离线 STA 备选。")
        box.append(broadcom_grp)
        self.broadcom_row = Adw.ActionRow(title="Broadcom 无线驱动")
        self.broadcom_button = Gtk.Button()
        self.broadcom_button.set_valign(Gtk.Align.CENTER)
        self.broadcom_button.connect("clicked", self.on_broadcom_action)
        self.broadcom_row.add_suffix(self.broadcom_button)
        broadcom_grp.add(self.broadcom_row)
        self.broadcom_row.set_subtitle("正在后台检测设备与驱动状态...")
        self.broadcom_button.set_visible(False)

        print_grp = Adw.PreferencesGroup(title="打印机与扫描仪", description="支持 USB 打印、局域网 IPP/AirPrint、常见打印机驱动和基础扫描。")
        box.append(print_grp)
        printer = Gtk.Button(label="打开打印机")
        printer.connect("clicked", self.open_printer_settings)
        scanner = Gtk.Button(label="打开扫描")
        scanner.connect("clicked", lambda _b: self.launch_first_available([["simple-scan"], ["document-scanner"]], "未找到扫描程序。"))
        print_grp.add(self.button_row("添加打印机", "打开图形化打印机管理器。", printer))
        print_grp.add(self.button_row("扫描文档", "打开扫描工具。", scanner))

        diag_grp = Adw.PreferencesGroup(title="诊断与可选增强", description="生成日志包、开启轻量模式或为 Surface 设备安装专用支持。")
        box.append(diag_grp)
        bundle = Gtk.Button(label="生成诊断包")
        bundle.connect("clicked", lambda _b: self.run_helper(["ming-diagnostic-bundle"], "问题诊断"))
        classic = Gtk.Button(label="切换经典轻量模式")
        classic.connect("clicked", lambda _b: self.run_helper(["ming-classic-mode"], "经典轻量模式"))
        disk_health = Gtk.Button(label="检查磁盘健康")
        disk_health.connect(
            "clicked",
            lambda _b: self.run_helper(
                self.pkexec_cmd("/usr/local/bin/ming-disk-health"), "磁盘健康检查"))
        surface = Gtk.Button(label="安装 Surface 支持")
        surface.connect("clicked", lambda _b: self.run_helper(self.pkexec_cmd("ming-surface-support"), "Surface 支持"))
        diag_grp.add(self.button_row("问题诊断包", "把安装器、网络、驱动和启动日志打包到桌面。", bundle))
        diag_grp.add(self.button_row("经典轻量模式", "关闭模糊和重动画，更适合机械硬盘与老 CPU。", classic))
        diag_grp.add(self.button_row("磁盘健康", "按需读取 SATA、SAS 和 NVMe 磁盘的 SMART 状态，不开启常驻监控。", disk_health))
        diag_grp.add(self.button_row("Surface 支持", "仅 Surface 设备需要；会添加 linux-surface 第三方源。", surface))

        raw_grp = Adw.PreferencesGroup(
            title="原始诊断",
            description="仅在排障或提交支持信息时导出、复制原始证据；默认不会显示在设置页面。")
        box.append(raw_grp)
        export_raw = Gtk.Button(label="导出原始诊断")
        export_raw.connect("clicked", lambda _button: self.export_hardware_diagnostics(copy_only=False))
        copy_raw = Gtk.Button(label="复制原始诊断")
        copy_raw.connect("clicked", lambda _button: self.export_hardware_diagnostics(copy_only=True))
        raw_grp.add(self.button_row("导出原始诊断", "保存结构化诊断到桌面，便于发送给支持人员。", export_raw))
        raw_grp.add(self.button_row("复制原始诊断", "复制结构化诊断到剪贴板；页面本身不展示原文。", copy_raw))
        self.hardware_page = sc
        self.refresh_hardware_status()
        return sc

    def refresh_hardware_status(self):
        generation = self.hardware_probe_state.begin()
        self.hardware_refresh_button.set_sensitive(False)
        self.hardware_refresh_button.set_label("正在检测...")

        def done(snapshot, error):
            if not self.hardware_probe_state.accept(generation):
                return False
            if self.hardware_page.get_root() is not self:
                return False
            self.hardware_refresh_button.set_sensitive(True)
            self.hardware_refresh_button.set_label("刷新硬件信息")
            snapshot = snapshot or {"ok": False, "error": error or "未知错误", "devices": {}}
            broadcom = snapshot.get("broadcom") or {
                "action": "error", "error": "未返回 Broadcom 驱动状态。"}
            if not snapshot.get("ok"):
                message = "硬件状态读取失败：%s" % (snapshot.get("error") or "未知错误")
                self.hardware_summary_row.set_title("硬件状态读取失败")
                self.hardware_summary_row.set_subtitle(message)
                for title, row in (("显卡", self.hardware_graphics_row),
                                   ("声卡", self.hardware_audio_row),
                                   ("网络", self.hardware_network_row)):
                    row.set_title("%s — 失败" % title)
                    row.set_subtitle(message)
                self.apply_broadcom_status(broadcom)
                return False
            devices = snapshot["devices"]
            states = {"normal": "正常", "attention": "注意", "failure": "失败"}
            rows = (("graphics", "显卡", self.hardware_graphics_row),
                    ("audio", "声卡", self.hardware_audio_row),
                    ("network", "网络", self.hardware_network_row))
            for key, title, row in rows:
                card = devices.get(key) or {}
                state = states.get(card.get("state"), "注意")
                row.set_title("%s — %s" % (title, state))
                row.set_subtitle("型号：%s · 驱动：%s · 建议：%s" % (
                    card.get("model") or "未知", card.get("driver") or "未知",
                    card.get("recommendation") or "请导出原始诊断以继续排查。"))
            self.hardware_summary_row.set_title("硬件状态已更新")
            self.hardware_summary_row.set_subtitle("显卡、声卡与网络卡片均已读取；原始证据仅在导出或复制时提供。")
            self.apply_broadcom_status(broadcom)
            return False

        run_task_async(hardware_status_snapshot, done)

    def export_hardware_diagnostics(self, copy_only):
        def done(snapshot, error):
            snapshot = snapshot or {"ok": False, "error": error or "无法导出硬件诊断。", "content": ""}
            if not snapshot.get("ok"):
                self.toast("导出原始诊断失败：%s" % snapshot.get("error"), "error")
                return False
            content = snapshot["content"]
            if copy_only:
                display = Gdk.Display.get_default()
                if not display:
                    self.toast("无法访问剪贴板；请使用“导出原始诊断”。", "warning")
                    return False
                display.get_clipboard().set_text(content)
                self.toast("原始诊断已复制到剪贴板。", "info")
                return False
            desktop = os.path.join(HOME, "Desktop")
            target_dir = desktop if os.path.isdir(desktop) else HOME
            target = os.path.join(target_dir, "ming-hardware-diagnostic.json")
            try:
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write(content)
            except OSError as exc:
                self.toast("导出原始诊断失败：%s" % exc, "error")
                return False
            self.toast("原始诊断已导出到 %s。" % target, "info")
            return False

        run_task_async(hardware_export_snapshot, done)

    def read_broadcom_status(self):
        return read_broadcom_status_snapshot()

    def refresh_broadcom_status(self):
        self.refresh_hardware_status()

    def apply_broadcom_status(self, status):
        action = status.get("action", "error")
        model = status.get("model") or "未检测到 Broadcom 无线设备"
        module = status.get("active_module") or "none"
        pci_id = status.get("pci_id") or "未知"
        self.broadcom_action = None
        self.broadcom_row.set_title(model)
        self.broadcom_button.set_visible(False)
        self.broadcom_button.set_sensitive(False)
        self.broadcom_button.remove_css_class("suggested-action")

        if action == "install":
            self.broadcom_row.set_subtitle(
                "设备 ID %s，当前驱动 %s；未发现无线接口，可安装 ISO 内的离线兼容驱动。" % (pci_id, module))
            self.broadcom_button.set_label("安装 Broadcom 兼容驱动")
            self.broadcom_button.set_visible(True)
            self.broadcom_button.set_sensitive(True)
            self.broadcom_button.add_css_class("suggested-action")
            self.broadcom_action = "install"
        elif action == "restore":
            self.broadcom_row.set_subtitle(
                "当前已安装 Broadcom STA。恢复后将重新使用 Linux 内核开源驱动。")
            self.broadcom_button.set_label("恢复开源驱动")
            self.broadcom_button.set_visible(True)
            self.broadcom_button.set_sensitive(True)
            self.broadcom_button.remove_css_class("suggested-action")
            self.broadcom_action = "restore"
        elif action == "blocked_secure_boot":
            self.broadcom_row.set_subtitle(
                "设备受 STA 支持，但 Secure Boot 已开启或状态未知，不能加载未注册 MOK 的 DKMS 模块。")
            self.broadcom_button.set_label("Secure Boot 阻止安装")
            self.broadcom_button.set_visible(True)
            self.broadcom_button.set_sensitive(False)
        elif action == "none" and status.get("detected"):
            self.broadcom_row.set_subtitle(
                "无线接口工作正常，当前驱动 %s，无需更改。" % module)
        elif action == "unsupported" and status.get("detected"):
            self.broadcom_row.set_subtitle(
                "设备 ID %s 不在 Debian STA 推荐列表中，继续使用内核开源驱动。" % pci_id)
        elif action == "error":
            self.broadcom_row.set_subtitle(
                "暂时无法读取驱动状态：%s" % status.get("error", "未知错误"))
        else:
            self.broadcom_row.set_subtitle("没有需要处理的 Broadcom 无线设备。")

    def on_broadcom_action(self, _button):
        action = self.broadcom_action
        if action not in ("install", "restore"):
            return
        installing = action == "install"
        heading = "安装 Broadcom 兼容驱动？" if installing else "恢复开源驱动？"
        body = (
            "系统将校验 ISO 内的 Debian 驱动包，构建 wl 模块并更新 initramfs。"
            "当前网络模块不会被强制卸载，完成后需要重启。"
            if installing else
            "系统将卸载 Broadcom STA、移除其黑名单并重建 initramfs。完成后需要重启。"
        )
        dlg = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dlg.add_response("cancel", "取消")
        dlg.add_response("apply", "确认执行")
        dlg.set_default_response("cancel")
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dialog, response):
            if response != "apply":
                return
            operation_generation = self.hardware_probe_state.begin()
            self.broadcom_button.set_sensitive(False)
            cmd = self.pkexec_cmd("/usr/local/sbin/ming-broadcom-driver", action)

            def done(rc):
                if not self.hardware_probe_state.accept(operation_generation):
                    return False
                if self.hardware_page.get_root() is not self:
                    return False
                self.refresh_broadcom_status()
                if rc == 0:
                    self.toast("驱动操作完成，请重启电脑。日志：/var/log/ming-broadcom-driver.log")
                else:
                    self.toast("驱动操作未成功，系统已保留或恢复原配置。日志：/var/log/ming-broadcom-driver.log")
                return False

            run_async(cmd, on_done=done)

        dlg.connect("response", on_response)
        dlg.present()

    def button_row(self, title, subtitle, button):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        button.set_valign(Gtk.Align.CENTER)
        row.add_suffix(button)
        return row

    def run_helper(self, cmd, title):
        self.toast("%s 已开始，完成后会显示结果或日志位置。" % title)
        run_async(cmd, on_done=lambda rc: self.toast("%s 已完成。" % title if rc == 0 else "%s 未成功完成，请查看弹出的日志或生成诊断包。" % title))

    def pkexec_cmd(self, *args):
        display = os.environ.get("DISPLAY", ":0")
        xauthority = os.environ.get("XAUTHORITY", os.path.join(HOME, ".Xauthority"))
        argv = list(args)
        if argv and "/" not in argv[0]:
            argv[0] = "/usr/local/bin/" + argv[0]
        return ["pkexec", "env", "DISPLAY=%s" % display, "XAUTHORITY=%s" % xauthority] + argv

    def launch_first_available(self, candidates, missing_text):
        for cmd in candidates:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                return
        self.toast(missing_text)

    def open_printer_settings(self, _btn):
        candidates = [["system-config-printer"]]
        debian_gui = "/usr/share/system-config-printer/system-config-printer.py"
        if os.path.exists(debian_gui):
            candidates.append([debian_gui])
        candidates.append(["xdg-open", "http://localhost:631"])
        self.launch_first_available(candidates, "未找到打印机管理器。")

    # ---- 6. 一键还原系统（timeshift 回滚出厂快照） ----
    def build_restore(self):
        sc, box = self.page_scroller()
        grp = Adw.PreferencesGroup(title="系统还原",
                                   description="把系统恢复到出厂初始状态。个人文件（主目录）不受影响。")
        box.append(grp)

        info = Gtk.Label(
            label="Ming OS 在首次开机时自动创建了一个“出厂初始”系统快照。\n"
                  "如果系统变得不稳定或被误改，可一键回到那个干净状态。",
            xalign=0, wrap=True)
        info.set_margin_top(4)
        grp.add(info)

        reset_btn = Gtk.Button(label="恢复出厂设置")
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_margin_top(16)
        reset_btn.connect("clicked", self.on_factory_reset)
        box.append(reset_btn)

        self.restore_status = Gtk.Label(label="", xalign=0, wrap=True)
        self.restore_status.set_margin_top(10)
        box.append(self.restore_status)
        return sc

    def on_factory_reset(self, _btn):
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading="确认恢复出厂设置？",
            body="系统将回滚到出厂初始快照并自动重启。\n"
                 "已安装的软件和系统改动将被撤销，但您的个人文件会保留。\n\n此操作不可撤销。")
        dlg.add_response("cancel", "取消")
        dlg.add_response("reset", "确认恢复并重启")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", self.on_factory_reset_confirm)
        dlg.present()

    def on_factory_reset_confirm(self, _dlg, resp):
        if resp != "reset":
            return
        self.restore_status.set_label("正在准备回滚出厂快照…")
        # 找到 ming-factory 标签的快照并回滚（rsync 模式回滚后需重启）
        def line(l): self.restore_status.set_label(l)
        def done(rc):
            if rc == 0:
                self.restore_status.set_label("回滚完成，系统即将重启。")
                run(["pkexec", "systemctl", "reboot"])
            else:
                self.restore_status.set_label("回滚失败：未找到出厂快照或权限不足。")
        # timeshift 选择最早的 O(nboot/factory) 快照名
        run_async(["pkexec", "bash", "-c",
                   "snap=$(timeshift --list | awk '/ming-factory|O /{print $3; exit}'); "
                   "[ -n \"$snap\" ] && timeshift --restore --snapshot \"$snap\" --yes "
                   "|| timeshift --restore --yes"],
                  on_line=line, on_done=done)

    # __PAGE_BUILDERS__


class MingSettingsApp(Adw.Application):
    def __init__(self, initial_page=None):
        super().__init__(application_id="uno.scallion.MingSettings")
        self.initial_page = initial_page

    def do_activate(self):
        win = MingSettings(self, self.initial_page)
        win.present()


if __name__ == "__main__":
    initial_page = None
    argv = list(sys.argv[1:])
    if "--page" in argv:
        index = argv.index("--page")
        if index + 1 < len(argv):
            initial_page = argv[index + 1]
    Adw.init()
    app = MingSettingsApp(initial_page)
    app.run([sys.argv[0]])
