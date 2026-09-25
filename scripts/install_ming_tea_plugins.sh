#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly LOCK_FILE="${REPO_ROOT}/assets/ming-tea-dsh-lock.json"
readonly RUNTIME_DIR="${MING_TEA_DSH_RUNTIME_DIR:-${REPO_ROOT}/.ming-tea/runtime}"

command -v npm >/dev/null 2>&1 || { echo "ERROR: npm is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required" >&2; exit 1; }
[[ -s "${LOCK_FILE}" ]] || { echo "ERROR: missing ${LOCK_FILE}" >&2; exit 1; }

mapfile -t packages < <(python3 - "${LOCK_FILE}" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(f'{payload["runtime"]["package"]}@{payload["runtime"]["version"]}')
for plugin in payload["plugins"]:
    print(f'{plugin["package"]}@{plugin["version"]}')
PY
)

mkdir -p "${RUNTIME_DIR}"
if [[ ! -f "${RUNTIME_DIR}/package.json" ]]; then
    printf '{"private":true,"name":"ming-tea-dsh-runtime"}\n' > "${RUNTIME_DIR}/package.json"
fi

npm install --prefix "${RUNTIME_DIR}" --ignore-scripts --no-audit --no-fund --save-exact "${packages[@]}"
runtime_bin="${RUNTIME_DIR}/node_modules/.bin/dsh"
[[ -x "${runtime_bin}" ]] || { echo "ERROR: DSH CLI was not installed" >&2; exit 1; }
expected="$(python3 - "${LOCK_FILE}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["runtime"]["version"])')"
actual="$(${runtime_bin} --version)"
[[ "${actual}" == "${expected}" ]] || { echo "ERROR: DSH ${actual} != ${expected}" >&2; exit 1; }
echo "Installed audited 铭荼 DSH runtime ${actual} (${#packages[@]} packages)"
