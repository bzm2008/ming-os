# 铭荼插件审计清单

铭荼首期遵循“先找社区项目，再做适配”的规则。插件清单的机器可读版本位于 `assets/ming-tea-plugins.json`，构建阶段会把它安装到 `/usr/share/ming-os/ming-tea/plugins.json` 并做内容校验。

社区候选的机器可读审计表位于 `assets/ming-tea-community-candidates.json`。候选插件只有 `adapted-local`、`adaptation-review`、`optional-adapter` 或 `optional-adapter-ready` 状态时才允许进入开发环境；`blocked-version`、`external-connector-only` 和 `needs-source` 不得进入默认 ISO。

## 社区来源

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：运行时和官方插件接口，MIT，当前处于 developer preview。
- [DSH 插件主题](https://github.com/topics/dsh-plugin)：发现可复用的浏览器、记忆、桌面和工作流插件。
- [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)：社区插件精选索引。
- [dsh-desktop](https://github.com/anywhere-labs/dsh-desktop)：桌面交互和插件化 UI 的参考实现；铭荼不直接复制其网页壳。
- [dsh-web](https://github.com/zhu1090093659/dsh-web)：插件聚合和分发的参考实现；铭荼仅借鉴清单/发现思路。
- [LibreOffice core](https://github.com/LibreOffice/core)：办公能力使用系统已有 LibreOffice/UNO 运行时，不把第三方二进制复制进 ISO。

## 已审计社区候选

- `dsh-agent-identity`：MIT，固定提交 `cbab483bbaa7c8ee44c1bdc958b491afaee6abdd`；已由 `IdentityMemoryAdapter` 做 SOUL/USER/MEMORY 本地适配，按 profile/session/task 分级并在写入时脱敏。
- `dsh-session-workbench`：MIT，固定提交 `9312b28922e65499eed54df86d9d26eda3000e4f`；已由 `SessionWorkbenchAdapter` 提供本地只读检索和会话引用，原生界面不复用其 Web UI。
- `everos-memory`：Apache-2.0，固定审计提交 `f76f4d06135a0b5d784d15eed133ecdcedc12d47`；已提供默认关闭的 `EverosMemoryAdapter`，只允许显式本机 loopback 连接器。
- `cleverer-dsh`：MIT，固定提交 `40bd216ea9c7a95da887aa97fb661a0e8c7b1dd2`；已由 `ClevererDshAdapter` 接入尝试次数上限、失败记录和成功重置，不执行第三方脚本。
- `honcho-memory`：AGPL-3.0，只允许外部连接器，不直接随 Ming OS 发行。
- `dsh-data-agent`：MIT，但 v0.2.0 要求 DSH `0.1.7-rc.1`，等待铭荼升级 DSH 适配层后再接入。

## 当前适配策略

| 能力 | 铭荼适配方式 | 默认权限 |
| --- | --- | --- |
| 浏览器 | 复用 DSH 工具接口，经本地审批层接入 | 读取/打开自动；提交/上传确认 |
| 终端与诊断 | 优先调用 Ming OS 现有诊断工具 | 低风险命令自动；sudo/包管理确认 |
| 办公 | 通过系统 LibreOffice/UNO 适配 | 读取自动；写入/上传确认 |
| 学习笔记 | 本地 Markdown 扩展，避免常驻向量库 | 读取自动；写入确认 |

## 已接入的本地适配器

插件适配契约集中在 `platform/ming-tea/packages/agent/src/community-adapters.ts`，持久化和运行状态分别委托给 `memory.ts`、`session-library.ts` 和 `discipline.ts`。Agent service 调用这些本地模块，第三方仓库不会被直接复制进 ISO：

- `IdentityMemoryAdapter`：通过 `LocalMemoryStore` 保存经过脱敏的身份事实，按 scope 生成提示上下文。
- `SessionWorkbenchAdapter`：通过 `SessionLibrary` 提供本地只读检索和会话引用。
- `ClevererDshAdapter`：把尝试次数上限和失败摘要交给 `ExecutionDiscipline` 协调，防止重复执行卡死。
- `EverosMemoryAdapter`：显式启用后才接收本地 loopback 连接器，默认不可用。

## 进入正式 ISO 前的门槛

每个插件必须记录固定版本、许可证、运行时、权限、Debian 13 兼容性、低配置可用性和适配说明。未满足任一项时，插件只能留在开发目录，不能进入正式构建。

构建门会检查：

- 插件清单存在且包含核心适配器。
- 铭荼桌面入口、运行时和清单同时存在。
- 桌面应用没有回退到 `127.0.0.1:3080` 网页入口。
- 审批事件和本地审计日志仍由铭荼核心策略统一处理。
