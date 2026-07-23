#!/usr/bin/env python3
"""Event-driven foreground boost and background policy bridge.

The old session loop enumerated every X11 window and queried properties repeatedly.
This process subscribes to Wnck events instead.  It only touches a window when
it opens, becomes active, changes minimized/workspace state, or closes.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows source-test compatibility
    fcntl = None
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from typing import Any


HIDDEN_DELAY_MS = 10_000
POLICY_TIMEOUT_SECONDS = 2
WINDOW_MANAGER_HELPER_TIMEOUT_SECONDS = 8
WINDOW_MANAGER_RETRY_DELAY_MS = 2_000
WINDOW_MANAGER_RETRY_BACKOFF_SECONDS = 60.0
TRUSTED_DESKTOP = "/usr/share/applications/ming-running-apps.desktop"
LOCK_PATH = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "ming-window-resource-monitor.lock"
MAX_TRACKED_WINDOWS = 512
MAX_TRACKED_PROCESS_IDENTITIES = 512


class EventState:
    """Deterministic event bookkeeping, kept independent of GTK/Wnck."""

    def __init__(self):
        self.boosted: dict[tuple[int, str], float] = {}
        self.hidden: dict[str, dict[str, Any]] = {}
        self.window_identities: dict[str, tuple[int, str]] = {}
        self.window_tracking_saturated = False
        self.backgrounded: set[tuple[int, str]] = set()
        self.background_generations: dict[tuple[int, str], int] = {}
        self.process_policy_saturated = False

    def _prune_window_state(self) -> None:
        """Keep event bookkeeping bounded during long-running sessions."""
        while len(self.hidden) > MAX_TRACKED_WINDOWS:
            removable = next(
                (window_id for window_id in self.hidden
                 if window_id not in self.window_identities),
                None,
            )
            if removable is None:
                break
            self.hidden.pop(removable, None)

    def _prune_process_state(self) -> None:
        while len(self.background_generations) > MAX_TRACKED_PROCESS_IDENTITIES:
            removable = next(
                (identity for identity in self.background_generations
                 if identity not in self.backgrounded),
                None,
            )
            if removable is None:
                self.process_policy_saturated = True
                break
            self.background_generations.pop(removable, None)
        while len(self.boosted) > MAX_TRACKED_PROCESS_IDENTITIES:
            self.boosted.pop(next(iter(self.boosted)))

    def remember_window_identity(self, window_id: str, pid: int, starttime: str) -> bool:
        key = str(window_id)
        if key not in self.window_identities and len(self.window_identities) >= MAX_TRACKED_WINDOWS:
            # Never evict a live identity. An untracked window makes the
            # monitor conservative until capacity is available again.
            self.window_tracking_saturated = True
            return False
        # Re-inserting makes the most recently observed window the newest entry.
        self.window_identities.pop(key, None)
        self.window_identities[key] = (int(pid), str(starttime))
        self._prune_window_state()
        return True

    def window_identity(self, window_id: str) -> tuple[int, str] | None:
        return self.window_identities.get(str(window_id))

    def refresh_window_tracking_capacity(self, live_window_ids) -> None:
        if not self.window_tracking_saturated:
            return
        live = {str(window_id) for window_id in live_window_ids}
        if live.issubset(self.window_identities):
            self.window_tracking_saturated = False

    def discard_process_identity(self, pid: int, starttime: str) -> None:
        identity = (int(pid), str(starttime))
        self.backgrounded.discard(identity)
        self.background_generations.pop(identity, None)
        self.boosted.pop(identity, None)
        for window_id, window_identity in tuple(self.window_identities.items()):
            if window_identity == identity:
                self.window_identities.pop(window_id, None)
                self.hidden.pop(window_id, None)
        self._prune_process_state()

    def allow_boost(self, pid: int, starttime: str, now: float) -> bool:
        key = (int(pid), str(starttime))
        previous = self.boosted.get(key)
        if previous is not None and float(now) - previous < 1.5:
            return False
        self.boosted[key] = float(now)
        # Keep the table bounded when a long-running session opens many apps.
        cutoff = float(now) - 5.0
        self.boosted = {item: stamp for item, stamp in self.boosted.items() if stamp >= cutoff}
        self._prune_process_state()
        return True

    def mark_hidden(self, window_id: str, pid: int, starttime: str, now: float) -> int:
        current = self.hidden.get(str(window_id))
        generation = int(current.get("generation", 0)) + 1 if current else 1
        same_process = bool(
            current
            and int(current.get("pid", -1)) == int(pid)
            and str(current.get("starttime", "")) == str(starttime)
        )
        self.hidden[str(window_id)] = {
            "pid": int(pid), "starttime": str(starttime),
            "since": float(current["since"]) if same_process else float(now),
            "generation": generation,
        }
        self._prune_window_state()
        return generation

    def hidden_ready(self, window_id: str, generation: int, now: float) -> bool:
        value = self.hidden.get(str(window_id))
        if not value or int(value.get("generation", -1)) != int(generation):
            return False
        return float(now) - float(value.get("since", now)) >= HIDDEN_DELAY_MS / 1000.0

    def remaining_hidden_ms(self, window_id: str, generation: int, now: float) -> int:
        value = self.hidden.get(str(window_id))
        if not value or int(value.get("generation", -1)) != int(generation):
            return 0
        elapsed_ms = max(0, round((float(now) - float(value.get("since", now))) * 1000))
        return max(0, HIDDEN_DELAY_MS - elapsed_ms)

    def mark_visible(self, window_id: str) -> dict[str, Any] | None:
        return self.hidden.pop(str(window_id), None)

    def mark_backgrounded(self, pid: int, starttime: str) -> bool:
        key = (int(pid), str(starttime))
        if key in self.backgrounded:
            return False
        if len(self.backgrounded) >= MAX_TRACKED_PROCESS_IDENTITIES:
            self.process_policy_saturated = True
            return False
        self.backgrounded.add(key)
        self._prune_process_state()
        return True

    def consume_backgrounded(self, pid: int, starttime: str) -> bool:
        key = (int(pid), str(starttime))
        if key not in self.backgrounded:
            return False
        self.backgrounded.remove(key)
        return True

    def is_backgrounded(self, pid: int, starttime: str) -> bool:
        return (int(pid), str(starttime)) in self.backgrounded

    def next_background_generation(self, pid: int, starttime: str) -> int:
        key = (int(pid), str(starttime))
        for stale in tuple(self.background_generations):
            if stale[0] == key[0] and stale != key:
                self.background_generations.pop(stale, None)
                self.backgrounded.discard(stale)
        generation = max(
            int(time.monotonic_ns()),
            self.background_generations.get(key, 0) + 1,
        )
        self.background_generations[key] = generation
        self._prune_process_state()
        return generation

    def close(self, window_id: str) -> tuple[int, str] | None:
        identity = self.window_identities.pop(str(window_id), None)
        self.hidden.pop(str(window_id), None)
        return identity


def process_starttime(pid: int) -> str | None:
    try:
        text = pathlib.Path(f"/proc/{int(pid)}/stat").read_text(encoding="ascii")
        fields = text.rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, IndexError, ValueError):
        return None


class PolicyClient:
    def __init__(self, state: EventState):
        self.state = state

    @staticmethod
    def _spawn(argv: list[str], timeout_seconds: int = POLICY_TIMEOUT_SECONDS):
        command = ["timeout", "--foreground", f"{int(timeout_seconds)}s", *argv]
        try:
            return subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError):
            return None

    def boost(self, pid: int, reason: str) -> None:
        starttime = process_starttime(pid)
        if not starttime:
            return
        now = time.monotonic()
        if not self.state.allow_boost(pid, starttime, now):
            return
        self._spawn([
            "ming-interaction-boost", "begin", "--pid", str(pid),
            "--starttime", starttime, "--reason", reason, "--json",
        ])

    def background(self, pid: int, starttime: str, visible: bool) -> None:
        generation = self.state.next_background_generation(pid, starttime)
        self._spawn([
            "ming-background-policy", "apply", "--pid", str(pid),
            "--starttime", str(starttime), "--desktop-file", TRUSTED_DESKTOP,
            "--visible", "true" if visible else "false", "--json",
            "--generation", str(generation),
        ])

    def repair_window_manager(self):
        return self._spawn(
            ["/usr/local/bin/ming-window-manager-watchdog", "--repair-if-needed"],
            timeout_seconds=WINDOW_MANAGER_HELPER_TIMEOUT_SECONDS,
        )


class WnckResourceMonitor:
    def __init__(self, GLib, screen, client: PolicyClient):
        self.GLib = GLib
        self.screen = screen
        self.client = client
        self.windows: dict[str, Any] = {}
        self.timers: dict[str, int] = {}
        self.window_manager_repair_active = False
        self.window_manager_retry_source = 0
        self.window_manager_retry_not_before = 0.0

    def _schedule_window_manager_retry(self) -> None:
        if self.window_manager_retry_source:
            return
        try:
            self.window_manager_retry_source = self.GLib.timeout_add(
                WINDOW_MANAGER_RETRY_DELAY_MS,
                self._run_window_manager_retry,
            )
        except (AttributeError, TypeError, ValueError):
            self.window_manager_retry_not_before = (
                time.monotonic() + WINDOW_MANAGER_RETRY_BACKOFF_SECONDS)

    def _run_window_manager_retry(self):
        self.window_manager_retry_source = 0
        self._start_window_manager_repair(retry=True)
        return False

    def _start_window_manager_repair(self, retry: bool = False) -> bool:
        now = time.monotonic()
        if self.window_manager_repair_active or self.window_manager_retry_source:
            return False
        if now < self.window_manager_retry_not_before:
            return False
        process = self.client.repair_window_manager()
        if process is None:
            self._finish_window_manager_repair(retry, 126)
            return False
        self.window_manager_repair_active = True
        self._observe_window_manager_repair(process, retry)
        return True

    def _observe_window_manager_repair(self, process, retry: bool) -> None:
        observer = threading.Thread(
            target=self._wait_window_manager_repair,
            args=(process, retry),
            name="ming-window-manager-repair-observer",
            daemon=True,
        )
        observer.start()

    def _wait_window_manager_repair(self, process, retry: bool) -> None:
        try:
            returncode = process.wait(timeout=WINDOW_MANAGER_HELPER_TIMEOUT_SECONDS + 1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (AttributeError, OSError, ProcessLookupError):
                try:
                    process.kill()
                except (AttributeError, OSError):
                    pass
            returncode = 124
        except (AttributeError, OSError, ValueError):
            returncode = 126
        try:
            self.GLib.idle_add(
                self._finish_window_manager_repair,
                retry,
                int(returncode),
            )
        except (AttributeError, TypeError, ValueError):
            pass

    def _finish_window_manager_repair(self, retry: bool, returncode: int):
        self.window_manager_repair_active = False
        if int(returncode) == 0:
            self.window_manager_retry_not_before = 0.0
            return False
        if retry:
            self.window_manager_retry_not_before = max(
                self.window_manager_retry_not_before,
                time.monotonic() + WINDOW_MANAGER_RETRY_BACKOFF_SECONDS,
            )
        else:
            self._schedule_window_manager_retry()
        return False

    @staticmethod
    def window_id(window) -> str:
        try:
            xid = int(window.get_xid())
            return f"0x{xid:x}"
        except (AttributeError, TypeError, ValueError):
            return str(id(window))

    @staticmethod
    def pid(window) -> int | None:
        try:
            value = int(window.get_pid())
            return value if value > 0 else None
        except (AttributeError, TypeError, ValueError):
            return None

    def is_visible(self, window) -> bool:
        try:
            if window.is_minimized():
                return False
        except AttributeError:
            return True
        try:
            workspace = self.screen.get_active_workspace()
            if workspace is not None and not window.is_visible_on_workspace(workspace):
                return False
        except AttributeError:
            pass
        return True

    def process_has_visible_window(self, pid: int, starttime: str | None = None) -> bool:
        if self.client.state.window_tracking_saturated:
            return True
        if starttime is None:
            starttime = process_starttime(pid)
        requested = (int(pid), str(starttime)) if starttime else None
        return any(
            self._window_matches_identity(window, requested) and self.is_visible(window)
            for window in self.windows.values()
        )

    def _window_matches_identity(self, window, requested: tuple[int, str] | None) -> bool:
        pid = self.pid(window)
        if pid is None or requested is None or pid != requested[0]:
            return False
        identity = self.client.state.window_identity(self.window_id(window))
        return identity == requested

    def attach(self, window, launch=False):
        key = self.window_id(window)
        if key in self.windows:
            self.reconcile(window)
            return
        self.windows[key] = window
        try:
            window.connect("state-changed", self.on_window_state_changed)
            window.connect("workspace-changed", self.on_window_workspace_changed)
        except (AttributeError, TypeError):
            pass
        pid = self.pid(window)
        if launch and pid is not None:
            self.client.boost(pid, "launch")
        self.reconcile(window)

    def detach(self, window):
        key = self.window_id(window)
        pid = self.pid(window)
        stored_identity = self.client.state.window_identity(key)
        probe_pid = pid if pid is not None else (
            stored_identity[0] if stored_identity is not None else None)
        current_starttime = process_starttime(probe_pid) if probe_pid is not None else None
        self.cancel_timer(key)
        closed_identity = self.client.state.close(key) or stored_identity
        self.windows.pop(key, None)
        self.client.state.refresh_window_tracking_capacity(self.windows)
        if closed_identity:
            old_pid, old_starttime = closed_identity
            if current_starttime is None:
                # The process has exited; there is no safe target to restore.
                self.client.state.discard_process_identity(old_pid, old_starttime)
            elif str(current_starttime) != str(old_starttime):
                # The numeric PID was reused.  Drop only the old identity and
                # never apply its restore request to the new process.
                self.client.state.discard_process_identity(old_pid, old_starttime)
            else:
                if self.client.state.is_backgrounded(old_pid, old_starttime):
                    if self.client.state.consume_backgrounded(old_pid, old_starttime):
                        self.client.background(old_pid, old_starttime, True)
                for item in tuple(self.windows.values()):
                    if (self._window_matches_identity(
                            item, (old_pid, str(old_starttime)))
                            and not self.is_visible(item)):
                        self.reconcile(item)

    def cancel_timer(self, key: str):
        source = self.timers.pop(key, 0)
        if source:
            try:
                self.GLib.source_remove(source)
            except (TypeError, ValueError):
                pass

    def reconcile(self, window):
        pid = self.pid(window)
        if pid is None:
            return
        starttime = process_starttime(pid)
        if not starttime:
            return
        key = self.window_id(window)
        if not self.client.state.remember_window_identity(key, pid, starttime):
            self.client.state.mark_visible(key)
            self.cancel_timer(key)
            return
        self.client.state.refresh_window_tracking_capacity(self.windows)
        if self.is_visible(window):
            self.client.state.mark_visible(key)
            self.cancel_timer(key)
            if self.client.state.consume_backgrounded(pid, starttime):
                self.client.background(pid, starttime, True)
            return
        generation = self.client.state.mark_hidden(
            key, pid, starttime, time.monotonic())
        self.cancel_timer(key)
        delay_ms = max(
            1, self.client.state.remaining_hidden_ms(
                key, generation, time.monotonic()))
        self.timers[key] = self.GLib.timeout_add(
            delay_ms, self.on_hidden_timeout, key, generation)

    def on_hidden_timeout(self, key: str, generation: int):
        window = self.windows.get(key)
        if window is None:
            self.timers.pop(key, None)
            return False
        if not self.client.state.hidden_ready(key, generation, time.monotonic()):
            self.timers.pop(key, None)
            return False
        pid = self.pid(window)
        value = self.client.state.hidden.get(key)
        self.timers.pop(key, None)
        if (pid is not None and value
                and process_starttime(pid) == str(value["starttime"])
                and not self.process_has_visible_window(pid, str(value["starttime"]))
                and self.client.state.mark_backgrounded(pid, str(value["starttime"]))):
            self.client.background(pid, str(value["starttime"]), False)
        return False

    def on_window_state_changed(self, window, *_args):
        self.reconcile(window)

    def on_window_workspace_changed(self, window, *_args):
        self.reconcile(window)

    def on_active_window_changed(self, _screen, _previous=None):
        try:
            window = self.screen.get_active_window()
        except AttributeError:
            window = None
        if window is not None:
            pid = self.pid(window)
            if pid is not None:
                self.client.boost(pid, "activate")
            self.reconcile(window)

    def on_window_opened(self, _screen, window):
        self.attach(window, launch=True)

    def on_window_closed(self, _screen, window):
        self.detach(window)

    def on_workspace_changed(self, *_args):
        for window in tuple(self.windows.values()):
            self.reconcile(window)

    def on_window_manager_changed(self, *_args):
        self._start_window_manager_repair(retry=False)

    def run(self):
        self.screen.connect("active-window-changed", self.on_active_window_changed)
        self.screen.connect("window-opened", self.on_window_opened)
        self.screen.connect("window-closed", self.on_window_closed)
        self.screen.connect("active-workspace-changed", self.on_workspace_changed)
        self.screen.connect("window-manager-changed", self.on_window_manager_changed)
        self.screen.force_update()
        for window in self.screen.get_windows():
            self.attach(window)
        self.GLib.MainLoop().run()


def run() -> int:
    if fcntl is None:
        return 2
    try:
        import gi
        gi.require_version("Wnck", "3.0")
        from gi.repository import GLib, Wnck
    except (ImportError, ValueError):
        return 2
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        Wnck.set_client_type(Wnck.ClientType.PAGER)
        screen = Wnck.Screen.get_default()
        if screen is None:
            return 2
        WnckResourceMonitor(GLib, screen, PolicyClient(EventState())).run()
    return 0


if __name__ == "__main__":
    sys.exit(run())
