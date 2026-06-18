#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 02: 应用软件安装
# ============================================================================
# 设计意图：
#   安装桌面环境核心组件、中文输入法、预装应用（WPS/微信/Firefox）、
#   星火应用商店以及中文字体。
#   所有安装均在 chroot 中以非交互模式完成。
#
# 输入：
#   环境变量: ONION_USER
#
# 输出：
#   安装完成的桌面环境与应用软件
#
# 关键步骤：
#   1. 安装 Xfce 4.18 桌面环境与 Compton 合成器
#   2. 安装 LightDM 显示管理器（自动登录）
#   3. 安装 Firefox ESR 浏览器
#   4. 安装 WPS Office（官方 deb）及字体依赖
#   5. 安装微信（腾讯官方 Linux 版 + Onion 低内存包装器）
#   6. 安装 Fcitx5 中文输入法
#   7. 安装星火应用商店（按需安装应用，避免低内存设备后台批量装软件）
#   8. 安装中文字体
# ============================================================================

set -uo pipefail

# ======================== 桌面环境 ========================

install_xfce_desktop() {
    apt install -y --no-install-recommends \
        xserver-xorg \
        xserver-xorg-video-intel \
        xserver-xorg-video-amdgpu \
        xserver-xorg-video-ati \
        xserver-xorg-video-nouveau \
        xserver-xorg-input-libinput \
        xfce4 \
        xfce4-panel \
        xfce4-session \
        xfce4-settings \
        xfce4-terminal \
        xfce4-appfinder \
        xfce4-whiskermenu-plugin \
        xfce4-taskmanager \
        xfce4-notifyd \
        python3-gi \
        gir1.2-gtk-3.0 \
        thunar \
        thunar-archive-plugin \
        thunar-media-tags-plugin \
        thunar-volman \
        tumbler \
        mousepad \
        ristretto \
        xdg-user-dirs \
        xdg-utils \
        desktop-base \
        xfce4-power-manager \
        xfce4-power-manager-plugins

    apt install -y --no-install-recommends \
        picom \
        plank \
        librsvg2-bin \
        librsvg2-common \
        imagemagick

    mkdir -p /etc/xdg/picom
    cat > /etc/xdg/picom/picom.conf << PICOMDEFAULT
backend = "glx";
vsync = true;
unredir-if-possible = true;

shadow = true;
shadow-radius = 8;
shadow-opacity = 0.5;
shadow-offset-x = -8;
shadow-offset-y = -8;
shadow-exclude = [
    "name = 'Notification'",
    "class_g = 'Conky'",
    "class_g ?= 'Notify-osd'",
    "class_g = 'Cairo-clock'",
    "_GTK_FRAME_EXTENTS@:c",
    "name = 'xfce4-notifyd'",
    "window_type = 'dock'",
    "window_type = 'desktop'"
];

fading = true;
fade-in-step = 3.0e-2;
fade-out-step = 3.0e-2;
fade-delta = 4;

inactive-opacity = 0.92;
frame-opacity = 0.95;
inactive-opacity-override = false;

blur-background = true;
blur-background-frame = true;
blur-background-fixed = true;
blur-background-exclude = [
    "window_type = 'dock'",
    "window_type = 'desktop'",
    "_GTK_FRAME_EXTENTS@:c"
];
blur-method = "dual_kawase";
blur-strength = 5;

wintypes:
{
    tooltip = { fade = true; shadow = true; opacity = 0.9; focus = true; };
    dock = { shadow = false; };
    dnd = { shadow = false; };
    popup_menu = { opacity = 0.95; };
    dropdown_menu = { opacity = 0.95; };
};

detect-client-leader = true;
detect-transient = true;
use-damage = true;
log-level = "warn";
xrender-sync-fence = true;
PICOMDEFAULT

    # 老旧GPU回退配置（当 GLX 不可用时自动降级到 xrender）
    mkdir -p /etc/xdg/picom-backup
    cat > /etc/xdg/picom/picom-fallback.conf << PICOMFALLBACK
backend = "xrender";
vsync = false;
unredir-if-possible = false;
shadow = false;
fading = true;
fade-in-step = 5.0e-2;
fade-out-step = 5.0e-2;
fade-delta = 4;
inactive-opacity = 0.95;
frame-opacity = 0.98;
inactive-opacity-override = false;
use-damage = true;
log-level = "warn";
detect-client-leader = true;
detect-transient = true;
PICOMFALLBACK

    # Picom 启动包装器（自动探测 GLX 可用性，回退 xrender）
    cat > /usr/local/bin/onion-picom << 'PICOMWRAP'
#!/usr/bin/env bash
CONF="${HOME}/.config/picom/picom.conf"
FALLBACK="/etc/xdg/picom/picom-fallback.conf"
PICOM_BIN=$(command -v picom 2>/dev/null || echo "picom")
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)

if [ "${MEM_MB}" -le 2600 ]; then
    exec ${PICOM_BIN} --config "${FALLBACK}" -b "$@"
fi

HAS_GPU=0
if [ -e /dev/dri/card0 ] || [ -e /dev/dri/renderD128 ] || [ -e /dev/dri/card1 ]; then
    HAS_GPU=1
fi
if [ "$HAS_GPU" -eq 0 ] && command -v glxinfo &>/dev/null; then
    if glxinfo 2>/dev/null | grep -q "direct rendering: Yes"; then
        HAS_GPU=1
    fi
fi
if [ "$HAS_GPU" -eq 0 ]; then
    if ${PICOM_BIN} --config "${CONF}" --backend glx --no-fading-openclose 2>/dev/null &
        PICOM_PID=$!
        sleep 1
        kill "${PICOM_PID}" 2>/dev/null
        wait "${PICOM_PID}" 2>/dev/null
    then
        HAS_GPU=1
    fi
fi

if [ "$HAS_GPU" -eq 1 ]; then
    exec ${PICOM_BIN} --config "${CONF}" -b "$@"
else
    exec ${PICOM_BIN} --config "${FALLBACK}" -b "$@"
fi
PICOMWRAP
    chmod +x /usr/local/bin/onion-picom

    echo "lightdm lightdm/default-display-manager select lightdm" | debconf-set-selections
    apt install -y --no-install-recommends \
        lightdm \
        lightdm-gtk-greeter \
        plymouth \
        plymouth-themes

    mkdir -p /etc/plymouth
    echo -e "[Daemon]\nTheme=onion-os\nShowDelay=0" > /etc/plymouth/plymouthd.conf
    mkdir -p /usr/share/plymouth/themes/onion-os
    cat > /usr/share/plymouth/themes/onion-os/onion-os.plymouth << PLYMOUTHCONF
[Plymouth Theme]
Name=Onion OS
Description=Onion OS Boot Splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/onion-os
ScriptFile=/usr/share/plymouth/themes/onion-os/onion-os.script
PLYMOUTHCONF

    cat > /usr/share/plymouth/themes/onion-os/onion-os.script << 'PLYMOUTHSCRIPT'
wallpaper_image = Image("wallpaper.png");
screen_width = Window.GetWidth();
screen_height = Window.GetHeight();
resized_wallpaper = wallpaper_image.Scale(screen_width, screen_height);
resized_wallpaper.SetOpacity(0.8);
logo_image = Image("logo.png");
logo_sprite = Sprite(logo_image);
logo_sprite.SetX(screen_width / 2 - logo_image.GetWidth() / 2);
logo_sprite.SetY(screen_height / 2 - logo_image.GetHeight() / 2);
message_sprite = Sprite();
message_sprite.SetX(screen_width / 2);
message_sprite.SetY(screen_height / 2 + logo_image.GetHeight() / 2 + 20);

progress = 0;
fun refresh_callback()
    progress = progress + 0.01;
    if (progress > 1)
        progress = 1;
    opacity = 1 - progress;
    logo_sprite.SetOpacity(opacity);
    message_sprite.SetOpacity(opacity);
    resized_wallpaper.SetOpacity(0.8 * opacity);
end

Plymouth.SetRefreshFunction(refresh_callback);

fun quit_callback()
    if (Plymouth.GetMode() == "shutdown")
        return;
    message_sprite.SetText("欢迎使用 Onion OS");
end

Plymouth.SetQuitFunction(quit_callback);

fun message_callback(message)
    message_sprite.SetText(message);
end

Plymouth.SetMessageFunction(message_callback);
PLYMOUTHSCRIPT

    plymouth-set-default-theme onion-os 2>/dev/null || true

    systemctl enable lightdm 2>/dev/null || true

    install_vbox_guest_and_display

    mkdir -p /etc/live/config.conf.d
    cat > /etc/live/config.conf.d/onion-autologin.conf << LIVECONFIG
# Keep Ventoy/Live boots on the same default account as the installed system.
LIVE_USERNAME="${ONION_USER}"
LIVE_USER_FULLNAME="Onion OS User"
LIVE_HOSTNAME="onion-os"
LIVE_USER_DEFAULT_GROUPS="audio cdrom dip floppy video plugdev netdev powerdev scanner bluetooth sudo adm lpadmin nopasswdlogin autologin"
LIVECONFIG

    mkdir -p /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/50-onion-autologin.conf << AUTOLOGIN
[Seat:*]
autologin-user=${ONION_USER}
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
AUTOLOGIN

    cat > /etc/lightdm/lightdm-gtk-greeter.conf << GREETERCFG
[greeter]
theme-name = Onion-Glass
icon-theme-name = Papirus
font-name = WenQuanYi Micro Hei 11
background = /usr/share/backgrounds/onion-os/default.png
user-background = false
GREETERCFG

    cat > /usr/local/bin/onion-autologin-setup << 'AUTOLOGINSETUP'
#!/usr/bin/env bash
set -euo pipefail

is_live_environment() {
    grep -qw "boot=live" /proc/cmdline 2>/dev/null && return 0
    grep -qw "live-config" /proc/cmdline 2>/dev/null && return 0
    [ -d /lib/live/mount/medium ] && return 0
    [ -f /.disk/info ] && return 0
    return 1
}

target_user=""
if is_live_environment && id onion >/dev/null 2>&1; then
    target_user="onion"
fi
if [[ -z "${target_user}" ]]; then
    target_user="$(awk -F: '$3 >= 1000 && $3 < 60000 && $1 != "nobody" && $1 != "onion" {print $1; exit}' /etc/passwd)"
fi
if [[ -z "${target_user}" ]] && id onion >/dev/null 2>&1; then
    target_user="onion"
fi

if [[ -z "${target_user}" ]]; then
    exit 0
fi

for grp in nopasswdlogin autologin; do
    getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    usermod -aG "${grp}" "${target_user}" 2>/dev/null || true
done

mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-onion-autologin.conf << AUTOLOGIN
[Seat:*]
autologin-user=${target_user}
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
AUTOLOGIN

    chmod 0644 /etc/lightdm/lightdm.conf.d/50-onion-autologin.conf
AUTOLOGINSETUP
    chmod +x /usr/local/bin/onion-autologin-setup

    cat > /usr/local/bin/onion-getty-autologin << 'GETTYAUTO'
#!/usr/bin/env bash
set -euo pipefail

tty_name="${1:-tty1}"
term="${2:-linux}"

is_live_environment() {
    grep -qw "boot=live" /proc/cmdline 2>/dev/null && return 0
    grep -qw "live-config" /proc/cmdline 2>/dev/null && return 0
    [ -d /lib/live/mount/medium ] && return 0
    [ -f /.disk/info ] && return 0
    return 1
}

agetty_args=(--noclear)
case "${tty_name}" in
    ttyS*|hvc*|xvc*|hvsi*)
        agetty_args=(--keep-baud 115200,38400,9600 --noclear)
        ;;
esac

if is_live_environment && id onion >/dev/null 2>&1; then
    agetty_args=(--autologin onion "${agetty_args[@]}")
fi

exec /sbin/agetty "${agetty_args[@]}" "${tty_name}" "${term}"
GETTYAUTO
    chmod +x /usr/local/bin/onion-getty-autologin

    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/10-onion-live-autologin.conf << 'GETTYTTY1'
[Unit]
After=live-config.service onion-autologin-setup.service
Wants=onion-autologin-setup.service

[Service]
ExecStart=
ExecStart=-/usr/local/bin/onion-getty-autologin %I linux
GETTYTTY1

    mkdir -p /etc/systemd/system/serial-getty@ttyS0.service.d
    cat > /etc/systemd/system/serial-getty@ttyS0.service.d/10-onion-live-autologin.conf << 'GETTYSERIAL'
[Unit]
After=live-config.service onion-autologin-setup.service
Wants=onion-autologin-setup.service

[Service]
ExecStart=
ExecStart=-/usr/local/bin/onion-getty-autologin %I vt102
GETTYSERIAL

    cat > /etc/systemd/system/onion-autologin-setup.service << 'AUTOLOGINSVC'
[Unit]
Description=Onion OS automatic desktop login setup
After=local-fs.target live-config.service
Before=lightdm.service display-manager.service
ConditionPathExists=/etc/lightdm

[Service]
Type=oneshot
ExecStart=/usr/local/bin/onion-autologin-setup

[Install]
WantedBy=multi-user.target graphical.target
AUTOLOGINSVC

    systemctl enable onion-autologin-setup.service 2>/dev/null || true
    /usr/local/bin/onion-autologin-setup 2>/dev/null || true
}

# ======================== VirtualBox / 虚拟机显示 ========================

install_vbox_guest_and_display() {
    apt install -y --no-install-recommends \
        xserver-xorg-video-vesa \
        xserver-xorg-video-fbdev \
        xserver-xorg-video-vmware \
        xserver-xorg-video-qxl \
        xserver-xorg-video-modesetting \
        || true

    if apt-cache show virtualbox-guest-utils virtualbox-guest-x11 >/dev/null 2>&1; then
        apt install -y --no-install-recommends \
            virtualbox-guest-utils \
            virtualbox-guest-x11 \
            || true
        systemctl enable vboxadd-service 2>/dev/null || true
    fi
}

# ======================== 中文字体 ========================

install_fonts() {
    apt install -y --no-install-recommends \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-noto-cjk \
        fonts-noto-cjk-extra

    apt install -y --no-install-recommends fonts-liberation fonts-croscore || true

    fc-cache -f -v
}

# ======================== Firefox ESR ========================

install_firefox() {
    apt install -y --no-install-recommends \
        firefox-esr \
        firefox-esr-l10n-zh-cn

    sudo -u "${ONION_USER}" xdg-settings set default-web-browser firefox-esr.desktop 2>/dev/null || true
}

# ======================== WPS Office ========================

install_wps_office() {
    local wps_page="https://linux.wps.cn/"
    local wps_url=""
    local wps_deb="/tmp/wps-office.deb"

    wps_url=$(curl -fsSL "${wps_page}" 2>/dev/null \
        | grep -oE "https://wps-linux-personal\.wpscdn\.cn/wps/download/ep/[^']+_amd64\.deb" \
        | head -n1 || true)
    if [[ -z "${wps_url}" ]]; then
        wps_url="https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/26885/wps-office_12.1.2.26885.AK.preread.sw.Personal_715971_amd64.deb"
    fi

    # WPS Linux downloads are protected by a public time+md5 token used by the
    # official download page. Generate the same token so unattended ISO builds
    # do not fail with "secure-time-arg-time-not-found".
    local wps_path timestamp token
    wps_path="${wps_url#https://wps-linux-personal.wpscdn.cn}"
    timestamp="$(date +%s)"
    token="$(printf '7f8faaaa468174dc1c9cd62e5f218a5b%s%s' "${wps_path}" "${timestamp}" | md5sum | awk '{print $1}')"
    wps_url="${wps_url}?t=${timestamp}&k=${token}"

    echo "下载 WPS Office..."
    if wget -q --show-progress -O "${wps_deb}" "${wps_url}" 2>/dev/null; then
        apt install -y "${wps_deb}" || apt install -y -f
        rm -f "${wps_deb}"
    else
        echo "[WARN] WPS Office 下载失败，跳过。用户可后续从应用商店安装。"
        rm -f "${wps_deb}"
        return 0
    fi

    apt install -y --no-install-recommends \
        libglu1-mesa \
        libxslt1.1 \
        libxml2

    if [[ -d /usr/share/fonts/wps-office ]]; then
        ln -sf /usr/share/fonts/truetype/wqy /usr/share/fonts/wps-office/wqy 2>/dev/null || true
    fi
}

# ======================== 微信 (官方 Linux 版 + 低内存包装器) ========================

install_wechat() {
    local wechat_url="https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
    local wechat_deb="/tmp/wechat.deb"

    echo "下载微信官方 Linux 版..."
    if wget -q --show-progress -O "${wechat_deb}" "${wechat_url}" 2>/dev/null; then
        apt install -y "${wechat_deb}" || apt install -y -f
        rm -f "${wechat_deb}"
    else
        echo "[WARN] 微信官方 deb 下载失败，跳过。用户可后续运行 onion-install-wechat 安装。"
        rm -f "${wechat_deb}"
    fi

    cat > /usr/local/bin/onion-install-wechat << 'WECHATINSTALL'
#!/usr/bin/env bash
set -euo pipefail
url="https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
deb="/tmp/WeChatLinux_x86_64.deb"
echo "Downloading official WeChat for Linux..."
wget -c --show-progress -O "${deb}" "${url}"
sudo apt install -y "${deb}" || sudo apt install -y -f
rm -f "${deb}"
echo "WeChat installed."
WECHATINSTALL
    chmod +x /usr/local/bin/onion-install-wechat

    cat > /usr/local/bin/onion-wechat << 'WECHATWRAP'
#!/usr/bin/env bash
set -uo pipefail

find_wechat_bin() {
    for bin in \
        /usr/bin/wechat \
        /usr/bin/weixin \
        /opt/wechat/wechat \
        /opt/weixin/weixin \
        /opt/apps/com.tencent.wechat/files/wechat \
        /opt/apps/com.tencent.wechat/files/bin/wechat; do
        if [[ -x "${bin}" ]]; then
            echo "${bin}"
            return 0
        fi
    done
    command -v wechat 2>/dev/null || command -v weixin 2>/dev/null || return 1
}

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
mode="${ONION_WECHAT_MODE:-auto}"
wechat_bin="$(find_wechat_bin || true)"

if [[ -z "${wechat_bin}" ]]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --question \
            --title="微信未安装" \
            --text="未找到微信。是否现在下载安装官方 Linux 版？" \
            --ok-label="安装" --cancel-label="取消" 2>/dev/null || exit 1
    fi
    pkexec /usr/local/bin/onion-install-wechat || sudo /usr/local/bin/onion-install-wechat || exit 1
    wechat_bin="$(find_wechat_bin || true)"
fi

mkdir -p "${HOME}/.cache/onion-os"

if [[ "${mode}" == "auto" && "${mem_mb}" -le 2600 ]]; then
    mode="light"
fi

if [[ "${mode}" == "light" ]]; then
    note="${HOME}/.config/onion-os/wechat-low-memory-note"
    if [[ ! -f "${note}" ]] && command -v zenity >/dev/null 2>&1; then
        mkdir -p "$(dirname "${note}")"
        if ! zenity --question \
            --title="微信省内存模式" \
            --text="检测到本机内存约 ${mem_mb}MB。\n\n微信好友和群组较多时会明显占用内存。Onion OS 会用低缓存、低优先级方式启动微信；如果仍然卡顿，可以改用网页版。" \
            --ok-label="省内存启动" \
            --cancel-label="改用网页版" \
            --width=460 2>/dev/null; then
            echo "shown" > "${note}"
            exec /usr/local/bin/onion-wechat-web
        fi
        echo "shown" > "${note}"
    fi

    find "${HOME}/.cache" -maxdepth 3 \( -iname '*wechat*' -o -iname '*weixin*' \) \
        -type f -size +16M -delete 2>/dev/null || true
    export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:-} --disable-gpu-shader-disk-cache --disable-accelerated-video-decode --disable-background-networking --disk-cache-size=67108864 --media-cache-size=33554432"
    export ELECTRON_DISABLE_SECURITY_WARNINGS=1
    export GDK_BACKEND=x11

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i wechat "微信省内存模式" "已启用低缓存、低优先级和桌面保护策略。" 2>/dev/null || true
    fi

    if command -v systemd-run >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
        exec systemd-run --user --scope \
            -p MemoryHigh=1050M \
            -p MemoryMax=1450M \
            -p CPUWeight=60 \
            -p IOWeight=50 \
            nice -n 8 ionice -c3 "${wechat_bin}" "$@"
    fi

    exec nice -n 8 ionice -c3 "${wechat_bin}" "$@"
fi

exec "${wechat_bin}" "$@"
WECHATWRAP
    chmod +x /usr/local/bin/onion-wechat

    cat > /usr/local/bin/onion-wechat-web << 'WECHATWEB'
#!/usr/bin/env bash
set -e
url="https://wx.qq.com/"
if command -v firefox-esr >/dev/null 2>&1; then
    exec firefox-esr --new-window "${url}"
elif command -v xdg-open >/dev/null 2>&1; then
    exec xdg-open "${url}"
else
    echo "${url}"
fi
WECHATWEB
    chmod +x /usr/local/bin/onion-wechat-web

    mkdir -p /home/${ONION_USER}/Desktop
    cat > /home/${ONION_USER}/Desktop/wechat.desktop << WECHATDESKTOP
[Desktop Entry]
Name=微信
Name[zh_CN]=微信
Comment=Official WeChat for Linux with Onion low-memory guard
Exec=/usr/local/bin/onion-wechat
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATDESKTOP
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/Desktop/wechat.desktop"
    chmod +x "/home/${ONION_USER}/Desktop/wechat.desktop"

    cat > /usr/share/applications/onion-wechat.desktop << WECHATDESKTOPSYS
[Desktop Entry]
Name=微信
Name[zh_CN]=微信
Comment=Official WeChat for Linux with Onion low-memory guard
Exec=/usr/local/bin/onion-wechat
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATDESKTOPSYS

    cat > /usr/share/applications/onion-wechat-web.desktop << WECHATWEBDESKTOP
[Desktop Entry]
Name=微信网页版
Name[zh_CN]=微信网页版
Comment=Use web WeChat when memory is too limited for the desktop client
Exec=/usr/local/bin/onion-wechat-web
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATWEBDESKTOP
}

# ======================== Fcitx5 中文输入法 ========================

install_fcitx5() {
    apt install -y --no-install-recommends \
        fcitx5 \
        fcitx5-chinese-addons \
        fcitx5-frontend-gtk3 \
        fcitx5-frontend-gtk4 \
        fcitx5-frontend-qt5 \
        fcitx5-config-qt \
        fcitx5-material-color

    sudo -u "${ONION_USER}" bash -c 'cat > ~/.xinputrc << XINPUTRC
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export GLFW_IM_MODULE=ibus
fcitx5 -d --replace
XINPUTRC'

    sudo -u "${ONION_USER}" mkdir -p /home/${ONION_USER}/.config/fcitx5/profile
    sudo -u "${ONION_USER}" bash -c 'cat > /home/'${ONION_USER}'/.config/fcitx5/profile/default << FCITX5PROFILE
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[GroupOrder]
0=Default
FCITX5PROFILE'
}

# ======================== 应用商店 (星火应用商店) ========================

install_app_store() {
    apt install -y --no-install-recommends \
        jq \
        apt-transport-https \
        xdg-utils \
        xdg-desktop-portal \
        xdg-desktop-portal-gtk \
        libnotify-bin

    cat > /usr/local/bin/onion-install-spark-store << 'SPARKINSTALL'
#!/usr/bin/env bash
set -euo pipefail

api="https://gitee.com/api/v5/repos/spark-store-project/spark-store/releases/latest"
fallback="https://gitee.com/spark-store-project/spark-store/releases/download/5.1.1/spark-store_5.1.1_amd64.deb"
deb="/tmp/spark-store.deb"

echo "Resolving latest Spark Store release..."
url="$(curl -fsSL "${api}" 2>/dev/null | jq -r '.assets[]? | select(.name | test("_amd64\\.deb$")) | .browser_download_url' | head -n1 || true)"
if [[ -z "${url}" || "${url}" == "null" ]]; then
    url="${fallback}"
fi

echo "Downloading Spark Store: ${url}"
wget -c --show-progress -O "${deb}" "${url}"
mkdir -p /root/.config "${HOME:-/root}/.config"
touch /root/.config/mimeapps.list "${HOME:-/root}/.config/mimeapps.list" 2>/dev/null || true
sudo apt install -y "${deb}" || sudo apt install -y -f
rm -f "${deb}"
echo "Spark Store installed."
SPARKINSTALL
    chmod +x /usr/local/bin/onion-install-spark-store

    if ! /usr/local/bin/onion-install-spark-store; then
        echo "[WARN] 星火应用商店安装失败，保留 onion-install-spark-store 供用户联网后重试。"
    fi

    # 推荐应用改为打开星火商店，避免在 2GB 设备上首次登录就后台批量安装。
    cat > /usr/local/bin/onion-app-recommend << 'RECOMMEND'
#!/usr/bin/env bash
# Onion OS 推荐应用入口 - 低内存机器不做后台批量安装

MARKER="${HOME}/.config/onion-os/app-recommend-done"
if [[ -f "${MARKER}" ]]; then
    exit 0
fi

mkdir -p "$(dirname "${MARKER}")"

if command -v notify-send >/dev/null 2>&1; then
    notify-send -i onion-app-store "Onion OS 应用商店" "常用软件请从星火应用商店按需安装，2GB 设备不会后台批量装应用。" 2>/dev/null || true
fi

echo "done" > "${MARKER}"
RECOMMEND

    chmod +x /usr/local/bin/onion-app-recommend

    # 创建应用商店桌面快捷方式
    mkdir -p /home/${ONION_USER}/Desktop
    cat > /home/${ONION_USER}/Desktop/spark-store.desktop << SPARKDESKTOP
[Desktop Entry]
Name=星火应用商店
Name[zh_CN]=星火应用商店
Comment=Install Chinese Linux applications on demand
Comment[zh_CN]=按需安装适合中文用户的 Linux 应用
Exec=spark-store
Icon=onion-app-store
Terminal=false
Type=Application
Categories=System;PackageManager;
Keywords=software;store;app;install;spark;应用;商店;软件;星火;安装;
StartupNotify=true
SPARKDESKTOP
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/Desktop/spark-store.desktop"
    chmod +x "/home/${ONION_USER}/Desktop/spark-store.desktop"

    # 同样更新 system 级 desktop（菜单用）
    cat > /usr/share/applications/spark-store.desktop << SPARKSYSDESKTOP
[Desktop Entry]
Name=星火应用商店
Name[zh_CN]=星火应用商店
Comment=Install Chinese Linux applications on demand
Comment[zh_CN]=按需安装适合中文用户的 Linux 应用
Exec=spark-store
Icon=onion-app-store
Terminal=false
Type=Application
Categories=System;PackageManager;
Keywords=software;store;app;install;spark;应用;商店;软件;星火;安装;
StartupNotify=true
SPARKSYSDESKTOP

    cat > /usr/share/applications/onion-install-spark-store.desktop << SPARKINSTALLDESKTOP
[Desktop Entry]
Name=修复星火应用商店
Name[zh_CN]=修复星火应用商店
Comment=Download and install Spark Store if it was not bundled during image build
Exec=pkexec /usr/local/bin/onion-install-spark-store
Icon=onion-app-store
Terminal=true
Type=Application
Categories=System;PackageManager;
StartupNotify=true
SPARKINSTALLDESKTOP

    # 推荐应用首次启动项
    mkdir -p "/home/${ONION_USER}/.config/autostart"
    cat > "/home/${ONION_USER}/.config/autostart/onion-app-recommend.desktop" << APPRECAUTOSTART
[Desktop Entry]
Type=Application
Name=Onion App Recommendations
Comment=Recommended apps for Onion OS
Exec=/usr/local/bin/onion-app-recommend
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=15
APPRECAUTOSTART
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/.config/autostart/onion-app-recommend.desktop"

    cat > /etc/systemd/system/onion-appstore-ready.service << 'SVCUNIT'
[Unit]
Description=Onion OS App Store Readiness
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'command -v spark-store >/dev/null 2>&1 || /usr/local/bin/onion-install-spark-store || true'
TimeoutStartSec=90
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SVCUNIT

    systemctl enable onion-appstore-ready.service 2>/dev/null || true
}

# ======================== 附加实用工具 ========================

install_utilities() {
    apt install -y --no-install-recommends \
        pavucontrol \
        pulseaudio \
        pulseaudio-module-bluetooth \
        alsa-utils \
        volumeicon-alsa \
        gnome-calculator \
        gnome-screenshot \
        evince \
        file-roller \
        engrampa \
        timeshift \
        baobab \
        zenity \
        yad \
        gvfs \
        gvfs-backends \
        udisks2 \
        udisks2-btrfs \
        pkexec \
        polkitd \
        lxpolkit \
        upower \
        dmidecode \
        x11-xserver-utils \
        arandr \
        autorandr \
        mesa-utils \
        inxi \
        brightnessctl \
        redshift
}

# ======================== 主流程 ========================

main() {
    echo "=====> [02_apps] 开始安装应用软件 <====="

    install_xfce_desktop
    install_fonts
    install_firefox
    install_wps_office
    install_wechat
    install_fcitx5
    install_app_store
    install_utilities

    echo "=====> [02_apps] 应用软件安装完成 <====="
}

main
