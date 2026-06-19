# Onion OS 26.2.0 - 执行指南

## 当前目标

26.2.0 是一次面向真实用户反馈的维护发布：修复 ISO 启动直接进入 `Welcome to GRUB` 的问题，把系统底座推进到 Debian 13/Trixie，并针对“2GB 内存 + 微信好友/群组较多”场景做系统和应用双层优化。

## 本轮改动清单

| 文件 | 重点 |
| --- | --- |
| `build_onion_os.sh` | 版本升至 26.2.0；Debian suite 改为 `trixie`；BIOS/UEFI 手工 ISO fallback 内嵌 early GRUB 配置 |
| `modules/01_base.sh` | Trixie 源、zram、earlyoom、irqbalance、运行时内存 profile、日志限容 |
| `modules/02_apps.sh` | 官方微信 Linux deb、`onion-wechat` 低内存包装器、星火应用商店 |
| `modules/03_desktop.sh` | Dock/菜单入口改为 Onion 设置任务面板、星火商店和官方微信；低内存时关闭重动画并缩小界面 |
| `modules/06_ota_update.sh` | OTA 客户端重试、JSON 校验、SHA256/size 校验、pending artifact 识别、GRUB OTA 启动项 |
| `config/preseed.cfg` | 安装源切换到 Trixie |

## 构建环境

- Linux 宿主：Debian 13、Debian 12、Ubuntu 22.04+ 或 WSL Debian
- 权限：root
- 磁盘：至少 30GB 可用空间
- 内存：推荐 8GB；构建机最低不建议低于 4GB
- 网络：需要访问 Debian 镜像、腾讯微信 deb、WPS、星火应用商店发布页

## 构建命令

```bash
cd onion-os
chmod +x build_onion_os.sh modules/*.sh
sudo ./build_onion_os.sh
```

成功后应得到：

```text
output/onion-os-26.2.0-home-amd64.iso
```

本轮最终验证镜像：

```text
SHA256: f96bb22b7840c186a1f089a44698ce26e44521a5dfed47525edd4033794dcf82
```

## 验证重点

构建后至少检查：

- ISO 内存在 `/live/vmlinuz`、`/live/initrd`、`/live/filesystem.squashfs`。
- ISO 内存在 `/boot/grub/grub.cfg`，菜单包含 `启动 Onion OS 26.2.0 Home`。
- UEFI `EFI/BOOT/BOOTX64.EFI` 可加载 GRUB 菜单。
- BIOS fallback 存在 `boot/grub/i386-pc/eltorito.img`。
- QEMU/VirtualBox 启动不再停在 `Welcome to GRUB`。
- Ventoy/Live 不再要求手动输入用户名密码：LightDM 自动登录 `onion`，tty1/ttyS0 文本控制台也兜底自动登录。
- 串口验证应看到 `onion-os login: onion (automatic login)`。
- `onion-firewall.service` 应显示 OK，不应在 Live 启动日志里标红失败。
- Live 桌面能自动弹出 Calamares。
- Dock 和开始菜单的设置入口优先打开 Onion 设置任务面板，而不是原版 Xfce Settings Manager。
- Onion 设置里常见操作必须是按钮：检查更新、修复界面显示、连接网络、安装微信、微信省内存、清理微信缓存、网页版微信、修复应用商店、查看内存策略。
- 2GB 虚拟机中 Picom 使用 fallback、Dock 不放大、微信启动器提示省内存模式。
- `onion-update check` 能从 Scallion 返回 JSON，而不是 PHP 源码或前端 HTML。

## OTA 发布流程

1. 构建 ISO。
2. 上传到线上 Scallion：

```text
/www/wwwroot/scallion.uno/public/iso/onion-os-26.2.0-home-amd64.iso
```

3. 线上接口应返回 `ready: true`、`has_update: true`、有效 `checksum` 和 `size`：

```bash
curl -s "https://scallion.uno/api/onion-update/check?version=26.1.0&channel=stable"
```

4. 客户端下载后执行：

```bash
sudo onion-update install
```

该命令不会粗暴覆盖当前系统文件，而是校验 ISO 结构后写入 GRUB 的 `Onion OS 26.2.0 OTA Installer` 启动项，重启后进入新系统安装器。

## 用户反馈口径

对 2GB 内存用户需要说清楚：Onion OS 本身会尽量轻量运行，但微信好友和群组过多时，微信才是主要内存压力源。26.2.0 会通过 zram、earlyoom、轻量合成器、低优先级启动、微信缓存限制和网页版入口尽量缓解；如果用户账号群组特别多，2GB 机器仍建议优先使用网页版微信或升级内存。

对“不会用命令”的用户，默认说法应是：打开 Dock 里的 `Onion 设置`，点对应按钮完成操作。命令行只作为高级排障方式写在文档后面，不作为普通用户的第一路径。
