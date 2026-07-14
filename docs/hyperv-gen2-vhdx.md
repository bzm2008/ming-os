# Ming OS Hyper-V Gen2 VHDX

`scripts/build-hyperv-gen2-vhdx.sh` produces a standalone VHDX artifact for
**Hyper-V Generation 2** virtual machines. It creates a GPT disk containing a
FAT32 EFI System Partition and an ext4 root filesystem. The firmware fallback
path is `/EFI/BOOT/BOOTX64.EFI` so Hyper-V can boot it without an NVRAM entry.

This is a UEFI-only artifact. Hyper-V **Generation 1 is not supported**: it
uses legacy BIOS rather than UEFI and needs a separate BIOS-oriented image.
Do not upload or describe this VHDX as a Generation 1 cloud image.

## Secure Boot

The generated GRUB fallback is unsigned. Create the Hyper-V Generation 2 VM
with Secure Boot **disabled**. The script records this as
`secure_boot: disabled-required` in the manifest. Turning Secure Boot on is
not a supported workaround and may leave the VM at the Hyper-V firmware screen.

## Prerequisites

Run the generator on a privileged **Linux host**. It intentionally refuses to
run from Windows PowerShell because it must create loop devices and mount
temporary filesystems. Required packages normally include:

```bash
sudo apt install qemu-utils gdisk dosfstools e2fsprogs rsync grub-efi-amd64-bin \
  initramfs-tools squashfs-tools xorriso
```

The source rootfs must contain an installed Linux kernel, initramfs,
`grub-install`, `update-grub`, and `update-initramfs`. The generator stops
before writing an output VHDX when a required command or source component is
missing.

## Build From a Root Filesystem

Use the completed build root filesystem whenever possible. This produces an
installed, directly bootable disk rather than a Live installer disk.

```bash
sudo scripts/build-hyperv-gen2-vhdx.sh \
  --rootfs /path/to/chroot \
  --output output/ming-os-26.3.3-hyperv-gen2.vhdx \
  --size 24G
```

The root volume label defaults to `MING_OS_2633`; override it only when a
release uses a different documented label.

## Build From an ISO

The ISO mode extracts `/live/filesystem.squashfs` and is useful for a limited
preview or recovery artifact:

```bash
sudo scripts/build-hyperv-gen2-vhdx.sh \
  --iso output/ming-os-26.3.3-home-amd64.iso \
  --output output/ming-os-26.3.3-hyperv-gen2.vhdx
```

The generator converts the copied Live persona to an installed Xfce/LightDM
desktop target before publication and removes the Live Calamares session and
autostart entries. ISO mode still does not replace a full installed-system
regression test. It is not a fully tested release artifact until a Hyper-V
Generation 2 VM reaches the desktop, reconnects networking, and completes a
reboot check.

## Verify Before Uploading

Each output has adjacent checksum and manifest files:

```text
ming-os-26.3.3-hyperv-gen2.vhdx
ming-os-26.3.3-hyperv-gen2.vhdx.sha256
ming-os-26.3.3-hyperv-gen2.vhdx.manifest.json
```

Run the inspector on Linux before publishing to a cloud firmware service:

```bash
sudo scripts/inspect-hyperv-gen2-vhdx.sh \
  --image output/ming-os-26.3.3-hyperv-gen2.vhdx
```

It validates the VHDX format, GPT layout, FAT32 ESP, ext4 root label,
`EFI/BOOT/BOOTX64.EFI`, GRUB configuration, SHA256 sidecar, manifest, and the
`hv_vmbus`, `hv_storvsc`, `hv_netvsc`, and `hid_hyperv` modules in the latest
initramfs. It also rejects unresolved Calamares root UUID placeholders and a
remaining Live/installer desktop persona.

## Hyper-V Creation Boundary

Create a new Generation 2 VM, attach this VHDX as its boot disk, set the
firmware boot order to the disk, and leave Secure Boot disabled. This release
does not provide a Generation 1 VHDX, a legacy BIOS boot path, a signed Secure
Boot chain, or a claim of compatibility with every cloud firmware importer.
