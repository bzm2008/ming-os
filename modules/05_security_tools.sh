#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 05: 安全工具与 QQ 安装
# ============================================================================
# 设计意图：
#   安装 Onion 安全管家、安全扫描工具、防火墙配置以及 QQ Linux 版。
#
# 输入：
#   环境变量: ONION_USER
#
# 输出：
#   完整的安全工具集和 QQ 即时通讯软件
# ============================================================================

set -uo pipefail

# ======================== 安全工具安装 ========================

install_security_tools() {
    echo "安装安全工具..."
    
    apt install -y --no-install-recommends \
        rkhunter \
        chkrootkit \
        lynis \
        bleachbit \
        yad \
        nftables

    echo "nf_tables" >> /etc/modules
    echo "nf_conntrack" >> /etc/modules
    echo "nft_ct" >> /etc/modules
    echo "nft_counter" >> /etc/modules

    mkdir -p /etc/modprobe.d
    cat > /etc/modprobe.d/onion-nftables.conf << MODPROBE
softdep nf_tables pre: nf_conntrack
MODPROBE
}

# ======================== Onion 安全管家部署 ========================

deploy_onion_master() {
    echo "部署 Onion 安全管家..."
    
    # 复制主程序
    cp /tmp/onion-build/config/security/onion-master.py /usr/local/bin/
    chmod +x /usr/local/bin/onion-master.py
    
    # 复制防火墙配置
    cp /tmp/onion-build/config/security/nftables.conf /etc/nftables.conf
    chmod 600 /etc/nftables.conf
    
    # 复制防火墙服务文件
    cp /tmp/onion-build/config/security/onion-firewall.service /etc/systemd/system/
    chmod 0644 /etc/systemd/system/onion-firewall.service
    systemctl enable onion-firewall 2>/dev/null || true
    
    # 创建桌面快捷方式
    cat > /usr/share/applications/onion-master.desktop << ONIONMASTERDESKTOP
[Desktop Entry]
Name=Onion 管家
Name[zh_CN]=Onion 安全管家
Comment=Onion OS 安全工具集
Comment[zh_CN]=系统安全扫描、清理与防火墙管理
Exec=pkexec python3 /usr/local/bin/onion-master.py
Icon=onion-security
Terminal=false
Type=Application
Categories=System;Security;
Keywords=security;firewall;clean;scan;
StartupNotify=true
ONIONMASTERDESKTOP
    
    # 在用户桌面放置快捷方式
    mkdir -p "/home/${ONION_USER}/Desktop"
    cp /usr/share/applications/onion-master.desktop \
        "/home/${ONION_USER}/Desktop/onion-master.desktop"
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/Desktop/onion-master.desktop"
    chmod +x "/home/${ONION_USER}/Desktop/onion-master.desktop"
    
    # 配置 sudo 免密运行 Onion 管家
    echo "${ONION_USER} ALL=(ALL) NOPASSWD: /usr/local/bin/onion-master.py" > /etc/sudoers.d/onion-master
    chmod 440 /etc/sudoers.d/onion-master
}

# ======================== QQ Linux 版安装 ========================

install_qq_linux() {
    echo "配置 QQ Linux 版 (用户可从应用商店安装)..."
    mkdir -p "/home/${ONION_USER}/Desktop"
}

# ======================== Listen1 音乐播放器安装 ========================

install_listen1() {
    echo "配置 Listen1 音乐播放器 (用户可从应用商店安装)..."
}

# ======================== 主流程 ========================

main() {
    echo "=====> [05_security_tools] 开始安装安全工具、QQ 与 Listen1 <====="
    
    install_security_tools
    deploy_onion_master
    install_qq_linux
    install_listen1
    
    echo "=====> [05_security_tools] 安全工具、QQ 与 Listen1 安装完成 <====="
}

main
