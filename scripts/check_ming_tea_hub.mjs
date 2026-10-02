#!/usr/bin/env node
// 站点接入的无凭据自测：用假 fetch + 假宿主服务覆盖状态机与归一化逻辑。
//
// 为什么需要它：真正的设备授权要用户本人在浏览器点「同意授权」，没凭据时整条链路
// 没法跑；但**除授权那一下之外**的逻辑（设备码状态机、额度归一化、版本比较、
// 凭证读写、模型路由的写入 ops）都可以离线断言。这些正是最容易写错的部分。
//
// 用法：node scripts/check_ming_tea_hub.mjs   （退出码非 0 表示有用例失败）

import { readFileSync } from "node:fs";

import {
  compareVersions,
  createHubClient,
  DEVICE_AUTH_PATH,
  downloadUrlFor,
  HUB_API_BASE_URL,
  normalizeModels,
  normalizeUsage,
} from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/hub-client.mjs";
import { createAccountStore, isExpired } from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/account-store.mjs";
import { createModelRoute, routeConfig, ROUTE_ID, FALLBACK_MODEL, FALLBACK_PROVIDER } from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/model-route.mjs";
import { openInSystemBrowser, openerCommand } from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/open-external.mjs";
import { buildUsageBoard, describeExpiry } from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/usage-view.mjs";
import { createSummonTrust, shouldAutoAllow } from "../platform/ming-tea/plugins/ming-tea-ui/lib/host/summon-trust.mjs";

let passed = 0;
const failures = [];

function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      passed += 1;
      console.log(`  ✅ ${name}`);
    })
    .catch((error) => {
      failures.push(`${name}: ${error?.message ?? error}`);
      console.log(`  ❌ ${name}\n     ${error?.message ?? error}`);
    });
}

function assert(condition, message) {
  if (!condition) throw new Error(message ?? "断言失败");
}

function assertEqual(actual, expected, message) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${message ?? "值不相等"}：实际 ${a}，期望 ${b}`);
}

/** 假 fetch：按 (method, path) 命中路由表；返回 Response。 */
function fakeFetch(routes) {
  const calls = [];
  const impl = async (url, init = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    calls.push({ method, path, headers: init.headers ?? {}, body: init.body });
    const key = `${method} ${path}`;
    const route = routes[key] ?? routes[path];
    if (route === undefined) {
      return new Response(JSON.stringify({ error: "no fake route", key }), {
        status: 599,
        headers: { "content-type": "application/json" },
      });
    }
    const status = route.status ?? 200;
    const headers = { "content-type": "application/json", ...(route.headers ?? {}) };
    const body = typeof route.body === "string" ? route.body : JSON.stringify(route.body ?? {});
    return new Response(body, { status, headers });
  };
  impl.calls = calls;
  return impl;
}

function jsonRoute(body, status = 200, headers) {
  return { body, status, headers };
}

console.log("== 1. hub-client：设备授权状态机 ==");
await check("鉴权路径是站点给的品牌化别名 /api/ming-tea/auth/*", () => {
  assertEqual(DEVICE_AUTH_PATH, "/api/ming-tea/auth/device");
});

await check("startDeviceAuth 归一化并补出 verificationUrl", async () => {
  const fetchImpl = fakeFetch({
    [`POST ${DEVICE_AUTH_PATH}`]: jsonRoute({ deviceCode: "D1", userCode: "U1", expiresIn: 600, interval: 3 }),
  });
  const hub = createHubClient({ fetchImpl });
  const started = await hub.startDeviceAuth();
  assertEqual(
    { code: started.deviceCode, user: started.userCode, interval: started.interval },
    { code: "D1", user: "U1", interval: 3 },
  );
  assert(started.verificationUrl.includes("device=D1"), "verificationUrl 应带设备码");
});

await check("startDeviceAuth 缺字段就报 malformed（不静默）", async () => {
  const hub = createHubClient({ fetchImpl: fakeFetch({ [`POST ${DEVICE_AUTH_PATH}`]: jsonRoute({ userCode: "U1" }) }) });
  await hub.startDeviceAuth().then(
    () => {
      throw new Error("应当抛错");
    },
    (error) => assertEqual(error.code, "hub/device-start-malformed"),
  );
});

await check("轮询：pending / approved(带 token) / 404=expired", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({
      [`${DEVICE_AUTH_PATH}/OK`]: jsonRoute({ status: "approved", token: "JWT-1", user: { id: "u", name: "旅行者" } }),
      [`${DEVICE_AUTH_PATH}/WAIT`]: jsonRoute({ status: "pending" }),
      [`${DEVICE_AUTH_PATH}/GONE`]: jsonRoute({ code: "device_not_found" }, 404),
    }),
  });
  assertEqual((await hub.pollDeviceAuth("WAIT")).status, "pending");
  const approved = await hub.pollDeviceAuth("OK");
  assertEqual({ s: approved.status, t: approved.token, n: approved.user.name }, { s: "approved", t: "JWT-1", n: "旅行者" });
  assertEqual((await hub.pollDeviceAuth("GONE")).status, "expired");
});

await check("approved 但没有 token → malformed", async () => {
  const hub = createHubClient({ fetchImpl: fakeFetch({ [`${DEVICE_AUTH_PATH}/X`]: jsonRoute({ status: "approved" }) }) });
  await hub.pollDeviceAuth("X").then(
    () => {
      throw new Error("应当抛错");
    },
    (error) => assertEqual(error.code, "hub/device-poll-malformed"),
  );
});

console.log("\n== 2. hub-client：额度归一化 ==");
await check("免费层 → 百分比 + 「本月剩余 x/y 次」", () => {
  const usage = normalizeUsage({ monthly_limit: 100, monthly_used: 30, monthly_remaining: 70 });
  assertEqual({ tier: usage.tier, percent: usage.percent, detail: usage.detail }, { tier: "free", percent: 70, detail: "本月剩余 70/100 次" });
});

await check("付费层 → 用 used_percent 反推剩余百分比", () => {
  const usage = normalizeUsage({ budget_rmb: 280, used_rmb: 28, remaining_rmb: 252, used_percent: 10 });
  assertEqual(
    { tier: usage.tier, percent: usage.percent, detail: usage.detail, paidBudget: usage.paid.budget },
    { tier: "paid", percent: 90, detail: "剩余 ¥252.00（已用 10%）", paidBudget: 280 },
  );
});

await check("付费层 → 读出档位名与月度额度", () => {
  const usage = normalizeUsage({ tier: { name: "Plus" }, monthly_budget_rmb: 120, remaining_rmb: 90, used_rmb: 30, used_percent: 25 });
  assertEqual({ tier: usage.tier, tierName: usage.tierName, budget: usage.paid.budget, percent: usage.percent }, { tier: "paid", tierName: "Plus", budget: 120, percent: 75 });
});

await check("normalizeModels：三种形态 + available 过滤 + 去重", () => {
  const openai = normalizeModels({ data: [{ id: "a" }, { id: "b", available: false }] });
  assertEqual(openai.map((m) => m.id), ["a", "b"]);
  assertEqual(openai[1].available, false);
  const dict = normalizeModels({ models: { auto: { name: "Auto" }, x: { id: "x" } } });
  assertEqual(dict.map((m) => m.id), ["auto", "x"]);
  assertEqual(dict[0].name, "Auto");
  const dupes = normalizeModels({ models: [{ id: "a" }, { id: "a" }] });
  assertEqual(dupes.length, 1);
  assertEqual(normalizeModels(null).length, 0);
});

await check("站点实测形态：healthy/last_error 决定可用性，plan.name 作档位名", () => {
  // 优先级：available（文档写法） > healthy（站点实测字段） > last_error 为空才可用
  const models = normalizeModels({
    data: [
      { id: "gpt-5.6-luna", healthy: true, latency_ms: 15157, last_error: "" },
      { id: "unhealthy", healthy: false, last_error: "boom" },
      { id: "error-only", last_error: "upstream 503" },
      { id: "explicit", available: false, healthy: true },
      { id: "bare" },
    ],
  });
  assertEqual(
    models.map((m) => `${m.id}:${m.available}`),
    ["gpt-5.6-luna:true", "unhealthy:false", "error-only:false", "explicit:false", "bare:true"],
  );
  assertEqual(models[0].latencyMs, 15157);

  const usage = normalizeUsage({
    monthly_limit: 100,
    monthly_remaining: 100,
    monthly_used: 0,
    mode: "free",
    plan: { key: "free", name: "Free", auto_monthly_calls: 300 },
  });
  assertEqual({ tier: usage.tier, tierName: usage.tierName, percent: usage.percent }, { tier: "free", tierName: "Free", percent: 100 });
});

await check("两层额度同时存在：都保留，活跃档取付费", () => {
  const usage = normalizeUsage({
    monthly_limit: 100,
    monthly_used: 70,
    monthly_remaining: 30,
    budget_rmb: 120,
    used_rmb: 30,
    remaining_rmb: 90,
    used_percent: 25,
  });
  assertEqual(
    {
      active: usage.tier,
      ringPercent: usage.percent,
      freeRemaining: usage.free.remaining,
      paidRemaining: usage.paid.remaining,
    },
    { active: "paid", ringPercent: 75, freeRemaining: 30, paidRemaining: 90 },
  );
});

await check("站点 5xx → hub/site-error（不能当成登录过期）", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({
      "/api/dev-api/software-auto/v1/usage": jsonRoute({ error: { message: "softwareTierAutoQuota is not defined" } }, 500),
    }),
  });
  await hub.fetchUsage("t").then(
    () => {
      throw new Error("应当抛错");
    },
    (error) => assertEqual({ code: error.code, status: error.httpStatus }, { code: "hub/site-error", status: 500 }),
  );
  const modelsHub = createHubClient({
    fetchImpl: fakeFetch({ "/api/dev-api/software-auto/v1/models": jsonRoute({}, 503) }),
  });
  await modelsHub.fetchModels("t").then(
    () => {
      throw new Error("应当抛错");
    },
    (error) => assertEqual(error.code, "hub/site-error"),
  );
});

await check("付费档 plan / auto_free 解析（与站点新文档对齐）", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({
      "/api/dev-api/software-auto/v1/usage": jsonRoute({
        plan: { key: "plus", name: "Plus", expires_at: "2026-10-27T00:00:00.000Z", is_member: true },
        auto_free: { limit: 200, used: 5, remaining: 195, exhausted: false },
        budget_rmb: 120,
        used_rmb: 6,
        remaining_rmb: 114,
        used_percent: 5,
      }),
    }),
  });
  const usage = await hub.fetchUsage("t");
  assertEqual(
    {
      planKey: usage.plan.key,
      planName: usage.plan.name,
      isMember: usage.plan.isMember,
      autoFreeRemaining: usage.autoFree.remaining,
      paidRemaining: usage.paid.remaining,
    },
    { planKey: "plus", planName: "Plus", isMember: true, autoFreeRemaining: 195, paidRemaining: 114 },
  );
  assert(typeof usage.plan.expiresAt === "number", "到期时间应解析成时间戳");
});

await check("付费档实测形态：tier 对象 + auto_free.configured + catalog（站点 2026-09-27 修复后）", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({
      "/api/dev-api/software-auto/v1/usage": jsonRoute({
        mode: "paid",
        plan: null,
        tier: {
          key: "plus",
          name: "Plus",
          price_rmb: 19.9,
          monthly_budget_rmb: 120,
          auto_free_calls: 200,
          concurrency: 5,
          entitlement_type: "plus",
        },
        tier_name: "Plus",
        budget_rmb: 120,
        used_rmb: 0,
        remaining_rmb: 120,
        used_percent: 0,
        auto_free: { limit: 200, used: 1, remaining: 199, exhausted: false, configured: true },
        catalog: [
          { id: "auto", context_window: "256K" },
          { id: "deepseek-v4.1-flash", context_window: "1M" },
        ],
      }),
    }),
  });
  const usage = await hub.fetchUsage("t");
  assertEqual(
    {
      tier: usage.tier,
      tierName: usage.tierName,
      percent: usage.percent,
      planKey: usage.plan.key,
      concurrency: usage.plan.concurrency,
      autoFreeCalls: usage.plan.autoFreeCalls,
      autoFreeRemaining: usage.autoFree.remaining,
      configured: usage.autoFree.configured,
    },
    {
      tier: "paid",
      tierName: "Plus",
      percent: 100,
      planKey: "plus",
      concurrency: 5,
      autoFreeCalls: 200,
      autoFreeRemaining: 199,
      configured: true,
    },
  );
  // catalog 的上下文值要能从 "256K"/"1M" 解析成数字
  assertEqual(usage.contextOf("auto"), 256000);
  assertEqual(usage.contextOf("deepseek-v4.1-flash"), 1000000);
  assertEqual(usage.contextOf("不存在"), undefined);
});

await check("免费档 auto_free.configured=false 时不显示「Auto 赠送」", () => {
  const usage = normalizeUsage({ monthly_limit: 100, monthly_remaining: 90, auto_free: { configured: false } });
  assertEqual({ tier: usage.tier, percent: usage.percent }, { tier: "free", percent: 90 });
});

await check("available_tiers 透传（面板用来列可升级档位）", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({
      "/api/dev-api/software-auto/v1/usage": jsonRoute({
        monthly_limit: 100,
        monthly_remaining: 100,
        available_tiers: [{ name: "Plus", price_rmb: 19.9, monthly_budget_rmb: 120 }],
      }),
    }),
  });
  const usage = await hub.fetchUsage("t");
  assertEqual(usage.availableTiers.map((t) => t.name), ["Plus"]);
});

await check("字段缺失 → 不编造百分比", () => {
  const usage = normalizeUsage({ something_else: 1 });
  assertEqual({ tier: usage.tier, percent: usage.percent }, { tier: "unknown", percent: null });
});

await check("401/403 → signedIn:false + expired:true（不抛错）", async () => {
  const hub = createHubClient({ fetchImpl: fakeFetch({ "/api/dev-api/software-auto/v1/usage": jsonRoute({ error: "unauthorized" }, 401) }) });
  assertEqual(await hub.fetchUsage("bad-token"), { signedIn: false, expired: true });
});

await check("空 token 不发起请求", async () => {
  const fetchImpl = fakeFetch({});
  const hub = createHubClient({ fetchImpl });
  assertEqual(await hub.fetchUsage(""), { signedIn: false, expired: false });
  assertEqual(fetchImpl.calls.length, 0);
});

await check("usage 请求打在 API Base 路径下（不是站点根）", async () => {
  const fetchImpl = fakeFetch({ "/api/dev-api/software-auto/v1/usage": jsonRoute({ monthly_limit: 100, monthly_remaining: 100 }) });
  const hub = createHubClient({ fetchImpl });
  await hub.fetchUsage("t");
  assertEqual(fetchImpl.calls[0].path, new URL(HUB_API_BASE_URL).pathname + "/usage");
  assertEqual(fetchImpl.calls[0].headers.authorization, "Bearer t");
});

await check("fetchModels：带 Bearer 打 /models，401 → expired", async () => {
  const ok = fakeFetch({ "/api/dev-api/software-auto/v1/models": jsonRoute({ data: [{ id: "auto" }, { id: "deepseek-v4.1-flash", available: true }] }) });
  const hub = createHubClient({ fetchImpl: ok });
  const listed = await hub.fetchModels("t");
  assertEqual(listed.models.map((m) => m.id), ["auto", "deepseek-v4.1-flash"]);
  assertEqual(ok.calls[0].headers.authorization, "Bearer t");

  const unauthorized = createHubClient({ fetchImpl: fakeFetch({ "/api/dev-api/software-auto/v1/models": jsonRoute({}, 401) }) });
  assertEqual((await unauthorized.fetchModels("bad")).expired, true);
});

console.log("\n== 3. hub-client：更新清单与版本比较 ==");
await check("404 + 业务码 → unpublished（界面当作已是最新）", async () => {
  const hub = createHubClient({ fetchImpl: fakeFetch({ "/api/ming-tea/update": jsonRoute({ code: "ming_tea_release_not_published" }, 404) }) });
  assertEqual(await hub.fetchUpdateManifest(), { status: "unpublished", code: "ming_tea_release_not_published" });
});

await check("200 且带 version → published", async () => {
  const hub = createHubClient({
    fetchImpl: fakeFetch({ "/api/ming-tea/update": jsonRoute({ version: "1.1.2", notes: "修复", pub_date: "2026-09-27", platforms: {} }) }),
  });
  const manifest = await hub.fetchUpdateManifest();
  assertEqual({ s: manifest.status, v: manifest.version, n: manifest.notes }, { s: "published", v: "1.1.2", n: "修复" });
});

await check("200 但没有 version → malformed", async () => {
  const hub = createHubClient({ fetchImpl: fakeFetch({ "/api/ming-tea/update": jsonRoute({ platforms: {} }) }) });
  await hub.fetchUpdateManifest().then(
    () => {
      throw new Error("应当抛错");
    },
    (error) => assertEqual(error.code, "hub/update-malformed"),
  );
});

await check("compareVersions：相等/大于/小于/前缀 v/补零/不可解析", () => {
  assertEqual(compareVersions("1.0.0", "1.0.0"), 0);
  assertEqual(compareVersions("1.2.0", "1.1.9"), 1);
  assertEqual(compareVersions("1.0.0", "1.0.1"), -1);
  assertEqual(compareVersions("v1.1.0", "1.1.0"), 0);
  assertEqual(compareVersions("1.1", "1.1.0"), 0);
  assertEqual(compareVersions("abc", "1.0.0"), null);
});

await check("下载地址按平台/架构映射", () => {
  assert(downloadUrlFor("darwin", "arm64").endsWith("platform=darwin&arch=arm64"), "darwin/arm64");
  assert(downloadUrlFor("win32", "x64").endsWith("platform=windows&arch=x64"), "win32→windows");
  assert(downloadUrlFor("linux", "arm64").endsWith("platform=linux&arch=arm64"), "linux/arm64");
});

console.log("\n== 4. 用系统浏览器打开授权页 ==");
await check("按平台选打开命令", () => {
  assertEqual(openerCommand("darwin"), { command: "open", args: [] });
  assertEqual(openerCommand("win32").command, "cmd");
  assertEqual(openerCommand("linux"), { command: "xdg-open", args: [] });
});

await check("打开时把 URL 作为参数传入，且不阻塞", () => {
  const calls = [];
  const fakeSpawn = (command, args) => {
    calls.push({ command, args });
    return { on() {}, unref() {} };
  };
  const result = openInSystemBrowser("https://sca-hub.cn/device/authorize?device=X", {
    platform: "darwin",
    spawnImpl: fakeSpawn,
  });
  assertEqual(result, { ok: true, command: "open" });
  assertEqual(calls[0].args, ["https://sca-hub.cn/device/authorize?device=X"]);
});

await check("只允许 http(s)，非链接直接拒绝", () => {
  assertEqual(openInSystemBrowser("file:///etc/passwd", { spawnImpl: () => ({}) }).ok, false);
  assertEqual(openInSystemBrowser("javascript:alert(1)", { spawnImpl: () => ({}) }).ok, false);
});

await check("spawn 抛错 → 如实返回失败（不抛出）", () => {
  const result = openInSystemBrowser("https://example.com", {
    platform: "linux",
    spawnImpl: () => {
      throw new Error("no xdg-open");
    },
  });
  assertEqual(result.ok, false);
  assert(result.error.includes("no xdg-open"), "应带原始错误");
});

console.log("\n== 5. 凭证存储（假 credentials 服务） ==");
function stubCtx({ settings } = {}) {
  const store = new Map();
  const records = new Map();
  const credentials = {
    async resolve(ref) {
      return store.has(ref) ? { value: store.get(ref), source: "file" } : undefined;
    },
    async set(ref, value) {
      store.set(ref, value);
    },
    async unset(ref) {
      store.delete(ref);
    },
    async readRecord(key) {
      return records.get(key);
    },
    async modifyRecord(key, mutate) {
      const next = await mutate(records.get(key));
      if (next === undefined) records.delete(key);
      else records.set(key, next);
      return records.get(key);
    },
    async deleteRecord(key) {
      records.delete(key);
    },
  };
  return {
    __store: store,
    __records: records,
    get(name) {
      if (name === "credentials") return credentials;
      if (name === "settings") return settings;
      return undefined;
    },
  };
}

await check("token 读写与隐式不泄露（只存引用值）", async () => {
  const ctx = stubCtx();
  const account = createAccountStore(ctx);
  assertEqual(await account.readToken(), null);
  await account.saveToken("JWT-ABC");
  assertEqual(await account.readToken(), "JWT-ABC");
  await account.clearToken();
  assertEqual(await account.readToken(), null);
});

await check("账号记录 + status（未过期 / 已过期）", async () => {
  const nowRef = { value: 1_000_000 };
  const ctx = stubCtx();
  const account = createAccountStore(ctx, { now: () => nowRef.value });
  await account.saveAccount({ user: { name: "旅行者5361" }, expiresAt: 1_000_000 + 60_000 });
  await account.saveToken("JWT");
  assertEqual((await account.status()).signedIn, true);
  nowRef.value = 1_000_000 + 60_001;
  const expired = await account.status();
  assertEqual({ signedIn: expired.signedIn, expired: expired.expired }, { signedIn: false, expired: true });
  await account.clearAccount();
  assertEqual(await account.readAccount(), null);
});

await check("isExpired：缺字段视为过期（宁可要求重新登录）", () => {
  assert(isExpired(undefined, 1) === true, "undefined 应算过期");
  assert(isExpired(200, 100) === false, "未来时间不算过期");
});

console.log("\n== 6. 模型路由写入 ops（假 settings 服务） ==");
function settingsStub({ current = { provider: FALLBACK_PROVIDER, model: FALLBACK_MODEL }, conflictOnce = false } = {}) {
  const calls = [];
  let conflicted = false;
  return {
    calls,
    describe() {
      return [{ ns: "agent-default-model", value: { provider: current.provider, model: current.model } }];
    },
    async mutate(ns, ops, revision) {
      calls.push({ ns, ops, revision });
      if (conflictOnce && !conflicted && revision === undefined) {
        conflicted = true;
        const error = new Error("conflict");
        error.code = "SETTINGS_CONFLICT";
        error.actual = 7;
        throw error;
      }
    },
  };
}

await check("ensureRoute 写入 providers.<route>，含 baseURL 与凭证引用", async () => {
  const settings = settingsStub();
  const route = createModelRoute(stubCtx({ settings }));
  await route.ensureRoute();
  const call = settings.calls[0];
  assertEqual(call.ns, "llm-pi-ai");
  assertEqual(call.ops[0].path, ["providers", ROUTE_ID]);
  assertEqual(call.ops[0].value.apiKeyEnv, "MING_TEA_HUB_TOKEN");
  assertEqual(call.ops[0].value.api, "openai-completions");
  assert(call.ops[0].value.baseURL.startsWith("https://sca-hub.cn/"), "baseURL 指向站点");
});

await check("routeConfig：auto 恒在首位、跳过不可用、去重", () => {
  const config = routeConfig(undefined, [
    { id: "auto", name: "重复的 auto" },
    { id: "deepseek-v4.1-flash", name: "DS 付费", available: true },
    { id: "step-3.7-flash", name: "不可用", available: false },
  ]);
  assertEqual(config.models.map((m) => m.id), ["auto", "deepseek-v4.1-flash"]);
  assertEqual(config.models[1].name, "DS 付费");
});

await check("看屏幕：实测能看图的模型声明 image（含 auto），其余只声明文本", () => {
  // 背景（2026-10-02 实测）：不声明 image 时截图到不了模型，
  // read_image 直接报 `model "…" does not declare image input`，代理只能盲点像素。
  // auto 必须声明：免费层用户只有 auto 可选，不声明等于他们永远用不了「看屏幕」；
  // 2026-10-02 用真实截图连测 4 次 auto 均正确读图（上游 gpt-5.4-nano / claude-haiku-4-5）。
  const config = routeConfig(undefined, [
    { id: "deepseek-v4.1-flash", available: true },
    { id: "mimo-v2.6-flash", available: true },
    { id: "glm-5.3", available: true },
    { id: "glm-5.2", available: true },
    { id: "step-3.7-flash", available: true },
  ]);
  const input = (id) => config.models.find((m) => m.id === id)?.input;
  assertEqual(input("auto"), ["text", "image"], "免费层唯一的模型，必须能看图");
  assertEqual(input("deepseek-v4.1-flash"), ["text", "image"], "钉住的视觉模型必须声明 image");
  assertEqual(input("mimo-v2.6-flash"), ["text", "image"]);
  assertEqual(input("glm-5.3"), ["text", "image"]);
  assertEqual(input("glm-5.2"), ["text"], "实测不能看图 ⇒ 只声明文本");
  assertEqual(input("step-3.7-flash"), ["text"]);
  assert(
    config.models.every((m) => Array.isArray(m.input) && m.input.includes("text")),
    "每个模型都至少声明文本",
  );
});

await check("useAsDefault 只改 provider/model 两个字段", async () => {
  const settings = settingsStub();
  const route = createModelRoute(stubCtx({ settings }));
  await route.useAsDefault();
  assertEqual(settings.calls[0].ops, [
    { op: "set", path: ["provider"], value: ROUTE_ID },
    { op: "set", path: ["model"], value: "auto" },
  ]);
});

await check("登出：默认仍指向我们 → 回退；用户已自选 → 不动", async () => {
  const ours = settingsStub({ current: { provider: ROUTE_ID, model: "auto" } });
  const routeOurs = createModelRoute(stubCtx({ settings: ours }));
  assertEqual(await routeOurs.restoreDefaultIfOurs(), {
    reverted: true,
    current: { provider: FALLBACK_PROVIDER, model: FALLBACK_MODEL },
  });
  const theirs = settingsStub({ current: { provider: "my-deepseek", model: "deepseek-chat" } });
  const routeTheirs = createModelRoute(stubCtx({ settings: theirs }));
  assertEqual(await routeTheirs.restoreDefaultIfOurs(), {
    reverted: false,
    current: { provider: "my-deepseek", model: "deepseek-chat" },
  });
  assertEqual(theirs.calls.length, 0, "不该写入任何配置");
});

await check("写入冲突 → 拿真实 revision 重试一次", async () => {
  const settings = settingsStub({ conflictOnce: true });
  const route = createModelRoute(stubCtx({ settings }));
  await route.useAsDefault();
  assertEqual(settings.calls.length, 2);
  assertEqual(settings.calls[1].revision, 7);
});

console.log("\n== 7. 用量看板：把 /usage 整形成界面可直接渲染的模型 ==");

await check("未登录：给出可操作提示，且不编造任何计量条", async () => {
  const board = buildUsageBoard({ signedIn: false, expired: false });
  assertEqual(board.signedIn, false);
  assertEqual(board.expired, false);
  assertEqual(board.heading, "尚未登录");
  assertEqual(board.meters.length, 0, "未登录不应有计量条");
  assertEqual(board.facts.length, 0);
  assert(board.detail.includes("登录"), "提示里要告诉用户去登录");
  assert(board.notes.length > 0, "要说明未登录时不请求站点");
});

await check("登录过期：与「未登录」区分开，且不谎报额度", async () => {
  const board = buildUsageBoard({ signedIn: false, expired: true });
  assertEqual(board.expired, true);
  assertEqual(board.heading, "登录已过期");
  assertEqual(board.meters.length, 0);
});

await check("付费档：Auto 赠送次数与付费层余额各一条，顺序与口径正确", async () => {
  const board = buildUsageBoard(
    {
      signedIn: true,
      detail: "剩余 ¥120.00",
      tierName: "Plus",
      autoFree: { limit: 200, used: 1, remaining: 199, exhausted: false, configured: true },
      paid: { budget: 120, used: 0, remaining: 120, percent: 100 },
      free: null,
      plan: { name: "Plus", expiresAt: Date.UTC(2026, 9, 6), concurrency: 5 },
      availableTiers: [
        { key: "plus", name: "Plus", auto_free_calls: 200, monthly_budget_rmb: 120, concurrency: 5 },
      ],
    },
    { now: Date.UTC(2026, 8, 30) },
  );
  assertEqual(board.signedIn, true);
  assertEqual(board.meters.map((m) => m.key), ["auto-free", "paid"]);
  const [auto, paid] = board.meters;
  assertEqual(auto.percent, 100, "199/200 取整后是 100%");
  assertEqual(auto.used, 1);
  assertEqual(paid.limit, 120);
  assertEqual(paid.remaining, 120);
  assertEqual(paid.unit, "¥");
  assertEqual(board.facts.find((f) => f.key === "tier").value, "Plus");
  assertEqual(board.facts.find((f) => f.key === "expiry").value, "2026-10-06（6 天）");
  assertEqual(board.facts.find((f) => f.key === "concurrency").value, "5");
  assertEqual(board.tiers.length, 1);
  assertEqual(board.tiers[0].autoFreeCalls, 200);
  assertEqual(board.tiers[0].monthlyBudgetRmb, 120);
});

await check("Auto 赠送用尽：如实说明「不硬封、改从余额扣」", async () => {
  const board = buildUsageBoard({
    signedIn: true,
    autoFree: { limit: 200, used: 200, remaining: 0, exhausted: true, configured: true },
    paid: { budget: 120, used: 12, remaining: 108 },
  });
  const auto = board.meters.find((m) => m.key === "auto-free");
  assertEqual(auto.remaining, 0);
  assertEqual(auto.percent, 0);
  assert(auto.note.includes("不会硬性停用"), "必须写明不会硬性停用");
});

await check("站点只给部分字段：缺的降级，不显示假 0", async () => {
  const board = buildUsageBoard({
    signedIn: true,
    // configured:false 表示这个档位没有 Auto 赠送（实测免费档就没有），不该画一条 0/0
    autoFree: { configured: false },
    paid: null,
    free: { limit: 100, used: 3, remaining: 97 },
    plan: null,
    availableTiers: [],
  });
  assertEqual(board.meters.map((m) => m.key), ["free"]);
  assertEqual(board.meters[0].percent, 97);
  assertEqual(board.facts.find((f) => f.key === "tier").value, "—");
  assertEqual(board.meters.some((m) => m.limit === 0), false, "不能出现编造的 0 额度");
});

await check("剩余占比：总数缺失或非正时是 null（界面显示「—」）", async () => {
  const board = buildUsageBoard({
    signedIn: true,
    paid: { budget: 0, remaining: 0 },
    free: { remaining: 5 },
  });
  assertEqual(board.meters.find((m) => m.key === "paid").percent, null, "budget 为 0 时不能算出 0%");
  assertEqual(board.meters.find((m) => m.key === "free").percent, null, "没有总数时不能假装 0%");
});

await check("describeExpiry：可注入 now，非法输入返回 null 而不是瞎猜", async () => {
  const at = Date.UTC(2026, 9, 6, 12);
  assertEqual(describeExpiry(at, at - 86_400_000), { text: "2026-10-06", days: 1 });
  assertEqual(describeExpiry(undefined, at), { text: null, days: null });
  assertEqual(describeExpiry("not-a-number", at), { text: null, days: null });
  assertEqual(describeExpiry(at, at + 86_400_000).days, 0, "已过期不显示负数");
});

console.log("\n== 8. 会话级信任：谁可以在已信任会话里免确认 ==");

await check("已信任会话里，看屏幕/点击类操作不再询问", async () => {
  assertEqual(shouldAutoAllow("cua_driver_native__get_accessibility_tree"), true);
  assertEqual(shouldAutoAllow("cua_driver_native__list_windows"), true);
  assertEqual(shouldAutoAllow("cua_driver_native__get_window_state"), true);
  assertEqual(shouldAutoAllow("cua_driver_native__click"), true, "点击是「看一眼再点一下」的常规动作");
});

await check("输入/剪贴板类始终要继续确认（可能打进密码）", async () => {
  assertEqual(shouldAutoAllow("cua_driver_native__type_text"), false);
  assertEqual(shouldAutoAllow("cua_driver_native__press_key"), false);
  assertEqual(shouldAutoAllow("cua_driver_native__paste"), false);
});

await check("上传/提交、删除/安装/系统命令始终要继续确认", async () => {
  assertEqual(shouldAutoAllow("cua_driver_native__upload_file"), false);
  assertEqual(shouldAutoAllow("mcp__playwright-mcp__submit"), false);
  assertEqual(shouldAutoAllow("computer_delete_file"), false);
  assertEqual(shouldAutoAllow("computer_install_package"), false);
  assertEqual(shouldAutoAllow("terminal_run"), false);
});

await check("拿不准就继续问（空名字 fail-closed）", async () => {
  assertEqual(shouldAutoAllow(""), false);
  assertEqual(shouldAutoAllow(undefined), false);
});

await check("信任状态只在本进程内、按会话隔离，且能撤销", async () => {
  const trust = createSummonTrust();
  assertEqual(trust.isTrusted("s1"), false, "默认不信任任何会话");
  trust.noteAskedSession("s1");
  assertEqual(trust.trust(), "s1", "不传 sessionId 时信任「最近来问的那个」");
  assertEqual(trust.isTrusted("s1"), true);
  assertEqual(trust.isTrusted("s2"), false, "不跨会话");
  assertEqual(trust.untrust("s1"), "s1");
  assertEqual(trust.isTrusted("s1"), false, "可撤销");
  const other = createSummonTrust();
  assertEqual(other.isTrusted("s1"), false, "不同实例互不影响（=重启即失效）");
});

await check("客户端产物是最新的：改了 ui-tweaks.js 必须重新构建 lib/client.js", () => {
  // 这条不是「逻辑测试」而是**构建新鲜度**守门：插件的客户端半区是 ui-tweaks.js 打包进
  // lib/client.js 后被宿主缓存的（改了源码不重新构建，页面会一直跑旧包 —— 本项目踩过好几次）。
  // 这里挑两个只在 2026-10-02 才加进去的字符串做指纹。
  const tweaks = readFileSync(new URL("../platform/ming-tea/plugins/ming-tea-ui/ui-tweaks.js", import.meta.url), "utf8");
  const bundle = readFileSync(new URL("../platform/ming-tea/plugins/ming-tea-ui/lib/client.js", import.meta.url), "utf8");
  assert(tweaks.includes("mingTeaHumanizeError"), "源码里应当有这个函数");
  assert(bundle.includes("mingTeaHumanizeError"), "lib/client.js 里也应当有 ⇒ 构建没漏");
  assert(bundle.includes("当前模型不能看图"), "「模型不能看图」的提示要真的进包");
  assert(
    /does not declare image input\|does not support image input/.test(bundle.replace(/\\/g, "")),
    "识别两种官方的「不能收图」错误原文",
  );
});

console.log(`\n结果：${passed} 项通过，${failures.length} 项失败`);
if (failures.length > 0) {
  console.log("失败明细：");
  for (const line of failures) console.log(`  - ${line}`);
  process.exit(1);
}
