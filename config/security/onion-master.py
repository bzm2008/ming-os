#!/usr/bin/env python3
# ============================================================================
# Onion OS 安全管家 - Onion Master
# ============================================================================
# 设计意图：
#   提供图形化安全工具，集成安全扫描、系统清理、防火墙管理等功能。
#   使用 yad/zenity 作为 GUI 前端，适合初学者使用。
# ============================================================================

import os
import sys
import shutil
import subprocess
import json
from pathlib import Path

# 常量定义
ONION_USER = "onion"
GARLIC_CLAW_PORT = 18789
CONFIG_DIR = Path.home() / ".config" / "onion-os"
CONFIG_FILE = CONFIG_DIR / "onion-master.json"


def check_dependencies():
    """检查必要的依赖是否安装"""
    deps = ["yad", "rkhunter", "chkrootkit", "lynis", "bleachbit", "nft"]
    missing = []
    for dep in deps:
        if not shutil.which(dep):
            missing.append(dep)
    return missing


def run_command(cmd, shell=True, check=False):
    """运行命令并返回结果"""
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


def show_info(title, text):
    """显示信息对话框"""
    run_command(f'yad --info --title="{title}" --text="{text}" --width=500')


def show_warning(title, text):
    """显示警告对话框"""
    run_command(f'yad --warning --title="{title}" --text="{text}" --width=500')


def show_error(title, text):
    """显示错误对话框"""
    run_command(f'yad --error --title="{title}" --text="{text}" --width=500')


def quick_clean():
    """快速清理系统垃圾"""
    cmd = "sudo bleachbit --clean system.cache system.trash system.tmp"
    code, out, err = run_command(cmd)
    if code == 0:
        show_info("清理完成", "系统缓存和垃圾文件已清理完成！")
    else:
        show_error("清理失败", f"清理过程中出现错误：\n{err}")


def security_check():
    """执行安全检查"""
    progress_cmd = (
        'yad --progress --title="安全检查" --text="正在执行安全扫描..." '
        '--percentage=0 --auto-close --width=500'
    )
    
    # 依次执行安全扫描
    results = []
    
    # rkhunter 扫描
    code, out, err = run_command("sudo rkhunter --check --skip-keypress --rwo")
    results.append(("Rootkit 检测 (rkhunter)", out if code == 0 else err))
    
    # lynis 审计
    code, out, err = run_command("sudo lynis audit system --quiet")
    results.append(("系统安全审计 (lynis)", out if code == 0 else err))
    
    # 显示结果
    result_text = "\n\n".join([f"=== {title} ===\n{content}" for title, content in results])
    
    # 将结果写入临时文件并显示
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(result_text)
        temp_file = f.name
    
    run_command(f'yad --text-info --title="安全检查结果" --filename="{temp_file}" --width=800 --height=600')
    os.unlink(temp_file)


def firewall_status():
    """显示防火墙状态"""
    code, out, err = run_command("sudo nft list ruleset")
    if code == 0:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(out)
            temp_file = f.name
        run_command(f'yad --text-info --title="防火墙规则" --filename="{temp_file}" --width=800 --height=600')
        os.unlink(temp_file)
    else:
        show_error("获取失败", f"无法获取防火墙状态：\n{err}")


def garlic_claw_status():
    """显示 Garlic Claw 状态"""
    status_info = []
    
    # 检查服务状态
    code, out, err = run_command(f'sudo -u {ONION_USER} systemctl --user is-active openclaw-gateway')
    service_status = "运行中 ✓" if code == 0 and out.strip() == "active" else "未运行 ✗"
    status_info.append(f"Gateway 服务状态: {service_status}")
    
    # 检查端口监听
    code, out, err = run_command(f'ss -tuln | grep :{GARLIC_CLAW_PORT}')
    port_status = f"端口 {GARLIC_CLAW_PORT} 正在监听" if code == 0 else f"端口 {GARLIC_CLAW_PORT} 未监听"
    status_info.append(f"端口状态: {port_status}")
    
    # 检查配置文件
    config_file = Path.home() / ".openclaw" / "config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                provider = config.get('provider', '未设置')
            status_info.append(f"AI 提供商: {provider}")
        except:
            status_info.append("AI 提供商: 配置文件读取失败")
    else:
        status_info.append("AI 提供商: 未配置")
    
    show_info("Garlic Claw 状态", "\n".join(status_info))


def main_menu():
    """主菜单"""
    menu_cmd = (
        'yad --list --title="Onion OS 安全管家" '
        '--text="请选择要执行的操作：" '
        '--column="操作" --column="说明" '
        '"快速清理" "清理系统缓存和垃圾文件" '
        '"安全检查" "执行 Rootkit 检测和系统安全审计" '
        '"防火墙状态" "查看当前防火墙规则" '
        '"Garlic Claw 状态" "查看 AI 助手服务运行状态" '
        '--width=600 --height=400 --button="退出:1" --button="执行:0"'
    )
    
    while True:
        code, out, err = run_command(menu_cmd)
        if code != 0:
            break
            
        selection = out.strip().split('|')[0] if out else ""
        
        if selection == "快速清理":
            quick_clean()
        elif selection == "安全检查":
            security_check()
        elif selection == "防火墙状态":
            firewall_status()
        elif selection == "Garlic Claw 状态":
            garlic_claw_status()


if __name__ == "__main__":
    import shutil
    
    # 检查依赖
    missing = check_dependencies()
    if missing:
        show_warning(
            "依赖缺失",
            f"以下工具未安装，部分功能可能无法使用：\n{', '.join(missing)}\n\n"
            "请运行：sudo apt install -y rkhunter chkrootkit lynis bleachbit yad nftables"
        )
    
    # 确保配置目录存在
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    # 启动主菜单
    main_menu()
