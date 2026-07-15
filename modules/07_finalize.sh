#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 07: 收尾与配置固化
# ============================================================================
# 设计意图：
#   解决历史顽疾——此前所有桌面美化都写入 /home/user（仅 Live 会话用户），
#   而 Calamares 安装时会新建用户并从 /etc/skel 拉取初始配置，导致安装后的
#   系统完全没有应用美化。本模块把已配置好的用户配置同步到 /etc/skel，
#   保证“安装后的新用户”与“Live 用户”获得完全一致的外观与体验。
#
# 输入：
#   环境变量: MING_USER, MING_OS_VERSION
#
# 输出：
#   /etc/skel 被填充为完整的 Ming OS 默认用户配置
#   登录时外观强制应用脚本就位
#
# 关键步骤：
#   1. 将 /home/${MING_USER} 的配置镜像到 /etc/skel
#   2. 清除“一次性完成”标记，让新用户也能看到欢迎引导
#   3. 校验关键美化文件确实存在（构建期自检）
# ============================================================================

set -uo pipefail

readonly USER_HOME="/home/${MING_USER}"
readonly DEFAULT_DESKTOP_LAYOUT="${USER_HOME}/.config/ming-os/desktop-layout.json"

readonly DESKTOP_LAUNCHERS=(
    "ming-settings.desktop"
    "ming-files.desktop"
    "ming-edge.desktop"
    "spark-store.desktop"
    "garlic-claw.desktop"
    "ming-terminal.desktop"
)

refresh_dock_launchers() {
    local helper="/usr/local/sbin/ming-refresh-dock-launchers"
    if [[ ! -x "${helper}" ]]; then
        echo "[07_finalize][ERROR] Dock launcher refresh helper is missing" >&2
        return 1
    fi
    if ! "${helper}" "${MING_USER}"; then
        echo "[07_finalize][ERROR] Dock launcher refresh reported missing targets" >&2
        return 1
    fi

    local name
    for name in ming-settings; do
        if [[ ! -s "/usr/share/applications/ming-dock-${name}.desktop" \
           || ! -s "${USER_HOME}/.config/plank/dock1/launchers/${name}.dockitem" ]]; then
            echo "[07_finalize][ERROR] final Dock launcher missing: ${name}" >&2
            return 1
        fi
    done
}

# Keep the shipped desktop intentional. App discovery belongs in Ming App Library.
copy_default_launcher() {
    local launcher="$1"
    local target_dir="$2"
    local source="/usr/share/applications/${launcher}"

    case "${launcher}" in
        ming-edge.desktop)
            [[ -f "${source}" ]] || source="/usr/share/applications/microsoft-edge.desktop"
            [[ -f "${source}" ]] || source="/usr/share/applications/microsoft-edge-stable.desktop"
            ;;
        spark-store.desktop)
            [[ -f "${source}" ]] || source="/usr/share/applications/ming-install-spark-store.desktop"
            ;;
    esac

    if [[ ! -f "${source}" ]]; then
        echo "[07_finalize][WARN] default desktop launcher missing: ${launcher}"
        return 0
    fi

    cp -f "${source}" "${target_dir}/${launcher}" 2>/dev/null || true
    chmod 0755 "${target_dir}/${launcher}" 2>/dev/null || true
}

reset_desktop_dir() {
    local target_dir="$1"
    local owner="$2"

    mkdir -p "${target_dir}"
    find "${target_dir}" -maxdepth 1 -type f -name '*.desktop' -delete 2>/dev/null || true
    find "${target_dir}" -maxdepth 1 -type l -delete 2>/dev/null || true
    while IFS= read -r -d '' dir; do
        if ! find "${dir}" -mindepth 1 ! -name '*.desktop' -print -quit 2>/dev/null | grep -q .; then
            rm -rf "${dir}"
        fi
    done < <(find "${target_dir}" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)

    local launcher
    for launcher in "${DESKTOP_LAUNCHERS[@]}"; do
        copy_default_launcher "${launcher}" "${target_dir}"
    done

    chown -R "${owner}" "${target_dir}" 2>/dev/null || true
}

constrain_default_desktop() {
    echo "[07_finalize] constraining default desktop launchers ..."

    rm -f "${DEFAULT_DESKTOP_LAYOUT}" \
          "/etc/skel/.config/ming-os/desktop-layout.json" 2>/dev/null || true

    reset_desktop_dir "${USER_HOME}/Desktop" "${MING_USER}:${MING_USER}"
    reset_desktop_dir "/etc/skel/Desktop" "root:root"
}

repair_default_user_ownership() {
    echo "[07_finalize] repairing default user ownership ..."

    mkdir -p "${USER_HOME}/.cache/ming-os" \
             "${USER_HOME}/.cache/sessions" \
             "${USER_HOME}/.config/ming-os" 2>/dev/null || true

    # Calamares no longer runs the users module; /home/user comes from squashfs.
    # Keep it user-owned so autostart watchdogs can write logs and state files.
    chown -R "${MING_USER}:${MING_USER}" "${USER_HOME}" 2>/dev/null || true
    chmod 0700 "${USER_HOME}" 2>/dev/null || true
    chmod 0755 "${USER_HOME}/.cache" "${USER_HOME}/.cache/ming-os" 2>/dev/null || true
}

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
    rm -f /etc/skel/.config/ming-os/scale-done \
          /etc/skel/.config/ming-os/welcome-done \
          /etc/skel/.config/ming-os/oobe-account-done \
          /etc/skel/.config/ming-os/app-recommend-done 2>/dev/null || true

    # /etc/skel 内文件应为 root 所有（useradd 复制时会重新赋予新用户）
    chown -R root:root /etc/skel 2>/dev/null || true

    echo "[07_finalize] /etc/skel 同步完成"
}

# ======================== 关键美化文件自检 ========================

verify_appearance_assets() {
    echo "[07_finalize] 校验关键美化资源 ..."
    local missing=0

    local must_exist=(
        "/usr/share/themes/Ming-Glass/gtk-3.0/gtk.css"
        "/usr/share/backgrounds/ming-os/default.png"
        "/usr/share/icons/hicolor/48x48/apps/ming-os-menu.svg"
        "/usr/local/bin/ming-picom"
        "/usr/local/bin/ming-lock"
        "/usr/local/bin/ming-apply-appearance"
        "/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
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
    echo "=====> [07_finalize] 开始收尾与配置固化 (${MING_OS_VERSION}) <====="

    refresh_dock_launchers || return 1
    seed_skel
    constrain_default_desktop
    repair_default_user_ownership
    verify_appearance_assets

    echo "=====> [07_finalize] 收尾完成 <====="
}

main
