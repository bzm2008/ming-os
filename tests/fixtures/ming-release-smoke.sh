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
        *) echo "usage: $0 [--exercise-apt] [--exercise-files] [--exercise-apps]" >&2; exit 2 ;;
    esac
done

pass() { printf 'PASS  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; failures=$((failures + 1)); }

launch_socket_path() {
    local base="${XDG_RUNTIME_DIR:-}"
    if [[ -z "${base}" || "${base}" != /* ]]; then
        base="/tmp/ming-runtime-$(id -u)"
    fi
    printf '%s/ming-os/launch.sock\n' "${base}"
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
        for _cleanup_try in 1 2 3 4 5; do
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
    ming-launch --server >>/tmp/ming-smoke-launch-broker.log 2>&1 &
    smoke_broker_pid=$!
    for _broker_try in $(seq 1 30); do
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
    if dpkg --audit | grep -q .; then
        fail "dpkg reports unfinished packages"
    else
        pass "dpkg database is consistent"
    fi
    if [[ -e /var/lib/dpkg/lock-frontend ]] && command -v fuser >/dev/null 2>&1 \
        && fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
        warn "APT frontend lock is currently held"
    else
        pass "APT frontend is not locked"
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
    [[ -x /usr/local/sbin/ming-ota-backup ]] \
        && pass "OTA backup engine is installed" \
        || fail "OTA backup engine is missing"
    if [[ -x /usr/local/sbin/ming-ota-backup ]]; then
        local tmp source backup restore manifest
        tmp=$(mktemp -d)
        source="${tmp}/source"
        backup="${tmp}/backup"
        restore="${tmp}/restore"
        mkdir -p "${source}" "${restore}"
        printf 'ming-ota-smoke\n' > "${source}/payload.txt"
        if MING_OTA_TEST_MODE=1 \
            MING_OTA_MACHINE_ID=smoke-machine \
            MING_OTA_DISK_UUID=smoke-disk \
            MING_OTA_AVAILABLE_BYTES=1073741824 \
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
                fail "OTA backup restore round trip failed"
            fi
        else
            fail "OTA backup round trip could not create a backup"
        fi
        rm -rf -- "${tmp}"
    fi
}

check_windows() {
    local edge_desktop=/usr/share/applications/ming-edge.desktop
    local spark_desktop=/usr/share/applications/spark-store.desktop
    [[ -f "${edge_desktop}" ]] || fail "Edge desktop launcher is missing"
    [[ -f "${spark_desktop}" ]] || fail "Spark Store desktop launcher is missing"
    if [[ "${exercise_apps}" -eq 1 ]]; then
        if ensure_launch_broker; then
            if [[ -f "${edge_desktop}" ]] && ! launch_app_bounded "${edge_desktop}"; then
                fail "Edge launch request timed out or failed"
            fi
            if [[ -f "${spark_desktop}" ]] && ! launch_app_bounded "${spark_desktop}"; then
                fail "Spark Store launch request timed out or failed"
            fi
        else
            fail "Ming launch broker is unavailable"
        fi
        for _attempt in $(seq 1 60); do
            if wmctrl -lx 2>/dev/null | grep -qi 'microsoft-edge' \
                && wmctrl -lx 2>/dev/null | grep -Eqi 'spark|星火'; then
                break
            fi
            sleep 0.25
        done
    fi
    if command -v wmctrl >/dev/null 2>&1; then
        local edge_lines spark_lines
        edge_lines=$(wmctrl -lx 2>/dev/null | grep -i 'microsoft-edge' || true)
        spark_lines=$(wmctrl -lx 2>/dev/null | grep -Ei 'spark|星火' || true)
        if [[ "${exercise_apps}" -eq 1 ]]; then
            [[ -n "${edge_lines}" ]] && pass "Edge has a visible X11 window" || fail "Edge has no visible window"
            [[ -n "${spark_lines}" ]] && pass "Spark Store has a visible window" || fail "Spark Store has no visible window"
        fi
    else
        warn "wmctrl is unavailable; window geometry cannot be inspected"
    fi
    if pgrep -f 'microsoft-edge.*--ozone-platform=x11' >/dev/null 2>&1; then
        pass "Edge uses the VM-safe X11 wrapper"
    else
        [[ "${exercise_apps}" -eq 1 ]] && fail "Edge VM-safe process flags were not observed" || warn "Edge is not running"
    fi
    if pgrep -f 'spark-store|spark-store-launcher|store.spark-app' >/dev/null 2>&1; then
        pass "Spark Store process is running"
    elif [[ -s "${HOME}/.cache/ming-os/spark-store.log" ]]; then
        [[ "${exercise_apps}" -eq 1 ]] && fail "Spark Store exited; see ${HOME}/.cache/ming-os/spark-store.log" || warn "Spark Store is not running"
    else
        [[ "${exercise_apps}" -eq 1 ]] && fail "Spark Store is not running and has no diagnostic log" || warn "Spark Store is not running"
    fi
}

check_network() {
    if ! command -v nmcli >/dev/null 2>&1; then
        fail "NetworkManager nmcli is missing"
        return
    fi
    local state wifi_count
    state=$(nmcli -t -f STATE general 2>/dev/null || true)
    [[ -n "${state}" ]] && pass "NetworkManager state: ${state}" || fail "NetworkManager is not responding"
    wifi_count=$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2=="wifi" {count++} END {print count+0}')
    if [[ "${wifi_count}" -gt 0 ]]; then
        pass "Wi-Fi interface detected"
        nmcli -t -f IN-USE,SSID,SIGNAL device wifi list --rescan yes 2>/dev/null | head -n 8 || true
    else
        warn "No Wi-Fi interface; collecting PCI/USB, driver, rfkill and firmware evidence"
        lspci -nnk 2>/dev/null | grep -A3 -Ei 'network|wireless' || true
        lsusb 2>/dev/null || true
        rfkill list 2>/dev/null || true
        journalctl -b -k --no-pager 2>/dev/null | grep -Ei 'firmware|iwlwifi|rtw|ath|brcm|b43|wl' | tail -n 20 || true
    fi
}

check_shell() {
    pgrep -f '/usr/local/bin/ming-phone-desktop' >/dev/null 2>&1 \
        && pass "Android-style desktop is running" \
        || fail "Android-style desktop is not running"
    pgrep -x plank >/dev/null 2>&1 \
        && pass "Plank Dock is running" \
        || fail "Plank Dock is not running"
    local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ming-os"
    if [[ -S "${runtime_dir}/launch.sock" || -S "${runtime_dir}/app-drawer.sock" ]]; then
        pass "Ming shell IPC socket is ready"
    else
        fail "Ming shell IPC sockets are not ready"
    fi
    [[ -x /usr/local/bin/ming-app-drawer ]] \
        && pass "Application drawer is installed" \
        || fail "Application drawer is missing"
    [[ -x /usr/local/bin/ming-files ]] \
        && pass "Ming Files is installed" \
        || fail "Ming Files is missing"
    [[ -x /usr/local/bin/ming-notifications ]] \
        && pass "Notification helper is installed" \
        || fail "Notification helper is missing"
}

check_files_exercise() {
    [[ "${exercise_files}" -eq 1 ]] || return 0
    local tmp
    tmp=$(mktemp -d)
    command_ok "Ming Files Gio copy/rename/delete self-test" ming-files --self-test "${tmp}"
    rm -rf -- "${tmp}"
}

check_apt
check_ota
check_windows
check_network
check_shell
check_files_exercise

printf '\nSummary: %d failure(s), %d warning(s)\n' "${failures}" "${warnings}"
[[ "${failures}" -eq 0 ]]
