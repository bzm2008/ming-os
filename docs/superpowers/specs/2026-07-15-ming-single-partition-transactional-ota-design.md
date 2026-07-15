# Ming OS Single-Partition Transactional OTA Design

## Status

This document is the approved architecture baseline for a first-generation
single-partition transactional OTA. It defines contracts and implementation
boundaries only. It does not authorize production code changes, an ISO build,
release publication, or server deployment.

For single-partition OTA, this document supersedes the in-place changed-file
journal described in
`2026-07-15-ming-ota-bootstrap-and-spark-launch-design.md` and the matching
transaction sections of earlier implementation plans. Those documents remain
historical context for Spark launch and recovery behavior, but must not be used
as the transaction storage design.

The design supplements, but does not replace, the existing recovery ISO flow.
The recovery ISO continues to require independent preservation media and keeps
all existing same-disk safety checks.

## Goals

- Upgrade an installed Ming OS whose system and `/home` share one root
  filesystem, without an external disk.
- Preserve `/home`, accounts, machine identity, network configuration, SSH host
  keys, Bluetooth state, installed package state, and locally installed apps.
- Leave the active system root untouched while the candidate is prepared.
- Recover automatically from download failure, staging failure, power loss,
  an invalid transaction boot, or a failed post-boot health check.
- Use one-time GRUB boot selection and a bounded initramfs slot selector.
- Require detached offline-public-key signature verification for the release
  manifest, payload bytes, and content index independently.
- Preserve the one-time signed 26.3.2 bootstrap route.
- Expose a versioned CLI/JSON interface that Terra and Luna can consume without
  access to boot, trust, state, or filesystem internals.

## Non-Goals

- No Calamares, partitioner, `mkfs`, `resize2fs`, filesystem conversion, or
  partition-table change is used by this delivery type.
- No protection is claimed against physical disk loss. Same-disk rollback
  protects software state, not hardware failure.
- No kernel replacement, initramfs ABI change through a release payload, DKMS,
  third-party driver, or desktop redesign is included.
- The transaction engine does not merge arbitrary user edits with vendor files.
- The first version does not attempt an in-place file journal or an
  overlay/squashfs root.

## Hard Invariants

1. `delivery=transactional-slot-v1` and `delivery=recovery-iso` are separate
   protocols, code paths, artifacts, and release gates.
2. The recovery ISO target guard and its independent-backup-media requirement
   are unchanged. Transactional OTA code cannot call the recovery installer.
3. The saved GRUB default remains the last committed root until candidate
   health succeeds.
4. No GRUB state is changed until all three signatures, all hashes, available
   space, the inactive slot, and candidate contents have passed validation.
5. The active root is never a payload target. All package and file mutations
   happen inside the inactive slot.
6. `/home`, `/boot`, the transaction store, virtual mounts, machine identity,
   account databases, network secrets, and SSH host keys are never payload
   targets.
7. Every persistent state transition uses write-to-new-file, `fsync`, atomic
   rename, and parent-directory `fsync`. Every transition also appends and
   `fsync`s a JSONL event.
8. A candidate is committed only after a durable health token exists and the
   GRUB saved entry has been written and read back successfully.
9. A missing, malformed, expired, unsigned, unsupported, or ambiguous input is
   rejected. There is no permissive fallback.
10. Insufficient free space refuses staging before any candidate, initramfs, or
    GRUB boot state is armed.

## Alternatives Considered

### Directory A/B roots on the existing filesystem

Selected. The active root is cloned to an inactive directory on the same ext4
filesystem. The signed release is applied offline to that clone. Initramfs
bind-mounts the selected directory as `/`, while `/home`, `/boot`, and the
transaction state anchor remain shared from the physical root.

This costs more disk space than a journal, but package scripts and dpkg state
cannot corrupt the known-good root. The space cost is explicit and checked
before any boot change.

### In-place updates with a changed-file rollback journal

Rejected for v1. Debian maintainer scripts may mutate files not predicted by a
content list, update alternatives, rebuild caches, change package databases,
or partially complete. Proving a complete rollback after arbitrary interruption
would be substantially harder than keeping the old root untouched.

### SquashFS plus overlay root

Rejected for v1. It complicates preservation of user-installed packages,
machine configuration, mutable system state, and future rebasing. It also
introduces a new runtime storage model unrelated to the current installation.

## Root and Slot Architecture

### Physical filesystem anchor

The installed root block device remains unchanged. During initramfs it is
mounted at `/run/ming-update/physical-root`. The canonical persistent store is
the directory below on that physical filesystem:

```text
/var/lib/ming-update/
```

When a directory slot is active, initramfs bind-mounts the physical store over
the slot's own `/var/lib/ming-update`. This prevents recursive slot copies and
ensures every slot sees one transaction history.

The first bootstrapped installation is represented as slot `legacy`; it is the
physical root itself. The first transaction clones `legacy` into `B`. Later
transactions alternate between `A` and `B`. The legacy root remains a retained
fallback until an explicit future cleanup design is approved; v1 does not
delete it automatically.

### Slot filesystem layout

```text
/var/lib/ming-update/
  protocol-version
  current.json
  active-transaction.json
  locks/
    engine.lock
  slots/
    A/
      slot.json
      root/
    B/
      slot.json
      root/
  transactions/
    <transaction-id>/
      state.json
      events.jsonl
      plan.json
      base-seal.json
      candidate-seal.json
      health-token.json
      artifacts.json
      failure.json
  artifacts/
    sha256/<first-two>/<sha256>
  boot/
    attempts.jsonl
    initramfs.log
  quarantine/
    <transaction-id>.json

/var/cache/ming-update/
  discovery.json
  downloads/
  partial/

/usr/share/ming-update/
  schemas/
  trust/
    release-keyring.gpg
    key-policy.json
  fixtures/

/run/ming-update/
  physical-root/
  selected-root/
  engine.pid
  health/
```

`/var/cache/ming-update` is disposable. `/var/lib/ming-update` is authoritative.
Trust material is installed read-only under `/usr/share`; the updater never
downloads keys or uses a keyserver.

### Clone policy

The clone is created with numeric ownership, permissions, ACLs, xattrs, hard
links, sparse files, and symlinks preserved. It excludes:

```text
/boot
/dev
/home
/lost+found
/media
/mnt
/proc
/run
/sys
/tmp
/var/lib/ming-update
/var/tmp
```

Mounted paths discovered from `/proc/self/mountinfo` are excluded unless they
are the physical root itself. Known swap files are not copied and are mounted
or re-enabled from the physical root only after slot selection.

Staging holds the dpkg frontend/backend locks and a Ming update lock. A first
copy may run while the user session is active; a bounded final synchronization
captures `/etc`, `/usr`, package databases, and machine state while package
mutation is blocked. If a consistent final pass cannot be obtained, staging
fails without arming a boot.

The following protected state is hashed before and after payload application;
any unexpected change rejects the candidate:

- `/etc/machine-id`
- `/etc/passwd`, `/etc/group`, `/etc/shadow`, `/etc/gshadow`
- `/etc/NetworkManager/system-connections`
- `/etc/ssh/ssh_host_*`
- `/var/lib/NetworkManager`
- `/var/lib/bluetooth`
- locally installed package records not selected by the signed package plan

`/home` is not copied. It is bind-mounted from the physical root into the
candidate, so success and rollback see the same user data. The engine computes
a metadata-only preservation seal before arming and confirms that the OTA
engine itself did not write below `/home`. It does not claim to undo writes made
by users or running applications during the update window.

## Release Artifact Trust Model

### Root of trust

Verification uses `gpgv` and a pinned, root-owned keyring. There is no TOFU,
keyserver lookup, downloaded trust root, or web-PKI-only acceptance. The key
policy pins allowed primary fingerprints and signing subkeys, validity windows,
release channels, and a minimum bootstrap protocol.

Key rotation requires a separately reviewed bootstrap or a cross-signed key
policy accepted by the already pinned key. Terra never receives a private key.

### Independently signed artifacts

Each transaction consists of six immutable objects:

```text
manifest.json
manifest.json.sig
content-index.json
content-index.json.sig
payload.tar.zst
payload.tar.zst.sig
```

The validation order is fixed:

1. Verify `manifest.json.sig` against the pinned keyring.
2. Validate manifest schema, source version, architecture, expiry, and policy.
3. Download the content index and payload to hash-addressed temporary files.
4. Verify each detached signature independently.
5. Verify each manifest-pinned SHA256 and byte size.
6. Validate every content-index entry and compute the required-space plan.
7. Atomically promote downloads into the artifact store.

A valid manifest cannot compensate for a missing payload or index signature.
A valid artifact signature cannot compensate for a hash mismatch in the signed
manifest.

### Manifest contract

The canonical JSON is UTF-8, duplicate-key-free, and schema validated before
use. Security decisions never depend on object key ordering.

```json
{
  "schema": "ming.transaction-manifest.v1",
  "release_id": "ming-os-26.3.3-amd64-20260715.1",
  "version": "26.3.3",
  "channel": "stable",
  "architecture": "amd64",
  "delivery": "transactional-slot-v1",
  "from_versions": ["26.3.2", "26.3.3-preview"],
  "minimum_bootstrap": "1.0.0",
  "created_at": "2026-07-15T00:00:00Z",
  "expires_at": "2026-08-15T00:00:00Z",
  "kernel_release": "6.12.0-amd64",
  "payload": {
    "url": "https://downloads.example.invalid/objects/<sha256>",
    "sha256": "<64 lowercase hex>",
    "size": 123456789,
    "signature_url": "https://downloads.example.invalid/objects/<sig-id>"
  },
  "content_index": {
    "url": "https://downloads.example.invalid/objects/<sha256>",
    "sha256": "<64 lowercase hex>",
    "size": 123456,
    "signature_url": "https://downloads.example.invalid/objects/<sig-id>"
  },
  "space": {
    "minimum_free_bytes": 8589934592,
    "reserve_bytes": 2147483648
  },
  "slot_policy": {
    "maximum_uncommitted_boots": 1,
    "retain_previous_committed_slots": 1
  },
  "preserve_paths": ["/home"],
  "health_profile": "ming-core-v1"
}
```

URLs must be HTTPS, immutable, and free of credentials and query-controlled
commands. `kernel_release` must equal the installed kernel used by the active
slot. v1 rejects a manifest that changes it.

### Content index contract

The content index describes every payload object and offline package. Regular
file blobs are stored by SHA256 inside the payload; the archive is treated as
an object container, never extracted directly into a root.

```json
{
  "schema": "ming.content-index.v1",
  "release_id": "ming-os-26.3.3-amd64-20260715.1",
  "entries": [
    {
      "path": "usr/share/ming-os/example",
      "type": "file",
      "blob": "sha256:<64 lowercase hex>",
      "mode": 420,
      "uid": 0,
      "gid": 0,
      "config_policy": "replace"
    }
  ],
  "deletions": ["usr/share/ming-os/obsolete"],
  "packages": [
    {
      "name": "ming-example",
      "version": "1.2.3",
      "architecture": "amd64",
      "blob": "sha256:<64 lowercase hex>"
    }
  ]
}
```

Allowed entry types are `file`, `directory`, and `symlink`. Symlink targets
must remain inside the candidate root after lexical and filesystem resolution.
Device nodes, sockets, FIFOs, hard links, setuid additions not explicitly
allowlisted, absolute paths, `..`, duplicate normalized paths, and case/Unicode
ambiguity are rejected.

Forbidden targets include `/home`, `/boot`, transaction storage, virtual or
runtime filesystems, `/usr/lib/modules`, `/lib/modules`, kernel images,
initramfs images, GRUB binaries/configuration, account databases, machine ID,
network secrets, SSH host keys, and private key material. Package plans reject
kernel, bootloader, initramfs-tools, DKMS, firmware-driver replacement, and
third-party driver packages.

Configuration policies are limited to:

- `replace`: vendor-owned files that are not machine configuration.
- `replace-if-unmodified`: replace only when the candidate base hash equals the
  signed expected base hash.
- `preserve`: retain the cloned file and record the vendor candidate as a
  `.ming-new` diagnostic artifact.

There are no payload-provided shell hooks. Debian package maintainer scripts
may run only for packages named and hashed in the signed index, inside the
inactive root, offline, with service starts blocked. `--force-confold` preserves
machine conffiles. Package failure invalidates the candidate.

## Persistent State Machine

### States

```text
new
  -> verified
  -> staging
  -> staged
  -> armed
  -> booting
  -> pending_health
  -> committing
  -> committed

new|verified|staging|staged
  -> aborting
  -> aborted

armed|booting|pending_health|committing
  -> rollback_armed
  -> rolling_back
  -> rolled_back
```

Only one nonterminal transaction may exist. `current.json` identifies the last
committed slot. `active-transaction.json` contains only the transaction ID,
candidate slot, previous slot, state generation, and state file hash.

### State writers

| Transition | Authorized writer | Required durable evidence |
| --- | --- | --- |
| `new -> verified` | verifier | three valid detached signatures and normalized plan |
| `verified -> staging` | slot manager | global lock and space reservation |
| `staging -> staged` | candidate applicator | base seal, candidate seal, dpkg audit, protected-path seal |
| `staged -> armed` | boot coordinator | GRUB tx fields and `next_entry` readback |
| `armed -> booting` | initramfs selector | matching candidate slot sentinel and boot attempt record |
| `booting -> pending_health` | early health service | selected-root and critical mount checks |
| `pending_health -> committing` | health confirmer | durable health token for the exact transaction and candidate seal |
| `committing -> committed` | commit coordinator | saved GRUB entry points to candidate and readback matches |
| pre-arm to `aborted` | engine | candidate never selected for boot |
| post-arm to `rolled_back` | initramfs/rollback service | previous slot selected and candidate quarantined |

Invalid transition, stale generation, missing evidence, or writer mismatch is a
hard error. State files include `schema`, `generation`, `updated_at`,
`transaction_id`, `release_id`, `previous_slot`, `candidate_slot`, `state`,
and evidence hashes.

### Power-loss reconciliation

| Durable state found at boot | Action |
| --- | --- |
| `new`, `verified`, `staging` | Boot committed slot; mark interrupted staging abortable; never select candidate. |
| `staged` without GRUB readback evidence | Boot committed slot; clear stale GRUB transaction fields; keep staged data for doctor/cancel. |
| GRUB one-shot set but state still `staged` | Candidate initramfs refuses it and boots the committed slot; mark aborted. |
| `armed` and candidate entry selected | Verify sentinel/seal, increment the single boot attempt, select candidate. |
| `armed` but committed entry selected | Mark rollback armed; boot committed slot and quarantine candidate. |
| `booting` or `pending_health` on a later committed-slot boot | Treat candidate as interrupted/unhealthy; mark rolled back. |
| `committing` with valid health token and GRUB saved entry on candidate | Finish `committed` idempotently. |
| `committing` with token but saved entry still previous | Retry one bounded GRUB write; on failure restore previous and roll back. |
| `committing` without valid token | Restore previous saved entry and roll back. |
| `committed` but current-slot readback disagrees | Refuse further OTA, boot the last verified slot, and require `doctor`. |

## GRUB and Initramfs Protocol

### Bootstrap-installed entries

The bootstrap installs stable GRUB entry IDs, not release-specific generated
commands:

```text
ming-legacy
ming-slot-a
ming-slot-b
ming-recovery-manual
```

Each slot entry passes only a fixed `ming.slot=legacy|A|B` kernel argument.
Transaction identity comes from the root-owned persistent state, not an
untrusted kernel command string. The existing kernel and initramfs are used for
all entries.

Arming order is:

1. Persist `staged` state and candidate sentinel.
2. Write bounded GRUB transaction metadata and read it back.
3. Call `grub-reboot` for the exact inactive-slot entry and read back
   `next_entry`.
4. Persist `armed` state.

The saved default is not changed. A power failure before step 4 causes the
candidate initramfs to reject the boot because the state is not armed.

Commit order is:

1. Persist the health token.
2. Persist `committing`.
3. Set the saved entry to the candidate slot.
4. Read back the GRUB environment and entry ID.
5. Persist `current.json` and `committed`.

This order makes `committing` recoverable after power loss.

### Initramfs selector

The initramfs component has a fixed 15-second total deadline and 2-second
deadlines for individual probes. It:

1. Mounts the physical root at `/run/ming-update/physical-root`.
2. Validates the requested fixed slot, state schema, generation, transaction
   relationship, attempt count, slot sentinel, candidate seal, and kernel
   release.
3. If valid, bind-mounts the candidate root as the future `/`.
4. Bind-mounts the physical `/home`, `/boot`, and transaction store into the
   candidate.
5. Mounts virtual filesystems and switches root using the existing kernel.
6. If any candidate check fails, records rollback evidence and directly selects
   the previous committed root; it does not invoke an installer or filesystem
   tool.

The selector contains a compile-time forbidden-command test for `calamares`,
`parted`, `fdisk`, `sfdisk`, `gdisk`, `mkfs`, `resize2fs`, and equivalents.

When the requested slot equals the last slot in `current.json`, initramfs treats
it as a normal committed boot and does not require an armed transaction. A
different slot is accepted only as the exact `armed` one-shot candidate. No
third interpretation or best-effort slot scan is allowed.

## Health, Commit, and Rollback

### Rollback-critical health profile

`ming-core-v1` is bounded to 60 seconds and runs before graphical login. It
checks:

- the selected slot and transaction IDs match;
- root is writable and `/home` is the expected shared mount;
- candidate and protected-path seals match;
- `dpkg --audit` is clean and no package operation is pending;
- essential units can reach their required state;
- NetworkManager, D-Bus, logind, audio session prerequisites, and the display
  manager configuration are valid;
- the Ming settings, launcher, desktop, Dock, and session supervisor pass their
  offline runtime self-checks;
- no forbidden kernel, module, bootloader, or transaction-engine replacement
  occurred.

The display manager starts only after this rollback-critical profile commits.
Actual GPU rendering and a user's graphical session are observed after login,
but do not trigger an automatic slot rollback because transient display or user
configuration faults could otherwise create a boot loop. They remain eligible
for the existing desktop fallback and diagnostics.

Health failure writes `failure.json`, arms rollback, restores the previous GRUB
saved entry, and reboots. The next initramfs selects the previous slot, records
`rolling_back -> rolled_back`, and quarantines the failed candidate. There is no
second automatic candidate attempt in v1.

### Rollback log

Every rollback creates:

```text
/var/lib/ming-update/transactions/<id>/failure.json
/var/lib/ming-update/transactions/<id>/events.jsonl
/var/log/ming-update/rollback.jsonl
/var/log/ming-update/transactions/<id>/engine.log
```

The rollback record contains no user file contents or network credentials. It
includes transaction/release IDs, prior/candidate slots, state generation,
boot ID, health check, stable error code, monotonic and wall-clock timestamps,
GRUB readback, and log paths.

## CLI and JSON Contract

### Public CLI

```text
ming-update status --json
ming-update check --json
ming-update apply --release-id ID --manifest-sha256 HASH --json
ming-update cancel --transaction ID --json
ming-update doctor --json
ming-update logs --transaction ID --json
```

`check` and read-only commands run unprivileged. `apply` and `cancel` cross a
dedicated polkit boundary. Root revalidates the release ID and manifest hash
against its own cache; it never trusts a caller-provided path or URL.

`cancel` is allowed only through `staged`. At `armed` or later it returns a
non-cancelable error and rollback is coordinated by the boot state machine.
`logs` returns metadata and approved log paths, not raw arbitrary files.

### Common envelope

All JSON is one UTF-8 object on stdout. Diagnostics go to the documented log,
not stdout. The schema is append-only within v1; consumers ignore unknown keys.

```json
{
  "schema": "ming.update.cli.v1",
  "ok": true,
  "command": "status",
  "exit_code": 0,
  "error_code": null,
  "state": "committed",
  "transaction": {
    "id": "tx-20260715-<random>",
    "release_id": "ming-os-26.3.3-amd64-20260715.1",
    "previous_slot": "legacy",
    "candidate_slot": "B",
    "generation": 12
  },
  "update": {
    "current_version": "26.3.2",
    "available_version": "26.3.3",
    "delivery": "transactional-slot-v1"
  },
  "action": "none",
  "progress": {"phase": "idle", "percent": 100},
  "requires_reboot": false,
  "message_key": "update.status.committed",
  "message_args": {},
  "log_path": "/var/log/ming-update/transactions/<id>/engine.log",
  "timestamp": "2026-07-15T12:00:00Z"
}
```

No-update is `ok=true`, exit 0, `action=none`. Progress is monotonic within one
phase. Human text is selected by Luna from `message_key`; the core does not
make security decisions from localized strings.

### Exit codes and stable errors

| Exit | Class | Stable error codes |
| ---: | --- | --- |
| 0 | Success/no update | none |
| 2 | CLI usage/not found | `E_ARGUMENT`, `E_TRANSACTION_NOT_FOUND` |
| 3 | Policy refusal | `E_BUSY`, `E_NOT_CANCELABLE`, `E_SPACE`, `E_SOURCE_UNSUPPORTED`, `E_BOOTSTRAP_REQUIRED` |
| 4 | Trust/validation | `E_MANIFEST_SIGNATURE`, `E_MANIFEST_SCHEMA`, `E_MANIFEST_EXPIRED`, `E_ARTIFACT_SIGNATURE`, `E_ARTIFACT_HASH`, `E_CONTENT_POLICY` |
| 5 | Staging | `E_CLONE`, `E_PACKAGE_STATE`, `E_PACKAGE_APPLY`, `E_PROTECTED_PATH_CHANGED`, `E_CANDIDATE_SEAL` |
| 6 | Boot coordination | `E_GRUB_WRITE`, `E_GRUB_READBACK`, `E_INITRAMFS_CONTRACT`, `E_SLOT_MOUNT`, `E_SLOT_MISMATCH` |
| 7 | Health | `E_HEALTH_TIMEOUT`, `E_HEALTH_ROOT`, `E_HEALTH_PACKAGES`, `E_HEALTH_SERVICE`, `E_HEALTH_DESKTOP_PROBE` |
| 8 | Rollback | `E_ROLLBACK_GRUB`, `E_ROLLBACK_STATE`, `E_ROLLBACK_SLOT` |
| 9 | Persistent state | `E_STATE_SCHEMA`, `E_STATE_TRANSITION`, `E_STATE_DURABILITY`, `E_STATE_RECONCILE` |
| 10 | Bootstrap/protocol | `E_BOOTSTRAP_SIGNATURE`, `E_BOOTSTRAP_VERSION`, `E_PROTOCOL_UNSUPPORTED`, `E_KEY_POLICY` |

Shell wrappers must preserve these exit codes. Internal exceptions map to one
stable error and are logged with a private diagnostic ID.

## 26.3.2 Bootstrap Contract

26.3.2 cannot infer or download trust. Its existing one-time signed bootstrap
path remains the only supported transition into `transactional-slot-v1`.

The bootstrap package installs:

- the pinned release keyring and key policy;
- verifier, state engine, slot manager, initramfs selector, health and rollback
  units;
- stable GRUB slot entries while leaving the saved default on `legacy`;
- CLI v1 and polkit policy;
- `/var/lib/ming-update/protocol-version` and a capability marker;
- an uninstall/refusal path that is allowed only before any transaction is
  armed.

The bootstrap package, checksum, detached signature, and expected signing
fingerprint remain published together. The existing 26.3.2 trusted bootstrap
verifier must validate them before dpkg runs. The bootstrap's post-install
script regenerates initramfs and GRUB, then reads back both artifacts. A partial
bootstrap does not advertise the capability.

Terra returns transactional discovery metadata to 26.3.2 only after the client
reports `transactional-slot-v1` and the minimum bootstrap version. Otherwise it
returns the signed bootstrap action; it must not silently route an
unbootstrapped single-partition machine into recovery ISO installation.

## Recovery ISO Isolation

The existing `recovery-iso` implementation and `ming-ota-target-guard` remain
unchanged. In particular:

- independent preservation-media UUID and physical-disk ancestry checks stay
  mandatory;
- no same-root `/home` exception is added to the recovery path;
- transactional artifacts cannot be interpreted as an ISO or Calamares job;
- `ming-update apply` dispatches by verified delivery type before privilege is
  granted;
- release tests fail if recovery code imports transaction slot exceptions or
  transaction code calls installer/partitioning commands.

## Terra Interface Boundary

Terra owns discovery and immutable artifact publication. It may return:

```json
{
  "schema": "ming.update.discovery.v1",
  "current_version": "26.3.2",
  "capability": "transactional-slot-v1",
  "release_id": "ming-os-26.3.3-amd64-20260715.1",
  "delivery": "transactional-slot-v1",
  "manifest_url": "https://downloads.example.invalid/objects/<sha256>",
  "manifest_signature_url": "https://downloads.example.invalid/objects/<sig-id>"
}
```

Terra may not return shell commands, package scripts, local paths, GRUB entry
names, trust keys, or caller-selected payload paths. Discovery metadata is a
locator, not a trust decision; the signed manifest is authoritative. Artifacts
are immutable and content-addressed. Private signing happens offline outside
Terra.

Terra's stable inputs are current version, architecture, channel, and an
allowlisted capability set. Its stable outputs are the discovery schema above
or a bootstrap-required action. Terra does not inspect transaction state.

## Luna Interface Boundary

Luna owns presentation only. It:

- calls the public CLI and parses `ming.update.cli.v1`;
- starts `check`, and requests privileged `apply` or `cancel` through the
  dedicated polkit action;
- polls `status --json` for monotonic phase/progress;
- maps `message_key` and stable errors to localized UI;
- shows reboot, rollback, space, and bootstrap outcomes;
- may open only a `log_path` returned by the CLI after path validation.

Luna never parses human logs, writes `/var/lib/ming-update`, invokes GRUB or
initramfs tools, supplies URLs or filesystem paths to root, installs keys,
chooses slots, or converts a recovery release into a transaction. Unknown
schema major versions are shown as unsupported, not guessed.

## Core-Owner-Only Surface

The following files and logical sections require core OTA owner review and
CODEOWNERS approval. Terra and Luna must not modify them.

- `contracts/ota/*.schema.json` and security fixtures.
- `assets/ming-transaction-state.py`.
- `assets/ming-transaction-verify.py`.
- `assets/ming-transaction-slot.py`.
- `assets/ming-transaction-apply.py`.
- `assets/ming-transaction-health.py`.
- `assets/ming-transaction-rollback.py`.
- `assets/initramfs/ming-transaction-hook`.
- `assets/initramfs/ming-transaction-local-premount`.
- `assets/grub/40_ming_transaction`.
- `assets/systemd/ming-transaction-*.service`.
- `assets/polkit/org.mingos.update.policy`.
- `assets/trust/ming-ota-release-keyring.gpg` and key policy.
- `tools/build-ming-transaction-payload.py`.
- `tools/sign-ming-transaction-release.sh` and offline signing procedure.
- boot integration sections of `modules/01_base.sh`.
- trust, dispatch, state, slot, bootstrap, and recovery-isolation sections of
  `modules/06_ota_update.sh`.
- transactional release gates in `build_onion_os.sh`.
- `.github/CODEOWNERS` rules protecting this surface.

Terra may own its server adapter and contract tests. Luna may own the update
page in `assets/ming-settings.py` and UI tests, but cross-layer schema changes
still require core approval.

## Test Contracts

### Trust and content

- Each of manifest, index, and payload fails independently when its signature
  is absent, wrong, expired, revoked by policy, or made by an unpinned key.
- Hash, size, release ID, source version, architecture, expiry, and kernel
  mismatch fail before slot creation.
- Duplicate JSON keys, duplicate normalized paths, traversal, unsafe symlinks,
  special files, forbidden targets, kernel packages, and DKMS are rejected.
- Archive parsing never writes a path selected by an archive member name.

### Slot and preservation

- A fixture root with accounts, NetworkManager secrets, SSH keys, Bluetooth
  state, local packages, and `/home` produces a candidate with identical
  protected hashes before payload changes.
- Success, staging failure, package failure, and simulated interruption leave
  the active root byte-for-byte unchanged for the tested surface.
- `/home` tree and metadata hashes are unchanged by the engine on success and
  rollback.
- Insufficient space fails before candidate promotion or GRUB environment
  writes.
- A mounted subdirectory, swap file, transaction store, and virtual filesystem
  are not recursively copied.

### State and power loss

- Every legal transition is accepted once and idempotently reconciled; every
  illegal or stale-generation transition is rejected.
- Fault injection after every durable write produces one of: safe abort before
  arm, candidate one-shot boot, or automatic previous-slot boot.
- `committing` recovers correctly for all health-token/GRUB-readback
  combinations.
- JSONL event generations are monotonic and the final event matches state.

### Boot and health

- BIOS and UEFI VMs boot `legacy`, `A`, and `B` using the unchanged kernel.
- Candidate is selected once while saved default remains previous.
- Invalid sentinel, seal, mount, kernel, or state causes direct previous-slot
  selection within the initramfs deadline.
- Health success commits and changes saved default only after readback.
- Health timeout, failed package audit, failed essential service, or failed
  desktop offline probe rolls back automatically.
- A user cannot log in to an unconfirmed candidate.

### Interface and isolation

- CLI stdout validates against `ming.update.cli.v1` for success and every error
  class; stderr contains no JSON protocol fragments.
- Terra contract fixtures contain no commands, keys, local paths, or mutable
  artifact URLs.
- Luna works from fixtures without transaction-store or log parsing.
- Static and runtime tests prove the transaction path never invokes Calamares,
  partitioners, formatters, resizers, kernel replacement, or DKMS.
- Existing recovery tests still reject same-disk preservation media.
- An unbootstrapped 26.3.2 client receives only the signed bootstrap action; a
  bootstrapped client can discover and complete a v1 transaction.

## Operational Risks

1. **Free-space pressure:** a full inactive root may be too large for some
   single-partition installations. Refusal before arming is mandatory; v1 does
   not trade rollback integrity for lower space use.
2. **No filesystem snapshot:** live cloning needs a bounded final sync and dpkg
   locking. A consistency failure must abort staging.
3. **Shared `/home`:** rollback does not undo user/application writes. The OTA
   engine must never write there, and release migrations must remain backward
   compatible with user data.
4. **Shared `/boot` and unchanged kernel:** the first protocol cannot deliver a
   kernel or initramfs ABI update. Such a release requires a separately designed
   boot-artifact transaction.
5. **GRUB environment durability:** readback and `committing` reconciliation are
   mandatory because grubenv writes are not a general transactional database.
6. **Disk failure:** both roots share one device. Users still need normal data
   backup; recovery ISO retains independent-media requirements.
7. **Legacy-root retention:** the initial physical root consumes space after
   first commit. Automatic pruning is intentionally out of scope until a
   separately reviewed migration/cleanup protocol exists.
8. **Maintainer scripts:** offline DEB scripts are less deterministic than
   content-index writes. Strict package allowlisting, network isolation,
   service blocking, conffile preservation, and post-apply seals are required.
9. **False health failures:** overly broad graphical checks can cause rollback
   loops. Only bounded, deterministic pre-login checks are rollback-critical.
10. **Trust-key loss or compromise:** offline signing, dual control, published
    fingerprints, key-policy expiry, and rehearsed revocation are release
    prerequisites.
11. **Configuration changes during staging:** package or machine configuration
    changed after the bounded final sync may not exist in the candidate. Arming
    must immediately recheck protected generations and proceed directly to
    reboot; a detected change returns to staging instead of using a stale clone.
