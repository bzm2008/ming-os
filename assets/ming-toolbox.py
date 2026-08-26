#!/usr/bin/env python3
"""Ming Toolbox: official software, stable tools, and per-app experiments."""

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys


SECTIONS = ("official", "toolbox", "lab")
SECTION_LABELS = {
    "official": "官方软件",
    "toolbox": "工具箱",
    "lab": "Ming 实验室",
}
LAB_ACTIONS = {
    "dxvk": "为当前应用启用 DXVK 图形兼容",
    "proton": "为当前应用测试 Proton 兼容层",
    "game_mode": "为当前应用启用实验性游戏模式",
    "verbose_logs": "为当前应用启用详细诊断日志",
}
ANDROID_LAB_ACTIONS = {
    "arm_translation": "为当前 Android 应用测试 ARM 转译",
    "gpu_software_render": "为当前 Android 应用测试软件渲染",
    "window_compat": "为当前 Android 应用启用窗口兼容模式",
    "clipboard_share": "允许当前 Android 应用共享剪贴板",
    "directory_share": "允许当前 Android 应用访问指定目录",
    "debug_logs": "为当前 Android 应用启用调试日志",
}


class LabStateStore:
    """Persist experimental flags per application, never as global defaults."""

    def __init__(self, root=None):
        self.root = pathlib.Path(root or pathlib.Path.home() / ".local/share/ming-wine/lab")

    def _path(self, app_id):
        safe = str(app_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", safe):
            raise ValueError("应用 ID 无效。")
        return self.root / (safe + ".json")

    def get(self, app_id):
        try:
            data = json.loads(self._path(app_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        state = default_lab_state()
        if isinstance(data, dict):
            for key in LAB_ACTIONS:
                state[key] = bool(data.get(key, False))
        return state

    def set(self, app_id, key, enabled):
        if key not in LAB_ACTIONS:
            raise ValueError("实验室选项不在白名单内。")
        state = self.get(app_id)
        state[key] = bool(enabled)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self._path(app_id).with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._path(app_id))
        return state


def official_catalog():
    """Reserved for signed Ming software; empty until a catalog is published."""
    return []


def toolbox_actions():
    return {
        "install_windows_app": "安装 Windows EXE/MSI",
        "runtime_status": "检查 Wine 运行环境",
        "enable_wine32": "按需启用 32 位兼容",
        "install_fonts": "安装 Windows 常用字体",
        "manage_prefixes": "管理独立 Wine 环境",
        "repair_desktop_entries": "修复桌面、Dock 与应用目录",
        "diagnostics": "生成脱敏诊断报告",
        "android_runtime_status": "检查 Android/Waydroid 运行环境",
        "install_android_apk": "导入本地 Android APK",
        "manage_android_apps": "管理 Android 应用",
        "repair_android_runtime": "修复 Android 运行环境",
    }


def default_android_lab_state():
    return {
        "scope": "application",
        **{key: False for key in ANDROID_LAB_ACTIONS},
        "software_rendering": False,
    }


# Uninstall failures expose residual files (残留) instead of claiming success.


def default_lab_state():
    return {
        "scope": "application",
        "dxvk": False,
        "proton": False,
        "game_mode": False,
        "verbose_logs": False,
    }


def _load_wine_module():
    candidates = (
        pathlib.Path(__file__).with_name("ming-wine-installer.py"),
        pathlib.Path("/usr/local/lib/ming-os/ming-wine-installer.py"),
        pathlib.Path("/usr/local/bin/ming-wine-installer"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_wine_installer_for_toolbox", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Wine 管理组件缺失，请重新安装 Ming 工具箱。")


def _load_android_module():
    candidates = (
        pathlib.Path(__file__).with_name("ming-android-runtime.py"),
        pathlib.Path("/usr/local/lib/ming-os/ming-android-runtime.py"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_android_runtime_for_toolbox", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Android 管理组件缺失，请重新安装 Ming 工具箱。")


class ToolboxController:
    def __init__(self, home=None, runner=None):
        self.home = pathlib.Path(home or pathlib.Path.home())
        self.runner = runner or self._run

    @staticmethod
    def _run(command, timeout=60):
        completed = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout,
            check=False, shell=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def runtime_status(self):
        return _load_wine_module().WineInstaller(home=self.home).detect_runtime()

    def install_windows(self, source):
        return _load_wine_module().WineInstaller(home=self.home).install(source)

    def list_windows_apps(self):
        return _load_wine_module().WineInstaller(home=self.home).list_apps()

    def uninstall_windows_app(self, app_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", str(app_id)):
            return {"ok": False, "state": "invalid_app_id", "error": "应用 ID 无效。"}
        try:
            rc, output, error = self.runner(
                ("/usr/local/bin/ming-wine-installer", "uninstall", str(app_id)),
                timeout=900,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "state": "uninstall_failed", "error": str(exc)}
        try:
            result = json.loads(output or "")
        except ValueError:
            result = {}
        if rc != 0 or not result.get("ok"):
            return {
                "ok": False, "state": result.get("state", "uninstall_failed"),
                "error": result.get("error") or error or "卸载失败。",
            }
        return result

    def launch_windows_app(self, item):
        desktop = pathlib.Path(str(item.get("desktop_file") or ""))
        allowed = self.home / ".local/share/applications"
        try:
            resolved = desktop.resolve(strict=True)
            allowed = allowed.resolve(strict=True)
        except OSError as exc:
            return {"ok": False, "state": "launcher_missing", "error": str(exc)}
        if resolved.parent != allowed:
            return {"ok": False, "state": "launcher_invalid", "error": "应用入口不在受信任目录中。"}
        try:
            subprocess.Popen(
                ["/usr/local/bin/ming-launch", "--desktop-file", str(resolved), "--source", "settings"],
                shell=False,
            )
        except OSError as exc:
            return {"ok": False, "state": "launch_failed", "error": str(exc)}
        return {"ok": True, "state": "launch_requested"}

    def authorized_wine_action(self, action):
        try:
            completed = subprocess.run(
                ["/usr/local/bin/ming-authorized-action", "wine", action],
                capture_output=True, text=True, timeout=900, check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "state": "action_failed", "error": str(exc)}
        return {
            "ok": completed.returncode == 0,
            "state": "completed" if completed.returncode == 0 else "action_failed",
            "detail": (completed.stdout or completed.stderr or "").strip()[:1000],
        }

    def authorized_android_action(self, action):
        try:
            completed = subprocess.run(
                ["/usr/local/bin/ming-authorized-action", "android", action],
                capture_output=True, text=True, timeout=900, check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "state": "action_failed", "error": str(exc)}
        return {
            "ok": completed.returncode == 0,
            "state": "completed" if completed.returncode == 0 else "action_failed",
            "detail": (completed.stdout or completed.stderr or "").strip()[:1000],
        }

    def system_check(self):
        checks = {
            "network": ("nmcli", "-t", "-f", "STATE", "general"),
            "disk": ("df", "-Pk", str(self.home)),
            "authorization": ("pgrep", "-u", str(os.getuid()), "-f", "lxpolkit|polkit-gnome"),
        }
        result = {}
        for name, command in checks.items():
            try:
                rc, output, error = self.runner(command, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                rc, output, error = 1, "", str(exc)
            result[name] = {
                "ok": rc == 0,
                "detail": (output or error or "").strip()[:500],
            }
        runtime = self.runtime_status()
        result["wine"] = {
            "ok": bool(runtime.get("ok")),
            "detail": str(runtime.get("version") or runtime.get("error") or ""),
        }
        result["wine_apps"] = {
            "ok": True,
            "detail": json.dumps(self.list_windows_apps(), ensure_ascii=False)[:1000],
        }
        return result

    def android_runtime_status(self):
        return _load_android_module().AndroidRuntime(home=self.home).status()

    def install_android_apk(self, source):
        runtime = _load_android_module().AndroidRuntime(home=self.home)
        status = runtime.status()
        if not status.get("ok"):
            return {"ok": False, "state": "unavailable", "error": "Android 当前不可用：" + "、".join(status.get("reasons", []))}
        return runtime.install_apk(source)

    def list_android_apps(self):
        module = _load_android_module()
        root = self.home / ".local/share/ming-android/apps"
        result = []
        for metadata in sorted(root.glob("*/metadata.json")):
            try:
                item = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def uninstall_android_app(self, package):
        return _load_android_module().AndroidRuntime(home=self.home).uninstall(package)

    def launch_android_app(self, package):
        return _load_android_module().AndroidRuntime(home=self.home).launch(package)


def _show_dialog(title, message, error=False):
    command = ["zenity", "--error" if error else "--info", "--title", title, "--text", message, "--width", "480"]
    try:
        subprocess.run(command, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        print("%s: %s" % (title, message), file=sys.stderr if error else sys.stdout)


def _gtk_main(section="toolbox", install_file=""):
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, Gtk
    except (ImportError, ValueError):
        _show_dialog("Ming 工具箱", "图形组件暂不可用，请稍后重试。", error=True)
        return 1

    controller = ToolboxController()

    class ToolboxWindow(Adw.ApplicationWindow):
        def __init__(self, application):
            super().__init__(application=application)
            self.set_title("Ming 工具箱")
            self.set_default_size(820, 600)
            self.home = controller.home
            tabs = Adw.ViewStack()
            switcher = Adw.ViewSwitcher()
            switcher.set_stack(tabs)
            header = Adw.HeaderBar()
            header.set_title_widget(switcher)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(header)
            toolbar.set_content(tabs)
            self.set_content(toolbar)
            self.lab_store = LabStateStore()
            installed_apps = controller.list_windows_apps()
            self.lab_app_id = installed_apps[0].get("app_id", "") if installed_apps else ""
            android_apps = controller.list_android_apps()
            self.android_app_id = android_apps[0].get("package", "") if android_apps else ""
            self.android_lab_store = _load_android_module().AndroidLabStateStore(
                self.home / ".local/share/ming-android/lab"
            )
            for key in SECTIONS:
                page = self._build_page(key)
                tabs.add_titled(page, key, SECTION_LABELS[key])
            tabs.set_visible_child_name(section if section in SECTIONS else "toolbox")
            if install_file:
                self._install_file(install_file)

        def _group(self, title, description=""):
            group = Adw.PreferencesGroup(title=title, description=description)
            return group

        def _row(self, title, subtitle="", action=None):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            if action is not None:
                button = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER)
                button.set_tooltip_text(title)
                button.connect("clicked", action)
                row.add_suffix(button)
                row.set_activatable_widget(button)
            return row

        def _build_page(self, key):
            page = Adw.PreferencesPage()
            if key == "official":
                group = self._group("官方软件", "当前尚未发布官方软件。这里不会显示未经验证的第三方条目。")
                group.add(self._row("暂未发布官方软件", "未来仅展示经过签名和校验的 Ming 官方软件。"))
                page.add(group)
            elif key == "toolbox":
                group = self._group("Windows 与 Wine", "稳定功能会读回真实状态，不会把进度结束当作安装成功。")
                group.add(self._row("安装 Windows 应用", "选择本地 .exe 或 .msi 文件", self._choose_windows_file))
                group.add(self._row("检查 Wine 运行环境", "检查 Wine 和 wineboot", self._check_runtime))
                group.add(self._row("按需启用 32 位兼容", "需要管理员授权和网络", self._enable_wine32))
                group.add(self._row("安装 Windows 常用字体", "仅安装公开字体包", self._install_fonts))
                group.add(self._row("查看独立 Wine 应用", "每个应用使用独立兼容环境", self._show_wine_apps))
                group.add(self._row("修复桌面入口", "刷新应用抽屉、Dock 和图标缓存", self._refresh_desktop))
                group.add(self._row("生成诊断报告", "只生成脱敏日志，不自动上传", self._diagnostics))
                page.add(group)
                android_group = self._group("Android 应用", "纯净 AOSP/Waydroid，按需启动；首版稳定支持 x86_64/universal APK。")
                android_group.add(self._row("检查 Android 运行环境", "检查内存、GPU、binderfs、LXC、DBus 和 Wayland", self._check_android_runtime))
                android_group.add(self._row("导入本地 APK", "只接受本地单 APK，不接受远程地址", self._choose_android_file))
                android_group.add(self._row("修复 Android 运行环境", "需要图形授权；不会开机常驻", self._repair_android_runtime))
                page.add(android_group)
                android_apps = self._group("已安装的 Android 应用", "每个应用有独立 metadata 和桌面入口。")
                apps = controller.list_android_apps()
                if not apps:
                    android_apps.add(self._row("暂无 Android 应用", "请先检查运行环境，再导入 APK。"))
                for item in apps:
                    package = str(item.get("package") or item.get("package_id") or "")
                    row = Adw.ActionRow(title=package, subtitle=str(item.get("state") or ""))
                    launch = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER)
                    launch.set_tooltip_text("启动 Android 应用")
                    launch.connect("clicked", lambda _button, value=package: self._launch_android(value))
                    remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                    remove.set_tooltip_text("卸载 Android 应用")
                    remove.connect("clicked", lambda _button, value=package: self._uninstall_android(value))
                    row.add_suffix(launch)
                    row.add_suffix(remove)
                    android_apps.add(row)
                page.add(android_apps)
                apps_group = self._group("已安装的 Windows 应用", "启动或卸载独立 Wine 环境。")
                installed_apps = controller.list_windows_apps()
                if not installed_apps:
                    apps_group.add(self._row("暂无 Windows 应用", "可从本页或文件管理器导入 .exe/.msi。"))
                for item in installed_apps:
                    row = Adw.ActionRow(
                        title=str(item.get("name") or item.get("app_id") or "Windows 应用"),
                        subtitle=str(item.get("state") or ""),
                    )
                    launch_button = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER)
                    launch_button.set_tooltip_text("启动")
                    launch_button.connect("clicked", self._launch_installed, item)
                    remove_button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                    remove_button.set_tooltip_text("卸载")
                    remove_button.add_css_class("destructive-action")
                    remove_button.connect("clicked", self._uninstall_installed, item)
                    row.add_suffix(launch_button)
                    row.add_suffix(remove_button)
                    apps_group.add(row)
                page.add(apps_group)
            else:
                group = self._group("Ming 实验室", "实验选项默认关闭，只对选定应用生效，可能增加磁盘占用或降低兼容性。")
                if not self.lab_app_id:
                    group.add(self._row("请先安装一个 Windows 应用", "实验室选项必须绑定到具体应用，不能修改全局 Wine 配置。"))
                current_state = self.lab_store.get(self.lab_app_id) if self.lab_app_id else default_lab_state()
                for key_name, title in LAB_ACTIONS.items():
                    row = Adw.SwitchRow(title=title, subtitle="默认关闭；启用前会再次确认。")
                    row.set_active(bool(current_state.get(key_name)))
                    row.set_sensitive(bool(self.lab_app_id))
                    row.connect("notify::active", self._lab_changed, key_name)
                    group.add(row)
                page.add(group)
                android_group = self._group("Android 实验选项", "默认关闭，只对当前 Android 应用生效；共享目录可能暴露文件。")
                if not self.android_app_id:
                    android_group.add(self._row("请先安装一个 Android 应用", "ARM 转译、软件渲染和共享功能不会修改全局配置。"))
                android_state = self.android_lab_store.get(self.android_app_id) if self.android_app_id else default_android_lab_state()
                for key_name, title in ANDROID_LAB_ACTIONS.items():
                    row = Adw.SwitchRow(title=title, subtitle="默认关闭；启用前需要确认风险。")
                    row.set_active(bool(android_state.get(key_name)))
                    row.set_sensitive(bool(self.android_app_id))
                    row.connect("notify::active", self._android_lab_changed, key_name)
                    android_group.add(row)
                page.add(android_group)
            return page

        def _lab_changed(self, row, _param, key_name):
            if not self.lab_app_id:
                row.set_active(False)
                return
            desired = row.get_active()
            if not self._confirm_lab_change(key_name, desired):
                row.set_active(not desired)
                return
            self.lab_store.set(self.lab_app_id, key_name, desired)

        def _android_lab_changed(self, row, _param, key_name):
            if not self.android_app_id:
                row.set_active(False)
                return
            desired = row.get_active()
            if not self._confirm_lab_change(key_name, desired):
                row.set_active(not desired)
                return
            self.android_lab_store.set(self.android_app_id, key_name, desired)

        @staticmethod
        def _confirm_lab_change(key_name, enabled):
            if not enabled:
                return True
            try:
                return subprocess.run(
                    ["zenity", "--question", "--title", "Ming 实验室",
                     "--text", "启用实验选项 %s 可能增加资源占用或降低安全性，是否继续？" % key_name,
                     "--ok-label", "继续", "--cancel-label", "取消", "--width", "500"],
                    timeout=30, check=False, shell=False,
                ).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False

        def _check_android_runtime(self, _button):
            result = controller.android_runtime_status()
            if result.get("ok"):
                message = "Android 运行环境已就绪。"
            else:
                message = "Android 当前不可用：" + "、".join(result.get("reasons", []))
            _show_dialog("Android 运行环境", message, error=not bool(result.get("ok")))

        def _choose_android_file(self, _button):
            dialog = Gtk.FileChooserNative(title="选择 Android APK", transient_for=self,
                                            action=Gtk.FileChooserAction.OPEN,
                                            accept_label="导入", cancel_label="取消")
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Android APK (*.apk)")
            file_filter.add_pattern("*.apk")
            file_filter.add_pattern("*.APK")
            dialog.add_filter(file_filter)
            dialog.connect("response", self._android_file_response)
            dialog.show()

        def _android_file_response(self, dialog, response):
            if response == Gtk.ResponseType.ACCEPT and dialog.get_file():
                result = controller.install_android_apk(dialog.get_file().get_path())
                _show_dialog("Android 应用", "安装完成。" if result.get("ok") else str(result.get("error") or "安装失败。"), error=not bool(result.get("ok")))
            dialog.destroy()

        def _repair_android_runtime(self, _button):
            result = controller.authorized_android_action("repair")
            _show_dialog("Android 运行环境", "已提交修复。" if result.get("ok") else result.get("detail", "修复失败。"), error=not bool(result.get("ok")))

        def _launch_android(self, package):
            result = controller.launch_android_app(package)
            if not result.get("ok"):
                _show_dialog("Android 应用", result.get("error", "启动失败。"), error=True)

        def _uninstall_android(self, package):
            result = controller.uninstall_android_app(package)
            _show_dialog("Android 应用", "卸载完成。" if result.get("ok") else result.get("error", "卸载失败。"), error=not bool(result.get("ok")))

        def _choose_windows_file(self, _button):
            dialog = Gtk.FileChooserNative(
                title="选择 Windows 安装文件", transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
                accept_label="安装", cancel_label="取消",
            )
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Windows 应用 (*.exe, *.msi)")
            file_filter.add_pattern("*.exe")
            file_filter.add_pattern("*.EXE")
            file_filter.add_pattern("*.msi")
            file_filter.add_pattern("*.MSI")
            dialog.add_filter(file_filter)
            dialog.connect("response", self._file_response)
            dialog.show()

        def _file_response(self, dialog, response):
            if response == Gtk.ResponseType.ACCEPT and dialog.get_file():
                self._install_file(dialog.get_file().get_path())
            dialog.destroy()

        def _install_file(self, path):
            result = controller.install_windows(path)
            _show_dialog(
                "Windows 应用安装",
                "安装完成，已创建独立兼容环境和应用入口。"
                if result.get("ok") else str(result.get("error") or "安装失败，请查看 Wine 日志。"),
                error=not bool(result.get("ok")),
            )

        def _check_runtime(self, _button):
            result = controller.runtime_status()
            _show_dialog(
                "Wine 运行环境",
                "Wine 已就绪：%s" % result.get("version", "")
                if result.get("ok") else str(result.get("error") or "Wine 运行环境未就绪。"),
                error=not bool(result.get("ok")),
            )

        def _enable_wine32(self, _button):
            result = controller.authorized_wine_action("enable-wine32")
            _show_dialog("Wine 32 位兼容", "已启用。" if result["ok"] else result["detail"], error=not result["ok"])

        def _install_fonts(self, _button):
            result = controller.authorized_wine_action("install-fonts")
            _show_dialog("Windows 字体", "字体已安装。" if result["ok"] else result["detail"], error=not result["ok"])

        def _show_wine_apps(self, _button):
            apps = controller.list_windows_apps()
            message = "暂无已安装的 Windows 应用。" if not apps else "\n".join(
                "%s：%s" % (item.get("name", item.get("app_id", "")), item.get("state", ""))
                for item in apps
            )
            _show_dialog("独立 Wine 应用", message)

        def _launch_installed(self, _button, item):
            result = controller.launch_windows_app(item)
            if not result.get("ok"):
                _show_dialog("无法启动", str(result.get("error") or "启动失败。"), error=True)

        def _uninstall_installed(self, _button, item):
            name = str(item.get("name") or item.get("app_id") or "该应用")
            try:
                confirmed = subprocess.run(
                    ["zenity", "--question", "--title", "卸载 Windows 应用",
                     "--text", "确定卸载 %s？只会删除它自己的 Wine 环境。" % name,
                     "--ok-label", "卸载", "--cancel-label", "取消", "--width", "460"],
                    timeout=60, check=False, shell=False,
                ).returncode == 0
            except (OSError, subprocess.SubprocessError):
                confirmed = False
            if not confirmed:
                return
            result = controller.uninstall_windows_app(str(item.get("app_id") or ""))
            _show_dialog(
                "卸载 Windows 应用",
                "卸载完成。" if result.get("ok") else str(result.get("error") or "卸载失败。"),
                error=not bool(result.get("ok")),
            )

        def _refresh_desktop(self, _button):
            try:
                result = subprocess.run(
                    ["/usr/local/bin/ming-refresh-desktop-state"],
                    capture_output=True, text=True, timeout=60, check=False, shell=False,
                )
                ok = result.returncode == 0
                _show_dialog("桌面入口", "刷新完成。" if ok else (result.stderr or "刷新失败。"), error=not ok)
            except (OSError, subprocess.SubprocessError) as exc:
                _show_dialog("桌面入口", str(exc), error=True)

        def _diagnostics(self, _button):
            _show_dialog("诊断报告", json.dumps(controller.system_check(), ensure_ascii=False, indent=2))

    application = Adw.Application(application_id="org.ming.Toolbox", flags=Gio.ApplicationFlags.FLAGS_NONE)
    application.connect("activate", lambda app: ToolboxWindow(app).present())
    return application.run([])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-toolbox")
    parser.add_argument("--section", choices=SECTIONS, default="toolbox")
    parser.add_argument("--install-windows")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.json:
        payload = {
            "schema": "ming.toolbox.v1",
            "sections": [{"id": key, "label": SECTION_LABELS[key]} for key in SECTIONS],
            "official": official_catalog(),
            "toolbox": toolbox_actions(),
            "lab": default_lab_state(),
            "android": {"runtime": "waydroid+cage", "lab": default_android_lab_state()},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    return _gtk_main(args.section, args.install_windows or "")


if __name__ == "__main__":
    raise SystemExit(main())
