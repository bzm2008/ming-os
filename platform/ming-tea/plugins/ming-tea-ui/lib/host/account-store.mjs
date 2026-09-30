// 账号存储：JWT 走凭证引用，用户信息走 grant 记录。
//
// 两点刻意的选择：
// - **JWT 不放浏览器、不落日志**，只写进 `$DSH_HOME/.credentials.yaml`（0600）。
//   放进凭证引用的另一个好处：pi-ai 的 `apiKeyEnv` 每次请求都重新解析引用，
//   所以换 token 不需要重启 DSH。
// - 用户信息（昵称、id、到期时间）不是秘密，放在 `grant` 记录里，读起来比解析 JWT 稳。

import { TOKEN_LIFETIME_MS, TOKEN_REF } from "./hub-client.mjs";

/** 记录键的语法是 `<scope>/<id>`，两段都要求小写连字符标识符。 */
export const ACCOUNT_KEY = "ming-tea-ui/account";

/** 到期判断抽成纯函数，便于在自测脚本里断言（不依赖宿主服务）。 */
export function isExpired(expiresAt, now = Date.now()) {
  return typeof expiresAt !== "number" || !Number.isFinite(expiresAt) || expiresAt <= now;
}

export function createAccountStore(ctx, { now = () => Date.now() } = {}) {
  const service = () => {
    const credentials = ctx.get?.("credentials");
    if (credentials === undefined) throw new Error("credentials 服务不可用，无法读写登录凭证");
    return credentials;
  };

  return {
    /** 当前 JWT（未登录返回 null）。 */
    async readToken() {
      try {
        const resolved = await service().resolve(TOKEN_REF);
        return typeof resolved?.value === "string" && resolved.value !== "" ? resolved.value : null;
      } catch {
        return null;
      }
    },

    /** 写入 JWT。环境变量遮蔽会抛错（凭证服务的 assertUnshadowed），原样上抛让界面如实提示。 */
    async saveToken(token) {
      if (typeof token !== "string" || token === "") throw new Error("token 为空，拒绝写入");
      await service().set(TOKEN_REF, token);
    },

    async clearToken() {
      try {
        await service().unset(TOKEN_REF);
      } catch {
        /* 本来就没有：忽略 */
      }
    },

    /** `{ user, expiresAt, savedAt }`，没有记录返回 null。 */
    async readAccount() {
      try {
        const record = await service().readRecord(ACCOUNT_KEY);
        const payload = record?.kind === "grant" ? record.payload : undefined;
        if (payload === null || typeof payload !== "object") return null;
        return {
          user: payload.user ?? null,
          expiresAt: typeof payload.expiresAt === "number" ? payload.expiresAt : undefined,
          savedAt: typeof payload.savedAt === "number" ? payload.savedAt : undefined,
        };
      } catch {
        return null;
      }
    },

    async saveAccount({ user, expiresAt }) {
      const payload = {
        version: 1,
        user: user ?? null,
        expiresAt: typeof expiresAt === "number" ? expiresAt : now() + TOKEN_LIFETIME_MS,
        savedAt: now(),
      };
      await service().modifyRecord(ACCOUNT_KEY, async () => ({ kind: "grant", payload }));
      return payload;
    },

    async clearAccount() {
      try {
        await service().deleteRecord(ACCOUNT_KEY);
      } catch {
        /* 同上 */
      }
    },

    /** 界面用的综合状态；`expired` 为真时上层应提示重新登录。 */
    async status() {
      const [token, account] = await Promise.all([this.readToken(), this.readAccount()]);
      if (token === null) {
        return { signedIn: false, expired: false, user: null, expiresAt: account?.expiresAt };
      }
      const expired = isExpired(account?.expiresAt, now());
      return {
        signedIn: !expired,
        expired,
        user: account?.user ?? null,
        expiresAt: account?.expiresAt,
      };
    },
  };
}
