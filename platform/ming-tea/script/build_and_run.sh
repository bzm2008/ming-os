#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="铭荼"
PROCESS_NAME="ming-tea-desktop"
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${ROOT_DIR}/apps/desktop/src-tauri/target/release/bundle/macos/${APP_NAME}.app"

pkill -x "${PROCESS_NAME}" >/dev/null 2>&1 || true

export PATH="${HOME}/.cargo/bin:${PATH}"
pnpm --filter @ming-tea/desktop tauri:build --bundles app

[[ -x "${APP_BUNDLE}/Contents/MacOS/${PROCESS_NAME}" ]] || {
    echo "error: Tauri app bundle was not created: ${APP_BUNDLE}" >&2
    exit 1
}

open_app() {
    /usr/bin/open -n "${APP_BUNDLE}"
}

case "${MODE}" in
    run)
        open_app
        ;;
    --debug|debug)
        lldb -- "${APP_BUNDLE}/Contents/MacOS/${PROCESS_NAME}"
        ;;
    --logs|logs)
        open_app
        /usr/bin/log stream --info --style compact --predicate "process == \"${PROCESS_NAME}\""
        ;;
    --telemetry|telemetry)
        open_app
        /usr/bin/log stream --info --style compact --predicate "subsystem == \"cn.mingos.mingtea\""
        ;;
    --verify|verify)
        open_app
        sleep 1
        pgrep -x "${PROCESS_NAME}" >/dev/null
        ;;
    *)
        echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
        exit 2
        ;;
esac
