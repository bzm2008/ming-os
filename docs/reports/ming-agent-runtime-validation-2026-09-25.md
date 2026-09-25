# Ming Agent Runtime Validation

## 已验证

- Agent runtime、JSON bridge、store facade、D-Bus metadata tests: 11 项通过。
- Security、runtime dependency、release gate 相关回归子集: 107 项通过。
- `PYTHONPYCACHEPREFIX=/tmp/ming-os-pycache python3 -m py_compile assets/*.py`: 通过。
- Bash 语法和 `git diff --check`: 通过。
- 远程 Debian WSL2 `fast-test`: 退出码 0。
- Source commit: `cca00fae20cac028cd8d2ef4a8206400dbabcf27`。
- Build ID: `2641-rc4-cca00fae20ca-20260925T130235Z`。
- ISO: `/var/tmp/ming-os-build/output/fast-test/ming-os-26.4.1-home-amd64-rc4-fast-test.iso`。
- ISO SHA256: `4e0b074d8d8d18218a7ea74f0cdb86b720b15cfab3349338a455894893682ef2`。
- ISO size: `2511667200` bytes。
- Kernel hash、Calamares、BIOS isolinux、UEFI GRUB 和老硬件门禁均通过。
- 开发 VM `MingOS-devagent-593b8ee-BIOS` 保持运行：BIOS、4GB、2 CPU；未替换其挂载 ISO。

## 已复现

- macOS 本地运行完整 `test_build_script_contracts` 时，部分旧测试依赖 Linux `/var`、`/run` 路径和临时 hash 注入环境；这些失败与 agent 改动无关。
- 本地 macOS 没有真实 X11/DBus/Xvfb 会话，因此未在本机执行实际后台图形操作。

## 未验证

- 尚未把正在开发中的 DSH 应用接入 `org.mingos.Agent1` 或 `ming-agent-bridge`。
- 尚未在真实硬件上验证 Xvfb 并发会话、AT-SPI 控件树和高负载应用。
- 应用商店 agent 变更操作尚未在已安装系统中执行真实安装；源码测试确认其复用现有授权事务链。

## 阻塞

- 无源码或构建阻塞。本次为 `fast-test`，OTA release key 未提供，`release_eligible=false`。

## 任务后审视

最没把握的是不同显卡/窗口管理器下的后台 Xvfb 应用渲染，以及 DSH 应用对 session-bus 接口的最终调用方式。

## 你可能遗漏的事

当前没有发现有证据支持的遗漏；唯一未完成项是 DSH 应用本身尚在开发，尚未进行端到端接入验收。
