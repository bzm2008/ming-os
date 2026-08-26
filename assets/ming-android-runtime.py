#!/usr/bin/env python3
"""Safe, on-demand Waydroid/Cage integration for Ming Toolbox.

The module deliberately keeps Android separate from the Wine runtime.  It does
not start a container at import time and accepts only structured local APK
paths.  The CLI emits JSON so the GTK toolbox can show the real state.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import time
import uuid


MAX_APK_BYTES = 512 * 1024 * 1024
PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
APP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STATES = (
    "unavailable", "checking", "ready", "preparing", "installing",
    "installed", "launching", "running", "stopped", "failed",
    "uninstalling", "removed",
)
AUTHORIZED_ACTIONS = frozenset(("install-deps", "start-container", "stop-container", "repair"))


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
    def __init__(self, root):
        self.root = pathlib.Path(root)

    def _path(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            raise ValueError("Android 包名无效。")
        return self.root / (str(package) + ".json")

    def get(self, package):
        state = default_android_lab_state()
        try:
            data = json.loads(self._path(package).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for key in state:
                if key != "scope":
                    state[key] = bool(data.get(key, False))
        return state

    def set(self, package, key, enabled):
        if key not in default_android_lab_state() or key == "scope":
            raise ValueError("实验室选项不在白名单内。")
        state = self.get(package)
        state[key] = bool(enabled)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        temporary = self._path(package).with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._path(package))
        try:
            self._path(package).chmod(0o600)
        except OSError:
            pass
        return state


class ApkValidationError(ValueError):
    """Raised by the pure validation API for an untrusted APK input."""


def _sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
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
        if source.is_symlink() or not source.is_file() or source.suffix.casefold() != ".apk":
            return {"ok": False, "state": "invalid_source", "error": "APK 必须是普通本地文件。"}
        size = source.stat().st_size
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
    if abis <= {"arm64-v8a", "armeabi-v7a", "armeabi", "arm"}:
        return {"ok": False, "state": "unsupported_architecture", "error": "这是 ARM APK；请在 Ming 实验室启用 ARM 转译后再试。"}
    architecture = "universal" if "universal" in abis or not abis else "x86_64"
    if not (abis & {"x86_64", "x86", "universal"}):
        return {"ok": False, "state": "unsupported_architecture", "error": "APK 不包含 x86/x86_64 架构。"}
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
                 environment=None, runtime=None):
        self.home = pathlib.Path(home or pathlib.Path.home())
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
        self._spawner_injected = spawner is not None
        self.apps_root = self.home / ".local/share/ming-android/apps"
        self.state_root = self.home / ".local/state/ming-os/android"

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
        return {
            "ram_mb": ram_mb,
            "render_nodes": [str(path) for path in pathlib.Path("/dev/dri").glob("renderD*")],
            "binderfs": pathlib.Path("/dev/binderfs").exists() or pathlib.Path("/dev/binder").exists(),
            "lxc": bool(self.executable("lxc-start")),
            "dbus": bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS") or pathlib.Path("/run/dbus/system_bus_socket").exists()),
            "wayland": bool(os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_RUNTIME_DIR")),
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
            container = str(self.probes.get("container", "unknown"))
        elif self.executable("waydroid"):
            try:
                rc, output, _error = self.runner(("waydroid", "status"), capture_output=True, text=True, timeout=20)
                container = "running" if rc == 0 and "RUNNING" in (output or "").upper() else "not_initialized"
            except (OSError, subprocess.SubprocessError):
                container = "unknown"
        return {
            "ok": not reasons,
            "state": "ready" if not reasons else "unavailable",
            "runtime": "waydroid+cage",
            "reasons": reasons,
            "memory_mb": int(probe.get("ram_mb") or 0),
            "gpu": bool(probe.get("render_nodes")),
            "container": container,
        }

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

    def validate_apk(self, path):
        return _validate_apk_result(path, metadata_reader=self.metadata_reader, home=self.home)

    # Compatibility alias used by the toolbox controller and older callers.
    def install(self, path):
        return self.install_apk(path)

    def _log(self, package, action, state, detail=""):
        path = self.state_root / package / ("install.jsonl" if action in {"install", "uninstall"} else "launch.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.state_root.chmod(0o700)
            path.parent.chmod(0o700)
        except OSError:
            pass
        record = {"action": action, "state": state, "detail": str(detail)[:500]}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _list_contains(self, package):
        rc, output, _error = self.runner(("waydroid", "app", "list"), capture_output=True, text=True, timeout=30)
        if rc != 0:
            return False
        return any((line.strip().split()[0] if line.strip() else "") == package for line in (output or "").splitlines())

    def _desktop_file(self, package, label=None):
        directory = self.home / ".local/share/applications"
        target_directory = self.home / ".local/share/ming-android/targets"
        directory.mkdir(parents=True, exist_ok=True)
        target_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        path = directory / ("ming-android-" + package.replace(".", "-") + ".desktop")
        target = target_directory / ("ming-android-target-" + package.replace(".", "-") + ".desktop")
        target.write_text(
            "[Desktop Entry]\nType=Application\nName=%s\n"
            "Exec=/usr/local/bin/ming-android-runtime launch %s\n"
            "Terminal=false\n" % (label or package, package),
            encoding="utf-8",
        )
        target.chmod(0o600)
        path.write_text(
            "[Desktop Entry]\nType=Application\n"
            "Name=%s\nExec=/usr/local/bin/ming-launch --desktop-file %s --source toolbox\n"
            "Icon=application-x-apk\nTerminal=false\nCategories=System;\n"
            "X-Ming-Android-Package=%s\n" % (label or package, target, package),
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def install_apk(self, path):
        if self.probes is not None and not self.status().get("ok"):
            status = self.status()
            return {"ok": False, "state": "unavailable", "error": "Android 当前不可用：" + "、".join(status.get("reasons", []))}
        result = self.validate_apk(path)
        if not result["ok"]:
            return result
        package = result["package"]
        app_dir = self.apps_root / package
        metadata_path = app_dir / "metadata.json"
        if metadata_path.exists():
            try:
                current = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = {}
            if current.get("sha256") != result["sha256"]:
                return {"ok": False, "state": "package_conflict", "error": "同一包名已有不同 APK，请先卸载后再安装。"}
        app_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            app_dir.chmod(0o700)
        except OSError:
            pass
        artifact = app_dir / "artifact.apk"
        temporary = app_dir / "artifact.apk.tmp"
        try:
            shutil.copyfile(result["path"], temporary)
            temporary.chmod(0o600)
            os.replace(temporary, artifact)
            rc, output, error = self.runner(("waydroid", "app", "install", str(artifact)), capture_output=True, text=True, timeout=900)
            if rc != 0 or not self._list_contains(package):
                artifact.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                if app_dir.exists() and not metadata_path.exists():
                    shutil.rmtree(app_dir, ignore_errors=True)
                self._log(package, "install", "install_unconfirmed", error or output)
                return {"ok": False, "state": "install_unconfirmed", "error": "Waydroid 未确认 APK 已安装。"}
            desktop = self._desktop_file(package, package)
            metadata = dict(result, state="installed", package_id=package, artifact=str(artifact), desktop_file=str(desktop))
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            metadata_path.chmod(0o600)
            self._log(package, "install", "installed", "readback confirmed")
            return {"ok": True, "state": "installed", "package_id": package, "metadata_file": str(metadata_path), "desktop_file": str(desktop), "sha256": result["sha256"]}
        except (OSError, subprocess.SubprocessError) as exc:
            temporary.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            if app_dir.exists() and not metadata_path.exists():
                shutil.rmtree(app_dir, ignore_errors=True)
            self._log(package, "install", "failed", exc)
            return {"ok": False, "state": "failed", "error": str(exc)}

    def uninstall(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        package = str(package)
        app_dir = self.apps_root / package
        rc, output, error = self.runner(("waydroid", "app", "remove", package), capture_output=True, text=True, timeout=120)
        if rc != 0 or self._list_contains(package):
            self._log(package, "uninstall", "failed", error or output)
            return {"ok": False, "state": "uninstall_unconfirmed", "error": "Waydroid 未确认应用已卸载。"}
        desktop = self.home / ".local/share/applications" / ("ming-android-" + package.replace(".", "-") + ".desktop")
        target = self.home / ".local/share/ming-android/targets" / ("ming-android-target-" + package.replace(".", "-") + ".desktop")
        try:
            if app_dir.exists():
                shutil.rmtree(app_dir)
            desktop.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
        except OSError as exc:
            self._log(package, "uninstall", "cleanup_failed", exc)
            return {"ok": False, "state": "cleanup_failed", "error": "应用已卸载，但本地入口清理失败。"}
        self._log(package, "uninstall", "removed", "readback confirmed")
        return {"ok": True, "state": "removed", "package_id": package}

    def launch(self, package, process_probe=None):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        if not self._spawner_injected and not self._list_contains(str(package)):
            return {"ok": False, "state": "not_installed", "error": "Android 应用尚未安装。"}
        lock_path = self.state_root / str(package) / "cage.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = None
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(uuid.uuid4()).encode("ascii"))
        except FileExistsError:
            return {"ok": False, "state": "already_running", "error": "该 Android 应用已经在运行。"}
        except OSError as exc:
            return {"ok": False, "state": "launch_failed", "error": str(exc)}
        if self._spawner_injected:
            try:
                process = self.spawner(("cage", "--", "waydroid", "app", "launch", str(package)), shell=False)
                for _ in range(8):
                    if process.poll() is not None:
                        os.close(lock_fd)
                        lock_path.unlink(missing_ok=True)
                        return {"ok": False, "state": "launch_failed", "error": "Android 应用启动后立即退出。"}
                    self.sleeper(0.1)
            except (OSError, subprocess.SubprocessError) as exc:
                if lock_fd is not None:
                    os.close(lock_fd)
                lock_path.unlink(missing_ok=True)
                return {"ok": False, "state": "launch_failed", "error": str(exc)}
        else:
            command = ("cage", "--", "waydroid", "app", "launch", str(package))
            rc, output, error = self.runner(command, capture_output=True, text=True, timeout=60)
            if rc != 0:
                if lock_fd is not None:
                    os.close(lock_fd)
                lock_path.unlink(missing_ok=True)
                self._log(str(package), "launch", "failed", error or output)
                return {"ok": False, "state": "launch_failed", "error": "Waydroid 启动失败。"}
        if lock_fd is not None:
            os.close(lock_fd)
        if process_probe is not None and not process_probe():
            lock_path.unlink(missing_ok=True)
            return {"ok": False, "state": "launch_failed", "error": "Android 应用未稳定运行。"}
        self._log(str(package), "launch", "running", "launch requested")
        return {"ok": True, "state": "running", "package_id": str(package)}

    def stop(self, package):
        if not PACKAGE_RE.fullmatch(str(package)):
            return {"ok": False, "state": "invalid_package", "error": "Android 包名无效。"}
        lock_path = self.state_root / str(package) / "cage.lock"
        lock_path.unlink(missing_ok=True)
        self._log(str(package), "launch", "stopped", "session stopped")
        return {"ok": True, "state": "stopped", "package_id": str(package)}


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
