# Ming OS 26.4.1 Live 与安装后系统问题报告

生成时间：2026-07-26
测试目标：output/ming-os-26.4.1-home-amd64.iso
ISO SHA256：671caf31264900765bbcfcddb82d0fa23231472f61c4c7b07e65227a7a9018bf
测试虚拟机：MingOS-2641-Install-BIOS-20260726-1726
虚拟机配置：BIOS、VirtualBox、VMSVGA、2 vCPU、4GB RAM、NAT、有线网、32GB 临时 VDI
测试原则：本轮只记录问题，不修复源码。

## 总结

26.4.1 ISO 可以启动 Live 安装器，并且可以安装到 32GB 临时 VDI。安装后的系统可以进入桌面，有线网络、Firefox ESR、星火应用商店、Dock 和桌面图标有基本可用性。

但仍有发布前阻塞问题：Live 模式最小化安装器后桌面是空背景；已安装系统仍显示已弃用的 Garlic Claw；Papyrus 没有出现在桌面和 Dock；Papyrus 虽有 desktop entry，但 /usr/bin/papyrus 指向普通用户无法访问的 /opt/papyrus/launch-papyrus，实际无法运行；首次欢迎向导按钮和键盘操作不可靠；完成向导后完整小组件消失；桌面 Firefox 图标重复。

## 已验证范围

- BIOS Live 模式启动安装器。
- 带 32GB 临时 VDI 的 BIOS Live 安装流程。
- 安装后从 VDI 启动进入桌面。
- 桌面图标、Dock、终端、Firefox ESR、星火应用商店、有线网络、Wi-Fi 无硬件场景诊断、亮度软件回退、Papyrus 文件入口。

## 未验证范围

- UEFI 安装后系统完整流程。
- 真机旧 Intel 笔记本、台式机、真实无线网卡、中文 SSID 连接、真实蓝牙、真实背光设备、真实音频设备。
- 从浏览器下载 .deb / AppImage / 星火应用后的安装、首开、关闭后再打开链路。
- OTA 点一次后全自动重启完成的真实安装器闭环。
- Papyrus Browser Bridge 和 agent 指令执行能力，因为 Papyrus 当前无法以普通用户运行。

## Live 模式问题

### L-001 Live 模式最小化安装器后桌面是空背景

- 严重级别：P0
- 状态：已确认
- 影响：用户最小化安装器后只看到深绿色背景，没有 Dock、桌面图标、小组件，容易认为系统卡死或桌面损坏。
- 复现：BIOS Live 启动 ISO，等待 Calamares 出现后最小化安装器。
- 观察结果：桌面只剩空背景，Ctrl+Alt+T 没有可见终端。
- 证据：output/mingos-2641-live-bios-desktop-empty-20s.png、output/mingos-2641-live-bios-minimized-installer.png、output/mingos-2641-livedisk-bios-minimized.png
- 推测：Live/installer session 只为 Calamares 启动了最小桌面环境，未启动普通桌面组件，或普通组件被 installer session 策略禁用。
- 建议：Live 安装器会话应保留可恢复的安装器入口、Dock/小组件基本入口，或提供明确的安装器模式遮罩。

### L-002 UEFI Live 启动明显慢于 BIOS Live

- 严重级别：P1
- 状态：已确认
- 观察结果：UEFI Live 在 90 秒时仍为空安装器窗口，约 150 秒才显示安装器欢迎页和加载状态。
- 证据：output/mingos-2641-live-uefi-90s.png、output/mingos-2641-live-uefi-150s.png
- 建议：拆分 UEFI 启动耗时，记录 GRUB、内核、图形会话、Calamares 各阶段时间。

### L-003 VirtualBox VMSVGA 早期显示 vmwgfx unsupported hypervisor 警告

- 严重级别：P2
- 状态：已确认但环境相关
- 影响：VirtualBox 下出现图形驱动兼容性警告，暂未确认是否影响真实旧硬件。
- 建议：给 VirtualBox 推荐图形控制器策略，或在安全图形模式中规避该警告。

### L-004 无硬盘 Live VM 中安装器提示无可安装分区

- 严重级别：P3
- 状态：已确认，属于测试配置预期
- 说明：带 32GB 临时 VDI 后，安装器可识别 /dev/sda 并继续安装，因此不是 ISO 安装器本身的阻塞问题。

## 安装过程问题

### I-001 安装器完成后重启仍回到 ISO 启动菜单

- 严重级别：P2
- 状态：已确认，属于介质和启动顺序体验问题
- 复现：BIOS Live 安装到 32GB VDI，保持 ISO 挂载且 DVD 优先，点击完成重启。
- 观察结果：回到 Ming OS Installer 蓝色启动菜单。
- 证据：output/mingos-2641-installed-boot75s.png
- 建议：安装完成页应提示移除安装介质，或在可控场景提示从硬盘启动。

### I-002 安装耗时偏慢但最终成功

- 严重级别：P2
- 状态：已确认
- 观察结果：约 30 秒为 17%，约 90 秒为 23%，约 210 秒为 57%，约 360 秒显示安装成功。
- 证据：output/mingos-2641-install-30s.png、output/mingos-2641-install-90s.png、output/mingos-2641-install-210s.png、output/mingos-2641-install-360s.png
- 说明：该速度来自 VirtualBox 和临时 VDI，不能单独证明真机性能问题，但安装进度需要更细和更清楚。

## 安装后启动与首次设置问题

### P-001 首次图形启动约 80 秒才进入欢迎向导

- 严重级别：P1
- 状态：已确认
- 证据：output/mingos-2641-installed-firstboot-disk.png、output/mingos-2641-installed-firstboot-80s.png
- 推测：首次启动服务较多，包括硬件预载、网络等待、桌面策略、Spark 更新通知等。
- 建议：区分首次启动和后续冷启动，减少阻塞服务，重点检查 NetworkManager-wait-online.service 和首次欢迎/账户流程启动时序。

### P-002 首次欢迎向导键盘确认和鼠标点击不可靠

- 严重级别：P0
- 状态：已确认
- 影响：普通用户可能卡在欢迎向导，无法自然进入桌面。
- 复现：首次启动进入欢迎使用 Ming OS 向导，使用 Tab/Enter/Space 或 VirtualBox 鼠标点击按钮。
- 观察结果：多次 Enter、Space、鼠标点击没有触发按钮；为继续验收，临时在 VM 内写入 ~/.config/ming-os/welcome-done 并杀掉 ming-welcome。
- 证据：output/mingos-2641-setup-start.png、output/mingos-2641-setup-enter-focus.png、output/mingos-2641-click-start.png、output/mingos-2641-click-child-focus.png
- 建议：欢迎向导按钮应支持 Enter/Space 激活，并降低 keep_above/焦点策略对输入的干扰。

### P-003 账户设置向导在欢迎向导之后才弹出，流程顺序混乱

- 严重级别：P1
- 状态：已确认
- 观察结果：欢迎向导被绕过后，仍出现为本机设置一个密码窗口；留空后提示账户设置完成。
- 证据：output/mingos-2641-account-dialog.png、output/mingos-2641-account-after-ok.png
- 建议：欢迎向导和账户向导应合并为一个线性流程，或账户设置先于欢迎完成页。

### P-004 Ctrl+Alt+T 在欢迎向导期间触发锁屏，不是打开终端

- 严重级别：P1
- 状态：已确认
- 观察结果：欢迎向导期间按 Ctrl+Alt+T 后进入锁屏密码界面；输入 user/user 可解锁。
- 证据：output/mingos-2641-installed-ctrlaltt.png、output/mingos-2641-after-unlock-user.png
- 建议：检查快捷键冲突和锁屏触发链；首次 OOBE 期间应禁用误触锁屏或保证终端快捷键一致。

## 桌面、Dock、图标和小组件问题

### D-001 安装后桌面显示已弃用的 Garlic Claw，未显示 Papyrus

- 严重级别：P0
- 状态：已确认
- 影响：与产品要求相反，用户会看到旧 AI 助手入口，而新 Papyrus 入口不可见。
- 观察结果：桌面显示 Garlic Claw 电...，Dock 中也有旧 Garlic Claw 图标，桌面和 Dock 未显示 Papyrus。
- 证据：output/mingos-2641-clean-desktop.png、output/mingos-2641-desktop-after-marker.png
- 用户补充：截图中没有 Papyrus，反而出现之前弃用的 Garlic Claw，必须写入报告。
- 建议：移除 Garlic Claw desktop/dock/autostart 残留；Papyrus 应出现在桌面、应用抽屉和 Dock 的目标位置。

### D-002 Firefox ESR 桌面图标重复

- 严重级别：P1
- 状态：已确认
- 观察结果：桌面左侧同时出现两个 Firefox ESR 浏览器图标。
- 证据：output/mingos-2641-clean-desktop.png
- 建议：统一 desktop shortcut 生成逻辑，避免 firefox-esr.desktop 与 Ming wrapper 同时投放到桌面。

### D-003 完成首次向导后右侧完整小组件消失

- 严重级别：P1
- 状态：已确认
- 观察结果：欢迎向导期间右侧有完整小组件，完成账户向导后只剩顶部时间胶囊。
- 证据：output/mingos-2641-setup-start.png、output/mingos-2641-clean-desktop.png
- 建议：检查小组件自动收起和常驻状态规则，确保用户能发现音量、亮度、设置、电源入口。

### D-004 Dock 和桌面图标本轮基本可显示，但内容错误

- 严重级别：记录项
- 状态：已确认
- 观察结果：安装后 Dock 可见，桌面图标可见，完成 OOBE 后 Ctrl+Alt+T 可打开终端。
- 证据：output/mingos-2641-clean-desktop.png、output/mingos-2641-installed-terminal-test.png
- 说明：显示机制不是完全坏掉，主要问题是 Papyrus 缺席、Garlic 残留、Firefox 重复、小组件消失。

## Papyrus 问题

### A-001 Papyrus 有 desktop entry，但普通用户无法运行

- 严重级别：P0
- 状态：已确认
- 影响：Papyrus 作为内置 agent 工具无法运行，浏览器控制和电脑操控能力无法验收。
- 观察结果：/usr/share/applications/papyrus.desktop 存在，Exec=/usr/bin/papyrus %U；/usr/bin/papyrus 是指向 /opt/papyrus/launch-papyrus 的符号链接；普通用户访问 /opt/papyrus 报权限不够；file /usr/bin/papyrus 显示 broken symbolic link；执行 /usr/bin/papyrus --version 报权限不够。
- 证据：output/mingos-2641-papyrus-entry.png、output/mingos-2641-papyrus-permissions.png
- 建议：安装脚本应确保 /opt/papyrus 目录、启动器、AppImage/二进制和图标对普通用户可读可执行；desktop entry 应可由应用抽屉正常启动。

### A-002 Papyrus 没有出现在桌面和 Dock

- 严重级别：P0
- 状态：已确认
- 证据：output/mingos-2641-clean-desktop.png
- 建议：把 Papyrus 加入桌面快捷方式、Dock 项和应用抽屉推荐项，同时移除 Garlic Claw。

## 应用与软件商店问题

### S-001 Firefox ESR 可以启动，但默认是英文界面

- 严重级别：P2
- 状态：已确认
- 观察结果：Firefox ESR 可以打开新标签页，但界面文本为英文。
- 证据：output/mingos-2641-firefox-launch.png
- 建议：预置 Firefox ESR 中文语言包、默认区域、首启策略和书签/主页。

### S-002 星火应用商店可以启动，但日志存在集成警告

- 严重级别：P1
- 状态：已确认
- 观察结果：星火应用商店图形界面可打开，能显示首页内容；日志出现 xdg-settings default-url-scheme-handler not implemented for xfce，以及 未检测到 app 命令。
- 证据：output/mingos-2641-spark-launch.png、output/mingos-2641-after-spark-close.png
- 影响：可能影响默认链接处理、深链安装、应用安装和启动链路。
- 建议：为 XFCE 提供 xdg-settings 兼容路径，确认 Spark 的 app 命令探测和安装后入口刷新。

### S-003 Garlic Claw 仍有 desktop entry

- 严重级别：P0
- 状态：已确认
- 观察结果：/usr/share/applications/garlic-claw.desktop 存在，Exec=garlic-claw-app，桌面也显示 Garlic Claw。
- 证据：output/mingos-2641-papyrus-diagnostics.png
- 建议：从 rootfs、桌面、Dock、应用抽屉、推荐项中移除 Garlic Claw 残留。

### S-004 浏览器下载应用安装链未完成真实验收

- 严重级别：P1
- 状态：未验证
- 原因：本轮重点先完成安装后桌面和预装应用基础检查，尚未下载外部 .deb / AppImage / 星火应用并验证安装后首开、关闭、再打开。
- 建议：下一轮用真实 .deb、AppImage 和星火应用复现 26.4.0 用户反馈的软件打不开问题。

## 网络与硬件控制问题

### N-001 有线网络可连接互联网，但状态 JSON 的 IPv4/DNS/Gateway 字段为空

- 严重级别：P1
- 状态：已确认
- 观察结果：nmcli 显示 enp0s3:ethernet:connected:Wired connection 1；ip addr 显示 10.0.2.15/24；ip route 有默认路由 10.0.2.2；getent hosts 和 curl 到 deb.debian.org 成功。但 ming-device-control ethernet-status --json 中 ipv4.addresses/dns/gateway 为空，同时 state=online。
- 证据：output/mingos-2641-network-status.png、output/mingos-2641-network-real.png
- 建议：ming-device-control 应从 NetworkManager 或 ip 正确回填 IPv4、DNS、Gateway。

### N-002 无无线硬件场景下，Wi-Fi 诊断文案基本正确

- 严重级别：记录项
- 状态：已确认
- 观察结果：小组件显示 Wi-Fi 不可用；ming-device-control wifi-scan --json 返回 diagnostic_unavailable，说明无法完成无线 PCI/USB 硬件探测，不能确认没有无线网卡。
- 证据：output/mingos-2641-network-status.png
- 未验证：真实无线网卡、中文 SSID 扫描、密码弹窗、连接成功/失败提示。

### N-003 蓝牙诊断不可用

- 严重级别：P2
- 状态：已确认但环境相关
- 观察结果：状态 JSON 显示蓝牙硬件诊断不可用，小组件显示蓝牙不可用。
- 证据：output/mingos-2641-audio-services.png
- 说明：VirtualBox 环境没有蓝牙硬件，不能判断真机蓝牙是否可用。

### H-001 亮度使用 xrandr 软件回退，显示 100%

- 严重级别：记录项
- 状态：已确认
- 观察结果：状态 JSON 显示 brightness.available=true、backend=xrandr-software、outputs=["Virtual1"]、value=100。
- 证据：output/mingos-2641-audio-services.png
- 未验证：真实笔记本背光硬件调节。

### H-002 音频只有 PulseAudio null sink，没有真实输出设备

- 严重级别：P2
- 状态：已确认但环境相关
- 观察结果：ming-device-control audio-status --json 显示 state=no_default_sink；pactl list short sinks 只有 auto_null module-null-sink.c。
- 证据：output/mingos-2641-audio-status.png
- 说明：VirtualBox 音频设备可能未正确暴露；不能证明真机音频失败，但说明 VM 验收环境下小组件音量无法真实调节。
- 建议：补充 VirtualBox 音频配置测试，真机再验 ALSA/Pulse/PipeWire 默认输出。

### H-003 笔记本电量显示未验证

- 严重级别：未验证项
- 原因：VirtualBox 台式机形态无电池。
- 建议：用至少一台旧笔记本验证有电池时小组件显示电量、台式机隐藏电量。

## 自动挂载、输入法和 OTA

### M-001 仅确认根分区不会被误挂载，未验证数据分区自动挂载

- 严重级别：P1
- 状态：部分验证
- 观察结果：ming-volume-automount --json 返回 /dev/sda1 already_mounted /。
- 证据：output/mingos-2641-diagnostics.png
- 未验证：NTFS/exFAT/VFAT/ext 数据分区热插拔和登录后自动挂载到 /media/<user>/<label-or-uuid>。
- 建议：给当前 VM 再挂一块含 NTFS/exFAT 测试分区的临时磁盘，只做验证，不接触宿主真实磁盘。

### IM-001 输入法未完成图形验收

- 严重级别：P1
- 状态：未验证
- 原因：本轮优先用于安装、桌面、网络、Papyrus 和应用基础链路，未打开文本编辑器验证 fcitx5 中文输入、旧 .xinputrc 迁移、Rime 可选项。
- 建议：用新装系统创建旧 .xinputrc 冲突样本，运行登录迁移和设置页修复输入法按钮，再验证中文输入。

### O-001 OTA 一键全自动更新未完成真实验收

- 严重级别：P0
- 状态：未验证
- 原因：本轮只安装并启动当前 26.4.1 ISO，没有部署 26.3/26.4 源系统和在线 OTA manifest。
- 风险：代码测试通过不等于真实 GRUB next-entry、重启进入 OTA 安装器、安装后回到新系统成功。
- 建议：准备 26.3.x 和 26.4.0 VM 快照，分别走设置页、电源菜单、命令入口三条更新路径。

## 构建日志非阻塞问题

### B-001 Spark Store systemd unit 存在格式警告

- 严重级别：P2
- 状态：构建日志已观察，未在本轮修复
- 现象：RestartSec=15 行尾中文注释导致解析警告；StartLimitIntervalSec 放在 [Service]；unit 文件权限带 executable。
- 建议：清理 unit 语法，确保 systemd-analyze verify 覆盖。

### B-002 Garlic Claw 构建阶段仍尝试写用户配置目录并权限拒绝

- 严重级别：P1
- 状态：构建日志已观察，且安装后确认 Garlic 残留
- 现象：尝试创建 /home/user/.config/systemd、/home/user/.config/ming-os 时权限拒绝。
- 建议：既然 Garlic Claw 已废弃，应移除整个构建安装链，而不是修补权限。

### B-003 07_finalize 仍检查已移除的 ming-edge.desktop

- 严重级别：P2
- 状态：构建日志已观察
- 影响：Edge 替换 Firefox ESR 后仍有旧检查，可能导致错误门禁或错误提示。
- 建议：将相关检查迁移为 Firefox ESR/Papyrus 检查。

### B-004 Spark Store postinst 写不存在的 /root/.config/mimeapps.list

- 严重级别：P2
- 状态：构建日志已观察
- 影响：可能导致默认应用关联没有按预期生效。
- 建议：postinst 不应写 root 用户图形 MIME 配置，应写系统级关联或目标用户关联。

## 补充静态审查问题

### X-001 升级用户可能收不到新的字体和缩放策略

- 严重级别：P1
- 状态：静态审查确认，非 VM 现场复现
- 影响：已有 scale-done 的 26.4.0/26.3 升级用户可能保留旧 9pt 字体，收不到本轮 10pt 字体美化。
- 证据来源：字体审查子代理指出 modules/03_desktop.sh:503-512 的自动缩放完成标记没有字体策略版本。
- 建议：给自动缩放标记增加字体策略版本；版本变更时重跑，同时继续尊重用户已有 scale-preference.json。

### X-002 应用抽屉 320px 宽屏下分类栏可能溢出

- 严重级别：P2
- 状态：静态审查确认，非 VM 现场复现
- 影响：老旧小屏设备上应用抽屉横向分类按钮可能超出可用宽度。
- 证据来源：字体审查子代理指出 assets/ming-app-drawer.py:296,331-338,470-472 中 8 个分类按钮在不可换行、不可滚动横向 Gtk.Box 中可能超过 288px 可用宽度。
- 建议：分类栏改为横向滚动或 Gtk.FlowBox，增加 320px 回归测试。

## 发布阻断清单

以下问题建议作为 26.4.1 发布阻断：

1. P0：Live 模式最小化安装器后桌面空背景。
2. P0：首次欢迎向导按钮和键盘操作不可靠，用户可能卡住。
3. P0：Papyrus 无法普通用户运行，且不在桌面和 Dock 显示。
4. P0：已弃用 Garlic Claw 仍预装并显示在桌面和 Dock。
5. P0：OTA 一键全自动更新尚未真实验收。

## 优先修复建议

1. 先修 Papyrus/Garlic：移除 Garlic 残留，修正 Papyrus 权限、启动器、桌面和 Dock 入口。
2. 再修 OOBE：欢迎向导和账户向导合并，按钮支持键盘和鼠标，避免锁屏快捷键干扰。
3. 再修 Live 桌面：安装器最小化后必须有可见控件或恢复安装器入口。
4. 再修网络/硬件状态：有线 IPv4/DNS/Gateway 正确回填，音频和亮度区分无硬件和系统错误。
5. 最后做完整安装应用链、OTA 升级链和真机 Wi-Fi/输入法验收。

## 主要截图索引

- Live BIOS 安装器：output/mingos-2641-live-bios-boot110.png
- Live BIOS 空桌面：output/mingos-2641-live-bios-desktop-empty-20s.png
- Live 带磁盘最小化后空桌面：output/mingos-2641-livedisk-bios-minimized.png
- UEFI 慢启动：output/mingos-2641-live-uefi-90s.png、output/mingos-2641-live-uefi-150s.png
- 安装成功：output/mingos-2641-install-360s.png
- 安装后首次启动：output/mingos-2641-installed-firstboot-80s.png
- 欢迎向导卡住：output/mingos-2641-setup-enter-focus.png
- 完整桌面：output/mingos-2641-clean-desktop.png
- 终端可打开：output/mingos-2641-installed-terminal-test.png
- 网络状态：output/mingos-2641-network-status.png、output/mingos-2641-network-real.png
- Papyrus 权限错误：output/mingos-2641-papyrus-permissions.png
- Firefox 启动：output/mingos-2641-firefox-launch.png
- 星火应用商店启动：output/mingos-2641-spark-launch.png

## 任务后审视

- 最没把握的事：真实旧电脑上的 Wi-Fi、音频、背光和输入法表现仍未知；VirtualBox 只能证明当前 ISO 的安装和桌面基础链路，不能替代真机验收。
- 你可能遗漏的事：浏览器/星火下载应用后的安装、首开、关闭后重开尚未做真实闭环，这正是 26.4.0 用户反馈最严重的问题之一，下一轮应优先用真实 .deb、AppImage 和星火应用复现。
