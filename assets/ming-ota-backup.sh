#!/usr/bin/env bash
set -euo pipefail

readonly VERSION="1.0.0"
SOURCE_ROOT="${MING_OTA_SOURCE_ROOT:-/home}"
DEST_ROOT="${MING_OTA_DEST_ROOT:-}"
TARGET_ROOT=""
MANIFEST_PATH=""
SYSTEM_TARGET_ROOT="${MING_OTA_SYSTEM_TARGET_ROOT:-}"

log_error() { printf 'ming-ota-backup: %s\n' "$*" >&2; }

usage() {
    cat <<'HELP'
Usage:
  ming-ota-backup backup  [--source PATH] --dest PATH
  ming-ota-backup verify  [--dest PATH | --manifest PATH]
  ming-ota-backup restore --manifest PATH --target TARGET_HOME --system-target TARGET_ROOT
  ming-ota-backup doctor  [--source PATH] --dest PATH

Environment overrides:
  MING_OTA_SOURCE_ROOT, MING_OTA_DEST_ROOT, MING_OTA_SYSTEM_TARGET
  MING_OTA_MACHINE_ID, MING_OTA_DISK_UUID
HELP
}

canonical_path() {
    realpath -m -- "$1"
}

paths_overlap() {
    local left right
    left="$(canonical_path "$1")"
    right="$(canonical_path "$2")"
    [[ "${left}" == "${right}" || "${left}" == "${right}/"* || "${right}" == "${left}/"* ]]
}

existing_path_for_mount() {
    local path
    path="$(canonical_path "$1")"
    while [[ ! -e "${path}" && "${path}" != "/" ]]; do
        path="$(dirname "${path}")"
    done
    printf '%s\n' "${path}"
}

mount_device() {
    local path
    path="$(existing_path_for_mount "$1")"
    findmnt -nro SOURCE -T "${path}" 2>/dev/null || true
}

physical_disks() {
    local device
    device="$(mount_device "$1")"
    [[ "${device}" == /dev/* ]] || return 1
    lsblk -s -nrpo NAME,TYPE "${device}" 2>/dev/null \
        | awk '$2 == "disk" {print $1}' \
        | sort -u
}

disk_uuid() {
    if [[ -n "${MING_OTA_DISK_UUID:-}" ]]; then
        printf '%s\n' "${MING_OTA_DISK_UUID}"
        return
    fi
    local device uuid
    device="$(mount_device "${DEST_ROOT}")"
    uuid="$(blkid -s UUID -o value "${device}" 2>/dev/null || true)"
    printf '%s\n' "${uuid:-unknown}"
}

machine_id() {
    if [[ -n "${MING_OTA_MACHINE_ID:-}" ]]; then
        printf '%s\n' "${MING_OTA_MACHINE_ID}"
    elif [[ -s /etc/machine-id ]]; then
        tr -d '\n' < /etc/machine-id
        printf '\n'
    else
        printf 'unknown\n'
    fi
}

source_bytes() {
    du -sb -- "${SOURCE_ROOT}" | awk '{print $1}'
}

available_bytes() {
    if [[ -n "${MING_OTA_AVAILABLE_BYTES:-}" ]]; then
        printf '%s\n' "${MING_OTA_AVAILABLE_BYTES}"
        return
    fi
    local probe
    probe="$(existing_path_for_mount "${DEST_ROOT}")"
    df -PB1 -- "${probe}" | awk 'NR == 2 {print $4}'
}

validate_paths() {
    local source="$1" dest="$2" system_target root_disks dest_disks disk
    [[ -d "${source}" ]] || { log_error "source does not exist: ${source}"; return 2; }
    [[ -n "${dest}" ]] || { log_error "destination is required"; return 2; }
    if paths_overlap "${source}" "${dest}"; then
        log_error "source and destination overlap"
        return 2
    fi

    system_target="${MING_OTA_SYSTEM_TARGET:-}"
    if [[ -n "${system_target}" ]] && paths_overlap "${dest}" "${system_target}"; then
        log_error "destination conflicts with system target"
        return 2
    fi

    if [[ "${MING_OTA_TEST_MODE:-0}" != "1" ]]; then
        root_disks="$(physical_disks / || true)"
        dest_disks="$(physical_disks "${dest}" || true)"
        if [[ -z "${root_disks}" || -z "${dest_disks}" ]]; then
            log_error "unable to resolve physical disk ancestry"
            return 2
        fi
        while IFS= read -r disk; do
            [[ -n "${disk}" ]] || continue
            if grep -Fxq -- "${disk}" <<< "${dest_disks}"; then
                log_error "destination disk conflicts with system target disk ${disk}"
                return 2
            fi
        done <<< "${root_disks}"
    fi
}

copy_tree() {
    local source="$1" dest="$2"
    mkdir -p "${dest}"
    if command -v rsync >/dev/null 2>&1; then
        rsync -aHAX --numeric-ids --delete -- "${source}/" "${dest}/"
    elif [[ "${MING_OTA_TEST_MODE:-0}" == "1" ]]; then
        cp -a -- "${source}/." "${dest}/"
    else
        log_error "rsync is required for metadata-safe backups"
        return 6
    fi
}

write_inventory() {
    local payload="$1" output="$2"
    python3 - "${payload}" > "${output}" <<'PY'
import hashlib
import json
import os
import stat
import sys

root = os.path.realpath(sys.argv[1])
entries = []

def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

def visit(directory, prefix=""):
    with os.scandir(directory) as iterator:
        children = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
    for child in children:
        relative = f"{prefix}/{child.name}" if prefix else child.name
        metadata = child.stat(follow_symlinks=False)
        item = {
            "path": relative,
            "type": "other",
            "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "size": metadata.st_size,
            "sha256": None,
            "link_target": None,
        }
        if stat.S_ISREG(metadata.st_mode):
            item["type"] = "file"
            item["sha256"] = digest(child.path)
        elif stat.S_ISDIR(metadata.st_mode):
            item["type"] = "directory"
        elif stat.S_ISLNK(metadata.st_mode):
            item["type"] = "symlink"
            item["link_target"] = os.readlink(child.path)
        entries.append(item)
        if item["type"] == "directory":
            visit(child.path, relative)

visit(root)
json.dump(entries, sys.stdout, ensure_ascii=True, separators=(",", ":"))
sys.stdout.write("\n")
PY
}

verify_inventory() {
    local manifest="$1" payload="$2" actual
    actual="$(mktemp)"
    if ! write_inventory "${payload}" "${actual}"; then
        rm -f "${actual}"
        log_error "unable to build backup inventory"
        return 4
    fi
    if ! python3 - "${manifest}" "${actual}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    expected = json.load(handle).get("entries")
with open(sys.argv[2], encoding="utf-8") as handle:
    actual = json.load(handle)
if not isinstance(expected, list) or expected != actual:
    raise SystemExit(1)
PY
    then
        rm -f "${actual}"
        log_error "backup inventory mismatch"
        return 4
    fi
    rm -f "${actual}"
}

write_manifest() {
    local path="$1" source="$2" dest="$3" uuid="$4" files="$5" bytes="$6" inventory="$7" tmp
    tmp="${path}.tmp.$$"
    python3 - "${source}" "${dest}" "$(machine_id)" "${uuid}" "${files}" "${bytes}" "${inventory}" > "${tmp}" <<'PY'
import datetime
import json
import sys

source, dest, machine_id, disk_uuid, file_count, byte_count, inventory_path = sys.argv[1:]
with open(inventory_path, encoding="utf-8") as handle:
    entries = json.load(handle)
json.dump({
    "schema": 1,
    "machine_id": machine_id,
    "source": source,
    "dest": dest,
    "destination": dest,
    "disk_uuid": disk_uuid,
    "backup_uuid": disk_uuid,
    "file_count": int(file_count),
    "bytes": int(byte_count),
    "entries": entries,
    "completed": True,
    "complete": True,
    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, sys.stdout, ensure_ascii=True, indent=2)
sys.stdout.write("\n")
PY
    chmod 600 "${tmp}"
    mv -f -- "${tmp}" "${path}"
}

json_value() {
    local manifest="$1" key="$2"
    python3 - "${manifest}" "${key}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle).get(sys.argv[2], "")
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

resolve_manifest() {
    if [[ -n "${MANIFEST_PATH}" ]]; then
        canonical_path "${MANIFEST_PATH}"
    elif [[ -n "${DEST_ROOT}" ]]; then
        printf '%s/manifest.json\n' "$(canonical_path "${DEST_ROOT}")"
    else
        return 1
    fi
}

backup_command() {
    SOURCE_ROOT="$(canonical_path "${SOURCE_ROOT}")"
    DEST_ROOT="$(canonical_path "${DEST_ROOT}")"
    validate_paths "${SOURCE_ROOT}" "${DEST_ROOT}"

    local required available reserve headroom minimum_reserve payload marker manifest files bytes uuid inventory
    bytes="$(source_bytes)"
    headroom=$(( (bytes + 9) / 10 ))
    minimum_reserve="${MING_OTA_MIN_RESERVE_BYTES:-67108864}"
    [[ "${minimum_reserve}" =~ ^[0-9]+$ ]] || minimum_reserve=67108864
    reserve="${headroom}"
    if [[ "${reserve}" -lt "${minimum_reserve}" ]]; then
        reserve="${minimum_reserve}"
    fi
    required=$((bytes + reserve))
    available="$(available_bytes)"
    if [[ ! "${available}" =~ ^[0-9]+$ || "${available}" -lt "${required}" ]]; then
        log_error "insufficient space: need ${required} bytes, have ${available:-0}"
        return 3
    fi

    mkdir -p "${DEST_ROOT}"
    marker="${DEST_ROOT}/.incomplete"
    manifest="${DEST_ROOT}/manifest.json"
    payload="${DEST_ROOT}/data"
    printf 'backup started %s\n' "$(date -Iseconds)" > "${marker}"
    rm -f -- "${manifest}"
    if ! copy_tree "${SOURCE_ROOT}" "${payload}"; then
        log_error "backup copy failed; incomplete marker retained"
        return 5
    fi

    files="$(find "${payload}" -type f -printf '.' | wc -c)"
    uuid="$(disk_uuid)"
    if [[ "${uuid}" == "unknown" || -z "${uuid}" ]]; then
        log_error "destination disk has no verifiable UUID; incomplete marker retained"
        return 4
    fi
    inventory="${DEST_ROOT}/.inventory.tmp.$$"
    if ! write_inventory "${payload}" "${inventory}"; then
        rm -f "${inventory}"
        log_error "failed to create content inventory; incomplete marker retained"
        return 4
    fi
    write_manifest "${manifest}" "${SOURCE_ROOT}" "${DEST_ROOT}" "${uuid}" "${files}" "${bytes}" "${inventory}"
    rm -f "${inventory}"
    rm -f -- "${marker}"
    printf '%s\n' "${manifest}"
}

verify_command() {
    local manifest dest payload expected_files expected_bytes actual_files actual_bytes complete expected_uuid current_uuid
    manifest="$(resolve_manifest)" || { log_error "manifest is required"; return 2; }
    [[ -f "${manifest}" ]] || { log_error "manifest not found: ${manifest}"; return 4; }
    dest="$(dirname "${manifest}")"
    if [[ -e "${dest}/.incomplete" ]]; then
        log_error "backup is incomplete"
        return 4
    fi
    complete="$(json_value "${manifest}" complete)"
    [[ "${complete}" == "true" ]] || { log_error "manifest is incomplete"; return 4; }
    payload="${dest}/data"
    [[ -d "${payload}" ]] || { log_error "backup payload is missing"; return 4; }
    expected_files="$(json_value "${manifest}" file_count)"
    expected_bytes="$(json_value "${manifest}" bytes)"
    actual_files="$(find "${payload}" -type f -printf '.' | wc -c)"
    actual_bytes="$(du -sb -- "${payload}" | awk '{print $1}')"
    [[ "${actual_files}" == "${expected_files}" ]] || { log_error "backup file count mismatch"; return 4; }
    [[ "${actual_bytes}" -ge "${expected_bytes}" ]] || { log_error "backup byte count mismatch"; return 4; }
    expected_uuid="$(json_value "${manifest}" backup_uuid)"
    DEST_ROOT="${dest}"
    current_uuid="$(disk_uuid)"
    if [[ -z "${expected_uuid}" || "${expected_uuid}" == "unknown" ]]; then
        log_error "manifest has no verifiable backup disk UUID"
        return 4
    fi
    if [[ "${current_uuid}" != "unknown" && "${expected_uuid}" != "${current_uuid}" ]]; then
        log_error "backup disk UUID mismatch"
        return 4
    fi
    verify_inventory "${manifest}" "${payload}"
    printf 'verified: %s\n' "${manifest}"
}

restore_command() {
    local manifest payload target system_target
    manifest="$(resolve_manifest)" || { log_error "--manifest is required"; return 2; }
    [[ -n "${TARGET_ROOT}" ]] || { log_error "--target is required"; return 2; }
    [[ -n "${SYSTEM_TARGET_ROOT}" ]] || { log_error "--system-target is required"; return 2; }
    if [[ -L "${TARGET_ROOT}" || -L "${SYSTEM_TARGET_ROOT}" ]]; then
        log_error "restore target and system target must not be symlinks"
        return 2
    fi
    [[ -d "${SYSTEM_TARGET_ROOT}" ]] || { log_error "system target does not exist"; return 2; }
    MANIFEST_PATH="${manifest}"
    verify_command >/dev/null
    payload="$(dirname "${manifest}")/data"
    target="$(canonical_path "${TARGET_ROOT}")"
    system_target="$(canonical_path "${SYSTEM_TARGET_ROOT}")"
    if [[ "${target}" != "${system_target}" && "${target}" != "${system_target}/"* ]]; then
        log_error "restore target is outside trusted system target"
        return 2
    fi
    if paths_overlap "${payload}" "${target}"; then
        log_error "restore target overlaps backup"
        return 2
    fi
    copy_tree "${payload}" "${target}"
    printf 'restored: %s\n' "${target}"
}

doctor_command() {
    SOURCE_ROOT="$(canonical_path "${SOURCE_ROOT}")"
    DEST_ROOT="$(canonical_path "${DEST_ROOT}")"
    local rsync_available=true paths_safe=true error=""
    command -v rsync >/dev/null 2>&1 || rsync_available=false
    if ! error="$(validate_paths "${SOURCE_ROOT}" "${DEST_ROOT}" 2>&1)"; then
        paths_safe=false
    fi
    python3 - "${SOURCE_ROOT}" "${DEST_ROOT}" "${rsync_available}" "${paths_safe}" "${error}" <<'PY'
import json
import sys
source, dest, rsync_available, paths_safe, error = sys.argv[1:]
print(json.dumps({
    "source": source,
    "dest": dest,
    "rsync_available": rsync_available == "true",
    "paths_safe": paths_safe == "true",
    "error": error,
}, ensure_ascii=True))
PY
    [[ "${paths_safe}" == "true" ]]
}

parse_options() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source) [[ $# -ge 2 ]] || return 2; SOURCE_ROOT="$2"; shift 2 ;;
            --dest) [[ $# -ge 2 ]] || return 2; DEST_ROOT="$2"; shift 2 ;;
            --target) [[ $# -ge 2 ]] || return 2; TARGET_ROOT="$2"; shift 2 ;;
            --system-target) [[ $# -ge 2 ]] || return 2; SYSTEM_TARGET_ROOT="$2"; shift 2 ;;
            --manifest) [[ $# -ge 2 ]] || return 2; MANIFEST_PATH="$2"; shift 2 ;;
            --help|-h) usage; exit 0 ;;
            *) log_error "unknown option: $1"; return 2 ;;
        esac
    done
}

main() {
    local command="${1:---help}"
    case "${command}" in
        --help|-h|help) usage ;;
        backup|verify|restore|doctor)
            shift
            parse_options "$@"
            case "${command}" in
                backup) backup_command ;;
                verify) verify_command ;;
                restore) restore_command ;;
                doctor) doctor_command ;;
            esac
            ;;
        *) log_error "unknown command: ${command}"; usage >&2; return 2 ;;
    esac
}

main "$@"
