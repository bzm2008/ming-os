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
import threading
import shutil

USER = getpass.getuser()
HOME = os.path.expanduser("~")


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


class MingSettings(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ming 设置")
        self.set_default_size(1000, 700)
        self.add_css_class("ming-settings-window")
        self.install_css()

        # Adw.NavigationSplitView：左导航 + 右内容（Android 风格单窗口）
        self.split = Adw.NavigationSplitView()
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
        self.nav_list.set_size_request(232, -1)
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
            page_widget = builder()
            self.content_stack.add_named(page_widget, title)

        self.nav_list.select_row(self.nav_list.get_row_at_index(0))

    def install_css(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        css = b"""
        window.ming-settings-window {
            background: #F6F8F5;
            color: #1C2320;
        }

        .ming-settings-sidebar {
            background: alpha(#EFF4EF, 0.96);
            border-right: 1px solid alpha(#215E52, 0.06);
        }

        .ming-settings-content {
            background: linear-gradient(to bottom, #F8FAF7, #F3F6F2);
        }

        .ming-settings-window headerbar {
            background: alpha(#FFFFFF, 0.90);
            border-bottom: 1px solid alpha(#215E52, 0.06);
            min-height: 44px;
        }

        .ming-settings-window list.navigation-sidebar {
            background: transparent;
            margin: 10px;
        }

        .ming-settings-window row.ming-nav-row {
            border-radius: 10px;
            margin: 3px 0;
            min-height: 44px;
        }

        .ming-settings-window row.ming-nav-row:hover {
            background: alpha(#2F8A7D, 0.08);
        }

        .ming-settings-window row.ming-nav-row:selected {
            background: alpha(#2F8A7D, 0.12);
            color: #1C2320;
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
            background: alpha(#FFFFFF, 0.92);
            border-radius: 12px;
            border: 1px solid alpha(#215E52, 0.06);
            padding: 2px;
        }

        .ming-settings-window button {
            border-radius: 9px;
            min-height: 38px;
            padding-left: 15px;
            padding-right: 15px;
        }

        .ming-settings-window button.suggested-action {
            background: #2F8A7D;
            color: #FFFFFF;
        }

        .ming-settings-window button.suggested-action:hover {
            background: #24796E;
        }

        .ming-settings-window entry,
        .ming-settings-window passwordentry {
            border-radius: 9px;
        }

        .ming-settings-window progressbar trough {
            min-height: 8px;
            border-radius: 999px;
            background: alpha(#1B5A4B, 0.08);
        }

        .ming-settings-window progressbar progress {
            border-radius: 999px;
            background: #2F8A7D;
        }

        .ming-settings-window label.dim-label {
            color: alpha(#21302A, 0.68);
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
        self.content_stack.set_visible_child_name(title)
        self.content_title.set_title(title)

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

    def toast(self, text):
        # 简化：用 message dialog 反馈关键结果
        dlg = Adw.MessageDialog(transient_for=self, heading="提示", body=text)
        dlg.add_response("ok", "好的")
        dlg.present()

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

        # WLAN 开关
        wifi_grp = Adw.PreferencesGroup(title="无线网络 (WLAN)")
        self.wifi_switch = Adw.SwitchRow(title="启用 WLAN")
        rc, out, _ = run(["nmcli", "radio", "wifi"])
        self.wifi_switch.set_active(out.strip() == "enabled")
        self.wifi_switch.connect("notify::active", self.on_wifi_toggle)
        wifi_grp.add(self.wifi_switch)
        scan_btn = Gtk.Button(label="扫描并显示可用网络")
        scan_btn.set_margin_top(8)
        scan_btn.connect("clicked", self.on_wifi_scan)
        wifi_grp.add(scan_btn)
        box.append(wifi_grp)

        self.wifi_list_grp = Adw.PreferencesGroup(title="可用网络")
        box.append(self.wifi_list_grp)

        # 蓝牙开关
        bt_grp = Adw.PreferencesGroup(title="蓝牙")
        self.bt_switch = Adw.SwitchRow(title="启用蓝牙")
        rc, out, _ = run(["bluetoothctl", "show"])
        self.bt_switch.set_active("Powered: yes" in out)
        self.bt_switch.connect("notify::active", self.on_bt_toggle)
        bt_grp.add(self.bt_switch)
        open_blueman = Gtk.Button(label="打开蓝牙设备管理器")
        open_blueman.set_margin_top(8)
        open_blueman.connect("clicked", lambda _b: subprocess.Popen(["blueman-manager"]))
        bt_grp.add(open_blueman)
        box.append(bt_grp)
        return sc

    def on_wifi_toggle(self, sw, _p):
        state = "on" if sw.get_active() else "off"
        run(["nmcli", "radio", "wifi", state])

    def on_bt_toggle(self, sw, _p):
        state = "on" if sw.get_active() else "off"
        run(["bluetoothctl", "power", state])

    def on_wifi_scan(self, _btn):
        # 清空旧列表
        child = self.wifi_list_grp.get_first_child()
        run(["nmcli", "dev", "wifi", "rescan"], timeout=8)
        rc, out, _ = run(["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi", "list"], timeout=12)
        seen = set()
        rows = []
        for line in out.splitlines():
            parts = line.split(":")
            ssid = parts[0].strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            signal = parts[1] if len(parts) > 1 else "?"
            rows.append((ssid, signal))
        # 重建分组
        new_grp = Adw.PreferencesGroup(title="可用网络 (%d)" % len(rows))
        for ssid, signal in rows[:20]:
            r = Adw.ActionRow(title=ssid, subtitle="信号 %s%%" % signal)
            conn = Gtk.Button(label="连接")
            conn.set_valign(Gtk.Align.CENTER)
            conn.connect("clicked", self.on_wifi_connect, ssid)
            r.add_suffix(conn)
            new_grp.add(r)
        # 替换
        parent = self.wifi_list_grp.get_parent()
        parent.remove(self.wifi_list_grp)
        parent.append(new_grp)
        self.wifi_list_grp = new_grp

    def on_wifi_connect(self, _btn, ssid):
        dlg = Adw.MessageDialog(transient_for=self, heading="连接到 %s" % ssid,
                               body="请输入 Wi-Fi 密码（开放网络可留空）：")
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "取消")
        dlg.add_response("ok", "连接")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        def on_resp(d, resp):
            if resp == "ok":
                pw = entry.get_text()
                cmd = ["nmcli", "dev", "wifi", "connect", ssid]
                if pw:
                    cmd += ["password", pw]
                rc, o, e = run(cmd, timeout=30)
                self.toast("已连接 %s" % ssid if rc == 0 else "连接失败：%s" % (e or o))
        dlg.connect("response", on_resp)
        dlg.present()

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
        grp = Adw.PreferencesGroup(title="系统更新", description="当前版本：Ming OS %s" % cur)
        box.append(grp)

        self.update_status = Gtk.Label(label="点击下方按钮检查更新。", xalign=0, wrap=True)
        self.update_status.set_margin_top(6)
        grp.add(self.update_status)

        self.update_bar = Gtk.ProgressBar()
        self.update_bar.set_show_text(True)
        self.update_bar.set_visible(False)
        self.update_bar.set_margin_top(10)
        grp.add(self.update_bar)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_margin_top(12)
        check = Gtk.Button(label="检查更新")
        check.add_css_class("suggested-action")
        check.connect("clicked", self.on_update_check)
        oneclick = Gtk.Button(label="一键更新")
        oneclick.connect("clicked", self.on_update_oneclick)
        btn_box.append(check); btn_box.append(oneclick)
        grp.add(btn_box)
        return sc

    def on_update_check(self, _btn):
        self.update_status.set_label("正在检查更新…")
        def line(l): self.update_status.set_label(l)
        def done(rc):
            self.update_status.set_label("检查完成。" if rc == 0 else "未发现可用更新或检查失败。")
        run_async(["ming-update", "check"], on_line=line, on_done=done)

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

    # ---- 5. 显示与无障碍（字体 + 图标等比缩放） ----
    def build_display(self):
        sc, box = self.page_scroller()
        grp = Adw.PreferencesGroup(title="适老化缩放",
                                   description="拖动滑块同时放大系统字体与桌面/Dock 图标，立即生效。")
        box.append(grp)

        # 当前字体大小（xsettings Gtk/FontName 形如 "Sans 11"）
        rc, out, _ = run(["xfconf-query", "-c", "xsettings", "-p", "/Gtk/FontName"])
        cur_size = 11
        try:
            cur_size = int(out.strip().split()[-1])
        except Exception:
            pass

        row = Adw.ActionRow(title="界面缩放", subtitle="字体与图标等比例放大")
        self.scale_adj = Gtk.Adjustment(value=cur_size, lower=9, upper=20, step_increment=1)
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.scale_adj)
        slider.set_digits(0)
        slider.set_draw_value(True)
        slider.set_size_request(280, -1)
        slider.set_hexpand(True)
        for mark in (9, 11, 14, 17, 20):
            slider.add_mark(mark, Gtk.PositionType.BOTTOM, str(mark))
        slider.connect("value-changed", self.on_scale_changed)
        row.add_suffix(slider)
        grp.add(row)

        hint = Gtk.Label(
            label="提示：9≈紧凑，11≈标准，14 以上适合老人与触屏。",
            xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        hint.set_margin_top(8)
        box.append(hint)
        return sc

    def on_scale_changed(self, slider):
        size = int(slider.get_value())
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
                run(["bash", "-c", "pkill plank; (sleep 1 && nohup plank >/dev/null 2>&1 &)"])
            except Exception:
                pass
        # 4) GSettings 文本缩放（GTK4 应用自身也放大）
        factor = size / 11.0
        run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor",
             "%.2f" % factor])

    # ---- 6. 硬件与诊断：老电脑网络、驱动、打印、诊断包 ----
    def build_hardware(self):
        sc, box = self.page_scroller()

        summary = Adw.PreferencesGroup(
            title="老电脑兼容",
            description="面向一代/二代/三代 i3、i5、E3 V1/V2 等老 64 位电脑。所有操作都封装为按钮，不需要输入命令。")
        box.append(summary)

        rc, cpu, _ = run(["bash", "-lc", "lscpu | sed -n '1,8p'"], timeout=8)
        rc2, flags, _ = run(["bash", "-lc", "lscpu | awk -F: '/Flags|标志/ {print $2; exit}'"], timeout=8)
        avx2 = "未检测到 AVX2，Ming OS r4 会按老 CPU 兼容路径运行。"
        if " avx2 " in (" " + flags + " "):
            avx2 = "检测到 AVX2；系统仍按 Debian amd64 基线运行。"
        cpu_row = Adw.ActionRow(title="CPU 兼容状态", subtitle=(cpu.splitlines()[0] if cpu else avx2))
        cpu_row.add_suffix(Gtk.Label(label=avx2))
        summary.add(cpu_row)

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
        surface = Gtk.Button(label="安装 Surface 支持")
        surface.connect("clicked", lambda _b: self.run_helper(self.pkexec_cmd("ming-surface-support"), "Surface 支持"))
        diag_grp.add(self.button_row("问题诊断包", "把安装器、网络、驱动和启动日志打包到桌面。", bundle))
        diag_grp.add(self.button_row("经典轻量模式", "关闭模糊和重动画，更适合机械硬盘与老 CPU。", classic))
        diag_grp.add(self.button_row("Surface 支持", "仅 Surface 设备需要；会添加 linux-surface 第三方源。", surface))
        return sc

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
    def __init__(self):
        super().__init__(application_id="uno.scallion.MingSettings")

    def do_activate(self):
        win = MingSettings(self)
        win.present()


if __name__ == "__main__":
    Adw.init()
    app = MingSettingsApp()
    app.run(None)
