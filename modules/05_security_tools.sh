#!/usr/bin/env bash
# ============================================================================
# Ming OS module 05: security tools and Ming Security Manager
# ============================================================================

set -uo pipefail

install_security_tools() {
    echo "Installing security tools..."

    apt install -y --no-install-recommends \
        rkhunter \
        chkrootkit \
        lynis \
        bleachbit \
        yad \
        nftables

    grep -qxF "nf_tables" /etc/modules || echo "nf_tables" >> /etc/modules
    grep -qxF "nf_conntrack" /etc/modules || echo "nf_conntrack" >> /etc/modules
    grep -qxF "nft_ct" /etc/modules || echo "nft_ct" >> /etc/modules
    grep -qxF "nft_counter" /etc/modules || echo "nft_counter" >> /etc/modules

    mkdir -p /etc/modprobe.d
    cat > /etc/modprobe.d/ming-nftables.conf << 'MODPROBE'
softdep nf_tables pre: nf_conntrack
MODPROBE
}

deploy_ming_master() {
    echo "Deploying Ming Security Manager..."

    install -m 0755 /tmp/ming-build/config/security/ming-master.py /usr/local/bin/ming-master.py

    cat > /usr/local/bin/ming-master << 'MINGMASTERWRAPPER'
#!/usr/bin/env bash
set -u

LOG=/tmp/ming-master.log
: > "${LOG}"

show_error() {
    local text="$1"
    if command -v yad >/dev/null 2>&1; then
        yad --error --title="Ming 安全管家" --width=620 --text="${text}" 2>/dev/null || true
    elif command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Ming 安全管家" --width=620 --text="${text}" 2>/dev/null || true
    else
        printf '%s\n' "${text}" >&2
    fi
}

if ! command -v python3 >/dev/null 2>&1; then
    show_error "未找到 python3，无法启动安全管家。"
    exit 127
fi

python3 /usr/local/bin/ming-master.py "$@" >>"${LOG}" 2>&1
rc=$?
if [ "${rc}" -ne 0 ]; then
    summary="$(tail -n 40 "${LOG}" 2>/dev/null)"
    show_error "安全管家启动失败。\n\n${summary}\n\n完整日志：${LOG}"
fi
exit "${rc}"
MINGMASTERWRAPPER
    chmod 0755 /usr/local/bin/ming-master

    install -m 0600 /tmp/ming-build/config/security/nftables.conf /etc/nftables.conf
    install -m 0644 /tmp/ming-build/config/security/ming-firewall.service /etc/systemd/system/ming-firewall.service
    systemctl enable ming-firewall 2>/dev/null || true

    cat > /usr/share/applications/ming-master.desktop << 'MINGMASTERDESKTOP'
[Desktop Entry]
Name=Ming Manager
Name[zh_CN]=Ming 安全管家
Comment=Ming OS security toolkit
Comment[zh_CN]=系统安全扫描、清理与防火墙管理
Exec=/usr/local/bin/ming-master
Icon=ming-security
Terminal=false
Type=Application
Categories=System;Security;
Keywords=security;firewall;clean;scan;
StartupNotify=true
MINGMASTERDESKTOP

    mkdir -p "/home/${MING_USER}/Desktop"
    cp /usr/share/applications/ming-master.desktop "/home/${MING_USER}/Desktop/ming-master.desktop"
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/ming-master.desktop"
    chmod +x "/home/${MING_USER}/Desktop/ming-master.desktop"

    cat > /etc/sudoers.d/user-master << SUDOERS
${MING_USER} ALL=(ALL) NOPASSWD: /usr/bin/bleachbit, /usr/sbin/rkhunter, /usr/sbin/lynis, /usr/sbin/nft
SUDOERS
    chmod 0440 /etc/sudoers.d/user-master
}

install_qq_linux() {
    echo "QQ Linux is available from the app store; skipping bundled install."
    mkdir -p "/home/${MING_USER}/Desktop"
}

install_listen1() {
    echo "Listen1 is available from the app store; skipping bundled install."
}

main() {
    echo "=====> [05_security_tools] Installing security tools and Ming Security Manager <====="
    install_security_tools
    deploy_ming_master
    install_qq_linux
    install_listen1
    echo "=====> [05_security_tools] Security tools installed <====="
}

main
