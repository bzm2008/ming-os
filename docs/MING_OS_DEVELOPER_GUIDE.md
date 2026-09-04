# Ming OS 开发者全方位手册

本手册面向参与 Ming OS 开发、构建、测试、桌面适配、软件商店、Wine、Android 和 OTA 的开发者。它描述仓库中的真实入口、边界和验收方法，不把旧 ISO、网页预览或未完成分支当作当前实现。

## 1. 先确认你在哪条线上

Ming OS 使用多个并行分支。开始工作前先运行：

~~~bash
git status --short --branch
git branch -a -vv
git worktree list
~~~

当前仓库快照中的分支职责如下：

| 分支或标签 | 用途 | 可信范围 |
| --- | --- | --- |
| master | 26.3.2 主线基线 | 已发布基线；不要把它误称为 RC4 |
| feature/ming-os-26.3.3 | 26.3.3 兼容与 OTA 改动 | 功能分支，需独立验收 |
| integration/transactional-ota-closure | 事务式 OTA、启动和 Live 安装器收尾 | 集成分支，不能替代发布验证 |
| fix/daily-function-chain-2641 | 26.4.1 RC4 桌面、商店、Wine、Android 和可续跑构建 | 当前最完整的 RC4 开发线；ISO 必须由该提交重新构建 |
| fix/device-controls-2641、fix/performance-recovery-2641、fix/spark-installer-2641 | 主题性修复分支 | 合并前需做接口和测试审查 |
| v26.3.1、v26.3.2、v26.3.3 | 发布标签 | 只读基线，不要在标签上开发 |

旧 ISO 只可作为历史参考。验收时必须同时记录 ISO 的 build metadata、source commit 和 SHA256。

## 2. 仓库结构

~~~text
build_onion_os.sh       主构建脚本；阶段、配置、缓存和续跑
continue_build.sh       兼容入口，转交主构建脚本的 resume 流程
resume_build.sh         从已有 chroot/checkpoint 恢复构建
fast_build_iso.sh       fast-test 配置入口
final_build_iso.sh      release 配置入口
rebuild_iso.sh          已有工作目录的显式重打包入口
modules/01_base.sh      debootstrap、APT、账户、启动和基础系统
modules/02_apps.sh      浏览器、运行库、Wine/商店相关应用
modules/03_desktop.sh   Xfce、Dock、组件、抽屉、图标、启动器
modules/04_garlic_claw.sh 旧版 Garlic 兼容模块（新分支可能已退役）
modules/05_security_tools.sh 安全工具和受限授权
modules/06_ota_update.sh OTA、A/B、回滚和升级门禁
modules/07_finalize.sh 收尾、desktop entry 和默认用户配置
modules/08_settings_hub.sh 设置入口和设置资源
assets/                  用户态 Python、Shell、图标、壁纸和受信资源
config/                  构建配置、安全策略和防火墙配置
tests/                   契约、单元、回归和构建门禁
scripts/ming_build_state.py 构建状态、输入哈希和 checkpoint 工具
docs/                    设计、计划、验收报告和本手册
~~~

不要在 E:\llinux os 工作区父目录执行项目级 Git 命令。实际仓库是 E:\llinux os\onion-os；RC4 工作树通常位于 E:\llinux os\codex-worktrees\onion-os\daily-function-chain-2641 或 Codex 管理的 worktree 中。

## 3. 系统架构

### 3.1 基础系统

Ming OS 基于 Debian 13/Trixie，目标架构为 amd64。默认桌面是 Xfce，窗口管理器是 Xfwm4，Dock 使用 Plank，显示管理器使用 LightDM。常用图形设置集中到 Ming Settings，文件操作集中到 Ming Files，应用抽屉由 Ming App Drawer 管理。

系统设计优先级是：

1. BIOS/UEFI 能启动并能完成安装；
2. 安装后的 GRUB、桌面会话和网络可用；
3. 软件安装、启动、卸载和 OTA 有真实结果读回；
4. 最后再做动画、主题和视觉细节。

### 3.2 用户态入口

所有受管应用都应使用统一启动器：

~~~text
desktop entry / Dock / 应用抽屉
        -> ming-launch
        -> 受控参数和环境
        -> 实际应用
~~~

不要在 desktop entry 中直接拼接用户输入、sh -c 或 eval。一个应用可以有多个展示位置，但只能有一个 canonical desktop entry，入口去重优先依据 desktop ID、包名、DPKG ownership 和 StartupWMClass。

核心用户态脚本：

- assets/ming-launch.py：应用启动、来源标记、结果读回；
- assets/ming-phone-desktop.py：桌面图标、文件夹和右上角小组件；
- assets/ming-app-drawer.py：系统应用扫描和抽屉状态；
- assets/ming-settings.py：图形设置界面；
- assets/ming-files.py、assets/ming-files-model.py：文件管理器和右键文件操作；
- assets/ming-device-control.py：无线、蓝牙、音频、亮度等设备控制。

### 3.3 Ming 应用商店

RC4 开发线的商店链路为：

~~~text
Ming 应用商店
  -> Provider/目录缓存
  -> ming-store-control
  -> ming-authorized-action
  -> Polkit 图形授权
  -> APT/DPKG 或 PackageKit（可用时）
  -> 版本和包状态读回
  -> desktop database、图标、Dock、抽屉刷新
~~~

商店 UI 不直接调用某个来源的安装器。Provider 首版包括：

- ming-official：Ming 官方目录，允许为空；
- debian-apt：Debian/Ming APT 和可信镜像；
- vendor-official：厂商官方 HTTPS 包；
- wine-official：受信 Wine 应用目录；
- spark-public：只读取公开、可验证的星火目录，不启动 Spark 客户端。

商店安装请求只允许包含 schema、request ID、UID、操作、Provider、软件 ID、期望版本和时间戳。请求不得携带命令、URL、本地任意路径或 shell 片段。

安装状态必须按以下阶段读回：

~~~text
解析 -> 下载 -> 校验 -> 等待授权 -> 安装 -> 读回 -> 刷新
-> 成功 / 刷新警告 / 失败
~~~

下载完成不等于安装成功。桌面刷新失败时必须显示“软件已安装，但桌面入口刷新失败”，不能把普通成功覆盖掉真实错误。

主线 master 的历史快照仍可见旧 Spark 客户端构建引用；新功能应以 RC4 分支的 Ming Store 适配器为准，不要重新引入旧 Spark/APM/ACE 安装链。

### 3.4 Windows/Wine

稳定 Wine 应用每个使用独立前缀：

~~~text
~/.local/share/ming-wine/apps/<app-id>/prefix
~/.local/share/ming-wine/apps/<app-id>/metadata.json
~/.local/state/ming-os/wine/<app-id>/install.jsonl
~/.local/state/ming-os/wine/<app-id>/launch.jsonl
~~~

.exe 和 .msi 必须从本地文件选择器或工具箱进入受控安装流程。.msi 使用结构化的 wine msiexec /i 参数。启动成功必须满足窗口出现或进程在稳定窗口内存活，不能只返回 launch_requested。卸载只删除当前应用的前缀、入口和日志。

### 3.5 Android/Waydroid/Cage

Android 运行时按需启动，不开机常驻、不预装 GApps、不开放网络 ADB：

~~~text
ming-toolbox
  -> ming-android-runtime
  -> Waydroid 容器
  -> Cage 单应用窗口
~~~

首版稳定支持 x86_64/universal APK。APK 必须是本地单文件，限制大小、拒绝 symlink、路径穿越、URL、APKS/XAPK 和 ARM-only 包。应用目录和日志按包名隔离：

~~~text
~/.local/share/ming-android/apps/<package-id>/
~/.local/state/ming-os/android/<package-id>/install.jsonl
~/.local/state/ming-os/android/<package-id>/launch.jsonl
~~~

启动成功必须同时读回 Waydroid session 和稳定进程或窗口；容器缺失、无 binderfs/LXC/DBus/Wayland、内存低于 4GB 或无 GPU render node 时要返回明确状态。ARM 转译、软件渲染、共享目录和调试参数只在实验室按应用启用。

### 3.6 桌面、组件和 Dock

小组件采用带版本的单一状态文件。折叠态只显示时间、日期、Wi-Fi、电池和 Ming 按钮；展开态才采样 CPU、内存和网络。Win 键与胶囊按钮必须调用同一个状态机，不能广播多个信号。

Dock 的稳定约束：

- 短边不超过 720px：32px；721-900px：36px；更大：40px；
- ZoomPercent=148；
- 居中，底部留白约 12px；
- 真全屏和应用抽屉打开时移除工作区保留区并隐藏到最底层；
- 普通窗口恢复时重新读回映射、层级和 _NET_WM_STRUT(_PARTIAL)；
- 只保留一个 Plank 实例和一个合成路径。

## 4. 安全边界

### 4.1 权限

系统级操作统一经过 ming-authorized-action 和正常 Polkit 图形授权。策略必须使用 allow_any=no，不恢复全局免密 sudo。root helper 只接受明确的动作和结构化参数。

### 4.2 命令和路径

禁止：

- eval、sh -c、用户拼接命令；
- UI 直接传入 URL、包路径、版本或任意参数；
- 未校验的远程脚本；
- 通过下载后路径替换绕过 SHA256 校验；
- 把用户可写目录当作 root helper 的信任目录。

Python 子进程使用参数数组、shell=False、超时和返回码检查。文件写入使用私有目录、临时文件和原子替换；需要防劫持的文件先检查 symlink、类型、权限和 owner。

### 4.3 密钥和日志

OTA 使用受信 Minisign 公钥；Spark 公开目录使用单独的公开 keyring，不能把 GPG keyring 当成 OTA Minisign 公钥。私钥、密码、Token、Cookie 和完整环境文件不得进入仓库、日志、测试输出或 ISO。

日志写入用户状态目录并脱敏。诊断上传必须先得到用户确认，服务端还要再次限制附件类型、大小、路径和敏感字段。

## 5. 开发工作流

### 5.1 建立隔离工作树

~~~bash
git fetch --prune origin
git worktree add ../ming-os-my-feature -b feature/my-feature origin/master
cd ../ming-os-my-feature
~~~

如果仓库有未提交修改，先记录 git status 和 git diff --stat。不要使用 git reset --hard 或 git checkout -- 覆盖他人修改。

### 5.2 先写红测

每个修复先在 tests/ 增加一个能准确失败的契约，然后只做最小实现：

~~~text
红测失败 -> 最小修复 -> 定向测试 -> 全量测试
-> Python 编译 -> Shell 语法 -> diff 检查 -> 审查 Git 差异
~~~

测试应验证真实状态读回，而不是只检查“函数被调用”或“进度条结束”。涉及 GTK、X11、Dock、窗口、安装器和硬件的功能，还必须安排 VM 或真实设备验证，并把未验证项明确标出。

### 5.3 提交格式

建议使用短、可搜索的提交标题：

~~~text
feat: add Ming Store provider contract
fix: prevent duplicate desktop entries
test: cover OTA rollback failure
docs: update developer handbook
~~~

一个提交尽量只解决一个边界。提交前检查：

~~~bash
git diff --check
git status --short
git diff --stat
~~~

## 6. 测试门禁

在仓库根目录执行：

~~~bash
python -m unittest discover -s tests
python -m py_compile assets/*.py tests/*.py
find . -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
git diff --check
~~~

如果某个测试依赖 GTK、X11、VirtualBox、硬件或网络，测试可以按约定跳过，但报告必须注明“未验证原因”。HTTP 200、文件存在、进度条走完、VM 已启动或 PM2 在线都不能单独证明功能闭环成功。

常见测试范围：

- test_build_script_contracts.py、test_build_resume.py：构建参数、输入哈希和续跑；
- test_grub_contracts.py、test_dual_boot_grub.py：BIOS/UEFI、ESP、A/B 和 UUID；
- test_ming_store_*、test_spark_store_provider.py：目录、签名、SHA、缓存和交易状态；
- test_wine_installer.py、test_android_runtime.py：独立前缀、APK 安全、启动和卸载；
- test_dock_lifecycle.py、test_status_widget_controls.py：Dock、抽屉和小组件状态机；
- test_settings_*、test_radio_contracts.py、test_audio_session.py：设置、无线、音频和输入法；
- test_release_gate.py、test_runtime_dependencies.py：rootfs 和发布残留门禁。

## 7. 构建与断点续跑

### 7.1 RC4 构建参数

RC4 分支的 build_onion_os.sh 支持：

~~~bash
sudo ./build_onion_os.sh --fresh --profile fast-test
sudo ./build_onion_os.sh --resume --profile fast-test
sudo ./build_onion_os.sh --resume --from modules --profile release
~~~

可用 profile：

| profile | 用途 | 压缩 |
| --- | --- | --- |
| release | 发布候选，要求 OTA 公钥和完整受信资源 | xz |
| fast-test | 本地快速回归 | zstd |
| legacy-lowram | 低内存兼容验证 | zstd |
| compat-hwe | 较新的硬件兼容验证 | xz |

常用环境变量：

~~~bash
MING_BUILD_PROFILE=fast-test
MING_BUILD_STATE_ROOT=/var/tmp/ming-os-build/build-state
MING_APT_ARCHIVES_CACHE=/var/tmp/ming-os-build/apt-archives
MING_CHROOT_CACHE_DIR=/var/tmp/ming-os-build/chroot-cache
MING_OUTPUT_ROOT=/var/tmp/ming-os-build/output
MING_DEBIAN_MIRROR=https://deb.debian.org/debian/
MING_CLEAN_ISO_WORKDIR=1
~~~

release 构建额外要求 MING_OTA_RELEASE_PUBLIC_KEY_SOURCE 指向真实、非空、非 symlink 的 Minisign 公钥。公钥来源、SHA256 和指纹必须独立核验；测试夹具、Wine key 或 GPG keyring 都不能替代 OTA 公钥。

### 7.2 续跑原则

构建状态通常位于 /var/tmp/ming-os-build/build-state/<profile>，缓存位于同一工作目录下。中断后先检查：

~~~bash
ps aux | grep '[b]uild_onion_os'
find /var/tmp/ming-os-build/build-state -maxdepth 3 -type f -print
cat /var/tmp/ming-os-build/build-state/<profile>/last-failure.json
~~~

只有 source commit、profile、脚本输入哈希和环境契约都匹配时才使用 --resume。若输入已改变，使用对应阶段的 --from；不要把旧 ISO 重命名成新构建结果。构建锁文件和 checkpoint 由脚本管理，不要手工伪造成功标记。

### 7.3 产物检查

构建完成后至少检查：

~~~bash
sha256sum /var/tmp/ming-os-build/output/release/*.iso
ISO=/var/tmp/ming-os-build/output/release/ming-os-26.4.1-home-amd64-rc4.iso
xorriso -indev "$ISO" -report_el_torito plain
xorriso -osirrox on -indev "$ISO" -extract /live/filesystem.squashfs /tmp/ming-squashfs
unsquashfs -ll /tmp/ming-squashfs
~~~

实际环境中可先把 ISO 挂载或解出，再核对：

- /etc/ming-os-build.json 中 source commit、版本和 build ID；
- BIOS/UEFI 引导文件和 /live/vmlinuz、initramfs；
- Ming-Mint 图标、主题、商店、工具箱和启动器；
- OTA 公钥路径和 SHA256；
- 没有私钥、测试密钥、旧 Spark/APM 客户端、旧安装 GUI 或错误 desktop entry。

## 8. VM 与真实桌面验收

源码测试通过后，才进入 VM。BIOS 和 UEFI 必须分开记录，Live 和硬盘安装系统也必须分开记录。建议先保存：

~~~bash
VBoxManage showvminfo <vm-name> --machinereadable
VBoxManage guestproperty enumerate <vm-name>
~~~

验收矩阵至少包括：

1. Live 启动、关闭安装器后桌面会话仍存活；
2. BIOS/UEFI 安装、重启、硬盘冷启动；
3. 桌面、抽屉、Dock、小组件和 Win 键；
4. 终端关闭按钮、最大化和真全屏；
5. Ming Store 搜索、目录、安装、失败、卸载和刷新警告；
6. Wine/Android 工具箱的安装、启动、停止和卸载；
7. Xiahai、设置、壁纸、无线密码框、音频和 Fcitx5；
8. OTA 检查、签名拒绝、版本前进和 A/B 回滚。

静态测试不能替代窗口证据。黑屏、Dock 覆盖、抽屉自动关闭、右键无响应和“启动失败但实际启动”等问题，都要保留截图、日志、窗口类名和退出码。

## 9. 常见问题排查

| 现象 | 首先检查 | 不要做 |
| --- | --- | --- |
| 构建中断 | checkpoint、source commit、last-failure.json、磁盘空间 | 不要盲目 --fresh，不要删除 chroot |
| 商店一直读取 | Provider 超时、缓存、签名索引、APT 状态 | 不要把网络失败显示成“已安装” |
| 星火来源不可用 | InRelease 签名、Packages 哈希、缓存年龄和证书 | 不要恢复 Spark 客户端或私有 API |
| Wine 启动失败 | receipt、sandbox 错误、窗口/进程读回 | 不要全局添加 --no-sandbox |
| APK 不兼容 | 包名、ABI、Waydroid session、GPU/内存门槛 | 不要解压 APK 到宿主机或开网络 ADB |
| Dock 覆盖全屏 | _NET_WM_WINDOW_TYPE_DOCK、层级、strut、Plank 实例数 | 不要反复启动多个 Dock 或合成器 |
| 组件重复/漂移 | 单一状态文件、Win 键去重、窗口数量 | 不要广播 SIGUSR1 给所有桌面进程 |
| 右键无操作 | Ming Files model、权限、文件管理器进程日志 | 不要让 UI 静默吞掉异常 |
| OTA 不升级 | schema、签名、版本前进、A/B 槽位和 UUID | 不要用 HTTP 200 或文件存在代替签名证据 |

## 10. GitHub 同步与发布边界

同步代码前先确认远端和工作树：

~~~bash
git remote -v
git status --short --branch
git log -1 --oneline --decorate
~~~

推送当前分支时优先使用显式 refspec：

~~~bash
git push origin HEAD:<当前分支名>
~~~

在确认所有本地分支都经过审查后，才可以同步全部分支和标签：

~~~bash
git push origin --all
git push origin --tags
~~~

这些命令只同步 Git 引用，不代表 ISO 已发布、OTA 已部署或 VM 已验收。禁止强制推送覆盖远端历史；遇到 non-fast-forward 应先比较提交图，再决定是否合并。推送前必须排除私钥、密码、Token、ISO、VM 磁盘、构建缓存和用户验收附件。

## 11. 发布前清单

~~~text
[ ] 分支、source commit 和工作树边界已记录
[ ] 定向测试和全量 unittest 通过
[ ] Python 编译、Shell bash -n、git diff --check 通过
[ ] 构建输入哈希和 OTA 公钥独立核验
[ ] ISO SHA256、BIOS/UEFI 引导和 SquashFS 内容已核对
[ ] 无私钥、测试密钥、旧 Spark/APM 和错误 desktop entry
[ ] Live、硬盘安装、BIOS、UEFI 的证据分开保存
[ ] 商店、Wine、Android、Dock、组件、设置和 OTA 的未验证项已列出
[ ] Git 提交信息清楚，远端同步使用非强制 push
[ ] 没有把旧 ISO 当新构建结果，也没有未经授权部署生产服务
~~~

## 12. 贡献原则

- 优先修复安装、启动、网络、输入和数据安全，再处理视觉细节；
- 让按钮和图形界面有真实的成功、失败、重试和日志入口；
- 保留用户文件、壁纸、应用数据和非 Ming 配置；
- 不以“看起来完成”替代状态读回；
- 把硬件、VM、网络和生产服务的未验证项说清楚；
- 任何跨模块改动都要同时更新契约测试和本手册相关章节。
