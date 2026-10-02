# 铭荼实时协作状态

> Codex 与 ZCode 的共享事实来源。开始开发前先读本文件与[项目说明](ming-tea-project.md)，不得把旧聊天上下文当作当前仓库状态。
>
> 每次完成一项实现、重要决策、测试失败或阻塞时更新本页。状态值只使用 `done`、`in-progress`、`blocked`、`planned`。锁文件已登记不等于插件已经安装。

## 更新时间

2026-10-01（Asia/Shanghai）。本轮把 **DSH runtime 从 `0.1.7-rc.1` 升到 `0.2.0-rc.2`**（= npm `latest`，与本机 GUI 一致），并把 13 个插件的对应版本对齐：10 个 `@michengai/*` 升到声明兼容 0.2.0-rc.2 的最新版，`plugin-effort-slider@1.2.2` 不变（无 peer 声明），`dsh-plugin-shop@0.8.3` 因上游没有支持 0.2 的版本而走**精确版本豁免**。同时修掉 4 条因官方文案改写而失效的定制规则、补上 `characterData` 监听（插值句此前换不掉），并完成干净环境下的浏览器实测。详见「变更记录」2026-10-01 各条与「版本豁免」一节。

## 工作区

- 主仓库：`/Users/mac/ming-os`
- 铭荼当前功能工作树：`/Users/mac/.codex/worktrees/ming-tea-dsh-assistant/ming-os`
- 继续修改铭荼实现时使用该工作树；不要误把主仓库当作已经包含这批跨平台改动。
- 工作树原为 detached HEAD（2026-09-26 实测 `## HEAD (no branch)`）。**2026-09-30 已提交**：新建本地分支 `feat/ming-tea-desktop-agent`
  （**未推送**），把这批跨平台改动分成 4 个提交落盘，工作树现在干净：
  `f08ad4d` 忽略本机 agent 产物（`/.playwright-mcp/`、`/platform/ming-tea/.codex/`）→
  `13e4023` 铭荼界面层插件与三场景预设（含新设置页「用量看板」）→
  `1f8a5b1` 协作文档、两份调研与门禁脚本 → `f90fedc` 跨平台 Agent、Tauri 壳、锁文件与安装脚本。
  基线仍是 `f46f634`。**要继续开发就在这个分支上做；推送需要另外确认**（分支尚未 push 到 origin/nas）。
- 本机工具链：node 位于 `/Users/mac/.local/bin/node`（v26.9.0），无全局 `pnpm`/`corepack`；运行 workspace 命令用 `export PATH="/Users/mac/.local/bin:$PATH" && npx -y pnpm@11.19.0 <command>`。
- **浏览器实测必须用干净环境**（2026-10-01 踩到）：DSH 的运行时会读 `DSH_PROFILE` / `DSH_PROFILE_DIR` 等环境变量，在这些变量存在（例如从 DSH 桌面 App 的会话里起子进程）时，即使用 `--profile ming-tea` + 自定 `DSH_HOME`，**桌面 profile 的用户层 patch 也会叠进来**（表现为默认模型变成 `deepseek-official` + `max`、需要 `DEEPSEEK_API_KEY` 而报 `MISSING_CREDENTIAL`）。起临时实例要用
  `env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=... DSH_HOME=<home> dsh --profile ming-tea --port 19390 --no-open`。

## 版本豁免（`compatibility.json`）

0.2 在**安装前**（`dsh plugin add` 的 preflight，跑在 pnpm 之前）和**加载时**都会检查插件的 `@deepseek-ai/dsh*` peer；不满足就拒绝安装（退出码 1）或跳过该 bundle。唯一官方出路的「精确版本豁免」写在 profile 的 `compatibility.json`，格式 `{ "<包>@<精确版本>": ["<精确 DSH 版本>"] }`。

当前工作树上的豁免**只有一条**（锁在 `assets/ming-tea-dsh-lock.json` 的 `versionExemptions`，由安装脚本在 `add` 之前自动授予）：

| 包 | 版本 | DSH | 为什么 |
| --- | --- | --- | --- |
| `dsh-plugin-shop` | `0.8.3` | `0.2.0-rc.2` | 上游 0.8.3 与 0.8.4-beta.0 的 peer 都停在 `^0.1.1-rc.2`，**没有支持 0.2 的版本**。经审计它实际 import 的 API（`dsh-app-boot` 的 `loadOptionalPatches`/`readProfileManifest`/`resolveProfileDir`、`dsh-typert-protocol` 的 `Remote`/`TypertRemoteService`）在 0.2 都存在（`dsh-app-boot` 导出面只增不减、另两个包逐字节相同），故按官方 `allow-version` 机制**接受风险运行**。上游发布支持 0.2 的版本后应撤销豁免并删除该条目（`dsh plugin revoke-version`）。 |

**实测到什么程度**：商店页面在 0.2 上正常渲染（目录列出 12551 条、分类、我们的「隐藏不兼容」默认开关都在、无控制台错误）；**「从商店安装某个第三方插件」这条动作没有实测**（会在 profile 里真的装入未审插件，留待单独验证）。其余 12 个插件在 0.2 上 peer 全部满足，不需要豁免。

## 当前目标

在先审计/复用 DSH 官方和社区组件的前提下，迭代 Windows、macOS 与 Ming OS 共用的 Agent Harness；由铭荼自己的权限、审批、审计和平台桥接控制电脑能力，并保持桌面应用独立于浏览器标签页。

## 当前状态

| 项目 | 状态 | 事实 |
| --- | --- | --- |
| TypeScript Agent、IPC、会话/场景、思考强度和权限审批 | `done` | 源码和对应 Vitest 测试存在；最近一次实测为 **9 个测试文件、37 项通过**（2026-09-30，`platform/ming-tea` 下 `vitest run`，退出码 0），之后变更应重新跑测试。 |
| Tauri macOS 开发 `.app` 构建 | `done` | 此前本地构建成功；不等于已完成签名、公证或自包含发行。 |
| macOS 独立分发 | `planned` | 当前 bundle 启动 Agent 仍依赖系统 Node；target-specific 签名 sidecar 和公证未完成。 |
| Windows 可分发版本 | `planned` | Named Pipe transport 已实现；Windows 桌面构建/实机验收未记录。 |
| Ming OS GTK/Python 接入 | `in-progress` | 旧入口和协议/构建集成存在；跨平台 Agent 接入与整套发行验收尚需核对。 |
| DSH runtime 0.2.0-rc.2 升级验证 | `done` | **2026-10-01 工作树实测**：runtime 与 9 个官方工具包升到 `0.2.0-rc.2`（= npm `latest`，与本机 GUI 一致），`dsh --version` 返回 `0.2.0-rc.2`；13 个社区插件升到声明兼容 0.2.0-rc.2 的版本。干净环境（`env -i`）下 `--dump-config` 1600 行、**无跳过/禁用**；浏览器实测三场景、设置页、商店目录、对话均通过。唯一例外是 `dsh-plugin-shop` 需要精确版本豁免（见「版本豁免」一节）。 |
| 社区 dsh-codex-ui 基线 | `done` | 2026-09-26 实测 `dsh plugin --profile web add @michengai/dsh-codex-ui@1.1.18` 成功；profile `package.json` 的 `bundles` 自动加入该插件，`--dump-config` 组合通过，web 页面注册并加载其 `client.js`。先前 `ERESOLVE` 阻塞未能复现；未解已证伪。 |
| OpenCode Zen / Big Pickle 免费端点 | `done` | 已从产品、IPC 和代码删除；上游 chat completion 返回 403，提示免费端点仅供 OpenCode 内部使用。不得重新加入。 |
| Windows/macOS OTA 发布链 | `planned` | Ming OS 侧复用 `ming-update` bridge；桌面端签名更新清单和保留旧版本的发布验收未完成。 |
| 铭荼图像生成技能 | `in-progress` | `~/.codex/skills/ming-tea-imagegen` 已建立，脚本只读 `MING_TEA_IMAGE_API_KEY` 并写无密钥恢复记录；尚未产出合格角色素材。 |
| web 前端（社区插件 + 铭荼定制层） | `in-progress` | profile `ming-tea`：官方 web 应用外壳 + `@michengai/dsh-codex-ui` 侧栏 + 铭荼定制层 + 10 个社区插件。2026-09-27 更：宠物换成 `@michengai/dsh-codex-pet`、新增思考强度滑块与归档/技能/专家/IM 插件。Tauri 壳仍是自绘 UI，尚未嵌入该前端。 |
| 铭荼界面层 | `in-progress` | 2026-09-27：品牌字标、首页「铭」字方章与标题、薄荷识别色、纸面底色、圆角尺度、思考强度滑块配色与宽度均已落地并实测；**浅色主题已在设置页验证**（`bgBase #f6faf8`、品牌 `#16857d`、`data-ds-theme-source=light` 正常切换，验证后已恢复“跟随系统”）。会话页内部（消息气泡、菜单、弹窗）仍未逐项走查。 |
| 辅助学习场景只读 | `done` | 2026-09-27 实测：学习场景移除 `write`/`edit`（工具可见性与执行一并移除），切回办公自动解除。机制是 agent 层 `tools.restrict`，不是内围栏；边界见插件 README。 |
| 浏览器自动化 | `in-progress` | 仅开发场景挂 `browser-use` + `playwright-mcp`（launch/headless）；结构性验证通过（组合树 + 启动无错），**运行时未验**（需模型凭据跑一次真实会话）。办公/学习未开放，桌面控制未装。 |
| 插件商店/应用市场 | `done` | 2026-09-27 实测：装入并跑通 `dsh-plugin-shop@0.8.3`（设置 → 随应用自带 → 插件商店），界面列出 12225 个插件；目录源已在 profile patch 钉死，typert codec 偏移由 `scripts/patch_shop_typert_compat.mjs` 本地等价改写。自建白名单目录仍是推荐下一步。 |
| 社区插件族（MichengAI + 滑块） | `done` | 2026-09-27 装入 10 个包并全部验收：codex-pet、archive-manager、skills-manager、agency-agents、im-connect、btw、simplify、code-review、plugin-effort-slider。11 个客户端 bundle 在页面注册成功，宿主启动零错误。 |
| 场景模式（替代官方四模式） | `done` | 按行 id 覆盖内置 preset 的 config（保留 `standard`/`ptc`/`minimal` 三个 id，只换名称、人格与**挂载的工具行**），停用内置 `cordis`。三场景现在能力不同：办公与学习不挂终端/后台任务，开发全量。实测选择器只列三个场景、默认办公模式、新建会话正常。教训见“重要教训：不要停用内置 preset”与阶段 6 证据。 |
| 站点接入（登录 / 额度 / 检查更新） | `in-progress` | 2026-09-27：宿主侧站点客户端 + 同源 RPC 通道（`/ming-tea`）+ 凭证存储 + 模型路由热改已实现并实测通路（`ping`/`debug.state`/`settings` 幂等空写/`update.check` 全部通过；离线自测 `scripts/check_ming_tea_hub.mjs` 22 项）；界面侧账户面板、剩余用量圆环、检查更新均已接线。**登录需账号本人授权一次**，故整条链仍标 `in-progress`。 |

## 已完成

- 建立 `platform/ming-tea` 的 TypeScript Agent、JSON-lines IPC 和 Tauri 桌面壳代码。
- IPC 覆盖三种场景、会话取消、会话中思考强度切换、工具请求/审批、模型提供方、记忆与会话检索、平台状态及 Ming OS OTA。
- 权限策略默认拒绝未知工具；高风险 terminal/system/file/browser/office 操作走审批；会话事件进行密钥脱敏。
- 社区适配器代码包括身份记忆、会话工作台、Cleverer DSH 安全子集和默认关闭的 EverOS loopback connector。
- OpenCode/Big Pickle endpoint 与对应实现已移除。
- 社区桌面插件 `@michengai/dsh-codex-ui@1.1.18` 的许可证、固定 revision 与 integrity 已审计并写入锁文件。
- 项目说明和本实时协作文档已建立，README 链接已加入。
- 已探测图像端点 `/v1/models`，实际模型 ID 为 `gpt-image-2.5-flare` 和 `gpt-image-2.5-sunburst`；没有字面上的 `image-2.5`。
- 已建立人物一致性四格提示词和 `ming-tea-imagegen` 技能；没有把用户 API key 写入项目或技能。

## 进行中

- ~~把隔离目录已验证成功的 DSH + 社区 UI 安装固化进 `scripts/install_ming_tea_plugins.sh` 与工作树 `.ming-tea/runtime`~~ **已完成（2026-10-01）**：脚本锁驱动、runtime 升到 `0.2.0-rc.2`，并新增「安装前清 `node_modules`」（否则旧包会锁死新包 peer，npm 直接 ERESOLVE）与「安装前授予精确版本豁免」两步。
- 对照当前 Tauri 壳与社区插件实际挂载方式，准确界定 UI 哪些能力来自社区包、哪些为铭荼壳自有；不要将“参考/计划挂载”表述为“已直接复用源码”。
- 校准 Ming OS GTK/Python 入口与跨平台 TypeScript Agent 的运行和发行边界。
- 等待图像服务提供与 GPT Image 模型兼容的生成/编辑路径，再生成 4K 四格人物参考图。

## 已解阻塞（留证）

### DSH 与社区 UI 安装（2026-09-26 实测通过）

先前记录的 `npm ERR! ERESOLVE unable to resolve dependency tree` 在本轮隔离验证中**未复现**，阻塞不再成立。实际失败原因与先前记录不同：首次执行 `dsh plugin --profile web add` 的报错是 `spawn pnpm ENOENT`——本机没有全局 `pnpm`，与 peer 依赖无关。

隔离验证步骤与结果（目录 `/tmp/ming-tea-dsh-verify`，验证后已删除）：

1. `npm install --ignore-scripts --save-exact` 锁定 runtime 与 8 个工具包（`@deepseek-ai/dsh@0.1.7-rc.1` 等）：525 packages、退出码 0。
2. `dsh --version` → `0.1.7-rc.1`；`dsh --help` 正常输出。
3. `DSH_HOME=<隔离目录> dsh web --help` 从内置模板自动初始化 `$DSH_HOME/profiles/web`。
4. 将 `pnpm@11.19.0` 加入 PATH 后 `dsh plugin --profile web add @michengai/dsh-codex-ui@1.1.18 --ignore-scripts --save-exact`：成功，`Done in 7.7s`，profile `dsh.profile.bundles` 自动变为 `[dsh-base, dsh-web-app, @michengai/dsh-codex-ui]`。
5. `dsh web --dump-config`：组合树成功，末尾出现 `# == @michengai/dsh-codex-ui` 及 `michengai-codex-ui-session-title`、`codex-ui` 两个条目。
6. `dsh web --no-open --port 18231` 实际启动：日志输出带 token 的 URL，HTTP 探测 401/303→200 正常。服务端 HTML 中该插件注册为 `{"id":"@michengai/dsh-codex-ui","url":"plugins/??@michengai/dsh-codex-ui/client.js&rev=cf8b1a12809c","inject":[8 个 client 包]}`，`inject` 所需依赖均在同一 profile 已解析。

`pnpm peers check` 仍会列出多组 "missing peer"，但这是 **hoisting 视角问题**：这些 `@deepseek-ai/dsh-client-*` 包由 dsh 安装自身的 web-app bundle 提供，pnpm 在 profile 目录看不到，不影响挂载——`--dump-config` 与真实启动均为成功，故不构成阻塞。

### 两项附带发现（尚未修复）

- `scripts/install_ming_tea_plugins.sh` 在 `dsh plugin ... add` 之后手工覆写 `${PROFILE_DIR}/cordis.patch.yml`。实测 DSH 已自行把插件写入 profile `package.json` 的 `bundles`，`cordis.patch.yml` 应保持用户 patch 层（模板内容为 `[]`）。该覆写是多此一举，且会覆盖用户 patch 层，建议删除这几行。
- 脚本第 12 行虽已用 `command -v pnpm` 检查，但实际调用 `dsh plugin` 时子进程仍需 pnpm 在 PATH；报错表现为较难定位的 `spawn pnpm ENOENT`。建议在文档中明确要求 pnpm 可执行，并改善该错误的提示。

### 图像接口（2026-09-27 探测）

图像端点 `/v1/models` 在带授权探测时返回两个 GPT Image 2.5 模型，但 `POST /v1/images/batches` 对 `gpt-image-2.5-flare` 返回 `404 RESOURCE_UNAVAILABLE`；带参考图的 `POST /v1/images/edits` 请求返回 `502 UPSTREAM_ERROR`。当前没有生成任务或成品图片，不能把技能状态写成 done。恢复要求：服务端提供与模型兼容的批量/编辑路径后，使用同一参考图提交四格测试，检查返回图像、人物一致性和透明裁切，再更新状态。

## 关键决策

- 用户可见场景固定为办公、开发、辅助学习；场景开始后锁定，思考强度 `fast/balanced/deep` 会话中可切换、下一轮生效。
- 用户要求优先在社区现成插件上进行大幅改造；当前选中的桌面面板候选是 `@michengai/dsh-codex-ui@1.1.18`，其 web profile 安装与挂载已于 2026-09-26 在隔离目录实测通过。
- 产品 UI、IPC 和 Agent 的权限边界归铭荼管理；DSH 是适配层后的运行时，不让 DSH 版本结构泄漏到平台外壳。
- OpenCode/Big Pickle 端点永久从当前产品方案中移除，不再探测或回接。
- Ming OS OTA 只经 `ming-update`；macOS/Windows app updater 是不同的发行机制，尚未完成。
- 未签名/未公证开发构建不能描述成可供普通用户安装的正式发行版。

## 插件/依赖状态

| 组件 | 固定版本/来源 | 状态 | 说明 |
| --- | --- | --- | --- |
| DeepSeek Harness runtime | `@deepseek-ai/dsh@0.2.0-rc.2`，MIT | `done` | **2026-10-01 工作树实测**：安装退出码 0、`dsh --version` 返回 `0.2.0-rc.2`、干净环境 web profile 可启动、`--dump-config` 组合通过。锁文件 `assets/ming-tea-dsh-lock.json` 的 `runtime.version` 与磁盘一致。 |
| DSH Bash、FS、Web、Browser Use、Computer Use、Playwright MCP experimental、Office skill、str-replace-editor | `assets/ming-tea-dsh-lock.json` 中均锁为 `0.2.0-rc.2`，MIT | `done` | 9 个工具包随 runtime 一起安装成功（退出码 0），版本与 lock 逐项比对一致；开发场景挂载的 `str-replace-editor` 在组合树内。各插件的权限边界与系统依赖见 `docs/ming-tea-plugin-audit.md`。 |
| dsh-codex-ui | `@michengai/dsh-codex-ui@1.1.25`，Apache-2.0 | `done` | npm integrity 在 `assets/ming-tea-dsh-lock.json`。**2026-10-01 在 0.2.0-rc.2 上实测**：`--dump-config` 无跳过、设置左导航 15 项全部渲染、首页/页脚/场景卡命中点存活、`dcu-*` 类名集合与 1.1.18 逐字节一致（273 个 token 无差异）。 |
| dsh-pet（桌宠） | `@linxin666/dsh-pet@0.4.3`，Apache-2.0 | `done` | peer 精确要求 `@deepseek-ai/dsh >=0.1.7-rc.1`，依赖仅 `clsx` 与 `schemastery`。2026-09-26 验证已挂载并实际渲染（页面出现 3 个 `data-plugin="@linxin666/dsh-pet"` 元素）。随包默认宠物素材许可另计，铭荼自有素材待制作。 |
| ming-tea-ui（铭荼界面层） | `@ming-tea/dsh-ui@0.1.0`，MIT，源码 `platform/ming-tea/plugins/ming-tea-ui` | `done` | 铭荼**自有**插件，非社区包：按 DSH 官方契约实现 cordis bundle + web client 插件，以 `link:` 装入。2026-09-26 量化验证圆角生效（详见“最近验证证据”）。不 fork 社区源码、不改写 DOM。 |
| dsh-agent-identity | commit `cbab483bbaa7c8ee44c1bdc958b491afaee6abdd`，MIT | `done` | 本地 `IdentityMemoryAdapter` 安全子集，不表示上游原插件运行时已安装。 |
| dsh-session-workbench | commit `9312b28922e65499eed54df86d9d26eda3000e4f`，MIT | `done` | 本地只读会话搜索/引用适配。 |
| cleverer-dsh | commit `40bd216ea9c7a95da887aa97fb661a0e8c7b1dd2`，MIT | `done` | 仅采用尝试次数和失败记录等本地安全子集，不执行上游脚本。 |
| EverOS Memory | commit `f76f4d06135a0b5d784d15eed133ecdcedc12d47`，Apache-2.0 | `planned` | 本机 loopback connector 默认关闭，不随低配系统默认启用。 |
| Honcho | AGPL-3.0 | `planned` | 仅外部连接器候选，未进入默认安装。 |
| dsh-data-agent | MIT, v0.2.0 | `blocked` | 还需评估数据库只读账户、SQL 执行和报告写入的审批边界；不进入默认安装。 |
| 用户提及但来源未确认的插件 | 清单 `unresolved-user-names` | `planned` | 未找到可唯一确认的来源前不安装。 |
| ming-tea-imagegen | 项目外技能 `~/.codex/skills/ming-tea-imagegen` | `in-progress` | 端点和模型已探测；批量/编辑实际生成分别遇到 404/502，等待上游兼容路径。 |
| 图像模型配置 | `platform/ming-tea/assets/ming-tea-image-models.json` | `blocked` | 已登记 `gpt-image-2.5-flare` 与 `gpt-image-2.5-sunburst`；两者生成请求均返回 502，未生成图片。 |

逐项审计细节见[插件审计清单](ming-tea-plugin-audit.md)和机器可读清单。

## 最近验证证据

2026-09-27 ZCode 界面质感与第三方插件优化（浏览器实测）：

- antd 统一：技能页 `--ant-color-primary` = `#4fb3a4`、`--ant-border-radius` = 12px，主按钮背景 `rgb(79,179,164)`；技能/归档页各 40–53 个 antd 元素全部跟随，未改 antd 类选择器。
- 质感细节：键盘 Tab 聚焦实测 `matchesFocusVisible: true`、outline 2px 薄荷（60% 透明度）、offset 2px；`--dsh-scrollbar-thumb` = `color-mix(in srgb, brand 28%, transparent)`；codex-pet 浮窗 `filter: drop-shadow(0 8px 20px rgb(0 0 0 / 26%))`。
- 布局：composer 底部行高保持 44px（单行），滑块 168px，控件为「权限 · 专家 · 模型+强度滑块」。
- 生图端点：`/health` 200、`/v1/models` 可读（`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`）；`/v1/images/batches/models` 与 `/v1/images/batches` **404 RESOURCE_UNAVAILABLE**；`POST /v1/images/generations/async` 返回 202 后任务数秒内 `failed`，`http_status: 503`、`error.type: upstream_error`。**未生成任何图片**。


2026-09-27 ZCode 插件扩充与回归修复（环境同上，profile `ming-tea`）：

- 插件安装：10 个包逐个 `dsh plugin --profile ming-tea add ... --ignore-scripts --save-exact`，全部退出码 0；`--dump-config` 仅剩一条预期告警（archive-manager 的 `ui-settings-unarchive-sessions not found`，插件自带说明已声明旧宿主会跳过）。
- 宿主启动：重启后日志零 error/failed/exception。
- 页面注册：11 个客户端 bundle 全部注册（`@ming-tea/dsh-ui`、`@michengai/dsh-codex-pet`、`plugin-effort-slider`、`@michengai/dsh-archive-manager`、`@michengai/dsh-btw`、`@michengai/dsh-codex-ui`、`@michengai/dsh-skills-manager`、`@michengai/dsh-code-review`、`@michengai/dsh-simplify`、`@michengai/dsh-im-connect`、`@michengai/dsh-agency-agents`）。
- 场景模式：选择器只列「办公模式 / 开发模式 / 辅助学习模式」，默认办公模式；`presetError: false`（回归已修）。
- 思考强度滑块：拖动轨道可改档并写回（`aria-valuenow` 3→1、`aria-valuetext` Max→Low，胶囊文本同步）；收窄宽度后 composer 底部行高 77px→44px 恢复单行；`--dsh-alias-button-info-fill` 实测解析为 `#16857d`（薄荷）。
- 宠物：linxin 宠物已卸载且无残留目录，codex-pet 正常渲染。
- 测试：`pnpm test` 9 files / 37 tests passed、`tsc --noEmit` 通过、`build:agent` 与 desktop build 通过、Ming OS unittest 30 tests OK。
- 锁文件：19 条（8 个官方工具包保持 sha512 + 10 个社区插件含 integrity + 1 个自建 link 包）。


2026-09-27 ZCode 中缝修复与界面定制（浏览器实测）：

- 中缝 A/B：有主题时该处为 30px 模糊阴影带；临时移除主题后为干净 1px 线。修复后 `_guide` 计算样式 `box-shadow: none`、`border-radius: 22px`，放大截图恢复为单线。
- 定制层实测：侧栏与设置导航文案变为「连接手机」（`IM助理` 不再出现）；`data-ming-tea-hidden="1"` 命中 `Codex UI`、`GitHub`、`问题反馈`（各 `display: none`），专家页头部只剩「检查更新」；页脚注入三个按钮（224×32，含 20px 图标位），点击「账户」弹出「账户与登录尚未接入，后续版本提供。」。
- 内置插件归属实测：在「内置插件」页搜索 `codex` 命中 `michengai-codex-ui-session-title`、`michengai-codex-pet`；搜索 `effort` 命中 `plugin-effort-slider`；搜索 `ming-tea` 命中 `@ming-tea/dsh-ui`（带「已启用」）。**这批插件已在部署内置列表中**；「插件配置」里的「已安装」分组是管理视图的分类，不代表未内置。


2026-09-27 ZCode 侧栏重排（浏览器实测）：

- 侧栏最终内容：新建任务 / 定时任务 / 任务·频道 / 置顶 / 项目 / 最近，页脚一行 `账户 · 📱 · ◯— · ⚙设置`（`.dcu-footer-actions` 与 `.dcu-settings-seat` 同排，y 分别 685/682）。
- 隐藏项实测：`.dcu-extensions-group` 整块 `display:none`，侧栏文本不再含「扩展管理」「连接手机」；设置导航可见 12 项（含保留的「连接手机」与新增的「检查更新」），仅「Codex UI」隐藏。
- 踩坑记录：定制层初版把 `Codex UI` 归入“仅侧栏隐藏”，而它只存在于设置页 → 被设置页保护规则跳过、重新显示；改为 `hideAnywhere` 后修正。
- 圆环实现：环（conic-gradient + 径向遮罩）与中心数值分两层，否则遮罩会把数字一起吃
  掉；无数据源时显示 `—` 并把圆环作为占位轨道。


2026-09-27 ZCode 定时任务插件与主按钮统一（浏览器实测）：

- 安装前：点侧栏「定时任务」落到设置页（automation 未装时 codex-ui 给的引导路径）。安装 `@michengai/dsh-automation@0.1.51` 后重启，同一入口打开真实界面：`定时任务 v0.1.51`、搜索、通过对话创建、新建定时任务、推荐案例三张卡、我的定时任务/执行记录标签页，空态文案「让重复的编码工作自动运行」。
- 主题继承：该页 `--ant-color-primary` 实测 `#16857d`，34 个 antd 元素一并跟随，无需为该插件写专门样式。
- 主按钮统一：新增规则把 `button[class*="_primary"]`（codex-ui 自绘主按钮）指向品牌色，排除 `:disabled`；实测「添加插件」渲染为薄荷底白字。注意 DSH 客户端 bundle 是**惰性物化**的——首次读取计算样式可能早于主题注入，判断生效需看渲染结果或稍后复测。
- 插件配置页现状：只列「官方 6」与「已安装 12」，无可下载插件列表；安装入口是右上角「添加插件」。


2026-09-27 ZCode 清亮模式与频道文案（浏览器实测）：

- 开关渲染：`.mt-lite-row` 位于「性能与用量」之后，标题/描述与原生行一致，开关 40×22，默认 `data-on="0"`。
- 开启实测（主界面）：`html[data-ming-tea-lite="1"]`；侧栏文本由「新建任务 定时任务 任务 频道 定时 …」变为「新建任务 任务 频道 …」；宠物浮窗 `display: none`；设置导航隐藏项 = 宠物/专家/定时任务（+ 常隐的 Codex UI）；`--mt-shadow-soft` = `none`，`transition-duration` = `0s`。
- 关闭实测：上述全部恢复（侧栏含定时任务、宠物 `display: block`、设置导航只剩 Codex UI 隐藏），开关 `data-on="0"`。刷新后状态由 localStorage 记忆，ON/OFF 均可复现。
- 文案修正实测：频道空态由「还没有频道会话。先在设置 → IM助理 里连接渠道。」变为「…设置 → 连接手机 里连接渠道。」；手工替换验证了 React 不会回写该节点。


2026-09-27 ZCode 前端深度质感升级（浏览器实测）：

- 令牌根因：修复前深层元素读到 `--dsw-alias-link: #4176e6`（官方蓝）、`--dsw-alias-bg-base: #fff`；修复后同一探针读到 `#16857d` / `#f6faf8`，`body` 内联样式出现我方全部令牌，`window.__mingTeaThemeErrors` 为空（官方 API 接受了 `--dsw-*` 命名）。
- 材质：`.dcu-*_card` 上 `--dsw-hovercard-bg` 解析为 `#fbfdfc`；`--trajectory-turn-accent` = mint 混色；`--dsw-specific-menu` = 我方纸面浮层；代码块圆角 12px、内容区圆角 22px。
- 首屏：服务端首屏 HTML 中我方 boot 样式紧跟官方之后（`dsh-boot-bg:#f6faf8`），且在应用脚本之前 —— 白色闪屏消除。
- 首页卡：官方 `.dcu-home-suggestions` 隐藏，注入 6 张场景卡（办公/开发/辅助学习各 2 张）；点击"整理文件与表格"后实测输入框内容变为我方提示词（`editorText` 命中），非装饰性卡片。
- 去品牌：`document.title` = `铭荼`；页面文本中 `DeepSeek Harness` / `DSH` / `DSH Web` / `DeepSeek 官方模型` 计数均为 0；模型名 `DeepSeek-V41-Flash` 保留（真实模型名不改）。

### 本轮未完成（下一轮）

- **阶段 6（场景化能力裁剪）**：需要先在隔离 profile 里试点"preset 内挂 `ctx.tools.restrict` 行"的行为，属会话能力变更，不能在本轮末尾仓促上；权限档品牌化经复核发现官方中文标签已合理（"工作区内修改"等），改名边际收益低，一并放到下一轮与场景裁剪同批评估。
- **阶段 7（低配默认不挂重插件）**：需先按既有教训核查这两行有无持久化引用，并实测插件配置页"手动开"确实生效。
- 阶段 8 的收尾（全量测试 + 锁文件一致性）在下一轮随 6/7 一起做。


2026-09-27 ZCode 阶段 6/7 实测证据：

- **restrict 机制被证伪**：把 `ctx.tools.restrict` 放进 preset 作用域后，新建会话报
  `agent-preset/invalid: ming-tea-scene-policy … 工具名无法识别`，且把办公场景的
  17 个工具名**全部**判为不可识别 —— 与契约"scope-local names fail"一致。
  该方案已完整回滚（模块、exports、生成行均已移除）。
- **行级裁剪生效**（`--dump-config` 分段核对）：办公 `tool-bash=无 tool-pwsh=无 tool-jobs=无`，
  辅助学习同上，开发三者皆 `有`；三场景的 tool-fs / tool-web / tool-subagent 均保留。
  生成器输出：办公 15/18 行、开发 18/18 行、学习 15/18 行。
- **低配策略 A/B**：以 `MING_TEA_LOW_SPEC=1` 强制后，marker 文件写入
  `(disabled: dsh-automation, im-connect)`、profile patch 追加带标记的段、原有四条用户设置完好；
  运行时侧栏的「任务/频道/定时」标签**消失**（`tabsInSidebar: []`）。手动删除该段后
  dump 中 `im-connect` 的 `disabled` 同步消失，恢复启动后三个标签**全部回来**。
- 边界如实记录：`tool-fs` 一次注册 read/write/edit 且无关闭开关，**学习场景做不到完全只读**；
  现状是无命令执行 + 写入逐次审批。

## 重要教训：不要停用内置 preset

2026-09-26 曾把官方四个模式（`standard`/`ptc`/`minimal`/`cordis`）停用并插入三个新 id 的场景 preset。2026-09-27 发现这是**破坏性改动**：

- DSH 把用户选中的默认 preset 持久化在 `agent-presets` 设置命名空间（`selectedDefault`），它优先于 bundle 里声明的部署默认值。
- 停用 `standard` 后该引用悬空，**新建会话直接失败**：`agent-preset/not-found: Unknown agent preset: standard`。
- 连锁后果：会话建不起来 → 编辑器不激活 → 模型选择、思考强度、权限预设三类控件都不渲染（用户报告“按钮没有了”）。
- 正确做法：**按行 id 覆盖内置 preset 的 `config`**（保留 `standard`/`ptc`/`minimal` 三个 id，只替换名称、描述与人格提示），这样任何已持久化的引用都能继续解析。内置第四个 `cordis` 可停用（创造模式入口，本产品不用），但若将来产品需要四档以上，也要走覆盖而非新增 id。

推论（写进 DSH 使用纪律）：**任何“删除/停用内置行”的补丁都要先问一句——有没有别处的持久化引用指向它？** 同样适用于 `agent-default-model`、主题与语言偏好这类被设置写入的 id。

- `cd platform/ming-tea && pnpm test`：9 test files、36 tests passed。
- `cd platform/ming-tea && pnpm exec tsc --noEmit`：通过（退出码 0）。
- `cd platform/ming-tea && pnpm build:agent`：通过。
- `cd platform/ming-tea && pnpm --filter @ming-tea/desktop build`：通过（vite 产物正常生成）。
- Ming OS unittest：`tests.test_ming_tea_core`、`tests.test_ming_tea_integration`、`tests.test_xiahai_integration`、`tests.test_papyrus_integration`，30 tests OK。
- Tauri macOS `.app` build：沿用 Codex 早前成功记录；本轮未重跑，DMG 路径仍未验证为稳定流程。
- 社区 UI profile 安装：**已在隔离目录与工作树 profile `ming-tea` 双重实测通过**（见“已解阻塞”一节）。工作树 `.ming-tea/runtime` 现已升到 `0.2.0-rc.2`（2026-10-01）。
- 图像生成：模型列表探测成功；批量生图返回 404，参考图编辑返回 502；未生成成品。

2026-09-26 ZCode 建成 web 前端并验证：

- `bash scripts/install_ming_tea_plugins.sh`：退出码 0；runtime 9 个包装为 `0.1.7-rc.1`；profile `ming-tea` 由 `web` 模板派生；`profile bundles: @deepseek-ai/dsh-base, @deepseek-ai/dsh-web-app, @michengai/dsh-codex-ui, @linxin666/dsh-pet, @ming-tea/dsh-ui`。
- `dsh --profile ming-tea --dump-config`：1251 行组合树，出现 `# == @michengai/dsh-codex-ui`、`# == @linxin666/dsh-pet`、`# == @ming-tea/dsh-ui` 三段插入条目。
- `dsh --profile ming-tea --no-open --port 18240`：启动成功，HTTP 200；页面注册 `@michengai/dsh-codex-ui` 与 `@linxin666/dsh-pet` 的 client bundle，宠物实际渲染。
- 圆角改造量化对照（改造前 → 改造后，均为浏览器实测计算值）：`.dcu-home-card` 20→22px、`.dcu-home-task` 10→16px、`.dcu-wb-section-head` 6→12px、`.dcu-wb-more` 4→12px、`.dcu-settings-trigger` 8→12px、`.dcu-extensions-toggle` 8→12px、`.dcu-wb-project-head` 10→16px、`[role=dialog]` 24→26px；官方令牌 `--dsh-windows-content-radius` 生效为 22px。
- 测试：`pnpm test` 9 files / 37 tests passed（新增“registry 包强制 sha512、第一方 link 包豁免”一条）；`pnpm exec tsc --noEmit` 通过；`build:agent` 与 desktop build 通过；Ming OS unittest 30 tests OK。

2026-09-26 ZCode 场景模式与界面差异化（第二轮）：

- 模式合并实测：首页模式选择器点击后只列出 `办公模式` / `开发模式` / `辅助学习模式` 三项（各带一句说明），座位按钮默认显示 `办公模式`；官方四个模式不再出现。
- 界面差异化实测：侧栏字标由官方矢量字标换成「铭荼」（`.dcu-brand::after` 生效、官方 SVG `display: none`）；首页标语由官方文案换成「需要我帮你做什么？」（`[data-phase="hero"]` 内替换，官方 span `display: none`，徽标保留）；首页标记换成薄荷「铭」字方章；`--dsw-alias-link` 实测为 `#16857d`；`.dcu-home-card` 圆角 22px、描边为薄荷 26% 混色。
- 陷阱记录：`dsh --dump-config` 对 preset 这类 EntryGroup 行**不渲染 `disabled` 字段**，停用已生效时 dump 里仍看不到标志。判断模式是否停用要看运行时名册（首页选择器实际列出的项），不能只凭 dump。
- profile 的 `cordis.patch.yml` 是 DSH 与设置界面托管的用户层，实测内含主题偏好（system）、语言（zh）、默认模型（deepseek-official/deepseek-flash, reasoningEffort max）与宠物配置（visible, size 160, petId whale-girl）。任何脚本不得覆写；本轮未改动。

以上是最近一次已记录结果，不自动代表之后的改动也通过。

## 下一步

当前执行方向（2026-09-26 用户确认）：**先把 DSH web profile 当作铭荼前端在浏览器里做出来，确认外观后再嵌入 Tauri**；圆角按“明显圆润”一套 token（12/16/22/26/999）。宠物于 2026-09-27 改为 `@michengai/dsh-codex-pet`（原 linxin 宠物已卸载，两者并存会出现两只）。

1. 制作铭荼自有宠物素材并按 v2 manifest 契约校验入库。完整规格见[铭荼宠物素材规格](ming-tea-pet-assets.md)：webp 图集、8 列、单元格默认 192×208、经典 9 行（idle / running-right / running-left / waving / jumping / failed / waiting / running / review），另需 `sequences` 把应用相位（idle / waiting / thinking / tool / review / done / failed）映射到行轨道。注意：校验 CLI 未随 npm 包发布，改用 `contracts/pet-manifest-v2.schema.json` 校验 + 页面实际渲染验收；codex-pet 另带 `hatch-pet` Skill，需先在 DSH 内配置图像生成工具。
2. 在真实会话中继续打磨铭荼界面：会话页标题、消息气泡、菜单/弹窗的薄荷化与圆角（主页与设置页已成型，**浅色主题已验**）。首页四张引导卡片仍是官方文案且偏开发向，按场景改写属于后续工作。
3. 验收本轮新装插件尚未覆盖的行为（安装后清单来自 rc.1 审计）：`/btw` 气泡、`/simplify` 与 `/review` 命令、技能管理器的 GitHub 安装流程、专家插入行为、归档/永久删除（含批量）、IM 接入在未配置渠道时确认无后台出网（账号绑定需用户扫码）。
3. 按场景裁剪工具与权限边界（当前三个场景工具相同、仅人格提示不同），需要与 Agent 权限策略一起设计。
4. 把 web 前端嵌进 Tauri 壳：`dsh-adapter.ts` 抓 stdout 的 URL+token、新增 IPC action、`lib.rs` 建窗口导航、`tauri.conf.json` CSP 放行，并同步改写设计文档第 31/97/110 行与 `app-shell.test.ts`、`ming-tea-plugin-audit.md` 的门禁。
5. 对 Windows、macOS 开发构建分别做启动、IPC、权限状态和打包验收；随后推进签名 sidecar 与桌面 OTA。
6. 维护 Ming OS GTK 与跨平台 Agent 共享 IPC 的安装/启动契约，并运行 Python、TypeScript、Tauri 和发行门测试。
7. 图像服务恢复后，用 `ming-tea-imagegen` 生成 4K 四格角色图，核对人物一致性后再裁切为宠物图集；使用后轮换曾在聊天中暴露的 API key。

| 2026-09-27 | ZCode 修复中缝并做界面定制：**中缝根因是我的主题**——把 30px 模糊阴影加到了 `[class*="_guide"]` 等结构容器上，而右侧边栏内容容器全高整列，阴影跨栏渗出成一条发虚竖带（A/B 实测：摘掉主题后是干净的 1px 线）。改为「结构容器只统一圆角、阴影只给浮动与小面积元素」。新增 `ui-tweaks.js` 定制层（内联进 client bundle）：IM助理→连接手机（侧栏与设置导航）、隐藏 Codex UI/GitHub/问题反馈、侧栏页脚注入账户/剩余用量/检查更新三个占位入口（点击提示尚未接入）。该层按叶子文本精确匹配、幂等重放、异常静默降级；与「只做样式叠加」的原则是有意例外，脆弱性已记入插件 README。 |
| 2026-09-27 | ZCode 侧栏重排与入口整理：页脚改为一行（账户 · 连接手机图标 · 剩余用量圆环 · 设置）；移除侧栏「扩展管理」整块（含 专家/技能/插件/连接器）与侧栏「连接手机」条目（设置里保留并新增「检查更新」）；把定制层拆成 `hideAnywhere` 与 `hideInSidebar` 两类——第一版曾因把 `Codex UI` 放进“仅侧栏”规则而让它重新显示（它只存在于设置页），已修正。剩余用量圆环在无数据源时显示 `—`，**未编造百分比**。 |
| 2026-09-27 | ZCode 安装定时任务插件：`@michengai/dsh-automation@0.1.51`（+69 包，含 antd 6.6.5/luxon/zod）。实测侧栏「定时任务」从“打开设置页的安装引导”变为真实界面（推荐案例 / 我的定时任务 / 执行记录 / 新建定时任务），其 antd 主色自动为 `#16857d`。另把 codex-ui 自绘主按钮（`<hash>_primary`，如「添加插件」）与 antd 主按钮统一为品牌色，并排除 disabled 态。锁文件补 integrity 时被既有测试拦下一次（缺 sha512 不许入库），补齐后 37 项测试全绿。 |
| 2026-09-27 | ZCode 新增「清亮模式」与文案修正：设置→常规 加开关（行结构克隆真实设置行），打开后隐藏宠物/专家/定时任务（含浮窗、侧栏条目、设置导航条目、侧栏「定时」标签）并停掉全部动画与柔光阴影；状态存浏览器 `localStorage`（未做服务端同步，已如实标注）。修正频道空态句子里的「IM助理」→「连接手机」（此前等值改名覆盖不到句内引用，新增子串替换规则）。过程中修掉两个自身缺陷：开关外观不随状态刷新、文本遍历器 24 字上限导致 24+ 字的提示句根本未被处理（实测手工替换有效、证明是逻辑 bug 而非 React 回写）。默认关闭，未改变原有环境。 |
| 2026-09-27 | ZCode 前端深度质感升级（阶段 1-5）：**修复令牌覆盖失效的根因** —— 官方把设计令牌声明在 `body` 上，我们此前写在 `:root` 被遮蔽，实测 `--dsw-alias-link` 在深层元素仍是官方蓝（此为"界面仍像原版"的主因）。改为色板单一来源 `theme/palette.mjs`，经**两条通道**投递：官方 `ctx.theme.overrideTokens`（写 body 内联样式）+ `:root body` 作用域的 CSS 兜底；共 58 个令牌（含官方 15 个未定义项，如 `state-warning-primary` 原本回退成品牌蓝）。另完成：中文优先字体栈与 `--dsw-font-mono` 补位、HoverCard 浅色硬编码深卡修正、macOS 侧栏蓝紫渐变换薄荷、轨迹强调色、**宿主侧首屏防闪**（`webserver/index-inject`）、**首页四张开发向卡片换成铭荼三场景卡且点击真的写入输入框**（走官方 `conversation.input.for(...).setDraft` 路径）、标题与引导弹窗/账号页等去品牌文案（实测该页 DSH/DeepSeek Harness 残留为 0，模型真实名保留）。 |
| 2026-09-27 | ZCode 场景化能力裁剪 + 低配默认策略（阶段 6/7）：**按场景裁剪工具的正确机制被实测纠正** —— 原计划用 `ctx.tools.restrict`，但契约写明它只作用于全局工具且"作用域内的名字会失败"，而 preset 的 `tool-*` 行属于该 preset 作用域，实测直接导致 preset 挂载失败（`agent-preset/invalid`）；`guard` 同样不适用（plain-context 全局生效）。改用官方预设本来的做法：**每个场景挂哪些行不同**（办公与学习不挂终端/后台任务行，开发全量）。低配策略：宿主半区启动体检（内存 ≤4GB 或核数 ≤2）时把 `dsh-automation` 与 `im-connect` 写进 profile 用户层设为停用（标记文件保证只决定一次、只追加不动其他条目），用户可在插件配置页手动开启。 |
| 2026-09-27 | ZCode 辅助学习场景改造 + 电脑操作能力核查：① 按用户澄清把该场景定位改为**面向中小学生与大学生、角色相当于老师**（引导思考而非代做作业），并用官方 `agent/created` + `agent-preset/selected` 两条路径对该场景 `restrict({deny:[write,edit]})`，实现**真正的只读**（实测日志：切到学习→`已移除写入工具: write,edit`，切回办公→`已解除只读限制`）。② 电脑操作能力核查结论：**当前完全没有** —— 四个官方包已装且可解析但**零挂载**；且 DSH 层对这些工具无审批闸门，我们的 `computer-use-guard.ts` 是死代码且参数模型与真实工具不匹配。因此只给**开发场景**挂了浏览器自动化（launch+headless，独立会话），办公/学习不挂，桌面控制刻意不装。③ 插件商店调研：**没有任何现成商店可直接用于家庭用户产品**，详见审计文档新增章节。 |
### 2026-09-27 ZCode 应用商店落地 + 三个场景预加载失败修复（浏览器实测）

**1. 三个场景全部「加载失败」——真回归，已定位并修复（用户可见）**

- 现象：设置 → 助手模式 里三个场景都带红色「加载失败」；「随应用自带 → 插件列表」里选预设显示「办公模式（加载失败）」。
- 定位方法：预设管理页卡片内有一段**视觉隐藏的原因文本**（`[class*="cardBrokenReason"]`），可直接从 DOM 读出真正原因：
  - `standard`：`Preset services require isolate realms: browserUse, computerUse`
  - `ptc` / `minimal`：逐条报重复注册 —— `service "browserUse" has been registered at <BrowserUseRegistry>`（browser-use / playwright-mcp / computer-use / cua-driver-native 四行）
- 根因（上一轮引入）：把「浏览器 + 电脑操作」四行**平铺**进每个场景的 `plugins` 列表。服务型行（publish `browserUse` / `computerUse`）在 preset 作用域里会与根作用域或其它预设的注册撞名。
- 修复：`scenes.config.mjs` 改为 `extraGroups: [MING_TEA_ABILITY_GROUP]`，生成器输出 `- id: abilities / name: cordis:group / group: true / isolate: { browserUse: true, computerUse: true }`。官方在预设里挂服务型行正是同一手法（standard 预设的 planning / compaction / delegation 三组都带 `isolate`）。
- 验证（浏览器实测）：重新生成并重启后，三个场景 `broken` 全为 false、诊断原因列表为空；`abilities` 组出现 3 次（每场景一次）。
- 能力与闸门：四个能力包本次**真正挂载**（此前 0 挂载）。实测工具名前缀 `cua_driver_native__*`（包源码确认）与 `mcp__playwright-mcp__*`（`mountSessionMcp({ name: "playwright-mcp" })` + runtime 的 ``` `mcp__ ``` 前缀约定），与 `lib/index.mjs` 的 `AUTOMATION_FAMILIES` 一致；`mode` / `headless` 也确认是合法配置键。只读元数据（list_apps / list_windows / get_screen_size / get_cursor_position / list_tools）免审批，其余一律 `approval.request` 且只接受 `allowed-once`，无审批通道时失败关闭。

**2. 插件商店（`dsh-plugin-shop@0.8.3`）已装入并实测可用**

- 位置：设置 → 随应用自带 →「插件商店」（与官方「插件列表」并列）。实测列出 **12225 个插件**、分类筛选（工具 5859 / 界面 3094 / 集成 1061 / 模型服务 650 / 工作流 637 / 主题 319 / 其他 605）、搜索、刷新、安装按钮、作者/星数/磁盘占用；目录构建于 2026-09-26。
- 商店自带「隐藏不兼容 1103」开关（默认关）：目录里有 1103 个被标记与当前 DSH 不兼容。**建议后续把默认改为开**，本轮未做。
- **typert codec 形态偏移（装不起来的真因）**：商店编译产物把 codec 写成 `schema: <zod schema 值>`，而 DSH 加载器（rc.1 与 rc.2 实现相同）和**已发布的**生成器（`@deepseek-ai/dsh-typert-generator@0.1.7-rc.2`）都用 `create: <惰性工厂>`。宿主半区报 `… codec has no create() factory`（`1 entry did not activate`）；只改 `typert.host.js` 时网页端又报 `web boot: 1 entry did not activate dsh-plugin-shop: failed`（因为 `client.js` 里另有一份内联清单）。
- 处置：新增 `scripts/patch_shop_typert_compat.mjs`（幂等、`--revert` 可还原），对 `typert.host.js`、`typert.remote-client.js`、`client.js` 三份产物做等价改写 `schema: X → create: () => X`（只在 codec 对象内命中，`schemas:` 数组不动），共 48 处；已接入 `install_ming_tea_plugins.sh`。
- **目录源钉死**：`catalogUrl` 钉到 `https://LivXue.github.io/dsh-plugin-shop/v1/`，避免环境变量 `DSH_SHOP_CATALOG_URL` 把目录改向（目录内容决定能装什么）。写入位置必须落在 **profile patch**（`$DSH_HOME/profiles/ming-tea/cordis.patch.yml`）——我们的 patch 是 bundle 层，先于商店自己的 bundle 层应用，按 id 覆盖会**静默失效**（实测 dump 里仍是环境变量表达式）。自建白名单目录时只改这一行 URL。
- 目录契约（用 Node 独立复核）：指针 JSON（`schemaVersion` 5、`count` 12283）+ 内容寻址的 `plugins.<sha256>.json`（10.46 MB，sha256 一致）；条目含 owner / repository / version / integrity / license / stars / downloads / tier / verified；安装走 `dist.tarball` 且**强制与声明的 registry 同源**；商店支持 `schemaVersion ≤ 6`。

**3. 本轮新增教训（全部实测踩到）**

- **按 id 的 `config` 覆盖是整块替换，不是深合并**：给商店只写 `catalogUrl` 会丢掉默认的 `cacheDir`，界面表现是「暂时无法读取目录。」——覆盖时必须把同行的其它必需键一起写上。
- **preset 内挂服务型行必须 `group` + `isolate`**：平铺会让**整个场景**加载失败（不是那几行不可用），且不同 preset 的报错文案还不一样（isolate 缺失 / 重复注册）。
- **bundle 层之间按 id 覆盖会静默失效**：跨 bundle 的覆盖要写进 profile patch（它在所有 bundle 层之后应用）。

### 2026-09-27 ZCode 界面四项（页脚版式 / 收起态 / 商店改造 / 上下文指示器）+ 自动化点击实测

**1. 侧栏页脚按用户给的参考图重排**：`账户（圆头像 + 名称）→ 剩余用量圆环 → 连接手机 → 设置`。
顺序用 CSS `order` 实现而不是挪 DOM —— 官方「设置」在它自己的 `.dcu-settings-seat` 容器里
（是 `.dcu-footer-actions` 的**兄弟节点**，不是子节点），靠 DOM 顺序排不了。

**2. 收起侧栏的两个问题（用户报的「只显示一半的账户」是真的）**：

- 实测几何：轨道 36px 宽（`.dcu-root.dcu-compact` 还带 `overflow:hidden`），而我们注入的账户按钮
  当时 58px 宽、所在容器 126px ⇒ 被裁掉一半，同时**官方设置入口被压成 0 宽而整块消失**。
- 修复：`.dcu-compact` 下把 `.dcu-foot` 改成竖排，四个入口各 32px 定宽居中、隐藏文字只留图标；
  同时给 `.dcu-settings-seat` 定宽，官方设置图标回到轨道里。
- 实测（浏览器量框）：`footW=36`，头像/圆环/手机/设置分别落在 `left=12 top=564/600/634/668`，
  四个都 `w=32`、全部 `insideRail: true`。

**3. 商店（dsh-plugin-shop）三项改造**（都在我们自己的层里，不 fork 商店源码）：

- **安装前二次确认弹窗**：捕获阶段拦 `[data-shop-install]` / `[data-shop-update]` 的点击
  （React 18 的委托监听在 root 容器上，捕获阶段 `stopPropagation` 就能拦住），
  弹我们自己的确认框：名称/版本/来源/信任级别/作者/许可/仓库/校验值 + 风险说明，
  焦点默认落在「取消」。许可与仓库是**实时向商店目录查**的（`ctx.get("remote.shop").catalog()`），
  1.5s 超时或查不到就少显示几行，不阻塞确认。
  - 实测：取消 → 弹窗消失、无任何安装动作；确认 → 放行给商店（社区条目会再弹商店自己的
    acknowledged 确认，这一步是宿主强制的，我们不复刻也不绕过）。
  - **本轮踩坑**：`[data-shop-confirm]` 是商店自己的「确认」按钮而不是「取消」，
    我在测试里点错导致触发了一次真实安装请求；随后核查 `profiles/ming-tea/package.json`、
    `pnpm-lock.yaml`、`node_modules` 均无痕迹、无安装进程在跑 ⇒ **实际没有装进任何东西**。
    教训：拿别人的按钮做测试前先确认它到底是哪个动作，并且只点「取消」。
- **美化**：卡片 22px 圆角 + 薄荷描边 + 悬停上浮、安装/更新按钮改成品牌实心胶囊、
  分类 chip 选中态品牌色、搜索框胶囊化、徽章统一浅薄荷底、开关品牌色。
  选择器用 `data-*` 与语义后缀（`[class*="_installButton"]`）并把作用域限定在 `[data-shop-tab]` 内，
  不依赖随构建变化的 hash 前缀，也不波及其他插件的同名类。
- **默认隐藏不兼容条目**：商店原本是 `useState(false)`（内存态、不持久化），
  默认把 1103 个与当前 DSH 不兼容的条目一起展示。经 `scripts/patch_shop_typert_compat.mjs`
  改写为 `true`（幂等、`--revert` 可还原），实测开关 `aria-checked="true"`。

**4. 输入框旁新增「上下文占用」指示器**：官方其实有 ContextMeter（composer 下方的圆环），
但它只在「有会话且已产生过一次用量」时才渲染 —— 没有模型凭据时永远看不到，这正是用户报的现象。
我们按同一口径（`used = projectedTokens ?? pressureTokens`，分母 `contextWindow`）自己渲染一个
**始终可见**的指示器（无数据时显示「上下文 —」并说明首次回复后出现），
数据来自 `ctx.sessions.binding(id).session.projections.faceOf("contextPressure")` 订阅，
挂在输入卡片内部底栏下方（用「含 `_input` 元素的 `_card`」定位真正的输入卡片）。

**5. 自动化「实际点击流程」实测通过（无模型凭据条件下）**：新增可复用脚本
`scripts/check_automation_click.mjs`，用与插件**完全相同**的命令行启动 Playwright MCP server
（`node <@playwright/mcp>/cli.js --browser chromium --isolated --headless`，v0.0.80 / Playwright 1.63.0-alpha），
走 MCP 协议实测：握手成功 → 列出 24 个 `browser_*` 工具 → 打开铭荼页面（标题「铭荼」）
→ 快照定位场景卡（ref e125）→ `browser_click` 点击 → **读回输入框确认生效**
（内容为场景卡提示词「帮我把这个文件夹里的文件按类型整理好，并列出重命名建议。」28 字）→ 截图。
结论：本机自动化后端（Chromium 二进制、MCP server、点击链路）确实可用；
工具名前缀由 DSH 侧统一加 `mcp__playwright-mcp__`，与我们审批闸门的匹配规则一致。
**仍未验证的一段**：模型 → DSH 工具层 → 审批闸门 → 真实点击的整链，需要模型凭据（见下）。

**6. 两条新教训**：

- `requestAnimationFrame` 回调在插件物化后**没有执行**，导致确认弹窗写进了 DOM 但 `opacity` 停在 0
  （看不见也点不到）。改成插入后同步置位。凡是"延迟一帧再显示"的地方都要有可见性自检。
- 拦截第三方按钮做确认时，要先确认这个按钮**到底绑的哪个动作**：`data-shop-confirm` 看着像"确认框"，
  实际是"确认安装"本身。

## 更新规则

- 每次完成实现、作出架构决策、测试失败、遇到外部依赖阻塞时更新“当前状态”“最近验证证据”和“变更记录”。
- 记录日期、命令、退出结果和环境；只写实际观测结果，不根据预期推断通过。
- 版本变化同步更新 `assets/ming-tea-dsh-lock.json`、社区候选 JSON、插件审计文档和项目说明。
- 只有 profile 文件与 runtime 检查均通过，才把社区 UI 状态改为 `done`/已安装。
- 所有后来者（包括 ZCode）先读本页，再开始实现；有不同结论时先用仓库/命令输出核验，并在此追加，不要悄悄覆盖事实。

## 变更记录

| 日期 | 更新 |
| --- | --- |
| 2026-09-26 | 首次建立项目说明与跨助手实时状态；记录 DSH `0.1.7-rc.1` 升级和社区 UI profile 安装仍未验收，澄清 OpenCode 已移除。 |
| 2026-09-26 | ZCode 接入复核：重跑 TypeScript 测试（36 通过）、tsc、agent/desktop 构建与 Python 集成测试（30 通过）均通过；实测 `.ming-tea/runtime` 仍为 `0.1.6-alpha.2` 且无 `dsh-home` web profile，确认 DSH/社区 UI 安装阻塞属实；补充工作树 detached HEAD、未提交改动清单与本机 node/pnpm 运行方式。 |
| 2026-09-26 | ZCode 隔离验证解除 DSH/社区 UI 阻塞：runtime `0.1.7-rc.1` 安装与 `dsh --version` 通过；`dsh plugin --profile web add @michengai/dsh-codex-ui@1.1.18` 成功且 `--dump-config`、真实启动、页面 client 注册均通过。原 `ERESOLVE` 未复现，真实首错为 `spawn pnpm ENOENT`（本机缺全局 pnpm）。同时确认安装脚本手工覆写 `cordis.patch.yml` 属多余。 |
| 2026-09-26 | ZCode 建成 web 前端：安装脚本改为锁驱动并支持第一方 `link:` 包（删除 patch 覆写、自带 pnpm 入 PATH、profile 改名 `ming-tea`）；工作树 runtime 重装为 `0.1.7-rc.1`；profile 装入 codex-ui、dsh-pet 与自建 `@ming-tea/dsh-ui`；新增铭荼自有界面层（cordis bundle + web client 插件，CSS 内联注入）并量化验证“明显圆润”主题生效。`dsh-adapter` 默认 profile 由 `web` 改为 `ming-tea`；`plugin-registry` 对第一方 link 包豁免 npm integrity，registry 包仍强制 sha512（新增测试覆盖）。测试 37 项、tsc、构建与 Python 30 项全部通过。 |
| 2026-09-26 | ZCode 场景模式与界面差异化（用户要求“四个模式改成一个/自动，用户只选三个场景”＋“前端太像原版”）：停用官方 标准/PC/极简/创造 四个 preset，生成三个场景 preset（工具列表派生自官方 standard，避免漂移），部署默认值改办公模式；实测选择器只列三个场景。界面层加入铭荼字标与首页「铭」字方章、首页标题改「需要我帮你做什么？」、薄荷识别色覆盖官方蓝别名、纸面底色与卡片薄荷描边。记录 `--dump-config` 不渲染 EntryGroup 行 `disabled` 的陷阱。测试 37 项与 tsc 通过。 |
| 2026-09-27 | ZCode 修复上一行引入的**真实回归**：停用内置 preset 使设置里持久化的 `selectedDefault: standard` 悬空，新建会话报 `agent-preset/not-found: Unknown agent preset: standard`，编辑器不激活、模型/思考强度/权限三类控件全部不渲染（用户报“按钮没有了”即此）。改为**按行 id 覆盖内置 preset 的 config**（保留三个内置 id，只换名称/描述/人格；停用内置 `cordis`），会话与控件恢复。**教训：不要停用内置 preset。** |
| 2026-09-27 | ZCode 插件扩充（11 个包，逐个安装并验收）：宠物换代 `@linxin666/dsh-pet` → `@michengai/dsh-codex-pet@0.1.10`（两者并存会出现两只宠物；后者随包图集来自 OpenAI Codex，NOTICE 明确不在 Apache-2.0 内，不可随 ISO 分发）；新增思考强度滑块 `plugin-effort-slider@1.2.2`（实测改档 Max↔Low 可用，颜色随 `--dsw-alias-button-info-fill` 被主题接管，宽度由 208px 收窄到 168px 使 composer 恢复单行）；新增归档/技能/专家/IM/旁问/simplify/code-review。安装前由子代理做 rc.1 契约源码审计：10 包均可装、无 peer 硬阻断。清理了指向已卸载宠物的死 patch 条目（DSH 曾报 `patch: entry "pet" not found`）。锁文件重生成时曾误删 8 个官方工具包，被既有测试抓出并恢复（19 条）。测试 37 项、tsc、构建、Python 30 项通过。 |
| 2026-09-27 | ZCode 界面质感打磨与第三方插件针对性优化：**antd token 统一**（技能/归档等插件用 antd v5 的 CSS 变量模式，只覆盖 `--ant-color-primary` 与 `--ant-border-radius*`，实测主色 `#7aaaff`→`#4fb3a4`、按钮圆角 4px→12px，分段控件与开关一并跟随）；新增键盘焦点环（`:focus-visible` 2px 薄荷描边，Tab 实测 `matchesFocusVisible=true`）、文本选中薄荷底、细滚动条跟随品牌、交互过渡统一 180ms/`cubic-bezier(.22,1,.36,1)`、首页卡片悬停上浮（reduced-motion 下关闭）、codex-pet 浮窗柔和投影。**生图能力仍不可用**：批量路由实测 404、单图异步路由 202 后上游 503；已把技能副本装到 ZCode 侧（`~/.zcode/skills/ming-tea-imagegen`）并在其 API 文档中补录实测路由现状，未声称生成任何图片。 |
| 2026-09-27 | 建立 `ming-tea-imagegen` 技能和四格人物提示词；探测到 GPT Image 2.5 模型，但批量接口 404、参考图编辑接口 502，未生成图片；API key 未写入项目或技能。 |
| 2026-09-27 | **Scallion 侧（站点）对接现状——来自站点仓库会话**：铭荼的三个占位都有现成接口可接。①账户登录 → Papyrus 设备授权三端点（`POST /api/papyrus/auth/device` → 浏览器打开 `verificationUrl` 用主站账号授权 → 客户端轮询 `GET /api/papyrus/auth/device/:code` 拿一次性 token）；②剩余用量 → `GET /api/papyrus/llm/quota`（返回 `auto.{monthly_limit,monthly_used,monthly_remaining,daily_*}`，正好喂圆环）；③检查更新 → `GET /api/papyrus/update`（Tauri 签名清单）。免费层用 `POST /api/papyrus/llm/chat` 且 `model:'auto'`（服务端选模型、不扣积分、Free 300 次/月、日不限）。完整契约、发布清单与实测证据写在站点仓库 `docs/MING_TEA_INTEGRATION_2026-09-27.md`。 |
| 2026-09-27 | **Scallion 侧两个阻塞（实测，均已定位，修复未发布）**：①线上授权页被下线——`src/App.jsx` 里 `/papyrus/authorize` 自 8/31 构建起是 `<Navigate to="/">`，用户打开授权链接会被弹回首页，**整条设备登录目前无法完成授权**；站点侧已恢复该路由源码（未构建未发布）。②免费层 Auto 被挡死——生产 `services/papyrusPlanAccess.js`（7/28 旧版）把 `autoDailyCalls: null` 当 0，`assertPapyrusAutoAvailable` 要求月/日剩余同时 >0，于是 **Free 用户每次 Auto 都抛 429 `auto_quota_exhausted`**（三个免费用户实测全部 BLOCKED；接口却仍声明「300 次/月，日限额不限」）。Auto 引擎本身正常：服务器本机实调 `/api/internal-ai/v1/chat/completions`（`model:"auto"`）返回内容成功。修复已在站点仓库（`services/papyrusPlanAccess.js` + `routes/papyrus.js`，需同批发布）。 |
| 2026-09-27 | **给铭荼侧的接入约定与红线**：①**不要把站点 `INTERNAL_AI_API_KEY` 或任何站点密钥打进客户端**——客户端可被反编译，泄漏等于厂商额度对外开放；一律用设备授权换**用户自己的** token，额度按用户计。②本侧不是 OpenAI 兼容端点，铭荼侧建议写薄 adapter（宿主 OpenAI 请求 ↔ `POST /api/papyrus/llm/chat` `model:'auto'`，把 `choices[0].message.content` 还原）。③在站点侧两个修复发布、且端到端验证通过之前，**不要声称登录或免费额度已可用**；「剩余用量」继续显示 `—` 而不是编造百分比。④铭荼若要做自己的 OTA，复用「清单端点 + GitHub release + 签名」这套形态，但要用独立清单文件，不要共用 Papyrus 的。 |
| 2026-09-27 | **站点侧已发布（铭荼可开始对接）**：①**登录**——`/papyrus/authorize` 授权页已恢复上线并去 Papyrus 化（默认显示「铭荼」、`?client=ming-tea` 可切换、薄荷色 `#16857d`），另加通用别名 `/device/authorize`；设备授权链路实测通过（申请设备码→浏览器授权→轮询取一次性 token）。②**模型**——按你的选择，铭荼用 **`POST /api/dev-api/v1/chat/completions`**（OpenAI 兼容，`model:"auto"`，支持 `stream:true`，实测解析到 `gpt-5.4-nano` 并返回内容）。③**免费层额度**——站点侧 429 阻塞已修复，Free 用户 Auto 恢复可用（300 次/月、日不限）。④另：`/api/papyrus/download/latest` 已对齐到 **v1.1.1**，`/api/papyrus/update` 清单原本就是 1.1.1。 |
| 2026-09-27 | **给铭荼侧的三个必须知道的事**：①dev-api 端点用 **API Key** 鉴权，而**创建 Key 需要 Deeper 套餐或已激活开发者**（免费用户无法自助创建）——所以「免费层」的三条路是：运营方一把 Key（最简单，但**切勿硬编码进安装包**，会被反编译烧额度）、每用户自助建 Key（免费用户走不通）、或铭荼自己做服务端代理（最安全，需开发）。请确认走哪条。②**上游现在不稳**：NVIDIA 4 把 Key 里 3 把 429 限流、多数型号已 410 下线，Agnes 限流，DE5 有一把 Key 额度 $0.00，SiliconFlow 仅 9/72 型号可用——Auto 只能当「尽力而为的免费层」，别承诺强 SLA。③额度查询用 `GET /api/papyrus/llm/quota`（返回 `auto.monthly_*`），圆环可以据此填真实百分比。 |
| 2026-09-27 | **「PM 端点」= 胖猫（pmcat）网关——铭荼可直接用它当免费层模型源**：站点 `NEW_API_BASE_URL` 是 `https://xn--wnup5g6so4wn.de5.net`（punycode 解码=「我是胖猫」），与用户记的 `https://pmcat.top` **同一网关**（同 Key 请求两边 `/v1/models` 列表完全一致）；站内 `NEW_API_KEYS` 存的正是用户给的那三把 Key，即站内 "New API Auto" 走的就是胖猫。**铭荼侧接入：OpenAI 兼容，base URL `https://pmcat.top/v1`，用对应分组的 Key**——实测：Key A(default 组) 出 `gpt-5.4-nano`/`gpt-5.4-mini`/`gpt-5.6-luna`；Key C(Free 组) 出 `nvidia/nemotron-3-super-120b-a12b`/`z-ai/glm-5.3-flash`/`moonshotai/kimi-k3`；Key B(default/distributor) 实测模型无通道。注意 `/v1/models` 能列出不等于能调用（`deepseek/deepseek-v4-flash`、`claude-sonnet-4-5` 三把 Key 全 503 model_not_found）。 |
| 2026-09-27 | **安全**：这三把 Key 已出现在聊天记录里，等同暴露 —— 建议在胖猫后台轮换；并且**不要把 Key 硬编码进铭荼安装包**（客户端可反编译）。若要让铭荼用户共享免费额度，优先做服务端代理（铭荼自己持 Key、按铭荼用户计额度）；若必须随包分发，做成用户可填的设置项并接受额度被刷风险。站点侧完整证据与矩阵见 `scallion` 仓库 `docs/MING_TEA_INTEGRATION_2026-09-27.md` §11。 |
| 2026-09-27 | **铭荼的免费模型层已上线（站点转发，Key 不外泄）**：站点新增 **软件专用 Auto**，铭荼直接按 OpenAI 兼容接入——**Base URL `https://sca-hub.cn/api/dev-api/software-auto/v1`**，**鉴权用设备授权换来的站点 JWT**（`Authorization: Bearer <JWT>`，不用胖猫 Key，Key 只在服务器 env）。`POST /chat/completions`（支持 stream）、`GET /models`（含每个模型健康状态与实测延迟）、`GET /usage`（额度）。响应头 `X-Scallion-Resolved-Model` / `X-Scallion-Software-Auto-Attempts` / `X-Scallion-Software-Auto-Remaining`。**额度：免费层 100 次/月**（付费套餐沿用其 Auto 月额度），超额 429 `software_auto_quota_exhausted`。 |
| 2026-09-27 | **转发层的选路与故障切换（实测）**：候选池 10 个模型（`gpt-5.6-luna`、`gpt-5.6-sol`、`nvidia/nemotron-3-ultra-550b-a55b`、`nvidia/nemotron-3-super-120b-a12b`、`claude-haiku-4-5`、`gpt-5.4-mini`、`z-ai/glm-5.3-flash`、`moonshotai/kimi-k3`、`agnes-2.5-flash`、`gpt-5.4-nano`；**Mistral 全族已按用户要求排除**）。健康表存 Redis（TTL 15 分钟），**可用且最快的优先**，失败自动切下一个（单次最多试 4 个）；实测强制首选失败时：`claude-sonnet-4-5`（两把 Key 都失败）→ 自动切到 `gpt-5.6-luna` 成功返回，共 3 次尝试。当次探活 10/10 可用（延迟 1.2–24s）。注意：给 reasoning 模型 `max_tokens` 太小会返回空内容（思考把预算吃光），客户端给常规预算即可。 |
| 2026-09-27 | **铭荼的 OTA 更新与下载通道已就绪（站点侧）**：清单 endpoint **`https://sca-hub.cn/downloads/ming-tea/latest.json`**，安装包放同目录即为 `https://sca-hub.cn/downloads/ming-tea/<文件名>`（也可让清单里的 `url` 指向 GitHub Release）。服务端目录 `/srv/scallion/downloads/ming-tea/` 在 release 之外，**发布新版本不会丢清单**；场内已放 `README.md` 与 `latest.json.example`（Tauri updater 结构：`version/notes/pub_date/platforms{<target>:{signature,url}}`）。**铭荼侧要做的**：Tauri 构建出安装包与 `.sig` 后，用模板写 `latest.json`——`version` 必须等于客户端 `tauri.conf.json` 的版本、`signature` 必须是本次构建的真实签名，否则更新会被忽略/拒绝；服务端不替你们生成签名，只提供通道。 |
| 2026-09-27 | 站点侧顺带清理与修复：删除了 Papyrus 的陈旧应用包（0.1.1 安装包/portable/WPS 插件，先备份到 `/srv/scallion/backups/papyrus-package-cleanup-*`），Papyrus 的 1.1.1 动态接口保持不动（现存客户端不受影响）；并修掉了 `public/downloads` 目录权限导致的 **403**（`750 root:scallion` → `755`），否则所有静态下载链接（包括铭荼的）都会 403。若要了解「Papyrus 动态接口是否也切给铭荼」的三条方案，见站点仓库 `docs/MING_TEA_INTEGRATION_2026-09-27.md` §13.4。 |
| 2026-09-27 | **Papyrus 正式退役，发行通道已切给铭荼（站点侧实测通过）**：①铭荼的更新 endpoint = **`https://sca-hub.cn/api/ming-tea/update`**（等价静态地址 `https://sca-hub.cn/downloads/ming-tea/latest.json`，同一份清单），按平台安装包跳转 = `https://sca-hub.cn/api/ming-tea/download/latest?platform=<windows\|darwin\|linux>&arch=<x64\|arm64>`。现在还没有安装包，所以这两个接口返回 `404 ming_tea_release_not_published`——**把安装包与 `.sig` 放进 `/srv/scallion/downloads/ming-tea/` 并照该目录 `README.md` 写 `latest.json` 即自动生效，不需要改代码或重启**。②Papyrus 的四个发行接口（`/api/papyrus/update`、`download/latest`、`wps/*`）现在**一律 410 `papyrus_retired`**，且不会再指向铭荼（两个产品不兼容，铭荼不能被当成 Papyrus 的升级目标）；Papyrus 的静态清单已下掉。 |
| 2026-09-27 | **注意：Papyrus 退役只涉及「应用发行通道」**——`/api/papyrus/auth/*`（设备授权登录，铭荼换 JWT 用）与 `/api/papyrus/llm/*`（模型与额度）**继续可用**（实测：device 200、models 未登录 401）。铭荼的免费模型层仍按 §12 用 `https://sca-hub.cn/api/dev-api/software-auto/v1`（登录后 100 次/月）；如果后续要把 auth/llm 也改名到 `/api/ming-tea/*`，那是一次会破坏在用客户端的重命名，需要先约定再动。 |
| 2026-09-27 | **铭荼的付费层模型已上线（站点侧）**：三档 **Plus ¥19.9/月 → 官方额度 ¥120**、**Pro ¥39.9 → ¥280**、**Ultra ¥129 → ¥900**（「官方额度就是实际可用的额度」，按上游成本计量、不加倍率）。铭荼**不需要区分免费/付费**，仍是同一组 URL `https://sca-hub.cn/api/dev-api/software-auto/v1`：付费用户 `GET /models` 会返回付费上游的模型与价目表，`GET /usage` 返回 `{budget_rmb, used_rmb, remaining_rmb, used_percent}`，`POST /chat/completions` 按人民币额度限流（超额 `429 software_tier_quota_exhausted`，模型不在白名单 `400 paid_model_not_allowed`），响应头多出 `X-Scallion-Tier` / `X-Scallion-Cost-Rmb` / `X-Scallion-Remaining-Rmb`。计量优先用上游回报的实际成本（含缓存命中价），公开价目表见站点仓库 `docs/MING_TEA_INTEGRATION_2026-09-27.md` §15。 |
| 2026-09-27 | **付费上游现状（重要）**：付费上游（MaxAPI / `ai.max66.xyz`）用当前这把 key **只能调 3 个模型**：`deepseek-v4.1-flash`、`mimo-v2.6-flash`、`step-3.7-flash`（实测 19 个 GLM/Kimi/Qwen/Claude/GPT 型号全部 404，错误原文 `not supported by any configured account in this group`；该网关是按「分组」控制模型可见性的聚合网关，全量目录在登录后台里）。**GLM/Kimi 目前只在免费层的胖猫（pmcat）上游**（`z-ai/glm-5.3-flash`、`moonshotai/kimi-k3` 实测可用）。若付费层也要 GLM/Kimi，需要运营方在后台确认分组并换 key；代码侧只需在 `config/paidModelPrices.js` 加行（Kimi 官方价已取得：`kimi-k3` 缓存命中 ¥2/M、输入 ¥20/M、输出 ¥100/M）。 |
| 2026-09-27 | **付费层上游更正（登录网关后台后查清）**：付费分组里其实有 **8 个模型**，不是 3 个。后台 `/keys` 显示账号只有一把 key（我们用的这把），分组 `国模不降质0.1x ds4.1 glm k3都有`；`/model-plaza` 列出 8 个模型与「官方价格/折后实付」两列；`/usage` 证明近 24h 真实调用过 `glm-5.3`(14)、`glm-5.2`(2)、`kimi-k3`(4)。**当前只有 `deepseek-v4.1-flash`、`step-3.7-flash`、`mimo-v2.6-flash` 可路由**，`glm-5.3`/`glm-5.2`/`kimi-k3`/`deepseek-v4-pro`/`deepseek-v4-flash` 调用返回 `not supported by any configured account in this group`（= 分组里有、但此刻无可用渠道，网关侧问题，渠道恢复后无需改代码）。公开价目表（8 行，含缓存命中价）已在站点仓库 `config/paidModelPrices.js` 与 `docs/MING_TEA_INTEGRATION_2026-09-27.md` §16。 |
| 2026-09-27 | **付费层经济模型（实测）**：网关后台把「标准价」与「实际价」分列，比值**正好 10.0**，即该分组按**官方价的一折**结算；而 API 回报的 `usage.cost` 是**标准价（官方价）**。所以对铭荼用户的计量按官方价（=「官方额度就是实际可用的额度」），站点实际支出约为用户消耗的 1/10。铭荼侧无需感知这一层。 |
| 2026-09-27 | ZCode 应用商店落地 + 场景预加载失败修复：①`dsh-plugin-shop@0.8.3` 装入并浏览器实测可用（12225 个插件、分类/搜索/安装按钮齐备），目录源钉死在 profile patch，typert codec 形态偏移用 `scripts/patch_shop_typert_compat.mjs` 等价改写（48 处，幂等、可 revert），并接入安装脚本。②修复上一轮引入的**真回归**：把浏览器/电脑操作四行平铺进 preset 导致三个场景全部「加载失败」（原因文本实测：`Preset services require isolate realms: browserUse, computerUse` / `service "browserUse" has been registered`），改为 `cordis:group` + `isolate` 后三场景恢复正常。③办公/开发/学习三场景因此真正具备浏览器自动化与电脑操作，且桌面/浏览器自动化工具全部经人工审批（`allowed-once`，无通道即拒绝；工具名前缀与真实值核对一致）。④测试 37 项（vitest）通过。 |
| 2026-09-27 | **铭荼的产品页与套餐页已上线（站点侧）**：①**产品介绍页 `/ming-tea`**（新页面，骨架沿用已退役的 Papyrus 页）：定位、能力六宫格、额度区（Free + Plus/Pro/Ultra）、**付费模型价目表**（含「暂不可用」标注）、登录授权说明；下载按钮按发布状态自动切换，当前显示「安装包即将发布」。②**套餐页 `/recharge`** 的套餐区已从旧 Papyrus 四档换成**铭荼三档**（价格 / 官方额度 / 扣费方式 / 重置日 / 激活方式），保留兑换码与点数包；③`/papyrus` 现在 302 到 `/ming-tea`；导航新增「铭荼」入口。前端不改代码即可改价：档位与价目分别在站点 `config/softwareTiers.js` 与 `config/paidModelPrices.js`，页面读公开接口 **`GET /api/ming-tea/tiers`**（无需登录，返回三档 + 免费层 + 8 个模型价目 + 发布状态）。 |
| 2026-09-27 | 套餐购买链路（站点侧）：套餐卡默认无外链，点击会滚动到兑换码输入框；**建好爱发电商品后**在 `config/softwareTiers.js` 对应档位填 `purchaseUrl` 即可变成「购买 X」按钮（无需改前端）。发码用 `activation_codes.entitlement_type = plus/pro/ultra`，用户兑换后权益写入 `user_package_entitlements`，付费层立即生效。铭荼客户端无需感知套餐细节，`/api/dev-api/software-auto/v1` 会按账号档位自动分流。 |
| 2026-09-27 | **页面上的「暂不可用」是真状态，且已改成实时探测**：付费层 8 个模型此刻只有 `deepseek-v4.1-flash`、`step-3.7-flash`、`mimo-v2.6-flash` 有可用渠道；`kimi-k3`、`glm-5.3`、`glm-5.2`、`deepseek-v4-pro`、`deepseek-v4-flash` 调用返回 `Model "..." is not supported by any configured account in this group`（网关按「分组+渠道」控制可路由性，属网关侧）。原先「不可用」是写死的静态标注（渠道恢复也不会变），**现已改为实时探测**：结果缓存 Redis（TTL 15 分钟），过期时后台自动重刷，`/api/ming-tea/tiers` 与客户端 `/models` 都返回 `available` / `probed` / `checked_at` / `latency_ms` / `last_error`，所以渠道恢复后最迟 15 分钟内自动变回「可用」，无需改代码或重启。 |
| 2026-09-27 | ZCode 界面四项 + 自动化点击实测：①侧栏页脚按参考图重排为「头像+名称 / 剩余用量圆环 / 手机 / 设置」（order 排序，官方设置在兄弟容器里）；②修复收起态——轨道 36px 下账户按钮被裁一半、官方设置被压成 0 宽消失，改为竖排定宽四图标（实测四项都在轨道内）；③商店加安装前二次确认（捕获阶段拦截，展示许可/仓库/校验值，取消默认聚焦）、整店美化（卡片/按钮/chip/搜索/开关套铭荼语言）、默认隐藏 1103 个不兼容条目（本地补丁，可 revert）；④输入框旁新增「上下文占用」指示器（接官方 contextPressure 投影，无数据时显示「—」）；⑤自动驾驶实测：用与插件相同的命令行直驱 Playwright MCP server，走通「列工具→开页面→快照→点击→读回输入框→截图」，证明本机自动化后端可用（整链仍需模型凭据）。测试 37 项通过。 |
| 2026-09-27 | **站点侧下达三项接入任务（用户要求）：配置用户登录、接入站内 AI、接入 OTA 更新。** 三者的端点契约、自测命令与注意事项见文末「给铭荼的接入任务」一节；站点侧已全部就绪并实测通过，铭荼这边可直接开工。 |

---

## 给铭荼的接入任务（2026-09-27 站点侧下达）

站点侧已经全部上线并实测通过；下面每一项都给了可直接复制的命令与字段。**改动前先按仓库规则跑测试与构建门禁。**

### 任务 1｜配置用户登录（设备授权，拿站点 JWT）

铭荼不持有任何上游密钥：用设备授权把「浏览器里已登录的 Scallion 账号」换成客户端自己的调用凭证。

```bash
# ① 申请设备码（免登录）
curl -sX POST https://sca-hub.cn/api/papyrus/auth/device
# → {"deviceCode":"...","userCode":"XXXXXX","verificationUrl":"https://sca-hub.cn/papyrus/authorize?device=<deviceCode>","expiresIn":600,"interval":3}

# ② 让用户在浏览器打开 verificationUrl 并点「同意授权」
#    授权页已上线（铭荼品牌化、薄荷色）；通用别名：https://sca-hub.cn/device/authorize?device=<deviceCode>&client=ming-tea

# ③ 客户端按 interval 轮询
curl -s https://sca-hub.cn/api/papyrus/auth/device/<deviceCode>
# → {"status":"pending"} / {"status":"approved","token":"<JWT>","user":{...}} / {"status":"expired"}
```

要点：`approved` 的那次响应**同时返回 token 与 user，且该 device 记录立即删除**（一次性，取走即失效）；token 是站点 JWT，**7 天有效**；`expiresIn` 600 秒内未完成授权就要重新申请。客户端应把 token 存在本地安全位置（不要打进日志），过期后用同样的流程重新登录。

### 任务 2｜接入本站 AI（免费层与付费层是同一组 URL）

**Base URL：`https://sca-hub.cn/api/dev-api/software-auto/v1`**
**鉴权：`Authorization: Bearer <上一步拿到的站点 JWT>`**（未登录返回 401）

| 端点 | 用途 |
| --- | --- |
| `POST /chat/completions` | OpenAI 兼容（支持 `stream: true`）；`model` 可留空/传 `auto` |
| `GET /models` | 当前账号可用模型（免费层给 Auto 池与实测延迟；付费层给付费模型与可用性） |
| `GET /usage` | 额度：免费层 `{monthly_limit, monthly_used, monthly_remaining}`；付费层 `{budget_rmb, used_rmb, remaining_rmb, used_percent}` |

- **免费层**：站内 Auto（胖猫池，10 个模型自动选路、掉线自动切换），**每月 100 次**；
- **付费层**：账号有 `plus`/`pro`/`ultra` 权益时自动切换为付费模型，按「**实际可用额度**」（人民币）计，扣完即止（429 `software_tier_quota_exhausted`）；
- 响应头：`X-Scallion-Resolved-Model`（实际用的模型）、`X-Scallion-Software-Auto-Attempts`（免费层尝试次数）、`X-Scallion-Cost-Rmb` / `X-Scallion-Remaining-Rmb`（付费层）；
- **建议**：把 `/usage` 的百分比喂给「剩余用量」圆环，把 `X-Scallion-Resolved-Model` 显示在回复旁边（可选）。

```bash
TOKEN=<上面的 JWT>
curl -s https://sca-hub.cn/api/dev-api/software-auto/v1/usage -H "Authorization: Bearer $TOKEN"
curl -s https://sca-hub.cn/api/dev-api/software-auto/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

### 任务 3｜接入 OTA 更新

- **Tauri updater endpoint 直接填**：`https://sca-hub.cn/api/ming-tea/update`（等价静态地址 `https://sca-hub.cn/downloads/ming-tea/latest.json`，同一份清单）；
- 按平台跳转安装包：`https://sca-hub.cn/api/ming-tea/download/latest?platform=<windows|darwin|linux>&arch=<x64|arm64>`；
- **当前返回 `404 ming_tea_release_not_published`**（还没有安装包）——客户端应把它当作「暂无更新」静默处理，不要报错弹窗；
- 发布方式：把安装包与 `.sig` 放进 `/srv/scallion/downloads/ming-tea/`，照该目录 `README.md` 写 `latest.json`（`version` 必须等于客户端 `tauri.conf.json` 的版本；`signature` 必须是本次构建的真实签名），**放好即生效，不需要改代码或重启**。


### ⚠️ 站点侧阻塞（2026-09-27 晚，铭荼侧实测发现）

**Plus 开通后整条 `software-auto` 链路 500**：`/usage`、`/models`、`/chat/completions` 全部返回
`{"error":{"message":"softwareTierAutoQuota is not defined","type":"dev_api_error"}}`。
免费档时同一条链路正常（实测 200 + 真实内容），所以与档位分支相关。

铭荼侧的应对（已完成，与站点修复解耦）：

1. **不误登出**：只有 401/403 判定登录过期并清凭证；5xx 归类为 `hub/site-error`，保持登录态；
2. **不破坏配置**：模型同步失败时保留上一次的模型列表，不会把路由写成空；
3. **错误说人话**：宿主 RPC 改为「端点内部兜住错误、永远返回 `ok:true` + value」（否则 5xx 会走 connection 的失败信封，
   用户只能看到 `invalid server-response failure`）；界面文案例如
   「站点暂时不可用（HTTP 500），稍后会自动重试；这不影响你的登录状态。」
4. 圆环回落成 `—` 并给可操作提示，不编造数值。

诊断报告（可直接交给站点侧）：[`docs/ming-tea-site-500-report.md`](ming-tea-site-500-report.md)。
**站点修好前，档位徽标、付费层额度、Auto 赠送次数都无法实测**（字段解析与两层展示已按新文档实现并通过离线断言）。

### 2026-09-27 ZCode 强化「开发模式」（对标 Codex / Claude Code）

**先给出盘查结论（决定了这轮怎么做）**：

1. **我们叫「开发模式」的场景，此前并不是官方的开发场景。** `build-presets.mjs` 把
   `standard.patch.yml` 写死成所有场景的来源、只换 persona，于是 id 为 `ptc` 的开发场景
   拿到的是 standard 的行表：`workflow-ptc`/`tool-workflow` 开着（官方 ptc 故意关掉），
   又缺官方 ptc 的 `tool-presentation`（`mode: ptc`）。名字叫开发、能力对不上。
2. **编程能力最大的缺口是「编辑」这一步**：`@deepseek-ai/dsh-tool-str-replace-editor`
   （`str_replace_editor`：查看/创建/**字面量精确替换**/按行插入）**已随 runtime 安装，
   但官方没有任何预设挂载它**（孤儿包）。我们此前只有 `tool-fs` 的 read/write/edit，
   其中 write 是整文件重写 —— 费 token 且易误改。这正是 Codex 用 `apply_patch`、
   Claude Code 用精确字串 `Edit` 的那一层。
3. **DSH 里没有 LSP / git / test-runner 工具行**（grep 283 个包 0 命中）。diff 视图 / 文件树 /
   终端面板**已经存在且已挂载**（`dsh-client-ui-deliverables`、`dsh-client-ui-sidebar-files`、
   `dsh-client-ui-sidebar-terminal`），**不在** `dsh-codex-ui` 里（那个插件对 `diff`/`hunk`
   是 0 命中，只做项目/会话组织与设置外壳）。所以提升编程能力只能靠"工具行 + 人格纪律 + 委派"。

**已落地**：

- 每场景新增 `sourcePreset`：办公→`standard`、开发→**`ptc`**、学习→`standard`。
  生成器改为按场景读对应官方预设。**学习场景刻意不用官方 minimal** —— 实测它只挂
  `persona` + `persistent-shell` 两行（官方那是极简测试场景），派生出来只剩 1 个工具行。
- 开发场景能力（`rowToggles`，仅此场景）：`tool-subagent-codex`、`tool-ralph` 两个官方默认关的行**打开**
  （**不开** `tool-subagent-claude-code`：DSH 自带 subagent/subagent_fork，外部 CLI 后端是重复能力，
  且本机未装 claude，开了只会让模型挑到必然失败的工具）；新增官方孤儿包 `str-replace-editor`
  （`extraRows`）；`tool-presentation`（PTC 呈现）随 ptc 来源一并获得。
- 开发人格重写为一套**编程纪律**（逐条对标 Codex 的 AGENTS.md 与 Claude Code 官方最佳实践）：
  先读再写、精确编辑优先、非平凡任务先计划、**声明完成前必须跑测试/构建并把命令与输出作为证据**、
  修根因不打补丁、多步任务维护待办、只改相关代码、大范围检索可交 subagent。
  刻意**没有**照搬 Claude Code 的 `/clear` 与 Stop hook（DSH 没有这些机制）。

**两个必须如实知道的边界（都实测过）**：

- **ralph 与「关掉工作流」不可兼得**：`tool-ralph` 硬注入 `workflowEngine`，该服务由
  `workflow-ptc` 提供。第一次打开 ralph 时开发场景直接**加载失败**，诊断原文
  `tool-ralph (@deepseek-ai/dsh-tool-ralph): waiting for workflowEngine`。因为 ralph 是明确要的，
  所以 `workflow-ptc`/`tool-workflow` 一并开启 —— 这是**与官方 ptc 的有意分歧**，代价是多一个 workflow 工具。
  另外上游自己注明：ralph 的完成与否是**工人自报**、不是独立评估。
- **委派后端：本机 CLI 装了，但 DSH 侧没有对应 provider**（2026-10-01 更正）。本机 `codex` ✅ 已安装（`~/.local/bin/codex`）、`claude` ❌ 未安装；
  但 0.2 的 runtime 里**只注册了 `spawn` 与 `fork` 两个 subagent provider**（`dsh-subagent-spawn-in-process` 的 `providerName` 默认 `spawn`、`-fork-in-process` 默认 `fork`），
  **没有任何包注册名为 `codex` 的 provider**。`dsh-tool-subagent` 在 provider 未注册时只打一行 info、不注册工具（`lib/index.js:573-577`），
  所以开发场景里 `disabled: false` 的 `tool-subagent-codex` 行**不会产生 `subagent_codex` 工具**（0.1.7 与 0.2 同样如此）。
  `subagent_claude_code` 保持关闭。→ 上一轮文档写的「实测可用」是错的，已按事实更正；要不要另找一个注册 `codex` provider 的包属新产品决策。

**判定陷阱（这轮踩到，已写成可复用校验脚本）**：`--dump-config` 里 preset 内子行的
`disabled` 判定必须**按缩进**做：`- id:` 的横线缩进是 N，同行键的缩进是 N+2，
用「首行/固定缩进」的正则会全部判错（先后误判过两次）。校验脚本：`/tmp/verify_presets.py` 的逻辑，
本轮用它确认「生成文件 == 运行时 dump」逐行一致。

**验收证据**：三场景 `broken: false`、诊断为空；开发场景 dump 显示
`tool-subagent-codex=false, tool-subagent-claude-code=true, tool-ralph=false, workflow-ptc=false,
tool-workflow=false, tool-presentation=启用`，且 `str-replace-editor` 在组合树内；
`vitest` 37 项、`check_ming_tea_hub.mjs` 38 项通过；锁文件 22 条（新增该包，带 sha512）。

> 2026-09-30 复验并更正两处：`tool-subagent-claude-code` 原记作 `false`，**实测是 `true`（保持关闭）**；
> `check_ming_tea_hub.mjs` 已从 36 项增至 **38 项**。复验命令（均只读）：
> `DSH_HOME=<repo>/.ming-tea/runtime/dsh-home .ming-tea/runtime/node_modules/.bin/dsh --profile ming-tea --dump-config`
> 组合成功（退出码 0）；`python3 scripts/verify_presets.py <dump>` 输出上述行状态；
> `node scripts/check_ming_tea_hub.mjs` → 38 项通过；`platform/ming-tea` 下 `vitest run` → 37 项通过；
> `node scripts/build-presets.mjs` 重新生成后与仓库内 `presets/scenes.patch.yml` **逐字节一致**。

### 2026-09-27 站点修复后的收尾（付费档全链路实测）

站点侧已修复 `softwareTierAutoQuota is not defined`（根因是 import 漏了，另有两处：响应头写非 ASCII 触发
`ERR_INVALID_CHAR`、`/models` 里 auto 重复）。修复后铭荼侧逐条收尾，并**按站点新形态改了四处**：

1. **付费档字段形态与早期文档不同**（按实测实现）：付费档 `plan` 是 `null`，档位信息在 **`tier` 对象**里
   （`key/name/monthly_budget_rmb/auto_free_calls/concurrency/entitlement_type`）；顶层另有 `tier_name`。
   已归一：`plan` 与 `tier` 都认，并解析 `concurrency` / `autoFreeCalls` / `monthlyBudgetRmb`。
2. **`available_tiers` 在付费档不再返回** → 免费档的「可升级」那一行改为优先读**站点公开的档位目录**
   `GET /api/ming-tea/tiers`（免登录、字段最全：价格/额度/并发/Auto 赠送 + `context_catalog`）。
   实测该端点返回 Free 100 次/并发 2/上下文 256K，以及 Plus/Pro/Ultra 三档完整权益。
3. **修掉一个真 bug（付费档看不到本档权益）**：此前那行只在免费档渲染，
   付费档因为 `available_tiers` 为空而**整行为空**。现在付费档显示「本档权益：含 Auto 赠送 200 次/月 · 并发 5 · 额度 ¥120/月」。
4. **`auto_free.configured`**：站点用它标记「本档是否配了 Auto 赠送」，`false` 时不显示该行（免费档就是 false）。
5. **上下文目录**：`/usage` 的 `catalog` 带每个模型的 `context_window`（`256K`/`1M` 这种字符串），
   已解析成数字备用（`contextOf()`），实测 auto=256000、deepseek-v4.1-flash=1000000。

**如实标注两件站点侧不一致（已记录，未在铭荼侧伪造）**：

- **`auth` 的 `user` 对象不反映 Plus**：`is_member:false`、`member_expires_at:null`、`member_type:none`，
  而 `/usage` 正确给 `tier:Plus / budget_rmb:120`。因此**徽标与额度一律以 `/usage` 为准**；
  徽标悬浮的到期时间取不到时如实显示「（站点未返回到期时间）」，不编造。
- 建议站点侧确认：会员字段是否应随 Plus 更新、到期时间是否有对外端点、`/api/ming-tea/tiers` 是否长期公开。

**验收（浏览器实测，账号 id 112 / Plus 生效中）**：页脚圆环 100%、`PLUS` 徽标、账户面板与悬浮卡字段如上；
离线自测扩到 **38 项全通过**（新增付费档 `tier` 形态、`catalog` 上下文解析、`configured:false` 三条），vitest 37 项通过。

### ⚠️ 站点侧再次全线 500（2026-09-27 晚，内部服务 13306 连不上）

上一轮 `softwareTierAutoQuota` 修好、付费档实测通过后，**又出现一次故障**，错误变为
`connect ECONNREFUSED 127.0.0.1:13306`：`/usage`、`/models`、`/chat/completions`（auto 与付费模型都走它）、
`/api/papyrus/llm/quota` 全部 500；同一时刻**静态端点正常**（`/api/ming-tea/tiers` 200、
`/api/ming-tea/update` 404、授权页 200）⇒ 判定为**站点后端连不上自己的内部服务**，与客户端无关。

铭荼侧容错实测按设计生效：登录态未被误清（`signedIn:true`）、模型同步失败**保留了现有配置**
（`agent-default-model` 仍是 `deepseek-v4.1-flash`）、额度回落 `—` 并给出可操作文案。

**本轮同时发现一处我们自己的小问题（已修）**：模型同步失败后没有任何重试，只能等下一次开页面。
已加**有限退避重试**（约 5s / 30s / 2min，最多 3 次，站点恢复后自动补齐模型列表与额度）。
详见 `docs/ming-tea-site-500-report.md` 的追加章节。

### 2026-09-27 官方调研：DSH 有第一方桌面客户端，且「更新」只有一条发布流

用户提到「DSH 出了官方客户端」。查证结论（来源：`github.com/deepseek-ai/deepseek-harness` 仓库与
其 `.agents/notes/implemented/architecture/` 下的官方设计笔记，均为第一方文档）：

- **官方有桌面客户端**：`apps/desktop` → 包名 `@deepseek-ai/dsh-desktop`，是 **Electron 壳**，
  复用同一套 Web UI；**npm 上没有单独发布**（我查过 `@deepseek-ai/dsh-desktop` / `dsh-app` / `dsh-client` 都不存在），
  只能从源码构建（`make desktop` / `pnpm package:desktop:mac:arm64` 等）。
- **它自带内置 Node 与 pnpm**，所以干净离线机器不需要系统 Node/pnpm；用内置 pnpm 管理桌面 profile 的外部插件。
- **一个 Desktop 发布号 = Electron 产物 + 精确的 `@deepseek-ai/dsh` 版本 + `@deepseek-ai/dsh-desktop-host` 版本**。
  官方原文：*「不存在独立 dsh manifest、兼容范围或仅更新 dsh 的操作」*，且*「即使壳代码没有变化，
  更新 dsh 也必须产生新的 Electron 发布」*。
- **更新机制**：只有一条 `electron-updater` 发布流 + 签名的 `electron-builder` 产物；版本号即 Desktop 发布版本。
- **profile 隔离**：Desktop 独占保留 profile `~/.dsh/profiles/desktop`，与 npm 安装的 CLI profile
  严格分开（不共享可执行包、lockfile、node_modules、插件激活状态）。两者共享 `.dsh` 数据根
  （会话/设置/凭据/工作区）。
- 渲染进程 `nodeIntegration:false` + `contextIsolation:true` + `sandbox:true`；发布要求签名，macOS 还要求公证。

**对我们 OTA 决策的影响**（用户问的 (a)/(b)）：官方对同类问题的答案是 **(b) 一起发布**，理由写得很直白 ——
GUI 协议把 Web 客户端与后端版本绑在一起，独立定版本会产生「未经验证的壳/客户端/后端/插件组合」，
而且无法判断更新是否可用。**我们目前的形态更危险**：Tauri 壳、DSH runtime（`0.1.7-rc.1` 锁定）、
我们自己的插件层（`@ming-tea/dsh-ui`）三者独立，且 runtime 版本还与上游可用版本脱节
（上游已有 `0.2.0-rc.2`）。详见下节建议。

### 自测清单（2026-09-27 ZCode 执行，逐条记录）

| # | 项目 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | 设备授权全流程 → 拿到 token → 成功调一次 `/chat/completions` | `done` | **全流程实测通过**：品牌化别名申请设备码 → 系统浏览器打开授权页（站点对已登录账号自动授权）→ 宿主轮询拿到一次性 token → 落库到 `.credentials.yaml`（0600）→ 用该 token 真调一次 `/chat/completions`：**HTTP 200，实际路由到 `agnes-2.5-flash`，1 次尝试，返回真实内容（42 字）**，`usage` 正常上报。此后用户自己的会话也跑通（界面模型显示「铭荼 Auto」）。 |
| 2 | `/usage` 取到额度、界面圆环显示真实数值 | `done` | **付费档实测通过（站点修复后）**：`/usage` 200 → `mode:paid`、`tier:Plus`、`budget_rmb:120`、`auto_free:{limit:200,used:1,remaining:199,configured:true}`；界面实测页脚圆环 **100%**、用户名旁 **PLUS 徽标**、账户面板显示「带带葱铭 / 6 天后到期 · Auto 赠送 199/200 次 · 付费层 ¥120.00 / 档位：Plus / 本档权益：含 Auto 赠送 200 次/月 · 并发 5 · 额度 ¥120/月」；悬浮卡同时列「Auto 赠送 199/200 次」与「付费层：剩余 ¥120.00，已用 ¥0.00 / 额度 ¥120」。 |
| 3 | 「检查更新」在未发布状态下静默提示「已是最新」 | `done` | 浏览器实测：`update.check` → `{status:"latest", reason:"unpublished", current:"0.1.0", message:"已是最新（0.1.0）"}`；404 + `ming_tea_release_not_published` 被当作正常返回，不弹错。版本号取自 `apps/desktop/src-tauri/tauri.conf.json`（实测读到 `0.1.0`）。 |
| 4 | token 过期后能自动重新发起设备授权 | `in-progress` | 机制已就位且有实测与离线断言：站点 401 → 宿主清除本地凭证并让界面转为「登录已过期」（**假 token 实测过**）；本地记录 7 天到期时间，设备码 600 秒过期时面板提示重新开始。**完整 7 天过期与「退出→重登」往返仍未做**（会打断用户正在进行的会话，且首次登录已验证走同一套代码）；另外站点 `auth` 的 `user` 对象目前**不反映 Plus**（`is_member:false`、`member_expires_at:null`、`member_type:none`），而 `/usage` 正确给 `tier:Plus` —— 徽标与额度因此以 `/usage` 为准。 |

补充记录（同批实测）：

- **鉴权已切品牌化别名**：设备授权走 `POST/GET /api/ming-tea/auth/device[/:code]`（站点建议路径），实测 200 且 `verificationUrl` 指向品牌化授权页 `/device/authorize?…&client=ming-tea`。模型接口仍用 OpenAI 兼容的 `https://sca-hub.cn/api/dev-api/software-auto/v1`（模型提供方要的是 OpenAI 协议，这条已实测可用）；站点另有 `/api/ming-tea/llm/*` 别名，本轮未改，理由记录在此以免后来者误判为漏做。
- **付费档能力已接**：`/usage` 的 `tier.name` 与 `monthly_budget_rmb` 已解析；登录时会拉一次 `/models` 并把**可用**模型写进站点路由的 `models`（`auto` 恒在首位，`available:false` 的丢弃），因此模型选择器里能直接选到付费型号（deepseek-v4.1-flash / step-3.7-flash / mimo-v2.6-flash）。`/models` 的返回形态站点未承诺，归一化写成防御式（`data[]`、`models[]`、`models{}` 三种都认，解析不出来就只保留 `auto`）。
- **站点 `/models` 在 2026-09-27 晚些时候又变了**：现在只返回 **一个 `auto`** 条目（`{id:"auto", display_name:"Auto", available:true, pool_size:10, pool_available:8, description:"由站点在可用模型里自动选择…"}`）
  —— 池内成员不再逐个可选，选路完全交给站点（与其「掉线自动切换」的设计一致）。同时 `available_tiers` 每档多了 `auto_free_calls`（Plus 200 / Pro 300 / Ultra 400），即**付费档也含 Auto 免费次数**。
  我们的路由因此只有一个模型 `auto`；面板文案在「只有一个 auto」时改成「模型：Auto（站点自动选路，掉线自动切换）」，不再假装是清单。
- **站点接口的实测形态**（与文档描述有差异，按实测实现）：`/usage` 是**扁平**结构 `{monthly_limit, monthly_used, monthly_remaining, plan:{name:"Free",…}, available_tiers:[{name,price_rmb,monthly_budget_rmb}], mode, source, unlimited}`；
  `/models` 是 `{data:[{id, healthy, latency_ms, last_error, owned_by}], software_auto:{mode, preference, quota, available_tiers}}`。
  归一化据此调整：可用性优先级 `available` > `healthy` > `last_error` 为空；档位名三种形态都认（`tier.name` / `plan` 字符串 / `plan.name`）。
- **自测抓出的两个真 bug（都已修）**：①档位名的计算写在免费分支**之后**，而免费分支提前 return ⇒ 免费档永远拿不到档位名（断言 `tierName==="Free"` 直接失败）；
  ②页脚用户名按 `name || … || id` 取，而站点返回的是 **`username`**，结果页脚显示成「112」（实测截图发现）。
- **站点侧已确认我们的 provider 选择正确**（其 §22 更正）：`software-auto` 与 `/api/ming-tea/llm/*` **不是同一接口的两种叫法**，而是两套额度体系——前者 OpenAI 兼容、拿 Plus/Pro/Ultra 的人民币额度；
  后者是 Papyrus 兼容别名、走 Papyrus 套餐+积分。我们保持在 `software-auto`，不写进 provider 配置的做法**应保持**。
- **宿主 settings 写入通路可用且热生效**：用「幂等空写」（把 `agent-default-model` 写回它当前的值）验证了 `settings.mutate` 的 volatile 校验与 configEditor 写入，浏览器实测返回 `{ok:true}` 且写前写后一致 —— 这意味着登录后**不需要重启**就能挂上站点路由并切默认模型。
| 2026-09-27 | ZCode 按文档《给铭荼的接入任务》实现站点接入的铭荼侧：①**宿主半区新增站点客户端**（`lib/host/hub-client.mjs`，纯 ESM + 注入 fetch，含设备授权状态机、额度归一化、更新清单语义、版本比较）；②**账号存储**（`account-store.mjs`）：JWT 只写 `$DSH_HOME/.credentials.yaml`（0600，凭证引用 `MING_TEA_HUB_TOKEN`），用户信息走 `grant` 记录，**token 不进浏览器、不进日志**；③**模型路由热改**（`model-route.mjs`）：经宿主 `settings` 的路径寻址 `mutate` 写入 `llm-pi-ai.providers[ming-tea-hub]` 并切默认模型，登出只在默认仍指向我们时回退——用户仍可在模型页自配第三方厂商；④**同源 RPC 通道** `/ming-tea`（`connection.rpc.handle`，DSH 自带 cookie 鉴权），端点白名单：`ping/auth.status/auth.start/auth.poll/auth.signOut/quota.get/update.check/debug.*`；⑤**界面**：账户面板（设备码大字 + 打开授权页 + 自动轮询 + 登录态/退出）、剩余用量圆环接真实数据（未登录或取不到仍显示 `—`）、设置里「检查更新」改为真实调用。实测：通路 `ping`/`debug.state` 通过、settings 幂等空写返回 `{ok:true}`、`update.check` 对未发布清单返回「已是最新（0.1.0）」；新增离线自测 `scripts/check_ming_tea_hub.mjs` **22 项全通过**；仓库门禁 vitest 37 项通过。**待用户授权一次**才能补完自测 1/2/4。 |
| 2026-09-27 | **登录体验改版 + 铭荼品牌化鉴权别名（站点侧已上线）**：①已登录用户打开授权页**会自动完成授权**——不用点按钮、不用输入验证码，页面直接显示「已授权 铭荼」；②只有一种情况需要手动确认：本次登录**不是从当前网络发起**（服务端返回 409 `confirm_required`），因为设备码免登录可申请，无条件自动授权会让攻击者用自己生成的链接骗走已登录用户的 token；③铭荼可用自己的路径：`POST /api/ming-tea/auth/device`、`GET /api/ming-tea/auth/device/:code`、`POST /api/ming-tea/auth/approve`、`GET /api/ming-tea/llm/{models,quota}`、`POST /api/ming-tea/llm/chat`，verificationUrl 会返回 `https://sca-hub.cn/device/authorize?device=…&client=ming-tea`；旧 `/api/papyrus/*` 与 `/papyrus/authorize` 仍可用（老客户端不受影响）。铭荼侧建议改用 `/api/ming-tea/auth/*`，并在授权页返回后继续按 interval 轮询取 token。 |
| 2026-09-27 | **最新情况（站点侧收尾盘点）**：①**登录**：已登录用户打开授权页**自动完成授权**（不点按钮、不输验证码；跨网络才需一次确认，防设备码钓鱼）；铭荼可改用品牌化别名 `POST /api/ming-tea/auth/device`（verificationUrl 会指向 `/device/authorize?…&client=ming-tea`），旧 `/api/papyrus/*` 仍可用。②**站内 AI / OTA / 剩余用量**：站点侧就绪且已被铭荼接入（见上一行）；客户端传 `model:"auto"` 现在**付费层也支持**了——站点会挑默认模型并在上游掉线时自动切换（最多 3 个候选），显式指定模型则只调那一个。③**付费层（Plus/Pro/Ultra）之前确实接不通，根因在站点侧三处断点，均已修复**：客户端传 `auto` 被付费分支拒绝（400）、卡密商品白名单没有这三个档位（发不出码）、兑换流程硬要求积分>0（而套餐只给额度不给积分）。现在链路闭环：发码 → 兑换 → 权益 → 付费分流。④已生成 1 个 Plus 档验收测试码 `SC-1B4A-KCIU-GLYS`（31 天、0 积分），登录后在 `/recharge` 兑换即可验证付费层。⑤铭荼侧可选补：账户面板显示档位（`/usage` 付费档返回 `tier.name` 与 `monthly_budget_rmb`）、付费模型选择器（3 个可用：deepseek-v4.1-flash / step-3.7-flash / mimo-v2.6-flash；`/models` 带 `available` 标记）。 |
| 2026-09-27 | ZCode 按站点侧更新再改一版：①**鉴权切品牌化别名** `/api/ming-tea/auth/*`（推荐路径，实测 200；授权页现在是 `/device/authorize?…&client=ming-tea`，已登录账号**打开即自动完成授权**、跨网络才需确认一次；面板文案同步说明）；②**档位显示**：解析 `/usage` 的 `tier.name` 与 `monthly_budget_rmb`，账户面板显示「档位：Plus/Pro/Ultra」；③**付费模型可选**：新增宿主端点 `models.list`，登录时拉 `/models` 并把可用模型写进站点路由（`auto` 恒在首位、不可用条目丢弃），模型选择器因此能直接选付费型号，面板也列出可用模型；④防御式解析 `/models` 的三种可能形态。离线自测扩到 **27 项全通过**（新增品牌化路径断言、档位字段、模型归一化、路由模型合并），vitest 37 项通过。 |
| 2026-09-27 | **站点侧确认：铭荼「provider base URL 保持 `software-auto`、不换 `/api/ming-tea/llm/chat`」的判断正确且应保持**。核对实现后确认二者不是同一接口的两种叫法，而是两套额度体系：`software-auto` 是 **OpenAI 兼容**（`/chat/completions` 支持 stream），免费层=站内 Auto 池 100 次/月、付费层=**Plus/Pro/Ultra 的「实际可用额度」（人民币，`budget_rmb/used_rmb/remaining_rmb`）**；而 `/api/ming-tea/llm/*` 只是 **Papyrus 兼容别名**（`/llm/models|quota|chat`，非 OpenAI 端点，额度走 **Papyrus 套餐+积分**，拿不到 Plus/Pro/Ultra 的额度）。站点侧文档曾把它列成"等价别名"，已更正并加警示（站点仓库 §22；§20.3 表格那行也改了）。**结论**：铭荼客户端只用 `https://sca-hub.cn/api/dev-api/software-auto/v1`（对话/模型/额度）+ `POST /api/ming-tea/auth/device` 系列（登录）+ `/api/ming-tea/update`（更新清单）；`/api/ming-tea/llm/*` 与 `/api/papyrus/llm/*` 保留给 Papyrus 时代客户端，不要写进 provider 配置。 |
| 2026-09-27 | ZCode 登录体验改造（用户反馈：不该让用户看授权码、应在弹窗里一步跳去授权页）：①**宿主半区新增 `open-external.mjs`**，用**系统默认浏览器**打开授权页（`open`/`xdg-open`/`cmd start`）——必须由宿主打开而不是页面 `window.open`：站点会话在用户日常浏览器里，嵌入式 webview 里多半没登录，拿不到「已登录即自动授权」这条捷径；而且 await 网络后再 window.open 会被弹窗拦截。②`auth.start` 现在**申请设备码后立即打开浏览器**并返回 `opened`；新增 `auth.open` 供「重新打开授权页」。③面板改成**一键**：未登录只有「登录铭荼账号」一个主动作，不再展示大号设备码；待授权态只显示状态、链接与**小字备用码**（`hasBigCode: false` 实测），宿主打不开浏览器时才退回页面内 `window.open` 并把链接留在面板上。实测：点一次即弹出系统浏览器（面板显示「已在系统浏览器打开授权页，等待授权…」）。离线自测扩到 **31 项全通过**（新增打开命令的平台选择、URL 参数、非 http(s) 拒绝、spawn 失败如实返回）。 |
| 2026-09-27 | ZCode 完成站点接入验收与修正：①自测 ①②**结项**（设备授权→token→真调 `/chat/completions` 200/`agnes-2.5-flash`/真实内容；额度 100/100 且页脚圆环随调用递减为 96，账户面板显示用户名/到期/档位/可用模型）；②按实测形态修正接口解析（`/usage` 扁平结构 + `plan.name`；`/models` 的 `healthy`/`latency_ms`/`last_error`），并修掉两个自测抓出的真 bug（免费档拿不到档位名——分支提前 return；页脚用户名取 `name` 而非 `username`，显示成「112」）；③离线自测扩到 **33 项全通过**；④如实标注自测 ④：401 路径与机制已验，完整 7 天过期与「退出→重登」往返未跑（避免打断用户正在进行的会话）。 |
| 2026-09-27 | **免费层模型列表改为只暴露 `auto`（站点侧已上线，铭荼侧需知悉）**：`GET /api/dev-api/software-auto/v1/models` 在免费层**只返回一个 `auto` 项**（`display_name: "Auto"`，另有 `pool_size` / `pool_available` 两个数量字段），**不再列出池内 10 个真实模型名**（此前会列出名字+健康状态+延迟，客户端的模型选择器因此把它们显示成可选模型）。公开的 `GET /api/ming-tea/tiers` 里 `free.models` 也换成了 `free.model_count`。**付费层不变**：仍列出付费模型与 `available` 标记（按官方价计费，用户本就该选具体模型）。铭荼侧要做的：①provider 继续用 `ROUTE_MODEL_ID="auto"`（正确且现在更必要——即使客户端传具体池内模型名，服务端也会覆盖为 Auto 选路）；②模型选择器里免费档只会看到「Auto」一项，不要再去枚举池内模型；③「剩余用量」继续用 `/usage`，与模型列表无关。 |
| 2026-09-27 | **付费档新增「Auto 免费调用次数」（站点侧已上线）**：Plus **200 次/月**、Pro **300 次/月**、Ultra **400 次/月**，走站内 Auto 池（该上游对站点免费，故赠送零成本）；**超出后 Auto 调用改为从「实际可用额度」（人民币）里扣，不硬封**；预算耗尽（429 `software_tier_quota_exhausted`）也不影响尚未用完的 Auto 免费次数（服务端先判 Auto 再判额度）。计次用**独立 source** `software-tier-auto`，与免费层 100 次额度、与按人民币计费的 `software_paid_usage` 互不污染。铭荼侧可用的字段：`GET /models`（付费档首位有 `auto` 项，带 `auto_free_calls` / `auto_free_remaining`，仍不列池内模型名）、`GET /usage`（`auto_free: {limit, used, remaining, exhausted}`）、响应头 `X-Scallion-Auto-Free-Remaining`，以及响应体 `software_auto.via = 'auto_free'`（用于区分这次走的是 Auto 免费次数还是付费模型）。客户端不传 `model` 或传 `"auto"` 即自动享受该额度，无需改动。 |
| 2026-09-27 | ZCode 修四处界面/功能问题（用户反馈）：①**上下文胶囊移进「模型 + 思考强度」那一栏**（trailing 的首个子元素，实测同一行：`上下文 18% | 铭荼 Auto | —●— High | ↑`）；②**会话里恢复思考强度控件**——根因是**手写路由必须显式声明 `reasoningEfforts`**：该字段缺省时只会继承「已安装目录里同 id 模型」的能力，而我们的 `auto` 不在任何目录里，于是会话里连强度控件都不出现；现声明 `{off: null, low, medium, high}`（读 pi-ai 源码确认语义：键是档位、值是上线拼写，只有 off 可留空），并实测站点接受 `reasoning_effort`（200）。因该声明只在「写路由时」生效，新增 `models.sync` 端点（登录时与每次打开页面时各同步一次，**内容一致就不写**，避免无谓改配置）；③**修好侧栏「设置」入口消失**：官方 `.dcu-settings-seat` 被相邻页脚操作区挤成 0 宽、齿轮只剩 8px（我的 `flex:1 1 auto; min-width:0` 造成的），改为定宽 32px；④**用量悬浮卡**：自绘卡片同时列「账号（档位）/ 免费层：本月剩余 x/y 次 / 付费层：剩余 ¥z（已用…）/ 额度 ¥b」，未开通时写明「开通后这里会同时显示两层剩余」；圆环 `title` 也带两层作兜底。离线自测 **34 项全通过**（新增两层额度同时存在的用例），vitest 37 项通过。 |
| 2026-09-27 | **并发 / 上下文 / Auto 超次计价（站点侧已上线）**：①**并发**（同一账号同时进行中的请求数）免费层 **2**、Plus **5**、Pro **10**、Ultra **15**；用 Redis 计数（跨两个 cluster 实例一致），**一次客户端请求 = 1 个并发位，内部换模型重试不额外占位**，超限返回 `429 {code:'concurrency_limit'}` + `Retry-After` + `X-Scallion-Concurrency-Limit`。②**上下文**：`auto` 统一 **256K**；付费模型逐个登记真实上限（`deepseek-v4.1-flash`/`kimi-k3`/`deepseek-v4-flash` 1M、`glm-5.3`/`glm-5.2`/`step-3.7-flash`/`mimo-v2.6-flash` 256K、`deepseek-v4-pro` 128K）；服务端只拦「明显超限」（超过上限 90% 才拒绝，返回 `413 {code:'context_too_long'}`），对外经 `/models` 的 `context_window`/`context_tokens` 与 `/api/ming-tea/tiers` 的 `context_catalog` 暴露。③**Auto 超出免费次数后**改为**仍走 Auto 池**、但按象征性单价从人民币额度扣（¥0.1/百万输入、¥0.2/百万输出、缓存 ¥0.01，比最便宜付费模型再低一个数量级），响应体 `software_auto.via='auto_overage'`、响应头 `X-Scallion-Cost-Rmb`/`X-Scallion-Remaining-Rmb`；只有**预算也耗尽**才 429 `software_tier_quota_exhausted`。铭荼侧动作：把 `/models` 的上下文值与 `X-Scallion-Concurrency-Limit` 用于本地提示；并发超限请按 `Retry-After` 重试而不是报错弹窗。 |
| 2026-09-27 | **上下文定稿 · GLM-5.3-Flash 补入 · 备用 key（站点侧已上线）**：①**上下文按厂商文档重填**（原先除 kimi-k3 都是估算）：DeepSeek 三个档位（v4.1-flash / v4-pro / v4-flash）与 **GLM-5.3 / GLM-5.3-Flash / GLM-5.2**、kimi-k3 **都是 1M**；`step-3.7-flash` 与 `mimo-v2.6-flash` 厂商文档查不到数字 → **标 estimate，只展示不硬拦**（由上游报错更准）；`auto` 按用户指定 256K 且强制。②**补入之前漏掉的 `glm-5.3-flash`**：价目 ¥5.6/¥19.6（缓存 ¥1.4，按网关 0.1x 折后价反推官方价 $0.80/$2.80），上下文 1M，并加入 Auto 兜底的付费候选顺序。③**付费上游支持备用 key**：`PAID_MODEL_API_KEY_BACKUP`（0.125x 分组），仅当主 key「额度耗尽/无权限/认证失败」时回退；**限流（429）与模型无渠道（model_not_found）不回退**（换 key 没用）。④**0.125x 盈亏实测**（成本=官方价×倍率）：Plus ¥12→¥15（毛利率 40%→25%）、Pro ¥28→¥35（30%→12%）、Ultra ¥90→¥112.5（30%→13%），**都不亏**，可作保险，但长期跑 0.125x 建议提价或额度按 0.8 折。⑤顺带：付费调用遇「200 但内容为空」自动重试一次（推理型模型输出预算偏小时常见）。⑥**可达性提醒**：实测两把 key 的可用集合完全一致——只有 `deepseek-v4.1-flash` 稳定；`deepseek-v4-pro` 限流；`glm-5.3`/`glm-5.3-flash`/`glm-5.2`/`kimi-k3`/`step-3.7-flash`/`mimo-v2.6-flash` 均返回 `not supported by any configured account`，**「GLM/Kimi 可用」两把 key 都没复现**；价目与上下文已备好，渠道恢复后可用性探测会自动反映。 |
| 2026-09-27 | ZCode 按用户反馈做七项改动：①「深度求索中」→**「深度思考中」**（整段改名 + 插值句子串替换两条规则，覆盖「深度求索中，用时 N 秒」）；②**档位徽标**：付费档在侧栏用户名旁显示 PLUS/PRO/ULTRA 小胶囊（悬浮显示到期日，免费档不显示，收起侧栏时隐藏）；③**模型列表规则**：检测到用户外接 provider 时，把站点与官方内置那两组**隐藏**（是隐藏不是删除，清空外接后自动回来）；④**保护本站路由**：设置 → 模型 里 `ming-tea-hub` 的删除/移除类按钮被禁用并在捕获阶段拦截；⑤**插件页措辞**：这一节实际列的是「本部署装配的全部插件」（含已安装的第三方），把「内置插件/随应用自带」改成「已安装插件」，副标题写明；官方 `agent-team` 与语音输入本就默认启用（组合树无 disabled，实测 0 处）；⑥**「检查更新」独立页面**：注册成官方设置页的一节（`settings.section`），显示当前版本/站点最新版本/检查结果/更新内容/下载入口；同时移除旧的导航注入项（此前会出现两个「检查更新」）与多余的「账户」注入项；⑦**站点 500 降级**：5xx 不再当成登录过期（不误登出）、模型同步失败保留配置、宿主 RPC 改为端点内兜错并翻译成人话。离线自测扩到 **36 项全通过**，vitest 37 项通过。 |
| 2026-09-27 | **【已修复】付费档 software-auto 全链路 500 —— 铭荼侧报告的 `softwareTierAutoQuota is not defined` 确认属实，根因在站点侧，已修复并线上验收**。①**根因一**：加「付费档 Auto 免费次数」时 `routes/devApi.js` 的调用点写进去了、**import 漏了**（同文件两次替换互相覆盖）；只在付费分支执行，所以免费档正常、Plus 一兑换就全挂。②**根因二**（修完①才暴露）：我把 `auto→模型名` 的 `→` 写进了 `X-Scallion-Resolved-Model` **响应头**，HTTP 头只允许 ASCII → `500 ERR_INVALID_CHAR`；已改为 ASCII（`auto-<model>`）并把真实模型名另放 **`X-Scallion-Auto-Model`**。③**根因三**：`/models` 出现两个 `auto`（价目表里的 auto 计费项与手写项重复），已过滤。**验收（付费账号 id 112，Plus 生效中）**：`/usage` 200（`mode:paid`、`tier:Plus`、`budget_rmb:120`、`auto_free:{limit:200,used:0,remaining:200}`）、`/models` 200（10 个模型、auto 仅 1 次、付费模型带 `context_window`）、`/chat/completions {"model":"auto"}` **200 返回内容「可用」**，响应头 `X-Scallion-Resolved-Model: auto-nvidia/nemotron-3-ultra-550b-a55b`、`X-Scallion-Auto-Model`、`X-Scallion-Tier: plus`、`X-Scallion-Auto-Free-Remaining: 199`、`X-Scallion-Concurrency-Limit: 5`，响应体 `software_auto.via='auto_free'`。**铭荼侧可以按报告的字段清单收尾了**。 |
| 2026-09-27 | 站点侧就该故障补了 4 项**静态防回归检查**（`routes/devApiWiring.test.js`，这类错误行为测试测不到）：①控制器里对本地模块导出的引用必须真的 import；②付费档关键函数必须在 import 里；③响应头字面量不得含非 ASCII；④付费 `/models` 必须过滤价目表里的 `auto`。另外**站点侧发生并已处理的发布事故（如实记录）**：我在修第一个问题时把上传与安装写在同一条命令里、管道与 heredoc 抢 stdin，把生产 `routes/devApi.js` **覆盖成 0 字节**（PM2 重启 17 次），发现后立即用同批次备份恢复（站点即刻恢复），并改为「先上传校验字节 → 再安装 → 再重启」的分步发布。**对铭荼侧的影响是几分钟的 500，已完全恢复。** |
| 2026-09-27 | ZCode 强化开发模式（用户要求「开发模式下具备更好的编程能力」）：①**修正场景派生来源**——此前所有场景都从官方 standard 派生，导致 id 为 ptc 的「开发模式」既多开了官方故意关掉的 workflow-ptc/tool-workflow、又缺官方 ptc 的 tool-presentation；现按 preset id 对应来源派生（办公→standard、开发→ptc、学习→standard，学习不用 minimal 是因为它只挂两行）；②**补齐编程能力**：打开官方默认关的 `tool-subagent-codex`/`tool-ralph`，挂上官方孤儿包 `str-replace-editor`（精确字面量编辑，DSH 里唯一不整文件重写就能改代码的工具）；③**重写开发人格为编程纪律**（先读再写、精确编辑优先、计划先行、**验证才算完成**、修根因、待办、克制范围、可委派检索），逐条对标 Codex AGENTS.md 与 Claude Code 官方最佳实践；④实测踩到并修掉 **ralph↔workflowEngine 依赖冲突**（打开 ralph 而未开 workflow-ptc 会让整个开发场景加载失败）；⑤如实标注 **不开** `subagent_claude_code`（用户判断：DSH 自带子代理，外部 CLI 后端价值不大；本机也未装 claude）；⑥锁文件补该包（sha512）。三场景 broken=false、vitest 37 项与自测 38 项通过。 |
| 2026-09-27 | ZCode 站点修复后的付费档收尾：站点已修 `softwareTierAutoQuota is not defined`（import 漏了 + 响应头非 ASCII + /models auto 重复）。铭荼侧按**实测新形态**改四处：①付费档档位在 `tier` 对象（plan 为 null），已归一并解析 concurrency/auto_free_calls/monthly_budget_rmb；②`available_tiers` 付费档不再返回 → 「可升级」改读站点公开档位目录 `GET /api/ming-tea/tiers`（免登录、含 context_catalog）；③**修真 bug**：付费档此前看不到任何本档权益（那行只在免费档渲染、而付费档 available_tiers 为空）；④解析 `/usage` 的 `catalog` 上下文（256K/1M → 数字）。实测：页脚圆环 100%、用户名旁 PLUS 徽标、面板「档位：Plus / Auto 赠送 199/200 次 / 付费层 ¥120.00 / 本档权益：Auto 200 次/月 · 并发 5 · 额度 ¥120/月」。并如实标注两处站点不一致：`auth` 的 user 不反映 Plus（is_member:false）、到期时间取不到（徽标如实写站点未返回到期时间，不编造）。自测 38 项 + vitest 37 项通过。 |
| 2026-09-27 | ZCode 按用户判断**关闭 `subagent_claude_code`**：DSH 自带 `subagent`/`subagent_fork`，再多挂一个外部 Claude Code CLI 后端属于重复能力；且本机未装 `claude`，开着只会让模型挑到一个必然失败的工具。保留 `subagent_codex`（本机已装 codex、实测可用）。生成文件与运行时 dump 双双复核：开发场景 `tool-subagent-codex=false`（开）、`tool-subagent-claude-code=true`（关），三场景 `broken:false`。如需改回，在 `scenes.config.mjs` 的 rowToggles 里把该项设为 true 并先装好 CLI。 |
| 2026-09-27 | **站点侧再次全线 500**（`ECONNREFUSED 127.0.0.1:13306`）：`/usage`、`/models`、`/chat/completions`（auto 与付费模型同一入口）、`/api/papyrus/llm/quota` 全 500，而静态端点（tiers 200 / update 404 / 授权页 200）正常 ⇒ 站点内部服务未运行，非客户端问题。铭荼侧容错生效（未误登出、保留模型配置、额度回落 `—` 并给人话提示）。**顺带修掉我们自己的一个短板**：模型同步失败后没有重试，已加有限退避重试（5s/30s/2min，最多 3 次），站点恢复后自动补齐。证据与复测命令见 `docs/ming-tea-site-500-report.md` 追加章节。 |
| 2026-09-30 | DSH 侧复验并更正开发模式那批改动的两处记录（**只读复验，未改插件行为**）：①`subagent_claude_code` 在「开发场景边界」与「验收证据」里被写成"已启用但环境不具备"/`disabled=false`，与最终配置不符——实测是**刻意保持关闭**（`tool-subagent-claude-code=true`），已按事实更正，避免后来者以为存在一个必然失败的工具；②`check_ming_tea_hub.mjs` 自测项数由 36 更正为 **38**。复验命令（均只读、退出码 0）：`DSH_HOME=<repo>/.ming-tea/runtime/dsh-home .ming-tea/runtime/node_modules/.bin/dsh --profile ming-tea --dump-config` 组合成功；`python3 scripts/verify_presets.py <dump>` 输出开发场景 `tool-subagent-codex=false, tool-subagent-claude-code=true, tool-ralph=false, workflow-ptc=false, tool-workflow=false, tool-presentation=启用`；`node scripts/check_ming_tea_hub.mjs` 38/38；`platform/ming-tea` 下 `vitest run` 37/37（9 个文件）；`node scripts/build-presets.mjs` 重新生成与仓库内 `presets/scenes.patch.yml` 逐字节一致（22944 bytes）；插件 `node scripts/build.mjs` 产物 `lib/client.js` 哈希不变。**另注**：计划文本里「ptc/minimal 的 `plugins:` 在 6 空格」是错的——三个官方预设实测都是 8 空格（子项 10），生成器按 8 断言是对的。 |
| 2026-09-30 | DSH 侧新增设置页**「用量看板」**（用户要求「设置里加一个用量看板页面」）：①**先判该不该自建**——调研 12609 条商店目录（元数据全字段零命中 `sca-hub`/`ming-tea`/`铭荼`/`auto_free`）+ 源码级读 17 个候选包，结论是**不能复用**：生态里的「用量看板」读的是 DSH 本地会话 token，与站点额度不是同一个数；读中转站账本的 `dsh-tokenledger` 靠固定指纹，而实测 `sca-hub.cn` 是 SPA catch-all（5 条探测路由全 `200 text/html`）⇒ 判为 unknown relay、不产出数字；唯一支持自定义 HTTP 的 `dsh-credits@0.4.0` peer 经 node-semver 实测不满足 `0.1.7-rc.1`。②**落地**：扩展点用官方 `settings.section`（契约 `dsh-client-ui-settings/lib/types/client/contract/slots.d.ts`；写法与本仓库已验证的 `ming-tea-update` 同形状），`id: ming-tea-usage`、`order: 91`；新增宿主纯函数 `lib/host/usage-view.mjs` 的 `buildUsageBoard()`（未登录/过期/付费档/赠送用尽/缺字段/占比未知/到期解析共 7 条离线断言），新端点 `usage.board` **一次请求同时回传看板模型与归一后的 quota**，客户端用 `mingTeaApplyQuota()` 同步页脚圆环 ⇒ 两个界面永远同口径、不多打请求；`auto_free.configured:false` 不画 0/0、总数缺失时占比为 `null`（画斜纹显示「—」，不假装 0%）；页面**只读**，不放购买/升级入口。③**如实标注的边界**：本页**浏览器实测尚未做**，只完成静态与离线验证（`node --check`、bundle 内含注册调用、45 项自测、`--dump-config` 组合成功）。自测从 38 项增至 **45 项**。 |
| 2026-09-30 | DSH 侧新增两份调研文档（均只读调研，未改代码）：①`docs/ming-tea-desktop-official-lessons.md`——官方第一方桌面客户端（**Electron**，`apps/desktop`，读的是 `0.2.0-rc.2` 源码，**非**我们锁的 `0.1.7-rc.1`，已注明版本折扣、且不含运行观测）可借鉴点：**别抓 stdout**（官方用 Node IPC 信道 `{type:'ready',url,injections}` + `fetch(…,{redirect:'manual'})` 换 cookie）、分级优雅关停、PID 锁 profile、**先开窗再起 Host**、三段式路由嵌官方 Web 前端（实测 `dsh-web-frontend/dist` 在**我们锁定的 0.1.7-rc.1 里就已存在**）、安装更新前「锁准入 → 排空 → 复检 → 停不干净就拒绝安装」、更新流水账只落白名单字段、macOS GUI 启动拿不到 `~/.zshrc` 的 PATH（需跑一次登录 shell）；同时列出 Tauri 下**不成立**的做法（`ELECTRON_RUN_AS_NODE`、electron-updater、asar、`registerSchemesAsPrivileged`）与我们现状差距（`lib.rs` 只有一句 `child.kill()`、启动失败静默吞掉、stderr 无人读、无单实例锁、Tauri updater 其实**根本没接**——文档此前那句 endpoint 描述的是计划而非现状）。②`docs/ming-tea-usage-dashboard-survey.md`——用量看板选型证据与不确定清单。 |
| 2026-09-30 | DSH 侧把这批未提交改动**整理提交**（用户确认的策略：新建本地分支、**不推送**、本机 agent 产物不入库）：基线 `f46f634`（原 detached HEAD，提交会不可达）→ 新分支 `feat/ming-tea-desktop-agent`，4 个提交 `f08ad4d`（.gitignore）→ `13e4023`（界面层插件 + 三场景预设 + 用量看板）→ `1f8a5b1`（协作文档 + 两份调研 + 门禁脚本）→ `f90fedc`（跨平台 Agent + Tauri 壳 + 锁文件 + 安装脚本）。`.playwright-mcp/`（41 个快照 / 656K）与 `platform/ming-tea/.codex/` 已进 `.gitignore`。提交后工作树干净。**未推送**：`feat/ming-tea-desktop-agent` 只存在于本工作树。 |
| 2026-09-30 | DSH 侧对用量看板做**浏览器实测**（用户同意起临时实例）：`DSH_HOME=<repo>/.ming-tea/runtime/dsh-home .ming-tea/runtime/node_modules/.bin/dsh --profile ming-tea --port 19390 --no-open`（**注意**：`--profile` 模式下不能再写 `web`，否则报 `too many arguments`；实测后已关闭该实例）。Playwright 结果：①应用标题为「铭荼」、首页三场景正常；②设置左导航出现**「用量看板」**（位于「检查更新」下方，`settings.section` 注册生效）；③点击后页面在设置内容区渲染，**无控制台错误**；④站点 `/usage` 此刻 **HTTP 500**，页面如实显示「读取用量失败：站点暂时不可用（HTTP 500）…（hub/site-error）」——**计量条为空、没有编造数字**。**仍未覆盖**：正常数据态（三条计量条/占比条/档位/到期）的真数据渲染，受站点 500 阻塞；该形态由 `buildUsageBoard` 的 7 条离线断言覆盖。截图存于本机 `/tmp/mt-1-home.png`、`mt-2-settings.png`、`mt-3-usage.png`（未入库）。 |
| 2026-09-30 | DSH 侧补齐用量看板的**正常数据态**浏览器验证（用户同意起临时实例）：站点 `/usage` 当时仍 **HTTP 500**，因此**用本地假站点**（`127.0.0.1:19391` 重放付费档实测形态 payload）临时顶替，并**临时**把 `createHubService()` 的 hub 指向它（`createHubClient({origin, apiBaseUrl})` 本就允许注入）——**该临时改动已 `git checkout` 还原，工作树与提交一致**。Playwright 实测结果：Auto 赠送次数「剩余 199 / 200 次」进度条 100%、付费层余额「剩余 108.5 / 120 ¥」进度条 90%、事实区「Plus / 2026-10-06（6 天）/ 并发 5」、档位列表列出 Plus 与 Pro；**侧栏页脚圆环同步显示 90**，证实「`usage.board` 一次请求同时喂看板与圆环」的设计生效；控制台无错误。**证据边界（如实）**：「真站点返回正常数据」这一条**仍未实测**（站点 500 未修复），正常态的界面渲染是用 stub 验证的。**未把 hub 地址做成常驻环境变量开关**是有意为之：那等于让任何能设环境变量的人把站点凭证引到别的服务器。 |
| 2026-10-01 | **DSH runtime 0.1.7-rc.1 → 0.2.0-rc.2 升级**（用户要求「和官方版本对齐，然后更新对应插件」；0.2.0-rc.2 就是 npm `latest`，也是本机 GUI 的版本）。改动：①`assets/ming-tea-dsh-lock.json` 的 runtime 与 9 个官方工具包升到 0.2.0-rc.2、10 个 `@michengai/*` 升到**声明兼容 0.2.0-rc.2 的最新版**、新增 `versionExemptions`（见「版本豁免」）；②`scripts/install_ming_tea_plugins.sh` 两处修复——**先清 `node_modules` 再装**（否则 0.1.7 残留包与新包互锁 peer，npm 直接 `ERESOLVE`，实测踩到）与**在 `dsh plugin add` 之前授予版本豁免**（preflight 跑在 pnpm 之前，晚一步会因 `set -e` 半途中止）；③`app-shell.test.ts` 里硬编码的 codex-ui `1.1.18` → `1.1.25`；④自有插件 `dsh.engines.dsh` 提到 `>=0.2.0-rc.2`。**验证**（干净环境）：`dsh --version` = 0.2.0-rc.2；`dsh plugin version-exemptions` 只有 shop 一条；profile 里 13 个插件版本与 lock 逐项一致；`--dump-config` 1600 行、**无 `skipping profile bundle`/`is incompatible`**；用官方同一套 `semver` 复算：11 个插件 peer 全满足、只有 shop 需要豁免；vitest 37 项、`check_ming_tea_hub.mjs` 45 项、Python 8 项、插件与 preset 生成幂等全部通过。 |
| 2026-10-01 | **契约复核结论（两份只读调研 + 本人交叉验证）**：我们真正依赖的官方契约在 0.2 里**几乎全部稳定**——四个官方 preset 文件（`standard`/`ptc`/`minimal`/`cordis`）与 0.1.7 **逐字节相同**（unpkg 与真实安装两处各验一次，md5 一致），因此 preset 派生产物不变；`settings.section` 契约**纯新增**（只多 `settingsOpen`/`settingsShortcut`）；`theme.overrideTokens`、`connection.rpc` 的 `{ok,value}` 信封、`settings.mutate` 路径寻址、`credentials` 六方法、`agent-preset-registry` 的 `default: z.string().required()` 全部未变；`@deepseek-ai/cordis` 仍 `~4.0.4`；`tool-ralph` 仍硬依赖 `workflowEngine`（所以开发场景继续同时开 `workflow-ptc`）；0.2 新增的 `product-analytics`/`desktop-product-telemetry` 两行都带 `disabled: ctx.get('profileContext')?.name !== 'desktop'` ⇒ **ming-tea profile 自动不采集**。**同时纠正两条来自子代理的过期结论**：①「所有社区插件都会被兼容闸门禁用」不成立——那是按**旧版本** peer 推的；按本轮的**新版本** peer 复算是 11/12 通过，只有 shop 需要豁免（已实证：dump 无跳过）。②「官方 `定时任务` 改成 `自动化任务`」不准确——`定时任务` 在 `dsh-client-ui-conversation` 里仍在，`自动化任务` 属于我们**没有挂**的 schedule bundle，所以这条规则未改。 |
| 2026-10-01 | **定制层按 0.2 官方文案改写修 4 条 + 补 1 个监听**（都是静默失效型）：官方把 `内测声明` 改成 `预览版说明`、`设置 Subagent 的递归层级、数量和模型。` 改成「子智能体」措辞、删除 `开始你的创作`、并把账号页引导句换成带 DeepSeek 品牌的新句。对应改用例：前两条更新源串（第二条目标仍用更口语的「子任务」，**浏览器实测命中**）、`开始你的创作` 规则删除（官方新文案本身就是我们要的「开始使用」）、账号页那句**新增去品牌化规则**。另修一个更根本的问题：`MutationObserver` 只监听 `childList`，而 React 对「深度求索中，用时 N 秒」这类**插值句**只改文本节点的值（`characterData`），导致同一串在状态标签上换掉了、在消息元信息里还是原文——补上 `characterData: true` 后**在真实对话里实测生效**（页面显示「深度思考中，用时 29秒」）。 |
| 2026-10-01 | **一条对自己旧文档的更正 + 一条新发现的坑**：①旧文档（本文件与插件 README）写「`subagent_codex` 本机已装 codex CLI、实测可用」，**这与实现不符**：官方 runtime 只注册了 `spawn`/`fork` 两个 subagent provider（`dsh-subagent-spawn-in-process` 的 `providerName` 默认 `spawn`、`-fork-in-process` 默认 `fork`），**没有任何包注册名为 `codex` 的 provider**；而 `dsh-tool-subagent` 在 provider 未注册时只打一行 info、不注册工具（`lib/index.js:573-577`）。所以开发场景里 `disabled: false` 的 `tool-subagent-codex` 行**不会产生工具**（0.1.7 与 0.2 同）。已按事实改文档，不改 preset（是否要另找注册 codex provider 的包，属新产品决策）。②**模型选择的坑**：官方 DeepSeek provider 的模型展示名（`DeepSeek-V41-Flash`）与站点模型 id（`deepseek-v4.1-flash`）高度相似，若持久化的「当前选择」落在官方 provider 上，新会话会走 `deepseek-official` 并因缺 `DEEPSEEK_API_KEY` 报 `MISSING_CREDENTIAL`（实测遇到，根因是环境变量污染把桌面 profile 的默认模型写进了持久选择）。运行时的 `agent-default-model` 仍是 `ming-tea-hub`（`debug.state` 实测）；在模型选择器里选回**铭荼（站点）→ deepseek-v4.1-flash** 后会话即走 `ming-tea-hub`（已实测）。 |
| 2026-10-01 | **新增「快捷键呼出的语音助手面板」（summon）**（用户要求：关闭/最小化软件后按快捷键，像手机助手一样弹出宠物、听我说话、看屏幕决定是否操纵电脑）。落地：①**热键守护进程** `platform/ming-tea/apps/hotkey-daemon`（Rust + global-hotkey，随登录 LaunchAgent，**不申请任何 TCC 权限**；`settings.json` 可配热键，开发期可配 command 直接拉起开发二进制）；②**壳接管 DSH host**——不再 spawn 进程内桩 agent（那份 `ming-tea-agent.mjs` 保留留档但不再启动），改为真正监督 `dsh --profile ming-tea --port <随机> --no-open`（解析 token URL、SIGTERM/SIGINT 时带走子进程、pidfile 残留清理、**单飞锁**防并发起两个 host）；③**summon 面板**：透明置顶无边框、预建隐藏、`mingtea://summon` 唤起（`--summon` 可直接试）、再按一次或 Esc 收起、**冷启动只弹面板不弹主窗口**；④**三态 UI**（用户明确要求）：只有宠物（200×200、点击穿透）→ 说话时转录胶囊（420×280）→ 回答出现在胶囊下方（470×520），窗口底边固定、宠物钉在底部居中；两态之间由「面板页 → 壳」的 loopback token 上报口驱动；⑤**权限真实探测**（辅助功能/屏幕录制/麦克风）+ 一键跳系统设置；⑥文档 `docs/ming-tea-voice-summon.md`（含 8 个踩过的坑）。**实测**：壳侧端到端日志（单实例转发→面板→档位 pet/answer/listening 三档 curl 驱动）、Playwright 验证三态与几何（answerBelowCapsule/answerAbovePet/petCentered 全 true）、冷启动只弹面板、宿主生命周期两场景回归、**用户实测确认「只有宠物」**。测试：vitest 37/37、hub 自测 45/45、Python 8/8。**未做**：守护进程未通过 externalBin 入包（正式分发的 URL scheme 注册要等打包）、审批卡的紧凑版式与「本会话信任」接进 DSH 审批流、多屏跟随。 |
| 2026-10-01 | **summon 面板打通「看屏幕 → 决定动手」+ 会话级信任**（目标里的 ⑤⑥）。①**看屏幕不需要额外管线**：DSH 的电脑操作工具自带截图（`cua_driver_native__list_windows`/`get_accessibility_tree`/`get_window_state`）且 DSH 本就注入「先确认窗口再取新鲜快照」的引导；我们只需保证模型能看图（默认 `deepseek-v4.1-flash` ✓，实测 `glm-5.2` 明确说不能看图、`auto` 返回空 ⇒ 不要把看屏幕的会话切到 auto）。②**新增「本会话信任」**：宿主闸门（`installApprovalGate`，文案「将对电脑界面执行操作」就是它）在已信任会话里放行普通操作，输入/剪贴板、上传/提交、删除/安装/系统与终端命令**始终确认**；判定在 `lib/host/summon-trust.mjs`（纯函数 + 5 组离线断言，自测 45→50 项），信任是内存态、不落盘、不跨会话；按钮由客户端注入官方审批卡（主窗口与 summon 面板都有）。③**修了一个会被卡死的坑**：审批卡原本挂在 composerSeat 里、按整页布局排在 766px 高的底部，而 summon 宠物档只有 200×200 且点击穿透 ⇒ 审批卡在视口外**看不到也点不到**；现在有审批时切到 `approval` 档（520×600、可交互）并把卡片 `position: fixed` 钉到视口底部。**实测证据**：一次真实会话里模型先列窗口/取无障碍树 → 弹审批 → 点「本会话信任」→ **信任后 0 次新审批** → 取到窗口快照并准确回答「主窗口是铭荼（pid 85616），其余应用（Safari、ChatGPT、DeepSeek Harness、访达、文本编辑等）都在后台、没有可见窗口」。**仍未验证**：真人语音链路（需开麦说话，自动化覆盖不到）。 |
| 2026-10-01 | **补充：审批的「拒绝并停止」**。实测发现只点官方「拒绝」时模型会换个工具再试（拒绝 `check_permissions` 后立刻又弹 `get_desktop_state`），于是审批卡上再补一个「拒绝并停止」= 拒绝当前这次 + 调官方 `conversation.cancel()` 中止本轮。宿主策略日志（`MING_TEA_POLICY_TRACE=1`）实证：`deny …（审批结果：cancelled）` 与 `deny …（审批结果：rejected）`，之后 **3 分钟没有任何新的工具决策** —— 本轮确实停了，不再反复弹窗。另注：策略日志默认关闭，排障时要显式设 `MING_TEA_POLICY_TRACE=1`。 |
| 2026-10-01 | **summon 响应速度实测**：预热路径（应用在跑、面板已预建）从第二次实例启动到主实例处理完 **132 ms**（目标 <300ms）；冷启动（应用完全退出）到出现宠物 **2.9 s**、再到 DSH host 就绪 **4.8 s**（期间显示「正在唤醒…」本地页，不是空白）。快的原因：面板窗口启动即预建隐藏、host 常驻；冷启动的等待由唤醒页承担。 |
| 2026-10-01 | **summon 打通打包**：①守护进程作为 Tauri `bundle.externalBin` 进包（`Contents/MacOS/ming-tea-hotkey`），`pnpm build:hotkey` 会构建 release 并按 target triple 暂存；②`.app` 的 Info.plist 真的带上了 `CFBundleURLTypes`（`mingtea`）与 `NSMicrophoneUsageDescription` —— 这两条在裸二进制里都不生效；③**修掉一个会让热键指向死路径的 bug**：应用原先只在「没装过 LaunchAgent」时才写 plist，于是从开发机切到打包版后 plist 仍指向 repo 里的旧二进制；现在会比对「plist 里记录的 helper」与「当前解析到的 helper」，不一致或未加载就重写并重新 bootstrap。**实测**：`open "mingtea://summon"` → 打包版收到 deep link 并弹面板；plist 从 dev 路径自动刷新为 `.app/Contents/MacOS/ming-tea-hotkey` 且 launchd 已加载。**仍未做**：DSH runtime 未进包（打包版仍需 `MING_TEA_REPO_ROOT`/`MING_TEA_DSH_BIN` 才能找到 dsh）；DMG 打包在本机失败（`bundle_dmg.sh`，9-26 起就存在），本地验收用 `--bundles app`。 |
| 2026-10-01 | **summon 冷启动的生产路径打通**（应用被 Dock/URL 拉起时）：这类进程**不继承 shell 环境**，`MING_TEA_REPO_ROOT` 拿不到，而 runtime 又还没进包 ⇒ 之前 `open mingtea://summon` 只能拉起应用、DSH host 起不来（无 pidfile、无 host 进程）。现在 `resolve_dsh()` 增加两条兜底：读 `settings.json` 的 `dshBin`（显式路径）与 `repoRoot`（再拼 `.ming-tea/runtime/node_modules/.bin/dsh`）。**实测**：`open "mingtea://summon"`（完全无 shell 环境）→ 应用起来 → 从 settings 找到 runtime → DSH host 就绪（pid 89278）。配置字段与原因已写进 `docs/ming-tea-voice-summon.md`。 |
| 2026-10-01 | **语音链路盘查（三条硬结论，下一轮接着做）**：①**官方语音按钮只在「会话内输入条」里挂载**——新任务落地页整页没有任何语音元素（实测搜 `voice\|trigger\|mic` 只命中设置按钮与工作区选择器），而 summon 面板正落在新任务页 ⇒ 自动起麦点不到；修法是呼出时先进入一个会话（倾向打开最近会话，顺带续上上下文）。②**WKWebView 惯例要真实用户手势才开麦**，合成点击不算 ⇒ 已加「点一下宠物就开始说」兜底（宠物档同时改成可交互，否则点击穿透到背后窗口）。③**首次使用要下 239 MB 模型**（HuggingFace `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` 的 int8，另有 316 KB tokens + 1.8 MB VAD），落在 `$DSH_HOME/speech-to-text/sensevoice`，本机直连 HF 实测 200/0.7s，但未下载前语音不可用。已在 `docs/ming-tea-voice-summon.md` 写明。**另修**：`.mt-summon-layer` 原为 `pointer-events:none`，导致绑定在层上的点击永远收不到（自己埋的坑，已改）。 |
| 2026-10-01 | **语音根因定位（下一轮执行）**：①**语音行只存在于会话内的输入条** —— 在新任务落地页做真实点击后，输入卡片里仍只有「添加文件或调用指令」「发送消息」，`VoiceInput`/`data-voice-activity`/`录音|语音` aria-label **全部 0 命中**；会话页同位置则有「开始录音」。所以 summon 面板（落在新任务页）上的自动起麦/点一下说话都找不到按钮。**下一轮**：呼出时先进入会话（优先最近会话，顺带续上下文），再起麦。②**模型已下载并校验**：242 MB（int8 239 MB + tokens 316 KB + silero VAD 1.8 MB），三个文件 sha256 与官方 `assets.json` **逐字节一致**，落在 `$DSH_HOME/speech-to-text/sensevoice/models/`（HuggingFace，本机直连 200/0.7s）。③顺带修掉一个真坑：输入区原先 `visibility:hidden`，而语音行是**懒挂载**、隐藏时不挂载；改为「移到屏幕外 + 全透明」（React 认为它正常可见），并实测审批卡仍由自身 `position:fixed` 正确落在视口底部（注入假审批卡：`position=fixed`、rect 在 520×600 视口底部可见）。 |
| 2026-10-01 | **语音链路补齐一个 runtime 依赖（真缺陷，影响所有用户）+ 更正一条错误结论**：①官方语音 provider 的 worker 是 `.ts`，用 `node --import tsx/esm worker.ts` 启动，而 runtime 里**没有 tsx** ⇒ `prepareRuntime` 抛错、表现为「点麦克风后永远停在 requesting、模型从不加载、无 worker 进程」。已把 **tsx@4.23.15 写进锁文件**（与 pnpm 同属 runtime 依赖），并给宿主加 `speech.prepare`：面板呼出时**主动预热**（模型加载要几十秒）。②**更正**：先前写的「语音行只存在于会话内」是错的 —— 真因是「缺 tsx 时不挂载 + 我早期探针只等 13–14s，而该行在模型就绪后约 15–17s 才挂载」；落地页上「开始录音」稳定出现，输入区也用「移到屏幕外+全透明」保证懒挂载。③已核实（无需真麦克风）：三行语音配置正常挂载、`speech.status` 有 `sensevoice-local`、`speech.prepare` 返回 ok 且**真的拉起 worker 进程**、模型 242 MB 与官方 sha256 一致、原生依赖 `sherpa-onnx-node`+`sherpa-onnx-darwin-arm64` 在位、**点面板 → 官方麦克风被点 → 相态 requesting + 面板切 listening 并显示「我在听…」**。④**测试环境限制**：Playwright 的 Chromium 没有音频输入设备（`getUserMedia` 报 `Could not start audio source`），所以「录到声音→转文字」必须真机真麦验证。 |
| 2026-10-01 | **更正上一条关于 tsx 的判断（我错了，已撤回）**：上一条写「语音 worker 是 `.ts`、runtime 缺 `tsx` 导致所有人语音准备不起来」并据此把 tsx 钉进安装脚本 —— **这个判断不成立**。provider 自己的代码写明 `import.meta.url.endsWith(".ts") ? "./worker.ts" : "./worker.js"`（`lib/index.js:218`），发布包是 `.js` ⇒ 永远用 `lib/worker.js`，根本走不到 `--import tsx/esm`；实测 `speech.prepare` 拉起的进程确实是 `node …/sensevoice/lib/worker.js {"providerId":"sensevoice-local",…}`。**tsx 已从安装脚本撤回**（不引入用不到的运行时依赖）。当时看似卡住的真因是：①我的 RPC 传参 bug（`config.defaultProvider` 是响应式引用不是字符串，被当成 `"[object Object]"`）；②测试环境没有音频输入设备。教训记在 `docs/ming-tea-voice-summon.md`。保留的改动：面板预热 `speech.prepare`、`speech.status` 排障端点。 |
| 2026-10-01 | **修掉「语音预热从未生效」这个真 bug**：面板的预热调用原本在**第一轮 tick** 就发，并**提前把 `speechWarmed` 置位** —— 而那时 `connection` 服务还没注入，于是报「宿主连接不可用（connection 服务未注入）」并且**永不重试**，打包版里预热一直没起作用（实测：打包版面板打开 45s 也不见 worker）。改为「成功才置位 + 有限次重试」，并把状态写进 `data-ming-tea-speech-prepare`。复验：预热标记从 `error:宿主连接不可用…` 变为 `ok`。另注：worker 是在**真正需要转录时**才起（点麦克风后实测能看到 `sensevoice/lib/worker.js` 进程），所以「预热是否拉起 worker」不能作为预热成功的判据。 |
| 2026-10-01 | **热键加固：候选回退 + 生效键落盘**。①守护进程改为按**候选列表**注册（`hotkey` 写字符串时自动补 `ctrl+alt+space`/`cmd+shift+space`；也可直接写数组），第一个成功的生效并写入 `~/Library/Application Support/铭荼/hotkey-state.json`（`{active, configured, pid}`），应用的 `hotkey_status` 读它，界面可显示「按哪个键」。②**实测发现**：macOS **允许两个进程都注册同一个热键**（两个守护进程都报「已注册 alt+space」）⇒「注册成功」不等于「事件一定到我们手里」，若被启动器抢占，判断办法是看 `hotkey.log` 里有没有「触发」行（有=键到了、问题在后续 `open`；没有=被别的应用拿走，换键即可）。③候选回退已用「首选键不可解析」验证：日志 `解析失败，跳过` → `已注册 ctrl+alt+space`，状态文件同步。 |
| 2026-10-01 | **summon 面板的工具活动与失败可见性**（此前完全看不到）：面板把官方对话区藏起来了，于是「助手正在看屏幕」和「工具失败」这两类信息对用户不可见（实测踩到：审批拒绝后模型换了工具重试、面板却一直像没反应）。现在按 回答区三语气 显示：新回答 > 挂起错误（`操作失败：…`，红边）> 工具运行中（`正在看屏幕…`，斜体灰）> 最近回答；并把档位切到 `answer` 以保证可见。**两个实测坑**：①「工具失败→模型随后解释」时错误优先级若恒定高于回答会一直显示旧错误 ⇒ 新回答到达要清掉挂起错误；②`[data-turn-process-answer]` 里**上一条回答也一直在 DOM**，所以判断必须用「文本是否变化」。用注入假节点验证了四种状态（pet / status / error / answer）全部正确。 |
| 2026-10-02 | **「看屏幕」此前是坏的（真缺陷，已修）+ 更正我 10-01 的一条结论**。①**症状**：代理能点（像素坐标点得准），却**看不见** —— 实测轨迹里它自述「no screenshot available to me (image unavailable — model doesn't support images)」，只能猜坐标硬试。②**根因**：DSH 用模型条目上的 `inputModalities` 判断能否收图，而 pi-ai 的模型条目**缺省 `["text"]`**（`DEFAULT_INPUT`）；我们的站点路由是**手写**的，只声明了 `id/name/contextWindow/reasoningEfforts`，于是工具层直接拒绝：`cannot read "…/calc_before.png" as an image: model "deepseek-v4.1-flash" does not declare image input` —— **截图根本到不了模型**。③**更正**：10-01 那条「默认 deepseek-v4.1-flash 能看图 ✓（所以视觉链路真的通了）」**在站点 API 层面成立、在产品里不成立**：当时模型拿到的只是无障碍树的**文本**（窗口标题清单），图片是被拦掉的。④**修法**（`lib/host/model-route.mjs`）：给站点实测能看图的模型显式声明 `input:["text","image"]`（`deepseek-v4.1-flash`/`mimo-v2.6-flash`/`glm-5.3`），其余 `["text"]`；**`auto` 特意不声明**（传图返回空，声明了只会换来空回答）。打开一次页面即生效（`modelsSync` 按内容差异重写 `profiles/ming-tea/cordis.patch.yml` 里的 `llm-pi-ai.providers.ming-tea-hub`，已核对落盘值逐条正确）。⑤**修完复测（用户已授予全部权限）**：①壳上报口五项**全部 `available:true`**（辅助功能、屏幕录制、麦克风、终端、诊断 —— 麦克风只有 `Authorized` 才为真，`NotDetermined` 会如实报 false，所以不是误读）；②一次真实会话（1 轮/28 步/1.4M tok/4 分 12 秒）把「截图→决策→动手」跑通：代理启动计算器→点 AC→点 `7 + 3 =`，思考里出现「the `%` button (center 145,185)」这类**从画面读出的坐标**，显示区按 `7 → 7+ → 7+3 → 10` 推进，回答「显示区结果：10（上方还保留算式 7+3）」，并如实交代两条限制（计算器无障碍树为空 `elements=0`、后台输入被拒故走前台 CGEvent，`bring_to_front` 无法独立验证 frontmost）；③**独立复核**：把上一轮留下的真实截图直接发站点 API，`deepseek-v4.1-flash` 答出「计算器 / 0」（点击前状态，prompt_tokens 253）⇒ 图片确实能进模型、我们的声明属实。**证据边界**：最终「10」是代理**读自己的截图**得出的，我无法独立复核那张图（本会话主模型不收图；从 bash 调 `osascript`/`screencapture` 无权限会挂起，已实测并终止）；可独立确认的是计算器进程确实被拉起、截图能被站点模型正确读出。⑥**顺带**：审批行为符合设计 —— 点过「本会话信任」后点击类不再问，但「模拟键盘输入或读写剪贴板」**仍每次都问**（本次 5 次审批里 3 次是键盘输入，其中一次卡片上不提供「本会话信任」）。**新测试**：hub 自测 51/51（+7 条图像声明断言）、vitest 37/37。**待用户决定**：路由默认模型 `auto` 不能看图 ⇒ 新用户首次「看屏幕」会报错；预设层钉不了模型（`dsh-agent-preset` 无 model 字段），要么把默认值换成 `deepseek-v4.1-flash`，要么在面板里提示切模型。 |
| 2026-10-02 | **「模型不能看图」的用户可感失败已消除一半**：上一条把 `auto` 不能看图这件事留成了产品取舍，但用户当时只会看到一句英文工具错误（无任何可操作性）。现在面板把两种确定的官方措辞翻译成可操作提示（`mingTeaHumanizeError`，客户端半区，只翻译确定的、其余原样透出）：「操作失败：当前模型不能看图（Auto 不支持图片），请在输入框旁把模型切到 deepseek-v4.1-flash，再说一次。」**真实页面注入验证三例**：`does not declare image input` ✓、`does not support image input` ✓、无关错误（`bring_to_front … was not verified as frontmost`）**原样透出** ✓（均 tone=error、stage=answer）。顺带补一条**产物新鲜度断言**：`check_ming_tea_hub.mjs` 现在会核对 `lib/client.js` 里含新函数与新文案 —— 改了 `ui-tweaks.js` 忘了 `node scripts/build.mjs` 会被自测挡住（本项目踩过多次「宿主缓存旧包」）。自测 52/52。**仍未决定**：`auto` 作为路由默认模型时「看屏幕」必然失败，是要换默认值还是保持提示。 |
