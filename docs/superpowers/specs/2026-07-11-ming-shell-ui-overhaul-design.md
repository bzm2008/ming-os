# Ming Shell UI Overhaul Design

Date: 2026-07-11
Status: Approved for implementation

## Context

Ming OS currently mixes a custom Android-style desktop, Plank, two different
settings applications, Thunar, Xfce notification UI, and two visually different
GRUB menus. Several Xfce settings no longer affect the desktop because Ming OS
has replaced the panel, desktop icons, Dock, wallpaper, and parts of session
startup. The existing "Advanced Settings" entry still opens
`xfce4-settings-manager`, exposing controls that appear to work but are ignored.

The current OTA major-upgrade path also claims that user data is preserved, but
only writes `/tmp/ming-major-upgrade.conf`; it does not copy `/home`, persist the
backup metadata across reboot, or restore it after installation. This capability
must not be advertised as working until real backup and restore behavior exists.

## Goals

- Keep Xfwm, Xfconf, NetworkManager, UPower, PipeWire/PulseAudio, UDisks and
  Xfce Notifyd as lightweight service layers while replacing most visible Xfce
  applications with Ming interfaces.
- Make every visible setting effective, observable, and reversible.
- Add volume, brightness, recent notifications, clear-history, Do Not Disturb,
  Wi-Fi, Bluetooth, battery, settings, and power controls to the desktop widget.
- Replace the application-library window with a bottom application drawer
  toggled from one Plank item, and remove its desktop icon.
- Remove the separate "All Disks" feature and integrate disks and removable
  media into a redesigned Ming Files application.
- Make launches feel immediate through a lightweight source-to-window animation
  that remains usable on software-rendered and old Intel graphics.
- Unify Live and installed GRUB appearance and preserve text-mode fallbacks.
- Establish release gates for OTA, APT, user-data backup/restore, Edge rendering,
  Spark Store startup, installer reliability, Wi-Fi diagnostics, and desktop
  activation.

## Non-Goals

- Replacing Xfwm with KWin, Mutter, or another full desktop shell.
- Adding a third-party Picom fork or requiring 3D acceleration.
- Reimplementing filesystem protocols, trash, mounts, or copy semantics instead
  of using GIO/GVfs.
- Promising support for an unidentified Wi-Fi device without a PCI or USB ID.
- Performing an unattended destructive OTA reinstall when no verified backup
  destination exists.

## Architecture

### Source Layout

New user-facing Python programs live in `assets/` and are installed by
`modules/03_desktop.sh`. Large generated Python heredocs are retired as each
replacement lands.

- `assets/ming-shell-common.py`: shared command runner, desktop-file parsing,
  work-area geometry, easing functions, process logging, and single-instance IPC.
- `assets/ming-app-drawer.py`: bottom drawer, search, categories, app grid, and
  launch requests.
- `assets/ming-launch.py`: launch broker and source-to-window feedback surface.
- `assets/ming-files.py`: GTK4/libadwaita file browser backed by GIO/GVfs.
- `assets/ming-settings.py`: single settings application, including advanced
  settings and hardware diagnostics.
- `assets/ming-settings-backend.py`: allowlisted settings model and privileged
  operations; no arbitrary shell command execution.
- `assets/ming-notifications.py`: defensive parser for Xfce Notifyd history and
  Do Not Disturb state.
- `assets/ming-phone-desktop.py`: Android-style desktop and expanded status widget.

`ming-shell-service` is a session-long single-instance process implemented by
the launch component. It exposes a per-user Unix socket under
`$XDG_RUNTIME_DIR/ming-shell/` for drawer toggle and launch-feedback requests.
The protocol is newline-delimited JSON with a version, action, desktop-file path,
and optional source rectangle. Paths must resolve to an installed `.desktop`
file in an allowlisted application directory.

### Settings

`ming-control-center` becomes a compatibility wrapper for `ming-settings`.
`xfce4-settings-manager` is removed from the default application list and no
longer launched by a visible Ming entry.

The settings backend exposes typed operations for:

- appearance: GTK theme, icon theme, font, cursor size, text scale;
- desktop and Dock: icon scale, Dock size, zoom, auto-hide, position, favorites;
- windows: focus mode, raise delay, placement, title font, compositing profile;
- display: resolution, refresh rate, rotation and scaling through `xrandr` with
  confirmation and automatic rollback;
- input: keyboard layout, repeat rate, pointer speed, touchpad tap/scroll;
- sound: output volume, mute, input volume, and preferred output;
- power: brightness, lid behavior, idle timeout and battery policy;
- notifications: Do Not Disturb, timeout, placement and history size;
- default applications: browser, file manager, terminal and media handler;
- startup: enable/disable user autostart entries without exposing required Ming
  shell services;
- compatibility: compositor profile, software rendering, desktop/Dock repair,
  hardware and driver diagnostics.

Each setter reads back the effective value. A failed write leaves the previous
value in place and produces a Chinese error with the responsible backend and log
path. Controls that are unsupported on the current hardware are disabled and
explain why.

### Status Widget And Notifications

The compact widget retains time, Wi-Fi, Bluetooth, battery, settings, and power.
It adds two horizontal sliders for volume and brightness and a notification
button with an unread count. Volume uses `wpctl`, then `pactl`, then `amixer`.
Brightness uses `brightnessctl` only when a writable backlight device exists;
otherwise the control is hidden.

The notification button opens a right-aligned panel showing the latest 50
entries from the Xfce Notifyd log. Entries show application, summary, body and
time with bounded text. The panel provides "Clear" and a Do Not Disturb switch.
Clear truncates the user-owned log atomically. Do Not Disturb uses the
`xfce4-notifyd` Xfconf channel and reads the effective value back. Missing or
unknown log formats produce an empty state rather than crashing the desktop.

### Application Drawer

The Plank application-library item launches a single-instance drawer. Repeated
clicks toggle it. The drawer occupies the bottom 72 percent of the work area,
has rounded top corners, and animates from below the screen in 180-220 ms using
an ease-out curve. It closes on Escape, clicking the dimmed backdrop, selecting
an application, or losing focus to a launched application.

The drawer contains search, category tabs, a responsive icon grid, recent apps,
and context actions for adding an application to the Android desktop or a desktop
folder. It does not show quick actions that duplicate Settings or Ming Files.
The application-library desktop file remains installed for Plank and search but
is excluded from the Android desktop and physical `~/Desktop` launchers.

### Launch Animation

Desktop tiles and drawer tiles send their source rectangle to `ming-launch`.
Dock launches use a bottom-center source rectangle. The broker immediately shows
a non-interactive launch surface that expands from the source to a bounded
window-like rectangle in 220 ms, then displays a spinner or application icon
while the real process starts. Window detection runs outside the GTK thread.
When a matching window appears, the surface fades in 100 ms and disappears.
The total feedback lifetime is capped; failure produces one notification and a
log entry without launching a duplicate instance.

Animation duration is fixed rather than viewport-scaled. The software-rendering
profile uses position and opacity only. Reduced-motion mode uses a 90 ms fade.
No animation may delay process creation or intercept desktop clicks.

### Ming Files

Ming Files is a GTK4/libadwaita browser using GIO and GVfs. It provides:

- sidebar entries for Home, Desktop, Documents, Downloads, Trash, mounted disks,
  removable media and network locations;
- breadcrumb navigation, back/forward/up, search, list/grid mode and hidden-file
  toggle;
- open, open-with, new folder, rename, copy, cut, paste, trash, restore and
  permanent delete with confirmation;
- asynchronous operations with progress and cancellation;
- GIO mount/unmount/eject and clear error feedback;
- keyboard and touch activation with bounded labels and stable hit areas.

The old `ming-disk-hub` script, desktop entry, Dock item, favorites entry,
`~/所有磁盘`, generated symlinks and bookmark are removed. Existing user-created
files inside `~/所有磁盘` are moved to `~/Documents/所有磁盘-旧文件` before the
generated directory is removed; symlinks and generated README files are deleted.

### GRUB

Live ISO GRUB and installed GRUB share one Ming theme directory containing the
font, colors, background and selection assets. The installed configuration may
not reference a missing theme. Both paths use a black high-contrast text fallback
if graphics or theme loading fails. BIOS and UEFI retain the same menu content
and compatibility entries appropriate to their context; the Live menu contains
installer modes, while the installed menu contains installed-system modes.

### OTA, APT And User Data

APT patch updates remain package-based and run only allowlisted package names
from a validated JSON manifest. Remote patch scripts are not executed unless the
manifest is signed by the embedded Ming release key. Package transactions log
their exact command and result and recover interrupted `dpkg` state before retry.

Major OTA requires one of:

1. a verified separate `/home` partition that the installer will preserve; or
2. a writable non-target disk with enough free space for a real `rsync -aHAX
   --numeric-ids` backup plus ten percent headroom.

For the second path, Ming creates a versioned backup directory, writes a JSON
manifest with machine ID, source, destination, disk UUID, file count, byte count
and completion marker, then verifies the copied tree before staging GRUB. The
GRUB OTA entry carries the backup disk UUID and relative manifest path as kernel
parameters. The installed-identity step mounts that disk, validates the manifest,
restores `/home` into the target, preserves ownership, and writes a persistent
restore log. Missing, incomplete or unverifiable backups block destructive OTA.

The OTA CLI adds a non-destructive `doctor` command and testable backup/restore
functions with overridable source and target paths. The release test never
formats a user disk; destructive installation is tested only in disposable VMs.

## Error Handling And Fallbacks

- All UI subprocess calls are bounded by timeouts and run off the GTK main loop.
- Every long-running operation has a visible busy state and cancellation where
  cancellation is safe.
- Plank remains the Dock; if the drawer fails, its launcher shows a notification
  and records `~/.cache/ming-os/app-drawer.log`.
- If Ming Files fails, its launcher offers a logged Thunar safe fallback instead
  of silently doing nothing.
- If the notification log is unavailable, Do Not Disturb still works.
- Unsupported brightness, battery, touchpad, Wi-Fi or Bluetooth controls are
  hidden or disabled based on detected hardware.
- Software rendering and no-compositor sessions retain opaque, usable windows.

## Verification And Release Gates

Static gates:

- Shell syntax, Python compilation, unit tests, `git diff --check`, generated
  heredoc script syntax and build validation must pass.
- Tests must prove retired Xfce advanced-settings and All Disks entries are absent.
- Tests must cover settings read/write/readback, drawer toggle and slide geometry,
  notification parsing/clear/DND, launch deduplication, file operations against a
  temporary GIO directory, OTA manifest validation and backup/restore round trips.

Runtime gates in the current VirtualBox baseline:

- Application drawer toggles from one Dock item and never appears on the desktop.
- Volume, brightness where supported, notification history, clear and DND work.
- Desktop, drawer and Dock launches each create one process and visible window.
- Ming Files can browse, mount, copy, rename, trash and restore test data; no All
  Disks launcher or generated directory remains.
- Edge has no black frame at 1024x768, 1366x768 or dynamic resize.
- Spark Store either opens a usable window or reports a precise logged dependency
  failure; daemonized success is not misreported.
- Wi-Fi shows an interface and networks when supported hardware is present, or a
  precise PCI/USB ID, driver, rfkill or firmware diagnosis when it is not.
- APT update and a harmless package install/remove transaction complete in the
  built image without leaving locks or broken dpkg state.
- OTA `doctor` passes; a temporary user-data backup/restore round trip preserves
  content, modes and ownership metadata.
- BIOS and UEFI installation complete, the first warm reboot does not show
  SQUASHFS errors or `grub>`, and the installed disk boots after ISO removal.
- Live and installed GRUB use the same visual language and text fallback.

The ISO is not a preview candidate until every static gate and all disposable-VM
runtime gates pass. Real-hardware Wi-Fi and warm-reboot results remain explicitly
model-specific until the adapter IDs and logs are captured.
