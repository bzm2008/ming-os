# Onion OS 26.2.0 发布改动与网站素材说明

## 本次版本定位

Onion OS 26.2.0 是面向正式用户发布前的维护与体验升级版本。重点解决 ISO 启动、低内存微信、OTA 更新、桌面辨识度、图形化操作入口和安装后易用性问题。

## 核心改动摘要

- 系统底座升级到 Debian 13 / Trixie。
- 修复 ISO 启动可能停在 `Welcome to GRUB` 的问题，BIOS/UEFI 回退引导会加载 Onion OS 菜单。
- 微信改为腾讯官方 Linux deb，提供 `onion-wechat` 低内存启动包装器和网页版微信入口。
- 2GB 内存优化：zram、earlyoom、低内存 Picom 回退、Dock 动效降级、微信低缓存和低优先级启动。
- 应用商店改为星火应用商店，避免 GNOME Software/Flatpak 在低内存设备上带来额外后台负担。
- Xfce 页面重构为 Onion 风格：Onion Glass 主题、Dock、控制中心、品牌化图标、壁纸和首次启动引导。
- 减少命令依赖：常见操作集中到 `Onion 设置` 按钮，包括更新、网络、微信、应用商店、桌面整理、所有磁盘、内存策略、声音、电源、外观。
- 新增 `Onion 应用库`：像手机应用库/Launchpad 一样搜索并打开已安装软件。
- 新增安卓式桌面文件夹：应用、系统、上网、办公、影音、游戏。新安装应用会自动同步到桌面文件夹。
- 新增 `所有磁盘`：用一个入口聚合个人文件、桌面、下载、文档、系统盘和已挂载数据盘，缓解 C/D 盘式空间焦虑。
- 增强 OTA 客户端：更新清单 JSON 校验、下载重试、SHA256/大小校验、未上传 ISO 时不会错误缓存更新。
- 新增免登录：Ventoy/Live 默认自动登录 `onion`，图形 LightDM 和 tty1/ttyS0 文本控制台都有兜底；安装后 LightDM 会自动选择 `onion` 或第一个普通用户进入桌面。

## 最终构建与验证

- ISO 文件名：`onion-os-26.2.0-home-amd64.iso`
- SHA256：`f96bb22b7840c186a1f089a44698ce26e44521a5dfed47525edd4033794dcf82`
- BIOS/UEFI：最终 ISO 已检查 El Torito 引导结构，QEMU BIOS/UEFI 均进入 Onion OS 26.2.0 GRUB 菜单，不再停在裸 `Welcome to GRUB`。
- Ventoy/Live：最终 ISO 内含 `live-config username=onion`、LightDM 自动登录和 tty1/ttyS0 兜底自动登录。串口启动验证结果为 `onion-os login: onion (automatic login)`。
- 防火墙服务：最终串口日志显示 `onion-firewall.service` 为 OK，不再在 Live 启动时标红失败。

## 用户沟通口径

2GB 内存可以运行 Onion OS，但微信好友和群组很多时，主要压力来自微信本身。Onion OS 会尽量保护系统流畅：压缩内存、降低微信优先级、限制缓存、必要时建议使用网页版微信。若用户账号群组特别多，2GB 设备仍建议优先网页版微信或升级内存。

## 链接素材

- 官网：`https://scallion.uno`
- OTA API：`https://scallion.uno/api/onion-update/check?version=26.1.0&channel=stable`
- ISO 下载链接：`https://scallion.uno/iso/onion-os-26.2.0-home-amd64-f96bb22b.iso`
- GitHub 仓库：`https://github.com/bzm2008/onion-os`
- 推荐页面主标题：`Onion OS 26.2.0`
- 推荐副标题：`给老旧电脑和中文用户的按钮化 Linux 桌面`

注意：ISO 文件上传并校验完成前，OTA 会返回 `pending_artifact`，网页上的下载按钮应支持“即将开放下载”或“复制 OTA 检查链接”的状态。

## 给其他 AI 制作 Scallion 网站 Onion 产品介绍页的提示词

你是一个资深产品网页设计与前端实现 AI。请为 Scallion 官网制作 `Onion OS 26.2.0` 产品介绍页，页面要面向普通中文用户和老旧电脑用户，不要做泛泛的 Linux 技术页。

页面目标：
- 让用户理解 Onion OS 是一个基于 Debian 13/Trixie 的中文桌面系统。
- 强调正式版重点：修复 ISO 启动、低内存微信优化、OTA 更新、按钮化设置、应用库、安卓式桌面文件夹、所有磁盘入口、免登录。
- 提供清晰的 ISO 下载入口、GitHub 链接和 OTA 状态说明。

必须包含的链接：
- ISO 下载：`https://scallion.uno/iso/onion-os-26.2.0-home-amd64-f96bb22b.iso`
- GitHub：`https://github.com/bzm2008/onion-os`
- OTA 检查：`https://scallion.uno/api/onion-update/check?version=26.1.0&channel=stable`

页面结构建议：
- 首屏：标题 `Onion OS 26.2.0`，副标题 `给老旧电脑和中文用户的按钮化 Linux 桌面`，按钮 `下载 ISO`、`查看 GitHub`、`检查 OTA`。
- 第二屏：三张重点卡片，分别是 `2GB 内存也尽量顺滑`、`不用记命令`、`像手机一样整理应用`。
- 功能区：官方微信低内存模式、星火应用商店、Onion 设置、Onion 应用库、所有磁盘、OTA 更新。
- 说明区：明确提示 2GB 机器可以跑系统，但群组很多的微信仍可能吃内存，推荐网页版微信作为兜底。
- 下载区：展示 ISO 文件名 `onion-os-26.2.0-home-amd64.iso`，并支持 ISO 未就绪时的 `即将开放下载` 状态。

设计风格：
- 避免原版 Xfce 或 Linux Mint 视觉感。
- 使用 Onion OS 特色的深色玻璃、青绿色和紫色点缀，但不要整页单一紫色。
- 面向“数字难民”，文案要短、直接、像按钮说明，不要堆命令。
- 首屏要让用户一眼看到产品名、下载入口和适用人群。

实现要求：
- 页面必须响应式，手机和桌面都能读。
- 下载按钮若 ISO 返回非 ISO 内容或未就绪，应显示友好状态，不要让用户下载错误文件。
- 不要展示服务器密码、SSH、运维私密信息。
