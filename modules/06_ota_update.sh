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

Usage: ming-update [check|download|install|status|config|help]

Commands:
  check      Check for available updates.
  download   Download and verify the current update.
  install    Stage the downloaded ISO as a GRUB boot entry.
  status     Show current OTA state.
  config     Configure update source/channel.
HELP
}

case "${1:-help}" in
    check) check_update ;;
    download) download_update ;;
    install) install_update ;;
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
    echo "Creating ming-update systemd timer..."

    cat > /etc/systemd/system/ming-update-check.service << SYSTEMDSERVICE
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

    cat > /etc/systemd/system/ming-update-check.timer << SYSTEMDTIMER
[Unit]
Description=Ming OS OTA Update Check Timer

[Timer]
OnCalendar=Mon *-*-* 03:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMDTIMER

    systemctl daemon-reload 2>/dev/null || true
    systemctl enable ming-update-check.timer 2>/dev/null || true
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
