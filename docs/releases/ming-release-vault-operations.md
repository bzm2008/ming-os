# Ming OS Release Vault Operations

This procedure applies to the 26.4.0 release and later. It is a release-side
process only: it does not change the OTA transaction engine, initramfs, GRUB,
rollback journal, or the recovery ISO independent-backup-media gate.

## Public and private boundaries

The GitHub repository and the website may contain only the public keyring, key
policy, signed manifests, content indexes, detached signatures, hashes,
release notes, and sanitized receipts. The rule is: encrypted recovery bundles are not uploaded to GitHub.
No private key, passphrase, NAS credential, SSH private
key, or host-local path may enter the repository, ISO build context, or a
production server.

The public discovery domain is `ming.scallion.uno`. The reserved fallback is
`ming.sca-hub.cn`; it remains disabled until HTTPS,备案, API parity, and signed
discovery responses have been verified. A DNS or HTTP fallback never relaxes
manifest, version, architecture, content-index, or signature checks.

The local release vault is selected explicitly with `MING_RELEASE_VAULT` and
contains the encrypted bundle, its SHA256 sidecar, and a public receipt. The
NAS copy is stored under the fixed vault directory and is reachable only over
the existing reverse SSH tunnel. The NAS account is read-only and its pinned
`known_hosts` file is checked before every fixed `stat`, `sha256sum`, or `cat`
operation. It cannot upload, delete, rename, open a shell, or forward an
agent/X11 session.

## Release preflight

Run the release gate only on the isolated release workstation, after the
official public keyring, policy, signed bootstrap, manifest, content-index and
payload signatures have been reviewed:

```text
python3 tools/ming-release-vault.py preflight --mode release --config /path/to/release-preflight.json
```

The result is one JSON object. `status=ok` means the public scan, receipt
schema, freshness window, keyring/policy hashes, local bundle, sidecar, and
NAS verification all passed. It does not mean an ISO or OTA has been
published. A failed gate must stop the build and publication.

The build script runs the same command when `MING_RELEASE_MODE=release` and
requires `MING_RELEASE_PREFLIGHT_CONFIG`. Development builds do not claim
release readiness. The configuration contains only public paths and the fixed
NAS verifier configuration; it must not contain a password or private key.

## Failure codes

The checker emits sanitized JSON with one of these codes:

- `E_VAULT_NOT_CONFIGURED`: a required vault was not configured.
- `E_VAULT_UNREACHABLE`: the tunnel, SSH service, or pinned host was not reachable.
- `E_VAULT_PERMISSION`: an object, permission, symlink, or fixed command was unsafe.
- `E_VAULT_HASH_MISMATCH`: local, sidecar, receipt, or NAS content differed.
- `E_PUBLIC_TRUST_MISMATCH`: public keyring, policy, or fingerprint did not match.
- `E_SECRET_EXPOSURE`: private material or a forbidden path entered the public tree.
- `E_SIGNING_KEY_UNAVAILABLE`: the reviewed signing key was unavailable.
- `E_RECOVERY_DECRYPT_FAILED`: a manual recovery drill could not decrypt the bundle.
- `E_RECEIPT_STALE`: a receipt is outside the approved freshness window.
- `E_RELEASE_NOT_READY`: one or more release prerequisites are missing.

Never replace an official key when a trust check fails. freeze OTA and manual
publication until the release owner restores the reviewed trust material.

## Monthly integrity check

Install the service and timer on the production server only after the config
file is root-owned and mode `0600` or `0640` (the installer installs a
group-readable `0640` copy for the dedicated checker user):

```text
sudo tools/ming-release-vault-install.sh /etc/ming-os/release-vault.json
```

The timer is enabled but not started by the installer. It runs once per month
with a bounded random delay and persistent catch-up. The service runs as the
dedicated unprivileged `ming-release-vault` user, has a 30-second timeout, and
writes sanitized JSONL to:

```text
/var/log/ming-os/release-vault-check.jsonl
```

The monthly check reads metadata, hashes, the sidecar, the public receipt, and
the pinned host identity. It never decrypts the recovery bundle, reads a
password, creates a plaintext copy, or changes the NAS. Inspect the last 12
months of records and investigate any nonzero exit or trust mismatch.

## Quarterly recovery drill

The quarterly recovery exercise is a manual offline check, never an automated
decryption step.

An authorized release owner performs a manual offline drill each quarter. Copy
the encrypted bundle to a one-time encrypted workspace, enter the passphrase
directly into the age TTY prompt, verify the public fingerprints, revocation
certificate, policy, and every file hash, then destroy the plaintext, temporary
GPG home, and workspace. Do not put the decrypted material on the production
server or GitHub. A failed drill freezes release and OTA; do not generate a
replacement key as a workaround.

## Domain and bootstrap continuity

The 26.3.2 client must first install the official signed bootstrap. Without it,
the client may only show the bootstrap instruction and must not enter the
recovery ISO same-disk path. After bootstrap capability is confirmed, the
26.4.0 manifest may be offered through the existing transactional OTA path.
The independent backup-media restriction of the recovery ISO remains intact.

## Operator checklist

1. Verify the official public keyring and key policy fingerprints offline.
2. Verify bootstrap, manifest, content-index, payload signatures and hashes.
3. Run `scan-public` and `preflight --mode release` and archive only their
   sanitized receipts in the audit directory.
4. Confirm the local and NAS encrypted copies and sidecars match.
5. Build only after the gate is `status=ok`; publish only public objects.
6. Keep the previous signed release available for rollback and manual install.
