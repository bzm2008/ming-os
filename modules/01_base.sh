#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 01: 基础系统配置
# ============================================================================
# 设计意图：
#   在 debootstrap 生成的最小系统上，配置 APT 源、安装核心系统组件、
#   设置语言/时区/用户/网络等基础环境，为后续模块提供可运行的基础系统。
#
# 输入：
#   环境变量: MING_OS_VERSION, MING_USER
#   （由主构建脚本通过 chroot_exec 注入）
#
# 输出：
#   配置完成的 chroot 根文件系统
#
# 关键步骤：
#   1. 配置清华 TUNA APT 源
#   2. 安装 Linux 内核、systemd、基础工具
#   3. 配置语言环境 (zh_CN.UTF-8) 与时区 (Asia/Shanghai)
#   4. 创建默认用户 ming（特权操作仅通过受控 helper）
#   5. 安装 NetworkManager 与基础网络工具
#   6. 配置系统标识为 Ming OS
# ============================================================================

set -uo pipefail

# Keep the user-facing release label separate from the monotonic transactional
# version used by the OTA verifier.  Older callers that only provide
# MING_OS_VERSION continue to build with that value for development images.
MING_OS_RELEASE_STAGE="${MING_OS_RELEASE_STAGE:-development}"
case "${MING_OS_RELEASE_STAGE}" in
    stable)
        MING_OS_UPDATE_VERSION="${MING_OS_UPDATE_VERSION:-26.4.0.1}"
        MING_OS_RELEASE_LABEL="${MING_OS_RELEASE_LABEL:-正式版}"
        ;;
    development)
        MING_OS_UPDATE_VERSION="${MING_OS_UPDATE_VERSION:-26.4.0.1-development}"
        MING_OS_RELEASE_LABEL="${MING_OS_RELEASE_LABEL:-开发构建}"
        ;;
    *)
        echo "[01_base][ERROR] invalid MING_OS_RELEASE_STAGE" >&2
        exit 2
        ;;
esac

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
        dkms \
        systemd \
        systemd-sysv \
        systemd-timesyncd \
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
        systemd-oomd \
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
        thermald

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
        intel-microcode; do
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
# Network and Bluetooth drivers (iwlwifi, ath9k, e1000e, btusb, btintel,
# btrtl, btbcm, ath3k) are selected by modalias/udev.  The explicit radio
# repair tool may load Bluetooth modules only after a user-requested diagnosis.
modules=(
usbhid
i2c_hid
hid_multitouch
bcm5974
hid_apple
applespi
applesmc
apple_gmux
spi_pxa2xx_platform
spi_pxa2xx_pci
thinkpad_acpi
ideapad_laptop
huawei_wmi
intel_lpss
intel_lpss_pci
k10temp
surface_aggregator
surface_hid_core
)

mkdir -p /tmp
for module in "${modules[@]}"; do
    if modprobe -q "${module}" 2>/dev/null; then
        printf '%s loaded %s\n' "$(date '+%F %T')" "${module}" >> "${LOG}" 2>/dev/null || true
    else
        printf '%s skipped %s\n' "$(date '+%F %T')" "${module}" >> "${LOG}" 2>/dev/null || true
    fi
done

{
    printf '%s Broadcom devices and kernel bindings\n' "$(date '+%F %T')"
    lspci -Dnnk -d 14e4: 2>/dev/null || true
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
After=local-fs.target systemd-modules-load.service
Before=NetworkManager.service bluetooth.service display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-hardware-preload
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
HWPRELOADSVC
    systemctl enable ming-hardware-preload.service 2>/dev/null || true

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
    # Fresh systems have no reusable factory credential. Root stays locked.
    passwd -l root

    # 创建默认用户 ming
    useradd -m -s /bin/bash -c "Ming OS User" "${MING_USER}"
    passwd -d "${MING_USER}"

    # 创建必要的组（如果不存在）
    for grp in lpadmin plugdev nopasswdlogin autologin render; do
        getent group "${grp}" >/dev/null 2>&1 || groupadd -r "${grp}" 2>/dev/null || true
    done

    # 将 ming 用户加入必要组（逐个添加，跳过不存在的组）
    for grp in adm cdrom dip plugdev lpadmin netdev audio video render input scanner bluetooth nopasswdlogin autologin; do
        getent group "${grp}" >/dev/null 2>&1 && usermod -aG "${grp}" "${MING_USER}" || true
    done
    gpasswd -d "${MING_USER}" sudo >/dev/null 2>&1 || true

    # Privileged desktop actions use narrowly scoped polkit helpers.
    rm -f /etc/sudoers.d/"${MING_USER}"

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
        gir1.2-nm-1.0 \
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

    systemctl disable --now networking.service 2>/dev/null || true
    systemctl disable --now systemd-networkd.service 2>/dev/null || true
    systemctl enable NetworkManager.service
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

    # Repair only stale, image-generated wired profiles.  The helper refuses
    # user profiles, static routes and 802.1x before performing an atomic write.
    cat > /etc/systemd/system/ming-network-profile-migrate.service << 'MINGNMPROFILE'
[Unit]
Description=Ming OS safe NetworkManager profile migration
After=local-fs.target
Before=NetworkManager.service
ConditionPathExists=/usr/local/bin/ming-device-control

[Service]
Type=oneshot
ExecStart=/usr/local/bin/ming-device-control migrate-network-profiles --json

[Install]
WantedBy=multi-user.target
MINGNMPROFILE
    systemctl enable ming-network-profile-migrate.service 2>/dev/null || true

    # Keep the normal AC/battery policy under TLP.  Only after repeated drops
    # do we disable Wi-Fi power saving, and then only for that exact profile.
    install -d -m 0755 /etc/NetworkManager/dispatcher.d
    cat > /etc/NetworkManager/dispatcher.d/80-ming-wifi-reliability << 'MINGWIFIRELIABILITY'
#!/usr/bin/env bash
set -uo pipefail

ifname="${1:-}"
action="${2:-}"
[[ -n "${ifname}" && -d "/sys/class/net/${ifname}/wireless" ]] || exit 0
[[ "${action}" == "down" ]] || exit 0
[[ -n "${CONNECTION_UUID:-}" ]] || exit 0

# Do not reinterpret an intentional user disconnect as a hardware fault.
# NetworkManager's C.UTF-8 reason is the only trigger for this scoped recovery.
reason=$(env LC_ALL=C.UTF-8 nmcli -g GENERAL.REASON device show "${ifname}" 2>/dev/null || true)
case "${reason,,}" in
    *supplicant*|*ip-config*|*carrier*|*firmware*|*timeout*|*failed*) ;;
    *) exit 0 ;;
esac

state_dir=/run/ming-os/wifi-drop-history
install -d -m 0700 "${state_dir}"
key=$(printf '%s' "${CONNECTION_UUID}" | sha256sum | awk '{print $1}')
history="${state_dir}/${key}"
now=$(date +%s)
temporary=$(mktemp "${history}.XXXXXX") || exit 0
{
    [[ -f "${history}" ]] && awk -v cutoff="$((now - 600))" '$1 >= cutoff' "${history}"
    printf '%s\n' "${now}"
} > "${temporary}"
chmod 0600 "${temporary}"
mv -f "${temporary}" "${history}"

if [[ $(wc -l < "${history}") -ge 3 ]]; then
    env LC_ALL=C.UTF-8 nmcli --wait 5 \
        connection modify uuid "${CONNECTION_UUID}" 802-11-wireless.powersave 2 \
        >/dev/null 2>&1 || true
fi
exit 0
MINGWIFIRELIABILITY
    chmod 0755 /etc/NetworkManager/dispatcher.d/80-ming-wifi-reliability

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
    local synchronized service network state
    synchronized="$(read_timedatectl 5 show -p NTPSynchronized --value)"
    service="$(timeout --foreground 5s systemctl is-active systemd-timesyncd 2>/dev/null || true)"
    network="$(timeout --foreground 5s nm-online -q -t 4 >/dev/null 2>&1 && printf online || printf offline)"
    case "${synchronized,,}" in
        yes|true|1) state=synchronized ;;
        *)
            if [[ "${service}" == active || "${service}" == activating ]]; then
                state=waiting
            else
                state=error
            fi
            ;;
    esac
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

    # Keep a bounded retry path for systems that boot with an already-active
    # connection (where NetworkManager may not emit a fresh dispatcher event).
    # The timer is deliberately independent of network-online.target so a
    # missing network can never hold the graphical login hostage.
    cat > /etc/systemd/system/ming-time-sync.service << 'MINGTIMESYNCSVC'
[Unit]
Description=Ming OS bounded network time synchronisation
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-time-sync sync
TimeoutStartSec=60s

[Install]
WantedBy=multi-user.target
MINGTIMESYNCSVC

    cat > /etc/systemd/system/ming-time-sync.timer << 'MINGTIMESYNCTIMER'
[Unit]
Description=Ming OS periodic time synchronisation retry
After=graphical.target

[Timer]
OnBootSec=90s
OnUnitActiveSec=6h
RandomizedDelaySec=5m
Persistent=true
Unit=ming-time-sync.service

[Install]
WantedBy=timers.target
MINGTIMESYNCTIMER
    systemctl enable ming-time-sync.timer 2>/dev/null || true
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
    # The policy daemon is a narrow root boundary.  The desktop talks to the
    # installed aliases, never to sysfs/cgroup files or pkexec directly.
    local asset="/tmp/ming-build/assets/ming-performance-policy.py"
    local prefetch_asset="/tmp/ming-build/assets/ming-prefetch.py"
    local lib_dir="/usr/local/lib/ming-os"
    local uid
    [[ -s "${asset}" ]] || {
        echo "[ERROR] missing performance policy asset: ${asset}" >&2
        return 1
    }
    [[ -s "${prefetch_asset}" ]] || {
        echo "[ERROR] missing prefetch asset: ${prefetch_asset}" >&2
        return 1
    }
    install -d -m 0755 "${lib_dir}" /usr/local/bin /run/ming-os
    install -m 0755 "${asset}" "${lib_dir}/ming-performance-policy.py" || return 1
    install -m 0755 "${prefetch_asset}" "${lib_dir}/ming-prefetch.py" || return 1
    cat > /usr/local/bin/ming-prefetch << 'PREFETCHALIAS'
#!/bin/sh
exec /usr/bin/python3 /usr/local/lib/ming-os/ming-prefetch.py "$@"
PREFETCHALIAS
    [[ -s /usr/local/bin/ming-prefetch ]] || {
        echo "[ERROR] failed to generate ming-prefetch alias" >&2
        return 1
    }
    chmod 0755 /usr/local/bin/ming-prefetch
    for alias in ming-interaction-boost ming-background-policy ming-performance-policy; do
        cat > "/usr/local/bin/${alias}" << POLICYALIAS
#!/bin/sh
exec /usr/bin/python3 ${lib_dir}/ming-performance-policy.py "\$@"
POLICYALIAS
        [[ -s /usr/local/bin/${alias} ]] || {
            echo "[ERROR] failed to generate ${alias} alias" >&2
            return 1
        }
        chmod 0755 "/usr/local/bin/${alias}"
    done
    uid="$(id -u "${MING_USER}" 2>/dev/null || echo 1000)"
    cat > /etc/systemd/system/ming-resource-policy.service << POLICYUNIT
[Unit]
Description=Ming adaptive foreground and background resource policy
After=graphical.target
ConditionPathExists=${lib_dir}/ming-performance-policy.py

[Service]
Type=simple
ExecStart=/usr/local/bin/ming-performance-policy daemon --uid ${uid}
Restart=always
RestartSec=1s
NoNewPrivileges=false
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/${MING_USER}/.cache/ming-os

[Install]
WantedBy=graphical.target
POLICYUNIT
    [[ -s /etc/systemd/system/ming-resource-policy.service ]] || {
        echo "[ERROR] failed to generate resource policy unit" >&2
        return 1
    }
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable ming-resource-policy.service 2>/dev/null || true
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
    exec pkexec /usr/local/sbin/ming-radio-repair bluetooth
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
VERSION="${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL}"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL}"
VERSION_ID="${MING_OS_UPDATE_VERSION}"
MING_DISPLAY_VERSION="${MING_OS_VERSION}"
MING_RELEASE_STAGE="${MING_OS_RELEASE_STAGE}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
OSRELEASE

    # 更新 issue 文件（控制台登录提示）
    cat > /etc/issue << ISSUE
Ming OS ${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL} - 层层精简，层层用心

ISSUE

    cat > /etc/issue.net << ISSUENET
Ming OS ${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL}
ISSUENET

    # 自定义 lsb_release 信息
    apt install -y --no-install-recommends lsb-release
    mkdir -p /etc/lsb-release.d
    cat > /etc/lsb-release << LSBRELEASE
DISTRIB_ID=MingOS
DISTRIB_RELEASE=${MING_OS_VERSION}
DISTRIB_CODENAME=ming
DISTRIB_DESCRIPTION="Ming OS ${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL}"
LSBRELEASE

    # 确保 /etc/debian_version 显示 Debian 13 (Trixie)，而非历史遗留的12
    echo "trixie/sid" > /etc/debian_version

    cat > /etc/ming-release << RELEASE
Ming OS ${MING_OS_VERSION} ${MING_OS_RELEASE_LABEL}
RELEASE
    mkdir -p /usr/share /etc/default/grub.d /boot/grub/themes/ming
    ln -sf /etc/ming-release /usr/share/ming-release
    if [[ ! -s /tmp/ming-build/assets/grub-theme/theme.txt ]]; then
        echo "ERROR: Ming GRUB theme asset is missing" >&2
        return 1
    fi
    install -m 0644 /tmp/ming-build/assets/grub-theme/theme.txt /boot/grub/themes/ming/theme.txt
    cat > /etc/default/grub.d/10-ming-os.cfg << GRUBCFG
GRUB_DISTRIBUTOR="Ming OS"
GRUB_THEME="/boot/grub/themes/ming/theme.txt"
GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
GRUB_TERMINAL_INPUT=console
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_RECORDFAIL_TIMEOUT=0
GRUB_DISABLE_SUBMENU=true
GRUB_DISABLE_OS_PROBER=true
GRUB_DISABLE_RECOVERY=true
GRUBCFG
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

log=/tmp/ming-installer/ota-preflight.log
marker=/run/ming-ota-preflight.ok
mkdir -p /tmp/ming-installer /run
exec >>"${log}" 2>&1
rm -f "${marker}"

/usr/local/sbin/ming-installer-verify receipt --begin-attempt || {
    echo "cannot begin a fresh authoritative Calamares target receipt attempt"
    exit 31
}

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

release_field() {
    local key="$1"
    awk -F= -v key="${key}" '$1 == key {value=substr($0, index($0, "=") + 1); gsub(/^"|"$/, "", value); print value; exit}' \
        /etc/os-release 2>/dev/null
}

version="${MING_OS_VERSION:-$(release_field MING_DISPLAY_VERSION)}"
update_version="${MING_OS_UPDATE_VERSION:-$(release_field VERSION_ID)}"
release_stage="${MING_OS_RELEASE_STAGE:-$(release_field MING_RELEASE_STAGE)}"
version="${version:-26.4.0}"
case "${release_stage}" in
    stable)
        update_version="${update_version:-26.4.0.1}"
        release_label="正式版"
        ;;
    development)
        update_version="${update_version:-26.4.0.1-development}"
        release_label="开发构建"
        ;;
    *)
        echo "ERROR: live release stage is invalid" >&2
        exit 30
        ;;
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

has_authoritative_root_fstab() {
    local fstab="$1"
    [[ -f "${fstab}" ]] || return 1
    awk -v expected_uuid="UUID=${root_uuid}" -v expected_fstype="${root_fstype}" '
        /^[[:space:]]*#/ || NF < 3 { next }
        $2 == "/" && $1 == expected_uuid && $3 == expected_fstype { found=1 }
        END { exit found ? 0 : 1 }
    ' "${fstab}"
}

ensure_persistent_root_fstab() {
    local fstab temporary
    fstab="${target}/etc/fstab"
    if has_authoritative_root_fstab "${fstab}"; then
        return 0
    fi

    mkdir -p "${target}/etc"
    temporary="$(mktemp "${fstab}.ming.XXXXXX")" || return 1
    if [[ -f "${fstab}" ]]; then
        if ! awk '/^[[:space:]]*#/ || NF < 3 || $2 != "/" { print }' "${fstab}" > "${temporary}"; then
            rm -f -- "${temporary}"
            echo "ERROR: failed to preserve non-root fstab entries" >&2
            return 1
        fi
    else
        : > "${temporary}"
    fi
    if ! printf 'UUID=%s / %s defaults 0 1\n' "${root_uuid}" "${root_fstype}" >> "${temporary}"; then
        rm -f -- "${temporary}"
        echo "ERROR: failed to append authoritative root fstab entry" >&2
        return 1
    fi
    if ! chmod 0644 "${temporary}" || ! mv -f "${temporary}" "${fstab}"; then
        rm -f -- "${temporary}"
        echo "ERROR: failed to atomically replace fstab" >&2
        return 1
    fi
    echo "Rebuilt authoritative root fstab entry: UUID=${root_uuid} / ${root_fstype}" >&2
}

ensure_persistent_root_fstab || exit 30

write_file() {
    local path="$1"
    shift
    mkdir -p "$(dirname "${target}${path}")"
    cat > "${target}${path}"
}

ensure_ming_user() {
    local user_name="user"
    local user_home="/home/${user_name}"
    local groups=(
        users adm cdrom dip plugdev lp lpadmin netdev audio video render input
        scanner bluetooth nopasswdlogin autologin
    )
    local grp

    mkdir -p "${target}/etc/sudoers.d" "${target}/etc/lightdm/lightdm.conf.d" "${target}${user_home}"

    for grp in "${groups[@]}"; do
        chroot "${target}" getent group "${grp}" >/dev/null 2>&1 \
            || chroot "${target}" groupadd -r "${grp}" >/dev/null 2>&1 \
            || true
    done
    chroot "${target}" gpasswd -d "${user_name}" sudo >/dev/null 2>&1 || true

    if chroot "${target}" getent passwd "${user_name}" >/dev/null 2>&1; then
        chroot "${target}" usermod -d "${user_home}" -s /bin/bash -c "Ming OS User" "${user_name}" >/dev/null 2>&1 || true
    else
        chroot "${target}" useradd -m -d "${user_home}" -s /bin/bash -c "Ming OS User" "${user_name}" >/dev/null 2>&1 || true
    fi

    chroot "${target}" passwd -d "${user_name}" >/dev/null 2>&1 || return 1
    chroot "${target}" passwd -l root >/dev/null 2>&1 || return 1

    for grp in "${groups[@]}"; do
        chroot "${target}" getent group "${grp}" >/dev/null 2>&1 \
            && chroot "${target}" usermod -aG "${grp}" "${user_name}" >/dev/null 2>&1 \
            || true
    done

    chroot "${target}" chown "${user_name}:${user_name}" "${user_home}" >/dev/null 2>&1 || true
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

safe_graphics_requested() {
    grep -Eq '(^|[[:space:]])ming\.safe_graphics=1([[:space:]]|$)' /proc/cmdline 2>/dev/null \
        || grep -Eq '(^|[[:space:]])nomodeset([[:space:]]|$)' /proc/cmdline 2>/dev/null
}

ota_install_requested() {
    grep -Eq '(^|[[:space:]])ming\.ota=1([[:space:]]|$)' /proc/cmdline 2>/dev/null
}

configure_safe_graphics_default() {
    local safe_cfg="${target}/etc/default/grub.d/20-ming-safe-graphics.cfg"

    if safe_graphics_requested; then
        cat > "${safe_cfg}" <<'SAFEGRAPHICSDEFAULT'
# Created when Ming OS was installed through the explicit Safe Graphics entry.
# Keep that fallback as the default until the user deliberately changes it.
GRUB_DEFAULT="Ming OS (Safe Graphics)"
GRUB_TIMEOUT_STYLE=menu
GRUB_TIMEOUT=8
SAFEGRAPHICSDEFAULT
    elif ! ota_install_requested; then
        # A normal fresh install must not inherit a stale compatibility default.
        rm -f "${safe_cfg}"
    fi
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
VERSION="${version} ${release_label}"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${version} ${release_label}"
VERSION_ID="${update_version}"
MING_DISPLAY_VERSION="${version}"
MING_RELEASE_STAGE="${release_stage}"
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
DISTRIB_DESCRIPTION="Ming OS ${version} ${release_label}"
LSBRELEASE

write_file /etc/issue <<ISSUE
Ming OS ${version} ${release_label} - 层层精简，层层用心

ISSUE

write_file /etc/issue.net <<ISSUENET
Ming OS ${version} ${release_label}
ISSUENET

write_file /etc/ming-release <<MINGRELEASE
NAME="Ming OS"
VERSION="${version} ${release_label}"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${version} ${release_label}"
VERSION_ID="${update_version}"
MING_DISPLAY_VERSION="${version}"
MING_RELEASE_STAGE="${release_stage}"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
MINGRELEASE

write_file /etc/ming-version <<MINGVERSION
${update_version}
MINGVERSION

write_file /etc/ming-display-version <<MINGDISPLAYVERSION
${version}
MINGDISPLAYVERSION

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
ensure_ming_user
ensure_kernel_boot_links

mkdir -p "${target}/etc/grub.d"
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

menuentry 'Ming OS (Safe Graphics)' --class ming --class gnu-linux --class gnu --class os {
    load_video
    insmod gzio
    insmod part_msdos
    insmod part_gpt
    insmod ext2
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog ming.safe_graphics=1 nomodeset vga=791
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
EOF
TARGETGRUBENTRY
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
if grep -Fq '__MING_ROOT_UUID__' "${grub_template}"; then
    echo "ERROR: Ming GRUB template still contains __MING_ROOT_UUID__" >&2
    exit 30
fi
if ! grep -Fq "root=UUID=${root_uuid}" "${grub_template}"; then
    echo "ERROR: Ming GRUB template does not contain the authoritative root UUID" >&2
    exit 30
fi

for noisy_grub in 10_linux 20_linux_xen 30_os-prober 30_uefi-firmware; do
    if [[ -f "${target}/etc/grub.d/${noisy_grub}" ]]; then
        if [[ "${noisy_grub}" == "10_linux" ]]; then
            # Keep Debian's official kernel generator executable so GRUB
            # retains every installed kernel/initramfs as a real fallback.
            chmod 0755 "${target}/etc/grub.d/${noisy_grub}" 2>/dev/null || true
        else
            chmod 0644 "${target}/etc/grub.d/${noisy_grub}" 2>/dev/null || true
        fi
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
# 老旧硬件友好 + 隐藏内核日志：安静启动、低日志级别、隐藏 systemd 状态刷屏
GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog"
GRUB_TERMINAL_INPUT=console
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_RECORDFAIL_TIMEOUT=0
GRUB_DISABLE_SUBMENU=true
GRUB_DISABLE_OS_PROBER=true
GRUB_DISABLE_RECOVERY=true
GRUBCFG
configure_safe_graphics_default

mkdir -p "${target}/usr/share"
ln -sf /etc/ming-release "${target}/usr/share/ming-release" 2>/dev/null || true

mkdir -p "${target}/etc"
cat > "${target}/etc/machine-info" <<MACHINEINFO
PRETTY_HOSTNAME="Ming OS"
MACHINEINFO

mkdir -p "${target}/etc/lightdm/lightdm.conf.d"
cat > "${target}/etc/lightdm/lightdm.conf.d/60-ming-autologin.conf" <<LIGHTDM
[Seat:*]
autologin-user=user
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
LIGHTDM

# The installed system should boot directly to the graphical desktop. Calamares
# invokes this while the target is offline, so write stable unit links instead
# of treating an offline systemctl query as proof that LightDM is unavailable.
find_systemd_unit() {
    local name="$1" candidate
    for candidate in "/lib/systemd/system/${name}" "/usr/lib/systemd/system/${name}"; do
        if [[ -f "${target}${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

ensure_graphical_boot_chain() {
    local graphical_unit lightdm_unit
    graphical_unit="$(find_systemd_unit graphical.target)" || {
        echo "ERROR: graphical.target is missing from the installed system" >&2
        return 1
    }
    lightdm_unit="$(find_systemd_unit lightdm.service)" || {
        echo "ERROR: LightDM service is missing from the installed system" >&2
        return 1
    }
    mkdir -p "${target}/etc/systemd/system/graphical.target.wants"
    ln -sfn "${graphical_unit}" "${target}/etc/systemd/system/default.target"
    ln -sfn "${lightdm_unit}" "${target}/etc/systemd/system/display-manager.service"
    ln -sfn "${lightdm_unit}" "${target}/etc/systemd/system/graphical.target.wants/lightdm.service"
    chroot "${target}" systemctl enable lightdm.service >/dev/null 2>&1 || true
}

ensure_graphical_boot_chain || exit 30

rm -f "${target}/etc/sudoers.d/user"

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
    "${target}/var/lib/ming-os/trusted-desktops/Install Ming OS.desktop" \
    "${target}/usr/share/xsessions/ming-installer.desktop" \
    "${target}/usr/share/icons/hicolor/scalable/apps/ming-os-install.svg" \
    "${target}/usr/local/sbin/sfdisk" \
    "${target}/etc/systemd/system/ming-live-installer.service" \
    "${target}/etc/systemd/system/graphical.target.wants/ming-live-installer.service" \
    "${target}"/home/*/.config/ming-os/desktop-layout.json \
    "${target}"/home/*/.config/ming-os/desktop-layout.last-good.json \
    "${target}/etc/skel/.config/ming-os/desktop-layout.json" \
    "${target}/etc/skel/.config/ming-os/desktop-layout.last-good.json" \
    2>/dev/null || true

for installer_entry in \
    "${target}"/home/*/.config/autostart/calamares-live.desktop \
    "${target}"/home/*/Desktop/calamares.desktop \
    "${target}"/home/*/Desktop/install-debian.desktop \
    "${target}"/home/*/Desktop/"Install Debian.desktop" \
    "${target}"/home/*/Desktop/"安装 Debian.desktop" \
    "${target}"/home/*/Desktop/"Install Ming OS.desktop" \
    "${target}"/etc/skel/.config/autostart/calamares-live.desktop \
    "${target}"/etc/skel/Desktop/calamares.desktop \
    "${target}"/etc/skel/Desktop/install-debian.desktop \
    "${target}"/etc/skel/Desktop/"Install Debian.desktop" \
    "${target}"/etc/skel/Desktop/"安装 Debian.desktop" \
    "${target}/etc/skel/Desktop/Install Ming OS.desktop"; do
    [[ -e "${installer_entry}" ]] && rm -f "${installer_entry}" 2>/dev/null || true
done

# NetworkManager is the sole network owner in a newly installed target.
chroot "${target}" systemctl disable networking.service systemd-networkd.service 2>/dev/null || true
chroot "${target}" systemctl enable NetworkManager.service 2>/dev/null || return 1
# Let modalias/udev select Ethernet and Wi-Fi drivers.  Force-loading a popular
# module can bind the wrong device, hide carrier, or delay boot on unrelated
# old hardware.
mkdir -p "${target}/etc/modules-load.d"
rm -f "${target}/etc/modules-load.d/ming-network.conf"
# 确保固件被 initramfs 包含（update-initramfs 已在前面运行）
chroot "${target}" depmod -a 2>/dev/null || true
MINGIDENTITY
    chmod +x /usr/local/sbin/ming-fix-installed-identity

    cat > /usr/local/sbin/ming-safe-graphics-persist <<'MINGSAFEGRAPHICSPERSIST'
#!/usr/bin/env bash
set -euo pipefail

safe_cfg=/etc/default/grub.d/20-ming-safe-graphics.cfg

safe_graphics_requested() {
    grep -Eq '(^|[[:space:]])ming\.safe_graphics=1([[:space:]]|$)' /proc/cmdline 2>/dev/null \
        || grep -Eq '(^|[[:space:]])nomodeset([[:space:]]|$)' /proc/cmdline 2>/dev/null
}

write_safe_default() {
    local tmp changed=false
    install -d -m 0755 /etc/default/grub.d
    tmp="$(mktemp /etc/default/grub.d/.20-ming-safe-graphics.XXXXXX)"
    trap 'rm -f -- "${tmp:-}"' EXIT
    cat > "${tmp}" <<'SAFEGRAPHICSDEFAULT'
# Keep the known-working emergency video path selected after a Safe Graphics boot.
GRUB_DEFAULT="Ming OS (Safe Graphics)"
GRUB_TIMEOUT_STYLE=menu
GRUB_TIMEOUT=8
SAFEGRAPHICSDEFAULT
    chmod 0644 "${tmp}"
    if [[ ! -f "${safe_cfg}" ]] || ! cmp -s "${tmp}" "${safe_cfg}"; then
        mv -f "${tmp}" "${safe_cfg}"
        changed=true
    fi
    ${changed} || return 0
    if command -v update-grub >/dev/null 2>&1; then
        update-grub
    elif command -v grub-mkconfig >/dev/null 2>&1; then
        grub-mkconfig -o /boot/grub/grub.cfg
    fi
}

safe_graphics_requested || exit 0
write_safe_default
MINGSAFEGRAPHICSPERSIST
    chmod 0755 /usr/local/sbin/ming-safe-graphics-persist

    cat > /etc/systemd/system/ming-safe-graphics-persist.service <<'MINGSAFEGRAPHICSSERVICE'
[Unit]
Description=Preserve Ming OS Safe Graphics boot selection
After=local-fs.target
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-safe-graphics-persist

[Install]
WantedBy=multi-user.target
MINGSAFEGRAPHICSSERVICE
    ln -sfn ../ming-safe-graphics-persist.service \
        /etc/systemd/system/multi-user.target.wants/ming-safe-graphics-persist.service

    cat > /usr/local/sbin/ming-install-bootloader << 'MINGBOOTLOADER'
#!/usr/bin/env bash
set -euo pipefail

LOG=/tmp/ming-installer/bootloader.log
mkdir -p /tmp/ming-installer
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
current_root_source="$(findmnt -n -o SOURCE --target "${root}" 2>/dev/null || true)"
[[ "${current_root_source}" == "${root_source}" ]] || {
    echo "ERROR: authoritative receipt source no longer matches the mounted target"
    exit 20
}
if ! grep -Fq "root=UUID=${root_uuid}" "${root}/etc/grub.d/09_ming_os" \
    || grep -Fq '__MING_ROOT_UUID__' "${root}/etc/grub.d/09_ming_os"; then
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

# The identity helper runs before this final bootloader stage and may not see
# Calamares' target mount in every installation environment. Resolve the UUID
# from the verified root block device here, immediately before GRUB generation.
root_uuid="$(blkid -s UUID -o value "${root_source}" 2>/dev/null | head -n 1 || true)"
if [[ ! "${root_uuid}" =~ ^[A-Fa-f0-9-]{4,128}$ ]]; then
    root_uuid="$(awk '$2 == "/" && $1 ~ /^UUID=/ {sub(/^UUID=/, "", $1); print $1; exit}' "${root}/etc/fstab" 2>/dev/null || true)"
fi
if [[ ! "${root_uuid}" =~ ^[A-Fa-f0-9-]{4,128}$ ]]; then
    echo "ERROR: cannot resolve installed root UUID for GRUB"
    exit 22
fi
grub_custom_entry="${root}/etc/grub.d/09_ming_os"
if [ ! -f "${grub_custom_entry}" ]; then
    echo "ERROR: Ming custom GRUB entry is missing"
    exit 22
fi
sed -i "s/__MING_ROOT_UUID__/${root_uuid}/g" "${grub_custom_entry}"
if grep -Fq '__MING_ROOT_UUID__' "${grub_custom_entry}"; then
    echo "ERROR: Ming custom GRUB entry still contains a root UUID placeholder"
    exit 22
fi
echo "root_uuid=${root_uuid}"

for mountpoint in dev proc sys run; do
    mkdir -p "${root}/${mountpoint}"
done
mountpoint -q "${root}/dev" || mount --bind /dev "${root}/dev"
mountpoint -q "${root}/proc" || mount -t proc proc "${root}/proc"
mountpoint -q "${root}/sys" || mount -t sysfs sysfs "${root}/sys"
mountpoint -q "${root}/run" || mount --bind /run "${root}/run"

mkdir -p "${root}/boot/grub"

remove_live_grub_fragments() {
    local fragment name
    rm -f -- "${root}/boot/grub/loopback.cfg" "${root}/boot/grub/grub-live.cfg"
    [[ -d "${root}/etc/grub.d" ]] || return 0
    while IFS= read -r -d '' fragment; do
        name="$(basename "${fragment}")"
        case "${name}" in
            09_ming_os|40_ming_transaction) continue ;;
        esac
        if grep -Eq 'boot=live|ming\.installer=1|Live Mode|Install Ming OS' "${fragment}"; then
            echo "Removing inherited Live GRUB fragment: ${name}"
            rm -f -- "${fragment}"
        fi
    done < <(find "${root}/etc/grub.d" -maxdepth 1 -type f -print0)
}

reject_live_grub_entries() {
    local grub_cfg="$1"
    if grep -Eq 'boot=live|ming\.installer=1|Live Mode|Install Ming OS' "${grub_cfg}"; then
        echo "ERROR: installed GRUB configuration still contains Live or installer entries"
        return 1
    fi
    return 0
}

remove_live_grub_fragments

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
            --recheck || echo "WARN: UEFI NVRAM grub-install failed; keeping removable fallback"
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

if [ -d /sys/firmware/efi ]; then
    install_uefi_grub || {
        echo "ERROR: UEFI bootloader installation failed; refusing an unusable BIOS fallback"
        exit 23
    }
    echo "Ming UEFI bootloader path completed"
    prefer_ming_uefi_boot
else
    install_bios_grub
    echo "Ming BIOS bootloader path completed"
fi

# A GRUB core without a usable config drops users at grub>, so final config
# generation and validation are installation hard gates.
if [ -x "${root}/usr/sbin/update-grub" ]; then
    chroot "${root}" /usr/sbin/update-grub \
        >/tmp/ming-installer/update-grub.log 2>&1 || exit 22
elif [ -x "${root}/usr/sbin/grub-mkconfig" ]; then
    chroot "${root}" /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg \
        >/tmp/ming-installer/update-grub.log 2>&1 || exit 22
else
    grub-mkconfig -o "${root}/boot/grub/grub.cfg" \
        >/tmp/ming-installer/update-grub.log 2>&1 || exit 22
fi

if [ -e "${root}/boot/grub/grub.cfg.new" ]; then
    echo "ERROR: uncommitted grub.cfg.new remains after GRUB generation"
    exit 22
fi
if ! reject_live_grub_entries "${root}/boot/grub/grub.cfg"; then
    exit 22
fi

validate_final_grub_root_uuid() {
    local grub_cfg="$1"
    awk -v expected="root=UUID=${root_uuid}" '
        /^[[:space:]]*linux[[:space:]]+\/vmlinuz([[:space:]]|$)/ {
            ming_linux_count++
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
                print "ERROR: final grub.cfg has no Ming linux /vmlinuz stanzas" > "/dev/stderr"
                exit 1
            }
            exit invalid ? 1 : 0
        }
    ' "${grub_cfg}"
}

prepare_transaction_grubenv() {
    # A fresh target may receive a new GRUB environment during grub-install.
    # Recreate the known-safe default before declaring its embedded OTA
    # runtime ready; this is independent of the legacy recovery ISO path.
    chroot "${root}" grub-editenv /boot/grub/grubenv create || return 1
    chroot "${root}" grub-editenv /boot/grub/grubenv set saved_entry=ming-legacy || return 1
    chroot "${root}" grub-editenv /boot/grub/grubenv list \
        | grep -Fxq 'saved_entry=ming-legacy'
}

verify_embedded_ota_runtime() {
    local capability="/usr/local/lib/ming-update/ming-ota-bootstrap-capability.py"

    [[ -x "${root}${capability}" ]] || {
        echo "ERROR: embedded transactional OTA capability verifier is missing"
        return 1
    }
    prepare_transaction_grubenv || {
        echo "ERROR: cannot initialize the transactional GRUB environment"
        return 1
    }
    chroot "${root}" "${capability}" --write-marker || {
        echo "ERROR: embedded transactional OTA runtime is incomplete"
        return 1
    }
    MING_OTA_RUN_IN_SLICE=1 chroot "${root}" /usr/local/bin/ming-update status --json \
        | python3 -c '
import json
import sys
value = json.load(sys.stdin)
if value.get("schema") != "ming.update.cli.v1":
    raise SystemExit("embedded OTA status schema is invalid")
if value.get("ok") is not True or value.get("error_code") is not None:
    raise SystemExit("embedded OTA runtime did not become ready")
if value.get("bootstrap_required"):
    raise SystemExit("fresh image must not require an OTA bootstrap")
' || {
        echo "ERROR: embedded transactional OTA status readback failed"
        return 1
    }
}

if [ ! -s "${root}/boot/grub/grub.cfg" ] \
    || grep -Fq '__MING_ROOT_UUID__' "${root}/boot/grub/grub.cfg"; then
    echo "ERROR: final grub.cfg is missing, empty, or still contains a placeholder"
    exit 22
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
verify_embedded_ota_runtime || exit 24
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

LOG=/tmp/ming-installer/finish-reboot.log
mkdir -p /tmp/ming-installer
exec >>"${LOG}" 2>&1

echo "==== Ming finish reboot $(date -Is) ===="
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
efiSystemPartition: "/boot/efi"
userSwapChoices:
  - none
  - small
  - file
drawNestedPartitions: false
alwaysShowPartitionLabels: true
defaultFileSystemType: "ext4"
# 只保留 ext4，移除 btrfs：
# btrfs 在已有 Fedora/旧 btrfs 卷的磁盘上创建分区会失败（图二错误）
# ext4 稳定可靠，是绝大多数老机器的最佳选择
availableFileSystemTypes:
  - "ext4"
initialPartitioningChoice: none
initialSwapChoice: none
requiredStorage: 12
# 保留手动分区入口，同时让全盘安装选项保持可见。
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
  - welcome
  - partition
  - summary
- exec:
  - shellprocess@ming-ota-preflight
  - ming-ota-target-guard@ming-ota-target-guard
  - partition
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
NoDisplay=true
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
    vfs_cache_pressure=60
dirty_ratio=12
dirty_background_ratio=4

if [[ "${mem_mb}" -le 2600 ]]; then
    profile="low-memory"
    zram_percent=100
    swappiness=80
    vfs_cache_pressure=100
    dirty_ratio=8
    dirty_background_ratio=2
elif [[ "${mem_mb}" -le 4200 ]]; then
    profile="compact"
    zram_percent=75
    swappiness=50
    vfs_cache_pressure=80
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
# Ming OS 26.3.2 内核深度优化
# 目标：兼容 2GB+ RAM / 老 i3-i5-E3 / 老 AMD / 机械硬盘，同时保持桌面流畅

# ---- 内存：老机器优先减少换页 ----
vm.swappiness=10
vm.vfs_cache_pressure=60
vm.page-cluster=0
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
# Timer coalescing: unsupported kernels are skipped by ming-sysctl-apply.
kernel.timer_migration=1
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

    # 启用 zram 与低内存保护
    systemctl enable ming-memory-profile.service 2>/dev/null || true
    systemctl enable zramswap 2>/dev/null || true
    systemctl enable irqbalance 2>/dev/null || true

    mkdir -p /etc/default
    cat > /etc/default/earlyoom << EARLYOOMCFG
EARLYOOM_ARGS="-m 4 -s 8 -r 60 --avoid '^(Xorg|xfwm4|xfce4-session|lightdm|NetworkManager|pulseaudio|fcitx5|ming-phone-desktop|plank|picom|ming-update)$'"
EARLYOOMCFG

    cat > /usr/local/sbin/ming-oom-profile << 'MINGOOMPROFILE'
#!/usr/bin/env bash
set -u

log_file=/var/log/ming-oom-profile.log
mkdir -p "$(dirname "${log_file}")" 2>/dev/null || true
mkdir -p /run/ming-os 2>/dev/null || true
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"${log_file}" 2>/dev/null || true; }

if [[ -e /sys/fs/cgroup/cgroup.controllers ]] && command -v systemd-oomd >/dev/null 2>&1; then
    systemctl disable --now earlyoom.service 2>/dev/null || true
    systemctl enable --now systemd-oomd.service 2>/dev/null || log 'systemd-oomd unavailable at runtime'
    printf 'backend=systemd-oomd\n' >/run/ming-os/oom-profile 2>/dev/null || true
else
    systemctl disable --now systemd-oomd.service 2>/dev/null || true
    systemctl enable --now earlyoom.service 2>/dev/null || log 'earlyoom unavailable at runtime'
    printf 'backend=earlyoom\n' >/run/ming-os/oom-profile 2>/dev/null || true
fi
MINGOOMPROFILE
    [[ -s /usr/local/sbin/ming-oom-profile ]] || {
        echo "[ERROR] failed to generate OOM profile helper" >&2
        return 1
    }
    chmod 0755 /usr/local/sbin/ming-oom-profile
    cat > /etc/systemd/system/ming-oom-profile.service << 'MINGOOMPROFILESVC'
[Unit]
Description=Ming hardware-aware OOM backend selection
After=local-fs.target
Before=graphical.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ming-oom-profile
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MINGOOMPROFILESVC
    [[ -s /etc/systemd/system/ming-oom-profile.service ]] || {
        echo "[ERROR] failed to generate OOM profile unit" >&2
        return 1
    }
    systemctl enable ming-oom-profile.service 2>/dev/null || true

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
After=local-fs.target

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
USB_AUTOSUSPEND=0
# Old laptops often expose Wi-Fi/HID/audio through USB bridges whose autosuspend
# support is incomplete.  Keep autosuspend disabled globally and retain the
# supported exclusions as documentation/defense if a user enables it later.
USB_EXCLUDE_BTUSB=1
USB_EXCLUDE_AUDIO=1
USB_EXCLUDE_WWAN=1
USB_EXCLUDE_PRINTER=1
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

    # Network drivers are selected by kernel modalias/udev.  Keep the legacy
    # heredoc marker for resumed-build parsers, then remove the preview list so
    # it cannot steal a device binding in the final rootfs.
    cat > /etc/modules-load.d/ming-network.conf << 'STATICNETMOD'
# Ming OS: network drivers are selected by kernel modalias/udev.
STATICNETMOD
    rm -f /etc/modules-load.d/ming-network.conf

    cat > /etc/grub.d/09_ming_os << 'STATICGRUB'
#!/bin/sh
set -e

cat <<'EOF'
menuentry 'Ming OS' --class ming --class gnu-linux --class gnu --class os {
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog
    initrd /initrd.img
}
menuentry 'Ming OS (Safe Graphics)' --class ming --class gnu-linux --class gnu --class os {
    search --no-floppy --set=root --file /vmlinuz
    linux /vmlinuz root=UUID=__MING_ROOT_UUID__ ro quiet loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 nowatchdog ming.safe_graphics=1 nomodeset vga=791
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
EOF
STATICGRUB
    chmod 0755 /etc/grub.d/09_ming_os

    cat > /etc/lightdm/lightdm.conf.d/60-ming-autologin.conf << 'STATICLIGHTDM'
[Seat:*]
autologin-user=user
autologin-user-timeout=0
autologin-session=xfce
user-session=xfce
greeter-session=lightdm-gtk-greeter
allow-guest=false
STATICLIGHTDM
}

# ======================== 主流程 ========================

main() {
    echo "=====> [01_base] 开始基础系统配置 <====="

    configure_apt_sources
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
