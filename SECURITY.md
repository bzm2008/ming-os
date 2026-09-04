# Ming OS 安全策略

## 报告漏洞

请不要在公开 Issue、Pull Request、截图或聊天中发布可利用细节、密码、私钥、Token、Cookie、用户数据或完整诊断包。

优先使用 GitHub 私密漏洞报告：

- [提交私密漏洞报告](https://github.com/bzm2008/ming-os/security/advisories/new)

如果页面不可用，请先通过维护者的 GitHub 账号联系，并只提供最小复现信息。不要把真实密钥、生产日志或用户文件上传到仓库。

## 报告内容

请尽量包含：

- 受影响的版本、分支或提交；
- 最小复现步骤；
- 预期和实际结果；
- 影响范围；
- 是否需要本地账户、Polkit、网络或特殊硬件；
- 可安全提供的日志片段，已去除用户名、Home 路径、IP、MAC、SSID、Token 和密钥。

维护者会在确认后给出修复分支、测试计划和修复版本。未经协调，不要公开未修复漏洞。

## 高风险区域

以下区域默认按安全敏感代码审查：

- `modules/01_base.sh`、`modules/06_ota_update.sh` 和构建脚本；
- `config/security/`、Polkit、`ming-authorized-action` 和 root helper；
- Ming Store 下载、签名、SHA256、APT/DPKG 和 PackageKit 适配器；
- Wine/Waydroid 安装、路径、前缀、容器和桌面入口；
- 诊断收集、上传、服务器二次脱敏和附件处理；
- GRUB、A/B 槽位、回滚、安装器和磁盘分区。

## 开发者要求

- 使用结构化参数和 `shell=False`，禁止 `eval`、`sh -c` 和任意命令；
- root helper 必须有动作白名单、路径和 owner 检查、超时、日志和真实结果读回；
- Polkit 策略不得恢复宽权限或免密路径；
- 下载内容必须校验来源、签名、版本、架构和 SHA256；
- 测试密钥和测试保险库只能用于测试，不能进入 release；
- 日志和诊断包必须脱敏，上传必须由用户确认；
- 不把静态测试、HTTP 状态或文件存在当成安全闭环证据。

## 支持和披露

当前稳定主线是 `master`。RC4 集成线的功能必须先通过 CI、定向回归和必要的 VM/硬件验收，再进入稳定发布流程。安全修复合并后，维护者会更新受影响版本、修复提交和验证范围。
