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
readonly DESKTOP_LAUNCHERS=(
    "ming-settings.desktop"
    "ming-files.desktop"
    "ming-firefox.desktop"
    "ming-store.desktop"
    "ming-toolbox.desktop"
    "xiahai-xiaoming.desktop"
    "ming-terminal.desktop"
    "Install Ming OS.desktop"
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

seed_trusted_desktop_receipts() {
    local receipt_dir="/var/lib/ming-os/trusted-desktops"
    local launcher source
    install -d -m 0755 "${receipt_dir}"
    for launcher in \
        "ming-settings.desktop" "ming-files.desktop" "ming-app-library.desktop" \
        "ming-firefox.desktop" "ming-terminal.desktop" "ming-store.desktop" "ming-toolbox.desktop" "xiahai-xiaoming.desktop" \
        "Install Ming OS.desktop"; do
        source="/usr/share/applications/${launcher}"
        [[ -f "${source}" ]] || continue
        printf '%s\n' "${source}" > "${receipt_dir}/${launcher}"
        chown root:root "${receipt_dir}/${launcher}" 2>/dev/null || true
        chmod 0644 "${receipt_dir}/${launcher}"
    done
}

# The installed bootloader invokes this conservative detector after the ESP is
# mounted; keep the helper outside user state so it survives account changes.
# ming-detect-other-os is intentionally mentioned here for the final gate.

verify_other_os_detector() {
    local detector="/usr/local/sbin/ming-detect-other-os"
    if [[ ! -x "${detector}" ]]; then
        echo "[07_finalize][ERROR] other-OS detector is missing: ${detector}" >&2
        return 1
    fi
}

# Keep the shipped desktop intentional. App discovery belongs in Ming App Library.
write_managed_launcher_copy() {
    local source="$1"
    local target="$2"
    awk -v source="${source}" '
        /^X-Ming-Managed=/ || /^X-Ming-Source-Desktop=/ { next }
        /^\[/ && $0 != "[Desktop Entry]" && in_desktop {
            print "X-Ming-Managed=true"
            print "X-Ming-Source-Desktop=" source
            in_desktop=0
            marked=1
        }
        { print }
        $0 == "[Desktop Entry]" { in_desktop=1 }
        END {
            if (in_desktop && !marked) {
                print "X-Ming-Managed=true"
                print "X-Ming-Source-Desktop=" source
            }
        }
    ' "${source}" > "${target}"
}

copy_default_launcher() {
    local launcher="$1"
    local target_dir="$2"
    local source="/usr/share/applications/${launcher}"
    local target="${target_dir}/${launcher}"

    case "${launcher}" in
        ming-firefox.desktop)
            [[ -f "${source}" ]] || source="/usr/share/applications/firefox-esr.desktop"
            [[ -f "${source}" ]] || source="/usr/share/applications/firefox.desktop"
            ;;
        ming-toolbox.desktop)
            [[ -f "${source}" ]] || source="/usr/share/applications/ming-toolbox.desktop"
            ;;
    esac

    if [[ ! -f "${source}" ]]; then
        echo "[07_finalize][WARN] default desktop launcher missing: ${launcher}"
        return 0
    fi

    # Never overwrite an unmanaged file supplied by the user.  A byte-identical
    # copy from an older image is safe to migrate into the marked form; any
    # other contents are left untouched and the canonical system entry remains
    # available to the drawer and Dock.
    if [[ -e "${target}" || -L "${target}" ]] \
       && ! is_managed_desktop_file "${target}"; then
        if [[ -f "${target}" ]] && cmp -s -- "${source}" "${target}"; then
            write_managed_launcher_copy "${source}" "${target}" || true
        else
            echo "[07_finalize] preserving unmanaged desktop launcher: ${target}"
            return 0
        fi
    fi

    write_managed_launcher_copy "${source}" "${target}" || {
        echo "[07_finalize][WARN] could not write managed launcher: ${launcher}"
        return 0
    }
    chmod 0755 "${target}" 2>/dev/null || true
}

is_managed_desktop_file() {
    local target="$1"
    [[ -f "${target}" ]] || return 1
    grep -Eiq '^[[:space:]]*X-Ming-Managed[[:space:]]*=[[:space:]]*true[[:space:]]*$' "${target}"
}

reset_desktop_dir() {
    local target_dir="$1"
    local owner="$2"
    local seed="${3:-true}"

    mkdir -p "${target_dir}"
    # User-created launchers are preserved, not ours to remove.  Only files carrying the
    # explicit Ming marker participate in migration; old Spark entries are
    # removed by retire_legacy_store_runtime using their known names.
    while IFS= read -r -d '' launcher; do
        if is_managed_desktop_file "${launcher}"; then
            rm -f -- "${launcher}"
        fi
    done < <(find "${target_dir}" -maxdepth 1 \( -type f -o -type l \) -name '*.desktop' -print0 2>/dev/null)
    # Do not remove directories: a directory containing only a user launcher
    # is still user data.  Ming-owned category directories are handled by the
    # organizer's explicit marker-aware cleanup path.

    if [[ "${seed}" == true ]]; then
        local launcher
        for launcher in "${DESKTOP_LAUNCHERS[@]}"; do
            copy_default_launcher "${launcher}" "${target_dir}"
        done
    fi

    chown -R "${owner}" "${target_dir}" 2>/dev/null || true
}

constrain_default_desktop() {
    echo "[07_finalize] constraining default desktop launchers ..."

    rm -f "${USER_HOME}/.config/ming-os/desktop-layout.json" \
          "${USER_HOME}/.config/ming-os/desktop-layout.last-good.json" \
          "${USER_HOME}/.config/ming-os/desktop-generated-manifest.json" \
          "/etc/skel/.config/ming-os/desktop-layout.json" \
          "/etc/skel/.config/ming-os/desktop-layout.last-good.json" \
          "/etc/skel/.config/ming-os/desktop-generated-manifest.json" 2>/dev/null || true

    local canonical_desktop="${USER_HOME}/Desktop"
    local discovered_desktop=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        discovered_desktop="$(runuser -u "${MING_USER}" -- env HOME="${USER_HOME}" \
            xdg-user-dir DESKTOP 2>/dev/null || true)"
        if [[ "${discovered_desktop}" == "${USER_HOME}"/* \
           && "${discovered_desktop}" != "${USER_HOME}" ]]; then
            canonical_desktop="${discovered_desktop}"
        fi
    fi
    reset_desktop_dir "${canonical_desktop}" "${MING_USER}:${MING_USER}" true
    for legacy_desktop in "${USER_HOME}/Desktop" "${USER_HOME}/桌面"; do
        [[ "${legacy_desktop}" == "${canonical_desktop}" ]] && continue
        reset_desktop_dir "${legacy_desktop}" "${MING_USER}:${MING_USER}" false
    done
    reset_desktop_dir "/etc/skel/Desktop" "root:root" true
    reset_desktop_dir "/etc/skel/桌面" "root:root" false
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

disable_phone_panel_restore() {
    local xfconf_dir="${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml"
    local autostart_dir="${USER_HOME}/.config/autostart"
    mkdir -p "${xfconf_dir}" "${autostart_dir}"
    cat > "${xfconf_dir}/xfce4-session.xml" << 'PHONESESSIONXML'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-session" version="1.0">
  <property name="sessions" type="empty">
    <property name="Failsafe" type="empty">
      <property name="Client0_Command" type="array"><value type="string" value="xfwm4"/></property>
      <property name="Client1_Command" type="array"><value type="string" value="xfsettingsd"/></property>
      <property name="Client2_Command" type="array"><value type="string" value="xfdesktop"/></property>
    </property>
  </property>
</channel>
PHONESESSIONXML
    cat > "${autostart_dir}/xfce4-panel.desktop" << 'PANELDISABLED'
[Desktop Entry]
Type=Application
Name=Xfce Panel
Exec=xfce4-panel
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
PANELDISABLED
}

# Old image layers occasionally leave zero-byte Calamares launchers behind.
# Remove only those known installer filenames from the finite desktop roots;
# keep valid Live autostart entries and all unrelated user files intact.
cleanup_zero_byte_calamares_entries() {
    local root entry
    for root in \
        "${USER_HOME}/Desktop" "${USER_HOME}/桌面" \
        "${USER_HOME}/.local/share/applications" \
        "/etc/skel/Desktop" "/etc/skel/桌面" \
        "/etc/skel/.local/share/applications" \
        "/usr/share/applications"; do
        [[ -d "${root}" ]] || continue
        while IFS= read -r -d '' entry; do
            echo "[07_finalize] removing zero-byte Calamares entry: ${entry}"
            rm -f -- "${entry}" || return 1
        done < <(find "${root}" -maxdepth 1 -type f -size 0 \
            \( -iname '*calamares*.desktop' -o -iname '*calamares*.dockitem' \) \
            -print0 2>/dev/null)
    done
    return 0
}

query_legacy_package_state() {
    local package="$1" output rc
    output="$(dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null)"
    rc=$?
    if [[ "${rc}" -eq 0 ]]; then
        printf '%s\n' "${output}"
        return 0
    fi
    if [[ "${rc}" -eq 1 && -z "${output}" ]]; then
        printf '%s\n' absent
        return 0
    fi
    echo "[07_finalize][ERROR] 旧组件 ${package} 的包数据库状态读取失败（退出码 ${rc}）" >&2
    return 1
}

retire_legacy_store_runtime() {
    local package state plan planned allowed installed=()
    local legacy_packages=(spark-store apm cn.flamescion.bookworm-compatibility-mode)
    echo "[07_finalize] 检查并退役旧 Spark/APM 运行时 ..."
    systemctl disable --now spark-update-notifier.service \
        spark-store-refresh.service 2>/dev/null || true
    for package in "${legacy_packages[@]}"; do
        state="$(query_legacy_package_state "${package}")" || return 1
        case "${state}" in
            absent|un*|rc*) ;;
            *) installed+=("${package}") ;;
        esac
    done
    if (( ${#installed[@]} > 0 )); then
        echo "[07_finalize] 正在移除旧组件（保留第三方应用和用户数据）: ${installed[*]}"
        if ! plan="$(LC_ALL=C apt-get -s remove --no-auto-remove -y "${installed[@]}" 2>&1)"; then
            echo "[07_finalize][ERROR] 无法验证旧组件卸载计划，停止收尾" >&2
            printf '%s\n' "${plan}" >&2
            return 1
        fi
        while read -r _ planned _; do
            [[ -n "${planned:-}" ]] || continue
            planned="${planned%%:*}"
            allowed=0
            for package in "${legacy_packages[@]}"; do
                [[ "${planned}" == "${package}" ]] && allowed=1 && break
            done
            if [[ "${allowed}" != "1" ]]; then
                echo "[07_finalize][ERROR] 卸载旧商店会意外连带移除用户软件 ${planned}，已停止收尾" >&2
                return 1
            fi
        done < <(awk '/^Remv[[:space:]]/ {print}' <<<"${plan}")
        if ! apt-get remove --no-auto-remove -y "${installed[@]}"; then
            echo "[07_finalize][ERROR] 旧 Spark/APM 组件移除失败，停止收尾以避免生成含残留的镜像" >&2
            return 1
        fi
        for package in "${legacy_packages[@]}"; do
            state="$(query_legacy_package_state "${package}")" || return 1
            if [[ "${state}" != "absent" && "${state}" != un* && "${state}" != rc* ]]; then
                echo "[07_finalize][ERROR] 旧组件 ${package} 移除后仍处于已安装状态（${state}），停止收尾" >&2
                return 1
            fi
        done
        echo "[07_finalize] 旧 Spark/APM 运行时已确认移除"
    else
        echo "[07_finalize] 未检测到已安装的旧 Spark/APM 运行时"
    fi
    rm -f /usr/share/polkit-1/actions/org.ming.spark.package-control.policy \
        /usr/share/polkit-1/actions/store.spark-app.*.policy \
        /usr/local/sbin/ming-spark-package-control \
        /usr/local/bin/ming-spark-store \
        /usr/local/bin/ming-package-install-gui \
        /usr/local/bin/ming-spark-backend-status \
        /usr/local/libexec/ming-spark-aria2c \
        /usr/local/bin/ming-install-wps \
        /etc/apt/preferences.d/90-ming-spark-store \
        /usr/lib/systemd/system/spark-update-notifier.service \
        /etc/systemd/system/spark-store-refresh.service 2>/dev/null || true
    rm -f /usr/share/applications/spark-store.desktop \
        /usr/share/applications/ming-install-spark-store.desktop \
        /usr/share/applications/ming-install-wps.desktop 2>/dev/null || true
    # Remove only legacy entries that Ming generated itself and carry the
    # explicit X-Ming-Managed=true marker.  A user may have
    # independently installed a similarly named application, so matching the
    # filename alone is not sufficient and no broad find -delete is allowed.
    local legacy_root legacy_entry
    is_legacy_spark_launcher() {
        local entry="$1"
        [[ -f "${entry}" && ! -L "${entry}" ]] || return 1
        grep -Eiq \
            '^[[:space:]]*(Exec|TryExec)=.*(ming-spark-store|spark-store|ming-package-install-gui)|^[[:space:]]*Name(\[[^]]+\])?=.*星火应用商店' \
            "${entry}"
    }
    for legacy_root in /home /etc/skel; do
        [[ -d "${legacy_root}" ]] || continue
        while IFS= read -r -d '' legacy_entry; do
            if is_managed_desktop_file "${legacy_entry}" \
               || is_legacy_spark_launcher "${legacy_entry}"; then
                rm -f -- "${legacy_entry}"
            fi
        done < <(find "${legacy_root}" -xdev -type f \
            \( -name 'spark-store.desktop' -o -name 'spark-store.dockitem' \
               -o -name 'ming-spark-store.desktop' -o -name 'ming-spark-store.dockitem' \
               -o -name 'ming-install-wps.desktop' -o -name 'ming-install-wps.dockitem' \
               -o -name 'wps-office.dockitem' \) \
            -print0 2>/dev/null)
    done
    # Xfce AppFinder persists recent commands in user cache/config files.
    # Remove only retired Spark command lines; keep all other user history.
    local appfinder_state
    for appfinder_state in \
        "${USER_HOME}/.cache/xfce4/appfinder"* \
        "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-appfinder.xml" \
        "/etc/skel/.cache/xfce4/appfinder"* \
        "/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-appfinder.xml"; do
        [[ -f "${appfinder_state}" ]] || continue
        sed -i -e '/ming-spark-store/d' -e '/ming-package-install-gui/d' "${appfinder_state}" 2>/dev/null || true
    done
    # Do not run autoremove and do not touch /opt/apps or user application
    # data: software installed through the old store remains installed.
    systemctl daemon-reload 2>/dev/null || true
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

converge_package_state() {
    if [[ -x /usr/local/sbin/ming-apt-source-select ]]; then
        /usr/local/sbin/ming-apt-source-select >/dev/null 2>&1 || \
            echo "[07_finalize][WARN] APT 镜像测速失败，保留 Debian 官方源" >&2
    fi
    if [[ -x /usr/local/sbin/ming-apt-upgrade-converge ]]; then
        /usr/local/sbin/ming-apt-upgrade-converge || {
            echo "[07_finalize][ERROR] APT 软件包收敛失败" >&2
            return 1
        }
    fi
}

# ======================== 主流程 ========================

main() {
    echo "=====> [07_finalize] 开始收尾与配置固化 (${MING_OS_VERSION}) <====="

    refresh_dock_launchers || return 1
    seed_trusted_desktop_receipts
    verify_other_os_detector || return 1
    disable_phone_panel_restore
    cleanup_zero_byte_calamares_entries || return 1
    retire_legacy_store_runtime || return 1
    seed_skel
    constrain_default_desktop
    repair_default_user_ownership
    verify_appearance_assets
    converge_package_state || return 1

    echo "=====> [07_finalize] 收尾完成 <====="
}

main
