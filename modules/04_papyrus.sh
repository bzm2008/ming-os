#!/usr/bin/env bash
# Ming OS module 04: Papyrus local application integration.
# No network access or fake executable is permitted here. Only pinned local
# Papyrus release artifacts are allowed into an image.

set -uo pipefail

readonly PAPYRUS_ROOT="/opt/papyrus"
readonly PAPYRUS_WINDOW_POLICY="/usr/local/lib/ming-os/ming-papyrus-window"
readonly PAPYRUS_ASSET_DIR="${PAPYRUS_ASSET_DIR:-/tmp/ming-build/papyrus-assets}"
readonly PAPYRUS_MARKER="/var/lib/ming-os/papyrus-installed"
readonly PAPYRUS_TRUST_MARKER="/var/lib/ming-os/trusted-desktops/papyrus.desktop"
readonly PAPYRUS_DEB_NAME="Papyrus_1.0.0_amd64.deb"
readonly PAPYRUS_APPIMAGE_NAME="Papyrus_1.0.0_amd64.AppImage"
readonly PAPYRUS_DEB_SHA256="993A100E4F88190EAF833BEA3456E38C60322E24A3A553B4935E5B2550C9D368"
readonly PAPYRUS_APPIMAGE_SHA256="8B86F8CB1F9E6E39F0A3FEF9E7B36C57EB8700F7899AD4FEBD8344D0D05531B4"

find_papyrus_asset() {
    local candidate
    if [[ -n "${PAPYRUS_ASSET:-}" && -f "${PAPYRUS_ASSET}" ]]; then
        printf '%s\n' "${PAPYRUS_ASSET}"
        return 0
    fi
    for candidate in \
        "${PAPYRUS_ASSET_DIR}/${PAPYRUS_DEB_NAME}" \
        "${PAPYRUS_ASSET_DIR}/${PAPYRUS_APPIMAGE_NAME}"; do
        [[ -f "${candidate}" ]] && { printf '%s\n' "${candidate}"; return 0; }
    done
    return 1
}

expected_papyrus_sha256() {
    case "$(basename "$1")" in
        "${PAPYRUS_DEB_NAME}") printf '%s\n' "${PAPYRUS_DEB_SHA256}" ;;
        "${PAPYRUS_APPIMAGE_NAME}") printf '%s\n' "${PAPYRUS_APPIMAGE_SHA256}" ;;
        *) return 1 ;;
    esac
}

rollback_papyrus() {
    local backup="$1" artifact_backup="$2"
    rm -rf "${PAPYRUS_ROOT}"
    [[ -e "${backup}" ]] && mv "${backup}" "${PAPYRUS_ROOT}"
    rm -f /usr/bin/papyrus "${PAPYRUS_WINDOW_POLICY}" /usr/share/applications/papyrus.desktop \
        "${PAPYRUS_MARKER}" "${PAPYRUS_TRUST_MARKER}" /usr/share/icons/hicolor/128x128/apps/papyrus.png
    cp -a "${artifact_backup}"/* / 2>/dev/null || true
    rm -rf "${artifact_backup}"
}

verify_papyrus_asset() {
    local asset="$1" expected actual
    [[ -s "${asset}" ]] || return 1
    expected="$(expected_papyrus_sha256 "${asset}")" || return 1
    actual="$(sha256sum "${asset}" 2>/dev/null | awk '{print $1}' | tr '[:lower:]' '[:upper:]')" || return 1
    [[ "${actual}" == "${expected}" ]] || return 1
    case "$(basename "${asset}")" in
        "${PAPYRUS_DEB_NAME}")
            command -v dpkg-deb >/dev/null 2>&1 || return 1
            dpkg-deb --info "${asset}" >/dev/null 2>&1 || return 1
            dpkg-deb --info "${asset}" 2>/dev/null | awk -F': ' '/^ Package:/ {print $2}' | grep -Fxq 'papyrus' || return 1
            ;;
        "${PAPYRUS_APPIMAGE_NAME}")
            # AppImages are ELF payloads; reject text or shell placeholders.
            command -v file >/dev/null 2>&1 || return 1
            file -b "${asset}" | grep -Eiq 'ELF' || return 1
            ;;
        *) return 1 ;;
    esac
}

verify_papyrus_runtime() {
    # A .deb is unpacked rather than installed through apt, so validate the
    # staged executable against the image's actual shared-library set before
    # atomically replacing a known-good Papyrus payload.
    local stage="$1" candidate linkage
    [[ -d "${stage}" ]] || return 1
    if [[ -x "${stage}/Papyrus.AppImage" ]]; then
        # AppImage dependencies are intentionally self-contained and cannot be
        # checked against the rootfs with ldd before its FUSE runtime starts.
        return 0
    fi
    for candidate in \
        "${stage}/usr/bin/papyrus" \
        "${stage}/usr/lib/papyrus/papyrus" \
        "${stage}/usr/lib/papyrus/papyrus-bin" \
        "${stage}/usr/bin/Papyrus" \
        "${stage}/Papyrus"; do
        [[ -x "${candidate}" ]] || continue
        linkage="$(ldd "${candidate}" 2>&1)" || return 1
        if grep -Fq "not found" <<<"${linkage}"; then
            echo "[04_papyrus] Papyrus runtime dependency is missing: ${candidate}" >&2
            return 1
        fi
        return 0
    done
    echo "[04_papyrus] Papyrus .deb did not provide an executable to validate." >&2
    return 1
}

write_papyrus_window_policy() {
    install -d -m 0755 "$(dirname "${PAPYRUS_WINDOW_POLICY}")"
    cat > "${PAPYRUS_WINDOW_POLICY}" <<'PAPYRUSWINDOWPOLICY'
#!/usr/bin/env bash
set -u

usage() {
    printf 'Usage: %s --fit-pid PID\n' "$0" >&2
}

pid=""
if [[ "${1:-}" == "--fit-pid" && "$#" -eq 2 ]]; then
    pid="${2:-}"
else
    usage
    exit 2
fi
[[ "${pid}" =~ ^[1-9][0-9]*$ ]] || exit 2
[[ -n "${DISPLAY:-}" ]] || exit 0
command -v wmctrl >/dev/null 2>&1 || exit 0
command -v xrandr >/dev/null 2>&1 || exit 0

screen="$(xrandr --current 2>/dev/null | awk '/\\*/ {print $1; exit}')"
case "${screen}" in
    *x*) ;;
    *) exit 0 ;;
esac
screen_width="${screen%x*}"
screen_height="${screen#*x}"
[[ "${screen_width}" =~ ^[0-9]+$ && "${screen_height}" =~ ^[0-9]+$ ]] || exit 0

# Fit only an oversized first Papyrus window below the physical display bounds.
# Never undo a maximized or fullscreen state: those are explicit user choices
# and must retain Xfwm's normal titlebar and keyboard controls.
width=$((screen_width * 86 / 100))
height=$((screen_height * 76 / 100))
(( width > 1100 )) && width=1100
(( height > 700 )) && height=700
(( width < 640 && screen_width >= 640 )) && width=640
(( height < 420 && screen_height >= 420 )) && height=420
(( width > screen_width - 24 )) && width=$((screen_width - 24))
(( height > screen_height - 48 )) && height=$((screen_height - 48))
x=$(((screen_width - width) / 2))
y=$(((screen_height - height) / 2))
(( x < 12 )) && x=12
(( y < 12 )) && y=12

preserves_user_window_state() {
    command -v xprop >/dev/null 2>&1 || return 1
    xprop -id "$1" _NET_WM_STATE 2>/dev/null | grep -Eq \
        '_NET_WM_STATE_(FULLSCREEN|MAXIMIZED_VERT|MAXIMIZED_HORZ)'
}

for _attempt in $(seq 1 20); do
    window_id="$(wmctrl -lp 2>/dev/null | awk -v target="${pid}" '$3 == target {print $1; exit}')"
    if [[ -z "${window_id}" ]]; then
        window_id="$(wmctrl -lx 2>/dev/null | awk 'tolower($0) ~ /uno\\.scallion\\.papyrus/ {print $1; exit}')"
    fi
    if [[ "${window_id}" =~ ^0[xX][0-9a-fA-F]+$ ]]; then
        if ! preserves_user_window_state "${window_id}"; then
            geometry="$(wmctrl -lG 2>/dev/null | awk -v target="${window_id}" '$1 == target {print $3, $4, $5, $6; exit}')"
            read -r current_x current_y current_width current_height <<<"${geometry}"
            if [[ ! "${current_x:-}" =~ ^-?[0-9]+$ \
               || ! "${current_y:-}" =~ ^-?[0-9]+$ \
               || ! "${current_width:-}" =~ ^[0-9]+$ \
               || ! "${current_height:-}" =~ ^[0-9]+$ ]]; then
                wmctrl -i -r "${window_id}" -e "0,${x},${y},${width},${height}" 2>/dev/null || true
            elif (( current_width > width || current_height > height \
                   || current_x < 0 || current_y < 0 \
                   || current_x + current_width > screen_width \
                   || current_y + current_height > screen_height )); then
                wmctrl -i -r "${window_id}" -e "0,${x},${y},${width},${height}" 2>/dev/null || true
            fi
        fi
        wmctrl -i -a "${window_id}" 2>/dev/null || true
        exit 0
    fi
    sleep 0.25
done
exit 0
PAPYRUSWINDOWPOLICY
    chmod 0755 "${PAPYRUS_WINDOW_POLICY}"
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

launch_papyrus() {
    command_path="$1"
    shift
    "${command_path}" "$@" &
    child=$!
    if [ -x /usr/local/lib/ming-os/ming-papyrus-window ]; then
        /usr/local/lib/ming-os/ming-papyrus-window --fit-pid "${child}" >/dev/null 2>&1 &
    fi
    trap 'kill -TERM "${child}" 2>/dev/null || true; wait "${child}"; exit 143' HUP INT TERM
    wait "${child}"
}

if [ -x "$APP_ROOT/Papyrus.AppImage" ]; then
    cd "$APP_ROOT"
    launch_papyrus "$APP_ROOT/Papyrus.AppImage" "$@"
    exit $?
fi
for candidate in \
    "$APP_ROOT/usr/bin/papyrus" \
    "$APP_ROOT/usr/lib/papyrus/papyrus" \
    "$APP_ROOT/usr/lib/papyrus/papyrus-bin" \
    "$APP_ROOT/usr/bin/Papyrus" \
    "$APP_ROOT/Papyrus"; do
    if [ -x "$candidate" ] && [ "$(readlink -f "$candidate" 2>/dev/null || true)" != "/usr/bin/papyrus" ]; then
        cd "$APP_ROOT"
        launch_papyrus "$candidate" "$@"
        exit $?
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

refresh_papyrus_phone_desktop() {
    local user="${MING_USER:-user}"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    if ! id "${user}" >/dev/null 2>&1 || [[ ! -x /usr/local/bin/ming-phone-desktop ]]; then
        return 0
    fi
    runuser -u "${user}" -- env \
        HOME="/home/${user}" \
        XDG_CONFIG_HOME="/home/${user}/.config" \
        XDG_DATA_HOME="/home/${user}/.local/share" \
        timeout --foreground 12s /usr/local/bin/ming-phone-desktop --sync \
        >/tmp/ming-papyrus-desktop-sync.log 2>&1 || \
        echo "[04_papyrus] phone desktop refresh deferred; session organizer will retry." >&2
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
    # mktemp creates the staging root as 0700. Keep the atomic move, but make
    # the final /opt payload traversable by the desktop user.
    chmod 0755 "${stage}" || { rm -rf "${stage}"; return 1; }
    find "${stage}" -type d -exec chmod 0755 {} + || { rm -rf "${stage}"; return 1; }
    if ! verify_papyrus_runtime "${stage}"; then
        rm -rf "${stage}"
        return 1
    fi
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
    for artifact in /usr/bin/papyrus "${PAPYRUS_WINDOW_POLICY}" /usr/share/applications/papyrus.desktop \
        "${PAPYRUS_MARKER}" "${PAPYRUS_TRUST_MARKER}" /usr/share/icons/hicolor/128x128/apps/papyrus.png; do
        if [[ -e "${artifact}" ]]; then
            mkdir -p "${artifact_backup}$(dirname "${artifact}")"
            cp -a "${artifact}" "${artifact_backup}${artifact}" || {
                rollback_papyrus "${backup}" "${artifact_backup}"
                return 1
            }
        fi
    done
    if ! write_papyrus_window_policy || ! write_papyrus_launcher || ! write_papyrus_desktop; then
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
    if ! install -d -m 0755 "$(dirname "${PAPYRUS_MARKER}")" "$(dirname "${PAPYRUS_TRUST_MARKER}")"; then
        rollback_papyrus "${backup}" "${artifact_backup}"
        return 1
    fi
    if ! printf '%s\n' "/usr/share/applications/papyrus.desktop" > "${PAPYRUS_TRUST_MARKER}"; then
        rollback_papyrus "${backup}" "${artifact_backup}"
        return 1
    fi
    if ! printf '%s\n' "verified" > "${PAPYRUS_MARKER}"; then
        rollback_papyrus "${backup}" "${artifact_backup}"
        return 1
    fi
    rm -rf "${artifact_backup}" "${backup}"
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
    refresh_papyrus_phone_desktop
    echo "[04_papyrus] Papyrus installed from verified local asset."
}

main "$@"
