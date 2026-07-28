# Ming OS A/B OTA contract

RC2 uses Calamares' native `partitionLayout` for an explicitly selected whole-disk
installation. It creates root A, root B, a shared `/boot` and a separate `/home`,
then writes the root-owned `/etc/ming-update/slots.json` from actual label and UUID
readback. The recipe requires a 48 GiB disk and never becomes the preselected choice.
The shared `/boot` is required so BIOS and UEFI boots read and update the same
GRUB environment regardless of which root slot is mounted.
Each GRUB entry must load its own `/boot/ming-slots/A|B/vmlinuz` and
`initrd.img`; updating one slot never replaces the rollback slot's kernel.

The updater derives the active slot from the mounted root UUID and writes only the
other declared slot. It refuses missing, ambiguous, mounted or UUID-mismatched
targets. It never invokes `parted` or `mkfs`. A one-shot GRUB entry boots the new
slot while the old slot stays the default. The boot health service promotes the
new slot only after version, `/home`, system state and GRUB saved-entry readback
checks pass. Failure requests the previous slot and records rollback state.

Machines without this exact layout retain the existing external-backup major OTA
path. The client does not repartition existing single-root installations.

`ming.sca-hub.cn` is the preferred default, but every default or migration use
passes the same TLS 1.2+, discovery-schema and Ming OS OTA Minisign preflight.
An unsigned `delivery:none` discovery response is recognized as valid schema but
cannot authorize migration; the client falls back to `ming.scallion.uno` until a
signed discovery response is available. Major A/B staging independently refetches
and verifies the authoritative signed manifest before copying the ISO into the
root-only staging directory.

Release acceptance still required:

- verify the Calamares recipe on empty 48 GiB and larger BIOS and UEFI disks;
- verify both `Ming OS slot A/B` GRUB entries can boot and share one GRUB environment;
- verify the layout manifest remains available through updates and rollback;
- run power-loss, corrupt-image, no-space and failed-health VM tests.
