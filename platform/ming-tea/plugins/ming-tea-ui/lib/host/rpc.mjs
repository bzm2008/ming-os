// 宿主半区对浏览器暴露的唯一入口：DSH 的同源 RPC 通道 `/ming-tea`。
//
// 为什么用这条通道而不是自建 HTTP 路由：`connection.rpc.handle` 由 DSH 自带
// cookie 鉴权（`admit()`）与同源约束，端点即 `<channel>/<endpoint>`，客户端一行
// `ctx.connection.rpc.call("/ming-tea", "quota.get", {})` 就能拿到结果。
//
// 安全约定：
// - deviceCode 只留在宿主内存，不下发浏览器；
// - JWT 只写 `.credentials.yaml`，**任何日志/返回值都不含 token**；
// - 端点表是白名单，未列出的方法一律拒绝。

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createAccountStore } from "./account-store.mjs";
import {
  compareVersions,
  createHubClient,
  downloadUrlFor,
  TOKEN_LIFETIME_MS,
} from "./hub-client.mjs";
import { createModelRoute, ROUTE_ID } from "./model-route.mjs";
import { openInSystemBrowser } from "./open-external.mjs";
import { buildUsageBoard } from "./usage-view.mjs";

export const CHANNEL = "/ming-tea";

/** 应用版本：优先环境变量（打包时由壳注入），其次读仓库里的 Tauri 配置，都没有就返回 null。
 * 注意版本轴是**桌面应用**的版本（tauri.conf.json），不是 DSH runtime 的版本。 */
export function resolveAppVersion({ env = process.env } = {}) {
  const fromEnv = env.MING_TEA_APP_VERSION;
  if (typeof fromEnv === "string" && fromEnv.trim() !== "") return fromEnv.trim();
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    const repoRoot = join(here, "..", "..", "..", "..", "..", "..");
    const configPath = join(repoRoot, "platform", "ming-tea", "apps", "desktop", "src-tauri", "tauri.conf.json");
    if (!existsSync(configPath)) return null;
    const parsed = JSON.parse(readFileSync(configPath, "utf8"));
    const version = parsed?.version;
    return typeof version === "string" && version !== "" ? version : null;
  } catch {
    return null;
  }
}

/** 把授权通过后的收尾（存 token、写账号、接模型路由、切默认模型）收在一处，
 * 这样「首次登录」和「过期后重新登录」走的是同一条路径。 */
async function completeSignIn({ hub, account, model, poll, log }) {
  await account.saveToken(poll.token);
  const expiresAt = Date.now() + TOKEN_LIFETIME_MS;
  await account.saveAccount({ user: poll.user, expiresAt });
  const steps = { token: true, route: false, defaultModel: false, models: [] };
  // 先问站点有哪些可用模型（付费档会多出具体型号），拿不到就只保留 auto
  try {
    const listed = await hub.fetchModels(poll.token);
    steps.models = Array.isArray(listed.models) ? listed.models : [];
    steps.tierName = listed.tierName;
  } catch (error) {
    log(`读取模型列表失败（只保留 auto）：${String(error?.message ?? error)}`);
  }
  try {
    await model.ensureRoute(undefined, steps.models);
    steps.route = true;
    await model.useAsDefault();
    steps.defaultModel = true;
  } catch (error) {
    // 模型路由失败不影响登录本身：账号已登录、额度可看，用户也能在模型页手动选。
    log(`模型路由写入失败：${String(error?.message ?? error)}`);
  }
  return { user: poll.user ?? null, expiresAt, steps };
}

export function createHubService(ctx, { log = () => {}, hub = createHubClient(), appVersion = resolveAppVersion() } = {}) {
  const account = createAccountStore(ctx);
  const model = createModelRoute(ctx);
  /** 进行中的设备授权（宿主内存，单飞）。 */
  let pending = null;

  function pendingView() {
    if (pending === null) return null;
    return {
      userCode: pending.userCode,
      verificationUrl: pending.verificationUrl,
      expiresAt: pending.expiresAt,
      interval: pending.interval,
    };
  }

  return {
    /** 通路探针：用于确认「宿主 RPC 通道 + 客户端 connection 服务」这套假设成立。 */
    async ping() {
      return { pong: true, at: Date.now() };
    },

    async authStatus() {
      const status = await account.status();
      return { ...status, pending: pendingView() };
    },

    /** ① 申请设备码，并**直接**用系统浏览器打开授权页。
     * 站点对已登录账号会「打开即自动授权」，所以正常路径下用户不需要看码、也不需要点按钮。 */
    async authStart() {
      const started = await hub.startDeviceAuth();
      pending = {
        deviceCode: started.deviceCode,
        userCode: started.userCode,
        verificationUrl: started.verificationUrl,
        interval: started.interval,
        expiresAt: Date.now() + started.expiresIn * 1000,
      };
      const opened = openInSystemBrowser(started.verificationUrl);
      log(
        `已申请设备码并${opened.ok ? "用系统浏览器打开授权页" : `尝试打开授权页失败（${opened.error}）`}（userCode=${started.userCode}）`,
      );
      return { ...pendingView(), opened: opened.ok, openError: opened.ok ? undefined : opened.error };
    },

    /** 重新打开当前待授权的链接（首次被拦截、或用户关掉了标签页时用）。 */
    async authOpen() {
      if (pending === null) return { opened: false, error: "没有进行中的授权，请重新点击登录" };
      const opened = openInSystemBrowser(pending.verificationUrl);
      return { opened: opened.ok, error: opened.ok ? undefined : opened.error, verificationUrl: pending.verificationUrl };
    },

    /** ③ 客户端按 interval 轮询：宿主去问站点，授权通过即落库并接上模型路由。 */
    async authPoll() {
      if (pending === null) {
        const status = await account.status();
        return { status: status.signedIn ? "approved" : "idle", ...status };
      }
      if (Date.now() > pending.expiresAt) {
        pending = null;
        return { status: "expired" };
      }
      const poll = await hub.pollDeviceAuth(pending.deviceCode);
      if (poll.status === "pending") return { status: "pending", ...pendingView() };
      if (poll.status === "expired") {
        pending = null;
        return { status: "expired" };
      }
      const done = await completeSignIn({ hub, account, model, poll, log });
      pending = null;
      log(
        `登录成功：${done.user?.name ?? done.user?.username ?? "（站点未返回用户名）"}；模型路由=${done.steps.route}，默认模型=${done.steps.defaultModel}`,
      );
      return { status: "approved", signedIn: true, expired: false, ...done };
    },

    /** 退出登录：清凭证 + 只在默认仍指向我们时回退模型，并移除我们的路由。 */
    async authSignOut() {
      pending = null;
      await account.clearToken();
      await account.clearAccount();
      let reverted = false;
      try {
        const result = await model.restoreDefaultIfOurs();
        reverted = result.reverted;
        await model.removeRoute();
      } catch (error) {
        log(`登出时回退模型失败（不影响登出）：${String(error?.message ?? error)}`);
      }
      log(`已退出登录；默认模型回退=${reverted}`);
      return { signedIn: false, revertedDefault: reverted };
    },

    /** ② 额度：登录过期时顺手清掉本地凭证，让界面进入「请重新登录」。 */
    async quotaGet() {
      const token = await account.readToken();
      if (token === null) return { signedIn: false, expired: false, fromCache: false };
      const status = await account.status();
      if (status.expired) {
        await account.clearToken();
        return { signedIn: false, expired: true, fromCache: false };
      }
      try {
        const usage = await hub.fetchUsage(token);
        if (usage.signedIn === false && usage.expired === true) {
          await account.clearToken();
          log("站点返回 401：本地凭证已清除，需要重新登录");
          return { signedIn: false, expired: true, fromCache: false };
        }
        return { ...usage, fromCache: false };
      } catch (error) {
        // 站点故障（5xx）时不清凭证、不登出，只如实上报，让界面显示「站点暂时不可用」
        if (error?.code === "hub/site-error") {
          log(`站点返回 ${error.httpStatus}：额度读取失败（站点故障，不改本地登录态）`);
        }
        throw error;
      }
    },

    /** ③ 检查更新：未发布（404 + 业务码）静默当作「已是最新」。 */
    async updateCheck() {
      const current = appVersion;
      const manifest = await hub.fetchUpdateManifest();
      if (manifest.status === "unpublished") {
        return {
          status: "latest",
          reason: "unpublished",
          current,
          channel: "https://sca-hub.cn/api/ming-tea/update",
          message: current === null ? "站点暂无发布包（开发环境）" : `已是最新（${current}）`,
        };
      }
      if (current === null) {
        return {
          status: "unknown-current",
          latest: manifest.version,
          message: `站点最新版本 ${manifest.version}；当前为开发环境，无法比对`,
          url: downloadUrlFor(),
          notes: manifest.notes,
        };
      }
      const order = compareVersions(manifest.version, current);
      if (order === null) {
        return {
          status: "unknown-current",
          latest: manifest.version,
          current,
          message: `站点版本 ${manifest.version}，本机版本 ${current}（无法比较）`,
          url: downloadUrlFor(),
        };
      }
      if (order > 0) {
        return {
          status: "available",
          current,
          latest: manifest.version,
          message: `有新版本 ${manifest.version}（当前 ${current}）`,
          url: downloadUrlFor(),
          notes: manifest.notes,
          pubDate: manifest.pubDate,
          channel: "https://sca-hub.cn/api/ming-tea/update",
        };
      }
      return { status: "latest", current, channel: "https://sca-hub.cn/api/ming-tea/update", message: `已是最新（${current}）` };
    },

    /** 站点公开的档位目录（免登录）。失败返回 null，由界面降级。 */
    async tiersGet() {
      try {
        return await hub.fetchTiers();
      } catch (error) {
        log(`读取档位目录失败：${String(error?.message ?? error)}`);
        return null;
      }
    },

    /** 账号可用模型（含档位名）。未登录返回空列表；过期则清凭证。 */
    async modelsList() {
      const token = await account.readToken();
      if (token === null) return { signedIn: false, models: [] };
      const listed = await hub.fetchModels(token);
      if (listed.signedIn === false && listed.expired === true) {
        await account.clearToken();
        return { signedIn: false, expired: true, models: [] };
      }
      return listed;
    },

    /** 重新拉取站点模型并**重写我们的路由配置**。
     * 用途：站点模型池变化、或我们升级了路由结构（例如本次补上 reasoningEfforts）后，
     * 不必重新登录就能让配置跟上——登录时走的是同一段写入逻辑。 */
    async modelsSync() {
      const token = await account.readToken();
      if (token === null) return { ok: false, reason: "not-signed-in", models: [] };
      let listed;
      try {
        listed = await hub.fetchModels(token);
      } catch (error) {
        // 站点故障时**保留上一次的模型列表**：把路由改成空列表会让用户连模型都选不了
        log(`模型同步失败（保留现有配置）：${error?.code ?? ""} ${String(error?.message ?? error)}`);
        return { ok: false, reason: error?.code ?? "hub/error", models: [] };
      }
      if (listed.signedIn === false && listed.expired === true) {
        await account.clearToken();
        return { ok: false, reason: "expired", models: [] };
      }
      const models = Array.isArray(listed.models) ? listed.models : [];
      // 内容一致就不写：这个同步在每次打开页面时都会跑，无条件写会让配置文件反复被改
      const changed = await model.ensureRouteIfChanged(undefined, models);
      if (changed) log(`已同步站点模型到路由：可用 ${models.length} 个（含思维档位声明）`);
      return { ok: true, models, tierName: listed.tierName ?? null, routeId: ROUTE_ID, changed };
    },

    /** 仅自测脚本使用：确认 token 能真的调通站点模型（文档自测第 1 项）。 */
    async chatProbe({ prompt } = {}) {
      const token = await account.readToken();
      if (token === null) return { ok: false, code: "not-signed-in" };
      const result = await hub.probeChat(token, prompt ?? "你好");
      return { ok: true, model: result.model, chars: result.content.length, sample: result.content.slice(0, 40) };
    },

    /** 诊断：对本行的默认模型做一次**幂等空写**（写入它当前已经是的值），
     * 用来确认 settings 的 volatile 校验与写入通路可用；不会改变任何配置。 */
    async settingsWriteProbe() {
      const current = await model.currentDefault();
      if (typeof current.provider !== "string" || typeof current.model !== "string") {
        return { ok: false, reason: "读不到当前默认模型，跳过探针" };
      }
      await model.writeDefaultProbe(current.provider, current.model);
      const after = await model.currentDefault();
      return {
        ok: after.provider === current.provider && after.model === current.model,
        wrote: current,
        after,
      };
    },

    /** 供自测/诊断读取，不含 token。 */
    async debugState() {
      const status = await account.status();
      const current = await model.currentDefault();
      return { channel: CHANNEL, routeId: ROUTE_ID, appVersion, account: status, defaultModel: current, pending: pendingView() };
    },
  };
}

/** 把技术错误码翻译成用户能看懂、且可操作的一句话。 */
export function describeHubFailure(code, httpStatus, raw) {
  if (code === "hub/site-error") {
    return `站点暂时不可用（HTTP ${httpStatus ?? "5xx"}），稍后会自动重试；这不影响你的登录状态。`;
  }
  if (code === "hub/timeout") return "站点响应超时，请检查网络后重试。";
  if (code === "hub/network") return "无法连接站点，请检查网络。";
  if (code === "hub/unauthorized" || code === "hub/usage-failed") return "站点拒绝了这次请求，可能需要重新登录。";
  return raw;
}

export function registerHubRpc(ctx, service, { log = () => {} } = {}) {
  const routes = {
    ping: () => service.ping(),
    "auth.status": () => service.authStatus(),
    "auth.start": () => service.authStart(),
    "auth.open": () => service.authOpen(),
    "auth.poll": () => service.authPoll(),
    "auth.signOut": () => service.authSignOut(),
    "quota.get": () => service.quotaGet(),
    // 设置页「用量看板」：复用 quotaGet 的取数（含登录态判定与站点故障分类），把结果整形成
    // 界面可直接渲染的模型（纯函数在 host/usage-view.mjs，可离线断言）。同时回传归一后的
    // quota —— 让页脚圆环与本页共用**同一次**请求，避免两个界面出现两个口径。
    "usage.board": async () => {
      const quota = await service.quotaGet();
      return { ...buildUsageBoard(quota), quota };
    },
    "tiers.get": () => service.tiersGet(),
    "models.list": () => service.modelsList(),
    "models.sync": () => service.modelsSync(),
    "update.check": () => service.updateCheck(),
    "debug.state": () => service.debugState(),
    "debug.settingsWriteProbe": () => service.settingsWriteProbe(),
  };
  ctx.inject(["connection"], (scope) => {
    scope.effect(
      () =>
        scope.connection.rpc.handle(CHANNEL, async (endpoint, payload) => {
          const handler = routes[endpoint];
          if (handler === undefined) {
            return { ok: true, value: { ok: false, code: "hub/unknown-endpoint", message: `未知端点：${endpoint}` } };
          }
          void payload;
          // **永远返回 ok:true**：错误放进 value 里。若不这样，任何抛错都会走 connection 的
          // 失败信封，客户端只能看到「invalid server-response failure」这种通用串，
          // 站点故障的具体原因（HTTP 500 / 超时 / 需要重新登录）就到不了界面。
          try {
            return { ok: true, value: { ok: true, data: await handler() } };
          } catch (error) {
            const code = typeof error?.code === "string" ? error.code : "hub/error";
            const httpStatus = typeof error?.httpStatus === "number" ? error.httpStatus : undefined;
            log(`RPC ${endpoint} 失败：${code} ${String(error?.message ?? error)}`);
            return {
              ok: true,
              value: {
                ok: false,
                code,
                httpStatus,
                message: describeHubFailure(code, httpStatus, String(error?.message ?? error)),
              },
            };
          }
        }),
      "ming-tea-ui: hub rpc channel",
    );
  });
}
