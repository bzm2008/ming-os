# Ming OS 26.4.1 RC4：Mac 接手与远程构建文档

更新日期：2026-09-18

## 1. 当前基线

- 工作树：E:\llinux os\codex-worktrees\onion-os\daily-function-chain-2641
- 分支：fix/daily-function-chain-2641
- HEAD：19e3a2c5f70cef5eda8e7dc7a66c0e388f242d3d
- 最近提交：fix: repair store provider refresh and local package picker
- GitHub：https://github.com/bzm2008/ming-os.git
- 当前状态：相对 origin ahead 14，迁移前后都要重新核对

本文只描述源码迁移和远程构建准备，不代表已经构建、安装或发布。旧 ISO、旧 VM、静态测试不能替代当前源码的真实硬件验收。

## 2. SSH 连接

优先使用局域网 IP，主机名不可用时使用 Bonjour：

    ssh mac@192.168.100.4
    ssh mac@MacBook-Air.local

首次连接由操作者完成主机指纹确认和密码输入。密码、私钥、Token、Cookie、.env 不得写入仓库或本文档。

Mac 端准备检查：

    sw_vers
    uname -m
    git --version
    python3 --version
    command -v rsync
    df -h ~

建议 Mac Codex 工作目录为 /Users/mac/ming-os。若目录已有内容，改用新目录或先人工备份，不覆盖未知文件。

## 3. 同步源码和 Git

Windows PowerShell 中进入本工作树后，推荐 rsync。它保留 .git、权限和符号链接，并排除本机临时产物：

    cd 'E:\llinux os\codex-worktrees\onion-os\daily-function-chain-2641'
    rsync -a --delete-delay --exclude='output/' --exclude='tmp/' --exclude='scratch/' --exclude='.tmp*' --exclude='.codex*' --exclude='__pycache__/' ./ mac@192.168.100.4:/Users/mac/ming-os/

如果 Windows 没有 rsync，可使用 Git bundle：

    git bundle create .\ming-os-2641.bundle --all
    scp .\ming-os-2641.bundle mac@192.168.100.4:/Users/mac/
    ssh mac@192.168.100.4 'mkdir -p /Users/mac/ming-os && cd /Users/mac/ming-os && git clone /Users/mac/ming-os-2641.bundle .'

目标目录必须是空目录或新目录。不要把 output、tmp、scratch 和缓存当作源码依据；需要保留旧产物时单独复制并记录 SHA256。

Mac 端核对：

    cd /Users/mac/ming-os
    git status --short --branch
    git branch --show-current
    git rev-parse HEAD
    git log -1 --oneline
    git remote -v
    shasum -a 256 build_onion_os.sh assets/ming-device-control.py assets/ming-phone-desktop.py

Windows 与 Mac 的 HEAD 必须一致且工作树干净。推送前先 fetch 和检查差异：

    git fetch origin
    git log --oneline --decorate origin/fix/daily-function-chain-2641..HEAD
    git push origin HEAD:fix/daily-function-chain-2641

## 4. Mac 开发与 Windows 构建分工

- Mac：源码编辑、Git、测试、代码审查和 Codex 继续开发。
- Windows：仅提供 SSH 进入 WSL/Debian 或既有 Linux 构建环境。
- 不在 macOS 原生 shell 直接运行 Debian 构建脚本。
- 本次迁移不构建 ISO、不部署、不操作 OTA。

示例命令，实际 Windows 用户、端口、WSL 发行版和挂载路径以现场配置为准：

    ssh windows-builder 'wsl.exe -d Debian -- bash -lc "cd /mnt/e/llinux\ os/codex-worktrees/onion-os/daily-function-chain-2641 && git status --short --branch"'

## 5. 功能状态与未验证项

已有代码级证据：
- Store、设备、设置定向测试最近共 318 项通过。
- 19e3a2c 修复商店栏目 ID/来源 ID 混用，以及本地 DEB/Wine 文件选择器 GTK 回调参数。
- Wi-Fi 链路为：扫描记录 -> 连接 -> network_id -> 中文密码对话框 -> Gtk.PasswordEntry -> nmcli --ask。

仍需修复或真实验证：
- 音量：amixer 回退时，桌面点击先调用音频修复；现有修复路径只处理 wpctl/pactl，可能阻断有效 amixer 设置。
- 亮度：Debian 13 brightnessctl -m 格式是“设备,类别,当前值,最大值,百分比”；现有解析和部分测试顺序错误。
- Wi-Fi：真实 GTK 密码框、NetworkManager、中文 SSID、认证失败和成功连接尚未真机验证。
- 商店：Ming 官方目录没有公开 RC4 Release 材料，不能伪造可安装条目；星火还需要真实签名索引、公钥和网络证据。材料不完整必须显示阻塞，不能降低校验强度。
- 真实音频、背光、无线网卡、Surface、AMD、Mac EFI、安装后重启均未验证。

## 6. 后续开发顺序

1. Mac 核对工作树和提交。
2. 为 amixer 回退和真实 brightnessctl 输出各补失败测试。
3. 做最小修复并运行设备、桌面、商店、设置定向测试。
4. 运行全量 unittest、Python 编译、修改过的 Shell 的 bash -n 和 git diff --check。
5. 只有官方信任材料、安装后状态回读和硬件/VM 验证条件齐备，才进入构建验收。

## 7. 安全规则

- 不复制 SSH 私钥、密码、Token、Cookie、.env 或签名私钥。
- 不使用 git reset --hard、强制推送、全盘删除或覆盖已有 Mac 目录。
- 不通过放宽 HTTPS、签名、SHA256、架构或包状态校验来修复商店。
- 不引入全局免密 sudo、eval、sh -c 或旧 Polkit 特权路径。
- 每次同步前记录 git rev-parse HEAD；不以旧 ISO 推断源码状态。

## 8. 本次迁移记录

- Windows 工作树已确认干净，分支 fix/daily-function-chain-2641，HEAD 为 19e3a2c。
- 192.168.100.4:22 可达，首次主机指纹已加入本机 known_hosts。
- SSH 公钥认证未通过，实际传输尚未完成；需要 Mac 用户授权 Windows 客户端公钥，或由用户在可见终端完成一次密码登录后运行同步命令。
- 未构建 ISO，未部署，未操作 OTA。

