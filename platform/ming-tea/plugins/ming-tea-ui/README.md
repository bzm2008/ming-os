# @ming-tea/dsh-ui

铭荼自有的 DSH 界面层。它是叠加在社区前端之上的**定制插件**，不是社区源码的分叉：
本包不复制 `@michengai/dsh-codex-ui` 的任何代码，只通过 DSH 官方的 bundle 补丁与
client 插件机制注入自己的品牌、配色、几何与场景模式。

## 结构

| 文件 | 作用 |
| --- | --- |
| `cordis.patch.yml` | 停用官方四个 Agent 模式、把 preset 默认值指向办公场景、插入本插件行 |
| `scenes.config.mjs` | 三个场景（办公 / 开发 / 辅助学习）的 id、名称、描述与人格提示 |
| `presets/scenes.patch.yml` | **生成物**：三个场景的 preset 声明，工具列表派生自官方 standard 预设 |
| `lib/index.mjs` | 宿主半区，无运行时职责 |
| `theme/ming-tea-theme.css` | 铭荼主题（品牌、配色、圆角、首页文案）的唯一事实来源 |
| `lib/client.js` | **生成物**：把 CSS 内联进浏览器 bundle |
| `scripts/build.mjs` | 生成 `lib/client.js` |
| `scripts/build-presets.mjs` | 生成 `presets/scenes.patch.yml` |

## 版本对齐：DSH `0.2.0-rc.2`（2026-10-01）

本插件现在对齐 **`@deepseek-ai/dsh@0.2.0-rc.2`**（npm `latest`，也是官方桌面客户端的版本），
`package.json` 的 `dsh.engines.dsh` 同步为 `>=0.2.0-rc.2`。升级记录与逐项证据见
`docs/ming-tea-live-status.md` 的 2026-10-01 条目；这里只留对本插件有直接影响的结论。

**官方契约：无破坏性变更**（逐项核对，证据见协作文档）

- 四个官方 preset 文件（`standard`/`ptc`/`minimal`/`cordis`）与 0.1.7-rc.1 **逐字节相同**
  ⇒ `scenes.patch.yml` 重新生成后无任何 diff，三个场景的行状态与升级前一致。
- `settings.section` 槽位契约、`theme.overrideTokens`、`connection.rpc` 的 `{ok, value}` 信封、
  `settings.mutate` 路径寻址、`credentials` 五方法、`agent-preset-registry` 的 `default` 配置键**均未变**。
- 0.2 新增的 `product-analytics` / `desktop-product-telemetry` 两行带
  `disabled: ctx.get('profileContext')?.name !== 'desktop'` ⇒ **`ming-tea` profile 不采集**。

**插件兼容矩阵（0.2 的实际闸门：逐条 `@deepseek-ai/dsh*` peer 用 `semver.satisfies(rt, range, {includePrerelease:true})`）**

- 10 个 `@michengai/*` + `plugin-effort-slider`：**全部通过**（前者升到声明兼容 `0.2.0-rc.2` 的最新版；
  后者**没有 `peerDependencies`**，闸门直接放行）。
- 本插件：**没有 `peerDependencies`**，永远放行（`dsh.engines` 在 DSH 里没有任何代码读它）。
- `dsh-plugin-shop@0.8.3`：**唯一需要精确版本豁免**的一个 —— 上游 0.8.3 与 0.8.4-beta.0 的 peer 都停在
  `^0.1.1-rc.2`。豁免由 `scripts/install_ming_tea_plugins.sh` 按锁文件 `versionExemptions`
  在 `dsh plugin add` **之前**授予（preflight 跑在 pnpm 之前，晚一步会因 `set -e` 半途中断）。
  实测：0.2 下商店页面正常渲染（目录 12551 条、分类、「隐藏不兼容」默认开启、无控制台错误）；
  **「从商店安装第三方插件」这一步未实测**。

**定制层适配（0.2 改写了官方中文文案）**

| 我们的规则 | 0.2 的官方新文案 | 处理 |
| --- | --- | --- |
| `内测声明 → 使用说明` | `预览版说明` | 源串跟改（旧串在 0.2 里已 0 命中） |
| `设置 Subagent 的递归层级、数量和模型。` | 改成「子智能体」措辞 | 源串跟改，目标仍用更口语的「子任务」；**浏览器实测命中** |
| `开始你的创作 → 使用` | 官方已删除该句、新文案本身就是「开始使用」 | 规则删除（留着只是永不命中的死规则） |
| `登录后即可创建…` | 换成带 DeepSeek 品牌的新句 | **新增去品牌化规则**（旧规则随之删除） |

另外修了一个更根本的问题：`MutationObserver` 原本只监听 `childList`，而 React 对
「深度求索中，用时 N 秒」这类**插值句**只改文本节点的值（`characterData` 事件），
导致同一串在状态标签上换掉了、在消息元信息里仍是原文。补上 `characterData: true` 后
**在真实对话里实测生效**（页面显示「深度思考中，用时 29秒」）。

**本轮验证清单**：`dsh --version` = 0.2.0-rc.2；干净环境 `--dump-config` 无跳过/禁用、三场景行状态与升级前一致；
浏览器实测首页三场景卡、场景选择芯片（办公/开发/辅助学习三种描述）、设置页 15 项、用量看板（真站点数据）、
商店目录、思考强度滑块、真实对话（走 `ming-tea-hub`）；控制台零错误。
**未实测**：商店的安装动作、`.dcu-home-suggestions` 之外的社区 DOM 细节、`tool-subagent-codex`（见上文更正——它本来就不产生工具）。

## 开发场景：编程能力与纪律

**场景派生来源（2026-09-27 修正的坑）**：每个场景按自己的 `sourcePreset` 派生 ——
办公→`standard`、开发→**`ptc`**、学习→`standard`。

此前**所有**场景都从 `standard` 派生、只换 persona，于是 id 为 `ptc` 的「开发模式」
拿到了 standard 的行表：多开了官方故意关掉的 `workflow-ptc`/`tool-workflow`，
又缺官方 ptc 的 `tool-presentation`（`mode: ptc`）。**名字叫开发、能力却是别的。**
教训：按 preset id 命名场景时，必须按同一个 id 的官方来源派生。

学习场景**不用**官方 `minimal` 作来源：实测它只挂 `persona` + `persistent-shell` 两行
（官方那是极简测试场景），派生出来只剩 1 个工具行，连读写文件都没有。
所以学习场景来源仍是 `standard`，能力靠 `dropRows` + 宿主侧 restrict 裁剪；
代价是**官方 persistent-shell 仍然缺席**，这是有意的取舍（其价值是命令状态跨调用保持，
对只读场景收益低于引入终端的风险）。

**只给开发场景带来的能力**（`rowToggles` / `extraRows`）：

| 能力 | 之前 | 现在 | 说明 |
| --- | --- | --- | --- |
| `str_replace_editor` | 官方没有任何预设挂载 | 开发场景挂上 | DSH 里唯一「不整文件重写就能改代码」的工具：查看/创建/字面量精确替换/按行插入 |
| `subagent_codex` | `disabled: true` | 打开 | ⚠️ **2026-10-01 更正：这一行不会产生工具**。DSH 只注册了 `spawn`/`fork` 两个 subagent provider，没有任何包注册 `codex`；`dsh-tool-subagent` 在 provider 未注册时只打一行 info、不注册工具（`lib/index.js:573-577`）。本机确实装了 `codex` CLI，但那是另一回事。要真正启用需另找一个注册 `codex` provider 的包（未做）。 |
| `subagent_claude_code` | `disabled: true` | **保持关闭** | 刻意不开：DSH 自带 `subagent`/`subagent_fork`，外部 CLI 后端是重复能力；本机也未装 `claude` |
| `tool-ralph` | `disabled: true` | 打开 | 新鲜上下文循环重构；上游注明「完成与否是**工人自报**、非独立评估」 |
| `tool-presentation` | 无（源自 standard） | 随 ptc 来源获得 | 官方 ptc 用它替代工作流编排 |
| `workflow-ptc` / `tool-workflow` | 开着（错） | 开着（有意） | 见下方冲突说明 |

**⚠️ ralph 与「关掉工作流」不可兼得（实测）**：`tool-ralph` 硬注入 `workflowEngine`，
该服务由 `workflow-ptc` 提供。第一次只打开 ralph 而保持 workflow 关闭时，开发场景
**直接加载失败**，诊断原文：

```
tool-ralph (@deepseek-ai/dsh-tool-ralph): waiting for workflowEngine
```

既然 ralph 是要的，`workflow-ptc`/`tool-workflow` 就一并开启 —— **这是与官方 ptc 的有意分歧**，
代价是模型多一个 workflow 工具。

**开发人格 = 一套编程纪律**（逐条对标 Codex 的 AGENTS.md 与 Claude Code 官方最佳实践）：
先读再写；精确编辑优先（不整文件重写）；非平凡任务先计划并写清验收标准；
**声明完成前必须跑测试/构建并把命令与输出作为证据**，没验证就说没验证；
修根因不打补丁；多步任务维护待办；只改相关代码；大范围检索交给 subagent。
刻意**没有**照搬 Claude Code 的 `/clear` 与 Stop hook —— DSH 没有这些机制，不引入本环境不存在的东西。

**判定陷阱（写在这里免得再踩）**：校验 preset 内子行的 `disabled` 必须**按缩进**判断 ——
`- id:` 的横线缩进是 N、同行键的缩进是 N+2；用「首行」或固定缩进的正则会全部判错
（本轮先后误判两次）。仓库里已有 `scripts/verify_presets.py` 可复用。

## 场景模式（替代官方四个模式）

DSH 的"模式"就是 Agent preset：新对话开始前在首页选择，决定该会话的工具组合与人格。
铭荼把官方的 标准 / PC / 极简 三个模式**按行 id 覆盖**成 办公 / 开发 / 辅助学习 三个场景，
并停用内置的 `cordis`（创造模式入口，本产品不用）——用户只需要在三个场景里选一个。

**为什么是覆盖而不是"停用＋新增"**（2026-09-27 实测教训）：DSH 把用户选中的默认 preset
持久化在 `agent-presets` 设置命名空间（`selectedDefault`），且它优先于部署默认值。曾用
"停用内置四个 + 插入三个新 id"的做法，结果旧引用悬空，新建会话直接失败：

```
agent-preset/not-found: Unknown agent preset: standard
```

连锁后果是编辑器不激活，模型选择、思考强度、权限预设三类控件全都不渲染。
保留 `standard`/`ptc`/`minimal` 三个内置 id 只替换 config，任何已持久化的引用都继续有效。

**按场景裁剪能力的正确机制：少挂行，而不是过滤工具**（2026-09-27 实测教训）。

最初设想用官方 `ctx.tools.restrict` 在 preset 作用域内做白名单。实测被证伪：

- `restrict` 的契约写明它只限制**全局**工具，且"**作用域内的名字会失败**"；
- 而 preset 自己注册的 `tool-*` 行（tool-bash / tool-fs / …）属于**该 preset 作用域**，
  不是全局层 —— 因此传进去的每个名字都会被判为不可识别，preset 直接挂载失败
  （实测报错 `agent-preset/invalid … 工具名无法识别`）。
- `guard` 也不适用：契约写明 **plain-context 的 guard 全局生效**，只有经 `agent.ctx`
  注册才限定到单个 agent，而 preset 行的 ctx 属于哪一类无法从契约确认。

所以采用官方预设本来的做法：**每个场景挂哪些行不同**（`scenes.config.mjs` 的 `dropRows`）。
当前：办公与辅助学习不挂 `tool-bash` / `tool-pwsh` / `tool-jobs`；开发保持全量。

**能力边界（不要在文档里夸大）**：`tool-fs` 一次注册 `read`/`write`/`edit` 全套且
**没有关闭写入的配置项**，因此"学习场景完全只读"做不到。现状是：学习场景没有命令执行
能力，文件写入仍存在但由权限档（工作区内修改 + 每次询问）逐次审批，场景人格也明确
"以讲解为主、不代替动手"。

工具列表不手抄：`scripts/build-presets.mjs` 读已安装的
`@deepseek-ai/dsh-web-app/presets/standard.patch.yml`，只替换 preset 的名称 / 描述 /
排序 / 人格提示，因此 DSH 升级新增工具时会自动跟上。当前三个场景**工具组合相同、差异在
人格提示**；按场景裁剪工具与权限属于后续工作（需与 Agent 的权限策略一起设计）。

## 主题

- 圆角尺度：`--mt-radius-sm` 12px、`md` 16px、`lg` 22px、`xl` 26px、`pill` 999px；
  官方令牌 `--dsh-windows-content-radius` 与 `--dsl-code-block-border-radius` 一并接管。
- 品牌色：薄荷青绿 `#16857d`（深色模式提亮为 `#4fb3a4`），覆盖官方蓝色别名
  `--dsw-alias-link`、`--dsw-alias-state-business-primary`、`--dsw-specific-bubble-highlight` 等。
  只改别名层，不动 `--dsw-static-*` 调色板，避免影响语义状态色。
- 品牌与文案：隐藏官方矢量字标与首页标语，用伪元素写出「铭荼」字标、首页「铭」字方章
  与首页标题「需要我帮你做什么？」。文案替换限定在 `[data-phase="hero"]` 内，不影响会话页。
- **第三方插件的 antd 界面**：技能、归档等插件用 antd v5，且开了 CSS 变量模式（token 挂在
  `css-var-*` 包装元素上）。因此只覆盖 token 而不碰 antd 的类选择器：
  `--ant-color-primary` → 薄荷、`--ant-border-radius*` → 本尺度。按钮、开关、下拉、日期选择、
  分段控件会一并跟随，插件升级也不易碎。实测：主色 `#7aaaff` → `#4fb3a4`，按钮圆角 4px → 12px。
- **质感细节**：键盘焦点环（`:focus-visible` 2px 薄荷描边、偏移 2px）、文本选中薄荷底、
  细滚动条跟随品牌色、交互过渡统一 180ms 与 `cubic-bezier(0.22,1,0.36,1)`、
  首页卡片悬停上浮 1px（`prefers-reduced-motion` 下关闭）、codex-pet 浮窗加贴合轮廓的柔和投影。
- 颜色只覆盖别名与底色，浅色与深色两套主题都成立（浅色已在设置页验证）。

## 选择器约定

依据对真实运行页面的 DOM 普查：

- 社区 UI 用稳定的 `dcu-*` 前缀类名，直接匹配。
- 官方组件是 CSS Modules，类名形如 `<hash>_<语义名>`；哈希随构建变化而语义名稳定，
  因此按 `[class*="_语义名"]` 匹配。
- 宿主外壳提供语义化钩子（`data-slot`、`data-phase`、`data-composer-card` 等），最稳定，优先使用。
- 所有覆盖带 `:root` 前缀（0-2-0），胜过单类选择器（0-1-0），且不用 `!important`。

## 开发

```bash
node scripts/build.mjs            # 改完 theme/*.css 后重新生成 lib/client.js
node scripts/build-presets.mjs    # 改完 scenes.config.mjs 后重新生成场景预设
```

本包以 `link:` 方式装入开发用 profile，改完重新 build 并刷新页面即可，不需要重装。
两者的产物都随包提交（`lib/client.js`、`presets/scenes.patch.yml`），不要手改。

## UI 定制层（ui-tweaks.js）

侧栏与设置导航的条目**没有语义类名**（只有文字），CSS 无法按文字选择，因此改名、
隐藏、追加页脚入口这三类需求只能靠一小段 DOM 调整。`ui-tweaks.js` 就是这层，
它被 `scripts/build.mjs` 内联进 `lib/client.js`，在 factory 物化时启动。

当前清单（改这里即可，不要加特例）：

| 类型 | 内容 |
| --- | --- |
| 改名 | `IM助理` → `连接手机`（侧栏与设置导航同时生效） |
| 任何位置隐藏 | `Codex UI`、`GitHub`、`问题反馈` |
| **仅侧栏**隐藏 | `连接手机`、`专家`、`技能`、`插件`、`连接器` |
| 整块隐藏 | `.dcu-extensions-group`（侧栏「扩展管理」及其全部子项） |
| 侧栏页脚一行 | 账户 · 连接手机（手机图标）· 剩余用量（圆环）· 设置（末尾由宿主提供） |
| 设置导航追加 | 检查更新 |

**两处关键区分**（踩过坑，别再简化）：

1. **`hideAnywhere` 与 `hideInSidebar` 必须分开**。侧栏与设置导航复用同一列区域，且都有
   「连接手机 / 专家 / 技能」这类同名条目；设置页条目带 `dcu-settings-link` 类。若把
   `Codex UI` 也放进"仅侧栏"规则，它会因为位于设置页而被跳过——**它只存在于设置页，
   必须用 anywhere 规则**。第一版曾因此把 Codex UI 又显示回来。
2. 页脚三个入口目前是**占位**：点击弹出「尚未接入」说明，不做任何假装成功的动作。
   剩余用量圆环在无数据源时显示 `—`，**不编造百分比**；接入用量后把 `ui-tweaks.js`
   里的 `MING_TEA_QUOTA_PERCENT` 接上真实数值即可（同时会启用 `data-has-value` 样式）。

设计约束：只匹配叶子文本**完全相等**的节点；隐藏只作用于按钮/链接祖先，避免误伤正文；
MutationObserver + 180ms 去抖重放以适配 React 重渲染；已处理节点打标记保证幂等；
任何异常都静默降级（定制层失败绝不影响页面可用）。

**已知脆弱性（有意接受）**：匹配依据是第三方文案，文案一变对应调整会**静默失效**
（不报错、不破坏页面）。届时改这张清单，而不是继续加特例。长期做法是让插件作者
提供配置项；这一步只是让产品当下可用。

## 与站点交互的三条硬规则（都踩过）

1. **只有 401/403 才算「登录过期」**。5xx 是站点故障：清凭证会把用户无辜登出，且额度接口故障时
   用户仍能用别的模型。现在 5xx 归到 `hub/site-error`，界面显示
   「站点暂时不可用（HTTP 500），稍后会自动重试；这不影响你的登录状态。」
2. **模型同步失败必须保留上一次的配置**。把路由写成空列表会让用户连模型都选不了。
3. **宿主 RPC 的端点要在内部兜住错误、永远返回 `ok: true` + `value`**。抛错会让 connection
   走失败信封，客户端只能拿到 `invalid server-response failure` 这种通用串，站点故障的真实原因就到不了界面。
   错误码与 HTTP 状态放在 `value` 里，并由 `describeHubFailure()` 翻译成人话。

另：`settings.plugins.tab` 下「插件列表」列的是**本部署装配的全部插件**（`dsh-host-plugin-inventory`
直接读 Loader 条目），所以已安装的第三方插件本来就在里面；我们只改了措辞（「内置插件/随应用自带」→「已安装插件」），
没有改分组逻辑——那属于官方清单的语义，动它会让用户对不上官方文档。

## 站点接入（登录 / 额度 / 检查更新）

三项都**只在宿主半区发起请求**：浏览器直连站点受 CORS 限制（未确认），Tauri 壳的 CSP 也只允许
`'self' ipc:`；宿主（DSH 的 Node 进程）没有这两个问题，**站点 JWT 因此不进浏览器页面**。

- 通道：`ctx.inject(["connection"])` → `scope.connection.rpc.handle("/ming-tea", handler)`，
  客户端经 `ctx.connection.rpc.call("/ming-tea", endpoint)` 调用（DSH 自带 cookie 鉴权、同源、
  `{ok, value}` 信封）。端点白名单：`ping`、`auth.status|start|poll|signOut`、`quota.get`、
  `usage.board`、`update.check`、`debug.state|settingsWriteProbe`。**deviceCode 只留宿主内存**，不下发浏览器。
- 路径：**鉴权用品牌化别名** `POST/GET /api/ming-tea/auth/device[/:code]`（站点建议；`verificationUrl` 指向 `/device/authorize?…&client=ming-tea`，
  已登录账号打开即自动授权、跨网络才需确认一次）。模型接口用 OpenAI 兼容的 `…/api/dev-api/software-auto/v1`——
  模型提供方要的是 OpenAI 协议，这条已实测可用；站点另有 `/api/ming-tea/llm/*` 别名，未采用（避免把非 OpenAI 形态的路径塞进 provider base URL）。
- **付费档字段实测形态**（与早期文档不同）：付费档 `plan` 为 `null`，档位在 **`tier` 对象**里
  （`key/name/monthly_budget_rmb/auto_free_calls/concurrency`），顶层另有 `tier_name`；归一后两者都认。
  `auto_free.configured:false` 表示本档没有 Auto 赠送（免费档如此），此时不显示该行。
- **可升级档位读站点公开目录** `GET /api/ming-tea/tiers`（免登录、字段最全：价格/额度/并发/Auto 赠送 + `context_catalog`）；
  `/usage` 的 `available_tiers` 只作兜底（实测付费档不再返回它）。
  ⚠️ 曾因此有真 bug：付费档「本档权益」那行**整行为空**（它只在免费档渲染，而付费档没数据）。
- **`auth` 的 `user` 不反映 Plus**（实测 `is_member:false`、`member_expires_at:null`），所以**档位与额度一律以 `/usage` 为准**；
  徽标悬浮取不到到期时间时如实显示「（站点未返回到期时间）」，不编造。
- 付费档：解析 `/usage` 的 `tier.name`（面板显示档位）与 `monthly_budget_rmb`；登录时拉 `/models` 并把**可用**模型并入站点路由的 `models`
  （`auto` 恒在首位、`available:false` 丢弃），因此模型选择器能直接选付费型号。`/models` 形态未承诺，解析是防御式的（三种形态都认，认不出只留 `auto`）。
- 凭证：JWT 走 `ctx.credentials.set("MING_TEA_HUB_TOKEN", …)`（`$DSH_HOME/.credentials.yaml`，0600）。
  放进凭证引用的另一个好处是 pi-ai **每次请求都重新解析引用**，换 token 不必重启。
  用户信息（昵称/到期）走 `grant` 记录 `ming-tea-ui/account`。环境变量遮蔽时写入会被拒，错误如实上报。
- **手写路由必须声明 `reasoningEfforts`**（本次踩到）：缺省时它只继承「已安装目录里同 id 模型」的能力，
  而我们的 `auto` 不在任何目录里 ⇒ **会话里不会有思考强度控件**。声明形如 `{ off: null, low: low, medium: medium, high: high }`
  （键是选择器档位，值是上线时发的拼写；只有 `off` 允许留空）。该声明只在写路由时生效，所以有 `models.sync` 端点
  （登录时与每次打开页面同步，内容一致就不写）。
- 模型：登录后经宿主 `settings.mutate` **路径寻址**写入 `llm-pi-ai.providers["ming-tea-hub"]`
  （`api: openai-completions`、站点 base URL、`apiKeyEnv` 指向上面的引用）并切默认模型；
  登出只在默认**仍指向我们**时回退。用户自己加的第三方 provider 不会被碰到（模型页照常可用）。
  实测：幂等空写探针返回 `{ok:true}`，说明写入是**热生效**、不需要重启。
- 界面：页脚账户按钮与设置页「账户」项打开同一个面板（设备码大字 + 打开授权页 + 按站点
  `interval` 自动轮询 + 登录态/刷新用量/退出）；剩余用量圆环显示站点额度，
  **未登录或取不到时仍是 `—`，不编造百分比**；「检查更新」真实调用清单接口，
  未发布（404 + `ming_tea_release_not_published`）静默显示「已是最新」。
- 离线自测：`node scripts/check_ming_tea_hub.mjs`（假 fetch + 假宿主服务，**45 项**）覆盖设备码状态机、
  额度归一化、404 语义、版本比较、凭证读写、模型路由写入 ops 与冲突重试，以及**用量看板的整形**（见下节）。

## 设置页「用量看板」（`settings.section`）

入口：设置 → 用量看板（与「检查更新」同一个官方槽位机制：`ctx.slots.register("settings.section", …)`，
`id: ming-tea-usage`、`order: 91`，参照物是本仓库已验证过的 `ming-tea-update`）。

- **为什么整形放宿主**：本仓库对宿主模块有可离线跑的断言（`scripts/check_ming_tea_hub.mjs` 直接 import），
  客户端半区是浏览器 bundle、没有可跑测试的环境。所以 `lib/host/usage-view.mjs` 的
  `buildUsageBoard()` 是**纯函数**（7 条断言覆盖未登录/过期/付费档/用尽/缺字段/占比未知/到期解析），
  界面只负责画。
- **一次请求两个界面**：端点是 `usage.board` —— 复用 `quota.get` 的取数（含登录态判定与站点故障分类），
  返回整形后的模型**加上**归一后的 `quota`；页面拿到后用 `mingTeaApplyQuota()` 同步页脚圆环与账号徽标。
  这样刷新看板不会出现「看板一个数、圆环另一个数」，也不多打一次请求。
  （为此把额度缓存的写入从 `mingTeaRefreshQuota()` 里抽成了 `mingTeaApplyQuota()`。）
- **三条计量条**：Auto 赠送次数（次数）、付费层余额（¥）、本月免费次数（免费档才有）。
  `auto_free.configured:false`（本档没有赠送）**不画 0/0**；总数缺失或非正时占比是 `null`，
  界面画斜纹并显示「—」，**不假装 0%**。Auto 用尽时如实写明「改为从付费层余额扣、不会硬性停用」（站点侧承诺）。
- **只读**：页面只列额度与档位，**不放购买/升级按钮** —— 下单支付属于站点页面，这里不放一个假装能买的入口。
- **浏览器实测（2026-09-30，临时实例 `127.0.0.1:19390` + Playwright，已通过）**：设置左导航出现「用量看板」
  （排在「检查更新」下方），点击后页面在设置内容区渲染，**无控制台报错**。两种数据态都测到了：
  - **错误态（对真站点）**：站点此刻 `/usage` 返回 500，页面如实显示「读取用量失败：站点暂时不可用（HTTP 500），
    稍后会自动重试；这不影响你的登录状态。（hub/site-error）」，**计量条为空、没有编造任何数字**。
  - **正常数据态（对本地假站点，见下）**：两条计量条按真实形态渲染 ——
    「Auto 赠送次数 剩余 199 / 200 次、已用 1 次 · 剩余 100%」、「付费层余额 剩余 108.5 / 120 ¥、已用 11.5 ¥ · 剩余 90%」，
    进度条宽度 100% / 90%；事实区显示「当前档位 Plus、到期时间 2026-10-06（6 天）、并发上限 5」；
    档位列表列出站点给的 Plus / Pro 及权益。**侧栏页脚圆环同步变成 `90`** —— 证实「一次请求两个界面同口径」确实生效。
- ⚠️ **这条正常数据态的证据边界（必须如实读）**：站点 `/usage` 当时 **HTTP 500**（站点侧老毛病，
  见 `docs/ming-tea-site-500-report.md`），所以**正常态是用本地假站点跑出来的**：临时起了一个
  `127.0.0.1:19391` 的 stub 重放「付费档实测形态」的 payload（`plan:null` + `tier` + `auto_free` + `available_tiers`），
  并**临时**把宿主 `createHubService()` 的 hub 指向它（`createHubClient({origin, apiBaseUrl})` 本就允许注入）。
  **该临时改动已用 `git checkout` 还原**，工作树与提交一致；`createHubClient()` 现仍固定指向 `https://sca-hub.cn`。
  未把它做成常驻的环境变量开关是有意的：那等于让任何能设环境变量的人把**站点凭证**引到别的服务器。
  → 因此「真站点返回正常数据」这一条**仍未实测**，等站点修复后重跑一次即可闭合。

## 侧栏页脚与收起态

版式：`账户（圆头像 + 名称）→ 剩余用量圆环 → 连接手机 → 设置`，账户 `margin-right:auto` 推左、
其余靠右。**顺序只能用 CSS `order` 排**：官方「设置」在它自己的 `.dcu-settings-seat` 里，
是 `.dcu-footer-actions` 的兄弟节点，挪 DOM 排不到它。

收起（`.dcu-root.dcu-compact`，轨道 36px、根容器 `overflow:hidden`）时必须竖排 + 定宽，
否则我们注入的宽按钮被裁一半、官方设置被压成 0 宽整块消失（两个都实测踩过）。
规则：`.dcu-compact` 下 `.dcu-foot` 竖排、四个入口各 32px、`.mt-foot-name`/`.mt-foot-label` 隐藏。

## 输入框旁的「上下文占用」指示器

口径与官方 ContextMeter 完全一致：`used = projectedTokens ?? pressureTokens`，
`percent = round(used / contextWindow * 100)`，数据来自
`ctx.sessions.binding(id).session.projections.faceOf("contextPressure")` 的订阅
（官方的圆环只在「有会话且产生过用量」时才渲染，没有凭据时用户永远看不到，所以我们自己补一个）。

- 无数据时显示「上下文 —」并给出说明，**不编造百分比**。
- 挂载点：输入卡片内部底栏下方；输入卡片用「含 `[class*="_input"]` 的 `[class*="_card"]`」定位，
  避免挂到首页建议卡上。React 重渲染会清掉它，由填充循环幂等补回。

## 商店（dsh-plugin-shop）的三项改造

1. **安装前二次确认**：捕获阶段拦 `[data-shop-install]` / `[data-shop-update]` 的点击
   （React 18 委托监听挂在 root 容器上，捕获阶段 `stopPropagation` 拦得住），弹我们自己的确认框
   （名称/版本/来源/信任级别/作者/许可/仓库/校验值 + 风险说明，焦点默认在「取消」）。
   许可与仓库是实时向商店目录查的（`ctx.get("remote.shop").catalog()`，1.5s 超时；查不到就少显示几行）。
   确认后**放行**给商店：非 verified 条目仍会弹商店自己的 acknowledged 确认（宿主强制的，我们不复刻也不绕过）。
   **注意**：`[data-shop-confirm]` 是商店的「确认安装」按钮本身，不要当成"取消"用来测试（踩过）。
2. **默认隐藏不兼容条目**：商店原本 `useState(false)`（内存态），默认展示 1103 个与当前 DSH 不兼容的条目；
   由 `scripts/patch_shop_typert_compat.mjs` 改写为 `true`（幂等、`--revert` 还原）。
3. **美化**：样式只在 `[data-shop-tab]` 范围内、用 `data-*` 与语义后缀选择器，不碰 hash 类名前缀。

## 清亮模式（低配 / 专注）

「设置 → 常规 → 清亮模式」的开关（行结构克隆自真实设置行，保证与原生一致）。打开后：

- **隐藏非必要功能与其入口**：宠物（含浮窗外框 `.dcp-floating`）、专家、定时任务（侧栏条目、设置导航条目、侧栏「定时」标签）。
- **减少动画与渲染**：停掉全部 `animation`/`transition`（`!important`），本主题引入的柔光阴影置 `none`，对话框/菜单/编辑器卡片阴影清零，宠物浮窗去掉 `drop-shadow`，用量圆环退化为细描边。

状态存在浏览器 `localStorage` 的 `ming-tea.lite-mode`（**不做假的服务端同步**；换浏览器不跟随，这是当前取舍，接入 DSH settings 后可升级）。
隐藏清单在 `ui-tweaks.js` 的 `MING_TEA_TWEAKS.lite.hide`，加功能只需追加一条 `{ text }`；同名文字用 `hostClass` 消歧（如「定时」标签用 `dcu-im-tab`）。

关键实现点：**开→关时要能完全恢复**。因此仅由清亮模式隐藏的元素额外打 `data-ming-tea-lite-hidden="1"`，
且恢复动作放在隐藏规则**之前**执行——否则同时被侧栏规则命中的元素（如「专家」既在
`hideInSidebar` 又在 `lite.hide`）会在关闭清亮模式时被错误显示。

## 文案替换的两种规则

- `rename`：叶子文本**完全相等**时替换（安全）。
- `replaceText`：**子串**替换，仅作用于长度 < 80 的叶子文本。用于句子内部的引用——
  频道空态原文是「还没有频道会话。先在设置 → IM助理 里连接渠道。」，只靠等值规则改不到。

注意文本遍历器的长度上限必须 ≥ 子串替换的阈值：早期上限写成 24 字符，导致那句 24+ 字的
提示**根本没被遍历到**，表现为"规则写了但不生效"。现在两者统一为 80。

## 令牌覆盖：作用域与两条通道（重要）

**官方把设计令牌声明在 `body{}` / `body[data-ds-dark-theme]{}` 上**（`dsh-client-ui-theme`
的 design-platform.css），因此写在 `:root` 的覆盖会被 body 自身声明遮蔽。2026-09-27 实测：

| 令牌 | `<html>` | `<body>`（官方） | 深层元素实际取到 |
| --- | --- | --- | --- |
| `--dsw-alias-link` | 我方 `#16857d` | 官方 `#4176e6` | **官方蓝**（我方失效） |
| `--dsw-alias-bg-base` | 我方 `#f6faf8` | 官方 `#fff` | **官方白**（我方失效） |

**现在的做法**：色板集中在 `theme/palette.mjs`（单一事实来源），由 `scripts/build.mjs`
同时投递到两条通道——

1. **官方 API**：client 半区导出 `apply(ctx)` + `inject: ["theme", "sessions", "conversation"]`，
   调用 `ctx.theme.overrideTokens(packageId, tokens)`。该 API 把值写成 **body 内联样式**，
   必然胜出；`overrideTokens` 的取值形态是 `{ '--token': { light, dark } }`，
   两种模式的字符串都必填。
2. **CSS 兜底**：由 `palette.mjs` 生成 `:root body { … }` / `:root body[data-ds-dark-theme] { … }`
   的样式块内联进 bundle。特异度高于官方的 `body`，因此不依赖注入顺序；API 形态变化时样式仍在。

覆盖集包含品牌/链接/业务强调/气泡/侧栏激活/背景层/文字层级/发丝线/交互态/浮层/蒙层，
外加**官方 15 个"被消费却从未定义"的令牌**（其中 `--dsw-alias-state-warning-primary`
原本会回退成品牌蓝，即"警告色实际显示为蓝色"）。只改别名层与少量字面值，
不动 `--dsw-static-*` 原始调色板——否则会连带改变语义状态色。

`apply()` 里对 `overrideTokens` 的调用被 try/catch 包住并把错误记进
`window.__mingTeaThemeErrors`：官方"教学式错误"能让命名不匹配时**可诊断而不是静默**，
同时样式已由 CSS 通道保证。

## 首屏防闪（宿主半区）

官方 boot 把画布底色写死 `#fff`/`#151517`，与我们的纸面不一致会闪白。
`lib/index.mjs`（宿主半区）监听 `webserver/index-inject`，推一行 `{ kind: "style" }`
（**不 prepend**，排在官方之后，同特异度后者胜）。实测首屏 HTML：

```html
<style>…background-color:#fff;--dsh-boot-bg:#fff</style>        ← 官方
<style>…background-color:#f6faf8;--dsh-boot-bg:#f6faf8…</style> ← 铭荼
```

已知边界：宿主侧读不到用户持久化的外观偏好（需要 settings 服务），只能按
`prefers-color-scheme` 分流；偏好与系统配色不一致时首屏可能有一瞬不符，应用挂载后修正。

## 辅助学习场景的真正只读（宿主半区）

`dsh-tool-fs` 一次注册 read/write/edit 且没有关闭写入的开关，所以"不挂那几行"只能去掉读。
真正的按 agent 裁剪要用官方 `tools.restrict` —— 契约里"作用域内的名字会失败"指的是
**调用者自己层**的名字；从 agent 层调用时 `write`/`edit` 是**继承自 preset 层**的名字，
可被 restrict（官方样板：subagent 的 `toolFilter`）。

实现要点（见 `lib/index.mjs`）：

- 监听 `agent/created`（新会话）与 `agent-preset/selected`（既有会话切换预设）两条路径。
  **切换预设不会重建 agent** —— 实测切到学习场景后 `agent/created` 不再触发，
  只在既有 agent 上重绑代际，所以必须两处都处理，否则"从办公切到学习"会漏掉。
- 先动态过滤"确实可见"的名字再下 deny：名字写错会让 restrict 抛错，而该事件是串行
  await 的，**会导致 agent 创建失败**（我们已在阶段 6 被这个机制咬过一次）。
- 限制可解除：切回办公场景时调用 disposer，避免限制滞留（这也是官方文档提示的风险）。
- 诊断：`MING_TEA_POLICY_TRACE=1` 时把决策写进 `$DSH_HOME/.ming-tea-policy-trace.log`，
  用于在没有模型凭据的情况下验证策略是否真的执行。实测输出：
  `preset=minimal 已移除写入工具: write,edit` → `preset=standard 已解除只读限制`。

边界如实说明：这是**工具可见性层面**的移除（模型看不到也调不动），不是内核级围栏；
会话内仍可把权限档切到 workspace-write，但没有可调用的写入工具。
真正的硬围栏是 `dsh-fs-sandbox` 的 `read-only` 模式，但它是**全局**的，无法按场景区分。

## 浏览器自动化与电脑操作：三个场景都有（宿主半区 + preset 组）

用户要求（2026-09-27）：办公与辅助学习也要浏览器，电脑操作同样装上，于是三个场景都挂。
落地方式是每个场景的 `plugins` 末尾加一个 `abilities` 组：

```yaml
- id: abilities
  name: cordis:group
  group: true
  isolate:
    browserUse: true
    computerUse: true
  config:
    - id: browser-use            # @deepseek-ai/dsh-browser-use（提供 browserUse 服务）
    - id: browser-use-playwright-mcp   # mode: launch + headless，独立会话
    - id: computer-use           # @deepseek-ai/dsh-computer-use（提供 computerUse 服务）
    - id: computer-use-cua-driver-native
```

**为什么必须是 group + isolate（2026-09-27 实测踩到）**：把这四行平铺进 preset，三个场景
**全部**加载失败——`standard` 报 `Preset services require isolate realms: browserUse, computerUse`，
`ptc`/`minimal` 报 `service "browserUse" has been registered at <BrowserUseRegistry>`。
服务型行在 preset 作用域里会与根作用域或其它预设的注册撞名，必须显式隔离；官方在预设里挂
服务型行也是同一手法（standard 预设的 planning / compaction / delegation 三组都带 `isolate`）。
排查入口：预设管理页卡片里有一段视觉隐藏的原因文本（`[class*="cardBrokenReason"]`），
比只看「加载失败」徽标有用得多。

安全边界（两条，均已落地）：

1. **审批闸门**（`lib/index.mjs` 的 `installApprovalGate`）：`tools/pre-execute` 拦截两类工具族
   —— `cua_driver_native__*`（包源码确认的前缀）与 `mcp__playwright-mcp__*`（源自
   `mountSessionMcp({ name: "playwright-mcp" })` 与 runtime 的 `` `mcp__ `` 前缀约定）——
   除只读元数据（`list_apps` / `list_windows` / `get_screen_size` / `get_cursor_position` /
   `list_tools`）外，一律走 `approval.request`，且**只接受 `allowed-once`**；拿不到审批通道
   时失败关闭（拒绝）。这补上了官方层缺失的闸门。
2. **桌面控制的系统前提**：macOS 上需要给**启动 DSH 的宿主 App**授予 Accessibility + 屏幕录制
   全量授权（归系统设置管，装包得不到）；Linux/Ming OS 还需对应原生包。

浏览器自动化用 `launch + headless`（独立会话），不接管用户已登录的浏览器。

## 低配机器的默认策略（宿主半区）

定时任务与连接手机合计带约 90 个包（含 antd 与 5 家 IM SDK），对老电脑是明显的启动
与内存负担。宿主半区在启动时做一次体检：**内存 ≤4GB 或核数 ≤2** 视为低配，把
`dsh-automation` 与 `im-connect` 两行设为 `disabled: true`。

几个刻意的设计：

- 写进 **profile 的用户层**（不是 home 层），所以用户能在「设置 → 插件配置」里手动开启，
  或直接删掉这一段——这是"低配默认不挂、可手动开"的落地方式。
- **只决定一次**：用独立的 `$DSH_HOME/.ming-tea-low-spec-applied` 记录已评估，
  既不会每次启动都改用户的 patch，也不会在用户删掉我们的段落后又加回来。
  要重新评估就删掉这个标记文件。
- **只追加、不动其他条目**：写入前不加锁，但通过标记文件保证幂等；实测原有四条
  用户设置（ui-theme / locale / agent-default-model / agency-agents）完好无损。
- 前置校验：确认这两行**没有持久化引用指向**（参照"停用被引用行会导致新建会话失败"
  的既有教训），且停用效果用**运行时名册**验证（侧栏的 任务/频道/定时 标签消失），
  不以 `--dump-config` 为准——该命令对部分行不渲染 `disabled`。
- 可用 `MING_TEA_LOW_SPEC=1|0` 显式覆盖，用于部署指定与验证。

职责边界：这属于"部署策略"而非界面定制，放在本包是因为它目前是铭荼唯一的自有 DSH 插件；
将来自有插件变多时，应搬进独立的部署插件。

## 首页场景卡（替换官方开发向卡片）

官方首页是四张编程向卡片（探索代码/构建工具/审查/修复）+ 八条推荐任务，与铭荼三场景
定位冲突。现在：`.dcu-home-suggestions` 隐藏，由定制层注入 `.mt-scene-cards`
（办公 / 开发 / 辅助学习 各两张），**点击真的写入输入框**——走的是官方同一条路径：

```js
currentSessionId → sessions.binding(id) → conversation.input.for(binding.ctx).setDraft(text)
```

并复用官方的三种前置状态语义（未选工作区 / 忙碌 / 已有草稿），失败时用我们的提示气泡
告诉用户原因，而不是静默无反应。

## 文案替换：两种规则与长度上限

- `rename`：叶子文本**完全相等**时替换（安全）。
- `replaceText`：**子串**替换，按数组顺序在同一节点上连续执行。

**两条纪律**（都踩过坑）：

1. **遍历上限必须 ≥ 精确匹配需要的长度**。早期上限写成 24 字符，导致 24+ 字的提示句
   根本没被遍历到（表现为"规则写了但不生效"）；现在为 400，子串替换另在应用处限 80。
2. **泛化子串必须排在最后**。`replaceText` 是按序替换，若 `DSH` 排在 `DSH Web` 之前，
   会先把 `DSH Web` 改成 `铭荼 Web`。顺序：具体优先，`DSH` 兜底收尾。

## 验证陷阱

`dsh --dump-config` 对 preset 这类 EntryGroup 行**不渲染 `disabled` 字段**：即使停用
已经生效，dump 里仍看不到标志。判断模式是否被停用要看运行时名册（首页模式选择器里
实际列出哪些选项），不要只凭 dump 下结论。

## 边界

- 不 fork 社区插件源码；样式只做叠加。**唯一例外是 `ui-tweaks.js`**：它按文字匹配
  改名/隐藏少数第三方入口并追加页脚按钮，原因与脆弱性见上节。
- 不改变权限、审批与审计行为（那些归铭荼 Agent 与权限策略管理）。
- `.ming-tea/runtime/dsh-home/profiles/<name>/cordis.patch.yml` 是 DSH 与设置界面
  托管的**用户层**（主题偏好、语言、默认模型、宠物配置都在里面），任何脚本都不得覆写它。

## 插件商店（在「随应用自带 → 插件商店」标签里）

`dsh-plugin-shop@0.8.3` 由安装脚本装入 profile，界面就是官方的插件管理页新增的一个标签，
我们只做两件事：

1. **typert codec 兼容改写**（`scripts/patch_shop_typert_compat.mjs`）：商店编译产物把 codec 写成
   `schema: <zod schema 值>`，而 DSH 加载器与已发布的生成器都用 `create: <惰性工厂>`，因此
   宿主半区报 `… codec has no create() factory`、网页端报
   `web boot: 1 entry did not activate dsh-plugin-shop: failed`。脚本对三份产物
   （`typert.host.js`、`typert.remote-client.js`、`client.js`）做等价改写 `schema: X → create: () => X`
   （只在 codec 对象内命中），幂等、`--revert` 可还原、已接入安装脚本。**上游对齐后请还原并删掉这一步。**
2. **目录源钉死**：`catalogUrl` 写死在 profile patch，避免环境变量 `DSH_SHOP_CATALOG_URL` 改向目录
   （目录内容决定能装什么）。自建白名单目录时只改这一行 URL，需要的目录格式与必须补的控制
   见 `docs/ming-tea-plugin-audit.md` 的商店章节。

注意**层**：这个覆盖必须写在 profile patch，不能写在我们的 bundle patch 里 ——
bundle 层之间的按 id 覆盖会静默失效（我们的 bundle 先于商店的 bundle 应用）。

## 教训：按 id 的 config 覆盖是整块替换

给商店只写 `catalogUrl` 会丢掉默认的 `cacheDir`，界面表现是「暂时无法读取目录。」，
日志里没有任何报错。覆盖某个行的 `config` 时必须把它原本依赖的键一起写上。

## 教训：模糊阴影不要给整列结构容器

曾把 `--mt-shadow-soft`（`0 10px 30px`）加到 `[class*="_card"/"_panel"/"_surface"/"_guide"]`
一整组上。右侧边栏的内容容器正好匹配 `_guide`，而它是**全高整列**——30px 模糊阴影跨过
栏边界渗到相邻栏，表现为中缝位置一条发虚的竖带（用户报的“缝”）。A/B 实测：摘掉主题后
该处只是一条干净的 1px 线。

规则：**结构容器只统一圆角，阴影只给真正浮动（对话框/菜单/气泡）或小面积（卡片）的元素。**
同类风险还有 `filter`、`backdrop-filter`、大面积 `border-image`。
