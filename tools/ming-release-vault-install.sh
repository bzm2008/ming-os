#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    printf '%s\n' 'must run as root' >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-${MING_RELEASE_VAULT_CONFIG:-/etc/ming-os/release-vault.json}}"
VAULT_ROOT="${MING_RELEASE_VAULT:-/var/lib/ming-os/release-vault}"

[[ -f "${CONFIG}" && ! -L "${CONFIG}" ]] || {
    printf '%s\n' 'release vault config is required and must be a regular file' >&2
    exit 78
}
config_mode="$(stat -c '%a' -- "${CONFIG}" 2>/dev/null || true)"
config_owner="$(stat -c '%u' -- "${CONFIG}" 2>/dev/null || true)"
[[ "${config_owner}" == "0" && ( "${config_mode}" == "600" || "${config_mode}" == "640" ) ]] || {
    printf '%s\n' 'release vault config must be root-owned with mode 0600 or 0640' >&2
    exit 78
}
[[ -d "${VAULT_ROOT}" && ! -L "${VAULT_ROOT}" ]] || {
    printf '%s\n' 'configured release vault directory is unavailable' >&2
    exit 78
}

if grep -Eiq 'gpg[[:space:]]+--decrypt|age[[:space:]]+-d|BEGIN[ -]+(OPENSSH|RSA|EC|DSA|PGP)[ -]+PRIVATE KEY|MING_RELEASE_PASSWORD' -- "${CONFIG}"; then
    printf '%s\n' 'private recovery or decryption material is not accepted by this installer' >&2
    exit 78
fi

install -d -m 0755 /usr/local/lib/ming-os /var/log/ming-os
if ! getent group ming-release-vault >/dev/null 2>&1; then
    groupadd --system ming-release-vault
fi
if ! id ming-release-vault >/dev/null 2>&1; then
    useradd --system --gid ming-release-vault --home-dir /nonexistent --shell /usr/sbin/nologin ming-release-vault
fi
install -d -m 0755 /etc/ming-os
[[ ! -L /etc/ming-os/release-vault.json ]] || {
    printf '%s\n' 'release vault target config must not be a symlink' >&2
    exit 78
}
install -o root -g ming-release-vault -m 0640 "${CONFIG}" /etc/ming-os/release-vault.json
install -o root -g root -m 0755 "${SCRIPT_DIR}/ming-release-vault.py" /usr/local/lib/ming-os/ming-release-vault.py
install -o root -g root -m 0644 "${SCRIPT_DIR}/ming-release-vault-check.service" /etc/systemd/system/ming-release-vault-check.service
install -o root -g root -m 0644 "${SCRIPT_DIR}/ming-release-vault-check.timer" /etc/systemd/system/ming-release-vault-check.timer
chown ming-release-vault:ming-release-vault /var/log/ming-os
chmod 0750 /var/log/ming-os
systemctl daemon-reload
systemctl enable ming-release-vault-check.timer
printf '%s\n' 'Ming release vault check installed; timer enabled but not started.'
