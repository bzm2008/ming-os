#!/usr/bin/env node
// 生成铭荼三个场景的 preset 补丁（覆盖内置 preset 的 config，不新增 id）。
//
// 为什么生成而不是手写：手抄一份工具行会在 DSH 升级时悄悄漂移。
// 这里按**每个场景自己的 sourcePreset** 读官方预设（办公→standard、开发→ptc、
// 学习→minimal），只替换名称/描述/排序/人格提示，其余原样保留。
//
// ⚠️ 2026-09-27 修正的坑：此前**所有**场景都从 standard 派生，于是 id 为 ptc 的
// 「开发模式」拿到的是 standard 的行表——workflow-ptc/tool-workflow 开着（官方 ptc 故意关掉），
// 又缺官方 ptc 的 tool-presentation（mode: ptc）。名字叫开发、能力却是别的。
// 教训：按 preset id 命名场景时，必须按同一个 id 的官方来源派生。
//
// 为什么覆盖内置行而不是停用＋新增：见 scenes.config.mjs 顶部说明——
// 停用内置 preset 会让已持久化的 selectedDefault 变成悬空引用，
// 新建会话报 agent-preset/not-found。
//
// 产物：presets/scenes.patch.yml（随包提交，勿手改）。
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { scenes } from "../scenes.config.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, "..");

// 运行时目录默认从插件目录向上查找仓库根的 .ming-tea/runtime，
// 不写死层级，避免插件目录挪动后再次算错路径。
function findRuntimeDir(from) {
  let dir = from;
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = join(dir, ".ming-tea", "runtime");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return join(from, "..", "..", "..", "..", ".ming-tea", "runtime");
}

const runtimeDir = process.env.MING_TEA_DSH_RUNTIME_DIR ?? findRuntimeDir(pluginRoot);
const presetsDir = join(runtimeDir, "node_modules/@deepseek-ai/dsh-web-app/presets");

/** 读一个官方预设，返回它 plugins 下的工具行区块（已去掉 persona、已反缩进）。 */
function readSourceBlocks(sourcePreset) {
  const file = join(presetsDir, `${sourcePreset}.patch.yml`);
  if (!existsSync(file)) {
    throw new Error(
      `找不到官方 ${sourcePreset} 预设：${file}\n` +
        "请先运行 scripts/install_ming_tea_plugins.sh 安装锁定的 DSH runtime，或设置 MING_TEA_DSH_RUNTIME_DIR。",
    );
  }
  const sourceLines = readFileSync(file, "utf8").split("\n");
  const pluginsLine = sourceLines.findIndex((line) => /^ +plugins:\s*$/.test(line));
  if (pluginsLine < 0) throw new Error(`官方 ${sourcePreset} 预设结构已变化，找不到 plugins 列表：${file}`);
  // 实测量过：standard / ptc / minimal 的 plugins 都在 8 空格、子项在 10 空格。
  // 上游改版时这里会立刻报错，而不是静默生成错文件。
  const pluginsIndent = sourceLines[pluginsLine].length - sourceLines[pluginsLine].trimStart().length;
  if (pluginsIndent !== 8) {
    throw new Error(
      `官方 ${sourcePreset} 预设的 plugins 缩进从 8 变成了 ${pluginsIndent}，需同步调整生成器：${file}`,
    );
  }
  const childStart = /^ {10}- id: /;
  const blocks = [];
  for (const line of sourceLines.slice(pluginsLine + 1)) {
    if (childStart.test(line)) blocks.push([line]);
    else if (blocks.length > 0) blocks[blocks.length - 1].push(line);
    else if (line.trim()) break; // plugins 块结束
  }
  const personaAt = blocks.findIndex((block) => /^ {10}- id: persona\s*$/.test(block[0]));
  if (personaAt < 0) throw new Error(`官方 ${sourcePreset} 预设里没有 persona 行，无法替换人格提示`);
  // 官方子项在 10 空格；我们的覆盖结构里 plugins: 在 4 空格、子项应为 6 空格，故反缩进 4 格。
  const DEDENT = 4;
  return blocks
    .filter((_, index) => index !== personaAt)
    .map((block) => {
      const lines = [...block];
      while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
      return lines
        .map((line) => {
          if (line.trim() === "") return "";
          if (!line.startsWith(" ".repeat(DEDENT))) {
            throw new Error(`工具行缩进异常，无法反缩进：${JSON.stringify(line.slice(0, 40))}`);
          }
          return line.slice(DEDENT);
        })
        .join("\n");
    });
}

// 逐场景缓存（三个场景最多三种来源，避免重复解析）
const sourceCache = new Map();
function blocksFor(scene) {
  const source = scene.sourcePreset ?? "standard";
  if (!sourceCache.has(source)) sourceCache.set(source, readSourceBlocks(source));
  return sourceCache.get(source);
}

/** 从工具行里取出它的行 id（形如 `      - id: tool-bash`）。 */
function rowIdOf(block) {
  // 不写死缩进、也不假设它在首行：组的区块里 `config:` 之后可能先是空行
  // （官方 standard 的 planning/compaction/delegation 就是这样），只看首行会全部落空。
  const match = block.match(/^( +)- id: ([^\s]+)/m);
  return match ? match[2] : "";
}

/**
 * 按场景裁剪工具：dropRows 里列出的行整个不挂。
 * 这是官方预设区分能力的原生机制（standard / ptc / minimal 本来就不挂同样的行）；
 * 相比之下 ctx.tools.restrict 只作用于全局工具，对 preset 作用域内的工具无效。
 *
 * rowToggles 用于「官方默认关、我们这一场景要开」的行（如 ralph、委派后端）：
 * 在反缩进后的区块里把顶层的 `disabled: true` 改成 false。只改该行自身的顶层键。
 */
function toolBlocksFor(scene) {
  const drop = new Set(scene.dropRows ?? []);
  const toggles = scene.rowToggles ?? {};
  return blocksFor(scene)
    .filter((block) => !drop.has(rowIdOf(block)))
    .map((block) => {
      const lines = block.split("\n");
      for (const [targetId, want] of Object.entries(toggles)) {
        const index = lines.findIndex((l) => new RegExp(`^ +- id: ${targetId}\\s*$`).test(l));
        if (index < 0) continue;
        // YAML 列表项：`- id:` 的横线缩进是 N，同行键的缩进是 N+2，
        // 所以它自己的 `disabled:` 在 N+2。**子行的缩进更深，不会被误伤。**
        const dashIndent = lines[index].match(/^( +)- id:/)[1].length;
        const keyIndent = dashIndent + 2;
        const keyPad = " ".repeat(keyIndent);
        let found = -1;
        for (let i = index + 1; i < lines.length; i += 1) {
          const l = lines[i];
          if (l.trim() === "") continue;
          const indentOf = l.match(/^( *)/)[1].length;
          if (indentOf < keyIndent) break; // 离开了这一项
          if (indentOf > keyIndent) continue; // 子项内部
          const nextId = l.match(/^( *)- id: /);
          if (nextId) break; // 下一个兄弟项
          if (/^ *disabled:/.test(l)) {
            found = i;
            break;
          }
          if (/^ *config:\s*$/.test(l)) break; // 键区结束，本项没有 disabled
        }
        if (want) {
          if (found >= 0) lines[found] = `${keyPad}disabled: false`;
        } else if (found < 0) {
          lines.splice(index + 1, 0, `${keyPad}disabled: true`);
        }
      }
      return lines.join("\n");
    });
}

const sceneBlock = (scene) => {
  // 缩进与覆盖结构对齐：plugins: 在 4 空格，子项 6、键 8、值 10
  const personaLines = [
    `      - id: persona`,
    `        name: '@deepseek-ai/dsh-persona'`,
    `        config:`,
    `          prefix: ${JSON.stringify(scene.persona)}`,
    `          suffix: Your working directory is {{cwd}}.`,
  ];
  const sceneToolBlocks = toolBlocksFor(scene);
  // 场景专属的额外**组**（浏览器自动化 + 电脑操作）。
  // 必须是 group + isolate：服务型行平铺进 preset 会让预设加载失败（见 scenes.config.mjs 说明）。
  const extraGroupLines = (scene.extraGroups ?? []).flatMap((group) => [
    `      - id: ${group.id}`,
    `        name: cordis:group`,
    `        group: true`,
    `        isolate:`,
    ...Object.entries(group.isolate).map(([k, v]) => `          ${k}: ${v}`),
    `        config:`,
    ...group.rows.flatMap((row) => [
      `          - id: ${row.id}`,
      `            name: ${JSON.stringify(row.name)}`,
      ...(row.config
        ? [
            `            config:`,
            ...Object.entries(row.config).map(([k, v]) => `              ${k}: ${JSON.stringify(v)}`),
          ]
        : []),
    ]),
  ]);
  return [
    `# 场景「${scene.name}」→ 覆盖内置 ${scene.rowId}（preset id 保持 ${scene.presetId}，`,
    `# 这样历史上指向该 id 的持久化默认值仍然有效）。工具行派生自官方 ${scene.sourcePreset ?? "standard"} 预设。`,
    `- id: ${scene.rowId}`,
    `  config:`,
    `    id: ${scene.presetId}`,
    `    name: ${JSON.stringify(scene.name)}`,
    `    description: ${JSON.stringify(scene.description)}`,
    `    order: ${scene.order}`,
    `    plugins:`,
    ...personaLines,
    ...sceneToolBlocks,
    // 场景专属的额外**顶层行**（如开发场景的 str-replace-editor；官方没挂、我们要挂）
    ...(scene.extraRows ?? []).flatMap((row) => [
      `      - id: ${row.id}`,
      `        name: ${JSON.stringify(row.name)}`,
      ...(row.config
        ? [`        config:`, ...Object.entries(row.config).map(([k, v]) => `          ${k}: ${JSON.stringify(v)}`)]
        : []),
    ]),
    ...extraGroupLines,
  ].join("\n");
};

const output =
  [
    "# 本文件由 scripts/build-presets.mjs 生成，请勿手改。",
    "# 工具列表源：@deepseek-ai/dsh-web-app/presets/standard.patch.yml",
    "# 场景定义源：scenes.config.mjs",
    "",
    ...scenes.map(sceneBlock),
    "",
  ].join("\n");

const outPath = join(pluginRoot, "presets", "scenes.patch.yml");
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, output);
console.log(
  `built ${outPath} (${output.length} bytes; ${scenes.length} scenes: ${scenes
    .map(
      (s) =>
        `${s.name}→${s.presetId}(${s.sourcePreset ?? "standard"}, 工具行 ${toolBlocksFor(s).length})`,
    )
    .join(", ")})`,
);
