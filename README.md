# Onion OS 26.2.6-r2 Home Edition

Onion OS is a Debian 13 / Trixie based desktop system for older PCs and Chinese desktop users. The current public release is `26.2.6-r2`, a bootfix and desktop experience rebuild that replaces the earlier 26.2.5 and 26.2.6 images.

## Current Release

| Item | Value |
| --- | --- |
| Version | 26.2.6-r2 Home Edition |
| Base | Debian 13 / Trixie |
| Desktop | Xfce + Plank Dock + Onion desktop tools |
| ISO | `onion-os-26.2.6-r2-home-amd64-f2823efa.iso` |
| Size | `2568552448` bytes |
| SHA256 | `f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25` |
| Official download | `https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso` |
| OTA check | `https://scallion.uno/api/onion-update/check?version=26.2.0&channel=stable` |
| GitHub release | `https://github.com/bzm2008/onion-os/releases/tag/v26.2.6-r2` |

## What Changed In 26.2.6-r2

- Fixed the bad 26.2.5 boot chain that could show `invalid magic number` / `you need to load the kernel first`.
- Kept the 26.2.6 bootfix ISO layout: BIOS + UEFI El Torito, isohybrid MBR, stable volume label `ONION_OS_2626`.
- Rebuilt the desktop experience so the Onion wallpaper is applied by default instead of the Debian wallpaper.
- Removed broad opaque PNG icon overrides that caused white square borders on desktop and Dock icons.
- Fixed Onion Security Manager startup by using a stable wrapper and readable logs.
- Rebranded the Live installer from Debian to Onion OS: desktop entry, Calamares branding, and installed system identity.
- Added installed-system identity repair so a completed install presents itself as Onion OS rather than Debian.
- Added HDD/SSD runtime tuning for schedulers, read-ahead, and writeback behavior.
- Continued the Android-like app folders, "All Disks" entry, low-command workflow, Dock-only desktop, update button flow, and low-memory WeChat strategy.

## GitHub Assets

GitHub Release assets are split because the ISO is larger than the practical single-asset limit used by previous releases.

Download these files from the release page:

```text
onion-os-26.2.6-r2-home-amd64.iso.part01
onion-os-26.2.6-r2-home-amd64.iso.part02
onion-os-26.2.6-r2-home-amd64.iso.sha256
SHA256SUMS
```

Merge on Linux, macOS, or WSL:

```bash
cat onion-os-26.2.6-r2-home-amd64.iso.part01 onion-os-26.2.6-r2-home-amd64.iso.part02 > onion-os-26.2.6-r2-home-amd64.iso
sha256sum -c onion-os-26.2.6-r2-home-amd64.iso.sha256
```

Merge on Windows PowerShell:

```powershell
cmd /c copy /b onion-os-26.2.6-r2-home-amd64.iso.part01+onion-os-26.2.6-r2-home-amd64.iso.part02 onion-os-26.2.6-r2-home-amd64.iso
Get-FileHash onion-os-26.2.6-r2-home-amd64.iso -Algorithm SHA256
```

The merged file must match:

```text
f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25
```

## Install

For most users, download the complete ISO from the official website and write it with Rufus, Ventoy, or `dd`.

```bash
sudo dd if=onion-os-26.2.6-r2-home-amd64-f2823efa.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Live mode should auto-login as `onion`. The desktop installer is branded as `Install Onion OS` / `安装 Onion OS`; it should no longer appear as `Install Debian`.

## OTA

Normal users should click the desktop update button. Advanced users can still use:

```bash
onion-update check
onion-update download
sudo onion-update install
```

The public OTA endpoint currently returns:

```json
{
  "version": "26.2.6-r2",
  "ready": true,
  "download_url": "https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso",
  "checksum": "f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25",
  "size": 2568552448
}
```

## Low-Memory Notes

Onion OS itself is designed to stay usable on low-memory machines, but WeChat can still be the main pressure source when an account has many friends or group chats. Onion OS uses zram, earlyoom, lower-cost desktop effects, cache cleanup, and a low-memory WeChat launcher to protect the system first. For very heavy WeChat accounts on 2GB RAM machines, Web WeChat or a memory upgrade is still recommended.

## Build

Recommended build host: Debian 13, Debian 12, Ubuntu 22.04+, or WSL Debian, with root privileges and at least 30GB free space.

```bash
chmod +x build_onion_os.sh modules/*.sh
sudo ./build_onion_os.sh
```

Important source areas:

```text
build_onion_os.sh          ISO build and boot chain checks
modules/01_base.sh         Debian/Trixie base, identity, memory profile
modules/02_apps.sh         WeChat, WPS, Spark Store, Chinese desktop apps
modules/03_desktop.sh      Xfce/Dock desktop, wallpaper, app folders, website handoff docs
modules/05_security_tools.sh
modules/06_ota_update.sh   OTA CLI and GUI updater
repack_2626_r2_desktopfix.sh
```
