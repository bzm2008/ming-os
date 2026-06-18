#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 07: 收尾与配置固化
# ============================================================================
# 设计意图：
#   解决历史顽疾——此前所有桌面美化都写入 /home/onion（仅 Live 会话用户），
#   而 Calamares 安装时会新建用户并从 /etc/skel 拉取初始配置，导致安装后的
#   系统完全没有应用美化。本模块把已配置好的用户配置同步到 /etc/skel，
#   保证“安装后的新用户”与“Live 用户”获得完全一致的外观与体验。
#
# 输入：
#   环境变量: ONION_USER, ONION_OS_VERSION
#
# 输出：
#   /etc/skel 被填充为完整的 Onion OS 默认用户配置
#   登录时外观强制应用脚本就位
#
# 关键步骤：
#   1. 将 /home/${ONION_USER} 的配置镜像到 /etc/skel
#   2. 清除“一次性完成”标记，让新用户也能看到欢迎引导
#   3. 校验关键美化文件确实存在（构建期自检）
# ============================================================================

set -uo pipefail

readonly USER_HOME="/home/${ONION_USER}"

# ======================== 同步用户配置到 /etc/skel ========================

seed_skel() {
    echo "[07_finalize] 将默认用户配置同步到 /etc/skel ..."
    mkdir -p /etc/skel

    # 需要带入新用户的配置项（目录与点文件）
    local items=(
        ".config"
        ".gtkrc-2.0"
        ".xinputrc"
        ".face"
        "Desktop"
        ".local"
    )

    for item in "${items[@]}"; do
        local src="${USER_HOME}/${item}"
        if [[ -e "${src}" ]]; then
            rm -rf "/etc/skel/${item}"
            cp -a "${src}" "/etc/skel/${item}"
        fi
    done

    # 清除 Live 会话写下的“一次性完成”标记，
    # 否则新安装用户会跳过欢迎引导与缩放检测。
    rm -f /etc/skel/.config/onion-os/scale-done \
          /etc/skel/.config/onion-os/welcome-done \
          /etc/skel/.config/onion-os/app-recommend-done 2>/dev/null || true

    # /etc/skel 内文件应为 root 所有（useradd 复制时会重新赋予新用户）
    chown -R root:root /etc/skel 2>/dev/null || true

    echo "[07_finalize] /etc/skel 同步完成"
}

# ======================== 关键美化文件自检 ========================

verify_appearance_assets() {
    echo "[07_finalize] 校验关键美化资源 ..."
    local missing=0

    local must_exist=(
        "/usr/share/themes/Onion-Glass/gtk-3.0/gtk.css"
        "/usr/share/backgrounds/onion-os/default.png"
        "/usr/share/icons/hicolor/48x48/apps/onion-os-menu.svg"
        "/usr/local/bin/onion-picom"
        "/usr/local/bin/onion-apply-appearance"
        "/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml"
        "/etc/skel/.config/plank/dock1/settings"
    )

    for f in "${must_exist[@]}"; do
        if [[ ! -e "${f}" ]]; then
            echo "[07_finalize][WARN] 缺少美化资源: ${f}"
            missing=$((missing + 1))
        fi
    done

    if [[ ${missing} -eq 0 ]]; then
        echo "[07_finalize] 全部关键美化资源就位 ✓"
    else
        echo "[07_finalize][WARN] 有 ${missing} 项资源缺失，安装后外观可能不完整"
    fi
}

# ======================== 主流程 ========================

main() {
    echo "=====> [07_finalize] 开始收尾与配置固化 (${ONION_OS_VERSION}) <====="

    seed_skel
    verify_appearance_assets

    echo "=====> [07_finalize] 收尾完成 <====="
}

main
