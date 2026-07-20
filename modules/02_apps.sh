#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 02: 应用软件安装
# ============================================================================
# 设计意图：
#   安装桌面环境核心组件、中文输入法、Firefox、星火应用商店以及中文字体。
#   WPS 与微信仅保留按需安装入口，不随 26.3.2 镜像预装。
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
#   4. 写入 WPS Office 按需安装入口
#   5. 写入微信按需安装入口与 Ming 低内存包装器
#   6. 安装 Fcitx5 中文输入法
#   7. 安装星火应用商店（按需安装应用，避免低内存设备后台批量装软件）
#   8. 安装中文字体
# ============================================================================

set -uo pipefail

readonly REQUIRED_DESKTOP_RUNTIME_PACKAGES=(
    python3-gi
    gir1.2-gtk-4.0
    gir1.2-adw-1
    libadwaita-1-0
    # Papyrus uses the Debian WebKitGTK 4.1 ABI.  Keep this in the required
    # runtime set so a visible launcher cannot ship without its ELF runtime.
    libwebkit2gtk-4.1-0
    gvfs
    gvfs-backends
    brightnessctl
    xdotool
    xinput
    wmctrl
    rfkill
    pulseaudio
    pulseaudio-utils
    alsa-utils
    libasound2-plugins
    pulseaudio-module-bluetooth
    pavucontrol
    bluez
    upower
    pkexec
    polkitd
    lxpolkit
    libnotify-bin
    x11-utils
    desktop-file-utils
    im-config
    blueman
    bamfdaemon
    # Debian 13/Trixie renamed the BAMF library for the 64-bit time_t ABI.
    # Keep the concrete package in the required list so dpkg-query cannot
    # silently accept a missing Dock window-mapping backend.
    libbamf3-2t64
    ffmpeg
)

run_required_step() {
    local step="$1"
    shift
    if ! "${step}" "$@"; then
        echo "[ERROR] [02_apps] required step failed: ${step}" >&2
        return 1
    fi
}

run_optional_step() {
    local step="$1"
    shift
    if ! "${step}" "$@"; then
        echo "[WARN] [02_apps] optional step failed: ${step}" >&2
    fi
    return 0
}

# ======================== 桌面环境 ========================

install_xfce_desktop() {
    apt install -y --no-install-recommends \
        xserver-xorg \
        xserver-xorg-video-amdgpu \
        xserver-xorg-video-ati \
        xserver-xorg-video-nouveau \
        xserver-xorg-input-libinput \
        xfwm4 \
        xfconf \
        xfce4-session \
        xfce4-settings \
        xfdesktop4 \
        xfce4-terminal \
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
        desktop-base || return 1

    apt install -y --no-install-recommends \
        picom \
        plank \
        librsvg2-bin \
        librsvg2-common \
        imagemagick || return 1

    # Picom configuration and wrapper are generated only by 03_desktop.sh.

    echo "lightdm lightdm/default-display-manager select lightdm" | debconf-set-selections
    apt install -y --no-install-recommends \
        lightdm \
        lightdm-gtk-greeter \
        xfce4-screensaver \
        plymouth \
        plymouth-themes || return 1

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
LIVE_USER_DEFAULT_GROUPS="audio cdrom dip floppy video render plugdev netdev powerdev scanner bluetooth adm lpadmin nopasswdlogin autologin"
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
# session-wrapper 确保 dbus-launch 在 autologin 时正确启动 session bus，
# 修复 GDBus.Error:ServiceUnknown: org.xfce.Panel（session bus 未就绪）
session-wrapper=/etc/X11/Xsession
AUTOLOGIN

    cat > /etc/lightdm/lightdm-gtk-greeter.conf << GREETERCFG
[greeter]
theme-name = Ming-Glass
icon-theme-name = Papirus
font-name = Noto Sans CJK SC 11
background = /usr/share/backgrounds/ming-os/default.png
user-background = false
clock-format = %H:%M
indicators = ~host;~spacer;~clock;~spacer;~power
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
gpasswd -d "${target_user}" sudo >/dev/null 2>&1 || true

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
        fontconfig \
        fonts-noto-core \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-noto-cjk \
        fonts-noto-mono \
        fonts-noto-cjk-extra || return 1

    apt install -y --no-install-recommends fonts-liberation fonts-croscore || true

    # Keep one deterministic CJK family for GTK/Qt/Fcitx and leave WenQuanYi
    # only as a last-resort fallback for legacy applications.  This also
    # enables antialiasing and light hinting on old LCD panels.
    mkdir -p /etc/fonts/conf.d
    cat > /etc/fonts/conf.d/99-ming-os-fonts.conf << 'MINGFONTS'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <match target="pattern">
    <test name="family" compare="eq" qual="any"><string>sans</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK SC</string>
      <string>Noto Sans</string>
      <string>WenQuanYi Micro Hei</string>
    </edit>
  </match>
  <match target="pattern">
    <test name="family" compare="eq" qual="any"><string>sans-serif</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK SC</string>
      <string>Noto Sans</string>
      <string>WenQuanYi Micro Hei</string>
    </edit>
  </match>
  <match target="pattern">
    <test name="family" compare="eq" qual="any"><string>system-ui</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans CJK SC</string>
      <string>Noto Sans</string>
      <string>WenQuanYi Micro Hei</string>
    </edit>
  </match>
  <match target="pattern">
    <test name="family" compare="eq" qual="any"><string>monospace</string></test>
    <edit name="family" mode="prepend" binding="strong">
      <string>Noto Sans Mono</string>
      <string>DejaVu Sans Mono</string>
    </edit>
  </match>
  <match target="font">
    <edit name="antialias" mode="assign"><bool>true</bool></edit>
    <edit name="hinting" mode="assign"><bool>true</bool></edit>
    <edit name="hintstyle" mode="assign"><const>hintslight</const></edit>
    <edit name="rgba" mode="assign"><const>rgb</const></edit>
  </match>
</fontconfig>
MINGFONTS

    fc-cache -f -v || return 1
}

# ======================== Microsoft Edge ========================

generate_edge_video_samples() {
    local sample_dir="${MING_EDGE_SAMPLE_DIR:-/usr/share/ming-os/media-tests}"
    mkdir -p "${sample_dir}" || return 1
    ffmpeg -hide_banner -loglevel error -y -f lavfi \
        -i 'testsrc2=size=64x64:rate=12' -t 0.5 -pix_fmt yuv420p \
        -c:v libx264 -movflags +faststart "${sample_dir}/h264.mp4" || return 1
    ffmpeg -hide_banner -loglevel error -y -f lavfi \
        -i 'testsrc2=size=64x64:rate=12' -t 0.5 -pix_fmt yuv420p \
        -c:v libvpx-vp9 -deadline good -cpu-used 8 "${sample_dir}/vp9.webm" || return 1
    test -s "${sample_dir}/h264.mp4" && test -s "${sample_dir}/vp9.webm" || return 1
    ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
        -of default=nw=1:nk=1 "${sample_dir}/h264.mp4" | grep -qx h264 || return 1
    ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
        -of default=nw=1:nk=1 "${sample_dir}/vp9.webm" | grep -qx vp9 || return 1
}

install_edge_graphics_helper() {
    cat > /usr/local/bin/ming-edge-graphics << 'MINGEDGEGRAPHICS'
#!/usr/bin/env bash
set -uo pipefail

state_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/ming-os"
mode_file="${state_dir}/edge-graphics-mode"
samples=/usr/share/ming-os/media-tests

active_render_node() {
    local node card boot candidate
    for boot in /sys/class/drm/card*/device/boot_vga; do
        [[ "$(cat "${boot}" 2>/dev/null || true)" == 1 ]] || continue
        card="${boot%/device/boot_vga}"
        for candidate in "${card}"/device/drm/renderD*; do
            [[ -e "${candidate}" ]] || continue
            node="/dev/dri/${candidate##*/}"
            [[ -r "${node}" && -w "${node}" ]] && { printf '%s\n' "${node}"; return; }
        done
    done
    for node in /dev/dri/renderD*; do
        [[ -r "${node}" && -w "${node}" ]] && { printf '%s\n' "${node}"; return; }
    done
    return 1
}

decode_samples() {
    local node="$1" sample successful_codecs=0
    command -v ffmpeg >/dev/null 2>&1 || return 1
    for sample in "${samples}/h264.mp4" "${samples}/vp9.webm"; do
        [[ -s "${sample}" ]] || continue
        if timeout 12 ffmpeg -v error -hwaccel vaapi -hwaccel_device "${node}" \
            -hwaccel_output_format vaapi -i "${sample}" -frames:v 12 -f null - \
            >/dev/null 2>&1; then
            successful_codecs=$((successful_codecs + 1))
        fi
    done
    (( successful_codecs >= 1 ))
}

mode="$(cat "${mode_file}" 2>/dev/null || printf auto)"
node="$(active_render_node || true)"
video=false
[[ "${mode}" != compat && -n "${node}" ]] && decode_samples "${node}" && video=true

case "${1:-status}" in
    status)
        printf '{"mode":"%s","render_node":"%s","video_decode":%s}\n' \
            "${mode}" "${node}" "${video}"
        ;;
    test-video) ${video} ;;
    set-mode)
        [[ "${2:-}" == auto || "${2:-}" == compat ]] || exit 2
        mkdir -p "${state_dir}" && printf '%s\n' "$2" >"${mode_file}"
        ;;
    *) printf 'Usage: %s status|test-video|set-mode auto|compat\n' "$0" >&2; exit 2 ;;
esac
MINGEDGEGRAPHICS
    chmod 0755 /usr/local/bin/ming-edge-graphics
}

install_edge() {
    install_edge_graphics_helper
    apt install -y --no-install-recommends \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        xdg-utils

    install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg.tmp
    mv -f /usr/share/keyrings/microsoft-edge.gpg.tmp /usr/share/keyrings/microsoft-edge.gpg
    chmod 0644 /usr/share/keyrings/microsoft-edge.gpg

    cat > /etc/apt/sources.list.d/microsoft-edge.list << 'EDGEREPO'
deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] https://packages.microsoft.com/repos/edge stable main
EDGEREPO
    apt update
    apt install -y --no-install-recommends microsoft-edge-stable

    cat > /usr/local/bin/ming-edge << 'MINGEDGE'
#!/usr/bin/env bash
set -e
homepage=/usr/share/ming-os/homepage/index.html
edge_args=(--ozone-platform=x11)
edge_graphics_mode=software

edge_gpu_mode() {
    local probe_cache probe_tmp probe_json
    if [[ -z "$(ming-edge-graphics status 2>/dev/null | sed -n 's/.*\"render_node\":\"\([^\"]*\)\".*/\1/p')" ]] \
        || (command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt --quiet) \
        || grep -Eq '(^|[[:space:]])nomodeset([[:space:]]|$)|(i915|amdgpu|radeon|nouveau)\.modeset=0' /proc/cmdline 2>/dev/null; then
        printf '%s\n' software
        return
    fi
    command -v ming-hardware-status >/dev/null 2>&1 || { printf '%s\n' software; return; }
    probe_cache="${XDG_CACHE_HOME:-${HOME}/.cache}/ming-os/edge-hardware.json"
    mkdir -p "$(dirname "${probe_cache}")"
    if [[ ! -s "${probe_cache}" ]] || ! find "${probe_cache}" -mmin -5 -print -quit 2>/dev/null | grep -q .; then
        probe_tmp="${probe_cache}.tmp.$$"
        if timeout 4 ming-hardware-status status --json > "${probe_tmp}" 2>/dev/null; then
            mv -f "${probe_tmp}" "${probe_cache}"
        else
            rm -f "${probe_tmp}"
        fi
    fi
    probe_json="$(cat "${probe_cache}" 2>/dev/null || true)"
    if ! grep -Fq '"desktop_rendering": true' <<< "${probe_json}" \
        || ! grep -Fq '"render_access": true' <<< "${probe_json}"; then
        printf '%s\n' software
    elif ming-edge-graphics test-video >/dev/null 2>&1; then
        printf '%s\n' video
    else
        # The compositor can still use the native GPU when VA-API has no
        # matching codec.  Do not turn a healthy Radeon/Zhaoxin desktop into
        # llvmpipe merely because browser video decode is unavailable.
        printf '%s\n' desktop
    fi
}

edge_graphics_mode="$(edge_gpu_mode)"
if [[ "${edge_graphics_mode}" == software ]]; then
    # VirtualBox, no render node, safe-graphics mode, or failed VA-API all
    # use a deterministic software path to avoid black borders and hangs.
    edge_args+=(--disable-gpu --disable-gpu-compositing)
elif [[ "${edge_graphics_mode}" == video ]]; then
    # Only enable Chromium's VA-API path after ming-hardware-status has
    # confirmed a real KMS/Mesa path, render access and at least one supported
    # browser codec on a non-virtual host.
    edge_args+=(
        --enable-accelerated-video-decode
        --enable-features=VaapiVideoDecodeLinuxGL
    )
else
    edge_args+=(--disable-accelerated-video-decode)
fi
# Keep browser playback independent from a stale PulseAudio user session.  This
# is intentionally bounded and only repairs a missing/broken default output;
# ming-audio-session preserves a valid user-selected HDMI, Bluetooth or USB
# sink rather than overriding it with an internal device.
if command -v ming-audio-session >/dev/null 2>&1; then
    audio_log="${XDG_CACHE_HOME:-${HOME}/.cache}/ming-os/audio-session.log"
    mkdir -p "$(dirname "${audio_log}")" 2>/dev/null || true
    (timeout 3 ming-audio-session ensure --json >>"${audio_log}" 2>&1 &) || true
fi
if [[ "$#" -eq 0 ]] && [[ -r "${homepage}" ]]; then
    set -- "file://${homepage}"
fi
if command -v microsoft-edge-stable >/dev/null 2>&1; then
    exec microsoft-edge-stable "${edge_args[@]}" "$@"
elif command -v microsoft-edge >/dev/null 2>&1; then
    exec microsoft-edge "${edge_args[@]}" "$@"
elif command -v xdg-open >/dev/null 2>&1 && [[ "$#" -gt 0 ]]; then
    exec xdg-open "$1"
else
    echo "Microsoft Edge is not installed." >&2
    exit 127
fi
MINGEDGE
    chmod 0755 /usr/local/bin/ming-edge

    cat > /usr/share/applications/ming-edge.desktop << 'MINGEDGEDESKTOP'
[Desktop Entry]
Type=Application
Name=Microsoft Edge
Name[zh_CN]=Microsoft Edge Browser
Comment=Browse the web with Microsoft Edge
Exec=/usr/local/bin/ming-edge %U
Icon=microsoft-edge
Terminal=false
Categories=Network;WebBrowser;
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
StartupNotify=true
StartupWMClass=microsoft-edge
MINGEDGEDESKTOP

    mkdir -p "/home/${MING_USER}/.config"
    cat > "/home/${MING_USER}/.config/mimeapps.list" << 'MIMECFG'
[Default Applications]
text/html=ming-edge.desktop
text/xml=ming-edge.desktop
application/xhtml+xml=ming-edge.desktop
x-scheme-handler/http=ming-edge.desktop
x-scheme-handler/https=ming-edge.desktop
MIMECFG
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/mimeapps.list"
    sudo -u "${MING_USER}" xdg-settings set default-web-browser ming-edge.desktop 2>/dev/null || true
    update-alternatives --install /usr/bin/x-www-browser x-www-browser /usr/bin/microsoft-edge-stable 200 2>/dev/null || true
    update-alternatives --install /usr/bin/gnome-www-browser gnome-www-browser /usr/bin/microsoft-edge-stable 200 2>/dev/null || true

    configure_edge_policies
    deploy_browser_homepage
}

configure_edge_policies() {
    local pol_dirs=(
        "/etc/opt/edge/policies/managed"
        "/etc/chromium/policies/managed"
    )
    for d in "${pol_dirs[@]}"; do
        mkdir -p "${d}"
        cat > "${d}/ming-os.json" << 'EDGEPOLICY'
{
  "ExtensionSettings": {
    "cjpalhdlnbpafiamejdnhcphjbkeiagm": {
      "installation_mode": "force_installed",
      "update_url": "https://clients2.google.com/service/update2/crx"
    }
  },
  "HomepageLocation": "file:///usr/share/ming-os/homepage/index.html",
  "RestoreOnStartup": 4,
  "RestoreOnStartupURLs": ["file:///usr/share/ming-os/homepage/index.html"],
  "ShowHomeButton": true,
  "DefaultBrowserSettingEnabled": false,
  "MetricsReportingEnabled": false,
  "PromotionalTabsEnabled": false,
  "HideFirstRunExperience": true
}
EDGEPOLICY
    done
    echo "[02_apps] Edge policies deployed."
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
deploy_browser_homepage() {
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
    echo "[02_apps] Browser homepage deployed."
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
        echo "[02_apps] MING_PREINSTALL_WPS=0，跳过 WPS 预装，保留按需安装脚本。"
        return 0
    fi

    echo "下载 WPS Office..."
    if wget -q --show-progress -O "${wps_deb}" "${wps_url}" 2>/dev/null; then
        # 用 apt-build 可执行包装器（非 shell 函数，timeout 可直接调用）
        if ! timeout 900 /usr/local/sbin/apt-build install "${wps_deb}"; then
            echo "[WARN] WPS Office 安装超时或失败，保留按需安装脚本。"
            /usr/local/sbin/apt-build -f install || true
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
    cat > /usr/local/bin/ming-install-wechat << 'WECHATINSTALL'
#!/usr/bin/env bash
set -euo pipefail
url="https://dldir1.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
deb="/tmp/WeChatLinux_x86_64.deb"
trap 'rm -f "${deb}"' EXIT
echo "Downloading official WeChat for Linux..."
wget -c --show-progress -O "${deb}" "${url}"
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Administrator privileges are required to install WeChat." >&2
    exit 1
fi
if [[ -x /usr/local/sbin/ming-package-installer ]]; then
    /usr/local/sbin/ming-package-installer install "${deb}"
else
    # The image-build fallback is still verified, because 03_desktop deploys
    # the common installer after this optional application phase.
    apt-get -y -o Dpkg::Use-Pty=0 install "${deb}" \
        || { apt-get -y -o Dpkg::Use-Pty=0 -f install && apt-get -y -o Dpkg::Use-Pty=0 install "${deb}"; }
fi
echo "WeChat installed."
WECHATINSTALL
    chmod +x /usr/local/bin/ming-install-wechat

    cat > /usr/local/bin/ming-wechat << 'WECHATWRAP'
#!/usr/bin/env bash
set -uo pipefail

MING_SHELL_COMMON="/usr/local/lib/ming-os/ming-shell-common.py"
[[ -r "${MING_SHELL_COMMON}" ]] || MING_SHELL_COMMON="/usr/local/bin/ming-shell-common.py"

find_wechat_argv() {
    # Prefer a system desktop entry shipped by a dpkg-owned WeChat package.
    # The shared parser rejects shell wrappers and validates the real program,
    # so a store entry can retain required arguments without ever evaluating
    # arbitrary third-party Exec text or user-controlled desktop files.
    local package desktop owner candidate
    local -a candidate_argv=()
    shopt -s nullglob
    for desktop in /usr/share/applications/*.desktop; do
        owner="$(dpkg-query -S -- "${desktop}" 2>/dev/null || true)"
        case "${owner,,}" in
            *wechat*:*|*weixin*:*) ;;
            *) continue ;;
        esac
        mapfile -d '' -t candidate_argv < <(
            python3 - "${MING_SHELL_COMMON}" "${desktop}" <<'WECHATDESKTOPPY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("ming_shell_common", sys.argv[1])
module = importlib.util.module_from_spec(spec) if spec else None
try:
    if module is None or spec is None or spec.loader is None:
        raise RuntimeError("shared desktop parser is unavailable")
    spec.loader.exec_module(module)
    entry = module.parse_desktop_file(sys.argv[2])
    if entry is None or module.desktop_launch_diagnostic(entry.argv):
        raise RuntimeError("desktop entry is not safely launchable")
    for argument in entry.argv:
        sys.stdout.buffer.write(str(argument).encode("utf-8", "surrogateescape") + b"\0")
except Exception:
    pass
WECHATDESKTOPPY
        )
        if (( ${#candidate_argv[@]} )); then
            printf '%s\0' "${candidate_argv[@]}"
            return 0
        fi
    done

    # Keep direct package-owned executables and legacy paths as a fallback for
    # packages that do not supply a desktop entry.  They are passed as one argv
    # item, never through a shell.
    for package in wechat weixin com.tencent.wechat; do
        while IFS= read -r candidate; do
            if [[ -x "${candidate}" ]] && [[ "${candidate}" != */share/applications/* ]]; then
                printf '%s\0' "${candidate}"
                return 0
            fi
        done < <(dpkg-query -L "${package}" 2>/dev/null || true)
    done
    for candidate in \
        /usr/bin/wechat \
        /usr/bin/weixin \
        /opt/wechat/wechat \
        /opt/weixin/weixin \
        /opt/apps/com.tencent.wechat/files/wechat \
        /opt/apps/com.tencent.wechat/files/bin/wechat; do
        if [[ -x "${candidate}" ]]; then
            printf '%s\0' "${candidate}"
            return 0
        fi
    done
    candidate="$(command -v wechat 2>/dev/null || command -v weixin 2>/dev/null || true)"
    if [[ -n "${candidate}" ]]; then
        printf '%s\0' "${candidate}"
        return 0
    fi
    return 1
}

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
mode="${MING_WECHAT_MODE:-auto}"
wechat_argv=()
mapfile -d '' -t wechat_argv < <(find_wechat_argv || true)

if (( ${#wechat_argv[@]} == 0 )); then
    if command -v zenity >/dev/null 2>&1; then
        zenity --question \
            --title="微信未安装" \
            --text="未找到微信。是否现在下载安装官方 Linux 版？" \
            --ok-label="安装" --cancel-label="取消" 2>/dev/null || exit 1
    fi
    pkexec /usr/local/bin/ming-install-wechat || sudo /usr/local/bin/ming-install-wechat || exit 1
    mapfile -d '' -t wechat_argv < <(find_wechat_argv || true)
fi

if (( ${#wechat_argv[@]} == 0 )); then
    notify-send -i dialog-error "微信" "安装后仍未找到可安全启动的微信入口，请在应用抽屉查看“启动器需修复”提示。" 2>/dev/null || true
    exit 127
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
    export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:-} --disable-gpu-shader-disk-cache --disable-background-networking --disk-cache-size=67108864 --media-cache-size=33554432"
    export ELECTRON_DISABLE_SECURITY_WARNINGS=1
    export GDK_BACKEND=x11

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i wechat "微信省内存模式" "已启用低缓存策略；通话音频不会受内存上限限制。" 2>/dev/null || true
    fi
fi

audio_preflight() {
    local log="${HOME}/.cache/ming-os/wechat-audio.log"
    if command -v ming-audio-session >/dev/null 2>&1; then
        (timeout 3 ming-audio-session ensure --json >> "${log}" 2>&1 &) || true
    fi
    if command -v ming-device-control >/dev/null 2>&1; then
        {
            printf '[%s] checking playback and call audio\n' "$(date '+%F %T')"
            ming-device-control audio-repair-playback
            ming-device-control audio-repair-call
            ming-device-control audio-status --json
        } >> "${log}" 2>&1 || true
    fi
}

audio_preflight
exec "${wechat_argv[@]}" "$@"
WECHATWRAP
    chmod +x /usr/local/bin/ming-wechat

    cat > /usr/local/bin/ming-wechat-web << 'WECHATWEB'
#!/usr/bin/env bash
set -e
url="https://wx.qq.com/"
if command -v ming-edge >/dev/null 2>&1; then
    exec ming-edge --new-window "${url}"
elif command -v microsoft-edge-stable >/dev/null 2>&1; then
    exec microsoft-edge-stable --new-window "${url}"
elif command -v xdg-open >/dev/null 2>&1; then
    exec xdg-open "${url}"
else
    echo "${url}"
fi
WECHATWEB
    chmod +x /usr/local/bin/ming-wechat-web

    cat > /usr/share/applications/ming-install-wechat.desktop << WECHATDESKTOPSYS
[Desktop Entry]
Name=Install WeChat
Name[zh_CN]=安装微信
Comment=Download and install official WeChat for Linux on demand
Comment[zh_CN]=按需下载安装腾讯官方 Linux 版微信
Exec=pkexec /usr/local/bin/ming-install-wechat
Icon=wechat
Terminal=true
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
NoDisplay=true
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
NoDisplay=true
WECHATMGRDESKTOP
}

# ======================== Fcitx5 中文输入法 ========================

seed_ming_input_file() {
    local relative_path="$1"
    local source="/etc/skel/${relative_path}"
    local destination="/home/${MING_USER}/${relative_path}"

    install -d -m 0755 "$(dirname "${destination}")"
    case "${relative_path}" in
        .config/fcitx5/profile)
            normalize_fcitx_profile "${destination}" "${source}" "${MING_USER}" || return 1
            return 0
            ;;
        .config/fcitx5/conf/classicui.conf)
            normalize_fcitx_classicui "${destination}" "${MING_USER}" || return 1
            return 0
            ;;
        .xinputrc)
            normalize_fcitx_xinputrc "${destination}" "${source}" "${MING_USER}" || return 1
            return 0
            ;;
    esac
    if [[ ! -e "${destination}" ]]; then
        install -m 0644 -o "${MING_USER}" -g "${MING_USER}" "${source}" "${destination}"
    fi
}

migrate_legacy_fcitx_profile_path() {
    local profile_path="$1"
    local backup_path="${profile_path}.legacy-directory"

    [[ -d "${profile_path}" ]] || return 0
    if [[ -z "$(find "${profile_path}" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        rmdir "${profile_path}"
        return
    fi
    if [[ -e "${backup_path}" && ! -d "${backup_path}" ]]; then
        echo "[ERROR] cannot preserve legacy Fcitx5 profile directory: ${backup_path} already exists" >&2
        return 1
    fi
    mv "${profile_path}" "${backup_path}"
}

backup_legacy_fcitx_file() {
    local path="$1"
    local backup_path="${path}.ming-legacy-backup"

    [[ -f "${path}" ]] || return 0
    [[ -e "${backup_path}" ]] || cp -a "${path}" "${backup_path}"
}

fcitx_profile_is_ming() {
    local path="$1"
    [[ -f "${path}" ]] \
        && grep -Fxq "DefaultIM=pinyin" "${path}" \
        && grep -Fxq "Name=pinyin" "${path}" \
        && grep -Fxq "Name=rime" "${path}"
}

normalize_fcitx_profile() {
    local path="$1"
    local source="$2"
    local owner="${3:-}"

    migrate_legacy_fcitx_profile_path "${path}" || return 1
    if fcitx_profile_is_ming "${path}"; then
        return 0
    fi
    backup_legacy_fcitx_file "${path}" || return 1
    install -d -m 0755 "$(dirname "${path}")"
    install -m 0644 "${source}" "${path}"
    [[ -z "${owner}" ]] || chown "${owner}:${owner}" "${path}"
}

fcitx_classicui_is_ming() {
    local path="$1"
    local key

    [[ -f "${path}" ]] || return 1
    for key in Theme Font MenuFont 'Vertical Candidate List'; do
        [[ "$(grep -Ec "^${key}=" "${path}")" -eq 1 ]] || return 1
    done
    grep -Fxq "Theme=Ming-Candidate" "${path}" \
        && grep -Fxq "Font=Noto Sans CJK SC 15" "${path}" \
        && grep -Fxq "MenuFont=Noto Sans CJK SC 16" "${path}" \
        && grep -Fxq "Vertical Candidate List=True" "${path}"
}

normalize_fcitx_classicui() {
    local path="$1"
    local owner="${2:-}"

    if fcitx_classicui_is_ming "${path}"; then
        return 0
    fi
    backup_legacy_fcitx_file "${path}" || return 1
    install -d -m 0755 "$(dirname "${path}")"
    touch "${path}"
    sed -i -E '/^(Theme|Font|MenuFont|Vertical Candidate List)=/d' "${path}"
    cat >> "${path}" << 'FCITX5CLASSICUI'
Theme=Ming-Candidate
Font=Noto Sans CJK SC 15
MenuFont=Noto Sans CJK SC 16
Vertical Candidate List=True
FCITX5CLASSICUI
    [[ -z "${owner}" ]] || chown "${owner}:${owner}" "${path}"
}

fcitx_xinputrc_is_ming() {
    local path="$1"

    [[ -f "${path}" ]] \
        && grep -Fxq "export GTK_IM_MODULE=fcitx" "${path}" \
        && grep -Fxq "export QT_IM_MODULE=fcitx" "${path}" \
        && grep -Fxq "export XMODIFIERS=@im=fcitx" "${path}" \
        && ! grep -Fq "run_im fcitx5" "${path}" \
        && ! grep -Fq "fcitx5 -d --replace" "${path}"
}

normalize_fcitx_xinputrc() {
    local path="$1"
    local source="$2"
    local owner="${3:-}"

    if fcitx_xinputrc_is_ming "${path}"; then
        return 0
    fi
    backup_legacy_fcitx_file "${path}" || return 1
    install -d -m 0755 "$(dirname "${path}")"
    install -m 0644 "${source}" "${path}"
    [[ -z "${owner}" ]] || chown "${owner}:${owner}" "${path}"
}

write_ming_input_seeds() {
    local skel_root="/etc/skel"

    install -d -m 0755 \
        "${skel_root}/.config/autostart" \
        "${skel_root}/.config/fcitx5" \
        "${skel_root}/.config/fcitx5/conf"

    # This is an Fcitx5 data directory, outside package-owned /usr/share.
    # XDG_DATA_DIRS includes /usr/local/share on Ming OS sessions.
    install -d -m 0755 /usr/local/share/fcitx5/themes/Ming-Candidate
    cat > /usr/local/share/fcitx5/themes/Ming-Candidate/theme.conf << 'MINGCANDIDATETHEME'
[Metadata]
Name=Ming Candidate
Version=1
Author=Ming OS

[InputPanel]
NormalColor=#1F2937
HighlightCandidateColor=#0F766E
HighlightColor=#FFFFFF
HighlightBackgroundColor=#CCFBF1
MINGCANDIDATETHEME

    if ! fcitx_xinputrc_is_ming "${skel_root}/.xinputrc"; then
        backup_legacy_fcitx_file "${skel_root}/.xinputrc" || return 1
        cat > "${skel_root}/.xinputrc" << 'MINGXINPUTRC'
# Compatibility environment only.  The Fcitx5 daemon is started exclusively
# by ~/.config/autostart/fcitx5.desktop.
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export GLFW_IM_MODULE=fcitx
MINGXINPUTRC
    fi

    cat > "${skel_root}/.config/autostart/fcitx5.desktop" << 'FCITX5AUTO'
[Desktop Entry]
Type=Application
Name=Fcitx 5
Comment=Start Chinese input method
Exec=sh -c 'sleep 2; fcitx5 -d --replace'
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
FCITX5AUTO

    # Fcitx5 reads this exact file; profile is not a directory.
    migrate_legacy_fcitx_profile_path "${skel_root}/.config/fcitx5/profile" || return 1
    if ! fcitx_profile_is_ming "${skel_root}/.config/fcitx5/profile"; then
        backup_legacy_fcitx_file "${skel_root}/.config/fcitx5/profile" || return 1
        cat > "${skel_root}/.config/fcitx5/profile" << 'FCITX5PROFILE'
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

[Groups/0/Items/2]
Name=rime
Layout=

[GroupOrder]
0=Default
FCITX5PROFILE
    fi

    normalize_fcitx_classicui "${skel_root}/.config/fcitx5/conf/classicui.conf" || return 1

    cat > "${skel_root}/.config/fcitx5/config" << 'FCITX5CONFIG'
[Behavior]
DefaultPageSize=7
FCITX5CONFIG
}

ensure_fcitx5_environment() {
    local line variable
    touch /etc/environment
    while IFS= read -r line; do
        variable="${line%%=*}"
        sed -i "\\|^${variable}=|d" /etc/environment
        printf '%s\n' "${line}" >> /etc/environment
    done << 'FCITX5ENV'
GTK_IM_MODULE=fcitx
QT_IM_MODULE=fcitx
XMODIFIERS=@im=fcitx
SDL_IM_MODULE=fcitx
GLFW_IM_MODULE=fcitx
FCITX5ENV

    cat > /etc/X11/Xsession.d/80-ming-fcitx5 << 'FCITX5XSESSION'
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export GLFW_IM_MODULE=fcitx
FCITX5XSESSION
}

install_fcitx5() {
    apt install -y --no-install-recommends \
        fcitx5 \
        fcitx5-chinese-addons \
        fcitx5-frontend-gtk2 \
        fcitx5-frontend-gtk3 \
        fcitx5-frontend-gtk4 \
        fcitx5-frontend-qt5 \
        fcitx5-frontend-qt6 \
        fcitx5-config-qt \
        fcitx5-material-color || return 1

    apt install -y --no-install-recommends \
        fcitx5-rime \
        librime-data \
        rime-data-luna-pinyin || return 1

    write_ming_input_seeds || return 1
    ensure_fcitx5_environment

    # First-install seeds only.  Existing profiles, Rime user dictionaries and
    # user customizations remain untouched during re-runs and upgrades.
    seed_ming_input_file .xinputrc || return 1
    seed_ming_input_file .config/autostart/fcitx5.desktop || return 1
    seed_ming_input_file .config/fcitx5/profile || return 1
    seed_ming_input_file .config/fcitx5/conf/classicui.conf || return 1
    seed_ming_input_file .config/fcitx5/config || return 1

    cat > /usr/local/bin/ming-input-healthcheck << 'MINGINPUTHEALTH'
#!/usr/bin/env bash
set -u
errors=0
check_file() {
    if [[ -f "$1" ]]; then
        printf '[OK] %s\n' "$1"
    else
        printf '[ERROR] missing %s\n' "$1" >&2
        errors=$((errors + 1))
    fi
}
for cmd in fcitx5 fcitx5-config-qt; do
    if command -v "${cmd}" >/dev/null 2>&1; then
        printf '[OK] %s available\n' "${cmd}"
    else
        printf '[ERROR] %s missing\n' "${cmd}" >&2
        errors=$((errors + 1))
    fi
done
check_file "${HOME}/.xinputrc"
check_file "${HOME}/.config/autostart/fcitx5.desktop"
check_file "${HOME}/.config/fcitx5/profile"
grep -Fq 'DefaultIM=pinyin' "${HOME}/.config/fcitx5/profile" 2>/dev/null || {
    printf '[ERROR] Fcitx5 default input method is not pinyin\n' >&2
    errors=$((errors + 1))
}
env | grep -Eq '^XMODIFIERS=@im=fcitx$|XMODIFIERS=@im=fcitx' || {
    printf '[WARN] current shell does not expose XMODIFIERS=@im=fcitx; check after graphical login\n' >&2
}
exit "${errors}"
MINGINPUTHEALTH
    chmod 0755 /usr/local/bin/ming-input-healthcheck

    cat > /usr/local/sbin/ming-input-control << 'MINGINPUTCONTROL'
#!/usr/bin/env bash
set -u

PROFILE_FILE="${MING_INPUT_PROFILE:-${HOME}/.config/fcitx5/profile}"
CLASSICUI_FILE="${MING_INPUT_CLASSICUI:-${HOME}/.config/fcitx5/conf/classicui.conf}"
RIME_SCHEMA="${MING_RIME_SCHEMA:-/usr/share/rime-data/luna_pinyin.schema.yaml}"
RIME_ADDON="${MING_RIME_ADDON:-}"

bool() {
    if "$@"; then
        printf 'true'
    else
        printf 'false'
    fi
}

profile_value() {
    local key="$1"
    grep -m1 -E "^${key}=" "${PROFILE_FILE}" 2>/dev/null | cut -d= -f2-
}

config_value() {
    local key="$1"
    grep -m1 -E "^${key}=" "${CLASSICUI_FILE}" 2>/dev/null | cut -d= -f2-
}

profile_has_rime() {
    grep -Fxq 'Name=rime' "${PROFILE_FILE}" 2>/dev/null
}

rime_addon_available() {
    if [[ -n "${RIME_ADDON}" ]]; then
        [[ -r "${RIME_ADDON}" ]]
        return
    fi
    find /usr/lib /usr/libexec -type f -path '*/fcitx5/rime.so' -print -quit 2>/dev/null | grep -q .
}

rime_schema_available() {
    [[ -r "${RIME_SCHEMA}" ]]
}

rime_available() {
    rime_addon_available && rime_schema_available && profile_has_rime
}

daemon_running() {
    pgrep -x fcitx5 >/dev/null 2>&1
}

framework_available() {
    command -v fcitx5 >/dev/null 2>&1
}

current_engine() {
    local engine=""
    if command -v fcitx5-remote >/dev/null 2>&1; then
        engine="$(fcitx5-remote -n 2>/dev/null || true)"
    fi
    if [[ -n "${engine}" ]]; then
        printf '%s' "${engine}"
    else
        profile_value DefaultIM
    fi
}

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    printf '%s' "${value}"
}

status_json() {
    local framework_name="missing"
    local default_engine theme font menu_font engine
    local framework daemon rime_profile rime_addon rime_schema rime_ready deploy_capable

    framework="$(bool framework_available)"
    daemon="$(bool daemon_running)"
    rime_profile="$(bool profile_has_rime)"
    rime_addon="$(bool rime_addon_available)"
    rime_schema="$(bool rime_schema_available)"
    rime_ready="$(bool rime_available)"
    if [[ "${framework}" == true ]]; then
        framework_name="fcitx5"
    fi
    default_engine="$(profile_value DefaultIM)"
    theme="$(config_value Theme)"
    font="$(config_value Font)"
    menu_font="$(config_value MenuFont)"
    engine="$(current_engine)"
    deploy_capable=false
    if [[ "${daemon}" == true ]] && command -v fcitx5-remote >/dev/null 2>&1; then
        deploy_capable=true
    fi
    [[ -n "${default_engine}" ]] || default_engine="missing"
    [[ -n "${theme}" ]] || theme="missing"
    if [[ "${font}" == 'Noto Sans CJK SC 15' && "${menu_font}" == 'Noto Sans CJK SC 16' ]]; then
        font='Noto Sans CJK SC 15/16'
    fi
    [[ -n "${font}" ]] || font="missing"
    [[ -n "${engine}" ]] || engine="missing"

    printf '{"framework":{"name":"%s","available":%s},' "$(json_escape "${framework_name}")" "${framework}"
    printf '"daemon":{"running":%s},' "${daemon}"
    printf '"profile":{"default":"%s","rime_entry":%s},' "$(json_escape "${default_engine}")" "${rime_profile}"
    printf '"addon":{"rime":%s},' "${rime_addon}"
    printf '"theme":"%s","font":"%s",' "$(json_escape "${theme}")" "$(json_escape "${font}")"
    printf '"current_engine":"%s",' "$(json_escape "${engine}")"
    printf '"rime":{"available":%s,"schema":%s,"deploy_capable":%s}}\n' "${rime_ready}" "${rime_schema}" "${deploy_capable}"
}

fallback_to_pinyin() {
    if command -v fcitx5-remote >/dev/null 2>&1; then
        fcitx5-remote -s pinyin >/dev/null 2>&1 || true
    fi
    printf 'Rime schema or deployment is unavailable; fell back to pinyin.\n' >&2
    return 1
}

set_engine() {
    local requested="$1"
    case "${requested}" in
        pinyin)
            if ! command -v fcitx5-remote >/dev/null 2>&1 || ! fcitx5-remote -s pinyin; then
                printf 'Fcitx5 daemon is unavailable; could not select pinyin.\n' >&2
                return 1
            fi
            ;;
        rime)
            if ! rime_available; then
                fallback_to_pinyin
                return 1
            fi
            if ! command -v fcitx5-remote >/dev/null 2>&1 || ! fcitx5-remote -s rime; then
                fallback_to_pinyin
                return 1
            fi
            if [[ "$(current_engine)" != rime ]]; then
                fallback_to_pinyin
                return 1
            fi
            ;;
        *)
            printf 'usage: ming-input-control set-engine <pinyin|rime>\n' >&2
            return 2
            ;;
    esac
}

case "${1:-}" in
    status)
        if [[ "${2:-}" != --json || "$#" -ne 2 ]]; then
            printf 'usage: ming-input-control status --json\n' >&2
            exit 2
        fi
        status_json
        ;;
    set-engine)
        if [[ "$#" -ne 2 ]]; then
            printf 'usage: ming-input-control set-engine <pinyin|rime>\n' >&2
            exit 2
        fi
        set_engine "$2"
        ;;
    *)
        printf 'usage: ming-input-control {status --json|set-engine <pinyin|rime>}\n' >&2
        exit 2
        ;;
esac
MINGINPUTCONTROL
    chmod 0755 /usr/local/sbin/ming-input-control
}

# ======================== 应用商店 (星火应用商店) ========================

install_app_store() {
    /usr/local/sbin/apt-build install --no-install-recommends \
        curl \
        wget \
        apt-transport-https \
        xdg-utils \
        xdg-desktop-portal \
        xdg-desktop-portal-gtk \
        libnotify-bin || return 1

    cat > /usr/local/bin/ming-install-spark-store << 'SPARKINSTALL'
#!/usr/bin/env bash
set -euo pipefail

readonly version="5.2.1.0"
readonly deb_name="spark-store_5.2.1.0_amd64.deb"
readonly expected_sha256="88AE82CE4E487FF0E1F7172CC089BDC50332D5ABF8183DDAE4B9E6650CAC2D55"
readonly url="https://gitee.com/spark-store-project/spark-store/releases/download/5.2.1.0/${deb_name}"
readonly deb="/tmp/${deb_name}"
log="/var/log/ming-spark-store-install.log"

cleanup() {
    rm -f "${deb}" "${apt_output:-}" "${result_file:-}"
}
trap cleanup EXIT

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Administrator privileges are required to install Spark Store." >&2
    exit 1
fi

verify_spark_asset() {
    local candidate="$1"
    local actual_sha256
    [[ -f "${candidate}" && ! -L "${candidate}" ]] || return 1
    actual_sha256="$(sha256sum -- "${candidate}" | awk '{print toupper($1)}')" \
        || return 1
    [[ "${actual_sha256}" == "${expected_sha256}" ]]
}

build_mode="${MING_RELEASE_MODE:-development}"
case "${build_mode}" in
    release|development) ;;
    *) printf '%s\n' E_PACKAGE_FAILED >&2; exit 1 ;;
esac

asset=""
candidates=()
if [[ -n "${MING_SPARK_STORE_ASSET:-}" ]]; then
    candidates+=("${MING_SPARK_STORE_ASSET}")
fi
candidates+=(
    "/tmp/ming-build/assets/${deb_name}"
    "/var/cache/ming-os/${deb_name}"
)
for candidate in "${candidates[@]}"; do
    [[ -e "${candidate}" || -L "${candidate}" ]] || continue
    if verify_spark_asset "${candidate}"; then
        asset="${candidate}"
        break
    fi
    if [[ "${build_mode}" == "release" ]]; then
        printf '%s\n' E_PACKAGE_FAILED | tee -a "${log}" >&2
        exit 1
    fi
done

if [[ -n "${asset}" ]]; then
    if [[ "${asset}" != "${deb}" ]]; then
        install -m 0600 "${asset}" "${deb}"
    else
        chmod 0600 "${deb}"
    fi
elif [[ "${build_mode}" == "release" ]]; then
    printf '%s\n' E_PACKAGE_FAILED | tee -a "${log}" >&2
    exit 1
else
    echo "Downloading pinned Spark Store ${version}..."
    if ! timeout --foreground 300s wget \
        --quiet \
        --https-only \
        --connect-timeout=10 \
        --read-timeout=60 \
        --tries=2 \
        --output-document="${deb}" \
        "${url}"; then
        printf '%s\n' E_PACKAGE_FAILED | tee -a "${log}" >&2
        exit 1
    fi
fi
if ! verify_spark_asset "${deb}"; then
    printf '%s\n' E_PACKAGE_FAILED | tee -a "${log}" >&2
    exit 1
fi

mkdir -p /root/.config "${HOME:-/root}/.config"
touch /root/.config/mimeapps.list "${HOME:-/root}/.config/mimeapps.list" 2>/dev/null || true

# At runtime the shared local-package installer validates architecture and
# metadata, performs one controlled dependency repair, verifies dpkg state and
# refreshes desktop/icon caches.  During the early image build 03_desktop has
# not deployed it yet, so retain a strictly verified bootstrap fallback.
if [[ -x /usr/local/sbin/ming-package-installer ]]; then
    result_file="$(mktemp /tmp/ming-spark-install-result.XXXXXX)"
    if ! /usr/local/sbin/ming-package-installer install "${deb}" >"${result_file}"; then
        cat "${result_file}" | tee -a "${log}" >&2
        rm -f "${result_file}"
        exit 1
    fi
    cat "${result_file}" >>"${log}"
    if ! python3 - "${result_file}" <<'SPARKRESULTPY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as stream:
        result = json.load(stream)
except (OSError, TypeError, ValueError):
    result = None
if not isinstance(result, dict):
    print("E_PACKAGE_FAILED", file=sys.stderr)
    raise SystemExit(1)
if result.get("ok") is not True:
    print(str(result.get("error_code") or "E_PACKAGE_FAILED"), file=sys.stderr)
    raise SystemExit(1)
if result.get("launch_ready") is True:
    raise SystemExit(0)
print(str(result.get("error_code") or "E_LAUNCH_NOT_READY"), file=sys.stderr)
raise SystemExit(1)
SPARKRESULTPY
    then
        cat "${result_file}" >&2
        rm -f "${result_file}"
        exit 1
    fi
    cat "${result_file}"
    rm -f "${result_file}"
else
    apt_output="$(mktemp /tmp/ming-spark-apt.XXXXXX)"

    run_locked_apt() {
        flock --exclusive --timeout 30 --conflict-exit-code 75 \
            /run/lock/ming-package-manager.lock \
            apt-get -y -o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=60 "$@"
    }

    apt_failure_code() {
        local returncode="$1"
        if [[ "${returncode}" -eq 75 ]] || grep -Eqi \
            'Could not (get|open) lock (file )?/var/lib/dpkg/|Unable to acquire the dpkg frontend lock|Unable to lock the administration directory' \
            "${apt_output}"; then
            printf '%s\n' E_PACKAGE_BUSY
        elif grep -Eqi \
            'unmet dependencies|held broken packages|unable to correct problems|pkgProblemResolver|dependency problems|dependency error' \
            "${apt_output}"; then
            printf '%s\n' E_RESOLVER_FAILED
        else
            printf '%s\n' E_PACKAGE_FAILED
        fi
    }

    install_rc=0
    install_code=""
    if run_locked_apt install "${deb}" >"${apt_output}" 2>&1; then
        :
    else
        install_rc=$?
        install_code="$(apt_failure_code "${install_rc}")"
        if [[ "${install_code}" == "E_RESOLVER_FAILED" ]]; then
            if run_locked_apt -f install >"${apt_output}" 2>&1; then
                if run_locked_apt install "${deb}" >"${apt_output}" 2>&1; then
                    install_rc=0
                    install_code=""
                else
                    install_rc=$?
                    install_code="$(apt_failure_code "${install_rc}")"
                fi
            else
                install_rc=$?
                install_code="$(apt_failure_code "${install_rc}")"
            fi
        fi
    fi
    if [[ "${install_rc}" -ne 0 ]]; then
        printf '[%s] Spark Store package transaction failed: %s\n' \
            "$(date '+%F %T')" "${install_code}" >>"${log}"
        printf '%s\n' "${install_code}" >&2
        exit 1
    fi
    if ! dpkg-query -W -f='${db:Status-Abbrev}' spark-store 2>/dev/null | grep -q '^ii '; then
        printf '%s\n' E_PACKAGE_FAILED | tee -a "${log}" >&2
        exit 1
    fi
    if [[ ! -x /usr/local/bin/spark-store ]]; then
        printf '%s\n' E_LAUNCH_NOT_READY | tee -a "${log}" >&2
        exit 1
    fi
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# Spark Store currently ships an enabled notifier with invalid restart fields
# and an unbounded network wait. Keep the vendor file intact, but prevent it
# from entering the boot transaction; Ming's delayed readiness timer owns this.
mask_spark_update_notifier() {
    if [[ ! -f /usr/lib/systemd/system/spark-update-notifier.service \
        && ! -f /lib/systemd/system/spark-update-notifier.service ]]; then
        return 0
    fi
    mkdir -p /etc/systemd/system
    rm -f /etc/systemd/system/multi-user.target.wants/spark-update-notifier.service
    ln -sfn /dev/null /etc/systemd/system/spark-update-notifier.service
}
mask_spark_update_notifier

# A graphical user may already be logged in while a package is installed.
# Trigger a bounded catalog sync; the drawer also rescans when it opens.
target_user=""
if [[ -n "${SUDO_USER:-}" ]] && id "${SUDO_USER}" >/dev/null 2>&1; then
    target_user="${SUDO_USER}"
elif [[ -n "${PKEXEC_UID:-}" ]]; then
    target_user="$(getent passwd "${PKEXEC_UID}" 2>/dev/null | cut -d: -f1 || true)"
fi
if [[ -n "${target_user}" ]]; then
    runuser -u "${target_user}" -- ming-phone-desktop --sync >>"${log}" 2>&1 || true
fi
echo "Spark Store installed."
SPARKINSTALL
    chmod +x /usr/local/bin/ming-install-spark-store

    if ! /usr/local/bin/ming-install-spark-store; then
        echo "[WARN] 星火应用商店安装失败，保留 ming-install-spark-store 供用户联网后重试。"
    fi

    cat > /usr/local/bin/ming-spark-store << 'MINGSPARK'
#!/usr/bin/env bash
set -u

MING_SPARK_LOG="${HOME}/.cache/ming-os/spark-store.log"
mkdir -p "$(dirname "${MING_SPARK_LOG}")" 2>/dev/null || MING_SPARK_LOG="/tmp/ming-spark-store.log"

spark_bin="/usr/local/bin/spark-store"

if [[ ! -x "${spark_bin}" ]]; then
    notify-send -i dialog-error "星火应用商店" "应用商店尚未安装，请从应用库运行“修复星火应用商店”。" 2>/dev/null || true
    printf '[%s] Spark Store binary is missing\n' "$(date '+%F %T')" >>"${MING_SPARK_LOG}"
    if command -v pkexec >/dev/null 2>&1 && [[ -x /usr/local/bin/ming-install-spark-store ]]; then
        exec pkexec /usr/local/bin/ming-install-spark-store "$@"
    fi
    exit 127
fi

spark_args=()
if [[ ! -e /dev/dri/renderD128 ]] || (command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt --quiet); then
    spark_args+=(--ozone-platform=x11 --disable-gpu)
fi

printf '[%s] starting %s %s\n' "$(date '+%F %T')" "${spark_bin}" "${spark_args[*]}" >>"${MING_SPARK_LOG}"
"${spark_bin}" "${spark_args[@]}" "$@" >>"${MING_SPARK_LOG}" 2>&1 &
spark_pid=$!
sleep 2
if kill -0 "${spark_pid}" 2>/dev/null; then
    printf '[%s] Spark Store process is running after startup window\n' "$(date '+%F %T')" >>"${MING_SPARK_LOG}"
    exit 0
fi

wait "${spark_pid}"
rc=$?
if [[ "${rc}" -eq 0 ]]; then
    printf '[%s] Spark Store launcher daemonized successfully\n' "$(date '+%F %T')" >>"${MING_SPARK_LOG}"
    exit 0
fi

if pgrep -f '[/](spark-store)( |$)' >/dev/null 2>&1 \
    || (command -v wmctrl >/dev/null 2>&1 && wmctrl -lx 2>/dev/null | grep -qi 'spark-store'); then
    printf '[%s] Spark Store is ready despite launcher exit rc=%s\n' "$(date '+%F %T')" "${rc}" >>"${MING_SPARK_LOG}"
    exit 0
fi

printf '[%s] Spark Store startup failed rc=%s with no process or window\n' "$(date '+%F %T')" "${rc}" >>"${MING_SPARK_LOG}"
notify-send -i dialog-error "星火应用商店" "启动失败，日志：${MING_SPARK_LOG}" 2>/dev/null || true
exit "${rc}"
MINGSPARK
    chmod 0755 /usr/local/bin/ming-spark-store

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
Exec=/usr/local/bin/ming-spark-store
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
Exec=/usr/local/bin/ming-spark-store
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
Description=Ming OS App Store Readiness (delayed, non-blocking)
After=graphical.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'command -v spark-store >/dev/null 2>&1 || /usr/local/bin/ming-install-spark-store || true'
TimeoutStartSec=90
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SVCUNIT

    cat > /etc/systemd/system/ming-appstore-ready.timer << 'SPARKTIMER'
[Unit]
Description=Ming OS delayed Spark Store readiness
After=graphical.target

[Timer]
OnBootSec=90s
AccuracySec=30s
Unit=ming-appstore-ready.service

[Install]
WantedBy=timers.target
SPARKTIMER

    systemctl disable --now ming-appstore-ready.service 2>/dev/null || true
    systemctl enable ming-appstore-ready.timer 2>/dev/null || true
}

# ======================== 附加实用工具 ========================

install_required_desktop_runtime() {
    local package
    if ! apt install -y -o Dpkg::Options::=--force-confold --no-install-recommends \
        "${REQUIRED_DESKTOP_RUNTIME_PACKAGES[@]}"; then
        echo "[ERROR] [02_apps] failed to install required desktop runtime packages" >&2
        return 1
    fi

    for package in "${REQUIRED_DESKTOP_RUNTIME_PACKAGES[@]}"; do
        if ! dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -qx 'ii '; then
            echo "[ERROR] [02_apps] required desktop runtime package is not installed: ${package}" >&2
            return 1
        fi
    done
}

enable_bluetooth_after_runtime() {
    # Do not ask systemd to enable bluetooth until the mandatory runtime
    # validation has confirmed that BlueZ is actually installed.  `systemctl
    # enable` is idempotent and may be unavailable inside the build chroot.
    if ! dpkg-query -W -f='${db:Status-Abbrev}' bluez 2>/dev/null \
        | grep -qx 'ii '; then
        echo "[ERROR] BlueZ runtime is missing; refusing to enable bluetooth.service" >&2
        return 1
    fi
    systemctl enable bluetooth.service 2>/dev/null || true
}

install_utilities() {
    apt install -y -o Dpkg::Options::=--force-confold --no-install-recommends \
        pavucontrol \
        pulseaudio-module-bluetooth \
        bluez-tools \
        blueman \
        bluetooth \
        network-manager-openvpn \
        network-manager-openvpn-gnome \
        mobile-broadband-provider-info \
        modemmanager \
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
        udisks2 \
        udisks2-btrfs \
        dmidecode \
        x11-xserver-utils \
        arandr \
        autorandr \
        mesa-utils \
        inxi \
        redshift

    # Trixie does not currently publish touchegg.  Keep it an independent,
    # best-effort enhancement so its absence cannot suppress Blueman or the
    # other audio/network utilities above.
    apt install -y --no-install-recommends touchegg || true
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

    run_required_step install_xfce_desktop || return 1
    run_required_step install_fonts || return 1
    run_required_step install_required_desktop_runtime || return 1
    run_required_step generate_edge_video_samples || return 1
    run_required_step enable_bluetooth_after_runtime || return 1
    run_required_step install_fcitx5 || return 1
    run_required_step deploy_eyecare || return 1

    run_optional_step install_edge
    run_optional_step install_wps_office
    run_optional_step install_wechat
    run_optional_step install_app_store
    run_optional_step install_utilities

    echo "=====> [02_apps] 应用软件安装完成 <====="
}

main
