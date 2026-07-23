# Ming OS Single-Partition Transactional OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a signed directory-slot transactional OTA that upgrades a Ming OS single-root installation without external storage, preserves `/home` and machine state, and automatically returns to the last committed root after interruption or failed health.

**Architecture:** Keep the existing partition and recovery ISO path unchanged. Clone the active root into an inactive `A` or `B` directory on the same filesystem, apply independently signed content and offline packages only to that clone, boot it once through a fixed GRUB entry and initramfs selector, then change the saved default only after a durable bounded health check. Persistent atomic JSON state and fsynced JSONL events reconcile every power-loss point.

**Tech Stack:** Python 3 standard library, Bash, `gpgv`, `rsync`, `tar`/`zstd` object reading, dpkg/apt offline package operations, initramfs-tools, GRUB 2, systemd, polkit, JSON Schema, Python `unittest`, shell syntax/static gates, BIOS and UEFI virtual machines.

---

## Delivery Rules

- This plan does not authorize production implementation in the current task.
- Implement in a dedicated worktree after the design document is approved.
- Write a failing contract test before each production change.
- Commit after each task; do not mix Terra, Luna, boot, trust, and release-gate
  changes in one commit.
- Never modify or relax the existing recovery ISO same-disk guard.
- Never use Calamares, a partitioning tool, `mkfs`, `resize2fs`, a kernel
  replacement, DKMS, or a third-party driver in the transaction path.
- Stop the release if any power-loss, BIOS, UEFI, 26.3.2 bootstrap, or recovery
  non-regression contract is not proven.

## Planned File Map

### Cross-layer contracts

- Create: `contracts/ota/discovery-v1.schema.json`
- Create: `contracts/ota/cli-v1.schema.json`
- Create: `contracts/ota/transaction-manifest-v1.schema.json`
- Create: `contracts/ota/content-index-v1.schema.json`
- Create: `contracts/ota/state-v1.schema.json`
- Create: `contracts/ota/fixtures/*.json`
- Modify: `.github/CODEOWNERS`

### Core transaction runtime

- Create: `assets/ming-transaction-state.py`
- Create: `assets/ming-transaction-verify.py`
- Create: `assets/ming-transaction-slot.py`
- Create: `assets/ming-transaction-apply.py`
- Create: `assets/ming-transaction-health.py`
- Create: `assets/ming-transaction-rollback.py`
- Create: `assets/ming-update-cli.py`
- Create: `assets/initramfs/ming-transaction-hook`
- Create: `assets/initramfs/ming-transaction-local-premount`
- Create: `assets/grub/40_ming_transaction`
- Create: `assets/systemd/ming-transaction-health.service`
- Create: `assets/systemd/ming-transaction-reconcile.service`
- Create: `assets/polkit/org.mingos.update.policy`
- Create: `assets/trust/ming-ota-key-policy.json`
- Add reviewed binary artifact: `assets/trust/ming-ota-release-keyring.gpg`

### Bootstrap and release tooling

- Create: `assets/bootstrap/ming-ota-bootstrap.postinst`
- Create: `assets/bootstrap/ming-ota-bootstrap.prerm`
- Create: `tools/build-ming-ota-bootstrap.sh`
- Create: `tools/build-ming-transaction-payload.py`
- Create: `tools/sign-ming-transaction-release.sh`
- Create: `tools/verify-ming-transaction-release.sh`

### Build integration

- Modify: `modules/01_base.sh`
- Modify: `modules/06_ota_update.sh`
- Modify: `build_onion_os.sh`

### Luna presentation boundary

- Modify: `assets/ming-settings.py`
- Create: `tests/test_update_cli_contract.py`
- Modify: `tests/test_update_single_flow.py`

### Tests

- Create: `tests/test_transaction_contracts.py`
- Create: `tests/test_transaction_state.py`
- Create: `tests/test_transaction_verify.py`
- Create: `tests/test_transaction_slot.py`
- Create: `tests/test_transaction_apply.py`
- Create: `tests/test_transaction_boot_contract.py`
- Create: `tests/test_transaction_health.py`
- Create: `tests/test_transaction_powerloss.py`
- Create: `tests/test_transaction_bootstrap.py`
- Create: `tests/test_transaction_terra_contract.py`
- Create: `tests/test_transaction_forbidden_commands.py`
- Modify: `tests/test_ota_target_guard.py`
- Modify: `tests/test_ota_backup.py`
- Modify: `tests/test_release_gate.py`

### Terra adapter boundary

Terra's production repository path is intentionally not fixed by this plan.
Terra consumes `contracts/ota/discovery-v1.schema.json` and shared fixtures,
then changes only its discovery route, immutable artifact publisher, and route
contract tests. It does not share a commit with the core runtime.

## Ownership Boundary

The following tasks are **core-owner-only**: 1 through 10, 13, and the
security-sensitive portions of 14 and 15. The corresponding paths must require
core OTA owner approval in `.github/CODEOWNERS`:

```text
/contracts/ota/                         @ming-os/ota-core
/assets/ming-transaction-*              @ming-os/ota-core
/assets/ming-update-cli.py               @ming-os/ota-core
/assets/initramfs/                       @ming-os/ota-core
/assets/grub/40_ming_transaction         @ming-os/ota-core
/assets/systemd/ming-transaction-*       @ming-os/ota-core
/assets/polkit/org.mingos.update.policy  @ming-os/ota-core @ming-os/security
/assets/trust/                           @ming-os/ota-core @ming-os/security
/assets/bootstrap/                       @ming-os/ota-core @ming-os/security
/tools/*ming-transaction*                @ming-os/ota-core @ming-os/release
/tools/*ming-ota-bootstrap*              @ming-os/ota-core @ming-os/release
/modules/01_base.sh                      @ming-os/core @ming-os/ota-core
/modules/06_ota_update.sh                @ming-os/ota-core
/build_onion_os.sh                       @ming-os/core @ming-os/release
```

Luna may change only the settings presentation and its UI tests. Terra may
change only discovery/publication adapters and their contract tests. A schema,
error code, CLI argument, trust, path-policy, slot, boot, or state-machine
change always returns to core review.

## Milestone 1: Freeze Contracts and Trust

### Task 1: Add schemas, fixtures, and ownership gates

**Files:**
- Create: `contracts/ota/*.schema.json`
- Create: `contracts/ota/fixtures/*.json`
- Modify: `.github/CODEOWNERS`
- Test: `tests/test_transaction_contracts.py`

- [ ] **Step 1: Write failing schema tests.**

  Add tests named:

  ```text
  test_valid_discovery_fixture_matches_v1
  test_valid_cli_success_and_error_fixtures_match_v1
  test_valid_manifest_and_content_index_match_v1
  test_state_fixture_rejects_unknown_transition_fields
  test_duplicate_json_keys_are_rejected_before_schema_validation
  test_unknown_major_schema_is_rejected
  ```

  The tests must load JSON with a duplicate-key-detecting parser, validate exact
  required fields and types, and assert that schema v1 permits additive unknown
  response fields only where the design explicitly allows them.

- [ ] **Step 2: Prove the tests fail before contracts exist.**

  Run:

  ```powershell
  $env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-ota-contract-pycache'
  python -m unittest tests.test_transaction_contracts -v
  ```

  Expected: FAIL because the schemas and fixtures do not exist.

- [ ] **Step 3: Add the five schemas and representative fixtures.**

  Freeze the exact names from the design: `ming.update.discovery.v1`,
  `ming.update.cli.v1`, `ming.transaction-manifest.v1`,
  `ming.content-index.v1`, and `ming.transaction-state.v1`. Include fixtures for
  no update, bootstrap required, staging, reboot required, committed, rollback,
  signature failure, space refusal, and unsupported protocol.

- [ ] **Step 4: Protect core paths.**

  Add the ownership entries listed above. Add a test that reads CODEOWNERS and
  proves every trust, initramfs, GRUB, state, signer, and schema path is covered.

- [ ] **Step 5: Run contract tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_contracts -v
  git diff --check
  git add contracts/ota tests/test_transaction_contracts.py .github/CODEOWNERS
  git commit -m "test: freeze transactional OTA contracts"
  ```

  Expected: all contract tests PASS; only contract and ownership files are in
  the commit.

### Task 2: Implement independent artifact verification

**Files:**
- Create: `assets/ming-transaction-verify.py`
- Create: `assets/trust/ming-ota-key-policy.json`
- Add: `assets/trust/ming-ota-release-keyring.gpg`
- Test: `tests/test_transaction_verify.py`

- [ ] **Step 1: Write failing trust tests.**

  Cover three independent detached signatures; pinned primary/subkey
  fingerprint; expiry; wrong source version/architecture/kernel; URL policy;
  size/hash mismatch; revoked key policy; missing signature; manifest/index
  release-ID mismatch; duplicate keys; and expired manifest.

  Include a matrix where exactly one of manifest, payload, or index has a bad
  signature. All three rows must fail with `E_MANIFEST_SIGNATURE` or
  `E_ARTIFACT_SIGNATURE` before any slot-manager call.

- [ ] **Step 2: Verify tests fail.**

  ```powershell
  python -m unittest tests.test_transaction_verify -v
  ```

  Expected: FAIL because verifier and trust policy are absent.

- [ ] **Step 3: Implement pinned `gpgv` verification and normalization.**

  Use argument arrays, a 15-second subprocess timeout, a root-owned keyring, no
  keyserver, and no shell. Return a normalized immutable plan containing only
  validated values. Do not expose URLs or paths as commands.

- [ ] **Step 4: Validate trust artifacts and permissions.**

  Assert the keyring contains only reviewed public material, the policy pins the
  expected fingerprints, neither file is group/world writable, and no private
  key packet is present.

- [ ] **Step 5: Run and commit.**

  ```powershell
  python -m unittest tests.test_transaction_verify -v
  python -m py_compile assets/ming-transaction-verify.py
  git diff --check
  git add assets/ming-transaction-verify.py assets/trust tests/test_transaction_verify.py
  git commit -m "feat: verify signed transaction artifacts"
  ```

  Expected: all three signature layers fail closed and valid fixtures normalize
  to the contract schema.

### Task 3: Validate content paths and build deterministic payloads

**Files:**
- Create: `tools/build-ming-transaction-payload.py`
- Create: `tools/sign-ming-transaction-release.sh`
- Create: `tools/verify-ming-transaction-release.sh`
- Modify: `tests/test_transaction_verify.py`
- Test: `tests/test_transaction_apply.py`

- [ ] **Step 1: Add failing content-policy tests.**

  Reject absolute/traversal paths, duplicate normalized paths, unsafe symlinks,
  hard links, special files, sockets, FIFOs, unexpected setuid files, `/home`,
  `/boot`, transaction storage, account databases, machine ID, network secrets,
  SSH keys, `/lib/modules`, `/usr/lib/modules`, kernel/initramfs/GRUB packages,
  DKMS, and unindexed archive members.

- [ ] **Step 2: Run the focused tests and confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_verify tests.test_transaction_apply -v
  ```

  Expected: FAIL on missing builder/content parser.

- [ ] **Step 3: Implement a hash-object payload builder.**

  Store only regular blobs under a deterministic SHA256 object name. Generate a
  canonical index; never depend on archive member paths during installation.
  Enforce `replace`, `replace-if-unmodified`, and `preserve` only. Include each
  offline DEB by name, exact version, architecture, and blob hash.

- [ ] **Step 4: Implement offline signing and verification scripts.**

  Signing must require an explicitly supplied offline key, write three detached
  signatures, and never upload. Verification must run without network and fail
  if any one signature or manifest-pinned hash is wrong.

- [ ] **Step 5: Prove reproducibility and commit.**

  Build the same fixture twice with fixed metadata and assert identical index
  and payload SHA256. Then run:

  ```powershell
  python -m unittest tests.test_transaction_verify tests.test_transaction_apply -v
  bash -n tools/sign-ming-transaction-release.sh tools/verify-ming-transaction-release.sh
  git diff --check
  git add tools tests/test_transaction_verify.py tests/test_transaction_apply.py
  git commit -m "feat: build deterministic OTA payloads"
  ```

## Milestone 2: Durable State and Inactive Slot

### Task 4: Implement the persistent state engine

**Files:**
- Create: `assets/ming-transaction-state.py`
- Test: `tests/test_transaction_state.py`
- Test: `tests/test_transaction_powerloss.py`

- [ ] **Step 1: Write the complete transition-table tests.**

  Test every legal edge in the design and every illegal shortcut. Require one
  active nonterminal transaction, generation compare-and-swap, writer role,
  evidence hashes, terminal-state idempotence, lock exclusivity, atomic rename,
  file fsync, directory fsync, and a matching fsynced JSONL event.

- [ ] **Step 2: Add fault injection after each durable operation.**

  Parameterize interruption after temporary-state write, file fsync, rename,
  directory fsync, event append, and event fsync. On reload, reconciliation must
  produce the previous complete generation or the new complete generation,
  never mixed fields.

- [ ] **Step 3: Confirm tests fail.**

  ```powershell
  python -m unittest tests.test_transaction_state tests.test_transaction_powerloss -v
  ```

  Expected: FAIL because the state engine is absent.

- [ ] **Step 4: Implement atomic state and event persistence.**

  Use same-directory temporary files, restrictive permissions, `os.replace`,
  fsync of file and parent, monotonic generation, boot ID, monotonic time, and
  UTC wall time. Reject symlinked state paths and unexpected ownership.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_state tests.test_transaction_powerloss -v
  python -m py_compile assets/ming-transaction-state.py
  git diff --check
  git add assets/ming-transaction-state.py tests/test_transaction_state.py tests/test_transaction_powerloss.py
  git commit -m "feat: add durable OTA state machine"
  ```

### Task 5: Plan space and clone the active root safely

**Files:**
- Create: `assets/ming-transaction-slot.py`
- Test: `tests/test_transaction_slot.py`

- [ ] **Step 1: Write failing slot-planner tests.**

  Cover `legacy -> B`, `A -> B`, `B -> A`, one active transaction, inactive-slot
  refusal, mounted-subtree exclusion, physical-root detection, separate mount
  exclusion, swap-file handling, sparse files, ACL/xattr/hard-link preservation,
  transaction-store recursion, and exact free-space accounting.

  Required bytes must include active-root allocated bytes after exclusions,
  payload/index bytes, offline package temporary space, metadata overhead, and
  manifest reserve. The test must assert `E_SPACE` occurs before slot directory
  promotion or GRUB invocation.

- [ ] **Step 2: Write protected-state fixture tests.**

  Seed accounts, machine ID, NetworkManager secrets, SSH host keys, Bluetooth
  state, dpkg records, third-party desktop entries, and `/home`. The cloned root
  must preserve the first group exactly and omit `/home`, `/boot`, and update
  storage.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_slot -v
  ```

  Expected: FAIL because the slot manager is absent.

- [ ] **Step 4: Implement preflight, clone, final sync, and seals.**

  Acquire Ming and dpkg locks; derive exclusions from mountinfo; clone metadata
  faithfully; perform a bounded final synchronization; write `slot.json`,
  `base-seal.json`, and a protected-path seal; atomically promote the candidate
  directory. Record protected-state generations for the later arm-time recheck.
  A timeout or changing package database fails staging.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_slot -v
  python -m py_compile assets/ming-transaction-slot.py
  git diff --check
  git add assets/ming-transaction-slot.py tests/test_transaction_slot.py
  git commit -m "feat: stage inactive root slots"
  ```

### Task 6: Apply files and packages only inside the candidate

**Files:**
- Create: `assets/ming-transaction-apply.py`
- Modify: `tests/test_transaction_apply.py`
- Modify: `tests/test_transaction_slot.py`

- [ ] **Step 1: Write failing applicator tests.**

  Prove that blob writes use a candidate-root file descriptor, reject symlink
  races, preserve mode/uid/gid/xattrs, honor all three config policies, process
  deletions safely, block network and service starts during offline dpkg, use
  `--force-confold`, and reject a package not listed in the signed index.

- [ ] **Step 2: Add active-root immutability tests.**

  Hash the active fixture before and after success, bad blob, package failure,
  injected interruption, and protected-path mutation. The active fixture and
  `/home` must remain unchanged in every row.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_apply -v
  ```

  Expected: FAIL because no applicator exists.

- [ ] **Step 4: Implement candidate-only application.**

  Open paths relative to a verified candidate directory, reject traversal at
  every component, materialize regular blobs atomically, run only indexed DEBs
  in an offline candidate context with `policy-rc.d`, and restore temporary
  policy/mounts on every exit. Run dpkg audit and protected-path comparison, then
  write `candidate-seal.json` before `staged`.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_apply tests.test_transaction_slot -v
  python -m py_compile assets/ming-transaction-apply.py
  git diff --check
  git add assets/ming-transaction-apply.py tests/test_transaction_apply.py tests/test_transaction_slot.py
  git commit -m "feat: apply updates to inactive slots"
  ```

## Milestone 3: Transaction Boot, Health, and Rollback

### Task 7: Install fixed GRUB slot entries and one-shot arming

**Files:**
- Create: `assets/grub/40_ming_transaction`
- Modify: `modules/01_base.sh`
- Create: `tests/test_transaction_boot_contract.py`

- [ ] **Step 1: Write failing generated-GRUB tests.**

  Require stable IDs `ming-legacy`, `ming-slot-a`, and `ming-slot-b`; fixed
  `ming.slot` values; the current kernel/initramfs; no release URL, tx path, ISO,
  Calamares, partitioning command, `nomodeset`, or kernel replacement. Verify
  the existing normal and recovery entries still exist.

- [ ] **Step 2: Write arming-order tests with a fake grubenv.**

  Assert `staged` evidence precedes transaction metadata, `grub-reboot`, and
  readback; `armed` is persisted last. Inject failure after each operation and
  assert the saved default remains previous. Change a protected-state generation
  after staging and assert arming returns to staging before any grubenv write.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_boot_contract -v
  ```

  Expected: FAIL because fixed slot entries do not exist.

- [ ] **Step 4: Add bootstrap-installed GRUB entries and coordinator calls.**

  Use exact entry IDs, array-safe command invocation, a global lock, bounded
  grubenv fields, and readback. Do not modify the saved default while arming.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_boot_contract -v
  bash -n assets/grub/40_ming_transaction modules/01_base.sh
  git diff --check
  git add assets/grub/40_ming_transaction modules/01_base.sh tests/test_transaction_boot_contract.py
  git commit -m "feat: add one-shot OTA slot entries"
  ```

### Task 8: Select or reject a slot in initramfs

**Files:**
- Create: `assets/initramfs/ming-transaction-hook`
- Create: `assets/initramfs/ming-transaction-local-premount`
- Modify: `modules/01_base.sh`
- Modify: `tests/test_transaction_boot_contract.py`
- Create: `tests/test_transaction_forbidden_commands.py`

- [ ] **Step 1: Write failing initramfs contract tests.**

  Cover valid `legacy/A/B`, unknown slot, malformed state, stale generation,
  wrong sentinel/seal/kernel, exhausted attempt, missing home/store bind, mount
  timeout, candidate refusal, and direct previous-slot fallback. Require a
  15-second total deadline and 2-second probe deadlines.

  Prove the slot in `current.json` boots normally without an armed transaction,
  while a different slot boots only when it is the exact armed one-shot
  candidate. The selector must never scan for a merely bootable alternative.

- [ ] **Step 2: Add forbidden-command tests.**

  Scan generated and source initramfs/transaction scripts for Calamares,
  partitioners, `mkfs*`, filesystem resizers, kernel installation, DKMS, and
  recovery ISO dispatch. Also run with PATH stubs that fail if invoked.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_boot_contract tests.test_transaction_forbidden_commands -v
  ```

  Expected: FAIL because the hook and selector are absent.

- [ ] **Step 4: Implement the bounded selector.**

  Mount the physical root, validate the fixed slot and transaction evidence,
  increment one boot attempt durably, bind candidate `/`, physical `/home`,
  physical `/boot`, and the physical transaction store, then continue normal
  switch-root. On any candidate error, record evidence and select the previous
  committed root without an installer or disk mutation.

- [ ] **Step 5: Build and inspect an initramfs fixture.**

  ```bash
  update-initramfs -u -k "$(uname -r)"
  lsinitramfs "/boot/initrd.img-$(uname -r)" | grep ming-transaction
  ```

  Expected: hook, selector, schemas/runtime dependencies, and no private signing
  material are present.

- [ ] **Step 6: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_boot_contract tests.test_transaction_forbidden_commands -v
  bash -n assets/initramfs/ming-transaction-hook assets/initramfs/ming-transaction-local-premount modules/01_base.sh
  git diff --check
  git add assets/initramfs modules/01_base.sh tests/test_transaction_boot_contract.py tests/test_transaction_forbidden_commands.py
  git commit -m "feat: boot transactional root slots"
  ```

### Task 9: Gate commit on deterministic post-boot health

**Files:**
- Create: `assets/ming-transaction-health.py`
- Create: `assets/systemd/ming-transaction-health.service`
- Create: `assets/systemd/ming-transaction-reconcile.service`
- Test: `tests/test_transaction_health.py`

- [ ] **Step 1: Write failing health-profile tests.**

  Cover selected root, shared-home mount, candidate/protected seals, dpkg audit,
  essential units, NetworkManager/D-Bus/logind, audio prerequisites, display
  manager dry validation, Ming runtime self-checks, forbidden boot changes, one
  60-second total timeout, and per-check timeouts.

  Each failure must map to exactly one `E_HEALTH_*` code, write `failure.json`,
  arm rollback, and leave the previous saved entry intact.

- [ ] **Step 2: Test graphical-login ordering.**

  Parse systemd dependencies and assert rollback-critical health completes
  before the display manager permits normal graphical login. Verify post-login
  graphics observation is diagnostic only and cannot create a rollback loop.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_health -v
  ```

  Expected: FAIL because health runtime and units are absent.

- [ ] **Step 4: Implement health token and service ordering.**

  Write the token only after all required checks pass. Bind it to transaction
  ID, candidate slot, candidate seal, boot ID, and state generation. Ensure all
  commands use fixed argument arrays and timeouts.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_health -v
  python -m py_compile assets/ming-transaction-health.py
  systemd-analyze verify assets/systemd/ming-transaction-health.service assets/systemd/ming-transaction-reconcile.service
  git diff --check
  git add assets/ming-transaction-health.py assets/systemd tests/test_transaction_health.py
  git commit -m "feat: gate OTA commit on system health"
  ```

### Task 10: Commit idempotently or roll back automatically

**Files:**
- Create: `assets/ming-transaction-rollback.py`
- Modify: `assets/ming-transaction-health.py`
- Modify: `assets/ming-transaction-state.py`
- Modify: `tests/test_transaction_powerloss.py`
- Modify: `tests/test_transaction_health.py`

- [ ] **Step 1: Add the full reconciliation matrix as failing tests.**

  Test every row in the design's power-loss table, especially all four
  combinations of a valid/missing health token and candidate/previous GRUB
  saved entry while state is `committing`.

- [ ] **Step 2: Add rollback-log tests.**

  Require transaction ID, slots, generation, boot ID, stable error, check name,
  GRUB readback, timestamps, and approved log paths. Assert user file contents,
  SSIDs, passwords, and secrets never appear.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_powerloss tests.test_transaction_health -v
  ```

  Expected: FAIL on missing reconciliation/rollback implementation.

- [ ] **Step 4: Implement ordered commit and rollback.**

  Commit: token, `committing`, saved-entry write, readback, `current.json`, then
  `committed`. Rollback: preserve previous saved entry, write failure evidence,
  select previous root, record `rolling_back -> rolled_back`, and quarantine the
  candidate. Retry no candidate automatically in v1.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_powerloss tests.test_transaction_health -v
  python -m py_compile assets/ming-transaction-state.py assets/ming-transaction-health.py assets/ming-transaction-rollback.py
  git diff --check
  git add assets/ming-transaction-state.py assets/ming-transaction-health.py assets/ming-transaction-rollback.py tests/test_transaction_powerloss.py tests/test_transaction_health.py
  git commit -m "feat: reconcile and roll back OTA boots"
  ```

## Milestone 4: Public CLI, Bootstrap, Terra, and Luna

### Task 11: Expose only the stable CLI/JSON interface

**Files:**
- Create: `assets/ming-update-cli.py`
- Create: `assets/polkit/org.mingos.update.policy`
- Modify: `modules/06_ota_update.sh`
- Create: `tests/test_update_cli_contract.py`
- Modify: `tests/test_update_single_flow.py`

- [ ] **Step 1: Write failing command-contract tests.**

  Cover exactly `status`, `check`, `apply --release-id --manifest-sha256`,
  `cancel --transaction`, `doctor`, and `logs --transaction`, each with `--json`.
  Validate the common envelope and exit/error mapping from the design. Assert no
  update is success, cancel is refused after `armed`, and caller URLs/paths are
  rejected.

- [ ] **Step 2: Test privilege boundaries.**

  Read-only commands run unprivileged. Apply/cancel require only the dedicated
  polkit action. Root must reopen and revalidate its own cached manifest by
  release ID and hash. No `NOPASSWD: ALL`, caller-selected executable, shell
  fragment, trust path, slot, or GRUB entry crosses the boundary.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_update_cli_contract tests.test_update_single_flow -v
  ```

  Expected: FAIL because CLI v1 and policy are absent.

- [ ] **Step 4: Implement CLI adapter over core components.**

  Keep state logic out of the CLI. Emit one JSON object on stdout, log private
  detail separately, preserve exit classes, and return only validated log paths.
  Dispatch recovery ISO and transaction delivery before privilege-sensitive
  work; do not share safety exceptions.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_update_cli_contract tests.test_update_single_flow -v
  python -m py_compile assets/ming-update-cli.py
  bash -n modules/06_ota_update.sh
  git diff --check
  git add assets/ming-update-cli.py assets/polkit modules/06_ota_update.sh tests/test_update_cli_contract.py tests/test_update_single_flow.py
  git commit -m "feat: expose transactional update CLI"
  ```

### Task 12: Preserve the signed 26.3.2 bootstrap path

**Files:**
- Create: `assets/bootstrap/ming-ota-bootstrap.postinst`
- Create: `assets/bootstrap/ming-ota-bootstrap.prerm`
- Create: `tools/build-ming-ota-bootstrap.sh`
- Modify: `modules/06_ota_update.sh`
- Test: `tests/test_transaction_bootstrap.py`

- [ ] **Step 1: Write failing bootstrap tests.**

  Use a 26.3.2 fixture and test valid signed bootstrap, absent/bad signature,
  wrong fingerprint, unsupported architecture, interrupted postinst, failed
  initramfs generation, failed GRUB readback, capability-marker ordering, and
  uninstall refusal after `armed`.

- [ ] **Step 2: Test trust continuity.**

  Assert the bootstrap is verified by the already trusted 26.3.2 verifier
  before dpkg, installs only public trust material, and advertises
  `transactional-slot-v1` only after CLI, initramfs, GRUB, systemd, schemas, and
  readbacks succeed.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_transaction_bootstrap -v
  ```

  Expected: FAIL because package scripts/build tool are absent.

- [ ] **Step 4: Build a deterministic minimal bootstrap DEB.**

  Install the transaction engine and fixed boot integration without changing
  the active saved entry. Make postinst idempotent. On partial failure, withhold
  the capability marker and keep `legacy` bootable.

- [ ] **Step 5: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_transaction_bootstrap -v
  bash -n assets/bootstrap/ming-ota-bootstrap.postinst assets/bootstrap/ming-ota-bootstrap.prerm tools/build-ming-ota-bootstrap.sh modules/06_ota_update.sh
  git diff --check
  git add assets/bootstrap tools/build-ming-ota-bootstrap.sh modules/06_ota_update.sh tests/test_transaction_bootstrap.py
  git commit -m "feat: bootstrap transactional OTA on 26.3.2"
  ```

### Task 13: Give Terra a locator-only discovery contract

**Files:**
- Modify in Terra repository: update discovery route and immutable publisher
- Consume: `contracts/ota/discovery-v1.schema.json`
- Consume: `contracts/ota/fixtures/*.json`
- Test in Ming repository: `tests/test_transaction_terra_contract.py`

- [ ] **Step 1: Write failing provider/consumer contract tests.**

  Cover no update, unbootstrapped 26.3.2, bootstrapped 26.3.2, wrong arch,
  unsupported capability, expired release, immutable object URLs, and exact
  release ID. Assert responses contain no commands, local paths, trust keys,
  GRUB names, or transaction state.

- [ ] **Step 2: Confirm the fixtures fail against current Terra output.**

  Run the Terra repository's route tests and:

  ```powershell
  python -m unittest tests.test_transaction_terra_contract -v
  ```

  Expected: FAIL until Terra emits the frozen discovery schema.

- [ ] **Step 3: Implement capability-gated discovery in Terra only.**

  An unbootstrapped 26.3.2 client receives the signed bootstrap action. A client
  reporting the minimum bootstrap and `transactional-slot-v1` receives immutable
  manifest/signature locators. Terra stores no private signing key and performs
  no local-state or slot decisions.

- [ ] **Step 4: Run both sides' contract tests and commit in Terra.**

  Expected: both repositories validate the same fixture bytes. Do not deploy.

### Task 14: Give Luna a presentation-only update flow

**Files:**
- Modify: `assets/ming-settings.py`
- Modify: `tests/test_update_single_flow.py`
- Modify: `tests/test_update_cli_contract.py`

- [ ] **Step 1: Write failing Luna boundary tests.**

  Feed every CLI fixture to the settings page. Assert one check/update action,
  progress by phase, reboot status, space refusal, bootstrap action, rollback
  result, stable localized error mapping, and unknown-schema refusal.

  Static assertions must prove Luna does not parse engine logs, write the state
  store, run GRUB/initramfs commands, install keys, choose slots, accept URLs,
  or call recovery code for a transaction release.

- [ ] **Step 2: Confirm failure.**

  ```powershell
  python -m unittest tests.test_update_single_flow tests.test_update_cli_contract -v
  ```

  Expected: FAIL until settings consumes CLI v1.

- [ ] **Step 3: Implement fixture-driven UI mapping.**

  Call only public CLI commands. Pass release ID and manifest hash exactly as
  returned by `check`; allow root to revalidate. Poll status without inferring
  state. Use `message_key` for text and only open CLI-approved log paths.

- [ ] **Step 4: Run tests and commit.**

  ```powershell
  python -m unittest tests.test_update_single_flow tests.test_update_cli_contract -v
  python -m py_compile assets/ming-settings.py
  git diff --check
  git add assets/ming-settings.py tests/test_update_single_flow.py tests/test_update_cli_contract.py
  git commit -m "feat: present transactional OTA status in settings"
  ```

## Milestone 5: Build Gates and Fault-Injection Qualification

### Task 15: Integrate runtime assets and release gates

**Files:**
- Modify: `modules/01_base.sh`
- Modify: `modules/06_ota_update.sh`
- Modify: `build_onion_os.sh`
- Modify: `tests/test_release_gate.py`
- Modify: `tests/test_ota_target_guard.py`
- Modify: `tests/test_ota_backup.py`

- [ ] **Step 1: Write failing rootfs/release-gate tests.**

  Require all core assets, schemas, public keyring, restrictive permissions,
  polkit policy, systemd ordering, initramfs contents, fixed GRUB entries, CLI
  self-test, and no private key. Require full and resume builds to run the same
  transaction gates.

- [ ] **Step 2: Add explicit recovery non-regression tests.**

  Keep every current independent-media, backup UUID, physical-disk ancestry,
  manifest location, and Calamares target guard assertion. Add a test that no
  transaction option or same-root exception appears in recovery functions.

- [ ] **Step 3: Confirm failure.**

  ```powershell
  python -m unittest tests.test_release_gate tests.test_ota_target_guard tests.test_ota_backup -v
  ```

  Expected: FAIL until build integration deploys and validates transaction
  assets; existing recovery tests must continue to pass throughout.

- [ ] **Step 4: Deploy assets through build modules.**

  Install dependencies, files, permissions, units, hook, GRUB fragment, schemas,
  and trust material. Generate initramfs/GRUB and read back. Fail the build on
  any missing dependency, signature fixture failure, wrong permission, invalid
  unit, missing hook, or recovery guard regression.

- [ ] **Step 5: Run static and full tests.**

  ```powershell
  $env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-transaction-final-pycache'
  python -m unittest discover -s tests -v
  bash -n build_onion_os.sh resume_build.sh modules/*.sh assets/initramfs/* assets/grub/* assets/bootstrap/* tools/*.sh
  python -m py_compile assets/*.py tests/*.py tools/*.py
  git diff --check
  ```

  Expected: PASS with no generated cache, private key, ISO, or server state in
  the commit.

- [ ] **Step 6: Commit build integration.**

  ```bash
  git add modules/01_base.sh modules/06_ota_update.sh build_onion_os.sh tests/test_release_gate.py tests/test_ota_target_guard.py tests/test_ota_backup.py
  git commit -m "build: gate transactional OTA runtime"
  ```

### Task 16: Run power-loss tests at every durable boundary

**Files:**
- Modify: `tests/test_transaction_powerloss.py`
- Create: `tests/fixtures/transaction-root/README.md`
- Create outside production image: `tools/test-transaction-powerloss.ps1`

- [ ] **Step 1: Enumerate fault points.**

  Include artifact promotion, state temp write/fsync/rename/dir-fsync, event
  append/fsync, slot clone promotion, each payload object, dpkg start/end,
  candidate seal, GRUB metadata write/readback, `grub-reboot`, `armed`,
  initramfs boot-attempt record, health token, `committing`, saved-entry write,
  saved-entry readback, `current.json`, rollback record, and quarantine.

- [ ] **Step 2: Run process-kill tests against temporary roots.**

  For every fault point, kill the worker after the durable boundary, restart the
  reconciler, and assert exactly one safe terminal/continuable state. Hash the
  active root and `/home` before and after.

- [ ] **Step 3: Run VM hard-power tests.**

  Execute the same critical points in BIOS and UEFI VMs by powering off the VM,
  not by orderly shutdown. On restart, assert either the old committed slot or a
  health-confirmed candidate; never a rescue shell, installer, or unconfirmed
  saved default.

- [ ] **Step 4: Record machine-readable evidence.**

  Save transaction state/events, GRUB readback, selected slot, root UUID,
  `/home` hash, and health/rollback result. Do not record secrets.

- [ ] **Step 5: Run and commit the harness.**

  ```powershell
  python -m unittest tests.test_transaction_powerloss -v
  powershell -ExecutionPolicy Bypass -File tools/test-transaction-powerloss.ps1 -Mode Fixture
  git diff --check
  git add tests/test_transaction_powerloss.py tests/fixtures/transaction-root/README.md tools/test-transaction-powerloss.ps1
  git commit -m "test: fault-inject transactional OTA"
  ```

### Task 17: Qualify BIOS, UEFI, bootstrap, and rollback end to end

**Files:**
- Create: `docs/validation/ming-transactional-ota-qualification.md`
- No production code changes are allowed during evidence collection.

- [ ] **Step 1: Build test artifacts, not a public release.**

  Produce an internal 26.3.2 bootstrap DEB and a locally signed fixture release
  with a disposable test key. Record hashes. Do not use or copy the production
  signing key.

- [ ] **Step 2: Run the BIOS matrix.**

  Test clean 26.3.2, signed bootstrap, insufficient space, successful update,
  staging interruption, candidate boot interruption, each health failure,
  rollback, second update `B -> A`, and manual previous-slot entry.

- [ ] **Step 3: Run the identical UEFI matrix.**

  Verify saved and one-shot GRUB behavior through firmware reboot, not only
  `grub-editenv` mocks.

- [ ] **Step 4: Verify preservation.**

  Before and after success/rollback, compare `/home`, accounts, machine ID,
  NetworkManager secrets, SSH host keys, Bluetooth state, installed third-party
  packages, and desktop entries. The OTA engine must not change `/home`.

- [ ] **Step 5: Verify health and login gating.**

  Healthy candidate commits within 60 seconds before normal graphical login.
  Broken package state, essential unit, root mount, seal, or desktop offline
  self-check automatically returns to the previous slot. Post-login GPU/session
  diagnostics do not trigger a rollback loop.

- [ ] **Step 6: Verify recovery isolation.**

  Boot the recovery ISO and prove same-disk preservation is still rejected while
  independent media succeeds. Confirm transaction storage is never accepted as
  recovery backup media.

- [ ] **Step 7: Run the final gate.**

  ```powershell
  $env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-transaction-release-pycache'
  python -m unittest discover -s tests -v
  bash -n build_onion_os.sh resume_build.sh modules/*.sh assets/initramfs/* assets/grub/* assets/bootstrap/* tools/*.sh
  python -m py_compile assets/*.py tests/*.py tools/*.py
  git diff --check
  git status --short
  ```

  Expected: all tests pass; the qualification document contains BIOS and UEFI
  evidence; there is no ISO/publication/private key; recovery guards are intact.

- [ ] **Step 8: Commit validation evidence.**

  ```bash
  git add docs/validation/ming-transactional-ota-qualification.md
  git commit -m "docs: qualify transactional OTA rollback"
  ```

## Release Stop Conditions

Do not build or publish a public ISO/OTA if any condition below is true:

- a signature layer is optional or shares a permissive fallback;
- private signing material exists on Terra, in the repository, or in an image;
- active-root or `/home` hashes change during a fault-injection test;
- space refusal occurs after GRUB or candidate promotion;
- a power-loss point can leave an unconfirmed candidate as saved default;
- candidate health can be bypassed to reach normal graphical login;
- rollback retries a failed candidate automatically;
- transaction code references Calamares, partitioning, formatting, resizing,
  kernel replacement, DKMS, or third-party drivers;
- 26.3.2 capability is advertised before bootstrap readback succeeds;
- recovery ISO accepts same-disk preservation media;
- Terra emits commands/keys/local paths or Luna writes core state;
- BIOS and UEFI end-to-end evidence is incomplete.

## Stable Handoff Boundaries

### Terra

Terra can proceed independently once Task 1 fixtures are frozen. Its only
required behavior is capability-gated discovery with immutable manifest and
signature locators. It does not need core implementation details or a local
transaction state fixture.

### Luna

Luna can proceed independently once CLI fixtures from Tasks 1 and 11 are
frozen. It can build all update states from static JSON. It must not wait for
boot, initramfs, GRUB, signing, or slot internals to be complete.

### Core

Core owns schema evolution, trust, artifacts, state, slot mutation, initramfs,
GRUB, health, rollback, bootstrap, and release gates. Terra and Luna integration
cannot merge until the shared contract suite passes unchanged on both sides.
