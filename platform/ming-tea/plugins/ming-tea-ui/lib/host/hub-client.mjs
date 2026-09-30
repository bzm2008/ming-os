// 站点（sca-hub.cn）客户端。
//
// 为什么整个请求都放在宿主半区：浏览器直连站点的 CORS 未确认，Tauri 壳的 CSP 也只允许
// `'self' ipc:`；宿主（DSH 的 Node 进程）没有这两个限制。**站点 JWT 因此不进浏览器页面**，
// 只以凭证引用的形式存在 `.credentials.yaml`（0600）。
//
// 这个文件刻意不依赖 cordis / DSH：纯 ESM + 注入式 fetch，便于用假 fetch 做回归
// （见 scripts/check_ming_tea_hub.mjs），也便于以后搬到别的宿主里复用。

/** 站点 origin。设备授权、模型端点、更新清单同源。 */
export const HUB_ORIGIN = "https://sca-hub.cn";
/** 站内 AI 的 API Base（免费层与付费层是同一组 URL）。 */
export const HUB_API_BASE_URL = `${HUB_ORIGIN}/api/dev-api/software-auto/v1`;
/** 设备授权（免登录申请设备码）。
 * 用站点给的**品牌化别名** `/api/ming-tea/auth/*`：verificationUrl 会指向
 * `/device/authorize?device=…&client=ming-tea`（铭荼品牌化授权页）。
 * 旧的 `/api/papyrus/*` 仍然可用，切换不影响老客户端。 */
export const DEVICE_AUTH_PATH = "/api/ming-tea/auth/device";
/** 更新清单（等价静态地址 /downloads/ming-tea/latest.json）。 */
export const UPDATE_MANIFEST_PATH = "/api/ming-tea/update";
/** 站点公开的档位与上下文目录（无需登录也能看；给账户面板列可升级档位用）。 */
export const TIERS_PATH = "/api/ming-tea/tiers";
/** 按平台跳转安装包。 */
export const DOWNLOAD_LATEST_URL = `${HUB_ORIGIN}/api/ming-tea/download/latest`;
/** 尚未发布任何安装包时站点返回的业务码。 */
export const UNPUBLISHED_CODE = "ming_tea_release_not_published";
/** 站点 JWT 官方标称有效期（7 天）；真正失效以站点 401 为准。 */
export const TOKEN_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;
/** 存 JWT 的凭证引用名（pi-ai 的 apiKeyEnv 指的就是它）。 */
export const TOKEN_REF = "MING_TEA_HUB_TOKEN";

const DEFAULT_TIMEOUT_MS = 20000;

/** 把 `{monthly_limit, monthly_used, monthly_remaining}`（免费层）或
 * `{budget_rmb, used_rmb, remaining_rmb, used_percent}`（付费层）归一成一个形状。
 * 字段不全会退化成「没有百分比、只有文字」——**不编造数值**。 */
export function normalizeUsage(raw) {
  if (raw === null || typeof raw !== "object") {
    return { tier: "unknown", percent: null, detail: "" };
  }
  const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
  // 档位名要在分支之前算：免费档下面会提前 return（实测 /usage 里档位是 plan.name，免费档也有）
  const tierName =
    typeof raw.tier?.name === "string" && raw.tier.name !== ""
      ? raw.tier.name
      : typeof raw.plan === "string"
        ? raw.plan
        : typeof raw.plan?.name === "string"
          ? raw.plan.name
          : undefined;
  const percentOf = (remaining, total) =>
    total !== undefined && total > 0 && remaining !== undefined
      ? Math.max(0, Math.min(100, Math.round((remaining / total) * 100)))
      : null;

  // 免费层（次数）与付费层（人民币额度）**分开算**：付费用户的响应里可能两者都在，
  // 悬浮提示要能同时显示两层的剩余，所以两个块都返回，不互相覆盖。
  const limit = num(raw.monthly_limit);
  const remainingCalls = num(raw.monthly_remaining);
  const free =
    limit !== undefined && remainingCalls !== undefined
      ? {
          limit,
          used: num(raw.monthly_used) ?? Math.max(0, limit - remainingCalls),
          remaining: remainingCalls,
          percent: percentOf(remainingCalls, limit),
          detail: `本月剩余 ${remainingCalls}/${limit} 次`,
        }
      : null;

  // 站点付费档实测：顶层给 budget_rmb/remaining_rmb，档位信息在 tier（含 monthly_budget_rmb）
  const budget = num(raw.budget_rmb) ?? num(raw.monthly_budget_rmb) ?? num(raw.tier?.monthly_budget_rmb);
  const remainingRmb = num(raw.remaining_rmb);
  const usedPercent = num(raw.used_percent);
  const paid =
    budget !== undefined && remainingRmb !== undefined
      ? {
          budget,
          used: num(raw.used_rmb),
          remaining: remainingRmb,
          percent:
            usedPercent !== undefined
              ? Math.max(0, Math.min(100, Math.round(100 - usedPercent)))
              : percentOf(remainingRmb, budget),
          detail:
            usedPercent !== undefined
              ? `剩余 ¥${remainingRmb.toFixed(2)}（已用 ${usedPercent}%）`
              : `剩余 ¥${remainingRmb.toFixed(2)}`,
        }
      : null;

  // 圆环显示「当前生效档位」的剩余；付费优先（付费账号以人民币额度为准）
  const active = paid ?? free;
  if (active === null) return { tier: "unknown", tierName, percent: null, detail: "", free, paid };
  return {
    tier: paid !== null ? "paid" : "free",
    tierName,
    percent: active.percent,
    detail: active.detail,
    free,
    paid,
  };
}

/** `/models` 的形态站点没有对外承诺，这里做防御式归一：
 * 支持 `{data:[{id}]}`（OpenAI 风格）、`{models:[…]}`、`{models:{id:{…}}}` 三种，
 * 并保留 `available` 标记（文档：付费层 `/models` 带可用性）。解析不出来就返回空数组，
 * 让上层只保留 `auto` —— 不猜模型名。 */
export function normalizeModels(raw) {
  const list = [];
  const push = (entry, id) => {
    const modelId = typeof entry?.id === "string" && entry.id !== "" ? entry.id : id;
    if (typeof modelId !== "string" || modelId === "") return;
    if (list.some((item) => item.id === modelId)) return;
    // 站点实际用的字段是 healthy + last_error（实测 /models: {id, healthy, latency_ms, last_error}）；
    // available 是文档里提到的写法，两者都认，都没有就按可用处理。
    const healthy =
      entry?.available !== undefined
        ? entry.available !== false
        : entry?.healthy !== undefined
          ? entry.healthy !== false
          : !(typeof entry?.last_error === "string" && entry.last_error !== "");
    list.push({
      id: modelId,
      name: typeof entry?.name === "string" ? entry.name : modelId,
      available: healthy,
      latencyMs:
        typeof entry?.latencyMs === "number"
          ? entry.latencyMs
          : typeof entry?.latency_ms === "number"
            ? entry.latency_ms
            : undefined,
    });
  };
  if (Array.isArray(raw?.data)) for (const entry of raw.data) push(entry, entry?.id);
  if (Array.isArray(raw?.models)) for (const entry of raw.models) push(entry, entry?.id);
  else if (raw?.models !== null && typeof raw?.models === "object") {
    for (const [id, entry] of Object.entries(raw.models)) push(entry, id);
  }
  return list;
}

/** 版本比较：只用于「已是最新 / 有新版本」。返回 -1 / 0 / 1；无法解析时返回 null。 */
export function compareVersions(a, b) {
  const parse = (value) => {
    if (typeof value !== "string") return null;
    const core = value.trim().replace(/^v/i, "").split(/[-+]/)[0];
    if (core === "") return null;
    const parts = core.split(".").map((piece) => Number.parseInt(piece, 10));
    if (parts.length === 0 || parts.some((n) => Number.isNaN(n))) return null;
    return parts;
  };
  const left = parse(a);
  const right = parse(b);
  if (left === null || right === null) return null;
  const length = Math.max(left.length, right.length);
  for (let i = 0; i < length; i += 1) {
    const l = left[i] ?? 0;
    const r = right[i] ?? 0;
    if (l !== r) return l < r ? -1 : 1;
  }
  return 0;
}

/** 当前平台 → 站点下载参数（windows / darwin / linux；x64 / arm64）。 */
export function downloadTargetFor(platform = process.platform, arch = process.arch) {
  const platformName = platform === "win32" ? "windows" : platform === "darwin" ? "darwin" : "linux";
  const archName = arch === "arm64" ? "arm64" : "x64";
  return { platform: platformName, arch: archName };
}

export function downloadUrlFor(platform, arch) {
  const target = downloadTargetFor(platform, arch);
  return `${DOWNLOAD_LATEST_URL}?platform=${target.platform}&arch=${target.arch}`;
}

/** 站点调用失败：带机器可读的 code，便于上层区分 401 / 404 / 业务码。 */
export class HubError extends Error {
  constructor(message, { code = "hub/error", httpStatus, body } = {}) {
    super(message);
    this.name = "HubError";
    this.code = code;
    this.httpStatus = httpStatus;
    this.body = body;
  }
}

export function createHubClient({
  fetchImpl,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  origin = HUB_ORIGIN,
  apiBaseUrl = HUB_API_BASE_URL,
} = {}) {
  const doFetch = fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  // 站点把模型端点整段挂在 API Base 下（/api/dev-api/software-auto/v1/usage …），
  // 设备授权与更新清单则在 /api/... 下 —— 所以统一用「绝对路径」而不是「origin + path」。
  const apiBasePath = (() => {
    try {
      return new URL(apiBaseUrl).pathname.replace(/\/$/, "");
    } catch {
      return "/api/dev-api/software-auto/v1";
    }
  })();

  async function readJson(response) {
    const text = await response.text();
    if (text === "") return undefined;
    try {
      return JSON.parse(text);
    } catch {
      return { __raw: text.slice(0, 400) };
    }
  }

  async function request(path, { method = "GET", body, token, timeoutOverrideMs } = {}) {
    const headers = {};
    if (body !== undefined) headers["content-type"] = "application/json";
    if (token !== undefined) headers.authorization = `Bearer ${token}`;
    let response;
    try {
      response = await doFetch(`${origin}${path}`, {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(timeoutOverrideMs ?? timeoutMs),
      });
    } catch (error) {
      throw new HubError(`无法连接站点：${error?.message ?? error}`, {
        code: error?.name === "TimeoutError" ? "hub/timeout" : "hub/network",
      });
    }
    return { response, payload: await readJson(response) };
  }

  return {
    /** ① 申请设备码（免登录）。 */
    async startDeviceAuth() {
      const { response, payload } = await request(DEVICE_AUTH_PATH, { method: "POST", body: {} });
      if (!response.ok) {
        throw new HubError(`申请设备码失败（HTTP ${response.status}）`, {
          code: "hub/device-start-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const deviceCode = payload?.deviceCode;
      const userCode = payload?.userCode;
      const verificationUrl = payload?.verificationUrl;
      if (typeof deviceCode !== "string" || typeof userCode !== "string") {
        throw new HubError("设备码响应缺少 deviceCode/userCode", {
          code: "hub/device-start-malformed",
          body: payload,
        });
      }
      return {
        deviceCode,
        userCode,
        verificationUrl:
          typeof verificationUrl === "string" && verificationUrl !== ""
            ? verificationUrl
            : `${origin}/device/authorize?device=${encodeURIComponent(deviceCode)}&client=ming-tea`,
        expiresIn: typeof payload?.expiresIn === "number" ? payload.expiresIn : 600,
        interval: typeof payload?.interval === "number" && payload.interval > 0 ? payload.interval : 3,
      };
    },

    /** ③ 轮询授权结果：pending / approved（带一次性 token 与 user）/ expired。 */
    async pollDeviceAuth(deviceCode) {
      const { response, payload } = await request(`${DEVICE_AUTH_PATH}/${encodeURIComponent(deviceCode)}`);
      if (response.status === 404) return { status: "expired" };
      if (!response.ok) {
        throw new HubError(`轮询设备授权失败（HTTP ${response.status}）`, {
          code: "hub/device-poll-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const status = typeof payload?.status === "string" ? payload.status : "pending";
      if (status === "approved") {
        if (typeof payload?.token !== "string" || payload.token === "") {
          throw new HubError("授权已通过但响应里没有 token", {
            code: "hub/device-poll-malformed",
            body: payload,
          });
        }
        return { status: "approved", token: payload.token, user: payload.user ?? null };
      }
      if (status === "expired") return { status: "expired" };
      return { status: "pending" };
    },

    /** ② 额度：未登录返回 signedIn:false；401/403 视为过期（不抛错，界面据此提示重新登录）。 */
    async fetchUsage(token) {
      if (typeof token !== "string" || token === "") return { signedIn: false, expired: false };
      const { response, payload } = await request(`${apiBasePath}/usage`, { token });
      if (response.status === 401 || response.status === 403) return { signedIn: false, expired: true };
      if (!response.ok) {
        // 5xx 是站点故障（实测遇过 500 softwareTierAutoQuota is not defined）：
        // 必须与「登录过期」区分开，否则会把用户无辜登出
        throw new HubError(`读取额度失败（HTTP ${response.status}）`, {
          code: response.status >= 500 ? "hub/site-error" : "hub/usage-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const normalized = normalizeUsage(payload);
      // 站点把档位放在 plan（付费档含到期时间）；Auto 赠送次数在 auto_free
      // 站点实测有两种形态：免费档给 `plan:{key,name,...}`，付费档给 `tier:{key,name,monthly_budget_rmb,
      // auto_free_calls,concurrency,...}` 且 plan 为 null。两者都归一成同一个 plan 对象。
      const planSource =
        payload?.plan && typeof payload.plan === "object"
          ? payload.plan
          : payload?.tier && typeof payload.tier === "object"
            ? payload.tier
            : undefined;
      const num0 = (v) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
      const plan =
        planSource !== undefined
          ? {
              key: typeof planSource.key === "string" ? planSource.key : undefined,
              name: typeof planSource.name === "string" ? planSource.name : undefined,
              expiresAt:
                typeof planSource.expires_at === "string"
                  ? Date.parse(planSource.expires_at)
                  : num0(planSource.expires_at),
              isMember: planSource.is_member === true || payload?.is_member === true,
              // 付费档新增：档位自带的并发上限与 Auto 赠送次数
              concurrency: num0(planSource.concurrency),
              autoFreeCalls: num0(planSource.auto_free_calls),
              monthlyBudgetRmb: num0(planSource.monthly_budget_rmb),
            }
          : typeof payload?.plan === "string"
            ? { key: payload.plan, name: payload.plan }
            : typeof payload?.tier_name === "string"
              ? { key: payload.tier_name.toLowerCase(), name: payload.tier_name }
              : undefined;
      const autoFree =
        payload?.auto_free && typeof payload.auto_free === "object"
          ? {
              limit: num0(payload.auto_free.limit),
              used: num0(payload.auto_free.used),
              remaining: num0(payload.auto_free.remaining),
              exhausted: payload.auto_free.exhausted === true,
              // configured:false 表示这个档位没有 Auto 赠送（实测免费档就没有）
              configured: payload.auto_free.configured !== false,
            }
          : undefined;
      const availableTiers = Array.isArray(payload?.available_tiers)
        ? payload.available_tiers
        : Array.isArray(payload?.software_auto?.available_tiers)
          ? payload.software_auto.available_tiers
          : [];
      // catalog：站点 2026-09-27 起用它承载「上下文长度 + 各模型价目」，
      // 顶层另有 tier 的档位信息；旧字段 available_tiers 在付费档已不再返回。
      const catalog = Array.isArray(payload?.catalog) ? payload.catalog : [];
      const contextOf = (id) => {
        const hit = catalog.find((row) => row?.id === id);
        const raw = hit?.context_window ?? hit?.context_tokens;
        if (typeof raw === "number") return raw;
        // 站点把 auto 写成 "256K" 这种字符串
        const m = typeof raw === "string" ? /^(\d+(?:\.\d+)?)\s*([KkMm])$/.exec(raw.trim()) : null;
        return m ? Math.round(Number.parseFloat(m[1]) * (m[2].toLowerCase() === "m" ? 1_000_000 : 1_000)) : undefined;
      };
      return {
        signedIn: true,
        ...normalized,
        availableTiers,
        plan,
        autoFree,
        catalog,
        contextOf,
        raw: payload,
      };
    },

    /** 账号可用的模型（免费层是 Auto 池与实测延迟；付费层给付费模型与可用性）。 */
    async fetchModels(token) {
      if (typeof token !== "string" || token === "") return { signedIn: false, models: [] };
      const { response, payload } = await request(`${apiBasePath}/models`, { token });
      if (response.status === 401 || response.status === 403) return { signedIn: false, expired: true, models: [] };
      if (!response.ok) {
        throw new HubError(`读取模型列表失败（HTTP ${response.status}）`, {
          code: response.status >= 500 ? "hub/site-error" : "hub/models-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const models = normalizeModels(payload);
      const tierName = typeof payload?.tier?.name === "string" ? payload.tier.name : undefined;
      return { signedIn: true, models, tierName, raw: payload };
    },

    /** 站点公开的档位目录：各档价格、额度、并发上限、Auto 赠送次数，以及上下文目录。
     * 免登录即可取；失败返回 null（面板降级为只用 /usage 里的字段）。 */
    async fetchTiers() {
      const { response, payload } = await request(TIERS_PATH);
      if (!response.ok) return null;
      const tiers = Array.isArray(payload?.tiers) ? payload.tiers : [];
      const free = payload?.free && typeof payload.free === "object" ? payload.free : null;
      const contextCatalog = Array.isArray(payload?.context_catalog) ? payload.context_catalog : [];
      const release = payload?.product?.release;
      return {
        free,
        tiers,
        contextCatalog,
        // 发行信息：站点侧没发布安装包时 published:false（与我们的"检查更新"互补）
        release: release && typeof release === "object" ? release : null,
      };
    },

    /** 更新清单：未发布（404 + 业务码）返回 unpublished，界面当作「已是最新」静默处理。 */
    async fetchUpdateManifest() {
      const { response, payload } = await request(UPDATE_MANIFEST_PATH);
      if (response.status === 404) {
        return {
          status: "unpublished",
          code: typeof payload?.code === "string" ? payload.code : UNPUBLISHED_CODE,
        };
      }
      if (!response.ok) {
        throw new HubError(`读取更新清单失败（HTTP ${response.status}）`, {
          code: "hub/update-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const version = payload?.version;
      if (typeof version !== "string" || version === "") {
        throw new HubError("更新清单缺少 version", { code: "hub/update-malformed", body: payload });
      }
      return {
        status: "published",
        version,
        notes: typeof payload?.notes === "string" ? payload.notes : "",
        pubDate: typeof payload?.pub_date === "string" ? payload.pub_date : "",
        platforms: payload?.platforms ?? null,
      };
    },

    /** 自测用：真的调一次 /chat/completions，确认 token 可用（文档自测第 1 项）。 */
    async probeChat(token, prompt = "你好") {
      const { response, payload } = await request(`${apiBasePath}/chat/completions`, {
        method: "POST",
        token,
        body: { model: "auto", messages: [{ role: "user", content: prompt }], stream: false },
        timeoutOverrideMs: 60000,
      });
      if (!response.ok) {
        throw new HubError(`模型调用失败（HTTP ${response.status}）`, {
          code: response.status === 401 ? "hub/unauthorized" : "hub/chat-failed",
          httpStatus: response.status,
          body: payload,
        });
      }
      const content = payload?.choices?.[0]?.message?.content;
      return {
        model: response.headers?.get?.("X-Scallion-Resolved-Model") ?? payload?.model ?? null,
        content: typeof content === "string" ? content : "",
      };
    },
  };
}
