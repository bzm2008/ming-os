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
        "${PAPYRUS_ASSET_DIR}"/*.deb \
        "${PAPYRUS_ASSET_DIR}"/*.AppImage \
        "${PAPYRUS_ASSET_DIR}"/Papyrus_*.deb \
        "${PAPYRUS_ASSET_DIR}"/Papyrus_*.AppImage \
        "${PAPYRUS_ASSET_DIR}"/papyrus_*.deb \
        "${PAPYRUS_ASSET_DIR}"/papyrus_*.AppImage; do
        [[ -f "${candidate}" ]] && { printf '%s\n' "${candidate}"; return 0; }
    done
    return 1
}

rollback_papyrus() {
    local backup="$1" artifact_backup="$2"
    rm -rf "${PAPYRUS_ROOT}"
    [[ -e "${backup}" ]] && mv "${backup}" "${PAPYRUS_ROOT}"
    rm -f /usr/bin/papyrus /usr/share/applications/papyrus.desktop \
        "${PAPYRUS_MARKER}" /usr/share/icons/hicolor/128x128/apps/papyrus.png
    cp -a "${artifact_backup}"/* / 2>/dev/null || true
    rm -rf "${artifact_backup}"
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

install_papyrus_thunar_action() {
    local uca="/home/${MING_USER:-user}/.config/Thunar/uca.xml"
    [[ -f "${uca}" ]] || return 0
    grep -Fq 'papyrus-uca-action' "${uca}" && return 0
    sed -i '/<\/actions>/i\\<action><icon>papyrus</icon><name>Papyrus</name><unique-id>papyrus-uca-action</unique-id><command>/usr/bin/papyrus %f</command><description>Open with Papyrus</description><patterns>*</patterns><text-files/><other-files/></action>' "${uca}"
}

install_papyrus_asset() {
    local asset="$1" stage
    stage=$(mktemp -d /tmp/papyrus-stage.XXXXXX)
    local backup="${PAPYRUS_ROOT}.previous"
    rm -rf "${backup}"
    case "${asset}" in
        *.deb|*.DEB) dpkg-deb -x "${asset}" "${stage}" || { rm -rf "${stage}"; return 1; } ;;
        *.AppImage|*.appimage) install -Dm755 "${asset}" "${stage}/Papyrus.AppImage" || { rm -rf "${stage}"; return 1; } ;;
    esac
    if [[ -e "${PAPYRUS_ROOT}" || -L "${PAPYRUS_ROOT}" ]]; then
        mv "${PAPYRUS_ROOT}" "${backup}" || { rm -rf "${stage}"; return 1; }
    fi
    if ! mv "${stage}" "${PAPYRUS_ROOT}"; then
        [[ -e "${backup}" ]] && mv "${backup}" "${PAPYRUS_ROOT}"
        rm -rf "${stage}"
        return 1
    fi
    local artifact_backup
    artifact_backup=$(mktemp -d /tmp/papyrus-artifacts.XXXXXX)
    for artifact in /usr/bin/papyrus /usr/share/applications/papyrus.desktop \
        "${PAPYRUS_MARKER}" /usr/share/icons/hicolor/128x128/apps/papyrus.png; do
        if [[ -e "${artifact}" ]]; then
            mkdir -p "${artifact_backup}$(dirname "${artifact}")"
            cp -a "${artifact}" "${artifact_backup}${artifact}"
        fi
    done
    if ! write_papyrus_launcher || ! write_papyrus_desktop; then
        rollback_papyrus "${backup}" "${artifact_backup}"
        return 1
    fi
    if [[ -f "${PAPYRUS_ROOT}/usr/share/icons/hicolor/128x128/apps/papyrus.png" ]]; then
        install -Dm644 "${PAPYRUS_ROOT}/usr/share/icons/hicolor/128x128/apps/papyrus.png" \
            /usr/share/icons/hicolor/128x128/apps/papyrus.png || {
                rollback_papyrus "${backup}" "${artifact_backup}"
                return 1
            }
    fi
    rm -rf "${artifact_backup}"
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
    if [[ -x /usr/local/sbin/ming-refresh-dock-launchers ]]; then
        /usr/local/sbin/ming-refresh-dock-launchers "${MING_USER:-user}" || true
    fi
    install_papyrus_thunar_action
    echo "[04_papyrus] Papyrus installed from verified local asset."
}

main "$@"
