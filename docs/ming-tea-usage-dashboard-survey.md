# 「用量看板」选型调研：能不能复用现成插件？

> **版本说明（2026-10-01 追记）**：本文的契约核查基于 `0.1.7-rc.1`；铭荼 runtime 随后升到 `0.2.0-rc.2`，
> 已复核 `settings.section` 契约在 0.2 里**只新增**（多了 `settingsOpen`/`settingsShortcut`），注册形状不变，
> 因此本文的「自建」结论与扩展点仍然成立。选型结论本身不受版本影响（「没有现成插件可用」是数据源层面的判断）。

> **结论先行**：**不能。**铭荼的「用量」是**站点额度**（`sca-hub.cn` 的 `/usage`：Auto 赠送次数、付费层余额），
> 而生态里的「用量看板」插件几乎全部读 **DSH 本地会话日志**（「本次对话烧了多少 token」）——**不是同一个数**。
> 因此设置页「用量看板」按自建落地（见 `platform/ming-tea/plugins/ming-tea-ui/README.md` 的
> 「设置页『用量看板』」一节）。本文记录依据与唯一候选，避免后来者重复调研。

调研方式（2026-09-30，只读，未安装任何插件）：

- 商店目录**重新拉取** `https://LivXue.github.io/dsh-plugin-shop/v1/index.json` → `count 12609`（`builtAt 2026-09-29T07:11:44Z`），
  下载分片并核对 sha256 与 index 声明一致；另比对本地缓存 `<repo>/.ming-tea/runtime/dsh-home/shop/index.json`（12352 条）。
- 目录**元数据全字段**检索 `sca-hub` / `ming-tea` / `铭荼` / `auto_free` / `monthly_budget_rmb` → **零命中**（全库没有任何插件提及铭荼）。
- **源码级**解包阅读 17 个最有望的包（`npm pack` 到 `/tmp` 后读 `package.json` 与入口），逐个人工判定。

## 一、为什么现成插件用不上：数据源分层

396 个「用量/额度/统计」类插件里，按数据源分三类：

| 数据源 | 典型代表 | 对铭荼 |
| --- | --- | --- |
| **DSH 本地会话日志**（`chunk.type === "usage"` 的 `inputTokens`/`cacheReadTokens`） | `dsh-usage-panel`、`dsh-plugin-usage-stats`、`dsh-usage-statistics-panel`、`model-usage-plugin`、`dsh-cost-meter`、`dsh-token-meter-panel` | **不可用**：它统计的是「本机这次对话消耗」，站点额度是服务端账本，两者永远对不上 |
| **中转站账本**（按固定指纹识别 New API / Sub2API） | `dsh-tokenledger`（★202，生态最流行） | **不可用**（含实测证据，见下） |
| **固定厂商官方余额接口** | `dsh-token-plan-quota`、`@lcthe/dsh-usage-monitor`、`ccs-balance` | **不可用**：接口与我们的站点无关；且两个包 `engines`/peer 明确不含 `0.1.7-rc.1` |

**`dsh-tokenledger` 的实测反证**：它靠「路由存在/不存在」识别中转软件（要求不存在的路由返回 404）。
实测 `sca-hub.cn` 是 **SPA catch-all** —— `/api/status`、`/v1/usage`、`/api/usage/token`、`/api/log/self`、`/api/v1/usage`
**全部 `200 text/html`**，不满足判据，于是判为 unknown relay **不产出任何额度数字**
（作者原话：an unrecognized site is never guessed into a known adapter）。→ 指纹类插件对我们**结构性失效**。

**顺带确认**：`@michengai/*` 序列**没有任何用量看板插件**；`MichengAI` 在 GitHub 是 user 不是 org（`/orgs/MichengAI` → Not Found），
20 个仓库里无 usage 类；且 `@michengai` **未发布到公共 npm**（`scope:michengai` 搜索结果 `total 0`）。

## 二、唯一候选：`dsh-credits@0.4.0`（改造后可用，但不推荐）

它是唯一支持「**自定义 HTTP 接口**」的：任意 URL + Bearer/Token/Cookie 鉴权 + JSONPath 字段映射
（`valuePath`/`usedPath`/`totalPath`，`calculation: direct|subtract`）——刚好能表达我们的
`auto_free.{limit,used}` 与 `remaining_rmb`/`budget_rmb`。

**实测障碍**：

1. **peer 冲突**：它声明 `dsh-credentials: ^0.1.3-alpha.2`，用 node-semver 对 `0.1.7-rc.1` 推算 **不满足**（预发布规则）
   ⇒ `dsh plugin add` 会撞 peer 冲突（**未实机验证**是否真阻断）。
2. **别人的设置页**：装上去等于把铭荼的用量放进第三方 UI，档位/中文/只读口径都不受我们控制。
3. **会出现两个数**：它自带本地口径的消耗统计，与站点口径**并存**，用户看到两个「用量」。
4. 需要按我们的 `ROUTE_ID` 绑定供应商，且凭证要经它的 `credentialMode: reference` 指回 `MING_TEA_HUB_TOKEN`。

**其余候选**（`dsh-plugin-balance` 只有单个 `customUrl`、无字段映射层；`dsh-peak-balance` 的适配器硬编码在 `lib/providers/*`；
`dsh-gateway-wallet` **npm registry 404、非公共包、完全没读到源码**）均判定不可用或不划算。

## 三、自建的扩展点（已独立核实）

- **契约**：`@deepseek-ai/dsh-client-ui-settings@0.1.7-rc.1` → `lib/types/client/contract/slots.d.ts`：
  `'settings.section': { kind: 'list'; scope: 'root'; owner: SettingsSectionOwnerProps }`，
  `SettingsSectionOwnerProps = { close: () => void }`。注释原文：*"A feature owns its own settings pages —
  adding a setting never means editing the shell"*。
- **渲染方**：`@deepseek-ai/dsh-client-ui-settings-general@0.1.7-rc.1` → `lib/client.js:307`
  `renderSlot("settings.section", { close: onClose }, { only: active })`；`:981/:986/:996` 左导航由
  `ctx.slots.entries("settings.section")` + `subscribe` 构建 ⇒ **注册即出现，支持运行时摘挂**。
- **官方 order 基线**：`account` -10 → `general` 0 → `models` 10 → `plugins` 15 → `agent-presets` 20。
  我们用 `ming-tea-update` 90、`ming-tea-usage` 91 排在末尾，合理。
- **写法**：必须走 `ctx.slots.inject("settings.section", …)` 再 `slots.register({name, id, order, label, locale}, Component)`
  —— 该 slot 类型由 `ui-settings-general` 在运行时声明。铭荼已有两处同形状先例（`ming-tea-update`、本轮的 `ming-tea-usage`）。
- **官方 settings 域里没有「用量」节**：`account` 是 DeepSeek Platform 余额（与我们的站点额度无关），
  所以**必须自己注册一节**，不能挂靠。

**最值得抄的结构**（如果将来重构本页）：

1. `dsh-usage-panel@0.2.3` —— 与我们**架构同构**（bundle + 自有 RPC 通道 + `settings.section`）：
   `lib/client.js:1054-1065` 把 `ctx.connection.rpc` 透传给 section 组件；`:1066-1070` `ctx.effect` 卸载时清理 DOM 与 locale 订阅。
2. `dsh-plugin-usage-stats@0.4.3` —— 契约/席位文档最规范：`README.zh.md:55` 列出「认领席位」；`:63` 说明
   「总开关连 Tab：关闭即摘掉注册，左导航条目一起消失；读不到配置时 fail-open」。将来要加「隐藏本页」开关可照此做。
3. `dsh-credits@0.4.0` 的**额度映射模型**可借鉴：`QuotaMetric`（`src/index.js:546-551`）+
   轻量 JSONPath（`:668-704`）+ `normalizeCustomMetrics`（`:986-1030`）。
4. **不要抄**：`dsh-client-ui-settings-account/lib/client.js:2378-2391` 的「凭证就绪才显示」条件注册/注销 —— 我们常驻即可。

## 四、没验证到的（如实）

1. **未实机安装任何插件**（调研任务禁止安装）；兼容性均为包内声明或 node-semver 静态推算，
   `dsh-credits` 的 peer 冲突是否真阻断 `dsh plugin add` **未实测**。
2. `dsh-gateway-wallet` **完全没读到源码**（registry 404），其「站点账本」支持范围是**推断**。
3. `dsh-third-party-api-balance-wallet` / `dsh-usage-plus` 的「自定义」能力**未证实**（前者仅一句摘要；
   后者解包后 `lib/*.js` 里搜不到任何自定义 URL 取值实现）。
4. **「零命中」覆盖的是目录元数据全字段**（12609 条）+ 本地缓存 12352 条；**源码级只读了 17 个最有望的包**。
   「某冷门插件恰好支持我们的 schema」无法 100% 排除，但概率极低。
5. 商店星数文件是子集（连 `LivXue/dsh-plugin-shop` 都 ABSENT），故「ABSENT」≠ 0 星；
   候选星数另用 GitHub API 复核（TokenLedger ★202、usage-statistics-panel ★14、usage-panel ★9、plugin-balance ★6、credits ★2）。
6. `dsh-plugin-usage-stats` 的 repo 地址可疑（GitHub API `Not Found`），但 npm 包本身正常（0.4.3, 2026-09-20）。
7. 目录与缓存均有滞后（示例：`dsh-cost-meter` 目录 1.7.44 vs npm 1.8.0）⇒ 清单类判断要回 registry 复核。
8. **未验证 `locale: "ming-tea-ui"` 是否需注册语言包命名空间**（现有「检查更新」页同一写法，未实测）。
9. `sca-hub.cn` 指纹探测仅**无凭证 GET**（5 条路由均 `200 text/html`），证据强但非穷尽。
10. **未 A/B 验证**客户端是否必须声明 `dsh.client.inject: ["@deepseek-ai/dsh-client-ui-settings"]`
    （官方与 `dsh-usage-panel` 都没在我们的 `ui-tweaks.js` 里声明，而「检查更新」页据称可用 ⇒ 推测 `slots.inject` 自身会等声明）。
