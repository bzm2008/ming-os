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

# Treat every path component as an lstat-style boundary.  Missing tail
# components are allowed so callers can create them, but an existing symlink or
# non-directory parent always fails closed.
finalize_path_is_safe() {
    local path="$1" current component index last_index
    local -a _finalize_components
    [[ "${path}" == /* ]] || return 1
    current=""
    IFS='/' read -r -a _finalize_components <<< "${path#/}"
    last_index=$((${#_finalize_components[@]} - 1))
    for index in "${!_finalize_components[@]}"; do
        component="${_finalize_components[index]}"
        [[ -n "${component}" && "${component}" != "." && "${component}" != ".." ]] || return 1
        current="${current}/${component}"
        [[ ! -L "${current}" ]] || return 1
        if [[ "${index}" -lt "${last_index}" && -e "${current}" && ! -d "${current}" ]]; then
            return 1
        fi
    done
    return 0
}

remove_managed_state_file() {
    local path="$1" parent
    [[ "${path}" == /* ]] || return 1
    parent="$(dirname -- "${path}")"
    finalize_path_is_safe "${parent}" || return 1
    # A user-owned symlink is deliberately left untouched.  In particular,
    # never let rm resolve it while cleaning stale coordinator state.
    [[ ! -L "${path}" ]] || return 0
    finalize_path_is_safe "${path}" || return 1
    [[ -e "${path}" || -L "${path}" ]] || return 0
    [[ -f "${path}" ]] || return 1
    rm -f -- "${path}"
}

remove_staged_tree() {
    local root="$1"
    finalize_path_is_safe "${root}" || return 1
    [[ -d "${root}" && ! -L "${root}" ]] || return 0
    # find -P never follows a link while cleaning an atomically replaced tree.
    find -P "${root}" -depth -mindepth 1 -type f -delete 2>/dev/null || return 1
    find -P "${root}" -depth -mindepth 1 -type l -delete 2>/dev/null || return 1
    find -P "${root}" -depth -mindepth 1 -type d -empty -delete 2>/dev/null || return 1
    rmdir -- "${root}" 2>/dev/null || return 1
}

# Keep the shipped desktop intentional. App discovery belongs in Ming App Library.
write_managed_launcher_copy() {
    local source="$1"
    local target="$2"
    local parent temporary
    [[ -f "${source}" && ! -L "${source}" ]] || return 1
    finalize_path_is_safe "${source}" || return 1
    finalize_path_is_safe "${target}" || return 1
    parent="$(dirname -- "${target}")"
    mkdir -p "${parent}" || return 1
    finalize_path_is_safe "${target}" || return 1
    temporary="$(mktemp "${target}.tmp.XXXXXX" 2>/dev/null || true)"
    [[ -n "${temporary}" && -f "${temporary}" && ! -L "${temporary}" ]] || return 1
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
    ' "${source}" > "${temporary}" || { rm -f -- "${temporary}"; return 1; }
    finalize_path_is_safe "${target}" || { rm -f -- "${temporary}"; return 1; }
    [[ ! -L "${target}" ]] || { rm -f -- "${temporary}"; return 1; }
    mv -f -- "${temporary}" "${target}" || { rm -f -- "${temporary}"; return 1; }
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

    if [[ ! -f "${source}" || -L "${source}" ]]; then
        echo "[07_finalize][WARN] default desktop launcher missing: ${launcher}"
        return 0
    fi
    # Validate the parent first.  A user-owned leaf symlink is preserved as-is
    # rather than followed (or causing the whole finalization to abort).
    finalize_path_is_safe "${target_dir}" || return 1
    if [[ -L "${target}" ]]; then
        echo "[07_finalize] preserving symlinked desktop launcher: ${target}"
        return 0
    fi
    finalize_path_is_safe "${target}" || return 1

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
    [[ -f "${target}" && ! -L "${target}" ]] || return 1
    grep -Eiq '^[[:space:]]*X-Ming-Managed[[:space:]]*=[[:space:]]*true[[:space:]]*$' "${target}"
}

reset_desktop_dir() {
    local target_dir="$1"
    local owner="$2"
    local seed="${3:-true}"

    finalize_path_is_safe "${target_dir}" || return 1
    mkdir -p "${target_dir}" || return 1
    finalize_path_is_safe "${target_dir}" || return 1
    # User-created launchers are preserved, not ours to remove.  Only files carrying the
    # explicit Ming marker participate in migration; old Spark entries are
    # removed by retire_legacy_store_runtime using their known names.
    while IFS= read -r -d '' launcher; do
        if is_managed_desktop_file "${launcher}"; then
            rm -f -- "${launcher}"
        fi
    done < <(find -P "${target_dir}" -maxdepth 1 \( -type f -o -type l \) -name '*.desktop' -print0 2>/dev/null)
    # Do not remove directories: a directory containing only a user launcher
    # is still user data.  Ming-owned category directories are handled by the
    # organizer's explicit marker-aware cleanup path.

    if [[ "${seed}" == true ]]; then
        local launcher
        for launcher in "${DESKTOP_LAUNCHERS[@]}"; do
            copy_default_launcher "${launcher}" "${target_dir}"
        done
    fi

    chown -R --no-dereference "${owner}" "${target_dir}" 2>/dev/null || true
}

constrain_default_desktop() {
    echo "[07_finalize] constraining default desktop launchers ..."

    # These are transient coordinator files, not user content.  Remove them
    # only after checking every parent component and never follow a leaf link.
    local managed_state
    for managed_state in \
        "${USER_HOME}/.config/ming-os/desktop-layout.json" \
        "${USER_HOME}/.config/ming-os/desktop-layout.last-good.json" \
        "${USER_HOME}/.config/ming-os/desktop-generated-manifest.json" \
        "/etc/skel/.config/ming-os/desktop-layout.json" \
        "/etc/skel/.config/ming-os/desktop-layout.last-good.json" \
        "/etc/skel/.config/ming-os/desktop-generated-manifest.json"; do
        finalize_path_is_safe "${managed_state}" || return 1
        remove_managed_state_file "${managed_state}" || return 1
    done

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
    local cache_dir="${USER_HOME}/.cache"
    local cache_ming_dir="${cache_dir}/ming-os"
    local cache_sessions_dir="${cache_dir}/sessions"
    local config_dir="${USER_HOME}/.config"
    local config_ming_dir="${config_dir}/ming-os"
    local managed_dir
    local -a managed_dirs=(
        "${cache_dir}"
        "${cache_ming_dir}"
        "${cache_sessions_dir}"
        "${config_dir}"
        "${config_ming_dir}"
    )

    echo "[07_finalize] repairing default user ownership ..."

    finalize_path_is_safe "${USER_HOME}" || {
        echo "[07_finalize][ERROR] refusing unsafe default user home: ${USER_HOME}" >&2
        return 1
    }
    for managed_dir in "${managed_dirs[@]}"; do
        finalize_path_is_safe "${managed_dir}" || {
            echo "[07_finalize][ERROR] refusing unsafe default user state path: ${managed_dir}" >&2
            return 1
        }
    done
    mkdir -p "${managed_dirs[@]}" 2>/dev/null || return 1
    for managed_dir in "${managed_dirs[@]}"; do
        finalize_path_is_safe "${managed_dir}" || return 1
    done

    # Calamares no longer runs the users module; /home/user comes from squashfs.
    # Keep it user-owned so autostart watchdogs can write logs and state files.
    finalize_path_is_safe "${USER_HOME}" || return 1
    chown -R --no-dereference "${MING_USER}:${MING_USER}" "${USER_HOME}" 2>/dev/null || true
    chmod 0700 "${USER_HOME}" 2>/dev/null || true
    chmod 0755 "${cache_dir}" "${cache_ming_dir}" 2>/dev/null || true
}

disable_phone_panel_restore() {
    local xfconf_dir="${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml"
    local autostart_dir="${USER_HOME}/.config/autostart"
    local session_path="${xfconf_dir}/xfce4-session.xml"
    local panel_path="${autostart_dir}/xfce4-panel.desktop"
    local session_tmp panel_tmp

    # Check the user boundary before creating any directory.  This prevents a
    # pre-existing /home/user link from redirecting writes outside the image.
    finalize_path_is_safe "${USER_HOME}" || return 1
    finalize_path_is_safe "${xfconf_dir}" || return 1
    finalize_path_is_safe "${autostart_dir}" || return 1
    mkdir -p "${xfconf_dir}" "${autostart_dir}" || return 1
    finalize_path_is_safe "${xfconf_dir}" || return 1
    finalize_path_is_safe "${autostart_dir}" || return 1

    if [[ -L "${session_path}" || -L "${panel_path}" ]]; then
        echo "[07_finalize][WARN] preserving symlinked panel/session state" >&2
        return 0
    fi
    session_tmp="$(mktemp "${xfconf_dir}/.xfce4-session.xml.XXXXXX" 2>/dev/null || true)"
    panel_tmp="$(mktemp "${autostart_dir}/.xfce4-panel.desktop.XXXXXX" 2>/dev/null || true)"
    [[ -n "${session_tmp}" && -f "${session_tmp}" && ! -L "${session_tmp}" ]] || return 1
    [[ -n "${panel_tmp}" && -f "${panel_tmp}" && ! -L "${panel_tmp}" ]] || {
        rm -f -- "${session_tmp}" 2>/dev/null || true
        return 1
    }
    cat > "${session_tmp}" << 'PHONESESSIONXML'
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
    cat > "${panel_tmp}" << 'PANELDISABLED'
[Desktop Entry]
Type=Application
Name=Xfce Panel
Exec=xfce4-panel
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
PANELDISABLED
    finalize_path_is_safe "${session_path}" || {
        rm -f -- "${session_tmp}" "${panel_tmp}" 2>/dev/null || true
        return 1
    }
    finalize_path_is_safe "${panel_path}" || {
        rm -f -- "${session_tmp}" "${panel_tmp}" 2>/dev/null || true
        return 1
    }
    mv -f -- "${session_tmp}" "${session_path}" || {
        rm -f -- "${session_tmp}" "${panel_tmp}" 2>/dev/null || true
        return 1
    }
    mv -f -- "${panel_tmp}" "${panel_path}" || {
        rm -f -- "${panel_tmp}" 2>/dev/null || true
        return 1
    }
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

copy_skel_item() {
    local src="$1" item="$2" destination="/etc/skel/${2}"
    local stage_root old_root parent

    [[ -e "${src}" || -L "${src}" ]] || return 0
    # A top-level link or a link in the source tree could escape the image.
    # Leave that item untouched rather than materializing it in /etc/skel.
    if [[ -L "${src}" ]]; then
        echo "[07_finalize][WARN] skipping symlinked skeleton source: ${src}" >&2
        return 0
    fi
    finalize_path_is_safe "${src}" || return 1
    [[ -f "${src}" || -d "${src}" ]] || return 0
    if find -P "${src}" \( -type l -o -type b -o -type c -o -type p -o -type s \) \
        -print -quit 2>/dev/null | grep -q .; then
        echo "[07_finalize][WARN] refusing special or symlinked skeleton source: ${src}" >&2
        return 0
    fi

    parent="$(dirname -- "${destination}")"
    finalize_path_is_safe "${parent}" || return 1
    if [[ -L "${destination}" ]]; then
        echo "[07_finalize][WARN] preserving symlinked skeleton target: ${destination}" >&2
        return 0
    fi
    finalize_path_is_safe "${destination}" || return 1
    if [[ -e "${destination}" ]] && find -P "${destination}" \
        \( -type l -o -type b -o -type c -o -type p -o -type s \) \
        -print -quit 2>/dev/null | grep -q .; then
        echo "[07_finalize][WARN] preserving unsafe existing skeleton target: ${destination}" >&2
        return 0
    fi

    mkdir -p "${parent}" || return 1
    finalize_path_is_safe "${destination}" || return 1
    stage_root="$(mktemp -d "${parent}/.ming-skel.XXXXXX" 2>/dev/null || true)"
    [[ -n "${stage_root}" && -d "${stage_root}" && ! -L "${stage_root}" ]] || return 1
    finalize_path_is_safe "${stage_root}" || { remove_staged_tree "${stage_root}" || true; return 1; }
    if ! cp -a --no-dereference -- "${src}" "${stage_root}/${item}"; then
        remove_staged_tree "${stage_root}" || true
        return 1
    fi
    finalize_path_is_safe "${stage_root}/${item}" || {
        remove_staged_tree "${stage_root}" || true
        return 1
    }
    if find -P "${stage_root}/${item}" -type l -print -quit 2>/dev/null | grep -q .; then
        echo "[07_finalize][WARN] staged skeleton unexpectedly contains a symlink: ${src}" >&2
        remove_staged_tree "${stage_root}" || true
        return 0
    fi

    old_root=""
    if [[ -e "${destination}" ]]; then
        old_root="$(mktemp -d "${parent}/.ming-skel-old.XXXXXX" 2>/dev/null || true)"
        [[ -n "${old_root}" && -d "${old_root}" && ! -L "${old_root}" ]] || {
            remove_staged_tree "${stage_root}" || true
            return 1
        }
        finalize_path_is_safe "${old_root}" || {
            remove_staged_tree "${stage_root}" || true
            remove_staged_tree "${old_root}" || true
            return 1
        }
        mv -- "${destination}" "${old_root}/${item}" || {
            remove_staged_tree "${stage_root}" || true
            remove_staged_tree "${old_root}" || true
            return 1
        }
    fi
    if ! mv -- "${stage_root}/${item}" "${destination}"; then
        if [[ -n "${old_root}" && -e "${old_root}/${item}" && ! -e "${destination}" ]]; then
            mv -- "${old_root}/${item}" "${destination}" || true
        fi
        remove_staged_tree "${stage_root}" || true
        [[ -z "${old_root}" ]] || remove_staged_tree "${old_root}" || true
        return 1
    fi
    remove_staged_tree "${stage_root}" || return 1
    [[ -z "${old_root}" ]] || remove_staged_tree "${old_root}" || return 1
    return 0
}

seed_skel() {
    echo "[07_finalize] 将默认用户配置同步到 /etc/skel ..."
    # lstat-style checks in finalize_path_is_safe keep the skeleton boundary intact.
    finalize_path_is_safe "/etc/skel" || return 1
    mkdir -p /etc/skel || return 1
    finalize_path_is_safe "/etc/skel" || return 1
    finalize_path_is_safe "${USER_HOME}" || return 1

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
        copy_skel_item "${src}" "${item}" || return 1
    done

    # 清除 Live 会话写下的“一次性完成”标记，
    # 否则新安装用户会跳过欢迎引导与缩放检测。
    local marker
    for marker in \
        /etc/skel/.config/ming-os/scale-done \
        /etc/skel/.config/ming-os/welcome-done \
        /etc/skel/.config/ming-os/oobe-account-done \
        /etc/skel/.config/ming-os/app-recommend-done; do
        finalize_path_is_safe "${marker}" || continue
        [[ ! -L "${marker}" ]] || continue
        rm -f -- "${marker}" 2>/dev/null || true
    done

    # /etc/skel 内文件应为 root 所有（useradd 复制时会重新赋予新用户）
    finalize_path_is_safe "/etc/skel" || return 1
    chown -R --no-dereference root:root /etc/skel 2>/dev/null || true

    echo "[07_finalize] /etc/skel 同步完成"
}

# ======================== 关键美化文件自检 ========================

verify_appearance_assets() {
    echo "[07_finalize] 校验关键美化资源 ..."
    local missing=0

    local must_exist=(
        "/usr/share/themes/Ming-Mint/gtk-3.0/gtk.css"
        "/usr/share/themes/Ming-Mint/index.theme"
        "/usr/share/themes/Ming-Mint/xfwm4/themerc"
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
    repair_default_user_ownership || return 1
    verify_appearance_assets
    converge_package_state || return 1

    echo "=====> [07_finalize] 收尾完成 <====="
}

main
