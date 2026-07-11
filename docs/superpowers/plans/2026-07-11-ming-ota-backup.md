# Ming OTA, APT And User Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make patch updates and major OTA staging honest, diagnosable and safe for user data.

**Architecture:** Move backup logic into a standalone script with test roots, require a complete verified backup before destructive staging, pass backup identity through GRUB kernel parameters, and restore from the Calamares installed-identity step.

**Tech Stack:** Bash, rsync, jq, sha256sum, GRUB custom.cfg, Calamares shellprocess, APT/dpkg.

---

### Task 1: Backup/Restore Engine

**Files:**
- Create: `assets/ming-ota-backup.sh`
- Create: `tests/test_ota_backup.py`

- [ ] Write failing tests using temporary source/destination trees for free-space rejection, interrupted backup, manifest completion, content verification, modes, symlinks and restore.
- [ ] Run `python -m unittest tests.test_ota_backup -v`; expect missing-script failures.
- [ ] Implement `backup`, `verify`, `restore` and `doctor` commands with overridable roots, `rsync -aHAX --numeric-ids`, JSON manifests and atomic completion markers.
- [ ] Reject source/destination overlap, target-disk destinations and paths without ten percent headroom.
- [ ] Run backup tests; expect all tests to pass.

### Task 2: OTA CLI And APT Safety

**Files:**
- Modify: `modules/06_ota_update.sh`
- Extend: `tests/test_ota_backup.py`

- [ ] Write failing tests for `doctor`, user-cache discovery, signed patch manifests, dpkg recovery, package-name validation and backup-required major staging.
- [ ] Install the backup engine and add `ming-update doctor`.
- [ ] Validate patch package names against Debian package syntax and reject unsigned remote scripts.
- [ ] Run `dpkg --configure -a` and `apt-get -f install` before patch retry, with logs and bounded lock waits.
- [ ] For major updates, require a preserved `/home` plan or invoke and verify the backup engine before writing GRUB.
- [ ] Add `ming.ota=1`, backup disk UUID and relative manifest path to the OTA GRUB entry.
- [ ] Run OTA tests and extract the generated CLI for `bash -n`.

### Task 3: Installed-System Restore

**Files:**
- Modify: `modules/01_base.sh`
- Extend: `tests/test_ota_backup.py`

- [ ] Write failing tests for kernel-parameter parsing, safe mount paths, manifest validation and target-root restore invocation.
- [ ] Extend `ming-fix-installed-identity` to detect OTA parameters, locate/mount the backup disk, verify the manifest and restore into `${target}/home` before final GRUB installation.
- [ ] Persist restore results in `${target}/var/log/ming-ota-restore.log` and never delete a backup automatically.
- [ ] Abort OTA installation when a required restore cannot be verified.
- [ ] Run tests and generated-script syntax checks.

### Task 4: Build Validation

**Files:**
- Modify: `build_onion_os.sh`

- [ ] Require the backup engine, `doctor`, signed-patch enforcement, backup kernel parameters and installed restore markers.
- [ ] Run OTA tests, `bash -n`, Python compilation and `git diff --check`.

