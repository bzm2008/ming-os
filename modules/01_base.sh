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
#   1. 配置 Debian 官方 APT 源
#   2. 安装 Linux 内核、systemd、基础工具
#   3. 配置语言环境 (zh_CN.UTF-8) 与时区 (Asia/Shanghai)
#   4. 创建默认用户 ming 并配置 sudo
#   5. 安装 NetworkManager 与基础网络工具
#   6. 配置系统标识为 Ming OS
# ============================================================================

set -uo pipefail

# ======================== APT 源配置 ========================

configure_apt_sources() {
    # Use Debian's official CDN by default. A single regional mirror returning
    # 403 must not make the installed system unable to receive patch updates.
    local debian_mirror="${MING_DEBIAN_MIRROR:-https://deb.debian.org/debian/}"
    local security_mirror="${MING_DEBIAN_SECURITY_MIRROR:-https://security.debian.org/debian-security}"
    install -d -m 0755 /etc/apt/sources.list.d
    cat > /etc/apt/sources.list.d/90-ming-mirror.list << APTSRC
# Managed by Ming OS. Use ming-apt-source-select to change this block.
deb ${debian_mirror} trixie main contrib non-free non-free-firmware
deb ${debian_mirror} trixie-updates main contrib non-free non-free-firmware
deb ${security_mirror} trixie-security main contrib non-free non-free-firmware
APTSRC
    cat > /etc/apt/sources.list << 'APTSRCROOT'
# Debian base repositories are managed in sources.list.d/90-ming-mirror.list.
# User-added repositories in this file are never rewritten by Ming tools.
APTSRCROOT

    cat > /etc/apt/apt.conf.d/99ming-network << 'APTNETWORK'
Acquire::Retries "5";
Acquire::ForceIPv4 "true";
Acquire::http::Timeout "15";
Acquire::https::Timeout "15";
Acquire::http::Pipeline-Depth "0";
Acquire::Queue-Mode "access";
Acquire::http::No-Cache "true";
Acquire::https::No-Cache "true";
APTNETWORK

    # Host dependency installation may use its own skip flag to avoid a
    # redundant host apt update. Do not let that leak into the target rootfs:
    # after rewriting sources.list, chroot apt must refresh so contrib,
    # non-free and non-free-firmware packages are visible.
    if [[ "${MING_SKIP_CHROOT_APT_UPDATE:-0}" != "1" ]]; then
        apt update
    fi
}

deploy_apt_source_selector() {
    cat > /usr/local/sbin/ming-apt-source-select << 'MINGAPTSOURCE'
#!/usr/bin/env bash
set -uo pipefail

suite="${MING_APT_SUITE:-trixie}"
state_dir=/var/lib/ming-os
state_file="${state_dir}/apt-source-state.json"
source_file=/etc/apt/sources.list.d/90-ming-mirror.list
legacy_source=/etc/apt/sources.list
official=https://deb.debian.org/debian
security=https://security.debian.org/debian-security
timeout_seconds="${MING_APT_MIRROR_TIMEOUT:-4}"
candidates=(
    "${official}|${security}"
    "https://mirrors.aliyun.com/debian|https://mirrors.aliyun.com/debian-security"
    "https://mirrors.tuna.tsinghua.edu.cn/debian|https://mirrors.tuna.tsinghua.edu.cn/debian-security"
    "https://mirrors.ustc.edu.cn/debian|https://mirrors.ustc.edu.cn/debian-security"
)

mkdir -p "${state_dir}" /etc/apt/sources.list.d
backup="${state_dir}/apt-source-backup.$$"
install -d -m 0700 "${backup}"
cp -a "${source_file}" "${backup}/90-ming-mirror.list" 2>/dev/null || true
cp -a "${legacy_source}" "${backup}/sources.list" 2>/dev/null || true

rollback() {
    if [[ -e "${backup}/90-ming-mirror.list" ]]; then
        cp -a "${backup}/90-ming-mirror.list" "${source_file}"
    else
        rm -f "${source_file}"
    fi
    if [[ -e "${backup}/sources.list" ]]; then
        cp -a "${backup}/sources.list" "${legacy_source}"
    fi
    rm -rf "${backup}"
}

# RC3 and early RC4 wrote exactly three Debian lines to sources.list without
# a marker. Migrate only that known template; leave every customised file
# untouched so Ming never takes ownership of a user's repository choices.
if [[ -f "${legacy_source}" ]] \
   && [[ "$(grep -Ec '^[[:space:]]*deb[[:space:]]+https://(deb|security)[.]debian[.]org/' "${legacy_source}" 2>/dev/null || true)" == 3 ]] \
   && [[ "$(grep -Ev '^[[:space:]]*(#.*)?$|^[[:space:]]*deb[[:space:]]+https://(deb|security)[.]debian[.]org/' "${legacy_source}" 2>/dev/null | wc -l)" == 0 ]]; then
    cat > "${legacy_source}" << 'LEGACYMIGRATED'
# Debian base repositories are managed in sources.list.d/90-ming-mirror.list.
# User-added repositories in this file are never rewritten by Ming tools.
LEGACYMIGRATED
fi

best=""
best_ms=999999
for candidate in "${candidates[@]}"; do
    debian="${candidate%%|*}"
    security_url="${candidate##*|}"
    started=$(date +%s%3N 2>/dev/null || date +%s000)
    if curl -4 -fsSL --max-time "${timeout_seconds}" -o /dev/null \
        "${debian}/dists/${suite}/InRelease" \
        && curl -4 -fsSL --max-time "${timeout_seconds}" -o /dev/null \
        "${security_url}/dists/${suite}-security/InRelease"; then
        ended=$(date +%s%3N 2>/dev/null || date +%s000)
        elapsed=$((ended - started))
        if (( elapsed < best_ms )); then
            best="${candidate}"
            best_ms="${elapsed}"
        fi
    fi
done

if [[ -z "${best}" ]]; then
    best="${official}|${security}"
    best_ms=-1
fi
debian="${best%%|*}"
security_url="${best##*|}"
tmp="${source_file}.tmp.$$"
cat > "${tmp}" << EOF
# Managed by Ming OS. Use ming-apt-source-select to change this block.
deb ${debian} ${suite} main contrib non-free non-free-firmware
deb ${debian} ${suite}-updates main contrib non-free non-free-firmware
deb ${security_url} ${suite}-security main contrib non-free non-free-firmware
EOF
if ! mv -f "${tmp}" "${source_file}"; then
    rm -f "${tmp}"
    rollback
    exit 1
fi
if ! apt-get update -o Acquire::Retries=2 -o Acquire::http::Timeout=15 \
        -o Acquire::https::Timeout=15 >/tmp/ming-apt-source-select.log 2>&1; then
    rollback
    echo "镜像验证失败，已 rollback 到原软件源。" >&2
    exit 1
fi
python3 - "${state_file}" "${debian}" "${security_url}" "${best_ms}" <<'PY'
import json, sys
from datetime import datetime, timezone
path, debian, security, elapsed = sys.argv[1:]
with open(path, "w", encoding="utf-8") as stream:
    json.dump({"schema": "ming.apt-source.v1", "debian": debian,
               "security": security, "latency_ms": int(elapsed),
               "selected_at": datetime.now(timezone.utc).isoformat()}, stream)
    stream.write("\n")
PY
rm -rf "${backup}"
printf '%s\n' "${debian}"
MINGAPTSOURCE
    chmod 0755 /usr/local/sbin/ming-apt-source-select
}

deploy_apt_upgrade_convergence() {
    cat > /usr/local/sbin/ming-apt-upgrade-converge << 'MINGAPTCONVERGE'
#!/usr/bin/env bash
set -uo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
log=/var/log/ming-apt-upgrade-converge.log
exec >>"${log}" 2>&1
dpkg --configure -a || exit 1
apt-get -f install -y -o Dpkg::Use-Pty=0 || exit 1
if [[ "${MING_SKIP_FULL_UPGRADE:-0}" != "1" ]]; then
    apt-get full-upgrade -y -o Dpkg::Use-Pty=0 -o APT::Install-Recommends=false || exit 1
fi
dpkg --audit | tee /var/log/ming-apt-audit.log
[[ ! -s /var/log/ming-apt-audit.log ]]
MINGAPTCONVERGE
    chmod 0755 /usr/local/sbin/ming-apt-upgrade-converge
}

# ======================== 内核与基础包 ========================

install_base_packages() {
    # 安装 Linux 内核及核心系统组件（必须成功）
    # Keep package names limited to packages present in the selected Debian
    # suite; AppStream metadata is refreshed separately when available.
    if ! apt install -y --no-install-recommends \
        linux-image-amd64 \
        linux-headers-amd64 \
        dkms \
        systemd \
        systemd-sysv \
        dbus \
        dbus-user-session \
        dbus-x11 \
        libpam-systemd \
        at-spi2-core \
        sudo \
        apt-utils \
        appstream \
        python3-yaml \
        gnupg2 \
        ca-certificates \
        curl \
        wget \
        jq \
        locales \
        im-config \
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
        grub-efi-amd64-signed \
        shim-signed \
        efibootmgr \
        eject \
        wmctrl \
        e2fsprogs \
        dosfstools \
        gdisk \
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
        network-manager-openvpn \
        network-manager-openvpn-gnome \
        mobile-broadband-provider-info \
        modemmanager \
        wireless-tools \
        iw \
        rfkill \
        wpasupplicant \
        acpi \
        acpid \
        acpi-support \
        laptop-detect \
        powertop \
        mbpfan \
        smartmontools \
        earlyoom \
        irqbalance \
        tlp \
        tlp-rdw \
        alsa-ucm-conf \
        xserver-xorg-video-modesetting \
        xserver-xorg-input-all \
        xserver-xorg-input-libinput \
        xserver-xorg-input-synaptics \
        libwacom-common \
        libwacom-bin \
        iio-sensor-proxy \
        i965-va-driver \
        intel-media-va-driver \
        libgl1-mesa-dri \
        mesa-va-drivers \
        mesa-vdpau-drivers \
        mesa-vulkan-drivers \
        mesa-utils \
        vainfo \
        lm-sensors \
        firmware-amd-graphics \
        amd64-microcode \
        mokutil \
        thermald; then
        echo "[ERROR] 基础软件或 Linux 内核安装失败，停止构建。" >&2
        return 1
    fi

    # A successful apt transaction must leave a bootable kernel in the
    # target.  This explicit read-back prevents errexit suppression when the
    # function is called from `install_base_packages || return 1`.
    if ! dpkg-query -W -f='${db:Status-Status}' linux-image-amd64 2>/dev/null \
        | grep -Fxq installed \
        || ! find /boot -maxdepth 1 -type f -name 'vmlinuz-*' -size +0c \
            | grep -q . \
        || ! find /lib/modules -mindepth 1 -maxdepth 1 -type d \
            | grep -q .; then
        echo "[ERROR] Linux 内核安装后校验失败，/boot 或 /lib/modules 不完整。" >&2
        return 1
    fi

    # Build the local AppStream index when the package is available.  The store
    # can still use its curated fallback if a mirror has no metadata.
    if command -v appstreamcli >/dev/null 2>&1; then
        appstreamcli refresh-cache --force >/var/log/ming-appstream-refresh.log 2>&1 \
            || appstreamcli refresh --force >>/var/log/ming-appstream-refresh.log 2>&1 \
            || true
    fi

    # These contrib installers download firmware from GitHub in postinst and can
    # hang an otherwise reproducible ISO build. Clean leftovers from resumed
    # chroots and keep them out of the default image.
    dpkg --purge --force-all firmware-b43-installer firmware-b43legacy-installer \
        >/dev/null 2>&1 || true

    # These packages cover the radio stacks shipped by Ming OS. They are
    # mandatory: a missing regulatory database or Bluetooth/Wi-Fi firmware is
    # a build error, not an optional hardware enhancement.
    install_required_radio_firmware || return 1

    # Firmware and microcode packaging shifts across Debian snapshots. Install
    # what exists without letting renamed packages break the whole base system.
    for pkg in \
        firmware-linux \
        firmware-linux-free \
        firmware-linux-nonfree \
        firmware-misc-nonfree \
        firmware-ath9k-htc \
        b43-fwcutter \
        firmware-sof-signed \
        firmware-intel-graphics \
        firmware-nvidia-graphics \
        firmware-ti-connectivity \
        intel-microcode \
        systemd-oomd; do
        apt install -y --no-install-recommends "${pkg}" || true
    done

    # Older resume builds may already contain wl. Keep the default image on
    # in-tree drivers and retain STA only as a verified offline fallback.
    if dpkg-query -W -f='${Status}' broadcom-sta-dkms 2>/dev/null | grep -Fq 'install ok installed'; then
        apt purge -y broadcom-sta-dkms || return 1
    fi
    cache_broadcom_sta_driver || return 1
    deploy_broadcom_driver_manager || return 1

    cat > /etc/modules-load.d/ming-hardware.conf << 'HWLOAD'
# Keep systemd-modules-load conservative. Broad hardware probing is handled by
# ming-hardware-preload.service so missing model-specific modules never fail boot.
loop
HWLOAD

    cat > /usr/local/sbin/ming-hardware-preload << 'HWPRELOAD'
#!/usr/bin/env bash
set -u

LOG=/tmp/ming-hardware-preload.log
PRELOAD_TOTAL_BUDGET_SECONDS=6
critical_modules=(
usbhid
i2c_hid
hid_multitouch
bcm5974
hid_apple
applespi
intel_lpss
intel_lpss_pci
surface_aggregator
surface_hid_core
)
late_modules=(
    btusb btintel btrtl btbcm ath3k applesmc apple_gmux
    spi_pxa2xx_platform spi_pxa2xx_pci
    thinkpad_acpi ideapad_laptop huawei_wmi k10temp
)
modules=("${critical_modules[@]}")
[[ "${1:-}" == "--late" ]] && modules=("${late_modules[@]}")

mkdir -p /tmp
preload_deadline=$((SECONDS + PRELOAD_TOTAL_BUDGET_SECONDS))
for module in "${modules[@]}"; do
    if (( SECONDS >= preload_deadline )); then
        printf '%s preload deadline reached before %s\n' "$(date '+%F %T')" "${module}" >> "${LOG}" 2>/dev/null || true
        break
    fi
    if timeout --foreground 2s modprobe -q "${module}" 2>/dev/null; then
        printf '%s loaded %s\n' "$(date '+%F %T')" "${module}" >> "${LOG}" 2>/dev/null || true
    else
        printf '%s skipped/timeout %s\n' "$(date '+%F %T')" "${module}" >> "${LOG}" 2>/dev/null || true
    fi
done

if (( SECONDS < preload_deadline )); then
    {
        printf '%s Broadcom devices and kernel bindings\n' "$(date '+%F %T')"
        timeout --foreground 1s lspci -Dnnk -d 14e4: 2>/dev/null || true
    } >> "${LOG}" 2>/dev/null || true
fi
{
    printf '%s wireless interfaces\n' "$(date '+%F %T')"
    for wireless_path in /sys/class/net/*/wireless; do
        [[ -d "${wireless_path}" ]] && printf '%s\n' "${wireless_path%/wireless}"
    done
} >> "${LOG}" 2>/dev/null || true
exit 0
HWPRELOAD
    chmod 0755 /usr/local/sbin/ming-hardware-preload

    cat > /etc/systemd/system/ming-hardware-preload.service << 'HWPRELOADSVC'
[Unit]
Description=Ming OS priority hardware module preload
ConditionKernelCommandLine=!boot=live
After=local-fs.target systemd-modules-load.service
Before=NetworkManager.service bluetooth.service display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-hardware-preload
TimeoutStartSec=8s
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
HWPRELOADSVC
    systemctl enable ming-hardware-preload.service 2>/dev/null || true

    cat > /etc/systemd/system/ming-hardware-preload-late.service << 'HWPRELOADLATESVC'
[Unit]
Description=Ming OS non-critical hardware module preload
ConditionKernelCommandLine=!boot=live
After=display-manager.service
Wants=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-hardware-preload --late
RemainAfterExit=yes

[Install]
WantedBy=graphical.target
HWPRELOADLATESVC
    systemctl enable ming-hardware-preload-late.service 2>/dev/null || true

    # i2c_piix4 is the in-tree SMBus driver used by many old AMD chipsets.
    # Never blacklist it globally: that hides temperature and power sensors.
    rm -f /etc/modprobe.d/ming-blacklist.conf

    mkdir -p /etc/modprobe.d
    cat > /etc/modprobe.d/ming-old-hardware.conf << 'OLDHWMOD'
# Make Broadcom/Intel/Realtek era laptops less fragile on first boot.  Wi-Fi
# power saving remains under NetworkManager/TLP control so it cannot disable a
# working radio or make suspend/resume unreliable.
options iwlwifi bt_coex_active=1 11n_disable=8
options iwlmvm power_scheme=1
options psmouse synaptics_intertouch=0
options snd_hda_intel power_save=0
OLDHWMOD

}

install_required_radio_firmware() {
    local package
    local required_packages=(
        wireless-regdb
        bluez-firmware
        firmware-mediatek
        firmware-libertas
        firmware-misc-nonfree
        firmware-iwlwifi
        firmware-realtek
        firmware-atheros
        firmware-brcm80211
    )

    apt install -y --no-install-recommends "${required_packages[@]}" || return 1
    for package in "${required_packages[@]}"; do
        if ! dpkg-query -W -f='${Status}' "${package}" 2>/dev/null \
            | grep -Fq 'install ok installed'; then
            echo "[ERROR] required radio firmware package is not installed: ${package}" >&2
            return 1
        fi
    done
}

install_required_wifi_firmware() {
    install_required_radio_firmware
}

cache_broadcom_sta_driver() {
    local cache_dir="/usr/share/ming-os/driver-cache/broadcom"
    local extract_dir
    local debs=()

    install -d -m 0755 "${cache_dir}"
    rm -f "${cache_dir}"/broadcom-sta-dkms_*.deb \
        "${cache_dir}/broadcom-sta.ids" \
        "${cache_dir}/SHA256SUMS"

    if ! (cd "${cache_dir}" && apt-get download broadcom-sta-dkms); then
        echo "[ERROR] 无法从当前 Debian 仓库缓存 broadcom-sta-dkms" >&2
        return 1
    fi

    shopt -s nullglob
    debs=("${cache_dir}"/broadcom-sta-dkms_*.deb)
    shopt -u nullglob
    if [[ "${#debs[@]}" -ne 1 || ! -s "${debs[0]:-}" ]]; then
        echo "[ERROR] Broadcom STA 缓存必须且只能包含一个有效 deb" >&2
        return 1
    fi

    extract_dir=$(mktemp -d)
    if ! dpkg-deb -x "${debs[0]}" "${extract_dir}"; then
        rm -rf "${extract_dir}"
        return 1
    fi
    if [[ ! -s "${extract_dir}/usr/share/broadcom-sta/broadcom-sta.ids" ]]; then
        echo "[ERROR] broadcom-sta-dkms 包内缺少设备 ID 清单" >&2
        rm -rf "${extract_dir}"
        return 1
    fi
    install -m 0644 \
        "${extract_dir}/usr/share/broadcom-sta/broadcom-sta.ids" \
        "${cache_dir}/broadcom-sta.ids"
    rm -rf "${extract_dir}"

    (
        cd "${cache_dir}"
        sha256sum "$(basename "${debs[0]}")" broadcom-sta.ids > SHA256SUMS
        sha256sum -c SHA256SUMS
    ) || return 1
}

configure_macbook_input_modules() {
    local module
    local initrd
    local initrd_modules
    local kernel_version
    local module_file

    kernel_version=$(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
        | sort -V | tail -1)
    if [[ -z "${kernel_version}" ]]; then
        echo "[WARN] 未发现已安装内核，跳过 MacBook initramfs 模块配置" >&2
        return 0
    fi

    install -d -m 0755 /etc/initramfs-tools
    touch /etc/initramfs-tools/modules
    for module in applespi spi_pxa2xx_platform intel_lpss_pci; do
        if modinfo -k "${kernel_version}" "${module}" >/dev/null 2>&1; then
            grep -Fxq "${module}" /etc/initramfs-tools/modules 2>/dev/null \
                || printf '%s\n' "${module}" >> /etc/initramfs-tools/modules
        else
            echo "[WARN] 目标内核 ${kernel_version} 不提供 MacBook 模块 ${module}，跳过 initramfs 固化" >&2
        fi
    done

    update-initramfs -u -k all || return 1
    initrd=$(find /boot -maxdepth 1 -type f -name 'initrd.img-*' -print 2>/dev/null \
        | sort -V | tail -1)
    if [[ -z "${initrd}" || ! -s "${initrd}" ]]; then
        echo "[ERROR] 无法验证 MacBook initramfs 模块" >&2
        return 1
    fi
    if ! initrd_modules="$(lsinitramfs "${initrd}" 2>/dev/null)"; then
        echo "[ERROR] 无法读取 MacBook initramfs 模块清单" >&2
        return 1
    fi
    for module_file in applespi spi-pxa2xx-platform intel-lpss-pci; do
        if ! grep -Eq "/${module_file}\.ko(\.|$)" <<< "${initrd_modules}"; then
            if modinfo -k "${kernel_version}" "${module_file//-/_}" >/dev/null 2>&1; then
                echo "[ERROR] ${module_file} 未进入 ${initrd}" >&2
                return 1
            fi
            echo "[WARN] ${module_file} 未出现在 ${initrd}，目标内核不提供该模块" >&2
        fi
    done
}

deploy_broadcom_driver_manager() {
    cat > /usr/local/sbin/ming-broadcom-driver << 'BROADCOMDRIVER'
#!/usr/bin/env bash
set -uo pipefail

CACHE_DIR=/usr/share/ming-os/driver-cache/broadcom
IDS_FILE="${CACHE_DIR}/broadcom-sta.ids"
SUMS_FILE="${CACHE_DIR}/SHA256SUMS"
LOG=/var/log/ming-broadcom-driver.log

usage() {
    echo "Usage: ming-broadcom-driver status --json | install | restore" >&2
}

bool_json() {
    [[ "$1" == "true" ]] && printf true || printf false
}

secure_boot_state() {
    if [[ ! -d /sys/firmware/efi ]]; then
        printf disabled
    elif ! command -v mokutil >/dev/null 2>&1; then
        printf unknown
    elif mokutil --sb-state 2>/dev/null | grep -qi enabled; then
        printf enabled
    elif mokutil --sb-state 2>/dev/null | grep -qi disabled; then
        printf disabled
    else
        printf unknown
    fi
}

load_state() {
    broadcom_line=$(lspci -Dnn -d 14e4: 2>/dev/null \
        | grep -Ei 'network controller|ethernet controller|wireless' \
        | head -1 || true)
    detected=false
    supported=false
    wifi_present=false
    pci_id=""
    pci_slot=""
    model=""
    active_module="none"
    sta_installed=false
    secure_boot=$(secure_boot_state)

    if [[ -n "${broadcom_line}" ]]; then
        detected=true
        pci_slot=$(awk '{print $1}' <<< "${broadcom_line}")
        pci_id=$(grep -oE '\[14e4:[[:xdigit:]]{4}\]' <<< "${broadcom_line}" \
            | tail -1 | tr -d '[]:' | tr '[:upper:]' '[:lower:]')
        model=$(sed -E 's/^[^ ]+[[:space:]]+//' <<< "${broadcom_line}")
        active_module=$(lspci -Dnnk -s "${pci_slot}" 2>/dev/null \
            | awk -F': ' '/Kernel driver in use:/ {print $2; exit}')
        active_module=${active_module:-none}
        if [[ -n "${pci_id}" && -s "${IDS_FILE}" ]] \
            && grep -Ev '^[[:space:]]*(#|$)' "${IDS_FILE}" \
                | tr '[:upper:]' '[:lower:]' | grep -Fxq "${pci_id}"; then
            supported=true
        fi
    fi

    for wireless_path in /sys/class/net/*/wireless; do
        if [[ -d "${wireless_path}" ]]; then
            wifi_present=true
            break
        fi
    done

    if dpkg-query -W -f='${Status}' broadcom-sta-dkms 2>/dev/null \
        | grep -Fq 'install ok installed'; then
        sta_installed=true
    fi

    action=none
    if [[ "${sta_installed}" == true ]]; then
        action=restore
    elif [[ "${supported}" != true ]]; then
        action=unsupported
    elif [[ "${wifi_present}" == true ]]; then
        action=none
    elif [[ "${secure_boot}" != disabled ]]; then
        action=blocked_secure_boot
    else
        action=install
    fi
}

print_status() {
    local json=${1:-false}
    load_state
    if [[ "${json}" == true ]]; then
        jq -n \
            --argjson detected "$(bool_json "${detected}")" \
            --argjson supported "$(bool_json "${supported}")" \
            --argjson wifi_present "$(bool_json "${wifi_present}")" \
            --arg active_module "${active_module}" \
            --arg secure_boot "${secure_boot}" \
            --argjson sta_installed "$(bool_json "${sta_installed}")" \
            --arg action "${action}" \
            --arg pci_id "${pci_id}" \
            --arg model "${model}" \
            '{detected:$detected,supported:$supported,wifi_present:$wifi_present,
              active_module:$active_module,secure_boot:$secure_boot,
              sta_installed:$sta_installed,action:$action,pci_id:$pci_id,model:$model}'
    else
        printf 'detected=%s\nsupported=%s\nwifi_present=%s\nactive_module=%s\nsecure_boot=%s\nsta_installed=%s\naction=%s\npci_id=%s\nmodel=%s\n' \
            "${detected}" "${supported}" "${wifi_present}" "${active_module}" \
            "${secure_boot}" "${sta_installed}" "${action}" "${pci_id}" "${model}"
    fi
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "此操作需要通过系统授权运行。" >&2
        exit 1
    fi
    touch "${LOG}" && chmod 0600 "${LOG}"
    exec > >(tee -a "${LOG}") 2>&1
    printf '\n[%s] ming-broadcom-driver %s\n' "$(date '+%F %T')" "$*"
}

verify_cache() {
    [[ -s "${IDS_FILE}" && -s "${SUMS_FILE}" ]] || return 1
    (cd "${CACHE_DIR}" && sha256sum -c SHA256SUMS)
}

rollback_sta() {
    dpkg --purge broadcom-sta-dkms >/dev/null 2>&1 || true
    rm -f /etc/modprobe.d/broadcom-sta-dkms.conf \
        /etc/modprobe.d/broadcom-sta.conf 2>/dev/null || true
    depmod -a 2>/dev/null || true
}

install_sta() {
    local debs=()
    require_root install
    load_state
    if [[ "${supported}" != true || "${wifi_present}" == true ]]; then
        echo "当前硬件不适用 Broadcom STA，或开源驱动已经提供无线接口。" >&2
        exit 2
    fi
    if [[ "${secure_boot}" != disabled ]]; then
        echo "Secure Boot 已开启或状态未知，不能加载未注册 MOK 的 DKMS 模块。" >&2
        exit 3
    fi
    if ! verify_cache; then
        echo "Broadcom 离线驱动缓存校验失败。" >&2
        exit 4
    fi

    shopt -s nullglob
    debs=("${CACHE_DIR}"/broadcom-sta-dkms_*.deb)
    shopt -u nullglob
    if [[ "${#debs[@]}" -ne 1 ]]; then
        echo "Broadcom 离线驱动包数量异常。" >&2
        exit 4
    fi

    if ! DEBIAN_FRONTEND=noninteractive dpkg -i "${debs[0]}"; then
        rollback_sta
        echo "Broadcom STA 安装失败，已恢复开源驱动配置。" >&2
        exit 5
    fi
    if ! dkms status 2>/dev/null | grep -Fq 'broadcom-sta/' \
        || ! modinfo wl >/dev/null 2>&1; then
        rollback_sta
        echo "Broadcom STA DKMS 未能为当前内核生成 wl 模块，已回滚。" >&2
        exit 5
    fi
    if ! update-initramfs -u -k all; then
        rollback_sta
        update-initramfs -u -k all >/dev/null 2>&1 || true
        echo "更新 initramfs 失败，Broadcom STA 已回滚。" >&2
        exit 5
    fi
    echo "Broadcom 兼容驱动已安装。请重启电脑后检查 Wi-Fi。"
}

restore_open_drivers() {
    require_root restore
    rollback_sta
    if ! update-initramfs -u -k all; then
        echo "恢复开源驱动后更新 initramfs 失败，请查看 ${LOG}。" >&2
        exit 5
    fi
    echo "已恢复内核开源 Broadcom 驱动配置。请重启电脑。"
}

case "${1:-status}" in
    status)
        [[ "${2:-}" == "--json" ]] && print_status true || print_status false
        ;;
    install)
        install_sta
        ;;
    restore)
        restore_open_drivers
        ;;
    *)
        usage
        exit 64
        ;;
esac
BROADCOMDRIVER
    chmod 0755 /usr/local/sbin/ming-broadcom-driver
}

configure_macbook_fan_and_disk_health() {
    cat > /usr/local/sbin/ming-is-intel-mac << 'MACDETECT'
#!/usr/bin/env bash
set -u
identity="$(cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name 2>/dev/null || true)"
grep -Eiq 'Apple|MacBook|Macmini|iMac|MacPro' <<< "${identity}"
MACDETECT
    chmod 0755 /usr/local/sbin/ming-is-intel-mac

    install -d -m 0755 /etc/systemd/system/mbpfan.service.d
    cat > /etc/systemd/system/mbpfan.service.d/ming-hardware-guard.conf << 'MBPFANGUARD'
[Unit]
Description=MacBook fan control (Apple hardware only)

[Service]
ExecCondition=/usr/local/sbin/ming-is-intel-mac
ExecStartPre=/sbin/modprobe coretemp
ExecStartPre=/sbin/modprobe applesmc
MBPFANGUARD
    systemctl enable mbpfan.service 2>/dev/null || true

    # SMART checks are user-triggered. Do not keep old HDDs awake with a
    # permanent monitoring daemon.
    systemctl disable smartmontools.service smartd.service 2>/dev/null || true
    cat > /usr/local/bin/ming-disk-health << 'DISKHEALTH'
#!/usr/bin/env bash
set -uo pipefail

LOG=/tmp/ming-disk-health.log
if [[ "${EUID}" -ne 0 ]]; then
    echo "此检查需要系统授权。" >&2
    exit 1
fi

: > "${LOG}"
chmod 0644 "${LOG}"
{
    echo "Ming OS 磁盘健康检查"
    date
    echo
    lsblk -o NAME,TYPE,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS 2>/dev/null || true
    echo
} >> "${LOG}"

found=false
while read -r name type; do
    [[ "${type}" == disk ]] || continue
    case "${name}" in
        loop*|ram*|zram*|sr*) continue ;;
    esac
    found=true
    device="/dev/${name}"
    {
        echo "===== ${device} ====="
        timeout 15 smartctl -H -A -l error "${device}" 2>&1 || true
        echo
    } >> "${LOG}"
done < <(lsblk -dn -o NAME,TYPE 2>/dev/null)

if [[ "${found}" != true ]]; then
    echo "未发现可读取 SMART 的物理磁盘。" >> "${LOG}"
fi

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Ming OS 磁盘健康" --width=900 --height=680 \
        --filename="${LOG}" 2>/dev/null || true
else
    cat "${LOG}"
fi
DISKHEALTH
    chmod 0755 /usr/local/bin/ming-disk-health
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

    # Printing is socket activated.  Discovery/scanning daemons remain
    # installed for the Settings repair flow, but never join the boot path.
    systemctl enable cups.socket 2>/dev/null || true
    systemctl disable --now cups.service cups-browsed.service \
        avahi-daemon.service saned.service saned.socket 2>/dev/null || true
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
    # 默认设置东八区，联网后 systemd-timesyncd 会自动同步精确时间
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
    echo "Asia/Shanghai" > /etc/timezone
    dpkg-reconfigure -f noninteractive tzdata

    # 启用 NTP 时间自动同步（联网后自动更新，用户无需手动设置时间）
    mkdir -p /etc/systemd/timesyncd.conf.d
    cat > /etc/systemd/timesyncd.conf.d/ming-ntp.conf << 'NTPCFG'
[Time]
NTP=ntp.aliyun.com ntp1.aliyun.com ntp2.aliyun.com cn.pool.ntp.org
FallbackNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org
NTPCFG
    systemctl enable systemd-timesyncd 2>/dev/null || true
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
    # Never publish a factory password. Root is locked and the desktop user
    # starts passwordless until the user explicitly configures one in OOBE.
    passwd -l root

    # 创建默认用户 ming
    useradd -m -s /bin/bash -c "Ming OS User" "${MING_USER}"
    passwd -d "${MING_USER}"

    # 创建必要的组（如果不存在）
    for grp in lpadmin plugdev netdev nopasswdlogin autologin render; do
        getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    done

    # 将 ming 用户加入必要组（逐个添加，跳过不存在的组）
    for grp in adm cdrom dip plugdev lpadmin netdev audio video render input scanner bluetooth nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 && usermod -aG "${grp}" "${MING_USER}" || true
    done
    gpasswd -d "${MING_USER}" sudo >/dev/null 2>&1 || true

    # Keep graphical auto-login separate from administrator authority.  A
    # passwordless sudo rule turns every desktop process into root, so desktop
    # maintenance actions cross a named Polkit boundary instead.
    rm -f /etc/sudoers.d/"${MING_USER}" /etc/sudoers.d/user

    install -d -m 0755 /usr/local/sbin /usr/share/polkit-1/actions
    install -m 0755 /tmp/ming-build/assets/ming-account-control.py /usr/local/sbin/ming-account-control
    install -m 0755 /tmp/ming-build/assets/ming-admin-bootstrap.py /usr/local/sbin/ming-admin-bootstrap
    cat > /usr/share/polkit-1/actions/org.ming.account.control.policy << 'ACCOUNT_CONTROL_POLICY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.ming.account.control">
    <description>Manage the current Ming OS account password</description>
    <message>Authentication is required to manage the current account password.</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/ming-account-control</annotate>
  </action>
</policyconfig>
ACCOUNT_CONTROL_POLICY

    cat > /usr/share/polkit-1/actions/org.ming.account.bootstrap.policy << 'ACCOUNT_BOOTSTRAP_POLICY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <action id="org.ming.account.bootstrap">
    <description>Initialize the first local Ming OS administrator</description>
    <message>Create the local administrator password to continue.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/ming-admin-bootstrap</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
ACCOUNT_BOOTSTRAP_POLICY


    cat > /usr/local/sbin/ming-timeshift-restore << 'TIMESHIFT_RESTORE_HELPER'
#!/usr/bin/env bash
set -u

if [[ "${EUID}" -ne 0 ]]; then
    echo "ming-timeshift-restore requires Polkit authorization" >&2
    exit 3
fi
command -v timeshift >/dev/null 2>&1 || {
    echo "timeshift is not installed" >&2
    exit 127
}

snapshot="$(timeshift --list --scripted 2>/dev/null \
    | awk '/ming-factory|O / {print $3; exit}')"
if [[ -n "${snapshot}" ]]; then
    exec timeshift --restore --snapshot "${snapshot}" --yes
fi
exec timeshift --restore --yes
TIMESHIFT_RESTORE_HELPER
    chmod 0755 /usr/local/sbin/ming-timeshift-restore

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
        bluez \
        ifupdown

    apt install -y --no-install-recommends iwd || true

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

    # Keep the default backend deterministic for older Wi-Fi hardware. iwd is
    # installed as an opt-in alternative, but must never run with wpa_supplicant.
    systemctl disable --now iwd.service 2>/dev/null || true
    systemctl enable --now wpa_supplicant.service 2>/dev/null || true

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

    # Joining a Wi-Fi network is an ordinary desktop action.  Permit only the
    # active local netdev user to control NetworkManager's radio and connection
    # state; driver repair and system-wide configuration still require admin.
    mkdir -p /etc/polkit-1/rules.d
    cat > /etc/polkit-1/rules.d/50-ming-network.rules << 'MINGNETWORKPOLICY'
polkit.addRule(function(action, subject) {
    var allowed = [
        "org.freedesktop.NetworkManager.network-control",
        "org.freedesktop.NetworkManager.enable-disable-wifi",
        "org.freedesktop.NetworkManager.enable-disable-network"
    ];
    if (subject.active && subject.local && subject.isInGroup("netdev")
            && allowed.indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
MINGNETWORKPOLICY
    chmod 0644 /etc/polkit-1/rules.d/50-ming-network.rules

    systemctl enable NetworkManager 2>/dev/null || true
    # ModemManager is installed for WWAN/USB modem compatibility but is
    # enabled by ming-service-profile only when hardware or explicit opt-in is
    # detected.  Ordinary Wi-Fi/ethernet machines pay no modem startup cost.

    cat > /etc/systemd/system/ming-regdom.service << 'REGDOMSVC'
[Unit]
Description=Ming OS CN wireless regulatory domain
After=systemd-modules-load.service
Before=NetworkManager.service

[Service]
Type=oneshot
# iw may report no phy on wired-only machines. The setting remains harmless and
# must never make a no-Wi-Fi boot fail.
ExecStart=-/usr/sbin/iw reg set CN
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
REGDOMSVC
    systemctl enable ming-regdom.service 2>/dev/null || true

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
After=NetworkManager.service
Before=graphical.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock all
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RFKILLSVC
    systemctl enable ming-rfkill.service 2>/dev/null || true

    mkdir -p /etc/systemd/system/bluetooth.service.d
    cat > /etc/systemd/system/bluetooth.service.d/ming-radio-unblock.conf << BTUNBLOCK
[Service]
ExecStartPre=-/usr/sbin/rfkill unblock bluetooth
ExecStartPost=-/bin/sh -c 'command -v btmgmt >/dev/null 2>&1 && btmgmt power on || true'
BTUNBLOCK

    if dpkg-query -W -f='${db:Status-Abbrev}' bluez 2>/dev/null | grep -qx 'ii '; then
        systemctl enable bluetooth.service 2>/dev/null || true
    else
        echo "[WARN] bluez is unavailable; bluetooth.service will not be enabled" >&2
    fi

    mkdir -p /etc/bluetooth
    cat > /etc/bluetooth/main.conf << BTCFG
[General]
Name = Ming OS
ControllerMode = dual
FastConnectable = true
DiscoverableTimeout = 0
PairableTimeout = 0

[Policy]
AutoEnable=true
BTCFG

    # Network availability must never delay the graphical session.  Time
    # synchronisation is retried by NetworkManager events after a connection
    # is actually usable; remove old resume-build drop-ins that waited here.
    systemctl disable --now NetworkManager-wait-online.service 2>/dev/null || true
    rm -rf /etc/systemd/system/NetworkManager-wait-online.service.d

    echo "ming-os" > /etc/hostname

    cat > /etc/hosts << HOSTSCFG
127.0.0.1       localhost
127.0.1.1       ming-os
::1             localhost ip6-localhost ip6-loopback
ff02::1         ip6-allnodes
ff02::2         ip6-allrouters
HOSTSCFG
}

deploy_service_profile() {
    cat > /etc/default/ming-os << 'MINGOSDEFAULT'
# Ming OS runtime profile switches.  Hardware-aware defaults keep optional
# daemons out of the graphical boot path; set values to 1 for diagnostics.
MING_KEEP_MODEMMANAGER=0
MING_DEBUG_SERIAL=0
MING_PHONE_DESKTOP=1
MINGOSDEFAULT

    cat > /usr/local/sbin/ming-service-profile << 'MINGSERVICEPROFILE'
#!/usr/bin/env bash
# Apply hardware-aware system service policy without waiting for the network.
set -uo pipefail

LOG=/var/log/ming-service-profile.log
CONFIG=/etc/default/ming-os
MODE=${1:-apply}

log() {
    mkdir -p "$(dirname "${LOG}")" 2>/dev/null || true
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${LOG}" 2>/dev/null || true
}

load_config() {
    local explicit_keep="${MING_KEEP_MODEMMANAGER-}"
    local explicit_debug="${MING_DEBUG_SERIAL-}"
    MING_KEEP_MODEMMANAGER=0
    MING_DEBUG_SERIAL=0
    if [[ -r "${CONFIG}" ]]; then
        # shellcheck disable=SC1091
        . "${CONFIG}"
    fi
    [[ -n "${explicit_keep}" ]] && MING_KEEP_MODEMMANAGER="${explicit_keep}"
    [[ -n "${explicit_debug}" ]] && MING_DEBUG_SERIAL="${explicit_debug}"
}

wwan_present() {
    local path
    shopt -s nullglob
    for path in /dev/cdc-wdm* /sys/class/net/wwan* /sys/class/net/*/wwan /sys/class/net/*/device/wwan*; do
        [[ -e "${path}" ]] && { shopt -u nullglob; return 0; }
    done
    shopt -u nullglob
    if command -v nmcli >/dev/null 2>&1 \
        && timeout --foreground 2s nmcli -t -f TYPE device status 2>/dev/null \
            | grep -Eiq 'gsm|wwan'; then
        return 0
    fi
    if command -v lspci >/dev/null 2>&1 \
        && timeout --foreground 2s lspci -nn 2>/dev/null \
            | grep -Eiq 'wwan|mobile broadband|cellular|modem'; then
        return 0
    fi
    if command -v lsusb >/dev/null 2>&1 \
        && timeout --foreground 2s lsusb 2>/dev/null \
            | grep -Eiq 'wwan|mobile broadband|cellular|modem'; then
        return 0
    fi
    return 1
}

bool_json() { [[ "$1" == true ]] && printf true || printf false; }

apply_optional_services() {
    local modem=false
    if [[ "${MING_KEEP_MODEMMANAGER}" == 1 ]] || wwan_present; then
        modem=true
        systemctl enable --now ModemManager.service 2>/dev/null || log "ModemManager enable/start failed"
    else
        systemctl disable --now ModemManager.service 2>/dev/null || true
    fi

    # CUPS stays available through its socket; browsing, mDNS and scanner
    # daemons are explicitly on-demand so they cannot slow graphical login.
    systemctl enable cups.socket 2>/dev/null || true
    systemctl disable --now cups.service cups-browsed.service \
        avahi-daemon.service saned.service saned.socket 2>/dev/null || true

    if [[ "${MING_DEBUG_SERIAL}" == 1 ]]; then
        systemctl enable --now serial-getty@ttyS0.service 2>/dev/null || \
            log "serial debug getty requested but unavailable"
    else
        systemctl disable --now serial-getty@ttyS0.service 2>/dev/null || true
    fi

    # Keep one owner for each graphical component.  Recovery is delegated to
    # the existing watchdogs; this helper only records duplicate processes.
    local user=${MING_SESSION_USER:-${SUDO_USER:-user}}
    local process count
    for process in xfce4-panel picom plank; do
        count=$(pgrep -u "${user}" -x "${process}" 2>/dev/null | wc -l | tr -d ' ')
        [[ "${count:-0}" -le 1 ]] || log "duplicate ${process} processes detected: ${count}"
    done
    printf '%s\n' "${modem}"
}

status_json() {
    load_config
    local modem_present=false modem_active=false serial_enabled=false
    wwan_present && modem_present=true
    systemctl is-active --quiet ModemManager.service 2>/dev/null && modem_active=true
    systemctl is-enabled --quiet serial-getty@ttyS0.service 2>/dev/null && serial_enabled=true
    jq -n \
        --argjson modem_present "$(bool_json "${modem_present}")" \
        --argjson modem_active "$(bool_json "${modem_active}")" \
        --argjson keep_modem "$(bool_json "${MING_KEEP_MODEMMANAGER}")" \
        --argjson serial_enabled "$(bool_json "${serial_enabled}")" \
        '{schema_version:1, modem:{hardware_present:$modem_present,active:$modem_active,explicit_opt_in:$keep_modem}, serial_getty:{enabled:$serial_enabled}, optional_services:{cups_socket:true, cups:false, avahi:false, saned:false}}'
}

load_config
case "${MODE}" in
    apply)
        apply_optional_services >/dev/null
        ;;
    status)
        [[ "${2:-}" == --json ]] && status_json || status_json
        ;;
    *)
        echo "Usage: ming-service-profile apply | status --json" >&2
        exit 2
        ;;
esac
MINGSERVICEPROFILE
    chmod 0755 /usr/local/sbin/ming-service-profile

    cat > /etc/systemd/system/ming-service-profile.service << 'MINGSERVICEPROFILESVC'
[Unit]
Description=Ming OS hardware-aware optional service profile
After=local-fs.target
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-service-profile apply
TimeoutStartSec=15s
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MINGSERVICEPROFILESVC
    systemctl disable --now ModemManager.service 2>/dev/null || true
    systemctl enable ming-service-profile.service 2>/dev/null || true
    # A chroot may not have a running systemd manager.  The best-effort apply
    # still leaves the on-disk unit and is re-applied on the real first boot.
    /usr/local/sbin/ming-service-profile apply >/dev/null 2>&1 || true
}

deploy_time_sync() {
    cat > /usr/local/sbin/ming-time-sync << 'MINGTIMESYNC'
#!/usr/bin/env bash
# Synchronise time only after NetworkManager reports a usable network.  This
# helper is deliberately event-driven: it must not hold up boot on an offline
# machine or repeatedly restart timesyncd when there is no connection.
set -uo pipefail

LOG=/var/log/ming-time-sync.log
LOCK=/run/ming-time-sync.lock
MODE="${1:-sync}"

log() {
    mkdir -p "$(dirname "${LOG}")" 2>/dev/null || true
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "${LOG}" 2>/dev/null || true
}

read_timedatectl() {
    local max_seconds="$1"
    shift
    timeout --foreground "${max_seconds}s" timedatectl "$@" 2>/dev/null || true
}

print_status_json() {
    local synchronized service network state timedate_error timedate_rc
    timedate_error="$(timeout --foreground 5s timedatectl show \
        -p NTPSynchronized --value 2>&1)"
    timedate_rc=$?
    synchronized="${timedate_error}"
    service="$(timeout --foreground 5s systemctl is-active systemd-timesyncd 2>/dev/null || true)"
    network="$(timeout --foreground 5s nm-online -q -t 4 >/dev/null 2>&1 && printf online || printf offline)"
    if (( timedate_rc != 0 )); then
        if grep -Eqi 'dbus|system has not been booted|failed to connect to bus' \
                <<<"${timedate_error}"; then
            state=dbus_unavailable
        else
            state=failed
        fi
        synchronized=""
    elif [[ "${synchronized,,}" =~ ^(yes|true|1)$ ]]; then
        state=synced
    elif [[ "${network}" != online ]]; then
        state=waiting_network
    elif [[ "${service}" != active && "${service}" != activating ]]; then
        state=service_inactive
    else
        state=waiting_network
    fi
    python3 - "${state}" "${synchronized}" "${service:-unknown}" "${network}" <<'PY'
import json
import sys
state, synchronized, service, network = sys.argv[1:]
print(json.dumps({
    "state": state,
    "synchronized": synchronized.strip().lower() in {"yes", "true", "1"},
    "service": service or "unknown",
    "network": network,
    "log": "/var/log/ming-time-sync.log",
}, ensure_ascii=False))
PY
}

synchronise() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "ming-time-sync sync requires system authorization" >&2
        return 1
    fi

    mkdir -p /run 2>/dev/null || return 1
    exec 9>"${LOCK}"
    if ! flock -n 9; then
        log "a time synchronisation is already running; skip duplicate request"
        return 0
    fi

    # Keep this guard before every timesyncd-changing operation.  Offline
    # starts are normal on old laptops and must not churn the time service.
    if ! nm-online -q -t 12; then
        log "network is not ready; leave systemd-timesyncd unchanged"
        return 0
    fi

    if ! timeout --foreground 10s timedatectl set-ntp true; then
        log "cannot enable NTP through timedatectl"
        return 1
    fi
    if ! timeout --foreground 10s systemctl restart systemd-timesyncd; then
        log "cannot restart systemd-timesyncd"
        return 1
    fi

    local deadline synchronized remaining probe_timeout sleep_for
    deadline=$((SECONDS + 45))
    while (( SECONDS < deadline )); do
        remaining=$((deadline - SECONDS))
        (( remaining > 0 )) || break
        probe_timeout=$(( remaining < 5 ? remaining : 5 ))
        synchronized="$(read_timedatectl "${probe_timeout}" show -p NTPSynchronized --value)"
        case "${synchronized,,}" in
            yes|true|1)
                log "time synchronised successfully"
                return 0
                ;;
        esac
        remaining=$((deadline - SECONDS))
        (( remaining > 0 )) || break
        sleep_for=$(( remaining < 3 ? remaining : 3 ))
        sleep "${sleep_for}"
    done
    log "NTP is still waiting after 45 seconds; service remains enabled"
    return 0
}

case "${MODE}" in
    status)
        [[ "${2:-}" == "--json" ]] || {
            echo "Usage: ming-time-sync status --json | sync" >&2
            exit 2
        }
        print_status_json
        ;;
    sync)
        synchronise
        ;;
    *)
        echo "Usage: ming-time-sync status --json | sync" >&2
        exit 2
        ;;
esac
MINGTIMESYNC
    chmod 0755 /usr/local/sbin/ming-time-sync

    install -d -m 0755 /etc/NetworkManager/dispatcher.d
    cat > /etc/NetworkManager/dispatcher.d/90-ming-time-sync << 'MINGTIMEDISPATCH'
#!/bin/sh
# NetworkManager invokes dispatchers as root.  Return immediately so DHCP and
# connection activation never wait for NTP; ming-time-sync serialises bursts.
set -u

event="${2:-}"
case "${event}" in
    up|dhcp4-change|dhcp6-change|connectivity-change)
        nohup /usr/local/sbin/ming-time-sync sync >/dev/null 2>&1 &
        ;;
esac
exit 0
MINGTIMEDISPATCH
    chmod 0755 /etc/NetworkManager/dispatcher.d/90-ming-time-sync
}

deploy_performance_status() {
    # Keep the performance baseline helper in assets so the same implementation
    # is used by the rootfs gate and by an installed system.  It is read-only and
    # all external probes are bounded, so missing hardware never blocks boot.
    local asset="/tmp/ming-build/assets/ming-performance-status.py"
    local target="/usr/local/sbin/ming-performance-status"
    if [[ ! -s "${asset}" ]]; then
        echo "[ERROR] missing performance status asset: ${asset}" >&2
        return 1
    fi
    install -m 0755 "${asset}" "${target}" || return 1
    if ! python3 - "${target}" <<'PY'
import ast
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
    then
        echo "[ERROR] ming-performance-status failed Python syntax validation" >&2
        return 1
    fi
}

deploy_performance_policy() {
    # Installs safe, best-effort policy controls.  The helper validates
    # PID+starttime+UID before touching scheduling state and reports missing
    # cgroup/ionice/renice support as degradation instead of blocking login.
    local asset="/tmp/ming-build/assets/ming-performance-policy.py"
    local target
    if [[ ! -s "${asset}" ]]; then
        echo "[ERROR] missing performance policy asset: ${asset}" >&2
        return 1
    fi
    python3 -m py_compile "${asset}" || return 1
    for target in \
        /usr/local/sbin/ming-performance-policy \
        /usr/local/sbin/ming-interaction-boost \
        /usr/local/sbin/ming-background-policy \
        /usr/local/bin/ming-prefetch; do
        install -m 0755 "${asset}" "${target}" || return 1
    done

    cat > /usr/local/bin/ming-ota-run << 'MINGOTARUN'
#!/usr/bin/env bash
set -uo pipefail

if [[ "$#" -lt 1 ]]; then
    echo "Usage: ming-ota-run <ming-update arguments...>" >&2
    exit 2
fi

run_direct_low_priority() {
    if command -v ionice >/dev/null 2>&1; then
        exec nice -n 10 ionice -c3 /usr/local/bin/ming-update "$@"
    fi
    exec nice -n 10 /usr/local/bin/ming-update "$@"
}

scope_started=false
run_in_systemd_scope() {
    local started_marker wrapped_rc
    [[ -r /sys/fs/cgroup/cgroup.controllers ]] || return 125
    command -v systemd-run >/dev/null 2>&1 || return 125
    mkdir -p -m 0755 /run/ming-os 2>/dev/null || return 125
    started_marker="$(mktemp /run/ming-os/ming-ota-started.XXXXXX)" || return 125
    rm -f -- "${started_marker}"

    if systemd-run --quiet --wait --collect --scope \
        -p Slice=ming-ota.slice \
        -p CPUWeight=20 \
        -p IOWeight=20 \
        -p Nice=10 \
        -p IOSchedulingClass=idle \
        /bin/sh -c '
            started_marker=$1
            shift
            : > "${started_marker}" || exit 125
            exec "$@"
        ' ming-ota-run "${started_marker}" /usr/local/bin/ming-update "$@"; then
        if [[ -e "${started_marker}" ]]; then
            scope_started=true
            rm -f -- "${started_marker}"
            return 0
        fi
        return 125
    else
        wrapped_rc=$?
        if [[ -e "${started_marker}" ]]; then
            scope_started=true
            rm -f -- "${started_marker}"
            return "${wrapped_rc}"
        fi
        rm -f -- "${started_marker}"
        return 125
    fi
}

if run_in_systemd_scope "$@"; then
    exit 0
else
    scope_rc=$?
fi
if [[ "${scope_started}" == true ]]; then
    exit "${scope_rc}"
fi

run_direct_low_priority "$@"
MINGOTARUN
    chmod 0755 /usr/local/bin/ming-ota-run

    cat > /etc/systemd/system/ming-ota.slice << 'MINGOTASLICE'
[Unit]
Description=Ming OS low-priority OTA workload slice

[Slice]
CPUWeight=20
IOWeight=20
MINGOTASLICE

    mkdir -p /run/ming-os
    cat > /run/ming-os/resource-policy.json << 'MINGRESOURCEDEFAULT' 2>/dev/null || true
{"mode":"adaptive","active_leases":0,"background_throttled":0,"degraded":["policy-service-not-yet-active"]}
MINGRESOURCEDEFAULT
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

switch_backend() {
    local backend="$1"
    local old_service new_service config_path config_tmp config_backup config_existed=false
    config_path=/etc/NetworkManager/conf.d/wifi-backend.conf
    case "${backend}" in
        iwd)
            old_service=wpa_supplicant.service
            new_service=iwd.service
            if ! systemctl list-unit-files iwd.service --no-legend 2>/dev/null | grep -q '^iwd\.service'; then
                echo "iwd is not installed; keep wpa_supplicant active." >&2
                return 1
            fi
            ;;
        wpa_supplicant)
            old_service=iwd.service
            new_service=wpa_supplicant.service
            ;;
        *)
            echo "Unknown Wi-Fi backend: ${backend}" >&2
            return 2
            ;;
    esac

    mkdir -p /etc/NetworkManager/conf.d || return 1
    config_backup="$(mktemp "${config_path}.backup.XXXXXX")" || return 1
    if [[ -f "${config_path}" ]]; then
        if ! cp -p "${config_path}" "${config_backup}"; then
            rm -f "${config_backup}"
            return 1
        fi
        config_existed=true
    fi

    rollback_backend() {
        echo "Rolling back Wi-Fi backend switch." >&2
        if ! systemctl disable --now "${new_service}"; then
            echo "Cannot stop ${new_service}; refusing to start ${old_service}." >&2
            return 1
        fi
        if [[ "${config_existed}" == true ]]; then
            if ! cp -pf "${config_backup}" "${config_path}"; then
                echo "Cannot restore ${config_path}; refusing to start ${old_service}." >&2
                return 1
            fi
        else
            if ! rm -f "${config_path}"; then
                echo "Cannot remove ${config_path}; refusing to start ${old_service}." >&2
                return 1
            fi
        fi
        if ! systemctl enable --now "${old_service}"; then
            echo "Cannot restore ${old_service}; both Wi-Fi backends remain stopped." >&2
            return 1
        fi
        if ! systemctl restart NetworkManager; then
            echo "Cannot restart NetworkManager after rollback." >&2
            return 1
        fi
        return 0
    }

    if ! systemctl disable --now "${old_service}"; then
        echo "Cannot stop ${old_service}; backend was not changed." >&2
        rm -f "${config_backup}"
        return 1
    fi
    if ! systemctl enable --now "${new_service}"; then
        echo "Cannot start ${new_service}; restoring ${old_service}." >&2
        rollback_backend || echo "Wi-Fi backend rollback was incomplete; ${old_service} was not started over ${new_service}." >&2
        rm -f "${config_backup}"
        return 1
    fi

    config_tmp="$(mktemp "${config_path}.XXXXXX")" || {
        rollback_backend || echo "Wi-Fi backend rollback was incomplete; ${old_service} was not started over ${new_service}." >&2
        rm -f "${config_backup}"
        return 1
    }
    if [[ "${backend}" == "iwd" ]]; then
        cat > "${config_tmp}" <<'EOF'
[device]
wifi.backend=iwd
wifi.iwd.autoconnect=yes
wifi.scan-rand-mac-address=no
EOF
    else
        cat > "${config_tmp}" <<'EOF'
[device]
wifi.backend=wpa_supplicant
wifi.scan-rand-mac-address=no
EOF
    fi
    if ! mv -f "${config_tmp}" "${config_path}"; then
        rm -f "${config_tmp}"
        rollback_backend || echo "Wi-Fi backend rollback was incomplete; ${old_service} was not started over ${new_service}." >&2
        rm -f "${config_backup}"
        return 1
    fi
    if ! systemctl restart NetworkManager; then
        echo "NetworkManager restart failed; restoring the previous backend." >&2
        rollback_backend || echo "Wi-Fi backend rollback was incomplete; ${old_service} was not started over ${new_service}." >&2
        rm -f "${config_backup}"
        return 1
    fi
    rm -f "${config_backup}"
    echo "Selected Wi-Fi backend: ${backend}"
}

case "${BACKEND}" in
    --use-iwd)
        switch_backend iwd || exit $?
        ;;
    --use-wpa|"")
        switch_backend wpa_supplicant || exit $?
        ;;
    *)
        echo "Unknown option: ${BACKEND}" >&2
        exit 2
        ;;
esac

rfkill unblock all 2>/dev/null || true
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
echo "== Bluetooth hardware =="
bluetoothctl list 2>/dev/null || true
btmgmt info 2>/dev/null || true
systemctl --no-pager --full status bluetooth 2>/dev/null | sed -n '1,60p' || true

echo
echo "== missing firmware hints =="
dmesg 2>/dev/null | grep -Ei 'firmware|iwlwifi|ath|brcm|b43|rtl|rt2|mt76|btusb|bluetooth|failed|missing' | tail -100 || true

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Ming OS 网络修复结果" --width=820 --height=620 --filename="${LOG}" 2>/dev/null || true
fi
NETREPAIR
    chmod 0755 /usr/local/bin/ming-network-repair

    cat > /usr/local/sbin/ming-radio-repair << 'RADIOREPAIR'
#!/usr/bin/env bash
set -uo pipefail

if [[ "${1:-}" != "bluetooth" || "$#" -ne 1 ]]; then
    echo "Usage: ming-radio-repair bluetooth" >&2
    exit 2
fi

# The repair command changes rfkill state, kernel modules and system services.
# Always cross the policy boundary through polkit when launched by a desktop user.
if [[ "${EUID}" -ne 0 ]]; then
    if ! command -v pkexec >/dev/null 2>&1; then
        echo "无法请求管理员授权：系统缺少 Polkit（pkexec）组件。" >&2
        exit 77
    fi
    if auth_output="$(pkexec /usr/local/sbin/ming-radio-repair bluetooth 2>&1)"; then
        auth_rc=0
    else
        auth_rc=$?
    fi
    if [[ "${auth_rc}" -ne 0 ]]; then
        if [[ "${auth_output}" == *"Error creating textual authentication agent"* \
                || "${auth_output}" == *"/dev/tty"* \
                || "${auth_output}" == *"No such device or address"* ]]; then
            echo "请在桌面授权弹窗中确认，当前无可用图形授权代理。请先完成账户设置或重启授权代理。" >&2
        elif [[ "${auth_output}" == *"not authorized"* || "${auth_output}" == *"Not authorized"* ]]; then
            echo "蓝牙修复需要管理员授权，请在桌面授权弹窗中确认。" >&2
        else
            printf '%s\n' "${auth_output:-蓝牙修复授权失败。}" >&2
        fi
        exit "${auth_rc}"
    fi
    [[ -n "${auth_output}" ]] && printf '%s\n' "${auth_output}"
    exit 0
fi

LOG=/var/log/ming-radio-repair.log
touch "${LOG}" 2>/dev/null || {
    echo "Unable to open ${LOG}" >&2
    exit 1
}
exec > >(tee -a "${LOG}") 2>&1

echo "Ming OS Bluetooth radio repair"
date -Is

if [[ ! -x /usr/local/bin/ming-device-control ]]; then
    echo "ming-device-control is unavailable" >&2
    exit 1
fi

status_json() {
    /usr/local/bin/ming-device-control bluetooth-status --json
}

status_state() {
    python3 -c 'import json, sys; print(json.load(sys.stdin).get("state", "unknown"))'
}

status_hard_blocked() {
    python3 -c 'import json, sys; print("true" if json.load(sys.stdin).get("rfkill", {}).get("hard_blocked") else "false")'
}

before_json="$(status_json 2>/dev/null || true)"
before_state="$(printf '%s' "${before_json}" | status_state 2>/dev/null || printf 'unknown')"
before_hard_blocked="$(printf '%s' "${before_json}" | status_hard_blocked 2>/dev/null || printf 'false')"
printf 'before-state=%s\n' "${before_state}"
printf 'before-hard-rfkill=%s\n' "${before_hard_blocked}"
printf 'before-json=%s\n' "${before_json}"

if [[ "${before_state}" == "diagnostic_unavailable" ]]; then
    echo "Bluetooth hardware diagnosis is incomplete; refusing service/module changes. Export diagnostics first." >&2
    exit 1
fi

if [[ "${before_hard_blocked}" == "true" ]]; then
    echo "Bluetooth is hard-blocked by a physical switch or BIOS; software repair cannot change it." >&2
    exit 1
fi

if [[ "${before_state}" == "no_hardware" ]]; then
    echo "No Bluetooth hardware detected; no repair is required."
    exit 0
fi

if systemctl list-unit-files bluetooth.service --no-legend 2>/dev/null \
        | awk '{print $1}' | grep -Fxq bluetooth.service; then
    systemctl stop bluetooth.service 2>/dev/null || true
else
    echo "bluetooth.service is unavailable; continuing with a hardware-only check"
fi

/usr/sbin/rfkill unblock bluetooth 2>/dev/null || true

mainline_modules=(btusb btintel btrtl btbcm ath3k)
loaded_modules=()
while read -r module; do
    [[ -n "${module}" ]] && loaded_modules+=("${module}")
done < <(lsmod 2>/dev/null | awk 'NR > 1 {print $1}' \
    | grep -Ex 'btusb|btintel|btrtl|btbcm|ath3k' || true)

if [[ "${#loaded_modules[@]}" -eq 0 ]]; then
    echo "No loaded mainline Bluetooth module needs reloading."
else
    printf 'detected-modules=%s\n' "${loaded_modules[*]}"
fi

removed_modules=()
for module in ath3k btbcm btrtl btintel btusb; do
    if [[ " ${loaded_modules[*]} " == *" ${module} "* ]]; then
        if modprobe -r "${module}" 2>/dev/null; then
            removed_modules+=("${module}")
            printf 'unloaded=%s\n' "${module}"
        else
            printf 'kept-loaded=%s\n' "${module}"
        fi
    fi
done
for module in btusb btintel btrtl btbcm ath3k; do
    if [[ " ${removed_modules[*]} " == *" ${module} "* ]]; then
        if modprobe "${module}" 2>/dev/null; then
            printf 'reloaded=%s\n' "${module}"
        else
            printf 'reload-failed=%s\n' "${module}"
        fi
    fi
done

if systemctl list-unit-files bluetooth.service --no-legend 2>/dev/null \
        | awk '{print $1}' | grep -Fxq bluetooth.service; then
    systemctl enable bluetooth.service 2>/dev/null || true
    systemctl start bluetooth.service 2>/dev/null || true
fi

after_json="$(status_json 2>/dev/null || true)"
if [[ -z "${after_json}" ]] || ! printf '%s' "${after_json}" | python3 -c 'import json, sys; json.load(sys.stdin)' >/dev/null 2>&1; then
    echo "Bluetooth status verification did not return JSON" >&2
    exit 1
fi
after_state="$(printf '%s' "${after_json}" | status_state)"
printf 'after-state=%s\n' "${after_state}"
printf 'after-json=%s\n' "${after_json}"

if [[ "${after_state}" == "no_hardware" || "${after_state}" == "ready" ]]; then
    exit 0
fi

echo "Bluetooth remains in state ${after_state}; inspect ${LOG}" >&2
exit 1
RADIOREPAIR
    chmod 0755 /usr/local/sbin/ming-radio-repair
    # pkexec sanitizes PATH on some desktop sessions and may omit
    # /usr/local/sbin. Keep a compatibility entry in the normal executable
    # path while retaining the privileged helper's canonical location.
    ln -sf /usr/local/sbin/ming-radio-repair /usr/local/bin/ming-radio-repair

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

echo
echo "== Broadcom driver recommendation =="
if [[ -x /usr/local/sbin/ming-broadcom-driver ]]; then
    /usr/local/sbin/ming-broadcom-driver status 2>&1 || true
else
    echo "Broadcom driver manager is unavailable"
fi

echo
echo "== Secure Boot and DKMS =="
mokutil --sb-state 2>/dev/null || echo "Secure Boot state unavailable"
dkms status 2>/dev/null || true

echo
echo "== VA-API =="
vainfo --display drm 2>&1 | sed -n '1,80p' || true

echo
echo "== In-tree legacy laptop modules =="
for module in rtw88_8821cu applespi spi_pxa2xx_platform intel_lpss_pci; do
    if modinfo "${module}" >/dev/null 2>&1; then
        echo "${module}: available"
    else
        echo "${module}: missing"
    fi
done
latest_initrd=$(find /boot -maxdepth 1 -type f -name 'initrd.img-*' -print 2>/dev/null \
    | sort -V | tail -1)
if [[ -n "${latest_initrd}" ]]; then
    echo "initramfs=${latest_initrd}"
    lsinitramfs "${latest_initrd}" 2>/dev/null \
        | grep -E '/(applespi|spi-pxa2xx-platform|intel-lpss-pci)\.ko' || true
fi

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --text-info --title="Ming OS 驱动检测" --width=860 --height=640 --filename="${LOG}" 2>/dev/null || true
fi
DRIVERDIAG
    chmod 0755 /usr/local/bin/ming-driver-diagnose

cat > /usr/local/bin/ming-diagnostic-bundle << 'DIAGBUNDLE'
#!/usr/bin/env bash
set -uo pipefail
umask 077
OUT_DIR="${HOME:-/tmp}/Desktop"
[[ -d "${OUT_DIR}" ]] || OUT_DIR="/tmp"
STAMP="$(date '+%Y%m%d-%H%M%S')"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ming-diagnostics.XXXXXX")" || {
    echo "无法建立诊断临时目录。" >&2
    exit 1
}
RAW_WORK="$(mktemp -d "${TMPDIR:-/tmp}/ming-diagnostics-raw.XXXXXX")" || {
    rm -rf "${WORK}"
    echo "无法建立诊断采集目录。" >&2
    exit 1
}
ARCHIVE="$(mktemp --suffix=.tar.gz "${OUT_DIR}/Ming-OS-诊断包-${STAMP}-XXXXXX")" || {
    rm -rf "${WORK}" "${RAW_WORK}"
    echo "无法建立诊断归档文件。" >&2
    exit 1
}
MAX_FILES=128
MAX_FILE_BYTES=$((4 * 1024 * 1024))
MAX_TOTAL_BYTES=$((5 * 1024 * 1024))
MAX_ARCHIVE_BYTES=$((8 * 1024 * 1024))
staged_files=0
staged_total=0
chmod 0700 "${WORK}" "${RAW_WORK}" "${ARCHIVE}" || {
    rm -rf "${WORK}" "${RAW_WORK}" "${ARCHIVE}"
    echo "无法设置诊断临时文件权限。" >&2
    exit 1
}
diagnostic_bundle_ready=0
cleanup_diagnostic_bundle() {
    rm -rf "${WORK}" "${RAW_WORK}"
    if [[ "${diagnostic_bundle_ready}" != "1" ]]; then
        rm -f "${ARCHIVE}"
    fi
}
trap cleanup_diagnostic_bundle EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM

# Only copy regular, unlinked UTF-8/text files.  The link-count check keeps a
# diagnostic archive from exposing another file through a hard link.  Files
# are copied with explicit 0600 permissions and a caller-supplied safe name.
stage_text_file() {
    local src="$1" dest="$2" size links
    [[ "${dest}" != /* && "${dest}" != *".."* ]] || return 1
    [[ -f "${src}" && ! -L "${src}" ]] || return 1
    links="$(stat -c '%h' -- "${src}" 2>/dev/null || echo 2)"
    [[ "${links}" == "1" ]] || return 1
    size="$(stat -c '%s' -- "${src}" 2>/dev/null || echo $((MAX_FILE_BYTES + 1)))"
    [[ "${size}" =~ ^[0-9]+$ && "${size}" -le "${MAX_FILE_BYTES}" ]] || return 1
    (( staged_files < MAX_FILES )) || return 1
    (( staged_total + size <= MAX_TOTAL_BYTES )) || return 1
    if [[ -s "${src}" ]] && ! LC_ALL=C grep -Iq . -- "${src}" 2>/dev/null; then
        return 1
    fi
    iconv -f UTF-8 -t UTF-8 "${src}" >/dev/null 2>&1 || return 1
    install -d -m 0700 "$(dirname "${WORK}/${dest}")" || return 1
    cp --reflink=auto -- "${src}" "${WORK}/${dest}" 2>/dev/null || return 1
    if ! sed -E -i \
        -e 's#(password|passphrase|passwd|token|secret|api[_-]?key|authorization|cookie)([[:space:]]*[=:][[:space:]]*|[[:space:]]+).*#\1=<redacted>#Ig' \
        -e 's#(SSID|BSSID)[[:space:]]*[=:][[:space:]]*.*#\1=<redacted>#Ig' \
        -e 's#/(home|root)/[^[:space:]/]+#/<redacted>#g' \
        -e 's#(machine[-_]?id|serial(number)?|MAC([[:space:]]+address)?|IPv?4|username|user)[[:space:]]*[=:][[:space:]]*.*#\1=<redacted>#Ig' \
        -e 's#([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}#<redacted>#Ig' \
        -e 's#([0-9]{1,3}\.){3}[0-9]{1,3}#<redacted>#g' \
        -e 's#([[:xdigit:]]{1,4}:){2,7}[[:xdigit:]]{0,4}#<redacted>#Ig' \
        "${WORK}/${dest}"; then
        rm -f "${WORK}/${dest}"
        return 1
    fi
    chmod 0600 "${WORK}/${dest}"
    ((staged_files += 1))
    ((staged_total += size))
}

stage_path() {
    local src="$1" label="$2" path rel index=0
    if [[ -f "${src}" || -L "${src}" ]]; then
        stage_text_file "${src}" "logs/${label}.txt" || true
        return
    fi
    [[ -d "${src}" && ! -L "${src}" ]] || return
    while IFS= read -r -d '' path; do
        rel="${path#${src}/}"
        # Keep names deterministic and harmless while retaining enough context
        # for a developer to identify the originating log.
        rel="${rel//[^A-Za-z0-9._-]/_}"
        stage_text_file "${path}" "logs/${label}-${index}-${rel}" || true
        ((index += 1))
        (( staged_files >= MAX_FILES )) && break
        (( staged_total >= MAX_TOTAL_BYTES )) && break
    done < <(find -P "${src}" -type f -print0 2>/dev/null)
}

collect() {
    local name="$1"; shift
    {
        echo "$ $*"
        "$@" 2>&1 || true
    } > "${RAW_WORK}/${name}.txt"
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
    stage_path "${src}" "$(basename "${src}")"
done

while IFS= read -r -d '' collected; do
    stage_text_file "${collected}" "logs/collect-$(basename "${collected}")" || true
done < <(find -P "${RAW_WORK}" -type f -print0 2>/dev/null)
rm -rf "${RAW_WORK}"

if ! (cd "${WORK}" && find -P . -type f ! -type l -print0 \
    | tar --create --gzip --format=gnu --owner=0 --group=0 --numeric-owner \
        --mtime='@0' --mode='u=rw,go=' --file="${ARCHIVE}" -C . \
        --null --no-recursion --files-from=-); then
    rm -rf "${WORK}"
    echo "无法生成安全诊断包。" >&2
    exit 1
fi
if [[ "$(wc -c < "${ARCHIVE}")" -gt "${MAX_ARCHIVE_BYTES}" ]]; then
    rm -rf "${WORK}" "${ARCHIVE}"
    echo "诊断包超过 8MB 限制。" >&2
    exit 3
fi
diagnostic_bundle_ready=1
rm -rf "${WORK}"

if command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    zenity --info --title="Ming OS 问题诊断" --width=620 \
        --text="诊断包已生成：\n${ARCHIVE}\n\n把这个文件发给开发者即可，不需要手动输入命令。" 2>/dev/null || true
else
    echo "${ARCHIVE}"
fi
DIAGBUNDLE
    chmod 0755 /usr/local/bin/ming-diagnostic-bundle

cat > /usr/local/bin/ming-diagnostic-upload << 'DIAGUPLOAD'
#!/usr/bin/env bash
set -uo pipefail
umask 077

endpoint="${MING_DIAGNOSTIC_ENDPOINT:-https://ming.sca-hub.cn/api/ming-diagnostics/reports}"
schema=ming.diagnostic.v1
queue="${XDG_CACHE_HOME:-${HOME}/.cache}/ming-os/diagnostic-queue"
mkdir -p "${queue}"
archive="${1:-}"
if [[ -z "${archive}" || ! -s "${archive}" ]]; then
    ming-diagnostic-bundle >/dev/null 2>&1 || true
    archive="$(find "${HOME}/Desktop" /tmp -maxdepth 2 -type f -name 'Ming-OS-诊断包-*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {$1=""; sub(/^ /,""); print}')"
fi
[[ -s "${archive}" ]] || { echo "无法生成诊断包。" >&2; exit 2; }

MAX_ARCHIVE_BYTES=$((8 * 1024 * 1024))
MAX_FILES=128
MAX_FILE_BYTES=$((4 * 1024 * 1024))
MAX_TOTAL_BYTES=$((5 * 1024 * 1024))
if [[ ! -f "${archive}" || -L "${archive}" ]]; then
    echo "诊断包必须是普通文件。" >&2
    exit 2
fi
archive_bytes="$(stat -c '%s' -- "${archive}" 2>/dev/null || echo $((MAX_ARCHIVE_BYTES + 1)))"
if [[ ! "${archive_bytes}" =~ ^[0-9]+$ || "${archive_bytes}" -gt "${MAX_ARCHIVE_BYTES}" ]]; then
    echo "诊断包超过 8MB 限制。" >&2
    exit 3
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/ming-diagnostic-upload.XXXXXX")" || {
    echo "无法建立安全诊断临时目录。" >&2
    exit 2
}
if ! chmod 0700 "${work}"; then
    rm -rf "${work}"
    echo "无法设置安全诊断临时目录权限。" >&2
    exit 2
fi
safe_archive=""
cleanup() { rm -rf "${work}" "${safe_archive:-}"; }
trap cleanup EXIT

# tarfile validates every member before anything is extracted.  It rejects
# links/devices/directories, path traversal, nested archives, binary payloads,
# oversized members and archive bombs.  Repacked names and metadata never carry
# the user's original home path or ownership information to the server.
if ! python3 - "${archive}" "${work}" "${MAX_FILES}" "${MAX_FILE_BYTES}" "${MAX_TOTAL_BYTES}" <<'PY' 2>/dev/null
import os
import re
import sys
import tarfile
from pathlib import PurePosixPath

archive, destination = sys.argv[1:3]
max_files, max_file_bytes, max_total_bytes = map(int, sys.argv[3:])
archive_suffixes = {
    ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".zip", ".7z", ".rar",
    ".lz", ".lz4", ".lzma", ".cab", ".cpio", ".ar", ".deb", ".apk", ".iso",
}
redactions = (
    (re.compile(r"(?im)(\b(?:password|passphrase|passwd|token|secret|api[_-]?key|authorization|cookie)\b(?:\s*[=:]\s*|\s+))[^\r\n]*"), r"\1<redacted>"),
    (re.compile(r"(?im)(\b(?:ssid|bssid)\b\s*[=:]\s*)[^\r\n]*"), r"\1<redacted>"),
    (re.compile(r"/(?:home|root)/[^\s/]+"), "/<redacted>"),
    (re.compile(r"(?im)(\b(?:machine[-_]?id|serial(?:number)?|mac(?:\s+address)?|ipv?4|username|user)\b\s*[=:]\s*)[^\r\n]*"), r"\1<redacted>"),
    (re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"), "<redacted>"),
    (re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"), "<redacted>"),
    (re.compile(r"(?i)\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}\b"), "<redacted>"),
)

def reject(reason):
    raise ValueError(reason)

with open(archive, "rb") as raw:
    if raw.read(2) != b"\x1f\x8b":
        reject("诊断包必须是 gzip 格式")

seen = set()
total = 0
count = 0
with tarfile.open(archive, mode="r:gz") as source:
    for member in source:
        count += 1
        if count > max_files:
            reject("诊断包文件数量超过限制")
        if not member.isreg() or member.issym() or member.islnk() or member.isdir():
            reject("诊断包包含非普通文本文件")
        name = member.name
        if not name or "\x00" in name:
            reject("诊断包文件名无效")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in ("", "..") for part in path.parts):
            reject("诊断包包含不安全路径")
        if name.casefold().endswith(tuple(archive_suffixes)):
            reject("诊断包禁止嵌套压缩文件")
        if member.size < 0 or member.size > max_file_bytes:
            reject("诊断包单文件超过 4MB 限制")
        if name in seen:
            reject("诊断包包含重复文件")
        seen.add(name)
        total += member.size
        if total > max_total_bytes:
            reject("诊断包内容超过 5MB 限制")
        stream = source.extractfile(member)
        if stream is None:
            reject("诊断包文件无法读取")
        payload = stream.read(max_file_bytes + 1)
        if len(payload) != member.size or len(payload) > max_file_bytes:
            reject("诊断包文件读取超出限制")
        if b"\x00" in payload:
            reject("诊断包包含二进制内容")
        if payload.startswith((b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"ustar")):
            reject("诊断包禁止嵌套压缩文件")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            reject("诊断包包含非文本内容")
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            reject("诊断包包含不可读控制字符")
        for pattern, replacement in redactions:
            text = pattern.sub(replacement, text)
        output = os.path.join(destination, "file-%03d.txt" % count)
        with open(output, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(output, 0o600)
PY
then
    echo "诊断包包含不安全内容，已拒绝上传。" >&2
    exit 2
fi

safe_archive="$(mktemp --suffix=.tar.gz "${TMPDIR:-/tmp}/ming-diagnostic-sanitized.XXXXXX")" || {
    echo "无法建立安全诊断归档文件。" >&2
    exit 2
}
if ! chmod 0600 "${safe_archive}"; then
    echo "无法设置安全诊断归档权限。" >&2
    exit 2
fi
if ! (cd "${work}" && find -P . -type f ! -type l -print0 \
    | tar --create --gzip --format=gnu --owner=0 --group=0 --numeric-owner \
        --mtime='@0' --mode='u=rw,go=' --file="${safe_archive}" -C . \
        --null --no-recursion --files-from=-); then
    echo "无法重新打包脱敏诊断内容。" >&2
    exit 2
fi
if [[ "$(wc -c < "${safe_archive}")" -gt "${MAX_ARCHIVE_BYTES}" ]]; then
    echo "脱敏诊断包超过 8MB 限制。" >&2
    exit 3
fi
response="$(curl -4 -fsS --max-time 30 \
    -H 'Accept: application/json' \
    -F 'report={"schema":"ming.diagnostic.v1","summary":"Ming OS 用户确认的脱敏诊断报告"};type=application/json' \
    -F "bundle=@${safe_archive};filename=ming-diagnostic.tar.gz;type=application/gzip" \
    "${endpoint}" 2>&1)"
rc=$?
if [[ "${rc}" -ne 0 ]]; then
    cp -f "${safe_archive}" "${queue}/$(date +%s).tar.gz" 2>/dev/null || true
    echo "诊断上传失败，已保存到本地待重试队列。" >&2
    exit "${rc}"
fi
printf '%s\n' "${response}"
DIAGUPLOAD
    chmod 0755 /usr/local/bin/ming-diagnostic-upload

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

# Autostart invokes this fixed entry point instead of composing a shell
# command in a desktop file.  Keep the session action idempotent and scoped to
# the current user's compositor/settings.
if [[ "${1:-}" == "--session" ]]; then
    pkill picom 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/use_compositing -s false 2>/dev/null || true
    exit 0
fi

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
Exec=/usr/local/bin/ming-classic-mode --session
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

    cat > /etc/ming-release << RELEASE
Ming OS ${MING_OS_VERSION} Home Edition
RELEASE
    mkdir -p /usr/share /etc/default/grub.d /boot/grub/themes/ming
    ln -sf /etc/ming-release /usr/share/ming-release
    if [[ ! -s /tmp/ming-build/assets/grub-theme/theme.txt ]]; then
        echo "ERROR: Ming GRUB theme asset is missing" >&2
        return 1
    fi
    install -m 0644 /tmp/ming-build/assets/grub-theme/theme.txt /boot/grub/themes/ming/theme.txt
    install -m 0755 /tmp/ming-build/assets/ming-detect-other-os /usr/local/sbin/ming-detect-other-os
    cat > /etc/default/grub.d/10-ming-os.cfg << GRUBCFG
GRUB_DISTRIBUTOR="Ming OS"
GRUB_THEME="/boot/grub/themes/ming/theme.txt"
GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
GRUB_TERMINAL_INPUT=console
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_RECORDFAIL_TIMEOUT=0
GRUB_DISABLE_SUBMENU=false
GRUB_DISABLE_OS_PROBER=true
GRUB_DISABLE_RECOVERY=true
GRUBCFG
    for noisy_grub in 10_linux 20_linux_xen 30_os-prober 30_uefi-firmware; do
        if [[ -f "/etc/grub.d/${noisy_grub}" ]]; then
            # Ming's audited generator owns the compact menu. Debian's generic
            # generator would add one top-level item per installed kernel.
            chmod 0644 "/etc/grub.d/${noisy_grub}" 2>/dev/null || true
        fi
    done
}

# ======================== 安装器品牌与安装后身份兜底 ========================

configure_installer_identity() {
    mkdir -p /usr/local/sbin
    install -d -m 0755 /usr/local/lib/ming-os
    install -m 0644 /tmp/ming-build/assets/ming-ota-target-guard.py \
        /usr/local/lib/ming-os/ming_ota_target_guard.py

    local ota_guard_module=/usr/lib/x86_64-linux-gnu/calamares/modules/ming-ota-target-guard
    install -d -m 0755 "${ota_guard_module}"
    cat > "${ota_guard_module}/module.desc" << 'MINGOTAGUARDDESC'
---
type: "job"
name: "ming-ota-target-guard"
interface: "python"
script: "main.py"
MINGOTAGUARDDESC
    cat > "${ota_guard_module}/main.py" << 'MINGOTAGUARDPY'
#!/usr/bin/env python3
import importlib.util
import pathlib

import libcalamares


GUARD_PATH = pathlib.Path("/usr/local/lib/ming-os/ming_ota_target_guard.py")
SPEC = importlib.util.spec_from_file_location("ming_ota_target_guard", GUARD_PATH)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def run():
    if not pathlib.Path("/run/ming-ota-preflight.ok").is_file():
        return None
    partitions = libcalamares.globalstorage.value("partitions")
    ok, message = GUARD.validate_from_marker(partitions)
    if ok:
        return None
    return "Ming OTA safety check failed", message
MINGOTAGUARDPY
    chmod 0644 "${ota_guard_module}/main.py"

    cat > /usr/local/sbin/ming-ota-preflight << 'MINGOTAPREFLIGHT'
#!/usr/bin/env bash
set -euo pipefail

log=/run/ming-installer/ota-preflight.log
marker=/run/ming-ota-preflight.ok
install -d -m 0755 /run/ming-installer
: >"${log}"
chmod 0600 "${log}"
exec >>"${log}" 2>&1
rm -f "${marker}"

cmdline_value() {
    local key="$1" token
    for token in $(cat /proc/cmdline 2>/dev/null); do
        case "${token}" in "${key}"=*) printf '%s\n' "${token#*=}"; return 0 ;; esac
    done
    return 1
}

grep -qw 'ming.ota=1' /proc/cmdline 2>/dev/null || exit 0
uuid="$(cmdline_value ming.ota_backup_uuid || true)"
relative="$(cmdline_value ming.ota_manifest || true)"
[[ "${uuid}" =~ ^[A-Fa-f0-9-]{4,128}$ ]] || { echo "invalid OTA UUID"; exit 31; }
relative="${relative#/}"
[[ -n "${relative}" && "${relative}" != *'..'* && "${relative}" != *$'\n'* ]] \
    || { echo "invalid OTA manifest path"; exit 31; }

device="$(blkid -U "${uuid}" 2>/dev/null | head -n 1 || true)"
[[ -b "${device}" ]] || { echo "OTA backup device is missing"; exit 31; }
mount_dir=/run/ming-ota-preflight-mount
mkdir -p "${mount_dir}"
mount -o ro,nosuid,nodev,noexec "${device}" "${mount_dir}"
trap 'umount "${mount_dir}" 2>/dev/null || true' EXIT

manifest="$(readlink -f "${mount_dir}/${relative}" 2>/dev/null || true)"
[[ -n "${manifest}" && "${manifest}" == "${mount_dir}/"* && -f "${manifest}" && ! -L "${manifest}" ]] \
    || { echo "OTA manifest escaped its backup mount"; exit 31; }
manifest_uuid="$(jq -r '.backup_uuid // .disk_uuid // ""' "${manifest}" 2>/dev/null || true)"
strategy="$(jq -r '.strategy // "completed_backup"' "${manifest}" 2>/dev/null || true)"
[[ "${manifest_uuid}" == "${uuid}" ]] || { echo "OTA manifest UUID mismatch"; exit 31; }

if [[ "${strategy}" == "separate_home" ]]; then
    [[ "$(jq -r '.complete // false' "${manifest}")" == "true" ]] \
        || { echo "separate home plan is incomplete"; exit 31; }
else
    /usr/local/sbin/ming-ota-backup verify --manifest "${manifest}"
fi

printf 'uuid=%s\nmanifest=/%s\nstrategy=%s\n' "${uuid}" "${relative}" "${strategy}" > "${marker}"
chmod 0600 "${marker}"
echo "OTA preflight passed before partitioning"
MINGOTAPREFLIGHT
    chmod 0755 /usr/local/sbin/ming-ota-preflight

    cat > /usr/local/sbin/ming-fix-installed-identity << 'MINGIDENTITY'
#!/usr/bin/env bash
set -uo pipefail

version="${MING_OS_VERSION:-26.4.1}"
install_mode_state=/run/ming-installer/install-mode.json
[[ -f "${install_mode_state}" && ! -L "${install_mode_state}" ]] || {
    echo "ERROR: root-only install mode receipt is missing or unsafe" >&2
    exit 30
}
install_mode_json="$(/usr/local/sbin/ming-install-mode show --state "${install_mode_state}")" || {
    echo "ERROR: install mode receipt failed validation" >&2
    exit 30
}
install_mode="$(jq -er '.mode' <<<"${install_mode_json}")" || exit 30
major_ota="$(jq -er '.major_ota' <<<"${install_mode_json}")" || exit 30
case "${install_mode}:${major_ota}" in
    blank_ab:ab_slot|dual_boot_preserve:disabled_dual_boot) ;;
    *) echo "ERROR: install mode and major OTA policy do not match" >&2; exit 30 ;;
esac
target="$(/usr/local/sbin/ming-installer-verify receipt --field target)" || {
    echo "ERROR: authoritative Calamares target receipt is missing or invalid" >&2
    exit 30
}
root_source="$(/usr/local/sbin/ming-installer-verify receipt --field source)" || {
    echo "ERROR: authoritative Calamares root source receipt is missing or invalid" >&2
    exit 30
}
root_fstype="$(/usr/local/sbin/ming-installer-verify receipt --field fstype)" || {
    echo "ERROR: authoritative Calamares root filesystem receipt is missing or invalid" >&2
    exit 30
}
root_uuid="$(/usr/local/sbin/ming-installer-verify receipt --field uuid)" || {
    echo "ERROR: root UUID receipt is missing or invalid" >&2
    exit 30
}
[[ "${target}" != "/" && -d "${target}/etc" && -d "${target}/boot" ]] || {
    echo "ERROR: authoritative Calamares target is not an unpacked installed root" >&2
    exit 30
}
case "${root_source}" in /dev/*) ;; *) echo "ERROR: root source receipt is not a block device" >&2; exit 30 ;; esac
case "${root_fstype}" in ""|overlay|tmpfs|squashfs) echo "ERROR: root filesystem receipt is not persistent" >&2; exit 30 ;; esac
[[ "${root_uuid}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    echo "ERROR: root UUID receipt is missing or invalid" >&2
    exit 30
}

ensure_persistent_root_fstab() {
    /usr/local/sbin/ming-installer-verify fstab --target "${target}" \
        --uuid "${root_uuid}" --fstype "${root_fstype}"
}

/usr/local/sbin/ming-installer-verify boundary --target "${target}" || exit 30
ensure_persistent_root_fstab || exit 30

write_ota_ready_layout() {
    local esp_device boot_device root_a_device root_b_device home_device
    local esp_uuid boot_uuid root_a_uuid root_b_uuid home_uuid unique_count target_disk candidate_disk
    local esp_fstype esp_parttype esp_mount_source
    physical_disk_for_device() {
        lsblk -s -nrpo NAME,TYPE "$1" 2>/dev/null \
            | awk '$2 == "disk" {print $1}' | sort -u
    }
    partlabel_device_on_disk() {
        local label="$1" disk="$2" link candidate candidate_disk
        for _attempt in 1 2 3 4 5 6; do
            udevadm settle --timeout=10 2>/dev/null || true
            link="/dev/disk/by-partlabel/${label}"
            candidate=""
            if [[ -e "${link}" || -L "${link}" ]]; then
                candidate="$(readlink -f -- "${link}" 2>/dev/null || true)"
            fi
            if [[ "${candidate}" == /dev/* && -b "${candidate}" ]]; then
                candidate_disk="$(physical_disk_for_device "${candidate}")"
                if [[ "${candidate_disk}" == "${disk}" ]]; then
                    printf '%s\n' "${candidate}"
                    return 0
                fi
            fi
            while IFS= read -r candidate; do
                candidate="$(readlink -f -- "${candidate}" 2>/dev/null || true)"
                [[ "${candidate}" == /dev/* && -b "${candidate}" ]] || continue
                candidate_disk="$(physical_disk_for_device "${candidate}")"
                if [[ "${candidate_disk}" == "${disk}" ]]; then
                    printf '%s\n' "${candidate}"
                    return 0
                fi
            done < <(blkid -t "PARTLABEL=${label}" -o device 2>/dev/null || true)
            candidate="$(lsblk -nrpo NAME,PARTLABEL "${disk}" 2>/dev/null \
                | awk -v wanted="${label}" '$2 == wanted {print $1; exit}')"
            if [[ "${candidate}" == /dev/* && -b "${candidate}" ]]; then
                printf '%s\n' "$(readlink -f -- "${candidate}" 2>/dev/null || printf '%s' "${candidate}")"
                return 0
            fi
            sleep 1
        done
        return 1
    }
    udevadm settle --timeout=10 2>/dev/null || true
    target_disk="$(physical_disk_for_device "${root_source}")"
    [[ -n "${target_disk}" && "${target_disk}" != *$'\n'* ]] || {
        echo "ERROR: cannot identify one OTA-ready target disk" >&2; return 1;
    }
    esp_device="$(partlabel_device_on_disk MING-ESP "${target_disk}" || true)"
    boot_device="$(partlabel_device_on_disk MING-BOOT "${target_disk}" || true)"
    root_a_device="$(partlabel_device_on_disk MING-ROOT-A "${target_disk}" || true)"
    root_b_device="$(partlabel_device_on_disk MING-ROOT-B "${target_disk}" || true)"
    home_device="$(partlabel_device_on_disk MING-HOME "${target_disk}" || true)"
    if [[ "${esp_device}" == /dev/* ]]; then
        esp_device="$(readlink -f -- "${esp_device}" 2>/dev/null || true)"
    fi
    if [[ "${boot_device}" == /dev/* ]]; then
        boot_device="$(readlink -f -- "${boot_device}" 2>/dev/null || true)"
    fi
    if [[ "${root_a_device}" == /dev/* ]]; then
        root_a_device="$(readlink -f -- "${root_a_device}" 2>/dev/null || true)"
    fi
    if [[ "${root_b_device}" == /dev/* ]]; then
        root_b_device="$(readlink -f -- "${root_b_device}" 2>/dev/null || true)"
    fi
    if [[ "${home_device}" == /dev/* ]]; then
        home_device="$(readlink -f -- "${home_device}" 2>/dev/null || true)"
    fi
    mounted_from_device() {
        local expected_device="$1" mountpoint="$2" mounted_source
        mounted_source="$(findmnt -nro SOURCE --target "${mountpoint}" 2>/dev/null || true)"
        if [[ "${mounted_source}" == /dev/* ]]; then
            mounted_source="$(readlink -f -- "${mounted_source}" 2>/dev/null || true)"
        fi
        [[ "${mounted_source}" == "${expected_device}" ]]
    }
    for device in "${esp_device}" "${boot_device}" "${root_a_device}" "${root_b_device}" "${home_device}"; do
        [[ "${device}" == /dev/* && -b "${device}" ]] || {
            echo "ERROR: OTA-ready partition labels are incomplete" >&2; return 1;
        }
    done
    for device in "${esp_device}" "${boot_device}" "${root_a_device}" "${root_b_device}" "${home_device}"; do
        candidate_disk="$(physical_disk_for_device "${device}")"
        [[ "${candidate_disk}" == "${target_disk}" ]] || {
            echo "ERROR: OTA-ready labels resolve outside the selected target disk" >&2; return 1;
        }
    done
    esp_fstype="$(blkid -s TYPE -o value "${esp_device}" 2>/dev/null | head -n 1 | tr '[:upper:]' '[:lower:]')"
    case "${esp_fstype}" in
        vfat|fat|fat32) ;;
        *) echo "ERROR: MING-ESP must be a FAT/vfat EFI System Partition" >&2; return 1 ;;
    esac
    esp_parttype="$(lsblk -ndo PARTTYPE "${esp_device}" 2>/dev/null | head -n 1 | tr '[:upper:]' '[:lower:]')"
    case "${esp_parttype}" in
        c12a7328-f81f-11d2-ba4b-00a0c93ec93b|0xef|ef) ;;
        *) echo "ERROR: MING-ESP PARTTYPE is not an EFI System Partition" >&2; return 1 ;;
    esac
    esp_mount_source="$(findmnt -nro SOURCE --target "${target}/boot/efi" 2>/dev/null || true)"
    if [[ "${esp_mount_source}" == /dev/* ]]; then
        esp_mount_source="$(readlink -f -- "${esp_mount_source}" 2>/dev/null || true)"
    fi
    [[ "${esp_mount_source}" == "${esp_device}" ]] || {
        echo "ERROR: target /boot/efi is not mounted from MING-ESP" >&2; return 1;
    }
    root_a_uuid="$(blkid -s UUID -o value "${root_a_device}")"
    root_b_uuid="$(blkid -s UUID -o value "${root_b_device}")"
    esp_uuid="$(blkid -s UUID -o value "${esp_device}")"
    boot_uuid="$(blkid -s UUID -o value "${boot_device}")"
    home_uuid="$(blkid -s UUID -o value "${home_device}")"
    for filesystem_uuid in "${root_a_uuid}" "${root_b_uuid}" "${esp_uuid}" "${boot_uuid}" "${home_uuid}"; do
        [[ "${filesystem_uuid}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
            echo "ERROR: OTA-ready filesystem UUID readback is invalid" >&2; return 1;
        }
    done
    unique_count="$(printf '%s\n' "${root_a_uuid}" "${root_b_uuid}" "${esp_uuid}" "${boot_uuid}" "${home_uuid}" | sort -u | wc -l)"
    [[ "${unique_count}" -eq 5 && "${root_uuid}" == "${root_a_uuid}" ]] || {
        echo "ERROR: OTA-ready UUID readback failed" >&2; return 1;
    }
    mounted_from_device "${boot_device}" "${target}/boot" || {
        echo "ERROR: target /boot is not mounted from MING-BOOT" >&2; return 1;
    }
    mounted_from_device "${home_device}" "${target}/home" || {
        echo "ERROR: target /home is not mounted from MING-HOME" >&2; return 1;
    }
    mounted_from_device "${esp_device}" "${target}/boot/efi" || {
        echo "ERROR: target /boot/efi is not mounted from MING-ESP" >&2; return 1;
    }
    fstab_uses_uuid() {
        local filesystem_uuid="$1" mountpoint="$2"
        awk -v source="UUID=${filesystem_uuid}" -v target_mount="${mountpoint}" '
            $0 !~ /^[[:space:]]*#/ && $1 == source && $2 == target_mount { matches++ }
            END { exit matches == 1 ? 0 : 1 }
        ' "${target}/etc/fstab"
    }
    fstab_uses_uuid "${boot_uuid}" "/boot" || {
        echo "ERROR: target fstab /boot UUID does not match MING-BOOT" >&2; return 1;
    }
    fstab_uses_uuid "${home_uuid}" "/home" || {
        echo "ERROR: target fstab /home UUID does not match MING-HOME" >&2; return 1;
    }
    fstab_uses_uuid "${esp_uuid}" "/boot/efi" || {
        echo "ERROR: target fstab /boot/efi UUID does not match MING-ESP" >&2; return 1;
    }
    mkdir -p "${target}/etc/ming-update"
    cat > "${target}/etc/ming-update/slots.json" <<SLOTS
{"schema":1,"layout":"ming-ab-v1","slots":{"A":{"device":"/dev/disk/by-uuid/${root_a_uuid}","uuid":"${root_a_uuid}","grub_entry":"Ming OS 高级启动>Ming OS slot A"},"B":{"device":"/dev/disk/by-uuid/${root_b_uuid}","uuid":"${root_b_uuid}","grub_entry":"Ming OS 高级启动>Ming OS slot B"}},"boot":{"device":"/dev/disk/by-uuid/${boot_uuid}","uuid":"${boot_uuid}"},"home":{"device":"/dev/disk/by-uuid/${home_uuid}","uuid":"${home_uuid}"}}
SLOTS
    printf 'A\n' > "${target}/etc/ming-ota-slot"
    printf 'ming-ab-v1\n' > "${target}/etc/ming-update/ota-ready"
    chmod 0600 "${target}/etc/ming-update/slots.json"
    chmod 0644 "${target}/etc/ming-ota-slot" "${target}/etc/ming-update/ota-ready"
    OTA_ROOT_A_UUID="${root_a_uuid}"
    OTA_ROOT_B_UUID="${root_b_uuid}"
    OTA_BOOT_UUID="${boot_uuid}"
}

mkdir -p "${target}/etc/ming-update"
# This policy contains no secret and must be readable by the unprivileged
# status/check commands so a preserved dual-boot install can explain why major
# A/B OTA is disabled. It remains root-owned and is written atomically.
install -m 0644 "${install_mode_state}" "${target}/etc/ming-update/install-mode.json" || exit 30
case "${install_mode}" in
    blank_ab)
        write_ota_ready_layout || exit 30
        ;;
    dual_boot_preserve)
        # A preserved dual-boot install has one Ming root.  Advertising slot
        # state here would let a major OTA overwrite an unrelated partition.
        rm -f "${target}/etc/ming-update/slots.json" \
            "${target}/etc/ming-update/ota-ready" \
            "${target}/etc/ming-ota-slot"
        ;;
esac

write_file() {
    local path="$1"
    shift
    mkdir -p "$(dirname "${target}${path}")"
    cat > "${target}${path}"
}

validate_posix_user_name() {
    [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$1" != "root" && "$1" != "nobody" ]]
}

passwd_record_for_user() {
    local user_name="$1"
    chroot "${target}" getent passwd "${user_name}" 2>/dev/null | head -n 1 || true
}

resolve_primary_user() {
    local candidates count resolved
    candidates="$(chroot "${target}" getent passwd 2>/dev/null \
        | awk -F: '$1 ~ /^[a-z_][a-z0-9_-]{0,31}$/ && $1 != "nobody" && $1 !~ /^systemd-/ && $3 >= 1000 && $3 < 60000 {print $3 ":" $1}' \
        | sort -t: -k1,1n || true)"
    count="$(printf '%s\n' "${candidates}" | awk 'NF {count++} END {print count+0}')"
    if [[ "${count}" -gt 0 ]]; then
        resolved="$(printf '%s\n' "${candidates}" | awk -F: 'NF {print $2; exit}')"
        if [[ "${count}" -gt 1 ]]; then
            echo "ERROR: multiple local primary users found; refusing to guess an administrator" >&2
            return 1
        fi
        if validate_posix_user_name "${resolved}"; then
            printf '%s\n' "${resolved}"
            return 0
        fi
    fi
    printf 'user\n'
}

resolve_user_home() {
    local user_name="$1" home record
    record="$(passwd_record_for_user "${user_name}")"
    home="$(awk -F: 'NR == 1 {print $6}' <<<"${record}" || true)"
    case "${home}" in
        /home/*) ;;
        *) home="/home/${user_name}" ;;
    esac
    case "${home}" in
        *'/../'*|*'/./'*|'') home="/home/${user_name}" ;;
    esac
    printf '%s\n' "${home}"
}

ensure_ming_user() {
    local user_name
    user_name="$(resolve_primary_user)" || return 1
    validate_posix_user_name "${user_name}" || { echo "ERROR: resolved primary user is invalid" >&2; return 1; }
    local user_home
    user_home="$(resolve_user_home "${user_name}")"
    local groups=(
        users adm cdrom dip plugdev lp lpadmin netdev audio video render input
        scanner bluetooth sudo nopasswdlogin autologin
    )
    local grp

    mkdir -p "${target}/etc/sudoers.d" "${target}/etc/lightdm/lightdm.conf.d" "${target}${user_home}"

    for grp in "${groups[@]}"; do
        chroot "${target}" getent group "${grp}" >/dev/null 2>&1 \
            || chroot "${target}" groupadd -r "${grp}" >/dev/null 2>&1 \
            || true
    done
    chroot "${target}" getent group sudo >/dev/null 2>&1 \
        || chroot "${target}" groupadd -r sudo >/dev/null 2>&1 \
        || return 1
    if chroot "${target}" getent passwd "${user_name}" >/dev/null 2>&1; then
        chroot "${target}" usermod -d "${user_home}" -s /bin/bash -c "Ming OS User" "${user_name}" >/dev/null 2>&1 || true
    else
        chroot "${target}" useradd -m -d "${user_home}" -s /bin/bash -c "Ming OS User" "${user_name}" >/dev/null 2>&1 || true
    fi
    # The active-session bootstrap policy sets the first password after first
    # boot. Keep the account locked until then, but preserve sudo membership so
    # installed systems are maintainable as soon as the user finishes OOBE.
    chroot "${target}" passwd -l "${user_name}" >/dev/null 2>&1 || return 1
    chroot "${target}" passwd -l root >/dev/null 2>&1 || return 1

    for grp in "${groups[@]}"; do
        chroot "${target}" getent group "${grp}" >/dev/null 2>&1 \
            && chroot "${target}" usermod -aG "${grp}" "${user_name}" >/dev/null 2>&1 \
            || true
    done
    if ! chroot "${target}" id -nG "${user_name}" 2>/dev/null | tr ' ' '\n' | grep -Fxq sudo; then
        echo "ERROR: installed primary user must belong to sudo group" >&2
        return 1
    fi

    chroot "${target}" chown "${user_name}:${user_name}" "${user_home}" >/dev/null 2>&1 || true
    MING_PRIMARY_USER="${user_name}"
    MING_PRIMARY_HOME="${user_home}"
}

ensure_kernel_boot_links() {
    local kernel initrd version
    kernel="$(find "${target}/boot" -maxdepth 1 -type f -name 'vmlinuz-*' 2>/dev/null | sort -V | tail -n 1 || true)"
    [[ -n "${kernel}" ]] || return 0
    version="${kernel##*/vmlinuz-}"
    initrd="${target}/boot/initrd.img-${version}"
    [[ -s "${initrd}" ]] || initrd="$(find "${target}/boot" -maxdepth 1 -type f -name 'initrd.img-*' 2>/dev/null | sort -V | tail -n 1 || true)"
    ln -sfn "boot/$(basename "${kernel}")" "${target}/vmlinuz" 2>/dev/null || true
    if [[ -n "${initrd}" && -s "${initrd}" ]]; then
        ln -sfn "boot/$(basename "${initrd}")" "${target}/initrd.img" 2>/dev/null || true
    fi
}

cmdline_value() {
    local key="$1" token
    for token in $(cat /proc/cmdline 2>/dev/null); do
        case "${token}" in
            "${key}"=*) printf '%s\n' "${token#*=}"; return 0 ;;
        esac
    done
    return 1
}

restore_ota_home() {
    grep -qw 'ming.ota=1' /proc/cmdline 2>/dev/null || return 0

    local backup_uuid manifest_arg manifest_rel backup_device mount_dir manifest_path engine strategy manifest_uuid home_fstype
    backup_uuid="$(cmdline_value ming.ota_backup_uuid || true)"
    manifest_arg="$(cmdline_value ming.ota_manifest || true)"
    mkdir -p "${target}/var/log"
    exec 8>>"${target}/var/log/ming-ota-restore.log"
    printf '[%s] OTA restore requested\n' "$(date -Is)" >&8

    if [[ -z "${backup_uuid}" || -z "${manifest_arg}" ]]; then
        echo "ERROR: OTA restore parameters are incomplete" >&8
        return 31
    fi
    if [[ ! -s /run/ming-ota-preflight.ok ]] \
        || ! grep -Fxq "uuid=${backup_uuid}" /run/ming-ota-preflight.ok \
        || ! grep -Fxq "manifest=${manifest_arg}" /run/ming-ota-preflight.ok; then
        echo "ERROR: destructive OTA did not pass the pre-partition verification gate" >&8
        return 31
    fi
    manifest_rel="${manifest_arg#/}"
    case "/${manifest_rel}/" in
        */../*|*/./*) echo "ERROR: unsafe OTA manifest path" >&8; return 31 ;;
    esac

    backup_device="$(blkid -U "${backup_uuid}" 2>/dev/null | head -n 1 || true)"
    if [[ -z "${backup_device}" || ! -b "${backup_device}" ]]; then
        echo "ERROR: OTA backup disk UUID was not found: ${backup_uuid}" >&8
        return 31
    fi

    mount_dir="/run/ming-ota-backup"
    mkdir -p "${mount_dir}"
    if mountpoint -q "${mount_dir}" 2>/dev/null; then
        umount "${mount_dir}" || return 31
    fi
    if ! mount -o ro "${backup_device}" "${mount_dir}"; then
        echo "ERROR: could not mount OTA backup disk read-only" >&8
        return 31
    fi

    manifest_path="$(readlink -f "${mount_dir}/${manifest_rel}" 2>/dev/null || true)"
    if [[ -z "${manifest_path}" || "${manifest_path}" != "${mount_dir}/"* \
        || ! -s "${manifest_path}" || -L "${manifest_path}" ]]; then
        echo "ERROR: OTA manifest is missing or outside the backup mount" >&8
        umount "${mount_dir}" || true
        return 31
    fi

    strategy="$(jq -r '.strategy // "completed_backup"' "${manifest_path}" 2>/dev/null || true)"
    manifest_uuid="$(jq -r '.backup_uuid // .disk_uuid // ""' "${manifest_path}" 2>/dev/null || true)"
    if [[ "${strategy}" == "separate_home" ]]; then
        if [[ "$(jq -r '.complete // false' "${manifest_path}" 2>/dev/null)" != "true" \
            || "${manifest_uuid}" != "${backup_uuid}" ]]; then
            echo "ERROR: separate /home preservation manifest is invalid" >&8
            umount "${mount_dir}" || true
            return 31
        fi
        home_fstype="$(blkid -s TYPE -o value "${backup_device}" 2>/dev/null | head -n 1 || true)"
        if [[ -z "${home_fstype}" ]]; then
            echo "ERROR: separate /home filesystem type is unknown" >&8
            umount "${mount_dir}" || true
            return 31
        fi
        mkdir -p "${target}/home" "${target}/etc"
        touch "${target}/etc/fstab"
        if ! grep -Eq '^[^#]+[[:space:]]+/home[[:space:]]' "${target}/etc/fstab"; then
            printf 'UUID=%s /home %s defaults,nofail,x-systemd.device-timeout=10 0 2\n' \
                "${backup_uuid}" "${home_fstype}" >> "${target}/etc/fstab"
        fi
        echo "separate /home preservation plan accepted: UUID=${backup_uuid} /home" >&8
        umount "${mount_dir}" || true
        return 0
    fi

    engine="${target}/usr/local/sbin/ming-ota-backup"
    if [[ ! -x "${engine}" ]]; then
        engine="/usr/local/sbin/ming-ota-backup"
    fi
    if [[ ! -x "${engine}" ]]; then
        echo "ERROR: ming-ota-backup restore engine is missing" >&8
        umount "${mount_dir}" || true
        return 31
    fi

    if ! "${engine}" verify --manifest "${manifest_path}" >&8 2>&8; then
        echo "ERROR: OTA backup verification failed" >&8
        umount "${mount_dir}" || true
        return 31
    fi
    if [[ -L "${target}/home" ]]; then
        echo "ERROR: OTA restore target /home must not be a symbolic link" >&8
        umount "${mount_dir}" || true
        return 31
    fi
    mkdir -p "${target}/home"
    if ! "${engine}" restore --manifest "${manifest_path}" --target "${target}/home" \
        --system-target "${target}" >&8 2>&8; then
        echo "ERROR: ming-ota-backup restore failed" >&8
        umount "${mount_dir}" || true
        return 31
    fi
    sync
    umount "${mount_dir}" || true
    printf '[%s] OTA restore completed; backup retained on %s\n' "$(date -Is)" "${backup_uuid}" >&8
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

write_file /etc/ming-release <<MINGRELEASE
Ming OS ${version} Home Edition
MINGRELEASE

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

restore_ota_home || exit $?
ensure_ming_user || exit 30
ensure_kernel_boot_links

kernel="$(find "${target}/boot" -maxdepth 1 -type f -name 'vmlinuz-*' 2>/dev/null | sort -V | tail -n 1 || true)"
initrd="$(find "${target}/boot" -maxdepth 1 -type f -name 'initrd.img-*' 2>/dev/null | sort -V | tail -n 1 || true)"
[[ -s "${kernel}" && -s "${initrd}" ]] || { echo "ERROR: installed boot payload is missing" >&2; exit 30; }
if [[ "${install_mode}" == "blank_ab" ]]; then
    # Initial installation must seed both shared-boot slot payloads.  Slot B is
    # not the default root, but it must remain bootable before the first OTA.
    install -d -m 0755 "${target}/boot/ming-slots/A" "${target}/boot/ming-slots/B"
    install -m 0644 "${kernel}" "${target}/boot/ming-slots/A/vmlinuz"
    install -m 0644 "${initrd}" "${target}/boot/ming-slots/A/initrd.img"
    install -m 0644 "${kernel}" "${target}/boot/ming-slots/B/vmlinuz"
    install -m 0644 "${initrd}" "${target}/boot/ming-slots/B/initrd.img"
    # seed both shared-boot slot payloads before GRUB generation
else
    rm -rf "${target}/boot/ming-slots"
fi

# Compact installed menu: A/B and hardware recovery entries live below one
# submenu so OTA can still address the named entries without top-level noise.
# submenu 'Ming OS 高级启动' | menuentry 'Ming OS slot A' | menuentry 'Ming OS slot B'
mkdir -p "${target}/etc/grub.d"
if [[ "${install_mode}" == "blank_ab" ]]; then
cat > "${target}/etc/grub.d/09_ming_os" <<'TARGETGRUBENTRY'
#!/bin/sh
set -e

cat <<'EOF'
menuentry 'Ming OS' --class ming --class gnu-linux --class gnu --class os {
    load_video
    insmod gzio
    insmod part_msdos
    insmod part_gpt
    insmod ext2
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
    initrd /initrd.img
}

submenu 'Ming OS 高级启动' --class ming --class gnu-linux --class os {
    menuentry 'Ming OS slot A' --class ming --class gnu-linux --class os {
        search --no-floppy --fs-uuid --set=root __MING_BOOT_UUID__
        linux /ming-slots/A/vmlinuz root=UUID=__MING_ROOT_A_UUID__ ro quiet loglevel=3 systemd.show_status=false
        initrd /ming-slots/A/initrd.img
    }
    menuentry 'Ming OS slot B' --class ming --class gnu-linux --class os {
        search --no-floppy --fs-uuid --set=root __MING_BOOT_UUID__
        linux /ming-slots/B/vmlinuz root=UUID=__MING_ROOT_B_UUID__ ro quiet loglevel=3 systemd.show_status=false
        initrd /ming-slots/B/initrd.img
    }
    menuentry 'Ming OS (Safe Graphics)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog nomodeset vga=791
        initrd /initrd.img
    }

    menuentry 'Ming OS (Old Intel / ThinkPad / MacBook)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
        initrd /initrd.img
    }

    menuentry 'Ming OS (Radeon Legacy Recovery)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog radeon.modeset=1 amdgpu.modeset=0
        initrd /initrd.img
    }

    menuentry 'Ming OS (Radeon GCN Recovery SI/CIK)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog amdgpu.si_support=1 radeon.si_support=0 amdgpu.cik_support=1 radeon.cik_support=0
        initrd /initrd.img
    }
}
EOF
TARGETGRUBENTRY
else
cat > "${target}/etc/grub.d/09_ming_os" <<'TARGETGRUBSINGLE'
#!/bin/sh
set -e

cat <<'EOF'
    menuentry 'Ming OS' --class ming --class gnu-linux --class gnu --class os {
    load_video
    insmod gzio
    insmod part_msdos
    insmod part_gpt
    insmod ext2
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
    initrd /initrd.img
}

    submenu 'Ming OS 高级启动' --class ming --class gnu-linux --class os {
    menuentry 'Ming OS (Safe Graphics)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog nomodeset vga=791
        initrd /initrd.img
    }
    menuentry 'Ming OS (Old Intel / ThinkPad / MacBook)' --class ming --class gnu-linux --class gnu --class os {
        load_video
        insmod gzio
        insmod part_msdos
        insmod part_gpt
        insmod ext2
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
        initrd /initrd.img
    }
}
EOF
TARGETGRUBSINGLE
fi
chmod 0755 "${target}/etc/grub.d/09_ming_os" 2>/dev/null || true

grub_template="${target}/etc/grub.d/09_ming_os"
if [[ ! -s "${grub_template}" ]] || ! grep -Fq '__MING_ROOT_UUID__' "${grub_template}"; then
    echo "ERROR: Ming GRUB template is missing the required root UUID placeholder" >&2
    exit 30
fi
if ! sed -i "s/__MING_ROOT_UUID__/${root_uuid}/g" "${grub_template}"; then
    echo "ERROR: failed to write the authoritative root UUID into the Ming GRUB template" >&2
    exit 30
fi
if [[ "${install_mode}" == "blank_ab" ]]; then
    sed -i "s/__MING_ROOT_A_UUID__/${OTA_ROOT_A_UUID}/g; s/__MING_ROOT_B_UUID__/${OTA_ROOT_B_UUID}/g; s/__MING_BOOT_UUID__/${OTA_BOOT_UUID}/g" "${grub_template}" || exit 30
    sed -i \
        -e "s|search --no-floppy --set=root --file /vmlinuz|search --no-floppy --fs-uuid --set=root ${OTA_BOOT_UUID}|g" \
        -e "s|linux /vmlinuz root=UUID=${root_uuid}|linux /ming-slots/A/vmlinuz root=UUID=${root_uuid}|g" \
        -e "s|initrd /initrd.img|initrd /ming-slots/A/initrd.img|g" \
        "${grub_template}" || exit 30
    grep -Fq "root=UUID=${OTA_ROOT_A_UUID}" "${grub_template}" || exit 30
    grep -Fq "root=UUID=${OTA_ROOT_B_UUID}" "${grub_template}" || exit 30
fi
if grep -Fq '__MING_ROOT_UUID__' "${grub_template}"; then
    echo "ERROR: Ming GRUB template still contains __MING_ROOT_UUID__" >&2
    exit 30
fi
if ! grep -Fq "root=UUID=${root_uuid}" "${grub_template}"; then
    echo "ERROR: Ming GRUB template does not contain the authoritative root UUID" >&2
    exit 30
fi

# The Live root already carries the audited detector asset.  Copy that exact
# helper into the installed target so Live and installed GRUB use one rule.
install -d -m 0755 "${target}/usr/local/sbin"
if [[ ! -x "/usr/local/sbin/ming-detect-other-os" ]]; then
    echo "ERROR: Ming other-OS detector is missing from the Live root" >&2
    exit 30
fi
install -m 0755 /usr/local/sbin/ming-detect-other-os \
    "${target}/usr/local/sbin/ming-detect-other-os"

for noisy_grub in "10_linux" 20_linux_xen 30_os-prober 30_uefi-firmware; do
    if [[ -f "${target}/etc/grub.d/${noisy_grub}" ]]; then
        # The authoritative Ming generator owns the compact menu.  Debian's
        # generic generator would add a second top-level entry for every
        # installed kernel, so leave it shipped but non-executable.
        chmod 0644 "${target}/etc/grub.d/${noisy_grub}" 2>/dev/null || true
    fi
done

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
GRUB_DEFAULT=saved
GRUB_SAVEDEFAULT=false
# 老旧硬件友好 + 隐藏内核日志：安静启动、低日志级别、隐藏 systemd 状态刷屏
GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
GRUB_TERMINAL_INPUT=console
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_RECORDFAIL_TIMEOUT=0
GRUB_DISABLE_SUBMENU=false
GRUB_DISABLE_OS_PROBER=true
GRUB_DISABLE_RECOVERY=true
GRUBCFG

mkdir -p "${target}/usr/share"
ln -sf /etc/ming-release "${target}/usr/share/ming-release" 2>/dev/null || true

mkdir -p "${target}/etc"
cat > "${target}/etc/machine-info" <<MACHINEINFO
PRETTY_HOSTNAME="Ming OS"
MACHINEINFO

mkdir -p "${target}/etc/lightdm/lightdm.conf.d"
cat > "${target}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf" <<LIGHTDM
[Seat:*]
autologin-user=${MING_PRIMARY_USER}
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
LIGHTDM

# The installed system should boot directly to the graphical desktop. Some
# installer paths leave systemd on multi-user.target, which is why a healthy
# install can still land on tty1 after reboot.
if chroot "${target}" systemctl list-unit-files lightdm.service >/dev/null 2>&1; then
    chroot "${target}" systemctl enable lightdm.service >/dev/null 2>&1 || true
    mkdir -p "${target}/etc/systemd/system"
    ln -sfn /lib/systemd/system/lightdm.service "${target}/etc/systemd/system/display-manager.service" 2>/dev/null || true
fi
if chroot "${target}" systemctl list-unit-files graphical.target >/dev/null 2>&1; then
    chroot "${target}" systemctl set-default graphical.target >/dev/null 2>&1 || true
else
    ln -sfn /lib/systemd/system/graphical.target "${target}/etc/systemd/system/default.target" 2>/dev/null || true
fi

# The target inherits the named Polkit helpers from the unpacked Live rootfs.
# Remove any legacy global sudo grant left by a resumed 26.3-era installation.
rm -f "${target}/etc/sudoers.d/user" "${target}/etc/sudoers.d/ming"

# The installed system is produced by unpacking the Live filesystem. Restore
# the real util-linux binary before removing Live-only installer components.
if [[ -x "${target}/usr/lib/ming-os/sfdisk.real" ]]; then
    install -m 0755 "${target}/usr/lib/ming-os/sfdisk.real" "${target}/usr/sbin/sfdisk"
    rm -f "${target}/usr/lib/ming-os/sfdisk.real"
fi

rm -f \
    "${target}/usr/share/applications/calamares.desktop" \
    "${target}/usr/share/applications/calamares-install-debian.desktop" \
    "${target}/usr/share/applications/Install Ming OS.desktop" \
    "${target}/usr/share/xsessions/ming-installer.desktop" \
    "${target}/usr/local/sbin/ming-live-installer-root" \
    "${target}/usr/share/polkit-1/actions/org.ming.live.installer.policy" \
    "${target}/usr/local/sbin/sfdisk" \
    "${target}/etc/systemd/system/ming-live-installer.service" \
    "${target}/etc/systemd/system/graphical.target.wants/ming-live-installer.service" \
    2>/dev/null || true

for installer_entry in \
    "${target}"/home/*/.config/autostart/calamares-live.desktop \
    "${target}"/home/*/Desktop/calamares.desktop \
    "${target}"/home/*/Desktop/calamares-install-debian.desktop \
    "${target}"/home/*/Desktop/install-debian.desktop \
    "${target}"/home/*/Desktop/"Install Debian.desktop" \
    "${target}"/home/*/Desktop/"Install Ming OS.desktop" \
    "${target}"/home/*/Desktop/"安装 Debian.desktop" \
    "${target}"/home/*/桌面/calamares.desktop \
    "${target}"/home/*/桌面/calamares-install-debian.desktop \
    "${target}"/home/*/桌面/"Install Ming OS.desktop" \
    "${target}"/home/*/桌面/"安装 Debian.desktop" \
    "${target}"/etc/skel/.config/autostart/calamares-live.desktop \
    "${target}"/etc/skel/Desktop/calamares.desktop \
    "${target}"/etc/skel/Desktop/calamares-install-debian.desktop \
    "${target}"/etc/skel/Desktop/install-debian.desktop \
    "${target}"/etc/skel/Desktop/"Install Debian.desktop" \
    "${target}"/etc/skel/Desktop/"Install Ming OS.desktop" \
    "${target}"/etc/skel/Desktop/"安装 Debian.desktop" \
    "${target}"/etc/skel/桌面/calamares.desktop \
    "${target}"/etc/skel/桌面/calamares-install-debian.desktop \
    "${target}"/etc/skel/桌面/"Install Ming OS.desktop" \
    "${target}"/etc/skel/桌面/"安装 Debian.desktop"; do
    [[ -e "${installer_entry}" ]] && rm -f "${installer_entry}" 2>/dev/null || true
done

# 已安装系统只由 NetworkManager 管理网络；legacy networking/systemd-networkd
# 不参与普通桌面连接，避免同一网卡被重复管理后反复断开。
if [ -f "${target}/usr/lib/systemd/system/NetworkManager.service" ] || \
   [ -f "${target}/lib/systemd/system/NetworkManager.service" ]; then
    chroot "${target}" systemctl enable NetworkManager >/dev/null 2>&1 || true
fi
chroot "${target}" systemctl disable networking.service >/dev/null 2>&1 || true
chroot "${target}" systemctl disable systemd-networkd.service >/dev/null 2>&1 || true
# 不强制加载具体以太网或 Wi-Fi 模块。驱动选择交给内核 modalias/udev；
# 这里保留空的受管文件，便于 OTA 清理旧版本留下的强制预加载项。
mkdir -p "${target}/etc/modules-load.d"
cat > "${target}/etc/modules-load.d/ming-network.conf" << 'NETMOD'
# Ming OS: NetworkManager owns networking; kernel modalias/udev selects drivers.
NETMOD
# 确保固件被 initramfs 包含（update-initramfs 已在前面运行）
chroot "${target}" depmod -a 2>/dev/null || true
MINGIDENTITY
    chmod +x /usr/local/sbin/ming-fix-installed-identity

    cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'
#!/usr/bin/env bash
set -euo pipefail

LOG=/run/ming-installer/bootloader.log
install -d -m 0755 /run/ming-installer
: >"${LOG}"
chmod 0644 "${LOG}"
exec > >(tee -a "${LOG}") 2>&1

echo "==== Ming bootloader install $(date -Is) ===="
echo "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null || true

resolve_verified_target() {
    local result candidate receipt_target
    result="$(/usr/local/sbin/ming-installer-verify installed --receipt)" || {
        printf '%s\n' "${result}" >&2
        return 1
    }
    candidate="$(printf '%s' "${result}" | python3 -c '
import json
import sys
payload = json.load(sys.stdin)
target = payload.get("target")
if not payload.get("ok") or not isinstance(target, str) or not target or target == "/":
    raise SystemExit(1)
print(target)
')" || return 1
    receipt_target="$(/usr/local/sbin/ming-installer-verify receipt --field target)" || return 1
    [[ "${candidate}" == "${receipt_target}" ]] || {
        echo "ERROR: installed verifier target does not match the authoritative receipt" >&2
        return 1
    }
    [[ -d "${candidate}/boot" && -f "${candidate}/etc/fstab" ]] || return 1
    printf '%s\n' "${candidate}"
}

root="$(resolve_verified_target)" || exit 20
echo "target_root=${root}"

root_source="$(/usr/local/sbin/ming-installer-verify receipt --field source)" || exit 20
root_uuid="$(/usr/local/sbin/ming-installer-verify receipt --field uuid)" || exit 20
install_mode="$(jq -er '.mode' "${root}/etc/ming-update/install-mode.json")" || exit 20
major_ota="$(jq -er '.major_ota' "${root}/etc/ming-update/install-mode.json")" || exit 20
case "${install_mode}:${major_ota}" in
    blank_ab:ab_slot|dual_boot_preserve:disabled_dual_boot) ;;
    *) echo "ERROR: installed mode receipt is inconsistent"; exit 20 ;;
esac
slot_b_uuid=""
if [[ "${install_mode}" == "blank_ab" ]]; then
    slot_b_uuid="$(python3 -c '
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["slots"]["B"]["uuid"]
if not isinstance(value, str) or not value:
    raise SystemExit(1)
print(value)
' "${root}/etc/ming-update/slots.json")" || exit 20
fi
current_root_source="$(findmnt -n -o SOURCE --target "${root}" 2>/dev/null || true)"
[[ "${current_root_source}" == "${root_source}" ]] || {
    echo "ERROR: authoritative receipt source no longer matches the mounted target"
    exit 20
}
if ! grep -Fq "root=UUID=${root_uuid}" "${root}/etc/grub.d/09_ming_os" \
    || grep -Eq '__MING_(ROOT|BOOT).*UUID__' "${root}/etc/grub.d/09_ming_os"; then
    echo "ERROR: target GRUB template does not contain the authoritative root UUID"
    exit 20
fi
echo "root_source=${root_source}"
resolve_boot_disk() {
    local root_source="$1" name type
    local -a physical_disks=()

    [ -n "${root_source}" ] && [ -b "${root_source}" ] || {
        echo "ERROR: cannot resolve target root block device for GRUB"
        return 1
    }

    while read -r name type; do
        [ "${type}" = disk ] || continue
        [[ " ${physical_disks[*]-} " == *" ${name} "* ]] || physical_disks+=("${name}")
    done < <(lsblk -s -nrpo NAME,TYPE "${root_source}" 2>/dev/null || true)

    if [ "${#physical_disks[@]}" -ne 1 ]; then
        echo "ERROR: expected one physical GRUB disk for ${root_source}, found ${#physical_disks[@]}"
        return 1
    fi
    boot_disk="${physical_disks[0]}"
    [ -b "${boot_disk}" ] || {
        echo "ERROR: resolved GRUB disk is not a block device: ${boot_disk}"
        return 1
    }
}

boot_disk=""
resolve_boot_disk "${root_source}" || exit 20
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
    local esp_source esp_fstype esp_parttype
    if ! mountpoint -q "${root}/boot/efi"; then
        echo "ERROR: UEFI firmware detected but target /boot/efi is not a real mount"
        return 1
    fi
    esp_source="$(findmnt -nro SOURCE --target "${root}/boot/efi" 2>/dev/null || true)"
    esp_fstype="$(findmnt -nro FSTYPE --target "${root}/boot/efi" 2>/dev/null || true)"
    [[ "${esp_source}" == /dev/* && -b "${esp_source}" ]] || {
        echo "ERROR: target ESP SOURCE is not an independent block device"
        return 1
    }
    case "${esp_fstype}" in
        vfat|fat|fat32) ;;
        *) echo "ERROR: target ESP FSTYPE must be FAT/vfat, got ${esp_fstype:-unknown}"; return 1 ;;
    esac
    esp_parttype="$(lsblk -ndo PARTTYPE "${esp_source}" 2>/dev/null | head -n 1 | tr '[:upper:]' '[:lower:]')"
    case "${esp_parttype}" in
        c12a7328-f81f-11d2-ba4b-00a0c93ec93b|0xef|ef) ;;
        *) echo "ERROR: target ESP PARTTYPE is not an EFI System Partition"; return 1 ;;
    esac
    mkdir -p "${root}/boot/efi/EFI/Ming" "${root}/boot/efi/EFI/BOOT"
    if [ -x "${root}/usr/sbin/grub-install" ]; then
        chroot "${root}" /usr/sbin/grub-install \
            --target=x86_64-efi \
            --efi-directory=/boot/efi \
            --bootloader-id="Ming OS" \
            --recheck || echo "WARN: UEFI NVRAM grub-install failed; keeping removable fallback"
        chroot "${root}" /usr/sbin/grub-install \
            --target=x86_64-efi \
            --efi-directory=/boot/efi \
            --bootloader-id="Ming OS" \
            --recheck \
            --removable \
            --no-nvram
    elif command -v grub-install >/dev/null 2>&1; then
        grub-install \
            --target=x86_64-efi \
            --efi-directory="${root}/boot/efi" \
            --boot-directory="${root}/boot" \
            --bootloader-id="Ming OS" \
            --recheck || echo "WARN: UEFI NVRAM grub-install failed; keeping removable fallback"
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
    local modules="part_gpt part_msdos ext2 search search_fs_uuid normal configfile linux"
    local pttype bios_boot_count
    pttype="$(lsblk -ndo PTTYPE "${boot_disk}" 2>/dev/null | head -n 1 | tr '[:upper:]' '[:lower:]')"
    if [[ "${pttype}" == "gpt" ]]; then
        bios_boot_count="$(lsblk -nrpo NAME,TYPE,PARTTYPE,PARTLABEL "${boot_disk}" 2>/dev/null \
            | awk 'BEGIN{count=0} $2 == "part" {
                parttype=tolower($3);
                if (parttype == "21686148-6449-6e6f-744e-656564454649") {
                    count++;
                }
            } END{print count}')"
        if [[ "${bios_boot_count:-0}" -lt 1 ]]; then
            echo "ERROR: BIOS + GPT 安装缺少 MING-BIOSBOOT / BIOS Boot Partition，无法可靠安装 BIOS GRUB"
            return 1
        fi
    elif [[ -n "${pttype}" && "${install_mode}" == "blank_ab" ]]; then
        echo "ERROR: blank_ab 自动安装必须使用 GPT 分区表，当前为 ${pttype}"
        return 1
    fi
    if [ -x "${root}/usr/sbin/grub-install" ]; then
        chroot "${root}" /usr/sbin/grub-install \
            --target=i386-pc --recheck --force --modules="${modules}" "${boot_disk}"
    elif command -v grub-install >/dev/null 2>&1; then
        grub-install --target=i386-pc --recheck --force --modules="${modules}" \
            --boot-directory="${root}/boot" "${boot_disk}"
    else
        echo "ERROR: grub-install is missing in live and target environments"
        exit 21
    fi
}

prefer_ming_uefi_boot() {
    [ -d /sys/firmware/efi ] || return 0
    command -v efibootmgr >/dev/null 2>&1 || return 0
    entry="$(efibootmgr 2>/dev/null | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\)\*.*Ming OS.*/\1/p' | head -n 1)"
    [ -n "${entry}" ] || return 0
    echo "Ming UEFI boot entry=${entry}"
    efibootmgr -n "${entry}" >/dev/null 2>&1 || true
    order="$(efibootmgr 2>/dev/null | awk -F': ' '/BootOrder/ {print $2; exit}')"
    if [ -n "${order}" ]; then
        rest="$(printf '%s\n' "${order}" | tr ',' '\n' | awk -v e="${entry}" '$0 != e && $0 != "" {print}' | paste -sd, -)"
        if [ -n "${rest}" ]; then
            efibootmgr -o "${entry},${rest}" >/dev/null 2>&1 || true
        else
            efibootmgr -o "${entry}" >/dev/null 2>&1 || true
        fi
    fi
}

firmware_mode="bios"
uefi_nvram=false
uefi_fallback=false
bios_grub_verified=false
if [ -d /sys/firmware/efi ]; then
    firmware_mode="uefi"
    install_uefi_grub || {
        echo "ERROR: UEFI bootloader installation failed; refusing an unusable BIOS fallback"
        exit 23
    }
    [ -s "${root}/boot/efi/EFI/BOOT/BOOTX64.EFI" ] && uefi_fallback=true
    if command -v efibootmgr >/dev/null 2>&1 \
        && efibootmgr 2>/dev/null | grep -Eq '^Boot[0-9A-Fa-f]{4}\*?.*Ming OS'; then
        uefi_nvram=true
    fi
    if [[ "${uefi_nvram}" != true && "${uefi_fallback}" != true ]]; then
        echo "ERROR: UEFI install produced neither a Ming OS NVRAM entry nor BOOTX64.EFI fallback"
        exit 23
    fi
    echo "Ming UEFI bootloader path completed"
    prefer_ming_uefi_boot
else
    install_bios_grub || {
        echo "ERROR: BIOS bootloader installation failed"
        exit 23
    }
    bios_grub_verified=true
    echo "Ming BIOS bootloader path completed"
fi

boot_mode_report="${root}/var/log/ming-installer-boot-mode.json"
install -d -m 0755 "${root}/var/log"
jq -n \
    --arg firmware_mode "${firmware_mode}" \
    --argjson uefi_nvram "${uefi_nvram}" \
    --argjson uefi_fallback "${uefi_fallback}" \
    --argjson bios_grub_verified "${bios_grub_verified}" \
    --arg warning "安装介质启动模式与电脑后续硬盘启动模式不一致时，固件可能看不到 Ming OS；UEFI 安装请保持 UEFI，BIOS 安装请保持 Legacy/CSM。" \
    '{"firmware_mode":$firmware_mode, "uefi_nvram":$uefi_nvram, "uefi_fallback":$uefi_fallback, "bios_grub_verified":$bios_grub_verified, "mismatch_warning":$warning}' \
    >"${boot_mode_report}.tmp"
chmod 0644 "${boot_mode_report}.tmp"
mv -f "${boot_mode_report}.tmp" "${boot_mode_report}"
cp -f "${boot_mode_report}" /run/ming-installer/ming-installer-boot-mode.json
echo "boot_mode_report=${boot_mode_report}"
echo "启动模式不一致提示：安装介质以 ${firmware_mode} 模式启动并安装，请保持电脑硬盘启动模式一致。"

# Discover a second OS only after the target ESP is mounted.  An empty helper
# output is valid and keeps single-OS installations at two top-level entries.
if [ -x "${root}/usr/local/sbin/ming-detect-other-os" ]; then
    MING_OTHER_OS_ROOT="${root}" \
    MING_OTHER_OS_OUT="${root}/etc/grub.d/11_ming_other_os" \
    MING_OTHER_OS_LOG="${root}/var/log/ming-other-os-detect.log" \
        python3 "${root}/usr/local/sbin/ming-detect-other-os" || {
            echo "ERROR: conservative other-OS detection failed; refusing unverified GRUB output"
            exit 22
        }
fi

# A GRUB core without a usable config drops users at grub>, so final config
# generation and validation are installation hard gates.
if [ -x "${root}/usr/sbin/update-grub" ]; then
    chroot "${root}" /usr/sbin/update-grub \
        >/run/ming-installer/update-grub.log 2>&1 || exit 22
elif [ -x "${root}/usr/sbin/grub-mkconfig" ]; then
    chroot "${root}" /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg \
        >/run/ming-installer/update-grub.log 2>&1 || exit 22
else
    grub-mkconfig -o "${root}/boot/grub/grub.cfg" \
        >/run/ming-installer/update-grub.log 2>&1 || exit 22
fi

validate_final_grub_root_uuid() {
    local grub_cfg="$1"
    awk -v expected_a="root=UUID=${root_uuid}" -v expected_b="root=UUID=${slot_b_uuid}" \
        -v install_mode="${install_mode:-blank_ab}" '
        /^[[:space:]]*linux[[:space:]]+\/(boot\/)?vmlinuz[^[:space:]]*([[:space:]]|$)/ ||
        /^[[:space:]]*linux[[:space:]]+\/ming-slots\/[AB]\/vmlinuz([[:space:]]|$)/ {
            ming_linux_count++
            expected=expected_a
            if ($2 == "/ming-slots/A/vmlinuz") {
                slot_a_count++
            } else if ($2 == "/ming-slots/B/vmlinuz") {
                slot_b_count++
                expected=expected_b
            }
            root_count=0
            for (field = 1; field <= NF; field++) {
                if ($field ~ /^root=/) {
                    root_count++
                    if ($field != expected) {
                        printf "ERROR: Ming linux stanza %d has unexpected %s\n", NR, $field > "/dev/stderr"
                        invalid=1
                    }
                }
            }
            if (root_count != 1) {
                printf "ERROR: Ming linux stanza %d must contain exactly one root=UUID argument\n", NR > "/dev/stderr"
                invalid=1
            }
        }
        END {
            if (ming_linux_count == 0) {
                print "ERROR: final grub.cfg has no Ming linux stanzas" > "/dev/stderr"
                exit 1
            }
            if (install_mode == "blank_ab" && (slot_a_count == 0 || slot_b_count == 0)) {
                print "ERROR: final grub.cfg is missing Ming A/B slot stanzas" > "/dev/stderr"
                exit 1
            }
            if (install_mode == "dual_boot_preserve" && (slot_a_count != 0 || slot_b_count != 0)) {
                print "ERROR: dual-boot grub.cfg contains false A/B slot stanzas" > "/dev/stderr"
                exit 1
            }
            exit invalid ? 1 : 0
        }
    ' "${grub_cfg}"
}

if [ ! -s "${root}/boot/grub/grub.cfg" ] \
    || grep -Eq '__MING_(ROOT|BOOT).*UUID__' "${root}/boot/grub/grub.cfg"; then
    echo "ERROR: final grub.cfg is missing, empty, or still contains a placeholder"
    exit 22
fi
if grep -Eq 'boot=live|ming\.installer=1|安装 Ming OS' "${root}/boot/grub/grub.cfg"; then
    echo "ERROR: installed GRUB contains Live installer arguments or labels"
    exit 22
fi
if [[ "${install_mode}" == "blank_ab" ]]; then
    for contract in \
        "Ming OS slot A" "/ming-slots/A/vmlinuz" "/ming-slots/A/initrd.img" \
        "Ming OS slot B" "/ming-slots/B/vmlinuz" "/ming-slots/B/initrd.img"; do
        grep -Fq "${contract}" "${root}/boot/grub/grub.cfg" || {
            echo "ERROR: final grub.cfg is missing A/B contract ${contract}"; exit 22;
        }
    done
else
    if grep -Eq 'Ming OS slot [AB]|/ming-slots/[AB]/' "${root}/boot/grub/grub.cfg"; then
        echo "ERROR: dual-boot grub.cfg contains false A/B state"
        exit 22
    fi
fi
if ! validate_final_grub_root_uuid "${root}/boot/grub/grub.cfg"; then
    echo "ERROR: all Ming linux stanzas must use the authoritative root UUID"
    exit 22
fi
if [ -x "${root}/usr/bin/grub-script-check" ]; then
    chroot "${root}" /usr/bin/grub-script-check /boot/grub/grub.cfg || exit 22
elif command -v grub-script-check >/dev/null 2>&1; then
    grub-script-check "${root}/boot/grub/grub.cfg" || exit 22
fi
final_install_result="$(/usr/local/sbin/ming-installer-verify installed --receipt --final-boot)" || {
    printf '%s\n' "${final_install_result}" >&2
    echo "ERROR: unified final installed-system verification failed"
    exit 22
}
printf '%s\n' "${final_install_result}"
echo "grub.cfg OK: $(wc -l < "${root}/boot/grub/grub.cfg") lines"
echo "Ming bootloader install completed"
MINGBOOTLOADER
    chmod +x /usr/local/sbin/ming-install-bootloader

    # Debian's calamares-settings package brands the installer as Debian. Keep
    # the module sequence from the package, but override the visible branding
    # and add a final identity repair that runs on the installed target.
    mkdir -p /etc/calamares/branding/ming /etc/calamares/modules

    # KPMcore 24.12 sends a legacy standalone "write" command to sfdisk.
    # util-linux 2.41 treats that line as another partition definition. Keep a
    # Live-only compatibility wrapper at the trusted command path, strip that
    # obsolete line for --append, and normalize the success text KPMcore parses.
    install -d -m 0755 /usr/lib/ming-os
    if [[ ! -x /usr/lib/ming-os/sfdisk.real ]]; then
        install -m 0755 /usr/sbin/sfdisk /usr/lib/ming-os/sfdisk.real
    fi
    cat > /usr/sbin/sfdisk << 'SFDISKWRAPPER'
#!/bin/sh
export LC_ALL=C LANG=C LANGUAGE=C

case " $* " in
    *" --append "*)
        input=$(mktemp)
        output=$(mktemp)
        error=$(mktemp)
        trap 'rm -f "$input" "$output" "$error"' EXIT HUP INT TERM
        cat > "$input"
        sed '/^[[:space:]]*write[[:space:]]*$/d' "$input" \
            | /usr/lib/ming-os/sfdisk.real "$@" > "$output" 2> "$error"
        rc=$?
        if [ "$rc" -eq 0 ]; then
            number=$(sed -n 's/.*Created a new partition \([0-9][0-9]*\).*/\1/p' \
                "$output" "$error" | tail -n 1)
            if [ -n "$number" ]; then
                printf 'Created a new partition %s\n' "$number"
                exit 0
            fi
        fi
        cat "$output"
        cat "$error" >&2
        exit "$rc"
        ;;
    *)
        exec /usr/lib/ming-os/sfdisk.real "$@"
        ;;
esac
SFDISKWRAPPER
    chmod 0755 /usr/sbin/sfdisk
    rm -f /usr/local/sbin/sfdisk

    cat > /usr/local/sbin/ming-fix-partition-types << 'MINGFIXPARTTYPES'
#!/usr/bin/env bash
set -euo pipefail

LOG="${RUNTIME_DIR:-/run}/ming-installer/partition-types.log"
install -d -m 0755 "${LOG%/*}"
: >"${LOG}"
chmod 0644 "${LOG}"

log() {
    printf '%s\n' "$*" >>"${LOG}"
}

fail_partition_types() {
    log "ERROR: $*"
    printf 'Ming OS 安装分区检查失败：%s。详细日志：%s\n' "$*" "${LOG}" >&2
    exit 31
}

install_mode="blank_ab"
if [[ -s /run/ming-installer/install-mode.json ]] && command -v python3 >/dev/null 2>&1; then
    install_mode="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode",""))' /run/ming-installer/install-mode.json 2>>"${LOG}" || printf '')"
fi
[[ "${install_mode}" == "blank_ab" ]] || {
    log "skip: install_mode=${install_mode:-unknown}"
    exit 0
}

partition_type_contracts=(
    "MING-BIOSBOOT:ef02:21686148-6449-6e6f-744e-656564454649"
    "MING-ESP:ef00:c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
    "MING-BOOT:8300:0fc63daf-8483-4772-8e79-3d69d8477de4"
    "MING-ROOT-A:8300:0fc63daf-8483-4772-8e79-3d69d8477de4"
    "MING-ROOT-B:8300:0fc63daf-8483-4772-8e79-3d69d8477de4"
    "MING-HOME:8300:0fc63daf-8483-4772-8e79-3d69d8477de4"
)

settle_partitions() {
    udevadm settle --timeout=10 >>"${LOG}" 2>&1 || udevadm settle >>"${LOG}" 2>&1 || true
}

lsblk_snapshot() {
    log "partition snapshot:"
    lsblk -nrpo NAME,TYPE,PKNAME,PARTN,PARTLABEL,PARTTYPE,FSTYPE,MOUNTPOINT \
        >>"${LOG}" 2>&1 || true
}

lsblk_parts_by_label() {
    local wanted="$1"
    lsblk -nrpo NAME,TYPE,PARTLABEL 2>>"${LOG}" \
        | awk -v wanted="${wanted}" '$2 == "part" && $3 == wanted { print $1 }'
}

wait_for_part_label() {
    local label="$1" link part
    local -a part_matches=()
    for _attempt in 1 2 3 4 5 6; do
        settle_partitions
        # Prefer the kernel's current partition table and fail closed when
        # another disk already contains the same Ming label. A global udev
        # by-partlabel link cannot safely disambiguate that situation.
        mapfile -t part_matches < <(lsblk_parts_by_label "${label}")
        if ((${#part_matches[@]} > 1)); then
            log "duplicate partition label ${label}: ${part_matches[*]}"
            return 3
        fi
        if ((${#part_matches[@]} == 1)); then
            part="${part_matches[0]}"
            if [[ -b "${part}" ]]; then
                printf '%s\n' "${part}"
                return 0
            fi
        fi
        link="/dev/disk/by-partlabel/${label}"
        if [[ -e "${link}" || -L "${link}" ]]; then
            part="$(readlink -f -- "${link}" 2>>"${LOG}" || true)"
            if [[ -b "${part}" ]]; then
                printf '%s\n' "${part}"
                return 0
            fi
        fi
        sleep 1
    done
    lsblk_snapshot
    return 1
}

part_by_label() {
    wait_for_part_label "$1"
}

part_disk() {
    local part="$1" parent
    parent="$(lsblk -nrpo PKNAME "${part}" 2>>"${LOG}" | head -n 1 || true)"
    if [[ -n "${parent}" ]]; then
        [[ "${parent}" == /* ]] || parent="/dev/${parent}"
        printf '%s\n' "${parent}"
        return 0
    fi
    lsblk -nrpo NAME,TYPE "${part}" 2>>"${LOG}" \
        | awk '$2 == "disk" { print $1; exit }'
}

part_number() {
    lsblk -nro PARTN "$1" 2>>"${LOG}" | head -n 1
}

part_type() {
    lsblk -nro PARTTYPE "$1" 2>>"${LOG}" \
        | head -n 1 | tr '[:upper:]' '[:lower:]'
}

wait_for_part_type() {
    local part="$1" expected="$2" actual
    for _attempt in 1 2 3 4 5 6; do
        settle_partitions
        actual="$(part_type "${part}")"
        [[ "${actual}" == "${expected}" ]] && return 0
        sleep 1
    done
    log "partition type did not settle for ${part}: ${actual:-missing} (expected ${expected})"
    lsblk_snapshot
    return 1
}

set_part_type() {
    local disk="$1" number="$2" short_code="$3" guid="$4"
    for _attempt in 1 2 3; do
        if command -v sgdisk >/dev/null 2>&1; then
            sgdisk --typecode="${number}:${short_code}" "${disk}" >>"${LOG}" 2>&1 && return 0
        elif command -v sfdisk >/dev/null 2>&1; then
            sfdisk --part-type "${disk}" "${number}" "${guid}" >>"${LOG}" 2>&1 && return 0
        else
            log "ERROR: neither sgdisk nor sfdisk is available to set GPT partition types"
            return 1
        fi
        partprobe "${disk}" >>"${LOG}" 2>&1 || true
        settle_partitions
        sleep 1
    done
    return 1
}

settle_partitions
changed_disks=()
for contract in "${partition_type_contracts[@]}"; do
    IFS=: read -r label short_code guid <<<"${contract}"
    if part="$(part_by_label "${label}")"; then
        :
    else
        lookup_rc=$?
        if [[ "${lookup_rc}" -eq 3 ]]; then
            fail_partition_types "duplicate partition label ${label}; disconnect other Ming OS disks and retry"
        fi
        fail_partition_types "missing partition label ${label}"
    fi
    disk="$(part_disk "${part}")"
    number="$(part_number "${part}")"
    [[ -b "${disk}" && -n "${number}" ]] \
        || fail_partition_types "cannot resolve disk/number for ${label} (${part})"
    actual="$(part_type "${part}")"
    if [[ "${actual}" != "${guid}" ]]; then
        log "fix ${label}: ${actual:-missing} -> ${guid} on ${disk}#${number}"
        set_part_type "${disk}" "${number}" "${short_code}" "${guid}" \
            || fail_partition_types "cannot set GPT partition type for ${label}"
        changed_disks+=("${disk}")
    else
        log "ok ${label}: ${guid}"
    fi
done

if ((${#changed_disks[@]})); then
    printf '%s\n' "${changed_disks[@]}" | sort -u | while read -r disk; do
        partprobe "${disk}" >>"${LOG}" 2>&1 || true
    done
    udevadm settle >>"${LOG}" 2>&1 || true
fi

for contract in "${partition_type_contracts[@]}"; do
    IFS=: read -r label _short_code guid <<<"${contract}"
    if part="$(part_by_label "${label}")"; then
        :
    else
        lookup_rc=$?
        if [[ "${lookup_rc}" -eq 3 ]]; then
            fail_partition_types "duplicate partition label ${label}; disconnect other Ming OS disks and retry"
        fi
        fail_partition_types "missing partition label ${label} during final verification"
    fi
    [[ -n "${part}" ]] && wait_for_part_type "${part}" "${guid}" || {
        actual="$(part_type "${part:-/dev/null}")"
        fail_partition_types "${label} PARTTYPE is ${actual:-missing}, expected ${guid}"
    }
done

log "partition type normalization complete"
exit 0
MINGFIXPARTTYPES
    chmod 0755 /usr/local/sbin/ming-fix-partition-types

    cat > /etc/calamares/modules/ming-fix-partition-types.conf << 'MINGFIXPARTTYPESCONF'
---
dontChroot: true
timeout: 45
script:
  - "/usr/local/sbin/ming-fix-partition-types"
MINGFIXPARTTYPESCONF

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

    cat > /etc/calamares/modules/ming-installed-desktop-gate.conf << 'INSTALLEDDESKTOPGATECONF'
---
dontChroot: true
timeout: 30
script:
  - "/usr/local/sbin/ming-installer-verify installed --receipt"
INSTALLEDDESKTOPGATECONF

    cat > /etc/calamares/modules/ming-ota-preflight.conf << PREFLIGHTCONF
---
dontChroot: true
timeout: 180
script:
  - "/usr/local/sbin/ming-ota-preflight"
PREFLIGHTCONF

    cat > /etc/calamares/modules/ming-ota-target-guard.conf << 'MINGOTAGUARDCONF'
---
MINGOTAGUARDCONF

    cat > /etc/calamares/modules/ming-bootloader.conf << BOOTLOADERCONF
---
dontChroot: true
timeout: 180
script:
  - "/usr/local/sbin/ming-install-bootloader"
BOOTLOADERCONF

    cat > /usr/local/sbin/ming-finish-install-reboot << 'FINISHREBOOT'
#!/usr/bin/env bash
set +e

LOG=/run/ming-installer/finish-reboot.log
install -d -m 0755 /run/ming-installer
: >"${LOG}"
chmod 0644 "${LOG}"
exec >>"${LOG}" 2>&1

echo "==== Ming finish reboot $(date -Is) ===="
echo "安装完成，请拔出 U 盘或安装介质，然后重启。"
if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    zenity --question \
        --title="Ming OS 安装完成" \
        --text="安装已完成。请拔出 U 盘或安装介质，然后重新启动电脑。" \
        --ok-label="拔出后重启" --cancel-label="稍后重启" \
        >/dev/null 2>&1 || exit 0
fi
sync

if [ -d /sys/firmware/efi ] && command -v efibootmgr >/dev/null 2>&1; then
    entry="$(efibootmgr 2>/dev/null | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\)\*.*Ming OS.*/\1/p' | head -n 1)"
    if [ -n "${entry}" ]; then
        echo "Prefer UEFI Boot${entry} for next boot"
        efibootmgr -n "${entry}" || true
        order="$(efibootmgr 2>/dev/null | awk -F': ' '/BootOrder/ {print $2; exit}')"
        if [ -n "${order}" ]; then
            rest="$(printf '%s\n' "${order}" | tr ',' '\n' | awk -v e="${entry}" '$0 != e && $0 != "" {print}' | paste -sd, -)"
            if [ -n "${rest}" ]; then
                efibootmgr -o "${entry},${rest}" || true
            else
                efibootmgr -o "${entry}" || true
            fi
        fi
    else
        echo "No Ming OS UEFI entry found"
    fi
fi

# The running squashfs root still needs the Live medium to complete shutdown.
# Ejecting it here causes an endless SQUASHFS error loop and a hard reset.
sync
systemctl -i reboot
FINISHREBOOT
    chmod +x /usr/local/sbin/ming-finish-install-reboot

    cat > /etc/calamares/modules/finished.conf << 'FINISHEDCONF'
---
restartNowEnabled: true
restartNowChecked: true
restartNowText: "安装完成。请拔出 U 盘或安装介质，然后重新启动。"
restartNowCommand: "/usr/local/sbin/ming-finish-install-reboot"
FINISHEDCONF

    cat > /etc/calamares/modules/unpackfs.conf << 'UNPACKFSCONF'
---
unpack:
  - source: "/run/ming-installer/filesystem.squashfs"
    sourcefs: "squashfs"
    destination: ""
UNPACKFSCONF

    # Keep Calamares from falling back to distro defaults that may not match
    # the installer-only Ming OS image. VirtualBox testing exposed failures in
    # the partition step when the target disk had no usable label yet.
    cat > /etc/calamares/modules/partition.conf << 'PARTITIONCONF'
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
# 业务分区只使用 ext4，额外允许 FAT32 给 MING-ESP：
# btrfs 在已有 Fedora/旧 btrfs 卷的磁盘上创建分区会失败（图二错误）
# ext4 稳定可靠，是绝大多数老机器的最佳选择
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
# 关闭手动分区入口——普通用户不需要也不会用，只显示"清空整个磁盘"
allowManualPartitioning: false
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
# 一键安装：跳过用户名/密码页面，使用以下预设值。
# 用户可在安装完成后通过「铭设置」修改账户信息和密码。
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

    cat > /etc/calamares/settings.conf << 'CALAMARESSETTINGS'
---
modules-search: [ local, /usr/lib/x86_64-linux-gnu/calamares/modules ]
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
# 一键安装：用户只需点击"开始安装"，无需配置任何选项。
# 语言/时区/键盘全部预设为中文/北京/US（装完后联网自动更新时间）。
# 用户账户由 ming-fix-installed-identity 幂等修复，避免 users 模块重复 useradd。
# 分区保留确认页，避免误清空硬盘。
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

ensure_single_tmpfs_fstab_entry() {
    local fstab="/etc/fstab"
    local normalized

    touch "${fstab}"
    normalized=$(mktemp "${fstab}.ming.XXXXXX")
    awk '
        /^[[:space:]]*#/ { print; next }
        NF >= 3 && $2 == "/tmp" && $3 == "tmpfs" { next }
        { print }
    ' "${fstab}" > "${normalized}"
    printf '%s\n' \
        'tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=512M 0 0' \
        >> "${normalized}"
    cat "${normalized}" > "${fstab}"
    rm -f "${normalized}"
}

optimize_system() {
    # 安装 zram 工具
    apt install -y --no-install-recommends zram-tools

    # Apply sysctls defensively: kernels differ across Debian point releases
    # and old tuning keys must never turn a missing knob into a boot failure.
    cat > /usr/local/sbin/ming-sysctl-apply << 'MINGSYSCTLAPPLY'
#!/usr/bin/env bash
set -uo pipefail

LOG=/var/log/ming-sysctl.log
mkdir -p "$(dirname "${LOG}")" 2>/dev/null || true

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${LOG}" 2>/dev/null || true; }

apply_file() {
    local file="${1:-}" line key value proc_key
    [[ -r "${file}" ]] || { log "missing sysctl file: ${file}"; return 0; }
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line="${line%%#*}"
        line="${line#${line%%[![:space:]]*}}"
        line="${line%${line##*[![:space:]]}}"
        [[ -n "${line}" && "${line}" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key//[[:space:]]/}"
        proc_key="/proc/sys/${key//./\/}"
        if [[ ! -e "${proc_key}" ]]; then
            log "unsupported sysctl ${key}; skipped"
            continue
        fi
        if ! sysctl -q "${key}=${value}" 2>>"${LOG}"; then
            log "failed sysctl ${key}"
        fi
    done < "${file}"
}

for file in "$@"; do apply_file "${file}"; done
MINGSYSCTLAPPLY
    chmod 0755 /usr/local/sbin/ming-sysctl-apply

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
    # Keep cache pressure within the supported policy range.  A value above
    # 100 evicts useful file cache too aggressively on 2 GB machines.
    vfs_cache_pressure=100
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

/usr/local/sbin/ming-sysctl-apply /etc/sysctl.d/99-ming-memory-runtime.conf

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
After=local-fs.target systemd-sysctl.service
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
# Ming OS 26.4.1 内核深度优化
# 目标：兼容 2GB+ RAM / 老 i3-i5-E3 / 老 AMD / 机械硬盘，同时保持桌面流畅

# ---- 内存：按实际内存由 ming-memory-profile 在 sysctl 后写入 ----
vm.watermark_boost_factor=0
vm.watermark_scale_factor=125
# 禁止内核 OOM 过于激进地杀进程（桌面常驻应用保护）
vm.oom_kill_allocating_task=0
vm.overcommit_memory=0
vm.overcommit_ratio=50
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
    ensure_single_tmpfs_fstab_entry

    # BBR 模块（多数 Debian 内核已内建，兜底加载）
    mkdir -p /etc/modules-load.d
    echo "tcp_bbr" > /etc/modules-load.d/ming-bbr.conf
    modprobe tcp_bbr 2>/dev/null || true

    # CPU 频率调节：老机器 ondemand，优先节能且响应快
    # cpufrequtils 不在 Live squashfs，走 udev 在启动时写 cpufreq governor
    cat > /etc/udev/rules.d/61-ming-cpufreq.rules << 'CPUFREQRULE'
# Ming OS：CPU 频率调节策略。由 ming-device-tune 读取
# scaling_available_governors 后选择 schedutil/ondemand，绝不写入不存在的键。
ACTION=="add", SUBSYSTEM=="cpu", KERNEL=="cpu[0-9]*", \
  RUN+="/usr/local/bin/ming-device-tune --governor-only"
CPUFREQRULE

    # 应用 sysctl 配置 only through the bounded, key-aware helper.  This
    # avoids failing on Debian kernels that no longer expose legacy knobs.
    /usr/local/sbin/ming-sysctl-apply \
        /etc/sysctl.d/99-ming-performance.conf \
        /etc/sysctl.d/99-ming-memory-runtime.conf || true

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

    # Serial getty is opt-in for hardware debugging; ordinary boots keep the
    # ttyS0 service disabled and avoid an unnecessary login process.
    if [[ "${MING_DEBUG_SERIAL:-0}" == 1 ]]; then
        serial_unit="serial-getty@ttyS0.service"
        systemctl enable --now "${serial_unit}" 2>/dev/null || true
    else
        systemctl disable --now serial-getty@ttyS0.service 2>/dev/null || true
    fi

    # Keep exactly one real system-wide OOM guard active.  cgroup v2 by itself
    # does not configure a ManagedOOM target, so selecting systemd-oomd merely
    # because its service exists can leave an old machine unprotected until it
    # is already swapping heavily.  earlyoom observes global memory/swap
    # pressure and is therefore the dependable default for this desktop.
    cat > /usr/local/sbin/ming-oom-policy << 'MINGOOMPOLICY'
#!/usr/bin/env bash
set -u

STATE_DIR=/run/ming-os
STATE_FILE="${STATE_DIR}/oom-policy"
mkdir -p "${STATE_DIR}"
backend=none
foreground_protected=false

unit_available() {
    systemctl cat "$1" >/dev/null 2>&1
}

systemctl disable --now systemd-oomd.service >/dev/null 2>&1 || true
if unit_available earlyoom.service; then
    systemctl enable --now earlyoom.service >/dev/null 2>&1 || true
    if systemctl is-active --quiet earlyoom.service 2>/dev/null; then
        backend=earlyoom
    fi
fi

# This file is diagnostic state only.  It is replaced atomically so a helper
# restart cannot leave a partially written backend name for the settings page.
temporary="${STATE_FILE}.tmp.$$"
{
    printf 'backend=%s\n' "${backend}"
    printf 'foreground_protected=%s\n' "${foreground_protected}"
    printf 'updated_at=%s\n' "$(date +%s)"
} > "${temporary}" 2>/dev/null && mv -f "${temporary}" "${STATE_FILE}" 2>/dev/null || rm -f "${temporary}"
exit 0
MINGOOMPOLICY
    chmod 0755 /usr/local/sbin/ming-oom-policy

    cat > /etc/systemd/system/ming-oom-policy.service << 'MINGOOMPOLICYSVC'
[Unit]
Description=Ming OS mutually exclusive OOM backend selector
After=local-fs.target
Before=graphical.target
ConditionPathExists=/usr/local/sbin/ming-oom-policy

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-oom-policy
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MINGOOMPOLICYSVC

    # Keep both vendor units installed for hardware compatibility, but let the
    # selector own activation so they cannot race during normal boot.
    systemctl disable --now earlyoom.service systemd-oomd.service 2>/dev/null || true
    systemctl enable ming-oom-policy.service 2>/dev/null || true

    mkdir -p /etc/default
    cat > /etc/default/earlyoom << EARLYOOMCFG
EARLYOOM_ARGS="-m 8 -s 12 -r 60 --avoid '^(Xorg|xfce4-session|lightdm|NetworkManager|pipewire|pulseaudio|wireplumber|fcitx5|ming-phone-desktop|ming-update)$'"
EARLYOOMCFG

    # Apply timer migration only when this kernel exposes the key.  The helper
    # uses the same key-aware sysctl path as the rest of Ming's runtime tuning.
    cat > /usr/local/sbin/ming-timer-policy << 'MINGTIMERPOLICY'
#!/usr/bin/env bash
set -u

if [[ -e /proc/sys/kernel/timer_migration ]]; then
    install -d -m 0755 /run/ming-os
    printf 'kernel.timer_migration=1\n' > /run/ming-os/timer-policy.conf
    /usr/local/sbin/ming-sysctl-apply /run/ming-os/timer-policy.conf >/dev/null 2>&1 || true
fi
exit 0
MINGTIMERPOLICY
    chmod 0755 /usr/local/sbin/ming-timer-policy

    cat > /etc/systemd/system/ming-timer-policy.service << 'MINGTIMERPOLICYSVC'
[Unit]
Description=Ming OS bounded timer coalescing policy
After=local-fs.target
Before=graphical.target
ConditionPathExists=/usr/local/sbin/ming-timer-policy

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-timer-policy
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MINGTIMERPOLICYSVC
    systemctl enable ming-timer-policy.service 2>/dev/null || true

    # 启用 zram 与低内存保护
    systemctl enable ming-memory-profile.service 2>/dev/null || true
    systemctl enable zramswap 2>/dev/null || true
    systemctl enable irqbalance 2>/dev/null || true

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

set_governors() {
    local gov_path available selected
    for gov_path in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        [[ -w "${gov_path}" ]] || continue
        available="$(cat "$(dirname "${gov_path}")/scaling_available_governors" 2>/dev/null || true)"
        selected=""
        if grep -qw schedutil <<< "${available}"; then
            selected=schedutil
        elif grep -qw ondemand <<< "${available}"; then
            selected=ondemand
        elif grep -qw powersave <<< "${available}"; then
            selected=powersave
        fi
        [[ -n "${selected}" ]] || continue
        echo "${selected}" > "${gov_path}" 2>/dev/null || true
    done
}

if [[ "${1:-}" == "--governor-only" ]]; then
    set_governors
    exit 0
fi

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
    /usr/local/sbin/ming-sysctl-apply /etc/sysctl.d/98-ming-hdd-runtime.conf || true
else
    rm -f /etc/sysctl.d/98-ming-hdd-runtime.conf
fi

cpu_governor="managed-by-tlp"
if ! systemctl is-active --quiet tlp.service 2>/dev/null; then
    set_governors
    cpu_governor="kernel-supported"
fi

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
Wants=ming-power-profile.service
After=local-fs.target ming-power-profile.service

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
    # TLP owns battery/AC policy, while thermald owns Intel temperature
    # protection.  The runtime helper enables them only when the hardware
    # warrants it, avoiding duplicate policy daemons on desktops/VMs.
    cat > /usr/local/sbin/ming-power-profile << 'MINGPOWERPROFILE'
#!/usr/bin/env bash
set -uo pipefail

LOG=/var/log/ming-power-profile.log
mkdir -p "$(dirname "${LOG}")" 2>/dev/null || true
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${LOG}" 2>/dev/null || true; }

has_battery=false
for battery in /sys/class/power_supply/BAT*; do
    [[ -e "${battery}" ]] && has_battery=true && break
done
is_laptop=false
for chassis_file in /sys/class/dmi/id/chassis_type /sys/devices/virtual/dmi/id/chassis_type; do
    if [[ -r "${chassis_file}" ]] && grep -Eq '^(8|9|10|11|14|30|31|32)$' "${chassis_file}"; then
        is_laptop=true
        break
    fi
done
if [[ "${is_laptop}" != true ]] && command -v laptop-detect >/dev/null 2>&1 \
    && timeout --foreground 2s laptop-detect >/dev/null 2>&1; then
    is_laptop=true
fi
if [[ "${is_laptop}" != true ]] \
    && grep -Eiq 'laptop|notebook|portable|tablet' \
        /sys/class/dmi/id/product_name /sys/class/dmi/id/chassis_type 2>/dev/null; then
    is_laptop=true
fi
portable=false
if [[ "${has_battery}" == true || "${is_laptop}" == true ]]; then
    portable=true
fi
is_intel=false
grep -Eiq 'GenuineIntel|Intel' /proc/cpuinfo /sys/class/dmi/id/sys_vendor 2>/dev/null && is_intel=true

if [[ "${portable}" == true ]]; then
    systemctl enable --now tlp.service 2>/dev/null || log "TLP unavailable on portable system"
else
    systemctl disable --now tlp.service 2>/dev/null || true
fi

if [[ "${is_intel}" == true ]]; then
    systemctl enable --now thermald.service 2>/dev/null || log "thermald unavailable on Intel system"
else
    systemctl disable --now thermald.service 2>/dev/null || true
fi

# Prevent another policy daemon from racing TLP/thermald when present.
systemctl disable --now power-profiles-daemon.service tuned.service 2>/dev/null || true
{
    echo "battery=${has_battery}"
    echo "laptop=${is_laptop}"
    echo "portable=${portable}"
    echo "intel=${is_intel}"
    echo "tlp=$(systemctl is-active tlp.service 2>/dev/null || echo inactive)"
    echo "thermald=$(systemctl is-active thermald.service 2>/dev/null || echo inactive)"
} > /run/ming-os/power-profile 2>/dev/null || true
MINGPOWERPROFILE
    chmod 0755 /usr/local/sbin/ming-power-profile

    cat > /etc/systemd/system/ming-power-profile.service << 'MINGPOWERPROFILESVC'
[Unit]
Description=Ming OS hardware-aware TLP and thermald ownership
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-power-profile
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MINGPOWERPROFILESVC
    systemctl disable --now tlp.service 2>/dev/null || true
    systemctl enable ming-power-profile.service 2>/dev/null || true

    mkdir -p /etc/tlp.d
    cat > /etc/tlp.d/ming-laptop.conf << TLPCONF
# Ming OS 笔记本电池优化
CPU_SCALING_GOVERNOR_ON_AC=schedutil
CPU_SCALING_GOVERNOR_ON_BAT=schedutil
CPU_ENERGY_PERF_POLICY_ON_AC=balance_performance
CPU_ENERGY_PERF_POLICY_ON_BAT=balance_performance
PLATFORM_PROFILE_ON_AC=balanced
PLATFORM_PROFILE_ON_BAT=balanced
DISK_DEVICES="nvme0n1 sda"
DISK_APM_LEVEL_ON_AC="254"
DISK_APM_LEVEL_ON_BAT="128"
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=off
USB_AUTOSUSPEND=0
# Old laptops often expose Wi-Fi/HID/audio through USB bridges whose autosuspend
# support is incomplete.  Keep autosuspend disabled globally and retain the
# supported exclusions as documentation/defense if a user enables it later.
USB_EXCLUDE_BTUSB=1
USB_EXCLUDE_AUDIO=1
USB_EXCLUDE_WWAN=1
USB_EXCLUDE_PRINTER=1
RUNTIME_PM_ON_AC=on
RUNTIME_PM_ON_BAT=on
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

    # ======================== Intel Xorg compatibility migration ========================
    # Let the in-tree i915/KMS stack select Xorg's modesetting backend.  The
    # previous generated Intel DDX path causes black screens on Atom/Cherry
    # Trail devices such as Mi Pad 2.  The retained helper only quarantines a
    # header-marked config or the exact previous Ming-generated signature.
    cat > /usr/local/sbin/ming-intel-xorg-setup << 'INTELXORGSETUP'
#!/bin/sh
set -eu

XCONF="/etc/X11/xorg.conf.d/20-intel.conf"
DISABLED="${XCONF}.ming-legacy-disabled"
HEADER="# Managed by Ming OS legacy Intel Xorg setup"
LOG="/var/log/ming-intel-xorg-migration.log"

mkdir -p "$(dirname "${LOG}")"
is_ming_legacy_config() {
    grep -Fxq "${HEADER}" "${XCONF}" && return 0
    grep -Eq '^[[:space:]]*Identifier[[:space:]]+"Intel Graphics"[[:space:]]*$' "${XCONF}" \
        && grep -Eq '^[[:space:]]*Driver[[:space:]]+"intel"[[:space:]]*$' "${XCONF}" \
        && grep -Eq '^[[:space:]]*Option[[:space:]]+"TearFree"[[:space:]]+"true"[[:space:]]*$' "${XCONF}" \
        && grep -Eq '^[[:space:]]*Option[[:space:]]+"AccelMethod"[[:space:]]+"sna"[[:space:]]*$' "${XCONF}" \
        && grep -Eq '^[[:space:]]*Option[[:space:]]+"DRI"[[:space:]]+"3"[[:space:]]*$' "${XCONF}" \
        && grep -Eq '^[[:space:]]*Option[[:space:]]+"TripleBuffer"[[:space:]]+"true"[[:space:]]*$' "${XCONF}"
}

if [ ! -f "${XCONF}" ]; then
    exit 0
fi
if is_ming_legacy_config; then
    mv -f "${XCONF}" "${DISABLED}"
    printf '%s disabled Ming-managed legacy Intel Xorg config\n' "$(date '+%F %T')" >> "${LOG}"
else
    printf '%s preserved user-owned Intel Xorg config: %s\n' "$(date '+%F %T')" "${XCONF}" >> "${LOG}"
fi
INTELXORGSETUP
    chmod 0755 /usr/local/sbin/ming-intel-xorg-setup
    systemctl disable --now ming-intel-xorg.service 2>/dev/null || true
    rm -f /etc/systemd/system/ming-intel-xorg.service \
        /etc/systemd/system/multi-user.target.wants/ming-intel-xorg.service
    cat > /etc/systemd/system/ming-intel-xorg-migration.service << 'INTELXORGMIGRATIONSVC'
[Unit]
Description=Ming OS Intel Xorg legacy configuration migration
After=local-fs.target
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-intel-xorg-setup

[Install]
WantedBy=multi-user.target
INTELXORGMIGRATIONSVC
    systemctl enable ming-intel-xorg-migration.service 2>/dev/null || true
    /usr/local/sbin/ming-intel-xorg-setup || true

    # ACPI 守护进程（处理笔记本热键/电源按钮）
    systemctl enable acpid 2>/dev/null || true
}

# ======================== 开机加速（26.2.5） ========================

configure_boot_speed() {
    echo "配置开机加速..."

    # Do not hold Bluetooth behind the graphical target. The radio service is
    # enabled only after BlueZ is present and its rfkill drop-in is independent
    # of the display stack; remove legacy delayed overrides on resumed builds.
    rm -f /etc/systemd/system/bluetooth.service.d/delay.conf

    # Printing/discovery/scanning stay socket/on-demand only.  The profile is
    # idempotent and is also applied on the first real boot.
    systemctl enable cups.socket 2>/dev/null || true
    systemctl disable --now cups.service cups-browsed.service \
        avahi-daemon.service saned.service saned.socket 2>/dev/null || true

    # Tracker 索引：全部屏蔽，用户搜索时不需要实时索引
    for svc in tracker-miner-fs-3.service tracker-extract-3.service tracker-writeback-3.service \
               tracker-miner-fs.service tracker-extract.service; do
        systemctl mask "${svc}" 2>/dev/null || true
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

    # No NetworkManager-wait-online drop-in is shipped.  The dispatcher-owned
    # ming-time-sync helper retries after actual network events instead.
    systemctl disable --now NetworkManager-wait-online.service 2>/dev/null || true
    rm -rf /etc/systemd/system/NetworkManager-wait-online.service.d

    systemctl enable ming-service-profile.service 2>/dev/null || true
    /usr/local/sbin/ming-service-profile apply >/dev/null 2>&1 || true

    echo "开机加速配置完成"
}

# ======================== 多盘合一 · 无感知存储 ========================
# 设计意图：
#   保留一个需要明确授权的手动存储管理器，供用户在确认数据盘后运行。它不会
#   参与启动链、udev 热插拔或登录前挂载，也不会在没有用户确认时修改 fstab。
#   只读取/挂载已格式化分区，绝不自动格式化或删除数据；首次绑定时用 rsync
#   把原目录内容迁移到数据盘，保证文件不丢。
configure_seamless_storage() {
    echo "配置无感知存储（多盘合一）..."

    # Upgrades from 26.3.1 may leave the old boot-enabled service and udev
    # trigger behind.  Retire both before installing the authorized on-demand
    # helper so a resumed build cannot silently mutate disks during boot.
    systemctl disable --now ming-storage.service 2>/dev/null || true
    rm -f /etc/systemd/system/multi-user.target.wants/ming-storage.service \
        /etc/udev/rules.d/99-ming-storage.rules

    cat > /usr/local/bin/ming-volume-automount << 'VOLUMEAUTOMOUNT'
#!/usr/bin/env bash
set -u

JSON=false
MONITOR=false
for arg in "$@"; do
    case "${arg}" in
        --json) JSON=true ;;
        --monitor) MONITOR=true ;;
        --session) MONITOR=true ;;
    esac
done

MING_TARGET_USER="${SUDO_USER:-${USER:-user}}"
if [[ "${MING_TARGET_USER}" == root || -z "${MING_TARGET_USER}" ]]; then
    MING_TARGET_USER="$(awk -F: '$3>=1000 && $3<60000 && $1!="nobody"{print $1; exit}' /etc/passwd 2>/dev/null || true)"
fi
[[ -n "${MING_TARGET_USER}" ]] || MING_TARGET_USER=user
MING_TARGET_HOME="$(getent passwd "${MING_TARGET_USER}" 2>/dev/null | cut -d: -f6)"
[[ -n "${MING_TARGET_HOME}" ]] || MING_TARGET_HOME="/home/${MING_TARGET_USER}"
mkdir -p "/media/${MING_TARGET_USER}" 2>/dev/null || true

json_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    printf '%s' "${value}"
}

emit() {
    local dev="$1" state="$2" detail="$3"
    if [[ "${JSON}" == true ]]; then
        printf '{"device":"%s","state":"%s","detail":"%s"}\n' \
            "$(json_escape "${dev}")" "$(json_escape "${state}")" "$(json_escape "${detail}")"
    fi
}

safe_name() {
    local name="$1"
    name="${name//[^A-Za-z0-9._-]/_}"
    [[ -n "${name}" ]] || name="volume"
    printf '%s' "${name}"
}

supported_fs() {
    case "$1" in
        ntfs|ntfs3|exfat|vfat|fat|ext2|ext3|ext4) return 0 ;;
        *) return 1 ;;
    esac
}

excluded_fs() {
    case "$1" in
        ""|swap|crypto_LUKS|linux_raid_member|LVM2_member|zfs_member|btrfs) return 0 ;;
        *) return 1 ;;
    esac
}

excluded_label() {
    case "$1" in
        *MING_INSTALL*|*MING_OTA*|*MING_RECOVERY*|*MING_BOOT*|MING_OS*) return 0 ;;
        *) return 1 ;;
    esac
}

protected_sources() {
    findmnt -R -no SOURCE / "${MING_TARGET_HOME}" 2>/dev/null | sed '/^$/d' | sort -u
}

live_media_root() {
    local mountpoint="$1"
    case "${mountpoint}" in
        /run/live/medium|/run/live/medium/*) printf '%s\n' "/run/live/medium"; return 0 ;;
        /lib/live/mount/medium|/lib/live/mount/medium/*) printf '%s\n' "/lib/live/mount/medium"; return 0 ;;
        *) return 1 ;;
    esac
}

expose_existing_mount() {
    local dev="$1" label="$2" uuid="$3" source="$4"
    local base target
    base="$(safe_name "${label}")"
    [[ "${base}" != volume && "${base}" != "_" ]] || base="$(safe_name "${uuid}")"
    [[ "${base}" != volume && "${base}" != "_" ]] || base="$(basename "${dev}")"
    target="/media/${MING_TARGET_USER}/${base}"
    mkdir -p "/media/${MING_TARGET_USER}" 2>/dev/null || true
    if [[ -L "${target}" && "$(readlink -- "${target}" 2>/dev/null)" == "${source}" ]]; then
        emit "${dev}" exposed "${target} -> ${source}"
        return 0
    fi
    if [[ -e "${target}" && ! -L "${target}" ]]; then
        emit "${dev}" expose_failed "target exists: ${target}"
        return 1
    fi
    rm -f -- "${target}" 2>/dev/null || true
    if ln -s -- "${source}" "${target}" 2>/dev/null; then
        emit "${dev}" exposed "${target} -> ${source}"
        return 0
    fi
    emit "${dev}" expose_failed "could not create ${target}"
    return 1
}

try_mount() {
    local dev="$1" label="$2" uuid="$3"
    local base detail
    base="$(safe_name "${label}")"
    [[ "${base}" != volume && "${base}" != "_" ]] || base="$(safe_name "${uuid}")"
    [[ "${base}" != volume && "${base}" != "_" ]] || base="$(basename "${dev}")"
    # Keep /media/<user> explicit for users coming from Windows drive letters.
    mkdir -p "/media/${MING_TARGET_USER}/${base}" 2>/dev/null || true
    if command -v udisksctl >/dev/null 2>&1; then
        if detail="$(udisksctl mount -b "${dev}" --no-user-interaction 2>&1)"; then
            emit "${dev}" mounted "${detail}"
            return 0
        fi
    elif command -v gio >/dev/null 2>&1; then
        if detail="$(gio mount -d "${dev}" 2>&1)"; then
            emit "${dev}" mounted "${detail}"
            return 0
        fi
    else
        emit "${dev}" no_mount_backend "udisksctl/gio unavailable"
        return 1
    fi
    emit "${dev}" mount_failed "${detail}"
    return 1
}

scan_once() {
    local protected
    protected="$(protected_sources)"
    while IFS= read -r -d '' NAME \
        && IFS= read -r -d '' TYPE \
        && IFS= read -r -d '' FSTYPE \
        && IFS= read -r -d '' MOUNTPOINT \
        && IFS= read -r -d '' LABEL \
        && IFS= read -r -d '' UUID \
        && IFS= read -r -d '' PARTTYPE \
        && IFS= read -r -d '' RM; do
        local dev="/dev/${NAME}"
        [[ "${TYPE}" == part ]] || continue
        if [[ -z "${FSTYPE}" ]]; then
            emit "${dev}" not_formatted "no filesystem"
            continue
        fi
        if excluded_fs "${FSTYPE}"; then
            emit "${dev}" skipped "excluded filesystem ${FSTYPE}"
            continue
        fi
        if ! supported_fs "${FSTYPE}"; then
            emit "${dev}" skipped "unsupported filesystem ${FSTYPE}"
            continue
        fi
        if [[ -n "${MOUNTPOINT}" ]]; then
            if printf '%s\n' "${protected}" | grep -Fxq "${dev}"; then
                emit "${dev}" already_mounted "protected root or home source"
                continue
            fi
            if live_root="$(live_media_root "${MOUNTPOINT}")"; then
                expose_existing_mount "${dev}" "${LABEL}" "${UUID}" "${live_root}" || true
            else
                emit "${dev}" already_mounted "${MOUNTPOINT}"
            fi
            continue
        fi
        if excluded_label "${LABEL}"; then
            emit "${dev}" skipped "protected label ${LABEL}"
            continue
        fi
        if printf '%s\n' "${protected}" | grep -Fxq "${dev}"; then
            emit "${dev}" already_mounted "protected root or home source"
            continue
        fi
        try_mount "${dev}" "${LABEL}" "${UUID}" || true
    done < <(
        lsblk --json -o NAME,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID,PARTTYPE,RM 2>/dev/null |
        python3 -c '
import json
import sys

FIELDS = ("name", "type", "fstype", "mountpoint", "label", "uuid", "parttype", "rm")

def flatten(nodes):
    for node in nodes or []:
        yield node
        yield from flatten(node.get("children", []))

for record in flatten(json.load(sys.stdin).get("blockdevices", [])):
    for field in FIELDS:
        value = record.get(field, "")
        if isinstance(value, list):
            value = next((item for item in value if item), "")
        sys.stdout.buffer.write(str(value or "").encode("utf-8", "surrogateescape") + b"\0")
'
    )
}

scan_once
if [[ "${MONITOR}" == true ]] && command -v udisksctl >/dev/null 2>&1; then
    udisksctl monitor 2>/dev/null | while IFS= read -r _event; do
        sleep 1
        scan_once
    done
fi
VOLUMEAUTOMOUNT
    chmod 0755 /usr/local/bin/ming-volume-automount

    mkdir -p /etc/xdg/autostart
    cat > /etc/xdg/autostart/ming-volume-automount.desktop << 'VOLUMEAUTOMOUNTDESKTOP'
[Desktop Entry]
Type=Application
Name=Ming Volume Automount
Name[zh_CN]=Ming 自动挂载数据分区
Comment=Mount safe data partitions in the user session without editing fstab.
Exec=/usr/local/bin/ming-volume-automount --session --json
OnlyShowIn=XFCE;
X-GNOME-Autostart-enabled=true
VOLUMEAUTOMOUNTDESKTOP

    cat > /usr/local/sbin/ming-storage-manager << 'STORAGEMGR'
#!/usr/bin/env bash
# Ming OS 无感知存储管理器：自动挂载额外数据盘并绑定到 Home 高频目录。
set -uo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "explicit authorization required: run via pkexec /usr/local/sbin/ming-storage-manager" >&2
    exit 3
fi

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

    # 保留手动 systemd 单元，只有显式执行 start 时才会运行。
    cat > /etc/systemd/system/ming-storage.service << 'STORAGESVC'
[Unit]
Description=Ming OS authorized seamless storage action (on demand only)
After=local-fs.target
ConditionPathExists=/usr/local/sbin/ming-storage-manager

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-storage-manager
RemainAfterExit=yes
STORAGESVC

    echo "无感知存储配置完成"
}

# ======================== Live / 已安装系统共同兜底 ========================

configure_installed_system_static_defaults() {
    # These files are unpacked into the target before Calamares shellprocess
    # runs. The identity script rewrites them with the final root UUID later.
    mkdir -p /etc/modules-load.d /etc/grub.d /etc/lightdm/lightdm.conf.d /boot/grub/themes/ming
    if [[ ! -s /tmp/ming-build/assets/grub-theme/theme.txt ]]; then
        echo "ERROR: Ming GRUB theme asset is missing" >&2
        return 1
    fi
    install -m 0644 /tmp/ming-build/assets/grub-theme/theme.txt /boot/grub/themes/ming/theme.txt

    cat > /etc/modules-load.d/ming-network.conf << 'STATICNETMOD'
# Ming OS: NetworkManager owns networking; kernel modalias/udev selects drivers.
STATICNETMOD

    cat > /etc/grub.d/09_ming_os << 'STATICGRUB'
#!/bin/sh
set -e

cat <<'EOF'
menuentry 'Ming OS' --class ming --class gnu-linux --class gnu --class os {
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
    initrd /initrd.img
}
submenu 'Ming OS 高级启动' --class ming --class gnu-linux --class os {
    menuentry 'Ming OS (Safe Graphics)' --class ming --class gnu-linux --class gnu --class os {
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog nomodeset vga=791
        initrd /initrd.img
    }
    menuentry 'Ming OS (Old Intel / ThinkPad / MacBook)' --class ming --class gnu-linux --class gnu --class os {
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
        initrd /initrd.img
    }
    menuentry 'Ming OS (Radeon Legacy Recovery)' --class ming --class gnu-linux --class gnu --class os {
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog radeon.modeset=1 amdgpu.modeset=0
        initrd /initrd.img
    }
    menuentry 'Ming OS (Radeon GCN Recovery SI/CIK)' --class ming --class gnu-linux --class gnu --class os {
        search --no-floppy --set=root --file /vmlinuz
        linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog amdgpu.si_support=1 radeon.si_support=0 amdgpu.cik_support=1 radeon.cik_support=0
        initrd /initrd.img
    }
}
EOF
STATICGRUB
    chmod 0755 /etc/grub.d/09_ming_os
    for noisy_grub in 10_linux 20_linux_xen 30_os-prober 30_uefi-firmware; do
        if [[ -f "/etc/grub.d/${noisy_grub}" ]]; then
            chmod 0644 "/etc/grub.d/${noisy_grub}" 2>/dev/null || true
        fi
    done

    cat > /etc/lightdm/lightdm.conf.d/60-ming-autologin.conf << 'STATICLIGHTDM'
[Seat:*]
autologin-user=user
autologin-user-timeout=0
autologin-session=ming-installer
user-session=ming-installer
greeter-session=lightdm-gtk-greeter
allow-guest=false
STATICLIGHTDM
}

# ======================== 主流程 ========================

main() {
    echo "=====> [01_base] 开始基础系统配置 <====="

    configure_apt_sources
    deploy_apt_source_selector
    deploy_apt_upgrade_convergence
    install_base_packages || return 1
    install_hardware_support_packages
    configure_installer_password_policy
    configure_locale
    configure_timezone
    configure_keyboard
    configure_users
    configure_network
    deploy_service_profile || return 1
    deploy_time_sync || return 1
    deploy_performance_status || return 1
    deploy_performance_policy || return 1
    deploy_hardware_diagnostics || return 1
    configure_os_identity
    configure_installer_identity
    configure_installed_system_static_defaults
    configure_macbook_input_modules
    configure_macbook_fan_and_disk_health
    optimize_system
    configure_seamless_storage
    configure_boot_speed

    echo "=====> [01_base] 基础系统配置完成 <====="
}

main
