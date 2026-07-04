#!/usr/bin/env bash
# ============================================================================
# Ming OS module 05: lightweight firewall baseline
# ============================================================================

set -uo pipefail

install_security_tools() {
    echo "Installing lightweight security baseline..."

    apt install -y --no-install-recommends \
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

remove_retired_security_manager() {
    echo "Removing retired Ming Security Manager preload..."
    rm -f /usr/local/bin/ming-master \
          /usr/local/bin/ming-master.py \
          /usr/share/applications/ming-master.desktop \
          "/home/${MING_USER}/Desktop/ming-master.desktop" \
          /etc/sudoers.d/user-master
    apt purge -y rkhunter chkrootkit lynis bleachbit yad 2>/dev/null || true
    apt autoremove -y 2>/dev/null || true
}

configure_ming_firewall() {
    echo "Deploying Ming firewall baseline..."
    install -m 0600 /tmp/ming-build/config/security/nftables.conf /etc/nftables.conf
    install -m 0644 /tmp/ming-build/config/security/ming-firewall.service /etc/systemd/system/ming-firewall.service
    systemctl enable ming-firewall 2>/dev/null || true
}

install_qq_linux() {
    echo "QQ Linux is available from the app store; skipping bundled install."
    mkdir -p "/home/${MING_USER}/Desktop"
}

install_listen1() {
    echo "Listen1 is available from the app store; skipping bundled install."
}

main() {
    echo "=====> [05_security_tools] Installing lightweight security baseline <====="
    install_security_tools
    remove_retired_security_manager
    configure_ming_firewall
    install_qq_linux
    install_listen1
    echo "=====> [05_security_tools] Security baseline installed <====="
}

main
