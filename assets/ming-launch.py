#!/usr/bin/env python3
"""Single-instance application launch broker with bounded visual feedback."""

import argparse
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time


ANIMATION_DURATION_MS = 200
FEEDBACK_TIMEOUT_MS = 4000
DEDUP_SECONDS = 0.6
IPC_VERSION = 1


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
        "command": request.argv[0],
        "detail": str(detail)[:1024],
    }
    try:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with _EVENT_LOCK, event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


class LaunchRequest:
    __slots__ = ("argv", "source", "rect", "desktop_file")

    def __init__(self, argv, source="unknown", rect=None, desktop_file=""):
        if not isinstance(argv, (list, tuple)) or not argv or not all(
            isinstance(item, str) and item and "\x00" not in item for item in argv
        ):
            raise ValueError("launch argv must be a non-empty string list")
        self.argv = tuple(argv)
        self.source = source if source in {"desktop", "drawer", "dock", "unknown"} else "unknown"
        self.rect = COMMON.Rect.from_mapping(rect) if rect is not None else None
        self.desktop_file = str(desktop_file or "")

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


def request_from_message(message, allowed_dirs=None):
    allowed_keys = {"version", "action", "desktop_file", "source", "rect"}
    if (
        not isinstance(message, dict)
        or message.get("version") != IPC_VERSION
        or message.get("action") != "launch"
        or not set(message).issubset(allowed_keys)
    ):
        raise ValueError("invalid launch message")
    path = _allowed_desktop_path(message.get("desktop_file"), allowed_dirs)
    entry = COMMON.parse_desktop_file(path)
    if entry is None:
        raise ValueError("desktop file is hidden or unavailable")
    return LaunchRequest(entry.argv, message.get("source", "unknown"), message.get("rect"), str(path))


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


class LaunchBroker:
    def __init__(
            self, spawn=None, animate=None, now=None, reduced_motion=None,
            workarea=None, probe=None, report_error=None, record_event=None):
        self.spawn = spawn or (lambda argv: subprocess.Popen(list(argv), shell=False))
        self.animate = animate or animate_launch
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
        try:
            process = self.spawn(request.argv)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            status = "command_missing" if isinstance(exc, FileNotFoundError) else "spawn_failed"
            self.record_event(request, status, exc)
            self.report_error(request, exc)
            return False
        self._recent[key] = moment
        self.record_event(request, "spawned")
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
        return True


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
    icon_name = "application-x-executable"
    if request.desktop_file:
        entry = COMMON.parse_desktop_file(request.desktop_file)
        if entry and entry.icon:
            icon_name = entry.icon
    icon_name = COMMON.resolve_icon(icon_name)
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
        path = _allowed_desktop_path(args.desktop_file)
        entry = COMMON.parse_desktop_file(path)
        if entry is None:
            raise ValueError("desktop file is hidden")
        return LaunchRequest(entry.argv, args.source, rect, str(path))
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
