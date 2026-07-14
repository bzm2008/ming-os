# Ming OS 26.3.3 Home Edition

Ming OS is a Debian 13 / Trixie based Chinese desktop system for older PCs, family machines, and users who prefer buttons over terminal commands. The current candidate release is `26.3.3`, focused on reliable BIOS/UEFI boot, a branded installer, Chinese defaults, old 64-bit PC compatibility, and a stable Ming desktop experience.

## Current Release

| Item | Value |
| --- | --- |
| Version | 26.3.3 Home Edition |
| Base | Debian 13 / Trixie |
| Kernel | Debian 6.12 LTS family in the current ISO |
| Desktop | Xfce + Plank Dock + Ming desktop tools |
| ISO | `ming-os-26.3.3-home-amd64.iso` |
| Size | 1,992,589,312 bytes (1.86 GiB) |
| SHA256 | `1d71a3d71441ca938fc46551d9d6c3cdfc9b5e9a9f2fb7b4eb4fb611360e5e11` |
| Release state | Built and ready for website/OTA publication |
| CPU target | Debian amd64 baseline; old 64-bit CPUs without AVX2 remain in scope |
| 32-bit status | Deferred; no i386 ISO in this release |

Planned download path after approval and a successful build:

```text
https://ming.scallion.uno/iso/ming-os-26.3.3-home-amd64.download
```

OTA endpoint:

```text
https://ming.scallion.uno/api/onion-update/check?version=26.3.2&channel=stable
```

Planned GitHub release:

```text
https://github.com/bzm2008/ming-os/releases/tag/v26.3.3
```

## What Ming OS Is

- A complete desktop system, not just a theme pack.
- A Chinese-friendly daily-use system for older 64-bit hardware.
- A button-first interface that hides command-line complexity behind clear controls.
- A branded installer and installed system, so users do not feel they installed plain Debian.
- A system line that prioritizes bootability, installation success, and easy support diagnostics.

## 26.3.3 Highlights

- Repaired the ISO boot chain and requires BIOS, UEFI, Rufus, Ventoy, and desktop checks before release.
- Keeps BIOS/Legacy and UEFI El Torito boot entries in the ISO.
- Removes problematic GRUB `splash` / `install` kernel parameters while keeping the Ming installer session marker.
- Uses a stable ISO volume label: `MING_OS_2633`.
- Points Calamares `unpackfs.conf` at `/run/ming-installer/filesystem.squashfs`.
- Defaults installer locale/timezone to Chinese usage, including Asia/Shanghai behavior.
- Uses Microsoft Edge as the default browser through a stable Ming launcher.
- Enables Fcitx5 pinyin input by default for GTK, Qt, and X11 applications.
- Hardens Wi-Fi and Bluetooth support with NetworkManager, wpa_supplicant, ModemManager, BlueZ, Blueman, RF kill unblocking, and broad firmware coverage.
- Keeps the desktop installer branded as Ming OS instead of Debian.
- Publishes OTA metadata only after the final ISO size and SHA256 are known.
- Keeps older 64-bit CPUs such as first/second/third-generation i3/i5 and E3 V1/V2 class machines in the support target.

## Core Experience

- Dock-centered workflow with the top taskbar removed.
- Ming Settings as the main control center for common user actions.
- App library and desktop entries designed for users who prefer visible buttons.
- Microsoft Edge is the default browser entry on the desktop and Dock.
- Android-like desktop folders for grouping applications.
- Ming Files is the single file and disk entry; duplicate disk tools are retired.
- Network, driver, printer, and diagnostic tools grouped in Settings rather than scattered on the desktop.
- Low-memory strategy with zram, lighter effects, cleanup helpers, and optional WeChat/WPS installers instead of preinstalling them.
- OTA update flow with readable status, checksum, size, and error messages.

## Install

For most users, download the ISO from the official website and write it with Rufus, Ventoy, or `dd`.

Supported test paths:

- Rufus ISO mode
- Rufus DD mode
- Ventoy Live boot
- VirtualBox DVD boot
- Direct disk write with `dd`

```bash
sudo dd if=ming-os-26.3.3-home-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Live/installer mode should enter the Ming OS installer without stopping at a Debian-branded desktop or a username/password prompt. If an old machine fails, record whether it stops before GRUB, at GRUB, while loading the kernel, or inside Calamares.

## OTA

Normal users should use the System Update page in Ming Settings. Advanced users can still use:

```bash
ming-update check
sudo ming-update apply
```

Expected public OTA response:

```json
{
  "has_update": true,
  "version": "26.3.3",
  "ready": true,
  "status": "ready",
  "update_type": "major",
  "download_url": "https://ming.scallion.uno/iso/ming-os-26.3.3-home-amd64.download",
  "checksum": "1d71a3d71441ca938fc46551d9d6c3cdfc9b5e9a9f2fb7b4eb4fb611360e5e11",
  "checksum_type": "sha256",
  "size": 1992589312
}
```

### 26.3.2 Transition

The 26.3.2 updater can validate, download, stage, and restore a 26.3.3 major
OTA when an independent `/home` or backup disk is available. Its already
installed client predates `grub-reboot`, though, so the first transition still
requires selecting `Ming OS 26.3.3 OTA Installer` once in GRUB. Ming OS 26.3.3
uses `grub-reboot` for later major updates. A fully hands-free 26.3.2 bridge
requires a separately signed updater package and is not represented as already
available here.

## GitHub Assets

If the ISO is split for GitHub Release assets, merge the parts before writing to USB.

```bash
cat ming-os-26.3.3-home-amd64.iso.part* > ming-os-26.3.3-home-amd64.iso
sha256sum -c ming-os-26.3.3-home-amd64.iso.sha256
```

Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.3.3-home-amd64.iso.part01+ming-os-26.3.3-home-amd64.iso.part02 ming-os-26.3.3-home-amd64.iso
Get-FileHash ming-os-26.3.3-home-amd64.iso -Algorithm SHA256
```

The merged file must match the SHA256 generated after the final build and
published alongside the release asset.

## Verification Status

Required validation for the 26.3.3 ISO:

- `xorriso -report_el_torito` shows BIOS and UEFI boot images.
- `/live/vmlinuz` is a valid Linux kernel, not zeroed data.
- BIOS isolinux and UEFI GRUB menus show Ming OS 26.3.3.
- VirtualBox BIOS smoke test reaches the Ming OS 26.3.3 installer, installs, and reboots into the hard disk.
- VirtualBox UEFI smoke test reaches the Ming OS 26.3.3 installer, installs, and reboots into the hard disk.

Recommended remaining field tests:

- Rufus ISO mode and DD mode on a first/second/third-generation Intel machine.
- Ventoy on older BIOS machines.
- A full install to a blank disk, followed by reboot into the installed system.
- Desktop update button check from an older installed Ming/Onion version.

## User Communication

- Ming OS can run on low-memory machines, but optional WeChat/WPS installs may become the largest memory consumers.
- The normal user path should be buttons, Settings, update UI, app folders, and graphical repair tools.
- Command-line usage is for advanced support, not daily operation.
- Ming OS 26.3.3 is published only after the ISO, checksum, GitHub tag, and server manifest are all verified.
