#!/usr/bin/env bash
# Read-only structural validation for a Ming OS Hyper-V Generation 2 VHDX.
set -Eeuo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly DEFAULT_LABEL="MING_OS_2640"
readonly -a HYPERV_INITRAMFS_MODULES=(
    hv_vmbus
    hv_storvsc
    hv_netvsc
    hid_hyperv
)

IMAGE=""
MANIFEST=""
CHECKSUM=""
WORK_DIR=""
RAW_IMAGE=""
LOOP_DEVICE=""
ROOT_MOUNT=""
EFI_MOUNT=""
ROOT_PART=""
EFI_PART=""
EXPECTED_LABEL="${DEFAULT_LABEL}"

die() {
    printf '%s: %s\n' "${SCRIPT_NAME}" "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: sudo scripts/inspect-hyperv-gen2-vhdx.sh --image FILE.vhdx [--manifest FILE] [--checksum FILE]

Validate a Ming OS Hyper-V Generation 2 VHDX without changing it. The
inspector verifies VHDX format, GPT, FAT32 ESP, ext4 root label,
EFI/BOOT/BOOTX64.EFI, initramfs Hyper-V modules, manifest, and checksum.
EOF
}

require_root() {
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "root privileges are required to attach the VHDX read-only"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing required host command: $1"
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${EFI_MOUNT}" ]] && mountpoint -q "${EFI_MOUNT}" 2>/dev/null; then
        umount -l "${EFI_MOUNT}" || true
    fi
    if [[ -n "${ROOT_MOUNT}" ]] && mountpoint -q "${ROOT_MOUNT}" 2>/dev/null; then
        umount -l "${ROOT_MOUNT}" || true
    fi
    if [[ -n "${LOOP_DEVICE}" ]]; then
        losetup -d "${LOOP_DEVICE}" || true
    fi
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf -- "${WORK_DIR}" || true
    fi
    exit "${status}"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --image)
                [[ $# -ge 2 ]] || die "--image requires a VHDX path"
                IMAGE="$2"
                shift 2
                ;;
            --manifest)
                [[ $# -ge 2 ]] || die "--manifest requires a path"
                MANIFEST="$2"
                shift 2
                ;;
            --checksum)
                [[ $# -ge 2 ]] || die "--checksum requires a path"
                CHECKSUM="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                die "unknown argument: $1"
                ;;
        esac
    done
    [[ -n "${IMAGE}" ]] || die "--image is required"
    [[ "${IMAGE}" == *.vhdx ]] || die "--image must end in .vhdx"
    [[ -f "${IMAGE}" ]] || die "VHDX image does not exist: ${IMAGE}"
    IMAGE="$(realpath -e "${IMAGE}")"
    [[ -n "${MANIFEST}" ]] || MANIFEST="${IMAGE}.manifest.json"
    [[ -n "${CHECKSUM}" ]] || CHECKSUM="${IMAGE}.sha256"
}

preflight() {
    [[ "$(uname -s)" == "Linux" ]] || die "a Linux host is required for loop-backed VHDX inspection"
    require_root
    for tool in awk blkid find grep lsinitramfs losetup mktemp mount mountpoint partprobe partx python3 qemu-img readlink realpath sgdisk sha256sum sleep sort umount; do
        require_cmd "${tool}"
    done
    [[ -f "${MANIFEST}" ]] || die "manifest does not exist: ${MANIFEST}"
    [[ -f "${CHECKSUM}" ]] || die "checksum does not exist: ${CHECKSUM}"
    MANIFEST="$(realpath -e "${MANIFEST}")"
    CHECKSUM="$(realpath -e "${CHECKSUM}")"
}

verify_manifest_and_checksum() {
    EXPECTED_LABEL="$(python3 - "${MANIFEST}" "${IMAGE}" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
image_path = pathlib.Path(sys.argv[2])
with manifest_path.open(encoding="utf-8") as handle:
    payload = json.load(handle)

try:
    artifact = payload["artifact"]
    partitioning = payload["partitioning"]
    assert artifact["format"] == "vhdx"
    assert artifact["firmware"] == "uefi-gen2"
    assert artifact["generation"] == 2
    assert artifact["secure_boot"] == "disabled-required"
    assert pathlib.Path(artifact["filename"]).name == image_path.name
    assert partitioning["partition_table"] == "gpt"
    assert partitioning["efi"]["filesystem"] == "vfat"
    assert partitioning["efi"]["efi_fallback"] == "/EFI/BOOT/BOOTX64.EFI"
    assert partitioning["root"]["filesystem"] == "ext4"
    assert partitioning["root"]["label"]
    assert payload["installed_desktop_identity"]["desktop_session"] == "xfce"
    assert payload["installed_desktop_identity"]["graphical_target"] is True
    assert payload["installed_desktop_identity"]["lightdm_enabled"] is True
    assert payload["installed_desktop_identity"]["live_installer_removed"] is True
except (AssertionError, KeyError, TypeError) as exc:
    raise SystemExit("invalid Hyper-V Gen2 VHDX manifest: " + str(exc))

print(partitioning["root"]["label"])
PY
)"
    (
        cd "$(dirname "${IMAGE}")"
        sha256sum -c "${CHECKSUM}"
    )
}

find_partition() {
    local number="$1"
    partx --raw --noheadings --output NR,PATH "${LOOP_DEVICE}" | awk -v wanted="${number}" '$1 == wanted {print $2}'
}

wait_for_partition() {
    local number="$1" attempt partition
    for ((attempt = 1; attempt <= 20; attempt++)); do
        partition="$(find_partition "${number}" || true)"
        if [[ -b "${partition}" ]]; then
            printf '%s\n' "${partition}"
            return 0
        fi
        sleep 0.25
    done
    die "partition device did not appear before the timeout: ${LOOP_DEVICE}p${number}"
}

verify_vhdx_format() {
    qemu-img info --output=json "${IMAGE}" | python3 -c '
import json
import sys
payload = json.load(sys.stdin)
if payload.get("format") != "vhdx":
    raise SystemExit("image format is not vhdx")
if payload.get("virtual-size", 0) <= 0:
    raise SystemExit("VHDX has no virtual disk size")
'
}

attach_raw_copy() {
    RAW_IMAGE="${WORK_DIR}/inspect.raw"
    qemu-img convert -f vhdx -O raw "${IMAGE}" "${RAW_IMAGE}"

    local partition_table
    partition_table="$(sgdisk --print "${RAW_IMAGE}")"
    grep -qi "Disklabel type: gpt" <<<"${partition_table}" || die "VHDX does not contain a GPT partition table"
    grep -Eqi '^[[:space:]]*1[[:space:]].*EF00' <<<"${partition_table}" || die "GPT partition 1 is not an EFI system partition (ef00)"
    grep -Eqi '^[[:space:]]*2[[:space:]].*8300' <<<"${partition_table}" || die "GPT partition 2 is not a Linux root partition (8300)"

    LOOP_DEVICE="$(losetup --find --show --partscan "${RAW_IMAGE}")"
    partprobe "${LOOP_DEVICE}" || true
    EFI_PART="$(wait_for_partition 1)"
    ROOT_PART="$(wait_for_partition 2)"
    [[ -b "${EFI_PART}" && -b "${ROOT_PART}" ]] || die "kernel did not expose temporary GPT partitions"
}

verify_filesystems_and_boot() {
    local root_type efi_type root_label initrd module archive_name archive_listing
    root_type="$(blkid -s TYPE -o value "${ROOT_PART}")"
    efi_type="$(blkid -s TYPE -o value "${EFI_PART}")"
    root_label="$(blkid -s LABEL -o value "${ROOT_PART}")"
    [[ "${efi_type}" == "vfat" ]] || die "EFI system partition is not vfat"
    [[ "${root_type}" == "ext4" ]] || die "root partition is not ext4"
    [[ "${root_label}" == "${EXPECTED_LABEL}" ]] || die "root label is ${root_label:-empty}, expected ${EXPECTED_LABEL}"

    ROOT_MOUNT="${WORK_DIR}/root"
    EFI_MOUNT="${WORK_DIR}/efi"
    mkdir -p "${ROOT_MOUNT}" "${EFI_MOUNT}"
    mount -o ro "${ROOT_PART}" "${ROOT_MOUNT}"
    mount -o ro "${EFI_PART}" "${EFI_MOUNT}"
    [[ -s "${EFI_MOUNT}/EFI/BOOT/BOOTX64.EFI" ]] || die "EFI fallback BOOTX64.EFI is missing"
    [[ -s "${ROOT_MOUNT}/boot/grub/grub.cfg" ]] || die "installed GRUB configuration is missing"
    grep -Fq "__MING_ROOT_UUID__" "${ROOT_MOUNT}/boot/grub/grub.cfg" && die "installed GRUB configuration still contains __MING_ROOT_UUID__"

    local lightdm_config="${ROOT_MOUNT}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf"
    [[ -f "${lightdm_config}" ]] || die "installed LightDM configuration is missing"
    grep -qxF "autologin-session=xfce" "${lightdm_config}" || die "VHDX still selects the Live installer session"
    grep -qxF "user-session=xfce" "${lightdm_config}" || die "VHDX user session is not Xfce"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/default.target" ]] || die "VHDX does not set graphical.target"
    [[ "$(readlink "${ROOT_MOUNT}/etc/systemd/system/default.target")" == */graphical.target ]] || die "VHDX default.target does not point to graphical.target"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/display-manager.service" ]] || die "VHDX display-manager.service is missing"
    [[ "$(readlink "${ROOT_MOUNT}/etc/systemd/system/display-manager.service")" == */lightdm.service ]] || die "VHDX display-manager.service does not point to LightDM"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/lightdm.service" ]] || die "VHDX does not enable LightDM"
    for live_entry in \
        "${ROOT_MOUNT}/usr/share/xsessions/ming-installer.desktop" \
        "${ROOT_MOUNT}/etc/systemd/system/ming-live-installer.service" \
        "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/ming-live-installer.service"; do
        [[ ! -e "${live_entry}" ]] || die "Live installer entry remains in VHDX: ${live_entry}"
    done

    initrd="$(find "${ROOT_MOUNT}/boot" -maxdepth 1 -type f -name 'initrd.img-*' -printf '%f\n' | sort -V | tail -n 1)"
    [[ -n "${initrd}" ]] || die "root partition has no initramfs"
    archive_listing="$(lsinitramfs "${ROOT_MOUNT}/boot/${initrd}")"
    for module in "${HYPERV_INITRAMFS_MODULES[@]}"; do
        archive_name="${module//_/-}"
        grep -Eq "(^|/)(${module}|${archive_name})\\.ko(\\.(xz|zst|gz))?$" <<<"${archive_listing}" || die "initramfs is missing ${module}"
    done
}

main() {
    parse_args "$@"
    preflight
    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ming-hyperv-vhdx-inspect.XXXXXX")"
    trap cleanup EXIT

    verify_manifest_and_checksum
    verify_vhdx_format
    attach_raw_copy
    verify_filesystems_and_boot

    python3 - "${IMAGE}" "${EXPECTED_LABEL}" <<'PY'
import json
import pathlib
import sys
print(json.dumps({
    "ok": True,
    "image": str(pathlib.Path(sys.argv[1])),
    "firmware": "uefi-gen2",
    "partition_table": "gpt",
    "root_label": sys.argv[2],
    "efi_fallback": "/EFI/BOOT/BOOTX64.EFI",
    "installed_desktop_identity": True,
}, ensure_ascii=False))
PY
}

main "$@"
