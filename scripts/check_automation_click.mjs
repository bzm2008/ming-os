#!/usr/bin/env node
// 自动化能力自检：按 DSH 的 browser-use-playwright-mcp 插件**完全相同**的方式启动
// Playwright MCP server（node <@playwright/mcp>/cli.js --browser chromium --isolated --headless），
// 然后用 MCP 协议真实地：列工具 → 打开指定页面 → 快照 → 点击一个元素 → 读回页面状态 → 截图。
//
// 为什么需要它：DSH 侧的自动化工具要模型凭据才能跑通（没有 API Key 就没有会话），
// 但「这台机器上自动化到底能不能点」是可以独立验证的——本脚本证明的就是这一段。
// 工具名前缀说明：MCP server 暴露的是裸名（browser_click），DSH 侧统一加
// `mcp__<serverName>__` 前缀（serverName = playwright-mcp），我们的审批闸门匹配的正是带前缀的名字。
//
// 用法：
//   node scripts/check_automation_click.mjs <url> [截图输出路径] [元素文本]
// 例：
//   node scripts/check_automation_click.mjs "http://127.0.0.1:18243/?token=<token>" /tmp/shot.png
//
// 退出码非 0 表示没走通（缺 Chromium、server 起不来、点击失败等），并在输出里写明原因。

// 自动化「实际点击流程」验证：按 DSH 的 browser-use-playwright-mcp 插件**完全相同**的方式
// 启动 Playwright MCP server（node <@playwright/mcp>/cli.js --browser chromium --isolated --headless），
// 然后用 MCP 协议真实地：列工具 → 打开我们的应用页面 → 快照 → 点击一个场景卡 → 截图 → 读回输入框内容。
// 目的：在没有模型凭据（无法跑真实会话）的前提下，验证自动化后端在这台机器上确实能点。
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const RUNTIME = process.env.MING_TEA_DSH_RUNTIME_DIR || `${process.cwd()}/.ming-tea/runtime`;
const CLI = `${RUNTIME}/node_modules/@playwright/mcp/cli.js`;
const APP = process.argv[2];
const OUT = process.argv[3] || "/tmp/mt-automation-shot.png";
const TARGET_TEXT = process.argv[4] || "整理文件与表格";
if (!APP) {
  console.error("用法：node scripts/check_automation_click.mjs <url> [截图输出路径] [要点击的元素文本]");
  process.exit(2);
}

const args = [CLI, "--browser", "chromium", "--isolated", "--headless"];
// 与插件一致：把 PLAYWRIGHT_MCP_* 环境变量清空（避免外部注入改变行为）
const env = Object.fromEntries(
  Object.keys(process.env)
    .filter((k) => k.toUpperCase().startsWith("PLAYWRIGHT_MCP_"))
    .map((k) => [k, ""]),
);

const child = spawn(process.execPath, args, { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, ...env } });
let buf = "";
const pending = new Map();
let nextId = 1;
const log = (...a) => console.log(...a);

child.stdout.on("data", (d) => {
  buf += d.toString();
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      continue;
    }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const { resolve, reject, method } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(`${method}: ${JSON.stringify(msg.error)}`));
      else resolve(msg.result);
    }
  }
});
child.stderr.on("data", (d) => {
  const text = d.toString().trim();
  if (text) log(`[mcp stderr] ${text.slice(0, 300)}`);
});
child.on("exit", (code) => log(`[mcp] 进程退出，code=${code}`));

function rpc(method, params = {}, timeoutMs = 90000) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject, method });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`${method} 超时（${timeoutMs}ms）`));
      }
    }, timeoutMs);
  });
}

const notify = (method, params = {}) =>
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}\n`);

function textOf(result) {
  return (result?.content ?? [])
    .filter((c) => c.type === "text")
    .map((c) => c.text)
    .join("\n");
}

try {
  log("== 1. initialize ==");
  let init;
  for (const version of ["2025-06-18", "2024-11-05"]) {
    try {
      init = await rpc("initialize", {
        protocolVersion: version,
        capabilities: {},
        clientInfo: { name: "ming-tea-automation-check", version: "0" },
      });
      log(`协议 ${version} 握手成功：`, JSON.stringify(init?.serverInfo ?? {}));
      break;
    } catch (error) {
      log(`协议 ${version} 失败：${error.message}`);
    }
  }
  if (!init) throw new Error("initialize 未成功");
  notify("notifications/initialized");

  log("\n== 2. tools/list ==");
  const { tools } = await rpc("tools/list");
  const names = tools.map((t) => t.name);
  log(`共 ${names.length} 个工具：${names.join(", ")}`);
  log(`工具名前缀是否为 mcp__ 之外的裸名（由 DSH 侧加前缀）：${names.every((n) => !n.startsWith("mcp__"))}`);
  for (const want of ["browser_click", "browser_evaluate"]) {
    const spec = tools.find((t) => t.name === want);
    log(`${want} 入参：${JSON.stringify(Object.keys(spec?.inputSchema?.properties ?? {}))}；必填 ${JSON.stringify(spec?.inputSchema?.required ?? [])}`);
  }

  log("\n== 3. 打开应用页面 ==");
  const nav = await rpc("tools/call", { name: "browser_navigate", arguments: { url: APP } }, 120000);
  log(textOf(nav).slice(0, 300));

  log("\n== 4. 快照并定位场景卡 ==");
  // 应用 boot 需要时间（先显示 Loading plugins…），且首启会弹「添加一个 API Key」引导框，
  // 所以这里轮询：先关弹窗，再等目标卡片出现。
  let ref;
  let snapshotText = "";
  for (let attempt = 0; attempt < 12 && !ref; attempt += 1) {
    const snap = await rpc("tools/call", { name: "browser_snapshot", arguments: {} }, 60000);
    snapshotText = textOf(snap);
    const dialogLine = snapshotText.split("\n").find((l) => l.includes("稍后配置"));
    if (dialogLine) {
      // 快照里那一行的 ref 是弹窗容器而不是按钮，用页面内点击关掉它
      const closed = await rpc(
        "tools/call",
        {
          name: "browser_evaluate",
          arguments: {
            function: `() => {
              const btn = [...document.querySelectorAll('[role="dialog"] button')].find(
                (b) => (b.innerText || "").trim() === "稍后配置",
              );
              if (!btn) return "no-button";
              btn.click();
              return "clicked";
            }`,
          },
        },
        60000,
      ).catch((e) => ({ content: [{ type: "text", text: `evaluate 失败：${e.message}` }] }));
      log(`第 ${attempt + 1} 次：关闭引导弹窗 → ${textOf(closed).slice(0, 60)}`);
      await rpc("tools/call", { name: "browser_wait_for", arguments: { time: 2 } }, 30000).catch(() => {});
      continue;
    }
    const targetLine = snapshotText.split("\n").find((l) => l.includes(TARGET_TEXT));
    if (targetLine) {
      ref = /\[ref=([^\]]+)\]/.exec(targetLine)?.[1];
      log(`第 ${attempt + 1} 次：找到目标 ${targetLine.trim().slice(0, 100)}`);
      break;
    }
    const booting = snapshotText.includes("Loading plugins");
    log(`第 ${attempt + 1} 次：${booting ? "应用仍在启动" : "目标未出现"}，等待 2s`);
    await rpc("tools/call", { name: "browser_wait_for", arguments: { time: 2 } }, 30000).catch(() => {});
  }
  if (!ref) {
    log(`快照前 500 字（诊断）：\n${snapshotText.slice(0, 500)}`);
    throw new Error("轮询后仍找不到按钮 ref");
  }
  log(`按钮 ref = ${ref}`);

  log("\n== 5. 真实点击 ==");
  let clickText = "";
  // 该版本的 browser_click 用 target 承载快照里的元素引用（不是文本），element 只作人类可读描述
  for (const payload of [{ target: ref, element: `${TARGET_TEXT} 按钮` }, { target: `text=${TARGET_TEXT}` }]) {
    try {
      const click = await rpc("tools/call", { name: "browser_click", arguments: payload }, 60000);
      clickText = textOf(click);
      log(`点击成功（参数形态 ${JSON.stringify(Object.keys(payload))}）：`);
      log(clickText.slice(0, 240) || "(无文本输出)");
      break;
    } catch (error) {
      log(`参数 ${JSON.stringify(Object.keys(payload))} 被拒：${error.message.slice(0, 120)}`);
    }
  }
  if (!clickText) throw new Error("browser_click 三种参数形态都没通过，未能完成点击");
  await rpc("tools/call", { name: "browser_wait_for", arguments: { time: 1.5 } }, 30000).catch(() => {});

  log("\n== 6. 读回输入框内容，确认点击生效 ==");
  const evaluate = await rpc(
    "tools/call",
    {
      name: "browser_evaluate",
      arguments: {
        function: `() => {
          const input = document.querySelector('[class*="_input"]');
          return input ? (input.innerText || input.textContent || "").trim().slice(0, 80) : "(未找到输入框)";
        }`,
      },
    },
    60000,
  ).catch((e) => ({ content: [{ type: "text", text: `browser_evaluate 不可用：${e.message}` }] }));
  const inputText = textOf(evaluate);
  log(inputText.slice(0, 400));
  const draft = /"([^"]*)"/.exec(inputText)?.[1] ?? "";
  log(`输入框内容长度：${draft.length}${draft ? `（"${draft.slice(0, 40)}"）` : ""}`);

  log("\n== 7. 截图 ==");
  const shot = await rpc("tools/call", { name: "browser_take_screenshot", arguments: { type: "png" } }, 60000);
  const image = (shot.content ?? []).find((c) => c.type === "image");
  if (image) {
    writeFileSync(OUT, Buffer.from(image.data, "base64"));
    log(`截图已保存：${OUT}（${image.mimeType}）`);
  } else {
    log("未拿到图片内容：", textOf(shot).slice(0, 200));
  }

  log(
    `\n结论：自动化后端可用——真实 Chromium 已打开应用、点了场景卡，输入框${draft ? "已被写入提示词" : "未写入内容（需人工复核）"}。`,
  );
} catch (error) {
  log(`\n❌ 失败：${error.message}`);
  process.exitCode = 1;
} finally {
  try {
    child.kill("SIGTERM");
  } catch {
    /* 已退出 */
  }
  setTimeout(() => process.exit(process.exitCode ?? 0), 1200);
}
