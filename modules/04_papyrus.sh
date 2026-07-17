#!/usr/bin/env bash
# Ming OS module 04: Papyrus local application integration.
# No network access or fake executable is permitted here.  A verified local
# Papyrus release asset is optional so normal builds remain reproducible.

set -uo pipefail

readonly PAPYRUS_ROOT="/opt/papyrus"
readonly PAPYRUS_ASSET_DIR="${PAPYRUS_ASSET_DIR:-/tmp/ming-build/papyrus-assets}"
readonly PAPYRUS_MARKER="/var/lib/ming-os/papyrus-installed"

find_papyrus_asset() {
    local candidate
    if [[ -n "${PAPYRUS_ASSET:-}" && -f "${PAPYRUS_ASSET}" ]]; then
        printf '%s\n' "${PAPYRUS_ASSET}"
        return 0
    fi
    for candidate in \
        "${PAPYRUS_ASSET_DIR}"/Papyrus_*.deb \
        "${PAPYRUS_ASSET_DIR}"/Papyrus_*.AppImage \
        "${PAPYRUS_ASSET_DIR}"/papyrus_*.deb \
        "${PAPYRUS_ASSET_DIR}"/papyrus_*.AppImage; do
        [[ -f "${candidate}" ]] && { printf '%s\n' "${candidate}"; return 0; }
    done
    return 1
}

verify_papyrus_asset() {
    local asset="$1"
    [[ -s "${asset}" ]] || return 1
    case "${asset}" in
        *.deb|*.DEB)
            command -v dpkg-deb >/dev/null 2>&1 || return 1
            dpkg-deb --info "${asset}" >/dev/null 2>&1 || return 1
            dpkg-deb --info "${asset}" 2>/dev/null | awk -F': ' '/^ Package:/ {print $2}' | grep -Fxq 'papyrus' || return 1
            ;;
        *.AppImage|*.appimage)
            # AppImages are ELF payloads; reject text or shell placeholders.
            command -v file >/dev/null 2>&1 || return 1
            file -b "${asset}" | grep -Eiq 'ELF' || return 1
            ;;
        *) return 1 ;;
    esac
}

write_papyrus_launcher() {
    install -d -m 0755 /usr/bin
    cat > /usr/bin/papyrus <<'PAPYRUSLAUNCHER'
#!/bin/sh
set -eu
APP_ROOT=/opt/papyrus
XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
XDG_DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
export XDG_CONFIG_HOME XDG_DATA_HOME

if [ -x "$APP_ROOT/Papyrus.AppImage" ]; then
    exec "$APP_ROOT/Papyrus.AppImage" "$@"
fi
for candidate in \
    "$APP_ROOT/usr/bin/papyrus" \
    "$APP_ROOT/usr/lib/papyrus/papyrus" \
    "$APP_ROOT/usr/lib/papyrus/papyrus-bin" \
    "$APP_ROOT/usr/bin/Papyrus" \
    "$APP_ROOT/Papyrus"; do
    if [ -x "$candidate" ] && [ "$(readlink -f "$candidate" 2>/dev/null || true)" != "/usr/bin/papyrus" ]; then
        cd "$APP_ROOT"
        exec "$candidate" "$@"
    fi
done
echo "Papyrus executable was not found under $APP_ROOT." >&2
exit 1
PAPYRUSLAUNCHER
    chmod 0755 /usr/bin/papyrus
}

write_papyrus_desktop() {
    install -d -m 0755 /usr/share/applications
    local icon_name=utilities-terminal
    if [[ -f "${PAPYRUS_ROOT}/usr/share/icons/hicolor/128x128/apps/papyrus.png" \
       || -f "${PAPYRUS_ROOT}/usr/share/pixmaps/papyrus.png" ]]; then
        icon_name=papyrus
    fi
    cat > /usr/share/applications/papyrus.desktop <<PAPYRUSDESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Papyrus
GenericName=Writing and work assistant
Comment=Local-first writing and work assistant
Exec=/usr/bin/papyrus %U
TryExec=/usr/bin/papyrus
Icon=${icon_name}
Terminal=false
StartupNotify=true
StartupWMClass=uno.scallion.papyrus
Categories=Office;Utility;
MimeType=text/plain;application/rtf;application/pdf;
Keywords=writing;research;assistant;
PAPYRUSDESKTOP
}

install_papyrus_asset() {
    local asset="$1" stage
    stage=$(mktemp -d /tmp/papyrus-stage.XXXXXX)
    local backup="${PAPYRUS_ROOT}.previous"
    rm -rf "${backup}"
    case "${asset}" in
        *.deb|*.DEB) dpkg-deb -x "${asset}" "${stage}" || { rm -rf "${stage}"; return 1; } ;;
        *.AppImage|*.appimage) install -Dm755 "${asset}" "${stage}/Papyrus.AppImage" ;;
    esac
    if [[ -e "${PAPYRUS_ROOT}" || -L "${PAPYRUS_ROOT}" ]]; then
        mv "${PAPYRUS_ROOT}" "${backup}" || { rm -rf "${stage}"; return 1; }
    fi
    if ! mv "${stage}" "${PAPYRUS_ROOT}"; then
        [[ -e "${backup}" ]] && mv "${backup}" "${PAPYRUS_ROOT}"
        rm -rf "${stage}"
        return 1
    fi
    if ! write_papyrus_launcher || ! write_papyrus_desktop; then
        rm -rf "${PAPYRUS_ROOT}"
        [[ -e "${backup}" ]] && mv "${backup}" "${PAPYRUS_ROOT}"
        return 1
    fi
    rm -rf "${backup}"
    install -d -m 0755 "$(dirname "${PAPYRUS_MARKER}")"
    printf '%s\n' "verified" > "${PAPYRUS_MARKER}"
}

main() {
    local asset
    # Remove legacy Garlic/OpenClaw launch surfaces on reused roots. User data
    # under XDG locations is intentionally untouched.
    rm -f /usr/share/applications/garlic-claw.desktop \
        /usr/local/bin/garlic-claw /usr/local/bin/garlic-claw-app \
        /usr/local/bin/openclaw
    rm -f "/home/${MING_USER:-user}/.config/systemd/user/openclaw-gateway.service"
    if ! asset=$(find_papyrus_asset); then
        echo "[04_papyrus] No Papyrus asset found; skipping optional integration."
        return 0
    fi
    if ! verify_papyrus_asset "${asset}"; then
        echo "[04_papyrus] Papyrus asset failed verification; skipping." >&2
        return 0
    fi
    if ! install_papyrus_asset "${asset}"; then
        echo "[04_papyrus] Papyrus installation failed; existing installation preserved." >&2
        return 1
    fi
    echo "[04_papyrus] Papyrus installed from verified local asset."
}

main "$@"
