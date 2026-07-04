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

# ======================== HiDPI 自动缩放 ========================

configure_hidpi_autoscale() {
    cat > /usr/local/bin/ming-scale << 'MINGSCALE'
#!/usr/bin/env bash
# Ming OS 自动缩放 - 覆盖所有屏幕比例与分辨率
# 支持: 5:4 / 4:3 / 16:9 / 16:10 / 21:9 / 32:9 及纵向旋转

SCALE_CONFIG="${HOME}/.config/ming-os/scale-done"
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
xfconf-query -c xsettings -p /Gtk/FontName -s "WenQuanYi Micro Hei ${FONT_SIZE}" 2>/dev/null || true
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
        sed -i "s/^ZoomPercent=.*/ZoomPercent=110/" "${PLANK_SETTINGS}" 2>/dev/null || true
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

    # 安全管家图标
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
gtk-font-name=WenQuanYi Micro Hei 11
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
gtk-font-name="WenQuanYi Micro Hei 11"
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
@define-color theme_bg_color #F6F8F6;
@define-color theme_fg_color #1D2421;
@define-color theme_selected_bg_color #2FAE8F;
@define-color theme_selected_fg_color #ffffff;
@define-color borders rgba(31, 98, 84, 0.14);
@define-color theme_base_color #FFFFFF;
@define-color theme_text_color #1D2421;
@define-color insensitive_bg_color #EEF2EF;
@define-color insensitive_fg_color #9AA6A0;
@define-color unfocused_bg_color #F1F4F2;
@define-color unfocused_fg_color #66736D;

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
  box-shadow: 0 14px 30px rgba(26, 67, 56, 0.10);
  margin: 8px;
}

button {
  border-radius: 10px;
  padding: 7px 15px;
  border: 1px solid @borders;
  background-image: none;
  background-color: rgba(255, 255, 255, 0.92);
  color: @theme_fg_color;
  transition: all 200ms ease;
  min-height: 30px;
}

button:hover {
  background-color: #FFFFFF;
  border-color: rgba(47, 138, 125, 0.26);
}

button:active {
  background-color: #EAF3EF;
}

button:disabled {
  background-color: @insensitive_bg_color;
  color: @insensitive_fg_color;
}

button.suggested-action {
  background-image: linear-gradient(to bottom, #329786, #23665E);
  border-color: rgba(24, 103, 89, 0.28);
  color: #FFFFFF;
}

button.suggested-action:hover {
  background-image: linear-gradient(to bottom, #39A08E, #2A776D);
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
  box-shadow: 0 12px 28px rgba(30, 70, 58, 0.08);
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
  background-color: rgba(255, 255, 255, 0.88);
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
  background-color: rgba(255, 255, 255, 0.80);
  border: 1px solid rgba(31, 98, 84, 0.12);
  border-radius: 14px;
  margin: 6px 8px 4px 8px;
  box-shadow: 0 10px 28px rgba(30, 70, 58, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.62);
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
Comment=Ming OS 26.3.1 Light Paper Theme
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

    # 默认壁纸：优先 macOS 风格（更接近用户期望的自然美观），回退深色，再回退旧 default
    local primary=""
    if [[ -f "${asset_macos}" ]]; then
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
        <value type="double" value="0.680000"/>
      </property>
      <property name="enter-opacity" type="uint" value="100"/>
      <property name="leave-opacity" type="uint" value="88"/>
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
      <property name="menu-opacity" type="uint" value="92"/>
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

    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/ming-dock-only.desktop" << 'DOCKONLY'
[Desktop Entry]
Type=Application
Name=Ming Dock Only
Comment=Hide the legacy Xfce top taskbar and keep Dock as the only launcher
Exec=sh -c "sleep 2; xfce4-panel --quit >/dev/null 2>&1 || true"
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
DockItems=ming-settings.dockitem;;ming-app-library.dockitem;;ming-files.dockitem;;firefox-esr.dockitem;;spark-store.dockitem;;wechat.dockitem;;garlic-claw.dockitem;;ming-update.dockitem;;ming-terminal.dockitem
#停靠位置: 0=左 1=右 2=上 3=下
Position=3
#对齐: 3=居中
Alignment=3
#图标大小（ming-scale 会按分辨率覆盖）
IconSize=42
#悬停放大开关
ZoomEnabled=true
#放大倍率：保持轻巧，避免图标跳动和低端显卡压力
ZoomPercent=122
#隐藏模式: 0=不隐藏 1=智能隐藏 2=自动隐藏 3=躲避窗口 4=窗口铺满时隐藏
HideMode=4
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

    # Dock 启动器（.dockitem 指向系统 .desktop）
    _plank_launcher() {
        local name="$1" target="$2"
        cat > "${plank_dir}/launchers/${name}.dockitem" << DOCKITEM
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/${target}
DOCKITEM
    }

    _plank_launcher "ming-app-library" "ming-app-library.desktop"
    _plank_launcher "firefox-esr" "firefox-esr.desktop"
    _plank_launcher "ming-files" "ming-files.desktop"
    _plank_launcher "spark-store" "spark-store.desktop"
    _plank_launcher "wechat" "ming-wechat.desktop"
    _plank_launcher "garlic-claw" "garlic-claw.desktop"
    _plank_launcher "ming-update" "ming-update.desktop"
    _plank_launcher "ming-settings" "ming-settings.desktop"
    _plank_launcher "ming-terminal" "ming-terminal.desktop"

    # Ming 纸感 Dock 主题
    local theme_dir="/usr/share/plank/themes/Ming"
    mkdir -p "${theme_dir}"
    cat > "${theme_dir}/dock.theme" << 'PLANKTHEME'
[PlankTheme]
TopRoundness=10
BottomRoundness=0
LineWidth=1
OuterStrokeColor=31;98;84;46
FillStartColor=255;255;255;196
FillEndColor=246;250;248;220
InnerStrokeColor=255;255;255;120

[PlankDockTheme]
HorizPadding=10
TopPadding=-4
BottomPadding=5
ItemPadding=4
IndicatorSize=5
IconShadowSize=1
UrgentBounceHeight=1.2
LaunchBounceHeight=0.45
FadeOpacity=1.0
ClickTime=300
UrgentBounceTime=420
LaunchBounceTime=360
ActiveTime=300
SlideTime=220
FadeTime=180
HideTime=180
GlowSize=8
GlowTime=10000
GlowPulseTime=2000
UrgentHueShift=100
ItemMoveTime=260
CascadeHide=true
PLANKTHEME

    # Plank 自启动（picom 之后启动，确保 dock 透明模糊正常）
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"
    cat > "${autostart_dir}/plank.desktop" << 'PLANKAUTO'
[Desktop Entry]
Type=Application
Name=Plank Dock
Comment=Ming OS macOS 风格程序坞
Exec=sh -c "sleep 2 && plank"
Icon=plank
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
PLANKAUTO

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/plank" \
        "${autostart_dir}/plank.desktop"
}

# ======================== Ming Shell: 控制中心与品牌化入口 ========================

configure_ming_shell() {
    mkdir -p "/home/${MING_USER}/.config/xfce4/terminal" \
             "/home/${MING_USER}/.local/share/applications"

    cat > /usr/local/bin/ming-files << 'MINGFILES'
#!/usr/bin/env bash
exec thunar "$@"
MINGFILES
    chmod +x /usr/local/bin/ming-files

    cat > /usr/local/bin/ming-disk-hub << 'DISKHUB'
#!/usr/bin/env bash
set -euo pipefail

hub="${HOME}/所有磁盘"
mkdir -p "${hub}"
index_file="${hub}/README.txt"
: > "${index_file}"
cat > "${index_file}" <<'INDEXHEAD'
Ming OS 所有磁盘

这里把系统盘、用户文件、数据盘和 U 盘集中到一个入口。
它不会合并或移动真实数据，只是用友好的入口减少“C盘/D盘”式空间焦虑。

INDEXHEAD

make_link() {
    local target="$1"
    local name="$2"
    [[ -e "${target}" ]] || return 0
    ln -sfn "${target}" "${hub}/${name}" 2>/dev/null || true
    printf '%s -> %s\n' "${name}" "${target}" >> "${index_file}"
}

make_link "${HOME}" "我的文件"
make_link "${HOME}/Desktop" "桌面"
make_link "${HOME}/Downloads" "下载"
make_link "${HOME}/Documents" "文档"
make_link "/" "系统盘"

idx=1
while IFS='|' read -r mountpoint label size fstype avail usep; do
    [[ -n "${mountpoint}" && "${mountpoint}" != "/" ]] || continue
    case "${mountpoint}" in
        /run/user/*|/proc*|/sys*|/dev*|/boot/efi) continue ;;
    esac
    clean_label="${label:-数据盘${idx}}"
    clean_label="${clean_label//\//-}"
    if [[ -n "${size}" ]]; then
        name="${clean_label} (${size})"
    else
        name="${clean_label}"
    fi
    make_link "${mountpoint}" "${name}"
    if [[ -n "${avail}" || -n "${usep}" ]]; then
        printf '  可用空间：%s，已用：%s，格式：%s\n' "${avail:---}" "${usep:---}" "${fstype:---}" >> "${index_file}"
    fi
    idx=$((idx + 1))
done < <(lsblk -rpo MOUNTPOINT,LABEL,SIZE,FSTYPE 2>/dev/null | awk 'NR>1 && $1 != "" {print $1 "|" $2 "|" $3 "|" $4}' | while IFS='|' read -r mp label size fstype; do
    stats="$(df -hP "${mp}" 2>/dev/null | awk 'NR==2 {print $4 "|" $5}')"
    printf '%s|%s|%s|%s|%s\n' "${mp}" "${label}" "${size}" "${fstype}" "${stats}"
done)

cat > "${hub}/释放空间.desktop" <<'FREESPACE'
[Desktop Entry]
Name=释放空间
Comment=打开 Ming 设置清理缓存和临时文件
Exec=ming-control-center
Icon=edit-clear
Terminal=false
Type=Application
Categories=Utility;System;
FREESPACE
chmod +x "${hub}/释放空间.desktop" 2>/dev/null || true

mkdir -p "${HOME}/.config/gtk-3.0"
bookmarks="${HOME}/.config/gtk-3.0/bookmarks"
touch "${bookmarks}"
grep -qxF "file://${hub} 所有磁盘" "${bookmarks}" 2>/dev/null || echo "file://${hub} 所有磁盘" >> "${bookmarks}"

if [[ "${1:-}" == "--open" ]]; then
    exec thunar "${hub}"
fi
DISKHUB
    chmod +x /usr/local/bin/ming-disk-hub

    cat > /usr/local/bin/ming-terminal << 'MINGTERM'
#!/usr/bin/env bash
exec xfce4-terminal --hide-menubar --title="Ming Terminal" "$@"
MINGTERM
    chmod +x /usr/local/bin/ming-terminal

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
window { background: #F4F6F3; }
.root {
  background: linear-gradient(135deg, #F7F9F5, #EEF4EF 58%, #E4EDE8);
  color: #1C2320;
}
.title { font-size: 24px; font-weight: 800; color: #1C2320; }
.subtitle { font-size: 12px; color: #51605A; }
.time { font-size: 34px; font-weight: 800; color: #1C2320; }
.date { font-size: 12px; color: #66726C; }
.tile {
  background: rgba(255,255,255,0.84);
  border: 1px solid rgba(31,98,84,0.10);
  border-radius: 12px;
  padding: 12px;
  color: #1C2320;
}
.tile:hover {
  background: rgba(255,255,255,0.96);
  border-color: rgba(47,138,125,0.26);
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
            ('显示', 'video-display', 'xfce4-display-settings'),
            ('设置', 'ming-control-center', 'ming-control-center'),
            ('应用库', 'ming-app-library', 'ming-app-library'),
            ('锁屏', 'system-lock-screen', 'xflock4'),
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
window { background: #F4F6F3; }
.root {
  background: linear-gradient(135deg, #F7F9F5, #EEF4EF 52%, #E4EDE8);
  color: #1C2320;
}
.title { font-size: 26px; font-weight: 800; color: #1C2320; }
.subtitle { font-size: 12px; color: #51605A; }
.search {
  min-height: 42px;
  border-radius: 12px;
  background: rgba(255,255,255,0.86);
  color: #1C2320;
  border: 1px solid rgba(31,98,84,0.10);
  padding: 0 12px;
}
.app-tile {
  background: rgba(255,255,255,0.84);
  border: 1px solid rgba(31,98,84,0.10);
  border-radius: 12px;
  padding: 10px;
  color: #1C2320;
}
.app-tile:hover {
  background: rgba(255,255,255,0.96);
  border-color: rgba(47,138,125,0.26);
}
.app-name { font-size: 11px; font-weight: 700; color: #1C2320; }
.quick-button {
  border-radius: 12px;
  padding: 9px 12px;
  background: rgba(255,255,255,0.84);
  color: #1C2320;
}
.quick-button:hover { background: rgba(47,138,125,0.12); }
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

class AppLibrary(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Ming 应用库')
        self.set_default_size(840, 560)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name('ming-app-library')
        self.apps = load_apps()
        self.filtered = self.apps

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 600)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.get_style_context().add_class('root')
        root.set_border_width(24)
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

        for label, cmd in [('整理桌面', 'ming-helper organize-desktop'), ('所有磁盘', 'ming-helper disks'), ('系统设置', 'ming-control-center')]:
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
        self.flow.set_max_children_per_line(6)
        self.flow.set_min_children_per_line(3)
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
        btn.set_size_request(118, 108)
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
disks_link="${desktop}/所有磁盘"
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
    if [[ "${cats}" == *network* || "${name}" == *firefox* || "${name}" == *微信* || "${name}" == *浏览器* ]]; then
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

cat > "${desktop}/Ming 应用库.desktop" << APPLIBDESKTOP
[Desktop Entry]
Name=Ming 应用库
Comment=像手机应用库一样搜索、打开、整理已安装软件
Exec=/usr/local/bin/ming-app-library
Icon=ming-app-library
Terminal=false
Type=Application
Categories=Utility;System;
StartupNotify=true
APPLIBDESKTOP

cat > "${desktop}/所有磁盘.desktop" << DISKDESKTOP
[Desktop Entry]
Name=所有磁盘
Comment=把系统盘、数据盘、U盘集中到一个入口
Exec=/usr/local/bin/ming-disk-hub --open
Icon=drive-harddisk
Terminal=false
Type=Application
Categories=System;FileManager;
StartupNotify=true
DISKDESKTOP
chmod +x "${desktop}/Ming 设置.desktop" "${desktop}/Ming 应用库.desktop" "${desktop}/所有磁盘.desktop" 2>/dev/null || true

/usr/local/bin/ming-disk-hub >/tmp/ming-disk-hub.log 2>&1 || true
ln -sfn "${HOME}/所有磁盘" "${disks_link}" 2>/dev/null || true

gio set "${apps_dir}" metadata::custom-icon-name application-x-executable 2>/dev/null || true
gio set "${system_dir}" metadata::custom-icon-name ming-control-center 2>/dev/null || true
gio set "${internet_dir}" metadata::custom-icon-name network-workgroup 2>/dev/null || true
gio set "${office_dir}" metadata::custom-icon-name x-office-document 2>/dev/null || true
gio set "${media_dir}" metadata::custom-icon-name multimedia-player 2>/dev/null || true
gio set "${games_dir}" metadata::custom-icon-name applications-games 2>/dev/null || true
gio set "${tools_dir}" metadata::custom-icon-name applications-utilities 2>/dev/null || true
gio set "${common_dir}" metadata::custom-icon-name emblem-favorite 2>/dev/null || true
gio set "${HOME}/所有磁盘" metadata::custom-icon-name drive-harddisk 2>/dev/null || true

if command -v ming-phone-desktop >/dev/null 2>&1; then
    ming-phone-desktop --sync >/tmp/ming-phone-desktop-sync.log 2>&1 || true
else
    sync_apps
fi
for item in "${desktop}/Ming 设置.desktop" "${desktop}/Ming 应用库.desktop" "${desktop}/所有磁盘.desktop"; do
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
        /usr/local/bin/ming-disk-hub >/tmp/ming-disk-hub.log 2>&1 || true
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

    cat > /usr/local/bin/ming-phone-desktop << 'PHONEDESKTOP'
#!/usr/bin/env python3
import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

HOME = Path.home()
STATE_DIR = HOME / '.config' / 'ming-os'
LAYOUT_PATH = STATE_DIR / 'desktop-layout.json'
DESKTOP_DIR = HOME / 'Desktop'
APP_DIRS = [Path('/usr/share/applications'), HOME / '.local/share/applications']
CORE_NAMES = {
    'ming-control-center.desktop',
    'ming-settings.desktop',
    'ming-files.desktop',
    'ming-app-library.desktop',
    'ming-update.desktop',
    'ming-terminal.desktop',
    'firefox-esr.desktop',
    'spark-store.desktop',
}
GRID_W = 112
GRID_H = 122
PAD_X = 36
PAD_Y = 34
DROP_DISTANCE = 58

CSS = b'''
window.ming-desktop {
  background-color: rgba(247, 250, 247, 0.01);
  color: #1D2421;
}
.tile {
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.24);
  border: 1px solid rgba(31, 98, 84, 0.08);
  padding: 8px;
  color: #1D2421;
}
.tile:hover, .tile.dragging {
  background: rgba(47, 138, 125, 0.12);
  border-color: rgba(47, 138, 125, 0.26);
}
.folder {
  background: rgba(47, 138, 125, 0.10);
  border-color: rgba(47, 138, 125, 0.32);
}
.label {
  color: #1D2421;
  font-size: 11px;
  font-weight: 700;
  text-shadow: 0 1px 0 rgba(255,255,255,0.7);
}
.folder-title {
  color: #1D2421;
  font-size: 18px;
  font-weight: 700;
}
.folder-panel {
  background: rgba(251, 253, 251, 0.98);
  border: 1px solid rgba(31, 98, 84, 0.10);
  border-radius: 12px;
  padding: 18px;
}
.folder-action {
  border-radius: 9px;
  padding: 7px 10px;
}
'''

def app_id(path):
    return hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:16]

def read_app(path):
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
    if not entry.get('Exec', ''):
        return None
    return {
        'id': app_id(path),
        'type': 'app',
        'path': str(path),
        'name': entry.get('Name[zh_CN]') or entry.get('Name') or Path(path).stem,
        'icon': entry.get('Icon') or 'application-x-executable',
        'categories': entry.get('Categories', ''),
    }

def load_apps():
    apps = []
    seen = set()
    for directory in APP_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob('*.desktop')):
            item = read_app(path)
            if not item or item['id'] in seen:
                continue
            seen.add(item['id'])
            apps.append(item)
    apps.sort(key=lambda item: (Path(item['path']).name not in CORE_NAMES, item['name'].lower()))
    return apps

def empty_layout():
    return {'version': 1, 'items': []}

def load_layout():
    try:
        data = json.loads(LAYOUT_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('items'), list):
            return data
    except Exception:
        pass
    return empty_layout()

def save_layout(layout):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LAYOUT_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(LAYOUT_PATH)

def next_position(index, width=1366):
    cols = max(4, int((width - PAD_X * 2) / GRID_W))
    row = index // cols
    col = index % cols
    return PAD_X + col * GRID_W, PAD_Y + row * GRID_H

def sync_layout(width=1366):
    apps = load_apps()
    layout = load_layout()
    items = layout.get('items', [])
    known = set()
    for item in items:
        if item.get('type') == 'folder':
            known.update(item.get('children', []))
        elif item.get('path'):
            known.add(item['path'])
    index = len(items)
    for app in apps:
        if app['path'] in known:
            continue
        x, y = next_position(index, width)
        app['x'] = x
        app['y'] = y
        items.append(app)
        index += 1
    layout['items'] = items
    save_layout(layout)
    sync_files(layout)
    return layout

def safe_name(name):
    cleaned = ''.join('-' if ch in '/\\:*?"<>|' else ch for ch in name).strip()
    return cleaned or '应用'

def copy_desktop(path, target_dir, name=None):
    src = Path(path)
    if not src.is_file():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_name(name or src.stem)}.desktop"
    try:
        shutil.copy2(src, target)
        target.chmod(0o755)
    except Exception:
        pass

def sync_files(layout):
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    folders_seen = set()
    for item in layout.get('items', []):
        if item.get('type') == 'folder':
            folder_dir = DESKTOP_DIR / safe_name(item.get('name', '文件夹'))
            folders_seen.add(folder_dir)
            folder_dir.mkdir(parents=True, exist_ok=True)
            for child_path in item.get('children', []):
                child = read_app(child_path)
                if child:
                    copy_desktop(child_path, folder_dir, child['name'])
        elif item.get('path') and Path(item['path']).name in CORE_NAMES:
            copy_desktop(item['path'], DESKTOP_DIR, item.get('name'))
    for old in DESKTOP_DIR.iterdir() if DESKTOP_DIR.exists() else []:
        if old.is_dir() and old.name not in {'应用', '系统', '上网', '办公', '影音', '游戏', '工具', '常用'} and old not in folders_seen:
            try:
                if not any(old.iterdir()):
                    old.rmdir()
            except Exception:
                pass

def command_add(path, folder=False):
    item = read_app(path)
    if not item:
        return 1
    layout = sync_layout()
    items = layout['items']
    if any(x.get('path') == item['path'] for x in items):
        return 0
    if folder:
        folder_item = next((x for x in items if x.get('type') == 'folder'), None)
        if not folder_item:
            folder_item = {'id': 'folder-' + app_id(item['path']), 'type': 'folder', 'name': '新文件夹', 'children': [], 'x': PAD_X, 'y': PAD_Y}
            items.insert(0, folder_item)
        if item['path'] not in folder_item['children']:
            folder_item['children'].append(item['path'])
    else:
        item['x'], item['y'] = next_position(len(items))
        items.append(item)
    save_layout(layout)
    sync_files(layout)
    return 0

class DesktopTile(Gtk.EventBox):
    def __init__(self, desktop, item):
        super().__init__()
        self.desktop = desktop
        self.item = item
        self.dragging = False
        self.offset = (0, 0)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect('button-press-event', self.on_press)
        self.connect('motion-notify-event', self.on_motion)
        self.connect('button-release-event', self.on_release)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_size_request(92, 102)
        box.get_style_context().add_class('tile')
        if item.get('type') == 'folder':
            box.get_style_context().add_class('folder')
            image = Gtk.Image.new_from_icon_name('folder', Gtk.IconSize.DIALOG)
        else:
            image = Gtk.Image.new_from_icon_name(item.get('icon') or 'application-x-executable', Gtk.IconSize.DIALOG)
        label = Gtk.Label(label=item.get('name', '应用'))
        label.get_style_context().add_class('label')
        label.set_justify(Gtk.Justification.CENTER)
        label.set_line_wrap(True)
        label.set_max_width_chars(8)
        box.pack_start(image, True, True, 0)
        box.pack_start(label, False, False, 0)
        self.box = box
        self.add(box)

    def on_press(self, _widget, event):
        if event.button == 3:
            self.desktop.show_context_menu(self.item, event)
            return True
        if event.button != 1:
            return False
        self.dragging = False
        self.offset = (event.x, event.y)
        return True

    def on_motion(self, _widget, event):
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        self.dragging = True
        self.box.get_style_context().add_class('dragging')
        root_x, root_y = event.x_root, event.y_root
        win_x, win_y = self.desktop.window_origin
        x = int(root_x - win_x - self.offset[0])
        y = int(root_y - win_y - self.offset[1])
        self.desktop.fixed.move(self, max(8, x), max(8, y))
        return True

    def on_release(self, _widget, event):
        self.box.get_style_context().remove_class('dragging')
        root_x, root_y = event.x_root, event.y_root
        win_x, win_y = self.desktop.window_origin
        x = int(root_x - win_x - self.offset[0])
        y = int(root_y - win_y - self.offset[1])
        if self.dragging:
            self.desktop.finish_drag(self.item, max(8, x), max(8, y))
        else:
            self.desktop.open_item(self.item)
        self.dragging = False
        return True

class PhoneDesktop(Gtk.Window):
    def __init__(self):
        super().__init__(title='Ming Desktop')
        self.set_name('ming-desktop-window')
        self.get_style_context().add_class('ming-desktop')
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
            self.set_app_paintable(True)
        self.maximize()
        self.fullscreen()
        self.connect('destroy', Gtk.main_quit)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, 700)
        self.fixed = Gtk.Fixed()
        self.add(self.fixed)
        self.tiles = {}
        self.layout = sync_layout(self.get_screen().get_width())
        self.render()
        GLib.timeout_add_seconds(8, self.refresh_from_apps)

    @property
    def window_origin(self):
        window = self.get_window()
        if window:
            try:
                ok, x, y = window.get_origin()
                if ok:
                    return x, y
            except Exception:
                pass
        return 0, 0

    def render(self):
        for child in self.fixed.get_children():
            self.fixed.remove(child)
        self.tiles = {}
        for item in self.layout.get('items', []):
            tile = DesktopTile(self, item)
            self.tiles[item['id']] = tile
            self.fixed.put(tile, int(item.get('x', PAD_X)), int(item.get('y', PAD_Y)))
        self.show_all()

    def refresh_from_apps(self):
        self.layout = sync_layout(self.get_screen().get_width())
        self.render()
        return True

    def find_drop_target(self, source, x, y):
        best = None
        best_distance = DROP_DISTANCE
        for item in self.layout.get('items', []):
            if item.get('id') == source.get('id'):
                continue
            dx = int(item.get('x', 0)) - x
            dy = int(item.get('y', 0)) - y
            distance = (dx * dx + dy * dy) ** 0.5
            if distance < best_distance:
                best = item
                best_distance = distance
        return best

    def finish_drag(self, item, x, y):
        target = self.find_drop_target(item, x, y)
        if target and item.get('type') == 'app':
            self.create_or_merge_folder(item, target)
        else:
            item['x'] = x
            item['y'] = y
        save_layout(self.layout)
        sync_files(self.layout)
        self.render()

    def create_or_merge_folder(self, source, target):
        items = self.layout.get('items', [])
        if target.get('type') == 'folder':
            if source.get('path') not in target.setdefault('children', []):
                target['children'].append(source.get('path'))
            items[:] = [x for x in items if x.get('id') != source.get('id')]
            return
        if target.get('type') != 'app':
            return
        folder = {
            'id': 'folder-' + app_id(source.get('path', '') + target.get('path', '')),
            'type': 'folder',
            'name': '新文件夹',
            'children': [target.get('path'), source.get('path')],
            'x': target.get('x', PAD_X),
            'y': target.get('y', PAD_Y),
        }
        items[:] = [x for x in items if x.get('id') not in {source.get('id'), target.get('id')}]
        items.append(folder)

    def open_item(self, item):
        if item.get('type') == 'folder':
            self.show_folder(item)
            return
        try:
            info = Gio.DesktopAppInfo.new_from_filename(item.get('path'))
            if info:
                info.launch([], None)
                return
        except Exception:
            pass
        subprocess.Popen(['gtk-launch', Path(item.get('path', '')).stem])

    def show_folder(self, folder):
        dialog = Gtk.Dialog(title=folder.get('name', '文件夹'), transient_for=self, flags=0)
        dialog.set_default_size(520, 420)
        area = dialog.get_content_area()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        panel.get_style_context().add_class('folder-panel')
        panel.set_border_width(16)
        area.add(panel)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Entry()
        title.set_text(folder.get('name', '文件夹'))
        title.get_style_context().add_class('folder-title')
        rename = Gtk.Button(label='改名')
        rename.get_style_context().add_class('folder-action')
        rename.connect('clicked', lambda _b: self.rename_folder(folder, title.get_text(), dialog))
        header.pack_start(title, True, True, 0)
        header.pack_start(rename, False, False, 0)
        panel.pack_start(header, False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_row_spacing(10)
        flow.set_column_spacing(10)
        panel.pack_start(flow, True, True, 0)
        for child_path in list(folder.get('children', [])):
            child = read_app(child_path)
            if not child:
                continue
            button = Gtk.Button()
            button.set_size_request(104, 94)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.pack_start(Gtk.Image.new_from_icon_name(child.get('icon'), Gtk.IconSize.DIALOG), True, True, 0)
            label = Gtk.Label(label=child.get('name'))
            label.set_line_wrap(True)
            label.set_max_width_chars(8)
            box.pack_start(label, False, False, 0)
            button.add(box)
            button.connect('clicked', lambda _b, app=child: self.open_item(app))
            button.connect('button-press-event', lambda w, e, app=child, f=folder, d=dialog: self.child_menu(w, e, app, f, d))
            flow.add(button)
        close = Gtk.Button(label='关闭')
        close.connect('clicked', lambda _b: dialog.destroy())
        panel.pack_start(close, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def child_menu(self, widget, event, app, folder, dialog):
        if getattr(event, 'button', 0) != 3:
            return False
        menu = Gtk.Menu()
        move = Gtk.MenuItem(label='移到桌面')
        move.connect('activate', lambda _i: self.move_child_to_desktop(app, folder, dialog))
        menu.append(move)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def rename_folder(self, folder, name, dialog):
        folder['name'] = safe_name(name)
        save_layout(self.layout)
        sync_files(self.layout)
        dialog.destroy()
        self.render()

    def move_child_to_desktop(self, app, folder, dialog):
        folder['children'] = [x for x in folder.get('children', []) if x != app.get('path')]
        app['x'], app['y'] = next_position(len(self.layout.get('items', [])), self.get_screen().get_width())
        self.layout['items'].append(app)
        if not folder['children']:
            self.layout['items'] = [x for x in self.layout['items'] if x.get('id') != folder.get('id')]
        save_layout(self.layout)
        sync_files(self.layout)
        dialog.destroy()
        self.render()

    def show_context_menu(self, item, event):
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label='打开')
        open_item.connect('activate', lambda _i: self.open_item(item))
        menu.append(open_item)
        if item.get('type') == 'folder':
            rename = Gtk.MenuItem(label='重命名文件夹')
            rename.connect('activate', lambda _i: self.show_folder(item))
            menu.append(rename)
            if not item.get('children'):
                delete = Gtk.MenuItem(label='删除空文件夹')
                delete.connect('activate', lambda _i: self.delete_item(item))
                menu.append(delete)
        else:
            remove = Gtk.MenuItem(label='从桌面移除')
            remove.connect('activate', lambda _i: self.delete_item(item))
            menu.append(remove)
        menu.show_all()
        menu.popup_at_pointer(event)

    def delete_item(self, item):
        self.layout['items'] = [x for x in self.layout.get('items', []) if x.get('id') != item.get('id')]
        save_layout(self.layout)
        sync_files(self.layout)
        self.render()

def main():
    if len(sys.argv) >= 3 and sys.argv[1] == '--add':
        raise SystemExit(command_add(sys.argv[2], folder=False))
    if len(sys.argv) >= 3 and sys.argv[1] == '--add-to-folder':
        raise SystemExit(command_add(sys.argv[2], folder=True))
    if len(sys.argv) >= 2 and sys.argv[1] == '--sync':
        sync_layout()
        return
    PhoneDesktop()
    Gtk.main()

if __name__ == '__main__':
    main()
PHONEDESKTOP
    chmod +x /usr/local/bin/ming-phone-desktop

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
        exec /usr/local/bin/ming-disk-hub --open
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
FontName=DejaVu Sans Mono 11
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
    ('所有磁盘', 'drive-harddisk', '像一个盘一样查看系统盘和数据盘', 'ming-helper disks'),
    ('查看内存策略', 'utilities-system-monitor', '了解系统为低内存做了什么', 'ming-helper memory'),
    ('声音和音量', 'multimedia-volume-control', '调节扬声器、麦克风和输出设备', 'pavucontrol'),
    ('电源和电池', 'battery', '调节亮度、合盖和省电', 'xfce4-power-manager-settings'),
    ('外观主题', 'preferences-desktop-theme', '更换主题、字体和图标', 'xfce4-appearance-settings'),
    ('文件', 'files-icon', '打开文件和下载目录', 'ming-files'),
    ('AI 助手', 'utilities-terminal', '打开 Garlic Claw', 'xfce4-terminal --hide-menubar --title="Garlic Claw" -e garlic-claw'),
    ('高级设置', 'ming-settings', '给懂电脑的人使用', 'xfce4-settings-manager'),
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

        footer = Gtk.Label(label='Ming OS 26.3.1 · Debian Trixie')
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
    if command -v wps >/dev/null 2>&1 || dpkg -s wps-office >/dev/null 2>&1; then
        cat > /usr/share/applications/wps-office.desktop << 'WPSDESKTOP'
[Desktop Entry]
Name=WPS Office
Name[zh_CN]=WPS Office
Comment=Kingsoft WPS Office for Linux
Exec=wps
Icon=wps-office
Terminal=false
Type=Application
Categories=Office;WordProcessor;Spreadsheet;Presentation;
StartupNotify=true
WPSDESKTOP
        cp /usr/share/applications/wps-office.desktop "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
        chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
        chmod +x "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
        return 0
    fi

    if [[ -x /usr/local/bin/ming-install-wps && -f /usr/share/applications/ming-install-wps.desktop ]]; then
        cp /usr/share/applications/ming-install-wps.desktop "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
        chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
        chmod +x "/home/${MING_USER}/Desktop/wps-office.desktop" 2>/dev/null || true
    fi
}

# ======================== Picom 用户级配置 ========================

configure_picom() {
    mkdir -p /home/${MING_USER}/.config/picom
    cat > /home/${MING_USER}/.config/picom/picom.conf << 'PICOMCFG'
# Ming OS 26.3.1 Picom 配置 - 液态玻璃效果 (mainline picom 10.x 兼容)
# 说明：Debian 仓库的 picom 主线版不支持 jonaburg/FT-Labs
#       分支的 animations 块；强行写入会导致配置解析失败、合成器拒绝启动，
#       这正是历史版本“美化没生效”的元凶之一。这里只用主线支持的特性：
#       dual_kawase 模糊 + 圆角 + 柔光阴影 + 渐入渐出，配合 Plank 的 dock
#       缩放动画，实现 macOS 风格的丝滑观感。
backend = "glx";
vsync = true;
unredir-if-possible = true;
glx-no-stencil = true;
glx-no-rebind-pixmap = true;
use-damage = true;
xrender-sync-fence = true;

# ---- 液态玻璃模糊 (dual_kawase 为主线 picom 支持) ----
blur-method = "dual_kawase";
blur-strength = 7;
blur-background = true;
blur-background-frame = true;
blur-background-fixed = true;
blur-background-exclude = [
  "class_g = 'firefox'",
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
  "window_type = 'dock'",
  "window_type = 'desktop'",
];

# ---- 圆角窗口 ----
corner-radius = 12;
rounded-corners-exclude = [
  "window_type = 'dock'",
  "window_type = 'desktop'",
  "window_type = 'notification'",
];

# ---- 透明度 ----
inactive-opacity = 0.92;
active-opacity = 0.98;
frame-opacity = 0.90;
inactive-opacity-override = false;
opacity-rule = [
  "85:class_g = 'firefox'",
  "90:class_g = 'Thunar'",
  "90:class_g = 'Xfce4-terminal'",
];

# ---- 渐入渐出 ----
fading = true;
fade-in-step = 0.04;
fade-out-step = 0.04;
fade-delta = 5;

# ---- wintypes ----
wintypes:
{
  tooltip = { fade = true; shadow = true; opacity = 0.85; focus = true; blur = true; };
  dock = { shadow = false; blur = true; };
  dnd = { shadow = false; };
  dropdown_menu = { shadow = true; blur = true; };
  popup_menu = { shadow = true; blur = true; };
  utility = { shadow = true; blur = true; };
  notification = { shadow = true; blur = true; };
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
vsync = true;
unredir-if-possible = false;
use-damage = true;
shadow = true;
shadow-radius = 6;
shadow-opacity = 0.20;
shadow-offset-x = -4;
shadow-offset-y = -4;
fading = true;
fade-in-step = 0.06;
fade-out-step = 0.06;
inactive-opacity = 0.95;
active-opacity = 1.0;
PICOMFALLBACK

    # 低内存轻动画配置 (2601-4200MB: GLX + 无 blur + 轻阴影 + 圆角)
    cat > /etc/xdg/picom/picom-lowmem.conf << 'PICOMLOWMEM'
backend = "glx";
vsync = true;
unredir-if-possible = true;
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

inactive-opacity = 0.95;
active-opacity = 1.0;
frame-opacity = 0.95;

detect-rounded-corners = true;
detect-client-opacity = true;
detect-transient = true;
PICOMLOWMEM

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

    cat > "${desktop_dir}/firefox.desktop" << FFDESKTOP
[Desktop Entry]
Name=浏览器
Name[zh_CN]=Firefox 浏览器
Comment=浏览互联网
Exec=firefox-esr
Icon=firefox-esr
Terminal=false
Type=Application
Categories=Network;WebBrowser;
StartupNotify=true
FFDESKTOP

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
# Ming OS 26.3.1 Release And Website Handoff

This document is the current website and AI handoff source for Ming OS. Use `26.3.1` as the public version. Do not point users to 26.2.0 or 26.2.5 as the recommended release.

## Positioning

Ming OS 26.3.1 is a Debian 13 / Trixie based Chinese desktop system for older PCs and users who prefer buttons over terminal commands. It fixes the 26.2.5 boot regression, improves Live desktop polish, and corrects the installer so the installed system presents itself as Ming OS rather than Debian.

This is the version to use when producing:

- website copy;
- download cards;
- release notes;
- OTA descriptions;
- social posts;
- screenshots and promo art captions.

## Public Links

- Official website: `https://scallion.uno`
- ISO download: `https://ming.scallion.uno/iso/ming-os-26.3.1-home-amd64.iso`
- ISO SHA256: `0a23e1ce739ced8daede1b2deb6e7d5879c9b0b44e1ecb43fda0422c2a164df1`
- ISO size: `2698493952`
- OTA check: `https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.3.1`

## Feature Summary

- Debian 13 / Trixie base.
- Rebuilt BIOS/UEFI boot chain with stable label `MING_OS_2631`.
- Fixes the 26.2.5 `invalid magic number` / `you need to load the kernel first` class of failures.
- Live/Ventoy auto-login as `ming`.
- Ming wallpaper applies by default.
- Main Ming icons no longer use white-background AI PNG overrides.
- Ming Security Manager opens through a stable wrapper and writes readable logs.
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
- low-memory WeChat support;
- update tooling;
- installer branding;
- desktop organization;
- storage simplification;
- and a control center for common actions.

## GitHub Download Note

The complete ISO is available on the official website. GitHub Release uses split assets:

```text
ming-os-26.3.1-home-amd64.iso.part01
ming-os-26.3.1-home-amd64.iso.part02
ming-os-26.3.1-home-amd64.iso.sha256
SHA256SUMS
```

Merge on Linux/macOS/WSL:

```bash
cat ming-os-26.3.1-home-amd64.iso.part01 ming-os-26.3.1-home-amd64.iso.part02 > ming-os-26.3.1-home-amd64.iso
sha256sum -c ming-os-26.3.1-home-amd64.iso.sha256
```

Merge on Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.3.1-home-amd64.iso.part01+ming-os-26.3.1-home-amd64.iso.part02 ming-os-26.3.1-home-amd64.iso
Get-FileHash ming-os-26.3.1-home-amd64.iso -Algorithm SHA256
```

## Prompt For Another AI Building The Scallion Product Page

You are a senior product web designer and frontend implementer. Build a Scallion website product page for `Ming OS 26.3.1`. The page should speak to ordinary Chinese users, older-PC users, and users who dislike terminal commands. Do not make it a generic Linux technical page.

Required links:

- ISO download: `https://ming.scallion.uno/iso/ming-os-26.3.1-home-amd64.iso`
- GitHub release: `https://github.com/bzm2008/ming-os/releases/tag/v26.3.1`
- GitHub repo: `https://github.com/bzm2008/ming-os`
- OTA check: `https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`

Page goals:

- Explain that Ming OS is a Debian 13 / Trixie based Chinese desktop system.
- Make `Ming OS 26.3.1` the visible product name in the first viewport.
- Highlight boot reliability, Live auto-login, low-memory WeChat support, graphical update button, Ming Settings, Android-like app folders, All Disks, and the Ming-branded installer.
- Tell users clearly that 2GB RAM can run the OS, but a WeChat account with many friends and groups may still be heavy; recommend Web WeChat as the fallback.
- Provide a clear ISO download button, GitHub button, and OTA status area.

Suggested message hierarchy:

- first show the product name and official download path;
- then show the boot and desktop improvements;
- then show the user-facing buttons and shortcuts;
- then show the compatibility and low-memory guidance.

Suggested structure:

- Hero: title `Ming OS 26.3.1`; subtitle `给老旧电脑和中文用户的按钮化 Linux 桌面`; buttons `下载 ISO`, `查看 GitHub`, `检查 OTA`.
- Trust strip: SHA256, size, release date, OTA ready status.
- Three cards: `启动更稳`, `不用记命令`, `像手机一样整理应用`.
- Feature section: WeChat low-memory mode, Spark Store, Ming Settings, Ming App Library, All Disks, OTA updates, Security Manager, Ming installer.
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
- protect low-memory machines from heavy WeChat sessions.
RELEASEREADME
}

# ======================== Xfce 会话自启动 ========================

configure_autostart() {
    local autostart_dir="/home/${MING_USER}/.config/autostart"
    mkdir -p "${autostart_dir}"

    # Picom 合成器 (GLX自动探测，老旧GPU回退xrender)
    cat > "${autostart_dir}/picom.desktop" << PICOMAUTOSTART
[Desktop Entry]
Type=Application
Name=Picom Compositor
Comment=窗口合成器
Exec=/usr/local/bin/ming-picom
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
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

    # 安卓式桌面文件夹：登录后自动整理应用，并监听新安装应用
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

    cat > "${autostart_dir}/ming-phone-desktop.desktop" << PHONEDESKTOPAUTO
[Desktop Entry]
Type=Application
Name=Ming Phone Desktop
Comment=手机式桌面图标和拖拽文件夹
Exec=sh -c "sleep 3 && /usr/local/bin/ming-phone-desktop"
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
PHONEDESKTOPAUTO

    chown -R "${MING_USER}:${MING_USER}" "${autostart_dir}"
}

# ======================== 首次启动欢迎引导 ========================

setup_welcome_wizard() {
    cat > /usr/local/bin/ming-welcome << 'WELCOMEPY'
#!/usr/bin/env python3
# Ming OS 26.3.1 首次启动欢迎引导

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
        window { background-color: #F6F8F6; border-radius: 16px; }
        .welcome-title { font-size: 28px; font-weight: bold; color: #1D2421; margin-top: 30px; }
        .welcome-subtitle { font-size: 16px; color: #4F625A; margin-top: 10px; margin-bottom: 20px; }
        .big-button { font-size: 18px; padding: 16px 40px; border-radius: 12px;
                      background-color: #1FA89E; color: white; border: none; min-height: 52px; }
        .big-button:hover { background-color: #1FA89E; }
        .big-button-alt { font-size: 18px; padding: 16px 40px; border-radius: 12px;
                          background-color: rgba(255,255,255,0.84); color: #1D2421; border: 1px solid rgba(31,98,84,0.18); min-height: 52px; }
        .step-label { font-size: 14px; color: #4F625A; margin-top: 16px; }
        .done-icon { font-size: 64px; color: #1FA89E; }
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
        title.set_markup('<span size="36000" weight="bold" foreground="#D4F7F1">🎉 欢迎使用 Ming OS</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="16000" foreground="#6DCFC4">让电脑更简单，让人人都会用</span>')
        subtitle.set_margin_bottom(30)

        btn = Gtk.Button(label='开始设置')
        btn.get_style_context().add_class('big-button')
        btn.set_size_request(240, 56)
        btn.connect('clicked', lambda w: self.next_step())

        dots = Gtk.Label()
        dots.set_markup('<span size="12000" foreground="#1FA89E">● ○ ○</span>')
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
        title.set_markup('<span size="24000" weight="bold" foreground="#D4F7F1">📶 连接到网络</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="14000" foreground="#6DCFC4">Wi-Fi 可以让您上网、更新系统和下载应用</span>')
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
        dots.set_markup('<span size="12000" foreground="#1FA89E">○ ● ○</span>')
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
        icon.set_markup('<span size="48000" foreground="#1FA89E">✨</span>')
        icon.set_margin_bottom(10)

        title = Gtk.Label()
        title.set_markup('<span size="28000" weight="bold" foreground="#D4F7F1">一切就绪！</span>')
        title.set_margin_bottom(10)

        subtitle = Gtk.Label()
        subtitle.set_markup('<span size="14000" foreground="#6DCFC4">您可以随时在底部 Dock 找到常用应用</span>')
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

# 始终先确保免密自动登录已就位（双保险，独立于用户选择）
ensure_autologin() {
    # 入 autologin / nopasswdlogin 组
    pkexec /bin/bash -c "
        for g in autologin nopasswdlogin; do
            getent group \$g >/dev/null 2>&1 || groupadd -r \$g 2>/dev/null || true
            usermod -aG \$g '${CUR_USER}' 2>/dev/null || true
        done
        mkdir -p /etc/lightdm/lightdm.conf.d
        cat > /etc/lightdm/lightdm.conf.d/50-ming-autologin.conf <<EOF
[Seat:*]
autologin-user=${CUR_USER}
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
EOF
        chmod 0644 /etc/lightdm/lightdm.conf.d/50-ming-autologin.conf
    " 2>/dev/null || true
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
    mkdir -p "$(dirname "${MARKER}")"
    echo "skipped" > "${MARKER}"
    dialog --title="已跳过" --text="已为您启用免密自动登录。\n开机将直接进入桌面。" \
        --width=380 --button="好的:0" 2>/dev/null || true
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
    mkdir -p "$(dirname "${MARKER}")"
    echo "skipped" > "${MARKER}"
    exit 0
fi

FULLNAME=$(echo "${FORM}" | cut -d'|' -f1)
PW1=$(echo "${FORM}" | cut -d'|' -f2)
PW2=$(echo "${FORM}" | cut -d'|' -f3)

ensure_autologin

# 设置显示名
if [[ -n "${FULLNAME}" ]]; then
    pkexec chfn -f "${FULLNAME}" "${CUR_USER}" 2>/dev/null || true
fi

# 设置密码（用于 sudo/解锁；登录仍自动免密）
if [[ -n "${PW1}" ]]; then
    if [[ "${PW1}" != "${PW2}" ]]; then
        dialog --title="提示" --text="两次密码不一致，已保持免密登录。\n可稍后在「设置中心」修改。" \
            --width=380 --button="好的:0" 2>/dev/null || true
    else
        echo -e "${PW1}\n${PW1}" | pkexec passwd "${CUR_USER}" 2>/dev/null \
            || pkexec /bin/bash -c "echo '${CUR_USER}:${PW1}' | chpasswd" 2>/dev/null || true
    fi
fi

mkdir -p "$(dirname "${MARKER}")"
echo "configured" > "${MARKER}"

dialog --title="完成" \
    --text="账户设置完成。\n开机仍会自动进入桌面，无需输入密码。" \
    --width=380 --button="开始使用:0" 2>/dev/null || true
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
- id: ming-identity
  module: shellprocess
  config: ming-identity.conf
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
  - partition
  - mount
  - unpackfs
  - machineid
  - fstab
  - locale
  - keyboard
  - localecfg
  - users
  - displaymanager
  - networkcfg
  - hwclock
  - initramfs
  - grubcfg
  - shellprocess@ming-bootloader
  - shellprocess@ming-identity
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
    if command -v parted >/dev/null 2>&1; then
        parted -s "${disk}" mklabel msdos >> "${LOG}" 2>&1 || true
    fi
done

exit 0
CALAMARESPREFLIGHT
    chmod +x /usr/local/sbin/ming-calamares-preflight

    # 静态兜底：确保无论 preflight 是否成功执行，
    # settings.conf/users.conf/partition.conf 都是一键安装版本。
    # 这一步由 03_desktop.sh 负责写入，resume_build 也会执行到这里。
    mkdir -p /etc/calamares/modules
    cat > /etc/calamares/settings.conf << 'STATICCALASETTINGS'
---
modules-search: [ local, /usr/lib/x86_64-linux-gnu/calamares/modules, /usr/lib/calamares/modules ]
instances:
- id: ming-identity
  module: shellprocess
  config: ming-identity.conf
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
  - partition
  - mount
  - unpackfs
  - machineid
  - fstab
  - locale
  - keyboard
  - localecfg
  - users
  - displaymanager
  - networkcfg
  - hwclock
  - initramfs
  - grubcfg
  - shellprocess@ming-bootloader
  - shellprocess@ming-identity
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
initialPartitioningChoice: erase
initialSwapChoice: none
requiredStorage: 12
allowManualPartitioning: false
STATICPARTCONF

    cat > /etc/calamares/modules/users.conf << 'STATICUSERSCONF'
---
defaultGroups:
  - users
  - audio
  - video
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
        if command -v parted >/dev/null 2>&1; then
            parted -s "${disk}" mklabel msdos >> /tmp/ming-installer/preflight.log 2>&1 || true
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

    # Kiosk session：无 WM → Calamares 无标题栏无关闭按钮，关了自动重启
    cat > /usr/local/bin/ming-installer-session << 'KIOSK'
#!/usr/bin/env bash
xsetroot -solid '#0c1f1c'
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
        if command -v parted >/dev/null 2>&1; then
            parted -s "${disk}" mklabel gpt >> /tmp/ming-installer/preflight.log 2>&1 || true
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


    systemctl enable ming-live-installer.service 2>/dev/null || true
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
    <property name="frame_opacity" type="int" value="95"/>
    <property name="full_width_title" type="bool" value="true"/>
    <property name="horiz_scroll_opacity" type="bool" value="false"/>
    <property name="inactive_opacity" type="int" value="92"/>
    <property name="maximized_offset" type="int" value="0"/>
    <property name="mousewheel_rollup" type="bool" value="true"/>
    <property name="move_opacity" type="int" value="85"/>
    <property name="placement_mode" type="string" value="center"/>
    <property name="placement_ratio" type="int" value="50"/>
    <property name="popup_opacity" type="int" value="100"/>
    <property name="prevent_focus_stealing" type="bool" value="false"/>
    <property name="raise_delay" type="int" value="200"/>
    <property name="raise_on_click" type="bool" value="true"/>
    <property name="raise_on_focus" type="bool" value="false"/>
    <property name="resize_opacity" type="int" value="85"/>
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
    <property name="title_font" type="string" value="WenQuanYi Micro Hei Bold 11"/>
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
    <property name="FontName" type="string" value="WenQuanYi Micro Hei 11"/>
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

    # Whisker Menu 配置（26.3.0 玻璃主题版）
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
favorites=ming-control-center.desktop,ming-files.desktop,firefox-esr.desktop,spark-store.desktop,ming-wechat.desktop,garlic-claw.desktop,ming-update.desktop,ming-master.desktop,ming-terminal.desktop
command-settings=ming-control-center
command-lockscreen=xflock4
command-switchuser=dm-tool switch-to-greeter
command-logoutuser=xfce4-session-logout --logout
command-restart=xfce4-session-logout --reboot
command-shutdown=xfce4-session-logout --halt
search-actions=1
position-categories-alternate=false
position-commands-alternate=true
position-search-alternate=false
category-icon-size=1
item-icon-size=2
menu-width=440
menu-height=540
menu-opacity=95
background-opacity=88
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
WALL_PNG="/usr/share/backgrounds/ming-os/default.png"
WALL_1366="/usr/share/backgrounds/ming-os/default-1366x768.png"

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

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
PLANK_SETTINGS="${HOME}/.config/plank/dock1/settings"
if [[ "${MEM_MB}" -le 2600 && -f "${PLANK_SETTINGS}" ]]; then
    sed -i "s/^IconSize=.*/IconSize=36/" "${PLANK_SETTINGS}" 2>/dev/null || true
    sed -i "s/^ZoomEnabled=.*/ZoomEnabled=false/" "${PLANK_SETTINGS}" 2>/dev/null || true
    sed -i "s/^ZoomPercent=.*/ZoomPercent=110/" "${PLANK_SETTINGS}" 2>/dev/null || true
fi

# 刷新桌面
xfdesktop --reload 2>/dev/null || true

# Dock-only 桌面：Xfce 面板只作为兼容组件安装，不作为可见任务栏运行。
xfce4-panel --quit >/dev/null 2>&1 || true

# 确保 Plank Dock 在运行（picom 启动后）
if command -v plank &>/dev/null && ! pgrep -x plank &>/dev/null; then
    (sleep 1 && nohup plank >/dev/null 2>&1 &) 2>/dev/null || true
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
    echo "=====> [03_desktop] 开始 Ming OS 26.3.1 Dock 桌面定制 <====="

    generate_ming_icons
    configure_hidpi_autoscale
    install_themes
    setup_wallpaper
    configure_ming_shell
    ensure_wps_office
    configure_xfce_settings      # 先写桌面/xfwm/xsettings（含壁纸 backdrop）
    configure_xfce_panel         # 顶部 macOS 菜单栏
    configure_plank_dock         # 底部可放大 Dock
    configure_picom
    configure_touch_input        # 触屏手势 + Onboard 虚拟键盘
    configure_notification_filter
    configure_simplified_menus   # 只动 Thunar 右键菜单，不再覆盖桌面/xfwm 配置
    deploy_release_readme
    configure_autostart
    deploy_live_installer
    setup_account_oobe
    setup_welcome_wizard
    configure_appearance_enforcer  # 最后部署登录期自愈强制应用

    echo "=====> [03_desktop] Ming OS 26.3.1 Dock 桌面定制完成 <====="
}

main


