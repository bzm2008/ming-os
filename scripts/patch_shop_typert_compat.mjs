#!/usr/bin/env node
// dsh-plugin-shop 的 typert 清单兼容改写。
//
// 背景：商店的编译产物把 codec 写成 `schema: <zod schema 值>`，而 DSH 的加载器
// （@deepseek-ai/dsh-typert-loader，0.1.7-rc.1 / rc.2 两版实现相同）读的是
// `create: <返回 zod schema 的工厂>`，已发布的生成器
// （@deepseek-ai/dsh-typert-generator@0.1.7-rc.2）也只生成 `create:` 形态。
// 形态不一致 → 插件行激活失败：
//   typert-loader: dsh-plugin-shop invocation "…/catalog" parameter codec has no create() factory
//
// 改写是语义等价的：把 `schema: X` 换成 `create: () => X`（官方形态就是"调用后拿到
// zod schema"，官方生成的是惰性缓存的函数，这里包一层同样满足契约）。
// 只命中 codec 对象内部（同一对象里前几行必须有 `mode: 'strict'`），不碰 `schemas:` 数组。
//
// 第二处改写（默认值，可选但推荐）：
//   「隐藏不兼容」开关在商店里是 `useState(false)`（内存态、不持久化），也就是默认
//   把 1103 个与当前 DSH 不兼容的条目一起展示给用户。对家庭用户这只会带来点开就
//   装不上的挫败，所以默认改成 true —— 用户仍可在界面上关掉它。
//
// 用法：
//   node scripts/patch_shop_typert_compat.mjs [--revert] [--dsh-home <path>]
// 幂等：已改写过的文件再跑不会重复改动。--revert 用于上游对齐后还原。
// 注意：pnpm install/重新安装商店会覆盖 node_modules，需要重跑（install_ming_tea_plugins.sh 已内置）。

import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2);
const revert = args.includes("--revert");
const homeIdx = args.indexOf("--dsh-home");
const dshHome =
  homeIdx >= 0
    ? args[homeIdx + 1]
    : (process.env.DSH_HOME ?? join(process.cwd(), ".ming-tea/runtime/dsh-home"));

// host 半区、浏览器 bundle、以及远程客户端清单三处都要改：商店的 codec 定义在三处都内联，
// 少改一处就会出现「宿主能起、网页端 boot 失败」（实测：client.js 漏改时页面报
// "web boot: 1 entry did not activate dsh-plugin-shop: failed"）。
const FILES = [
  "lib/typert.host.js",
  "lib/typert.remote-client.js",
  "lib/client.js",
];
// codec 内部的 key 行：`<indent>schema: <ident>[,]` / `<indent>create: () => <ident>[,]`
// typert.host.js 带尾逗号，client.js 不带 —— 两种都要认。
const SCHEMA_LINE = /^(\s*)schema: ([\w$]+),?\s*$/;
const CREATE_LINE = /^(\s*)create: \(\) => ([\w$]+),?\s*$/;
const STRICT_NEARBY = /mode: ['"]strict['"]/;

function targets() {
  const profilesDir = join(dshHome, "profiles");
  if (!existsSync(profilesDir)) return [];
  const out = [];
  for (const profile of readdirSync(profilesDir)) {
    const libDir = join(profilesDir, profile, "node_modules/dsh-plugin-shop/lib");
    for (const rel of FILES) {
      const file = join(profilesDir, profile, "node_modules/dsh-plugin-shop", rel);
      if (existsSync(file)) out.push({ profile, file });
    }
    void libDir;
  }
  return out;
}

function rewrite(text) {
  const lines = text.split("\n");
  let changed = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const m = revert ? CREATE_LINE.exec(lines[i]) : SCHEMA_LINE.exec(lines[i]);
    if (!m) continue;
    // 必须是 codec 对象的一部分：往上 6 行内出现 mode: 'strict'
    const window = lines.slice(Math.max(0, i - 6), i).join("\n");
    if (!STRICT_NEARBY.test(window)) continue;
    const [, indent, name] = m;
    lines[i] = revert ? `${indent}schema: ${name},` : `${indent}create: () => ${name},`;
    changed += 1;
  }
  return { text: lines.join("\n"), changed };
}

// 商店的「隐藏不兼容」开关默认值：false → true（见文件头的说明）
const DEFAULT_PATCH = {
  from: 'const [hideIncompatible, setHideIncompatible] = (0, react.useState)(false);',
  to: 'const [hideIncompatible, setHideIncompatible] = (0, react.useState)(true); /* ming-tea: 默认隐藏不兼容条目 */',
};

function patchIncompatibleDefault(text) {
  if (text.includes(DEFAULT_PATCH.to)) return { text, changed: 0 };
  if (!text.includes(DEFAULT_PATCH.from)) return { text, changed: 0 };
  return { text: text.replace(DEFAULT_PATCH.from, DEFAULT_PATCH.to), changed: 1 };
}

function revertIncompatibleDefault(text) {
  if (!text.includes(DEFAULT_PATCH.to)) return { text, changed: 0 };
  return { text: text.replace(DEFAULT_PATCH.to, DEFAULT_PATCH.from), changed: 1 };
}

const found = targets();
if (found.length === 0) {
  console.log(`未找到已安装的 dsh-plugin-shop（DSH_HOME=${dshHome}），跳过。`);
  process.exit(0);
}

let total = 0;
let defaultPatched = 0;
for (const { profile, file } of found) {
  const before = readFileSync(file, "utf8");
  const { text: afterCodec, changed } = rewrite(before);
  // 「隐藏不兼容」默认值只在浏览器 bundle（client.js）里
  const isClientBundle = file.endsWith("client.js");
  const { text, changed: changedDefault } = isClientBundle
    ? revert
      ? revertIncompatibleDefault(afterCodec)
      : patchIncompatibleDefault(afterCodec)
    : { text: afterCodec, changed: 0 };
  if (changed === 0 && changedDefault === 0) {
    console.log(`[${profile}] ${file}: 无需改动（已是目标形态）。`);
    continue;
  }
  writeFileSync(file, text, "utf8");
  total += changed;
  defaultPatched += changedDefault;
  const parts = [];
  if (changed > 0) {
    parts.push(
      `${revert ? "还原" : "改写"} ${changed} 处 codec key（${revert ? "create: () => X → schema: X" : "schema: X → create: () => X"}）`,
    );
  }
  if (changedDefault > 0) {
    parts.push(`${revert ? "还原" : "改写"}「隐藏不兼容」默认值（${revert ? "true → false" : "false → true"}）`);
  }
  console.log(`[${profile}] ${file}: ${parts.join("；")}。`);
}
console.log(
  total === 0 && defaultPatched === 0
    ? "全部文件都已处于目标形态。"
    : `完成：codec ${total} 处、默认值 ${defaultPatched} 处。这些都是本地改写，上游对齐后可 --revert 还原。`,
);
