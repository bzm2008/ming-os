# Ming OS Trusted App Launch and Transactional OTA Design

## Status

Approved design for the next implementation cycle. This document covers two
independent reliability problems that share a release boundary:

1. Third-party applications installed through Spark Store can install
   successfully but fail to open from the desktop or application drawer.
2. A normal single-partition installation cannot safely use the existing
   ISO-and-Calamares major OTA path without external preservation storage.

The implementation must not weaken the existing recovery ISO safety checks.

## Goals

- Make one installed application behave identically from the Ming desktop,
  application drawer, and Dock.
- Support package-maintained desktop wrappers used by third-party DEB
  applications without allowing arbitrary shell launchers.
- Report launch failure as a concrete user-visible result rather than a
  silent click.
- Enable a supported Ming OS 26.3.2 user to make one manual installation of
  an official signed bootstrap package, after which compatible major updates
  can complete on a single root partition without an external disk.
- Preserve /home and machine-specific state during transactional updates.
- Make interrupted, invalid, or unhealthy updates automatically roll back.

## Non-Goals

- Do not execute user-created desktop files through a shell.
- Do not automatically repartition, resize, format, or run Calamares for a
  transactional update.
- Do not claim protection from physical disk failure; same-disk rollback is
  a software-update recovery mechanism, not an external backup replacement.
- Do not remove the existing ISO recovery flow or its separate-media guard.

## Alternatives Considered

### A. Trusted package activation plus transactional in-place updates

Recommended and approved. The launcher remains strict by default, but the
launch broker may use the DesktopAppInfo API for a narrowly verified desktop
file owned by an installed DEB package. Major updates use a signed payload,
an on-disk rollback journal, and an initramfs transaction mode.

This is the only option that preserves both application compatibility and
the safety boundaries needed for old single-disk machines.

### B. Launch all desktop entries through GIO or a shell

Rejected. It would make wrappers appear to work, but a user-controlled or
modified desktop file could gain the same execution path.

### C. Allow existing ISO OTA to use same-disk backup storage

Rejected. The current path boots an ISO and runs a destructive Calamares
install. Formatting the root disk can destroy both the staged ISO and the
backup. Removing the guard would create data-loss risk.

## Application Launch Architecture

### One final application-library implementation

The build must install exactly one ming-app-library compatibility command:

    ming-app-drawer --toggle

The legacy Xfce GTK application library must not overwrite it later in the
desktop build. The final generated script must contain no application-launch
fallback using shell=True.

### Strict normal launcher path

The IPC contract continues to carry only a canonical desktop_file path. It
does not carry argv, an activation mode, or a caller-selected trust flag.

For normal desktop entries, the launch broker parses Exec using the existing
strict parser and starts the resulting argv with shell=False. User desktop
entries, Desktop copies, usr/local entries, symlinks, and malformed entries
stay on this path or are rejected with a diagnostic.

### Trusted package-desktop activation exception

The broker alone may activate an otherwise shell-wrapped entry through
Gio.DesktopAppInfo.new_from_filename(path).launch(). The exception is
allowed only when every condition below is true:

- The resolved file is a regular, non-symlink file directly below
  /usr/share/applications and has a .desktop suffix.
- It is owned by root and is not group-writable or other-writable.
- dpkg-query -S confirms an exact owner for the canonical path.
- The owning package reports installed state ii.
- The request reached the broker through the existing allowlisted
  desktop_file IPC contract.

The broker never uses gtk-launch by basename because an XDG user entry with
the same name could change the target. The application starts as the current
desktop user, never through pkexec.

If the broker cannot be reached, the desktop and drawer may invoke the
one-shot ming-launch --desktop-file path. They must not directly start a
wrapper or fall back to shell execution.

### Installation outcome and feedback

ming-package-installer will distinguish:

- installed: no visible desktop entry needs special handling.
- installed_with_desktop_activation: a package-owned system entry is safely
  launchable through the broker.
- installed_with_launch_problem: a visible entry has a missing executable,
  missing ELF library, bad metadata, or an unsafe ownership state.

The last state remains truthful even if dpkg finished successfully. It
includes the package, desktop-file path, reason, repair command, and log
path. Cache refresh and desktop/drawer rescan occur only after dpkg state is
verified.

Launch events record requested, activated, spawned, process_exit,
window_timeout, and ready states. The user sees a clear notice for a missing
command, immediate exit, or no visible window, including the diagnostic log
location. A background application that deliberately has no window is
recorded as activated rather than falsely described as a shell failure.

## Transactional OTA Architecture

### Delivery split

There are two distinct major-update delivery modes:

- transactional-inplace: compatible systems use a signed payload and never
  run Calamares or a partitioning tool.
- recovery_iso: existing ISO boot and Calamares recovery. It continues to
  require independent preservation media and retains all same-disk guards.

The server must choose transactional-inplace only after the client reports a
bootstrap capability. Legacy 26.3.2 clients continue to receive the recovery
description until the bootstrap has been installed.

### 26.3.2 bootstrap

Ming OS 26.3.2 has no transaction engine, trusted manifest verifier, or
initramfs hook. It cannot safely acquire those capabilities merely by seeing
a new server manifest.

The official migration is a one-time manual installation of a signed
ming-ota-bootstrap DEB package. It installs:

- The transaction-aware ming-update client.
- The trusted release public key and detached-signature verifier.
- The initramfs transaction hook and rollback command.
- The systemd post-boot health service.
- The client capability marker used by the update check request.

The bootstrap artifact, checksum, detached signature, and public-key
fingerprint are published together on the official website and GitHub
release. The package itself is verified through the local package installer
before installation. Once it has completed, the normal Settings update page
continues to expose one check/update action.

### Signed manifest and payload

The bootstrap embeds an offline release public key. A compatible release
manifest is detached-signed and its signature covers every security-relevant
field, including version range, payload URL, SHA256, content-index SHA256,
allowed target paths, required free space, and rollback policy.

The manifest schema includes at least:

    schema: 2
    release_id: a unique immutable release identifier
    version: target Ming OS version
    arch: amd64
    delivery: transactional-inplace
    from_versions: explicit supported source versions
    payload: URL, size, SHA256, tar.zst format
    content_index_sha256: hash of file and deletion plan
    required_free_bytes: minimum journal and payload space
    preserve_paths: includes /home
    rollback: maximum uncommitted boot attempts and retention policy
    expires_at: release validity deadline

The payload contains only an explicitly allowlisted Ming system surface.
Machine identity, user files, user application state, package databases,
runtime directories, and /home are excluded. Debian package changes remain
an explicit signed APT package list handled before the transaction.

### State machine and rollback

Each transaction lives under:

    /var/lib/ming-update/transactions/<release-id>/

The persistent state changes in this order:

    prepared -> applying -> pending-health -> committed
                               |
                               -> rolling-back -> rolled-back

Before changing each allowed system file, the engine writes an fsync-backed
journal record and stores the previous file or deletion marker in the
transaction journal. New files are verified against the signed content index
and atomically renamed into place. Path traversal, symlinks, hard-link
escapes, and modifications outside the allowlist are rejected.

The prepared transaction is started by a one-time GRUB/initramfs update mode.
The initramfs mounts the existing root filesystem and applies the payload;
it does not invoke Calamares, mkfs, fdisk, parted, resize2fs, or a
partitioning command. It then boots the normal system in pending-health
state.

A bounded post-boot health unit validates the transaction record, graphical
target, display manager, Ming desktop health, and essential system services.
Only a successful health check marks the transaction committed. If power is
lost, the payload fails, or the next boot sees pending-health without a
commit marker, initramfs restores the journal before continuing. GRUB also
retains a visible manual rollback entry.

The free-space check requires the downloaded payload, all replacement
journal data, metadata, and a fixed reserve. Insufficient space refuses the
update before any boot entry or file change is written.

## Observability and User Flow

Settings remains the only normal update entry:

1. Check updates obtains a capability-appropriate manifest.
2. A legacy unbootstrapped 26.3.2 device is told to install the official
   bootstrap once rather than being sent into an unsafe ISO route.
3. A bootstrapped compatible device uses the existing one-button update
   action.
4. The update downloads, verifies, reserves rollback space, stages, and
   requests one transactional reboot.
5. Health success commits silently; failure rolls back and presents the
   transaction log on the next successful session.

The existing ISO recovery route remains available only for machines that
choose independent preservation media.

## Required Verification

### Application launch

- The final image deploys ming-app-library once and it delegates to the
  drawer.
- A trusted package-owned shell wrapper activates only through GIO.
- User, Desktop, usr/local, symlinked, writable, unowned, ambiguous, and
  non-installed desktop files cannot activate through the exception.
- IPC cannot select an activation mode.
- Broker-unavailable fallback never executes a wrapper directly.
- The installer differentiates a safely activatable wrapper from a missing
  executable or missing library.
- Desktop, drawer, and Dock share launch result logging and produce one
  action per user click.

### Transactional OTA

- Invalid, expired, mismatched, unsigned, wrong-architecture, wrong-source,
  or path-escaping manifests/payloads are rejected.
- Space checks reject before writing GRUB state or changing files.
- /home hashes and tree structure remain unchanged after success, payload
  failure, simulated interruption, and rollback.
- Interrupted journaling and failed health checks restore the prior system.
- Transaction mode proves it never invokes Calamares, a partitioner, mkfs,
  or filesystem resizing.
- The recovery ISO path still rejects same-disk preservation media.
- A bootstrap client reports its capability, obtains only the compatible
  manifest, and can complete a transaction after reboot.

## Release Constraints

No ISO build, website deployment, or OTA publication follows from this design
document alone. The implementation must pass the new unit, generated-script,
signature, rootfs validation, and boot rollback checks before a release
artifact is considered publishable.
