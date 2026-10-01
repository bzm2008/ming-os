// 会话级「信任一次」：用户在面板里点过之后，同一会话内的**普通**电脑操作不再反复问，
// 但敏感动作照样问。
//
// 为什么要有它（2026-10-01 用户决定）：一个「看一眼屏幕再点一下」的任务会产生多次工具调用，
// 每次都弹审批会把体验毁掉（实测：看一次屏幕就连续弹了 4 次）。
// 边界（用户明确要求）：
//   - **只在本会话内有效**：内存态、不落盘、换会话或重启失效；
//   - **危险动作始终确认**：输入/粘贴/剪贴板、上传/提交、删除/安装/系统级命令一律继续问。
//
// 放在宿主半区而不是客户端：审批决策在宿主侧（`installApprovalGate` 的 `tools/pre-execute`），
// 而且这里有可离线运行的断言（scripts/check_ming_tea_hub.mjs 直接 import 本模块）。

/** 即使在已信任会话里也必须继续询问的动作族。 */
const ALWAYS_ASK = [
  // 输入类：可能把密码/验证码打进别人的窗口
  /type|hotkey|key|paste|clipboard|insert|write/i,
  // 对外提交类：上传、拖放、确认对话框
  /upload|drop|submit|dialog|confirm|send/i,
  // 破坏性/系统级：删除、卸载/安装、提权、shell/终端
  /delete|remove|uninstall|install|package|system|sudo|exec|shell|command|terminal|bash|pwsh|zsh|reboot|shutdown|format|kill|run/i,
];

/**
 * 已信任会话里，这个工具是否可以不再询问。
 * 拿不准（名字空、命中 ALWAYS_ASK）= 继续问，fail-closed。
 */
export function shouldAutoAllow(toolName) {
  const name = String(toolName ?? "").trim();
  if (name === "") return false;
  return !ALWAYS_ASK.some((pattern) => pattern.test(name));
}

/** 会话级信任的存放处：一份进程内状态，由 index.mjs 与 rpc.mjs 共用。 */
export function createSummonTrust() {
  const trusted = new Set();
  let lastAsked = null;
  return {
    /** 记住最近一次来问的会话 —— 面板点「本会话信任」时不必自己知道 sessionId。 */
    noteAskedSession(sessionId) {
      if (typeof sessionId === "string" && sessionId !== "") lastAsked = sessionId;
    },
    /** 信任某个会话；不传就用最近来问的那个。 */
    trust(sessionId) {
      const target = typeof sessionId === "string" && sessionId !== "" ? sessionId : lastAsked;
      if (target === null) return null;
      trusted.add(target);
      return target;
    },
    untrust(sessionId) {
      const target = typeof sessionId === "string" && sessionId !== "" ? sessionId : lastAsked;
      if (target === null) return null;
      trusted.delete(target);
      return target;
    },
    isTrusted(sessionId) {
      return typeof sessionId === "string" && trusted.has(sessionId);
    },
    status() {
      return { trusted: [...trusted], lastAsked };
    },
  };
}

let singleton = null;

/** 进程内单例（宿主半区只有一份）。 */
export function getSummonTrust() {
  if (singleton === null) singleton = createSummonTrust();
  return singleton;
}
