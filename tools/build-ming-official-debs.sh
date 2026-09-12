#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
output_dir="${2:-}"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+~rc[0-9]+$ ]] || {
    echo "用法：build-ming-official-debs.sh VERSION OUTPUT_DIR package|description|source ..." >&2
    exit 2
}
[[ -n "${output_dir}" && $# -ge 3 ]] || exit 2
command -v dpkg-deb >/dev/null 2>&1 || { echo "缺少 dpkg-deb。" >&2; exit 127; }
mkdir -p "${output_dir}"

root="$(mktemp -d)"
trap 'rm -rf "${root}"' EXIT

build_component() {
    local package="$1" description="$2" source_path="$3"
    local package_root="${root}/${package}"
    mkdir -p "${package_root}/DEBIAN" "${package_root}/usr/share/ming-os/official"
    cat > "${package_root}/DEBIAN/control" <<EOF
Package: ${package}
Version: ${version}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Ming OS <release@ming-os.invalid>
Description: ${description}
 Ming OS official component.
EOF
    [[ -f "${source_path}" ]] || { echo "官方组件源文件不存在：${source_path}" >&2; exit 1; }
    install -m 0644 "${source_path}" "${package_root}/usr/share/ming-os/official/"
    dpkg-deb --build --root-owner-group "${package_root}" "${output_dir}/${package}_${version}_amd64.deb" >/dev/null
}

shift 2
while [[ $# -gt 0 ]]; do
    spec="$1"
    shift
    IFS='|' read -r package description source_path <<< "${spec}"
    [[ "${package}" =~ ^[a-z0-9][a-z0-9-]+$ && -n "${description}" && -n "${source_path}" ]] || {
        echo "组件格式无效，应为 package|description|source_path。" >&2
        exit 2
    }
    build_component "${package}" "${description}" "${source_path}"
done
