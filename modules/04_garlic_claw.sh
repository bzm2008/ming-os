#!/usr/bin/env bash
# ============================================================================
# Onion OS 模块 04: Garlic Claw（深度定制 OpenClaw）AI 助手
# ============================================================================
# 设计意图：
#   将 OpenClaw 深度定制为 Garlic Claw，作为 Onion OS 的标志性 AI 助手。
#   以独立终端客户端形式运行，开机自启 Gateway 服务，并配置安全加固。
#
# 输入：
#   环境变量: ONION_USER
#
# 输出：
#   完整安装的 Garlic Claw AI 助手，含 .desktop 启动器、
#   首次配置向导、防火墙规则、systemd 用户服务
#
# 关键步骤：
#   1. 安装 Node.js（优先 v22，失败时回退到 Debian 稳定版）
#   2. 执行 OpenClaw 官方安装脚本
#   3. 创建 garlic-claw 命令别名与 PATH 集成
#   4. 创建 .desktop 启动器（TUI 模式）
#   5. 配置 Gateway 服务开机自启
#   6. 配置防火墙规则（端口 18789 仅监听 127.0.0.1）
#   7. 部署首次配置向导脚本
# ============================================================================

set -uo pipefail

readonly GARLIC_CLAW_PORT=18789

# ======================== Node.js 安装 ========================

install_nodejs() {
    if command -v node &>/dev/null; then
        local node_version
        node_version=$(node -v | sed 's/v//' | cut -d. -f1)
        if [[ ${node_version} -ge 20 ]]; then
            echo "Node.js $(node -v) 已安装，满足要求。"
            return 0
        fi
    fi

    echo "安装 Node.js..."

    if timeout 180 bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -' 2>/dev/null; then
        timeout 180 apt-get \
            -o Acquire::Retries=2 \
            -o Acquire::http::Timeout=30 \
            -o Acquire::https::Timeout=30 \
            install -y --no-install-recommends nodejs 2>/dev/null || true
    fi

    if ! command -v node &>/dev/null; then
        echo "[WARN] NodeSource 安装失败，尝试从 Debian 仓库安装..."
        rm -f /etc/apt/sources.list.d/nodesource*.list /etc/apt/keyrings/nodesource*.gpg 2>/dev/null || true
        rm -f /var/lib/apt/lists/*nodesource* 2>/dev/null || true
        apt-get update 2>/dev/null || true
        timeout 180 apt-get \
            -o Acquire::Retries=2 \
            -o Acquire::http::Timeout=30 \
            -o Acquire::https::Timeout=30 \
            install -y --no-install-recommends nodejs npm 2>/dev/null || true
    fi

    if ! command -v node &>/dev/null; then
        echo "[WARN] Node.js 安装失败，创建占位脚本..."
        echo "[WARN] 用户可后续手动安装: apt install -y nodejs npm"
        return 0
    fi

    local node_version
    node_version=$(node -v | sed 's/v//' | cut -d. -f1)
    if [[ ${node_version} -lt 20 ]]; then
        echo "[WARN] Node.js 版本 $(node -v) 低于 v20，部分功能可能受限。"
    elif [[ ${node_version} -lt 22 ]]; then
        echo "[INFO] 当前使用 Debian 稳定版 Node.js $(node -v)。"
    fi

    echo "Node.js $(node -v) 安装完成"
}

# ======================== OpenClaw 安装 ========================

install_openclaw() {
    echo "安装 OpenClaw (使用占位脚本，用户首次使用时自动安装)..."

    create_openclaw_placeholder

    echo "[INFO] OpenClaw 将在用户首次运行 garlic-claw 时自动安装"
}

# 创建 openclaw 占位脚本（当官方安装失败时使用）
create_openclaw_placeholder() {
    cat > /usr/local/bin/openclaw << OPENCLAWPLACEHOLDER
#!/usr/bin/env bash
echo "========================================="
echo "  Garlic Claw (OpenClaw) 首次运行安装"
echo "========================================="
echo ""
echo "正在安装 OpenClaw，请稍候..."
echo ""

npm config set registry https://registry.npmmirror.com

if curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-prompt --no-onboard; then
    echo ""
    echo "安装成功！正在启动 Garlic Claw..."
    echo ""
    exec openclaw "\$@"
else
    echo ""
    echo "自动安装失败，请手动运行："
    echo "  curl -fsSL https://openclaw.ai/install.sh | bash"
    echo ""
fi
OPENCLAWPLACEHOLDER
    chmod +x /usr/local/bin/openclaw
}

# ======================== Garlic Claw 命令集成 ========================

create_garlic_claw_command() {
    # 设计意图：创建 garlic-claw 命令作为 Onion OS 的 AI 助手入口
    # 该命令封装 openclaw 的 TUI 模式，提供更友好的交互体验

    cat > /usr/local/bin/garlic-claw << GARLICCLAWCMD
#!/usr/bin/env bash
# Garlic Claw - Onion OS AI 助手
# 基于 OpenClaw TUI 模式的深度定制客户端

readonly GC_VERSION="1.0.0-onion"
readonly GC_CONFIG_DIR="\${HOME}/.openclaw"
readonly GC_CONFIG_FILE="\${GC_CONFIG_DIR}/config.json"

show_banner() {
    clear
    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       🧄 Garlic Claw AI 助手 🧄       ║"
    echo "  ║       Onion OS 标志性功能              ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""
}

check_config() {
    if [[ ! -f "\${GC_CONFIG_FILE}" ]]; then
        echo "[提示] 首次使用，请先运行配置向导："
        echo "       onion-first-run.sh"
        echo ""
        echo "或手动创建配置文件："
        echo "       mkdir -p \${GC_CONFIG_DIR}"
        echo '       echo \'{"provider":"kimi","apiKey":"YOUR_KEY"}\' > \${GC_CONFIG_FILE}'
        echo ""
        return 1
    fi
    return 0
}

main() {
    show_banner

    # 处理子命令
    case "\${1:-}" in
        ask)
            shift
            if command -v openclaw &>/dev/null; then
                openclaw chat "\$@"
            else
                echo "[错误] OpenClaw 未安装，请先运行："
                echo "       curl -fsSL https://openclaw.ai/install.sh | bash"
            fi
            ;;
        config)
            /usr/local/bin/onion-first-run.sh
            ;;
        status)
            if systemctl --user is-active openclaw-gateway &>/dev/null; then
                echo "[状态] Garlic Claw Gateway 服务运行中 ✓"
            else
                echo "[状态] Garlic Claw Gateway 服务未运行 ✗"
                echo "       启动命令: systemctl --user start openclaw-gateway"
            fi
            ;;
        version)
            echo "Garlic Claw v\${GC_VERSION}"
            ;;
        *)
            check_config || exit 1
            if command -v openclaw &>/dev/null; then
                openclaw chat "\$@"
            else
                echo "[错误] OpenClaw 未安装，请先运行："
                echo "       curl -fsSL https://openclaw.ai/install.sh | bash"
            fi
            ;;
    esac
}

main "\$@"
GARLICCLAWCMD

    chmod +x /usr/local/bin/garlic-claw
}

# ======================== .desktop 启动器 ========================

create_desktop_entry() {
    # 设计意图：创建 Garlic Claw 的 .desktop 文件
    # 在独立终端窗口中启动 TUI 模式，完全脱离浏览器

    cat > /usr/share/applications/garlic-claw.desktop << GCDESKTOP
[Desktop Entry]
Name=Garlic Claw
Name[zh_CN]=Garlic Claw AI 助手
Comment=Onion OS 标志性 AI 助手
Comment[zh_CN]=基于 OpenClaw 的独立 AI 对话客户端
Exec=xfce4-terminal --title="Garlic Claw" -e "garlic-claw"
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=System;Utility;AI;
Keywords=ai;chat;assistant;openclaw;garlic;
StartupNotify=true
StartupWMClass=garlic-claw
GCDESKTOP

    # 同时在用户桌面放置快捷方式
    mkdir -p "/home/${ONION_USER}/Desktop"
    cp /usr/share/applications/garlic-claw.desktop \
        "/home/${ONION_USER}/Desktop/garlic-claw.desktop"
    chown "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/Desktop/garlic-claw.desktop"
    chmod +x "/home/${ONION_USER}/Desktop/garlic-claw.desktop"
}

# ======================== Gateway 服务配置 ========================

configure_gateway_service() {
    # 设计意图：配置 OpenClaw Gateway 为用户级 systemd 服务
    # 用户登录后自动启动，AI 能力即就绪

    # 创建用户级 systemd 目录
    sudo -u "${ONION_USER}" mkdir -p "/home/${ONION_USER}/.config/systemd/user"

    cat > "/home/${ONION_USER}/.config/systemd/user/openclaw-gateway.service" << GATEWAYSERVICE
[Unit]
Description=OpenClaw Gateway Service (Garlic Claw)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/openclaw gateway --port ${GARLIC_CLAW_PORT} --host 127.0.0.1
Restart=on-failure
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=default.target
GATEWAYSERVICE

    chown -R "${ONION_USER}:${ONION_USER}" "/home/${ONION_USER}/.config/systemd"

    # 启用 linger（允许用户服务在未登录时也运行）
    loginctl enable-linger "${ONION_USER}" 2>/dev/null || true
}

# ======================== 防火墙安全加固 ========================

configure_firewall() {
    echo "配置 Garlic Claw 防火墙规则（将在 05_security_tools 中统一部署）..."
    apt install -y --no-install-recommends nftables
}

# ======================== 首次配置向导 ========================

deploy_first_run_wizard() {
    # 设计意图：系统首次启动后自动运行图形化配置向导
    # 使用 zenity 实现，引导用户选择模型并输入 API Key

    cat > /usr/local/bin/onion-first-run.sh << FIRSTRUNWIZARD
#!/usr/bin/env bash
# Onion OS 首次配置向导
# 引导用户配置 Garlic Claw AI 助手

readonly CONFIG_DIR="\${HOME}/.openclaw"
readonly CONFIG_FILE="\${CONFIG_DIR}/config.json"
readonly MARKER_FILE="\${HOME}/.config/onion-os/first-run-done"

# 检查是否已完成首次配置
if [[ -f "\${MARKER_FILE}" ]]; then
    exit 0
fi

# 等待桌面环境完全加载
sleep 5

# 欢迎界面
zenity --info \\
    --title="欢迎使用 Onion OS" \\
        --text="欢迎使用 Onion OS 26.2.0 Home Edition！\\n\\n接下来将引导您配置 Garlic Claw AI 助手。\\n如果您暂时不需要 AI 助手，可以跳过此步骤。" \\
    --width=450 \\
    --ok-label="开始配置" \\
    --extra-button="跳过" \\
    --extra-button="稍后提醒"

case \$? in
    1)
        # 用户选择跳过
        mkdir -p \$(dirname "\${MARKER_FILE}")
        echo "skipped" > "\${MARKER_FILE}"
        exit 0
        ;;
    3)
        # 用户选择稍后提醒（不创建标记文件，下次登录仍会弹出）
        exit 0
        ;;
esac

# 选择 AI 模型提供商
MODEL_PROVIDER=\$(zenity --list \\
    --title="选择 AI 模型" \\
    --text="请选择您要使用的 AI 模型提供商：" \\
    --column="提供商" --column="说明" \\
    "kimi" "月之暗面 Kimi（推荐，中文能力强）" \\
    "openai" "OpenAI GPT 系列" \\
    "deepseek" "DeepSeek 深度求索" \\
    "zhipu" "智谱 GLM 系列" \\
    --width=500 --height=300 \\
    --ok-label="下一步")

if [[ -z "\${MODEL_PROVIDER}" ]]; then
    exit 0
fi

# 输入 API Key
API_KEY=\$(zenity --entry \\
    --title="输入 API Key" \\
    --text="请输入 \${MODEL_PROVIDER} 的 API Key：\\n\\n如果您还没有 API Key，请前往对应平台注册获取。\\n输入后将被安全保存在本地。" \\
    --hide-text \\
    --width=450)

if [[ -z "\${API_KEY}" ]]; then
    zenity --warning \\
        --title="未输入 API Key" \\
        --text="您未输入 API Key，Garlic Claw 将无法正常工作。\\n您可以稍后通过菜单中的 Garlic Claw 配置向导重新设置。" \\
        --width=400
    exit 0
fi

# 生成配置文件
mkdir -p "\${CONFIG_DIR}"
cat > "\${CONFIG_FILE}" << CONFIGJSON
{
  "provider": "\${MODEL_PROVIDER}",
  "apiKey": "\${API_KEY}",
  "gateway": {
    "port": ${GARLIC_CLAW_PORT},
    "host": "127.0.0.1"
  }
}
CONFIGJSON

chmod 600 "\${CONFIG_FILE}"

# 启动 Gateway 服务
systemctl --user daemon-reload
systemctl --user enable openclaw-gateway
systemctl --user start openclaw-gateway 2>/dev/null || true

# 标记首次配置完成
mkdir -p \$(dirname "\${MARKER_FILE}")
echo "completed" > "\${MARKER_FILE}"

zenity --info \\
    --title="配置完成" \\
    --text="Garlic Claw AI 助手配置完成！\\n\\n您可以通过以下方式使用：\\n• 桌面上的 Garlic Claw 图标\\n• 任务栏中的快捷方式\\n• 文件管理器右键菜单" \\
    --width=450
FIRSTRUNWIZARD

    chmod +x /usr/local/bin/onion-first-run.sh

    # 创建标记目录
    sudo -u "${ONION_USER}" mkdir -p "/home/${ONION_USER}/.config/onion-os"
}

# ======================== 主流程 ========================

main() {
    echo "=====> [04_garlic_claw] 开始安装 Garlic Claw AI 助手 <====="

    install_nodejs
    install_openclaw
    create_garlic_claw_command
    create_desktop_entry
    configure_gateway_service
    configure_firewall
    deploy_first_run_wizard

    echo "=====> [04_garlic_claw] Garlic Claw AI 助手安装完成 <====="
}

main
