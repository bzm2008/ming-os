#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'usage: secret-scan-ming-release.sh --root PUBLIC_ROOT' >&2
}

root=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            root="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[[ -n "${root}" && -d "${root}" && ! -L "${root}" ]] || {
    printf '%s\n' 'public scan root must be a real directory' >&2
    exit 78
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${script_dir}/ming-release-vault.py" scan-public --root "${root}"
