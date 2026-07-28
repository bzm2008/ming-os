#!/usr/bin/env bash
set -euo pipefail

LAYOUT="${MING_OTA_AB_LAYOUT:-/etc/ming-update/slots.json}"
TRANSACTION="${MING_OTA_AB_TRANSACTION:-/home/.ming-ota/ab-transaction.json}"
MOUNT_ROOT="${MING_OTA_AB_MOUNT_ROOT:-/run/ming-ota-inactive}"
ISO=""
VERSION=""
CHECKSUM=""
ISO_MOUNT=""
TRUSTED_STAGING_ROOT="${MING_OTA_AB_TRUSTED_STAGING_ROOT:-/var/lib/ming-update/ab-staging}"

fail() { printf 'ming-ota-ab-stage: %s\n' "$*" >&2; exit 2; }
cleanup() {
    if mountpoint -q "${MOUNT_ROOT}" 2>/dev/null; then
        umount "${MOUNT_ROOT}" || true
    fi
    if [[ -n "${ISO_MOUNT}" ]] && mountpoint -q "${ISO_MOUNT}" 2>/dev/null; then
        umount "${ISO_MOUNT}" || true
    fi
    [[ -z "${ISO_MOUNT}" ]] || rmdir "${ISO_MOUNT}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iso) [[ $# -ge 2 ]] || fail "--iso requires a path"; ISO="$2"; shift 2 ;;
        --version) [[ $# -ge 2 ]] || fail "--version requires a value"; VERSION="$2"; shift 2 ;;
        --checksum) [[ $# -ge 2 ]] || fail "--checksum requires a value"; CHECKSUM="$2"; shift 2 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "root privileges are required"
[[ -f "${ISO}" && ! -L "${ISO}" ]] || fail "ISO is missing or unsafe"
[[ "${CHECKSUM}" =~ ^[A-Fa-f0-9]{64}$ ]] || fail "checksum is invalid"
[[ "${VERSION}" =~ ^[0-9]+(\.[0-9]+){1,3}([A-Za-z0-9._-]*)?$ ]] || fail "version is invalid"
ISO="$(readlink -f -- "${ISO}")" || fail "cannot resolve trusted ISO staging path"
trusted_root="$(readlink -f -- "${TRUSTED_STAGING_ROOT}")" || fail "trusted ISO staging root is missing"
[[ "${ISO}" == "${trusted_root}/"*.iso ]] || fail "ISO is outside trusted ISO staging"
[[ "$(stat -c '%u:%g:%a' "${trusted_root}")" == "0:0:700" ]] || fail "trusted ISO staging directory ownership or mode is unsafe"
[[ "$(stat -c '%u:%g:%a' "${ISO}")" == "0:0:600" ]] || fail "trusted ISO staging file ownership or mode is unsafe"
[[ "$(sha256sum -- "${ISO}" | awk '{print $1}')" == "${CHECKSUM,,}" ]] || fail "ISO checksum mismatch"

status="$(ming-ota-ab --layout "${LAYOUT}" --transaction "${TRANSACTION}" status)" \
    || fail "ming-ota-ab status rejected this machine"
active="$(jq -r '.active_slot' <<<"${status}")"
target="$(jq -r '.inactive_slot' <<<"${status}")"
target_device="$(jq -r '.inactive.device' <<<"${status}")"
target_uuid="$(jq -r '.inactive.uuid' <<<"${status}")"
target_entry="$(jq -r '.inactive.grub_entry' <<<"${status}")"
[[ "${active}" != "${target}" && "${target_device}" == /dev/* ]] || fail "inactive slot identity is invalid"
[[ -b "${target_device}" ]] || fail "inactive slot is not a block device"
[[ "$(findmnt -nro UUID -T /)" != "${target_uuid}" ]] || fail "refusing to overwrite the mounted root slot"
[[ -z "$(findmnt -nro TARGET -S "${target_device}" 2>/dev/null || true)" ]] || fail "inactive slot is already mounted"
[[ -z "$(findmnt -nro TARGET -S "UUID=${target_uuid}" 2>/dev/null || true)" ]] || fail "inactive slot UUID is already mounted"
[[ "$(blkid -s UUID -o value "${target_device}")" == "${target_uuid}" ]] || fail "inactive slot UUID changed"
grep -Fq "menuentry '${target_entry}'" /boot/grub/grub.cfg \
    || grep -Fq "menuentry \"${target_entry}\"" /boot/grub/grub.cfg \
    || fail "target slot is absent from grub.cfg"
grep -Fq "/ming-slots/${target}/vmlinuz" /boot/grub/grub.cfg \
    || fail "target GRUB entry does not use its slot-specific kernel"
grep -Fq "/ming-slots/${target}/initrd.img" /boot/grub/grub.cfg \
    || fail "target GRUB entry does not use its slot-specific initrd"

ISO_MOUNT="$(mktemp -d /run/ming-ota-iso.XXXXXX)"
mount -o loop,ro "${ISO}" "${ISO_MOUNT}"
squash="${ISO_MOUNT}/live/filesystem.squashfs"
[[ -f "${squash}" ]] || fail "ISO has no live filesystem"
expanded_bytes=""
if [[ -s "${ISO_MOUNT}/live/filesystem.size" ]]; then
    expanded_bytes="$(tr -d '[:space:]' < "${ISO_MOUNT}/live/filesystem.size")"
fi
if [[ ! "${expanded_bytes}" =~ ^[0-9]+$ || "${expanded_bytes}" -le 0 ]]; then
    expanded_bytes="$(unsquashfs -lln "${squash}" 2>/dev/null \
        | awk '$3 ~ /^[0-9]+$/ {total += $3} END {printf "%.0f", total}')"
fi
[[ "${expanded_bytes}" =~ ^[0-9]+$ && "${expanded_bytes}" -gt 0 ]] \
    || fail "cannot determine expanded filesystem size"
capacity="$(blockdev --getsize64 "${target_device}")"
headroom=$((expanded_bytes / 10))
[[ "${headroom}" -ge 1073741824 ]] || headroom=1073741824
required=$((expanded_bytes + headroom))
[[ "${capacity}" -gt "${required}" ]] || fail "inactive slot is too small for expanded filesystem"

mkdir -p "${MOUNT_ROOT}"
mount -o rw,nosuid,nodev "${target_device}" "${MOUNT_ROOT}"
filesystem_bytes="$(df --output=size -B1 "${MOUNT_ROOT}" | tail -n 1 | tr -d ' ')"
[[ "${filesystem_bytes}" =~ ^[0-9]+$ && "${filesystem_bytes}" -gt "${required}" ]] \
    || fail "inactive filesystem has insufficient expanded capacity"
find "${MOUNT_ROOT}" -mindepth 1 -maxdepth 1 -xdev -exec rm -rf -- {} +
unsquashfs -f -d "${MOUNT_ROOT}" "${squash}"
ming-ota-ab --layout "${LAYOUT}" prepare-root --root "${MOUNT_ROOT}" --target "${target}"
kernel="$(find "${MOUNT_ROOT}/boot" -maxdepth 1 -type f -name 'vmlinuz-*' -print | sort -V | tail -n 1)"
initrd="$(find "${MOUNT_ROOT}/boot" -maxdepth 1 -type f -name 'initrd.img-*' -print | sort -V | tail -n 1)"
[[ -f "${kernel}" && -f "${initrd}" ]] || fail "new slot kernel or initrd is missing"
slot_boot="/boot/ming-slots/${target}"
install -d -o root -g root -m 0755 "${slot_boot}"
install -o root -g root -m 0644 "${kernel}" "${slot_boot}/.vmlinuz.new"
install -o root -g root -m 0644 "${initrd}" "${slot_boot}/.initrd.img.new"
mv -f "${slot_boot}/.vmlinuz.new" "${slot_boot}/vmlinuz"
mv -f "${slot_boot}/.initrd.img.new" "${slot_boot}/initrd.img"
# Installer GRUB entries use /ming-slots/${target}/vmlinuz and
# /ming-slots/${target}/initrd.img on the shared boot filesystem.
sync -f "${MOUNT_ROOT}"
sync -f "${slot_boot}"
umount "${ISO_MOUNT}"
rmdir "${ISO_MOUNT}"
ISO_MOUNT=""

# Transaction command: ming-ota-ab begin
ming-ota-ab --layout "${LAYOUT}" --transaction "${TRANSACTION}" begin \
    --target "${target}" --version "${VERSION}" --checksum "${CHECKSUM}" >/dev/null
grub-reboot "${target_entry}"
grep -Fqx "next_entry=${target_entry}" <<<"$(grub-editenv list)" \
    || fail "GRUB next_entry readback failed"
printf 'staged slot %s; previous slot %s remains the default\n' "${target}" "${active}"
