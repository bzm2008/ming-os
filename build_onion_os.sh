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
readonly MING_OS_VERSION="26.3.0"
readonly MING_OS_BUILD_SUFFIX="r4"
readonly MING_OS_EDITION="Home"
readonly MING_OS_CODENAME="ming"
readonly ISO_VOLUME_ID="MING_OS_2630"
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
readonly MING_USER_PASS="user"
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
        MING_USER_PASS="${MING_USER_PASS}" \
        ROOT_PASS="${ROOT_PASS}" \
        "$@"
}
# 将模块脚本和配置文件复制到 chroot 中
prepare_chroot_scripts() {
    log_info "准备 chroot 内执行环境"
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/modules"
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/config"
    cp -r "${MODULES_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/modules/"
    cp -r "${CONFIG_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/config/"
    chmod +x "${CHROOT_DIR}/tmp/ming-build/modules/"*.sh
    # 复制 assets（含 AI 生成壁纸 PNG）
    if [[ -d "${SCRIPT_DIR}/assets" ]]; then
        mkdir -p "${CHROOT_DIR}/tmp/ming-build/assets"
        cp -r "${SCRIPT_DIR}/assets/"* "${CHROOT_DIR}/tmp/ming-build/assets/" 2>/dev/null || true
    fi
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
    chroot_exec bash -c "update-initramfs -c -k all"
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

    if dd if="${kernel_path}" bs=1 count=4096 2>/dev/null | tr -d '\000' | head -c 1 | grep -q .; then
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
for phase in settings.get("sequence", []) or []:
    if isinstance(phase, dict) and "exec" in phase:
        exec_steps = phase.get("exec") or []
        break
expected_steps = [
    "partition", "mount", "unpackfs", "machineid", "fstab", "locale",
    "keyboard", "localecfg", "users", "displaymanager", "networkcfg",
    "hwclock", "initramfs", "grubcfg", "bootloader", "shellprocess@ming-identity",
    "umount",
]
for step in expected_steps:
    if step not in exec_steps:
        errors.append(f"settings.conf exec sequence missing {step}")
blocked_debian_steps = {
    "luksbootkeyfile", "dpkg-unsafe-io", "sources-media", "services-systemd",
    "bootloader-config", "packages", "plymouthcfg", "initramfscfg",
    "dpkg-unsafe-io-undo", "sources-media-unmount", "sources-final",
}
for step in blocked_debian_steps.intersection(exec_steps):
    errors.append(f"settings.conf still contains Debian installer step {step}")

instances = settings.get("instances") or []
if not any(isinstance(item, dict) and item.get("id") == "ming-identity" for item in instances):
    errors.append("settings.conf missing ming-identity instance")

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

users = load_yaml("etc/calamares/modules/users.conf")
if users.get("allowWeakPasswords") is not True:
    errors.append("users.conf must allow weak passwords to avoid pwquality dictionary install blockers")
requirements = users.get("passwordRequirements") or {}
libpwquality = requirements.get("libpwquality") or []
libpwquality_text = "\n".join(str(item) for item in libpwquality)
if "dictcheck=0" not in libpwquality_text or "enforcing=0" not in libpwquality_text:
    errors.append("users.conf must disable libpwquality dictionary enforcement")

for relative_path in [
    "usr/local/sbin/ming-calamares-preflight",
    "usr/local/bin/ming-calamares-launcher",
    "usr/local/bin/ming-live-installer.sh",
    "usr/local/bin/ming-installer-session",
]:
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"{relative_path} missing or empty")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if relative_path.endswith("ming-calamares-preflight") and "Asia/Shanghai" not in text:
            errors.append(f"{relative_path} missing Asia/Shanghai runtime enforcement")
        if relative_path.endswith("ming-calamares-preflight"):
            if "/run/ming-installer/filesystem.squashfs" not in text:
                errors.append(f"{relative_path} missing stable unpackfs runtime source")
            if "ln -s" not in text and "mount --bind" not in text:
                errors.append(f"{relative_path} must create a stable unpackfs source before Calamares starts")
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

validate_r4_compatibility() {
    log_info "Validating Ming OS r4 legacy hardware and Settings Hub integration..."
    python3 - "${CHROOT_DIR}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
errors = []

def require_file(relative_path, marker=None):
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"missing or empty {relative_path}")
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if marker and marker not in text:
        errors.append(f"{relative_path} missing marker {marker!r}")
    return text

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

for helper in [
    "usr/local/bin/ming-network-repair",
    "usr/local/bin/ming-driver-diagnose",
    "usr/local/bin/ming-diagnostic-bundle",
    "usr/local/bin/ming-surface-support",
    "usr/local/bin/ming-classic-mode",
]:
    require_file(helper)

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

pwquality = require_file("etc/security/pwquality.conf", "dictcheck = 0")
if "minlen = 1" not in pwquality and "minlen=1" not in pwquality:
    errors.append("pwquality.conf must keep installer password policy lenient")

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

search --no-floppy --label ${ISO_VOLUME_ID} --set=root
search --no-floppy --file --set=root /live/vmlinuz
set prefix=(\$root)/boot/grub

loadfont /boot/grub/fonts/unicode.pf2
terminal_output gfxterm

set color_normal=white/black
set color_highlight=black/light-gray
set menu_color_normal=white/black
set menu_color_highlight=black/white
set gfxmode=auto

# Ming OS is an installer-only image: the boot menu offers a single "安装 Ming OS"
# entry plus a safe-graphics fallback for old hardware. The ming.installer=1 flag
# tells the booted session to launch Calamares directly instead of a live desktop.
menuentry "安装 Ming OS ${MING_OS_VERSION}  (Install Ming OS)" {
    linux /live/vmlinuz boot=live components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 splash ming.installer=1 install
    initrd /live/initrd
}

menuentry "安装 Ming OS ${MING_OS_VERSION}  (安全显卡模式 / Safe Graphics)" {
    linux /live/vmlinuz boot=live components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog ming.installer=1 install nomodeset vga=791
    initrd /live/initrd
}

menuentry "Ming OS ${MING_OS_VERSION} 老电脑兼容模式 (1-3代酷睿 / E3 V1-V2)" {
    linux /live/vmlinuz boot=live components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 install nomodeset i915.modeset=0 nouveau.modeset=0 radeon.modeset=0 pcie_aspm=off acpi_osi=Linux pci=nomsi
    initrd /live/initrd
}

menuentry "内存检测 Memory Test (Memtest86+)" {
    linux /boot/memtest86+x64.efi
}
GRUBCFG
}


build_iso() {
    log_step "构建 ISO 镜像"
    rm -rf "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}" "${OUTPUT_DIR}"
    mkdir -p "${ISO_DIR}/boot/grub"
    mkdir -p "${ISO_DIR}/live"

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
    local iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64-${MING_OS_BUILD_SUFFIX}.iso"

    build_iso_manual "${iso_name}"

    if [[ -f "${OUTPUT_DIR}/${iso_name}" ]]; then
        validate_iso_kernel "${OUTPUT_DIR}/${iso_name}" "${kernel_sha}"
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
            -c boot/grub/boot.cat
            -b boot/grub/i386-pc/eltorito.img
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
        log_error "缺少 BIOS 引导文件 boot/grub/i386-pc/eltorito.img，拒绝生成不可启动 ISO"
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
