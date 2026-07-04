# Ming OS 26.3.1 Execution Guide

This guide tracks the current public Ming OS `26.3.1` release. Older Onion OS 26.2.x and Ming OS 26.3.0-r* notes are historical context only and must not be treated as the recommended download target.

## Release Artifact

```text
output/ming-os-26.3.1-home-amd64.iso
```

```text
Size:   2698493952
SHA256: 0a23e1ce739ced8daede1b2deb6e7d5879c9b0b44e1ecb43fda0422c2a164df1
Label:  MING_OS_2631
Kernel: 6.12.94+deb13-amd64
```

Official public URL:

```text
https://ming.scallion.uno/iso/ming-os-26.3.1-home-amd64.iso
```

GitHub release:

```text
https://github.com/bzm2008/onion-os/releases/tag/v26.3.1
```

## Must-Pass Checks

- ISO contains `/live/vmlinuz`, `/live/initrd`, and `/live/filesystem.squashfs`.
- Extracted `/live/vmlinuz` is a Linux bzImage, not zeroed data.
- `xorriso -report_el_torito` shows both BIOS and UEFI boot images.
- GRUB menu identifies Ming OS 26.3.1.
- GRUB Linux lines do not include the problematic `splash` / `install` parameters.
- Calamares `unpackfs.conf` points to `/run/ming-installer/filesystem.squashfs`.
- Calamares defaults are Chinese-friendly: Asia/Shanghai timezone, zh_CN.UTF-8 locale, and a safe physical keyboard layout.
- Live/installer flow is Ming-branded, not Debian-branded.
- Locally shipped executables under `/usr/local/bin` and `/usr/local/sbin` must not require AVX/AVX2/x86-64-v3.
- The Settings center contains diagnostics, network repair, driver detection, printer/scanner entry points, lightweight mode, and optional Surface support.

## VirtualBox Smoke Status

The current candidate has passed:

- BIOS VM reaches GRUB and then the Ming OS 26.3.1 installer.
- UEFI VM reaches GRUB and then the Ming OS 26.3.1 installer.
- ISO metadata and kernel checks passed locally before publishing.

This is enough for a guarded public candidate, but broad user promotion should still follow real USB tests on old BIOS and mixed UEFI hardware.

## Field Test Matrix

Prioritize these machines and paths:

- First/second/third-generation Intel i3/i5 notebooks.
- E3 V1/V2 desktop platforms.
- Older AMD desktop platforms.
- ThinkPad X200-class old BIOS machines where 64-bit boot is available.
- Microsoft Surface Pro 1/2/3 only as an optional compatibility target.

For each machine, record:

- Rufus ISO mode result.
- Rufus DD mode result.
- Ventoy result.
- Whether it reaches GRUB, starts loading kernel/initrd, reaches installer, completes installation, and boots installed system.

## OTA Publish Flow

Current OTA endpoint:

```text
https://ming.scallion.uno/api/onion-update/check?version=26.2.0&channel=stable
```

Expected public response:

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

## GitHub Release Assets

If uploading split assets:

```text
ming-os-26.3.1-home-amd64.iso.part01
ming-os-26.3.1-home-amd64.iso.part02
ming-os-26.3.1-home-amd64.iso.sha256
SHA256SUMS
```

Merge and verify:

```bash
cat ming-os-26.3.1-home-amd64.iso.part* > ming-os-26.3.1-home-amd64.iso
sha256sum -c ming-os-26.3.1-home-amd64.iso.sha256
```

## User Communication

- Current recommended release: Ming OS 26.3.1.
- Do not point users to 26.2.x or failed 26.3.0-r builds as the main download.
- Ming OS targets old 64-bit PCs first; 32-bit/i386 remains deferred.
- Ask users with boot issues to report the exact stop point: before GRUB, at GRUB, during kernel load, in installer, or after installed reboot.
- Tell non-technical users to use Settings, the update button, diagnostics, app folders, and graphical repair tools before trying terminal commands.
