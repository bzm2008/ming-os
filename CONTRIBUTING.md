# Ming OS 贡献指南

感谢参与 Ming OS。项目目标是为老电脑和中文用户提供稳定、易懂、可恢复的桌面系统。贡献时，安装、启动、网络、输入、安全和数据保留优先于视觉细节。

## 开始前

先阅读：

- [Ming OS 开发者全方位手册](docs/MING_OS_DEVELOPER_GUIDE.md)
- [安全策略](SECURITY.md)

确认自己使用的是仓库根目录，而不是工作区父目录：

~~~bash
cd /path/to/ming-os
git status --short --branch
git worktree list
git fetch --prune origin
~~~

当前分支约定：

- `master`：稳定主线，受保护，只能通过 Pull Request 合并。
- `integration/rc4-next`：RC4 集成线，功能先在这里合并和回归。
- `feature/<issue>-<name>`：普通功能或重构。
- `fix/<issue>-<name>`：缺陷修复。
- `hotfix/<issue>-<name>`：已发布版本的紧急修复。

不直接向 `master`、`integration/rc4-next` 推送，不强制推送，不重写他人的公共分支历史。

## 创建任务分支

从集成线开始工作：

~~~bash
git switch integration/rc4-next
git pull --ff-only
git switch -c feature/123-store-cache
~~~

如果任务明确属于主线维护，则从 `master` 创建。一个分支只解决一个 Issue，避免把 ISO、临时截图、无关重构混在一起。

## 修改边界

常见模块边界：

- `modules/01_base.sh`：基础系统、APT、账户、启动和 GRUB；
- `modules/02_apps.sh`：应用和运行库；
- `modules/03_desktop.sh`：Xfce、Dock、抽屉、小组件、主题和启动器；
- `modules/06_ota_update.sh`：OTA、A/B、签名和回滚；
- `assets/ming-store*`：Ming 应用商店和受控安装；
- `assets/ming-wine*`：Wine 应用和独立前缀；
- `assets/ming-android*`：Waydroid/Cage 和 APK；
- `assets/ming-settings*`、`assets/ming-device-control.py`：设置、无线、音频、亮度和输入；
- `tests/`：契约、单元、回归和构建门禁。

跨模块修改时，先说明接口和状态变化，再同步补充测试。不要把新实现接回已退役的 Spark/APM/ACE 客户端链路。

## 本地验证

提交前在仓库根目录执行：

~~~bash
python -m unittest discover -s tests
python -m py_compile assets/*.py tests/*.py
find . -type f -name '*.sh' \
  -not -path './.git/*' \
  -not -path './output/*' \
  -not -path './chroot/*' \
  -print0 | xargs -0 -r -n1 bash -n
git diff --check
~~~

测试顺序：

~~~text
先写红测 -> 确认失败原因 -> 最小实现 -> 定向测试
-> 全量测试 -> Python 编译 -> Shell 语法 -> 差异检查
~~~

涉及 GTK、X11、Plank、VirtualBox、硬件、网络、安装器或 OTA 的修改，必须在 PR 中区分：

- 已通过：有命令、日志、退出码或截图；
- 未验证：说明缺少的硬件、服务或权限；
- 阻塞：说明复现条件和下一步。

不要用“文件存在”“HTTP 200”“进度条结束”“进程启动过”替代真实功能读回。

## 代码和安全要求

- Python 子进程使用参数数组、`shell=False`、超时和返回码检查；
- Shell 使用白名单和明确的参数边界；
- 禁止 `eval`、`sh -c`、任意 URL、任意命令和用户拼接的 root 参数；
- 系统级操作必须经过 `ming-authorized-action` 和 Polkit；
- 不恢复免密 sudo，不把用户可写目录作为 root helper 的信任目录；
- 不提交私钥、密码、Token、Cookie、完整 `.env`、ISO、VM 磁盘和构建缓存；
- 日志必须脱敏，诊断上传必须经过用户确认；
- OTA 公钥、Spark 公开 keyring、Wine key 和测试夹具不能互相替代。

## 提交和 Pull Request

提交标题使用简短的英文动词开头：

~~~text
feat: add provider cache fallback
fix: prevent duplicate desktop entries
test: cover OTA rollback failure
docs: update developer guide
~~~

PR 描述至少包含：

1. 问题和用户影响；
2. 修改范围和不包含的范围；
3. 新增或更新的测试；
4. 实际测试命令和结果；
5. UI、VM 或硬件证据；
6. 未验证项目、风险和回滚方式。

提交前：

~~~bash
git status --short
git diff --stat
git diff --check
git push -u origin feature/123-store-cache
~~~

PR 默认目标为 `integration/rc4-next`。只有集成线完成回归后，维护者才会向 `master` 提交发布 PR。

## 审查和合并

- 提交者不能自己批准和合并自己的 PR；
- 受保护分支至少需要一名维护者批准；
- 修改 `modules/01_base.sh`、`modules/06_ota_update.sh`、`config/security/`、授权 helper、商店安装链或构建脚本时，必须由对应 CODEOWNERS 审查；
- 旧 Spark/APM/ACE 客户端、Polkit、私有 API 和生产 OTA 的改动不能通过普通功能 PR 绕过审查；
- CI 未通过、存在未解释的跳过测试或有合并冲突时，不合并；
- 优先使用 Squash merge，保持主线提交可读。

## UI、VM 和硬件证据

桌面行为不能只靠静态测试判断。对小组件、Dock、应用抽屉、右键菜单、终端、设置、商店、Wine、Android 或 Xiahai 的改动，应保存：

- 分辨率、BIOS/UEFI、Live/硬盘安装状态；
- 操作步骤；
- 截图或录屏；
- 相关日志、窗口类名、进程状态和退出码；
- 已验证、未验证或阻塞结论。

真实硬件无法复现时，保留失败保护和可读诊断，不伪造成功。

## 发布边界

志愿开发者默认只参与源码、测试、文档和非生产 staging。以下动作由发布维护者执行：

- 构建 release ISO；
- 使用或轮换 OTA 签名材料；
- 发布 GitHub Release；
- 修改生产 OTA、官网、镜像或诊断服务器；
- BIOS/UEFI 和硬盘安装闭环验收；
- 发布后回滚。

`master` 的合并不等于 ISO 已发布，也不等于 OTA 已部署。发布必须另有版本、source commit、ISO SHA256、引导结构、SquashFS、VM 和回滚证据。
