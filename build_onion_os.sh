#!/usr/bin/env bash
# ============================================================================
# Onion OS 26.2.0 Home Edition - 主构建脚本
# ============================================================================
# 设计意图：
#   在 Debian 13 (Trixie) 宿主系统上，通过 debootstrap 构建一个完整的
#   Onion OS 根文件系统，依次调用模块脚本完成系统定制，最终生成可启动 ISO。
#
# 输入：
#   无（所有参数通过常量定义在本脚本头部）
#
# 输出：
#   ${OUTPUT_DIR}/onion-os-${ONION_OS_VERSION}-home-amd64.iso
#
# 关键步骤：
#   1. 环境检查与依赖安装
#   2. debootstrap 构建 base 系统
#   3. chroot 环境中依次执行模块脚本
#   4. 生成 initramfs 与 GRUB 引导
#   5. 打包为 ISO 镜像
#
# 使用方法：
#   sudo ./build_onion_os.sh
# ============================================================================

set -euo pipefail

# ======================== 项目常量 ========================
readonly ONION_OS_NAME="Onion OS"
readonly ONION_OS_VERSION="26.2.0"
readonly ONION_OS_EDITION="Home"
readonly ONION_OS_CODENAME="onion"
readonly DEBIAN_MIRROR="https://mirrors.tuna.tsinghua.edu.cn/debian/"
readonly DEBIAN_SUITE="trixie"
readonly ARCH="amd64"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LINUX_WORKDIR="/var/tmp/onion-os-build"
readonly CHROOT_DIR="${LINUX_WORKDIR}/chroot"
readonly OUTPUT_DIR="${LINUX_WORKDIR}/output"
readonly ISO_DIR="${LINUX_WORKDIR}/iso_build"
readonly MODULES_DIR="${SCRIPT_DIR}/modules"
readonly CONFIG_DIR="${SCRIPT_DIR}/config"
readonly ONION_USER="onion"
readonly ONION_USER_PASS="onion"
readonly ROOT_PASS="root"
# 日志颜色
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

# ======================== 工具函数 ========================
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}
log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}
log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}
log_step() {
    echo -e "\n${BLUE}=====> $1 <=====${NC}\n"
}
# 检查命令是否存在，不存在则报错退出
# 参数: $1=命令名 $2=安装提示(可选)
require_cmd() {
    if ! command -v "$1" &>/dev/null; then
        log_error "缺少必要命令: $1"
        if [[ -n "${2:-}" ]]; then
            log_error "安装方法: $2"
        fi
        exit 1
    fi
}
# 检查是否以 root 运行
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "此脚本必须以 root 身份运行 (使用 sudo)"
        exit 1
    fi
}
# ======================== 环境检查 ========================
check_host_environment() {
    log_step "检查宿主系统环境"
    require_root
    require_cmd debootstrap "dnf install debootstrap (EPEL) 或 apt install debootstrap"
    require_cmd mksquashfs "dnf install squashfs-tools 或 apt install squashfs-tools"
    require_cmd xorriso "dnf install xorriso 或 apt install xorriso"
    if command -v grub-mkrescue &>/dev/null; then
        GRUB_MKRESCUE="grub-mkrescue"
    elif command -v grub2-mkrescue &>/dev/null; then
        GRUB_MKRESCUE="grub2-mkrescue"
    else
        log_error "缺少 grub-mkrescue 或 grub2-mkrescue"
        log_error "安装方法: dnf install grub2-tools-extra 或 apt install grub-pc-bin grub-efi-amd64-bin"
        exit 1
    fi
    export GRUB_MKRESCUE
    require_cmd chroot "系统内置"
    if [[ ! -d /proc/sys ]]; then
        log_error "请确保 /proc 已挂载"
        exit 1
    fi
    local free_gb
    free_gb=$(df -BG "${SCRIPT_DIR}" | awk 'NR==2 {print $4}' | tr -d 'G')
    if [[ ${free_gb} -lt 15 ]]; then
        log_warn "磁盘剩余空间不足 15GB (当前 ${free_gb}GB)，构建可能失败"
    fi
    log_info "宿主系统环境检查通过 (GRUB: ${GRUB_MKRESCUE})"
}
install_build_deps() {
    log_step "安装构建依赖"
    if command -v apt &>/dev/null; then
        apt update
        apt install -y --no-install-recommends \
            debootstrap squashfs-tools xorriso isolinux syslinux-common \
            grub-pc-bin grub-efi-amd64-bin grub-efi-amd64-signed shim-signed \
            mtools dosfstools
    elif command -v dnf &>/dev/null; then
        dnf install -y debootstrap squashfs-tools xorriso \
            grub2-tools grub2-tools-extra grub2-efi-x64-modules \
            mtools dosfstools syslinux
    elif command -v yum &>/dev/null; then
        yum install -y debootstrap squashfs-tools xorriso \
            grub2-tools grub2-tools-extra grub2-efi-x64-modules \
            mtools dosfstools syslinux
    else
        log_error "未找到 apt/dnf/yum 包管理器"
        exit 1
    fi
    log_info "构建依赖安装完成"
}
# ======================== debootstrap 构建基础系统 ========================
run_debootstrap() {
    log_step "执行 debootstrap 构建 ${DEBIAN_SUITE} 基础系统"
    if [[ -d "${CHROOT_DIR}" ]]; then
        log_warn "chroot 目录已存在，清除旧数据..."
        umount_chroot || true
        rm -rf "${CHROOT_DIR}"
    fi
    mkdir -p "${CHROOT_DIR}"
    debootstrap \
        --arch="${ARCH}" \
        --variant=minbase \
        --include=ca-certificates,gnupg2,apt-transport-https \
        "${DEBIAN_SUITE}" \
        "${CHROOT_DIR}" \
        "${DEBIAN_MIRROR}"
    log_info "debootstrap 完成"
}
# ======================== chroot 环境管理 ========================
mount_chroot() {
    log_info "挂载 chroot 必要文件系统"
    mount --bind /dev "${CHROOT_DIR}/dev"
    mount --bind /dev/pts "${CHROOT_DIR}/dev/pts"
    mount --bind /proc "${CHROOT_DIR}/proc"
    mount --bind /sys "${CHROOT_DIR}/sys"
    mount --bind /run "${CHROOT_DIR}/run"
    # 为安全起见，阻止 chroot 访问宿主 udev
    if [[ -d "${CHROOT_DIR}/dev/shm" ]]; then
        mount --bind /dev/shm "${CHROOT_DIR}/dev/shm" 2>/dev/null || true
    fi
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
# 在 chroot 中执行命令
# 参数: $@ 要执行的命令
chroot_exec() {
    chroot "${CHROOT_DIR}" /usr/bin/env \
        DEBIAN_FRONTEND=noninteractive \
        DEBCONF_NONINTERACTIVE_SEEN=true \
        HOME="/root" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/bin" \
        TERM="linux" \
        ONION_OS_VERSION="${ONION_OS_VERSION}" \
        ONION_USER="${ONION_USER}" \
        ONION_USER_PASS="${ONION_USER_PASS}" \
        ROOT_PASS="${ROOT_PASS}" \
        "$@"
}
# 将模块脚本和配置文件复制到 chroot 中
prepare_chroot_scripts() {
    log_info "准备 chroot 内执行环境"
    mkdir -p "${CHROOT_DIR}/tmp/onion-build/modules"
    mkdir -p "${CHROOT_DIR}/tmp/onion-build/config"
    cp -r "${MODULES_DIR}"/* "${CHROOT_DIR}/tmp/onion-build/modules/"
    cp -r "${CONFIG_DIR}"/* "${CHROOT_DIR}/tmp/onion-build/config/"
    chmod +x "${CHROOT_DIR}/tmp/onion-build/modules/"*.sh
}
# ======================== 模块脚本执行 ========================
run_modules() {
    log_step "在 chroot 中执行模块脚本"
    prepare_chroot_scripts
    local modules=(
        "01_base.sh"
        "02_apps.sh"
        "03_desktop.sh"
        "04_garlic_claw.sh"
        "05_security_tools.sh"
        "06_ota_update.sh"
        "07_finalize.sh"
    )
    for mod in "${modules[@]}"; do
        local mod_path="/tmp/onion-build/modules/${mod}"
        if [[ -f "${CHROOT_DIR}${mod_path}" ]]; then
            log_step "执行模块: ${mod}"
            chroot_exec bash "${mod_path}"
            log_info "模块 ${mod} 执行完成"
        else
            log_error "模块脚本不存在: ${mod}"
            exit 1
        fi
    done
    log_info "所有模块执行完成"
}
# ======================== 清理 chroot ========================
clean_chroot() {
    log_step "清理 chroot 环境"
    chroot_exec bash -c "apt clean"
    chroot_exec bash -c "rm -rf /var/lib/apt/lists/*"
    chroot_exec bash -c "rm -rf /tmp/onion-build"
    chroot_exec bash -c "rm -f /var/log/*.log /var/log/apt/*.log"
    chroot_exec bash -c "rm -f /var/cache/debconf/*-old"
    chroot_exec bash -c "> /etc/machine-id"
    log_info "chroot 清理完成"
}
# ======================== 生成 initramfs ========================
generate_initramfs() {
    log_step "生成 initramfs"
    chroot_exec bash -c "update-initramfs -c -k all"
    log_info "initramfs 生成完成"
}
# ======================== ISO 镜像打包 ========================
build_iso() {
    log_step "构建 ISO 镜像"
    rm -rf "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}/boot/grub"
    mkdir -p "${ISO_DIR}/live"

    local kernel_version
    kernel_version=$(ls "${CHROOT_DIR}/boot/vmlinuz-"* | head -1 | xargs basename)
    local kernel_path="${CHROOT_DIR}/boot/${kernel_version}"
    local initrd_path
    initrd_path=$(ls "${CHROOT_DIR}/boot/initrd.img-"* | head -1)
    cp "${kernel_path}" "${ISO_DIR}/live/vmlinuz"
    cp "${initrd_path}" "${ISO_DIR}/live/initrd"

    log_info "生成 squashfs 文件系统..."
    mksquashfs "${CHROOT_DIR}" "${ISO_DIR}/live/filesystem.squashfs" \
        -comp xz \
        -Xbcj x86 \
        -b 1M \
        -no-progress

    cat > "${ISO_DIR}/boot/grub/grub.cfg" << GRUBCFG
set default=0
set timeout=12

insmod part_gpt
insmod part_msdos
insmod ext2
insmod iso9660
insmod all_video
insmod gfxterm
insmod png
insmod font
insmod search
insmod gfxmenu
insmod gfxterm_background
insmod jpeg

search --no-floppy --file --set=root /live/vmlinuz
set prefix=(\$root)/boot/grub

loadfont /boot/grub/fonts/unicode.pf2
terminal_output gfxterm

set color_normal=white/black
set color_highlight=black/light-gray
set menu_color_normal=white/black
set menu_color_highlight=black/white

# 1920x1080 仅在支持时使用，否则自动回退
set gfxmode=auto

menuentry "启动 ${ONION_OS_NAME} ${ONION_OS_VERSION} ${ONION_OS_EDITION}" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet splash
    initrd /live/initrd
}

menuentry "启动 ${ONION_OS_NAME} ${ONION_OS_VERSION} (兼容模式 / VirtualBox)" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet splash nomodeset vga=791
    initrd /live/initrd
}

menuentry "启动 ${ONION_OS_NAME} ${ONION_OS_VERSION} (低分辨率 1024x768 / 老旧显卡)" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet splash nomodeset xforcevesa vga=792
    initrd /live/initrd
}

menuentry "启动 ${ONION_OS_NAME} ${ONION_OS_VERSION} (最低分辨率 800x600 / 极旧显卡)" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet splash nomodeset xforcevesa vga=788 video=800x600
    initrd /live/initrd
}

menuentry "${ONION_OS_NAME} 安全模式" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet nomodeset vga=normal noapic noacpi
    initrd /live/initrd
}

menuentry "${ONION_OS_NAME} 调试模式" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 debug nomodeset vga=normal
    initrd /live/initrd
}

menuentry "安装 ${ONION_OS_NAME} 到硬盘" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 quiet splash nomodeset install
    initrd /live/initrd
}

menuentry "低内存模式 (1.0GB)" {
    linux /live/vmlinuz boot=live components live-config username=${ONION_USER} user-fullname=Onion_OS_User hostname=onion-os locales=zh_CN.UTF-8 nomodeset mem=1024M
    initrd /live/initrd
}

menuentry "内存检测 Memtest86+" {
    linux /boot/memtest86+x64.efi
}
GRUBCFG

    log_info "配置 GRUB 字体..."
    mkdir -p "${ISO_DIR}/boot/grub/fonts"
    if [[ -f /usr/share/grub/unicode.pf2 ]]; then
        cp /usr/share/grub/unicode.pf2 "${ISO_DIR}/boot/grub/fonts/"
    fi

    if [[ -f "${CHROOT_DIR}/boot/memtest86+x64.efi" ]]; then
        mkdir -p "${ISO_DIR}/boot"
        cp "${CHROOT_DIR}/boot/memtest86+x64.efi" "${ISO_DIR}/boot/"
    fi

    log_info "生成 ISO 镜像文件..."
    local iso_name="onion-os-${ONION_OS_VERSION}-${ONION_OS_EDITION,,}-amd64.iso"

    build_iso_manual "${iso_name}"

    if [[ ! -f "${OUTPUT_DIR}/${iso_name}" ]]; then
        log_warn "手动 GRUB ISO 构建未生成 ISO，尝试 grub-mkrescue 回退..."
        ${GRUB_MKRESCUE:-grub-mkrescue} \
            --output="${OUTPUT_DIR}/${iso_name}" \
            "${ISO_DIR}" \
            2>&1 || true
    fi

    if [[ -f "${OUTPUT_DIR}/${iso_name}" ]]; then
        local iso_size
        iso_size=$(du -sh "${OUTPUT_DIR}/${iso_name}" | cut -f1)
        log_info "ISO 镜像生成成功: ${OUTPUT_DIR}/${iso_name} (${iso_size})"
    else
        log_error "ISO 镜像生成失败"
        exit 1
    fi
    rm -rf "${ISO_DIR}"

    if [[ "${SCRIPT_DIR}" == /mnt/* ]]; then
        local win_output_dir="${SCRIPT_DIR}/output"
        mkdir -p "${win_output_dir}"
        cp "${OUTPUT_DIR}/${iso_name}" "${win_output_dir}/${iso_name}"
        log_info "ISO 已复制到 Windows 目录: ${win_output_dir}/${iso_name}"
    fi
}

build_iso_manual() {
    local iso_name="$1"
    local iso_workdir="${ISO_DIR}"

    mkdir -p "${iso_workdir}/EFI/BOOT"

    mkdir -p "${iso_workdir}/boot/grub/x86_64-efi"
    if [[ -d /usr/lib/grub/x86_64-efi ]]; then
        cp /usr/lib/grub/x86_64-efi/*.mod "${iso_workdir}/boot/grub/x86_64-efi/"
        cp /usr/lib/grub/x86_64-efi/*.lst "${iso_workdir}/boot/grub/x86_64-efi/" 2>/dev/null || true
        cp /usr/lib/grub/x86_64-efi/*.efi "${iso_workdir}/boot/grub/x86_64-efi/" 2>/dev/null || true
    fi

    mkdir -p "${iso_workdir}/boot/grub/i386-pc"
    if [[ -d /usr/lib/grub/i386-pc ]]; then
        cp /usr/lib/grub/i386-pc/*.mod "${iso_workdir}/boot/grub/i386-pc/" 2>/dev/null || true
        cp /usr/lib/grub/i386-pc/*.lst "${iso_workdir}/boot/grub/i386-pc/" 2>/dev/null || true
    fi

    # Both BIOS and UEFI fallback boot images embed this tiny config. Without it
    # GRUB can start but stop at the "Welcome to GRUB" prompt instead of loading
    # the Onion OS menu.
    cat > "${iso_workdir}/boot/grub/early-grub.cfg" << 'EOF'
search --no-floppy --file --set=root /live/vmlinuz
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
EOF

    if command -v grub-mkimage &>/dev/null && [[ -d /usr/lib/grub/x86_64-efi ]]; then
        grub-mkimage \
            -O x86_64-efi \
            -p /boot/grub \
            -c "${iso_workdir}/boot/grub/early-grub.cfg" \
            -o "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" \
            part_gpt part_msdos fat ntfs exfat iso9660 udf ext2 all_video font gfxterm normal configfile \
            search search_fs_file linux chain boot 2>/dev/null || true
    fi

    if [[ ! -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]] && [[ -f /usr/lib/grub/x86_64-efi/monolithic/grubx64.efi ]]; then
        cp /usr/lib/grub/x86_64-efi/monolithic/grubx64.efi "${iso_workdir}/EFI/BOOT/BOOTX64.EFI"
        log_warn "使用未嵌入 early-grub.cfg 的 monolithic UEFI GRUB 作为回退"
    fi

    if [[ -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]]; then
        log_info "已生成 EFI 引导文件 (BOOTX64.EFI with early config)"
    fi

    if command -v "${GRUB_MKRESCUE:-grub-mkrescue}" &>/dev/null; then
        log_info "使用 grub-mkrescue 构建 GRUB2 hybrid ISO (Rufus ISO/DD + Ventoy)"
        local rescue_src="${iso_workdir}.grubrescue"
        rm -rf "${rescue_src}"
        cp -a "${iso_workdir}" "${rescue_src}"
        if [[ -f "${rescue_src}/EFI/BOOT/BOOTX64.EFI" ]]; then
            mkdir -p "${rescue_src}/efi/boot"
            cp "${rescue_src}/EFI/BOOT/BOOTX64.EFI" "${rescue_src}/efi/boot/bootx64.efi"
            rm -rf "${rescue_src}/EFI"
        fi
        rm -f "${rescue_src}/boot/grub/efi.img" "${rescue_src}/boot/grub/boot.cat"
        if ${GRUB_MKRESCUE:-grub-mkrescue} --output="${OUTPUT_DIR}/${iso_name}" "${rescue_src}" 2>&1; then
            rm -rf "${rescue_src}"
            return 0
        fi
        rm -rf "${rescue_src}"
        log_warn "grub-mkrescue hybrid 构建失败，回退到手动 xorriso"
    fi
    if command -v grub-mkimage &>/dev/null && [[ -f /usr/lib/grub/i386-pc/cdboot.img ]]; then
        grub-mkimage \
            -O i386-pc \
            -p /boot/grub \
            -c "${iso_workdir}/boot/grub/early-grub.cfg" \
            -o "${iso_workdir}/boot/grub/i386-pc/core.img" \
            biosdisk iso9660 udf part_gpt part_msdos normal configfile search search_fs_file \
            linux all_video font gfxterm boot 2>/dev/null || true

        if [[ -f "${iso_workdir}/boot/grub/i386-pc/core.img" ]]; then
            cat /usr/lib/grub/i386-pc/cdboot.img \
                "${iso_workdir}/boot/grub/i386-pc/core.img" \
                > "${iso_workdir}/boot/grub/i386-pc/eltorito.img"
        fi
    fi

    if [[ -f "${iso_workdir}/boot/grub/i386-pc/eltorito.img" ]]; then
        log_info "使用 xorriso 手动构建可引导 ISO (BIOS + UEFI)..."

        local efi_data=""
        if [[ -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]]; then
            efi_data="-eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot"
            local efi_img="${iso_workdir}/boot/grub/efi.img"
            local efi_tmpdir
            efi_tmpdir="$(mktemp -d)"
            mkdir -p "${efi_tmpdir}/EFI/BOOT"
            cp "${iso_workdir}/EFI/BOOT/"* "${efi_tmpdir}/EFI/BOOT/"
            dd if=/dev/zero of="${efi_img}" bs=1M count=4 2>/dev/null
            mkfs.vfat -F 12 "${efi_img}" 2>/dev/null
            mmd -i "${efi_img}" ::EFI ::EFI/BOOT 2>/dev/null
            mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/BOOTX64.EFI" ::EFI/BOOT/BOOTX64.EFI 2>/dev/null
            if [[ -f "${efi_tmpdir}/EFI/BOOT/grubx64.efi" ]]; then
                mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/grubx64.efi" ::EFI/BOOT/grubx64.efi 2>/dev/null
            fi
            rm -rf "${efi_tmpdir}"
        fi

        local isohybrid_mbr=""
        local hybrid_mbr_args=()
        for candidate in \
            /usr/lib/grub/i386-pc/isohdpfx.bin \
            /usr/lib/ISOLINUX/isohdpfx.bin \
            /usr/lib/syslinux/bios/isohdpfx.bin; do
            if [[ -f "${candidate}" ]]; then
                isohybrid_mbr="${candidate}"
                break
            fi
        done
        if [[ -n "${isohybrid_mbr}" ]]; then
            hybrid_mbr_args=(-isohybrid-mbr "${isohybrid_mbr}")
            log_info "使用 isohybrid MBR: ${isohybrid_mbr}"
        else
            log_warn "未找到 isohdpfx.bin，ISO 仍可通过 BIOS/UEFI 引导，但可能不支持部分 USB-HDD 混合启动模式"
        fi

        xorriso -as mkisofs \
            -iso-level 3 \
            -full-iso9660-filenames \
            -R -J -joliet-long \
            -c boot/grub/boot.cat \
            -b boot/grub/i386-pc/eltorito.img \
            -no-emul-boot \
            -boot-load-size 4 \
            -boot-info-table \
            ${efi_data} \
            -isohybrid-gpt-basdat \
            "${hybrid_mbr_args[@]}" \
            -output "${OUTPUT_DIR}/${iso_name}" \
            "${iso_workdir}" \
            2>&1
    else
        log_warn "缺少 BIOS 引导文件，使用 xorriso 简单模式..."
        xorriso -as mkisofs \
            -iso-level 3 \
            -full-iso9660-filenames \
            -R -J \
            -o "${OUTPUT_DIR}/${iso_name}" \
            "${iso_workdir}" \
            2>&1
    fi
}
# ======================== 主流程 ========================
main() {
    echo -e "${GREEN}"
    echo "  ╔══════════════════════════════════════════╗"
    echo "  ║     Onion OS ${ONION_OS_VERSION} Home Edition         ║"
    echo "  ║     层层精简，层层用心                    ║"
    echo "  ╚══════════════════════════════════════════╝"
    echo -e "${NC}"
    local start_time
    start_time=$(date +%s)
    check_host_environment
    install_build_deps
    mkdir -p "${LINUX_WORKDIR}"
    run_debootstrap
    mount_chroot
    trap 'umount_chroot' EXIT
    run_modules
    generate_initramfs
    clean_chroot
    umount_chroot
    trap - EXIT
    build_iso
    local end_time
    end_time=$(date +%s)
    local duration=$(( end_time - start_time ))
    local minutes=$(( duration / 60 ))
    local seconds=$(( duration % 60 ))
    echo -e "${GREEN}"
    echo "  ╔══════════════════════════════════════════╗"
    echo "  ║   Onion OS 构建完成！                     ║"
    echo "  ║   耗时: ${minutes}分${seconds}秒                            ║"
    echo "  ╚══════════════════════════════════════════╝"
    echo -e "${NC}"
}
main "$@"
