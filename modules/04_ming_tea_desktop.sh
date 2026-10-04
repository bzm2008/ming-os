#!/usr/bin/env bash
# ============================================================================
# Ming OS 模块 04: 铭荼桌面应用（Tauri + DSH runtime）—— Debian 13 安装层
# ============================================================================
# 设计意图：
#   铭荼有两个形态：
#     a) GTK 原生壳（模块 03 的 install_ming_tea，assets/ming-tea.py）：轻量、
#        无外部依赖，作为「助手」入口占位；
#     b) 本模块安装的 **DSH 桌面应用**（Tauri 壳 + DSH runtime + 社区插件）：
#        完整的语音助手面板能力（见 docs/ming-tea-voice-summon.md）。
#
# 构建产物来源（**不在 ISO 构建机/ chroot 里编译**，原因见下）：
#   /tmp/ming-build/assets/ming-tea-desktop/
#     ├── ming-tea-desktop_amd64.deb        # Tauri Linux 包（含主程序 + 资源）
#     ├── ming-tea-hotkey                   # 全局热键守护进程（独立二进制）
#     ├── ming-tea-runtime.tar.zst          # DSH runtime（node_modules 树 + dsh CLI）
#     ├── ming-tea-dsh-lock.json            # 插件锁（与 runtime 一一对应）
#     ├── ming-tea-hotkey.desktop           # 守护进程的自启动入口
#     └── receipt.json                      # sha256 清单（安装前全部校验）
#
#   为什么 chroot 里不编译：Tauri/WebKitGTK 构建需要 ~2GB 工具链与 20+ 分钟，
#   且 ISO 构建要求确定性（build-input 哈希会随 rustc/pnpm 版本漂移）。产物在
#   有 Rust 工具链的宿主机上由 scripts/prepare_ming_tea_desktop.sh 产出，
#   通过 receipt.json 的 sha256 锁进 build-input（build_onion_os.sh 会校验）。
#
# 输入：/tmp/ming-build/assets/ming-tea-desktop/**（receipt.json 覆盖全部文件）
# 输出：/opt/ming-tea/**（应用与 runtime）+ ming-tea-dsh.desktop 桌面入口
#       + /etc/xdg/autostart/ming-tea-hotkey.desktop（热键守护进程自启动）
# 失败语义：任何一步失败都 return 1，让整个 ISO 构建失败（fail-closed）。
# ============================================================================

set -uo pipefail

readonly MTD_ASSET_DIR="/tmp/ming-build/assets/ming-tea-desktop"
readonly MTD_OPT_DIR="/opt/ming-tea"
readonly MTD_BIN="/usr/local/bin/ming-tea-dsh"

mtd_log() { echo "[04_ming_tea_desktop] $*"; }
mtd_fail() { echo "[04_ming_tea_desktop] ERROR: $*" >&2; return 1; }

# ── 1. 产物完整性：receipt.json 覆盖的每个文件都要 sha256 匹配 ────────────────
verify_receipt() {
    local receipt="${MTD_ASSET_DIR}/receipt.json"
    [[ -s "${receipt}" ]] || mtd_fail "missing receipt.json in ${MTD_ASSET_DIR}"

    python3 - "${receipt}" "${MTD_ASSET_DIR}" <<'PY'
import hashlib
import json
import pathlib
import sys

receipt_path, asset_dir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
files = receipt.get("files") or {}
if not files:
    raise SystemExit("receipt.json lists no files")

for name, expected in sorted(files.items()):
    target = asset_dir / name
    if not target.is_file():
        raise SystemExit(f"missing build input: {name}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"sha256 mismatch for {name}: {actual} != {expected}")
print(f"[04_ming_tea_desktop] receipt verified: {len(files)} files")
PY
    return "${?}"
}

# ── 2. Debian 13 运行依赖（WebKitGTK 是 Tauri 在 Linux 的硬依赖）─────────────
install_runtime_dependencies() {
    apt-get update -qq || return 1
    apt-get install -y --no-install-recommends \
        libwebkit2gtk-4.1-0 \
        libgtk-3-0 \
        libayatana-appindicator3-1 \
        librsvg2-2 \
        libxdo3 \
        libssl3 \
        xdg-utils \
        desktop-file-utils || return 1
    return 0
}

# ── 3. 安装 .deb（先 dpkg 校验容器与数据档，照 xiahai 的口径）────────────────
install_application_package() {
    local deb="${MTD_ASSET_DIR}/ming-tea-desktop_amd64.deb"
    [[ -s "${deb}" ]] || mtd_fail "missing ${deb}"

    dpkg-deb --info "${deb}" >/dev/null 2>&1 || mtd_fail "corrupt .deb container"
    dpkg-deb --contents "${deb}" >/dev/null 2>&1 || mtd_fail "corrupt .deb data archive"

    dpkg --unpack "${deb}" >/tmp/ming-tea-desktop-dpkg.log 2>&1 || mtd_fail "dpkg unpack failed; see /tmp/ming-tea-desktop-dpkg.log"
    apt-get -y -f install >>/tmp/ming-tea-desktop-dpkg.log 2>&1 || true
    local status
    status="$(dpkg-query -W -f='${Status}' cn.mingos.mingtea 2>/dev/null || true)"
    if [[ "${status}" != "install ok installed" ]]; then
        dpkg --configure cn.mingos.mingtea >>/tmp/ming-tea-desktop-dpkg.log 2>&1 || true
        status="$(dpkg-query -W -f='${Status}' cn.mingos.mingtea 2>/dev/null || true)"
    fi
    [[ "${status}" == "install ok installed" ]] || mtd_fail "package did not reach install ok installed"
    return 0
}

# ── 4. DSH runtime 摆放（/opt/ming-tea/runtime，随发行版本一起走）────────────
install_dsh_runtime() {
    # 产物优先 zstd（.tar.zst），构建宿主机没有 zstd 时是 .tar.gz —— 两种都要认。
    local tarball
    if [[ -s "${MTD_ASSET_DIR}/ming-tea-runtime.tar.zst" ]]; then
        tarball="${MTD_ASSET_DIR}/ming-tea-runtime.tar.zst"
        if ! command -v zstd >/dev/null 2>&1; then
            apt-get install -y --no-install-recommends zstd || return 1
        fi
    elif [[ -s "${MTD_ASSET_DIR}/ming-tea-runtime.tar.gz" ]]; then
        tarball="${MTD_ASSET_DIR}/ming-tea-runtime.tar.gz"
    else
        mtd_fail "missing DSH runtime tarball (ming-tea-runtime.tar.zst/.tar.gz)"
    fi

    mkdir -p "${MTD_OPT_DIR}/runtime"
    tar -xf "${tarball}" -C "${MTD_OPT_DIR}/runtime" || mtd_fail "runtime extract failed"

    # 锁文件一起落地：排障与升级时能对出「这发行版里是哪个版本/哪些插件」
    install -m 0644 "${MTD_ASSET_DIR}/ming-tea-dsh-lock.json" "${MTD_OPT_DIR}/runtime/ming-tea-dsh-lock.json" || return 1

    # dsh CLI 可执行位（tar 里保留的权限可能被文件系统抹掉）
    chmod 0755 "${MTD_OPT_DIR}/runtime/node_modules/.bin/dsh" 2>/dev/null || true
    [[ -x "${MTD_OPT_DIR}/runtime/node_modules/.bin/dsh" ]] || mtd_fail "dsh CLI is not executable after extract"

    mkdir -p "${MTD_OPT_DIR}/dsh-home"
    return 0
}

# ── 5. 热键守护进程（自启动 + 可执行）────────────────────────────────────────
install_hotkey_daemon() {
    local helper="${MTD_ASSET_DIR}/ming-tea-hotkey"
    [[ -s "${helper}" ]] || mtd_fail "missing hotkey daemon binary"
    install -m 0755 "${helper}" "${MTD_OPT_DIR}/bin/ming-tea-hotkey" || return 1

    # autostart：登录后守护进程常驻，任何应用退出后热键都能呼出面板
    cat > /etc/xdg/autostart/ming-tea-hotkey.desktop <<'HOTKEYDESKTOP'
[Desktop Entry]
Type=Application
Name=铭荼热键服务
Comment=铭荼助手的全局快捷键守护进程
Exec=/opt/ming-tea/bin/ming-tea-hotkey
Terminal=false
X-Ming-Managed=true
NoDisplay=true
HOTKEYDESKTOP
    chmod 0644 /etc/xdg/autostart/ming-tea-hotkey.desktop || return 1
    return 0
}

# ── 6. 启动器与桌面入口 ──────────────────────────────────────────────────────
install_launcher() {
    cat > "${MTD_BIN}" <<'LAUNCHER'
#!/usr/bin/env bash
# 铭荼 DSH 桌面启动器：把 runtime/DSH_HOME 指到 /opt/ming-tea 再拉起 Tauri 应用。
set -euo pipefail

export MING_TEA_DSH_HOME="${MING_TEA_DSH_HOME:-/opt/ming-tea/dsh-home}"
export MING_TEA_DSH_RUNTIME_DIR="${MING_TEA_DSH_RUNTIME_DIR:-/opt/ming-tea/runtime}"
export MING_TEA_REPO_ROOT="${MING_TEA_REPO_ROOT:-/opt/ming-tea}"
export PATH="${MING_TEA_DSH_RUNTIME_DIR}/node_modules/.bin:${PATH}"

exec /usr/bin/ming-tea-desktop "$@"
LAUNCHER
    chmod 0755 "${MTD_BIN}" || return 1

    cat > /usr/share/applications/ming-tea-dsh.desktop <<'APPDESKTOP'
[Desktop Entry]
Name=铭荼助手
Name[zh_CN]=铭荼助手
Comment=铭荼语音与任务助手（DeepSeek Harness 桌面版）
Exec=/usr/local/bin/ming-tea-dsh
Icon=ming-os-logo
Terminal=false
Type=Application
Categories=Utility;Office;Development;Education;
StartupNotify=true
StartupWMClass=cn.mingos.mingtea
X-Ming-Managed=true
X-Ming-AppRole=assistant
APPDESKTOP
    chmod 0644 /usr/share/applications/ming-tea-dsh.desktop || return 1
    desktop-file-validate /usr/share/applications/ming-tea-dsh.desktop || return 1
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    return 0
}

# ── 7. 插件 manifest 标记核验（ISO 不得缺审计目录）────────────────────────────
# 原 backend-validator 版检查与独立运行的 fixture 测试冲突（validator 只管 Exec
# 可解析性，无 shell 函数作用域）；manifest 的 marker 检查归位到本模块。
verify_plugin_manifest() {
    local manifest="/usr/share/ming-os/ming-tea/plugins.json"
    [[ -s "${manifest}" ]] || mtd_fail "missing Ming Tea plugin manifest: ${manifest}"
    local marker
    for marker in ming-browser-adapter ming-terminal-adapter ming-office-adapter; do
        grep -q "\"${marker}\"" "${manifest}" || mtd_fail "Ming Tea plugin manifest missing ${marker}"
    done
    return 0
}

main() {
    mtd_log "开始安装铭荼 DSH 桌面应用"
    [[ -d "${MTD_ASSET_DIR}" ]] || mtd_fail "missing ${MTD_ASSET_DIR}（构建宿主机要先跑 scripts/prepare_ming_tea_desktop.sh）"

    verify_receipt || return 1
    install_runtime_dependencies || return 1
    install_application_package || return 1
    install_dsh_runtime || return 1
    install_hotkey_daemon || return 1
    install_launcher || return 1
    verify_plugin_manifest || return 1

    # 与模块 03 的 GTK 壳并存：ming-tea.desktop（原生壳）保留，
    # ming-tea-dsh.desktop 是完整版；把「铭荼」入口留给更完整的 DSH 版本。
    if [[ -f /usr/share/applications/ming-tea.desktop ]]; then
        sed -i 's/^Name=铭荼$/Name=铭荼（经典）/' /usr/share/applications/ming-tea.desktop 2>/dev/null || true
        sed -i 's/^Name\[zh_CN\]=铭荼$/Name[zh_CN]=铭荼（经典）/' /usr/share/applications/ming-tea.desktop 2>/dev/null || true
    fi

    mtd_log "铭荼 DSH 桌面应用安装完成（/opt/ming-tea，启动器 ${MTD_BIN}）"
}

main "$@"
