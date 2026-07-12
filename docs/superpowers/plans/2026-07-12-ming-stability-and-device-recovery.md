# Ming OS Stability And Device Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans task-by-task. All production changes follow a red-green-refactor loop. This repository is already dirty; do not reset, stash, or commit unrelated work.

**Goal:** Make the installed desktop recoverable on Intel hardware, restore reliable window controls and network time sync, present display controls in user language, and add the selected compact status-widget state.

**Architecture:** Retain Linux 6.12, i915, Xfwm and Picom. Replace Ming's forced Intel DDX with Xorg's i915/KMS modesetting default; add narrowly scoped session diagnostics/recovery instead of killing user applications. Put display, window-manager and time-sync state behind small, JSON-capable helpers so GTK only consumes structured results.

**Tech stack:** Bash/systemd/NetworkManager dispatcher, X11 xrandr/xprop/wmctrl/Xfwm, GTK3 desktop, GTK4/libadwaita settings, Python unittest.

---

### Task 1: Lock the regressions in tests

**Files:**
- Create: `tests/test_stability_recovery.py`
- Modify: `tests/test_hardware_status.py`
- Modify: `tests/test_desktop_regressions.py`

- [ ] **Step 1: Write failing contract tests for the known failures.**

```python
def test_intel_xorg_setup_never_forces_the_legacy_ddx():
    self.assertNotIn('Driver      "intel"', BASE)
    self.assertNotIn('AccelMethod" "sna', BASE)

def test_earlyoom_does_not_prefer_wps():
    self.assertNotIn('|wps|', BASE)

def test_window_health_reports_xfwm_and_ewmh_and_repair_preserves_apps():
    for marker in ('ming-window-control', '_NET_SUPPORTING_WM_CHECK',
                   '_NET_CLOSE_WINDOW', 'xfwm4 --replace', 'window_manager'):
        self.assertIn(marker, DESKTOP)
    self.assertNotIn('pkill -x wps', DESKTOP)

def test_time_sync_is_retried_by_networkmanager_without_blocking_boot():
    for marker in ('ming-time-sync', 'NetworkManager/dispatcher.d',
                   'dhcp4-change', 'connectivity-change', 'NTPSynchronized'):
        self.assertIn(marker, BASE)
```

- [ ] **Step 2: Add display/widget contract tests.**

```python
def test_display_uses_human_labels_and_confirmed_xrandr_rollback():
    for marker in ('100% 标准', '1920 × 1080', 'display apply',
                   'display confirm', 'display rollback'):
        self.assertIn(marker, SETTINGS_SOURCE)

def test_status_widget_persists_compact_time_date_arrow_state():
    for marker in ('status-widget.json', 'collapsed', 'Gtk.Revealer',
                   '收起', '展开'):
        self.assertIn(marker, PHONE_SOURCE)
```

- [ ] **Step 3: Run only the new test module and verify it fails for missing behavior.**

Run: `PYTHONPYCACHEPREFIX=$env:TEMP\ming-stability-pycache python -m unittest tests.test_stability_recovery -v`

Expected: failures naming the absent helpers/contracts, not import errors.

### Task 2: Restore safe Intel graphics defaults and truthful GPU status

**Files:**
- Modify: `modules/01_base.sh`
- Modify: `modules/02_apps.sh`
- Modify: `assets/ming-hardware-status.py`
- Modify: `modules/03_desktop.sh`
- Modify: `build_onion_os.sh`
- Modify: `tests/test_hardware_status.py`

- [ ] **Step 1: Add failing unit tests for `xorg_backend`, `legacy_intel_config`, `render_access`, and VA-API permission failures.**

```python
def test_graphics_status_marks_legacy_intel_ddx_as_attention(self):
    status = self.service.graphics_status()
    self.assertEqual('attention', status['state'])
    self.assertEqual('legacy-intel-ddx', status['xorg_backend'])
    self.assertTrue(status['legacy_intel_config'])
```

- [ ] **Step 2: Replace the generated all-Intel `20-intel.conf` path.**

`ming-intel-xorg-setup` must become a migration/diagnostic helper: disable only a file carrying the Ming-generated header, preserve any user-owned configuration, and never write `Driver "intel"`, SNA, TearFree, DRI or TripleBuffer. Disable the old service for new images and on upgrade; let Xorg select `modesetting` over i915/KMS.

- [ ] **Step 3: Make the modesetting path and render access mandatory.**

Install/verify `xserver-xorg-video-modesetting`; do not require `xserver-xorg-video-intel`. Add `render` beside `video` to the Live user and every Calamares user-group definition. Extend the rootfs gate to require modesetting, verify `render` membership, and validate VA-API from the non-root desktop user rather than merely finding libraries.

- [ ] **Step 4: Expand the graphics JSON and cards.**

Report kernel driver, Xorg backend, generated-legacy-config residue, render-node access, VA-API result/error, and Edge eligibility separately. A virtual machine or HD 620 without AV1 remains attention/capability information, not a driver failure.

- [ ] **Step 5: Verify this task.**

Run: `PYTHONPYCACHEPREFIX=$env:TEMP\ming-stability-pycache python -m unittest tests.test_hardware_status tests.test_radio_contracts -v`

Expected: all graphics and firmware contracts pass.

### Task 3: Add recoverable window-manager health and conservative composition

**Files:**
- Modify: `modules/01_base.sh`
- Modify: `modules/03_desktop.sh`
- Modify: `assets/ming-settings.py`
- Modify: `build_onion_os.sh`
- Modify: `tests/test_stability_recovery.py`
- Modify: `tests/test_dock_lifecycle.py`

- [ ] **Step 1: Keep the Task 1 window-health tests red, then implement `/usr/local/bin/ming-window-control`.**

It exposes `status --json`, `repair`, `focus --window-id`, `maximize --window-id`, `restore --window-id`, and `close --window-id`. `status` reports Xfwm process, `wmctrl -m`, root EWMH support, active window, Picom process/profile and a safe log path. Window IDs must be hexadecimal X11 IDs; reject all other input.

- [ ] **Step 2: Integrate a non-destructive session watchdog.**

`ming-desktop-healthcheck --json` gains `window_manager`. Its repair path invokes `ming-window-control repair`; repair may run `xfwm4 --replace` only when Xfwm/EWMH is missing, waits for EWMH recovery, rate-limits failures, and never kills applications. Add an autostart session watchdog that checks every 10 seconds, requires three failed observations before one repair, and logs to `~/.cache/ming-os/window-manager.log`.

- [ ] **Step 3: Stabilize low-end composition.**

Remove `wps` from `earlyoom --prefer`; retain desktop-service protection. Set Picom `unredir-if-possible=false` in ordinary and low-memory profiles, retaining opaque normal windows and the existing software fallback. Do not disable i915 GPU acceleration globally.

- [ ] **Step 4: Surface repair safely in Settings.**

Add “修复窗口控制” under compatibility/diagnostics. It runs `ming-window-control repair` off the GTK thread and displays a high-contrast result plus the log path; it must not close the current document or application.

- [ ] **Step 5: Verify this task.**

Run: `PYTHONPYCACHEPREFIX=$env:TEMP\ming-stability-pycache python -m unittest tests.test_stability_recovery tests.test_dock_lifecycle tests.test_settings_backend -v`

Expected: repair is absent on healthy Xfwm, exactly one replacement is attempted after a confirmed failure, and no app-kill command is present.

### Task 4: Make time synchronization event-driven and observable

**Files:**
- Modify: `modules/01_base.sh`
- Modify: `assets/ming-settings.py`
- Modify: `build_onion_os.sh`
- Modify: `tests/test_stability_recovery.py`

- [ ] **Step 1: Add failing tests for a locked, bounded `ming-time-sync` flow.**

```python
def test_time_sync_checks_network_then_reads_ntp_synchronization():
    self.assertIn('flock', TIME_SYNC)
    self.assertIn('nm-online -q -t 12', TIME_SYNC)
    self.assertIn('timedatectl show -p NTPSynchronized --value', TIME_SYNC)
```

- [ ] **Step 2: Implement one time-sync helper and one NetworkManager dispatcher.**

The helper holds a run lock, refuses to restart NTP when `nm-online` fails, enables NTP, restarts `systemd-timesyncd`, polls for at most 45 seconds, and writes `/var/log/ming-time-sync.log`. The dispatcher starts the helper non-blockingly on `up`, `dhcp4-change`, `dhcp6-change`, and `connectivity-change`. Retire the conflicting 60-second/5-second wait-online overrides; a boot timer may retry after login but must not gate graphical startup.

- [ ] **Step 3: Add a small Settings status row.**

Show “已自动校时”, “等待网络校时”, or “校时服务异常”; retry is explicit and non-blocking. Never change the selected timezone.

- [ ] **Step 4: Verify this task.**

Run: `PYTHONPYCACHEPREFIX=$env:TEMP\ming-stability-pycache python -m unittest tests.test_stability_recovery -v`

Expected: offline calls do not restart timesyncd; repeated dispatcher events share one lock; supported events lead to an observable NTP status.

### Task 5: Replace misleading display UI and implement widget compact state A

**Files:**
- Create: `assets/ming-display-control.py`
- Modify: `assets/ming-settings.py`
- Modify: `modules/03_desktop.sh`
- Modify: `assets/ming-phone-desktop.py`
- Modify: `build_onion_os.sh`
- Modify: `tests/test_stability_recovery.py`
- Modify: `tests/test_device_control.py`

- [ ] **Step 1: Add failing pure-Python tests for xrandr parsing, staged apply, confirm and rollback.**

```python
def test_mode_label_has_pixels_and_hz_not_a_scaling_factor():
    self.assertEqual('1920 × 1080 · 60 Hz', mode_label('1920x1080', '60.00'))

def test_unconfirmed_display_change_restores_the_snapshot():
    token = control.apply('eDP-1', '1280x720', '60.00', 'normal')
    self.assertTrue(control.rollback(token)['ok'])
```

- [ ] **Step 2: Add `ming-display-control`.**

It offers `status --json`, `apply --output --mode --rate --rotation`, `confirm TOKEN`, and `rollback TOKEN`. It accepts only an output/mode/rate pair returned by its current `xrandr --query` snapshot, saves a private staged snapshot, and starts a 15-second rollback timer. Confirm cancels the timer only after readback matches. A failed apply rolls back immediately.

- [ ] **Step 3: Build a Ming display page, not a native Xfce bridge.**

Visible entries launch `ming-control-center --page display`, never `xfce4-display-settings`. Place real screen resolution/refresh/rotation above a separate “界面大小” selector: `100% 标准`, `125% 较大`, `150% 很大`, `175% 超大`, `200% 特大`. `ming-scale` may set first-login defaults only; repairs must preserve an explicit user scale preference.

- [ ] **Step 4: Implement the selected widget A state.**

Persist only `{"collapsed": bool}` atomically in `$HOME/.config/ming-os/status-widget.json`. A header pill toggles a `Gtk.Revealer`; compact mode contains only `HH:MM | 周x MM/DD | 展开箭头`, occupies 54px, stays top-right on resize, and continues clock refresh. Expanded mode preserves all existing controls. Missing/corrupt state defaults to expanded and no settings/layout file is overwritten.

- [ ] **Step 5: Verify this task.**

Run: `PYTHONPYCACHEPREFIX=$env:TEMP\ming-stability-pycache python -m unittest tests.test_stability_recovery tests.test_device_control tests.test_settings_backend -v`

Expected: no `1.25` value is labelled resolution, display rollback is deterministic, and compact widget state survives reload.

### Task 6: Release gates and proportional verification

**Files:**
- Modify: `build_onion_os.sh`
- Modify: `tests/test_release_gate.py`

- [ ] **Step 1: Add failing gate tests.**

The gate must require modesetting and render access, `ming-window-control`, `ming-time-sync`, `ming-display-control`, the dispatcher/unit files, and the compact widget source.

- [ ] **Step 2: Implement the rootfs checks.**

The release validator checks files, executable modes, unit syntax, no forced `Driver "intel"`, no WPS earlyoom preference, and generated-script `bash -n`/Python compilation. It does not claim a real Wi-Fi, Bluetooth or GPU regression without target hardware.

- [ ] **Step 3: Run complete static verification.**

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-stability-final-pycache'
python -m unittest discover -s tests -v
bash -n build_onion_os.sh resume_build.sh modules/*.sh
python -m py_compile assets/ming-phone-desktop.py assets/ming-settings.py assets/ming-display-control.py assets/ming-hardware-status.py
git diff --check
```

Expected: all tests pass, generated scripts parse, Python compiles, and the diff has no whitespace errors.

- [ ] **Step 4: Defer ISO build and hardware claims.**

Do not build in this task unless explicitly requested. Before calling the result a preview ISO, run an install/reboot loop on Mi Pad 2 and i5-7200U, capture Xorg/DRM logs, verify normal user VA-API, WPS/Quark window controls, NTP after reconnect, and the A widget state.
