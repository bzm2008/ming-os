# 铭荼插件审计清单

铭荼首期遵循“先找社区项目，再做适配”的规则。插件清单的机器可读版本位于 `assets/ming-tea-plugins.json`，构建阶段会把它安装到 `/usr/share/ming-os/ming-tea/plugins.json` 并做内容校验。

社区候选的机器可读审计表位于 `assets/ming-tea-community-candidates.json`。候选插件只有 `adapted-local`、`adaptation-review`、`optional-adapter`、`optional-adapter-ready` 或 `installed-verified` 状态时才允许进入开发环境；`blocked-version`、`external-connector-only` 和 `needs-source` 不得进入默认 ISO。`installed-verified` 表示已在本机 profile 真实安装并验收，不等于可随 ISO 分发（分发另看许可证与素材授权）。

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
- `dsh-data-agent`：MIT；DSH runtime 已锁定 `0.2.0-rc.2`（2026-10-01 升级），但该插件尚未完成 peer/runtime 集成验收，数据库只读与 SQL 执行审批也未实现。
- `@michengai/dsh-codex-ui@1.1.25`：Apache-2.0，SHA512 已锁定（2026-10-01 升到声明兼容 `0.2.0-rc.2` 的版本）；作为 DSH web profile 的客户端基线，提供侧栏、工作区会话树、搜索、会话导航和 composer。2026-09-26 完成真实安装验证：profile `ming-tea` 的 bundles 自动登记、`--dump-config` 组合通过、页面注册并加载 `client.js`。先前记录的 `ERESOLVE` peer 冲突未复现。
- `@linxin666/dsh-pet@0.4.3`：Apache-2.0，peer 精确要求 `@deepseek-ai/dsh >=0.1.7-rc.1`（历史条目：该宠物已被 `@michengai/dsh-codex-pet` 取代），依赖仅 `clsx` 与 `schemastery`（无 Electron/原生依赖，适配 `--ignore-scripts`）。2026-09-26 验证挂载并实际渲染。随包默认宠物图集的许可见其仓库 `THIRD_PARTY_NOTICES`，不在 Apache-2.0 范围内；铭荼自有宠物素材单独授权。
- `@ming-tea/dsh-ui@0.1.0`：铭荼**自有**界面定制层（MIT，源码随本仓库置于 `platform/ming-tea/plugins/ming-tea-ui`，以 `link:` 装入开发 profile）。按 DSH 官方契约实现 cordis bundle + web client 插件，只在社区前端之上注入圆润几何、薄荷品牌与柔光阴影；**不 fork 社区源码、不改写 DOM 结构、不触碰权限与审批**。

### MichengAI 插件族（2026-09-27 安装并验收）

同一作者（`MichengAI`，`dsh-codex-ui` 的来源）的插件族，全部 Apache-2.0；**2026-10-01 全部升到声明兼容 `0.2.0-rc.2` 的最新版**（此前声明的 `0.1.7-rc.1` 已过时）、无原生依赖、`hasInstallScript` 全为 false。已入库：`dsh-codex-pet@0.1.10`（替换原 linxin 宠物）、`dsh-archive-manager@1.0.5`、`dsh-skills-manager@1.1.4`、`dsh-agency-agents@1.0.5`、`dsh-im-connect@0.1.55`、`dsh-btw@0.1.13`、`dsh-simplify@0.1.10`、`dsh-code-review@0.1.7`、`dsh-automation@0.1.51`（定时任务，2026-09-27 补装）。

安装方式：逐包装入 profile `ming-tea`，`--ignore-scripts --save-exact`，装后 `--dump-config` 校验并重启验收。**未使用** `@michengai/dsh-codex-suite-installer`（官方已标注不建议新安装，且会重写 profile 的 `dsh.profile.bundles` 与 `pnpm-workspace.yaml`）与聚合包 `@michengai/dsh-codex-suite`（依赖锁死在远古版本）。

逐项风险：

- **automation（后补）**：依赖 `antd 6.6.5` + `luxon` + `zod`（约 4.7MB，连带 69 个包），是本批里 UI 依赖最重的一个；它没有自己的样式体系，界面自动继承铭荼的 antd token 覆盖（实测主色 `#16857d`），因此不需要专门适配。排程会在无人值守时创建会话，能力已标 `approval-gated`。
- **archive-manager（最高）**：按设计接替官方 `workspace` 与 `session-projection-cache` 两行。已实测宿主启动无错误、工作区选择与会话创建正常。安装时那条 `patch: entry "ui-settings-unarchive-sessions" not found` 是插件自带说明里的预期告警（rc.1 无该行）。
- **im-connect（安全面最大）**：含 5 家 IM 厂商 SDK、需要平台凭据与出网；账号绑定需用户自行扫码。其 `sidebar.channels` 槽由 codex-ui 声明（软依赖已满足）。
- **codex-pet 素材授权**：随包 `assets/codex` 图集来自 OpenAI Codex，NOTICE 明确不在 Apache-2.0 内 → **不可随 ISO 分发**，自用可以。
- **agency-agents**：11MB 内容资产，321 份专家文本的再分发版权未逐条核实，随 ISO 前需内容审计。
- **第三方思考强度滑块** `plugin-effort-slider@1.2.2`（MIT，非 MichengAI）：用户要求的滑块形态。实测挂官方 `conversation.input.right`（list 槽）、改档可用、颜色随 `--dsw-alias-button-info-fill` 被铭荼主题接管。其 `engines` 仅声明 `>=0.1.2-alpha`，但审计确认所用槽与服务在 rc.1 全部存在。

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

## 插件商店 / 应用市场的评估（2026-09-27）

用户希望应用内有"商店"让普通用户发现并安装插件，但**安全第一**。对生态内主要候选做过一次
源码级评估后，结论是：**没有任何现成商店可以不加改造地放进面向家庭用户的产品**。

| 候选 | 关键事实 | 结论 |
| --- | --- | --- |
| `dsh-desktop-safe-market@0.6.0` | 安全模型最贴合（市场默认关闭、插件自身**没有安装执行接口**，只把审查提示词填进输入框；展示 license；要求锁精确版本 + integrity）；有 provenance | **等版本**：要求 DSH ≥0.1.7-rc.2，与我们锁的 rc.1 不兼容；0.5.2 的 peer 范围可覆盖但作者只验证过 0.1.5 系列 |
| `dshmarket@1.66.2` | 生态最大（月下载 42 万、4652★、provenance），有**策展目录准入**（非目录来源直接 400 拒绝）、同源 POST、自述无遥测 | **只借机制**：功能面过大 —— WebDAV/Gist 备份（接触用户配置与凭据）、重启宿主、GitHub 加速代理、桌面端注入通道 |
| `dsh-plugin-shop@0.8.3` | 工程最干净：目录**内容寻址**（sha256 指针 + 校验）、Host/Client 权限切割、**不经 shell** 调官方 CLI、安装时校验 tarball 与声明的 registry 同源；但自述"无人审读过任何条目、无沙箱" | **已装入并实测可用**（2026-09-27，见文末落地记录）：目录源已钉死、codec 形态做了等价改写；**仍不宜作为唯一信任源**，自建白名单目录是推荐下一步 |
| `dsh-plugin`(Hub)@1.4.8 | 会**自动写 allowBuilds 放行构建脚本**并重试，可执行 `npm install -g`；peer 锁 cordis 4.0.1 与 rc.1 不符 | **不建议** |
| `dshhub-market@0.8.60` | 在本机开 HTTP 桥（127.0.0.1:3750-3754）、平台 zip 安装、口令付费、黑名单**fail-open** | **不建议** |
| `dsh-store@0.5.2` | 目录靠"npm 关键词 + GitHub topic 抓取"，**无白名单**；8-16 后停滞 | **不建议**（可参考骨架） |
| `@linxin666/dsh-client-ui-market@0.4.3` | `engines.dsh >=0.1.7-rc.2` | **版本已排除** |
| `dsh-skin-market@0.1.55` | 皮肤非代码，但 CSS/字体仍可外带数据；无 provenance | **不建议内置** |

**若自建"白名单商店"，必须补的控制**（调研结论，供后续实现）：

1. 目录**自持且只读**：不用第三方目录站，用随发行版本内置、带校验的 `catalog.json`，
   字段含精确版本、`dist.integrity`、license、仓库、能力声明、审计日期。
2. 安装**只走官方通道**：`dsh plugin --profile <p> add <name>@<exact>`，禁止 git/URL/latest/file 目标，
   禁止 `npm install -g`；用 argv 数组调用、`shell: false`（照抄 shop 的做法）。
3. **禁止自动放行构建脚本**：不写 `allowBuilds`/`onlyBuiltDependencies`；含 install script 的包必须已审计。
4. **安装前展示** license、来源仓库、精确版本、integrity 前若干位、能力声明。
5. **不联网回传**：商店组件不得有遥测，不得上传已装列表。
6. **可回滚**：安装前快照 profile（`package.json`/`cordis.patch.yml`/lockfile）。

不要复用：Hub 的 `npm install -g` 与自动 allowBuilds、`dshhub-market` 的本机 HTTP 桥、
`dsh-store`/`dsh-plugins-store` 的抓取式目录与硬编码第三方后端、`dshmarket` 的备份与重启宿主能力。

## 落地记录：`dsh-plugin-shop@0.8.3`（2026-09-27 实测）

> **2026-10-01 升级到 DSH `0.2.0-rc.2` 后的状态（必读）**：商店**没有任何声明支持 0.2 的版本**
> （`0.8.3` 与 `0.8.4-beta.0` 的 peer 都停在 `^0.1.1-rc.2`），而 0.2 会在 `dsh plugin add` 的
> **preflight**（跑在 pnpm 之前）与**加载时**都检查 `@deepseek-ai/dsh*` peer。因此它现在跑在
> **精确版本豁免**上（`dsh plugin --profile ming-tea allow-version dsh-plugin-shop@0.8.3
> --dsh-version 0.2.0-rc.2 --accept-risk`，记录写在 profile 的 `compatibility.json`，
> 由 `scripts/install_ming_tea_plugins.sh` 按锁文件的 `versionExemptions` 自动授予）。
> 依据：经审计它实际 import 的 API —— `dsh-app-boot` 的 `loadOptionalPatches`/`readProfileManifest`/`resolveProfileDir`
> 与 `dsh-typert-protocol` 的 `Remote`/`TypertRemoteService` —— 在 0.2 全部存在
> （`dsh-app-boot` 导出面**只增不减**，另两个包导出**逐字节相同**）。
> **实测程度**：0.2 下商店页面正常渲染（目录列出 12551 条、分类、我们的「隐藏不兼容」默认开启、无控制台错误）；
> **「从商店真的安装一个第三方插件」这一步没有实测**。上游发布支持 0.2 的版本后应撤销豁免。

**已装入并在浏览器里跑通**：设置 → 随应用自带 → 插件商店，列出 12225 个插件（目录构建于
2026-09-26；指针声明 12283，去重后 12225），分类筛选（工具 5859 / 界面 3094 / 集成 1061 /
模型服务 650 / 工作流 637 / 主题 319 / 其他 605）、搜索、刷新、安装按钮、作者/星数/占用齐备。
商店自带「隐藏不兼容 1103」开关（默认关）——目录里 1103 个条目被判定与当前 DSH 不兼容。

**我们做的两处改造**（都在 `scripts/`，不 fork 商店源码本体）：

1. `patch_shop_typert_compat.mjs` —— 等价改写 codec key（`schema: X` → `create: () => X`），
   覆盖 `typert.host.js` / `typert.remote-client.js` / `client.js` 三份产物共 48 处；幂等、
   可 `--revert`、已接入安装脚本。**上游生成器/加载器对齐后应还原。**
2. `install_ming_tea_plugins.sh` 把 `catalogUrl` 钉到作者官方地址并写进 **profile patch**
   （该层在所有 bundle 层之后应用）。环境变量 `DSH_SHOP_CATALOG_URL` 因此不再能改向目录。

**独立复核的目录契约**：指针 JSON（`schemaVersion: 5`、`count: 12283`）+ 内容寻址的
`plugins.<sha256>.json`（10.46 MB，本地 sha256 与指针一致）；条目字段含 owner / repository /
version / integrity / license / stars / downloads / tier / verified；安装走 `dist.tarball` 且
**强制与声明的 registry 同源**（跨源直接拒绝）；商店支持 `schemaVersion ≤ 6`。

**仍然欠缺、需要我们自己补的**（与上文「必须补的控制」一致）：

1. 目录**不自持**：现在用的仍是作者目录，12225 个条目全部未经我们审计，且**没有任何沙箱**。
   自建白名单目录时只改 profile patch 里那一行 URL。
2. 「隐藏不兼容」**默认关**：建议后续把它默认打开（低配/家庭用户应只看到与当前 DSH 兼容的条目）。
3. 安装动作**没有二次确认弹窗**：商店点「安装」即走官方 CLI 安装通道（argv、`shell: false`），
   但没有 license / 版本 / integrity 的展示确认（商店条目里带这些字段，界面也展示了 license 与来源）。
4. **不写 `allowBuilds`** 这条商店本身满足（用官方 CLI + `--ignore-scripts` 由我们的安装脚本保证）。

结论不变：**商店可用，但仍不能当作"安全来源"** —— 它的价值是"发现 + 安装通道"，
真正的安全边界要由我们自建白名单目录 + 安装前确认来提供。

## 2026-10-02 批次：五社区插件装进铭荼（含 codex-guard vendoring）

用户点名装五个（原始清单里 `deja-vu`＝`dsh-deja` 是同一个插件、`dsh-context` 写了两遍）。
全部按 lock 驱动安装：`assets/ming-tea-dsh-lock.json` 固定版本 + `versionExemptions`（需要时）
+ `scripts/install_ming_tea_plugins.sh` 安装，机器可读记录进 `assets/ming-tea-community-candidates.json`。

| 包 | 版本 | 许可 | 运行时 | 权限（记在候选清单） | 状态 |
| --- | --- | --- | --- | --- | --- |
| `dsh-routing-suite` | 0.1.2 | MIT | dsh-web-client-bundle | `prompt.assemble`、`webserver.loopback.read` | 装上，**模式不露出**（见下） |
| `dsh-graphlint` | 0.4.0 | MIT | dsh-cordis-node | `workspace.read`、`process.exec.graphlint` | 装上，**只挂开发模式** |
| `dsh-deja` | 0.21.4 | MIT | dsh-cordis-node | `session-history.read.other-agents`、`file.write.deja-index`、`process.exec.deja` | 装上，**默认关闭** |
| `dsh-context` | 0.62.2 | Apache-2.0 | dsh-web-client-bundle | `session.read`、`context.insight.read` | 装上，正常渲染 |
| `dsh-codex-guard` | 1.9.0 | MIT | dsh-cordis-node | `workspace.read`、`git.read`、`process.exec.codex-guard` | **vendor 进仓库**，只挂开发模式 |

### 三个需要解释的取舍

1. **`dsh-routing-suite` 装了但不露出模式。** 它的宿主半区只在会话选中 `routing-suite`
   这个 agent preset 时才改写 system prompt，而它**不自注册 preset** —— 上游靠「官方 desktop
   安装器把 `preset/routing-suite` 物化到用户预设根」，而本 DSH 构建里**没有** `.agent-presets`
   这条路径（运行时代码零引用）。剩下的唯一办法是把它的 preset 当成一个 profile 行挂上，
   但那会让铭荼的模式菜单多出一个「智能路由模式」，内容却是**官方 standard 的英文
   coding-agent persona**（`You are a coding agent powered by the {{model}} model`），
   与我们的中文三场景形态直接冲突。所以本版本保持「装上、可审计、只提供只读状态接口」；
   要真正启用，需要把那份 preset 适配成第 4 个铭荼场景（persona、工具裁剪、主题），属另一轮工作。
2. **`dsh-graphlint` / `dsh-codex-guard` 只挂「开发模式」。** 两者注册的是模型可见工具
   （死代码检测三个、提交前卫生检查一个）。插件的 bundle patch 是把行插在 **profile 根**上的，
   根行会让工具对**所有场景**可见；因此我们在 **profile patch**（在所有 bundle 层之后）把这两个
   根行 `disabled: true`，改用 `platform/ming-tea/plugins/ming-tea-ui/scenes.config.mjs` 里
   开发场景的 `extraRows` 挂载。实测组合后的配置：`preset-ptc` 有两个插件行，
   `preset-standard`/`preset-minimal` 没有。
3. **`dsh-deja` 默认关闭**（用户明确选择）。profile patch 写的是 `- id: deja` + `disabled: true`
   （行 id 是 `deja`，不是包名 `dsh-deja`；行 id 写错会静默不生效）。要开启就删掉那两行并重启宿主。
   它同时会拉一个 **13.9 MB 的预编译原生二进制**（`@vshulcz/deja-vu-darwin-arm64@0.21.4`，
   integrity 记在 lock 的 notes 里）并索引本机其它 agent 的历史，所以即使开启也应视为高权限能力。

### `dsh-codex-guard` 为什么 vendor，以及改了什么

- **npm 上没有这个包**（只发布在 GitHub）。按我们自己的口径「依赖必须锁定、禁止 git/URL 安装」，
  把它的 `dsh/` 子树按 commit `87e138cdcca2e2929073bb913212dc06769087fc` 放进
  `platform/ming-tea/plugins/vendor-dsh-codex-guard/`，用 `link:` 安装（见该目录 `VENDOR.md`）。
- **唯一改动**：上游的工具实现是 `npx --yes codex-guard` —— 也就是**运行时按需去 npm registry
  拉一个包并执行**。改成执行锁里钉住的 `codex-guard@1.16.0`（作为 **runtime 包**安装，带 integrity），
  解析顺序：`MING_TEA_CODEX_GUARD_CLI` → `require.resolve` → `<DSH_HOME>/../node_modules/codex-guard`；
  解析不到就如实报错并提示重装，**不退回动态下载**。
- 顺带记一条 pnpm 语义（实测）：`link:` 与 `file:` 目标**都只建软链、都不会安装被链包自己的依赖**，
  所以 CLI 不能作为 vendored 包的 dependency 指望被自动装上 —— 它必须单独作为 runtime 包钉住。

### 兼容性豁免（新增一条）

`dsh-graphlint@0.4.0`：上游 peer 只声明 `@deepseek-ai/dsh-tools ^0.1.0-rc.6`（与 `cordis ^4.0.1`），
没有覆盖 `0.2.0-rc.2`。经审计它在 `@deepseek-ai/*` 上**只 import `defineTool`**（0.2.0-rc.2 的
`dsh-tools/lib/index.js` 里该导出仍在），其余能力全走宿主服务 `tools/subprocess/fs/skills/jobs/timer`
（`resolveExecutable` 由 `@deepseek-ai/dsh-subprocess-local` 提供、`timer` 由
`@deepseek-ai/cordis-plugin-timer` 提供，本运行时均有），故按官方 `allow-version` 机制接受风险。
**`dsh-codex-guard` 不需要豁免**：它的 peer 写成 `>=0.1.0-rc.1 <0.2.0 || >=0.2.0`，预检实测放行
（比严格 semver 宽松）。`dsh-context` 上游自己声明了 `0.2.0-rc.2: compatible`，也不需要。

### 主题适配（唯一一处真缺口）

`dsh-context`（21 处）与 `dsh-routing-suite` 的界面都用 `--dsw-alias-*` 令牌，因此本来就继承铭荼主题；
但它们引用的是**短名** `--dsw-alias-brand-primary`，官方把它定义成偏蓝的中性色
（`--dsw-static-neutral-bluish-1000`），而我们的调色板只覆盖了长名变体
`--dsw-alias-brand-primary-new-colorprimary-new-color`。已把短名补进
`platform/ming-tea/plugins/ming-tea-ui/theme/palette.mjs`；实测临时实例页面 `body` 上该令牌
= `#16857d`（深色 `#4fb3a4`），插件的强调色随铭荼品牌色。

### 实测边界（如实）

- **已验证**：安装与组合（`--dump-config` 里五个插件行与 disabled 状态）、宿主干净启动
  （无错误无警告）、`/routing-suite/api/status` 200、`/api/dsh-context/detail` 200、
  浏览器控制台 0 错误 0 失败请求、右侧栏出现 context 面板入口、`codex-guard` CLI 按插件形态
  跑通（`--git` / `--git --json`）、summon 紧凑模式里新插件 UI 全部不可见（真可见判定下只有宠物层）。
- **未验证**：`graphlint_*` 三个工具需要外部 `graphlint` CLI（本机没装，未跑通端到端）；
  `deja_*` 七个工具（默认关闭，未启用）；`dsh-context` 面板**内部**图表在真实会话下的渲染
  （只在落地页确认入口与零报错）；routing-suite 的模式（未露出）；Debian 13 与低配可用性
  （五个都**未在 Linux 上实测**，故都没进正式 ISO 清单，只留在开发树与候选清单里）。
