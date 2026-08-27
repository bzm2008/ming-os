#!/usr/bin/env python3
"""Trusted catalog, provider, transaction and download logic for Ming Store."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.parse
import urllib.request


CATALOG_SCHEMA = "ming.store.catalog.v1"
TRANSACTION_SCHEMA = "ming.store.transaction.v1"
ALLOWED_PROVIDERS = ("ming-official", "debian-apt", "vendor-official")
ALLOWED_ACTIONS = ("install", "update", "remove", "refresh")
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StoreError(RuntimeError):
    pass


class InvalidCatalog(StoreError):
    pass


class ProviderUnavailable(StoreError):
    pass


class InvalidTransaction(StoreError):
    pass


class InvalidTransition(StoreError):
    pass


class DownloadRejected(StoreError):
    pass


class DownloadFailed(StoreError):
    pass


class IntegrityError(StoreError):
    pass


def _default_runner(command, timeout=15):
    completed = subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout,
        check=False, shell=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _catalog_root(catalog_root=None):
    return pathlib.Path(catalog_root or pathlib.Path(__file__).with_name("ming-store-catalog"))


class Provider:
    """Stable source-adapter interface used by the UI and transaction service."""

    source_id = ""

    def refresh_catalog(self):
        raise NotImplementedError

    def search(self, query):
        raise NotImplementedError

    def get(self, app_id):
        raise NotImplementedError

    def resolve(self, app_id):
        raise NotImplementedError

    def installed_state(self, app_id):
        raise NotImplementedError


class CatalogProvider(Provider):
    def __init__(self, source_id, catalog_root=None):
        if source_id not in ALLOWED_PROVIDERS:
            raise ValueError("不受支持的软件来源。")
        self.source_id = source_id
        self.catalog_root = _catalog_root(catalog_root)
        self._items = []

    def _load(self):
        path = self.catalog_root / (self.source_id + ".json")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InvalidCatalog("软件目录无法读取：%s" % exc) from exc
        if document.get("schema") != CATALOG_SCHEMA:
            raise InvalidCatalog("软件目录 schema 不受支持。")
        source = document.get("source")
        items = document.get("applications")
        if not isinstance(source, dict) or source.get("id") != self.source_id:
            raise InvalidCatalog("软件目录来源身份不匹配。")
        if not isinstance(items, list):
            raise InvalidCatalog("软件目录 applications 字段无效。")
        checked = []
        seen = set()
        for raw in items:
            item = self._validate_item(raw)
            if item["app_id"] in seen:
                raise InvalidCatalog("软件目录包含重复 app_id。")
            seen.add(item["app_id"])
            item["source_id"] = self.source_id
            checked.append(item)
        self._items = checked
        return copy.deepcopy(checked)

    def _validate_item(self, raw):
        if not isinstance(raw, dict):
            raise InvalidCatalog("软件条目必须是对象。")
        item = copy.deepcopy(raw)
        for field in (
                "app_id", "name", "package_name", "version", "architectures",
                "install_method", "dependencies", "license", "identity",
                "enabled", "protected"):
            if field not in item:
                raise InvalidCatalog("软件条目缺少字段：%s" % field)
        if not SAFE_ID.fullmatch(str(item["app_id"])):
            raise InvalidCatalog("软件 app_id 无效。")
        if not SAFE_ID.fullmatch(str(item["package_name"])):
            raise InvalidCatalog("软件包名无效。")
        if not isinstance(item["architectures"], list) or "amd64" not in item["architectures"]:
            raise InvalidCatalog("软件架构不受支持。")
        if not isinstance(item["identity"], dict):
            raise InvalidCatalog("软件身份信息无效。")
        if not isinstance(item["dependencies"], list) or not all(
                SAFE_ID.fullmatch(str(dependency)) for dependency in item["dependencies"]):
            raise InvalidCatalog("软件依赖字段无效。")
        if not isinstance(item["license"], str) or not item["license"].strip():
            raise InvalidCatalog("软件许可证说明缺失。")
        if not isinstance(item["enabled"], bool) or not isinstance(item["protected"], bool):
            raise InvalidCatalog("软件启用或保护状态无效。")
        identity_type = str(item["identity"].get("type") or "")
        if self.source_id == "debian-apt" and identity_type != "apt-repository-signature":
            raise InvalidCatalog("APT 软件必须使用仓库签名身份。")
        if self.source_id == "vendor-official" and identity_type != "sha256":
            raise InvalidCatalog("厂商软件必须使用固定 SHA256 身份。")
        return item

    def refresh_catalog(self):
        return self._load()

    def _ensure_loaded(self):
        if not self._items:
            self._load()

    def search(self, query):
        self._ensure_loaded()
        needle = str(query or "").casefold().strip()
        result = []
        for item in self._items:
            haystack = " ".join((
                str(item.get("name", "")), str(item.get("app_id", "")),
                str(item.get("package_name", "")),
                " ".join(str(value) for value in item.get("categories", [])),
            )).casefold()
            if not needle or needle in haystack:
                result.append(copy.deepcopy(item))
        return result

    def get(self, app_id):
        self._ensure_loaded()
        for item in self._items:
            if item["app_id"] == app_id:
                return copy.deepcopy(item)
        raise KeyError(app_id)

    def resolve(self, app_id):
        item = self.get(app_id)
        if not item.get("enabled", False):
            raise ProviderUnavailable("软件来源尚未固定版本和校验信息。")
        return item

    def installed_state(self, app_id):
        self.get(app_id)
        return {"installed": False, "version": None, "architecture": None}


class MingOfficialProvider(CatalogProvider):
    def __init__(self, catalog_root=None):
        super().__init__("ming-official", catalog_root=catalog_root)


class VendorOfficialProvider(CatalogProvider):
    def __init__(self, catalog_root=None):
        super().__init__("vendor-official", catalog_root=catalog_root)

    def resolve(self, app_id):
        item = self.get(app_id)
        digest = item.get("identity", {}).get("sha256")
        url = item.get("download_url")
        if (
            not item.get("enabled") or not item.get("version")
            or not isinstance(digest, str) or not SHA256.fullmatch(digest)
            or not isinstance(url, str) or not url.startswith("https://")
        ):
            raise ProviderUnavailable("厂商软件尚未固定官方版本与 SHA256，暂不可安装。")
        return item


class DebianAptProvider(CatalogProvider):
    def __init__(self, catalog_root=None, runner=None):
        super().__init__("debian-apt", catalog_root=catalog_root)
        self.runner = runner or _default_runner

    def resolve(self, app_id):
        item = super().resolve(app_id)
        package = item["package_name"]
        rc, output, error = self.runner(["apt-cache", "policy", package], timeout=15)
        match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", output or "", re.MULTILINE)
        if rc != 0 or not match or match.group(1) in ("(none)", "none"):
            raise ProviderUnavailable("APT 仓库中没有可安装版本。")
        version = match.group(1)
        rc, output, error = self.runner(["dpkg", "--print-architecture"], timeout=15)
        architecture = (output or "").strip()
        if rc != 0 or architecture not in item["architectures"]:
            raise ProviderUnavailable("当前系统架构不受该软件支持。")
        item["resolved_version"] = version
        item["resolved_architecture"] = architecture
        item["apt_target"] = "%s=%s" % (package, version)
        return item

    def installed_state(self, app_id):
        item = self.get(app_id)
        rc, output, error = self.runner(
            ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}\t${Architecture}", item["package_name"]],
            timeout=15,
        )
        if rc != 0:
            return {"installed": False, "version": None, "architecture": None}
        fields = (output or "").strip().split("\t")
        installed = len(fields) == 3 and fields[0].strip().startswith("ii")
        return {
            "installed": installed,
            "version": fields[1].strip() if installed else None,
            "architecture": fields[2].strip() if installed else None,
        }


class MemoryProvider(Provider):
    """Small in-memory adapter useful for merging cached or signed catalogs."""

    def __init__(self, source_id, items):
        if source_id not in ALLOWED_PROVIDERS:
            raise ValueError("不受支持的软件来源。")
        self.source_id = source_id
        self.items = [dict(item, source_id=source_id) for item in items]

    def refresh_catalog(self):
        return copy.deepcopy(self.items)

    def search(self, query):
        needle = str(query or "").casefold()
        return [
            copy.deepcopy(item) for item in self.items
            if not needle or needle in (str(item.get("name", "")) + " " + str(item.get("package_name", ""))).casefold()
        ]

    def get(self, app_id):
        for item in self.items:
            if item.get("app_id") == app_id:
                return copy.deepcopy(item)
        raise KeyError(app_id)

    def resolve(self, app_id):
        item = self.get(app_id)
        if not item.get("enabled"):
            raise ProviderUnavailable("软件条目当前不可用。")
        return item

    def installed_state(self, app_id):
        self.get(app_id)
        return {"installed": False, "version": None, "architecture": None}


class ProviderRegistry:
    def __init__(self, providers=None):
        providers = providers or {
            "ming-official": MingOfficialProvider(),
            "debian-apt": DebianAptProvider(),
            "vendor-official": VendorOfficialProvider(),
        }
        for source_id, provider in providers.items():
            if source_id not in ALLOWED_PROVIDERS:
                raise ValueError("不允许注册的软件来源：%s" % source_id)
            if getattr(provider, "source_id", source_id) != source_id:
                raise ValueError("Provider 身份不匹配。")
        self.providers = dict(providers)

    def get(self, source_id):
        try:
            return self.providers[source_id]
        except KeyError as exc:
            raise ProviderUnavailable("软件来源不可用。") from exc


class StoreCatalog:
    def __init__(self, registry=None):
        self.registry = registry or ProviderRegistry()

    def search(self, query, source_id=None):
        source_ids = [source_id] if source_id else list(self.registry.providers)
        result = []
        positions = {}
        for current in source_ids:
            provider = self.registry.get(current)
            try:
                items = provider.search(query)
            except StoreError:
                continue
            for item in items:
                identity = item.get("package_name") or item.get("app_id")
                if identity in positions:
                    continue
                positions[identity] = len(result)
                result.append(item)
        return result

    def get(self, source_id, app_id):
        return self.registry.get(source_id).get(app_id)

    def installed_state(self, source_id, app_id):
        return self.registry.get(source_id).installed_state(app_id)


class StoreTransactionRequest:
    FIELDS = {
        "schema", "request_id", "uid", "action", "provider", "app_id",
        "expected_version", "created_at",
    }

    def __init__(self, payload):
        self.schema = payload["schema"]
        self.request_id = payload["request_id"]
        self.uid = payload["uid"]
        self.action = payload["action"]
        self.provider = payload["provider"]
        self.app_id = payload["app_id"]
        self.expected_version = payload["expected_version"]
        self.created_at = payload["created_at"]

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != cls.FIELDS:
            raise InvalidTransaction("安装请求字段不完整或包含未授权字段。")
        if payload.get("schema") != TRANSACTION_SCHEMA:
            raise InvalidTransaction("安装请求 schema 不受支持。")
        if not REQUEST_ID.fullmatch(str(payload.get("request_id", ""))):
            raise InvalidTransaction("request_id 无效。")
        if not isinstance(payload.get("uid"), int) or payload["uid"] < 0:
            raise InvalidTransaction("uid 无效。")
        if payload.get("action") not in ALLOWED_ACTIONS:
            raise InvalidTransaction("安装操作不在白名单内。")
        if payload.get("provider") not in ALLOWED_PROVIDERS:
            raise InvalidTransaction("软件来源不受信任。")
        if not SAFE_ID.fullmatch(str(payload.get("app_id", ""))):
            raise InvalidTransaction("app_id 无效。")
        expected = payload.get("expected_version")
        if expected is not None and (
            not isinstance(expected, str) or len(expected) > 128
            or any(character in expected for character in "\r\n\0;|&`$<>")
        ):
            raise InvalidTransaction("期望版本无效。")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(payload.get("created_at", ""))):
            raise InvalidTransaction("created_at 无效。")
        return cls(payload)

    def to_dict(self):
        return {field: getattr(self, field) for field in self.FIELDS}


class TransactionStateMachine:
    TRANSITIONS = {
        "created": {"resolving", "failed"},
        "resolving": {"downloading", "awaiting_authorization", "failed"},
        "downloading": {"verifying", "failed"},
        "verifying": {"awaiting_authorization", "failed"},
        "awaiting_authorization": {"installing", "failed"},
        "installing": {"readback", "failed"},
        "readback": {"refreshing", "failed"},
        "refreshing": {"succeeded", "refresh_warning", "failed"},
        "succeeded": set(),
        "refresh_warning": set(),
        "failed": set(),
    }

    def __init__(self):
        self.state = "created"

    @property
    def terminal(self):
        return self.state in ("succeeded", "refresh_warning", "failed")

    def transition(self, state):
        if state not in self.TRANSITIONS.get(self.state, set()):
            raise InvalidTransition("不允许从 %s 跳转到 %s。" % (self.state, state))
        self.state = state
        return state


_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|cookie|authorization|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_HOME = re.compile(r"(?i)(?:/home/|[A-Z]:\\Users\\)[^/\\\s]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_MAC = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
_SENSITIVE_KEYS = {
    "password", "passwd", "token", "cookie", "authorization", "secret",
    "api_key", "apikey", "private_key",
}


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).casefold() in _SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    text = _SECRET.sub(lambda match: match.group(1) + "=[REDACTED]", value)
    text = _HOME.sub("/home/[REDACTED]", text)
    text = _MAC.sub("[REDACTED]", text)
    text = _IPV4.sub("[REDACTED]", text)
    return text


class TransactionJournal:
    def __init__(self, path):
        self.path = pathlib.Path(path)

    def write(self, event):
        if not isinstance(event, dict):
            raise TypeError("日志事件必须是对象。")
        payload = redact(dict(event))
        payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


class SecureDownloader:
    def __init__(self, opener=None, sleeper=None, attempts=3, timeout=30, chunk_size=1024 * 256):
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep
        self.attempts = attempts
        self.timeout = timeout
        self.chunk_size = chunk_size

    @staticmethod
    def _validate(url, expected_sha256):
        parsed = urllib.parse.urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise DownloadRejected("只允许无账号信息的 HTTPS 下载地址。")
        digest = str(expected_sha256 or "").lower()
        if not SHA256.fullmatch(digest):
            raise DownloadRejected("SHA256 格式无效。")
        return digest

    def download(self, url, destination, expected_sha256):
        expected = self._validate(url, expected_sha256)
        destination = pathlib.Path(destination)
        partial = destination.with_suffix(destination.suffix + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or partial.is_symlink():
            raise DownloadRejected("下载目标不能是符号链接。")
        last_error = None
        for attempt in range(1, self.attempts + 1):
            offset = partial.stat().st_size if partial.is_file() else 0
            headers = {"User-Agent": "Ming-Store/1"}
            if offset:
                headers["Range"] = "bytes=%d-" % offset
            request = urllib.request.Request(str(url), headers=headers, method="GET")
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else str(url)
                    if urllib.parse.urlsplit(final_url).scheme != "https":
                        raise DownloadRejected("下载地址被重定向到不安全的非 HTTPS 来源。")
                    status = response.getcode()
                    append = bool(offset and status == 206)
                    mode = "ab" if append else "wb"
                    with partial.open(mode) as stream:
                        while True:
                            chunk = response.read(self.chunk_size)
                            if not chunk:
                                break
                            stream.write(chunk)
                actual = hashlib.sha256(partial.read_bytes()).hexdigest()
                if actual != expected:
                    partial.unlink(missing_ok=True)
                    destination.unlink(missing_ok=True)
                    raise IntegrityError("下载文件 SHA256 校验失败。")
                os.replace(partial, destination)
                return {
                    "ok": True, "state": "verified", "sha256": actual,
                    "attempts": attempt, "bytes": destination.stat().st_size,
                }
            except IntegrityError:
                raise
            except (OSError, TimeoutError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    self.sleeper(min(attempt, 2))
        raise DownloadFailed("下载失败，已重试 %d 次：%s" % (self.attempts, redact(str(last_error))))


def default_catalog(catalog_root=None, runner=None):
    root = _catalog_root(catalog_root)
    return StoreCatalog(ProviderRegistry({
        "ming-official": MingOfficialProvider(root),
        "debian-apt": DebianAptProvider(root, runner=runner),
        "vendor-official": VendorOfficialProvider(root),
    }))
