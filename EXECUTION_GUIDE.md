# Ming OS 26.3.0-r4 Execution Guide

This guide now tracks the Ming OS `26.3.0-r4` development target. Older Onion OS 26.2.x notes below are historical release context only and must not be treated as the current build target.

## 26.3.0-r4 Must-Pass Checks

- Build artifact name: `ming-os-26.3.0-home-amd64-r4.iso`.
- Main architecture: `amd64` only. The 32-bit/i386 system is deferred.
- Boot menu includes the old-computer compatibility entry for first/second/third-generation i3/i5 and E3 V1/V2 class hardware.
- Calamares defaults to `Asia/Shanghai`, `zh_CN.UTF-8`, physical keyboard `us`, and does not fail on the users page with `error loading dictionary`.
- Ming Settings contains `硬件与诊断` with buttons for network repair, driver detection, printer/scanner access, diagnostic bundle generation, classic lightweight mode, and optional Surface support.
- Wi-Fi defaults to NetworkManager + wpa_supplicant; iwd remains available as a switch inside Ming Settings.
- Printer/scanner support is present through CUPS, graphical printer tools, IPP/AirPrint, USB printer support, and scan tools.
- Locally shipped executables under `/usr/local/bin` and `/usr/local/sbin` must not require AVX/AVX2/x86-64-v3.

This guide previously reflected the public `26.2.6-r2` release. Older 26.2.0/26.2.5 notes are historical and should not be used as the recommended download target for new Ming OS builds.

## Release Target

`26.2.6-r2` is the current public replacement for the earlier 26.2.x images. It focuses on:

- boot reliability after 26.2.5 reported `invalid magic number` / `you need to load the kernel first`;
- desktop polish fixes found during VirtualBox testing;
- installer branding so Live installation produces Onion OS, not a Debian-looking install;
- update button and OTA clarity;
- HDD/SSD and low-memory runtime tuning.

This release is intended to be the version you hand to testers, content writers, and other AI systems. It is the current truth for downloads and website copy.

## Final Artifact

```text
output/onion-os-26.2.6-r2-home-amd64-f2823efa.iso
```

```text
Size:   2568552448
SHA256: f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25
```

Official public URL:

```text
https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso
```

GitHub release:

```text
https://github.com/bzm2008/onion-os/releases/tag/v26.2.6-r2
```

## GitHub Release Assets

GitHub uses split assets for the ISO:

```text
onion-os-26.2.6-r2-home-amd64.iso.part01
onion-os-26.2.6-r2-home-amd64.iso.part02
onion-os-26.2.6-r2-home-amd64.iso.sha256
SHA256SUMS
```

Merge and verify:

```bash
cat onion-os-26.2.6-r2-home-amd64.iso.part01 onion-os-26.2.6-r2-home-amd64.iso.part02 > onion-os-26.2.6-r2-home-amd64.iso
sha256sum -c onion-os-26.2.6-r2-home-amd64.iso.sha256
```

## Verification Checklist

Build or repack output must pass:

- `/live/vmlinuz`, `/live/initrd`, and `/live/filesystem.squashfs` exist.
- Extracted `/live/vmlinuz` is a Linux bzImage, not zeroed data.
- ISO volume label is `ONION_OS_2626`.
- `xorriso -report_el_torito` shows both BIOS and UEFI boot images.
- `fdisk -l` should not show the old HFS/APM hybrid layout.
- QEMU or VirtualBox reaches the Onion OS boot menu and starts loading kernel/initrd.
- Live mode auto-logins as `onion`; Ventoy/Live should not ask for username/password.
- Desktop wallpaper is Onion-branded.
- White-background icon overrides are not present on main Onion icons.
- `Onion 安全管家` opens or shows a readable diagnostic log.
- Desktop installer is `Install Onion OS` / `安装 Onion OS`, not `Install Debian`.
- Installed system identity is Onion OS after Calamares completes.
- Desktop update button opens a clear check/download/install flow.

When testing in VirtualBox, check both a clean live session and a post-install session. A file that boots in Live but installs as Debian or loses its wallpaper/theme is still a regression.

## Feature Checklist

The release should visibly provide:

- Onion Settings as the main control center.
- Onion App Library for installed software.
- Android-like desktop folders for app grouping.
- All Disks as the single storage entry point.
- Onion Security Manager for readable system diagnostics.
- Graphical update flow with clear success, pending, and error states.
- Low-memory WeChat launcher and cleanup helpers.
- Dock-only desktop behavior without relying on a top taskbar.

## User Scenarios

Scenario 1:

- User boots from Rufus or Ventoy.
- The system auto-logs in without a password prompt.
- The desktop appears with Onion wallpaper and branded launchers.
- The user clicks a Dock icon or Onion Settings button instead of typing a command.

Scenario 2:

- User runs the update check.
- The client verifies JSON, size, and SHA256.
- If the artifact is ready, the download proceeds.
- If not, the interface says the package is not ready yet instead of failing silently.

Scenario 3:

- User installs to the target disk.
- The installed system remains identified as Onion OS.
- The desktop does not revert to a Debian-branded identity.

## OTA Publish Flow

Current OTA endpoint:

```text
https://scallion.uno/api/onion-update/check?version=26.2.0&channel=stable
```

Expected public response:

```json
{
  "version": "26.2.6-r2",
  "ready": true,
  "status": "ready",
  "download_url": "https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso",
  "checksum": "f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25",
  "size": 2568552448
}
```

Recommended deploy helper:

```bash
python scallion/scripts/deploy-onion-26.2-server.py \
  --check --deploy --verify-public \
  --version 26.2.6-r2 \
  --release-date 2026-06-21 \
  --iso-name onion-os-26.2.6-r2-home-amd64-f2823efa.iso \
  --public-iso-name onion-os-26.2.6-r2-home-amd64-f2823efa.iso \
  --sha256 f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25 \
  --size 2568552448
```

## User Communication

Say plainly:

- Onion OS can run on 2GB RAM, but a heavy WeChat account may still consume too much memory.
- Prefer desktop buttons: Onion Settings, update button, app folders, All Disks, and GUI repair tools.
- Command-line usage is for advanced troubleshooting, not the normal path.
- For Rufus/Ventoy boot issues, test both ISO mode and DD mode, and record the exact error text and machine generation.

## Notes For Support And Content Teams

- When writing screenshots or release posts, use `26.2.6-r2` as the visible version.
- Do not reuse the old `26.2.0` page title for the current release.
- Do not describe the installer as Debian; it is Onion OS.
- The desktop is intended to look like a finished branded system, not a plain Xfce install.
- If you mention the OTA endpoint, remember it is a compatibility query path; the current public version it returns is `26.2.6-r2`.
