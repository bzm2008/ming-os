#!/usr/bin/env bash
# Build a bootable Ming OS VHDX for Hyper-V Generation 2 (UEFI) only.
set -Eeuo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly GEN2_ONLY=1
readonly DEFAULT_LABEL="MING_OS_2633"
readonly EFI_LABEL="MING_EFI"
readonly DEFAULT_MIN_DISK_GIB=16
readonly EFI_SIZE_MIB=512
readonly -a HYPERV_INITRAMFS_MODULES=(
    hv_vmbus
    hv_storvsc
    hv_netvsc
    hid_hyperv
)

ROOTFS=""
ISO=""
OUTPUT=""
LABEL="${DEFAULT_LABEL}"
DISK_SIZE=""
FORCE=0
WORK_DIR=""
RAW_IMAGE=""
LOOP_DEVICE=""
ROOT_MOUNT=""
EFI_MOUNT=""
EFI_PART=""
ROOT_PART=""
EFI_UUID=""
ROOT_UUID=""
KERNEL_VERSION=""
SOURCE_ROOTFS=""
SOURCE_DESCRIPTION=""
STAGED_OUTPUT=""

die() {
    printf '%s: %s\n' "${SCRIPT_NAME}" "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  sudo scripts/build-hyperv-gen2-vhdx.sh --rootfs DIR --output FILE.vhdx [options]
  sudo scripts/build-hyperv-gen2-vhdx.sh --iso FILE.iso --output FILE.vhdx [options]

Build a Ming OS VHDX for Hyper-V Generation 2 (UEFI) only.

Required:
  --rootfs DIR       Completed installed/chroot root filesystem, or
  --iso FILE.iso     Ming ISO containing /live/filesystem.squashfs.
  --output FILE      Destination VHDX path. It must end in .vhdx.

Options:
  --label LABEL      ext4 root label (default: MING_OS_2633)
  --size SIZE        Virtual disk size accepted by qemu-img (for example: 24G)
  --force            Replace an existing destination after all checks succeed.
  --help             Show this help.

The image is deliberately Hyper-V Generation 2 / UEFI only. It does not
contain an i386-pc boot loader and must be created with Secure Boot disabled.
EOF
}

require_root() {
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "root privileges are required to create loop devices and mount filesystems"
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
    for mount_path in "${ROOT_MOUNT}/proc" "${ROOT_MOUNT}/sys" "${ROOT_MOUNT}/dev" "${ROOT_MOUNT}"; do
        if [[ -n "${ROOT_MOUNT}" ]] && mountpoint -q "${mount_path}" 2>/dev/null; then
            umount -R -l "${mount_path}" || true
        fi
    done
    if [[ -n "${LOOP_DEVICE}" ]]; then
        losetup -d "${LOOP_DEVICE}" || true
    fi
    if [[ -n "${STAGED_OUTPUT}" && -e "${STAGED_OUTPUT}" ]]; then
        rm -f -- "${STAGED_OUTPUT}" || true
    fi
    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf -- "${WORK_DIR}" || true
    fi
    exit "${status}"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --rootfs)
                [[ $# -ge 2 ]] || die "--rootfs requires a directory"
                ROOTFS="$2"
                shift 2
                ;;
            --iso)
                [[ $# -ge 2 ]] || die "--iso requires an ISO path"
                ISO="$2"
                shift 2
                ;;
            --output)
                [[ $# -ge 2 ]] || die "--output requires a destination path"
                OUTPUT="$2"
                shift 2
                ;;
            --label)
                [[ $# -ge 2 ]] || die "--label requires a value"
                LABEL="$2"
                shift 2
                ;;
            --size)
                [[ $# -ge 2 ]] || die "--size requires a qemu-img size"
                DISK_SIZE="$2"
                shift 2
                ;;
            --force)
                FORCE=1
                shift
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

    [[ -n "${OUTPUT}" ]] || die "--output is required"
    if [[ -n "${ROOTFS}" && -n "${ISO}" ]] || [[ -z "${ROOTFS}" && -z "${ISO}" ]]; then
        die "exactly one of --rootfs or --iso is required"
    fi
    [[ "${OUTPUT}" == *.vhdx ]] || die "--output must end in .vhdx"
    [[ "${LABEL}" =~ ^[A-Za-z0-9_.-]{1,16}$ ]] || die "--label must be 1-16 ASCII letters, numbers, dot, underscore, or hyphen"
}

preflight() {
    [[ "$(uname -s)" == "Linux" ]] || die "a Linux host is required; Windows PowerShell and Hyper-V cannot create this artifact directly"
    require_root
    for tool in awk blkid chroot cp du find grep lsinitramfs losetup mkfs.ext4 mkfs.vfat mktemp mount mountpoint partprobe partx python3 qemu-img readlink realpath rsync sed sgdisk sha256sum sleep sort umount; do
        require_cmd "${tool}"
    done
    if [[ -n "${ISO}" ]]; then
        require_cmd xorriso
        require_cmd unsquashfs
        [[ -f "${ISO}" ]] || die "ISO does not exist: ${ISO}"
    else
        [[ -d "${ROOTFS}" ]] || die "rootfs directory does not exist: ${ROOTFS}"
    fi
    if [[ -e "${OUTPUT}" && "${FORCE}" -ne 1 ]]; then
        die "destination already exists (pass --force after checking it): ${OUTPUT}"
    fi
}

validate_source_rootfs() {
    [[ -f "${SOURCE_ROOTFS}/etc/os-release" ]] || die "source does not look like a Linux root filesystem: ${SOURCE_ROOTFS}"
    compgen -G "${SOURCE_ROOTFS}/boot/vmlinuz-*" >/dev/null || die "source rootfs has no kernel in /boot"
    compgen -G "${SOURCE_ROOTFS}/boot/initrd.img-*" >/dev/null || die "source rootfs has no initramfs in /boot"
}

prepare_source() {
    if [[ -n "${ROOTFS}" ]]; then
        SOURCE_ROOTFS="$(realpath -e "${ROOTFS}")"
        SOURCE_DESCRIPTION="rootfs:${SOURCE_ROOTFS}"
    else
        local squashfs="${WORK_DIR}/filesystem.squashfs"
        SOURCE_ROOTFS="${WORK_DIR}/source-rootfs"
        xorriso -osirrox on -indev "${ISO}" -extract /live/filesystem.squashfs "${squashfs}" >/dev/null
        [[ -s "${squashfs}" ]] || die "ISO does not contain /live/filesystem.squashfs: ${ISO}"
        unsquashfs -d "${SOURCE_ROOTFS}" "${squashfs}" >/dev/null
        SOURCE_DESCRIPTION="iso:$(realpath -e "${ISO}")"
    fi
    validate_source_rootfs
}

default_disk_size() {
    local source_bytes minimum_bytes gibibyte whole_gib
    source_bytes="$(du -sB1 "${SOURCE_ROOTFS}" | awk '{print $1}')"
    gibibyte=$((1024 * 1024 * 1024))
    minimum_bytes=$((source_bytes + 3 * gibibyte))
    whole_gib=$(((minimum_bytes + gibibyte - 1) / gibibyte))
    if ((whole_gib < DEFAULT_MIN_DISK_GIB)); then
        whole_gib=${DEFAULT_MIN_DISK_GIB}
    fi
    printf '%sG\n' "${whole_gib}"
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

create_partitioned_disk() {
    local disk_bytes minimum_bytes source_bytes gibibyte
    [[ -n "${DISK_SIZE}" ]] || DISK_SIZE="$(default_disk_size)"
    qemu-img create -f raw "${RAW_IMAGE}" "${DISK_SIZE}" >/dev/null

    source_bytes="$(du -sB1 "${SOURCE_ROOTFS}" | awk '{print $1}')"
    gibibyte=$((1024 * 1024 * 1024))
    minimum_bytes=$((source_bytes + 3 * gibibyte))
    disk_bytes="$(qemu-img info --output=json "${RAW_IMAGE}" | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])')"
    ((disk_bytes >= minimum_bytes)) || die "--size is too small for the source rootfs; need at least ${minimum_bytes} bytes"

    sgdisk --zap-all "${RAW_IMAGE}" >/dev/null
    sgdisk --clear \
        --new=1:2048:+${EFI_SIZE_MIB}M --typecode=1:ef00 --change-name=1:"${EFI_LABEL}" \
        --new=2:0:0 --typecode=2:8300 --change-name=2:MING_ROOT \
        "${RAW_IMAGE}" >/dev/null
    sgdisk --verify "${RAW_IMAGE}" >/dev/null

    LOOP_DEVICE="$(losetup --find --show --partscan "${RAW_IMAGE}")"
    partprobe "${LOOP_DEVICE}" || true
    EFI_PART="$(wait_for_partition 1)"
    ROOT_PART="$(wait_for_partition 2)"
    [[ -b "${EFI_PART}" && -b "${ROOT_PART}" ]] || die "kernel did not expose GPT partitions for the temporary VHDX disk"

    mkfs.vfat -F 32 -n "${EFI_LABEL}" "${EFI_PART}" >/dev/null
    mkfs.ext4 -F -L "${LABEL}" "${ROOT_PART}" >/dev/null
    EFI_UUID="$(blkid -s UUID -o value "${EFI_PART}")"
    ROOT_UUID="$(blkid -s UUID -o value "${ROOT_PART}")"
}

copy_rootfs() {
    mkdir -p "${ROOT_MOUNT}"
    mount "${ROOT_PART}" "${ROOT_MOUNT}"
    rsync -aHAX --numeric-ids --one-file-system \
        --exclude='/dev/***' --exclude='/proc/***' --exclude='/sys/***' \
        --exclude='/run/***' --exclude='/tmp/***' --exclude='/lost+found' \
        "${SOURCE_ROOTFS}/" "${ROOT_MOUNT}/"
    mkdir -p "${ROOT_MOUNT}/dev" "${ROOT_MOUNT}/proc" "${ROOT_MOUNT}/sys"
    mkdir -p "${EFI_MOUNT}"
    mount "${EFI_PART}" "${EFI_MOUNT}"
}

write_target_fstab() {
    mkdir -p "${ROOT_MOUNT}/etc"
    cat > "${ROOT_MOUNT}/etc/fstab" <<EOF
# Generated by ${SCRIPT_NAME} for Hyper-V Generation 2.
UUID=${ROOT_UUID} / ext4 defaults 0 1
UUID=${EFI_UUID} /boot/efi vfat umask=0077 0 1
EOF
}

replace_calamares_root_uuid_placeholder() {
    local ming_grub="${ROOT_MOUNT}/etc/grub.d/09_ming_os"
    [[ -f "${ming_grub}" ]] || return 0

    if grep -Fq "__MING_ROOT_UUID__" "${ming_grub}"; then
        sed -i "s|__MING_ROOT_UUID__|${ROOT_UUID}|g" "${ming_grub}"
    fi
    grep -Fq "__MING_ROOT_UUID__" "${ming_grub}" && die "could not replace __MING_ROOT_UUID__ in ${ming_grub}"
}

find_target_unit() {
    local unit_name="$1" candidate
    for candidate in \
        "/lib/systemd/system/${unit_name}" \
        "/usr/lib/systemd/system/${unit_name}"; do
        if [[ -f "${ROOT_MOUNT}${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    die "source rootfs is missing required systemd unit: ${unit_name}"
}

prepare_installed_desktop_identity() {
    local lightdm_unit graphical_unit base installer_entry
    lightdm_unit="$(find_target_unit lightdm.service)"
    graphical_unit="$(find_target_unit graphical.target)"

    mkdir -p "${ROOT_MOUNT}/etc/lightdm/lightdm.conf.d"
    cat > "${ROOT_MOUNT}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf" <<'EOF'
[Seat:*]
autologin-user=user
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
EOF

    mkdir -p \
        "${ROOT_MOUNT}/etc/systemd/system" \
        "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants"
    ln -sfn "${graphical_unit}" "${ROOT_MOUNT}/etc/systemd/system/default.target"
    ln -sfn "${lightdm_unit}" "${ROOT_MOUNT}/etc/systemd/system/display-manager.service"
    ln -sfn "${lightdm_unit}" "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/lightdm.service"

    rm -f \
        "${ROOT_MOUNT}/usr/share/xsessions/ming-installer.desktop" \
        "${ROOT_MOUNT}/etc/systemd/system/ming-live-installer.service" \
        "${ROOT_MOUNT}/lib/systemd/system/ming-live-installer.service" \
        "${ROOT_MOUNT}/usr/lib/systemd/system/ming-live-installer.service" \
        "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/ming-live-installer.service" \
        "${ROOT_MOUNT}/usr/share/applications/calamares.desktop" \
        "${ROOT_MOUNT}/usr/share/applications/calamares-install-debian.desktop"

    for base in "${ROOT_MOUNT}/home" "${ROOT_MOUNT}/etc/skel"; do
        [[ -d "${base}" ]] || continue
        for installer_entry in \
            calamares-live.desktop \
            calamares.desktop \
            install-debian.desktop \
            'Install Debian.desktop' \
            $'\u5b89\u88c5 Debian.desktop'; do
            find "${base}" -type f -name "${installer_entry}" -delete 2>/dev/null || true
        done
    done
}

validate_installed_desktop_identity() {
    local lightdm_config="${ROOT_MOUNT}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf"
    local search_root installer_entry
    [[ -f "${lightdm_config}" ]] || die "installed desktop identity did not write LightDM configuration"
    grep -qxF "autologin-session=xfce" "${lightdm_config}" || die "installed desktop identity did not select the Xfce session"
    grep -qxF "user-session=xfce" "${lightdm_config}" || die "installed desktop identity did not set the Xfce user session"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/default.target" ]] || die "installed desktop identity did not set graphical.target"
    [[ "$(readlink "${ROOT_MOUNT}/etc/systemd/system/default.target")" == */graphical.target ]] || die "default.target does not point to graphical.target"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/display-manager.service" ]] || die "installed desktop identity did not point display-manager.service at LightDM"
    [[ "$(readlink "${ROOT_MOUNT}/etc/systemd/system/display-manager.service")" == */lightdm.service ]] || die "display-manager.service does not point to LightDM"
    [[ -L "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/lightdm.service" ]] || die "LightDM is not enabled for graphical.target"

    for installer_entry in \
        "${ROOT_MOUNT}/usr/share/xsessions/ming-installer.desktop" \
        "${ROOT_MOUNT}/etc/systemd/system/ming-live-installer.service" \
        "${ROOT_MOUNT}/etc/systemd/system/graphical.target.wants/ming-live-installer.service"; do
        [[ ! -e "${installer_entry}" ]] || die "Live installer entry remains in VHDX rootfs: ${installer_entry}"
    done
    for search_root in "${ROOT_MOUNT}/home" "${ROOT_MOUNT}/etc/skel"; do
        [[ -d "${search_root}" ]] || continue
        if find "${search_root}" -type f -name calamares-live.desktop -print -quit 2>/dev/null | grep -q .; then
            die "Live Calamares autostart entry remains in VHDX rootfs"
        fi
    done
}

configure_hyperv_initramfs() {
    local modules_file="${ROOT_MOUNT}/etc/initramfs-tools/modules"
    mkdir -p "$(dirname "${modules_file}")"
    touch "${modules_file}"
    for module in "${HYPERV_INITRAMFS_MODULES[@]}"; do
        grep -qxF "${module}" "${modules_file}" || printf '%s\n' "${module}" >> "${modules_file}"
    done
}

mount_chroot_support() {
    mount --rbind /dev "${ROOT_MOUNT}/dev"
    mount --make-rslave "${ROOT_MOUNT}/dev"
    mount -t proc proc "${ROOT_MOUNT}/proc"
    mount --rbind /sys "${ROOT_MOUNT}/sys"
    mount --make-rslave "${ROOT_MOUNT}/sys"
}

require_chroot_command() {
    local command_name="$1"
    chroot "${ROOT_MOUNT}" /bin/sh -c "command -v '${command_name}' >/dev/null" || die "source rootfs is missing required target command: ${command_name}"
}

install_uefi_bootloader() {
    local command_name
    for command_name in grub-install update-grub update-initramfs lsinitramfs; do
        require_chroot_command "${command_name}"
    done

    chroot "${ROOT_MOUNT}" update-initramfs -u -k all
    chroot "${ROOT_MOUNT}" grub-install \
        --target=x86_64-efi \
        --efi-directory=/boot/efi \
        --bootloader-id=MingOS \
        --removable \
        --no-nvram \
        --recheck
    chroot "${ROOT_MOUNT}" update-grub
    [[ -s "${EFI_MOUNT}/EFI/BOOT/BOOTX64.EFI" ]] || die "grub-install did not create EFI/BOOT/BOOTX64.EFI"
}

verify_generated_grub() {
    local grub_config="${ROOT_MOUNT}/boot/grub/grub.cfg"
    [[ -s "${grub_config}" ]] || die "final GRUB configuration is missing"
    grep -Fq "__MING_ROOT_UUID__" "${grub_config}" && die "final GRUB configuration still contains __MING_ROOT_UUID__"
}

latest_initramfs() {
    find "${ROOT_MOUNT}/boot" -maxdepth 1 -type f -name 'initrd.img-*' -printf '%f\n' | sort -V | tail -n 1
}

verify_hyperv_initramfs() {
    local initrd module archive_name archive_listing
    initrd="$(latest_initramfs)"
    [[ -n "${initrd}" ]] || die "target rootfs has no generated initramfs"
    KERNEL_VERSION="${initrd#initrd.img-}"
    archive_listing="$(lsinitramfs "${ROOT_MOUNT}/boot/${initrd}")"
    for module in "${HYPERV_INITRAMFS_MODULES[@]}"; do
        archive_name="${module//_/-}"
        if ! grep -Eq "(^|/)(${module}|${archive_name})\\.ko(\\.(xz|zst|gz))?$" <<<"${archive_listing}"; then
            die "initramfs is missing required Hyper-V module: ${module}"
        fi
    done
}

write_manifest() {
    local manifest_path="$1" checksum="$2"
    python3 - "${manifest_path}" "${OUTPUT}" "${SOURCE_DESCRIPTION}" "${LABEL}" "${EFI_UUID}" "${ROOT_UUID}" "${KERNEL_VERSION}" "${DISK_SIZE}" "${checksum}" "${HYPERV_INITRAMFS_MODULES[@]}" <<'PY'
import json
import os
import pathlib
import sys
import tempfile

manifest_path = pathlib.Path(sys.argv[1])
modules = sys.argv[10:]
payload = {
    "artifact": {
        "filename": pathlib.Path(sys.argv[2]).name,
        "format": "vhdx",
        "firmware": "uefi-gen2",
        "architecture": "amd64",
        "generation": 2,
        "secure_boot": "disabled-required",
        "sha256": sys.argv[9],
    },
    "source": sys.argv[3],
    "partitioning": {
        "partition_table": "gpt",
        "efi": {
            "label": "MING_EFI",
            "filesystem": "vfat",
            "uuid": sys.argv[5],
            "efi_fallback": "/EFI/BOOT/BOOTX64.EFI",
        },
        "root": {
            "label": sys.argv[4],
            "filesystem": "ext4",
            "uuid": sys.argv[6],
        },
    },
    "boot": {
        "kernel_version": sys.argv[7],
        "hyperv_initramfs_modules": modules,
    },
    "installed_desktop_identity": {
        "desktop_session": "xfce",
        "graphical_target": True,
        "lightdm_enabled": True,
        "live_installer_removed": True,
    },
    "virtual_disk_size": sys.argv[8],
}
manifest_path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=manifest_path.name + ".", dir=str(manifest_path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, manifest_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

publish_vhdx() {
    local output_dir output_name checksum manifest staged_checksum staged_manifest
    output_dir="$(dirname "${OUTPUT}")"
    output_name="$(basename "${OUTPUT}")"
    mkdir -p "${output_dir}"
    OUTPUT="$(realpath -m "${OUTPUT}")"
    STAGED_OUTPUT="$(mktemp "${output_dir}/.${output_name}.tmp.XXXXXX")"
    rm -f -- "${STAGED_OUTPUT}"

    qemu-img convert -p -f raw -O vhdx "${RAW_IMAGE}" "${STAGED_OUTPUT}"
    qemu-img check "${STAGED_OUTPUT}" >/dev/null
    checksum="$(sha256sum "${STAGED_OUTPUT}" | awk '{print $1}')"

    staged_checksum="${WORK_DIR}/$(basename "${OUTPUT}").sha256"
    staged_manifest="${WORK_DIR}/$(basename "${OUTPUT}").manifest.json"
    printf '%s  %s\n' "${checksum}" "${output_name}" > "${staged_checksum}"
    write_manifest "${staged_manifest}" "${checksum}"

    mv -f -- "${STAGED_OUTPUT}" "${OUTPUT}"
    STAGED_OUTPUT=""
    mv -f -- "${staged_checksum}" "${OUTPUT}.sha256"
    mv -f -- "${staged_manifest}" "${OUTPUT}.manifest.json"
}

main() {
    parse_args "$@"
    preflight
    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ming-hyperv-vhdx.XXXXXX")"
    RAW_IMAGE="${WORK_DIR}/ming-hyperv.raw"
    ROOT_MOUNT="${WORK_DIR}/root"
    EFI_MOUNT="${ROOT_MOUNT}/boot/efi"
    trap cleanup EXIT

    prepare_source
    create_partitioned_disk
    copy_rootfs
    write_target_fstab
    replace_calamares_root_uuid_placeholder
    prepare_installed_desktop_identity
    validate_installed_desktop_identity
    configure_hyperv_initramfs
    mount_chroot_support
    install_uefi_bootloader
    verify_generated_grub
    verify_hyperv_initramfs
    publish_vhdx

    printf 'Created Hyper-V Generation 2 VHDX: %s\n' "${OUTPUT}"
    printf 'Checksum: %s.sha256\nManifest: %s.manifest.json\n' "${OUTPUT}" "${OUTPUT}"
    printf 'Secure Boot must remain disabled for this unsigned UEFI image.\n'
}

main "$@"
