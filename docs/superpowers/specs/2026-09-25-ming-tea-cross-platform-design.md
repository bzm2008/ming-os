# 铭荼跨平台桌面助手设计

## 目标

先交付 Windows 和 macOS 上可独立运行的铭荼桌面应用，再把同一套运行时接入 Ming OS 的 GTK4 原生外壳。所有平台共享场景、权限、审批、审计和插件协议；平台差异只存在于系统控制适配器和桌面外壳。

## 约束

- 应用必须是独立桌面程序，不能依赖用户打开浏览器访问网页。
- DSH 只通过适配层运行，GTK、Windows 和 macOS 界面不能直接依赖 DSH 内部模块。
- 办公、开发、辅助学习三种场景共享一套 Agent，不复制三套 Agent 实现。
- 终端、浏览器、文件、办公和系统控制都经过统一权限策略。
- 安装、sudo、上传、提交、删除和系统配置修改必须经过审批。
- DSH 版本和插件包必须固定版本、许可证和完整性信息。
- 运行时默认支持 x86_64；首发不要求本地模型。
- 模型提供方支持 Ming 主站接口、OpenAI-compatible endpoint 和 Kim 等第三方 endpoint；API key 进入系统安全存储，不写入会话日志。
- 新能力优先复用 DSH 官方插件和社区插件，铭荼只做权限、兼容性、低资源和 UI 适配。
- DSH 官方 computer-use 作为基础能力，铭荼通过目标窗口白名单、动作预算、截图节流和审批包装进行优化，不复制另一套 computer-use 引擎。

## 架构

```text
Windows WinUI/Tauri shell  ┐
macOS SwiftUI/Tauri shell   ├─ local IPC ── 铭荼 Agent service ── DSH adapter
Ming GTK4 shell             ┘                    │
                                      platform capability adapters
```

共享 Agent service 使用 TypeScript/Node 运行，原因是 DSH 官方运行时和插件生态已经以 Node 包发布；服务负责会话、场景路由、工具审批、审计脱敏、插件清单和模型适配。Windows 使用 Named Pipe，macOS/Ming OS 使用 Unix socket；协议消息保持 JSON-lines，方便诊断和回归测试。

Windows 和 macOS 首期使用 Tauri 2 外壳，把 UI 作为桌面应用资源随程序发布，不暴露本地 Web 服务端口。Tauri 的 Rust host 只负责窗口、IPC 转发和平台权限桥接；核心工具编排仍在 Agent service。Ming OS 继续使用现有 GTK4/libadwaita 界面并连接相同 IPC。

## 平台适配器

### Windows

- `terminal`: PowerShell/Windows Terminal，危险命令交给权限策略审批。
- `computer`: Windows UI Automation，只开放声明过的窗口和控件操作。
- `browser`: Playwright/官方 DSH 浏览器插件。
- `office`: LibreOffice UNO；Microsoft Office 适配器作为可选插件。
- 系统级修改通过 UAC 触发并回传审批事件。
- 官方 computer-use 适配器负责窗口、鼠标、键盘和截图能力；铭荼包装层限制目标应用、动作类型、动作频率和审批级别。

### macOS

- `terminal`: shell 子进程和现有终端应用。
- `computer`: Accessibility/Automation API，首次使用时提示用户授予系统权限。
- `browser`: Playwright/官方 DSH 浏览器插件。
- `office`: LibreOffice UNO；其他办公软件适配器保持可选。
- Full Disk Access、Automation 和 Accessibility 权限缺失时，服务返回可读错误，不绕过系统授权。
- 官方 computer-use 适配器负责 macOS 桌面动作；铭荼包装层把 Accessibility/Automation 授权状态映射为可读能力状态。

### Ming OS

- 继续使用现有诊断工具、终端、Xfce 应用入口和 LibreOffice。
- 通过 Unix socket 接入共享协议。
- ISO 构建阶段安装 DSH 固定运行时和审核清单。

## IPC 协议

请求至少包括：

- `ping`
- `session.create`
- `session.cancel`
- `tool.request`
- `approval.consume`
- `status`

事件至少包括：

- `session.created`
- `plan.updated`
- `tool.requested`
- `approval.requested`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `session.completed`

所有事件进入本地审计日志。API key、Bearer token、密码和会话凭据在事件进入 UI 或日志前脱敏。

## 模型提供方

模型提供方实现统一的 `ModelProvider` 接口，至少包含 `id`、`label`、`endpoint`、`model`、`credential_ref` 和 `capabilities`。铭荼首期内置：

- `ming-main`: Ming 主站的 AI 服务接口。
- `openai-compatible`: 用户自定义的 OpenAI-compatible endpoint。
- `kim`: Kim API 的兼容适配器，API key 只保存为安全存储引用。

提供方配置与会话内容分离保存。UI 只显示提供方名称和连接状态，不回显 key。

## 插件策略

插件注册表分为官方、社区和铭荼本地适配三类。加载前检查固定版本、许可证、完整性、运行时、平台、权限和维护状态。社区插件优先二次适配；没有可审计来源时才新增铭荼本地实现。

铭荼 UI 使用自己的品牌、布局和主题令牌，不复制 DSH Web 或 dsh-desktop 的页面布局。官方 DSH 插件只提供能力，界面事件统一转换为铭荼内部协议。

## 首期交付顺序

1. 固定跨平台协议和 Node Agent service，提供 fake DSH adapter 与 fake platform adapter。
2. 加入 Windows/macOS 的启动器、配置目录和权限状态 API。
3. 加入 Tauri 2 桌面 shell，展示场景、对话、计划、工具调用和审批。
4. 接入官方 DSH 运行时、终端、文件、网页和 Office 插件。
5. 用同一协议连接现有 Ming GTK4 壳，最后再做 Ming OS ISO 集成。

## 失败与安全行为

- DSH 不可用时桌面仍能打开并显示离线状态、本地会话和设置。
- IPC 服务启动失败时外壳显示可读错误，不自动回退到网页入口。
- 未声明的插件、权限、许可证、版本或完整性字段拒绝加载。
- 取消会话后拒绝后续工具执行。
- 所有临时授权在会话结束时失效。
- 平台权限缺失只阻止对应能力，不阻止应用启动。

## 验收

- Windows/macOS 可启动独立窗口，不需要用户打开浏览器。
- 三个场景生成稳定路由结果。
- fake adapter 能覆盖浏览器、终端、文件和 Office smoke test。
- 高风险请求只生成 `approval.requested`，不会在自动化测试中执行破坏性操作。
- API key 不出现在 IPC 返回、UI 事件和审计日志。
- DSH runtime 不可用、离线和平台权限缺失都有可读状态。
- Ming OS 现有 29 项铭荼/桌面回归保持通过。
