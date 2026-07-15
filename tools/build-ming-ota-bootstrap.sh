#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 --version VERSION --keyring FILE --policy FILE --signing-key FINGERPRINT --out DIRECTORY" >&2
}

version=""
keyring=""
policy=""
signing_key=""
output=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) version="${2:-}"; shift 2 ;;
        --keyring) keyring="${2:-}"; shift 2 ;;
        --policy) policy="${2:-}"; shift 2 ;;
        --signing-key) signing_key="${2:-}"; shift 2 ;;
        --out) output="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[[ "${version}" =~ ^[0-9]+(\.[0-9]+)*$ ]] || { usage; exit 2; }
[[ -f "${keyring}" && ! -L "${keyring}" ]] || { echo "keyring is missing or unsafe" >&2; exit 2; }
[[ -f "${policy}" && ! -L "${policy}" ]] || { echo "policy is missing or unsafe" >&2; exit 2; }
[[ "${signing_key}" =~ ^[A-Fa-f0-9]{40,64}$ ]] || { echo "signing key fingerprint is invalid" >&2; exit 2; }
[[ -n "${output}" ]] || { usage; exit 2; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

verify_bootstrap_signing_key_input() {
    local verifier="${root}/assets/ming-transaction-verify.py"
    [[ -s "${verifier}" && ! -L "${verifier}" ]] || {
        echo "transaction verifier is missing or unsafe" >&2
        return 1
    }
    python3 - "${verifier}" "${policy}" "${signing_key}" <<'PY'
import importlib.util
import sys

verifier_path, policy_path, requested = sys.argv[1:]
spec = importlib.util.spec_from_file_location("ming_bootstrap_verify", verifier_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy = module.load_key_policy(policy_path)
requested = requested.upper()
if requested not in policy["allowed_signing_fingerprints"]:
    raise SystemExit("requested bootstrap signing key is not permitted by the release policy")
PY
}

verify_bootstrap_signature_policy() {
    local artifact="$1" signature="$2" status_file="$3"
    # gpgv --status-fd=1 supplies the one VALIDSIG record parsed by the runtime verifier.
    if ! gpgv --status-fd=1 --keyring "${keyring}" "${signature}" "${artifact}" > "${status_file}"; then
        echo "bootstrap detached signature verification failed" >&2
        return 1
    fi
    python3 - "${root}/assets/ming-transaction-verify.py" "${policy}" "${signing_key}" "${status_file}" <<'PY'
import importlib.util
import pathlib
import sys

verifier_path, policy_path, requested, status_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("ming_bootstrap_verify", verifier_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy = module.load_key_policy(policy_path)
primary, signing = module._validated_signature_fingerprints(
    pathlib.Path(status_path).read_text(encoding="utf-8", errors="strict")
)
if signing != requested.upper():
    raise SystemExit("bootstrap signature does not match the requested signing fingerprint")
if primary not in policy["allowed_primary_fingerprints"]:
    raise SystemExit("bootstrap signature primary key is not permitted by the release policy")
if signing not in policy["allowed_signing_fingerprints"]:
    raise SystemExit("bootstrap signature signing key is not permitted by the release policy")
PY
}

verify_bootstrap_signing_key_input
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
pkg="${work}/ming-ota-bootstrap"
mkdir -p "${pkg}/DEBIAN" "${pkg}/usr/local/lib/ming-update" \
    "${pkg}/usr/local/bin" "${pkg}/usr/local/sbin" \
    "${pkg}/usr/share/ming-update/trust" "${pkg}/usr/share/polkit-1/actions" \
    "${pkg}/etc/initramfs-tools/hooks" "${pkg}/etc/grub.d" "${pkg}/etc/systemd/system"

cat > "${pkg}/DEBIAN/control" <<EOF
Package: ming-ota-bootstrap
Version: ${version}
Architecture: amd64
Maintainer: Ming OS Release Team <release@ming-os.invalid>
Depends: python3, gpgv, initramfs-tools, grub-common, systemd, zstd, rsync, polkitd
Description: Ming OS transactional OTA bootstrap
EOF

install -m 0755 "${root}/assets/bootstrap/ming-ota-bootstrap.postinst" "${pkg}/DEBIAN/postinst"
install -m 0755 "${root}/assets/bootstrap/ming-ota-bootstrap.prerm" "${pkg}/DEBIAN/prerm"
for asset in \
    ming-update-cli.py \
    ming-transaction-verify.py \
    ming-transaction-state.py \
    ming-transaction-slot.py \
    ming-transaction-apply.py \
    ming-transaction-rollback.py \
    ming-transaction-boot.py \
    ming-transaction-health.py \
    ming-transaction-engine.py \
    ming-transaction-diagnostics.py \
    ming-ota-bootstrap-capability.py \
    ming-transaction-allowlist.txt \
    ming-transaction-local-premount; do
    source="${root}/assets/${asset}"
    if [[ "${asset}" == ming-transaction-local-premount ]]; then
        source="${root}/assets/initramfs/${asset}"
    fi
    test -s "${source}" || { echo "required runtime asset is missing: ${asset}" >&2; exit 1; }
    install -m 0644 "${source}" "${pkg}/usr/local/lib/ming-update/${asset}"
done
chmod 0755 "${pkg}/usr/local/lib/ming-update/ming-update-cli.py" \
    "${pkg}/usr/local/lib/ming-update/ming-transaction-health.py" \
    "${pkg}/usr/local/lib/ming-update/ming-transaction-diagnostics.py" \
    "${pkg}/usr/local/lib/ming-update/ming-ota-bootstrap-capability.py" \
    "${pkg}/usr/local/lib/ming-update/ming-transaction-local-premount"
cat > "${pkg}/usr/local/bin/ming-update" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/ming-update/ming-update-cli.py "$@"
EOF
cat > "${pkg}/usr/local/sbin/ming-transaction-health" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/ming-update/ming-transaction-health.py "$@"
EOF
cat > "${pkg}/usr/local/bin/ming-transaction-diagnostics" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/ming-update/ming-transaction-diagnostics.py "$@"
EOF
chmod 0755 "${pkg}/usr/local/bin/ming-update" "${pkg}/usr/local/sbin/ming-transaction-health" "${pkg}/usr/local/bin/ming-transaction-diagnostics"

install -m 0644 "${keyring}" "${pkg}/usr/share/ming-update/trust/release-keyring.gpg"
install -m 0644 "${policy}" "${pkg}/usr/share/ming-update/trust/key-policy.json"
install -m 0755 "${root}/assets/initramfs/ming-transaction-hook" "${pkg}/etc/initramfs-tools/hooks/ming-transaction"
install -m 0755 "${root}/assets/grub/40_ming_transaction" "${pkg}/etc/grub.d/40_ming_transaction"
install -m 0644 "${root}/assets/systemd/ming-transaction-health.service" "${pkg}/etc/systemd/system/ming-transaction-health.service"
install -m 0644 "${root}/assets/systemd/ming-transaction-reconcile.service" "${pkg}/etc/systemd/system/ming-transaction-reconcile.service"
install -m 0644 "${root}/assets/polkit/org.mingos.update.policy" "${pkg}/usr/share/polkit-1/actions/org.mingos.update.policy"

mkdir -p "${output}"
deb="${output}/ming-ota-bootstrap_${version}_amd64.deb"
dpkg-deb --build --root-owner-group "${pkg}" "${deb}"
sha256sum "${deb}" > "${deb}.sha256"
gpg --batch --yes --local-user "${signing_key}" --detach-sign --output "${deb}.sig" "${deb}"
verify_bootstrap_signature_policy "${deb}" "${deb}.sig" "${work}/bootstrap.sig.status"
