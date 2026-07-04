# Ming OS 26.3.1 Home Edition

Ming OS is a Debian 13 / Trixie based Chinese desktop system for older PCs, family machines, and users who prefer buttons over terminal commands. The current public release is `26.3.1`, focused on reliable BIOS/UEFI boot, a branded installer, Chinese defaults, old 64-bit PC compatibility, and a small but polished desktop experience.

## Current Release

| Item | Value |
| --- | --- |
| Version | 26.3.1 Home Edition |
| Base | Debian 13 / Trixie |
| Kernel | Debian 6.12 LTS family in the current ISO |
| Desktop | Xfce + Plank Dock + Ming desktop tools |
| ISO | `ming-os-26.3.1-home-amd64.iso` |
| Size | `2698493952` bytes |
| SHA256 | `0a23e1ce739ced8daede1b2deb6e7d5879c9b0b44e1ecb43fda0422c2a164df1` |
| CPU target | Debian amd64 baseline; old 64-bit CPUs without AVX2 remain in scope |
| 32-bit status | Deferred; no i386 ISO in this release |

Official download:

```text
https://ming.scallion.uno/iso/ming-os-26.3.1-home-amd64.iso
```

OTA endpoint:

```text
https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable
```

GitHub release:

```text
https://github.com/bzm2008/onion-os/releases/tag/v26.3.1
```

## What Ming OS Is

- A complete desktop system, not just a theme pack.
- A Chinese-friendly daily-use system for older 64-bit hardware.
- A button-first interface that hides command-line complexity behind clear controls.
- A branded installer and installed system, so users do not feel they installed plain Debian.
- A system line that prioritizes bootability, installation success, and easy support diagnostics.

## 26.3.1 Highlights

- Repaired the ISO boot chain and verified BIOS and UEFI VirtualBox startup into the Ming OS 26.3.1 installer.
- Keeps BIOS/Legacy and UEFI El Torito boot entries in the ISO.
- Removes problematic GRUB `splash` / `install` kernel parameters while keeping the Ming installer session marker.
- Uses a stable ISO volume label: `MING_OS_2631`.
- Points Calamares `unpackfs.conf` at `/run/ming-installer/filesystem.squashfs`.
- Defaults installer locale/timezone to Chinese usage, including Asia/Shanghai behavior.
- Keeps the desktop installer branded as Ming OS instead of Debian.
- Publishes OTA metadata with exact ISO size and SHA256 so the update button can detect wrong artifacts.
- Keeps older 64-bit CPUs such as first/second/third-generation i3/i5 and E3 V1/V2 class machines in the support target.

## Core Experience

- Dock-centered workflow with the top taskbar removed.
- Ming Settings as the main control center for common user actions.
- App library and desktop entries designed for users who prefer visible buttons.
- Android-like desktop folders for grouping applications.
- All Disks entry to reduce anxiety around separate C/D-style partitions.
- Network, driver, printer, and diagnostic tools grouped in Settings rather than scattered on the desktop.
- Low-memory strategy with zram, lighter effects, cleanup helpers, and a practical WeChat path.
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
sudo dd if=ming-os-26.3.1-home-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Live/installer mode should enter the Ming OS installer without stopping at a Debian-branded desktop or a username/password prompt. If an old machine fails, record whether it stops before GRUB, at GRUB, while loading the kernel, or inside Calamares.

## OTA

Normal users should click the desktop update button. Advanced users can still use:

```bash
onion-update check
onion-update download
sudo onion-update install
```

Expected public OTA response:

```json
{
  "version": "26.3.1",
  "ready": true,
  "status": "ready",
  "download_url": "https://ming.scallion.uno/iso/ming-os-26.3.1-home-amd64.iso",
  "checksum": "0a23e1ce739ced8daede1b2deb6e7d5879c9b0b44e1ecb43fda0422c2a164df1",
  "checksum_type": "sha256",
  "size": 2698493952
}
```

## GitHub Assets

If the ISO is split for GitHub Release assets, merge the parts before writing to USB.

```bash
cat ming-os-26.3.1-home-amd64.iso.part* > ming-os-26.3.1-home-amd64.iso
sha256sum -c ming-os-26.3.1-home-amd64.iso.sha256
```

Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.3.1-home-amd64.iso.part01+ming-os-26.3.1-home-amd64.iso.part02 ming-os-26.3.1-home-amd64.iso
Get-FileHash ming-os-26.3.1-home-amd64.iso -Algorithm SHA256
```

The merged file must match:

```text
0a23e1ce739ced8daede1b2deb6e7d5879c9b0b44e1ecb43fda0422c2a164df1
```

## Verification Status

Local validation for the current ISO:

- `xorriso -report_el_torito` shows BIOS and UEFI boot images.
- `/live/vmlinuz` is a valid Linux kernel, not zeroed data.
- GRUB menu shows Ming OS 26.3.1.
- VirtualBox BIOS smoke test reaches the Ming OS 26.3.1 installer.
- VirtualBox UEFI smoke test reaches the Ming OS 26.3.1 installer.

Recommended remaining field tests:

- Rufus ISO mode and DD mode on a first/second/third-generation Intel machine.
- Ventoy on older BIOS machines.
- A full install to a blank disk, followed by reboot into the installed system.
- Desktop update button check from an older installed Ming/Onion version.

## User Communication

- Ming OS can run on low-memory machines, but WeChat itself may become the largest memory consumer when the account has many friends or groups.
- The normal user path should be buttons, Settings, update UI, app folders, and graphical repair tools.
- Command-line usage is for advanced support, not daily operation.
- Current official release is Ming OS 26.3.1. Do not recommend older 26.2.x or failed 26.3.0-r builds to new users.
