#!/usr/bin/env bash
set -uo pipefail

exercise_apt=0
exercise_files=0
exercise_apps=0
failures=0
warnings=0
smoke_broker_pid=""

for arg in "$@"; do
    case "${arg}" in
        --exercise-apt) exercise_apt=1 ;;
        --exercise-files) exercise_files=1 ;;
        --exercise-apps) exercise_apps=1 ;;
        *) printf 'usage: %s [--exercise-apt] [--exercise-files] [--exercise-apps]\n' "$0" >&2; exit 2 ;;
    esac
done

pass() { printf 'PASS  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; failures=$((failures + 1)); }

launch_socket_path() {
    local runtime="${XDG_RUNTIME_DIR:-}"
    [[ "${runtime}" == /* ]] || runtime="/tmp/ming-runtime-$(id -u)"
    printf '%s/ming-os/launch.sock\n' "${runtime}"
}

broker_socket_ready() {
    local socket_path="$1"
    [[ -S "${socket_path}" ]] || return 1
    python3 - "${socket_path}" <<'PY'
import socket
import sys

client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(0.4)
try:
    client.connect(sys.argv[1])
except OSError:
    raise SystemExit(1)
finally:
    client.close()
PY
}

cleanup_smoke_broker() {
    local pid="${smoke_broker_pid}"
    smoke_broker_pid=""
    [[ -n "${pid}" ]] || return 0
    if kill -0 "${pid}" 2>/dev/null; then
        kill -TERM "${pid}" 2>/dev/null || true
        for _try in 1 2 3 4 5; do
            kill -0 "${pid}" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL "${pid}" 2>/dev/null || true
    fi
    wait "${pid}" 2>/dev/null || true
}
trap cleanup_smoke_broker EXIT

ensure_launch_broker() {
    local socket_path
    socket_path="$(launch_socket_path)"
    broker_socket_ready "${socket_path}" && return 0
    command -v ming-launch >/dev/null 2>&1 || return 1
    mkdir -p "$(dirname "${socket_path}")" 2>/dev/null || true
    ming-launch --server >/tmp/ming-smoke-launch-broker.log 2>&1 &
    smoke_broker_pid=$!
    for _try in $(seq 1 30); do
        broker_socket_ready "${socket_path}" && return 0
        kill -0 "${smoke_broker_pid}" 2>/dev/null || break
        sleep 0.1
    done
    cleanup_smoke_broker
    return 1
}

launch_app_bounded() {
    local desktop_file="$1"
    timeout --signal=TERM --kill-after=1s 4s \
        ming-launch --desktop-file "${desktop_file}" --source unknown \
        >/tmp/ming-smoke-launch.log 2>&1
}

command_ok() {
    local label="$1"
    shift
    if "$@" >/tmp/ming-smoke-command.log 2>&1; then
        pass "${label}"
    else
        fail "${label}: $(tail -n 2 /tmp/ming-smoke-command.log | tr '\n' ' ')"
    fi
}

check_apt() {
    if dpkg --audit 2>/dev/null | grep -q .; then
        fail "dpkg reports unfinished packages"
    else
        pass "dpkg database is consistent"
    fi
    if [[ "${exercise_apt}" -eq 1 ]]; then
        command_ok "APT package index refresh" pkexec apt-get update
        command_ok "APT harmless install/remove transaction" bash -c \
            'pkexec apt-get install -y --no-install-recommends sl && pkexec apt-get remove -y sl'
    fi
}

check_ota() {
    if command -v ming-update >/dev/null 2>&1; then
        command_ok "OTA doctor" ming-update doctor
    else
        fail "ming-update command is missing"
    fi
    [[ -x /usr/local/sbin/ming-ota-backup ]] && pass "OTA backup engine is installed" \
        || fail "OTA backup engine is missing"
    [[ -x /usr/local/sbin/ming-ota-backup ]] || return 0
    local tmp source backup restore manifest
    tmp=$(mktemp -d)
    source="${tmp}/source"; backup="${tmp}/backup"; restore="${tmp}/restore"
    mkdir -p "${source}" "${restore}"
    printf 'ming-ota-smoke\n' > "${source}/payload.txt"
    if MING_OTA_TEST_MODE=1 MING_OTA_MACHINE_ID=smoke-machine \
        MING_OTA_DISK_UUID=smoke-disk MING_OTA_AVAILABLE_BYTES=1073741824 \
        /usr/local/sbin/ming-ota-backup backup --source "${source}" --dest "${backup}" \
        >/tmp/ming-smoke-backup.log 2>&1; then
        manifest="${backup}/manifest.json"
        if MING_OTA_TEST_MODE=1 MING_OTA_DISK_UUID=smoke-disk \
            /usr/local/sbin/ming-ota-backup verify --manifest "${manifest}" >/dev/null 2>&1 \
            && MING_OTA_TEST_MODE=1 MING_OTA_DISK_UUID=smoke-disk \
            /usr/local/sbin/ming-ota-backup restore --manifest "${manifest}" \
                --target "${restore}" --system-target "${tmp}" >/dev/null 2>&1 \
            && cmp -s "${source}/payload.txt" "${restore}/payload.txt"; then
            pass "OTA backup/verify/restore round trip"
        else
            fail "OTA backup/verify/restore round trip failed"
        fi
    else
        fail "OTA backup round trip could not create a backup"
    fi
    rm -rf -- "${tmp}"
}

check_apps() {
    local edge="/usr/share/applications/ming-edge.desktop"
    local spark="/usr/share/applications/spark-store.desktop"
    [[ -f "${edge}" ]] && pass "Edge desktop launcher is installed" || fail "Edge desktop launcher is missing"
    [[ -f "${spark}" ]] && pass "Spark Store desktop launcher is installed" || fail "Spark Store desktop launcher is missing"
    [[ "${exercise_apps}" -eq 1 ]] || return 0
    ensure_launch_broker || { fail "Ming launch broker is unavailable"; return 0; }
    [[ -f "${edge}" ]] && launch_app_bounded "${edge}" || true
    [[ -f "${spark}" ]] && launch_app_bounded "${spark}" || true
}

check_network() {
    command -v nmcli >/dev/null 2>&1 || { fail "NetworkManager nmcli is missing"; return; }
    local state
    state=$(LC_ALL=C nmcli -t -f STATE general 2>/dev/null || true)
    [[ -n "${state}" ]] && pass "NetworkManager state: ${state}" || fail "NetworkManager is not responding"
}

check_shell() {
    pgrep -f '/usr/local/bin/ming-phone-desktop' >/dev/null 2>&1 \
        && pass "Ming desktop is running" || warn "Ming desktop is not running"
    pgrep -x plank >/dev/null 2>&1 && pass "Plank Dock is running" || warn "Plank Dock is not running"
    [[ -x /usr/local/bin/ming-app-drawer ]] && pass "Application drawer is installed" \
        || fail "Application drawer is missing"
    [[ -x /usr/local/bin/ming-files ]] && pass "Ming Files is installed" \
        || fail "Ming Files is missing"
}

check_files_exercise() {
    [[ "${exercise_files}" -eq 1 ]] || return 0
    local tmp
    tmp=$(mktemp -d)
    command_ok "Ming Files self-test" ming-files --self-test "${tmp}"
    rm -rf -- "${tmp}"
}

check_apt
check_ota
check_apps
check_network
check_shell
check_files_exercise
printf '\nSummary: %d failure(s), %d warning(s)\n' "${failures}" "${warnings}"
[[ "${failures}" -eq 0 ]]
