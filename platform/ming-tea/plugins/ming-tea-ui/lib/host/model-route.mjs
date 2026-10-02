// 模型路由：登录后把站点 AI 接成 DSH 的一个 provider，并把它设为默认模型。
//
// 关键点（都经读码确认）：
// - 走宿主 `settings` 服务的**路径寻址写入**（`mutate(ns, [{op:'set', path:[...]}])`），
//   它内部经 configEditor 直接改活的配置树 ⇒ **不需要重启**，也不会碰用户在
//   「设置 → 模型」里自己加的第三方厂商（这正是用户要求的：登录后仍可自配 DeepSeek 等）。
// - 站点 JWT 不写进配置，只以凭证引用 `MING_TEA_HUB_TOKEN` 出现（pi-ai 每次请求解析）。
// - 登出只在默认模型**仍指向我们**时回退，不跟用户的手动选择打架。

import { HUB_API_BASE_URL, TOKEN_REF } from "./hub-client.mjs";

/** 我们在 llm-pi-ai 里占用的路由 id。 */
export const ROUTE_ID = "ming-tea-hub";
/** 站点返回的模型名（`/chat/completions` 里 `model: "auto"`）。 */
export const ROUTE_MODEL_ID = "auto";
/** 未登录/登出后的默认模型（与 profile 原本的取值一致）。 */
export const FALLBACK_PROVIDER = "deepseek-official";
export const FALLBACK_MODEL = "deepseek-flash";
/** 站点未公开 Auto 的上下文长度，取一个保守值；文档已标注这是估值。 */
export const CONTEXT_WINDOW = 262144;

const LLM_NS = "llm-pi-ai";
const DEFAULT_MODEL_NS = "agent-default-model";

/**
 * 思维档位声明。**手写路由必须声明它**：`reasoningEfforts` 缺省时只会继承「已安装目录里同 id 的模型」
 * 的能力，而我们的 auto 不在任何目录里 ⇒ 会话里连强度控件都不会出现（用户报的「对话开启后改不了思考强度」）。
 * 语义（读 pi-ai 源码确认）：键是选择器提供的档位，值是**上线时发的拼写**；只有 `off` 允许留空（null = 不发这个参数）。
 * 站点是 OpenAI 兼容网关，实测接受 `reasoning_effort: low|high`（HTTP 200），故声明标准四档。
 */
export const REASONING_EFFORTS = { off: null, low: "low", medium: "medium", high: "high" };

/**
 * 输入模态。**手写路由必须显式声明**：pi-ai 的模型条目缺省是 `["text"]`
 * （`DEFAULT_INPUT`），未声明 `image` 的模型会让「看屏幕」在工具层直接被拒：
 * `read_image` 报 `cannot read "…png" as an image: model "…" does not declare image input`，
 * 截图根本到不了模型（2026-10-02 实测踩到：代理会用像素坐标点计算器，却看不见截图）。
 *
 * 名单只收**站点侧实测能正确描述图片**的模型；其余一律只声明文本 ——
 * 宁可让「看屏幕」明确失败，也不要声明了却拿到空回答（`auto` 就是这种：
 * 传图返回空，因此特意不声明 image，「看屏幕」会自动落到具体视觉模型上）。
 */
export const VISION_MODEL_IDS = new Set(["deepseek-v4.1-flash", "mimo-v2.6-flash", "glm-5.3"]);
const TEXT_ONLY_INPUT = ["text"];
const VISION_INPUT = ["text", "image"];

function inputModalitiesFor(id) {
  return VISION_MODEL_IDS.has(id) ? VISION_INPUT : TEXT_ONLY_INPUT;
}

export const AUTO_MODEL = {
  id: ROUTE_MODEL_ID,
  name: "铭荼 Auto",
  contextWindow: CONTEXT_WINDOW,
  reasoningEfforts: REASONING_EFFORTS,
  input: inputModalitiesFor(ROUTE_MODEL_ID),
};

/** 路由配置。`extraModels` 来自站点 `/models`（付费档会多出具体模型），
 * auto 恒在首位；不可用的条目直接丢弃——不把选不了的模型摆进模型列表。 */
export function routeConfig(baseURL = HUB_API_BASE_URL, extraModels = []) {
  const seen = new Set([AUTO_MODEL.id]);
  const models = [AUTO_MODEL];
  for (const model of extraModels) {
    if (typeof model?.id !== "string" || model.id === "" || seen.has(model.id)) continue;
    if (model.available === false) continue;
    seen.add(model.id);
    models.push({
      id: model.id,
      name: model.name || model.id,
      contextWindow: CONTEXT_WINDOW,
      reasoningEfforts: REASONING_EFFORTS,
      input: inputModalitiesFor(model.id),
    });
  }
  return {
    displayName: "铭荼（站点）",
    api: "openai-completions",
    baseURL,
    apiKeyEnv: TOKEN_REF,
    models,
  };
}

export function createModelRoute(ctx) {
  const settings = () => {
    const service = ctx.get?.("settings");
    if (service === undefined) throw new Error("settings 服务不可用，无法切换模型路由");
    return service;
  };

  /** 写入并处理版本冲突：冲突时用服务返回的真实 revision 重试一次。 */
  async function edit(ns, ops) {
    try {
      await settings().mutate(ns, ops);
    } catch (error) {
      if (error?.code === "SETTINGS_CONFLICT" && typeof error.actual === "number") {
        await settings().mutate(ns, ops, error.actual);
        return;
      }
      throw error;
    }
  }

  /** 读当前默认模型（describe() 是同步的，这里兼容同步/异步两种返回）。 */
  async function currentDefault() {
    try {
      const rows = await Promise.resolve(settings().describe?.() ?? []);
      const row = (Array.isArray(rows) ? rows : []).find((item) => item?.ns === DEFAULT_MODEL_NS);
      const value = row?.value;
      return {
        provider: typeof value?.provider === "string" ? value.provider : undefined,
        model: typeof value?.model === "string" ? value.model : undefined,
      };
    } catch {
      return {};
    }
  }

  return {
    currentDefault,

    /** 登录后：加/更新我们的路由（可带上站点给的可用模型清单）。 */
    async ensureRoute(baseURL, extraModels = []) {
      await edit(LLM_NS, [
        { op: "set", path: ["providers", ROUTE_ID], value: routeConfig(baseURL, extraModels) },
      ]);
    },

    /** 与当前配置比对，只有不同才写。返回是否发生了写入。 */
    async ensureRouteIfChanged(baseURL, extraModels = []) {
      const desired = routeConfig(baseURL, extraModels);
      let current;
      try {
        const rows = await Promise.resolve(settings().describe?.() ?? []);
        const row = (Array.isArray(rows) ? rows : []).find((item) => item?.ns === LLM_NS);
        current = row?.value?.providers?.[ROUTE_ID];
      } catch {
        current = undefined;
      }
      if (current !== undefined && JSON.stringify(current) === JSON.stringify(desired)) return false;
      await this.ensureRoute(baseURL, extraModels);
      return true;
    },

    /** 把默认模型切到站点 Auto。 */
    async useAsDefault() {
      await edit(DEFAULT_MODEL_NS, [
        { op: "set", path: ["provider"], value: ROUTE_ID },
        { op: "set", path: ["model"], value: ROUTE_MODEL_ID },
      ]);
    },

    /** 登出：只在默认仍指向我们时回退，避免覆盖用户自己的选择。 */
    async restoreDefaultIfOurs() {
      const current = await currentDefault();
      if (current.provider !== ROUTE_ID) return { reverted: false, current };
      await edit(DEFAULT_MODEL_NS, [
        { op: "set", path: ["provider"], value: FALLBACK_PROVIDER },
        { op: "set", path: ["model"], value: FALLBACK_MODEL },
      ]);
      return { reverted: true, current: { provider: FALLBACK_PROVIDER, model: FALLBACK_MODEL } };
    },

    /** 诊断用：把默认模型写回它当前的值（幂等），验证写入通路。 */
    async writeDefaultProbe(provider, model) {
      await edit(DEFAULT_MODEL_NS, [
        { op: "set", path: ["provider"], value: provider },
        { op: "set", path: ["model"], value: model },
      ]);
    },

    /** 移除我们的路由（登出时调用；留着其实无害，但不留残留更干净）。 */
    async removeRoute() {
      try {
        await edit(LLM_NS, [{ op: "unset", path: ["providers", ROUTE_ID] }]);
      } catch {
        /* 本来就不存在：忽略 */
      }
    },
  };
}
