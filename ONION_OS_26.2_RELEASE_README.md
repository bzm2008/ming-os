# Onion OS 26.2.6-r2 Release And Website Handoff

This document is the current website and AI handoff source for Onion OS. Use `26.2.6-r2` as the public version. Do not point users to 26.2.0 or 26.2.5 as the recommended release.

## Positioning

Onion OS 26.2.6-r2 is a Debian 13 / Trixie based Chinese desktop system for older PCs and users who prefer buttons over terminal commands. It fixes the 26.2.5 boot regression, improves Live desktop polish, and corrects the installer so the installed system presents itself as Onion OS rather than Debian.

## Public Links

- Official website: `https://scallion.uno`
- ISO download: `https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso`
- ISO SHA256: `f2823efa5545502fb6ec93ad8b476b5821c915ccede8366283f4c560fc26ce25`
- ISO size: `2568552448`
- OTA check: `https://scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`
- GitHub repo: `https://github.com/bzm2008/onion-os`
- GitHub release: `https://github.com/bzm2008/onion-os/releases/tag/v26.2.6-r2`

## Feature Summary

- Debian 13 / Trixie base.
- Rebuilt boot chain for BIOS and UEFI with stable label `ONION_OS_2626`.
- Fixes the 26.2.5 `invalid magic number` and `you need to load the kernel first` class of failures.
- Live/Ventoy auto-login as `onion`.
- Onion wallpaper applies by default.
- Main Onion icons no longer use white-background AI PNG overrides.
- Onion Security Manager opens through a stable wrapper and writes readable logs.
- Live installer is branded as `Install Onion OS` / `安装 Onion OS`.
- Installed OS identity is repaired to Onion OS after installation.
- Desktop update button uses a clear GUI flow.
- Low-memory WeChat strategy: zram, earlyoom, cache cleanup, low-priority launcher, Web WeChat fallback.
- Android-like desktop app folders and automatic app visibility.
- `All Disks` entry combines common user folders and mounted disks to reduce C/D drive anxiety.
- HDD/SSD runtime tuning for schedulers, read-ahead, and dirty writeback.
- Dock-only desktop and Onion Settings reduce reliance on terminal commands.

## GitHub Download Note

The complete ISO is available on the official website. GitHub Release uses split assets:

```text
onion-os-26.2.6-r2-home-amd64.iso.part01
onion-os-26.2.6-r2-home-amd64.iso.part02
onion-os-26.2.6-r2-home-amd64.iso.sha256
SHA256SUMS
```

Merge on Linux/macOS/WSL:

```bash
cat onion-os-26.2.6-r2-home-amd64.iso.part01 onion-os-26.2.6-r2-home-amd64.iso.part02 > onion-os-26.2.6-r2-home-amd64.iso
sha256sum -c onion-os-26.2.6-r2-home-amd64.iso.sha256
```

Merge on Windows PowerShell:

```powershell
cmd /c copy /b onion-os-26.2.6-r2-home-amd64.iso.part01+onion-os-26.2.6-r2-home-amd64.iso.part02 onion-os-26.2.6-r2-home-amd64.iso
Get-FileHash onion-os-26.2.6-r2-home-amd64.iso -Algorithm SHA256
```

## Prompt For Another AI Building The Scallion Product Page

You are a senior product web designer and frontend implementer. Build a Scallion website product page for `Onion OS 26.2.6-r2`. The page should speak to ordinary Chinese users, older-PC users, and users who dislike terminal commands. Do not make it a generic Linux technical page.

Required links:

- ISO download: `https://scallion.uno/iso/onion-os-26.2.6-r2-home-amd64-f2823efa.iso`
- GitHub release: `https://github.com/bzm2008/onion-os/releases/tag/v26.2.6-r2`
- GitHub repo: `https://github.com/bzm2008/onion-os`
- OTA check: `https://scallion.uno/api/onion-update/check?version=26.2.0&channel=stable`

Page goals:

- Explain that Onion OS is a Debian 13 / Trixie based Chinese desktop system.
- Make `Onion OS 26.2.6-r2` the visible product name in the first viewport.
- Highlight boot reliability, Live auto-login, low-memory WeChat support, graphical update button, Onion Settings, Android-like app folders, All Disks, and the Onion-branded installer.
- Tell users clearly that 2GB RAM can run the OS, but a WeChat account with many friends and groups may still be heavy; recommend Web WeChat as the fallback.
- Provide a clear ISO download button, GitHub button, and OTA status area.

Suggested structure:

- Hero: title `Onion OS 26.2.6-r2`; subtitle `给老旧电脑和中文用户的按钮化 Linux 桌面`; buttons `下载 ISO`, `查看 GitHub`, `检查 OTA`.
- Trust strip: SHA256, size, release date, OTA ready status.
- Three cards: `启动更稳`, `不用记命令`, `像手机一样整理应用`.
- Feature section: WeChat low-memory mode, Spark Store, Onion Settings, Onion App Library, All Disks, OTA updates, Security Manager, Onion installer.
- Download section: show official full ISO and GitHub split download instructions.
- Compatibility section: Rufus ISO/DD, Ventoy/Live, BIOS/UEFI, VirtualBox.

Design direction:

- Avoid stock Xfce, Linux Mint, or generic distro visuals.
- Use Onion OS identity: dark glass surfaces, restrained green and violet accents, clear icons, and dense but readable feature blocks.
- Keep language short and concrete. This page is for users who want buttons and confidence, not command-heavy troubleshooting.
- Do not show server passwords, SSH commands, internal deployment notes, or private operations details.
