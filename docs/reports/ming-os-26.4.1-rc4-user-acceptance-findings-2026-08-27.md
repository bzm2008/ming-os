# Ming OS 26.4.1 RC4 用户验收问题记录

生成时间：2026-08-27

测试原则：本文件保留用户现场反馈与已取得的证据，并单独记录后续源码处置。源码测试通过不等于新镜像或真实硬件已经通过，所有运行时结论仍以之后的 ISO/VM/真机验收为准。

## 2026-08-27 源码处置更新

本轮未构建 ISO、未操作 VM、未修改生产 OTA/官网/密钥，也未部署生产服务。以下状态均为“源码已处理、待下一镜像验证”，不能覆盖下方已有的 RC4 现场证据。

- `RC4-APP-011`：下一镜像不再提供 Spark/APM/ACE。新增 Ming 应用商店，界面与信息结构参考 KDE Discover，首批只接入受信的 Ming、Debian APT 和厂商官方 Provider。PackageKit 仅在 `packagekitd`、`aptcc` 和 `pkcon` 都可用时声明为可选只读能力，当前不作为写操作后端；安装、更新和卸载仍由 Ming 受限授权 helper 执行，并在结束后读回 DPKG/APT 状态。
- Spark 迁移：升级时使用 `apt-get remove --no-auto-remove`，不执行 `autoremove`，不删除 `/opt/apps` 或用户数据。构建门禁逐项拒绝 APM 启动器、兼容运行器、APT pin、旧 Polkit 策略、systemd 服务和 desktop entry 残留。
- `RC4-UX-001/002/010`：增加 Xiahai 沙盒权限修复和受控图形降级启动；补齐 Ming 工具箱桌面入口；清理用户桌面与 `/etc/skel` 中的 Calamares 残留。
- `RC4-UX-003/008/011/012`、`RC4-SES-013/014`：Dock 改为响应式 32/36/40px、居中、12px 底部留白和 148% 悬停缩放；真全屏或应用抽屉打开时移除工作区占位并隐藏 Dock。会话协调器会跟踪当前工作区的可见全屏窗口、Plank 重启和抽屉 PID，避免独立授权弹窗抢焦点后错误恢复 Dock。
- `RC4-UX-004/005/006/007`：设置页移除固定窄宽，增加响应式断点和页面错误占位；支持导入 PNG/JPEG 壁纸并读回；校时状态区分已同步、等待网络、服务未运行、DBus 不可用和失败。
- 小组件：状态迁移到 A 方案 v2，默认折叠；胶囊只保留时间、日期、Wi-Fi、电池和 Ming 展开按钮，资源采样仅在展开时运行。
- `RC4-DIAG-016`：Staging 源码增加服务端二次解包、限制、脱敏、重打包和新 SHA256；恶意路径、链接、设备、二进制、嵌套压缩及超限包会被拒绝。相关 Node 测试已通过，但没有发现与生产隔离的远程 Staging 发布入口，因此本轮没有远程部署，避免误改生产服务。

源码验证结果：`python -m unittest discover -s tests` 执行 1301 项，其中 1294 项通过、7 项因当前 Windows 缺少 GTK 或完整 Linux 文件语义而跳过；修改的 33 个 Python 文件编译通过，5 个 Shell 文件语法通过，Staging Node 测试 19 项通过，`git diff --check` 通过。

### 2026-08-27 审计加固更新

本轮针对历史验收中尚未关闭的安全与可靠性缺口完成源码修复，仍未构建 ISO、操作 VM 或部署生产服务：

- 诊断包生成端使用随机临时目录和归档名，限制 128 个文件、单文件 4 MiB、总内容 5 MiB、归档 8 MiB；仅收集普通 UTF-8 文本，拒绝符号链接、硬链接、设备文件、二进制和路径穿越，并在客户端统一脱敏。
- 诊断上传端不再直接执行无约束 `tar -xzf`；先用 `tarfile` 完整校验 gzip、成员类型、路径、重复名、嵌套压缩、大小、文本编码和控制字符，再重命名、脱敏、以 0600 权限重新打包后上传。
- 旧 Spark/APM/ACE 退役改为硬门禁：`apt-get remove --no-auto-remove` 失败或任一旧包仍为 `ii` 状态时，收尾流程立即失败；不执行 `autoremove`，不触碰 `/opt/apps` 或用户数据。
- Xiahai 的 `--disable-gpu` 只在明确 GPU/图形初始化错误时触发；权限错误、文件缺失、普通退出和沙盒错误不会被误判为 GPU 故障。商店图形刷新在缺少 `/run/user/<uid>` 时明确返回刷新告警，不再跳过检查后报告成功。

新增行为测试先确认旧实现失败，再验证修复：诊断归档、旧组件清理、Xiahai 回退和商店刷新相关回归均通过。最新完整源码验证为：`python -m unittest discover -s tests` 执行 1312 项，其中 1305 项通过、7 项跳过；修改过的 Python 编译通过，修改过的 Shell 语法通过，`git diff --check` 通过。上述结果仍不等价于 ISO、VM 或真实硬件验收。

## 验收环境

- RC4 ISO：`output/ming-os-26.4.1-home-amd64-rc4.iso`
- ISO SHA256：`e21f6e5a016186c5a5dc3badf5b1bb3f42b012eee61637583adac00eb65dae61`
- 测试机：`MingOS-2641-rc4-smoke-20260826-UEFI`
- 固件：UEFI；VirtualBox；2 vCPU；4 GB RAM；NAT。
- 硬盘启动复核：`boot1=disk`、`boot2=none`、光驱 `IDE-0-0=none`。

## 结论

用户在 RC4 验收中发现多项桌面与设置问题。此前截图来自 Live 会话，但其中一部分有很高的安装后复发风险：同一套桌面、Dock、设置和应用启动器会被安装到目标磁盘。不能仅以 Live 环境为由关闭这些问题。

其中 Xiahai Xiaoming 已取得硬盘启动后的异常证据：桌面入口图标退化为通用齿轮，点击后出现没有可读内容的黑色窗口。其余项目应在硬盘系统完成 OOBE 后逐项再次点击确认；在完成复现前，状态标为“安装后高风险待复现”，而不是“已经在安装后确认”。

2026-08-27 再次核对已确认该 VM 从硬盘启动，光驱为空，不能归因为误进入 Live。硬盘系统实际读取的 `/etc/ming-os-build.json` 为 `2641-rc4-550d0b83a3a2-20260826T103412Z`，与 RC4 ISO metadata 的 build id 一致，已排除“装成 RC3/旧镜像”。硬盘画面同时包含 RC4 的 Mint 壁纸、新抽屉图标和小组件；更符合证据的判断是：RC4 安装后的部分用户配置、桌面资源或升级迁移未完整继承。RC4 build metadata 中记录的完整 source commit 仍无法在当前 Git 对象库中解析，构建可追溯性问题需另行处理，但它不是本次硬盘系统版本错误的证据。

## 问题清单

### RC4-UX-001 Xiahai Xiaoming 无法正常启动

- 严重级别：P0
- 状态：硬盘 RC4 已稳定复现，已取得进程日志。
- 用户反馈：`Xiahai Xiaoming` 仍然无法启动。
- 现场证据：硬盘启动截图 `E:/llinux os/test-evidence/rc4-hard-disk-current.png` 中，桌面 `Xiahai Xiaoming` 图标显示为通用齿轮；其下方出现无可读内容的黑色窗口。
- 影响：预装应用入口不可用，用户无法判断应用是否启动、崩溃或被权限链阻断。
- 已确认：实际入口为 `/opt/xiahai-xiaoming/xiahai-xiaoming %F`；目录权限为 `root:root 0755`，二进制为 `root:root 0755` 的 x86_64 ELF，普通用户有读取与执行权限。直接运行 `timeout 5 /opt/xiahai-xiaoming/xiahai-xiaoming` 后，终端连续记录 `ERROR:gpu_process_host.cc(976)] GPU process launch failed: error_code=1002`、`ERROR:zygote_communication_linux.cc(297)] Failed to send GetTerminationStatus message to zygote` 与网络服务崩溃重启。证据：`E:/llinux os/test-evidence/rc4-hard-disk-xiahai-timeout-repro-20260827.png`。
- 初步归类：这不是快捷方式缺失或用户无权读取 `/opt`，而是 Chromium/Electron 渲染子进程在当前 VirtualBox 图形环境下未能启动。后续修复需先验证受控的 GPU/软件渲染兼容参数或白名单启动包装，不能只修改 desktop entry。

### RC4-UX-002 Ming 工具箱未显示在桌面

- 严重级别：P1
- 状态：硬盘 RC4 已确认。
- 用户反馈：桌面没有 `Ming 工具箱` 入口。
- 证据：用户桌面/应用抽屉截图 `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-e6381b29-d448-455e-aa48-daddde4a586c.png` 未显示桌面快捷方式；应用抽屉能看到 `Ming 工具箱`，说明应用条目与桌面快捷方式投放链可能不一致。
- 已确认：实际本地化桌面目录 `$(xdg-user-dir DESKTOP)` 中存在 `ming-settings.desktop`、`ming-files.desktop`、`ming-firefox.desktop`、`ming-terminal.desktop`、`spark-store.desktop`、`xiahai-xiaoming.desktop`，但没有 `ming-toolbox.desktop`。证据：`E:/llinux os/test-evidence/rc4-hard-disk-localized-desktop-20260827.png`。
- 影响：用户很难发现 Wine、Android、驱动与诊断等工具箱入口。
- 后续取证：核对 `/usr/share/applications/ming-toolbox.desktop`、桌面组织器规则和升级迁移逻辑；同时验证应用抽屉条目是否依赖同一 desktop source。

### RC4-UX-003 全屏应用上方仍显示 Dock

- 严重级别：P1
- 状态：Live 已确认；硬盘 RC4 的真实全屏仍待独立复核。
- 用户反馈：软件全屏后 Dock 仍叠在窗口最上层，不能实现真正全屏。
- 证据：用户截图 `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-3c696fa9-aa70-49ff-8e26-1cd1894b5e20.png` 中，星火应用商店已占满屏幕，Dock 仍在底部前景显示。
- 影响：视频、演示、游戏和全屏应用会被 Dock 遮挡；现有“全屏时降低 Dock”的契约没有产生可见效果。
- 硬盘复核边界：同一星火窗口按 F11 后，窗口最大化且 Dock 消失，但标题栏仍存在，未能证明该状态是 EWMH 的真实全屏。证据：`E:/llinux os/test-evidence/rc4-hard-disk-spark-f11-fullscreen-20260827.png`。不得把该结果误写成“硬盘真全屏已通过”或“硬盘复现了 Live 的真全屏问题”。
- 后续取证：在硬盘系统记录 `xprop -root _NET_ACTIVE_WINDOW`、活动窗口的 `_NET_WM_STATE_FULLSCREEN`、Plank 窗口层级与 `ming-dock` 日志，再分别验证普通窗口、最大化、全屏和应用抽屉。

### RC4-UX-004 设置左侧栏图标/布局缺失

- 严重级别：P1
- 状态：当前硬盘分辨率下未复现“图标缺失”，但右侧内容加载失败，保留为分辨率/状态相关风险。
- 用户反馈：设置左侧栏出现 UI 缺失。
- 证据：用户截图 `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-c4dbc0dd-5a7e-441e-94d5-6fa4a141f94c.png` 中，左侧多个项目只显示细窄占位区域或缺少正常图标/文字对齐。
- 当前复核：`E:/llinux os/test-evidence/rc4-hard-disk-settings-open-20260827.png` 中左侧图标与文字在 792x600 虚拟屏下均出现，说明该症状没有在此尺寸下稳定复现；但右侧内容区并未正确显示。
- 影响：设置分类难以辨认，破坏基本可用性与视觉一致性。
- 后续取证：核对 GTK/libadwaita 版本、图标主题查找结果、侧栏最小宽度、CSS 规则和缩放因子，并在用户截图的分辨率下重测。

### RC4-UX-005 设置全屏后内容仍局限于狭窄区域

- 严重级别：P1
- 状态：硬盘 RC4 已确认更严重的内容区布局/加载异常。
- 用户反馈：设置最大化后，内容仍只在很窄的中间区域显示。
- 证据：同 `codex-clipboard-c4dbc0dd-5a7e-441e-94d5-6fa4a141f94c.png`；窗口宽度很大，但主要内容卡片约为中等固定宽度，左右出现大面积空白。
- 已确认：在硬盘系统打开设置后，左侧分类可被键盘选中和切换，但右侧内容区持续为空或被挤到窗口下方；`E:/llinux os/test-evidence/rc4-hard-disk-settings-account-20260827.png` 保存了复现画面。底部同时有 Dock 覆盖窗口。该现象比单纯的“限宽”更严重，当前无法验证壁纸导入、Wi-Fi 连接等设置页操作。
- 影响：大屏或全屏时信息密度过低，网络列表与外观预览不易使用；当前实际状态下设置内容不可用。
- 后续取证：分别记录窗口实际宽度、Adw clamp/breakpoint、内容容器 `hexpand` 和最大宽度配置；读取 `ming-settings` 的标准错误与 Gtk Inspector 布局，区分页面构建失败、垂直布局错误与内容被 Dock/窗口管理器覆盖。

### RC4-UX-006 设置页校时服务异常

- 严重级别：P1
- 状态：硬盘 RC4 已确认。
- 用户反馈：校时服务异常。
- 证据：同设置截图显示“校时服务异常”，提示“无法读取系统校时服务；可尝试重试”。
- 影响：用户无法确认系统时间是否自动同步，证书、软件源、OTA 与日志时间都可能受影响。
- 已确认：硬盘系统执行 `timedatectl show-timesync --all` 返回 `Failed to parse bus message: 没有到主机的路由`。证据：`E:/llinux os/test-evidence/rc4-hard-disk-timesync-status-20260827.png`。这说明设置页报错有系统层来源，不是纯 UI 文案问题。
- 后续取证：采集 `systemctl status systemd-timesyncd`、网络状态、`ming-time-sync` 输出与 UI 调用日志。无网络时应呈现“等待网络”，不能笼统报服务异常。

### RC4-UX-007 外观与设计只能选默认壁纸，不能导入自定义壁纸

- 严重级别：P1
- 状态：安装后高风险待复现。
- 用户反馈：外观与设计只能选择默认壁纸，无法自行更改壁纸。
- 影响：用户的基础个性化入口不可用。
- 已知实现：源码包含内置壁纸扫描与安全导入逻辑，但用户界面未提供可用的自定义文件选择闭环，或入口/状态回读失效。
- 后续取证：在硬盘系统中选择一张 PNG 和 JPEG，记录文件选择器是否出现、`ming-appearance-control import-wallpaper` 的返回、用户目录保存、缩略图生成和 Xfce 背景读回。

### RC4-UX-008 应用抽屉底部出现异常大空白

- 严重级别：P2
- 状态：Live 已确认，安装后高风险待复现。
- 证据：用户截图 `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-e6381b29-d448-455e-aa48-daddde4a586c.png` 的橙色标记区域显示抽屉底部有明显空白，未与桌面底边/Dock 正确衔接。
- 影响：应用抽屉像未完成布局，降低桌面可用面积。
- 后续取证：记录屏幕工作区、抽屉高度、底部锚点、Dock 高度、抽屉滚动容器与全屏状态。

### RC4-REL-009 安装后界面继承风险与构建可追溯性异常

- 严重级别：P1
- 状态：硬盘系统为 RC4 已确认，根因待取证。
- 用户反馈：RC4 Live 已显示更多新图标和 UI，而安装后的系统看起来只保留少量小组件变化。
- 已确认：测试 VM `MingOS-2641-rc4-smoke-20260826-UEFI` 的首启动设备为硬盘，光驱无挂载，虚拟硬盘为 `E:/VirtualBox VMs/MingOS-2641-rc4-smoke-20260826-UEFI/MingOS-2641-rc4-smoke-20260826-UEFI.vdi`。硬盘系统的 `/etc/ming-os-build.json` 与 ISO 的 build id 均为 `2641-rc4-550d0b83a3a2-20260826T103412Z`；`E:/llinux os/test-evidence/rc4-hard-disk-build-identity-20260827.png` 保存了读取证据。硬盘直接截屏 `E:/llinux os/test-evidence/rc4-hard-disk-vbox-capture-20260827.png` 仍能看到 Mint 壁纸、新抽屉图标与右上组件，说明不是完整的旧系统回退。
- 可追溯性异常：`output/ming-os-26.4.1-home-amd64-rc4.build.json` 和硬盘 build identity 都记录 `source_commit=550d0b83a3a21d833a54a0a1a5d2232dced7ed58`；该完整对象在当前验收仓库中无法解析。现阶段不能由此断定 ISO 内容错误，但发布前必须消除这一歧义。
- 影响：RC4 安装后存在桌面资源或用户配置未完整继承的风险，造成 Live 与安装后体验不一致；构建元数据又不能直接指向可审计源码对象，增加排障成本。
- 后续取证：从硬盘系统读取主题/图标包文件清单、`/etc/skel` 与实际用户配置的差异；从 ISO rootfs 读取同一组文件并逐项比较。构建脚本还必须在写入 build metadata 前验证 source commit 可由 Git 解析。

### RC4-UX-010 安装后残留 Calamares desktop 文件

- 严重级别：P2
- 状态：硬盘 RC4 已确认。
- 已确认：`$(xdg-user-dir DESKTOP)` 存在 0 字节的 `calamares-install-debian.desktop`。该文件不会构成可用安装入口，但表明安装清理门禁未完全清除 Live/Calamares 残留。
- 影响：升级、用户桌面同步或后续桌面管理规则可能重新暴露无效的安装入口。
- 后续取证：检查安装后清理脚本、Calamares users module 和 desktop organization service 的执行顺序，确认 Live-only desktop 文件在目标系统中被删除而非写成空文件。

### RC4-APP-011 Spark 正式启动链退出，普通软件安装未能进入详情

- 严重级别：P0
- 状态：硬盘 RC4 已确认正式入口异常；本轮普通软件安装闭环被阻断，不能报通过。
- 已确认：通过正式入口 `/usr/local/bin/ming-spark-store` 启动后，`/home/user/.cache/ming-os/spark-store.log` 先记录 `Spark Store process or window is ready` 和 `Renderer process is ready.`，随后记录 `Cleaning up temp dir` 与 `Done, exiting`。同一日志还记录 `未检测到 apm 命令`。证据：`E:/llinux os/test-evidence/rc4-hard-disk-spark-wrapper-log-after-search-20260827.png`。
- 进程读回：日志写出退出后，`pgrep -af spark-store` 没有任何输出。证据：`E:/llinux os/test-evidence/rc4-hard-disk-spark-processes-after-wrapper-20260827.png`。
- UI 现象：商店可搜索并显示 `fastfetch` 的 `SPARK` 结果，见 `E:/llinux os/test-evidence/rc4-hard-disk-spark-wrapper-search-neofetch-20260827.png`；但结果卡片不能稳定进入详情，无法到达“安装 -> 授权 -> 下载 -> 包状态读回”的步骤。本轮没有安装任何软件。
- 影响：用户反馈的“星火无法安装软件”在正式启动路径上仍未被关闭；APM 扩展后端则明确不可用。
- 后续取证：从正式 wrapper 的启动到退出按时间关联 Spark 主进程、renderer、桌面 deep-link、安装动作事件和 `/var/log/ming-spark-package-control.jsonl`；先修复进程生命周期和详情页事件，再验收一个普通 Spark 包与一个 APM/ACE 包。

### RC4-UX-011 Dock 覆盖硬盘系统普通应用窗口

- 严重级别：P1
- 状态：硬盘 RC4 已确认。
- 已确认：硬盘系统中启动 Ming 工具箱与星火商店时，Dock 仍出现在两个普通应用窗口的底部前景。工具箱证据：`E:/llinux os/test-evidence/rc4-hard-disk-toolbox-launch-20260827.png`；星火证据：`E:/llinux os/test-evidence/rc4-hard-disk-spark-launch-20260827.png`。
- 影响：窗口底部内容、操作按钮和下载状态可能被遮挡。该问题与 Live 真全屏问题相关，但不是同一个已完成复现的结论。
- 后续取证：检查 Plank layer、自动隐藏条件和 `ming-dock` 状态机，分别与最大化、真全屏、抽屉状态关联。

### RC4-UX-012 小组件与 Dock 未应用已确认的视觉方案

- 严重级别：P1
- 状态：Live 与硬盘 RC4 均有用户现场观察，待以设计规格逐项复核。
- 用户反馈：无论 Live 还是安装到硬盘后，小组件变化很小；Dock 仍是旧 RC3 逻辑，与此前网页预览中已确认的小组件胶囊、展开/收起交互、紧凑自适应 Dock 和动画方案有明显差异。
- 影响：RC4 的实际界面与已确认设计不一致，用户无法依靠设计预览判断最终交付；同时旧 Dock 逻辑与 RC4-UX-011 的窗口遮挡问题共存。
- 后续取证：以确认后的 A 方案规格逐项对照 Live rootfs、`/etc/skel`、首次登录迁移和硬盘用户配置，校验胶囊时间/日期/网络/电池/展开图标、Win 键行为、资源信息、Dock 尺寸、居中、自适应尺寸与悬停缩放，而非仅检查源码字符串。

### RC4-SES-013 Xfwm4 会话错误伴随窗口异常

- 严重级别：P2
- 状态：硬盘 RC4 已确认，尚未证明与 Dock/设置问题的因果关系。
- 已确认：`/home/user/.xsession-errors` 连续出现 `xfwm4: GLib-CRITICAL ... g_hash_table_lookup: assertion 'hash_table != NULL' failed`；同时 `blueman-applet` 报告 `Adapter is None`。证据：`E:/llinux os/test-evidence/rc4-hard-disk-xsession-errors-tail-20260827.png`。
- 影响：Xfwm4 是窗口层级、全屏与工作区行为的核心组件。该错误必须与 Dock 层级异常一起排查，但不能在未做最小复现前草率认定为唯一根因。
- 后续取证：按一次普通窗口、最大化、真实全屏、应用抽屉的操作顺序，时间戳关联 `xfwm4`、Plank、`ming-dock` 与设置日志。

### RC4-SES-014 已退出窗口的残留绘制区域

- 严重级别：P1
- 状态：硬盘 RC4 有直接屏幕证据，根因待定位。
- 已确认：Spark wrapper 已写入 `Done, exiting`，且 `pgrep -af spark-store` 无结果时，终端截图后方仍可见商店界面的彩色区域。证据：`E:/llinux os/test-evidence/rc4-hard-disk-spark-processes-after-wrapper-20260827.png`。
- 影响：这与此前“重复桌面窗口区域”的反馈一致；用户会看到已关闭窗口的残影，且可能影响后续点击与状态判断。
- 后续取证：以 Xfwm4、Picom/合成器、VirtualBox VBoxSVGA 的窗口损坏区域为边界，分别复测关闭 Spark、设置、Xiahai 后的重绘；须区分 VirtualBox 截屏/虚拟显卡问题与真机会出现的合成器缺陷。

## 需要在硬盘系统回归的最小矩阵

1. 完成 OOBE 后启动 Xiahai，记录进程、窗口和日志。
2. 检查桌面、Dock、应用抽屉是否各有且仅有一个 Ming 工具箱入口。
3. 用星火分别验证普通、最大化和真实全屏，确认 Dock 完全下沉。
4. 最大化设置窗口，检查侧栏图标与内容宽度。
5. 在有网络和断网两种状态下检查校时文案及 `systemd-timesyncd`。
6. 导入 PNG/JPEG 自定义壁纸，重启会话后确认仍生效。

## 非结论

- 本文件不说明 Spark、Wi-Fi、音频或 OTA 已通过验收。
- VirtualBox 无真实 Wi-Fi/蓝牙/笔记本电池，不能替代真机硬件验收。
- 在没有读取 Xiahai 与校时日志前，不把当前现象归因于单一源码模块。

### RC4-DIAG-015 用户确认的诊断上报链路

- 严重级别：通过（命令行完整链路）；设置页按钮因 RC4-UX-005 的内容区异常，尚未完成图形界面点击验收。
- 已确认：RC4 硬盘虚拟机先成功生成 `Ming-OS-诊断包`，再由 `ming-diagnostic-upload` 上传；客户端返回成功且退出码为 `0`。完整诊断包的报告编号为 `78a463007045a676103f340af9c0ea19`。
- 服务器验收：报告 JSON 与附件均已落在受限目录，权限为 `640 scallion:scallion`；服务已注册管理员查看路由 `/api/admin/ming-diagnostics`，管理员端可按报告编号查看和下载。客户端上传使用的是生产 HTTPS 地址 `https://ming.sca-hub.cn/api/ming-diagnostics/reports`。
- 脱敏验收：另一个只含测试标记的探针包返回编号 `c5d3b3a9a44d0c7b676b6e8903e4a5e7`。服务器保存的附件中，测试密码整行已删除，`/home/ming-probe/demo` 已变为 `/<redacted>/demo`。完整 RC4 诊断附件扫描也未发现 `/home` 或 `/root` 路径，以及 password、SSID、BSSID、token、secret、API key 等敏感字面量。
- 证据：`E:/llinux os/test-evidence/rc4-diagnostic-upload-probe-result-3-20260827.png`、`E:/llinux os/test-evidence/rc4-diagnostic-full-upload-result-20260827.png`。

### RC4-DIAG-016 服务端附件仅信任客户端脱敏

- 严重级别：P1
- 状态：生产环境问题已确认；Staging 源码已修复并通过自动化测试，尚未远程部署或进行生产发布。
- 已确认：生产服务端会脱敏报告 JSON，但对上传的附件仅计算 SHA256 后直接保存；没有在服务端解包检查、二次脱敏或拒绝敏感内容。当前 RC4 客户端正确完成了脱敏，所以本次实际上传的内容安全；但旧客户端、篡改客户端或未来客户端回归时，原始隐私数据仍可能被保存到服务器。
- 影响：诊断上报依赖单层客户端保护，不符合“出现问题时可安全上传日志”的纵深保护要求。
- 源码处置：服务端在写入前只接受受限 gzip tar 文本包，重新检查并替换敏感字段、路径、MAC/IP 等信息，再重新打包和计算 SHA256；无法确认安全的附件返回 400 且不落盘。生产环境仍保持原状，需在独立部署授权后发布。
