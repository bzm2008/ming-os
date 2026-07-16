# Ming OS 26.3.2 Execution Guide

This guide tracks the Ming OS `26.3.2` release candidate. Older Onion OS 26.2.x and Ming OS 26.3.0-r* notes are historical context only and must not be treated as the recommended download target.

## Release Artifact

```text
output/ming-os-26.3.2-home-amd64.iso
```

```text
Size:   1984790528 bytes
SHA256: 09f8f8493a539b37ad1973e5cbcb74db138c33eb3c01fd77f0ee6ad1b61f220c
Label:  MING_OS_2632
Kernel: 6.12.94+deb13-amd64
```

Official public URL:

```text
https://ming.scallion.uno/iso/ming-os-26.3.2-home-amd64.download
```

GitHub release:

```text
https://github.com/bzm2008/ming-os/releases/tag/v26.3.2
```

## Must-Pass Checks

- ISO contains `/live/vmlinuz`, `/live/initrd`, and `/live/filesystem.squashfs`.
- Extracted `/live/vmlinuz` is a Linux bzImage, not zeroed data.
- `xorriso -report_el_torito` shows both BIOS and UEFI boot images.
- BIOS isolinux and UEFI GRUB menus identify Ming OS 26.3.2.
- GRUB Linux lines do not include the problematic `splash` / `install` parameters.
- Calamares `unpackfs.conf` points to `/run/ming-installer/filesystem.squashfs`.
- Calamares defaults are Chinese-friendly: Asia/Shanghai timezone, zh_CN.UTF-8 locale, and a safe physical keyboard layout.
- Live/installer flow is Ming-branded, not Debian-branded.
- Locally shipped executables under `/usr/local/bin` and `/usr/local/sbin` must not require AVX/AVX2/x86-64-v3.
- The Settings center contains diagnostics, network repair, driver detection, printer/scanner entry points, lightweight mode, and optional Surface support.

## VirtualBox Smoke Status

The current candidate must pass before publishing:

- BIOS VM reaches GRUB, completes installation, and reboots into the installed system.
- UEFI VM reaches GRUB, completes installation, and reboots into the installed system.
- ISO metadata, kernel, El Torito, isohybrid, and isolinux checks pass locally before publishing.

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
  "version": "26.3.2",
  "ready": true,
  "status": "ready",
  "download_url": "https://ming.scallion.uno/iso/ming-os-26.3.2-home-amd64.download",
  "checksum": "09f8f8493a539b37ad1973e5cbcb74db138c33eb3c01fd77f0ee6ad1b61f220c",
  "checksum_type": "sha256",
  "size": 1984790528
}
```

### OTA discovery 备用域名

`ming.scallion.uno` 仍是当前主地址。为域名失效后的迁移预留
`https://ming.sca-hun.cn/api/onion-update/check`，但新域名尚未备案，当前版本
默认不发送请求。完成备案、证书、API parity 和签名响应核验后，才可由发布负责人
在受控配置中开启 fallback；它不会放宽任何 manifest、payload 或签名门禁。

## GitHub Release Assets

If uploading split assets:

```text
ming-os-26.3.2-home-amd64.iso.part01
ming-os-26.3.2-home-amd64.iso.part02
ming-os-26.3.2-home-amd64.iso.sha256
SHA256SUMS
```

Merge and verify:

```bash
cat ming-os-26.3.2-home-amd64.iso.part* > ming-os-26.3.2-home-amd64.iso
sha256sum -c ming-os-26.3.2-home-amd64.iso.sha256
```

## User Communication

- Current recommended release: Ming OS 26.3.2.
- Do not point users to 26.2.x or failed 26.3.0-r builds as the main download.
- Ming OS targets old 64-bit PCs first; 32-bit/i386 remains deferred.
- Ask users with boot issues to report the exact stop point: before GRUB, at GRUB, during kernel load, in installer, or after installed reboot.
- Tell non-technical users to use Settings, the update button, diagnostics, app folders, and graphical repair tools before trying terminal commands.
