#!/usr/bin/env bash
# ============================================================================
# Ming OS 26.4.1 Home Edition - 主构建脚本
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
readonly MING_OS_VERSION="26.4.1"
readonly MING_OS_BUILD_SUFFIX="rc4"
readonly MING_OS_EDITION="Home"
readonly MING_OS_CODENAME="ming"
readonly ISO_VOLUME_ID="MING_OS_2641"
readonly DEBIAN_MIRROR="${MING_DEBIAN_MIRROR:-https://deb.debian.org/debian/}"
readonly DEBIAN_SECURITY_MIRROR="${MING_DEBIAN_SECURITY_MIRROR:-https://security.debian.org/debian-security}"
readonly DEBIAN_SUITE="trixie"
readonly DEBIAN_ARCHIVE_KEYRING="${MING_DEBIAN_ARCHIVE_KEYRING:-/usr/share/keyrings/debian-archive-keyring.gpg}"
readonly SPARK_ARCHIVE_KEYRING="/etc/ming-os/store/spark-archive-keyring.gpg"
readonly ARCH="amd64"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LINUX_WORKDIR="/var/tmp/ming-os-build"
readonly APT_ARCHIVES_CACHE="${MING_APT_ARCHIVES_CACHE:-${LINUX_WORKDIR}/apt-archives}"
readonly CHROOT_CACHE_DIR="${MING_CHROOT_CACHE_DIR:-${LINUX_WORKDIR}/chroot-cache}"
readonly BUILD_STATE_ROOT="${MING_BUILD_STATE_ROOT:-${LINUX_WORKDIR}/build-state}"
BUILD_STATE_DIR="${MING_BUILD_STATE_DIR:-${BUILD_STATE_ROOT}/${MING_BUILD_PROFILE:-release}}"
readonly BUILD_STATE_HELPER="${SCRIPT_DIR}/scripts/ming_build_state.py"
readonly BUILD_LOCK_PATH="${LINUX_WORKDIR}/build.lock"
readonly CHROOT_DIR="${LINUX_WORKDIR}/chroot"
readonly OUTPUT_ROOT="${MING_OUTPUT_ROOT:-${LINUX_WORKDIR}/output}"
OUTPUT_DIR="${MING_OUTPUT_DIR:-${OUTPUT_ROOT}/${MING_BUILD_PROFILE:-release}}"
readonly CHECKPOINT_DIR="${LINUX_WORKDIR}/checkpoints"
readonly ISO_DIR="${LINUX_WORKDIR}/iso_build"
readonly MODULES_DIR="${SCRIPT_DIR}/modules"
readonly CONFIG_DIR="${SCRIPT_DIR}/config"
readonly MING_USER="user"
readonly MING_USER_PASS="${MING_USER_PASS:-}"
readonly ROOT_PASS="${ROOT_PASS:-}"
readonly MING_SKIP_XIAHAI="${MING_SKIP_XIAHAI:-0}"
readonly MING_OTA_RELEASE_PUBLIC_KEY_SOURCE="${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE:-}"
readonly MING_REUSE_CHROOT="${MING_REUSE_CHROOT:-0}"
readonly MING_CLEAN_ISO_WORKDIR="${MING_CLEAN_ISO_WORKDIR:-0}"
readonly PROFILE_RELEASE="release"
readonly PROFILE_FAST_TEST="fast-test"
readonly PROFILE_LEGACY_LOWRAM="legacy-lowram"
readonly PROFILE_COMPAT_HWE="compat-hwe"
export MING_SKIP_XIAHAI
BUILD_SOURCE_COMMIT=""
BUILD_TIME_UTC=""
BUILD_ID=""
XIAHAI_ASSET_SHA256=""
MING_BUILD_PROFILE="${MING_BUILD_PROFILE:-release}"
MING_BUILD_RESUME=0
MING_BUILD_FRESH=0
MING_BUILD_FROM=""
MING_BUILD_COMPRESSION="xz"
MING_BUILD_INPUT_HASH=""
MING_BUILD_LOCK_FD=""
CURRENT_STAGE=""
CURRENT_COMMAND=""
CURRENT_STAGE_LINE=0
declare -a MING_SQUASHFS_ARGS=()
declare -a STAGE_ARTIFACTS=()
declare -a BUILD_STAGES=(
    host-preflight
    debootstrap
    prepare-chroot
    modules
    initramfs
    clean-rootfs
    squashfs
    boot-assets
    iso
    publish-artifacts
)
declare -a GIT_COMMAND=()
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

print_build_usage() {
    cat <<'USAGE'
Usage: sudo ./build_onion_os.sh [options]

  --fresh                 discard safe checkpoints and rebuild from debootstrap
  --resume                continue a build with matching source and profile inputs
  --from STAGE            rerun STAGE and all later stages
  --profile PROFILE       release | fast-test | legacy-lowram | compat-hwe
  --help                  show this help

Profiles: release:xz fast-test:zstd legacy-lowram:zstd compat-hwe:xz

Environment:
  MING_CLEAN_ISO_WORKDIR=1  remove the resumable ISO work directory after success
USAGE
}

parse_build_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --fresh)
                MING_BUILD_FRESH=1
                ;;
            --resume)
                MING_BUILD_RESUME=1
                ;;
            --from)
                [[ $# -ge 2 ]] || { log_error "--from requires a stage"; return 2; }
                MING_BUILD_FROM="$2"
                MING_BUILD_RESUME=1
                shift
                ;;
            --profile)
                [[ $# -ge 2 ]] || { log_error "--profile requires a value"; return 2; }
                MING_BUILD_PROFILE="$2"
                shift
                ;;
            --help|-h)
                print_build_usage
                exit 0
                ;;
            *)
                log_error "unknown build option: $1"
                print_build_usage >&2
                return 2
                ;;
        esac
        shift
    done
    if [[ "${MING_BUILD_FRESH}" == "1" && "${MING_BUILD_RESUME}" == "1" ]]; then
        log_error "--fresh and --resume cannot be used together"
        return 2
    fi
    # Compatibility for older automation. Reuse is now checkpoint-validated.
    if [[ "${MING_REUSE_CHROOT}" == "1" ]]; then
        log_warn "MING_REUSE_CHROOT is deprecated; resume is checkpoint validated"
        MING_BUILD_RESUME=1
        MING_BUILD_FROM="${MING_BUILD_FROM:-modules}"
    fi
    if [[ "${MING_BUILD_FRESH}" == "0" && "${MING_BUILD_RESUME}" == "0" ]]; then
        MING_BUILD_FRESH=1
    fi
}

configure_build_profile() {
    case "${MING_BUILD_PROFILE}" in
        "${PROFILE_RELEASE}")
            if [[ "${MING_SKIP_XIAHAI}" == "1" ]]; then
                log_error "release profile requires the verified Xiahai Xiaoming asset; use fast-test for an internal build without it"
                return 2
            fi
            if [[ -z "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ||
                  ! -s "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ||
                  -L "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ]]; then
                log_error "release profile requires MING_OTA_RELEASE_PUBLIC_KEY_SOURCE pointing to a verified OTA Minisign public key"
                return 2
            fi
            MING_BUILD_COMPRESSION="xz"
            MING_SQUASHFS_ARGS=(-comp xz -Xbcj x86 -b 1M -no-xattrs -no-progress)
            ;;
        "${PROFILE_FAST_TEST}")
            MING_BUILD_COMPRESSION="zstd"
            MING_SQUASHFS_ARGS=(-comp zstd -Xcompression-level 6 -b 1M -no-xattrs -no-progress)
            ;;
        "${PROFILE_LEGACY_LOWRAM}")
            MING_BUILD_COMPRESSION="zstd"
            MING_SQUASHFS_ARGS=(-comp zstd -Xcompression-level 10 -b 1M -no-xattrs -no-progress)
            ;;
        "${PROFILE_COMPAT_HWE}")
            MING_BUILD_COMPRESSION="xz"
            MING_SQUASHFS_ARGS=(-comp xz -Xbcj x86 -b 1M -no-xattrs -no-progress)
            ;;
        *)
            log_error "unknown build profile: ${MING_BUILD_PROFILE}"
            return 2
            ;;
    esac
    OUTPUT_DIR="${MING_OUTPUT_DIR:-${OUTPUT_ROOT}/${MING_BUILD_PROFILE}}"
    BUILD_STATE_DIR="${MING_BUILD_STATE_DIR:-${BUILD_STATE_ROOT}/${MING_BUILD_PROFILE}}"
}

acquire_build_lock() {
    mkdir -p "${LINUX_WORKDIR}"
    require_cmd flock "apt install util-linux"
    exec {MING_BUILD_LOCK_FD}>"${BUILD_LOCK_PATH}"
    if ! flock -n "${MING_BUILD_LOCK_FD}"; then
        log_error "another Ming OS build is already using ${LINUX_WORKDIR}"
        return 1
    fi
}

source_tree_sha256() {
    git_build ls-tree -r --full-tree HEAD | sha256sum | awk '{print $1}'
}

build_inputs_sha256() {
    {
        printf '%s\0' "${MING_OS_BUILD_SUFFIX}" "${ISO_VOLUME_ID}" \
            "${MING_SKIP_XIAHAI}" "${MING_BUILD_PROFILE}"
        while IFS= read -r -d '' input_file; do
            sha256sum "${input_file}"
        done < <(find "${MODULES_DIR}" "${CONFIG_DIR}" "${SCRIPT_DIR}/assets" \
            -type f -print0 | sort -z)
        printf 'xiahai-source=%s\0' \
            "$(file_sha256_or_missing "${MING_XIAHAI_DEB_SOURCE:-${SCRIPT_DIR}/assets/vendor/xiahai-xiaoming/xiahai-xiaoming_0.0.2-beta_amd64.deb}")"
        printf 'ota-release-key=%s\0' \
            "$(file_sha256_or_missing "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}")"
        sha256sum "${SCRIPT_DIR}/build_onion_os.sh" \
            "${SCRIPT_DIR}/resume_build.sh" "${BUILD_STATE_HELPER}"
    } | sha256sum | awk '{print $1}'
}

file_sha256_or_missing() {
    local path="$1"
    if [[ -s "${path}" ]]; then
        sha256sum "${path}" | awk '{print $1}'
    else
        printf 'missing'
    fi
}

tools_fingerprint() {
    local tool
    for tool in debootstrap mksquashfs xorriso grub-mkimage; do
        printf '%s=' "${tool}"
        command -v "${tool}" >/dev/null 2>&1 \
            && "${tool}" --version 2>&1 | head -n 1 \
            || printf 'missing\n'
    done | sha256sum | awk '{print $1}'
}

initialize_build_state() {
    require_cmd python3 "apt install python3"
    [[ -f "${BUILD_STATE_HELPER}" ]] || {
        log_error "missing build state helper: ${BUILD_STATE_HELPER}"
        return 1
    }
    local state_args=(
        init
        --state-dir "${BUILD_STATE_DIR}"
        --build-id "${BUILD_ID}"
        --build-time-utc "${BUILD_TIME_UTC}"
        --version "${MING_OS_VERSION}"
        --source-commit "${BUILD_SOURCE_COMMIT}"
        --source-tree-sha256 "$(source_tree_sha256)"
        --modules-sha256 "$(build_inputs_sha256)"
        --profile "${MING_BUILD_PROFILE}"
        --suite "${DEBIAN_SUITE}"
        --arch "${ARCH}"
        --debian-mirror "${DEBIAN_MIRROR}"
        --security-mirror "${DEBIAN_SECURITY_MIRROR}"
        --squashfs-compression "${MING_BUILD_COMPRESSION}"
        --build-suffix "${MING_OS_BUILD_SUFFIX}"
        --iso-volume-id "${ISO_VOLUME_ID}"
        --skip-xiahai "${MING_SKIP_XIAHAI}"
        --xiahai-sha256 "$(file_sha256_or_missing "${MING_XIAHAI_DEB_SOURCE:-${SCRIPT_DIR}/assets/vendor/xiahai-xiaoming/xiahai-xiaoming_0.0.2-beta_amd64.deb}")"
        --keyring-sha256 "$(file_sha256_or_missing "${DEBIAN_ARCHIVE_KEYRING}")"
        --apt-snapshot-sha256 "pending"
        --tools-fingerprint "$(tools_fingerprint)"
    )
    [[ "${MING_BUILD_FRESH}" == "1" ]] && state_args+=(--fresh)
    [[ "${MING_BUILD_RESUME}" == "1" ]] && state_args+=(--resume)
    [[ -n "${MING_BUILD_FROM}" ]] && state_args+=(--from "${MING_BUILD_FROM}")

    local payload
    payload="$(python3 "${BUILD_STATE_HELPER}" "${state_args[@]}")"
    read -r MING_BUILD_INPUT_HASH BUILD_ID BUILD_TIME_UTC < <(
        python3 -c 'import json,sys; p=json.load(sys.stdin); print(p["input_hash"], p["build_id"], p["build_time_utc"])' \
            <<<"${payload}"
    )
    export BUILD_ID BUILD_TIME_UTC MING_BUILD_INPUT_HASH
}

state_is_complete() {
    python3 "${BUILD_STATE_HELPER}" is-complete \
        --state-dir "${BUILD_STATE_DIR}" --stage "$1" \
        --input-hash "${MING_BUILD_INPUT_HASH}"
}

state_mark_started() {
    python3 "${BUILD_STATE_HELPER}" start \
        --state-dir "${BUILD_STATE_DIR}" --stage "$1" \
        --input-hash "${MING_BUILD_INPUT_HASH}" >/dev/null
}

state_mark_completed() {
    local stage="$1"
    shift
    local args=()
    local artifact
    for artifact in "$@"; do
        [[ -n "${artifact}" ]] && args+=(--artifact "${artifact}")
    done
    python3 "${BUILD_STATE_HELPER}" complete \
        --state-dir "${BUILD_STATE_DIR}" --stage "${stage}" \
        --input-hash "${MING_BUILD_INPUT_HASH}" "${args[@]}" >/dev/null
}

invalidate_from() {
    python3 "${BUILD_STATE_HELPER}" invalidate-from \
        --state-dir "${BUILD_STATE_DIR}" --stage "$1" >/dev/null
}

invalidate_successors() {
    local stage="$1"
    local candidate found=0
    for candidate in "${BUILD_STAGES[@]}"; do
        if [[ "${found}" == "1" ]]; then
            invalidate_from "${candidate}"
        elif [[ "${candidate}" == "${stage}" ]]; then
            found=1
        fi
    done
}

record_build_failure() {
    local stage="$1" exit_code="$2" action="$3" line="$4"
    [[ -n "${MING_BUILD_INPUT_HASH}" && -n "${stage}" ]] || return 0
    # The helper writes ${BUILD_STATE_DIR}/last-failure.json with a redacted resume_hint.
    python3 "${BUILD_STATE_HELPER}" fail \
        --state-dir "${BUILD_STATE_DIR}" --stage "${stage}" \
        --input-hash "${MING_BUILD_INPUT_HASH}" --exit-code "${exit_code}" \
        --command "${action}" --line "${line}" >/dev/null 2>&1 || true
}

build_error_trap() {
    local exit_code=$?
    local line="${BASH_LINENO[0]:-${CURRENT_STAGE_LINE:-0}}"
    local failed_command="${CURRENT_COMMAND:-${BASH_COMMAND:-unknown}}"
    local pipeline_status="${PIPESTATUS[*]:-}"
    CURRENT_COMMAND="${failed_command} pipeline=${pipeline_status}"
    record_build_failure "${CURRENT_STAGE}" "${exit_code}" "${CURRENT_COMMAND}" "${line}"
    umount_chroot >/dev/null 2>&1 || true
    exit "${exit_code}"
}

run_stage() {
    local stage="$1"
    shift
    if [[ "${stage}" != "host-preflight" ]] && state_is_complete "${stage}"; then
        log_info "checkpoint valid; skipping completed stage: ${stage}"
        return 0
    fi
    if [[ "${stage}" != "host-preflight" ]]; then
        invalidate_successors "${stage}"
    fi
    CURRENT_STAGE="${stage}"
    CURRENT_COMMAND="$*"
    CURRENT_STAGE_LINE="${LINENO}"
    state_mark_started "${stage}"
    "$@"
    state_mark_completed "${stage}" "${STAGE_ARTIFACTS[@]}"
    STAGE_ARTIFACTS=()
    CURRENT_COMMAND=""
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
resolve_git_invocation() {
    local git_file raw_gitdir drive rest drive_lower linux_gitdir
    GIT_COMMAND=(git -c core.filemode=false -C "${SCRIPT_DIR}")
    git_file="${SCRIPT_DIR}/.git"
    if [[ ! -f "${git_file}" ]]; then
        return 0
    fi
    raw_gitdir="$(sed -n 's/^gitdir: //p' "${git_file}" | head -n 1 | tr -d '\r')"
    if [[ "${raw_gitdir}" =~ ^([A-Za-z]):/(.*)$ ]]; then
        drive="${BASH_REMATCH[1]}"
        rest="${BASH_REMATCH[2]}"
        drive_lower="$(printf '%s' "${drive}" | tr '[:upper:]' '[:lower:]')"
        linux_gitdir="/mnt/${drive_lower}/${rest}"
        if [[ -d "${linux_gitdir}" ]]; then
            GIT_COMMAND=(git -c core.filemode=false "--git-dir=${linux_gitdir}" "--work-tree=${SCRIPT_DIR}")
        fi
    fi
}
git_build() {
    if [[ ${#GIT_COMMAND[@]} -eq 0 ]]; then
        resolve_git_invocation
    fi
    "${GIT_COMMAND[@]}" "$@"
}
assert_clean_source_tree() {
    local untracked
    if ! git_build diff --ignore-cr-at-eol --quiet -- .; then
        log_error "构建要求干净工作树；请先提交本次 RC3 源码与测试。"
        return 1
    fi
    if ! git_build diff --cached --ignore-cr-at-eol --quiet -- .; then
        log_error "构建要求没有暂存但未提交的源码变化。"
        return 1
    fi
    untracked="$(git_build ls-files --others --exclude-standard)"
    if [[ -n "${untracked}" ]]; then
        log_error "构建要求没有未跟踪源码文件；请提交或加入忽略清单。"
        printf '%s\n' "${untracked}" >&2
        return 1
    fi
}
capture_build_identity() {
    require_cmd git "apt install git"
    resolve_git_invocation
    assert_clean_source_tree
    BUILD_SOURCE_COMMIT="$(git_build rev-parse HEAD)"
    if ! git_build cat-file -e "${BUILD_SOURCE_COMMIT}^{commit}"; then
        log_error "当前源码提交无法解析为 Git commit 对象，拒绝构建。"
        return 1
    fi
    BUILD_TIME_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    BUILD_ID="2641-rc4-${BUILD_SOURCE_COMMIT:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
    export BUILD_SOURCE_COMMIT BUILD_TIME_UTC BUILD_ID
}

verify_build_identity() {
    local current_commit
    current_commit="$(git_build rev-parse HEAD)"
    if ! git_build cat-file -e "${BUILD_SOURCE_COMMIT}^{commit}"; then
        log_error "构建记录的源码提交对象已不可解析，拒绝继续。"
        return 1
    fi
    if [[ "${current_commit}" != "${BUILD_SOURCE_COMMIT}" ]]; then
        log_error "源码在构建期间发生变化，拒绝生成无法追溯的 ISO。"
        return 1
    fi
    assert_clean_source_tree || {
        log_error "源码在构建期间发生变化，拒绝生成无法追溯的 ISO。"
        return 1
    }
}

write_rootfs_build_identity() {
    local source_digest
    source_digest="$(git_build ls-tree -r --full-tree HEAD \
        | sha256sum | awk '{print $1}')"
    install -d -m 0755 "${CHROOT_DIR}/etc"
    python3 - "${CHROOT_DIR}/etc/ming-os-build.json" \
        "${MING_OS_VERSION}" "${BUILD_ID}" "${BUILD_SOURCE_COMMIT}" \
        "${BUILD_TIME_UTC}" "${source_digest:0:16}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = {
    "version": sys.argv[2], "build_id": sys.argv[3],
    "source_commit": sys.argv[4], "build_time_utc": sys.argv[5],
    "source_tree_sha256_prefix": sys.argv[6], "iso_sha256": None,
}
path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n", encoding="ascii")
PY
    chmod 0644 "${CHROOT_DIR}/etc/ming-os-build.json"
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
    if [[ ${#missing_bins[@]} -eq 0 && -s "${DEBIAN_ARCHIVE_KEYRING}" ]]; then
        log_info "构建依赖已存在，跳过在线安装"
        return 0
    fi
    log_warn "缺少构建依赖: ${missing_bins[*]}"
    if command -v apt-get &>/dev/null; then
        local apt_ok=0
        local apt_options=(
            -o Acquire::Retries=5
            -o Acquire::http::Timeout=15
            -o Acquire::https::Timeout=15
            -o Acquire::http::Pipeline-Depth=0
            -o Acquire::Queue-Mode=access
        )
        if [[ "${MING_SKIP_APT_UPDATE:-0}" != "1" ]] \
            && DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none \
                apt-get "${apt_options[@]}" update; then
            apt_ok=1
        else
            log_warn "apt-get update 失败，改用已有缓存继续安装"
        fi
        if ! DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none \
            apt-get "${apt_options[@]}" install -y --no-install-recommends \
            debootstrap squashfs-tools xorriso isolinux syslinux-common \
            grub-pc-bin grub-efi-amd64-bin grub-efi-amd64-signed shim-signed \
            mtools dosfstools debian-archive-keyring; then
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

verify_debootstrap_keyring() {
    if [[ ! -s "${DEBIAN_ARCHIVE_KEYRING}" ]]; then
        log_error "缺少 Debian archive keyring，拒绝执行无法验证 Release 签名的 debootstrap。"
        log_error "需要安装 debian-archive-keyring，或设置 MING_DEBIAN_ARCHIVE_KEYRING。"
        return 1
    fi
    log_info "Debian Release keyring 已就绪: ${DEBIAN_ARCHIVE_KEYRING}"
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
    mkdir -p "${APT_ARCHIVES_CACHE}"
    validate_apt_cache_manifest
    debootstrap \
        --cache-dir="${APT_ARCHIVES_CACHE}" \
        --arch="${ARCH}" \
        --variant=minbase \
        --keyring="${DEBIAN_ARCHIVE_KEYRING}" \
        --include=ca-certificates,gnupg2,apt-transport-https \
        "${DEBIAN_SUITE}" \
        "${CHROOT_DIR}" \
        "${DEBIAN_MIRROR}"
    write_apt_cache_manifest
    log_info "debootstrap 完成"
}

validate_apt_cache_manifest() {
    local manifest="${APT_ARCHIVES_CACHE}/cache-manifest.json"
    if [[ ! -s "${manifest}" ]]; then
        return 0
    fi
    if ! python3 - "${manifest}" "${DEBIAN_SUITE}" "${ARCH}" \
        "${DEBIAN_MIRROR}" "${DEBIAN_SECURITY_MIRROR}" \
        "$(file_sha256_or_missing "${DEBIAN_ARCHIVE_KEYRING}")" <<'PY'
import json
import pathlib
import sys

try:
    payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(
    0
    if (
        payload.get("suite") == sys.argv[2]
        and payload.get("arch") == sys.argv[3]
        and payload.get("mirror") == sys.argv[4]
        and payload.get("security_mirror") == sys.argv[5]
        and payload.get("keyring_sha256") == sys.argv[6]
    )
    else 1
)
PY
    then
        log_warn "APT cache invalid: cache identity mismatch; discarding cached package archives and partial downloads"
        # A .deb cached for another suite, architecture or mirror must never
        # be reused merely because its filename still matches an apt request.
        # Keep the directory itself so debootstrap can repopulate it atomically.
        find "${APT_ARCHIVES_CACHE}" -type f \
            \( -name '*.deb' -o -name '*.deb.*' \) -delete
        rm -rf "${APT_ARCHIVES_CACHE}/partial"
    fi
}

write_apt_cache_manifest() {
    mkdir -p "${APT_ARCHIVES_CACHE}"
    local manifest="${APT_ARCHIVES_CACHE}/cache-manifest.json"
    local partial="${APT_ARCHIVES_CACHE}/cache-manifest.json.partial"
    rm -f "${partial}"
    python3 - "${partial}" "${DEBIAN_SUITE}" "${ARCH}" \
        "${DEBIAN_MIRROR}" "${DEBIAN_SECURITY_MIRROR}" \
        "$(file_sha256_or_missing "${DEBIAN_ARCHIVE_KEYRING}")" <<'PY'
import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "suite": sys.argv[2],
            "arch": sys.argv[3],
            "mirror": sys.argv[4],
            "security_mirror": sys.argv[5],
            "keyring_sha256": sys.argv[6],
            "cache_schema": 2,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    + "\n",
    encoding="ascii",
)
PY
    mv -f "${partial}" "${manifest}"
}

write_stage_marker() {
    local stage="$1"
    mkdir -p "${CHECKPOINT_DIR}/${MING_BUILD_PROFILE}"
    printf 'stage=%s\ninput_hash=%s\nupdated_at=%s\n' \
        "${stage}" "${MING_BUILD_INPUT_HASH}" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "${CHECKPOINT_DIR}/${MING_BUILD_PROFILE}/${stage}.marker.partial"
    mv -f "${CHECKPOINT_DIR}/${MING_BUILD_PROFILE}/${stage}.marker.partial" \
        "${CHECKPOINT_DIR}/${MING_BUILD_PROFILE}/${stage}.marker"
}

stage_marker_path() {
    printf '%s/%s/%s.marker' "${CHECKPOINT_DIR}" "${MING_BUILD_PROFILE}" "$1"
}
# ======================== chroot 环境管理 ========================
mount_chroot() {
    log_info "挂载 chroot 必要文件系统"
    mkdir -p "${CHROOT_CACHE_DIR}/apt-archives/partial"
    mkdir -p "${CHROOT_DIR}/var/cache/apt/archives"
    if ! mountpoint -q "${CHROOT_DIR}/var/cache/apt/archives" 2>/dev/null; then
        mount --bind "${CHROOT_CACHE_DIR}/apt-archives" \
            "${CHROOT_DIR}/var/cache/apt/archives"
    fi
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
    local mounts=("var/cache/apt/archives" "dev/shm" "dev/pts" "run" "sys" "proc" "dev")
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
        MING_SKIP_XIAHAI="${MING_SKIP_XIAHAI}" \
        MING_DEBIAN_MIRROR="${DEBIAN_MIRROR}" \
        MING_DEBIAN_SECURITY_MIRROR="${DEBIAN_SECURITY_MIRROR}" \
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
    if [[ ! -s "${SCRIPT_DIR}/assets/wallpaper-ming-2640-abstract.png" ]]; then
        log_error "missing required build asset: assets/wallpaper-ming-2640-abstract.png"
        return 1
    fi
    if [[ ! -s "${SCRIPT_DIR}/assets/ming-installer-verify.py" ]]; then
        log_error "missing required build asset: assets/ming-installer-verify.py"
        return 1
    fi
    if [[ ! -s "${SCRIPT_DIR}/assets/ming-detect-other-os" ]]; then
        log_error "missing required build asset: assets/ming-detect-other-os"
        return 1
    fi
    if [[ ! -s "${SCRIPT_DIR}/assets/ming-os-logo.png" ]]; then
        log_error "missing required build asset: assets/ming-os-logo.png"
        return 1
    fi
    # Xiahai is a user-supplied binary input.  Copy it into the build asset
    # tree only when explicitly provided, then let dpkg-deb validate it inside
    # the rootfs.  A corrupt attachment must stop before any ISO is produced.
    local xiahai_asset_path="${SCRIPT_DIR}/assets/vendor/xiahai-xiaoming/xiahai-xiaoming_0.0.2-beta_amd64.deb"
    local xiahai_asset="${MING_XIAHAI_DEB_SOURCE:-${xiahai_asset_path}}"
    if [[ ! -s "${xiahai_asset}" || -L "${xiahai_asset}" ]]; then
        if [[ "${MING_SKIP_XIAHAI}" == "1" ]]; then
            XIAHAI_ASSET_SHA256="missing"
        else
            log_error "Xiahai Xiaoming build input is missing or is a symlink: ${xiahai_asset}"
            return 1
        fi
    fi
    if [[ "${MING_SKIP_XIAHAI}" == "1" ]]; then
        log_warn "MING_SKIP_XIAHAI=1: omitting Xiahai Xiaoming from this internal RC build"
        install -d -m 0755 "${CHROOT_DIR}/etc/ming-os"
        : > "${CHROOT_DIR}/etc/ming-os/skip-xiahai"
        XIAHAI_ASSET_SHA256="missing"
    else
        rm -f "${CHROOT_DIR}/etc/ming-os/skip-xiahai"
    fi
    if [[ "${MING_SKIP_XIAHAI}" != "1" ]]; then
        if ! command -v dpkg-deb >/dev/null 2>&1; then
            log_error "dpkg-deb is required to validate the Xiahai build asset"
            return 1
        fi
        if ! dpkg-deb --info "${xiahai_asset}" >/dev/null 2>&1 \
            || ! dpkg-deb --contents "${xiahai_asset}" >/dev/null 2>&1; then
            log_error "Xiahai Xiaoming package is corrupt or incomplete; refusing to start the build"
            return 1
        fi
        if [[ "$(dpkg-deb -f "${xiahai_asset}" Package 2>/dev/null || true)" != "xiahai-xiaoming" \
            || "$(dpkg-deb -f "${xiahai_asset}" Version 2>/dev/null || true)" != "0.0.2~beta" \
            || "$(dpkg-deb -f "${xiahai_asset}" Architecture 2>/dev/null || true)" != "amd64" ]]; then
            log_error "Xiahai Xiaoming package metadata does not match RC4 requirements"
            return 1
        fi
        local xiahai_sha
        xiahai_sha="$(sha256sum "${xiahai_asset}" | awk '{print toupper($1)}')"
        if [[ "${xiahai_sha}" != "F3D9612F19D53DB6F4E96E62982060C283D8A2707FEC05429F3074C15E825244" ]]; then
            log_error "Xiahai Xiaoming package SHA256 is not the approved repaired asset"
            return 1
        fi
        XIAHAI_ASSET_SHA256="${xiahai_sha}"
    fi
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/modules"
    mkdir -p "${CHROOT_DIR}/tmp/ming-build/config"
    cp -r "${MODULES_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/modules/"
    cp -r "${CONFIG_DIR}"/* "${CHROOT_DIR}/tmp/ming-build/config/"
    chmod +x "${CHROOT_DIR}/tmp/ming-build/modules/"*.sh
    if [[ -d "${SCRIPT_DIR}/assets" ]]; then
        mkdir -p "${CHROOT_DIR}/tmp/ming-build/assets"
        cp -r "${SCRIPT_DIR}/assets/"* "${CHROOT_DIR}/tmp/ming-build/assets/" 2>/dev/null || true
    fi
    if [[ -n "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ]]; then
        if [[ ! -s "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ||
              -L "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" ]]; then
            log_error "OTA Minisign public key source is missing or a symlink"
            return 1
        fi
        if ! grep -Eq '^RW[A-Za-z0-9+/=]{40,}$' "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}"; then
            log_error "OTA Minisign public key source has an invalid format"
            return 1
        fi
        install -m 0644 "${MING_OTA_RELEASE_PUBLIC_KEY_SOURCE}" \
            "${CHROOT_DIR}/tmp/ming-build/assets/ota-release.minisign.pub"
    fi
    if [[ "${MING_SKIP_XIAHAI}" != "1" && "${xiahai_asset}" != "${xiahai_asset_path}" ]]; then
        install -d -m 0755 "${CHROOT_DIR}/tmp/ming-build/assets/vendor/xiahai-xiaoming"
        install -m 0644 "${xiahai_asset}" \
            "${CHROOT_DIR}/tmp/ming-build/assets/vendor/xiahai-xiaoming/xiahai-xiaoming_0.0.2-beta_amd64.deb"
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
    -o Acquire::Retries=5 \
    -o Acquire::ForceIPv4=true \
    -o Acquire::http::Timeout=15 \
    -o Acquire::https::Timeout=15 \
    -o Acquire::http::Pipeline-Depth=0 \
    -o Acquire::Queue-Mode=access \
    -o Acquire::http::No-Cache=true \
    -o Acquire::https::No-Cache=true \
    "$@" </dev/null
APT_BUILD_WRAPPER
    chmod 0755 "${CHROOT_DIR}/usr/local/sbin/apt-build"

    cat > "${CHROOT_DIR}/usr/local/sbin/apt" << 'APT_FRONTEND_WRAPPER'
#!/bin/sh
# Route module-time apt calls through the non-interactive apt-get wrapper.
exec /usr/local/sbin/apt-build "$@"
APT_FRONTEND_WRAPPER
    chmod 0755 "${CHROOT_DIR}/usr/local/sbin/apt"
}
# ======================== 模块脚本执行 ========================
run_modules() {
    log_step "在 chroot 中执行模块脚本"
    local modules=(
        "01_base.sh"
        "02_apps.sh"
        "03_desktop.sh"
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
    # APT archives are bind-mounted from CHROOT_CACHE_DIR. Do not run apt clean
    # while the mount is active, otherwise an interrupted build destroys the
    # host cache it is meant to reuse.
    if mountpoint -q "${CHROOT_DIR}/var/cache/apt/archives" 2>/dev/null; then
        log_info "保留宿主 APT archives cache；只清理 target metadata"
    else
        chroot_exec bash -c "apt clean"
    fi
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
    python3 - "${CHROOT_DIR}" "${SCRIPT_DIR}" <<'PY'
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
source_root = Path(sys.argv[2])
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
    "partition", "shellprocess@ming-fix-partition-types",
    "shellprocess@ming-installer-target-receipt-reset", "mount",
    "ming-installer-target-receipt@ming-installer-target-receipt", "unpackfs", "machineid",
    "fstab", "networkcfg", "hwclock", "initramfs", "grubcfg", "shellprocess@ming-identity",
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
if all(step in exec_steps for step in ["partition", "shellprocess@ming-fix-partition-types", "mount"]):
    if not (exec_steps.index("partition") < exec_steps.index("shellprocess@ming-fix-partition-types") < exec_steps.index("mount")):
        errors.append("partition type normalizer must run after partition and before mount")
receipt_order = [
    "partition",
    "shellprocess@ming-fix-partition-types",
    "shellprocess@ming-installer-target-receipt-reset",
    "mount",
    "ming-installer-target-receipt@ming-installer-target-receipt",
    "unpackfs",
]
if all(step in exec_steps for step in receipt_order):
    if [exec_steps.index(step) for step in receipt_order] != sorted(
        exec_steps.index(step) for step in receipt_order
    ):
        errors.append("fresh receipt reset must run before mount and capture immediately after mount")
if all(step in exec_steps for step in [
    "shellprocess@ming-identity",
    "shellprocess@ming-installed-desktop-gate",
    "shellprocess@ming-bootloader",
]):
    if exec_steps.index("shellprocess@ming-identity") > exec_steps.index("shellprocess@ming-bootloader"):
        errors.append("installed identity and root UUID must be finalized before GRUB installation")
    if exec_steps.index("shellprocess@ming-identity") > exec_steps.index("shellprocess@ming-installed-desktop-gate"):
        errors.append("installed desktop gate must run after identity repair")
    if exec_steps.index("shellprocess@ming-installed-desktop-gate") > exec_steps.index("shellprocess@ming-bootloader"):
        errors.append("installed desktop gate must run before bootloader installation")
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
if not any(isinstance(item, dict) and item.get("id") == "ming-installer-target-receipt" for item in instances):
    errors.append("settings.conf missing ming-installer-target-receipt instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-fix-partition-types" for item in instances):
    errors.append("settings.conf missing ming-fix-partition-types instance")
if not any(isinstance(item, dict) and item.get("id") == "ming-installer-target-receipt-reset" for item in instances):
    errors.append("settings.conf missing ming-installer-target-receipt-reset instance")
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

mount = load_yaml("etc/calamares/modules/mount.conf")
for item in mount.get("extraMounts") or []:
    if not isinstance(item, dict):
        continue
    if item.get("mountPoint") == "/run" or item.get("device") == "/run":
        errors.append("mount.conf must not bind the Live /run into the target before unpackfs")

partition = load_yaml("etc/calamares/modules/partition.conf")
initial_choice = partition.get("initialPartitioningChoice")
if initial_choice not in {"erase", "none"}:
    errors.append("partition.conf initialPartitioningChoice must be erase or none")
if partition.get("allowManualPartitioning") is not False:
    errors.append("partition.conf must disable manual partitioning for the OTA-ready layout")
if partition.get("defaultPartitionTableType") != "gpt" or partition.get("requiredPartitionTableType") != "gpt":
    errors.append("partition.conf blank_ab layout must require GPT")
layout = partition.get("partitionLayout") or []
expected_layout = {
    "MING-BIOSBOOT": ({"unformatted"}, None),
    "MING-ESP": ({"fat32", "vfat"}, "/boot/efi"),
    "MING-BOOT": ({"ext4"}, "/boot"),
    "MING-ROOT-A": ({"ext4"}, "/"),
    "MING-ROOT-B": ({"ext4"}, None),
    "MING-HOME": ({"ext4"}, "/home"),
}
layout_names = [
    item.get("name")
    for item in layout
    if isinstance(item, dict)
]
if (
        "MING-BIOSBOOT" not in layout_names
        or "MING-ESP" not in layout_names
        or "MING-BOOT" not in layout_names
        or layout_names.index("MING-BIOSBOOT") > layout_names.index("MING-ESP")
        or layout_names.index("MING-ESP") > layout_names.index("MING-BOOT")):
    errors.append("partition.conf MING-BIOSBOOT, MING-ESP, and MING-BOOT must be ordered for BIOS+UEFI install")
if partition.get("efiSystemPartition") is not None:
    errors.append("partition.conf must use explicit MING-ESP instead of efiSystemPartition")
for label, (filesystems, mountpoint) in expected_layout.items():
    entries = [item for item in layout if isinstance(item, dict) and item.get("name") == label]
    if len(entries) != 1:
        errors.append(f"partition.conf must create exactly one {label} partition")
        continue
    entry = entries[0]
    if str(entry.get("filesystem", "")).casefold() not in filesystems:
        errors.append(f"partition.conf {label} has the wrong filesystem")
    if entry.get("mountPoint") != mountpoint:
        errors.append(f"partition.conf {label} has the wrong mount point")
    if label == "MING-BIOSBOOT" and (
            str(entry.get("type", "")).casefold() != "21686148-6449-6E6F-744E-656564454649".casefold()
            or str(entry.get("filesystem", "")).casefold() != "unformatted"):
        errors.append("partition.conf MING-BIOSBOOT must be an unformatted BIOS Boot Partition")
    if label == "MING-ESP" and (
            str(entry.get("type", "")).casefold() != "C12A7328-F81F-11D2-BA4B-00A0C93EC93B".casefold()
            or entry.get("mountPoint") != "/boot/efi"
            or str(entry.get("filesystem", "")).casefold() not in {"fat32", "vfat"}):
        errors.append("partition.conf must create explicit MING-ESP FAT EFI partition")
if initial_choice == "erase":
    for required_live_path in (
        "usr/local/sbin/ming-live-installer-root",
        "usr/local/bin/ming-calamares-launcher",
        "usr/local/bin/ming-install-mode-chooser",
        "usr/local/sbin/ming-install-mode",
        "usr/share/polkit-1/actions/org.ming.live.installer.policy",
    ):
        if not (root / required_live_path).is_file():
            errors.append(
                f"blank_ab erase flow requires explicit mode/root helper: {required_live_path}"
            )

partition_type_normalizer_path = root / "usr/local/sbin/ming-fix-partition-types"
if not partition_type_normalizer_path.is_file():
    errors.append("missing usr/local/sbin/ming-fix-partition-types")
    partition_type_normalizer = ""
else:
    if b"\r" in partition_type_normalizer_path.read_bytes():
        errors.append("usr/local/sbin/ming-fix-partition-types must use LF line endings")
    partition_type_normalizer = partition_type_normalizer_path.read_text(encoding="utf-8", errors="replace")
efi_system_partition_guid = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
for marker in ("MING-BIOSBOOT:ef02", "MING-ESP:ef00", "MING-BOOT:8300", "MING-ROOT-A:8300", "MING-ROOT-B:8300", "MING-HOME:8300"):
    if marker not in partition_type_normalizer:
        errors.append(f"ming-fix-partition-types missing {marker}")
if efi_system_partition_guid.casefold() not in partition_type_normalizer.casefold():
    errors.append("partition type normalizer must preserve the EFI System Partition GUID")
for marker in ("find_auto_esp_partition", "claim_auto_esp_as_ming_esp", "Calamares auto ESP", "sgdisk --change-name="):
    if marker in partition_type_normalizer:
        errors.append("partition type normalizer must require explicit MING-ESP")

desktop_gate = load_yaml("etc/calamares/modules/ming-installed-desktop-gate.conf")
if desktop_gate.get("dontChroot") is not True or \
        "/usr/local/sbin/ming-installer-verify installed --receipt" not in (desktop_gate.get("script") or []):
    errors.append("installed desktop gate must use the authoritative target receipt")
receipt_reset = load_yaml("etc/calamares/modules/ming-installer-target-receipt-reset.conf")
if receipt_reset.get("dontChroot") is not True or \
        "/usr/local/sbin/ming-installer-verify receipt --begin-attempt" not in (receipt_reset.get("script") or []):
    errors.append("authoritative target receipt reset must clear stale state before mount")
receipt_module = root / "usr/lib/x86_64-linux-gnu/calamares/modules/ming-installer-target-receipt"
if not (receipt_module / "module.desc").is_file() or not (receipt_module / "main.py").is_file():
    errors.append("authoritative Calamares target receipt module is missing")
else:
    receipt_job = (receipt_module / "main.py").read_text(encoding="utf-8", errors="replace")
    if 'globalstorage.value("rootMountPoint")' not in receipt_job:
        errors.append("receipt module must capture Calamares globalstorage.rootMountPoint")
    if "calamares-root" in receipt_job or "glob(" in receipt_job:
        errors.append("receipt module must not scan candidate target directories")

def source_settings_block(path, opener, marker):
    text = path.read_text(encoding="utf-8", errors="replace")
    if opener not in text:
        errors.append(f"missing generated settings path in {path.name}: {opener}")
        return {}
    block = text.split(opener, 1)[1]
    if marker not in block:
        errors.append(f"unterminated generated settings path in {path.name}: {marker}")
        return {}
    try:
        return yaml.safe_load(block.split(marker, 1)[0]) or {}
    except Exception as exc:
        errors.append(f"generated settings YAML parse failed in {path.name}: {exc}")
        return {}

generated_settings = [
    source_settings_block(
        source_root / "modules/01_base.sh",
        "cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'\n",
        "\nCALAMARESSETTINGS",
    ),
    source_settings_block(
        source_root / "modules/03_desktop.sh",
        "cat > /etc/calamares/settings.conf <<'SETTINGS'\n",
        "\nSETTINGS",
    ),
    source_settings_block(
        source_root / "modules/03_desktop.sh",
        "cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'\n",
        "\nSTATICCALASETTINGS",
    ),
]
for generated in generated_settings:
    generated_exec = []
    for phase in generated.get("sequence", []) or []:
        if isinstance(phase, dict) and "exec" in phase:
            generated_exec = phase.get("exec") or []
    if any(step not in generated_exec for step in expected_steps):
        errors.append("generated Calamares settings path is missing receipt or desktop gate steps")
    elif [generated_exec.index(step) for step in receipt_order] != sorted(
        generated_exec.index(step) for step in receipt_order
    ):
        errors.append("generated Calamares settings path has unsafe receipt ordering")

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

grub_install = root / "usr/sbin/grub-install"
if not grub_install.is_file():
    errors.append("live installer environment is missing /usr/sbin/grub-install; BIOS bootloader install will fail")

for relative_path in [
    "usr/local/sbin/ming-calamares-preflight",
    "usr/local/sbin/ming-live-installer-root",
    "usr/local/sbin/ming-install-bootloader",
    "usr/local/sbin/ming-installer-verify",
    "usr/local/sbin/ming-finish-install-reboot",
    "usr/local/bin/ming-install-mode-chooser",
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
        if relative_path.endswith("ming-live-installer-root"):
            if "boot=live" not in text or "ming.installer=1" not in text:
                errors.append(f"{relative_path} must refuse non-Live execution")
            if (
                "ming-install-mode write" not in text
                or "ming-calamares-preflight" not in text
                or "calamares -d" not in text
            ):
                errors.append(f"{relative_path} must own mode write, preflight, and Calamares")
        if relative_path.endswith((
            "ming-calamares-preflight", "ming-live-installer-root",
            "ming-ota-preflight", "ming-install-bootloader",
            "ming-finish-install-reboot",
        )):
            if "/tmp/ming-installer" in text:
                errors.append(f"{relative_path} must not write privileged logs under /tmp")
            if "/run/ming-installer" not in text:
                errors.append(f"{relative_path} must use the root-owned /run/ming-installer state directory")
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
            if "ming-installer-verify installed --receipt --final-boot" not in text:
                errors.append(
                    f"{relative_path} must run the unified final installed-system verification"
                )
        if relative_path.endswith("ming-finish-install-reboot"):
            if "systemctl -i reboot" not in text:
                errors.append(f"{relative_path} must request an inhibitor-safe reboot")
            if "eject " in text:
                errors.append(f"{relative_path} must not eject the mounted live medium before reboot")
            if "efibootmgr -n" not in text:
                errors.append(f"{relative_path} must prefer Ming OS for the next UEFI boot")
        if relative_path.endswith("ming-calamares-launcher"):
            if "is_live_or_installer" not in text:
                errors.append(f"{relative_path} must refuse to run outside Live/installer sessions")
            if "choose_install_mode" not in text or "ming-install-mode-chooser" not in text or "ming-live-installer-root" not in text:
                errors.append(f"{relative_path} must choose an install mode before invoking the root helper")
            if "zenity --list --radiolist" in text:
                errors.append(f"{relative_path} must not use the keyboard-hostile Zenity radiolist chooser")
        if relative_path.endswith("ming-install-mode-chooser"):
            if "Gtk.ResponseType.OK" not in text or "self.blank_button.grab_focus()" not in text:
                errors.append(f"{relative_path} must provide a keyboard-accessible default install choice")
        if relative_path.endswith(("ming-live-installer.sh", "ming-installer-session")) and "ming-calamares-launcher" not in text:
            errors.append(f"{relative_path} must launch Calamares through ming-calamares-launcher")

chooser_path = root / "usr/local/bin/ming-install-mode-chooser"
launcher_path = root / "usr/local/bin/ming-calamares-launcher"
for path, marker, label in (
    (chooser_path, "Gtk.ResponseType.OK", "ming-install-mode-chooser"),
    (launcher_path, "ming-install-mode-chooser", "ming-calamares-launcher"),
):
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"{label} missing or empty")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    if marker not in text:
        errors.append(f"{label} missing required marker {marker}")

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
    if ! grep -Fq 'set timeout=1' "${grub_cfg}"; then
        log_error "ISO GRUB must auto-start after one second"
        exit 1
    fi
    if [[ "$(grep -c '^menuentry "启动/安装 Ming OS' "${grub_cfg}" || true)" -ne 1 ]]; then
        log_error "ISO GRUB must expose exactly one visible default installer entry"
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
    # PCI power management untouched. Hidden Shift-only compatibility entries
    # may carry recovery parameters without cluttering the default menu.
    local default_entry
    default_entry=$(awk '/^menuentry "启动\/安装 Ming OS/{body=""; in_entry=1; next} in_entry{body=body $0 "\n"} in_entry && /^}/{print body; exit}' "${grub_cfg}")
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
    if ! grep -Fq 'DEFAULT ming' "${cfg}" || ! grep -Fq 'ONTIMEOUT ming' "${cfg}" || ! grep -Fq 'TIMEOUT 10' "${cfg}"; then
        log_error "isolinux fallback must use one default entry and a one-second timeout"
        return 1
    fi
    if [[ "$(grep -c '^LABEL ' "${cfg}" || true)" -ne 1 ]]; then
        log_error "isolinux fallback must expose exactly one boot label"
        return 1
    fi
    if grep -Eq '^LABEL (safe|oldpc)' "${cfg}" || grep -Fq 'nomodeset' "${cfg}"; then
        log_error "isolinux fallback must not expose separate safe/oldpc labels"
        return 1
    fi
    for marker in 'LABEL ming' 'MENU LABEL Boot / Install Ming OS' 'KERNEL /live/vmlinuz' 'INITRD /live/initrd' 'ming.installer=1'; do
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
    for command in brightnessctl xdotool wmctrl pactl bluetoothctl upower pkexec lxpolkit notify-send zenity xprop nm-online fc-match; do
        if ! chroot_exec /bin/sh -c "command -v '${command}' >/dev/null 2>&1"; then
            log_error "required desktop command is missing: ${command}"
            return 1
        fi
    done
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
        pipewire pipewire-pulse pipewire-alsa wireplumber pulseaudio-utils alsa-utils libasound2-plugins \
        libspa-0.2-bluetooth pavucontrol dbus-user-session dbus-x11 libpam-systemd bluez upower pkexec polkitd \
        lxpolkit libnotify-bin zenity x11-utils desktop-file-utils fontconfig fonts-noto-core fonts-noto-cjk fonts-noto-mono \
        i965-va-driver intel-media-va-driver libgl1-mesa-dri mesa-va-drivers mesa-vdpau-drivers \
        mesa-vulkan-drivers mesa-utils lm-sensors firmware-amd-graphics amd64-microcode vainfo \
        fcitx5-rime librime-data rime-data-luna-pinyin minisign; do
        if ! chroot_exec dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -qx 'ii '; then
            log_error "required desktop runtime package is not installed: ${package}"
            return 1
        fi
    done
    if [[ "${MING_BUILD_PROFILE}" == "release" ]]; then
        if [[ ! -s "${CHROOT_DIR}/etc/ming-update/ota-release.minisign.pub" ||
              -L "${CHROOT_DIR}/etc/ming-update/ota-release.minisign.pub" ]]; then
            log_error "release rootfs is missing the OTA Minisign public key"
            return 1
        fi
    fi

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
        | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("state") in {"synced", "waiting_network", "service_inactive", "dbus_unavailable", "failed"} else 1)'; then
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
import os
import shlex
import shutil
import sys

root = Path(sys.argv[1])
desktop_names = [
    "ming-settings.desktop",
    "ming-files.desktop",
    "ming-terminal.desktop",
    "ming-firefox.desktop",
    "ming-store.desktop",
    "ming-toolbox.desktop",
]
if os.environ.get("MING_SKIP_XIAHAI") != "1":
    desktop_names.append("xiahai-xiaoming.desktop")
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

firefox_backends = [
    root / "usr/bin/firefox-esr",
    root / "usr/bin/firefox",
]
if not any(path.is_file() and os.access(path, os.X_OK) for path in firefox_backends):
    errors.append("missing Firefox ESR browser backend behind ming-firefox wrapper")

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
    MING_BUILD_PROFILE="${MING_BUILD_PROFILE}" python3 - "${CHROOT_DIR}" <<'PY'
from pathlib import Path
import os
import re
import stat
import subprocess
import struct
import sys
import tempfile
import hashlib
import subprocess
import zlib
import importlib.util

root = Path(sys.argv[1])
build_profile = os.environ.get("MING_BUILD_PROFILE", "dev")
errors = []

build_identity_path = root / "etc/ming-os-build.json"
try:
    import json
    build_identity = json.loads(build_identity_path.read_text(encoding="ascii"))
    for field in ("version", "build_id", "source_commit", "build_time_utc",
                  "source_tree_sha256_prefix", "iso_sha256"):
        if field not in build_identity:
            errors.append(f"ming-os-build.json missing {field}")
    if build_identity.get("version") != "26.4.1":
        errors.append("ming-os-build.json version mismatch")
    if build_identity.get("iso_sha256") is not None:
        errors.append("rootfs build identity must not claim a self-referential ISO hash")
except (OSError, ValueError, TypeError) as error:
    errors.append(f"invalid etc/ming-os-build.json: {error}")

def _rootfs_path(relative_path):
    """Return a normalized path that is lexically contained by the target rootfs."""
    candidate = Path(os.path.normpath(str(root / str(relative_path))))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate

def rootfs_resolved_path(relative_path, depth=0):
    """Resolve target-rootfs symlinks without consulting the host root."""
    path = _rootfs_path(relative_path)
    if path is None:
        return None
    if depth >= 8:
        return path
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    except OSError:
        return path
    if not stat.S_ISLNK(info.st_mode):
        return path
    try:
        target = os.readlink(path)
    except OSError:
        return path
    target_path = Path(target)
    # Rootfs links always use POSIX targets, even when a source-level gate is
    # exercised on a Windows development host.
    if str(target).startswith("/"):
        absolute_target = str(target_path).lstrip("/")
        next_path = root / absolute_target
    else:
        next_path = path.parent / target_path
    next_path = Path(os.path.normpath(str(next_path)))
    try:
        next_relative = next_path.relative_to(root)
    except ValueError:
        return None
    return rootfs_resolved_path(str(next_relative), depth + 1)

def _rootfs_lstat(relative_path):
    path = rootfs_resolved_path(relative_path)
    if path is None:
        errors.append(f"{relative_path} resolves outside target rootfs")
        return None, None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path, None
    except OSError as error:
        errors.append(f"cannot inspect {relative_path}: {error}")
        return path, None
    if stat.S_ISLNK(info.st_mode):
        errors.append(f"{relative_path} contains an unresolved or looping symlink")
        return path, None
    return path, info

def require_file(relative_path, marker=None):
    path, info = _rootfs_lstat(relative_path)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        errors.append(f"missing or empty {relative_path}")
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if marker and marker not in text:
        errors.append(f"{relative_path} missing marker {marker!r}")
    return text

def verify_openpgp_keyring(relative_path, expected_sha256, expected_fingerprint, label):
    original = _rootfs_path(relative_path)
    if original is None:
        errors.append(f"{label} trusted keyring is outside target rootfs")
        return False
    try:
        original_info = original.lstat()
    except OSError:
        original_info = None
    if original_info is None or stat.S_ISLNK(original_info.st_mode):
        errors.append(f"{label} trusted keyring is missing or unsafe")
        return False
    path, info = _rootfs_lstat(relative_path)
    if info is None or not stat.S_ISREG(info.st_mode):
        errors.append(f"{label} trusted keyring is missing or unsafe")
        return False
    try:
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    except OSError as error:
        errors.append(f"{label} trusted keyring cannot be read: {error}")
        return False
    if actual_sha256 != expected_sha256:
        errors.append(f"{label} trusted keyring SHA256 mismatch")
        return False
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", "--fingerprint", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        errors.append(f"{label} trusted keyring fingerprint check failed: {error}")
        return False
    primary_fingerprints = []
    previous_type = None
    for line in result.stdout.splitlines():
        fields = line.split(":")
        record_type = fields[0] if fields else ""
        if record_type == "fpr" and previous_type == "pub" and len(fields) > 9:
            primary_fingerprints.append(fields[9].upper())
        previous_type = record_type
    if result.returncode != 0 or expected_fingerprint not in primary_fingerprints:
        errors.append(f"{label} trusted keyring fingerprint mismatch")
        return False
    return True

if build_profile == "release":
    ota_release_key_path = root / "etc/ming-update/ota-release.minisign.pub"
    if ota_release_key_path.is_symlink():
        errors.append("OTA release Minisign public key must not be a symlink")
    ota_release_key = require_file(
        "etc/ming-update/ota-release.minisign.pub", "untrusted comment:"
    )
    if ota_release_key and not re.search(r"^RW[A-Za-z0-9+/=]{40,}$", ota_release_key, re.MULTILINE):
        errors.append("OTA release Minisign public key has an invalid format")

def require_path(relative_path):
    _path, info = _rootfs_lstat(relative_path)
    if info is None or (stat.S_ISREG(info.st_mode) and info.st_size == 0):
        errors.append(f"missing or empty {relative_path}")

def require_absent(relative_path, reason):
    path = _rootfs_path(relative_path)
    if path is None:
        errors.append(f"{relative_path} resolves outside target rootfs: {reason}")
        return
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        errors.append(f"cannot inspect forbidden path {relative_path}: {error}")
        return
    # lstat deliberately treats dangling links as present, so forbidden
    # residues cannot hide behind a broken symlink.
    if path.exists() or path.is_symlink():
        errors.append(f"{relative_path} must not be preinstalled: {reason}")

def validate_generated_executable(relative_path, language):
    """Reject a missing, non-executable, or syntactically invalid shipped helper."""
    path, info = _rootfs_lstat(relative_path)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_size == 0:
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
    if relative_path.endswith(".slice"):
        if "[Unit]" not in text or "[Slice]" not in text:
            errors.append(f"{relative_path} is not a complete systemd slice unit")
        if not re.search(r"^(CPUWeight|IOWeight|MemoryMax|TasksMax|StartupCPUWeight|StartupIOWeight)=.+$", text, flags=re.MULTILINE):
            errors.append(f"{relative_path} has no slice directive")
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
require_file("usr/share/pixmaps/ming-os-logo.png")
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

security_control = require_file(
    "usr/local/sbin/ming-security-control", "apply_firewall_atomic")
for marker in ["status", "quick-check", "firewall", "profile", "ssh", "security-updates"]:
    if marker not in security_control:
        errors.append(f"ming-security-control missing interface marker {marker}")
require_file(
    "usr/share/polkit-1/actions/org.ming.security.control.policy",
    "/usr/local/sbin/ming-security-control")
validate_generated_executable("usr/local/sbin/ming-security-control", "python")
admin_bootstrap = require_file("usr/local/sbin/ming-admin-bootstrap", "administrator_status")
for marker in ["bootstrap_administrator", "caller_matches_user", "usable_administrator_exists"]:
    if marker not in admin_bootstrap:
        errors.append(f"ming-admin-bootstrap missing boundary marker {marker}")
require_file("usr/bin/sudo")
require_file("usr/bin/pkexec")
require_file("etc/sudoers", "%sudo")
require_file("usr/share/polkit-1/actions/org.ming.account.bootstrap.policy",
             "/usr/local/sbin/ming-admin-bootstrap")
validate_generated_executable("usr/local/sbin/ming-admin-bootstrap", "python")
ota_ab = require_file("usr/local/sbin/ming-ota-ab", "layout_status")
for marker in ["validate_layout", "observe_boot", "prepare_slot_root"]:
    if marker not in ota_ab:
        errors.append(f"ming-ota-ab missing contract marker {marker}")
require_file("usr/local/sbin/ming-ota-ab-stage", "/boot/ming-slots/${target}")
validate_generated_executable("usr/local/sbin/ming-ota-ab", "python")
validate_generated_executable("usr/local/sbin/ming-ota-ab-stage", "bash")
partition_config = require_file("etc/calamares/modules/partition.conf", "partitionLayout:")
for marker in ["MING-BOOT", "MING-ROOT-A", "MING-ROOT-B", "MING-HOME", "requiredStorage: 48"]:
    if marker not in partition_config:
        errors.append(f"Calamares OTA-ready layout missing {marker}")

storage_status = require_file("usr/local/bin/ming-storage-status", "LSBLK_FIELDS")
for marker in ["partitions", "--json", "parse_lsblk", "timeout=3"]:
    if marker not in storage_status:
        errors.append(f"ming-storage-status missing read-only marker {marker}")
validate_generated_executable("usr/local/bin/ming-storage-status", "python")

appearance_control = require_file(
    "usr/local/bin/ming-appearance-control", "apply_and_commit")
for marker in ["status", "apply", "reset", "reapply", "appearance.last-good.json"]:
    if marker not in appearance_control:
        errors.append(f"ming-appearance-control missing interface marker {marker}")
validate_generated_executable("usr/local/bin/ming-appearance-control", "python")

settings_desktop = require_file("usr/share/applications/ming-settings.desktop", "Exec=/usr/local/bin/ming-control-center")
if "Exec=/usr/local/bin/ming-settings" in settings_desktop:
    errors.append("ming-settings.desktop must use the stable ming-control-center launcher")

trusted_receipts = root / "var/lib/ming-os/trusted-desktops"
if not trusted_receipts.is_dir():
    errors.append("trusted desktop receipt directory is missing")
for launcher in [
    "ming-settings.desktop", "ming-files.desktop", "ming-app-library.desktop",
    "ming-firefox.desktop", "ming-terminal.desktop", "Install Ming OS.desktop",
]:
    launcher_path = root / "usr/share/applications" / launcher
    if launcher_path.is_file() and not (trusted_receipts / launcher).is_file():
        errors.append(f"missing trusted desktop receipt for {launcher}")

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
session_autostart = require_file(
    "home/user/.config/autostart/ming-session-healthcheck.desktop",
    "ming-session-healthcheck --session",
)
if "X-GNOME-Autostart-enabled=true" not in session_autostart or "Hidden=false" not in session_autostart:
    errors.append("unified session healthcheck autostart must be enabled")
dock_autostart = require_file("home/user/.config/autostart/ming-dock.desktop", "/usr/bin/true")
if "X-GNOME-Autostart-enabled=false" not in dock_autostart or "Hidden=true" not in dock_autostart:
    errors.append("legacy Dock autostart must be disabled")
phone_autostart = require_file("home/user/.config/autostart/ming-phone-desktop.desktop", "/usr/bin/true")
if "X-GNOME-Autostart-enabled=false" not in phone_autostart or "Hidden=true" not in phone_autostart:
    errors.append("legacy phone desktop autostart must be disabled")
for legacy_entry in (dock_autostart, phone_autostart):
    legacy_exec = next(
        (line for line in legacy_entry.splitlines() if line.startswith("Exec=")), "")
    if legacy_exec != "Exec=/usr/bin/true":
        errors.append("legacy desktop autostart must not launch a second session loop")

plank_settings = require_file("home/user/.config/plank/dock1/settings", "DockItems=ming-settings.dockitem")
# The retired GTK3 Dock filename may exist only as an upgrade shim.  The
# shipped shim must be inert so an old environment variable cannot create a
# second launcher surface.
legacy_dock = require_file("usr/local/bin/ming-dock", "exit 0")
if "import gi" in legacy_dock or "Gtk.Window" in legacy_dock:
    errors.append("retired ming-dock must be an inert compatibility shim")
legacy_status = require_file("usr/local/bin/ming-status-center", "ming-status-widget-toggle")
legacy_library = require_file("usr/local/bin/ming-app-library", "ming-app-drawer")
if "import gi" in legacy_status or "Gtk." in legacy_status:
    errors.append("retired ming-status-center must delegate to the Ming widget")
if "import gi" in legacy_library or "Gtk." in legacy_library:
    errors.append("retired ming-app-library must delegate to the Ming drawer")
# One responsive Dock profile owns all installed and Live sessions. Runtime
# sizing selects 32/36/40 px from the screen short edge without changing the
# centered geometry, 12 px gap, Ming theme or hover animation.
for marker in [
    # RC3's Offset=12 shifted the centered Dock; the shipped profile must use
    # the active value "Offset=0" (the legacy marker "Offset=12" is rejected).
    # zero offset and reserve the 12px bottom margin through the strut helper.
    "MingDockProfile=2641-responsive-centered", "Alignment=3", "Offset=0",
    "ZoomEnabled=true", "ZoomPercent=148", "HideMode=0", "Theme=Ming",
    "ming-store.dockitem",
]:
    if marker not in plank_settings:
        errors.append(f"responsive Plank settings missing {marker}")
if plank_settings.count("ming-app-library.dockitem") != 1:
    errors.append("Plank settings must contain exactly one application drawer item")
if "ming-disk-hub.dockitem" in plank_settings:
    errors.append("Plank settings must not include the retired All Disks item")
if "ming-firefox.dockitem" not in plank_settings:
    errors.append("Plank settings must include ming-firefox.dockitem as the default browser")
if os.environ.get("MING_SKIP_XIAHAI") != "1" and "xiahai-xiaoming.dockitem" not in plank_settings:
    errors.append("Plank settings must include xiahai-xiaoming.dockitem as the default agent")
for forbidden_dock in ["wechat.dockitem", "wps-office.dockitem"]:
    if forbidden_dock in plank_settings:
        errors.append(f"Plank settings must not include retired dock item {forbidden_dock}")
for dock_item in plank_settings.split("DockItems=", 1)[-1].splitlines()[0].split(";;"):
    if "claw" in dock_item.casefold():
        errors.append(f"Plank settings contains a retired agent item: {dock_item}")

plank_theme = require_file("usr/share/plank/themes/Ming/dock.theme", "IndicatorSize=4")
plank_default_theme = require_file("usr/share/plank/themes/Default/dock.theme", "IndicatorSize=4")
for marker in [
        "OuterStrokeColor=31;;98;;84;;54",
        "FillStartColor=255;;255;;255;;226",
        "FillEndColor=242;;250;;247;;238",
        "[PlankDockTheme]",
        "TopRoundness=14",
        "BottomRoundness=0",
        "BottomPadding=2",
        "HorizPadding=16",
        "ItemPadding=4",
        "UrgentBounceTime=420",
        "LaunchBounceTime=150",
        "ItemMoveTime=130"]:
    if marker not in plank_theme:
        errors.append(f"Plank theme missing animation marker {marker}")
    if marker not in plank_default_theme:
        errors.append(f"Default Plank theme missing animation marker {marker}")

require_file("usr/share/themes/Ming-Dark/gtk-3.0/gtk.css", "#151A18")
require_file("usr/share/themes/Ming-Dark/xfce-notify-4.0/gtk.css", "window#XfceNotifyWindow")
require_file("usr/share/themes/Ming-Dark/index.theme", "GtkTheme=Ming-Dark")

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

package_installer = require_file(
    "usr/local/sbin/ming-package-installer", "sync_opt_app_proxies")
store_core = require_file(
    "usr/local/lib/ming-os/ming-store-core.py", "ming.store.catalog.v1")
require_file(
    "usr/local/lib/ming-os/ming-store-core.py", "ming.store.transaction.v1")
require_file(
    "usr/local/lib/ming-os/ming-store-core.py", "WineOfficialProvider")
store_control = require_file(
    "usr/local/sbin/ming-store-control", "REQUEST_ID")
android_runtime = require_file(
    "usr/local/bin/ming-android-runtime", "class AndroidRuntime")
validate_generated_executable("usr/local/bin/ming-android-runtime", "python")
android_root_helper = require_file(
    "usr/local/sbin/ming-android-runtime", "install-deps")
for marker in [
    "install-deps", "start-container", "stop-container", "repair",
    "repo.waydro.id", "ming-waydroid.gpg",
    "71FE05D735C812E15FE229BF10106B02B62561BE8AA5280D63A58E25A5C0C5E2",
    "Pin-Priority: -1", "dpkg-query -W",
]:
    if marker not in android_root_helper:
        errors.append(f"Android root helper missing allowlisted action {marker}")
android_policy = require_file(
    "usr/share/polkit-1/actions/org.ming.android.runtime.policy",
    "/usr/local/sbin/ming-android-runtime")
for marker in ["<allow_any>no</allow_any>", "<allow_inactive>no</allow_inactive>"]:
    if marker not in android_policy:
        errors.append(f"Android Polkit policy missing {marker}")
for marker in ["REQUEST_ID", "PROTECTED_PACKAGES", "Acquire::Retries=3",
               "--no-auto-remove", "installed_state", "refresh_warning",
               "toolbox_required", "installation_disabled", "--status-fd=1",
               "resources.json", "resolved_architecture"]:
    if marker not in store_control and marker not in store_core:
        errors.append(f"Ming Store transaction boundary missing {marker}")
store_policy = require_file(
    "usr/share/polkit-1/actions/org.mingos.store.manage.policy",
    "/usr/local/sbin/ming-store-control")
for marker in ["<allow_any>no</allow_any>", "<allow_inactive>no</allow_inactive>"]:
    if marker not in store_policy:
        errors.append(f"Ming Store Polkit policy missing {marker}")
validate_generated_executable("usr/local/sbin/ming-store-control", "python")
# Wine manifest entries are display-only until a fixed official artifact,
# SHA256 and Minisign identity are present; installation is handed to Toolbox.
for source_id in ["ming-official", "debian-apt", "vendor-official", "wine-official"]:
    catalog_path = root / "usr/share/ming-os/store/catalog" / f"{source_id}.json"
    if not catalog_path.is_file():
        errors.append(f"missing Ming Store catalog: {source_id}")
        continue
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        errors.append(f"invalid Ming Store catalog {source_id}: {error}")
        continue
    if catalog.get("schema") != "ming.store.catalog.v1":
        errors.append(f"Ming Store catalog schema mismatch: {source_id}")
    if source_id == "wine-official" and catalog.get("source", {}).get("trust") != "minisign":
        errors.append("Wine manifest must declare Minisign trust")
spark_config_path = root / "usr/share/ming-os/store/catalog/spark-public.json"
spark_config = {}
spark_keyring_valid = verify_openpgp_keyring(
    "etc/ming-os/store/spark-archive-keyring.gpg",
    "49DFC2D391822E0E50AC9D79B94FF2B5A4EBEBFF9939CEA056733FEF01B9BAA4",
    "9D9AA859F75024B1A1ECE16E0E41D354A29A440C",
    "Spark",
)
verify_openpgp_keyring(
    "usr/share/keyrings/ming-waydroid.gpg",
    "71FE05D735C812E15FE229BF10106B02B62561BE8AA5280D63A58E25A5C0C5E2",
    "7CE0331F71E0A238BB1002D70E406D181DCEE19C",
    "Waydroid",
)
if not spark_config_path.is_file() or spark_config_path.stat().st_size == 0:
    errors.append("missing Ming Store Spark public provider configuration")
else:
    try:
        spark_config = json.loads(spark_config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        errors.append(f"invalid Spark public provider configuration: {error}")
if spark_config:
    if spark_config.get("schema") != "ming.store.spark-public.v1":
        errors.append("Spark public provider configuration schema mismatch")
    if spark_config.get("provider") != "spark-public":
        errors.append("Spark public provider configuration identity mismatch")
    if spark_config.get("key_fingerprint") != "9D9AA859F75024B1A1ECE16E0E41D354A29A440C":
        errors.append("Spark public provider key fingerprint mismatch")
    if not isinstance(spark_config.get("installation_enabled"), bool):
        errors.append("Spark public provider installation_enabled policy must be boolean")
    if spark_config.get("installation_enabled"):
        keyring_value = str(spark_config.get("keyring") or "")
        if (keyring_value != "/etc/ming-os/store/spark-archive-keyring.gpg"
                or not spark_keyring_valid):
            errors.append("Spark installation cannot be enabled without a valid trusted archive keyring")
    else:
        errors.append("Spark public provider installation must be enabled in a trusted-key image")
store_core_path = root / "usr/local/lib/ming-os/ming-store-core.py"
if store_core_path.is_file():
    try:
        spec = importlib.util.spec_from_file_location("ming_store_core_build_gate", store_core_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        errors.append(f"Ming Store core import failed: {error}")
    else:
        # Count the metadata shipped in this rootfs, rather than relying on a
        # synthetic JSON fixture or a test-only AppStream document.  The store
        # may display curated entries, but a release image must contain a real
        # AppStream inventory of at least 1000 installable desktop apps.
        MIN_ROOTFS_APPSTREAM_APPS = 1000
        appstream_inventory = module.scan_appstream_rootfs(root)
        try:
            module.validate_appstream_rootfs(
                root, minimum=MIN_ROOTFS_APPSTREAM_APPS, inventory=appstream_inventory)
        except Exception as error:
            errors.append(f"rootfs AppStream metadata gate failed: {error}")
        try:
            provider = module.WineOfficialProvider(
                catalog_root=root / "usr/share/ming-os/store/catalog",
                public_key_path=root / "etc/ming-os/store/ming-wine-catalog.minisign.pub",
            )
            provider.refresh_catalog()
        except Exception as error:
            errors.append(f"Wine manifest validation failed: {error}")
else:
    errors.append("missing Ming Store core for Wine manifest validation")
launch_broker = require_file("usr/local/bin/ming-launch", "verify_desktop_proxy")
for marker in ["manifest-v1.json", "manifest_sha256", "source_sha256", "proxy_sha256"]:
    if marker not in package_installer or marker not in launch_broker:
        errors.append(f"managed /opt/apps proxy contract missing {marker}")

for residue in [
    "usr/bin/spark-store", "opt/spark-store", "opt/durapps/spark-store",
    "usr/bin/apm", "usr/bin/bookworm-run", "usr/bin/trixie-run",
    "usr/local/bin/ming-spark-store", "usr/local/sbin/ming-spark-package-control",
    "usr/local/bin/ming-spark-backend-status", "usr/local/libexec/ming-spark-aria2c",
    "etc/apt/preferences.d/90-ming-spark-store",
    "usr/share/polkit-1/actions/org.ming.spark.package-control.policy",
    "usr/share/polkit-1/actions/store.spark-app.spark-store.policy",
    "usr/share/polkit-1/actions/store.spark-app.ssinstall.policy",
    "usr/lib/systemd/system/spark-update-notifier.service",
    "etc/systemd/system/spark-store-refresh.service",
    "usr/share/applications/spark-store.desktop",
    "usr/share/applications/ming-install-spark-store.desktop",
    "usr/local/bin/ming-install-wps",
    "usr/share/applications/ming-install-wps.desktop",
    "usr/share/applications/wps-office.desktop",
    "usr/share/ming-os/vendor/spark-store",
]:
    require_absent(residue, "Spark/APM residue")

if os.environ.get("MING_SKIP_XIAHAI") != "1":
    xiahai_binary = require_file(
        "opt/xiahai-xiaoming/xiahai-xiaoming", "ELF")
    if stat.S_IMODE((root / "opt/xiahai-xiaoming/xiahai-xiaoming").stat().st_mode) & 0o055 != 0o055:
        errors.append("Xiahai Xiaoming executable is not readable/executable by desktop users")
    xiahai_desktop = require_file(
        "usr/share/applications/xiahai-xiaoming.desktop",
        "Exec=/opt/xiahai-xiaoming/xiahai-xiaoming")
    for marker in [
        "Name=Xiahai Xiaoming",
        "Exec=/opt/xiahai-xiaoming/xiahai-xiaoming",
        "Type=Application",
    ]:
        if marker not in xiahai_desktop:
            errors.append(f"Xiahai Xiaoming desktop entry missing {marker}")
    # Accept the legacy icon name for upgrades while requiring the RC4 name
    # shipped by the repaired package.
    if "Icon=ming-xiahai" not in xiahai_desktop and "Icon=xiahai-xiaoming" not in xiahai_desktop:
        errors.append("Xiahai Xiaoming desktop entry has no approved icon")
    xiahai_icon = root / "usr/share/icons/hicolor/128x128/apps/xiahai-xiaoming.png"
    if not xiahai_icon.is_file() or xiahai_icon.stat().st_size == 0:
        errors.append("missing Xiahai Xiaoming app icon: usr/share/icons/hicolor/128x128/apps/xiahai-xiaoming.png")

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
    "usr/share/applications/ming-update.desktop",
    "home/user/Desktop/ming-update.desktop",
    "home/user/.config/plank/dock1/launchers/ming-update.dockitem",
]:
    require_absent(retired_path, "retired Ming shell surface")

drawer_desktop = require_file("usr/share/applications/ming-app-library.desktop", "Exec=/usr/local/bin/ming-app-drawer")
if "NoDisplay=true" not in drawer_desktop:
    errors.append("application drawer desktop entry must stay hidden outside the Dock")

update_gui = require_file("usr/local/bin/ming-update-gui", "exec /usr/local/bin/ming-control-center --page update")
for forbidden in ["zenity", "ming-update.desktop", "/usr/local/bin/ming-update check"]:
    if forbidden in update_gui:
        errors.append(f"ming-update-gui must only redirect to Ming Settings, found {forbidden}")

expected_wallpaper_sizes = {
    "usr/share/backgrounds/ming-os/default-2640.png": (3840, 2160),
    "usr/share/backgrounds/ming-os/default.png": (3840, 2160),
    "usr/share/backgrounds/ming-os/default-3840x2160.png": (3840, 2160),
    "usr/share/backgrounds/ming-os/default-1920x1080.png": (1920, 1080),
    "usr/share/backgrounds/ming-os/default-1366x768.png": (1366, 768),
}
for wallpaper_path, expected_size in expected_wallpaper_sizes.items():
    wallpaper = root / wallpaper_path
    if not wallpaper.is_file() or wallpaper.stat().st_size == 0:
        errors.append(f"missing or empty wallpaper: {wallpaper_path}")
        continue
    try:
        data = wallpaper.read_bytes()
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("invalid PNG signature or truncated header")
        if struct.unpack(">I", data[8:12])[0] != 13 or data[12:16] != b"IHDR":
            raise ValueError("missing initial PNG IHDR chunk")
        stored_crc = struct.unpack(">I", data[29:33])[0]
        if stored_crc != (zlib.crc32(data[12:29]) & 0xFFFFFFFF):
            raise ValueError("invalid PNG IHDR checksum")
        if not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise ValueError("missing PNG IEND chunk")
        dimensions = struct.unpack(">II", data[16:24])
    except (OSError, ValueError, struct.error) as error:
        errors.append(f"invalid wallpaper PNG {wallpaper_path}: {error}")
        continue
    if dimensions != expected_size:
        errors.append(
            f"wallpaper dimensions mismatch for {wallpaper_path}: "
            f"expected {expected_size[0]}x{expected_size[1]}, got {dimensions[0]}x{dimensions[1]}"
        )
appearance = require_file("usr/local/bin/ming-apply-appearance", "/usr/share/backgrounds/ming-os/default.png")
for marker in ["/desktop-icons/style", "-s 0", "ming-phone-desktop-watchdog", "ming-plank-watchdog"]:
    if marker not in appearance:
        errors.append(f"ming-apply-appearance missing native desktop marker {marker}")
for forbidden in ["xfce4-panel --quit", "-s 2"]:
    if forbidden in appearance:
        errors.append(f"ming-apply-appearance must not start or stop the native desktop with {forbidden}")

cache_dir = root / "home/user/.cache/ming-os"
if not cache_dir.exists():
    errors.append("home/user/.cache/ming-os must exist so watchdog logs are writable")
else:
    st = cache_dir.stat()
    if st.st_uid != 1000 or st.st_gid != 1000:
        errors.append(f"home/user/.cache/ming-os must be owned by uid/gid 1000, got {st.st_uid}/{st.st_gid}")

state_root = root / "home/user/.config/ming-os"
if not state_root.is_dir():
    errors.append("home/user/.config/ming-os must exist for writable desktop state")
else:
    for state_path in [state_root, *state_root.rglob("*")]:
        state = state_path.lstat()
        if state.st_uid != 1000 or state.st_gid != 1000:
            errors.append(
                f"{state_path.relative_to(root)} must be owned by uid/gid 1000, "
                f"got {state.st_uid}/{state.st_gid}")

home_root = root / "home/user"
if not home_root.is_dir():
    errors.append("home/user ownership mismatch: directory is missing")
else:
    for home_path in [home_root, *home_root.rglob("*")]:
        state = home_path.lstat()
        if state.st_uid != 1000 or state.st_gid != 1000:
            errors.append(
                f"home/user ownership mismatch: {home_path.relative_to(root)} "
                f"is uid/gid {state.st_uid}/{state.st_gid}")

for helper in [
    "usr/local/bin/ming-network-repair",
    "usr/local/bin/ming-driver-diagnose",
    "usr/local/bin/ming-diagnostic-bundle",
    "usr/local/bin/ming-surface-support",
    "usr/local/bin/ming-classic-mode",
    "usr/local/bin/ming-lock",
    "usr/local/bin/ming-power-action",
    "usr/local/bin/ming-picom",
    "usr/local/bin/ming-plank-watchdog",
    "usr/local/bin/ming-desktop-healthcheck",
    "usr/local/bin/ming-window-control",
    "usr/local/bin/ming-window-manager-watchdog",
    "usr/local/bin/ming-volume-automount",
    "usr/local/bin/ming-input-healthcheck",
    "usr/local/bin/ming-phone-desktop-watchdog",
    "usr/local/bin/ming-firefox",
    "usr/local/bin/ming-store",
    "usr/local/sbin/ming-store-control",
    "usr/local/bin/ming-authorized-action",
    "usr/local/bin/ming-audio-session",
    "usr/local/sbin/ming-package-installer",
]:
    require_file(helper)

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
    "cgroup.controllers", "timer_migration", "systemd-oomd", "earlyoom",
]:
    if marker not in performance_status:
        errors.append(f"ming-performance-status missing diagnostic marker {marker}")
bash_generated_helpers = [
    "usr/local/sbin/ming-time-sync",
    "etc/NetworkManager/dispatcher.d/90-ming-time-sync",
    "usr/local/sbin/ming-intel-xorg-setup",
    "usr/local/bin/ming-window-control",
    "usr/local/bin/ming-desktop-healthcheck",
    "usr/local/bin/ming-plank-watchdog",
    "usr/local/bin/ming-window-manager-watchdog",
    "usr/local/sbin/ming-oom-policy",
    "usr/local/sbin/ming-timer-policy",
    "usr/local/bin/ming-ota-run",
    "usr/local/bin/ming-power-action",
    "usr/local/bin/ming-authorized-action",
]
for relative_path in bash_generated_helpers:
    validate_generated_executable(relative_path, "bash")

python_generated_helpers = [
    "usr/local/bin/ming-display-control",
    "usr/local/bin/ming-hardware-status",
    "usr/local/sbin/ming-performance-status",
    "usr/local/sbin/ming-performance-policy",
    "usr/local/sbin/ming-interaction-boost",
    "usr/local/sbin/ming-background-policy",
    "usr/local/bin/ming-prefetch",
    "usr/local/bin/ming-phone-desktop",
    "usr/local/bin/ming-settings",
    "usr/local/bin/ming-audio-session",
    "usr/local/sbin/ming-package-installer",
]
for relative_path in python_generated_helpers:
    validate_generated_executable(relative_path, "python")
for relative_path in [
    "etc/systemd/system/ming-intel-xorg-migration.service",
    "etc/systemd/system/ming-regdom.service",
    "etc/systemd/system/ming-hardware-preload.service",
    "etc/systemd/system/ming-oom-policy.service",
    "etc/systemd/system/ming-timer-policy.service",
    "etc/systemd/system/ming-ota.slice",
]:
    validate_systemd_unit(relative_path)
if (root / "etc/systemd/system/NetworkManager-wait-online.service.d").exists():
    errors.append("NetworkManager-wait-online drop-ins must not gate graphical boot")

display_control = require_file("usr/local/bin/ming-display-control", "parse_xrandr_snapshot")
for marker in [
    "status", "apply", "confirm", "rollback", "CONFIRM_SECONDS = 15",
    "request_is_supported", "parse_xrandr_brightness", "software-status",
    "software-set", "software-reapply", "--wait-seconds",
]:
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
for marker in ["low-memory", "safe-graphics-cmdline", "software-renderer", "virtual-machine-xrender", "VirtualBox", "QEMU", "VMware", "no-dri", "old-intel-gpu"]:
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
    require_absent(retired_path, "WeChat and WPS are optional installs in Ming OS 26.4.1")

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
    "usr/bin/firefox-esr",
    "usr/bin/firefox",
]):
    errors.append("missing Firefox ESR browser binary")

if not any((root / candidate).is_file() for candidate in [
    "usr/libexec/bluetooth/bluetoothd",
    "usr/lib/bluetooth/bluetoothd",
    "usr/sbin/bluetoothd",
]):
    errors.append("missing bluetoothd daemon")

require_file("usr/share/applications/ming-firefox.desktop", "Exec=/usr/local/bin/ming-firefox")
firefox_wrapper = require_file("usr/local/bin/ming-firefox", "homepage=/usr/share/ming-os/homepage/index.html")
if "firefox-esr" not in firefox_wrapper:
    errors.append("ming-firefox wrapper does not launch Firefox ESR")
require_file("usr/share/ming-os/homepage/index.html", "Ming OS")
firefox_policy = require_file("etc/firefox-esr/policies/policies.json", "Homepage")
if "file:///usr/share/ming-os/homepage/index.html" not in firefox_policy:
    errors.append("Firefox policy must restore the Ming OS homepage")

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
for marker in ["scanner bluetooth sudo nopasswdlogin autologin",
               "passwd -l \"${user_name}\"",
               "installed primary user must belong to sudo group",
               "ensure_ming_user || exit 30"]:
    if marker not in installed_identity:
        errors.append("installed identity repair must preserve password-backed sudo administration")
if 'gpasswd -d "${user_name}" sudo' in installed_identity:
    errors.append("installed identity repair must keep the primary user in sudo")
if 'chroot "${target}" passwd -d "${user_name}"' in installed_identity:
    errors.append("installed identity repair must not clear the pre-OOBE user password")
if '["usermod", "-aG", "sudo", user]' not in admin_bootstrap:
    errors.append("ming-admin-bootstrap must add sudo only after password setup succeeds")
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
require_file("home/user/.config/autostart/fcitx5.desktop", "ming-fcitx5-watchdog")
xinputrc = require_file("home/user/.xinputrc", "XMODIFIERS=@im=fcitx")
if "run_im fcitx5" in xinputrc or "fcitx5 -d --replace" in xinputrc:
    errors.append("xinputrc must not start a second Fcitx5 daemon")
require_file("etc/X11/Xsession.d/80-ming-fcitx5", "XMODIFIERS=@im=fcitx")
require_file("etc/skel/.config/fcitx5/profile", "Name=rime")
require_file("etc/skel/.config/autostart/fcitx5.desktop", "ming-fcitx5-watchdog")
require_file("usr/local/bin/ming-fcitx5-watchdog", "VmRSS")
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
input_repair = require_file("usr/local/sbin/ming-input-repair", "DefaultIM=pinyin")
for marker in ["--user", ".xinputrc.ming-legacy-backup", "run_im fcitx5"]:
    if marker not in input_repair:
        errors.append(f"ming-input-repair missing {marker} migration guard")

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
    "pkexec /usr/local/sbin/ming-radio-repair bluetooth",
    "rfkill unblock bluetooth", "systemctl enable bluetooth.service",
    "systemctl start bluetooth.service", "no_hardware", "/var/log/ming-radio-repair.log",
]:
    if marker not in radio_repair:
        errors.append(f"ming-radio-repair missing Bluetooth recovery marker {marker}")
if not os.access(root / "usr/local/sbin/ming-radio-repair", os.X_OK):
    errors.append("ming-radio-repair must be executable")
require_path("usr/local/bin/ming-radio-repair")

hardware_modules = require_file("usr/local/sbin/ming-hardware-preload", "btusb")
for marker in [
    "btusb", "btintel", "btrtl", "btbcm", "ath3k",
    "hid_multitouch", "bcm5974", "hid_apple", "applespi",
    "spi_pxa2xx_platform", "spi_pxa2xx_pci", "thinkpad_acpi", "ideapad_laptop",
    "huawei_wmi", "surface_aggregator", "surface_hid_core",
]:
    if marker not in hardware_modules:
        errors.append(f"hardware modules preload missing {marker}")
for forbidden in [
    "r8169", "r8168", "iwlwifi", "iwlmvm", "ath9k", "ath10k_pci",
    "rtl8192ee", "rtl8188ee", "e1000e",
]:
    if f"\n{forbidden}\n" in f"\n{hardware_modules}\n":
        errors.append(f"network driver must be selected by modalias/udev, not forced by ming-hardware-preload: {forbidden}")
require_file("etc/systemd/system/ming-hardware-preload.service", "Before=NetworkManager.service bluetooth.service display-manager.service")
require_file("etc/modules-load.d/ming-hardware.conf", "loop")

old_hw_modprobe = require_file("etc/modprobe.d/ming-old-hardware.conf", "bt_coex_active=1")
for marker in ["psmouse synaptics_intertouch=0", "snd_hda_intel power_save=0"]:
    if marker not in old_hw_modprobe:
        errors.append(f"old hardware modprobe policy missing {marker}")

ota_client = require_file("usr/local/bin/ming-update", "https://ming.sca-hub.cn")
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
    "GRUB_DISABLE_SUBMENU=false",
    "GRUB_DISABLE_OS_PROBER=true",
    "GRUB_DISABLE_RECOVERY=true",
]:
    if marker not in grub_defaults:
        errors.append(f"grub defaults missing {marker}")

hard_disk_grub = require_file("etc/grub.d/09_ming_os", "menuentry 'Ming OS'")
other_os_detector = require_file("usr/local/sbin/ming-detect-other-os", "EFI/Microsoft/Boot/bootmgfw.efi")
if "EFI/Linux" not in other_os_detector or "chainloader ($esp)" not in other_os_detector:
    errors.append("other-OS detector must keep explicit Windows and Linux EFI chainloader rules")
official_grub = root / "etc/grub.d/10_linux"
if not official_grub.is_file():
    errors.append("Debian official /etc/grub.d/10_linux generator is missing")
elif official_grub.stat().st_mode & 0o111:
    errors.append("Debian official /etc/grub.d/10_linux generator must be disabled to avoid duplicate top-level entries")
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
    "usr/bin/xfce4-panel",
    "usr/bin/xfdesktop",
    "usr/bin/thunar",
    "usr/sbin/mkfs.ext4",
    "lib/systemd/system/lightdm.service",
]:
    require_path(desktop_runtime)
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
set timeout=1
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
insmod keystatus

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
set timeout=1

menuentry "启动/安装 Ming OS ${MING_OS_VERSION}" {
 linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1
    initrd /live/initrd
}

if keystatus --shift; then
  submenu "高级兼容启动" {
    menuentry "Ming OS ${MING_OS_VERSION} 安全显卡模式" {
     linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog ming.installer=1 nomodeset vga=791
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

    # Surface Pro 1/2/3: preserve the touch and ACPI compatibility arguments.
    menuentry "Ming OS ${MING_OS_VERSION} Surface Pro 1/2/3 专用模式" {
     linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 i8042.noloop i8042.nomux i8042.nopnp i8042.reset intel_idle.max_cstate=1 acpi_mask_gpe=0x6e
        initrd /live/initrd
    }

    # Mac EFI / MacBook compatibility.
    menuentry "Ming OS ${MING_OS_VERSION} Mac EFI / MacBook 兼容模式" {
     linux /live/vmlinuz boot=live rootdelay=10 live-media-path=/live union=overlay components live-config username=${MING_USER} user-fullname=Ming_OS_User hostname=ming-os locales=zh_CN.UTF-8 timezone=Asia/Shanghai keyboard-layouts=us quiet loglevel=3 systemd.show_status=false nowatchdog zswap.enabled=1 ming.installer=1 acpi_osi=Darwin reboot=pci
        initrd /live/initrd
    }
  }
fi

GRUBCFG
}


derive_iso_context() {
    local kernel_version kernel_path initrd_path suffix
    kernel_version="$(select_latest_kernel)"
    [[ -n "${kernel_version}" ]] || {
        log_error "未找到 chroot 内核: ${CHROOT_DIR}/boot/vmlinuz-*"
        return 1
    }
    KERNEL_PATH="${CHROOT_DIR}/boot/vmlinuz-${kernel_version}"
    initrd_path="${CHROOT_DIR}/boot/initrd.img-${kernel_version}"
    if [[ ! -s "${initrd_path}" ]]; then
        initrd_path="$(find "${CHROOT_DIR}/boot" -maxdepth 1 -type f -name 'initrd.img-*' | sort -V | tail -n 1)"
    fi
    [[ -s "${initrd_path}" ]] || {
        log_error "未找到 initrd: ${CHROOT_DIR}/boot/initrd.img-*"
        return 1
    }
    INITRD_PATH="${initrd_path}"
    KERNEL_SHA="$(sha256sum "${KERNEL_PATH}" | awk '{print $1}')"
    suffix="${MING_OS_BUILD_SUFFIX}"
    ISO_NAME="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64"
    [[ -n "${suffix}" ]] && ISO_NAME+="-${suffix}"
    [[ "${MING_BUILD_PROFILE}" == "release" ]] || ISO_NAME+="-${MING_BUILD_PROFILE}"
    ISO_NAME+=".iso"
    BUILD_SIDECAR="${OUTPUT_DIR}/${ISO_NAME%.iso}.build.json"
}

stage_squashfs() {
    log_step "阶段: 生成 squashfs (${MING_BUILD_PROFILE}:${MING_BUILD_COMPRESSION})"
    rm -rf "${ISO_DIR}"
    mkdir -p "${ISO_DIR}/boot/grub" "${ISO_DIR}/boot/grub/themes/ming" "${ISO_DIR}/live"
    mkdir -p "${OUTPUT_DIR}"
    derive_iso_context
    validate_linux_kernel "${KERNEL_PATH}" "source kernel"
    require_cmd sha256sum "coreutils"
    validate_calamares_config
    validate_r4_compatibility
    # Do not include any live mount in the compressed rootfs.
    if command -v findmnt >/dev/null 2>&1; then
        if findmnt -R "${CHROOT_DIR}" 2>/dev/null | grep -Fq "${CHROOT_DIR}/"; then
            log_error "chroot still has nested mounts; refusing to compress a live mount"
            return 1
        fi
    fi
    local squashfs_partial="${ISO_DIR}/live/filesystem.squashfs.partial"
    rm -f "${squashfs_partial}" "${ISO_DIR}/live/filesystem.squashfs"
    mksquashfs "${CHROOT_DIR}" "${squashfs_partial}" "${MING_SQUASHFS_ARGS[@]}"
    [[ -s "${squashfs_partial}" ]] || {
        log_error "mksquashfs produced an empty filesystem"
        return 1
    }
    mv -f "${squashfs_partial}" "${ISO_DIR}/live/filesystem.squashfs"
    STAGE_ARTIFACTS=("${ISO_DIR}/live/filesystem.squashfs")
}

stage_boot_assets() {
    log_step "阶段: 准备 BIOS/UEFI 引导资源"
    derive_iso_context
    install -m 0644 "${SCRIPT_DIR}/assets/grub-theme/theme.txt" \
        "${ISO_DIR}/boot/grub/themes/ming/theme.txt"
    cp "${KERNEL_PATH}" "${ISO_DIR}/live/vmlinuz.partial"
    cp "${INITRD_PATH}" "${ISO_DIR}/live/initrd.partial"
    cmp -s "${KERNEL_PATH}" "${ISO_DIR}/live/vmlinuz.partial" || return 1
    mv -f "${ISO_DIR}/live/vmlinuz.partial" "${ISO_DIR}/live/vmlinuz"
    mv -f "${ISO_DIR}/live/initrd.partial" "${ISO_DIR}/live/initrd"
    validate_linux_kernel "${ISO_DIR}/live/vmlinuz" "ISO workdir /live/vmlinuz"
    write_grub_config
    validate_iso_grub_config
    mkdir -p "${ISO_DIR}/boot/grub/fonts"
    [[ -s /usr/share/grub/unicode.pf2 ]] || {
        log_error "required GRUB unicode font is missing: /usr/share/grub/unicode.pf2"
        return 1
    }
    cp /usr/share/grub/unicode.pf2 "${ISO_DIR}/boot/grub/fonts/"
    if [[ -f "${CHROOT_DIR}/boot/memtest86+x64.efi" ]]; then
        cp "${CHROOT_DIR}/boot/memtest86+x64.efi" "${ISO_DIR}/boot/"
    fi
    STAGE_ARTIFACTS=(
        "${ISO_DIR}/live/vmlinuz"
        "${ISO_DIR}/live/initrd"
        "${ISO_DIR}/boot/grub/grub.cfg"
        "${ISO_DIR}/boot/grub/themes/ming/theme.txt"
        "${ISO_DIR}/boot/grub/fonts/unicode.pf2"
    )
}

stage_iso() {
    log_step "阶段: 生成 ISO 镜像"
    derive_iso_context
    local iso_partial_name="${ISO_NAME}.partial"
    local iso_partial_path="${OUTPUT_DIR}/${iso_partial_name}"
    rm -f -- "${iso_partial_path}"
    build_iso_manual "${iso_partial_name}"
    [[ -s "${iso_partial_path}" ]] || {
        log_error "ISO 镜像生成失败"
        return 1
    }
    validate_iso_kernel "${iso_partial_path}" "${KERNEL_SHA}"
    validate_iso_boot_layout "${iso_partial_path}"
    mv -f -- "${iso_partial_path}" "${OUTPUT_DIR}/${ISO_NAME}"
    STAGE_ARTIFACTS=("${OUTPUT_DIR}/${ISO_NAME}")
}

stage_publish_artifacts() {
    log_step "阶段: 发布校验和与构建元数据"
    derive_iso_context
    local iso_sha256 iso_size xiahai_sha256 ota_key_present xiahai_hash_source
    iso_sha256="$(sha256sum "${OUTPUT_DIR}/${ISO_NAME}" | awk '{print $1}')"
    iso_size="$(stat -c '%s' "${OUTPUT_DIR}/${ISO_NAME}")"
    xiahai_hash_source="${MING_XIAHAI_DEB_SOURCE:-${SCRIPT_DIR}/assets/vendor/xiahai-xiaoming/xiahai-xiaoming_0.0.2-beta_amd64.deb}"
    xiahai_sha256="$(file_sha256_or_missing "${xiahai_hash_source}")"
    ota_key_present=false
    if [[ -s "${CHROOT_DIR}/etc/ming-update/ota-release.minisign.pub" &&
          ! -L "${CHROOT_DIR}/etc/ming-update/ota-release.minisign.pub" ]]; then
        ota_key_present=true
    fi
    printf '%s  %s\n' "${iso_sha256}" "${ISO_NAME}" > "${OUTPUT_DIR}/SHA256SUMS.partial"
    mv -f "${OUTPUT_DIR}/SHA256SUMS.partial" "${OUTPUT_DIR}/SHA256SUMS"
    printf '%s  %s\n' "${iso_sha256}" "${ISO_NAME}" > "${OUTPUT_DIR}/${ISO_NAME}.sha256.partial"
    mv -f "${OUTPUT_DIR}/${ISO_NAME}.sha256.partial" "${OUTPUT_DIR}/${ISO_NAME}.sha256"
    python3 - "${BUILD_SIDECAR}.partial" "${MING_OS_VERSION}" "${BUILD_ID}" \
        "${BUILD_SOURCE_COMMIT}" "${BUILD_TIME_UTC}" "${iso_sha256}" \
        "${iso_size}" "${MING_BUILD_PROFILE}" "${MING_BUILD_COMPRESSION}" \
        "${MING_SKIP_XIAHAI}" "${xiahai_sha256}" "${ota_key_present}" <<'PY'
import json
import pathlib
import sys
skip_xiahai = sys.argv[10] == "1"
xiahai_sha256 = sys.argv[11]
ota_key_present = sys.argv[12] == "true"
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "version": sys.argv[2], "build_id": sys.argv[3],
    "source_commit": sys.argv[4], "build_time_utc": sys.argv[5],
    "iso_sha256": sys.argv[6], "iso_size": int(sys.argv[7]),
    "profile": sys.argv[8], "compression": sys.argv[9],
    "xiahai_sha256": xiahai_sha256,
    "xiahai_included": not skip_xiahai and xiahai_sha256 != "missing",
    "ota_release_key_present": ota_key_present,
    "release_eligible": (
        sys.argv[8] == "release" and not skip_xiahai
        and xiahai_sha256 != "missing" and ota_key_present
    ),
}, ensure_ascii=True, sort_keys=True) + "\n", encoding="ascii")
PY
    mv -f "${BUILD_SIDECAR}.partial" "${BUILD_SIDECAR}"
    if [[ "${SCRIPT_DIR}" == /mnt/* ]]; then
        if [[ "${MING_BUILD_PROFILE}" == "release" ]]; then
            local win_output_dir="${SCRIPT_DIR}/output"
            mkdir -p "${win_output_dir}"
            cp "${OUTPUT_DIR}/${ISO_NAME}" "${win_output_dir}/${ISO_NAME}"
            cp "${OUTPUT_DIR}/SHA256SUMS" "${win_output_dir}/SHA256SUMS"
            cp "${OUTPUT_DIR}/${ISO_NAME}.sha256" "${win_output_dir}/${ISO_NAME}.sha256"
            cp "${BUILD_SIDECAR}" "${win_output_dir}/$(basename "${BUILD_SIDECAR}")"
            verify_build_identity
            log_info "ISO 已复制到 Windows 目录: ${win_output_dir}/${ISO_NAME}"
        else
            log_warn "${MING_BUILD_PROFILE} 仅用于内部验收，不复制到发布目录"
        fi
    elif [[ "${MING_BUILD_PROFILE}" != "release" ]]; then
        log_warn "${MING_BUILD_PROFILE} 仅用于内部验收，不复制到发布目录"
    fi
    log_info "ISO 镜像生成成功: ${OUTPUT_DIR}/${ISO_NAME} (${iso_size} bytes)"
    STAGE_ARTIFACTS=(
        "${OUTPUT_DIR}/${ISO_NAME}"
        "${OUTPUT_DIR}/SHA256SUMS"
        "${OUTPUT_DIR}/${ISO_NAME}.sha256"
        "${BUILD_SIDECAR}"
    )
}

build_iso() {
    run_stage squashfs stage_squashfs
    run_stage boot-assets stage_boot_assets
    run_stage iso stage_iso
    run_stage publish-artifacts stage_publish_artifacts
    if [[ "${MING_CLEAN_ISO_WORKDIR}" == "1" ]]; then
        rm -rf -- "${ISO_DIR}"
    else
        log_info "保留 ISO 中间目录，便于后续断点续建: ${ISO_DIR}"
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
DEFAULT ming
PROMPT 0
TIMEOUT 10
ONTIMEOUT ming
MENU TITLE Ming OS Installer

LABEL ming
  MENU LABEL Boot / Install Ming OS
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
# ======================== 分阶段主流程 ========================
stage_host_preflight() {
    check_host_environment
    install_build_deps
    verify_debootstrap_keyring
}

stage_debootstrap() {
    run_debootstrap
    write_stage_marker debootstrap
    STAGE_ARTIFACTS=(
        "$(stage_marker_path debootstrap)"
        "${CHROOT_DIR}/etc/debian_version"
    )
}

stage_prepare_chroot() {
    prepare_chroot_scripts
    write_stage_marker prepare-chroot
    STAGE_ARTIFACTS=(
        "$(stage_marker_path prepare-chroot)"
        "${CHROOT_DIR}/tmp/ming-build/modules/01_base.sh"
    )
}

stage_modules() {
    run_modules
    write_rootfs_build_identity
    write_stage_marker modules
    STAGE_ARTIFACTS=(
        "$(stage_marker_path modules)"
        "${CHROOT_DIR}/etc/ming-os-build.json"
    )
}

stage_initramfs() {
    generate_initramfs
    write_stage_marker initramfs
    STAGE_ARTIFACTS=("$(stage_marker_path initramfs)")
    while IFS= read -r initrd_path; do
        [[ -n "${initrd_path}" ]] && STAGE_ARTIFACTS+=("${initrd_path}")
    done < <(find "${CHROOT_DIR}/boot" -maxdepth 1 -type f -name 'initrd.img-*' -print)
}

stage_clean_rootfs() {
    local audit_output
    audit_output="$(chroot_exec dpkg --audit)"
    if [[ -n "${audit_output}" ]]; then
        log_error "resume build has unfinished dpkg packages"
        printf '%s\n' "${audit_output}" >&2
        return 1
    fi
    clean_chroot
    write_stage_marker clean-rootfs
    STAGE_ARTIFACTS=("$(stage_marker_path clean-rootfs)")
}

main() {
    echo -e "${GREEN}"
    echo "  ╔══════════════════════════════════════════╗"
    echo "  ║     Ming OS ${MING_OS_VERSION} Home Edition         ║"
    echo "  ║     层层精简，层层用心                    ║"
    echo "  ╚══════════════════════════════════════════╝"
    echo -e "${NC}"
    local start_time end_time duration minutes seconds
    start_time=$(date +%s)
    parse_build_args "$@"
    configure_build_profile
    acquire_build_lock
    capture_build_identity
    initialize_build_state
    trap 'build_error_trap' ERR INT TERM HUP

    run_stage host-preflight stage_host_preflight
    run_stage debootstrap stage_debootstrap

    mount_chroot
    trap 'umount_chroot' EXIT
    run_stage prepare-chroot stage_prepare_chroot
    run_stage modules stage_modules
    run_stage initramfs stage_initramfs
    run_stage clean-rootfs stage_clean_rootfs
    umount_chroot
    trap - EXIT

    verify_build_identity
    build_iso
    verify_build_identity
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
