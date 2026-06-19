#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 01: 基础系统配置
# ============================================================================
# 设计意图：
#   在 debootstrap 生成的最小系统上，配置 APT 源、安装核心系统组件、
#   设置语言/时区/用户/网络等基础环境，为后续模块提供可运行的基础系统。
#
# 输入：
#   环境变量: ONION_OS_VERSION, ONION_USER, ONION_USER_PASS, ROOT_PASS
#   （由主构建脚本通过 chroot_exec 注入）
#
# 输出：
#   配置完成的 chroot 根文件系统
#
# 关键步骤：
#   1. 配置清华 TUNA APT 源
#   2. 安装 Linux 内核、systemd、基础工具
#   3. 配置语言环境 (zh_CN.UTF-8) 与时区 (Asia/Shanghai)
#   4. 创建默认用户 onion 并配置 sudo
#   5. 安装 NetworkManager 与基础网络工具
#   6. 配置系统标识为 Onion OS
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

    apt update
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
        calamares \
        calamares-settings-debian \
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

    # 固件包（部分可能在 Trixie 镜像同步期不可用，失败不中断）
    apt install -y --no-install-recommends \
        firmware-linux-nonfree \
        firmware-misc-nonfree \
        firmware-iwlwifi \
        firmware-realtek \
        firmware-atheros \
        firmware-brcm80211 \
        intel-microcode \
        amd64-microcode \
        || true

    echo "loop" >> /etc/modules
    echo "iwlmvm" >> /etc/modules || true
    echo "iwlwifi" >> /etc/modules || true

    mkdir -p /etc/modprobe.d
    cat > /etc/modprobe.d/onion-blacklist.conf << BLACKLIST
blacklist i2c_piix4
BLACKLIST
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

    # 创建默认用户 onion
    useradd -m -s /bin/bash -c "Onion OS User" "${ONION_USER}"
    echo "${ONION_USER}:${ONION_USER_PASS}" | chpasswd

    # 创建必要的组（如果不存在）
    for grp in lpadmin plugdev nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    done

    # 将 onion 用户加入必要组（逐个添加，跳过不存在的组）
    for grp in sudo adm cdrom dip plugdev lpadmin netdev audio video input scanner bluetooth nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 && usermod -aG "${grp}" "${ONION_USER}" || true
    done

    # 配置 sudo 免密（方便初学者，避免频繁输入密码）
    echo "${ONION_USER} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/"${ONION_USER}"
    chmod 440 /etc/sudoers.d/"${ONION_USER}"

    # 创建用户桌面等 XDG 目录
    sudo -u "${ONION_USER}" mkdir -p \
        "/home/${ONION_USER}/Desktop" \
        "/home/${ONION_USER}/Documents" \
        "/home/${ONION_USER}/Downloads" \
        "/home/${ONION_USER}/Music" \
        "/home/${ONION_USER}/Pictures" \
        "/home/${ONION_USER}/Videos"
}

# ======================== 网络管理 ========================

configure_network() {
    apt install -y --no-install-recommends \
        network-manager \
        network-manager-gnome \
        iwd \
        ifupdown

    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/wifi-backend.conf << NMWIFICFG
[device]
wifi.backend=iwd
wifi.iwd.autoconnect=yes
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

    systemctl enable iwd 2>/dev/null || true

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

    cat > /etc/systemd/system/onion-rfkill.service << RFKILLSVC
[Unit]
Description=Onion OS RF Kill Unblock
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock all
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RFKILLSVC
    systemctl enable onion-rfkill.service 2>/dev/null || true

    mkdir -p /etc/systemd/system/NetworkManager-wait-online.service.d
    cat > /etc/systemd/system/NetworkManager-wait-online.service.d/override.conf << NMONLINE
[Service]
ExecStart=
ExecStart=/usr/bin/nm-online -s -q -t 60
NMONLINE

    echo "onion-os" > /etc/hostname

    cat > /etc/hosts << HOSTSCFG
127.0.0.1       localhost
127.0.1.1       onion-os
::1             localhost ip6-localhost ip6-loopback
ff02::1         ip6-allnodes
ff02::2         ip6-allrouters
HOSTSCFG
}

# ======================== 系统标识 ========================

configure_os_identity() {
    # 设置 Onion OS 品牌标识
    cat > /etc/os-release << OSRELEASE
NAME="Onion OS"
VERSION="${ONION_OS_VERSION} Home Edition"
ID=onion-os
ID_LIKE=debian
PRETTY_NAME="Onion OS ${ONION_OS_VERSION} Home Edition"
VERSION_ID="${ONION_OS_VERSION}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=onion
DEBIAN_CODENAME=trixie
OSRELEASE

    # 更新 issue 文件（控制台登录提示）
    cat > /etc/issue << ISSUE
Onion OS ${ONION_OS_VERSION} Home Edition - 层层精简，层层用心

ISSUE

    cat > /etc/issue.net << ISSUENET
Onion OS ${ONION_OS_VERSION} Home Edition
ISSUENET

    # 自定义 lsb_release 信息
    apt install -y --no-install-recommends lsb-release
    mkdir -p /etc/lsb-release.d
    cat > /etc/lsb-release << LSBRELEASE
DISTRIB_ID=OnionOS
DISTRIB_RELEASE=${ONION_OS_VERSION}
DISTRIB_CODENAME=onion
DISTRIB_DESCRIPTION="Onion OS ${ONION_OS_VERSION} Home Edition"
LSBRELEASE

    # 确保 /etc/debian_version 显示 Debian 13 (Trixie)，而非历史遗留的12
    echo "trixie/sid" > /etc/debian_version
}

# ======================== 系统优化 ========================

optimize_system() {
    # 安装 zram 工具
    apt install -y --no-install-recommends zram-tools

    # 配置 zram（内存压缩，提升低内存设备性能）
    cat > /etc/default/zramswap << ZRAMCFG
# Onion OS zram 配置
# 首次启动时 onion-memory-profile 会按真实内存重写此文件：
# <=2.6GB 使用 100% zram，<=4.2GB 使用 75%，更高内存使用 50%。
ALGO=zstd
PERCENT=50
PRIORITY=100
ZRAMCFG

    cat > /usr/local/bin/onion-memory-profile << 'MEMPROFILE'
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
# Generated by onion-memory-profile
ALGO=zstd
PERCENT=${zram_percent}
PRIORITY=100
ZRAMCFG

cat > /etc/sysctl.d/99-onion-memory-runtime.conf << SYSCONF
# Generated by onion-memory-profile
vm.swappiness=${swappiness}
vm.vfs_cache_pressure=${vfs_cache_pressure}
vm.dirty_ratio=${dirty_ratio}
vm.dirty_background_ratio=${dirty_background_ratio}
vm.page-cluster=0
SYSCONF

sysctl -q -p /etc/sysctl.d/99-onion-memory-runtime.conf 2>/dev/null || true

mkdir -p /run/onion-os
cat > /run/onion-os/memory-profile << PROFILE
profile=${profile}
mem_mb=${mem_mb}
zram_percent=${zram_percent}
swappiness=${swappiness}
PROFILE
MEMPROFILE
    chmod +x /usr/local/bin/onion-memory-profile

    cat > /etc/systemd/system/onion-memory-profile.service << MEMSVC
[Unit]
Description=Onion OS runtime memory profile
DefaultDependencies=no
After=local-fs.target
Before=zramswap.service sysinit.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/onion-memory-profile
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
MEMSVC

    # 系统内核参数优化
    cat > /etc/sysctl.d/99-onion-performance.conf << SYSCTLCONF
# Onion OS 性能优化配置
# 内存管理
vm.swappiness=15
vm.vfs_cache_pressure=80
vm.dirty_ratio=12
vm.dirty_background_ratio=4
vm.page-cluster=0
vm.watermark_boost_factor=0
vm.watermark_scale_factor=125

# 网络优化
net.core.somaxconn=65535
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15
net.ipv4.tcp_keepalive_time=300
net.ipv4.tcp_keepalive_probes=5
net.ipv4.tcp_keepalive_intvl=15

# 文件系统优化
fs.file-max=2097152
fs.nr_open=2097152

# 内核调度优化
kernel.sched_latency_ns=1000000
kernel.sched_min_granularity_ns=100000
kernel.sched_wakeup_granularity_ns=50000
SYSCTLCONF

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
    systemctl enable onion-memory-profile.service 2>/dev/null || true
    systemctl enable zramswap 2>/dev/null || true
    systemctl enable earlyoom 2>/dev/null || true
    systemctl enable irqbalance 2>/dev/null || true

    mkdir -p /etc/default
    cat > /etc/default/earlyoom << EARLYOOMCFG
EARLYOOM_ARGS="-m 4 -s 8 -r 60 --prefer '^(firefox|chromium|wps|wechat|code)$' --avoid '^(Xorg|xfce4-session|lightdm|NetworkManager)$'"
EARLYOOMCFG

    # 配置 I/O 调度器（针对 SSD 和 HDD 的优化）
    cat > /etc/udev/rules.d/60-ioscheduler.rules << IOSCHEDRULE
# Onion OS I/O 调度器配置
# SSD: 使用 mq-deadline 或 none
# HDD: 使用 mq-deadline
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"
ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="mq-deadline"
ACTION=="add|change", KERNEL=="mmcblk[0-9]*", ATTR{queue/scheduler}="mq-deadline"
IOSCHEDRULE

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
    cat > /etc/tlp.d/onion-laptop.conf << TLPCONF
# Onion OS 笔记本电池优化
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
    cat > /etc/systemd/logind.conf.d/onion-lid.conf << LIDCONF
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
    Identifier "Onion OS Touchpad"
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
    mkdir -p /etc/systemd/system/onion-update-check.service.d
    printf '[Service]\nExecStartPre=/bin/sleep 120\n' \
        > /etc/systemd/system/onion-update-check.service.d/delay.conf

    # 缩短 systemd 启动/停止超时（默认 90s 太长）
    mkdir -p /etc/systemd/system.conf.d
    cat > /etc/systemd/system.conf.d/onion-timeouts.conf << 'EOF'
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

# ======================== 主流程 ========================

main() {
    echo "=====> [01_base] 开始基础系统配置 <====="

    configure_apt_sources
    install_base_packages
    configure_locale
    configure_timezone
    configure_keyboard
    configure_users
    configure_network
    configure_os_identity
    optimize_system
    configure_boot_speed

    echo "=====> [01_base] 基础系统配置完成 <====="
}

main
