#!/usr/bin/env python3
"""Per-user Wine application installer with isolated prefixes and JSON output."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import time


MAX_WINDOWS_PACKAGE_BYTES = 8 * 1024 * 1024 * 1024
MAX_COMMAND_OUTPUT = 64 * 1024
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _run(command, timeout=60, env=None):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run(
            list(command), stdout=stdout, stderr=stderr, text=False, timeout=timeout,
            env=env, check=False, shell=False,
        )
        def tail(handle):
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - MAX_COMMAND_OUTPUT))
            return handle.read().decode("utf-8", errors="replace")
        return completed.returncode, tail(stdout), tail(stderr)


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _desktop_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


class WineInstaller:
    def __init__(self, home=None, runner=None, log_path=None, uid_getter=None,
                 spawner=None, sleeper=None, executable=None, execer=None):
        self.home = pathlib.Path(home or pathlib.Path.home())
        self.runner = runner or _run
        self.root = self.home / ".local" / "share" / "ming-wine" / "apps"
        self.desktop_dir = self.home / ".local" / "share" / "applications"
        self.target_desktop_dir = self.home / ".local" / "share" / "ming-wine" / "targets"
        self.log_path = pathlib.Path(
            log_path or self.home / ".local" / "state" / "ming-os" / "wine" / "install.jsonl"
        )
        self.uid_getter = uid_getter or getattr(os, "geteuid", lambda: 1)
        self.spawner = spawner or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.executable = executable or (
            shutil.which if runner is None else lambda name: name
        )
        self.execer = execer or os.execvpe

    @staticmethod
    def app_id(application):
        raw_text = str(application)
        if (
            APP_ID_PATTERN.fullmatch(raw_text)
            and "/" not in raw_text and "\\" not in raw_text
            and pathlib.Path(raw_text).suffix.lower() not in {".exe", ".msi"}
        ):
            return raw_text
        raw_path = pathlib.Path(raw_text)
        raw = raw_path.stem
        value = UNSAFE_NAME.sub("-", raw).strip("._-")[:64] or "application"
        if not APP_ID_PATTERN.fullmatch(value):
            raise ValueError("应用名称无法生成安全的应用 ID。")
        identity = str(raw_path.resolve()) if raw_path.is_absolute() else str(raw_path)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return "%s-%s" % (value[:47], digest)

    def app_dir_for(self, application):
        return self.root / self.app_id(application)

    def prefix_for(self, application):
        return self.app_dir_for(application) / "prefix"

    def metadata_for(self, application):
        return self.app_dir_for(application) / "metadata.json"

    def desktop_for(self, application):
        return self.desktop_dir / ("ming-wine-%s.desktop" % self.app_id(application))

    def _redact(self, value):
        text = str(value or "")[:1024]
        text = text.replace(str(self.home), "$HOME")
        text = re.sub(r"(?i)(password|passwd|token|secret|api[_-]?key)=\S+", r"\1=[REDACTED]", text)
        text = re.sub(r"https?://\S+", "[REDACTED_URL]", text)
        return text

    def _log(self, action, state, application="", detail=""):
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": str(action),
            "state": str(state),
            "app_id": self.app_id(application) if application else "",
            "detail": self._redact(detail),
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.log_path.parent.chmod(0o700)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            self.log_path.chmod(0o600)
            if application:
                channel = "launch" if action == "launch" else "install"
                channel_path = self._channel_log_path(application, channel)
                channel_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                channel_path.parent.chmod(0o700)
                with channel_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                channel_path.chmod(0o600)
        except OSError:
            pass

    def _channel_log_path(self, application, channel):
        return (
            self.home / ".local" / "state" / "ming-os" / "wine"
            / self.app_id(application) / ("%s.jsonl" % channel)
        )

    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()
        with pathlib.Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _stage_source(self, source):
        app_dir = self.app_dir_for(source)
        source_dir = app_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        source_dir.chmod(0o700)
        staged = source_dir / ("installer" + pathlib.Path(source).suffix.lower())
        temporary = source_dir / (".installer.%s.tmp" % os.getpid())
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        digest = hashlib.sha256()
        total = 0
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("安装源不再是普通文件。")
            with os.fdopen(descriptor, "rb", closefd=True) as source_handle:
                descriptor = -1
                with temporary.open("wb") as target_handle:
                    for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > MAX_WINDOWS_PACKAGE_BYTES:
                            raise OSError("安装文件超过允许大小。")
                        digest.update(chunk)
                        target_handle.write(chunk)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, staged)
            staged.chmod(0o600)
            return staged, digest.hexdigest()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def detect_runtime(self):
        version = ""
        for command in (("wine", "--version"), ("wineboot", "--help")):
            try:
                rc, output, error = self.runner(command, timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "state": "runtime_unavailable", "version": version, "error": self._redact(exc)}
            if rc != 0:
                return {
                    "ok": False, "state": "runtime_unavailable", "version": version,
                    "error": self._redact(error or "%s 不可用。" % command[0]),
                }
            if command[0] == "wine":
                version = (output or "").strip().splitlines()[0] if (output or "").strip() else ""
        return {"ok": True, "state": "runtime_ready", "version": version, "error": ""}

    @staticmethod
    def _validate_source(source):
        if not isinstance(source, (str, os.PathLike)) or "\x00" in os.fspath(source):
            return None, "安装文件路径无效。"
        path = pathlib.Path(source).expanduser()
        if path.is_symlink():
            return None, "不允许通过符号链接安装 Windows 应用。"
        try:
            resolved = path.resolve(strict=True)
            info = resolved.stat()
        except (OSError, RuntimeError):
            return None, "找不到 Windows 安装文件。"
        if not stat.S_ISREG(info.st_mode):
            return None, "只能安装普通本地文件。"
        if resolved.suffix.lower() not in {".exe", ".msi"}:
            return None, "只支持 .exe 或 .msi 文件。"
        if not 0 < info.st_size <= MAX_WINDOWS_PACKAGE_BYTES:
            return None, "安装文件大小无效。"
        return resolved, ""

    @staticmethod
    def detect_architecture(path):
        if pathlib.Path(path).suffix.lower() != ".exe":
            return "win64"
        try:
            with pathlib.Path(path).open("rb") as handle:
                data = handle.read(0x1000)
            if len(data) >= 0x40 and data[:2] == b"MZ":
                pe_offset = int.from_bytes(data[0x3C:0x40], "little")
                if pe_offset + 6 <= len(data) and data[pe_offset:pe_offset + 4] == b"PE\0\0":
                    machine = int.from_bytes(data[pe_offset + 4:pe_offset + 6], "little")
                    return "win32" if machine == 0x014C else "win64"
        except (OSError, ValueError):
            pass
        return "win64"

    def _write_metadata(self, application, payload):
        _atomic_json(self.metadata_for(application), payload)

    def _write_desktop(self, application, name):
        app_id = self.app_id(application)
        desktop = self.desktop_for(application)
        target_desktop = self.target_desktop_dir / ("ming-wine-target-%s.desktop" % app_id)
        desktop.parent.mkdir(parents=True, exist_ok=True)
        target_desktop.parent.mkdir(parents=True, exist_ok=True)
        target_desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=%s\n"
            "Exec=/usr/local/bin/ming-wine-installer run %s\n"
            "Icon=application-x-ms-dos-executable\n"
            "Terminal=false\n"
            "StartupWMClass=%s\n"
            "X-Ming-Managed=true\n"
            % (_desktop_escape(name), app_id, _desktop_escape(name)),
            encoding="utf-8",
        )
        target_desktop.chmod(0o644)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=%s\n"
            "Comment=通过 Ming Wine 兼容层启动\n"
            "Exec=/usr/local/bin/ming-launch --desktop-file %s --source desktop\n"
            "Icon=application-x-ms-dos-executable\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            "StartupNotify=true\n"
            "X-Ming-Wine-App=%s\n"
            "X-Ming-Managed=true\n"
            % (_desktop_escape(name), str(target_desktop), app_id),
            encoding="utf-8",
        )
        desktop.chmod(0o644)
        return desktop

    @staticmethod
    def _snapshot_executables(prefix):
        drive_c = pathlib.Path(prefix) / "drive_c"
        if not drive_c.is_dir():
            return set()
        return {
            str(path.resolve())
            for path in drive_c.rglob("*")
            if path.is_file() and path.suffix.lower() == ".exe"
        }

    @staticmethod
    def _discover_launch_target(prefix, before=None):
        before = set(before or ())
        drive_c = pathlib.Path(prefix) / "drive_c"
        if not drive_c.is_dir():
            return None
        rejected = ("unins", "uninstall", "setup", "installer", "update", "crash", "helper")
        candidates = []
        for path in drive_c.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".exe":
                continue
            resolved = str(path.resolve())
            if resolved in before:
                continue
            lower_name = path.name.casefold()
            if any(marker in lower_name for marker in rejected):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            candidates.append((-size, len(path.parts), lower_name, path))
        return sorted(candidates)[0][-1] if candidates else None

    def _refresh_desktop(self):
        try:
            rc, _output, _error = self.runner(("/usr/local/bin/ming-refresh-desktop-state",), timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return rc == 0

    def install(self, source):
        if self.uid_getter() == 0:
            return {"ok": False, "state": "permission_denied", "error": "Wine 应用必须由当前登录用户安装。"}
        path, error = self._validate_source(source)
        if error:
            self._log("install", "validation_failed", detail=error)
            return {"ok": False, "state": "validation_failed", "error": error}
        runtime = self.detect_runtime()
        if not runtime["ok"]:
            self._log("install", "runtime_unavailable", path, runtime["error"])
            return runtime

        app_id = self.app_id(path)
        try:
            staged_path, source_sha256 = self._stage_source(path)
        except OSError as exc:
            error = "无法把安装文件复制到受保护的临时目录：%s" % self._redact(exc)
            self._log("install", "staging_failed", path, error)
            return {"ok": False, "state": "staging_failed", "error": error}
        existing = self._load_metadata(path)
        if existing and existing.get("source_sha256") != source_sha256:
            error = "同一路径上的安装文件内容已变化；为保护现有 Wine 应用，请先卸载旧应用或使用新文件名。"
            self._log("install", "source_conflict", path, error)
            return {"ok": False, "state": "source_conflict", "error": error}
        architecture = self.detect_architecture(staged_path)
        if architecture == "win32" and not self.executable("wine32"):
            error = "检测到 32 位 Windows 应用，但 Wine 32 位组件未安装。请在 Ming 工具箱中启用 32 位兼容。"
            self._log("install", "runtime_32_unavailable", path, error)
            return {"ok": False, "state": "runtime_32_unavailable", "architecture": architecture, "error": error}
        prefix = self.prefix_for(path)
        prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
        kind = path.suffix.lower().lstrip(".")
        command = ("wine", "msiexec", "/i", str(staged_path)) if kind == "msi" else ("wine", str(staged_path))
        env = dict(os.environ, WINEPREFIX=str(prefix), WINEARCH=architecture)
        before = self._snapshot_executables(prefix)
        metadata = {
            "schema": "ming.wine.app.v1", "app_id": app_id, "name": path.stem,
            "state": "installing", "kind": kind, "source": str(path),
            "staged_source": str(staged_path), "source_sha256": source_sha256,
            "prefix": str(prefix), "launch_target": "", "architecture": architecture,
            "lab": {"dxvk": False, "proton": False, "game_mode": False},
        }
        self._write_metadata(path, metadata)
        try:
            rc, output, stderr = self.runner(command, timeout=900, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            metadata.update(state="install_failed", error=self._redact(exc))
            self._write_metadata(path, metadata)
            self._log("install", "install_failed", path, exc)
            return {"ok": False, "state": "install_failed", "kind": kind, "prefix": str(prefix), "error": self._redact(exc)}
        if rc != 0:
            metadata.update(state="install_failed", error=self._redact(stderr or "Wine 安装失败。"))
            self._write_metadata(path, metadata)
            self._log("install", "install_failed", path, metadata["error"])
            return {"ok": False, "state": "install_failed", "kind": kind, "prefix": str(prefix), "error": metadata["error"]}

        launch_target = self._discover_launch_target(prefix, before)
        if launch_target is None:
            metadata.update(
                state="installed_needs_launcher",
                error="安装已完成，但未找到可安全启动的主程序。请在 Ming 工具箱中选择启动程序。",
            )
            self._write_metadata(path, metadata)
            self._log("install", "installed_needs_launcher", path, metadata["error"])
            return {
                "ok": False, "state": "installed_needs_launcher", "kind": kind,
                "app_id": app_id, "prefix": str(prefix), "file": str(path),
                "architecture": architecture, "metadata": metadata,
                "error": metadata["error"],
            }

        desktop = self._write_desktop(path, path.stem)
        metadata["launch_target"] = str(launch_target)
        metadata.update(state="installed", desktop_file=str(desktop))
        metadata.pop("error", None)
        self._write_metadata(path, metadata)
        refreshed = self._refresh_desktop()
        state = "installed" if refreshed else "installed_with_refresh_warning"
        self._log("install", state, path)
        return {
            "ok": True, "state": state, "kind": kind, "app_id": app_id,
            "architecture": architecture, "metadata": metadata,
            "prefix": str(prefix), "file": str(path), "desktop_file": str(desktop),
            "output": (output or "").strip(), "refresh_ok": refreshed,
        }

    def _load_metadata(self, application):
        try:
            data = json.loads(self.metadata_for(application).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("app_id") == self.app_id(application) else None

    def _validated_launch_target(self, application, metadata):
        prefix = self.prefix_for(application).resolve()
        target = pathlib.Path(str(metadata.get("launch_target") or ""))
        try:
            resolved = target.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved.suffix.lower() != ".exe" or resolved.is_symlink():
            return None
        if prefix != resolved and prefix not in resolved.parents:
            return None
        return resolved

    def _lab_state(self, application):
        path = (
            self.home / ".local" / "share" / "ming-wine" / "lab"
            / ("%s.json" % self.app_id(application))
        )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {
            key: bool(data.get(key, False))
            for key in ("dxvk", "proton", "game_mode", "verbose_logs")
        }

    def launch(self, application):
        metadata = self._load_metadata(application)
        if not metadata or metadata.get("state") not in {"installed", "installed_with_refresh_warning"}:
            return {"ok": False, "state": "not_installed", "error": "找不到可启动的 Wine 应用。"}
        target = self._validated_launch_target(application, metadata)
        prefix = self.prefix_for(application)
        if target is None or not target.is_file() or not prefix.is_dir():
            self._log("launch", "launch_target_invalid", application, "target outside prefix or missing")
            return {"ok": False, "state": "launch_target_invalid", "error": "应用启动目标不在受信任的 Wine 前缀内。"}
        env = dict(os.environ, WINEPREFIX=str(prefix), WINEARCH=str(metadata.get("architecture") or "win64"))
        launch_output = self._channel_log_path(application, "launch")
        launch_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        launch_output.parent.chmod(0o700)
        launch_output.touch(exist_ok=True)
        launch_output.chmod(0o600)
        try:
            process = self.spawner(
                ("wine", str(target)), env=env, shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._log("launch", "launch_failed", application, exc)
            return {"ok": False, "state": "launch_failed", "error": self._redact(exc)}
        for _attempt in range(8):
            return_code = process.poll()
            if return_code is not None:
                error = "Wine 应用启动后立即退出（状态码 %s）。" % return_code
                self._log("launch", "launch_failed", application, error)
                return {"ok": False, "state": "launch_failed", "error": error}
            self.sleeper(0.15)
        if process.poll() is not None:
            error = "Wine 应用未能稳定运行。"
            self._log("launch", "launch_failed", application, error)
            return {"ok": False, "state": "launch_failed", "error": error}
        self._log("launch", "started", application)
        return {"ok": True, "state": "started", "pid": getattr(process, "pid", 0)}

    def run_foreground(self, application):
        metadata = self._load_metadata(application)
        if not metadata or metadata.get("state") not in {"installed", "installed_with_refresh_warning"}:
            return {"ok": False, "state": "not_installed", "error": "找不到可启动的 Wine 应用。"}
        target = self._validated_launch_target(application, metadata)
        if target is None:
            return {"ok": False, "state": "launch_target_invalid", "error": "应用启动目标不在受信任的 Wine 前缀内。"}
        prefix = self.prefix_for(application)
        env = dict(
            os.environ,
            WINEPREFIX=str(prefix),
            WINEARCH=str(metadata.get("architecture") or "win64"),
        )
        lab = self._lab_state(application)
        command = ["wine", str(target)]
        if lab["dxvk"]:
            env["WINEDLLOVERRIDES"] = "dxgi,d3d11=n,b"
        if lab["proton"]:
            proton = self.executable("proton")
            if not proton:
                return {"ok": False, "state": "lab_dependency_missing", "error": "已为此应用启用 Proton，但 Proton 尚未安装。"}
            env["STEAM_COMPAT_DATA_PATH"] = str(prefix)
            command = [proton, "run", str(target)]
        if lab["game_mode"]:
            gamemode = self.executable("gamemoderun")
            if not gamemode:
                return {"ok": False, "state": "lab_dependency_missing", "error": "已为此应用启用游戏模式，但 GameMode 尚未安装。"}
            command.insert(0, gamemode)
        if lab["verbose_logs"]:
            env["WINEDEBUG"] = "+timestamp,+seh,+module"
        self._log("launch", "exec", application)
        try:
            self.execer(command[0], tuple(command), env)
        except OSError as exc:
            self._log("launch", "launch_failed", application, exc)
            return {"ok": False, "state": "launch_failed", "error": self._redact(exc)}
        return {"ok": True, "state": "exec_requested", "argv": command}

    def uninstall(self, application):
        app_dir = self.app_dir_for(application)
        prefix = self.prefix_for(application)
        desktop = self.desktop_for(application)
        metadata = self._load_metadata(application) or {}
        launch_target = pathlib.Path(str(metadata.get("launch_target") or ""))
        if not app_dir.is_dir() or not prefix.is_dir():
            return {"ok": False, "state": "not_installed", "prefix": str(prefix), "error": "找不到该应用的 Wine 前缀。"}
        try:
            rc, output, stderr = self.runner(("wine", "uninstaller"), timeout=120, env=dict(os.environ, WINEPREFIX=str(prefix)))
        except (OSError, subprocess.SubprocessError) as exc:
            self._log("uninstall", "uninstall_failed", application, exc)
            return {"ok": False, "state": "uninstall_failed", "error": self._redact(exc)}
        if rc != 0:
            error = self._redact(stderr or "Wine 卸载失败。")
            self._log("uninstall", "uninstall_failed", application, error)
            return {"ok": False, "state": "uninstall_failed", "error": error}
        if launch_target.is_file():
            error = "卸载程序已关闭，但应用文件仍存在；为避免误删数据，Ming 工具箱没有清理该兼容环境。"
            self._log("uninstall", "uninstall_cancelled_or_incomplete", application, error)
            return {"ok": False, "state": "uninstall_cancelled_or_incomplete", "error": error}
        try:
            resolved_root = self.root.resolve()
            resolved_app = app_dir.resolve()
            if resolved_app.parent != resolved_root:
                raise OSError("拒绝清理 Wine 应用目录以外的路径。")
            shutil.rmtree(resolved_app)
            try:
                desktop.unlink()
            except FileNotFoundError:
                pass
            try:
                (self.target_desktop_dir / ("ming-wine-target-%s.desktop" % self.app_id(application))).unlink()
            except FileNotFoundError:
                pass
        except OSError as exc:
            self._log("uninstall", "cleanup_failed", application, exc)
            return {"ok": False, "state": "cleanup_failed", "error": self._redact(exc)}
        self._refresh_desktop()
        self._log("uninstall", "uninstalled", application)
        return {"ok": True, "state": "uninstalled", "prefix": str(prefix), "output": (output or "").strip()}

    def list_apps(self):
        items = []
        for metadata_path in sorted(self.root.glob("*/metadata.json")) if self.root.is_dir() else ():
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("app_id"):
                items.append(data)
        return items


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-wine-installer")
    parser.add_argument("action", choices=("runtime", "install", "launch", "run", "uninstall", "list"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)
    installer = WineInstaller()
    if args.action == "runtime":
        result = installer.detect_runtime()
    elif args.action == "list":
        result = {"ok": True, "state": "listed", "apps": installer.list_apps()}
    elif not args.value:
        result = {"ok": False, "state": "invalid_request", "error": "缺少应用或安装文件。"}
    elif args.action == "install":
        result = installer.install(args.value)
    elif args.action == "launch":
        result = installer.launch(args.value)
    elif args.action == "run":
        result = installer.run_foreground(args.value)
    else:
        result = installer.uninstall(args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
