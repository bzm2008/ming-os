# Ming OS 26.3.0-r4 Home Edition

Ming OS is a Debian 13 / Trixie based Chinese desktop system for older PCs, family machines, and users who prefer buttons over terminal commands. The current development target is `26.3.0-r4`, focused on old 64-bit PC compatibility and moving diagnostic/repair tools into Ming Settings.

> Historical note: older sections in this repository may still mention Onion OS 26.2.x. The active product name for this development line is Ming OS, and the active build suffix is `r4`.

The design goal is simple: make Linux feel usable without asking the user to become a Linux expert first. The common actions live where people expect them to live: Dock, desktop icons, app folders, settings buttons, a visible update flow, and a branded installer.

## What Ming OS Is

- A complete desktop system, not just a theme pack.
- A Chinese-friendly daily-use system for older hardware.
- A button-first interface that hides command-line complexity behind clear controls.
- A release family that is meant to boot, install, update, and explain itself clearly.

## Current Release

| Item | Value |
| --- | --- |
| Version | 26.3.0-r4 Home Edition |
| Base | Debian 13 / Trixie |
| Desktop | Xfce + Plank Dock + Ming desktop tools |
| ISO | `ming-os-26.3.0-home-amd64-r4.iso` after the next successful build |
| CPU target | Debian amd64 baseline; supports old 64-bit CPUs without AVX2 |
| 32-bit status | Deferred; no i386 ISO in this round |

## 26.3.0-r4 Compatibility Focus

- Keeps first/second/third-generation i3/i5 and E3 V1/V2 class CPUs in scope.
- Adds a GRUB "old computer compatibility mode" for old BIOS, old graphics, and fragile PCI/MSI setups.
- Defaults Wi-Fi to NetworkManager + wpa_supplicant, with an iwd switch available in Ming Settings.
- Adds printer/scanner support through CUPS, IPP/AirPrint, USB printer support, and a graphical printer panel.
- Fixes the Calamares password page class of failures caused by missing/broken pwquality dictionaries.
- Puts network repair, driver detection, diagnostic bundle generation, classic lightweight mode, printer tools, and optional Surface support inside Ming Settings.

## Who It Is For

- Users who want a Chinese desktop that feels straightforward and uncluttered.
- Older PCs with limited CPU, memory, or storage headroom.
- People who prefer buttons, menus, and visible entry points over terminal commands.
- Users who want a desktop that is easy to explain to family members or non-technical users.
- Users who want the system to stay branded as Onion OS after installation.

## System Shape

Onion OS keeps the desktop intentionally simple and recognizable:

- top taskbar removed;
- Dock-centered workflow;
- Onion Settings as the main control panel;
- Onion App Library for installed apps;
- Android-like desktop folders for grouping software;
- All Disks as a single storage entry point;
- branded wallpaper and installer identity;
- graphical update tooling with readable status.

## Core Experience

- Dock-only desktop: the top taskbar is hidden, and the main launch area lives in the bottom Dock.
- Onion Settings: common actions are collected into one control panel instead of scattered through the stock Xfce settings app.
- Onion App Library: installed software is organized in a phone-like launcher so apps are easier to find.
- Android-like desktop folders: apps are grouped into folders such as Common, Apps, System, Internet, Office, Media, and Tools.
- All Disks: the desktop exposes a single entry for the system disk and mounted data disks, reducing the feeling that files are split across too many places.
- Live auto-login: Ventoy and Live boot are configured to auto-login as `onion` so the boot path does not stop at a username prompt.
- OTA update flow: the update button checks a manifest, validates SHA256 and size, and only then proceeds to download or install.
- Installed-system identity repair: a completed install still presents itself as Onion OS instead of Debian.

## Signature Features

### Onion Settings

The system control center brings together the most common actions that older users and support staff need:

- appearance and wallpaper;
- update status;
- storage view;
- security tools;
- network and system basics;
- convenience toggles that would otherwise require several separate panels.

### Onion App Library

Installed applications are shown in a launcher that feels closer to a phone home screen than a stock Linux menu. This makes it easier to find software without learning the full Xfce application structure first.

### Android-like Desktop Folders

The desktop can group apps into readable folders. This helps keep the workspace tidy and makes the system easier to hand to less technical users.

### All Disks

Instead of making users think about every mounted partition separately, Onion OS presents a simpler storage entry that reduces the feeling of C and D drive fragmentation.

### Low-Memory WeChat Support

Onion OS is tuned so the desktop itself stays light. WeChat can still become the main memory pressure point when an account has many friends or groups, so the system adds practical protections such as zram, cleanup helpers, lower-overhead effects, and a low-memory launcher path.

### On-Device Updates

The update flow is designed to be visible and checkable. Users should see a clear status instead of a silent failure or a broken button.

## What Changed In 26.2.6-r2

- Fixed the bad 26.2.5 boot chain that could show `invalid magic number` / `you need to load the kernel first`.
- Kept the 26.2.6 bootfix ISO layout: BIOS + UEFI El Torito, isohybrid MBR, stable volume label `ONION_OS_2626`.
- Rebuilt the desktop experience so the Onion wallpaper is applied by default instead of the Debian wallpaper.
- Removed broad opaque PNG icon overrides that caused white square borders on desktop and Dock icons.
- Fixed Onion Security Manager startup by using a stable wrapper and readable logs.
- Rebranded the Live installer from Debian to Onion OS: desktop entry, Calamares branding, and installed system identity.
- Added installed-system identity repair so a completed install presents itself as Onion OS rather than Debian.
- Added HDD/SSD runtime tuning for schedulers, read-ahead, and writeback behavior.
- Continued the Android-like app folders, `All Disks` entry, low-command workflow, Dock-only desktop, update button flow, and low-memory WeChat strategy.
- Expanded release documentation so GitHub, the website, and AI handoff text all describe the same public version and feature set.

## Feature Highlights

- Low-memory WeChat mode that lowers startup pressure and keeps the system responsive first.
- Spark Store entry for a lightweight Chinese software installation path.
- Onion Security Manager for firewall, logs, and system status in one place.
- Onion wallpaper and branded installer so Live mode looks like Onion OS from the start.
- App icons and Dock assets tuned to avoid the white-card look that older PNG overrides caused.
- Runtime tuning for HDD machines, including read-ahead and scheduler behavior.
- Built-in checks so the update flow can distinguish between pending artifacts and ready releases.
- Desktop-first workflows for users who want to click instead of type.
- Visual consistency across Live boot, installed system, update UI, and website copy.

## Typical Use Cases

- Boot an older office PC and get straight to a simple desktop.
- Install software through buttons and visible menus instead of terminal commands.
- Use WeChat on lower-memory hardware without letting the whole system feel frozen.
- Hand the machine to a non-technical family member without teaching command-line basics first.
- Update the system through the desktop and confirm the result with readable status.

## Visual Identity

The desktop is meant to feel like Onion OS at a glance:

- dark glass surfaces;
- restrained green and violet accents;
- centered Dock at the bottom;
- branded wallpaper;
- Onion-specific desktop shortcuts;
- common actions shown as buttons rather than hidden commands.

This release is intentionally not styled like stock Xfce.

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

Supported boot/write paths:

- Rufus ISO mode
- Rufus DD mode
- Ventoy Live boot
- VirtualBox DVD boot
- Direct disk write with `dd`

```bash
sudo dd if=onion-os-26.2.6-r2-home-amd64-f2823efa.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Live mode should auto-login as `onion`. The desktop installer is branded as `Install Onion OS` / `安装 Onion OS`; it should no longer appear as `Install Debian`.

If you are testing on an older machine:

- Try both ISO mode and DD mode.
- Record the exact boot error text if it fails.
- Check whether the machine enters the boot menu, starts loading the kernel, or stops before GRUB hands off to Linux.

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

The OTA client and website are built to avoid the old failure mode where users were handed a dead or unfinished artifact. If the ISO is not ready, the server should say so clearly instead of advertising a broken download.

## Installation And Boot Notes

Supported boot/write paths:

- Rufus ISO mode
- Rufus DD mode
- Ventoy Live boot
- VirtualBox DVD boot
- direct disk write with `dd`

For older Intel machines, boot behavior should be checked in both ISO and DD mode because BIOS implementations can behave differently.

Live mode should auto-login as `onion`. The desktop installer is branded as `Install Onion OS` / `安装 Onion OS`; it should not appear as `Install Debian`.

If you are troubleshooting boot issues, note the exact stage:

- never reached GRUB;
- reached GRUB but stopped before kernel load;
- kernel started and then failed;
- Live boot worked but install identity was wrong.

## Low-Memory Notes

Onion OS itself is designed to stay usable on low-memory machines, but WeChat can still be the main pressure source when an account has many friends or group chats. Onion OS uses zram, earlyoom, lower-cost desktop effects, cache cleanup, and a low-memory WeChat launcher to protect the system first.

Practical advice:

- 2GB RAM can run Onion OS, but the WeChat account shape matters more than the OS shell.
- Heavy friend lists or many large groups may still push memory pressure high.
- Web WeChat remains the safest fallback for very busy accounts.
- HDD machines benefit from the runtime tuning, but they are still not SSDs.

## FAQ

### Is Onion OS just Debian with a wallpaper?

No. It is a Debian 13 / Trixie based system with its own boot behavior, desktop organization, installer branding, update flow, and user-facing controls.

### Can it run on 2GB RAM?

Yes, the desktop can run on 2GB RAM. The limiting factor is usually the user's WeChat workload and other heavy applications.

### Does Live mode ask for a password?

It should not. Live and Ventoy flows are configured for auto-login.

### Does the installed system still say Debian?

It should not. The install flow is branded to preserve Onion OS identity after installation.

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

## Release Links To Share

- Download ISO: `https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso`
- GitHub release: `https://github.com/bzm2008/onion-os/releases/tag/v26.2.6-r2`
- OTA: `https://scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`
