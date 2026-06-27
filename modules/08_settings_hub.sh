#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 08: 统一设置中心 (Settings Hub) — GTK4 / libadwaita
# ============================================================================
# 设计意图：
#   面向"数字难民"的单窗口全图形设置应用，零命令行。仿 Android 设置风格，
#   左侧导航 + 右侧内容页。封装系统底层命令，提供：
#     1) 账户管理（重设密码）
#     2) WLAN 与蓝牙（开关 + 列表，封装 nmcli / bluetoothctl）
#     3) 存储可视化（合并后空间使用率进度条，读 /run/ming-os/storage-info + df）
#     4) OTA 更新（封装 ming-update CLI，一键检查/下载/安装 + 进度）
#     5) 显示与无障碍（字体 + 图标等比缩放滑块，xfconf-query / xsettings）
#     6) 一键还原系统（timeshift rsync，首次开机后台快照 + 红色恢复出厂按钮）
#
# 输入：环境变量 MING_USER, MING_OS_VERSION
# 输出：/usr/local/bin/ming-settings (GTK4 应用) + 桌面入口 + timeshift 快照服务
# ============================================================================

set -uo pipefail

# ======================== 首次开机后台基线快照 (timeshift rsync) ========================
deploy_factory_snapshot() {
    echo "[08_settings_hub] 部署 timeshift 出厂快照服务 ..."

    # timeshift rsync 模式默认配置（首次运行时若无配置则自动建）
    mkdir -p /etc/timeshift
    cat > /etc/timeshift/timeshift.json << 'TSJSON'
{
  "backup_device_uuid" : "",
  "parent_device_uuid" : "",
  "do_first_run" : "false",
  "btrfs_mode" : "false",
  "include_btrfs_home_for_backup" : "false",
  "include_btrfs_home_for_restore" : "false",
  "stop_cron_emails" : "true",
  "schedule_monthly" : "false",
  "schedule_weekly" : "false",
  "schedule_daily" : "false",
  "schedule_hourly" : "false",
  "schedule_boot" : "false",
  "count_monthly" : "2",
  "count_weekly" : "3",
  "count_daily" : "5",
  "count_hourly" : "6",
  "count_boot" : "5",
  "snapshot_size" : "0",
  "snapshot_count" : "0",
  "exclude" : [
    "/home/**",
    "/root/**",
    "/var/cache/**",
    "/var/tmp/**"
  ],
  "exclude-apps" : []
}
TSJSON

    # 首次开机后台创建"出厂初始"基线快照（仅一次，标签 ming-factory）
    cat > /usr/local/sbin/ming-factory-snapshot << 'FACTORYSNAP'
#!/usr/bin/env bash
# 首次开机创建出厂基线快照（仅系统盘根，rsync 模式），供"恢复出厂设置"回滚。
set -uo pipefail
MARKER="/var/lib/ming-os/factory-snapshot-done"
mkdir -p /var/lib/ming-os
[[ -f "${MARKER}" ]] && exit 0

# 仅在已安装系统上做（Live/安装器环境不做）
if grep -qwE "boot=live|ming.installer=1" /proc/cmdline 2>/dev/null; then
    exit 0
fi

command -v timeshift >/dev/null 2>&1 || exit 0

# 等系统空闲一会，避免与首启其它任务争 I/O
sleep 60

timeshift --create --comments "ming-factory 出厂初始快照" --tags O \
    >/var/log/ming-factory-snapshot.log 2>&1 || true

echo "done @ $(date '+%F %T')" > "${MARKER}"
FACTORYSNAP
    chmod 0755 /usr/local/sbin/ming-factory-snapshot

    cat > /etc/systemd/system/ming-factory-snapshot.service << 'FACTORYSVC'
[Unit]
Description=Ming OS first-boot factory baseline snapshot (timeshift)
After=multi-user.target network-online.target
ConditionPathExists=!/var/lib/ming-os/factory-snapshot-done

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-factory-snapshot
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
FACTORYSVC
    systemctl enable ming-factory-snapshot.service 2>/dev/null || true
}

# ======================== Settings Hub 主程序 (GTK4/libadwaita) ========================
deploy_settings_hub() {
    echo "[08_settings_hub] 部署 GTK4 设置中心 ..."
    local src="/tmp/ming-build/assets/ming-settings.py"
    if [[ -f "${src}" ]]; then
        install -m 0755 "${src}" /usr/local/bin/ming-settings
    else
        echo "[08_settings_hub][WARN] 未找到 ming-settings.py，跳过设置中心安装"
        return 0
    fi
    # 构建期自检：语法 + 关键依赖（gi/Adw 不一定在 chroot 可导入，仅校验语法）
    python3 -m py_compile /usr/local/bin/ming-settings 2>/dev/null \
        && echo "[08_settings_hub] ming-settings 语法校验通过" \
        || echo "[08_settings_hub][WARN] ming-settings 语法校验未通过"
}

# ======================== 桌面入口 ========================
deploy_settings_launchers() {
    cat > /usr/share/applications/ming-settings.desktop << 'SETTINGSDESKTOP'
[Desktop Entry]
Type=Application
Name=Ming Settings
Name[zh_CN]=Ming 设置
Comment=Ming OS 统一设置中心
Comment[zh_CN]=账户、网络、存储、更新、显示与系统还原
Exec=/usr/local/bin/ming-settings
Icon=ming-control-center
Terminal=false
Categories=Settings;System;
SETTINGSDESKTOP

    # 放入用户桌面与自启目录所属位置（seed_skel 会带给安装后用户）
    mkdir -p "/home/${MING_USER}/.local/share/applications"
    cp /usr/share/applications/ming-settings.desktop \
       "/home/${MING_USER}/.local/share/applications/" 2>/dev/null || true
    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.local/share/applications" 2>/dev/null || true
}

# ======================== 主流程 ========================
main() {
    echo "=====> [08_settings_hub] 开始部署统一设置中心 <====="
    deploy_factory_snapshot
    deploy_settings_hub
    deploy_settings_launchers
    echo "=====> [08_settings_hub] 设置中心部署完成 <====="
}

main
