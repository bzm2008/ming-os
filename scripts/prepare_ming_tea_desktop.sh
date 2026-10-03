#!/usr/bin/env bash
# ============================================================================
# 铭荼 DSH 桌面 —— ming-os ISO 构建产物准备（在构建宿主机上跑，不在 chroot 里）
# ============================================================================
# 产出：assets/vendor/ming-tea-desktop/
#   ├── ming-tea-desktop_amd64.deb       Tauri Linux 包（libwebkit2gtk-4.1 依赖）
#   ├── ming-tea-hotkey                  全局热键守护进程（Linux x86_64）
#   ├── ming-tea-runtime.tar.zst         DSH runtime（node_modules 树 + dsh CLI）
#   ├── ming-tea-dsh-lock.json           插件锁（来自 assets/ming-tea-dsh-lock.json）
#   └── receipt.json                     每个文件的 sha256（ISO 构建时逐个校验）
#
# 用法：
#   scripts/prepare_ming_tea_desktop.sh                # 全流程（当前平台是 Linux 才能出 deb）
#   scripts/prepare_ming_tea_desktop.sh --runtime-only # 只重打 runtime（deb 已就位时）
#
# 前置：Linux 上需要 cargo/rustc（stable）、pnpm、node22、zstd、dpkg-deb。
# macOS 上跑会先校验「产物已就位」，只重打 runtime 与 receipt（不能产出 Linux deb）。
# ============================================================================

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly LOCK_FILE="${REPO_ROOT}/assets/ming-tea-dsh-lock.json"
readonly OUT_DIR="${REPO_ROOT}/assets/vendor/ming-tea-desktop"
readonly PLATFORM_DIR="${REPO_ROOT}/platform/ming-tea/apps/desktop"
readonly RUNTIME_DIR="${REPO_ROOT}/.ming-tea/runtime"

runtime_only=false
[[ "${1:-}" == "--runtime-only" ]] && runtime_only=true

log() { echo "[prepare-ming-tea] $*"; }
fail() { echo "[prepare-ming-tea] ERROR: $*" >&2; exit 1; }

mkdir -p "${OUT_DIR}"

# ── 1. Linux .deb（只能在 Linux 上产出）─────────────────────────────────────
if ! ${runtime_only}; then
  if [[ "$(uname -s)" == "Linux" ]]; then
    log "构建 Linux .deb（tauri build --bundles deb）…"
    export PATH="${RUNTIME_DIR}/node_modules/.bin:${HOME}/.cargo/bin:${PATH}"
    command -v cargo >/dev/null || fail "需要 cargo（rustup）"
    (cd "${PLATFORM_DIR}" && pnpm build:hotkey && pnpm exec tauri build --bundles deb)
    produced="$(ls "${PLATFORM_DIR}/src-tauri/target/release/bundle/deb/"*.deb 2>/dev/null | head -1 || true)"
    [[ -n "${produced}" ]] || fail "tauri build 没有产出 .deb"
    cp "${produced}" "${OUT_DIR}/ming-tea-desktop_amd64.deb"
  else
    if [[ -s "${OUT_DIR}/ming-tea-desktop_amd64.deb" ]]; then
      log "当前平台是 $(uname -s)，跳过 .deb 构建（使用已有 ${OUT_DIR}/ming-tea-desktop_amd64.deb）"
    else
      fail "Linux .deb 不存在且当前平台是 $(uname -s)：请在 Linux（Debian 13）上先跑一次本脚本，或提交 CI 产物（.github/workflows/ming-tea-platform.yml 的 deb,appimage 矩阵）"
    fi
  fi
fi

# ── 2. 热键守护进程（Linux x86_64）──────────────────────────────────────────
if [[ "$(uname -s)" == "Linux" ]]; then
  log "构建热键守护进程…"
  cargo build --release --manifest-path "${REPO_ROOT}/platform/ming-tea/apps/hotkey-daemon/Cargo.toml"
  cp "${REPO_ROOT}/platform/ming-tea/apps/hotkey-daemon/target/release/ming-tea-hotkey" "${OUT_DIR}/ming-tea-hotkey"
elif [[ ! -s "${OUT_DIR}/ming-tea-hotkey" && "${runtime_only}" == "false" ]]; then
  fail "Linux 守护进程二进制不存在：请在 Linux 上产出（CI 的 deb,appimage 矩阵会产出）"
fi

# ── 3. DSH runtime 打包（node_modules + dsh CLI + 插件 profile 骨架）────────
log "打包 DSH runtime…"
[[ -d "${RUNTIME_DIR}/node_modules" ]] || fail "runtime 未安装：先跑 scripts/install_ming_tea_plugins.sh"
# 只带运行需要的部分：node_modules（含 .bin）、package.json；会话/凭证绝不打包。
# 优先 zstd（体积/速度）；本机没有 zstd 时降级 gzip（Debian 侧模块 04 两种都认）。
if command -v zstd >/dev/null 2>&1; then
    RUNTIME_TARBALL="ming-tea-runtime.tar.zst"
    tar --zstd -cf "${OUT_DIR}/${RUNTIME_TARBALL}" \
        -C "${RUNTIME_DIR}" node_modules package.json
else
    RUNTIME_TARBALL="ming-tea-runtime.tar.gz"
    log "本机没有 zstd，改用 gzip（体积略大，功能等价）"
    tar -czf "${OUT_DIR}/${RUNTIME_TARBALL}" \
        -C "${RUNTIME_DIR}" node_modules package.json
fi

cp "${LOCK_FILE}" "${OUT_DIR}/ming-tea-dsh-lock.json"

# ── 4. receipt.json（全部产物的 sha256）─────────────────────────────────────
log "生成 receipt.json…"
RUNTIME_ONLY="${runtime_only}" python3 - "${OUT_DIR}" <<'PY'
import hashlib
import json
import pathlib
import sys
import time

out_dir = pathlib.Path(sys.argv[1])
runtime_only = __import__("os").environ.get("RUNTIME_ONLY") == "true"
expected = [
    "ming-tea-desktop_amd64.deb",
    "ming-tea-hotkey",
    "ming-tea-runtime.tar.zst",
    "ming-tea-dsh-lock.json",
]
files = {}
missing = []
for name in expected:
    target = out_dir / name
    if not target.is_file():
        missing.append(name)
        continue
    files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
if runtime_only:
    files = {k: v for k, v in files.items() if k in ("ming-tea-runtime.tar.zst", "ming-tea-dsh-lock.json")}
elif missing:
    raise SystemExit(f"missing build outputs: {', '.join(missing)}")

receipt = {
    "name": "ming-tea-desktop",
    "platform": "debian13-amd64",
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "dsh_lock": "assets/ming-tea-dsh-lock.json",
    "files": files,
}
(out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"[prepare-ming-tea] receipt.json: {len(files)} files")
PY

log "完成。产物在 ${OUT_DIR}："
ls -la "${OUT_DIR}"
