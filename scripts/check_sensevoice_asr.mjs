#!/usr/bin/env node
// 语音识别链路的**免麦克风**验证：合成中文语音 → 官方 SenseVoice → 比对文字。
//
// 为什么能这样测：官方 provider 的 `transcribe` 本质是「POST 到 worker 起的本地 HTTP 服务」，
// 而那个服务收的就是**规范 16 kHz 单声道 PCM16 WAV**（浏览器录音也是编码成这个格式再上传的）。
// 于是「模型能不能把中文语音转成文字」可以完全离线验证，把「麦克风采集」这一段留给人。
//
// 依赖：macOS 的 `say`（TTS）与 `afconvert`（转 16k 单声道）；Node 侧只用内置模块。
// 用法：node scripts/check_sensevoice_asr.mjs   （退出码非 0 表示识别结果不符合预期）
//
// 2026-10-02 实测（本机 M 系列，Tingting 语音）：
//   「帮我看看屏幕上有什么」→「我看看屏幕上有什么。」 模型 0.07s
//   「把音量调大一点」      →「把音量调大一点。」     模型 0.05s
//   模型加载 0.7s（磁盘缓存已热）；一次 2 秒语音的端到端往返约 0.09s。

import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const RUNTIME = join(ROOT, ".ming-tea/runtime");
const PROVIDER = join(RUNTIME, "node_modules/@deepseek-ai/dsh-experimental-speech-to-text-sensevoice");
const DATA_ROOT = join(RUNTIME, "dsh-home/speech-to-text/sensevoice");
const MODELS = join(DATA_ROOT, "models");

/** 每条用例：说什么、期望转写里出现哪些片段。 */
const CASES = [
  { say: "帮我看看屏幕上有什么", expect: ["屏幕上有什么"] },
  { say: "把音量调大一点", expect: ["音量", "调大"] },
];

/** 把任意 WAV 归一成**规范 44 字节头**。
 * worker 的 validateWave 逐字段校验（'data' 必须正好在偏移 36、fmt 块正好 16 字节、长度自洽），
 * 而 `afconvert` 会插入额外块（FLLR 等），所以必须重写头，否则报 `Invalid speech WAV`。 */
function canonicalWav(input) {
  if (input.toString("ascii", 0, 4) !== "RIFF" || input.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("不是 WAV");
  }
  let offset = 12;
  let pcm = null;
  let fmt = null;
  while (offset + 8 <= input.length) {
    const id = input.toString("ascii", offset, offset + 4);
    const size = input.readUInt32LE(offset + 4);
    const body = input.subarray(offset + 8, offset + 8 + size);
    if (id === "fmt ") fmt = body;
    if (id === "data") pcm = body;
    offset += 8 + size + (size % 2);
  }
  if (fmt === null || pcm === null) throw new Error("缺 fmt 或 data 块");
  const format = fmt.readUInt16LE(0);
  const channels = fmt.readUInt16LE(2);
  const rate = fmt.readUInt32LE(4);
  const bits = fmt.readUInt16LE(14);
  if (format !== 1 || channels !== 1 || rate !== 16000 || bits !== 16) {
    throw new Error(`需要 16kHz 单声道 PCM16，实际 fmt=${format} ch=${channels} rate=${rate} bits=${bits}`);
  }
  const payload = pcm.subarray(0, pcm.length - (pcm.length % 2));
  const header = Buffer.alloc(44);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + payload.length, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(16000, 24);
  header.writeUInt32LE(32000, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(payload.length, 40);
  return Buffer.concat([header, payload]);
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

const workdir = mkdtempSync(join(tmpdir(), "ming-tea-asr-"));
const failures = [];

try {
  // 1. 合成语音并转成 provider 要的格式
  const clips = [];
  for (const [index, testCase] of CASES.entries()) {
    const aiff = join(workdir, `case-${index}.aiff`);
    const wav = join(workdir, `case-${index}.wav`);
    await run("say", ["-v", "Tingting", "-o", aiff, testCase.say]);
    await run("afconvert", ["-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav]);
    clips.push({ ...testCase, bytes: canonicalWav(readFileSync(wav)) });
  }

  // 2. 按官方 worker 的启动方式拉起它（argv[2] 是配置 JSON，token 走环境变量）
  const token = randomBytes(32).toString("hex");
  const config = {
    providerId: "sensevoice-local",
    dataRoot: DATA_ROOT,
    modelDirectory: join(MODELS, "sensevoice-onnx"),
    vadModelPath: join(MODELS, "silero/silero_vad.onnx"),
    model: join(MODELS, "sensevoice-onnx/model.int8.onnx"),
    tokens: join(MODELS, "sensevoice-onnx/tokens.txt"),
    vad: join(MODELS, "silero/silero_vad.onnx"),
  };
  const worker = spawn(process.execPath, [join(PROVIDER, "lib/worker.js"), JSON.stringify(config)], {
    cwd: PROVIDER,
    env: { ...process.env, DSH_SPEECH_TOKEN: token },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let workerStderr = "";
  worker.stderr.on("data", (chunk) => { workerStderr += chunk.toString(); });

  const started = Date.now();
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`worker 90s 内没就绪；stderr=${workerStderr.slice(-300)}`)), 90_000);
    let buffer = "";
    worker.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const line = buffer.split("\n").find((text) => text.trim() !== "");
      if (line === undefined) return;
      clearTimeout(timer);
      resolve(JSON.parse(line).port);
    });
    worker.on("exit", (code) => reject(new Error(`worker 提前退出 code=${code}；stderr=${workerStderr.slice(-300)}`)));
  });
  console.log(`worker 就绪（模型加载 ${((Date.now() - started) / 1000).toFixed(1)}s，端口 ${port}）`);

  // 3. 逐条转写并比对
  for (const clip of clips) {
    const sent = Date.now();
    const response = await fetch(`http://127.0.0.1:${port}/transcribe?language=zh`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "audio/wav",
        "content-length": String(clip.bytes.length),
      },
      body: clip.bytes,
    });
    const body = await response.json();
    if (!response.ok) {
      failures.push(`HTTP ${response.status} ${JSON.stringify(body)}`);
      console.log(`❌ 「${clip.say}」→ HTTP ${response.status} ${JSON.stringify(body)}`);
      continue;
    }
    const missing = clip.expect.filter((keyword) => !String(body.text ?? "").includes(keyword));
    const ok = missing.length === 0 && String(body.text ?? "").trim() !== "";
    console.log(
      `${ok ? "✅" : "❌"} 「${clip.say}」→「${body.text}」` +
        `（音频 ${body.audioSeconds?.toFixed?.(2)}s，模型 ${body.inferenceSeconds?.toFixed?.(2)}s，往返 ${((Date.now() - sent) / 1000).toFixed(2)}s）`,
    );
    if (!ok) failures.push(`「${clip.say}」转写「${body.text}」缺少 ${JSON.stringify(missing)}`);
  }

  worker.kill("SIGTERM");
} finally {
  rmSync(workdir, { recursive: true, force: true });
}

if (failures.length > 0) {
  console.log(`\n结果：${failures.length} 项不符合预期`);
  for (const line of failures) console.log(`  - ${line}`);
  process.exit(1);
}
console.log("\n结果：全部通过（识别链路本体正常；麦克风采集仍需真人验证）");
