# Ming Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Thunar wrapper and duplicate All Disks feature with one GTK4/libadwaita file manager backed by GIO/GVfs.

**Architecture:** Keep filesystem semantics in GIO. Ming Files owns navigation, presentation, progress and error UI while GIO handles enumeration, mounts, copy, move and trash.

**Tech Stack:** Python 3, GTK4, libadwaita, Gio, GVfs, UDisks integration through Gio.VolumeMonitor.

---

### Task 1: File Model

**Files:**
- Create: `assets/ming-files-model.py`
- Create: `tests/test_ming_files.py`

- [ ] Write failing tests against temporary directories for enumeration, hidden files, sorting, breadcrumbs, search, copy, move, rename, trash and cancellation.
- [ ] Run `python -m unittest tests.test_ming_files -v`; expect missing-module failures.
- [ ] Implement async GIO-backed `LocationModel`, `FileOperation` and `VolumeModel` classes with structured progress and errors.
- [ ] Run the model tests; expect all tests to pass.

### Task 2: Ming Files UI

**Files:**
- Create: `assets/ming-files.py`
- Extend: `tests/test_ming_files.py`

- [ ] Add source-contract tests for sidebar locations, breadcrumb controls, list/grid toggle, search, context menu and progress cancellation.
- [ ] Implement the responsive GTK4/libadwaita window with stable toolbar dimensions and bounded file labels.
- [ ] Add keyboard, mouse and touch activation plus open-with, create folder, rename, clipboard, trash, restore and delete confirmation.
- [ ] Add volume mount/unmount/eject actions and clear empty/error states.
- [ ] Compile both Ming Files assets and run tests.

### Task 3: Remove All Disks And Migrate User Data

**Files:**
- Modify: `modules/03_desktop.sh`
- Modify: `assets/ming-phone-desktop.py`
- Modify: `build_onion_os.sh`
- Extend: `tests/test_ming_files.py`

- [ ] Write failing tests proving `ming-disk-hub`, its desktop file, Dock item, favorites entry, bookmark and generated launchers are absent.
- [ ] Implement an idempotent migration that preserves real non-symlink user files under `~/所有磁盘` in `~/Documents/所有磁盘-旧文件` and removes generated links/README entries.
- [ ] Install Ming Files as the default file manager and update MIME/default-app settings.
- [ ] Remove all disk-hub generation and validation markers.
- [ ] Run file tests, desktop regression tests and Shell syntax checks.

