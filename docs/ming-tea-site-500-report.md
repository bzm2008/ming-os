# 站点侧故障：Plus 开通后整条 software-auto 链路 500（2026-09-27，铭荼侧报告）

## 一句话

铭荼账号（`username: 带带葱铭`，id 112）兑换 Plus 套餐后，`software-auto` 的三个端点**全部返回 HTTP 500**，错误体是同一个未定义变量：

```json
{"error":{"message":"softwareTierAutoQuota is not defined","type":"dev_api_error"}}
```

这不是额度不足、也不是鉴权问题（同一 token 访问其它接口正常），而是**代码里引用了一个没有定义的标识符**，任何走到该分支的请求都会 500。

## 复现（三条命令，全部可复制）

```bash
TOKEN=<铭荼账号的站点 JWT>

BASE=https://sca-hub.cn/api/dev-api/software-auto/v1

# ① 额度
curl -s -o /dev/null -w "%{http_code}\n" $BASE/usage      -H "Authorization: Bearer $TOKEN"
# → 500
curl -s $BASE/usage -H "Authorization: Bearer $TOKEN"
# → {"error":{"message":"softwareTierAutoQuota is not defined","type":"dev_api_error"}}

# ② 模型列表
curl -s -o /dev/null -w "%{http_code}\n" $BASE/models     -H "Authorization: Bearer $TOKEN"
# → 500（同一个错误体）

# ③ 对话（关键：连模型调用也走不通）
curl -s -X POST $BASE/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"hi"}],"stream":false}'
# → 500 {"error":{"message":"softwareTierAutoQuota is not defined","type":"dev_api_error"}}
```

## 影响范围

| 受影响 | 表现 |
| --- | --- |
| `GET {BASE}/usage` | 500 —— 铭荼的「剩余用量」圆环与账户面板拿不到数据（用户看到的是「读取失败」） |
| `GET {BASE}/models` | 500 —— 刷新/同步模型路由失败（我们已做降级：失败时保留上一次的模型列表，不覆盖配置） |
| `POST {BASE}/chat/completions` | 500 —— **用户完全无法用站点模型对话**（这是最严重的一条） |
| 不受影响 | `POST /api/ming-tea/auth/device` 系列（登录正常）、`/api/ming-tea/update`（更新清单正常）、`/api/papyrus/llm/quota`（返回 200，但那是 Papyrus 积分体系，`is_member:false`，看不到 Plus 权益） |

**判定：故障与账号档位相关。** 开通 Plus 之前（免费档）同一条链路实测正常：

- `GET /usage` → 200，返回 `{monthly_limit:100, monthly_remaining:93, plan:{key:"free",name:"Free"}, available_tiers:[…]}`
- `POST /chat/completions` → 200，实际路由到 `agnes-2.5-flash`，返回真实内容

免费档走不到 `softwareTierAutoQuota` 这个标识符所在的分支，所以那时没有暴露。

## 定位建议（按站点侧 2026-09-27 的几次变更）

该标识符的名字指向「**付费档的 Auto 免费次数**」这一功能。站点最近的变更记录里有两条相关：

1. **付费档新增 Auto 免费调用次数**（Plus 200 / Pro 300 / Ultra 400，`auto_free`、`auto_free_remaining`、`X-Scallion-Auto-Free-Remaining`、`software_auto.via='auto_free'`）；
2. **Auto 超次后按象征性单价从人民币额度扣**（`via='auto_overage'`）。

判断：实现这两条时引入（或被重命名后漏改）了一个标识符 `softwareTierAutoQuota`。请检查：

- 该文件里 `softwareTierAutoQuota` 的定义是否被删/改名（例如改成了 `softwareTierAutoQuotaState`、`getSoftwareTierAutoQuota`、或从另一个模块 import 但忘了解构/import）；
- 是否在**付费档分支**里引用了它，而免费档分支不引用（这正好解释「免费正常、Plus 后 500」）；
- 该文件是 `routes/devApi*.js` / `services/softwareAuto*.js` 之一（按错误 `type: "dev_api_error"` 与该前缀定位）。

## 修复后请顺带核对（铭荼侧要消费的字段）

修好后我们会立刻按这些字段收尾，文档里的规格与代码不一致的话请在站点侧文档里标注：

| 端点 | 铭荼要用的字段 |
| --- | --- |
| `GET /usage` | `auto_free: {limit, used, remaining, exhausted}`；`plan.name` / 档位；`monthly_limit/remaining`（免费层）；`budget_rmb/used_rmb/remaining_rmb/used_percent`（付费层） |
| `GET /models` | `auto` 项上的 `auto_free_calls` / `auto_free_remaining`；每条模型的 `context_window` / `context_tokens`（`auto` = 256K） |
| 响应头 | `X-Scallion-Auto-Free-Remaining`、`X-Scallion-Concurrency-Limit`、`X-Scallion-Cost-Rmb`、`X-Scallion-Remaining-Rmb` |
| 错误码 | `429 concurrency_limit`（应按 `Retry-After` 重试，铭荼侧会做成提示而不是报错弹窗）、`413 context_too_long`、`429 software_tier_quota_exhausted` |

另外 `/api/ming-tea/tiers`（文档提到它带 `context_catalog`）铭荼侧目前**没有使用**，如果它是稳定的公开端点，我们更愿意用它来拿「各档位额度上限 + 并发上限 + 上下文目录」这些**静态**信息（现在的做法只能从 `/usage` 的 `available_tiers` 里读，字段较少）。

## 铭荼侧的应对（已做，与站点修复解耦）

1. **降级不破坏配置**：模型同步失败时保留上一次的模型列表，不会把路由改成空列表；
2. **不再污染登录态**：只有 401/403 才判定「登录过期并清除凭证」，5xx 一律按「站点故障」提示，不会把用户登出；
3. 额度读取失败时圆环回落成 `—` 并给出可操作提示（「站点暂时不可用，稍后自动重试」），不编造数值；
4. 日志里记录了 `HTTP 500` 的原文（`RPC quota.get 失败：hub/usage-failed 读取额度失败（HTTP 500）`），便于对照时间点。

修复后无需铭荼侧改动即可恢复；如果字段与上表不同，请回写本文档，我们按实际字段调整。

---

# 追加（2026-09-27 晚）：修复后**再次**全线 500，这次是内部服务连不上

站点侧上一轮 `softwareTierAutoQuota` 修好、付费档实测通过之后，**又出现一次全线故障**，错误换了一个：

```json
{"error":{"message":"connect ECONNREFUSED 127.0.0.1:13306","type":"ECONNREFUSED"}}
```

## 端点体检（同一时刻实测，用来界定范围）

| 端点 | 结果 | 说明 |
| --- | --- | --- |
| `GET /api/ming-tea/tiers` | **200** | 静态档位目录，不依赖内部服务 ⇒ 正常 |
| `GET /api/ming-tea/update` | 404 | 预期行为（未发布安装包） |
| `/device/authorize` | 200 | 授权页正常 |
| `GET /api/dev-api/software-auto/v1/usage` | **500** | ← 需查库/内部服务 |
| `GET /api/dev-api/software-auto/v1/models` | **500** | ← 同上 |
| `POST /api/dev-api/software-auto/v1/chat/completions` | **500** | ← **两者都走这里：不传 model（auto）与传付费模型名，报的是同一个错** |
| `GET /api/papyrus/llm/quota` | **500** | 另一个查询额度的入口也挂 |

**判定**：站点后端连不上它自己的内部服务（`127.0.0.1:13306`）。这是**服务端环境/依赖问题**，
与客户端无关（同一 token、同一台机器，静态端点 200、动态端点 500）。`13306` 通常是 MySQL 的
备用端口（默认 3306/3307 冲突时的习惯选择），也可能是某个内部 API 端口 —— 请站点侧确认该服务是否在跑、
端口是否被改过、以及进程重启后是否忘记拉起它。

## 铭荼侧的表现（容错按设计生效，非故障原因）

实测同一时刻的客户端状态：

- 登录态**未被误清**：`signedIn: true`、`expired: false`、`user: 带带葱铭` ✅
- 模型列表同步失败时**保留了现有配置**：`models.sync` 返回 `{ok:false, reason:"hub/site-error"}`，
  但 `agent-default-model` 仍是 `deepseek-v4.1-flash / ming-tea-hub`，没有被写成空 ✅
- 额度接口失败时圆环回落成 `—`，提示为可操作文案：
  「站点暂时不可用（HTTP 500），稍后会自动重试；这不影响你的登录状态。」 ✅

所以用户看到的现象就是「模型用不了」——**因为请求在站点侧就 500 了**，客户端没有可用的后备模型可切
（切到 DeepSeek 官方需要用户自己的 API Key）。

## 建议站点侧修完后自测这几条

```bash
TOKEN=<铭荼账号 JWT>
BASE=https://sca-hub.cn/api/dev-api/software-auto/v1
curl -s -o /dev/null -w "usage  %{http_code}\n" -H "Authorization: Bearer $TOKEN" $BASE/usage
curl -s -o /dev/null -w "models %{http_code}\n" -H "Authorization: Bearer $TOKEN" $BASE/models
curl -s -o /dev/null -w "auto   %{http_code}\n" -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' \
  $BASE/chat/completions
```
三条都应 200，且第三条响应头带 `X-Scallion-Resolved-Model`。

## 追加二（用户复测仍不可用时的复查，2026-09-27 深夜）

用户复测仍报「auto 与付费模型都用不了」。复查结论**与追加一完全一致**，且能进一步缩小范围：

| 探测 | 结果 | 含义 |
| --- | --- | --- |
| `POST /chat/completions {"model":"auto"}` | 500 `ECONNREFUSED 127.0.0.1:13306` | 请求未到模型层 |
| `POST /chat/completions {"model":"deepseek-v4.1-flash"}` | 500 同上 | 付费路径同样死在同一个地方 |
| `GET /api/ming-tea/tiers` ×5 | **全部 200**（1.4–2.7s） | 站点进程活着、静态/轻量端点正常 |
| `GET https://sca-hub.cn/` | **200** | 主站没挂 |
| `GET /usage`、`/models` | 一度 `HTTP 000`（超时） | 该链路**不稳定**，时快时慢 |

**判定**：站点进程本身健康（首页与 tiers 稳定 200），但它依赖的**内部服务**
（`127.0.0.1:13306`）不可用 —— 因此所有需要查库/查该服务的端点（额度、模型列表、对话、
Papyrus 额度）一致失败。**这不是铭荼客户端能修的问题**，客户端在这条链路上只是发起方。

### 请站点侧按顺序确认

1. **`13306` 上原本跑的是什么**（MySQL 备用端口？内部 API？）—— 它在 `NAS` 上还是 ECS 本机？
   若在 NAS 经 frp 隧道映射，**隧道断了**会同现这个错（与站点此前记录的 `13306` 历史问题一致）。
2. 该服务**是否在运行**、端口是否被改动、进程重启后是否漏拉。
3. 修好后跑这份报告「自测」三条命令，三条都应 200，且对话那条带 `X-Scallion-Resolved-Model`。

### 铭荼侧无需改动

客户端容错已实测生效（未误登出、模型配置保留、圆环如实显示 `—`），并已加 **5s/30s/2min 退避自愈**，
站点恢复后会自动补齐模型列表与额度，用户不需要重开应用。
