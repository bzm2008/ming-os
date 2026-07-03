#!/usr/bin/env bash
# resume_build.sh — 从 03_desktop.sh 继续打包 ISO
# 设计原则：所有逻辑（变量、函数、ISO打包）全部来自 build_onion_os.sh，
# 不维护任何独立副本，避免版本、卷标、GRUB菜单、引导链路出现漂移。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 直接 source 主构建脚本，获取所有经过验证的函数和变量 ----
# 去掉末尾的 main "$@" 调用，避免触发完整构建
_MAIN_SH="${SCRIPT_DIR}/build_onion_os.sh"
if [[ ! -f "${_MAIN_SH}" ]]; then
    echo "[ERROR] 找不到 ${_MAIN_SH}" >&2
    exit 1
fi
# 把 main 调用剥离后 source，这样所有函数和 readonly 变量都可用
eval "$(grep -v '^main ' "${_MAIN_SH}")"

# ---- 验证必要变量已就绪 ----
echo "[INFO] MING_OS_VERSION=${MING_OS_VERSION}"
echo "[INFO] ISO_VOLUME_ID=${ISO_VOLUME_ID}"
echo "[INFO] CHROOT_DIR=${CHROOT_DIR}"

# ---- 主流程：跳过 debootstrap，从模块执行继续 ----
resume_main() {
    [[ "${EUID}" -ne 0 ]] && { echo "[ERROR] 需要 root 权限"; exit 1; }

    echo "[INFO] 从 03_desktop.sh 恢复构建..."
    mount_chroot
    trap 'umount_chroot' EXIT

    # 同步最新的 assets（含壁纸、图标、settings.py）
    prepare_chroot_scripts

    # 执行剩余模块（03 及之后）
    local modules=(
        "03_desktop.sh"
        "04_garlic_claw.sh"
        "05_security_tools.sh"
        "06_ota_update.sh"
        "08_settings_hub.sh"
        "07_finalize.sh"
    )
    for mod in "${modules[@]}"; do
        local mod_path="/tmp/ming-build/modules/${mod}"
        log_step "执行模块: ${mod}"
        chroot_exec bash "${mod_path}"
        log_info "模块 ${mod} 完成"
    done

    clean_chroot
    umount_chroot
    trap - EXIT

    generate_initramfs

    # 调用主脚本里完整的 build_iso（含 build_iso_manual → grub-mkimage + El Torito + EFI）
    build_iso

    # 复制到 Windows 目录
    if [[ "${SCRIPT_DIR}" == /mnt/* ]]; then
        local win_output="${SCRIPT_DIR}/output"
        mkdir -p "${win_output}"
        local iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64.iso"
        if [[ -f "${OUTPUT_DIR}/${iso_name}" ]]; then
            cp "${OUTPUT_DIR}/${iso_name}" "${win_output}/${iso_name}"
            log_info "ISO 已复制到 Windows: ${win_output}/${iso_name}"
        fi
    fi

    log_info "=== 恢复构建完成 ==="
}

resume_main "$@"
