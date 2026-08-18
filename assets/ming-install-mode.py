#!/usr/bin/env python3
"""Fail-closed install-mode contract shared by the Ming Live installer."""

import argparse
import json
import os
import pathlib
import tempfile


SCHEMA = "ming-install-mode/v1"
MODES = {
    "blank_ab": {
        "major_ota": "ab_slot",
        "message": "空白盘自动安装会创建完整 A/B 系统槽，支持 major OTA 和自动回滚。",
    },
    "dual_boot_preserve": {
        "major_ota": "disabled_dual_boot",
        "message": (
            "保留双系统需要手动选择空闲空间；仅支持已签名的 patch/minor 更新，"
            "大版本 A/B OTA 已禁用。"
        ),
    },
}


BLANK_AB_COMMON = """---
userSwapChoices:
  - none
drawNestedPartitions: false
alwaysShowPartitionLabels: true
defaultPartitionTableType: gpt
requiredPartitionTableType: gpt
defaultFileSystemType: "ext4"
availableFileSystemTypes:
  - "ext4"
  - "fat32"
initialPartitioningChoice: none
initialSwapChoice: none
# Blank-disk A/B installation does not use Calamares' optional LUKS widget.
# Keeping it disabled avoids an initial state-notification race that leaves
# the Next button disabled until the checkbox is toggled.
enableLuksAutomatedPartitioning: false
"""


BLANK_AB_BIOS_ESP = """partitionLayout:
  - name: "MING-BIOSBOOT"
    filesystem: "unformatted"
    noEncrypt: true
    type: "21686148-6449-6E6F-744E-656564454649"
    size: 8M
    minSize: 8M
  - name: "MING-ESP"
    filesystem: "fat32"
    noEncrypt: true
    mountPoint: "/boot/efi"
    type: "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    size: 512M
    minSize: 300M
    flags:
      - esp
"""


# Keep UEFI and BIOS on the same explicit partition layout.  Calamares 3.3
# can stall in the partition view while resolving the automatic `efi:` helper
# on a fresh VirtualBox disk, before Ming's exec-stage guards have a chance to
# run.  A concrete MING-ESP entry is also easier for the installer verifier and
# post-install gates to audit.
BLANK_AB_UEFI_ESP = BLANK_AB_BIOS_ESP


BLANK_AB_LAYOUT_TAIL = """
  - name: "MING-BOOT"
    filesystem: "ext4"
    noEncrypt: true
    mountPoint: "/boot"
    size: 1G
  - name: "MING-ROOT-A"
    filesystem: "ext4"
    mountPoint: "/"
    size: 35%
    minSize: 14G
  - name: "MING-ROOT-B"
    filesystem: "ext4"
    noEncrypt: true
    size: 35%
    minSize: 14G
  - name: "MING-HOME"
    filesystem: "ext4"
    mountPoint: "/home"
    size: 100%
    minSize: 8G
requiredStorage: 48
allowManualPartitioning: false
"""


DUAL_BOOT_PARTITION = """---
efiSystemPartition: "/boot/efi"
userSwapChoices:
  - none
  - small
  - file
drawNestedPartitions: true
alwaysShowPartitionLabels: true
defaultFileSystemType: "ext4"
availableFileSystemTypes:
  - "ext4"
initialPartitioningChoice: none
initialSwapChoice: none
requiredStorage: 16
allowManualPartitioning: true
"""


def build_mode_payload(mode):
    if mode not in MODES:
        raise ValueError("unsupported Ming install mode")
    return {
        "schema": SCHEMA,
        "version": 1,
        "mode": mode,
        "major_ota": MODES[mode]["major_ota"],
        "message": MODES[mode]["message"],
    }


def validate_mode_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("install mode must be an object")
    expected = build_mode_payload(payload.get("mode"))
    if payload != expected:
        raise ValueError("install mode fields do not match the selected policy")
    return expected


def detect_firmware(sys_firmware_efi="/sys/firmware/efi"):
    return "uefi" if pathlib.Path(sys_firmware_efi).is_dir() else "bios"


def partition_config(mode, firmware=None):
    build_mode_payload(mode)
    if mode != "blank_ab":
        return DUAL_BOOT_PARTITION
    selected_firmware = firmware or detect_firmware()
    if selected_firmware not in {"bios", "uefi"}:
        raise ValueError("unsupported firmware mode")
    esp = BLANK_AB_UEFI_ESP if selected_firmware == "uefi" else BLANK_AB_BIOS_ESP
    return BLANK_AB_COMMON + esp + BLANK_AB_LAYOUT_TAIL


def _atomic_write(path, content, mode):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise ValueError("install mode target must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_mode(path, mode):
    payload = build_mode_payload(mode)
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        0o600,
    )
    return payload


def write_partition(path, mode, firmware=None):
    _atomic_write(path, partition_config(mode, firmware=firmware), 0o644)


def read_mode(path):
    path = pathlib.Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("install mode receipt is missing or unsafe")
    return validate_mode_payload(json.loads(path.read_text(encoding="utf-8")))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ming-install-mode")
    subcommands = parser.add_subparsers(dest="command", required=True)
    write = subcommands.add_parser("write")
    write.add_argument("mode", nargs="?", choices=sorted(MODES))
    write.add_argument("--mode", dest="mode_option", choices=sorted(MODES))
    write.add_argument("--state", default="/run/ming-installer/install-mode.json")
    write.add_argument("--partition", default="/etc/calamares/modules/partition.conf")
    write.add_argument("--firmware", choices=("auto", "bios", "uefi"), default="auto")
    show = subcommands.add_parser("show")
    show.add_argument("--state", default="/run/ming-installer/install-mode.json")
    args = parser.parse_args(argv)
    if args.command == "write":
        selected = args.mode_option or args.mode
        if not selected:
            parser.error("write requires an install mode")
        write_mode(args.state, selected)
        firmware = None if args.firmware == "auto" else args.firmware
        write_partition(args.partition, selected, firmware=firmware)
        return 0
    payload = read_mode(args.state)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
