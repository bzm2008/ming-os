#!/usr/bin/env python3
"""Trusted catalog, provider, transaction and download logic for Ming Store."""

from __future__ import annotations

import copy
import datetime
import email.utils
import gzip
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    import yaml
except ImportError:  # AppStream YAML is optional on minimal installed systems.
    yaml = None


CATALOG_SCHEMA = "ming.store.catalog.v1"
TRANSACTION_SCHEMA = "ming.store.transaction.v1"
ALLOWED_PROVIDERS = (
    "ming-official", "debian-apt", "vendor-official", "wine-official", "spark-public",
)
ALLOWED_ACTIONS = ("install", "update", "remove", "refresh")
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SAFE_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]{0,127}\Z")
SAFE_WINE_APP = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
SAFE_WINE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,127}\Z")
SAFE_WINE_EXECUTABLE = re.compile(r"(?:[A-Za-z0-9._ +()-]+/)*[A-Za-z0-9._ +()-]+\.exe\Z", re.IGNORECASE)
DEFAULT_APPSTREAM_PATHS = (
    "/var/cache/app-info/xmls/*.xml",
    "/var/cache/app-info/xmls/*.xml.gz",
    "/var/cache/swcatalog/xml/*.xml",
    "/var/cache/swcatalog/xml/*.xml.gz",
    "/var/lib/app-info/xmls/*.xml",
    "/var/lib/app-info/xmls/*.xml.gz",
    "/var/cache/swcatalog/yaml/*.yml",
    "/var/cache/swcatalog/yaml/*.yaml",
    "/var/cache/swcatalog/yaml/*.yml.gz",
    "/var/cache/swcatalog/yaml/*.yaml.gz",
    "/var/lib/swcatalog/yaml/*.yml",
    "/var/lib/swcatalog/yaml/*.yaml",
    "/var/lib/swcatalog/yaml/*.yml.gz",
    "/usr/share/ming-os/appstream/*.yml",
    "/usr/share/ming-os/appstream/*.yaml",
    "/usr/share/ming-os/appstream/*.yml.gz",
    "/usr/share/ming-os/appstream/*.yaml.gz",
    "/var/lib/swcatalog/yaml/*.yaml.gz",
)
# These paths are relative to a target rootfs.  Keep the source metadata
# separate from Ming's JSON catalog: the release gate must count applications
# that are actually shipped in the image, not synthetic catalog entries.
ROOTFS_APPSTREAM_PATTERNS = (
    "usr/share/metainfo/*.xml",
    "usr/share/metainfo/*.xml.gz",
    "usr/share/app-info/xmls/*.xml",
    "usr/share/app-info/xmls/*.xml.gz",
    "usr/share/swcatalog/xml/*.xml",
    "usr/share/swcatalog/xml/*.xml.gz",
    "var/cache/app-info/xmls/*.xml",
    "var/cache/app-info/xmls/*.xml.gz",
    "var/cache/swcatalog/xml/*.xml",
    "var/cache/swcatalog/xml/*.xml.gz",
    "var/lib/app-info/xmls/*.xml",
    "var/lib/app-info/xmls/*.xml.gz",
    "var/lib/swcatalog/xml/*.xml",
    "var/lib/swcatalog/xml/*.xml.gz",
    "var/cache/swcatalog/yaml/*.yml",
    "var/cache/swcatalog/yaml/*.yaml",
    "var/cache/swcatalog/yaml/*.yml.gz",
    "var/cache/swcatalog/yaml/*.yaml.gz",
    "var/lib/swcatalog/yaml/*.yml",
    "var/lib/swcatalog/yaml/*.yaml",
    "var/lib/swcatalog/yaml/*.yml.gz",
    "var/lib/swcatalog/yaml/*.yaml.gz",
    "usr/share/ming-os/appstream/*.yml",
    "usr/share/ming-os/appstream/*.yaml",
    "usr/share/ming-os/appstream/*.yml.gz",
    "usr/share/ming-os/appstream/*.yaml.gz",
)
MIN_ROOTFS_APPSTREAM_APPS = 1000
SPARK_SOURCE_ID = "spark-public"
SPARK_BASE_URL = "https://cdn.d.store.deepinos.org.cn"
SPARK_CATEGORIES = (
    "network", "chat", "music", "video", "image_graphics", "games",
    "office", "reading", "development", "tools", "themes", "others",
)
SPARK_KEY_FINGERPRINT = "9D9AA859F75024B1A1ECE16E0E41D354A29A440C"
SPARK_ALLOWED_HOSTS = frozenset({
    "cdn.d.store.deepinos.org.cn",
    "mirrors.sdu.edu.cn",
})


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


def _local_name(tag):
    return str(tag).rsplit("}", 1)[-1]


def _xml_child(element, name):
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _xml_children(element, name):
    return [child for child in list(element) if _local_name(child.tag) == name]


def _xml_descendants(element, name):
    return [child for child in element.iter() if _local_name(child.tag) == name]


def _xml_text(element, path, default=""):
    current = element
    for part in path.split("/"):
        current = _xml_child(current, part)
        if current is None:
            return default
    return str(current.text or "").strip() or default


def parse_appstream_xml(document):
    """Convert trusted local AppStream metadata into Ming Store APT items."""
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, TypeError, ValueError):
        return []

    items = []
    seen_packages = set()
    for component in _xml_descendants(root, "component"):
        if component.get("type") != "desktop-application":
            continue
        package = _xml_text(component, "pkgname").lower()
        if not SAFE_PACKAGE.fullmatch(package) or package in seen_packages:
            continue

        architectures = set()
        for node in _xml_descendants(component, "architecture"):
            value = str(node.text or node.get("arch") or "").strip().lower()
            if value:
                architectures.add(value)
        compatible = {"amd64", "x86_64", "all", "any", "noarch", "universal"}
        if architectures and architectures.isdisjoint(compatible):
            continue

        name = _xml_text(component, "name") or package
        component_id = _xml_text(component, "id")
        desktop_ids = []
        for launchable in _xml_children(component, "launchable"):
            desktop_id = str(launchable.text or "").strip()
            if launchable.get("type") == "desktop-id" and desktop_id.endswith(".desktop"):
                desktop_ids.append(desktop_id)
        if not desktop_ids and component_id.endswith(".desktop"):
            desktop_ids.append(component_id)

        categories_node = _xml_child(component, "categories")
        categories = [
            str(node.text or "").strip()
            for node in _xml_children(
                categories_node if categories_node is not None else component, "category")
            if str(node.text or "").strip()
        ]
        license_name = (
            _xml_text(component, "project_license")
            or _xml_text(component, "metadata_license")
            or "软件包元数据未声明许可证"
        )
        app_id = re.sub(r"[^a-z0-9._-]", "-", package)
        items.append({
            "app_id": app_id,
            "name": name,
            "summary": _xml_text(component, "summary"),
            "package_name": package,
            "version": "candidate",
            "architectures": ["amd64"],
            "install_method": "apt",
            "dependencies": [],
            "license": license_name,
            "categories": categories or ["其他"],
            "desktop_ids": list(dict.fromkeys(desktop_ids)),
            "identity": {"type": "apt-repository-signature", "required": True},
            "enabled": True,
            "protected": False,
        })
        seen_packages.add(package)
    return items


def _dep11_localized(value, default=""):
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, dict):
        for key in ("zh-Hans-CN", "zh_CN", "C", "en"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return default


def _dep11_list(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def parse_dep11_yaml(document):
    """Convert signed Debian DEP-11 YAML components into store items."""
    if yaml is None:
        return []
    try:
        documents = yaml.safe_load_all(document)
    except (TypeError, ValueError, yaml.YAMLError):
        return []
    items = []
    seen_packages = set()
    try:
        for component in documents:
            if not isinstance(component, dict) or component.get("Type") != "desktop-application":
                continue
            package = str(component.get("Package") or component.get("PkgName") or "").strip().lower()
            if not SAFE_PACKAGE.fullmatch(package) or package in seen_packages:
                continue
            architectures = set(item.lower() for item in _dep11_list(
                component.get("Architectures") or component.get("Architecture")))
            compatible = {"amd64", "x86_64", "all", "any", "noarch", "universal"}
            if architectures and architectures.isdisjoint(compatible):
                continue
            component_id = str(component.get("ID") or "").strip()
            launchables = component.get("Launchable") or component.get("Launchables")
            if isinstance(launchables, dict):
                launchables = [launchables]
            desktop_ids = []
            for launchable in launchables if isinstance(launchables, list) else []:
                if isinstance(launchable, dict):
                    value = launchable.get("value") or launchable.get("ID") or launchable.get("id")
                    kind = launchable.get("type") or launchable.get("Type")
                    if kind and str(kind).lower() not in ("desktop-id", "desktop_id"):
                        continue
                else:
                    value = launchable
                value = str(value or "").strip()
                if value.endswith(".desktop"):
                    desktop_ids.append(value)
            if not desktop_ids and component_id.endswith(".desktop"):
                desktop_ids.append(component_id)
            categories = _dep11_list(component.get("Categories") or component.get("Category"))
            item = {
                "app_id": re.sub(r"[^a-z0-9._-]", "-", package),
                "name": _dep11_localized(component.get("Name"), package),
                "summary": _dep11_localized(component.get("Summary"), ""),
                "package_name": package,
                "version": "candidate",
                "architectures": ["amd64"],
                "install_method": "apt",
                "dependencies": [],
                "license": str(component.get("ProjectLicense") or component.get("MetadataLicense") or "软件包元数据未声明许可证"),
                "categories": categories or ["其他"],
                "desktop_ids": list(dict.fromkeys(desktop_ids)),
                "identity": {"type": "apt-repository-signature", "required": True},
                "enabled": True,
                "protected": False,
            }
            items.append(item)
            seen_packages.add(package)
    except (TypeError, AttributeError):
        return items
    return items


def _rootfs_appstream_paths(root, paths=None):
    """Return regular AppStream metadata files contained by *root*.

    The image may expose the same metadata through more than one glob.  A
    physical-file key keeps the inventory deterministic and avoids counting a
    duplicate XML file twice.  Symlinks are intentionally ignored so a build
    cannot satisfy the release gate by pointing outside the rootfs.
    """
    root = pathlib.Path(root)
    candidates = []
    if paths is None:
        for pattern in ROOTFS_APPSTREAM_PATTERNS:
            candidates.extend(sorted(root.glob(pattern)))
    else:
        for raw_path in paths:
            path = pathlib.Path(raw_path)
            candidates.append(path if path.is_absolute() else root / path)

    result = []
    seen = set()
    for path in candidates:
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            continue
        try:
            relative = path.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError:
            # Explicit paths must not let callers escape the target rootfs.
            continue
        key = (metadata.st_dev, metadata.st_ino)
        if key in seen:
            continue
        seen.add(key)
        result.append(root / relative)
    return tuple(result)


def _read_appstream_document(path):
    path = pathlib.Path(path)
    try:
        if path.suffix.casefold() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                return stream.read()
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def scan_appstream_rootfs(root, paths=None):
    """Inventory valid desktop applications present in a built rootfs.

    Only AppStream XML metadata under the target rootfs is considered.  Ming
    catalog JSON files, arbitrary paths and symlinked metadata are excluded by
    construction.  Package names are the identity boundary because one
    package can be represented by several AppStream files.
    """
    metadata_paths = _rootfs_appstream_paths(root, paths=paths)
    items_by_package = {}
    metadata_bytes = 0
    for path in metadata_paths:
        try:
            metadata_bytes += path.stat().st_size
        except OSError:
            continue
        parser = parse_dep11_yaml if path.suffix.casefold() in {".yml", ".yaml"} or path.name.casefold().endswith((".yml.gz", ".yaml.gz")) else parse_appstream_xml
        for item in parser(_read_appstream_document(path)):
            items_by_package.setdefault(item["package_name"], item)
    return {
        "count": len(items_by_package),
        "items": list(items_by_package.values()),
        "paths": [str(path) for path in metadata_paths],
        "metadata_bytes": metadata_bytes,
    }


def validate_appstream_rootfs(root, minimum=MIN_ROOTFS_APPSTREAM_APPS, inventory=None):
    """Enforce the release inventory contract for a target rootfs."""
    try:
        minimum = int(minimum)
    except (TypeError, ValueError) as error:
        raise InvalidCatalog("AppStream 最低应用数量无效。") from error
    if minimum < 1:
        raise InvalidCatalog("AppStream 最低应用数量必须为正数。")
    inventory = inventory or scan_appstream_rootfs(root)
    paths = inventory.get("paths")
    count = inventory.get("count")
    if not paths:
        raise InvalidCatalog("构建 rootfs 中没有可读取的 AppStream 元数据。")
    if not isinstance(count, int) or count < minimum:
        raise InvalidCatalog(
            "构建 rootfs 的 AppStream 仅包含 %s 个有效桌面应用，至少需要 %d 个。"
            % (count if isinstance(count, int) else "未知", minimum)
        )
    return inventory


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
        if not SAFE_PACKAGE.fullmatch(str(item["package_name"])):
            raise InvalidCatalog("软件包名无效。")
        if (not isinstance(item["architectures"], list)
                or not any(str(arch).casefold() in {"amd64", "universal"}
                           for arch in item["architectures"])):
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


class WineOfficialProvider(CatalogProvider):
    """Trusted Wine manifest adapter; installation is delegated to Toolbox.

    A Wine catalog entry is only actionable when the artifact identity is
    fixed in the image.  The provider deliberately never returns an EXE/MSI
    command or a user supplied URL to the privileged transaction helper.
    """

    PUBLIC_KEY_PATH = pathlib.Path("/etc/ming-os/store/ming-wine-catalog.minisign.pub")

    def __init__(self, catalog_root=None, home=None, verifier=None, public_key_path=None):
        super().__init__("wine-official", catalog_root=catalog_root)
        self.home = pathlib.Path(home or pathlib.Path.home())
        self.public_key_path = pathlib.Path(public_key_path or self.PUBLIC_KEY_PATH)
        self.verifier = verifier or self._verify_signature

    @staticmethod
    def _canonical_manifest(item):
        unsigned = copy.deepcopy(item)
        identity = dict(unsigned.get("identity") or {})
        identity.pop("signature", None)
        unsigned["identity"] = identity
        return json.dumps(
            {"schema": CATALOG_SCHEMA, "source": "wine-official", "application": unsigned},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    def _verify_signature(self, payload, signature, trusted_comment, public_key):
        if not public_key.is_file() or not signature:
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="ming-wine-signature-") as directory:
                root = pathlib.Path(directory)
                message = root / "manifest.json"
                detached = root / "manifest.minisig"
                message.write_text(payload, encoding="utf-8")
                detached.write_text(signature + "\n", encoding="utf-8")
                result = subprocess.run(
                    ["minisign", "-Vm", str(message), "-x", str(detached),
                     "-p", str(public_key)],
                    capture_output=True, text=True, timeout=15,
                    check=False, shell=False,
                )
                if result.returncode != 0:
                    return False
                # Minisign verifies the cryptographic signature and the
                # trusted comment separately.  Require the latter to match
                # the catalog field so a valid signature cannot be replayed
                # with a different application/version label.
                output = "%s\n%s" % (result.stdout or "", result.stderr or "")
                match = re.search(r"(?m)^Trusted comment:\s*(.*?)\s*$", output)
                return bool(match and match.group(1).strip() == str(trusted_comment).strip())
        except (OSError, subprocess.SubprocessError):
            return False

    def _validate_item(self, raw):
        item = super()._validate_item(raw)
        if item.get("install_method") != "wine-managed":
            raise InvalidCatalog("Wine 软件必须使用受控 Wine 安装方式。")
        wine_app_id = str(item.get("wine_app_id") or item.get("app_id") or "")
        if not SAFE_WINE_APP.fullmatch(wine_app_id):
            raise InvalidCatalog("Wine 应用 ID 无效。")
        item["wine_app_id"] = wine_app_id
        identity = item.get("identity") or {}
        if str(identity.get("type") or "") != "minisign":
            raise InvalidCatalog("Wine 软件必须使用 Minisign 清单身份。")
        if item.get("enabled"):
            version = str(item.get("version") or "")
            artifact = item.get("artifact")
            if (not version or version == "candidate" or not isinstance(artifact, dict)):
                raise InvalidCatalog("可安装的 Wine 软件必须固定版本和安装包。")
            url = str(artifact.get("url") or "")
            parsed = urllib.parse.urlsplit(url)
            digest = str(artifact.get("sha256") or "").lower()
            filename = str(artifact.get("filename") or "")
            executable = str(artifact.get("executable") or "")
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password
                    or not SHA256.fullmatch(digest)
                    or not SAFE_WINE_FILENAME.fullmatch(filename)
                    or pathlib.PurePath(filename).suffix.lower() not in {".exe", ".msi"}
                    or not SAFE_WINE_EXECUTABLE.fullmatch(executable)
                    or any(part in {"", ".", ".."} for part in executable.split("/"))):
                raise InvalidCatalog("Wine 安装包必须使用安全 HTTPS、SHA256 和启动文件。")
            signature = str(identity.get("signature") or "").strip()
            trusted_comment = str(identity.get("trusted_comment") or "").strip()
            if (not signature or not trusted_comment.startswith("Ming Wine manifest ")
                    or wine_app_id not in trusted_comment
                    or version not in trusted_comment):
                raise InvalidCatalog("Wine 清单签名或可信说明无效。")
            canonical = self._canonical_manifest(item)
            if not self.verifier(
                    canonical, signature, trusted_comment, self.public_key_path):
                raise InvalidCatalog("Wine 清单未通过 Minisign 密码学验签。")
            item["artifact"] = {
                "url": url, "sha256": digest, "filename": filename,
                "executable": executable,
            }
        else:
            reason = str(item.get("disabled_reason") or "").strip()
            if not reason:
                raise InvalidCatalog("不可安装的 Wine 软件必须说明原因。")
        return item

    def resolve(self, app_id):
        item = self.get(app_id)
        if not item.get("enabled"):
            raise ProviderUnavailable(
                str(item.get("disabled_reason") or "Wine 软件尚未完成来源校验，暂不可安装。"))
        artifact = item["artifact"]
        resolved = copy.deepcopy(item)
        resolved.update({
            "resolved_version": item["version"],
            "download_url": artifact["url"],
            "sha256": artifact["sha256"],
            "artifact_filename": artifact["filename"],
            "wine_executable": artifact["executable"],
        })
        return resolved

    def installed_state(self, app_id):
        item = self.get(app_id)
        app_root = self.home / ".local" / "share" / "ming-wine" / "apps"
        app_dir = app_root / item["wine_app_id"]
        metadata_path = app_dir / "metadata.json"
        prefix = app_dir / "prefix"

        def damaged():
            return {
                "installed": False, "version": None,
                "architecture": None, "state": "damaged",
            }

        controlled_parts = [
            self.home, self.home / ".local", self.home / ".local" / "share",
            self.home / ".local" / "share" / "ming-wine", app_root, app_dir,
        ]
        try:
            for controlled in controlled_parts:
                info = controlled.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    return damaged()
            app_info = app_dir.lstat()
        except FileNotFoundError:
            return {"installed": False, "version": None,
                    "architecture": None, "state": "not_installed"}
        except OSError:
            return damaged()
        if stat.S_ISLNK(app_info.st_mode) or not stat.S_ISDIR(app_info.st_mode):
            return damaged()
        try:
            prefix_info = prefix.lstat()
            metadata_info = metadata_path.lstat()
        except OSError:
            return damaged()
        if (stat.S_ISLNK(prefix_info.st_mode) or not stat.S_ISDIR(prefix_info.st_mode)
                or stat.S_ISLNK(metadata_info.st_mode) or not stat.S_ISREG(metadata_info.st_mode)):
            return damaged()
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return damaged()
        if (not isinstance(metadata, dict)
                or metadata.get("schema") != "ming.wine.app.v1"
                or metadata.get("app_id") != item["wine_app_id"]):
            return damaged()
        installed = metadata.get("state") in {"installed", "installed_with_refresh_warning"}
        if not installed:
            return {"installed": False, "version": None,
                    "architecture": None, "state": str(metadata.get("state") or "not_installed")}
        if (str(metadata.get("prefix") or "") != str(prefix)
                or str(metadata.get("version") or "").strip() == ""
                or str(metadata.get("architecture") or "").strip() not in {"win32", "win64"}):
            return damaged()
        try:
            target_value = pathlib.Path(str(metadata.get("launch_target") or ""))
            target = target_value.resolve(strict=True)
            resolved_prefix = prefix.resolve(strict=True)
            inside_prefix = resolved_prefix == target or resolved_prefix in target.parents
            target_info = target.lstat()

            def no_symlink_components(path, root):
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    return False
                current = root
                for part in relative.parts:
                    current = current / part
                    if stat.S_ISLNK(current.lstat().st_mode):
                        return False
                return True

            target_without_links = no_symlink_components(target_value, prefix)
        except (OSError, RuntimeError, ValueError):
            return damaged()
        expected_version = str(item.get("version") or "").strip()
        if (expected_version and expected_version != "candidate"
                and str(metadata.get("version") or "").strip() != expected_version):
            return damaged()
        # A catalog-managed Wine artifact has a fixed identity.  Metadata is
        # user-owned, so it cannot alone assert that this prefix belongs to
        # the current signed catalog application.
        artifact = item.get("artifact") if isinstance(item.get("artifact"), dict) else {}
        expected_artifact = str(artifact.get("sha256") or "").lower()
        if expected_artifact:
            if (not SHA256.fullmatch(expected_artifact)
                    or str(metadata.get("catalog_app_id") or "") != item.get("app_id")
                    or str(metadata.get("source_sha256") or "").lower() != expected_artifact):
                return damaged()
        if (not inside_prefix or target.suffix.casefold() != ".exe"
                or not target_without_links or target_value.is_symlink()
                or stat.S_ISLNK(target_info.st_mode)
                or not stat.S_ISREG(target_info.st_mode)):
            return damaged()
        return {
            "installed": installed,
            "version": str(metadata.get("version")),
            "architecture": str(metadata.get("architecture")),
            "state": metadata.get("state"),
        }


def parse_spark_packages(document):
    """Parse the public Spark repository Packages index without aptss.

    The returned records are deliberately plain data.  The caller must first
    verify the signed InRelease digest before using them for installation.
    """
    records = []
    current = {}
    last_key = None
    for line in str(document or "").splitlines():
        if not line.strip():
            if current:
                records.append(current)
            current = {}
            last_key = None
            continue
        if line[:1].isspace() and last_key:
            current[last_key] = "%s\n%s" % (current[last_key], line.strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        current[key] = value.strip()
        last_key = key
    if current:
        records.append(current)
    return records


def parse_spark_inrelease(document):
    """Return the SHA256 digest advertised for ``Packages`` in InRelease."""
    text = str(document or "")
    match = re.search(
        r"(?ms)^SHA256:\s*.*?^\s*([0-9a-f]{64})\s+\d+\s+Packages\s*$",
        text,
    )
    if not match:
        raise InvalidCatalog("星火仓库 InRelease 缺少 Packages SHA256。")
    return match.group(1).lower()


def parse_spark_release_date(document):
    """Parse the signed Release ``Date`` header as a UTC timestamp."""
    match = re.search(r"(?mi)^Date:\s*(.+?)\s*$", str(document or ""))
    if not match:
        raise InvalidCatalog("星火仓库 InRelease 缺少 Date。")
    try:
        parsed = email.utils.parsedate_to_datetime(match.group(1).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidCatalog("星火仓库 InRelease Date 无效。") from exc
    if parsed is None:
        raise InvalidCatalog("星火仓库 InRelease Date 无效。")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


class SparkPublicProvider(Provider):
    """Adapter for the public Spark catalog and its signed APT index.

    Spark's JSON endpoints are useful presentation metadata, but are not an
    installation trust boundary.  A package is actionable only when its
    package/version/path/hash matches a verified InRelease/Packages pair.
    """

    source_id = SPARK_SOURCE_ID
    ARCH_DIR = "store"

    def __init__(self, cache_root=None, categories=None, fetcher=None,
                 release_verifier=None, keyring_path=None, base_url=None,
                 cache_ttl=24 * 60 * 60, clock=None, max_response_bytes=32 * 1024 * 1024,
                 config_path=None, max_fetch_attempts=3):
        self.cache_root = pathlib.Path(cache_root or (
            pathlib.Path.home() / ".cache" / "ming-os" / "store" / SPARK_SOURCE_ID
        ))
        self.categories = tuple(categories or SPARK_CATEGORIES)
        unknown = [category for category in self.categories if category not in SPARK_CATEGORIES]
        if unknown:
            raise ValueError("星火分类无效：%s" % ",".join(unknown))
        self.fetcher = fetcher or self._default_fetcher
        self.release_verifier = release_verifier
        self.keyring_path = pathlib.Path(keyring_path or "/etc/ming-os/store/spark-archive-keyring.gpg")
        self.base_url = str(base_url or SPARK_BASE_URL).rstrip("/")
        self.cache_ttl = max(0, int(cache_ttl))
        self.clock = clock or time.time
        self.max_response_bytes = max(1024, int(max_response_bytes))
        self.max_fetch_attempts = max(1, min(int(max_fetch_attempts), 3))
        self.config_path = pathlib.Path(config_path or (
            pathlib.Path("/usr/share/ming-os/store/catalog/spark-public.json")
        ))
        self._items = []
        self.catalog_state = "unavailable"
        self.index_digest = ""
        self.cache_trusted = False
        self.last_error = ""
        self.release_date = None
        self._resource_updates = {}

    @staticmethod
    def _safe_host(url):
        parsed = urllib.parse.urlsplit(str(url))
        return (
            parsed.scheme == "https" and parsed.hostname in SPARK_ALLOWED_HOSTS
            and not parsed.username and not parsed.password
        )

    @staticmethod
    def _json_digest(value):
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _safe_filename(filename):
        value = str(filename or "")
        path = pathlib.PurePosixPath(value)
        if (
            not value or "\\" in value or "\x00" in value
            or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.lower() != ".deb"
        ):
            return None
        return path.as_posix()

    @staticmethod
    def _tags(raw):
        value = raw.get("Tags", raw.get("tags", ""))
        return tuple(sorted({part.strip().casefold() for part in str(value).split(";") if part.strip()}))

    @staticmethod
    def _safe_https(value):
        if not isinstance(value, str):
            return ""
        parsed = urllib.parse.urlsplit(value)
        return value if parsed.scheme == "https" and parsed.hostname else ""

    def _load_policy(self):
        disabled = {
            "schema": "ming.store.spark-public.v1",
            "provider": self.source_id,
            "installation_enabled": False,
            "policy_valid": False,
            "installation_disabled_reason": (
                "星火公开目录当前仅供浏览，未独立配置可用于安装的可信公钥。"
            ),
        }
        try:
            document = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return disabled
        if not isinstance(document, dict):
            return disabled
        fingerprint = str(document.get("key_fingerprint") or "").strip().upper()
        valid_identity = (
            document.get("schema") == "ming.store.spark-public.v1"
            and document.get("provider") == self.source_id
            and fingerprint == SPARK_KEY_FINGERPRINT
            and bool(re.fullmatch(r"[0-9A-F]{40}", fingerprint))
        )
        enabled = bool(document.get("installation_enabled")) and valid_identity
        reason = str(document.get("installation_disabled_reason") or "").strip()
        if not enabled and not reason:
            reason = disabled["installation_disabled_reason"]
        return dict(document, installation_enabled=enabled,
                    policy_valid=valid_identity,
                    key_fingerprint=fingerprint,
                    installation_disabled_reason=reason)

    def _cache_paths(self):
        return (
            self.cache_root / "catalog.json",
            self.cache_root / "metadata.json",
            self.cache_root / "resources.json",
        )

    def _ensure_cache_root(self, create=False):
        """Create/check the private cache tree without following symlinks."""
        root = self.cache_root
        missing = []
        cursor = root
        while True:
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                if not create:
                    raise ProviderUnavailable("没有可用的星火目录缓存。")
                missing.append(cursor)
                parent = cursor.parent
                if parent == cursor:
                    raise ProviderUnavailable("星火缓存目录路径无效。")
                cursor = parent
                continue
            except OSError as exc:
                raise ProviderUnavailable("无法检查星火缓存目录。") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ProviderUnavailable("星火缓存目录不得是符号链接或普通文件。")
            break
        for path in reversed(missing):
            try:
                path.mkdir(mode=0o700)
                info = path.lstat()
            except FileExistsError:
                info = path.lstat()
            except OSError as exc:
                raise ProviderUnavailable("无法创建星火缓存目录。") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ProviderUnavailable("星火缓存目录不得是符号链接或普通文件。")
        try:
            info = root.lstat()
        except OSError as exc:
            raise ProviderUnavailable("无法检查星火缓存目录。") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProviderUnavailable("星火缓存目录不得是符号链接或普通文件。")
        if os.name == "posix":
            getuid = getattr(os, "getuid", None)
            if getuid is not None and info.st_uid != getuid():
                raise ProviderUnavailable("星火缓存目录必须由当前用户拥有。")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise ProviderUnavailable("星火缓存目录权限过宽。")
        return root

    def _atomic_write(self, path, payload):
        self._ensure_cache_root(create=True)
        if path.is_symlink():
            raise ProviderUnavailable("星火缓存路径不得是符号链接。")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % path.name, suffix=".tmp", dir=str(self.cache_root)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def _default_fetcher(self, url, headers=None):
        if not self._safe_host(url):
            raise DownloadRejected("星火来源主机不在 HTTPS 白名单中。")
        request_headers = {"User-Agent": "Ming-Store/1", "Accept": "application/json, text/plain"}
        if headers:
            request_headers.update(dict(headers))
        request = urllib.request.Request(str(url), headers=request_headers, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            status = int(response.getcode() or 0)
            final_url = response.geturl() if hasattr(response, "geturl") else str(url)
            if not self._safe_host(final_url):
                raise DownloadRejected("星火目录重定向到了不受信任的 HTTPS 主机。")
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise DownloadRejected("星火目录响应超过大小限制。")
            return {
                "status": status,
                "headers": dict(response.headers.items()),
                "body": body.decode("utf-8", errors="strict"),
                "final_url": final_url,
            }

    def _fetch(self, path, etag=None):
        url = path if str(path).startswith("https://") else self.base_url + "/" + str(path).lstrip("/")
        if not self._safe_host(url):
            raise DownloadRejected("星火来源主机不在 HTTPS 白名单中。")
        headers = {"Accept": "application/json, text/plain"}
        if etag:
            headers["If-None-Match"] = str(etag)
        response = self.fetcher(url, headers)
        if not isinstance(response, dict):
            raise ProviderUnavailable("星火来源返回格式无效。")
        final_url = response.get("final_url")
        if final_url is not None and not self._safe_host(final_url):
            raise DownloadRejected("星火目录重定向到了不受信任的 HTTPS 主机。")
        status = int(response.get("status") or 0)
        if status == 304:
            return response
        if status != 200:
            raise ProviderUnavailable("星火来源请求失败（HTTP %s）。" % status)
        body = response.get("body", "")
        if isinstance(body, bytes):
            if len(body) > self.max_response_bytes:
                raise DownloadRejected("星火来源响应超过大小限制。")
            body = body.decode("utf-8", errors="strict")
        if not isinstance(body, str) or len(body.encode("utf-8")) > self.max_response_bytes:
            raise DownloadRejected("星火来源响应超过大小限制。")
        response["body"] = body
        return response

    @staticmethod
    def _header(headers, name):
        for key, value in dict(headers or {}).items():
            if str(key).casefold() == name.casefold():
                return str(value).strip()
        return ""

    def _load_resource_cache(self, required=False):
        _catalog_path, _metadata_path, resources_path = self._cache_paths()
        try:
            info = resources_path.lstat()
        except FileNotFoundError:
            if required:
                raise IntegrityError("星火资源缓存缺失，无法安全使用 304 响应。")
            return {}
        except OSError as exc:
            raise ProviderUnavailable("无法检查星火资源缓存。") from exc
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077)):
            raise IntegrityError("星火资源缓存不可信，无法安全使用。")
        try:
            document = json.loads(resources_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise IntegrityError("星火资源缓存格式无效。") from exc
        resources = document.get("resources") if isinstance(document, dict) else None
        if (not isinstance(document, dict)
                or document.get("schema") != "ming.store.spark-resources.v1"
                or not isinstance(resources, dict)):
            raise IntegrityError("星火资源缓存 schema 无效。")
        checked = {}
        for key, entry in resources.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise IntegrityError("星火资源缓存条目无效。")
            body = entry.get("body")
            body_hash = str(entry.get("body_sha256") or "").lower()
            if not isinstance(body, str) or not SHA256.fullmatch(body_hash):
                raise IntegrityError("星火资源缓存内容不完整。")
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != body_hash:
                raise IntegrityError("星火资源缓存内容校验失败。")
            etag = entry.get("etag")
            if etag is not None and not isinstance(etag, str):
                raise IntegrityError("星火资源缓存 ETag 无效。")
            checked[key] = {
                "etag": str(etag or ""), "body": body, "body_sha256": body_hash,
            }
        return checked

    def _fetch_resource(self, path):
        key = str(path).lstrip("/")
        resources = self._load_resource_cache(required=False)
        cached = resources.get(key)
        response = None
        retry_error = None
        for attempt in range(self.max_fetch_attempts):
            try:
                response = self._fetch(path, etag=(cached or {}).get("etag") or None)
                break
            except (OSError, DownloadFailed) as exc:
                retry_error = exc
                if attempt + 1 >= self.max_fetch_attempts:
                    raise
        if response is None:
            raise ProviderUnavailable("星火目录请求失败：%s" % retry_error)
        status = int(response.get("status") or 0)
        if status == 304:
            if (not cached or not cached.get("etag")
                    or not isinstance(cached.get("body"), str)):
                raise IntegrityError("星火服务器返回 304，但没有对应的完整验证缓存。")
            response_etag = self._header(response.get("headers"), "ETag")
            if response_etag and response_etag != cached.get("etag"):
                raise IntegrityError("星火服务器 304 的 ETag 与缓存不匹配。")
            body = cached["body"]
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body_hash != str(cached.get("body_sha256") or "").lower():
                raise IntegrityError("星火 304 缓存内容校验失败。")
            self._resource_updates[key] = dict(cached)
            return {"status": 200, "headers": {"ETag": cached.get("etag", "")},
                    "body": body, "from_cache": True}
        body = response.get("body", "")
        if not isinstance(body, str):
            raise IntegrityError("星火资源响应内容无效。")
        etag = self._header(response.get("headers"), "ETag")
        self._resource_updates[key] = {
            "etag": etag,
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        return response

    def _write_resource_cache(self, resources):
        _catalog_path, _metadata_path, resources_path = self._cache_paths()
        self._atomic_write(resources_path, {
            "schema": "ming.store.spark-resources.v1",
            "resources": resources,
        })

    def _verify_inrelease(self, inrelease):
        if self.release_verifier is not None:
            return bool(self.release_verifier(inrelease, self.keyring_path))
        try:
            key_info = self.keyring_path.lstat()
        except OSError as exc:
            raise ProviderUnavailable("星火仓库公钥缺失，暂不能安装。") from exc
        if (stat.S_ISLNK(key_info.st_mode) or not stat.S_ISREG(key_info.st_mode)
                or not shutil.which("gpgv")):
            raise ProviderUnavailable("星火仓库公钥或 gpgv 缺失，暂不能安装。")
        with tempfile.TemporaryDirectory(prefix="ming-spark-release-") as directory:
            path = pathlib.Path(directory) / "InRelease"
            path.write_text(inrelease, encoding="utf-8")
            try:
                result = subprocess.run(
                    ["gpgv", "--status-fd=1", "--keyring", str(self.keyring_path), str(path)],
                    capture_output=True, text=True, timeout=20, check=False, shell=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProviderUnavailable("星火仓库签名校验组件不可用。") from exc
        if result.returncode != 0:
            return False
        expected = SPARK_KEY_FINGERPRINT.casefold()
        for line in str(result.stdout or "").splitlines():
            fields = line.strip().split()
            if len(fields) < 10 or fields[:2] != ["[GNUPG:]", "VALIDSIG"]:
                continue
            signing_fingerprint = fields[2].casefold()
            primary_fingerprint = fields[11].casefold() if len(fields) > 11 else ""
            if (re.fullmatch(r"[0-9a-f]{40}", signing_fingerprint)
                    and (signing_fingerprint == expected or primary_fingerprint == expected)):
                return True
        return False

    @staticmethod
    def _safe_index_filename(filename):
        """Return a canonical relative repository path or ``None``.

        Debian's ``Filename`` comes from a signed index, but the index still
        must not be permitted to encode traversal, absolute paths or empty
        components before it is used to derive a download URL.
        """
        value = str(filename or "")
        if not value or "\\" in value or "\x00" in value:
            return None
        parts = value.split("/")
        if parts[:1] == ["."]:
            parts = parts[1:]
        if (not parts or any(part in {"", ".", ".."} for part in parts)
                or pathlib.PurePosixPath(parts[-1]).suffix.lower() != ".deb"):
            return None
        return "/".join(parts)

    @staticmethod
    def _record_index(records):
        index = {}
        for record in records:
            package = str(record.get("Package") or "").strip().lower()
            if not SAFE_PACKAGE.fullmatch(package):
                continue
            architecture = str(record.get("Architecture") or "").strip().lower()
            if architecture not in {"amd64", "all"}:
                continue
            filename = SparkPublicProvider._safe_index_filename(record.get("Filename"))
            digest = str(record.get("SHA256") or "").strip().lower()
            if not SHA256.fullmatch(digest) or not filename:
                continue
            index.setdefault(package, []).append({
                "package": package,
                "architecture": architecture,
                "version": str(record.get("Version") or "").strip(),
                "filename": filename,
                "sha256": digest,
                "sha512": str(record.get("SHA512") or "").strip().lower(),
                "depends": str(record.get("Depends") or "").strip(),
                "pre_depends": str(record.get("Pre-Depends") or "").strip(),
            })
        return index

    def _map_item(self, raw, category, package_index, index_digest):
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("Name", raw.get("name", ""))).strip()
        package = str(raw.get("Pkgname", raw.get("pkgname", ""))).strip().lower()
        version = str(raw.get("Version", raw.get("version", ""))).strip()
        filename = self._safe_filename(raw.get("Filename", raw.get("filename", "")))
        app_id = "spark-" + re.sub(r"[^a-z0-9._-]", "-", package).strip("-")
        if not SAFE_PACKAGE.fullmatch(package) or not SAFE_ID.fullmatch(app_id):
            return None
        tags = self._tags(raw)
        install_method = "spark-wine-deb" if any(
            "wine" in tag or tag.startswith("dwine") for tag in tags
        ) else "spark-deb"
        item = {
            "app_id": app_id,
            "name": name or package,
            "summary": str(raw.get("More", raw.get("more", ""))).strip()[:1000],
            "package_name": package,
            "version": version,
            "architectures": ["amd64"],
            "install_method": install_method,
            "dependencies": [],
            "dependency_names": [],
            "license": "星火条目未声明许可证，请查看来源说明",
            "categories": [category],
            "spark_tags": list(tags),
            "spark_category": category,
            "desktop_ids": [],
            "identity": {
                "type": "spark-inrelease-sha256", "inrelease_sha256": index_digest,
            },
            "enabled": False,
            "protected": False,
            "source_id": self.source_id,
            "website": self._safe_https(raw.get("Website", raw.get("website", ""))),
            "icon_url": self._safe_https(raw.get("icons", raw.get("Icons", ""))),
            "metadata_url": self.base_url + "/store/" + category + "/" + package + "/app.json",
        }
        if filename is None:
            item["disabled_reason"] = "星火条目文件路径不安全。"
            return item
        candidates = package_index.get(package, [])
        match = next((record for record in candidates if record["version"] == version), None)
        expected_path = "%s/%s/%s" % (category, package, filename)
        if match is None or match["filename"] != expected_path:
            item["disabled_reason"] = "星火条目未匹配签名索引，暂不可安装。"
            return item
        item["identity"].update({
            "sha256": match["sha256"], "sha512": match["sha512"],
            "architecture": match["architecture"],
        })
        item["architectures"] = [match["architecture"]]
        dependencies = [part.strip() for part in match["depends"].split(",") if part.strip()]
        pre_depends = [part.strip() for part in match["pre_depends"].split(",") if part.strip()]
        item["dependencies"] = dependencies
        item["pre_dependencies"] = pre_depends
        item["dependency_names"] = [
            part.split("(", 1)[0].strip().split(":", 1)[0].strip()
            for part in dependencies + pre_depends
        ]
        item["download_url"] = self.base_url + "/store/%s/%s/%s" % (category, package, filename)
        item["resolved_version"] = version
        item["resolved_architecture"] = match["architecture"]
        item["artifact_filename"] = filename
        if install_method == "spark-wine-deb":
            item["compatibility_state"] = "toolbox_required"
            item["compatibility_reason"] = (
                "星火 Wine 软件需要 Ming 工具箱的兼容声明和独立前缀，"
                "不能通过普通 APT 安装。"
            )
            item["disabled_reason"] = "该星火 Wine 软件需要兼容声明/工具箱，不能通过普通 APT 安装。"
            item["enabled"] = False
        else:
            item["enabled"] = True
        return item

    def _remote_refresh(self):
        policy = self._load_policy()
        policy_enabled = bool(policy.get("installation_enabled"))
        self._resource_updates = {}
        advertised = ""
        release_timestamp = None
        package_index = {}
        if policy_enabled:
            inrelease = self._fetch_resource("store/InRelease")["body"]
            if not self._verify_inrelease(inrelease):
                raise ProviderUnavailable("星火仓库 InRelease 签名验证失败。")
            release_timestamp = parse_spark_release_date(inrelease)
            now = float(self.clock())
            if release_timestamp > now + 5 * 60:
                raise InvalidCatalog("星火仓库 InRelease Date 来自未来，已拒绝。")
            if release_timestamp < now - self.cache_ttl:
                raise InvalidCatalog("星火仓库 InRelease 已明显过期，已拒绝。")
            _catalog_path, metadata_path, _resources_path = self._cache_paths()
            try:
                previous = json.loads(metadata_path.read_text(encoding="utf-8"))
                previous_date = float(previous.get("release_timestamp"))
            except (OSError, ValueError, TypeError, AttributeError):
                previous_date = None
            if previous_date is not None and release_timestamp < previous_date:
                raise IntegrityError("星火仓库 InRelease Date 回退，已拒绝重放。")
            packages = self._fetch_resource("store/Packages")["body"]
            advertised = parse_spark_inrelease(inrelease)
            actual = hashlib.sha256(packages.encode("utf-8")).hexdigest()
            if actual != advertised:
                raise IntegrityError("星火 Packages 索引 SHA256 与 InRelease 不一致。")
            package_index = self._record_index(parse_spark_packages(packages))
        items = []
        seen = set()
        for category in self.categories:
            response = self._fetch_resource("store/%s/applist.json" % category)
            try:
                raw_items = json.loads(response["body"])
            except (TypeError, ValueError) as exc:
                raise InvalidCatalog("星火分类目录 JSON 无效：%s" % category) from exc
            if not isinstance(raw_items, list):
                raise InvalidCatalog("星火分类目录必须是数组：%s" % category)
            for raw in raw_items:
                item = self._map_item(raw, category, package_index, advertised)
                if item is None or item["package_name"] in seen:
                    continue
                if not policy_enabled:
                    item["enabled"] = False
                    item["disabled_reason"] = str(
                        policy.get("installation_disabled_reason")
                        or "星火公开目录当前仅供浏览，不能安装。"
                    )
                seen.add(item["package_name"])
                items.append(item)
        catalog_path, metadata_path, _resources_path = self._cache_paths()
        self._atomic_write(catalog_path, items)
        self._atomic_write(metadata_path, {
            "schema": "ming.store.spark-cache.v1",
            "state": "ready" if policy_enabled else "browse-only",
            "timestamp": int(self.clock()),
            "inrelease_sha256": advertised,
            "release_timestamp": release_timestamp,
            "policy_enabled": policy_enabled,
            "catalog_sha256": self._json_digest(items),
        })
        self._write_resource_cache(self._resource_updates)
        self.index_digest = advertised
        self.release_date = release_timestamp
        self.catalog_state = "ready" if policy_enabled else "browse-only"
        self.cache_trusted = policy_enabled
        self.last_error = ""
        self._items = items
        return copy.deepcopy(items)

    def _load_cache(self):
        self._ensure_cache_root(create=False)
        catalog_path, metadata_path, _resources_path = self._cache_paths()
        for path in (catalog_path, metadata_path):
            try:
                info = path.lstat()
            except FileNotFoundError as exc:
                raise ProviderUnavailable("没有可用的星火目录缓存。") from exc
            except OSError as exc:
                raise ProviderUnavailable("无法检查星火目录缓存。") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ProviderUnavailable("星火缓存路径不得是符号链接或特殊文件。")
            if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                raise ProviderUnavailable("星火目录缓存权限过宽。")
        try:
            items = json.loads(catalog_path.read_text(encoding="utf-8"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProviderUnavailable("没有可用的星火目录缓存。") from exc
        if not isinstance(items, list) or not isinstance(metadata, dict):
            raise ProviderUnavailable("星火目录缓存格式无效。")
        if metadata.get("schema") != "ming.store.spark-cache.v1":
            raise ProviderUnavailable("星火目录缓存 schema 无效。")
        catalog_digest = str(metadata.get("catalog_sha256") or "").lower()
        if (not SHA256.fullmatch(catalog_digest)
                or self._json_digest(items) != catalog_digest):
            raise ProviderUnavailable("星火展示目录缓存校验失败。")
        checked_items = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ProviderUnavailable("星火展示目录缓存格式无效。")
            item = dict(raw, source_id=self.source_id)
            item["enabled"] = False
            item["disabled_reason"] = "正在使用缓存目录，仅供浏览；安装前必须联网重新验签。"
            checked_items.append(item)
        self._items = checked_items
        self.index_digest = str(metadata.get("inrelease_sha256") or "")
        try:
            self.release_date = float(metadata.get("release_timestamp"))
        except (TypeError, ValueError):
            self.release_date = None
        self.cache_trusted = False
        return copy.deepcopy(self._items)

    def refresh_catalog(self):
        try:
            return self._remote_refresh()
        except (DownloadRejected, DownloadFailed, IntegrityError, InvalidCatalog,
                ProviderUnavailable, OSError, ValueError) as error:
            self.last_error = str(error)
            try:
                cached = self._load_cache()
            except ProviderUnavailable:
                self.catalog_state = "unavailable"
                raise ProviderUnavailable("星火目录刷新失败，且没有可用缓存：%s" % error) from error
            self.catalog_state = "stale"
            return cached

    def _ensure_loaded(self):
        if not self._items:
            try:
                self._load_cache()
            except ProviderUnavailable:
                return

    def search(self, query):
        self._ensure_loaded()
        needle = str(query or "").casefold().strip()
        return [
            copy.deepcopy(item) for item in self._items
            if not needle or needle in " ".join((
                str(item.get("name", "")), str(item.get("package_name", "")),
                str(item.get("summary", "")), " ".join(item.get("spark_tags", [])),
            )).casefold()
        ]

    def get(self, app_id):
        self._ensure_loaded()
        for item in self._items:
            if item.get("app_id") == app_id:
                return copy.deepcopy(item)
        raise KeyError(app_id)

    def resolve(self, app_id):
        if not self.cache_trusted or self.catalog_state != "ready":
            raise ProviderUnavailable("星火目录需要联网刷新并重新验证签名后才能安装。")
        item = self.get(app_id)
        if not item.get("enabled"):
            raise ProviderUnavailable(str(item.get(
                "disabled_reason", "星火条目尚未通过签名索引验证。")))
        return item

    def installed_state(self, app_id):
        item = self.get(app_id)
        rc, output, _error = _default_runner((
            "dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}\t${Architecture}",
            item["package_name"],
        ), timeout=15)
        fields = (output or "").strip().split("\t")
        installed = rc == 0 and len(fields) == 3 and fields[0].strip().startswith("ii")
        return {
            "installed": installed,
            "version": fields[1].strip() if installed else None,
            "architecture": fields[2].strip() if installed else None,
        }


class DebianAptProvider(CatalogProvider):
    def __init__(self, catalog_root=None, runner=None, appstream_paths=None):
        super().__init__("debian-apt", catalog_root=catalog_root)
        self.runner = runner or _default_runner
        self.appstream_paths = self._resolve_appstream_paths(appstream_paths)

    @staticmethod
    def _resolve_appstream_paths(paths):
        if paths is not None:
            return tuple(pathlib.Path(path) for path in paths)
        resolved = []
        for pattern in DEFAULT_APPSTREAM_PATHS:
            resolved.extend(sorted(pathlib.Path("/").glob(pattern.lstrip("/"))))
        return tuple(dict.fromkeys(resolved))

    @staticmethod
    def _read_appstream(path):
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    return stream.read()
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    def refresh_catalog(self):
        curated = super().refresh_catalog()
        merged = {item["package_name"]: item for item in curated}
        for path in self.appstream_paths:
            parser = parse_dep11_yaml if path.suffix.casefold() in {".yml", ".yaml"} or path.name.casefold().endswith((".yml.gz", ".yaml.gz")) else parse_appstream_xml
            for raw in parser(self._read_appstream(path)):
                if raw["package_name"] in merged:
                    continue
                item = self._validate_item(raw)
                item["source_id"] = self.source_id
                merged[item["package_name"]] = item
        self._items = list(merged.values())
        return copy.deepcopy(self._items)

    def _ensure_loaded(self):
        if not self._items:
            self.refresh_catalog()

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
            "wine-official": WineOfficialProvider(),
            "spark-public": SparkPublicProvider(),
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
        parent = self.path.parent
        missing = []
        current = parent
        while True:
            try:
                parent_info = current.lstat()
            except FileNotFoundError:
                missing.append(current)
                if current.parent == current:
                    raise OSError("日志父目录路径无效。")
                current = current.parent
                continue
            except OSError as exc:
                raise OSError("日志父目录无法校验：%s" % exc) from exc
            if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
                raise OSError("日志父目录不得是符号链接或普通文件。")
            if current.parent == current:
                break
            current = current.parent

        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            try:
                parent_info = directory.lstat()
            except OSError as exc:
                raise OSError("日志父目录无法校验：%s" % exc) from exc
            if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
                raise OSError("日志父目录不得是符号链接或普通文件。")
            if os.name == "posix" and stat.S_IMODE(parent_info.st_mode) & 0o022:
                raise OSError("日志父目录权限过宽。")

        current = parent
        while True:
            try:
                parent_info = current.lstat()
            except OSError as exc:
                raise OSError("日志父目录无法校验：%s" % exc) from exc
            if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
                raise OSError("日志父目录不得是符号链接或普通文件。")
            if os.name == "posix" and stat.S_IMODE(parent_info.st_mode) & 0o022:
                raise OSError("日志父目录权限过宽。")
            if current.parent == current:
                break
            current = current.parent

        try:
            existing_info = self.path.lstat()
        except FileNotFoundError:
            existing_info = None
        except OSError as exc:
            raise OSError("日志文件无法校验：%s" % exc) from exc
        if existing_info is not None:
            if stat.S_ISLNK(existing_info.st_mode) or not stat.S_ISREG(existing_info.st_mode):
                raise OSError("日志文件不得是符号链接或特殊文件。")

        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise OSError("日志文件无法安全打开：%s" % exc) from exc
        try:
            info = os.fstat(descriptor)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise OSError("日志文件必须是普通文件。")
            if os.name == "posix":
                getuid = getattr(os, "geteuid", None)
                if getuid is not None and info.st_uid != getuid():
                    raise OSError("日志文件所有者不可信。")
                if stat.S_IMODE(info.st_mode) & 0o077:
                    os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                descriptor = None
                stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                if hasattr(os, "fsync"):
                    os.fsync(stream.fileno())
        finally:
            if descriptor is not None:
                os.close(descriptor)


class SecureDownloader:
    def __init__(self, opener=None, sleeper=None, attempts=3, timeout=30,
                 chunk_size=1024 * 256, max_bytes=8 * 1024 * 1024 * 1024,
                 allowed_hosts=None):
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep
        self.attempts = attempts
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.max_bytes = max(1, int(max_bytes))
        self.allowed_hosts = frozenset(
            str(host).casefold() for host in (allowed_hosts or ()) if str(host).strip()
        )

    @staticmethod
    def _validate(url, expected_sha256):
        parsed = urllib.parse.urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise DownloadRejected("只允许无账号信息的 HTTPS 下载地址。")
        digest = str(expected_sha256 or "").lower()
        if not SHA256.fullmatch(digest):
            raise DownloadRejected("SHA256 格式无效。")
        return digest

    @staticmethod
    def _ensure_destination_parent(path):
        """Create a private destination parent without following links."""
        parent = pathlib.Path(path).parent
        missing = []
        current = parent
        while True:
            try:
                info = current.lstat()
            except FileNotFoundError:
                missing.append(current)
            except OSError as exc:
                raise DownloadRejected("下载目标父目录无法校验：%s" % exc) from exc
            else:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise DownloadRejected("下载目标父目录不得是符号链接或普通文件。")
                break
            if current.parent == current:
                raise DownloadRejected("下载目标父目录路径无效。")
            current = current.parent

        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            try:
                info = directory.lstat()
            except OSError as exc:
                raise DownloadRejected("下载目标父目录无法校验：%s" % exc) from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise DownloadRejected("下载目标父目录不得是符号链接或普通文件。")

    def download(self, url, destination, expected_sha256):
        expected = self._validate(url, expected_sha256)
        source_host = urllib.parse.urlsplit(str(url)).hostname
        if self.allowed_hosts and (not source_host or source_host.casefold() not in self.allowed_hosts):
            raise DownloadRejected("下载来源主机不在受信白名单中。")
        destination = pathlib.Path(destination)
        partial = destination.with_suffix(destination.suffix + ".part")
        self._ensure_destination_parent(destination)
        if destination.is_symlink() or partial.is_symlink():
            raise DownloadRejected("下载目标不能是符号链接。")
        last_error = None
        for attempt in range(1, self.attempts + 1):
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(partial, flags, 0o600)
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    os.close(descriptor)
                    raise DownloadRejected("下载临时目标不是普通文件。")
            except DownloadRejected:
                raise
            except OSError as exc:
                raise DownloadRejected("无法安全打开下载临时文件：%s" % redact(str(exc))) from exc
            offset = opened.st_size
            headers = {"User-Agent": "Ming-Store/1"}
            if offset:
                headers["Range"] = "bytes=%d-" % offset
            request = urllib.request.Request(str(url), headers=headers, method="GET")
            try:
                stream = os.fdopen(descriptor, "r+b")
                descriptor = None
                with stream:
                    with self.opener(request, timeout=self.timeout) as response:
                        final_url = response.geturl() if hasattr(response, "geturl") else str(url)
                        final_parts = urllib.parse.urlsplit(final_url)
                        if (final_parts.scheme != "https" or final_parts.username
                                or final_parts.password
                                or (self.allowed_hosts and (
                                    not final_parts.hostname
                                    or final_parts.hostname.casefold() not in self.allowed_hosts))):
                            raise DownloadRejected("下载地址被重定向到不安全的非 HTTPS 来源。")
                        status = response.getcode()
                        append = bool(offset and status == 206)
                        if not append:
                            offset = 0
                        content_length = None
                        response_headers = getattr(response, "headers", None)
                        if response_headers is not None:
                            try:
                                content_length = int(response_headers.get("Content-Length"))
                            except (TypeError, ValueError):
                                content_length = None
                        if content_length is not None and offset + content_length > self.max_bytes:
                            raise DownloadRejected("下载文件超过允许大小。")
                        digest = hashlib.sha256()
                        if append:
                            stream.seek(0)
                            for existing_chunk in iter(lambda: stream.read(self.chunk_size), b""):
                                digest.update(existing_chunk)
                            stream.seek(0, os.SEEK_END)
                        else:
                            stream.seek(0)
                            stream.truncate(0)
                        total = offset
                        while True:
                            chunk = response.read(self.chunk_size)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise DownloadRejected("下载文件超过允许大小。")
                            stream.write(chunk)
                            digest.update(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                        completed = os.fstat(stream.fileno())
                actual = digest.hexdigest()
                if actual != expected:
                    partial.unlink(missing_ok=True)
                    destination.unlink(missing_ok=True)
                    raise IntegrityError("下载文件 SHA256 校验失败。")
                current = partial.lstat()
                if (not stat.S_ISREG(current.st_mode)
                        or (current.st_dev, current.st_ino) != (completed.st_dev, completed.st_ino)):
                    partial.unlink(missing_ok=True)
                    raise DownloadRejected("下载临时文件在校验期间被替换。")
                os.replace(partial, destination)
                return {
                    "ok": True, "state": "verified", "sha256": actual,
                    "attempts": attempt, "bytes": destination.stat().st_size,
                }
            except (IntegrityError, DownloadRejected):
                if descriptor is not None:
                    os.close(descriptor)
                partial.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
            except (OSError, TimeoutError) as exc:
                if descriptor is not None:
                    os.close(descriptor)
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
        "wine-official": WineOfficialProvider(root),
        "spark-public": SparkPublicProvider(
            cache_root=pathlib.Path.home() / ".cache" / "ming-os" / "store" / SPARK_SOURCE_ID,
            config_path=root / "spark-public.json",
        ),
    }))
