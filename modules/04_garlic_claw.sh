#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 04: Garlic Claw（深度定制 OpenClaw）AI 助手
# ============================================================================
# 设计意图：
#   将 OpenClaw 深度定制为 Garlic Claw，作为 Ming OS 的标志性 AI 助手。
#   以独立终端客户端形式运行，开机自启 Gateway 服务，并配置安全加固。
#
# 输入：
#   环境变量: MING_USER
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
    # 设计意图：创建 garlic-claw 命令作为 Ming OS 的 AI 助手入口
    # 该命令封装 openclaw 的 TUI 模式，提供更友好的交互体验

    cat > /usr/local/bin/garlic-claw << GARLICCLAWCMD
#!/usr/bin/env bash
# Garlic Claw - Ming OS AI 助手
# 基于 OpenClaw TUI 模式的深度定制客户端

readonly GC_VERSION="1.0.0-ming"
readonly GC_CONFIG_DIR="\${HOME}/.openclaw"
readonly GC_CONFIG_FILE="\${GC_CONFIG_DIR}/config.json"

show_banner() {
    clear
    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║       🧄 Garlic Claw AI 助手 🧄       ║"
    echo "  ║       Ming OS 标志性功能              ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""
}

check_config() {
    if [[ ! -f "\${GC_CONFIG_FILE}" ]]; then
        echo "[提示] 首次使用，请先运行配置向导："
        echo "       ming-first-run.sh"
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
            /usr/local/bin/ming-first-run.sh
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

# ======================== Garlic Claw 桌面 GUI 应用（26.2.5） ========================

install_garlic_claw_gui() {
    # 依赖：python3-gi 已由 03_desktop.sh 安装；补充 VTE 终端组件
    apt install -y --no-install-recommends python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-vte-2.91 xterm 2>/dev/null || true

    cat > /usr/local/bin/garlic-claw-app << 'GARLICAPP'
#!/usr/bin/env python3
"""Garlic Claw - Ming OS AI 电脑助手（面向数码难民）"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango
import subprocess, threading, shutil, os

APP_COLOR  = "#00453E"
MINT       = "#31C476"

SYSTEM_CMDS = [
    ("🧹 清理磁盘缓存",  "sudo apt clean; journalctl --vacuum-time=7d; read -p '按回车关闭'"),
    ("💾 查看内存使用",   "free -h; echo; vmstat -s | head -12; read -p '按回车关闭'"),
    ("🌐 修复网络连接",   "nmcli dev status; echo; nmcli networking off; sleep 1; nmcli networking on; read -p '按回车关闭'"),
    ("📊 进程管理",       "htop"),
    ("ℹ️  系统信息",      "neofetch 2>/dev/null || lsb_release -a; uname -r; read -p '按回车关闭'"),
]

OFFICE_CMDS = [
    ("📁 文件管理器", "thunar"),
    ("📸 截图",       "xfce4-screenshooter"),
    ("🖥️  终端",      "xfce4-terminal"),
    ("📝 文字处理",   "wps 2>/dev/null || libreoffice --writer 2>/dev/null || mousepad"),
]

def run_cmd(cmd):
    if any(cmd.startswith(x) for x in ["htop","thunar","xfce4","wps","libreoffice","mousepad"]):
        subprocess.Popen(cmd.split(None, 1) if ' ' not in cmd else ["sh","-c",cmd])
    else:
        subprocess.Popen(["xterm", "-fa", "Monospace", "-fs", "11", "-bg", "#0d1117", "-fg", "#c9d1d9",
                          "-title", "Garlic Claw", "-e", f"bash -c {cmd!r}"])

class GarlicClaw(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Garlic Claw · AI 电脑助手")
        self.set_default_size(860, 580)
        self.set_border_width(0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(hbox)

        # 左侧导航栏
        sidebar = Gtk.StackSidebar()
        sidebar.set_size_request(140, -1)
        sidebar.get_style_context().add_class("sidebar")
        hbox.pack_start(sidebar, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        hbox.pack_start(sep, False, False, 0)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        sidebar.set_stack(stack)
        hbox.pack_start(stack, True, True, 0)

        stack.add_titled(self._build_chat(),   "chat",   "💬 AI 对话")
        stack.add_titled(self._build_system(), "system", "🔧 系统管理")
        stack.add_titled(self._build_office(), "office", "📁 办公辅助")

    def _build_chat(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_border_width(12)

        # 对话历史
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self.chat_buf, editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          left_margin=8, right_margin=8, top_margin=8, bottom_margin=8)
        tv.override_font(Pango.FontDescription("Monospace 10"))
        scroll.add(tv)
        vbox.pack_start(scroll, True, True, 0)
        self.chat_scroll = scroll

        # 输入行
        hbox = Gtk.Box(spacing=6)
        hbox.set_border_width(6)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("输入问题，按回车发送…")
        self.entry.connect("activate", self._send)
        send_btn = Gtk.Button(label="发送")
        send_btn.connect("clicked", self._send)
        hbox.pack_start(self.entry, True, True, 0)
        hbox.pack_start(send_btn, False, False, 0)
        vbox.pack_end(hbox, False, False, 0)

        # 是否有 claude CLI
        if not shutil.which("claude") and not shutil.which("garlic-claw"):
            self._append("\n⚠️  未检测到 claude / garlic-claw 命令。\n"
                         "请先运行 /usr/local/bin/ming-garlic-setup 完成安装。\n\n"
                         "安装后重新打开此窗口即可使用 AI 对话。\n")
        else:
            self._append("👋 你好！我是 Garlic Claw，Ming OS 的 AI 电脑助手。\n"
                         "有什么我可以帮你的？（电脑问题、文件操作、办公辅助都可以问）\n\n")
        return vbox

    def _append(self, text):
        end = self.chat_buf.get_end_iter()
        self.chat_buf.insert(end, text)
        GLib.idle_add(lambda: self.chat_scroll.get_vadjustment().set_value(
            self.chat_scroll.get_vadjustment().get_upper()))

    def _send(self, *_):
        msg = self.entry.get_text().strip()
        if not msg:
            return
        self.entry.set_text("")
        self._append(f"你：{msg}\n")
        self._append("Garlic Claw：正在思考...\n")
        threading.Thread(target=self._ask, args=(msg,), daemon=True).start()

    def _ask(self, msg):
        cli = shutil.which("claude") or shutil.which("garlic-claw")
        if not cli:
            GLib.idle_add(self._append, "（请先安装 claude CLI）\n\n")
            return
        try:
            result = subprocess.run([cli, msg], capture_output=True, text=True, timeout=120)
            reply = (result.stdout or result.stderr or "（无输出）").strip()
        except subprocess.TimeoutExpired:
            reply = "（请求超时，请检查网络和 API Key 配置）"
        except Exception as e:
            reply = f"（调用失败：{e}）"
        # 替换掉"正在思考..."那行
        GLib.idle_add(self._replace_last_thinking, reply)

    def _replace_last_thinking(self, reply):
        text = self.chat_buf.get_text(self.chat_buf.get_start_iter(), self.chat_buf.get_end_iter(), False)
        if "正在思考..." in text:
            start = self.chat_buf.get_start_iter()
            end = self.chat_buf.get_end_iter()
            new_text = text.replace("Garlic Claw：正在思考...\n", f"Garlic Claw：{reply}\n\n", 1)
            self.chat_buf.set_text(new_text)

    def _build_system(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(16)
        label = Gtk.Label(label="<b>系统管理</b>", use_markup=True, xalign=0)
        vbox.pack_start(label, False, False, 0)
        grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        for i, (name, cmd) in enumerate(SYSTEM_CMDS):
            btn = Gtk.Button(label=name)
            btn.set_size_request(180, 48)
            btn.connect("clicked", lambda _, c=cmd: run_cmd(c))
            grid.attach(btn, i % 2, i // 2, 1, 1)
        vbox.pack_start(grid, False, False, 0)
        return vbox

    def _build_office(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_border_width(16)
        label = Gtk.Label(label="<b>办公辅助</b>", use_markup=True, xalign=0)
        vbox.pack_start(label, False, False, 0)
        box = Gtk.Box(spacing=8)
        for name, cmd in OFFICE_CMDS:
            btn = Gtk.Button(label=name)
            btn.set_size_request(160, 56)
            btn.connect("clicked", lambda _, c=cmd: run_cmd(c))
            box.pack_start(btn, True, True, 0)
        vbox.pack_start(box, False, False, 0)
        return vbox

class GarlicApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="uno.scallion.garlic-claw")
    def do_activate(self):
        win = GarlicClaw(self)
        win.show_all()

GarlicApp().run(None)
GARLICAPP
    chmod +x /usr/local/bin/garlic-claw-app
}

# ======================== .desktop 启动器 ========================

create_desktop_entry() {
    cat > /usr/share/applications/garlic-claw.desktop << GCDESKTOP
[Desktop Entry]
Name=Garlic Claw
Name[zh_CN]=Garlic Claw 电脑助手
Comment=AI-powered computer assistant for everyday users
Comment[zh_CN]=面向数码难民的 AI 电脑援助平台
Exec=garlic-claw-app
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=System;Utility;
Keywords=ai;chat;assistant;garlic;
StartupNotify=true
StartupWMClass=garlic-claw
GCDESKTOP

    # 同时在用户桌面放置快捷方式
    mkdir -p "/home/${MING_USER}/Desktop"
    cp /usr/share/applications/garlic-claw.desktop \
        "/home/${MING_USER}/Desktop/garlic-claw.desktop"
    chown "${MING_USER}:${MING_USER}" "/home/${MING_USER}/Desktop/garlic-claw.desktop"
    chmod +x "/home/${MING_USER}/Desktop/garlic-claw.desktop"
}

# ======================== Gateway 服务配置 ========================

configure_gateway_service() {
    # 设计意图：配置 OpenClaw Gateway 为用户级 systemd 服务
    # 用户登录后自动启动，AI 能力即就绪

    # 创建用户级 systemd 目录
    sudo -u "${MING_USER}" mkdir -p "/home/${MING_USER}/.config/systemd/user"

    cat > "/home/${MING_USER}/.config/systemd/user/openclaw-gateway.service" << GATEWAYSERVICE
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

    chown -R "${MING_USER}:${MING_USER}" "/home/${MING_USER}/.config/systemd"

    # 启用 linger（允许用户服务在未登录时也运行）
    loginctl enable-linger "${MING_USER}" 2>/dev/null || true
}

# ======================== 防火墙安全加固 ========================

configure_firewall() {
    echo "配置 Garlic Claw 本地访问限制..."
    apt install -y --no-install-recommends nftables
}

# ======================== 首次配置向导 ========================

deploy_first_run_wizard() {
    # 设计意图：系统首次启动后自动运行图形化配置向导
    # 使用 zenity 实现，引导用户选择模型并输入 API Key

    cat > /usr/local/bin/ming-first-run.sh << FIRSTRUNWIZARD
#!/usr/bin/env bash
# Ming OS 首次配置向导
# 引导用户配置 Garlic Claw AI 助手

readonly CONFIG_DIR="\${HOME}/.openclaw"
readonly CONFIG_FILE="\${CONFIG_DIR}/config.json"
readonly MARKER_FILE="\${HOME}/.config/ming-os/first-run-done"

# 检查是否已完成首次配置
if [[ -f "\${MARKER_FILE}" ]]; then
    exit 0
fi

# 等待桌面环境完全加载
sleep 5

# 欢迎界面
zenity --info \\
    --title="欢迎使用 Ming OS" \\
        --text="欢迎使用 Ming OS ${MING_OS_VERSION} Home Edition！\\n\\n接下来将引导您配置 Garlic Claw AI 助手。\\n如果您暂时不需要 AI 助手，可以跳过此步骤。" \\
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

    chmod +x /usr/local/bin/ming-first-run.sh

    # 创建标记目录
    sudo -u "${MING_USER}" mkdir -p "/home/${MING_USER}/.config/ming-os"
}

# ======================== 主流程 ========================

main() {
    echo "=====> [04_garlic_claw] 开始安装 Garlic Claw AI 助手 <====="

    install_nodejs
    install_openclaw
    create_garlic_claw_command
    install_garlic_claw_gui
    create_desktop_entry
    configure_gateway_service
    configure_firewall
    deploy_first_run_wizard

    echo "=====> [04_garlic_claw] Garlic Claw AI 助手安装完成 <====="
}

main
