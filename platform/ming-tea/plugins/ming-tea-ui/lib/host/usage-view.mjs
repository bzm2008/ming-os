// 设置页「用量看板」的数据整形（宿主半区，纯函数）。
//
// 为什么整形放在宿主、而不是客户端直接渲染 /usage 的原始字段：
//   1. 本仓库对宿主模块有**可离线运行的断言**（`scripts/check_ming_tea_hub.mjs` 直接 import），
//      客户端半区是浏览器 bundle、本仓库没有能跑它的测试环境。判断放这里才「验证得了」。
//   2. 客户端只负责画：拿到 `meters` / `facts` / `tiers` 直接渲染，不重复做单位与百分比判断。
//
// 数据来源是站点 `/usage`（已由 `hub-client.normalizeUsage` 归一）。本模块**不猜**站点没给的数字：
// 缺字段就降级成 `null` / 空数组，由界面显示「—」。

const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);

/** 剩余占比。与页脚圆环同一口径：显示「还剩多少」，不是「用了多少」。
 *  站点没给总数或总数非正时返回 null（界面显示「—」，不假装 0%）。 */
function remainingPercent(remaining, limit) {
  if (remaining === undefined || limit === undefined || limit <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((remaining / limit) * 100)));
}

/** 一个「计量条」：两层额度（Auto 赠送 / 付费余额 / 免费层）共用同一形状。 */
function meterOf({ key, label, unit, limit, used, remaining, note = "" }) {
  const total = limit ?? num(used + remaining);
  return {
    key,
    label,
    unit,
    limit: limit ?? null,
    used: used ?? null,
    remaining: remaining ?? null,
    percent: remainingPercent(remaining, limit),
    note,
  };
}

/**
 * 档位到期：`expiresAt` 是毫秒时间戳（hub-client 已把 ISO 字符串解析过）。
 * 返回 `{ text, days }`；解析不出来返回 `{ text: null, days: null }` —— 界面显示「—」。
 * `now` 可注入，便于离线断言。
 */
export function describeExpiry(expiresAt, now = Date.now()) {
  const at = num(expiresAt);
  if (at === undefined) return { text: null, days: null };
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return { text: null, days: null };
  const pad = (value) => String(value).padStart(2, "0");
  const text = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const days = Math.max(0, Math.ceil((at - now) / 86_400_000));
  return { text, days };
}

/** 站点 `available_tiers` / `catalog` 里的档位条目。只保留站点确实给了的字段，不编造价格。 */
function tierOf(entry) {
  if (entry === null || typeof entry !== "object") return null;
  const key = typeof entry.key === "string" && entry.key !== "" ? entry.key : undefined;
  const name = typeof entry.name === "string" && entry.name !== "" ? entry.name : undefined;
  if (key === undefined && name === undefined) return null;
  return {
    key: key ?? null,
    name: name ?? key ?? null,
    autoFreeCalls: num(entry.auto_free_calls) ?? null,
    monthlyBudgetRmb: num(entry.monthly_budget_rmb) ?? null,
    concurrency: num(entry.concurrency) ?? null,
  };
}

/**
 * 组装用量看板视图模型。
 *
 * 输入：`quota.get` 的返回（`{signedIn, expired, tierName, detail, percent, free, paid, autoFree, plan, availableTiers}`）。
 * 输出：`{signedIn, expired, heading, detail, meters, facts, tiers, notes, generatedAt}` —— 全部可直接渲染。
 */
export function buildUsageBoard(quota, { now = Date.now() } = {}) {
  const source = quota !== null && typeof quota === "object" ? quota : {};

  if (source.signedIn !== true) {
    const expired = source.expired === true;
    return {
      signedIn: false,
      expired,
      heading: expired ? "登录已过期" : "尚未登录",
      detail: expired
        ? "本地凭证已失效：重新登录后这里会显示 Auto 赠送次数与付费层余额。"
        : "登录铭荼账号后，这里会显示 Auto 赠送次数、付费层余额与当前档位。",
      meters: [],
      facts: [],
      tiers: [],
      notes: ["未登录时不向站点发起用量请求。"],
      generatedAt: now,
    };
  }

  const plan = source.plan !== null && typeof source.plan === "object" ? source.plan : null;
  const tierName =
    (plan !== null && typeof plan.name === "string" && plan.name !== "" ? plan.name : undefined) ??
    (typeof source.tierName === "string" && source.tierName !== "" ? source.tierName : undefined) ??
    null;

  const meters = [];

  // ① Auto 赠送次数：站点按档位赠送（Plus 200 / Pro 300 / Ultra 400），超出后**不硬封**，
  //    改为从「实际可用额度」扣 —— 这句是站点侧承诺，写进 note 让用户知道不会突然不能用。
  const auto = source.autoFree !== null && typeof source.autoFree === "object" ? source.autoFree : null;
  if (auto !== null && auto.configured !== false) {
    const limit = num(auto.limit);
    const used = num(auto.used);
    const remaining = num(auto.remaining);
    if (limit !== undefined || remaining !== undefined || used !== undefined) {
      const exhausted = auto.exhausted === true || (remaining !== undefined && remaining <= 0);
      meters.push(
        meterOf({
          key: "auto-free",
          label: "Auto 赠送次数",
          unit: "次",
          limit,
          used,
          remaining,
          note: exhausted
            ? "本月赠送次数已用完：Auto 调用改为从付费层余额扣费，不会硬性停用。"
            : "用完后不硬封：Auto 调用改为从付费层余额扣费。",
        }),
      );
    }
  }

  // ② 付费层余额（人民币）。付费账号以它为准，页脚圆环显示的也是这一层。
  const paid = source.paid !== null && typeof source.paid === "object" ? source.paid : null;
  if (paid !== null) {
    meters.push(
      meterOf({
        key: "paid",
        label: "付费层余额",
        unit: "¥",
        limit: num(paid.budget),
        used: num(paid.used),
        remaining: num(paid.remaining),
        note: "超出赠送次数后的 Auto 调用与付费模型都从这里扣。",
      }),
    );
  }

  // ③ 免费层次数（免费档才有；付费档的响应里通常没有这一块）。
  const free = source.free !== null && typeof source.free === "object" ? source.free : null;
  if (free !== null) {
    meters.push(
      meterOf({
        key: "free",
        label: "本月免费次数",
        unit: "次",
        limit: num(free.limit),
        used: num(free.used),
        remaining: num(free.remaining),
        note: "免费层的调用不消耗付费层余额。",
      }),
    );
  }

  const expiry = describeExpiry(plan === null ? undefined : plan.expiresAt, now);
  const facts = [];
  facts.push({ key: "tier", label: "当前档位", value: tierName ?? "—" });
  if (expiry.text !== null) {
    facts.push({
      key: "expiry",
      label: "到期时间",
      value: expiry.days === null ? expiry.text : `${expiry.text}（${expiry.days} 天）`,
    });
  }
  if (plan !== null && plan.concurrency !== undefined) {
    facts.push({ key: "concurrency", label: "并发上限", value: String(plan.concurrency) });
  }
  facts.push({
    key: "channel",
    label: "数据来源",
    value: "站点 /usage（铭荼账号）",
  });

  const tiers = (Array.isArray(source.availableTiers) ? source.availableTiers : [])
    .map(tierOf)
    .filter((entry) => entry !== null);

  const notes = ["本页面只读：刷新不会改变账户或额度。"];
  if (meters.length === 0) {
    notes.push("站点本次没有返回任何额度字段，因此没有可显示的计量条（不显示 0 以免误导）。");
  }

  return {
    signedIn: true,
    expired: false,
    heading: tierName === null ? "我的用量" : `${tierName} · 我的用量`,
    detail: typeof source.detail === "string" ? source.detail : "",
    meters,
    facts,
    tiers,
    notes,
    generatedAt: now,
  };
}
