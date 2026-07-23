# Ming OS Release Trust Dual-Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed release-vault workflow that keeps long-lived signing material in two encrypted locations, validates the NAS copy through the existing reverse SSH path, and prevents private material from entering GitHub, ISO builds, or production servers.

**Architecture:** A release-side Python CLI owns receipt validation, public-material scanning, local vault checks, and fixed-command NAS verification. An optional systemd timer runs only the read-only NAS check on the production host. The existing OTA runtime remains the consumer of the public keyring/policy and signed artifacts; no transaction engine, initramfs, GRUB, rollback, or recovery-ISO code is changed.

**Tech Stack:** Python 3 standard library, `gpg`/`gpgv`, `age`, OpenSSH, JSONL diagnostics, systemd units, unittest, Bash syntax checks.

---

## File Map

| File | Responsibility |
| --- | --- |
| `tools/ming-release-vault.py` | Release-side CLI: strict receipts, public-tree scanning, local encrypted-vault checks, fixed-command NAS verification, JSON output and failure codes. |
| `tools/ming-release-vault-check.service` | Production-host read-only monthly check unit. |
| `tools/ming-release-vault-check.timer` | Monthly schedule with bounded delay and persistent catch-up. |
| `tools/ming-release-vault-install.sh` | Installs the checker and units without creating keys or copying secrets. |
| `tests/test_release_vault.py` | Unit and contract tests for the CLI, receipt schema, secret scanner, command allowlist, and failure codes. |
| `tests/test_release_vault_systemd.py` | Static checks for the service/timer/install script. |
| `tests/fixtures/release-vault/` | Public-only receipt, policy, keyring metadata, and malicious input fixtures. No private or encrypted recovery data is committed. |
| `tools/secret-scan-ming-release.sh` | Local/CI public-tree scan wrapper; exits nonzero on private material or forbidden paths. |
| `.gitignore` | Ignores local vaults, encrypted recovery bundles, temporary GPG homes, and release receipts containing host-local data. |
| `build_onion_os.sh` | Release-mode hook that calls the CLI before an ISO release build; it does not receive private keys. |
| `docs/releases/ming-release-vault-operations.md` | Operator procedure for key custody, NAS setup, release preflight, monthly checks, and quarterly offline recovery. |
| `docs/releases/26.4.0-release-receipt.schema.json` | Public receipt schema consumed by the checker and release documentation. |

## Task 1: Public trust and receipt model

**Files:**
- Create: `tools/ming-release-vault.py`
- Create: `tests/test_release_vault.py`
- Create: `tests/fixtures/release-vault/good-receipt.json`
- Create: `tests/fixtures/release-vault/private-key.txt`
- Create: `tests/fixtures/release-vault/.env`
- Create: `tests/fixtures/release-vault/private-path-log.txt`

- [ ] **Step 1: Write failing tests for the public scanner and receipt validator**

Add these tests to `tests/test_release_vault.py`:

```python
def test_public_scan_accepts_public_keyring_policy_signature_and_hash(self):
    result = run_cli("scan-public", "--root", str(self.good_public))
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(result.stdout)["status"], "ok")

def test_public_scan_rejects_secret_key_dotenv_private_path_and_age_bundle(self):
    for name in ("private-key.txt", ".env", "private-path-log.txt", "recovery-bundle-1.age"):
        target = self.public / name
        if name.endswith(".age"):
            target.write_bytes(b"age-encrypted-placeholder")
        else:
            shutil.copy2(FIXTURES / name, target)
        result = run_cli("scan-public", "--root", str(self.public))
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_SECRET_EXPOSURE")
        target.unlink()

def test_receipt_rejects_unknown_fields_missing_hashes_and_non_hex_fingerprints(self):
    receipt = json.loads((FIXTURES / "good-receipt.json").read_text())
    for mutation in (
        {"unexpected": True},
        {"bundle_sha256": "short"},
        {"primary_fingerprint": "not-a-fingerprint"},
    ):
        candidate = self.temp / "receipt.json"
        value = dict(receipt)
        value.update(mutation)
        candidate.write_text(json.dumps(value), encoding="utf-8")
        result = run_cli("verify-receipt", "--receipt", str(candidate))
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")
```

- [ ] **Step 2: Run the focused tests and confirm the expected failure**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultTests -v
```

Expected: import/CLI failures because `tools/ming-release-vault.py` and the receipt fixture do not exist yet.

- [ ] **Step 3: Implement the minimal strict JSON and scan primitives**

Implement `tools/ming-release-vault.py` with these exact public functions and exit codes:

```python
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_READY = 78
EXIT_UNREACHABLE = 69

def emit_ok(payload: dict) -> None: ...
def emit_error(code: str, message: str, details: dict | None = None) -> None: ...
def validate_receipt(value: dict) -> dict: ...
def scan_public_tree(root: pathlib.Path) -> dict: ...
```

`validate_receipt` must reject unknown fields, require the exact `ming-release-vault-receipt-v1` format, require 64 lowercase hex SHA256 strings, require 40-or-64 uppercase hexadecimal fingerprints, require a positive integer generation, require `age-v1`, and permit only `verified` status. `scan_public_tree` must reject regular files whose basenames or contents contain private-key markers, `.env`, `.age`, `secret`, `password`, `token`, `known_hosts`, private paths, or SSH private-key names. It must permit the public keyring, policy, detached signatures, public manifests, hashes, and sanitized receipts.

The CLI must print one JSON object to stdout for both success and failure. No secret content, command line, environment variable, or absolute path may be included in the output.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultTests -v
```

Expected: all Task 1 tests pass and invalid inputs return exit `78` with the expected structured error code.

- [ ] **Step 5: Commit the task**

```powershell
git add tools/ming-release-vault.py tests/test_release_vault.py tests/fixtures/release-vault
git commit -m "feat: add release trust receipt and secret scanner"
```

## Task 2: Local encrypted recovery bundle and public receipt

**Files:**
- Modify: `tools/ming-release-vault.py`
- Modify: `tests/test_release_vault.py`
- Create: `docs/releases/26.4.0-release-receipt.schema.json`

- [ ] **Step 1: Write failing tests for bundle preparation and receipt generation**

Add tests that require `create-bundle` to reject a missing `MING_RELEASE_VAULT`, symlinked input, a password in `argv`/environment, and an absent `age` binary. Add a success test with a fake `age` runner that records arguments and proves no password is passed to the child process:

```python
def test_create_bundle_never_passes_password_on_argv_or_environment(self):
    result = run_cli(
        "create-bundle", "--input", str(self.private_input),
        "--output", str(self.vault / "encrypted" / "recovery-bundle-1.age"),
        "--recipient-file", str(self.recipient),
        env={"MING_RELEASE_VAULT": str(self.vault), "MING_RELEASE_TEST_AGE": "1"},
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    invocation = json.loads(result.stdout)["test_invocation"]
    self.assertNotIn("password", " ".join(invocation["argv"]).lower())
    self.assertNotIn("MING_RELEASE_PASSWORD", invocation["environment"])

def test_create_bundle_rejects_symlinked_private_input(self):
    os.symlink(self.private_input / "secret.key", self.private_input / "link.key")
    result = run_cli("create-bundle", "--input", str(self.private_input), ...)
    self.assertEqual(result.returncode, 78)
    self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")
```

- [ ] **Step 2: Run the tests and confirm they fail for the missing command**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultBundleTests -v
```

Expected: `create-bundle` is not implemented and the tests fail with the command contract error.

- [ ] **Step 3: Implement bounded bundle creation**

Add `create_bundle(input_dir, output, recipient_file, age_runner)` and the `create-bundle` CLI command. Require an explicit vault output under `MING_RELEASE_VAULT`, reject symlinks and paths outside the input root, write a deterministic tar stream to `age -p` or `age -R <recipient-file>`, and create an atomic SHA256 sidecar. Password mode must use the age TTY prompt only; it must never read a password from an option, environment variable, JSON, or stdin redirected from a file. The command must refuse output paths under the Git worktree and refuse `.age` files in the repository.

Add `write_receipt(bundle, sidecar, public_keyring, policy, bundle_id, generation, fingerprints)` with an atomic JSON write and read-back validation. The receipt must match `docs/releases/26.4.0-release-receipt.schema.json` and contain no host-local path or NAS address.

- [ ] **Step 4: Run the tests and verify the green result**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultBundleTests -v
```

Expected: all bundle, password-boundary, symlink, sidecar, receipt and atomic-write tests pass.

- [ ] **Step 5: Commit the task**

```powershell
git add tools/ming-release-vault.py tests/test_release_vault.py docs/releases/26.4.0-release-receipt.schema.json
git commit -m "feat: prepare encrypted release recovery bundles"
```

## Task 3: NAS reverse-SSH read-only verification

**Files:**
- Modify: `tools/ming-release-vault.py`
- Modify: `tests/test_release_vault.py`
- Create: `tools/ming-release-vault-remote-command.sh`

- [ ] **Step 1: Write failing tests for fixed NAS commands and failure codes**

Add tests for successful metadata/hash comparison and for these failures: tunnel unavailable, host-key mismatch, path traversal, symlink target, missing sidecar, and hash mismatch. Assert that every remote command is one of `stat`, `read sidecar`, or `sha256sum` and that no command contains `rm`, `mv`, `chmod`, a shell expansion, or an unvalidated path.

```python
def test_verify_nas_uses_only_fixed_read_commands(self):
    result = run_cli("verify-nas", "--config", str(self.nas_config), env={"MING_RELEASE_TEST_SSH": "1"})
    self.assertEqual(result.returncode, 0, result.stderr)
    for command in json.loads(result.stdout)["test_commands"]:
        self.assertIn(command[0], ("stat", "sha256sum", "cat"))
        self.assertNotRegex(" ".join(command), r"(?:rm|mv|chmod|sh -c|;|&&|\\$\\(|\.\.)")

def test_verify_nas_rejects_remote_path_traversal_and_hash_mismatch(self):
    for mutation in ("../outside.age", "recovery-bundle-1.age/child"):
        result = run_cli("verify-nas", "--config", config_with_object(mutation), env={"MING_RELEASE_TEST_SSH": "1"})
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)["error_code"], "E_VAULT_PERMISSION")
```

- [ ] **Step 2: Run the tests and confirm they fail before implementation**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultNasTests -v
```

Expected: missing `verify-nas` command and missing fixed-command helper.

- [ ] **Step 3: Implement the fixed-command NAS verifier**

Add `verify_nas(config, ssh_runner)` with a strict JSON config containing only `host_alias`, `port`, `remote_dir`, `known_hosts`, `object`, `sidecar`, and `receipt`. Validate that the remote directory is absolute, the object names are single path components matching `recovery-bundle-[0-9]+.(age|sha256|json)`, and the configured host alias is not an IP literal unless explicitly marked as an approved tunnel endpoint. Invoke OpenSSH with `BatchMode=yes`, `StrictHostKeyChecking=yes`, the pinned `UserKnownHostsFile`, `ConnectTimeout=10`, and no agent forwarding. Compare remote size/hash/sidecar/receipt with the local receipt and return `E_VAULT_UNREACHABLE`, `E_VAULT_PERMISSION`, or `E_VAULT_HASH_MISMATCH` without exposing SSH output.

Add `tools/ming-release-vault-remote-command.sh` as a NAS-side forced-command reference. It must accept only the three fixed read operations, reject all other input, reject symlinks, and never execute a shell-provided path.

- [ ] **Step 4: Run the NAS tests and verify they pass**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultNasTests -v
```

Expected: fixed command allowlist, path validation, host-key pinning, hash comparison and failure-code tests pass.

- [ ] **Step 5: Commit the task**

```powershell
git add tools/ming-release-vault.py tools/ming-release-vault-remote-command.sh tests/test_release_vault.py
git commit -m "feat: verify encrypted release vault through NAS tunnel"
```

## Task 4: Release preflight and public-tree build gate

**Files:**
- Modify: `tools/ming-release-vault.py`
- Modify: `build_onion_os.sh`
- Create: `tools/secret-scan-ming-release.sh`
- Modify: `tests/test_release_vault.py`
- Modify: `tests/test_release_gate.py`

- [ ] **Step 1: Write failing tests for release-mode refusal**

Add tests that run `preflight --mode release` with each missing prerequisite and assert `E_RELEASE_NOT_READY`: missing public keyring, policy mismatch, missing local receipt, missing NAS verification, stale receipt, and secret scan findings. Add a build contract test asserting release mode calls the preflight tool before ISO creation and does not accept a private key path or password option.

```python
def test_release_preflight_refuses_without_verified_local_and_nas_copies(self):
    result = run_cli("preflight", "--mode", "release", "--config", str(self.config))
    self.assertEqual(result.returncode, 78)
    self.assertEqual(json.loads(result.stdout)["error_code"], "E_RELEASE_NOT_READY")

def test_build_gate_uses_public_preflight_without_private_key_arguments(self):
    source = BUILD.read_text(encoding="utf-8")
    self.assertIn("ming-release-vault.py preflight --mode release", source)
    self.assertNotRegex(source, r"(?:--private-key|--password|MING_RELEASE_PASSWORD)")
```

- [ ] **Step 2: Run the tests and confirm the gate contract fails**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultPreflightTests tests.test_release_gate.ReleaseGateContracts.test_release_preflight_is_before_iso_creation -v
```

Expected: the new preflight command and build marker are absent.

- [ ] **Step 3: Implement release-mode preflight and scanner wrapper**

Implement `preflight` as a read-only composition of `scan_public_tree`, `validate_receipt`, local hash checks, and NAS verification. Require `--mode release`; development mode must not claim release readiness. Add `tools/secret-scan-ming-release.sh` to run the scanner against the repository root and reject ignored local vault paths if they are explicitly supplied. Insert one release-mode call in `build_onion_os.sh` immediately before ISO output generation, guarded by `MING_RELEASE_PREFLIGHT_CONFIG`; if release mode is requested and the config is absent, fail closed. Do not pass private paths, private keys, passwords, or NAS secrets to the chroot.

- [ ] **Step 4: Run focused gate tests and static checks**

Run:

```powershell
python -m unittest tests.test_release_vault.ReleaseVaultPreflightTests tests.test_release_gate -v
bash -n tools/secret-scan-ming-release.sh build_onion_os.sh
```

Expected: all new preflight tests and existing release-gate tests pass.

- [ ] **Step 5: Commit the task**

```powershell
git add tools/ming-release-vault.py tools/secret-scan-ming-release.sh build_onion_os.sh tests/test_release_vault.py tests/test_release_gate.py
git commit -m "build: enforce release vault preflight"
```

## Task 5: Monthly production check units

**Files:**
- Create: `tools/ming-release-vault-check.service`
- Create: `tools/ming-release-vault-check.timer`
- Create: `tools/ming-release-vault-install.sh`
- Create: `tests/test_release_vault_systemd.py`

- [ ] **Step 1: Write failing systemd and installer tests**

Add tests that require the service to run as a dedicated unprivileged user, use `NoNewPrivileges=yes`, `PrivateTmp=yes`, a read-only config path, a bounded timeout, and the JSONL log path. Require the timer to use `OnCalendar=monthly`, `Persistent=true`, and a bounded `RandomizedDelaySec`. Require the installer to refuse missing config, never generate keys, never call `gpg --decrypt`, and install units with mode `0644`.

```python
def test_monthly_service_is_read_only_and_bounded(self):
    text = SERVICE.read_text(encoding="utf-8")
    self.assertIn("Type=oneshot", text)
    self.assertIn("NoNewPrivileges=yes", text)
    self.assertIn("PrivateTmp=yes", text)
    self.assertIn("TimeoutStartSec=30s", text)
    self.assertIn("verify-nas", text)
    self.assertNotIn("gpg --decrypt", text)

def test_timer_is_monthly_persistent_and_bounded(self):
    text = TIMER.read_text(encoding="utf-8")
    self.assertIn("OnCalendar=monthly", text)
    self.assertIn("Persistent=true", text)
    self.assertIn("RandomizedDelaySec=1h", text)
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```powershell
python -m unittest tests.test_release_vault_systemd -v
```

Expected: missing service, timer and installer files.

- [ ] **Step 3: Implement units and installer**

Create a service that invokes `/usr/local/lib/ming-os/ming-release-vault.py verify-nas --config /etc/ming-os/release-vault.json`, writes only sanitized JSONL diagnostics to `/var/log/ming-os/release-vault-check.jsonl`, and exits nonzero on any vault error. Create the timer with monthly persistent execution and one-hour randomized delay. The installer must validate root ownership and mode of the supplied config, install only public CLI/unit files, create the log directory, and refuse to copy or read private key material. It must use `systemctl daemon-reload` and `enable` only; it must not start the timer automatically when configuration is incomplete.

- [ ] **Step 4: Run static unit validation**

Run:

```powershell
python -m unittest tests.test_release_vault_systemd -v
wsl.exe -d Ubuntu -- systemd-analyze verify tools/ming-release-vault-check.service tools/ming-release-vault-check.timer
bash -n tools/ming-release-vault-install.sh tools/ming-release-vault-remote-command.sh
```

Expected: all tests pass; `systemd-analyze verify` exits zero when available, otherwise the test records a documented skip.

- [ ] **Step 5: Commit the task**

```powershell
git add tools/ming-release-vault-check.service tools/ming-release-vault-check.timer tools/ming-release-vault-install.sh tests/test_release_vault_systemd.py
git commit -m "ops: add monthly release vault integrity check"
```

## Task 6: Documentation, ignore rules, and final regression gate

**Files:**
- Create: `docs/releases/ming-release-vault-operations.md`
- Modify: `.gitignore`
- Modify: `tests/test_release_vault.py`
- Create: `tests/test_release_vault_documentation.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing documentation and ignore-rule tests**

Require the documentation to name `ming.sca-hub.cn`, explicitly state that encrypted bundles are not uploaded to GitHub, describe the reverse SSH/NAS boundary, list every failure code, and include the monthly and quarterly procedures. Require `.gitignore` to ignore local vault directories, `.age` bundles, temporary GPG homes, and private receipts while allowing public receipt files.

- [ ] **Step 2: Run the documentation tests and confirm they fail**

Run:

```powershell
python -m unittest tests.test_release_vault_documentation -v
```

Expected: missing documentation and ignore markers.

- [ ] **Step 3: Write the operator documentation and safe ignore rules**

Document the exact release sequence, the manual key-custody step, the NAS restricted account, `known_hosts` pinning, monthly read-only check, quarterly recovery drill, GitHub publication boundary, and the decision to freeze OTA if both encrypted copies or the separate password are lost. Add ignore rules that cover only local secrets and encrypted recovery artifacts; do not ignore public keyring, policy, signatures, hashes, or public release receipts.

- [ ] **Step 4: Run the complete verification suite**

Run:

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ming-release-vault-pycache'
python -m unittest discover -s tests -v
bash -n build_onion_os.sh resume_build.sh modules/*.sh tools/*.sh
python -m py_compile assets/*.py tests/*.py tools/*.py
git diff --check
bash tools/secret-scan-ming-release.sh --root .
```

Expected: all tests pass, shell/Python checks pass, the scanner reports no forbidden public-tree material, and the working tree contains no generated vault or encrypted bundle.

- [ ] **Step 5: Commit the final documentation and gate**

```powershell
git add docs/releases/ming-release-vault-operations.md .gitignore tests/test_release_vault_documentation.py README.md
git commit -m "docs: document release vault recovery operations"
```

## Self-review checklist

- Coverage: tasks 1-2 cover public trust, receipts, local encrypted copies and password boundaries; task 3 covers the NAS tunnel and forced command; task 4 covers release gating; task 5 covers the monthly check; task 6 covers operator recovery, GitHub boundaries and final regression.
- Core safety: no task modifies `modules/06_ota_update.sh`, transaction Python modules, initramfs, GRUB, rollback journal or recovery ISO protection.
- No secret creation: the plan never generates a release key or accepts a real private key in the repository.
- No placeholders in implementation contracts: all CLI commands, failure codes, paths, test names and expected exits are specified.
- Release condition: no ISO build, GitHub push, website deployment or OTA publication is authorized until a real official trust keyring, key policy, signed bootstrap, signed 26.4.0 manifest/content-index/payload, and two verified encrypted copies exist.
