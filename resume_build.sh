#!/usr/bin/env bash
# resume_build.sh — 从 03_desktop.sh 继续打包 ISO
# 设计原则：所有逻辑（变量、函数、ISO打包）全部来自 build_onion_os.sh，
# 不维护任何独立副本，避免版本、卷标、GRUB菜单、引导链路出现漂移。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 直接 source 主构建脚本，获取所有经过验证的函数和变量 ----
# 去掉末尾的 main "$@" 调用，避免触发完整构建
_MAIN_SH="${SCRIPT_DIR}/build_onion_os.sh"
if [[ ! -f "${_MAIN_SH}" ]]; then
    echo "[ERROR] 找不到 ${_MAIN_SH}" >&2
    exit 1
fi
# 把 main 调用剥离后 source，这样所有函数和 readonly 变量都可用
eval "$(grep -v '^main ' "${_MAIN_SH}")"

# ---- 验证必要变量已就绪 ----
echo "[INFO] MING_OS_VERSION=${MING_OS_VERSION}"
echo "[INFO] ISO_VOLUME_ID=${ISO_VOLUME_ID}"
echo "[INFO] CHROOT_DIR=${CHROOT_DIR}"

# ---- 主流程：跳过 debootstrap，从模块执行继续 ----
seed_resume_package_installer() {
    local installer_source="/tmp/ming-build/assets/ming-package-installer.py"
    local common_source="/tmp/ming-build/assets/ming-shell-common.py"
    local runtime_root="/usr/local/lib/ming-os/package-installer-runtimes"
    local current_link="/usr/local/lib/ming-os/package-installer-current"
    local stage contract required_common_sha actual_common_sha actual_installer_sha target
    local target_dir_meta target_installer_meta target_common_meta
    local target_installer_sha target_common_sha
    if ! chroot_exec test -s "${installer_source}" \
        || ! chroot_exec test -s "${common_source}"; then
        log_error "resume 构建缺少受控的 ming-package-installer/ming-shell-common 资产"
        return 1
    fi
    contract="$(chroot_exec python3 - "${installer_source}" <<'PY'
import ast
import pathlib
import sys

tree = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
values = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and len(node.targets) == 1
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in {"PACKAGE_INSTALLER_CONTRACT", "REQUIRED_COMMON_SHA256"}
}
print(values.get("PACKAGE_INSTALLER_CONTRACT", ""))
print(values.get("REQUIRED_COMMON_SHA256", ""))
PY
)" || return 1
    required_common_sha="$(printf '%s\n' "${contract}" | sed -n '2p')"
    contract="$(printf '%s\n' "${contract}" | sed -n '1p')"
    if [[ ! "${contract}" =~ ^[a-z0-9.-]+$ \
        || ! "${required_common_sha}" =~ ^[a-f0-9]{64}$ ]]; then
        log_error "resume 构建的安装器版本契约无效"
        return 1
    fi
    actual_common_sha="$(chroot_exec sha256sum "${common_source}" | awk '{print $1}')"
    if [[ "${actual_common_sha}" != "${required_common_sha}" ]]; then
        log_error "resume 构建拒绝旧版或不匹配的 ming-shell-common"
        return 1
    fi
    actual_installer_sha="$(chroot_exec sha256sum "${installer_source}" | awk '{print $1}')"
    if [[ ! "${actual_installer_sha}" =~ ^[a-f0-9]{64}$ ]]; then
        log_error "resume 构建无法校验 ming-package-installer 资产"
        return 1
    fi

    chroot_exec mkdir -p "${runtime_root}" /usr/local/sbin
    stage="$(chroot_exec mktemp -d "${runtime_root}/.stage.XXXXXX")" || return 1
    if ! chroot_exec install -m 0755 "${installer_source}" "${stage}/ming-package-installer" \
        || ! chroot_exec install -m 0644 "${common_source}" "${stage}/ming-shell-common.py" \
        || ! chroot_exec chmod 0755 "${stage}" \
        || ! chroot_exec python3 -m py_compile "${stage}/ming-package-installer" \
        || ! chroot_exec python3 "${stage}/ming-package-installer" --help >/dev/null; then
        chroot_exec rm -rf "${stage}" || true
        log_error "resume 构建的安装器运行时校验失败"
        return 1
    fi

    target="${runtime_root}/${contract}"
    if chroot_exec test -e "${target}" || chroot_exec test -L "${target}"; then
        target_dir_meta="$(chroot_exec stat -c '%a:%u:%g' "${target}" 2>/dev/null || true)"
        target_installer_meta="$(chroot_exec stat -c '%a:%u:%g' \
            "${target}/ming-package-installer" 2>/dev/null || true)"
        target_common_meta="$(chroot_exec stat -c '%a:%u:%g' \
            "${target}/ming-shell-common.py" 2>/dev/null || true)"
        target_installer_sha="$(chroot_exec sha256sum "${target}/ming-package-installer" \
            2>/dev/null | awk '{print $1}' || true)"
        target_common_sha="$(chroot_exec sha256sum "${target}/ming-shell-common.py" \
            2>/dev/null | awk '{print $1}' || true)"
        if ! chroot_exec test ! -L "${target}" \
            || ! chroot_exec test -d "${target}" \
            || ! chroot_exec test ! -L "${target}/ming-package-installer" \
            || ! chroot_exec test -f "${target}/ming-package-installer" \
            || ! chroot_exec test ! -L "${target}/ming-shell-common.py" \
            || ! chroot_exec test -f "${target}/ming-shell-common.py" \
            || [[ "${target_dir_meta}" != "755:0:0" ]] \
            || [[ "${target_installer_meta}" != "755:0:0" ]] \
            || [[ "${target_common_meta}" != "644:0:0" ]] \
            || [[ "${target_installer_sha}" != "${actual_installer_sha}" ]] \
            || [[ "${target_common_sha}" != "${required_common_sha}" ]]; then
            chroot_exec rm -rf "${stage}" || true
            log_error "resume 构建拒绝复用损坏的安装器运行时: ${target}"
            return 1
        fi
        chroot_exec rm -rf "${stage}"
    else
        chroot_exec mv -T "${stage}" "${target}"
    fi
    chroot_exec ln -sfn "${target}" "${current_link}.new"
    chroot_exec mv -Tf "${current_link}.new" "${current_link}"
    chroot_exec ln -sfn \
        "${current_link}/ming-package-installer" \
        /usr/local/sbin/.ming-package-installer.new
    chroot_exec mv -Tf \
        /usr/local/sbin/.ming-package-installer.new \
        /usr/local/sbin/ming-package-installer
    chroot_exec ln -sfn \
        "${current_link}/ming-shell-common.py" \
        /usr/local/lib/ming-os/.ming-shell-common.py.new
    chroot_exec mv -Tf \
        /usr/local/lib/ming-os/.ming-shell-common.py.new \
        /usr/local/lib/ming-os/ming-shell-common.py
    chroot_exec python3 -m py_compile /usr/local/sbin/ming-package-installer
}

ensure_resume_runtime_packages() {
    log_step "补齐 resume 构建新增运行时依赖"
    # Interrupted b43 installer postinst scripts download from GitHub and can
    # block every later apt invocation. They are intentionally not part of the
    # deterministic ISO; purge leftovers before touching apt.
    chroot_exec dpkg --purge --force-all \
        firmware-b43-installer firmware-b43legacy-installer \
        >/dev/null 2>&1 || true
    if ! chroot_exec apt-get update; then
        log_error "resume 构建无法更新 APT 索引"
        return 1
    fi
    if ! chroot_exec /usr/local/sbin/apt-build install \
        xserver-xorg \
        xserver-xorg-input-libinput \
        xfce4-session \
        xfce4-settings \
        xfce4-terminal \
        xfce4-notifyd \
        xfdesktop4 \
        thunar \
        tumbler \
        lightdm \
        lightdm-gtk-greeter \
        e2fsprogs \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        xfce4-screensaver \
        python3-gi \
        gir1.2-gtk-4.0 \
        gir1.2-adw-1 \
        libadwaita-1-0 \
        gvfs \
        gvfs-backends \
        brightnessctl \
        xdotool \
        wmctrl \
        pulseaudio \
        pulseaudio-utils \
        alsa-utils \
        libasound2-plugins \
        pulseaudio-module-bluetooth \
        pavucontrol \
        bluez \
        upower \
        pkexec \
        polkitd \
        lxpolkit \
        libnotify-bin \
        x11-utils \
        desktop-file-utils \
        im-config \
        blueman \
        network-manager \
        wpasupplicant \
        iw \
        rfkill; then
        log_error "resume 构建无法安装必需运行时依赖"
        return 1
    fi
    settle_chroot_dpkg "resume runtime packages"

    # The resume helper used to install the full xfce4 meta-package, which
    # pulled the retired panel/appfinder/Whisker shell back into the image.
    # Remove those packages before the desktop gate scans the final rootfs.
    chroot_exec apt-get purge -y --no-install-recommends \
        xfce4 xfce4-panel xfce4-appfinder xfce4-whiskermenu-plugin \
        xfce4-pulseaudio-plugin >/dev/null 2>&1 || true

    local package
    for package in \
        python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 \
        gvfs gvfs-backends brightnessctl xdotool wmctrl rfkill \
        pulseaudio pulseaudio-utils alsa-utils libasound2-plugins \
        pulseaudio-module-bluetooth pavucontrol bluez upower pkexec polkitd \
        lxpolkit libnotify-bin x11-utils desktop-file-utils im-config blueman; do
        if ! chroot_exec dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -qx 'ii '; then
            log_error "resume required runtime package is not installed: ${package}"
            return 1
        fi
    done

    chroot_exec systemctl enable lightdm.service >/dev/null 2>&1 || true

    for bin in lightdm startxfce4 xfce4-session xfdesktop xfce4-screensaver xfce4-screensaver-command wmctrl mkfs.ext4; do
        if ! chroot_exec /bin/sh -c "command -v '${bin}'" >/dev/null 2>&1; then
            log_error "resume 构建缺少必要命令: ${bin}"
            exit 1
        fi
    done
    if ! chroot_exec systemctl list-unit-files lightdm.service 2>/dev/null | grep -Fq lightdm.service; then
        log_error "resume 构建缺少 lightdm.service"
        exit 1
    fi
    if ! chroot_exec fc-match ':lang=zh-cn' | grep -Eiq 'Noto|WenQuanYi|CJK'; then
        log_error "resume 构建缺少可供 Qt/Calamares 使用的中文字体"
        exit 1
    fi
}

verify_resume_release_identity() {
    local os_release="${CHROOT_DIR}/etc/os-release"
    local ming_release="${CHROOT_DIR}/etc/ming-release"
    local lsb_release="${CHROOT_DIR}/etc/lsb-release"
    local update_version_file="${CHROOT_DIR}/etc/ming-version"
    local display_version_file="${CHROOT_DIR}/etc/ming-display-version"
    local issue_file="${CHROOT_DIR}/etc/issue"
    local issue_net_file="${CHROOT_DIR}/etc/issue.net"
    local file
    for file in "${os_release}" "${ming_release}" "${lsb_release}" \
        "${update_version_file}" "${display_version_file}" \
        "${issue_file}" "${issue_net_file}"; do
        if [[ ! -f "${file}" || -L "${file}" ]]; then
            log_error "resume rootfs release identity is missing or unsafe: ${file}"
            return 1
        fi
    done
    if ! python3 - "${os_release}" "${ming_release}" "${lsb_release}" \
        "${update_version_file}" "${display_version_file}" \
        "${issue_file}" "${issue_net_file}" "${MING_OS_VERSION}" \
        "${MING_OS_UPDATE_VERSION}" "${MING_OS_RELEASE_STAGE}" \
        "${MING_OS_RELEASE_LABEL}" <<'PY'
import pathlib
import sys


def parse_unique(path):
    values = {}
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#"):
            continue
        key, separator, value = raw.partition("=")
        if not separator or key in values:
            raise ValueError("duplicate or malformed release key")
        values[key] = value.strip().strip('"')
    return values


os_path, ming_path, lsb_path, update_path, display_path, issue_path, issue_net_path = sys.argv[1:8]
display_version, update_version, stage, label = sys.argv[8:12]
expected = {
    "VERSION_ID": update_version,
    "MING_DISPLAY_VERSION": display_version,
    "MING_RELEASE_STAGE": stage,
    "PRETTY_NAME": f"Ming OS {display_version} {label}",
}
for path in (os_path, ming_path):
    values = parse_unique(path)
    if any(values.get(key) != value for key, value in expected.items()):
        raise SystemExit(1)
lsb = parse_unique(lsb_path)
if lsb.get("DISTRIB_RELEASE") != display_version:
    raise SystemExit(1)
if pathlib.Path(update_path).read_text(encoding="utf-8").strip() != update_version:
    raise SystemExit(1)
if pathlib.Path(display_path).read_text(encoding="utf-8").strip() != display_version:
    raise SystemExit(1)
expected_issue = f"Ming OS {display_version} {label}"
for path in (issue_path, issue_net_path):
    if expected_issue not in pathlib.Path(path).read_text(encoding="utf-8"):
        raise SystemExit(1)
PY
    then
            log_error "resume rootfs release identity does not match this formal build"
            return 1
    fi
}

resume_main() {
    [[ "${EUID}" -ne 0 ]] && { echo "[ERROR] 需要 root 权限"; exit 1; }

    echo "[INFO] 从 03_desktop.sh 恢复构建..."

    # An interrupted run may already have completed the rootfs and initramfs
    # but failed in a release validator. Reuse that verified chroot without
    # replaying every package module; this is intentionally opt-in so normal
    # recovery remains a full deterministic replay.
    if [[ "${MING_RESUME_SKIP_MODULES:-0}" == "1" ]]; then
        if [[ "${MING_RELEASE_MODE}" != "development" ]]; then
            log_error "正式发布禁止跳过模块重放"
            return 1
        fi
        log_step "复用现有 chroot，跳过模块重放"
        verify_resume_release_identity
        run_release_preflight
        build_iso
        return 0
    fi

    mount_chroot
    trap 'umount_chroot' EXIT

    # 同步最新的 assets（含壁纸、图标、settings.py）
    prepare_chroot_scripts
    seed_resume_package_installer
    ensure_resume_runtime_packages

    # 执行剩余模块（03 及之后）
    local modules=(
        "01_base.sh"
        "02_apps.sh"
        "03_desktop.sh"
        "04_papyrus.sh"
        "05_security_tools.sh"
        "06_ota_update.sh"
        "08_settings_hub.sh"
        "07_finalize.sh"
    )
    for mod in "${modules[@]}"; do
        local mod_path="/tmp/ming-build/modules/${mod}"
        ensure_chroot_build_link
        log_step "执行模块: ${mod}"
        chroot_exec bash "${mod_path}"
        settle_chroot_dpkg "${mod}"
        log_info "模块 ${mod} 完成"
    done

    # unpackfs 配置已由 modules/01_base.sh 正确写入 chroot，
    # resume_build 不需要也不应该在这里单独覆盖它（否则会把旧路径写回去）

    local audit_output
    audit_output="$(chroot_exec dpkg --audit)"
    if [[ -n "${audit_output}" ]]; then
        log_error "resume build has unfinished dpkg packages"
        printf '%s\n' "${audit_output}" >&2
        exit 1
    fi

    # initramfs hooks require the chroot runtime mounts. Keep this in the same
    # order as a full build so resume cannot silently produce a weaker image.
    generate_initramfs

    clean_chroot
    umount_chroot
    trap - EXIT

    # 调用主脚本里完整的 build_iso（含 build_iso_manual → grub-mkimage + El Torito + EFI）
    run_release_preflight
    build_iso

    # 复制到 Windows 目录
    if [[ "${SCRIPT_DIR}" == /mnt/* ]]; then
        local win_output="${SCRIPT_DIR}/output"
        mkdir -p "${win_output}"
        local suffix="${MING_OS_BUILD_SUFFIX}"
        local iso_name
        if [[ -n "${suffix}" ]]; then
            iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64-${suffix}.iso"
        else
            iso_name="ming-os-${MING_OS_VERSION}-${MING_OS_EDITION,,}-amd64.iso"
        fi
        if [[ -f "${OUTPUT_DIR}/${iso_name}" ]]; then
            cp "${OUTPUT_DIR}/${iso_name}" "${win_output}/${iso_name}"
            log_info "ISO 已复制到 Windows: ${win_output}/${iso_name}"
        fi
    fi

    log_info "=== 恢复构建完成 ==="
}

resume_main "$@"
