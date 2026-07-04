#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 01: 基础系统配置
# ============================================================================
# 设计意图：
#   在 debootstrap 生成的最小系统上，配置 APT 源、安装核心系统组件、
#   设置语言/时区/用户/网络等基础环境，为后续模块提供可运行的基础系统。
#
# 输入：
#   环境变量: MING_OS_VERSION, MING_USER, MING_USER_PASS, ROOT_PASS
#   （由主构建脚本通过 chroot_exec 注入）
#
# 输出：
#   配置完成的 chroot 根文件系统
#
# 关键步骤：
#   1. 配置清华 TUNA APT 源
#   2. 安装 Linux 内核、systemd、基础工具
#   3. 配置语言环境 (zh_CN.UTF-8) 与时区 (Asia/Shanghai)
#   4. 创建默认用户 ming 并配置 sudo
#   5. 安装 NetworkManager 与基础网络工具
#   6. 配置系统标识为 Ming OS
# ============================================================================

set -uo pipefail

# ======================== APT 源配置 ========================

configure_apt_sources() {
    # 使用清华大学 TUNA 镜像源，加速国内下载
    cat > /etc/apt/sources.list << APTSRC
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie main contrib non-free non-free-firmware
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ trixie-updates main contrib non-free non-free-firmware
deb https://mirrors.tuna.tsinghua.edu.cn/debian-security trixie-security main contrib non-free non-free-firmware
APTSRC

    if [[ "${MING_SKIP_APT_UPDATE:-0}" != "1" ]]; then
        apt update
    fi
}

# ======================== 内核与基础包 ========================

install_base_packages() {
    # 安装 Linux 内核及核心系统组件（必须成功）
    apt install -y --no-install-recommends \
        linux-image-amd64 \
        linux-headers-amd64 \
        systemd \
        systemd-sysv \
        dbus \
        dbus-x11 \
        at-spi2-core \
        sudo \
        apt-utils \
        gnupg2 \
        ca-certificates \
        curl \
        wget \
        jq \
        locales \
        tzdata \
        console-setup \
        keyboard-configuration \
        kmod \
        live-boot \
        live-config \
        live-config-systemd \
        squashfs-tools \
        calamares \
        calamares-settings-debian \
        grub2-common \
        grub-pc-bin \
        grub-efi-amd64-bin \
        efibootmgr \
        dosfstools \
        libpwquality-tools \
        cracklib-runtime \
        wamerican \
        pciutils \
        usbutils \
        procps \
        psmisc \
        less \
        nano \
        vim-tiny \
        bash-completion \
        man-db \
        htop \
        iotop \
        lsof \
        strace \
        file \
        unzip \
        p7zip-full \
        xz-utils \
        bzip2 \
        rsync \
        openssh-client \
        net-tools \
        iproute2 \
        inetutils-ping \
        traceroute \
        dnsutils \
        wireless-tools \
        iw \
        rfkill \
        wpasupplicant \
        acpi \
        acpid \
        acpi-support \
        laptop-detect \
        powertop \
        earlyoom \
        irqbalance \
        tlp \
        tlp-rdw \
        alsa-ucm-conf \
        xserver-xorg-input-all \
        xserver-xorg-input-libinput \
        xserver-xorg-input-synaptics

    # Firmware and microcode packaging shifts across Debian snapshots. Install
    # what exists without letting renamed packages break the whole base system.
    for pkg in \
        firmware-linux \
        firmware-linux-free \
        firmware-linux-nonfree \
        firmware-misc-nonfree \
        firmware-iwlwifi \
        firmware-realtek \
        firmware-atheros \
        firmware-ath9k-htc \
        firmware-brcm80211 \
        firmware-sof-signed \
        firmware-intel-graphics \
        firmware-amd-graphics \
        firmware-nvidia-graphics \
        firmware-mediatek \
        firmware-ralink \
        firmware-ti-connectivity \
        bluez-firmware \
        intel-microcode \
        amd64-microcode; do
        apt install -y --no-install-recommends "${pkg}" || true
    done

    echo "loop" >> /etc/modules
    echo "iwlmvm" >> /etc/modules || true
    echo "iwlwifi" >> /etc/modules || true

    mkdir -p /etc/modprobe.d
    cat > /etc/modprobe.d/ming-blacklist.conf << BLACKLIST
blacklist i2c_piix4
BLACKLIST
}

install_hardware_support_packages() {
    # Core printer/scanner UI must be present because Ming Settings exposes it.
    apt install -y --no-install-recommends \
        cups \
        cups-client \
        system-config-printer \
        avahi-daemon \
        sane-utils \
        simple-scan

    # Extra printer/scanner drivers are intentionally broad but non-fatal:
    # Debian mirrors can temporarily miss a driver package during Trixie syncs.
    for pkg in \
        cups-bsd \
        cups-filters \
        cups-ipp-utils \
        printer-driver-all \
        printer-driver-cups-pdf \
        ipp-usb \
        sane-airscan; do
        apt install -y --no-install-recommends "${pkg}" || true
    done

    systemctl enable cups.socket 2>/dev/null || true
    systemctl enable avahi-daemon 2>/dev/null || true
}

configure_installer_password_policy() {
    # Calamares users page can fail with "error loading dictionary" when
    # libpwquality/cracklib dictionaries are missing or broken. For a consumer
    # installer, accepting the user's chosen password is better than blocking
    # installation. Keep this lenient policy in the image and target system.
    mkdir -p /etc/security
    cat > /etc/security/pwquality.conf << 'PWQUALITY'
# Ming OS installer-friendly password policy.
# The account wizard and auto-login flow are designed for ordinary home users.
minlen = 1
minclass = 0
maxrepeat = 0
maxclassrepeat = 0
dictcheck = 0
usercheck = 0
enforcing = 0
PWQUALITY

    if command -v update-cracklib >/dev/null 2>&1; then
        update-cracklib >/dev/null 2>&1 || true
    fi
}

# ======================== 语言与区域设置 ========================

configure_locale() {
    # 生成简体中文 locale
    sed -i 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen

    # 设置系统默认语言为简体中文
    update-locale LANG=zh_CN.UTF-8
    update-locale LANGUAGE=zh_CN:zh
    update-locale LC_ALL=zh_CN.UTF-8

    # 同时生成英文 locale（部分程序需要）
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen
}

configure_timezone() {
    # 设置时区为东八区（中国标准时间）
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
    echo "Asia/Shanghai" > /etc/timezone
    dpkg-reconfigure -f noninteractive tzdata
}

configure_keyboard() {
    # 配置键盘布局为美式英语（中文输入法后续由 Fcitx5 提供）
    cat > /etc/default/keyboard << KBCFG
XKBMODEL="pc105"
XKBLAYOUT="us"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
KBCFG
    dpkg-reconfigure -f noninteractive keyboard-configuration
}

# ======================== 用户与权限 ========================

configure_users() {
    # 设置 root 密码
    echo "root:${ROOT_PASS}" | chpasswd

    # 创建默认用户 ming
    useradd -m -s /bin/bash -c "Ming OS User" "${MING_USER}"
    echo "${MING_USER}:${MING_USER_PASS}" | chpasswd

    # 创建必要的组（如果不存在）
    for grp in lpadmin plugdev nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    done

    # 将 ming 用户加入必要组（逐个添加，跳过不存在的组）
    for grp in sudo adm cdrom dip plugdev lpadmin netdev audio video input scanner bluetooth nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 && usermod -aG "${grp}" "${MING_USER}" || true
    done

    # 配置 sudo 免密（方便初学者，避免频繁输入密码）
    echo "${MING_USER} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/"${MING_USER}"
    chmod 440 /etc/sudoers.d/"${MING_USER}"

    # 创建用户桌面等 XDG 目录
    sudo -u "${MING_USER}" mkdir -p \
        "/home/${MING_USER}/Desktop" \
        "/home/${MING_USER}/Documents" \
        "/home/${MING_USER}/Downloads" \
        "/home/${MING_USER}/Music" \
        "/home/${MING_USER}/Pictures" \
        "/home/${MING_USER}/Videos"
}

# ======================== 网络管理 ========================

configure_network() {
    apt install -y --no-install-recommends \
        network-manager \
        network-manager-gnome \
        wpasupplicant \
        ifupdown

    apt install -y --no-install-recommends iwd network-manager-iwd || true

    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/wifi-backend.conf << NMWIFICFG
[device]
# Ming OS r4 defaults to wpa_supplicant because it is still the safer choice
# for first/second/third-generation Intel-era laptops and old Broadcom/Atheros
# cards. Users can switch to iwd from Ming Settings if their machine prefers it.
wifi.backend=wpa_supplicant
wifi.scan-rand-mac-address=no
NMWIFICFG

    mkdir -p /etc/iwd
    cat > /etc/iwd/main.conf << IWDCFG
[General]
EnableNetworkConfiguration=true
UseDefaultInterface=true

[Network]
EnableIPv6=true
NameResolvingService=systemd

[Scan]
DisableRoamingScan=false
IWDCFG

    systemctl disable iwd 2>/dev/null || true

    mkdir -p /etc/network

    cat > /etc/network/interfaces << IFACES
# This file describes the network interfaces available on your system
# and how to activate them. For more information, see interfaces(5).

source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback
IFACES

    mkdir -p /etc/network/interfaces.d

    cat > /etc/NetworkManager/NetworkManager.conf << NMCFG
[main]
plugins=ifupdown,keyfile

[ifupdown]
managed=true

[device]
wifi.scan-rand-mac-address=no
NMCFG

    systemctl enable NetworkManager 2>/dev/null || true

    # 禁止 rfkill 软阻断 WiFi 无线电
    mkdir -p /etc/systemd/system/NetworkManager.service.d
    cat > /etc/systemd/system/NetworkManager.service.d/rfkill-unblock.conf << RFKILLFIX
[Service]
ExecStartPre=-/usr/sbin/rfkill unblock wifi
ExecStartPre=-/usr/sbin/rfkill unblock all
RFKILLFIX

    cat > /etc/systemd/system/ming-rfkill.service << RFKILLSVC
[Unit]
Description=Ming OS RF Kill Unblock
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock all
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RFKILLSVC
    systemctl enable ming-rfkill.service 2>/dev/null || true

    mkdir -p /etc/systemd/system/NetworkManager-wait-online.service.d
    cat > /etc/systemd/system/NetworkManager-wait-online.service.d/override.conf << NMONLINE
[Service]
ExecStart=
ExecStart=/usr/bin/nm-online -s -q -t 60
NMONLINE

    echo "ming-os" > /etc/hostname

    cat > /etc/hosts << HOSTSCFG
127.0.0.1       localhost
127.0.1.1       ming-os
::1             localhost ip6-localhost ip6-loopback
ff02::1         ip6-allnodes
ff02::2         ip6-allrouters
HOSTSCFG
}

deploy_hardware_diagnostics() {
    cat > /usr/local/bin/ming-network-repair << 'NETREPAIR'
#!/usr/bin/env bash
set -uo pipefail
LOG="/tmp/ming-network-repair.log"
BACKEND="${1:-}"
mkdir -p /tmp
exec > >(tee "${LOG}") 2>&1

echo "Ming OS network repair"
date
echo

if [[ "${BACKEND}" == "--use-iwd" ]]; then
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/wifi-backend.conf <<'EOF'
[device]
wifi.backend=iwd
wifi.iwd.autoconnect=yes
wifi.scan-rand-mac-address=no
EOF
    systemctl enable iwd 2>/dev/null || true
    echo "Selected Wi-Fi backend: iwd"
elif [[ "${BACKEND}" == "--use-wpa" || -z "${BACKEND}" ]]; then
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/wifi-backend.conf <<'EOF'
[device]
wifi.backend=wpa_supplicant
wifi.scan-rand-mac-address=no
EOF
    systemctl disable iwd 2>/dev/null || true
    echo "Selected Wi-Fi backend: wpa_supplicant"
else
    echo "Unknown option: ${BACKEND}"
fi

rfkill unblock all 2>/dev/null || true
systemctl restart wpa_supplicant 2>/dev/null || true
systemctl restart iwd 2>/dev/null || true
systemctl restart NetworkManager 2>/dev/null || true
sleep 2

echo
echo "== radios =="
nmcli radio 2>/dev/null || true
rfkill list 2>/dev/null || true

echo
echo "== devices =="
nmcli device status 2>/dev/null || true

echo
echo "== Wi-Fi hardware =="
lspci -nn 2>/dev/null | grep -Ei 'network|wireless|wifi|802\.11|ethernet' || true
lsusb 2>/dev/null | grep -Ei 'network|wireless|wifi|802\.11|bluetooth|realtek|atheros|broadcom|intel|ralink|mediatek' || true

echo
echo "== missing firmware hints =="
dmesg 2>/dev/null | grep -Ei 'firmware|iwlwifi|ath|brcm|b43|rtl|rt2|mt76|failed|missing' | tail -80 || true

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Ming OS 网络修复结果" --width=820 --height=620 --filename="${LOG}" 2>/dev/null || true
fi
NETREPAIR
    chmod 0755 /usr/local/bin/ming-network-repair

    cat > /usr/local/bin/ming-driver-diagnose << 'DRIVERDIAG'
#!/usr/bin/env bash
set -uo pipefail
LOG="/tmp/ming-driver-diagnose.log"
exec > >(tee "${LOG}") 2>&1
echo "Ming OS driver diagnose"
date
echo
echo "== CPU =="
lscpu 2>/dev/null | sed -n '1,32p' || true
if lscpu 2>/dev/null | grep -Eq '\bavx2\b'; then
    echo "AVX2: available"
else
    echo "AVX2: not available; Ming OS r4 must remain compatible with this class of CPU."
fi
echo
echo "== PCI display/audio/network =="
lspci -nn 2>/dev/null | grep -Ei 'vga|3d|display|audio|network|wireless|ethernet' || true
echo
echo "== USB devices =="
lsusb 2>/dev/null || true
echo
echo "== Loaded display/network/audio modules =="
lsmod 2>/dev/null | grep -Ei 'i915|nouveau|amdgpu|radeon|snd|iwl|ath|brcm|b43|rtl|rt2|mt76|wl|btusb' || true
echo
echo "== Missing firmware / driver errors =="
dmesg 2>/dev/null | grep -Ei 'firmware|microcode|drm|i915|nouveau|amdgpu|radeon|iwlwifi|ath|brcm|b43|rtl|rt2|mt76|snd|failed|error' | tail -140 || true

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Ming OS 驱动检测" --width=860 --height=640 --filename="${LOG}" 2>/dev/null || true
fi
DRIVERDIAG
    chmod 0755 /usr/local/bin/ming-driver-diagnose

    cat > /usr/local/bin/ming-diagnostic-bundle << 'DIAGBUNDLE'
#!/usr/bin/env bash
set -uo pipefail
OUT_DIR="${HOME:-/tmp}/Desktop"
[[ -d "${OUT_DIR}" ]] || OUT_DIR="/tmp"
STAMP="$(date '+%Y%m%d-%H%M%S')"
WORK="/tmp/ming-diagnostics-${STAMP}"
ARCHIVE="${OUT_DIR}/Ming-OS-诊断包-${STAMP}.tar.gz"
mkdir -p "${WORK}"

collect() {
    local name="$1"; shift
    {
        echo "$ $*"
        "$@" 2>&1 || true
    } > "${WORK}/${name}.txt"
}

collect system uname -a
collect os-release cat /etc/os-release
collect cpu lscpu
collect memory free -h
collect disks lsblk -f
collect partitions bash -c 'parted -l 2>/dev/null || true'
collect pci lspci -nn
collect usb lsusb
collect rfkill rfkill list
collect network nmcli device status
collect wifi bash -c 'nmcli -f IN-USE,SSID,BSSID,CHAN,RATE,SIGNAL,SECURITY dev wifi list 2>/dev/null || true'
collect services systemctl --failed --no-pager
collect journal bash -c 'journalctl -b -p warning --no-pager | tail -400'
collect dmesg bash -c 'dmesg | tail -500'

for src in \
    /tmp/ming-installer \
    /tmp/calamares.log \
    /tmp/ming-network-repair.log \
    /tmp/ming-driver-diagnose.log \
    /var/log/calamares.log \
    /var/log/installer \
    /var/log/Xorg.0.log; do
    if [[ -e "${src}" ]]; then
        cp -a "${src}" "${WORK}/" 2>/dev/null || true
    fi
done

tar -C "$(dirname "${WORK}")" -czf "${ARCHIVE}" "$(basename "${WORK}")"
rm -rf "${WORK}"

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --info --title="Ming OS 问题诊断" --width=620 \
        --text="诊断包已生成：\n${ARCHIVE}\n\n把这个文件发给开发者即可，不需要手动输入命令。" 2>/dev/null || true
else
    echo "${ARCHIVE}"
fi
DIAGBUNDLE
    chmod 0755 /usr/local/bin/ming-diagnostic-bundle

    cat > /usr/local/bin/ming-surface-support << 'SURFACE'
#!/usr/bin/env bash
set -uo pipefail
LOG="/tmp/ming-surface-support.log"
exec > >(tee "${LOG}") 2>&1

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --question --width=680 --title="Ming OS Surface 支持" \
        --text="此功能会添加 linux-surface 第三方软件源，并安装 Surface 专用内核与工具。\n\n只建议 Surface Pro/Book/Laptop 等设备使用。安装后需要联网和重启。\n\n是否继续？" \
        2>/dev/null || exit 0
fi

    echo "Installing optional linux-surface support..."
    if [[ "${MING_SKIP_APT_UPDATE:-0}" != "1" ]]; then
        apt update
    fi
    apt install -y --no-install-recommends curl ca-certificates gnupg
mkdir -p /etc/apt/keyrings
curl -fsSL https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc | gpg --dearmor > /etc/apt/keyrings/linux-surface.gpg
cat > /etc/apt/sources.list.d/linux-surface.list <<'EOF'
deb [arch=amd64 signed-by=/etc/apt/keyrings/linux-surface.gpg] https://pkg.surfacelinux.com/debian release main
EOF
    if [[ "${MING_SKIP_APT_UPDATE:-0}" != "1" ]]; then
        apt update
    fi
    apt install -y --no-install-recommends linux-image-surface linux-headers-surface iptsd libwacom-surface linux-surface-secureboot-mok \
    || apt install -y --no-install-recommends linux-image-surface linux-headers-surface \
    || true
apt install -y --no-install-recommends surface-control || true

if command -v update-grub >/dev/null 2>&1; then
    update-grub || true
fi

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Surface 支持安装结果" --width=820 --height=620 --filename="${LOG}" 2>/dev/null || true
fi
SURFACE
    chmod 0755 /usr/local/bin/ming-surface-support

    cat > /usr/local/bin/ming-classic-mode << 'CLASSIC'
#!/usr/bin/env bash
set -uo pipefail
STATE="${HOME}/.config/ming-os/classic-mode"
mkdir -p "$(dirname "${STATE}")"

if [[ -f "${STATE}" ]]; then
    rm -f "${STATE}"
    rm -f "${HOME}/.config/autostart/ming-classic-mode.desktop" 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/use_compositing -s true 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p /desktop-icons/icon-size -n -t int -s 48 2>/dev/null || true
    notify-send "Ming OS 经典轻量模式" "已关闭，重新登录后恢复完整效果。" 2>/dev/null || true
else
    touch "${STATE}"
    pkill picom 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/use_compositing -s false 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p /desktop-icons/icon-size -n -t int -s 42 2>/dev/null || true
    mkdir -p "${HOME}/.config/autostart"
    cat > "${HOME}/.config/autostart/ming-classic-mode.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Ming Classic Mode Runtime
Exec=sh -c 'pkill picom 2>/dev/null || true; xfconf-query -c xfwm4 -p /general/use_compositing -s false 2>/dev/null || true'
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
    notify-send "Ming OS 经典轻量模式" "已开启：关闭模糊和重动画，更适合老 i3/i5/E3 与机械硬盘。" 2>/dev/null || true
fi
CLASSIC
    chmod 0755 /usr/local/bin/ming-classic-mode
}

# ======================== 系统标识 ========================

configure_os_identity() {
    # 设置 Ming OS 品牌标识
    cat > /etc/os-release << OSRELEASE
NAME="Ming OS"
VERSION="${MING_OS_VERSION} Home Edition"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${MING_OS_VERSION} Home Edition"
VERSION_ID="${MING_OS_VERSION}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
OSRELEASE

    # 更新 issue 文件（控制台登录提示）
    cat > /etc/issue << ISSUE
Ming OS ${MING_OS_VERSION} Home Edition - 层层精简，层层用心

ISSUE

    cat > /etc/issue.net << ISSUENET
Ming OS ${MING_OS_VERSION} Home Edition
ISSUENET

    # 自定义 lsb_release 信息
    apt install -y --no-install-recommends lsb-release
    mkdir -p /etc/lsb-release.d
    cat > /etc/lsb-release << LSBRELEASE
DISTRIB_ID=MingOS
DISTRIB_RELEASE=${MING_OS_VERSION}
DISTRIB_CODENAME=ming
DISTRIB_DESCRIPTION="Ming OS ${MING_OS_VERSION} Home Edition"
LSBRELEASE

    # 确保 /etc/debian_version 显示 Debian 13 (Trixie)，而非历史遗留的12
    echo "trixie/sid" > /etc/debian_version
}

# ======================== 安装器品牌与安装后身份兜底 ========================

configure_installer_identity() {
    mkdir -p /usr/local/sbin
    cat > /usr/local/sbin/ming-fix-installed-identity << 'MINGIDENTITY'
#!/usr/bin/env bash
set -uo pipefail

target="${1:-}"
version="${MING_OS_VERSION:-26.3.1}"

find_target_root() {
    local candidate
    for candidate in "${target}" /target /tmp/calamares-root-* /; do
        [[ -n "${candidate}" ]] || continue
        [[ -d "${candidate}" ]] || continue
        if [[ "${candidate}" == "/" || -d "${candidate}/etc" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

target="$(find_target_root)"
if [[ "${target}" != "/" ]]; then
    target="${target%/}"
fi

write_file() {
    local path="$1"
    shift
    mkdir -p "$(dirname "${target}${path}")"
    cat > "${target}${path}"
}

write_file /etc/os-release <<OSRELEASE
NAME="Ming OS"
VERSION="${version} Home Edition"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${version} Home Edition"
VERSION_ID="${version}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
OSRELEASE

write_file /etc/lsb-release <<LSBRELEASE
DISTRIB_ID=MingOS
DISTRIB_RELEASE=${version}
DISTRIB_CODENAME=ming
DISTRIB_DESCRIPTION="Ming OS ${version} Home Edition"
LSBRELEASE

write_file /etc/issue <<ISSUE
Ming OS ${version} Home Edition - 层层精简，层层用心

ISSUE

write_file /etc/issue.net <<ISSUENET
Ming OS ${version} Home Edition
ISSUENET

echo "trixie/sid" > "${target}/etc/debian_version" 2>/dev/null || true
echo "ming-os" > "${target}/etc/hostname" 2>/dev/null || true
ln -sf /usr/share/zoneinfo/Asia/Shanghai "${target}/etc/localtime" 2>/dev/null || true
echo "Asia/Shanghai" > "${target}/etc/timezone" 2>/dev/null || true
if [[ -f "${target}/etc/hosts" ]]; then
    sed -i 's/[[:space:]]debian\\b/ ming-os/g; s/[[:space:]]debian$/ ming-os/' "${target}/etc/hosts" 2>/dev/null || true
fi

mkdir -p "${target}/etc/default"
cat > "${target}/etc/default/locale" <<'TARGETLOCALE'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_ALL=zh_CN.UTF-8
TARGETLOCALE

cat > "${target}/etc/locale.conf" <<'TARGETETCLOCALE'
LANG=zh_CN.UTF-8
LANGUAGE=zh_CN:zh
LC_ALL=zh_CN.UTF-8
TARGETETCLOCALE

cat > "${target}/etc/default/keyboard" <<'TARGETKEYBOARD'
XKBMODEL="pc105"
XKBLAYOUT="us"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
TARGETKEYBOARD

mkdir -p "${target}/etc/security"
cat > "${target}/etc/security/pwquality.conf" <<'TARGETPWQUALITY'
# Ming OS installer-friendly password policy.
minlen = 1
minclass = 0
maxrepeat = 0
maxclassrepeat = 0
dictcheck = 0
usercheck = 0
enforcing = 0
TARGETPWQUALITY

mkdir -p "${target}/etc/default/grub.d"
cat > "${target}/etc/default/grub.d/10-ming-os.cfg" <<GRUBCFG
GRUB_DISTRIBUTOR="Ming OS"
GRUB_THEME="/boot/grub/themes/ming/theme.txt"
# 老旧硬件友好 + 隐藏内核日志：安静启动、低日志级别、隐藏 systemd 状态刷屏
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUBCFG

mkdir -p "${target}/etc"
cat > "${target}/etc/machine-info" <<MACHINEINFO
PRETTY_HOSTNAME="Ming OS"
MACHINEINFO

mkdir -p "${target}/etc/lightdm/lightdm.conf.d"
cat > "${target}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf" <<LIGHTDM
[Seat:*]
autologin-user=user
autologin-user-timeout=0
user-session=xfce
LIGHTDM

if [[ -d "${target}/etc/sudoers.d" ]]; then
    echo "user ALL=(ALL) NOPASSWD: ALL" > "${target}/etc/sudoers.d/user" 2>/dev/null || true
    chmod 440 "${target}/etc/sudoers.d/user" 2>/dev/null || true
fi

# The installed system is produced by unpacking the Live filesystem, so remove
# Live-only installer launchers and sessions from the target before first boot.
rm -f \
    "${target}/usr/share/applications/calamares.desktop" \
    "${target}/usr/share/applications/calamares-install-debian.desktop" \
    "${target}/usr/share/xsessions/ming-installer.desktop" \
    "${target}/etc/systemd/system/ming-live-installer.service" \
    "${target}/etc/systemd/system/graphical.target.wants/ming-live-installer.service" \
    2>/dev/null || true

for installer_entry in \
    "${target}"/home/*/.config/autostart/calamares-live.desktop \
    "${target}"/home/*/Desktop/calamares.desktop \
    "${target}"/home/*/Desktop/install-debian.desktop \
    "${target}"/home/*/Desktop/"Install Debian.desktop" \
    "${target}"/home/*/Desktop/"安装 Debian.desktop" \
    "${target}"/etc/skel/.config/autostart/calamares-live.desktop \
    "${target}"/etc/skel/Desktop/calamares.desktop \
    "${target}"/etc/skel/Desktop/install-debian.desktop \
    "${target}"/etc/skel/Desktop/"Install Debian.desktop" \
    "${target}"/etc/skel/Desktop/"安装 Debian.desktop"; do
    [[ -e "${installer_entry}" ]] && rm -f "${installer_entry}" 2>/dev/null || true
done

if [[ -x "${target}/usr/sbin/update-grub" ]]; then
    chroot "${target}" /usr/sbin/update-grub >/tmp/ming-update-grub.log 2>&1 || true
elif [[ -x "${target}/usr/sbin/grub-mkconfig" ]]; then
    chroot "${target}" /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg >/tmp/ming-update-grub.log 2>&1 || true
fi

# 确保 NetworkManager 已启用，并强制加载常见老网卡驱动模块（Bug2: 安装后无网卡）
# i5-2430M 等 Sandy Bridge 机器常用 iwlwifi / Realtek r8169 / r8168
for svc in NetworkManager networking systemd-networkd; do
    if [ -f "${target}/usr/lib/systemd/system/${svc}.service" ] || \
       [ -f "${target}/lib/systemd/system/${svc}.service" ]; then
        chroot "${target}" systemctl enable "${svc}" 2>/dev/null || true
    fi
done
# 写 /etc/modules-load.d 确保常见网卡模块在下次开机自动加载
mkdir -p "${target}/etc/modules-load.d"
cat > "${target}/etc/modules-load.d/ming-network.conf" << 'NETMOD'
# Ming OS：确保常见老网卡驱动在开机时加载
r8169
r8168
iwlwifi
ath9k
ath10k_pci
rtl8192ee
rtl8188ee
brcmfmac
e1000e
NETMOD
# 确保固件被 initramfs 包含（update-initramfs 已在前面运行）
chroot "${target}" depmod -a 2>/dev/null || true
MINGIDENTITY
    chmod +x /usr/local/sbin/ming-fix-installed-identity

    cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'
#!/usr/bin/env bash
set -euo pipefail

LOG=/tmp/ming-installer/bootloader.log
mkdir -p /tmp/ming-installer
exec > >(tee -a "${LOG}") 2>&1

echo "==== Ming bootloader install $(date -Is) ===="
echo "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null || true

find_root() {
    local candidate
    for candidate in /tmp/calamares-root-* /target; do
        [ -d "${candidate}" ] || continue
        [ -d "${candidate}/boot" ] || continue
        [ -f "${candidate}/etc/fstab" ] || continue
        printf '%s\n' "${candidate}"
        return 0
    done
    return 1
}

root="$(find_root)"
echo "target_root=${root}"

root_source="$(findmnt -n -o SOURCE --target "${root}" 2>/dev/null || true)"
echo "root_source=${root_source}"
boot_disk=""
if [ -n "${root_source}" ] && [ -b "${root_source}" ]; then
    pkname="$(lsblk -no PKNAME "${root_source}" 2>/dev/null | head -n 1 || true)"
    if [ -n "${pkname}" ] && [ -b "/dev/${pkname}" ]; then
        boot_disk="/dev/${pkname}"
    fi
fi
if [ -z "${boot_disk}" ]; then
    boot_disk="$(lsblk -ndo NAME,TYPE,TRAN /dev/sd? /dev/vd? /dev/nvme?n? /dev/mmcblk? 2>/dev/null | awk '$2=="disk" && $3!="usb" {print "/dev/"$1; exit}')"
fi
if [ -z "${boot_disk}" ]; then
    boot_disk="$(lsblk -ndo NAME,TYPE /dev/sd? /dev/vd? /dev/nvme?n? /dev/mmcblk? 2>/dev/null | awk '$2=="disk"{print "/dev/"$1; exit}')"
fi
if [ -z "${boot_disk}" ] || [ ! -b "${boot_disk}" ]; then
    echo "ERROR: cannot find install disk for GRUB"
    exit 20
fi
echo "boot_disk=${boot_disk}"

for mountpoint in dev proc sys run; do
    mkdir -p "${root}/${mountpoint}"
done
mountpoint -q "${root}/dev" || mount --bind /dev "${root}/dev"
mountpoint -q "${root}/proc" || mount -t proc proc "${root}/proc"
mountpoint -q "${root}/sys" || mount -t sysfs sysfs "${root}/sys"
mountpoint -q "${root}/run" || mount --bind /run "${root}/run"

mkdir -p "${root}/boot/grub"

install_uefi_grub() {
    [ -d /sys/firmware/efi ] || return 1
    if ! findmnt --target "${root}/boot/efi" >/dev/null 2>&1; then
        echo "UEFI firmware detected but target /boot/efi is not mounted; falling back to BIOS GRUB"
        return 1
    fi
    mkdir -p "${root}/boot/efi/EFI/Ming" "${root}/boot/efi/EFI/BOOT"
    if [ -x "${root}/usr/sbin/grub-install" ]; then
        chroot "${root}" /usr/sbin/grub-install \
            --target=x86_64-efi \
            --efi-directory=/boot/efi \
            --bootloader-id="Ming OS" \
            --recheck \
            --removable
    elif command -v grub-install >/dev/null 2>&1; then
        grub-install \
            --target=x86_64-efi \
            --efi-directory="${root}/boot/efi" \
            --boot-directory="${root}/boot" \
            --bootloader-id="Ming OS" \
            --recheck \
            --removable \
            --no-nvram
    else
        return 1
    fi
    if [ -f "${root}/boot/efi/EFI/BOOT/BOOTX64.EFI" ]; then
        echo "UEFI fallback bootloader installed at /boot/efi/EFI/BOOT/BOOTX64.EFI"
        return 0
    fi
    echo "ERROR: UEFI grub-install finished without BOOTX64.EFI"
    return 2
}

install_bios_grub() {
    if command -v grub-install >/dev/null 2>&1; then
        grub-install --target=i386-pc --recheck --force \
            --boot-directory="${root}/boot" "${boot_disk}"
    elif [ -x "${root}/usr/sbin/grub-install" ]; then
        chroot "${root}" /usr/sbin/grub-install --target=i386-pc --recheck --force "${boot_disk}"
    else
        echo "ERROR: grub-install is missing in live and target environments"
        exit 21
    fi
}

if [ -d /sys/firmware/efi ]; then
    if install_uefi_grub; then
        echo "Ming UEFI bootloader path completed"
    else
        # UEFI 安装失败（无论是 ESP 未挂载还是 grub-install 出错），
        # 都降级到 BIOS GRUB——确保老主板/半残 UEFI 机器仍可引导
        echo "WARN: UEFI bootloader install failed; falling back to BIOS GRUB"
        install_bios_grub
        echo "Ming BIOS fallback bootloader path completed"
    fi
else
    install_bios_grub
    echo "Ming BIOS bootloader path completed"
fi

# 更新 GRUB 配置（失败不致命，grub.cfg 可能已由 Calamares grubcfg 模块写好）
if [ -x "${root}/usr/sbin/update-grub" ]; then
    chroot "${root}" /usr/sbin/update-grub 2>/tmp/ming-installer/update-grub.log || \
        echo "WARN: update-grub returned non-zero; check /tmp/ming-installer/update-grub.log"
elif [ -x "${root}/usr/sbin/grub-mkconfig" ]; then
    chroot "${root}" /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg \
        2>/tmp/ming-installer/update-grub.log || true
else
    grub-mkconfig -o "${root}/boot/grub/grub.cfg" 2>/dev/null || true
fi

# grub.cfg 存在即视为成功；空文件不阻断安装（Calamares grubcfg 模块会兜底）
if [ -s "${root}/boot/grub/grub.cfg" ]; then
    echo "grub.cfg OK: $(wc -l < "${root}/boot/grub/grub.cfg") lines"
else
    echo "WARN: grub.cfg is missing or empty after update-grub"
fi
echo "Ming bootloader install completed"
MINGBOOTLOADER
    chmod +x /usr/local/sbin/ming-install-bootloader

    # Debian's calamares-settings package brands the installer as Debian. Keep
    # the module sequence from the package, but override the visible branding
    # and add a final identity repair that runs on the installed target.
    mkdir -p /etc/calamares/branding/ming /etc/calamares/modules
    cat > /etc/calamares/branding/ming/branding.desc << BRANDING
---
componentName:  ming
strings:
    productName:         "Ming OS"
    shortProductName:    "Ming OS"
    version:             "${MING_OS_VERSION}"
    shortVersion:        "${MING_OS_VERSION}"
    versionedName:       "Ming OS ${MING_OS_VERSION}"
    shortVersionedName:  "Ming OS ${MING_OS_VERSION}"
    bootloaderEntryName: "Ming OS"
    productUrl:          "https://scallion.uno"
    supportUrl:          "https://scallion.uno/support"
    knownIssuesUrl:      "https://scallion.uno/bugs"
    releaseNotesUrl:     "https://scallion.uno"
style:
    sidebarBackground:    "#120820"
    sidebarText:          "#F4FFF9"
    sidebarTextSelect:    "#9FE7D7"
    sidebarTextHighlight: "#31C476"
images:
    productLogo:         "/usr/share/icons/hicolor/128x128/apps/ming-os-logo.svg"
    productIcon:         "/usr/share/icons/hicolor/128x128/apps/ming-os-logo.svg"
    productWelcome:      "/usr/share/backgrounds/ming-os/default.png"
slideshow:               "show.qml"
BRANDING

    cat > /etc/calamares/branding/ming/show.qml << 'SHOWQML'
import QtQuick 2.0;
Rectangle {
    color: "#120820"
    Text {
        anchors.centerIn: parent
        text: "Ming OS"
        color: "#F4FFF9"
        font.pixelSize: 42
        font.bold: true
    }
}
SHOWQML

    cat > /etc/calamares/modules/ming-identity.conf << IDENTITYCONF
---
dontChroot: true
timeout: 120
script:
  - "/usr/local/sbin/ming-fix-installed-identity"
IDENTITYCONF

    cat > /etc/calamares/modules/ming-bootloader.conf << BOOTLOADERCONF
---
dontChroot: true
timeout: 180
script:
  - "/usr/local/sbin/ming-install-bootloader"
BOOTLOADERCONF

    cat > /etc/calamares/modules/unpackfs.conf << 'UNPACKFSCONF'
---
unpack:
  # ming-calamares-preflight resolves the real live-boot path and exposes this
  # stable source before Calamares starts.
  - source: "/run/ming-installer/filesystem.squashfs"
    sourcefs: "squashfs"
    destination: ""
UNPACKFSCONF

    # Keep Calamares from falling back to distro defaults that may not match
    # the installer-only Ming OS image. VirtualBox testing exposed failures in
    # the partition step when the target disk had no usable label yet.
    cat > /etc/calamares/modules/partition.conf << 'PARTITIONCONF'
---
efiSystemPartition: "/boot/efi"
userSwapChoices:
  - none
  - small
  - suspend
  - file
drawNestedPartitions: true
alwaysShowPartitionLabels: true
defaultFileSystemType: "ext4"
availableFileSystemTypes:
  - "ext4"
  - "btrfs"
  - "xfs"
# 兼容老 BIOS 机器（i5-2430M 等 Sandy Bridge 时代）：
# erase 模式在 Legacy BIOS 下自动用 MBR，UEFI 下用 GPT。
# 不强制 GPT，避免老 BIOS 固件不认 GPT 磁盘。
initialPartitioningChoice: erase
initialSwapChoice: file
requiredStorage: 12
allowManualPartitioning: true
PARTITIONCONF

    cat > /etc/calamares/modules/mount.conf << 'MOUNTCONF'
---
extraMounts:
  - device: proc
    fs: proc
    mountPoint: /proc
  - device: sys
    fs: sysfs
    mountPoint: /sys
  - device: /dev
    fs: none
    mountPoint: /dev
    options: bind
  - device: /run
    fs: none
    mountPoint: /run
    options: bind
MOUNTCONF

    # Installer defaults for Chinese users. Keep the physical keyboard as US
    # layout for password safety; Fcitx5 provides Chinese Pinyin input after
    # login.
    cat > /etc/calamares/modules/locale.conf << 'LOCALECONF'
---
region: "Asia"
zone: "Shanghai"
locale: "zh_CN.UTF-8"
useSystemTimezone: true
adjustLiveTimezone: true
LOCALECONF

    cat > /etc/calamares/modules/keyboard.conf << 'KEYBOARDCONF'
---
model: "pc105"
layout: "us"
variant: ""
KEYBOARDCONF

    cat > /etc/calamares/modules/localecfg.conf << 'LOCALECFGCONF'
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

    cat > /etc/calamares/modules/users.conf << 'USERSCONF'
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

    cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'
---
modules-search: [ local, /usr/lib/x86_64-linux-gnu/calamares/modules ]
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
  - locale
  - keyboard
  - partition
  - users
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
CALAMARESSETTINGS

    mkdir -p /usr/share/applications
    cat > /usr/share/applications/calamares.desktop << 'CALAMARESDESKTOP'
[Desktop Entry]
Type=Application
Name=Install Ming OS
Name[zh_CN]=安装 Ming OS
Comment=Install Ming OS to this computer
Comment[zh_CN]=将 Ming OS 安装到这台电脑
Exec=/usr/local/bin/ming-calamares-launcher
Icon=calamares
Terminal=false
Categories=System;
StartupNotify=true
CALAMARESDESKTOP

    rm -f \
        /usr/share/applications/calamares-install-debian.desktop \
        /home/*/Desktop/calamares.desktop \
        /home/*/Desktop/install-debian.desktop \
        /home/*/Desktop/"Install Debian.desktop" \
        /etc/skel/Desktop/calamares.desktop \
        /etc/skel/Desktop/install-debian.desktop \
        /etc/skel/Desktop/"Install Debian.desktop" 2>/dev/null || true

    if [[ -f /usr/share/applications/calamares-install-debian.desktop ]]; then
        sed -i 's/^NoDisplay=.*/NoDisplay=true/; t; $aNoDisplay=true' /usr/share/applications/calamares-install-debian.desktop
    fi
}

# ======================== 系统优化 ========================

optimize_system() {
    # 安装 zram 工具
    apt install -y --no-install-recommends zram-tools

    # 配置 zram（内存压缩，提升低内存设备性能）
    cat > /etc/default/zramswap << ZRAMCFG
# Ming OS zram 配置
# 首次启动时 ming-memory-profile 会按真实内存重写此文件：
# <=2.6GB 使用 100% zram，<=4.2GB 使用 75%，更高内存使用 50%。
ALGO=zstd
PERCENT=50
PRIORITY=100
ZRAMCFG

    cat > /usr/local/bin/ming-memory-profile << 'MEMPROFILE'
#!/usr/bin/env bash
set -euo pipefail

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
profile="balanced"
zram_percent=50
swappiness=25
vfs_cache_pressure=80
dirty_ratio=12
dirty_background_ratio=4

if [[ "${mem_mb}" -le 2600 ]]; then
    profile="low-memory"
    zram_percent=100
    swappiness=80
    vfs_cache_pressure=120
    dirty_ratio=8
    dirty_background_ratio=2
elif [[ "${mem_mb}" -le 4200 ]]; then
    profile="compact"
    zram_percent=75
    swappiness=50
    vfs_cache_pressure=100
    dirty_ratio=10
    dirty_background_ratio=3
fi

cat > /etc/default/zramswap << ZRAMCFG
# Generated by ming-memory-profile
ALGO=zstd
PERCENT=${zram_percent}
PRIORITY=100
ZRAMCFG

cat > /etc/sysctl.d/99-ming-memory-runtime.conf << SYSCONF
# Generated by ming-memory-profile
vm.swappiness=${swappiness}
vm.vfs_cache_pressure=${vfs_cache_pressure}
vm.dirty_ratio=${dirty_ratio}
vm.dirty_background_ratio=${dirty_background_ratio}
vm.page-cluster=0
SYSCONF

sysctl -q -p /etc/sysctl.d/99-ming-memory-runtime.conf 2>/dev/null || true

mkdir -p /run/ming-os
cat > /run/ming-os/memory-profile << PROFILE
profile=${profile}
mem_mb=${mem_mb}
zram_percent=${zram_percent}
swappiness=${swappiness}
PROFILE
MEMPROFILE
    chmod +x /usr/local/bin/ming-memory-profile

    cat > /etc/systemd/system/ming-memory-profile.service << MEMSVC
[Unit]
Description=Ming OS runtime memory profile
DefaultDependencies=no
After=local-fs.target
Before=zramswap.service sysinit.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-memory-profile
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
MEMSVC

    # 系统内核参数优化
    cat > /etc/sysctl.d/99-ming-performance.conf << 'SYSCTLCONF'
# Ming OS 26.3.1 内核深度优化
# 目标：兼容 2GB+ RAM / 老 i3-i5-E3 / 老 AMD / 机械硬盘，同时保持桌面流畅

# ---- 内存：老机器优先减少换页 ----
vm.swappiness=10
vm.vfs_cache_pressure=60
vm.dirty_ratio=15
vm.dirty_background_ratio=5
vm.dirty_expire_centisecs=3000
vm.dirty_writeback_centisecs=1500
vm.page-cluster=0
vm.watermark_boost_factor=0
vm.watermark_scale_factor=125
# 禁止内核 OOM 过于激进地杀进程（桌面常驻应用保护）
vm.oom_kill_allocating_task=0
vm.overcommit_memory=0
vm.overcommit_ratio=50
# 最小空闲内存保留（单位 kB），防止老机器频繁触发回收
vm.min_free_kbytes=65536

# ---- 网络：BBR + 快速建连（弱网友好）----
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.core.somaxconn=4096
net.core.netdev_max_backlog=2048
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=20
net.ipv4.tcp_keepalive_time=300
net.ipv4.tcp_keepalive_probes=5
net.ipv4.tcp_keepalive_intvl=15
net.ipv4.tcp_mtu_probing=1
net.ipv4.tcp_slow_start_after_idle=0
net.ipv4.tcp_rmem=4096 87380 6291456
net.ipv4.tcp_wmem=4096 16384 4194304
# IPv6 隐私扩展（老机器无线网卡友好）
net.ipv6.conf.all.use_tempaddr=2

# ---- 文件系统 ----
fs.file-max=1048576
fs.nr_open=1048576
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=512

# ---- 内核调度：桌面响应优先（低延迟）----
# 降低调度延迟让桌面交互更跟手，适合老 CPU
kernel.sched_latency_ns=6000000
kernel.sched_min_granularity_ns=750000
kernel.sched_wakeup_granularity_ns=1500000
kernel.sched_migration_cost_ns=500000
# 禁用 NMI watchdog 减少 CPU 中断开销
kernel.nmi_watchdog=0
kernel.randomize_va_space=2
# 减少 printk 刷屏（老机器串口不快）
kernel.printk=3 4 1 3
SYSCTLCONF

    # 透明大页 → madvise（延迟到首次使用，避免 GC pause）
    mkdir -p /etc/tmpfiles.d
    cat > /etc/tmpfiles.d/ming-thp.conf << 'THPCONF'
w /sys/kernel/mm/transparent_hugepage/enabled - - - - madvise
w /sys/kernel/mm/transparent_hugepage/defrag  - - - - defer+madvise
THPCONF

    # /tmp 挂载到 tmpfs（减少机械硬盘随机写，老机器流畅感提升明显）
    # 上限 512MB，超大 tmp 操作自动溢出到磁盘
    cat >> /etc/fstab << 'TMPFSMOUNT'
tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=512M 0 0
TMPFSMOUNT

    # BBR 模块（多数 Debian 内核已内建，兜底加载）
    mkdir -p /etc/modules-load.d
    echo "tcp_bbr" > /etc/modules-load.d/ming-bbr.conf
    modprobe tcp_bbr 2>/dev/null || true

    # CPU 频率调节：老机器 ondemand，优先节能且响应快
    # cpufrequtils 不在 Live squashfs，走 udev 在启动时写 cpufreq governor
    cat > /etc/udev/rules.d/61-ming-cpufreq.rules << 'CPUFREQRULE'
# Ming OS：CPU 频率调节策略。老机器（<= 4核 <= Sandy Bridge）用 ondemand，
# 新机器用 schedutil（内核调度驱动）。两者均比 powersave 响应更快。
ACTION=="add", SUBSYSTEM=="cpu", KERNEL=="cpu[0-9]*", \
  RUN+="/bin/sh -c 'echo schedutil > /sys/devices/system/cpu/cpu%n/cpufreq/scaling_governor 2>/dev/null || echo ondemand > /sys/devices/system/cpu/cpu%n/cpufreq/scaling_governor 2>/dev/null || true'"
CPUFREQRULE

    # 应用 sysctl 配置
    sysctl --system

    # 限制日志大小，防止 /var/log 膨胀
    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/size-limit.conf << JOURNALCFG
[Journal]
SystemMaxUse=200M
SystemMaxFileSize=50M
Compress=yes
MaxRetentionSec=14day
JOURNALCFG

    # 禁用不必要的 tty（2-6），节省资源
    for i in 2 3 4 5 6; do
        if [[ -f "/etc/systemd/system/getty.target.wants/getty@tty${i}.service" ]]; then
            ln -sf /dev/null "/etc/systemd/system/getty@tty${i}.service"
        fi
    done

    # 启用串行控制台（用于虚拟机调试）
    systemctl enable serial-getty@ttyS0.service 2>/dev/null || true

    # 启用 zram 与低内存保护
    systemctl enable ming-memory-profile.service 2>/dev/null || true
    systemctl enable zramswap 2>/dev/null || true
    systemctl enable earlyoom 2>/dev/null || true
    systemctl enable irqbalance 2>/dev/null || true

    mkdir -p /etc/default
    cat > /etc/default/earlyoom << EARLYOOMCFG
EARLYOOM_ARGS="-m 4 -s 8 -r 60 --prefer '^(firefox|chromium|wps|wechat|code)$' --avoid '^(Xorg|xfce4-session|lightdm|NetworkManager)$'"
EARLYOOMCFG

    # 配置 I/O 调度器（针对 SSD 和 HDD 的优化）
    cat > /etc/udev/rules.d/60-ioscheduler.rules << IOSCHEDRULE
# Ming OS I/O 调度器配置
# SSD: 优先 none，回退 mq-deadline；HDD: 使用 mq-deadline，启动后由 ming-device-tune 优先尝试 bfq
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="none"
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="mq-deadline"
ACTION=="add|change", KERNEL=="mmcblk[0-9]*", ATTR{queue/scheduler}="mq-deadline"
IOSCHEDRULE

    cat > /usr/local/bin/ming-device-tune << 'DEVICETUNE'
#!/usr/bin/env bash
set -uo pipefail

log_dir="/run/ming-os"
mkdir -p "${log_dir}"
profile="${log_dir}/device-profile"
: > "${profile}"

has_hdd=0
has_ssd=0

for queue in /sys/block/*/queue; do
    dev="$(basename "$(dirname "${queue}")")"
    case "${dev}" in
        loop*|ram*|zram*|sr*) continue ;;
    esac

    rotational="$(cat "${queue}/rotational" 2>/dev/null || echo 0)"
    scheduler_file="${queue}/scheduler"
    read_ahead_file="${queue}/read_ahead_kb"
    nr_requests_file="${queue}/nr_requests"

    if [[ "${rotational}" == "1" ]]; then
        has_hdd=1
        if [[ -w "${scheduler_file}" ]]; then
            if grep -qw bfq "${scheduler_file}" 2>/dev/null; then
                echo bfq > "${scheduler_file}" 2>/dev/null || true
            elif grep -qw mq-deadline "${scheduler_file}" 2>/dev/null; then
                echo mq-deadline > "${scheduler_file}" 2>/dev/null || true
            fi
        fi
        [[ -w "${read_ahead_file}" ]] && echo 4096 > "${read_ahead_file}" 2>/dev/null || true
        [[ -w "${nr_requests_file}" ]] && echo 256 > "${nr_requests_file}" 2>/dev/null || true
        echo "${dev}=hdd" >> "${profile}"
    else
        has_ssd=1
        if [[ -w "${scheduler_file}" ]]; then
            if grep -qw none "${scheduler_file}" 2>/dev/null; then
                echo none > "${scheduler_file}" 2>/dev/null || true
            elif grep -qw mq-deadline "${scheduler_file}" 2>/dev/null; then
                echo mq-deadline > "${scheduler_file}" 2>/dev/null || true
            fi
        fi
        [[ -w "${read_ahead_file}" ]] && echo 1024 > "${read_ahead_file}" 2>/dev/null || true
        echo "${dev}=ssd" >> "${profile}"
    fi
done

if [[ "${has_hdd}" -eq 1 ]]; then
    cat > /etc/sysctl.d/98-ming-hdd-runtime.conf <<'HDDSYSCTL'
# Ming OS runtime HDD profile
vm.dirty_ratio=8
vm.dirty_background_ratio=2
vm.dirty_expire_centisecs=1000
vm.dirty_writeback_centisecs=300
HDDSYSCTL
    sysctl -q -p /etc/sysctl.d/98-ming-hdd-runtime.conf 2>/dev/null || true
else
    rm -f /etc/sysctl.d/98-ming-hdd-runtime.conf
fi

cpu_governor="schedutil"
if grep -qi "hypervisor" /proc/cpuinfo 2>/dev/null; then
    cpu_governor="ondemand"
fi
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [[ -w "${gov}" ]] || continue
    if grep -qw "${cpu_governor}" "$(dirname "${gov}")/scaling_available_governors" 2>/dev/null; then
        echo "${cpu_governor}" > "${gov}" 2>/dev/null || true
    fi
done

{
    echo "has_hdd=${has_hdd}"
    echo "has_ssd=${has_ssd}"
    echo "cpu_governor=${cpu_governor}"
} >> "${profile}"
DEVICETUNE
    chmod +x /usr/local/bin/ming-device-tune

    cat > /etc/systemd/system/ming-device-tune.service << DEVICETUNESVC
[Unit]
Description=Ming OS disk, CPU, and memory runtime tuning
After=local-fs.target systemd-udev-settle.service
Wants=systemd-udev-settle.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-device-tune
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
DEVICETUNESVC
    systemctl enable ming-device-tune.service 2>/dev/null || true

    # 配置 fstrim（SSD 定期 TRIM）
    cat > /etc/systemd/system/fstrim.timer << FSTRIMTIMER
[Unit]
Description=Discard unused blocks once a week
Documentation=man:fstrim

[Timer]
OnCalendar=weekly
AccuracySec=1h
Persistent=true

[Install]
WantedBy=timers.target
FSTRIMTIMER

    systemctl enable fstrim.timer 2>/dev/null || true

    # 不默认启用 preload：它会用空闲内存预读程序，对 2GB + 微信场景得不偿失。

    # ======================== 笔记本优化 ========================
    # 配置 TLP 电源管理
    systemctl enable tlp 2>/dev/null || true
    mkdir -p /etc/tlp.d
    cat > /etc/tlp.d/ming-laptop.conf << TLPCONF
# Ming OS 笔记本电池优化
CPU_SCALING_GOVERNOR_ON_AC=performance
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=balance_performance
CPU_ENERGY_PERF_POLICY_ON_BAT=power
PLATFORM_PROFILE_ON_AC=balanced
PLATFORM_PROFILE_ON_BAT=low-power
DISK_DEVICES="nvme0n1 sda"
DISK_APM_LEVEL_ON_AC="254"
DISK_APM_LEVEL_ON_BAT="128"
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=on
USB_AUTOSUSPEND=1
USB_BLACKLIST_BTUSB=1
USB_BLACKLIST_PRINTER=1
RUNTIME_PM_ON_AC=on
RUNTIME_PM_ON_BAT=auto
TLPCONF

    # systemd-logind 合盖行为（笔记本合盖不挂起，仅锁定屏幕）
    mkdir -p /etc/systemd/logind.conf.d
    cat > /etc/systemd/logind.conf.d/ming-lid.conf << LIDCONF
[Login]
HandleLidSwitch=lock
HandleLidSwitchExternalPower=lock
HandleLidSwitchDocked=ignore
LidSwitchIgnoreInhibited=yes
LIDCONF

    # 触摸板配置（点击即点击、双指滚动、自然滚动）
    mkdir -p /etc/X11/xorg.conf.d
    cat > /etc/X11/xorg.conf.d/40-touchpad.conf << TOUCHPADCONF
Section "InputClass"
    Identifier "Ming OS Touchpad"
    MatchIsTouchpad "on"
    Driver "libinput"
    Option "Tapping" "on"
    Option "TappingButtonMap" "lrm"
    Option "NaturalScrolling" "true"
    Option "ScrollMethod" "twofinger"
    Option "HorizontalScrolling" "true"
    Option "DisableWhileTyping" "true"
    Option "ClickMethod" "clickfinger"
    Option "MiddleEmulation" "true"
EndSection
TOUCHPADCONF

    # 触摸屏配置（小米平板一代 / Surface 等）：用 libinput 接管，启用点击/拖动，
    # 不做无效的右键长按映射（交给桌面手势）。配合 Onboard 虚拟键盘自动弹起。
    cat > /etc/X11/xorg.conf.d/41-touchscreen.conf << TOUCHSCREENCONF
Section "InputClass"
    Identifier "Ming OS Touchscreen"
    MatchIsTouchscreen "on"
    Driver "libinput"
    Option "Tapping" "on"
    Option "TapButton1" "1"
    Option "NaturalScrolling" "true"
EndSection
TOUCHSCREENCONF

    # ======================== 英特尔显卡优化配置 ========================
    # 关键：20-intel.conf 只能在确认有 Intel GPU 时才写入。
    # 若无条件写入 Driver "intel"，在 VirtualBox/QEMU/AMD 机器上 X 服务器会
    # 因找不到 intel DDX 驱动而加载失败，导致黑屏。
    # 通过 systemd tmpfiles + 开机脚本动态检测，只在 Intel GPU 机器上生效。
    cat > /usr/local/sbin/ming-intel-xorg-setup << 'INTELXORGSETUP'
#!/bin/sh
# 开机时检查是否有 Intel GPU，仅有 Intel 时写入 xorg.conf
XCONF="/etc/X11/xorg.conf.d/20-intel.conf"
vendor=$(cat /sys/class/drm/card0/device/vendor 2>/dev/null || echo "")
if [ "${vendor}" = "0x8086" ]; then
    mkdir -p /etc/X11/xorg.conf.d
    cat > "${XCONF}" << 'EOF'
Section "Device"
    Identifier  "Intel Graphics"
    Driver      "intel"
    Option      "TearFree"    "true"
    Option      "AccelMethod" "sna"
    Option      "DRI"         "3"
    Option      "TripleBuffer" "true"
EndSection
EOF
else
    rm -f "${XCONF}"
fi
INTELXORGSETUP
    chmod 0755 /usr/local/sbin/ming-intel-xorg-setup
    rm -f /etc/X11/xorg.conf.d/20-intel.conf

    cat > /etc/systemd/system/ming-intel-xorg.service << 'INTELXORGSVC'
[Unit]
Description=Ming OS Intel Xorg config (only on Intel GPU)
DefaultDependencies=no
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-intel-xorg-setup
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
INTELXORGSVC
    systemctl enable ming-intel-xorg.service 2>/dev/null || true

    # ACPI 守护进程（处理笔记本热键/电源按钮）
    systemctl enable acpid 2>/dev/null || true
}

# ======================== 开机加速（26.2.5） ========================

configure_boot_speed() {
    echo "配置开机加速..."

    # 蓝牙：等桌面就绪后再启动（延迟 5s），不卡 boot sequence
    mkdir -p /etc/systemd/system/bluetooth.service.d
    cat > /etc/systemd/system/bluetooth.service.d/delay.conf << 'EOF'
[Unit]
After=graphical.target
[Service]
ExecStartPre=/bin/sleep 5
EOF

    # 打印：改为 socket 按需激活，开机不自启
    systemctl disable cups 2>/dev/null || true
    systemctl disable cups-browsed 2>/dev/null || true

    # Avahi mDNS：延迟到桌面就绪后
    mkdir -p /etc/systemd/system/avahi-daemon.service.d
    cat > /etc/systemd/system/avahi-daemon.service.d/delay.conf << 'EOF'
[Unit]
After=graphical.target
EOF

    # Tracker 索引：全部屏蔽，用户搜索时不需要实时索引
    for svc in tracker-miner-fs-3.service tracker-extract-3.service tracker-writeback-3.service \
               tracker-miner-fs.service tracker-extract.service; do
        systemctl mask "${svc}" 2>/dev/null || true
    done

    # ModemManager：无 SIM 卡设备不需要
    systemctl disable ModemManager 2>/dev/null || true

    # 应用商店后台刷新：延迟 90s，不阻塞第一屏
    for svc in spark-store-refresh.service; do
        if [[ -f "/usr/lib/systemd/system/${svc}" ]] || [[ -f "/etc/systemd/system/${svc}" ]]; then
            mkdir -p "/etc/systemd/system/${svc}.d"
            printf '[Service]\nExecStartPre=/bin/sleep 90\n' > "/etc/systemd/system/${svc}.d/delay.conf"
        fi
    done

    # OTA 后台检查：延迟 120s
    mkdir -p /etc/systemd/system/ming-update-check.service.d
    printf '[Service]\nExecStartPre=/bin/sleep 120\n' \
        > /etc/systemd/system/ming-update-check.service.d/delay.conf

    # 缩短 systemd 启动/停止超时（默认 90s 太长）
    mkdir -p /etc/systemd/system.conf.d
    cat > /etc/systemd/system.conf.d/ming-timeouts.conf << 'EOF'
[Manager]
DefaultTimeoutStartSec=15s
DefaultTimeoutStopSec=10s
EOF

    # NetworkManager-wait-online：最多等 5s，避免无网络时卡启动
    mkdir -p /etc/systemd/system/NetworkManager-wait-online.service.d
    cat > /etc/systemd/system/NetworkManager-wait-online.service.d/timeout.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/nm-online -s -q --timeout=5
EOF

    echo "开机加速配置完成"
}

# ======================== 多盘合一 · 无感知存储 ========================
# 设计意图：
#   "数字难民" 用户不理解多分区/多硬盘概念。本功能在开机及插入新盘时自动把
#   检测到的额外数据盘（非系统盘、非可移动 U 盘）通过 fstab(UUID) 持久挂载，
#   再用 mount --bind 把其空间无缝映射到 /home/user 下的高频目录（Downloads
#   等），用户感知不到"第二块盘"。只读取/挂载已格式化分区，绝不自动格式化或
#   删除数据；首次绑定时用 rsync 把原目录内容迁移到数据盘，保证文件不丢。
configure_seamless_storage() {
    echo "配置无感知存储（多盘合一）..."

    cat > /usr/local/sbin/ming-storage-manager << 'STORAGEMGR'
#!/usr/bin/env bash
# Ming OS 无感知存储管理器：自动挂载额外数据盘并绑定到 Home 高频目录。
set -uo pipefail

MING_USER_NAME="user"
[[ -d /home/user ]] || MING_USER_NAME="$(awk -F: '$3>=1000 && $3<60000 && $1!="nobody"{print $1; exit}' /etc/passwd)"
[[ -n "${MING_USER_NAME}" ]] || exit 0
USER_HOME="/home/${MING_USER_NAME}"

POOL_ROOT="/mnt/ming-data"          # 数据盘挂载根
BIND_DIRS=("Downloads" "Documents" "Pictures" "Videos" "Music")
LOG="/run/ming-os/storage.log"
mkdir -p /run/ming-os "${POOL_ROOT}"
exec 9>>"${LOG}"
log() { echo "[$(date '+%F %T')] $*" >&9; }

# 系统盘（含 / 的物理磁盘）——绝不动它
root_src="$(findmnt -no SOURCE / 2>/dev/null)"
root_disk="$(lsblk -no PKNAME "${root_src}" 2>/dev/null | head -1)"
[[ -n "${root_disk}" ]] || root_disk="$(basename "$(readlink -f /sys/class/block/$(basename "${root_src}")/.. 2>/dev/null)" 2>/dev/null)"
log "root_src=${root_src} root_disk=${root_disk} user=${MING_USER_NAME}"

# 找候选数据分区：有文件系统、非系统盘、非可移动、非 swap、容量 >= 8GB
mapfile -t CANDIDATES < <(
    lsblk -rno NAME,TYPE,FSTYPE,RM,SIZE,MOUNTPOINT,PKNAME 2>/dev/null | \
    awk -v rootdisk="${root_disk}" '
        $2=="part" && $3!="" && $3!="swap" && $3!="crypto_LUKS" && $4=="0" && $6=="" && $7!=rootdisk {print $1":"$3}'
)
log "candidates=${CANDIDATES[*]:-none}"
[[ ${#CANDIDATES[@]} -gt 0 ]] || { log "无额外数据盘，退出"; exit 0; }

ensure_fstab() {  # $1=uuid $2=mntdir $3=fstype
    local uuid="$1" mnt="$2" fs="$3"
    grep -q "UUID=${uuid}" /etc/fstab 2>/dev/null && return 0
    echo "UUID=${uuid} ${mnt} ${fs} defaults,nofail,x-systemd.device-timeout=10 0 2" >> /etc/fstab
    log "fstab += UUID=${uuid} -> ${mnt}"
}

# 选最大的候选盘作为主数据盘
best=""; best_bytes=0
for entry in "${CANDIDATES[@]}"; do
    name="${entry%%:*}"; fs="${entry##*:}"
    dev="/dev/${name}"
    bytes="$(blockdev --getsize64 "${dev}" 2>/dev/null || echo 0)"
    if [[ "${bytes}" -gt "${best_bytes}" ]]; then best_bytes="${bytes}"; best="${dev}:${fs}"; fi
done
[[ -n "${best}" ]] || exit 0
data_dev="${best%%:*}"; data_fs="${best##*:}"
data_uuid="$(blkid -s UUID -o value "${data_dev}" 2>/dev/null)"
[[ -n "${data_uuid}" ]] || { log "无 UUID，放弃 ${data_dev}"; exit 0; }

mnt="${POOL_ROOT}/$(basename "${data_dev}")"
mkdir -p "${mnt}"
ensure_fstab "${data_uuid}" "${mnt}" "${data_fs}"
mountpoint -q "${mnt}" || mount "${mnt}" 2>/dev/null || mount "${data_dev}" "${mnt}" 2>/dev/null || { log "挂载失败 ${data_dev}"; exit 0; }
log "已挂载 ${data_dev} -> ${mnt}"
STORAGEMGR

    # 第二段：把数据盘空间无缝绑定到 Home 高频目录（rsync 迁移 + mount --bind）
    cat >> /usr/local/sbin/ming-storage-manager << 'STORAGEMGR2'

# 在数据盘上为每个高频目录建一个承载目录，首次绑定时迁移原内容
for d in "${BIND_DIRS[@]}"; do
    src_home="${USER_HOME}/${d}"
    pool_dir="${mnt}/${d}"
    mkdir -p "${src_home}" "${pool_dir}"
    chown "${MING_USER_NAME}:${MING_USER_NAME}" "${pool_dir}" 2>/dev/null || true

    # 已经绑定则跳过（幂等）
    if findmnt -rno TARGET "${src_home}" 2>/dev/null | grep -qx "${src_home}"; then
        log "${src_home} 已绑定，跳过"
        continue
    fi

    # 首次：把家目录里已有文件迁移到数据盘承载目录（保留属性，不删源直至成功）
    if [[ -n "$(ls -A "${src_home}" 2>/dev/null)" ]]; then
        if rsync -aXS --ignore-existing "${src_home}/" "${pool_dir}/" >>"${LOG}" 2>&1; then
            log "迁移 ${src_home} -> ${pool_dir} 完成"
        else
            log "迁移失败，跳过绑定 ${src_home}（保护用户数据）"
            continue
        fi
    fi

    # 持久化 bind（fstab）+ 立即生效
    grep -q " ${src_home} none bind" /etc/fstab 2>/dev/null || \
        echo "${pool_dir} ${src_home} none bind,nofail,x-systemd.requires=${mnt} 0 0" >> /etc/fstab
    if mount --bind "${pool_dir}" "${src_home}" 2>>"${LOG}"; then
        chown "${MING_USER_NAME}:${MING_USER_NAME}" "${src_home}" 2>/dev/null || true
        log "bind ${pool_dir} -> ${src_home} 生效"
    else
        log "bind 失败 ${src_home}"
    fi
done

# 记录合并后的可用空间，供设置中心"存储可视化"读取
{
    echo "data_device=${data_dev}"
    echo "data_mount=${mnt}"
    df -B1 --output=size,used,avail "${mnt}" 2>/dev/null | tail -1 | awk '{print "pool_size="$1"\npool_used="$2"\npool_avail="$3}'
} > /run/ming-os/storage-info 2>/dev/null || true
log "存储管理完成"
exit 0
STORAGEMGR2
    chmod 0755 /usr/local/sbin/ming-storage-manager

    # systemd 服务：开机后、用户登录前完成绑定
    cat > /etc/systemd/system/ming-storage.service << 'STORAGESVC'
[Unit]
Description=Ming OS seamless multi-disk storage (auto-mount + bind to Home)
After=local-fs.target
Before=lightdm.service display-manager.service
ConditionPathExists=/usr/local/sbin/ming-storage-manager

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-storage-manager
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
STORAGESVC
    systemctl enable ming-storage.service 2>/dev/null || true

    # udev 热插拔：插入新盘后触发一次
    cat > /etc/udev/rules.d/99-ming-storage.rules << 'STORAGEUDEV'
ACTION=="add", SUBSYSTEM=="block", KERNEL=="sd[a-z][0-9]|nvme[0-9]n[0-9]p[0-9]|mmcblk[0-9]p[0-9]", ENV{ID_FS_TYPE}!="", RUN+="/bin/systemctl start --no-block ming-storage.service"
STORAGEUDEV

    echo "无感知存储配置完成"
}

# ======================== 主流程 ========================

main() {
    echo "=====> [01_base] 开始基础系统配置 <====="

    configure_apt_sources
    install_base_packages
    install_hardware_support_packages
    configure_installer_password_policy
    configure_locale
    configure_timezone
    configure_keyboard
    configure_users
    configure_network
    deploy_hardware_diagnostics
    configure_os_identity
    configure_installer_identity
    optimize_system
    configure_seamless_storage
    configure_boot_speed

    echo "=====> [01_base] 基础系统配置完成 <====="
}

main
