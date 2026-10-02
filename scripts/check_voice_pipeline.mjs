#!/usr/bin/env node
// 语音链路**端到端**验证（不需要真人麦克风）：合成语音 → 假麦克风 → 真实采集/上传 → SenseVoice → 文字。
//
// 原理：Chromium 支持把 WAV 文件当成麦克风输入
//   `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<file.wav>`
// 于是 `getUserMedia` 会成功（此前一直卡在这里：无音频输入设备），
// 而后面所有的环节都是**真的**：真实采集、真实重采样编码、真实上传、真实本地模型推理。
//
// 两种模式：
//   composer —— 官方输入区的「开始录音」：录完断言文字落进输入框；
//   panel    —— summon 面板：点一下宠物 → 相态 recording + 面板切 listening（胶囊「我在听…」）
//               → 录完断言胶囊里出现转写文字（这条是被呼出的助手真正的用法）。
//
// 用法：
//   node scripts/check_voice_pipeline.mjs                # 两种模式都跑（用 ~/…/dsh-host.json 里的宿主）
//   node scripts/check_voice_pipeline.mjs --mode panel
//   node scripts/check_voice_pipeline.mjs --host http://127.0.0.1:PORT/?token=…
//
// 依赖：macOS 的 `say` + `afconvert`；宿主需已在运行（打包版应用或 `dsh --profile ming-tea`）。
// 2026-10-02 实测：composer 模式 8 秒录制里文件循环，输入框得到
//   「我看看屏幕上有什么，帮我看看屏幕上有什么，帮我看看屏幕上有什么，帮我看看。」

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const requireFromRuntime = createRequire(join(ROOT, ".ming-tea/runtime/"));
const { chromium } = requireFromRuntime("playwright");

const PHRASE = "帮我看看屏幕上有什么";
const EXPECT = "屏幕上有什么";
/** 官方语音触发按钮的选择器（与插件 ui-tweaks.js 的 MING_TEA_VOICE_TRIGGER_SELECTORS 一致）。 */
const TRIGGER_SELECTORS = [
  '[data-composer-card] button[aria-label*="录音"]',
  '[data-composer-card] button[aria-label*="语音"]',
  '[data-composer-card] button[aria-label*="说话"]',
  '[data-composer-card] [class*="triggerAnchor"] button',
  '[data-composer-card] button[class*="trigger"]',
  '[class*="triggerAnchor"] button',
];

function argOf(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

/** 宿主 URL：默认读应用写的 dsh-host.json（打包版运行中时就在那里）。 */
function hostUrl() {
  const explicit = argOf("host", null);
  if (explicit !== null) return explicit;
  const file = join(homedir(), "Library/Application Support/铭荼/dsh-host.json");
  if (!existsSync(file)) throw new Error(`没有 --host，且找不到 ${file}（宿主没在跑？）`);
  return JSON.parse(readFileSync(file, "utf8")).url;
}

/** 合成语音并转成 16kHz 单声道 16bit WAV（Chromium 假麦克风与 provider 都认这个格式）。 */
async function synthesizeWorkdirPhrase(workdir) {
  const aiff = join(workdir, "phrase.aiff");
  const wav = join(workdir, "phrase.wav");
  await run("say", ["-v", "Tingting", "-o", aiff, PHRASE]);
  await run("afconvert", ["-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav]);
  return wav;
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${command} 退出码 ${code}: ${stderr.slice(0, 200)}`))));
  });
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 停止录音并开始识别。
 * ⚠️ 录音中官方把麦克风按钮改名成「取消」，另起一个「停止并识别」——
 * 按 aria-label 找「录音」会点空（2026-10-02 实测踩到：以为点了停止，其实是自动停止兜住的）。 */
async function stopRecording(page) {
  // 必须用**页面内 JS 点击**：summon 模式下我们把输入区移到了屏幕外（left:-10000px）
  // 并对它设了 pointer-events:none，Playwright 的真实点击会报 Element is outside of the viewport。
  // 插件自己的「点一下开始说话」也是这么点的（合成事件对 WKWebView 开麦够用，但真实手势点不到）。
  const clicked = await page.evaluate(() => {
    const button = document.querySelector(
      '[data-composer-card] button[aria-label="停止并识别"], [data-composer-card] button[aria-label*="停止"]',
    );
    if (button === null) return false;
    button.click();
    return true;
  });
  if (!clicked) log("⚠️ 没找到「停止并识别」按钮（相态可能已经自己结束了）");
}
const log = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

/** 共用：开浏览器（带假麦克风）、打开宿主页面。 */
async function openPage(url, wav, { summon }) {
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${wav}`,
    ],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, permissions: ["microphone"] });
  const page = await context.newPage();
  if (summon) {
    // 模拟壳在 document-start 注入的参数（与真实面板一致）
    await page.addInitScript(() => {
      window.__MING_TEA_SUMMON = { on: true, auto: false, shell: "http://127.0.0.1:9", token: "t" };
    });
  }
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForSelector("[data-composer-card]", { timeout: 90_000 });
  return { browser, page };
}

async function checkFakeMic(page) {
  const info = await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const track = stream.getAudioTracks()[0];
    const info = { label: track?.label ?? null, sampleRate: track?.getSettings?.().sampleRate ?? null };
    stream.getTracks().forEach((item) => item.stop());
    return info;
  });
  log("假麦克风:", JSON.stringify(info));
  return info;
}

async function runComposerMode(url, wav) {
  const { browser, page } = await openPage(url, wav, { summon: false });
  try {
    await checkFakeMic(page);
    const mic = page.locator('[data-composer-card] button[aria-label*="录音"]').first();
    await mic.waitFor({ timeout: 90_000 });
    await mic.click({ force: true });
    log("composer：开始录音，喂 8 秒合成语音…");
    await wait(8000);
    await stopRecording(page);
    for (let i = 0; i < 20; i += 1) {
      await wait(2000);
      const value = await page.evaluate(() => {
        const box = document.querySelector("[data-composer-card] textarea, [data-composer-card] [contenteditable='true']");
        return box?.value ?? box?.textContent ?? "";
      });
      if (value.includes(EXPECT)) {
        log(`composer：转写已落进输入框 →「${value.slice(0, 80)}」`);
        return `composer 模式通过（${value.trim().slice(0, 40)}…）`;
      }
    }
    return "composer 模式失败：20 次轮询内输入框没有出现预期文字";
  } finally {
    await browser.close();
  }
}

async function runPanelMode(url, wav) {
  const { browser, page } = await openPage(url, wav, { summon: true });
  try {
    await checkFakeMic(page);
    await page.waitForFunction(() => typeof window.__mingTeaHub === "object" && !!document.querySelector(".mt-summon-layer"), null, { timeout: 90_000 });
    await page.locator(".mt-summon-layer").first().click({ force: true });
    log("panel：已点一下宠物，等待官方麦克风被点开…");
    await wait(3000);
    const during = await page.evaluate(() => ({
      phase: document.querySelector("[data-voice-activity]")?.getAttribute("data-voice-activity") ?? null,
      stage: document.documentElement.dataset.mingTeaSummonStage ?? null,
      capsule: document.querySelector(".mt-summon-capsule")?.textContent ?? "",
    }));
    log("panel：录音中状态", JSON.stringify(during));
    if (during.phase !== "recording") log("⚠️ 没有进入 recording（面板里的开麦可能失败）");

    await wait(8000);
    await stopRecording(page);
    for (let i = 0; i < 20; i += 1) {
      await wait(2000);
      const capsule = await page.evaluate(() => ({
        text: document.querySelector(".mt-summon-capsule")?.textContent ?? "",
        stage: document.documentElement.dataset.mingTeaSummonStage ?? null,
      }));
      if (capsule.text.includes(EXPECT)) {
        log(`panel：胶囊里有转写 →「${capsule.text.slice(0, 60)}」（stage=${capsule.stage}）`);
        return `panel 模式通过（胶囊显示「${capsule.text.trim().slice(0, 30)}…」）`;
      }
    }
    return "panel 模式失败：20 次轮询内胶囊没出现预期文字";
  } finally {
    await browser.close();
  }
}

/** 第三档：点得太早时**要重试**（2026-10-02 修的那个「点了没反应」）。
 *
 * 造条件的方式：把官方语音触发按钮**全部禁用**（等价于「按钮还没懒挂载」），点宠物 → 插件应记下意图、
 * 胶囊显示「正在准备麦克风…」；随后恢复按钮 → 插件应在下一轮 tick 自己把麦克风点开。 */
async function runEarlyTapMode(url, wav) {
  const { browser, page } = await openPage(url, wav, { summon: true });
  const toggle = (disabled) =>
    page.evaluate(
      ({ selectors, disabled }) => {
        let count = 0;
        for (const selector of selectors) {
          for (const button of document.querySelectorAll(selector)) {
            button.disabled = disabled;
            count += 1;
          }
        }
        return count;
      },
      { selectors: TRIGGER_SELECTORS, disabled },
    );
  const snap = () =>
    page.evaluate(() => ({
      tapResult: document.documentElement.dataset.mingTeaTapResult ?? null,
      phase: document.querySelector("[data-voice-activity]")?.getAttribute("data-voice-activity") ?? null,
      capsule: document.querySelector(".mt-summon-capsule")?.textContent ?? "",
    }));
  try {
    await checkFakeMic(page);
    await page.waitForFunction(() => typeof window.__mingTeaHub === "object" && !!document.querySelector(".mt-summon-layer"), null, { timeout: 90_000 });
    await wait(12_000); // 等语音行挂载，禁用才有对象
    const disabled = await toggle(true);
    if (disabled === 0) return "early-tap 模式失败：没找到任何语音触发按钮，无法造条件";
    await page.locator(".mt-summon-layer").first().click({ force: true });
    await wait(900);
    const early = await snap();
    log("early-tap：按钮不可用时点了宠物 →", JSON.stringify(early));
    if (early.tapResult !== "no-trigger") return `early-tap 模式失败：期望 no-trigger，实际 ${early.tapResult}`;
    if (!early.capsule.includes("准备")) return `early-tap 模式失败：期望胶囊给出「正在准备麦克风…」反馈，实际「${early.capsule}」`;
    await toggle(false);
    for (let i = 0; i < 12; i += 1) {
      await wait(1000);
      const state = await snap();
      if (state.phase === "recording") {
        log(`early-tap：第 ${i + 1} 秒重试成功（tapResult=${state.tapResult}，胶囊「${state.capsule}」）`);
        return `early-tap 模式通过（重试 ${i + 1}s 后进入录音）`;
      }
    }
    return "early-tap 模式失败：恢复按钮后 12 秒内没有进入录音";
  } finally {
    await browser.close();
  }
}

const workdir = mkdtempSync(join(tmpdir(), "ming-tea-voice-"));
const results = [];
try {
  const url = hostUrl();
  const wav = await synthesizeWorkdirPhrase(workdir);
  log(`宿主 ${url.split("?")[0]}；语音文件 ${wav}`);
  const mode = argOf("mode", "all");
  if (mode === "all" || mode === "composer") results.push(await runComposerMode(url, wav));
  if (mode === "all" || mode === "panel") results.push(await runPanelMode(url, wav));
  if (mode === "all" || mode === "early-tap") results.push(await runEarlyTapMode(url, wav));
} finally {
  rmSync(workdir, { recursive: true, force: true });
}

console.log("\n结果：");
for (const line of results) console.log(`  - ${line}`);
process.exit(results.every((line) => line.includes("通过")) ? 0 : 1);
