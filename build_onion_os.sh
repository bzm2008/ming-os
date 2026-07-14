#!/usr/bin/env bash
# ============================================================================
# Ming OS 26.3.0 Home Edition - 主构建脚本
# ============================================================================
# 设计意图：
#   在 Debian 13 (Trixie) 宿主系统上，通过 debootstrap 构建一个完整的
#   Ming OS 根文件系统，依次调用模块脚本完成系统定制，最终生成可启动 ISO。
#
# 输入：
#   无（所有参数通过常量定义在本脚本头部）
#
# 输出：
#   ${OUTPUT_DIR}/ming-os-${MING_OS_VERSION}-home-amd64.iso
#
# 关键步骤：
#   1. 环境检查与依赖安装
#   2. debootstrap 构建 base 系统
#   3. chroot 环境中依次执行模块脚本
#   4. 生成 initramfs 与 GRUB 引导
#   5. 打包为 ISO 镜像
#
# 使用方法：
#   sudo ./build_ming_os.sh
# ============================================================================

set -euo pipefail

# ======================== 项目常量 ========================
readonly MING_OS_NAME="Ming OS"
readonly MING_OS_VERSION="26.3.3"
readonly MING_OS_BUILD_SUFFIX=""
readonly MING_OS_EDITION="Home"
readonly MING_OS_CODENAME="ming"
readonly ISO_VOLUME_ID="MING_OS_2633"
readonly DEBIAN_MIRROR="https://mirrors.tuna.tsinghua.edu.cn/debian/"
readonly DEBIAN_SUITE="trixie"
readonly ARCH="amd64"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LINUX_WORKDIR="/var/tmp/ming-os-build"
readonly CHROOT_DIR="${LINUX_WORKDIR}/chroot"
readonly OUTPUT_DIR="${LINUX_WORKDIR}/output"
readonly ISO_DIR="${LINUX_WORKDIR}/iso_build"
readonly MODULES_DIR="${SCRIPT_DIR}/modules"
readonly CONFIG_DIR="${SCRIPT_DIR}/config"
readonly MING_USER="user"
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
    require_cmd grub-mkimage "dnf install grub2-tools-extra 或 apt install grub-pc-bin grub-efi-amd64-bin"
    require_cmd mkfs.vfat "dnf install dosfstools 或 apt install dosfstools"
    require_cmd mcopy "dnf install mtools 或 apt install mtools"
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
    log_info "宿主系统环境检查通过 (manual xorriso + grub-mkimage)"
}
install_build_deps() {
    log_step "安装构建依赖"
    local required_bins=(debootstrap mksquashfs xorriso grub-mkimage mkfs.vfat mcopy)
    local missing_bins=()
    local bin
    for bin in "${required_bins[@]}"; do
        if ! command -v "${bin}" &>/dev/null; then
            missing_bins+=("${bin}")
        fi
    done
    if [[ ${#missing_bins[@]} -eq 0 ]]; then
        log_info "构建依赖已存在，跳过在线安装"
        return 0
    fi
    log_warn "缺少构建依赖: ${missing_bins[*]}"
    if command -v apt-get &>/dev/null; then
        local apt_ok=0
        if [[ "${MING_SKIP_APT_UPDATE:-0}" != "1" ]] && apt-get update; then
            apt_ok=1
        else
            log_warn "apt-get update 失败，改用已有缓存继续安装"
        fi
        if ! apt-get install -y --no-install-recommends \
            debootstrap squashfs-tools xorriso isolinux syslinux-common \
            grub-pc-bin grub-efi-amd64-bin grub-efi-amd64-signed shim-signed \
            mtools dosfstools; then
            if [[ "${apt_ok}" -eq 0 ]]; then
                log_error "apt 依赖安装失败且缓存不可用"
                exit 1
            fi
            log_warn "apt-get install 失败，但依赖可能已存在，继续后续检查"
        fi
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
        MING_OS_VERSION="${MING_OS_VERSION}" \
        MING_USER="${MING_USER}" \
        "$@" </dev/null
}

wait_chroot_apt_locks() {
    local attempt
    for attempt in $(seq 1 120); do
        if ! chroot_exec fuser /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/cache/apt/archives/lock >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    log_error "chroot apt/dpkg locks did not clear after 120 seconds"
}

settle_chroot_dpkg() {
    local label="$1"
    local audit_output
    log_info "Checking package database after ${label}"
    wait_chroot_apt_locks
    chroot_exec dpkg --configure -a
    wait_chroot_apt_locks
    chroot_exec apt-get -f install -y --no-install-recommends
    audit_output="$(chroot_exec dpkg --audit)"
    if [[ -n "${audit_output}" ]]; then
        log_error "dpkg audit still reports unfinished packages after ${label}"
        printf '%s\n' "${audit_output}" >&2
        return 1
    fi
}
# 将模块脚本和配置文件复制到 chroot 中
prepare_chroot_scripts() {
    log_info "准备 chroot 内执行环境"
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/modules"
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/config"
    cp -r "${MODULES_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/modules/"
    cp -r "${CONFIG_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/config/"
    chmod +x "${CHROOT_DIR}/tmp/ming-build/modules/"*.sh
    if [[ -d "${SCRIPT_DIR}/assets" ]]; then
        mkdir -p "${CHROOT_DIR}/tmp/ming-build/assets"
        cp -r "${SCRIPT_DIR}/assets/"* "${CHROOT_DIR}/tmp/ming-build/assets/" 2>/dev/null || true
    fi

    # 部署可执行的 apt-build wrapper，供模块脚本里 timeout 直接调用。
    # 根因：bash 函数无法被 timeout 启动（exec 语义），必须是真实可执行文件。
    # 该脚本断开 stdin + 关闭 pty，彻底避免后台构建时 apt/dpkg/maintainer-script 挂住。
    cat > "${CHROOT_DIR}/usr/local/sbin/apt-build" << 'APT_BUILD_WRAPPER'
#!/bin/sh
# Ming OS build-time apt wrapper: non-interactive, no pty, stdin from /dev/null.
# Usage: apt-build install [-y] [--no-install-recommends] pkg...
#        apt-build <any apt-get sub-command> [args...]
exec env \
    DEBIAN_FRONTEND=noninteractive \
    DEBCONF_NONINTERACTIVE_SEEN=true \
    APT_LISTCHANGES_FRONTEND=none \
    UCF_FORCE_CONFFOLD=1 \
    apt-get \
    -y \
    -o Dpkg::Use-Pty=0 \
    -o APT::Install-Recommends=false \
    -o Dpkg::Options::="--force-confold" \
    -o Dpkg::Options::="--force-confdef" \
    "$@" </dev/null
APT_BUILD_WRAPPER
    chmod 0755 "${CHROOT_DIR}/usr/local/sbin/apt-build"
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
        "08_settings_hub.sh"
        "07_finalize.sh"
    )
    for mod in "${modules[@]}"; do
        local mod_path="/tmp/ming-build/modules/${mod}"
        if [[ -f "${CHROOT_DIR}${mod_path}" ]]; then
            log_step "执行模块: ${mod}"
            chroot_exec bash "${mod_path}"
            settle_chroot_dpkg "${mod}"
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
    chroot_exec bash -c "rm -rf /tmp/ming-build"
    chroot_exec bash -c "rm -f /var/log/*.log /var/log/apt/*.log"
    chroot_exec bash -c "rm -f /var/cache/debconf/*-old"
    chroot_exec bash -c "> /etc/machine-id"
    log_info "chroot 清理完成"
}
# ======================== 生成 initramfs ========================
generate_initramfs() {
    log_step "生成 initramfs"
    chroot_exec bash -c '
        set -e
        shopt -s nullglob
        initrds=(/boot/initrd.img-*)
        if (( ${#initrds[@]} > 0 )); then
            update-initramfs -u -k all
        else
            kernel_dirs=(/lib/modules/*)
            (( ${#kernel_dirs[@]} > 0 )) || {
                echo "ERROR: no installed kernels are available for initramfs" >&2
                exit 1
            }
            for kernel_dir in "${kernel_dirs[@]}"; do
                update-initramfs -c -k "${kernel_dir##*/}"
            done
        fi
    '
    log_info "initramfs 生成完成"
}
# ======================== ISO 镜像打包 ========================
select_latest_kernel() {
    find "${CHROOT_DIR}/boot" -maxdepth 1 -type f -name 'vmlinuz-*' -printf '%f\n' \
        | sed 's/^vmlinuz-//' \
        | sort -V \
        | tail -n 1
}

validate_linux_kernel() {
    local kernel_path="$1"
    local label="$2"

    if [[ ! -s "${kernel_path}" ]]; then
        log_error "${label} is missing or empty: ${kernel_path}"
        return 1
    fi

    local file_info
    file_info=$(file -b "${kernel_path}" 2>/dev/null || true)
    if [[ "${file_info}" != *"Linux kernel"* ]]; then
        log_error "${label} is not a Linux kernel: ${file_info}"
        return 1
    fi

    local boot_sig setup_sig
    boot_sig=$(dd if="${kernel_path}" bs=1 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
    setup_sig=$(dd if="${kernel_path}" bs=1 skip=514 count=4 2>/dev/null)
    if [[ "${boot_sig}" == "0000" || "${setup_sig}" != "HdrS" ]]; then
        log_error "${label} failed bzImage signature check (boot=${boot_sig}, setup=${setup_sig})"
        return 1
    fi

    local sample_hex
    sample_hex=$(od -An -tx1 -N4096 "${kernel_path}" 2>/dev/null | tr -d ' \n0')
    if [[ -n "${sample_hex}" ]]; then
        log_info "${label} kernel validation passed: ${file_info}"
    else
        log_error "${label} appears to be all zero bytes"
        return 1
    fi
}

validate_iso_kernel() {
    local iso_path="$1"
    local expected_sha="$2"
    local tmp_dir extracted_sha

    tmp_dir="$(mktemp -d)"

    xorriso -osirrox on -indev "${iso_path}" -extract /live/vmlinuz "${tmp_dir}/vmlinuz" >/dev/null 2>&1
    validate_linux_kernel "${tmp_dir}/vmlinuz" "ISO /live/vmlinuz" || {
        rm -rf "${tmp_dir}"
        return 1
    }

    extracted_sha=$(sha256sum "${tmp_dir}/vmlinuz" | awk '{print $1}')
    if [[ "${extracted_sha}" != "${expected_sha}" ]]; then
        log_error "ISO kernel SHA256 mismatch"
        log_error "expected: ${expected_sha}"
        log_error "actual:   ${extracted_sha}"
        rm -rf "${tmp_dir}"
        return 1
    fi

    log_info "ISO /live/vmlinuz SHA256 matches source: ${extracted_sha}"
    rm -rf "${tmp_dir}"
}

validate_iso_boot_layout() {
    local iso_path="$1"
    local report files

    report=$(xorriso -indev "${iso_path}" -report_el_torito plain 2>/dev/null || true)
    if [[ "${report}" != *"El Torito"* ]]; then
        log_error "ISO El Torito boot catalog is missing or unreadable"
        return 1
    fi
    if [[ "${report}" != *"isolinux/isolinux.bin"* ]]; then
        log_error "ISO BIOS boot image is not isolinux/isolinux.bin"
        return 1
    fi
    if [[ "${report}" != *"boot/grub/efi.img"* ]]; then
        log_error "ISO UEFI boot image boot/grub/efi.img is missing"
        return 1
    fi

    # xorriso 1.5.x uses the default -find action to print paths and quotes
    # them.  It does not implement GNU find's -print action.
    files=$(xorriso -indev "${iso_path}" -find / -type f 2>/dev/null \
        | sed "s/^'//; s/'$//" || true)
    for required in \
        /live/vmlinuz \
        /live/initrd \
        /live/filesystem.squashfs \
        /isolinux/isolinux.bin \
        /isolinux/ldlinux.c32 \
        /isolinux/isolinux.cfg \
        /boot/grub/grub.cfg \
        /boot/grub/themes/ming/theme.txt \
        /boot/grub/fonts/unicode.pf2 \
        /EFI/BOOT/BOOTX64.EFI; do
        if ! grep -Fxq "${required}" <<< "${files}"; then
            log_error "ISO boot layout missing ${required}"
            return 1
        fi
    done
    log_info "ISO boot layout validation passed (BIOS isolinux + UEFI GRUB + live payload)"
}

validate_calamares_config() {
    log_info "Validating Calamares installer configuration..."
    python3 - "${CHROOT_DIR}" <<'PY'
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
errors = []

def load_yaml(relative_path):
    path = root / relative_path
    if not path.is_file():
        errors.append(f"missing {relative_path}")
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception as exc:
        errors.append(f"{relative_path} YAML parse failed: {exc}")
        return {}

settings = load_yaml("etc/calamares/settings.conf")
if settings.get("branding") != "ming":
    errors.append("settings.conf branding is not ming")
if settings.get("dont-chroot") is not False:
    errors.append(f"settings.conf dont-chroot must be boolean false, got {settings.get('dont-chroot')!r}")

exec_steps = []
show_steps = []
for phase in settings.get("sequence", []) or []:
    if isinstance(phase, dict) and "show" in phase:
        show_steps.extend(phase.get("show") or [])
    if isinstance(phase, dict) and "exec" in phase:
        exec_steps = phase.get("exec") or []
expected_steps = [
    "shellprocess@ming-ota-preflight", "ming-ota-target-guard@ming-ota-target-guard",
    "partition", "mount", "unpackfs", "machineid", "fstab", "networkcfg",
    "hwclock", "initramfs", "grubcfg", "shellprocess@ming-identity",
    "shellprocess@ming-installed-desktop-gate", "shellprocess@ming-bootloader",
    "umount",
]
for step in expected_steps:
    if step not in exec_steps:
        errors.append(f"settings.conf exec sequence missing {step}")
if all(step in exec_steps for step in ["shellprocess@ming-ota-preflight", "partition"]):
    if exec_steps.index("shellprocess@ming-ota-preflight") > exec_steps.index("partition"):
        errors.append("OTA backup verification must run before the destructive partition step")
if all(step in exec_steps for step in ["ming-ota-target-guard@ming-ota-target-guard", "partition"]):
    if exec_steps.index("ming-ota-target-guard@ming-ota-target-guard") > exec_steps.index("partition"):
        errors.append("OTA target disk guard must run before the destructive partition step")
if all(step in exec_steps for step in ["shellprocess@ming-identity", "shellprocess@ming-installed-desktop-gate", "shellprocess@ming-bootloader"]):
    if exec_steps.index("shellprocess@ming-identity") > exec_steps.index("shellprocess@ming-installed-desktop-gate"):
        errors.append("installed identity and root UUID must be finalized before desktop verification")
    if exec_steps.index("shellprocess@ming-installed-desktop-gate") > exec_steps.index("shellprocess@ming-bootloader"):
        errors.append("installed desktop verification must pass before GRUB installation")
blocked_show_steps = {"locale", "keyboard", "users"}
for step in blocked_show_steps.intersection(show_steps):
    errors.append(f"settings.conf visible sequence must not show {step}")
blocked_exec_steps = {"locale", "keyboard", "localecfg"}
for step in blocked_exec_steps.intersection(exec_steps):
    errors.append(f"settings.conf exec sequence must not run ViewModule {step}")
blocked_ming_steps = {"users", "displaymanager"}
for step in blocked_ming_steps.intersection(exec_steps):
    errors.append(f"settings.conf exec sequence must not run Calamares {step}; ming-identity handles it")
blocked_debian_steps = {
    "luksbootkeyfile", "dpkg-unsafe-io", "sources-media", "services-systemd",
    "bootloader-config", "packages", "plymouthcfg", "initramfscfg",
    "dpkg-unsafe-io-undo", "sources-media-unmount", "sources-final",
}
for step in blocked_debian_steps.intersection(exec_steps):
    errors.append(f"settings.conf still contains Debian installer step {step}")
if "bootloader" in exec_steps:
    errors.append("settings.conf must use Ming's diagnostic bootloader shellprocess instead of Calamares bootloader")

instances = settings.get("instances") or []
if not any(isinstance(item, dict) and item.get("id") == "ming-ota-preflight" for item in instances):
    errors.append("settings.conf missing ming-ota-preflight instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-ota-target-guard" for item in instances):
    errors.append("settings.conf missing ming-ota-target-guard instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-identity" for item in instances):
    errors.append("settings.conf missing ming-identity instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-installed-desktop-gate" for item in instances):
    errors.append("settings.conf missing ming-installed-desktop-gate instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-bootloader" for item in instances):
    errors.append("settings.conf missing ming-bootloader instance")

unpack = load_yaml("etc/calamares/modules/unpackfs.conf")
items = unpack.get("unpack") or []
if not items:
    errors.append("unpackfs.conf has no unpack entries")
else:
    item = items[0]
    if item.get("sourcefs") != "squashfs":
        errors.append("unpackfs.conf sourcefs must be squashfs")
    if item.get("destination") != "":
        errors.append("unpackfs.conf destination must be empty string for root target")
    if item.get("source") != "/run/ming-installer/filesystem.squashfs":
        errors.append(f"unpackfs.conf must use the stable Ming runtime source, got {item.get('source')!r}")

partition = load_yaml("etc/calamares/modules/partition.conf")
if partition.get("initialPartitioningChoice") != "none":
    errors.append("partition.conf must not force one-click erase; initialPartitioningChoice must be none")
if partition.get("allowManualPartitioning") is not True:
    errors.append("partition.conf must allow manual partitioning")

locale = load_yaml("etc/calamares/modules/locale.conf")
if locale.get("region") != "Asia" or locale.get("zone") != "Shanghai":
    errors.append("locale.conf does not default to Asia/Shanghai")
if locale.get("locale") != "zh_CN.UTF-8":
    errors.append("locale.conf does not default to zh_CN.UTF-8")
if locale.get("useSystemTimezone") is not True or locale.get("adjustLiveTimezone") is not True:
    errors.append("locale.conf must use the preflight-pinned Asia/Shanghai system timezone")

localecfg = load_yaml("etc/calamares/modules/localecfg.conf")
locale_conf = localecfg.get("localeConf") or {}
if locale_conf.get("LANG") != "zh_CN.UTF-8":
    errors.append("localecfg.conf must write zh_CN.UTF-8 LANG")

keyboard = load_yaml("etc/calamares/modules/keyboard.conf")
if keyboard.get("layout") != "us":
    errors.append("keyboard.conf must keep physical keyboard layout as us")

finished = load_yaml("etc/calamares/modules/finished.conf")
if finished.get("restartNowCommand") != "/usr/local/sbin/ming-finish-install-reboot":
    errors.append("finished.conf must reboot through ming-finish-install-reboot")

users = load_yaml("etc/calamares/modules/users.conf")
if users.get("allowWeakPasswords") is not True:
    errors.append("users.conf must allow weak passwords to avoid pwquality dictionary install blockers")
requirements = users.get("passwordRequirements") or {}
libpwquality = requirements.get("libpwquality") or []
libpwquality_text = "\n".join(str(item) for item in libpwquality)
if "dictcheck=0" not in libpwquality_text or "enforcing=0" not in libpwquality_text:
    errors.append("users.conf must disable libpwquality dictionary enforcement")

desktop_gate = load_yaml("etc/calamares/modules/ming-installed-desktop-gate.conf")
if desktop_gate.get("dontChroot") is not True:
    errors.append("installed desktop gate must inspect /target from the Live environment")
if "/usr/local/sbin/ming-installer-verify installed /target" not in (desktop_gate.get("script") or []):
    errors.append("installed desktop gate must validate the target before bootloader installation")

grub_install = root / "usr/sbin/grub-install"
if not grub_install.is_file():
    errors.append("live installer environment is missing /usr/sbin/grub-install; BIOS bootloader install will fail")

for relative_path in [
    "usr/local/sbin/ming-calamares-preflight",
    "usr/local/sbin/ming-installer-verify",
    "usr/local/sbin/ming-install-bootloader",
    "usr/local/sbin/ming-finish-install-reboot",
    "usr/local/bin/ming-calamares-launcher",
    "usr/local/bin/ming-live-installer.sh",
    "usr/local/bin/ming-installer-session",
]:
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"{relative_path} missing or empty")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "mklabel" in text or ("parted -s" in text and "mklabel" in text):
            errors.append(f"{relative_path} must not create partition tables before the Calamares partition page")
        if relative_path.endswith("ming-calamares-preflight") and "Asia/Shanghai" not in text:
            errors.append(f"{relative_path} missing Asia/Shanghai runtime enforcement")
        if relative_path.endswith("ming-calamares-preflight"):
            # 运行时会动态找到 squashfs 并创建 /run/ming-installer 软链接
            if "run/live/medium" not in text and "lib/live/mount" not in text and "find /run/live" not in text:
                errors.append(f"{relative_path} must search for live squashfs in standard live-boot paths")
            if "ln -s" not in text and "mount --bind" not in text:
                errors.append(f"{relative_path} must create a stable unpackfs source before Calamares starts")
            if "wipefs -n" not in text:
                errors.append(f"{relative_path} must log non-destructive disk signatures with wipefs -n")
        if relative_path.endswith("ming-install-bootloader"):
            if "--boot-directory=" not in text or "--target=i386-pc" not in text:
                errors.append(f"{relative_path} must install BIOS GRUB into the target boot directory")
            if "--target=x86_64-efi" not in text or "BOOTX64.EFI" not in text or "--removable" not in text:
                errors.append(f"{relative_path} must install a removable UEFI fallback bootloader")
            if "efibootmgr -n" not in text or "prefer_ming_uefi_boot" not in text:
                errors.append(f"{relative_path} must prefer the installed Ming UEFI boot entry")
            if "bootloader.log" not in text:
                errors.append(f"{relative_path} must write a diagnostic bootloader log")
            if "grub-script-check" not in text or "exit 22" not in text:
                errors.append(f"{relative_path} must reject a missing or invalid target grub.cfg")
        if relative_path.endswith("ming-finish-install-reboot"):
            if "systemctl -i reboot" not in text:
                errors.append(f"{relative_path} must request an inhibitor-safe reboot")
            if "eject " in text:
                errors.append(f"{relative_path} must not eject the mounted live medium before reboot")
            if "efibootmgr -n" not in text:
                errors.append(f"{relative_path} must prefer Ming OS for the next UEFI boot")
        if relative_path.endswith("ming-calamares-launcher"):
            if "ming-calamares-preflight" not in text or "calamares -d" not in text:
                errors.append(f"{relative_path} must run preflight before calamares")
            if "is_live_or_installer" not in text:
                errors.append(f"{relative_path} must refuse to run outside Live/installer sessions")
        if relative_path.endswith(("ming-live-installer.sh", "ming-installer-session")) and "ming-calamares-launcher" not in text:
            errors.append(f"{relative_path} must launch Calamares through ming-calamares-launcher")

for relative_path in [
    "usr/share/applications/calamares.desktop",
    "home/user/.config/autostart/calamares-live.desktop",
    "usr/share/xsessions/ming-installer.desktop",
]:
    path = root / relative_path
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "calamares" in text and "ming-calamares-launcher" not in text and "ming-installer-session" not in text:
            errors.append(f"{relative_path} can bypass Ming Calamares preflight")
        if relative_path.endswith("calamares-live.desktop") and "ming-live-installer.sh" not in text:
            errors.append(f"{relative_path} must keep Live-session guard through ming-live-installer.sh")

if errors:
    for error in errors:
        print(f"CALAMARES_CONFIG_ERROR: {error}", file=sys.stderr)
    sys.exit(1)
PY
    log_info "Calamares installer configuration validation passed"
}

validate_iso_grub_config() {
    local grub_cfg="${ISO_DIR}/boot/grub/grub.cfg"
    local grub_theme="${ISO_DIR}/boot/grub/themes/ming/theme.txt"
    if [[ ! -s "${grub_cfg}" ]]; then
        log_error "ISO GRUB config is missing: ${grub_cfg}"
        exit 1
    fi
    if [[ ! -s "${grub_theme}" ]]; then
        log_error "ISO GRUB theme is missing: ${grub_theme}"
        exit 1
    fi
    if ! cmp -s "${SCRIPT_DIR}/assets/grub-theme/theme.txt" "${grub_theme}"; then
        log_error "ISO GRUB theme differs from the source theme"
        exit 1
    fi
    for marker in 'title-text: "Ming OS"' 'desktop-color: "#07110f"' 'selected_item_color = "#43d19e"'; do
        if ! grep -Fq "${marker}" "${grub_theme}"; then
            log_error "ISO GRUB theme is missing marker: ${marker}"
            exit 1
        fi
    done
    local forbidden
    for forbidden in 'if [' 'echo ' 'sleep ' 'ming-installed' 'root=UUID='; do
        if grep -Fq "${forbidden}" "${grub_cfg}"; then
            log_error "ISO GRUB config contains old-BIOS fragile token: ${forbidden}"
            exit 1
        fi
    done
    if ! grep -Fq 'Ming OS' "${grub_cfg}"; then
        log_error "ISO GRUB must expose Ming OS installer entries"
        exit 1
    fi
    if ! grep -Fq 'ming.installer=1' "${grub_cfg}"; then
        log_error "ISO GRUB must boot the installer session"
        exit 1
    fi
    if ! grep -Fq 'nomodeset' "${grub_cfg}"; then
        log_error "ISO GRUB must keep a safe-graphics entry"
        exit 1
    fi
    if ! grep -Fq 'ming.safe_graphics=1' "${grub_cfg}"; then
        log_error "ISO GRUB safe-graphics entry must identify itself for installed-boot persistence"
        exit 1
    fi
    if ! grep -Fq 'terminal_input console' "${grub_cfg}"; then
        log_error "ISO GRUB must use console input for old firmware keyboard compatibility"
        exit 1
    fi
    for marker in 'Surface Pro' 'MacBook' 'acpi_osi=Darwin'; do
        if ! grep -Fq "${marker}" "${grub_cfg}"; then
            log_error "ISO GRUB missing priority hardware marker: ${marker}"
            exit 1
        fi
    done
    # The first installer entry is the default and must leave i915/KMS and
    # PCI power management untouched.  Only the explicitly labelled Safe
    # Graphics entry may carry nomodeset for emergency software rendering.
    local default_entry
    default_entry=$(awk '/^menuentry /{entry=$0; body=""; in_entry=1; next} in_entry{body=body $0 "\n"} in_entry && /^}/{if (entry !~ /Safe Graphics/ && entry !~ /安全显卡模式/ && body ~ /linux \/live\/vmlinuz/) {print body; exit}}' "${grub_cfg}")
    for forbidden in nomodeset i915.modeset=0 pcie_aspm=off pci=nomsi acpi_osi=Linux; do
        if grep -Eq "(^|[[:space:]])${forbidden}([[:space:]]|$)" <<< "${default_entry}"; then
            log_error "default installer GRUB entry must not force ${forbidden}"
            exit 1
        fi
    done
    if ! grep -Fq 'linux /live/vmlinuz' "${grub_cfg}" || ! grep -Fq 'initrd /live/initrd' "${grub_cfg}"; then
        log_error "ISO GRUB must directly load /live/vmlinuz and /live/initrd"
        exit 1
    fi
    log_info "ISO GRUB installer-menu validation passed"
}

validate_isolinux_fallback() {
    local iso_workdir="$1"
    local cfg="${iso_workdir}/isolinux/isolinux.cfg"
    for required in \
        "${iso_workdir}/isolinux/isolinux.bin" \
        "${iso_workdir}/isolinux/ldlinux.c32" \
        "${cfg}"; do
        if [[ ! -s "${required}" ]]; then
            log_error "isolinux BIOS/Rufus fallback is missing: ${required}"
            return 1
        fi
    done
    if grep -Fq 'chain.c32' "${cfg}" || grep -Fq 'COM32 chain' "${cfg}"; then
        log_error "isolinux fallback must boot Linux directly, not chain-load GRUB"
        return 1
    fi
    for marker in 'DEFAULT install' 'KERNEL /live/vmlinuz' 'INITRD /live/initrd' 'ming.installer=1' 'ming.safe_graphics=1' 'nomodeset'; do
        if ! grep -Fq "${marker}" "${cfg}"; then
            log_error "isolinux fallback missing marker: ${marker}"
            return 1
        fi
    done
    if grep -Fq 'UI menu.c32' "${cfg}" && [[ ! -s "${iso_workdir}/isolinux/menu.c32" ]]; then
        log_error "isolinux.cfg uses menu.c32 but menu.c32 was not copied"
        return 1
    fi
    log_info "isolinux BIOS/Rufus direct-boot fallback validation passed"
}

validate_required_desktop_runtime() {
    log_info "Validating required Ming desktop runtime..."

    if ! chroot_exec python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); from gi.repository import Gtk, Adw, Gio"; then
        log_error "GTK4/libadwaita/Gio typelibs are unavailable in the target system"
        return 1
    fi

    local command package
    for command in brightnessctl xdotool wmctrl pactl bluetoothctl upower pkexec lxpolkit notify-send xprop nm-online fc-match; do
        if ! chroot_exec /bin/sh -c "command -v '${command}' >/dev/null 2>&1"; then
            log_error "required desktop command is missing: ${command}"
            return 1
        fi
    done
    if ! chroot_exec /bin/sh -c '
        test -x /usr/local/sbin/ming-safe-graphics-persist &&
        test -s /etc/systemd/system/ming-safe-graphics-persist.service &&
        test -L /etc/systemd/system/multi-user.target.wants/ming-safe-graphics-persist.service
    '; then
        log_error "safe-graphics persistence runtime is incomplete in the target system"
        return 1
    fi
    if ! chroot_exec fc-match monospace 2>/dev/null \
        | grep -Eq 'Noto Sans Mono|Noto Mono|DejaVu Sans Mono|Liberation Mono'; then
        log_error "fontconfig monospace fallback does not resolve to a monospace family"
        return 1
    fi
    local cjk_font_match candidate_font_match
    cjk_font_match="$(chroot_exec fc-match 'sans:lang=zh' 2>/dev/null || true)"
    if ! grep -Eiq 'Noto Sans CJK SC|NotoSansCJK' <<< "${cjk_font_match}"; then
        log_error "fontconfig Chinese sans fallback does not resolve to Noto Sans CJK SC"
        return 1
    fi
    candidate_font_match="$(chroot_exec fc-match 'Noto Sans CJK SC' 2>/dev/null || true)"
    if ! grep -Eiq 'Noto Sans CJK SC|NotoSansCJK' <<< "${candidate_font_match}"; then
        log_error "fontconfig cannot resolve the Fcitx candidate family Noto Sans CJK SC"
        return 1
    fi
    if ! chroot_exec grep -Fxq "Font=Noto Sans CJK SC 15" \
        "/home/${MING_USER}/.config/fcitx5/conf/classicui.conf" \
        || ! chroot_exec grep -Fxq "MenuFont=Noto Sans CJK SC 16" \
        "/home/${MING_USER}/.config/fcitx5/conf/classicui.conf"; then
        log_error "Fcitx candidate UI is not configured with Noto Sans CJK SC"
        return 1
    fi
    if [[ ! -x "${CHROOT_DIR}/usr/sbin/rfkill" ]]; then
        log_error "required desktop command is missing: /usr/sbin/rfkill"
        return 1
    fi
    for package in \
        python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 \
        gvfs gvfs-backends brightnessctl xdotool wmctrl rfkill \
        pulseaudio pulseaudio-utils alsa-utils libasound2-plugins \
        pulseaudio-module-bluetooth pavucontrol bluez upower pkexec polkitd \
        lxpolkit libnotify-bin x11-utils desktop-file-utils fontconfig fonts-noto-core fonts-noto-cjk fonts-noto-mono \
        i965-va-driver intel-media-va-driver libgl1-mesa-dri mesa-va-drivers mesa-vdpau-drivers \
        mesa-vulkan-drivers mesa-utils lm-sensors firmware-amd-graphics amd64-microcode vainfo \
        fcitx5-rime librime-data rime-data-luna-pinyin; do
        if ! chroot_exec dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -qx 'ii '; then
            log_error "required desktop runtime package is not installed: ${package}"
            return 1
        fi
    done

    # Debian Trixie ships the Xorg modesetting DDX from xserver-xorg-core;
    # older releases exposed it as a separate xserver-xorg-video-modesetting
    # package.  Gate the capability, accepting either packaging layout.
    if ! chroot_exec test -s "/usr/lib/xorg/modules/drivers/modesetting_drv.so" \
        && ! chroot_exec dpkg-query -W -f='${db:Status-Abbrev}' \
            xserver-xorg-video-modesetting 2>/dev/null | grep -qx 'ii '; then
        log_error "required Xorg modesetting driver is missing (modesetting_drv.so or xserver-xorg-video-modesetting)"
        return 1
    fi

    if ! chroot_exec getent group render >/dev/null 2>&1; then
        log_error "required render group is missing from the target system"
        return 1
    fi
    if ! chroot_exec /bin/sh -c "id -nG '${MING_USER}' | tr ' ' '\\n' | grep -qx render"; then
        log_error "desktop user ${MING_USER} is not a member of the render group"
        return 1
    fi

    for package in \
        wireless-regdb bluez-firmware firmware-mediatek firmware-libertas \
        firmware-misc-nonfree firmware-iwlwifi firmware-realtek firmware-atheros \
        firmware-brcm80211; do
        if ! chroot_exec dpkg-query -W -f='${Status}' "${package}" 2>/dev/null \
            | grep -qx 'install ok installed'; then
            log_error "required radio firmware package is not installed: ${package}"
            return 1
        fi
    done

    if ! chroot_exec python3 -c "import runpy; runpy.run_path('/usr/local/bin/ming-settings', run_name='ming_runtime_check')"; then
        log_error "Ming Settings runtime import check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/bin/ming-files --check-runtime; then
        log_error "Ming Files runtime check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/bin/ming-device-control status --json \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); required={"audio","brightness","wifi","bluetooth","battery"}; raise SystemExit(0 if required <= set(value) else 1)'; then
        log_error "Ming device control runtime check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/bin/ming-window-control status --json \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if isinstance(value.get("healthy"), bool) and isinstance(value.get("xfwm"), dict) else 1)'; then
        log_error "Ming window-control JSON runtime check failed"
        return 1
    fi
    # The health helper intentionally returns non-zero when no graphical
    # session exists (which is the normal build-chroot state).  Validate its
    # JSON contract without treating that diagnostic status as malformed data.
    if ! (chroot_exec /usr/local/bin/ming-desktop-healthcheck --json || true) \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); window=value.get("window_manager"); raise SystemExit(0 if isinstance(window, dict) and isinstance(window.get("healthy"), bool) else 1)'; then
        log_error "Ming desktop-healthcheck JSON runtime check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/sbin/ming-time-sync status --json \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("state") in {"synchronized", "waiting", "error"} else 1)'; then
        log_error "Ming time-sync JSON runtime check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/sbin/ming-performance-status status --json \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); required={"schema_version","ok","boot","memory","cpu","storage","temperatures","services"}; raise SystemExit(0 if value.get("schema_version") == 1 and value.get("ok") is True and required <= set(value) else 1)'; then
        log_error "Ming performance-status JSON runtime check failed"
        return 1
    fi
    if ! chroot_exec /usr/local/sbin/ming-service-profile status --json \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); required={"schema_version","modem","serial_getty","optional_services"}; raise SystemExit(0 if value.get("schema_version") == 1 and required <= set(value) else 1)'; then
        log_error "Ming service-profile JSON runtime check failed"
        return 1
    fi
    # No graphical session is expected while the rootfs gate runs.  The helper
    # reports that condition as JSON with exit code 2, which is valid as long
    # as its diagnostic schema remains intact.  Do not let pipefail turn that
    # expected diagnostic into a false build failure, while still rejecting
    # every other helper failure and malformed JSON.
    local display_status display_status_rc
    if display_status="$(chroot_exec /usr/local/bin/ming-display-control status --json)"; then
        display_status_rc=0
    else
        display_status_rc=$?
    fi
    if [[ "${display_status_rc}" -ne 0 && "${display_status_rc}" -ne 2 ]]; then
        log_error "Ming display-control runtime check exited unexpectedly: ${display_status_rc}"
        return 1
    fi
    if ! printf '%s\n' "${display_status}" | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if isinstance(value.get("outputs"), list) and value.get("confirm_seconds") == 15 else 1)'; then
        log_error "Ming display-control JSON runtime check failed"
        return 1
    fi

    if ! python3 - "${CHROOT_DIR}" <<'PY'
# MING_DESKTOP_BACKEND_VALIDATOR_BEGIN
from pathlib import Path
import configparser
import io
import os
import shlex
import shutil
import sys

root = Path(sys.argv[1])
desktop_names = [
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-terminal.desktop",
    "ming-edge.desktop",
    "spark-store.desktop",
]
search_path = ":".join(str(root / item) for item in (
    "usr/local/bin", "usr/bin", "bin", "usr/local/sbin", "usr/sbin", "sbin"
))
errors = []
desktop_commands = {}
for name in desktop_names:
    path = root / "usr/share/applications" / name
    if not path.is_file():
        errors.append(f"missing core desktop entry: {name}")
        continue
    exec_line = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Exec="):
            exec_line = line[5:].strip()
            break
    if not exec_line:
        errors.append(f"core desktop entry has no Exec target: {name}")
        continue
    try:
        command = shlex.split(exec_line)[0]
    except (ValueError, IndexError) as error:
        errors.append(f"invalid Exec target in {name}: {error}")
        continue
    desktop_commands[name] = command
    if command.startswith("/"):
        target = root / command.lstrip("/")
        found = target.is_file() and target.stat().st_size > 0 and os.access(target, os.X_OK)
    else:
        found = shutil.which(command, path=search_path) is not None
    if not found:
        errors.append(f"unresolved Exec target in {name}: {command}")

edge_backends = [
    root / "usr/bin/microsoft-edge-stable",
    root / "usr/bin/microsoft-edge",
    root / "opt/microsoft/msedge/microsoft-edge",
]
if not any(path.is_file() and os.access(path, os.X_OK) for path in edge_backends):
    errors.append("missing Microsoft Edge browser backend behind ming-edge wrapper")

spark_backends = [
    root / "usr/bin/spark-store",
    root / "opt/spark-store/bin/spark-store",
]
if not any(path.is_file() and os.access(path, os.X_OK) for path in spark_backends):
    spark_wrapper = root / "usr/local/bin/ming-spark-store"
    spark_installer = root / "usr/local/bin/ming-install-spark-store"
    wrapper_text = spark_wrapper.read_text(encoding="utf-8", errors="replace") if spark_wrapper.is_file() else ""
    has_repair_fallback = (
        desktop_commands.get("spark-store.desktop") == "/usr/local/bin/ming-spark-store"
        and spark_installer.is_file()
        and os.access(spark_installer, os.X_OK)
        and 'exec pkexec /usr/local/bin/ming-install-spark-store "$@"' in wrapper_text
    )
    if not has_repair_fallback:
        errors.append("Spark Store repair fallback is missing or not executable")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
# MING_DESKTOP_BACKEND_VALIDATOR_END
PY
    then
        log_error "core desktop launcher validation failed"
        return 1
    fi

    log_info "Required Ming desktop runtime validation passed"
}

validate_r4_compatibility() {
    log_info "Validating Ming OS r4 legacy hardware and Settings Hub integration..."
    validate_required_desktop_runtime || return 1
    python3 - "${CHROOT_DIR}" <<'PY'
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
errors = []
# Required runtime helpers are deliberately listed before the nested helper
# functions so the static validator and the rootfs gate share one visible
# contract even when a lightweight source scanner stops at the first closure.
# usr/local/bin/ming-audio-session
# usr/local/sbin/ming-package-installer

def require_file(relative_path, marker=None):
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"missing or empty {relative_path}")
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if marker and marker not in text:
        errors.append(f"{relative_path} missing marker {marker!r}")
    return text

def require_path(relative_path):
    path = root / relative_path
    if not path.exists() or (path.is_file() and path.stat().st_size == 0):
        errors.append(f"missing or empty {relative_path}")

def require_absent(relative_path, reason):
    path = root / relative_path
    if path.exists():
        errors.append(f"{relative_path} must not be preinstalled: {reason}")

def validate_generated_executable(relative_path, language):
    """Reject a missing, non-executable, or syntactically invalid shipped helper."""
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"missing or empty generated helper {relative_path}")
        return
    if not os.access(path, os.X_OK):
        errors.append(f"{relative_path} must be executable")
        return
    if language == "bash":
        parsed = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    elif language == "python":
        with tempfile.TemporaryDirectory(prefix="ming-rootfs-pycache-") as pycache:
            environment = os.environ.copy()
            environment["PYTHONPYCACHEPREFIX"] = pycache
            parsed = subprocess.run(
                [sys.executable, "-m", "py_compile", str(path)],
                capture_output=True,
                text=True,
                env=environment,
            )
    else:
        errors.append(f"unknown generated helper language for {relative_path}: {language}")
        return
    if parsed.returncode != 0:
        detail = (parsed.stderr or parsed.stdout).strip()
        errors.append(f"{relative_path} failed {language} syntax validation: {detail}")

def validate_systemd_unit(relative_path):
    """Perform a small structural gate before systemd-analyze verifies the unit."""
    text = require_file(relative_path)
    if not text:
        return
    if "[Timer]" in text:
        if "[Unit]" not in text or "[Timer]" not in text:
            errors.append(f"{relative_path} is not a complete systemd timer unit")
        if not re.search(r"^(OnBootSec|OnCalendar|OnUnitActiveSec)=.+$", text, flags=re.MULTILINE):
            errors.append(f"{relative_path} has no timer schedule directive")
        return
    if "[Unit]" not in text or "[Service]" not in text:
        errors.append(f"{relative_path} is not a complete systemd service unit")
    if not re.search(r"^ExecStart=.+$", text, flags=re.MULTILINE):
        errors.append(f"{relative_path} has no ExecStart directive")

fstab = require_file("etc/fstab")
tmpfs_tmp_entries = [
    line for line in fstab.splitlines()
    if line.strip() and not line.lstrip().startswith("#")
    and len(line.split()) >= 3
    and line.split()[1] == "/tmp" and line.split()[2] == "tmpfs"
]
if len(tmpfs_tmp_entries) != 1:
    errors.append("Live fstab must contain exactly one /tmp tmpfs entry")

settings = require_file("usr/local/bin/ming-settings", "硬件与诊断")
for marker in [
    "ming-network-repair",
    "ming-driver-diagnose",
    "ming-diagnostic-bundle",
    "ming-surface-support",
    "ming-classic-mode",
    "system-config-printer",
]:
    if marker not in settings:
        errors.append(f"ming-settings does not expose {marker}")

security_control = require_file("usr/local/sbin/ming-security-control", "apply_firewall_atomic")
for marker in ["status", "quick-check", "firewall", "profile", "security-updates"]:
    if marker not in security_control:
        errors.append(f"ming-security-control missing interface marker {marker}")
account_control = require_file("usr/local/sbin/ming-account-control", "set-password")
for marker in ["status", "clear-password", "passwd", "chpasswd"]:
    if marker not in account_control:
        errors.append(f"ming-account-control missing interface marker {marker}")
require_file("etc/nftables.conf", "table inet ming_filter")
require_file("usr/share/polkit-1/actions/org.ming.security.control.policy", "/usr/local/sbin/ming-security-control")
require_file("usr/share/polkit-1/actions/org.ming.account.control.policy", "/usr/local/sbin/ming-account-control")
require_file("usr/local/bin/ming-connection-notify", "NotificationDeduplicator")
require_file("home/user/.config/autostart/ming-connection-notify.desktop", "X-GNOME-Autostart-enabled=true")
for helper in ["usr/local/sbin/ming-security-control", "usr/local/sbin/ming-account-control",
               "usr/local/bin/ming-connection-notify"]:
    validate_generated_executable(helper, "python")

settings_desktop = require_file("usr/share/applications/ming-settings.desktop", "Exec=/usr/local/bin/ming-control-center")
if "Exec=/usr/local/bin/ming-settings" in settings_desktop:
    errors.append("ming-settings.desktop must use the stable ming-control-center launcher")

desktop_organizer = require_file("usr/local/bin/ming-desktop-organizer", "sync_apps")
if "ming-phone-desktop --sync" not in desktop_organizer:
    errors.append("desktop organizer must synchronize the phone-style desktop")
if "Ming 设置.desktop" not in desktop_organizer:
    errors.append("desktop organizer must keep the Ming Settings launcher")
for retired in ["cat > \"${desktop}/Ming 应用库.desktop\"", "cat > \"${desktop}/所有磁盘.desktop\""]:
    if retired in desktop_organizer:
        errors.append(f"desktop organizer still generates retired launcher {retired}")

phone_desktop = require_file("usr/local/bin/ming-phone-desktop", "InteractionState")
for marker in [
    "Gdk.EventMask.TOUCH_MASK",
    "begin_touch(self, tile, event)",
    "label.set_size_request(LABEL_W, LABEL_H)",
    "Pango.EllipsizeMode.END",
    "无法打开此应用",
    "class LaunchFeedbackOverlay",
    "LAUNCH_FEEDBACK_TIMEOUT_MS = 4000",
    "class StatusWidget",
    "dispatch_activation",
    'self.fixed.connect("button-release-event", self.on_fixed_button_release)',
]:
    if marker not in phone_desktop:
        errors.append(f"ming-phone-desktop missing bounded input marker {marker}")

plank_watchdog = require_file("usr/local/bin/ming-plank-watchdog", "plank_window_visible")
for marker in ["start_plank()", "stop_legacy_dock()", "while true; do", "ming-plank-watchdog.lock", "nohup plank"]:
    if marker not in plank_watchdog:
        errors.append(f"ming-plank-watchdog missing primary Dock marker {marker}")
phone_watchdog = require_file("usr/local/bin/ming-phone-desktop-watchdog", "starting ming-phone-desktop")
for marker in ["ming_log_dir()", "start_xfdesktop_fallback()", "ming-phone-desktop did not stay running", "stop_xfdesktop", "wait_phone_desktop_ready()", "ming-phone-desktop.ready"]:
    if marker not in phone_watchdog:
        errors.append(f"ming-phone-desktop-watchdog missing black-screen guard marker {marker}")
if "if wait_phone_desktop_ready" not in phone_watchdog:
    errors.append("ming-phone-desktop-watchdog must wait for Ming desktop readiness before stopping xfdesktop")
if 'if wait_phone_desktop_ready "${log_file}"; then\n            stop_xfdesktop' not in phone_watchdog:
    errors.append("ming-phone-desktop-watchdog must stop xfdesktop only after Ming desktop is running")
legacy_shell_autostarts = {
    "home/user/.config/autostart/ming-dock.desktop": "Dock",
    "home/user/.config/autostart/ming-phone-desktop.desktop": "phone desktop",
    "home/user/.config/autostart/picom.desktop": "Picom",
}
for path, component in legacy_shell_autostarts.items():
    entry = require_file(path, "X-Ming-Managed-By=ming-session-healthcheck")
    for marker in ("Exec=/usr/bin/true", "Hidden=true", "X-GNOME-Autostart-enabled=false"):
        if marker not in entry:
            errors.append(f"legacy shell lifecycle autostart must stay disabled for {component}: {path}")

session_health_autostart = require_file(
    "home/user/.config/autostart/ming-session-healthcheck.desktop",
    "Exec=/usr/local/bin/ming-session-healthcheck --session",
)
for marker in (
    "Hidden=false",
    "X-GNOME-Autostart-enabled=true",
    "X-Ming-Managed-Components=phone-desktop;plank;picom",
):
    if marker not in session_health_autostart:
        errors.append(f"ming-session-healthcheck autostart missing {marker}")

plank_settings = require_file("home/user/.config/plank/dock1/settings", "DockItems=ming-settings.dockitem")
dpkg_status = require_file("var/lib/dpkg/status", "Package: bamfdaemon")
if "Package: bamfdaemon\n" not in dpkg_status:
    errors.append("Dock runtime dependency is not installed: bamfdaemon")
if not any(f"Package: {package}\n" in dpkg_status for package in ("libbamf3-2t64", "libbamf3-2")):
    errors.append("Dock runtime dependency is not installed: libbamf3-2t64 (Trixie) or libbamf3-2")
for marker in ["IconSize=40", "ZoomEnabled=true", "ZoomPercent=148", "HideMode=0", "Theme=Ming"]:
    if marker not in plank_settings:
        errors.append(f"Plank settings missing {marker}")
if plank_settings.count("ming-app-library.dockitem") != 1:
    errors.append("Plank settings must contain exactly one application drawer item")
if "ming-disk-hub.dockitem" in plank_settings:
    errors.append("Plank settings must not include the retired All Disks item")
if "ming-edge.dockitem" not in plank_settings:
    errors.append("Plank settings must include ming-edge.dockitem as the default browser")
if "firefox-esr.dockitem" in plank_settings or "firefox.dockitem" in plank_settings:
    errors.append("Plank settings must not include Firefox dock items")
for forbidden_dock in ["wechat.dockitem", "wps-office.dockitem"]:
    if forbidden_dock in plank_settings:
        errors.append(f"Plank settings must not include retired dock item {forbidden_dock}")

plank_theme = require_file("usr/share/plank/themes/Ming/dock.theme", "IndicatorSize=4")
for marker in ["UrgentBounceTime=600", "LaunchBounceTime=520", "ItemMoveTime=260"]:
    if marker not in plank_theme:
        errors.append(f"Plank theme missing animation marker {marker}")

for path, marker in [
    ("usr/local/lib/ming-os/ming-shell-common.py", "DesktopEntry"),
    ("usr/local/bin/ming-app-drawer", "drawer_geometry"),
    ("usr/local/bin/ming-launch", "LaunchRequest"),
    ("usr/local/bin/ming-notifications", "parse_notification_log"),
    ("usr/local/bin/ming-device-control", "DeviceController"),
    ("usr/local/bin/ming-audio-session", "audio-repair-playback"),
    ("usr/local/sbin/ming-package-installer", "PackageInstaller"),
    ("usr/local/bin/ming-hardware-status", "HardwareStatus"),
    ("usr/local/bin/ming-files", "ming-files.py"),
    ("usr/local/lib/ming-os/ming-files.py", "class MingFiles"),
    ("usr/local/lib/ming-os/ming-files-model.py", "LocationModel"),
    ("usr/local/lib/ming-os/ming-settings-backend", "SettingsBackend"),
    ("usr/local/sbin/ming-ota-backup", "doctor"),
]:
    require_file(path, marker)

ota_backup = require_file("usr/local/sbin/ming-ota-backup", "--system-target")
for marker in ["sha256", "readlink", "headroom", "verify_command"]:
    if marker not in ota_backup:
        errors.append(f"OTA backup engine missing verification marker {marker}")

for retired_path in [
    "usr/local/bin/ming-disk-hub",
    "usr/share/applications/ming-disk-hub.desktop",
    "home/user/Desktop/Ming 应用库.desktop",
    "home/user/Desktop/所有磁盘.desktop",
    "home/user/Desktop/ming-app-library.desktop",
    "home/user/Desktop/ming-disk-hub.desktop",
]:
    require_absent(retired_path, "retired Ming shell surface")

drawer_desktop = require_file("usr/share/applications/ming-app-library.desktop", "Exec=/usr/local/bin/ming-app-drawer")
if "NoDisplay=true" not in drawer_desktop:
    errors.append("application drawer desktop entry must stay hidden outside the Dock")

update_gui = require_file("usr/local/bin/ming-update-gui", "Ming OS 更新管理器")
if "Ming OS Update Manager" in update_gui or "Check updates" in update_gui or "System Update" in update_gui:
    errors.append("ming-update-gui must keep user-facing update UI in Chinese")

require_path("usr/share/backgrounds/ming-os/default.png")
require_path("usr/share/backgrounds/ming-os/default-2633.png")
require_path("usr/share/backgrounds/ming-os/default-light.png")
require_path("usr/share/backgrounds/ming-os/default-dark.png")

# The generated 4K source is kept for high-density displays, while the
# desktop must have real pre-scaled caches for common older hardware.  Check
# PNG IHDR dimensions here so a failed ImageMagick conversion cannot silently
# leave three copies of the 4K file in the rootfs.
def require_png_dimensions(relative_path, expected_width, expected_height):
    path = root / relative_path
    if not path.is_file() or path.stat().st_size < 24:
        errors.append(f"missing or empty wallpaper cache {relative_path}")
        return
    try:
        header = path.read_bytes()[:24]
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise ValueError("not a PNG")
        import struct
        width, height = struct.unpack(">II", header[16:24])
        if (width, height) != (expected_width, expected_height):
            errors.append(
                f"{relative_path} has dimensions {width}x{height}, expected {expected_width}x{expected_height}"
            )
    except (OSError, ValueError, struct.error) as exc:
        errors.append(f"invalid wallpaper cache {relative_path}: {exc}")

for wallpaper_path, wallpaper_width, wallpaper_height in [
    ("usr/share/backgrounds/ming-os/default-1366x768.png", 1366, 768),
    ("usr/share/backgrounds/ming-os/default-1920x1080.png", 1920, 1080),
    ("usr/share/backgrounds/ming-os/default-3840x2160.png", 3840, 2160),
]:
    require_png_dimensions(wallpaper_path, wallpaper_width, wallpaper_height)
appearance = require_file("usr/local/bin/ming-apply-appearance", "ming-appearance-control reapply")
for marker in ["ming-appearance-control reapply", "timeout --foreground 8s", "appearance.log"]:
    if marker not in appearance:
        errors.append(f"ming-apply-appearance missing bounded preference marker {marker}")
for forbidden in ["xfce4-panel --quit", "ming-phone-desktop-watchdog", "ming-plank-watchdog", "-s 2"]:
    if forbidden in appearance:
        errors.append(f"ming-apply-appearance must not coordinate shell components with {forbidden}")

cache_dir = root / "home/user/.cache/ming-os"
if not cache_dir.exists():
    errors.append("home/user/.cache/ming-os must exist so watchdog logs are writable")
else:
    st = cache_dir.stat()
    if st.st_uid != 1000 or st.st_gid != 1000:
        errors.append(f"home/user/.cache/ming-os must be owned by uid/gid 1000, got {st.st_uid}/{st.st_gid}")

for helper in [
    "usr/local/bin/ming-network-repair",
    "usr/local/bin/ming-driver-diagnose",
    "usr/local/bin/ming-diagnostic-bundle",
    "usr/local/bin/ming-surface-support",
    "usr/local/bin/ming-classic-mode",
    "usr/local/bin/ming-lock",
    "usr/local/bin/ming-picom",
    "usr/local/bin/ming-plank-watchdog",
    "usr/local/bin/ming-desktop-healthcheck",
    "usr/local/bin/ming-window-control",
    "usr/local/bin/ming-window-manager-watchdog",
    "usr/local/bin/ming-input-healthcheck",
    "usr/local/bin/ming-phone-desktop-watchdog",
    "usr/local/bin/ming-edge",
    "usr/local/bin/ming-spark-store",
    "usr/local/bin/ming-audio-session",
    "usr/local/sbin/ming-package-installer",
]:
    require_file(helper)

legacy_scale = require_file("usr/local/bin/ming-scale", "未更改现有外观设置")
for forbidden_scale_write in ["xfconf-query", "dconf write", "IconSize=", "sed -i"]:
    if forbidden_scale_write in legacy_scale:
        errors.append(f"retired ming-scale must not mutate appearance settings: {forbidden_scale_write}")

service_profile = require_file("usr/local/sbin/ming-service-profile", "status --json")
for marker in [
    "MING_KEEP_MODEMMANAGER", "/dev/cdc-wdm", "timeout --foreground 2s nmcli",
    "timeout --foreground 2s lspci", "timeout --foreground 2s lsusb",
    "systemctl enable --now ModemManager.service",
    "systemctl disable --now ModemManager.service",
    "systemctl enable cups.socket", "cups-browsed.service", "avahi-daemon.service",
    "saned.service saned.socket", "serial-getty@ttyS0.service", "pgrep",
]:
    if marker not in service_profile:
        errors.append(f"ming-service-profile missing service-policy marker {marker}")
power_profile = require_file("usr/local/sbin/ming-power-profile", "has_battery")
for marker in [
    "has_battery", "chassis_type", "laptop-detect", "portable", "tlp.service", "thermald.service", "power-profiles-daemon.service",
    "systemctl enable --now tlp.service", "systemctl disable --now tlp.service",
]:
    if marker not in power_profile:
        errors.append(f"ming-power-profile missing power-policy marker {marker}")
sysctl_helper = require_file("usr/local/sbin/ming-sysctl-apply", "/proc/sys/")
for marker in ["unsupported sysctl", "sysctl -q"]:
    if marker not in sysctl_helper:
        errors.append(f"ming-sysctl-apply missing safe-key marker {marker}")
for relative_path in [
    "usr/local/sbin/ming-service-profile",
    "usr/local/sbin/ming-power-profile",
    "usr/local/sbin/ming-sysctl-apply",
]:
    validate_generated_executable(relative_path, "bash")
for relative_path in [
    "etc/systemd/system/ming-service-profile.service",
    "etc/systemd/system/ming-power-profile.service",
    "etc/systemd/system/ming-appstore-ready.timer",
]:
    validate_systemd_unit(relative_path)
for relative_path, marker in [
    ("etc/systemd/system/ming-rfkill.service", "After=NetworkManager.service"),
    ("etc/systemd/system/ming-device-tune.service", "After=local-fs.target"),
]:
    unit = require_file(relative_path, marker)
    if relative_path.endswith("ming-rfkill.service") and "After=multi-user.target" in unit:
        errors.append("ming-rfkill.service must not order after multi-user.target")
    if relative_path.endswith("ming-device-tune.service") and "systemd-udev-settle.service" in unit:
        errors.append("ming-device-tune.service must not wait for udev settle")
if "options iwlwifi power_save=0" in (root / "etc/modprobe.d/ming-old-hardware.conf").read_text(encoding="utf-8", errors="replace") if (root / "etc/modprobe.d/ming-old-hardware.conf").is_file() else False:
    errors.append("iwlwifi power_save=0 must not be forced globally")
if "kernel.sched_latency_ns" in (root / "etc/sysctl.d/99-ming-performance.conf").read_text(encoding="utf-8", errors="replace") if (root / "etc/sysctl.d/99-ming-performance.conf").is_file() else False:
    errors.append("legacy kernel.sched_* sysctls must not be shipped")
tlp_conf = require_file("etc/tlp.d/ming-laptop.conf", "USB_AUTOSUSPEND=0")
if "USB_BLACKLIST_BTUSB=1" in tlp_conf:
    errors.append("TLP must not blacklist btusb")
for marker in [
    "USB_EXCLUDE_BTUSB=1", "USB_EXCLUDE_AUDIO=1", "USB_EXCLUDE_WWAN=1",
    "USB_AUTOSUSPEND=0",
]:
    if marker not in tlp_conf:
        errors.append(f"TLP USB safety exclusion missing {marker}")
storage_unit_path = root / "etc/systemd/system/ming-storage.service"
if storage_unit_path.is_file():
    storage_unit = storage_unit_path.read_text(encoding="utf-8", errors="replace")
    if "Before=lightdm.service display-manager.service" in storage_unit:
        errors.append("ming-storage.service must not gate graphical boot")
    if "WantedBy=multi-user.target" in storage_unit:
        errors.append("ming-storage.service must remain on-demand")
if (root / "etc/systemd/system/multi-user.target.wants/ming-storage.service").exists():
    errors.append("ming-storage.service must not be enabled by default")
if (root / "etc/udev/rules.d/99-ming-storage.rules").exists():
    errors.append("automatic storage udev trigger must not be shipped")

time_sync = require_file("usr/local/sbin/ming-time-sync", "status --json")
for marker in [
    "flock", "nm-online -q -t 12", "timedatectl set-ntp true",
    "systemctl restart systemd-timesyncd", "NTPSynchronized", "SECONDS + 45",
    "/var/log/ming-time-sync.log",
]:
    if marker not in time_sync:
        errors.append(f"ming-time-sync missing bounded-sync marker {marker}")
time_dispatcher = require_file(
    "etc/NetworkManager/dispatcher.d/90-ming-time-sync", "connectivity-change")
for marker in ["up|dhcp4-change|dhcp6-change|connectivity-change", "nohup", "&"]:
    if marker not in time_dispatcher:
        errors.append(f"time-sync dispatcher missing event marker {marker}")
performance_status = require_file("usr/local/sbin/ming-performance-status", "status --json")
for marker in [
    "systemd-analyze", "/proc/meminfo", "scaling_governor",
    "discard_max_bytes", "fstrim.timer", "sensors", "ModemManager",
    "bluetooth.service", "pgrep", "vainfo", "probe_timeout_seconds",
]:
    if marker not in performance_status:
        errors.append(f"ming-performance-status missing diagnostic marker {marker}")
for relative_path in [
    "usr/local/sbin/ming-time-sync",
    "etc/NetworkManager/dispatcher.d/90-ming-time-sync",
    "usr/local/sbin/ming-intel-xorg-setup",
    "usr/local/bin/ming-window-control",
    "usr/local/bin/ming-desktop-healthcheck",
    "usr/local/bin/ming-plank-watchdog",
    "usr/local/bin/ming-window-manager-watchdog",
]:
    validate_generated_executable(relative_path, "bash")
for relative_path in [
    "usr/local/bin/ming-display-control",
    "usr/local/bin/ming-hardware-status",
    "usr/local/sbin/ming-performance-status",
    "usr/local/bin/ming-phone-desktop",
    "usr/local/bin/ming-settings",
    "usr/local/bin/ming-audio-session",
    "usr/local/sbin/ming-package-installer",
]:
    validate_generated_executable(relative_path, "python")
for relative_path in [
    "etc/systemd/system/ming-intel-xorg-migration.service",
    "etc/systemd/system/ming-regdom.service",
    "etc/systemd/system/ming-hardware-preload.service",
]:
    validate_systemd_unit(relative_path)
if (root / "etc/systemd/system/NetworkManager-wait-online.service.d").exists():
    errors.append("NetworkManager-wait-online drop-ins must not gate graphical boot")

display_control = require_file("usr/local/bin/ming-display-control", "parse_xrandr_snapshot")
for marker in ["status", "apply", "confirm", "rollback", "CONFIRM_SECONDS = 15", "request_is_supported"]:
    if marker not in display_control:
        errors.append(f"ming-display-control missing confirmed-display marker {marker}")
display_control_path = root / "usr/local/bin/ming-display-control"
if display_control_path.is_file() and not (display_control_path.stat().st_mode & 0o111):
    errors.append("ming-display-control must be executable")
xfce_display_wrapper = require_file(
    "usr/bin/xfce4-display-settings", "Ming OS display settings compatibility launcher")
for marker in ["ming-control-center --page display", "xfce4-display-settings.real"]:
    if marker not in xfce_display_wrapper:
        errors.append(f"xfce4-display-settings compatibility wrapper missing {marker}")
for marker in ["ming-display-control", "100% 标准", "1920 × 1080", "保留此显示设置"]:
    if marker not in settings:
        errors.append(f"ming-settings missing display control marker {marker}")
for marker in [
    "status-widget.json", "widget_state_path", "save_widget_state", "os.replace",
    "Gtk.Revealer", "collapsed", "收起", "展开",
]:
    if marker not in phone_desktop:
        errors.append(f"ming-phone-desktop missing compact widget marker {marker}")

window_control = require_file("usr/local/bin/ming-window-control", "_NET_SUPPORTING_WM_CHECK")
for marker in ["status --json", "xfwm4 --replace", "_NET_CLOSE_WINDOW", "x11_call()", "x11_id_is_valid()", "timeout --foreground 2s", "json.dumps"]:
    if marker not in window_control:
        errors.append(f"ming-window-control missing window recovery marker {marker}")
window_health = require_file("usr/local/bin/ming-desktop-healthcheck", "window_manager")
for marker in ["x11_call()", "x11_id_is_valid()", "timeout --foreground 2s", "json.dumps"]:
    if marker not in window_health:
        errors.append(f"ming-desktop-healthcheck missing safe X11/JSON marker {marker}")
plank_watchdog = require_file("usr/local/bin/ming-plank-watchdog", "plank_window_visible")
for marker in ["x11_call()", "valid_window_id()", "timeout --foreground 2s"]:
    if marker not in plank_watchdog:
        errors.append(f"ming-plank-watchdog missing bounded X11 marker {marker}")
window_watchdog = require_file("usr/local/bin/ming-window-manager-watchdog", "failure_count >= 3")
for marker in ["sleep 10", "ming-window-control repair", "window-manager.log"]:
    if marker not in window_watchdog:
        errors.append(f"ming-window-manager-watchdog missing health marker {marker}")
require_file("home/user/.config/autostart/ming-window-manager.desktop", "ming-window-manager-watchdog --session")

picom_wrapper = require_file("usr/local/bin/ming-picom", "/tmp/ming-picom.log")
for marker in ["low-memory", "safe-graphics-cmdline", "software-renderer", "virtual-machine-gpu", "no-dri", "old-intel-gpu"]:
    if marker not in picom_wrapper:
        errors.append(f"ming-picom missing backend selection marker {marker}")

for config_path in [
    "home/user/.config/picom/picom.conf",
    "etc/xdg/picom/picom-fallback.conf",
    "etc/xdg/picom/picom-lowmem.conf",
]:
    picom_config = require_file(config_path, "inactive-opacity = 1.0")
    for marker in ["active-opacity = 1.0", "frame-opacity = 1.0"]:
        if marker not in picom_config:
            errors.append(f"{config_path} missing opaque-window marker {marker}")
    if config_path in [
        "home/user/.config/picom/picom.conf",
        "etc/xdg/picom/picom-lowmem.conf",
    ] and "unredir-if-possible = false;" not in picom_config:
        errors.append(f"{config_path} must keep normal windows redirected for reliable Xfwm controls")

for retired_path in [
    "usr/share/applications/ming-wechat.desktop",
    "usr/share/applications/wps-office.desktop",
    "home/user/Desktop/ming-wechat.desktop",
    "home/user/Desktop/wechat.desktop",
    "home/user/Desktop/wps-office.desktop",
]:
    require_absent(retired_path, "WeChat and WPS are optional installs in Ming OS 26.3.2")

for binary in [
    "usr/bin/wmctrl",
    "usr/bin/xfce4-screensaver",
    "usr/bin/xfce4-screensaver-command",
    "usr/bin/fcitx5",
    "usr/bin/im-config",
    "usr/sbin/NetworkManager",
    "usr/sbin/wpa_supplicant",
    "usr/sbin/rfkill",
    "usr/sbin/iw",
    "usr/bin/bluetoothctl",
    "usr/bin/blueman-manager",
    "usr/sbin/ModemManager",
]:
    require_path(binary)

if not any(((root / candidate).is_file() or (root / candidate).is_symlink()) for candidate in [
    "usr/bin/microsoft-edge-stable",
    "usr/bin/microsoft-edge",
    "opt/microsoft/msedge/microsoft-edge",
]):
    errors.append("missing Microsoft Edge browser binary")

if not any((root / candidate).is_file() for candidate in [
    "usr/libexec/bluetooth/bluetoothd",
    "usr/lib/bluetooth/bluetoothd",
    "usr/sbin/bluetoothd",
]):
    errors.append("missing bluetoothd daemon")

require_file("usr/share/applications/ming-edge.desktop", "Exec=/usr/local/bin/ming-edge")
edge_wrapper = require_file("usr/local/bin/ming-edge", "homepage=/usr/share/ming-os/homepage/index.html")
for marker in ["--ozone-platform=x11", "--disable-gpu"]:
    if marker not in edge_wrapper:
        errors.append(f"ming-edge missing VM graphics marker {marker}")
for forbidden in ["--use-gl=egl", "UseMultiPlaneFormatForHardwareVideo"]:
    if forbidden in edge_wrapper:
        errors.append(f"ming-edge forces unstable graphics option {forbidden}")
edge_graphics = require_file("usr/local/bin/ming-edge-graphics", "active_render_node")
for marker in ["renderD*", "ffmpeg", "test-video", "set-mode", "auto", "compat"]:
    if marker not in edge_graphics:
        errors.append(f"ming-edge-graphics missing marker {marker}")
require_path("usr/bin/ffmpeg")
require_path("usr/bin/ffprobe")
for sample, codec in [("h264.mp4", "h264"), ("vp9.webm", "vp9")]:
    relative = f"usr/share/ming-os/media-tests/{sample}"
    require_path(relative)
    sample_path = root / relative
    if sample_path.exists() and sample_path.stat().st_size == 0:
        errors.append(f"Edge media test sample is empty: {relative}")
    if sample_path.exists() and (root / "usr/bin/ffprobe").exists():
        try:
            probe = subprocess.run(
                ["chroot", str(root), "/usr/bin/ffprobe", "-v", "error",
                 "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                 "-of", "default=nw=1:nk=1", f"/usr/share/ming-os/media-tests/{sample}"],
                capture_output=True, text=True, timeout=15)
            if probe.returncode != 0 or probe.stdout.strip() != codec:
                errors.append(f"Edge media test sample failed ffprobe: {relative}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"Edge media test sample probe could not run: {relative}: {exc}")
require_file("usr/share/ming-os/homepage/index.html", "Ming OS")
edge_policy = require_file("etc/opt/edge/policies/managed/ming-os.json", "HomepageLocation")
if "RestoreOnStartupURLs" not in edge_policy:
    errors.append("Edge policy must restore the Ming OS homepage")

require_path("usr/lib/x86_64-linux-gnu/dri/i965_drv_video.so")
require_path("usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so")
require_path("usr/lib/x86_64-linux-gnu/dri/radeonsi_drv_video.so")
require_path("usr/bin/vainfo")
require_path("usr/lib/xorg/modules/drivers/modesetting_drv.so")
for xorg_config in (root / "etc/X11/xorg.conf.d").glob("*.conf"):
    xorg_text = xorg_config.read_text(encoding="utf-8", errors="replace")
    if re.search(r'^\s*Driver\s+"intel"\s*$', xorg_text, flags=re.MULTILINE):
        errors.append(f"{xorg_config.relative_to(root)} forces the legacy Intel DDX")
    if re.search(r'^\s*Option\s+"AccelMethod"\s+"sna"\s*$', xorg_text, flags=re.MULTILINE):
        errors.append(f"{xorg_config.relative_to(root)} forces legacy Intel SNA")
intel_xorg_migration = require_file("usr/local/sbin/ming-intel-xorg-setup", "Ming OS legacy Intel Xorg setup")
for marker in [
    "ming-legacy-disabled", "preserved user-owned Intel Xorg config",
    'Identifier[[:space:]]+"Intel Graphics"', 'Driver[[:space:]]+"intel"',
    '"AccelMethod"[[:space:]]+"sna"', '"TripleBuffer"[[:space:]]+"true"',
]:
    if marker not in intel_xorg_migration:
        errors.append(f"ming-intel-xorg-setup missing safe migration marker {marker}")
if (root / "etc/X11/xorg.conf.d/20-intel.conf").exists():
    errors.append("active legacy 20-intel.conf must not be shipped in the image")
if (root / "etc/systemd/system/ming-intel-xorg.service").exists():
    errors.append("legacy Intel Xorg service must not be enabled in the image")
migration_unit = require_file("etc/systemd/system/ming-intel-xorg-migration.service", "Before=display-manager.service")
for marker in ["ExecStart=/usr/local/sbin/ming-intel-xorg-setup", "WantedBy=multi-user.target"]:
    if marker not in migration_unit:
        errors.append(f"Intel Xorg migration service missing {marker}")
if not (root / "etc/systemd/system/multi-user.target.wants/ming-intel-xorg-migration.service").is_symlink():
    errors.append("Intel Xorg migration service must be enabled before graphical login")
hardware_status = require_file("usr/local/bin/ming-hardware-status", "xorg_backend")
for marker in ["kernel_driver", "legacy_intel_config", "render_access", "vaapi_error", "xorg_log_evidence"]:
    if marker not in hardware_status:
        errors.append(f"ming-hardware-status missing graphics reporting field {marker}")

earlyoom_config = require_file("etc/default/earlyoom", "EARLYOOM_ARGS=")
prefer_match = re.search(
    r"--prefer(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|(\S+))",
    earlyoom_config,
    flags=re.IGNORECASE,
)
if prefer_match and "wps" in "".join(part or "" for part in prefer_match.groups()).lower():
    errors.append("earlyoom must not prefer WPS over the desktop session")

for firmware_package in [
    "wireless-regdb",
    "bluez-firmware",
    "firmware-mediatek",
    "firmware-libertas",
    "firmware-misc-nonfree",
    "firmware-iwlwifi",
    "firmware-realtek",
    "firmware-atheros",
    "firmware-brcm80211",
]:
    require_path(f"var/lib/dpkg/info/{firmware_package}.list")

kernel_module_patterns = {
    "rtw88_8821cu": "kernel/drivers/net/wireless/realtek/rtw88/rtw88_8821cu.ko*",
    "applespi": "kernel/drivers/input/keyboard/applespi.ko*",
    "spi_pxa2xx_platform": "kernel/drivers/spi/spi-pxa2xx-platform.ko*",
    "intel_lpss_pci": "kernel/drivers/mfd/intel-lpss-pci.ko*",
}
kernel_dirs = list((root / "lib/modules").glob("*"))
for module, pattern in kernel_module_patterns.items():
    if not any(any(kernel_dir.glob(pattern)) for kernel_dir in kernel_dirs):
        errors.append(f"kernel is missing required in-tree module {module}")

broadcom_cache = root / "usr/share/ming-os/driver-cache/broadcom"
broadcom_debs = list(broadcom_cache.glob("broadcom-sta-dkms_*.deb"))
if len(broadcom_debs) != 1 or broadcom_debs[0].stat().st_size == 0:
    errors.append("Broadcom offline cache must contain exactly one non-empty broadcom-sta-dkms deb")
require_file("usr/share/ming-os/driver-cache/broadcom/broadcom-sta.ids")
broadcom_sums = require_file("usr/share/ming-os/driver-cache/broadcom/SHA256SUMS", "broadcom-sta.ids")
if broadcom_debs and broadcom_debs[0].name not in broadcom_sums:
    errors.append("Broadcom SHA256SUMS does not cover the cached STA deb")
if (root / "var/lib/dpkg/info/broadcom-sta-dkms.list").exists():
    errors.append("broadcom-sta-dkms must be cached but not installed by default")
for installer_package in ["firmware-b43-installer", "firmware-b43legacy-installer"]:
    if (root / f"var/lib/dpkg/info/{installer_package}.list").exists():
        errors.append(f"{installer_package} must not run during the ISO build because its postinst downloads from GitHub")

hardware_preload = require_file("usr/local/sbin/ming-hardware-preload", "modules=(")
for conflicting_module in ["brcmfmac", "brcmsmac", "b43", "wl"]:
    if f"\n{conflicting_module}\n" in hardware_preload:
        errors.append(f"ming-hardware-preload must not blindly load Broadcom module {conflicting_module}")
network_modules = require_file("etc/modules-load.d/ming-network.conf")
for conflicting_module in ["brcmfmac", "brcmsmac", "b43", "wl"]:
    if f"\n{conflicting_module}\n" in f"\n{network_modules}\n":
        errors.append(f"modules-load.d must not force Broadcom module {conflicting_module}")
installed_identity = require_file("usr/local/sbin/ming-fix-installed-identity")
for conflicting_module in ["brcmfmac", "brcmsmac", "b43", "wl"]:
    if f"\n{conflicting_module}\n" in installed_identity:
        errors.append(f"installed identity repair must not force Broadcom module {conflicting_module}")

initramfs_modules = require_file("etc/initramfs-tools/modules", "applespi")
for marker in ["spi_pxa2xx_platform", "intel_lpss_pci"]:
    if marker not in initramfs_modules:
        errors.append(f"initramfs module list missing MacBook dependency {marker}")

broadcom_manager = require_file("usr/local/sbin/ming-broadcom-driver", "status --json")
for marker in [
    "broadcom-sta.ids", "SHA256SUMS", "mokutil --sb-state", "install)",
    "restore)", "/var/log/ming-broadcom-driver.log", "update-initramfs -u -k all",
]:
    if marker not in broadcom_manager:
        errors.append(f"ming-broadcom-driver missing marker {marker}")
for marker in ["ming-broadcom-driver", "安装 Broadcom 兼容驱动", "恢复开源驱动"]:
    if marker not in settings:
        errors.append(f"ming-settings missing Broadcom integration marker {marker}")

driver_diagnose = require_file("usr/local/bin/ming-driver-diagnose", "Ming OS driver diagnose")
for marker in [
    "Broadcom driver recommendation", "mokutil --sb-state", "dkms status",
    "vainfo", "lsinitramfs", "rtw88_8821cu", "applespi",
]:
    if marker not in driver_diagnose:
        errors.append(f"ming-driver-diagnose missing legacy compatibility marker {marker}")

require_path("usr/sbin/mbpfan")
require_path("usr/sbin/smartctl")
mac_fan_guard = require_file("usr/local/sbin/ming-is-intel-mac", "MacBook")
if "sys_vendor" not in mac_fan_guard or "product_name" not in mac_fan_guard:
    errors.append("ming-is-intel-mac must gate mbpfan by DMI identity")
mbpfan_override = require_file("etc/systemd/system/mbpfan.service.d/ming-hardware-guard.conf", "ExecCondition=/usr/local/sbin/ming-is-intel-mac")
for marker in ["modprobe coretemp", "modprobe applesmc"]:
    if marker not in mbpfan_override:
        errors.append(f"mbpfan hardware guard missing {marker}")
disk_health = require_file("usr/local/bin/ming-disk-health", "smartctl")
if "磁盘健康" not in settings or "ming-disk-health" not in settings:
    errors.append("ming-settings must expose on-demand disk health diagnostics")

fcitx_profile = require_file("home/user/.config/fcitx5/profile", "DefaultIM=pinyin")
for marker in ["Name=pinyin", "Name=rime"]:
    if marker not in fcitx_profile:
        errors.append(f"Fcitx5 profile must include {marker}")
require_file("home/user/.config/autostart/fcitx5.desktop", "fcitx5 -d --replace")
xinputrc = require_file("home/user/.xinputrc", "XMODIFIERS=@im=fcitx")
if "run_im fcitx5" in xinputrc or "fcitx5 -d --replace" in xinputrc:
    errors.append("xinputrc must not start a second Fcitx5 daemon")
require_file("etc/X11/Xsession.d/80-ming-fcitx5", "XMODIFIERS=@im=fcitx")
require_file("etc/skel/.config/fcitx5/profile", "Name=rime")
require_file("etc/skel/.config/autostart/fcitx5.desktop", "fcitx5 -d --replace")
input_theme = require_file("usr/local/share/fcitx5/themes/Ming-Candidate/theme.conf", "Name=Ming Candidate")
for marker in ["NormalColor", "HighlightCandidateColor"]:
    if marker not in input_theme:
        errors.append(f"Ming Candidate theme missing {marker}")
input_classicui = require_file("home/user/.config/fcitx5/conf/classicui.conf", "Theme=Ming-Candidate")
for marker in ["Font=Noto Sans CJK SC 15", "MenuFont=Noto Sans CJK SC 16", "Vertical Candidate List=True"]:
    if marker not in input_classicui:
        errors.append(f"Ming Fcitx5 UI config missing {marker}")
input_config = require_file("home/user/.config/fcitx5/config", "DefaultPageSize=7")
if "DefaultPageSize=7" not in input_config:
    errors.append("Ming Fcitx5 config must use seven candidate rows")
input_control = require_file("usr/local/sbin/ming-input-control", "set-engine")
for marker in ["RIME_SCHEMA", "rime_addon_available"]:
    if marker not in input_control:
        errors.append(f"ming-input-control missing {marker} readiness check")

bt_conf = require_file("etc/bluetooth/main.conf", "AutoEnable=true")
if "ControllerMode = dual" not in bt_conf:
    errors.append("Bluetooth must support dual controller mode for broad hardware compatibility")

bt_override = require_file("etc/systemd/system/bluetooth.service.d/ming-radio-unblock.conf", "rfkill unblock bluetooth")
if "btmgmt power on" not in bt_override:
    errors.append("Bluetooth service override must power on adapters after startup")
if not (root / "etc/systemd/system/bluetooth.target.wants/bluetooth.service").is_symlink():
    errors.append("bluetooth.service must be enabled when BlueZ is installed")
require_absent(
    "etc/systemd/system/bluetooth.service.d/delay.conf",
    "Bluetooth must not wait for graphical.target or a fixed startup delay",
)

regdom_service = require_file("etc/systemd/system/ming-regdom.service", "iw reg set CN")
for marker in ["Before=NetworkManager.service", "WantedBy=multi-user.target"]:
    if marker not in regdom_service:
        errors.append(f"CN regulatory domain service missing {marker}")
if not (root / "etc/systemd/system/multi-user.target.wants/ming-regdom.service").is_symlink():
    errors.append("CN regulatory domain service must be enabled before NetworkManager")

radio_repair = require_file("usr/local/sbin/ming-radio-repair", "bluetooth-status --json")
for marker in [
    "exec pkexec /usr/local/sbin/ming-radio-repair bluetooth",
    "rfkill unblock bluetooth", "systemctl enable bluetooth.service",
    "systemctl start bluetooth.service", "no_hardware", "/var/log/ming-radio-repair.log",
]:
    if marker not in radio_repair:
        errors.append(f"ming-radio-repair missing Bluetooth recovery marker {marker}")
if not os.access(root / "usr/local/sbin/ming-radio-repair", os.X_OK):
    errors.append("ming-radio-repair must be executable")

hardware_modules = require_file("usr/local/sbin/ming-hardware-preload", "iwlwifi")
for marker in [
    "btusb", "btintel", "btrtl", "btbcm", "ath3k",
    "hid_multitouch", "bcm5974", "hid_apple", "applespi",
    "spi_pxa2xx_platform", "spi_pxa2xx_pci", "thinkpad_acpi", "ideapad_laptop",
    "huawei_wmi", "surface_aggregator", "surface_hid_core",
]:
    if marker not in hardware_modules:
        errors.append(f"hardware modules preload missing {marker}")
require_file("etc/systemd/system/ming-hardware-preload.service", "Before=NetworkManager.service bluetooth.service display-manager.service")
require_file("etc/modules-load.d/ming-hardware.conf", "loop")
network_modules = require_file("etc/modules-load.d/ming-network.conf", "iwlwifi")
for forbidden_module in ["r8168", "r8169"]:
    if re.search(rf"(?m)^\s*{forbidden_module}\s*$", network_modules):
        errors.append(f"Ethernet module must be selected by modalias, not preloaded: {forbidden_module}")
if not (root / "etc/systemd/system/multi-user.target.wants/NetworkManager.service").is_symlink():
    errors.append("NetworkManager.service must be enabled as the sole network owner")
for owner in ["networking.service", "systemd-networkd.service"]:
    if (root / "etc/systemd/system/multi-user.target.wants" / owner).exists():
        errors.append(f"competing network owner must be disabled: {owner}")

old_hw_modprobe = require_file("etc/modprobe.d/ming-old-hardware.conf", "bt_coex_active=1")
for marker in ["psmouse synaptics_intertouch=0", "snd_hda_intel power_save=0"]:
    if marker not in old_hw_modprobe:
        errors.append(f"old hardware modprobe policy missing {marker}")

ota_client = require_file("usr/local/bin/ming-update", "https://ming.scallion.uno")
for marker in [
    "resolve_home()",
    'HOME="${HOME:-$(resolve_home)}"',
    "find_cached_manifest()",
    "/home/*/.cache/ming-update/update_info.json",
    "ota_doctor",
    "ming.ota_backup_uuid=",
    "ming.ota_manifest=",
    'STAGING_RECORD="/var/lib/ming-update/staging.json"',
    "validate_staging_inputs",
    'basename -- "${iso_name}"',
    "home_is_independent_device",
]:
    if marker not in ota_client:
        errors.append(f"ming-update missing HOME safety marker {marker}")
if "/api/onion-update" not in ota_client:
    errors.append("ming-update must use the deployed /api/onion-update endpoint")
if 'readonly API_ENDPOINT="/api/ming-update"' in ota_client:
    errors.append("ming-update must not default to the undeployed /api/ming-update endpoint")

for retired_path in [
    "usr/local/bin/ming-master",
    "usr/local/bin/ming-master.py",
    "usr/share/applications/ming-master.desktop",
    "home/user/Desktop/ming-master.desktop",
]:
    require_absent(retired_path, "Ming Security Manager was removed from the default install")

require_file("usr/sbin/cupsd")
if not any((root / candidate).is_file() for candidate in [
    "usr/bin/system-config-printer",
    "usr/share/system-config-printer/system-config-printer.py",
]):
    errors.append("missing system-config-printer GUI entry")

nm_backend = root / "etc/NetworkManager/conf.d/wifi-backend.conf"
if nm_backend.exists():
    text = nm_backend.read_text(encoding="utf-8", errors="replace")
    if "wifi.backend=iwd" in text:
        errors.append("NetworkManager defaults to iwd; r4 must default to wpa_supplicant for old Wi-Fi")
    if "wifi.backend=wpa_supplicant" not in text:
        errors.append("NetworkManager must explicitly use wpa_supplicant by default")
else:
    errors.append("missing NetworkManager Wi-Fi backend config")

pwquality = require_file("etc/security/pwquality.conf", "dictcheck = 0")
if "minlen = 1" not in pwquality and "minlen=1" not in pwquality:
    errors.append("pwquality.conf must keep installer password policy lenient")

ming_release = require_file("etc/ming-release", "Ming OS")
ming_share_release = root / "usr/share/ming-release"
if not (ming_share_release.exists() or ming_share_release.is_symlink()):
    errors.append("missing usr/share/ming-release")

grub_defaults = require_file("etc/default/grub.d/10-ming-os.cfg", "GRUB_TIMEOUT=3")
for marker in [
    "GRUB_TIMEOUT_STYLE=menu",
    "GRUB_TERMINAL_INPUT=console",
    "GRUB_RECORDFAIL_TIMEOUT=0",
    "GRUB_DISABLE_SUBMENU=true",
    "GRUB_DISABLE_OS_PROBER=true",
    "GRUB_DISABLE_RECOVERY=true",
]:
    if marker not in grub_defaults:
        errors.append(f"grub defaults missing {marker}")

hard_disk_grub = require_file("etc/grub.d/09_ming_os", "menuentry 'Ming OS'")
official_grub = root / "etc/grub.d/10_linux"
if not official_grub.is_file():
    errors.append("Debian official /etc/grub.d/10_linux generator is missing")
elif not (official_grub.stat().st_mode & 0o111):
    errors.append("Debian official /etc/grub.d/10_linux generator must remain executable for kernel fallback")
if "ming.installer=1" in hard_disk_grub or "boot=live" in hard_disk_grub or "安装 Ming OS" in hard_disk_grub:
    errors.append("installed hard-disk GRUB entry must not boot the Live installer")
if " splash" in hard_disk_grub:
    errors.append("installed hard-disk GRUB entry must not use splash on old hardware")
for marker in ["Ming OS (Safe Graphics)", "Ming OS (Old Intel / ThinkPad / MacBook)", "nomodeset"]:
    if marker not in hard_disk_grub:
        errors.append(f"installed hard-disk GRUB entry missing compatibility marker {marker}")
normal_grub_match = re.search(r"menuentry 'Ming OS'.*?\n}\n", hard_disk_grub, re.S)
if normal_grub_match and re.search(r"(?:^|\s)(?:nomodeset|i915\.modeset=0|pcie_aspm=off|pci=nomsi|acpi_osi=Linux)(?:\s|$)", normal_grub_match.group(0)):
    errors.append("installed hard-disk default GRUB entry must use i915/KMS without forced safe-mode flags")

lightdm_autologin = require_file("etc/lightdm/lightdm.conf.d/60-ming-autologin.conf", "autologin-session=ming-installer")
for marker in ["user-session=ming-installer", "greeter-session=lightdm-gtk-greeter", "allow-guest=false"]:
    if marker not in lightdm_autologin:
        errors.append(f"Live LightDM installer session missing {marker}")

installed_identity = require_file("usr/local/sbin/ming-fix-installed-identity", "autologin-session=xfce")
if "user-session=xfce" not in installed_identity:
    errors.append("installed-system identity repair must restore the Xfce session")
for marker in [
    "restore_ota_home",
    "cmdline_value ming.ota_backup_uuid",
    "cmdline_value ming.ota_manifest",
    "ming-ota-restore.log",
    '"${engine}" restore',
]:
    if marker not in installed_identity:
        errors.append(f"installed-system identity repair missing OTA restore marker {marker}")

require_file("boot/grub/themes/ming/theme.txt", 'title-text: "Ming OS"')
ota_preflight = require_file("usr/local/sbin/ming-ota-preflight", "OTA preflight passed before partitioning")
for marker in ["readlink -f", "ming-ota-backup verify", "/run/ming-ota-preflight.ok"]:
    if marker not in ota_preflight:
        errors.append(f"OTA preflight missing marker {marker}")
ota_guard = require_file("usr/local/lib/ming-os/ming_ota_target_guard.py", "validate_target")
for marker in ["lsblk", "same physical disk", "partition plan has no root target"]:
    if marker not in ota_guard:
        errors.append(f"OTA target guard missing marker {marker}")
require_file(
    "usr/lib/x86_64-linux-gnu/calamares/modules/ming-ota-target-guard/main.py",
    "validate_from_marker",
)

for desktop_runtime in [
    "usr/sbin/lightdm",
    "usr/bin/startxfce4",
    "usr/bin/xfce4-session",
    "usr/bin/xfdesktop",
    "usr/bin/thunar",
    "usr/bin/xinput",
    "usr/bin/xfce4-notifyd",
    "usr/sbin/mkfs.ext4",
    "lib/systemd/system/lightdm.service",
]:
    require_path(desktop_runtime)
for retired_runtime in [
    "usr/bin/xfce4-panel",
    "usr/bin/xfce4-appfinder",
    "usr/lib/x86_64-linux-gnu/xfce4/panel/plugins/libwhiskermenu.so",
    "usr/bin/volumeicon",
    "usr/bin/nm-applet",
]:
    if (root / retired_runtime).exists():
        errors.append(f"retired duplicate shell runtime must not be installed: {retired_runtime}")
configured_user = os.environ.get("MING_USER", "").strip()
autostart_roots = [
    root / "etc/xdg/autostart",
    root / "etc/skel/.config/autostart",
    root / "home/user/.config/autostart",
]
if configured_user and "/" not in configured_user:
    autostart_roots.append(root / "home" / configured_user / ".config/autostart")
home_root = root / "home"
if home_root.is_dir():
    autostart_roots.extend(path / ".config/autostart" for path in home_root.iterdir() if path.is_dir())

def autostart_processes(value, current_desktop="XFCE"):
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read_string(value)
        entry = parser["Desktop Entry"]
        if entry.getboolean("Hidden", fallback=False):
            return ()
        if not entry.getboolean("X-GNOME-Autostart-enabled", fallback=True):
            return ()
        only = {item.casefold() for item in entry.get("OnlyShowIn", "").split(";") if item}
        excluded = {item.casefold() for item in entry.get("NotShowIn", "").split(";") if item}
        desktop = current_desktop.casefold()
        if (only and desktop not in only) or desktop in excluded:
            return ()
        argv = shlex.split(entry.get("Exec", ""), posix=True)
        if not argv:
            return ()
        offset = 0
        if Path(argv[0]).name == "env":
            offset = 1
            while offset < len(argv) and (argv[offset].startswith("-") or "=" in argv[offset]):
                offset += 1
        executable = Path(argv[offset]).name if offset < len(argv) else ""
        programs = []
        if executable in {"sh", "bash", "dash", "zsh", "ksh"} and "-c" in argv[offset + 1:]:
            script_index = argv.index("-c", offset + 1) + 1
            lexer = shlex.shlex(io.StringIO(argv[script_index]), posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""
            segment = []
            for token in list(lexer) + [";"]:
                if token and all(char in ";&|" for char in token):
                    executable = unwrapped_program(segment)
                    if executable:
                        programs.append(executable)
                    segment = []
                else:
                    segment.append(token)
        else:
            programs.append(unwrapped_program(argv[offset:]))
        return tuple(dict.fromkeys(programs))
    except (KeyError, ValueError, configparser.Error):
        return ()

def unwrapped_program(tokens):
    tokens = list(tokens)
    while tokens:
        executable = Path(tokens[0]).name
        if tokens[0] == "exec" or executable == "nohup" or ("=" in tokens[0] and not tokens[0].startswith("-")):
            tokens.pop(0)
            continue
        if executable == "env":
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                tokens.pop(0)
            continue
        if executable == "timeout":
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            if tokens:
                tokens.pop(0)
            continue
        return executable
    return ""

for autostart_root in autostart_roots:
    if not autostart_root.exists():
        continue
    for desktop_entry in autostart_root.glob("*.desktop"):
        content = desktop_entry.read_text(encoding="utf-8", errors="replace")
        processes = autostart_processes(content)
        for duplicate in ["xfce4-panel", "xfce4-appfinder", "whiskermenu", "volumeicon", "nm-applet", "xfdesktop", "xfce4-power-manager"]:
            if duplicate in processes:
                errors.append(f"normal session starts duplicate shell process {duplicate}: {desktop_entry}")
display_manager = root / "etc/systemd/system/display-manager.service"
if not (display_manager.exists() or display_manager.is_symlink()):
    errors.append("LightDM is installed but display-manager.service is not enabled")
require_file("usr/share/xsessions/ming-installer.desktop", "Exec=/usr/local/bin/ming-installer-session")
calamares_launcher = require_file("usr/local/bin/ming-calamares-launcher", "ming-calamares.lock")
if "flock -n 9" not in calamares_launcher:
    errors.append("Calamares launcher must enforce a single installer instance")
if (root / "etc/systemd/system/graphical.target.wants/ming-live-installer.service").exists():
    errors.append("ming-live-installer.service must stay disabled to avoid duplicate Calamares windows")
sfdisk_wrapper = require_file("usr/sbin/sfdisk", "sfdisk.real")
for marker in ["--append", "sed '/^[[:space:]]*write", "Created a new partition"]:
    if marker not in sfdisk_wrapper:
        errors.append(f"Live sfdisk compatibility wrapper missing {marker}")
require_path("usr/lib/ming-os/sfdisk.real")
for marker in ['"${target}/usr/lib/ming-os/sfdisk.real"', '"${target}/usr/sbin/sfdisk"']:
    if marker not in installed_identity:
        errors.append(f"installed-system identity repair must restore sfdisk via {marker}")
installer_session = require_file("usr/local/bin/ming-installer-session", "xfwm4 --replace")
if "wmctrl -x -a calamares.calamares" not in installer_session:
    errors.append("installer session must focus Calamares through xfwm4/wmctrl")
for font_marker in [
    "usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
]:
    if not (root / font_marker).exists():
        errors.append(f"missing required Noto font asset: {font_marker}")
font_policy = require_file("etc/fonts/conf.d/99-ming-os-fonts.conf", "Noto Sans CJK SC")
require_path("usr/bin/fc-match")
for marker in ["antialias", "hinting", "hintslight", "WenQuanYi Micro Hei", "monospace", "Noto Sans Mono"]:
    if marker not in font_policy:
        errors.append(f"fontconfig policy missing {marker}")

desktop_names = [
    "ming-network-repair.desktop",
    "ming-driver-diagnose.desktop",
    "ming-diagnostic-bundle.desktop",
    "ming-surface-support.desktop",
    "ming-classic-mode.desktop",
]
for base in ["usr/share/applications", "home/user/Desktop", "etc/skel/Desktop"]:
    for name in desktop_names:
        if (root / base / name).exists():
            errors.append(f"{base}/{name} should not exist; tools must stay inside Ming Settings")

if errors:
    for error in errors:
        print(f"R4_COMPAT_ERROR: {error}", file=sys.stderr)
    sys.exit(1)
PY

    # The Python gate above checks the units are present and structurally
    # complete.  Ask the target system's systemd parser to validate the same
    # shipped units, so a typo cannot reach an ISO only to be ignored at boot.
    local unit
    for unit in \
        /etc/systemd/system/ming-intel-xorg-migration.service \
        /etc/systemd/system/ming-regdom.service \
        /etc/systemd/system/ming-hardware-preload.service; do
        if ! chroot_exec /usr/bin/systemd-analyze verify "${unit}"; then
            log_error "systemd-analyze verify failed for ${unit}"
            return 1
        fi
    done

    local elf_hits
    elf_hits=$(find "${CHROOT_DIR}/usr/local/bin" "${CHROOT_DIR}/usr/local/sbin" -type f -perm -111 -print0 2>/dev/null \
        | xargs -0 -r file 2>/dev/null \
        | awk -F: '/ELF/ {print $1}' \
        | while IFS= read -r elf; do
            if objdump -d "${elf}" 2>/dev/null | grep -Eiq '\b(vzeroupper|vinsert|vextract|vbroadcast|vperm|ymm[0-9]|zmm[0-9]|avx2)\b'; then
                echo "${elf#${CHROOT_DIR}/}"
            fi
          done)
    if [[ -n "${elf_hits}" ]]; then
        log_error "Found AVX/AVX2-looking instructions in locally shipped executables:"
        echo "${elf_hits}" >&2
        return 1
    fi
    log_info "Ming OS r4 legacy hardware and Settings Hub validation passed"
}

write_grub_config() {
    cat > "${ISO_DIR}/boot/grub/grub.cfg" << GRUBCFG
set default=0
set timeout=8
set pager=1

insmod part_gpt
insmod part_msdos
insmod ext2
insmod iso9660
insmod all_video
insmod gfxterm
insmod png
insmod font
insmod search
insmod search_label
insmod search_fs_file
# 老 BIOS 机器（i3-370M 等 Westmere/Arrandale）必须显式加载 linux/initrd 模块
# 否则 GRUB 报 "can't find command 'linux'" 并无法引导
insmod linux
insmod loopback
insmod probe

search --no-floppy --label ${ISO_VOLUME_ID} --set=root
search --no-floppy --file --set=root /live/vmlinuz
set prefix=(\$root)/boot/grub
set theme=(\$root)/boot/grub/themes/ming/theme.txt

loadfont /boot/grub/fonts/unicode.pf2
terminal_input console
terminal_output gfxterm

set color_normal=white/black
set color_highlight=black/light-gray
set menu_color_normal=white/black
set menu_color_highlight=black/light-gray
set gfxmode=auto
set default=0
set timeout=8

menuentry "安装 Ming OS ${MING_OS_VERSION} (Install Ming OS)" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1
    initrd /live/initrd
}

menuentry "安装 Ming OS ${MING_OS_VERSION}  (安全显卡模式 / Safe Graphics)" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog ming.installer=1 ming.safe_graphics=1 nomodeset vga=791
    initrd /live/initrd
}

menuentry "Ming OS ${MING_OS_VERSION} 老电脑兼容模式 (1-3代酷睿 / E3 V1-V2)" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1
    initrd /live/initrd
}

menuentry "Ming OS ${MING_OS_VERSION} Radeon Legacy 恢复模式" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 radeon.modeset=1 amdgpu.modeset=0
    initrd /live/initrd
}

menuentry "Ming OS ${MING_OS_VERSION} Radeon GCN 尝试模式 (SI/CIK)" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 amdgpu.si_support=1 radeon.si_support=0 amdgpu.cik_support=1 radeon.cik_support=0
    initrd /live/initrd
}

# Surface Pro 1/2/3：Atom/Ivy Bridge + IPTS 触控 + 特殊 EFI 固件
# 关键参数：i8042.noloop 修复键盘不识别；ipts=1 启用触控板协议；
# intel_idle.max_cstate=1 防止老Atom/IvyBridge挂起后不醒；
# acpi_mask_gpe=0x6e 处理 Surface 特定 ACPI GPE 事件风暴
menuentry "Ming OS ${MING_OS_VERSION} Surface Pro 1/2/3 专用模式" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 i8042.noloop i8042.nomux i8042.nopnp i8042.reset intel_idle.max_cstate=1 acpi_mask_gpe=0x6e
    initrd /live/initrd
}

# Mac EFI / 苹果 MacBook：Apple EFI 固件有特殊 ACPI 实现
# acpi_osi=Darwin 让 BIOS 暴露 Mac 专用 ACPI 表；
# reboot=pci 解决 Mac 重启后停在黑屏问题
menuentry "Ming OS ${MING_OS_VERSION} Mac EFI / MacBook 兼容模式" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 acpi_osi=Darwin reboot=pci
    initrd /live/initrd
}

GRUBCFG
}


build_iso() {
    log_step "构建 ISO 镜像"
    rm -rf "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}/boot/grub"
    mkdir -p "${ISO_DIR}/boot/grub/themes/ming"
    mkdir -p "${ISO_DIR}/live"
    if [[ ! -s "${SCRIPT_DIR}/assets/grub-theme/theme.txt" ]]; then
        log_error "缺少 Ming GRUB 主题资源"
        exit 1
    fi
    install -m 0644 "${SCRIPT_DIR}/assets/grub-theme/theme.txt" "${ISO_DIR}/boot/grub/themes/ming/theme.txt"

    local kernel_version kernel_path kernel_sha
    kernel_version=$(select_latest_kernel)
    if [[ -z "${kernel_version}" ]]; then
        log_error "未找到 chroot 内核: ${CHROOT_DIR}/boot/vmlinuz-*"
        exit 1
    fi
    kernel_path="${CHROOT_DIR}/boot/vmlinuz-${kernel_version}"
    local initrd_path
    initrd_path="${CHROOT_DIR}/boot/initrd.img-${kernel_version}"
    if [[ ! -s "${initrd_path}" ]]; then
        initrd_path=$(find "${CHROOT_DIR}/boot" -maxdepth 1 -type f -name 'initrd.img-*' | sort -V | tail -n 1)
    fi
    if [[ ! -s "${initrd_path}" ]]; then
        log_error "未找到 initrd: ${CHROOT_DIR}/boot/initrd.img-*"
        exit 1
    fi

    validate_linux_kernel "${kernel_path}" "source ${kernel_version}"
    kernel_sha=$(sha256sum "${kernel_path}" | awk '{print $1}')

    cp "${kernel_path}" "${ISO_DIR}/live/vmlinuz"
    cp "${initrd_path}" "${ISO_DIR}/live/initrd"
    cmp -s "${kernel_path}" "${ISO_DIR}/live/vmlinuz" || {
        log_error "复制到 ISO 工作目录的 vmlinuz 与源内核不一致"
        exit 1
    }
    validate_linux_kernel "${ISO_DIR}/live/vmlinuz" "ISO workdir /live/vmlinuz"
    validate_calamares_config
    validate_r4_compatibility
    log_info "使用内核 ${kernel_version}, SHA256=${kernel_sha}"

    log_info "生成 squashfs 文件系统..."
    mksquashfs "${CHROOT_DIR}" "${ISO_DIR}/live/filesystem.squashfs" \
        -comp xz \
        -Xbcj x86 \
        -b 1M \
        -no-xattrs \
        -no-progress

    write_grub_config
    validate_iso_grub_config

    log_info "配置 GRUB 字体..."
    mkdir -p "${ISO_DIR}/boot/grub/fonts"
    if [[ ! -s /usr/share/grub/unicode.pf2 ]]; then
        log_error "required GRUB unicode font is missing: /usr/share/grub/unicode.pf2"
        exit 1
    fi
    cp /usr/share/grub/unicode.pf2 "${ISO_DIR}/boot/grub/fonts/"

    if [[ -f "${CHROOT_DIR}/boot/memtest86+x64.efi" ]]; then
        mkdir -p "${ISO_DIR}/boot"
        cp "${CHROOT_DIR}/boot/memtest86+x64.efi" "${ISO_DIR}/boot/"
    fi

    log_info "生成 ISO 镜像文件..."
    local suffix="${MING_OS_BUILD_SUFFIX}"
    local iso_name
    if [[ -n "${suffix}" ]]; then
        iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64-${suffix}.iso"
    else
        iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64.iso"
    fi

    build_iso_manual "${iso_name}"

    if [[ -f "${OUTPUT_DIR}/${iso_name}" ]]; then
        validate_iso_kernel "${OUTPUT_DIR}/${iso_name}" "${kernel_sha}"
        validate_iso_boot_layout "${OUTPUT_DIR}/${iso_name}"
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
    local early_cfg="${iso_workdir}/boot/grub/early-grub.cfg"

    mkdir -p "${iso_workdir}/EFI/BOOT"
    mkdir -p "${iso_workdir}/isolinux"

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

    # isolinux 存根：Rufus ISO 模式写盘时会在 MBR 注入寻找 isolinux.bin 的代码。
    # 若 ISO 里没有 isolinux.bin，老 BIOS 机器（如 i5-2430M/Dell Inspiron）会报
    # "isolinux.bin missing or corrupt" 并尝试 PXE 引导。
    # 解决方案：复制 isolinux.bin + ldlinux.c32，使用 isolinux.cfg 直接加载 Linux。
    local isolinux_bin=""
    for f in /usr/lib/ISOLINUX/isolinux.bin /usr/lib/syslinux/isolinux.bin; do
        [[ -f "${f}" ]] && { isolinux_bin="${f}"; break; }
    done
    local ldlinux_c32=""
    for f in /usr/lib/syslinux/modules/bios/ldlinux.c32 /usr/lib/syslinux/ldlinux.c32; do
        [[ -f "${f}" ]] && { ldlinux_c32="${f}"; break; }
    done
    if [[ -n "${isolinux_bin}" && -n "${ldlinux_c32}" ]]; then
        cp "${isolinux_bin}" "${iso_workdir}/isolinux/isolinux.bin"
        cp "${ldlinux_c32}"  "${iso_workdir}/isolinux/ldlinux.c32"
        for module in libcom32.c32 libutil.c32 menu.c32 vesamenu.c32; do
            for f in "/usr/lib/syslinux/modules/bios/${module}" "/usr/lib/syslinux/${module}"; do
                [[ -f "${f}" ]] && { cp "${f}" "${iso_workdir}/isolinux/${module}"; break; }
            done
        done
        cat > "${iso_workdir}/isolinux/isolinux.cfg" << 'ISOLINUXCFG'
# Ming OS BIOS/Rufus fallback. Boot Linux directly instead of chain-loading GRUB.
UI menu.c32
DEFAULT install
PROMPT 0
TIMEOUT 80
MENU TITLE Ming OS Installer

LABEL install
  MENU LABEL Install Ming OS
  KERNEL /live/vmlinuz
  INITRD /live/initrd
  APPEND boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=user user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1

LABEL safe
  MENU LABEL Install Ming OS (Safe Graphics)
  KERNEL /live/vmlinuz
  INITRD /live/initrd
  APPEND boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=user user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog ming.installer=1 ming.safe_graphics=1 nomodeset vga=791

LABEL oldpc
  MENU LABEL Ming OS Old PC Compatibility
  KERNEL /live/vmlinuz
  INITRD /live/initrd
  APPEND boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=user user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1
ISOLINUXCFG
        log_info "isolinux direct Linux fallback written for Rufus BIOS mode"
    else
        log_error "未找到 isolinux.bin/ldlinux.c32，拒绝生成缺少 Rufus/老 BIOS fallback 的 ISO"
        return 1
    fi
    validate_isolinux_fallback "${iso_workdir}"

    # Both BIOS and UEFI boot images embed this tiny config. Without it GRUB can
    # start but stop at the prompt instead of loading the Ming OS menu.
    cat > "${early_cfg}" << EOF
search --no-floppy --label ${ISO_VOLUME_ID} --set=root
search --no-floppy --file --set=root /live/vmlinuz
set prefix=(\$root)/boot/grub
configfile (\$root)/boot/grub/grub.cfg
EOF

    if command -v grub-mkimage &>/dev/null && [[ -d /usr/lib/grub/x86_64-efi ]]; then
        grub-mkimage \
            -O x86_64-efi \
            -p /boot/grub \
            -c "${early_cfg}" \
            -o "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" \
            part_gpt part_msdos fat ntfs exfat iso9660 udf ext2 all_video font gfxterm gfxmenu \
            normal configfile search search_fs_file search_label search_fs_uuid loadenv \
            linux linux16 chain boot jpeg png 2>/dev/null || true
    fi

    # 32位UEFI（部分老旧平板/上网本，如Bay Trail）
    if command -v grub-mkimage &>/dev/null && [[ -d /usr/lib/grub/i386-efi ]]; then
        grub-mkimage \
            -O i386-efi \
            -p /boot/grub \
            -c "${early_cfg}" \
            -o "${iso_workdir}/EFI/BOOT/BOOTIA32.EFI" \
            part_gpt part_msdos fat iso9660 udf ext2 all_video font gfxterm normal configfile \
            search search_fs_file search_label linux linux16 chain boot 2>/dev/null || true
    fi

    if [[ ! -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]] && [[ -f /usr/lib/grub/x86_64-efi/monolithic/grubx64.efi ]]; then
        cp /usr/lib/grub/x86_64-efi/monolithic/grubx64.efi "${iso_workdir}/EFI/BOOT/BOOTX64.EFI"
        log_warn "使用未嵌入 early-grub.cfg 的 monolithic UEFI GRUB 作为回退"
    fi

    if [[ -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]]; then
        log_info "已生成 EFI 引导文件 (BOOTX64.EFI with early config)"
    fi

    local shim_x64="" signed_grub_x64=""
    for f in \
        /usr/lib/shim/shimx64.efi.signed \
        /usr/lib/shim/shimx64.efi.signed.latest \
        /usr/lib/shim/shimx64.efi; do
        [[ -f "${f}" ]] && { shim_x64="${f}"; break; }
    done
    for f in \
        /usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed \
        /usr/lib/grub/x86_64-efi-signed/grubx64.efi \
        /usr/lib/grub/x86_64-efi/monolithic/grubx64.efi; do
        [[ -f "${f}" ]] && { signed_grub_x64="${f}"; break; }
    done
    if [[ -n "${shim_x64}" && -n "${signed_grub_x64}" ]]; then
        cp "${shim_x64}" "${iso_workdir}/EFI/BOOT/BOOTX64.EFI"
        cp "${signed_grub_x64}" "${iso_workdir}/EFI/BOOT/grubx64.efi"
        cp "${early_cfg}" "${iso_workdir}/EFI/BOOT/grub.cfg"
        mkdir -p "${iso_workdir}/EFI/debian"
        cp "${early_cfg}" "${iso_workdir}/EFI/debian/grub.cfg"
        log_info "Secure-Boot-friendly removable EFI path written with shim + signed GRUB"
    fi

    if command -v grub-mkimage &>/dev/null && [[ -f /usr/lib/grub/i386-pc/cdboot.img ]]; then
        grub-mkimage \
            -O i386-pc \
            -p /boot/grub \
            -c "${early_cfg}" \
            -o "${iso_workdir}/boot/grub/i386-pc/core.img" \
            biosdisk iso9660 udf part_gpt part_msdos normal configfile search search_fs_file \
            search_label linux linux16 all_video font gfxterm boot 2>/dev/null || true

        if [[ -f "${iso_workdir}/boot/grub/i386-pc/core.img" ]]; then
            cat /usr/lib/grub/i386-pc/cdboot.img \
                "${iso_workdir}/boot/grub/i386-pc/core.img" \
                > "${iso_workdir}/boot/grub/i386-pc/eltorito.img"
        fi
    fi

    if [[ -f "${iso_workdir}/isolinux/isolinux.bin" ]]; then
        log_info "使用 xorriso 手动构建可引导 ISO (isolinux BIOS + GRUB UEFI)..."

        local efi_data=""
        if [[ -f "${iso_workdir}/EFI/BOOT/BOOTX64.EFI" ]]; then
            efi_data="-eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot"
            local efi_img="${iso_workdir}/boot/grub/efi.img"
            local efi_tmpdir
            efi_tmpdir="$(mktemp -d)"
            mkdir -p "${efi_tmpdir}/EFI/BOOT"
            cp "${iso_workdir}/EFI/BOOT/"* "${efi_tmpdir}/EFI/BOOT/"
            # 8MB：容纳 BOOTX64.EFI + BOOTIA32.EFI（32位UEFI老机器）
            dd if=/dev/zero of="${efi_img}" bs=1M count=8 2>/dev/null
            mkfs.vfat -F 12 "${efi_img}" 2>/dev/null
            mmd -i "${efi_img}" ::EFI ::EFI/BOOT 2>/dev/null
            mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/BOOTX64.EFI" ::EFI/BOOT/BOOTX64.EFI 2>/dev/null
            if [[ -f "${efi_tmpdir}/EFI/BOOT/BOOTIA32.EFI" ]]; then
                mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/BOOTIA32.EFI" ::EFI/BOOT/BOOTIA32.EFI 2>/dev/null
            fi
            if [[ -f "${efi_tmpdir}/EFI/BOOT/grubx64.efi" ]]; then
                mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/grubx64.efi" ::EFI/BOOT/grubx64.efi 2>/dev/null
            fi
            if [[ -f "${efi_tmpdir}/EFI/BOOT/grub.cfg" ]]; then
                mcopy -i "${efi_img}" "${efi_tmpdir}/EFI/BOOT/grub.cfg" ::EFI/BOOT/grub.cfg 2>/dev/null
            fi
            if [[ -f "${iso_workdir}/EFI/debian/grub.cfg" ]]; then
                mmd -i "${efi_img}" ::EFI/debian 2>/dev/null || true
                mcopy -i "${efi_img}" "${iso_workdir}/EFI/debian/grub.cfg" ::EFI/debian/grub.cfg 2>/dev/null
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

        local xorriso_args=(
            -as mkisofs
            -iso-level 3
            -V "${ISO_VOLUME_ID}"
            -full-iso9660-filenames
            -R -J -joliet-long
            -c isolinux/boot.cat
            -b isolinux/isolinux.bin
            -no-emul-boot
            -boot-load-size 4
            -boot-info-table
        )
        if [[ -n "${efi_data}" ]]; then
            # shellcheck disable=SC2206
            xorriso_args+=(${efi_data})
        fi
        xorriso_args+=(
            -isohybrid-gpt-basdat
        )
        if [[ ${#hybrid_mbr_args[@]} -gt 0 ]]; then
            xorriso_args+=("${hybrid_mbr_args[@]}")
        fi
        xorriso_args+=(
            -o "${OUTPUT_DIR}/${iso_name}"
            "${iso_workdir}"
        )

        xorriso "${xorriso_args[@]}" 2>&1
    else
        log_error "缺少 BIOS 引导文件 isolinux/isolinux.bin，拒绝生成不可启动 ISO"
        return 1
    fi
}
# ======================== 主流程 ========================
main() {
    echo -e "${GREEN}"
    echo "  ╔══════════════════════════════════════════╗"
    echo "  ║     Ming OS ${MING_OS_VERSION} Home Edition         ║"
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
    echo "  ║   Ming OS 构建完成！                     ║"
    echo "  ║   耗时: ${minutes}分${seconds}秒                            ║"
    echo "  ╚══════════════════════════════════════════╝"
    echo -e "${NC}"
}
main "$@"
