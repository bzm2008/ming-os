# 铭荼项目说明

## 项目定位

铭荼是一个独立桌面 Agent Harness 项目，目标平台为 Windows、macOS 和 Ming OS。它把 DeepSeek Harness（DSH）作为可替换的工具与 Agent 运行时，通过铭荼自己的 Agent service、权限策略和本地 IPC 接入桌面应用；用户不需要把浏览器网页当作主应用入口。

铭荼与 Ming OS 的关系是“跨平台应用 + Ming OS 官方桌面集成”：Windows 和 macOS 是先行开发、验证的平台；Ming OS 继续使用 GTK4/libadwaita 桌面入口，并逐步接入共享协议和运行时。铭荼不等同于 Ming OS，也不应让 Ming OS 的构建依赖未审计的社区包。

## 当前阶段

当前仓库包含两条实现路径：Ming OS 原有的 GTK/Python 应用集成，以及 `platform/ming-tea` 下的跨平台 TypeScript Agent 与 Tauri 桌面壳。跨平台部分仍是开发版，不应描述为已经完成 Windows/macOS 正式发行或完整系统级控制。

- macOS：Tauri `.app` 曾在本地成功构建；当前开发包启动 Agent bundle 时仍依赖系统 Node。签名、公证和独立分发 sidecar 尚未完成。
- Windows：纳入目标平台；Named Pipe 协议有实现，完整打包与桌面端实机验收尚未完成。
- Ming OS：已有 GTK 桌面入口与 Python IPC/构建集成；跨平台 Agent 与现有 Ming OS 入口的发行集成仍需逐步验证。
- DSH：运行时和工具包锁定为 `0.1.7-rc.1`；安装流程存在，但需要通过实际安装、profile 内容和启动检查才能称为安装完成。

## 产品场景

三个场景共享同一个 Agent 和插件集合，只改变任务提示、工具优先级和权限边界：

| 场景 | 用途 | 权限倾向 |
| --- | --- | --- |
| 办公 | 浏览器协作、文档/表格/演示文稿、文件整理 | 读取可自动；写入、上传、提交需审批 |
| 开发 | 阅读代码和日志、运行测试与构建、项目诊断 | 项目范围内操作优先；提权和系统变更需审批 |
| 辅助学习 | 解释资料、命令和错误，分步引导并整理笔记 | 以讲解和建议为主，高风险动作需单独确认 |

会话开始后场景锁定；思考强度 `fast`、`balanced`、`deep` 可在会话期间改变，下一轮请求生效。

## 架构与代码入口

```text
Tauri 桌面壳（Windows/macOS） ─┐
GTK4/libadwaita 入口（Ming OS） ├─ JSON-lines IPC ─ Agent service ─ DSH adapter/profile
                               ┘                     ├─ 场景与会话
                                                     ├─ 权限、审批、审计脱敏
                                                     ├─ 本地记忆与会话库
                                                     ├─ 模型提供方注册表
                                                     └─ 平台状态与 Ming OS OTA bridge
```

- `platform/ming-tea/packages/protocol`：场景、请求/响应、事件和 Unix socket/Windows Named Pipe 传输契约。
- `platform/ming-tea/packages/agent`：会话、权限策略、模型注册、DSH adapter、记忆、会话检索、OTA 与平台状态。
- `platform/ming-tea/apps/desktop`：Tauri 2 桌面壳和 WebView 资源。它是桌面 UI，不是 DSH 插件源码本身。
- `assets/ming-tea-dsh-lock.json`：固定 DSH runtime 与插件的版本、许可证及包完整性。
- `assets/ming-tea-community-candidates.json`、`docs/ming-tea-plugin-audit.md`：社区来源和适配审计。
- `scripts/install_ming_tea_plugins.sh`：在开发/构建环境安装锁定的 DSH runtime 与 web profile 客户端。
- `assets/ming-tea-core.py`、`assets/ming-tea-runtime.py`、`assets/ming-tea.py`：Ming OS 现有 Python/GTK 集成路径。

## 社区组件与来源

新增能力先审计 DSH 官方接口和社区项目，再决定适配；第三方代码不因出现在锁文件中就自动视为已安装或可进入 ISO。

### 桌面工作区基线

- 项目：[`MichengAI/dsh-codex-ui`](https://github.com/MichengAI/dsh-codex-ui)
- 包：`@michengai/dsh-codex-ui@1.1.18`
- 许可证：Apache-2.0
- revision：`84946e2abec910b60e1f5f8aa69e45ab34df2a04`
- npm integrity：`sha512-mYmEU7tf7p8k3CFeiftaOpkuPh6s62DE+1anRCmifsIVHJG0uUXb44RZCkDZmR+vhLeKptMKTf501TpCkKdQPA==`
- 用途：参考其会话侧栏、工作区会话树、搜索、导航和 composer，并计划通过 DSH `web` profile 挂载。
- 状态：已审计并锁定；实际 profile 安装流程/peer 依赖兼容性仍待验证。不能把它称为已经成功安装，也不能声称 Tauri 界面直接复用了其 DOM 或源码。

### DSH runtime 与工具包

`assets/ming-tea-dsh-lock.json` 固定 `@deepseek-ai/dsh@0.1.7-rc.1` 以及对应版本的 Bash、FS、Web、Browser Use、Computer Use、Playwright MCP 实验适配器和 Office skill。锁文件提供审计依据；安装是否成功以本地 runtime 的包清单、profile 配置和实际启动结果为证。

### 其他社区适配

身份记忆、会话工作台、EverOS Memory 和 Cleverer DSH 的审计状态以 `assets/ming-tea-community-candidates.json` 为准。铭荼当前存在本地适配器实现，但这不表示对应第三方服务或上游插件运行时已一并安装。Honcho 作为外部连接器候选；尚未定位的插件源和受版本/安全条件阻塞的能力不得加入默认发行包。

## 安全、权限与数据

- 工具必须在铭荼策略中显式声明；未识别工具默认拒绝。
- 文件/网页/系统状态读取和部分低风险操作可以自动执行。
- `sudo`、包管理、系统配置修改、删除、上传、表单提交及办公文档写入必须进入审批路径。
- 审批令牌单次使用；会话取消后不得继续执行。
- API key 通过 `credentialRef` 引用安全配置，不得放进对话、普通 IPC 事件或审计日志。
- 本地会话和记忆位于用户数据目录；捕获记忆应脱敏并可由用户控制。
- macOS Accessibility/Automation 等平台授权只能由系统设置授予。缺少权限时报告 `permissionRequired`，不得绕过 TCC/UAC 等系统保护。
- 社区包在默认发行前必须核验固定来源/版本、许可证、完整性、依赖、权限、平台兼容性和维护状态。

## 模型与 OTA

模型注册表当前声明 Ming 主站、OpenAI-compatible 和 Kim 提供方；凭据使用引用，不在 UI 或日志回显。铭荼没有 OpenCode Zen/Big Pickle 免费端点：该服务的模型列表可以访问，但 chat completion 实测返回 403，提示仅允许在 OpenCode 内使用，因此该端点已从产品中移除。

铭荼图像素材使用项目外的 `ming-tea-imagegen` 技能。用户提供的图像端点当前实际列出 `gpt-image-2.5-flare` 和 `gpt-image-2.5-sunburst`，不提供字面上的 `image-2.5`；技能默认使用 `gpt-image-2.5-flare`。密钥只从 `MING_TEA_IMAGE_API_KEY` 环境变量读取。2026-09-27 探测中，批量路径返回 `404 RESOURCE_UNAVAILABLE`，参考图编辑路径返回 `502 UPSTREAM_ERROR`，因此目前没有把任何生成结果登记为项目素材。

两个模型的配置记录在 `platform/ming-tea/assets/ming-tea-image-models.json`。配置只包含端点、模型 ID 和密钥环境变量名，不包含明文密钥；运行时由本机环境提供密钥。

Ming OS 的 `os.update.*` IPC 通过 `ming-update` 调用现有系统 OTA。铭荼不另造 A/B 更新引擎。Windows/macOS 应用更新属于独立发行能力，当前尚未完成签名更新清单和端到端发布验证。

## 开发与验证

前置条件：Node.js `>=22.19.0`、pnpm `11.19.0`；构建 Tauri 桌面应用还需要 Rust/Cargo 和相应平台工具链。

```bash
cd platform/ming-tea
pnpm install --no-frozen-lockfile
pnpm test
pnpm exec tsc --noEmit
pnpm build:agent
pnpm --filter @ming-tea/desktop build
pnpm --filter @ming-tea/desktop tauri:build --bundles app
```

Ming OS Python 集成测试：

```bash
python3 -m unittest -q \
  tests.test_ming_tea_core \
  tests.test_ming_tea_integration \
  tests.test_xiahai_integration \
  tests.test_papyrus_integration
```

DSH profile 安装脚本会访问 npm registry 并安装第三方包。只有在隔离的开发/构建目录运行；不可为了绕过 peer dependency 冲突而无记录地使用 `--force`。本地安装验证命令和结果须写入实时协作文档。

本项目尚未提供可直接承诺的生产安装包。桌面开发测试结果、发行签名/公证、ISO 预装与系统级控制能力验收是不同状态，记录时必须分开。

## 协作入口

- [铭荼实时协作状态](ming-tea-live-status.md)：Codex 与 ZCode 的当前共同事实来源；继续开发前先读。
- [铭荼插件审计清单](ming-tea-plugin-audit.md)：候选组件、权限和兼容性说明。
- [跨平台架构设计](superpowers/specs/2026-09-25-ming-tea-cross-platform-design.md)：原始设计目标，不代表所有部分已实现。
