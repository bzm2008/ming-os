#!/bin/bash
# ============================================================================
# Onion OS 增量升级脚本
# ============================================================================
# 只运行 05_security_tools.sh 模块并重新打包 ISO

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ONION_OS_VERSION="26.0.3"
CHROOT_DIR="$SCRIPT_DIR/chroot"
OUTPUT_DIR="$SCRIPT_DIR/output"
MODULES_DIR="$SCRIPT_DIR/modules"
CONFIG_DIR="$SCRIPT_DIR/config"
ONION_USER="onion"

echo "=====> Onion OS 增量升级开始 <====="

# ======================== 工具函数 ========================
log_info() {
    echo -e "\033[1;32m[INFO]\033[0m $1"
}
log_step() {
    echo -e "\n\033[1;34m=====> $1 <=====\033[0m\n"
}

# ======================== chroot 环境管理 ========================
mount_chroot() {
    log_info "挂载 chroot 必要文件系统"
    mount --bind /dev "${CHROOT_DIR}/dev"
    mount --bind /dev/pts "${CHROOT_DIR}/dev/pts"
    mount --bind /proc "${CHROOT_DIR}/proc"
    mount --bind /sys "${CHROOT_DIR}/sys"
    mount --bind /run "${CHROOT_DIR}/run"
}

umount_chroot() {
    log_info "卸载 chroot 文件系统"
    local mounts=("dev/shm" "dev/pts" "run" "sys" "proc" "dev")
    for m in "${mounts[@]}"; do
        if mountpoint -q "${CHROOT_DIR}/${m}" 2>/dev/null; then
            umount -l "${CHROOT_DIR}/${m}" 2>/dev/null || true
        fi
    done
}

chroot_exec() {
    chroot "${CHROOT_DIR}" /usr/bin/env \
        DEBIAN_FRONTEND=noninteractive \
        DEBCONF_NONINTERACTIVE_SEEN=true \
        HOME="/root" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/bin" \
        TERM="linux" \
        ONION_OS_VERSION="${ONION_OS_VERSION}" \
        ONION_USER="${ONION_USER}" \
        "$@"
}

prepare_chroot_scripts() {
    log_info "准备 chroot 内执行环境"
    mkdir -p "${CHROOT_DIR}/tmp/onion-build/modules"
    mkdir -p "${CHROOT_DIR}/tmp/onion-build/config"
    cp -r "${MODULES_DIR}"/* "${CHROOT_DIR}/tmp/onion-build/modules/"
    cp -r "${CONFIG_DIR}"/* "${CHROOT_DIR}/tmp/onion-build/config/"
    chmod +x "${CHROOT_DIR}/tmp/onion-build/modules/"*.sh
}

# ======================== 主流程 ========================
main() {
    log_step "开始增量升级"

    if [[ ! -d "${CHROOT_DIR}" ]]; then
        echo "错误：chroot 目录不存在！"
        exit 1
    fi

    mount_chroot
    trap 'umount_chroot' EXIT

    prepare_chroot_scripts

    log_step "运行 05_security_tools.sh 模块"
    chroot_exec bash "/tmp/onion-build/modules/05_security_tools.sh"

    log_step "清理 chroot 环境"
    chroot_exec bash -c "apt clean"
    chroot_exec bash -c "rm -rf /var/lib/apt/lists/*"
    chroot_exec bash -c "rm -rf /tmp/onion-build"
    chroot_exec bash -c "rm -f /var/log/*.log /var/log/apt/*.log"
    chroot_exec bash -c "rm -f /var/cache/debconf/*-old"
    chroot_exec bash -c "> /etc/machine-id"

    log_step "增量升级完成！"
    echo "现在可以重新运行 build_onion_os.sh 的 build_iso 部分，或者手动重新打包 ISO！"
}

main "$@"
