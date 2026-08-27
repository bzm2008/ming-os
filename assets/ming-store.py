#!/usr/bin/env python3
"""Ming Store UI shell and dependency-free presentation controller."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time


APP_NAME = "Ming 应用商店"
APP_ID = "cn.sca.ming.Store"
NAVIGATION = ("home", "categories", "installed", "updates", "failures")
NAVIGATION_LABELS = {
    "home": "首页",
    "categories": "分类",
    "installed": "已安装",
    "updates": "更新",
    "failures": "失败记录",
}
SOURCES = {
    "all": "全部来源",
    "ming-official": "Ming 官方",
    "debian-apt": "Debian / Ming 仓库",
    "vendor-official": "厂商官方",
}
MING_MINT_CSS = """
window.ming-store { background: #f5faf8; color: #17332c; }
.ming-store-sidebar { background: #e8f3ef; padding: 8px; }
.ming-store-accent { background: #1f8a70; color: white; border-radius: 8px; }
.ming-store-card { background: #ffffff; border-radius: 8px; padding: 12px; }
"""
SAFE_REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
TRANSACTION_PHASE_LABELS = {
    "resolving": "正在解析可信软件来源",
    "downloading": "正在下载软件包",
    "verifying": "正在校验软件身份与完整性",
    "awaiting_authorization": "等待管理员授权",
    "installing": "正在执行软件操作",
    "readback": "正在读回安装状态",
    "refreshing": "正在刷新桌面入口",
    "succeeded": "操作已完成",
    "refresh_warning": "软件操作完成，但桌面入口刷新失败",
    "failed": "操作未完成",
}


def layout_mode(width):
    return "compact" if int(width) < 700 else "wide"


def operation_presentation(result):
    state = str(result.get("state") or "failed")
    if state == "refresh_warning" or "refresh_warning" in state:
        return {
            "tone": "warning", "retry_label": "重试刷新",
            "retry_action": "refresh",
            "message": str(result.get("message") or TRANSACTION_PHASE_LABELS["refresh_warning"]),
        }
    if state in ("succeeded", "installed", "removed", "updated"):
        return {
            "tone": "success", "retry_label": "已完成", "retry_action": None,
            "message": str(result.get("message") or TRANSACTION_PHASE_LABELS["succeeded"]),
        }
    return {
        "tone": "error", "retry_label": "重试",
        "retry_action": str(result.get("action") or "install"),
        "message": str(result.get("message") or result.get("error") or "操作未完成，请查看日志。"),
    }


def packagekit_capability(which=None, exists=None):
    """Report optional read-only PackageKit discovery support.

    Mutating transactions always remain under ming-store-control, which
    re-resolves the exact APT candidate and verifies the final dpkg state.
    """
    which = which or shutil.which
    exists = exists or (lambda path: pathlib.Path(path).is_file())
    daemon = any(exists(path) for path in (
        "/usr/libexec/packagekitd", "/usr/lib/packagekit/packagekitd",
        "/usr/lib/packagekitd",
    ))
    aptcc = any(exists(path) for path in (
        "/usr/lib/packagekit-backend/libpk_backend_aptcc.so",
        "/usr/lib/x86_64-linux-gnu/packagekit-backend/libpk_backend_aptcc.so",
    ))
    available = bool(which("pkcon") and daemon and aptcc)
    return {
        "available": available,
        "mode": "query_only" if available else "disabled",
        "fallback": "apt_provider",
        "root_re_resolve_required": True,
        "reason": "ready" if available else "packagekitd_or_aptcc_missing",
    }


def _load_core():
    candidates = (
        pathlib.Path(__file__).with_name("ming-store-core.py"),
        pathlib.Path("/usr/local/lib/ming-os/ming-store-core.py"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_store_core_for_ui", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Ming 应用商店核心组件缺失，请重新安装系统组件。")


def gtk_dependency_status(importer=None):
    importer = importer or importlib.import_module
    try:
        gi = importer("gi")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except (ImportError, AttributeError, ValueError) as exc:
        return {
            "ok": False,
            "state": "gtk_unavailable",
            "error": "图形组件不可用，无法打开 Ming 应用商店。",
            "detail": str(exc)[:300],
        }
    return {"ok": True, "state": "ready"}


class StoreController:
    def __init__(
            self, catalog=None, deb_inspector=None, command_runner=None,
            version_comparator=None):
        self.core = _load_core()
        self.catalog = catalog if catalog is not None else self.core.default_catalog()
        self.deb_inspector = deb_inspector or self._inspect_deb
        self.command_runner = command_runner or subprocess.run
        self.version_comparator = version_comparator or self._version_is_newer

    @staticmethod
    def _version_is_newer(installed, candidate):
        try:
            completed = subprocess.run(
                ["dpkg", "--compare-versions", str(installed), "lt", str(candidate)],
                capture_output=True, text=True, timeout=10, check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def search(self, query="", source_id=None):
        if source_id in (None, "", "all"):
            source_id = None
        if source_id is not None and source_id not in self.core.ALLOWED_PROVIDERS:
            raise ValueError("软件来源无效。")
        return self.catalog.search(str(query or "").strip(), source_id=source_id)

    def inventory(self, query="", source_id=None):
        inventory = []
        for item in self.search(query, source_id):
            current = dict(item)
            try:
                state = self.catalog.installed_state(
                    current["source_id"], current["app_id"])
            except (KeyError, RuntimeError, ValueError):
                state = {
                    "installed": False, "version": None,
                    "architecture": None, "state": "status_unavailable",
                }
            current["_installed_state"] = state
            inventory.append(current)
        return inventory

    def categories(self):
        grouped = {}
        for item in self.search(""):
            categories = item.get("categories") or ["其他"]
            for category in categories:
                grouped.setdefault(str(category), []).append(item)
        return grouped

    def installed_apps(self):
        result = []
        for item in self.inventory(""):
            state = item["_installed_state"]
            if not state.get("installed"):
                continue
            current = dict(item)
            current["installed_version"] = state.get("version")
            current["installed_architecture"] = state.get("architecture")
            result.append(current)
        return result

    def available_updates(self):
        updates = []
        for item in self.installed_apps():
            if not item.get("enabled", True):
                continue
            try:
                provider = self.catalog.registry.get(item["source_id"])
                resolved = provider.resolve(item["app_id"])
            except (KeyError, RuntimeError, ValueError):
                continue
            candidate = resolved.get("resolved_version") or resolved.get("version")
            installed = item.get("installed_version")
            if not installed or not candidate or candidate == "candidate":
                continue
            if self.version_comparator(installed, candidate):
                current = dict(item)
                current["available_version"] = candidate
                updates.append(current)
        return updates

    @staticmethod
    def failure_records(path):
        records = []
        try:
            lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            return records
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("state") in {
                "failed", "timeout", "runtime_missing", "authorization_failed",
                "network_failed", "dependency_failed", "dpkg_lock",
                "readback_failed", "refresh_warning", "live_blocked",
            }:
                records.append(item)
        return records[-100:]

    @staticmethod
    def read_log(path=None, limit=100):
        path = pathlib.Path(path or StoreController.result_journal_path())
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records = []
        core = _load_core()
        for line in lines[-max(1, min(int(limit), 500)):]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(core.redact(item))
        return records

    def retry_failure(self, record):
        if not isinstance(record, dict):
            raise ValueError("失败记录格式无效。")
        source = str(record.get("provider") or "")
        app_id = str(record.get("app_id") or "")
        action = "refresh" if record.get("state") == "refresh_warning" else str(record.get("action") or "")
        if action not in ("install", "update", "remove", "refresh"):
            raise ValueError("失败记录中的操作无效。")
        if source not in self.core.ALLOWED_PROVIDERS or not self.core.SAFE_ID.fullmatch(app_id):
            raise ValueError("失败记录中的软件身份无效。")
        return self.run_transaction(action, source, app_id)

    @staticmethod
    def diagnostic_command(upload=False, confirmed=False):
        if upload:
            if not confirmed:
                raise PermissionError("上传诊断前必须由用户明确确认。")
            return ["/usr/local/bin/ming-diagnostic-upload"]
        return ["/usr/local/bin/ming-diagnostic-bundle"]

    def run_diagnostic(self, upload=False, confirmed=False, timeout=180):
        command = self.diagnostic_command(upload=upload, confirmed=confirmed)
        try:
            completed = self.command_runner(
                command, capture_output=True, text=True, timeout=timeout,
                check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "state": "diagnostic_unavailable", "message": str(exc)}
        return {
            "ok": completed.returncode == 0,
            "state": "diagnostic_ready" if completed.returncode == 0 else "diagnostic_failed",
            "message": self.core.redact(
                (completed.stdout or completed.stderr or "").strip()[:1000]),
        }

    @staticmethod
    def _inspect_deb(path):
        completed = subprocess.run(
            [
                "dpkg-deb", "--field", str(path),
                "Package", "Version", "Architecture", "Description",
            ],
            capture_output=True, text=True, timeout=15, check=False, shell=False,
        )
        if completed.returncode != 0:
            raise ValueError("无法读取 DEB 软件信息。")
        lines = completed.stdout.splitlines()
        if len(lines) < 3:
            raise ValueError("DEB 软件信息不完整。")
        return {
            "package": lines[0].strip(),
            "version": lines[1].strip(),
            "architecture": lines[2].strip(),
            "description": "\n".join(lines[3:]).strip(),
        }

    def local_deb_detail(self, source):
        raw = str(source)
        if "://" in raw:
            raise ValueError("只允许选择本地 DEB 文件。")
        path = pathlib.Path(source)
        if path.is_symlink():
            raise ValueError("不接受符号链接形式的安装文件。")
        if path.suffix.lower() != ".deb" or not path.is_file():
            raise ValueError("请选择有效的本地 .deb 文件。")
        if path.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise ValueError("安装文件超过 2 GiB 限制。")
        details = self.deb_inspector(path.resolve(strict=True))
        if not isinstance(details, dict):
            raise ValueError("DEB 检查结果无效。")
        package = str(details.get("package") or "")
        architecture = str(details.get("architecture") or "")
        if not self.core.SAFE_ID.fullmatch(package):
            raise ValueError("DEB 包名无效。")
        if architecture not in ("amd64", "all"):
            raise ValueError("该 DEB 架构不受支持。")
        return {
            "ok": True,
            "state": "review_required",
            "source_type": "local-deb",
            "path": str(path.resolve()),
            "package": package,
            "version": str(details.get("version") or ""),
            "architecture": architecture,
            "description": str(details.get("description") or ""),
            "notice": "确认软件来源后，将通过 Ming 受限授权链安装。",
        }

    def create_transaction(self, action, source_id, app_id, runtime_root=None):
        if action not in ("install", "update", "remove", "refresh"):
            raise ValueError("操作不在白名单内。")
        provider = self.catalog.registry.get(source_id)
        item = provider.get(app_id)
        if action in ("install", "update"):
            item = provider.resolve(app_id)
        expected = item.get("resolved_version") or item.get("version")
        if expected == "candidate":
            expected = None
        request_id = secrets.token_hex(16)
        payload = {
            "schema": self.core.TRANSACTION_SCHEMA,
            "request_id": request_id,
            "uid": int(getattr(os, "getuid", lambda: 1000)()),
            "action": action,
            "provider": source_id,
            "app_id": app_id,
            "expected_version": expected,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.core.StoreTransactionRequest.from_dict(payload)
        root = pathlib.Path(runtime_root or (
            pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
            / "ming-os/store/requests"
        ))
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = root / (request_id + ".json")
        temporary = root / ("." + request_id + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(target)
        return payload

    @staticmethod
    def live_mode():
        if pathlib.Path("/run/live").exists() or pathlib.Path("/lib/live/mount").exists():
            return True
        try:
            return "boot=live" in pathlib.Path("/proc/cmdline").read_text(
                encoding="utf-8", errors="replace").split()
        except OSError:
            return False

    @staticmethod
    def result_journal_path():
        return pathlib.Path.home() / ".local/state/ming-os/store/transactions.jsonl"

    @staticmethod
    def _report_progress(callback, state, **detail):
        if callback is not None:
            callback({"state": state, "label": TRANSACTION_PHASE_LABELS.get(state, state), **detail})

    def run_transaction(
            self, action, source_id, app_id, timeout=960,
            progress_callback=None):
        if self.live_mode():
            result = {
                "ok": False, "state": "live_blocked",
                "message": "Live 模式只能浏览。请先安装系统并完成账户设置。",
            }
            self._report_progress(progress_callback, "failed", result=result)
            return result
        try:
            payload = self.create_transaction(action, source_id, app_id)
            command = self.authorization_command(action, payload["request_id"])
            self._report_progress(progress_callback, "awaiting_authorization")
            completed = self.command_runner(
                command, capture_output=True, text=True, timeout=timeout,
                check=False, shell=False,
            )
            self._report_progress(progress_callback, "readback")
            raw = (completed.stdout or completed.stderr or "").strip().splitlines()
            try:
                result = json.loads(raw[-1]) if raw else {}
            except (ValueError, TypeError):
                result = {}
            if not isinstance(result, dict) or "state" not in result:
                result = {
                    "ok": False, "state": "authorization_failed",
                    "message": "授权被取消，或系统没有返回可读结果。",
                }
            result.update({
                "request_id": payload["request_id"], "action": action,
                "provider": source_id, "app_id": app_id,
            })
        except subprocess.TimeoutExpired:
            result = {
                "ok": False, "state": "timeout", "action": action,
                "provider": source_id, "app_id": app_id,
                "request_id": payload["request_id"],
                "message": "软件操作超时，请检查网络后重试。",
            }
        except OSError:
            result = {
                "ok": False, "state": "runtime_missing", "action": action,
                "provider": source_id, "app_id": app_id,
                "request_id": locals().get("payload", {}).get("request_id"),
                "message": "系统授权或软件管理组件缺失。",
            }
        except (KeyError, RuntimeError, ValueError) as exc:
            result = {
                "ok": False, "state": "failed", "action": action,
                "provider": source_id, "app_id": app_id,
                "request_id": locals().get("payload", {}).get("request_id"),
                "message": str(exc),
            }
        try:
            self.core.TransactionJournal(self.result_journal_path()).write(result)
        except OSError:
            pass
        terminal = result.get("state") if result.get("state") in TRANSACTION_PHASE_LABELS else "failed"
        self._report_progress(progress_callback, terminal, result=result)
        return result

    def stage_local_deb(self, source, runtime_root=None):
        detail = self.local_deb_detail(source)
        source_path = pathlib.Path(detail["path"])
        request_id = secrets.token_hex(16)
        root = pathlib.Path(runtime_root or (
            pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
            / "ming-os/store/local-deb"
        ))
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = root / (request_id + ".deb")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(source_path, flags)
        try:
            source_stat = os.fstat(source_fd)
            if not source_stat.st_size or source_stat.st_size > 2 * 1024 * 1024 * 1024:
                raise ValueError("DEB 文件大小无效。")
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(source_fd, "rb", closefd=False) as source_stream, \
                        os.fdopen(target_fd, "wb", closefd=True) as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, 1024 * 1024)
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            os.close(source_fd)
        return target

    def install_local_deb(self, source, timeout=960):
        if self.live_mode():
            return {
                "ok": False, "state": "live_blocked",
                "message": "Live 模式只能浏览。请先安装系统并完成账户设置。",
            }
        staged = self.stage_local_deb(source)
        try:
            completed = self.command_runner(
                ["/usr/local/bin/ming-authorized-action", "package", "install", str(staged)],
                capture_output=True, text=True, timeout=timeout, check=False, shell=False,
            )
            raw = (completed.stdout or completed.stderr or "").strip().splitlines()
            try:
                result = json.loads(raw[-1]) if raw else {}
            except (ValueError, TypeError):
                result = {}
            if not isinstance(result, dict) or "state" not in result:
                result = {
                    "ok": False, "state": "authorization_failed",
                    "message": "授权被取消，或安装器没有返回可读结果。",
                }
            return result
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def authorization_command(action, request_id):
        if action not in ("install", "update", "remove", "refresh"):
            raise ValueError("操作不在白名单内。")
        if not SAFE_REQUEST_ID.fullmatch(str(request_id)):
            raise ValueError("request_id 无效。")
        return [
            "/usr/local/bin/ming-authorized-action", "store", action,
            str(request_id),
        ]


def _build_window(application, controller, initial_query="", local_deb=None):
    from gi.repository import Adw, GLib, Gtk

    window = Adw.ApplicationWindow(application=application)
    window.set_title(APP_NAME)
    window.set_default_size(980, 680)
    window.add_css_class("ming-store")

    header = Adw.HeaderBar()
    title = Gtk.Label(label=APP_NAME)
    title.add_css_class("title")
    header.set_title_widget(title)
    search = Gtk.SearchEntry(placeholder_text="搜索软件")
    search.set_text(initial_query)
    source = Gtk.DropDown.new_from_strings(list(SOURCES.values()))
    controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for margin in ("start", "end", "top", "bottom"):
        getattr(controls, "set_margin_" + margin)(8 if margin in ("top", "bottom") else 12)
    search.set_hexpand(True)
    controls.append(search)
    controls.append(source)

    split = Adw.OverlaySplitView()
    split.set_collapsed(False)
    sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    sidebar.add_css_class("ming-store-sidebar")
    for name in NAVIGATION:
        row = Gtk.ListBoxRow()
        row.set_child(Gtk.Label(label=NAVIGATION_LABELS[name], xalign=0))
        row.page_name = name
        sidebar.append(row)
    split.set_sidebar(sidebar)
    results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    scroller = Gtk.ScrolledWindow(child=results, hexpand=True, vexpand=True)
    split.set_content(scroller)
    current_page = {"name": "home"}
    page_generation = {"value": 0}

    breakpoint = Adw.Breakpoint.new(
        Adw.BreakpointCondition.parse("max-width: 699px"))
    breakpoint.add_setter(split, "collapsed", True)
    window.add_breakpoint(breakpoint)

    def clear_results():
        while results.get_first_child() is not None:
            results.remove(results.get_first_child())

    def add_message(title_text, subtitle=""):
        row = Adw.ActionRow(title=title_text, subtitle=subtitle)
        row.add_css_class("ming-store-card")
        results.append(row)
        return row

    def load_page_async(loader, renderer, empty_title, empty_subtitle):
        page_generation["value"] += 1
        generation = page_generation["value"]
        clear_results()
        add_message("正在读取", "正在查询软件目录和系统安装状态…")

        def worker():
            try:
                payload = loader()
                error = None
            except Exception as exc:
                payload, error = [], str(exc)

            def apply():
                if generation != page_generation["value"]:
                    return False
                clear_results()
                if error:
                    add_message("页面暂不可用", error)
                elif not payload:
                    add_message(empty_title, empty_subtitle)
                else:
                    renderer(payload)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, name="ming-store-page-loader", daemon=True).start()

    def update_progress(row, button, event):
        state = str(event.get("state") or "failed")
        button.set_label(TRANSACTION_PHASE_LABELS.get(state, "处理中"))
        row.set_subtitle(str(event.get("label") or TRANSACTION_PHASE_LABELS.get(state, state)))
        return False

    def set_row_result(row, button, result):
        presentation = operation_presentation(result)
        row.set_subtitle(presentation["message"])
        button.set_label(presentation["retry_label"])
        button.store_action = presentation["retry_action"]
        if presentation["tone"] == "success":
            previous = str(result.get("action") or "")
            button.store_action = "install" if previous == "remove" else "remove"
            button.set_label("安装" if previous == "remove" else "卸载")
        button.set_sensitive(button.store_action is not None)
        return False

    def run_item_action(_clicked, row, button, item):
        action = str(getattr(button, "store_action", "install") or "")
        if action not in ("install", "update", "remove", "refresh"):
            return
        button.set_sensitive(False)
        update_progress(row, button, {"state": "resolving"})

        def progress(event):
            GLib.idle_add(update_progress, row, button, event)

        def worker():
            try:
                result = controller.run_transaction(
                    action, item["source_id"], item["app_id"],
                    progress_callback=progress,
                )
            except Exception as exc:
                result = {
                    "ok": False, "state": "failed", "action": action,
                    "message": "操作未完成：%s" % exc,
                }
            GLib.idle_add(set_row_result, row, button, result)

        threading.Thread(target=worker, name="ming-store-transaction", daemon=True).start()

    def action_button(row, item, forced_action=None):
        installed = item.get("_installed_state") or {"installed": False}
        action = forced_action or ("remove" if installed.get("installed") else "install")
        labels = {"install": "安装", "update": "更新", "remove": "卸载", "refresh": "重试刷新"}
        button = Gtk.Button(label=labels[action], valign=Gtk.Align.CENTER)
        button.store_action = action
        if controller.live_mode():
            button.set_sensitive(False)
            button.set_tooltip_text("Live 模式只能浏览，请先安装系统并完成账户设置。")
        elif not item.get("enabled", True):
            button.set_sensitive(False)
            button.set_label("身份待确认")
            button.set_tooltip_text("厂商版本和 SHA256 尚未固定，暂不上架。")
        else:
            button.connect("clicked", run_item_action, row, button, item)
        row.add_suffix(button)
        return button

    def build_detail(item):
        clear_results()
        name = str(item.get("name") or item.get("app_id") or "未知软件")
        row = add_message(name, str(item.get("description") or "暂无软件介绍。"))
        action_button(row, item)
        source_name = SOURCES.get(item.get("source_id"), item.get("source_id", "未知来源"))
        identity = item.get("identity") or {}
        add_message("软件来源", "%s；安装前由系统重新解析并校验。" % source_name)
        add_message(
            "版本与身份",
            "%s · %s · %s" % (
                item.get("resolved_version") or item.get("version") or "待解析",
                ", ".join(item.get("architectures") or ["未知架构"]),
                identity.get("type") or "身份待确认",
            ),
        )
        add_message("许可说明", str(item.get("license") or "请查看软件厂商许可协议。"))

    def add_software_row(item, forced_action=None):
        source_name = SOURCES.get(item.get("source_id"), item.get("source_id", ""))
        version = item.get("available_version") or item.get("installed_version") or item.get("version") or ""
        row = Adw.ActionRow(
            title=str(item.get("name") or item.get("app_id") or "未知软件"),
            subtitle="%s · %s · %s" % (item.get("package_name", item.get("app_id", "")), version, source_name),
        )
        row.add_css_class("ming-store-card")
        detail = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER)
        detail.set_tooltip_text("查看软件详情与来源")
        detail.connect("clicked", lambda _button: build_detail(item))
        row.add_suffix(detail)
        if item.get("app_id") and item.get("source_id"):
            action_button(row, item, forced_action=forced_action)
        results.append(row)

    def selected_source():
        selected = source.get_selected()
        return list(SOURCES)[selected] if selected < len(SOURCES) else "all"

    def show_home():
        query = search.get_text()
        source_id = selected_source()
        load_page_async(
            lambda: controller.inventory(query, source_id),
            lambda items: [add_software_row(item) for item in items],
            "没有找到软件", "请更换关键词或来源。",
        )

    def show_categories():
        page_generation["value"] += 1
        clear_results()
        grouped = controller.categories()
        if not grouped:
            add_message("暂无分类", "软件目录当前为空。")
        for category, items in sorted(grouped.items()):
            row = add_message(category, "%d 个软件" % len(items))
            button = Gtk.Button(label="查看", valign=Gtk.Align.CENTER)

            def open_category(_button, selected_category=category):
                load_page_async(
                    lambda: [
                        item for item in controller.inventory("")
                        if selected_category in (item.get("categories") or ["其他"])
                    ],
                    lambda selected_items: [add_software_row(item) for item in selected_items],
                    "分类为空", "该分类当前没有可用软件。",
                )

            button.connect("clicked", open_category)
            row.add_suffix(button)

    def show_installed():
        load_page_async(
            controller.installed_apps,
            lambda items: [add_software_row(item, forced_action="remove") for item in items],
            "暂无已安装软件", "从首页选择软件即可安装。",
        )

    def show_updates():
        load_page_async(
            controller.available_updates,
            lambda items: [add_software_row(item, forced_action="update") for item in items],
            "暂无可用更新", "已安装软件当前没有可验证的新版本。",
        )

    def show_logs(_button=None):
        page_generation["value"] += 1
        clear_results()
        records = controller.read_log(limit=100)
        if not records:
            add_message("暂无商店日志", "完成一次软件操作后会在这里显示脱敏记录。")
        for record in reversed(records):
            add_message(
                str(record.get("app_id") or "系统记录"),
                "%s · %s" % (record.get("state", "unknown"), record.get("message") or record.get("detail") or ""),
            )

    def run_diagnostic(button, upload=False, confirmed=False):
        button.set_sensitive(False)
        button.set_label("处理中")

        def worker():
            result = controller.run_diagnostic(upload=upload, confirmed=confirmed)

            def finish():
                button.set_sensitive(True)
                button.set_label("上传脱敏诊断" if upload else "生成脱敏诊断")
                add_message("诊断结果", result.get("message") or "诊断操作未完成。")
                return False

            GLib.idle_add(finish)

        threading.Thread(target=worker, name="ming-store-diagnostic", daemon=True).start()

    def confirm_diagnostic_upload(button):
        dialog = Adw.MessageDialog.new(
            window, "上传脱敏诊断？",
            "只会在你确认后上传诊断包。上传前客户端会脱敏，服务器还会再次检查。",
        )
        dialog.add_responses("cancel", "取消", "upload", "确认上传")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, response_id):
            if response_id == "upload":
                run_diagnostic(button, upload=True, confirmed=True)

        dialog.connect("response", response)
        dialog.present()

    def show_failures():
        page_generation["value"] += 1
        clear_results()
        tools = add_message("日志与诊断", "日志只显示脱敏内容；上传必须再次确认。")
        log_button = Gtk.Button(label="查看日志", valign=Gtk.Align.CENTER)
        log_button.connect("clicked", show_logs)
        bundle_button = Gtk.Button(label="生成脱敏诊断", valign=Gtk.Align.CENTER)
        bundle_button.connect("clicked", run_diagnostic, False, False)
        upload_button = Gtk.Button(label="上传脱敏诊断", valign=Gtk.Align.CENTER)
        upload_button.connect("clicked", confirm_diagnostic_upload)
        tools.add_suffix(log_button)
        tools.add_suffix(bundle_button)
        tools.add_suffix(upload_button)
        records = controller.failure_records(controller.result_journal_path())
        if not records:
            add_message("暂无失败记录", "失败、授权取消和刷新警告会显示在这里。")
        for record in reversed(records):
            row = add_message(
                str(record.get("app_id") or "未知软件"),
                str(record.get("message") or record.get("state") or "操作未完成。"),
            )
            if record.get("provider") and record.get("app_id"):
                item = {
                    "app_id": record["app_id"], "source_id": record["provider"],
                    "enabled": True,
                }
                action = "refresh" if record.get("state") == "refresh_warning" else record.get("action")
                if action in ("install", "update", "remove", "refresh"):
                    action_button(row, item, forced_action=action)

    def render_local_deb(path, detail):
        clear_results()
        row = add_message(
            "本地 DEB：" + detail["package"],
            "%s · %s" % (detail["version"], detail["architecture"]),
        )
        add_message("来源说明", "这是用户选择的本地文件，不属于受信软件目录；确认来源后才会请求授权。")
        add_message("安装说明", detail["notice"])
        button = Gtk.Button(label="确认并安装", valign=Gtk.Align.CENTER)
        row.add_suffix(button)

        def run_local_action(_clicked):
            button.set_sensitive(False)
            button.set_label("等待授权")

            def worker():
                try:
                    result = controller.install_local_deb(path)
                except Exception as exc:
                    result = {"ok": False, "state": "failed", "message": str(exc)}
                GLib.idle_add(set_row_result, row, button, result)

            threading.Thread(target=worker, name="ming-store-local-deb", daemon=True).start()

        button.connect("clicked", run_local_action)

    def load_local_deb_async(path):
        page_generation["value"] += 1
        generation = page_generation["value"]
        clear_results()
        add_message("正在检查本地 DEB", "正在读取包名、版本和架构，不会直接执行该文件。")

        def worker():
            try:
                detail, error = controller.local_deb_detail(path), None
            except Exception as exc:
                detail, error = None, str(exc)

            def apply():
                if generation != page_generation["value"]:
                    return False
                if error:
                    clear_results()
                    add_message("无法读取本地 DEB", error)
                else:
                    render_local_deb(path, detail)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, name="ming-store-deb-inspector", daemon=True).start()

    def show_page(name):
        current_page["name"] = name
        {
            "home": show_home,
            "categories": show_categories,
            "installed": show_installed,
            "updates": show_updates,
            "failures": show_failures,
        }.get(name, show_home)()
        if split.get_collapsed():
            split.set_show_sidebar(False)

    def on_navigation(_listbox, row):
        if row is not None:
            show_page(getattr(row, "page_name", "home"))

    sidebar.connect("row-selected", on_navigation)
    search.connect("search-changed", lambda *_args: show_home() if current_page["name"] == "home" else None)
    source.connect("notify::selected", lambda *_args: show_home() if current_page["name"] == "home" else None)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    content.append(controls)
    content.append(split)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(header)
    toolbar.set_content(content)
    window.set_content(toolbar)
    if local_deb:
        load_local_deb_async(local_deb)
    else:
        sidebar.select_row(sidebar.get_row_at_index(0))
    return window


def run_gui(initial_query="", local_deb=None):
    status = gtk_dependency_status()
    if not status["ok"]:
        print(json.dumps(status, ensure_ascii=False), file=sys.stderr)
        return 2
    from gi.repository import Adw, Gdk, Gtk

    application = Adw.Application(application_id=APP_ID)

    def activate(app):
        provider = Gtk.CssProvider()
        provider.load_from_data(MING_MINT_CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        try:
            controller = StoreController()
            window = _build_window(
                app, controller, initial_query=initial_query, local_deb=local_deb)
        except Exception as exc:
            window = Adw.ApplicationWindow(application=app)
            window.set_title(APP_NAME)
            window.set_content(Gtk.Label(label="商店暂不可用：%s" % exc, wrap=True))
        window.present()

    application.connect("activate", activate)
    return application.run([])


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--search", default="")
    parser.add_argument("--inspect-deb")
    parser.add_argument("--local-deb")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(gtk_dependency_status(), ensure_ascii=False))
        return 0
    local_deb = args.local_deb or args.inspect_deb
    if args.inspect_deb:
        try:
            result = StoreController().local_deb_detail(args.inspect_deb)
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"ok": False, "state": "inspection_failed", "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return run_gui(args.search, local_deb=args.local_deb)


if __name__ == "__main__":
    raise SystemExit(main())
