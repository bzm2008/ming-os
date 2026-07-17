#!/usr/bin/env python3
"""Single-instance application launch broker with bounded visual feedback."""

import argparse
import importlib.util
import json
import os
import pathlib
import re
import socket
import stat
import subprocess
import sys
import threading
import time


ANIMATION_DURATION_MS = 200
FEEDBACK_TIMEOUT_MS = 4000
DEDUP_SECONDS = 0.6
IPC_VERSION = 1
ACTIVATION_ACK_TIMEOUT = 6.0
DPKG_QUERY = "/usr/bin/dpkg-query"
SYSTEM_APPLICATION_DIR = pathlib.Path("/usr/share/applications")
TRUSTED_DESKTOP_MARKER_DIR = pathlib.Path("/var/lib/ming-os/trusted-desktops")
INTERACTION_BOOST = "/usr/local/bin/ming-interaction-boost"
PREFETCH_HELPER = "/usr/local/bin/ming-prefetch"


def _load_common():
    path = pathlib.Path(__file__).with_name("ming-shell-common.py")
    spec = importlib.util.spec_from_file_location("ming_shell_common_for_launch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = _load_common()
_EVENT_LOCK = threading.Lock()


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
        if mode == "argv":
            if not isinstance(argv, (list, tuple)) or not argv or not all(
                isinstance(item, str) and item and "\x00" not in item for item in argv
            ):
                raise ValueError("launch argv must be a non-empty string list")
        elif mode == "desktop_app_info":
            if argv not in ((), []):
                raise ValueError("desktop app info launch must not carry argv")
            path = os.fspath(desktop_file)
            if not os.path.isabs(path) or any(
                    part in {".", ".."} for part in pathlib.PurePath(path).parts):
                raise ValueError("desktop app info requires a canonical desktop file")
        else:
            raise ValueError("unsupported launch mode")
        self.argv = tuple(argv)
        self.source = source if source in {"desktop", "drawer", "dock", "unknown"} else "unknown"
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


def _canonical_system_desktop_file(path, system_dir=SYSTEM_APPLICATION_DIR):
    raw_path = os.fspath(path)
    raw_directory = os.fspath(system_dir)
    if not isinstance(raw_path, str) or not isinstance(raw_directory, str):
        raise ValueError("desktop path is invalid")
    if not os.path.isabs(raw_path) or not os.path.isabs(raw_directory):
        raise ValueError("desktop path must be absolute")
    if (
            any(part in {".", ".."} for part in pathlib.PurePath(raw_path).parts)
            or any(part in {".", ".."} for part in pathlib.PurePath(raw_directory).parts)):
        raise ValueError("desktop path must be canonical")
    desktop_path = pathlib.Path(raw_path)
    directory_path = pathlib.Path(raw_directory)
    if desktop_path.parent != directory_path or desktop_path.suffix != ".desktop":
        raise ValueError("desktop path is outside the system directory")
    return desktop_path, directory_path


def _protected_directory(metadata):
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == 0
        and not (metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    )


def _protected_regular_file(metadata):
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and not (metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    )


def descriptor_revalidate_system_desktop(
        path, system_dir=SYSTEM_APPLICATION_DIR, fstat_reader=None):
    """Recheck a system desktop entry without following its directory or leaf."""
    try:
        desktop_path, directory_path = _canonical_system_desktop_file(path, system_dir)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        nonblock_flag = getattr(os, "O_NONBLOCK", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        if (
                not isinstance(directory_flag, int)
                or not isinstance(nofollow_flag, int)
                or not isinstance(nonblock_flag, int)
                or not isinstance(cloexec_flag, int)
                or directory_flag <= 0
                or nofollow_flag <= 0
                or nonblock_flag <= 0
                or cloexec_flag <= 0):
            return False
        metadata_reader = fstat_reader or os.fstat
        directory_flags = os.O_RDONLY | directory_flag | nofollow_flag
        directory_fd = os.open(str(directory_path), directory_flags)
    except (AttributeError, OSError, PermissionError, RuntimeError, TypeError, ValueError):
        return False
    try:
        if not _protected_directory(metadata_reader(directory_fd)):
            return False
        try:
            leaf_fd = os.open(
                desktop_path.name,
                os.O_RDONLY | nofollow_flag | nonblock_flag | cloexec_flag,
                dir_fd=directory_fd,
            )
        except (AttributeError, OSError, PermissionError, RuntimeError, TypeError, ValueError):
            return False
        try:
            return _protected_regular_file(metadata_reader(leaf_fd))
        finally:
            try:
                os.close(leaf_fd)
            except OSError:
                pass
    except (AttributeError, OSError, PermissionError, RuntimeError, TypeError, ValueError):
        return False
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass


def _run_dpkg_query(argv, timeout=2):
    return subprocess.run(
        [DPKG_QUERY, *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def verify_package_owned_system_desktop(
        path, system_dir=SYSTEM_APPLICATION_DIR, command_runner=None,
        descriptor_revalidator=None):
    """Verify package ownership and protected descriptors immediately before GIO."""
    try:
        desktop_path, directory_path = _canonical_system_desktop_file(path, system_dir)
        runner = command_runner or _run_dpkg_query
        owner_lookup = getattr(COMMON, "installed_package_owner", None)
        if not callable(owner_lookup):
            return False
        if not owner_lookup(desktop_path, command_runner=runner, timeout=2):
            marker = TRUSTED_DESKTOP_MARKER_DIR / desktop_path.name
            metadata = marker.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                return False
            if marker.read_text(encoding="utf-8").strip() != str(desktop_path):
                return False
        revalidator = descriptor_revalidator or descriptor_revalidate_system_desktop
        return bool(revalidator(desktop_path, directory_path))
    except (
            AttributeError, OSError, PermissionError, RuntimeError, TypeError,
            ValueError, subprocess.TimeoutExpired):
        return False


def _is_system_catalog_desktop_file(path):
    try:
        desktop_path = pathlib.PurePosixPath(str(os.fspath(path)).replace("\\", "/"))
        return (
            desktop_path.suffix == ".desktop"
            and desktop_path.parent == pathlib.PurePosixPath(SYSTEM_APPLICATION_DIR.as_posix()))
    except (TypeError, ValueError):
        return False


def _is_shell_wrapper_error(error):
    return str(error) == "shell command wrappers are not allowed"


def request_from_desktop_file(
        desktop_file, source="unknown", rect=None, allowed_dirs=None,
        candidate_verifier=None, trusted_verifier=None, defer_trusted_verification=False):
    path = _allowed_desktop_path(desktop_file, allowed_dirs)
    system_catalog_entry = _is_system_catalog_desktop_file(path)
    try:
        entry = COMMON.parse_desktop_file(
            path, respect_desktop_environment=system_catalog_entry)
    except ValueError as exc:
        if not _is_shell_wrapper_error(exc):
            raise
        verifier = candidate_verifier or COMMON.is_system_desktop_activation_candidate
        if not verifier(path):
            raise
        try:
            visibility_entry = COMMON.parse_desktop_file(
                path, respect_desktop_environment=True)
        except ValueError as visibility_error:
            if not _is_shell_wrapper_error(visibility_error):
                raise
        else:
            if visibility_entry is None:
                raise ValueError("desktop file is hidden or unavailable")
        final_verifier = trusted_verifier or verify_package_owned_system_desktop
        if not defer_trusted_verification and not final_verifier(path):
            raise ValueError("system desktop wrapper is not verified")
        return LaunchRequest((), source, rect, str(path), mode="desktop_app_info")
    if entry is None:
        raise ValueError("desktop file is hidden or unavailable")
    return LaunchRequest(entry.argv, source, rect, str(path))


def request_from_message(message, allowed_dirs=None, defer_trusted_verification=False):
    allowed_keys = {"version", "action", "request_id", "desktop_file", "source", "rect"}
    if (
            not isinstance(message, dict)
            or message.get("version") != IPC_VERSION
            or message.get("action") != "launch"
            or not set(message).issubset(allowed_keys)
    ):
        raise ValueError("invalid launch message")
    request_id = message.get("request_id")
    if request_id is not None and not COMMON.is_launch_request_id(request_id):
        raise ValueError("invalid launch request id")
    return request_from_desktop_file(
        message.get("desktop_file"),
        source=message.get("source", "unknown"),
        rect=message.get("rect"),
        allowed_dirs=allowed_dirs,
        defer_trusted_verification=defer_trusted_verification,
    )


def resolve_origin(request, workarea):
    if request.source in {"desktop", "drawer", "dock"} and request.rect is not None:
        return request.rect
    workarea = COMMON.Rect.from_mapping(workarea)
    center = workarea.x + workarea.width / 2.0
    bottom = workarea.y + workarea.height
    return COMMON.Rect(center - 0.5, bottom - 1.0, 1.0, 1.0)


def feedback_geometry(origin, workarea, progress):
    origin = COMMON.Rect.from_mapping(origin.to_dict() if hasattr(origin, "to_dict") else origin)
    workarea = COMMON.Rect.from_mapping(workarea)
    progress = COMMON.ease_out_cubic(max(0.0, min(1.0, float(progress))))
    start_width = 52.0
    start_height = 52.0
    start_center_x, start_bottom = origin.bottom_center
    start_x = start_center_x - start_width / 2.0
    start_y = start_bottom - start_height

    target_width = min(420.0, max(280.0, workarea.width * 0.34))
    target_height = min(260.0, max(168.0, workarea.height * 0.28))
    target_x = workarea.x + (workarea.width - target_width) / 2.0
    target_y = workarea.y + max(36.0, (workarea.height - target_height) * 0.42)

    def blend(start, end):
        return start + (end - start) * progress

    return COMMON.Rect(
        blend(start_x, target_x),
        blend(start_y, target_y),
        blend(start_width, target_width),
        blend(start_height, target_height),
    )


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


def probe_window_async(
        process, desktop_file="", on_ready=None, on_failure=None,
        on_timeout=None, attempts=20, interval=0.15):
    pid = getattr(process, "pid", None)

    def probe():
        for _attempt in range(attempts):
            returncode = process.poll() if hasattr(process, "poll") else None
            if returncode is not None:
                if returncode != 0 and on_failure:
                    on_failure(RuntimeError("application exited with status {}".format(returncode)))
                elif returncode == 0 and on_timeout:
                    on_timeout()
                return
            try:
                result = subprocess.run(
                    ["wmctrl", "-lp"], capture_output=True, text=True, timeout=1,
                    check=False, shell=False,
                )
                needle = pathlib.Path(desktop_file).stem.casefold() if desktop_file else ""
                lines = result.stdout.casefold().splitlines()
                if any((pid and " {} ".format(pid) in " {} ".format(line)) or (needle and needle in line) for line in lines):
                    if on_ready:
                        on_ready()
                    return
            except (OSError, subprocess.SubprocessError):
                break
            if interval:
                time.sleep(interval)
        returncode = process.poll() if hasattr(process, "poll") else None
        if returncode not in (None, 0) and on_failure:
            on_failure(RuntimeError("application exited with status {}".format(returncode)))
        elif on_timeout:
            on_timeout()
    threading.Thread(target=probe, name="ming-launch-wmctrl", daemon=True).start()


def report_launch_error(request, error):
    message = "{}: {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%S"), error)
    try:
        log_path = COMMON.runtime_path("launch-errors.log")
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message[:4096])
    except OSError:
        pass
    label = pathlib.Path(request.desktop_file).stem if request.desktop_file else request.argv[0]
    COMMON.run_command(
        ["notify-send", "Ming OS", "无法启动 {}".format(label)], timeout=2
    )


def activate_desktop_app_info(desktop_file):
    """Activate a final-verified package desktop file in the user session."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    app_info = Gio.DesktopAppInfo.new_from_filename(str(desktop_file))
    return bool(app_info and app_info.launch([], None))


class LaunchBroker:
    def __init__(
            self, spawn=None, animate=None, now=None, reduced_motion=None,
            workarea=None, probe=None, report_error=None, record_event=None,
            desktop_activator=None, trusted_verifier=None):
        self.spawn = spawn or (lambda argv: subprocess.Popen(list(argv), shell=False))
        self.desktop_activator = desktop_activator or activate_desktop_app_info
        self.trusted_verifier = trusted_verifier or verify_package_owned_system_desktop
        self.animate = animate or animate_launch
        self.now = now or time.monotonic
        self.reduced_motion = reduced_motion or reduced_motion_enabled
        self.workarea = workarea or _default_workarea
        self.probe = probe or probe_window_async
        self.report_error = report_error or report_launch_error
        self.record_event = record_event or record_launch_event
        self._recent = {}

    def preflight(self, request):
        """Perform the final package check for protected system launchers."""
        if (
                request.mode != "desktop_app_info"
                and not _is_system_catalog_desktop_file(request.desktop_file)):
            return True
        return bool(self.trusted_verifier(request.desktop_file))

    def launch(self, request):
        moment = self.now()
        key = request.desktop_file or "\x1f".join(request.argv)
        previous = self._recent.get(key)
        if previous is not None and moment - previous < DEDUP_SECONDS:
            return False
        if request.mode == "desktop_app_info":
            return self._launch_desktop_app_info(request, key, moment)
        return self._launch_argv(request, key, moment)

    def _launch_desktop_app_info(self, request, key, moment):
        try:
            verified = self.preflight(request)
        except Exception as exc:
            self.record_event(request, "activation_failed", exc)
            self.report_error(request, exc)
            return False
        if not verified:
            error = RuntimeError("desktop launcher verification failed")
            self.record_event(request, "activation_failed", error)
            self.report_error(request, error)
            return False
        return self._activate_desktop_app_info(request, key, moment)

    def _activate_desktop_app_info(self, request, key, moment):
        try:
            activated = self.desktop_activator(request.desktop_file)
        except Exception as exc:
            self.record_event(request, "activation_failed", exc)
            self.report_error(request, exc)
            return False
        if not activated:
            error = RuntimeError("desktop launcher activation failed")
            self.record_event(request, "activation_failed", error)
            self.report_error(request, error)
            return False
        self._after_start(request, key, moment, None, "activated")
        return True

    def _launch_argv(self, request, key, moment):
        if _is_system_catalog_desktop_file(request.desktop_file):
            try:
                verified = self.preflight(request)
            except Exception as exc:
                self.record_event(request, "spawn_failed", exc)
                self.report_error(request, exc)
                return False
            if not verified:
                error = RuntimeError("desktop launcher verification failed")
                self.record_event(request, "spawn_failed", error)
                self.report_error(request, error)
                return False
        try:
            process = self.spawn(request.argv)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            status = "command_missing" if isinstance(exc, FileNotFoundError) else "spawn_failed"
            self.record_event(request, status, exc)
            self.report_error(request, exc)
            return False
        self._after_start(request, key, moment, process, "spawned")
        return True

    def _after_start(self, request, key, moment, process, status):
        self._recent[key] = moment
        self.record_event(request, status)
        self._request_interaction_boost(process, request)
        self._request_prefetch(process)
        origin = resolve_origin(request, self.workarea())
        finish = None
        if not self.reduced_motion():
            finish = self.animate(request, origin)

        def ready():
            self.record_event(request, "ready")
            if callable(finish):
                finish()

        def failed(error):
            self._recent.pop(key, None)
            if callable(finish):
                finish()
            self.record_event(request, "process_exit", error)
            self.report_error(request, error)

        def timed_out():
            self.record_event(request, "window_timeout")
            if callable(finish):
                finish()

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

    @staticmethod
    def _process_starttime(process):
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 1:
            return None
        try:
            text = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            return text.rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError, ValueError):
            return None

    @classmethod
    def _request_interaction_boost(cls, process, request):
        """Ask the bounded policy daemon without delaying launch feedback."""
        if not process or not pathlib.Path(INTERACTION_BOOST).exists():
            return
        pid = getattr(process, "pid", None)
        starttime = cls._process_starttime(process)
        if not isinstance(pid, int) or not starttime:
            return
        try:
            subprocess.Popen(
                [INTERACTION_BOOST, "begin", "--pid", str(pid),
                 "--starttime", starttime, "--reason", "launch", "--json"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError):
            # A missing daemon or an unprivileged fallback must never make an
            # otherwise valid desktop launch fail.
            return

    @classmethod
    def _request_prefetch(cls, process):
        """Warm a bounded dependency list only when the helper detects HDD."""
        if not process or not pathlib.Path(PREFETCH_HELPER).exists():
            return
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 1:
            return
        try:
            subprocess.Popen(
                [PREFETCH_HELPER, "warm", "--pid", str(pid), "--json"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError):
            return


def feedback_icon_name(request):
    """Return an animation icon without interpreting a trusted shell wrapper."""
    icon_name = "application-x-executable"
    if request.mode != "desktop_app_info" and request.desktop_file:
        try:
            entry = COMMON.parse_desktop_file(request.desktop_file)
        except ValueError:
            entry = None
        if entry and entry.icon:
            icon_name = entry.icon
    return COMMON.resolve_icon(icon_name)


def animate_launch(request, origin):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError):
        return
    workarea = _default_workarea()
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_decorated(False)
    window.set_app_paintable(True)
    window.set_keep_above(True)
    window.set_accept_focus(False)
    screen = window.get_screen()
    visual = screen.get_rgba_visual() if screen else None
    if visual:
        window.set_visual(visual)

    initial = feedback_geometry(origin, workarea, 0.0)
    window.resize(int(initial.width), int(initial.height))
    window.move(int(initial.x), int(initial.y))
    window.set_opacity(0.18)

    provider = Gtk.CssProvider()
    provider.load_from_data(
        b".ming-launch-feedback { background-color: rgba(247,252,250,0.94);"
        b" border: 1px solid rgba(38,110,91,0.38); border-radius: 16px; }"
    )
    window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window.get_style_context().add_class("ming-launch-feedback")

    overlay = Gtk.Overlay()
    icon_name = feedback_icon_name(request)
    if pathlib.Path(icon_name).is_absolute():
        image = Gtk.Image()
        pixbuf = COMMON.load_icon_pixbuf(Gtk.IconTheme.get_default(), icon_name, 48)
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)
    else:
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
    image.set_halign(Gtk.Align.CENTER)
    image.set_valign(Gtk.Align.CENTER)
    overlay.add(image)
    spinner = Gtk.Spinner()
    spinner.set_halign(Gtk.Align.CENTER)
    spinner.set_valign(Gtk.Align.END)
    spinner.set_margin_bottom(18)
    spinner.start()
    overlay.add_overlay(spinner)
    window.add(overlay)
    window.show_all()
    started = GLib.get_monotonic_time()

    state = {"destroyed": False}

    def destroy():
        if not state["destroyed"]:
            state["destroyed"] = True
            window.destroy()
        return False

    def finish():
        GLib.idle_add(destroy)

    def step():
        elapsed = (GLib.get_monotonic_time() - started) / 1000.0
        progress = min(1.0, elapsed / ANIMATION_DURATION_MS)
        geometry = feedback_geometry(origin, workarea, progress)
        window.move(int(geometry.x), int(geometry.y))
        window.resize(max(1, int(geometry.width)), max(1, int(geometry.height)))
        window.set_opacity(0.18 + 0.60 * COMMON.ease_out_cubic(progress))
        return progress < 1.0 and not state["destroyed"]
    GLib.timeout_add(16, step)
    GLib.timeout_add(FEEDBACK_TIMEOUT_MS, destroy)
    return finish


def schedule_launch(idle_add, broker, request):
    def dispatch(value):
        broker.launch(value)
        return False
    return idle_add(dispatch, request)


def schedule_launch_after_preflight(
        idle_add, broker, request, timeout=ACTIVATION_ACK_TIMEOUT):
    """Schedule a launch only after GTK has completed its final preflight.

    One preflight runs in the IPC worker before GTK receives an activation
    request.  The normal GTK-context broker launch repeats final verification
    immediately before GIO, then returns the real activation result.  Legacy
    disks therefore do not block the desktop while the first dpkg checks run.
    """
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        return COMMON.LaunchRequestResult("rejected", "invalid launch scheduling timeout")
    if timeout <= 0 or timeout > 8:
        return COMMON.LaunchRequestResult("rejected", "invalid launch scheduling timeout")

    preflight = getattr(broker, "preflight", None)
    if not callable(preflight):
        return COMMON.LaunchRequestResult("rejected", "launch preflight is unavailable")
    try:
        if not preflight(request):
            return COMMON.LaunchRequestResult("rejected", "system desktop wrapper is not verified")
    except Exception as exc:
        return COMMON.LaunchRequestResult(
            "rejected", str(exc) or "desktop launcher verification failed")

    completed = threading.Event()
    cancelled = threading.Event()
    state = {"result": COMMON.LaunchRequestResult("rejected", "launch was not run")}

    def dispatch(value):
        if cancelled.is_set():
            completed.set()
            return False
        try:
            launched = bool(broker.launch(value))
            if launched and not cancelled.is_set():
                state["result"] = COMMON.LaunchRequestResult("accepted")
            else:
                state["result"] = COMMON.LaunchRequestResult("rejected", "application launch failed")
        except Exception as exc:
            state["result"] = COMMON.LaunchRequestResult("rejected", str(exc) or "application launch failed")
        finally:
            completed.set()
        return False

    try:
        source_id = idle_add(dispatch, request)
    except Exception:
        return COMMON.LaunchRequestResult("rejected", "launch scheduling failed")
    if not source_id:
        return COMMON.LaunchRequestResult("rejected", "launch scheduling failed")
    if not completed.wait(timeout):
        cancelled.set()
        return COMMON.LaunchRequestResult("rejected", "launch preflight timed out")
    return state["result"]


class LaunchServer:
    def __init__(self, broker=None):
        self.broker = broker or LaunchBroker()
        self.socket = None

    def _read_request(self, connection):
        with connection:
            return request_from_message(COMMON.recv_json_line(connection, timeout=0.5))

    @staticmethod
    def _send_result(connection, request_id, accepted, error=""):
        if request_id is None:
            return True
        if not COMMON.is_launch_request_id(request_id):
            return False
        try:
            connection.sendall(COMMON.encode_json_line(
                COMMON.launch_result_message(request_id, accepted, error)))
            return True
        except (OSError, ValueError):
            return False

    def _handle_connection(self, connection, dispatch):
        """Validate one request, return its correlated result, then dispatch it."""
        request_id = None
        try:
            with connection:
                message = COMMON.recv_json_line(connection, timeout=0.5)
                if isinstance(message, dict):
                    request_id = message.get("request_id")
                try:
                    request = request_from_message(message, defer_trusted_verification=True)
                except ValueError as exc:
                    self._send_result(connection, request_id, False, str(exc))
                    return False
                try:
                    dispatched = dispatch(request)
                except Exception as exc:
                    self._send_result(connection, request_id, False, str(exc) or "launch scheduling failed")
                    return False
                if isinstance(dispatched, COMMON.LaunchRequestResult):
                    if not dispatched.accepted:
                        self._send_result(connection, request_id, False, dispatched.error)
                        return False
                elif dispatched is False:
                    self._send_result(connection, request_id, False, "launch scheduling failed")
                    return False
                if not self._send_result(connection, request_id, True):
                    return False
        except (OSError, ValueError):
            return False
        return True

    def _accept_loop(self, dispatch):
        while True:
            try:
                connection, _address = self.socket.accept()
                self._handle_connection(connection, dispatch)
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
            args=(lambda request: schedule_launch_after_preflight(GLib.idle_add, self.broker, request),),
            name="ming-launch-ipc",
            daemon=True,
        ).start()
        if initial_request is not None:
            self.broker.launch(initial_request)
        Gtk.main()


def send_to_broker(request):
    rect = request.rect.to_dict() if request.rect else None
    return COMMON.send_launch_request(
        request.desktop_file,
        request.source,
        rect,
        timeout=getattr(COMMON, "ASYNC_LAUNCH_REQUEST_TIMEOUT", 12.0),
    )


def request_from_args(args):
    rect = json.loads(args.rect) if args.rect else None
    if args.desktop_file:
        return request_from_desktop_file(args.desktop_file, args.source, rect)
    raise ValueError("an allowlisted desktop file is required")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-file")
    parser.add_argument("--source", default="unknown", choices=("desktop", "drawer", "dock", "unknown"))
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
    broker_result = send_to_broker(request)
    if broker_result:
        return 0
    if getattr(broker_result, "rejected", False):
        return 1
    server = LaunchServer()
    try:
        server.serve_forever(initial_request=request)
    except COMMON.InstanceAlreadyRunning:
        for _attempt in range(5):
            time.sleep(0.05)
            broker_result = send_to_broker(request)
            if broker_result:
                return 0
            if getattr(broker_result, "rejected", False):
                return 1
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
