# Ming Release Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the redesigned shell and existing critical workflows work before producing a preview ISO.

**Architecture:** Combine static contracts, disposable filesystem tests, generated-script checks, one full ISO build and BIOS/UEFI VirtualBox runs. Destructive tests use disposable VMs only.

**Tech Stack:** unittest, Bash, xorriso, VirtualBox VBoxManage, wmctrl/xdotool screenshots, nmcli, APT, rsync.

---

### Task 1: Static Release Gate

**Files:**
- Create: `tests/test_release_gate.py`
- Modify: `build_onion_os.sh`

- [ ] Add checks covering retired UI, generated assets, OTA backup, GRUB theme, Edge compositor exclusions, Spark wrapper, firmware packages and desktop activation markers.
- [ ] Run all Python tests, Python compilation, Shell syntax and `git diff --check`.

### Task 2: Runtime Diagnostic Script

**Files:**
- Create: `scratch/ming-release-smoke.sh`

- [ ] Implement read-only checks for APT/dpkg health, OTA doctor, Edge flags/window geometry, Spark process/window/log, NetworkManager/rfkill/firmware, desktop and drawer processes, Plank, notifications and Ming Files.
- [ ] Add optional `--exercise-apt` using a harmless install/remove transaction and `--exercise-files` using a temporary directory.
- [ ] Validate script syntax and run it in the hot-test VM.

### Task 3: Full Build And Boot Matrix

**Files:**
- Modify only when a failing gate identifies a defect.

- [ ] Run `./resume_build.sh` once implementation and static gates pass.
- [ ] Validate El Torito BIOS/UEFI entries with xorriso.
- [ ] Install in disposable VirtualBox BIOS and UEFI VMs, remove ISO, warm reboot and cold reboot.
- [ ] Capture GRUB, desktop, drawer, Settings, Ming Files, Edge and Spark screenshots at 1024x768 and 1366x768.
- [ ] Run the smoke script and record exact failures.
- [ ] Fix failures with new regression tests, then repeat affected gates.

