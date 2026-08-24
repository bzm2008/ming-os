#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 03: 桌面定制与美化 (26.3.0 Dock + 低内存自适应版)
# ============================================================================
# 设计意图：
#   将 Xfce 深度定制为 Ming OS 独特风格 —— 丝滑动画、简洁面板、
#   自动 HiDPI 缩放、品牌化视觉、开箱即用的完整体验。
#
# 核心改进 (vs 26.0.6)：
#   1. 真·macOS Dock：底部 Plank 程序坞（悬停放大），顶部细菜单栏放托盘/时钟
#   2. 美化必达：配置同步进 /etc/skel，安装后的新用户也继承（见 07_finalize.sh）
#   3. Picom 改用主线 10.x 兼容写法，杜绝因解析失败导致美化无效
#   4. 登录自愈 ming-apply-appearance：逐显示器强制套用壁纸/主题/Dock
#   5. 自动分辨率检测 → 自适应 DPI/顶栏/Dock 图标/字体缩放
#   6. Ming 品牌化轻量纸感主题 (玉绿主色) + 多分辨率壁纸生成
# ============================================================================

set -uo pipefail

readonly MING_GREEN="#2F8A7D"
readonly MING_GREEN_DARK="#1E5D55"
readonly MING_BG="#F4F7F3"
readonly MING_ACCENT="#2F8A7D"

install_ota_target_guard() {
    local source=/tmp/ming-build/assets/ming-ota-target-guard.py
    local module_dir=/usr/lib/x86_64-linux-gnu/calamares/modules/ming-ota-target-guard
    [[ -s "${source}" ]] || {
        echo "ERROR: missing OTA target guard asset: ${source}" >&2
        return 1
    }
    install -d -m 0755 /usr/local/lib/ming-os "${module_dir}" /etc/calamares/modules
    install -m 0644 "${source}" /usr/local/lib/ming-os/ming_ota_target_guard.py
    cat > "${module_dir}/module.desc" << 'MINGOTAGUARDDESC'
---
type: "job"
name: "ming-ota-target-guard"
interface: "python"
script: "main.py"
MINGOTAGUARDDESC
    cat > "${module_dir}/main.py" << 'MINGOTAGUARDPY'
#!/usr/bin/env python3
import importlib.util
import pathlib

import libcalamares

path = pathlib.Path("/usr/local/lib/ming-os/ming_ota_target_guard.py")
spec = importlib.util.spec_from_file_location("ming_ota_target_guard", path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def run():
    if not pathlib.Path("/run/ming-ota-preflight.ok").is_file():
        return None
    ok, message = guard.validate_from_marker(
        libcalamares.globalstorage.value("partitions")
    )
    return None if ok else ("Ming OTA safety check failed", message)
MINGOTAGUARDPY
    cat > /etc/calamares/modules/ming-ota-target-guard.conf << 'MINGOTAGUARDCONF'
---
MINGOTAGUARDCONF
    chmod 0644 "${module_dir}/main.py"
}

install_ming_settings() {
    local asset_dir="/tmp/ming-build/assets"
    local lib_dir="/usr/local/lib/ming-os"
    mkdir -p "${lib_dir}" /usr/local/bin

    for asset in ming-settings.py ming-settings-backend.py ming-display-control.py; do
        if [[ ! -s "${asset_dir}/${asset}" ]]; then
            echo "ERROR: missing Ming Settings asset: ${asset}" >&2
            return 1
        fi
    done

    install -m 0755 "${asset_dir}/ming-settings.py" /usr/local/bin/ming-settings
    install -m 0755 "${asset_dir}/ming-settings-backend.py" "${lib_dir}/ming-settings-backend"
    install -m 0755 "${asset_dir}/ming-display-control.py" /usr/local/bin/ming-display-control
    cat > /usr/local/bin/ming-control-center << 'MINGCONTROLWRAPPER'
#!/usr/bin/env bash
set -euo pipefail

log_dir="${HOME}/.cache/ming-os"
if ! mkdir -p "${log_dir}" 2>/dev/null; then
    log_dir="${XDG_RUNTIME_DIR:-/tmp}"
fi
log_file="${log_dir}/ming-settings-launch.log"

if ! runtime_error="$(/usr/bin/python3 - <<'PY' 2>&1
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
PY
)"; then
    printf '[%s] GTK4/Adwaita runtime check failed: %s\n' \
        "$(date '+%F %T')" "${runtime_error}" >>"${log_file}" 2>/dev/null || true
    message="Ming 设置缺少 GTK4/Adwaita 运行依赖。请安装 gir1.2-gtk-4.0 和 gir1.2-adw-1 后重试。日志：${log_file}"
    if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
        zenity --error --title="Ming 设置无法启动" --text="${message}" --width=520 2>/dev/null || \
            notify-send "Ming 设置无法启动" "${message}" 2>/dev/null || true
    else
        notify-send "Ming 设置无法启动" "${message}" 2>/dev/null || true
    fi
    exit 1
fi

exec /usr/local/bin/ming-settings "$@"
MINGCONTROLWRAPPER
    chmod 0755 /usr/local/bin/ming-control-center

    # The Xfce backend remains installed, but its obsolete visible control
    # center must not compete with Ming Settings in launchers or search.
    local desktop_file
    for desktop_file in \
        /usr/share/applications/xfce-settings-manager.desktop \
        /usr/share/applications/xfce4-settings-manager.desktop \
        /usr/share/applications/xfce4-display-settings.desktop; do
        [[ -f "${desktop_file}" ]] || continue
        if grep -q '^NoDisplay=' "${desktop_file}"; then
            sed -i 's/^NoDisplay=.*/NoDisplay=true/' "${desktop_file}"
        else
            sed -i '/^\[Desktop Entry\]/a NoDisplay=true' "${desktop_file}"
        fi
    done

    # Some applications invoke the old executable directly instead of its
    # desktop entry.  Preserve it once, then route that compatibility command
    # to the Ming display page so it cannot bypass the confirmed rollback UI.
    local xfce_display_binary=/usr/bin/xfce4-display-settings
    local xfce_display_real="${lib_dir}/xfce4-display-settings.real"
    if [[ -e "${xfce_display_binary}" ]] \
        && ! grep -Fq 'Ming OS display settings compatibility launcher' "${xfce_display_binary}" 2>/dev/null \
        && [[ ! -e "${xfce_display_real}" ]]; then
        mv -- "${xfce_display_binary}" "${xfce_display_real}"
    fi
    cat > "${xfce_display_binary}" << 'MINGXFCECOMPATDISPLAY'
#!/usr/bin/env bash
# Ming OS display settings compatibility launcher; do not call the preserved
# xfce4-display-settings.real here, otherwise callers bypass confirmation.
exec /usr/local/bin/ming-control-center --page display "$@"
MINGXFCECOMPATDISPLAY
    chmod 0755 "${xfce_display_binary}"
}

cleanup_retired_ming_entries() {
    cat > /usr/local/bin/ming-migrate-all-disks << 'MINGMIGRATEDISKS'
#!/usr/bin/env bash
set -euo pipefail

hub="${HOME}/所有磁盘"
destination="${HOME}/Documents/所有磁盘-旧文件"
bookmarks="${HOME}/.config/gtk-3.0/bookmarks"

if [[ -d "${hub}" && ! -L "${hub}" ]]; then
    mkdir -p "${destination}"
    shopt -s dotglob nullglob
    for item in "${hub}"/*; do
        name="$(basename "${item}")"
        generated=false
        if [[ -L "${item}" ]]; then
            case "${name}" in
                我的文件|桌面|下载|文档|系统盘) generated=true ;;
            esac
        elif [[ "${name}" == "README.txt" ]] && grep -Fq 'Ming OS 所有磁盘' "${item}" 2>/dev/null; then
            generated=true
        elif [[ "${name}" == "释放空间.desktop" ]] && grep -Fq 'Exec=ming-control-center' "${item}" 2>/dev/null; then
            generated=true
        fi
        if [[ "${generated}" == "true" ]]; then
            rm -f -- "${item}"
            continue
        fi
        target="${destination}/${name}"
        if [[ -e "${target}" || -L "${target}" ]]; then
            target="${target}.$(date +%Y%m%d-%H%M%S)"
        fi
        mv -- "${item}" "${target}"
    done
    rmdir -- "${hub}" 2>/dev/null || true
fi

if [[ -f "${bookmarks}" ]]; then
    sed -i '\|file://.*/所有磁盘|d' "${bookmarks}"
fi
rm -f -- \
    "${HOME}/Desktop/Ming 应用库.desktop" \
    "${HOME}/Desktop/所有磁盘.desktop" \
    "${HOME}/Desktop/ming-app-library.desktop" \
    "${HOME}/Desktop/ming-disk-hub.desktop"
MINGMIGRATEDISKS
    chmod 0755 /usr/local/bin/ming-migrate-all-disks

    rm -f /usr/share/applications/ming-disk-hub.desktop
    rm -f /usr/local/bin/ming-disk-hub
    rm -f "/home/${MING_USER}/.config/plank/dock1/launchers/ming-disk-hub.dockitem"
    for retired_root in /usr/local/bin /usr/local/sbin /usr/share/applications \
        /usr/share/icons /etc/systemd/system /usr/lib/systemd/system; do
        [[ -d "${retired_root}" ]] || continue
        find "${retired_root}" -maxdepth 2 -type f \
            \( -iname '*claw*' -o -iname 'open*claw*' \) \
            -delete 2>/dev/null || true
        find "${retired_root}" -maxdepth 2 -type l \
            \( -iname '*claw*' -o -iname 'open*claw*' \) \
            -delete 2>/dev/null || true
    done
    find "/home/${MING_USER}" -maxdepth 4 -type f \
        \( -iname '*claw*' -o -iname 'open*claw*' \) \
        -delete 2>/dev/null || true
    find /usr/share/applications \
         "/home/${MING_USER}/Desktop" \
         "/home/${MING_USER}/.config/plank/dock1/launchers" \
         -maxdepth 1 \( -iname '*claw*.desktop' -o -iname '*claw*.dockitem' \) \
         -delete 2>/dev/null || true
    rm -f "/home/${MING_USER}/Desktop/Ming 应用库.desktop" \
          "/home/${MING_USER}/Desktop/所有磁盘.desktop" \
          "/home/${MING_USER}/Desktop/ming-app-library.desktop" \
          "/home/${MING_USER}/Desktop/ming-disk-hub.desktop"

    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/ming-migrate-all-disks.desktop" << 'MINGMIGRATEAUTO'
[Desktop Entry]
Type=Application
Name=Ming Files Migration
Exec=sh -c '/usr/local/bin/ming-migrate-all-disks && rm -f ~/.config/autostart/ming-migrate-all-disks.desktop'
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
MINGMIGRATEAUTO
    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/autostart/ming-migrate-all-disks.desktop"
}

install_ming_shell_components() {
    local asset_dir="/tmp/ming-build/assets"
    local lib_dir="/usr/local/lib/ming-os"
    local asset
    mkdir -p "${lib_dir}" /usr/local/bin /usr/local/sbin /etc/udev/rules.d \
        "/home/${MING_USER}/.local/share/applications"
    for asset in ming-shell-common.py ming-notifications.py ming-device-control.py ming-audio-session.py ming-hardware-status.py ming-storage-status.py ming-appearance-control.py ming-app-drawer.py ming-launch.py ming-package-installer.py ming-appimage-installer.py; do
        if [[ ! -s "${asset_dir}/${asset}" ]]; then
            echo "ERROR: missing Ming shell asset: ${asset}" >&2
            return 1
        fi
    done

    install -m 0644 "${asset_dir}/ming-shell-common.py" "${lib_dir}/ming-shell-common.py"
    install -m 0644 "${asset_dir}/ming-notifications.py" "${lib_dir}/ming-notifications.py"
    install -m 0644 "${asset_dir}/ming-device-control.py" "${lib_dir}/ming-device-control.py"
    # Drawer and broker load the common module beside their executable.
    install -m 0644 "${asset_dir}/ming-shell-common.py" /usr/local/bin/ming-shell-common.py
    install -m 0755 "${asset_dir}/ming-notifications.py" /usr/local/bin/ming-notifications
    install -m 0755 "${asset_dir}/ming-device-control.py" /usr/local/bin/ming-device-control
    install -m 0755 "${asset_dir}/ming-audio-session.py" /usr/local/bin/ming-audio-session
    install -m 0755 "${asset_dir}/ming-hardware-status.py" /usr/local/bin/ming-hardware-status
    install -m 0755 "${asset_dir}/ming-storage-status.py" /usr/local/bin/ming-storage-status
    install -m 0755 "${asset_dir}/ming-appearance-control.py" /usr/local/bin/ming-appearance-control
    install -m 0755 "${asset_dir}/ming-app-drawer.py" /usr/local/bin/ming-app-drawer
    install -m 0755 "${asset_dir}/ming-launch.py" /usr/local/bin/ming-launch
    install -m 0755 "${asset_dir}/ming-package-installer.py" /usr/local/sbin/ming-package-installer
    # Keep the privileged implementation in sbin, but expose a normal-user
    # PATH entry so terminal diagnostics and documented commands are usable.
    cat > /usr/local/bin/ming-package-installer << 'MINGPACKAGEINSTALLER'
#!/usr/bin/env bash
exec /usr/local/sbin/ming-package-installer "$@"
MINGPACKAGEINSTALLER
    chmod 0755 /usr/local/bin/ming-package-installer
    install -m 0755 "${asset_dir}/ming-appimage-installer.py" /usr/local/bin/ming-appimage-installer
    install -m 0644 "${asset_dir}/90-ming-backlight.rules" /etc/udev/rules.d/90-ming-backlight.rules

    # All GUI-triggered privileged operations cross one narrow, auditable
    # Polkit boundary.  The route and its arguments are validated before
    # pkexec is invoked, so a missing graphical agent produces a useful
    # message instead of a silent /dev/tty failure.
    cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'
#!/usr/bin/env bash
set -uo pipefail

route="${1:-}"
shift || true
command=()
case "${route}" in
    package)
        [[ "${1:-}" == "install" && "$#" -eq 2 ]] || {
            echo "软件安装请求无效，只允许安装一个本地 DEB 文件。" >&2
            exit 2
        }
        package_file="$2"
        [[ "${package_file}" == /*.deb && -f "${package_file}" ]] || {
            echo "软件包路径无效，只允许读取本地 DEB 文件。" >&2
            exit 2
        }
        command=(/usr/local/sbin/ming-package-installer install "${package_file}")
        ;;
    spark)
        [[ "$#" -ge 1 ]] || { echo "星火应用请求缺少操作。" >&2; exit 2; }
        command=(/usr/local/sbin/ming-spark-package-control "$@")
        ;;
    broadcom)
        [[ "$#" -eq 1 && ( "$1" == install || "$1" == restore ) ]] || {
            echo "Broadcom 驱动请求无效。" >&2
            exit 2
        }
        command=(/usr/local/sbin/ming-broadcom-driver "$1")
        ;;
    radio)
        [[ "$#" -eq 1 && "$1" == bluetooth ]] || {
            echo "无线设备修复请求无效。" >&2
            exit 2
        }
        command=(/usr/local/sbin/ming-radio-repair bluetooth)
        ;;
    *)
        echo "拒绝未受信任的系统授权操作。" >&2
        exit 2
        ;;
esac

if [[ ! -x "${command[0]}" ]]; then
    echo "系统组件缺失：${command[0]}。" >&2
    exit 127
fi

if output="$(pkexec "${command[@]}" 2>&1)"; then
    rc=0
else
    rc=$?
fi
if [[ "${rc}" -ne 0 ]]; then
    if [[ "${output}" == *"Error creating textual authentication agent"* \
            || "${output}" == *"/dev/tty"* \
            || "${output}" == *"No such device or address"* ]]; then
        echo "请在桌面授权弹窗中确认，当前无可用图形授权代理。请先完成账户设置或重启授权代理。" >&2
    elif [[ "${output}" == *"not authorized"* || "${output}" == *"Not authorized"* ]]; then
        echo "此操作需要管理员授权，请先完成账户设置并在桌面授权弹窗中确认。" >&2
    elif [[ -n "${output}" ]]; then
        printf '%s\n' "${output}" >&2
    else
        echo "系统授权失败，请检查账户设置和 Polkit 服务。" >&2
    fi
else
    [[ -n "${output}" ]] && printf '%s\n' "${output}"
fi
exit "${rc}"
MINGAUTHORIZE
    chmod 0755 /usr/local/bin/ming-authorized-action

    # AppImage files normally mount through FUSE.  Older kernels, containers
    # and some virtual machines do not expose /dev/fuse, so all generated
    # launchers go through this small user-level dispatcher and use the
    # runtime's extract-and-run fallback in that case.
    cat > /usr/local/bin/ming-appimage-run << 'MINGAPPIMAGERUN'
#!/usr/bin/env bash
set -u

appimage="${1:-}"
shift || true
if [[ -z "${appimage}" || ! -f "${appimage}" || ! -x "${appimage}" ]]; then
    echo "AppImage 文件不存在或不可执行。" >&2
    exit 2
fi

if [[ -e /dev/fuse && -r /dev/fuse && -w /dev/fuse ]]; then
    exec "${appimage}" "$@"
fi

# AppImage type 2 runtimes provide this mode specifically for systems where
# FUSE is unavailable.  Keep the original arguments after the control flag.
exec "${appimage}" --appimage-extract-and-run "$@"
MINGAPPIMAGERUN
    chmod 0755 /usr/local/bin/ming-appimage-run

    cat > /usr/local/bin/ming-refresh-desktop-state << 'MINGREFRESHDESKTOP'
#!/usr/bin/env bash
set -uo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    target_user=""
    if [[ -n "${PKEXEC_UID:-}" ]]; then
        target_user="$(getent passwd "${PKEXEC_UID}" 2>/dev/null | cut -d: -f1 || true)"
    elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        target_user="${SUDO_USER}"
    elif command -v loginctl >/dev/null 2>&1; then
        target_user="$(loginctl list-users --no-legend 2>/dev/null |
            awk '$1 >= 1000 && $2 != "root" {print $2; exit}')"
    fi
    if [[ -z "${target_user}" ]] || ! id "${target_user}" >/dev/null 2>&1; then
        echo "ERROR: no active desktop user is available for refresh" >&2
        exit 1
    fi
    target_uid="$(id -u "${target_user}")"
    target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
    exec runuser -u "${target_user}" -- env \
        HOME="${target_home}" USER="${target_user}" LOGNAME="${target_user}" \
        XDG_RUNTIME_DIR="/run/user/${target_uid}" \
        /usr/local/bin/ming-refresh-desktop-state
fi

status=0
user_apps="${HOME}/.local/share/applications"
mkdir -p "${user_apps}"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${user_apps}" >/dev/null 2>&1 || status=1
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || status=1
fi
if [[ -d "${HOME}/.local/share/icons/hicolor" ]] &&
   command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || status=1
fi
if command -v ming-phone-desktop >/dev/null 2>&1; then
    ming-phone-desktop --sync >/dev/null 2>&1 || status=1
fi
if [[ -x /usr/local/sbin/ming-refresh-dock-launchers ]]; then
    /usr/local/sbin/ming-refresh-dock-launchers "$(id -un)" >/dev/null 2>&1 || status=1
fi
exit "${status}"
MINGREFRESHDESKTOP
    chmod 0755 /usr/local/bin/ming-refresh-desktop-state

    # Thunar custom actions do not display a command's stdout.  Keep privilege
    # elevation in the narrow installer, while this unprivileged wrapper turns
    # its structured result into an explicit success/failure dialog and asks
    # the running phone desktop to rescan newly installed launchers.
    cat > /usr/local/bin/ming-package-install-gui << 'MINGPACKAGEGUI'
#!/usr/bin/env bash
set -uo pipefail

package_file="${1:-}"
if [[ -z "${package_file}" || ! -f "${package_file}" ]]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="安装 DEB 软件包" --text="找不到要安装的本地 DEB 软件包。" --width=420 2>/dev/null || true
    else
        notify-send -u critical "安装 DEB 软件包" "找不到要安装的本地 DEB 软件包。" 2>/dev/null || true
    fi
    exit 2
fi

show_blocking_error() {
    local message="$1"
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="无法安装软件" --text="${message}" --width=460 2>/dev/null || true
    else
        notify-send -u critical "无法安装软件" "${message}" 2>/dev/null || true
    fi
}

current_user="$(id -un)"
admin_status="$(/usr/local/sbin/ming-admin-bootstrap status \
    --user "${current_user}" --json 2>/dev/null || true)"
if ! grep -Fq '"ready": true' <<<"${admin_status}"; then
    show_blocking_error "请先完成首次开机账户设置，再安装需要管理员权限的软件。"
    if [[ -x /usr/local/bin/ming-oobe-account ]]; then
        nohup /usr/local/bin/ming-oobe-account \
            >"${XDG_RUNTIME_DIR:-/tmp}/ming-oobe-account.log" 2>&1 </dev/null &
    fi
    exit 4
fi

polkit_agent_pattern='lxpolkit|polkit-gnome-authentication-agent'
if ! pgrep -u "$(id -u)" -f "${polkit_agent_pattern}" >/dev/null 2>&1; then
    if command -v lxpolkit >/dev/null 2>&1; then
        nohup lxpolkit >"${XDG_RUNTIME_DIR:-/tmp}/ming-polkit-agent.log" 2>&1 </dev/null &
        for _attempt in 1 2 3 4 5 6 7 8 9 10; do
            pgrep -u "$(id -u)" -f "${polkit_agent_pattern}" >/dev/null 2>&1 && break
            sleep 0.2
        done
    fi
fi
if ! pgrep -u "$(id -u)" -f "${polkit_agent_pattern}" >/dev/null 2>&1; then
    show_blocking_error "系统授权服务尚未就绪，请注销并重新登录后再试。"
    exit 5
fi

result_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/ming-package-result.XXXXXX" 2>/dev/null || true)"
if [[ -z "${result_file}" ]]; then
    notify-send -u critical "安装 DEB 软件包" "无法创建安装结果文件。" 2>/dev/null || true
    exit 1
fi
trap 'rm -f "${result_file}"' EXIT

if /usr/local/bin/ming-authorized-action package install "${package_file}" >"${result_file}" 2>&1; then
    installer_rc=0
else
    installer_rc=$?
fi

if python3 - "${result_file}" "${installer_rc}" << 'MINGPACKAGEUIPY'
import json
from pathlib import Path
import shutil
import subprocess
import sys

raw = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").strip()
return_code = int(sys.argv[2])
try:
    result = json.loads(raw)
except (TypeError, ValueError):
    result = {}
installed = bool(result.get("installed"))
launch_ready = bool(result.get("launch_ready"))
state = str(result.get("state") or "")
refresh_warning = installed and state == "installed_with_refresh_warning"
ok = bool(result.get("ok")) and installed and launch_ready and return_code == 0
package = str(result.get("package") or "该软件")
version = str(result.get("version") or "")
log_path = str(result.get("log_path") or "/var/log/ming-package-installer.log")
launcher_warnings = result.get("launcher_warnings")
launcher_warnings = launcher_warnings if isinstance(launcher_warnings, list) else []
if ok:
    title = "软件安装完成"
    detail = "已安装：%s%s\n应用抽屉和桌面将自动刷新。\n日志：%s" % (
        package, (" " + version) if version else "", log_path)
    if launcher_warnings:
        title = "软件已安装，但启动器需要修复"
        warnings = [str(item.get("error") or "启动器不可用")
                    for item in launcher_warnings if isinstance(item, dict)]
        detail += "\n\n注意：" + "；".join(warnings[:3])
elif refresh_warning:
    title = "软件已安装，但桌面刷新失败"
    reason = str(result.get("error") or "桌面刷新失败，可点击刷新/重试。")
    detail = "%s\n可点击刷新/重试；软件本体已经安装。\n日志：%s" % (
        reason[:1200], log_path)
elif installed and not launch_ready:
    title = "软件已安装，但无法确认可启动"
    reason = str(result.get("error") or "未找到可验证的图形启动器。")
    detail = "%s\n日志：%s" % (reason[:1200], log_path)
else:
    title = "软件安装失败"
    reason = str(result.get("error") or raw or "安装被取消或未返回可读结果。")
    detail = "%s\n日志：%s" % (reason[:1200], log_path)
if shutil.which("zenity"):
    subprocess.run(
        ["zenity", "--info" if ok else ("--warning" if refresh_warning else "--error"), "--title=" + title,
         "--text=" + detail, "--width=520"], check=False)
elif shutil.which("notify-send"):
    subprocess.run(
        ["notify-send", "-u", "normal" if ok or refresh_warning else "critical", title, detail], check=False)
else:
    print(title + "\n" + detail, file=sys.stderr)
raise SystemExit(0 if ok or refresh_warning else 1)
MINGPACKAGEUIPY
then
    if command -v ming-phone-desktop >/dev/null 2>&1; then
        ming-phone-desktop --sync >/dev/null 2>&1 || true
    fi
    if command -v ming-refresh-desktop-state >/dev/null 2>&1; then
        ming-refresh-desktop-state >/dev/null 2>&1 || true
    fi
    if command -v ming-refresh-dock-launchers >/dev/null 2>&1; then
        ming-refresh-dock-launchers "$(id -un)" >/dev/null 2>&1 || true
    fi
    [[ "${installer_rc}" -eq 0 ]] && exit 0
    exit "${installer_rc}"
fi
exit 1
MINGPACKAGEGUI
    chmod 0755 /usr/local/bin/ming-package-install-gui

    cat > /usr/local/bin/ming-appimage-install-gui << 'MINGAPPIMAGEGUI'
#!/usr/bin/env bash
set -uo pipefail

appimage_file="${1:-}"
if [[ -z "${appimage_file}" || ! -f "${appimage_file}" ]]; then
    notify-send -u critical "安装 AppImage" "找不到要安装的 AppImage 文件。" 2>/dev/null || true
    exit 2
fi
result_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/ming-appimage-result.XXXXXX" 2>/dev/null || true)"
[[ -n "${result_file}" ]] || exit 1
trap 'rm -f "${result_file}"' EXIT
if /usr/local/bin/ming-appimage-installer "${appimage_file}" >"${result_file}"; then
    rc=0
else
    rc=$?
fi
if python3 - "${result_file}" "${rc}" << 'MINGAPPIMAGEUIPY'
import json
import shutil
import subprocess
import sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").strip()
try:
    result = json.loads(raw)
except (TypeError, ValueError):
    result = {}
ok = bool(result.get("ok")) and int(sys.argv[2]) == 0
if ok:
    title = "AppImage 安装完成"
    text = "已添加到应用抽屉：%s" % result.get("desktop_file", "")
else:
    title = "AppImage 安装失败"
    text = str(result.get("error") or "文件格式不受支持。")
if shutil.which("zenity"):
    subprocess.run(["zenity", "--info" if ok else "--error", "--title=" + title, "--text=" + text, "--width=520"], check=False)
elif shutil.which("notify-send"):
    subprocess.run(["notify-send", "-u", "normal" if ok else "critical", title, text], check=False)
raise SystemExit(0 if ok else 1)
MINGAPPIMAGEUIPY
then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${HOME}/.local/share/applications" >/dev/null 2>&1 || true
    fi
    if command -v ming-phone-desktop >/dev/null 2>&1; then
        ming-phone-desktop --sync >/dev/null 2>&1 || true
    fi
    if command -v ming-refresh-desktop-state >/dev/null 2>&1; then
        ming-refresh-desktop-state >/dev/null 2>&1 || true
    fi
    if command -v ming-refresh-dock-launchers >/dev/null 2>&1; then
        ming-refresh-dock-launchers "$(id -un)" >/dev/null 2>&1 || true
    fi
    exit 0
fi
exit 1
MINGAPPIMAGEGUI
    chmod 0755 /usr/local/bin/ming-appimage-install-gui

    cat > /usr/share/applications/ming-appimage-installer.desktop << 'MINGAPPIMAGEINSTALLERDESKTOP'
[Desktop Entry]
Type=Application
Name=安装 AppImage
Name[zh_CN]=安装 AppImage
Comment=Install an AppImage for the current user
Comment[zh_CN]=为当前用户安全安装 AppImage
Exec=/usr/local/bin/ming-appimage-install-gui %f
Icon=application-x-executable
Terminal=false
MimeType=application/x-appimage;
NoDisplay=true
StartupNotify=true
MINGAPPIMAGEINSTALLERDESKTOP
    cp /usr/share/applications/ming-appimage-installer.desktop \
        "/home/${MING_USER}/.local/share/applications/"
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.local/share/applications/ming-appimage-installer.desktop"

    # Both browser downloads and Ming Files resolve Debian packages through
    # this unprivileged MIME handler.  Only the installer itself requests the
    # narrowly scoped polkit privilege.
    cat > /usr/share/applications/ming-package-installer.desktop << 'MINGPACKAGEINSTALLERDESKTOP'
[Desktop Entry]
Type=Application
Name=安装 DEB 软件包
Name[zh_CN]=安装 DEB 软件包
Comment=验证并安装本地 Debian 软件包
Comment[zh_CN]=验证并安装本地 Debian 软件包
Exec=/usr/local/bin/ming-package-install-gui %f
Icon=package-x-generic
Terminal=false
MimeType=application/vnd.debian.binary-package;
NoDisplay=true
StartupNotify=true
MINGPACKAGEINSTALLERDESKTOP
    cp /usr/share/applications/ming-package-installer.desktop \
        "/home/${MING_USER}/.local/share/applications/"
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.local/share/applications/ming-package-installer.desktop"

    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/ming-launch-broker.desktop" << 'MINGLAUNCHAUTO'
[Desktop Entry]
Type=Application
Name=Ming Launch Broker
Exec=/usr/local/bin/ming-launch --server
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=1
MINGLAUNCHAUTO
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.config/autostart/ming-launch-broker.desktop"

    # Start the user-level PulseAudio health check after the graphical session
    # is ready.  The helper is bounded, records diagnostics in the user's
    # cache and never replaces an already valid HDMI/Bluetooth/USB selection.
    cat > "/home/${MING_USER}/.config/autostart/ming-audio-session.desktop" << 'MINGAUDIOAUTO'
[Desktop Entry]
Type=Application
Name=Ming Audio Session Recovery
Exec=/usr/local/bin/ming-audio-session ensure --json
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
MINGAUDIOAUTO
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.config/autostart/ming-audio-session.desktop"

    cat > "/home/${MING_USER}/.config/autostart/ming-software-brightness.desktop" << 'MINGSOFTWAREBRIGHTNESSAUTO'
[Desktop Entry]
Type=Application
Name=Ming Software Brightness Restore
Exec=/usr/local/bin/ming-device-control reapply-brightness --wait-seconds 10 --json
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
MINGSOFTWAREBRIGHTNESSAUTO
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.config/autostart/ming-software-brightness.desktop"

    cat > /usr/local/bin/ming-app-library << 'MINGDRAWERCOMPAT'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/bin/ming-app-drawer --toggle "$@"
MINGDRAWERCOMPAT
    chmod 0755 /usr/local/bin/ming-app-library

    cat > /usr/share/applications/ming-app-library.desktop << 'MINGDRAWERDESKTOP'
[Desktop Entry]
Type=Application
Name=Ming 应用抽屉
Name[zh_CN]=Ming 应用抽屉
Comment=Browse and launch installed applications
Comment[zh_CN]=从底部抽屉浏览并启动应用
Exec=/usr/local/bin/ming-app-drawer --toggle
Icon=ming-app-library
Terminal=false
Categories=Utility;System;
StartupNotify=false
NoDisplay=true
MINGDRAWERDESKTOP
    cp /usr/share/applications/ming-app-library.desktop "/home/${MING_USER}/.local/share/applications/"
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.local/share/applications/ming-app-library.desktop"
}

install_ming_files() {
    local asset_dir="/tmp/ming-build/assets"
    local lib_dir="/usr/local/lib/ming-os"
    mkdir -p "${lib_dir}" /usr/local/bin "/home/${MING_USER}/.config"
    for asset in ming-files.py ming-files-model.py; do
        if [[ ! -s "${asset_dir}/${asset}" ]]; then
            echo "ERROR: missing Ming Files asset: ${asset}" >&2
            return 1
        fi
    done

    install -m 0755 "${asset_dir}/ming-files.py" "${lib_dir}/ming-files.py"
    install -m 0644 "${asset_dir}/ming-files-model.py" "${lib_dir}/ming-files-model.py"
    cat > /usr/local/bin/ming-files << 'MINGFILESWRAPPER'
#!/usr/bin/env bash
set -uo pipefail
log="${HOME}/.cache/ming-os/ming-files.log"
mkdir -p "$(dirname "${log}")"
python3 /usr/local/lib/ming-os/ming-files.py "$@" 2>>"${log}"
rc=$?
if [[ "${rc}" -ne 0 ]] && command -v thunar >/dev/null 2>&1; then
    notify-send "Ming 文件" "Ming Files 运行组件不可用，已切换到兼容文件管理器。日志：${log}" 2>/dev/null || true
    exec thunar "$@"
fi
exit "${rc}"
MINGFILESWRAPPER
    chmod 0755 /usr/local/bin/ming-files

    cat > /usr/share/applications/ming-files.desktop << 'MINGFILESDESKTOP'
[Desktop Entry]
Type=Application
Name=Ming 文件
Name[zh_CN]=Ming 文件
Comment=Browse files, disks and network locations
Comment[zh_CN]=浏览文件、磁盘与网络位置
Exec=/usr/local/bin/ming-files %U
Icon=files-icon
Terminal=false
Categories=System;FileManager;
MimeType=inode/directory;application/x-gnome-saved-search;
StartupNotify=true
MINGFILESDESKTOP
    cp /usr/share/applications/ming-files.desktop "/home/${MING_USER}/.local/share/applications/"
    python3 - "/home/${MING_USER}/.config/mimeapps.list" << 'MINGMIMEAPPS'
import configparser
from pathlib import Path
import sys

path = Path(sys.argv[1])
config = configparser.ConfigParser(interpolation=None, strict=False)
config.optionxform = str
if path.exists():
    config.read(path, encoding="utf-8")
for section in ("Default Applications", "Added Associations"):
    if not config.has_section(section):
        config.add_section(section)

def remove_handler(section, mime_type, handler):
    entries = [item for item in config[section].get(mime_type, "").split(";") if item]
    entries = [item for item in entries if item != handler]
    if entries:
        config[section][mime_type] = ";".join(entries) + ";"
    else:
        config[section].pop(mime_type, None)

# Older Ming images associated every executable with the AppImage installer.
# Remove only that retired handler and preserve any explicit user choice.
remove_handler("Default Applications", "application/x-executable", "ming-appimage-installer.desktop")
remove_handler("Added Associations", "application/x-executable", "ming-appimage-installer.desktop")
config["Default Applications"]["inode/directory"] = "ming-files.desktop"
config["Default Applications"]["application/x-gnome-saved-search"] = "ming-files.desktop"
config["Default Applications"]["application/vnd.debian.binary-package"] = "ming-package-installer.desktop"
config["Default Applications"]["application/x-appimage"] = "ming-appimage-installer.desktop"
existing = config["Added Associations"].get("inode/directory", "")
items = [item for item in existing.split(";") if item]
items = ["ming-files.desktop"] + [item for item in items if item != "ming-files.desktop"]
config["Added Associations"]["inode/directory"] = ";".join(items) + ";"
with path.open("w", encoding="utf-8") as handle:
    config.write(handle, space_around_delimiters=False)
MINGMIMEAPPS
    chown -R "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.local/share/applications/ming-files.desktop" \
        "/home/${MING_USER}/.config/mimeapps.list"
}

# ======================== HiDPI 自动缩放 ========================

configure_hidpi_autoscale() {
    cat > /usr/local/bin/ming-scale << 'MINGSCALE'
#!/usr/bin/env bash
# Ming OS 自动缩放 - 覆盖所有屏幕比例与分辨率
# 支持: 5:4 / 4:3 / 16:9 / 16:10 / 21:9 / 32:9 及纵向旋转

SCALE_CONFIG="${HOME}/.config/ming-os/scale-done"
SCALE_PREFERENCE="${HOME}/.config/ming-os/scale-preference.json"
SCALE_POLICY_VERSION=2
if [[ -s "${SCALE_PREFERENCE}" ]]; then
    # Ming Settings wrote an explicit accessibility choice.  A repair or
    # resolution change must not silently replace it with an auto default.
    exit 0
fi
if [[ -f "${SCALE_CONFIG}" ]] && \
   grep -Fxq "font-policy=${SCALE_POLICY_VERSION}" "${SCALE_CONFIG}" 2>/dev/null; then
    exit 0
fi
mkdir -p "$(dirname "${SCALE_CONFIG}")"

# 等待 Xorg 就绪
for i in $(seq 1 15); do
    if xrandr --current &>/dev/null; then break; fi
    sleep 1
done

RESOLUTION=$(xrandr --current 2>/dev/null | grep '*' | head -1 | awk '{print $1}')
WIDTH=$(echo "${RESOLUTION}" | cut -d'x' -f1 2>/dev/null || echo "1920")
HEIGHT=$(echo "${RESOLUTION}" | cut -d'x' -f2 2>/dev/null || echo "1080")
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
LOW_MEMORY=0
if [[ "${MEM_MB}" -le 2600 ]]; then
    LOW_MEMORY=1
fi

# 宽高比计算
if [[ -n "${HEIGHT}" && "${HEIGHT}" -gt 0 ]]; then
    ASPECT=$(awk "BEGIN {printf \"%.2f\", ${WIDTH}/${HEIGHT}}")
else
    ASPECT=1.78
fi

# DPI策略：像素密度 / 屏幕物理尺寸估算
# PANEL_SIZE = 顶部菜单栏高度；DOCK_ICON = Plank 底部 Dock 图标尺寸
if [[ "${WIDTH}" -ge 5120 ]]; then
    DPI=240;   PANEL_SIZE=40; DOCK_ICON=72; CURSOR_SIZE=44; FONT_SIZE=16
elif [[ "${WIDTH}" -ge 3840 ]]; then
    DPI=192;   PANEL_SIZE=36; DOCK_ICON=64; CURSOR_SIZE=36; FONT_SIZE=14
elif [[ "${WIDTH}" -ge 2560 ]]; then
    DPI=144;   PANEL_SIZE=32; DOCK_ICON=56; CURSOR_SIZE=30; FONT_SIZE=12
elif [[ "${WIDTH}" -ge 1920 ]]; then
    DPI=96;    PANEL_SIZE=30; DOCK_ICON=48; CURSOR_SIZE=24; FONT_SIZE=11
elif [[ "${WIDTH}" -ge 1680 ]]; then
    DPI=96;    PANEL_SIZE=28; DOCK_ICON=44; CURSOR_SIZE=22; FONT_SIZE=11
elif [[ "${WIDTH}" -ge 1440 ]]; then
    DPI=96;    PANEL_SIZE=28; DOCK_ICON=42; CURSOR_SIZE=22; FONT_SIZE=10
elif [[ "${WIDTH}" -ge 1366 ]]; then
    DPI=96;    PANEL_SIZE=26; DOCK_ICON=40; CURSOR_SIZE=22; FONT_SIZE=10
elif [[ "${WIDTH}" -ge 1280 ]]; then
    DPI=96;    PANEL_SIZE=26; DOCK_ICON=38; CURSOR_SIZE=20; FONT_SIZE=10
elif [[ "${WIDTH}" -ge 1024 ]]; then
    DPI=96;    PANEL_SIZE=24; DOCK_ICON=34; CURSOR_SIZE=18; FONT_SIZE=10
else
    DPI=96;    PANEL_SIZE=24; DOCK_ICON=30; CURSOR_SIZE=18; FONT_SIZE=10
fi

# 纵向模式修正（如平板旋转）— 用 awk 做数值比较，避免字符串字典序误判
if awk "BEGIN {exit !(${ASPECT} < 1.0)}"; then
    PANEL_SIZE=$((PANEL_SIZE + 2))
    FONT_SIZE=$((FONT_SIZE + 1))
fi

# 矮屏幕修正（小于 800px 高度）— 紧缩面板与 Dock
if [[ "${HEIGHT}" -lt 800 ]]; then
    PANEL_SIZE=$((PANEL_SIZE > 24 ? PANEL_SIZE - 2 : 22))
    DOCK_ICON=$((DOCK_ICON > 32 ? DOCK_ICON - 6 : 30))
    FONT_SIZE=$((FONT_SIZE > 10 ? FONT_SIZE - 1 : 10))
fi

if [[ "${LOW_MEMORY}" -eq 1 ]]; then
    DOCK_ICON=$((DOCK_ICON > 36 ? 36 : DOCK_ICON))
    PANEL_SIZE=$((PANEL_SIZE > 26 ? 26 : PANEL_SIZE))
    FONT_SIZE=$((FONT_SIZE > 10 ? 10 : FONT_SIZE))
fi

# 应用设置
xfconf-query -c xsettings -p /Xft/DPI -s "${DPI}" 2>/dev/null || true
xfconf-query -c xsettings -p /Gtk/CursorThemeSize -s "${CURSOR_SIZE}" 2>/dev/null || true
xfconf-query -c xsettings -p /Gtk/FontName -s "Noto Sans CJK SC ${FONT_SIZE}" 2>/dev/null || true
xfconf-query -c xfce4-panel -p /panels/panel-0/size -s "${PANEL_SIZE}" 2>/dev/null || true

# Plank Dock 图标尺寸（写入 dconf；Plank 优先读 dconf 再回退 settings 文件）
if command -v dconf &>/dev/null; then
    dconf write /net/launchpad/plank/docks/dock1/icon-size "${DOCK_ICON}" 2>/dev/null || true
fi
# 同步更新 settings 文件，确保下次启动一致
PLANK_SETTINGS="${HOME}/.config/plank/dock1/settings"
if [[ -f "${PLANK_SETTINGS}" ]]; then
    sed -i "s/^IconSize=.*/IconSize=${DOCK_ICON}/" "${PLANK_SETTINGS}" 2>/dev/null || true
    if [[ "${LOW_MEMORY}" -eq 1 ]]; then
        sed -i "s/^ZoomEnabled=.*/ZoomEnabled=false/" "${PLANK_SETTINGS}" 2>/dev/null || true
        sed -i "s/^ZoomPercent=.*/ZoomPercent=100/" "${PLANK_SETTINGS}" 2>/dev/null || true
    fi
fi

# Whisfer Menu 高度安全约束（不能超出屏幕 75%）
MENU_HEIGHT=$((HEIGHT * 72 / 100))
if [[ "${MENU_HEIGHT}" -gt 540 ]]; then MENU_HEIGHT=540; fi
if [[ "${MENU_HEIGHT}" -lt 360 ]]; then MENU_HEIGHT=360; fi
xfconf-query -c xfce4-panel -p /plugins/plugin-1/menu-height -s "${MENU_HEIGHT}" 2>/dev/null || true

if [[ "${LOW_MEMORY}" -eq 1 ]]; then
    cat > "${HOME}/.config/ming-os/memory-profile" << PROFILE
profile=low-memory
mem_mb=${MEM_MB}
picom=xrender
wechat=light
dock_zoom=false
PROFILE
else
    cat > "${HOME}/.config/ming-os/memory-profile" << PROFILE
profile=balanced
mem_mb=${MEM_MB}
picom=auto
wechat=auto
dock_zoom=true
PROFILE
fi

printf "font-policy=%s\n" "${SCALE_POLICY_VERSION}" > "${SCALE_CONFIG}"
MINGSCALE

    chmod +x /usr/local/bin/ming-scale

    # 创建自启动项
    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/ming-scale.desktop" << SCALEAUTOSTART
[Desktop Entry]
Type=Application
Name=Ming Display Scale
Comment=Auto-configure display scaling
Exec=/usr/local/bin/ming-scale
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
SCALEAUTOSTART
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/autostart/ming-scale.desktop"
}

# ======================== Ming OS 品牌图标生成 (SVG) ========================

generate_ming_icons() {
    local icon_base="/usr/share/icons/hicolor"
    mkdir -p "${icon_base}/32x32/apps" "${icon_base}/48x48/apps" \
             "${icon_base}/64x64/apps" "${icon_base}/128x128/apps" \
             "${icon_base}/scalable/apps"

    # 菜单图标 (32x32)
    cat > "${icon_base}/32x32/apps/ming-os-menu.svg" << 'MENUICON'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="mingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#285F58"/>
      <stop offset="100%" style="stop-color:#173D39"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="35%" r="50%">
      <stop offset="0%" style="stop-color:#E8F4F0;stop-opacity:0.46"/>
      <stop offset="100%" style="stop-color:#2F8A7D;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="32" height="32" rx="6" fill="url(#mingGrad)"/>
  <rect width="32" height="32" rx="6" fill="url(#glow)"/>
  <ellipse cx="16" cy="13" rx="7" ry="5" fill="none" stroke="#ECFAF7" stroke-width="1.5" opacity="0.88"/>
  <ellipse cx="16" cy="9" rx="5" ry="3.5" fill="none" stroke="#DFF6F1" stroke-width="1.2" opacity="0.76"/>
  <path d="M13 6 Q16 2 19 6 Q16 8 13 6Z" fill="#ECFAF7" opacity="0.65"/>
  <path d="M14 20 Q16 18 18 20 L18 24 Q16 25 14 24Z" fill="#DFF6F1" opacity="0.45"/>
  <circle cx="16" cy="13" r="2" fill="#ECFAF7" opacity="0.36"/>
</svg>
MENUICON

    # 48x48
    cp "${icon_base}/32x32/apps/ming-os-menu.svg" "${icon_base}/48x48/apps/ming-os-menu.svg"
    # 实际 48x48 版本
    cat > "${icon_base}/48x48/apps/ming-os-menu.svg" << 'MENUICON48'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="mingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#285F58"/>
      <stop offset="100%" style="stop-color:#173D39"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="35%" r="50%">
      <stop offset="0%" style="stop-color:#E8F4F0;stop-opacity:0.34"/>
      <stop offset="100%" style="stop-color:#2F8A7D;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="48" height="48" rx="8" fill="url(#mingGrad)"/>
  <rect width="48" height="48" rx="8" fill="url(#glow)"/>
  <ellipse cx="24" cy="19" rx="10" ry="7" fill="none" stroke="#ECFAF7" stroke-width="2" opacity="0.88"/>
  <ellipse cx="24" cy="13" rx="7" ry="5" fill="none" stroke="#DFF6F1" stroke-width="1.5" opacity="0.76"/>
  <ellipse cx="24" cy="8" rx="4.5" ry="3.2" fill="none" stroke="#A8DCD4" stroke-width="1.2" opacity="0.68"/>
  <path d="M19 9 Q24 3 29 9 Q24 11 19 9Z" fill="#ECFAF7" opacity="0.55"/>
  <circle cx="24" cy="19" r="3" fill="#ECFAF7" opacity="0.28"/>
</svg>
MENUICON48

    # 系统 Logo (128x128)
    cat > "${icon_base}/128x128/apps/ming-os-logo.svg" << 'LOGOICON'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#3AA891"/>
      <stop offset="100%" style="stop-color:#1F6F67"/>
    </linearGradient>
    <radialGradient id="logoGlow" cx="50%" cy="40%" r="50%">
      <stop offset="0%" style="stop-color:#FFFFFF;stop-opacity:0.28"/>
      <stop offset="100%" style="stop-color:#D7EAE4;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="128" height="128" rx="24" fill="url(#logoGrad)"/>
  <rect width="128" height="128" rx="24" fill="url(#logoGlow)"/>
  <circle cx="64" cy="52" r="30" fill="none" stroke="#EAFBF6" stroke-width="1.5" opacity="0.42"/>
  <circle cx="64" cy="52" r="22" fill="none" stroke="#EAFBF6" stroke-width="1.5" opacity="0.58"/>
  <circle cx="64" cy="52" r="14" fill="none" stroke="#FFFFFF" stroke-width="1.5" opacity="0.72"/>
  <path d="M64 18 Q72 44 64 52 Q56 44 64 18Z" fill="#FFFFFF" opacity="0.42"/>
  <path d="M51 32 Q64 44 64 52 Q56 44 51 32Z" fill="#EAFBF6" opacity="0.34"/>
  <path d="M77 32 Q64 44 64 52 Q72 44 77 32Z" fill="#EAFBF6" opacity="0.34"/>
  <circle cx="64" cy="34" r="5" fill="#FFFFFF" opacity="0.38"/>
  <circle cx="64" cy="52" r="4" fill="#FFFFFF" opacity="0.28"/>
  <text x="64" y="98" text-anchor="middle" fill="#EAFBF6" font-family="sans-serif" font-size="11" font-weight="bold" opacity="0.86">MING OS</text>
</svg>
LOGOICON

    # 文件管理器图标 (32x32) - 简洁文件夹，深紫渐变，玻璃透明效果
    cat > "${icon_base}/32x32/apps/files-icon.svg" << 'FILESICON32'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="filesGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
    <radialGradient id="filesGlow" cx="50%" cy="30%" r="55%">
      <stop offset="0%" style="stop-color:#D6EEE8;stop-opacity:0.34"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="32" height="32" rx="7" fill="url(#filesGrad)"/>
  <rect width="32" height="32" rx="7" fill="url(#filesGlow)"/>
  <path d="M4 9 L4 25 Q4 27 6 27 L26 27 Q28 27 28 25 L28 11 Q28 9 26 9 L15 9 L13 6 L5 6 Q4 6 4 7Z" fill="none" stroke="#D4F7F1" stroke-width="1.5" opacity="0.9"/>
  <path d="M4 9 L15 9 L13 6 L5 6 Q4 6 4 7Z" fill="#9FE7D7" opacity="0.3"/>
  <rect x="6" y="11" width="20" height="14" rx="1.5" fill="#D4F7F1" opacity="0.12"/>
</svg>
FILESICON32

    # 文件管理器图标 (48x48)
    cat > "${icon_base}/48x48/apps/files-icon.svg" << 'FILESICON48'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="filesGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1FA89E"/>
      <stop offset="100%" style="stop-color:#0E5C54"/>
    </linearGradient>
    <radialGradient id="filesGlow" cx="50%" cy="30%" r="55%">
      <stop offset="0%" style="stop-color:#9FE7D7;stop-opacity:0.45"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#filesGrad)"/>
  <rect width="48" height="48" rx="10" fill="url(#filesGlow)"/>
  <path d="M6 13 L6 37 Q6 40 9 40 L39 40 Q42 40 42 37 L42 17 Q42 14 39 14 L22 14 L19 9 L7 9 Q6 9 6 10Z" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <path d="M6 13 L22 13 L19 9 L7 9 Q6 9 6 10Z" fill="#9FE7D7" opacity="0.28"/>
  <rect x="9" y="16" width="30" height="21" rx="2" fill="#D4F7F1" opacity="0.10"/>
</svg>
FILESICON48

    # 浏览器图标 (32x32) - 地球/浏览器，紫色渐变，玻璃效果
    cat > "${icon_base}/32x32/apps/browser-icon.svg" << 'BROWSERICON32'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="browserGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
    <radialGradient id="browserGlow" cx="50%" cy="35%" r="50%">
      <stop offset="0%" style="stop-color:#D6EEE8;stop-opacity:0.34"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="32" height="32" rx="7" fill="url(#browserGrad)"/>
  <rect width="32" height="32" rx="7" fill="url(#browserGlow)"/>
  <circle cx="16" cy="16" r="10" fill="none" stroke="#D4F7F1" stroke-width="1.5" opacity="0.85"/>
  <ellipse cx="16" cy="16" rx="10" ry="4" fill="none" stroke="#9FE7D7" stroke-width="1" opacity="0.6"/>
  <ellipse cx="16" cy="16" rx="4" ry="10" fill="none" stroke="#9FE7D7" stroke-width="1" opacity="0.6"/>
  <line x1="6" y1="16" x2="26" y2="16" stroke="#9FE7D7" stroke-width="0.8" opacity="0.5"/>
  <circle cx="16" cy="16" r="3" fill="#D4F7F1" opacity="0.25"/>
</svg>
BROWSERICON32

    # 浏览器图标 (48x48)
    cat > "${icon_base}/48x48/apps/browser-icon.svg" << 'BROWSERICON48'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="browserGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1FA89E"/>
      <stop offset="100%" style="stop-color:#0E5C54"/>
    </linearGradient>
    <radialGradient id="browserGlow" cx="50%" cy="35%" r="50%">
      <stop offset="0%" style="stop-color:#9FE7D7;stop-opacity:0.45"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#browserGrad)"/>
  <rect width="48" height="48" rx="10" fill="url(#browserGlow)"/>
  <circle cx="24" cy="24" r="15" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.85"/>
  <ellipse cx="24" cy="24" rx="15" ry="6" fill="none" stroke="#9FE7D7" stroke-width="1.2" opacity="0.55"/>
  <ellipse cx="24" cy="24" rx="6" ry="15" fill="none" stroke="#9FE7D7" stroke-width="1.2" opacity="0.55"/>
  <line x1="9" y1="24" x2="39" y2="24" stroke="#9FE7D7" stroke-width="1" opacity="0.4"/>
  <circle cx="24" cy="24" r="4.5" fill="#D4F7F1" opacity="0.2"/>
</svg>
BROWSERICON48

    # 应用商店图标 (32x32) - 购物袋风格，紫色渐变，玻璃效果
    cat > "${icon_base}/32x32/apps/store-icon.svg" << 'STOREICON32'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="storeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
    <radialGradient id="storeGlow" cx="50%" cy="30%" r="50%">
      <stop offset="0%" style="stop-color:#D6EEE8;stop-opacity:0.34"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="32" height="32" rx="7" fill="url(#storeGrad)"/>
  <rect width="32" height="32" rx="7" fill="url(#storeGlow)"/>
  <path d="M8 10 L8 25 Q8 27 10 27 L22 27 Q24 27 24 25 L24 10Z" fill="none" stroke="#D4F7F1" stroke-width="1.5" opacity="0.9"/>
  <path d="M11 10 Q11 5 16 5 Q21 5 21 10" fill="none" stroke="#D4F7F1" stroke-width="1.5" opacity="0.85"/>
  <line x1="8" y1="14" x2="24" y2="14" stroke="#9FE7D7" stroke-width="1" opacity="0.5"/>
  <circle cx="13" cy="20" r="1.5" fill="#D4F7F1" opacity="0.5"/>
  <circle cx="19" cy="20" r="1.5" fill="#D4F7F1" opacity="0.5"/>
</svg>
STOREICON32

    # 应用商店图标 (48x48) - 购物袋风格
    cat > "${icon_base}/48x48/apps/store-icon.svg" << 'STOREICON48'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="storeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
    <radialGradient id="storeGlow" cx="50%" cy="30%" r="50%">
      <stop offset="0%" style="stop-color:#9FE7D7;stop-opacity:0.45"/>
      <stop offset="100%" style="stop-color:#1FA89E;stop-opacity:0"/>
    </radialGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#storeGrad)"/>
  <rect width="48" height="48" rx="10" fill="url(#storeGlow)"/>
  <path d="M11 14 L11 38 Q11 41 14 41 L34 41 Q37 41 37 38 L37 14Z" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <path d="M16 14 Q16 7 24 7 Q32 7 32 14" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.85"/>
  <line x1="11" y1="21" x2="37" y2="21" stroke="#9FE7D7" stroke-width="1.2" opacity="0.45"/>
  <circle cx="19" cy="31" r="2.5" fill="#D4F7F1" opacity="0.45"/>
  <circle cx="29" cy="31" r="2.5" fill="#D4F7F1" opacity="0.45"/>
</svg>
STOREICON48

    # 应用商店图标 (48x48)
    cat > "${icon_base}/48x48/apps/ming-app-store.svg" << STOREICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="storeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1FA89E"/>
      <stop offset="100%" style="stop-color:#0E5C54"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#storeGrad)"/>
  <rect x="10" y="14" width="28" height="24" rx="3" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <line x1="10" y1="22" x2="38" y2="22" stroke="#D4F7F1" stroke-width="1.5" opacity="0.7"/>
  <circle cx="16" cy="33" r="2" fill="#D4F7F1" opacity="0.7"/>
  <circle cx="24" cy="33" r="2" fill="#D4F7F1" opacity="0.7"/>
  <circle cx="32" cy="33" r="2" fill="#D4F7F1" opacity="0.7"/>
  <circle cx="16" cy="27" r="1.5" fill="#9FE7D7" opacity="0.5"/>
  <circle cx="24" cy="27" r="1.5" fill="#9FE7D7" opacity="0.5"/>
  <path d="M18 14 L16 6 L20 6Z" fill="#D4F7F1" opacity="0.5"/>
  <path d="M30 14 L28 6 L32 6Z" fill="#D4F7F1" opacity="0.5"/>
  <line x1="20" y1="10" x2="28" y2="10" stroke="#D4F7F1" stroke-width="1.5" opacity="0.4"/>
</svg>
STOREICON

    # Generic security icon kept for Settings status surfaces.
    cat > "${icon_base}/48x48/apps/ming-security.svg" << SECICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="secGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#174C47"/>
      <stop offset="100%" style="stop-color:#2F8A7D"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#secGrad)"/>
  <path d="M24 4 L38 12 L38 26 Q38 36 24 44 Q10 36 10 26 L10 12Z" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <path d="M24 16 L20 22 L28 22 L24 30" fill="none" stroke="#9FE7D7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.8"/>
</svg>
SECICON

    # 缩放显示图标
    cat > "${icon_base}/48x48/apps/ming-display.svg" << DISPICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="dispGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#dispGrad)"/>
  <rect x="6" y="8" width="36" height="26" rx="3" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <rect x="10" y="12" width="28" height="18" rx="1" fill="#F6FAF8" opacity="0.28"/>
  <line x1="6" y1="36" x2="18" y2="44" stroke="#D4F7F1" stroke-width="2" opacity="0.7"/>
  <line x1="42" y1="36" x2="30" y2="44" stroke="#D4F7F1" stroke-width="2" opacity="0.7"/>
  <line x1="16" y1="40" x2="32" y2="40" stroke="#9FE7D7" stroke-width="1.5" opacity="0.5"/>
</svg>
DISPICON

    # 更新管理器图标
    cat > "${icon_base}/48x48/apps/ming-update-icon.svg" << UPDICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="updGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#updGrad)"/>
  <path d="M24 8 L24 16" stroke="#D4F7F1" stroke-width="2.5" stroke-linecap="round" opacity="0.9"/>
  <path d="M18 14 L24 6 L30 14" fill="none" stroke="#D4F7F1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.8"/>
  <circle cx="24" cy="28" r="12" fill="none" stroke="#9FE7D7" stroke-width="2" opacity="0.7"/>
  <path d="M24 22 L24 30 M20 28 L28 28" stroke="#D4F7F1" stroke-width="2" stroke-linecap="round" opacity="0.8"/>
</svg>
UPDICON

    # 系统设置图标
    cat > "${icon_base}/48x48/apps/ming-settings.svg" << SETICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <rect width="48" height="48" rx="10" fill="#174C47"/>
  <circle cx="24" cy="24" r="5" fill="none" stroke="#D4F7F1" stroke-width="2" opacity="0.9"/>
  <path d="M24 4 L24 12 M24 36 L24 44 M4 24 L12 24 M36 24 L44 24" stroke="#D4F7F1" stroke-width="2" stroke-linecap="round" opacity="0.6"/>
  <path d="M10 10 L16 16 M32 32 L38 38 M38 10 L32 16 M10 38 L16 32" stroke="#9FE7D7" stroke-width="1.5" stroke-linecap="round" opacity="0.4"/>
  <circle cx="24" cy="24" r="12" fill="none" stroke="#1FA89E" stroke-width="1" opacity="0.3"/>
</svg>
SETICON

    cat > "${icon_base}/48x48/apps/ming-control-center.svg" << CONTROLICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="ctrlGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#3AA891"/>
      <stop offset="55%" style="stop-color:#2F8A7D"/>
      <stop offset="100%" style="stop-color:#174C47"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="url(#ctrlGrad)"/>
  <rect x="9" y="11" width="30" height="26" rx="5" fill="#FFFFFF" opacity="0.18"/>
  <circle cx="18" cy="20" r="4" fill="none" stroke="#9FE7D7" stroke-width="2"/>
  <path d="M28 18h7M28 22h5M13 31h22" stroke="#D4F7F1" stroke-width="2" stroke-linecap="round" opacity="0.9"/>
  <path d="M15 9l4-5 4 5" fill="none" stroke="#9FE7D7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/>
</svg>
CONTROLICON

    cat > "${icon_base}/48x48/apps/ming-terminal.svg" << TERMICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <rect width="48" height="48" rx="10" fill="#1A2220"/>
  <rect x="7" y="9" width="34" height="30" rx="5" fill="#232F2C" stroke="#2F8A7D" stroke-width="1.5"/>
  <path d="M15 20l5 4-5 4" fill="none" stroke="#9FE7D7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M24 29h9" stroke="#D4F7F1" stroke-width="2.2" stroke-linecap="round"/>
</svg>
TERMICON

    cat > "${icon_base}/48x48/apps/ming-app-library.svg" << APPLIBICON
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
  <defs>
    <linearGradient id="appLibGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#9FE7D7"/>
      <stop offset="48%" style="stop-color:#1F8A8A"/>
      <stop offset="100%" style="stop-color:#0E5C54"/>
    </linearGradient>
  </defs>
  <rect width="48" height="48" rx="10" fill="#1D2421"/>
  <rect x="5" y="5" width="38" height="38" rx="9" fill="url(#appLibGrad)" opacity="0.88"/>
  <g fill="#FFFFFF" opacity="0.92">
    <rect x="13" y="13" width="7" height="7" rx="2"/>
    <rect x="22" y="13" width="7" height="7" rx="2"/>
    <rect x="31" y="13" width="7" height="7" rx="2"/>
    <rect x="13" y="22" width="7" height="7" rx="2"/>
    <rect x="22" y="22" width="7" height="7" rx="2"/>
    <rect x="31" y="22" width="7" height="7" rx="2"/>
    <rect x="13" y="31" width="7" height="7" rx="2"/>
    <rect x="22" y="31" width="7" height="7" rx="2"/>
    <rect x="31" y="31" width="7" height="7" rx="2"/>
  </g>
</svg>
APPLIBICON

    # Update gtk icon cache
    gtk-update-icon-cache "${icon_base}" 2>/dev/null || true

    # MCP 生成的 macOS 风格图标（已是 squircle 圆角，直接缩放复制）
    local assets="/tmp/ming-build/assets/icons"
    if [[ -d "${assets}" ]]; then
        for size in 48 64 128 256; do
            mkdir -p "${icon_base}/${size}x${size}/apps"
        done

        # 资产名 -> 目标图标名（一对多用空格分隔）
        local -A png_map=(
            [settings]="ming-settings ming-control-center"
            [files]="ming-files"
            [terminal]="ming-terminal"
            [update]="ming-update-icon"
            [store]="ming-app-store spark-store"
            [app-library]="ming-app-library"
            [wechat-mgr]="ming-wechat-manager wechat"
        )
        for src_name in "${!png_map[@]}"; do
            local src_file="${assets}/${src_name}.png"
            [[ -f "${src_file}" ]] || continue
            for dest_name in ${png_map[$src_name]}; do
                for size in 48 64 128 256; do
                    local dest="${icon_base}/${size}x${size}/apps/${dest_name}.png"
                    if command -v convert &>/dev/null; then
                        convert "${src_file}" -resize "${size}x${size}" "${dest}" 2>/dev/null || \
                            cp "${src_file}" "${dest}"
                    else
                        cp "${src_file}" "${dest}"
                    fi
                done
            done
        done
        gtk-update-icon-cache "${icon_base}" 2>/dev/null || true
        echo "Ming SVG icons kept; optional PNG icons transparentized."
    fi

    generate_squircle_icons "${icon_base}" "${assets}"
}

# 把核心软件图标重塑为 macOS/Deepin 风格的 Squircle（超椭圆圆角平滑）瓷砖图标。
# 用 librsvg 渲染超椭圆遮罩，将去白底的彩色图标居中合成到带柔光的 squircle 上。
generate_squircle_icons() {
    local icon_base="$1"
    local assets="$2"
    command -v rsvg-convert &>/dev/null || command -v convert &>/dev/null || return 0
    [[ -d "${assets}" ]] || return 0

    local work; work="$(mktemp -d)"
    # squircle 遮罩（超椭圆形状，IM 也能正确渲染 path）。颜色瓷砖单独用
    # ImageMagick 原生渐变生成——IM 的 SVG delegate 不渲染 linearGradient 会掉色，
    # rsvg 在 chroot 内可用但不强依赖它。
    cat > "${work}/mask.svg" << 'MASKSVG'
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <path fill="#ffffff" d="M512 8
    C 180 8 8 180 8 512 C 8 844 180 1016 512 1016
    C 844 1016 1016 844 1016 512 C 1016 180 844 8 512 8 Z"/>
</svg>
MASKSVG

    command -v convert &>/dev/null || { rm -rf "${work}"; return 0; }

    # 颜色瓷砖：青葱深绿对角渐变（优先 rsvg 渲染 SVG 渐变，回退 IM 原生渐变）
    if command -v rsvg-convert &>/dev/null; then
        cat > "${work}/tile.svg" << 'TILESVG'
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#1FA89E"/><stop offset="100%" stop-color="#0E5C54"/>
  </linearGradient></defs><rect width="1024" height="1024" fill="url(#t)"/>
</svg>
TILESVG
        rsvg-convert -w 1024 -h 1024 "${work}/tile.svg" -o "${work}/tile.png" 2>/dev/null
        rsvg-convert -w 1024 -h 1024 "${work}/mask.svg" -o "${work}/mask.png" 2>/dev/null
    fi
    [[ -s "${work}/tile.png" ]] || convert -size 1024x1024 gradient:'#1FA89E'-'#0E5C54' "${work}/tile.png" 2>/dev/null
    [[ -s "${work}/mask.png" ]] || convert -background none "${work}/mask.svg" "${work}/mask.png" 2>/dev/null
    [[ -s "${work}/tile.png" && -s "${work}/mask.png" ]] || { rm -rf "${work}"; return 0; }

    # 核心软件: 资产名 -> 目标图标名
    local -A sq_map=(
        [browser]="ming-browser"
        [files]="ming-files"
        [control]="ming-control-center"
        [settings]="ming-settings"
        [security]="ming-security"
        [store]="ming-app-store"
        [terminal]="ming-terminal"
        [update]="ming-update-icon"
    )

    for src_name in "${!sq_map[@]}"; do
        local src="${assets}/${src_name}.png"
        [[ -f "${src}" ]] || continue
        local dest_name="${sq_map[$src_name]}"
        # 去白底 -> 缩到 ~62% 居中 -> 贴到 squircle 瓷砖 -> 用 squircle 遮罩裁形
        convert "${src}" -fuzz 8% -transparent white -trim +repage \
            -resize 620x620 -background none -gravity center -extent 1024x1024 \
            "${work}/fg.png" 2>/dev/null || continue
        convert "${work}/tile.png" "${work}/fg.png" -gravity center -compose over -composite \
            "${work}/mask.png" -alpha set -compose DstIn -composite \
            "${work}/sq.png" 2>/dev/null || continue
        for size in 48 64 128 256; do
            mkdir -p "${icon_base}/${size}x${size}/apps"
            convert "${work}/sq.png" -resize "${size}x${size}" \
                "${icon_base}/${size}x${size}/apps/${dest_name}.png" 2>/dev/null || true
        done
    done
    gtk-update-icon-cache "${icon_base}" 2>/dev/null || true
    rm -rf "${work}"
    echo "Squircle 核心图标已生成。"
}


# ======================== Ming Mint 统一视觉资源 ========================

install_ming_mint_icon_set() {
    local asset_dir="/tmp/ming-build/assets/icons/ming-mint"
    local icon_base="/usr/share/icons/Ming-Mint"
    [[ -d "${asset_dir}" ]] || {
        echo "ERROR: missing Ming Mint icon source: ${asset_dir}" >&2
        return 1
    }

    install -d -m 0755 \
        "${icon_base}/24x24/apps" "${icon_base}/32x32/apps" \
        "${icon_base}/48x48/apps" "${icon_base}/scalable/apps"
    install -m 0644 "${asset_dir}/index.theme" "${icon_base}/index.theme"
    local name
    for name in settings files terminal app-library update control store papyrus xiahai; do
        [[ -s "${asset_dir}/${name}.svg" ]] || {
            echo "ERROR: missing Ming Mint icon: ${name}" >&2
            return 1
        }
        # The source SVG is intentionally transparent and scales cleanly.  A
        # single source is copied into fixed-size theme slots so GTK3, GTK4,
        # Plank and desktop entries resolve the same visual asset.
        install -m 0644 "${asset_dir}/${name}.svg" \
            "${icon_base}/scalable/apps/ming-${name}.svg"
        for size in 24 32 48; do
            install -m 0644 "${asset_dir}/${name}.svg" \
                "${icon_base}/${size}x${size}/apps/ming-${name}.svg"
        done
    done
    gtk-update-icon-cache -f -t "${icon_base}" 2>/dev/null || true
}

configure_ming_mint_theme() {
    local theme_root="/usr/share/themes/Ming-Mint"
    local user_home="/home/${MING_USER}"
    install -d -m 0755 "${theme_root}/gtk-3.0" "${theme_root}/gtk-4.0" \
        "${theme_root}/xfwm4" "${theme_root}/xfce-notify-4.0" \
        "${user_home}/.config/gtk-3.0" "${user_home}/.config/gtk-4.0"

    cat > "${theme_root}/gtk-3.0/gtk.css" << 'MINGMINTGTK3'
/* Ming Mint: opaque, flat surfaces that remain readable without a compositor. */
@define-color ming_surface #F7FBF9;
@define-color ming_surface_strong #FFFFFF;
@define-color ming_text #21423D;
@define-color ming_muted #58756D;
@define-color ming_accent #2F7775;
@define-color ming_line #C8DED6;

* {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 14px;
  line-height: 1.45;
}
window, dialog, popover, menu, tooltip {
  background-color: #F7FBF9;
  color: #21423D;
  border-radius: 10px;
}
headerbar, .titlebar {
  min-height: 32px;
  padding: 0 10px;
  background-color: #EDF5F1;
  color: #21423D;
  border-bottom: 1px solid #C8DED6;
  border-radius: 10px 10px 0 0;
}
button, entry, combobox, spinbutton, scale trough {
  background-color: #FFFFFF;
  color: #21423D;
  border: 1px solid #C8DED6;
  border-radius: 8px;
}
button:hover, button:checked {
  background-color: #DCEFE8;
  border-color: #2F7775;
}
tooltip, .tooltip {
  background-color: #21423D;
  color: #FFFFFF;
}
MINGMINTGTK3

    cat > "${theme_root}/gtk-4.0/gtk.css" << 'MINGMINTGTK4'
/* Ming Mint GTK4 compatibility layer. */
@define-color ming_surface #F7FBF9;
@define-color ming_text #21423D;
* { font-family: "Noto Sans CJK SC", sans-serif; font-size: 14px; line-height: 1.45; }
window, dialog, popover, menu, tooltip { background-color: #F7FBF9; color: #21423D; border-radius: 10px; }
headerbar { min-height: 32px; background-color: #EDF5F1; color: #21423D; border-bottom: 1px solid #C8DED6; }
button, entry { background-color: #FFFFFF; color: #21423D; border: 1px solid #C8DED6; border-radius: 8px; }
button:hover, button:checked { background-color: #DCEFE8; border-color: #2F7775; }
MINGMINTGTK4

    cat > "${theme_root}/xfce-notify-4.0/gtk.css" << 'MINGMINTNOTIFY'
#XfceNotifyWindow, #XfceNotifyWindow * {
  background-color: #21423D;
  color: #FFFFFF;
  border-radius: 10px;
}
MINGMINTNOTIFY

    cat > "${theme_root}/xfwm4/themerc" << 'MINGMINTXFW'
active_text_color=#21423D
inactive_text_color=#58756D
title_font=Noto Sans CJK SC Medium 14
button_spacing=4
title_alignment=left
MINGMINTXFW

    cat > "${theme_root}/index.theme" << 'MINGMINTINDEX'
[X-GNOME-Metatheme]
Name=Ming Mint
GtkTheme=Ming-Mint
MetacityTheme=Ming-Mint
IconTheme=Ming-Mint
CursorTheme=Adwaita
MINGMINTINDEX

    cat > "${user_home}/.config/gtk-3.0/settings.ini" << 'MINGMINTGTKSETTINGS'
[Settings]
gtk-theme-name=Ming-Mint
gtk-icon-theme-name=Ming-Mint
gtk-font-name=Noto Sans CJK SC 14
gtk-enable-animations=true
MINGMINTGTKSETTINGS
    cat > "${user_home}/.config/gtk-4.0/settings.ini" << 'MINGMINTGTK4SETTINGS'
[Settings]
gtk-theme-name=Ming-Mint
gtk-icon-theme-name=Ming-Mint
gtk-font-name=Noto Sans CJK SC 14
MINGMINTGTK4SETTINGS
    cat > "${user_home}/.config/ming-os/ming-mint-theme" << 'MINGMINTMARKER'
theme=Ming-Mint
titlebar_height=32
window_radius=10
font=Noto Sans CJK SC 14
line_height=1.45
control_spacing=8
MINGMINTMARKER

    # Keep old theme names as compatibility aliases, but make Ming Mint the
    # active Xfce and GTK selection for new and upgraded users.
    xfconf-query -c xsettings -p /Net/ThemeName -s "Ming-Mint" 2>/dev/null || true
    xfconf-query -c xsettings -p /Net/IconThemeName -s "Ming-Mint" 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/theme -s "Ming-Mint" 2>/dev/null || true
    chown -R "${MING_USER}:${MING_USER}" "${user_home}/.config/gtk-3.0" \
        "${user_home}/.config/gtk-4.0" "${user_home}/.config/ming-os" 2>/dev/null || true
}

configure_ming_mint_dock_profile() {
    local settings="/home/${MING_USER}/.config/plank/dock1/settings"
    local theme_dir="/usr/share/plank/themes/Ming-Mint"
    install -d -m 0755 "${theme_dir}"
    if [[ -f "${settings}" ]]; then
        sed -i 's/^IconSize=.*/IconSize=32/' "${settings}"
        sed -i 's/^ZoomEnabled=.*/ZoomEnabled=true/' "${settings}"
        sed -i 's/^ZoomPercent=.*/ZoomPercent=125/' "${settings}"
        sed -i 's/^Offset=.*/Offset=12/' "${settings}"
        sed -i 's/^Theme=.*/Theme=Ming-Mint/' "${settings}"
    fi
    cat > "${theme_dir}/dock.theme" << 'MINGMINTPLANK'
[PlankTheme]
TopRoundness=10
BottomRoundness=10
HorizPadding=8
TopPadding=5
BottomPadding=5
ItemPadding=3
IndicatorSize=3
OuterStrokeColor=47;;119;;117;;72
FillStartColor=255;;255;;255;;244
FillEndColor=247;;251;;249;;244
InnerStrokeColor=255;;255;;255;;180

[PlankDockTheme]
LaunchBounceTime=100
LaunchBounceHeight=0.10
UrgentBounceTime=120
ItemMoveTime=90
HoverTime=80
MINGMINTPLANK
    cat > "/usr/local/sbin/ming-mint-dock-profile" << 'MINGMINTDOCK'
#!/usr/bin/env bash
set -u
settings="${HOME}/.config/plank/dock1/settings"
[[ -f "${settings}" ]] || exit 0
sed -i -e 's/^IconSize=.*/IconSize=32/' \
       -e 's/^ZoomEnabled=.*/ZoomEnabled=true/' \
       -e 's/^ZoomPercent=.*/ZoomPercent=125/' \
       -e 's/^Offset=.*/Offset=12/' \
       -e 's/^Theme=.*/Theme=Ming-Mint/' "${settings}"
MINGMINTDOCK
    chmod 0755 /usr/local/sbin/ming-mint-dock-profile
    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/plank" 2>/dev/null || true
}

configure_ming_mint_desktop_icons() {
    local app_dir="/usr/share/applications"
    declare -A icons=(
        [ming-control-center.desktop]=ming-settings
        [ming-settings.desktop]=ming-settings
        [ming-files.desktop]=ming-files
        [ming-terminal.desktop]=ming-terminal
        [ming-app-library.desktop]=ming-app-library
        [ming-update.desktop]=ming-update
        [spark-store.desktop]=ming-store
        [xiahai-xiaoming.desktop]=ming-xiahai
    )
    local desktop_file icon
    for desktop_file in "${!icons[@]}"; do
        [[ -f "${app_dir}/${desktop_file}" ]] || continue
        icon="${icons[$desktop_file]}"
        if grep -q '^Icon=' "${app_dir}/${desktop_file}"; then
            sed -i "s/^Icon=.*/Icon=${icon}/" "${app_dir}/${desktop_file}"
        else
            sed -i "/^\[Desktop Entry\]/a Icon=${icon}" "${app_dir}/${desktop_file}"
        fi
    done
    update-desktop-database "${app_dir}" >/dev/null 2>&1 || true
}

# ======================== 主题与图标 ========================

install_themes() {
    apt install -y --no-install-recommends \
        arc-theme \
        numix-gtk-theme \
        papirus-icon-theme \
        numix-icon-theme-circle

    # 生成 Ming 品牌化 GTK3 CSS 覆盖（26.3.0 深绿强调色）
    mkdir -p /usr/share/themes/Arc-Darker/gtk-3.0
    cat > /usr/share/themes/Arc-Darker/gtk-3.0/gtk-ming.css << 'MINGGTKCSS'
@define-color theme_selected_bg_color #31C476;
@define-color theme_selected_fg_color #ffffff;
@define-color theme_selected_bg_color_rgba rgba(49,196,118,0.85);

headerbar entry selection,
headerbar .selection,
entry selection,
label selection,
.view:selected,
.tile:selected {
    background-color: #31C476;
    color: #ffffff;
}

button.suggested-action {
    background-image: linear-gradient(to bottom, #31C476, #147D74);
    border-color: #00453E;
    color: #ffffff;
}
button.suggested-action:hover {
    background-image: linear-gradient(to bottom, #3DD486, #1A9088);
}

/* macOS 风格圆角窗口边框 */
window decoration {
    border-radius: 10px 10px 0 0;
}
headerbar {
    border-radius: 10px 10px 0 0;
}
MINGGTKCSS

    mkdir -p "/home/${MING_USER}/.config/gtk-3.0"
    cat > "/home/${MING_USER}/.config/gtk-3.0/settings.ini" << 'GTKSETTINGS'
[Settings]
gtk-theme-name=Ming-Glass
gtk-icon-theme-name=Papirus
gtk-font-name=Noto Sans CJK SC 11
gtk-cursor-theme-name=Adwaita
gtk-cursor-theme-size=24
gtk-toolbar-style=GTK_TOOLBAR_ICONS
gtk-toolbar-icon-size=GTK_ICON_SIZE_SMALL_TOOLBAR
gtk-button-images=0
gtk-menu-images=0
gtk-enable-event-sounds=0
gtk-enable-input-feedback-sounds=0
gtk-enable-animations=0
gtk-application-prefer-dark-theme=0
gtk-decoration-layout=close,minimize,maximize:
GTKSETTINGS

    cat > "/home/${MING_USER}/.gtkrc-2.0" << 'GTK2SETTINGS'
gtk-theme-name="Ming-Glass"
gtk-icon-theme-name="Papirus"
gtk-font-name="Noto Sans CJK SC 11"
gtk-cursor-theme-name="Adwaita"
gtk-cursor-theme-size=24
gtk-toolbar-style=GTK_TOOLBAR_ICONS
gtk-button-images=0
gtk-menu-images=0
gtk-enable-event-sounds=0
gtk-enable-input-feedback-sounds=0
gtk-enable-animations=0
GTK2SETTINGS
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/gtk-3.0/settings.ini" "/home/${MING_USER}/.gtkrc-2.0"

    # Ming-Glass GTK3 轻量纸感主题：参考 Lingmo/deepin/macOS 的统一浅色层级，
    # 但避免高成本模糊和重发光，优先照顾老电脑。
    mkdir -p /usr/share/themes/Ming-Glass/gtk-3.0 \
        /usr/share/themes/Ming-Glass/xfce-notify-4.0
    cat > /usr/share/themes/Ming-Glass/gtk-3.0/gtk.css << 'MINGGLASSCSS'
@define-color theme_bg_color #F7F9F6;
@define-color theme_fg_color #1D2421;
@define-color theme_selected_bg_color #2FAE8F;
@define-color theme_selected_fg_color #ffffff;
@define-color borders rgba(31, 98, 84, 0.12);
@define-color theme_base_color #FFFFFF;
@define-color theme_text_color #1D2421;
@define-color insensitive_bg_color #EEF3F0;
@define-color insensitive_fg_color #9AA8A2;
@define-color unfocused_bg_color #F2F5F2;
@define-color unfocused_fg_color #5C6963;

* {
  -GtkWidget-cursor-aspect-ratio: 0.05;
}

/* Text stays familiar on old displays: regular body copy, a calm title weight,
 * and fixed 4/8/12/16px rhythm.  This is static GTK CSS, with no runtime work. */
label,
button,
entry,
menuitem,
notebook tab {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-weight: 400;
}

label.title,
headerbar label {
  font-weight: 600;
}

window, dialog {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
  border-radius: 10px;
}

window decoration {
  border-radius: 12px;
  margin: 0;
}

button {
  border-radius: 10px;
  padding: 6px 12px;
  border: 1px solid @borders;
  background-image: none;
  background-color: #FFFFFF;
  color: @theme_fg_color;
  min-height: 32px;
}

button:hover {
  background-color: #FFFFFF;
  border-color: rgba(47, 138, 125, 0.24);
}

button:active {
  background-color: #EAF3EF;
}

button:disabled {
  background-color: @insensitive_bg_color;
  color: @insensitive_fg_color;
}

button.suggested-action {
  background-image: none;
  background-color: #2F8A7D;
  border-color: rgba(24, 103, 89, 0.24);
  color: #FFFFFF;
}

button.suggested-action:hover {
  background-image: none;
  background-color: #28786E;
}

button.destructive-action {
  color: #A64653;
  border-color: rgba(166, 70, 83, 0.20);
  background-color: #FFF9F9;
}

entry {
  border-radius: 10px;
  padding: 6px 12px;
  border: 1px solid @borders;
  background-color: #FFFFFF;
  color: @theme_fg_color;
  min-height: 32px;
}

entry:focus {
  border-color: #2F8A7D;
  background-color: #FFFFFF;
}

notebook header {
  background-color: #F5F8F4;
  border: none;
}

notebook tab {
  border-radius: 10px 10px 0 0;
  padding: 6px 12px;
  background-color: #EEF3F0;
  color: @unfocused_fg_color;
  border: 1px solid transparent;
  border-bottom: none;
  min-height: 30px;
}

notebook tab:checked {
  background-color: #FFFFFF;
  color: @theme_fg_color;
  border-color: @borders;
}

scrollbar slider {
  border-radius: 6px;
  background-color: rgba(71, 111, 98, 0.28);
  min-width: 8px;
  min-height: 24px;
}

scrollbar slider:hover {
  background-color: rgba(47, 138, 125, 0.34);
}

tooltip {
  border-radius: 10px;
  background-color: #1C2723;
  color: #FFFFFF;
  border: 1px solid rgba(255, 255, 255, 0.12);
  padding: 8px 12px;
}

menu, .menu {
  background-color: #FFFFFF;
  border: 1px solid @borders;
  border-radius: 12px;
  padding: 4px;
}

menuitem {
  border-radius: 8px;
  padding: 8px 12px;
  min-height: 24px;
  color: @theme_fg_color;
}

menuitem:hover {
  background-color: rgba(47, 138, 125, 0.08);
}

headerbar {
  background-color: #FFFFFF;
  border: none;
  border-bottom: 1px solid rgba(47, 138, 125, 0.08);
  border-radius: 12px 12px 0 0;
  padding: 6px 12px;
  min-height: 40px;
}

toolbar {
  background-color: #FFFFFF;
  border: none;
}

.separator {
  color: rgba(47, 138, 125, 0.10);
}

switch {
  border-radius: 17px;
  background-color: #DEE7E4;
  border: 1px solid @borders;
}

switch:checked {
  background-color: #2F8A7D;
  border-color: #2F8A7D;
}

scale slider {
  border-radius: 50%;
  background-color: #2F8A7D;
  border: 2px solid #2F8A7D;
  min-width: 16px;
  min-height: 16px;
}

scale trough {
  border-radius: 4px;
  background-color: rgba(47, 138, 125, 0.08);
  min-height: 6px;
}

progressbar trough {
  border-radius: 6px;
  background-color: rgba(47, 138, 125, 0.07);
  min-height: 8px;
}

progressbar progress {
  border-radius: 6px;
  background-color: #2F8A7D;
}

checkbutton check, radiobutton radio {
  border-radius: 5px;
  background-color: #FFFFFF;
  border: 1px solid @borders;
  min-width: 18px;
  min-height: 18px;
}

checkbutton check:checked, radiobutton radio:checked {
  background-color: #2F8A7D;
  border-color: #2F8A7D;
}

.view, iconview {
  background-color: #FFFFFF;
  color: @theme_fg_color;
  border-radius: 10px;
}

.view:selected, iconview:selected {
  background-color: rgba(47, 138, 125, 0.12);
  color: @theme_selected_fg_color;
}

treeview header button {
  background-color: #F5F8F4;
  color: @theme_fg_color;
  border: none;
  border-bottom: 1px solid @borders;
  padding: 4px 8px;
  min-height: 24px;
}

placessidebar,
.sidebar,
paned > box,
stacksidebar {
  background-color: #EEF3F0;
  border-right: 1px solid rgba(47, 138, 125, 0.10);
}

placessidebar row,
.sidebar row,
stacksidebar row {
  border-radius: 10px;
  margin: 4px 8px;
  padding: 8px;
}

placessidebar row:selected,
.sidebar row:selected,
stacksidebar row:selected {
  background-color: rgba(47, 138, 125, 0.12);
  color: #1D2421;
}

.titlebar,
decoration {
  border-radius: 12px 12px 0 0;
}

.whiskermenu-window,
#whiskermenu-window {
  background-color: #FFFFFF;
  border: 1px solid rgba(31, 98, 84, 0.14);
  border-radius: 14px;
}

spinbutton entry {
  border-radius: 8px 0 0 8px;
}

spinbutton button {
  border-radius: 0;
  padding: 4px 8px;
}

.xfce4-panel {
  background-color: #FFFFFF;
  border: 1px solid rgba(31, 98, 84, 0.10);
  border-radius: 14px;
  margin: 6px 8px 4px 8px;
  padding: 2px 6px;
}

.xfce4-panel button {
  border-radius: 11px;
  padding: 4px 7px;
  margin: 2px 4px;
  border: 1px solid transparent;
  background-color: transparent;
  min-width: 36px;
  min-height: 36px;
}

.xfce4-panel button:hover {
  background-color: rgba(47, 138, 125, 0.11);
  border-color: rgba(47, 138, 125, 0.24);
}

.xfce4-panel button:checked {
  background-color: rgba(47, 138, 125, 0.17);
  border-color: rgba(47, 138, 125, 0.32);
}
MINGGLASSCSS

    cat > /usr/share/themes/Ming-Glass/xfce-notify-4.0/gtk.css << 'MINGGLASSNOTIFY'
window#XfceNotifyWindow {
  background-color: #FFFFFF;
  color: #1D2421;
  border: 1px solid rgba(31, 98, 84, 0.20);
  border-radius: 10px;
}

window#XfceNotifyWindow label {
  color: #1D2421;
}
MINGGLASSNOTIFY

    if [[ -d /usr/share/themes/Arc-Darker/xfwm4 ]]; then
        rm -rf /usr/share/themes/Ming-Glass/xfwm4
        cp -a /usr/share/themes/Arc-Darker/xfwm4 /usr/share/themes/Ming-Glass/xfwm4
        cat >> /usr/share/themes/Ming-Glass/xfwm4/themerc << 'XFWMMING'

# Ming OS tuned window frame
active_text_color=#1D2421
inactive_text_color=#66736D
button_offset=4
button_spacing=2
full_width_title=true
title_alignment=center
XFWMMING
    fi

    # 创建 index.theme 文件
    cat > /usr/share/themes/Ming-Glass/index.theme << 'THEMEINDEX'
[Desktop Entry]
Type=X-GNOME-Metatheme
Name=Ming Glass
Comment=Ming OS 26.4.1 Light Paper Theme
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=Ming-Glass
MetacityTheme=Ming-Glass
IconTheme=Papirus
CursorTheme=Adwaita
THEMEINDEX

    # Ming-Dark stays static for old GPUs: the same type scale and controls as
    # Ming-Glass, with solid surfaces that remain readable without a compositor.
    mkdir -p /usr/share/themes/Ming-Dark/gtk-3.0 \
        /usr/share/themes/Ming-Dark/xfce-notify-4.0
    cat > /usr/share/themes/Ming-Dark/gtk-3.0/gtk.css << 'MINGDARKCSS'
@define-color theme_bg_color #151A18;
@define-color theme_fg_color #E7EEE9;
@define-color theme_selected_bg_color #2F8A7D;
@define-color theme_selected_fg_color #FFFFFF;
@define-color borders rgba(159, 231, 215, 0.16);
@define-color theme_base_color #202824;
@define-color theme_text_color #E7EEE9;
@define-color insensitive_bg_color #272F2B;
@define-color insensitive_fg_color #93A19A;

* {
  -GtkWidget-cursor-aspect-ratio: 0.05;
}

label, button, entry, menuitem, notebook tab {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-weight: 400;
}

label.title, headerbar label {
  font-weight: 600;
}

window, .view, iconview {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
}

headerbar, toolbar, notebook header, menu, .menu {
  background-color: #1B211E;
  color: @theme_fg_color;
  border-color: @borders;
}

button, entry, checkbutton check, radiobutton radio {
  background-image: none;
  background-color: #202824;
  color: @theme_fg_color;
  border: 1px solid @borders;
  border-radius: 10px;
}

button:hover, entry:focus {
  background-color: #29342F;
  border-color: rgba(159, 231, 215, 0.34);
}

button.suggested-action, switch:checked, progressbar progress {
  background-image: none;
  background-color: #2F8A7D;
  color: #FFFFFF;
  border-color: #2F8A7D;
}

button:disabled {
  background-color: @insensitive_bg_color;
  color: @insensitive_fg_color;
}

tooltip {
  background-color: #101513;
  color: #FFFFFF;
  border: 1px solid @borders;
}

scrollbar slider, scale slider {
  background-color: #62C9B5;
}

scale trough, progressbar trough {
  background-color: #29342F;
}
MINGDARKCSS
    cat > /usr/share/themes/Ming-Dark/xfce-notify-4.0/gtk.css << 'MINGDARKNOTIFY'
window#XfceNotifyWindow {
  background-color: #202824;
  color: #E7EEE9;
  border: 1px solid rgba(159, 231, 215, 0.28);
  border-radius: 10px;
}

window#XfceNotifyWindow label {
  color: #E7EEE9;
}
MINGDARKNOTIFY
    if [[ -d /usr/share/themes/Arc-Darker/xfwm4 ]]; then
        rm -rf /usr/share/themes/Ming-Dark/xfwm4
        cp -a /usr/share/themes/Arc-Darker/xfwm4 /usr/share/themes/Ming-Dark/xfwm4
        cat >> /usr/share/themes/Ming-Dark/xfwm4/themerc << 'XFWMDARK'

active_text_color=#E7EEE9
inactive_text_color=#93A19A
button_offset=4
button_spacing=2
full_width_title=true
title_alignment=center
XFWMDARK
    fi
    cat > /usr/share/themes/Ming-Dark/index.theme << 'DARKTHEMEINDEX'
[Desktop Entry]
Type=X-GNOME-Metatheme
Name=Ming Dark
Comment=Ming OS static dark theme
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=Ming-Dark
MetacityTheme=Ming-Dark
IconTheme=Papirus
CursorTheme=Adwaita
DARKTHEMEINDEX
}

# ======================== 壁纸生成 ========================

setup_wallpaper() {
    mkdir -p /usr/share/backgrounds/ming-os
    command -v convert >/dev/null 2>&1 || {
        echo "[03_desktop][ERROR] ImageMagick convert is required to generate wallpaper caches" >&2
        return 1
    }

    local asset_2640="/tmp/ming-build/assets/wallpaper-ming-2640-abstract.png"
    local asset_dark="/tmp/ming-build/assets/wallpaper-ming-dark.png"
    local asset_light="/tmp/ming-build/assets/wallpaper-ming-light.png"
    local asset_macos="/tmp/ming-build/assets/wallpaper-ming-macos.png"
    local asset_png="/tmp/ming-build/assets/wallpaper-default.png"

    # 浅色壁纸
    [[ -f "${asset_light}" ]] && cp "${asset_light}" /usr/share/backgrounds/ming-os/default-light.png
    # macOS 风格壁纸（绿山）
    [[ -f "${asset_macos}" ]] && cp "${asset_macos}" /usr/share/backgrounds/ming-os/default-macos.png

    # 26.4.0 final wallpaper is the canonical default; older assets are fallback.
    local primary=""
    if [[ -s "${asset_2640}" ]]; then
        primary="${asset_2640}"
        cp "${asset_2640}" /usr/share/backgrounds/ming-os/default-2640.png
        cp "${asset_2640}" /usr/share/backgrounds/ming-os/default.png
    elif [[ -f "${asset_light}" ]]; then
        primary="${asset_light}"
        cp "${asset_light}" /usr/share/backgrounds/ming-os/default.png
        [[ -f "${asset_dark}" ]] && cp "${asset_dark}" /usr/share/backgrounds/ming-os/default-dark.png
        [[ -f "${asset_macos}" ]] && cp "${asset_macos}" /usr/share/backgrounds/ming-os/default-macos.png
    elif [[ -f "${asset_macos}" ]]; then
        primary="${asset_macos}"
        cp "${asset_macos}" /usr/share/backgrounds/ming-os/default.png
        [[ -f "${asset_dark}" ]] && cp "${asset_dark}" /usr/share/backgrounds/ming-os/default-dark.png
    elif [[ -f "${asset_dark}" ]]; then
        primary="${asset_dark}"
        cp "${asset_dark}" /usr/share/backgrounds/ming-os/default-dark.png
        cp "${asset_dark}" /usr/share/backgrounds/ming-os/default.png
    elif [[ -f "${asset_png}" ]]; then
        primary="${asset_png}"
    fi

    if [[ -n "${primary}" ]]; then
        cp "${primary}" /usr/share/backgrounds/ming-os/default.png
        local geometry output
        for geometry in 3840x2160 1920x1080 1366x768; do
            output="/usr/share/backgrounds/ming-os/default-${geometry}.png"
            if ! convert /usr/share/backgrounds/ming-os/default.png \
                -resize "${geometry}^" \
                -gravity center \
                -extent "${geometry}" \
                "${output}" 2>/dev/null; then
                echo "[03_desktop][ERROR] failed to generate wallpaper cache ${geometry}" >&2
                return 1
            fi
        done
    fi

    cat > /usr/share/backgrounds/ming-os/default.svg << 'WALLPAPERSVG'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#F7FAF7"/>
      <stop offset="48%" stop-color="#EAF4EF"/>
      <stop offset="100%" stop-color="#D8EDE6"/>
    </linearGradient>
    <radialGradient id="glow" cx="26%" cy="36%" r="62%">
      <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.95"/>
      <stop offset="56%" stop-color="#CFECE2" stop-opacity="0.34"/>
      <stop offset="100%" stop-color="#CFECE2" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="ridge" x1="0%" y1="35%" x2="100%" y2="92%">
      <stop offset="0%" stop-color="#2FAE8F" stop-opacity="0.20"/>
      <stop offset="48%" stop-color="#6CBBA9" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="#1E7F70" stop-opacity="0.22"/>
    </linearGradient>
  </defs>
  <rect width="1920" height="1080" fill="url(#bg)"/>
  <rect width="1920" height="1080" fill="url(#glow)"/>
  <path d="M0 730 C260 628 404 768 642 662 C875 558 1052 606 1268 520 C1512 424 1680 492 1920 400 L1920 1080 L0 1080Z" fill="url(#ridge)"/>
  <path d="M0 846 C338 720 548 856 842 744 C1088 650 1304 726 1548 590 C1708 500 1810 520 1920 466 L1920 1080 L0 1080Z" fill="#FFFFFF" opacity="0.34"/>
  <circle cx="450" cy="320" r="210" fill="none" stroke="#2FAE8F" stroke-width="2" opacity="0.10"/>
  <circle cx="450" cy="320" r="138" fill="none" stroke="#1E7F70" stroke-width="2" opacity="0.08"/>
  <text x="124" y="172" font-family="sans-serif" font-size="54" font-weight="700" fill="#1D2421">Ming OS</text>
  <text x="128" y="218" font-family="sans-serif" font-size="22" fill="#4F625A">小而美的桌面系统</text>
</svg>
WALLPAPERSVG

    cat > /usr/share/backgrounds/ming-os/default-1366x768.svg << 'WALLPAPERSVG1366'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1366" height="768" viewBox="0 0 1366 768">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#F7FAF7"/>
      <stop offset="52%" stop-color="#EAF4EF"/>
      <stop offset="100%" stop-color="#D8EDE6"/>
    </linearGradient>
  </defs>
  <rect width="1366" height="768" fill="url(#bg)"/>
  <path d="M0 520 C190 448 286 548 456 472 C626 394 750 432 906 370 C1080 302 1196 350 1366 286 L1366 768 L0 768Z" fill="#2FAE8F" opacity="0.17"/>
  <path d="M0 612 C238 512 392 618 596 536 C784 462 936 522 1112 424 C1226 362 1286 378 1366 336 L1366 768 L0 768Z" fill="#FFFFFF" opacity="0.34"/>
  <circle cx="320" cy="228" r="148" fill="none" stroke="#2FAE8F" stroke-width="2" opacity="0.10"/>
  <text x="86" y="126" font-family="sans-serif" font-size="42" font-weight="700" fill="#1D2421">Ming OS</text>
  <text x="88" y="164" font-family="sans-serif" font-size="18" fill="#4F625A">小而美的桌面系统</text>
</svg>
WALLPAPERSVG1366

    if [[ ! -f /usr/share/backgrounds/ming-os/default.png ]]; then
        if command -v rsvg-convert &>/dev/null; then
            rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/ming-os/default.svg \
                > /usr/share/backgrounds/ming-os/default.png 2>/dev/null || true
        elif command -v convert &>/dev/null; then
            convert -resize 1920x1080 /usr/share/backgrounds/ming-os/default.svg \
                /usr/share/backgrounds/ming-os/default.png 2>/dev/null || true
        fi
    fi

    if [[ ! -f /usr/share/backgrounds/ming-os/default-1366x768.png ]]; then
        if command -v rsvg-convert &>/dev/null; then
            rsvg-convert -w 1366 -h 768 /usr/share/backgrounds/ming-os/default-1366x768.svg \
                > /usr/share/backgrounds/ming-os/default-1366x768.png 2>/dev/null || true
        elif command -v convert &>/dev/null; then
            convert -resize 1366x768 /usr/share/backgrounds/ming-os/default-1366x768.svg \
                /usr/share/backgrounds/ming-os/default-1366x768.png 2>/dev/null || true
        fi
    fi

    [[ -f /usr/share/backgrounds/ming-os/default.png ]] || cp /usr/share/backgrounds/ming-os/default.svg /usr/share/backgrounds/ming-os/default.png
    [[ -f /usr/share/backgrounds/ming-os/default-1366x768.png ]] || cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-1366x768.png

    mkdir -p /usr/share/plymouth/themes/ming-os
    cp /usr/share/backgrounds/ming-os/default.png /usr/share/plymouth/themes/ming-os/wallpaper.png 2>/dev/null || true
    plymouth-set-default-theme ming-os 2>/dev/null || true
}

# ======================== Xfce 顶部菜单栏 (macOS 风格) ========================
# 设计：顶部一条细面板充当 macOS 菜单栏（左 Ming 菜单 + 右托盘/时钟），
#       底部由 Plank 提供可放大的真·Dock（见 configure_plank_dock）。
#       Xfce 面板本身无法做 dock 悬停放大动画，因此 Dock 交给 Plank。

configure_xfce_panel() {
    local xfconf_dir="/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml"
    mkdir -p "${xfconf_dir}"

    local old_panel_dir="/home/${MING_USER}/.config/xfce4/panel"
    rm -rf "${old_panel_dir}"
    mkdir -p "${old_panel_dir}"

    cat > "${xfconf_dir}/xfce4-panel.xml" << 'PANELXML'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="0"/>
    <property name="panel-0" type="empty">
      <property name="position" type="string" value="p=6;x=0;y=0"/>
      <property name="position-locked" type="bool" value="true"/>
      <property name="autohide-behavior" type="uint" value="0"/>
      <property name="length" type="uint" value="100"/>
      <property name="length-adjust" type="bool" value="true"/>
      <property name="size" type="uint" value="30"/>
      <property name="icon-size" type="uint" value="18"/>
      <property name="nrows" type="uint" value="1"/>
      <property name="mode" type="uint" value="0"/>
      <property name="background-style" type="uint" value="2"/>
      <property name="background-rgba" type="array">
        <value type="double" value="0.101961"/>
        <value type="double" value="0.039216"/>
        <value type="double" value="0.180392"/>
        <value type="double" value="1.000000"/>
      </property>
      <property name="enter-opacity" type="uint" value="100"/>
      <property name="leave-opacity" type="uint" value="100"/>
      <property name="disable-struts" type="bool" value="false"/>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>
        <value type="int" value="2"/>
        <value type="int" value="3"/>
        <value type="int" value="4"/>
        <value type="int" value="6"/>
      </property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <!-- plugin-1: Whisker Menu (Ming 品牌开始菜单) -->
    <property name="plugin-1" type="string" value="whiskermenu">
      <property name="button-icon" type="string" value="ming-os-menu"/>
      <property name="button-title" type="string" value="Ming OS"/>
      <property name="show-button-title" type="bool" value="true"/>
      <property name="menu-width" type="uint" value="440"/>
      <property name="menu-height" type="uint" value="520"/>
      <property name="menu-opacity" type="uint" value="100"/>
      <property name="position-categories-alternate" type="bool" value="false"/>
      <property name="view-mode" type="uint" value="1"/>
      <property name="show-generic-names" type="bool" value="true"/>
      <property name="show-tooltips" type="bool" value="true"/>
      <property name="launcher-show-description" type="bool" value="true"/>
    </property>

    <!-- plugin-2: 弹性分隔符（把右侧内容推到最右） -->
    <property name="plugin-2" type="string" value="separator">
      <property name="style" type="uint" value="0"/>
      <property name="expand" type="bool" value="true"/>
    </property>

    <!-- plugin-3: 电源管理插件 (电池/亮度) -->
    <property name="plugin-3" type="string" value="power-manager-plugin"/>

    <!-- plugin-4: 状态托盘 (网络/音量/通知; Xfce 4.18 systray 已内置 SNI 支持) -->
    <property name="plugin-4" type="string" value="systray">
      <property name="square-icons" type="bool" value="true"/>
      <property name="icon-size" type="uint" value="16"/>
      <property name="known-legacy-items" type="array">
        <value type="string" value="networkmanager applet"/>
        <value type="string" value="pulseaudio plugin"/>
      </property>
    </property>

    <!-- plugin-6: 时钟 -->
    <property name="plugin-6" type="string" value="clock">
      <property name="digital-layout" type="uint" value="3"/>
      <property name="digital-time-format" type="string" value="%H:%M"/>
      <property name="digital-date-format" type="string" value="%m月%d日"/>
      <property name="tooltip-format" type="string" value="%Y年%m月%d日 %A"/>
      <property name="mode" type="uint" value="2"/>
    </property>
  </property>
</channel>
PANELXML

    # Dock-only mode: keep xfce4-panel installed for compatibility, but do not
    # show the top taskbar. All user-facing entry points live in Plank Dock.
    cat > "${xfconf_dir}/xfce4-panel.xml" << 'PANELXML_DOCK_ONLY'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array"/>
  <property name="plugins" type="empty"/>
</channel>
PANELXML_DOCK_ONLY

    # Remove xfce4-panel from the factory failsafe session itself. Killing the
    # process after login is too late because the restored panel can flash.
    cat > "${xfconf_dir}/xfce4-session.xml" << 'PHONESESSIONXML'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-session" version="1.0">
  <property name="sessions" type="empty">
    <property name="Failsafe" type="empty">
      <property name="Client0_Command" type="array">
        <value type="string" value="xfwm4"/>
      </property>
      <property name="Client1_Command" type="array">
        <value type="string" value="xfsettingsd"/>
      </property>
      <property name="Client2_Command" type="array">
        <value type="string" value="xfdesktop"/>
      </property>
    </property>
  </property>
</channel>
PHONESESSIONXML

    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/xfce4-panel.desktop" << 'PANELDISABLED'
[Desktop Entry]
Type=Application
Name=Xfce Panel
Exec=xfce4-panel
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
PANELDISABLED
    cat > "${autostart_dir}/ming-dock-only.desktop" << 'DOCKONLY'
[Desktop Entry]
Type=Application
Name=Ming Dock Only
Comment=Hide the legacy Xfce top taskbar and keep Dock as the only launcher
Exec=sh -c "mkdir -p ~/.cache/sessions; rm -f ~/.cache/sessions/xfce4-session-* 2>/dev/null || true; xfconf-query -c xfce4-session -p /sessions/Failsafe/Client0_Command -n -t string -s xfwm4 2>/dev/null || true"
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
DOCKONLY

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/xfce4"
    chown -R "${MING_USER}:${MING_USER}" \
        "${autostart_dir}/ming-dock-only.desktop" \
        "${autostart_dir}/xfce4-panel.desktop"
}

# ======================== Plank macOS 风格 Dock ========================

configure_plank_dock() {
    local plank_dir="/home/${MING_USER}/.config/plank/dock1"
    mkdir -p "${plank_dir}/launchers"

    # Dock 行为与外观：底部居中、轻放大、磨砂白悬浮底座；避免老机动画压力过大。
    cat > "${plank_dir}/settings" << 'PLANKSETTINGS'
[PlankDockPreferences]
# MingDockProfile=2640-legacy-centered
#当前 Dock 上的启动器（顺序即显示顺序）
DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-files.dockitem;;ming-firefox.dockitem;;spark-store.dockitem;;xiahai-xiaoming.dockitem;;ming-terminal.dockitem
#停靠位置: 0=左 1=右 2=上 3=下
Position=3
#对齐: 3=居中
Alignment=3
#居中偏移：0=真正水平居中；底部留白由主题 padding 和工作区预留负责
Offset=0
#图标大小（ming-scale 会按分辨率覆盖）
IconSize=40
#悬停放大开关
ZoomEnabled=true
#放大倍率：只提供轻微反馈，避免图标跳动和低端显卡压力
ZoomPercent=148
#隐藏模式: 0=不隐藏 1=智能隐藏 2=自动隐藏 3=躲避窗口 4=窗口铺满时隐藏
HideMode=0
#自动隐藏延迟
UnhideDelay=0
HideDelay=0
#主题（见下方 Ming.theme）
Theme=Ming
#显示在所有工作区
Monitor=
#锁定图标，防止误拖拽
LockItems=false
#压力解锁
PressureReveal=false
#显示正在运行程序的指示点
ShowDockItem=true
ItemsAlignment=3
#淡入淡出
FadeOpacity=1.0
PLANKSETTINGS

    if [[ "${MING_SKIP_XIAHAI:-0}" == "1" ]]; then
        sed -i 's/;;xiahai-xiaoming\.dockitem//g' "${plank_dir}/settings"
    fi

    # Late modules install some launchers after this module. Keep generation in
    # one idempotent helper and let 07_finalize run it again before seeding skel.
    cat > /usr/local/sbin/ming-refresh-dock-launchers << 'MINGREFRESHDOCK'
#!/usr/bin/env bash
set -uo pipefail

target_user="${1:-${SUDO_USER:-$(id -un)}}"
user_home="$(getent passwd "${target_user}" | awk -F: 'NR == 1 { print $6 }')"
if [[ -z "${user_home}" || ! -d "${user_home}" ]]; then
    echo "ERROR: cannot resolve Dock home for ${target_user}" >&2
    exit 1
fi

plank_dir="${user_home}/.config/plank/dock1"
mkdir -p "${plank_dir}/launchers"
missing=0
skip_xiahai=false
[[ -f /etc/ming-os/skip-xiahai ]] && skip_xiahai=true

_plank_launcher() {
        local name="$1" target="$2"
        local target_path="/usr/share/applications/${target}"
        local proxy_path="/usr/share/applications/ming-dock-${name}.desktop"
        local display_name icon wm_class exec_line
        case "${name}" in
            ming-firefox)
                [[ -f "${target_path}" ]] || target_path=/usr/share/applications/firefox-esr.desktop
                ;;
            spark-store)
                [[ -f "${target_path}" ]] || target_path=/usr/share/applications/ming-install-spark-store.desktop
                ;;
        esac
        [[ -f "${target_path}" ]] || {
            echo "WARN: Dock target missing: ${target}" >&2
            return 1
        }
        display_name="$(awk -F= '/^Name\[zh_CN\]=/{print substr($0,index($0,"=")+1); exit} /^Name=/{fallback=substr($0,index($0,"=")+1)} END{if (!found && fallback) print fallback}' "${target_path}" | head -n1)"
        icon="$(awk -F= '/^Icon=/{print substr($0,index($0,"=")+1); exit}' "${target_path}" | head -n1)"
        wm_class="$(awk -F= '/^StartupWMClass=/{print substr($0,index($0,"=")+1); exit}' "${target_path}" | head -n1)"
        case "${name}" in
            ming-settings) wm_class="${wm_class:-uno.scallion.MingSettings}" ;;
            ming-files) wm_class="${wm_class:-org.mingos.Files}" ;;
            ming-firefox) wm_class="${wm_class:-Firefox-esr}" ;;
            ming-terminal) wm_class="${wm_class:-Xfce4-terminal}" ;;
        esac
        exec_line="/usr/local/bin/ming-launch --desktop-file ${target_path} --source dock"
        cat > "${proxy_path}" << DOCKPROXY
[Desktop Entry]
Type=Application
Name=${display_name:-${name}}
Exec=${exec_line}
Icon=${icon:-application-x-executable}
Terminal=false
NoDisplay=true
StartupNotify=true
StartupWMClass=${wm_class:-${name}}
DOCKPROXY
        cat > "${plank_dir}/launchers/${name}.dockitem" << DOCKITEM
[PlankDockItemPreferences]
Launcher=file://${proxy_path}
DOCKITEM
}

cat > "${plank_dir}/launchers/ming-app-library.dockitem" << 'DRAWERDOCKITEM'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/ming-app-library.desktop
DRAWERDOCKITEM
for launcher in \
    "ming-firefox:ming-firefox.desktop" \
    "ming-files:ming-files.desktop" \
    "spark-store:spark-store.desktop" \
    "ming-settings:ming-settings.desktop" \
    "ming-terminal:ming-terminal.desktop"; do
    _plank_launcher "${launcher%%:*}" "${launcher#*:}" || missing=1
done
if ! ${skip_xiahai}; then
    _plank_launcher "xiahai-xiaoming" "xiahai-xiaoming.desktop" || missing=1
else
    rm -f "${plank_dir}/launchers/xiahai-xiaoming.dockitem" \
          /usr/share/applications/ming-dock-xiahai-xiaoming.desktop
fi

if [[ "$(id -u)" -eq 0 ]]; then
    chown -R "${target_user}:$(id -gn "${target_user}")" "${plank_dir}/launchers" 2>/dev/null || true
fi
exit "${missing}"
MINGREFRESHDOCK
    chmod 0755 /usr/local/sbin/ming-refresh-dock-launchers
    /usr/local/sbin/ming-refresh-dock-launchers "${MING_USER}" || \
        echo "[03_desktop][WARN] Late Dock launchers will be completed by 07_finalize"

    # Ming 26.4.0 / 26.3.2 经典底部 Dock 主题。
    local theme_dir
    for theme_dir in /usr/share/plank/themes/Ming /usr/share/plank/themes/Default; do
    mkdir -p "${theme_dir}"
    cat > "${theme_dir}/dock.theme" << 'PLANKTHEME'
[PlankTheme]
TopRoundness=14
BottomRoundness=0
LineWidth=1
OuterStrokeColor=31;;98;;84;;54
FillStartColor=255;;255;;255;;226
FillEndColor=242;;250;;247;;238
InnerStrokeColor=255;;255;;255;;176

[PlankDockTheme]
HorizPadding=16
TopPadding=6
BottomPadding=2
ItemPadding=4
IndicatorSize=4
IconShadowSize=1
UrgentBounceHeight=1.20
LaunchBounceHeight=0.20
FadeOpacity=1.0
ClickTime=160
UrgentBounceTime=420
LaunchBounceTime=150
ActiveTime=160
SlideTime=160
FadeTime=120
HideTime=120
GlowSize=0
GlowTime=10000
GlowPulseTime=1600
UrgentHueShift=86
ItemMoveTime=130
CascadeHide=false
PLANKTHEME
    done

    cat > /usr/local/bin/ming-dock << 'MINGDOCK'
#!/usr/bin/env python3
import configparser
import subprocess
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

APPS = [
    ('ming-settings.desktop', 'ming-control-center', 'Ming 设置'),
    ('ming-app-library.desktop', 'ming-app-library', '应用库'),
    ('ming-files.desktop', 'files-icon', '文件'),
    ('ming-firefox.desktop', 'firefox-esr', 'Firefox ESR'),
    ('spark-store.desktop', 'spark-store', 'Spark'),
    ('xiahai-xiaoming.desktop', 'xiahai-xiaoming', '小明 AI 助手'),
    ('ming-terminal.desktop', 'ming-terminal', '终端'),
]

CSS = b'''
window#ming-dock-window {
  background: transparent;
}
.dock {
  border-radius: 16px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.78);
  box-shadow: 0 18px 42px rgba(21, 68, 56, 0.18), inset 0 1px 0 rgba(255,255,255,0.82);
}
.dock-button {
  border-radius: 12px;
  padding: 5px;
  background: rgba(255, 255, 255, 0.22);
  border: 1px solid transparent;
}
.dock-button:hover {
  background: rgba(47, 138, 125, 0.14);
  border-color: rgba(47, 138, 125, 0.22);
}
'''

def desktop_path(basename):
    for base in (Path('/usr/share/applications'), Path.home() / '.local/share/applications', Path.home() / 'Desktop'):
        path = base / basename
        if path.exists():
            return path
    return None

def app_name(path, fallback):
    if not path:
        return fallback
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding='utf-8')
        entry = parser['Desktop Entry']
        return entry.get('Name[zh_CN]') or entry.get('Name') or fallback
    except Exception:
        return fallback

class DockButton(Gtk.Button):
    def __init__(self, basename, icon, fallback):
        super().__init__()
        self.basename = basename
        self.path = desktop_path(basename)
        self.icon = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self.icon.set_pixel_size(36)
        self.set_image(self.icon)
        self.set_always_show_image(True)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_tooltip_text(app_name(self.path, fallback))
        self.get_style_context().add_class('dock-button')
        self.connect('clicked', self.launch)
        self.connect('enter-notify-event', self.hover_in)
        self.connect('leave-notify-event', self.hover_out)

    def hover_in(self, *_args):
        self.icon.set_pixel_size(46)
        return False

    def hover_out(self, *_args):
        self.icon.set_pixel_size(36)
        return False

    def launch(self, *_args):
        try:
            info = Gio.DesktopAppInfo.new_from_filename(str(self.path)) if self.path else None
            if info and info.launch([], None):
                return
        except Exception:
            pass
        try:
            subprocess.Popen(['gtk-launch', Path(self.basename).stem])
            return
        except Exception:
            pass
        try:
            Gio.AppInfo.launch_default_for_uri(f'appstream://{self.basename}', None)
        except Exception:
            pass

class MingDock(Gtk.Window):
    def __init__(self):
        super().__init__(title='Ming Dock')
        self.set_name('ming-dock-window')
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.stick()
        self.set_keep_above(True)
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 710)
        except GLib.Error:
            pass

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.get_style_context().add_class('dock')
        for basename, icon, fallback in APPS:
            box.pack_start(DockButton(basename, icon, fallback), False, False, 0)
        self.add(box)
        self.connect('size-allocate', lambda *_args: self.place())
        self.get_screen().connect('size-changed', lambda *_args: self.place())
        GLib.timeout_add_seconds(2, self.place)
        self.show_all()
        self.place()

    def place(self):
        screen = self.get_screen()
        width = self.get_allocated_width() or 560
        height = self.get_allocated_height() or 70
        x = max(12, int((screen.get_width() - width) / 2))
        y = max(12, screen.get_height() - height - 18)
        self.move(x, y)
        return True

if __name__ == '__main__':
    MingDock()
    Gtk.main()
MINGDOCK
    chmod 0755 /usr/local/bin/ming-dock

cat > /usr/local/bin/ming-dock-watchdog << 'MINGDOCKWATCH'
#!/usr/bin/env bash
set -u

# The custom GTK Dock was retired in favor of Plank.  Keep this filename as a
# compatibility shim for upgraded user profiles, but never start a second
# launcher surface during a real session.
if [[ "${MING_USE_LEGACY_MING_DOCK:-0}" != "1" ]]; then
    exit 0
fi

ming_log_dir() {
    local primary="${HOME}/.cache/ming-os"
    if mkdir -p "${primary}" 2>/dev/null && [[ -w "${primary}" ]]; then
        printf '%s\n' "${primary}"
        return 0
    fi
    local fallback="${XDG_RUNTIME_DIR:-/tmp}/ming-os-$(id -u)"
    mkdir -p "${fallback}" 2>/dev/null || fallback="/tmp"
    printf '%s\n' "${fallback}"
}

ming_log() {
    local file="$1"
    shift
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${file}" 2>/dev/null || true
}

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

dock_process_running() {
    pgrep -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-dock([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-dock([[:space:]]|$)' >/dev/null 2>&1
}

dock_window_visible() {
    dock_process_running || return 1
    command -v wmctrl >/dev/null 2>&1 || return 0
    local size sw sh
    size="$(xrandr --current 2>/dev/null | awk '/\*/ {print $1; exit}')"
    sw="${size%x*}"
    sh="${size#*x}"
    [[ "${sw}" =~ ^[0-9]+$ && "${sh}" =~ ^[0-9]+$ ]] || { sw=32768; sh=32768; }
    x11_call wmctrl -lG 2>/dev/null | awk -v sw="${sw}" -v sh="${sh}" '
        /Ming Dock$/ && $3 < sw && $4 < sh && $5 > 0 && $6 > 0 { found=1 }
        END { exit !found }
    '
}

stop_ming_dock() {
    pkill -TERM -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-dock([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-dock([[:space:]]|$)' >/dev/null 2>&1 || true
}

start_plank_fallback() {
    command -v plank >/dev/null 2>&1 || return 1
    stop_ming_dock
    pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 && return 0
    local log_file
    log_file="$(ming_log_dir)/ming-dock.log"
    ming_log "${log_file}" "starting single-instance Plank fallback"
    (nohup plank >>"${log_file}" 2>&1 &) || true
}

start_ming_dock() {
    command -v ming-dock >/dev/null 2>&1 || return 1
    dock_window_visible && return 0
    if dock_process_running; then
        stop_ming_dock
        sleep 1
    fi
    export DISPLAY="${DISPLAY:-:0}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
    local log_file
    log_file="$(ming_log_dir)/ming-dock.log"
    ming_log "${log_file}" "starting ming-dock DISPLAY=${DISPLAY}"
    (nohup ming-dock >>"${log_file}" 2>&1 &) || (nohup ming-dock >/dev/null 2>&1 &)
    for _ready_try in $(seq 1 10); do
        dock_window_visible && return 0
        sleep 0.25
    done
    ming_log "${log_file}" "ming-dock did not publish a visible in-bounds window"
    stop_ming_dock
    return 1
}

case "${1:-start}" in
    --session)
        lock_dir="${XDG_RUNTIME_DIR:-/tmp}/ming-dock-watchdog.lock"
        if ! mkdir "${lock_dir}" 2>/dev/null; then
            exit 0
        fi
        trap 'rmdir "${lock_dir}" 2>/dev/null || true' EXIT
        sleep 3
        failures=0
        while true; do
            if start_ming_dock; then
                failures=0
            else
                failures=$((failures + 1))
                if [[ "${failures}" -ge 3 ]]; then
                    start_plank_fallback || true
                    while true; do
                        pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 || start_plank_fallback || true
                        sleep 5
                    done
                fi
            fi
            sleep 5
        done
        ;;
    *)
        start_ming_dock
        ;;
esac
MINGDOCKWATCH
    chmod 0755 /usr/local/bin/ming-dock-watchdog

cat > /usr/local/bin/ming-window-control << 'MINGWINDOWCONTROL'
#!/usr/bin/env bash
# Conservative Xfwm/EWMH recovery.  It never terminates client applications.
set -u

log_dir="${HOME}/.cache/ming-os"
mkdir -p "${log_dir}" 2>/dev/null || log_dir="${XDG_RUNTIME_DIR:-/tmp}"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/ming-os"
mkdir -p "${state_dir}" 2>/dev/null || state_dir="${XDG_RUNTIME_DIR:-/tmp}"
log_file="${log_dir}/window-manager.log"
rate_file="${state_dir}/window-manager.last-repair"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${log_file}" 2>/dev/null || true
}

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

status_fields() {
    xfwm_running=false
    wmctrl_available=false
    wm_name="missing"
    wm_name_matches=false
    wm_mismatch="wmctrl-unavailable"
    ewmh=false
    active_window="none"
    picom_running=false
    picom_profile="none"
    window_healthy=false

    if pgrep -u "$(id -u)" -x xfwm4 >/dev/null 2>&1; then
        xfwm_running=true
    fi

    local wm_info=""
    if wm_info="$(x11_call wmctrl -m 2>/dev/null)"; then
        wmctrl_available=true
        wm_name="$(awk -F: '/^Name:/ {sub(/^[[:space:]]*/, "", $2); print $2; exit}' <<<"${wm_info}" | tr -cd '[:alnum:]. _-')"
        wm_name="${wm_name:-unknown}"
        if [[ "${wm_name,,}" == "xfwm4" ]]; then
            wm_name_matches=true
            wm_mismatch="none"
        else
            wm_mismatch="wmctrl-reports-${wm_name}"
        fi
    fi

    local root_props=""
    if root_props="$(x11_call xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null)" \
        && grep -qE 'window id #[[:space:]]*0x[0-9a-fA-F]+' <<<"${root_props}"; then
        ewmh=true
    fi

    local active_props=""
    if active_props="$(x11_call xprop -root _NET_ACTIVE_WINDOW 2>/dev/null)"; then
        active_window="$(grep -oE '0x[0-9a-fA-F]+' <<<"${active_props}" | head -n1 || true)"
        active_window="${active_window:-none}"
    fi

    local picom_cmd=""
    if picom_cmd="$(pgrep -a -u "$(id -u)" -x picom 2>/dev/null)"; then
        picom_running=true
        case "${picom_cmd}" in
            *picom-fallback.conf*) picom_profile="software" ;;
            *picom-lowmem.conf*) picom_profile="low-memory" ;;
            *) picom_profile="main" ;;
        esac
    fi

    if ${xfwm_running} && ${wmctrl_available} && ${ewmh} && ${wm_name_matches}; then
        window_healthy=true
    fi
}

status_json() {
    status_fields
    MING_XFWM_RUNNING="${xfwm_running}" MING_WMCTRL_AVAILABLE="${wmctrl_available}" \
    MING_WM_NAME="${wm_name}" MING_WM_NAME_MATCHES="${wm_name_matches}" \
    MING_EWMH="${ewmh}" MING_HEALTHY="${window_healthy}" \
    MING_MISMATCH="${wm_mismatch}" MING_ACTIVE_WINDOW="${active_window}" \
    MING_PICOM_RUNNING="${picom_running}" MING_PICOM_PROFILE="${picom_profile}" \
    MING_LOG_FILE="${log_file}" python3 - <<'PY'
import json
import os

boolean = lambda name: os.environ.get(name) == "true"
print(json.dumps({
    "xfwm": {"running": boolean("MING_XFWM_RUNNING"),
             "wmctrl": boolean("MING_WMCTRL_AVAILABLE"),
             "name": os.environ["MING_WM_NAME"],
             "matches_ewmh": boolean("MING_WM_NAME_MATCHES")},
    "ewmh": boolean("MING_EWMH"),
    "healthy": boolean("MING_HEALTHY"),
    "mismatch": os.environ["MING_MISMATCH"],
    "active_window": os.environ["MING_ACTIVE_WINDOW"],
    "picom": {"running": boolean("MING_PICOM_RUNNING"),
              "profile": os.environ["MING_PICOM_PROFILE"]},
    "log": os.environ["MING_LOG_FILE"],
}, ensure_ascii=False))
PY
}

x11_id_is_valid() {
    [[ "${1:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]
}

require_window() {
    local window_id="$1"
    x11_id_is_valid "${window_id}" || {
        printf '窗口 ID 必须是十六进制 X11 ID（例如 0x01200007）。\n' >&2
        return 22
    }
    x11_call xprop -id "${window_id}" >/dev/null 2>&1 || {
        printf '未找到指定窗口：%s\n' "${window_id}" >&2
        return 3
    }
}

repair_window_manager() {
    [[ -n "${DISPLAY:-}" ]] || {
        log 'repair refused: no X11 DISPLAY is available'
        printf '当前没有可用的图形会话，未执行窗口管理器修复。\n' >&2
        return 2
    }
    command -v flock >/dev/null 2>&1 || {
        log 'repair refused: flock is unavailable'
        return 1
    }
    command -v xfwm4 >/dev/null 2>&1 || {
        log 'repair refused: xfwm4 is unavailable'
        return 1
    }

    exec 9>"${XDG_RUNTIME_DIR:-/tmp}/ming-window-control.lock" || return 1
    if ! flock -n 9; then
        log 'repair skipped: another window-manager repair is active'
        return 75
    fi

    status_fields
    if ${window_healthy}; then
        log 'repair skipped: Xfwm/EWMH is already healthy'
        return 0
    fi

    local now last_repair=0
    now="$(date +%s)"
    [[ -r "${rate_file}" ]] && read -r last_repair < "${rate_file}" || true
    if [[ "${last_repair}" =~ ^[0-9]+$ ]] && (( now - last_repair < 60 )); then
        log 'repair skipped: rate limited after a recent attempt'
        return 75
    fi
    printf '%s\n' "${now}" > "${rate_file}" 2>/dev/null || true

    log "repair starting: xfwm=${xfwm_running} wmctrl=${wmctrl_available} wm_name=${wm_name} ewmh=${ewmh} mismatch=${wm_mismatch}"
    # xfwm4 --replace reclaims only the window-manager selection; it does not
    # kill WPS, Quark, or any other client application.
    nohup xfwm4 --replace >>"${log_file}" 2>&1 &
    for _attempt in $(seq 1 8); do
        sleep 1
        status_fields
        if ${window_healthy}; then
            log 'repair succeeded: Xfwm/EWMH recovered'
            return 0
        fi
    done
    log "repair failed: xfwm=${xfwm_running} wmctrl=${wmctrl_available} wm_name=${wm_name} ewmh=${ewmh} mismatch=${wm_mismatch}"
    return 1
}

window_action() {
    local action="$1" window_id="$2"
    require_window "${window_id}" || return $?
    command -v wmctrl >/dev/null 2>&1 || {
        printf '窗口控制组件 wmctrl 不可用。\n' >&2
        return 127
    }
    case "${action}" in
        focus)
            x11_call wmctrl -i -a "${window_id}"
            ;;
        maximize)
            x11_call wmctrl -i -r "${window_id}" -b add,maximized_vert,maximized_horz && \
                x11_call wmctrl -i -a "${window_id}"
            ;;
        restore)
            x11_call wmctrl -i -r "${window_id}" -b remove,maximized_vert,maximized_horz,hidden && \
                x11_call wmctrl -i -a "${window_id}"
            ;;
        close)
            # wmctrl sends the EWMH _NET_CLOSE_WINDOW request.  The client is
            # allowed to veto it or prompt for unsaved work; no process is killed.
            x11_call wmctrl -i -c "${window_id}"
            ;;
    esac
}

case "${1:-}" in
    status)
        [[ "${2:-}" == "--json" && "$#" -eq 2 ]] || {
            printf 'Usage: %s status --json\n' "$0" >&2
            exit 2
        }
        status_json
        ;;
    repair)
        [[ "$#" -eq 1 ]] || { printf 'Usage: %s repair\n' "$0" >&2; exit 2; }
        repair_window_manager
        ;;
    focus|maximize|restore|close)
        [[ "${2:-}" == "--window-id" && -n "${3:-}" && "$#" -eq 3 ]] || {
            printf 'Usage: %s %s --window-id 0x01200007\n' "$0" "$1" >&2
            exit 2
        }
        window_action "$1" "$3"
        ;;
    *)
        printf 'Usage: %s {status --json|repair|focus|maximize|restore|close --window-id HEX}\n' "$0" >&2
        exit 2
        ;;
esac
MINGWINDOWCONTROL
    chmod 0755 /usr/local/bin/ming-window-control

cat > /usr/local/bin/ming-desktop-healthcheck << 'MINGDESKHEALTH'
#!/usr/bin/env bash
set -u

json=false
repair=false
for argument in "$@"; do
    case "${argument}" in
        --json) json=true ;;
        --repair) repair=true ;;
        *) printf 'Usage: %s [--json] [--repair]\n' "$0" >&2; exit 2 ;;
    esac
done

log_dir="${HOME}/.cache/ming-os"
mkdir -p "${log_dir}" 2>/dev/null || log_dir="${XDG_RUNTIME_DIR:-/tmp}"
log_file="${log_dir}/desktop-health.log"
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${log_file}" 2>/dev/null || true; }

has_proc() {
    pgrep -u "$(id -u)" -f "$1" >/dev/null 2>&1
}

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

window_manager_snapshot() {
    window_manager_running=false
    window_manager_ewmh=false
    window_manager_healthy=false
    window_manager_visible=false
    window_manager_stacking="missing"
    window_manager_geometry="n/a"
    window_manager_log="${HOME}/.cache/ming-os/window-manager.log"
    local status=""
    if [[ ! -x /usr/local/bin/ming-window-control ]]; then
        log "window manager helper is missing"
        return 0
    fi
    status="$(/usr/local/bin/ming-window-control status --json 2>>"${log_file}" || true)"
    if grep -Eq '"xfwm"[[:space:]]*:[[:space:]]*\{[[:space:]]*"running"[[:space:]]*:[[:space:]]*true' <<<"${status}"; then
        window_manager_running=true
        window_manager_visible=true
    fi
    if grep -Eq '"ewmh"[[:space:]]*:[[:space:]]*true' <<<"${status}"; then
        window_manager_ewmh=true
        window_manager_stacking="ewmh"
    fi
    if grep -Eq '"healthy"[[:space:]]*:[[:space:]]*true' <<<"${status}"; then
        window_manager_healthy=true
    fi
}

x11_id_is_valid() {
    [[ "${1:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]
}

window_id() {
    local kind="$1"
    command -v wmctrl >/dev/null 2>&1 || return 1
    case "${kind}" in
        desktop) x11_call wmctrl -lx 2>/dev/null | awk 'tolower($0) ~ /ming desktop/ && $1 ~ /^0[xX][0-9a-fA-F]+$/ { print $1; exit }' ;;
        dock)
            local fallback_id="" candidate_id candidate_geometry screen=""
            local xprop_available=false
            command -v xprop >/dev/null 2>&1 && xprop_available=true
            ${xprop_available} || screen="$(screen_geometry)"
            local dock_candidates=""
            # Avoid bash process substitution here: the rootfs validation
            # runs without /dev/fd mounted, while the equivalent here-string
            # remains safe in both the build chroot and a live X session.
            dock_candidates="$(x11_call wmctrl -lx 2>/dev/null | awk 'tolower($3) ~ /plank/ { print $1 }' || true)"
            while read -r candidate_id; do
                [[ "${candidate_id}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue
                [[ -n "${fallback_id}" ]] || fallback_id="${candidate_id}"
                if ${xprop_available} && x11_call xprop -id "${candidate_id}" 2>/dev/null | grep -q '_NET_WM_WINDOW_TYPE_DOCK'; then
                    printf '%s\n' "${candidate_id}"
                    return 0
                fi
                if ! ${xprop_available}; then
                    candidate_geometry="$(window_geometry "${candidate_id}")"
                    if geometry_is_in_bounds "${candidate_geometry}" "${screen}" && \
                       geometry_is_bottom "${candidate_geometry}" "${screen}"; then
                        printf '%s\n' "${candidate_id}"
                        return 0
                    fi
                fi
            done <<< "${dock_candidates}"
            [[ -n "${fallback_id}" ]] && printf '%s\n' "${fallback_id}"
            ;;
    esac
}

window_geometry() {
    local id="$1"
    x11_id_is_valid "${id}" || return 1
    x11_call wmctrl -lGx 2>/dev/null | awk -v id="${id}" '$1 == id { printf "%s,%s,%s,%s", $3, $4, $5, $6; exit }'
}

screen_geometry() {
    local dimensions
    dimensions="$(x11_call xrandr --current 2>/dev/null | sed -n 's/.*current \([0-9][0-9]*\) x \([0-9][0-9]*\).*/0 0 \1 \2/p' | head -n1)"
    if [[ -z "${dimensions}" ]] && command -v xdpyinfo >/dev/null 2>&1; then
        dimensions="$(x11_call xdpyinfo 2>/dev/null | sed -n 's/.*dimensions:[[:space:]]*\([0-9][0-9]*\)x\([0-9][0-9]*\).*/0 0 \1 \2/p' | head -n1)"
    fi
    [[ -n "${dimensions}" ]] || dimensions="0 0 32768 32768"
    printf '%s\n' "${dimensions}"
}

geometry_is_visible() {
    local geometry="$1" screen="$2"
    local x y width height sx sy sw sh
    IFS=, read -r x y width height <<<"${geometry}"
    read -r sx sy sw sh <<<"${screen}"
    [[ "${x:-}" =~ ^-?[0-9]+$ && "${y:-}" =~ ^-?[0-9]+$ && "${width:-}" =~ ^[0-9]+$ && "${height:-}" =~ ^[0-9]+$ ]] || return 1
    (( width > 0 && height > 0 && x < sx + sw && y < sy + sh && x + width > sx && y + height > sy ))
}

geometry_is_in_bounds() {
    local geometry="$1" screen="$2"
    local x y width height sx sy sw sh
    IFS=, read -r x y width height <<<"${geometry}"
    read -r sx sy sw sh <<<"${screen}"
    [[ "${x:-}" =~ ^-?[0-9]+$ && "${y:-}" =~ ^-?[0-9]+$ && "${width:-}" =~ ^[0-9]+$ && "${height:-}" =~ ^[0-9]+$ ]] || return 1
    (( width > 0 && height > 0 && x >= sx && y >= sy && x + width <= sx + sw && y + height <= sy + sh ))
}

geometry_is_bottom() {
    local geometry="$1" screen="$2"
    local x y width height sx sy sw sh
    IFS=, read -r x y width height <<<"${geometry}"
    read -r sx sy sw sh <<<"${screen}"
    [[ "${y:-}" =~ ^-?[0-9]+$ && "${height:-}" =~ ^[0-9]+$ ]] || return 1
    (( y + height >= sy + (sh * 3 / 4) ))
}

window_stacking() {
    local id="$1" role="$2" properties=""
    if x11_id_is_valid "${id}" && command -v xprop >/dev/null 2>&1; then
        properties="$(x11_call xprop -id "${id}" 2>/dev/null || true)"
    fi
    if [[ "${role}" == "dock" ]]; then
        if grep -q '_NET_WM_WINDOW_TYPE_DOCK' <<<"${properties}"; then
            printf 'dock'
        else
            printf 'normal'
        fi
    elif grep -q '_NET_WM_WINDOW_TYPE_DESKTOP' <<<"${properties}"; then
        printf 'desktop'
    else
        printf 'normal'
    fi
}

repair_components() {
    log "repair requested"
    if command -v ming-window-control >/dev/null 2>&1; then
        /usr/local/bin/ming-window-control repair >>"${log_file}" 2>&1 \
            || log "window-manager repair did not recover Xfwm/EWMH"
    fi
    if command -v ming-phone-desktop-watchdog >/dev/null 2>&1; then
        /usr/local/bin/ming-phone-desktop-watchdog >/dev/null 2>&1 || log "desktop repair failed"
    fi
    if command -v ming-plank-watchdog >/dev/null 2>&1; then
        /usr/local/bin/ming-plank-watchdog >/dev/null 2>&1 || log "dock repair failed"
    fi
    if ! has_proc 'ming-launch[[:space:]]+--server' && [[ -x /usr/local/bin/ming-launch ]]; then
        (nohup /usr/local/bin/ming-launch --server >>"${log_file}" 2>&1 &) || log "launch broker repair failed"
    fi
    sleep 1
    log "repair completed"
}

${repair} && repair_components

window_manager_snapshot

desktop_running=false
desktop_visible=false
desktop_stacking="missing"
desktop_geometry="none"
if has_proc '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)'; then
    desktop_running=true
fi
desktop_id="$(window_id desktop)"
if [[ -n "${desktop_id}" ]]; then
    desktop_stacking="$(window_stacking "${desktop_id}" desktop)"
    desktop_geometry="$(window_geometry "${desktop_id}")"
    if [[ -n "${desktop_geometry}" ]] && geometry_is_visible "${desktop_geometry}" "$(screen_geometry)"; then
        desktop_visible=true
    else
        desktop_geometry="${desktop_geometry:-unknown}"
    fi
fi

dock_running=false
dock_visible=false
dock_healthy=false
dock_stacking="missing"
dock_geometry="none"
if pgrep -u "$(id -u)" -x plank >/dev/null 2>&1; then
    dock_running=true
fi
dock_id="$(window_id dock)"
if [[ -n "${dock_id}" ]]; then
    dock_stacking="$(window_stacking "${dock_id}" dock)"
    dock_geometry="$(window_geometry "${dock_id}")"
    if [[ -n "${dock_geometry}" ]] && \
       geometry_is_in_bounds "${dock_geometry}" "$(screen_geometry)" && \
       geometry_is_bottom "${dock_geometry}" "$(screen_geometry)"; then
        dock_visible=true
        if [[ "${dock_stacking}" == "dock" ]]; then
            dock_healthy=true
        fi
    else
        dock_geometry="${dock_geometry:-unknown}"
    fi
fi

broker_running=false
has_proc 'ming-launch[[:space:]]+--server' && broker_running=true

if ${json}; then
    MING_DESKTOP_RUNNING="${desktop_running}" MING_DESKTOP_VISIBLE="${desktop_visible}" \
    MING_DESKTOP_STACKING="${desktop_stacking}" MING_DESKTOP_GEOMETRY="${desktop_geometry}" \
    MING_DOCK_RUNNING="${dock_running}" MING_DOCK_VISIBLE="${dock_visible}" \
    MING_DOCK_STACKING="${dock_stacking}" MING_DOCK_GEOMETRY="${dock_geometry}" \
    MING_BROKER_RUNNING="${broker_running}" MING_WM_RUNNING="${window_manager_running}" \
    MING_WM_VISIBLE="${window_manager_visible}" MING_WM_STACKING="${window_manager_stacking}" \
    MING_WM_GEOMETRY="${window_manager_geometry}" MING_WM_EWMH="${window_manager_ewmh}" \
    MING_WM_HEALTHY="${window_manager_healthy}" MING_WM_LOG="${window_manager_log}" python3 - <<'PY'
import json
import os

boolean = lambda name: os.environ.get(name) == "true"
component = lambda prefix: {"running": boolean(prefix + "_RUNNING"),
                            "visible": boolean(prefix + "_VISIBLE"),
                            "stacking": os.environ[prefix + "_STACKING"],
                            "geometry": os.environ[prefix + "_GEOMETRY"]}
payload = {
    "desktop": component("MING_DESKTOP"),
    "dock": component("MING_DOCK"),
    "launch_broker": {"running": boolean("MING_BROKER_RUNNING"), "visible": False,
                      "stacking": "n/a", "geometry": "n/a"},
    "window_manager": {"running": boolean("MING_WM_RUNNING"),
                       "visible": boolean("MING_WM_VISIBLE"),
                       "stacking": os.environ["MING_WM_STACKING"],
                       "geometry": os.environ["MING_WM_GEOMETRY"],
                       "ewmh": boolean("MING_WM_EWMH"),
                       "healthy": boolean("MING_WM_HEALTHY"),
                       "log": os.environ["MING_WM_LOG"]},
}
print(json.dumps(payload, ensure_ascii=False))
PY
else
    printf 'desktop: running=%s visible=%s stacking=%s geometry=%s\n' "${desktop_running}" "${desktop_visible}" "${desktop_stacking}" "${desktop_geometry}"
    printf 'dock: running=%s visible=%s stacking=%s geometry=%s\n' "${dock_running}" "${dock_visible}" "${dock_stacking}" "${dock_geometry}"
    printf 'launch_broker: running=%s visible=false stacking=n/a geometry=n/a\n' "${broker_running}"
    printf 'window_manager: running=%s ewmh=%s healthy=%s log=%s\n' \
        "${window_manager_running}" "${window_manager_ewmh}" "${window_manager_healthy}" "${window_manager_log}"
fi

${desktop_running} && ${desktop_visible} && ${dock_running} && ${dock_healthy} && ${broker_running} && ${window_manager_healthy}
MINGDESKHEALTH
    chmod 0755 /usr/local/bin/ming-desktop-healthcheck

cat > /usr/local/bin/ming-window-manager-watchdog << 'MINGWINDOWWATCH'
#!/usr/bin/env bash
# Observe Xfwm/EWMH separately from the desktop/Dock watchdogs.  A transient
# X11 query failure is not enough to replace the window manager.
set -u

log_dir="${HOME}/.cache/ming-os"
mkdir -p "${log_dir}" 2>/dev/null || log_dir="${XDG_RUNTIME_DIR:-/tmp}"
log_file="${log_dir}/window-manager.log"
log() { printf '[%s] watchdog: %s\n' "$(date '+%F %T')" "$*" >>"${log_file}" 2>/dev/null || true; }

window_manager_healthy() {
    local status=""
    status="$(/usr/local/bin/ming-window-control status --json 2>>"${log_file}" || true)"
    grep -Fq '"healthy":true' <<<"${status}"
}

run_session() {
    local lock_file="${XDG_RUNTIME_DIR:-/tmp}/ming-window-manager-watchdog.lock"
    exec 9>"${lock_file}" || exit 1
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        exit 0
    fi

    local failure_count=0 last_repair=0 now
    sleep 3
    while true; do
        if window_manager_healthy; then
            failure_count=0
        else
            failure_count=$((failure_count + 1))
            log "health failure ${failure_count}/3"
            now="$(date +%s)"
            if (( failure_count >= 3 )) && (( now - last_repair >= 60 )); then
                log 'three consecutive failures; requesting conservative Xfwm/EWMH repair'
                /usr/local/bin/ming-window-control repair >>"${log_file}" 2>&1 \
                    || log 'repair attempt did not report recovery'
                last_repair="${now}"
                failure_count=0
            fi
        fi
        sleep 10
    done
}

case "${1:-start}" in
    --session) run_session ;;
    --check) window_manager_healthy ;;
    *)
        /usr/local/bin/ming-window-control status --json
        ;;
esac
MINGWINDOWWATCH
    chmod 0755 /usr/local/bin/ming-window-manager-watchdog

cat > /usr/local/bin/ming-phone-desktop-watchdog << 'PHONEDESKWATCH'
#!/usr/bin/env bash
set -u

ming_log_dir() {
    local primary="${HOME}/.cache/ming-os"
    if mkdir -p "${primary}" 2>/dev/null && [[ -w "${primary}" ]]; then
        printf '%s\n' "${primary}"
        return 0
    fi
    local fallback="${XDG_RUNTIME_DIR:-/tmp}/ming-os-$(id -u)"
    mkdir -p "${fallback}" 2>/dev/null || fallback="/tmp"
    printf '%s\n' "${fallback}"
}

ming_log() {
    local file="$1"
    shift
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${file}" 2>/dev/null || true
}

stop_xfdesktop() {
    # Ming Phone Desktop owns wallpaper, icons and click handling.
    # Stop xfdesktop only after Ming Phone Desktop is confirmed alive; otherwise
    # the user would be left with a black root window.
    xfdesktop --quit >/dev/null 2>&1 || true
    pkill -u "$(id -u)" -x xfdesktop >/dev/null 2>&1 || true
}

start_xfdesktop_fallback() {
    pgrep -u "$(id -u)" -x xfdesktop >/dev/null 2>&1 && return 0
    command -v xfdesktop >/dev/null 2>&1 || return 0
    (nohup xfdesktop >/dev/null 2>&1 &) || true
}

phone_desktop_running() {
    pgrep -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' >/dev/null 2>&1
}

phone_desktop_process_count() {
    local count
    count="$(pgrep -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' 2>/dev/null | wc -l || true)"
    [[ "${count}" =~ ^[0-9]+$ ]] || count=0
    printf '%s\n' "${count}"
}

stop_duplicate_phone_desktops() {
    local count
    count="$(phone_desktop_process_count)"
    [[ "${count}" -le 1 ]] && return 0
    log "stopping duplicate Ming Phone Desktop processes (${count})"
    pkill -TERM -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' >/dev/null 2>&1 || true
    sleep 0.2
}

phone_desktop_ready() {
    [[ -s "${HOME}/.cache/ming-os/ming-phone-desktop.ready" ]]
}

wait_phone_desktop_ready() {
    local log_file="$1"
    local attempt
    # The session coordinator owns the fixed eight-second desktop startup
    # budget.  Keep this one-shot helper bounded to the same deadline so a
    # repair cannot outlive the supervisor's startup window.
    for attempt in $(seq 1 16); do
        if phone_desktop_running && phone_desktop_ready; then
            return 0
        fi
        sleep 0.5
    done
    ming_log "${log_file}" "ming-phone-desktop did not publish ready marker; keeping xfdesktop fallback"
    return 1
}

start_phone_desktop() {
    if [[ "${MING_PHONE_DESKTOP:-1}" != "1" ]]; then
        ming_log "$(ming_log_dir)/ming-phone-desktop.log" \
            'MING_PHONE_DESKTOP is not 1; keeping xfdesktop fallback'
        start_xfdesktop_fallback
        return 1
    fi
    command -v ming-phone-desktop >/dev/null 2>&1 || return 0
    stop_duplicate_phone_desktops
    export DISPLAY="${DISPLAY:-:0}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
    local log_file
    log_file="$(ming_log_dir)/ming-phone-desktop.log"
    if phone_desktop_running; then
        if wait_phone_desktop_ready "${log_file}"; then
            stop_xfdesktop
            return 0
        fi
        start_xfdesktop_fallback
        return 1
    fi
    rm -f "${HOME}/.cache/ming-os/ming-phone-desktop.ready" 2>/dev/null || true
    ming_log "${log_file}" "starting ming-phone-desktop DISPLAY=${DISPLAY}"
    (nohup ming-phone-desktop >>"${log_file}" 2>&1 &) || (nohup ming-phone-desktop >/dev/null 2>&1 &)
    if wait_phone_desktop_ready "${log_file}"; then
        stop_xfdesktop
    else
        ming_log "${log_file}" "ming-phone-desktop did not stay running; keeping xfdesktop fallback"
        start_xfdesktop_fallback
        return 1
    fi
}

lock_dir="${XDG_RUNTIME_DIR:-/tmp}/ming-phone-desktop-watchdog.lock"
if ! mkdir "${lock_dir}" 2>/dev/null; then
    # A concurrent coordinator/repair already owns the one-shot startup.
    # Never launch a second desktop while the lock is held.
    exit 0
fi
trap 'rmdir "${lock_dir}" 2>/dev/null || true' EXIT

case "${1:-start}" in
    --session)
        sleep 4
        while true; do
            start_phone_desktop
            sleep 5
        done
        ;;
    *)
        start_phone_desktop
        ;;
esac
PHONEDESKWATCH
    chmod 0755 /usr/local/bin/ming-phone-desktop-watchdog

    cat > /usr/local/bin/ming-plank-watchdog << 'PLANKWATCH'
#!/usr/bin/env bash
set -u

log_dir="${HOME}/.cache/ming-os"
mkdir -p "${log_dir}" 2>/dev/null || log_dir="${XDG_RUNTIME_DIR:-/tmp}"
log_file="${log_dir}/plank.log"
stacking_promotion_attempted_for=""

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${log_file}" 2>/dev/null || true
}

stop_legacy_dock() {
    pkill -TERM -u "$(id -u)" -f '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-dock([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-dock([[:space:]]|$)' >/dev/null 2>&1 || true
}

write_default_plank_settings() {
    local settings="$1"
    cat >"${settings}" << 'PLANKRUNTIMESETTINGS'
[PlankDockPreferences]
# MingDockProfile=2640-legacy-centered
DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-files.dockitem;;ming-firefox.dockitem;;spark-store.dockitem;;xiahai-xiaoming.dockitem;;ming-terminal.dockitem
Position=3
Alignment=3
Offset=0
IconSize=40
ZoomEnabled=true
ZoomPercent=148
HideMode=0
UnhideDelay=0
HideDelay=0
Theme=Ming
Monitor=
LockItems=false
PressureReveal=false
ShowDockItem=true
ItemsAlignment=3
FadeOpacity=1.0
PLANKRUNTIMESETTINGS
}

apply_low_resource_plank_profile() {
    local settings="$1"
    local mem_mb virt renderer cmdline
    mem_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
    virt="$(systemd-detect-virt 2>/dev/null || true)"
    cmdline="$(cat /proc/cmdline 2>/dev/null || true)"
    renderer="$(glxinfo -B 2>/dev/null | awk -F: '/OpenGL renderer/ {print tolower($2); exit}' | sed 's/^ *//' || true)"
    # Keep the approved 26.4.0 Dock geometry on every machine.  Low-resource
    # savings come from compositor/session policy, not a visually different Dock.
    log "legacy Plank geometry retained (mem=${mem_mb}MB virt=${virt:-none} renderer=${renderer:-unknown} cmdline=${cmdline:-none})"
}

plank_setting_value() {
    local settings="$1" key="$2" fallback="$3" value
    value="$(awk -F= -v key="${key}" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "${settings}" 2>/dev/null || true)"
    printf '%s\n' "${value:-$fallback}"
}

apply_plank_runtime_preferences() {
    local settings="${HOME}/.config/plank/dock1/settings"
    local theme theme_dconf current_theme icon_size zoom_enabled zoom_percent hide_mode hide_mode_runtime offset
    local plank_schema='net.launchpad.plank.dock.settings:/net/launchpad/plank/docks/dock1/'
    theme="$(plank_setting_value "${settings}" Theme Ming)"
    theme="${theme//\'/}"
    theme_dconf="'Ming'"
    theme_dconf="'${theme:-Ming}'"
    # MingMintCompact is the active profile.  Keep the legacy defaults above
    # for migration detection, then enforce the compact values before Plank
    # is started so upgraded users converge on the same geometry.
    theme="Ming-Mint"
    theme_dconf="'Ming-Mint'"
    if command -v gsettings >/dev/null 2>&1; then
        current_theme="$(gsettings get "${plank_schema}" theme 2>/dev/null || true)"
    elif command -v dconf >/dev/null 2>&1; then
        current_theme="$(dconf read /net/launchpad/plank/docks/dock1/theme 2>/dev/null || true)"
    else
        log "neither gsettings nor dconf is available; Plank runtime preferences were not applied"
        return 1
    fi
    if pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 \
        && [[ "${current_theme}" != "${theme_dconf}" ]]; then
        MING_PLANK_RELOAD_REQUIRED=1
        log "existing Plank theme ${current_theme:-unset} differs from ${theme_dconf}; one reload required"
    fi
    icon_size="$(plank_setting_value "${settings}" IconSize 40)"
    zoom_enabled="$(plank_setting_value "${settings}" ZoomEnabled true)"
    zoom_percent="$(plank_setting_value "${settings}" ZoomPercent 148)"
    hide_mode="$(plank_setting_value "${settings}" HideMode 0)"
    offset="$(plank_setting_value "${settings}" Offset 0)"
    icon_size=32
    zoom_enabled=true
    zoom_percent=125
    offset=12
    if [[ -f "${settings}" ]]; then
        sed -i -e 's/^IconSize=.*/IconSize=32/' \
               -e 's/^ZoomEnabled=.*/ZoomEnabled=true/' \
               -e 's/^ZoomPercent=.*/ZoomPercent=125/' \
               -e 's/^Offset=.*/Offset=12/' \
               -e 's/^Theme=.*/Theme=Ming-Mint/' "${settings}" 2>/dev/null || true
    fi
    case "${hide_mode}" in
        0) hide_mode_runtime=none ;;
        1) hide_mode_runtime=intelligent ;;
        2) hide_mode_runtime=auto ;;
        *) hide_mode_runtime=none ;;
    esac
    if command -v gsettings >/dev/null 2>&1; then
        gsettings set "${plank_schema}" theme "${theme:-Ming}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings theme"
        gsettings set "${plank_schema}" icon-size "${icon_size:-40}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings icon-size"
        gsettings set "${plank_schema}" zoom-enabled "${zoom_enabled:-true}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings zoom-enabled"
        gsettings set "${plank_schema}" zoom-percent "${zoom_percent:-148}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings zoom-percent"
        gsettings set "${plank_schema}" hide-mode "${hide_mode_runtime}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings hide-mode"
        gsettings set "${plank_schema}" position bottom >>"${log_file}" 2>&1 || log "could not write Plank gsettings position"
        gsettings set "${plank_schema}" alignment center >>"${log_file}" 2>&1 || log "could not write Plank gsettings alignment"
        gsettings set "${plank_schema}" items-alignment center >>"${log_file}" 2>&1 || log "could not write Plank gsettings item alignment"
        gsettings set "${plank_schema}" offset "${offset:-0}" >>"${log_file}" 2>&1 || log "could not write Plank gsettings offset"
    else
        dconf write /net/launchpad/plank/docks/dock1/theme "${theme_dconf}" >>"${log_file}" 2>&1 || log "could not write Plank dconf theme"
        dconf write /net/launchpad/plank/docks/dock1/icon-size "${icon_size:-40}" >>"${log_file}" 2>&1 || log "could not write Plank dconf icon-size"
        dconf write /net/launchpad/plank/docks/dock1/zoom-enabled "${zoom_enabled:-true}" >>"${log_file}" 2>&1 || log "could not write Plank dconf zoom-enabled"
        dconf write /net/launchpad/plank/docks/dock1/zoom-percent "${zoom_percent:-148}" >>"${log_file}" 2>&1 || log "could not write Plank dconf zoom-percent"
        dconf write /net/launchpad/plank/docks/dock1/hide-mode "${hide_mode:-0}" >>"${log_file}" 2>&1 || log "could not write Plank dconf hide-mode"
        dconf write /net/launchpad/plank/docks/dock1/alignment "'center'" >>"${log_file}" 2>&1 || log "could not write Plank dconf alignment"
        dconf write /net/launchpad/plank/docks/dock1/items-alignment "'center'" >>"${log_file}" 2>&1 || log "could not write Plank dconf item alignment"
        dconf write /net/launchpad/plank/docks/dock1/offset "${offset:-0}" >>"${log_file}" 2>&1 || log "could not write Plank dconf offset"
    fi
}

migrate_legacy_dock_profile() {
    local settings="$1"
    grep -q '^# MingDockProfile=2640-legacy-centered$' "${settings}" 2>/dev/null && return 0

    local dock_items='ming-settings.dockitem;;ming-app-library.dockitem;;ming-files.dockitem;;ming-firefox.dockitem;;spark-store.dockitem;;xiahai-xiaoming.dockitem;;ming-terminal.dockitem'
    if grep -q '^DockItems=' "${settings}"; then
        sed -i "s|^DockItems=.*|DockItems=${dock_items}|" "${settings}" 2>/dev/null || true
    else
        printf 'DockItems=%s\n' "${dock_items}" >>"${settings}"
    fi
    if grep -q '^IconSize=' "${settings}"; then
        sed -i "s/^IconSize=.*/IconSize=40/" "${settings}" 2>/dev/null || true
    else
        printf 'IconSize=40\n' >>"${settings}"
    fi
    if grep -q '^ZoomPercent=' "${settings}"; then
        sed -i "s/^ZoomPercent=.*/ZoomPercent=148/" "${settings}" 2>/dev/null || true
    else
        printf 'ZoomPercent=148\n' >>"${settings}"
    fi
    if grep -q '^ZoomEnabled=' "${settings}"; then
        sed -i "s/^ZoomEnabled=.*/ZoomEnabled=true/" "${settings}" 2>/dev/null || true
    else
        printf 'ZoomEnabled=true\n' >>"${settings}"
    fi
    if grep -q '^Alignment=' "${settings}"; then
        sed -i "s/^Alignment=.*/Alignment=3/" "${settings}" 2>/dev/null || true
    else
        printf 'Alignment=3\n' >>"${settings}"
    fi
    if grep -q '^Offset=' "${settings}"; then
        sed -i "s/^Offset=.*/Offset=0/" "${settings}" 2>/dev/null || true
    else
        printf 'Offset=0\n' >>"${settings}"
    fi
    if grep -q '^ItemsAlignment=' "${settings}"; then
        sed -i "s/^ItemsAlignment=.*/ItemsAlignment=3/" "${settings}" 2>/dev/null || true
    else
        printf 'ItemsAlignment=3\n' >>"${settings}"
    fi
    if grep -q '^Theme=' "${settings}"; then
        sed -i "s/^Theme=.*/Theme=Ming/" "${settings}" 2>/dev/null || true
    else
        printf 'Theme=Ming\n' >>"${settings}"
    fi
    sed -i '/^# MingDockProfile=2641-compact-rail-1$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-glass-rail-1$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-glass-rail-2$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-frosted-white-rail-2$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-macos-compact-glass-1$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-macos-frosted-centered-1$/d' "${settings}" 2>/dev/null || true
    sed -i '/^# MingDockProfile=2641-macos-frosted-centered-2$/d' "${settings}" 2>/dev/null || true
    printf '# MingDockProfile=2640-legacy-centered\n' >>"${settings}"
    find "${HOME}/.config/plank/dock1/launchers" -maxdepth 1 -iname '*claw*.dockitem' -delete 2>/dev/null || true
    MING_PLANK_RELOAD_REQUIRED=1
    log "migrated Dock to the 26.4.0 legacy profile"
}

ensure_plank_settings() {
    local settings="${HOME}/.config/plank/dock1/settings"
    local skel_settings="/etc/skel/.config/plank/dock1/settings"
    local restored=false
    mkdir -p "$(dirname "${settings}")" 2>/dev/null || true
    if [[ ! -f "${settings}" ]]; then
        if [[ -s "${skel_settings}" ]]; then
            cp "${skel_settings}" "${settings}" 2>/dev/null || write_default_plank_settings "${settings}"
        else
            write_default_plank_settings "${settings}"
        fi
        restored=true
        log "restored complete Plank settings profile"
    fi
    MING_PLANK_RELOAD_REQUIRED=0
    migrate_legacy_dock_profile "${settings}"
    if grep -q '^HideMode=' "${settings}"; then
        sed -i 's/^HideMode=.*/HideMode=0/' "${settings}" 2>/dev/null || true
    else
        printf 'HideMode=0\n' >>"${settings}"
    fi
    if ${restored} && [[ -x /usr/local/sbin/ming-refresh-dock-launchers ]]; then
        /usr/local/sbin/ming-refresh-dock-launchers "$(id -un)" >>"${log_file}" 2>&1 || \
            log "Dock launcher refresh after settings restore failed"
    fi
}

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

valid_window_id() {
    [[ "${1:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]
}

plank_process_count() {
    local processes
    processes="$(pgrep -u "$(id -u)" -x plank 2>/dev/null | wc -l || true)"
    [[ "${processes}" =~ ^[0-9]+$ ]] || processes=0
    printf '%s\n' "${processes}"
}

plank_window_id() {
    command -v wmctrl >/dev/null 2>&1 || return 1
    local fallback_id="" candidate_id candidate_geometry screen=""
    local xprop_available=false
    command -v xprop >/dev/null 2>&1 && xprop_available=true
    ${xprop_available} || screen="$(screen_geometry)"
    local dock_candidates=""
    dock_candidates="$(x11_call wmctrl -lx 2>/dev/null | awk 'tolower($3) ~ /plank/ { print $1 }' || true)"
    while read -r candidate_id; do
        [[ "${candidate_id}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue
        [[ -n "${fallback_id}" ]] || fallback_id="${candidate_id}"
        if ${xprop_available} && x11_call xprop -id "${candidate_id}" 2>/dev/null | grep -q '_NET_WM_WINDOW_TYPE_DOCK'; then
            printf '%s\n' "${candidate_id}"
            return 0
        fi
        if ! ${xprop_available}; then
            candidate_geometry="$(window_geometry "${candidate_id}")"
            if geometry_in_bounds "${candidate_geometry}" "${screen}" && \
               position_is_bottom "${candidate_geometry}" "${screen}"; then
                printf '%s\n' "${candidate_id}"
                return 0
            fi
        fi
    done <<< "${dock_candidates}"
    [[ -n "${fallback_id}" ]] && printf '%s\n' "${fallback_id}"
}

window_geometry() {
    local window_id="$1"
    valid_window_id "${window_id}" || return 1
    x11_call wmctrl -lGx 2>/dev/null | awk -v id="${window_id}" '$1 == id { printf "%s %s %s %s\n", $3, $4, $5, $6; exit }'
}

screen_geometry() {
    local dimensions
    dimensions="$(x11_call xrandr --current 2>/dev/null | sed -n 's/.*current \([0-9][0-9]*\) x \([0-9][0-9]*\).*/0 0 \1 \2/p' | head -n1)"
    [[ -n "${dimensions}" ]] || dimensions="0 0 32768 32768"
    printf '%s\n' "${dimensions}"
}

geometry_in_bounds() {
    local geometry="$1" screen="$2"
    local x y width height sx sy sw sh
    read -r x y width height <<<"${geometry}"
    read -r sx sy sw sh <<<"${screen}"
    [[ "${x:-}" =~ ^-?[0-9]+$ && "${y:-}" =~ ^-?[0-9]+$ && "${width:-}" =~ ^[0-9]+$ && "${height:-}" =~ ^[0-9]+$ ]] || return 1
    (( width > 0 && height > 0 && x >= sx && y >= sy && x + width <= sx + sw && y + height <= sy + sh ))
}

position_is_bottom() {
    local geometry="$1" screen="$2"
    local x y width height sx sy sw sh
    read -r x y width height <<<"${geometry}"
    read -r sx sy sw sh <<<"${screen}"
    (( y + height >= sy + (sh * 3 / 4) ))
}

window_has_property() {
    local window_id="$1" property="$2"
    valid_window_id "${window_id}" || return 1
    command -v xprop >/dev/null 2>&1 || return 1
    x11_call xprop -id "${window_id}" 2>/dev/null | grep -q "${property}"
}

plank_health_reason() {
    local processes
    processes="$(plank_process_count)"
    [[ "${processes}" -eq 0 ]] && { printf 'not-running\n'; return; }
    [[ "${processes}" -eq 1 ]] || { printf 'duplicate-processes\n'; return; }
    local window_id geometry screen
    window_id="$(plank_window_id)"
    [[ -n "${window_id}" ]] || { printf 'window-not-visible\n'; return; }
    if command -v xprop >/dev/null 2>&1 && \
       ! window_has_property "${window_id}" '_NET_WM_WINDOW_TYPE_DOCK'; then
        printf 'wrong-window-type\n'
        return
    fi
    geometry="$(window_geometry "${window_id}")"
    screen="$(screen_geometry)"
    geometry_in_bounds "${geometry}" "${screen}" || { printf 'out-of-bounds\n'; return; }
    position_is_bottom "${geometry}" "${screen}" || { printf 'wrong-position\n'; return; }
    printf 'healthy\n'
}

plank_window_visible() {
    [[ "$(plank_health_reason)" == "healthy" ]]
}

repair_plank_stacking() {
    local window_id
    window_id="$(plank_window_id)"
    valid_window_id "${window_id}" || return 1
    x11_call wmctrl -i -r "${window_id}" -b add,sticky >/dev/null 2>&1 || return 1
}

avoid_covering_windows() {
    local window_id
    window_id="$(plank_window_id)"
    valid_window_id "${window_id}" || return 0
    if command -v xprop >/dev/null 2>&1; then
        x11_call xprop -id "${window_id}" -remove _NET_WM_STRUT >/dev/null 2>&1 || true
        x11_call xprop -id "${window_id}" -remove _NET_WM_STRUT_PARTIAL >/dev/null 2>&1 || true
    fi
    repair_plank_stacking || log "could not keep Dock sticky across workspaces"
}

diagnose_and_promote_stacking() {
    local window_id
    window_id="$(plank_window_id)"
    [[ -n "${window_id}" ]] || return 0
    if ! command -v xprop >/dev/null 2>&1; then
        log "xprop unavailable; accepting running visible Dock without stacking diagnostics"
        return 0
    fi
    if ! window_has_property "${window_id}" '_NET_WM_STATE_ABOVE'; then
        [[ "${stacking_promotion_attempted_for}" == "${window_id}" ]] && return 0
        stacking_promotion_attempted_for="${window_id}"
        log "not-above: ABOVE state is absent; reserving Dock workarea without forcing topmost"
        avoid_covering_windows
    fi
    return 0
}

wait_for_plank_exit() {
    for _stop_try in $(seq 1 20); do
        pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 || return 0
        sleep 0.1
    done
    return 1
}

stop_plank() {
    pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 || return 0
    pkill -TERM -u "$(id -u)" -x plank >/dev/null 2>&1 || return 1
    wait_for_plank_exit
}

start_plank() {
    command -v plank >/dev/null 2>&1 || return 1
    stop_legacy_dock
    export DISPLAY="${DISPLAY:-:0}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
    ensure_plank_settings
    apply_low_resource_plank_profile "${HOME}/.config/plank/dock1/settings"
    apply_plank_runtime_preferences
    if [[ "${MING_PLANK_RELOAD_REQUIRED:-0}" == "1" ]] && pgrep -u "$(id -u)" -x plank >/dev/null 2>&1; then
        log "restarting Plank so the glass rail theme replaces the previous running Dock"
        stop_plank || true
        sleep 0.5
    fi
    local reason
    reason="$(plank_health_reason)"
    if [[ "${reason}" == "healthy" ]]; then
        avoid_covering_windows
        diagnose_and_promote_stacking
        return 0
    fi
    log "Plank health failure: ${reason}; starting recovery"
    if pgrep -u "$(id -u)" -x plank >/dev/null 2>&1; then
        stop_plank
        sleep 1
    fi
    log "starting Plank DISPLAY=${DISPLAY} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
    (nohup plank >>"${log_file}" 2>&1 &) || return 1
    # Keep the one-shot repair within the coordinator's fixed eight-second
    # Plank startup budget (32 x 250ms).
    for _ready_try in $(seq 1 32); do
        reason="$(plank_health_reason)"
        if [[ "${reason}" == "healthy" ]]; then
            avoid_covering_windows
            diagnose_and_promote_stacking
            log "Plank recovery succeeded"
            return 0
        fi
        sleep 0.25
    done
    log "Plank recovery failed: $(plank_health_reason)"
    stop_plank
    return 1
}

run_one_shot() {
    local lock_file="${XDG_RUNTIME_DIR:-/tmp}/ming-plank-watchdog.lock"
    exec 9>"${lock_file}" || exit 1
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        log "Plank one-shot repair already owns ${lock_file}"
        exit 0
    fi
    start_plank
}

case "${1:-start}" in
    --check)
        reason="$(plank_health_reason)"
        if [[ "${reason}" == "healthy" ]]; then
            exit 0
        fi
        log "Plank health check failed: ${reason}"
        exit 1
        ;;
    --reload)
        lock_file="${XDG_RUNTIME_DIR:-/tmp}/ming-plank-watchdog.lock"
        exec 9>"${lock_file}" || exit 1
        if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
            exit 1
        fi
        stop_plank || {
            log "Plank reload could not stop the old process"
            exit 1
        }
        start_plank
        ;;
    --session)
        lock_file="${XDG_RUNTIME_DIR:-/tmp}/ming-plank-watchdog.lock"
        exec 9>"${lock_file}" || exit 1
        if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
            exit 0
        fi
        sleep 3
        while true; do
            start_plank || log "Plank recovery attempt failed"
            sleep 5
        done
        ;;
    *)
        run_one_shot
        ;;
esac
PLANKWATCH
    chmod 0755 /usr/local/bin/ming-plank-watchdog

    # Plank is the primary Dock: it owns zoom, running indicators and minimized-window restore.
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    rm -f "${autostart_dir}/plank.desktop"
    # Compatibility filename retained for upgrades; the direct Dock session
    # loop is disabled so only ming-session-healthcheck owns the long-lived
    # Plank lifecycle.
    cat > "${autostart_dir}/ming-dock.desktop" << 'MINGDOCKAUTO'
[Desktop Entry]
Type=Application
Name=Ming Dock
Exec=/usr/bin/true
Comment=Ming OS Dock; legacy ming-plank-watchdog --session is one-shot only
Icon=ming-os-menu
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
X-Ming-Managed-By=ming-session-healthcheck
MINGDOCKAUTO

    cat > "${autostart_dir}/ming-window-manager.desktop" << 'MINGWINDOWMANAGERAUTO'
[Desktop Entry]
Type=Application
Name=Ming Window Manager Health
Comment=检测并保守恢复 Xfwm 窗口控制
Exec=/usr/local/bin/ming-window-manager-watchdog --session
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=4
MINGWINDOWMANAGERAUTO

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/plank" \
        "${autostart_dir}/ming-dock.desktop" \
        "${autostart_dir}/ming-window-manager.desktop"
}

# ======================== 统一会话启动/健康协调器 ========================
#
# Phone Desktop、Plank 与 Picom 都保留可单次调用的 watchdog，便于外观
# 修复和设置页做幂等 repair；真正的登录期常驻循环只有这个协调器。
configure_session_healthcheck() {
    cat > /usr/local/bin/ming-session-healthcheck << 'MINGSESSIONHEALTH'
#!/usr/bin/env bash
# Ming OS unified session startup and health coordinator.
set -u

readonly PHONE_STARTUP_DEADLINE=8
readonly PLANK_STARTUP_DEADLINE=8
readonly PICOM_STARTUP_DEADLINE=5
# Startup watchdogs are bounded as timeout --foreground 8s / 8s / 5s.
readonly PROBE_TIMEOUT=2
readonly SUPERVISOR_INTERVAL=10
readonly AUDIO_CHECK_INTERVAL=30

log_dir="${HOME}/.cache/ming-os"
mkdir -p "${log_dir}" 2>/dev/null || log_dir="${XDG_RUNTIME_DIR:-/tmp}"
mkdir -p "${log_dir}" 2>/dev/null || true
health_log="${log_dir}/session-health.log"
metrics_file="${log_dir}/session-startup.json"
lock_file="${XDG_RUNTIME_DIR:-/tmp}/ming-session-healthcheck.lock"
pid_file="${XDG_RUNTIME_DIR:-/tmp}/ming-session-healthcheck.pid"
picom_policy_file="${XDG_RUNTIME_DIR:-/tmp}/ming-picom-policy"
touch "${health_log}" 2>/dev/null || true

# Image builds may provide a system-wide default.  An explicitly exported
# session value still wins so MING_PHONE_DESKTOP=1/0 is honored at login.
ming_phone_desktop_env="${MING_PHONE_DESKTOP-__unset__}"
if [[ -r /etc/default/ming-os ]]; then
    . /etc/default/ming-os
fi
if [[ "${ming_phone_desktop_env}" != "__unset__" ]]; then
    MING_PHONE_DESKTOP="${ming_phone_desktop_env}"
fi
: "${MING_PHONE_DESKTOP:=1}"

# Per-component counters are intentionally kept in the coordinator process so
# the JSON snapshot can explain whether a repair was needed without scraping
# human log text.  They reset on a fresh login, while the log remains append-only.
phone_elapsed_ms=0
plank_elapsed_ms=0
picom_elapsed_ms=0
phone_restarts=0
plank_restarts=0
picom_restarts=0
phone_recovered=false
plank_recovered=false
picom_recovered=false
last_audio_check=0

now_ms() {
    local value
    value="$(date +%s%3N 2>/dev/null || true)"
    [[ "${value}" =~ ^[0-9]+$ ]] && printf '%s\n' "${value}" || printf '%s\n' "$(( $(date +%s) * 1000 ))"
}

process_count() {
    local kind="$1" count="0"
    case "${kind}" in
        phone)
            count="$(probe_timeout pgrep -u "$(id -u)" -f \
                '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' 2>/dev/null | wc -l || true)"
            ;;
        plank)
            count="$(probe_timeout pgrep -u "$(id -u)" -x plank 2>/dev/null | wc -l || true)"
            ;;
        picom)
            count="$(probe_timeout pgrep -u "$(id -u)" -x picom 2>/dev/null | wc -l || true)"
            ;;
    esac
    [[ "${count}" =~ ^[0-9]+$ ]] || count=0
    printf '%s\n' "${count}"
}

stop_legacy_ming_dock() {
    local legacy_pattern='(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-dock([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-dock([[:space:]]|$)'
    local watchdog_pattern='(^|[[:space:]])/usr/local/bin/ming-dock-watchdog([[:space:]]|$)'
    if probe_timeout pgrep -u "$(id -u)" -f "${legacy_pattern}" >/dev/null 2>&1; then
        log 'stopping retired custom Ming Dock before starting Plank'
        probe_timeout pkill -TERM -u "$(id -u)" -f "${legacy_pattern}" >/dev/null 2>&1 || true
    fi
    if probe_timeout pgrep -u "$(id -u)" -f "${watchdog_pattern}" >/dev/null 2>&1; then
        log 'stopping retired Ming Dock watchdog from an upgraded session'
        probe_timeout pkill -TERM -u "$(id -u)" -f "${watchdog_pattern}" >/dev/null 2>&1 || true
    fi
    # Old session snapshots can contain this autostart entry even after the
    # package has migrated the factory profile.  Remove only the retired
    # Ming-specific entry; leave all user applications untouched.
    rm -f "${HOME}/.config/autostart/ming-dock-watchdog.desktop" \
          "${HOME}/.config/autostart/ming-legacy-dock.desktop" 2>/dev/null || true
}

stop_duplicate_phone_desktops() {
    local processes
    processes="$(process_count phone)"
    [[ "${processes}" -eq 1 ]] && return 0
    [[ "${processes}" -gt 1 ]] || return 0
    log "stopping duplicate Ming Phone Desktop processes (${processes})"
    probe_timeout pkill -TERM -u "$(id -u)" -f \
        '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' \
        >/dev/null 2>&1 || true
    sleep 0.2
}

stop_duplicate_picom() {
    local processes
    processes="$(process_count picom)"
    [[ "${processes}" -eq 1 ]] && return 0
    [[ "${processes}" -gt 1 ]] || return 0
    log "stopping duplicate Picom processes (${processes})"
    stop_picom_and_wait
}

xfce_panel_running() {
    probe_timeout pgrep -u "$(id -u)" -x xfce4-panel >/dev/null 2>&1
}

xfce_panel_window_visible() {
    command -v wmctrl >/dev/null 2>&1 || return 1
    x11_call wmctrl -lx 2>/dev/null |
        awk 'tolower($3) ~ /xfce4-panel/ {found=1} END {exit !found}'
}

suppress_xfce_panel() {
    if [[ "${MING_PHONE_DESKTOP:-1}" != "1" ]]; then
        if ! xfce_panel_running && command -v xfce4-panel >/dev/null 2>&1; then
            (nohup xfce4-panel >/dev/null 2>&1 &) || true
        fi
        return 0
    fi
    xfce_panel_running || xfce_panel_window_visible || return 0
    probe_timeout xfce4-panel --quit >/dev/null 2>&1 || true
    probe_timeout pkill -TERM -u "$(id -u)" -x xfce4-panel >/dev/null 2>&1 || true
    sleep 0.1
    ! xfce_panel_running && ! xfce_panel_window_visible
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${health_log}" 2>/dev/null || true
}

# Every X11 probe is bounded.  Process probes use the same helper so a broken
# DISPLAY cannot stall the session supervisor.
# timeout --foreground 2s is the hard probe ceiling.
probe_timeout() {
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${PROBE_TIMEOUT}s" "$@"
    else
        "$@"
    fi
}

x11_call() {
    probe_timeout "$@"
}

run_bounded() {
    local deadline="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${deadline}s" "$@"
    else
        "$@"
    fi
}

ensure_audio_session() {
    # PulseAudio normally handles hotplug itself, but old HDA codecs can wake
    # from suspend with a stale default sink/profile.  Recheck from the already
    # user-scoped session coordinator at a low cadence.  The helper is bounded
    # and preserves a valid user-selected HDMI, Bluetooth or USB output.
    local now
    now="$(date +%s 2>/dev/null || printf '0')"
    [[ "${now}" =~ ^[0-9]+$ ]] || now=0
    if (( last_audio_check > 0 && now > 0 && now - last_audio_check < AUDIO_CHECK_INTERVAL )); then
        return 0
    fi
    last_audio_check="${now}"
    command -v ming-audio-session >/dev/null 2>&1 || return 0
    log 'checking audio session after login, resume or device change'
    (run_bounded 6 /usr/local/bin/ming-audio-session ensure --json \
        >>"${health_log}" 2>&1 &) || true
}

phone_desktop_running() {
    [[ "$(process_count phone)" -eq 1 ]]
}

phone_desktop_ready() {
    phone_desktop_running && [[ -s "${HOME}/.cache/ming-os/ming-phone-desktop.ready" ]]
}

xfdesktop_running() {
    probe_timeout pgrep -u "$(id -u)" -x xfdesktop >/dev/null 2>&1
}

start_xfdesktop_fallback() {
    xfdesktop_running && return 0
    command -v xfdesktop >/dev/null 2>&1 || {
        log 'xfdesktop fallback unavailable'
        return 1
    }
    log 'starting xfdesktop fallback'
    (nohup xfdesktop >>"${health_log}" 2>&1 &) || true
    return 0
}

stop_xfdesktop_after_phone_ready() {
    phone_desktop_ready || return 1
    probe_timeout xfdesktop --quit >/dev/null 2>&1 || true
    probe_timeout pkill -TERM -u "$(id -u)" -x xfdesktop >/dev/null 2>&1 || true
}

plank_running() {
    [[ "$(process_count plank)" -eq 1 ]]
}

plank_window_visible() {
    plank_running || return 1
    if command -v ming-plank-watchdog >/dev/null 2>&1; then
        run_bounded "${PROBE_TIMEOUT}" /usr/local/bin/ming-plank-watchdog --check
        return $?
    fi
    command -v wmctrl >/dev/null 2>&1 || return 0
    x11_call wmctrl -lx 2>/dev/null | awk 'tolower($3) ~ /plank/ {found=1} END {exit !found}'
}

picom_running() {
    [[ "$(process_count picom)" -eq 1 ]]
}

picom_policy_disabled() {
    [[ -r "${picom_policy_file}" ]] \
        && grep -Fxq 'compositor=disabled-by-policy' "${picom_policy_file}" 2>/dev/null
}

picom_user_disabled() {
    command -v python3 >/dev/null 2>&1 || return 1
    local settings="${HOME}/.config/ming-os/settings.json"
    local appearance="${HOME}/.config/ming-os/appearance.json"
    [[ -r "${settings}" || -r "${appearance}" ]] || return 1
    python3 - "${settings}" "${appearance}" <<'PY'
import json
import pathlib
import sys

for value in sys.argv[1:]:
    try:
        payload = json.loads(pathlib.Path(value).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        continue
    if isinstance(payload, dict) and payload.get("compositor_profile") == "off":
        raise SystemExit(0)
raise SystemExit(1)
PY
}

picom_disabled() {
    picom_policy_disabled || picom_user_disabled
}

wait_for_picom_exit() {
    local deadline_at=$(( $(now_ms) + 2000 )) current_ms
    while [[ "$(process_count picom)" -gt 0 ]]; do
        current_ms="$(now_ms)"
        [[ "${current_ms}" =~ ^[0-9]+$ ]] || return 1
        (( current_ms >= deadline_at )) && return 1
        sleep 0.1
    done
    return 0
}

stop_picom_and_wait() {
    [[ "$(process_count picom)" -gt 0 ]] || return 0
    probe_timeout pkill -TERM -u "$(id -u)" -x picom >/dev/null 2>&1 || return 1
    wait_for_picom_exit
}

wait_for_process() {
    local kind="$1"
    local deadline="$2"
    local deadline_at=$(( $(now_ms) + deadline * 1000 ))
    wait_for_process_until "${kind}" "${deadline_at}"
}

wait_for_process_until() {
    local kind="$1"
    local deadline_at="$2"
    local current_ms
    while true; do
        case "${kind}" in
            phone) phone_desktop_ready && return 0 ;;
            plank) plank_window_visible && return 0 ;;
            picom) picom_running && return 0 ;;
        esac
        current_ms="$(now_ms)"
        [[ "${current_ms}" =~ ^[0-9]+$ ]] || current_ms="${deadline_at}"
        (( current_ms >= deadline_at )) && break
        sleep 0.1
    done
    return 1
}

start_phone_desktop() {
    local started_at finished_at deadline_at
    if [[ "${MING_PHONE_DESKTOP:-1}" != "1" ]]; then
        log 'MING_PHONE_DESKTOP is not 1; keeping native xfdesktop'
        phone_recovered=false
        start_xfdesktop_fallback
        return 1
    fi
    if ! command -v ming-phone-desktop >/dev/null 2>&1; then
        log 'ming-phone-desktop is unavailable; keeping native xfdesktop'
        phone_recovered=false
        start_xfdesktop_fallback
        return 1
    fi
    stop_duplicate_phone_desktops
    if phone_desktop_ready; then
        phone_recovered=true
        stop_xfdesktop_after_phone_ready || true
        return 0
    fi
    phone_restarts=$((phone_restarts + 1))
    started_at="$(now_ms)"
    deadline_at=$((started_at + PHONE_STARTUP_DEADLINE * 1000))
    log "starting Ming Phone Desktop (deadline=${PHONE_STARTUP_DEADLINE}s)"
    (run_bounded "${PHONE_STARTUP_DEADLINE}" \
        /usr/local/bin/ming-phone-desktop-watchdog >>"${health_log}" 2>&1 &) || true
    if wait_for_process_until phone "${deadline_at}"; then
        finished_at="$(now_ms)"
        phone_elapsed_ms=$((finished_at - started_at))
        phone_recovered=true
        stop_xfdesktop_after_phone_ready || true
        log 'Ming Phone Desktop ready'
        return 0
    fi
    finished_at="$(now_ms)"
    phone_elapsed_ms=$((finished_at - started_at))
    phone_recovered=false
    log 'Ming Phone Desktop startup failed; using xfdesktop fallback'
    start_xfdesktop_fallback
    return 1
}

start_plank_dock() {
    local started_at finished_at deadline_at was_running=false
    plank_running && was_running=true
    command -v ming-plank-watchdog >/dev/null 2>&1 || {
        log 'ming-plank-watchdog is unavailable'
        return 1
    }
    if [[ "${was_running}" != "true" ]]; then
        plank_restarts=$((plank_restarts + 1))
    fi
    started_at="$(now_ms)"
    deadline_at=$((started_at + PLANK_STARTUP_DEADLINE * 1000))
    log "applying Plank runtime theme and checking Dock (deadline=${PLANK_STARTUP_DEADLINE}s)"
    (run_bounded "${PLANK_STARTUP_DEADLINE}" \
        /usr/local/bin/ming-plank-watchdog >>"${health_log}" 2>&1 &) || true
    if wait_for_process_until plank "${deadline_at}"; then
        finished_at="$(now_ms)"
        plank_elapsed_ms=$((finished_at - started_at))
        plank_recovered=true
        log 'Plank Dock ready'
        return 0
    fi
    finished_at="$(now_ms)"
    plank_elapsed_ms=$((finished_at - started_at))
    plank_recovered=false
    log 'Plank Dock startup failed'
    return 1
}

start_xrender_picom() {
    local deadline_at="${1:-$(( $(now_ms) + PICOM_STARTUP_DEADLINE * 1000 ))}"
    picom_disabled && return 0
    command -v picom >/dev/null 2>&1 || return 1
    picom_running && return 0
    log 'starting Picom xrender fallback'
    (nohup picom --config /etc/xdg/picom/picom-fallback.conf \
        >>"${health_log}" 2>&1 &) || true
    wait_for_process_until picom "${deadline_at}"
}

start_picom() {
    local started_at finished_at deadline_at
    stop_duplicate_picom || return 1
    if picom_disabled; then
        if ! stop_picom_and_wait; then
            log 'Picom is disabled but the old process did not exit'
            picom_recovered=false
            return 1
        fi
        picom_recovered=true
        return 0
    fi
    if picom_running; then
        picom_recovered=true
        return 0
    fi
    picom_restarts=$((picom_restarts + 1))
    started_at="$(now_ms)"
    deadline_at=$((started_at + PICOM_STARTUP_DEADLINE * 1000))
    if command -v ming-picom >/dev/null 2>&1; then
        log "starting Picom (deadline=${PICOM_STARTUP_DEADLINE}s)"
        (nohup /usr/local/bin/ming-picom >>"${health_log}" 2>&1 &) || true
        for _policy_try in 1 2 3 4 5; do
            picom_disabled && {
                picom_elapsed_ms=$(( $(now_ms) - started_at ))
                picom_recovered=true
                return 0
            }
            sleep 0.1
        done
        if wait_for_process_until picom "${deadline_at}"; then
            finished_at="$(now_ms)"
            picom_elapsed_ms=$((finished_at - started_at))
            picom_recovered=true
            return 0
        fi
    fi
    if start_xrender_picom "${deadline_at}"; then
        finished_at="$(now_ms)"
        picom_elapsed_ms=$((finished_at - started_at))
        picom_recovered=true
        return 0
    fi
    finished_at="$(now_ms)"
    picom_elapsed_ms=$((finished_at - started_at))
    picom_recovered=false
    return 1
}

write_metrics() {
    local phase="$1"
    local phone_fallback="${2:-false}"
    local phone_enabled=false phone_running=false phone_ready=false
    local xfdesktop=false dock=false dock_visible=false compositor=false panel_running=false
    local compositor_backend=none
    local phone_pid_count=0 plank_pid_count=0 picom_pid_count=0
    local phone_duplicates=0 plank_duplicates=0 picom_duplicates=0
    [[ "${MING_PHONE_DESKTOP:-1}" == "1" ]] && phone_enabled=true
    phone_desktop_running && phone_running=true
    phone_desktop_ready && phone_ready=true
    xfdesktop_running && xfdesktop=true
    plank_running && dock=true
    plank_window_visible && dock_visible=true
    picom_running && compositor=true
    if xfce_panel_running || xfce_panel_window_visible; then panel_running=true; fi
    phone_pid_count="$(process_count phone)"
    plank_pid_count="$(process_count plank)"
    picom_pid_count="$(process_count picom)"
    (( phone_pid_count > 1 )) && phone_duplicates=$((phone_pid_count - 1))
    (( plank_pid_count > 1 )) && plank_duplicates=$((plank_pid_count - 1))
    (( picom_pid_count > 1 )) && picom_duplicates=$((picom_pid_count - 1))
    if picom_user_disabled; then
        compositor_backend=disabled-by-user
    elif picom_policy_disabled; then
        compositor_backend=disabled-by-policy
    elif ${compositor}; then
        local compositor_cmd
        compositor_cmd="$(probe_timeout pgrep -a -u "$(id -u)" -x picom 2>/dev/null || true)"
        case "${compositor_cmd}" in
            *picom-fallback.conf*) compositor_backend=xrender ;;
            *picom-lowmem.conf*) compositor_backend=low-memory ;;
            *) compositor_backend=auto ;;
        esac
    fi
    MING_METRICS_FILE="${metrics_file}" \
    MING_PHASE="${phase}" MING_PHONE_ENABLED="${phone_enabled}" \
    MING_PHONE_RUNNING="${phone_running}" MING_PHONE_READY="${phone_ready}" \
    MING_PHONE_FALLBACK="${phone_fallback}" MING_XFDESKTOP="${xfdesktop}" \
    MING_DOCK_RUNNING="${dock}" MING_DOCK_VISIBLE="${dock_visible}" \
    MING_PICOM_RUNNING="${compositor}" MING_PICOM_BACKEND="${compositor_backend}" \
    MING_PHONE_PID_COUNT="${phone_pid_count}" MING_PLANK_PID_COUNT="${plank_pid_count}" \
    MING_PICOM_PID_COUNT="${picom_pid_count}" MING_PHONE_DUPLICATES="${phone_duplicates}" \
    MING_PLANK_DUPLICATES="${plank_duplicates}" MING_PICOM_DUPLICATES="${picom_duplicates}" \
    MING_PHONE_ELAPSED_MS="${phone_elapsed_ms}" MING_PLANK_ELAPSED_MS="${plank_elapsed_ms}" \
    MING_PICOM_ELAPSED_MS="${picom_elapsed_ms}" MING_PHONE_RESTARTS="${phone_restarts}" \
    MING_PLANK_RESTARTS="${plank_restarts}" MING_PICOM_RESTARTS="${picom_restarts}" \
    MING_PHONE_RECOVERED="${phone_recovered}" MING_PLANK_RECOVERED="${plank_recovered}" \
    MING_PICOM_RECOVERED="${picom_recovered}" MING_PANEL_RUNNING="${panel_running}" \
    MING_HEALTH_LOG="${health_log}" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

boolean = lambda name: os.environ.get(name) == "true"
integer = lambda name: int(os.environ.get(name, "0") or 0)
payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "phase": os.environ.get("MING_PHASE", "unknown"),
    "phone_desktop": {
        "enabled": boolean("MING_PHONE_ENABLED"),
        "running": boolean("MING_PHONE_RUNNING"),
        "ready": boolean("MING_PHONE_READY"),
        "fallback": boolean("MING_PHONE_FALLBACK"),
        "pid_count": integer("MING_PHONE_PID_COUNT"),
        "elapsed_ms": integer("MING_PHONE_ELAPSED_MS"),
        "restarts": integer("MING_PHONE_RESTARTS"),
        "recovered": boolean("MING_PHONE_RECOVERED"),
        "duplicates": integer("MING_PHONE_DUPLICATES"),
    },
    "xfdesktop": {"running": boolean("MING_XFDESKTOP")},
    "xfce_panel": {"running": boolean("MING_PANEL_RUNNING")},
    "plank": {
        "running": boolean("MING_DOCK_RUNNING"),
        "visible": boolean("MING_DOCK_VISIBLE"),
        "pid_count": integer("MING_PLANK_PID_COUNT"),
        "elapsed_ms": integer("MING_PLANK_ELAPSED_MS"),
        "restarts": integer("MING_PLANK_RESTARTS"),
        "recovered": boolean("MING_PLANK_RECOVERED"),
        "duplicates": integer("MING_PLANK_DUPLICATES"),
    },
    "picom": {
        "running": boolean("MING_PICOM_RUNNING"),
        "backend": os.environ.get("MING_PICOM_BACKEND", "none"),
        "pid_count": integer("MING_PICOM_PID_COUNT"),
        "elapsed_ms": integer("MING_PICOM_ELAPSED_MS"),
        "restarts": integer("MING_PICOM_RESTARTS"),
        "recovered": boolean("MING_PICOM_RECOVERED"),
        "duplicates": integer("MING_PICOM_DUPLICATES"),
    },
    "deadlines": {"phone_desktop": 8, "plank": 8, "picom": 5},
    "startup_deadlines": {"phone_desktop": 8, "plank": 8, "picom": 5},
    "probe_timeout": 2,
    "supervisor_interval": 10,
    "health_log": os.environ.get("MING_HEALTH_LOG", ""),
    "duplicates": {
        "phone_desktop": integer("MING_PHONE_DUPLICATES"),
        "plank": integer("MING_PLANK_DUPLICATES"),
        "picom": integer("MING_PICOM_DUPLICATES"),
    },
}
payload["healthy"] = (
    (payload["phone_desktop"]["ready"] or
     (payload["phone_desktop"]["fallback"] and payload["xfdesktop"]["running"]))
    and payload["plank"]["visible"]
    and (payload["picom"]["running"]
         or payload["picom"]["backend"] in {"disabled-by-policy", "disabled-by-user"})
    and (not payload["phone_desktop"]["enabled"]
         or not payload["xfce_panel"]["running"])
)
path = Path(os.environ["MING_METRICS_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

startup_once() {
    local phone_fallback=false
    log 'session startup check begin'
    stop_legacy_ming_dock
    suppress_xfce_panel || log 'Xfce panel remained visible in Phone Desktop mode'
    start_phone_desktop || phone_fallback=true
    start_plank_dock || log 'Plank Dock is not healthy after startup deadline'
    start_picom || log 'Picom is not healthy after startup deadline'
    ensure_audio_session
    write_metrics startup "${phone_fallback}"
    log 'session startup check complete'
}

supervise_once() {
    local phone_fallback=false
    log 'session supervisor check begin'
    stop_legacy_ming_dock
    suppress_xfce_panel || log 'Xfce panel remained visible in Phone Desktop mode'
    if ! start_phone_desktop; then
        phone_fallback=true
    fi
    start_plank_dock || log 'Plank Dock repair did not recover a visible window'
    start_picom || log 'Picom repair did not recover a compositor'
    ensure_audio_session
    write_metrics supervisor "${phone_fallback}"
    log 'session supervisor check complete'
}

acquire_coordinator_lock() {
    exec 9>"${lock_file}" || return 1
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        log 'session coordinator already owns the lock'
        return 1
    fi
    if [[ -s "${pid_file}" ]]; then
        local old_pid
        read -r old_pid <"${pid_file}" || old_pid=""
        if [[ "${old_pid}" =~ ^[0-9]+$ && "${old_pid}" != "$$" ]] && \
           probe_timeout kill -0 "${old_pid}" >/dev/null 2>&1; then
            log "session coordinator pid ${old_pid} is still alive"
            return 1
        fi
    fi
    printf '%s\n' "$$" >"${pid_file}" 2>/dev/null || true
    trap 'rm -f "${pid_file}" 2>/dev/null || true' EXIT
    return 0
}

case "${1:---once}" in
    --session)
        acquire_coordinator_lock || exit 0
        startup_once
        while true; do
            sleep "${SUPERVISOR_INTERVAL}" # fixed supervisor cadence: sleep 10
            supervise_once
        done
        ;;
    --once)
        acquire_coordinator_lock || exit 0
        startup_once
        ;;
    --check)
        [[ -s "${metrics_file}" ]] && cat "${metrics_file}" || write_metrics check
        ;;
    --reload-dock)
        command -v ming-plank-watchdog >/dev/null 2>&1 || exit 1
        run_bounded "${PLANK_STARTUP_DEADLINE}" \
            /usr/local/bin/ming-plank-watchdog --reload
        ;;
    *)
        printf 'Usage: %s --session|--once|--check|--reload-dock\n' "$0" >&2
        exit 2
        ;;
esac
MINGSESSIONHEALTH
    chmod 0755 /usr/local/bin/ming-session-healthcheck
}


# ======================== Ming Shell: 控制中心与品牌化入口 ========================

configure_ming_shell() {
    mkdir -p "/home/${MING_USER}/.config/xfce4/terminal" \
             "/home/${MING_USER}/.local/share/applications"

    cat > /usr/local/bin/ming-terminal << 'MINGTERM'
#!/usr/bin/env bash
exec xfce4-terminal --hide-menubar --title="Ming Terminal" "$@"
MINGTERM
    chmod +x /usr/local/bin/ming-terminal

    cat > /usr/local/bin/ming-power-action << 'MINGPOWERACTION'
#!/usr/bin/env bash
# Route all desktop power requests through one bounded, diagnosable fallback chain.
set -u

LOG=/tmp/ming-power-action.log
ACTION="${1:-menu}"

log() {
    mkdir -p /tmp 2>/dev/null || true
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "${LOG}" 2>/dev/null || true
}

record_inhibitors() {
    log "action=${ACTION} user=$(id -un 2>/dev/null || printf unknown) session=${XDG_SESSION_ID:-unknown}"
    if command -v systemd-inhibit >/dev/null 2>&1; then
        timeout --foreground 3s systemd-inhibit --list >> "${LOG}" 2>&1 || \
            log "systemd-inhibit list was unavailable"
    fi
}

run_attempt() {
    local label="$1"
    shift
    command -v "$1" >/dev/null 2>&1 || {
        log "${label}: missing command $1"
        return 1
    }
    log "${label}: $*"
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground 8s "$@" >> "${LOG}" 2>&1 && return 0
    else
        "$@" >> "${LOG}" 2>&1 && return 0
    fi
    log "${label}: failed"
    return 1
}

notify_failure() {
    local message="$1"
    log "failure: ${message}"
    notify-send -i dialog-error "Ming OS 电源操作未完成" "${message}。详情：${LOG}" 2>/dev/null || true
}

record_inhibitors
case "${ACTION}" in
    menu)
        if run_attempt "xfce power menu" xfce4-session-logout; then
            exit 0
        fi
        notify_failure "无法打开电源菜单"
        exit 1
        ;;
    logout)
        run_attempt "xfce logout" xfce4-session-logout --logout && exit 0
        if [[ -n "${XDG_SESSION_ID:-}" ]]; then
            run_attempt "logind terminate session" loginctl terminate-session "${XDG_SESSION_ID}" && exit 0
        fi
        run_attempt "logind terminate user" loginctl terminate-user "$(id -un)" && exit 0
        notify_failure "无法注销当前会话"
        exit 1
        ;;
    reboot|poweroff)
        if [[ "${ACTION}" == reboot ]]; then
            run_attempt "xfce reboot" xfce4-session-logout --reboot && exit 0
        else
            run_attempt "xfce poweroff" xfce4-session-logout --halt && exit 0
        fi
        run_attempt "logind ${ACTION}" loginctl "${ACTION}" && exit 0
        run_attempt "systemctl ${ACTION}" systemctl "${ACTION}" --no-wall && exit 0
        notify_failure "系统拒绝${ACTION}请求"
        exit 1
        ;;
    *)
        notify_failure "不支持的电源操作"
        exit 2
        ;;
esac
MINGPOWERACTION
    chmod 0755 /usr/local/bin/ming-power-action

    cat > /usr/local/bin/ming-lock << 'MINGLOCK'
#!/usr/bin/env bash
set -uo pipefail

lock_is_deferred() {
    if grep -qwE "boot=live|live-config|ming.installer=1" /proc/cmdline 2>/dev/null \
        || [[ -f /.disk/info || -d /lib/live/mount/medium ]]; then
        return 0
    fi
    local marker="${HOME}/.config/ming-os/oobe-account-done"
    [[ -r "${marker}" ]] && grep -Fxq configured "${marker}" 2>/dev/null && return 1
    return 0
}

if lock_is_deferred; then
    notify-send "Ming OS" "Live 或首次设置期间暂不锁屏。" 2>/dev/null || true
    exit 0
fi

if command -v xfce4-screensaver-command >/dev/null 2>&1; then
    xfce4-screensaver-command --lock >/tmp/ming-lock.log 2>&1 && exit 0
fi

if command -v xflock4 >/dev/null 2>&1; then
    xflock4 >/tmp/ming-lock.log 2>&1 && exit 0
fi

if command -v dm-tool >/dev/null 2>&1; then
    dm-tool lock >/tmp/ming-lock.log 2>&1 && exit 0
fi

if command -v loginctl >/dev/null 2>&1; then
    loginctl lock-session >/tmp/ming-lock.log 2>&1 && exit 0
fi

notify-send "Ming OS" "暂时无法锁定屏幕，请稍后重试。" 2>/dev/null || true
exit 1
MINGLOCK
    chmod +x /usr/local/bin/ming-lock

    cat > /usr/local/bin/ming-status-center << 'STATUSCENTER'
#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import datetime
import os
import subprocess
import sys

CSS = b'''
window { background: #F7F9F6; }
.root {
  background: linear-gradient(135deg, #F9FBF8, #EFF5F1 58%, #E5EFE9);
  color: #1C2320;
}
.title { font-size: 24px; font-weight: 800; color: #1C2320; }
.subtitle { font-size: 12px; color: #5C6963; }
.time { font-size: 34px; font-weight: 800; color: #1C2320; }
.date { font-size: 12px; color: #5C6963; }
.tile {
  background: #FFFFFF;
  border: 1px solid rgba(31,98,84,0.09);
  border-radius: 12px;
  padding: 12px;
  color: #1C2320;
}
.tile:hover {
  background: #F4F8F5;
  border-color: rgba(47,138,125,0.22);
  box-shadow: 0 8px 20px rgba(30,70,58,0.07);
}
.tile label { color: #1C2320; font-weight: 700; }
.danger {
  background: #FFF7F7;
  border-color: rgba(178,59,72,0.28);
}
'''

def run(command):
    try:
        subprocess.Popen(command, shell=True)
    except Exception:
        pass

def text(command, fallback='--'):
    try:
        out = subprocess.check_output(command, shell=True, stderr=subprocess.DEVNULL, text=True, timeout=2)
        return out.strip() or fallback
    except Exception:
        return fallback

class StatusCenter(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Ming 状态中心')
        self.set_default_size(520, 430)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name('ming-control-center')

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.get_style_context().add_class('root')
        root.set_border_width(22)
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.pack_start(header, False, False, 0)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.pack_start(title_box, True, True, 0)

        title = Gtk.Label(label='Ming 状态中心')
        title.set_halign(Gtk.Align.START)
        title.get_style_context().add_class('title')
        title_box.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(label='网络、声音、电源和退出都在这里')
        subtitle.set_halign(Gtk.Align.START)
        subtitle.get_style_context().add_class('subtitle')
        title_box.pack_start(subtitle, False, False, 0)

        self.time_label = Gtk.Label()
        self.time_label.set_halign(Gtk.Align.END)
        self.time_label.get_style_context().add_class('time')
        header.pack_start(self.time_label, False, False, 0)

        self.date_label = Gtk.Label()
        self.date_label.set_halign(Gtk.Align.END)
        self.date_label.get_style_context().add_class('date')
        root.pack_start(self.date_label, False, False, 0)

        self.summary = Gtk.Label()
        self.summary.set_halign(Gtk.Align.START)
        self.summary.set_line_wrap(True)
        root.pack_start(self.summary, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        root.pack_start(grid, True, True, 0)

        actions = [
            ('连接网络', 'network-wireless', 'nm-connection-editor'),
            ('声音', 'multimedia-volume-control', 'pavucontrol'),
            ('电源', 'battery', 'xfce4-power-manager-settings'),
            ('显示', 'video-display', 'ming-control-center --page display'),
            ('设置', 'ming-control-center', 'ming-control-center'),
            ('应用库', 'ming-app-library', 'ming-app-library'),
            ('锁屏', 'system-lock-screen', 'ming-lock'),
            ('退出/关机', 'system-shutdown', 'ming-power-action menu'),
        ]
        for index, (label, icon, command) in enumerate(actions):
            button = self.tile(label, icon, command, danger=(label == '退出/关机'))
            grid.attach(button, index % 4, index // 4, 1, 1)

        self.refresh()
        GLib.timeout_add_seconds(30, self.refresh)

    def tile(self, label, icon, command, danger=False):
        button = Gtk.Button()
        button.set_size_request(112, 92)
        button.get_style_context().add_class('tile')
        if danger:
            button.get_style_context().add_class('danger')
        button.connect('clicked', lambda _button: run(command))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        text_label = Gtk.Label(label=label)
        text_label.set_justify(Gtk.Justification.CENTER)
        text_label.set_line_wrap(True)
        box.pack_start(image, False, False, 0)
        box.pack_start(text_label, False, False, 0)
        button.add(box)
        return button

    def refresh(self):
        now = datetime.datetime.now()
        self.time_label.set_text(now.strftime('%H:%M'))
        self.date_label.set_text(now.strftime('%Y-%m-%d  %A'))
        network = text("nmcli -t -f NAME connection show --active | head -n1", "未连接网络")
        volume = text("sh -c \"amixer get Master | awk -F'[][]' '/%/ {print $2; exit}'\"", "--")
        battery = text("sh -c \"upower -e | grep BAT | head -n1 | xargs -r upower -i | awk -F': *' '/percentage/ {print $2; exit}'\"", "台式机/无电池")
        self.summary.set_text(f'网络：{network}    音量：{volume}    电池：{battery}')
        return True

class App(Gtk.Application):
    def do_activate(self):
        window = StatusCenter(self)
        window.show_all()

if __name__ == '__main__':
    app = App()
    app.run(sys.argv)
STATUSCENTER
    chmod +x /usr/local/bin/ming-status-center

    cat > /usr/local/bin/ming-app-library << 'APPLIB'
#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Gio
import configparser
import os
import subprocess
import sys

APP_DIRS = ['/usr/share/applications', os.path.expanduser('~/.local/share/applications')]

CSS = b'''
window { background: #F7F9F6; }
.root {
  background: linear-gradient(135deg, #F9FBF8, #EFF5F1 52%, #E5EFE9);
  color: #1C2320;
}
.title { font-size: 26px; font-weight: 800; color: #1C2320; }
.subtitle { font-size: 12px; color: #5C6963; }
.search {
  min-height: 42px;
  border-radius: 12px;
  background: #FFFFFF;
  color: #1C2320;
  border: 1px solid rgba(31,98,84,0.09);
  padding: 0 12px;
}
.app-tile {
  background: #FFFFFF;
  border: 1px solid rgba(31,98,84,0.09);
  border-radius: 12px;
  padding: 10px;
  color: #1C2320;
}
.app-tile:hover {
  background: #F4F8F5;
  border-color: rgba(47,138,125,0.22);
  box-shadow: 0 8px 20px rgba(30,70,58,0.07);
}
.app-name { font-size: 11px; font-weight: 700; color: #1C2320; }
.quick-button {
  border-radius: 12px;
  padding: 9px 12px;
  background: #FFFFFF;
  color: #1C2320;
}
.quick-button:hover { background: #EAF3EF; }
'''

def read_desktop_file(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding='utf-8')
    except Exception:
        return None
    if not parser.has_section('Desktop Entry'):
        return None
    entry = parser['Desktop Entry']
    if entry.get('Type', 'Application') != 'Application':
        return None
    if entry.get('NoDisplay', '').lower() == 'true' or entry.get('Hidden', '').lower() == 'true':
        return None
    name = entry.get('Name[zh_CN]') or entry.get('Name') or os.path.basename(path)
    exec_cmd = entry.get('Exec', '')
    if not exec_cmd:
        return None
    return {
        'name': name,
        'comment': entry.get('Comment[zh_CN]') or entry.get('Comment') or '',
        'icon': entry.get('Icon') or 'application-x-executable',
        'exec': exec_cmd,
        'path': path,
        'categories': entry.get('Categories', '')
    }

def load_apps():
    seen = set()
    apps = []
    for directory in APP_DIRS:
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.desktop'):
                continue
            path = os.path.join(directory, filename)
            app = read_desktop_file(path)
            if app and app['name'] not in seen:
                seen.add(app['name'])
                apps.append(app)
    return sorted(apps, key=lambda item: item['name'].lower())

def responsive_window_size(preferred_width=760, preferred_height=520):
    screen = Gdk.Screen.get_default()
    if not screen:
        return preferred_width, preferred_height
    return (
        max(520, min(preferred_width, screen.get_width() - 64)),
        max(420, min(preferred_height, screen.get_height() - 80)),
    )

class AppLibrary(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Ming 应用库')
        window_width, window_height = responsive_window_size()
        self.set_default_size(window_width, window_height)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name('ming-app-library')
        self.apps = load_apps()
        self.filtered = self.apps

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.get_style_context().add_class('root')
        root.set_border_width(18)
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = Gtk.Label(label='Ming 应用库')
        title.set_halign(Gtk.Align.START)
        title.get_style_context().add_class('title')
        subtitle = Gtk.Label(label='搜索、打开、整理新安装应用')
        subtitle.set_halign(Gtk.Align.START)
        subtitle.get_style_context().add_class('subtitle')
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(subtitle, False, False, 0)
        header.pack_start(title_box, True, True, 0)

        for label, cmd in [('整理桌面', 'ming-helper organize-desktop'), ('Ming 文件', 'ming-files'), ('系统设置', 'ming-control-center')]:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class('quick-button')
            btn.connect('clicked', lambda _b, c=cmd: subprocess.Popen(c, shell=True))
            header.pack_start(btn, False, False, 0)
        root.pack_start(header, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.get_style_context().add_class('search')
        self.search.set_placeholder_text('输入应用名称，比如 微信、浏览器、文档')
        self.search.connect('search-changed', self.on_search)
        root.pack_start(self.search, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_max_children_per_line(5)
        self.flow.set_min_children_per_line(1)
        self.flow.set_row_spacing(12)
        self.flow.set_column_spacing(12)
        scroller.add(self.flow)
        root.pack_start(scroller, True, True, 0)
        self.render()

    def on_search(self, entry):
        q = entry.get_text().strip().lower()
        if not q:
            self.filtered = self.apps
        else:
            self.filtered = [a for a in self.apps if q in (a['name'] + ' ' + a['comment'] + ' ' + a['categories']).lower()]
        self.render()

    def render(self):
        for child in self.flow.get_children():
            self.flow.remove(child)
        for app in self.filtered:
            self.flow.add(self.make_tile(app))
        self.show_all()

    def make_tile(self, app):
        btn = Gtk.Button()
        btn.get_style_context().add_class('app-tile')
        btn.set_size_request(112, 102)
        btn.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        btn.connect('clicked', lambda _b: self.launch(app))
        btn.connect('button-press-event', lambda widget, event: self.show_app_menu(widget, event, app))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(app['icon'], Gtk.IconSize.DIALOG)
        label = Gtk.Label(label=app['name'])
        label.get_style_context().add_class('app-name')
        label.set_justify(Gtk.Justification.CENTER)
        label.set_line_wrap(True)
        label.set_max_width_chars(12)
        box.pack_start(image, False, False, 0)
        box.pack_start(label, False, False, 0)
        btn.add(box)
        return btn

    def show_app_menu(self, widget, event, app):
        if getattr(event, 'button', 0) != 3:
            return False
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label='打开')
        open_item.connect('activate', lambda _item: self.launch(app))
        desktop_item = Gtk.MenuItem(label='添加到桌面')
        desktop_item.connect('activate', lambda _item: subprocess.Popen(['ming-phone-desktop', '--add', app['path']]))
        folder_item = Gtk.MenuItem(label='添加到桌面文件夹')
        folder_item.connect('activate', lambda _item: subprocess.Popen(['ming-phone-desktop', '--add-to-folder', app['path']]))
        for item in (open_item, desktop_item, folder_item):
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def launch(self, app):
        broker = '/usr/local/bin/ming-launch'
        if not os.path.isfile(broker):
            return
        try:
            subprocess.Popen(
                [broker, '--desktop-file', app['path'], '--source', 'app-library'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return

class MingApp(Gtk.Application):
    def do_activate(self):
        AppLibrary(self).show_all()

if __name__ == '__main__':
    MingApp().run(sys.argv)
APPLIB
    chmod +x /usr/local/bin/ming-app-library

    cat > /usr/local/bin/ming-desktop-organizer << 'DESKORG'
#!/usr/bin/env bash
set -uo pipefail

desktop="${HOME}/Desktop"
apps_dir="${desktop}/应用"
system_dir="${desktop}/系统"
internet_dir="${desktop}/上网"
office_dir="${desktop}/办公"
media_dir="${desktop}/影音"
games_dir="${desktop}/游戏"
tools_dir="${desktop}/工具"
common_dir="${desktop}/常用"
state_dir="${HOME}/.config/ming-os"

mkdir -p "${apps_dir}" "${system_dir}" "${internet_dir}" "${office_dir}" "${media_dir}" "${games_dir}" "${tools_dir}" "${common_dir}" "${state_dir}" "${desktop}"

desktop_name() {
    local file="$1"
    awk -F= '
        /^\[Desktop Entry\]/{in_entry=1}
        in_entry && /^Name\[zh_CN\]=/{print $2; exit}
        in_entry && /^Name=/{print $2; exit}
    ' "${file}" 2>/dev/null | head -n1
}

desktop_categories() {
    awk -F= '/^Categories=/{print tolower($2); exit}' "$1" 2>/dev/null
}

target_for() {
    local file="$1"
    local cats name
    cats="$(desktop_categories "${file}")"
    name="$(desktop_name "${file}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${cats}" == *network* || "${name}" == *edge* || "${name}" == *firefox* || "${name}" == *微信* || "${name}" == *浏览器* ]]; then
        echo "${internet_dir}"
    elif [[ "${cats}" == *office* || "${name}" == *wps* || "${name}" == *文档* || "${name}" == *表格* ]]; then
        echo "${office_dir}"
    elif [[ "${cats}" == *audio* || "${cats}" == *video* || "${cats}" == *graphics* || "${name}" == *音乐* || "${name}" == *视频* ]]; then
        echo "${media_dir}"
    elif [[ "${cats}" == *game* || "${name}" == *游戏* ]]; then
        echo "${games_dir}"
    elif [[ "${name}" == *文件* || "${name}" == *磁盘* || "${name}" == *清理* || "${name}" == *安全* ]]; then
        echo "${tools_dir}"
    elif [[ "${cats}" == *settings* || "${cats}" == *system* || "${name}" == *设置* || "${name}" == *更新* || "${name}" == *终端* ]]; then
        echo "${system_dir}"
    else
        echo "${apps_dir}"
    fi
}

copy_launcher() {
    local src="$1"
    local dest_dir="$2"
    local display_name
    [[ -f "${src}" ]] || return 0
    display_name="$(desktop_name "${src}")"
    [[ -n "${display_name}" ]] || return 0
    display_name="${display_name//\//-}"
    cp -f "${src}" "${dest_dir}/${display_name}.desktop" 2>/dev/null || return 0
    chmod +x "${dest_dir}/${display_name}.desktop" 2>/dev/null || true
}

sync_apps() {
    for src in /usr/share/applications/*.desktop "${HOME}/.local/share/applications/"*.desktop; do
        [[ -f "${src}" ]] || continue
        grep -q '^NoDisplay=true' "${src}" 2>/dev/null && continue
        grep -q '^Hidden=true' "${src}" 2>/dev/null && continue
        case "$(basename "${src}")" in
            mimeinfo.cache|defaults.list) continue ;;
        esac
        copy_launcher "${src}" "$(target_for "${src}")"
    done
}

legacy_settings="${desktop}/Ming 设置.desktop"
legacy_common_settings="${common_dir}/Ming 设置.desktop"
legacy_settings_is_managed=false
if [[ -f "${legacy_settings}" ]] \
   && grep -qxF 'Exec=/usr/local/bin/ming-control-center' "${legacy_settings}" 2>/dev/null; then
    legacy_settings_is_managed=true
fi
if [[ "${legacy_settings_is_managed}" == true && -L "${legacy_common_settings}" ]]; then
    legacy_common_target="$(readlink -- "${legacy_common_settings}" 2>/dev/null || true)"
    if [[ "${legacy_common_target}" == "${legacy_settings}" ]]; then
        rm -f "${legacy_common_settings}" 2>/dev/null || true
    fi
fi
if [[ "${legacy_settings_is_managed}" == true ]]; then
    rm -f "${legacy_settings}" 2>/dev/null || true
fi
rm -f "${desktop}/Ming 应用库.desktop" "${desktop}/所有磁盘.desktop" 2>/dev/null || true

gio set "${apps_dir}" metadata::custom-icon-name application-x-executable 2>/dev/null || true
gio set "${system_dir}" metadata::custom-icon-name ming-control-center 2>/dev/null || true
gio set "${internet_dir}" metadata::custom-icon-name network-workgroup 2>/dev/null || true
gio set "${office_dir}" metadata::custom-icon-name x-office-document 2>/dev/null || true
gio set "${media_dir}" metadata::custom-icon-name multimedia-player 2>/dev/null || true
gio set "${games_dir}" metadata::custom-icon-name applications-games 2>/dev/null || true
gio set "${tools_dir}" metadata::custom-icon-name applications-utilities 2>/dev/null || true
gio set "${common_dir}" metadata::custom-icon-name emblem-favorite 2>/dev/null || true

if command -v ming-phone-desktop >/dev/null 2>&1; then
    timeout --foreground 8s ming-phone-desktop --sync >/tmp/ming-phone-desktop-sync.log 2>&1 || true
else
    sync_apps
fi
if [[ "${1:-}" == "--watch" ]]; then
    while true; do
        if command -v inotifywait >/dev/null 2>&1; then
            inotifywait -q -e close_write,create,move,delete /usr/share/applications "${HOME}/.local/share/applications" >/dev/null 2>&1 || sleep 5
        else
            sleep 20
        fi
        if command -v ming-phone-desktop >/dev/null 2>&1; then
            timeout --foreground 8s ming-phone-desktop --sync >/tmp/ming-phone-desktop-sync.log 2>&1 || true
        else
            sync_apps
        fi
        # 增量更新图标缓存（.desktop 变化后立即刷新，避免图标库全盘扫描）
        for icon_dir in /usr/share/icons/hicolor /usr/share/icons/Papirus /usr/share/icons/Adwaita; do
            if [[ -d "${icon_dir}" ]] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
                gtk-update-icon-cache -q -t -f "${icon_dir}" 2>/dev/null || true
            fi
        done
        xdg-desktop-menu forceupdate 2>/dev/null || true
    done
fi
DESKORG
    chmod +x /usr/local/bin/ming-desktop-organizer

    local phone_desktop_src="/tmp/ming-build/assets/ming-phone-desktop.py"
    if [[ ! -f "${phone_desktop_src}" ]]; then
        echo "[03_desktop][ERROR] missing ${phone_desktop_src}; cannot install Ming phone desktop"
        return 1
    fi
    install -m 0755 "${phone_desktop_src}" /usr/local/bin/ming-phone-desktop

    cat > /usr/local/bin/ming-helper << 'MINGHELPER'
#!/usr/bin/env bash
set -uo pipefail

title="Ming OS"

info() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --info --title="${title}" --text="$1" --width=420 2>/dev/null || true
    else
        notify-send "${title}" "$1" 2>/dev/null || true
    fi
}

warn() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --warning --title="${title}" --text="$1" --width=460 2>/dev/null || true
    else
        notify-send "${title}" "$1" 2>/dev/null || true
    fi
}

confirm() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --question --title="${title}" --text="$1" --ok-label="${2:-继续}" --cancel-label="取消" --width=460 2>/dev/null
    else
        return 0
    fi
}

run_progress() {
    local message="$1"
    shift
    if command -v zenity >/dev/null 2>&1; then
        (
            echo 15
            echo "# ${message}"
            "$@" >/tmp/ming-helper.log 2>&1
            echo $?
        ) | {
            read -r _pct || true
            read -r _msg || true
            (echo 15; echo "${_msg}"; sleep 1; echo 75; echo "# 正在收尾..."; sleep 1; echo 100) | zenity --progress --title="${title}" --text="${message}" --percentage=0 --auto-close --no-cancel --width=420 2>/dev/null || true
        }
        return 0
    fi
    "$@"
}

case "${1:-}" in
    update)
        exec /usr/local/bin/ming-update-gui check
        ;;
    install-wechat)
        if confirm "将下载安装腾讯官方 Linux 版微信。这个过程需要联网，可能需要几分钟。" "安装微信"; then
            if /usr/local/bin/ming-install-wechat >/tmp/ming-install-wechat.log 2>&1; then
                info "微信已安装。现在可以从 Dock 或开始菜单打开。"
            else
                warn "微信安装没有完成。请先确认网络可用，再点一次“安装微信”。"
            fi
        fi
        ;;
    wechat-light)
        MING_WECHAT_MODE=light /usr/local/bin/ming-wechat
        ;;
    wechat-web)
        exec /usr/local/bin/ming-wechat-web
        ;;
    clean-wechat)
        if confirm "将清理微信的大缓存文件，不会删除聊天账号。清理后微信下次启动可能稍慢。" "清理缓存"; then
            find "${HOME}/.cache" -maxdepth 4 \( -iname '*wechat*' -o -iname '*weixin*' \) -type f -size +8M -delete 2>/dev/null || true
            find "${HOME}/.config" -maxdepth 4 \( -iname '*wechat*' -o -iname '*weixin*' \) -type f -size +32M -delete 2>/dev/null || true
            info "微信缓存已清理。若微信仍然卡顿，可以点“网页版微信”。"
        fi
        ;;
    repair-display)
        rm -f "${HOME}/.config/ming-os/scale-done" 2>/dev/null || true
        /usr/local/bin/ming-scale >/tmp/ming-scale.log 2>&1 || true
        /usr/local/bin/ming-apply-appearance >/tmp/ming-appearance.log 2>&1 || true
        info "界面显示已重新整理：壁纸、主题、Dock 和缩放会在几秒内刷新。"
        ;;
    repair-store)
        if confirm "将修复或重新安装星火应用商店。这个过程需要联网。" "修复商店"; then
            spark_deb="/usr/share/ming-os/vendor/spark-store/spark-store_5.2.1.0_amd64.deb"
            if /usr/local/bin/ming-package-install-gui "${spark_deb}" >/tmp/ming-spark.log 2>&1; then
                info "星火应用商店已就绪。"
            else
                warn "商店修复没有完成。请先连接网络，再点一次“修复应用商店”。"
            fi
        fi
        ;;
    organize-desktop)
        /usr/local/bin/ming-desktop-organizer >/tmp/ming-desktop-organizer.log 2>&1 || true
        info "桌面已经刷新。\n\n新安装的软件会自动出现在 Ming 手机式桌面；把一个图标拖到另一个图标上，就会自动生成文件夹。"
        ;;
    disks)
        exec /usr/local/bin/ming-files
        ;;
    memory)
        mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
        profile="$(cat "${HOME}/.config/ming-os/memory-profile" 2>/dev/null || true)"
        info "本机内存约 ${mem_mb}MB。\n\nMing OS 会自动启用 zram、低内存桌面策略和微信省内存模式。\n\n${profile}"
        ;;
    install-os)
        exec /usr/local/bin/ming-live-installer.sh
        ;;
    *)
        info "请选择 Ming 设置中的按钮来完成操作。"
        ;;
esac
MINGHELPER
    chmod +x /usr/local/bin/ming-helper
    bash -n /usr/local/bin/ming-helper

    cat > "/home/${MING_USER}/.config/xfce4/terminal/terminalrc" << 'TERMINALRC'
[Configuration]
    FontName=Noto Sans Mono 11
MiscAlwaysShowTabs=FALSE
MiscBell=FALSE
MiscBordersDefault=TRUE
MiscCursorBlinks=FALSE
MiscCursorShape=TERMINAL_CURSOR_SHAPE_BLOCK
MiscDefaultGeometry=92x26
MiscMenubarDefault=FALSE
MiscToolbarDefault=FALSE
MiscConfirmClose=FALSE
ColorForeground=#D4F7F1
ColorBackground=#1D2421
ColorCursor=#9FE7D7
ColorSelection=#2FAE8F
ColorSelectionUseDefault=FALSE
BackgroundMode=TERMINAL_BACKGROUND_SOLID
BackgroundDarkness=1.00
BackgroundOpacity=1.00
ColorPalette=#1D2421;#D75D66;#58B88F;#D7B95A;#5A8CCF;#7B72B9;#4DB9B1;#D4F7F1;#51635C;#E9747C;#7ED6AD;#E5CB72;#78A9E5;#9C92D8;#72D3CC;#FFFFFF
TERMINALRC
    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/xfce4/terminal"

    cat > /usr/local/bin/ming-control-center << 'MINGCONTROL'
#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk
import subprocess
import sys

TASKS = [
    ('检查系统更新', 'ming-update-icon', '下载并安装新版本 Ming OS', 'ming-helper update'),
    ('修复界面显示', 'ming-display', '重新整理壁纸、缩放和 Dock', 'ming-helper repair-display'),
    ('连接网络', 'network-wireless', '打开无线和有线网络设置', 'nm-connection-editor'),
    ('安装微信', 'wechat', '下载腾讯官方 Linux 版微信', 'ming-helper install-wechat'),
    ('微信省内存启动', 'wechat', '适合 2GB 内存和群组较多账号', 'ming-helper wechat-light'),
    ('清理微信缓存', 'edit-clear', '释放微信缓存占用的磁盘和内存压力', 'ming-helper clean-wechat'),
    ('网页版微信', 'web-browser', '机器太卡时用浏览器聊天', 'ming-helper wechat-web'),
    ('打开应用商店', 'ming-app-store', '按需安装常用软件', 'spark-store'),
    ('修复应用商店', 'ming-app-store', '商店打不开时点这里', 'ming-helper repair-store'),
    ('应用库', 'ming-app-library', '搜索并打开所有已安装应用', 'ming-app-library'),
    ('整理桌面应用', 'application-x-executable', '把新软件自动放进桌面文件夹', 'ming-helper organize-desktop'),
    ('查看内存策略', 'utilities-system-monitor', '了解系统为低内存做了什么', 'ming-helper memory'),
    ('声音和音量', 'multimedia-volume-control', '调节扬声器、麦克风和输出设备', 'pavucontrol'),
    ('电源和电池', 'battery', '调节亮度、合盖和省电', 'xfce4-power-manager-settings'),
    ('外观主题', 'preferences-desktop-theme', '更换主题、字体和图标', 'xfce4-appearance-settings'),
    ('文件', 'files-icon', '打开文件和下载目录', 'ming-files'),
    ('小明 AI 助手', 'xiahai-xiaoming', '打开小明 AI 助手', '/opt/xiahai-xiaoming/xiahai-xiaoming'),
    ('高级设置', 'ming-settings', '窗口、Dock、动画和通知', 'ming-settings --page advanced'),
]

CSS = b'''
window {
  background: #F6F8F6;
}
.root {
  background: linear-gradient(135deg, #F8FAF8, #EEF6F2 55%, #DDEFE8);
  color: #1D2421;
}
.title {
  font-size: 26px;
  font-weight: 800;
  color: #1D2421;
}
.subtitle {
  font-size: 12px;
  color: #4F625A;
}
.tile {
  background: #FFFFFF;
  border: 1px solid rgba(31,98,84,0.13);
  border-radius: 10px;
  padding: 12px;
  color: #1D2421;
}
.tile:hover {
  background: #F4F8F5;
  border-color: rgba(47,174,143,0.36);
}
.tile:active {
  background: #EAF3EF;
}
.tile label {
  color: #1D2421;
  font-weight: 700;
}
.tile .desc {
  color: #66736D;
  font-size: 10px;
  font-weight: 400;
}
.footer {
  color: #66736D;
  font-size: 11px;
}
'''

class ControlCenter(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Ming Control Center')
        self.set_default_size(760, 520)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name('ming-control-center')

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.get_style_context().add_class('root')
        root.set_border_width(24)
        self.add(root)

        title = Gtk.Label(label='Ming 设置')
        title.set_halign(Gtk.Align.START)
        title.get_style_context().add_class('title')
        root.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(label='不用记命令，点按钮完成常见电脑维护')
        subtitle.set_halign(Gtk.Align.START)
        subtitle.get_style_context().add_class('subtitle')
        root.pack_start(subtitle, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.NONE)

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(12)
        flow.set_column_spacing(12)
        scroller.add(flow)
        root.pack_start(scroller, True, True, 0)

        for label, icon, desc, command in TASKS:
            flow.add(self.make_tile(label, icon, desc, command))

        footer = Gtk.Label(label='Ming OS 26.4.1 · Debian Trixie')
        footer.set_halign(Gtk.Align.END)
        footer.get_style_context().add_class('footer')
        root.pack_start(footer, False, False, 0)

    def make_tile(self, label, icon, desc, command):
        button = Gtk.Button()
        button.get_style_context().add_class('tile')
        button.set_size_request(164, 116)
        button.connect('clicked', lambda _button: self.launch(command))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
        text = Gtk.Label(label=label)
        text.set_justify(Gtk.Justification.CENTER)
        text.set_line_wrap(True)
        desc_label = Gtk.Label(label=desc)
        desc_label.get_style_context().add_class('desc')
        desc_label.set_justify(Gtk.Justification.CENTER)
        desc_label.set_line_wrap(True)
        desc_label.set_max_width_chars(18)
        box.pack_start(image, False, False, 0)
        box.pack_start(text, False, False, 0)
        box.pack_start(desc_label, False, False, 0)
        button.add(box)
        return button

    def launch(self, command):
        try:
            subprocess.Popen(command, shell=True)
        except Exception:
            pass

class App(Gtk.Application):
    def do_activate(self):
        window = ControlCenter(self)
        window.show_all()

if __name__ == '__main__':
    app = App()
    app.run(sys.argv)
MINGCONTROL
    chmod +x /usr/local/bin/ming-control-center

    cat > /usr/share/applications/ming-control-center.desktop << 'CONTROLAPP'
[Desktop Entry]
Name=Ming 设置
Name[zh_CN]=Ming 设置
Comment=Ming OS control center
Exec=/usr/local/bin/ming-control-center
Icon=ming-control-center
Terminal=false
Type=Application
Categories=Settings;System;
StartupNotify=true
CONTROLAPP

    cat > /usr/share/applications/ming-files.desktop << 'FILESAPP'
[Desktop Entry]
Name=Ming 文件
Name[zh_CN]=Ming 文件
Comment=Browse files in Ming OS
Exec=/usr/local/bin/ming-files
Icon=files-icon
Terminal=false
Type=Application
Categories=System;FileManager;
StartupNotify=true
FILESAPP

    cat > /usr/share/applications/ming-terminal.desktop << 'TERMAPP'
[Desktop Entry]
Name=Ming 终端
Name[zh_CN]=Ming 终端
Comment=Ming OS terminal
Exec=/usr/local/bin/ming-terminal
Icon=ming-terminal
Terminal=false
Type=Application
Categories=System;TerminalEmulator;
StartupNotify=true
TERMAPP

    cat > /usr/share/applications/ming-status-center.desktop << 'STATUSAPP'
[Desktop Entry]
Name=Ming 状态中心
Name[zh_CN]=Ming 状态中心
Comment=Network, sound, power, time, and session actions
Comment[zh_CN]=网络、声音、电源、时间和退出
Exec=/usr/local/bin/ming-status-center
Icon=ming-control-center
Terminal=false
Type=Application
Categories=Settings;System;Utility;
StartupNotify=true
STATUSAPP

    cat > /usr/share/applications/ming-app-library.desktop << 'APPLIBAPP'
[Desktop Entry]
Name=Ming 应用库
Name[zh_CN]=Ming 应用库
Comment=Search, open, and organize installed apps
Comment[zh_CN]=搜索、打开、整理已安装应用
Exec=/usr/local/bin/ming-app-library
Icon=ming-app-library
Terminal=false
Type=Application
Categories=Utility;System;
StartupNotify=true
NoDisplay=true
APPLIBAPP

    cp /usr/share/applications/ming-control-center.desktop "/home/${MING_USER}/.local/share/applications/"
    cp /usr/share/applications/ming-files.desktop "/home/${MING_USER}/.local/share/applications/"
    cp /usr/share/applications/ming-terminal.desktop "/home/${MING_USER}/.local/share/applications/"
    cp /usr/share/applications/ming-status-center.desktop "/home/${MING_USER}/.local/share/applications/"
    cp /usr/share/applications/ming-app-library.desktop "/home/${MING_USER}/.local/share/applications/"
    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.local/share/applications"
}

# ======================== WPS 兜底安装 ========================

ensure_wps_office() {
    mkdir -p "/home/${MING_USER}/Desktop" "/usr/share/applications"
    rm -f "/home/${MING_USER}/Desktop/wps-office.desktop" \
          /usr/share/applications/wps-office.desktop 2>/dev/null || true
    # WPS is optional in 26.4.1. Keep ming-install-wps.desktop in App Library,
    # but do not create a desktop or Dock launcher for it.
}

# ======================== Picom 用户级配置 ========================

configure_picom() {
    mkdir -p /home/${MING_USER}/.config/picom
    cat > /home/${MING_USER}/.config/picom/picom.conf << 'PICOMCFG'
# Ming OS 26.4.1 Picom 配置 - 老显卡/虚拟机稳定路径
# 普通应用窗口保持不透明；透明度只留给 Dock 与通知等独立界面。
backend = "glx";
vsync = false;
unredir-if-possible = false;
glx-no-stencil = true;
glx-no-rebind-pixmap = true;
use-damage = true;
xrender-sync-fence = true;

# ---- 低成本合成：关闭模糊，避免老显卡与 VirtualBox 黑边/闪烁 ----
blur-background = false;
blur-background-frame = false;
blur-background-fixed = false;
blur-background-exclude = [
  "class_g = 'Firefox'",
  "class_g = 'Chromium'",
  "class_g = 'Code'",
  "window_type = 'dock'",
  "window_type = 'desktop'",
  "_GTK_FRAME_EXTENTS@:c",
];

# ---- 阴影 ----
shadow = true;
shadow-radius = 16;
shadow-opacity = 0.30;
shadow-offset-x = -10;
shadow-offset-y = -10;
shadow-exclude = [
  "name = 'Notification'",
  "class_g = 'Conky'",
  "class_g ?= 'Notify-osd'",
  "class_g = 'Cairo-clock'",
  "class_g = 'Firefox'",
  "window_type = 'dock'",
  "window_type = 'desktop'",
];

# ---- 圆角窗口 ----
corner-radius = 12;
rounded-corners-exclude = [
  "class_g = 'Firefox'",
  "window_type = 'desktop'",
  "window_type = 'notification'",
];

# ---- 透明度 ----
inactive-opacity = 1.0;
active-opacity = 1.0;
frame-opacity = 1.0;
inactive-opacity-override = false;

# ---- 渐入渐出 ----
fading = true;
fade-in-step = 0.04;
fade-out-step = 0.04;
fade-delta = 5;

# ---- wintypes ----
wintypes:
{
  tooltip = { fade = true; shadow = true; opacity = 1.0; focus = true; };
  dock = { shadow = false; opacity = 0.92; };
  dnd = { shadow = false; };
  dropdown_menu = { shadow = true; opacity = 1.0; };
  popup_menu = { shadow = true; opacity = 1.0; };
  utility = { shadow = true; opacity = 1.0; };
  notification = { shadow = true; opacity = 1.0; };
};

detect-rounded-corners = true;
detect-client-opacity = true;
detect-transient = true;
detect-client-leader = true;
PICOMCFG

    # Fallback 配置 (老显卡 xrender, 无 blur, 无动画)
    mkdir -p /etc/xdg/picom
    cat > /etc/xdg/picom/picom-fallback.conf << 'PICOMFALLBACK'
backend = "xrender";
vsync = false;
unredir-if-possible = false;
use-damage = true;
shadow = false;
shadow-radius = 0;
shadow-opacity = 0;
shadow-offset-x = -4;
shadow-offset-y = -4;
shadow-exclude = [
  "class_g = 'Firefox'",
  "window_type = 'dock'",
  "window_type = 'desktop'",
];
fading = false;
corner-radius = 24;
rounded-corners-exclude = [
  "window_type = 'desktop'",
  "window_type = 'notification'",
];
inactive-opacity = 1.0;
active-opacity = 1.0;
frame-opacity = 1.0;
detect-rounded-corners = true;
detect-client-opacity = false;
detect-transient = true;
wintypes:
{
  dock = { shadow = false; opacity = 0.92; };
  normal = { shadow = false; opacity = 1.0; };
  dialog = { shadow = false; opacity = 1.0; };
  menu = { shadow = false; opacity = 1.0; };
  tooltip = { shadow = false; opacity = 1.0; };
  popup_menu = { shadow = false; opacity = 1.0; };
  dropdown_menu = { shadow = false; opacity = 1.0; };
  notification = { shadow = false; opacity = 1.0; };
};
PICOMFALLBACK

# 低内存配置 (2601-4200MB: GLX + 无 blur/动画/阴影)
    cat > /etc/xdg/picom/picom-lowmem.conf << 'PICOMLOWMEM'
backend = "glx";
vsync = true;
unredir-if-possible = false;
glx-no-stencil = true;
glx-no-rebind-pixmap = true;
use-damage = true;

# 不启用 blur（blur 是 GPU/内存消耗大户）
blur-background = false;

shadow = false;

# 圆角保留（纯 CPU 开销极低）
corner-radius = 10;
rounded-corners-exclude = [
  "window_type = 'desktop'",
];

fading = false;

inactive-opacity = 1.0;
active-opacity = 1.0;
frame-opacity = 1.0;

wintypes:
{
  dock = { shadow = false; opacity = 0.92; };
  normal = { shadow = false; opacity = 1.0; };
  dialog = { shadow = false; opacity = 1.0; };
  menu = { shadow = false; opacity = 1.0; };
  tooltip = { shadow = false; opacity = 1.0; };
  popup_menu = { shadow = false; opacity = 1.0; };
  dropdown_menu = { shadow = false; opacity = 1.0; };
  notification = { shadow = false; opacity = 1.0; };
};

detect-rounded-corners = true;
detect-client-opacity = false;
detect-transient = true;
PICOMLOWMEM

    cat > /usr/local/bin/ming-picom << 'MINGPICOM'
#!/usr/bin/env bash
set -u

log="/tmp/ming-picom.log"
policy_file="${XDG_RUNTIME_DIR:-/tmp}/ming-picom-policy"
main_conf="${HOME}/.config/picom/picom.conf"
fallback_conf="/etc/xdg/picom/picom-fallback.conf"
lowmem_conf="/etc/xdg/picom/picom-lowmem.conf"
config="${main_conf}"
reason="modern-gpu"
disabled_reason=""

mem_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
cmdline="$(cat /proc/cmdline 2>/dev/null || true)"
virt="$(systemd-detect-virt 2>/dev/null || true)"
gpu="$(LC_ALL=C lspci 2>/dev/null | grep -Ei 'vga|3d|display' | tr '\n' ' ' || true)"
renderer=""
if command -v glxinfo >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    renderer="$(glxinfo -B 2>/dev/null | awk -F: '/OpenGL renderer/ {print tolower($2); exit}' | sed 's/^ *//')"
fi

if [[ "${mem_mb}" -gt 0 && "${mem_mb}" -lt 2662 ]]; then
    # Below 2.6 GB, compositing costs more memory and bandwidth than it saves.
    disabled_reason="low-memory-${mem_mb}mb"
elif [[ "${cmdline}" == *nomodeset* || "${cmdline}" == *"i915.modeset=0"* || "${cmdline}" == *"radeon.modeset=0"* || "${cmdline}" == *"amdgpu.modeset=0"* ]]; then
    disabled_reason="safe-graphics-cmdline"
elif [[ ! -d /dev/dri ]]; then
    disabled_reason="no-dri"
elif [[ "${renderer}" == *svga3d* ]] \
    || echo "${gpu}" | grep -Eiq 'VMware.*SVGA|VirtualBox|QEMU' \
    || [[ "${virt}" == "oracle" || "${virt}" == "vbox" || "${virt}" == "vmware" || "${virt}" == "qemu" ]]; then
    # Virtual GPUs need compositing for Plank alpha/rounded corners, but not
    # animations, blur, or shadows. XRender keeps the Dock polished without the
    # flicker-prone GLX path that caused trouble on older VirtualBox sessions.
    config="${fallback_conf}"
    reason="virtual-machine-xrender"
elif [[ "${renderer}" == *llvmpipe* || "${renderer}" == *softpipe* ]]; then
    disabled_reason="software-renderer"
elif [[ "${mem_mb}" -gt 0 && "${mem_mb}" -lt 4200 ]]; then
    config="${lowmem_conf}"
    reason="balanced-low-memory-${mem_mb}mb"
elif echo "${gpu}" | grep -Eiq 'Intel.*(Core Processor|HD Graphics 2000|HD Graphics 3000|GMA|4 Series|Ironlake|Sandy Bridge)'; then
    disabled_reason="old-intel-gpu"
fi

if [[ -n "${disabled_reason}" ]]; then
    mkdir -p "$(dirname "${policy_file}")" 2>/dev/null || true
    printf 'compositor=disabled-by-policy\nreason=%s\n' "${disabled_reason}" >"${policy_file}"
    pkill -TERM -u "$(id -u)" -x picom >/dev/null 2>&1 || true
    printf '[%s] disabled-by-policy reason=%s mem_mb=%s renderer=%s gpu=%s\n' \
        "$(date '+%F %T')" "${disabled_reason}" "${mem_mb}" "${renderer:-unknown}" "${gpu:-unknown}" \
        >> "${log}" 2>/dev/null || true
    exit 0
fi
rm -f "${policy_file}" 2>/dev/null || true

if [[ ! -f "${config}" ]]; then
    config="${fallback_conf}"
    reason="${reason}-missing-main"
fi

{
    printf '[%s] backend config=%s reason=%s mem_mb=%s renderer=%s gpu=%s\n' \
        "$(date '+%F %T')" "${config}" "${reason}" "${mem_mb}" "${renderer:-unknown}" "${gpu:-unknown}"
} >> "${log}" 2>/dev/null || true

if ! command -v picom >/dev/null 2>&1; then
    printf '[%s] picom command missing\n' "$(date '+%F %T')" >> "${log}" 2>/dev/null || true
    exit 0
fi

pgrep -u "$(id -u)" -x picom >/dev/null 2>&1 && exit 0
exec picom --config "${config}" --log-level=warn
MINGPICOM
    chmod 0755 /usr/local/bin/ming-picom

    chown -R "${MING_USER}:${MING_USER}" /home/${MING_USER}/.config/picom
}

# ======================== 通知降噪 ========================

configure_notification_filter() {
    mkdir -p /home/${MING_USER}/.config/xfce4
    cat > "/home/${MING_USER}/.config/xfce4/xfce4-notifyd.xml" << 'NOTIFYCFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-notifyd" version="1.0">
  <property name="notify-location" type="uint" value="3"/>
  <property name="theme" type="string" value="Smoke"/>
  <property name="initial-opacity" type="double" value="1.0"/>
  <property name="expire-timeout" type="int" value="3"/>
  <property name="do-fadeout" type="bool" value="true"/>
  <property name="do-slideout" type="bool" value="true"/>
  <property name="log-only" type="bool" value="false"/>
  <property name="log-max-size" type="int" value="50"/>
  <property name="known-applications" type="array">
    <value type="string" value="network-manager-applet"/>
    <value type="string" value="xfce4-power-manager"/>
    <value type="string" value="pulseaudio"/>
    <value type="string" value="xiahai-xiaoming"/>
    <value type="string" value="xfce4-power-manager-settings"/>
  </property>
</channel>
NOTIFYCFG
}

# ======================== Thunar 右键菜单 ========================

configure_thunar_uca() {
    mkdir -p /home/${MING_USER}/.config/Thunar
    cat > "/home/${MING_USER}/.config/Thunar/uca.xml" << 'UCACFG'
<?xml version="1.0" encoding="UTF-8"?>
<actions>
<action>
    <icon>terminal</icon>
    <name>在此打开终端</name>
    <unique-id>1</unique-id>
    <command>exo-open --working-directory %f --launch TerminalEmulator</command>
    <description>在当前目录打开终端</description>
    <patterns>*</patterns>
    <directories/>
</action>
<action>
    <icon>accessories-text-editor</icon>
    <name>以管理员身份编辑</name>
    <unique-id>2</unique-id>
    <command>pkexec mousepad %f</command>
    <description>使用管理员权限编辑此文件</description>
    <patterns>*</patterns>
    <text-files/>
</action>
<action>
    <icon>folder</icon>
    <name>以管理员身份打开</name>
    <unique-id>3</unique-id>
    <command>pkexec thunar %f</command>
    <description>使用管理员权限打开此文件夹</description>
    <patterns>*</patterns>
    <directories/>
</action>
</actions>
UCACFG
}

# ======================== 桌面快捷方式 (极简) ========================

setup_desktop_shortcuts() {
    local desktop_dir="/home/${MING_USER}/Desktop"
    mkdir -p "${desktop_dir}"

    # 仅保留 3 个核心快捷方式
    cat > "${desktop_dir}/thunar.desktop" << THUNARDESKTOP
[Desktop Entry]
Name=文件
Name[zh_CN]=文件管理器
Comment=浏览文件和文件夹
Exec=thunar
Icon=system-file-manager
Terminal=false
Type=Application
Categories=System;FileManager;
StartupNotify=true
THUNARDESKTOP

    cat > "${desktop_dir}/ming-firefox.desktop" << FIREFOXDESKTOP
[Desktop Entry]
Name=浏览器
Name[zh_CN]=Firefox ESR 浏览器
Comment=浏览互联网
Exec=/usr/local/bin/ming-firefox
Icon=firefox-esr
Terminal=false
Type=Application
Categories=Network;WebBrowser;
StartupNotify=true
FIREFOXDESKTOP

    cat > "${desktop_dir}/ming-app-library.desktop" << APPLIBDESKTOP
[Desktop Entry]
Name=应用库
Name[zh_CN]=Ming 应用库
Comment=搜索、打开、整理已安装应用
Exec=/usr/local/bin/ming-app-library
Icon=ming-app-library
Terminal=false
Type=Application
Categories=Utility;System;
StartupNotify=true
APPLIBDESKTOP

    if [[ -s /usr/share/applications/xiahai-xiaoming.desktop ]]; then
        cp -f /usr/share/applications/xiahai-xiaoming.desktop "${desktop_dir}/xiahai-xiaoming.desktop"
    fi

    chown -R "${MING_USER}:${MING_USER}" "${desktop_dir}"
    chmod +x "${desktop_dir}"/*.desktop
}

# ======================== 发布说明与给网站 AI 的提示词 ========================

deploy_release_readme() {
    local doc_dir="/usr/share/doc/ming-os"
    mkdir -p "${doc_dir}"

    cat > "${doc_dir}/MING_OS_26.4.1_RELEASE_README.md" << 'RELEASEREADME'
# Ming OS 26.4.1 Release And Website Handoff

This document is the current website and AI handoff source for Ming OS. Use `26.4.1` as the public version. Do not point users to older 26.3.x or failed preview builds as the recommended release.

## Positioning

Ming OS 26.4.1 is a Debian 13 / Trixie based Chinese desktop system for older PCs and users who prefer buttons over terminal commands. It focuses on daily usability: local app installation, Spark Store launch behavior, Wi-Fi/Ethernet controls, audio, brightness, Bluetooth diagnostics, time sync, Live installation, and safe OTA upgrades from the 26.3 and 26.4 families.

This is the version to use when producing:

- website copy;
- download cards;
- release notes;
- OTA descriptions;
- social posts;
- screenshots and promo art captions.

## Public Links

- Official website: `https://scallion.uno`
- ISO download: `https://ming.sca-hub.cn/iso/ming-os-26.4.1-home-amd64.iso`
- ISO SHA256: see `SHA256SUMS` on the GitHub release page
- ISO size: see the current release asset metadata
- OTA check: `https://ming.sca-hub.cn/api/onion-update/check?version=26.4.0&channel=stable`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.4.1`

## Feature Summary

- Debian 13 / Trixie base.
- Rebuilt BIOS/UEFI boot chain with stable label `MING_OS_2641`.
- Preserves the 26.3-era boot reliability work while improving daily app, network, audio, brightness, Bluetooth, and OTA behavior.
- Live/Ventoy auto-login as `ming`.
- Ming wallpaper applies by default.
- Main Ming icons no longer use white-background AI PNG overrides.
- Ming Settings opens through a stable wrapper and writes readable logs.
- Installed system identity is repaired to Ming OS after installation.
- Desktop update button uses a clear GUI flow.
- Low-memory WeChat strategy: zram, earlyoom, cache cleanup, low-priority launcher, Web WeChat fallback.
- Android-like desktop app folders and automatic app visibility.
- `All Disks` entry combines common user folders and mounted disks to reduce C/D drive anxiety.
- HDD/SSD runtime tuning for schedulers, read-ahead, and dirty writeback.
- Dock-only desktop and Ming Settings reduce reliance on terminal commands.

## Product Narrative

The release should be described as:

- stable enough to hand to real users;
- visually branded enough to look like a system, not a theme pack;
- simple enough for users who do not want to remember command lines;
- pragmatic enough to stay useful on old hardware.

It should not be sold as a minimal Linux demo. It is a complete desktop with:

- boot repair;
- auto-login;
- optional WeChat installer and low-memory wrapper;
- update tooling;
- installer branding;
- desktop organization;
- storage simplification;
- and a control center for common actions.

## GitHub Download Note

The complete ISO is available on the official website. GitHub Release uses split assets:

```text
ming-os-26.4.1-home-amd64.iso.part01
ming-os-26.4.1-home-amd64.iso.part02
ming-os-26.4.1-home-amd64.iso.sha256
SHA256SUMS
```

Merge on Linux/macOS/WSL:

```bash
cat ming-os-26.4.1-home-amd64.iso.part01 ming-os-26.4.1-home-amd64.iso.part02 > ming-os-26.4.1-home-amd64.iso
sha256sum -c ming-os-26.4.1-home-amd64.iso.sha256
```

Merge on Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.4.1-home-amd64.iso.part01+ming-os-26.4.1-home-amd64.iso.part02 ming-os-26.4.1-home-amd64.iso
Get-FileHash ming-os-26.4.1-home-amd64.iso -Algorithm SHA256
```

## Prompt For Another AI Building The Scallion Product Page

You are a senior product web designer and frontend implementer. Build a Scallion website product page for `Ming OS 26.4.1`. The page should speak to ordinary Chinese users, older-PC users, and users who dislike terminal commands. Do not make it a generic Linux technical page.

Required links:

- ISO download: `https://ming.sca-hub.cn/iso/ming-os-26.4.1-home-amd64.iso`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.4.1`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- OTA check: `https://ming.sca-hub.cn/api/onion-update/check?version=26.4.0&channel=stable`

Page goals:

- Explain that Ming OS is a Debian 13 / Trixie based Chinese desktop system.
- Make `Ming OS 26.4.1` the visible product name in the first viewport.
- Highlight boot reliability, Live auto-login, optional WeChat/WPS installers, graphical update button, Ming Settings, Android-like app folders, All Disks, and the Ming-branded installer.
- Tell users clearly that 2GB RAM can run the OS, but optional WeChat/WPS installs may still be heavy.
- Provide a clear ISO download button, GitHub button, and OTA status area.

Suggested message hierarchy:

- first show the product name and official download path;
- then show the boot and desktop improvements;
- then show the user-facing buttons and shortcuts;
- then show the compatibility and low-memory guidance.

Suggested structure:

- Hero: title `Ming OS 26.4.1`; subtitle `给老旧电脑和中文用户的按钮化 Linux 桌面`; buttons `下载 ISO`, `查看 GitHub`, `检查 OTA`.
- Trust strip: SHA256, size, release date, OTA ready status.
- Three cards: `启动更稳`, `不用记命令`, `像手机一样整理应用`.
- Feature section: optional WeChat/WPS installers, Spark Store, Ming Settings, Ming App Library, All Disks, OTA updates, Ming installer.
- Download section: show official full ISO and GitHub split download instructions.
- Compatibility section: Rufus ISO/DD, Ventoy/Live, BIOS/UEFI, VirtualBox.

Design direction:

- Avoid stock Xfce, Linux Mint, or generic distro visuals.
- Use Ming OS identity: light paper-like surfaces, one restrained jade-green accent, clear icons, and dense but readable feature blocks.
- Keep language short and concrete. This page is for users who want buttons and confidence, not command-heavy troubleshooting.
- Do not show server passwords, SSH commands, internal deployment notes, or private operations details.

## Feature Language To Reuse

- boot more reliably;
- auto-login in Live mode;
- use buttons instead of commands;
- group apps like a phone launcher;
- keep files and disks easy to find;
- make updates clear and checkable;
- keep the system branded as Ming OS after installation;
- protect low-memory machines from heavy optional apps.
RELEASEREADME
}

# ======================== Xfce 会话自启动 ========================

configure_autostart() {
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"

    # Picom is owned by ming-session-healthcheck.  Keep a disabled compatibility
    # entry for the Settings backend, but never launch Picom directly at login.
    cat > "${autostart_dir}/picom.desktop" << PICOMAUTOSTART
[Desktop Entry]
Type=Application
Name=Picom Compositor
Comment=窗口合成器
Exec=/usr/bin/true
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
X-Ming-Managed-By=ming-session-healthcheck
PICOMAUTOSTART

    # NetworkManager 小程序
    cat > "${autostart_dir}/nm-applet.desktop" << NMAUTOSTART
[Desktop Entry]
Type=Application
Name=Network Manager
Comment=网络管理
Exec=nm-applet
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
NMAUTOSTART

    # 音量控制
    cat > "${autostart_dir}/volumeicon.desktop" << VOLAUTOSTART
[Desktop Entry]
Type=Application
Name=Volume Control
Comment=音量控制
Exec=volumeicon
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
VOLAUTOSTART

    # 电源管理器（笔记本电池图标）
    cat > "${autostart_dir}/xfce4-power-manager.desktop" << POWERAUTOSTART
[Desktop Entry]
Type=Application
Name=Power Manager
Comment=电源管理
Exec=xfce4-power-manager
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
POWERAUTOSTART

    cat > /usr/local/bin/ming-screensaver-after-oobe << 'SCREENSAVERHELPER'
#!/usr/bin/env bash
set -u
marker="${HOME}/.config/ming-os/oobe-account-done"
if grep -qwE "boot=live|live-config|ming.installer=1" /proc/cmdline 2>/dev/null \
    || [[ -f /.disk/info || -d /lib/live/mount/medium ]]; then
    exit 0
fi
for _try in $(seq 1 900); do
    if [[ -r "${marker}" ]] && grep -Fxq configured "${marker}" 2>/dev/null; then
        command -v xfconf-query >/dev/null 2>&1 && {
            xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s true 2>/dev/null || true
            xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s true 2>/dev/null || true
        }
        exec xfce4-screensaver
    fi
    sleep 1
done
exit 0
SCREENSAVERHELPER
    chmod 0755 /usr/local/bin/ming-screensaver-after-oobe

    cat > /usr/local/bin/ming-install-disable-locking << 'DISABLELOCK'
#!/usr/bin/env bash
set -u
if command -v xset >/dev/null 2>&1; then
    xset s off >/dev/null 2>&1 || true
    xset s noblank >/dev/null 2>&1 || true
    xset -dpms >/dev/null 2>&1 || true
fi
if command -v xfconf-query >/dev/null 2>&1; then
    xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/saver-activation/enabled -n -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Primary><Alt>t' -n -t string -s "ming-terminal" 2>/dev/null || true
    xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Primary><Alt>l' -n -t string -s "ming-lock" 2>/dev/null || true
fi
xfce4-screensaver-command --exit >/dev/null 2>&1 || true
pkill -TERM -u "$(id -u)" -x xfce4-screensaver >/dev/null 2>&1 || true
pkill -TERM -u "$(id -u)" -x light-locker >/dev/null 2>&1 || true
DISABLELOCK
    chmod 0755 /usr/local/bin/ming-install-disable-locking

    cat > "${autostart_dir}/xfce4-screensaver.desktop" << SCREENSAVERAUTO
[Desktop Entry]
Type=Application
Name=Xfce Screensaver
Comment=Ming OS lock screen and idle screensaver
Exec=/usr/local/bin/ming-screensaver-after-oobe
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
SCREENSAVERAUTO

    # Polkit 图形授权代理（让安装微信/修复商店/系统安装器等按钮能弹出授权窗口）
    cat > "${autostart_dir}/lxpolkit.desktop" << POLKITAUTO
[Desktop Entry]
Type=Application
Name=Polkit Authentication Agent
Comment=系统授权弹窗
Exec=lxpolkit
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
POLKITAUTO

    # 旧的首次配置入口已退役；只保留后面的账户向导与欢迎页。
    rm -f "${autostart_dir}/ming-first-run.desktop"

    # Calamares Live 安装器
    cat > "${autostart_dir}/calamares-live.desktop" << CALAMARES
[Desktop Entry]
Type=Application
Name=Install Ming OS
Name[zh_CN]=安装 Ming OS
Comment=系统安装程序
Exec=/usr/local/bin/ming-live-installer.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
CALAMARES

    rm -f \
        "/home/${MING_USER}/Desktop/calamares.desktop" \
        "/home/${MING_USER}/Desktop/install-debian.desktop" \
        "/home/${MING_USER}/Desktop/Install Debian.desktop" \
        "/home/${MING_USER}/Desktop/安装 Debian.desktop" \
        "/etc/skel/Desktop/calamares.desktop" \
        "/etc/skel/Desktop/install-debian.desktop" \
        "/etc/skel/Desktop/Install Debian.desktop" \
        "/etc/skel/Desktop/安装 Debian.desktop" 2>/dev/null || true

    # 安卓式桌面文件夹：登录后自动整理应用，并监听新安装应用。
    cat > "${autostart_dir}/ming-desktop-organizer.desktop" << DESKORGAUTO
[Desktop Entry]
Type=Application
Name=Ming Desktop Organizer
Comment=同步新安装应用到 Ming 手机式桌面
Exec=sh -c "sleep 5 && /usr/local/bin/ming-desktop-organizer --watch"
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
DESKORGAUTO

    # Compatibility filename retained for Settings/upgrade migrations.  The
    # old session watchdog is intentionally disabled; the unified coordinator
    # is the only long-lived desktop stack owner.
    cat > "${autostart_dir}/ming-phone-desktop.desktop" << PHONEDESKTOPAUTO
[Desktop Entry]
Type=Application
Name=Ming Phone Desktop
Comment=手机式桌面图标和拖拽文件夹
Exec=/usr/bin/true
Comment=Managed by ming-session-healthcheck; legacy ming-phone-desktop-watchdog --session is one-shot only
# Legacy image contract retained as comments: X-GNOME-Autostart-enabled=true Hidden=false.
Hidden=true
NoDisplay=true
X-GNOME-Autostart-enabled=false
X-Ming-Managed-By=ming-session-healthcheck
PHONEDESKTOPAUTO

    cat > "${autostart_dir}/ming-session-healthcheck.desktop" << SESSIONHEALTHAUTO
[Desktop Entry]
Type=Application
Name=Ming Session Health
Comment=统一启动并监测手机桌面、Dock 与合成器
Exec=/usr/local/bin/ming-session-healthcheck --session
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
X-Ming-Managed-Components=phone-desktop;plank;picom
SESSIONHEALTHAUTO

    chown -R "${MING_USER}:${MING_USER}" "${autostart_dir}"
}

# ======================== 首次启动欢迎引导 ========================

setup_welcome_wizard() {
    cat > /usr/local/bin/ming-welcome << 'WELCOMEPY'
#!/usr/bin/env python3
# Ming OS 26.4.1 首次启动欢迎引导

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import os
import subprocess
import sys
import time

WELCOME_DONE = os.path.expanduser('~/.config/ming-os/welcome-done')
OOBE_ACCOUNT_DONE = os.path.expanduser('~/.config/ming-os/oobe-account-done')
if os.path.exists(WELCOME_DONE):
    sys.exit(0)

def is_live_session():
    try:
        cmdline = open('/proc/cmdline', encoding='ascii').read()
    except OSError:
        cmdline = ''
    return (
        any(token in cmdline.split() for token in ('boot=live', 'live-config', 'ming.installer=1'))
        or os.path.exists('/.disk/info')
        or os.path.exists('/lib/live/mount/medium')
    )

if is_live_session():
    sys.exit(0)

def wait_for_account_oobe(timeout_seconds=900):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if open(OOBE_ACCOUNT_DONE, encoding='utf-8').readline().strip() == 'configured':
                return True
        except OSError:
            pass
        time.sleep(1)
    return False

class WelcomeWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        Gtk.ApplicationWindow.__init__(self, application=app, title='欢迎使用 Ming OS')
        self.set_default_size(600, 480)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_keep_above(True)

        self.steps = [
            self.step_welcome,
            self.step_wifi,
            self.step_done
        ]
        self.current = 0

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.main_box)

        css = b'''
        window { background-color: #F7F9F6; border-radius: 16px; }
        .welcome-title { font-size: 28px; font-weight: bold; color: #1D2421; margin-top: 30px; }
        .welcome-subtitle { font-size: 16px; color: #5C6963; margin-top: 10px; margin-bottom: 20px; }
        .big-button { font-size: 18px; padding: 16px 40px; border-radius: 12px;
                      background-color: #2F8A7D; color: white; border: none; min-height: 52px; }
        .big-button:hover { background-color: #28786E; }
        .big-button-alt { font-size: 18px; padding: 16px 40px; border-radius: 12px;
                          background-color: #FFFFFF; color: #1D2421; border: 1px solid rgba(31,98,84,0.14); min-height: 52px; }
        .step-label { font-size: 14px; color: #5C6963; margin-top: 16px; }
        .done-icon { font-size: 64px; color: #2F8A7D; }
        '''
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)

        self.show_step()

    def show_step(self):
        for child in self.main_box.get_children():
            self.main_box.remove(child)
        if self.current < len(self.steps):
            self.steps[self.current]()

    def step_welcome(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_halign(Gtk.Align.CENTER)

        title = Gtk.Label()
        title.set_markup('<span size="36000" weight="bold" foreground="#1D2421">欢迎使用 Ming OS</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="16000" foreground="#5C6963">让电脑更简单，让人人都会用</span>')
        subtitle.set_margin_bottom(30)

        btn = Gtk.Button(label='开始设置')
        btn.get_style_context().add_class('big-button')
        btn.set_size_request(240, 56)
        btn.connect('clicked', lambda w: self.next_step())
        btn.set_can_default(True)
        btn.set_receives_default(True)
        btn.connect('key-press-event', self.activate_button_on_key)
        GLib.idle_add(btn.grab_focus)

        dots = Gtk.Label()
        dots.set_markup('<span size="12000" foreground="#2F8A7D">● ○ ○</span>')
        dots.set_margin_top(24)

        vbox.pack_start(title, False, False, 0)
        vbox.pack_start(subtitle, False, False, 0)
        vbox.pack_start(btn, False, False, 0)
        vbox.pack_start(dots, False, False, 0)
        self.main_box.pack_start(vbox, True, True, 0)
        self.show_all()

    def step_wifi(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_halign(Gtk.Align.CENTER)

        title = Gtk.Label()
        title.set_markup('<span size="24000" weight="bold" foreground="#1D2421">连接到网络</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="14000" foreground="#5C6963">Wi-Fi 可以让您上网、更新系统和下载应用</span>')
        subtitle.set_margin_bottom(20)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        btn_box.set_halign(Gtk.Align.CENTER)

        wifi_btn = Gtk.Button(label='连接 Wi-Fi')
        wifi_btn.get_style_context().add_class('big-button')
        wifi_btn.set_size_request(280, 56)
        wifi_btn.connect('clicked', lambda w: self.open_wifi())
        wifi_btn.set_can_default(True)
        wifi_btn.set_receives_default(True)
        wifi_btn.connect('key-press-event', self.activate_button_on_key)
        GLib.idle_add(wifi_btn.grab_focus)

        skip_btn = Gtk.Button(label='跳过，稍后设置')
        skip_btn.get_style_context().add_class('big-button-alt')
        skip_btn.set_size_request(280, 52)
        skip_btn.connect('clicked', lambda w: self.next_step())

        dots = Gtk.Label()
        dots.set_markup('<span size="12000" foreground="#2F8A7D">○ ● ○</span>')
        dots.set_margin_top(24)

        btn_box.pack_start(wifi_btn, False, False, 0)
        btn_box.pack_start(skip_btn, False, False, 0)
        vbox.pack_start(title, False, False, 0)
        vbox.pack_start(subtitle, False, False, 0)
        vbox.pack_start(btn_box, False, False, 0)
        vbox.pack_start(dots, False, False, 0)
        self.main_box.pack_start(vbox, True, True, 0)
        self.show_all()

    def step_done(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Label()
        icon.set_markup('<span size="48000" foreground="#2F8A7D">•</span>')
        icon.set_margin_bottom(10)

        title = Gtk.Label()
        title.set_markup('<span size="28000" weight="bold" foreground="#1D2421">一切就绪</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="14000" foreground="#5C6963">您可以随时在底部 Dock 找到常用应用</span>')
        subtitle.set_margin_bottom(20)

        btn = Gtk.Button(label='开始使用 Ming OS')
        btn.get_style_context().add_class('big-button')
        btn.set_size_request(300, 56)
        btn.connect('clicked', lambda w: self.finish())
        btn.set_can_default(True)
        btn.set_receives_default(True)
        btn.connect('key-press-event', self.activate_button_on_key)
        GLib.idle_add(btn.grab_focus)

        dots = Gtk.Label()
        dots.set_markup('<span size="12000" foreground="#1FA89E">○ ○ ●</span>')
        dots.set_margin_top(24)

        vbox.pack_start(icon, False, False, 0)
        vbox.pack_start(title, False, False, 0)
        vbox.pack_start(subtitle, False, False, 0)
        vbox.pack_start(btn, False, False, 0)
        vbox.pack_start(dots, False, False, 0)
        self.main_box.pack_start(vbox, True, True, 0)
        self.show_all()

    def next_step(self):
        self.current += 1
        self.show_step()

    @staticmethod
    def activate_button_on_key(button, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            button.clicked()
            return True
        return False

    def open_wifi(self):
        try:
            subprocess.Popen(['nm-connection-editor'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            pass
        self.next_step()

    def finish(self):
        os.makedirs(os.path.dirname(WELCOME_DONE), exist_ok=True)
        with open(WELCOME_DONE, 'w') as f:
            f.write('done')
        self.destroy()

class WelcomeApp(Gtk.Application):
    def __init__(self):
        Gtk.Application.__init__(self)
    def do_activate(self):
        if not wait_for_account_oobe():
            return
        win = WelcomeWindow(self)
        win.show_all()
        win.present()
        win.connect('destroy', lambda w: self.quit())
    def do_startup(self):
        Gtk.Application.do_startup(self)

if __name__ == '__main__':
    app = WelcomeApp()
    app.run()
WELCOMEPY

    chmod +x /usr/local/bin/ming-welcome

    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/ming-welcome.desktop" << WELCOMEAUTO
[Desktop Entry]
Type=Application
Name=Ming OS Welcome
Comment=Ming OS 首次启动引导
Exec=/usr/local/bin/ming-welcome
X-GNOME-Autostart-enabled=true
WELCOMEAUTO
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/autostart/ming-welcome.desktop"
}

# ======================== 首次开机账户向导 (OOBE) ========================
# 首次启动必须建立本机管理员密码。LightDM 仍保持自动登录，密码只用于
# Polkit 管理操作和锁屏认证；root 账户保持锁定。
setup_account_oobe() {
    cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'
#!/usr/bin/env bash
# Ming OS 首次开机账户向导 (OOBE)
set -uo pipefail

MARKER="${HOME}/.config/ming-os/oobe-account-done"

# 仅在已安装系统的首次开机运行；Live/安装器会话中不弹出（那里只跑 Calamares）
if grep -qwE "boot=live|live-config|ming.installer=1" /proc/cmdline 2>/dev/null \
   || [ -f /.disk/info ] || [ -d /lib/live/mount/medium ]; then
    exit 0
fi

if [[ -f "${MARKER}" ]] && [[ "$(head -n 1 "${MARKER}" 2>/dev/null || true)" == "configured" ]]; then
    exit 0
fi

log_oobe_event() {
    local event="$1"
    local detail="${2:-}"
    local log_dir="${HOME}/.local/state/ming-os"
    mkdir -p "${log_dir}"
    python3 - "${log_dir}/oobe-account.jsonl" "${event}" "${detail}" <<'PY' 2>/dev/null || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
path, event, detail = sys.argv[1:]
record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event}
if detail:
    record["detail"] = detail[:300]
with Path(path).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
PY
}

# Zenity/yad can leave a decorated placeholder behind on old XRender
# sessions after the password dialog closes.  Close only windows whose title
# belongs to this OOBE flow; never terminate unrelated user processes.
close_stale_oobe_windows() {
    command -v wmctrl >/dev/null 2>&1 || return 0
    while IFS= read -r window_id; do
        [[ -n "${window_id}" ]] || continue
        title="$(wmctrl -l 2>/dev/null | awk -v id="${window_id}" '$1 == id {for (i=4; i<=NF; i++) printf "%s%s", $i, (i==NF ? "" : " ")}' || true)"
        case "${title}" in
            *设置账户*|*管理员初始化*|*无法完成*|*"Ming OS 管理员"*|*账户设置*)
                wmctrl -i -c "${window_id}" >/dev/null 2>&1 || true
                ;;
        esac
    done < <(wmctrl -lx 2>/dev/null | awk '{print $1}')
}

# Yad/Zenity inherit the geometry of the window restored by the previous
# LightDM/Xfce session.  Re-center only this OOBE's titled windows inside the
# current work area; this is bounded and does not move normal applications.
OOBE_CENTER_PID=""
OOBE_DIALOG_PID=""
center_oobe_dialogs() {
    command -v wmctrl >/dev/null 2>&1 || return 0
    command -v xrandr >/dev/null 2>&1 || return 0
    for _center_try in $(seq 1 120); do
        local screen_width screen_height
        read -r screen_width screen_height < <(
            xrandr --current 2>/dev/null |
                sed -n 's/.*current \([0-9][0-9]*\) x \([0-9][0-9]*\).*/\1 \2/p' | head -n1
        )
        [[ "${screen_width:-}" =~ ^[0-9]+$ && "${screen_height:-}" =~ ^[0-9]+$ ]] || {
            screen_width=1024
            screen_height=768
        }
        while read -r window_id _desktop window_x window_y window_width window_height _host title; do
            [[ "${window_id:-}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue
            case "${title:-}" in
                *设置账户*|*管理员初始化*|*无法完成*|*Ming\ OS\ 管理员*|*账户设置*|*完成*) ;;
                *) continue ;;
            esac
            [[ "${window_width:-}" =~ ^[0-9]+$ && "${window_height:-}" =~ ^[0-9]+$ ]] || continue
            local max_x=$((screen_width - window_width))
            local max_y=$((screen_height - window_height))
            (( max_x < 0 )) && max_x=0
            (( max_y < 0 )) && max_y=0
            local target_x=$((max_x / 2))
            local target_y=$((max_y / 2))
            wmctrl -i -r "${window_id}" -e "0,${target_x},${target_y},-1,-1" >/dev/null 2>&1 || true
        done < <(wmctrl -lG 2>/dev/null)
        sleep 0.25
    done
}

stop_oobe_center() {
    if [[ -n "${OOBE_CENTER_PID:-}" ]]; then
        kill "${OOBE_CENTER_PID}" >/dev/null 2>&1 || true
        wait "${OOBE_CENTER_PID}" >/dev/null 2>&1 || true
        OOBE_CENTER_PID=""
    fi
}

# The old literal `trap close_stale_oobe_windows EXIT` was intentionally
# extended with the bounded centering worker below.
trap 'stop_oobe_center; close_stale_oobe_windows' EXIT

# 等桌面与授权代理就绪
sleep 4

CUR_USER="$(whoami)"

dialog() {
    center_oobe_dialogs >/dev/null 2>&1 &
    OOBE_CENTER_PID=$!
    local rc=0
    if command -v yad >/dev/null 2>&1; then
        # Use Yad's native centering before the bounded wmctrl correction.  A
        # background child lets the centering worker observe the real window,
        # while wait preserves the dialog output and exit status.
        yad --center "$@" &
        OOBE_DIALOG_PID=$!
        wait "${OOBE_DIALOG_PID}" || rc=$?
    else
        zenity --center "$@" &
        OOBE_DIALOG_PID=$!
        wait "${OOBE_DIALOG_PID}" || rc=$?
    fi
    OOBE_DIALOG_PID=""
    stop_oobe_center
    return "${rc}"
}

repair_desktop_session() {
    if command -v ming-desktop-healthcheck >/dev/null 2>&1; then
        /usr/local/bin/ming-desktop-healthcheck --repair >/dev/null 2>&1 || true
    fi
}

# 始终先确保免密自动登录已就位（双保险，独立于用户选择）
ensure_autologin() {
    # Groups and LightDM autologin are installed by the base module.
    return 0
}

# OOBE only collects the display name. The privileged helper owns the visible
# password prompts so an untrusted session process cannot submit a password.
# Keep retries finite: a failed helper must never exec this script recursively.
OOBE_MAX_ATTEMPTS=3
oobe_attempt=0
while (( oobe_attempt < OOBE_MAX_ATTEMPTS )); do
    oobe_attempt=$((oobe_attempt + 1))
    FORM=$(dialog --form --title="设置账户" \
        --text="下一步将由系统安全窗口创建本机管理员密码。开机仍会自动进入桌面；安装软件或更改保护设置时需要输入此密码。" \
        --field="显示名称:" \
        --width=440 \
        "Ming 用户" 2>/dev/null)
    FRC=$?
    if [[ "${FRC}" != "0" ]]; then
        log_oobe_event "retry" "account form cancelled (attempt ${oobe_attempt}/${OOBE_MAX_ATTEMPTS})"
        continue
    fi

    FULLNAME=$(echo "${FORM}" | cut -d'|' -f1)
    ensure_autologin

    # 首次授权由一次性 bootstrap 完成。密码只在特权 helper 的可见窗口输入。
    bootstrap_output=""
    if ! bootstrap_output="$(DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-${HOME}/.Xauthority}" pkexec /usr/local/sbin/ming-admin-bootstrap --user "${CUR_USER}" 2>&1)"; then
        log_oobe_event "bootstrap_failed" "${bootstrap_output:-admin bootstrap returned non-zero} (attempt ${oobe_attempt}/${OOBE_MAX_ATTEMPTS})"
        dialog --title="无法完成" --text="管理员初始化未成功，请重新设置。" \
            --width=400 --button="重新设置:0" 2>/dev/null || true
        continue
    fi
    status_output="$(/usr/local/sbin/ming-admin-bootstrap status --user "${CUR_USER}" --json 2>&1 || true)"
    if ! grep -Fq '"ready": true' <<<"${status_output}"; then
        dialog --title="无法完成" --text="管理员状态回读失败，请重新设置。" \
            --width=400 --button="重新设置:0" 2>/dev/null || true
        log_oobe_event "status_not_ready" "${status_output:-admin status was not ready} (attempt ${oobe_attempt}/${OOBE_MAX_ATTEMPTS})"
        continue
    fi
    if [[ -n "${FULLNAME}" ]]; then
        if ! fullname_output="$(pkexec chfn -f "${FULLNAME}" "${CUR_USER}" 2>&1)"; then
            log_oobe_event "fullname_update_failed" "${fullname_output:-chfn returned non-zero} (attempt ${oobe_attempt}/${OOBE_MAX_ATTEMPTS})"
        fi
    fi

    mkdir -p "$(dirname "${MARKER}")"
    echo "configured" > "${MARKER}"

    dialog --title="完成" \
        --text="本机管理员已建立。\n开机仍会自动进入桌面，管理操作会要求输入刚才的密码。" \
        --width=380 --button="开始使用:0" 2>/dev/null || true
    repair_desktop_session
    close_stale_oobe_windows
    exit 0
done

log_oobe_event "retry_exhausted" "administrator setup did not reach ready state"
exit 1
OOBEACCOUNT
    chmod +x /usr/local/bin/ming-oobe-account

    # 账户设置在欢迎页之前运行，避免两个窗口争抢焦点。
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/ming-oobe-account.desktop" << OOBEAUTO
[Desktop Entry]
Type=Application
Name=Ming OS Account Setup
Comment=首次开机账户向导
Exec=/usr/local/bin/ming-oobe-account
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
OOBEAUTO
    chown -R "${MING_USER}:${MING_USER}" "${autostart_dir}/ming-oobe-account.desktop"
}

# ======================== 精简右键菜单 ========================

configure_simplified_menus() {
    mkdir -p "/home/${MING_USER}/.config/Thunar"
    cat > "/home/${MING_USER}/.config/Thunar/uca.xml" << 'UCACFG'
<?xml version="1.0" encoding="UTF-8"?>
<actions>
<action>
    <icon>package-x-generic</icon>
    <name>安装 DEB 软件包</name>
    <submenu></submenu>
    <command>/usr/local/bin/ming-package-install-gui "%f"</command>
    <description>验证并安装本地 Debian 软件包</description>
    <range>*</range>
    <patterns>*.deb</patterns>
    <other-files/>
</action>
<action>
    <icon>application-x-executable</icon>
    <name>安装 AppImage</name>
    <submenu></submenu>
    <command>/usr/local/bin/ming-appimage-install-gui "%f"</command>
    <description>设置执行权限并创建用户启动器</description>
    <range>*</range>
    <patterns>*.AppImage;*.appimage</patterns>
    <other-files/>
</action>
<action>
    <icon>folder-new</icon>
    <name>新建文件夹</name>
    <submenu></submenu>
    <command>mkdir %f</command>
    <description>在当前目录创建新文件夹</description>
    <range></range>
    <patterns>*</patterns>
    <directories/>
</action>
<action>
    <icon>utilities-terminal</icon>
    <name>在此打开终端</name>
    <submenu></submenu>
    <command>exo-open --working-directory %f --launch TerminalEmulator</command>
    <description>在此目录打开终端</description>
    <range></range>
    <patterns>*</patterns>
    <directories/>
</action>
<action>
    <icon>accessories-text-editor</icon>
    <name>以管理员身份编辑</name>
    <submenu></submenu>
    <command>pkexec mousepad %f</command>
    <description>使用管理员权限编辑文本文件</description>
    <range>*</range>
    <patterns>*</patterns>
    <text-files/>
</action>
<action>
    <icon>folder</icon>
    <name>以管理员身份打开</name>
    <submenu></submenu>
    <command>pkexec thunar %f</command>
    <description>使用管理员权限打开文件夹</description>
    <range>*</range>
    <patterns>*</patterns>
    <directories/>
</action>
<action>
    <icon>document-properties</icon>
    <name>属性</name>
    <submenu></submenu>
    <command>thunar --bulk-rename %F</command>
    <description>查看文件/文件夹属性</description>
    <range>*</range>
    <patterns>*</patterns>
    <directories/>
    <audio-files/>
    <image-files/>
    <other-files/>
    <text-files/>
    <video-files/>
</action>
</actions>
UCACFG
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/Thunar/uca.xml"

    # 桌面采用安卓式文件夹分组，不再清空应用入口。
    runuser -u "${MING_USER}" -- /usr/local/bin/ming-desktop-organizer >/tmp/ming-desktop-organizer.log 2>&1 || true
}

# ======================== Live 安装器脚本 ========================

deploy_live_installer() {
    local verifier_source=/tmp/ming-build/assets/ming-installer-verify.py
    local install_mode_source=/tmp/ming-build/assets/ming-install-mode.py
    local receipt_module=/usr/lib/x86_64-linux-gnu/calamares/modules/ming-installer-target-receipt
    if [[ ! -s "${verifier_source}" || ! -s "${install_mode_source}" ]]; then
        echo "ERROR: missing installer verification asset" >&2
        return 1
    fi
    install -d -m 0755 /usr/local/sbin "${receipt_module}" /etc/calamares/modules
    install -m 0755 "${verifier_source}" /usr/local/sbin/ming-installer-verify
    install -m 0755 "${install_mode_source}" /usr/local/sbin/ming-install-mode
    cat > "${receipt_module}/module.desc" << 'TARGETRECEIPTDESC'
---
type: "job"
name: "ming-installer-target-receipt"
interface: "python"
script: "main.py"
TARGETRECEIPTDESC
    cat > "${receipt_module}/main.py" << 'TARGETRECEIPTPY'
#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import pathlib

import libcalamares


VERIFIER_PATH = pathlib.Path("/usr/local/sbin/ming-installer-verify")
LOADER = importlib.machinery.SourceFileLoader("ming_installer_verify", str(VERIFIER_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
VERIFIER = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(VERIFIER)


def run():
    root_mount_point = libcalamares.globalstorage.value("rootMountPoint")
    try:
        VERIFIER.capture_target_receipt(root_mount_point)
    except VERIFIER.TargetReceiptError as exc:
        return "Ming installer target receipt failed", str(exc)
    return None
TARGETRECEIPTPY
    chmod 0644 "${receipt_module}/main.py"

    cat > /etc/calamares/modules/ming-installer-target-receipt.conf << 'TARGETRECEIPTCONF'
---
TARGETRECEIPTCONF
cat > /etc/calamares/modules/ming-installer-target-receipt-reset.conf << 'TARGETRECEIPTRESETCONF'
---
dontChroot: true
timeout: 10
script:
  - "/usr/local/sbin/ming-installer-verify receipt --begin-attempt"
TARGETRECEIPTRESETCONF
cat > /etc/calamares/modules/ming-fix-partition-types.conf << 'MINGFIXPARTTYPESCONF'
---
dontChroot: true
timeout: 45
script:
  - "/usr/local/sbin/ming-fix-partition-types"
MINGFIXPARTTYPESCONF
# ming-fix-partition-types enforces MING-BIOSBOOT:ef02, MING-ESP:ef00,
# MING-BOOT:8300, MING-ROOT-A:8300, MING-ROOT-B:8300 and MING-HOME:8300.
    cat > /etc/calamares/modules/ming-installed-desktop-gate.conf << 'INSTALLEDDESKTOPGATECONF'
---
dontChroot: true
timeout: 30
script:
  - "/usr/local/sbin/ming-installer-verify installed --receipt"
INSTALLEDDESKTOPGATECONF

    cat > /usr/local/sbin/ming-calamares-preflight << 'CALAMARESPREFLIGHT'
#!/usr/bin/env bash
set -u

LOG="/run/ming-installer/preflight.log"
install -d -m 0755 /run/ming-installer
: > "${LOG}"
chmod 0644 "${LOG}"

log() {
    printf '%s\n' "$*" >> "${LOG}"
}

log "date=$(date --iso-8601=seconds 2>/dev/null || date)"
log "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL >> "${LOG}" 2>&1 || true

export TZ=Asia/Shanghai
ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime 2>>"${LOG}" || true
printf 'Asia/Shanghai\n' > /etc/timezone 2>>"${LOG}" || true
timeout 5 timedatectl set-timezone Asia/Shanghai >> "${LOG}" 2>&1 || true

mkdir -p /etc/calamares/modules

    cat > /etc/calamares/settings.conf <<'SETTINGS'
---
modules-search: [ local, /usr/lib/x86_64-linux-gnu/calamares/modules, /usr/lib/calamares/modules ]
instances:
- id: ming-ota-preflight
  module: shellprocess
  config: ming-ota-preflight.conf
- id: ming-ota-target-guard
  module: ming-ota-target-guard
  config: ming-ota-target-guard.conf
- id: ming-installer-target-receipt
  module: ming-installer-target-receipt
  config: ming-installer-target-receipt.conf
- id: ming-fix-partition-types
  module: shellprocess
  config: ming-fix-partition-types.conf
- id: ming-installer-target-receipt-reset
  module: shellprocess
  config: ming-installer-target-receipt-reset.conf
- id: ming-identity
  module: shellprocess
  config: ming-identity.conf
- id: ming-installed-desktop-gate
  module: shellprocess
  config: ming-installed-desktop-gate.conf
- id: ming-bootloader
  module: shellprocess
  config: ming-bootloader.conf
branding: ming
prompt-install: false
oem-setup: false
disable-cancel: false
disable-cancel-during-exec: false
quit-at-end: false
dont-chroot: false
sequence:
# 一键安装：用户只需点"开始安装"，无需配置语言/键盘/用户/时区
- show:
  - partition
  - summary
- exec:
  - shellprocess@ming-ota-preflight
  - ming-ota-target-guard@ming-ota-target-guard
  - partition
  - shellprocess@ming-fix-partition-types
  - shellprocess@ming-installer-target-receipt-reset
  - mount
  - ming-installer-target-receipt@ming-installer-target-receipt
  - unpackfs
  - machineid
  - fstab
  - networkcfg
  - hwclock
  - initramfs
  - grubcfg
  - shellprocess@ming-identity
  - shellprocess@ming-installed-desktop-gate
  - shellprocess@ming-bootloader
  - umount
- show:
  - finished
SETTINGS

cat > /etc/calamares/modules/locale.conf <<'LOCALECONF'
---
region: "Asia"
zone: "Shanghai"
locale: "zh_CN.UTF-8"
useSystemTimezone: true
adjustLiveTimezone: true
LOCALECONF

cat > /etc/calamares/modules/keyboard.conf <<'KEYBOARDCONF'
---
model: "pc105"
layout: "us"
variant: ""
KEYBOARDCONF

cat > /etc/calamares/modules/localecfg.conf <<'LOCALECFGCONF'
---
localeConf:
  LANG: "zh_CN.UTF-8"
  LANGUAGE: "zh_CN:zh"
  LC_ALL: "zh_CN.UTF-8"
  LC_TIME: "zh_CN.UTF-8"
  LC_NUMERIC: "zh_CN.UTF-8"
  LC_MONETARY: "zh_CN.UTF-8"
  LC_PAPER: "zh_CN.UTF-8"
  LC_NAME: "zh_CN.UTF-8"
  LC_ADDRESS: "zh_CN.UTF-8"
  LC_TELEPHONE: "zh_CN.UTF-8"
  LC_MEASUREMENT: "zh_CN.UTF-8"
  LC_IDENTIFICATION: "zh_CN.UTF-8"
LOCALECFGCONF

cat > /etc/calamares/modules/users.conf <<'USERSCONF'
---
defaultGroups:
  - users
  - audio
  - video
  - render
  - plugdev
  - netdev
  - bluetooth
  - lp
  - scanner
sudoersGroup: sudo
autologinGroup: autologin
sudoersConfigureWithGroup: false
setRootPassword: false
doReusePassword: false
displayAutologin: true
doAutologin: true
presets:
  fullName:
    value: "Ming OS User"
    editable: false
  loginName:
    value: "user"
    editable: false
passwordRequirements:
  minLength: -1
  maxLength: -1
  libpwquality:
    - minlen=0
    - minclass=0
    - dictcheck=0
    - enforcing=0
allowWeakPasswords: true
allowWeakPasswordsDefault: true
user:
  shell: /bin/bash
  forbidden_names: [ root, nobody ]
  home_permissions: "o700"
hostname:
  location: EtcFile
  writeHostsFile: true
  template: "ming-os"
  forbidden_names: [ localhost ]
USERSCONF

cat > /etc/calamares/modules/ming-bootloader.conf <<'BOOTLOADERCONF'
---
dontChroot: true
timeout: 180
script:
  - "/usr/local/sbin/ming-install-bootloader"
BOOTLOADERCONF

cat > /etc/default/locale <<'DEFAULTLOCALE'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_ALL=zh_CN.UTF-8
DEFAULTLOCALE

cat > /etc/locale.conf <<'ETCLOCALE'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_ALL=zh_CN.UTF-8
ETCLOCALE

if [ -f /etc/locale.gen ]; then
    sed -i 's/^# *zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/; s/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen 2>>"${LOG}" || true
fi
update-locale LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh LC_ALL=zh_CN.UTF-8 >> "${LOG}" 2>&1 || true

squash=""
for candidate in \
    /run/live/medium/live/filesystem.squashfs \
    /lib/live/mount/medium/live/filesystem.squashfs \
    /lib/live/mount/medium/live/filesystem.squashfs \
    /cdrom/live/filesystem.squashfs; do
    if [ -s "${candidate}" ]; then
        squash="${candidate}"
        break
    fi
done

if [ -z "${squash}" ]; then
    squash=$(find /run/live /lib/live /cdrom /media /run/media \
        -path '*/live/filesystem.squashfs' \
        -type f -size +1M -print -quit 2>/dev/null || true)
fi

squash_link="/run/ming-installer/filesystem.squashfs"
mkdir -p /run/ming-installer
rm -f "${squash_link}"

if [ -z "${squash}" ]; then
    log "unpackfs_source=NOT_FOUND"
    log "ERROR: cannot find live/filesystem.squashfs"
    exit 2
fi

if ln -s "${squash}" "${squash_link}" 2>>"${LOG}"; then
    :
elif command -v mount >/dev/null 2>&1; then
    touch "${squash_link}" 2>>"${LOG}" || true
    mount --bind "${squash}" "${squash_link}" >> "${LOG}" 2>&1 || true
fi

if [ ! -s "${squash_link}" ]; then
    # squashfs 软链接创建失败，直接用真实路径写 unpackfs.conf，不中断安装
    log "WARN: cannot create ${squash_link}, using direct path ${squash}"
    final_source="${squash}"
else
    final_source="${squash_link}"
fi

cat > /etc/calamares/modules/unpackfs.conf <<UNPACKFSCONF
---
unpack:
  - source: "${final_source}"
    sourcefs: "squashfs"
    destination: ""
UNPACKFSCONF

# Re-apply the user-selected install mode after the static fallback files have
# been written and before verification.  A cancelled or invalid choice never
# reaches Calamares, and dual_boot_preserve is not checked as blank_ab.
if [ -s /run/ming-installer/install-mode.json ]; then
    /usr/local/sbin/ming-install-mode show \
        --state /run/ming-installer/install-mode.json >/dev/null 2>>"${LOG}" || {
        log "ERROR: selected install mode receipt is invalid"
        exit 31
    }
    mode="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mode"])' \
        /run/ming-installer/install-mode.json 2>>"${LOG}" || true)"
    case "${mode}" in
        blank_ab|dual_boot_preserve)
            /usr/local/sbin/ming-install-mode write --mode "${mode}" \
                --state /run/ming-installer/install-mode.json \
                --partition /etc/calamares/modules/partition.conf >>"${LOG}" 2>&1 || exit 31
            ;;
        *)
            log "ERROR: selected install mode value is invalid"
            exit 31
            ;;
    esac
fi

if ! /usr/local/sbin/ming-installer-verify live --source "${final_source}" >> "${LOG}" 2>&1; then
    log "ERROR: Live Calamares verification failed"
    exit 2
fi

log "unpackfs_source=${squash}"
log "unpackfs_stable_source=${squash_link}"
log "timezone=$(cat /etc/timezone 2>/dev/null || true)"
log "locale_conf=$(tr '\n' ';' </etc/calamares/modules/locale.conf 2>/dev/null || true)"
log "calamares_settings_sha256=$(sha256sum /etc/calamares/settings.conf 2>/dev/null | awk '{print $1}')"

# Fresh VirtualBox disks sometimes reach Calamares without a usable label.
# Only inspect completely blank non-removable disks; never touch a disk that
# already has partitions or a mounted filesystem. blank_ab is intentionally
# GPT-only and carries its own BIOS Boot Partition for BIOS GRUB.
for disk in /dev/sd? /dev/vd? /dev/nvme?n?; do
    [ -b "${disk}" ] || continue
    case "${disk}" in
        /dev/sr*|/dev/loop*|/dev/ram*) continue ;;
    esac
    if lsblk -nr -o TYPE "${disk}" 2>/dev/null | grep -q '^part$'; then
        continue
    fi
    if lsblk -nr -o MOUNTPOINT "${disk}" 2>/dev/null | grep -q '/'; then
        continue
    fi
    if command -v wipefs >/dev/null 2>&1; then
        wipefs -n "${disk}" >> "${LOG}" 2>&1 || true
    fi
done

exit 0
CALAMARESPREFLIGHT
    chmod +x /usr/local/sbin/ming-calamares-preflight

    # 静态兜底：确保无论 preflight 是否成功执行，
    # settings.conf/users.conf/partition.conf are the 26.4.1 safe-partition installer defaults.
    # 这一步由 03_desktop.sh 负责写入，resume_build 也会执行到这里。
    mkdir -p /etc/calamares/modules
    cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'
---
modules-search: [ local, /usr/lib/x86_64-linux-gnu/calamares/modules, /usr/lib/calamares/modules ]
instances:
- id: ming-ota-preflight
  module: shellprocess
  config: ming-ota-preflight.conf
- id: ming-ota-target-guard
  module: ming-ota-target-guard
  config: ming-ota-target-guard.conf
- id: ming-installer-target-receipt
  module: ming-installer-target-receipt
  config: ming-installer-target-receipt.conf
- id: ming-fix-partition-types
  module: shellprocess
  config: ming-fix-partition-types.conf
- id: ming-installer-target-receipt-reset
  module: shellprocess
  config: ming-installer-target-receipt-reset.conf
- id: ming-identity
  module: shellprocess
  config: ming-identity.conf
- id: ming-installed-desktop-gate
  module: shellprocess
  config: ming-installed-desktop-gate.conf
- id: ming-bootloader
  module: shellprocess
  config: ming-bootloader.conf
branding: ming
prompt-install: false
oem-setup: false
disable-cancel: false
disable-cancel-during-exec: false
quit-at-end: false
dont-chroot: false
sequence:
- show:
  - partition
  - summary
- exec:
  - shellprocess@ming-ota-preflight
  - ming-ota-target-guard@ming-ota-target-guard
  - partition
  - shellprocess@ming-fix-partition-types
  - shellprocess@ming-installer-target-receipt-reset
  - mount
  - ming-installer-target-receipt@ming-installer-target-receipt
  - unpackfs
  - machineid
  - fstab
  - networkcfg
  - hwclock
  - initramfs
  - grubcfg
  - shellprocess@ming-identity
  - shellprocess@ming-installed-desktop-gate
  - shellprocess@ming-bootloader
  - umount
- show:
  - finished
STATICCALASETTINGS

    cat > /etc/calamares/modules/partition.conf << 'STATICPARTCONF'
---
userSwapChoices:
  - none
  - small
  - file
drawNestedPartitions: false
alwaysShowPartitionLabels: true
defaultPartitionTableType: gpt
requiredPartitionTableType: gpt
defaultFileSystemType: "ext4"
availableFileSystemTypes:
  - "ext4"
  - "fat32"
initialPartitioningChoice: none
initialSwapChoice: none
# Ming A/B installation has its own explicit, non-encrypted slot layout.
# Disable the unused Calamares LUKS widget so initial auto-partitioning can
# enable Next immediately without requiring a checkbox round-trip.
enableLuksAutomatedPartitioning: false
partitionLayout:
  - name: "MING-BIOSBOOT"
    filesystem: "unformatted"
    noEncrypt: true
    type: "21686148-6449-6E6F-744E-656564454649"
    size: 8M
    minSize: 8M
  - name: "MING-ESP"
    filesystem: "fat32"
    noEncrypt: true
    mountPoint: "/boot/efi"
    type: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    size: 512M
    minSize: 300M
    flags:
      - esp
  - name: "MING-BOOT"
    filesystem: "ext4"
    noEncrypt: true
    mountPoint: "/boot"
    size: 1G
  - name: "MING-ROOT-A"
    filesystem: "ext4"
    mountPoint: "/"
    size: 35%
    minSize: 14G
  - name: "MING-ROOT-B"
    filesystem: "ext4"
    noEncrypt: true
    size: 35%
    minSize: 14G
  - name: "MING-HOME"
    filesystem: "ext4"
    mountPoint: "/home"
    size: 100%
    minSize: 8G
requiredStorage: 48
allowManualPartitioning: false
STATICPARTCONF

    cat > /etc/calamares/modules/users.conf << 'STATICUSERSCONF'
---
defaultGroups:
  - users
  - audio
  - video
  - render
  - plugdev
  - netdev
  - bluetooth
  - lp
  - scanner
sudoersGroup: sudo
autologinGroup: autologin
sudoersConfigureWithGroup: false
setRootPassword: false
doReusePassword: false
displayAutologin: true
doAutologin: true
presets:
  fullName:
    value: "Ming OS User"
    editable: false
  loginName:
    value: "user"
    editable: false
passwordRequirements:
  minLength: -1
  maxLength: -1
  libpwquality:
    - minlen=0
    - minclass=0
    - dictcheck=0
    - enforcing=0
allowWeakPasswords: true
allowWeakPasswordsDefault: true
user:
  shell: /bin/bash
  forbidden_names: [ root, nobody ]
  home_permissions: "o700"
hostname:
  location: EtcFile
  writeHostsFile: true
  template: "ming-os"
  forbidden_names: [ localhost ]
STATICUSERSCONF

    cat > /usr/local/sbin/ming-live-installer-root << 'LIVEINSTALLERROOT'
#!/usr/bin/env bash
set -euo pipefail

mode=""
display="${DISPLAY:-:0}"
xauthority="${XAUTHORITY:-}"
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --mode) mode="${2:-}"; shift 2 ;;
        --display) display="${2:-:0}"; shift 2 ;;
        --xauthority) xauthority="${2:-}"; shift 2 ;;
        *) echo "unknown ming-live-installer-root argument: $1" >&2; exit 2 ;;
    esac
done
[[ "${EUID}" -eq 0 ]] || {
    echo "ming-live-installer-root must be called through Polkit" >&2
    exit 10
}
case "${mode}" in
    blank_ab|dual_boot_preserve) ;;
    *) echo "an explicit Ming install mode is required" >&2; exit 11 ;;
esac
if ! grep -qw "boot=live" /proc/cmdline 2>/dev/null \
    && ! grep -qw "live-config" /proc/cmdline 2>/dev/null \
    && ! grep -qw "ming.installer=1" /proc/cmdline 2>/dev/null \
    && [[ ! -d /run/live/medium && ! -d /lib/live/mount/medium && ! -f /.disk/info ]]; then
    echo "refusing to run the Live installer helper outside a Live session" >&2
    exit 12
fi
install -d -m 0755 /run/ming-installer
/usr/local/sbin/ming-install-mode write --mode "${mode}" \
    --state /run/ming-installer/install-mode.json \
    --partition /etc/calamares/modules/partition.conf
/usr/local/sbin/ming-calamares-preflight
if [[ ! -d /etc/calamares/qml ]]; then
    if [[ -e /etc/calamares/qml && ! -L /etc/calamares/qml ]]; then
        echo "Ming installer QML path is occupied by a non-directory" >&2
        exit 13
    fi
    ln -sfn /usr/share/calamares/qml /etc/calamares/qml
fi
if [[ ! -d /etc/calamares/qml ]]; then
    echo "Ming installer QML modules are unavailable" >&2
    exit 13
fi
{
    echo "ming-live-installer-root mode=${mode}"
    echo "ming-live-installer-root settings=/etc/calamares/settings.conf"
    echo "ming-live-installer-root qml=/etc/calamares/qml"
    sed -n '/^sequence:/,/^- show:/p' /etc/calamares/settings.conf 2>/dev/null || true
} >> /run/ming-installer/preflight.log 2>&1
export DISPLAY="${display}"
if [[ -n "${xauthority}" ]]; then
    export XAUTHORITY="${xauthority}"
fi
export TZ=Asia/Shanghai LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh LC_ALL=zh_CN.UTF-8
exec calamares -d -c /etc/calamares
LIVEINSTALLERROOT
    chmod 0755 /usr/local/sbin/ming-live-installer-root

    cat > /usr/share/polkit-1/actions/org.ming.live.installer.policy << 'LIVEINSTALLERPOLICY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.ming.live.installer">
    <description>Start the Ming OS Live installer</description>
    <message>Authentication is required to start the Live installer.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/ming-live-installer-root</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
LIVEINSTALLERPOLICY

    cat > /usr/local/bin/ming-install-mode-chooser << 'INSTALLMODECHOOSER'
#!/usr/bin/env python3
import sys

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk

CSS = b'''
dialog {
  background: #f6faf8;
}
.mode-title {
  font-size: 21px;
  font-weight: 700;
  color: #17231f;
}
.mode-help {
  color: #4c5f58;
}
.mode-card {
  border-radius: 14px;
  padding: 14px;
  border: 1px solid rgba(47, 138, 125, 0.22);
  background: #FFFFFF;
}
.mode-card:focus,
.mode-card:hover {
  border-color: rgba(47, 138, 125, 0.66);
  background: #F4F8F5;
}
.mode-card-primary {
  border-color: rgba(47, 138, 125, 0.72);
}
.card-title {
  font-size: 16px;
  font-weight: 700;
  color: #14362f;
}
.card-body {
  color: #34453f;
}
'''


class InstallModeChooser(Gtk.Dialog):
    def __init__(self):
        super().__init__(title='选择安装模式')
        self.selected_mode = 'blank_ab'
        self.set_default_size(760, 360)
        self.set_resizable(False)
        self.set_modal(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.add_button('取消', Gtk.ResponseType.CANCEL)
        self.add_button('继续安装', Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect('key-press-event', self.on_key_press)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_border_width(18)
        self.get_content_area().add(root)

        title = Gtk.Label(label='安装 Ming OS 前，请先选择安装方式')
        title.set_xalign(0)
        title.get_style_context().add_class('mode-title')
        root.pack_start(title, False, False, 0)

        help_text = Gtk.Label(
            label='空白盘自动安装会创建完整 A/B 分区并支持大版本 OTA 回滚；保留双系统会保护另一个系统，但大版本 A/B OTA 会禁用。'
        )
        help_text.set_xalign(0)
        help_text.set_line_wrap(True)
        help_text.get_style_context().add_class('mode-help')
        root.pack_start(help_text, False, False, 0)

        cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.pack_start(cards, True, True, 0)
        self.blank_button = self.mode_button(
            'blank_ab',
            '空白盘自动安装（支持 A/B OTA）',
            '需要至少 48GB；会创建 MING-ESP、/boot、A/B root 和独立 /home，并支持自动回滚。'
        )
        self.dual_button = self.mode_button(
            'dual_boot_preserve',
            '保留双系统（禁用 major A/B OTA）',
            '手动选择空闲空间或目标分区；保留另一个系统，只允许签名 patch/minor 更新。'
        )
        cards.pack_start(self.blank_button, False, False, 0)
        cards.pack_start(self.dual_button, False, False, 0)
        self.show_all()
        self.blank_button.grab_focus()

    def mode_button(self, mode, title, body):
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_can_default(True)
        button.set_receives_default(True)
        button.get_style_context().add_class('mode-card')
        if mode == 'blank_ab':
            button.get_style_context().add_class('mode-card-primary')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.get_style_context().add_class('card-title')
        detail = Gtk.Label(label=body)
        detail.set_xalign(0)
        detail.set_line_wrap(True)
        detail.get_style_context().add_class('card-body')
        box.pack_start(label, False, False, 0)
        box.pack_start(detail, False, False, 0)
        button.add(box)
        button.connect('clicked', lambda *_args: self.choose(mode))
        button.connect(
            'key-press-event',
            lambda _button, event, selected_mode=mode:
                self.on_mode_button_key(selected_mode, event),
        )
        return button

    def choose(self, mode):
        self.selected_mode = mode
        self.response(Gtk.ResponseType.OK)

    def on_mode_button_key(self, mode, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.choose(mode)
            return True
        return False

    def on_key_press(self, _widget, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.response(Gtk.ResponseType.OK)
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True
        return False


dialog = InstallModeChooser()
response = dialog.run()
if response == Gtk.ResponseType.OK and dialog.selected_mode in ('blank_ab', 'dual_boot_preserve'):
    print(dialog.selected_mode)
    sys.exit(0)
sys.exit(1)
INSTALLMODECHOOSER
    chmod 0755 /usr/local/bin/ming-install-mode-chooser

    cat > /usr/local/bin/ming-calamares-launcher << 'CALAMARESLAUNCHER'
#!/usr/bin/env bash
set -e

export TZ=Asia/Shanghai
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8

mkdir -p /tmp/ming-installer
chmod 1777 /tmp/ming-installer 2>/dev/null || true
exec 9>/tmp/ming-installer/ming-calamares.lock
flock -n 9 || exit 0

selected_mode=""

is_live_or_installer() {
    grep -qw "boot=live" /proc/cmdline 2>/dev/null && return 0
    grep -qw "live-config" /proc/cmdline 2>/dev/null && return 0
    grep -qw "ming.installer=1" /proc/cmdline 2>/dev/null && return 0
    grep -qw "install" /proc/cmdline 2>/dev/null && return 0
    [ -d /run/live/medium ] && return 0
    [ -d /lib/live/mount/medium ] && return 0
    [ -f /.disk/info ] && return 0
    return 1
}

show_preflight_error() {
    local detail
    detail="$(tail -n 80 /run/ming-installer/preflight.log 2>/dev/null || true)"
    zenity --error --title="Ming OS installer preflight failed" --width=620 \
        --text="Could not prepare the installer unpack source or Beijing timezone defaults.\n\n${detail}\n\nLog: /run/ming-installer/preflight.log" \
        2>/dev/null || true
}

choose_install_mode() {
    # 选择安装模式：由专用 GTK 选择器处理鼠标、Enter 和 Space。
    local choice=""
    if [ ! -x /usr/local/bin/ming-install-mode-chooser ]; then
        zenity --error --title='安装器缺少组件' \
            --text='缺少安装方式选择器，安装程序不会启动。请重新构建或修复系统镜像。' \
            2>/dev/null || true
        return 1
    fi
    if ! choice="$(/usr/local/bin/ming-install-mode-chooser 2>>/tmp/ming-installer/mode-chooser.log)"; then
        zenity --warning --title='未选择安装方式' \
            --text='未选择安装方式，安装程序不会启动。请从“安装 Ming OS”再次打开并选择。' \
            2>/dev/null || true
        return 1
    fi
    case "${choice}" in
        blank_ab|dual_boot_preserve) ;;
        *)
            zenity --warning --title='未选择安装方式' \
                --text='未选择安装方式，安装程序不会启动。请从“安装 Ming OS”再次打开并选择。' \
                2>/dev/null || true
            return 1
            ;;
    esac
    selected_mode="${choice}"
    printf '%s\n' "${selected_mode}" > /tmp/ming-installer/selected-mode
    chmod 0644 /tmp/ming-installer/selected-mode 2>/dev/null || true
}

if ! is_live_or_installer; then
    zenity --info --title="Ming OS" --text="This is not a Live installer session. The installer does not need to run here." 2>/dev/null || true
    exit 0
fi

if ! choose_install_mode; then
    exit 2
fi

helper=(/usr/local/sbin/ming-live-installer-root
    --mode "${selected_mode}"
    --display "${DISPLAY:-:0}"
    --xauthority "${XAUTHORITY:-${HOME}/.Xauthority}")

# Calamares 3.3 emits its initial partition next-state signal before the
# PartitionViewStep connects to it.  Blank A/B therefore starts with no action
# selected, and the button would remain disabled without a real user click.
# Select the erase card only after the actual Calamares window exists.  This is
# a bounded, best-effort UI assist: if xdotool/window discovery is unavailable,
# the user can still click the card normally and the installer never proceeds
# without Calamares' own enabled-state checks.
auto_select_blank_ab() {
    [[ "${selected_mode}" == "blank_ab" ]] || return 0
    command -v wmctrl >/dev/null 2>&1 || return 0
    command -v xdotool >/dev/null 2>&1 || return 0
    local calamares_window geometry width height click_x click_y page_ready
    for _ in $(seq 1 240); do
        calamares_window="$({
            timeout --foreground 2s wmctrl -lx 2>/dev/null || true
        } | awk 'tolower($0) ~ /calamares/ { print $1; exit }')"
        if [[ -n "${calamares_window}" ]]; then
            page_ready=false
            # The top-level window exists during the slow requirements scan.
            # Wait for the partition page's own debug marker so the click is
            # never delivered to the transient spinner page.
            for _page_try in $(seq 1 240); do
                if grep -Eq 'No partitioning choice has been made yet|Updating partitioning preview widgets' \
                    /tmp/ming-installer/calamares.log 2>/dev/null; then
                    page_ready=true
                    break
                fi
                sleep 0.25
            done
            if [[ "${page_ready}" == "true" ]]; then
                sleep 2
            else
                # Direct launches may not have a debug log; give the page a
                # conservative fallback delay before using geometry.
                sleep 8
            fi
            geometry="$(xdotool getwindowgeometry --shell "${calamares_window}" 2>/dev/null || true)"
            width="$(printf '%s\n' "${geometry}" | awk -F= '$1 == "WIDTH" {print $2}')"
            height="$(printf '%s\n' "${geometry}" | awk -F= '$1 == "HEIGHT" {print $2}')"
            if [[ "${width}" =~ ^[0-9]+$ && "${height}" =~ ^[0-9]+$ && "${width}" -ge 800 ]]; then
                click_x=$((width * 205 / 1000))
                click_y=$((height * 110 / 1000))
                (( click_y < 70 )) && click_y=70
                (( click_y > 90 )) && click_y=90
                xdotool windowactivate --sync "${calamares_window}" >/dev/null 2>&1 || true
                xdotool mousemove --window "${calamares_window}" "${click_x}" "${click_y}" click 1 \
                    >/tmp/ming-installer/auto-select.log 2>&1 || true
                printf '[%s] selected blank_ab erase card at %sx%s\n' \
                    "$(date '+%F %T')" "${click_x}" "${click_y}" >>/tmp/ming-installer/auto-select.log
                return 0
            fi
        fi
        sleep 0.25
    done
    return 0
}

auto_select_pid=""
if [[ "${selected_mode}" == "blank_ab" ]]; then
    auto_select_blank_ab &
    auto_select_pid="$!"
fi
if command -v xhost >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    xhost +SI:localuser:root >/tmp/ming-installer/xhost.log 2>&1 || true
fi
launch_status=0
if [ "$(id -u)" -eq 0 ]; then
    "${helper[@]}" || launch_status=$?
elif ! command -v pkexec >/dev/null 2>&1; then
    launch_status=127
else
    pkexec "${helper[@]}" || launch_status=$?
fi
if [[ -n "${auto_select_pid}" ]]; then
    kill "${auto_select_pid}" 2>/dev/null || true
    wait "${auto_select_pid}" 2>/dev/null || true
fi
if [[ "${launch_status}" -ne 0 ]]; then
    show_preflight_error
    exit "${launch_status}"
fi
exit 0
CALAMARESLAUNCHER
    chmod +x /usr/local/bin/ming-calamares-launcher

    cat > /usr/local/bin/ming-live-installer.sh << 'LIVEINSTALLER'
#!/usr/bin/env bash
set -e

is_live_environment() {
    grep -q "boot=live" /proc/cmdline 2>/dev/null && return 0
    grep -q "live-config" /proc/cmdline 2>/dev/null && return 0
    [ -d /lib/live/mount/medium ] && return 0
    [ -f /.disk/info ] && return 0
    [ -f /lib/live/boot/boot.sh ] && return 0
    return 1
}

# Ming OS is an installer-only image. When booted with ming.installer=1 the
# session launches Calamares immediately and full-screen; the live desktop is
# only a thin host for the installer, never a destination of its own.
is_installer_boot() {
    grep -qw "ming.installer=1" /proc/cmdline 2>/dev/null && return 0
    grep -qw "install" /proc/cmdline 2>/dev/null && return 0
    return 1
}

prepare_installer_disks() {
    mkdir -p /tmp/ming-installer
    chmod 1777 /tmp/ming-installer 2>/dev/null || true
    {
        date
        echo "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
        lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null || true
    } > /tmp/ming-installer/preflight.log 2>&1

    # Fresh VirtualBox disks sometimes reach Calamares without a usable label.
    # Only inspect completely blank non-removable disks; never touch a disk that
    # already has partitions or a mounted filesystem. blank_ab is intentionally
    # GPT-only and carries its own BIOS Boot Partition for BIOS GRUB.
    for disk in /dev/sd? /dev/vd? /dev/nvme?n?; do
        [ -b "${disk}" ] || continue
        case "${disk}" in
            /dev/sr*|/dev/loop*|/dev/ram*) continue ;;
        esac
        if lsblk -nr -o TYPE "${disk}" 2>/dev/null | grep -q '^part$'; then
            continue
        fi
        if lsblk -nr -o MOUNTPOINT "${disk}" 2>/dev/null | grep -q '/'; then
            continue
        fi
        if command -v wipefs >/dev/null 2>&1; then
            wipefs -n "${disk}" >> /tmp/ming-installer/preflight.log 2>&1 || true
        fi
    done
}

sleep 2

if is_live_environment || is_installer_boot; then
    if [ -z "${DISPLAY}" ]; then
        export DISPLAY=:0
    fi
    mkdir -p /tmp/ming-installer
    chmod 1777 /tmp/ming-installer 2>/dev/null || sudo -n chmod 1777 /tmp/ming-installer 2>/dev/null || true
    /usr/local/bin/ming-install-disable-locking >/tmp/ming-installer/disable-locking.log 2>&1 || true

    if command -v calamares &>/dev/null; then
        # -style/maximize handled by calamares window manager hint; keep retrying
        # if the X session is not ready yet.
        /usr/local/bin/ming-calamares-launcher >/tmp/ming-installer/calamares.log 2>&1 &
    else
        zenity --error --title="安装错误" --text="找不到 Calamares 安装程序。" 2>/dev/null || true
    fi
fi
LIVEINSTALLER

    chmod +x /usr/local/bin/ming-live-installer.sh

    # Keep the installer as a normal Live application so the phone desktop,
    # app drawer and Plank can all reopen it after Calamares is minimized.
    cat > "/usr/share/applications/Install Ming OS.desktop" << 'LIVEINSTALLERDESKTOP'
[Desktop Entry]
Type=Application
Name=Install Ming OS
Name[zh_CN]=安装 Ming OS
Comment=Install Ming OS to this computer
Comment[zh_CN]=将 Ming OS 安装到此计算机
Exec=/usr/local/bin/ming-live-installer.sh
Icon=system-software-install
Terminal=false
StartupNotify=true
X-Ming-Live-Only=true
LIVEINSTALLERDESKTOP
    install -d -m 0755 "/home/${MING_USER}/Desktop" "/etc/skel/Desktop"
    install -m 0644 "/usr/share/applications/Install Ming OS.desktop" \
        "/home/${MING_USER}/Desktop/Install Ming OS.desktop"
    install -m 0644 "/usr/share/applications/Install Ming OS.desktop" \
        "/etc/skel/Desktop/Install Ming OS.desktop"
    chown "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/Desktop/Install Ming OS.desktop"

cat > /usr/local/bin/ming-live-notice << 'LIVENOTICE'
#!/usr/bin/env python3
import json
import pathlib
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk


def build_identity():
    try:
        payload = json.loads(pathlib.Path("/etc/ming-os-build.json").read_text(encoding="ascii"))
        return str(payload.get("build_id") or "unknown")
    except (OSError, ValueError, TypeError):
        return "unknown"


class LiveNotice(Gtk.Window):
    def __init__(self):
        super().__init__(title="Ming OS Live 模式")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.stick()
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)

        screen = Gdk.Screen.get_default()
        width = min(820, max(480, screen.get_width() - 32))
        self.set_size_request(width, 92)
        self.move(max(16, (screen.get_width() - width) // 2), 16)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        row.set_border_width(14)
        title = Gtk.Label()
        title.set_markup(
            "<b>当前为 Live 模式，Ming OS 尚未安装。</b>\n"
            "此环境中的设置和文件在重启后不会保留任何数据。\n"
            "构建编号：%s" % build_identity()
        )
        title.set_xalign(0)
        title.set_line_wrap(True)
        button = Gtk.Button(label="继续安装 Ming OS")
        button.set_size_request(170, 48)
        button.connect("clicked", self.activate_installer)
        row.pack_start(title, True, True, 0)
        row.pack_end(button, False, False, 0)
        frame.add(row)
        self.add(frame)

    @staticmethod
    def activate_installer(_button):
        try:
            activated = subprocess.run(
                ["wmctrl", "-x", "-a", "calamares.calamares"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            activated = False
        if not activated:
            try:
                subprocess.Popen(
                    ["/usr/local/bin/ming-calamares-launcher"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return


window = LiveNotice()
window.connect("destroy", Gtk.main_quit)
window.show_all()
Gtk.main()
LIVENOTICE
    chmod 0755 /usr/local/bin/ming-live-notice

    cat > /usr/local/bin/ming-apply-wallpaper << 'APPLYWALLPAPER'
#!/usr/bin/env bash
set -u
wallpaper="${1:-/usr/share/backgrounds/ming-os/default.png}"
[[ -r "${wallpaper}" ]] || {
    echo "wallpaper not readable: ${wallpaper}" >&2
    exit 1
}
if command -v xfconf-query >/dev/null 2>&1; then
    while IFS= read -r property; do
        case "${property}" in
            */last-image|*/image-path)
                xfconf-query -c xfce4-desktop -p "${property}" -s "${wallpaper}" 2>/dev/null || true
                ;;
        esac
    done < <(xfconf-query -c xfce4-desktop -l 2>/dev/null || true)
    xfconf-query -c xfce4-desktop \
        -p /backdrop/screen0/monitor0/workspace0/last-image \
        -n -t string -s "${wallpaper}" 2>/dev/null || true
    xfconf-query -c xfce4-desktop \
        -p /backdrop/screen0/monitor0/workspace0/image-style \
        -n -t int -s 5 2>/dev/null || true
fi
if pgrep -u "$(id -u)" -x xfdesktop >/dev/null 2>&1; then
    xfdesktop --reload >/dev/null 2>&1 || true
elif command -v xfdesktop >/dev/null 2>&1; then
    (nohup xfdesktop >/tmp/ming-installer-xfdesktop.log 2>&1 &) || exit 1
else
    echo "xfdesktop is unavailable" >&2
    exit 1
fi
APPLYWALLPAPER
    chmod 0755 /usr/local/bin/ming-apply-wallpaper

    # Dedicated installer session with a minimal WM for reliable keyboard and
    # mouse focus. Calamares is maximized and automatically restarted on exit.
cat > /usr/local/bin/ming-installer-session << 'KIOSK'
#!/usr/bin/env bash
if command -v ming-install-disable-locking >/dev/null 2>&1; then
    ming-install-disable-locking >/tmp/ming-installer-disable-locking.log 2>&1 || true
fi
if command -v ming-apply-wallpaper >/dev/null 2>&1; then
    if ! ming-apply-wallpaper /usr/share/backgrounds/ming-os/default.png; then
        echo "Live wallpaper failed; using light fallback" >&2
        xsetroot -solid '#eff7f2' 2>/dev/null || true
    fi
else
    echo "Live wallpaper failed; helper unavailable" >&2
    xsetroot -solid '#eff7f2' 2>/dev/null || true
fi
if command -v xfwm4 >/dev/null 2>&1; then
    xfwm4 --replace >/tmp/ming-installer-xfwm4.log 2>&1 &
fi
if command -v ming-desktop-organizer >/dev/null 2>&1; then
    ming-desktop-organizer >/tmp/ming-installer-desktop-organizer.log 2>&1 || true
fi
if command -v ming-session-healthcheck >/dev/null 2>&1; then
    /usr/local/bin/ming-session-healthcheck --session \
        >/tmp/ming-installer-session-health.log 2>&1 &
fi
if command -v ming-window-manager-watchdog >/dev/null 2>&1; then
    /usr/local/bin/ming-window-manager-watchdog --session \
        >/tmp/ming-installer-window-health.log 2>&1 &
fi
/usr/local/bin/ming-live-notice >/tmp/ming-installer-live-notice.log 2>&1 &
notice_pid="$!"

close_live_notice_when_calamares_visible() {
    command -v wmctrl >/dev/null 2>&1 || return 0
    # Keep watching through the mode selector and slow module startup. The
    # notice must remain available when preflight or Calamares fails.
    for _focus_try in $(seq 1 720); do
        local calamares_window
        calamares_window="$(
            timeout --foreground 2s wmctrl -lx 2>/dev/null |
                awk 'tolower($0) ~ /calamares/ || tolower($0) ~ /ming os 安装程序/ || $0 ~ /安装程序/ {
                    if ($1 ~ /^0[xX][0-9a-fA-F]+$/) { print $1; exit }
                }'
        )"
        if [ -n "${calamares_window}" ]; then
            timeout --foreground 2s wmctrl -i -r "${calamares_window}" -b add,maximized_vert,maximized_horz 2>/dev/null || true
            timeout --foreground 2s wmctrl -x -a calamares.calamares 2>/dev/null || true
            timeout --foreground 2s wmctrl -i -a "${calamares_window}" 2>/dev/null || true
            kill "${notice_pid}" 2>/dev/null || true
            wait "${notice_pid}" 2>/dev/null || true
            return 0
        fi
        sleep 0.25
    done
}
prepare_installer_disks() {
    mkdir -p /tmp/ming-installer
    chmod 1777 /tmp/ming-installer 2>/dev/null || true
    {
        date
        echo "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
        lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null || true
    } > /tmp/ming-installer/preflight.log 2>&1

    for disk in /dev/sd? /dev/vd? /dev/nvme?n?; do
        [ -b "${disk}" ] || continue
        case "${disk}" in
            /dev/sr*|/dev/loop*|/dev/ram*) continue ;;
        esac
        if lsblk -nr -o TYPE "${disk}" 2>/dev/null | grep -q '^part$'; then
            continue
        fi
        if lsblk -nr -o MOUNTPOINT "${disk}" 2>/dev/null | grep -q '/'; then
            continue
        fi
        if command -v wipefs >/dev/null 2>&1; then
            wipefs -n "${disk}" >> /tmp/ming-installer/preflight.log 2>&1 || true
        fi
    done
}
while true; do
    if command -v ming-install-disable-locking >/dev/null 2>&1; then
        ming-install-disable-locking >/tmp/ming-installer-disable-locking.log 2>&1 || true
    fi
    install -d -m 1777 /tmp/ming-installer 2>/dev/null || \
        sudo -n install -d -m 1777 /tmp/ming-installer 2>/dev/null || \
        { mkdir -p /tmp/ming-installer 2>/dev/null && chmod 1777 /tmp/ming-installer 2>/dev/null; } || true
    close_live_notice_when_calamares_visible &
    notice_watcher_pid="$!"
    /usr/local/bin/ming-calamares-launcher >/tmp/ming-installer/calamares.log 2>&1
    launcher_status="$?"
    if [[ "${launcher_status}" -ne 0 ]]; then
        kill "${notice_watcher_pid}" 2>/dev/null || true
        wait "${notice_watcher_pid}" 2>/dev/null || true
    fi
    [[ "${launcher_status}" -eq 2 ]] && break
    # 已触发关机/重启则退出循环
    systemctl is-active --quiet reboot.target poweroff.target shutdown.target 2>/dev/null && break
    sleep 1
done
KIOSK
    chmod +x /usr/local/bin/ming-installer-session

    mkdir -p /usr/share/xsessions
    cat > /usr/share/xsessions/ming-installer.desktop << 'KISOSESS'
[Desktop Entry]
Name=Ming OS Installer
Exec=/usr/local/bin/ming-installer-session
Type=Application
KISOSESS

    mkdir -p /etc/systemd/system
    cat > /etc/systemd/system/ming-live-installer.service << SYSTEMDSERVICE
[Unit]
Description=Ming OS Live Installer
After=lightdm.service display-manager.service
Wants=lightdm.service
ConditionKernelCommandLine=|boot=live
ConditionKernelCommandLine=|live-config
ConditionKernelCommandLine=|ming.installer=1

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-live-installer.sh
User=${MING_USER}
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/${MING_USER}/.Xauthority
RemainAfterExit=yes

[Install]
WantedBy=graphical.target
SYSTEMDSERVICE


    # The dedicated ming-installer LightDM session owns automatic startup.
    # Keep this service available for diagnostics, but disabled so it cannot
    # race the session launcher and create overlapping Calamares windows.
    systemctl disable ming-live-installer.service 2>/dev/null || true
    rm -f /etc/systemd/system/graphical.target.wants/ming-live-installer.service
}

# ======================== Xfce 全局设置 ========================

configure_xfce_settings() {
    mkdir -p /home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml

    # Xfwm4 窗口管理器 (同步 Picom glx 设置)
    cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml" << 'XFWM4CFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="activate_action" type="string" value="bring"/>
    <property name="borderless_maximize" type="bool" value="true"/>
    <property name="box_move" type="bool" value="false"/>
    <property name="box_resize" type="bool" value="false"/>
    <property name="button_layout" type="string" value="O|HMC"/>
    <property name="button_offset" type="int" value="0"/>
    <property name="button_spacing" type="int" value="0"/>
    <property name="click_to_focus" type="bool" value="true"/>
    <property name="cycle_apps_only" type="bool" value="false"/>
    <property name="cycle_draw_frame" type="bool" value="true"/>
    <property name="cycle_hidden" type="bool" value="true"/>
    <property name="cycle_minimum" type="bool" value="true"/>
    <property name="cycle_workspaces" type="bool" value="false"/>
    <property name="double_click_action" type="string" value="maximize"/>
    <property name="focus_delay" type="int" value="200"/>
    <property name="focus_hint" type="bool" value="true"/>
    <property name="focus_new" type="bool" value="true"/>
    <property name="frame_opacity" type="int" value="100"/>
    <property name="full_width_title" type="bool" value="true"/>
    <property name="horiz_scroll_opacity" type="bool" value="false"/>
    <property name="inactive_opacity" type="int" value="100"/>
    <property name="maximized_offset" type="int" value="0"/>
    <property name="mousewheel_rollup" type="bool" value="true"/>
    <property name="move_opacity" type="int" value="100"/>
    <property name="placement_mode" type="string" value="center"/>
    <property name="placement_ratio" type="int" value="50"/>
    <property name="popup_opacity" type="int" value="100"/>
    <property name="prevent_focus_stealing" type="bool" value="false"/>
    <property name="raise_delay" type="int" value="200"/>
    <property name="raise_on_click" type="bool" value="true"/>
    <property name="raise_on_focus" type="bool" value="false"/>
    <property name="resize_opacity" type="int" value="100"/>
    <property name="scroll_workspaces" type="bool" value="true"/>
    <property name="shadow_delta_x" type="int" value="0"/>
    <property name="shadow_delta_y" type="int" value="0"/>
    <property name="shadow_opacity" type="int" value="0"/>
    <property name="show_app_icon" type="bool" value="true"/>
    <property name="show_dock_shadow" type="bool" value="false"/>
    <property name="show_frame_shadow" type="bool" value="false"/>
    <property name="show_popup_shadow" type="bool" value="false"/>
    <property name="snap_to_border" type="bool" value="true"/>
    <property name="snap_to_windows" type="bool" value="true"/>
    <property name="snap_width" type="int" value="10"/>
    <property name="sync_to_vblank" type="bool" value="true"/>
	    <property name="theme" type="string" value="Ming-Glass"/>
    <property name="tile_on_move" type="bool" value="true"/>
    <property name="title_alignment" type="string" value="center"/>
    <property name="title_font" type="string" value="Noto Sans CJK SC Medium 11"/>
    <property name="title_horizontal_offset" type="int" value="0"/>
    <property name="titleless_maximize" type="bool" value="false"/>
    <property name="title_shadow_active" type="string" value="false"/>
    <property name="title_shadow_inactive" type="string" value="false"/>
    <property name="title_vertical_offset_active" type="int" value="0"/>
    <property name="title_vertical_offset_inactive" type="int" value="0"/>
    <property name="toggle_workspaces" type="bool" value="false"/>
    <property name="unredirect_overlays" type="bool" value="true"/>
    <property name="use_compositing" type="bool" value="false"/>
    <property name="workspace_count" type="int" value="1"/>
    <property name="wrap_cycle" type="bool" value="true"/>
    <property name="wrap_layout" type="bool" value="true"/>
    <property name="wrap_resistance" type="int" value="10"/>
    <property name="wrap_windows" type="bool" value="true"/>
    <property name="wrap_workspaces" type="bool" value="false"/>
    <property name="zoom_desktop" type="bool" value="true"/>
    <property name="vblank_mode" type="string" value="glx"/>
  </property>
</channel>
XFWM4CFG

    # Xfce desktop wallpaper settings. Xfce stores one backdrop path per monitor
    # name, so seed the common names and let ming-apply-appearance update the
    # actual runtime monitor paths after login.
    cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml" << 'DESKTOPCFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="backdrop" type="empty">
    <property name="screen0" type="empty">
      <!-- 覆盖所有常见显示器连接器名称，确保任何机器都能应用壁纸 -->
      <!-- screen / Virtual（VirtualBox/QEMU）-->
      <property name="monitorscreen" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorVirtual-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- VGA（老台式机/部分笔记本）-->
      <property name="monitorVGA-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorVGA1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- HDMI（大多数现代台式机/笔记本）-->
      <property name="monitorHDMI-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorHDMI1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorHDMI-A-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- DP（DisplayPort，台式机/工作站）-->
      <property name="monitorDP-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- eDP（内置屏幕，大多数现代笔记本）-->
      <property name="monitoreDP-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitoreDP1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- LVDS（老笔记本内置屏，如 i5-2430M 时代）-->
      <property name="monitorLVDS-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorLVDS1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <!-- DVI（老台式机）-->
      <property name="monitorDVI-D-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
      <property name="monitorDVI-I-1" type="empty">
        <property name="workspace0" type="empty">
          <property name="color-style" type="int" value="0"/>
          <property name="image-style" type="int" value="5"/>
          <property name="last-image" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
          <property name="image-path" type="string" value="/usr/share/backgrounds/ming-os/default.png"/>
        </property>
      </property>
    </property>
  </property>
  <property name="desktop-icons" type="empty">
    <property name="style" type="int" value="0"/>
    <property name="file-icons" type="empty">
      <property name="show-home" type="bool" value="false"/>
      <property name="show-trash" type="bool" value="false"/>
      <property name="show-filesystem" type="bool" value="false"/>
      <property name="show-removable" type="bool" value="false"/>
    </property>
  </property>
</channel>
DESKTOPCFG

    # Xsettings 全局外观
    cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml" << 'XSETTINGSCFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Net" type="empty">
    <property name="ThemeName" type="string" value="Ming-Glass"/>
    <property name="IconThemeName" type="string" value="Papirus"/>
    <property name="DoubleClickTime" type="int" value="400"/>
    <property name="DoubleClickDistance" type="int" value="5"/>
    <property name="DndDragThreshold" type="int" value="8"/>
    <property name="CursorBlink" type="bool" value="true"/>
    <property name="CursorBlinkTime" type="int" value="1200"/>
    <property name="SoundThemeName" type="string" value="default"/>
    <property name="EnableEventSounds" type="bool" value="false"/>
    <property name="EnableInputFeedbackSounds" type="bool" value="false"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="CanChangeAccels" type="bool" value="false"/>
    <property name="ColorPalette" type="string" value="black:white:gray50:red:purple:blue:light blue:green:yellow:orange:lavender:brown:gold1:gold2:gold3:gold4:gold5:gold6:gold7:gold8:gold9:gold10:gold11:gold12:gold13:gold14:gold15:gold16:gold17:gold18:gold19:gold20"/>
    <property name="FontName" type="string" value="Noto Sans CJK SC 11"/>
    <property name="IconSizes" type="string" value=""/>
    <property name="KeyThemeName" type="string" value=""/>
    <property name="ToolbarStyle" type="string" value="icons"/>
    <property name="ToolbarIconSize" type="string" value="small-toolbar"/>
    <property name="MenuImages" type="bool" value="false"/>
    <property name="ButtonImages" type="bool" value="false"/>
    <property name="MenuBarAccel" type="string" value="F10"/>
    <property name="CursorThemeName" type="string" value="Adwaita"/>
    <property name="CursorThemeSize" type="int" value="24"/>
    <property name="DecorationLayout" type="string" value="menu:minimize,maximize,close"/>
    <property name="DialogsUseHeader" type="bool" value="true"/>
    <property name="TitlebarMiddleClick" type="string" value="none"/>
  </property>
  <property name="Xft" type="empty">
    <property name="DPI" type="int" value="96"/>
    <property name="Antialias" type="int" value="1"/>
    <property name="Hinting" type="int" value="1"/>
    <property name="HintStyle" type="string" value="hintslight"/>
    <property name="RGBA" type="string" value="rgb"/>
  </property>
</channel>
XSETTINGSCFG

    cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml" << 'SCREENSAVERCFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-screensaver" version="1.0">
  <property name="saver" type="empty">
    <property name="enabled" type="bool" value="false"/>
    <property name="fullscreen-inhibit" type="bool" value="true"/>
    <property name="mode" type="int" value="0"/>
  </property>
  <property name="lock" type="empty">
    <property name="enabled" type="bool" value="false"/>
    <property name="saver-activation" type="empty">
      <property name="enabled" type="bool" value="false"/>
      <property name="delay" type="int" value="5"/>
    </property>
  </property>
</channel>
SCREENSAVERCFG

    cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml" << 'KEYBOARDSHORTCUTSCFG'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Primary&gt;&lt;Alt&gt;t" type="string" value="ming-terminal"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;l" type="string" value="ming-lock"/>
      <property name="&lt;Super&gt;e" type="string" value="ming-files"/>
      <property name="&lt;Super&gt;i" type="string" value="ming-control-center"/>
    </property>
  </property>
</channel>
KEYBOARDSHORTCUTSCFG

    # Whisker Menu 配置（26.4.1 玻璃主题版）
    mkdir -p "/home/${MING_USER}/.config/xfce4/panel"
    cat > "/home/${MING_USER}/.config/xfce4/panel/whiskermenu-1.rc" << 'WHISKERRC'
button-title=Ming OS
show-button-title=true
launcher-icon-size=2
button-icon=ming-os-menu
show-favorites=true
show-commands=true
show-recent=true
recent-items-max=6
show-category-names=true
favorites=ming-control-center.desktop,ming-files.desktop,ming-firefox.desktop,spark-store.desktop,xiahai-xiaoming.desktop,ming-terminal.desktop
command-settings=ming-control-center
command-lockscreen=ming-lock
command-switchuser=dm-tool switch-to-greeter
command-logoutuser=ming-power-action logout
command-restart=ming-power-action reboot
command-shutdown=ming-power-action poweroff
search-actions=1
position-categories-alternate=false
position-commands-alternate=true
position-search-alternate=false
category-icon-size=1
item-icon-size=2
menu-width=440
menu-height=540
menu-opacity=100
background-opacity=100
view-mode=1
sort-categories=true
WHISKERRC

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/xfce4"
}

# ======================== 登录期外观强制应用 ========================
# 为什么需要它：构建期写入的 xfconf XML 在真实硬件上未必被 xfdesktop 接受
# （真实显示器连接器名未知），且 Plank/picom 需要在会话内启动。此脚本在每次
# 登录时自愈式地强制套用壁纸、主题与 Dock，是“美化确实生效”的最后保障。

configure_appearance_enforcer() {
    cat > /usr/local/bin/ming-apply-appearance << 'APPLYAPPEARANCE'
#!/usr/bin/env bash
# Ming OS 外观强制应用 - 每次登录运行，确保美化生效
set -u
appearance_log="${HOME}/.cache/ming-os/appearance.log"
mkdir -p "$(dirname "${appearance_log}")" 2>/dev/null || true
if command -v ming-appearance-control >/dev/null 2>&1; then
    timeout --foreground 8s ming-appearance-control reapply --json \
        >>"${appearance_log}" 2>&1 || true
    exit 0
fi
WALL_PNG="/usr/share/backgrounds/ming-os/default.png"
WALL_1366="/usr/share/backgrounds/ming-os/default-1366x768.png"

appearance_phone_env="${MING_PHONE_DESKTOP-__unset__}"
if [[ -r /etc/default/ming-os ]]; then
    . /etc/default/ming-os
fi
if [[ "${appearance_phone_env}" != "__unset__" ]]; then
    MING_PHONE_DESKTOP="${appearance_phone_env}"
fi
: "${MING_PHONE_DESKTOP:=1}"

# 等待 xfdesktop / xfconfd 就绪
for i in $(seq 1 15); do
    if xfconf-query -c xfce4-desktop -l &>/dev/null; then break; fi
    sleep 1
done

# 低分辨率优先使用小尺寸壁纸，减小内存占用
WALL="${WALL_PNG}"
RES=$(xrandr --current 2>/dev/null | grep '\*' | head -1 | awk '{print $1}')
W=$(echo "${RES}" | cut -d'x' -f1)
if [[ -n "${W}" && "${W}" -le 1366 && -f "${WALL_1366}" ]]; then
    WALL="${WALL_1366}"
fi

# 对每一个真实 backdrop 属性（逐显示器/逐工作区）套用壁纸。
# 这样无论连接器叫 monitorVGA-1 / monitorHDMI-1 / monitorscreen 都能命中。
mapfile -t PROPS < <(xfconf-query -c xfce4-desktop -l 2>/dev/null | grep '/last-image$')
if [[ ${#PROPS[@]} -eq 0 ]]; then
    # 首次登录 xfconf 数据库为空，无法枚举属性。
    # 解决方案：把所有常见连接器名称全部写一遍，确保至少一个命中。
    # 真实机器连接器名（xrandr --listmonitors）各不相同：
    # VGA-1 / HDMI-1 / DP-1 / eDP-1 / LVDS-1 / Virtual-1 / screen 等。
    for mon in screen Virtual-1 VGA-1 VGA1 HDMI-1 HDMI1 DP-1 DP1 eDP-1 eDP1 LVDS-1 LVDS1 DVI-1 DVI1 DVI-D-1; do
        for ws in workspace0 workspace1; do
            xfconf-query -c xfce4-desktop \
                -p "/backdrop/screen0/monitor${mon}/${ws}/last-image" \
                -n -t string -s "${WALL}" 2>/dev/null || true
            xfconf-query -c xfce4-desktop \
                -p "/backdrop/screen0/monitor${mon}/${ws}/image-style" \
                -n -t int -s 5 2>/dev/null || true
            xfconf-query -c xfce4-desktop \
                -p "/backdrop/screen0/monitor${mon}/${ws}/image-path" \
                -n -t string -s "${WALL}" 2>/dev/null || true
        done
    done
else
    for p in "${PROPS[@]}"; do
        xfconf-query -c xfce4-desktop -p "${p}" -s "${WALL}" 2>/dev/null || true
        # 同步设置缩放方式为 5 (zoomed/拉伸填充)
        style_prop="${p%/last-image}/image-style"
        xfconf-query -c xfce4-desktop -p "${style_prop}" -n -t int -s 5 2>/dev/null || true
        # 同时写 image-path（部分 XFCE 版本优先读这个）
        path_prop="${p%/last-image}/image-path"
        xfconf-query -c xfce4-desktop -p "${path_prop}" -n -t string -s "${WALL}" 2>/dev/null || true
    done
fi

# 强制主题/图标主题（防止首次会话回退到默认）
xfconf-query -c xsettings -p /Net/ThemeName -s "Ming-Glass" 2>/dev/null || true
xfconf-query -c xsettings -p /Net/IconThemeName -s "Papirus" 2>/dev/null || true
xfconf-query -c xfwm4 -p /general/theme -s "Ming-Glass" 2>/dev/null || true
# Ming Mint is the final active selection; the legacy values above remain only
# as compatibility markers for older user profiles and release tooling.
xfconf-query -c xsettings -p /Net/ThemeName -s "Ming-Mint" 2>/dev/null || true
xfconf-query -c xsettings -p /Net/IconThemeName -s "Ming-Mint" 2>/dev/null || true
xfconf-query -c xfwm4 -p /general/theme -s "Ming-Mint" 2>/dev/null || true
xfconf-query -c xfce4-session -p /general/LockCommand -n -t string -s "ming-lock" 2>/dev/null || true
xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Primary><Alt>t' -n -t string -s "ming-terminal" 2>/dev/null || true
xfconf-query -c xfce4-keyboard-shortcuts -p '/commands/custom/<Primary><Alt>l' -n -t string -s "ming-lock" 2>/dev/null || true
oobe_ready=false
if [[ -r "${HOME}/.config/ming-os/oobe-account-done" ]] \
    && grep -Fxq configured "${HOME}/.config/ming-os/oobe-account-done" 2>/dev/null; then
    oobe_ready=true
fi
if "${oobe_ready}"; then
    xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s true 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /saver/fullscreen-inhibit -n -t bool -s true 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s true 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/saver-activation/enabled -n -t bool -s true 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/saver-activation/delay -n -t int -s 5 2>/dev/null || true
else
    if command -v ming-install-disable-locking >/dev/null 2>&1; then
        ming-install-disable-locking >/dev/null 2>&1 || true
    else
        xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s false 2>/dev/null || true
        xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s false 2>/dev/null || true
    fi
fi

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
PLANK_SETTINGS="${HOME}/.config/plank/dock1/settings"
if [[ "${MEM_MB}" -le 2600 && -f "${PLANK_SETTINGS}" ]]; then
    printf '[ming-appearance] low-memory host keeps approved legacy Dock geometry\n' >&2
fi

# Ming 手机桌面接管壁纸、图标和点击。watchdog 只会在确认它就绪后
# 停止 xfdesktop，因此启动失败时仍保留原生桌面作为安全后备。
xfconf-query -c xfce4-desktop -p /desktop-icons/style -n -t int -s 0 2>/dev/null || true

# Dock-only 桌面：Xfce 面板只作为兼容组件安装，不作为可见任务栏运行。
mkdir -p "${HOME}/.cache/sessions"
rm -f "${HOME}/.cache/sessions/xfce4-session-"* 2>/dev/null || true
if pgrep -u "$(id -u)" -x xfce4-panel >/dev/null 2>&1; then
    pkill -TERM -u "$(id -u)" -x xfce4-panel >/dev/null 2>&1 || true
fi

# 确保 Ming 手机桌面在运行。它必须早于 Dock 出现，避免只剩空壁纸。
# MING_PHONE_DESKTOP=0 保留 Xfce 原生桌面作为显式兼容模式。
if [[ "${MING_PHONE_DESKTOP:-1}" == "1" ]] && command -v ming-phone-desktop-watchdog &>/dev/null; then
    /usr/local/bin/ming-phone-desktop-watchdog >/dev/null 2>&1 || true
elif command -v xfdesktop &>/dev/null && ! pgrep -u "$(id -u)" -x xfdesktop >/dev/null 2>&1; then
    (nohup xfdesktop >/dev/null 2>&1 &) 2>/dev/null || true
fi

# Ensure the primary Plank Dock is visible after the compositor/session settles.
if command -v ming-plank-watchdog &>/dev/null; then
    /usr/local/bin/ming-plank-watchdog >/dev/null 2>&1 || true
fi

exit 0
APPLYAPPEARANCE
    chmod +x /usr/local/bin/ming-apply-appearance

    # 登录自启动（在 picom/plank 之后，phase=Applications）
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/ming-apply-appearance.desktop" << 'APPLYAUTO'
[Desktop Entry]
Type=Application
Name=Ming Appearance
Comment=确保 Ming OS 外观正确应用
Exec=/usr/local/bin/ming-apply-appearance
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
X-GNOME-Autostart-Delay=3
APPLYAUTO
    chown -R "${MING_USER}:${MING_USER}" "${autostart_dir}/ming-apply-appearance.desktop"
}

# ======================== 触屏手势 + 虚拟键盘 (Onboard) ========================
# 设计意图：为小米平板一代/Surface 等触屏设备提供 macOS 风格触摸体验——
#   1. Onboard 虚拟键盘：点击文本输入框时自动弹起（auto-show），无物理键盘也能输入。
#   2. touchegg：三指上滑=显示桌面、三指下滑=最小化、四指左右=切换工作区。
configure_touch_input() {
    cat > /usr/local/bin/ming-touch-session << 'TOUCHSESSION'
#!/usr/bin/env bash
set -u
action="${1:-}"
has_touch_hardware() {
    if command -v xinput >/dev/null 2>&1 \
        && xinput --list --short 2>/dev/null | grep -Eiq 'touchscreen|tablet'; then
        return 0
    fi
    for name in /sys/class/input/event*/device/name; do
        [[ -r "${name}" ]] || continue
        grep -Eiq 'touchscreen|tablet' "${name}" && return 0
    done
    return 1
}
case "${action}" in
    onboard|touchegg)
        has_touch_hardware || exit 0
        command -v "${action}" >/dev/null 2>&1 || exit 0
        exec "${action}"
        ;;
    *) exit 2 ;;
esac
TOUCHSESSION
    chmod 0755 /usr/local/bin/ming-touch-session

    # ---- Onboard 虚拟键盘：自动弹起 ----
    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/onboard-autostart.desktop" << 'ONBOARDAUTO'
[Desktop Entry]
Type=Application
Name=Onboard 虚拟键盘
Comment=触屏点击输入框时自动弹起
Exec=/usr/local/bin/ming-touch-session onboard
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
ONBOARDAUTO

    # 通过 gsettings schema 覆盖让 Onboard 默认 auto-show + 停靠底部 + 触屏友好
    # 平板专项：大键盘布局，按键放大1.4倍，适合手指点击
    mkdir -p "/home/${MING_USER}/.config/onboard"
    cat > "/home/${MING_USER}/.config/onboard/ming-defaults.dconf" << 'ONBOARDCFG'
[org/onboard]
layout='Compact'
theme='Nightshade'
xembed-onboard=false
start-minimized=true
key-size=1.4

[org/onboard/auto-show]
enabled=true
hide-on-key-press=false
tablet-mode-detection-enabled=true

[org/onboard/window]
docking-enabled=true
docking-edge='bottom'
force-to-top=true
ONBOARDCFG

    # 登录时把默认值灌入 dconf（用户可后续自行调整）
    cat > "/home/${MING_USER}/.config/autostart/ming-onboard-defaults.desktop" << 'ONBOARDLOAD'
[Desktop Entry]
Type=Application
Name=Ming Onboard Defaults
Comment=应用虚拟键盘默认设置
Exec=sh -c "test -f ~/.config/onboard/.applied || (dconf load /org/onboard/ < ~/.config/onboard/ming-defaults.dconf 2>/dev/null && touch ~/.config/onboard/.applied)"
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
ONBOARDLOAD

    # ---- touchegg 触摸手势（增强版：单指/双指/三指/四指全覆盖）----
    mkdir -p "/home/${MING_USER}/.config/touchegg"
    cat > "/home/${MING_USER}/.config/touchegg/touchegg.conf" << 'TOUCHEGGCFG'
<touchégg>
  <settings>
    <property name="animation_delay">100</property>
    <property name="action_execute_threshold">10</property>
  </settings>
  <application name="All">
    <!-- 双指：两指上下滚动（模拟鼠标滚轮，触屏最常用操作）-->
    <gesture type="SWIPE" fingers="2" direction="UP">
      <action type="SCROLL">
        <direction>UP</direction>
        <speed>2</speed>
      </action>
    </gesture>
    <gesture type="SWIPE" fingers="2" direction="DOWN">
      <action type="SCROLL">
        <direction>DOWN</direction>
        <speed>2</speed>
      </action>
    </gesture>
    <!-- 双指：捏合缩放（浏览器/图片查看必备）-->
    <gesture type="PINCH" fingers="2" direction="IN">
      <action type="SEND_KEYS">
        <keys>Control_L+minus</keys>
        <repeat>true</repeat>
      </action>
    </gesture>
    <gesture type="PINCH" fingers="2" direction="OUT">
      <action type="SEND_KEYS">
        <keys>Control_L+plus</keys>
        <repeat>true</repeat>
      </action>
    </gesture>
    <!-- 双指：长按 = 右键（平板操作习惯）-->
    <gesture type="TAP" fingers="2" direction="UNKNOWN">
      <action type="MOUSE_CLICK">
        <button>3</button>
        <on>begin</on>
      </action>
    </gesture>
    <!-- 三指：上划 = 显示桌面；下划 = 最小化当前窗口 -->
    <gesture type="SWIPE" fingers="3" direction="UP">
      <action type="RUN_COMMAND">
        <repeat>false</repeat>
        <command>wmctrl -k on</command>
        <on>begin</on>
      </action>
    </gesture>
    <gesture type="SWIPE" fingers="3" direction="DOWN">
      <action type="RUN_COMMAND">
        <repeat>false</repeat>
        <command>xdotool getactivewindow windowminimize</command>
        <on>begin</on>
      </action>
    </gesture>
    <!-- 三指：左右划 = 前进/后退（浏览器）-->
    <gesture type="SWIPE" fingers="3" direction="LEFT">
      <action type="SEND_KEYS">
        <keys>Alt_L+Left</keys>
        <on>begin</on>
      </action>
    </gesture>
    <gesture type="SWIPE" fingers="3" direction="RIGHT">
      <action type="SEND_KEYS">
        <keys>Alt_L+Right</keys>
        <on>begin</on>
      </action>
    </gesture>
    <!-- 四指：左右划 = 切换工作区 -->
    <gesture type="SWIPE" fingers="4" direction="LEFT">
      <action type="RUN_COMMAND">
        <repeat>false</repeat>
        <command>wmctrl -s $(( $(wmctrl -d | grep '\*' | cut -d' ' -f1) + 1 ))</command>
        <on>begin</on>
      </action>
    </gesture>
    <gesture type="SWIPE" fingers="4" direction="RIGHT">
      <action type="RUN_COMMAND">
        <repeat>false</repeat>
        <command>wmctrl -s $(( $(wmctrl -d | grep '\*' | cut -d' ' -f1) - 1 ))</command>
        <on>begin</on>
      </action>
    </gesture>
    <!-- 四指：上划 = 应用切换器 -->
    <gesture type="SWIPE" fingers="4" direction="UP">
      <action type="SEND_KEYS">
        <keys>Alt_L+Tab</keys>
        <on>begin</on>
      </action>
    </gesture>
  </application>
</touchégg>
TOUCHEGGCFG

    # touchegg 守护进程需常驻；提供用户级自启（客户端）
    cat > "/home/${MING_USER}/.config/autostart/touchegg.desktop" << 'TOUCHEGGAUTO'
[Desktop Entry]
Type=Application
Name=Touchégg
Comment=触摸手势
Exec=/usr/local/bin/ming-touch-session touchegg
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
TOUCHEGGAUTO
    systemctl disable touchegg.service 2>/dev/null || true

    chown -R "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.config/onboard" \
        "/home/${MING_USER}/.config/touchegg" \
        "/home/${MING_USER}/.config/autostart" 2>/dev/null || true
}

# ======================== 主流程 ========================

main() {
    echo "=====> [03_desktop] 开始 Ming OS 26.4.1 Dock 桌面定制 <====="

    generate_ming_icons
    install_ming_mint_icon_set || return 1
    configure_hidpi_autoscale
    install_themes
    setup_wallpaper || return 1
    configure_ming_shell
    install_ming_shell_components
    install_ming_settings
    cleanup_retired_ming_entries
    install_ota_target_guard
    install_ming_files
    ensure_wps_office
    configure_xfce_settings      # 先写桌面/xfwm/xsettings（含壁纸 backdrop）
    configure_xfce_panel         # 顶部 macOS 菜单栏
    configure_plank_dock         # 底部可放大 Dock
    configure_ming_mint_dock_profile
    configure_ming_mint_desktop_icons
    configure_ming_mint_theme
    configure_picom
    configure_session_healthcheck # 统一启动/健康协调器（唯一常驻入口）
    configure_touch_input        # 触屏手势 + Onboard 虚拟键盘
    configure_notification_filter
    configure_simplified_menus   # 只动 Thunar 右键菜单，不再覆盖桌面/xfwm 配置
    deploy_release_readme
    configure_autostart
    deploy_live_installer
    setup_account_oobe
    setup_welcome_wizard
    configure_appearance_enforcer  # 最后部署登录期自愈强制应用

    echo "=====> [03_desktop] Ming OS 26.4.1 Dock 桌面定制完成 <====="
}

main
