#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 02: 应用软件安装
# ============================================================================
# 设计意图：
#   安装桌面环境核心组件、中文输入法、预装应用（WPS/微信/Firefox）、
#   星火应用商店以及中文字体。
#   所有安装均在 chroot 中以非交互模式完成。
#
# 输入：
#   环境变量: MING_USER
#
# 输出：
#   安装完成的桌面环境与应用软件
#
# 关键步骤：
#   1. 安装 Xfce 4.18 桌面环境与 Compton 合成器
#   2. 安装 LightDM 显示管理器（自动登录）
#   3. 安装 Firefox ESR 浏览器
#   4. 安装 WPS Office（官方 deb）及字体依赖
#   5. 安装微信（腾讯官方 Linux 版 + Ming 低内存包装器）
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

    # Picom 启动包装器（自动探测 GLX 可用性，低内存/老显卡回退）
    cat > /usr/local/bin/ming-picom << 'PICOMWRAP'
#!/usr/bin/env bash
CONF="${HOME}/.config/picom/picom.conf"
LOWMEM="/etc/xdg/picom/picom-lowmem.conf"
FALLBACK="/etc/xdg/picom/picom-fallback.conf"
PICOM_BIN=$(command -v picom 2>/dev/null || echo "picom")
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)

# <=2600MB: 无 blur, 无重阴影, 仅 fade (最省 GPU/内存)
if [ "${MEM_MB}" -le 2600 ]; then
    exec ${PICOM_BIN} --config "${FALLBACK}" -b "$@"
fi

# 2601-4200MB: 低内存轻动画配置（无 blur, 轻阴影）
if [ "${MEM_MB}" -le 4200 ]; then
    if [ -f "${LOWMEM}" ]; then
        exec ${PICOM_BIN} --config "${LOWMEM}" -b "$@"
    fi
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

if [ "$HAS_GPU" -eq 1 ]; then
    exec ${PICOM_BIN} --config "${CONF}" -b "$@"
else
    exec ${PICOM_BIN} --config "${LOWMEM}" -b "$@"
fi
PICOMWRAP
    chmod +x /usr/local/bin/ming-picom

    echo "lightdm lightdm/default-display-manager select lightdm" | debconf-set-selections
    apt install -y --no-install-recommends \
        lightdm \
        lightdm-gtk-greeter \
        plymouth \
        plymouth-themes

    mkdir -p /etc/plymouth
    echo -e "[Daemon]\nTheme=ming-os\nShowDelay=0" > /etc/plymouth/plymouthd.conf
    mkdir -p /usr/share/plymouth/themes/ming-os
    cat > /usr/share/plymouth/themes/ming-os/ming-os.plymouth << PLYMOUTHCONF
[Plymouth Theme]
Name=Ming OS
Description=Ming OS Boot Splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/ming-os
ScriptFile=/usr/share/plymouth/themes/ming-os/ming-os.script
PLYMOUTHCONF

    cat > /usr/share/plymouth/themes/ming-os/ming-os.script << 'PLYMOUTHSCRIPT'
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
    message_sprite.SetText("欢迎使用 Ming OS");
end

Plymouth.SetQuitFunction(quit_callback);

fun message_callback(message)
    message_sprite.SetText(message);
end

Plymouth.SetMessageFunction(message_callback);
PLYMOUTHSCRIPT

    plymouth-set-default-theme ming-os 2>/dev/null || true

    systemctl enable lightdm 2>/dev/null || true

    install_vbox_guest_and_display

    mkdir -p /etc/live/config.conf.d
    cat > /etc/live/config.conf.d/ming-autologin.conf << LIVECONFIG
# Keep Ventoy/Live boots on the same default account as the installed system.
LIVE_USERNAME="${MING_USER}"
LIVE_USER_FULLNAME="Ming OS User"
LIVE_HOSTNAME="ming-os"
LIVE_USER_DEFAULT_GROUPS="audio cdrom dip floppy video plugdev netdev powerdev scanner bluetooth sudo adm lpadmin nopasswdlogin autologin"
LIVECONFIG

    mkdir -p /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/50-ming-autologin.conf << AUTOLOGIN
[Seat:*]
autologin-user=${MING_USER}
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
AUTOLOGIN

    cat > /etc/lightdm/lightdm-gtk-greeter.conf << GREETERCFG
[greeter]
theme-name = Ming-Glass
icon-theme-name = Papirus
font-name = WenQuanYi Micro Hei 11
background = /usr/share/backgrounds/ming-os/default.png
user-background = false
GREETERCFG

    cat > /usr/local/bin/ming-autologin-setup << 'AUTOLOGINSETUP'
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
if is_live_environment && id user >/dev/null 2>&1; then
    target_user="user"
fi
if [[ -z "${target_user}" ]]; then
    target_user="$(awk -F: '$3 >= 1000 && $3 < 60000 && $1 != "nobody" && $1 != "user" {print $1; exit}' /etc/passwd)"
fi
if [[ -z "${target_user}" ]] && id user >/dev/null 2>&1; then
    target_user="user"
fi

if [[ -z "${target_user}" ]]; then
    exit 0
fi

for grp in nopasswdlogin autologin; do
    getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    usermod -aG "${grp}" "${target_user}" 2>/dev/null || true
done

# installer boot 用专用 kiosk session，普通 boot 用 xfce
session="xfce"
grep -qwE "ming.installer=1|install" /proc/cmdline 2>/dev/null && session="ming-installer"

mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-ming-autologin.conf << AUTOLOGIN
[Seat:*]
autologin-user=${target_user}
autologin-user-timeout=0
autologin-session=${session}
user-session=${session}
greeter-session=lightdm-gtk-greeter
allow-guest=false
AUTOLOGIN

    chmod 0644 /etc/lightdm/lightdm.conf.d/50-ming-autologin.conf
AUTOLOGINSETUP
    chmod +x /usr/local/bin/ming-autologin-setup

    cat > /usr/local/bin/ming-getty-autologin << 'GETTYAUTO'
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

if is_live_environment && id user >/dev/null 2>&1; then
    agetty_args=(--autologin user "${agetty_args[@]}")
fi

exec /sbin/agetty "${agetty_args[@]}" "${tty_name}" "${term}"
GETTYAUTO
    chmod +x /usr/local/bin/ming-getty-autologin

    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/10-ming-live-autologin.conf << 'GETTYTTY1'
[Unit]
After=live-config.service ming-autologin-setup.service
Wants=ming-autologin-setup.service

[Service]
ExecStart=
ExecStart=-/usr/local/bin/ming-getty-autologin %I linux
GETTYTTY1

    mkdir -p /etc/systemd/system/serial-getty@ttyS0.service.d
    cat > /etc/systemd/system/serial-getty@ttyS0.service.d/10-ming-live-autologin.conf << 'GETTYSERIAL'
[Unit]
After=live-config.service ming-autologin-setup.service
Wants=ming-autologin-setup.service

[Service]
ExecStart=
ExecStart=-/usr/local/bin/ming-getty-autologin %I vt102
GETTYSERIAL

    cat > /etc/systemd/system/ming-autologin-setup.service << 'AUTOLOGINSVC'
[Unit]
Description=Ming OS automatic desktop login setup
After=local-fs.target live-config.service
Before=lightdm.service display-manager.service
ConditionPathExists=/etc/lightdm

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-autologin-setup

[Install]
WantedBy=multi-user.target graphical.target
AUTOLOGINSVC

    systemctl enable ming-autologin-setup.service 2>/dev/null || true
    /usr/local/bin/ming-autologin-setup 2>/dev/null || true

    # ---- 免密自动登录加固 (修复"配了自动登录仍弹密码框"恶性 Bug) ----
    # 根因：Debian 的 /etc/pam.d/lightdm-autologin 含
    #   auth required pam_succeed_if.so user ingroup autologin
    # 若目标账户不在 autologin 组，autologin 静默失败并回退到密码框。
    # 这里确保 PAM 文件存在、组存在、账户入组，三重保险。
    if [[ ! -f /etc/pam.d/lightdm-autologin ]]; then
        cat > /etc/pam.d/lightdm-autologin << 'PAMAUTOLOGIN'
#%PAM-1.0
auth    requisite       pam_nologin.so
auth    required        pam_env.so readenv=1
auth    required        pam_env.so readenv=1 envfile=/etc/default/locale
auth    optional        pam_permit.so
auth    required        pam_permit.so
@include common-account
session required        pam_limits.so
@include common-session
@include common-password
PAMAUTOLOGIN
    fi

    # 确保 autologin/nopasswdlogin 组存在且默认账户入组（live 与安装后都生效）
    for grp in autologin nopasswdlogin; do
        getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
        usermod -aG "${grp}" "${MING_USER}" 2>/dev/null || true
    done

    # 关闭 GNOME keyring 在自动登录后弹出的"解锁密钥环"密码框：
    # 自动登录无登录密码可用于解锁，必须禁用 keyring 的 login/autologin 钩子。
    for pamfile in /etc/pam.d/lightdm-autologin /etc/pam.d/lightdm; do
        [[ -f "${pamfile}" ]] || continue
        sed -i '/pam_gnome_keyring\.so/d' "${pamfile}" 2>/dev/null || true
    done

    # 蓝牙：安装 bluez/blueman 后启用守护进程（延迟启动已在 01_base 配好）
    systemctl enable bluetooth 2>/dev/null || true
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

    sudo -u "${MING_USER}" xdg-settings set default-web-browser firefox-esr.desktop 2>/dev/null || true

    configure_firefox_policies
    deploy_firefox_homepage
}

# ---- Firefox 适老化：policies.json（预装 uBlock Origin、屏蔽复杂菜单、锁定主页） ----
configure_firefox_policies() {
    # Debian firefox-esr 读取 /etc/firefox-esr/policies/policies.json 与
    # /usr/lib/firefox-esr/distribution/policies.json，两处都写以确保生效。
    local pol_dirs=(
        "/etc/firefox-esr/policies"
        "/usr/lib/firefox-esr/distribution"
    )
    for d in "${pol_dirs[@]}"; do
        mkdir -p "${d}"
        cat > "${d}/policies.json" << 'FXPOLICY'
{
  "policies": {
    "ExtensionSettings": {
      "uBlock0@raymondhill.net": {
        "installation_mode": "force_installed",
        "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi"
      }
    },
    "Homepage": {
      "URL": "file:///usr/share/ming-os/homepage/index.html",
      "Locked": false,
      "StartPage": "homepage"
    },
    "OverrideFirstRunPage": "file:///usr/share/ming-os/homepage/index.html",
    "OverridePostUpdatePage": "",
    "DisableProfileImport": true,
    "DisablePocket": true,
    "DisableFirefoxAccounts": true,
    "DisableTelemetry": true,
    "DisableFirefoxStudies": true,
    "DisableFeedbackCommands": true,
    "DisableSetDesktopBackground": false,
    "NoDefaultBookmarks": false,
    "DontCheckDefaultBrowser": true,
    "DisplayBookmarksToolbar": "always",
    "PromptForDownloadLocation": false,
    "Preferences": {
      "browser.toolbars.bookmarks.visibility": "always",
      "browser.uidensity": 0,
      "layout.css.devPixelsPerPx": "1.25",
      "font.minimum-size.zh-CN": 18,
      "font.minimum-size.x-western": 16
    },
    "UserMessaging": {
      "ExtensionRecommendations": false,
      "FeatureRecommendations": false,
      "SkipOnboarding": true,
      "MoreFromMozilla": false
    },
    "FirefoxHome": {
      "Search": true,
      "TopSites": true,
      "SponsoredTopSites": false,
      "Highlights": false,
      "Pocket": false,
      "SponsoredPocket": false,
      "Snippets": false
    }
  }
}
FXPOLICY
    done
    echo "[02_apps] Firefox policies.json 已部署（uBlock Origin + 适老化）。"
}

# ---- 极简本地导航主页（大字体、常用站点） ----
deploy_firefox_homepage() {
    local hp="/usr/share/ming-os/homepage"
    mkdir -p "${hp}"
    cat > "${hp}/index.html" << 'HOMEPAGE'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ming OS 导航</title>
<style>
  :root { --green:#1FA89E; --dark:#0E5C54; --bg:#0c1f1c; }
  * { box-sizing: border-box; }
  body {
    margin:0; min-height:100vh;
    font-family:"Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;
    background:linear-gradient(135deg,#0c1f1c 0%,#11332e 100%);
    color:#eafff8; display:flex; flex-direction:column; align-items:center;
    padding:6vh 4vw;
  }
  h1 { font-size:2.6rem; font-weight:700; margin:0 0 0.2em; letter-spacing:2px; }
  .sub { font-size:1.1rem; color:#9FE7D7; margin-bottom:2em; }
  .search { width:min(680px,90vw); display:flex; margin-bottom:2.5em; }
  .search input {
    flex:1; font-size:1.4rem; padding:0.7em 1em; border:none;
    border-radius:14px 0 0 14px; outline:none;
  }
  .search button {
    font-size:1.3rem; padding:0 1.4em; border:none; cursor:pointer;
    background:var(--green); color:#fff; border-radius:0 14px 14px 0;
  }
  .grid {
    display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
    gap:22px; width:min(900px,92vw);
  }
  .tile {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    height:120px; border-radius:20px; text-decoration:none; color:#eafff8;
    background:rgba(31,168,158,0.16); border:1px solid rgba(159,231,215,0.2);
    font-size:1.35rem; font-weight:600; transition:transform .12s, background .12s;
  }
  .tile:hover { transform:translateY(-4px); background:rgba(31,168,158,0.32); }
  .tile .ico { font-size:2.4rem; margin-bottom:0.3em; }
</style>
</head>
<body>
  <h1>Ming OS 导航</h1>
  <div class="sub">青葱常用入口 · 大字清晰</div>
  <form class="search" action="https://www.baidu.com/s" method="get">
    <input name="wd" placeholder="搜索…" autofocus>
    <button type="submit">搜索</button>
  </form>
  <div class="grid">
    <a class="tile" href="https://www.baidu.com"><span class="ico">🔍</span>百度</a>
    <a class="tile" href="https://www.taobao.com"><span class="ico">🛒</span>淘宝</a>
    <a class="tile" href="https://www.jd.com"><span class="ico">📦</span>京东</a>
    <a class="tile" href="https://www.bilibili.com"><span class="ico">📺</span>哔哩哔哩</a>
    <a class="tile" href="https://news.baidu.com"><span class="ico">📰</span>新闻</a>
    <a class="tile" href="https://map.baidu.com"><span class="ico">🗺️</span>地图</a>
    <a class="tile" href="https://mail.qq.com"><span class="ico">✉️</span>邮箱</a>
    <a class="tile" href="https://weather.cma.cn"><span class="ico">☀️</span>天气</a>
    <a class="tile" href="https://www.12306.cn"><span class="ico">🚄</span>火车票</a>
    <a class="tile" href="https://www.gov.cn"><span class="ico">🏛️</span>政务服务</a>
    <a class="tile" href="https://www.iqiyi.com"><span class="ico">🎬</span>爱奇艺</a>
    <a class="tile" href="file:///usr/share/ming-os/homepage/help.html"><span class="ico">❓</span>使用帮助</a>
  </div>
</body>
</html>
HOMEPAGE

    cat > "${hp}/help.html" << 'HELPPAGE'
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>Ming OS 使用帮助</title>
<style>body{font-family:"Noto Sans CJK SC",sans-serif;background:#0c1f1c;color:#eafff8;
padding:8vh 6vw;font-size:1.3rem;line-height:1.9}h1{color:#9FE7D7}a{color:#5fe0c8}</style></head>
<body><h1>Ming OS 使用帮助</h1>
<p>· 桌面底部是<strong>程序坞</strong>，点击图标即可打开应用。</p>
<p>· 打开「<strong>铭设置</strong>」可调整字体大小、连接 Wi-Fi、检查更新。</p>
<p>· 上网遇到广告会被自动拦截（已内置 uBlock Origin）。</p>
<p>· 需要更多软件，点开「<strong>星火应用商店</strong>」。</p>
<p><a href="index.html">返回导航首页</a></p>
</body></html>
HELPPAGE
    echo "[02_apps] Firefox 极简本地导航主页已部署。"
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

    apt install -y --no-install-recommends \
        libglu1-mesa \
        libxslt1.1 \
        libxml2

    cat > /usr/local/bin/ming-install-wps << 'WPSINSTALL'
#!/usr/bin/env bash
set -euo pipefail

wps_url="https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/26885/wps-office_12.1.2.26885.AK.preread.sw.Personal_715971_amd64.deb"
wps_deb="/tmp/wps-office.deb"
wps_path="${wps_url#https://wps-linux-personal.wpscdn.cn}"
timestamp="$(date +%s)"
token="$(printf '7f8faaaa468174dc1c9cd62e5f218a5b%s%s' "${wps_path}" "${timestamp}" | md5sum | awk '{print $1}')"
wps_url="${wps_url}?t=${timestamp}&k=${token}"

echo "Downloading WPS Office..."
wget -c --show-progress -O "${wps_deb}" "${wps_url}"
apt install -y --no-install-recommends libglu1-mesa libxslt1.1 libxml2
apt install -y "${wps_deb}" || apt install -y -f
rm -f "${wps_deb}"

if [[ -d /usr/share/fonts/wps-office ]]; then
    ln -sf /usr/share/fonts/truetype/wqy /usr/share/fonts/wps-office/wqy 2>/dev/null || true
fi

echo "WPS Office installed."
WPSINSTALL
    chmod +x /usr/local/bin/ming-install-wps

    cat > /usr/share/applications/ming-install-wps.desktop << 'WPSINSTALLDESKTOP'
[Desktop Entry]
Name=WPS Office
Name[zh_CN]=WPS Office
Comment=Download and install WPS Office on demand
Comment[zh_CN]=按需下载安装 WPS Office
Exec=pkexec /usr/local/bin/ming-install-wps
Icon=wps-office
Terminal=true
Type=Application
Categories=Office;
StartupNotify=true
WPSINSTALLDESKTOP

    if [[ "${MING_PREINSTALL_WPS:-0}" != "1" ]]; then
        echo "[02_apps] Skip WPS preinstall in image build; keep on-demand installer for faster and more reliable builds."
        return 0
    fi

    echo "下载 WPS Office..."
    if wget -q --show-progress -O "${wps_deb}" "${wps_url}" 2>/dev/null; then
        if ! timeout 900 apt install -y "${wps_deb}"; then
            echo "[WARN] WPS Office build-time install timed out or failed; keeping on-demand installer instead."
            apt install -y -f || true
        fi
        rm -f "${wps_deb}"
    else
        echo "[WARN] WPS Office 下载失败，跳过。用户可后续从应用商店安装。"
        rm -f "${wps_deb}"
        return 0
    fi

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
        echo "[WARN] 微信官方 deb 下载失败，跳过。用户可后续运行 ming-install-wechat 安装。"
        rm -f "${wechat_deb}"
    fi

    cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'
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
    chmod +x /usr/local/bin/ming-install-wechat

    cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'
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
mode="${MING_WECHAT_MODE:-auto}"
wechat_bin="$(find_wechat_bin || true)"

if [[ -z "${wechat_bin}" ]]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --question \
            --title="微信未安装" \
            --text="未找到微信。是否现在下载安装官方 Linux 版？" \
            --ok-label="安装" --cancel-label="取消" 2>/dev/null || exit 1
    fi
    pkexec /usr/local/bin/ming-install-wechat || sudo /usr/local/bin/ming-install-wechat || exit 1
    wechat_bin="$(find_wechat_bin || true)"
fi

mkdir -p "${HOME}/.cache/ming-os"

if [[ "${mode}" == "auto" && "${mem_mb}" -le 2600 ]]; then
    mode="light"
fi

if [[ "${mode}" == "light" ]]; then
    note="${HOME}/.config/ming-os/wechat-low-memory-note"
    if [[ ! -f "${note}" ]] && command -v zenity >/dev/null 2>&1; then
        mkdir -p "$(dirname "${note}")"
        if ! zenity --question \
            --title="微信省内存模式" \
            --text="检测到本机内存约 ${mem_mb}MB。\n\n微信好友和群组较多时会明显占用内存。Ming OS 会用低缓存、低优先级方式启动微信；如果仍然卡顿，可以改用网页版。" \
            --ok-label="省内存启动" \
            --cancel-label="改用网页版" \
            --width=460 2>/dev/null; then
            echo "shown" > "${note}"
            exec /usr/local/bin/ming-wechat-web
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
    chmod +x /usr/local/bin/ming-wechat

    cat > /usr/local/bin/ming-wechat-web << 'WECHATWEB'
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
    chmod +x /usr/local/bin/ming-wechat-web

    mkdir -p /home/${MING_USER}/Desktop
    cat > /home/${MING_USER}/Desktop/wechat.desktop << WECHATDESKTOP
[Desktop Entry]
Name=微信
Name[zh_CN]=微信
Comment=Official WeChat for Linux with Ming low-memory guard
Exec=/usr/local/bin/ming-wechat
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATDESKTOP
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/wechat.desktop"
    chmod +x "/home/${MING_USER}/Desktop/wechat.desktop"

    cat > /usr/share/applications/ming-wechat.desktop << WECHATDESKTOPSYS
[Desktop Entry]
Name=微信
Name[zh_CN]=微信
Comment=Official WeChat for Linux with Ming low-memory guard
Exec=/usr/local/bin/ming-wechat
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATDESKTOPSYS

    cat > /usr/share/applications/ming-wechat-web.desktop << WECHATWEBDESKTOP
[Desktop Entry]
Name=微信网页版
Name[zh_CN]=微信网页版
Comment=Use web WeChat when memory is too limited for the desktop client
Exec=/usr/local/bin/ming-wechat-web
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;
StartupNotify=true
WECHATWEBDESKTOP

    # 省内存图形管理工具（26.2.5）
    cat > /usr/local/bin/ming-wechat-manager << 'WECHATMGR'
#!/usr/bin/env bash
# Ming OS 微信省内存管理器
set -uo pipefail

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
wechat_mem_kb=$(ps aux 2>/dev/null | awk '/[Ww]e[Cc]hat|[Ww]eixin/ && !/awk/{sum+=$6} END{print int(sum)}')
wechat_mem_mb=$((wechat_mem_kb / 1024))
cache_size=$(du -sm "${HOME}/.cache/wechat" "${HOME}/.cache/weixin" "${HOME}/.cache/tencent/wechat" 2>/dev/null | awk '{sum+=$1} END{print sum+0}')

msg="系统总内存：${mem_mb} MB\n"
if [[ "${wechat_mem_mb}" -gt 0 ]]; then
    msg+="微信当前占用：${wechat_mem_mb} MB\n"
else
    msg+="微信当前未运行\n"
fi
msg+="微信缓存大小：约 ${cache_size} MB\n\n请选择操作："

choice=$(zenity --list \
    --title="微信省内存管理" \
    --text="${msg}" \
    --column="操作" \
    "清理微信缓存" \
    "省内存模式启动微信" \
    "切换到网页版微信" \
    "关闭微信进程" \
    --width=420 --height=340 2>/dev/null) || exit 0

case "${choice}" in
    "清理微信缓存")
        find "${HOME}/.cache" -maxdepth 4 \( -iname '*wechat*' -o -iname '*weixin*' -o -iname '*tencent*' \) \
            -type f -size +1M -delete 2>/dev/null || true
        zenity --info --title="微信缓存清理" --text="缓存已清理完成。" --width=300 2>/dev/null || true
        ;;
    "省内存模式启动微信")
        MING_WECHAT_MODE=light /usr/local/bin/ming-wechat &
        ;;
    "切换到网页版微信")
        /usr/local/bin/ming-wechat-web &
        ;;
    "关闭微信进程")
        pkill -f '[Ww]e[Cc]hat|[Ww]eixin' 2>/dev/null || true
        zenity --info --title="已关闭微信" --text="微信进程已终止。" --width=300 2>/dev/null || true
        ;;
esac
WECHATMGR
    chmod +x /usr/local/bin/ming-wechat-manager

    cat > /usr/share/applications/ming-wechat-manager.desktop << 'WECHATMGRDESKTOP'
[Desktop Entry]
Name=微信内存管理
Name[zh_CN]=微信内存管理
Comment=查看微信内存占用、清理缓存、省内存启动或切换网页版
Exec=/usr/local/bin/ming-wechat-manager
Icon=wechat
Terminal=false
Type=Application
Categories=Network;InstantMessaging;System;
WECHATMGRDESKTOP
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

    sudo -u "${MING_USER}" bash -c 'cat > ~/.xinputrc << XINPUTRC
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export GLFW_IM_MODULE=ibus
fcitx5 -d --replace
XINPUTRC'

    sudo -u "${MING_USER}" mkdir -p /home/${MING_USER}/.config/fcitx5/profile
    sudo -u "${MING_USER}" bash -c 'cat > /home/'${MING_USER}'/.config/fcitx5/profile/default << FCITX5PROFILE
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

    cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'
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
    chmod +x /usr/local/bin/ming-install-spark-store

    if ! /usr/local/bin/ming-install-spark-store; then
        echo "[WARN] 星火应用商店安装失败，保留 ming-install-spark-store 供用户联网后重试。"
    fi

    # 锁定关键包版本，避免 OTA / apt 操作误删或降级星火商店及其运行依赖，
    # 造成"商店打不开"。OTA 本身是镜像级（暂存启动项），但用户经星火装应用会跑
    # apt；这里给星火及其核心依赖加 apt pin，保持其优先级与不被自动移除。
    mkdir -p /etc/apt/preferences.d
    cat > /etc/apt/preferences.d/90-ming-spark-store << 'SPARKPIN'
# Ming OS：保护星火应用商店及其核心运行依赖，防止被 apt/OTA 误降级或移除
Package: spark-store
Pin: version *
Pin-Priority: 1001

Package: libqt5core5a libqt5widgets5 libqt5network5 libqt5gui5
Pin: version *
Pin-Priority: 990
SPARKPIN

    # 标记星火商店为手动安装，避免 apt autoremove 把它当孤儿清掉
    apt-mark manual spark-store 2>/dev/null || true
    echo "[02_apps] 已为星火应用商店添加 apt 版本锁定与防误删保护。"

    # 推荐应用改为打开星火商店，避免在 2GB 设备上首次登录就后台批量安装。
    cat > /usr/local/bin/ming-app-recommend << 'RECOMMEND'
#!/usr/bin/env bash
# Ming OS 推荐应用入口 - 低内存机器不做后台批量安装

MARKER="${HOME}/.config/ming-os/app-recommend-done"
if [[ -f "${MARKER}" ]]; then
    exit 0
fi

mkdir -p "$(dirname "${MARKER}")"

if command -v notify-send >/dev/null 2>&1; then
    notify-send -i ming-app-store "Ming OS 应用商店" "常用软件请从星火应用商店按需安装，2GB 设备不会后台批量装应用。" 2>/dev/null || true
fi

echo "done" > "${MARKER}"
RECOMMEND

    chmod +x /usr/local/bin/ming-app-recommend

    # 创建应用商店桌面快捷方式
    mkdir -p /home/${MING_USER}/Desktop
    cat > /home/${MING_USER}/Desktop/spark-store.desktop << SPARKDESKTOP
[Desktop Entry]
Name=星火应用商店
Name[zh_CN]=星火应用商店
Comment=Install Chinese Linux applications on demand
Comment[zh_CN]=按需安装适合中文用户的 Linux 应用
Exec=spark-store
Icon=ming-app-store
Terminal=false
Type=Application
Categories=System;PackageManager;
Keywords=software;store;app;install;spark;应用;商店;软件;星火;安装;
StartupNotify=true
SPARKDESKTOP
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/spark-store.desktop"
    chmod +x "/home/${MING_USER}/Desktop/spark-store.desktop"

    # 同样更新 system 级 desktop（菜单用）
    cat > /usr/share/applications/spark-store.desktop << SPARKSYSDESKTOP
[Desktop Entry]
Name=星火应用商店
Name[zh_CN]=星火应用商店
Comment=Install Chinese Linux applications on demand
Comment[zh_CN]=按需安装适合中文用户的 Linux 应用
Exec=spark-store
Icon=ming-app-store
Terminal=false
Type=Application
Categories=System;PackageManager;
Keywords=software;store;app;install;spark;应用;商店;软件;星火;安装;
StartupNotify=true
SPARKSYSDESKTOP

    cat > /usr/share/applications/ming-install-spark-store.desktop << SPARKINSTALLDESKTOP
[Desktop Entry]
Name=修复星火应用商店
Name[zh_CN]=修复星火应用商店
Comment=Download and install Spark Store if it was not bundled during image build
Exec=pkexec /usr/local/bin/ming-install-spark-store
Icon=ming-app-store
Terminal=true
Type=Application
Categories=System;PackageManager;
StartupNotify=true
SPARKINSTALLDESKTOP

    # 推荐应用首次启动项
    mkdir -p "/home/${MING_USER}/.config/autostart"
    cat > "/home/${MING_USER}/.config/autostart/ming-app-recommend.desktop" << APPRECAUTOSTART
[Desktop Entry]
Type=Application
Name=Ming App Recommendations
Comment=Recommended apps for Ming OS
Exec=/usr/local/bin/ming-app-recommend
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=15
APPRECAUTOSTART
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/autostart/ming-app-recommend.desktop"

    cat > /etc/systemd/system/ming-appstore-ready.service << 'SVCUNIT'
[Unit]
Description=Ming OS App Store Readiness
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'command -v spark-store >/dev/null 2>&1 || /usr/local/bin/ming-install-spark-store || true'
TimeoutStartSec=90
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SVCUNIT

    systemctl enable ming-appstore-ready.service 2>/dev/null || true
}

# ======================== 附加实用工具 ========================

install_utilities() {
    apt install -y --no-install-recommends \
        pavucontrol \
        pulseaudio \
        pulseaudio-module-bluetooth \
        bluez \
        bluez-tools \
        blueman \
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
        onboard \
        touchegg \
        xdotool \
        python3-gi \
        gir1.2-gtk-4.0 \
        gir1.2-adw-1 \
        libadwaita-1-0 \
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

# ======================== 护眼模式（26.2.5） ========================

deploy_eyecare() {
    cat > /usr/local/bin/ming-eyecare << 'EOF'
#!/usr/bin/env bash
# Ming OS 护眼模式 - 切换屏幕色温（暖色/正常）
STATE="${HOME}/.config/ming-os/eyecare-enabled"
if [[ -f "${STATE}" ]]; then
    pkill -f redshift 2>/dev/null || true
    rm -f "${STATE}"
    # 重置为正常色温
    redshift -O 6500 -b 1.0 2>/dev/null && sleep 0.3 && pkill -f redshift 2>/dev/null || true
    notify-send -i display-brightness-symbolic "护眼模式" "已关闭，屏幕恢复正常色温" 2>/dev/null || true
else
    mkdir -p "$(dirname "${STATE}")"
    touch "${STATE}"
    # 4000K 暖色温，亮度 0.9
    redshift -O 4000 -b 0.9 2>/dev/null &
    notify-send -i display-brightness-symbolic "护眼模式" "已开启，屏幕切换为暖色调（4000K）" 2>/dev/null || true
fi
EOF
    chmod +x /usr/local/bin/ming-eyecare

    cat > /usr/share/applications/ming-eyecare.desktop << 'EOF'
[Desktop Entry]
Name=护眼模式
Name[zh_CN]=护眼模式
Comment=切换屏幕暖色调，减少蓝光
Exec=ming-eyecare
Icon=display-brightness-symbolic
Terminal=false
Type=Application
Categories=System;Settings;
EOF
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
    deploy_eyecare

    echo "=====> [02_apps] 应用软件安装完成 <====="
}

main
