# Ming Shell, Settings And Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace visible Xfce control surfaces with Ming settings, status, notification, drawer and launch-animation components.

**Architecture:** Keep Xfwm/Xfconf and system daemons as backends. Add focused Python assets with a typed settings model and single-instance drawer/launch IPC, then install them from `modules/03_desktop.sh`.

**Tech Stack:** Python 3, GTK3 for desktop overlays, GTK4/libadwaita for Settings, Gio, Xfconf CLI, wpctl/pactl, brightnessctl, Unix sockets.

---

### Task 1: Shared Shell Utilities

**Files:**
- Create: `assets/ming-shell-common.py`
- Create: `tests/test_shell_common.py`

- [ ] Write failing tests for desktop-file parsing, source-rectangle validation, easing endpoints, command timeout and runtime socket paths.
- [ ] Run `python -m unittest tests.test_shell_common -v`; expect missing-module failures.
- [ ] Implement `DesktopEntry`, `Rect`, `ease_out_cubic()`, `run_command()`, `runtime_path()` and JSON-line encode/decode without shell evaluation.
- [ ] Run the test again; expect all tests to pass.

### Task 2: Settings Backend And Advanced Settings

**Files:**
- Create: `assets/ming-settings-backend.py`
- Modify: `assets/ming-settings.py`
- Create: `tests/test_settings_backend.py`

- [ ] Write failing tests using temporary Xfconf/GSettings/Plank fixtures for typed read/write/readback, invalid values, rollback and protected autostart entries.
- [ ] Run `python -m unittest tests.test_settings_backend -v`; expect missing backend failures.
- [ ] Implement an allowlisted `SettingSpec` registry for appearance, Dock, windows, display, input, audio, power, notifications, defaults, startup and compatibility.
- [ ] Add backend adapters for Xfconf, GSettings, Plank settings, `wpctl`/`pactl`, `brightnessctl`, `xrandr` and user autostart files. Every setter must read back the value and return structured JSON.
- [ ] Replace the current seven-page Settings navigation with supported controls plus an Advanced page. Unsupported hardware controls must be disabled with a reason.
- [ ] Make `/usr/local/bin/ming-control-center` a compatibility wrapper that executes the GTK4 Settings app and accepts an optional `--page` argument.
- [ ] Remove the visible `xfce4-settings-manager` action.
- [ ] Run backend tests and `python -m py_compile assets/ming-settings.py assets/ming-settings-backend.py`.

### Task 3: Notification History And Status Controls

**Files:**
- Create: `assets/ming-notifications.py`
- Modify: `assets/ming-phone-desktop.py`
- Create: `tests/test_notifications.py`

- [ ] Write failing parser tests for known Xfce Notifyd log variants, malformed entries, 50-item limits, clear-history and DND readback.
- [ ] Run `python -m unittest tests.test_notifications -v`; expect missing-module failures.
- [ ] Implement defensive history parsing and atomic user-owned log clearing.
- [ ] Add bounded volume and brightness sliders, notification unread count, notification panel, clear action and DND switch to `StatusWidget`.
- [ ] Perform volume work in a thread using `wpctl`, `pactl`, then `amixer`; hide brightness when no backlight is writable.
- [ ] Ensure all GTK updates return through `GLib.idle_add` and the widget never blocks desktop clicks.
- [ ] Run notification tests and compile the desktop asset.

### Task 4: Bottom Application Drawer

**Files:**
- Create: `assets/ming-app-drawer.py`
- Modify: `modules/03_desktop.sh`
- Modify: `assets/ming-phone-desktop.py`
- Create: `tests/test_app_drawer.py`

- [ ] Write failing tests for application discovery, desktop exclusion, drawer geometry at 1024x768 and 1366x768, toggle IPC and 180-220 ms easing.
- [ ] Run `python -m unittest tests.test_app_drawer -v`; expect missing-module failures.
- [ ] Implement a single-instance GTK3 drawer with search, categories, recent apps, responsive grid, Escape/backdrop close and context actions.
- [ ] Install only one Plank launcher for the drawer and exclude `ming-app-library.desktop` from the Android desktop and `~/Desktop`.
- [ ] Retire the generated GTK3 application-library heredoc after the asset is installed.
- [ ] Run drawer tests and verify generated launcher syntax.

### Task 5: Unified Launch Broker And Responsive Animation

**Files:**
- Create: `assets/ming-launch.py`
- Modify: `assets/ming-phone-desktop.py`
- Modify: `assets/ming-app-drawer.py`
- Modify: `modules/03_desktop.sh`
- Extend: `tests/test_desktop_regressions.py`

- [ ] Write failing tests for request validation, source rectangles, per-app deduplication, process start before animation, reduced-motion duration and window-probe threading.
- [ ] Run the focused tests; expect failures for missing broker behavior.
- [ ] Implement a per-user single-instance broker and non-interactive feedback surface. Launch the process before the first animation frame.
- [ ] Animate desktop/drawer requests from their tile rectangle and Dock requests from bottom-center; use position/opacity only in software mode.
- [ ] Route core desktop entries and drawer launches through `ming-launch` without changing third-party desktop files on disk.
- [ ] Run all shell UI tests and `tests.test_desktop_regressions`.

### Task 6: Integration Validation

**Files:**
- Modify: `build_onion_os.sh`
- Modify: `scratch/ming-desktop-hotfix.sh`

- [ ] Add build validation for installed assets, removed Xfce Advanced entry, one drawer Dock item, no drawer desktop icon, notification controls and launch broker markers.
- [ ] Update the hotfix installer to deploy the new assets without building an ISO.
- [ ] Run `bash -n build_onion_os.sh modules/03_desktop.sh scratch/ming-desktop-hotfix.sh`.
- [ ] Run `python -m unittest tests.test_shell_common tests.test_settings_backend tests.test_notifications tests.test_app_drawer tests.test_desktop_regressions -v`.

