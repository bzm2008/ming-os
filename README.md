# Onion OS 26.2.0 Home Edition

> 层层精简，层层用心

Onion OS 是面向老旧电脑和中文桌面用户的 Debian 定制系统。26.2.0 以 Debian 13/Trixie 为底座，重点修复 ISO 启动卡在 `Welcome to GRUB` 的问题，并围绕 2GB 内存设备、微信重负载、OTA 更新和桌面辨识度做了一轮系统级整理。

## 26.2.0 重点

- **启动修复**：BIOS 和 UEFI 引导镜像内嵌 early GRUB 配置，启动时直接寻找 `/live/vmlinuz` 并加载 Onion OS 菜单，避免落入 GRUB 命令行。
- **Debian 13/Trixie**：APT 源、preseed、系统标识和 OTA release 信息统一为 Trixie。
- **官方微信**：移除 deepin-wine 方案，改用腾讯官方 Linux deb，并提供 `onion-wechat` 低内存包装器。
- **2GB 内存策略**：按真实内存动态调整 zram、swappiness、Picom、Dock 缩放和微信启动方式，优先保证桌面不被微信拖死。
- **星火应用商店**：替换 GNOME Software/Flatpak 默认入口，改为按需安装，减少首次登录后台负担。
- **OTA 健全化**：客户端验证 JSON、支持重试和校验和；线上 Scallion 服务器提供权威 `/api/onion-update` 清单。
- **按钮化桌面**：Onion Control Center 把更新、修复显示、连接网络、安装微信、清理微信缓存、修复应用商店等常见任务做成按钮，减少手动输入指令。

## 项目概览

| 项目 | 说明 |
| --- | --- |
| 版本 | 26.2.0 Home Edition |
| 底层系统 | Debian 13 (Trixie) |
| 桌面环境 | Xfce + Plank Dock |
| 窗口合成器 | Picom，低内存/老显卡自动回退 xrender |
| 默认语言 | 简体中文 (`zh_CN.UTF-8`) |
| 应用入口 | Onion Control Center、Firefox ESR、WPS、官方微信、星火应用商店、Garlic Claw、系统更新 |
| OTA 服务器 | `https://scallion.uno/api/onion-update` |

## 构建

推荐在 Debian 13、Debian 12、Ubuntu 22.04+ 或 WSL Debian 中构建，需 root 权限、稳定网络和 30GB 以上可用空间。

```bash
chmod +x build_onion_os.sh modules/*.sh
sudo ./build_onion_os.sh
```

构建产物：

```text
output/onion-os-26.2.0-home-amd64.iso
```

当前已验证发布候选：

```text
SHA256: ecca3e1ee619a2cb34d918029468434db9f0e66b34430d1d023f3a338f42bbd2
```

最终镜像已检查 BIOS/UEFI El Torito 引导结构，QEMU BIOS/UEFI 可进入 Onion OS 26.2.0 GRUB 菜单，不再停在裸 `Welcome to GRUB`。Live/ Ventoy 场景同时启用 LightDM 图形自动登录和 tty1/ttyS0 文本控制台兜底自动登录，串口验证结果为 `onion-os login: onion (automatic login)`。

## 安装

```bash
sudo dd if=output/onion-os-26.2.0-home-amd64.iso of=/dev/sdX bs=4M status=progress
```

从 U 盘或虚拟机启动后进入 Live 桌面，系统会自动弹出 Calamares 图形化安装器。若显卡较旧，可在 GRUB 菜单选择兼容模式、低分辨率模式或安全模式。

Live 默认用户为 `onion`，正常情况下无需输入用户名和密码；如果显卡或虚拟机导致图形桌面未及时启动，文本控制台也会自动登录到 `onion`，避免用户卡在登录提示符前。

## 低内存与微信

2GB 内存可以运行 Onion OS，但微信在好友和群组较多时会成为主要压力源。26.2.0 的处理方式是“保护系统优先”：

- `onion-memory-profile` 在启动早期按内存重写 zram 和 sysctl：2GB 设备使用 100% zram、较高 swappiness 和更激进脏页回写。
- `earlyoom` 优先处理微信、浏览器、WPS 等重应用，避免 Xfce、LightDM、NetworkManager 被误杀。
- `onion-picom` 在 2.6GB 以下自动使用 xrender fallback，关闭重模糊负担。
- `onion-scale` 在低内存设备缩小 Dock、关闭 Dock 放大动画，并写入内存 profile。
- `onion-wechat` 清理大缓存、降低 CPU/IO 权重，并在 systemd user scope 中给微信设置内存高水位和上限。
- 用户仍可从菜单启动“微信网页版”，适合群组很多但机器只有 2GB 的情况。

## 桌面界面

26.2.0 不再把原版 Xfce 设置管理器作为主要入口。Dock 和开始菜单会优先打开 `Onion 设置`，以 Onion 风格的任务按钮集中处理：

- 检查系统更新
- 修复界面显示
- 连接网络
- 安装微信
- 微信省内存启动
- 清理微信缓存
- 打开网页版微信
- 打开或修复应用商店
- 查看低内存策略
- 调节声音、电源、外观

底层 Xfce 工具仍保留在“高级设置”中，方便排障，但普通用户不需要记住命令。

## OTA

普通用户从 `Onion 设置` 点击“检查系统更新”即可。高级用户也可以使用：

```bash
onion-update check
onion-update download
sudo onion-update install
```

更新清单由线上 Scallion 服务生成。ISO 未上传完成时接口会返回 `pending_artifact`，客户端只提示“更新包尚未就绪”；ISO 上传后会返回下载地址、大小和 SHA256，客户端再允许下载并写入 GRUB OTA 启动项。

## 目录

```text
onion-os/
├── build_onion_os.sh
├── modules/
│   ├── 01_base.sh          # Debian/Trixie 基础系统、网络、zram、earlyoom
│   ├── 02_apps.sh          # Xfce、WPS、官方微信、Fcitx5、星火商店
│   ├── 03_desktop.sh       # 主题、Dock、Picom、壁纸、欢迎引导、低内存视觉策略
│   ├── 04_garlic_claw.sh   # Garlic Claw AI 助手
│   ├── 05_security_tools.sh
│   ├── 06_ota_update.sh
│   └── 07_finalize.sh
├── config/
└── output/                 # ISO 构建产物，git 忽略
```
