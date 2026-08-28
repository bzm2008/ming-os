#!/usr/bin/env python3
"""Root-side Ming Store transaction executor.

The desktop sends only an action and opaque request id across Polkit.  This
helper reloads the request and catalog from fixed local locations, resolves
the APT candidate itself, performs the mutation, and verifies the result.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
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
            euid_getter=None, account_lookup=None, downloader=None,
            artifact_root=pathlib.Path("/var/cache/ming-os/store")):
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
        self.downloader = downloader or self.core.SecureDownloader(
            max_bytes=8 * 1024 * 1024 * 1024,
            allowed_hosts=getattr(self.core, "SPARK_ALLOWED_HOSTS", ()),
        )
        self.artifact_root = pathlib.Path(artifact_root)

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

    def _claimed_request_path(self, uid, request_id):
        """Return the root-owned staging name used to consume a request.

        The request is moved here before it is parsed.  This removes the
        user-writable request pathname from the transaction before any
        package operation starts, so a later cleanup cannot unlink a file
        that a caller replaced in the meantime.
        """
        return self.claim_base / (str(uid) + "-" + request_id + ".request")

    def _load_request(self, uid, action, request_id):
        if action not in ALLOWED_ACTIONS or not REQUEST_ID.fullmatch(request_id):
            raise StoreControlError("invalid_request", "商店请求格式无效。", 2)
        path = self._request_path(uid, request_id)
        claimed = self._claimed_request_path(uid, request_id)
        try:
            # _claim_request() has already authenticated this directory and
            # established the per-request lock.  os.replace performs the
            # consume as one filesystem operation before we parse anything.
            if claimed.exists():
                raise StoreControlError(
                    "request_in_progress", "该软件操作正在处理中，请勿重复提交。", 2)
            os.replace(path, claimed)
        except StoreControlError:
            raise
        except (FileNotFoundError, OSError) as exc:
            raise StoreControlError(
                "invalid_request", "无法领取商店请求，请重新操作。", 2
            ) from exc
        try:
            try:
                nofollow = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
                descriptor = os.open(claimed, os.O_RDONLY | nofollow)
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
            return claimed, request
        finally:
            # claimed lives in the trusted root-side directory, unlike the
            # caller-controlled request pathname that was atomically moved.
            try:
                os.unlink(claimed)
            except FileNotFoundError:
                pass

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
            "wine-official": self.core.WineOfficialProvider(self.catalog_root),
            "spark-public": self.core.SparkPublicProvider(
                cache_root=self.artifact_root / "catalog",
                keyring_path="/etc/ming-os/store/spark-archive-keyring.gpg",
                config_path=self.catalog_root / "spark-public.json",
            ),
        })
        return registry.get(request.provider)

    def _spark_policy(self):
        path = self.catalog_root / "spark-public.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {
                "schema": "ming.store.spark-public.v1",
                "provider": "spark-public",
                "installation_enabled": False,
                "installation_disabled_reason": "星火公开目录当前仅供浏览，不能安装。",
            }
        if not isinstance(document, dict):
            return {"installation_enabled": False}
        fingerprint = str(document.get("key_fingerprint") or "").strip().upper()
        valid = (
            document.get("schema") == "ming.store.spark-public.v1"
            and document.get("provider") == "spark-public"
            and fingerprint == getattr(self.core, "SPARK_KEY_FINGERPRINT", "")
            and bool(re.fullmatch(r"[0-9A-F]{40}", fingerprint))
        )
        return dict(document,
                    key_fingerprint=fingerprint,
                    installation_enabled=bool(document.get("installation_enabled")) and valid)

    @staticmethod
    def _legacy_package_name(package):
        value = str(package or "").strip().casefold()
        bases = (
            "spark-store", "ssinstall", "aptss", "apm", "amber-ce",
            "bookworm-run", "trixie-run", "cn.flamescion.bookworm-compatibility-mode",
        )
        return any(value == base or value.startswith(base + "-") for base in bases)

    @classmethod
    def _has_legacy_spark_dependency(cls, dependency_text):
        lines = str(dependency_text or "").splitlines()
        has_dependency_label = any(
            re.match(r"^\s*(?:Pre-)?Depends:\s*", line, re.IGNORECASE)
            for line in lines
        )
        dependency_continuation = False
        for line in lines:
            match = re.match(r"^\s*(?:Pre-)?Depends:\s*(.*)$", line, re.IGNORECASE)
            if match:
                values = match.group(1)
                dependency_continuation = True
            else:
                if has_dependency_label:
                    if not (line[:1].isspace() and dependency_continuation):
                        dependency_continuation = False
                        continue
                    values = line
                else:
                    dependency_continuation = False
                    prefix = line.strip().split(":", 1)[0].casefold()
                    if (":" in line and prefix in {
                            "package", "version", "architecture", "description",
                            "maintainer", "section", "priority", "source", "size",
                            "sha256", "filename", "installed-size", "homepage",
                    }):
                        continue
                    values = line
            for group in values.split(","):
                for alternative in group.split("|"):
                    atom = alternative.strip()
                    atom = re.sub(r"\s*\([^)]*\)", "", atom).strip()
                    atom = atom.split(":", 1)[0].strip().casefold()
                    if cls._legacy_package_name(atom):
                        return True
        return False

    @staticmethod
    def _deb_metadata(output):
        lines = [line.rstrip() for line in str(output or "").splitlines()]
        labelled = {}
        current_key = None
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip() in {"Package", "Version", "Architecture", "Depends", "Pre-Depends"}:
                    current_key = key.strip()
                    labelled.setdefault(current_key, []).append(value.strip())
                    continue
            if line[:1].isspace() and current_key in {"Depends", "Pre-Depends"}:
                labelled[current_key][-1] = "%s %s" % (
                    labelled[current_key][-1], line.strip())
            else:
                current_key = None
        if labelled.get("Package") and labelled.get("Version") and labelled.get("Architecture"):
            return {
                "package": labelled["Package"][0],
                "version": labelled["Version"][0],
                "architecture": labelled["Architecture"][0],
                "dependencies": [
                    "%s: %s" % (key, value)
                    for key in ("Depends", "Pre-Depends")
                    for value in labelled.get(key, [])
                ],
            }
        return {
            "package": lines[0].strip() if len(lines) > 0 else "",
            "version": lines[1].strip() if len(lines) > 1 else "",
            "architecture": lines[2].strip() if len(lines) > 2 else "",
            "dependencies": lines[3:],
        }

    def _secure_artifact_dir(self, request_id):
        if not REQUEST_ID.fullmatch(str(request_id)):
            raise StoreControlError("invalid_request", "商店请求格式无效。", 2)
        self.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self.artifact_root,):
            info = directory.lstat()
            if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                    or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)):
                raise StoreControlError("runtime_untrusted", "软件包缓存目录不可信。", 2)
        target = self.artifact_root / str(request_id)
        if target.exists() and target.is_symlink():
            raise StoreControlError("runtime_untrusted", "软件包事务目录不得是符号链接。", 2)
        target.mkdir(mode=0o700, exist_ok=True)
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise StoreControlError("runtime_untrusted", "软件包事务目录不可信。", 2)
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise StoreControlError("runtime_untrusted", "软件包事务目录权限过宽。", 2)
        return target

    @staticmethod
    def _spark_filename(resolved):
        filename = str(resolved.get("artifact_filename") or "")
        if not filename:
            filename = pathlib.PurePosixPath(
                urllib.parse.urlsplit(str(resolved.get("download_url") or "")).path
            ).name
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+ -]{0,127}\.deb", filename)
                or pathlib.PurePath(filename).name != filename):
            raise StoreControlError("provider_unavailable", "星火软件包文件名不安全。", 9)
        return filename

    def _spark_install(self, request, item, resolved, action):
        if str(item.get("install_method") or "") == "spark-wine-deb":
            raise StoreControlError(
                "toolbox_required",
                "该星火 Wine 软件需要兼容声明/工具箱，不能通过普通 APT 安装。", 9)
        url = str(resolved.get("download_url") or "")
        parsed = urllib.parse.urlsplit(url)
        if (not hasattr(self.core, "SparkPublicProvider")
                or not self.core.SparkPublicProvider._safe_host(url)):
            raise StoreControlError("provider_unavailable", "星火下载地址未通过来源白名单校验。", 9)
        digest = str((resolved.get("identity") or {}).get("sha256") or "").lower()
        if not self.core.SHA256.fullmatch(digest):
            raise StoreControlError("provider_unavailable", "星火软件包缺少签名索引 SHA256。", 9)
        package_name = str(item.get("package_name") or "").strip().lower()
        if self._legacy_package_name(package_name):
            raise StoreControlError(
                "legacy_dependency",
                "该软件包属于已退役的 Spark/APM/ACE 运行时，已拒绝安装。", 9)
        resolved_package = str(resolved.get("package_name") or "").strip().lower()
        if not package_name or resolved_package != package_name:
            raise StoreControlError("identity_mismatch", "软件包身份与签名索引不一致。", 9)
        item_digest = str((item.get("identity") or {}).get("sha256") or "").lower()
        if item_digest and item_digest != digest:
            raise StoreControlError("identity_mismatch", "软件包身份与签名索引不一致。", 9)
        filename = self._spark_filename(resolved)
        if pathlib.PurePosixPath(parsed.path).name != filename:
            raise StoreControlError("provider_unavailable", "星火下载路径与索引文件名不一致。", 9)
        artifact_dir = self._secure_artifact_dir(request.request_id)
        destination = artifact_dir / filename
        self._journal(request, "downloading")
        try:
            downloaded = self.downloader.download(url, destination, digest)
        except StoreControlError:
            raise
        except Exception as exc:
            state, message = self._apt_error(str(exc))
            raise StoreControlError(state, message) from exc
        try:
            info = destination.lstat()
        except OSError as exc:
            raise StoreControlError("integrity_failed", "星火软件包下载结果不可用。", 9) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise StoreControlError("integrity_failed", "星火软件包下载结果不是普通文件。", 9)
        returned_digest = str(downloaded.get("sha256") or "").lower() if isinstance(downloaded, dict) else ""
        if not isinstance(downloaded, dict) or not downloaded.get("ok") or returned_digest != digest:
            destination.unlink(missing_ok=True)
            raise StoreControlError("integrity_failed", "星火软件包 SHA256 校验失败。", 9)
        actual_digest = hashlib.sha256()
        try:
            with destination.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    actual_digest.update(chunk)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise StoreControlError("integrity_failed", "星火软件包无法读取以完成校验。", 9) from exc
        if actual_digest.hexdigest() != digest:
            destination.unlink(missing_ok=True)
            raise StoreControlError("integrity_failed", "星火软件包 SHA256 校验失败。", 9)
        self._journal(request, "verifying")
        rc, output, error = self._call((
            "dpkg-deb", "--field", str(destination),
            "Package", "Version", "Architecture", "Depends", "Pre-Depends",
        ), timeout=30)
        if rc != 0:
            raise StoreControlError("invalid_package", "下载的软件包无法读取元数据。", 9)
        metadata = self._deb_metadata(output)
        expected_version = str(resolved.get("resolved_version") or resolved.get("version") or "")
        signed_architecture = str(
            resolved.get("resolved_architecture")
            or (resolved.get("identity") or {}).get("architecture")
            or ""
        ).strip().casefold()
        actual_package = str(metadata.get("package") or "").strip().casefold()
        actual_version = str(metadata.get("version") or "").strip()
        actual_architecture = str(metadata.get("architecture") or "").strip().casefold()
        if (actual_package != package_name or actual_version != expected_version
                or not signed_architecture or actual_architecture != signed_architecture):
            raise StoreControlError("identity_mismatch", "软件包身份与签名索引不一致。", 9)
        item_architecture = str(
            (item.get("identity") or {}).get("architecture")
            or (item.get("architectures") or [""])[0]
        ).strip().casefold()
        if item_architecture and item_architecture != signed_architecture:
            raise StoreControlError("identity_mismatch", "软件包架构与签名索引不一致。", 9)
        if actual_architecture not in {"amd64", "all"}:
            raise StoreControlError("unsupported_architecture", "软件包架构不受支持。", 9)
        dependencies = "\n".join(metadata.get("dependencies") or [])
        if self._has_legacy_spark_dependency(dependencies):
            raise StoreControlError(
                "legacy_dependency", "该星火软件包依赖已退役的 Spark/APM/ACE 运行时，已拒绝安装。", 9)
        self._journal(request, "awaiting_authorization")
        self._journal(request, "installing")
        rc, _output, error = self._call((
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "Acquire::Retries=3", "--no-install-recommends",
            "install", str(destination),
        ), timeout=900)
        if rc != 0:
            state, message = self._apt_error(error)
            raise StoreControlError(state, message)
        self._journal(request, "readback")
        state = self._installed(package_name)
        if (not state["installed"] or state["version"] != expected_version
                or str(state.get("architecture") or "").strip().casefold() != signed_architecture):
            raise StoreControlError("readback_failed", "软件操作结束，但版本读回不一致。")
        try:
            destination.unlink(missing_ok=True)
            artifact_dir.rmdir()
        except OSError:
            pass
        return state

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
                "/usr/local/bin/ming-phone-desktop", "--sync",
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
        _claimed_path, request = self._load_request(uid, action, request_id)
        self._journal(request, "resolving")
        if request.provider == "spark-public" and action in ("install", "update"):
            policy = self._spark_policy()
            if not policy.get("installation_enabled"):
                raise StoreControlError(
                    "installation_disabled",
                    str(policy.get("installation_disabled_reason")
                        or "星火公开目录当前仅供浏览，不能安装。"), 9)
        provider = self._provider(request)
        if (request.provider == "spark-public"
                and action in ("install", "update")
                and hasattr(provider, "refresh_catalog")):
            try:
                provider.refresh_catalog()
            except Exception as exc:
                raise StoreControlError(
                    "network_failed", "星火目录无法刷新并验证签名，请检查网络后重试。", 9
                ) from exc
            if (getattr(provider, "catalog_state", "unavailable") != "ready"
                    or getattr(provider, "cache_trusted", False) is not True):
                raise StoreControlError(
                    "provider_unavailable",
                    "星火安装必须在本轮联网刷新并完成签名校验后进行。", 9)
        item = provider.get(request.app_id)
        if request.provider == "wine-official":
            raise StoreControlError(
                "toolbox_required",
                "Wine 应用必须由 Ming 工具箱下载、校验和安装，商店不会直接执行 Windows 安装文件。",
                9,
            )
        package = item["package_name"]
        if action == "remove" and (
                bool(item.get("protected")) or self._is_protected(package)):
            raise StoreControlError("protected_package", "该软件是系统核心组件，不能卸载。", 8)

        if action in ("install", "update"):
            if self._legacy_package_name(package):
                raise StoreControlError(
                    "legacy_dependency",
                    "该软件包属于已退役的 Spark/APM/ACE 运行时，已拒绝安装。", 9)
            if str(item.get("install_method") or "") == "spark-wine-deb":
                raise StoreControlError(
                    "toolbox_required",
                    "该星火 Wine 软件需要兼容声明/工具箱，不能通过普通 APT 安装。", 9)
            resolved = provider.resolve(request.app_id)
            version = resolved.get("resolved_version") or resolved.get("version")
            if request.expected_version and request.expected_version != version:
                raise StoreControlError("candidate_changed", "软件版本已变化，请刷新页面后重试。", 9)
            if request.provider == "spark-public":
                state = self._spark_install(request, item, resolved, action)
            else:
                target = resolved.get("apt_target")
                if not target:
                    raise StoreControlError("provider_unavailable", "该来源尚未开放安装。", 9)
                rc, package_metadata, _metadata_error = self._call(
                    ("apt-cache", "show", package), timeout=15)
                if rc == 0 and self._has_legacy_spark_dependency(package_metadata):
                    raise StoreControlError(
                        "legacy_dependency",
                        "该软件包依赖已退役的 Spark/APM/ACE 运行时，已拒绝安装。", 9)
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
                "provider": request.provider, "app_id": request.app_id,
                "message": "软件操作已完成，但桌面入口刷新失败。可在商店中安全重试刷新。",
                "refresh_failed": failed,
            }
        self._journal(request, "succeeded")
        return {
            "ok": True, "state": "succeeded", "installed_state": state,
            "provider": request.provider, "app_id": request.app_id,
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
