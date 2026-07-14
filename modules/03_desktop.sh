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
    mkdir -p "${lib_dir}" /usr/local/bin /usr/local/sbin "/home/${MING_USER}/.local/share/applications"
    for asset in ming-shell-common.py ming-appearance-control.py ming-notifications.py ming-connection-notify.py ming-device-control.py ming-audio-session.py ming-hardware-status.py ming-app-drawer.py ming-launch.py ming-package-installer.py; do
        if [[ ! -s "${asset_dir}/${asset}" ]]; then
            echo "ERROR: missing Ming shell asset: ${asset}" >&2
            return 1
        fi
    done

    install -m 0644 "${asset_dir}/ming-shell-common.py" "${lib_dir}/ming-shell-common.py"
    install -m 0644 "${asset_dir}/ming-notifications.py" "${lib_dir}/ming-notifications.py"
    install -m 0644 "${asset_dir}/ming-connection-notify.py" "${lib_dir}/ming-connection-notify.py"
    install -m 0644 "${asset_dir}/ming-device-control.py" "${lib_dir}/ming-device-control.py"
    # Drawer and broker load the common module beside their executable.
    install -m 0644 "${asset_dir}/ming-shell-common.py" /usr/local/bin/ming-shell-common.py
    install -m 0755 "${asset_dir}/ming-notifications.py" /usr/local/bin/ming-notifications
    install -m 0755 "${asset_dir}/ming-connection-notify.py" /usr/local/bin/ming-connection-notify
    install -m 0755 "${asset_dir}/ming-device-control.py" /usr/local/bin/ming-device-control
    install -m 0755 "${asset_dir}/ming-audio-session.py" /usr/local/bin/ming-audio-session
    install -m 0755 "${asset_dir}/ming-hardware-status.py" /usr/local/bin/ming-hardware-status
    install -m 0755 "${asset_dir}/ming-app-drawer.py" /usr/local/bin/ming-app-drawer
    install -m 0755 "${asset_dir}/ming-launch.py" /usr/local/bin/ming-launch
    install -m 0755 "${asset_dir}/ming-appearance-control.py" /usr/local/bin/ming-appearance-control
    install -m 0755 "${asset_dir}/ming-package-installer.py" /usr/local/sbin/ming-package-installer

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

result_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/ming-package-result.XXXXXX" 2>/dev/null || true)"
if [[ -z "${result_file}" ]]; then
    notify-send -u critical "安装 DEB 软件包" "无法创建安装结果文件。" 2>/dev/null || true
    exit 1
fi
trap 'rm -f "${result_file}"' EXIT

if pkexec /usr/local/sbin/ming-package-installer install "${package_file}" >"${result_file}" 2>&1; then
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
ok = bool(result.get("ok")) and return_code == 0
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
else:
    title = "软件安装失败"
    reason = str(result.get("error") or raw or "安装被取消或未返回可读结果。")
    detail = "%s\n日志：%s" % (reason[:1200], log_path)
if shutil.which("zenity"):
    subprocess.run(
        ["zenity", "--info" if ok else "--error", "--title=" + title,
         "--text=" + detail, "--width=520"], check=False)
elif shutil.which("notify-send"):
    subprocess.run(
        ["notify-send", "-u", "normal" if ok else "critical", title, detail], check=False)
else:
    print(title + "\n" + detail, file=sys.stderr)
raise SystemExit(0 if ok else 1)
MINGPACKAGEUIPY
then
    if command -v ming-phone-desktop >/dev/null 2>&1; then
        ming-phone-desktop --sync >/dev/null 2>&1 || true
    fi
    exit 0
fi
exit 1
MINGPACKAGEGUI
    chmod 0755 /usr/local/bin/ming-package-install-gui

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
config["Default Applications"]["inode/directory"] = "ming-files.desktop"
config["Default Applications"]["application/x-gnome-saved-search"] = "ming-files.desktop"
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
if [[ -s "${SCALE_PREFERENCE}" ]]; then
    # Ming Settings wrote an explicit accessibility choice.  A repair or
    # resolution change must not silently replace it with an auto default.
    exit 0
fi
if [[ -f "${SCALE_CONFIG}" ]]; then
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
    DPI=96;    PANEL_SIZE=24; DOCK_ICON=34; CURSOR_SIZE=18; FONT_SIZE=9
else
    DPI=96;    PANEL_SIZE=24; DOCK_ICON=30; CURSOR_SIZE=18; FONT_SIZE=9
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
    FONT_SIZE=$((FONT_SIZE > 9 ? FONT_SIZE - 1 : 9))
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
        sed -i "s/^ZoomEnabled=.*/ZoomEnabled=true/" "${PLANK_SETTINGS}" 2>/dev/null || true
        sed -i "s/^ZoomPercent=.*/ZoomPercent=126/" "${PLANK_SETTINGS}" 2>/dev/null || true
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

echo "done" > "${SCALE_CONFIG}"
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
            [garlic-claw]="garlic-claw"
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
GTK2SETTINGS
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/gtk-3.0/settings.ini" "/home/${MING_USER}/.gtkrc-2.0"

    # Ming-Glass GTK3 轻量纸感主题：参考 Lingmo/deepin/macOS 的统一浅色层级，
    # 但避免高成本模糊和重发光，优先照顾老电脑。
    mkdir -p /usr/share/themes/Ming-Glass/gtk-3.0
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

window {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
  border-radius: 10px;
}

window decoration {
  border-radius: 12px;
  box-shadow: 0 12px 28px rgba(26, 67, 56, 0.09);
  margin: 0;
}

button {
  border-radius: 10px;
  padding: 7px 15px;
  border: 1px solid @borders;
  background-image: none;
  background-color: rgba(255, 255, 255, 0.90);
  color: @theme_fg_color;
  transition: background-color 160ms ease-out, border-color 160ms ease-out, box-shadow 180ms ease-out;
  min-height: 30px;
}

button:hover {
  background-color: #FFFFFF;
  border-color: rgba(47, 138, 125, 0.24);
  box-shadow: 0 4px 12px rgba(30, 70, 58, 0.06);
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
  background-color: rgba(255, 249, 249, 0.94);
}

entry {
  border-radius: 10px;
  padding: 7px 12px;
  border: 1px solid @borders;
  background-color: rgba(255, 255, 255, 0.95);
  color: @theme_fg_color;
  min-height: 30px;
}

entry:focus {
  border-color: #2F8A7D;
  box-shadow: 0 0 0 2px rgba(47, 138, 125, 0.10);
}

notebook header {
  background-color: rgba(245, 248, 244, 0.96);
  border: none;
}

notebook tab {
  border-radius: 10px 10px 0 0;
  padding: 7px 16px;
  background-color: rgba(238, 243, 240, 0.92);
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
  background-color: rgba(28, 39, 35, 0.94);
  color: #FFFFFF;
  border: 1px solid rgba(255, 255, 255, 0.12);
  padding: 7px 11px;
}

menu, .menu {
  background-color: rgba(255, 255, 255, 0.96);
  border: 1px solid @borders;
  border-radius: 12px;
  padding: 4px;
  box-shadow: 0 10px 24px rgba(30, 70, 58, 0.07);
}

menuitem {
  border-radius: 8px;
  padding: 7px 12px;
  min-height: 24px;
  color: @theme_fg_color;
}

menuitem:hover {
  background-color: rgba(47, 138, 125, 0.08);
}

headerbar {
  background-color: rgba(255, 255, 255, 0.86);
  border: none;
  border-bottom: 1px solid rgba(47, 138, 125, 0.08);
  border-radius: 12px 12px 0 0;
  padding: 5px 10px;
  min-height: 38px;
}

toolbar {
  background-color: rgba(255, 255, 255, 0.86);
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
  background-color: rgba(255, 255, 255, 0.94);
  border: 1px solid @borders;
  min-width: 18px;
  min-height: 18px;
}

checkbutton check:checked, radiobutton radio:checked {
  background-color: #2F8A7D;
  border-color: #2F8A7D;
}

.view, iconview {
  background-color: rgba(255, 255, 255, 0.80);
  color: @theme_fg_color;
  border-radius: 10px;
}

.view:selected, iconview:selected {
  background-color: rgba(47, 138, 125, 0.12);
  color: @theme_selected_fg_color;
}

treeview header button {
  background-color: rgba(245, 248, 244, 0.96);
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
  background-color: rgba(238, 243, 240, 0.92);
  border-right: 1px solid rgba(47, 138, 125, 0.10);
}

placessidebar row,
.sidebar row,
stacksidebar row {
  border-radius: 10px;
  margin: 2px 6px;
  padding: 6px 9px;
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
  background-color: rgba(255, 255, 255, 0.96);
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
  background-color: rgba(255, 255, 255, 0.74);
  border: 1px solid rgba(31, 98, 84, 0.10);
  border-radius: 14px;
  margin: 6px 8px 4px 8px;
  box-shadow: 0 8px 22px rgba(30, 70, 58, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.60);
  padding: 2px 6px;
}

.xfce4-panel button {
  border-radius: 11px;
  padding: 4px 7px;
  margin: 2px 4px;
  border: 1px solid transparent;
  background-color: transparent;
  transition: all 200ms ease;
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
Comment=Ming OS 26.3.2 Light Paper Theme
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=Ming-Glass
MetacityTheme=Ming-Glass
IconTheme=Papirus
CursorTheme=Adwaita
THEMEINDEX
}

# ======================== 壁纸生成 ========================

setup_wallpaper() {
    mkdir -p /usr/share/backgrounds/ming-os

    local asset_dark="/tmp/ming-build/assets/wallpaper-ming-dark.png"
    local asset_light="/tmp/ming-build/assets/wallpaper-ming-light.png"
    local asset_macos="/tmp/ming-build/assets/wallpaper-ming-macos.png"
    local asset_png="/tmp/ming-build/assets/wallpaper-default.png"

    # 浅色壁纸
    [[ -f "${asset_light}" ]] && cp "${asset_light}" /usr/share/backgrounds/ming-os/default-light.png
    # macOS 风格壁纸（绿山）
    [[ -f "${asset_macos}" ]] && cp "${asset_macos}" /usr/share/backgrounds/ming-os/default-macos.png

    # 默认壁纸：优先 Ming 浅色纸感壁纸，风景壁纸保留为可选资产。
    local primary=""
    if [[ -f "${asset_light}" ]]; then
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
        if command -v convert &>/dev/null; then
            convert /usr/share/backgrounds/ming-os/default.png \
                -resize 1366x768^ \
                -gravity center \
                -extent 1366x768 \
                /usr/share/backgrounds/ming-os/default-1366x768.png 2>/dev/null || \
            cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-1366x768.png
        else
            cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-1366x768.png
        fi
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
    [[ -s /usr/share/backgrounds/ming-os/default-light.png ]] || cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-light.png
    [[ -s /usr/share/backgrounds/ming-os/default-dark.png ]] || cp /usr/share/backgrounds/ming-os/default.png /usr/share/backgrounds/ming-os/default-dark.png

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

    # Minimal compatibility state: if a distribution session tries to start
    # xfce4-panel, it has no panels or plugins to expose alongside Ming shell.
    cat > "${xfconf_dir}/xfce4-panel.xml" << 'PANELXML_DOCK_ONLY'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array"/>
  <property name="plugins" type="empty"/>
</channel>
PANELXML_DOCK_ONLY

    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/ming-dock-only.desktop" << 'DOCKONLY'
[Desktop Entry]
Type=Application
Name=Ming Dock Only
Comment=Hide the legacy Xfce top taskbar and keep Dock as the only launcher
Exec=sh -c "mkdir -p ~/.cache/sessions; rm -f ~/.cache/sessions/xfce4-session-* 2>/dev/null || true"
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
DOCKONLY

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/xfce4"
    chown -R "${MING_USER}:${MING_USER}" "${autostart_dir}/ming-dock-only.desktop"
}

# ======================== Plank macOS 风格 Dock ========================

configure_plank_dock() {
    local plank_dir="/home/${MING_USER}/.config/plank/dock1"
    mkdir -p "${plank_dir}/launchers"

    # Dock 行为与外观：底部居中、轻放大、浅色半透明；避免老机动画压力过大。
    cat > "${plank_dir}/settings" << 'PLANKSETTINGS'
[PlankDockPreferences]
#当前 Dock 上的启动器（顺序即显示顺序）
DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-running-apps.dockitem;;ming-files.dockitem;;ming-edge.dockitem;;spark-store.dockitem;;garlic-claw.dockitem;;ming-update.dockitem;;ming-terminal.dockitem
#停靠位置: 0=左 1=右 2=上 3=下
Position=3
#对齐: 3=居中
Alignment=3
#图标大小（ming-scale 会按分辨率覆盖）
IconSize=40
#悬停放大开关
ZoomEnabled=true
#放大倍率：保持轻巧，避免图标跳动和低端显卡压力
ZoomPercent=148
#隐藏模式: 0=不隐藏 1=智能隐藏 2=自动隐藏 3=躲避窗口 4=窗口铺满时隐藏
HideMode=0
PinOnly=false
CurrentWorkspaceOnly=false
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

_plank_launcher() {
        local name="$1" target="$2"
        local target_path="/usr/share/applications/${target}"
        local proxy_path="/usr/share/applications/ming-dock-${name}.desktop"
        local display_name icon wm_class exec_line
        case "${name}" in
            ming-edge)
                [[ -f "${target_path}" ]] || target_path=/usr/share/applications/microsoft-edge.desktop
                [[ -f "${target_path}" ]] || target_path=/usr/share/applications/microsoft-edge-stable.desktop
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
            ming-edge) wm_class="${wm_class:-microsoft-edge}" ;;
            ming-terminal) wm_class="${wm_class:-Xfce4-terminal}" ;;
            ming-update) wm_class="${wm_class:-Zenity}" ;;
        esac
        # The running-window helper deliberately has NoDisplay=true so it
        # stays out of the application drawer.  ming-launch correctly rejects
        # hidden entries, therefore its Dock proxy must invoke the helper
        # directly while ordinary application proxies stay brokered.
        case "${name}" in
            ming-running-apps) exec_line="/usr/local/bin/ming-running-apps menu" ;;
            *) exec_line="/usr/local/bin/ming-launch --desktop-file ${target_path} --source dock" ;;
        esac
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
    "ming-running-apps:ming-running-apps.desktop" \
    "ming-edge:ming-edge.desktop" \
    "ming-files:ming-files.desktop" \
    "spark-store:spark-store.desktop" \
    "garlic-claw:garlic-claw.desktop" \
    "ming-update:ming-update.desktop" \
    "ming-settings:ming-settings.desktop" \
    "ming-terminal:ming-terminal.desktop"; do
    _plank_launcher "${launcher%%:*}" "${launcher#*:}" || missing=1
done

if [[ "$(id -u)" -eq 0 ]]; then
    chown -R "${target_user}:$(id -gn "${target_user}")" "${plank_dir}/launchers" 2>/dev/null || true
fi
exit "${missing}"
MINGREFRESHDOCK
    chmod 0755 /usr/local/sbin/ming-refresh-dock-launchers

    # Optional third-party applications frequently publish incomplete or
    # changing desktop metadata.  Keep a system-owned class-to-launcher map
    # for the running-window entry instead of rewriting package or user
    # desktop files.  StartupWMClass still wins when a package provides it.
    mkdir -p /usr/share/ming-os
    cat > /usr/share/ming-os/ming-running-apps-known-launchers.conf << 'RUNNINGAPPSMAP'
# wm_class fragment|candidate desktop file names (first installed candidate wins)
wps|wps-office-wps.desktop,wps-office.desktop,wps.desktop
wechat|wechat.desktop,wechat-universal.desktop,com.tencent.wechat.desktop
weixin|weixin.desktop,wechat.desktop,com.tencent.wechat.desktop
wine|wine.desktop,winecfg.desktop
electron|electron.desktop
RUNNINGAPPSMAP

    cat > /usr/share/applications/ming-running-apps.desktop << 'RUNNINGAPPSDESKTOP'
[Desktop Entry]
Type=Application
Name=正在运行
Name[zh_CN]=正在运行
Comment=Show and manage currently running X11 applications
Comment[zh_CN]=显示并管理正在运行的 X11 应用窗口
Exec=/usr/local/bin/ming-running-apps menu
Icon=window-list
Terminal=false
NoDisplay=true
StartupNotify=true
StartupWMClass=ming-running-apps
RUNNINGAPPSDESKTOP

    /usr/local/sbin/ming-refresh-dock-launchers "${MING_USER}" || \
        echo "[03_desktop][WARN] Late Dock launchers will be completed by 07_finalize"

    # Ming 纸感 Dock 主题
    local theme_dir="/usr/share/plank/themes/Ming"
    mkdir -p "${theme_dir}"
    cat > "${theme_dir}/dock.theme" << 'PLANKTHEME'
[PlankTheme]
TopRoundness=14
BottomRoundness=0
LineWidth=1
OuterStrokeColor=31;98;84;54
FillStartColor=255;255;255;226
FillEndColor=242;250;247;238
InnerStrokeColor=255;255;255;176

[PlankDockTheme]
HorizPadding=16
TopPadding=-8
BottomPadding=8
ItemPadding=6
IndicatorSize=4
IconShadowSize=3
UrgentBounceHeight=1.50
LaunchBounceHeight=1.05
FadeOpacity=1.0
ClickTime=220
UrgentBounceTime=600
LaunchBounceTime=520
ActiveTime=220
SlideTime=240
FadeTime=150
HideTime=150
GlowSize=14
GlowTime=10000
GlowPulseTime=1600
UrgentHueShift=86
ItemMoveTime=260
CascadeHide=false
PLANKTHEME

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
    ('ming-edge.desktop', 'microsoft-edge', 'Edge'),
    ('spark-store.desktop', 'spark-store', 'Spark'),
    ('garlic-claw.desktop', 'utilities-terminal', 'Garlic Claw'),
    ('ming-update.desktop', 'ming-update-icon', '系统更新'),
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

window_is_manageable() {
    local properties="$1"
    ! grep -Eq '_NET_WM_WINDOW_TYPE_(DESKTOP|DOCK|NOTIFICATION|TOOLTIP|MENU|DROPDOWN_MENU|POPUP_MENU|SPLASH|DND)' <<< "${properties}"
}

require_window() {
    local window_id="$1" properties=""
    x11_id_is_valid "${window_id}" || {
        printf '窗口 ID 必须是十六进制 X11 ID（例如 0x01200007）。\n' >&2
        return 22
    }
    properties="$(x11_call xprop -id "${window_id}" 2>/dev/null)" || {
        printf '未找到指定窗口：%s\n' "${window_id}" >&2
        return 3
    }
    window_is_manageable "${properties}" || {
        printf '指定窗口不是可管理的应用窗口：%s\n' "${window_id}" >&2
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

cat > /usr/local/bin/ming-running-apps << 'MINGRUNNINGAPPS'
#!/usr/bin/env bash
# Safe fallback for windows that Plank/BAMF cannot associate with a launcher.
# It only sends EWMH requests through ming-window-control; it never kills a
# process, clears a window, or touches application data.
set -u

window_control_bin="${MING_WINDOW_CONTROL_BIN:-/usr/local/bin/ming-window-control}"
desktop_dirs_value="${MING_RUNNING_APPS_DESKTOP_DIRS:-/usr/share/applications:/usr/local/share/applications}"
known_launcher_map="${MING_RUNNING_APPS_MAPPING_FILE:-/usr/share/ming-os/ming-running-apps-known-launchers.conf}"
desktop_entry_path="${MING_RUNNING_APPS_DESKTOP_FILE:-/usr/share/applications/ming-running-apps.desktop}"
plank_settings_path="${MING_PLANK_SETTINGS_FILE:-${HOME}/.config/plank/dock1/settings}"
plank_launchers_dir="${MING_PLANK_LAUNCHERS_DIR:-${HOME}/.config/plank/dock1/launchers}"
running_apps_dock_proxy_path="${MING_RUNNING_APPS_DOCK_PROXY_FILE:-/usr/share/applications/ming-dock-ming-running-apps.desktop}"
IFS=':' read -r -a desktop_dirs <<< "${desktop_dirs_value}"

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

x11_id_is_valid() {
    [[ "${1:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]
}

window_control_available() {
    [[ -x "${window_control_bin}" ]] || command -v "${window_control_bin}" >/dev/null 2>&1
}

desktop_field() {
    local desktop_file="$1" field="$2"
    awk -v key="${field}" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' \
        "${desktop_file}" 2>/dev/null
}

class_matches() {
    local observed="${1,,}" expected="${2,,}"
    observed="${observed//./,}"
    expected="${expected//./,}"
    [[ -n "${expected}" ]] || return 1
    [[ ",${observed}," == *",${expected},"* ]]
}

launcher_for_startup_wm_class() {
    local wm_class="$1" desktop_file startup_class
    for desktop_dir in "${desktop_dirs[@]}"; do
        [[ -d "${desktop_dir}" ]] || continue
        for desktop_file in "${desktop_dir}"/*.desktop; do
            [[ -f "${desktop_file}" ]] || continue
            startup_class="$(desktop_field "${desktop_file}" StartupWMClass)"
            if class_matches "${wm_class}" "${startup_class}"; then
                printf '%s\n' "${desktop_file}"
                return 0
            fi
        done
    done
    return 1
}

first_installed_launcher() {
    local candidates="$1" candidate desktop_dir desktop_file
    local -a names=()
    IFS=',' read -r -a names <<< "${candidates}"
    for candidate in "${names[@]}"; do
        for desktop_dir in "${desktop_dirs[@]}"; do
            desktop_file="${desktop_dir}/${candidate}"
            if [[ -f "${desktop_file}" ]]; then
                printf '%s\n' "${desktop_file}"
                return 0
            fi
        done
    done
    return 1
}

known_launcher_for_class() {
    local wm_class="${1,,}" fragment candidates desktop_file
    if [[ -r "${known_launcher_map}" ]]; then
        while IFS='|' read -r fragment candidates; do
            [[ -n "${fragment}" && "${fragment:0:1}" != "#" ]] || continue
            if [[ "${wm_class}" == *"${fragment,,}"* ]]; then
                desktop_file="$(first_installed_launcher "${candidates}")" || continue
                printf '%s\n' "${desktop_file}"
                return 0
            fi
        done < "${known_launcher_map}"
    fi

    # The built-in fallback keeps the entry useful on upgrades where the
    # system-owned mapping file has not been deployed yet.
    case "${wm_class}" in
        *wps*) candidates="wps-office-wps.desktop,wps-office.desktop,wps.desktop" ;;
        *wechat*|*weixin*) candidates="wechat.desktop,weixin.desktop,com.tencent.wechat.desktop" ;;
        *wine*) candidates="wine.desktop,winecfg.desktop" ;;
        *electron*) candidates="electron.desktop" ;;
        *) return 1 ;;
    esac
    first_installed_launcher "${candidates}"
}

declare -A mapping_cache=()
mapping_kind="unmapped"
mapping_file=""
mapping_source="unmapped"

resolve_launcher_mapping() {
    local wm_class="$1" key="" cached="" desktop_file=""
    key="$(LC_ALL=C tr -cd '[:alnum:].,_-' <<< "${1,,}")"
    key="${key:-unknown}"
    if [[ -n "${mapping_cache["${key}"]+present}" ]]; then
        cached="${mapping_cache["${key}"]}"
        IFS='|' read -r mapping_kind mapping_file mapping_source <<< "${cached}"
        return 0
    fi

    mapping_kind="unmapped"
    mapping_file=""
    mapping_source="unmapped"
    desktop_file="$(launcher_for_startup_wm_class "${wm_class}" 2>/dev/null || true)"
    if [[ -n "${desktop_file}" ]]; then
        mapping_kind="launcher"
        mapping_file="${desktop_file}"
        mapping_source="startup-wm-class"
    else
        desktop_file="$(known_launcher_for_class "${wm_class}" 2>/dev/null || true)"
        if [[ -n "${desktop_file}" ]]; then
            # A filename/class heuristic helps the running-window menu, but
            # it is not evidence that Plank/BAMF owns this client window.
            mapping_kind="candidate"
            mapping_file="${desktop_file}"
            mapping_source="known-class"
        fi
    fi
    mapping_cache["${key}"]="${mapping_kind}|${mapping_file}|${mapping_source}"
}

window_wm_class() {
    local properties="$1" fallback="$2" value
    value="$(sed -n 's/^WM_CLASS([^)]*) = //p' <<< "${properties}" | head -n1)"
    value="${value//\"/}"
    value="${value//, /,}"
    value="${value//$'\r'/}"
    printf '%s\n' "${value:-${fallback}}"
}

window_is_manageable() {
    local properties="$1"
    ! grep -Eq '_NET_WM_WINDOW_TYPE_(DESKTOP|DOCK|NOTIFICATION|TOOLTIP|MENU|DROPDOWN_MENU|POPUP_MENU|SPLASH|DND)' <<< "${properties}"
}

require_manageable_window() {
    local window_id="$1" properties=""
    x11_id_is_valid "${window_id}" || {
        printf 'window ID must be a hexadecimal X11 ID.\n' >&2
        return 22
    }
    properties="$(x11_call xprop -id "${window_id}" 2>/dev/null)" || {
        printf 'window ID is not an existing X11 window: %s\n' "${window_id}" >&2
        return 3
    }
    window_is_manageable "${properties}" || {
        printf 'window ID is not a manageable application window: %s\n' "${window_id}" >&2
        return 3
    }
    window_control_available || {
        printf 'ming-window-control is unavailable.\n' >&2
        return 127
    }
}

collect_windows() {
    local listing="" window_line="" window_id workspace host wmctrl_class title
    local properties="" wm_class="" minimized skip_taskbar actionable
    declare -A seen_window_ids=()

    listing="$(x11_call wmctrl -lx 2>/dev/null || true)"
    while IFS= read -r window_line; do
        [[ -n "${window_line}" ]] || continue
        read -r window_id workspace host wmctrl_class title <<< "${window_line}"
        x11_id_is_valid "${window_id}" || continue
        [[ -z "${seen_window_ids[${window_id}]+present}" ]] || continue
        seen_window_ids[${window_id}]=true
        properties="$(x11_call xprop -id "${window_id}" 2>/dev/null)" || continue
        window_is_manageable "${properties}" || continue

        wm_class="$(window_wm_class "${properties}" "${wmctrl_class}")"
        minimized=false
        skip_taskbar=false
        grep -q '_NET_WM_STATE_HIDDEN' <<< "${properties}" && minimized=true
        grep -q '_NET_WM_STATE_SKIP_TASKBAR' <<< "${properties}" && skip_taskbar=true
        actionable=false
        window_control_available && actionable=true
        resolve_launcher_mapping "${wm_class}"
        printf '%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' \
            "${window_id}" "${title:-${wm_class}}" "${wm_class}" "${minimized}" \
            "${skip_taskbar}" "${mapping_kind}" "${mapping_file}" "${mapping_source}" "${actionable}"
    done <<< "${listing}"
}

dock_settings_include_running_apps_item() {
    local dock_items=""
    [[ -r "${plank_settings_path}" ]] || return 1
    dock_items="$(awk 'index($0, "DockItems=") == 1 { print substr($0, 11); exit }' "${plank_settings_path}" 2>/dev/null)"
    [[ -n "${dock_items}" ]] || return 1
    [[ ";;${dock_items};;" == *";;ming-running-apps.dockitem;;"* ]]
}

running_apps_dock_item_present() {
    local dock_item="${plank_launchers_dir}/ming-running-apps.dockitem"
    dock_settings_include_running_apps_item || return 1
    [[ -s "${dock_item}" && -s "${running_apps_dock_proxy_path}" ]] || return 1
    grep -qxF "Launcher=file://${running_apps_dock_proxy_path}" "${dock_item}" && \
        grep -qxF 'Exec=/usr/local/bin/ming-running-apps menu' "${running_apps_dock_proxy_path}"
}

running_apps_entry_available() {
    window_control_available || return 1
    command -v zenity >/dev/null 2>&1 || return 1
    [[ -s "${desktop_entry_path}" ]] || return 1
    grep -qxF 'Exec=/usr/local/bin/ming-running-apps menu' "${desktop_entry_path}"
}

list_json() {
    local records_file entry_available=false dock_item=false rc=0
    records_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/ming-running-apps.XXXXXX" 2>/dev/null)" \
        || records_file="$(mktemp /tmp/ming-running-apps.XXXXXX)" \
        || return 1
    collect_windows > "${records_file}"
    running_apps_entry_available && entry_available=true
    running_apps_dock_item_present && dock_item=true
    MING_RUNNING_ENTRY_AVAILABLE="${entry_available}" MING_RUNNING_DOCK_ITEM="${dock_item}" \
    MING_RUNNING_DESKTOP_FILE="${desktop_entry_path}" python3 - "${records_file}" <<'PY'
import json
import os
import sys

fields = open(sys.argv[1], "rb").read().split(b"\0")
if fields and not fields[-1]:
    fields.pop()
windows = []
for start in range(0, len(fields), 9):
    record = fields[start:start + 9]
    if len(record) != 9:
        continue
    value = [part.decode("utf-8", "replace") for part in record]
    window = {
        "id": value[0],
        "title": value[1],
        "wm_class": value[2],
        "minimized": value[3] == "true",
        "skip_taskbar": value[4] == "true",
        "mapping": {
            "kind": value[5],
            "desktop_file": value[6] or None,
            "source": value[7],
        },
        "actionable": value[8] == "true",
    }
    windows.append(window)

unmapped_minimized = [
    window for window in windows
    if window["minimized"] and window["mapping"]["kind"] != "launcher"
]
print(json.dumps({
    "windows": windows,
    "unmapped_minimized": unmapped_minimized,
    "entry": {
        "available": os.environ.get("MING_RUNNING_ENTRY_AVAILABLE") == "true",
        "dock_item": os.environ.get("MING_RUNNING_DOCK_ITEM") == "true",
        "desktop_file": os.environ["MING_RUNNING_DESKTOP_FILE"],
    },
}, ensure_ascii=False))
PY
    rc=$?
    rm -f "${records_file}"
    return "${rc}"
}

run_action() {
    local action="$1" window_id="$2"
    require_manageable_window "${window_id}" || return $?
    "${window_control_bin}" "${action}" --window-id "${window_id}"
}

show_menu() {
    local payload="" rows="" selected="" action="" window_id title state
    local -a dialog=()
    command -v zenity >/dev/null 2>&1 || {
        printf 'ming-running-apps list --json\n' >&2
        return 1
    }
    payload="$(list_json)" || return $?
    rows="$(MING_RUNNING_APPS_JSON="${payload}" python3 - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["MING_RUNNING_APPS_JSON"])
except (KeyError, json.JSONDecodeError):
    payload = {"windows": []}
for window in payload.get("windows", []):
    state = []
    if window.get("minimized"):
        state.append("已最小化")
    if window.get("skip_taskbar"):
        state.append("跳过任务栏")
    if window.get("mapping", {}).get("kind") == "unmapped":
        state.append("运行中入口")
    elif window.get("mapping", {}).get("kind") == "candidate":
        state.append("候选启动器")
    print("%s\t%s\t%s" % (
        window.get("id", ""),
        str(window.get("title", "")).replace("\t", " ").replace("\n", " "),
        "，".join(state) or "可管理",
    ))
PY
)"
    [[ -n "${rows}" ]] || {
        zenity --info --title="正在运行" --text="没有可管理的 X11 应用窗口。" 2>/dev/null || true
        return 0
    }
    dialog=(--list --title="正在运行" --text="选择要恢复、聚焦或关闭的窗口。" \
        --column="窗口 ID" --column="应用" --column="状态" --hide-column=1 --print-column=1)
    while IFS=$'\t' read -r window_id title state; do
        [[ -n "${window_id}" ]] || continue
        dialog+=("${window_id}" "${title}" "${state}")
    done <<< "${rows}"
    selected="$(zenity "${dialog[@]}" 2>/dev/null)" || return 0
    [[ -n "${selected}" ]] || return 0
    action="$(zenity --list --radiolist --title="正在运行" --text="选择对该窗口执行的操作。" \
        --column="" --column="操作" true "恢复并聚焦" false "仅聚焦" false "关闭窗口" 2>/dev/null)" || return 0
    case "${action}" in
        恢复并聚焦) run_action restore "${selected}" ;;
        仅聚焦) run_action focus "${selected}" ;;
        关闭窗口) run_action close "${selected}" ;;
        *) return 0 ;;
    esac
}

case "${1:-menu}" in
    list)
        [[ "${2:-}" == "--json" && "$#" -eq 2 ]] || {
            printf 'Usage: %s list --json\n' "$0" >&2
            exit 2
        }
        list_json
        ;;
    restore|focus|close)
        [[ "${2:-}" == "--window-id" && -n "${3:-}" && "$#" -eq 3 ]] || {
            printf 'Usage: %s %s --window-id 0x01200007\n' "$0" "$1" >&2
            exit 2
        }
        run_action "$1" "$3"
        ;;
    menu)
        [[ "$#" -eq 1 ]] || { printf 'Usage: %s menu\n' "$0" >&2; exit 2; }
        show_menu
        ;;
    *)
        printf 'Usage: %s {menu|list --json|restore|focus|close --window-id HEX}\n' "$0" >&2
        exit 2
        ;;
esac
MINGRUNNINGAPPS
    chmod 0755 /usr/local/bin/ming-running-apps

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

window_control_bin="${MING_WINDOW_CONTROL_BIN:-/usr/local/bin/ming-window-control}"
running_apps_bin="${MING_RUNNING_APPS_BIN:-/usr/local/bin/ming-running-apps}"

plank_mapping_snapshot() {
    dock_pin_only=false
    dock_pin_only_valid=false
    dock_settings="${MING_PLANK_SETTINGS_FILE:-${HOME}/.config/plank/dock1/settings}"
    if [[ ! -r "${dock_settings}" ]]; then
        dock_settings="/etc/skel/.config/plank/dock1/settings"
    fi
    if grep -qx 'PinOnly=false' "${dock_settings}" 2>/dev/null; then
        dock_pin_only=false
        dock_pin_only_valid=true
    elif grep -qx 'PinOnly=true' "${dock_settings}" 2>/dev/null; then
        dock_pin_only=true
    fi

    bamfdaemon_available=false
    bamfdaemon_running=false
    command -v bamfdaemon >/dev/null 2>&1 && bamfdaemon_available=true
    pgrep -u "$(id -u)" -x bamfdaemon >/dev/null 2>&1 && bamfdaemon_running=true
}

running_apps_snapshot() {
    running_apps_available=false
    running_apps_entry_available=false
    running_apps_dock_item=false
    running_apps_mapped_count=0
    unmapped_minimized_count=0
    running_apps_json='{"windows":[],"unmapped_minimized":[],"entry":{"available":false,"dock_item":false}}'
    if [[ -x "${running_apps_bin}" ]]; then
        running_apps_available=true
        running_apps_json="$("${running_apps_bin}" list --json 2>>"${log_file}" || true)"
    fi
    local summary=""
    summary="$(MING_RUNNING_APPS_JSON="${running_apps_json}" python3 - <<'PY'
import json
import os

try:
    value = json.loads(os.environ.get("MING_RUNNING_APPS_JSON", "{}"))
except json.JSONDecodeError:
    value = {}
windows = value.get("windows") if isinstance(value.get("windows"), list) else []
unmapped = value.get("unmapped_minimized")
if not isinstance(unmapped, list):
    unmapped = []
entry = value.get("entry") if isinstance(value.get("entry"), dict) else {}
mapped = sum(1 for window in windows if isinstance(window, dict)
             and isinstance(window.get("mapping"), dict)
             and window["mapping"].get("kind") == "launcher")
print("%s %s %s %s" % (
    str(bool(entry.get("available"))).lower(),
    str(bool(entry.get("dock_item"))).lower(),
    mapped,
    len(unmapped),
))
PY
)"
    read -r running_apps_entry_available running_apps_dock_item \
        running_apps_mapped_count unmapped_minimized_count <<< "${summary}"
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
    if [[ ! -x "${window_control_bin}" ]]; then
        log "window manager helper is missing"
        return 0
    fi
    status="$("${window_control_bin}" status --json 2>>"${log_file}" || true)"
    local flags=""
    flags="$(MING_WINDOW_MANAGER_STATUS="${status}" python3 - <<'PY'
import json
import os

try:
    value = json.loads(os.environ.get("MING_WINDOW_MANAGER_STATUS", "{}"))
except json.JSONDecodeError:
    value = {}
xfwm = value.get("xfwm") if isinstance(value.get("xfwm"), dict) else {}
print("%s %s %s" % (
    str(bool(xfwm.get("running"))).lower(),
    str(bool(value.get("ewmh"))).lower(),
    str(bool(value.get("healthy"))).lower(),
))
PY
)"
    local parsed_running=false parsed_ewmh=false parsed_healthy=false
    read -r parsed_running parsed_ewmh parsed_healthy <<< "${flags}"
    if ${parsed_running}; then
        window_manager_running=true
        window_manager_visible=true
    fi
    if ${parsed_ewmh}; then
        window_manager_ewmh=true
        window_manager_stacking="ewmh"
    fi
    ${parsed_healthy} && window_manager_healthy=true
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
        if grep -q '_NET_WM_WINDOW_TYPE_DOCK' <<<"${properties}" && grep -q '_NET_WM_STATE_ABOVE' <<<"${properties}"; then
            printf 'dock+above'
        elif grep -q '_NET_WM_WINDOW_TYPE_DOCK' <<<"${properties}"; then
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
    if [[ -x "${window_control_bin}" ]]; then
        "${window_control_bin}" repair >>"${log_file}" 2>&1 \
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
        if [[ "${dock_stacking}" == "dock" || "${dock_stacking}" == "dock+above" ]]; then
            dock_healthy=true
        fi
    else
        dock_geometry="${dock_geometry:-unknown}"
    fi
fi

broker_running=false
has_proc 'ming-launch[[:space:]]+--server' && broker_running=true

plank_mapping_snapshot
running_apps_snapshot

# A missing launcher association is observable degradation, not a reason to
# restart a healthy Dock.  The running-window entry remains usable for every
# validated X11 client, including minimized SKIP_TASKBAR clients.
dock_degraded=false
dock_degradation="none"
if ! ${dock_pin_only_valid}; then
    dock_degraded=true
    dock_degradation="pin-only-enabled"
elif ! ${bamfdaemon_running}; then
    dock_degraded=true
    dock_degradation="bamfdaemon-not-running"
elif ! ${running_apps_entry_available}; then
    dock_degraded=true
    dock_degradation="running-apps-entry-unavailable"
elif ! ${running_apps_dock_item}; then
    dock_degraded=true
    dock_degradation="running-apps-dock-item-unavailable"
elif (( unmapped_minimized_count > 0 )); then
    dock_degraded=true
    dock_degradation="unmapped-minimized"
fi

if ${json}; then
    MING_DESKTOP_RUNNING="${desktop_running}" MING_DESKTOP_VISIBLE="${desktop_visible}" \
    MING_DESKTOP_STACKING="${desktop_stacking}" MING_DESKTOP_GEOMETRY="${desktop_geometry}" \
    MING_DOCK_RUNNING="${dock_running}" MING_DOCK_VISIBLE="${dock_visible}" \
    MING_DOCK_STACKING="${dock_stacking}" MING_DOCK_GEOMETRY="${dock_geometry}" \
    MING_BROKER_RUNNING="${broker_running}" MING_WM_RUNNING="${window_manager_running}" \
    MING_WM_VISIBLE="${window_manager_visible}" MING_WM_STACKING="${window_manager_stacking}" \
    MING_WM_GEOMETRY="${window_manager_geometry}" MING_WM_EWMH="${window_manager_ewmh}" \
    MING_WM_HEALTHY="${window_manager_healthy}" MING_WM_LOG="${window_manager_log}" \
    MING_DOCK_PIN_ONLY="${dock_pin_only}" MING_DOCK_PIN_ONLY_VALID="${dock_pin_only_valid}" \
    MING_BAMF_AVAILABLE="${bamfdaemon_available}" MING_BAMF_RUNNING="${bamfdaemon_running}" \
    MING_DOCK_DEGRADED="${dock_degraded}" MING_DOCK_DEGRADATION="${dock_degradation}" \
    MING_RUNNING_APPS_AVAILABLE="${running_apps_available}" \
    MING_RUNNING_APPS_ENTRY_AVAILABLE="${running_apps_entry_available}" \
    MING_RUNNING_APPS_DOCK_ITEM="${running_apps_dock_item}" \
    MING_RUNNING_APPS_MAPPED_COUNT="${running_apps_mapped_count}" \
    MING_UNMAPPED_MINIMIZED_COUNT="${unmapped_minimized_count}" \
    MING_RUNNING_APPS_JSON="${running_apps_json}" python3 - <<'PY'
import json
import os

boolean = lambda name: os.environ.get(name) == "true"
component = lambda prefix: {"running": boolean(prefix + "_RUNNING"),
                            "visible": boolean(prefix + "_VISIBLE"),
                            "stacking": os.environ[prefix + "_STACKING"],
                            "geometry": os.environ[prefix + "_GEOMETRY"]}
try:
    running_apps_payload = json.loads(os.environ.get("MING_RUNNING_APPS_JSON", "{}"))
except json.JSONDecodeError:
    running_apps_payload = {}
windows = running_apps_payload.get("windows")
if not isinstance(windows, list):
    windows = []
unmapped_minimized = running_apps_payload.get("unmapped_minimized")
if not isinstance(unmapped_minimized, list):
    unmapped_minimized = []
payload = {
    "desktop": component("MING_DESKTOP"),
    "dock": {**component("MING_DOCK"),
             "pin_only": boolean("MING_DOCK_PIN_ONLY"),
             "pin_only_valid": boolean("MING_DOCK_PIN_ONLY_VALID"),
             "bamfdaemon": {"available": boolean("MING_BAMF_AVAILABLE"),
                            "running": boolean("MING_BAMF_RUNNING")},
             "mapped_windows": int(os.environ["MING_RUNNING_APPS_MAPPED_COUNT"]),
             "degraded": boolean("MING_DOCK_DEGRADED"),
             "degradation": os.environ["MING_DOCK_DEGRADATION"]},
    "launch_broker": {"running": boolean("MING_BROKER_RUNNING"), "visible": False,
                      "stacking": "n/a", "geometry": "n/a"},
    "window_manager": {"running": boolean("MING_WM_RUNNING"),
                       "visible": boolean("MING_WM_VISIBLE"),
                       "stacking": os.environ["MING_WM_STACKING"],
                       "geometry": os.environ["MING_WM_GEOMETRY"],
                       "ewmh": boolean("MING_WM_EWMH"),
                       "healthy": boolean("MING_WM_HEALTHY"),
                       "log": os.environ["MING_WM_LOG"]},
    "windows": windows,
    "unmapped_minimized": unmapped_minimized,
    "running_apps": {"available": boolean("MING_RUNNING_APPS_AVAILABLE"),
                     "entry_available": boolean("MING_RUNNING_APPS_ENTRY_AVAILABLE"),
                     "dock_item": boolean("MING_RUNNING_APPS_DOCK_ITEM"),
                     "mapped_windows": int(os.environ["MING_RUNNING_APPS_MAPPED_COUNT"]),
                     "unmapped_minimized": int(os.environ["MING_UNMAPPED_MINIMIZED_COUNT"])},
}
print(json.dumps(payload, ensure_ascii=False))
PY
else
    printf 'desktop: running=%s visible=%s stacking=%s geometry=%s\n' "${desktop_running}" "${desktop_visible}" "${desktop_stacking}" "${desktop_geometry}"
    printf 'dock: running=%s visible=%s stacking=%s geometry=%s pin_only=%s bamfdaemon=%s degraded=%s degradation=%s\n' \
        "${dock_running}" "${dock_visible}" "${dock_stacking}" "${dock_geometry}" \
        "${dock_pin_only}" "${bamfdaemon_running}" "${dock_degraded}" "${dock_degradation}"
    printf 'launch_broker: running=%s visible=false stacking=n/a geometry=n/a\n' "${broker_running}"
    printf 'window_manager: running=%s ewmh=%s healthy=%s log=%s\n' \
        "${window_manager_running}" "${window_manager_ewmh}" "${window_manager_healthy}" "${window_manager_log}"
    printf 'running_apps: available=%s entry=%s mapped=%s unmapped_minimized=%s\n' \
        "${running_apps_available}" "${running_apps_entry_available}" \
        "${running_apps_mapped_count}" "${unmapped_minimized_count}"
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

phone_desktop_ready() {
    [[ -s "${HOME}/.cache/ming-os/ming-phone-desktop.ready" ]]
}

wait_phone_desktop_ready() {
    local log_file="$1"
    local attempt
    # The session coordinator owns the fixed 8-second desktop startup
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
PLANK_STARTUP_TIMEOUT=8
PLANK_PROBE_TIMEOUT=2

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
DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-running-apps.dockitem;;ming-files.dockitem;;ming-edge.dockitem;;spark-store.dockitem;;garlic-claw.dockitem;;ming-update.dockitem;;ming-terminal.dockitem
Position=3
Alignment=3
IconSize=40
ZoomEnabled=true
ZoomPercent=148
HideMode=0
PinOnly=false
CurrentWorkspaceOnly=false
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

plank_settings_include_running_apps_item() {
    local settings="$1" dock_items=""
    [[ -r "${settings}" ]] || return 1
    dock_items="$(awk 'index($0, "DockItems=") == 1 { print substr($0, 11); exit }' "${settings}" 2>/dev/null)"
    [[ -n "${dock_items}" ]] || return 1
    [[ ";;${dock_items};;" == *";;ming-running-apps.dockitem;;"* ]]
}

running_apps_dock_item_healthy() {
    local settings="$1" launchers_dir="$2"
    local dock_item="${launchers_dir}/ming-running-apps.dockitem"
    local proxy_path="${MING_RUNNING_APPS_DOCK_PROXY_FILE:-/usr/share/applications/ming-dock-ming-running-apps.desktop}"
    plank_settings_include_running_apps_item "${settings}" || return 1
    [[ -s "${dock_item}" && -s "${proxy_path}" ]] || return 1
    grep -qxF "Launcher=file://${proxy_path}" "${dock_item}" && \
        grep -qxF 'Exec=/usr/local/bin/ming-running-apps menu' "${proxy_path}"
}

ensure_plank_settings() {
    local plank_dir="${HOME}/.config/plank/dock1"
    local settings="${MING_PLANK_SETTINGS_FILE:-${plank_dir}/settings}"
    local launchers_dir="${MING_PLANK_LAUNCHERS_DIR:-${plank_dir}/launchers}"
    local skel_settings="/etc/skel/.config/plank/dock1/settings"
    local restored=false running_item_added=false running_item_repaired=false
    mkdir -p "$(dirname "${settings}")" "${launchers_dir}" 2>/dev/null || true
    if [[ ! -f "${settings}" ]]; then
        if [[ -s "${skel_settings}" ]]; then
            cp "${skel_settings}" "${settings}" 2>/dev/null || write_default_plank_settings "${settings}"
        else
            write_default_plank_settings "${settings}"
        fi
        restored=true
        log "restored complete Plank settings profile"
    fi
    if grep -q '^HideMode=' "${settings}"; then
        sed -i 's/^HideMode=.*/HideMode=0/' "${settings}" 2>/dev/null || true
    else
        printf 'HideMode=0\n' >>"${settings}"
    fi
    if grep -q '^PinOnly=' "${settings}"; then
        sed -i 's/^PinOnly=.*/PinOnly=false/' "${settings}" 2>/dev/null || true
    else
        printf 'PinOnly=false\n' >>"${settings}"
    fi
    if plank_settings_include_running_apps_item "${settings}"; then
        :
    elif grep -q '^DockItems=' "${settings}"; then
        sed -i 's|^DockItems=.*|&;;ming-running-apps.dockitem|' "${settings}" 2>/dev/null || true
        running_item_added=true
    else
        printf 'DockItems=ming-running-apps.dockitem\n' >>"${settings}"
        running_item_added=true
    fi
    if ! running_apps_dock_item_healthy "${settings}" "${launchers_dir}"; then
        running_item_repaired=true
    fi
    if { ${restored} || ${running_item_added} || ${running_item_repaired}; } && [[ -x /usr/local/sbin/ming-refresh-dock-launchers ]]; then
        /usr/local/sbin/ming-refresh-dock-launchers "$(id -un)" >>"${log_file}" 2>&1 || \
            log "Dock launcher refresh after settings restore failed"
    fi
}

ensure_bamfdaemon() {
    pgrep -u "$(id -u)" -x bamfdaemon >/dev/null 2>&1 && return 0
    command -v bamfdaemon >/dev/null 2>&1 || {
        log "bamfdaemon is unavailable; running-window fallback remains available"
        return 1
    }
    (nohup bamfdaemon >>"${log_file}" 2>&1 &) || return 1
    for _bamf_try in $(seq 1 8); do
        pgrep -u "$(id -u)" -x bamfdaemon >/dev/null 2>&1 && return 0
        sleep 0.25
    done
    log "bamfdaemon did not become ready; retaining Plank and running-window fallback"
    return 1
}

x11_call() {
    command -v timeout >/dev/null 2>&1 || return 127
    timeout --foreground 2s "$@"
}

valid_window_id() {
    [[ "${1:-}" =~ ^0[xX][0-9a-fA-F]+$ ]]
}

plank_window_id() {
    command -v wmctrl >/dev/null 2>&1 || return 1
    local fallback_id="" candidate_id candidate_geometry screen="" candidate_count=0
    local xprop_available=false
    command -v xprop >/dev/null 2>&1 && xprop_available=true
    ${xprop_available} || screen="$(screen_geometry)"
    local dock_candidates=""
    dock_candidates="$(x11_call wmctrl -lx 2>/dev/null | awk 'tolower($3) ~ /plank/ { print $1 }' || true)"
    while read -r candidate_id; do
        [[ "${candidate_id}" =~ ^0[xX][0-9a-fA-F]+$ ]] || continue
        candidate_count=$((candidate_count + 1))
        (( candidate_count <= 3 )) || break
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
    pgrep -u "$(id -u)" -x plank >/dev/null 2>&1 || { printf 'not-running\n'; return; }
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

plank_health_reason_bounded() {
    local budget="${1:-${PLANK_PROBE_TIMEOUT}}"
    [[ "${budget}" =~ ^[1-9][0-9]*$ ]] || budget="${PLANK_PROBE_TIMEOUT}"
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${budget}s" "$0" --reason 2>/dev/null || true
    else
        plank_health_reason
    fi
}

start_plank_bounded() {
    if command -v timeout >/dev/null 2>&1; then
        timeout --foreground "${PLANK_STARTUP_TIMEOUT}s" "$0" --start-internal
    else
        start_plank
    fi
}

plank_window_visible() {
    [[ "$(plank_health_reason)" == "healthy" ]]
}

repair_plank_stacking() {
    local window_id
    window_id="$(plank_window_id)"
    valid_window_id "${window_id}" || return 1
    x11_call wmctrl -i -r "${window_id}" -b add,above,sticky >/dev/null 2>&1 || return 1
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
        log "not-above: ABOVE state is absent; requesting one non-destructive promotion"
        repair_plank_stacking || log "ABOVE promotion request was not accepted"
    fi
    return 0
}

stop_plank() {
    pkill -TERM -u "$(id -u)" -x plank >/dev/null 2>&1 || true
}

start_plank() {
    command -v plank >/dev/null 2>&1 || return 1
    stop_legacy_dock
    ensure_plank_settings
    ensure_bamfdaemon || true
    local reason
    reason="$(plank_health_reason_bounded)"
    reason="${reason:-probe-timeout}"
    if [[ "${reason}" == "healthy" ]]; then
        diagnose_and_promote_stacking
        return 0
    fi
    log "Plank health failure: ${reason}; starting recovery"
    if pgrep -u "$(id -u)" -x plank >/dev/null 2>&1; then
        stop_plank
        sleep 1
    fi
    export DISPLAY="${DISPLAY:-:0}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
    log "starting Plank DISPLAY=${DISPLAY} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
    (nohup plank >>"${log_file}" 2>&1 &) || return 1
    # Each health probe is separately capped; start_plank_bounded wraps this
    # complete recovery path in the coordinator's fixed eight-second budget.
    for _ready_try in $(seq 1 32); do
        reason="$(plank_health_reason_bounded)"
        reason="${reason:-probe-timeout}"
        if [[ "${reason}" == "healthy" ]]; then
            diagnose_and_promote_stacking
            log "Plank recovery succeeded"
            return 0
        fi
        sleep 0.25
    done
    log "Plank recovery failed: $(plank_health_reason_bounded)"
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
    start_plank_bounded
}

case "${1:-start}" in
    --check)
        [[ "$(plank_health_reason)" == "healthy" ]]
        ;;
    --reason)
        plank_health_reason
        ;;
    --start-internal)
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
            start_plank_bounded || log "Plank recovery attempt failed"
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
    probe_timeout pgrep -u "$(id -u)" -f \
        '(^|[[:space:]])python3([0-9.]*)?[[:space:]]+/usr/local/bin/ming-phone-desktop([[:space:]]|$)|(^|[[:space:]])/usr/local/bin/ming-phone-desktop([[:space:]]|$)' \
        >/dev/null 2>&1
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
    probe_timeout pgrep -u "$(id -u)" -x plank >/dev/null 2>&1
}

plank_window_visible() {
    plank_running || return 1
    command -v wmctrl >/dev/null 2>&1 || return 0
    command -v ming-plank-watchdog >/dev/null 2>&1 || return 1
    run_bounded "${PROBE_TIMEOUT}" /usr/local/bin/ming-plank-watchdog --check >/dev/null 2>&1
}

picom_running() {
    probe_timeout pgrep -u "$(id -u)" -x picom >/dev/null 2>&1
}

compositor_profile() {
    local settings="${HOME}/.config/ming-os/settings.json"
    local profile=""
    profile="$(sed -n 's/.*"compositor_profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${settings}" 2>/dev/null | head -n1)"
    case "${profile}" in auto|software|off) printf '%s\n' "${profile}" ;; *) printf 'auto\n' ;; esac
}

compositor_enabled() {
    [[ "$(compositor_profile)" != off ]]
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
    local started_at finished_at deadline_at
    if plank_window_visible; then
        plank_recovered=true
        return 0
    fi
    command -v ming-plank-watchdog >/dev/null 2>&1 || {
        log 'ming-plank-watchdog is unavailable'
        return 1
    }
    plank_restarts=$((plank_restarts + 1))
    started_at="$(now_ms)"
    deadline_at=$((started_at + PLANK_STARTUP_DEADLINE * 1000))
    log "starting Plank Dock (deadline=${PLANK_STARTUP_DEADLINE}s)"
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
    command -v picom >/dev/null 2>&1 || return 1
    picom_running && return 0
    log 'starting Picom xrender fallback'
    (nohup picom --config /etc/xdg/picom/picom-fallback.conf \
        >>"${health_log}" 2>&1 &) || true
    wait_for_process_until picom "${deadline_at}"
}

start_picom() {
    if ! compositor_enabled; then
        pkill -TERM -u "$(id -u)" -x picom >/dev/null 2>&1 || true
        picom_recovered=true
        return 0
    fi
    local started_at finished_at deadline_at
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
    local xfdesktop=false dock=false dock_visible=false compositor=false
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
    phone_pid_count="$(process_count phone)"
    plank_pid_count="$(process_count plank)"
    picom_pid_count="$(process_count picom)"
    (( phone_pid_count > 1 )) && phone_duplicates=$((phone_pid_count - 1))
    (( plank_pid_count > 1 )) && plank_duplicates=$((plank_pid_count - 1))
    (( picom_pid_count > 1 )) && picom_duplicates=$((picom_pid_count - 1))
    if ${compositor}; then
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
    MING_PICOM_RUNNING="${compositor}" MING_PICOM_ENABLED="$(compositor_enabled && printf true || printf false)" MING_PICOM_BACKEND="${compositor_backend}" \
    MING_PHONE_PID_COUNT="${phone_pid_count}" MING_PLANK_PID_COUNT="${plank_pid_count}" \
    MING_PICOM_PID_COUNT="${picom_pid_count}" MING_PHONE_DUPLICATES="${phone_duplicates}" \
    MING_PLANK_DUPLICATES="${plank_duplicates}" MING_PICOM_DUPLICATES="${picom_duplicates}" \
    MING_PHONE_ELAPSED_MS="${phone_elapsed_ms}" MING_PLANK_ELAPSED_MS="${plank_elapsed_ms}" \
    MING_PICOM_ELAPSED_MS="${picom_elapsed_ms}" MING_PHONE_RESTARTS="${phone_restarts}" \
    MING_PLANK_RESTARTS="${plank_restarts}" MING_PICOM_RESTARTS="${picom_restarts}" \
    MING_PHONE_RECOVERED="${phone_recovered}" MING_PLANK_RECOVERED="${plank_recovered}" \
    MING_PICOM_RECOVERED="${picom_recovered}" \
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
        "enabled": boolean("MING_PICOM_ENABLED"),
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
    and (not payload["picom"]["enabled"] or payload["picom"]["running"])
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
    *)
        printf 'Usage: %s --session|--once|--check\n' "$0" >&2
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

    cat > /usr/local/bin/ming-lock << 'MINGLOCK'
#!/usr/bin/env bash
set -uo pipefail

account_status="$(/usr/local/sbin/ming-account-control status --json --user "$(id -un)" 2>/dev/null || true)"
if grep -Fq '"password_set": false' <<< "${account_status}"; then
    # A passwordless account has nothing to authenticate. Activate the saver
    # without locking so pointer/key input resumes the current session directly.
    xfce4-screensaver-command --activate >/tmp/ming-lock.log 2>&1 || true
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
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(31,98,84,0.09);
  border-radius: 12px;
  padding: 12px;
  color: #1C2320;
}
.tile:hover {
  background: rgba(255,255,255,0.94);
  border-color: rgba(47,138,125,0.22);
  box-shadow: 0 8px 20px rgba(30,70,58,0.07);
}
.tile label { color: #1C2320; font-weight: 700; }
.danger {
  background: rgba(255,247,247,0.90);
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
            ('退出/关机', 'system-shutdown', 'xfce4-session-logout'),
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
  background: rgba(255,255,255,0.82);
  color: #1C2320;
  border: 1px solid rgba(31,98,84,0.09);
  padding: 0 12px;
}
.app-tile {
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(31,98,84,0.09);
  border-radius: 12px;
  padding: 10px;
  color: #1C2320;
}
.app-tile:hover {
  background: rgba(255,255,255,0.94);
  border-color: rgba(47,138,125,0.22);
  box-shadow: 0 8px 20px rgba(30,70,58,0.07);
}
.app-name { font-size: 11px; font-weight: 700; color: #1C2320; }
.quick-button {
  border-radius: 12px;
  padding: 9px 12px;
  background: rgba(255,255,255,0.78);
  color: #1C2320;
}
.quick-button:hover { background: rgba(47,138,125,0.10); }
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
        try:
            info = Gio.DesktopAppInfo.new_from_filename(app['path'])
            if info:
                info.launch([], None)
                return
        except Exception:
            pass
        command = app['exec'].replace('%U', '').replace('%u', '').replace('%F', '').replace('%f', '').strip()
        subprocess.Popen(command, shell=True)

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

    cat > "${desktop}/Ming 设置.desktop" << CONTROL
[Desktop Entry]
Name=Ming 设置
Comment=不用记命令，点按钮完成常见电脑维护
Exec=/usr/local/bin/ming-control-center
Icon=ming-control-center
Terminal=false
Type=Application
Categories=Settings;System;
StartupNotify=true
CONTROL

chmod +x "${desktop}/Ming 设置.desktop" 2>/dev/null || true
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
    ming-phone-desktop --sync >/tmp/ming-phone-desktop-sync.log 2>&1 || true
else
    sync_apps
fi
for item in "${desktop}/Ming 设置.desktop"; do
    [[ -f "${item}" ]] || continue
    ln -sfn "${item}" "${common_dir}/$(basename "${item}")" 2>/dev/null || true
done

if [[ "${1:-}" == "--watch" ]]; then
    while true; do
        if command -v inotifywait >/dev/null 2>&1; then
            inotifywait -q -e close_write,create,move,delete /usr/share/applications "${HOME}/.local/share/applications" >/dev/null 2>&1 || sleep 5
        else
            sleep 20
        fi
        if command -v ming-phone-desktop >/dev/null 2>&1; then
            ming-phone-desktop --sync >/tmp/ming-phone-desktop-sync.log 2>&1 || true
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
            if pkexec /usr/local/bin/ming-install-wechat >/tmp/ming-install-wechat.log 2>&1 || sudo /usr/local/bin/ming-install-wechat >/tmp/ming-install-wechat.log 2>&1; then
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
            if pkexec /usr/local/bin/ming-install-spark-store >/tmp/ming-spark.log 2>&1 || sudo /usr/local/bin/ming-install-spark-store >/tmp/ming-spark.log 2>&1; then
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
    ('AI 助手', 'utilities-terminal', '打开 Garlic Claw', 'xfce4-terminal --hide-menubar --title="Garlic Claw" -e garlic-claw'),
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
  background: rgba(255,255,255,0.76);
  border: 1px solid rgba(31,98,84,0.13);
  border-radius: 10px;
  padding: 12px;
  color: #1D2421;
}
.tile:hover {
  background: rgba(255,255,255,0.94);
  border-color: rgba(47,174,143,0.36);
}
.tile:active {
  background: rgba(47,174,143,0.14);
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

        footer = Gtk.Label(label='Ming OS 26.3.2 · Debian Trixie')
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
    # WPS is optional in 26.3.2. Keep ming-install-wps.desktop in App Library,
    # but do not create a desktop or Dock launcher for it.
}

# ======================== Picom 用户级配置 ========================

configure_picom() {
    mkdir -p /home/${MING_USER}/.config/picom
    cat > /home/${MING_USER}/.config/picom/picom.conf << 'PICOMCFG'
# Ming OS 26.3.2 Picom 配置 - 老显卡/虚拟机稳定路径
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
  "class_g = 'Microsoft-edge'",
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
  "class_g = 'Microsoft-edge'",
  "window_type = 'dock'",
  "window_type = 'desktop'",
];

# ---- 圆角窗口 ----
corner-radius = 12;
rounded-corners-exclude = [
  "class_g = 'Microsoft-edge'",
  "window_type = 'dock'",
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
  tooltip = { fade = true; shadow = true; opacity = 0.90; focus = true; };
  dock = { shadow = false; opacity = 0.92; };
  dnd = { shadow = false; };
  dropdown_menu = { shadow = true; opacity = 1.0; };
  popup_menu = { shadow = true; opacity = 1.0; };
  utility = { shadow = true; opacity = 1.0; };
  notification = { shadow = true; opacity = 0.94; };
};

detect-rounded-corners = true;
detect-client-opacity = false;
detect-transient = true;
detect-client-leader = true;
PICOMCFG

    # Fallback 配置 (老显卡 xrender, 无 blur, 无动画)
    mkdir -p /etc/xdg/picom
    cat > /etc/xdg/picom/picom-fallback.conf << 'PICOMFALLBACK'
backend = "xrender";
vsync = true;
unredir-if-possible = false;
use-damage = true;
shadow = false;
shadow-radius = 0;
shadow-opacity = 0;
shadow-offset-x = -4;
shadow-offset-y = -4;
shadow-exclude = [
  "class_g = 'Microsoft-edge'",
  "window_type = 'dock'",
  "window_type = 'desktop'",
];
fading = true;
fade-in-step = 0.06;
fade-out-step = 0.06;
inactive-opacity = 1.0;
active-opacity = 1.0;
frame-opacity = 1.0;
wintypes:
{
  dock = { shadow = false; opacity = 0.92; };
  notification = { shadow = false; opacity = 0.94; };
};
detect-client-opacity = false;
PICOMFALLBACK

    # 低内存轻动画配置 (2601-4200MB: GLX + 无 blur + 轻阴影 + 圆角)
    cat > /etc/xdg/picom/picom-lowmem.conf << 'PICOMLOWMEM'
backend = "glx";
vsync = true;
unredir-if-possible = false;
glx-no-stencil = true;
glx-no-rebind-pixmap = true;
use-damage = true;

# 不启用 blur（blur 是 GPU/内存消耗大户）
blur-background = false;

# 轻阴影
shadow = true;
shadow-radius = 8;
shadow-opacity = 0.20;
shadow-offset-x = -6;
shadow-offset-y = -6;
shadow-exclude = [
  "window_type = 'dock'",
  "window_type = 'desktop'",
];

# 圆角保留（纯 CPU 开销极低）
corner-radius = 10;
rounded-corners-exclude = [
  "window_type = 'dock'",
  "window_type = 'desktop'",
];

# 渐入渐出（transform/opacity 动画，不触发 layout）
fading = true;
fade-in-step = 0.05;
fade-out-step = 0.05;
fade-delta = 5;

inactive-opacity = 1.0;
active-opacity = 1.0;
frame-opacity = 1.0;

detect-rounded-corners = true;
detect-client-opacity = false;
detect-transient = true;
PICOMLOWMEM

    cat > /usr/local/bin/ming-picom << 'MINGPICOM'
#!/usr/bin/env bash
set -u

log="/tmp/ming-picom.log"
picom_bin="${MING_PICOM_BIN:-picom}"
settings_file="${MING_COMPOSITOR_SETTINGS:-${HOME}/.config/ming-os/settings.json}"
main_conf="${MING_PICOM_MAIN_CONF:-${HOME}/.config/picom/picom.conf}"
fallback_conf="${MING_PICOM_FALLBACK_CONF:-/etc/xdg/picom/picom-fallback.conf}"
lowmem_conf="${MING_PICOM_LOWMEM_CONF:-/etc/xdg/picom/picom-lowmem.conf}"
config="${main_conf}"
reason="modern-gpu"
compositor_profile="$(sed -n 's/.*"compositor_profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${settings_file}" 2>/dev/null | head -n1)"
compositor_profile="${compositor_profile:-auto}"

if [[ "${compositor_profile}" == off ]]; then
    exit 0
elif [[ "${compositor_profile}" == software ]]; then
    config="${fallback_conf}"
    reason="persisted-software-profile"
fi

mem_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
cmdline="$(cat /proc/cmdline 2>/dev/null || true)"
gpu="$(LC_ALL=C lspci 2>/dev/null | grep -Ei 'vga|3d|display' | tr '\n' ' ' || true)"
renderer=""
if command -v glxinfo >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    renderer="$(glxinfo -B 2>/dev/null | awk -F: '/OpenGL renderer/ {print tolower($2); exit}' | sed 's/^ *//')"
fi

if [[ "${compositor_profile}" == auto && "${mem_mb}" -gt 0 && "${mem_mb}" -lt 2600 ]]; then
    config="${fallback_conf}"
    reason="low-memory-${mem_mb}mb"
elif [[ "${compositor_profile}" == auto && ( "${cmdline}" == *nomodeset* || "${cmdline}" == *"i915.modeset=0"* || "${cmdline}" == *"radeon.modeset=0"* || "${cmdline}" == *"amdgpu.modeset=0"* ) ]]; then
    config="${fallback_conf}"
    reason="safe-graphics-cmdline"
elif [[ "${compositor_profile}" == auto && ! -d /dev/dri ]]; then
    config="${fallback_conf}"
    reason="no-dri"
elif [[ "${compositor_profile}" == auto && ( "${renderer}" == *llvmpipe* || "${renderer}" == *softpipe* ) ]]; then
    config="${fallback_conf}"
    reason="software-renderer"
elif [[ "${compositor_profile}" == auto && "${renderer}" == *svga3d* ]] || { [[ "${compositor_profile}" == auto ]] && echo "${gpu}" | grep -Eiq 'VMware.*SVGA|VirtualBox'; }; then
    config="${fallback_conf}"
    reason="virtual-machine-gpu"
elif [[ "${compositor_profile}" == auto && "${mem_mb}" -gt 0 && "${mem_mb}" -lt 4200 ]]; then
    config="${lowmem_conf}"
    reason="balanced-low-memory-${mem_mb}mb"
elif [[ "${compositor_profile}" == auto ]] && echo "${gpu}" | grep -Eiq 'Intel.*(Core Processor|HD Graphics 2000|HD Graphics 3000|GMA|4 Series|Ironlake|Sandy Bridge)'; then
    config="${fallback_conf}"
    reason="old-intel-gpu"
fi

if [[ ! -f "${config}" ]]; then
    config="${fallback_conf}"
    reason="${reason}-missing-main"
fi

{
    printf '[%s] backend config=%s reason=%s mem_mb=%s renderer=%s gpu=%s\n' \
        "$(date '+%F %T')" "${config}" "${reason}" "${mem_mb}" "${renderer:-unknown}" "${gpu:-unknown}"
} >> "${log}" 2>/dev/null || true

if ! command -v "${picom_bin}" >/dev/null 2>&1; then
    printf '[%s] picom command missing\n' "$(date '+%F %T')" >> "${log}" 2>/dev/null || true
    exit 0
fi

pgrep -u "$(id -u)" -x picom >/dev/null 2>&1 && exit 0
exec "${picom_bin}" --config "${config}" --log-level=warn
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
  <property name="initial-opacity" type="double" value="0.85"/>
  <property name="expire-timeout" type="int" value="3"/>
  <property name="do-fadeout" type="bool" value="true"/>
  <property name="do-slideout" type="bool" value="true"/>
  <property name="log-only" type="bool" value="false"/>
  <property name="log-max-size" type="int" value="50"/>
  <property name="known-applications" type="array">
    <value type="string" value="network-manager-applet"/>
    <value type="string" value="xfce4-power-manager"/>
    <value type="string" value="pulseaudio"/>
    <value type="string" value="garlic-claw"/>
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
<action>
    <icon>utilities-terminal</icon>
    <name>询问 Garlic Claw</name>
    <unique-id>4</unique-id>
    <command>xfce4-terminal --title="Garlic Claw" -e "garlic-claw ask \"请分析这个文件: %f\""</command>
    <description>使用 Garlic Claw AI 助手分析此文件</description>
    <patterns>*</patterns>
    <text-files/>
    <other-files/>
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

    cat > "${desktop_dir}/ming-edge.desktop" << EDGEDESKTOP
[Desktop Entry]
Name=浏览器
Name[zh_CN]=Microsoft Edge 浏览器
Comment=浏览互联网
Exec=/usr/local/bin/ming-edge
Icon=microsoft-edge
Terminal=false
Type=Application
Categories=Network;WebBrowser;
StartupNotify=true
EDGEDESKTOP

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

    cat > "${desktop_dir}/garlic-claw.desktop" << GCDESKTOP
[Desktop Entry]
Name=AI 助手
Name[zh_CN]=Garlic Claw
Comment=Ming OS AI 助手
Exec=xfce4-terminal --title="Garlic Claw" -e "garlic-claw"
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=System;AI;
StartupNotify=true
GCDESKTOP

    chown -R "${MING_USER}:${MING_USER}" "${desktop_dir}"
    chmod +x "${desktop_dir}"/*.desktop
}

# ======================== 发布说明与给网站 AI 的提示词 ========================

deploy_release_readme() {
    local doc_dir="/usr/share/doc/ming-os"
    mkdir -p "${doc_dir}"

    cat > "${doc_dir}/MING_OS_26.2_RELEASE_README.md" << 'RELEASEREADME'
# Ming OS 26.3.2 Release And Website Handoff

This document is the current website and AI handoff source for Ming OS. Use `26.3.2` as the public version. Do not point users to 26.2.0 or 26.2.5 as the recommended release.

## Positioning

Ming OS 26.3.2 is a Debian 13 / Trixie based Chinese desktop system for older PCs and users who prefer buttons over terminal commands. It fixes the 26.2.5 boot regression, improves Live desktop polish, and corrects the installer so the installed system presents itself as Ming OS rather than Debian.

This is the version to use when producing:

- website copy;
- download cards;
- release notes;
- OTA descriptions;
- social posts;
- screenshots and promo art captions.

## Public Links

- Official website: `https://scallion.uno`
- ISO download: `https://ming.scallion.uno/iso/ming-os-26.3.2-home-amd64.iso`
- ISO SHA256: see `SHA256SUMS` on the GitHub release page
- ISO size: see the current release asset metadata
- OTA check: `https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.3.2`

## Feature Summary

- Debian 13 / Trixie base.
- Rebuilt BIOS/UEFI boot chain with stable label `MING_OS_2632`.
- Fixes the 26.2.5 `invalid magic number` / `you need to load the kernel first` class of failures.
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
ming-os-26.3.2-home-amd64.iso.part01
ming-os-26.3.2-home-amd64.iso.part02
ming-os-26.3.2-home-amd64.iso.sha256
SHA256SUMS
```

Merge on Linux/macOS/WSL:

```bash
cat ming-os-26.3.2-home-amd64.iso.part01 ming-os-26.3.2-home-amd64.iso.part02 > ming-os-26.3.2-home-amd64.iso
sha256sum -c ming-os-26.3.2-home-amd64.iso.sha256
```

Merge on Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.3.2-home-amd64.iso.part01+ming-os-26.3.2-home-amd64.iso.part02 ming-os-26.3.2-home-amd64.iso
Get-FileHash ming-os-26.3.2-home-amd64.iso -Algorithm SHA256
```

## Prompt For Another AI Building The Scallion Product Page

You are a senior product web designer and frontend implementer. Build a Scallion website product page for `Ming OS 26.3.2`. The page should speak to ordinary Chinese users, older-PC users, and users who dislike terminal commands. Do not make it a generic Linux technical page.

Required links:

- ISO download: `https://ming.scallion.uno/iso/ming-os-26.3.2-home-amd64.iso`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.3.2`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- OTA check: `https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`

Page goals:

- Explain that Ming OS is a Debian 13 / Trixie based Chinese desktop system.
- Make `Ming OS 26.3.2` the visible product name in the first viewport.
- Highlight boot reliability, Live auto-login, optional WeChat/WPS installers, graphical update button, Ming Settings, Android-like app folders, All Disks, and the Ming-branded installer.
- Tell users clearly that 2GB RAM can run the OS, but optional WeChat/WPS installs may still be heavy.
- Provide a clear ISO download button, GitHub button, and OTA status area.

Suggested message hierarchy:

- first show the product name and official download path;
- then show the boot and desktop improvements;
- then show the user-facing buttons and shortcuts;
- then show the compatibility and low-memory guidance.

Suggested structure:

- Hero: title `Ming OS 26.3.2`; subtitle `给老旧电脑和中文用户的按钮化 Linux 桌面`; buttons `下载 ISO`, `查看 GitHub`, `检查 OTA`.
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

    # Ming 状态小组件直接读取 NetworkManager/PulseAudio/UPower。清除旧镜像
    # 遗留入口，避免恢复默认值时产生第二套托盘。
    rm -f "${autostart_dir}/nm-applet.desktop" "${autostart_dir}/volumeicon.desktop"

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

    cat > "${autostart_dir}/xfce4-screensaver.desktop" << SCREENSAVERAUTO
[Desktop Entry]
Type=Application
Name=Xfce Screensaver
Comment=Ming OS lock screen and idle screensaver
Exec=xfce4-screensaver
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

    cat > "${autostart_dir}/ming-connection-notify.desktop" << 'CONNECTIONNOTIFYAUTO'
[Desktop Entry]
Type=Application
Name=Ming Connection Notifications
Comment=Network and Bluetooth connection notifications
Exec=/usr/local/bin/ming-connection-notify
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
CONNECTIONNOTIFYAUTO

    # 首次启动配置向导
    cat > "${autostart_dir}/ming-first-run.desktop" << FIRSTRUN
[Desktop Entry]
Type=Application
Name=Ming First Setup
Comment=首次启动配置
Exec=/usr/local/bin/ming-first-run.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
FIRSTRUN

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
# Ming OS 26.3.2 首次启动欢迎引导

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import os
import subprocess
import sys

WELCOME_DONE = os.path.expanduser('~/.config/ming-os/welcome-done')
if os.path.exists(WELCOME_DONE):
    sys.exit(0)

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
                          background-color: rgba(255,255,255,0.78); color: #1D2421; border: 1px solid rgba(31,98,84,0.14); min-height: 52px; }
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
        win = WelcomeWindow(self)
        win.show_all()
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
# 极简用户名/密码设置 + 明显的"跳过"按钮。无论设置或跳过，都保持 lightdm
# 免密自动登录开启，确保跳过后后续开机绝不出现密码框（修复历史恶性 Bug）。
setup_account_oobe() {
    cat > /usr/local/bin/ming-oobe-account << 'OOBEACCOUNT'
#!/usr/bin/env bash
# Ming OS 首次开机账户向导 (OOBE)
set -uo pipefail

MARKER="${HOME}/.config/ming-os/oobe-account-done"
[[ -f "${MARKER}" ]] && exit 0

# 仅在已安装系统的首次开机运行；Live/安装器会话中不弹出（那里只跑 Calamares）
if grep -qwE "boot=live|live-config|ming.installer=1" /proc/cmdline 2>/dev/null \
   || [ -f /.disk/info ] || [ -d /lib/live/mount/medium ]; then
    exit 0
fi

# 等桌面与授权代理就绪
sleep 4

CUR_USER="$(whoami)"

dialog() {
    if command -v yad >/dev/null 2>&1; then yad "$@"; else zenity "$@"; fi
}

repair_desktop_session() {
    if command -v ming-desktop-healthcheck >/dev/null 2>&1; then
        /usr/local/bin/ming-desktop-healthcheck --repair >/dev/null 2>&1 || true
    fi
}

# 始终先确保免密自动登录已就位（双保险，独立于用户选择）
ensure_autologin() {
    # Groups and LightDM autologin are installed statically by module 01.
    return 0
}

clear_password_and_verify() {
    pkexec /usr/local/sbin/ming-account-control clear-password --user "${CUR_USER}" \
        >/dev/null 2>&1 || return 1
    passwd -S "${CUR_USER}" 2>/dev/null | awk 'NR == 1 { exit !($2 == "NP") }'
}

# 欢迎 + 选择：设置账户 / 跳过
CHOICE=$(dialog --title="欢迎使用 Ming OS" \
    --text="<b>欢迎使用 Ming OS</b>\n\n您可以为本机设置一个登录密码（用于安装软件等需要授权的操作），\n也可以直接跳过——跳过后开机将自动进入桌面，无需输入任何密码。" \
    --width=480 --height=200 \
    --button="设置密码:0" \
    --button="跳过 (Skip):2" 2>/dev/null)
RC=$?

if [[ "${RC}" != "0" ]]; then
    # 跳过：保证免密自动登录，写标记，结束
    ensure_autologin
    clear_password_and_verify || exit 1
    mkdir -p "$(dirname "${MARKER}")"
    echo "skipped" > "${MARKER}"
    dialog --title="已跳过" --text="已为您启用免密自动登录。\n开机将直接进入桌面。" \
        --width=380 --button="好的:0" 2>/dev/null || true
    repair_desktop_session
    exit 0
fi

# 设置密码流程（显示名 + 两次密码）
FORM=$(dialog --form --title="设置账户" \
    --text="为本机设置一个密码（留空则保持免密）。" \
    --field="显示名称:" \
    --field="密码:H" \
    --field="确认密码:H" \
    --width=440 \
    "Ming 用户" "" "" 2>/dev/null)
FRC=$?

if [[ "${FRC}" != "0" ]]; then
    # 关闭表单也视为跳过，仍保证免密
    ensure_autologin
    clear_password_and_verify || exit 1
    mkdir -p "$(dirname "${MARKER}")"
    echo "skipped" > "${MARKER}"
    repair_desktop_session
    exit 0
fi

FULLNAME=$(echo "${FORM}" | cut -d'|' -f1)
PW1=$(echo "${FORM}" | cut -d'|' -f2)
PW2=$(echo "${FORM}" | cut -d'|' -f3)

ensure_autologin

# 设置密码（用于 sudo/解锁；登录仍自动免密）
if [[ -n "${PW1}" ]]; then
    if [[ "${PW1}" != "${PW2}" ]]; then
        dialog --title="提示" --text="两次密码不一致，已保持免密登录。\n可稍后在「设置中心」修改。" \
            --width=380 --button="好的:0" 2>/dev/null || true
    else
        printf '%s\n' "${PW1}" | pkexec /usr/local/sbin/ming-account-control \
            set-password --user "${CUR_USER}" >/dev/null 2>&1 || exit 1
    fi
else
    clear_password_and_verify || exit 1
fi

mkdir -p "$(dirname "${MARKER}")"
echo "configured" > "${MARKER}"

dialog --title="完成" \
    --text="账户设置完成。\n开机仍会自动进入桌面，无需输入密码。" \
    --width=380 --button="开始使用:0" 2>/dev/null || true
repair_desktop_session
exit 0
OOBEACCOUNT
    chmod +x /usr/local/bin/ming-oobe-account

    # 自启动：在 Garlic Claw 欢迎之前运行（账户优先）
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
    <icon>utilities-terminal</icon>
    <name>询问 Garlic Claw</name>
    <submenu></submenu>
    <command>xfce4-terminal --title="Garlic Claw" -e "garlic-claw ask \"请分析这个文件: %f\""</command>
    <description>使用 Garlic Claw AI 助手分析文件</description>
    <range>*</range>
    <patterns>*</patterns>
    <text-files/>
    <other-files/>
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
    if [[ ! -s "${verifier_source}" ]]; then
        echo "ERROR: missing installer verification asset: ${verifier_source}" >&2
        return 1
    fi
    install -d -m 0755 /usr/local/sbin
    install -m 0755 "${verifier_source}" /usr/local/sbin/ming-installer-verify

    cat > /usr/local/sbin/ming-calamares-preflight << 'CALAMARESPREFLIGHT'
#!/usr/bin/env bash
set -u

LOG="/tmp/ming-installer/preflight.log"
mkdir -p /tmp/ming-installer
chmod 1777 /tmp/ming-installer 2>/dev/null || true
: > "${LOG}"

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
  - welcome
  - partition
  - summary
- exec:
  - shellprocess@ming-ota-preflight
  - ming-ota-target-guard@ming-ota-target-guard
  - partition
  - mount
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

cat > /etc/calamares/modules/ming-installed-desktop-gate.conf <<'INSTALLEDDESKTOPGATECONF'
---
dontChroot: true
timeout: 30
script:
  - "/usr/local/sbin/ming-installer-verify installed /target"
INSTALLEDDESKTOPGATECONF

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

if ! /usr/local/sbin/ming-installer-verify live --source "${final_source}" >> "${LOG}" 2>&1; then
    log "ERROR: Live Calamares verification failed; manual partitioning or live squashfs contract is invalid"
    exit 2
fi

log "unpackfs_source=${squash}"
log "unpackfs_stable_source=${squash_link}"
log "timezone=$(cat /etc/timezone 2>/dev/null || true)"
log "locale_conf=$(tr '\n' ';' </etc/calamares/modules/locale.conf 2>/dev/null || true)"
log "calamares_settings_sha256=$(sha256sum /etc/calamares/settings.conf 2>/dev/null | awk '{print $1}')"

# Fresh VirtualBox disks sometimes reach Calamares without a usable label.
# Only initialize completely blank non-removable disks; never touch a disk
# that already has partitions or a mounted filesystem. Prefer msdos here so
# BIOS installs do not depend on a BIOS boot partition just to install GRUB.
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
    # settings.conf/users.conf/partition.conf are the 26.3.2 safe-partition installer defaults.
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
  - welcome
  - partition
  - summary
- exec:
  - shellprocess@ming-ota-preflight
  - ming-ota-target-guard@ming-ota-target-guard
  - partition
  - mount
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
efiSystemPartition: "/boot/efi"
userSwapChoices:
  - none
  - small
  - file
drawNestedPartitions: false
alwaysShowPartitionLabels: true
defaultFileSystemType: "ext4"
availableFileSystemTypes:
  - "ext4"
initialPartitioningChoice: none
initialSwapChoice: none
requiredStorage: 12
allowManualPartitioning: true
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

    cat > /usr/local/bin/ming-calamares-launcher << 'CALAMARESLAUNCHER'
#!/usr/bin/env bash
set -e

export TZ=Asia/Shanghai
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8

mkdir -p /tmp/ming-installer
chmod 1777 /tmp/ming-installer 2>/dev/null || true
mkdir -p /run/lock
exec 9>/run/lock/ming-calamares.lock
flock -n 9 || exit 0

run_preflight() {
    if [ "$(id -u)" -eq 0 ]; then
        /usr/local/sbin/ming-calamares-preflight
    else
        sudo -n /usr/local/sbin/ming-calamares-preflight
    fi
}

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
    detail="$(tail -n 80 /tmp/ming-installer/preflight.log 2>/dev/null || true)"
    zenity --error --title="Ming OS installer preflight failed" --width=620 \
        --text="Could not prepare the installer unpack source or Beijing timezone defaults.\n\n${detail}\n\nLog: /tmp/ming-installer/preflight.log" \
        2>/dev/null || true
}

if ! is_live_or_installer; then
    zenity --info --title="Ming OS" --text="This is not a Live installer session. The installer does not need to run here." 2>/dev/null || true
    exit 0
fi

if ! run_preflight; then
    show_preflight_error
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    exec env TZ=Asia/Shanghai LANG=zh_CN.UTF-8 LANGUAGE=zh_CN:zh LC_ALL=zh_CN.UTF-8 \
        calamares -d
fi

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    exec sudo -n env \
        DISPLAY="${DISPLAY:-:0}" \
        XAUTHORITY="${XAUTHORITY:-${HOME}/.Xauthority}" \
        TZ=Asia/Shanghai \
        LANG=zh_CN.UTF-8 \
        LANGUAGE=zh_CN:zh \
        LC_ALL=zh_CN.UTF-8 \
        calamares -d
fi

exec pkexec calamares -d
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
    # Only initialize completely blank non-removable disks; never touch a disk
    # that already has partitions or a mounted filesystem. Prefer msdos here so
    # BIOS installs do not depend on a BIOS boot partition just to install GRUB.
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

prepare_calamares_runtime() {
    export TZ=Asia/Shanghai
    if [ "$(id -u)" -eq 0 ]; then
        /usr/local/sbin/ming-calamares-preflight
    else
        sudo -n /usr/local/sbin/ming-calamares-preflight
    fi
}

sleep 2

if is_live_environment || is_installer_boot; then
    if [ -z "${DISPLAY}" ]; then
        export DISPLAY=:0
    fi
    mkdir -p /tmp/ming-installer
    chmod 1777 /tmp/ming-installer 2>/dev/null || sudo -n chmod 1777 /tmp/ming-installer 2>/dev/null || true

    if command -v calamares &>/dev/null; then
        # -style/maximize handled by calamares window manager hint; keep retrying
        # if the X session is not ready yet.
        for _try in 1 2 3 4 5; do
            if /usr/local/bin/ming-calamares-launcher >/tmp/ming-installer/calamares.log 2>&1; then
                break
            fi
            sleep 2
        done &
    else
        zenity --error --title="安装错误" --text="找不到 Calamares 安装程序。" 2>/dev/null || true
    fi
fi
LIVEINSTALLER

    chmod +x /usr/local/bin/ming-live-installer.sh

    # Dedicated installer session with a minimal WM for reliable keyboard and
    # mouse focus. Calamares is maximized and automatically restarted on exit.
    cat > /usr/local/bin/ming-installer-session << 'KIOSK'
#!/usr/bin/env bash
xsetroot -solid '#0c1f1c'
if command -v xfwm4 >/dev/null 2>&1; then
    xfwm4 --replace >/tmp/ming-installer-xfwm4.log 2>&1 &
fi

focus_installer() {
    command -v wmctrl >/dev/null 2>&1 || return 0
    for _focus_try in $(seq 1 40); do
        if wmctrl -lx 2>/dev/null | grep -qi 'calamares\.calamares'; then
            wmctrl -x -r calamares.calamares -b add,maximized_vert,maximized_horz 2>/dev/null || true
            wmctrl -x -a calamares.calamares 2>/dev/null || true
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
prepare_calamares_runtime() {
    export TZ=Asia/Shanghai
    if [ -x /usr/local/sbin/ming-calamares-preflight ]; then
        if [ "$(id -u)" -eq 0 ]; then
            /usr/local/sbin/ming-calamares-preflight
        else
            sudo -n /usr/local/sbin/ming-calamares-preflight
        fi
    fi
}
while true; do
    if ! prepare_calamares_runtime; then
        zenity --error --title="安装预检失败" --text="无法找到或配置 live/filesystem.squashfs。\n\n请查看 /tmp/ming-installer/preflight.log" 2>/dev/null || true
        sleep 3
        continue
    fi
    chmod 1777 /tmp/ming-installer 2>/dev/null || sudo -n chmod 1777 /tmp/ming-installer 2>/dev/null || true
    focus_installer &
    /usr/local/bin/ming-calamares-launcher >/tmp/ming-installer/calamares.log 2>&1
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
    <property name="title_font" type="string" value="Noto Sans CJK SC Bold 11"/>
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
    <property name="enabled" type="bool" value="true"/>
    <property name="fullscreen-inhibit" type="bool" value="true"/>
    <property name="mode" type="int" value="0"/>
  </property>
  <property name="lock" type="empty">
    <property name="enabled" type="bool" value="true"/>
    <property name="saver-activation" type="empty">
      <property name="enabled" type="bool" value="true"/>
      <property name="delay" type="int" value="5"/>
    </property>
  </property>
</channel>
SCREENSAVERCFG

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
xfconf-query -c xfce4-session -p /general/LockCommand -n -t string -s "ming-lock" 2>/dev/null || true
xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s true 2>/dev/null || true
xfconf-query -c xfce4-screensaver -p /saver/fullscreen-inhibit -n -t bool -s true 2>/dev/null || true
xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s true 2>/dev/null || true
xfconf-query -c xfce4-screensaver -p /lock/saver-activation/enabled -n -t bool -s true 2>/dev/null || true
xfconf-query -c xfce4-screensaver -p /lock/saver-activation/delay -n -t int -s 5 2>/dev/null || true

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
PLANK_SETTINGS="${HOME}/.config/plank/dock1/settings"
if [[ "${MEM_MB}" -le 2600 && -f "${PLANK_SETTINGS}" ]]; then
    sed -i "s/^IconSize=.*/IconSize=36/" "${PLANK_SETTINGS}" 2>/dev/null || true
    sed -i "s/^ZoomEnabled=.*/ZoomEnabled=true/" "${PLANK_SETTINGS}" 2>/dev/null || true
    sed -i "s/^ZoomPercent=.*/ZoomPercent=126/" "${PLANK_SETTINGS}" 2>/dev/null || true
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

if command -v xfce4-screensaver >/dev/null 2>&1 && ! pgrep -f '^xfce4-screensaver' >/dev/null 2>&1; then
    (sleep 1 && nohup xfce4-screensaver >/dev/null 2>&1 &) 2>/dev/null || true
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
    # ---- Onboard 虚拟键盘：自动弹起 ----
    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/onboard-autostart.desktop" << 'ONBOARDAUTO'
[Desktop Entry]
Type=Application
Name=Onboard 虚拟键盘
Comment=触屏点击输入框时自动弹起
Exec=onboard
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
Exec=touchegg
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
TOUCHEGGAUTO
    systemctl enable touchegg 2>/dev/null || true

    chown -R "${MING_USER}:${MING_USER}" \
        "/home/${MING_USER}/.config/onboard" \
        "/home/${MING_USER}/.config/touchegg" \
        "/home/${MING_USER}/.config/autostart" 2>/dev/null || true
}

# ======================== 主流程 ========================

main() {
    echo "=====> [03_desktop] 开始 Ming OS 26.3.2 Dock 桌面定制 <====="

    generate_ming_icons
    configure_hidpi_autoscale
    install_themes
    setup_wallpaper
    configure_ming_shell
    install_ming_settings
    cleanup_retired_ming_entries
    install_ming_shell_components
    install_ota_target_guard
    install_ming_files
    ensure_wps_office
    configure_xfce_settings      # 先写桌面/xfwm/xsettings（含壁纸 backdrop）
    configure_xfce_panel         # 顶部 macOS 菜单栏
    configure_plank_dock         # 底部可放大 Dock
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

    echo "=====> [03_desktop] Ming OS 26.3.2 Dock 桌面定制完成 <====="
}

main
