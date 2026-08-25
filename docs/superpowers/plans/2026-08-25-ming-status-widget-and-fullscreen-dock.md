# Ming Mint 小组件与全屏 Dock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将已确认的短胶囊小组件和全屏 Dock 层级策略落到 Ming OS 运行时，并用自动化契约验证行为。

**Architecture:** `assets/ming-phone-desktop.py` 负责小组件状态、GTK 控件、设备电池探测和 Win 键切换；`assets/ming-app-drawer.py` 与 `modules/03_desktop.sh` 负责抽屉/Dock 状态协作。用小型纯函数承载设备能力和快捷键判断，保持现有会话协调器与 Plank 单实例模型不变。

**Tech Stack:** Python 3、GTK3/PyGObject、GLib、X11 EWMH、Bash、Python unittest。

---

### Task 1: 小组件红测

**Files:**
- Modify: `tests/test_desktop_regressions.py`
- Modify: `tests/test_device_control.py`

- [ ] Add tests for short capsule field order, `ming-widget-mark.svg`, laptop-only battery rendering, Win toggle binding, and reduced-motion behavior.
- [ ] Add tests for collapsed geometry: revealer hidden, expanded child zero height, and no battery placeholder on desktops.
- [ ] Run `python -m unittest tests.test_desktop_regressions tests.test_device_control` and verify the new assertions fail against the current implementation.

### Task 2: 小组件最小实现

**Files:**
- Modify: `assets/ming-phone-desktop.py`
- Modify: `modules/03_desktop.sh`

- [ ] Add pure helpers for laptop/power-device detection, battery summary formatting, and Win-key toggle eligibility.
- [ ] Replace the compact row with time/date/status icons and the Ming SVG-backed expand button; preserve existing control widgets and resource sampler.
- [ ] Bind the desktop window key-press path to Win-key toggling without intercepting text-entry windows.
- [ ] Keep compact mode at zero expanded height and maintain the 8px top-gap assertion.
- [ ] Run the focused tests from Task 1 and verify green.

### Task 3: Dock/fullscreen red test

**Files:**
- Modify: `tests/test_dock_lifecycle.py`
- Modify: `tests/test_desktop_regressions.py`

- [ ] Add assertions for a fullscreen/drawer state hook, Dock hide/lower behavior, no bottom workarea reservation, and restore after state exit.
- [ ] Run the focused tests and verify they fail before implementation.

### Task 4: Dock/fullscreen implementation

**Files:**
- Modify: `assets/ming-app-drawer.py`
- Modify: `modules/03_desktop.sh`

- [ ] Add a single state helper used by drawer/session coordination to detect fullscreen and drawer-open states.
- [ ] Lower or hide Plank during fullscreen/drawer presentation, without killing the unique Plank process; restore it after the state clears.
- [ ] Keep legacy centered geometry, hover zoom, bottom offset and low-resource policy unchanged outside fullscreen.
- [ ] Run focused Dock/desktop tests and verify green.

### Task 5: Full verification and commit

**Files:**
- Modify only files covered by Tasks 1-4.

- [ ] Run `python -m unittest discover -s tests`.
- [ ] Run `python -m py_compile` for modified Python files.
- [ ] Run `bash -n` for modified shell files.
- [ ] Run `git diff --check`.
- [ ] Commit the implementation with a focused message; do not build ISO in this round.
