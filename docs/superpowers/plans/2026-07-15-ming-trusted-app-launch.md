# Ming Trusted Application Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make Spark-installed DEB applications open consistently from the Ming desktop, application drawer, and Dock without allowing arbitrary shell desktop entries.

**Architecture:** Normal desktop entries retain strict Exec parsing and shell=False execution. Only the launch broker may activate a package-owned, protected system desktop file through GIO. The drawer, phone desktop, and package installer use that same broker contract. The legacy Xfce application library is removed so it cannot overwrite the drawer or bypass the policy.

**Tech Stack:** Python 3, GTK3/GIO, dpkg-query, Bash, unittest.

---

## File Structure

- Modify: assets/ming-shell-common.py - static protected-system-entry check and broker fallback argv.
- Modify: assets/ming-launch.py - internal launch mode, dpkg ownership verifier, GIO activation, result events.
- Modify: assets/ming-app-drawer.py and assets/ming-phone-desktop.py - broker-only fallback.
- Modify: assets/ming-package-installer.py - launch-ready result classification.
- Modify: modules/03_desktop.sh and build_onion_os.sh - one final app-library wrapper and rootfs gate.
- Modify: tests/test_app_drawer.py, tests/test_launch_results.py, tests/test_package_installer.py, tests/test_desktop_regressions.py, tests/test_release_gate.py.
- Create: tests/test_trusted_desktop_activation.py - trust-boundary and broker tests.

### Task 1: Define and Test the Protected Desktop Boundary

**Files:**
- Create: tests/test_trusted_desktop_activation.py
- Modify: assets/ming-shell-common.py
- Modify: tests/test_app_drawer.py

- [ ] **Step 1: Write failing policy tests**

~~~python
def test_package_owned_system_wrapper_gets_internal_activation_mode(self):
    request = self.launch.request_from_desktop_file(
        self.system_wrapper,
        allowed_dirs=(self.system_dir,),
        verifier=self.installed_verifier,
    )
    self.assertEqual("desktop_app_info", request.mode)

def test_user_wrapper_is_not_an_activation_candidate(self):
    with self.assertRaisesRegex(ValueError, "shell"):
        self.launch.request_from_desktop_file(
            self.user_wrapper, allowed_dirs=(self.user_dir,),
            verifier=self.installed_verifier,
        )

def test_ipc_cannot_select_the_activation_mode(self):
    with self.assertRaises(ValueError):
        self.launch.request_from_message({
            "version": 1, "action": "launch", "desktop_file": str(self.system_wrapper),
            "source": "drawer", "rect": None, "mode": "desktop_app_info",
        })
~~~

Fixture coverage must include symlink, group writable, other writable,
unowned, ambiguous owner, non-ii owner, and same-name user override cases.

- [ ] **Step 2: Confirm the new test fails**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-policy-pycache'
python -m unittest tests.test_trusted_desktop_activation -v
~~~

Expected: FAIL because no protected launcher policy exists.

- [ ] **Step 3: Implement static candidate validation without weakening parsing**

Add this shape to assets/ming-shell-common.py:

~~~python
def is_system_desktop_activation_candidate(path, system_dir=pathlib.Path("/usr/share/applications")):
    candidate = pathlib.Path(path)
    try:
        resolved = candidate.resolve(strict=True)
        details = resolved.stat()
    except OSError:
        return False
    return (
        resolved.parent == system_dir.resolve()
        and resolved.suffix == ".desktop"
        and stat.S_ISREG(details.st_mode)
        and details.st_uid == 0
        and not (details.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        and not candidate.is_symlink()
    )
~~~

Keep desktop_exec_argv unchanged: ordinary shell syntax and shell wrappers
remain invalid. The helper may identify only a potential broker exception; it
must not execute anything or call GIO.

- [ ] **Step 4: Add a strict catalog regression**

~~~python
def test_user_shell_wrapper_stays_nonlaunchable_in_catalog(self):
    apps = self.drawer.discover_apps((self.user_applications,))
    self.assertEqual((), apps[0].argv)
    self.assertIn("shell", apps[0].diagnostic)
~~~

- [ ] **Step 5: Run the policy checks**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-policy-pycache'
python -m unittest tests.test_trusted_desktop_activation tests.test_app_drawer -v
~~~

Expected: current failure only in the unimplemented broker mode.

- [ ] **Step 6: Commit the policy tests and helper**

~~~bash
git add assets/ming-shell-common.py tests/test_trusted_desktop_activation.py tests/test_app_drawer.py
git commit -m "test: define trusted desktop activation boundary"
~~~

### Task 2: Implement Broker-Only GIO Activation

**Files:**
- Modify: assets/ming-launch.py
- Modify: tests/test_trusted_desktop_activation.py
- Modify: tests/test_launch_results.py

- [ ] **Step 1: Write failing broker tests**

~~~python
def test_verified_wrapper_uses_gio_and_never_spawn(self):
    calls, spawned = [], []
    request = self.launch.LaunchRequest(
        (), desktop_file="/usr/share/applications/store.desktop",
        mode="desktop_app_info",
    )
    broker = self.launch.LaunchBroker(
        desktop_activator=lambda path: calls.append(path) or True,
        trusted_verifier=lambda path: True,
        spawn=lambda argv: spawned.append(argv),
        animate=lambda *_: None,
    )
    self.assertTrue(broker.launch(request))
    self.assertEqual([request.desktop_file], calls)
    self.assertEqual([], spawned)

def test_failed_gio_activation_records_failure_and_allows_retry(self):
    events = []
    broker = self.launch.LaunchBroker(
        desktop_activator=lambda _: (_ for _ in ()).throw(RuntimeError("wrapper exited")),
        trusted_verifier=lambda _: True,
        record_event=lambda _, state, detail="": events.append((state, str(detail))),
        report_error=lambda *_: None,
    )
    self.assertFalse(broker.launch(self.wrapper_request))
    self.assertEqual("activation_failed", events[-1][0])
~~~

- [ ] **Step 2: Verify failures**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-broker-pycache'
python -m unittest tests.test_trusted_desktop_activation tests.test_launch_results -v
~~~

Expected: FAIL because LaunchRequest requires argv and the broker cannot use GIO.

- [ ] **Step 3: Add an internal request mode and exact dpkg verifier**

LaunchRequest accepts only mode argv or desktop_app_info. argv mode still
requires a non-empty argv; desktop_app_info requires an empty argv and a
canonical desktop_file. Its IPC message does not expose mode.

Implement:

~~~python
def verify_package_owned_desktop(path, runner, system_dir):
    if not COMMON.is_system_desktop_activation_candidate(path, system_dir):
        return False, "not a protected system desktop entry"
    owner_result = runner(["dpkg-query", "-S", "--", str(path)], timeout=2)
    package = parse_exact_dpkg_owner(owner_result.stdout, path)
    status_result = runner(
        ["dpkg-query", "-W", "-f=<status-abbrev-format>", package], timeout=2,
    )
    return status_result.returncode == 0 and status_result.stdout.strip().startswith("ii"), package
~~~

parse_exact_dpkg_owner must reject zero, multiple, comma-separated, or
canonical-path-mismatched owners. The concrete production format string is
the dpkg Status-Abbrev field; retain it in the implementation, not the IPC.

- [ ] **Step 4: Implement the GIO path**

~~~python
def activate_desktop_app_info(path):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
    if app_info is None or not app_info.launch([], None):
        raise RuntimeError("desktop entry cannot be activated")
    return True
~~~

LaunchBroker.launch rechecks the verifier immediately before this call. It
records activated on success and activation_failed on failure. argv launch
continues to use subprocess.Popen(list(argv), shell=False). The existing
window observation remains bounded; there is no GIO-to-shell fallback.

- [ ] **Step 5: Run focused tests**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-broker-pycache'
python -m unittest tests.test_trusted_desktop_activation tests.test_launch_results tests.test_app_drawer -v
~~~

Expected: PASS, including the existing rejection of a temporary-directory shell wrapper.

- [ ] **Step 6: Commit the broker**

~~~bash
git add assets/ming-launch.py tests/test_trusted_desktop_activation.py tests/test_launch_results.py tests/test_app_drawer.py
git commit -m "feat: activate verified package desktop wrappers"
~~~

### Task 3: Give All Surfaces One Safe Fallback

**Files:**
- Modify: assets/ming-shell-common.py
- Modify: assets/ming-app-drawer.py
- Modify: assets/ming-phone-desktop.py
- Modify: tests/test_app_drawer.py
- Modify: tests/test_desktop_regressions.py

- [ ] **Step 1: Write failing fallback tests**

~~~python
def test_drawer_fallback_calls_one_shot_broker(self):
    calls = []
    self.drawer.launch_desktop_file(
        self.wrapper_path, sender=lambda *_: False,
        fallback=lambda argv: calls.append(tuple(argv)),
    )
    self.assertEqual(
        [("ming-launch", "--desktop-file", str(self.wrapper_path), "--source", "drawer")],
        calls,
    )

def test_phone_desktop_has_no_direct_entry_argv_fallback(self):
    self.assertNotIn("subprocess.Popen(list(entry.argv)", self.phone_launch_item_source)
~~~

- [ ] **Step 2: Confirm the fallback test fails**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-surfaces-pycache'
python -m unittest tests.test_app_drawer tests.test_desktop_regressions -v
~~~

Expected: FAIL because both surfaces can still spawn entry argv directly.

- [ ] **Step 3: Add the common fallback argv and refactor callers**

~~~python
def broker_fallback_argv(desktop_file, source):
    if source not in {"desktop", "drawer", "dock"}:
        raise ValueError("unsupported launch source")
    return ("ming-launch", "--desktop-file", str(desktop_file), "--source", source)
~~~

If the socket is unavailable, the drawer and phone desktop invoke only this
argv using shell=False. They do not parse, inspect, or run a wrapper directly.
Both retain the existing Chinese failure dialog and log desktop_file plus
source. Ensure the desktop_app_info request uses the existing 0.6-second
dedup key based on canonical desktop_file.

- [ ] **Step 4: Run cross-surface tests**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-surfaces-pycache'
python -m unittest tests.test_app_drawer tests.test_desktop_regressions tests.test_launch_results -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit the caller unification**

~~~bash
git add assets/ming-shell-common.py assets/ming-app-drawer.py assets/ming-phone-desktop.py tests/test_app_drawer.py tests/test_desktop_regressions.py
git commit -m "fix: unify desktop and drawer launch fallback"
~~~

### Task 4: Report Installation State and Refresh the Catalog

**Files:**
- Modify: assets/ming-package-installer.py
- Modify: modules/03_desktop.sh
- Modify: tests/test_package_installer.py

- [ ] **Step 1: Write failing package outcome tests**

~~~python
def test_package_wrapper_is_desktop_activatable(self):
    record = self.installer._launcher_record(self.wrapper, package="store-app")
    self.assertTrue(record["ok"])
    self.assertEqual("desktop_app_info", record["activation"])

def test_dpkg_success_with_missing_program_is_not_launch_ready(self):
    result = self.installer.install(self.package)
    self.assertTrue(result["ok"])
    self.assertFalse(result["launch_ready"])
    self.assertEqual("installed_with_launch_problem", result["state"])
~~~

- [ ] **Step 2: Verify the ambiguous-success behavior fails the new test**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-package-launch-pycache'
python -m unittest tests.test_package_installer -v
~~~

Expected: FAIL because current output is installed_with_launch_warning without launch_ready.

- [ ] **Step 3: Implement classified result fields**

Use this result contract:

~~~python
{
    "ok": True,
    "launch_ready": False,
    "state": "installed_with_launch_problem",
    "launchers": [],
    "launcher_warnings": [],
}
~~~

A protected package-owned wrapper gets activation desktop_app_info. Direct
entries still check executable permission and ldd output. A package with no
visible application launcher is launch_ready true because it may be a
command-line package.

Update ming-package-install-gui in modules/03_desktop.sh to parse
launch_ready after pkexec JSON. When false, show a Chinese warning with
/var/log/ming-package-installer.log and retain a repair action. Always run
the existing bounded user-session ming-phone-desktop --sync after verified
installation so the desktop and drawer rescan.

- [ ] **Step 4: Run installer tests**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-package-launch-pycache'
python -m unittest tests.test_package_installer -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit package feedback**

~~~bash
git add assets/ming-package-installer.py modules/03_desktop.sh tests/test_package_installer.py
git commit -m "fix: report unusable package launchers"
~~~

### Task 5: Remove the Late Legacy Library Override

**Files:**
- Modify: modules/03_desktop.sh
- Modify: build_onion_os.sh
- Modify: tests/test_desktop_regressions.py
- Modify: tests/test_release_gate.py

- [ ] **Step 1: Write failing final-wrapper contracts**

~~~python
def test_final_app_library_delegates_to_drawer_only(self):
    wrapper = extract_last_heredoc(self.desktop, "/usr/local/bin/ming-app-library")
    self.assertIn("exec /usr/local/bin/ming-app-drawer --toggle", wrapper)
    self.assertNotIn("Gio.DesktopAppInfo", wrapper)
    self.assertNotIn("shell=True", wrapper)
~~~

- [ ] **Step 2: Confirm the old override is detected**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-library-gate-pycache'
python -m unittest tests.test_desktop_regressions tests.test_release_gate -v
~~~

Expected: FAIL because configure_xfce_panel overwrites the drawer wrapper.

- [ ] **Step 3: Delete only the APPLIB heredoc**

Remove the APPLIB heredoc and chmod block from configure_xfce_panel while
preserving its unrelated status-center and input setup. The only deployed
ming-app-library is:

~~~bash
#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/bin/ming-app-drawer --toggle "$@"
~~~

Add a build_onion_os.sh rootfs check that requires this exec line and rejects
Gio.DesktopAppInfo or shell=True in that final wrapper.

- [ ] **Step 4: Run source checks**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-library-gate-pycache'
python -m unittest tests.test_desktop_regressions tests.test_release_gate tests.test_app_drawer tests.test_launch_results -v
bash -n modules/03_desktop.sh
python -m py_compile assets/ming-launch.py assets/ming-shell-common.py assets/ming-app-drawer.py assets/ming-phone-desktop.py assets/ming-package-installer.py
git diff --check
~~~

Expected: all commands exit 0.

- [ ] **Step 5: Commit the final library repair**

~~~bash
git add modules/03_desktop.sh build_onion_os.sh tests/test_desktop_regressions.py tests/test_release_gate.py
git commit -m "fix: keep app drawer as the only app library"
~~~

### Task 6: Verify the Complete App Launch Flow

**Files:**
- Modify only a file proven faulty by these checks.

- [ ] **Step 1: Run all focused tests**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-full-pycache'
python -m unittest tests.test_trusted_desktop_activation tests.test_app_drawer tests.test_launch_results tests.test_package_installer tests.test_desktop_regressions tests.test_release_gate -v
~~~

Expected: PASS.

- [ ] **Step 2: Run project-level static validation**

Run:

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-launch-full-pycache'
python -m unittest discover -s tests -v
bash -n build_onion_os.sh resume_build.sh modules/*.sh
python -m py_compile assets/*.py tests/*.py
git diff --check
~~~

Expected: all commands exit 0.

- [ ] **Step 3: Record the completed validation**

~~~bash
git status --short
git diff --check
~~~

Expected: no unexpected generated files or whitespace errors. The preceding
tasks already commit every concrete correction. Do not create an empty commit,
build, or publish an ISO in this plan.
