#!/usr/bin/env python3
"""Safe, on-demand Waydroid/Cage integration for Ming Toolbox.

The module deliberately keeps Android separate from the Wine runtime.  It does
not start a container at import time and accepts only structured local APK
paths.  The CLI emits JSON so the GTK toolbox can show the real state.
"""

import argparse
import contextlib
import errno
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux image always has fcntl.
    fcntl = None


MAX_APK_BYTES = 512 * 1024 * 1024
LOCK_STALE_SECONDS = 30
APP_OPERATION_TIMEOUT = 30
PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
APP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STATES = (
    "unavailable", "checking", "ready", "preparing", "installing",
    "installed", "launching", "running", "stopped", "failed",
    "uninstalling", "removed",
)
AUTHORIZED_ACTIONS = frozenset(("install-deps", "start-container", "stop-container", "repair"))
_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_CONFIG_LOCKS = {}
_CONFIG_LOCKS_GUARD = threading.Lock()


def authorized_command(action):
    if action not in AUTHORIZED_ACTIONS:
        raise ValueError("Android 授权操作不在白名单内。")
    return ("/usr/local/bin/ming-authorized-action", "android", action)


def validate_runtime_manifest(manifest, trusted_sources):
    if not isinstance(manifest, dict) or manifest.get("schema") != "ming.android.runtime.v1":
        return False, "运行时清单 schema 无效。"
    url = manifest.get("url")
    digest = str(manifest.get("sha256") or "").casefold()
    if not isinstance(url, str) or not re.fullmatch(r"https://[^ ]+", url):
        return False, "运行时下载地址必须是 HTTPS。"
    if trusted_sources.get(url, "").casefold() != digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False, "运行时来源或 SHA256 不在受信清单中。"
    return True, ""


def default_android_lab_state():
    return {
        "scope": "application", "arm_translation": False,
        "gpu_software_render": False, "software_rendering": False,
        "window_compat": False, "clipboard_share": False,
        "directory_share": False, "debug_logs": False,
    }


class AndroidLabStateStore:
    def __init__(self, root, lock_timeout=10):
        self.root = pathlib.Path(root)
        self.lock_timeout = max(0.0, float(lock_timeout))

    @staticmethod
    def _private_mode_ok(mode):
        return os.name != "posix" or stat.S_IMODE(mode) == 0o700

    @staticmethod
    def _owner_is_current(info):
        getuid = getattr(os, "getuid", None)
        return getuid is None or info.st_uid == getuid()

    @staticmethod
    def _validate_parent_chain(path):
        current = pathlib.Path(path).parent
        while True:
            try:
                info = current.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise AndroidStorageError("无法检查 Android 实验配置父目录：%s" % exc) from exc
            else:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise AndroidStorageError("Android 实验配置父目录不得是符号链接或普通文件。")
            if current.parent == current:
                break
            current = current.parent

    def _ensure_root(self, create=False):
        self._validate_parent_chain(self.root)
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            if not create:
                return False
            self.root.mkdir(parents=True, mode=0o700)
            info = self.root.lstat()
        except OSError as exc:
            raise AndroidStorageError("无法检查 Android 实验配置目录：%s" % exc) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AndroidStorageError("Android 实验配置目录不得是符号链接或普通文件。")
        if not self._owner_is_current(info):
            raise AndroidStorageError("Android 实验配置目录必须由当前用户拥有。")
        if not self._private_mode_ok(info.st_mode):
            raise AndroidStorageError("Android 实验配置目录权限必须为 0700。")
        return True

    def _path(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            raise ValueError("Android 包名无效。")
        return self.root / (str(package) + ".json")

    def _thread_lock(self, package):
        key = str(self._path(package))
        with _CONFIG_LOCKS_GUARD:
            return _CONFIG_LOCKS.setdefault(key, threading.Lock())

    @contextlib.contextmanager
    def _locked(self, package):
        self._ensure_root(create=True)
        lock = self._thread_lock(package)
        deadline = time.monotonic() + self.lock_timeout
        acquired = lock.acquire(blocking=False)
        while not acquired and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
            acquired = lock.acquire(blocking=False)
        if not acquired:
            raise AndroidStorageError("Android 实验配置锁超时。")
        try:
            yield
        finally:
            lock.release()

    def _secure_file(self, path, required=False):
        path = pathlib.Path(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            if required:
                raise AndroidStorageError("Android 实验配置文件不存在。")
            return False
        except OSError as exc:
            raise AndroidStorageError("无法检查 Android 实验配置文件：%s" % exc) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AndroidStorageError("Android 实验配置文件不得是符号链接或特殊文件。")
        if not self._owner_is_current(info):
            raise AndroidStorageError("Android 实验配置文件必须由当前用户拥有。")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
            raise AndroidStorageError("Android 实验配置文件权限必须为 0600。")
        return True

    def get(self, package):
        if not self._ensure_root(create=False):
            return default_android_lab_state()
        with self._locked(package):
            return self._get_unlocked(package)

    def _get_unlocked(self, package):
        state = default_android_lab_state()
        target = self._path(package)
        try:
            self._secure_file(target)
            descriptor = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or not self._owner_is_current(info):
                    raise AndroidStorageError("Android 实验配置文件必须由当前用户拥有的普通文件。")
                if info.st_size > 1024 * 1024:
                    raise AndroidStorageError("Android 实验配置文件内容过大。")
                data_bytes = b""
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    data_bytes += chunk
                data = json.loads(data_bytes.decode("utf-8"))
            finally:
                os.close(descriptor)
        except FileNotFoundError:
            data = {}
        except AndroidStorageError:
            raise
        except (OSError, UnicodeDecodeError, ValueError):
            data = {}
        if isinstance(data, dict):
            for key in state:
                if key != "scope":
                    state[key] = bool(data.get(key, False))
        return state

    def set(self, package, key, enabled):
        if key not in default_android_lab_state() or key == "scope":
            raise ValueError("实验室选项不在白名单内。")
        self._ensure_root(create=True)
        with self._locked(package):
            state = self._get_unlocked(package)
            state[key] = bool(enabled)
            target = self._path(package)
            self._secure_file(target)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".%s." % target.name, suffix=".tmp", dir=str(self.root)
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, ensure_ascii=False, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary_name, 0o600)
                self._secure_file(target)
                os.replace(temporary_name, target)
            finally:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
            return state


class ApkValidationError(ValueError):
    """Raised by the pure validation API for an untrusted APK input."""


class AndroidStorageError(ValueError):
    """Raised when an application state path is not private and regular."""


def _has_symlink_ancestor(path):
    """Return whether an existing ancestor of *path* is a symlink.

    ``Path.is_file`` and ``open`` follow parent symlinks, so checking only the
    final APK entry is not sufficient for an import supplied by another user.
    Missing ancestors are allowed here; callers that create state directories
    validate each newly-created component separately.
    """
    current = pathlib.Path(path).parent
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return True
        else:
            if stat.S_ISLNK(info.st_mode):
                return True
        if current.parent == current:
            return False
        current = current.parent


def _current_uid():
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else None


def _sha256(path):
    path = pathlib.Path(path)
    if _has_symlink_ancestor(path):
        raise OSError("refusing to hash a path with a symbolic-link parent")
    try:
        info = path.lstat()
    except OSError:
        raise
    if stat.S_ISLNK(info.st_mode):
        raise OSError("refusing to hash a symbolic link")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode):
            raise OSError("refusing to hash a non-regular file")
        if _current_uid() is not None and current.st_uid != _current_uid():
            raise OSError("refusing to hash a file owned by another user")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _default_apk_metadata(path):
    """Read only manifest metadata; never execute or extract the APK."""
    commands = (
        ("apkanalyzer", "manifest", "application-id", str(path)),
        ("aapt", "dump", "badging", str(path)),
    )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            completed = subprocess.run(
                list(command), capture_output=True, text=True, timeout=20,
                check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        output = completed.stdout or ""
        package = re.search(r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'", output)
        if package:
            abis = []
            native_code = re.search(r"native-code:\s*((?:'[^']+'\s*)+)", output)
            if native_code:
                abis.extend(re.findall(r"'([^']+)'", native_code.group(1)))
            return {
                "package": package.group(1),
                "version_code": package.group(2),
                "version_name": package.group(3),
                "abis": abis or ["universal"],
            }
        if command[0] == "apkanalyzer" and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", output.strip()):
            return {
                "package": output.strip(), "version_code": "0",
                "version_name": "", "abis": ["universal"],
            }
    raise ApkValidationError("系统缺少 aapt/apkanalyzer，无法读取 APK 清单。")


def _validate_apk_result(path, metadata_reader=None, home=None):
    if isinstance(path, (str, os.PathLike)) and str(path).startswith(("http://", "https://")):
        return {"ok": False, "state": "invalid_source", "error": "只允许导入本地 APK 文件。"}
    try:
        source = pathlib.Path(path)
    except (TypeError, ValueError):
        return {"ok": False, "state": "invalid_source", "error": "APK 路径无效。"}
    if not source.is_absolute() or source.name != source.name.strip():
        return {"ok": False, "state": "invalid_source", "error": "APK 必须使用本地绝对路径。"}
    try:
        if _has_symlink_ancestor(source):
            return {"ok": False, "state": "invalid_source", "error": "APK 路径父目录不得是符号链接。"}
        source_info = source.lstat()
        if not stat.S_ISREG(source_info.st_mode):
            return {"ok": False, "state": "invalid_source", "error": "APK 必须是普通本地文件。"}
        if _current_uid() is not None and source_info.st_uid != _current_uid():
            return {"ok": False, "state": "invalid_source", "error": "APK 必须由当前用户拥有。"}
        if source.is_symlink() or not source.is_file() or source.suffix.casefold() != ".apk":
            return {"ok": False, "state": "invalid_source", "error": "APK 必须是普通本地文件。"}
        size = source_info.st_size
    except OSError as exc:
        return {"ok": False, "state": "invalid_source", "error": str(exc)}
    if size <= 0 or size > MAX_APK_BYTES:
        return {"ok": False, "state": "invalid_source", "error": "APK 大小必须在 1 字节到 512 MiB 之间。"}
    try:
        metadata = (metadata_reader or _default_apk_metadata)(source)
    except (OSError, ValueError, ApkValidationError) as exc:
        return {"ok": False, "state": "invalid_manifest", "error": str(exc)}
    if not isinstance(metadata, dict):
        return {"ok": False, "state": "invalid_manifest", "error": "APK 清单格式无效。"}
    package = str(metadata.get("package") or "")
    if not PACKAGE_RE.fullmatch(package) or len(package) > 190:
        return {"ok": False, "state": "invalid_package", "error": "APK 包名不是有效的反向域名格式。"}
    raw_abis = metadata.get("abis") or ["universal"]
    if isinstance(raw_abis, str):
        raw_abis = [raw_abis]
    abis = {str(item).casefold() for item in raw_abis}
    arm_abis = {"arm64-v8a", "armeabi-v7a", "armeabi", "arm", "aarch64"}
    if abis and abis <= arm_abis:
        return {"ok": False, "state": "unsupported_architecture", "error": "这是 ARM APK；请在 Ming 实验室启用 ARM 转译后再试。"}
    # Stable Ming ships only the 64-bit x86 Waydroid image.  A 32-bit x86
    # native library is not a substitute for x86_64 and must be rejected just
    # like an ARM-only package.  APKs without native code are universal.
    if "universal" in abis:
        architecture = "universal"
    elif "x86_64" in abis:
        architecture = "x86_64"
    else:
        return {"ok": False, "state": "unsupported_architecture", "error": "APK 只支持 x86_64 或 universal 架构。"}
    return {
        "ok": True,
        "state": "validated",
        "path": str(source),
        "package": package,
        "version_code": str(metadata.get("version_code") or "0"),
        "version_name": str(metadata.get("version_name") or ""),
        "architecture": architecture,
        "abis": sorted(abis),
        "size": size,
        "sha256": _sha256(source),
    }


def validate_apk(path, metadata_reader=None):
    """Pure API used by callers that prefer exceptions over JSON states."""
    result = _validate_apk_result(path, metadata_reader=metadata_reader)
    if not result["ok"]:
        raise ApkValidationError(result["error"])
    return result


class AndroidRuntime:
    def __init__(self, home=None, runner=None, metadata_reader=None,
                 executable=None, probes=None, sleeper=None, spawner=None,
                 environment=None, runtime=None, terminator=None,
                 production=None, operation_timeout=APP_OPERATION_TIMEOUT):
        self.home = pathlib.Path(home or pathlib.Path.home())
        self._runner_injected = runner is not None
        self._spawner_injected = spawner is not None
        self._production_explicit = production is not None
        self.runner = runner or self._run
        self.metadata_reader = metadata_reader
        self.executable = executable or shutil.which
        if environment is not None:
            probes = dict(environment)
            probes["ram_mb"] = int(probes.get("total_memory", 0) / (1024 * 1024))
            probes["render_nodes"] = ["/dev/dri/renderD128"] if probes.get("gpu") else []
        self.probes = probes
        self.runtime = runtime
        if runtime is not None:
            if probes is None:
                self.probes = runtime.probes
            if executable is None:
                self.executable = runtime.executable
            if sleeper is None:
                self.sleeper = runtime.sleeper
        self.sleeper = sleeper or time.sleep
        self.spawner = spawner or subprocess.Popen
        # Tests and callers that inject a command runner retain the old dry
        # execution path.  The real CLI has neither injection and therefore
        # gets the production gates and asynchronous Cage process handling.
        self.production = bool(production) if production is not None else (
            not self._runner_injected and not self._spawner_injected
            and probes is None and environment is None and runtime is None
        )
        self.operation_timeout = max(1, int(operation_timeout))
        self.apps_root = self.home / ".local/share/ming-android/apps"
        self.state_root = self.home / ".local/state/ming-os/android"
        self.terminator = terminator or self._terminate_pid
        self._active_processes = {}
        self._process_records = {}

    @staticmethod
    def _mode_is_private(mode, expected):
        """POSIX image paths must have exact private modes.

        Windows development hosts do not preserve POSIX permission bits, so
        ownership/mode enforcement is applied by the Linux image at runtime.
        Symlink and regular-file checks remain enforced on every platform.
        """
        return os.name != "posix" or stat.S_IMODE(mode) == expected

    @staticmethod
    def _owner_is_current(st):
        getuid = getattr(os, "getuid", None)
        return getuid is None or st.st_uid == getuid()

    def _secure_path(self, path, kind, mode, label, required=False):
        """Validate one state path without following symlinks."""
        path = pathlib.Path(path)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if required:
                raise AndroidStorageError("Android %s 不存在。" % label)
            return False
        except OSError as exc:
            raise AndroidStorageError("无法检查 Android %s：%s" % (label, exc)) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise AndroidStorageError("Android %s 不得是符号链接。" % label)
        if kind == "dir" and not stat.S_ISDIR(metadata.st_mode):
            raise AndroidStorageError("Android %s 必须是目录。" % label)
        if kind == "file" and not stat.S_ISREG(metadata.st_mode):
            raise AndroidStorageError("Android %s 必须是普通文件。" % label)
        if not self._owner_is_current(metadata):
            raise AndroidStorageError("Android %s 必须由当前用户拥有。" % label)
        if not self._mode_is_private(metadata.st_mode, mode):
            raise AndroidStorageError("Android %s 权限必须为 %04o。" % (label, mode))
        return True

    def _private_dir(self, path, label, create=False):
        path = pathlib.Path(path)
        self._validate_directory_ancestors(path)
        if create:
            missing = []
            current = path
            while True:
                try:
                    current.lstat()
                    break
                except FileNotFoundError:
                    missing.append(current)
                    if current.parent == current:
                        break
                    current = current.parent
            for item in reversed(missing):
                try:
                    item.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                self._validate_directory_ancestors(item)
        self._secure_path(path, "dir", 0o700, label, required=True)
        return path

    def _validate_directory_ancestors(self, path):
        """Reject a symlink anywhere in a user state directory chain."""
        current = pathlib.Path(path)
        ancestors = []
        while True:
            ancestors.append(current)
            if current == self.home or current.parent == current:
                break
            current = current.parent
        for item in ancestors:
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AndroidStorageError("无法检查 Android 目录：%s" % exc) from exc
            if stat.S_ISLNK(info.st_mode):
                raise AndroidStorageError("Android 目录不得是符号链接：%s" % item)
            if not stat.S_ISDIR(info.st_mode):
                raise AndroidStorageError("Android 目录必须是目录：%s" % item)
            if not self._owner_is_current(info):
                raise AndroidStorageError("Android 目录必须由当前用户拥有：%s" % item)

    def _private_file(self, path, label, required=False):
        self._secure_path(path, "file", 0o600, label, required=required)
        return pathlib.Path(path)

    def _copy_apk_secure(self, source, destination, temporary, expected_sha256):
        """Copy an APK through O_NOFOLLOW descriptors and verify its digest.

        The source is opened only after validation and hashed while copied;
        replacing the source path during the operation cannot redirect the
        read, and a changed file is rejected before Waydroid sees it.
        """
        source = pathlib.Path(source)
        destination = pathlib.Path(destination)
        temporary = pathlib.Path(temporary)
        if _has_symlink_ancestor(source):
            raise AndroidStorageError("APK 路径父目录不得是符号链接。")
        self._validate_directory_ancestors(destination.parent)
        self._validate_directory_ancestors(temporary.parent)
        self._private_file(destination, "APK 安装文件")
        self._private_file(temporary, "APK 临时文件")
        try:
            source_fd = os.open(str(source), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise AndroidStorageError("无法安全打开 APK：%s" % exc) from exc
        target_fd = None
        try:
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode):
                raise AndroidStorageError("APK 必须是普通文件。")
            if not self._owner_is_current(source_info):
                raise AndroidStorageError("APK 必须由当前用户拥有。")
            if source_info.st_size <= 0 or source_info.st_size > MAX_APK_BYTES:
                raise AndroidStorageError("APK 大小超出限制。")
            try:
                temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                self._private_file(temporary, "APK 临时文件", required=True)
                temporary.unlink()
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            target_fd = os.open(str(temporary), flags, 0o600)
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_APK_BYTES:
                    raise AndroidStorageError("APK 大小超出限制。")
                digest.update(chunk)
                written = 0
                while written < len(chunk):
                    count = os.write(target_fd, chunk[written:])
                    if count <= 0:
                        raise AndroidStorageError("安全复制 APK 未写入完整文件。")
                    written += count
            if total != source_info.st_size:
                raise AndroidStorageError("APK 在复制期间大小发生变化，已拒绝安装。")
            actual = digest.hexdigest()
            if actual.casefold() != str(expected_sha256).casefold():
                raise AndroidStorageError("APK 在复制期间发生变化，已拒绝安装。")
            if hasattr(os, "fchmod"):
                os.fchmod(target_fd, 0o600)
            if hasattr(os, "fsync"):
                os.fsync(target_fd)
        except OSError as exc:
            raise AndroidStorageError("安全复制 APK 失败：%s" % exc) from exc
        finally:
            try:
                os.close(source_fd)
            except OSError:
                pass
            if target_fd is not None:
                try:
                    os.close(target_fd)
                except OSError:
                    pass
        try:
            self._secure_path(temporary, "file", 0o600, "APK 临时文件", required=True)
            os.replace(temporary, destination)
        except OSError as exc:
            raise AndroidStorageError("无法提交 APK 安装文件：%s" % exc) from exc
        return actual

    def _atomic_json(self, path, data, label):
        text = json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"
        self._atomic_text(path, text, label)

    def _read_private_text(self, path, label, max_bytes=1024 * 1024):
        path = pathlib.Path(path)
        self._private_file(path, label, required=True)
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not self._owner_is_current(info):
                raise AndroidStorageError("Android %s 必须是当前用户拥有的普通文件。" % label)
            if info.st_size > max_bytes:
                raise AndroidStorageError("Android %s 内容过大。" % label)
            chunks = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise AndroidStorageError("Android %s 内容过大。" % label)
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")
        finally:
            os.close(descriptor)

    def _app_storage(self, package, create=False):
        if not PACKAGE_RE.fullmatch(str(package)):
            raise AndroidStorageError("Android 包名无效。")
        package = str(package)
        base = self.home / ".local/share/ming-android"
        apps_root = base / "apps"
        app_dir = apps_root / package
        if create:
            self._private_dir(base, "应用数据目录", create=True)
            self._private_dir(apps_root, "应用目录", create=True)
            self._private_dir(app_dir, "应用包目录", create=True)
        else:
            self._private_dir(base, "应用数据目录")
            self._private_dir(apps_root, "应用目录")
            self._private_dir(app_dir, "应用包目录")
        # Android uses one Waydroid container rather than a Wine prefix, but
        # an optional prefix from older builds must never be followed.
        self._secure_path(app_dir / "prefix", "dir", 0o700, "应用前缀")
        for name in ("metadata.json", "artifact.apk", "artifact.apk.tmp", "operation.lock"):
            self._private_file(app_dir / name, "应用 %s" % name)
        return app_dir, app_dir / "metadata.json"

    @staticmethod
    def _terminate_pid(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            return True
        except (OSError, ValueError, TypeError):
            return False
        return True

    @staticmethod
    def _run(command, **kwargs):
        completed = subprocess.run(list(command), shell=False, check=False, **kwargs)
        return completed.returncode, completed.stdout, completed.stderr

    def _probe(self):
        if self.probes is not None:
            return dict(self.probes)
        try:
            ram_mb = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
        except (AttributeError, OSError, ValueError):
            ram_mb = 0
        dbus_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        dbus_ready = False
        if dbus_address:
            # A session bus may use an abstract socket; a path address must
            # point at a real socket rather than merely being set in the env.
            if "unix:path=" in dbus_address:
                socket_path = dbus_address.split("unix:path=", 1)[1].split(",", 1)[0]
                dbus_ready = pathlib.Path(socket_path).exists()
            else:
                dbus_ready = "unix:abstract=" in dbus_address
        if not dbus_ready:
            dbus_ready = pathlib.Path("/run/dbus/system_bus_socket").exists()
        wayland_ready = False
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
        if wayland_display and runtime_dir:
            socket = pathlib.Path(runtime_dir) / wayland_display
            try:
                wayland_ready = stat.S_ISSOCK(socket.stat().st_mode)
            except OSError:
                wayland_ready = False
        return {
            "ram_mb": ram_mb,
            "render_nodes": [str(path) for path in pathlib.Path("/dev/dri").glob("renderD*")],
            "binderfs": pathlib.Path("/dev/binderfs").exists() or pathlib.Path("/dev/binder").exists(),
            "lxc": bool(self.executable("lxc-start")),
            "dbus": dbus_ready,
            "wayland": wayland_ready,
        }

    def status(self):
        probe = self._probe()
        reasons = []
        for key, label in (("ram_mb", "memory"), ("binderfs", "binderfs"), ("lxc", "lxc"), ("dbus", "dbus"), ("wayland", "Wayland")):
            value = probe.get(key)
            if key == "ram_mb" and int(value or 0) < 4096:
                reasons.append("memory below 4GB")
            elif key != "ram_mb" and not value:
                reasons.append(label + " unavailable")
        if not probe.get("render_nodes"):
            reasons.append("GPU render node unavailable")
        for command in ("waydroid", "cage"):
            if not self.executable(command):
                reasons.append(command + " missing")
        container = "unknown"
        if self.probes is not None:
            # Injected probes are used by the source tests and may omit a
            # container state entirely.  An explicit state, however, must be
            # honoured rather than inferred from a substring such as
            # ``RUNNING`` inside ``NOT RUNNING``.
            if "container" in self.probes:
                container = self._normalize_container_state(self.probes.get("container"))
        elif self.executable("waydroid"):
            container = self._waydroid_container_state()
        else:
            container = "unavailable"
        if container in {"not_initialized", "stopped", "unavailable", "unknown"} and (
            self.probes is None or "container" in self.probes
        ):
            reasons.append("Waydroid container unavailable")
        return {
            "ok": not reasons,
            "state": "ready" if not reasons else "unavailable",
            "runtime": "waydroid+cage",
            "reasons": reasons,
            "memory_mb": int(probe.get("ram_mb") or 0),
            "gpu": bool(probe.get("render_nodes")),
            "container": container,
        }

    @staticmethod
    def _normalize_container_state(value):
        """Normalize a caller/Waydroid state without substring ambiguity."""
        text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if text in {"running", "started", "ready"}:
            return "running"
        if text in {"stopped", "not_running", "notinitialized", "not_initialized", "inactive"}:
            return "not_initialized"
        if text in {"unavailable", "missing", "failed"}:
            return "unavailable"
        return "unknown"

    def _waydroid_container_state(self):
        """Read exact status lines from Waydroid, never a loose substring."""
        try:
            rc, output, _error = self.runner(
                ("waydroid", "status"), capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        if rc != 0:
            return "unavailable"
        lines = [line.strip().casefold() for line in (output or "").splitlines()]
        if any(re.fullmatch(r"(?:session|container)\s*:\s*running", line) for line in lines):
            # A real RUNNING line is accepted only when the complete value is
            # RUNNING; ``NOT RUNNING`` can never match this expression.
            if any(re.fullmatch(r"(?:session|container)\s*:\s*not\s+running", line) for line in lines):
                return "not_initialized"
            return "running"
        if any(re.fullmatch(r"(?:session|container)\s*:\s*(?:stopped|not\s+running|inactive)", line) for line in lines):
            return "not_initialized"
        return "not_initialized"

    def check_environment(self):
        result = self.status()
        reasons = []
        probe = self._probe()
        if int(probe.get("ram_mb") or 0) < 4096:
            reasons.append("memory_below_4gb")
        if not probe.get("render_nodes"):
            reasons.append("gpu_unavailable")
        for key, label in (("binderfs", "binderfs_missing"), ("lxc", "lxc_missing"), ("dbus", "dbus_missing"), ("wayland", "wayland_missing")):
            if not probe.get(key):
                reasons.append(label)
        return {"stable": not reasons, "ok": not reasons, "state": result["state"], "reasons": reasons}

    def _runtime_gate(self, operation):
        """Return a failure result for production operations before mutation."""
        if not self.production:
            return None
        result = self.status()
        if result.get("ok"):
            return None
        return {
            "ok": False,
            "state": "unavailable",
            "error": "Android 当前不可用（%s）：%s" % (
                operation, "、".join(result.get("reasons", [])) or "运行时门槛未满足"
            ),
        }

    def validate_apk(self, path):
        return _validate_apk_result(path, metadata_reader=self.metadata_reader, home=self.home)

    # Compatibility alias used by the toolbox controller and older callers.
    def install(self, path):
        return self.install_apk(path)

    def _log(self, package, action, state, detail=""):
        if not PACKAGE_RE.fullmatch(str(package)):
            raise AndroidStorageError("Android 日志包名无效。")
        path = self.state_root / package / ("install.jsonl" if action in {"install", "uninstall"} else "launch.jsonl")
        self._private_dir(self.state_root, "状态目录", create=True)
        self._private_dir(path.parent, "应用状态目录", create=True)
        self._private_file(path, "日志文件")
        record = {"action": action, "state": state, "detail": str(detail)[:500]}
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not self._owner_is_current(info):
                raise AndroidStorageError("Android 日志文件必须是当前用户拥有的普通文件。")
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            os.write(descriptor, payload)
            if hasattr(os, "fsync"):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _list_readback(self, package):
        """Read Waydroid's installed package list as True/False/None.

        Recent Waydroid releases print ``packageName: <id>`` while older
        releases printed a whitespace-separated table.  A command failure or
        timeout is deliberately ``None`` (unknown), never an assertion that an
        application is absent.
        """
        self._last_list_error = None
        try:
            rc, output, _error = self.runner(
                ("waydroid", "app", "list"), capture_output=True, text=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            self._last_list_error = "timeout"
            return None
        except (OSError, subprocess.SubprocessError):
            self._last_list_error = "unavailable"
            return None
        if rc != 0:
            self._last_list_error = "failed"
            return None
        package = str(package)
        found = set()
        for line in (output or "").splitlines():
            match = re.match(r"^\s*packageName\s*:\s*([^\s]+)\s*$", line, re.IGNORECASE)
            if match:
                found.add(match.group(1))
                continue
            # Preserve compatibility with the old table output.  Ignore
            # column headers and version fields by requiring a valid package ID.
            token = line.strip().split()[0] if line.strip() else ""
            if PACKAGE_RE.fullmatch(token):
                found.add(token)
        return package in found

    def _list_contains(self, package):
        return self._list_readback(package) is True

    def _desktop_file(self, package, label=None):
        directory = self.home / ".local/share/applications"
        target_directory = self.home / ".local/share/ming-android/targets"
        self._validate_directory_ancestors(directory)
        try:
            directory_info = directory.lstat()
        except FileNotFoundError:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory_info = directory.lstat()
        if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
            raise AndroidStorageError("Android 桌面应用目录不得是符号链接或普通文件。")
        if not self._owner_is_current(directory_info):
            raise AndroidStorageError("Android 桌面应用目录必须由当前用户拥有。")
        try:
            directory.chmod(0o700)
        except OSError as exc:
            raise AndroidStorageError("无法设置 Android 桌面应用目录权限：%s" % exc) from exc
        self._secure_path(directory, "dir", 0o700, "桌面应用目录", required=True)
        self._private_dir(target_directory.parent, "应用数据目录", create=True)
        self._private_dir(target_directory, "桌面入口目录", create=True)
        path = directory / ("ming-android-" + package.replace(".", "-") + ".desktop")
        target = target_directory / ("ming-android-target-" + package.replace(".", "-") + ".desktop")
        target_text = (
            "[Desktop Entry]\nType=Application\nName=%s\n"
            "Exec=/usr/local/bin/ming-android-runtime launch %s\n"
            "Terminal=false\n" % (label or package, package)
        )
        visible_text = (
            "[Desktop Entry]\nType=Application\n"
            "Name=%s\nExec=/usr/local/bin/ming-launch --desktop-file %s --source toolbox\n"
            "Icon=application-x-apk\nTerminal=false\nCategories=System;\n"
            "X-Ming-Android-Package=%s\n" % (label or package, target, package)
        )
        self._atomic_text(target, target_text, "Android 启动目标")
        self._atomic_text(path, visible_text, "Android 桌面入口")
        return path

    def _atomic_text(self, path, content, label):
        """Write user state to a private temporary file then replace it."""
        path = pathlib.Path(path)
        self._private_dir(path.parent, label + "目录", create=True)
        self._validate_directory_ancestors(path.parent)
        self._private_file(path, label)
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
            )
        except OSError as exc:
            raise AndroidStorageError("无法写入 Android %s：%s" % (label, exc)) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            self._secure_path(path, "file", 0o600, label)
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @contextlib.contextmanager
    def _app_operation_lock(self, package, create=False):
        """Serialize install/uninstall/config mutations for one package."""
        app_dir, _metadata = self._app_storage(package, create=create)
        lock_path = app_dir / "operation.lock"
        self._validate_directory_ancestors(lock_path.parent)
        fd = os.open(
            str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        thread_key = str(lock_path)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(thread_key, threading.Lock())
        acquired_thread = False
        acquired_file = False
        try:
            deadline = time.monotonic() + self.operation_timeout
            while not acquired_thread:
                acquired_thread = thread_lock.acquire(blocking=False)
                if acquired_thread:
                    break
                if time.monotonic() >= deadline:
                    raise AndroidStorageError("Android 应用操作锁超时。")
                self.sleeper(0.05)
            if fcntl is not None:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired_file = True
                        break
                    except (BlockingIOError, OSError) as exc:
                        if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN):
                            if time.monotonic() >= deadline:
                                raise AndroidStorageError("Android 应用操作锁超时。")
                            self.sleeper(0.05)
                            continue
                        raise
            else:
                acquired_file = True
            yield app_dir
        finally:
            if acquired_file and fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            if acquired_thread:
                thread_lock.release()
            os.close(fd)

    def _remove_tree_secure(self, path, label):
        """Remove one private application directory without following links."""
        path = pathlib.Path(path)
        self._private_dir(path, label, create=False)
        try:
            entries = list(os.scandir(path))
        except OSError as exc:
            raise AndroidStorageError("无法读取 Android %s：%s" % (label, exc)) from exc
        for entry in entries:
            child = pathlib.Path(entry.path)
            try:
                info = child.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
                if stat.S_ISLNK(info.st_mode):
                    raise AndroidStorageError("Android %s 包含符号链接。" % label)
                child.unlink()
            elif stat.S_ISDIR(info.st_mode):
                self._remove_tree_secure(child, label)
            else:
                raise AndroidStorageError("Android %s 包含特殊文件。" % label)
        path.rmdir()

    def _unlink_secure(self, path, label):
        """Unlink a private regular file, rejecting symlink redirection."""
        path = pathlib.Path(path)
        self._validate_directory_ancestors(path.parent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AndroidStorageError("无法检查 Android %s：%s" % (label, exc)) from exc
        if stat.S_ISLNK(info.st_mode):
            raise AndroidStorageError("Android %s 不得是符号链接。" % label)
        if not stat.S_ISREG(info.st_mode):
            raise AndroidStorageError("Android %s 必须是普通文件。" % label)
        if not self._owner_is_current(info):
            raise AndroidStorageError("Android %s 必须由当前用户拥有。" % label)
        path.unlink()
        return True

    def _launcher_paths(self, package):
        package = str(package)
        return (
            self.home / ".local/share/ming-android/targets" / ("ming-android-target-" + package.replace(".", "-") + ".desktop"),
            self.home / ".local/share/applications" / ("ming-android-" + package.replace(".", "-") + ".desktop"),
        )

    def _cleanup_install_files(self, app_dir, metadata_path, package=None, remove_launchers=False):
        """Remove local artifacts after an unconfirmed install.

        New installs can create the target and visible desktop entries before
        metadata is committed.  Remove those entries only when the package had
        no prior metadata, so a failed update cannot erase a working launcher.
        """
        app_dir = pathlib.Path(app_dir)
        metadata_path = pathlib.Path(metadata_path)
        try:
            self._private_dir(app_dir, "应用包目录")
            for child in (app_dir / "artifact.apk", app_dir / "artifact.apk.tmp", metadata_path):
                try:
                    self._private_file(child, "Android 安装残留")
                except AndroidStorageError:
                    if child == metadata_path:
                        raise
                    continue
                child.unlink(missing_ok=True)
            if package is not None and remove_launchers:
                for launcher, label in zip(self._launcher_paths(package), ("Android 启动目标残留", "Android 桌面入口残留")):
                    try:
                        self._unlink_secure(launcher, label)
                    except FileNotFoundError:
                        pass
            # Keep the operation lock itself until the context manager closes;
            # an empty app directory is safe to remove afterwards.
            remaining = [item for item in app_dir.iterdir() if item.name != "operation.lock"]
            if not remaining:
                return
        except (OSError, AndroidStorageError):
            return

    def _remove_waydroid_package(self, package):
        """Remove a package and require a positive remote readback."""
        try:
            rc, output, error = self.runner(
                ("waydroid", "app", "remove", package),
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if rc != 0:
            return False
        return self._list_readback(package) is False

    def install_apk(self, path):
        gate = self._runtime_gate("install")
        if gate is not None:
            return gate
        result = self.validate_apk(path)
        if not result["ok"]:
            return result
        package = result["package"]
        waydroid_installed = False
        app_dir = None
        metadata_path = None
        had_metadata = False
        rollback_ok = True
        try:
            with self._app_operation_lock(package, create=True) as locked_dir:
                app_dir = locked_dir
                metadata_path = app_dir / "metadata.json"
                try:
                    current = json.loads(self._read_private_text(metadata_path, "Android metadata"))
                    had_metadata = True
                except FileNotFoundError:
                    current = {}
                except AndroidStorageError as exc:
                    if metadata_path.exists():
                        had_metadata = True
                        raise
                    current = {}
                except (OSError, ValueError):
                    current = {}
                if current.get("sha256") not in (None, result["sha256"]):
                    return {"ok": False, "state": "package_conflict", "error": "同一包名已有不同 APK，请先卸载后再安装。"}
                artifact = app_dir / "artifact.apk"
                temporary = app_dir / "artifact.apk.tmp"
                self._copy_apk_secure(result["path"], artifact, temporary, result["sha256"])
                try:
                    rc, output, error = self.runner(
                        ("waydroid", "app", "install", str(artifact)),
                        capture_output=True, text=True, timeout=900,
                    )
                except subprocess.TimeoutExpired as exc:
                    self._cleanup_install_files(app_dir, metadata_path, package, remove_launchers=not had_metadata)
                    self._log(package, "install", "install_timeout", exc)
                    return {"ok": False, "state": "install_timeout", "error": "Waydroid 安装超时。"}
                if rc != 0:
                    self._cleanup_install_files(app_dir, metadata_path, package, remove_launchers=not had_metadata)
                    self._log(package, "install", "install_failed", error or output)
                    return {"ok": False, "state": "install_failed", "error": "Waydroid 安装失败。"}
                list_state = self._list_readback(package)
                if list_state is not True:
                    self._cleanup_install_files(app_dir, metadata_path, package, remove_launchers=not had_metadata)
                    state = "install_timeout" if self._last_list_error == "timeout" else "install_unconfirmed"
                    self._log(package, "install", state, error or output)
                    return {"ok": False, "state": state, "error": "Waydroid 未确认 APK 已安装。"}
                waydroid_installed = True
                desktop = self._desktop_file(package, package)
                metadata = dict(
                    result, state="installed", package_id=package,
                    artifact=str(artifact), desktop_file=str(desktop),
                )
                self._atomic_json(metadata_path, metadata, "Android metadata")
                self._log(package, "install", "installed", "readback confirmed")
                return {
                    "ok": True, "state": "installed", "package_id": package,
                    "metadata_file": str(metadata_path), "desktop_file": str(desktop),
                    "sha256": result["sha256"],
                }
        except AndroidStorageError as exc:
            if app_dir is not None and metadata_path is not None:
                self._cleanup_install_files(app_dir, metadata_path, package, remove_launchers=not had_metadata)
            if waydroid_installed:
                rollback_ok = self._remove_waydroid_package(package)
            try:
                detail = str(exc)
                if not rollback_ok:
                    detail += "；Waydroid 回滚未确认"
                self._log(package, "install", "failed", detail)
            except AndroidStorageError:
                pass
            state = "operation_timeout" if "锁超时" in str(exc) else "storage_insecure"
            if not rollback_ok:
                state = "rollback_failed"
            message = str(exc) + ("；Waydroid 回滚未确认。" if not rollback_ok else "")
            return {"ok": False, "state": state, "error": message}
        except (OSError, subprocess.SubprocessError) as exc:
            if app_dir is not None and metadata_path is not None:
                self._cleanup_install_files(app_dir, metadata_path, package, remove_launchers=not had_metadata)
            if waydroid_installed:
                rollback_ok = self._remove_waydroid_package(package)
            try:
                detail = str(exc)
                if not rollback_ok:
                    detail += "；Waydroid 回滚未确认"
                self._log(package, "install", "failed", detail)
            except AndroidStorageError:
                pass
            state = "rollback_failed" if not rollback_ok else "failed"
            message = str(exc) + ("；Waydroid 回滚未确认。" if not rollback_ok else "")
            return {"ok": False, "state": state, "error": message}

    def uninstall(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        package = str(package)
        lock_path = self.state_root / package / "cage.lock"
        active_process = self._active_processes.get(package)
        # A stale lock may be left behind after a crash.  Recover it only after
        # proving the recorded PID is gone or its start identity changed.
        if active_process is not None or lock_path.exists():
            try:
                if active_process is None and lock_path.exists() and self._recover_stale_lock(lock_path, package):
                    pass
                elif self._active_processes.get(package) is not None or lock_path.exists():
                    stop_result = self.stop(package)
                    if not stop_result.get("ok"):
                        self._log(package, "uninstall", "uninstall_unconfirmed", stop_result.get("error", ""))
                        return {
                            "ok": False,
                            "state": "uninstall_unconfirmed",
                            "error": "卸载前无法安全停止 Android 应用。",
                        }
            except AndroidStorageError as exc:
                return {"ok": False, "state": "storage_insecure", "error": str(exc)}
        app_dir = self.apps_root / package
        app_exists = False
        try:
            info = app_dir.lstat()
            app_exists = True
            if stat.S_ISLNK(info.st_mode):
                raise AndroidStorageError("Android 应用包目录不得是符号链接。")
            self._app_storage(package, create=False)
        except AndroidStorageError as exc:
            return {"ok": False, "state": "storage_insecure", "error": str(exc)}
        except FileNotFoundError:
            # The APK may have been removed locally while the Waydroid package
            # still exists; remote readback remains the source of truth.
            app_exists = False
        try:
            rc, output, error = self.runner(
                ("waydroid", "app", "remove", package),
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            self._log(package, "uninstall", "uninstall_timeout", exc)
            return {"ok": False, "state": "uninstall_timeout", "error": "Waydroid 卸载超时。"}
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(package, "uninstall", "uninstall_unconfirmed", exc)
            return {"ok": False, "state": "uninstall_unconfirmed", "error": "无法确认 Waydroid 卸载状态。"}
        if rc != 0:
            self._log(package, "uninstall", "uninstall_failed", error or output)
            return {"ok": False, "state": "uninstall_failed", "error": "Waydroid 卸载失败。"}
        listed = self._list_readback(package)
        if listed is None:
            state = "uninstall_timeout" if self._last_list_error == "timeout" else "uninstall_unconfirmed"
            self._log(package, "uninstall", state, error or output)
            return {"ok": False, "state": state, "error": "Waydroid 未确认应用已卸载。"}
        if listed is True:
            self._log(package, "uninstall", "uninstall_unconfirmed", error or output)
            return {"ok": False, "state": "uninstall_unconfirmed", "error": "Waydroid 未确认应用已卸载。"}
        desktop = self.home / ".local/share/applications" / ("ming-android-" + package.replace(".", "-") + ".desktop")
        target = self.home / ".local/share/ming-android/targets" / ("ming-android-target-" + package.replace(".", "-") + ".desktop")
        lab = self.home / ".local/share/ming-android/lab" / (package + ".json")
        try:
            if app_exists:
                self._remove_tree_secure(app_dir, "应用包目录")
            for path, label in (
                (desktop, "Android 桌面入口"),
                (target, "Android 启动目标"),
                (lab, "Android 实验配置"),
            ):
                self._unlink_secure(path, label)
        except (OSError, AndroidStorageError) as exc:
            self._log(package, "uninstall", "cleanup_failed", exc)
            return {"ok": False, "state": "cleanup_failed", "error": "应用已卸载，但本地入口清理失败。"}
        self._active_processes.pop(package, None)
        self._process_records.pop(package, None)
        self._log(package, "uninstall", "removed", "readback confirmed")
        return {"ok": True, "state": "removed", "package_id": package}

    def _session_running(self, package):
        """Read back the Waydroid session instead of trusting cage's rc."""
        try:
            rc, output, _error = self.runner(
                ("waydroid", "status"), capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if rc != 0:
            return False
        for line in (output or "").splitlines():
            if re.search(r"^\s*Session:\s*RUNNING\s*$", line, re.IGNORECASE):
                return True
            if line.strip().upper() == "RUNNING":
                return True
        return False

    def _process_running(self, package):
        """Check for a cage/Waydroid process containing this validated ID."""
        return self._process_readback(package) is True

    def _process_readback(self, package):
        """Return True/False, or None when the process probe is unavailable."""
        pattern = "cage.*waydroid.*" + re.escape(str(package))
        try:
            rc, output, _error = self.runner(
                ("pgrep", "-af", pattern), capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if rc not in (0, 1):
            return None
        if rc != 0:
            return False
        expected = self._expected_argv(package)
        return any(self._argv_owned(line, expected) for line in (output or "").splitlines())

    def _window_running(self, package):
        """Use xdotool when available as a window-level launch readback."""
        return self._window_readback(package) is True

    def _window_readback(self, package):
        """Return True/False, or None when xdotool cannot be queried."""
        if self.executable("xdotool") is None:
            return None
        try:
            rc, output, _error = self.runner(
                ("xdotool", "search", "--name", str(package)),
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if rc not in (0, 1):
            return None
        return rc == 0 and bool((output or "").strip())

    def _launch_confirmed(self, package, process=None, process_probe=None,
                          session_probe=None, window_probe=None):
        """Wait for a real session plus process/window evidence.

        An injected Cage process is itself the session owner in tests and in
        the launcher wrapper, so its liveness is sufficient there.  The
        synchronous runner path has no process handle and must obtain both
        session and process/window readback before reporting ``running``.
        """
        if process is not None:
            try:
                if process.poll() is not None:
                    return False
            except (OSError, AttributeError):
                return False
            if session_probe is not None:
                if not session_probe():
                    return False
            elif self.production and not self._session_running(package):
                return False
            if process_probe is not None and not process_probe():
                return False
            return True
        session_probe = session_probe or (lambda: self._session_running(package))
        process_probe = process_probe or (lambda: self._process_running(package))
        window_probe = window_probe or (lambda: self._window_running(package))
        for _ in range(10):
            try:
                session_ready = bool(session_probe())
                process_ready = bool(process_probe())
                window_ready = bool(window_probe())
            except (OSError, subprocess.SubprocessError, ValueError):
                session_ready = process_ready = window_ready = False
            if session_ready and (process_ready or window_ready):
                return True
            self.sleeper(0.1)
        return False

    @staticmethod
    def _close_fd(fd):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _release_lock(self, lock_path, lock_fd=None):
        self._close_fd(lock_fd)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _write_lock_fd(fd, payload):
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("无法写入 Android 运行锁")
            offset += written
        if hasattr(os, "fsync"):
            os.fsync(fd)

    @staticmethod
    def _expected_argv(package):
        return ("cage", "--", "waydroid", "app", "launch", str(package))

    @staticmethod
    def _normalize_argv(argv):
        if isinstance(argv, str):
            try:
                argv = shlex.split(argv)
            except ValueError:
                return None
        if not isinstance(argv, (tuple, list)):
            return None
        values = [str(item) for item in argv]
        if values and values[0].isdigit():
            values = values[1:]
        if values:
            values[0] = pathlib.PurePosixPath(values[0]).name
        # Some Cage builds omit the separator from ``ps args=`` output while
        # preserving the exact child command.
        values = [item for item in values if item != "--"]
        return tuple(values)

    def _argv_owned(self, actual, expected):
        actual = self._normalize_argv(actual)
        expected = self._normalize_argv(expected)
        return actual is not None and expected is not None and actual == expected

    @staticmethod
    def _proc_start_identity(pid):
        try:
            fields = pathlib.Path("/proc", str(int(pid)), "stat").read_text(encoding="utf-8").split()
            return fields[21] if len(fields) > 21 else None
        except (OSError, ValueError, IndexError):
            return None

    def _process_info(self, pid):
        """Return process argv/start identity, or existence/unknown markers."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"exists": False, "unknown": False}
        if pid <= 0:
            return {"exists": False, "unknown": False}
        try:
            rc, output, _error = self.runner(
                ("ps", "-p", str(pid), "-o", "args="),
                capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            return {"unknown": True}
        except (OSError, subprocess.SubprocessError):
            return {"unknown": True}
        if rc == 1:
            return {"exists": False, "unknown": False}
        if rc != 0:
            return {"unknown": True}
        argv = self._normalize_argv(output or "")
        if not argv:
            return {"unknown": True}
        return {
            "exists": True,
            "argv": argv,
            "start_id": self._proc_start_identity(pid),
            "unknown": False,
        }

    def _recover_stale_lock(self, lock_path, package):
        """Return True when an existing lock was proven stale and removed."""
        try:
            payload = self._read_lock(lock_path, package)
        except AndroidStorageError:
            raise
        pid = payload.get("pid")
        if pid is None:
            try:
                age = max(0, time.time() - lock_path.stat().st_mtime)
            except OSError:
                age = 0
            if age < LOCK_STALE_SECONDS:
                return False
            self._release_lock(lock_path)
            return True
        info = self._process_info(pid)
        if info.get("unknown"):
            return False
        if not info.get("exists"):
            self._release_lock(lock_path)
            return True
        expected = payload.get("argv") or self._expected_argv(package)
        if not self._argv_owned(info.get("argv"), expected):
            # A live unrelated process means a stale lock cannot be safely
            # reused; leave the lock in place and fail closed.
            return False
        expected_start = payload.get("start_id")
        actual_start = info.get("start_id")
        if expected_start and actual_start and str(expected_start) != str(actual_start):
            self._release_lock(lock_path)
            return True
        return False

    def launch(self, package, process_probe=None, session_probe=None, window_probe=None):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        package = str(package)
        gate = self._runtime_gate("launch")
        if gate is not None:
            return gate
        try:
            self._app_storage(package, create=False)
        except AndroidStorageError as exc:
            return {"ok": False, "state": "storage_insecure", "error": str(exc)}
        if not self._spawner_injected:
            listed = self._list_readback(package)
            if listed is not True:
                state = "launch_timeout" if self._last_list_error == "timeout" else (
                    "not_installed" if listed is False else "launch_unconfirmed"
                )
                return {"ok": False, "state": state, "error": "Android 应用尚未确认安装。"}
        lock_parent = self.state_root / package
        try:
            self._private_dir(self.state_root, "状态目录", create=True)
            self._private_dir(lock_parent, "应用状态目录", create=True)
        except AndroidStorageError as exc:
            return {"ok": False, "state": "storage_insecure", "error": str(exc)}
        lock_path = lock_parent / "cage.lock"
        lock_fd = None
        process = None
        token = str(uuid.uuid4())
        payload = {"package": package, "pid": None, "token": token}
        try:
            self._private_file(lock_path, "运行锁")
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                lock_fd = os.open(str(lock_path), flags, 0o600)
            except FileExistsError:
                if not self._recover_stale_lock(lock_path, package):
                    raise
                lock_fd = os.open(str(lock_path), flags, 0o600)
            self._write_lock_fd(lock_fd, payload)
        except AndroidStorageError as exc:
            self._close_fd(lock_fd)
            return {"ok": False, "state": "storage_insecure", "error": str(exc)}
        except FileExistsError:
            self._close_fd(lock_fd)
            return {"ok": False, "state": "already_running", "error": "该 Android 应用已经在运行。"}
        except OSError as exc:
            self._close_fd(lock_fd)
            return {"ok": False, "state": "launch_failed", "error": str(exc)}
        try:
            command = self._expected_argv(package)
            if self.production or self._spawner_injected:
                process = self.spawner(
                    command, shell=False
                )
                payload["pid"] = getattr(process, "pid", None)
                payload["argv"] = list(command)
                payload["start_id"] = self._proc_start_identity(payload["pid"]) if payload["pid"] else None
                self._process_records[package] = {
                    "argv": command, "start_id": payload.get("start_id"), "process": process,
                }
                os.lseek(lock_fd, 0, os.SEEK_SET)
                os.ftruncate(lock_fd, 0)
                self._write_lock_fd(lock_fd, payload)
                for _ in range(8):
                    if process.poll() is not None:
                        self._release_lock(lock_path, lock_fd)
                        self._process_records.pop(package, None)
                        return {"ok": False, "state": "launch_failed", "error": "Android 应用启动后立即退出。"}
                    self.sleeper(0.1)
            elif not self.production:
                rc, output, error = self.runner(command, capture_output=True, text=True, timeout=60)
                if rc != 0:
                    self._release_lock(lock_path, lock_fd)
                    self._log(package, "launch", "failed", error or output)
                    return {"ok": False, "state": "launch_failed", "error": "Waydroid 启动失败。"}
            else:
                raise RuntimeError("生产 Android 启动器未配置")
        except subprocess.TimeoutExpired as exc:
            self._release_lock(lock_path, lock_fd)
            self._process_records.pop(package, None)
            return {"ok": False, "state": "launch_timeout", "error": str(exc)}
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._release_lock(lock_path, lock_fd)
            self._process_records.pop(package, None)
            return {"ok": False, "state": "launch_failed", "error": str(exc)}
        finally:
            self._close_fd(lock_fd)
            lock_fd = None
        if not self._launch_confirmed(
            package, process=process, process_probe=process_probe,
            session_probe=session_probe, window_probe=window_probe,
        ):
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError, AttributeError):
                    pass
            self._release_lock(lock_path)
            self._process_records.pop(package, None)
            self._log(package, "launch", "launch_unconfirmed", "session/process/window readback missing")
            return {"ok": False, "state": "launch_unconfirmed", "error": "Android 应用未确认启动；请重试。"}
        if process is not None:
            self._active_processes[package] = process
        self._log(package, "launch", "running", "session/process readback confirmed")
        return {"ok": True, "state": "running", "package_id": package}

    def _read_lock(self, lock_path, package):
        try:
            self._private_file(lock_path, "运行锁", required=True)
            descriptor = os.open(
                str(lock_path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or not self._owner_is_current(info):
                    raise AndroidStorageError("Android 运行锁必须由当前用户拥有的普通文件。")
                if info.st_size > 64 * 1024:
                    raise AndroidStorageError("Android 运行锁内容过大。")
                raw = b""
                while True:
                    chunk = os.read(descriptor, 4096)
                    if not chunk:
                        break
                    raw += chunk
            finally:
                os.close(descriptor)
            raw = raw.decode("utf-8")
        except (AndroidStorageError, OSError) as exc:
            raise AndroidStorageError("无法读取 Android 运行锁：%s" % exc) from exc
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = {"package": package, "pid": None, "token": raw[:128]}
        if not isinstance(payload, dict) or payload.get("package") != package:
            raise AndroidStorageError("Android 运行锁与应用不匹配。")
        if "argv" in payload:
            argv = payload.get("argv")
            if self._normalize_argv(argv) != self._normalize_argv(self._expected_argv(package)):
                raise AndroidStorageError("Android 运行锁命令与应用不匹配。")
        if "start_id" in payload and payload.get("start_id") is not None:
            if not isinstance(payload.get("start_id"), (str, int)):
                raise AndroidStorageError("Android 运行锁启动标识无效。")
        return payload

    def _pid_owned_by_android(self, pid, package, process=None, expected_start=None):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if process is not None:
            if getattr(process, "pid", None) != pid:
                return False
            args = getattr(process, "args", None)
            if args:
                owned = self._argv_owned(args, self._expected_argv(package))
            else:
                record = self._process_records.get(package) or {}
                owned = self._argv_owned(record.get("argv"), self._expected_argv(package))
            if not owned:
                return False
            if expected_start is not None:
                record = self._process_records.get(package) or {}
                actual_start = record.get("start_id") or self._proc_start_identity(pid)
                if actual_start is None or str(actual_start) != str(expected_start):
                    return False
            return True
        info = self._process_info(pid)
        if info.get("unknown") or not info.get("exists"):
            return False
        if not self._argv_owned(info.get("argv"), self._expected_argv(package)):
            return False
        if expected_start is not None:
            actual_start = info.get("start_id")
            if actual_start is None or str(actual_start) != str(expected_start):
                return False
        return True

    def stop(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        package = str(package)
        lock_path = self.state_root / package / "cage.lock"
        process = self._active_processes.get(package)
        payload = {}
        try:
            payload = self._read_lock(lock_path, package)
        except AndroidStorageError as exc:
            return {"ok": False, "state": "stop_unconfirmed", "error": str(exc)}
        pid = payload.get("pid") if isinstance(payload, dict) else None
        if pid is None:
            self._log(package, "launch", "stop_unconfirmed", "运行锁没有可验证的 Cage PID")
            return {
                "ok": False,
                "state": "stop_unconfirmed",
                "error": "无法安全确认 Android 应用进程；未执行停止操作。",
            }
        if self.production and (
            not payload.get("argv") or payload.get("start_id") is None
        ):
            self._log(package, "launch", "stop_unconfirmed", "生产运行锁缺少精确命令或启动标识")
            return {
                "ok": False,
                "state": "stop_unconfirmed",
                "error": "生产运行锁缺少可验证的进程身份；未执行停止操作。",
            }
        if pid is not None and not self._pid_owned_by_android(
            pid, package, process=process, expected_start=payload.get("start_id")
        ):
            self._log(package, "launch", "stop_unconfirmed", "运行锁 PID 不属于当前 Cage/Waydroid 应用")
            return {"ok": False, "state": "stop_unconfirmed", "error": "运行锁 PID 与 Android 应用不匹配。"}
        terminated = True
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError, AttributeError):
                terminated = False
        elif pid is not None:
            terminated = bool(self.terminator(pid))
        else:
            # There is no safe PID to signal.  In particular, Waydroid has no
            # portable ``app stop`` command; never invoke a guessed command
            # that could affect another application/container.
            terminated = False
        stopped = terminated
        if process is not None:
            try:
                stopped = stopped and process.poll() is not None
            except (OSError, AttributeError):
                stopped = False
        # A local Popen handle is only one observation.  Always read back the
        # external process/window state too, otherwise a detached Cage child
        # could survive while the UI reports a successful stop.
        if process is not None and not self.production and not self._runner_injected:
            # Unit/test callers that inject only a Popen-like handle do not
            # have a portable pgrep implementation.  Production and callers
            # with an injected runner still require the external readback.
            process_readback = False if stopped else None
        else:
            process_readback = self._process_readback(package)
        window_readback = self._window_readback(package)
        stopped = stopped and process_readback is False
        if window_readback is True:
            stopped = False
        elif window_readback is None and self.executable("xdotool") is not None:
            stopped = False
        if process_readback is None:
            stopped = False
        if not stopped:
            self._log(package, "launch", "stop_unconfirmed", "Cage/Waydroid process still present")
            return {"ok": False, "state": "stop_unconfirmed", "error": "Android 应用停止状态未确认。"}
        self._active_processes.pop(package, None)
        self._release_lock(lock_path)
        self._log(package, "launch", "stopped", "Cage/Waydroid stop readback confirmed")
        return {"ok": True, "state": "stopped", "package_id": package}


AndroidAppManager = AndroidRuntime


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-android-runtime")
    parser.add_argument("action", choices=("status", "install", "uninstall", "launch", "stop"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)
    runtime = AndroidRuntime()
    if args.action == "status":
        result = runtime.status()
    elif args.action == "install":
        result = runtime.install_apk(args.value or "")
    elif args.action == "uninstall":
        result = runtime.uninstall(args.value or "")
    elif args.action == "launch":
        result = runtime.launch(args.value or "")
    else:
        result = runtime.stop(args.value or "")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
