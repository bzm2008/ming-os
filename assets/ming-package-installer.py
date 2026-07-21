#!/usr/bin/env python3
"""Safely inspect and install local Debian packages for Ming OS."""

import argparse
import configparser
import errno
import hashlib
import importlib.util
import json
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows-only test path
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - Linux production path
    msvcrt = None


SUPPORTED_ARCHITECTURES = {"amd64", "all"}
PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+:~\-]+$")
SYSTEM_APPLICATION_DIR = pathlib.Path("/usr/share/applications")
SHELL_WRAPPER_REJECTION = "shell command wrappers are not allowed"
PACKAGE_INSTALLER_PATH = "/usr/local/sbin/ming-package-installer"
PACKAGE_INSTALLER_CONTRACT = "ming-package-installer-26.4.0-v4"
REQUIRED_COMMON_SHA256 = "cc2e34b62e6ab9cac74164dd51c2b5218d016f42500f80017931ce7d6f3b6ad1"
PACKAGE_MANAGER_LOCK = "/run/lock/ming-package-manager.lock"
PACKAGE_MANAGER_LOCK_TIMEOUT = 30
DPKG_LOCK_TIMEOUT = 60
PACKAGE_MANAGER_BUSY_EXIT = 75
DEFAULT_LOG_PATH = "/var/log/ming-os/package-installer.jsonl"
SPARK_SOURCE_LIST = "/etc/apt/sources.list.d/ming-spark-store.list"
OPT_APPS_ROOT = pathlib.Path("/opt/apps")
DESKTOP_PROXY_DIR = pathlib.Path("/usr/local/share/applications")
DESKTOP_PROXY_MANIFEST = pathlib.Path(
    "/var/lib/ming-os/desktop-proxies/manifest-v1.json")
DESKTOP_PROXY_SCHEMA_VERSION = 1
DESKTOP_PROXY_GENERATION = "ming-opt-desktop-proxies-v1"
DESKTOP_PROXY_LOCK = pathlib.Path("/run/lock/ming-desktop-proxies.lock")
DESKTOP_PROXY_LOCK_TIMEOUT = 10
DESKTOP_PROXY_STAGING_DIR = pathlib.Path(
    "/usr/local/share/.ming-desktop-proxy-transactions")
MING_LAUNCH_PATH = "/usr/local/bin/ming-launch"
OPT_APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,127}$")
E_PACKAGE_BUSY = "E_PACKAGE_BUSY"
E_RESOLVER_FAILED = "E_RESOLVER_FAILED"
E_LAUNCH_NOT_READY = "E_LAUNCH_NOT_READY"
E_PACKAGE_FAILED = "E_PACKAGE_FAILED"
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(password|passwd|token|access[_-]?token|refresh[_-]?token|auth[_-]?token)\b"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_SECRET_OPTION = re.compile(
    r"(--(?:password|passwd|token|access-token|refresh-token|auth-token)(?:\s+|=))"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"\b(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+",
    re.IGNORECASE,
)


class DesktopProxyLockTimeout(TimeoutError):
    """Raised when the desktop proxy transaction lock cannot be acquired."""


class _DesktopProxyTransactionLock:
    """Small cross-process lock; Debian uses flock and tests can inject a fake."""

    def __init__(self, path, timeout):
        self.path = pathlib.Path(path)
        self.timeout = max(0.0, float(timeout))
        self.handle = None

    @staticmethod
    def _metadata_is_protected(metadata):
        return (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and (os.name == "nt" or (
                int(getattr(metadata, "st_uid", -1)) == 0
                and not bool(metadata.st_mode & 0o077)
            ))
        )

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            before = os.lstat(self.path)
        except FileNotFoundError:
            before = None
        if before is not None and not self._metadata_is_protected(before):
            raise OSError("desktop proxy lock is unsafe")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if os.name != "nt" and hasattr(os, "fchown"):
                os.fchown(descriptor, 0, 0)
            metadata = os.fstat(descriptor)
            if not self._metadata_is_protected(metadata):
                raise OSError("desktop proxy lock is not root protected")
            self.handle = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = -1
            self._acquire()
            return self
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _acquire(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif msvcrt is not None:
                    self.handle.seek(0, os.SEEK_END)
                    if self.handle.tell() == 0:
                        self.handle.write(b"\0")
                        self.handle.flush()
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - supported targets provide one backend
                    raise OSError("desktop proxy locking backend is unavailable")
                return
            except (BlockingIOError, OSError) as error:
                busy = isinstance(error, BlockingIOError) or getattr(error, "errno", None) in {
                    errno.EACCES, errno.EAGAIN, errno.EDEADLK,
                }
                if not busy:
                    self.handle.close()
                    self.handle = None
                    raise
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise DesktopProxyLockTimeout("desktop proxy lock timeout") from error
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def __exit__(self, exception_type, _exception, _traceback):
        if self.handle is None:
            return False
        try:
            if fcntl is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None
        return False


def _redacted_url(match):
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,);]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        if not hostname:
            return "[REDACTED_URL]" + trailing
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[%s]" % hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = hostname if port is None else "%s:%s" % (hostname, port)
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", "")) + trailing
    except (AttributeError, TypeError, ValueError):
        return "[REDACTED_URL]" + trailing


def _redact_sensitive(value):
    text = str(value or "").replace("\x00", " ")
    text = _URL.sub(_redacted_url, text)
    text = _AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = _SECRET_OPTION.sub(r"\1[REDACTED]", text)
    return _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)


def _redact_json_value(value):
    if isinstance(value, str):
        return _redact_sensitive(value)
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_json_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_json_value(item) for key, item in value.items()}
    return value


def _common_paths(program_path=None, install_prefix=None):
    program = pathlib.Path(program_path or __file__)
    prefix = pathlib.Path(install_prefix or "/usr/local")
    candidates = (
        program.with_name("ming-shell-common.py"),
        prefix / "lib" / "ming-os" / "ming-shell-common.py",
        prefix / "bin" / "ming-shell-common.py",
    )
    return tuple(dict.fromkeys(candidates))


def _load_common(program_path=None, install_prefix=None):
    for path in _common_paths(program_path, install_prefix):
        try:
            path = path.resolve(strict=True)
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != REQUIRED_COMMON_SHA256:
                continue
            spec = importlib.util.spec_from_file_location(
                "ming_shell_common_for_package_installer", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except (AttributeError, ImportError, OSError, TypeError, ValueError):
            continue
    return None


COMMON = _load_common()


def _run(command, timeout=20):
    completed = subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


class PackageInstaller:
    def __init__(
            self, runner=None, log_path=None, uid_getter=None, logger=None,
            desktop_candidate_verifier=None, opt_apps_root=None, proxy_dir=None,
            proxy_manifest=None, proxy_lock_factory=None, proxy_lock_path=None,
            proxy_staging_dir=None):
        self.runner = runner or _run
        self.log_path = pathlib.Path(log_path or DEFAULT_LOG_PATH)
        self.uid_getter = uid_getter or getattr(os, "geteuid", lambda: 1)
        self.logger = logger
        self.desktop_candidate_verifier = (
            desktop_candidate_verifier
            or getattr(COMMON, "is_system_desktop_activation_candidate", None)
            or (lambda _path: False)
        )
        self.application_dir = SYSTEM_APPLICATION_DIR
        self.opt_apps_root = pathlib.Path(opt_apps_root or OPT_APPS_ROOT)
        self.proxy_dir = pathlib.Path(proxy_dir or DESKTOP_PROXY_DIR)
        self.proxy_manifest = pathlib.Path(proxy_manifest or DESKTOP_PROXY_MANIFEST)
        self.proxy_lock_factory = proxy_lock_factory or _DesktopProxyTransactionLock
        self.proxy_lock_path = (
            pathlib.Path(proxy_lock_path) if proxy_lock_path is not None else None)
        self.proxy_lock_timeout = DESKTOP_PROXY_LOCK_TIMEOUT
        self.proxy_staging_dir = (
            pathlib.Path(proxy_staging_dir) if proxy_staging_dir is not None else None)
        desktop_ids = getattr(COMMON, "current_desktop_ids", None)
        try:
            self.current_desktops = set(desktop_ids()) if callable(desktop_ids) else {"xfce"}
        except (AttributeError, OSError, TypeError, ValueError):
            self.current_desktops = {"xfce"}
        if not self.current_desktops:
            self.current_desktops = {"xfce"}

    def _result(self, ok, **values):
        result = {
            "ok": bool(ok),
            "action": "inspect",
            "file": "",
            "package": "",
            "version": "",
            "architecture": "",
            "resolver": "apt",
            "installed": False,
            "error": "",
            "error_code": "",
            "state": "",
            "log_path": str(self.log_path),
            "launchers": [],
            "desktop_proxies": [],
            "proxy_paths": [],
            "source_paths": [],
            "launcher_warnings": [],
            "launch_ready": False,
            "repair_argv": [],
        }
        result.update(values)
        return _redact_json_value(result)

    @staticmethod
    def _proxy_result_fields(launchers):
        proxies = [
            {
                "proxy_path": str(record.get("proxy_path")),
                "source_path": str(record.get("source_path")),
            }
            for record in launchers
            if record.get("ok") and record.get("proxy_path") and record.get("source_path")
        ]
        return {
            "desktop_proxies": proxies,
            "proxy_paths": [item["proxy_path"] for item in proxies],
            "source_paths": [item["source_path"] for item in proxies],
        }

    def _log(self, message):
        line = json.dumps({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "message": _redact_sensitive(message),
        }, ensure_ascii=False, sort_keys=True)
        try:
            if self.logger is not None:
                self.logger(line)
            else:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            pass

    def _call(self, command, timeout):
        try:
            returncode, output, error = self.runner(command, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "命令执行超时。"
        except OSError as exception:
            return 127, "", _redact_sensitive(exception)
        return (
            int(returncode),
            _redact_sensitive(output),
            _redact_sensitive(error),
        )

    def _package_file(self, package_file):
        path = pathlib.Path(package_file).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None, "找不到本地 DEB 软件包。"
        try:
            mode = resolved.stat().st_mode
        except OSError:
            return None, "无法读取本地 DEB 软件包。"
        if path.is_symlink() or not stat.S_ISREG(mode):
            return None, "只能安装普通本地 DEB 文件。"
        if resolved.suffix.lower() != ".deb":
            return None, "只能安装 .deb 软件包。"
        return resolved, ""

    @staticmethod
    def _metadata_fields(output):
        """Parse the exact labeled fields emitted by multi-field dpkg-deb."""
        expected = ("Package", "Version", "Architecture")
        values = {}
        for line in output.splitlines():
            field, separator, value = line.partition(":")
            value = value.strip()
            if (
                    not separator
                    or field not in expected
                    or field in values
                    or not value):
                return None
            values[field] = value
        if set(values) != set(expected):
            return None
        return tuple(values[field] for field in expected)

    def inspect(self, package_file):
        path, error = self._package_file(package_file)
        if error:
            return self._result(False, state="validation_failed", error=error)
        command = ("dpkg-deb", "--field", str(path), "Package", "Version", "Architecture")
        returncode, output, command_error = self._call(command, timeout=20)
        if returncode != 0:
            return self._result(
                False,
                file=str(path),
                state="validation_failed",
                error="无法读取 DEB 软件包元数据：%s" % (command_error.strip() or "dpkg-deb 失败"),
            )
        fields = self._metadata_fields(output)
        if fields is None:
            return self._result(
                False, file=str(path), state="validation_failed",
                error="DEB 软件包元数据不完整。")
        name, version, architecture = fields
        if not PACKAGE_PATTERN.fullmatch(name) or not VERSION_PATTERN.fullmatch(version):
            return self._result(
                False, file=str(path), state="validation_failed",
                error="DEB 软件包元数据格式无效。")
        if architecture not in SUPPORTED_ARCHITECTURES:
            return self._result(
                False,
                file=str(path),
                package=name,
                version=version,
                architecture=architecture,
                state="validation_failed",
                error="该 DEB 软件包不是 amd64 或 all 架构。",
            )
        return self._result(
            True,
            file=str(path),
            package=name,
            version=version,
            architecture=architecture,
            state="inspected",
        )

    @staticmethod
    def _locked_apt_command(*arguments, resolver="apt"):
        command = (
            "flock", "--exclusive", "--timeout", str(PACKAGE_MANAGER_LOCK_TIMEOUT),
            "--conflict-exit-code", str(PACKAGE_MANAGER_BUSY_EXIT),
            PACKAGE_MANAGER_LOCK,
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "DPkg::Lock::Timeout=%s" % DPKG_LOCK_TIMEOUT,
        )
        if resolver == "spark":
            command += (
                "-o", "Dir::Etc::sourcelist=%s" % SPARK_SOURCE_LIST,
                "-o", "Dir::Etc::sourceparts=-",
            )
        return command + tuple(str(value) for value in arguments)

    @staticmethod
    def _apt_install_command(package_file, resolver="apt"):
        return PackageInstaller._locked_apt_command(
            "install", package_file, resolver=resolver)

    @staticmethod
    def _apt_fix_command(resolver="apt"):
        return PackageInstaller._locked_apt_command("-f", "install", resolver=resolver)

    @staticmethod
    def _apt_reinstall_command(package):
        return PackageInstaller._locked_apt_command("--reinstall", "install", package)

    @staticmethod
    def _apt_failure_code(returncode, output="", error=""):
        detail = "%s\n%s" % (output or "", error or "")
        lock_markers = (
            "could not get lock /var/lib/dpkg/",
            "could not open lock file /var/lib/dpkg/",
            "unable to acquire the dpkg frontend lock",
            "unable to lock the administration directory",
        )
        if int(returncode) == PACKAGE_MANAGER_BUSY_EXIT or any(
                marker in detail.casefold() for marker in lock_markers):
            return E_PACKAGE_BUSY
        resolver_markers = (
            "unmet dependencies", "held broken packages",
            "unable to correct problems", "pkgproblemresolver",
            "dependency problems", "dependency error", "depends:", "conflicts:",
        )
        if any(marker in detail.casefold() for marker in resolver_markers):
            return E_RESOLVER_FAILED
        return E_PACKAGE_FAILED

    @staticmethod
    def _apt_failure_message(error_code):
        if error_code == E_PACKAGE_BUSY:
            return "软件包管理器正忙，请稍后重试。"
        if error_code == E_RESOLVER_FAILED:
            return "软件包依赖解析失败，请检查软件源后重试。"
        return "软件包安装过程失败，请查看受保护的系统日志。"

    @staticmethod
    def _apt_log_detail(output="", error=""):
        detail = " ".join(("%s\n%s" % (output or "", error or "")).split())
        return _redact_sensitive(detail)[:2048] or "apt-get failed without diagnostics"

    def _installed(self, package):
        command = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", package)
        returncode, output, _error = self._call(command, timeout=20)
        return returncode == 0 and output.strip().startswith("ii")

    def _refresh_caches(self):
        refresh = {}
        for name, command in (
            ("desktop_database", ("update-desktop-database", "/usr/share/applications")),
            ("icon_cache", ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")),
        ):
            returncode, _output, _error = self._call(command, timeout=30)
            refresh[name] = returncode == 0
        return refresh

    def _package_owns_launcher(self, path, package_paths, package=""):
        if str(path) not in package_paths:
            return False
        owner_lookup = getattr(COMMON, "installed_package_owner", None)
        if not callable(owner_lookup):
            return False
        try:
            owner = owner_lookup(
                path,
                command_runner=self._dpkg_query_runner,
                expected_package=package or None,
            )
            if not owner:
                return False
            # Keep the installer fail-closed even if a future shared helper is
            # replaced by an adapter that returns an unexpected installed owner.
            if package and str(owner).split(":", 1)[0] != package.split(":", 1)[0]:
                return False
            return True
        except (AttributeError, OSError, TypeError, ValueError, subprocess.TimeoutExpired):
            return False

    def _protected_package_wrapper(self, path, package_paths, package=""):
        if not self._package_owns_launcher(path, package_paths, package):
            return False
        try:
            return bool(self.desktop_candidate_verifier(path))
        except Exception:
            return False

    def _dpkg_query_runner(self, arguments, timeout=2):
        """Adapt the installer's tuple runner for shared package ownership checks."""
        returncode, output, error = self._call(
            ("dpkg-query", *tuple(arguments)), timeout=timeout)
        return types.SimpleNamespace(
            returncode=returncode,
            stdout=output,
            stderr=error,
        )

    @staticmethod
    def _protected_metadata(path, directory=False):
        try:
            metadata = os.lstat(path)
        except OSError:
            return None
        if directory:
            valid_type = stat.S_ISDIR(metadata.st_mode)
        else:
            valid_type = stat.S_ISREG(metadata.st_mode)
        if (
                not valid_type
                or stat.S_ISLNK(metadata.st_mode)
                or (os.name != "nt" and (
                    bool(metadata.st_mode & 0o022)
                    or int(getattr(metadata, "st_uid", -1)) != 0))):
            return None
        return metadata

    @staticmethod
    def _opt_directory_chain(path, anchor):
        current = pathlib.Path(path)
        anchor = pathlib.Path(anchor)
        if not current.is_absolute() or not anchor.is_absolute():
            return ()
        chain = []
        while True:
            chain.append(current)
            if current == anchor:
                return tuple(chain)
            parent = current.parent
            if parent == current:
                return ()
            current = parent

    def _opt_apps_desktop_path(self, value):
        """Accept only /opt/apps/<app>/entries/applications/<name>.desktop."""
        try:
            path = pathlib.Path(str(value).strip())
            root = self.opt_apps_root
            if not path.is_absolute() or not root.is_absolute():
                return None
            relative = path.relative_to(root)
        except (TypeError, ValueError):
            return None
        parts = relative.parts
        if (
                len(parts) != 4
                or parts[1:3] != ("entries", "applications")
                or not OPT_APP_ID_PATTERN.fullmatch(parts[0])
                or pathlib.PurePath(parts[3]).suffix != ".desktop"
                or parts[3] in {"", ".desktop"}
                or len(parts[3]) > 255
                or any(ord(character) < 32 for character in parts[3])
                or any(part in {".", ".."} for part in parts)):
            return None
        return path

    def _safe_opt_apps_source(self, path):
        path = self._opt_apps_desktop_path(path)
        if path is None:
            return False
        try:
            relative = path.relative_to(self.opt_apps_root)
        except ValueError:
            return False
        directories = self._opt_directory_chain(path.parent, self.opt_apps_root.parent)
        if not directories or any(
                self._protected_metadata(directory, directory=True) is None
                for directory in directories):
            return False
        return self._protected_metadata(path) is not None

    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()
        with pathlib.Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _opt_proxy_path(self, source):
        source = pathlib.Path(source)
        source_id = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        return self.proxy_dir / ("ming-opt-%s.desktop" % source_id)

    @staticmethod
    def _desktop_value(value, fallback=""):
        return str(value or fallback).replace("\r", " ").replace("\n", " ").strip()

    def _proxy_content(self, source, proxy, entry, package):
        command = "%s --desktop-file %s --source desktop" % (
            MING_LAUNCH_PATH, shlex.quote(str(proxy)))
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            "Name=%s" % self._desktop_value(getattr(entry, "name", ""), source.stem),
            "Exec=%s" % command,
            "X-Ming-Desktop-Proxy=true",
            "X-Ming-Proxy-Source=%s" % source,
            "X-Ming-Proxy-Package=%s" % package,
        ]
        icon = self._desktop_value(getattr(entry, "icon", ""))
        comment = self._desktop_value(getattr(entry, "comment", ""))
        categories = ";".join(
            self._desktop_value(value) for value in getattr(entry, "categories", ()) if value)
        if icon:
            lines.append("Icon=%s" % icon)
        if comment:
            lines.append("Comment=%s" % comment)
        if categories:
            lines.append("Categories=%s;" % categories.rstrip(";"))
        return "\n".join(lines) + "\n"

    def _ensure_proxy_dir(self):
        try:
            if self.proxy_dir.exists() or self.proxy_dir.is_symlink():
                if self._protected_metadata(self.proxy_dir, directory=True) is None:
                    raise OSError("desktop proxy directory is unsafe")
                return
            self.proxy_dir.mkdir(parents=True, exist_ok=False)
            os.chmod(self.proxy_dir, 0o755)
            if os.name != "nt" and hasattr(os, "chown"):
                os.chown(self.proxy_dir, 0, 0)
        except OSError as error:
            raise OSError("desktop proxy directory is unavailable") from error
        if self._protected_metadata(self.proxy_dir, directory=True) is None:
            raise OSError("desktop proxy directory is unsafe")

    @staticmethod
    def _atomic_write(path, content, mode=0o644):
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
        temporary = pathlib.Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content.encode("utf-8") if isinstance(content, str) else bytes(content))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            if os.name != "nt" and hasattr(os, "chown"):
                os.chown(temporary, 0, 0)
            os.replace(temporary, path)
            os.chmod(path, mode)
            if os.name != "nt" and hasattr(os, "chown"):
                os.chown(path, 0, 0)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def _read_proxy_manifest(self):
        path = self.proxy_manifest
        if not (path.exists() or path.is_symlink()):
            return {
                "schema_version": DESKTOP_PROXY_SCHEMA_VERSION,
                "generation": DESKTOP_PROXY_GENERATION,
                "entries": [],
            }
        if self._protected_metadata(path) is None:
            raise ValueError("desktop proxy manifest is unsafe")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("desktop proxy manifest is invalid") from error
        if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != DESKTOP_PROXY_SCHEMA_VERSION
                or payload.get("generation") != DESKTOP_PROXY_GENERATION
                or not isinstance(payload.get("entries"), list)
                or len(payload["entries"]) > 1024):
            raise ValueError("desktop proxy manifest schema is invalid")
        for entry in payload["entries"]:
            if not isinstance(entry, dict):
                raise ValueError("desktop proxy manifest entry is invalid")
            required = ("proxy_path", "source_path", "package", "source_sha256", "proxy_sha256")
            if any(not isinstance(entry.get(key), str) for key in required):
                raise ValueError("desktop proxy manifest entry is incomplete")
            if (
                    not pathlib.Path(entry["proxy_path"]).is_absolute()
                    or not pathlib.Path(entry["source_path"]).is_absolute()
                    or pathlib.Path(entry["proxy_path"]).parent != self.proxy_dir
                    or not re.fullmatch(
                        r"ming-opt-[0-9a-f]{64}\.desktop",
                        pathlib.Path(entry["proxy_path"]).name,
                    )
                    or any(part in {".", ".."}
                           for part in pathlib.Path(entry["proxy_path"]).parts)
                    or self._opt_apps_desktop_path(entry["source_path"]) is None
                    or not PACKAGE_PATTERN.fullmatch(entry["package"])
                    or not re.fullmatch(r"[0-9a-fA-F]{64}", entry["source_sha256"])
                    or not re.fullmatch(r"[0-9a-fA-F]{64}", entry["proxy_sha256"])):
                raise ValueError("desktop proxy manifest entry is unsafe")
        return payload

    def _opt_launcher_record(self, path, package_paths, package):
        source = pathlib.Path(path)
        record = {
            "path": str(source),
            "source_path": str(source),
            "proxy_path": "",
            "name": source.stem,
            "ok": False,
            "error": "",
            "visible": True,
            "activation": "desktop_proxy",
        }
        if not self._safe_opt_apps_source(source):
            record["error"] = "启动器源文件不安全。"
            return record, None, None
        if not self._package_owns_launcher(source, package_paths, package):
            record["error"] = "启动器所有权无法验证。"
            return record, None, None
        record["visible"], catalog_error = self._catalog_entry_status(source)
        if not record["visible"]:
            record["error"] = catalog_error
            return record, None, None
        parser = getattr(COMMON, "parse_desktop_file", None)
        diagnostic = getattr(COMMON, "desktop_launch_diagnostic", None)
        if not callable(parser) or not callable(diagnostic):
            record["error"] = "启动器校验组件不可用。"
            return record, None, None
        try:
            entry = parser(source)
        except (OSError, ValueError) as error:
            record["error"] = self._parse_error(source, error)
            return record, None, None
        if entry is None:
            record["error"] = "启动器不可见或配置无效。"
            return record, None, None
        launch_error = diagnostic(entry.argv)
        if launch_error:
            record["error"] = str(launch_error)
            return record, None, None
        try:
            source_hash = self._sha256_file(source)
            proxy = self._opt_proxy_path(source)
            content = self._proxy_content(source, proxy, entry, package)
            proxy_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except (OSError, TypeError, ValueError) as error:
            record["error"] = "无法生成启动器代理：%s" % error
            return record, None, None
        record.update(
            path=str(proxy), proxy_path=str(proxy), source_path=str(source),
            name=getattr(entry, "name", "") or source.stem, ok=True)
        manifest_entry = {
            "proxy_path": str(proxy),
            "source_path": str(source),
            "package": package,
            "source_sha256": source_hash,
            "proxy_sha256": proxy_hash,
            "generated_by": DESKTOP_PROXY_GENERATION,
        }
        return record, content, manifest_entry

    def _reserved_proxy_files(self):
        try:
            return sorted(self.proxy_dir.glob("ming-opt-*.desktop"), key=str)
        except OSError as error:
            raise OSError("desktop proxy directory cannot be enumerated") from error

    def _snapshot_proxy_state(self):
        snapshot = {}
        for path in self._reserved_proxy_files():
            metadata = self._protected_metadata(path)
            if metadata is None:
                raise OSError("reserved desktop proxy has an unsafe type")
            snapshot[str(path)] = (
                path.read_bytes(), stat.S_IMODE(metadata.st_mode) or 0o644)
        return snapshot

    def _effective_proxy_lock_path(self):
        if self.proxy_lock_path is not None:
            path = pathlib.Path(self.proxy_lock_path)
        elif (
                self.proxy_manifest == DESKTOP_PROXY_MANIFEST
                and self.proxy_dir == DESKTOP_PROXY_DIR):
            path = DESKTOP_PROXY_LOCK
        else:
            path = self.proxy_manifest.parent / ".desktop-proxies.lock"
        if str(path) == str(PACKAGE_MANAGER_LOCK):
            raise OSError("desktop proxy lock must be separate from package manager lock")
        return path

    def _effective_proxy_staging_dir(self):
        if self.proxy_staging_dir is not None:
            return pathlib.Path(self.proxy_staging_dir)
        if self.proxy_dir == DESKTOP_PROXY_DIR:
            return DESKTOP_PROXY_STAGING_DIR
        return self.proxy_dir.parent / ".ming-desktop-proxy-transactions"

    def _proxy_transaction_lock(self):
        return self.proxy_lock_factory(
            self._effective_proxy_lock_path(), self.proxy_lock_timeout)

    def _ensure_manifest_dir(self):
        directory = self.proxy_manifest.parent
        if directory.exists() or directory.is_symlink():
            if self._protected_metadata(directory, directory=True) is None:
                raise OSError("desktop proxy manifest directory is unsafe")
            return
        directory.mkdir(parents=True, exist_ok=False)
        os.chmod(directory, 0o755)
        if os.name != "nt" and hasattr(os, "chown"):
            os.chown(directory, 0, 0)
        if self._protected_metadata(directory, directory=True) is None:
            raise OSError("desktop proxy manifest directory is unsafe")

    def _ensure_proxy_staging_root(self):
        directory = self._effective_proxy_staging_dir()
        if not directory.is_absolute():
            raise OSError("desktop proxy staging directory is not absolute")
        try:
            directory.relative_to(self.proxy_dir)
        except ValueError:
            pass
        else:
            raise OSError("desktop proxy staging directory is inside application catalog")
        parent = directory.parent
        if self._protected_metadata(parent, directory=True) is None:
            raise OSError("desktop proxy staging parent is unsafe")
        if directory.exists() or directory.is_symlink():
            if self._protected_metadata(directory, directory=True) is None:
                raise OSError("desktop proxy staging directory is unsafe")
            return directory
        directory.mkdir(mode=0o700, exist_ok=False)
        os.chmod(directory, 0o700)
        if os.name != "nt" and hasattr(os, "chown"):
            os.chown(directory, 0, 0)
        if self._protected_metadata(directory, directory=True) is None:
            raise OSError("desktop proxy staging directory is unsafe")
        return directory

    def _new_proxy_transaction_dir(self, staging_root):
        directory = pathlib.Path(tempfile.mkdtemp(
            prefix="transaction-", dir=str(staging_root)))
        os.chmod(directory, 0o700)
        if os.name != "nt" and hasattr(os, "chown"):
            os.chown(directory, 0, 0)
        if self._protected_metadata(directory, directory=True) is None:
            raise OSError("desktop proxy transaction directory is unsafe")
        return directory

    def _stage_proxy(self, transaction_dir, record, content, manifest_entry):
        destination = pathlib.Path(record["proxy_path"])
        staged = pathlib.Path(transaction_dir) / (destination.name + ".staged")
        self._atomic_write(staged, content, mode=0o644)
        if (
                self._protected_metadata(staged) is None
                or self._sha256_file(staged) != manifest_entry["proxy_sha256"]):
            raise OSError("staged desktop proxy verification failed")
        return staged

    def _publish_staged_proxy(self, staged, destination):
        staged = pathlib.Path(staged)
        destination = pathlib.Path(destination)
        if (
                destination.parent != self.proxy_dir
                or not re.fullmatch(
                    r"ming-opt-[0-9a-f]{64}\.desktop", destination.name)
                or self._protected_metadata(staged) is None
                or self._protected_metadata(self.proxy_dir, directory=True) is None):
            raise OSError("desktop proxy publication path is unsafe")
        os.replace(staged, destination)
        os.chmod(destination, 0o644)
        if os.name != "nt" and hasattr(os, "chown"):
            os.chown(destination, 0, 0)
        if self._protected_metadata(destination) is None:
            raise OSError("published desktop proxy is not protected")
        self._fsync_directory(self.proxy_dir)

    @staticmethod
    def _fsync_directory(directory):
        try:
            descriptor = os.open(
                directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def _quarantine_proxy(self, path):
        transaction_dir = pathlib.Path(self._active_proxy_transaction_dir)
        path = pathlib.Path(path)
        if self._protected_metadata(path) is None:
            raise OSError("obsolete desktop proxy is unsafe")
        temporary = transaction_dir / ("retired-" + path.name)
        if temporary.exists() or temporary.is_symlink():
            raise OSError("desktop proxy quarantine collision")
        os.replace(path, temporary)
        if path.exists() or path.is_symlink():
            raise OSError("obsolete desktop proxy remains visible")
        return temporary

    def _cleanup_proxy_transaction_dir(self, transaction_dir):
        if transaction_dir is None:
            return []
        directory = pathlib.Path(transaction_dir)
        errors = []
        staging_root = self._effective_proxy_staging_dir()
        if directory.parent != staging_root or not directory.name.startswith("transaction-"):
            return ["cleanup refused an unexpected transaction path"]
        if not (directory.exists() or directory.is_symlink()):
            return []
        if self._protected_metadata(directory, directory=True) is None:
            return ["cleanup found an unsafe transaction directory"]
        try:
            children = sorted(directory.iterdir(), key=str)
        except OSError as error:
            return ["cleanup could not enumerate transaction directory: %s" % error]
        for child in children:
            try:
                metadata = os.lstat(child)
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise OSError("unsafe transaction artifact")
                os.unlink(child)
                if child.exists() or child.is_symlink():
                    raise OSError("transaction artifact remains")
            except OSError as error:
                errors.append("cleanup failed for %s: %s" % (child.name, error))
        if not errors:
            try:
                os.rmdir(directory)
                if directory.exists() or directory.is_symlink():
                    raise OSError("transaction directory remains")
            except OSError as error:
                errors.append("cleanup failed for transaction directory: %s" % error)
        return errors

    def _cleanup_abandoned_proxy_transactions(self, staging_root):
        errors = []
        try:
            candidates = sorted(pathlib.Path(staging_root).glob("transaction-*"), key=str)
        except OSError as error:
            return ["cleanup could not enumerate abandoned transactions: %s" % error]
        for candidate in candidates:
            errors.extend(self._cleanup_proxy_transaction_dir(candidate))
        return errors

    def _retained_proxy_contract_valid(self, entry):
        try:
            source = pathlib.Path(entry["source_path"])
            proxy = pathlib.Path(entry["proxy_path"])
            package = entry["package"]
            if (
                    entry.get("generated_by") != DESKTOP_PROXY_GENERATION
                    or proxy != self._opt_proxy_path(source)
                    or self._protected_metadata(self.proxy_dir, directory=True) is None
                    or self._protected_metadata(proxy) is None
                    or not self._safe_opt_apps_source(source)
                    or not self._package_owns_launcher(
                        source, {str(source)}, package)
                    or self._sha256_file(source) != entry["source_sha256"]
                    or self._sha256_file(proxy) != entry["proxy_sha256"]):
                return False
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            with proxy.open("r", encoding="utf-8", errors="replace") as handle:
                parser.read_file(handle)
            section = parser["Desktop Entry"]
            expected_exec = "%s --desktop-file %s --source desktop" % (
                MING_LAUNCH_PATH, shlex.quote(str(proxy)))
            return (
                section.get("X-Ming-Desktop-Proxy", "").strip().casefold() == "true"
                and section.get("X-Ming-Proxy-Source", "").strip() == str(source)
                and section.get("X-Ming-Proxy-Package", "").strip() == package
                and section.get("Exec", "").strip() == expected_exec
            )
        except (
                KeyError, OSError, TypeError, ValueError,
                configparser.Error, subprocess.TimeoutExpired):
            return False

    def _rollback_proxy_transaction(
            self, snapshot, manifest_before, manifest_mode, referenced_paths,
            quarantined):
        del referenced_paths, quarantined
        errors = []
        try:
            if manifest_before is None:
                if self.proxy_manifest.exists() or self.proxy_manifest.is_symlink():
                    os.unlink(self.proxy_manifest)
            else:
                type(self)._atomic_write(
                    self.proxy_manifest, manifest_before, manifest_mode or 0o644)
        except OSError as error:
            errors.append("manifest rollback failed: %s" % error)
        try:
            current = self._reserved_proxy_files()
        except OSError as error:
            current = []
            errors.append("proxy rollback enumeration failed: %s" % error)
        for candidate in current:
            if str(candidate) in snapshot:
                continue
            try:
                os.unlink(candidate)
                if candidate.exists() or candidate.is_symlink():
                    raise OSError("new proxy remains")
            except OSError as error:
                errors.append("proxy rollback removal failed for %s: %s" % (
                    candidate, error))
        for path, state in snapshot.items():
            try:
                content, mode = state
                type(self)._atomic_write(
                    pathlib.Path(path), content, mode or 0o644)
            except OSError as error:
                errors.append("proxy rollback restore failed for %s: %s" % (path, error))
        try:
            if manifest_before is None:
                if self.proxy_manifest.exists() or self.proxy_manifest.is_symlink():
                    errors.append("manifest rollback verification failed: manifest remains")
            elif (
                    self._protected_metadata(self.proxy_manifest) is None
                    or self.proxy_manifest.read_bytes() != manifest_before):
                errors.append("manifest rollback verification failed: content mismatch")
        except OSError as error:
            errors.append("manifest rollback verification failed: %s" % error)
        try:
            restored = self._snapshot_proxy_state()
            if set(restored) != set(snapshot):
                errors.append("proxy rollback verification failed: path set mismatch")
            else:
                for path, state in snapshot.items():
                    if restored.get(path) != state:
                        errors.append(
                            "proxy rollback verification failed for %s" % path)
        except OSError as error:
            errors.append("proxy rollback verification failed: %s" % error)
        return errors

    def _proxy_transaction_failure(
            self, package, records, error, rollback_errors=(), cleanup_errors=()):
        parts = [str(error) or "unknown desktop proxy transaction failure"]
        parts.extend("rollback: %s" % item for item in rollback_errors if item)
        parts.extend("cleanup: %s" % item for item in cleanup_errors if item)
        message = "desktop proxy transaction failed: %s" % "; ".join(parts)
        for record in records:
            record["ok"] = False
            record["error"] = message
        self._log("desktop proxy convergence failed for %s: %s" % (package, message))
        return records, message

    def _sync_desktop_proxies(self, package, source_paths):
        try:
            lock = self._proxy_transaction_lock()
            with lock:
                return self._sync_desktop_proxies_locked(package, source_paths)
        except (DesktopProxyLockTimeout, TimeoutError) as error:
            message = "desktop proxy lock timeout: %s" % error
            self._log("desktop proxy lock timeout for %s: %s" % (package, error))
            return [], message
        except OSError as error:
            message = "desktop proxy lock unavailable: %s" % error
            self._log("desktop proxy lock unavailable for %s: %s" % (package, error))
            return [], message

    def _sync_desktop_proxies_locked(self, package, source_paths):
        manifest_exists = self.proxy_manifest.exists() or self.proxy_manifest.is_symlink()
        proxy_dir_exists = self.proxy_dir.exists() or self.proxy_dir.is_symlink()
        if not source_paths and not manifest_exists and not proxy_dir_exists:
            return [], ""
        records = []
        transaction_dir = None
        try:
            existing = self._read_proxy_manifest()
            self._ensure_proxy_dir()
            self._ensure_manifest_dir()
            manifest_before = None
            manifest_mode = 0o644
            if manifest_exists:
                manifest_metadata = os.lstat(self.proxy_manifest)
                manifest_before = self.proxy_manifest.read_bytes()
                manifest_mode = stat.S_IMODE(manifest_metadata.st_mode) or 0o644
            proxy_snapshot = self._snapshot_proxy_state()
        except (OSError, ValueError) as error:
            return [], "无法读取图形启动器代理清单：%s" % error
        prepared = []
        package_paths = {str(path) for path in source_paths}
        for source in sorted({pathlib.Path(path) for path in source_paths}, key=str):
            record, content, manifest_entry = self._opt_launcher_record(
                source, package_paths, package)
            records.append(record)
            if record.get("ok"):
                prepared.append((record, content, manifest_entry))
        invalid_source = any(not record.get("ok") for record in records)
        old_entries = []
        for entry in existing.get("entries", []):
            if entry.get("package") == package:
                continue
            if self._retained_proxy_contract_valid(entry):
                old_entries.append(entry)
            else:
                self._log("dropping invalid retained desktop proxy for %s: %s" % (
                    entry.get("package") or "<unknown>",
                    entry.get("proxy_path") or "<unknown>",
                ))
        current_entries = [] if invalid_source else [item[2] for item in prepared]
        desired_entries = sorted(
            old_entries + current_entries, key=lambda item: item["proxy_path"])
        desired_paths = {entry["proxy_path"] for entry in desired_entries}
        referenced_paths = {
            entry["proxy_path"] for entry in existing.get("entries", [])}
        quarantined = []
        try:
            staging_root = self._ensure_proxy_staging_root()
            abandoned_errors = self._cleanup_abandoned_proxy_transactions(staging_root)
            if abandoned_errors:
                return self._proxy_transaction_failure(
                    package, records, "abandoned transaction cleanup failed",
                    cleanup_errors=abandoned_errors)
            transaction_dir = self._new_proxy_transaction_dir(staging_root)
            self._active_proxy_transaction_dir = transaction_dir
            staged = []
            if not invalid_source:
                for record, content, entry in prepared:
                    staged.append((
                        self._stage_proxy(transaction_dir, record, content, entry),
                        pathlib.Path(record["proxy_path"]),
                        entry,
                    ))
            payload = {
                "schema_version": DESKTOP_PROXY_SCHEMA_VERSION,
                "generation": DESKTOP_PROXY_GENERATION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "entries": desired_entries,
            }
            self._atomic_write(
                self.proxy_manifest,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                mode=0o644,
            )
            for staged_path, destination, entry in staged:
                self._publish_staged_proxy(staged_path, destination)
                if (
                        self._protected_metadata(destination) is None
                        or self._sha256_file(destination) != entry["proxy_sha256"]):
                    raise OSError("published desktop proxy verification failed")
            for candidate in self._reserved_proxy_files():
                if str(candidate) not in desired_paths:
                    quarantined.append((
                        str(candidate),
                        self._quarantine_proxy(candidate),
                        str(candidate) in referenced_paths,
                    ))
            cleanup_errors = self._cleanup_proxy_transaction_dir(transaction_dir)
            transaction_dir = None
            if cleanup_errors:
                raise OSError("cleanup: %s" % "; ".join(cleanup_errors))
        except (OSError, TypeError, ValueError) as error:
            rollback_errors = self._rollback_proxy_transaction(
                proxy_snapshot,
                manifest_before,
                manifest_mode,
                referenced_paths,
                quarantined,
            )
            cleanup_errors = self._cleanup_proxy_transaction_dir(transaction_dir)
            transaction_dir = None
            return self._proxy_transaction_failure(
                package, records, error,
                rollback_errors=rollback_errors,
                cleanup_errors=cleanup_errors,
            )
        finally:
            try:
                del self._active_proxy_transaction_dir
            except AttributeError:
                pass
        return records, ""

    def _catalog_entry_status(self, path):
        """Read catalog visibility only; launch validity comes from COMMON."""
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            with pathlib.Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                parser.read_file(handle)
            section = parser["Desktop Entry"]
            visible = getattr(COMMON, "desktop_entry_is_visible", None)
            if not callable(visible):
                return False, "启动器校验组件不可用。"
            if visible(
                    section, current_desktops=self.current_desktops,
                    respect_desktop_environment=True):
                return True, ""
            if (
                section.getboolean("Hidden", fallback=False)
                or section.getboolean("NoDisplay", fallback=False)
            ):
                return False, "启动器已隐藏（Hidden 或 NoDisplay）。"
            only = {
                item.strip().casefold()
                for item in section.get("OnlyShowIn", "").split(";") if item.strip()
            }
            excluded = {
                item.strip().casefold()
                for item in section.get("NotShowIn", "").split(";") if item.strip()
            }
            if only and not self.current_desktops.intersection(only):
                return False, "启动器不适用于当前桌面（OnlyShowIn）。"
            if self.current_desktops.intersection(excluded):
                return False, "启动器已在当前桌面禁用（NotShowIn）。"
            return False, "启动器不适用于当前桌面。"
        except (KeyError, OSError, ValueError, configparser.Error):
            return True, ""

    @staticmethod
    def _diagnostic_from_common(path, fallback):
        diagnostic = getattr(COMMON, "diagnose_desktop_file", None)
        if callable(diagnostic):
            try:
                entry = diagnostic(path)
                message = getattr(entry, "diagnostic", "") if entry is not None else ""
                if message:
                    return str(message)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        return fallback

    def _parse_error(self, path, error):
        return self._diagnostic_from_common(
            path, "启动器配置无效：%s" % (str(error) or "未知错误"))

    def _launcher_record(self, path, package_paths=(), package=""):
        path = pathlib.Path(path)
        record = {
            "path": str(path), "name": path.stem, "ok": False, "error": "",
            "visible": True, "activation": "",
        }
        try:
            if path.is_symlink():
                record["error"] = "启动器路径不能是符号链接。"
                return record
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("启动器文件不存在或为空")
            parser = getattr(COMMON, "parse_desktop_file", None)
            if not callable(parser):
                record["error"] = "启动器校验组件不可用。"
                return record
            record["visible"], catalog_error = self._catalog_entry_status(path)
            if not record["visible"]:
                record["error"] = catalog_error
                return record
            try:
                entry = parser(path)
            except ValueError as exc:
                if (
                        str(exc) == SHELL_WRAPPER_REJECTION
                        and self._protected_package_wrapper(
                            path, set(package_paths), package)):
                    record.update(ok=True, activation="desktop_app_info")
                    return record
                record["error"] = self._parse_error(path, exc)
                return record
            if entry is None:
                record["error"] = self._diagnostic_from_common(
                    path, "启动器不是可用的图形应用，或其 TryExec 依赖不可用。")
                return record
            record["name"] = entry.name or path.stem
            record["activation"] = "direct"
            diagnostic = getattr(COMMON, "desktop_launch_diagnostic", None)
            program_getter = getattr(COMMON, "desktop_exec_program", None)
            if not callable(diagnostic) or not callable(program_getter):
                record["error"] = "启动器校验组件不可用。"
                return record
            error = diagnostic(entry.argv)
            if error:
                record["error"] = str(error)
                return record
            program = program_getter(entry.argv)
            candidate = pathlib.Path(program)
            executable = candidate if candidate.is_absolute() else None
            try:
                if executable is None:
                    import shutil
                    found = shutil.which(program)
                    executable = pathlib.Path(found) if found else None
                with executable.open("rb") as handle:
                    is_elf = handle.read(4) == b"\x7fELF"
            except (AttributeError, OSError, TypeError, ValueError):
                is_elf = False
            if is_elf:
                _rc, output, _error = self._call(("ldd", str(executable)), timeout=20)
                missing = [line.strip() for line in output.splitlines() if "not found" in line]
                if missing:
                    record["error"] = "缺少运行库：%s" % "; ".join(missing[:3])
                    return record
            if package and not self._package_owns_launcher(
                    path, set(package_paths), package):
                record["error"] = "启动器所有权无法验证。"
                return record
            if package:
                try:
                    protected = bool(self.desktop_candidate_verifier(path))
                except Exception:
                    protected = False
                if not protected:
                    record["error"] = "启动器保护状态无法验证。"
                    return record
            record["ok"] = True
            return record
        except (OSError, configparser.Error) as exc:
            record["error"] = "无法读取启动器：%s" % exc
            return record

    def _catalog_desktop_path(self, value):
        try:
            path = pathlib.Path(str(value).strip())
            application_dir = pathlib.Path(self.application_dir)
        except (TypeError, ValueError):
            return None
        if (
                not path.is_absolute()
                or path.suffix != ".desktop"
                or path.parent != application_dir):
            return None
        return path

    def _package_launchers(self, package):
        """Validate direct system entries and bounded /opt/apps proxy sources."""
        returncode, output, command_error = self._call(
            ("dpkg-query", "-L", package), timeout=20)
        if returncode != 0:
            detail = command_error.strip() or "dpkg-query -L 失败"
            return [], "无法枚举软件包图形启动器：%s" % detail
        catalog_paths = {
            str(path) for value in output.splitlines()
            for path in (self._catalog_desktop_path(value),)
            if path is not None
        }
        opt_paths = {
            str(path) for value in output.splitlines()
            for path in (self._opt_apps_desktop_path(value),)
            if path is not None
        }
        package_paths = catalog_paths | opt_paths
        launchers = [
            self._launcher_record(
                pathlib.Path(value), package_paths=package_paths, package=package)
            for value in sorted(catalog_paths)
        ]
        proxies, proxy_error = self._sync_desktop_proxies(package, opt_paths)
        launchers.extend(proxies)
        return launchers, proxy_error

    def _launch_readiness(self, launchers, completed_state, enumeration_error=""):
        if enumeration_error:
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但无法确认图形启动器：%s。请查看日志：%s" % (
                    enumeration_error, self.log_path),
            )
        if not launchers:
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但未发现可验证的系统图形启动器。请查看日志：%s" % (
                    self.log_path,
                ),
            )
        visible = [record for record in launchers if record.get("visible")]
        visible_problems = [record for record in visible if not record.get("ok")]
        if visible_problems:
            details = []
            for record in visible_problems[:3]:
                name = str(record.get("name") or pathlib.Path(record.get("path", "")).stem)
                reason = str(record.get("error") or "启动器不可用")
                details.append("%s（%s）" % (name.replace("\n", " ")[:80], reason.replace("\n", " ")[:120]))
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但以下可见启动器无法启动：%s。请查看日志：%s" % (
                    "；".join(details), self.log_path),
            )
        if launchers and not visible:
            details = []
            for record in launchers[:3]:
                name = str(record.get("name") or pathlib.Path(record.get("path", "")).stem)
                reason = str(record.get("error") or "启动器不适用于当前桌面")
                details.append("%s（%s）" % (
                    name.replace("\n", " ")[:80], reason.replace("\n", " ")[:120]))
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但没有当前桌面可用的图形启动器：%s。请查看日志：%s" % (
                    "；".join(details), self.log_path),
            )
        if any(record.get("activation") == "desktop_app_info" for record in visible):
            return "%s_with_desktop_activation" % completed_state, True, ""
        return completed_state, True, ""

    @staticmethod
    def _repair_argv(package):
        return [PACKAGE_INSTALLER_PATH, "repair", package]

    def _log_launch_readiness(self, package, warnings, enumeration_error=""):
        if enumeration_error:
            self._log("launcher enumeration failed for %s: %s" % (
                package, enumeration_error))
        for warning in warnings:
            self._log("launcher readiness problem for %s: %s: %s" % (
                package,
                warning.get("path") or "<enumeration>",
                warning.get("error") or "启动器不可用",
            ))

    def install(self, package_file, resolver="apt"):
        if resolver not in {"apt", "spark"}:
            return self._result(
                False, action="install", state="validation_failed",
                resolver=str(resolver), error="软件包解析器无效。",
            )
        inspected = self.inspect(package_file)
        if not inspected["ok"]:
            inspected.update(action="install", state="validation_failed", resolver=resolver)
            return inspected
        if self.uid_getter() != 0:
            return self._result(
                False,
                action="install",
                state="permission_denied",
                file=inspected["file"],
                package=inspected["package"],
                version=inspected["version"],
                architecture=inspected["architecture"],
                resolver=resolver,
                error="安装 DEB 软件包需要管理员权限。",
            )

        command = self._apt_install_command(inspected["file"], resolver=resolver)
        returncode, output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        failure_code = self._apt_failure_code(returncode, output, error)
        if returncode != 0 and failure_code == E_RESOLVER_FAILED:
            dependency_repair_attempted = True
            fix_code, fix_output, fix_error = self._call(
                self._apt_fix_command(resolver=resolver), timeout=180)
            if fix_code == 0:
                returncode, output, error = self._call(command, timeout=180)
            else:
                returncode, output = fix_code, fix_output
                error = fix_error or error
        if returncode != 0:
            failure_code = self._apt_failure_code(returncode, output, error)
            self._log("install failed for %s: %s" % (
                inspected["file"], self._apt_log_detail(output, error)))
            return self._result(
                False,
                action="install",
                state="install_failed",
                error_code=failure_code,
                dependency_repair_attempted=dependency_repair_attempted,
                resolver=resolver,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error=self._apt_failure_message(failure_code),
            )
        if not self._installed(inspected["package"]):
            self._log("package verification failed for %s" % inspected["package"])
            return self._result(
                False,
                action="install",
                state="verification_failed",
                dependency_repair_attempted=dependency_repair_attempted,
                resolver=resolver,
                installed=False,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error="软件包安装后未处于已安装状态。",
            )
        refresh = self._refresh_caches()
        launchers, enumeration_error = self._package_launchers(inspected["package"])
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        if enumeration_error:
            launcher_warnings.append({
                "path": "", "name": inspected["package"], "ok": False,
                "visible": True, "activation": "", "error": enumeration_error,
            })
        state, launch_ready, launch_error = self._launch_readiness(
            launchers, "installed", enumeration_error)
        proxy_fields = self._proxy_result_fields(launchers)
        self._log("installed %s from %s" % (inspected["package"], inspected["file"]))
        self._log_launch_readiness(
            inspected["package"], launcher_warnings, enumeration_error)
        return self._result(
            True,
            action="install",
            state=state,
            launch_ready=launch_ready,
            installed=True,
            resolver=resolver,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
            error=launch_error,
            error_code=E_LAUNCH_NOT_READY if not launch_ready else "",
            repair_argv=(self._repair_argv(inspected["package"])
                         if not launch_ready else []),
            **proxy_fields,
            **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
        )

    def verify_installed(self, package):
        """Verify installed state and launcher readiness without another APT transaction."""
        package = package.strip() if isinstance(package, str) else ""
        if not PACKAGE_PATTERN.fullmatch(package):
            return self._result(
                False,
                action="verify-installed",
                state="validation_failed",
                package=package,
                error_code=E_PACKAGE_FAILED,
                error="软件包名称格式无效。",
            )
        returncode, output, command_error = self._call(
            ("dpkg-query", "-W", "-f=${Status}", package), timeout=20)
        if returncode != 0 or output.strip() != "install ok installed":
            self._log("installed-state verification failed for %s: %s" % (
                package, command_error.strip() or output.strip() or "not installed"))
            return self._result(
                False,
                action="verify-installed",
                state="verification_failed",
                package=package,
                installed=False,
                launch_ready=False,
                error_code=E_PACKAGE_FAILED,
                error="软件包事务结束后未处于完整安装状态。",
            )

        launchers, enumeration_error = self._package_launchers(package)
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        if enumeration_error:
            launcher_warnings.append({
                "path": "", "name": package, "ok": False,
                "visible": True, "activation": "", "error": enumeration_error,
            })
        state, launch_ready, launch_error = self._launch_readiness(
            launchers, "verified", enumeration_error)
        proxy_fields = self._proxy_result_fields(launchers)
        self._log_launch_readiness(package, launcher_warnings, enumeration_error)
        return self._result(
            True,
            action="verify-installed",
            state=state,
            package=package,
            installed=True,
            launch_ready=launch_ready,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
            error=launch_error,
            error_code=E_LAUNCH_NOT_READY if not launch_ready else "",
            repair_argv=self._repair_argv(package) if not launch_ready else [],
            **proxy_fields,
        )

    def repair(self, package):
        package = package.strip() if isinstance(package, str) else ""
        if not PACKAGE_PATTERN.fullmatch(package):
            return self._result(
                False,
                action="repair",
                state="validation_failed",
                error="软件包名称格式无效。",
            )
        if self.uid_getter() != 0:
            return self._result(
                False,
                action="repair",
                state="permission_denied",
                package=package,
                error="修复软件包需要管理员权限。",
            )
        command = self._apt_reinstall_command(package)
        returncode, output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        failure_code = self._apt_failure_code(returncode, output, error)
        if returncode != 0 and failure_code == E_RESOLVER_FAILED:
            dependency_repair_attempted = True
            fix_code, fix_output, fix_error = self._call(self._apt_fix_command(), timeout=180)
            if fix_code == 0:
                returncode, output, error = self._call(command, timeout=180)
            else:
                returncode, output = fix_code, fix_output
                error = fix_error or error
        if returncode != 0:
            failure_code = self._apt_failure_code(returncode, output, error)
            self._log("repair failed for %s: %s" % (
                package, self._apt_log_detail(output, error)))
            return self._result(
                False,
                action="repair",
                state="repair_failed",
                error_code=failure_code,
                package=package,
                dependency_repair_attempted=dependency_repair_attempted,
                error=self._apt_failure_message(failure_code),
            )
        if not self._installed(package):
            self._log("repair verification failed for %s" % package)
            return self._result(
                False,
                action="repair",
                state="verification_failed",
                package=package,
                dependency_repair_attempted=dependency_repair_attempted,
                error="软件包修复后未处于已安装状态。",
            )
        refresh = self._refresh_caches()
        launchers, enumeration_error = self._package_launchers(package)
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        if enumeration_error:
            launcher_warnings.append({
                "path": "", "name": package, "ok": False,
                "visible": True, "activation": "", "error": enumeration_error,
            })
        state, launch_ready, launch_error = self._launch_readiness(
            launchers, "repaired", enumeration_error)
        proxy_fields = self._proxy_result_fields(launchers)
        self._log("repaired %s" % package)
        self._log_launch_readiness(package, launcher_warnings, enumeration_error)
        return self._result(
            True,
            action="repair",
            state=state,
            launch_ready=launch_ready,
            installed=True,
            package=package,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
            error=launch_error,
            error_code=E_LAUNCH_NOT_READY if not launch_ready else "",
            repair_argv=self._repair_argv(package) if not launch_ready else [],
            **proxy_fields,
        )


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-package-installer")
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect")
    inspect.add_argument("file")
    inspect.add_argument("--json", action="store_true")
    install = actions.add_parser("install")
    install.add_argument("file")
    install.add_argument("--resolver", choices=("apt", "spark"), default="apt")
    install.add_argument("--json", action="store_true")
    repair = actions.add_parser("repair")
    repair.add_argument("package")
    return parser


def result_exit_code(result):
    if result.get("ok"):
        return 0
    if result.get("state") == "permission_denied":
        return 3
    if result.get("state") == "validation_failed":
        return 2
    return 4


def main(argv=None, installer=None, stdout=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    installer = installer or PackageInstaller()
    stdout = stdout or sys.stdout
    if args.action == "inspect":
        result = installer.inspect(args.file)
    elif args.action == "install":
        result = installer.install(args.file, resolver=args.resolver)
    else:
        result = installer.repair(args.package)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
