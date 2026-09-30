#!/usr/bin/env node
// 把 theme/*.css、生成的令牌覆盖块与 ui-tweaks.js 一起内联进 lib/client.js。
//
// bundle 只注册一个工厂，物化时执行：注入 `<style>`（含由 palette.mjs 生成的
// 令牌覆盖块）、应用 UI 定制、挂 MutationObserver。色板同时通过官方
// `ctx.theme.overrideTokens` 写入（见 apply），两条通道同源同值：
// 官方通道路径更"正统"（写 body 内联样式），CSS 通道保证 API 形态变化时样式仍在。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { toCssBlock, toOverrideTokens } from "../theme/palette.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const pluginName = JSON.parse(readFileSync(join(root, "package.json"), "utf8")).name;
const cssPath = join(root, "theme", "ming-tea-theme.css");
const tweaksPath = join(root, "ui-tweaks.js");
const outPath = join(root, "lib", "client.js");

const baseCss = readFileSync(cssPath, "utf8");
const tokenCss = toCssBlock();
const css = `${baseCss}\n\n/* ==== 由 theme/palette.mjs 生成，勿手改 ==== */\n${tokenCss}`;
const tokens = toOverrideTokens();

// ui-tweaks.js 是普通 JS 源码（含 export），去掉 export 后整体内联，
// 保证 bundle 自包含、不依赖宿主的模块图。
const tweaksSource = readFileSync(tweaksPath, "utf8")
  .replace(/^export\s+function\s+startMingTeaTweaks/m, "function startMingTeaTweaks")
  .replace(/^export\s+/gm, "");
const styleId = `${pluginName.replace(/[^a-z0-9]+/gi, "-")}-theme`;

const bundle = `window.__ModuleLoader__.load({
\tid: ${JSON.stringify(pluginName)},
\tfactory: (require) => {
\t\tvar module = { exports: {} };
\t\tvar exports = module.exports;
\t\tObject.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
\t\tconst PLUGIN_ID = ${JSON.stringify(pluginName)};
\t\tconst STYLE_ID = ${JSON.stringify(styleId)};
\t\tconst CSS = ${JSON.stringify(css)};
\t\tconst TOKEN_OVERRIDES = ${JSON.stringify(tokens)};
\t\tif (!document.getElementById(STYLE_ID)) {
\t\t\tconst style = document.createElement("style");
\t\t\tstyle.id = STYLE_ID;
\t\t\tstyle.setAttribute("data-ming-tea", "rounded-theme");
\t\t\tstyle.textContent = CSS;
\t\t\tdocument.head.appendChild(style);
\t\t}
${tweaksSource
  .split("\n")
  .map((line) => (line ? `\t\t${line}` : ""))
  .join("\n")}
\t\tstartMingTeaTweaks();

\t\t// 官方一等公民通道：overrideTokens 把所有值写成 body 内联样式，
\t\t// 必然胜过 body 上的官方声明（这正是早期 :root 覆盖失效的原因）。
\t\t// 失败只记录、不抛出：样式已由上面注入的 CSS 块保证。
\t\tfunction apply(ctx) {
\t\t\t// 先把上下文交给定制层：首页场景卡需要 sessions / conversation 服务
\t\t\tsetMingTeaContext(ctx);
\t\t\tmingTeaRegisterUpdatePage(ctx);
\t\t\tmingTeaRegisterUsagePage(ctx);
\t\t\ttry {
\t\t\t\tif (!ctx || !ctx.theme || typeof ctx.theme.overrideTokens !== "function") return;
\t\t\t\tconst dispose = ctx.theme.overrideTokens(PLUGIN_ID, TOKEN_OVERRIDES);
\t\t\t\tif (typeof ctx.effect === "function" && typeof dispose === "function") {
\t\t\t\t\tctx.effect(() => dispose);
\t\t\t\t}
\t\t\t} catch (error) {
\t\t\t\tconst list = (window.__mingTeaThemeErrors = window.__mingTeaThemeErrors || []);
\t\t\t\tlist.push(String((error && error.message) || error));
\t\t\t}
\t\t}
\t\texports.apply = apply;
\t\texports.inject = ["theme", "sessions", "conversation", "connection", "slots"];
\t\treturn module.exports;
\t}
});
`;

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, bundle);
console.log(
  `built ${outPath} (${bundle.length} bytes; css ${css.length}; tokens ${Object.keys(tokens).length}; tweaks ${tweaksSource.length})`,
);
