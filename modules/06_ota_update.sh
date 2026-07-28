#!/usr/bin/env bash
# ============================================================================
# Ming OS module 06: OTA update system
# ============================================================================

set -uo pipefail

readonly OTA_CONFIG_DIR="/etc/ming-update"
readonly OTA_CACHE_DIR="/var/cache/ming-update"
readonly OTA_UPDATE_SERVER="https://ming.scallion.uno"
readonly OTA_API_ENDPOINT="/api/onion-update"

install_ota_dependencies() {
    echo "Installing OTA update dependencies..."
    apt install -y --no-install-recommends \
        curl wget jq rsync python3 squashfs-tools zenity yad libnotify-bin \
        pkexec polkitd lxpolkit
}

deploy_ota_backup_engine() {
    local source="/tmp/ming-build/assets/ming-ota-backup.sh"
    if [[ ! -s "${source}" ]]; then
        echo "[06_ota_update][ERROR] Missing OTA backup engine: ${source}" >&2
        return 1
    fi
    install -m 0755 "${source}" /usr/local/sbin/ming-ota-backup
    bash -n /usr/local/sbin/ming-ota-backup
}

deploy_ota_cli() {
    echo "Deploying ming-update CLI..."

    cat > /usr/local/bin/ming-update << 'OTACLI'
#!/usr/bin/env bash
set -euo pipefail

resolve_home() {
    local uid home
    uid="${EUID:-$(id -u)}"
    home="$(getent passwd "${uid}" 2>/dev/null | cut -d: -f6 || true)"
    if [[ -n "${home}" ]]; then
        printf '%s\n' "${home}"
    elif [[ "${uid}" -eq 0 ]]; then
        printf '%s\n' /root
    else
        printf '%s\n' "/tmp/ming-update-${uid}"
    fi
}

HOME="${HOME:-$(resolve_home)}"
export HOME

readonly SCRIPT_VERSION="1.2.0"
readonly CONFIG_DIR="/etc/ming-update"
readonly CACHE_DIR="/var/cache/ming-update"
readonly STATE_FILE="${CONFIG_DIR}/state.json"
readonly CONFIG_FILE="${CONFIG_DIR}/config.json"
readonly STAGING_DIR="/var/lib/ming-update"
readonly STAGING_RECORD="/var/lib/ming-update/staging.json"
readonly USER_CONFIG_DIR="${HOME}/.config/ming-update"
readonly USER_CACHE_DIR="${HOME}/.cache/ming-update"
readonly USER_STATE_FILE="${USER_CONFIG_DIR}/state.json"
readonly USER_CONFIG_FILE="${USER_CONFIG_DIR}/config.json"
readonly UPDATE_SERVER="https://ming.scallion.uno"
readonly API_ENDPOINT="/api/onion-update"
readonly BACKGROUND_AVAILABILITY_FILE="${CACHE_DIR}/background-availability.json"
readonly OTA_RELEASE_PUBLIC_KEY="/etc/ming-update/ota-release.minisign.pub"

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
log_step() { printf '\n=====> %s <=====\n\n' "$*"; }

ensure_dirs() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        mkdir -p "${CONFIG_DIR}" "${CACHE_DIR}"
        chmod 755 "${CONFIG_DIR}" "${CACHE_DIR}"
    else
        mkdir -p "${USER_CONFIG_DIR}" "${USER_CACHE_DIR}"
        chmod 700 "${USER_CONFIG_DIR}" "${USER_CACHE_DIR}"
    fi
}

config_file() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${CONFIG_FILE}"
    else
        printf '%s\n' "${USER_CONFIG_FILE}"
    fi
}

cache_dir() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${CACHE_DIR}"
    else
        printf '%s\n' "${USER_CACHE_DIR}"
    fi
}

state_file() {
    if [[ ${EUID:-$(id -u)} -eq 0 || ( -w "${CONFIG_DIR}" && -w "${CACHE_DIR}" ) ]]; then
        printf '%s\n' "${STATE_FILE}"
    else
        printf '%s\n' "${USER_STATE_FILE}"
    fi
}

find_cached_manifest() {
    local candidate
    candidate="$(cache_dir)/update_info.json"
    if [[ -f "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    # Scheduled checks run as root; expose that root-owned, read-only cache to
    # the unprivileged Settings process as its authoritative background result.
    if [[ -r "${CACHE_DIR}/update_info.json" ]]; then
        printf '%s\n' "${CACHE_DIR}/update_info.json"
        return 0
    fi
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        for candidate in /home/*/.cache/ming-update/update_info.json; do
            if [[ -f "${candidate}" ]]; then
                printf '%s\n' "${candidate}"
                return 0
            fi
        done
    fi
    return 1
}

state_candidate_is_downloaded() {
    local candidate="$1" status iso_path
    [[ -f "${candidate}" ]] || return 1
    status="$(jq -r '.status // ""' "${candidate}" 2>/dev/null || true)"
    iso_path="$(jq -r '.iso_path // ""' "${candidate}" 2>/dev/null || true)"
    [[ "${status}" == "downloaded" && -n "${iso_path}" && -f "${iso_path}" ]]
}

find_download_state_file() {
    local candidate
    candidate="$(state_file)"
    if state_candidate_is_downloaded "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        for candidate in /home/*/.config/ming-update/state.json; do
            if state_candidate_is_downloaded "${candidate}"; then
                printf '%s\n' "${candidate}"
                return 0
            fi
        done
    fi
    return 1
}

find_update_state_file() {
    local candidate
    candidate="$(state_file)"
    if [[ -f "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    if [[ -r "${STATE_FILE}" ]]; then
        printf '%s\n' "${STATE_FILE}"
        return 0
    fi
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        for candidate in /home/*/.config/ming-update/state.json; do
            if [[ -f "${candidate}" ]]; then
                printf '%s\n' "${candidate}"
                return 0
            fi
        done
    fi
    return 1
}

update_state_fields() {
    local state_path="$1" filter="$2"
    shift 2
    local tmp owner mode
    owner="$(stat -c '%u:%g' "${state_path}")"
    mode="$(stat -c '%a' "${state_path}")"
    tmp="$(mktemp "${state_path}.tmp.XXXXXX")"
    if ! jq "$@" "${filter}" "${state_path}" > "${tmp}"; then
        rm -f "${tmp}"
        return 1
    fi
    chmod "${mode}" "${tmp}"
    chown "${owner}" "${tmp}"
    mv -f "${tmp}" "${state_path}"
}

physical_disks_for_path() {
    local path="$1" device
    device="$(findmnt -nro SOURCE -T "${path}" 2>/dev/null || true)"
    [[ "${device}" == /dev/* ]] || return 1
    lsblk -s -nrpo NAME,TYPE "${device}" 2>/dev/null \
        | awk '$2 == "disk" {print $1}' \
        | sort -u
}

paths_share_physical_disk() {
    local left="$1" right="$2" left_disks right_disks disk
    left_disks="$(physical_disks_for_path "${left}" || true)"
    right_disks="$(physical_disks_for_path "${right}" || true)"
    [[ -n "${left_disks}" && -n "${right_disks}" ]] || return 2
    while IFS= read -r disk; do
        [[ -n "${disk}" ]] || continue
        grep -Fxq -- "${disk}" <<< "${right_disks}" && return 0
    done <<< "${left_disks}"
    return 1
}

home_is_independent_device() {
    local root_majmin home_majmin root_uuid home_uuid shared_status
    root_majmin="$(findmnt -nro MAJ:MIN -T / 2>/dev/null || true)"
    home_majmin="$(findmnt -nro MAJ:MIN -T /home 2>/dev/null || true)"
    root_uuid="$(findmnt -nro UUID -T / 2>/dev/null || true)"
    home_uuid="$(findmnt -nro UUID -T /home 2>/dev/null || true)"
    [[ -n "${root_majmin}" && -n "${home_majmin}" ]] || return 1
    # Btrfs subvolumes may have different SOURCE strings but share MAJ:MIN/UUID.
    [[ "${root_majmin}" != "${home_majmin}" ]] || return 1
    if [[ -n "${root_uuid}" && -n "${home_uuid}" && "${root_uuid}" == "${home_uuid}" ]]; then
        return 1
    fi
    if paths_share_physical_disk / /home; then
        return 1
    else
        shared_status=$?
        [[ "${shared_status}" -eq 1 ]] || return 1
    fi
}

ota_backup_destination() {
    local destination=""
    if [[ -f /run/ming-os/storage-info ]]; then
        destination="$(awk -F= '$1 == "data_mount" {print substr($0, index($0, "=") + 1); exit}' \
            /run/ming-os/storage-info 2>/dev/null || true)"
    fi
    destination="${MING_OTA_BACKUP_DEST:-${destination}}"
    [[ -n "${destination}" && -d "${destination}" ]] || return 1
    printf '%s\n' "${destination}"
}

home_preservation_status_json() {
    # This is presentation-only. The privileged major OTA path revalidates
    # every filesystem, UUID, capacity and checksum before staging GRUB.
    local update_type="$1" destination="" backup_uuid="" shared_status=0
    if [[ "${update_type}" != "major" ]]; then
        jq -n --arg message "此更新不会替换系统分区，无需准备用户文件备份。" \
            '{ready: true, strategy: "not_required", message: $message}'
        return 0
    fi

    if home_is_independent_device; then
        jq -n --arg source "$(findmnt -nro SOURCE -T /home 2>/dev/null || true)" \
            --arg message "/home 已位于独立物理设备，major OTA 会保留用户文件。" \
            '{ready: true, strategy: "separate_home", source: $source, message: $message}'
        return 0
    fi

    destination="$(ota_backup_destination 2>/dev/null || true)"
    if [[ -z "${destination}" ]]; then
        jq -n --arg message "major OTA 需要独立 /home 或另一块物理备份盘；请连接备份盘后重新检查。" \
            '{ready: false, strategy: "backup_required", message: $message}'
        return 0
    fi

    if paths_share_physical_disk / "${destination}"; then
        jq -n --arg destination "${destination}" \
            --arg message "检测到的备份位置与系统盘相同，不能用于 major OTA；请连接另一块物理备份盘。" \
            '{ready: false, strategy: "backup_required", destination: $destination, message: $message}'
        return 0
    else
        shared_status=$?
        if [[ "${shared_status}" -ne 1 ]]; then
            jq -n --arg destination "${destination}" \
                --arg message "无法验证备份盘是否独立于系统盘；为保护用户文件，major OTA 不会开始。" \
                '{ready: false, strategy: "backup_required", destination: $destination, message: $message}'
            return 0
        fi
    fi

    backup_uuid="$(findmnt -nro UUID -T "${destination}" 2>/dev/null || true)"
    if [[ -z "${backup_uuid}" ]]; then
        jq -n --arg destination "${destination}" \
            --arg message "备份盘没有可验证 UUID；请使用正常挂载的本地磁盘后重新检查。" \
            '{ready: false, strategy: "backup_required", destination: $destination, message: $message}'
        return 0
    fi

    jq -n --arg destination "${destination}" --arg backup_uuid "${backup_uuid}" \
        --arg message "已检测到独立备份盘；开始更新前会精确核验 /home 所需空间并创建可恢复备份。" \
        '{ready: true, strategy: "completed_backup", destination: $destination,
          backup_uuid: $backup_uuid, message: $message}'
}

fetch_authoritative_major_manifest() {
    local url response
    url="$(api_url)"
    response="$(curl -fsSL --retry 2 --retry-delay 1 --connect-timeout 10 --max-time 45 "${url}")" || {
        log_error "unable to refetch authoritative major update manifest"
        return 1
    }
    is_json_response "${response}" || {
        log_error "authoritative major update manifest is not valid JSON"
        return 1
    }
    printf '%s\n' "${response}"
}

validate_staging_inputs() {
    local state_path="$1" state status version iso_path checksum actual_checksum
    local backup_uuid backup_manifest backup_manifest_relative strategy iso_name
    local iso_uuid iso_mount_target iso_boot_path shared_status
    local manifest_uuid manifest_complete manifest_strategy mount_target expected_relative
    local authoritative authoritative_version authoritative_checksum authoritative_filename
    local authoritative_ready authoritative_available authoritative_type
    state="$(cat "${state_path}")"
    status="$(printf '%s' "${state}" | jq -r '.status // ""')"
    version="$(printf '%s' "${state}" | jq -r '.version // ""')"
    iso_path="$(printf '%s' "${state}" | jq -r '.iso_path // ""')"
    checksum="$(printf '%s' "${state}" | jq -r '.checksum // ""')"
    backup_uuid="$(printf '%s' "${state}" | jq -r '.backup_uuid // ""')"
    backup_manifest="$(printf '%s' "${state}" | jq -r '.backup_manifest // ""')"
    backup_manifest_relative="$(printf '%s' "${state}" | jq -r '.backup_manifest_relative // ""')"
    strategy="$(printf '%s' "${state}" | jq -r '.home_preservation.strategy // ""')"

    [[ "${status}" == "downloaded" ]] || { log_error "OTA state is not downloaded"; return 1; }
    [[ "${version}" =~ ^[0-9]+(\.[0-9]+){1,3}([A-Za-z0-9._-]*)?$ ]] || {
        log_error "OTA version has an unsafe format"; return 1;
    }
    [[ "${checksum}" =~ ^[A-Fa-f0-9]{64}$ ]] || {
        log_error "major update state requires a valid SHA256"; return 1;
    }
    [[ "${backup_uuid}" =~ ^[A-Fa-f0-9-]{4,128}$ ]] || {
        log_error "backup UUID has an unsafe format"; return 1;
    }
    [[ "${backup_manifest_relative}" =~ ^/[A-Za-z0-9._/+:-]+$ ]] || {
        log_error "backup manifest relative path has an unsafe format"; return 1;
    }
    [[ -f "${iso_path}" && ! -L "${iso_path}" ]] || { log_error "OTA ISO is missing or a symlink"; return 1; }
    iso_path="$(readlink -f -- "${iso_path}")" || return 1
    iso_uuid="$(findmnt -nro UUID -T "${iso_path}" 2>/dev/null || true)"
    [[ -n "${iso_uuid}" && "${iso_uuid}" == "${backup_uuid}" ]] || {
        log_error "OTA ISO is not staged on the verified preservation filesystem"; return 1;
    }
    iso_mount_target="$(findmnt -nro TARGET -T "${iso_path}" 2>/dev/null || true)"
    [[ -n "${iso_mount_target}" ]] || { log_error "OTA ISO mount target is unavailable"; return 1; }
    if [[ "${iso_mount_target}" == "/" ]]; then
        iso_boot_path="/${iso_path#/}"
    elif [[ "${iso_path}" == "${iso_mount_target}/"* ]]; then
        iso_boot_path="/${iso_path#${iso_mount_target}/}"
    else
        log_error "OTA ISO path is outside its mounted filesystem"
        return 1
    fi
    [[ "${iso_boot_path}" =~ ^/[A-Za-z0-9._/+:-]+\.iso$ ]] || {
        log_error "OTA ISO boot path has an unsafe format"; return 1;
    }
    if paths_share_physical_disk / "${iso_path}"; then
        log_error "OTA ISO preservation media shares the system target physical disk"
        return 1
    else
        shared_status=$?
        [[ "${shared_status}" -eq 1 ]] || {
            log_error "unable to verify OTA ISO physical disk ancestry"; return 1;
        }
    fi
    iso_name="$(basename -- "${iso_path}")"
    [[ "${iso_name}" == ming-os-"${version}"*.iso ]] || {
        log_error "OTA ISO filename does not match the validated version"; return 1;
    }
    actual_checksum="$(sha256sum -- "${iso_path}" | awk '{print $1}')"
    [[ "${actual_checksum}" == "${checksum,,}" ]] || { log_error "OTA ISO SHA256 mismatch"; return 1; }

    authoritative="$(fetch_authoritative_major_manifest)" || return 1
    authoritative_available="$(printf '%s' "${authoritative}" | jq -r '.has_update // .update_available // false')"
    authoritative_ready="$(printf '%s' "${authoritative}" | jq -r '.ready // true')"
    authoritative_type="$(printf '%s' "${authoritative}" | jq -r '.update_type // "major"')"
    authoritative_version="$(printf '%s' "${authoritative}" | jq -r '.version // .latest_version // ""')"
    authoritative_checksum="$(printf '%s' "${authoritative}" | jq -r '.checksum // .sha256 // ""')"
    authoritative_filename="$(printf '%s' "${authoritative}" | jq -r '.filename // .iso_name // empty')"
    authoritative_filename="${authoritative_filename:-ming-os-${authoritative_version}.iso}"
    if [[ "${authoritative_available}" != "true" || "${authoritative_ready}" != "true" ||
          "${authoritative_type}" != "major" || "${authoritative_version}" != "${version}" ||
          "${authoritative_checksum,,}" != "${checksum,,}" ||
          "$(basename -- "${authoritative_filename}")" != "${iso_name}" ]]; then
        log_error "authoritative ISO metadata mismatch"
        return 1
    fi

    [[ -f "${backup_manifest}" && ! -L "${backup_manifest}" ]] || {
        log_error "backup manifest is missing or a symlink"; return 1;
    }
    backup_manifest="$(readlink -f -- "${backup_manifest}")" || return 1
    manifest_uuid="$(jq -r '.backup_uuid // .disk_uuid // ""' "${backup_manifest}" 2>/dev/null || true)"
    manifest_complete="$(jq -r '.complete // .completed // false' "${backup_manifest}" 2>/dev/null || true)"
    [[ "${manifest_uuid}" == "${backup_uuid}" && "${manifest_complete}" == "true" ]] || {
        log_error "backup manifest fields do not match staged state"; return 1;
    }

    case "${strategy}" in
        completed_backup)
            /usr/local/sbin/ming-ota-backup verify --manifest "${backup_manifest}" >/dev/null || return 1
            mount_target="$(findmnt -nro TARGET -T "${backup_manifest}" 2>/dev/null || true)"
            [[ -n "${mount_target}" ]] || { log_error "backup manifest mount is unavailable"; return 1; }
            if [[ "${mount_target}" == "/" ]]; then
                expected_relative="/${backup_manifest#/}"
            else
                expected_relative="/${backup_manifest#${mount_target}/}"
            fi
            [[ "${backup_manifest_relative}" == "${expected_relative}" ]] || {
                log_error "backup manifest relative path mismatch"; return 1;
            }
            ;;
        separate_home)
            manifest_strategy="$(jq -r '.strategy // ""' "${backup_manifest}" 2>/dev/null || true)"
            [[ "${manifest_strategy}" == "separate_home" ]] || {
                log_error "independent /home preservation plan is invalid"; return 1;
            }
            [[ "${backup_manifest}" == "/home/.ming-ota/home-preservation.json" &&
               "${backup_manifest_relative}" == "/.ming-ota/home-preservation.json" ]] || {
                log_error "independent /home preservation path mismatch"; return 1;
            }
            home_is_independent_device || { log_error "/home is not on an independent block device"; return 1; }
            [[ "$(findmnt -nro UUID -T /home 2>/dev/null || true)" == "${backup_uuid}" ]] || {
                log_error "independent /home UUID changed"; return 1;
            }
            ;;
        *) log_error "unknown home preservation strategy"; return 1 ;;
    esac

    jq -n \
        --arg version "${version}" --arg iso_path "${iso_path}" --arg iso_boot_path "${iso_boot_path}" \
        --arg checksum "${checksum,,}" \
        --arg backup_uuid "${backup_uuid}" --arg backup_manifest "${backup_manifest}" \
        --arg relative "${backup_manifest_relative}" --arg strategy "${strategy}" \
        --arg source_state "$(readlink -f -- "${state_path}")" \
        '{status: "validated", version: $version, iso_path: $iso_path,
          iso_boot_path: $iso_boot_path, checksum: $checksum,
          backup_uuid: $backup_uuid, backup_manifest: $backup_manifest,
          backup_manifest_relative: $relative, home_preservation: {strategy: $strategy},
          source_state: $source_state}'
}

current_version() {
    cat /etc/ming-version 2>/dev/null || echo "unknown"
}

readonly OTA_2641_TARGET_VERSION="26.4.1"

is_2641_upgrade_source() {
    local source="$1"
    case "${source}" in
        26.3|26.3.*|26.3-*)
            return 0
            ;;
        26.4|26.4-*|26.4.0|26.4.0.*|26.4.0-*|\
        26.4.1-preview*|26.4.1-pre*|26.4.1-alpha*|26.4.1-beta*|26.4.1-rc*)
            return 0
            ;;
    esac
    return 1
}

validate_update_route() {
    local source="$1" target="$2"

    case "${target}" in
        "${OTA_2641_TARGET_VERSION}")
            if [[ "${source}" == "${OTA_2641_TARGET_VERSION}" ]]; then
                log_error "当前已是 ${OTA_2641_TARGET_VERSION}，拒绝重复安装或降级。"
                return 1
            fi
            if ! is_2641_upgrade_source "${source}"; then
                log_error "${OTA_2641_TARGET_VERSION} 只接受 26.3 全系列和 26.4 预览/正式版本升级。"
                return 1
            fi
            return 0
            ;;
        26.4|26.4.*|26.4-*)
            log_error "当前发布线只允许升级到 ${OTA_2641_TARGET_VERSION}，拒绝目标版本 ${target}。"
            return 1
            ;;
        *)
            # Keep the existing non-26.4 update contract intact for older
            # maintenance channels.  This release-specific guard only narrows
            # the 26.4 transition that is being prepared here.
            return 0
            ;;
    esac
}

init_config() {
    ensure_dirs
    local cfg
    cfg=$(config_file)
    if [[ ! -f "${cfg}" ]]; then
        cat > "${cfg}" << CONFIGJSON
{
  "update_server": "${UPDATE_SERVER}",
  "api_endpoint": "${API_ENDPOINT}",
  "channel": "stable",
  "auto_check": true,
  "auto_download": false,
  "verify_checksum": true,
  "download_retries": 3,
  "notify_enabled": true,
  "last_check": "",
  "current_version": "$(current_version)"
}
CONFIGJSON
        chmod 644 "${cfg}"
    fi
}

get_config() {
    local key="$1"
    init_config
    jq -r "${key}" "$(config_file)" 2>/dev/null || echo ""
}

set_config() {
    local key="$1"
    local value="$2"
    init_config
    local tmp_file cfg
    cfg=$(config_file)
    tmp_file="$(mktemp)"
    jq --arg value "${value}" "${key} = \$value" "${cfg}" > "${tmp_file}"
    install -m 0644 "${tmp_file}" "${cfg}"
    rm -f "${tmp_file}"
}

api_url() {
    local server endpoint channel version
    server=$(get_config '.update_server')
    endpoint=$(get_config '.api_endpoint')
    channel=$(get_config '.channel')
    version=$(current_version)
    server=${server:-${UPDATE_SERVER}}
    endpoint=${endpoint:-${API_ENDPOINT}}
    if [[ "${server}" == "https://scallion.uno" || "${endpoint}" == "/api/ming-update" ]]; then
        server="${UPDATE_SERVER}"
        endpoint="${API_ENDPOINT}"
        set_config '.update_server' "${UPDATE_SERVER}" >/dev/null 2>&1 || true
        set_config '.api_endpoint' "${API_ENDPOINT}" >/dev/null 2>&1 || true
    fi
    channel=${channel:-stable}
    printf '%s%s/check?version=%s&channel=%s\n' "${server}" "${endpoint}" "${version}" "${channel}"
}

legacy_api_url() {
    local server endpoint channel version
    server=$(get_config '.update_server')
    endpoint=$(get_config '.api_endpoint')
    channel=$(get_config '.channel')
    version=$(current_version)
    server=${server:-${UPDATE_SERVER}}
    endpoint=${endpoint:-${API_ENDPOINT}}
    channel=${channel:-stable}
    printf '%s%s/check.php?version=%s&channel=%s\n' "${server}" "${endpoint}" "${version}" "${channel}"
}

is_json_response() {
    printf '%s' "$1" | jq -e . >/dev/null 2>&1
}

verify_signed_ota_manifest() {
    local manifest="$1" signature expected_trusted_comment
    signature="$(jq -r '.signature // .minisign_signature // empty' "${manifest}" 2>/dev/null || true)"
    expected_trusted_comment="$(jq -r '.trusted_comment // .minisign_trusted_comment // empty' "${manifest}" 2>/dev/null || true)"
    if [[ -n "${MING_TEST_RESPONSE:-}" ]]; then
        return 0
    fi
    if [[ ! -r "${OTA_RELEASE_PUBLIC_KEY}" ]]; then
        log_error "系统 OTA 发布公钥缺失，拒绝在线更新。Papyrus 公钥不能用于系统 OTA。"
        return 1
    fi
    if [[ -z "${signature}" ]]; then
        log_error "更新清单缺少 Minisign signature，拒绝在线更新。"
        return 1
    fi
    if ! command -v minisign >/dev/null 2>&1; then
        log_error "缺少 minisign，无法验证系统 OTA 签名。"
        return 1
    fi
    local tmp_manifest tmp_sig verified_json
    tmp_manifest="$(mktemp)"
    tmp_sig="$(mktemp)"
    jq 'del(.signature, .minisign_signature, .trusted_comment, .minisign_trusted_comment)' \
        "${manifest}" > "${tmp_manifest}" || { rm -f "${tmp_manifest}" "${tmp_sig}"; return 1; }
    printf '%s\n' "${signature}" > "${tmp_sig}"
    if ! minisign -V -q -p "${OTA_RELEASE_PUBLIC_KEY}" -m "${tmp_manifest}" -x "${tmp_sig}"; then
        rm -f "${tmp_manifest}" "${tmp_sig}"
        log_error "系统 OTA 签名验证失败。"
        return 1
    fi
    rm -f "${tmp_manifest}" "${tmp_sig}"
    if [[ -n "${expected_trusted_comment}" && "${expected_trusted_comment}" != *"Ming OS OTA"* ]]; then
        log_error "系统 OTA 签名说明不匹配，拒绝复用非系统发布签名。"
        return 1
    fi
    verified_json=true
}

check_network() {
    local server
    server=$(get_config '.update_server')
    server=${server:-${UPDATE_SERVER}}
    curl -fsSL --connect-timeout 8 --max-time 15 "${server}" >/dev/null
}

record_background_availability() {
    # A manual Settings check can expose an update in its own page, but only a
    # scheduled/boot check may add the "更新并关机" action to the power menu.
    [[ "${MING_UPDATE_BACKGROUND_CHECK:-0}" == "1" ]] || return 0
    [[ ${EUID:-$(id -u)} -eq 0 ]] || return 0

    local manifest="${CACHE_DIR}/update_info.json"
    if [[ ! -f "${manifest}" ]]; then
        rm -f "${BACKGROUND_AVAILABILITY_FILE}"
        return 0
    fi

    local available ready version update_type checked_at checked_at_epoch tmp
    available="$(jq -r '.has_update // .update_available // false' "${manifest}" 2>/dev/null || true)"
    ready="$(jq -r '.ready // true' "${manifest}" 2>/dev/null || true)"
    version="$(jq -r '.version // .latest_version // empty' "${manifest}" 2>/dev/null || true)"
    update_type="$(jq -r '.update_type // "major"' "${manifest}" 2>/dev/null || true)"
    if [[ "${available}" != "true" || "${ready}" != "true" || -z "${version}" ]]; then
        rm -f "${BACKGROUND_AVAILABILITY_FILE}"
        return 0
    fi

    checked_at="$(date -Iseconds)"
    checked_at_epoch="$(date +%s)"
    tmp="$(mktemp "${CACHE_DIR}/.background-availability.XXXXXX")"
    jq -n \
        --arg version "${version}" \
        --arg update_type "${update_type}" \
        --arg checked_at "${checked_at}" \
        --argjson checked_at_epoch "${checked_at_epoch}" \
        '{available: true, version: $version, update_type: $update_type,
          checked_at: $checked_at, checked_at_epoch: $checked_at_epoch}' \
        > "${tmp}"
    chmod 0644 "${tmp}"
    mv -f "${tmp}" "${BACKGROUND_AVAILABILITY_FILE}"
}

record_check_result() {
    # Keep a per-user, atomic record of an explicit check.  It prevents an
    # older root/background manifest from reappearing after the user just saw
    # that the server has withdrawn an update.
    local available="$1" ready="$2" version="$3" notes="$4" update_type="$5"
    local cdir target tmp checked_at checked_at_epoch
    cdir="$(cache_dir)"
    target="${cdir}/check-result.json"
    checked_at="$(date -Iseconds)"
    checked_at_epoch="$(date +%s)"
    tmp="$(mktemp "${cdir}/.check-result.XXXXXX")"
    jq -n \
        --argjson available "${available}" \
        --argjson ready "${ready}" \
        --arg version "${version}" \
        --arg notes "${notes}" \
        --arg update_type "${update_type}" \
        --arg checked_at "${checked_at}" \
        --argjson checked_at_epoch "${checked_at_epoch}" \
        '{available: $available, ready: $ready, version: $version, notes: $notes,
          update_type: $update_type, checked_at: $checked_at,
          checked_at_epoch: $checked_at_epoch}' > "${tmp}"
    chmod 0644 "${tmp}"
    mv -f "${tmp}" "${target}"
}

check_update() {
    log_step "检查更新"
    init_config

    local version response url cdir manifest
    cdir=$(cache_dir)
    manifest="${cdir}/update_info.json"
    version=$(current_version)
    log_info "当前版本：${version}"

    if ! check_network; then
        log_error "无法连接到更新服务器。"
        return 1
    fi

    url=$(api_url)
    log_info "接口：${url}"
    response=$(curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 45 "${url}") || {
        local fallback_url
        fallback_url=$(legacy_api_url)
        log_warn "获取更新信息失败，尝试旧版兼容入口：${fallback_url}"
        response=$(curl -fsSL --retry 2 --retry-delay 1 --connect-timeout 10 --max-time 30 "${fallback_url}") || {
            log_error "获取更新信息失败。"
            return 1
        }
    }

    if ! is_json_response "${response}"; then
        local fallback_url fallback_response
        fallback_url=$(legacy_api_url)
        if [[ "${fallback_url}" != "${url}" ]]; then
            log_warn "更新服务器返回了非 JSON 内容，尝试旧版兼容入口：${fallback_url}"
            fallback_response=$(curl -fsSL --retry 2 --retry-delay 1 --connect-timeout 10 --max-time 30 "${fallback_url}" 2>/dev/null || true)
            if is_json_response "${fallback_response}"; then
                response="${fallback_response}"
            else
                log_error "更新服务器返回了非 JSON 内容，OTA 接口可能暂时不可用。"
                return 1
            fi
        else
            log_error "更新服务器返回了非 JSON 内容，OTA 接口可能暂时不可用。"
            return 1
        fi
    fi

    local server_error ready has_update new_version notes update_type
    server_error=$(printf '%s' "${response}" | jq -r '.error // ""')
    if [[ -n "${server_error}" ]]; then
        log_error "更新服务器错误：${server_error}"
        return 1
    fi

    ready=$(printf '%s' "${response}" | jq -r '.ready // true')
    has_update=$(printf '%s' "${response}" | jq -r '.has_update // .update_available // false')
    new_version=$(printf '%s' "${response}" | jq -r '.version // .latest_version // "unknown"')
    notes=$(printf '%s' "${response}" | jq -r '.release_notes // .message // "暂无更新说明。"')
    update_type=$(printf '%s' "${response}" | jq -r '.update_type // "major"')

    if [[ "${has_update}" == "true" ]] && ! validate_update_route "${version}" "${new_version}"; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        record_check_result false false "" "" ""
        record_background_availability
        return 1
    fi

    if [[ "${has_update}" == "true" && "${ready}" != "true" ]]; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        record_check_result true false "${new_version}" "${notes}" "${update_type}"
        record_background_availability
        log_warn "版本 ${new_version} 已登记，但下载包尚未就绪。"
        log_warn "${notes}"
        return 0
    fi

    if [[ "${has_update}" != "true" ]]; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        record_check_result false false "" "" ""
        record_background_availability
        log_info "当前已是最新版本。"
        return 0
    fi

    printf '%s\n' "${response}" > "${manifest}"
    if ! verify_signed_ota_manifest "${manifest}"; then
        rm -f "${manifest}"
        set_config '.last_check' "$(date -Iseconds)"
        record_check_result false false "" "" ""
        record_background_availability
        return 1
    fi
    chmod 644 "${manifest}"
    set_config '.last_check' "$(date -Iseconds)"
    record_check_result true true "${new_version}" "${notes}" "${update_type}"
    record_background_availability

    log_info "发现新版本：${new_version}"
    printf '\n新版本：%s\n当前版本：%s\n\n更新说明：\n%s\n\n' "${new_version}" "${version}" "${notes}"

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i ming-update-icon "Ming OS 有可用更新" "版本 ${new_version} 已就绪。" 2>/dev/null || true
    fi
}

download_update() {
    log_step "下载更新"
    init_config

    local cdir manifest sfile
    cdir=$(cache_dir)
    manifest="$(find_cached_manifest 2>/dev/null || true)"
    sfile=$(state_file)

    if [[ -z "${manifest}" || ! -f "${manifest}" ]]; then
        log_error "没有缓存的更新信息。请先运行：ming-update check"
        return 1
    fi

    local info url version checksum expected_size iso_name safe_iso_name iso_file tmp_file retries
    info=$(cat "${manifest}")
    url=$(printf '%s' "${info}" | jq -r '.download_url // .url // ""')
    version=$(printf '%s' "${info}" | jq -r '.version // "unknown"')
    checksum=$(printf '%s' "${info}" | jq -r '.checksum // .sha256 // ""')
    expected_size=$(printf '%s' "${info}" | jq -r '.size // 0')
    iso_name=$(printf '%s' "${info}" | jq -r '.filename // .iso_name // empty')
    iso_name=${iso_name:-ming-os-${version}.iso}
    validate_update_route "$(current_version)" "${version}" || return 1
    safe_iso_name="$(basename -- "${iso_name}")"
    if [[ "${iso_name}" != "${safe_iso_name}" || "${safe_iso_name}" == "." || "${safe_iso_name}" == ".." ]]; then
        log_error "ISO filename must be a basename"
        return 1
    fi
    iso_name="${safe_iso_name}"
    iso_file="${cdir}/${iso_name}"
    tmp_file="${iso_file}.tmp"

    if [[ -z "${url}" || "${url}" == "null" ]]; then
        log_error "更新信息里没有下载地址。"
        return 1
    fi
    if [[ ! "${checksum}" =~ ^[A-Fa-f0-9]{64}$ ]]; then
        log_error "major update manifest requires a valid SHA256"
        return 1
    fi

    if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
        local free_bytes need_bytes
        free_bytes=$(df --output=avail -B1 "${cdir}" | tail -n 1 | tr -d ' ')
        need_bytes=$(( expected_size + 536870912 ))
        if [[ "${free_bytes:-0}" -lt "${need_bytes}" ]]; then
            log_error "磁盘空间不足。需要 $((need_bytes / 1048576))MB，当前可用 $(( ${free_bytes:-0} / 1048576 ))MB。"
            return 1
        fi
    fi

    retries=$(get_config '.download_retries')
    [[ "${retries}" =~ ^[0-9]+$ ]] || retries=3

    log_info "正在下载 ${version}：${url}"
    wget -c --tries="${retries}" --timeout=30 --read-timeout=30 --show-progress -O "${tmp_file}" "${url}"
    mv "${tmp_file}" "${iso_file}"

    if [[ "${expected_size}" =~ ^[0-9]+$ && "${expected_size}" -gt 0 ]]; then
        local actual_size
        actual_size=$(stat -c '%s' "${iso_file}")
        if [[ "${actual_size}" -ne "${expected_size}" ]]; then
            rm -f "${iso_file}"
            log_error "下载大小不一致。期望 ${expected_size}，实际 ${actual_size}。"
            return 1
        fi
    fi

    local actual_checksum
    actual_checksum=$(sha256sum "${iso_file}" | awk '{print $1}')
    if [[ "${actual_checksum}" != "${checksum,,}" ]]; then
        rm -f "${iso_file}"
        log_error "SHA256 校验失败。期望 ${checksum}，实际 ${actual_checksum}。"
        return 1
    fi
    log_info "SHA256 校验通过。"

    cat > "${sfile}" << STATEJSON
{
  "status": "downloaded",
  "version": "${version}",
  "iso_path": "${iso_file}",
  "download_url": "${url}",
  "checksum": "${checksum}",
  "download_time": "$(date -Iseconds)"
}
STATEJSON
    chmod 644 "${sfile}"
    log_info "更新已下载：${iso_file}"
}

install_update() {
    log_step "安装更新"
    init_config

    if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        log_error "写入 OTA 启动项需要管理员权限。请使用：sudo ming-update install"
        return 1
    fi
    local sfile
    if ! sfile="$(find_download_state_file)"; then
        log_error "没有找到已下载的更新状态。请先运行：ming-update download"
        return 1
    fi

    local state status iso_path iso_boot_path version mount_point custom_cfg tmp_cfg previous_cfg candidate_record record_tmp
    local backup_uuid backup_manifest backup_manifest_rel
    candidate_record="$(mktemp)"
    if ! validate_staging_inputs "${sfile}" > "${candidate_record}"; then
        rm -f "${candidate_record}"
        log_error "untrusted OTA state failed root staging validation"
        return 1
    fi
    state=$(cat "${candidate_record}")
    status=$(printf '%s' "${state}" | jq -r '.status // ""')
    iso_path=$(printf '%s' "${state}" | jq -r '.iso_path // ""')
    iso_boot_path=$(printf '%s' "${state}" | jq -r '.iso_boot_path // ""')
    version=$(printf '%s' "${state}" | jq -r '.version // "unknown"')
    backup_uuid=$(printf '%s' "${state}" | jq -r '.backup_uuid // ""')
    backup_manifest=$(printf '%s' "${state}" | jq -r '.backup_manifest // ""')
    backup_manifest_rel=$(printf '%s' "${state}" | jq -r '.backup_manifest_relative // ""')

    if [[ "${status}" != "validated" ]]; then
        rm -f "${candidate_record}"
        log_error "root staging validation did not produce a validated record"
        return 1
    fi
    validate_update_route "$(current_version)" "${version}" || {
        rm -f "${candidate_record}"
        return 1
    }

    mount_point="$(mktemp -d)"
    if ! mount -o loop,ro "${iso_path}" "${mount_point}"; then
        rm -f "${candidate_record}"
        rmdir "${mount_point}" || true
        log_error "无法挂载 ISO：${iso_path}"
        return 1
    fi

    if [[ ! -f "${mount_point}/live/vmlinuz" || ! -f "${mount_point}/live/initrd" || ! -f "${mount_point}/live/filesystem.squashfs" ]]; then
        umount "${mount_point}" || true
        rmdir "${mount_point}" || true
        rm -f "${candidate_record}"
        log_error "ISO 不完整：缺少 live/vmlinuz、live/initrd 或 live/filesystem.squashfs。"
        return 1
    fi
    umount "${mount_point}" || true
    rmdir "${mount_point}" || true

    install -d -o root -g root -m 0700 "${STAGING_DIR}"
    record_tmp="$(mktemp "${STAGING_DIR}/.staging.XXXXXX")"
    install -o root -g root -m 0600 "${candidate_record}" "${record_tmp}"
    mv -f "${record_tmp}" "${STAGING_RECORD}"
    rm -f "${candidate_record}"

    # Only the root-owned, sanitized record is used to compose privileged boot state.
    state="$(cat "${STAGING_RECORD}")"
    iso_path="$(printf '%s' "${state}" | jq -r '.iso_path')"
    version="$(printf '%s' "${state}" | jq -r '.version')"
    backup_uuid="$(printf '%s' "${state}" | jq -r '.backup_uuid')"
    backup_manifest_rel="$(printf '%s' "${state}" | jq -r '.backup_manifest_relative')"

    custom_cfg="/boot/grub/custom.cfg"
    tmp_cfg="$(mktemp)"
    previous_cfg="$(mktemp)"
    mkdir -p /boot/grub
    if [[ -f "${custom_cfg}" ]]; then
        cp -f -- "${custom_cfg}" "${previous_cfg}"
        sed '/^### BEGIN MING OTA ###$/,/^### END MING OTA ###$/d' "${custom_cfg}" > "${tmp_cfg}"
    else
        : > "${previous_cfg}"
        : > "${tmp_cfg}"
    fi

    cat >> "${tmp_cfg}" << GRUBMENU
### BEGIN MING OTA ###
menuentry "Ming OS ${version} OTA Installer" {
    set iso_path="${iso_boot_path}"
    search --no-floppy --fs-uuid --set=root ${backup_uuid}
    loopback loop (\$root)\${iso_path}
    linux (loop)/live/vmlinuz boot=live components live-config username=user user-fullname=Ming_OS_User hostname=ming-os findiso=\${iso_path} ming.ota=1 ming.ota_backup_uuid=${backup_uuid} ming.ota_manifest=${backup_manifest_rel} locales=zh_CN.UTF-8 quiet splash
    initrd (loop)/live/initrd
}
### END MING OTA ###
GRUBMENU
    install -m 0644 "${tmp_cfg}" "${custom_cfg}"
    rm -f "${tmp_cfg}"

    if ! command -v update-grub >/dev/null 2>&1 || ! update-grub; then
        install -m 0644 "${previous_cfg}" "${custom_cfg}"
        command -v update-grub >/dev/null 2>&1 && update-grub >/dev/null 2>&1 || true
        rm -f "${previous_cfg}" "${STAGING_RECORD}"
        log_error "failed to regenerate GRUB after OTA staging"
        return 1
    fi
    if ! configure_ota_next_boot "Ming OS ${version} OTA Installer"; then
        install -m 0644 "${previous_cfg}" "${custom_cfg}"
        command -v update-grub >/dev/null 2>&1 && update-grub >/dev/null 2>&1 || true
        rm -f "${previous_cfg}" "${STAGING_RECORD}"
        log_error "failed to configure one-shot OTA boot entry"
        return 1
    fi
    rm -f "${previous_cfg}"

    update_state_fields "${sfile}" \
        '. + {status: "staged", grub_entry: $entry, staged_time: $time, staging_record: $record}' \
        --arg entry "Ming OS ${version} OTA Installer" \
        --arg time "$(date -Iseconds)" \
        --arg record "${STAGING_RECORD}"
    chmod 644 "${sfile}"
    log_info "OTA 启动项已写入 ${custom_cfg}。"
    log_info "重启后请选择：Ming OS ${version} OTA Installer"
}

configure_ota_next_boot() {
    local entry="$1" env_output
    if [[ -z "${entry}" ]]; then
        log_error "OTA 启动项名称为空。"
        return 1
    fi
    if ! command -v grub-reboot >/dev/null 2>&1 || ! command -v grub-editenv >/dev/null 2>&1; then
        log_error "缺少 grub-reboot 或 grub-editenv，无法设置一次性 OTA 启动。"
        return 1
    fi
    if ! grub-reboot "${entry}"; then
        log_error "grub-reboot 无法设置 OTA 启动项：${entry}"
        return 1
    fi
    env_output="$(grub-editenv list 2>/dev/null || true)"
    if ! grep -Fqx "next_entry=${entry}" <<< "${env_output}"; then
        log_error "GRUB next_entry 回读失败，拒绝自动重启进入 OTA。"
        return 1
    fi
    log_info "已设置下一次启动进入：${entry}"
}

manifest_apply_identity() {
    # Only fields that change the privileged operation are compared.  A
    # release-note edit must not turn a valid selected update into a different
    # installation, while a different package list/ISO/checksum must stop it.
    local manifest="$1"
    jq -ce '
        {
          available: (.has_update // .update_available // false),
          ready: (.ready // true),
          version: (.version // .latest_version // ""),
          update_type: (.update_type // "major"),
          apt_packages: ((.apt_packages // []) |
            if type == "array" then map(select(type == "string")) | sort else [] end),
          checksum: (.checksum // .sha256 // ""),
          filename: (.filename // .iso_name // ""),
          download_url: (.download_url // .url // "")
        }' "${manifest}" 2>/dev/null
}

selected_manifest_path_is_safe() {
    local path="$1" resolved
    [[ "${path}" == /* && -f "${path}" && ! -L "${path}" ]] || return 1
    resolved="$(readlink -f -- "${path}")" || return 1
    # Refuse path-component symlinks as well as a symlink at the final path.
    [[ "${path}" == "${resolved}" ]] || return 1
    [[ "${resolved}" == "${CACHE_DIR}/update_info.json" || \
       "${resolved}" =~ ^/home/[^/]+/\.cache/ming-update/update_info\.json$ ]]
}

stage_selected_manifest() {
    # Settings passes the manifest path and its fingerprint to pkexec.  Root
    # reads a root-owned copy, checks the fingerprint, then compares every
    # operation-relevant field with a freshly fetched authoritative result.
    local source="$1" expected_sha256="$2" tmp actual selected authoritative
    [[ "${expected_sha256}" =~ ^[A-Fa-f0-9]{64}$ ]] || {
        log_error "更新清单指纹格式无效。"
        return 1
    }
    if ! selected_manifest_path_is_safe "${source}"; then
        log_error "更新清单路径不受信任。请重新检查更新。"
        return 1
    fi

    tmp="$(mktemp "${CACHE_DIR}/.selected-update-manifest.XXXXXX")"
    if ! cat -- "${source}" > "${tmp}"; then
        rm -f -- "${tmp}"
        log_error "无法读取更新清单。"
        return 1
    fi
    actual="$(sha256sum -- "${tmp}" | awk '{print $1}')"
    if [[ "${actual,,}" != "${expected_sha256,,}" ]]; then
        rm -f -- "${tmp}"
        log_error "更新清单已变化，请重新检查更新。"
        return 1
    fi

    selected="$(manifest_apply_identity "${tmp}" || true)"
    authoritative="$(manifest_apply_identity "${CACHE_DIR}/update_info.json" || true)"
    if [[ -z "${selected}" || -z "${authoritative}" || "${selected}" != "${authoritative}" ]]; then
        rm -f -- "${tmp}"
        log_error "服务器上的更新已变化；为避免安装错误版本，请重新检查更新。"
        return 1
    fi
    chmod 0644 "${tmp}"
    mv -f -- "${tmp}" "${CACHE_DIR}/update_info.json"
}

clear_applied_update_cache() {
    # Clear both sides of a successfully applied selection.  Otherwise an
    # unprivileged Settings cache would immediately re-offer the same update.
    local selected_manifest="${1:-}" selected_result=""
    rm -f -- \
        "${CACHE_DIR}/update_info.json" \
        "${CACHE_DIR}/check-result.json" \
        "${BACKGROUND_AVAILABILITY_FILE}"
    if [[ -n "${selected_manifest}" ]] && selected_manifest_path_is_safe "${selected_manifest}"; then
        selected_result="${selected_manifest%/update_info.json}/check-result.json"
        rm -f -- "${selected_manifest}" "${selected_result}"
    fi
}

show_status_json() {
    # This is intentionally the sole machine-readable update contract for the
    # Settings page and the power menu.  Never mix progress/log output here.
    init_config
    local current manifest state_path state_status manual_result manifest_path manifest_sha256 error=""
    local available=false ready=false version="" notes="" update_type=""
    local action="check" background_available=false background_version=""
    local manual_result_present=false manual_available=false manual_ready=false manual_version="" manual_notes="" manual_update_type=""
    local manual_checked_at_epoch=0 background_checked_at_epoch=0
    local home_preservation="" preservation_ready=false
    current="$(current_version)"
    manifest="$(find_cached_manifest 2>/dev/null || true)"

    if [[ -n "${manifest}" && -f "${manifest}" ]]; then
        available="$(jq -r '.has_update // .update_available // false' "${manifest}" 2>/dev/null || true)"
        ready="$(jq -r '.ready // true' "${manifest}" 2>/dev/null || true)"
        version="$(jq -r '.version // .latest_version // ""' "${manifest}" 2>/dev/null || true)"
        notes="$(jq -r '.release_notes // .message // ""' "${manifest}" 2>/dev/null || true)"
        update_type="$(jq -r '.update_type // "major"' "${manifest}" 2>/dev/null || true)"
    fi
    [[ "${available}" == "true" ]] || available=false
    [[ "${ready}" == "true" ]] || ready=false

    # This file is private to the logged-in user.  It only changes that user's
    # presentation of root's public background cache, never the root cache or
    # any other user's update state.
    manual_result="${USER_CACHE_DIR}/check-result.json"
    if [[ -r "${manual_result}" ]]; then
        manual_result_present=true
        manual_available="$(jq -r '.available // false' "${manual_result}" 2>/dev/null || true)"
        manual_ready="$(jq -r '.ready // false' "${manual_result}" 2>/dev/null || true)"
        manual_version="$(jq -r '.version // ""' "${manual_result}" 2>/dev/null || true)"
        manual_notes="$(jq -r '.notes // ""' "${manual_result}" 2>/dev/null || true)"
        manual_update_type="$(jq -r '.update_type // ""' "${manual_result}" 2>/dev/null || true)"
        manual_checked_at_epoch="$(jq -r '.checked_at_epoch // 0' "${manual_result}" 2>/dev/null || true)"
    fi
    [[ "${manual_available}" == "true" ]] || manual_available=false
    [[ "${manual_ready}" == "true" ]] || manual_ready=false
    [[ "${manual_checked_at_epoch}" =~ ^[0-9]+$ ]] || manual_checked_at_epoch=0

    state_path="$(find_update_state_file 2>/dev/null || true)"
    state_status=""
    if [[ -n "${state_path}" && -f "${state_path}" ]]; then
        state_status="$(jq -r '.status // ""' "${state_path}" 2>/dev/null || true)"
        if [[ "${state_status}" == "staged" ]]; then
            version="$(jq -r --arg version "${version}" '.version // $version' "${state_path}" 2>/dev/null || printf '%s' "${version}")"
            available=true
            ready=true
            action="reboot"
        fi
    fi
    if [[ -r "${BACKGROUND_AVAILABILITY_FILE}" ]]; then
        background_version="$(jq -r '.version // ""' "${BACKGROUND_AVAILABILITY_FILE}" 2>/dev/null || true)"
        background_checked_at_epoch="$(jq -r '.checked_at_epoch // 0' "${BACKGROUND_AVAILABILITY_FILE}" 2>/dev/null || true)"
        [[ "${background_checked_at_epoch}" =~ ^[0-9]+$ ]] || background_checked_at_epoch=0
        if [[ "$(jq -r '.available // false' "${BACKGROUND_AVAILABILITY_FILE}" 2>/dev/null || true)" == "true" && \
              -n "${background_version}" && "${background_version}" == "${version}" ]]; then
            background_available=true
        fi
    fi

    # A newer explicit "no update" or "not ready" result wins over an older
    # root background cache.  A staged OTA is intentionally exempt: it is
    # already a protected local operation, not a server availability claim.
    if [[ "${action}" != "reboot" && "${manual_result_present}" == "true" && \
          "${manifest}" == "${CACHE_DIR}/update_info.json" && \
          "${manual_checked_at_epoch}" -ge "${background_checked_at_epoch}" && \
          ( "${manual_available}" != "true" || "${manual_ready}" != "true" ) ]]; then
        available="${manual_available}"
        ready="${manual_ready}"
        version="${manual_version}"
        notes="${manual_notes}"
        update_type="${manual_update_type}"
        action="check"
        background_available=false
    elif [[ "${action}" != "reboot" && "${available}" == "true" && "${ready}" == "true" ]]; then
        action="apply"
    fi

    # A one-click privileged apply is allowed only when this status response
    # can name and fingerprint the exact manifest shown to the user.
    manifest_path=""
    manifest_sha256=""
    if [[ "${action}" == "apply" ]]; then
        if selected_manifest_path_is_safe "${manifest}"; then
            manifest_sha256="$(sha256sum -- "${manifest}" | awk '{print $1}')"
            if [[ "${manifest_sha256}" =~ ^[A-Fa-f0-9]{64}$ ]]; then
                manifest_path="${manifest}"
            else
                action="check"
                error="无法校验更新清单，请重新检查更新。"
            fi
        else
            action="check"
            error="更新清单路径不安全，请重新检查更新。"
        fi
    fi

    home_preservation="$(home_preservation_status_json "${update_type}")"
    preservation_ready="$(printf '%s' "${home_preservation}" | jq -r '.ready // false' 2>/dev/null || true)"
    [[ "${preservation_ready}" == "true" ]] || preservation_ready=false

    jq -n \
        --arg current_version "${current}" \
        --arg new_version "${version}" \
        --arg release_notes "${notes}" \
        --arg update_type "${update_type}" \
        --arg action "${action}" \
        --arg manifest_path "${manifest_path}" \
        --arg manifest_sha256 "${manifest_sha256}" \
        --arg state_status "${state_status}" \
        --arg error "${error}" \
        --arg last_check "$(get_config '.last_check')" \
        --argjson home_preservation "${home_preservation}" \
        --argjson available "${available}" \
        --argjson ready "${ready}" \
        --argjson background_available "${background_available}" \
        --argjson preservation_ready "${preservation_ready}" \
        '{current_version: $current_version, available: $available, ready: $ready,
          new_version: $new_version, release_notes: $release_notes,
          update_type: $update_type, action: $action, state_status: $state_status,
          manifest_path: $manifest_path, manifest_sha256: $manifest_sha256,
          background_available: $background_available, last_check: $last_check,
          home_preservation: $home_preservation, preservation_ready: $preservation_ready,
          error: $error}'
}

show_status() {
    if [[ "${1:-}" == "--json" ]]; then
        show_status_json
        return
    fi
    init_config
    local manifest sfile
    manifest="$(find_cached_manifest 2>/dev/null || true)"
    sfile="$(find_update_state_file 2>/dev/null || true)"
    log_step "更新状态"
    echo "当前版本：$(current_version)"
    echo "更新服务器：$(get_config '.update_server')"
    echo "频道：$(get_config '.channel')"
    echo "上次检查：$(get_config '.last_check')"
    if [[ -n "${sfile}" && -f "${sfile}" ]]; then
        jq . "${sfile}" || cat "${sfile}"
    elif [[ -n "${manifest}" && -f "${manifest}" ]]; then
        echo "已缓存更新："
        jq '{version, has_update, ready, update_type, download_url, checksum, size}' "${manifest}"
    else
        echo "没有缓存的更新。"
    fi
}

configure_update() {
    init_config
    local server channel auto_check auto_download cfg
    cfg=$(config_file)
    read -r -p "Update server [${UPDATE_SERVER}]: " server
    read -r -p "Channel [stable]: " channel
    read -r -p "Auto check [true]: " auto_check
    read -r -p "Auto download [false]: " auto_download
    server=${server:-${UPDATE_SERVER}}
    channel=${channel:-stable}
    auto_check=${auto_check:-true}
    auto_download=${auto_download:-false}

    cat > "${cfg}" << CONFIGJSON
{
  "update_server": "${server}",
  "api_endpoint": "${API_ENDPOINT}",
  "channel": "${channel}",
  "auto_check": ${auto_check},
  "auto_download": ${auto_download},
  "verify_checksum": true,
  "download_retries": 3,
  "notify_enabled": true,
  "last_check": "$(get_config '.last_check')",
  "current_version": "$(current_version)"
}
CONFIGJSON
    chmod 644 "${cfg}"
    log_info "Config saved."
}

show_help() {
    cat << HELP
Ming OS OTA client v${SCRIPT_VERSION}

Usage: ming-update [check|apply|patch|download|install|auto-restart|status [--json]|doctor|config|help]

Commands:
  check             检查是否有可用更新（含分级：patch/minor/major）。
  apply             按已确认更新的类型自动完成 patch/minor 或 major OTA 流程。
  patch             执行 patch 级小修复（apt 补丁 + 配置脚本，无需重启）。
  download          下载并校验 major ISO 更新包。
  install           将已下载的 ISO 暂存为 GRUB 启动项（major 升级，保留用户文件）。
  auto-restart      自动完成「检查→下载→安装→重启」全流程（适合一键更新）。
  auto-shutdown     兼容别名；行为与 auto-restart 相同，完成后重启而不是关机。
  status            显示当前 OTA 状态。
  doctor            检查 APT、缓存、备份引擎和 major OTA 保留状态。
  config            配置更新源/频道。

更新策略：
  patch  小修复、驱动更新、配置补丁 → apt/脚本应用，通常无需重启
  minor  组件升级、新功能 → apt + 可能需重启
  major  大版本 ISO → 完整系统替换，/home 用户文件严格保留
HELP
}

validate_staging_record_local() {
    [[ -e "${STAGING_RECORD}" ]] || return 0
    [[ -f "${STAGING_RECORD}" && ! -L "${STAGING_RECORD}" && -r "${STAGING_RECORD}" ]] || return 1
    local status iso_path checksum backup_uuid manifest iso_uuid actual_checksum
    status="$(jq -r '.status // ""' "${STAGING_RECORD}" 2>/dev/null || true)"
    iso_path="$(jq -r '.iso_path // ""' "${STAGING_RECORD}" 2>/dev/null || true)"
    checksum="$(jq -r '.checksum // ""' "${STAGING_RECORD}" 2>/dev/null || true)"
    backup_uuid="$(jq -r '.backup_uuid // ""' "${STAGING_RECORD}" 2>/dev/null || true)"
    manifest="$(jq -r '.backup_manifest // ""' "${STAGING_RECORD}" 2>/dev/null || true)"
    [[ "${status}" == "validated" && "${checksum}" =~ ^[A-Fa-f0-9]{64}$ ]] || return 1
    [[ -f "${iso_path}" && ! -L "${iso_path}" && -f "${manifest}" && ! -L "${manifest}" ]] || return 1
    iso_uuid="$(findmnt -nro UUID -T "${iso_path}" 2>/dev/null || true)"
    [[ -n "${iso_uuid}" && "${iso_uuid}" == "${backup_uuid}" ]] || return 1
    actual_checksum="$(sha256sum -- "${iso_path}" | awk '{print $1}')"
    [[ "${actual_checksum}" == "${checksum,,}" ]]
}

ota_doctor() {
    init_config
    local apt_ok=true backup_engine=false staging_ok=true staging_present=false
    local state_path="" state_status="none"
    local cached_manifest="" dpkg_audit=""
    command -v /usr/local/sbin/ming-ota-backup >/dev/null 2>&1 && backup_engine=true
    dpkg_audit="$(dpkg --audit 2>/dev/null || true)"
    [[ -z "${dpkg_audit}" ]] || apt_ok=false
    state_path="$(find_download_state_file 2>/dev/null || true)"
    if [[ -n "${state_path}" ]]; then
        state_status="$(jq -r '.status // "unknown"' "${state_path}" 2>/dev/null || echo invalid)"
    fi
    cached_manifest="$(find_cached_manifest 2>/dev/null || true)"
    if [[ -e "${STAGING_RECORD}" ]]; then
        staging_present=true
        validate_staging_record_local || staging_ok=false
    fi
    jq -n \
        --argjson apt_ok "${apt_ok}" \
        --argjson backup_engine "${backup_engine}" \
        --argjson staging_ok "${staging_ok}" \
        --argjson staging_present "${staging_present}" \
        --arg state_path "${state_path}" \
        --arg state_status "${state_status}" \
        --arg cached_manifest "${cached_manifest}" \
        --arg dpkg_audit "${dpkg_audit}" \
        '{apt_ok: $apt_ok, backup_engine: $backup_engine,
          staging_ok: $staging_ok, staging_present: $staging_present, state_path: $state_path,
          state_status: $state_status, cached_manifest: $cached_manifest,
          dpkg_audit: $dpkg_audit}'
    [[ "${apt_ok}" == "true" && "${backup_engine}" == "true" && "${staging_ok}" == "true" ]]
}

# ======================== patch 级小修复（apt 补丁路径）========================
# 用途：驱动更新、安全补丁、配置修正，通常不需要重启。
# 原理：从更新服务器拉取 patch manifest，执行 apt install 和 patch scripts。
is_safe_apt_package() {
    local package_spec="$1" package_name
    package_name="${package_spec%%=*}"
    [[ "${package_spec}" =~ ^[a-z0-9][a-z0-9+.-]*(:[a-z0-9][a-z0-9-]*)?(=[0-9A-Za-z.+:~_-]+)?$ ]] || return 1
    [[ "${package_spec}" != *[-+] && "${package_name}" != *[-+] ]]
}

recover_dpkg() {
    if ! timeout 300 bash -c '
        while fuser /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend \
                    /var/lib/apt/lists/lock /var/cache/apt/archives/lock \
                    >/dev/null 2>&1; do
            sleep 2
        done
    '; then
        log_error "等待 APT/dpkg 锁超时（300 秒）。"
        return 1
    fi
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a </dev/null
    DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Use-Pty=0 -f install </dev/null
}

patch_update() {
    log_step "Ming OS patch 级更新"
    init_config

    if ! check_network; then
        log_error "网络不可用，无法检查 patch 更新。"
        return 1
    fi

    local server
    server=$(get_config '.update_server')
    server=${server:-${UPDATE_SERVER}}
    local patch_url="${server}/api/onion-patch?version=$(current_version)&arch=amd64"

    log_info "检查 patch 更新：${patch_url}"
    local response
    response=$(curl -fsSL --retry 3 --connect-timeout 10 --max-time 30 "${patch_url}") || {
        log_warn "Patch manifest 获取失败，当前已是最新或网络超时。"
        return 0
    }

    if ! is_json_response "${response}"; then
        log_warn "Patch API 暂未返回有效 JSON，按暂无 patch 更新处理。"
        return 0
    fi

    local has_patch script_url
    has_patch=$(printf '%s' "${response}" | jq -r '.has_patch // false')
    if [[ "${has_patch}" != "true" ]]; then
        log_info "当前已是最新（patch 级别），无需更新。"
        notify-send -i system-software-update "Ming OS" "系统已是最新，无需 patch 更新。" 2>/dev/null || true
        return 0
    fi

    local patch_version notes
    patch_version=$(printf '%s' "${response}" | jq -r '.patch_version // "unknown"' 2>/dev/null)
    notes=$(printf '%s' "${response}" | jq -r '.notes // ""' 2>/dev/null)
    log_info "发现 patch 更新：${patch_version}  ${notes}"
    notify-send -i system-software-update "Ming OS patch 更新" "正在应用 ${patch_version}…" 2>/dev/null || true

    # Remote code is never executed without a signed update format and trust root.
    script_url=$(printf '%s' "${response}" | jq -r '.patch_script_url // ""' 2>/dev/null)
    if [[ -n "${script_url}" && "${script_url}" != "null" ]]; then
        log_error "unsigned patch_script_url is not supported; refusing the patch manifest"
        return 1
    fi

    # 1) apt 包更新（若 manifest 包含包列表）
    local -a packages=()
    mapfile -t packages < <(printf '%s' "${response}" | jq -r '.apt_packages[]?' 2>/dev/null)
    if [[ ${#packages[@]} -gt 0 ]]; then
        local package
        for package in "${packages[@]}"; do
            if ! is_safe_apt_package "${package}"; then
                log_error "Patch manifest 包含非法 APT 包名：${package}"
                return 1
            fi
        done
        if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
            log_error "APT patch 需要管理员权限，请使用 pkexec ming-update patch。"
            return 1
        fi
        recover_dpkg || return 1
        log_info "更新 apt 包：${packages[*]}"
        if ! DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Use-Pty=0 \
            install -- "${packages[@]}" </dev/null; then
            log_error "APT patch 安装失败，已保留 dpkg 日志供 ming-update doctor 检查。"
            return 1
        fi
    fi

    # 2) 记录已应用的 patch 版本
    set_config '.patch_version' "${patch_version}"
    set_config '.last_patch' "$(date -Iseconds)"
    notify-send -i system-software-update "Ming OS patch 完成" \
        "已应用 ${patch_version}，无需重启。" 2>/dev/null || true
    log_info "patch 更新完成：${patch_version}"
}

apply_manifest_apt_update() {
    # A one-button patch/minor update must execute exactly the manifest that
    # was displayed to the user.  Do not delegate to the legacy patch endpoint
    # here: it can describe a different update or report no patch at all.
    local manifest="$1" version package
    local -a packages=()
    [[ ${EUID:-$(id -u)} -eq 0 ]] || {
        log_error "应用更新需要管理员权限。请使用：pkexec ming-update apply"
        return 1
    }
    [[ -f "${manifest}" && ! -L "${manifest}" ]] || {
        log_error "更新清单不存在或不可信。"
        return 1
    }

    mapfile -t packages < <(jq -r '.apt_packages[]? | select(type == "string")' "${manifest}" 2>/dev/null)
    if [[ ${#packages[@]} -eq 0 ]]; then
        log_error "更新清单没有可安全应用的 APT 软件包；不会把未执行的更新标记为成功。"
        return 1
    fi
    for package in "${packages[@]}"; do
        if ! is_safe_apt_package "${package}"; then
            log_error "更新清单包含非法 APT 包名：${package}"
            return 1
        fi
    done

    recover_dpkg || return 1
    log_info "应用 ${#packages[@]} 个更新包：${packages[*]}"
    if ! DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Use-Pty=0 \
        install -- "${packages[@]}" </dev/null; then
        log_error "APT 更新失败，已保留 dpkg 状态供 ming-update doctor 检查。"
        return 1
    fi
    version="$(jq -r '.version // .latest_version // "unknown"' "${manifest}" 2>/dev/null || echo unknown)"
    set_config '.last_applied_update' "${version}"
    log_info "更新完成：${version}"
}

# ======================== major ISO 升级（保留用户文件）========================
# 核心承诺：用户数据和 Live ISO 都位于目标系统盘之外。
# Calamares 在分区前再次比较目标根分区与保留介质的物理盘祖先。
major_install_with_home_backup() {
    log_step "Ming OS major 大版本升级（保留用户文件）"
    if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        log_error "major OTA 备份与启动项暂存需要管理员权限。"
        return 1
    fi

    local manifest sfile version
    if ! manifest=$(find_cached_manifest); then
        log_error "未找到已下载的更新 manifest，请先运行：ming-update download"
        return 1
    fi
    if ! sfile="$(find_download_state_file)"; then
        log_error "未找到普通用户或系统缓存中的下载状态。"
        return 1
    fi
    version="$(jq -r '.version // "unknown"' "${sfile}" 2>/dev/null)"

    local update_type
    update_type=$(jq -r '.update_type // "major"' "${manifest}" 2>/dev/null)
    if [[ "${update_type}" == "patch" || "${update_type}" == "minor" ]]; then
        log_warn "当前缓存的更新类型为 ${update_type}，建议使用 ming-update patch 而非 major 升级。"
    fi

    # SOURCE differs for Btrfs subvolumes, so preservation requires a distinct block device.
    local home_separate=false home_src=""
    if home_is_independent_device; then
        home_separate=true
        home_src="$(findmnt -nro SOURCE -T /home 2>/dev/null || true)"
    fi

    local backup_uuid="" backup_manifest="" backup_manifest_relative=""
    local strategy="" preservation_dir="" mount_target=""
    local machine backup_dir backup_disk=""
    machine="$(cat /etc/machine-id 2>/dev/null || echo unknown)"

    if [[ "${home_separate}" == "true" ]]; then
        strategy="separate_home"
        backup_uuid="$(findmnt -nro UUID /home 2>/dev/null || true)"
        if [[ -z "${backup_uuid}" ]]; then
            log_error "独立 /home 分区缺少可验证 UUID，拒绝暂存 major OTA。"
            return 1
        fi
        preservation_dir="/home/.ming-ota"
        mkdir -p "${preservation_dir}"
        chmod 700 "${preservation_dir}"
        mount_target="$(findmnt -nro TARGET -T /home 2>/dev/null || true)"
        [[ -n "${mount_target}" ]] || {
            log_error "无法确定独立 /home 的挂载点。"
            return 1
        }
        backup_manifest="/home/.ming-ota/home-preservation.json"
        backup_manifest_relative="/.ming-ota/home-preservation.json"
        local plan_tmp
        plan_tmp="$(mktemp "${backup_manifest}.tmp.XXXXXX")"
        jq -n \
            --arg strategy "${strategy}" \
            --arg source "${home_src}" \
            --arg backup_uuid "${backup_uuid}" \
            --arg created_at "$(date -Iseconds)" \
            '{schema: 1, complete: true, completed: true, strategy: $strategy,
              source: $source, backup_uuid: $backup_uuid, disk_uuid: $backup_uuid,
              created_at: $created_at}' > "${plan_tmp}"
        chmod 600 "${plan_tmp}"
        mv -f "${plan_tmp}" "${backup_manifest}"
        log_info "/home 有独立分区（${home_src}），已建立 UUID 保留计划。"
    else
        # 同盘 /home 或单分区必须完整备份到另一块物理磁盘。
        backup_disk="$(ota_backup_destination 2>/dev/null || true)"
        if [[ -z "${backup_disk}" ]]; then
            log_error "未检测到独立物理备份盘；major OTA 不会继续。"
            return 1
        fi
        strategy="completed_backup"
        backup_dir="${backup_disk%/}/ming-ota-backup/${machine}/${version}"
        log_info "正在把 /home 原子备份到 ${backup_dir}。"
        /usr/local/sbin/ming-ota-backup backup --source /home --dest "${backup_dir}" || return 1
        backup_manifest="${backup_dir}/manifest.json"
        /usr/local/sbin/ming-ota-backup verify --manifest "${backup_manifest}" || return 1
        backup_uuid="$(jq -r '.backup_uuid // .disk_uuid // ""' "${backup_manifest}")"
        mount_target="$(findmnt -nro TARGET -T "${backup_manifest}" 2>/dev/null || true)"
        if [[ -z "${backup_uuid}" || -z "${mount_target}" ]]; then
            log_error "无法确定备份盘 UUID 或 manifest 相对路径。"
            return 1
        fi
        if [[ "${mount_target}" == "/" ]]; then
            backup_manifest_relative="/${backup_manifest#/}"
        else
            backup_manifest_relative="/${backup_manifest#${mount_target}/}"
        fi
    fi

    local source_iso checksum iso_name media_dir staged_iso staged_tmp
    local iso_bytes free_bytes staged_uuid
    source_iso="$(jq -r '.iso_path // ""' "${sfile}" 2>/dev/null)"
    checksum="$(jq -r '.checksum // ""' "${sfile}" 2>/dev/null)"
    [[ -f "${source_iso}" && ! -L "${source_iso}" && "${checksum}" =~ ^[A-Fa-f0-9]{64}$ ]] || {
        log_error "下载状态中的 ISO 或 SHA256 无效。"
        return 1
    }
    iso_name="$(basename -- "${source_iso}")"
    media_dir="${mount_target%/}/ming-ota-media/${machine}/${version}"
    install -d -m 0700 "${media_dir}"
    staged_iso="${media_dir}/${iso_name}"
    staged_tmp="${staged_iso}.tmp.$$"
    iso_bytes="$(stat -c '%s' "${source_iso}")"
    free_bytes="$(df --output=avail -B1 "${media_dir}" | tail -n 1 | tr -d ' ')"
    if [[ ! "${free_bytes}" =~ ^[0-9]+$ || "${free_bytes}" -lt $((iso_bytes + 268435456)) ]]; then
        log_error "保留介质空间不足，无法安全暂存 OTA ISO。"
        return 1
    fi
    rm -f -- "${staged_tmp}"
    if ! cp --reflink=auto --sparse=always -- "${source_iso}" "${staged_tmp}"; then
        rm -f -- "${staged_tmp}"
        log_error "复制 OTA ISO 到保留介质失败。"
        return 1
    fi
    chmod 0600 "${staged_tmp}"
    if [[ "$(sha256sum -- "${staged_tmp}" | awk '{print $1}')" != "${checksum,,}" ]]; then
        rm -f -- "${staged_tmp}"
        log_error "保留介质上的 OTA ISO 校验失败。"
        return 1
    fi
    mv -f -- "${staged_tmp}" "${staged_iso}"
    staged_uuid="$(findmnt -nro UUID -T "${staged_iso}" 2>/dev/null || true)"
    if [[ -z "${staged_uuid}" || "${staged_uuid}" != "${backup_uuid}" ]]; then
        rm -f -- "${staged_iso}"
        log_error "OTA ISO 与备份 manifest 不在同一验证文件系统。"
        return 1
    fi

    update_state_fields "${sfile}" \
        '. + {iso_path: $iso, backup_uuid: $uuid, backup_manifest: $manifest,
              backup_manifest_relative: $relative,
              home_preservation: {strategy: $strategy, prepared: true}}' \
        --arg iso "${staged_iso}" \
        --arg uuid "${backup_uuid}" \
        --arg manifest "${backup_manifest}" \
        --arg relative "${backup_manifest_relative}" \
        --arg strategy "${strategy}"

    # install_update 会再次检查 manifest 与保留字段，门禁通过后才写入 GRUB。
    install_update
}

schedule_update_restart() {
    log_info "更新已完成，正在请求系统自动重启。"
    sync
    if command -v systemctl >/dev/null 2>&1; then
        systemctl reboot --no-wall --message="Ming OS 更新已完成，正在自动重启。" && return 0
    fi
    reboot 2>/dev/null
}

apply_update() {
    # The UI calls one privileged action after a successful check.  When it
    # supplies a manifest path+fingerprint, root rechecks the server and then
    # applies that exact displayed manifest (or refuses if it has changed).
    init_config
    local manifest update_type available ready target_version already_checked=false restart_after_stage=false
    local selected_manifest="" selected_sha256=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --checked)
                already_checked=true
                shift
                ;;
            --restart-after-stage)
                restart_after_stage=true
                shift
                ;;
            --manifest)
                [[ $# -ge 2 && -z "${selected_manifest}" ]] || {
                    log_error "--manifest 参数无效。"
                    return 1
                }
                selected_manifest="$2"
                shift 2
                ;;
            --sha256)
                [[ $# -ge 2 && -z "${selected_sha256}" ]] || {
                    log_error "--sha256 参数无效。"
                    return 1
                }
                selected_sha256="$2"
                shift 2
                ;;
            *)
                log_error "未知 apply 参数：$1"
                return 1
                ;;
        esac
    done
    if [[ -n "${selected_manifest}" || -n "${selected_sha256}" ]] && \
       [[ -z "${selected_manifest}" || -z "${selected_sha256}" ]]; then
        log_error "必须同时提供更新清单和 SHA256 指纹。"
        return 1
    fi
    if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        log_error "应用更新需要管理员权限。请使用：pkexec ming-update apply"
        return 1
    fi
    if [[ "${already_checked}" != "true" ]]; then
        log_info "重新确认更新信息，避免使用过期缓存。"
        if ! check_update; then
            log_error "无法重新确认更新信息，已取消执行。"
            return 1
        fi
    fi
    if [[ -n "${selected_manifest}" ]]; then
        if ! stage_selected_manifest "${selected_manifest}" "${selected_sha256}"; then
            return 1
        fi
    fi

    # Root applies only its freshly confirmed cache.  Do not fall back to an
    # arbitrary user's cache when no explicit Settings selection was supplied.
    manifest="${CACHE_DIR}/update_info.json"
    if [[ ! -f "${manifest}" || -L "${manifest}" ]]; then
        log_error "没有已确认的更新。请先运行：ming-update check"
        return 1
    fi
    available="$(jq -r '.has_update // .update_available // false' "${manifest}" 2>/dev/null || true)"
    ready="$(jq -r '.ready // true' "${manifest}" 2>/dev/null || true)"
    update_type="$(jq -r '.update_type // "major"' "${manifest}" 2>/dev/null || true)"
    target_version="$(jq -r '.version // .latest_version // "unknown"' "${manifest}" 2>/dev/null || true)"
    if [[ "${available}" != "true" || "${ready}" != "true" ]]; then
        log_error "更新尚未准备完成，请稍后再次检查。"
        return 1
    fi
    validate_update_route "$(current_version)" "${target_version}" || return 1

    case "${update_type}" in
        patch|minor)
            if apply_manifest_apt_update "${manifest}"; then
                clear_applied_update_cache "${selected_manifest}"
                if [[ "${restart_after_stage}" == "true" ]] && ! schedule_update_restart; then
                    log_error "更新已完成，但自动重启未成功。请手动重启系统。"
                    return 2
                fi
                return 0
            fi
            return 1
            ;;
        major)
            if download_update && major_install_with_home_backup; then
                clear_applied_update_cache "${selected_manifest}"
                if [[ "${restart_after_stage}" == "true" ]] && ! schedule_update_restart; then
                    log_error "更新已准备完成，但自动重启未成功。请手动重启系统。"
                    return 2
                fi
                return 0
            fi
            return 1
            ;;
        *)
            log_error "更新类型无效：${update_type}"
            return 1
            ;;
    esac
}

# 用途：夜间挂机维护，或“更新并重启”按钮背后的实现。
auto_shutdown_update() {
    log_step "Ming OS 自动更新并重启"
    local notify_title="Ming OS 自动更新"

    _notify() {
        local msg="$1"
        log_info "${msg}"
        notify-send -i system-software-update "${notify_title}" "${msg}" 2>/dev/null || true
    }

    _notify "开始检查更新…"
    if ! MING_UPDATE_BACKGROUND_CHECK=1 check_update; then
        _notify "检查更新失败，已取消自动重启。"
        printf 'MING_UPDATE_RESULT=failed\n'
        return 1
    fi

    local manifest; manifest="$(find_cached_manifest 2>/dev/null || true)"
    if [[ -z "${manifest}" || ! -f "${manifest}" ]]; then
        _notify "当前已是最新版本，无需更新。不执行重启。"
        printf 'MING_UPDATE_RESULT=no_update\n'
        return 0
    fi

    local has_update
    has_update=$(jq -r '.has_update // .update_available // false' "${manifest}" 2>/dev/null)
    if [[ "${has_update}" != "true" ]]; then
        _notify "当前已是最新版本，无需更新。不执行重启。"
        printf 'MING_UPDATE_RESULT=no_update\n'
        return 0
    fi

    local new_version
    new_version=$(jq -r '.version // "unknown"' "${manifest}" 2>/dev/null)
    _notify "发现新版本 ${new_version}，开始自动更新…"
    if ! apply_update --checked --restart-after-stage; then
        _notify "更新未能完成，已取消自动重启。"
        printf 'MING_UPDATE_RESULT=failed\n'
        return 1
    fi

    _notify "更新已准备完成，系统正在自动重启。"
    printf 'MING_UPDATE_RESULT=staged\n'
}

auto_restart_update() {
    auto_shutdown_update "$@"
}

case "${1:-help}" in
    check) check_update ;;
    apply)
        shift
        apply_update "$@"
        ;;
    patch) patch_update ;;
    download) download_update ;;
    install) major_install_with_home_backup ;;
    auto-restart) auto_restart_update ;;
    # Backward-compatible command name.  Major OTA must reboot to continue.
    auto-shutdown) auto_restart_update ;;
    status) show_status "${2:-}" ;;
    doctor) ota_doctor ;;
    config) configure_update ;;
    help|--help|-h) show_help ;;
    *) log_error "Unknown command: $1"; show_help; exit 1 ;;
esac
OTACLI

    chmod +x /usr/local/bin/ming-update
    bash -n /usr/local/bin/ming-update
}

deploy_systemd_services() {
    echo "Creating ming-update systemd services and timers..."

    # 每周一凌晨3点定期检查（原有）
    cat > /etc/systemd/system/ming-update-check.service << 'SYSTEMDSERVICE'
[Unit]
Description=Ming OS OTA Update Check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=MING_UPDATE_BACKGROUND_CHECK=1
ExecStart=/usr/local/bin/ming-ota-run check
StandardOutput=journal
StandardError=journal
SYSTEMDSERVICE

    cat > /etc/systemd/system/ming-update-check.timer << 'SYSTEMDTIMER'
[Unit]
Description=Ming OS OTA Update Check Timer

[Timer]
OnCalendar=Mon *-*-* 03:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMDTIMER

    # 开机联网后自动检查（新增）：静默检查，有更新则桌面通知
    cat > /etc/systemd/system/ming-update-boot-check.service << 'BOOTCHECK'
[Unit]
Description=Ming OS Boot-time Update Check
After=network-online.target graphical.target
Wants=network-online.target
ConditionPathExists=/usr/local/bin/ming-update

[Service]
Type=oneshot
# 延迟90秒，等桌面完全就绪后弹通知
ExecStartPre=/bin/sleep 90
ExecStart=/usr/local/bin/ming-boot-update-check
StandardOutput=journal
StandardError=journal
# 检查失败不影响登录体验
SuccessExitStatus=0 1

[Install]
WantedBy=graphical.target
BOOTCHECK

    # 自动检查通知脚本（用户级，有更新弹 zenity/notify-send）
    cat > /usr/local/bin/ming-boot-update-check << 'BOOTCHECKSCRIPT'
#!/usr/bin/env bash
# Ming OS 开机自动检查更新（静默，有更新才弹通知）
set -uo pipefail

LOG="/tmp/ming-boot-check.log"
exec >> "${LOG}" 2>&1

echo "[$(date '+%F %T')] Boot update check started"

# 网络不通就退出，不阻塞；这个标记只由自动检查写入，供电源菜单判定。
MING_UPDATE_BACKGROUND_CHECK=1 /usr/local/bin/ming-ota-run check >/tmp/ming-update-check.log 2>&1
rc=$?
echo "[$(date '+%F %T')] ming-update check rc=${rc}"

# 没有 manifest 说明没有更新，静默退出。ming-update 的 root cache
# 固定放在 /var/cache/ming-update，不要拼接一个并不存在的 root 子目录。
manifest="/var/cache/ming-update/update_info.json"
[ -f "${manifest}" ] || exit 0

has_update=$(jq -r '.has_update // false' "${manifest}" 2>/dev/null)
[ "${has_update}" = "true" ] || exit 0

new_version=$(jq -r '.version // "新版本"' "${manifest}" 2>/dev/null)

# 找到当前登录用户的 DISPLAY
for user_home in /home/*; do
    uname=$(basename "${user_home}")
    display=$(cat /proc/*/environ 2>/dev/null | tr '\0' '\n' | grep "^DISPLAY=" | head -1 | cut -d= -f2)
    if [ -n "${display}" ]; then
        export DISPLAY="${display}"
        export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "${uname}" 2>/dev/null)/bus"
        break
    fi
done

# 桌面通知
notify-send \
    -i system-software-update \
    -a "Ming OS 更新" \
    "发现新版本 ${new_version}" \
    "打开「铭设置」→「系统更新」可一键更新；电源菜单也会提供“更新并关机”。" \
    2>/dev/null || true

echo "[$(date '+%F %T')] Notification sent for version ${new_version}"
BOOTCHECKSCRIPT
    chmod +x /usr/local/bin/ming-boot-update-check

    systemctl daemon-reload 2>/dev/null || true
    systemctl enable ming-update-check.timer 2>/dev/null || true
    systemctl enable ming-update-boot-check.service 2>/dev/null || true
    systemctl start ming-update-check.timer 2>/dev/null || true
}

deploy_gui_tool() {
    echo "Deploying ming-update compatibility redirect..."

    cat > /usr/local/bin/ming-update-gui << 'OTAGUIREDIRECT'
#!/usr/bin/env bash
# Compatibility command: the only update UI lives in Ming Settings.
exec /usr/local/bin/ming-control-center --page update "$@"
OTAGUIREDIRECT
    chmod +x /usr/local/bin/ming-update-gui
    bash -n /usr/local/bin/ming-update-gui

    # Remove standalone launchers left by older rootfs builds and users.
    rm -f /usr/share/applications/ming-update.desktop
    rm -f "/home/${MING_USER}/Desktop/ming-update.desktop"
    rm -f /home/*/Desktop/ming-update.desktop
    rm -f /home/*/.config/plank/dock1/launchers/ming-update.dockitem
    rm -f /etc/skel/Desktop/ming-update.desktop
    rm -f /etc/skel/.config/plank/dock1/launchers/ming-update.dockitem
    rm -f /usr/share/applications/ming-dock-ming-update.desktop
}


create_version_file() {
    echo "Creating Ming OS version files..."

    echo "${MING_OS_VERSION}" > /etc/ming-version
    chmod 644 /etc/ming-version

    cat > /etc/ming-release << RELEASEFILE
NAME="Ming OS"
VERSION="${MING_OS_VERSION} Home Edition"
ID=ming-os
ID_LIKE=debian
PRETTY_NAME="Ming OS ${MING_OS_VERSION} Home Edition"
VERSION_ID="${MING_OS_VERSION}"
HOME_URL="https://scallion.uno"
SUPPORT_URL="https://scallion.uno/support"
BUG_REPORT_URL="https://scallion.uno/bugs"
VERSION_CODENAME=ming
DEBIAN_CODENAME=trixie
OTA_ENABLED=true
OTA_VERSION=1.2.0
RELEASEFILE
    chmod 644 /etc/ming-release
}

main() {
    echo "=====> [06_ota_update] Deploying OTA update system <====="
    install_ota_dependencies
    deploy_ota_backup_engine
    deploy_ota_cli
    deploy_systemd_services
    deploy_gui_tool
    create_version_file
    echo "=====> [06_ota_update] OTA update system deployed <====="
}

main
