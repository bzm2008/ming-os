#!/usr/bin/env python3
"""Narrow privileged bridge for Spark Store package operations.

The helper deliberately exposes a tiny typed command surface.  It never
passes user input to a shell and it stages downloaded DEBs before any package
manager process can consume them.
"""

import dataclasses
import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

try:
    import pwd as _pwd
except ImportError:  # pragma: no cover - Windows test host
    _pwd = None


PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+:~\-]{1,256}$")
SHA512_PATTERN = re.compile(r"^[0-9a-fA-F]{128}$")
SUPPORTED_ARCHITECTURES = {"amd64", "all"}
MAX_DEB_BYTES = 1024 * 1024 * 1024
DOWNLOAD_ROOT = pathlib.Path("/tmp/spark-store/download")
STAGING_ROOT = pathlib.Path("/var/lib/ming-package-installer/incoming")
LOG_PATH = pathlib.Path("/var/log/ming-os/package-installer.jsonl")
SPARK_SOURCE_LIST = "/etc/apt/sources.list.d/ming-spark-store.list"
SPARK_KEYRING = "/etc/apt/keyrings/ming-spark-store.gpg"
SPARK_SOURCE_LINE = (
    "deb [signed-by=/etc/apt/keyrings/ming-spark-store.gpg] "
    "https://d.spark-app.store/store /"
)
PACKAGE_MANAGER_LOCK = "/run/lock/ming-package-manager.lock"
PACKAGE_MANAGER_LOCK_TIMEOUT = 30
DPKG_LOCK_TIMEOUT = 60
PACKAGE_MANAGER_BUSY_EXIT = 75
E_PACKAGE_BUSY = "E_PACKAGE_BUSY"
E_RESOLVER_FAILED = "E_RESOLVER_FAILED"
E_LAUNCH_NOT_READY = "E_LAUNCH_NOT_READY"
E_PACKAGE_FAILED = "E_PACKAGE_FAILED"
E_REQUEST_INVALID = "E_REQUEST_INVALID"
E_AUTHORIZATION_FAILED = "E_AUTHORIZATION_FAILED"
E_FILE_UNSAFE = "E_FILE_UNSAFE"
E_PACKAGE_UNVERIFIED = "E_PACKAGE_UNVERIFIED"
REQUIRED_PACKAGE_INSTALLER_CONTRACT = "ming-package-installer-26.4.0-v4"
PACKAGE_INSTALLER_PATH = pathlib.Path(
    "/usr/local/lib/ming-os/package-installer-runtimes/%s/ming-package-installer"
    % REQUIRED_PACKAGE_INSTALLER_CONTRACT)
PACKAGE_INSTALLER_FALLBACK_PATH = pathlib.Path(
    "/usr/local/sbin/ming-package-installer")


class HelperError(Exception):
    """A stable machine-readable helper failure."""

    def __init__(self, code, message=""):
        self.code = str(code)
        self.message = str(message or code)
        super().__init__(self.message)


@dataclasses.dataclass(frozen=True)
class Request:
    operation: str
    package: str = ""
    source: str = ""
    delete_source: bool = False


@dataclasses.dataclass(frozen=True)
class SourceIdentity:
    st_dev: int
    st_ino: int

    @classmethod
    def from_stat(cls, metadata):
        return cls(int(metadata.st_dev), int(metadata.st_ino))


@dataclasses.dataclass(frozen=True)
class StagedDeb:
    path: pathlib.Path
    sha512: str
    source_path: pathlib.Path
    source_identity: SourceIdentity


def _run(command, timeout=20):
    completed = subprocess.run(
        [str(value) for value in command],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _redact_url(match):
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,);]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if not hostname:
            return "[REDACTED_URL]" + trailing
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[%s]" % hostname
        port = ""
        try:
            if parsed.port is not None:
                port = ":%s" % parsed.port
        except ValueError:
            return "[REDACTED_URL]" + trailing
        return urlunsplit((parsed.scheme, hostname + port, parsed.path, "", "")) + trailing
    except (AttributeError, TypeError, ValueError):
        return "[REDACTED_URL]" + trailing


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"\b(password|passwd|token|access[_-]?token|refresh[_-]?token|auth[_-]?token)\b"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)


def _redact(value):
    text = str(value or "").replace("\x00", " ")
    text = _URL_RE.sub(_redact_url, text)
    return _SECRET_RE.sub(r"\1\2[REDACTED]", text)


def _redact_value(value):
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def _invalid(message="invalid request"):
    raise HelperError(E_REQUEST_INVALID, message)


def _valid_package(value):
    return isinstance(value, str) and PACKAGE_PATTERN.fullmatch(value) is not None


def parse_request(argv):
    """Parse the exact four command forms accepted by the privileged bridge."""
    values = tuple(argv or ())
    if not all(isinstance(value, str) and value != "" and "\x00" not in value
               for value in values):
        _invalid()

    if len(values) == 5 and values[0] == "ssinstall":
        source = values[1]
        if (
            values[2:] != (
                "--delete-after-install",
                "--no-create-desktop-entry",
                "--native",
            )
            or not (source.startswith("/") or pathlib.Path(source).is_absolute())
        ):
            _invalid()
        path = pathlib.PurePath(source)
        package = path.parent.name
        if not _valid_package(package) or path.name in ("", ".deb"):
            _invalid()
        return Request("install_deb", package=package, source=source, delete_source=True)

    if len(values) == 4 and values[0] == "ssinstall":
        if values[2:] != ("--no-create-desktop-entry", "--native") or not _valid_package(values[1]):
            _invalid()
        return Request("install_package", package=values[1])

    if values == ("aptss", "ssupdate"):
        return Request("update")

    if len(values) in (3, 4) and values[0:2] == ("aptss", "remove"):
        package = values[-1]
        if len(values) == 4 and values[2] != "-y":
            _invalid()
        if not _valid_package(package):
            _invalid()
        return Request("remove", package=package)

    _invalid()


def _parse_fields(output, expected):
    values = {}
    for line in str(output or "").splitlines():
        field, separator, value = line.partition(":")
        value = value.strip()
        if not separator or field not in expected or field in values or not value:
            return None
        values[field] = value
    if set(values) != set(expected):
        return None
    return values


def _deb822_stanzas(output):
    stanzas = []
    current = {}
    last_field = None
    for raw_line in str(output or "").splitlines() + [""]:
        if raw_line == "":
            if current:
                stanzas.append(current)
            current = {}
            last_field = None
            continue
        if raw_line[:1] in (" ", "\t"):
            if last_field is None:
                return []
            current[last_field] += "\n" + raw_line.strip()
            continue
        field, separator, value = raw_line.partition(":")
        if not separator or not field or field in current:
            return []
        current[field] = value.strip()
        last_field = field
    return stanzas


def _load_package_installer(
        path=PACKAGE_INSTALLER_PATH, lstat_func=None, resolve_func=None,
        platform_name=None):
    """Load only the root-controlled installer runtime with the pinned contract."""
    lstat_func = lstat_func or os.lstat
    resolve_func = resolve_func or (lambda candidate: candidate.resolve(strict=True))
    require_root = (platform_name or os.name) != "nt"

    def protected(candidate, expect_file):
        candidate = pathlib.Path(candidate)
        if not candidate.is_absolute():
            return False
        try:
            metadata = lstat_func(candidate)
        except OSError:
            return False
        expected = stat.S_ISREG if expect_file else stat.S_ISDIR
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not expected(metadata.st_mode)
            or (require_root and (
                int(metadata.st_uid) != 0 or bool(metadata.st_mode & 0o022)
            ))
        ):
            return False
        if expect_file:
            return all(protected(parent, False) for parent in candidate.parents)
        return True

    try:
        configured = pathlib.Path(path)
        if not protected(configured, True):
            return None
        runtime = pathlib.Path(resolve_func(configured))
        if not protected(runtime, True):
            return None
        spec = importlib.util.spec_from_file_location(
            "ming_package_installer_for_spark", runtime)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if (
            getattr(module, "PACKAGE_INSTALLER_CONTRACT", None)
            != REQUIRED_PACKAGE_INSTALLER_CONTRACT
            or not callable(getattr(module, "PackageInstaller", None))
            or not callable(getattr(module.PackageInstaller, "verify_installed", None))
        ):
            return None
        return module
    except (AttributeError, ImportError, NameError, OSError, RuntimeError,
            SyntaxError, TypeError, ValueError):
        return None


class SparkPackageHelper:
    MAX_DEB_BYTES = MAX_DEB_BYTES

    def __init__(
            self, runner=None, log_path=None, download_root=None, staging_root=None,
            source_list=None, euid_getter=None, environ=None, passwd_lookup=None,
            session_reader=None, lstat_func=None, keyring_path=None,
            verify_source=None, package_installer_module=None,
            package_installer_path=None):
        self.runner = runner or _run
        self.log_path = pathlib.Path(log_path or LOG_PATH)
        self.download_root = pathlib.Path(download_root or DOWNLOAD_ROOT)
        self.staging_root = pathlib.Path(staging_root or STAGING_ROOT)
        self.source_list = str(source_list or SPARK_SOURCE_LIST)
        self.keyring_path = str(keyring_path or SPARK_KEYRING)
        self.verify_source = (runner is None) if verify_source is None else bool(verify_source)
        self.package_installer_module = package_installer_module
        if package_installer_path is None:
            self.package_installer_paths = (
                PACKAGE_INSTALLER_PATH, PACKAGE_INSTALLER_FALLBACK_PATH)
        else:
            self.package_installer_paths = (pathlib.Path(package_installer_path),)
        self.package_installer_path = self.package_installer_paths[0]
        self.lstat_func = lstat_func or os.lstat
        self.euid_getter = euid_getter or getattr(os, "geteuid", lambda: 1)
        self.environ = dict(os.environ if environ is None else environ)
        if passwd_lookup is not None:
            self.passwd_lookup = passwd_lookup
        elif _pwd is not None:
            self.passwd_lookup = _pwd.getpwuid
        else:
            self.passwd_lookup = lambda uid: (_ for _ in ()).throw(KeyError(uid))
        self.session_reader = session_reader or self._loginctl_sessions

    def log_event(self, event, **values):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": str(event),
        }
        record.update(_redact_value(values))
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def _call(self, command, timeout=20):
        try:
            result = self.runner(tuple(str(value) for value in command), timeout=timeout)
            return int(result[0]), _redact(result[1]), _redact(result[2])
        except subprocess.TimeoutExpired:
            return 124, "", "command timed out"
        except OSError as error:
            return 127, "", _redact(error)

    def _loginctl_sessions(self, uid):
        code, output, _error = self._call(
            ("/usr/bin/loginctl", "list-sessions", "--no-legend", "--no-pager"),
            timeout=10,
        )
        if code != 0:
            return []
        sessions = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 2 or fields[1] != str(uid):
                continue
            session_id = fields[0]
            show_code, show_output, _show_error = self._call(
                (
                    "/usr/bin/loginctl",
                    "show-session",
                    session_id,
                    "--no-pager",
                    "--property=Active",
                    "--property=Remote",
                    "--property=Class",
                    "--property=Type",
                    "--property=Seat",
                    "--property=User",
                ),
                timeout=10,
            )
            if show_code != 0:
                continue
            values = {}
            for item in show_output.splitlines():
                field, separator, value = item.partition("=")
                if separator:
                    values[field] = value.strip()
            sessions.append(values)
        return sessions

    def authorize(self):
        try:
            if int(self.euid_getter()) != 0:
                raise ValueError("not root")
            raw_uid = self.environ.get("PKEXEC_UID", "")
            if not isinstance(raw_uid, str) or re.fullmatch(r"[1-9][0-9]*", raw_uid) is None:
                raise ValueError("invalid PKEXEC_UID")
            uid = int(raw_uid, 10)
            passwd_entry = self.passwd_lookup(uid)
            if int(getattr(passwd_entry, "pw_uid")) != uid:
                raise ValueError("passwd mismatch")
            if any(self.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
                raise ValueError("ssh caller")
            sessions = self.session_reader(uid)
            for session in sessions or ():
                if (
                    str(session.get("User", uid)) == str(uid)
                    and str(session.get("Active", "")).casefold() == "yes"
                    and str(session.get("Remote", "")).casefold() == "no"
                    and str(session.get("Class", "")).casefold() == "user"
                    and str(session.get("Type", "")).casefold() in {"x11", "wayland"}
                    and str(session.get("Seat", "")).strip()
                ):
                    return uid
        except (AttributeError, KeyError, TypeError, ValueError, OSError):
            pass
        raise HelperError(E_AUTHORIZATION_FAILED, "graphical local authentication session required")

    @staticmethod
    def validate_file_metadata(metadata, request_uid):
        mode = int(metadata.st_mode)
        if (
            not stat.S_ISREG(mode)
            or int(metadata.st_uid) != int(request_uid)
            or int(metadata.st_nlink) != 1
            or bool(mode & 0o022)
            or int(metadata.st_size) <= 0
            or int(metadata.st_size) > MAX_DEB_BYTES
        ):
            raise HelperError(E_FILE_UNSAFE, "downloaded DEB metadata is unsafe")

    def _reject_symlink_parents(self, relative):
        current = self.download_root
        parents = [current]
        for part in relative.parts[:-1]:
            current = current / part
            parents.append(current)
        for parent in parents:
            try:
                metadata = self.lstat_func(parent)
            except OSError as error:
                raise HelperError(E_FILE_UNSAFE, "download path is unavailable") from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise HelperError(E_FILE_UNSAFE, "download path contains a symlink")

    def _validate_layout(self, source):
        path = pathlib.Path(source)
        if not (path.is_absolute() or str(source).startswith("/")):
            raise HelperError(E_FILE_UNSAFE, "DEB path must be absolute")
        try:
            relative = path.relative_to(self.download_root)
        except ValueError as error:
            raise HelperError(E_FILE_UNSAFE, "DEB path is outside Spark download root") from error
        if len(relative.parts) != 2:
            raise HelperError(E_FILE_UNSAFE, "DEB path layout is invalid")
        package, filename = relative.parts
        if not _valid_package(package) or not filename.endswith(".deb") or filename == ".deb":
            raise HelperError(E_FILE_UNSAFE, "DEB path layout is invalid")
        self._reject_symlink_parents(relative)
        try:
            metadata = self.lstat_func(path)
        except OSError as error:
            raise HelperError(E_FILE_UNSAFE, "DEB file is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise HelperError(E_FILE_UNSAFE, "DEB file cannot be a symlink")
        return path, package

    def _ensure_staging_root(self):
        self.staging_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.staging_root, 0o700)
            metadata = os.lstat(self.staging_root)
        except OSError as error:
            raise HelperError(E_FILE_UNSAFE, "staging directory is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise HelperError(E_FILE_UNSAFE, "staging directory is unsafe")
        if int(self.euid_getter()) == 0 and int(getattr(metadata, "st_uid", 0)) != 0:
            raise HelperError(E_FILE_UNSAFE, "staging directory is not root-owned")

    @staticmethod
    def _validate_source_metadata(metadata, request_uid):
        try:
            SparkPackageHelper.validate_file_metadata(metadata, request_uid)
        except HelperError:
            # Windows has no POSIX group/other mode bits.  The deployed helper
            # runs on Linux; this narrow compatibility path keeps the asset's
            # real-file tests executable on the Windows build host while the
            # public metadata validator remains strict.
            if os.name != "nt":
                raise
            mode = int(metadata.st_mode)
            if (
                not stat.S_ISREG(mode)
                or int(metadata.st_uid) != int(request_uid)
                or int(metadata.st_nlink) != 1
                or int(metadata.st_size) <= 0
                or int(metadata.st_size) > MAX_DEB_BYTES
            ):
                raise

    def stage_deb(self, source, request_uid):
        path, package = self._validate_layout(source)
        try:
            pre_metadata = self.lstat_func(path)
            self._validate_source_metadata(pre_metadata, request_uid)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
        except HelperError:
            raise
        except OSError as error:
            raise HelperError(E_FILE_UNSAFE, "unable to open DEB safely") from error

        staged_path = None
        try:
            metadata = os.fstat(fd)
            self._validate_source_metadata(metadata, request_uid)
            if (int(metadata.st_dev), int(metadata.st_ino)) != (
                    int(pre_metadata.st_dev), int(pre_metadata.st_ino)):
                raise HelperError(E_FILE_UNSAFE, "DEB changed while opening")
            self._ensure_staging_root()
            staged_fd, staged_name = tempfile.mkstemp(
                prefix="%s-" % package, suffix=".deb", dir=str(self.staging_root))
            staged_path = pathlib.Path(staged_name)
            try:
                os.chmod(staged_path, 0o600)
                digest = hashlib.sha512()
                remaining = int(metadata.st_size)
                while remaining:
                    chunk = os.read(fd, min(1024 * 1024, remaining))
                    if not chunk:
                        raise HelperError(E_FILE_UNSAFE, "DEB ended during staging")
                    remaining -= len(chunk)
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(staged_fd, view)
                        view = view[written:]
                os.fsync(staged_fd)
            finally:
                os.close(staged_fd)
            try:
                directory_fd = os.open(
                    self.staging_root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            return StagedDeb(
                path=staged_path,
                sha512=digest.hexdigest(),
                source_path=path,
                source_identity=SourceIdentity.from_stat(pre_metadata),
            )
        except Exception:
            if staged_path is not None:
                try:
                    staged_path.unlink()
                except OSError:
                    pass
            raise
        finally:
            os.close(fd)

    @staticmethod
    def delete_source_if_unchanged(path, identity):
        try:
            metadata = os.lstat(path)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or int(metadata.st_nlink) != 1
                or SourceIdentity.from_stat(metadata) != identity
            ):
                return False
            os.unlink(path)
            return True
        except OSError:
            return False

    @staticmethod
    def _apt_cache_command(package, version, source_list):
        return (
            "/usr/bin/apt-cache",
            "-o", "Dir::Etc::sourcelist=%s" % source_list,
            "-o", "Dir::Etc::sourceparts=-",
            "show", "%s=%s" % (package, version),
        )

    def verify_staged_package(self, staged, expected_package):
        self._verify_fixed_source()
        code, output, _error = self._call(
            ("/usr/bin/dpkg-deb", "--field", str(staged.path),
             "Package", "Version", "Architecture"), timeout=30)
        if code != 0:
            raise HelperError(E_PACKAGE_UNVERIFIED, "dpkg metadata unavailable")
        fields = _parse_fields(output, ("Package", "Version", "Architecture"))
        if fields is None:
            raise HelperError(E_PACKAGE_UNVERIFIED, "dpkg metadata is malformed")
        if (
            not _valid_package(fields["Package"])
            or not VERSION_PATTERN.fullmatch(fields["Version"])
            or fields["Package"] != expected_package
            or fields["Architecture"] not in SUPPORTED_ARCHITECTURES
        ):
            raise HelperError(E_PACKAGE_UNVERIFIED, "DEB metadata does not match request")

        code, output, _error = self._call(
            self._apt_cache_command(fields["Package"], fields["Version"], self.source_list),
            timeout=30,
        )
        if code != 0:
            raise HelperError(E_RESOLVER_FAILED, "Spark repository resolver failed")
        matching = []
        for stanza in _deb822_stanzas(output):
            if (
                stanza.get("Package") == fields["Package"]
                and stanza.get("Version") == fields["Version"]
                and stanza.get("Architecture") == fields["Architecture"]
                and SHA512_PATTERN.fullmatch(stanza.get("SHA512", ""))
            ):
                matching.append(stanza)
        if len(matching) != 1 or not hmac.compare_digest(
                matching[0]["SHA512"].lower(), staged.sha512.lower()):
            raise HelperError(E_PACKAGE_UNVERIFIED, "Spark repository hash mismatch")
        return fields

    def _verify_fixed_source(self):
        if not self.verify_source:
            return
        source = pathlib.Path(self.source_list)
        keyring = pathlib.Path(self.keyring_path)
        try:
            source_metadata = os.lstat(source)
            key_metadata = os.lstat(keyring)
            source_text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise HelperError(E_PACKAGE_UNVERIFIED, "fixed Spark source is unavailable") from error
        if (
            stat.S_ISLNK(source_metadata.st_mode)
            or not stat.S_ISREG(source_metadata.st_mode)
            or stat.S_ISLNK(key_metadata.st_mode)
            or not stat.S_ISREG(key_metadata.st_mode)
            or not source_text == (
                "deb [signed-by=/etc/apt/keyrings/ming-spark-store.gpg] "
                "https://d.spark-app.store/store /\n")
            or int(source_metadata.st_size) <= 0
            or int(key_metadata.st_size) <= 0
            or (os.name != "nt" and (
                bool(source_metadata.st_mode & 0o022)
                or bool(key_metadata.st_mode & 0o022)
            ))
            or (int(self.euid_getter()) == 0 and (
                int(getattr(source_metadata, "st_uid", 0)) != 0
                or int(getattr(key_metadata, "st_uid", 0)) != 0
            ))
        ):
            raise HelperError(E_PACKAGE_UNVERIFIED, "fixed Spark source is unsafe")

    @staticmethod
    def _locked_apt_command(*arguments):
        return (
            "/usr/bin/flock", "--exclusive", "--timeout", str(PACKAGE_MANAGER_LOCK_TIMEOUT),
            "--conflict-exit-code", str(PACKAGE_MANAGER_BUSY_EXIT), PACKAGE_MANAGER_LOCK,
            "/usr/bin/apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "DPkg::Lock::Timeout=%s" % DPKG_LOCK_TIMEOUT,
            "-o", "Dir::Etc::sourcelist=%s" % SPARK_SOURCE_LIST,
            "-o", "Dir::Etc::sourceparts=-",
            *tuple(str(value) for value in arguments),
        )

    @staticmethod
    def _failure_code(returncode, output="", error=""):
        detail = "%s\n%s" % (output or "", error or "")
        if int(returncode) == PACKAGE_MANAGER_BUSY_EXIT or any(
                marker in detail.casefold() for marker in (
                    "could not get lock", "could not open lock", "unable to acquire the dpkg",
                    "unable to lock the administration directory")):
            return E_PACKAGE_BUSY
        if any(marker in detail.casefold() for marker in (
                "unmet dependencies", "held broken packages", "unable to correct problems",
                "pkgproblemresolver", "dependency problems", "dependency error", "depends:")):
            return E_RESOLVER_FAILED
        return E_PACKAGE_FAILED

    def _result(self, ok, request, **values):
        result = {
            "ok": bool(ok),
            "operation": request.operation,
            "package": request.package,
            "installed": False,
            "launch_ready": False,
            "launchers": [],
            "resolver": "spark",
            "log_path": str(self.log_path),
            "error_code": "",
            "error": "",
        }
        result.update(values)
        return _redact_value(result)

    def _verify_package_install(self, request):
        runtime = self.package_installer_module
        if runtime is None:
            for candidate in self.package_installer_paths:
                runtime = _load_package_installer(candidate)
                if runtime is not None:
                    break
        if (
            runtime is None
            or getattr(runtime, "PACKAGE_INSTALLER_CONTRACT", None)
            != REQUIRED_PACKAGE_INSTALLER_CONTRACT
            or not callable(getattr(runtime, "PackageInstaller", None))
            or not callable(getattr(runtime.PackageInstaller, "verify_installed", None))
        ):
            self.log_event(
                "postflight_rejected", package=request.package,
                error_code=E_PACKAGE_FAILED, detail="installer contract mismatch")
            return self._result(
                False, request, error_code=E_PACKAGE_FAILED,
                error="受控软件包验证组件不可用。")
        try:
            verifier = runtime.PackageInstaller(runner=self.runner)
            payload = verifier.verify_installed(request.package)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            self.log_event(
                "postflight_failed", package=request.package,
                error_code=E_PACKAGE_FAILED, detail=error)
            return self._result(False, request, error_code=E_PACKAGE_FAILED)
        if not isinstance(payload, dict) or payload.get("package") != request.package:
            return self._result(False, request, error_code=E_PACKAGE_FAILED)
        launchers = payload.get("launchers")
        if not isinstance(launchers, list):
            return self._result(False, request, error_code=E_PACKAGE_FAILED)
        payload_log_path = payload.get("log_path")
        if not isinstance(payload_log_path, str) or not payload_log_path.strip():
            payload_log_path = str(self.log_path)
        payload_error = payload.get("error")
        if not isinstance(payload_error, str):
            payload_error = ""
        if payload.get("installed") is not True:
            return self._result(
                False, request, installed=False, launchers=launchers,
                log_path=payload_log_path, error=payload_error,
                error_code=E_PACKAGE_FAILED)
        if payload.get("launch_ready") is not True:
            return self._result(
                False, request, installed=True, launch_ready=False,
                launchers=launchers, log_path=payload_log_path,
                error=payload_error, error_code=E_LAUNCH_NOT_READY)
        if payload.get("ok") is not True:
            return self._result(
                False, request, installed=True, launch_ready=True,
                launchers=launchers, log_path=payload_log_path,
                error=payload_error, error_code=E_PACKAGE_FAILED)
        return self._result(
            True, request, installed=True, launch_ready=True,
            launchers=launchers, log_path=payload_log_path,
            error=payload_error)

    def _run_typed_apt(self, request, operation, package=""):
        try:
            self._verify_fixed_source()
        except HelperError as error:
            self.log_event("rejected", operation=operation, package=package,
                           error_code=error.code)
            return self._result(False, request, error_code=error.code, error=error.message)
        command = self._locked_apt_command(operation, package) if package else self._locked_apt_command(operation)
        code, output, error = self._call(command, timeout=240)
        if code != 0:
            failure = self._failure_code(code, output, error)
            self.log_event("apt_failed", operation=operation, package=package, error_code=failure)
            return self._result(False, request, error_code=failure)
        if operation == "install":
            return self._verify_package_install(request)
        return self._result(
            True, request, installed=False, launch_ready=False)

    def execute(self, request, request_uid):
        staged = None
        try:
            if request.operation == "update":
                return self._run_typed_apt(request, "update")
            if request.operation == "remove":
                return self._run_typed_apt(request, "remove", request.package)
            if request.operation == "install_package":
                return self._run_typed_apt(request, "install", request.package)
            if request.operation != "install_deb":
                raise HelperError(E_REQUEST_INVALID, "unsupported operation")

            staged = self.stage_deb(request.source, request_uid)
            self.verify_staged_package(staged, request.package)
            command = (
                "/usr/local/sbin/ming-package-installer", "install", str(staged.path),
                "--resolver", "spark", "--json",
            )
            code, output, error = self._call(command, timeout=300)
            try:
                payload = json.loads(output.strip())
            except (TypeError, ValueError):
                payload = None
            if code != 0 or not isinstance(payload, dict):
                failure = (
                    str(payload.get("error_code"))
                    if isinstance(payload, dict) else self._failure_code(code, output, error)
                )
                if failure not in {
                    E_PACKAGE_BUSY, E_RESOLVER_FAILED, E_LAUNCH_NOT_READY, E_PACKAGE_FAILED,
                }:
                    failure = E_PACKAGE_FAILED
                return self._result(False, request, error_code=failure)
            if payload.get("ok") is not True or payload.get("resolver") != "spark":
                return self._result(False, request, error_code=E_PACKAGE_FAILED)
            if payload.get("installed") is not True:
                return self._result(False, request, error_code=E_PACKAGE_FAILED)
            if payload.get("launch_ready") is not True:
                return self._result(False, request, installed=True, error_code=E_LAUNCH_NOT_READY)
            deleted = False
            if request.delete_source:
                deleted = self.delete_source_if_unchanged(
                    staged.source_path, staged.source_identity)
            return self._result(
                True, request, installed=True, launch_ready=True, source_deleted=deleted)
        except HelperError as error:
            self.log_event("rejected", operation=request.operation, package=request.package,
                           error_code=error.code)
            return self._result(False, request, error_code=error.code, error=error.message)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.log_event("failed", operation=request.operation, package=request.package,
                           error_code=E_PACKAGE_FAILED, detail=error)
            return self._result(False, request, error_code=E_PACKAGE_FAILED)
        finally:
            if staged is not None:
                try:
                    staged.path.unlink()
                except OSError:
                    pass


def result_exit_code(result):
    if result.get("ok") is True:
        return 0
    if result.get("error_code") == E_AUTHORIZATION_FAILED:
        return 3
    if result.get("error_code") in {E_REQUEST_INVALID, E_FILE_UNSAFE, E_PACKAGE_UNVERIFIED}:
        return 2
    return 4


def main(argv=None, helper=None, stdout=None):
    stdout = stdout or sys.stdout
    helper = helper or SparkPackageHelper()
    try:
        request = parse_request(sys.argv[1:] if argv is None else argv)
        uid = helper.authorize()
        result = helper.execute(request, uid)
    except HelperError as error:
        request = Request("unknown")
        result = helper._result(False, request, error_code=error.code, error=error.message)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
