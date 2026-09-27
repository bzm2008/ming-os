# Ming OS Offline OTA Bundle v1

An offline bundle is a `.ming-ota` tar archive containing exactly two regular
files: `manifest.json` and one root-level `*.iso` payload. Directories, links,
device nodes, extra files, and nested payload paths are rejected.

The UTF-8 JSON manifest requires `schema`, `version`, `build_id`,
`update_type`, `base_version`, `payload`, `payload_size`, `payload_sha256`,
`trusted_comment`, and `signature`. The schema identifier is
`ming.update.bundle.v1`; version 1 accepts only `update_type: major`.

The release signer signs the canonical JSON with only `signature` omitted.
`trusted_comment` remains inside the signed data and must use the Ming OS OTA
namespace. The `signature` value follows the existing system OTA JSON
convention: the detached Minisign signature record consumed by
`minisign -V -p <release-public-key> -m <canonical-json> -x <signature-file>`.
Never ship or store the signing private key in the OS image or repository.

The validator checks the exact installed `base_version`, forward version,
build-ID/version agreement, payload size and SHA256, then the system OTA
Minisign public key. Version 1 accepts only major ISO updates on an installed,
healthy Ming A/B layout. Patch/minor packages and dual-boot/non-A/B installs
are rejected; they continue to use their existing supported update routes.
