#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 06: OTA 更新系统
# ============================================================================
# 设计意图：
#   为 Onion OS 提供完整的 OTA (Over-The-Air) 更新能力。
#   通过云服务器 API 检查更新，从 NAS 下载 ISO 镜像，
#   支持完整更新和增量更新两种模式。
#
# 输入：
#   环境变量: ONION_USER, ONION_OS_VERSION
#
# 输出：
#   完整的 OTA 更新客户端：CLI 工具、systemd 服务、GUI 工具
#
# 关键步骤：
#   1. 安装 OTA 依赖 (curl, jq, rsync)
#   2. 部署 onion-update CLI 工具
#   3. 创建 systemd 服务和定时器
#   4. 创建 GUI 更新工具
#   5. 配置更新源和版本清单
# ============================================================================

set -uo pipefail

readonly OTA_CONFIG_DIR="/etc/onion-update"
readonly OTA_CACHE_DIR="/var/cache/onion-update"
readonly OTA_UPDATE_SERVER="https://scallion.uno"
readonly OTA_API_ENDPOINT="/api/onion-update"

# ======================== 依赖安装 ========================

install_ota_dependencies() {
    echo "安装 OTA 更新依赖..."
    apt install -y --no-install-recommends \
        curl \
        wget \
        jq \
        rsync \
        squashfs-tools \
        zenity \
        yad \
        libnotify-bin \
        pkexec \
        polkitd \
        lxpolkit
}

# ======================== OTA CLI 工具 ========================

deploy_ota_cli() {
    echo "部署 onion-update CLI 工具..."

    cat > /usr/local/bin/onion-update << 'OTACLI'
#!/usr/bin/env bash
# Onion OS OTA 更新客户端
# 版本: 1.1.0

set -euo pipefail

readonly SCRIPT_VERSION="1.1.0"
readonly CONFIG_DIR="/etc/onion-update"
readonly CACHE_DIR="/var/cache/onion-update"
readonly STATE_FILE="${CONFIG_DIR}/state.json"
readonly CONFIG_FILE="${CONFIG_DIR}/config.json"
readonly UPDATE_SERVER="https://scallion.uno"
readonly API_ENDPOINT="/api/onion-update"

# 颜色定义
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "\n${BLUE}=====> $1 <=====${NC}\n"; }

# 确保配置目录存在
ensure_dirs() {
    mkdir -p "${CONFIG_DIR}" "${CACHE_DIR}"
    chmod 755 "${CONFIG_DIR}" "${CACHE_DIR}"
}

# 初始化默认配置
init_config() {
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        cat > "${CONFIG_FILE}" << CONFIGJSON
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
    "current_version": "$(cat /etc/onion-version 2>/dev/null || echo 'unknown')"
}
CONFIGJSON
        chmod 644 "${CONFIG_FILE}"
    fi
}

# 读取配置
get_config() {
    local key="$1"
    if [[ -f "${CONFIG_FILE}" ]]; then
        jq -r "${key}" "${CONFIG_FILE}" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

# 更新配置
set_config() {
    local key="$1"
    local value="$2"
    if [[ -f "${CONFIG_FILE}" ]]; then
        local tmp_file="${CONFIG_FILE}.tmp"
        jq "${key} = \"${value}\"" "${CONFIG_FILE}" > "${tmp_file}"
        mv "${tmp_file}" "${CONFIG_FILE}"
    fi
}

# 检查网络连接
check_network() {
    local server
    server=$(get_config '.update_server')
    server=${server:-${UPDATE_SERVER}}
    if ! curl -fsSL --connect-timeout 8 --max-time 15 "${server}/api/health" >/dev/null 2>&1 \
        && ! curl -fsSL --connect-timeout 8 --max-time 15 "${server}" >/dev/null 2>&1; then
        log_error "无法连接到更新服务器 (${server})"
        return 1
    fi
    return 0
}

# 检查更新
check_update() {
    log_step "检查更新"
    
    ensure_dirs
    init_config
    
    if ! check_network; then
        return 1
    fi
    
    local current_version
    current_version=$(cat /etc/onion-version 2>/dev/null || echo "unknown")
    
    log_info "当前版本: ${current_version}"
    log_info "正在连接更新服务器..."
    
    # 调用 API 检查更新
    local server endpoint channel
    server=$(get_config '.update_server')
    endpoint=$(get_config '.api_endpoint')
    channel=$(get_config '.channel')
    server=${server:-${UPDATE_SERVER}}
    endpoint=${endpoint:-${API_ENDPOINT}}
    channel=${channel:-stable}
    local api_url="${server}${endpoint}/check?version=${current_version}&channel=${channel}"
    local response
    
    if ! response=$(curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 45 "${api_url}" 2>/dev/null); then
        log_error "无法获取更新信息"
        return 1
    fi

    if ! echo "${response}" | jq -e . >/dev/null 2>&1; then
        log_error "更新服务器返回了无效 JSON"
        return 1
    fi

    local server_error
    server_error=$(echo "${response}" | jq -r '.error // ""')
    if [[ -n "${server_error}" ]]; then
        log_error "更新服务器错误: ${server_error}"
        return 1
    fi

    local manifest_status ready update_known
    manifest_status=$(echo "${response}" | jq -r '.status // ""')
    ready=$(echo "${response}" | jq -r '.ready // true')
    update_known=$(echo "${response}" | jq -r '.update_available // false')
    if [[ "${update_known}" == "true" && "${ready}" != "true" ]]; then
        local latest_version message
        latest_version=$(echo "${response}" | jq -r '.version // .latest_version // "unknown"')
        message=$(echo "${response}" | jq -r '.message // "新版本清单已发布，ISO 正在上传或校验中。"')
        log_warn "服务器已登记 Onion OS ${latest_version}，但更新包尚未就绪 (${manifest_status:-pending})"
        log_warn "${message}"
        rm -f "${CACHE_DIR}/update_info.json"
        set_config '.last_check' "$(date -Iseconds)"
        return 0
    fi
    
    # 解析响应
    local has_update
    has_update=$(echo "${response}" | jq -r '.has_update // false')
    
    if [[ "${has_update}" != "true" ]]; then
        log_info "当前已是最新版本！"
        rm -f "${CACHE_DIR}/update_info.json"
        set_config '.last_check' "$(date -Iseconds)"
        return 0
    fi
    
    local new_version
    new_version=$(echo "${response}" | jq -r '.version // "unknown"')
    
    log_info "发现新版本: ${new_version}"
    
    # 显示更新信息
    local release_notes
    release_notes=$(echo "${response}" | jq -r '.release_notes // "无更新说明"')
    
    echo ""
    echo "============================================"
    echo "  新版本: ${new_version}"
    echo "  当前版本: ${current_version}"
    echo "============================================"
    echo ""
    echo "更新说明:"
    echo "${release_notes}"
    echo ""
    
    # 保存更新信息
    echo "${response}" > "${CACHE_DIR}/update_info.json"
    
    # 发送桌面通知
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i system-software-update \
            "Onion OS 更新可用" \
            "新版本 ${new_version} 已发布\n运行 'onion-update download' 下载更新" \
            2>/dev/null || true
    fi
    
    set_config '.last_check' "$(date -Iseconds)"
    return 0
}

# 下载更新
download_update() {
    log_step "下载更新"
    
    ensure_dirs
    
    if [[ ! -f "${CACHE_DIR}/update_info.json" ]]; then
        log_error "没有可用的更新信息，请先运行 'onion-update check'"
        return 1
    fi
    
    local update_info
    update_info=$(cat "${CACHE_DIR}/update_info.json")
    
    local download_url
    download_url=$(echo "${update_info}" | jq -r '.download_url // ""')
    
    if [[ -z "${download_url}" || "${download_url}" == "null" ]]; then
        log_error "下载链接无效"
        return 1
    fi
    
    local new_version
    new_version=$(echo "${update_info}" | jq -r '.version // "unknown"')

    local iso_file="${CACHE_DIR}/onion-os-${new_version}.iso"

    log_info "开始下载 Onion OS ${new_version}..."
    log_info "下载地址: ${download_url}"

    # 下载前磁盘空间检查（26.2.5）
    local expected_size
    expected_size=$(echo "${update_info}" | jq -r '.size // 0')
    if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
        local free_bytes
        free_bytes=$(df --output=avail -B1 "${CACHE_DIR}" 2>/dev/null | tail -1 | tr -d ' ')
        local need_bytes=$(( expected_size + 536870912 ))  # ISO size + 512MB buffer
        if [[ "${free_bytes:-0}" -lt "${need_bytes}" ]]; then
            local free_mb=$(( ${free_bytes:-0} / 1048576 ))
            local need_mb=$(( need_bytes / 1048576 ))
            log_error "磁盘空间不足：需要至少 ${need_mb}MB，当前仅剩 ${free_mb}MB。"
            log_error "请清理磁盘后重试：sudo onion-cleanup 或手动清理 ${CACHE_DIR}"
            return 1
        fi
        log_info "磁盘空间检查通过（空闲 $(( ${free_bytes} / 1048576 ))MB）"
    fi

    local retries
    retries=$(get_config '.download_retries')
    if ! [[ "${retries}" =~ ^[0-9]+$ ]]; then
        retries=3
    fi

    # 使用 wget 下载，支持断点续传（.tmp 保留以便下次续传）
    if wget -c --tries="${retries}" --timeout=30 --read-timeout=30 --show-progress -O "${iso_file}.tmp" "${download_url}"; then
        mv "${iso_file}.tmp" "${iso_file}"
        log_info "下载完成: ${iso_file}"
        
        # 验证校验和
        local expected_checksum
        expected_checksum=$(echo "${update_info}" | jq -r '.checksum // ""')
        
        if [[ "${expected_checksum}" =~ ^[a-fA-F0-9]{64}$ ]]; then
            log_info "验证文件完整性..."
            local actual_checksum
            actual_checksum=$(sha256sum "${iso_file}" | awk '{print $1}')
            
            if [[ "${expected_checksum}" != "${actual_checksum}" ]]; then
                log_error "校验和验证失败！"
                log_error "预期: ${expected_checksum}"
                log_error "实际: ${actual_checksum}"
                rm -f "${iso_file}"
                return 1
            fi
            
            log_info "校验和验证通过"
        else
            log_warn "更新清单未提供有效 SHA256，已跳过校验。正式发布时应上传 ISO 后生成 checksum。"
        fi

        local expected_size
        expected_size=$(echo "${update_info}" | jq -r '.size // 0')
        if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
            local actual_size
            actual_size=$(stat -c '%s' "${iso_file}" 2>/dev/null || wc -c < "${iso_file}")
            if [[ "${actual_size}" -lt "${expected_size}" ]]; then
                log_error "下载文件大小异常，预期至少 ${expected_size} 字节，实际 ${actual_size} 字节"
                rm -f "${iso_file}"
                return 1
            fi
        fi
        
        # 保存状态
        cat > "${STATE_FILE}" << STATEJSON
{
    "status": "downloaded",
    "version": "${new_version}",
    "iso_path": "${iso_file}",
    "download_url": "${download_url}",
    "checksum": "${expected_checksum}",
    "download_time": "$(date -Iseconds)"
}
STATEJSON
        
        log_info "更新已准备好安装"
        log_info "运行 'onion-update install' 安装更新"
        
        return 0
    else
        log_error "下载失败"
        rm -f "${iso_file}.tmp"
        return 1
    fi
}

# 安装更新
install_update() {
    log_step "安装更新"
    
    if [[ ! -f "${STATE_FILE}" ]]; then
        log_error "没有已下载的更新，请先运行 'onion-update download'"
        return 1
    fi
    
    local state
    state=$(cat "${STATE_FILE}")
    
    local status
    status=$(echo "${state}" | jq -r '.status // ""')
    
    if [[ "${status}" != "downloaded" ]]; then
        log_error "更新状态无效: ${status}"
        return 1
    fi
    
    local iso_path
    iso_path=$(echo "${state}" | jq -r '.iso_path // ""')
    local new_version
    new_version=$(echo "${state}" | jq -r '.version // ""')
    
    if [[ ! -f "${iso_path}" ]]; then
        log_error "ISO 文件不存在: ${iso_path}"
        return 1
    fi
    
    log_warn "安装更新将需要重启系统"
    log_warn "请确保已保存所有工作"
    log_info "提示：安装后 GRUB 会保留旧版本启动项，若新版本启动异常可在开机时选择旧版本回滚。"
    echo ""
    read -p "是否继续安装? (y/N): " confirm
    
    if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
        log_info "已取消安装"
        return 0
    fi
    
    log_info "开始准备 Onion OS ${new_version} OTA 启动项..."

    if [[ $EUID -ne 0 ]]; then
        log_error "安装/写入 GRUB 启动项需要 root 权限，请使用 sudo onion-update install"
        return 1
    fi

    # 挂载 ISO 进行结构校验
    local mount_point="/mnt/onion-update"
    mkdir -p "${mount_point}"
    
    if ! mount -o loop "${iso_path}" "${mount_point}"; then
        log_error "无法挂载 ISO 文件"
        return 1
    fi
    
    if [[ ! -f "${mount_point}/live/vmlinuz" || ! -f "${mount_point}/live/initrd" || ! -f "${mount_point}/live/filesystem.squashfs" ]]; then
        umount "${mount_point}" || true
        rmdir "${mount_point}" || true
        log_error "ISO 结构不完整，缺少 live/vmlinuz、live/initrd 或 live/filesystem.squashfs"
        return 1
    fi

    # 卸载 ISO
    umount "${mount_point}" || true
    rmdir "${mount_point}" || true

    local custom_cfg="/boot/grub/custom.cfg"
    mkdir -p /boot/grub
    local tmp_cfg
    tmp_cfg="$(mktemp)"
    if [[ -f "${custom_cfg}" ]]; then
        sed '/^### BEGIN ONION OTA ###$/,/^### END ONION OTA ###$/d' "${custom_cfg}" > "${tmp_cfg}"
    else
        : > "${tmp_cfg}"
    fi

    cat >> "${tmp_cfg}" << GRUBMENU
### BEGIN ONION OTA ###
menuentry "Onion OS ${new_version} OTA Installer" {
    set iso_path="${iso_path}"
    search --no-floppy --file --set=root \${iso_path}
    loopback loop (\$root)\${iso_path}
    linux (loop)/live/vmlinuz boot=live components live-config username=onion user-fullname=Onion_OS_User hostname=onion-os findiso=\${iso_path} locales=zh_CN.UTF-8 quiet splash
    initrd (loop)/live/initrd
}
### END ONION OTA ###
GRUBMENU
    install -m 0644 "${tmp_cfg}" "${custom_cfg}"
    rm -f "${tmp_cfg}"

    cat > "${STATE_FILE}" << STATEJSON
{
    "status": "staged",
    "version": "${new_version}",
    "iso_path": "${iso_path}",
    "grub_entry": "Onion OS ${new_version} OTA Installer",
    "staged_time": "$(date -Iseconds)"
}
STATEJSON

    if command -v update-grub >/dev/null 2>&1; then
        update-grub || true
    fi

    log_info "OTA 启动项已写入: ${custom_cfg}"
    log_info "请重启，并在 GRUB 中选择 Onion OS ${new_version} OTA Installer 进入新系统安装器"
    
    # 发送通知
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i system-reboot \
            "Onion OS 更新完成" \
            "已写入 OTA 启动项，重启后可进入新系统安装器" \
            2>/dev/null || true
    fi
    
    return 0
}

# 显示更新状态
show_status() {
    log_step "更新状态"
    
    local current_version
    current_version=$(cat /etc/onion-version 2>/dev/null || echo "unknown")
    
    echo "当前版本: ${current_version}"
    echo "更新服务器: $(get_config '.update_server' || echo "${UPDATE_SERVER}")"
    
    if [[ -f "${CONFIG_FILE}" ]]; then
        local last_check
        last_check=$(get_config '.last_check')
        if [[ -n "${last_check}" && "${last_check}" != "null" ]]; then
            echo "上次检查: ${last_check}"
        else
            echo "上次检查: 从未"
        fi
    fi
    
    if [[ -f "${STATE_FILE}" ]]; then
        local state
        state=$(cat "${STATE_FILE}")
        local status
        status=$(echo "${state}" | jq -r '.status // ""')
        local version
        version=$(echo "${state}" | jq -r '.version // ""')
        
        echo "更新状态: ${status}"
        if [[ -n "${version}" && "${version}" != "null" ]]; then
            echo "待安装版本: ${version}"
        fi
    else
        echo "更新状态: 无可用更新"
    fi
}

# 配置更新设置
configure_update() {
    log_step "配置 OTA 更新"
    
    ensure_dirs
    init_config
    
    echo ""
    echo "Onion OS OTA 更新配置"
    echo "====================="
    echo ""
    
    read -p "更新服务器 [${UPDATE_SERVER}]: " server
    server=${server:-${UPDATE_SERVER}}
    
    read -p "更新通道 (stable/beta) [stable]: " channel
    channel=${channel:-stable}
    
    read -p "自动检查更新 (true/false) [true]: " auto_check
    auto_check=${auto_check:-true}
    
    read -p "自动下载更新 (true/false) [false]: " auto_download
    auto_download=${auto_download:-false}
    
    cat > "${CONFIG_FILE}" << CONFIGJSON
{
    "update_server": "${server}",
    "api_endpoint": "${API_ENDPOINT}",
    "channel": "${channel}",
    "auto_check": ${auto_check},
    "auto_download": ${auto_download},
    "notify_enabled": true,
    "last_check": "$(get_config '.last_check' 2>/dev/null || echo '')",
    "current_version": "$(cat /etc/onion-version 2>/dev/null || echo 'unknown')"
}
CONFIGJSON
    
    log_info "配置已保存"
}

# 显示帮助信息
show_help() {
    cat << HELP
Onion OS OTA 更新客户端 v${SCRIPT_VERSION}

用法: onion-update [命令] [选项]

命令:
    check       检查是否有可用更新
    download    下载最新更新
    install     安装已下载的更新
    status      显示更新状态
    config      配置更新设置
    help        显示此帮助信息

示例:
    onion-update check      # 检查更新
    onion-update download   # 下载更新
    onion-update install    # 安装更新
    onion-update status     # 查看状态

HELP
}

# 主函数
main() {
    case "${1:-help}" in
        check)
            check_update
            ;;
        download)
            download_update
            ;;
        install)
            install_update
            ;;
        status)
            show_status
            ;;
        config)
            configure_update
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
OTACLI

    chmod +x /usr/local/bin/onion-update
    echo "CLI 工具已部署: /usr/local/bin/onion-update"
}

# ======================== systemd 服务 ========================

deploy_systemd_services() {
    echo "创建 systemd 服务和定时器..."

    # OTA 更新检查服务
    cat > /etc/systemd/system/onion-update-check.service << SYSTEMDSERVICE
[Unit]
Description=Onion OS OTA Update Check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/onion-update check
StandardOutput=journal
StandardError=journal
SYSTEMDSERVICE

    # OTA 更新检查定时器（每周一凌晨 3 点检查）
    cat > /etc/systemd/system/onion-update-check.timer << SYSTEMDTIMER
[Unit]
Description=Onion OS OTA Update Check Timer

[Timer]
OnCalendar=Mon *-*-* 03:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMDTIMER

    # 启用定时器
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable onion-update-check.timer 2>/dev/null || true
    systemctl start onion-update-check.timer 2>/dev/null || true

    echo "systemd 服务和定时器已创建"
}

# ======================== GUI 更新工具 ========================

deploy_gui_tool() {
    echo "部署 GUI 更新工具..."

    cat > /usr/local/bin/onion-update-gui << 'OTAGUI'
#!/usr/bin/env bash
# Onion OS OTA 更新 GUI 工具

# 不用 set -e：任何 zenity 取消（退出码1）都不应让脚本崩溃
readonly CONFIG_DIR="/etc/onion-update"
readonly CACHE_DIR="/var/cache/onion-update"

# 检查更新并显示结果
check_update_gui() {
    local progress_file="/tmp/onion-update-progress"
    
    # 显示进度对话框
    (
        echo "10"
        echo "# 正在连接更新服务器..."
        
        # 执行检查
        if /usr/local/bin/onion-update check > /tmp/onion-update-check.log 2>&1; then
            echo "100"
            echo "# 检查完成"
        else
            echo "100"
            echo "# 检查完成（可能有错误）"
        fi
    ) | zenity --progress \
        --title="检查更新" \
        --text="正在检查 Onion OS 更新..." \
        --percentage=0 \
        --auto-close \
        --no-cancel \
        --width=400
    
    # 读取检查结果
    if [[ -f "${CACHE_DIR}/update_info.json" ]]; then
        local has_update
        has_update=$(jq -r '.has_update // false' "${CACHE_DIR}/update_info.json")
        
        if [[ "${has_update}" == "true" ]]; then
            local version
            version=$(jq -r '.version // "unknown"' "${CACHE_DIR}/update_info.json")
            local release_notes
            release_notes=$(jq -r '.release_notes // "无更新说明"' "${CACHE_DIR}/update_info.json")
            
            # 显示更新信息对话框
            if zenity --question \
                --title="发现新版本" \
                --text="发现 Onion OS 新版本: ${version}\n\n更新说明:\n${release_notes}\n\n是否下载更新?" \
                --ok-label="下载" \
                --cancel-label="稍后" \
                --width=500; then
                
                download_update_gui
            fi
        else
            zenity --info \
                --title="已是最新版本" \
                --text="当前已是最新版本的 Onion OS！" \
                --width=300
        fi
    else
        zenity --error \
            --title="检查失败" \
            --text="无法检查更新，请检查网络连接。\n\n详细日志:\n$(cat /tmp/onion-update-check.log)" \
            --width=400
    fi
}

# 下载更新（GUI 模式）
download_update_gui() {
    local progress_file="/tmp/onion-update-download-progress"
    
    # 创建进度监控文件
    echo "0" > "${progress_file}"
    
    # 在后台下载
    (
        /usr/local/bin/onion-update download > /tmp/onion-update-download.log 2>&1
        echo "100" > "${progress_file}"
    ) &
    local download_pid=$!
    
    # 显示进度对话框
    (
        while true; do
            if [[ -f "${progress_file}" ]]; then
                local progress
                progress=$(cat "${progress_file}")
                echo "${progress}"
                
                if [[ "${progress}" == "100" ]]; then
                    break
                fi
            fi
            sleep 1
        done
    ) | zenity --progress \
        --title="下载更新" \
        --text="正在下载 Onion OS 更新..." \
        --percentage=0 \
        --auto-close \
        --width=400
    
    # 等待下载完成
    wait "${download_pid}" || true
    
    # 检查下载结果
    if [[ -f "/var/cache/onion-update/update_info.json" ]]; then
        zenity --info \
            --title="下载完成" \
            --text="更新下载完成！\n\n请运行 'onion-update install' 安装更新，或点击'安装'按钮立即安装。" \
            --ok-label="安装" \
            --width=400
        
        # 询问是否立即安装
        if [[ $? -eq 0 ]]; then
            install_update_gui
        fi
    else
        zenity --error \
            --title="下载失败" \
            --text="更新下载失败，请稍后重试。\n\n详细日志:\n$(cat /tmp/onion-update-download.log)" \
            --width=400
    fi
    
    rm -f "${progress_file}"
}

# 安装更新（GUI 模式）
install_update_gui() {
    if zenity --question \
        --title="安装更新" \
        --text="安装更新将需要重启系统。\n\n请确保已保存所有工作。\n\n是否继续安装?" \
        --ok-label="安装并重启" \
        --cancel-label="取消" \
        --width=400; then
        
        # 显示安装进度
        zenity --progress \
            --title="安装更新" \
            --text="正在安装 Onion OS 更新..." \
            --pulsate \
            --auto-close \
            --no-cancel \
            --width=400 &
        local progress_pid=$!
        
        # 执行安装
        if /usr/local/bin/onion-update install > /tmp/onion-update-install.log 2>&1; then
            kill "${progress_pid}" 2>/dev/null || true
            
            zenity --info \
                --title="安装完成" \
                --text="更新安装完成！\n\n系统将自动重启。" \
                --width=300
            
            # 重启系统
            systemctl reboot
        else
            kill "${progress_pid}" 2>/dev/null || true
            
            zenity --error \
                --title="安装失败" \
                --text="更新安装失败。\n\n详细日志:\n$(cat /tmp/onion-update-install.log)" \
                --width=400
        fi
    fi
}

# 主菜单
main_menu() {
    while true; do
        local choice
        choice=$(zenity --list \
            --title="Onion OS 更新管理器" \
            --text="请选择操作:" \
            --column="操作" --column="说明" \
            "检查更新" "检查是否有新版本的 Onion OS" \
            "查看状态" "查看当前更新状态" \
            "配置设置" "配置更新服务器和选项" \
            "关于" "关于 OTA 更新系统" \
            --width=500 --height=350 \
            --ok-label="执行" \
            --cancel-label="退出")
        
        if [[ $? -ne 0 ]]; then
            break
        fi
        
        case "${choice}" in
            "检查更新")
                check_update_gui
                ;;
            "查看状态")
                local status_text
                status_text=$(/usr/local/bin/onion-update status 2>&1)
                zenity --info \
                    --title="更新状态" \
                    --text="${status_text}" \
                    --width=400
                ;;
            "配置设置")
                /usr/local/bin/onion-update config
                ;;
            "关于")
                zenity --info \
                    --title="关于" \
                    --text="Onion OS OTA 更新系统\n版本: 1.0.0\n\n通过云服务器自动检查和下载系统更新。" \
                    --width=300
                ;;
        esac
    done
}

# 主函数
main() {
    case "${1:-menu}" in
        menu)
            main_menu
            ;;
        check)
            check_update_gui
            ;;
        *)
            echo "用法: onion-update-gui [menu|check]"
            exit 1
            ;;
    esac
}

main "$@"
OTAGUI

    chmod +x /usr/local/bin/onion-update-gui

    # 创建桌面快捷方式
    cat > /usr/share/applications/onion-update.desktop << DESKTOPFILE
[Desktop Entry]
Name=系统更新
Name[zh_CN]=系统更新
Comment=检查并安装 Onion OS 更新
Comment[zh_CN]=检查并安装 Onion OS 系统更新
Exec=/usr/local/bin/onion-update-gui
Icon=onion-update-icon
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=update;upgrade;system;
StartupNotify=true
DESKTOPFILE

    # 在用户桌面放置快捷方式
    mkdir -p "/home/${ONION_USER}/Desktop"
    cp /usr/share/applications/onion-update.desktop "/home/${ONION_USER}/Desktop/"
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/Desktop/onion-update.desktop"
    chmod +x "/home/${ONION_USER}/Desktop/onion-update.desktop"

    echo "GUI 工具已部署"
}

# ======================== 版本信息文件 ========================

create_version_file() {
    echo "创建版本信息文件..."
    
    # 创建 /etc/onion-version 文件
    echo "${ONION_OS_VERSION}" > /etc/onion-version
    chmod 644 /etc/onion-version
    
    # 创建 /etc/onion-release 文件（更详细的信息）
    cat > /etc/onion-release << RELEASefile
NAME="Onion OS"
VERSION="${ONION_OS_VERSION} Home Edition"
ID=onion-os
ID_LIKE=debian
PRETTY_NAME="Onion OS ${ONION_OS_VERSION} Home Edition"
VERSION_ID="${ONION_OS_VERSION}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=onion
DEBIAN_CODENAME=trixie
OTA_ENABLED=true
OTA_VERSION=1.1.0
RELEASefile

    chmod 644 /etc/onion-release
    
    echo "版本信息文件已创建"
}

# ======================== 主流程 ========================

main() {
    echo "=====> [06_ota_update] 开始部署 OTA 更新系统 <====="

    install_ota_dependencies
    deploy_ota_cli
    deploy_systemd_services
    deploy_gui_tool
    create_version_file

    echo "=====> [06_ota_update] OTA 更新系统部署完成 <====="
    echo ""
    echo "使用方法:"
    echo "  CLI: onion-update [check|download|install|status|config]"
    echo "  GUI: onion-update-gui 或点击桌面'系统更新'图标"
    echo ""
}

main
