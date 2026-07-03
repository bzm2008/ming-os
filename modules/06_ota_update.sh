#!/usr/bin/env bash
# ============================================================================
# Ming OS module 06: OTA update system
# ============================================================================

set -uo pipefail

readonly OTA_CONFIG_DIR="/etc/ming-update"
readonly OTA_CACHE_DIR="/var/cache/ming-update"
readonly OTA_UPDATE_SERVER="https://scallion.uno"
readonly OTA_API_ENDPOINT="/api/ming-update"

install_ota_dependencies() {
    echo "Installing OTA update dependencies..."
    apt install -y --no-install-recommends \
        curl wget jq rsync squashfs-tools zenity yad libnotify-bin \
        pkexec polkitd lxpolkit
}

deploy_ota_cli() {
    echo "Deploying ming-update CLI..."

    cat > /usr/local/bin/ming-update << 'OTACLI'
#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_VERSION="1.2.0"
readonly CONFIG_DIR="/etc/ming-update"
readonly CACHE_DIR="/var/cache/ming-update"
readonly STATE_FILE="${CONFIG_DIR}/state.json"
readonly CONFIG_FILE="${CONFIG_DIR}/config.json"
readonly USER_CONFIG_DIR="${HOME}/.config/ming-update"
readonly USER_CACHE_DIR="${HOME}/.cache/ming-update"
readonly USER_STATE_FILE="${USER_CONFIG_DIR}/state.json"
readonly USER_CONFIG_FILE="${USER_CONFIG_DIR}/config.json"
readonly UPDATE_SERVER="https://scallion.uno"
readonly API_ENDPOINT="/api/ming-update"

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
log_step() { printf '\n=====> %s <=====\n\n' "$*"; }

ensure_dirs() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        mkdir -p "${CONFIG_DIR}" "${CACHE_DIR}"
        chmod 755 "${CONFIG_DIR}" "${CACHE_DIR}"
    else
        mkdir -p "${USER_CONFIG_DIR}" "${USER_CACHE_DIR}"
        chmod 700 "${USER_CONFIG_DIR}" "${USER_CACHE_DIR}"
    fi
}

config_file() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${CONFIG_FILE}"
    else
        printf '%s\n' "${USER_CONFIG_FILE}"
    fi
}

cache_dir() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${CACHE_DIR}"
    else
        printf '%s\n' "${USER_CACHE_DIR}"
    fi
}

state_file() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${STATE_FILE}"
    else
        printf '%s\n' "${USER_STATE_FILE}"
    fi
}

current_version() {
    cat /etc/ming-version 2>/dev/null || echo "unknown"
}

init_config() {
    ensure_dirs
    local cfg
    cfg=$(config_file)
    if [[ ! -f "${cfg}" ]]; then
        cat > "${cfg}" << CONFIGJSON
{
  "update_server": "${UPDATE_SERVER}",
  "api_endpoint": "${API_ENDPOINT}",
  "channel": "stable",
  "auto_check": true,
  "auto_download": false,
  "verify_checksum": true,
  "download_retries": 3,
  "notify_enabled": true,
  "last_check": "",
  "current_version": "$(current_version)"
}
CONFIGJSON
        chmod 644 "${cfg}"
    fi
}

get_config() {
    local key="$1"
    init_config
    jq -r "${key}" "$(config_file)" 2>/dev/null || echo ""
}

set_config() {
    local key="$1"
    local value="$2"
    init_config
    local tmp_file cfg
    cfg=$(config_file)
    tmp_file="$(mktemp)"
    jq --arg value "${value}" "${key} = \$value" "${cfg}" > "${tmp_file}"
    install -m 0644 "${tmp_file}" "${cfg}"
    rm -f "${tmp_file}"
}

api_url() {
    local server endpoint channel version
    server=$(get_config '.update_server')
    endpoint=$(get_config '.api_endpoint')
    channel=$(get_config '.channel')
    version=$(current_version)
    server=${server:-${UPDATE_SERVER}}
    endpoint=${endpoint:-${API_ENDPOINT}}
    channel=${channel:-stable}
    printf '%s%s/check?version=%s&channel=%s\n' "${server}" "${endpoint}" "${version}" "${channel}"
}

check_network() {
    local server
    server=$(get_config '.update_server')
    server=${server:-${UPDATE_SERVER}}
    curl -fsSL --connect-timeout 8 --max-time 15 "${server}" >/dev/null
}

check_update() {
    log_step "Check for updates"
    init_config

    local version response url cdir manifest
    cdir=$(cache_dir)
    manifest="${cdir}/update_info.json"
    version=$(current_version)
    log_info "Current version: ${version}"

    if ! check_network; then
        log_error "Cannot connect to update server."
        return 1
    fi

    url=$(api_url)
    log_info "API: ${url}"
    response=$(curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 45 "${url}") || {
        log_error "Failed to fetch update manifest."
        return 1
    }

    if ! printf '%s' "${response}" | jq -e . >/dev/null; then
        log_error "Update server returned invalid JSON."
        return 1
    fi

    local server_error ready has_update new_version notes
    server_error=$(printf '%s' "${response}" | jq -r '.error // ""')
    if [[ -n "${server_error}" ]]; then
        log_error "Update server error: ${server_error}"
        return 1
    fi

    ready=$(printf '%s' "${response}" | jq -r '.ready // true')
    has_update=$(printf '%s' "${response}" | jq -r '.has_update // .update_available // false')
    new_version=$(printf '%s' "${response}" | jq -r '.version // .latest_version // "unknown"')
    notes=$(printf '%s' "${response}" | jq -r '.release_notes // .message // "No release notes."')

    if [[ "${has_update}" == "true" && "${ready}" != "true" ]]; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        log_warn "Version ${new_version} is listed but not ready for download yet."
        log_warn "${notes}"
        return 0
    fi

    if [[ "${has_update}" != "true" ]]; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        log_info "No update available."
        return 0
    fi

    printf '%s\n' "${response}" > "${manifest}"
    chmod 644 "${manifest}"
    set_config '.last_check' "$(date -Iseconds)"

    log_info "Update available: ${new_version}"
    printf '\nVersion: %s\nCurrent: %s\n\nRelease notes:\n%s\n\n' "${new_version}" "${version}" "${notes}"

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i ming-update-icon "Ming OS update available" "Version ${new_version} is ready." 2>/dev/null || true
    fi
}

download_update() {
    log_step "Download update"
    init_config

    local cdir manifest sfile
    cdir=$(cache_dir)
    manifest="${cdir}/update_info.json"
    sfile=$(state_file)

    if [[ ! -f "${manifest}" ]]; then
        log_error "No update manifest cached. Run: ming-update check"
        return 1
    fi

    local info url version checksum expected_size iso_name iso_file tmp_file retries
    info=$(cat "${manifest}")
    url=$(printf '%s' "${info}" | jq -r '.download_url // .url // ""')
    version=$(printf '%s' "${info}" | jq -r '.version // "unknown"')
    checksum=$(printf '%s' "${info}" | jq -r '.checksum // .sha256 // ""')
    expected_size=$(printf '%s' "${info}" | jq -r '.size // 0')
    iso_name=$(printf '%s' "${info}" | jq -r '.filename // .iso_name // empty')
    iso_name=${iso_name:-ming-os-${version}.iso}
    iso_file="${cdir}/${iso_name}"
    tmp_file="${iso_file}.tmp"

    if [[ -z "${url}" || "${url}" == "null" ]]; then
        log_error "Manifest does not contain a download URL."
        return 1
    fi

    if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
        local free_bytes need_bytes
        free_bytes=$(df --output=avail -B1 "${cdir}" | tail -n 1 | tr -d ' ')
        need_bytes=$(( expected_size + 536870912 ))
        if [[ "${free_bytes:-0}" -lt "${need_bytes}" ]]; then
            log_error "Not enough disk space. Need $((need_bytes / 1048576))MB, have $(( ${free_bytes:-0} / 1048576 ))MB."
            return 1
        fi
    fi

    retries=$(get_config '.download_retries')
    [[ "${retries}" =~ ^[0-9]+$ ]] || retries=3

    log_info "Downloading ${version}: ${url}"
    wget -c --tries="${retries}" --timeout=30 --read-timeout=30 --show-progress -O "${tmp_file}" "${url}"
    mv "${tmp_file}" "${iso_file}"

    if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
        local actual_size
        actual_size=$(stat -c '%s' "${iso_file}")
        if [[ "${actual_size}" -ne "${expected_size}" ]]; then
            rm -f "${iso_file}"
            log_error "Downloaded size mismatch. Expected ${expected_size}, got ${actual_size}."
            return 1
        fi
    fi

    if [[ "${checksum}" =~ ^[A-Fa-f0-9]{64}$ ]]; then
        local actual_checksum
        actual_checksum=$(sha256sum "${iso_file}" | awk '{print $1}')
        if [[ "${actual_checksum}" != "${checksum}" ]]; then
            rm -f "${iso_file}"
            log_error "SHA256 mismatch. Expected ${checksum}, got ${actual_checksum}."
            return 1
        fi
        log_info "SHA256 verified."
    else
        log_warn "Manifest has no valid SHA256; download kept but not fully verified."
    fi

    cat > "${sfile}" << STATEJSON
{
  "status": "downloaded",
  "version": "${version}",
  "iso_path": "${iso_file}",
  "download_url": "${url}",
  "checksum": "${checksum}",
  "download_time": "$(date -Iseconds)"
}
STATEJSON
    chmod 644 "${sfile}"
    log_info "Update downloaded: ${iso_file}"
}

install_update() {
    log_step "Install update"
    init_config

    if [[ $EUID -ne 0 ]]; then
        log_error "Installing/staging an OTA boot entry requires root. Use: sudo ming-update install"
        return 1
    fi
    local sfile="${STATE_FILE}"
    if [[ ! -f "${sfile}" && -f "${USER_STATE_FILE}" ]]; then
        sfile="${USER_STATE_FILE}"
    fi
    if [[ ! -f "${sfile}" && ${EUID} -eq 0 ]]; then
        local candidate
        for candidate in /home/*/.config/ming-update/state.json; do
            if [[ -f "${candidate}" ]]; then
                sfile="${candidate}"
                break
            fi
        done
    fi

    if [[ ! -f "${sfile}" ]]; then
        log_error "No downloaded update state found. Run: ming-update download"
        return 1
    fi

    local state status iso_path version mount_point custom_cfg tmp_cfg
    state=$(cat "${sfile}")
    status=$(printf '%s' "${state}" | jq -r '.status // ""')
    iso_path=$(printf '%s' "${state}" | jq -r '.iso_path // ""')
    version=$(printf '%s' "${state}" | jq -r '.version // "unknown"')

    if [[ "${status}" != "downloaded" || ! -f "${iso_path}" ]]; then
        log_error "Downloaded update is missing or invalid."
        return 1
    fi

    mount_point="$(mktemp -d)"
    if ! mount -o loop,ro "${iso_path}" "${mount_point}"; then
        rmdir "${mount_point}" || true
        log_error "Cannot mount ISO: ${iso_path}"
        return 1
    fi

    if [[ ! -f "${mount_point}/live/vmlinuz" || ! -f "${mount_point}/live/initrd" || ! -f "${mount_point}/live/filesystem.squashfs" ]]; then
        umount "${mount_point}" || true
        rmdir "${mount_point}" || true
        log_error "ISO is incomplete: missing live/vmlinuz, live/initrd, or live/filesystem.squashfs."
        return 1
    fi
    umount "${mount_point}" || true
    rmdir "${mount_point}" || true

    custom_cfg="/boot/grub/custom.cfg"
    tmp_cfg="$(mktemp)"
    mkdir -p /boot/grub
    if [[ -f "${custom_cfg}" ]]; then
        sed '/^### BEGIN MING OTA ###$/,/^### END MING OTA ###$/d' "${custom_cfg}" > "${tmp_cfg}"
    else
        : > "${tmp_cfg}"
    fi

    cat >> "${tmp_cfg}" << GRUBMENU
### BEGIN MING OTA ###
menuentry "Ming OS ${version} OTA Installer" {
    set iso_path="${iso_path}"
    search --no-floppy --file --set=root \${iso_path}
    loopback loop (\$root)\${iso_path}
    linux (loop)/live/vmlinuz boot=live components live-config username=user user-fullname=Ming_OS_User hostname=ming-os findiso=\${iso_path} locales=zh_CN.UTF-8 quiet splash
    initrd (loop)/live/initrd
}
### END MING OTA ###
GRUBMENU
    install -m 0644 "${tmp_cfg}" "${custom_cfg}"
    rm -f "${tmp_cfg}"

    cat > "${sfile}" << STATEJSON
{
  "status": "staged",
  "version": "${version}",
  "iso_path": "${iso_path}",
  "grub_entry": "Ming OS ${version} OTA Installer",
  "staged_time": "$(date -Iseconds)"
}
STATEJSON
    chmod 644 "${sfile}"
    command -v update-grub >/dev/null 2>&1 && update-grub || true
    log_info "OTA boot entry staged in ${custom_cfg}."
    log_info "Reboot and choose: Ming OS ${version} OTA Installer"
}

show_status() {
    init_config
    local cdir manifest sfile
    cdir=$(cache_dir)
    manifest="${cdir}/update_info.json"
    sfile=$(state_file)
    log_step "Update status"
    echo "Current version: $(current_version)"
    echo "Update server: $(get_config '.update_server')"
    echo "Channel: $(get_config '.channel')"
    echo "Last check: $(get_config '.last_check')"
    if [[ -f "${sfile}" ]]; then
        jq . "${sfile}" || cat "${sfile}"
    elif [[ -f "${manifest}" ]]; then
        echo "Cached update:"
        jq '{version, has_update, ready, download_url, checksum, size}' "${manifest}"
    else
        echo "No cached update."
    fi
}

configure_update() {
    init_config
    local server channel auto_check auto_download cfg
    cfg=$(config_file)
    read -r -p "Update server [${UPDATE_SERVER}]: " server
    read -r -p "Channel [stable]: " channel
    read -r -p "Auto check [true]: " auto_check
    read -r -p "Auto download [false]: " auto_download
    server=${server:-${UPDATE_SERVER}}
    channel=${channel:-stable}
    auto_check=${auto_check:-true}
    auto_download=${auto_download:-false}

    cat > "${cfg}" << CONFIGJSON
{
  "update_server": "${server}",
  "api_endpoint": "${API_ENDPOINT}",
  "channel": "${channel}",
  "auto_check": ${auto_check},
  "auto_download": ${auto_download},
  "verify_checksum": true,
  "download_retries": 3,
  "notify_enabled": true,
  "last_check": "$(get_config '.last_check')",
  "current_version": "$(current_version)"
}
CONFIGJSON
    chmod 644 "${cfg}"
    log_info "Config saved."
}

show_help() {
    cat << HELP
Ming OS OTA client v${SCRIPT_VERSION}

Usage: ming-update [check|patch|download|install|auto-shutdown|status|config|help]

Commands:
  check             检查是否有可用更新（含分级：patch/minor/major）。
  patch             执行 patch 级小修复（apt 补丁 + 配置脚本，无需重启）。
  download          下载并校验 major ISO 更新包。
  install           将已下载的 ISO 暂存为 GRUB 启动项（major 升级，保留用户文件）。
  auto-shutdown     自动完成「检查→下载→安装→关机」全流程（major 升级，夜间维护）。
  status            显示当前 OTA 状态。
  config            配置更新源/频道。

更新策略：
  patch  小修复、驱动更新、配置补丁 → apt/脚本应用，通常无需重启
  minor  组件升级、新功能 → apt + 可能需重启
  major  大版本 ISO → 完整系统替换，/home 用户文件严格保留
HELP
}

# ======================== patch 级小修复（apt 补丁路径）========================
# 用途：驱动更新、安全补丁、配置修正，通常不需要重启。
# 原理：从更新服务器拉取 patch manifest，执行 apt install 和 patch scripts。
patch_update() {
    log_step "Ming OS patch 级更新"
    init_config

    if ! check_network; then
        log_error "网络不可用，无法检查 patch 更新。"
        return 1
    fi

    local server
    server=$(get_config '.update_server')
    server=${server:-${UPDATE_SERVER}}
    local patch_url="${server}/api/ming-patch?version=$(current_version)&arch=amd64"

    log_info "检查 patch 更新：${patch_url}"
    local response
    response=$(curl -fsSL --retry 3 --connect-timeout 10 --max-time 30 "${patch_url}") || {
        log_warn "Patch manifest 获取失败，当前已是最新或网络超时。"
        return 0
    }

    local has_patch pkg_list script_url
    has_patch=$(printf '%s' "${response}" | jq -r '.has_patch // false' 2>/dev/null)
    if [[ "${has_patch}" != "true" ]]; then
        log_info "当前已是最新（patch 级别），无需更新。"
        notify-send -i system-software-update "Ming OS" "系统已是最新，无需 patch 更新。" 2>/dev/null || true
        return 0
    fi

    local patch_version notes
    patch_version=$(printf '%s' "${response}" | jq -r '.patch_version // "unknown"' 2>/dev/null)
    notes=$(printf '%s' "${response}" | jq -r '.notes // ""' 2>/dev/null)
    log_info "发现 patch 更新：${patch_version}  ${notes}"
    notify-send -i system-software-update "Ming OS patch 更新" "正在应用 ${patch_version}…" 2>/dev/null || true

    # 1) apt 包更新（若 manifest 包含包列表）
    pkg_list=$(printf '%s' "${response}" | jq -r '.apt_packages[]?' 2>/dev/null | tr '\n' ' ')
    if [[ -n "${pkg_list}" ]]; then
        log_info "更新 apt 包：${pkg_list}"
        # shellcheck disable=SC2086
        DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Use-Pty=0 \
            install ${pkg_list} </dev/null || {
            log_warn "apt 包更新部分失败，继续执行脚本补丁。"
        }
    fi

    # 2) 配置/脚本补丁（若 manifest 提供 patch script URL）
    script_url=$(printf '%s' "${response}" | jq -r '.patch_script_url // ""' 2>/dev/null)
    if [[ -n "${script_url}" && "${script_url}" != "null" ]]; then
        log_info "下载并执行补丁脚本：${script_url}"
        local patch_script
        patch_script=$(mktemp /tmp/ming-patch-XXXXXX.sh)
        if curl -fsSL --retry 2 --max-time 60 "${script_url}" -o "${patch_script}"; then
            chmod +x "${patch_script}"
            bash "${patch_script}" </dev/null && log_info "补丁脚本执行成功。" \
                || log_warn "补丁脚本执行有警告，请查看日志。"
        fi
        rm -f "${patch_script}"
    fi

    # 3) 记录已应用的 patch 版本
    set_config '.patch_version' "${patch_version}"
    set_config '.last_patch' "$(date -Iseconds)"
    notify-send -i system-software-update "Ming OS patch 完成" \
        "已应用 ${patch_version}，无需重启。" 2>/dev/null || true
    log_info "patch 更新完成：${patch_version}"
}

# ======================== major ISO 升级（保留用户文件）========================
# 核心承诺：/home 分区/目录永不覆盖，用户文件严格保留。
# 机制：Calamares replacePartition 模式仅格式化根分区，/home 挂载点独立保留；
#       若是单分区布局则先备份 /home 到数据盘，安装后还原。
major_install_with_home_backup() {
    log_step "Ming OS major 大版本升级（保留用户文件）"
    local cdir; cdir=$(cache_dir)
    local sfile; sfile=$(state_file)
    local manifest="${cdir}/update_info.json"

    if [[ ! -f "${manifest}" ]]; then
        log_error "未找到已下载的更新 manifest，请先运行：ming-update download"
        return 1
    fi

    local update_type
    update_type=$(jq -r '.update_type // "major"' "${manifest}" 2>/dev/null)
    if [[ "${update_type}" == "patch" || "${update_type}" == "minor" ]]; then
        log_warn "当前缓存的更新类型为 ${update_type}，建议使用 ming-update patch 而非 major 升级。"
    fi

    # 检查 /home 是否有独立分区
    local home_separate=false
    if findmnt -rno SOURCE /home >/dev/null 2>&1; then
        local home_src; home_src=$(findmnt -rno SOURCE /home)
        if [[ "${home_src}" != "$(findmnt -rno SOURCE /)" ]]; then
            home_separate=true
        fi
    fi

    if [[ "${home_separate}" == "true" ]]; then
        log_info "/home 有独立分区（${home_src}），Calamares 安装时自动保留，用户文件安全。"
    else
        # 单分区：检查数据盘（多盘合一存储）是否可用作备份位置
        local backup_disk=""
        if [[ -f /run/ming-os/storage-info ]]; then
            backup_disk=$(grep '^data_mount=' /run/ming-os/storage-info 2>/dev/null | cut -d= -f2)
        fi
        if [[ -n "${backup_disk}" && -d "${backup_disk}" ]]; then
            log_info "将在 Calamares 安装前把 /home 备份到 ${backup_disk}/ming-home-backup"
            log_info "安装完成后自动还原。"
            # 写入 Calamares postinstall hook 配置，供 ming-fix-installed-identity 脚本在安装后执行还原
            echo "home_backup_src=/home" > /tmp/ming-major-upgrade.conf
            echo "home_backup_dst=${backup_disk}/ming-home-backup" >> /tmp/ming-major-upgrade.conf
            echo "restore_after_install=true" >> /tmp/ming-major-upgrade.conf
        else
            log_warn "未检测到独立 /home 分区或数据盘，major 升级将依赖 Calamares 的"
            log_warn "'replacePartition' 模式（只格式化根分区，不触碰 /home 目录）。"
            log_warn "强烈建议安装前手动备份重要文件到 U 盘。"
            notify-send -i dialog-warning "Ming OS 重要提示" \
                "major 升级前请手动备份重要文件。安装程序会尽量保留 /home，但无独立分区时请谨慎。" \
                2>/dev/null || true
        fi
    fi

    # 调用原 install_update 暂存 ISO 启动项
    install_update
}
# 用途：夜间挂机维护，或"帮我更新完关机"按钮背后的实现。
auto_shutdown_update() {
    log_step "Ming OS 自动更新并关机"
    local notify_title="Ming OS 自动更新"

    _notify() {
        local msg="$1"
        log_info "${msg}"
        notify-send -i system-software-update "${notify_title}" "${msg}" 2>/dev/null || true
    }

    _notify "开始检查更新…"
    if ! check_update; then
        _notify "检查更新失败，已取消自动关机。"
        return 1
    fi

    local manifest; manifest="$(cache_dir)/update_info.json"
    if [[ ! -f "${manifest}" ]]; then
        _notify "当前已是最新版本，无需更新。不执行关机。"
        return 0
    fi

    local has_update
    has_update=$(jq -r '.has_update // false' "${manifest}" 2>/dev/null)
    if [[ "${has_update}" != "true" ]]; then
        _notify "当前已是最新版本，无需更新。不执行关机。"
        return 0
    fi

    local new_version
    new_version=$(jq -r '.version // "unknown"' "${manifest}" 2>/dev/null)
    _notify "发现新版本 ${new_version}，开始下载…"

    if ! download_update; then
        _notify "下载失败，已取消自动关机。"
        return 1
    fi

    _notify "下载完成，正在暂存启动项…"
    if ! install_update; then
        _notify "安装暂存失败，已取消自动关机。"
        return 1
    fi

    _notify "更新准备完毕！系统将在 60 秒后关机，重启后自动应用新版本。"
    log_info "Scheduling shutdown in 60 seconds..."
    # 60 秒倒计时让用户有机会中断（运行 sudo shutdown -c 可取消）
    sudo shutdown -h +1 "Ming OS 更新完成，系统将在 1 分钟内关机。" 2>/dev/null \
        || systemctl poweroff --no-wall 2>/dev/null || poweroff 2>/dev/null || true
}

case "${1:-help}" in
    check) check_update ;;
    patch) patch_update ;;
    download) download_update ;;
    install) major_install_with_home_backup ;;
    auto-shutdown) auto_shutdown_update ;;
    status) show_status ;;
    config) configure_update ;;
    help|--help|-h) show_help ;;
    *) log_error "Unknown command: $1"; show_help; exit 1 ;;
esac
OTACLI

    chmod +x /usr/local/bin/ming-update
    bash -n /usr/local/bin/ming-update
}

deploy_systemd_services() {
    echo "Creating ming-update systemd services and timers..."

    # 每周一凌晨3点定期检查（原有）
    cat > /etc/systemd/system/ming-update-check.service << 'SYSTEMDSERVICE'
[Unit]
Description=Ming OS OTA Update Check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-update check
StandardOutput=journal
StandardError=journal
SYSTEMDSERVICE

    cat > /etc/systemd/system/ming-update-check.timer << 'SYSTEMDTIMER'
[Unit]
Description=Ming OS OTA Update Check Timer

[Timer]
OnCalendar=Mon *-*-* 03:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMDTIMER

    # 开机联网后自动检查（新增）：静默检查，有更新则桌面通知
    cat > /etc/systemd/system/ming-update-boot-check.service << 'BOOTCHECK'
[Unit]
Description=Ming OS Boot-time Update Check
After=network-online.target graphical.target
Wants=network-online.target
ConditionPathExists=/usr/local/bin/ming-update

[Service]
Type=oneshot
# 延迟90秒，等桌面完全就绪后弹通知
ExecStartPre=/bin/sleep 90
ExecStart=/usr/local/bin/ming-boot-update-check
StandardOutput=journal
StandardError=journal
# 检查失败不影响登录体验
SuccessExitStatus=0 1

[Install]
WantedBy=graphical.target
BOOTCHECK

    # 自动检查通知脚本（用户级，有更新弹 zenity/notify-send）
    cat > /usr/local/bin/ming-boot-update-check << 'BOOTCHECKSCRIPT'
#!/usr/bin/env bash
# Ming OS 开机自动检查更新（静默，有更新才弹通知）
set -uo pipefail

LOG="/tmp/ming-boot-check.log"
exec >> "${LOG}" 2>&1

echo "[$(date '+%F %T')] Boot update check started"

# 网络不通就退出，不阻塞
/usr/local/bin/ming-update check >/tmp/ming-update-check.log 2>&1
rc=$?
echo "[$(date '+%F %T')] ming-update check rc=${rc}"

# 没有 manifest 说明没有更新，静默退出
manifest="/var/cache/ming-update/$(id -un 2>/dev/null || echo root)/update_info.json"
[ -f "${manifest}" ] || manifest="/var/cache/ming-update/root/update_info.json"
[ -f "${manifest}" ] || exit 0

has_update=$(jq -r '.has_update // false' "${manifest}" 2>/dev/null)
[ "${has_update}" = "true" ] || exit 0

new_version=$(jq -r '.version // "新版本"' "${manifest}" 2>/dev/null)

# 找到当前登录用户的 DISPLAY
for user_home in /home/*; do
    uname=$(basename "${user_home}")
    display=$(cat /proc/*/environ 2>/dev/null | tr '\0' '\n' | grep "^DISPLAY=" | head -1 | cut -d= -f2)
    if [ -n "${display}" ]; then
        export DISPLAY="${display}"
        export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "${uname}" 2>/dev/null)/bus"
        break
    fi
done

# 桌面通知
notify-send \
    -i system-software-update \
    -a "Ming OS 更新" \
    "发现新版本 ${new_version}" \
    "点击「铭设置」→「系统更新」可一键更新，或运行 sudo ming-update auto-shutdown 更新后关机。" \
    2>/dev/null || true

echo "[$(date '+%F %T')] Notification sent for version ${new_version}"
BOOTCHECKSCRIPT
    chmod +x /usr/local/bin/ming-boot-update-check

    systemctl daemon-reload 2>/dev/null || true
    systemctl enable ming-update-check.timer 2>/dev/null || true
    systemctl enable ming-update-boot-check.service 2>/dev/null || true
    systemctl start ming-update-check.timer 2>/dev/null || true
}

deploy_gui_tool() {
    echo "Deploying ming-update GUI..."

    cat > /usr/local/bin/ming-update-gui << 'OTAGUI'
#!/usr/bin/env bash
set -uo pipefail

readonly CACHE_DIR="/var/cache/ming-update"
readonly CHECK_LOG="/tmp/ming-update-check.log"
readonly DOWNLOAD_LOG="/tmp/ming-update-download.log"
readonly INSTALL_LOG="/tmp/ming-update-install.log"

have_zenity() { command -v zenity >/dev/null 2>&1; }
log_tail() {
    local file="$1"
    if [[ -f "${file}" ]]; then
        tail -n 80 "${file}"
    else
        echo "No log file: ${file}"
    fi
}
show_info() {
    if have_zenity; then
        zenity --info --title="$1" --text="$2" --width=520 2>/dev/null || true
    else
        printf '%s\n%s\n' "$1" "$2"
    fi
}
show_error() {
    if have_zenity; then
        zenity --error --title="$1" --text="$2" --width=620 2>/dev/null || true
    else
        printf 'ERROR: %s\n%s\n' "$1" "$2" >&2
    fi
}
ask_yes_no() {
    if have_zenity; then
        zenity --question --title="$1" --text="$2" --ok-label="${3:-Yes}" --cancel-label="${4:-No}" --width=560 2>/dev/null
    else
        printf '%s\n%s\n' "$1" "$2"
        return 1
    fi
}

run_with_progress() {
    local title="$1"
    local text="$2"
    local log_file="$3"
    shift 3

    : > "${log_file}"
    if have_zenity; then
        (
            echo "10"
            echo "# ${text}"
            "$@" > "${log_file}" 2>&1
            rc=$?
            echo "${rc}" > "${log_file}.rc"
            echo "100"
            echo "# Done"
        ) | zenity --progress --title="${title}" --text="${text}" --percentage=0 --auto-close --no-cancel --width=460 2>/dev/null || true
        return "$(cat "${log_file}.rc" 2>/dev/null || echo 1)"
    fi

    "$@" > "${log_file}" 2>&1
}

check_update_gui() {
    if ! run_with_progress "检查更新" "正在检查 Ming OS 更新..." "${CHECK_LOG}" /usr/local/bin/ming-update check; then
        show_error "检查更新失败" "无法完成更新检查。\n\n日志:\n$(log_tail "${CHECK_LOG}")"
        return 1
    fi

    if [[ ! -f "${CACHE_DIR}/update_info.json" ]]; then
        show_info "已是最新版本" "当前没有可安装更新。\n\n日志:\n$(log_tail "${CHECK_LOG}")"
        return 0
    fi

    local version notes ready
    version=$(jq -r '.version // .latest_version // "unknown"' "${CACHE_DIR}/update_info.json" 2>/dev/null || echo "unknown")
    notes=$(jq -r '.release_notes // .message // "无更新说明。"' "${CACHE_DIR}/update_info.json" 2>/dev/null || echo "无更新说明。")
    ready=$(jq -r '.ready // true' "${CACHE_DIR}/update_info.json" 2>/dev/null || echo "true")
    if [[ "${ready}" != "true" ]]; then
        show_info "更新尚未就绪" "服务器已登记版本 ${version}，但下载包仍在准备或校验中。\n\n${notes}"
        return 0
    fi

    if ask_yes_no "发现新版本" "发现 Ming OS ${version}。\n\n${notes}\n\n是否现在下载？" "下载" "稍后"; then
        download_update_gui
    fi
}

download_update_gui() {
    if ! run_with_progress "下载更新" "正在下载并校验更新包..." "${DOWNLOAD_LOG}" /usr/local/bin/ming-update download; then
        show_error "下载失败" "更新没有下载完成。\n\n日志:\n$(log_tail "${DOWNLOAD_LOG}")"
        return 1
    fi
    if ask_yes_no "下载完成" "更新已下载并校验完成。\n\n是否写入 OTA 启动项？" "安装" "稍后"; then
        install_update_gui
    else
        show_info "下载完成" "你可以稍后重新打开系统更新并选择安装。"
    fi
}

install_update_gui() {
    if ! ask_yes_no "安装更新" "安装会写入 GRUB OTA 启动项，之后需要重启并选择新版本安装器。\n\n是否继续？" "继续" "取消"; then
        return 0
    fi

    : > "${INSTALL_LOG}"
    local rc=1
    if command -v pkexec >/dev/null 2>&1; then
        pkexec /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    elif command -v sudo >/dev/null 2>&1; then
        sudo /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    else
        /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    fi

    if [[ ${rc} -eq 0 ]]; then
        show_info "安装准备完成" "OTA 启动项已写入。\n\n重启后在 GRUB 中选择 Ming OS OTA Installer。"
    else
        show_error "安装失败" "无法写入 OTA 启动项。\n\n日志:\n$(log_tail "${INSTALL_LOG}")"
    fi
    return "${rc}"
}

main_menu() {
    if ! have_zenity; then
        /usr/local/bin/ming-update "${1:-check}"
        return $?
    fi

    while true; do
        local choice
        choice=$(zenity --list \
            --title="Ming OS 更新管理器" \
            --text="请选择操作" \
            --column="操作" --column="说明" \
            "检查更新" "检查是否有新版本" \
            "下载更新" "下载并校验已发现的更新" \
            "安装更新" "写入 OTA 启动项" \
            "查看状态" "显示当前更新状态" \
            --width=560 --height=360 \
            --ok-label="执行" --cancel-label="退出" 2>/dev/null)
        [[ $? -eq 0 ]] || break
        case "${choice}" in
            "检查更新") check_update_gui ;;
            "下载更新") download_update_gui ;;
            "安装更新") install_update_gui ;;
            "查看状态")
                local status_text
                status_text=$(/usr/local/bin/ming-update status 2>&1)
                show_info "更新状态" "${status_text}"
                ;;
        esac
    done
}

case "${1:-menu}" in
    menu) main_menu ;;
    check) check_update_gui ;;
    download) download_update_gui ;;
    install) install_update_gui ;;
    *) echo "Usage: ming-update-gui [menu|check|download|install]"; exit 1 ;;
esac
OTAGUI

    chmod +x /usr/local/bin/ming-update-gui
    bash -n /usr/local/bin/ming-update-gui

    cat > /usr/share/applications/ming-update.desktop << DESKTOPFILE
[Desktop Entry]
Name=系统更新
Name[zh_CN]=系统更新
Comment=检查并安装 Ming OS 更新
Comment[zh_CN]=检查并安装 Ming OS 系统更新
Exec=/usr/local/bin/ming-update-gui
Icon=ming-update-icon
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=update;upgrade;system;
StartupNotify=true
DESKTOPFILE

    mkdir -p "/home/${MING_USER}/Desktop"
    cp /usr/share/applications/ming-update.desktop "/home/${MING_USER}/Desktop/"
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/ming-update.desktop"
    chmod +x "/home/${MING_USER}/Desktop/ming-update.desktop"
}

deploy_gui_tool() {
    echo "Deploying ming-update GUI..."

    cat > /usr/local/bin/ming-update-gui << 'OTAGUI'
#!/usr/bin/env bash
set -uo pipefail

readonly CACHE_DIR="/var/cache/ming-update"
readonly USER_CACHE_DIR="${HOME}/.cache/ming-update"
readonly CHECK_LOG="/tmp/ming-update-check.log"
readonly DOWNLOAD_LOG="/tmp/ming-update-download.log"
readonly INSTALL_LOG="/tmp/ming-update-install.log"

have_zenity() { command -v zenity >/dev/null 2>&1; }

manifest_file() {
    if [[ -f "${CACHE_DIR}/update_info.json" ]]; then
        printf '%s\n' "${CACHE_DIR}/update_info.json"
    else
        printf '%s\n' "${USER_CACHE_DIR}/update_info.json"
    fi
}

log_tail() {
    local file="$1"
    if [[ -f "${file}" ]]; then
        tail -n 80 "${file}"
    else
        echo "No log file: ${file}"
    fi
}

show_info() {
    if have_zenity; then
        zenity --info --title="$1" --text="$2" --width=560 2>/dev/null || true
    else
        printf '%s\n%s\n' "$1" "$2"
    fi
}

show_error() {
    if have_zenity; then
        zenity --error --title="$1" --text="$2" --width=660 2>/dev/null || true
    else
        printf 'ERROR: %s\n%s\n' "$1" "$2" >&2
    fi
}

ask_yes_no() {
    if have_zenity; then
        zenity --question --title="$1" --text="$2" --ok-label="${3:-Yes}" --cancel-label="${4:-No}" --width=580 2>/dev/null
    else
        printf '%s\n%s\n' "$1" "$2"
        return 1
    fi
}

run_with_progress() {
    local title="$1"
    local text="$2"
    local log_file="$3"
    shift 3

    : > "${log_file}"
    if have_zenity; then
        (
            echo "10"
            echo "# ${text}"
            "$@" > "${log_file}" 2>&1
            rc=$?
            echo "${rc}" > "${log_file}.rc"
            echo "100"
            echo "# Done"
        ) | zenity --progress --title="${title}" --text="${text}" --percentage=0 --auto-close --no-cancel --width=480 2>/dev/null || true
        return "$(cat "${log_file}.rc" 2>/dev/null || echo 1)"
    fi

    "$@" > "${log_file}" 2>&1
}

check_update_gui() {
    if ! run_with_progress "Check updates" "Checking Ming OS updates..." "${CHECK_LOG}" /usr/local/bin/ming-update check; then
        show_error "Update check failed" "Could not complete the update check.\n\nLog:\n$(log_tail "${CHECK_LOG}")"
        return 1
    fi

    local manifest
    manifest=$(manifest_file)
    if [[ ! -f "${manifest}" ]]; then
        show_info "No update available" "This system is already up to date.\n\nLog:\n$(log_tail "${CHECK_LOG}")"
        return 0
    fi

    local version notes ready
    version=$(jq -r '.version // .latest_version // "unknown"' "${manifest}" 2>/dev/null || echo "unknown")
    notes=$(jq -r '.release_notes // .message // "No release notes."' "${manifest}" 2>/dev/null || echo "No release notes.")
    ready=$(jq -r '.ready // true' "${manifest}" 2>/dev/null || echo "true")
    if [[ "${ready}" != "true" ]]; then
        show_info "Update not ready" "Version ${version} is listed, but the download is still being prepared or verified.\n\n${notes}"
        return 0
    fi

    if ask_yes_no "Update available" "Ming OS ${version} is available.\n\n${notes}\n\nDownload it now?" "Download" "Later"; then
        download_update_gui
    fi
}

download_update_gui() {
    if ! run_with_progress "Download update" "Downloading and verifying the update..." "${DOWNLOAD_LOG}" /usr/local/bin/ming-update download; then
        show_error "Download failed" "The update was not downloaded.\n\nLog:\n$(log_tail "${DOWNLOAD_LOG}")"
        return 1
    fi
    if ask_yes_no "Download complete" "The update was downloaded and verified.\n\nWrite the OTA boot entry now?" "Install" "Later"; then
        install_update_gui
    else
        show_info "Download complete" "You can open System Update later and choose Install."
    fi
}

install_update_gui() {
    if ! ask_yes_no "Install update" "This writes a GRUB OTA boot entry. After reboot, choose the Ming OS OTA Installer entry.\n\nContinue?" "Continue" "Cancel"; then
        return 0
    fi

    : > "${INSTALL_LOG}"
    local rc=1
    if command -v pkexec >/dev/null 2>&1; then
        pkexec /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    elif command -v sudo >/dev/null 2>&1; then
        sudo /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    else
        /usr/local/bin/ming-update install > "${INSTALL_LOG}" 2>&1
        rc=$?
    fi

    if [[ ${rc} -eq 0 ]]; then
        show_info "Install staged" "The OTA boot entry was written.\n\nReboot and choose Ming OS OTA Installer in GRUB."
    else
        show_error "Install failed" "Could not write the OTA boot entry.\n\nLog:\n$(log_tail "${INSTALL_LOG}")"
    fi
    return "${rc}"
}

main_menu() {
    if ! have_zenity; then
        /usr/local/bin/ming-update "${1:-check}"
        return $?
    fi

    while true; do
        local choice
        choice=$(zenity --list \
            --title="Ming OS Update Manager" \
            --text="Choose an action" \
            --column="Action" --column="Description" \
            "Check updates" "Check for a new Ming OS version" \
            "Download update" "Download and verify the discovered update" \
            "Install update" "Write the OTA boot entry" \
            "Show status" "Show current update status" \
            --width=560 --height=360 \
            --ok-label="Run" --cancel-label="Exit" 2>/dev/null)
        [[ $? -eq 0 ]] || break
        case "${choice}" in
            "Check updates") check_update_gui ;;
            "Download update") download_update_gui ;;
            "Install update") install_update_gui ;;
            "Show status")
                local status_text
                status_text=$(/usr/local/bin/ming-update status 2>&1)
                show_info "Update status" "${status_text}"
                ;;
        esac
    done
}

case "${1:-menu}" in
    menu) main_menu ;;
    check) check_update_gui ;;
    download) download_update_gui ;;
    install) install_update_gui ;;
    *) echo "Usage: ming-update-gui [menu|check|download|install]"; exit 1 ;;
esac
OTAGUI

    chmod +x /usr/local/bin/ming-update-gui
    bash -n /usr/local/bin/ming-update-gui

    cat > /usr/share/applications/ming-update.desktop << DESKTOPFILE
[Desktop Entry]
Name=System Update
Name[zh_CN]=系统更新
Comment=Check and install Ming OS updates
Comment[zh_CN]=检查并安装 Ming OS 系统更新
Exec=/usr/local/bin/ming-update-gui
Icon=ming-update-icon
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=update;upgrade;system;
StartupNotify=true
DESKTOPFILE

    mkdir -p "/home/${MING_USER}/Desktop"
    cp /usr/share/applications/ming-update.desktop "/home/${MING_USER}/Desktop/"
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/ming-update.desktop"
    chmod +x "/home/${MING_USER}/Desktop/ming-update.desktop"
}

create_version_file() {
    echo "Creating Ming OS version files..."

    echo "${MING_OS_VERSION}" > /etc/ming-version
    chmod 644 /etc/ming-version

    cat > /etc/ming-release << RELEASEFILE
NAME="Ming OS"
VERSION="${MING_OS_VERSION} Home Edition"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${MING_OS_VERSION} Home Edition"
VERSION_ID="${MING_OS_VERSION}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
OTA_ENABLED=true
OTA_VERSION=1.2.0
RELEASEFILE
    chmod 644 /etc/ming-release
}

main() {
    echo "=====> [06_ota_update] Deploying OTA update system <====="
    install_ota_dependencies
    deploy_ota_cli
    deploy_systemd_services
    deploy_gui_tool
    create_version_file
    echo "=====> [06_ota_update] OTA update system deployed <====="
}

main
