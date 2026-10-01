#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly LOCK_FILE="${REPO_ROOT}/assets/ming-tea-dsh-lock.json"
readonly RUNTIME_DIR="${MING_TEA_DSH_RUNTIME_DIR:-${REPO_ROOT}/.ming-tea/runtime}"
readonly DSH_HOME_DIR="${MING_TEA_DSH_HOME:-${RUNTIME_DIR}/dsh-home}"
readonly PROFILE_NAME="${MING_TEA_DSH_PROFILE:-ming-tea}"
readonly PROFILE_TEMPLATE="${MING_TEA_DSH_PROFILE_TEMPLATE:-web}"
readonly PROFILE_DIR="${DSH_HOME_DIR}/profiles/${PROFILE_NAME}"
readonly PNPM_SPEC="$(python3 - "${REPO_ROOT}/platform/ming-tea/package.json" <<'PY'
import json, sys
try:
    manager = json.load(open(sys.argv[1], encoding="utf-8")).get("packageManager", "")
except (OSError, ValueError):
    manager = ""
print(manager if manager.startswith("pnpm@") else "pnpm@11.19.0")
PY
)"

command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required" >&2; exit 1; }
[[ -s "${LOCK_FILE}" ]] || { echo "ERROR: missing ${LOCK_FILE}" >&2; exit 1; }

# The lock file drives installation. Packages without a "profiles" entry belong
# to the runtime tree; packages that declare one are DSH profile plugins and are
# registered through "dsh plugin" instead. A plugin with a repo-relative
# "source" (link:...) is installed from the working tree rather than the registry.
runtime_packages=()
profile_plugins=()
profile_packages=()
while read -r kind spec pkg; do
    case "${kind}" in
        runtime) runtime_packages+=("${spec}") ;;
        plugin)
            profile_plugins+=("${spec}")
            profile_packages+=("${pkg}")
            ;;
    esac
done < <(python3 - "${LOCK_FILE}" "${REPO_ROOT}" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
repo_root = sys.argv[2].rstrip("/")
print("runtime", f'{payload["runtime"]["package"]}@{payload["runtime"]["version"]}')
for plugin in payload["plugins"]:
    name = plugin["package"]
    if not plugin.get("profiles"):
        print("runtime", f'{name}@{plugin["version"]}')
    elif plugin.get("source", "").startswith("link:"):
        print("plugin", f'link:{repo_root}/{plugin["source"][len("link:"):]}', name)
    else:
        print("plugin", f'{name}@{plugin["version"]}', name)
PY
)

mkdir -p "${RUNTIME_DIR}"
if [[ ! -f "${RUNTIME_DIR}/package.json" ]]; then
    printf '{"private":true,"name":"ming-tea-dsh-runtime"}\n' > "${RUNTIME_DIR}/package.json"
fi

# 运行时树是**构建产物**：先清掉 node_modules 再装，否则上一版的遗留包会把新版本的 peer 锁死，
# npm 直接 ERESOLVE 失败。实测 0.1.7-rc.1 → 0.2.0-rc.2 必然踩到：树里旧的
# `@deepseek-ai/dsh-experimental-browser-use-playwright-mcp@0.1.7-rc.1` 仍要求
# `@deepseek-ai/dsh-browser-use@0.1.7-rc.1`，与本次要装的 0.2.0-rc.2 冲突。
# 只删 node_modules 与 package-lock.json；`dsh-home/`（profile、会话、凭证）必须保留。
rm -rf "${RUNTIME_DIR}/node_modules" "${RUNTIME_DIR}/package-lock.json"

# pnpm ships beside the runtime so "dsh plugin" can spawn it: dsh does not
# resolve pnpm from the runtime prefix on its own.
# （曾经在这里加过 tsx：语音 provider 的 worker 只有在 provider 自身以 .ts 加载时才需要它，
#   而发布包是 .js（见其 lib/index.js 的 `import.meta.url.endsWith(".ts") ? worker.ts : worker.js`），
#   所以实测不需要，已撤掉 —— 不引入用不到的运行时依赖。）
npm install --prefix "${RUNTIME_DIR}" --ignore-scripts --no-audit --no-fund --save-exact \
    "${runtime_packages[@]}" "${PNPM_SPEC}"

runtime_bin_dir="${RUNTIME_DIR}/node_modules/.bin"
export PATH="${runtime_bin_dir}:${PATH}"
runtime_bin="${runtime_bin_dir}/dsh"
[[ -x "${runtime_bin}" ]] || { echo "ERROR: DSH CLI was not installed" >&2; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo "ERROR: pnpm was not installed into ${RUNTIME_DIR}" >&2; exit 1; }

expected="$(python3 - "${LOCK_FILE}" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["runtime"]["version"])
PY
)"
actual="$("${runtime_bin}" --version)"
[[ "${actual}" == "${expected}" ]] || { echo "ERROR: DSH ${actual} != ${expected}" >&2; exit 1; }

# Derive our own profile from a shipped template instead of mutating the shipped
# one, so upgrades and audits keep a clean boundary. DSH owns the profile
# manifest and its cordis.patch.yml user layer; this script must not rewrite them.
mkdir -p "${DSH_HOME_DIR}"
if [[ ! -f "${PROFILE_DIR}/package.json" ]]; then
    DSH_HOME="${DSH_HOME_DIR}" "${runtime_bin}" \
        --profile "${PROFILE_NAME}" --from-default-profile "${PROFILE_TEMPLATE}" --dump-config >/dev/null
fi

# 精确版本豁免（lock 的 versionExemptions）：**必须在 `dsh plugin add` 之前**授予。
# 原因（0.2.0-rc.2 实测读码）：DSH 的兼容预检跑在 pnpm 之前，不满足 `@deepseek-ai/dsh*`
# peer 的插件会被直接拒绝（`installation rejected: … nothing was installed`，退出码 1），
# 而本脚本是 `set -euo pipefail` —— 晚一步就会半途中止，留下「一半新一半旧」的 profile。
# 豁免写在 profile 的 compatibility.json 里，精确到 `包@版本 × DSH 版本`；撤销用 revoke-version。
while read -r exempt_spec exempt_dsh; do
    [[ -n "${exempt_spec}" ]] || continue
    echo "granting exact-version exemption: ${exempt_spec} on dsh ${exempt_dsh}"
    DSH_HOME="${DSH_HOME_DIR}" "${runtime_bin}" plugin --profile "${PROFILE_NAME}" \
        allow-version "${exempt_spec}" --dsh-version "${exempt_dsh}" --accept-risk
done < <(python3 - "${LOCK_FILE}" <<'PY'
import json, sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for entry in payload.get("versionExemptions", []):
    print(f'{entry["package"]}@{entry["version"]}', entry["dsh"])
PY
)

for spec in "${profile_plugins[@]}"; do
    DSH_HOME="${DSH_HOME_DIR}" "${runtime_bin}" plugin --profile "${PROFILE_NAME}" add "${spec}" --ignore-scripts --save-exact
done

# dsh-plugin-shop ships typert manifests in a codec shape our loader rejects
# (`schema:` instead of the `create: () => …` factory the published generator and
# loader agree on). Without this rewrite the shop row fails to activate.
node "${REPO_ROOT}/scripts/patch_shop_typert_compat.mjs" --dsh-home "${DSH_HOME_DIR}"

# Pin the shop's catalog source. This MUST live in the profile patch: that layer is
# applied after every bundle layer, whereas a patch shipped inside our own bundle is
# applied before the shop's bundle and loses the id-targeted override (measured).
python3 - "${PROFILE_DIR}/cordis.patch.yml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
marker = "# ming-tea:shop-catalog-pin"
block = f"""
{marker} —— 插件商店目录源钉死（详见 docs/ming-tea-plugin-audit.md）
# 默认值读环境变量 DSH_SHOP_CATALOG_URL，不钉死则任何人都能用环境变量把目录改向
# （目录内容决定能装什么）。自建白名单目录时只改下面这一行 URL。
- id: shop
  config:
    catalogUrl: "https://LivXue.github.io/dsh-plugin-shop/v1/"
    # 注意：按 id 的 config 覆盖是**整块替换**，不是深合并 —— 少写这里的 cacheDir
    # 会让商店丢掉缓存目录，表现为界面「暂时无法读取目录」（实测踩过）。
    cacheDir: !!js dshHomePath('shop')
"""
text = path.read_text(encoding="utf-8") if path.exists() else ""
if marker in text:
    print("shop catalog pin: already present")
else:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
    print("shop catalog pin: written to profile patch")
PY

python3 - "${PROFILE_DIR}/package.json" "${profile_packages[@]}" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
bundles = manifest.get("dsh", {}).get("profile", {}).get("bundles", [])
missing = [name for name in sys.argv[2:] if name not in bundles]
if missing:
    print("ERROR: profile bundles missing: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
print("profile bundles: " + ", ".join(bundles))
PY

echo "Installed audited 铭荼 DSH runtime ${actual} (${#runtime_packages[@]} packages); profile: ${PROFILE_DIR}"
