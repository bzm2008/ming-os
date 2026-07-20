# Ming OS 26.4.0 正式版

Ming OS is a Debian 13 / Trixie based Chinese desktop system for older PCs, family machines, and users who prefer buttons over terminal commands. The current release target is `26.4.0`, focused on reliable BIOS/UEFI boot, a branded installer, Chinese defaults, old 64-bit PC compatibility, and a stable Ming desktop experience.

## Current Release

| Item | Value |
| --- | --- |
| Version | 26.4.0 正式版（事务版本 26.4.0.1） |
| Base | Debian 13 / Trixie |
| Kernel | Debian 6.12 LTS family in the current ISO |
| Desktop | Xfce + Plank Dock + Ming desktop tools |
| ISO | `ming-os-26.4.0-home-amd64-formal.iso` |
| Size | `<FINAL_ISO_SIZE_AFTER_BUILD>` |
| SHA256 | `<FINAL_ISO_SHA256_AFTER_BUILD>` |
| Release state | Formal candidate; website and transactional OTA remain disabled until signed release validation passes |
| CPU target | Debian amd64 baseline; old 64-bit CPUs without AVX2 remain in scope |
| 32-bit status | Deferred; no i386 ISO in this release |

Official download path:

```text
https://ming.scallion.uno/iso/ming-os-26.4.0-home-amd64-formal.download
```

OTA endpoint:

```text
https://ming.scallion.uno/api/onion-update/check?version=26.3.2&channel=stable
```

### OTA discovery domain failover

The current discovery endpoint remains `ming.scallion.uno`. A reserved backup
endpoint is recorded for the day the primary domain is retired:

```text
https://ming.sca-hub.cn/api/onion-update/check
```

The backup is intentionally **disabled** in this release because the domain is
not yet备案 and must not receive production requests. The client only enables
it after the release owner has verified备案, HTTPS certificate coverage, API
parity, and signed discovery responses. Enabling a transport fallback does not
change manifest, payload, content-index, version, architecture, or signature
checks; the OTA schema identifiers continue to use the primary contract name.

GitHub release:

```text
https://github.com/bzm2008/ming-os/releases/tag/v26.4.0.1
```

## What Ming OS Is

- A complete desktop system, not just a theme pack.
- A Chinese-friendly daily-use system for older 64-bit hardware.
- A button-first interface that hides command-line complexity behind clear controls.
- A branded installer and installed system, so users do not feel they installed plain Debian.
- A system line that prioritizes bootability, installation success, and easy support diagnostics.

## 26.4.0 Highlights

- Repaired the ISO boot chain and requires BIOS, UEFI, Rufus, Ventoy, and desktop checks before release.
- Keeps BIOS/Legacy and UEFI El Torito boot entries in the ISO.
- Removes problematic GRUB `splash` / `install` kernel parameters while keeping the Ming installer session marker.
- Uses a stable ISO volume label: `MING_OS_2640`.
- Points Calamares `unpackfs.conf` at `/run/ming-installer/filesystem.squashfs`.
- Defaults installer locale/timezone to Chinese usage, including Asia/Shanghai behavior.
- Uses Microsoft Edge as the default browser through a stable Ming launcher.
- Enables Fcitx5 pinyin input by default for GTK, Qt, and X11 applications.
- Hardens Wi-Fi and Bluetooth support with NetworkManager, wpa_supplicant, ModemManager, BlueZ, Blueman, RF kill unblocking, and broad firmware coverage.
- Keeps the desktop installer branded as Ming OS instead of Debian.
- Publishes transactional OTA metadata only after signed manifest, content index, payload and bootstrap are available; the current discovery endpoint fails closed with `delivery:none`.
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
sudo dd if=ming-os-26.4.0-home-amd64-formal.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Live/installer mode should enter the Ming OS installer without stopping at a Debian-branded desktop or a username/password prompt. If an old machine fails, record whether it stops before GRUB, at GRUB, while loading the kernel, or inside Calamares.

## OTA

Normal users should use the System Update page in Ming Settings. Advanced users can still use:

```bash
ming-update check
sudo ming-update apply
```

Legacy website download response (transactional discovery remains separate):

```json
{
  "has_update": false,
  "version": "26.4.0.1",
  "ready": false,
  "status": "signed-metadata-pending",
  "update_type": "major",
  "download_url": null,
  "checksum": null,
  "checksum_type": "sha256",
  "size": null,
  "transactional_available": false,
  "ota_status": "signed-metadata-pending"
}
```

### 26.3.2 Transition

The 26.3.2 client must first install the official signed bootstrap. The
transactional path remains unavailable until the release owner publishes and
verifies a manifest with `from_versions: ["26.3.2", "26.3.3", "26.4.0"]`,
target `version: "26.4.0.1"`, and a matching content index,
payload and detached signatures. Until then the discovery endpoint returns
`delivery:none`; no manual GRUB step or recovery-ISO shortcut is offered. Recovery
ISO updates retain their independent-backup-media safety gate.

## GitHub Assets

If the ISO is split for GitHub Release assets, merge the parts before writing to USB.

```bash
cat ming-os-26.4.0-home-amd64-formal.iso.part* > ming-os-26.4.0-home-amd64-formal.iso
sha256sum -c ming-os-26.4.0-home-amd64-formal.iso.sha256
```

Windows PowerShell:

```powershell
cmd /c copy /b ming-os-26.4.0-home-amd64-formal.iso.part01+ming-os-26.4.0-home-amd64-formal.iso.part02 ming-os-26.4.0-home-amd64-formal.iso
Get-FileHash ming-os-26.4.0-home-amd64-formal.iso -Algorithm SHA256
```

The merged file must match the SHA256 generated after the final build and
published alongside the release asset.

## Verification Status

## Release Trust Operations

Release operators must read [`docs/releases/ming-release-vault-operations.md`](docs/releases/ming-release-vault-operations.md)
before building or publishing 26.4.0. The release gate is read-only and
requires the reviewed public keyring, policy, receipt, local encrypted bundle,
sidecar and fixed NAS verification. Encrypted recovery bundles and all private
credentials stay outside GitHub, the website and the ISO build context.

```bash
python3 tools/ming-release-vault.py preflight --mode release --config /path/to/release-preflight.json
```

Only a JSON result with `status=ok` permits a release build. A missing official
trust root or a failed NAS check must freeze publication; do not create a
replacement signing key.

Required validation for the 26.4.0 ISO:

- `xorriso -report_el_torito` shows BIOS and UEFI boot images.
- `/live/vmlinuz` is a valid Linux kernel, not zeroed data.
- BIOS isolinux and UEFI GRUB menus show Ming OS 26.4.0.
- VirtualBox BIOS smoke test reaches the Ming OS 26.4.0 installer, installs, and reboots into the hard disk.
- VirtualBox UEFI smoke test reaches the Ming OS 26.4.0 installer, installs, and reboots into the hard disk.

Recommended remaining field tests:

- Rufus ISO mode and DD mode on a first/second/third-generation Intel machine.
- Ventoy on older BIOS machines.
- A full install to a blank disk, followed by reboot into the installed system.
- Desktop update button check from an older installed Ming/Onion version.

## User Communication

- Ming OS can run on low-memory machines, but optional WeChat/WPS installs may become the largest memory consumers.
- The normal user path should be buttons, Settings, update UI, app folders, and graphical repair tools.
- Command-line usage is for advanced support, not daily operation.
- Ming OS 26.4.0 is published only after the ISO, checksum, GitHub tag, and server manifest are all verified.
