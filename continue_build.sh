#!/bin/bash
# 继续构建 Onion OS - 从模块阶段继续

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ONION_OS_VERSION="26.0.0"
CHROOT_DIR="$SCRIPT_DIR/chroot"
OUTPUT_DIR="$SCRIPT_DIR/output"
ISO_FILENAME="onion-os-${ONION_OS_VERSION}-home-amd64.iso"
ONION_USER="onion"

echo "=====> 继续 Onion OS 构建 <====="

log_info() {
    echo -e "\033[1;32m[INFO]\033[0m $1"
}

log_error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1" >&2
}

chroot_exec() {
    DEBIAN_FRONTEND=noninteractive \
    DEBCONF_NONINTERACTIVE_SEEN=true \
    HOME=/root \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/bin \
    TERM=linux \
    ONION_OS_VERSION="${ONION_OS_VERSION}" \
    ONION_USER="${ONION_USER}" \
    chroot "$CHROOT_DIR" "$@"
}

mount_chroot() {
    log_info "挂载 chroot 文件系统"
    mount --bind /dev "${CHROOT_DIR}/dev"
    mount --bind /dev/pts "${CHROOT_DIR}/dev/pts"
    mount --bind /proc "${CHROOT_DIR}/proc"
    mount --bind /sys "${CHROOT_DIR}/sys"
    mount --bind /run "${CHROOT_DIR}/run"
}

umount_chroot() {
    log_info "卸载 chroot 文件系统"
    local mounts=("dev/shm" "dev/pts" "dev" "sys" "proc" "run")
    for m in "${mounts[@]}"; do
        if mountpoint -q "${CHROOT_DIR}/${m}" 2>/dev/null; then
            umount -l "${CHROOT_DIR}/${m}" 2>/dev/null || true
        fi
    done
}

generate_initramfs() {
    log_info "initramfs 已存在，跳过重新生成"
    # 内核和 initramfs 已存在于 /boot/ 目录
    return 0
}

clean_chroot() {
    log_info "清理 chroot 环境"
    chroot_exec apt clean
    chroot_exec apt autoremove -y --purge 2>/dev/null || true
    chroot_exec bash -c "rm -rf /var/lib/apt/lists/*"
    chroot_exec bash -c "rm -rf /tmp/*"
    chroot_exec bash -c "rm -f /var/cache/debconf/*-old"
    chroot_exec bash -c "> /etc/machine-id"
}

build_iso() {
    log_info "开始构建 ISO 镜像"
    mkdir -p "$OUTPUT_DIR"
    local iso_workdir="$OUTPUT_DIR/iso-tmp"
    rm -rf "$iso_workdir"
    mkdir -p "$iso_workdir/live" "$iso_workdir/boot/grub"

    log_info "创建 squashfs 文件系统"
    mksquashfs "$CHROOT_DIR" "$iso_workdir/live/filesystem.squashfs" -comp xz -Xbcj x86 -b 1M -noappend

    log_info "配置内核和 initramfs"
    cp "$CHROOT_DIR"/boot/vmlinuz-* "$iso_workdir/live/vmlinuz"
    cp "$CHROOT_DIR"/boot/initrd.img-* "$iso_workdir/live/initrd.img"

    log_info "配置 GRUB 引导"
    cat > "$iso_workdir/boot/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=5

menuentry "Onion OS 26.0.0 Home Edition" {
    linux /live/vmlinuz boot=live live-config.username=onion live-config.user-default-groups=audio,cdrom,dip,netdev,plugdev,sudo,video quiet splash nomodeset
    initrd /live/initrd.img
}
GRUBCFG

    log_info "生成 ISO 镜像"
    grub-mkrescue -o "$OUTPUT_DIR/$ISO_FILENAME" "$iso_workdir"

    log_info "清理临时文件"
    rm -rf "$iso_workdir"
}

# 主流程
trap 'umount_chroot 2>/dev/null' EXIT

# mount_chroot - 已挂载过，现在 chroot 已包含 live-boot

# log_info "运行 04_garlic_claw.sh"
# chroot_exec bash /tmp/onion-build/modules/04_garlic_claw.sh
# 04_garlic_claw.sh 已成功完成，直接继续

# log_info "生成 initramfs"
# generate_initramfs - initramfs 已由 live-boot 重新生成

# log_info "清理 chroot"
# clean_chroot - 先不清理，直接构建 ISO

# umount_chroot - 先不卸载

log_info "构建 ISO"
build_iso

log_info "构建完成！"
log_info "ISO 文件位置: $OUTPUT_DIR/$ISO_FILENAME"
ls -lh "$OUTPUT_DIR/$ISO_FILENAME"
