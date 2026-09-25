# Ming Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, built-in agent bridge for DSH with isolated background computer-use sessions and authorized application-store operations.

**Architecture:** A per-user Python runtime owns Xvfb, a private DBus session, and xfwm4 for each agent session. A JSON-lines bridge exposes versioned screen and store capabilities; an optional session-bus service delegates to the same bridge. Store mutations continue through the existing request schema and Polkit path.

**Tech Stack:** Python 3, Xvfb, dbus-daemon, xfwm4, AT-SPI/DBus, wmctrl, xdotool, gnome-screenshot, existing `StoreController` and `ming-store-control` transaction flow.

**Spec:** `docs/superpowers/specs/2026-09-25-ming-agent-runtime-design.md`

## Global Constraints

- Background sessions must never use the user's foreground `DISPLAY`.
- No network listener, root execution, arbitrary shell command, or SSH-to-screen shortcut.
- Every response is one JSON object containing `protocol`, `request_id`, `ok`, `state`, and `message`.
- Store mutations preserve `ming.store.transaction.v1`, provider/source trust, Polkit, signatures, and post-install readback.
- Existing development VM and current SSH defaults remain unchanged.

### Task 1: Runtime Session Broker

**Files:**
- Create: `assets/ming-agent-runtime.py`
- Create: `tests/test_ming_agent_runtime.py`
- Modify: `modules/02_apps.sh`
- Modify: `build_onion_os.sh`

**Interfaces:**
- `ming-agent-runtime create|status|stop [session_id]` prints one JSON object.
- `AgentSessionManager.create() -> dict` creates a per-user Xvfb/DBus/xfwm4 session.
- `AgentSessionManager.status(session_id) -> dict` reads and validates state.
- `AgentSessionManager.stop(session_id) -> dict` terminates only recorded child processes.

- [ ] Write failing tests for package/runtime markers, display isolation, state-file permissions, invalid session IDs, and idempotent stop.
- [ ] Run `python3 -m unittest -q tests.test_ming_agent_runtime` and verify the new symbols/markers fail.
- [ ] Add the Python manager with strict session ID validation, display allocation under `$XDG_RUNTIME_DIR/ming-os/agent/sessions`, `Xvfb -nolisten tcp`, private DBus, xfwm4, child PID readback, and cleanup on partial startup.
- [ ] Add `xvfb`, `dbus-x11`, `at-spi2-core`, and the already-used X11 helpers to the desktop dependency list and release gate; do not add a daemon autostart.
- [ ] Re-run the focused tests and `bash -n modules/02_apps.sh build_onion_os.sh`.
- [ ] Commit: `feat: add isolated agent session runtime`.

### Task 2: JSON Screen Bridge

**Files:**
- Create: `assets/ming-agent-bridge.py`
- Create: `tests/test_ming_agent_bridge.py`
- Modify: `modules/03_desktop.sh`
- Modify: `build_onion_os.sh`

**Interfaces:**
- `ming-agent-bridge capabilities` returns protocol `ming.agent.v1` and capability records.
- `ming-agent-bridge screen status|windows|screenshot|activate|click|type|key --session SESSION`.
- All actions resolve the session state and inject only into its recorded `DISPLAY`/DBus environment.

- [ ] Write failing tests for JSON envelope, command allowlist, current-display rejection, screenshot path ownership, and timeout/error states.
- [ ] Run the focused bridge tests and verify they fail for the missing bridge.
- [ ] Implement JSON-lines dispatch using `wmctrl`, `xprop`, `xdotool`, and `gnome-screenshot`; use AT-SPI queries where available and return a clear fallback state when unavailable.
- [ ] Reject root, missing session state, foreign session paths, arbitrary command arguments, and foreground `DISPLAY` reuse.
- [ ] Deploy the bridge and a read-only `agent-capabilities.json` under `/usr/lib/ming-os/agent/`; add rootfs gate markers.
- [ ] Re-run focused tests, Python compilation, and shell syntax checks.
- [ ] Commit: `feat: add isolated screen control bridge`.

### Task 3: Store Agent Facade

**Files:**
- Modify: `assets/ming-agent-bridge.py`
- Create: `tests/test_ming_agent_store_bridge.py`
- Modify: `assets/ming-store.py` only if a narrow import-safe helper is required
- Modify: `build_onion_os.sh`

**Interfaces:**
- `store search`, `details`, `inventory`, `updates`, and `log` are read-only and return redacted JSON.
- `store install|update|remove|refresh` accepts a validated transaction request and delegates to `StoreController.create_transaction()` plus the existing authorized action.

- [ ] Write failing tests proving read-only queries do not call APT and mutation requests reject missing/invalid `request_id`, provider, source, and app identity.
- [ ] Run the focused tests and verify failure before implementation.
- [ ] Import the existing store controller through a stable adapter; preserve catalog signatures, Spark checks, architecture checks, journal schema, and installed-state readback.
- [ ] Return progress and final transaction states without exposing credentials, local paths outside the request/result area, or raw command lines.
- [ ] Re-run store regression tests plus the new bridge tests.
- [ ] Commit: `feat: expose store capabilities to agent bridge`.

### Task 4: Session-Bus DSH Facade and Policy

**Files:**
- Create: `assets/ming-agent-service.py`
- Create: `config/systemd/user/ming-agent.service`
- Create: `tests/test_ming_agent_service.py`
- Modify: `modules/08_settings_hub.sh`
- Modify: `build_onion_os.sh`

**Interfaces:**
- Session-bus name: `org.mingos.Agent1`; object path: `/org/mingos/Agent1`.
- Methods: `Capabilities()`, `CreateSession()`, `SessionStatus()`, `StopSession()`, and `DispatchJson()`.
- The service is user-scoped and disabled by default; the bridge remains usable as a one-shot CLI.

- [ ] Write failing tests for service metadata, disabled-by-default policy, same-user ownership, and foreground assist refusal.
- [ ] Implement a thin DBus facade that delegates to the bridge and never runs as root.
- [ ] Add a settings status row for agent availability and isolated-session state; do not expose a foreground-input toggle until a separate explicit UI flow exists.
- [ ] Re-run service and settings tests, then compile every asset.
- [ ] Commit: `feat: add DSH session bus facade`.

### Task 5: Integration and Remote Verification

**Files:**
- Modify: `tests/test_release_gate.py`
- Modify: `tests/test_runtime_dependencies.py`
- Add: `docs/reports/ming-agent-runtime-validation-2026-09-25.md`

- [ ] Run focused tests for runtime, bridge, store, security, and desktop contracts.
- [ ] Run `PYTHONPYCACHEPREFIX=/tmp/ming-os-pycache python3 -m py_compile assets/*.py`.
- [ ] Run `bash -n modules/01_base.sh modules/02_apps.sh modules/03_desktop.sh modules/05_security_tools.sh modules/08_settings_hub.sh build_onion_os.sh` and `git diff --check`.
- [ ] Synchronize the committed tree to the existing Windows worktree using a Git bundle; never edit WSL `.git` metadata.
- [ ] Run remote `fast-test`, record source commit, build ID, ISO path, SHA256, exit code, and process state.
- [ ] Verify the retained development VM is still running and do not replace its mounted ISO.
- [ ] Commit the validation report: `docs: record agent runtime validation`.
