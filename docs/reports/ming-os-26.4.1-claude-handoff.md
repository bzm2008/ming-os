# Ming OS 26.4.1 优化补充交接文档（给 Claude / 下一位接手 agent）

生成时间：2026-07-26
用途：作为 `ming-os-26.4.1-live-and-installed-issues.md` 的补充交接文档，帮助下一位 agent 继续优化 26.4.1。
注意：这不是新的问题清单原文，详细问题、截图证据和复现步骤以问题报告为准。

## 0. 最重要的接手结论

当前 26.4.1 ISO 已经能安装并启动到桌面，但不能作为可发布版本。需要先修 P0 问题，再重新构建和验收。

优先级最高的阻塞点：

1. Papyrus 没有显示在桌面/Dock，且普通用户无法运行。
2. 已弃用的 Garlic Claw 仍出现在桌面、Dock 和应用入口。
3. 首次欢迎向导按钮/键盘/鼠标操作不可靠，用户可能卡住。
4. Live 模式最小化安装器后桌面空白，没有可恢复入口。
5. OTA 点一次后全自动升级仍未做真实 VM 闭环验收。

通俗解释：系统“能装上”，但“用户第一眼能不能用、内置软件是不是对、升级是不是傻瓜式”还没过关。

## 1. 关键路径

- 工作树：`C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641`
- 分支：`fix/daily-function-chain-2641`
- 问题报告：`C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641\docs\reports\ming-os-26.4.1-live-and-installed-issues.md`
- 本交接文档：`C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641\docs\reports\ming-os-26.4.1-claude-handoff.md`
- ISO：`C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641\output\ming-os-26.4.1-home-amd64.iso`
- ISO SHA256：`671caf31264900765bbcfcddb82d0fa23231472f61c4c7b07e65227a7a9018bf`
- 构建日志：Linux 构建环境内 `/var/tmp/ming-os-build/build.log`
- 主要截图目录：`C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641\output`
- 临时安装 VM：`MingOS-2641-Install-BIOS-20260726-1726`
- 临时 VDI：`C:\Users\Administrator\VirtualBox VMs\MingOS-2641-Install-BIOS-20260726-1726\MingOS-2641-Install-BIOS-20260726-1726.vdi`

## 2. 当前工作树状态和注意事项

工作树是脏的，不能 reset，不能覆盖未确认的改动。用户已经多次明确要求保留当前 26.4.1 的修改和草稿。

当前已知改动包括但不限于：

- `assets/ming-app-drawer.py`
- `assets/ming-device-control.py`
- `assets/ming-launch.py`
- `assets/ming-phone-desktop.py`
- `assets/ming-settings.py`
- `assets/ming-shell-common.py`
- `build_onion_os.sh`
- `modules/01_base.sh`
- `modules/02_apps.sh`
- `modules/03_desktop.sh`
- `modules/06_ota_update.sh`
- 新增 `assets/ming-appimage-installer.py`、`assets/ming-input-repair.py`、`assets/ming-performance-policy.py`、`assets/ming-volume-automount.py`
- 新增 `assets/vendor/papyrus/`
- 多个新增/修改测试文件

不要做的事：

- 不要 `git reset --hard`。
- 不要把 Garlic Claw 问题当成“只删桌面图标”处理；它还涉及构建模块、Dock、desktop entry、可能还有旧 autostart/推荐入口。
- 不要降低签名、回滚、content index、`/home` 保护和固件许可边界。
- 不要把 b43 私有固件塞进公开 ISO。
- 不要把 Papyrus 公钥复用成 Ming OS OTA 公钥。
- 不要发布 ISO、推 GitHub、改官网或 OTA discovery，除非用户再次明确授权。

## 3. 已经做过的验证

### 3.1 静态/单元验证

之前已跑过并通过：

- `python -m unittest discover -s tests -v`：623 tests OK，skipped=1。
- `python -m py_compile assets/*.py tests/*.py`：通过。
- `bash -n build_onion_os.sh resume_build.sh modules/*.sh`：通过。
- `git diff --check`：通过。

注意：这些只能证明代码级契约，不证明真实桌面、真实无线网、真实 OTA 和真实安装应用体验。

### 3.2 ISO 构建验证

已构建 ISO：

- `output/ming-os-26.4.1-home-amd64.iso`
- SHA256：`671caf31264900765bbcfcddb82d0fa23231472f61c4c7b07e65227a7a9018bf`

已确认：

- ISO 9660 可启动镜像。
- BIOS El Torito：`/isolinux/isolinux.bin`。
- UEFI El Torito：`/boot/grub/efi.img`。
- ISO 内核 SHA256 与源内核一致。
- 构建日志 R4 门禁已通过。

### 3.3 Live 和安装后 VM 验证

已在 VirtualBox 中做过：

- BIOS Live 启动安装器。
- BIOS Live + 32GB 临时 VDI 安装。
- 安装完成后弹出 ISO，从 VDI 启动。
- 首次进入桌面。
- 终端可打开。
- Firefox ESR 可启动。
- 星火应用商店可启动。
- 有线 NAT 网络可访问公网。
- Papyrus 入口和权限检查。
- 小组件、Dock、桌面图标基本视觉检查。

需要强调：VirtualBox 不能代表旧真机，尤其是 Wi-Fi、音频、背光、电池、输入法。

## 4. VM 当前状态

测试 VM 名称：`MingOS-2641-Install-BIOS-20260726-1726`。

重要状态：

- ISO 已从虚拟光驱移除。
- 启动顺序已改为 disk 优先。
- 系统已安装到 32GB 临时 VDI。
- 为了绕过卡住的欢迎向导，测试中在 VM 当前用户下写过 `~/.config/ming-os/welcome-done`，并结束过 `ming-welcome` 进程。
- 这台 VM 适合继续复查“安装后系统”，但不再是完全纯净首次启动状态。

如果要复现纯净首次启动问题，请重新安装一台新的 VM，或恢复安装前快照。

## 5. 建议 Claude 先使用的技能

建议依次使用：

1. `completion-blindspot-check`：这是高影响、多模块、涉及安全和发布阻断的任务。需要区分事实、推测、未验证项。
2. `systematic-debugging`：用于 Papyrus 权限、OOBE 输入、Live 桌面空白等问题的根因排查。
3. `test-driven-development`：每个 P0/P1 修复都先补失败测试，再改生产代码。
4. `verification-before-completion`：任何“修好了”的结论必须用 VM 或真机证据确认。
5. `receiving-code-review` 或 `requesting-code-review`：P0 修复完成后建议做一次独立审查。
6. `finishing-a-development-branch`：准备重新构建 ISO 前再用，避免漏掉门禁。

不建议一上来就并行大规模改动。先修 Papyrus/Garlic 和 OOBE，因为这两个最直观、最阻塞。

## 6. 修复顺序建议

### 第一阶段：Papyrus 与 Garlic Claw

目标：用户安装后应该看到 Papyrus，而不是 Garlic Claw；Papyrus 必须能由普通用户启动。

先看这些文件：

- `modules/04_garlic_claw.sh`
- `modules/02_apps.sh`
- `modules/03_desktop.sh`
- `build_onion_os.sh`
- `assets/vendor/papyrus/`
- `tests/test_papyrus_integration.py`
- `tests/test_desktop_regressions.py`
- `tests/test_dock_lifecycle.py`
- `tests/test_runtime_dependencies.py`

已确认现象：

- 桌面和 Dock 显示 Garlic Claw。
- 桌面和 Dock 不显示 Papyrus。
- `/usr/share/applications/papyrus.desktop` 存在。
- `Exec=/usr/bin/papyrus %U`。
- `/usr/bin/papyrus -> /opt/papyrus/launch-papyrus`。
- 普通用户访问 `/opt/papyrus` 报权限不够。
- `file /usr/bin/papyrus` 显示 broken symbolic link。
- 执行 `/usr/bin/papyrus --version` 报权限不够。

推荐 TDD：

1. 增加/补强测试：rootfs 中不得有 Garlic Claw desktop、Dock 项、autostart、推荐入口。
2. 增加/补强测试：Papyrus desktop entry、Dock 项、桌面图标必须存在。
3. 增加/补强测试：`/opt/papyrus`、启动器、图标和签名资产对普通用户可读可执行。
4. 增加/补强测试：`/usr/bin/papyrus --version` 或等价 smoke command 不应权限失败。
5. 再改安装模块。

验收标准：

- 桌面没有 Garlic Claw。
- Dock 没有 Garlic Claw。
- 应用抽屉没有 Garlic Claw。
- 桌面、Dock、应用抽屉能看到 Papyrus。
- 普通用户能启动 Papyrus 图形窗口。
- Papyrus agent 基本命令链和 Browser Bridge 至少完成一次 smoke test。

### 第二阶段：首次设置 OOBE

目标：第一次开机不能卡在欢迎向导；流程要像手机/Windows 一样能一路点完。

先看这些入口：

- `modules/03_desktop.sh` 中 `setup_welcome_wizard()`
- `modules/03_desktop.sh` 中 `setup_account_oobe()`
- autostart 文件生成逻辑
- 快捷键、锁屏、窗口 keep-above/focus 逻辑

已确认现象：

- 欢迎向导里按钮对键盘和鼠标触发不可靠。
- 测试时必须手工写 `welcome-done` 才能继续。
- 账户设置向导在欢迎向导之后弹出，顺序混乱。
- 欢迎向导期间 Ctrl+Alt+T 触发锁屏，不是终端。

推荐修复方向：

- 把欢迎向导和账户向导合成一个线性流程，或至少保证账户设置先于完成页。
- Gtk 按钮必须支持 Enter/Space。
- 不要让欢迎窗口抢焦点到无法点击。
- 首次 OOBE 阶段不要让 Ctrl+Alt+T 误触锁屏。
- 留空密码保持免密的文案要清楚：普通用户能理解“留空=开机自动进入桌面，需要授权时再设置”。

验收标准：

- 新装 VM 第一次进入桌面后，可以只用鼠标完成 OOBE。
- 可以只用键盘 Tab/Enter 完成 OOBE。
- OOBE 完成后不会再重复弹出。
- 完成后进入普通桌面，终端快捷键正常。

### 第三阶段：Live 安装器桌面

目标：Live 模式不能让用户最小化安装器后看到空桌面。

先看这些入口：

- `modules/03_desktop.sh` 中 installer session、Calamares launcher、ming-installer session 相关逻辑。
- `ming-live-installer.service` 和 LightDM installer session 配置。

已确认现象：

- BIOS Live 安装器出现后，最小化安装器，只剩深绿色背景。
- 带 32GB VDI 后仍复现。
- Ctrl+Alt+T 没有可见终端。

推荐修复方向：

- Live 安装器会话应保留一个明确“返回安装器”的入口。
- 可以禁用最小化按钮，或最小化后显示恢复按钮。
- 不一定要启动完整桌面，但必须避免用户进入无控件状态。

验收标准：

- 最小化安装器后仍能明显恢复安装器。
- 用户能看懂自己处在安装模式。
- 不出现只有背景、无入口、无提示的状态。

### 第四阶段：桌面图标、Dock、小组件

目标：安装后桌面清楚、少重复、入口正确。

已确认现象：

- Firefox ESR 桌面图标重复。
- 完成首次设置后，完整右侧小组件消失，只剩顶部时间胶囊。
- Dock 能显示，但内容包含旧 Garlic。

建议修复：

- 合并 Firefox wrapper 与 Firefox ESR desktop shortcut 生成逻辑。
- 小组件应有明确展开/收起入口，不能完成 OOBE 后不可发现。
- Dock 项只保留目标产品入口：Ming 设置、应用抽屉、终端、Firefox ESR、文件、星火商店、Papyrus、更新/电源等。

验收标准：

- 桌面没有重复 Firefox。
- 小组件音量/亮度/网络/电源入口可发现。
- Dock 图标全部可解析，不出现空白图标。

### 第五阶段：网络与硬件控制

目标：设置页和小组件给出的状态要和真实系统一致。

已确认：

- VirtualBox NAT 有线网络可上网。
- `ip addr` 有 `10.0.2.15/24`。
- `ip route` 有默认路由。
- `curl -I https://deb.debian.org` 成功。
- 但 `ming-device-control ethernet-status --json` 里的 IPv4、DNS、Gateway 字段为空。
- 无无线硬件时 Wi-Fi 诊断能返回 `diagnostic_unavailable`，这符合当前 VM 环境。
- VirtualBox 中音频只有 PulseAudio null sink，不能证明真机音频失败。
- 亮度使用 xrandr 软件回退，显示 100%。

建议修复：

- `ming-device-control` 的有线状态应正确填 IPv4、DNS、Gateway。
- 音频和亮度状态要区分“没有硬件”“虚拟机环境”“系统错误”。
- 真实 Wi-Fi 测试必须在旧笔记本上做：中文 SSID、密码弹窗、连接成功、认证失败、热点消失。

### 第六阶段：应用安装和启动链

目标：解决 26.4.0 用户反馈的“安装后打不开、首开异常、关闭后再打不开”。

本轮未做完整闭环，必须补测：

- Firefox 下载普通 `.deb`，安装后首开、关闭、再打开。
- Firefox 下载 AppImage，导入后首开、关闭、再打开。
- 星火应用商店安装应用，首开、关闭、再打开。
- 安装后 desktop/icon/app drawer/Dock 缓存刷新。

建议从这些文件入手：

- `assets/ming-launch.py`
- `assets/ming-package-installer.py`
- `assets/ming-appimage-installer.py`
- `assets/ming-app-drawer.py`
- `assets/ming-phone-desktop.py`
- `tests/test_launch_results.py`
- `tests/test_package_installer.py`
- `tests/test_firefox_app_chain.py`

注意：不要为了让软件都能启动而放宽 unsafe Exec、sh -c、shell 操作符等安全限制。

### 第七阶段：OTA 一键全自动

目标：设置页、电源菜单、命令入口三种更新方式都像 Windows/安卓一样，点一次后自动重启并完成。

本轮未做真实升级验收。

必须保留的边界：

- 26.3 全系列可以升级到 26.4.1。
- 26.4 preview/stable 可以升级到 26.4.1。
- 26.2、同版本、降级应拒绝。
- 不破坏签名、回滚、content index、`/home` 保护。
- `ming-ota-run` 已启动事务不能重复执行。

建议看：

- `modules/06_ota_update.sh`
- `assets/ming-update` 相关入口，如果存在。
- `tests/test_update_single_flow.py`
- `tests/test_ota_2641_compatibility.py`

真实验收建议：

- 准备 26.3.x VM 快照。
- 准备 26.4.0 VM 快照。
- 分别走设置页、电源菜单、命令入口。
- 观察是否自动设置 GRUB next-entry、重启进入 OTA 安装器、安装后回到 26.4.1。

## 7. 构建日志中不要忽略的问题

问题报告已经列出，这里只写接手判断：

- Spark Store systemd unit 警告不是最高优先级，但会影响发布质量。
- Garlic Claw 构建阶段权限拒绝和安装后 Garlic 残留互相印证，应整体移除 Garlic 链路。
- `07_finalize` 还检查已移除的 `ming-edge.desktop`，说明 Edge -> Firefox 替换没有完全收口。
- Spark Store postinst 写 `/root/.config/mimeapps.list` 不合理，应改为系统级或目标用户级关联。

## 8. 建议的验证命令

每轮修复后至少跑：

```powershell
cd C:\Users\Administrator\.config\superpowers\worktrees\onion-os\daily-function-chain-2641
python -m unittest discover -s tests -v
python -m py_compile assets\*.py tests\*.py
bash -n build_onion_os.sh resume_build.sh modules/*.sh
git diff --check
```

如果在 Linux/WSL 构建环境内：

```bash
python -m unittest discover -s tests -v
python -m py_compile assets/*.py tests/*.py
bash -n build_onion_os.sh resume_build.sh modules/*.sh
git diff --check
```

构建 ISO 后重新做：

- BIOS Live：安装器、最小化、恢复入口。
- UEFI Live：启动时间。
- BIOS 安装：32GB 临时 VDI。
- 安装后首次启动：OOBE 鼠标和键盘。
- 桌面：图标、Dock、小组件。
- 应用：Firefox、星火、Papyrus。
- 网络：有线状态 JSON 和真实联网。
- 真实硬件：Wi-Fi、音频、背光、电池、输入法。

## 9. VirtualBox 常用命令

当前 VM：

```powershell
$vm = "MingOS-2641-Install-BIOS-20260726-1726"
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" showvminfo $vm --machinereadable
& "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" controlvm $vm screenshotpng "C:\path\to\shot.png"
```

如果要重新测试纯净安装，建议新建 VM，不要直接覆盖当前 VDI。当前 VDI 已经被测试过程写过欢迎完成标记。

## 10. 给 Claude 的开始步骤

建议 Claude 接手后按这个顺序做：

1. 先读问题报告，不要直接开始改代码。
2. 运行 `git status --short --branch`，确认脏工作树。
3. 先修 Papyrus/Garlic，因为这是用户已经肉眼指出的问题，也是 P0。
4. 为 Papyrus/Garlic 写失败测试。
5. 修生产代码。
6. 跑单元和静态门禁。
7. 重建 ISO。
8. 新建纯净 VM 验证桌面不再显示 Garlic、Papyrus 能启动。
9. 再修 OOBE。
10. 再修 Live 空桌面。
11. 最后做应用安装链和 OTA 真实闭环。

如果时间不足，至少先做到：

- Garlic 完全消失。
- Papyrus 可见且可运行。
- 首次向导不卡用户。
- Live 最小化安装器后不空白。

## 11. 不确定项和风险

- Papyrus 失败的直接表现是权限和符号链接问题，但安装脚本里可能还有签名、AppImage、Tauri 更新清单、浏览器桥接扩展的问题，不能只 chmod 完就宣布修好。
- VirtualBox 无 Wi-Fi、无真实音频、无真实背光、电池，因此硬件相关必须真机验收。
- 性能是否强于 Win7 还没有基准数据，不能凭主观启动速度判断。
- OTA 真实升级链没有跑过，代码测试通过不能证明用户点一次后自动完成。
- 星火应用商店能打开不等于能正确安装和启动应用，必须做真实安装闭环。

## 12. 交付给用户时的说法建议

用户可能不熟悉专业术语，解释时建议用简单话：

- “desktop entry” 可以说成“桌面启动入口”。
- “Dock” 可以说成“底部应用栏”。
- “OOBE” 可以说成“第一次开机设置向导”。
- “rootfs” 可以说成“系统预装文件”。
- “JSON 状态为空” 可以说成“系统能上网，但设置页读到的网络详情不完整”。

不要只说“测试通过”。要说清楚是“单元测试通过”“虚拟机通过”“真机未测”。

## 13. 任务后审视

- 最没把握的事：Papyrus 的失败是否只有权限和符号链接问题尚不确定；修复后必须继续验证 agent 命令、浏览器桥接和签名更新链。
- 你可能遗漏的事：当前 VM 不是纯净首次启动状态，因为测试中写过欢迎完成标记；下一轮验收首次设置必须用新 VM 或快照。
