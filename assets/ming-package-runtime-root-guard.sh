#!/usr/bin/env bash
set -euo pipefail

runtime_root="${1:-}"

fail() {
    printf '[ming-package-runtime-root-guard] %s\n' "$1" >&2
    exit 1
}

validate_parent() {
    local path="$1" metadata mode owner group mode_value
    [[ -d "${path}" && ! -L "${path}" ]] \
        || fail "unsafe runtime parent: ${path}"
    metadata="$(stat -c '%a:%u:%g' -- "${path}" 2>/dev/null)" \
        || fail "cannot inspect runtime parent: ${path}"
    IFS=: read -r mode owner group <<<"${metadata}"
    [[ "${owner}" == 0 && "${group}" == 0 && "${mode}" =~ ^[0-7]{3,4}$ ]] \
        || fail "runtime parent is not root controlled: ${path}"
    mode_value=$((8#${mode}))
    (( (mode_value & 8#022) == 0 )) \
        || fail "runtime parent is group/world writable: ${path}"
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "root privileges are required"
[[ "${runtime_root}" == /* && "${runtime_root}" != / ]] \
    || fail "runtime root must be an absolute directory"

validate_parent "/"
IFS=/ read -r -a components <<<"${runtime_root#/}"
current=""
last_index=$((${#components[@]} - 1))
for index in "${!components[@]}"; do
    component="${components[index]}"
    [[ -n "${component}" && "${component}" != . && "${component}" != .. ]] \
        || fail "runtime root contains an unsafe component"
    current="${current}/${component}"
    if [[ -e "${current}" || -L "${current}" ]]; then
        validate_parent "${current}"
    else
        install -d -o root -g root -m 0755 -- "${current}" \
            || fail "cannot create runtime directory: ${current}"
        validate_parent "${current}"
    fi
    if (( index == last_index )); then
        metadata="$(stat -c '%a:%u:%g' -- "${current}" 2>/dev/null)" \
            || fail "cannot inspect runtime root"
        [[ "${metadata}" == "755:0:0" ]] \
            || fail "runtime root must be root:root mode 0755"
    fi
done
