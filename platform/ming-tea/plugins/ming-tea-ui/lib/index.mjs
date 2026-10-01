// 铭荼界面层的宿主半区。它在 DSH 进程内运行，做浏览器侧做不到的两件事：
//
// 1. **首屏防闪**：官方 boot 把画布底色写死为 `#fff`/`#151517`，我们的纸面是
//    `#f6faf8`，加载瞬间会闪一下纯白。往 `webserver/index-inject` 推一行样式
//    （不 prepend，排在官方之后，同特异度后者胜）即可。
//    边界：宿主侧读不到用户持久化的外观偏好（需要 settings 服务），只能按系统
//    配色分流；偏好与系统配色不一致时首屏可能有一瞬不符，应用挂载后修正。
//
// 2. **低配机器的默认策略**：定时任务与连接手机合计带约 90 个包（含 antd 与
//    5 家 IM SDK），对老电脑是明显的启动与内存负担。内存或核数低于阈值时，
//    把它们默认设为不挂载 —— 写进 **profile 的用户层**（不是 home 层），
//    因此用户可以在「设置 → 插件配置」里手动开启，或直接删掉这一段。
//
// 关于职责边界：这属于"部署策略"而非界面定制，放在本包是因为它目前是铭荼
// 唯一的自有 DSH 插件。若将来自有插件变多，应把这段搬进独立的部署插件。

import { cpus, homedir, totalmem } from "node:os";
import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
// 站点接入（登录 / 额度 / 更新检查）：宿主侧实现，见 lib/host/
import { createHubService, registerHubRpc } from "./host/rpc.mjs";
import { getSummonTrust, shouldAutoAllow } from "./host/summon-trust.mjs";
import { join } from "node:path";

const BOOT_LIGHT = "#f6faf8";
const BOOT_DARK = "#171d1c";

/** 在任何脚本执行前给文档画布上色的样式。 */
function bootStyle() {
  const light = `:root{color-scheme:light}body{background-color:${BOOT_LIGHT};--dsh-boot-bg:${BOOT_LIGHT}}`;
  const dark = `:root{color-scheme:dark}body{background-color:${BOOT_DARK};--dsh-boot-bg:${BOOT_DARK}}`;
  return `${light}@media(prefers-color-scheme:dark){${dark}}`;
}

// ── 低配默认策略 ──────────────────────────────────────────────────

/** 内存 ≤4GB 或核数 ≤2 视为低配（老电脑的常见下限）。 */
const LOW_SPEC_LIMITS = { maxTotalMemGb: 4, maxCores: 2 };

/** 低配时默认不挂载的重插件行 id（已确认用户层没有引用指向它们）。 */
const HEAVY_ROWS = ["dsh-automation", "im-connect"];

const PATCH_MARKER = "# ming-tea:low-spec";

function dshHome() {
  return process.env.DSH_HOME ?? join(homedir(), ".dsh");
}

function isLowSpec() {
  // 允许显式覆盖：MING_TEA_LOW_SPEC=1/0 用于部署指定与验证
  const forced = process.env.MING_TEA_LOW_SPEC;
  if (forced === "1") return true;
  if (forced === "0") return false;
  const memGb = totalmem() / 1024 ** 3;
  return memGb <= LOW_SPEC_LIMITS.maxTotalMemGb || cpus().length <= LOW_SPEC_LIMITS.maxCores;
}

/**
 * 只做一次决定：用独立的标记文件记录"已评估过"，因此
 * ① 不会每次启动都改用户的 patch；
 * ② 用户删掉我们写的 block 后不会被重新加回来（要重新评估就删掉标记文件）。
 */
function applyLowSpecDefaults(ctx) {
  if (!isLowSpec()) return;
  const home = dshHome();
  const stamp = join(home, ".ming-tea-low-spec-applied");
  if (existsSync(stamp)) return;

  const profile = ctx?.get?.("profileContext")?.name ?? process.env.MING_TEA_DSH_PROFILE ?? "ming-tea";
  const patchPath = join(home, "profiles", profile, "cordis.patch.yml");
  if (!existsSync(patchPath)) return;

  const current = readFileSync(patchPath, "utf8");
  if (current.includes(PATCH_MARKER)) {
    writeFileSync(stamp, `${new Date().toISOString()} (block already present)\n`);
    return;
  }

  const block = [
    "",
    `${PATCH_MARKER}（本机内存或核数低于阈值，默认不挂载重插件）`,
    "# 在「设置 → 插件配置」里可手动开启；删除本段与 $DSH_HOME/.ming-tea-low-spec-applied 可恢复默认评估",
    ...HEAVY_ROWS.map((id) => `- id: ${id}\n  disabled: true`),
    "",
  ].join("\n");

  writeFileSync(patchPath, `${current.replace(/\s*$/, "")}\n${block}`, "utf8");
  writeFileSync(stamp, `${new Date().toISOString()} (disabled: ${HEAVY_ROWS.join(", ")})\n`);
  console.log(`[ming-tea] 低配机器：已默认停用 ${HEAVY_ROWS.join("、")}`);
}

// ── 辅助学习场景：真正的只读 ──────────────────────────────────────
//
// 为什么需要这一段：官方 `dsh-tool-fs` 一次注册 read/write/edit 全套且**没有关闭
// 写入的配置项**，所以"学习场景不挂那几行"的做法只能去掉读，去不掉写。
//
// 官方支持的按 agent 裁剪机制是 `tools.restrict`：契约说"作用域内的名字会失败"，
// 指的是**调用者自己层**的名字；从 agent 层调用时，write/edit 是**继承自 preset 层**
// 的名字，是可被 restrict 的（官方样板：subagent 的 toolFilter 就是这么做的）。
// 因此这里监听 `agent/created`——preset 绑定在此之前完成，此时能看到继承来的工具。
//
// 校验策略：名字写错会让 restrict 抛错，而该事件是串行 await 的，会**导致 agent
// 创建失败**。所以先动态过滤"确实可见"的名字，只对存在的名字下 deny；
// 若一个都没命中也不报错（说明该 DSH 版本已没有这些工具，等于目标已达成）。

/** 需要在学习场景移除的写入类工具（含未挂载但可能被引入的编辑器）。 */
const LEARNING_DENY_TOOLS = ["write", "edit", "str_replace_editor"];

const LEARNING_PRESET_IDS = new Set(["minimal"]);

/** 诊断开关：设置 MING_TEA_POLICY_TRACE=1 时把决策写进 $DSH_HOME 下的日志。 */
function policyTrace(line) {
  if (process.env.MING_TEA_POLICY_TRACE !== "1") return;
  try {
    appendFileSync(join(dshHome(), ".ming-tea-policy-trace.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* 诊断不可用不影响功能 */
  }
}

/** 尽力识别该 agent 绑定的场景（preset）id；识别不到就返回 undefined。 */
function presetIdOf(ctx, agent) {
  try {
    const projections = ctx.get?.("sessionProjections");
    const state = projections?.stateOf?.(agent.session, "agentPreset");
    if (typeof state === "string") return state;
    if (state && typeof state.id === "string") return state.id;
  } catch {
    /* 落到下一种方式 */
  }
  try {
    const presets = ctx.get?.("agentPresets");
    const composed = presets?.composedPreset?.(agent.ctx);
    if (typeof composed === "string") return composed;
    if (composed && typeof composed.id === "string") return composed.id;
  } catch {
    /* 识别不到就按未命中处理 */
  }
  return undefined;
}

function applyLearningReadOnly(ctx) {
  /** agent → 解除函数；这张表同时充当"已处理"标记 */
  const restrictions = new Map();
  const knownAgents = new Set();

  const evaluate = (agent) => {
    try {
      if (!agent || typeof agent !== "object") return;
      knownAgents.add(agent);

      const presetId = presetIdOf(ctx, agent);
      const isLearning = presetId !== undefined && LEARNING_PRESET_IDS.has(presetId);
      const existing = restrictions.get(agent);

      // 非学习场景：解除可能存在的限制（例如用户从学习切回办公）
      if (!isLearning) {
        if (existing) {
          existing();
          restrictions.delete(agent);
          policyTrace(`preset=${presetId ?? "unknown"} 已解除只读限制`);
        } else {
          policyTrace(`skip preset=${presetId ?? "unknown"}`);
        }
        return;
      }

      if (existing) {
        policyTrace(`preset=${presetId} 只读限制已在位`);
        return;
      }

      const tools = agent.ctx?.tools;
      if (!tools || typeof tools.restrict !== "function") {
        policyTrace(`preset=${presetId} 无 tools 服务，跳过`);
        return;
      }

      const deny = LEARNING_DENY_TOOLS.filter((name) => {
        try {
          return tools.get(name, agent) !== undefined;
        } catch {
          return false;
        }
      });
      if (deny.length === 0) {
        policyTrace(`preset=${presetId} 无写入类工具可见（目标已达成）`);
        return;
      }

      const dispose = tools.restrict({ deny });
      restrictions.set(agent, typeof dispose === "function" ? dispose : () => {});
      if (typeof agent.ctx.effect === "function" && typeof dispose === "function") {
        agent.ctx.effect(() => dispose);
      }
      policyTrace(`preset=${presetId} 已移除写入工具: ${deny.join(",")}`);
    } catch (error) {
      // 绝不让只读策略把会话创建搞挂：记录并放行
      policyTrace(`error ${String((error && error.message) || error)}`);
    }
  };

  ctx.on("agent/created", (payload) => evaluate(payload?.agent ?? payload));

  // 切换预设不会重建 agent（实测：切到学习场景后 agent/created 不再触发），
  // 只是在既有 agent 上重绑 preset 代际。因此必须复查全部已知 agent。
  ctx.on("agent-preset/selected", () => {
    for (const agent of knownAgents) evaluate(agent);
  });
}


// ── 电脑/浏览器操作的审批闸门 ──────────────────────────────────────
//
// 官方层对桌面控制与浏览器自动化**没有审批钩子**（无窗口白名单、无动作预算、
// 无截图节流，唯一约束是系统提示词），而用户要求三个场景都能用这些能力。
// 因此这里补一道闸门：`tools/pre-execute` 是作用域过滤的 waterfall，注册在普通
// ctx 即对所有调用生效；对动作类调用先向 `approval` 服务请求一次授权，
// 只有 `allowed-once` 才放行，其余（拒绝/取消/无应答）一律 fail-closed 拒绝。
//
// 纯元数据类调用（列应用、列窗口、屏幕尺寸、光标位置）不询问，否则自动化会被
// 提示音淹没；截屏与状态查询**不在**放行名单里，因为它们会暴露屏幕内容。

/** 需要审批的自动化工具族。 */
const AUTOMATION_FAMILIES = [/^cua_driver_native__/i, /^mcp__playwright-mcp__/i];

/** 无需审批的只读元数据调用（不涉及屏幕内容、不改变任何状态）。 */
const METADATA_ALLOWLIST = [
  /^cua_driver_native__(list_apps|list_windows|get_screen_size|get_cursor_position|list_tools)/i,
];

/** 人类可读的动作分类，用于审批提示。 */
function approvalReason(toolName) {
  if (/type|hotkey|paste|clipboard/i.test(toolName)) return "将模拟键盘输入或读写剪贴板（可能涉及密码等敏感内容）";
  if (/upload|drop|submit|dialog/i.test(toolName)) return "将上传文件或确认对话框（可能对外提交内容）";
  if (/click|drag|scroll|invoke|navigate|press/i.test(toolName)) return "将操作鼠标或界面元素（会改变屏幕上的状态）";
  return "将对电脑界面执行操作";
}

function needsApproval(toolName) {
  if (!AUTOMATION_FAMILIES.some((re) => re.test(toolName))) return false;
  return !METADATA_ALLOWLIST.some((re) => re.test(toolName));
}

function installApprovalGate(ctx) {
  const approval = ctx.get?.("approval");
  const trust = getSummonTrust();
  ctx.on("tools/pre-execute", async (exec, next) => {
    const toolName = String(exec?.name ?? "");
    if (!needsApproval(toolName)) return next();

    // 会话级「信任一次」：用户在面板点过之后，普通电脑操作不再反复问；
    // 输入/剪贴板、上传/提交、删除/安装/系统命令仍然每次都问（见 summon-trust.mjs）。
    const sessionId = exec?.agent?.session?.id;
    if (typeof sessionId === "string" && sessionId !== "") trust.noteAskedSession(sessionId);
    if (trust.isTrusted(sessionId) && shouldAutoAllow(toolName)) {
      policyTrace(`allow ${toolName}（会话已信任，不再询问）`);
      return next();
    }

    if (!approval || typeof approval.request !== "function") {
      policyTrace(`deny ${toolName}（无审批服务，fail-closed）`);
      return { kind: "deny", reason: "该操作需要人工确认，但当前没有可用的审批通道，已拒绝。" };
    }

    let outcome;
    try {
      outcome = await approval.request({
        agent: exec.agent,
        toolName,
        callId: exec.callId,
        reason: approvalReason(toolName),
        signal: exec.signal,
      });
    } catch (error) {
      policyTrace(`deny ${toolName}（审批异常：${String((error && error.message) || error)}）`);
      return { kind: "deny", reason: "该操作需要人工确认，但审批请求失败，已拒绝。" };
    }

    if (outcome === "allowed-once") {
      policyTrace(`allow ${toolName}（用户已批准）`);
      return next();
    }
    policyTrace(`deny ${toolName}（审批结果：${outcome}）`);
    return { kind: "deny", reason: "用户未批准该电脑操作。" };
  });
}

export const name = "ming-tea-ui";

/** 铭荼的站点接入（登录 / 额度 / 更新检查）全在宿主侧：
 * 浏览器直连站点受 CORS 与 Tauri CSP 限制，宿主没有这两个问题，JWT 也就不进页面。 */
function installHub(ctx) {
  const service = createHubService(ctx, { log: (line) => policyTrace(`[hub] ${line}`) });
  registerHubRpc(ctx, service, { log: (line) => policyTrace(`[hub] ${line}`) });
}

export function apply(ctx) {
  ctx.on("webserver/index-inject", (table) => {
    table.push({ kind: "style", text: bootStyle() });
  });

  // 低配策略失败不能影响启动：包一层并保持静默
  try {
    applyLowSpecDefaults(ctx);
  } catch {
    /* 只读文件系统、权限不足等情况下跳过 */
  }

  try {
    applyLearningReadOnly(ctx);
  } catch {
    /* 同上：策略不可用不应影响应用启动 */
  }

  try {
    installApprovalGate(ctx);
  } catch {
    /* 审批闸门安装失败不阻塞启动；但会记录，便于诊断 */
  }

  try {
    installHub(ctx);
  } catch (error) {
    // 站点接入不可用（缺 connection/settings 服务等）不应影响启动，但要说清楚
    policyTrace(`[hub] 安装失败：${String(error?.message ?? error)}`);
  }
}
