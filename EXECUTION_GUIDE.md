# Ming OS 26.4.1 RC4 Execution Guide

本文件对应当前工作树的 Ming OS 26.4.1 RC4。当前源码基线为提交 `a5b9295`；文档中的构建、验收和发布结论必须以重新核对该提交后的实际产物为准。

## 当前基线

- 版本：`Ming OS 26.4.1 RC4`
- 当前提交：`a5b9295`（构建时由 `BUILD_SOURCE_COMMIT` 写入构建元数据）
- 构建入口：`./build_onion_os.sh`
- 构建参数：`--fresh` 用于全新构建，`--resume` 用于继续匹配检查点的构建；本文件不代替实际构建记录。
- 预期产物：`output/ming-os-26.4.1-home-amd64.iso`（实际输出目录以构建脚本配置为准）

## 历史证据

Ming OS 26.3.2 以及更早版本只作为历史测试、回归和迁移证据（historical evidence only）。它们不是当前推荐版本、当前下载目标或当前 RC4 验收对象；旧 26.3.2 的 ISO、截图、哈希和启动记录不能证明当前提交的 RC4 已通过验收。

## 构建入口与追溯

在 Debian 13/Trixie 构建主机上，从仓库根目录执行：

```bash
sudo ./build_onion_os.sh --fresh
```

构建必须记录并核对：源码提交、构建 ID、构建时间、ISO 路径和 SHA256。当前工作树若不是 `a5b9295`，或存在未提交/未跟踪变化，不能把本文件的基线描述套用到该构建。

## 验收边界

以下是独立边界，不能互相替代：

- 构建验证：构建退出成功、构建元数据中的版本为 `26.4.1`、RC4 build ID 与源码提交可追溯。
- ISO 验证：检查 `/live/vmlinuz`、`/live/initrd`、`/live/filesystem.squashfs`，并确认 BIOS 与 UEFI 的 El Torito 启动项。
- Live 启动：确认 ISO 能进入 Live/安装器；Live 启动不等于已经安装成功。
- 安装版验收：分别在 BIOS 和 UEFI 完成安装、首次启动和重启，检查 `/etc/ming-os-build.json` 与目标 RC4 身份一致。
- 安装后功能：检查桌面、网络、Firefox、Ming Store、设置中心、文件管理器和关键硬件路径；Live 启动或构建成功都不能替代安装版验收。
- OTA：单独验证签名、版本/构建 ID 前进、A/B 槽确认和失败回滚；OTA 通过不等于 ISO 安装版通过。

在没有新鲜的安装版证据前，不得把 RC4 称为已发布或广泛推荐版本。不要构建、部署或执行 OTA 来“补齐”本文件；这些是后续独立操作。

## RC4 验收记录模板

```text
source_commit:
build_id:
iso_path:
iso_sha256:
BIOS Live:
UEFI Live:
BIOS installed first boot/reboot:
UEFI installed first boot/reboot:
desktop and core apps:
OTA signature/version/rollback:
open blockers:
```

## 应用安装边界

QQ、Listen1 等第三方应用不由 `chroot_install_apps.sh` 直接联网下载或用 apt 安装。用户应优先使用 Ming Store；需要本地安装时，必须使用 Ming OS 已审计的本地受控安装入口并记录来源、哈希和安装结果。该脚本现在只用于明确拒绝旧的直装路径。

## 用户沟通

- 当前讨论对象是 Ming OS 26.4.1 RC4，不是历史 26.3.2。
- 未完成安装版 BIOS/UEFI 和 OTA 边界验证前，使用“待验收”而不是“已发布”。
- 遇到启动问题时记录停在 BIOS、GRUB、内核/initrd、Live、安装器还是安装后首次启动。
