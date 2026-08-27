#!/usr/bin/env python3
"""Root-side Ming Store transaction executor.

The desktop sends only an action and opaque request id across Polkit.  This
helper reloads the request and catalog from fixed local locations, resolves
the APT candidate itself, performs the mutation, and verifies the result.
"""

from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import time
from contextlib import contextmanager

try:
    import pwd
except ImportError:  # Windows source-test host; deployed runtime is Linux.
    pwd = None


REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
ALLOWED_ACTIONS = ("install", "update", "remove", "refresh")
PROTECTED_PACKAGES = {
    "apt", "dpkg", "systemd", "policykit-1", "network-manager",
    "xfce4-session", "xfwm4", "lightdm", "linux-image-amd64",
    "grub-pc", "grub-efi-amd64", "firefox-esr", "ming-store",
    "ming-settings", "ming-toolbox",
}


def _load_core():
    candidates = (
        pathlib.Path(__file__).with_name("ming-store-core.py"),
        pathlib.Path("/usr/local/lib/ming-os/ming-store-core.py"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_store_core_for_control", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Ming 应用商店核心组件缺失。")


def default_runner(command, timeout=300):
    completed = subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout,
        check=False, shell=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


class StoreControlError(RuntimeError):
    def __init__(self, state, message, exit_code=1):
        super().__init__(message)
        self.state = state
        self.exit_code = exit_code


class StoreControl:
    def __init__(
            self, environ=None, runner=None, catalog_root=None,
            request_base=pathlib.Path("/run/user"),
            claim_base=pathlib.Path("/run/ming-store-control"),
            journal_path=pathlib.Path("/var/log/ming-store-transactions.jsonl"),
            live_paths=None, cmdline_path=pathlib.Path("/proc/cmdline"),
            euid_getter=None, account_lookup=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner or default_runner
        self.catalog_root = pathlib.Path(
            catalog_root or "/usr/share/ming-os/store/catalog")
        self.request_base = pathlib.Path(request_base)
        self.claim_base = pathlib.Path(claim_base)
        self.journal_path = pathlib.Path(journal_path)
        self.live_paths = tuple(live_paths or ("/run/live", "/lib/live/mount"))
        self.cmdline_path = pathlib.Path(cmdline_path)
        self.euid_getter = euid_getter or getattr(os, "geteuid", lambda: -1)
        self.account_lookup = account_lookup or (
            pwd.getpwuid if pwd is not None else None)
        self.core = _load_core()

    def _call(self, command, timeout=300):
        try:
            return self.runner(tuple(command), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise StoreControlError("timeout", "软件操作超时，请检查网络后重试。") from exc
        except OSError as exc:
            raise StoreControlError("runtime_missing", "系统软件管理组件不可用。") from exc

    def _caller(self):
        raw_uid = str(self.environ.get("PKEXEC_UID") or "")
        if not raw_uid.isdigit() or not 1000 <= int(raw_uid) < 60000:
            raise StoreControlError(
                "administrator_not_ready",
                "无法确认桌面管理员身份，请完成账户设置后重试。", 4,
            )
        uid = int(raw_uid)
        if self.account_lookup is None:
            raise StoreControlError("runtime_missing", "系统账户组件不可用。", 4)
        try:
            record = self.account_lookup(uid)
        except KeyError as exc:
            raise StoreControlError("administrator_not_ready", "找不到授权用户。", 4) from exc
        rc, output, _error = self._call(("passwd", "-S", record.pw_name), timeout=5)
        password_ready = rc == 0 and len(output.split()) >= 2 and output.split()[1] == "P"
        rc, output, _error = self._call(("id", "-nG", record.pw_name), timeout=5)
        administrator = rc == 0 and "sudo" in set(output.split())
        marker = pathlib.Path(record.pw_dir) / ".config/ming-os/oobe-account-done"
        try:
            oobe_ready = marker.read_text(encoding="utf-8").strip() == "configured"
        except OSError:
            oobe_ready = False
        if not (password_ready and administrator and oobe_ready):
            raise StoreControlError(
                "administrator_not_ready",
                "请先完成首次账户设置，并确认当前用户拥有管理员密码。", 4,
            )
        return uid, record

    def _live_mode(self):
        if any(pathlib.Path(path).exists() for path in self.live_paths):
            return True
        try:
            return "boot=live" in self.cmdline_path.read_text(
                encoding="utf-8", errors="replace").split()
        except OSError:
            return False

    def _request_path(self, uid, request_id):
        return (
            self.request_base / str(uid) / "ming-os/store/requests"
            / (request_id + ".json")
        )

    def _load_request(self, uid, action, request_id):
        if action not in ALLOWED_ACTIONS or not REQUEST_ID.fullmatch(request_id):
            raise StoreControlError("invalid_request", "商店请求格式无效。", 2)
        path = self._request_path(uid, request_id)
        try:
            nofollow = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
            descriptor = os.open(path, os.O_RDONLY | nofollow)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
                    raise StoreControlError("invalid_request", "商店请求文件不可信。", 2)
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise StoreControlError("invalid_request", "商店请求权限过宽。", 2)
                if info.st_size > 16 * 1024:
                    raise StoreControlError("invalid_request", "商店请求过大。", 2)
                payload = json.load(stream)
        except StoreControlError:
            raise
        except (OSError, ValueError) as exc:
            raise StoreControlError("invalid_request", "无法读取商店请求。", 2) from exc
        request = self.core.StoreTransactionRequest.from_dict(payload)
        if request.uid != uid or request.action != action or request.request_id != request_id:
            raise StoreControlError("invalid_request", "商店请求身份不一致。", 2)
        try:
            created = calendar.timegm(time.strptime(
                request.created_at, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError as exc:
            raise StoreControlError("invalid_request", "商店请求时间无效。", 2) from exc
        if abs(time.time() - created) > 20 * 60:
            raise StoreControlError("expired_request", "商店请求已过期，请重新操作。", 2)
        return path, request

    def _journal(self, request, state, detail=""):
        event = {
            "schema": "ming.store.transaction.event.v1",
            "request_id": request.request_id,
            "uid": request.uid,
            "action": request.action,
            "provider": request.provider,
            "app_id": request.app_id,
            "state": state,
            "detail": detail,
        }
        try:
            self.core.TransactionJournal(self.journal_path).write(event)
        except OSError:
            pass

    def _provider(self, request):
        registry = self.core.ProviderRegistry({
            "ming-official": self.core.MingOfficialProvider(self.catalog_root),
            "debian-apt": self.core.DebianAptProvider(
                self.catalog_root, runner=self.runner),
            "vendor-official": self.core.VendorOfficialProvider(self.catalog_root),
        })
        return registry.get(request.provider)

    def _is_protected(self, package):
        if package in PROTECTED_PACKAGES or package.startswith(("linux-image-", "grub-")):
            return True
        rc, output, _error = self._call(("apt-cache", "show", package), timeout=15)
        return rc == 0 and bool(re.search(
            r"^(?:Essential:\s*yes|Priority:\s*required)\s*$", output,
            re.IGNORECASE | re.MULTILINE,
        ))

    def _installed(self, package):
        rc, output, _error = self._call((
            "dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}\t${Architecture}",
            package,
        ), timeout=15)
        fields = (output or "").strip().split("\t")
        return {
            "installed": rc == 0 and len(fields) == 3 and fields[0].strip().startswith("ii"),
            "version": fields[1].strip() if len(fields) == 3 else None,
            "architecture": fields[2].strip() if len(fields) == 3 else None,
        }

    def _refresh_desktop(self, uid, user_name):
        checks = {}
        for name, command in (
            ("desktop_database", ("update-desktop-database", "/usr/share/applications")),
            ("icon_cache", ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")),
        ):
            rc, _output, _error = self._call(command, timeout=60)
            checks[name] = rc == 0
        runtime = pathlib.Path("/run/user") / str(uid)
        checks["desktop_shell"] = False
        if runtime.is_dir():
            rc, _output, _error = self._call((
                "runuser", "-u", user_name, "--", "env",
                "XDG_RUNTIME_DIR=" + str(runtime),
                "/usr/local/bin/ming-phone-desktop", "--refresh-apps",
            ), timeout=45)
            checks["desktop_shell"] = rc == 0
        return checks

    @staticmethod
    def _apt_error(stderr):
        detail = (stderr or "").casefold()
        if "could not get lock" in detail or "unable to acquire" in detail:
            return "dpkg_lock", "软件管理器正忙，请稍后重试。"
        if "temporary failure resolving" in detail or "failed to fetch" in detail:
            return "network_failed", "下载失败，请检查网络或稍后重试。"
        if "unmet dependencies" in detail or "dependency problems" in detail:
            return "dependency_failed", "软件依赖无法满足。"
        return "install_failed", "软件操作失败，请查看商店日志。"

    @contextmanager
    def _claim_request(self, uid, request_id):
        try:
            self.claim_base.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.claim_base.stat()
            if (not stat.S_ISDIR(info.st_mode) or (
                    os.name != "nt" and (
                        info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077
                    ))):
                raise StoreControlError(
                    "runtime_untrusted", "商店请求锁目录不可信。", 2)
            nofollow = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
            claim = self.claim_base / (str(uid) + "-" + request_id + ".lock")
            descriptor = os.open(
                claim,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
            os.close(descriptor)
        except FileExistsError as exc:
            raise StoreControlError(
                "request_in_progress", "该软件操作正在处理中，请勿重复提交。", 2
            ) from exc
        except StoreControlError:
            raise
        except OSError as exc:
            raise StoreControlError(
                "runtime_missing", "无法建立安全的软件操作锁。", 2
            ) from exc
        try:
            yield
        finally:
            claim.unlink(missing_ok=True)

    def _execute_request(self, uid, account, action, request_id):
        path, request = self._load_request(uid, action, request_id)
        try:
            path.unlink()
        except OSError as exc:
            raise StoreControlError(
                "invalid_request", "无法将商店请求标记为已使用。", 2
            ) from exc
        self._journal(request, "resolving")
        provider = self._provider(request)
        item = provider.get(request.app_id)
        package = item["package_name"]
        if action == "remove" and (
                bool(item.get("protected")) or self._is_protected(package)):
            raise StoreControlError("protected_package", "该软件是系统核心组件，不能卸载。", 8)

        if action in ("install", "update"):
            resolved = provider.resolve(request.app_id)
            version = resolved.get("resolved_version") or resolved.get("version")
            if request.expected_version and request.expected_version != version:
                raise StoreControlError("candidate_changed", "软件版本已变化，请刷新页面后重试。", 9)
            target = resolved.get("apt_target")
            if not target:
                raise StoreControlError("provider_unavailable", "该来源尚未开放安装。", 9)
            self._journal(request, "awaiting_authorization")
            command = (
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
                "-o", "Acquire::Retries=3", "--no-install-recommends",
                "install", target,
            )
            self._journal(request, "installing")
            rc, _output, error = self._call(command, timeout=900)
            if rc != 0:
                state, message = self._apt_error(error)
                raise StoreControlError(state, message)
            self._journal(request, "readback")
            state = self._installed(package)
            if not state["installed"] or state["version"] != version:
                raise StoreControlError("readback_failed", "软件操作结束，但版本读回不一致。")
        elif action == "remove":
            self._journal(request, "awaiting_authorization")
            self._journal(request, "installing")
            rc, _output, error = self._call((
                "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
                "remove", "--no-auto-remove", package,
            ), timeout=900)
            if rc != 0:
                state, message = self._apt_error(error)
                raise StoreControlError(state, message)
            self._journal(request, "readback")
            state = self._installed(package)
            if state["installed"]:
                raise StoreControlError("readback_failed", "卸载结束，但软件仍处于已安装状态。")
        else:
            self._journal(request, "awaiting_authorization")
            self._journal(request, "installing")
            rc, _output, error = self._call((
                "apt-get", "update", "-o", "Acquire::Retries=3",
            ), timeout=300)
            if rc != 0:
                state, message = self._apt_error(error)
                raise StoreControlError(state, message)
            self._journal(request, "readback")
            state = self._installed(package)

        self._journal(request, "refreshing")
        refresh = self._refresh_desktop(uid, account.pw_name)
        failed = sorted(name for name, ready in refresh.items() if not ready)
        if failed:
            self._journal(request, "refresh_warning", ",".join(failed))
            return {
                "ok": True, "state": "refresh_warning", "installed_state": state,
                "message": "软件操作已完成，但桌面入口刷新失败。可在商店中安全重试刷新。",
                "refresh_failed": failed,
            }
        self._journal(request, "succeeded")
        return {
            "ok": True, "state": "succeeded", "installed_state": state,
            "message": "软件操作已完成。", "refresh_failed": [],
        }

    def execute(self, action, request_id):
        if self.euid_getter() != 0:
            raise StoreControlError("permission_denied", "此操作需要管理员授权。", 3)
        if self._live_mode():
            raise StoreControlError(
                "live_blocked",
                "Live 模式只能浏览。请先安装系统并完成账户设置。", 7,
            )
        uid, account = self._caller()
        with self._claim_request(uid, request_id):
            return self._execute_request(uid, account, action, request_id)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-store-control")
    parser.add_argument("action", choices=ALLOWED_ACTIONS)
    parser.add_argument("request_id")
    args = parser.parse_args(argv)
    control = StoreControl()
    try:
        result = control.execute(args.action, args.request_id)
    except StoreControlError as exc:
        result = {"ok": False, "state": exc.state, "message": str(exc)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return exc.exit_code
    except Exception as exc:
        result = {"ok": False, "state": "failed", "message": "商店操作失败：%s" % exc}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
