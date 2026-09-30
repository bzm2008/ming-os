# 铭荼（ming-tea）× Scallion 站内能力对接说明

日期：2026-09-27
状态：**准备中，未发布**。本文只记录实测事实与对接契约，改动上线前必须按 §6 的清单逐条确认。

对端：`/Users/mac/.codex/worktrees/ming-tea-dsh-assistant/ming-os`（DSH 定制发行版「铭荼/明茶」），其共享状态文档是 `docs/ming-tea-live-status.md`。

---

## 1. 铭荼需要补齐的三个占位（对端现状）

| 占位 | 对端现状 | 本侧可提供的接口 |
| --- | --- | --- |
| 账户登录 | 按钮是占位，点击提示「尚未接入」 | Papyrus 设备授权（device code）→ 换取主站 JWT |
| 剩余用量（圆环百分比） | 显示 `—`，不伪造数值，预留 hook `MING_TEA_QUOTA_PERCENT` | `GET /api/papyrus/llm/quota` |
| 检查更新 | 设置里的占位入口，无 OTA 方案 | `GET /api/papyrus/update`（Tauri 签名清单）+ 页面下载路由 |

## 2. 登录：设备授权（现成，可参数化复用）

三个端点（生产已部署，授权段与仓库逐字一致）：

```
POST /api/papyrus/auth/device                 # 免登录
  → { deviceCode(32), userCode(6), verificationUrl, expiresIn:600, interval:3 }

GET  /api/papyrus/auth/device/:deviceCode     # 客户端轮询
  → { status:'pending' | 'approved' | 'expired' }
  → approved 时同一次响应里带 { token, user }，并立刻删除该记录（一次性）

POST /api/papyrus/auth/approve                # 浏览器侧，需登录
  body { deviceCode } 或 { code: userCode }，Header Authorization: Bearer <主站 JWT>
  → { ok: true }
```

- 记录存 Redis：`papyrus:auth:device:<deviceCode>`、`papyrus:auth:user-code:<USERCODE>`，TTL=10 分钟；Redis 不可用时退化为**进程内 Map**——PM2 是 2 个 cluster 实例，回落到 Map 时跨实例会失败。
- 换到的 token 就是主站 JWT（生产签发带 issuer `scallion` / audience `scallion-web`，**7 天**有效）。
- 授权页：`src/pages/PapyrusAuthorize.jsx`（读 `?device=`，未登录会带 `?redirect=` 跳 `/login`）。**当前线上该路由被改成跳回首页，整条链路无法完成授权**（见 §5）。

### 2.1 安全红线

**不要把 `INTERNAL_AI_API_KEY` 或任何站点密钥打进铭荼客户端。** 客户端可被反编译，密钥泄漏等于把厂商额度对外开放。正确做法是上面的设备授权：铭荼拿**用户自己的** token，额度按用户计。

## 3. 模型：免费层用 Auto

```
GET  /api/papyrus/llm/models   → 可用模型（按套餐过滤；Free 只有 Auto）
POST /api/papyrus/llm/chat     → { model:'auto' | '<modelId>', messages:[...], stream? }
GET  /api/papyrus/llm/quota    → { ...积分额度, auto:{monthly_limit,monthly_used,monthly_remaining,daily_limit,daily_used,daily_remaining}, plan:{...} }
```

- Free 套餐：`autoMonthlyCalls: 300`、`autoDailyCalls: null`（**日限额不限**）、`freeModels: ['Auto']`；Auto 由服务端选模型，客户端改 body 无权指定。
- Free 的 Auto 池（6 个，生产实测解析结果）：`deepseek-ai/deepseek-v4-flash-0731`、`moonshotai/kimi-k2.6`、`z-ai/glm-5.2`、`minimaxai/minimax-m3`、`openai/gpt-oss-120b`、`nvidia/nemotron-3-super-120b-a12b`。
- Auto 调用**不扣积分**，计 `dev_api_usage_logs` 里 `source='papyrus-auto'` 的记录。
- ⚠️ 需要客户端配合：这条接口是**主站 JWT + 站点侧额度**，不是 OpenAI 兼容端点。铭荼若要直接当 provider 用，需要在 adapter 里包一层（见 §4）。

## 4. 铭荼侧建议的接入形态

铭荼的 DSH 宿主接受「API Key + Base URL」式供应商配置，而本侧是 JWT + 自定义路径。两条路：

- **A（推荐，等本侧修好）**：在铭荼里写一个薄 adapter —— 用设备授权拿 token → 把宿主发出的 OpenAI 格式请求转成 `POST /api/papyrus/llm/chat`（`model:'auto'`），把返回的 `choices[0].message.content` 还原给宿主；登录状态与额度直接复用本侧，顺便把 `/llm/quota` 喂给「剩余用量」圆环。
- **B（不推荐）**：站点另开一个 OpenAI 兼容端点专供铭荼。等于新增一个对外模型网关，要独立做鉴权、限流、防滥用与配额，工作量与风险都大；除非铭荼要脱离用户登录跑，否则不建议。

## 5. 当前两个阻塞（都已定位，修复未发布）

1. **授权页被下线**：线上 bundle（8/31 构建）与本地构建里 `path="/papyrus/authorize"` 都是 `<Navigate to="/" replace />`，用户打开授权链接会被弹回首页 → 授权无法完成。历史构建（8/28 及更早）是真页面。
   - 已做：`src/App.jsx` 恢复 `import PapyrusAuthorize` + `<Route path="/papyrus/authorize" element={<PapyrusAuthorize />} />`（未提交、未构建）。
   - **代价**：恢复它需要重新构建前端，而重新构建会把工作树里其它未发布前端改动（「加入我们」一步加入 + 三级体系等）一起带上线。三条可选路径：
     - (a) 与「加入我们」改版一起发一次完整前端（最省事，但要按发布清单验收）；
     - (b) 从 HEAD（`5522b33`）拉临时工作副本、只带这一条改动单独构建、只替换 `dist/`（最接近线上现状的隔离发布）；
     - (c) 不做 React 构建，新增一个独立静态授权页（读 `localStorage.sc_token` 调 approve），只往 `dist/` 放一个文件，零重新构建风险（需要一个新路径，且要处理未登录跳转）。
2. **免费层 Auto 被 429 挡死**（生产实测，三个免费用户全部命中）：
   - 生产 `services/papyrusPlanAccess.js`（7/28）把 `autoDailyCalls: null` 当成 `0`，`assertPapyrusAutoAvailable` 要求 `monthly_remaining > 0 && daily_remaining > 0`，于是 Free 用户**每次 Auto 调用都抛 `429 auto_quota_exhausted`**；同一份接口却又对外声明 `auto_calls: "300 次/月，日限额不限"`。
   - 引擎本身没问题：`POST /api/internal-ai/v1/chat/completions` 实测 `model:"auto"` 正常返回内容（本次实测 22s、返回「在线」）。
   - 修复已在仓库里（`services/papyrusPlanAccess.js` 本地版有 `dailyUnlimited` 处理，243 行 vs 生产 151 行），配套 `routes/papyrus.js`（本地用 `withPapyrusAutoQuotaLock` 取代直接 assert，另有 `papyrusAutoRoutingCandidates` 的活模型过滤与轮换）。**这两个文件必须一起发**，本地版还顺带把 `defaultVersion` 从 1.1.0 提到 1.1.1。

## 6. 发布清单（未执行，待确认）

1. 后端：发 `routes/papyrus.js` + `services/papyrusPlanAccess.js`（两者耦合，需同批）。发完用本文 §7 的验证脚本确认 Free 用户 `assertPapyrusAutoAvailable` 从 BLOCKED 变为 PASS，且 `/llm/chat` 能返回内容。
2. 前端：按 §5 的 (a)/(b)/(c) 选一路恢复授权页；若选 (a) 要连带验收「加入我们」改版。
3. 发布方式遵循仓库纪律：文件级发布到 `/srv/scallion/current`，改 env 才需要 `pm2 delete` + `start`（本次不需要改 env）。
4. 回滚：保留改动前的 `routes/papyrus.js`、`services/papyrusPlanAccess.js` 与 `dist/`。

## 7. 本轮实测证据（2026-09-27）

```bash
# 授权链路（线上）
curl -sX POST https://sca-hub.cn/api/papyrus/auth/device
# → {"deviceCode":"895NSYWF…","userCode":"JFF97D","verificationUrl":"https://sca-hub.cn/papyrus/authorize?device=…","expiresIn":600,"interval":3}
curl -s https://sca-hub.cn/api/papyrus/auth/device/895NSYWF…
# → {"status":"pending"}
curl -sX POST https://sca-hub.cn/api/papyrus/auth/approve -d '{"deviceCode":"…"}'
# → HTTP 401 {"error":"请先登录"}

# Auto 引擎（服务器本机，不带用户、不扣额度）
POST http://127.0.0.1:5000/api/internal-ai/v1/chat/completions {"model":"auto",…}
# → HTTP 200 {"model":"auto","choices":[{"message":{"content":"在线"}}]}  耗时 22s（注入了站内知识，prompt_tokens=3841）

# 免费层额度（服务器本机，只读）
assertPapyrusAutoAvailable(dal, 1|2|112, free) → BLOCKED 429 auto_quota_exhausted
papyrusAutoQuota → { monthly_limit:300, monthly_used:0, monthly_remaining:300, daily_limit:0, daily_used:0, daily_remaining:0 }
```

## 8. 更新与下载（供铭荼的「检查更新」参考）

- `GET /api/papyrus/update` → Tauri 签名清单（线上 1.1.1），读取 `PAPYRUS_UPDATE_MANIFEST` 或 `public/downloads/papyrus/latest.json`；该路径实际是**指向 release 目录之外**的符号链接（`current/public/downloads/papyrus` → `/srv/scallion/downloads/papyrus`），所以发布新 release 不会丢清单。
- **静态地址 `https://sca-hub.cn/downloads/papyrus/latest.json` 现在 403**（nginx 以 `www` 身份穿不过 `current/public/downloads` 的 `750 root:scallion`）；客户端要用动态的 `/api/papyrus/update`。
- 铭荼要做自己的 OTA 时，复用这套「清单端点 + GitHub release 资源 + 签名」即可；需要独立清单文件与端点，不要挂在 Papyrus 的清单上（两者资产不同）。
- 已知不一致：`/api/papyrus/download/latest` 仍 302 到 GitHub **v1.1.0**，因为生产 `routes/papyrus.js` 的 `defaultVersion` 还是 1.1.0（仓库已改 1.1.1）——按 §6 发布后即对齐。

---

## 9. 端点选择（2026-09-27 决定：铭荼用 dev-api 的 Auto 端点）

铭荼直接用 **`POST /api/dev-api/v1/chat/completions`**（OpenAI 兼容，`model: "auto"`），不需要写适配层：

- 实测返回标准 OpenAI 结构（`{id,object,created,model,usage,choices,...}`，另带 `scallion_resolved_model`），`model:"auto"` 时服务端解析到 `gpt-5.4-nano` 并返回了内容（那次调用耗时 ~54s）。
- 也支持 `stream: true`（SSE，带 `X-Scallion-Resolved-Model` 头）。
- 内部走 `callListedModel(..., source:'dev-api')` → New API Auto 池（日志显示刷新到 14 个可用候选）。

**鉴权现实（必须知道）**：该端点用 **API Key**（`Authorization: Bearer <key>`，校验 `dev_api_keys`），而**创建 Key 需要 Deeper 套餐或已激活开发者**（`POST /api/dev-api/keys`，`apiAccessForUser(...).allowed`）。免费用户无法自助创建 Key。因此三种形态：

1. **运营方一把 Key**（最简单）：你自己账号建 Key，配到铭荼里。缺点：Key 一旦随客户端分发就等于公开，谁都能拿去烧你的额度 —— 若走这条，**不要硬编码进安装包**，做成用户可填的设置项，并接受泄漏风险。
2. **每用户自助**（最干净）：铭荼用设备授权登录（§2），用户在自己的 Deeper/开发者账号下建 Key 后填入。免费用户走不通这条。
3. **服务端代理**（最稳，但要开发）：铭荼自己出模型名给用户，由服务端持 Key 调 dev-api，额度按铭荼用户计 —— 相当于给铭荼做一个网关。

第 1 条能立刻跑通，第 3 条最安全。这条决策请确认。

## 10. 2026-09-27 发布记录（已完成并逐项验证）

| 项 | 结果 |
| --- | --- |
| 后端 Papyrus 额度修复 | 发布 `services/papyrusPlanAccess.js` + `routes/papyrus.js`（备份在 `/srv/scallion/backups/mingtea-release-20260927-101032/`）；免费用户由 `BLOCKED 429 auto_quota_exhausted` → 可用（`m_remain=300, daily_unlimited=true`） |
| Auto 池收敛 | `config/packagePlans.js` 移除 3 个失效型号，改为实测可用 4 个 |
| 授权页恢复 + 改版 | `src/pages/PapyrusAuthorize.jsx` 去 Papyrus 化（默认显示铭荼、薄荷色 `#16857d`、文案随 `?client=` 切换），`src/App.jsx` 恢复 `/papyrus/authorize` 并新增通用别名 `/device/authorize` |
| 前端全量发布 | 在服务器上用 vite 8.0.8 构建（`/srv/scallion/build/mingtea-20260927`），产物 `index-KZfxZkS1.js` / `index-BJDi2Zhi.css`，含未发布的「加入我们」改版与开发者三级体系 |
| 加入/三级后端 | 发布 `routes/join.js`、`routes/auth.js`、`services/developerAccess.js` 与新增 `services/developerTiers.js`、`services/githubContributions.js`；`POST /api/join/contributions/refresh` 由 404 → 401（已存在） |
| 数据库迁移 | `developer_profiles` 已加 `tier`/`commit_count`/`commit_synced_at`（0 行旧数据，无需回填） |
| 环境变量 | 新增 `MAINTENANCE_BANNER_DISABLED=1`（维护横条默认开启会让 `registrationBlocked()` 关掉密码注册，必须显式关闭）；随后 `pm2 delete` + `start` 让 env 生效 |
| 顺带修好 | `/api/papyrus/download/latest` 由 302→v1.1.0 变为 **v1.1.1**（此前新装用户拿 1.1.0、OTA 却升 1.1.1 的不一致）；`services/papyrusPlanAccess.test.js`、`services/papyrusChatAccess.test.js` 由 4+2 失败 → **17/17 通过**（测试改为从配置推导池） |
| 未发布（明确排除） | `server.js`（维护横条注入默认开启，会把全站挂上横条）、`middleware/adminAuth.js`/`middleware/auth.js`/`lib/socket.js`/`routes/photos.js`/`routes/payments.js`/`utils/xpay.js`（生产是更严的版本，覆盖会回退安全性） |

### 10.1 仍需你处理的上游问题（实测证据）

- **NVIDIA** 4 把 Key 里 3 把 `last_error` 为 **429 限流**；模型表 22/124 enabled 且多数型号已过期（`mistral-large-3`、`llama-3.3-70b`、`step-3.7-flash` 等实测 410 Gone）。
- **Agnes** 有 Key 被限流（`You've reached the API rate limit`）、有 Key `InternalServerError`。
- **DE5 New API** 一把 Key `用户额度不足, 剩余额度: $0.00`。
- **SiliconFlow** 仅 9/72 型号 enabled。
- 部分模型报 409「AI provider API key is not configured」（模型↔厂商映射缺 Key）。
- 结论：模型池需要一次系统性清理（下线型号、补齐/轮换 Key、重测 enabled 位）；在那之前，Auto 的稳定性取决于可用上游，铭荼只应把它当「免费层，尽力而为」，不要承诺强 SLA。

---

## 11. 「pm / 胖猫」端点澄清（2026-09-27 实测）

用户口中的 **"PM 端点" = 胖猫（pmcat）网关**，不是站内路径。证据：

- 站内 `NEW_API_BASE_URL=https://xn--wnup5g6so4wn.de5.net`，该 punycode 解码即「**我是胖猫**」。
- 用同一把 Key 请求 `https://pmcat.top/v1/models` 与 `https://xn--wnup5g6so4wn.de5.net/v1/models`，**模型列表完全一致**（11 个）→ 同一网关的两个域名。
- 站内 `NEW_API_KEYS` 里存的三把 Key，与用户提供的是**同一组**，即站内 "New API Auto" 走的就是胖猫。日志 `[New API Auto] probe refreshed 14 available candidates` 指的就是它。

### 11.1 Key 分组与各自能出哪些模型（逐个实测）

三把 Key 属于**不同分组**，模型通道按分组隔离（`/v1/models` 能列出 ≠ 能调用）：

| 模型 | Key A（default） | Key B（default/distributor） | Key C（Free） |
| --- | --- | --- | --- |
| `gpt-5.4-nano` / `gpt-5.4-mini` / `gpt-5.6-luna` | ✅ | 503 model_not_found | 503 |
| `nvidia/nemotron-3-super-120b-a12b` | 503 | 503 | ✅ |
| `z-ai/glm-5.3-flash` | 503 | 503 | ✅ |
| `moonshotai/kimi-k3` | 503 | 503 | ✅ |
| `deepseek/deepseek-v4-flash`、`claude-sonnet-4-5` | 503 | 503 | 503（只在列表里，无通道） |

- Key A 可见 11 个模型、Key B 33 个、Key C 58 个 —— **列表数量不代表可用数量**，必须逐个试。
- 站内 Auto 解析到 `gpt-5.4-nano` 与「Key A 能出 GPT 系列」一致；Papyrus Auto 池里唯一稳定的 `nvidia/nemotron-3-super-120b-a12b` 与「Key C 能出 NVIDIA 系列」一致。
- 结论：**站点与铭荼共用这一个上游**，只是入口不同（站点经 `/api/dev-api/v1/chat/completions` 的 Auto；铭荼可直接连 `https://pmcat.top/v1`）。

### 11.2 安全提醒

这三把 Key 已经出现在聊天记录里，等同于暴露：建议**尽快在胖猫后台轮换**，并且**不要硬编码进铭荼安装包**（客户端可被反编译）。若要让铭荼用户共享免费额度，优先考虑服务端代理（铭荼自己持 Key、按自己的用户计额度）。

---

## 12. 软件专用 Auto：站点转发（2026-09-27 已发布并实测）

按需求实现：**Key 不出服务器**、**必须登录**、**免费层 100 次/月**、**模型掉了自动切下一个**、**站内原 Auto 不动**。

### 12.1 铭荼怎么接（零适配层）

- **Base URL**：`https://sca-hub.cn/api/dev-api/software-auto/v1`
- **鉴权**：`Authorization: Bearer <站点 JWT>` —— 用设备授权（§2）换来的用户 token，**不是**胖猫 Key。Key 只存在服务器 env 里。
- **对话**：`POST /chat/completions`（OpenAI 格式，支持 `stream: true`）
- **可用模型**：`GET /models`（带每个模型的健康状态与实测延迟）
- **额度**：`GET /usage` → `{monthly_limit, monthly_used, monthly_remaining, exhausted, plan}`
- 响应头：`X-Scallion-Resolved-Model`（实际服务的模型）、`X-Scallion-Software-Auto-Attempts`（尝试次数）、`X-Scallion-Software-Auto-Remaining`（剩余额度）
- 响应体额外带 `software_auto: {model, latency_ms, attempts[]}`，便于客户端展示"这次用的是哪个模型"

### 12.2 选路与故障切换

- 候选池 = `services/softwareAutoPool.js` 的 `SOFTWARE_AUTO_PREFERENCE`（10 个，2026-09-27 逐个实测可用），**Mistral 全族排除**：
  `gpt-5.6-luna`、`gpt-5.6-sol`、`nvidia/nemotron-3-ultra-550b-a55b`、`nvidia/nemotron-3-super-120b-a12b`、`claude-haiku-4-5`、`gpt-5.4-mini`、`z-ai/glm-5.3-flash`、`moonshotai/kimi-k3`、`agnes-2.5-flash`、`gpt-5.4-nano`
- 健康表存 Redis（`software-auto:health`，TTL 15 分钟）：**实测可用且最快**的排最前 → 未探测的居中 → 实测失败的沉底；表过期会在请求时后台并发重刷（不阻塞当次请求）
- 单次请求最多尝试 4 个候选；**同一模型若报 `model_not_found` 说明是分组没通道，不再换 Key，直接换模型**；其他错误会先换 Key 再换模型
- 每次成功/失败都写健康表，所以"掉线"会被后续请求自动绕开，直到下次探活发现它恢复

### 12.3 额度规则

- **免费层：100 次/月**（`SOFTWARE_AUTO_FREE_MONTHLY_LIMIT` 可覆盖），按 `dev_api_usage_logs` 里 `source='software-auto'` 的当月成功调用计数
- 付费套餐：沿用该套餐 `autoMonthlyCalls`（Briefly 1000 / Futher 2500 / Deeper 5000）
- 超额返回 `429 {code:'software_auto_quota_exhausted'}`，下月 1 日重置
- **站内 Papyrus Auto 完全不变**（那条走 `PAPYRUS_AUTO_MODEL_IDS` + NVIDIA/SiliconFlow，额度也是另一套）

### 12.4 实测证据（2026-09-27）

```
GET  /api/dev-api/software-auto/v1/models  (无 token) → 401 {"error":"请先登录"}
POST .../chat/completions                  (无 token) → 401

真实探活 10 个候选（并发，25.6s）：10/10 全部可用（当次延迟 1.2s ~ 24.0s）
真实调用：✓ 服务模型 moonshotai/kimi-k3，1 次尝试
强制首选失败：claude-sonnet-4-5-20250929（Key A → do_request_failed，Key C → model_not_found）
            → 自动切到 gpt-5.6-luna ✓ 返回「可用」，共 3 次尝试
免费层额度：{monthly_limit:100, monthly_used:0, monthly_remaining:100, exhausted:false}
```

注：探测时给 reasoning 模型的 `max_tokens` 太小会拿到空内容（思考 token 把预算吃光），客户端请给常规预算（实测 200 即正常）。

---

## 13. 铭荼的更新（OTA）与下载通道（2026-09-27 已就绪）

沿用 Papyrus 的约定：**安装包与清单放在 release 之外的共享目录**，release 内用符号链接暴露，所以发布新版本不会丢清单。

### 13.1 对外 URL

| 用途 | URL |
| --- | --- |
| 更新清单（Tauri updater endpoint） | `https://sca-hub.cn/downloads/ming-tea/latest.json` |
| 安装包 | 放进该目录后直接 `https://sca-hub.cn/downloads/ming-tea/<文件名>` |
| 也可以 | 清单里的 `url` 指向 GitHub Release（Papyrus 就是这么做的） |

服务端位置：`/srv/scallion/downloads/ming-tea/` ← 符号链接 `/srv/scallion/current/public/downloads/ming-tea`；nginx `location ^~ /downloads/` 已 alias 到 `current/public/downloads/`。

### 13.2 发布步骤

1. 把安装包与 `.sig` 放进 `/srv/scallion/downloads/ming-tea/`（或上传 GitHub Release）；
2. 以 `latest.json.example` 为模板写 `latest.json`（版本号必须等于客户端 `tauri.conf.json` 里的版本，`signature` 必须是本次构建的真实签名，否则 Tauri 会拒绝更新）；
3. `chown root:www && chmod 644`；
4. 验收：`curl -fsS https://sca-hub.cn/downloads/ming-tea/latest.json`，再在客户端点一次「检查更新」。
5. 目录里已放 `README.md` 与 `latest.json.example` 作为现场说明。

### 13.3 本次清理与权限修复

- **删除了 Papyrus 的陈旧应用包**（先备份到 `/srv/scallion/backups/papyrus-package-cleanup-*`，含 11.4MB 全量 tar）：`Papyrus_0.1.1_x64-setup.exe`(+`.sig`)、`Papyrus-0.1.1-portable-win-x64.zip`、`wps/Papyrus-WPS-Addin_0.1.1.zip`（共享目录与 release 内各一份重复），以及 `dist/downloads/papyrus/latest.json`（陈旧 1.1.0 清单）和 macOS 垃圾文件 `._latest.json`。
  - 删除依据：前端 bundle 不引用这些静态路径；三个下载接口全部 302 到 GitHub 的 **1.1.1**；而这些静态文件此前是 **403**（见下），等于无人可访问的死文件。
- **保留了** `/srv/scallion/downloads/papyrus/latest.json`（1.1.1，`/api/papyrus/update` 正在读它），Papyrus 的 OTA 未受影响（验收：`/api/papyrus/update` 200、两个 download 302）。
- **修复权限**：`/srv/scallion/current/public/downloads` 原为 `750 root:scallion`，nginx 以 `www` 身份无法穿越 → 该目录下所有静态文件都是 403。改为 `755` 后 `/downloads/papyrus/latest.json` 由 403 变 **200**，铭荼的静态通道也就可用了。

### 13.4 待你决定

要不要把 **Papyrus 的动态更新接口也切给铭荼**（`/api/papyrus/update` → 读 ming-tea 的清单）？**我没有擅自切**，因为现存的 Papyrus 1.1.1 客户端还在轮询它，切了会让它们的更新检查指向铭荼的包。三条路：

1. **两条并存**（当前状态）：Papyrus 走 `/api/papyrus/*`，铭荼走 `/downloads/ming-tea/latest.json` —— 互不影响；
2. **Papyrus 正式退役**：确认没有存量用户后，把 `/api/papyrus/update` 与两个 download 接口下线或改指铭荼；
3. **新建动态接口**：给铭荼加 `/api/ming-tea/update`（与 Papyrus 同构，支持按平台 302 跳转），需要改 `server.js` 挂载。

铭荼侧只要拿到安装包就能自测（清单 URL 已经可用）。**注意**：铭荼的包名、签名与版本号由铭荼仓库那边（Tauri 构建）产生，我这边只提供通道，不替它们生成签名。

---

## 14. Papyrus 正式退役 + 通道切换（2026-09-27 已执行）

### 14.1 改了什么

| 项 | 之前 | 现在 |
| --- | --- | --- |
| `/api/papyrus/update`、`/api/papyrus/download/latest`、`/api/papyrus/wps/download/latest`、`/api/papyrus/wps/update` | 提供 Papyrus 1.1.1 清单与 GitHub 下载跳转 | **一律 410** `{code:'papyrus_retired', successor:'ming-tea'}` —— 既不让存量客户端继续拉 Papyrus 的包，也不把它们指向铭荼（两个产品不兼容，铭荼不是 Papyrus 的升级目标） |
| `/api/ming-tea/update` | 不存在 | **新增**：返回 `downloads/ming-tea/latest.json`（未发布时 `404 {code:'ming_tea_release_not_published'}`） |
| `/api/ming-tea/download/latest?platform=&arch=` | 不存在 | **新增**：按平台从清单里取地址并 302（清单是唯一事实来源，不硬编码仓库路径） |
| `/srv/scallion/downloads/papyrus/latest.json` | Papyrus 1.1.1 清单（静态可访问） | **已下掉**（静态 URL 404） |
| 代码 | `routes/papyrus.js` 里 513 行含发行辅助函数 | 发行辅助函数（`defaultVersion`/`papyrusDesktopReleaseUrl`/`readUpdaterManifest` 等）**整簇删除**，文件降到 446 行 |
| `server.js` | 本地领先但未发布（维护横条 + `index:false`） | **已发布**（横条由 `MAINTENANCE_BANNER_DISABLED=1` 关闭，实测首页无横条），用于挂载 `/api/ming-tea` |

**明确没动的部分**：`/api/papyrus/llm/*`（模型与额度）与 `/api/papyrus/auth/*`（设备授权登录）**继续可用** —— 铭荼现在就靠它们换 token 和调模型。退役的是「应用发行通道」，不是接口面。若后续要把这两组也改挂 `/api/ming-tea/*`，属于单独的改名工作（会破坏在用客户端），需要另行决定。

### 14.2 验收（公网实测）

```
/api/ming-tea/update                              → 404 ming_tea_release_not_published（未发布时的诚实回答）
/api/ming-tea/download/latest?platform=windows     → 404 同上
/api/papyrus/update | download/latest | wps/*      → 410 papyrus_retired（四条）
/api/papyrus/auth/device                           → 200（设备授权仍可用）
/api/papyrus/llm/models                            → 401（未登录，接口仍在）
/downloads/papyrus/latest.json                     → 404（清单已下）
/downloads/ming-tea/README.md | latest.json.example → 200
公网首页                                           → 200，且无维护横条（grep 计数 0）
/api/health                                        → ok；PM2 三进程 online；错误日志无本次相关报错
```

备份：`/srv/scallion/backups/mingtea-cutover-20260927-111718/`（`server.js`、`routes/papyrus.js`、`papyrus-latest.json`）。回滚 = 把这 3 个文件放回原位并重启。

### 14.3 铭荼侧现在该怎么接

1. **更新**：Tauri updater endpoint 填 `https://sca-hub.cn/api/ming-tea/update`（或静态 `https://sca-hub.cn/downloads/ming-tea/latest.json`，两者同一份清单）；
2. **下载**：`https://sca-hub.cn/api/ming-tea/download/latest?platform=<windows|darwin|linux>&arch=<x64|arm64>`，或直接用清单里的 `url`；
3. **发布**：把安装包与 `.sig` 放进 `/srv/scallion/downloads/ming-tea/`，照 `README.md` 写 `latest.json` —— 放好即生效，不需要改代码或重启。

---

## 15. 付费层：三档套餐 + 官方额度计量（2026-09-27 已发布）

### 15.1 档位（config/softwareTiers.js）

| 档位 | 售价 | 官方月度额度 | 权益类型（发码用） |
| --- | --- | --- | --- |
| Plus | ¥19.9/月 | ¥120 | `plus` |
| Pro | ¥39.9/月 | ¥280 | `pro` |
| Ultra | ¥129/月 | ¥900 | `ultra` |

- 额度即上游成本（不做加价倍率）：**「官方额度就是实际可用的额度」**。
- 权益沿用现有机制：用 `activation_codes.entitlement_type = plus/pro/ultra` 发码，用户兑换后写入 `user_package_entitlements`，即刻生效——**不需要改购买链路**。
- 同时持有多档时取额度最高的一档；过期自动回落免费层（旧 Papyrus 档位 briefly/futher/deeper **不参与**软件付费层）。

### 15.2 计费与价目表

- **优先用上游回报的实际成本**（我们付给上游的成本价）：`usage.cost` / `gateway_cost` / `market_cost` / `cost_details.upstream_inference_cost`，取其中最大非零值——BYOK 场景上游只回报 `cost_details`，已覆盖。
- 上游没回报成本时，用 `config/paidModelPrices.js` 的**公开价目表**按 token 兜底；**缓存命中单独计价**。
- 汇率 `PAID_MODEL_USD_TO_RMB`（默认 7.0）：实测上游把人民币官方价按 7.0 折成美元记账（step 两次样本与官方 ¥1.35/¥8.1 精确吻合）。

公开价目表（来源：各厂商官方定价页，2026-09-27）：

| 模型 | 输入（未命中） | 缓存命中 | 输出 | 核对方式 |
| --- | --- | --- | --- | --- |
| `deepseek-v4.1-flash` | $0.15/M（peak 翻倍 $0.30） | $0.003/M | $0.60/M | 官方页 off-peak 价，**两次样本精确相等**（`measured-exact`） |
| `step-3.7-flash` | ¥1.35/M | ¥0.27/M | ¥8.1/M | 官方页价 ÷7 与上游回报一致（`measured-cross-checked`） |
| `mimo-v2.6-flash` | ¥0.98/M | **未知** | ¥1.96/M | 官方页为 JS 渲染读不到，由成本明细反推（`derived-from-cost`）；缓存命中价未知时**不要用本表兜底**，必须依赖上游回报 |

### 15.3 端点行为（同一组 URL，按档位分流）

铭荼侧不用区分免费/付费，仍是 `https://sca-hub.cn/api/dev-api/software-auto/v1`：

| 端点 | 免费层 | 付费层（Plus/Pro/Ultra） |
| --- | --- | --- |
| `GET /models` | 胖猫 Auto 池（健康表排序） | MaxAPI 三个模型 + 价目表 |
| `GET /usage` | 次数（100/月） | `{budget_rmb, used_rmb, remaining_rmb, used_percent}` |
| `POST /chat/completions` | 胖猫 Auto，按次数 | MaxAPI，按人民币额度；超额 `429 software_tier_quota_exhausted`；模型不在白名单 `400 paid_model_not_allowed` |
| 响应头 | 原样 | 额外 `X-Scallion-Tier`、`X-Scallion-Cost-Rmb`、`X-Scallion-Remaining-Rmb` |

### 15.4 实测验收

```
公开价目表：3 个模型（含缓存命中价与人民币口径）✓
真实付费调用：deepseek-v4.1-flash → "可用"，70 tokens，成本 $0.00002625 = ¥0.000184（upstream_reported）
              step-3.7-flash     → 调用成功，57 tokens，成本 $0.00004940 = ¥0.000346
档位与额度：user1 无权益 → 回落免费层（不影响现有用户）；Plus 额度视图 {budget 120, used 0, remaining 120}
HTTP：/software-auto/v1/* 未登录 401；健康检查 ok；错误日志无付费层相关报错
测试：services/softwarePaidPool.test.js 11/11 通过（含缓存命中计价、汇率、成本提取、白名单）
```

备份：`/srv/scallion/backups/paid-layer-20260927-115641/`（`routes/devApi.js` + `scallion.env.bak`）。

### 15.5 ⚠️ 待确认：这个 key 的分组只有 3 个模型，没有 GLM/Kimi

用户记得付费层「还有 GLM 和 Kimi 等」，但用这个 key 把能想到的探测方式都试过，结论是**该 key 的分组只暴露这 3 个模型**：

| 探测方式 | 结果 |
| --- | --- |
| `GET /v1/models`（Bearer / x-api-key / api-key / `?group=` / `?key=` / 无鉴权） | 只有 3 个；无鉴权 401 `API_KEY_REQUIRED` |
| `GET /models`、`POST` 取列表 | 同上 3 个 |
| 直接调用 19 个型号（GLM：`glm-5.3-flash`/`glm-5.2`/`glm-4.6`/`glm-5`/`z-ai/glm-4.6`/`zhipu/glm-5.3-flash`/`GLM-5.3-Flash`；Kimi：`kimi-k3`/`kimi-k2.6`/`moonshotai/kimi-k3`/`moonshot/kimi-k3`/`Kimi-K3`；以及 Qwen/Claude/GPT） | 全部 404，错误原文：`Model "X" is not supported by any configured account in **this group**` |
| 网关价目/目录接口（`/api/pricing`、`/api/v1/*` 等 8 个候选） | 全 404 |
| 浏览器打开 `/pricing` | 是**登录页**（目录要登录后台才可见） |
| 前端 bundle 分析 | 是**管理控制台**（全是 `/admin/channels`、`/admin/accounts/upstream-billing-rates` 等），渠道/分组配置在后台 |

结论：这是「多上游账号聚合成渠道」的网关，**模型可见性由 key 所在分组决定**。需要你在 `ai.max66.xyz` 后台确认：① 这个分组下是否有 GLM/Kimi 渠道；② 若有，哪把 key（哪个分组）能调到——把那样的 key 给我，或把现有 key 换到含 GLM/Kimi 的分组。代码侧已就绪：`config/paidModelPrices.js` 加行即可（Kimi 官方价已拿到：`kimi-k3` 缓存命中 ¥2/M、输入 ¥20/M、输出 ¥100/M；智谱官方页是 JS 渲染，需要时我可以从别处取或由你提供）。

---

## 16. 更正 §15.5：付费分组其实有 8 个模型（登录后台后查清）

登录 `ai.max66.xyz` 后台后确认：**§15.5 里「这个 key 只有 3 个模型」的结论是错的**，那是「当前可路由的模型」而不是「分组里的模型」。完整事实：

### 16.1 后台证据

| 页面 | 内容 |
| --- | --- |
| `/keys` API 密钥 | 账号下**只有一把 key** = 我们用的这把（id 1，`sk-36d…9913`），分组 **`国模不降质0.1x ds4.1 glm k3都有`**（标 0.1x），2026/09/18 创建，今日用量 $4.1613 |
| `/model-plaza` 模型广场 | 该分组公布 **8 个模型**（含 GLM/Kimi），带「实付价格(折后)」与「官方价格」两列、折扣倍率 0.1x |
| `/usage` 使用记录 | 近 24h 真实调用过 **`glm-5.3`（14 次）、`glm-5.2`（2 次）、`kimi-k3`（4 次）** 与 `deepseek-v4.1-flash`（1,626 次）——**同一把 key** |
| `/monitor` 渠道状态 | 只监控到 **DeepSeek 一条渠道**（正常，7 天可用率 89.58%，延迟 ~1.1s）；GLM/Kimi 无监控项 |

### 16.2 关键经济事实：实际成本 = 官方价 × 0.1

后台把「标准 $46.5466」与「实际 $4.6511」分列，**比值正好 10.0** → 该分组按**官方价的一折**结算。而 `usage.cost` 回报的是**标准价（官方价）**，所以：

- **对用户计量用官方价**（=「官方额度就是实际可用的额度」）✓ 已实现；
- 我们的实际支出约为其 **1/10**，这就是毛利空间。

### 16.3 公开价目表（8 个模型，取自模型广场「官方价格」列 + 厂商官方页核对）

| 模型 | 官方价 输入 / 输出 / 缓存读 | 折后实付（0.1x） | 当前可否调用 |
| --- | --- | --- | --- |
| `deepseek-v4.1-flash` | $0.15 / $0.60 / $0.003 | $0.20 / $0.80 | ✅ 可用（实测精确吻合） |
| `step-3.7-flash` | ¥1.35 / ¥8.1 / ¥0.27（官方页） | $0.135 / $0.81 | ✅ 可用 |
| `mimo-v2.6-flash` | $1.00 / $2.00（按 0.1x 反推） | $0.10 / $0.20 | ✅ 可用 |
| `kimi-k3` | $2.948 / $14.740 / $0.295 | $0.30 / $1.50 | ❌ 暂无渠道 |
| `glm-5.3` | $1.40 / $4.40 / $0.26 | $0.80 / $2.80 | ❌ 暂无渠道 |
| `glm-5.2` | $1.179 / $4.127 / $0.295 | $0.14 / $0.44 | ❌ 暂无渠道 |
| `deepseek-v4-pro` | $0.66 / $1.98 / $0.022（厂商官方页；广场该行疑似抄错） | $0.90 / $2.70 | ❌ 暂无渠道 |
| `deepseek-v4-flash` | $0.15 / $0.60 / $0.003 | $0.30 / $0.90 | ❌ 暂无渠道 |

`config/paidModelPrices.js` 已按此表扩到 8 个模型（每条带 `verified` 标记：`measured-exact` / `measured-cross-checked` / `derived-from-cost` / `gateway-published` / `vendor-official`，不可用的标注 `unavailableReason`），已发布到生产。

### 16.4 待处理：GLM/Kimi 等 5 个模型当前无可用渠道

用**正确 id**（`glm-5.3`/`glm-5.2`/`kimi-k3`/`deepseek-v4-pro`）在 `/v1/chat/completions` 与 `/v1/messages` 两个端点上实测，均返回：

```
Model "..." is not supported by any configured account in this group
```

即**分组里有这些模型、但此刻没有可用渠道**（广场备注也写着「503限流联系群主 一般都不会死」）。这属于网关侧（上游账号/渠道）状态，不是权限或代码问题：**渠道恢复后无需改任何代码**，`/models` 会直接列出并可按官方价调用。建议：找群主确认 GLM/Kimi 渠道何时恢复，或把 key 切到「国模稳定备用0.125x」分组试试（后台 key 行有「选择分组」按钮，两个分组都列同样的 8 个模型，倍率 0.1x / 0.125x）。

---

## 17. 铭荼的产品页与套餐页（2026-09-27 已上线）

在退役的 Papyrus 页面上改造而来（骨架、`PageShell/SectionHeader/Surface` 组件、视觉语言都沿用），并新增了一个公开的套餐/价目接口给前端取数。

### 17.1 页面

| 路径 | 内容 | 说明 |
| --- | --- | --- |
| `/ming-tea` | **产品介绍页**（新增 `src/pages/MingTea.jsx`） | 首屏定位、能力六宫格、额度区（Free + Plus/Pro/Ultra）、**付费模型价目表**（含「暂不可用」标注）、登录/授权说明；下载按钮按发布状态自动切换（未发布时显示「安装包即将发布」） |
| `/papyrus` | 旧地址 | 已改为 **302 到 `/ming-tea`**（Papyrus 退役，旧链接不再落到首页） |
| `/recharge` | **套餐页**（改造 `src/pages/Recharge.jsx`） | 套餐区由旧的 Papyrus 四档换成 **铭荼三档**（卡面：价格 / 官方额度 / 说明 / 扣费方式 / 重置日 / 激活方式），保留兑换码与点数包区；卡片按钮：配了 `purchase_url` 就跳购买，否则滚动到兑换码输入框 |
| 导航 | `src/components/Navbar.jsx` | 新增「铭荼」入口（MingOS 之后） |

### 17.2 新增公开接口 `GET /api/ming-tea/tiers`

前端不再抄一份配置，套餐/价目/发布状态都从这里读（无需登录）：

```json
{
  "product": { "name": "铭荼", "release": { "published": false, "version": null, "targets": [] } },
  "free": { "monthly_calls": 100, "models": [ ...10 个 Auto 池模型... ] },
  "tiers": [ { "key": "plus", "name": "Plus", "price_rmb": 19.9, "monthly_budget_rmb": 120, "purchase_url": "", "description": "..." }, ... ],
  "models": [ { "id": "deepseek-v4.1-flash", "input_rmb_per_1m": 1.05, "cache_hit_rmb_per_1m": 0.021, "output_rmb_per_1m": 4.2, "unavailable_reason": "" }, ... ]
}
```

- 档位与价格来自 `config/softwareTiers.js`（改一处，页面与计费同时生效）；
- 价目来自 `config/paidModelPrices.js`（含缓存命中价与不可用说明）；
- 免费层次数读 `SOFTWARE_AUTO_FREE_MONTHLY_LIMIT`；
- 发布状态读 ming-tea 清单（未发布 = 页面显示「安装包即将发布」）。

### 17.3 购买链路怎么接

套餐卡默认没有外链（`purchase_url` 为空），点击会滚到兑换码输入框。**建好爱发电商品后**，在 `config/softwareTiers.js` 对应档位填 `purchaseUrl` 即可，页面立刻变成「购买 Plus」按钮，无需改前端：

```js
{ key: 'plus', ..., purchaseUrl: 'https://www.kufaka.com/item/xxxx' }
```

发码时把 `activation_codes.entitlement_type` 设为 `plus` / `pro` / `ultra`，用户兑换后权益写入 `user_package_entitlements`，付费层即刻生效（不需要改购买链路）。

### 17.4 验收（线上）

```
GET /api/ming-tea/tiers      → 200，三档 + 免费层（100 次/月）+ 8 个模型价目（5 个标注暂不可用）
浏览器打开 /ming-tea          → 标题「把 AI 助手装进你自己的电脑」、三档额度、价目表（¥1.05/¥1.35/¥0.98…）、下载区显示「安装包即将发布」
浏览器打开 /recharge          → 「铭荼套餐」区渲染出 Plus/Pro（主推）/Ultra 三张卡，兑换码与点数包区正常
/papyrus                     → 跳到 /ming-tea
首页与站点健康                 → 200 / ok
```

---

## 18. 「暂不可用」是怎么来的，以及它现在会自己变回来

### 18.1 为什么 GLM / Kimi 显示暂不可用

**是真实状态，不是页面 bug**：这三家 8 个模型里，只有 `deepseek-v4.1-flash`、`step-3.7-flash`、`mimo-v2.6-flash` 当前有可用渠道；`kimi-k3`、`glm-5.3`、`glm-5.2`、`deepseek-v4-pro`、`deepseek-v4-flash` 调用会返回网关原话：

```
Model "..." is not supported by any configured account in this group
```

含义是「分组里配置了这些模型，但此刻没有可用渠道」（网关是按「分组 + 渠道」控制可路由性的聚合网关；后台 `/usage` 显示这几个模型近 24h 确实被调用过，`/monitor` 只监控到 DeepSeek 一条渠道）。这属于网关侧渠道状态，需要找群主确认恢复时间，或把 key 切到 `国模稳定备用0.125x` 分组试试。

### 18.2 之前的实现有个缺陷（已修）

第一版把「不可用」写死在 `config/paidModelPrices.js` 的 `unavailableReason` 里 —— 那样渠道恢复了页面也不会变。现在改成**实时探测**：

- `services/softwarePaidPool.js` 新增 `probePaidModel` / `refreshPaidHealth` / `paidModelAvailability`，探测结果存 Redis `software-paid:health`（TTL 15 分钟）；
- `/api/ming-tea/tiers` 与软件 Auto 的 `/models` 都会带上 **`available` / `probed` / `checked_at` / `latency_ms` / `last_error`**；
- 请求发现快照过期时**在后台并发刷一轮**（去重、不阻塞当次请求），所以渠道一恢复，最迟 15 分钟内页面会自动变回「可用」，无需改代码或重启；
- 配置里的静态 `unavailableReason` 只作为**未探测时的回落说明**保留。

### 18.3 实测（2026-09-27 部署后）

```
GET /api/ming-tea/tiers →
  deepseek-v4.1-flash  可用  1752ms      step-3.7-flash       可用  2060ms
  mimo-v2.6-flash      可用  2138ms      kimi-k3              不可用（model_not_found）
  glm-5.3 / glm-5.2 / deepseek-v4-pro / deepseek-v4-flash     不可用（同上）

浏览器 /ming-tea 价目表：可用项显示「可用」（悬停看实测延迟），不可用项显示「暂不可用」（悬停看网关原文）
```

备份：`/srv/scallion/backups/paid-availability-20260927-123201/`（三个后端文件）。

---

## 19. 命名口径：对外一律写「实际可用额度」

2026-09-27 用户指定：页面与面向用户的提示里，**不要写「官方额度」，一律写「实际可用额度」**（含义不变：按厂商官方价计量、预算即上游成本、不做加价倍率）。

已改到的位置：

- `src/pages/MingTea.jsx`：额度区标题、说明、档位卡上的额度数字；
- `src/pages/Recharge.jsx`：套餐区标题、说明、TierCard 的额度行（原来写「模型额度 / 按官方价扣」的那行也改成「实际可用额度 / ¥X/月」）；
- `routes/devApi.js`：付费额度用尽时的 429 提示（`本月实际可用额度已用完（¥used/¥budget）`）。

代码注释与内部文档里仍可写「官方额度」（表示按官方价计量），但**不要让它出现在用户可见的文案里**；`config/softwareTiers.js` 顶部已记录这条口径。

验收：线上产物里 `官方额度` 出现 0 次、`实际可用额度` 已在额度区与三张档位卡上渲染（浏览器实测）。

---

## 20. 登录体验改版：已登录就自动授权（2026-09-27）

### 20.1 现在是什么体验

用户在铭荼里点「打开授权页」→ 浏览器打开 `verificationUrl` → **如果站点已登录（或登录后跳回来），页面直接完成授权并显示「已授权 铭荼」**，不需要点按钮，也不需要输入任何验证码。用户唯一要做的就是关掉页面回到客户端。

### 20.2 为什么保留了一个手动确认的兜底

设备码是**免登录**申请的，任何人不登录都能生成一个。如果无条件自动授权，攻击者可以自己申请一个设备码、把链接发给已登录的用户，用户一打开就把**自己的 token** 交给了攻击者（设备码钓鱼）。所以：

- 设备记录里现在会保存**申请设备码时的出口 IP**（`createdIp`，取自 `X-Forwarded-For`）；
- `POST /auth/approve` 支持 `{ auto: true }`：**只有当前浏览器 IP 与申请时 IP 相同**才自动同意；
- IP 不同（例如在另一台设备/网络上确认）时返回 `409 {code:'confirm_required'}`，页面退化成「点一下同意授权」——**仍然不需要验证码**；
- 旧记录（本次改动前创建的）没有 `createdIp`，一律走手动确认（10 分钟后自然过期）。

判定函数 `canAutoApprove(record, ip)` 与 `clientIp(req)` 都有单测（`routes/papyrusDeviceAuth.test.js`）。

### 20.3 铭荼的品牌化鉴权别名

`server.js` 在发布路由之后把同一套设备授权/模型逻辑又挂到了 `/api/ming-tea`：

```js
app.use('/api/ming-tea', mingTeaReleaseRoutes())
app.use('/api/ming-tea', papyrusRoutes(dal, { authorizePath: '/device/authorize', client: 'ming-tea' }))
```

所以铭荼可以完全用自己品牌下的路径，verificationUrl 也会指向铭荼的授权页：

| 用途 | 铭荼路径（新，推荐） | Papyrus 路径（旧，仍可用） |
| --- | --- | --- |
| 申请设备码 | `POST /api/ming-tea/auth/device` | `POST /api/papyrus/auth/device` |
| 轮询取 token | `GET /api/ming-tea/auth/device/:deviceCode` | `GET /api/papyrus/auth/device/:deviceCode` |
| 同意授权（页面用） | `POST /api/ming-tea/auth/approve` | `POST /api/papyrus/auth/approve` |
| 模型与额度 | ⚠️ **新客户端不要用**：`/api/ming-tea/llm/*` 只是 Papyrus 兼容别名（Papyrus 套餐/积分口径，非 OpenAI 端点），铭荼请走 `https://sca-hub.cn/api/dev-api/software-auto/v1` —— 详见 §22 | 同路径的 `/api/papyrus/...` |
| 授权页 | `/device/authorize?device=…&client=ming-tea` | `/papyrus/authorize?device=…` |

- **挂载顺序很重要**：发布路由必须在前，否则 `/api/ming-tea/update` 会先命中 Papyrus 的退役 410（已实测：现在返回的是 `404 ming_tea_release_not_published`）。
- 两个入口生成不同的 verificationUrl：铭荼别名 → `/device/authorize?...&client=ming-tea`；旧入口 → `/papyrus/authorize?device=...`。老客户端不受影响。

### 20.4 实测

```
POST /api/ming-tea/auth/device
  → userCode=PUG4UY, verificationUrl=https://sca-hub.cn/device/authorize?device=…&client=ming-tea
POST /api/papyrus/auth/device
  → verificationUrl=https://sca-hub.cn/papyrus/authorize?device=…        （老路径照常）
GET  /api/ming-tea/update            → 404 ming_tea_release_not_published（挂载顺序正确，不是 410）
POST /api/ming-tea/auth/approve(auto) 未登录 → 401；轮询 → {"status":"pending"}
设备记录：createdIp="114.244.118.140"（与用户出口 IP 一致）
单测：routes/papyrusDeviceAuth.test.js 5/5（含同 IP 自动授权判定与 XFF 解析）
```

备份：`/srv/scallion/backups/auth-alias-20260927-130741/`、`/srv/scallion/backups/auto-approve-20260927-131104/`。

---

## 21. 最新情况（2026-09-27 收尾盘点）与付费层接入的完整链路

### 21.1 端到端现状

| 环节 | 站点侧 | 铭荼侧 | 状态 |
| --- | --- | --- | --- |
| 登录（设备授权） | 已就绪：`/api/ming-tea/auth/device` 等别名 + 授权页已登录即自动授权 | 已实现（宿主 `hub-client.mjs` 状态机、token 只进 `.credentials.yaml`） | ✅ 待用户授权一次补完自测 |
| 站内 AI（免费层） | 已就绪：`/api/dev-api/software-auto/v1`，100 次/月 | 已接入（`model-route.mjs` 热挂站点 provider，`model: "auto"`） | ✅ |
| OTA 更新 | 已就绪：`/api/ming-tea/update`（未发布返回 404，客户端当「已是最新」） | 已接入（`update.check` 实测返回「已是最新（0.1.0）」） | ✅（等安装包） |
| 剩余用量 | 已就绪：`/usage` 同时给免费层与付费层字段 | 已接入（额度归一化已兼容 `budget_rmb/used_rmb`） | ✅ |
| **付费层（Plus/Pro/Ultra）** | **本轮补完三处**（见 21.2） | 计费透明，客户端无需改动即可用；建议做档位展示与模型选择（21.4） | ⚠️ 待兑换测试码验证 |

### 21.2 为什么「付费层好像没接入」——是我这边三处断点（均已修）

1. **客户端传 `model: "auto"` 会被付费分支拒绝**（铭荼的 `ROUTE_MODEL_ID="auto"`，而付费分支原本只认价目表里的具体模型 id → 400 `paid_model_not_allowed`）。
   已修：`resolvePaidRequest` 把 `auto`/空值解析成默认模型，并给出**可切换的候选顺序**；客户端显式指定模型时只调那一个（账单与选择一致）。auto 模式下失败会自动试下一个（最多 3 个）。
2. **发不出套餐码**：卡密商品白名单 `CODE_PRODUCTS` 里只有旧的 Papyrus 三档，发码会报「不支持的卡密商品」。
   已修：加入 `plus` / `pro` / `ultra` 三个商品（**points: 0**，`entitlementType` 对应，31 天）。
3. **兑换被拒**：兑换流程原来硬要求 `points > 0`，而铭荼套餐只给额度不给积分。
   已修：`activationCodeDelivery` 允许「只带权益、不带积分」的码（仍拒绝「既没积分也没权益」的坏码），并在响应里给出「套餐权益已开通」而不是「成功兑换 0 积分」。

修完这三处，链路才真正闭环：**发码 → 兑换 → `user_package_entitlements` → `softwareTierForUser` → 付费分流（按人民币额度计量）**。

### 21.3 验收用测试码

已用后台同一套发码函数生成 **1 个 Plus 档测试码**（31 天权益、0 积分、备注里写了来源）：

```
SC-1B4A-KCIU-GLYS
```

兑换方式：登录后在 `/recharge` 页的「兑换码」框粘贴 → 提示「套餐权益已开通」。之后：

```bash
TOKEN=<站点 JWT>
curl -s https://sca-hub.cn/api/dev-api/software-auto/v1/usage -H "Authorization: Bearer $TOKEN"
# 期望：{"mode":"paid","tier":{"key":"plus",...},"budget_rmb":120,"used_rmb":0,"remaining_rmb":120,...}
curl -s https://sca-hub.cn/api/dev-api/software-auto/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
# 期望：200，响应头带 X-Scallion-Resolved-Model / X-Scallion-Cost-Rmb / X-Scallion-Remaining-Rmb
```

不需要时删掉即可：`DELETE FROM activation_codes WHERE batch_note LIKE '铭荼付费层验收测试码%'`（未兑换的码；已兑换的权益会随 31 天到期）。

### 21.4 铭荼侧建议补的两件（不阻塞使用）

1. **档位展示**：`/usage` 在付费档会返回 `tier: {key,name,price_rmb,monthly_budget_rmb}`，账户面板可以显示「Plus · 实际可用额度 ¥120」；
2. **模型选择**：付费层支持 `deepseek-v4.1-flash` / `step-3.7-flash` / `mimo-v2.6-flash`（其余 5 个当前无渠道，`/models` 会带 `available` 标记），可以给用户一个选择器；保持传 `auto` 也完全没问题。

---

## 22. 明确一点：铭荼走 `software-auto`，**不要**用 `/api/ming-tea/llm/*`（2026-09-27）

铭荼侧决定不把 provider 的 base URL 从 `https://sca-hub.cn/api/dev-api/software-auto/v1` 换成 `/api/ming-tea/llm/chat`，理由（记在它的 live-status 里）：DSH 的 provider 要的是 **OpenAI 协议**，`software-auto` 是任务文档里给的且已实测；`/api/ming-tea/llm/*` 形态没验证过，不把不确定的路径塞进 provider 配置。

**这个判断正确，而且比"没验证过"更严重——它们根本不是同一个接口的两种叫法：**

| | `software-auto`（铭荼正式接入面） | `/api/ming-tea/llm/*`（仅 Papyrus 兼容别名） |
| --- | --- | --- |
| 协议 | OpenAI 兼容（`/chat/completions`，支持 `stream`） | Papyrus 风格（`/llm/models`、`/llm/quota`、`/llm/chat`，非 OpenAI 端点） |
| 实现 | `routes/devApi.js` 的 `software-auto` 段 + `services/softwareAutoPool.js` / `softwarePaidPool.js` | `routes/papyrus.js` 的 `/llm/*`（经 `/api/ming-tea` 别名挂载） |
| 免费额度 | 站内 Auto 池，**100 次/月** | Papyrus Free 套餐的 Auto 额度（300 次/月，NVIDIA 池） |
| 付费额度 | **Plus/Pro/Ultra 的「实际可用额度」（人民币）** | Papyrus 套餐 + **积分**（Briefly/Futher/Deeper） |
| `/usage` 形态 | `{mode, monthly_*}` 或 `{mode:'paid', budget_rmb, used_rmb, remaining_rmb, tier}` | `{...papyrusQuota, auto, plan}` |

所以铭荼若走 `/llm/*`：协议对不上（要自己包适配层），**额度也会走错体系**（拿不到 Plus/Pro/Ultra 的额度，反而去扣 Papyrus 的积分/套餐）。

**我在 §20.3 那张表里把 `/api/ming-tea/llm/*` 列成"等价别名"是误导**，已在下方更正。汇总口径：

- **铭荼客户端**：只用 `https://sca-hub.cn/api/dev-api/software-auto/v1`（`/chat/completions`、`/models`、`/usage`）+ `POST /api/ming-tea/auth/device` 系列登录 + `/api/ming-tea/update` 更新清单。
- **`/api/ming-tea/llm/*` 与 `/api/papyrus/llm/*`**：保留给 Papyrus 时代的客户端，行为是 Papyrus 套餐/积分口径；新客户端不要使用，也不要写进 provider 配置。
- 若担心后来者误用，可以把 `/api/ming-tea` 上的 `/llm` 别名摘掉（只留 auth），但这会改动 `server.js` 挂载，需要单独确认——目前选择保留 + 文档警示。

---

## 23. 免费层只对外暴露 `auto`（2026-09-27 改）

### 23.1 问题

免费层的 `GET /software-auto/v1/models` 原本把 Auto 池里的 10 个真实模型名（含健康状态与实测延迟）都列了出来，响应里还带了 `software_auto.preference` 池名单。客户端把它接进模型选择器后，用户可以直接点某个具体模型，看起来像是"绕开了 Auto"。

**功能上并没有被绕过**（服务端在免费层一律覆盖客户端传来的 `model`，仍按 Auto 选路），但对外暴露池内名单有三重坏处：①用户以为自己选中了某个模型，实际由服务端决定；②池内模型名成了对外承诺，换池就是破坏性变更；③把「此刻哪些模型可用」这种内部运维信息暴露给客户端。

### 23.2 现在的行为

```json
GET /api/dev-api/software-auto/v1/models      # 免费层（需登录）
{
  "object": "list",
  "data": [{
    "id": "auto", "object": "model", "owned_by": "scallion", "display_name": "Auto",
    "description": "由站点在可用模型里自动选择，模型掉线会自动切换。",
    "available": true, "pool_size": 10, "pool_available": 9
  }],
  "software_auto": { "mode": "free", "quota": {...}, "pool": { "size": 10, "available": 9 }, "available_tiers": [...] }
}
```

- **只给 `auto` 一个选项**，不再出现任何池内模型名（`freeTierModelList()` in `services/softwareAutoPool.js`）；
- 只保留**数量**（`pool_size` / `pool_available`）便于排查「当前有几个候选可用」，不含名字；
- 公开的 `GET /api/ming-tea/tiers` 里 `free.models`（10 个名字）也换成了 `free.model_count`（数字）；
- **付费层不变**：那里按官方价计费、本来就该让用户选具体模型（`/models` 仍列出付费模型与 `available` 标记）。

回归测试写进了 `services/softwareAutoPool.test.js`：断言免费层响应里**不得出现任何 `SOFTWARE_AUTO_PREFERENCE` 成员**，以及公开 tiers 载荷里不能有 `free.models`。

### 23.3 验收（线上）

```
GET /api/ming-tea/tiers            → free 字段为 [key,name,price_rmb,monthly_calls,description,model_count]，无 models；全载荷不含池内模型名
免费层 /models 载荷                 → 单个 auto 项 + pool_size 10 / pool_available 9
GET /api/dev-api/software-auto/v1/models（无 token）→ 401（鉴权不变）
测试：services/softwareAutoPool.test.js + routes/mingTeaRelease.test.js 共 22/22 通过
```

---

## 24. 付费档新增「Auto 免费调用次数」（2026-09-27，用户指定）

| 档位 | 每月 Auto 免费调用 | 超出后 |
| --- | --- | --- |
| Plus | **200 次** | Auto 调用改为从「实际可用额度」（人民币）里扣，**不硬封** |
| Pro | **300 次** | 同上 |
| Ultra | **400 次** | 同上 |

### 24.1 成本影响：0

Auto 走的是站内 Auto 池（胖猫），**该上游对我们免费**（用户 2026-09-27 确认），所以赠送这 200/300/400 次不产生任何成本；额度限制的意义只剩「防滥用 / 公平使用」。相对地，档位的成本压力仍全部来自付费模型（MaxAPI，按官方价一折算，Plus 满额约 ¥12 / Pro ¥28 / Ultra ¥90）。

### 24.2 实现要点

- `config/softwareTiers.js` 每档加 `autoFreeCalls`（200/300/400），并随 `publicSoftwareTier` 一起对外（`auto_free_calls`）；
- 计次走**独立 source**：`dev_api_usage_logs.source = 'software-tier-auto'`（`softwareTierAutoQuota` / `recordTierAutoUsage`），**与免费层的 100 次额度、与按人民币计费的 `software_paid_usage` 互不污染**；
- 路由顺序（`routes/devApi.js` 付费分支）：`model` 是 `auto`/空 → **先吃本档的 Auto 免费次数**（走站内 Auto 池、计次不计费）→ 次数用尽或 Auto 池整体不可用 → 落到付费模型（从 ¥ 额度扣）。预算耗尽（429）**不影响**还没用完的 Auto 免费次数——顺序上先判 Auto 再判额度；
- `/models`（付费档）**首位新增一个 `auto` 项**（带 `auto_free_calls` / `auto_free_remaining`），仍不泄露池内模型名（见 §23）；
- `/usage`（付费档）新增 `auto_free: {limit, used, remaining, exhausted, configured}`；
- 响应头新增 `X-Scallion-Auto-Free-Remaining`（走 Auto 免费次数时）；`software_auto.via = 'auto_free'` 可区分这次走的是哪条路；
- 顺带把三处重复的 SSE 转发实现收敛成一个 `pipeSoftwareAutoStream()`。

### 24.3 页面

产品页与套餐页的档位卡都加了「**含每月 N 次 Auto 免费调用**」一行（数据来自 `/api/ming-tea/tiers`，改配置即跟随）。

### 24.4 实测（线上）

```
GET /api/ming-tea/tiers → Plus ¥19.9 额度¥120 Auto 免费 200 次/月 ｜ Pro ¥39.9 ¥280 / 300 ｜ Ultra ¥129 ¥900 / 400
服务端额度视图            → limit=200/300/400, remaining 同步, exhausted=false；计次 source = software-tier-auto
浏览器 /recharge          → Plus 卡显示「含每月 200 次 Auto 免费调用」「Auto 免费调用 200 次/月」，Pro/Ultra 同理
测试                      → services/softwarePaidPool.test.js 12/12（含档位字段、独立计次、用完即切换、历史档位不被误判）
```

---

## 25. 并发限制、上下文上限、Auto 超次计价（2026-09-27 用户指定）

### 25.1 并发（同一账号同时进行中的请求数）

| 档位 | 并发 | 配置位置 |
| --- | --- | --- |
| 免费层 | **2**（`SOFTWARE_AUTO_FREE_CONCURRENCY` 可覆盖） | `config/softwareTiers.js` 的 `FREE_TIER_CONCURRENCY` |
| Plus | **5** | 档位 `concurrency` |
| Pro | **10** | 同上 |
| Ultra | **15** | 同上 |

实现（`services/softwareConcurrency.js`）：

- 用 **Redis 计数**（`INCR`/`DECR`，键 `software:concurrency:chat:<userId>`，带 120 秒 TTL 自愈）——站点是 PM2 两个 cluster 实例，进程内计数会各算各的，Redis 才能全局一致；
- **一次客户端请求 = 1 个并发位**；请求内部因「模型掉线自动切换」而重试**不额外占位**；`release` 幂等（重复调用不会减掉别人的名额）；
- 超限返回 **429** `{code:'concurrency_limit'}` + `Retry-After: 2` + `X-Scallion-Concurrency-Limit`；
- Redis 不可用时退化为进程内计数（`backend: 'memory'`）。**踩坑**：ioredis 默认 `maxRetriesPerRequest: null`，Redis 掉线时命令会一直排队**而不抛错** —— 直接 await 会把请求永久挂住（健康检查踩过同样的坑）。已给 Redis 命令加 1.5s 超时，超时即退化为内存计数。

### 25.2 上下文上限（`config/softwareModelContext.js`）

- **`auto` 统一 256K**（262144 tokens，用户指定）；
- 付费模型按真实上限逐个登记：`deepseek-v4.1-flash` 1M、`kimi-k3` 1M（厂商官方页注明 1,048,576）、`deepseek-v4-flash` 1M、`step-3.7-flash`/`mimo-v2.6-flash`/`glm-5.3`/`glm-5.2` 256K、`deepseek-v4-pro` 128K（保守取）；
- **校验刻意留余量**：只有输入 token 估算值超过上限的 **90%** 才拒绝（`SOFTWARE_CONTEXT_REJECT_RATIO` 可调），因为各家 tokenizer 不同，严格相等会误杀；输入规模用「字符数 ÷ 4」快速估算；
- 超限返回 **413** `{code:'context_too_long'}` 并给出可读提示（含实际估算值与上限）；
- 对外通过 `/models` 与 `/api/ming-tea/tiers` 的 `context_window`/`context_tokens`/`context_catalog` 暴露（原先付费模型一律显示「上游未公开 / 131072」，现已逐个登记）。

### 25.3 Auto 超出免费次数后的计价

用户选择「**按次象征性扣额度**」。实现：

- `config/paidModelPrices.js` 新增 `AUTO_OVERAGE_PRICE`：**¥0.1/百万 输入、¥0.2/百万 输出、缓存命中 ¥0.01**（按 token 计，比最便宜的付费模型 mimo ¥0.98/¥1.96 再低约一个数量级）——**意在限量而非盈利**；
- 付费档的 Auto 调用顺序变了：**①免费次数内 → 计次不计费；②超出后 → 仍走 Auto 池，但按上面的单价从人民币额度扣**（响应头 `X-Scallion-Cost-Rmb` / `X-Scallion-Remaining-Rmb`，响应体 `software_auto.via = 'auto_overage'`）；③只有**预算也耗尽**才 429。
  这样做的好处：用户不会在免费次数用完后突然发现「Auto 变成另一个模型、开始扣大钱」，体验连续。
- Auto 的这项单价也进了对外公示的价目表（`/api/ming-tea/tiers` 的 `models` 里 id = `auto`，带 `is_auto: true`）。

### 25.4 实测（线上）

```
GET /api/ming-tea/tiers → free.concurrency=2、free.context_window=256K
                          concurrency: Plus=5 / Pro=10 / Ultra=15
                          context_catalog: auto 256K + 8 个付费模型（1M/256K/128K）
                          models 含 auto 项：¥0.1/¥0.2（缓存 ¥0.01，is_auto=true）
浏览器 /recharge        → 档位卡新增「同时对话 N 路并发」
测试                    → 29 + 8（并发退化路径、上下文阈值、公开载荷）全通过
```

备份：`/srv/scallion/backups/limits-20260927-141748/`。

### 25.5 待你确认的一点

付费模型的上下文数字里，**只有 `kimi-k3` 有厂商文档直证**（Moonshot 官方页注明 1,048,576）；其余是我按该模型档位/mainstream 值取的保守数（标注 `estimate`）。如果你希望**逐个核对厂商文档**再定稿，告诉我，我按模型去查；数字不对时改 `config/softwareModelContext.js` 一处即可（对外展示与服务端校验会同时跟随）。

---

## 26. 上下文按厂商文档定稿 · GLM-5.3-Flash 补入 · 备用 key 与 0.125x 盈亏（2026-09-27）

### 26.1 上下文：从「估算」改为「厂商文档」

逐个查证后（`config/softwareModelContext.js` 每条都带 `source`）：

| 模型 | 上下文 | 依据 |
| --- | --- | --- |
| `deepseek-v4.1-flash` / `deepseek-v4-pro` / `deepseek-v4-flash` | **1M** | DeepSeek 官方定价页「上下文长度 1M」（输出上限最大 384K）；**并用实测印证**：超长请求报错原文 `prompt is too long: 1540024 tokens > 1048576 maximum` |
| `glm-5.3` / `glm-5.3-flash` / `glm-5.2` | **1M** | 智谱官方模型总览（最大输出 128K） |
| `kimi-k3` | **1M** | Moonshot 官方定价页注明 1,048,576 |
| `step-3.7-flash` / `mimo-v2.6-flash` | 256K（**仅展示、不强制**） | 两家官方文档都没给每模型上下文数字（StepFun 只写「输入+输出总数不能超过模型上限」，MiMo 页面 JS 渲染抓不到）——**没有依据就不硬拦**，由上游报错更准 |
| `auto` | 256K（强制） | 用户指定 |

**校验策略同时修正了两处**：
1. `enforced` 只有 `verified` 以 `estimate` 开头时才为 false（原先写错，导致有依据的模型也被跳过）；
2. 输入规模估算从「字符数 ÷ 4」改成「**字符数 ÷ 0.7**」——中文实测约 0.7 字符/token（DeepSeek 实测 108 万字符 = 154 万 token），÷4 会低估约 5 倍、导致漏拦。

### 26.2 补入 GLM-5.3-Flash（之前漏了）

- 价目：网关 0.1x 分组折后价 $0.08/$0.28、缓存读 $0.02 → 反推**官方价 $0.80/$2.80**，即 **¥5.6/¥19.6（缓存 ¥1.4）**；
- 上下文：1M（智谱官方）；
- 同时补进 Auto 兜底的付费候选顺序（`PAID_AUTO_ORDER`）。

### 26.3 备用 key（0.125x 分组）与自动回退

- env 新增 `PAID_MODEL_API_KEY_BACKUP`（只存服务器）；`paidUpstream().keys = [主, 备]`；
- **回退条件**（`shouldTryBackupKey`）：额度耗尽 `用户额度不足`／401/402/403／`invalid api key` 等「key 本身不可用」的情形；
  **不**在限流（429）、模型无渠道（model_not_found）时回退——那两种情况换 key 没用（该换模型或等一会儿）。
- 实测两把 key 的可达性现状**完全一致**：`deepseek-v4.1-flash` ✓；`deepseek-v4-pro` 被限流；`glm-5.3`/`glm-5.3-flash`/`glm-5.2`/`kimi-k3`/`step-3.7-flash`/`mimo-v2.6-flash` 全部 `not supported by any configured account`。**所以「GLM/Kimi 可用」两把 key 都没复现**，按约定先把价目与上下文备好，可达性交给实时探测（渠道恢复后 `/models` 与可用性会自动反映）。

### 26.4 0.125x 会不会亏钱

口径：网关成本 = 官方价 × 倍率；用户按官方价扣额度。

| 档位 | 售价 | ¥额度 | 0.1x 成本 | 0.125x 成本 | 0.125x 毛利 | 0.125x 毛利率 |
| --- | --- | --- | --- | --- | --- | --- |
| Plus | ¥19.9 | ¥120 | ¥12.0 | **¥15.0** | ¥4.9 | **24.6%** |
| Pro | ¥39.9 | ¥280 | ¥28.0 | **¥35.0** | ¥4.9 | **12.3%** |
| Ultra | ¥129 | ¥900 | ¥90.0 | **¥112.5** | ¥16.5 | **12.8%** |

结论：**都不亏**。0.125x 让实付成本上升 25%，Plus 毛利率从约 40% 降到约 25%，Pro/Ultra 从约 30% 降到约 12–13%。所以这套备用 key 作为**保险是可行的**，但只应在主 key 不可用时启用——若长期跑在 0.125x，建议要么提价、要么把额度按 0.8 折给（保持同样的毛利率）。

### 26.5 顺带：空回复单次重试

`callPaidModel` 增加 `retryOnEmpty`：**200 但内容为空**时自动重试一次（实测推理型模型在输出预算偏小时会空回复，重试可显著降低），并在返回值里带 `emptyReply` / `reasoningTokens` 供排查。

### 26.6 实测（线上）

```
paidUpstream() → keys 数 2（主 sk-36ddcad… / 备 sk-6e00d61…）
上下文目录     → auto 256K(强制) + DeepSeek×3/GLM×3/Kimi 1M(强制) + step/mimo 256K(仅展示)
价目表         → 10 条（含 auto ¥0.1/¥0.2、glm-5.3-flash ¥5.6/¥19.6）
主 key 实调    → ✓「在线」，成本 ¥0.000021，key=primary
测试           → 40/40 通过（含备用 key 回退判定、estimate 不强制、目录条数）
```

备份：`/srv/scallion/backups/key2-context-20260927-143413/`（含 `scallion.env.bak`）。

---

## 27. 故障复盘：付费档全链路 500 + 发布事故（2026-09-27）

铭荼侧提交了诊断报告（`docs/ming-tea-site-500-report.md`，站点仓库也留一份说明），三个端点全 500：

```json
{"error":{"message":"softwareTierAutoQuota is not defined","type":"dev_api_error"}}
```

### 27.1 根因一：漏 import（报告判断正确）

加「付费档 Auto 免费次数」时，`routes/devApi.js` 的**调用点写进去了、import 没进去**（同一文件我做了两次替换互相覆盖）。`node --check` 查不出来（语法合法），只有真正执行到那一行才抛 `ReferenceError` —— 而它只在**付费档分支**执行，所以免费档一切正常，Plus 用户一兑换就全挂。

**修复**：补上 `softwareTierAutoQuota` 与 `recordTierAutoUsage` 的 import。

### 27.2 根因二：非 ASCII 写进响应头（修完第一个才暴露）

补完 import 后 `/chat` 变成 `500 Invalid character in header content ["X-Scallion-Resolved-Model"]` —— 我把 `auto→模型名` 里的 `→` 直接写进了 HTTP 头。HTTP 头只允许 ASCII（`ERR_INVALID_CHAR`）。

**修复**：改为 ASCII 连接 `auto-<model>`，真实模型名另放 `X-Scallion-Auto-Model`。

### 27.3 根因三：`/models` 出现两个 `auto`

我把 `auto` 加进价目表（作为「超次计费项」）后，`paidModelAvailability()` 也会返回它，于是与手写的 `auto` 项重复。

**修复**：付费 `/models` 里过滤 `is_auto` 的条目。

### 27.4 发布事故（我的操作失误，已恢复）

修第一个问题时我把「上传」与「安装」写在同一条命令里，管道与 heredoc 抢同一个 stdin，结果 **`cat > /tmp/...` 收到空输入，把生产 `routes/devApi.js` 覆盖成 0 字节**，PM2 连续重启 17 次（进程起来即崩）。发现后立即用同批次备份恢复（`backups/import-fix-20260927-152726`，37,377 字节，恢复后站点立即恢复在线），再用**分步方式**（先上传并校验字节数 → 再安装 → 再重启）正确部署。

**教训**：①上传与执行必须分两条命令，绝不与 heredoc 混用；②覆盖前先 `wc -c` 校验源文件非空；③安装后立刻 `node --check` + 健康检查。

### 27.5 新增的防回归测试（`routes/devApiWiring.test.js`）

这类错误单元测试测不到，所以补了**静态检查**：

1. **控制器里对本地模块导出的每个引用，必须真的在 import 列表里**（防漏 import → 线上 500）；
2. 付费档两个关键函数必须在 import 里（点名断言）；
3. **响应头字面量不得含非 ASCII**（防 `ERR_INVALID_CHAR`）；
4. 付费 `/models` 必须过滤掉价目表里的 `auto`（防重复项）。

### 27.6 验收（付费账号 id 112「带带葱铭」，Plus 生效中，线上实测）

```
GET  /usage  → 200 {mode:'paid', tier:{name:'Plus'}, budget_rmb:120, used_rmb:0, remaining_rmb:120,
                    auto_free:{limit:200, used:0, remaining:200, exhausted:false, configured:true}}
GET  /models → 200 10 个模型，auto 仅 1 次；付费模型带 context_window 与 available
POST /chat/completions {"model":"auto"} → 200 内容「可用」
     X-Scallion-Resolved-Model: auto-nvidia/nemotron-3-ultra-550b-a55b
     X-Scallion-Auto-Model: nvidia/nemotron-3-ultra-550b-a55b
     X-Scallion-Tier: plus ｜ X-Scallion-Auto-Free-Remaining: 199 ｜ X-Scallion-Concurrency-Limit: 5
     software_auto.via = 'auto_free'（说明写对了：免费次数内走站内 Auto 池、计次不计费）
回归：42 项全通过、零失败（含 4 项新增静态检查）
```

备份：`backups/import-fix-20260927-152726`（事故前基线）、`backups/header-fix-20260927-154335`。

### 27.7 报告里「要消费的字段」逐条核对结果

| 端点 | 字段 | 状态 |
| --- | --- | --- |
| `GET /usage`（付费档） | `mode` / `tier` / `budget_rmb` / `used_rmb` / `remaining_rmb` / `used_percent` / `auto_free{limit,used,remaining,exhausted,configured}` | ✅ 全部存在 |
| `GET /usage`（付费档） | `monthly_limit` / `monthly_remaining` | ⚠️ **付费档没有这两个字段（设计如此）**：付费层按人民币额度计量，免费层的 `monthly_*`（次数）只在该账号走免费档时返回。客户端请按 `mode` 分流：`mode='free'` 读 `monthly_*`，`mode='paid'` 读 `budget_rmb/used_rmb/remaining_rmb`。 |
| `GET /usage`（免费档，实测 id 2） | `mode` / `monthly_limit` / `monthly_used` / `monthly_remaining` / `plan` / `available_tiers` | ✅ 全部存在（`0/100`） |
| `GET /models`（付费档） | `auto` 项带 `auto_free_calls` / `auto_free_remaining`；每条模型带 `context_window` / `context_tokens` | ✅（`auto` 只出现一次，256K） |
| `GET /models`（免费档） | 只返回一个 `auto`（`pool_size` / `pool_available` 数量），不泄露池内模型名 | ✅（实测 `['auto']`，pool 10/5） |
| 响应头 | `X-Scallion-Auto-Free-Remaining` / `X-Scallion-Concurrency-Limit` / `X-Scallion-Cost-Rmb` / `X-Scallion-Remaining-Rmb`，**新增 `X-Scallion-Auto-Model`** | ✅（付费/Auto 路径实测到前两个 + 新增那个；`Cost-Rmb`/`Remaining-Rmb` 在「超出免费次数走 auto」时出现） |
| 错误码 | `429 concurrency_limit` / `413 context_too_long` / `429 software_tier_quota_exhausted` | ✅ 已实现（并发实测触发过） |

**关于 `/api/ming-tea/tiers`**：报告里说铭荼目前没用它、更愿意用它拿静态信息——同意，它本来就是为这个设计的公开端点（无需登录），返回 `tiers[]`（含 `price_rmb`/`monthly_budget_rmb`/`auto_free_calls`/`concurrency`/`purchase_url`）、`free{monthly_calls,model_count,context_window,concurrency}`、`concurrency[]`、`context_catalog[]`、`models[]`（价目 + `available`/`checked_at`）、`product.release`。可以放心依赖，字段只增不减。
