# Ming Agent Runtime and DSH Bridge

## Goal

Provide a stable, local interface for a future DeepSeek Harness (DSH) system
agent. Computer-use tasks run in an isolated graphical session by default so
the user's foreground desktop is not moved, focused, or otherwise disturbed.
The current desktop remains available through an explicitly enabled assist
mode only.

## Architecture

`ming-agent-bridge` is a versioned JSON-lines CLI and the reference adapter.
`org.mingos.Agent1` is the optional session-bus facade for the built-in DSH
application. Both adapters call the same unprivileged runtime under
`/usr/lib/ming-os/agent/`; neither accepts arbitrary shell commands.

Each background session owns a per-user `Xvfb` display, DBus session, XDG
runtime directory, and lightweight window manager. Session state lives below
`$XDG_RUNTIME_DIR/ming-os/agent/sessions/<id>/` and is removed when the agent
stops it. The runtime refuses root, cross-user displays, missing session
variables, and requests exceeding action timeouts.

The foreground assist mode is separate and disabled by default. Enabling it
requires a local settings action, reports an active-control indicator, and
supports an immediate stop operation. SSH never grants screen-control access.

## Stable Capabilities

The bridge exposes `capabilities`, `session create|status|stop`,
`screen status|windows|screenshot|activate|click|type|key`, and
`store search|details|inventory|updates|log|install|update|remove|refresh`.
Every response includes `protocol`, `request_id`, `ok`, `state`, and a readable
`message`. Mutating store actions must use the existing
`ming.store.transaction.v1` request schema, provider/source checks, Polkit
authorization, signature verification, and post-install readback. The agent
cannot call APT directly.

`agent-capabilities.json` records protocol version, supported actions,
permission state, and runtime dependencies. It is data, not executable policy.

## Security and Failure Handling

Background input is confined to the agent display. Screen data and logs are
owned by the session user with restrictive permissions. Failures return JSON
state and reason, stop partial sessions, and never silently fall back to the
foreground desktop. Store failures preserve existing journal and rollback
semantics.

## Verification

Add contract tests for session isolation, JSON responses, action allowlists,
foreground opt-in, capability discovery, store query delegation, and
transaction handoff. Build gates must require `Xvfb`, DBus session support,
AT-SPI, `wmctrl`, `xdotool`, and screenshot tooling. Validate the runtime in
the existing Debian WSL2 `fast-test` build and retain the current development
VM.
