// 铭荼色板：单一事实来源。
//
// 为什么要放在数据里而不是直接写 CSS：官方把设计令牌声明在 `body{}` /
// `body[data-ds-dark-theme]{}` 上（见 dsh-client-ui-theme 的 design-platform.css），
// 早期我们把覆盖写在 `:root`，被 body 自身声明遮蔽 —— 实测 `--dsw-alias-link`
// 在 html 上是我们的值、在 body 与深层元素上仍是官方蓝，即**覆盖完全失效**。
//
// 现在两条路同时走，值来自同一份数据：
//   1) 官方 API `ctx.theme.overrideTokens(source, { '--token': { light, dark } })`
//      —— 写入 body 内联样式，必然胜出；
//   2) `body { ... }` + `body[data-ds-dark-theme] { ... }` 的 CSS 块（脚本生成）
//      —— 万一 API 形态与预期不符，样式仍然生效。
//
// 只覆盖"角色色"（alias 层）与语义状态色；不动 `--dsw-static-*` 原始调色板，
// 避免连带改变语义状态色与第三方组件的推断色。

/** 铭荼品牌色板（与 theme/ming-tea-theme.css 的 --mt-* 保持一致）。 */
export const MING_TEA_PALETTE = {
  // 品牌薄荷
  brand: { light: "#16857d", dark: "#4fb3a4" },
  brandStrong: { light: "#0f716b", dark: "#6cc7b9" },
  brandSoft: { light: "#e6f4f0", dark: "#12312e" },
  brandTint: { light: "#d9efea", dark: "#1b3a36" },

  // 纸面
  paper: { light: "#f6faf8", dark: "#171d1c" },
  paperRaised: { light: "#fbfdfc", dark: "#1d2423" },
  paperSunken: { light: "#eef4f1", dark: "#121817" },

  // 墨色（文字层级）
  ink: { light: "#12333a", dark: "#e8f3f1" },
  inkSecondary: { light: "#4a6a66", dark: "#b9cfc9" },
  inkTertiary: { light: "#6b8a85", dark: "#93aaa4" },
  inkCaption: { light: "#89a5a0", dark: "#7d948f" },

  // 发丝线（薄荷调，避免官方中性灰与品牌色错配）
  hairline1: { light: "rgba(22,133,125,0.06)", dark: "rgba(127,208,196,0.08)" },
  hairline2: { light: "rgba(22,133,125,0.12)", dark: "rgba(127,208,196,0.14)" },
  hairline3: { light: "rgba(22,133,125,0.16)", dark: "rgba(127,208,196,0.18)" },
  hairline4: { light: "rgba(22,133,125,0.22)", dark: "rgba(127,208,196,0.24)" },

  // 交互态
  hover: { light: "rgba(22,133,125,0.07)", dark: "rgba(127,208,196,0.10)" },
  hoverAccent: { light: "rgba(22,133,125,0.14)", dark: "rgba(127,208,196,0.18)" },
  active: { light: "rgba(22,133,125,0.12)", dark: "rgba(127,208,196,0.16)" },
  skeleton: { light: "rgba(22,133,125,0.05)", dark: "rgba(127,208,196,0.08)" },

  // 蒙层
  mask1: { light: "rgba(18,51,58,0.22)", dark: "rgba(0,0,0,0.50)" },
  mask2: { light: "rgba(18,51,58,0.12)", dark: "rgba(0,0,0,0.36)" },
  mask3: { light: "rgba(18,51,58,0.48)", dark: "rgba(0,0,0,0.66)" },

  // 浮层
  menu: { light: "rgba(251,253,252,0.86)", dark: "rgba(29,36,35,0.86)" },
  tooltip: { light: "#12333a", dark: "#e8f3f1" },
  overlay: { light: "#e4eeea", dark: "#2b3331" },
};

const { brand, brandStrong, brandSoft, brandTint, paper, paperRaised, paperSunken,
  ink, inkSecondary, inkTertiary, inkCaption, hairline1, hairline2, hairline3, hairline4,
  hover, hoverAccent, active, skeleton, mask1, mask2, mask3, menu, tooltip, overlay } =
  MING_TEA_PALETTE;

/**
 * 令牌覆盖表：`--dsw-*` 变量名 → { light, dark }。
 * 分四组：品牌与强调、表面与文字、发丝线与交互、浮层与蒙层；末尾补官方
 * "被消费却从未定义" 的令牌（不补会出现回退到品牌蓝等错误观感）。
 */
export const TOKEN_OVERRIDES = {
  // ── 品牌与强调 ────────────────────────────────────────────────
  "--dsw-alias-link": brand,
  "--dsw-alias-state-business-primary": brand,
  "--dsw-alias-state-business-tertiary": brandSoft,
  "--dsw-alias-brand-primary-new-colorprimary-new-color": brand,
  // 2026-10-02：第三方插件（dsh-context 仪表盘、dsh-routing-suite 状态条）用的是这个**短名字**，
  // 而官方把它定义成偏蓝的中性色（`--dsw-static-neutral-bluish-1000`）。不覆盖的话，
  // 新装的插件强调色会是官方蓝灰、与我们薄荷青绿不一致（实测：dsh-context 里 21 处引用）。
  "--dsw-alias-brand-primary": brand,
  "--dsw-alias-button-info-fill": brand,
  "--dsw-alias-button-info-hover": brandStrong,
  "--dsw-alias-button-primary-fill": brand,
  "--dsw-alias-button-primary-hover": brandStrong,

  // ── 表面与文字 ───────────────────────────────────────────────
  "--dsw-alias-bg-base": paper,
  "--dsw-alias-bg-layer-1": paperRaised,
  "--dsw-alias-bg-layer-2": paperRaised,
  "--dsw-alias-bg-layer-3": paperRaised,
  "--dsw-alias-bg-overlay": overlay,
  "--dsw-alias-bg-module-platform": paperSunken,
  "--dsw-alias-label-primary": ink,
  "--dsw-alias-label-secondary": inkSecondary,
  "--dsw-alias-label-tertiary": inkTertiary,
  "--dsw-alias-label-caption": inkCaption,

  // ── 发丝线与交互态 ───────────────────────────────────────────
  "--dsw-alias-border-l1": hairline1,
  "--dsw-alias-border-l2": hairline2,
  "--dsw-alias-border-l2-darkmode-thin": hairline1,
  "--dsw-alias-border-l3": hairline3,
  "--dsw-alias-border-l4": hairline4,
  "--dsw-alias-interactive-bg-hover": hover,
  "--dsw-alias-interactive-bg-hover-accent": hoverAccent,
  "--dsw-alias-interactive-bg-hover-solid": hover,
  "--dsw-alias-interactive-bg-active": active,
  "--dsw-alias-bg-skeleton": skeleton,

  // ── 气泡、代码、侧栏、浮层 ───────────────────────────────────
  "--dsw-specific-bubble": brandSoft,
  "--dsw-specific-bubble-highlight": brandTint,
  "--dsw-specific-menu": menu,
  "--dsw-specific-input-major": paperRaised,
  "--dsw-specific-sidebar-nav-item-active": brandSoft,
  "--dsw-specific-sidebar-nav-item-active-accent": brandTint,
  "--dsw-specific-sidebar-nav-item-hover": hover,
  "--dsw-alias-markdown-code-block": paperSunken,
  "--dsw-alias-markdown-code-block-banner": paperSunken,
  "--dsw-alias-markdown-inline-code": brandSoft,
  "--dsw-alias-tooltip-bg": tooltip,
  "--dsw-alias-toast-bg": tooltip,

  // ── 蒙层 ─────────────────────────────────────────────────────
  "--dsw-alias-bg-mask-1": mask1,
  "--dsw-alias-bg-mask-2": mask2,
  "--dsw-alias-bg-mask-3": mask3,

  // ── 官方"被消费但从未定义"的令牌（不补会回退到品牌蓝/无 fallback）──
  "--dsw-alias-state-warning-primary": { light: "#d98218", dark: "#f0a94a" },
  "--dsw-alias-state-warn-tertiary": { light: "#fdf1e0", dark: "#3a2c17" },
  "--dsw-alias-bg-layer-4": paperRaised,
  "--dsw-alias-label-error": { light: "#c62828", dark: "#f25a5a" },
  "--dsw-alias-bg-l1": paper,
  "--dsw-alias-bg-l2": paperRaised,
  "--dsw-alias-separator-primary": hairline1,
  "--dsw-alias-fill-l1": hover,
  "--dsw-alias-fill-l2": hoverAccent,
  "--dsw-alias-fill-tsp-secondary": hover,
  "--dsw-alias-label-quaternary": inkCaption,

  // ── 局部组件令牌（官方在组件类上写死或引用蓝色）──────────────
  // trajectory 的回合强调色官方用 blue-500 混色，浅色下发灰
  "--trajectory-turn-accent": {
    light: "color-mix(in srgb, #16857d 18%, var(--dsw-alias-bg-layer-1))",
    dark: "color-mix(in srgb, #4fb3a4 22%, var(--dsw-alias-bg-layer-1))",
  },
};

/** 生成 body 作用域的 CSS 覆盖块（兜底通道；与 overrideTokens 同源同值）。 */
export function toCssBlock(overrides = ALL_OVERRIDES) {
  const light = [];
  const dark = [];
  for (const [name, modes] of Object.entries(overrides)) {
    if (modes.light) light.push(`${name}: ${modes.light};`);
    if (modes.dark) dark.push(`${name}: ${modes.dark};`);
  }
  return [
    "/* 令牌覆盖：作用域必须是 body —— 官方把令牌声明在 body 上，写在 :root 会被遮蔽。",
    " * 这里再提高一级特异度（:root body），避免依赖样式注入顺序。 */",
    `:root body {\n  ${light.join("\n  ")}\n}`,
    `:root body[data-ds-dark-theme] {\n  ${dark.join("\n  ")}\n}`,
    "",
  ].join("\n");
}

/** 官方 overrideTokens 需要的 { light, dark } 形式（值必须是字符串）。 */
export function toOverrideTokens(overrides = ALL_OVERRIDES) {
  const out = {};
  for (const [name, modes] of Object.entries(overrides)) {
    out[name] = { light: modes.light, dark: modes.dark };
  }
  return out;
}

/** 字体栈：中文优先，等宽保留 CJK 兜底（否则代码块中文注释会退化）。 */
export const FONT_OVERRIDES = {
  "--dsw-font-family": {
    light:
      '"Noto Sans CJK SC", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
    dark: '"Noto Sans CJK SC", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
  },
  "--ds-font-family-code": {
    light:
      '"SF Mono", "JetBrains Mono", "Fira Code", Menlo, Consolas, "Noto Sans Mono CJK SC", "PingFang SC", "Microsoft YaHei", monospace',
    dark: '"SF Mono", "JetBrains Mono", "Fira Code", Menlo, Consolas, "Noto Sans Mono CJK SC", "PingFang SC", "Microsoft YaHei", monospace',
  },
  // 官方有 8 处消费 --dsw-font-mono 却从未定义它
  "--dsw-font-mono": {
    light:
      '"SF Mono", "JetBrains Mono", Menlo, Consolas, "Noto Sans Mono CJK SC", monospace',
    dark: '"SF Mono", "JetBrains Mono", Menlo, Consolas, "Noto Sans Mono CJK SC", monospace',
  },
};

/** 两条通道共用的完整覆盖集（角色色 + 字体栈 + 官方未定义令牌）。 */
export const ALL_OVERRIDES = { ...TOKEN_OVERRIDES, ...FONT_OVERRIDES };
