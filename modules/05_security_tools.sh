#!/usr/bin/env bash
# Ming OS module 05: security controls and secure account policy.

set -euo pipefail

install_security_runtime() {
    apt install -y --no-install-recommends nftables openssh-server unattended-upgrades
    install -d -m 0755 /etc/ming-security /etc/ssh/sshd_config.d
    install -d -m 0755 /usr/local/sbin /usr/share/polkit-1/actions

    install -m 0755 /tmp/ming-build/assets/ming-security-control.py \
        /usr/local/sbin/ming-security-control
    install -m 0755 /tmp/ming-build/assets/ming-account-control.py \
        /usr/local/sbin/ming-account-control
    python3 -m py_compile /usr/local/sbin/ming-security-control \
        /usr/local/sbin/ming-account-control
}

deploy_security_policy() {
    local candidate
    candidate="$(mktemp /etc/nftables.conf.ming.XXXXXX)"
    install -m 0600 /tmp/ming-build/config/security/nftables.conf "${candidate}"
    nft -c -f "${candidate}"
    mv -f "${candidate}" /etc/nftables.conf

    cat > /etc/ming-security/control.json << 'SECURITYSTATE'
{"firewall": true, "profile": "public", "security_updates": true, "ssh": false}
SECURITYSTATE
    chmod 0600 /etc/ming-security/control.json

    cat > /etc/apt/apt.conf.d/20auto-upgrades << 'APTPERIODIC'
APT::Periodic::Enable "1";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APTPERIODIC
    chmod 0644 /etc/apt/apt.conf.d/20auto-upgrades

    install -d -m 0755 /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/20-ming-security-defaults.conf << 'NMSECURITY'
[connection]
# New NetworkManager profiles start in the restrictive public zone.
connection.zone=public
NMSECURITY

    cat > /etc/ssh/sshd_config.d/60-ming-security.conf << 'SSHDSECURITY'
PermitRootLogin no
PermitEmptyPasswords no
PasswordAuthentication yes
SSHDSECURITY

    systemctl enable nftables.service
    systemctl disable ssh.service
    systemctl enable apt-daily.timer apt-daily-upgrade.timer
}

deploy_polkit_actions() {
    cat > /usr/share/polkit-1/actions/org.ming.security.control.policy << 'SECURITYPOLICY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Ming OS</vendor>
  <action id="org.ming.security.control">
    <description>Change Ming OS security settings</description>
    <message>Authentication is required to change security settings</message>
    <defaults><allow_any>no</allow_any><allow_inactive>no</allow_inactive><allow_active>yes</allow_active></defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/ming-security-control</annotate>
  </action>
</policyconfig>
SECURITYPOLICY

    cat > /usr/share/polkit-1/actions/org.ming.account.control.policy << 'ACCOUNTPOLICY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Ming OS</vendor>
  <action id="org.ming.account.control">
    <description>Change a Ming OS account password</description>
    <message>Authentication is required to change the account password</message>
    <defaults><allow_any>no</allow_any><allow_inactive>no</allow_inactive><allow_active>yes</allow_active></defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/ming-account-control</annotate>
  </action>
</policyconfig>
ACCOUNTPOLICY
}

retire_legacy_security_ui() {
    rm -f /usr/local/bin/ming-master /usr/local/bin/ming-master.py \
        /usr/share/applications/ming-master.desktop \
        "/home/${MING_USER}/Desktop/ming-master.desktop" \
        /etc/sudoers.d/user-master \
        /etc/systemd/system/ming-firewall.* \
        /etc/systemd/system/*.target.wants/ming-firewall.*
}

main() {
    echo "=====> [05_security_tools] Installing Ming security controls <====="
    install_security_runtime
    deploy_security_policy
    deploy_polkit_actions
    retire_legacy_security_ui
    echo "=====> [05_security_tools] Security controls installed <====="
}

main
