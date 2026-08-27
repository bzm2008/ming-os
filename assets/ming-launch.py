#!/usr/bin/env python3
"""Single-instance application launch broker with bounded visual feedback."""

import argparse
import configparser
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import stat
import socket
import subprocess
import sys
import threading
import time
import tempfile


ANIMATION_DURATION_MS = 160
FEEDBACK_TIMEOUT_MS = 4000
DEDUP_SECONDS = 0.6
IPC_VERSION = 1
SYSTEM_APPLICATION_DIR = pathlib.Path("/usr/share/applications")
TRUSTED_DESKTOP_MARKER_DIR = pathlib.Path("/var/lib/ming-os/trusted-desktops")
OPT_APPS_ROOT = pathlib.Path("/opt/apps")
DESKTOP_PROXY_DIR = pathlib.Path("/usr/local/share/applications")
DESKTOP_PROXY_MANIFEST = pathlib.Path("/var/lib/ming-os/desktop-proxies/manifest-v1.json")
DESKTOP_PROXY_GENERATION = "ming-opt-desktop-proxies-v1"

SANDBOX_RETRY_SIGNATURES = (
    "no usable sandbox",
    "suid sandbox",
    "setuid sandbox",
    "zygote_host_impl_linux",
    "sandbox_linux",
    "failed to move to new namespace",
    "without --no-sandbox",
)
SANDBOX_RETRY_ALLOWLIST = {
    "xiahai": {
        "desktop_ids": {"xiahai-xiaoming", "ming-xiahai"},
        "wm_classes": {"xiahai-xiaoming", "xiahai"},
        "programs": {"xiahai-xiaoming"},
    },
    "wechat": {
        "desktop_ids": {"wechat", "weixin", "ming-wechat"},
        "wm_classes": {"wechat", "weixin"},
        "programs": {"wechat", "weixin", "ming-wechat"},
    },
    "dingtalk": {
        "desktop_ids": {
            "dingtalk", "com.alibabainc.dingtalk", "dingding",
        },
        "wm_classes": {"dingtalk", "dingding"},
        "programs": {"dingtalk", "dingding", "elevator.sh"},
    },
    "wps": {
        "desktop_ids": {
            "wps", "wps-office", "wps-office-wps", "wps-office-et",
            "wps-office-wpp", "wps-office-pdf",
        },
        "wm_classes": {"wps", "et", "wpp", "wpspdf"},
        "programs": {"wps", "et", "wpp", "wpspdf"},
    },
}

XIAHAI_GPU_RETRY_ARGUMENT = "--disable-gpu"
XIAHAI_GPU_RETRY_SIGNATURES = (
    "gpu process",
    "gpu initialization",
    "gpu init",
    "failed to initialize gpu",
    "egl initialization",
    "glx initialization",
    "hardware acceleration failed",
)


def _load_common():
    path = pathlib.Path(__file__).with_name("ming-shell-common.py")
    spec = importlib.util.spec_from_file_location("ming_shell_common_for_launch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = _load_common()
_EVENT_LOCK = threading.Lock()


def sandbox_failure_signature(error):
    text = str(error or "").casefold()
    return bool(text) and any(marker in text for marker in SANDBOX_RETRY_SIGNATURES)


def _sandbox_desktop_metadata(desktop_file, argv):
    try:
        path = pathlib.Path(desktop_file)
        if not path.is_absolute() or path.suffix.casefold() != ".desktop":
            return None
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            parser.read_file(stream)
        if not parser.has_section("Desktop Entry"):
            return None
        section = parser["Desktop Entry"]
        if section.get("Type", "Application").strip().casefold() != "application":
            return None
        if section.getboolean("Hidden", fallback=False) or section.getboolean(
                "NoDisplay", fallback=False):
            return None
        entry = COMMON.parse_desktop_file(path)
        ordinary_argv = tuple(argv)
        while ordinary_argv and ordinary_argv[-1] in {
                XIAHAI_GPU_RETRY_ARGUMENT, "--no-sandbox"}:
            ordinary_argv = ordinary_argv[:-1]
        if entry is None or ordinary_argv != tuple(entry.argv):
            return None
        try:
            program = COMMON.desktop_exec_program(entry.argv)
        except (AttributeError, ValueError):
            if not entry.argv:
                return None
            program = entry.argv[0]
        return {
            "desktop_id": path.stem.casefold(),
            "wm_class": section.get("StartupWMClass", "").strip().casefold(),
            "program": pathlib.PurePath(program).name.casefold(),
        }
    except (OSError, UnicodeError, configparser.Error, TypeError, ValueError):
        return None


def _sandbox_retry_allowed(desktop_file, argv):
    metadata = _sandbox_desktop_metadata(desktop_file, argv)
    if not metadata:
        return False
    for allowed in SANDBOX_RETRY_ALLOWLIST.values():
        if metadata["program"] not in allowed["programs"]:
            continue
        if (
            metadata["desktop_id"] in allowed["desktop_ids"]
            or metadata["wm_class"] in allowed["wm_classes"]
        ):
            return True
    return False


def sandbox_retry_argv(desktop_file, argv, error):
    """Return a compatibility retry only for allowlisted sandbox failures."""
    if not sandbox_failure_signature(error) or not isinstance(argv, (tuple, list)):
        return None
    if not argv or "--no-sandbox" in argv or not _sandbox_retry_allowed(desktop_file, argv):
        return None
    return tuple(argv) + ("--no-sandbox",)


def xiahai_gpu_retry_argv(desktop_file, argv, error):
    """Retry the trusted Xiahai launcher once with software rendering."""
    error_text = str(error or "").casefold()
    if (
        not error_text
        or not any(signature in error_text for signature in XIAHAI_GPU_RETRY_SIGNATURES)
        or not isinstance(argv, (tuple, list))
        or not argv
    ):
        return None
    metadata = _sandbox_desktop_metadata(desktop_file, argv)
    allowed = SANDBOX_RETRY_ALLOWLIST["xiahai"]
    if not metadata or metadata["program"] not in allowed["programs"]:
        return None
    if not (
        metadata["desktop_id"] in allowed["desktop_ids"]
        or metadata["wm_class"] in allowed["wm_classes"]
    ):
        return None
    if XIAHAI_GPU_RETRY_ARGUMENT in argv:
        return None
    return tuple(argv) + (XIAHAI_GPU_RETRY_ARGUMENT,)


def compatibility_retry_argv(desktop_file, argv, error):
    """Apply the bounded GPU retry before the sandbox-only fallback."""
    return (
        xiahai_gpu_retry_argv(desktop_file, argv, error)
        or sandbox_retry_argv(desktop_file, argv, error)
    )


def _spawn_with_stderr(argv):
    capture = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(list(argv), shell=False, stderr=capture)
    except (OSError, ValueError, subprocess.SubprocessError):
        capture.close()
        raise
    process._ming_stderr_capture = capture
    return process


def _close_stderr_capture(process):
    capture = getattr(process, "_ming_stderr_capture", None)
    if capture is None:
        return
    try:
        capture.close()
    except OSError:
        pass
    try:
        delattr(process, "_ming_stderr_capture")
    except AttributeError:
        pass


def _process_exit_error(process, returncode):
    detail = ""
    capture = getattr(process, "_ming_stderr_capture", None)
    if capture is not None:
        try:
            capture.flush()
            capture.seek(0)
            detail = capture.read(8192).decode("utf-8", errors="replace").strip()
        except (OSError, UnicodeError):
            detail = ""
        finally:
            _close_stderr_capture(process)
    suffix = ": " + detail[-2048:] if detail else ""
    return RuntimeError("application exited with status {}{}".format(returncode, suffix))


def record_launch_event(request, status, detail="", path=None):
    event_path = pathlib.Path(path) if path else COMMON.runtime_path("launch-events.jsonl")
    event = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": str(status),
        "source": request.source,
        "desktop_file": request.desktop_file,
        "command": request.argv[0] if request.argv else request.desktop_file,
        "detail": str(detail)[:1024],
    }
    try:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with _EVENT_LOCK, event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


class LaunchRequest:
    __slots__ = ("argv", "source", "rect", "desktop_file", "mode")

    def __init__(self, argv, source="unknown", rect=None, desktop_file="", mode="argv"):
        if mode in {"argv", "desktop_proxy"}:
            if not isinstance(argv, (list, tuple)) or not argv or not all(
                isinstance(item, str) and item and "\x00" not in item for item in argv
            ):
                raise ValueError("launch argv must be a non-empty string list")
        elif mode == "desktop_app_info":
            if argv not in ((), []):
                raise ValueError("desktop activation must not carry argv")
            raw_desktop_file = os.fspath(desktop_file) if desktop_file else ""
            if not raw_desktop_file or not (
                    os.path.isabs(raw_desktop_file)
                    or pathlib.PurePosixPath(raw_desktop_file).is_absolute()):
                raise ValueError("desktop activation requires an absolute desktop file")
        else:
            raise ValueError("unsupported launch mode")
        self.argv = tuple(argv)
        self.source = source if source in {"desktop", "drawer", "dock", "settings", "toolbox", "unknown"} else "unknown"
        self.rect = COMMON.Rect.from_mapping(rect) if rect is not None else None
        self.desktop_file = str(desktop_file or "")
        self.mode = mode

    def to_message(self):
        return {
            "version": IPC_VERSION,
            "action": "launch",
            "source": self.source,
            "rect": self.rect.to_dict() if self.rect else None,
            "desktop_file": self.desktop_file,
        }

    @classmethod
    def from_message(cls, message):
        return request_from_message(message)


def allowed_application_dirs(home=None):
    home = pathlib.Path(home or pathlib.Path.home())
    return (
        home / ".local/share/applications",
        home / ".local/share/ming-wine/targets",
        home / ".local/share/ming-android/targets",
        pathlib.Path("/usr/local/share/applications"),
        pathlib.Path("/usr/share/applications"),
    )


def _allowed_desktop_path(path, allowed_dirs=None):
    try:
        candidate = pathlib.Path(path).expanduser().resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise ValueError("desktop file does not exist") from exc
    trusted_dirs = []
    for directory in allowed_dirs or allowed_application_dirs():
        try:
            base = pathlib.Path(directory).expanduser().resolve(strict=True)
        except OSError:
            continue
        trusted_dirs.append(base)
        if candidate == base or base in candidate.parents:
            return candidate
    for base in trusted_dirs:
        try:
            trusted_copy = (base / candidate.name).resolve(strict=True)
        except OSError:
            continue
        if base in trusted_copy.parents:
            return trusted_copy
    raise ValueError("desktop file is outside application directories")


def _is_system_catalog_desktop_file(path, system_dir=SYSTEM_APPLICATION_DIR):
    try:
        candidate = pathlib.Path(path).resolve(strict=True)
        base = pathlib.Path(system_dir).resolve(strict=True)
    except (OSError, TypeError):
        return False
    return candidate.parent == base and candidate.suffix == ".desktop"


def verify_package_owned_system_desktop(path, system_dir=SYSTEM_APPLICATION_DIR,
                                         command_runner=None,
                                         descriptor_revalidator=None):
    """Fail closed unless a system entry is package-owned or image-receipted."""
    return _verify_package_owned_system_desktop(
        path, system_dir, command_runner, descriptor_revalidator)


def _run_dpkg_query(arguments, timeout=2):
    try:
        completed = subprocess.run(
            ["/usr/bin/dpkg-query", *arguments], capture_output=True, text=True,
            timeout=timeout, check=False, shell=False)
        return completed.returncode, completed.stdout, completed.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _descriptor_revalidate(path, system_dir):
    try:
        candidate = pathlib.Path(path)
        base = pathlib.Path(system_dir)
        metadata = candidate.lstat()
        directory_metadata = base.lstat()
    except (OSError, TypeError, ValueError):
        return False
    return (
        candidate.parent == base
        and candidate.suffix == ".desktop"
        and stat.S_ISREG(metadata.st_mode)
        and stat.S_ISDIR(directory_metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not stat.S_ISLNK(directory_metadata.st_mode)
        and (os.name == "nt" or not (metadata.st_mode & 0o022))
        and (os.name == "nt" or not (directory_metadata.st_mode & 0o022))
        and (os.name == "nt" or metadata.st_uid == 0)
        and (os.name == "nt" or directory_metadata.st_uid == 0)
    )


def _verify_package_owned_system_desktop(path, system_dir=SYSTEM_APPLICATION_DIR,
                                         command_runner=None,
                                         descriptor_revalidator=None):
    try:
        candidate = pathlib.Path(path).resolve(strict=True)
        base = pathlib.Path(system_dir).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return False
    if not _descriptor_revalidate(candidate, base):
        return False
    runner = command_runner or _run_dpkg_query
    try:
        rc, output, _error = runner(("-S", "--", str(candidate)), timeout=2)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False
    matches = []
    for line in (output or "").splitlines():
        owner, separator, owned_path = line.partition(": ")
        if separator and owned_path == str(candidate) and owner.strip():
            matches.append(owner.strip())
    owners = sorted(set(matches))
    if len(owners) == 1 and rc == 0:
        try:
            status_rc, status, _status_error = runner(
                ("-W", "-f=${db:Status-Abbrev}", owners[0]), timeout=2)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError):
            return False
        if status_rc != 0 or (status or "").strip() != "ii":
            return False
    elif owners:
        return False
    else:
        marker = pathlib.Path(TRUSTED_DESKTOP_MARKER_DIR) / candidate.name
        try:
            marker_metadata = marker.lstat()
            if (
                    not stat.S_ISREG(marker_metadata.st_mode)
                    or stat.S_ISLNK(marker_metadata.st_mode)
                    or marker_metadata.st_mode & 0o022
                    or (os.name != "nt" and marker_metadata.st_uid != 0)
                    or marker.read_text(encoding="utf-8").strip() != str(candidate)):
                return False
        except (OSError, UnicodeError):
            return False
    revalidator = descriptor_revalidator or _descriptor_revalidate
    try:
        return bool(revalidator(candidate, base))
    except (OSError, TypeError, ValueError):
        return False


def _sha256_path(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_desktop_proxy(path, manifest_path=DESKTOP_PROXY_MANIFEST, receipt_path=None,
                         opt_apps_root=OPT_APPS_ROOT, proxy_dir=DESKTOP_PROXY_DIR,
                         command_runner=None):
    try:
        proxy = pathlib.Path(path).resolve(strict=True)
        proxy_root = pathlib.Path(proxy_dir).resolve(strict=True)
        opt_root = pathlib.Path(opt_apps_root).resolve(strict=True)
        manifest = pathlib.Path(manifest_path).resolve(strict=True)
        receipt = pathlib.Path(receipt_path or (
            str(manifest_path) + ".receipt.json")).resolve(strict=True)
        if (proxy.parent != proxy_root
                or not re.fullmatch(r"ming-opt-[0-9a-f]{64}\.desktop", proxy.name)):
            return False
        for target, directory in ((proxy, False), (manifest, False), (receipt, False),
                                  (proxy_root, True), (manifest.parent, True)):
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
            if directory and not stat.S_ISDIR(metadata.st_mode):
                return False
            if not directory and not stat.S_ISREG(metadata.st_mode):
                return False
            if os.name != "nt" and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
                return False
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        if (payload.get("schema_version") != 1
                or payload.get("generation") != DESKTOP_PROXY_GENERATION
                or not isinstance(payload.get("entries"), list)
                or len(payload["entries"]) > 1024
                or receipt_payload.get("manifest_sha256") != _sha256_path(manifest)):
            return False
        core = {key: payload[key] for key in ("schema_version", "generation", "entries")}
        core_bytes = (json.dumps(core, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n").encode("utf-8")
        if payload.get("sha256") != hashlib.sha256(core_bytes).hexdigest():
            return False
        matches = [entry for entry in payload["entries"]
                   if isinstance(entry, dict) and entry.get("proxy_path") == str(proxy)]
        if len(matches) != 1:
            return False
        entry = matches[0]
        source = pathlib.Path(entry.get("source_path", "")).resolve(strict=True)
        relative = source.relative_to(opt_root)
        if (len(relative.parts) != 4 or relative.parts[1:3] != ("entries", "applications")
                or source.suffix != ".desktop"):
            return False
        source_metadata = source.lstat()
        if (stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode)
                or (os.name != "nt" and (source_metadata.st_uid != 0
                                          or source_metadata.st_mode & 0o022))):
            return False
        if (_sha256_path(source) != entry.get("source_sha256")
                or _sha256_path(proxy) != entry.get("proxy_sha256")):
            return False
        runner = command_runner or _run_dpkg_query
        rc, output, _error = runner(("-S", "--", str(source)), timeout=2)
        owners = {line.partition(": ")[0].strip() for line in (output or "").splitlines()
                  if line.partition(": ")[1] and line.partition(": ")[2] == str(source)}
        if rc != 0 or owners != {entry.get("package")}:
            return False
        status_rc, status, _status_error = runner(
            ("-W", "-f=${db:Status-Abbrev}", entry["package"]), timeout=2)
        return status_rc == 0 and (status or "").strip() == "ii"
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError):
        return False
def request_from_desktop_file(path, source="unknown", rect=None, allowed_dirs=None,
                              system_dir=SYSTEM_APPLICATION_DIR):
    path = _allowed_desktop_path(path, allowed_dirs)
    entry = COMMON.parse_desktop_file(path)
    if entry is None:
        raise ValueError("desktop file is hidden or unavailable")
    if _is_system_catalog_desktop_file(path, system_dir):
        return LaunchRequest((), source, rect, str(path), mode="desktop_app_info")
    mode = "desktop_proxy" if (
        path.parent == DESKTOP_PROXY_DIR
        and re.fullmatch(r"ming-opt-[0-9a-f]{64}\.desktop", path.name)
    ) else "argv"
    return LaunchRequest(entry.argv, source, rect, str(path), mode=mode)


def request_from_message(message, allowed_dirs=None):
    allowed_keys = {"version", "action", "desktop_file", "source", "rect"}
    if (
        not isinstance(message, dict)
        or message.get("version") != IPC_VERSION
        or message.get("action") != "launch"
        or not set(message).issubset(allowed_keys)
    ):
        raise ValueError("invalid launch message")
    return request_from_desktop_file(
        message.get("desktop_file"), message.get("source", "unknown"),
        message.get("rect"), allowed_dirs=allowed_dirs)


def reduced_motion_enabled(path=None):
    override = os.environ.get("MING_REDUCED_MOTION", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    path = pathlib.Path(path or pathlib.Path.home() / ".config/ming-os/settings.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value.get("reduced_motion") is True if isinstance(value, dict) else False
    except (OSError, ValueError):
        return False


def _default_workarea():
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        return {"x": area.x, "y": area.y, "width": area.width, "height": area.height}
    except (ImportError, AttributeError, ValueError):
        return {"x": 0, "y": 0, "width": 1280, "height": 720}


def desktop_window_tokens(desktop_file):
    tokens = []
    if desktop_file:
        path = pathlib.Path(desktop_file)
        tokens.append(path.stem.casefold())
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                parser.read_file(stream)
            if parser.has_section("Desktop Entry"):
                section = parser["Desktop Entry"]
                for key in ("StartupWMClass", "Name", "Name[zh_CN]"):
                    value = section.get(key, "").strip()
                    if value:
                        tokens.append(value.casefold())
                # Gio activation has no child PID to correlate.  A number of
                # desktop files are compatibility aliases whose filename
                # differs from the actual executable/window class (for
                # example fcitx5-configtool -> fcitx5-config-qt).  Include
                # the safe executable basename as another window token.
                try:
                    command = shlex.split(section.get("Exec", ""), posix=True)
                except ValueError:
                    command = []
                if command:
                    executable = pathlib.Path(command[0]).name.strip()
                    if executable:
                        tokens.append(executable.casefold())
        except (OSError, configparser.Error):
            pass
    return tuple(dict.fromkeys(token for token in tokens if token))


def window_matches(stdout, pid=None, desktop_file=""):
    lines = (stdout or "").casefold().splitlines()
    pid_token = " {} ".format(pid) if pid else ""
    tokens = desktop_window_tokens(desktop_file)
    for line in lines:
        padded = " {} ".format(line)
        if pid_token and pid_token in padded:
            return True
        if any(token in line for token in tokens):
            return True
    return False


def probe_window_async(
        process, desktop_file="", on_ready=None, on_failure=None,
        on_timeout=None, attempts=20, interval=0.15):
    pid = getattr(process, "pid", None)

    def probe():
        for _attempt in range(attempts):
            returncode = process.poll() if hasattr(process, "poll") else None
            try:
                result = subprocess.run(
                    ["wmctrl", "-lx", "-p"], capture_output=True, text=True, timeout=1,
                    check=False, shell=False,
                )
                if window_matches(result.stdout, pid=pid, desktop_file=desktop_file):
                    _close_stderr_capture(process)
                    if on_ready:
                        on_ready()
                    return
            except (OSError, subprocess.SubprocessError):
                break
            if returncode not in (None, 0):
                if on_failure:
                    on_failure(_process_exit_error(process, returncode))
                else:
                    _close_stderr_capture(process)
                return
            if interval:
                time.sleep(interval)
        returncode = process.poll() if hasattr(process, "poll") else None
        if returncode not in (None, 0) and on_failure:
            on_failure(_process_exit_error(process, returncode))
        elif on_timeout:
            _close_stderr_capture(process)
            on_timeout()
        else:
            _close_stderr_capture(process)
    threading.Thread(target=probe, name="ming-launch-wmctrl", daemon=True).start()


def report_launch_error(request, error):
    message = "{}: {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%S"), error)
    try:
        log_path = COMMON.runtime_path("launch-errors.log")
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message[:4096])
    except OSError:
        pass
    label = pathlib.Path(request.desktop_file).stem if request.desktop_file else (
        request.argv[0] if request.argv else "应用")
    error_text = str(error or "")
    if "窗口未在等待时间内出现" in error_text:
        notification = "{} 已请求启动，窗口仍在加载或后台运行".format(label)
    else:
        notification = "无法启动 {}".format(label)
    COMMON.run_command(
        ["notify-send", "Ming OS", notification], timeout=2
    )


class LaunchBroker:
    def __init__(
            self, spawn=None, animate=None, now=None, reduced_motion=None,
            workarea=None, probe=None, report_error=None, record_event=None,
            trusted_verifier=None, desktop_activator=None, proxy_verifier=None,
            static_feedback=None):
        self.spawn = spawn or _spawn_with_stderr
        self.trusted_verifier = trusted_verifier or verify_package_owned_system_desktop
        self.desktop_activator = desktop_activator or activate_desktop_app_info
        self.proxy_verifier = proxy_verifier or verify_desktop_proxy
        self.animate = animate or animate_launch
        self.static_feedback = static_feedback or show_static_launch_feedback
        self.now = now or time.monotonic
        self.reduced_motion = reduced_motion or reduced_motion_enabled
        self.workarea = workarea or _default_workarea
        self.probe = probe or probe_window_async
        self.report_error = report_error or report_launch_error
        self.record_event = record_event or record_launch_event
        self._recent = {}

    def launch(self, request):
        moment = self.now()
        key = request.desktop_file or "\x1f".join(request.argv)
        previous = self._recent.get(key)
        if previous is not None and moment - previous < DEDUP_SECONDS:
            return False

        def watch_for_window(process, failure_status, launch_argv=None):
            feedback = self.static_feedback if self.reduced_motion() else self.animate
            finish = feedback(request, self.workarea())

            def ready():
                self.record_event(request, "ready")
                if callable(finish):
                    finish()

            def failed(error):
                retry_argv = compatibility_retry_argv(
                    request.desktop_file, launch_argv, error
                ) if launch_argv else None
                if retry_argv is not None:
                    if callable(finish):
                        finish()
                    retry_kind = (
                        "gpu_retry" if XIAHAI_GPU_RETRY_ARGUMENT in retry_argv
                        and XIAHAI_GPU_RETRY_ARGUMENT not in launch_argv
                        else "sandbox_retry"
                    )
                    self.record_event(request, retry_kind, error)
                    if retry_kind == "sandbox_retry" and "xiahai" in str(
                            request.desktop_file).casefold():
                        COMMON.run_command(
                            ["notify-send", "Xiahai Xiaoming",
                             "沙盒初始化失败，已仅对本应用使用安全兼容模式重试。"],
                            timeout=2,
                        )
                    try:
                        retry_process = self.spawn(retry_argv)
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        self._recent.pop(key, None)
                        self.record_event(request, retry_kind + "_failed", exc)
                        self.report_error(request, exc)
                        return
                    self.record_event(request, retry_kind + "_spawned")
                    watch_for_window(
                        retry_process, retry_kind + "_exit", launch_argv=retry_argv
                    )
                    return
                self._recent.pop(key, None)
                if callable(finish):
                    finish()
                self.record_event(request, failure_status, error)
                self.report_error(request, error)

            def timed_out():
                self._recent.pop(key, None)
                if callable(finish):
                    finish()
                error = RuntimeError("应用已启动，但窗口未在等待时间内出现")
                self.record_event(request, "window_timeout", error)
                self.report_error(request, error)

            try:
                self.probe(
                    process,
                    request.desktop_file,
                    on_ready=ready,
                    on_failure=failed,
                    on_timeout=timed_out,
                )
            except TypeError:
                self.probe(
                    process,
                    request.desktop_file,
                    on_ready=ready,
                    on_failure=failed,
                )

        if request.mode == "desktop_app_info":
            if not self.trusted_verifier(request.desktop_file):
                error = RuntimeError("desktop launcher verification failed")
                self.record_event(request, "verification_failed", error)
                self.report_error(request, error)
                return False
            try:
                entry = COMMON.parse_desktop_file(request.desktop_file)
            except (OSError, ValueError):
                entry = None
            ordinary_argv = tuple(entry.argv) if entry is not None else ()
            if ordinary_argv and _sandbox_retry_allowed(
                    request.desktop_file, ordinary_argv):
                try:
                    process = self.spawn(ordinary_argv)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    status = "command_missing" if isinstance(exc, FileNotFoundError) else "spawn_failed"
                    self.record_event(request, status, exc)
                    self.report_error(request, exc)
                    return False
                self._recent[key] = moment
                self.record_event(request, "spawned")
                watch_for_window(process, "process_exit", launch_argv=ordinary_argv)
                return True
            try:
                activated = self.desktop_activator(request.desktop_file)
            except (OSError, ValueError, RuntimeError) as exc:
                self.record_event(request, "activation_failed", exc)
                self.report_error(request, exc)
                return False
            if not activated:
                error = RuntimeError("desktop launcher activation failed")
                self.record_event(request, "activation_failed", error)
                self.report_error(request, error)
                return False
            self._recent[key] = moment
            self.record_event(request, "activated")
            watch_for_window(None, "activation_failed")
            return True
        if request.mode == "desktop_proxy" and not self.proxy_verifier(request.desktop_file):
            error = RuntimeError("desktop proxy verification failed")
            self.record_event(request, "verification_failed", error)
            self.report_error(request, error)
            return False
        try:
            process = self.spawn(request.argv)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            status = "command_missing" if isinstance(exc, FileNotFoundError) else "spawn_failed"
            self.record_event(request, status, exc)
            self.report_error(request, exc)
            return False
        self._recent[key] = moment
        self.record_event(request, "spawned")
        retry_argv = request.argv if request.mode == "desktop_proxy" else None
        watch_for_window(process, "process_exit", launch_argv=retry_argv)
        return True


def _launch_feedback_window(request, workarea=None, animated=True):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError):
        return
    workarea = COMMON.Rect.from_mapping(workarea or _default_workarea())
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_decorated(False)
    window.set_app_paintable(True)
    window.set_keep_above(True)
    window.set_accept_focus(False)
    screen = window.get_screen()
    visual = screen.get_rgba_visual() if screen else None
    if visual:
        window.set_visual(visual)

    width = min(320, max(240, int(workarea.width * 0.28)))
    height = 76
    window.resize(width, height)
    window.move(
        int(workarea.x + (workarea.width - width) / 2.0),
        int(workarea.y + max(24, min(72, workarea.height * 0.10))),
    )
    # Keep the top-level feedback surface opaque. Transparent top-level
    # windows become unreadable on XRender and older GPUs.
    window.set_opacity(1.0)

    provider = Gtk.CssProvider()
    provider.load_from_data(
        b".ming-launch-feedback { background-color: rgba(247,252,250,0.96);"
        b" border: 1px solid rgba(38,110,91,0.30); border-radius: 10px;"
        b" padding: 10px 14px; }"
    )
    window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window.get_style_context().add_class("ming-launch-feedback")

    panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    panel.get_style_context().add_class("ming-launch-feedback")
    icon_name = "application-x-executable"
    if request.desktop_file:
        entry = COMMON.parse_desktop_file(request.desktop_file)
        if entry and entry.icon:
            icon_name = entry.icon
    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
    image.set_pixel_size(28)
    panel.pack_start(image, False, False, 0)
    text = Gtk.Label(label="正在打开 %s" % (
        pathlib.Path(request.desktop_file).stem if request.desktop_file else "应用"))
    text.set_halign(Gtk.Align.START)
    text.set_hexpand(True)
    panel.pack_start(text, True, True, 0)
    spinner = Gtk.Spinner()
    if animated:
        spinner.start()
    else:
        spinner.set_no_show_all(True)
        spinner.hide()
    panel.pack_start(spinner, False, False, 0)
    window.add(panel)
    window.show_all()
    if not animated:
        spinner.hide()
    started = GLib.get_monotonic_time()

    state = {"destroyed": False}

    def destroy():
        if not state["destroyed"]:
            state["destroyed"] = True
            window.destroy()
        return False

    def finish():
        GLib.idle_add(destroy)

    if animated:
        def step():
            elapsed = (GLib.get_monotonic_time() - started) / 1000.0
            progress = min(1.0, elapsed / ANIMATION_DURATION_MS)
            # Keep the surface opaque on XRender/old GPUs; only the spinner
            # communicates progress so a transparent black rectangle cannot
            # remain after a window manager repaint.
            window.set_opacity(1.0)
            return progress < 1.0 and not state["destroyed"]
        GLib.timeout_add(33, step)
    GLib.timeout_add(FEEDBACK_TIMEOUT_MS, destroy)
    return finish


def animate_launch(request, workarea=None):
    return _launch_feedback_window(request, workarea, animated=True)


def show_static_launch_feedback(request, workarea=None):
    return _launch_feedback_window(request, workarea, animated=False)


def activate_desktop_app_info(desktop_file):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    app_info = Gio.DesktopAppInfo.new_from_filename(str(desktop_file))
    return bool(app_info and app_info.launch([], None))


def schedule_launch(idle_add, broker, request):
    def dispatch(value):
        broker.launch(value)
        return False
    return idle_add(dispatch, request)


class LaunchServer:
    def __init__(self, broker=None):
        self.broker = broker or LaunchBroker()
        self.socket = None

    def _read_request(self, connection):
        with connection:
            return request_from_message(COMMON.recv_json_line(connection, timeout=0.5))

    def _accept_loop(self, dispatch):
        while True:
            try:
                connection, _address = self.socket.accept()
                dispatch(self._read_request(connection))
            except (OSError, ValueError):
                continue

    def serve_forever(self, initial_request=None):
        self.socket = COMMON.claim_runtime_socket("launch", backlog=8)
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib, Gtk
        except (ImportError, ValueError):
            if initial_request is not None:
                self.broker.launch(initial_request)
            self._accept_loop(self.broker.launch)
            return
        threading.Thread(
            target=self._accept_loop,
            args=(lambda request: schedule_launch(GLib.idle_add, self.broker, request),),
            name="ming-launch-ipc",
            daemon=True,
        ).start()
        if initial_request is not None:
            self.broker.launch(initial_request)
        Gtk.main()


def send_to_broker(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.4)
            client.connect(str(COMMON.runtime_socket_path("launch")))
            client.sendall(COMMON.encode_json_line(request.to_message()))
        return True
    except (AttributeError, OSError, ValueError):
        return False


def request_from_args(args):
    rect = json.loads(args.rect) if args.rect else None
    if args.desktop_file:
        return request_from_desktop_file(args.desktop_file, args.source, rect)
    raise ValueError("an allowlisted desktop file is required")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-file")
    parser.add_argument(
        "--source", default="unknown",
        choices=("desktop", "drawer", "dock", "settings", "unknown"))
    parser.add_argument("--rect")
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args(argv)
    if args.server:
        try:
            LaunchServer().serve_forever()
        except COMMON.InstanceAlreadyRunning:
            return 0
        return 0
    try:
        request = request_from_args(args)
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if send_to_broker(request):
        return 0
    server = LaunchServer()
    try:
        server.serve_forever(initial_request=request)
    except COMMON.InstanceAlreadyRunning:
        for _attempt in range(5):
            time.sleep(0.05)
            if send_to_broker(request):
                return 0
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
