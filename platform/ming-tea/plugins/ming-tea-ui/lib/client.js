window.__ModuleLoader__.load({
	id: "@ming-tea/dsh-ui",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const PLUGIN_ID = "@ming-tea/dsh-ui";
		const STYLE_ID = "-ming-tea-dsh-ui-theme";
		const CSS = "/* 铭荼圆润界面层\n *\n * 叠加在社区前端（@michengai/dsh-codex-ui）与官方 DSH 客户端之上。\n * 只调整几何（圆角、阴影、描边），不覆盖颜色语义，因此浅色与深色两套\n * 官方主题都保持成立；铭荼的品牌色沿用社区侧栏已有的薄荷青绿。\n *\n * 选择器约定（依据 2026-09-26 对真实 DOM 的普查）：\n *   - 社区 UI 使用稳定的 dcu-* 前缀类名，直接匹配；\n *   - 官方组件使用 CSS Modules，类名形如 <hash>_<语义名>，哈希随构建变化\n *     而语义名稳定，因此按 [class*=\"_语义名\"] 匹配；\n *   - 宿主外壳提供语义化的 data-* 钩子（data-slot、data-composer-card 等），\n *     这些最稳定，优先使用。\n * 所有覆盖都带 :root 前缀，确保胜过单类选择器的组件样式（0-2-0 > 0-1-0），\n * 同时不依赖 !important。\n */\n\n:root {\n  /* 铭荼自有变量：圆角尺度、柔光阴影、品牌色。\n     这些是**我们自己的**变量（不是官方令牌），:root 声明可以正常继承。 */\n  --mt-radius-sm: 12px;\n  --mt-radius-md: 16px;\n  --mt-radius-lg: 22px;\n  --mt-radius-xl: 26px;\n  --mt-radius-pill: 999px;\n  --mt-shadow-soft: 0 10px 30px rgb(23 51 58 / 10%);\n  --mt-shadow-lift:\n    0 2px 8px rgb(23 51 58 / 6%), 0 12px 36px rgb(23 51 58 / 10%);\n\n  /* 铭荼品牌色：薄荷青绿（浅色模式） */\n  --mt-brand: #16857d;\n  --mt-brand-strong: #0f716b;\n  --mt-brand-soft: #e6f4f0;\n  --mt-brand-ink: #147d78;\n  --mt-ink-strong: #12333a;\n  --mt-paper: #f6faf8;\n  --mt-paper-raised: #fbfdfc;\n\n  /* 官方令牌 --dsw-* / --dsh-* / --dsl-* 的覆盖**不在这里**：\n     它们由官方声明在 body 上，写在 :root 会被遮蔽（实测失效）。\n     现由 theme/palette.mjs 统一生成，作用域为 `:root body`，\n     并同时通过 ctx.theme.overrideTokens 写入 body 内联样式。 */\n}\n\n/* 宿主内容区与代码块的圆角令牌：同样必须在 body 作用域 */\n:root body {\n  --dsh-windows-content-radius: var(--mt-radius-lg);\n  --dsl-code-block-border-radius: var(--mt-radius-sm);\n}\n\n/* 深色模式：品牌色提亮，底色带一点墨绿，避免与原版的中性灰同脸 */\nbody[data-ds-dark-theme] {\n  --mt-brand: #4fb3a4;\n  --mt-brand-strong: #6cc7b9;\n  --mt-brand-soft: #12312e;\n  --mt-brand-ink: #7fd0c4;\n  --mt-ink-strong: #e8f3f1;\n  --mt-paper: #171d1c;\n  --mt-paper-raised: #1d2423;\n  --mt-shadow-soft: 0 10px 30px rgb(0 0 0 / 45%);\n  --mt-shadow-lift:\n    0 2px 8px rgb(0 0 0 / 35%), 0 12px 36px rgb(0 0 0 / 45%);\n}\n\n/* ── 输入与编辑区：抬到 md ───────────────────────────────────────── */\n:root [class*=\"_input\"],\n:root [class*=\"_editor\"],\n:root [class*=\"_installField\"],\n:root [class*=\"_registryCustomField\"],\n:root [class*=\"_search\"],\n:root [class*=\"_select\"],\n:root [class*=\"_field\"] {\n  border-radius: var(--mt-radius-md);\n}\n\n/* ── 小控件：抬到 sm ─────────────────────────────────────────────── */\n:root [class*=\"_iconButton\"],\n:root [class*=\"_button\"],\n:root [class*=\"_trigger\"],\n:root [class*=\"_toggle\"],\n:root [class*=\"_rowTag\"],\n:root [class*=\"_badge\"],\n:root [class*=\"_chip\"],\n:root [class*=\"_candidate\"],\n:root [class*=\"_wizardClose\"] {\n  border-radius: var(--mt-radius-sm);\n}\n\n/* ── 卡片与条目：抬到 lg ──────────────────────────────────────────\n * 注意：这里的 `_guide` / `_panel` / `_surface` 常常是**整列或整块结构容器**\n * （例如右侧边栏的内容容器）。给这类元素加 30px 模糊阴影会跨过栏边界渗到\n * 相邻栏，表现为中缝位置一条发虚的竖带——即用户报的\"缝\"。\n * 因此结构容器只统一圆角，不加阴影；阴影只给真正浮动或小面积的元素。 */\n:root [class*=\"_card\"],\n:root [class*=\"_addCard\"],\n:root [class*=\"_rowCard\"],\n:root [class*=\"_balanceCard\"],\n:root [class*=\"_modelEntry\"],\n:root [class*=\"_banner\"],\n:root [class*=\"_notice\"],\n:root [class*=\"_guide\"],\n:root [class*=\"_panel\"],\n:root [class*=\"_surface\"] {\n  border-radius: var(--mt-radius-lg);\n}\n\n:root [class*=\"_card\"],\n:root [class*=\"_addCard\"],\n:root [class*=\"_rowCard\"],\n:root [class*=\"_balanceCard\"],\n:root [class*=\"_modelEntry\"] {\n  box-shadow: var(--mt-shadow-soft);\n}\n\n/* ── 浮层：对话框、菜单、弹层用最大圆角 ─────────────────────────── */\n:root [class*=\"_dialog\"],\n:root [class*=\"_menu\"],\n:root [class*=\"_popover\"],\n:root [class*=\"_tooltip\"],\n:root [role=\"dialog\"],\n:root [role=\"menu\"],\n:root [data-slot=\"conversation.input.overlay\"] > * {\n  border-radius: var(--mt-radius-xl);\n}\n\n:root [class*=\"_dialog\"],\n:root [role=\"dialog\"] {\n  box-shadow: var(--mt-shadow-lift);\n}\n\n/* ── 头像与缩略图：保持方形语汇但更圆 ───────────────────────────── */\n:root [class*=\"_avatar\"],\n:root [class*=\"_thumbnail\"] {\n  border-radius: var(--mt-radius-lg);\n}\n\n/* ── 宿主外壳的语义钩子（最稳定，优先） ─────────────────────────── */\n:root [data-composer-card] {\n  border-radius: var(--mt-radius-lg);\n  box-shadow: var(--mt-shadow-soft);\n}\n\n:root [data-slot=\"conversation.composer.bar\"],\n:root [data-slot=\"conversation.input.dock\"] > * {\n  border-radius: var(--mt-radius-lg);\n}\n\n:root [data-slot=\"conversation.session\"] [class*=\"_bubble\"],\n:root [class*=\"_bubble\"] {\n  border-radius: var(--mt-radius-lg);\n}\n\n/* ── 社区工作台（dcu-*）─────────────────────────────────────────── */\n:root .dcu-home-card {\n  border-radius: var(--mt-radius-lg);\n}\n\n:root .dcu-home-task,\n:root .dcu-wb-project-head {\n  border-radius: var(--mt-radius-md);\n}\n\n:root .dcu-wb-section-head,\n:root .dcu-wb-more,\n:root .dcu-extensions-toggle,\n:root .dcu-settings-trigger {\n  border-radius: var(--mt-radius-sm);\n}\n\n/* ── 铭荼品牌：替换官方字标与首页标记 ──────────────────────────────\n * 侧栏字标是官方插件的矢量路径（182×24，无文字），无法用 CSS 改字；\n * 这里隐藏该 SVG，用伪元素写出铭荼自己的品牌块。首页标记同理换成\n * 薄荷青绿的“铭”字方章。两处都只影响外观，不改变插件注册的插槽。 */\n\n:root .dcu-brand > svg {\n  display: none;\n}\n\n:root .dcu-brand {\n  display: inline-flex;\n  align-items: center;\n  gap: 9px;\n}\n\n:root .dcu-brand::before {\n  content: \"\";\n  width: 12px;\n  height: 12px;\n  border-radius: 4px;\n  background: var(--mt-brand);\n  box-shadow: 0 0 0 3px color-mix(in srgb, var(--mt-brand) 20%, transparent);\n}\n\n:root .dcu-brand::after {\n  content: \"铭荼\";\n  font-family: \"Noto Sans CJK SC\", \"PingFang SC\", system-ui, sans-serif;\n  font-size: 19px;\n  font-weight: 760;\n  letter-spacing: 0.16em;\n  color: var(--mt-brand-ink);\n}\n\n:root [data-slot=\"conversation.hero.brand.mark\"] > svg {\n  display: none;\n}\n\n:root [data-slot=\"conversation.hero.brand.mark\"] {\n  display: inline-flex;\n  align-items: center;\n}\n\n:root [data-slot=\"conversation.hero.brand.mark\"]::after {\n  content: \"铭\";\n  display: grid;\n  place-items: center;\n  width: 36px;\n  height: 36px;\n  border-radius: 13px;\n  background: linear-gradient(\n    160deg,\n    var(--mt-brand) 0%,\n    var(--mt-brand-strong) 100%\n  );\n  color: #fff;\n  font-family: \"Noto Sans CJK SC\", \"PingFang SC\", system-ui, sans-serif;\n  font-size: 19px;\n  font-weight: 700;\n  box-shadow: var(--mt-shadow-soft);\n}\n\n/* 预设（模式）选择器：胶囊 + 薄荷底，弱化官方蓝 */\n:root [data-slot=\"conversation.hero.agentPreset\"] button {\n  border-radius: var(--mt-radius-pill);\n  background: var(--mt-brand-soft);\n  color: var(--mt-brand-ink);\n}\n\n/* ── 首页（新任务）铭荼化 ─────────────────────────────────────────\n * [data-phase=\"hero\"] 是宿主提供的稳定钩子，只在未开始会话的首页出现，\n * 因此以下替换不会影响会话页标题。 */\n:root [data-phase=\"hero\"] [class*=\"_titleGroup\"] > span {\n  display: none;\n}\n\n:root [data-phase=\"hero\"] [class*=\"_titleGroup\"]::after {\n  content: \"需要我帮你做什么？\";\n  font-family: \"Noto Sans CJK SC\", \"PingFang SC\", system-ui, sans-serif;\n  font-size: 34px;\n  font-weight: 730;\n  letter-spacing: 0.01em;\n  color: var(--mt-ink-strong);\n}\n\n/* 首页卡片：薄荷描边与渐隐底，区别于官方中性卡片 */\n:root .dcu-home-card {\n  border-color: color-mix(in srgb, var(--mt-brand) 26%, transparent);\n  background: linear-gradient(\n    150deg,\n    color-mix(in srgb, var(--mt-brand) 10%, transparent) 0%,\n    transparent 55%\n  );\n}\n\n:root .dcu-home-card:hover {\n  border-color: var(--mt-brand);\n  box-shadow: var(--mt-shadow-soft);\n}\n\n/* ── 思考强度滑块（plugin-effort-slider）───────────────────────────\n * 该插件自述“所有颜色解析自 DSH 变量，accent 唯一来源是\n * var(--dsw-alias-button-info-fill)（发送按钮同款）”，因此这里只把这一个\n * 变量在根部与滑块作用域内指向铭荼品牌色：滑杆填充、刻度、最高档渐变与\n * 选中勾选会整体跟随。不用 !important。 */\n:root {\n  --dsw-alias-button-info-fill: var(--mt-brand);\n}\n\n:root .efs-root {\n  --dsw-alias-button-info-fill: var(--mt-brand);\n}\n\n/* 滑块胶囊与发送按钮统一成铭荼胶囊 */\n:root .efs-slider,\n:root .efs-track,\n:root .efs-trackFill,\n:root .efs-thumb,\n:root .efs-dot {\n  border-radius: var(--mt-radius-pill);\n}\n\n/* 收窄滑块胶囊：插件默认固定 208px，装上“专家”等控件后会把 composer\n * 底部挤成两行（实测行高 77px → 收窄后 44px 恢复单行）。模型名过长时\n * 由插件自带的省略号逻辑截断。 */\n:root .efs-root {\n  width: 168px;\n  min-width: 168px;\n  max-width: 168px;\n}\n\n/* ── 第三方插件界面统一：antd ─────────────────────────────────────\n * 技能 / 归档（以及任何用 antd 的插件）走的是 antd v5 的 CSS 变量模式，\n * 实测其 token 挂在 `css-var-*` 包装元素上（如 `--ant-color-primary: #7aaaff`、\n * `--ant-border-radius: 6px`）。所以这里只覆盖 token，不去改 antd 的类选择器：\n * 按钮、开关、下拉、日期选择、标签、折叠面板会一并跟随，且插件升级不易碎。\n * 只改主色与圆角，不动 antd 的文本/背景色，保证深色与浅色两套都成立。 */\n:root [class*=\"css-var-\"] {\n  --ant-color-primary: var(--mt-brand);\n  --ant-color-primary-hover: var(--mt-brand-strong);\n  --ant-color-primary-active: var(--mt-brand-strong);\n  --ant-color-primary-bg: var(--mt-brand-soft);\n  --ant-color-primary-bg-hover: var(--mt-brand-soft);\n  --ant-color-primary-border: color-mix(in srgb, var(--mt-brand) 40%, transparent);\n  --ant-border-radius: var(--mt-radius-sm);\n  --ant-border-radius-xs: 10px;\n  --ant-border-radius-sm: var(--mt-radius-sm);\n  --ant-border-radius-lg: var(--mt-radius-md);\n}\n\n/* ── 质感细节 ───────────────────────────────────────────────────── */\n\n/* 键盘焦点：铭荼薄荷环，替代浏览器默认描边（可访问性与质感同时受益） */\n:root :focus-visible {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 60%, transparent);\n  outline-offset: 2px;\n  border-radius: var(--mt-radius-sm);\n}\n\n/* 文本选中：薄荷底 */\n:root ::selection {\n  background: color-mix(in srgb, var(--mt-brand) 28%, transparent);\n}\n\n/* 滚动条：跟随品牌，细而低干扰（官方 scrollbar.css 在 body 上绑定，故一并作用） */\n:root,\n:root body {\n  --dsh-scrollbar-thumb: color-mix(in srgb, var(--mt-brand) 28%, transparent);\n  --dsh-scrollbar-thumb-hover: color-mix(in srgb, var(--mt-brand) 50%, transparent);\n  --dsh-scrollbar-width: 6px;\n}\n\n/* 交互过渡：统一时长与缓动（180ms / 标准减速曲线），只作用于颜色与阴影 */\n:root [class*=\"_button\"],\n:root [class*=\"_card\"],\n:root .ant-btn,\n:root .ant-segmented-item,\n:root .ant-switch {\n  transition:\n    background-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    border-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    box-shadow 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n/* 悬停抬升：卡片类元素轻微上浮，给出可点击的质感 */\n:root .dcu-home-card {\n  transition:\n    transform 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    border-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    box-shadow 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root .dcu-home-card:hover {\n  transform: translateY(-1px);\n}\n\n/* 尊重系统的“减少动态效果” */\n@media (prefers-reduced-motion: reduce) {\n  :root .dcu-home-card:hover {\n    transform: none;\n  }\n}\n\n/* antd 分段控件（技能/归档页的标签条）的轨道与滑块 */\n:root .ant-segmented,\n:root .ant-segmented-thumb,\n:root .ant-segmented-item {\n  border-radius: var(--mt-radius-sm);\n}\n\n/* codex-pet 浮窗：给一层贴合轮廓的柔和投影，让它像浮在场景之上而不是硬贴片。\n   drop-shadow 跟随精灵图的 alpha 通道，不会给透明区域加方框。 */\n:root .dcp-floating {\n  filter: drop-shadow(0 8px 20px rgb(0 0 0 / 26%));\n}\n\n/* ── 侧栏页脚一行：账户 · 连接手机 · 剩余用量 · 设置 ─────────────────\n * 由 ui-tweaks.js 注入到 `.dcu-footer-actions`（该容器默认空且高度为 0）。\n * 把 `.dcu-foot` 改成横向排列，让注入项与「设置」并排在同一行。 */\n:root .dcu-foot {\n  display: flex;\n  align-items: center;\n  gap: 4px;\n  padding: 0 2px 2px;\n}\n\n:root .dcu-footer-actions {\n  display: flex;\n  align-items: center;\n  gap: 4px;\n  padding: 0;\n  flex: 0 0 auto;\n}\n\n/* 设置入口（官方 .dcu-settings-seat）必须保留自己的宽度：\n * 之前用 flex:1 1 auto + min-width:0，被相邻的页脚操作区挤成 0 宽 ⇒ 齿轮只剩 8px（用户报「设置不见了」）。 */\n:root .dcu-settings-seat {\n  flex: 0 0 auto;\n  width: 32px;\n  min-width: 32px;\n}\n\n:root .mt-foot-action {\n  display: inline-flex;\n  align-items: center;\n  justify-content: center;\n  gap: 5px;\n  height: 30px;\n  padding: 0 8px;\n  border: 0;\n  border-radius: var(--mt-radius-sm);\n  color: var(--dcu-sidebar-secondary, currentColor);\n  background: transparent;\n  font: inherit;\n  font-size: 11px;\n  white-space: nowrap;\n  cursor: pointer;\n  transition:\n    background-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    color 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root .mt-foot-action:hover {\n  background: var(--dcu-sidebar-hover, rgb(0 0 0 / 6%));\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-foot-action:focus-visible {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 60%, transparent);\n  outline-offset: 1px;\n}\n\n/* 图标型入口（连接手机 / 剩余用量）只占一个方按钮 */\n:root .mt-foot-action--icon {\n  width: 30px;\n  padding: 0;\n}\n\n/* 剩余用量圆环：环（conic + 径向遮罩）与中心数值分两层，\n * 遮罩只作用于环层，否则中心文字会被一起遮掉。无真实数据源时显示「—」，\n * 不编造百分比；接入用量后把 ui-tweaks.js 的 MING_TEA_QUOTA_PERCENT 接上即可。 */\n:root .mt-quota-ring {\n  --mt-quota: 0;\n  position: relative;\n  display: inline-grid;\n  place-items: center;\n  width: 24px;\n  height: 24px;\n}\n\n:root .mt-quota-arc {\n  position: absolute;\n  inset: 0;\n  border-radius: 50%;\n  background: conic-gradient(\n    var(--mt-brand) calc(var(--mt-quota) * 1%),\n    color-mix(in srgb, var(--mt-brand) 24%, transparent) 0\n  );\n  -webkit-mask: radial-gradient(circle, transparent 0 58%, #000 59%);\n  mask: radial-gradient(circle, transparent 0 58%, #000 59%);\n}\n\n:root .mt-quota-value {\n  position: relative;\n  font-size: 9px;\n  line-height: 1;\n  color: var(--mt-brand-ink);\n}\n\n/* 已有真实数值时把环加粗、数值放大一点 */\n:root .mt-foot-quota[data-has-value=\"1\"] .mt-quota-arc {\n  -webkit-mask: radial-gradient(circle, transparent 0 52%, #000 53%);\n  mask: radial-gradient(circle, transparent 0 52%, #000 53%);\n}\n\n:root .mt-foot-quota[data-has-value=\"1\"] .mt-quota-value {\n  font-size: 8px;\n}\n\n/* 设置页导航追加项：跟随导航项的排布 */\n:root .mt-settings-update {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n}\n\n/* 提示气泡：说明\"尚未接入\"，避免空点击 */\n:root .mt-foot-hint {\n  position: fixed;\n  z-index: 2147483001;\n  max-width: 220px;\n  padding: 8px 10px;\n  border-radius: var(--mt-radius-sm);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 30%, transparent);\n  background: var(--mt-paper-raised);\n  color: var(--mt-ink-strong);\n  font-size: 12px;\n  line-height: 1.45;\n  box-shadow: var(--mt-shadow-lift);\n  opacity: 0;\n  transform: translate(-50%, -100%) translateY(4px);\n  transition:\n    opacity 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    transform 180ms cubic-bezier(0.22, 1, 0.36, 1);\n  pointer-events: none;\n}\n\n:root .mt-foot-hint[data-visible=\"1\"] {\n  opacity: 1;\n  transform: translate(-50%, -100%) translateY(0);\n}\n\n/* codex-ui 自绘主按钮（类名形如 `<hash>_primary`，如插件配置页的「添加插件」、\n * composer 的发送键）与 antd 主按钮统一成品牌色。限定 button 角色，\n * 避免命中只是带 primary 字样的文字节点；排除 disabled 以免看起来可点。 */\n:root button[class*=\"_primary\"]:not(:disabled),\n:root [role=\"button\"][class*=\"_primary\"]:not([aria-disabled=\"true\"]) {\n  background: var(--mt-brand);\n  border-color: var(--mt-brand);\n  color: #fff;\n}\n\n:root button[class*=\"_primary\"]:not(:disabled):hover,\n:root [role=\"button\"][class*=\"_primary\"]:not([aria-disabled=\"true\"]):hover {\n  background: var(--mt-brand-strong);\n  border-color: var(--mt-brand-strong);\n}\n\n/* ── 清亮模式开关 ──────────────────────────────────────────────────\n * 开关行由 ui-tweaks.js 克隆真实设置行生成，按钮是本插件自绘（薄荷）。\n * 状态存浏览器的 localStorage，不做假的服务端同步。 */\n:root .mt-switch {\n  position: relative;\n  flex: 0 0 auto;\n  width: 40px;\n  height: 22px;\n  padding: 0;\n  border: 0;\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, currentColor 20%, transparent);\n  cursor: pointer;\n  transition: background-color 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root .mt-switch[data-on=\"1\"] {\n  background: var(--mt-brand);\n}\n\n:root .mt-switch:focus-visible {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 60%, transparent);\n  outline-offset: 2px;\n}\n\n:root .mt-switch-knob {\n  position: absolute;\n  top: 3px;\n  left: 3px;\n  width: 16px;\n  height: 16px;\n  border-radius: 50%;\n  background: #fff;\n  box-shadow: 0 1px 3px rgb(0 0 0 / 24%);\n  transition: transform 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root .mt-switch[data-on=\"1\"] .mt-switch-knob {\n  transform: translateX(18px);\n}\n\n/* ── 清亮模式：减少动画与渲染负担 ──────────────────────────────────\n * 只做减法：停掉过渡与动画、去掉本主题引入的阴影与滤镜。\n * 低配置机器上这几项是主要的合成开销来源。 */\n:root[data-ming-tea-lite=\"1\"] {\n  --mt-shadow-soft: none;\n  --mt-shadow-lift: none;\n}\n\n:root[data-ming-tea-lite=\"1\"] *,\n:root[data-ming-tea-lite=\"1\"] *::before,\n:root[data-ming-tea-lite=\"1\"] *::after {\n  animation: none !important;\n  transition: none !important;\n}\n\n:root[data-ming-tea-lite=\"1\"] .dcp-floating {\n  filter: none !important;\n}\n\n:root[data-ming-tea-lite=\"1\"] [class*=\"_dialog\"],\n:root[data-ming-tea-lite=\"1\"] [class*=\"_menu\"],\n:root[data-ming-tea-lite=\"1\"] [class*=\"_popover\"],\n:root[data-ming-tea-lite=\"1\"] [data-composer-card] {\n  box-shadow: none !important;\n}\n\n:root[data-ming-tea-lite=\"1\"] .mt-quota-arc {\n  background: none;\n  border: 2px solid color-mix(in srgb, var(--mt-brand) 40%, transparent);\n}\n\n/* ── 官方写死材质的修正 ───────────────────────────────────────────\n * 这些是官方在组件类上写死、无法靠令牌覆盖的地方（有源码证据）。\n * 类名按\"哈希_语义名\"的后缀匹配，语义名稳定、哈希随构建变化。 */\n\n/* 1) HoverCard：官方注释写明\"表面两套主题都用 #2C2C2E（figma 值）\"，\n *    文字是写死的 #FFFFFF。浅色下是一块突兀的深卡。\n *    这里同时改表面变量与文字色——对其他卡片等价（我们的\n *    --mt-ink-strong 与已覆盖的 --dsw-alias-label-primary 同值），\n *    因此安全：只有 HoverCard 会真正改变观感。 */\n:root [class*=\"_card\"] {\n  --dsw-hovercard-bg: var(--mt-paper-raised);\n  color: var(--mt-ink-strong);\n}\n\n/* 2) macOS 侧栏：官方用蓝紫渐变（#7a9bf0 / #8f89b8），是\"官方味\"最重的一处，\n *    换成铭荼薄荷的同结构渐变。 */\n:root[data-platform=\"darwin\"] [class*=\"sidebarCol\"] {\n  background: linear-gradient(\n    to bottom,\n    color-mix(in srgb, var(--mt-brand) 10%, transparent),\n    color-mix(in srgb, var(--mt-brand) 3%, transparent) 40%,\n    transparent\n  );\n}\n\n:root[data-platform=\"darwin\"] [data-ds-dark-theme] [class*=\"sidebarCol\"] {\n  background: linear-gradient(\n    to bottom,\n    color-mix(in srgb, var(--mt-brand) 12%, transparent),\n    color-mix(in srgb, var(--mt-brand) 4%, transparent) 35%,\n    transparent 68%\n  );\n}\n\n/* 3) Markdown 表格与行内码：官方分别是 8px 与 6px，抬到我们的尺度 */\n:root [class*=\"_tableScroll\"],\n:root [class*=\"_tableFill\"] {\n  border-radius: var(--mt-radius-sm);\n}\n\n:root [class*=\"_markdown\"] code {\n  border-radius: var(--mt-radius-sm);\n}\n\n/* ── 首页场景卡（替换官方四张开发向卡片）──────────────────────────\n * 官方卡片块本身隐藏，位置由 ui-tweaks.js 注入的 .mt-scene-cards 接管。\n * 视觉语言：纸面卡 + 薄荷描边 + 悬停上浮，与设置页卡片同一套。 */\n:root .dcu-home-suggestions {\n  display: none;\n}\n\n:root .mt-scene-cards {\n  display: grid;\n  grid-template-columns: repeat(3, minmax(0, 1fr));\n  gap: 12px;\n  width: 100%;\n  max-width: 720px;\n  margin: 26px auto 0;\n}\n\n@media (max-width: 900px) {\n  :root .mt-scene-cards {\n    grid-template-columns: 1fr;\n  }\n}\n\n:root .mt-scene-column {\n  display: grid;\n  align-content: start;\n  gap: 8px;\n}\n\n:root .mt-scene-title {\n  color: var(--mt-brand-ink);\n  font-size: 11px;\n  font-weight: 650;\n  letter-spacing: 0.08em;\n}\n\n:root .mt-scene-card {\n  display: block;\n  width: 100%;\n  padding: 12px 14px;\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 22%, transparent);\n  border-radius: var(--mt-radius-md);\n  background: var(--mt-paper-raised);\n  color: var(--mt-ink-strong);\n  font: inherit;\n  font-size: 13px;\n  text-align: left;\n  cursor: pointer;\n  transition:\n    transform 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    border-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    box-shadow 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root .mt-scene-card:hover {\n  transform: translateY(-1px);\n  border-color: var(--mt-brand);\n  box-shadow: var(--mt-shadow-soft);\n}\n\n:root .mt-scene-card:focus-visible {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 60%, transparent);\n  outline-offset: 2px;\n}\n\n@media (prefers-reduced-motion: reduce) {\n  :root .mt-scene-card:hover {\n    transform: none;\n  }\n}\n\n/* ── 保护：胶囊与圆形不被上面的通用规则改形 ─────────────────────── */\n:root [class*=\"_pill\"],\n:root [class*=\"pill\"],\n:root [class*=\"_circle\"] {\n  border-radius: var(--mt-radius-pill);\n}\n\n\n/* ══ 侧栏页脚版式：账户（头像 + 名称）→ 剩余用量 → 连接手机 → 设置 ══════════\n * 顺序用 order 排，因为官方「设置」在它自己的 slot 容器里（DOM 顺序排不了）。\n * 账户 margin-right:auto 把它自己顶到最左，其余入口靠右。 */\n:root .dcu-footer-actions {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  flex: 1 1 auto;\n  min-width: 0;\n}\n\n:root .dcu-footer-actions > [data-slot=\"sidebar.footer.action\"] {\n  order: 4;\n}\n\n:root .mt-foot-account {\n  order: 1;\n  margin-right: auto;\n  gap: 7px;\n  height: 32px;\n  max-width: 100%;\n  min-width: 0;\n  padding: 0 8px 0 3px;\n}\n\n:root .mt-foot-quota {\n  order: 2;\n}\n\n:root [data-ming-tea-action=\"phone\"] {\n  order: 3;\n}\n\n:root .mt-foot-avatar {\n  display: inline-flex;\n  align-items: center;\n  justify-content: center;\n  flex: 0 0 auto;\n  width: 22px;\n  height: 22px;\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, var(--mt-brand) 18%, transparent);\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-foot-name {\n  font-weight: 600;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n/* 收起侧栏（.dcu-compact）：轨道只有 36px 宽，必须竖排 + 只留图标，\n * 否则宽按钮会被 overflow:hidden 的根容器裁掉一半（用户报的「只显示一半的账户」）。\n * 官方「设置」在自己的 .dcu-settings-seat 里，横向挤压时会被压成 0 宽而整块消失，\n * 所以这一档要连它也一起改成定宽竖排。 */\n:root .dcu-compact .dcu-foot {\n  flex-direction: column;\n  align-items: center;\n  gap: 4px;\n  height: auto;\n}\n\n:root .dcu-compact .dcu-footer-actions {\n  flex-direction: column;\n  align-items: center;\n  gap: 4px;\n  flex: 0 0 auto;\n  width: 32px;\n  min-width: 32px;\n}\n\n:root .dcu-compact .dcu-settings-seat {\n  flex: 0 0 auto;\n  width: 32px;\n  min-width: 32px;\n  overflow: visible;\n}\n\n:root .dcu-compact .dcu-settings-seat > *,\n:root .dcu-compact .dcu-settings-trigger {\n  width: 32px;\n  min-width: 32px;\n  height: 32px;\n}\n\n:root .dcu-compact .mt-foot-action,\n:root .dcu-compact .mt-foot-account {\n  width: 32px;\n  min-width: 0;\n  max-width: 32px;\n  padding: 0;\n  margin-right: 0;\n  justify-content: center;\n}\n\n:root .dcu-compact .mt-foot-name,\n:root .dcu-compact .mt-foot-label {\n  display: none;\n}\n\n/* ══ 输入框旁的「上下文占用」指示器 ══════════════════════════════════════ */\n:root .mt-ctx-meter {\n  display: inline-flex;\n  align-items: center;\n  align-self: center;\n  gap: 6px;\n  width: fit-content;\n  flex: 0 0 auto;\n  margin: 0 6px 0 0;\n  padding: 3px 9px 3px 4px;\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, var(--mt-brand) 7%, transparent);\n  color: color-mix(in srgb, var(--mt-ink-strong) 72%, transparent);\n  font-size: 11px;\n  line-height: 1.4;\n  cursor: default;\n  user-select: none;\n}\n\n:root .mt-ctx-ring {\n  position: relative;\n  display: inline-grid;\n  place-items: center;\n  width: 16px;\n  height: 16px;\n}\n\n:root .mt-ctx-arc {\n  position: absolute;\n  inset: 0;\n  border-radius: 50%;\n  background: conic-gradient(\n    var(--mt-brand) calc(var(--mt-ctx, 0) * 1%),\n    color-mix(in srgb, var(--mt-brand) 20%, transparent) 0\n  );\n  -webkit-mask: radial-gradient(circle, transparent 0 56%, #000 57%);\n  mask: radial-gradient(circle, transparent 0 56%, #000 57%);\n}\n\n:root .mt-ctx-meter[data-has-value=\"1\"] {\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-ctx-meter[data-has-value=\"0\"] .mt-ctx-arc {\n  background: color-mix(in srgb, var(--mt-brand) 18%, transparent);\n}\n\n/* ══ 商店（dsh-plugin-shop）：套上铭荼的卡片/按钮/徽章语言 ═══════════════\n * 只挂在商店自己的 [data-shop-tab] 面板内，避免波及其他插件的同名类；\n * 用 data-* 与语义后缀匹配，不依赖会随构建变化的 hash 前缀。\n * 厂商样式表后注入，所以这里统一加 :root 前缀提高特异度，保证不被压回去。 */\n:root [data-shop-tab] {\n  gap: 10px;\n}\n\n:root [data-shop-tab] [class*=\"_searchInput\"] {\n  height: 34px;\n  padding: 0 14px;\n  border-radius: var(--mt-radius-pill);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 18%, transparent);\n  background: var(--mt-paper-raised);\n}\n\n:root [data-shop-tab] [class*=\"_searchInput\"]:focus {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 45%, transparent);\n  outline-offset: 1px;\n}\n\n:root [data-shop-tab] [class*=\"_categoryButton\"] {\n  height: 28px;\n  padding: 0 11px;\n  border-radius: var(--mt-radius-pill);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 16%, transparent);\n  background: transparent;\n  color: color-mix(in srgb, var(--mt-ink-strong) 78%, transparent);\n  font-size: 12px;\n  transition: background-color 160ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root [data-shop-tab] [class*=\"_categoryButton\"]:hover {\n  background: color-mix(in srgb, var(--mt-brand) 10%, transparent);\n}\n\n:root [data-shop-tab] [class*=\"_categoryButtonOn\"] {\n  background: var(--mt-brand);\n  border-color: transparent;\n  color: #fff;\n  font-weight: 600;\n}\n\n/* 卡片：圆角一档更大、悬停轻微上浮，和首页场景卡同一手感 */\n:root [data-shop-tab] [data-shop-entry] {\n  border-radius: var(--mt-radius-lg);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 14%, transparent);\n  background: var(--mt-paper-raised);\n  transition:\n    border-color 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    box-shadow 180ms cubic-bezier(0.22, 1, 0.36, 1),\n    transform 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root [data-shop-tab] [data-shop-entry]:hover {\n  border-color: color-mix(in srgb, var(--mt-brand) 32%, transparent);\n  box-shadow: var(--mt-shadow-lift);\n}\n\n@media (prefers-reduced-motion: no-preference) {\n  :root [data-shop-tab] [data-shop-entry]:hover {\n    transform: translateY(-1px);\n  }\n}\n\n:root [data-shop-tab] [class*=\"_cardSpine\"] {\n  background: linear-gradient(\n    180deg,\n    var(--mt-brand),\n    color-mix(in srgb, var(--mt-brand) 35%, transparent)\n  );\n}\n\n/* 徽章统一成胶囊形 + 品牌浅底 */\n:root [data-shop-tab] [class*=\"_categoryBadge\"],\n:root [data-shop-tab] [class*=\"_sourceBadge\"],\n:root [data-shop-tab] [class*=\"_cardVersion\"],\n:root [data-shop-tab] [class*=\"_tierBadge\"],\n:root [data-shop-tab] [class*=\"_starsBadge\"] {\n  padding: 1px 8px;\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, var(--mt-brand) 10%, transparent);\n  color: var(--mt-brand-ink);\n  font-size: 11px;\n}\n\n/* 安装 / 更新：品牌实心主按钮 */\n:root [data-shop-tab] [data-shop-install],\n:root [data-shop-tab] [data-shop-update] {\n  height: 30px;\n  padding: 0 15px;\n  border: 0;\n  border-radius: var(--mt-radius-pill);\n  background: var(--mt-brand);\n  color: #fff;\n  font-size: 12px;\n  font-weight: 600;\n  cursor: pointer;\n  transition: background-color 160ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n:root [data-shop-tab] [data-shop-install]:hover:not(:disabled),\n:root [data-shop-tab] [data-shop-update]:hover:not(:disabled) {\n  background: var(--mt-brand-strong);\n}\n\n:root [data-shop-tab] [data-shop-refused],\n:root [data-shop-tab] [data-shop-install]:disabled,\n:root [data-shop-tab] [data-shop-update]:disabled {\n  background: color-mix(in srgb, var(--mt-ink-strong) 12%, transparent);\n  color: color-mix(in srgb, var(--mt-ink-strong) 55%, transparent);\n  cursor: not-allowed;\n}\n\n/* 卸载 / 刷新 / 检查更新：次级按钮 */\n:root [data-shop-tab] [data-shop-uninstall],\n:root [data-shop-tab] [class*=\"_catalogRefreshButton\"],\n:root [data-shop-tab] [class*=\"_checkUpdateButton\"],\n:root [data-shop-tab] [class*=\"_actionButton\"] {\n  height: 28px;\n  padding: 0 12px;\n  border-radius: var(--mt-radius-pill);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 22%, transparent);\n  background: transparent;\n  color: var(--mt-brand-ink);\n  font-size: 12px;\n  cursor: pointer;\n}\n\n:root [data-shop-tab] [data-shop-confirm] {\n  height: 28px;\n  padding: 0 13px;\n  border: 0;\n  border-radius: var(--mt-radius-pill);\n  background: var(--mt-brand);\n  color: #fff;\n  font-size: 12px;\n  font-weight: 600;\n  cursor: pointer;\n}\n\n/* 开关：品牌色 + 胶囊滑轨 */\n:root [data-shop-tab] [class*=\"_switch\"] {\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, var(--mt-ink-strong) 16%, transparent);\n}\n\n:root [data-shop-tab] [class*=\"_switchOn\"] {\n  background: var(--mt-brand);\n}\n\n:root [data-shop-tab] [class*=\"_switchKnob\"] {\n  box-shadow: 0 1px 3px rgb(23 51 58 / 22%);\n}\n\n/* ══ 安装前确认弹窗（我们自己画的，不依赖商店的类名） ══════════════════ */\n.mt-store-confirm {\n  position: fixed;\n  inset: 0;\n  z-index: 2147483002;\n  display: grid;\n  place-items: center;\n  opacity: 0;\n  transition: opacity 140ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n.mt-store-confirm[data-open=\"1\"] {\n  opacity: 1;\n}\n\n.mt-store-confirm-backdrop {\n  position: absolute;\n  inset: 0;\n  background: color-mix(in srgb, #0b1f24 42%, transparent);\n}\n\n.mt-store-confirm-panel,\n.mt-account-panel {\n  position: relative;\n  width: min(420px, calc(100vw - 40px));\n  padding: 18px 20px 16px;\n  border-radius: var(--mt-radius-lg);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 24%, transparent);\n  background: var(--mt-paper-raised);\n  color: var(--mt-ink-strong);\n  box-shadow: var(--mt-shadow-lift);\n}\n\n.mt-store-confirm-panel h3 {\n  margin: 0 0 4px;\n  font-size: 15px;\n  font-weight: 600;\n}\n\n.mt-store-confirm-name {\n  margin: 0 0 12px;\n  font-size: 13px;\n  font-weight: 600;\n  color: var(--mt-brand-ink);\n  overflow-wrap: anywhere;\n}\n\n.mt-store-confirm-name span {\n  font-weight: 400;\n  color: color-mix(in srgb, var(--mt-ink-strong) 60%, transparent);\n}\n\n.mt-store-confirm-facts {\n  display: grid;\n  gap: 4px;\n  margin: 0 0 12px;\n  font-size: 12px;\n}\n\n.mt-store-row {\n  display: grid;\n  grid-template-columns: 62px 1fr;\n  gap: 8px;\n}\n\n.mt-store-row dt {\n  color: color-mix(in srgb, var(--mt-ink-strong) 58%, transparent);\n}\n\n.mt-store-row dd {\n  margin: 0;\n  overflow-wrap: anywhere;\n}\n\n.mt-store-row code {\n  font-size: 11px;\n  padding: 1px 5px;\n  border-radius: 6px;\n  background: color-mix(in srgb, var(--mt-brand) 10%, transparent);\n}\n\n.mt-store-confirm-note {\n  margin: 0 0 14px;\n  padding: 8px 10px;\n  border-radius: var(--mt-radius-sm);\n  background: color-mix(in srgb, var(--mt-brand) 7%, transparent);\n  color: color-mix(in srgb, var(--mt-ink-strong) 76%, transparent);\n  font-size: 11px;\n  line-height: 1.55;\n}\n\n.mt-store-confirm-actions {\n  display: flex;\n  justify-content: flex-end;\n  gap: 8px;\n}\n\n.mt-store-cancel,\n.mt-store-ok {\n  height: 32px;\n  padding: 0 16px;\n  border-radius: var(--mt-radius-pill);\n  font-size: 13px;\n  cursor: pointer;\n}\n\n.mt-store-cancel {\n  border: 1px solid color-mix(in srgb, var(--mt-ink-strong) 18%, transparent);\n  background: transparent;\n  color: var(--mt-ink-strong);\n}\n\n.mt-store-ok {\n  border: 0;\n  background: var(--mt-brand);\n  color: #fff;\n  font-weight: 600;\n}\n\n.mt-store-cancel:focus-visible,\n.mt-store-ok:focus-visible {\n  outline: 2px solid color-mix(in srgb, var(--mt-brand) 60%, transparent);\n  outline-offset: 2px;\n}\n\n/* ── 账户面板 ─────────────────────────────────────────────────────────── */\n:root .mt-account-body {\n  margin: 0 0 10px;\n  font-size: 12px;\n}\n\n:root .mt-account-name {\n  margin: 0 0 4px;\n  font-size: 14px;\n  font-weight: 600;\n  color: var(--mt-brand-ink);\n  overflow-wrap: anywhere;\n}\n\n:root .mt-account-meta {\n  margin: 0 0 6px;\n  line-height: 1.6;\n  color: color-mix(in srgb, var(--mt-ink-strong) 72%, transparent);\n}\n\n:root .mt-account-code {\n  margin: 0 0 8px;\n  padding: 10px 14px;\n  border-radius: var(--mt-radius-sm);\n  background: color-mix(in srgb, var(--mt-brand) 10%, transparent);\n  color: var(--mt-brand-ink);\n  font-family: var(--ds-font-family-code, ui-monospace, monospace);\n  font-size: 22px;\n  font-weight: 600;\n  letter-spacing: 3px;\n  text-align: center;\n  user-select: all;\n}\n\n:root .mt-account-link {\n  margin: 0 0 8px;\n  font-size: 11px;\n  line-height: 1.5;\n  color: var(--mt-brand-ink);\n  overflow-wrap: anywhere;\n  user-select: all;\n}\n\n:root .mt-account-note {\n  margin: 0;\n  min-height: 16px;\n  font-size: 11px;\n  line-height: 1.5;\n  color: color-mix(in srgb, var(--mt-ink-strong) 62%, transparent);\n}\n\n:root .mt-account-note[data-tone=\"error\"] {\n  color: var(--dsw-alias-label-error, #c62828);\n}\n\n:root .mt-account-note[data-tone=\"ok\"] {\n  color: var(--mt-brand-ink);\n}\n\n/* 气泡里带链接（例如「前往下载」）：这一档需要能点，其余仍是纯提示 */\n:root .mt-foot-hint[data-interactive=\"1\"] {\n  pointer-events: auto;\n}\n\n:root .mt-foot-hint a {\n  color: var(--mt-brand-ink);\n  font-weight: 600;\n  text-decoration: underline;\n}\n\n/* 手动授权码：只在需要手动打开时才用得上，视觉上弱化 */\n:root .mt-account-fallback {\n  margin: 0 0 6px;\n  font-size: 11px;\n  color: color-mix(in srgb, var(--mt-ink-strong) 52%, transparent);\n  user-select: all;\n}\n\n/* ── 用量悬浮卡（免费层 + 付费层同时显示） ─────────────────────────────── */\n:root .mt-quota-tip {\n  position: fixed;\n  z-index: 2147483001;\n  max-width: 260px;\n  padding: 8px 10px;\n  border-radius: var(--mt-radius-sm);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 30%, transparent);\n  background: var(--mt-paper-raised);\n  color: color-mix(in srgb, var(--mt-ink-strong) 80%, transparent);\n  font-size: 11px;\n  line-height: 1.6;\n  box-shadow: var(--mt-shadow-lift);\n  opacity: 0;\n  transform: translate(-50%, -100%) translateY(4px);\n  transition:\n    opacity 140ms cubic-bezier(0.22, 1, 0.36, 1),\n    transform 140ms cubic-bezier(0.22, 1, 0.36, 1);\n  pointer-events: none;\n  white-space: pre-line;\n}\n\n:root .mt-quota-tip[data-visible=\"1\"] {\n  opacity: 1;\n  transform: translate(-50%, -100%) translateY(0);\n}\n\n:root .mt-quota-tip-strong {\n  color: var(--mt-ink-strong);\n  font-weight: 600;\n  margin-bottom: 2px;\n}\n\n/* ── 侧栏用户名旁的档位徽标（仅付费档出现） ───────────────────────────── */\n:root .mt-foot-badge {\n  flex: 0 0 auto;\n  padding: 1px 6px;\n  border-radius: var(--mt-radius-pill);\n  background: linear-gradient(135deg, var(--mt-brand), var(--mt-brand-strong));\n  color: #fff;\n  font-size: 9px;\n  font-weight: 700;\n  letter-spacing: 0.4px;\n  line-height: 1.5;\n}\n\n:root .mt-foot-badge[data-plan=\"pro\"] {\n  background: linear-gradient(135deg, #2f6fdd, #4a8bf0);\n}\n\n:root .mt-foot-badge[data-plan=\"ultra\"] {\n  background: linear-gradient(135deg, #b8860b, #d9a520);\n}\n\n/* 收起侧栏时徽标会挤占轨道，隐藏它（展开后仍在） */\n:root .dcu-compact .mt-foot-badge {\n  display: none;\n}\n\n/* ── 「检查更新」独立页面 ─────────────────────────────────────────────── */\n:root .mt-upd-page {\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n  padding: 4px 0 8px;\n  max-width: 640px;\n}\n\n:root .mt-upd-title {\n  margin: 0;\n  font-size: 16px;\n  font-weight: 600;\n  color: var(--mt-ink-strong);\n}\n\n:root .mt-upd-meta {\n  margin: 0;\n  font-size: 12px;\n  line-height: 1.6;\n  color: color-mix(in srgb, var(--mt-ink-strong) 66%, transparent);\n}\n\n:root .mt-upd-facts {\n  display: grid;\n  gap: 6px;\n  margin: 0;\n  padding: 12px 14px;\n  border-radius: var(--mt-radius-sm);\n  background: color-mix(in srgb, var(--mt-brand) 7%, transparent);\n  font-size: 12px;\n}\n\n:root .mt-upd-facts > div {\n  display: grid;\n  grid-template-columns: 92px 1fr;\n  gap: 10px;\n}\n\n:root .mt-upd-facts dt {\n  color: color-mix(in srgb, var(--mt-ink-strong) 58%, transparent);\n}\n\n:root .mt-upd-facts dd {\n  margin: 0;\n  overflow-wrap: anywhere;\n}\n\n:root .mt-upd-notes {\n  padding: 10px 14px;\n  border-radius: var(--mt-radius-sm);\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 20%, transparent);\n  font-size: 12px;\n  line-height: 1.65;\n}\n\n:root .mt-upd-notes h2 {\n  margin: 0 0 6px;\n  font-size: 13px;\n  font-weight: 600;\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-upd-notes p {\n  margin: 0;\n  white-space: pre-line;\n}\n\n:root .mt-upd-actions {\n  display: flex;\n  gap: 8px;\n}\n\n:root .mt-upd-primary,\n:root .mt-upd-secondary {\n  display: inline-flex;\n  align-items: center;\n  justify-content: center;\n  height: 30px;\n  padding: 0 14px;\n  border-radius: var(--mt-radius-pill);\n  font-size: 12px;\n  cursor: pointer;\n  text-decoration: none;\n}\n\n:root .mt-upd-primary {\n  border: 0;\n  background: var(--mt-brand);\n  color: #fff;\n  font-weight: 600;\n}\n\n:root .mt-upd-secondary {\n  border: 1px solid color-mix(in srgb, var(--mt-brand) 24%, transparent);\n  background: transparent;\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-upd-error {\n  margin: 0;\n  font-size: 12px;\n  color: var(--dsw-alias-label-error, #c62828);\n}\n\n/* ==== 设置页「用量看板」（.mt-usage-*）====\n   度量条与「检查更新」页共用 .mt-upd-page / .mt-upd-facts / .mt-upd-actions 的版式，\n   这里只加额度条自身的样式，避免两页各长一套。 */\n\n:root .mt-usage-heading {\n  margin: 0;\n  font-size: 13px;\n  font-weight: 600;\n  color: var(--mt-brand-ink);\n}\n\n:root .mt-usage-meter {\n  display: flex;\n  flex-direction: column;\n  gap: 6px;\n  padding: 12px 14px;\n  border-radius: var(--mt-radius-sm);\n  background: color-mix(in srgb, var(--mt-brand) 7%, transparent);\n}\n\n:root .mt-usage-meter-head {\n  display: flex;\n  align-items: baseline;\n  justify-content: space-between;\n  gap: 10px;\n  font-size: 12px;\n}\n\n:root .mt-usage-meter-label {\n  font-weight: 600;\n  color: var(--mt-ink-strong);\n}\n\n:root .mt-usage-meter-figure {\n  color: color-mix(in srgb, var(--mt-ink-strong) 72%, transparent);\n  font-variant-numeric: tabular-nums;\n}\n\n:root .mt-usage-bar {\n  height: 6px;\n  border-radius: var(--mt-radius-pill);\n  background: color-mix(in srgb, var(--mt-ink-strong) 12%, transparent);\n  overflow: hidden;\n}\n\n:root .mt-usage-bar-fill {\n  display: block;\n  height: 100%;\n  border-radius: inherit;\n  background: var(--mt-brand);\n  transition: width 180ms cubic-bezier(0.22, 1, 0.36, 1);\n}\n\n/* 站点没给总数时占比未知：不画一条假的满格，改成斜纹提示 */\n:root .mt-usage-bar-fill[data-unknown=\"1\"] {\n  width: 100% !important;\n  background: repeating-linear-gradient(\n    135deg,\n    color-mix(in srgb, var(--mt-ink-strong) 18%, transparent) 0 6px,\n    transparent 6px 12px\n  );\n}\n\n:root .mt-usage-note {\n  margin: 0;\n  font-size: 12px;\n  line-height: 1.6;\n  color: color-mix(in srgb, var(--mt-ink-strong) 60%, transparent);\n}\n\n/* ── summon 三态面板（快捷键呼出的助手）────────────────────────────\n * 产品要求的表现（2026-10-01 用户明确）：\n *   ① 按下快捷键 → **只出现一个宠物**，别的什么都没有；\n *   ② 用户说话 → 出现一个悬浮胶囊，显示语音转文字的内容；\n *   ③ 助手回答 → 出现在胶囊**下方**。\n *\n * 分工：窗口尺寸/位置/点击穿透由桌面壳按档位调整\n * （pet 200×200 / listening 420×280 / answer 470×520），页面只负责\n * 「藏起一切外壳、把宠物留在底部、把胶囊与回答叠在它上面」。\n *\n * 为什么用 visibility 而不是 display 隐藏对话区：宠物插件的浮层可能挂在对话区内部，\n * display:none 会让它一起消失；visibility 可以被后代用 visible 重新显形。\n * 对话区与输入框**必须保持挂载**——语音输入与发送按钮都在里面，我们只是看不见它们。 */\n\n:root[data-ming-tea-summon=\"1\"],\n:root[data-ming-tea-summon=\"1\"] body {\n  overflow: hidden;\n  background: transparent;\n}\n\n/* ① 社区外壳（侧栏 / 页脚 / 首页卡 / 工作区树 / 扩展与设置导航）全隐藏 */\n:root[data-ming-tea-summon=\"1\"] [class^=\"dcu-\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\" dcu-\"] {\n  display: none !important;\n}\n\n/* ② 官方骨架（侧栏列 / 右栏列 / 工作区树 / 页头）隐藏 */\n:root[data-ming-tea-summon=\"1\"] [class*=\"sidebarCol\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\"rightbarCol\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\"searchTree\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\"treeBody\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\"sectionHeader\"],\n:root[data-ming-tea-summon=\"1\"] [class*=\"headerSessionless\"] {\n  display: none !important;\n}\n\n/* ③ 对话正文：藏起来（我们用 textContent 镜像它的文本，所以 hidden 不影响复用） */\n:root[data-ming-tea-summon=\"1\"] [data-conversation-region=\"chat\"] {\n  visibility: hidden !important;\n  box-shadow: none !important;\n}\n\n/* ③b 输入区：**不能**用 visibility:hidden —— 实测语音行是懒挂载的，\n * 隐藏时它根本不挂载，于是「点一下说话」永远找不到麦克风按钮（2026-10-01 踩到）。\n * 改成「移到屏幕外 + 全透明」：React 认为它正常可见、照常挂载，用户也看不见它。\n * 注意：审批卡虽然也挂在这一带，但它自己有 position:fixed 规则，会钉回视口底部。 */\n:root[data-ming-tea-summon=\"1\"] [data-composer-seat],\n:root[data-ming-tea-summon=\"1\"] [data-composer-card] {\n  position: fixed !important;\n  left: -10000px !important;\n  top: 0 !important;\n  width: 820px !important;\n  max-width: none !important;\n  opacity: 0 !important;\n  visibility: visible !important;\n  pointer-events: none !important;\n  box-shadow: none !important;\n}\n\n/* ④ 宠物必须可见（哪怕它挂在被隐藏的对话区里），并且钉在面板底部居中。\n * 宠物插件的 left/top 是**内联**写死的（实测 200×200 下是 left:64px; top:38px），\n * 窗口一变它就会跑到左上角 —— 只有 !important 能盖过内联样式。 */\n:root[data-ming-tea-summon=\"1\"] [class*=\"dcp-\"] {\n  visibility: visible !important;\n}\n:root[data-ming-tea-summon=\"1\"] .dcp-floating,\n:root[data-ming-tea-summon=\"1\"] [class*=\"dcp-floating\"] {\n  left: 50% !important;\n  right: auto !important;\n  top: auto !important;\n  bottom: 14px !important;\n  transform: translateX(-50%) !important;\n}\n\n/* ⑤ 我们自己的层：贴底一列，胶囊在上、回答在下；底部留出宠物位置 */\n.mt-summon-layer {\n  position: fixed;\n  inset: 0;\n  display: none;\n  flex-direction: column;\n  justify-content: flex-end;\n  align-items: center;\n  gap: 8px;\n  padding: 10px 10px 158px; /* 底部留出宠物（约 130px）与边距 */\n  pointer-events: none;\n  z-index: 2147483000;\n}\n:root[data-ming-tea-summon=\"1\"] .mt-summon-layer {\n  display: flex;\n  /* 必须能收到点击：一是「点一下就开始说话」（WKWebView 要真实手势才开麦），\n   * 二是审批/权限条本身要可点。窗口在 summon 各档本来就是可交互的，这里不再穿透。 */\n  pointer-events: auto;\n  cursor: pointer;\n}\n\n/* 权限提示条：缺权限时才出现，点一下跳系统设置（⑦ 的可点击引导） */\n.mt-summon-permission {\n  pointer-events: auto;\n  padding: 6px 12px;\n  border-radius: 999px;\n  border: 1px solid color-mix(in srgb, #d08a1e 45%, transparent);\n  background: color-mix(in srgb, #fdf3e2 94%, transparent);\n  color: #7a4b06;\n  font-size: 12px;\n  line-height: 1.4;\n  cursor: pointer;\n}\n.mt-summon-permission[hidden] {\n  display: none;\n}\n\n/* 转录胶囊：只在听到话之后出现 */\n.mt-summon-capsule {\n  max-width: 100%;\n  padding: 8px 14px;\n  border-radius: 999px;\n  background: color-mix(in srgb, var(--mt-paper-raised, #f6faf8) 92%, transparent);\n  border: 1px solid color-mix(in srgb, var(--mt-brand, #16857d) 24%, transparent);\n  box-shadow: var(--mt-shadow-lift, 0 10px 24px rgba(12, 48, 44, 0.18));\n  font-size: 13px;\n  line-height: 1.5;\n  color: var(--mt-ink-strong, #10302c);\n  text-align: center;\n  overflow-wrap: anywhere;\n}\n.mt-summon-capsule[data-empty=\"1\"] {\n  color: color-mix(in srgb, var(--mt-ink-strong, #10302c) 55%, transparent);\n}\n\n/* 助手回答：胶囊**下方**，可滚动，不挡住宠物 */\n.mt-summon-answer {\n  max-width: 100%;\n  max-height: 300px;\n  overflow-y: auto;\n  padding: 10px 14px;\n  border-radius: var(--mt-radius-lg, 14px);\n  background: color-mix(in srgb, var(--mt-paper-raised, #f6faf8) 90%, transparent);\n  border: 1px solid color-mix(in srgb, var(--mt-brand, #16857d) 18%, transparent);\n  box-shadow: var(--mt-shadow-lift, 0 10px 24px rgba(12, 48, 44, 0.18));\n  font-size: 13px;\n  line-height: 1.65;\n  color: var(--mt-ink-strong, #10302c);\n  white-space: pre-wrap;\n  overflow-wrap: anywhere;\n  pointer-events: auto;\n}\n\n/* 档位规则：宠物档什么都没有；听音档只有胶囊；回答档两件都有 */\n:root[data-ming-tea-summon=\"1\"]:not([data-ming-tea-summon-stage=\"listening\"]):not([data-ming-tea-summon-stage=\"answer\"]) .mt-summon-capsule,\n:root[data-ming-tea-summon=\"1\"]:not([data-ming-tea-summon-stage=\"listening\"]):not([data-ming-tea-summon-stage=\"answer\"]) .mt-summon-answer {\n  display: none;\n}\n:root[data-ming-tea-summon=\"1\"][data-ming-tea-summon-stage=\"listening\"] .mt-summon-answer {\n  display: none;\n}\n\n/* 审批 / 提问卡：需要动手时才出现。\n * ⚠️ 必须 `position: fixed` 钉到视口底部：它原本挂在 composerSeat 里、按整页布局排布\n * （实测在 766px 高的布局底部），而 summon 面板只有几百像素高 —— 不钉住就会落在视口外，\n * 用户看不到也点不到（2026-10-01 实测：Playwright 报 element is not visible）。 */\n:root[data-ming-tea-summon=\"1\"] [data-approval-key],\n:root[data-ming-tea-summon=\"1\"] [data-question-key] {\n  visibility: visible !important;\n  position: fixed !important;\n  left: 10px !important;\n  right: 10px !important;\n  bottom: 10px !important;\n  top: auto !important;\n  width: auto !important;\n  max-width: none !important;\n  max-height: 62vh;\n  overflow-y: auto;\n  z-index: 2147483100;\n  pointer-events: auto;\n}\n\n/* 深色模式跟随主题 */\n@media (prefers-color-scheme: dark) {\n  .mt-summon-capsule,\n  .mt-summon-answer {\n    background: color-mix(in srgb, #16211f 92%, transparent);\n  }\n}\n\n/* summon 面板里补的「本会话信任」按钮：跟在官方审批选项后面，样式随官方按钮 */\n:root[data-ming-tea-summon=\"1\"] .mt-summon-trust {\n  margin-left: 8px;\n  opacity: 0.92;\n}\n\n/* 首次呼出的那一次提示（4 秒后消失，之后只留宠物） */\n.mt-summon-hint {\n  margin-bottom: 4px;\n  padding: 5px 12px;\n  border-radius: 999px;\n  background: color-mix(in srgb, var(--mt-paper-raised, #f6faf8) 88%, transparent);\n  color: color-mix(in srgb, var(--mt-ink-strong, #10302c) 72%, transparent);\n  font-size: 12px;\n  line-height: 1.4;\n  box-shadow: 0 6px 14px rgba(12, 48, 44, 0.12);\n}\n.mt-summon-hint[hidden] {\n  display: none;\n}\n\n/* 回答区的三种语气：正常回答 / 状态（正在看屏幕…）/ 失败 */\n.mt-summon-answer[data-tone=\"status\"] {\n  color: color-mix(in srgb, var(--mt-ink-strong, #10302c) 66%, transparent);\n  font-style: italic;\n}\n.mt-summon-answer[data-tone=\"error\"] {\n  border-color: color-mix(in srgb, #c62828 40%, transparent);\n  background: color-mix(in srgb, #fdeceb 92%, transparent);\n  color: #8c1d18;\n}\n\n\n/* ==== 由 theme/palette.mjs 生成，勿手改 ==== */\n/* 令牌覆盖：作用域必须是 body —— 官方把令牌声明在 body 上，写在 :root 会被遮蔽。\n * 这里再提高一级特异度（:root body），避免依赖样式注入顺序。 */\n:root body {\n  --dsw-alias-link: #16857d;\n  --dsw-alias-state-business-primary: #16857d;\n  --dsw-alias-state-business-tertiary: #e6f4f0;\n  --dsw-alias-brand-primary-new-colorprimary-new-color: #16857d;\n  --dsw-alias-brand-primary: #16857d;\n  --dsw-alias-button-info-fill: #16857d;\n  --dsw-alias-button-info-hover: #0f716b;\n  --dsw-alias-button-primary-fill: #16857d;\n  --dsw-alias-button-primary-hover: #0f716b;\n  --dsw-alias-bg-base: #f6faf8;\n  --dsw-alias-bg-layer-1: #fbfdfc;\n  --dsw-alias-bg-layer-2: #fbfdfc;\n  --dsw-alias-bg-layer-3: #fbfdfc;\n  --dsw-alias-bg-overlay: #e4eeea;\n  --dsw-alias-bg-module-platform: #eef4f1;\n  --dsw-alias-label-primary: #12333a;\n  --dsw-alias-label-secondary: #4a6a66;\n  --dsw-alias-label-tertiary: #6b8a85;\n  --dsw-alias-label-caption: #89a5a0;\n  --dsw-alias-border-l1: rgba(22,133,125,0.06);\n  --dsw-alias-border-l2: rgba(22,133,125,0.12);\n  --dsw-alias-border-l2-darkmode-thin: rgba(22,133,125,0.06);\n  --dsw-alias-border-l3: rgba(22,133,125,0.16);\n  --dsw-alias-border-l4: rgba(22,133,125,0.22);\n  --dsw-alias-interactive-bg-hover: rgba(22,133,125,0.07);\n  --dsw-alias-interactive-bg-hover-accent: rgba(22,133,125,0.14);\n  --dsw-alias-interactive-bg-hover-solid: rgba(22,133,125,0.07);\n  --dsw-alias-interactive-bg-active: rgba(22,133,125,0.12);\n  --dsw-alias-bg-skeleton: rgba(22,133,125,0.05);\n  --dsw-specific-bubble: #e6f4f0;\n  --dsw-specific-bubble-highlight: #d9efea;\n  --dsw-specific-menu: rgba(251,253,252,0.86);\n  --dsw-specific-input-major: #fbfdfc;\n  --dsw-specific-sidebar-nav-item-active: #e6f4f0;\n  --dsw-specific-sidebar-nav-item-active-accent: #d9efea;\n  --dsw-specific-sidebar-nav-item-hover: rgba(22,133,125,0.07);\n  --dsw-alias-markdown-code-block: #eef4f1;\n  --dsw-alias-markdown-code-block-banner: #eef4f1;\n  --dsw-alias-markdown-inline-code: #e6f4f0;\n  --dsw-alias-tooltip-bg: #12333a;\n  --dsw-alias-toast-bg: #12333a;\n  --dsw-alias-bg-mask-1: rgba(18,51,58,0.22);\n  --dsw-alias-bg-mask-2: rgba(18,51,58,0.12);\n  --dsw-alias-bg-mask-3: rgba(18,51,58,0.48);\n  --dsw-alias-state-warning-primary: #d98218;\n  --dsw-alias-state-warn-tertiary: #fdf1e0;\n  --dsw-alias-bg-layer-4: #fbfdfc;\n  --dsw-alias-label-error: #c62828;\n  --dsw-alias-bg-l1: #f6faf8;\n  --dsw-alias-bg-l2: #fbfdfc;\n  --dsw-alias-separator-primary: rgba(22,133,125,0.06);\n  --dsw-alias-fill-l1: rgba(22,133,125,0.07);\n  --dsw-alias-fill-l2: rgba(22,133,125,0.14);\n  --dsw-alias-fill-tsp-secondary: rgba(22,133,125,0.07);\n  --dsw-alias-label-quaternary: #89a5a0;\n  --trajectory-turn-accent: color-mix(in srgb, #16857d 18%, var(--dsw-alias-bg-layer-1));\n  --dsw-font-family: \"Noto Sans CJK SC\", \"Noto Sans SC\", \"PingFang SC\", \"Microsoft YaHei\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif;\n  --ds-font-family-code: \"SF Mono\", \"JetBrains Mono\", \"Fira Code\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", \"PingFang SC\", \"Microsoft YaHei\", monospace;\n  --dsw-font-mono: \"SF Mono\", \"JetBrains Mono\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", monospace;\n}\n:root body[data-ds-dark-theme] {\n  --dsw-alias-link: #4fb3a4;\n  --dsw-alias-state-business-primary: #4fb3a4;\n  --dsw-alias-state-business-tertiary: #12312e;\n  --dsw-alias-brand-primary-new-colorprimary-new-color: #4fb3a4;\n  --dsw-alias-brand-primary: #4fb3a4;\n  --dsw-alias-button-info-fill: #4fb3a4;\n  --dsw-alias-button-info-hover: #6cc7b9;\n  --dsw-alias-button-primary-fill: #4fb3a4;\n  --dsw-alias-button-primary-hover: #6cc7b9;\n  --dsw-alias-bg-base: #171d1c;\n  --dsw-alias-bg-layer-1: #1d2423;\n  --dsw-alias-bg-layer-2: #1d2423;\n  --dsw-alias-bg-layer-3: #1d2423;\n  --dsw-alias-bg-overlay: #2b3331;\n  --dsw-alias-bg-module-platform: #121817;\n  --dsw-alias-label-primary: #e8f3f1;\n  --dsw-alias-label-secondary: #b9cfc9;\n  --dsw-alias-label-tertiary: #93aaa4;\n  --dsw-alias-label-caption: #7d948f;\n  --dsw-alias-border-l1: rgba(127,208,196,0.08);\n  --dsw-alias-border-l2: rgba(127,208,196,0.14);\n  --dsw-alias-border-l2-darkmode-thin: rgba(127,208,196,0.08);\n  --dsw-alias-border-l3: rgba(127,208,196,0.18);\n  --dsw-alias-border-l4: rgba(127,208,196,0.24);\n  --dsw-alias-interactive-bg-hover: rgba(127,208,196,0.10);\n  --dsw-alias-interactive-bg-hover-accent: rgba(127,208,196,0.18);\n  --dsw-alias-interactive-bg-hover-solid: rgba(127,208,196,0.10);\n  --dsw-alias-interactive-bg-active: rgba(127,208,196,0.16);\n  --dsw-alias-bg-skeleton: rgba(127,208,196,0.08);\n  --dsw-specific-bubble: #12312e;\n  --dsw-specific-bubble-highlight: #1b3a36;\n  --dsw-specific-menu: rgba(29,36,35,0.86);\n  --dsw-specific-input-major: #1d2423;\n  --dsw-specific-sidebar-nav-item-active: #12312e;\n  --dsw-specific-sidebar-nav-item-active-accent: #1b3a36;\n  --dsw-specific-sidebar-nav-item-hover: rgba(127,208,196,0.10);\n  --dsw-alias-markdown-code-block: #121817;\n  --dsw-alias-markdown-code-block-banner: #121817;\n  --dsw-alias-markdown-inline-code: #12312e;\n  --dsw-alias-tooltip-bg: #e8f3f1;\n  --dsw-alias-toast-bg: #e8f3f1;\n  --dsw-alias-bg-mask-1: rgba(0,0,0,0.50);\n  --dsw-alias-bg-mask-2: rgba(0,0,0,0.36);\n  --dsw-alias-bg-mask-3: rgba(0,0,0,0.66);\n  --dsw-alias-state-warning-primary: #f0a94a;\n  --dsw-alias-state-warn-tertiary: #3a2c17;\n  --dsw-alias-bg-layer-4: #1d2423;\n  --dsw-alias-label-error: #f25a5a;\n  --dsw-alias-bg-l1: #171d1c;\n  --dsw-alias-bg-l2: #1d2423;\n  --dsw-alias-separator-primary: rgba(127,208,196,0.08);\n  --dsw-alias-fill-l1: rgba(127,208,196,0.10);\n  --dsw-alias-fill-l2: rgba(127,208,196,0.18);\n  --dsw-alias-fill-tsp-secondary: rgba(127,208,196,0.10);\n  --dsw-alias-label-quaternary: #7d948f;\n  --trajectory-turn-accent: color-mix(in srgb, #4fb3a4 22%, var(--dsw-alias-bg-layer-1));\n  --dsw-font-family: \"Noto Sans CJK SC\", \"Noto Sans SC\", \"PingFang SC\", \"Microsoft YaHei\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif;\n  --ds-font-family-code: \"SF Mono\", \"JetBrains Mono\", \"Fira Code\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", \"PingFang SC\", \"Microsoft YaHei\", monospace;\n  --dsw-font-mono: \"SF Mono\", \"JetBrains Mono\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", monospace;\n}\n";
		const TOKEN_OVERRIDES = {"--dsw-alias-link":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-state-business-primary":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-state-business-tertiary":{"light":"#e6f4f0","dark":"#12312e"},"--dsw-alias-brand-primary-new-colorprimary-new-color":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-brand-primary":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-button-info-fill":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-button-info-hover":{"light":"#0f716b","dark":"#6cc7b9"},"--dsw-alias-button-primary-fill":{"light":"#16857d","dark":"#4fb3a4"},"--dsw-alias-button-primary-hover":{"light":"#0f716b","dark":"#6cc7b9"},"--dsw-alias-bg-base":{"light":"#f6faf8","dark":"#171d1c"},"--dsw-alias-bg-layer-1":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-alias-bg-layer-2":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-alias-bg-layer-3":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-alias-bg-overlay":{"light":"#e4eeea","dark":"#2b3331"},"--dsw-alias-bg-module-platform":{"light":"#eef4f1","dark":"#121817"},"--dsw-alias-label-primary":{"light":"#12333a","dark":"#e8f3f1"},"--dsw-alias-label-secondary":{"light":"#4a6a66","dark":"#b9cfc9"},"--dsw-alias-label-tertiary":{"light":"#6b8a85","dark":"#93aaa4"},"--dsw-alias-label-caption":{"light":"#89a5a0","dark":"#7d948f"},"--dsw-alias-border-l1":{"light":"rgba(22,133,125,0.06)","dark":"rgba(127,208,196,0.08)"},"--dsw-alias-border-l2":{"light":"rgba(22,133,125,0.12)","dark":"rgba(127,208,196,0.14)"},"--dsw-alias-border-l2-darkmode-thin":{"light":"rgba(22,133,125,0.06)","dark":"rgba(127,208,196,0.08)"},"--dsw-alias-border-l3":{"light":"rgba(22,133,125,0.16)","dark":"rgba(127,208,196,0.18)"},"--dsw-alias-border-l4":{"light":"rgba(22,133,125,0.22)","dark":"rgba(127,208,196,0.24)"},"--dsw-alias-interactive-bg-hover":{"light":"rgba(22,133,125,0.07)","dark":"rgba(127,208,196,0.10)"},"--dsw-alias-interactive-bg-hover-accent":{"light":"rgba(22,133,125,0.14)","dark":"rgba(127,208,196,0.18)"},"--dsw-alias-interactive-bg-hover-solid":{"light":"rgba(22,133,125,0.07)","dark":"rgba(127,208,196,0.10)"},"--dsw-alias-interactive-bg-active":{"light":"rgba(22,133,125,0.12)","dark":"rgba(127,208,196,0.16)"},"--dsw-alias-bg-skeleton":{"light":"rgba(22,133,125,0.05)","dark":"rgba(127,208,196,0.08)"},"--dsw-specific-bubble":{"light":"#e6f4f0","dark":"#12312e"},"--dsw-specific-bubble-highlight":{"light":"#d9efea","dark":"#1b3a36"},"--dsw-specific-menu":{"light":"rgba(251,253,252,0.86)","dark":"rgba(29,36,35,0.86)"},"--dsw-specific-input-major":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-specific-sidebar-nav-item-active":{"light":"#e6f4f0","dark":"#12312e"},"--dsw-specific-sidebar-nav-item-active-accent":{"light":"#d9efea","dark":"#1b3a36"},"--dsw-specific-sidebar-nav-item-hover":{"light":"rgba(22,133,125,0.07)","dark":"rgba(127,208,196,0.10)"},"--dsw-alias-markdown-code-block":{"light":"#eef4f1","dark":"#121817"},"--dsw-alias-markdown-code-block-banner":{"light":"#eef4f1","dark":"#121817"},"--dsw-alias-markdown-inline-code":{"light":"#e6f4f0","dark":"#12312e"},"--dsw-alias-tooltip-bg":{"light":"#12333a","dark":"#e8f3f1"},"--dsw-alias-toast-bg":{"light":"#12333a","dark":"#e8f3f1"},"--dsw-alias-bg-mask-1":{"light":"rgba(18,51,58,0.22)","dark":"rgba(0,0,0,0.50)"},"--dsw-alias-bg-mask-2":{"light":"rgba(18,51,58,0.12)","dark":"rgba(0,0,0,0.36)"},"--dsw-alias-bg-mask-3":{"light":"rgba(18,51,58,0.48)","dark":"rgba(0,0,0,0.66)"},"--dsw-alias-state-warning-primary":{"light":"#d98218","dark":"#f0a94a"},"--dsw-alias-state-warn-tertiary":{"light":"#fdf1e0","dark":"#3a2c17"},"--dsw-alias-bg-layer-4":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-alias-label-error":{"light":"#c62828","dark":"#f25a5a"},"--dsw-alias-bg-l1":{"light":"#f6faf8","dark":"#171d1c"},"--dsw-alias-bg-l2":{"light":"#fbfdfc","dark":"#1d2423"},"--dsw-alias-separator-primary":{"light":"rgba(22,133,125,0.06)","dark":"rgba(127,208,196,0.08)"},"--dsw-alias-fill-l1":{"light":"rgba(22,133,125,0.07)","dark":"rgba(127,208,196,0.10)"},"--dsw-alias-fill-l2":{"light":"rgba(22,133,125,0.14)","dark":"rgba(127,208,196,0.18)"},"--dsw-alias-fill-tsp-secondary":{"light":"rgba(22,133,125,0.07)","dark":"rgba(127,208,196,0.10)"},"--dsw-alias-label-quaternary":{"light":"#89a5a0","dark":"#7d948f"},"--trajectory-turn-accent":{"light":"color-mix(in srgb, #16857d 18%, var(--dsw-alias-bg-layer-1))","dark":"color-mix(in srgb, #4fb3a4 22%, var(--dsw-alias-bg-layer-1))"},"--dsw-font-family":{"light":"\"Noto Sans CJK SC\", \"Noto Sans SC\", \"PingFang SC\", \"Microsoft YaHei\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif","dark":"\"Noto Sans CJK SC\", \"Noto Sans SC\", \"PingFang SC\", \"Microsoft YaHei\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", system-ui, sans-serif"},"--ds-font-family-code":{"light":"\"SF Mono\", \"JetBrains Mono\", \"Fira Code\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", \"PingFang SC\", \"Microsoft YaHei\", monospace","dark":"\"SF Mono\", \"JetBrains Mono\", \"Fira Code\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", \"PingFang SC\", \"Microsoft YaHei\", monospace"},"--dsw-font-mono":{"light":"\"SF Mono\", \"JetBrains Mono\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", monospace","dark":"\"SF Mono\", \"JetBrains Mono\", Menlo, Consolas, \"Noto Sans Mono CJK SC\", monospace"}};
		if (!document.getElementById(STYLE_ID)) {
			const style = document.createElement("style");
			style.id = STYLE_ID;
			style.setAttribute("data-ming-tea", "rounded-theme");
			style.textContent = CSS;
			document.head.appendChild(style);
		}
		// 铭荼界面定制层：按文字匹配的少数几处 DOM 调整。
		//
		// 为什么需要它：DSH 侧栏与设置导航的条目**没有语义类名**（只有文字），
		// CSS 无法按文字选择，因此改名、隐藏、追加页脚入口只能靠 DOM 调整。
		// 这是有意为之的例外——本插件此前只做样式叠加；这里的调整被限制成
		// "声明式清单 + 幂等应用"，只作用于最小必要节点。
		//
		// 关键区分：侧栏（应用内导航）与设置页导航会复用同一列区域，且都有
		// 「专家 / 技能 / 连接手机」这类同名条目。设置页的条目带 `dcu-settings-link`
		// 类，侧栏条目没有——因此隐藏规则一律**排除带该类或其设置容器内的节点**，
		// 避免把设置里的入口一起隐藏掉。
		//
		// 脆弱性（已知并接受）：匹配依据是第三方文案。文案一变，对应调整会
		// **静默失效**（不报错、不破坏页面），届时应改清单而不是加特例。
		//
		// 约束：只匹配叶子文本完全相等的节点；隐藏只作用于按钮/链接祖先；
		// MutationObserver + 去抖重放以适配 React 重渲染；已处理节点打标记保证幂等；
		// 任何异常静默降级。

		const MING_TEA_TWEAKS = {
		  // 文案替换（整段完全相等时替换，安全但覆盖面窄）
		  rename: [
		    ["IM助理", "连接手机"],
		    // 品牌与首屏
		    ["DSH 本地构建", "铭荼"],
		    ["DSH 导航", "铭荼导航"],
		    ["预览版", "内测版"],
		    // 账号页（官方是 DeepSeek 账号体系措辞）
		    ["已登录 DeepSeek", "已登录"],
		    ["账号与余额", "账号"],
		    ["充值余额", "余额"],
		    ["赠金余额", "赠送额度"],
		    ["查询用量", "用量"],
		    // 设置页：把开发者措辞改平实
		    // 0.2.0-rc.2 把官方原文从「内测声明」改成了「预览版说明」——旧串在 0.2 里已 0 命中，必须跟新串。
		    ["预览版说明", "使用说明"],
		    ["Agent 预设", "助手模式"],
		    ["Agent 循环", "运行方式"],
		    ["网页搜索", "联网搜索"],
		    // 这一节实际列的是**本部署装配的全部插件**（含我们装的社区/自有插件），
		    // 叫「内置插件 / 随应用自带」会让人以为只列官方自带的那批，改成「已安装插件」。
		    ["内置插件", "已安装插件"],
		    ["系统与所有会话共用", "本部署装配的全部插件（含已安装的第三方）"],
		    ["随应用自带", "已安装插件"],
		    ["第三方模型提供商", "常见模型服务"],
		    ["自定义模型 API", "自定义接口"],
		    // 0.2.0-rc.2 上游把「Subagent」统一改称「子智能体」（settings-subagent 里 10 处），源串跟着改；
		    // 目标仍用更口语的「子任务」。
		    ["设置子智能体的递归层级、数量和模型。", "设置子任务的层数、数量与所用模型。"],
		    // 「深度求索中」直译自 DeepSeek 品牌名，读着像"在搜东西"；用户要求改成「深度思考中」
		    ["深度求索中", "深度思考中"],
		    // 社区插件里的 DSH 残留
		    ["DSH 技能", "铭荼技能"],
		    ["DSH 自定义宠物", "自定义宠物"],
		    ["在 DSH 中创建", "开始创建"],
		    ["DSH 未接受宠物创建请求", "创建请求未被接受"],
		    // 会话占用错误整句（官方含 dsh web / 桌面端等术语）
		    [
		      "当前会话已被占用，可能是其他正在运行的 DSH 导致的（如其他 dsh web、桌面端），请退出其他正在运行的 DSH 后重试。",
		      "当前会话已被占用，可能其他窗口正在使用，请关闭其他窗口后重试。",
		    ],
		  ],

		  // 文案子串替换：用于句子内部的引用（频道空态会写「先在设置 → IM助理 里连接渠道」）。
		  // 限定叶子文本且长度 < 80，避免在大段正文里误替换。
		  replaceText: [
		    ["IM助理", "连接手机"],
		    // 轮次进行态的插值句：「深度求索中，用时 12 秒」（< 80 字，走子串规则）
		    ["深度求索中", "深度思考中"],
		    // 首启引导弹窗（官方写的是 DeepSeek 官方模型）
		    ["添加一个 API Key 开始使用", "添加 API Key 后即可开始使用"],
		    ["配置 DeepSeek 官方模型，即可开始使用。", "配置模型提供方后即可开始使用。"],
		    // 账号页整块（官方站点的「设计项目」文案与本产品完全不符）
		    ["返回 DeepSeek Harness", "返回铭荼"],
		    ["当前未登录 DeepSeek Harness 账号", "当前未登录铭荼账号"],
		    ["登录 DeepSeek Harness 账号获取专属 API Key", "登录铭荼账号以获取专属 API Key"],
		    // 「开始你的创作」在 0.2.0-rc.2 已被上游删除，官方新文案本身就是我们想要的「开始使用」，
		    // 所以这条规则删掉即可（旧规则留着也只是永不命中的死规则）。
		    [
		      // 0.2.0-rc.2 把旧句「登录后即可创建、编辑和分享你的设计项目…」换成了带 DeepSeek 品牌的新句，
		      // 旧规则因此失效且新句仍需去品牌化 —— 这是本轮唯一「必须补」的文案规则。
		      "登录 DeepSeek 账号，或添加 API Key，即可开始使用。你的项目和文件保存在本地。",
		      "登录铭荼账号，或添加 API Key 即可开始使用；会话与文件保存在本机。",
		    ],
		    // 设置页里的品牌引用
		    ["设置 DeepSeek 的搜索提供方。", "设置网页搜索的提供方。"],
		    ["运行 DeepSeek Harness 的主机", "运行铭荼的主机"],
		    // 社区插件更新提示里的运行环境名（必须排在泛化规则之前）
		    ["DSH Desktop", "桌面端"],
		    ["DSH Web", "应用"],
		    ["dsh web", "应用"],
		    ["DSH 终端", "终端"],
		    ["DSH 服务端", "服务端"],
		    ["DSH 工作区", "工作区"],
		    ["DSH 对话服务", "对话服务"],
		    ["DSH 版本", "铭荼版本"],
		    ["DSH 插件", "铭荼插件"],
		    ["Codex 风格", "现代风格"],
		    ["已归档", "归档"],
		    // 泛化兜底必须最后：否则会把上面的 DSH Web / DSH Desktop 误改成「铭荼 Web」
		    ["DSH", "铭荼"],
		  ],

		  // 隐藏（任何位置，包括设置页）：命中叶子文本后隐藏其按钮祖先
		  hideAnywhere: ["Codex UI", "GitHub", "问题反馈", "意见反馈"],

		  // 隐藏（仅侧栏，设置页保留同名入口）：侧栏与设置导航会复用同一列区域且
		  // 存在同名条目（连接手机 / 专家 / 技能 / 插件 / 连接器），必须按容器区分。
		  hideInSidebar: ["连接手机", "专家", "技能", "插件", "连接器"],

		  // 整块隐藏（结构容器，比逐项隐藏稳）：侧栏的「扩展管理」及其子项
		  hideContainers: [".dcu-extensions-group"],

		  // ── 清亮模式（低配/专注）─────────────────────────────────────────
		  // 开关位于「设置 → 常规」，状态存在浏览器 localStorage（不做假的服务端同步）。
		  lite: {
		    storageKey: "ming-tea.lite-mode",
		    label: "清亮模式",
		    desc: "关闭宠物、专家、定时任务等非必要功能并隐藏其入口，同时减少动画与渲染负担。适合低配置机器或需要专注时使用。",
		    // 命中叶子文本即隐藏其按钮祖先；hostClass 用于同名文字（如「定时」标签）消歧
		    hide: [
		      { text: "宠物" },
		      { text: "专家" },
		      { text: "定时任务" },
		      { text: "定时", hostClass: "dcu-im-tab" },
		    ],
		    hideSelectors: [".dcp-floating"],
		  },

		  // 页脚入口：与「设置」同一行（账户 / 连接手机 / 剩余用量）
		  // 账户与用量已接站点（登录走设备授权，用量走宿主 RPC）；连接手机仍是占位。
		  footer: {
		    account: { label: "账户", hint: "无法连接本地服务，登录面板打不开。" },
		    phone: { label: "连接手机", hint: "扫码绑定尚未接入；设置 → 连接手机 里可先看说明。" },
		    quota: { label: "剩余用量", hint: "正在读取站点额度…" },
		  },

		  // 设置页导航追加项：真实调用宿主做更新检查（站点未发布时静默「已是最新」）
		  settingsExtra: { label: "检查更新", hint: "检查中…" },
		};

		const MING_TEA_ICONS = {
		  account:
		    '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><circle cx="8" cy="5.4" r="2.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M2.9 13.2c.6-2.6 2.7-3.9 5.1-3.9s4.5 1.3 5.1 3.9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
		  // 头像里的实心人形（圆底由 .mt-foot-avatar 提供）
		  avatar:
		    '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><circle cx="8" cy="5.6" r="2.7" fill="currentColor"/><path d="M2.6 14c.5-3 2.8-4.5 5.4-4.5S13 11 13.5 14z" fill="currentColor"/></svg>',
		  phone:
		    '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><rect x="4.4" y="1.6" width="7.2" height="12.8" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7.1 12.4h1.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
		  update:
		    '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M12.9 6.4A5.3 5.3 0 1 0 13 9.7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13.4 3.4v3.2h-3.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
		};

		// 用量圆环的运行时状态：数据来自宿主半区（站点 /usage）。未登录或取不到时
		// 一律回落成「—」，**绝不编造百分比**。
		const MING_TEA_QUOTA = {
		  percent: null,
		  detail: "",
		  tier: null,
		  tierName: null,
		  availableTiers: [],
		  free: null, // { limit, used, remaining, percent, detail }
		  paid: null, // { budget, used, remaining, percent, detail }
		  autoFree: null, // { limit, used, remaining, exhausted, configured } —— 付费档赠送的 Auto 次数
		  plan: null, // { key, name, expiresAt, concurrency, autoFreeCalls, monthlyBudgetRmb }
		  catalog: [], // 站点给的上下文 + 价目表（含每个模型的 context_window）
		  loaded: false,
		  signedIn: false,
		  error: "",
		};

		// 站点给出的可用模型（付费档会多出具体型号），面板里展示
		const MING_TEA_MODELS = { list: [], tierName: null, loaded: false, error: "" };

		// 账户状态（页脚显示用户名、面板显示登录态）：同样只由宿主半区给出
		const MING_TEA_ACCOUNT = {
		  user: null,
		  signedIn: false,
		  expired: false,
		  expiresAt: undefined,
		  pending: null,
		  plan: null, // { key, name, expiresAt, concurrency, autoFreeCalls, monthlyBudgetRmb }（付费档才有）
		  memberExpiresAt: undefined, // 站点 user.member_expires_at（会员到期）
		};
		let mingTeaAccountPoller = null;
		let mingTeaQuotaTimer = null;

		// 站点公开的档位目录（/api/ming-tea/tiers）：免登录、字段最全，用于「可升级」那一行
		const MING_TEA_TIERS = { list: [], free: null, contextCatalog: [], loaded: false };

		// 站点故障后的退避重试：约 5s / 30s / 2min，最多 3 次。
		// 起因（实测）：站点内部服务挂掉时模型同步与额度都失败，而此前**没有任何重试**，
		// 只能等用户下次开页面；站点恢复后界面会长时间停在“读不到”的状态。
		const MING_TEA_RETRY_DELAYS_MS = [5000, 30000, 120000];
		let mingTeaRecoveryTimer = null;
		let mingTeaRecoveryAttempt = 0;

		function mingTeaLeafNodes(root) {
		  const out = [];
		  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
		    acceptNode: (node) => {
		      const text = (node.nodeValue || "").trim();
		      // 上限 400：精确匹配（rename）要能处理整句式提示，例如会话占用错误整句。
		      // 子串替换（replaceText）另在应用处限制 < 80，避免误伤长正文。
		      if (!text || text.length > 400) return NodeFilter.FILTER_REJECT;
		      const parent = node.parentElement;
		      if (!parent) return NodeFilter.FILTER_REJECT;
		      if (parent.closest("style, script, textarea, input, [contenteditable='true']")) return NodeFilter.FILTER_REJECT;
		      return NodeFilter.FILTER_ACCEPT;
		    },
		  });
		  let node = walker.nextNode();
		  while (node) {
		    out.push(node);
		    node = walker.nextNode();
		  }
		  return out;
		}

		function mingTeaActionable(el) {
		  return el.closest("button, a, [role='button'], [role='link']");
		}

		// 设置页导航项（这些必须保留，不能因为同名而被侧栏规则误伤）
		function mingTeaInSettings(host) {
		  if (!host) return false;
		  if (host.classList && host.classList.contains("dcu-settings-link")) return true;
		  return !!host.closest('[class*="settings"]');
		}

		function liteModeEnabled() {
		  try {
		    return localStorage.getItem(MING_TEA_TWEAKS.lite.storageKey) === "1";
		  } catch {
		    return false;
		  }
		}

		// ── summon 三态面板（快捷键呼出的助手）────────────────────────────
		// 产品要求的表现（2026-10-01 用户明确）：
		//   ① 按下快捷键 → **只出现一个宠物**；
		//   ② 用户说话 → 出现一个悬浮胶囊，显示语音转文字后的内容；
		//   ③ 助手回答 → 出现在胶囊**下方**。
		//
		// 分工：壳负责窗口尺寸/位置/点击穿透（它按我们上报的档位调整），我们负责
		// 藏外壳、留宠物、把胶囊与回答叠上去，并把当前档位告诉壳。
		// 上报通道是一条只绑 127.0.0.1、带 token 的极简单向 GET（壳里叫「上报口」）：
		// 面板页是外部源、拿不到 Tauri IPC，所以用 <img> 打一发最省事（还不触发 CORS 预检）。
		const MING_TEA_SUMMON = {
		  on: false,
		  auto: false,
		  shell: null,
		  token: null,
		  stage: "",
		  layer: null,
		  capsule: null,
		  answer: null,
		  permission: null,
		  hint: null,
		  hintShown: false,
		  lastError: "",
		  errorPending: false,
		  permissionCheckedAt: 0,
		  listeningRequested: false,
		  submittedText: "",
		  lastAnswer: "",
		};

		/** 解析召唤参数（只在首轮做一次）。
		 * 三类来源按可靠性排序：
		 *   ① `window.__MING_TEA_SUMMON`：壳用 initialization_script 在 **document-start** 抓好的
		 *      —— DSH 启动后会把 URL query 清掉（实测 6 秒内 search 变空），所以这是主路径；
		 *   ② `location.search`：万一我们比清理更早跑，也能直接读到；
		 *   ③ `window.name`：跨导航仍然保留，作为兜底。 */
		function mingTeaSummonInit() {
		  try {
		    const frozen = window.__MING_TEA_SUMMON;
		    if (frozen && frozen.on) {
		      MING_TEA_SUMMON.on = true;
		      MING_TEA_SUMMON.auto = frozen.auto === true;
		      MING_TEA_SUMMON.shell = frozen.shell ?? null;
		      MING_TEA_SUMMON.token = frozen.token ?? null;
		      return true;
		    }
		    // Esc 收起面板：走同一条上报口（hide=1），壳负责隐藏窗口
		    if (!window.__mingTeaSummonEscBound) {
		      window.__mingTeaSummonEscBound = true;
		      document.addEventListener(
		        "keydown",
		        (event) => {
		          if (event.key !== "Escape" || !MING_TEA_SUMMON.on) return;
		          event.preventDefault();
		          if (!MING_TEA_SUMMON.shell || !MING_TEA_SUMMON.token) return;
		          try {
		            const probe = new Image();
		            probe.src = `${MING_TEA_SUMMON.shell}/stage?t=${encodeURIComponent(MING_TEA_SUMMON.token)}&hide=1&_=${Date.now()}`;
		          } catch {
		            /* 收起失败不影响继续用 */
		          }
		        },
		        true,
		      );
		    }
		    const params = new URLSearchParams(location.search);
		    if (params.get("ming-tea") === "summon") {
		      MING_TEA_SUMMON.on = true;
		      MING_TEA_SUMMON.auto = params.get("auto") === "1";
		      MING_TEA_SUMMON.shell = params.get("mt-shell");
		      MING_TEA_SUMMON.token = params.get("mt-token");
		      return true;
		    }
		    if (typeof window.name === "string" && window.name.includes("ming-tea=summon")) {
		      const fromName = new URLSearchParams(window.name);
		      MING_TEA_SUMMON.on = true;
		      MING_TEA_SUMMON.auto = fromName.get("auto") === "1";
		      MING_TEA_SUMMON.shell = fromName.get("mt-shell");
		      MING_TEA_SUMMON.token = fromName.get("mt-token");
		      return true;
		    }
		    return false;
		  } catch {
		    return false;
		  }
		}

		/** 告诉壳「现在该是哪一档」——决定窗口大小、位置与是否点击穿透。 */
		function mingTeaSummonReport(stage) {
		  if (!MING_TEA_SUMMON.on) return;
		  if (MING_TEA_SUMMON.stage === stage) return;
		  MING_TEA_SUMMON.stage = stage;
		  document.documentElement.dataset.mingTeaSummonStage = stage;
		  if (!MING_TEA_SUMMON.shell || !MING_TEA_SUMMON.token) return;
		  try {
		    const probe = new Image();
		    probe.src = `${MING_TEA_SUMMON.shell}/stage?t=${encodeURIComponent(MING_TEA_SUMMON.token)}&stage=${encodeURIComponent(stage)}&_=${Date.now()}`;
		  } catch {
		    /* 上报失败只是尺寸不跟着变，不该影响说话与回答 */
		  }
		}

		/** 建我们自己的层（幂等）：胶囊在上、回答在下；底部留出宠物位置由 CSS 负责。 */
		function mingTeaSummonEnsureNodes() {
		  if (MING_TEA_SUMMON.layer) return;
		  if (!document.body) return;
		  const layer = document.createElement("div");
		  layer.className = "mt-summon-layer";
		  const hint = document.createElement("div");
		  hint.className = "mt-summon-hint";
		  hint.hidden = true;
		  const permission = document.createElement("button");
		  permission.type = "button";
		  permission.className = "mt-summon-permission";
		  permission.hidden = true;
		  const capsule = document.createElement("div");
		  capsule.className = "mt-summon-capsule";
		  const answer = document.createElement("div");
		  answer.className = "mt-summon-answer";
		  layer.append(hint, permission, capsule, answer);
		  MING_TEA_SUMMON.hint = hint;
		  MING_TEA_SUMMON.permission = permission;
		  document.body.appendChild(layer);
		  MING_TEA_SUMMON.layer = layer;
		  MING_TEA_SUMMON.capsule = capsule;
		  MING_TEA_SUMMON.answer = answer;
		}

		const mingTeaSummonText = (selector) => {
		  try {
		    const nodes = document.querySelectorAll(selector);
		    if (nodes.length === 0) return "";
		    const node = nodes[nodes.length - 1];
		    // ⚠️ 必须用 textContent：summon 模式把对话区设成 visibility:hidden，
		    // 而 innerText 对不可见元素返回空串 —— 2026-10-01 实测踩到，表现为「回答永远不显示」。
		    return String(node.textContent ?? "").trim();
		  } catch {
		    return "";
		  }
		};

		/** 输入框里的文字（语音识别结果最终会落在这里）。 */
		function mingTeaSummonComposerText() {
		  try {
		    const box = document.querySelector('[data-composer-card] [contenteditable="true"], [data-composer-card] textarea, [contenteditable="true"], textarea');
		    if (!box) return "";
		    return String(box.value ?? box.textContent ?? "").trim();
		  } catch {
		    return "";
		  }
		}

		/** 官方语音的相态：recording / transcribing / idle（元素不在=collapsed，即没在听）。 */
		function mingTeaSummonVoicePhase() {
		  try {
		    const el = document.querySelector("[data-voice-activity]");
		    return el ? el.getAttribute("data-voice-activity") : null;
		  } catch {
		    return null;
		  }
		}

		/** 点官方语音触发按钮开始听。
		 * 注意两点（2026-10-01 实测）：
		 *   ① 语音按钮是**按需挂载**的（模型就绪 + 输入区被交互过才出现），所以找不到时先聚焦输入框催一下，
		 *      并允许后续轮次继续重试（调用方按 attempts 控制次数）；
		 *   ② 它在被 summon 隐藏的输入区里，`visibility:hidden` 不影响程序化点击。 */
		const MING_TEA_VOICE_TRIGGER_SELECTORS = [
		  '[data-composer-card] button[aria-label*="录音"]',
		  '[data-composer-card] button[aria-label*="语音"]',
		  '[data-composer-card] button[aria-label*="说话"]',
		  '[data-composer-card] [class*="triggerAnchor"] button',
		  '[data-composer-card] button[class*="trigger"]',
		  '[class*="triggerAnchor"] button',
		];

		function mingTeaSummonStartListening() {
		  try {
		    for (const selector of MING_TEA_VOICE_TRIGGER_SELECTORS) {
		      const button = document.querySelector(selector);
		      if (button && !button.disabled) {
		        button.click();
		        return true;
		      }
		    }
		    // 还没挂载：语音行是在**输入区收到真实指针事件**之后才挂载的（只 focus() 不够，实测），
		    // 所以这里补一串合成的指针/焦点事件当「催挂载」，下一次 tick 再找按钮。
		    const input = document.querySelector('[data-composer-input="true"], [data-composer-card] [contenteditable="true"]');
		    if (input) {
		      input.focus?.();
		      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click", "focusin"]) {
		        try {
		          input.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
		        } catch {
		          /* 单个事件失败不影响其它 */
		        }
		      }
		    }
		    return false;
		  } catch {
		    return false;
		  }
		}

		/** 把输入框里的文字发出去（复用官方发送逻辑：对输入框派发回车）。 */
		function mingTeaSummonSubmit() {
		  try {
		    const box = document.querySelector('[data-composer-card] [contenteditable="true"], [data-composer-card] textarea, [contenteditable="true"], textarea');
		    if (!box) return false;
		    box.focus();
		    const event = new KeyboardEvent("keydown", {
		      key: "Enter",
		      code: "Enter",
		      keyCode: 13,
		      which: 13,
		      bubbles: true,
		      cancelable: true,
		    });
		    box.dispatchEvent(event);
		    return true;
		  } catch {
		    return false;
		  }
		}

		/** 向壳要一次权限状态，缺哪个就把提示条显示出来（点一下跳系统设置对应页）。
		 * 30 秒才查一次：权限不会自己变，没必要每轮都打。 */
		function mingTeaSummonCheckPermissions() {
		  if (!MING_TEA_SUMMON.shell || !MING_TEA_SUMMON.token) return;
		  const now = Date.now();
		  if (now - MING_TEA_SUMMON.permissionCheckedAt < 30000) return;
		  MING_TEA_SUMMON.permissionCheckedAt = now;
		  fetch(`${MING_TEA_SUMMON.shell}/permissions?t=${encodeURIComponent(MING_TEA_SUMMON.token)}`)
		    .then((response) => (response.ok ? response.json() : null))
		    .then((payload) => {
		      const chip = MING_TEA_SUMMON.permission;
		      if (!chip || !payload || !Array.isArray(payload.capabilities)) return;
		      const missing = payload.capabilities.find((item) => item && item.permission_required && !item.available);
		      if (!missing) {
		        chip.hidden = true;
		        return;
		      }
		      chip.hidden = false;
		      chip.textContent = `需要${missing.label ?? "系统"}权限 · 点此开启`;
		      chip.dataset.capability = missing.id ?? "";
		    })
		    .catch(() => {
		      /* 探测失败就不显示提示条，不打扰用户 */
		    });
		}

		/** 在官方审批卡里补一个「本会话信任」按钮：点一下之后，本会话的普通电脑操作不再反复问。
		 * 主窗口与 summon 面板都会注入（审批流是同一套，用户在哪遇到都能选）。
		 * 为什么补在官方卡里而不是自己画一套：审批是官方的流程（拒绝/允许一次），我们只加一个选项，
		 * 不抢它的语义；危险动作（输入/剪贴板/上传/删除/安装/系统命令）在宿主侧始终会继续问。 */
		function mingTeaInjectSessionTrust() {
		  try {
		    const cards = document.querySelectorAll("[data-approval-key]");
		    for (const card of cards) {
		      if (card.querySelector(".mt-summon-trust")) continue;
		      const buttons = Array.from(card.querySelectorAll("button"));
		      const allow = buttons.find((b) => /允许|批准/.test(b.textContent || ""));
		      const deny = buttons.find((b) => /拒绝/.test(b.textContent || ""));
		      const trustButton = document.createElement("button");
		      trustButton.type = "button";
		      trustButton.className = `${allow?.className ?? ""} mt-summon-trust`.trim();
		      trustButton.textContent = "本会话信任";
		      trustButton.title = "本会话内的普通电脑操作不再询问；输入、上传、删除、安装等仍会确认";
		      trustButton.addEventListener("click", async (event) => {
		        event.preventDefault();
		        event.stopPropagation();
		        try {
		          await mingTeaHubRpc("summon.trust");
		          trustButton.textContent = "已信任本会话";
		          trustButton.disabled = true;
		        } catch (error) {
		          trustButton.textContent = `信任失败：${String(error?.message ?? error).slice(0, 20)}`;
		          return;
		        }
		        // 顺手把当前这一次也允许掉，用户不用再点一次
		        if (allow) allow.click();
		      });
		      (allow?.parentElement ?? card).appendChild(trustButton);

		      // 「拒绝并停止」：只点拒绝的话模型会换个工具再试（实测：拒绝后立刻又弹一次），
		      // 所以这里拒绝当前这次 + 中止本轮（官方 conversation 服务有 cancel()）。
		      if (!card.querySelector(".mt-summon-deny-stop")) {
		        const denyStop = document.createElement("button");
		        denyStop.type = "button";
		        denyStop.className = `${deny?.className ?? ""} mt-summon-deny-stop`.trim();
		        denyStop.textContent = "拒绝并停止";
		        denyStop.title = "拒绝这次操作，并结束本轮，避免助手反复换工具重试";
		        denyStop.addEventListener("click", async (event) => {
		          event.preventDefault();
		          event.stopPropagation();
		          if (deny) deny.click();
		          try {
		            await mingTeaCtx?.conversation?.cancel?.();
		          } catch {
		            /* 取消失败也没关系，至少这次已经被拒 */
		          }
		        });
		        (deny?.parentElement ?? allow?.parentElement ?? card).appendChild(denyStop);
		      }
		    }
		  } catch {
		    /* 注入失败不影响审批本身 */
		  }
		}

		/** 把已知的工具错误翻译成用户能动手解决的提示。
		 *
		 * 为什么需要：summon 面板把对话区藏了，用户只看得到我们镜像的这一行错误。
		 * 工具层的英文原文（例如「模型不能收图」）对用户没有任何可操作性 ——
		 * 实测踩到：模型没声明 image 输入时「看屏幕」会在工具层被拒，
		 * 报 `cannot read … as an image: model "…" does not declare image input`，
		 * 用户只会看到一句英文、不知道该干什么（2026-10-02）。
		 * 只翻译**确定的**几种，其余原样透出 —— 不猜、不吞掉原始信息。 */
		function mingTeaHumanizeError(text) {
		  const raw = typeof text === "string" ? text : String(text ?? "");
		  if (/does not declare image input|does not support image input/i.test(raw)) {
		    return "当前模型不能看图，请在输入框旁换一个支持图片的模型（例如 deepseek-v4.1-flash），再说一次。";
		  }
		  return raw;
		}

		/** 每轮（180ms 节流）跑一次：判定档位、更新胶囊与回答、按需开始听与发送。 */
		function mingTeaSummonTick() {
		  if (!MING_TEA_SUMMON.on) return;
		  mingTeaSummonEnsureNodes();
		  if (!MING_TEA_SUMMON.capsule || !MING_TEA_SUMMON.answer) return;

		  // 首次呼出时给一次「点一下我就开始听」的提示，4 秒后自己消失；
		  // 之后永远只显示宠物（用户明确要求「只出现一个宠物」）。
		  if (MING_TEA_SUMMON.hint && !MING_TEA_SUMMON.hintShown) {
		    MING_TEA_SUMMON.hintShown = true;
		    try {
		      if (window.localStorage.getItem("ming-tea.summon-hint") !== "1") {
		        window.localStorage.setItem("ming-tea.summon-hint", "1");
		        MING_TEA_SUMMON.hint.textContent = "点一下我就开始听";
		        MING_TEA_SUMMON.hint.hidden = false;
		        setTimeout(() => {
		          if (MING_TEA_SUMMON.hint) MING_TEA_SUMMON.hint.hidden = true;
		        }, 4000);
		      }
		    } catch {
		      /* 存不进 localStorage（隐私模式）就干脆不提示，别每次呼出都弹 */
		    }
		  }
		  mingTeaSummonCheckPermissions();
		  // 一次性预热语音 provider：官方 provider 的 prepare 是「同步触发、异步就绪」，
		  // 模型加载要几十秒；不预热的话用户点麦克风后会长时间停在 requesting（实测踩到）。
		  // 预热绑定在发起它的连接上，面板一直开着所以没问题。
		  // ⚠️ 必须等 `connection` 服务就绪再调：面板刚起来的前几轮 tick 里它还没注入，
		  // 早期版本因此在第一轮就调用并**把 warmed 提前置位**，于是永远不重试、worker 从不启动
		  // （实测报「宿主连接不可用（connection 服务未注入）」，打包版里预热一直没生效）。
		  if (!MING_TEA_SUMMON.speechWarmed && MING_TEA_SUMMON.shell) {
		    MING_TEA_SUMMON.speechAttempts = (MING_TEA_SUMMON.speechAttempts ?? 0) + 1;
		    document.documentElement.dataset.mingTeaSpeechPrepare = "pending";
		    mingTeaHubRpc("speech.prepare")
		      .then((result) => {
		        MING_TEA_SUMMON.speechWarmed = true; // 成功才算预热完成
		        document.documentElement.dataset.mingTeaSpeechPrepare = result?.ok ? "ok" : `fail:${result?.reason ?? result?.error ?? "unknown"}`;
		      })
		      .catch((error) => {
		        document.documentElement.dataset.mingTeaSpeechPrepare = `error:${String(error?.message ?? error).slice(0, 60)}`;
		        if (MING_TEA_SUMMON.speechAttempts > 40) MING_TEA_SUMMON.speechWarmed = true; // 放弃，等官方 UI 自己准备
		      });
		  }
		  // 点一下就开始说：WKWebView 惯例要求**真实用户手势**才允许开麦，
		  // 我们合成的事件不算，所以「弹出即自动听」在 macOS 上不一定成立 —— 这一下点击是兜底。
		  //
		  // ⚠️ 必须挂在 document 的**捕获阶段**：宠物浮层是另一个子树（并且和我们的层 z-index 相同、
		  // 排在后面），点在宠物身上时事件根本不会冒泡到我们的层（2026-10-01 实测：handler 一次都没触发）。
		  if (!MING_TEA_SUMMON.tapBound) {
		    MING_TEA_SUMMON.tapBound = true;
		    document.addEventListener(
		      "click",
		      (event) => {
		        // 我们自己的可点控件与审批卡不当作「开始说话」
		        const target = event.target;
		        if (target && typeof target.closest === "function" && target.closest(".mt-summon-permission, .mt-summon-trust, .mt-summon-deny-stop, [data-approval-key], [data-question-key]")) {
		          return;
		        }
		        MING_TEA_SUMMON.listeningRequested = false; // 允许再点一次重试
		        MING_TEA_SUMMON.listeningAttempts = 0;
		        // 两个标记便于排障：点击是否到达处理函数、是否找到了官方麦克风按钮
		        MING_TEA_SUMMON.tapCount = (MING_TEA_SUMMON.tapCount ?? 0) + 1;
		        document.documentElement.dataset.mingTeaTap = String(MING_TEA_SUMMON.tapCount);
		        const started = mingTeaSummonStartListening();
		        document.documentElement.dataset.mingTeaTapResult = started ? "clicked" : "no-trigger";
		        if (started) {
		          MING_TEA_SUMMON.listeningRequested = true;
		          MING_TEA_SUMMON.capsule.textContent = "我在听…";
		          MING_TEA_SUMMON.capsule.dataset.empty = "1";
		        } else {
		          // 官方语音行是**懒挂载**的：刚呼出（宿主刚起）就点，按钮往往还不存在。
		          // 早先这里直接放弃 ⇒ 用户看到的是「点了没反应」（2026-10-02 实测：等 15s 再点才有用）。
		          // 现在记住意图，接下来每轮 tick 重试，并且**立刻给一句反馈**，让用户知道点到了。
		          MING_TEA_SUMMON.tapPending = true;
		          MING_TEA_SUMMON.tapAttempts = 0;
		          MING_TEA_SUMMON.capsule.textContent = "正在准备麦克风…";
		          MING_TEA_SUMMON.capsule.dataset.empty = "1";
		        }
		      },
		      true,
		    );
		  }

		  if (MING_TEA_SUMMON.permission && !MING_TEA_SUMMON.permissionBound) {
		    MING_TEA_SUMMON.permissionBound = true;
		    MING_TEA_SUMMON.permission.addEventListener("click", () => {
		      const id = MING_TEA_SUMMON.permission?.dataset.capability;
		      if (!id || !MING_TEA_SUMMON.shell || !MING_TEA_SUMMON.token) return;
		      const probe = new Image();
		      probe.src = `${MING_TEA_SUMMON.shell}/open-settings?t=${encodeURIComponent(MING_TEA_SUMMON.token)}&open-settings=${encodeURIComponent(id)}&_=${Date.now()}`;
		    });
		  }

		  const voicePhase = mingTeaSummonVoicePhase();
		  const composerText = mingTeaSummonComposerText();
		  const answerText = mingTeaSummonText("[data-turn-process-answer]");
		  // 工具活动与错误：summon 模式把对话区藏起来了，这两类信息如果不主动镜像出来，
		  // 用户只会看到面板一直「没反应」——既看不到「助手正在看屏幕」，也看不到「操作失败」。
		  const toolText = mingTeaSummonText("[data-turn-process-tool-calls]");
		  const screenBusy = /cua_driver_native__|playwright-mcp|computer/i.test(toolText);
		  const errorText = mingTeaSummonText("[data-error]").slice(0, 160);

		  // 用户点过宠物但当时按钮还没挂载 → 继续替他重试（「点了没反应」的那次修复）
		  if (MING_TEA_SUMMON.tapPending) {
		    if (voicePhase) {
		      MING_TEA_SUMMON.tapPending = false; // 已经听上了
		    } else {
		      MING_TEA_SUMMON.tapAttempts = (MING_TEA_SUMMON.tapAttempts ?? 0) + 1;
		      if (mingTeaSummonStartListening()) {
		        MING_TEA_SUMMON.tapPending = false;
		        MING_TEA_SUMMON.listeningRequested = true;
		        document.documentElement.dataset.mingTeaTapResult = "clicked-after-wait";
		        MING_TEA_SUMMON.capsule.textContent = "我在听…";
		      } else if (MING_TEA_SUMMON.tapAttempts > 70) {
		        // ≈180ms × 70 ≈ 12.6 秒还挂不上：如实告诉用户，并允许再点一次重来。
		        MING_TEA_SUMMON.tapPending = false;
		        document.documentElement.dataset.mingTeaTapResult = "no-trigger-give-up";
		        MING_TEA_SUMMON.capsule.textContent = "麦克风还没准备好，再点我一下";
		      }
		    }
		  }

		  // 自动开始听：只在「还没有回答、也没在听」时点一次，避免反复开关麦克风
		  if (MING_TEA_SUMMON.auto && !MING_TEA_SUMMON.listeningRequested && !voicePhase && !answerText) {
		    // 按钮按需挂载，允许重试若干次（大约 180ms × 20 ≈ 4 秒）
		    MING_TEA_SUMMON.listeningAttempts = (MING_TEA_SUMMON.listeningAttempts ?? 0) + 1;
		    if (mingTeaSummonStartListening()) MING_TEA_SUMMON.listeningRequested = true;
		    else if (MING_TEA_SUMMON.listeningAttempts > 20) MING_TEA_SUMMON.listeningRequested = true; // 放弃自动，等用户点
		  }
		  // 说完了（语音相态消失且输入框里有字）→ 自动发送一次
		  if (composerText && composerText !== MING_TEA_SUMMON.submittedText && !voicePhase) {
		    if (mingTeaSummonSubmit()) {
		      MING_TEA_SUMMON.submittedText = composerText;
		      MING_TEA_SUMMON.capsule.textContent = composerText;
		      MING_TEA_SUMMON.capsule.dataset.empty = "0";
		    }
		  }

		  // 胶囊内容：优先显示「已发出的那句」，其次显示正在识别的文字
		  if (composerText && composerText !== MING_TEA_SUMMON.submittedText) {
		    MING_TEA_SUMMON.capsule.textContent = composerText;
		    MING_TEA_SUMMON.capsule.dataset.empty = "0";
		  } else if (voicePhase && !MING_TEA_SUMMON.capsule.textContent) {
		    MING_TEA_SUMMON.capsule.textContent = voicePhase === "transcribing" ? "正在识别…" : "我在听…";
		    MING_TEA_SUMMON.capsule.dataset.empty = "1";
		  }

		  // 回答区的内容优先级（顺序很重要，实测踩过两个坑）：
		  //   1. **新的回答到达时以回答为准**，并清掉挂起的错误 —— 否则「工具失败 → 模型解释」时
		  //      面板会一直停在旧错误上，用户看不到模型已经解释了（实测第 4 步踩到）；
		  //   2. 没有新回答、但有挂起错误 → 显示错误（summon 把对话区藏了，不镜像就完全看不到失败）；
		  //   3. 工具正在跑（含 cua/computer 关键字）→ 显示「正在看屏幕…」；
		  //   4. 否则显示最近一次回答。
		  if (errorText && errorText !== MING_TEA_SUMMON.lastError) {
		    MING_TEA_SUMMON.lastError = errorText;
		    MING_TEA_SUMMON.errorPending = true;
		  }
		  const freshAnswer = answerText !== "" && answerText !== MING_TEA_SUMMON.lastAnswer;
		  if (freshAnswer) {
		    MING_TEA_SUMMON.lastAnswer = answerText;
		    MING_TEA_SUMMON.errorPending = false;
		  }

		  let answerShown = "";
		  let tone = "";
		  if (MING_TEA_SUMMON.errorPending) {
		    answerShown = `操作失败：${mingTeaHumanizeError(MING_TEA_SUMMON.lastError)}`;
		    tone = "error";
		  } else if (answerText !== "") {
		    answerShown = answerText;
		    tone = "answer";
		  } else if (screenBusy) {
		    answerShown = "正在看屏幕…";
		    tone = "status";
		  }
		  if (MING_TEA_SUMMON.answer.textContent !== answerShown) {
		    MING_TEA_SUMMON.answer.textContent = answerShown;
		  }
		  if (tone === "") {
		    delete MING_TEA_SUMMON.answer.dataset.tone;
		  } else {
		    MING_TEA_SUMMON.answer.dataset.tone = tone;
		  }

		  // 档位：有审批=approval（要大而可点，否则审批卡在视口外点不到）；
		  //       有回答=answer；在听或胶囊有字=listening；否则只有宠物
		  const hasCapsule = (MING_TEA_SUMMON.capsule.textContent || "").trim() !== "";
		  const approvalPending = document.querySelector("[data-approval-key], [data-question-key]") !== null;
		  let stage = "pet";
		  const answerVisible = answerText !== "" || errorText !== "" || screenBusy;
		  if (approvalPending) stage = "approval";
		  else if (answerVisible) stage = "answer";
		  else if (voicePhase || hasCapsule) stage = "listening";
		  mingTeaSummonReport(stage);
		}

		function setLiteMode(on) {
		  try {
		    localStorage.setItem(MING_TEA_TWEAKS.lite.storageKey, on ? "1" : "0");
		  } catch {
		    /* 隐私模式等场景下写不进去：本次会话仍然生效 */
		  }
		  applyMingTeaTweaks();
		}

		// ── 首页场景卡 ────────────────────────────────────────────────────
		// 官方首页是四张开发向卡片（探索代码/构建工具/审查/修复），与铭荼的三场景
		// 定位不符。这里隐藏官方卡片块，换成铭荼自己的三场景卡。
		//
		// 写入输入框走官方同一条路径（读 codex-ui 实现所得）：
		//   currentSessionId → sessions.binding(id) → conversation.input.for(binding.ctx).setDraft(text)
		// 并复用官方的三种前置状态语义：未选工作区 / 忙碌 / 已有草稿。
		const MING_TEA_SCENE_CARDS = [
		  { scene: "办公", task: "整理文件与表格", prompt: "帮我把这个文件夹里的文件按类型整理好，并列出重命名建议。" },
		  { scene: "办公", task: "起草一份文档", prompt: "帮我起草一份【主题】的文档：先给结构大纲，我确认后再补内容。" },
		  { scene: "开发", task: "看懂一段代码", prompt: "帮我解释这段代码在做什么，并指出可能的问题。" },
		  { scene: "开发", task: "定位失败的测试", prompt: "运行项目测试，找出失败原因并给出修复建议。" },
		  { scene: "辅助学习", task: "讲清一个概念", prompt: "用一个生活化的例子给我讲清楚【概念】，然后出两道练习题。" },
		  { scene: "辅助学习", task: "读懂一条报错", prompt: "这条报错是什么意思？请分步骤告诉我该怎么排查。" },
		];

		// 由插件 apply(ctx) 注入：与官方卡片使用同一个 cordis 上下文
		let mingTeaCtx = null;

		/** 宿主半区同源 RPC：通道 `/ming-tea`，返回值是 `{ok, value}` 信封。
		 * 失败一律抛出带 code 的错误，调用方负责降级成提示气泡（不静默假装成功）。 */
		async function mingTeaHubRpc(endpoint, payload = {}) {
		  const ctx = mingTeaCtx;
		  const rpc = ctx?.connection?.rpc;
		  if (!rpc?.call) throw new Error("宿主连接不可用（connection 服务未注入）");
		  const result = await rpc.call("/ming-tea", endpoint, payload);
		  if (result === undefined || result === null) throw new Error("宿主没有返回结果");
		  if (result.ok === false) {
		    // 兜底：宿主现在把错误放进 value 里（见 rpc.mjs 的说明），这是老信封的兼容分支
		    const error = result.error ?? {};
		    const failure = new Error(error.message || "宿主调用失败");
		    failure.code = error.code || "hub/error";
		    throw failure;
		  }
		  const value = result.value ?? {};
		  if (value.ok === false) {
		    const failure = new Error(value.message || "站点请求失败");
		    failure.code = value.code || "hub/error";
		    failure.httpStatus = value.httpStatus;
		    throw failure;
		  }
		  return value.data;
		}

		function setMingTeaContext(ctx) {
		  mingTeaCtx = ctx;
		  // 调试钩子（仅 mt-debug=1 时暴露）：用于在浏览器里核对宿主服务与「创建会话」这条链路
		  try {
		    if (new URLSearchParams(location.search).get("mt-debug") === "1") {
		      window.__mingTeaDebug = {
		        ctx,
		        sessions: ctx?.sessions ?? null,
		        conversation: ctx?.conversation ?? null,
		        createSession: async (opts) => {
		          if (!ctx?.sessions?.create) return { ok: false, reason: "no sessions.create" };
		          const id = await ctx.sessions.create(opts ?? {});
		          return { ok: true, id: String(id) };
		        },
		        send: async (text) => {
		          if (!ctx?.conversation?.send) return { ok: false, reason: "no conversation.send" };
		          await ctx.conversation.send(text);
		          return { ok: true };
		        },
		        voiceRow: () => {
		          const el = document.querySelector("[class*='VoiceInput'], [class*='triggerAnchor'], [data-voice-activity]");
		          return el ? { found: true, cls: (el.className || "").toString().slice(0, 60) } : { found: false };
		        },
		      };
		    }
		  } catch {
		    /* 调试钩子失败不影响功能 */
		  }
		  // 调试钩子：便于在浏览器里核对宿主接入（只暴露我们自己的通道，不暴露整个 ctx）
		  if (typeof window !== "undefined") {
		    window.__mingTeaHub = { call: (endpoint, payload) => mingTeaHubRpc(endpoint, payload) };
		  }
		  // 上下文占用需要订阅会话投影，只在拿到 ctx 时绑一次（填充循环里重复绑会泄漏订阅）
		  mingTeaBindContextMeter();
		  applyMingTeaTweaks();
		  // 站点接入：读登录态 + 拉一次额度；之后每 15 分钟刷新（额度变化不是秒级的事）
		  void mingTeaBootstrapAccount();
		  void mingTeaRefreshTiers();
		  // 启动即排一次自愈：站点故障时到这里已经失败过，光等 15 分钟定时器太慢
		  mingTeaScheduleRecovery("hub/site-error");
		  // 登录状态下顺带同步一次路由结构：客户端升级（如新增思维档位声明）后无需重新登录即可生效
		  void mingTeaRefreshModels({ sync: true });
		  if (mingTeaQuotaTimer === null) {
		    mingTeaQuotaTimer = setInterval(() => void mingTeaRefreshQuota(), 15 * 60 * 1000);
		  }
		}

		function mingTeaCurrentSessionId(list) {
		  // 0.1.6 起 sessions.list 快照不再有 current 字段，现行会话靠 retainedBy 判定
		  for (const [id, entry] of Object.entries(list?.byId ?? {})) {
		    if ((entry?.retainedBy?.mainView ?? 0) > 0) return id;
		  }
		  return undefined;
		}

		/** 把提示词写入输入框；返回与官方一致的语义状态。 */
		function mingTeaPrefill(text) {
		  try {
		    const ctx = mingTeaCtx;
		    if (!ctx?.sessions || !ctx?.conversation) return "unavailable";
		    const list = ctx.sessions.list.getSnapshot();
		    const id = mingTeaCurrentSessionId(list);
		    if (id === undefined) return "workspace";
		    const binding = ctx.sessions.binding(id);
		    if (binding?.ctx === undefined) return "workspace";
		    const input = ctx.conversation.input.for(binding.ctx);
		    const state = input.state.getSnapshot();
		    if (state.phase !== "plain") return "busy";
		    if ((state.draft ?? "").trim() !== "" || (state.occurrences?.length ?? 0) > 0) return "draft";
		    input.setDraft(text);
		    return "ready";
		  } catch (error) {
		    return `error:${String((error && error.message) || error)}`;
		  }
		}

		function mingTeaFillSceneCards() {
		  const official = document.querySelector(".dcu-home-suggestions");
		  if (!official || !official.parentElement) return;
		  if (document.querySelector(".mt-scene-cards")) return;

		  const wrap = document.createElement("div");
		  wrap.className = "mt-scene-cards";
		  const groups = ["办公", "开发", "辅助学习"];
		  for (const scene of groups) {
		    const column = document.createElement("div");
		    column.className = "mt-scene-column";
		    const title = document.createElement("div");
		    title.className = "mt-scene-title";
		    title.textContent = `${scene}场景`;
		    column.appendChild(title);
		    for (const card of MING_TEA_SCENE_CARDS.filter((c) => c.scene === scene)) {
		      const btn = document.createElement("button");
		      btn.type = "button";
		      btn.className = "mt-scene-card";
		      btn.dataset.mingTeaScene = scene;
		      btn.textContent = card.task;
		      btn.addEventListener("click", () => {
		        const result = mingTeaPrefill(card.prompt);
		        if (result === "ready") return;
		        const hint =
		          result === "workspace"
		            ? "请先选择工作区，再选择任务。"
		            : result === "busy"
		              ? "当前会话正忙，稍后再试。"
		              : result === "draft"
		                ? "输入框已有内容，先清空或发送后再试。"
		                : "暂时无法写入输入框（插件未就绪）。";
		        mingTeaHint(hint, btn);
		      });
		      column.appendChild(btn);
		    }
		    wrap.appendChild(column);
		  }
		  official.parentElement.insertBefore(wrap, official.nextSibling);
		}

		// 窗口/标签标题：官方把产品名写死成 "DeepSeek Harness"（layout 包的 productTitle 常量），
		// 且会随会话名变化（`标题 — DeepSeek Harness`）。这里是唯一能改的层面：
		// 观察 <title> 变化并改写产品名部分，不改会话名。
		const MING_TEA_PRODUCT_NAME = "铭荼";

		function mingTeaRewriteTitle() {
		  const current = document.title;
		  if (!current) return;
		  const next = current
		    .replace(/DeepSeek Harness/g, MING_TEA_PRODUCT_NAME)
		    .replace(/^DSH$/, MING_TEA_PRODUCT_NAME);
		  if (next !== current) document.title = next;
		}

		function mingTeaWatchTitle() {
		  mingTeaRewriteTitle();
		  const titleEl = document.querySelector("title");
		  if (!titleEl || titleEl.dataset.mingTeaWatch === "1") return;
		  titleEl.dataset.mingTeaWatch = "1";
		  new MutationObserver(mingTeaRewriteTitle).observe(titleEl, {
		    childList: true,
		    characterData: true,
		    subtree: true,
		  });
		}

		function applyMingTeaTweaks() {
		  try {
		    const lite = liteModeEnabled();
		    document.documentElement.dataset.mingTeaLite = lite ? "1" : "0";

		    // summon 三态面板：首轮解析参数并打标记，之后每轮判定档位（宠物/听音/回答）
		    if (!MING_TEA_SUMMON.on && mingTeaSummonInit()) {
		      document.documentElement.dataset.mingTeaSummon = "1";
		      window.__mingTeaSummon = MING_TEA_SUMMON;
		    }
		    if (MING_TEA_SUMMON.on) mingTeaSummonTick();

		    // 先恢复"仅因清亮模式"而隐藏的元素，再统一重跑隐藏规则——
		    // 否则同时被侧栏规则命中的元素会在关闭清亮模式时被错误显示。
		    if (!lite) {
		      for (const el of Array.from(document.querySelectorAll('[data-ming-tea-lite-hidden="1"]'))) {
		        el.style.display = "";
		        delete el.dataset.mingTeaHidden;
		        delete el.dataset.mingTeaLiteHidden;
		      }
		    }

		      mingTeaFillContextMeter();
		    mingTeaFilterModelMenu();
		    // 「本会话信任」按钮：主窗口与 summon 面板都注入（同一个宿主逻辑，用户在哪都能用）
		    mingTeaInjectSessionTrust();
		    mingTeaProtectHubRoute();

		  for (const node of mingTeaLeafNodes(document.body)) {
		      const text = (node.nodeValue || "").trim();

		      const rule = MING_TEA_TWEAKS.rename.find(([from]) => from === text);
		      if (rule) {
		        node.nodeValue = node.nodeValue.replace(rule[0], rule[1]);
		        continue;
		      }

		      // 子串替换：只处理较短的叶子文本，避免误伤正文
		      if (text.length < 80) {
		        for (const [from, to] of MING_TEA_TWEAKS.replaceText) {
		          if (!text.includes(from)) continue;
		          node.nodeValue = node.nodeValue.replace(from, to);
		        }
		      }

		      const hideAnywhere = MING_TEA_TWEAKS.hideAnywhere.includes(text);
		      const hideInSidebar = MING_TEA_TWEAKS.hideInSidebar.includes(text);
		      const liteRule = lite ? MING_TEA_TWEAKS.lite.hide.find((r) => r.text === text) : undefined;
		      if (!hideAnywhere && !hideInSidebar && !liteRule) continue;

		      const host = mingTeaActionable(node.parentElement);
		      if (!host) continue;
		      if (liteRule?.hostClass && !(host.className || "").toString().includes(liteRule.hostClass)) continue;
		      // 仅侧栏规则要避开设置页里的同名入口（清亮模式则两处都隐藏，故不跳过）
		      if (!liteRule && hideInSidebar && !hideAnywhere && mingTeaInSettings(host)) continue;
		      if (host.dataset.mingTeaHidden === "1") continue;
		      host.dataset.mingTeaHidden = "1";
		      if (liteRule) host.dataset.mingTeaLiteHidden = "1";
		      host.style.display = "none";
		    }

		    for (const selector of MING_TEA_TWEAKS.hideContainers) {
		      for (const el of Array.from(document.querySelectorAll(selector))) {
		        if (el.dataset.mingTeaHidden === "1") continue;
		        el.dataset.mingTeaHidden = "1";
		        el.style.display = "none";
		      }
		    }

		    if (lite) {
		      for (const selector of MING_TEA_TWEAKS.lite.hideSelectors) {
		        for (const el of Array.from(document.querySelectorAll(selector))) {
		          if (el.dataset.mingTeaHidden === "1") continue;
		          el.dataset.mingTeaHidden = "1";
		          el.dataset.mingTeaLiteHidden = "1";
		          el.style.display = "none";
		        }
		      }
		    }

		    mingTeaFillFooter();
		    mingTeaFillSettingsNav();
		    mingTeaFillLiteToggle();
		    mingTeaFillSceneCards();
		    mingTeaWatchTitle();
		  } catch {
		    // 定制层失败绝不能影响页面：静默降级为"未定制"
		  }
		}

		// 「设置 → 常规」里的清亮模式开关：结构克隆真实设置行，保证与原生一致
		function mingTeaFillLiteToggle() {
		  const on = liteModeEnabled();

		  // 已注入过：只把开关外观同步到当前状态（切换后由 apply 统一刷新，
		  // 避免把状态更新散落在事件处理里）
		  const existing = document.querySelector(".mt-switch");
		  if (existing) {
		    existing.dataset.on = on ? "1" : "0";
		    existing.setAttribute("aria-checked", on ? "true" : "false");
		    return;
		  }
		  if (document.querySelector(".mt-lite-row")) return;

		  const sampleTitle = Array.from(document.querySelectorAll("*")).find(
		    (el) => el.children.length === 0 && (el.textContent || "").trim() === "性能与用量",
		  );
		  const rowText = sampleTitle?.closest('[class*="_rowText"]');
		  const row = rowText?.parentElement;
		  if (!row || !row.parentElement) return;

		  const clone = row.cloneNode(true);
		  clone.classList.add("mt-lite-row");
		  // 克隆体会连内联 display:none 一起复制，这里清掉并移除我们的标记
		  for (const el of Array.from(clone.querySelectorAll("[data-ming-tea-hidden]"))) {
		    el.style.display = "";
		    delete el.dataset.mingTeaHidden;
		    delete el.dataset.mingTeaLiteHidden;
		  }
		  const cloneText = clone.querySelector('[class*="_rowText"]');
		  if (cloneText) {
		    const title = cloneText.querySelector('[class*="_title"]');
		    const desc = cloneText.querySelector('[class*="_desc"]');
		    if (title) title.textContent = MING_TEA_TWEAKS.lite.label;
		    if (desc) desc.textContent = MING_TEA_TWEAKS.lite.desc;
		  }
		  // 去掉克隆行里原有的控件（下拉等），换成我们的开关
		  Array.from(clone.children).forEach((child) => {
		    if (child !== cloneText) child.remove();
		  });

		  const sw = document.createElement("button");
		  sw.type = "button";
		  sw.className = "mt-switch";
		  sw.setAttribute("role", "switch");
		  sw.setAttribute("aria-checked", on ? "true" : "false");
		  sw.dataset.on = on ? "1" : "0";
		  sw.setAttribute("aria-label", MING_TEA_TWEAKS.lite.label);
		  sw.innerHTML = '<span class="mt-switch-knob"></span>';
		  sw.addEventListener("click", () => setLiteMode(!liteModeEnabled()));
		  clone.appendChild(sw);

		  row.parentElement.insertBefore(clone, row.nextSibling);
		}

		// 侧栏页脚：账户（头像 + 名称）/ 剩余用量圆环 / 连接手机 / 设置
		// 顺序由主题里的 order 规则决定（官方「设置」在它自己的 slot 容器里，靠 DOM 顺序排不了）：
		//   账户（推到最左）→ 剩余用量 → 连接手机 → 设置。
		// 收起侧栏时整列改竖排、只留头像与圆环（否则宽按钮会被 36px 的轨道裁掉一半）。
		function mingTeaFillFooter() {
		  const seat = document.querySelector(".dcu-footer-actions");
		  if (!seat || seat.dataset.mingTeaFilled === "1") return;
		  seat.dataset.mingTeaFilled = "1";

		  const add = (id, label, iconHtml, hint, extraClass) => {
		    const btn = document.createElement("button");
		    btn.type = "button";
		    btn.className = `mt-foot-action ${extraClass || ""}`.trim();
		    btn.dataset.mingTeaAction = id;
		    btn.title = label;
		    btn.setAttribute("aria-label", label);
		    btn.innerHTML = `${iconHtml}<span class="mt-foot-label">${label}</span>`;
		    btn.addEventListener("click", (event) => {
		      event.preventDefault();
		      mingTeaHint(hint, btn);
		    });
		    seat.appendChild(btn);
		    return btn;
		  };

		  // 账户：圆头像 + 名称，点击打开登录面板（版式对齐用户给的参考图）
		  const account = document.createElement("button");
		  account.type = "button";
		  account.className = "mt-foot-action mt-foot-account";
		  account.dataset.mingTeaAction = "account";
		  account.title = MING_TEA_TWEAKS.footer.account.label;
		  account.setAttribute("aria-label", MING_TEA_TWEAKS.footer.account.label);
		  account.innerHTML =
		    `<span class="mt-foot-avatar">${MING_TEA_ICONS.avatar}</span>` +
		    `<span class="mt-foot-name">${MING_TEA_TWEAKS.footer.account.label}</span>`;
		  account.addEventListener("click", (event) => {
		    event.preventDefault();
		    if (mingTeaCtx === null) {
		      mingTeaHint(MING_TEA_TWEAKS.footer.account.hint, account);
		      return;
		    }
		    void mingTeaOpenAccountPanel();
		  });
		  seat.appendChild(account);
		  mingTeaRenderAccountName();

		  const phone = add("phone", MING_TEA_TWEAKS.footer.phone.label, MING_TEA_ICONS.phone, MING_TEA_TWEAKS.footer.phone.hint, "mt-foot-action--icon");
		  phone.querySelector(".mt-foot-label")?.remove();

		  const quota = document.createElement("button");
		  quota.type = "button";
		  quota.className = "mt-foot-action mt-foot-action--icon mt-foot-quota";
		  quota.dataset.mingTeaAction = "quota";
		  // 环与文字分两层：遮罩只作用于环，中心文字才不会被一起遮掉
		  quota.innerHTML =
		    `<span class="mt-quota-ring" style="--mt-quota: 0">` +
		    `<span class="mt-quota-arc"></span>` +
		    `<span class="mt-quota-value">—</span>` +
		    `</span>`;
		  quota.addEventListener("click", async (event) => {
		    event.preventDefault();
		    if (mingTeaCtx === null) {
		      mingTeaHint(MING_TEA_TWEAKS.footer.quota.hint, quota);
		      return;
		    }
		    const usage = await mingTeaRefreshQuota();
		    if (usage.percent === null) {
		      mingTeaHint(
		        usage.error
		          ? `读取额度失败：${usage.error}`
		          : usage.signedIn
		            ? "站点没有返回可用的额度明细。"
		            : MING_TEA_ACCOUNT.expired
		              ? "登录已过期，点左侧账户重新登录。"
		              : "尚未登录站点账号，点左侧账户登录后即可显示额度。",
		        quota,
		      );
		      return;
		    }
		    mingTeaHint(`剩余用量：${usage.percent}%（${usage.detail}）`, quota);
		  });
		  quota.addEventListener("mouseenter", () => mingTeaShowQuotaTip(quota));
		  quota.addEventListener("mouseleave", mingTeaHideQuotaTip);
		  quota.addEventListener("focus", () => mingTeaShowQuotaTip(quota));
		  quota.addEventListener("blur", () => mingTeaHideQuotaTip());
		  seat.appendChild(quota);
		  mingTeaRenderQuota();
		}

		// ── 商店：安装前的二次确认 ────────────────────────────────────────────
		// 官方商店只在 tier !== "verified" 时弹它自己的内嵌确认，verified 直接开装。
		// 我们给**所有**安装/更新动作加一道自己的确认框，展示名称/版本/来源/许可/校验信息。
		//
		// 拦截方式：捕获阶段监听 document 上的点击。React 18 把委托监听挂在 root 容器上，
		// 捕获阶段 stopPropagation 就能让事件到不了 React，商店的 onClick 不会执行；
		// 用户点「确认」后再用 button.click() 放行一次（bypass 标记防止递归拦截）。
		// 非 verified 条目放行后商店仍会弹它自己的确认——这是刻意的：那一步会写入
		// acknowledged 应答，属于宿主侧强制的确认，我们不复刻、也不绕过。
		let mingTeaStoreBypass = false;

		function mingTeaStoreCardFacts(button) {
		  const card = button.closest("[data-shop-entry]");
		  if (!card) return null;
		  const pick = (suffix) => {
		    const el = card.querySelector(`[class*="_${suffix}"]`);
		    return el ? (el.textContent || "").trim() : "";
		  };
		  return {
		    name: card.getAttribute("data-shop-entry") || "",
		    version: card.getAttribute("data-card-version") || "",
		    tier: card.getAttribute("data-tier") || "community",
		    author: pick("author") || card.getAttribute("data-shop-author") || "",
		    // 体积优先读属性，属性不在卡片根上时退回可见文本（形如「37.5 kB」）
		    size:
		      card.getAttribute("data-shop-size") ||
		      (/\\d[\d.]*\s*(kB|MB|GB)/.exec(card.innerText || "")?.[0] ?? ""),
		    source: card.hasAttribute("data-shop-source-github")
		      ? "GitHub"
		      : card.hasAttribute("data-shop-source-npm")
		        ? "npm"
		        : "目录",
		    action: button.hasAttribute("data-shop-update") ? "更新" : "安装",
		  };
		}

		const MING_TEA_TIER_LABEL = {
		  verified: "已核验来源",
		  "verified-stale": "核验已过期",
		  community: "社区提交（未核验）",
		};

		/** 从商店自己的目录里补齐 license / 仓库 / 校验值；失败就少显示几行，不阻塞确认。 */
		async function mingTeaStoreEnrich(name) {
		  try {
		    const shop = mingTeaCtx?.get?.("remote.shop");
		    if (!shop?.catalog) return null;
		    const raw = await Promise.race([
		      shop.catalog({ refresh: false }),
		      new Promise((resolve) => setTimeout(() => resolve(null), 1500)),
		    ]);
		    const payload = raw?.value ?? raw;
		    const list = payload?.plugins ?? payload?.value?.plugins;
		    if (!Array.isArray(list)) return null;
		    const hit = list.find((p) => p?.name === name);
		    if (!hit) return null;
		    const digest = hit.sha256 || hit.integrity || "";
		    return {
		      license: hit.license || "",
		      repository: hit.repository || "",
		      integrity: String(digest).replace(/^sha512-/, "").slice(0, 24),
		    };
		  } catch {
		    return null;
		  }
		}

		function mingTeaCloseStoreConfirm() {
		  document.querySelector(".mt-store-confirm")?.remove();
		  document.removeEventListener("keydown", mingTeaStoreConfirmKey, true);
		}

		function mingTeaStoreConfirmKey(event) {
		  if (event.key === "Escape") {
		    event.preventDefault();
		    mingTeaCloseStoreConfirm();
		  }
		}

		function mingTeaStoreConfirm(button) {
		  const facts = mingTeaStoreCardFacts(button);
		  if (!facts) return;
		  document.querySelector(".mt-store-confirm")?.remove();

		  const wrap = document.createElement("div");
		  wrap.className = "mt-store-confirm";
		  const row = (label, value) =>
		    value ? `<div class="mt-store-row"><dt>${label}</dt><dd>${value}</dd></div>` : "";
		  wrap.innerHTML =
		    `<div class="mt-store-confirm-backdrop"></div>` +
		    `<div class="mt-store-confirm-panel" role="dialog" aria-modal="true" aria-label="安装前确认">` +
		    `<h3>确认${facts.action}这个插件？</h3>` +
		    `<p class="mt-store-confirm-name">${facts.name}${facts.version ? ` <span>v${facts.version}</span>` : ""}</p>` +
		    `<dl class="mt-store-confirm-facts">` +
		    row("来源", facts.source) +
		    row("信任级别", MING_TEA_TIER_LABEL[facts.tier] || facts.tier) +
		    row("作者", facts.author) +
		    row("许可", `<span class="mt-store-license">正在读取…</span>`) +
		    row("仓库", `<span class="mt-store-repo"></span>`) +
		    row("校验值", `<code class="mt-store-integrity"></code>`) +
		    row("体积", facts.size) +
		    `</dl>` +
		    `<p class="mt-store-confirm-note">第三方插件由社区作者提供，铭荼不对其安全性作担保；` +
		    `安装后会随应用一起运行。确认前请留意来源与许可。</p>` +
		    `<div class="mt-store-confirm-actions">` +
		    `<button type="button" class="mt-store-cancel">取消</button>` +
		    `<button type="button" class="mt-store-ok">确认${facts.action}</button>` +
		    `</div></div>`;
		  document.body.appendChild(wrap);
		  // 直接置位：曾用 requestAnimationFrame 延迟置位，实测回调未执行，
		  // 结果弹窗在 DOM 里但 opacity 停在 0（看不见、点不到）。
		  wrap.dataset.open = "1";

		  const licenseEl = wrap.querySelector(".mt-store-license");
		  const repoEl = wrap.querySelector(".mt-store-repo");
		  const integrityEl = wrap.querySelector(".mt-store-integrity");
		  const rowOf = (el) => el?.closest(".mt-store-row");
		  const setRow = (el, text) => {
		    const r = rowOf(el);
		    if (!text) {
		      r?.remove();
		      return;
		    }
		    el.textContent = text;
		  };
		  void mingTeaStoreEnrich(facts.name).then((extra) => {
		    if (!wrap.isConnected) return;
		    if (!extra) {
		      setRow(licenseEl, "");
		      setRow(repoEl, "");
		      setRow(integrityEl, "");
		      return;
		    }
		    setRow(licenseEl, extra.license);
		    setRow(repoEl, extra.repository);
		    setRow(integrityEl, extra.integrity ? `${extra.integrity}…` : "");
		  });

		  wrap.querySelector(".mt-store-confirm-backdrop").addEventListener("click", mingTeaCloseStoreConfirm);
		  wrap.querySelector(".mt-store-cancel").addEventListener("click", mingTeaCloseStoreConfirm);
		  const ok = wrap.querySelector(".mt-store-ok");
		  ok.addEventListener("click", () => {
		    mingTeaCloseStoreConfirm();
		    mingTeaStoreBypass = true;
		    try {
		      button.click();
		    } finally {
		      mingTeaStoreBypass = false;
		    }
		  });
		  wrap.querySelector(".mt-store-cancel").focus();
		  document.addEventListener("keydown", mingTeaStoreConfirmKey, true);
		}

		function mingTeaStoreClickGuard(event) {
		  if (mingTeaStoreBypass) return;
		  const target = event.target instanceof Element ? event.target.closest("[data-shop-install],[data-shop-update]") : null;
		  if (!target) return;
		  event.preventDefault();
		  event.stopPropagation();
		  event.stopImmediatePropagation();
		  mingTeaStoreConfirm(target);
		}

		// ── 输入框旁的「上下文占用」指示器 ────────────────────────────────────
		// 数据源是官方 token-meter 的 contextPressure 投影（provider 上报的 prompt 用量 +
		// 后续增量，分母是模型声明的上下文容量），口径与官方 ContextMeter 一致：
		//   used = projectedTokens ?? pressureTokens；percent = round(used / contextWindow * 100)
		// 官方那个圆环只在有会话且已产生过用量时才渲染，所以新会话里看不到东西——
		// 我们补一个**始终可见**的指示器：没数据时显示「—」，并说明首次回复后出现。
		const MING_TEA_CONTEXT = { boundId: undefined, offFace: null, value: null };

		function mingTeaContextOccupancy(pressure) {
		  const used = pressure?.projectedTokens ?? pressure?.pressureTokens;
		  const window = pressure?.contextWindow;
		  if (typeof used !== "number" || typeof window !== "number" || window <= 0) return null;
		  return {
		    percent: Math.max(0, Math.min(100, Math.round((used / window) * 100))),
		    used,
		    window,
		  };
		}

		function mingTeaRenderContext(value) {
		  MING_TEA_CONTEXT.value = value;
		  const node = document.querySelector(".mt-ctx-meter");
		  if (!node) return;
		  const fmt = (n) => (n >= 1000 ? `${Math.round(n / 1000)}K` : `${n}`);
		  if (!value) {
		    node.dataset.hasValue = "0";
		    node.title = "上下文占用会在首次回复后显示（数据来自模型上报的提示用量）";
		    node.querySelector(".mt-ctx-text").textContent = "上下文 —";
		    node.querySelector(".mt-ctx-ring").style.setProperty("--mt-ctx", "0");
		    return;
		  }
		  node.dataset.hasValue = "1";
		  node.title = `上下文已用 ${value.percent}%（约 ${fmt(value.used)} / ${fmt(value.window)} tokens）`;
		  node.querySelector(".mt-ctx-text").textContent = `上下文 ${value.percent}%`;
		  node.querySelector(".mt-ctx-ring").style.setProperty("--mt-ctx", String(value.percent));
		}

		function mingTeaBindContextMeter() {
		  const ctx = mingTeaCtx;
		  if (!ctx?.sessions?.list?.subscribe) return;
		  const resolve = () => {
		    let id;
		    try {
		      id = mingTeaCurrentSessionId(ctx.sessions.list.getSnapshot());
		    } catch {
		      id = undefined;
		    }
		    if (id === MING_TEA_CONTEXT.boundId) return;
		    MING_TEA_CONTEXT.boundId = id;
		    if (MING_TEA_CONTEXT.offFace) {
		      try {
		        MING_TEA_CONTEXT.offFace();
		      } catch {
		        /* 会话已销毁 */
		      }
		      MING_TEA_CONTEXT.offFace = null;
		    }
		    if (id === undefined) {
		      mingTeaRenderContext(null);
		      return;
		    }
		    let face;
		    try {
		      face = ctx.sessions.binding(id)?.session?.projections?.faceOf?.("contextPressure");
		    } catch {
		      face = undefined;
		    }
		    if (!face?.subscribe || !face?.getSnapshot) {
		      mingTeaRenderContext(null);
		      return;
		    }
		    const apply = () => {
		      try {
		        mingTeaRenderContext(mingTeaContextOccupancy(face.getSnapshot()));
		      } catch {
		        mingTeaRenderContext(null);
		      }
		    };
		    MING_TEA_CONTEXT.offFace = face.subscribe(apply);
		    apply();
		  };
		  try {
		    ctx.sessions.list.subscribe(resolve);
		  } catch {
		    /* 列表订阅不可用时退化为一次性解析 */
		  }
		  resolve();
		}

		/** 找出真正的输入卡片：它内部含 `_input` 元素（首页建议卡里没有），
		 * 避免把指示器挂到首页的场景卡上。 */
		function mingTeaComposerCard() {
		  const stack = document.querySelector('[class*="_composerStack"]');
		  if (!stack) return undefined;
		  return [...stack.querySelectorAll('[class*="_card"]')].find((c) =>
		    c.querySelector('[class*="_input"]'),
		  );
		}

		/** 指示器挂在输入卡片内部、底部工具栏之下 —— 即「输入框旁」。
		 * 卡片由 React 管理，重渲染可能把它清掉，所以由填充循环幂等补回。 */
		function mingTeaFillContextMeter() {
		  const card = mingTeaComposerCard();
		  if (!card) return;
		  // 目标是「模型选择 + 思考强度」那一栏（trailing 里的 standardControls），
		  // 放在它前面：`… 上下文 18% [模型] [强度滑杆] [发送]`
		  const trailing = card.querySelector('[class*="_trailing"]');
		  if (!trailing) return;
		  const node = card.querySelector(".mt-ctx-meter") ?? document.querySelector(".mt-ctx-meter");
		  if (node && trailing.contains(node)) return;
		  node?.remove();
		  const fresh = document.createElement("div");
		  fresh.className = "mt-ctx-meter";
		  fresh.dataset.hasValue = "0";
		  fresh.innerHTML =
		    `<span class="mt-ctx-ring"><span class="mt-ctx-arc"></span></span>` +
		    `<span class="mt-ctx-text">上下文 —</span>`;
		  trailing.insertBefore(fresh, trailing.firstChild);
		  mingTeaRenderContext(MING_TEA_CONTEXT.value);
		}


		/** 模型选择器里只保留「有意义的」那一档：
		 * - 用户自己外接的 provider（模型页可增删的）存在时，隐藏站点自带的与官方内置的
		 *   （否则列表里会同时出现「铭荼 Auto / DeepSeek 官方 / 用户自己的」三套，普通用户分不清）；
		 * - 没有外接时，仍显示站点与官方内置的，不把用户的选择拿走。
		 *
		 * 实现方式：读官方状态（不碰 React），只在有外接 provider 时把站点与官方那两组**隐藏**——
		 * 是隐藏不是删除，用户清空外接后它们会自己回来。失败一律不隐藏（宁可不生效也不误伤）。 */
		const MING_TEA_HIDDEN_PROVIDER_KEYS = ["ming-tea-hub", "deepseek-official"];
		const MING_TEA_MODEL_MENU_FLAG = "mingTeaMenuFiltered";

		function mingTeaFilterModelMenu() {
		  const menus = [...document.querySelectorAll('[role="menu"],[role="dialog"]')].filter((el) =>
		    /^(模型|Model)/.test((el.innerText || "").trim()),
		  );
		  for (const menu of menus) {
		    const items = [...menu.querySelectorAll('[role="menuitem"],[role="option"],button,li')];
		    if (items.length === 0) continue;
		    const labels = items.map((el) => (el.innerText || "").trim()).filter((text) => text !== "");
		    const hasExternal = labels.some((text) => !/DeepSeek|铭荼|站点|公司|内置/.test(text));
		    if (!hasExternal) {
		      delete menu.dataset[MING_TEA_MODEL_MENU_FLAG];
		      for (const item of items) item.style.display = "";
		      continue;
		    }
		    menu.dataset[MING_TEA_MODEL_MENU_FLAG] = "1";
		    for (const item of items) {
		      const text = (item.innerText || "").trim();
		      const isBundled = /DeepSeek|铭荼|站点/.test(text);
		      if (isBundled) item.style.display = "none";
		    }
		  }
		}

		/** 保护站内路由：设置 → 模型 里我们的那条（`ming-tea-hub`）不能被删除。
		 * 做法：在模型页对「路由卡片」里的删除类控件做**禁用 + 提示**，并在捕获阶段拦掉直接点击。
		 * 只按文本/属性命中我们那一张卡（卡上一定有 displayName「铭荼（站点）」）。 */
		function mingTeaProtectHubRoute() {
		  const cards = [...document.querySelectorAll("li,section,article,div")].filter((el) => {
		    const text = (el.innerText || "").slice(0, 200);
		    return text.includes("铭荼（站点）") && el.querySelector("button");
		  });
		  for (const card of cards) {
		    // 取最内层那张卡（避免把整页容器也当成卡）
		    const inner = [...card.querySelectorAll("button")]
		      .map((b) => b.closest("li,section,article,div"))
		      .filter((el) => el && (el.innerText || "").includes("铭荼（站点）"))
		      .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length)[0];
		    const scope = inner ?? card;
		    const buttons = [...scope.querySelectorAll("button")];
		    for (const button of buttons) {
		      const label = `${button.getAttribute("aria-label") || ""} ${button.title || ""} ${(button.innerText || "").trim()}`;
		      const isDestructive = /删除|移除|remove|delete|卸载|uninstall/i.test(label);
		      if (!isDestructive || button.dataset.mingTeaProtected === "1") continue;
		      button.dataset.mingTeaProtected = "1";
		      button.disabled = true;
		      button.setAttribute("aria-disabled", "true");
		      button.title = "本站点接入的模型不能删除（登录后自动维护）";
		    }
		  }
		}

		function mingTeaProtectHubRouteClick(event) {
		  const button = event.target instanceof Element ? event.target.closest("button") : null;
		  if (!button?.dataset?.mingTeaProtected) return;
		  event.preventDefault();
		  event.stopPropagation();
		  event.stopImmediatePropagation();
		}

		/** 到期时间的可读形式。 */
		function mingTeaExpiryText(expiresAt) {
		  if (typeof expiresAt !== "number") return "";
		  const left = expiresAt - Date.now();
		  if (left <= 0) return "已过期";
		  const days = Math.floor(left / 86400000);
		  if (days > 0) return `${days} 天后到期`;
		  const hours = Math.floor(left / 3600000);
		  if (hours > 0) return `${hours} 小时后到期`;
		  return `约 ${Math.max(1, Math.round(left / 60000))} 分钟后失效`;
		}

		/** 页脚用户名：登录后显示站点用户名，未登录显示「账户」。 */
		/** 站点把会员到期放在 user.member_expires_at（可能是 ISO 字符串或时间戳）。 */
		function mingTeaParseMemberExpiry(user) {
		  const raw = user?.member_expires_at;
		  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
		  if (typeof raw === "string" && raw !== "") {
		    const parsed = Date.parse(raw);
		    if (!Number.isNaN(parsed)) return parsed;
		  }
		  return undefined;
		}

		/** 用户名旁的档位徽标：只有付费档才出现（Plus / Pro / Ultra），悬浮显示到期日。
		 * 站点把档位放在 /usage 的 plan 里；免费档不显示任何东西，不占位置。 */
		function mingTeaRenderAccountBadge() {
		  const button = document.querySelector('[data-ming-tea-action="account"]');
		  if (!button) return;
		  const existing = button.querySelector(".mt-foot-badge");
		  const plan = MING_TEA_ACCOUNT.plan;
		  const isPaid = MING_TEA_ACCOUNT.signedIn && plan && /plus|pro|ultra/i.test(String(plan.key ?? plan.name ?? ""));
		  if (!isPaid) {
		    existing?.remove();
		    return;
		  }
		  const label = String(plan.name || plan.key).toUpperCase();
		  const badge = existing ?? document.createElement("span");
		  badge.className = "mt-foot-badge";
		  badge.textContent = label;
		  badge.dataset.plan = String(plan.key ?? label).toLowerCase();
		  const expiresAt =
		    plan.expiresAt ??
		    (typeof MING_TEA_ACCOUNT.memberExpiresAt === "number" ? MING_TEA_ACCOUNT.memberExpiresAt : undefined);
		  const expiry = typeof expiresAt === "number" ? new Date(expiresAt).toLocaleDateString("zh-CN") : "";
		  badge.title = expiry
		    ? `${plan.name || label} · ${expiry} 到期`
		    : `${plan.name || label}（站点未返回到期时间）`;
		  if (!existing) button.querySelector(".mt-foot-avatar")?.after(badge);
		}

		function mingTeaRenderAccountName() {
		  const node = document.querySelector('[data-ming-tea-action="account"] .mt-foot-name');
		  mingTeaRenderAccountBadge();
		  if (!node) return;
		  const user = MING_TEA_ACCOUNT.user;
		  // 站点返回的是 username（还有 id 是数字）；顺序错了会把页脚显示成 「112」——实测踩过
		  const name =
		    MING_TEA_ACCOUNT.signedIn && user
		      ? user.name || user.username || user.nickname || user.displayName || user.email || "已登录"
		      : "账户";
		  node.textContent = name;
		  const button = node.closest("button");
		  if (button) {
		    const title = MING_TEA_ACCOUNT.signedIn
		      ? `已登录：${name}${MING_TEA_ACCOUNT.expiresAt ? `（${mingTeaExpiryText(MING_TEA_ACCOUNT.expiresAt)}）` : ""}`
		      : MING_TEA_ACCOUNT.expired
		        ? "登录已过期，点击重新登录"
		        : "未登录站点账号，点击登录";
		    button.title = title;
		    button.setAttribute("aria-label", title);
		  }
		}

		/** 用量圆环：只更新已注入的节点，不重建（页脚只注入一次）。 */
		function mingTeaRenderQuota() {
		  const quota = document.querySelector('[data-ming-tea-action="quota"]');
		  if (!quota) return;
		  const hasValue = typeof MING_TEA_QUOTA.percent === "number";
		  quota.dataset.hasValue = hasValue ? "1" : "0";
		  const ring = quota.querySelector(".mt-quota-ring");
		  const value = quota.querySelector(".mt-quota-value");
		  if (ring) ring.style.setProperty("--mt-quota", String(hasValue ? MING_TEA_QUOTA.percent : 0));
		  if (value) value.textContent = hasValue ? String(MING_TEA_QUOTA.percent) : "—";
		  const lines = [];
		  if (MING_TEA_QUOTA.free) lines.push(`免费层：本月剩余 ${MING_TEA_QUOTA.free.remaining}/${MING_TEA_QUOTA.free.limit} 次`);
		  if (MING_TEA_QUOTA.paid) lines.push(`付费层：剩余 ¥${MING_TEA_QUOTA.paid.remaining.toFixed(2)} / 额度 ¥${MING_TEA_QUOTA.paid.budget.toFixed(0)}`);
		  const title = hasValue
		    ? `剩余用量：${MING_TEA_QUOTA.percent}%${lines.length > 0 ? `\n${lines.join("\n")}` : MING_TEA_QUOTA.detail ? `（${MING_TEA_QUOTA.detail}）` : ""}`
		    : MING_TEA_QUOTA.error
		      ? "读取额度失败，点击重试"
		      : MING_TEA_ACCOUNT.expired
		        ? "登录已过期，点击账户重新登录"
		        : MING_TEA_QUOTA.signedIn
		          ? "已登录，点击刷新额度"
		          : "未登录站点账号，点击查看说明";
		  quota.title = title;
		  quota.setAttribute("aria-label", title);
		}

		/** 用量悬浮卡：把免费层与付费层的剩余都列出来（付费用户两层都在响应里）。
		 * 用自绘卡片而不是原生 title：原生 tooltip 有延迟、也不能排版多行。 */
		function mingTeaShowQuotaTip(anchor) {
		  document.querySelector(".mt-quota-tip")?.remove();
		  const tip = document.createElement("div");
		  tip.className = "mt-quota-tip";
		  const add = (text, strong = false) => {
		    const line = document.createElement("div");
		    line.className = strong ? "mt-quota-tip-strong" : "";
		    line.textContent = text;
		    tip.appendChild(line);
		  };
		  const tier = MING_TEA_QUOTA.tierName || (MING_TEA_QUOTA.tier === "paid" ? "付费" : MING_TEA_QUOTA.tier === "free" ? "免费" : "");
		  if (MING_TEA_ACCOUNT.signedIn) {
		    add(`账号：${MING_TEA_ACCOUNT.user?.username || MING_TEA_ACCOUNT.user?.name || "已登录"}${tier ? `（${tier}）` : ""}`, true);
		  } else {
		    add("尚未登录站点账号", true);
		  }
		  if (MING_TEA_QUOTA.free) {
		    add(`免费层：本月剩余 ${MING_TEA_QUOTA.free.remaining}/${MING_TEA_QUOTA.free.limit} 次`);
		  }
		  if (MING_TEA_QUOTA.autoFree && MING_TEA_QUOTA.autoFree.configured !== false) {
		    const a = MING_TEA_QUOTA.autoFree;
		    const has = typeof a.remaining === "number" && typeof a.limit === "number";
		    add(
		      has
		        ? `Auto 赠送：${a.remaining}/${a.limit} 次${a.exhausted ? "（已用完，超出按象征价从额度扣）" : ""}`
		        : `Auto 赠送：本档已配置${typeof a.limit === "number" ? ` ${a.limit} 次/月` : ""}`,
		    );
		  }
		  if (MING_TEA_QUOTA.paid) {
		    const used = typeof MING_TEA_QUOTA.paid.used === "number" ? `，已用 ¥${MING_TEA_QUOTA.paid.used.toFixed(2)}` : "";
		    add(`付费层：剩余 ¥${MING_TEA_QUOTA.paid.remaining.toFixed(2)}${used} / 额度 ¥${MING_TEA_QUOTA.paid.budget.toFixed(0)}`);
		  }
		  if (!MING_TEA_QUOTA.free && !MING_TEA_QUOTA.paid) {
		    add(MING_TEA_QUOTA.error ? `读取失败：${MING_TEA_QUOTA.error}` : MING_TEA_QUOTA.signedIn ? "站点未返回额度明细" : "登录后显示额度");
		  }
		  if (MING_TEA_ACCOUNT.signedIn && !MING_TEA_QUOTA.paid && MING_TEA_QUOTA.tier === "free") {
		    add("付费层：未开通（开通后这里会同时显示两层剩余）");
		  }
		  document.body.appendChild(tip);
		  const rect = anchor.getBoundingClientRect();
		  tip.style.left = `${Math.max(8, Math.min(rect.left + rect.width / 2, window.innerWidth - 8))}px`;
		  tip.style.top = `${rect.top - 8}px`;
		  requestAnimationFrame(() => (tip.dataset.visible = "1"));
		}

		function mingTeaHideQuotaTip() {
		  const tip = document.querySelector(".mt-quota-tip");
		  if (!tip) return;
		  tip.remove();
		}

		/** 把一次 quota 结果写进本地缓存（不发请求、不渲染）。
		 * 抽出来的原因：设置页的「用量看板」用 `usage.board` 一次请求同时拿到看板模型与归一后的
		 * quota，两边必须同一个口径 —— 让页脚圆环复用这次结果，而不是再打一次 `quota.get`。 */
		function mingTeaApplyQuota(usage) {
		  if (usage === null || typeof usage !== "object") return MING_TEA_QUOTA;
		  MING_TEA_QUOTA.signedIn = usage.signedIn === true;
		  MING_TEA_QUOTA.tier = usage.tier ?? null;
		  MING_TEA_QUOTA.tierName = typeof usage.tierName === "string" ? usage.tierName : MING_TEA_QUOTA.tierName;
		  MING_TEA_QUOTA.detail = typeof usage.detail === "string" ? usage.detail : "";
		  MING_TEA_QUOTA.availableTiers = Array.isArray(usage.availableTiers) ? usage.availableTiers : [];
		  MING_TEA_QUOTA.free = usage.free ?? null;
		  MING_TEA_QUOTA.paid = usage.paid ?? null;
		  MING_TEA_QUOTA.autoFree = usage.autoFree ?? null;
		  MING_TEA_QUOTA.plan = usage.plan ?? null;
		  if (usage.plan) MING_TEA_ACCOUNT.plan = usage.plan;
		  MING_TEA_QUOTA.plan = usage.plan ?? MING_TEA_QUOTA.plan;
		  MING_TEA_QUOTA.catalog = Array.isArray(usage.catalog) ? usage.catalog : MING_TEA_QUOTA.catalog;
		  MING_TEA_QUOTA.percent = typeof usage.percent === "number" ? usage.percent : null;
		  MING_TEA_QUOTA.error = "";
		  MING_TEA_QUOTA.loaded = true;
		  mingTeaRecoveryAttempt = 0; // 站点已恢复，重置退避计数
		  if (usage.expired === true) {
		    MING_TEA_ACCOUNT.signedIn = false;
		    MING_TEA_ACCOUNT.expired = true;
		    MING_TEA_ACCOUNT.user = null;
		    mingTeaRenderAccountName();
		  }
		  return MING_TEA_QUOTA;
		}

		/** 拉一次额度。失败不改动任何「看起来成功」的状态，只记录错误。 */
		async function mingTeaRefreshQuota() {
		  if (mingTeaCtx === null) return MING_TEA_QUOTA;
		  try {
		    const usage = await mingTeaHubRpc("quota.get");
		    mingTeaApplyQuota(usage);
		    mingTeaRenderAccountBadge();
		  } catch (error) {
		    MING_TEA_QUOTA.percent = null;
		    MING_TEA_QUOTA.detail = "";
		    MING_TEA_QUOTA.error =
		      error?.code === "hub/site-error"
		        ? "站点暂时不可用，稍后自动重试"
		        : String(error?.message ?? error);
		    mingTeaScheduleRecovery(error?.code);
		  }
		  mingTeaRenderQuota();
		  return MING_TEA_QUOTA;
		}

		function mingTeaAccountNote(panel, text, tone = "") {
		  const note = panel.querySelector(".mt-account-note");
		  if (!note) return;
		  note.textContent = text;
		  note.dataset.tone = tone;
		}

		function mingTeaAccountRenderBody(panel) {
		  const body = panel.querySelector(".mt-account-body");
		  const actions = panel.querySelector(".mt-account-actions");
		  if (!body || !actions) return;
		  const pending = MING_TEA_ACCOUNT.pending;

		  if (MING_TEA_ACCOUNT.signedIn) {
		    const user = MING_TEA_ACCOUNT.user ?? {};
		    const name = user.name || user.username || user.nickname || user.displayName || user.email || "已登录";
		    const quotaParts = [];
		    if (MING_TEA_QUOTA.free) quotaParts.push(`免费层 ${MING_TEA_QUOTA.free.remaining}/${MING_TEA_QUOTA.free.limit} 次`);
		    if (MING_TEA_QUOTA.autoFree && MING_TEA_QUOTA.autoFree.configured !== false) {
		      const a = MING_TEA_QUOTA.autoFree;
		      quotaParts.push(
		        typeof a.remaining === "number" && typeof a.limit === "number"
		          ? `Auto 赠送 ${a.remaining}/${a.limit} 次`
		          : "Auto 赠送：本档已配置",
		      );
		    }
		    if (MING_TEA_QUOTA.paid) quotaParts.push(`付费层 ¥${MING_TEA_QUOTA.paid.remaining.toFixed(2)}`);
		    const quotaLine = MING_TEA_QUOTA.loaded
		      ? quotaParts.length > 0
		        ? quotaParts.join(" · ")
		        : MING_TEA_QUOTA.detail || "站点未返回额度明细"
		      : "正在读取额度…";
		    const tierLabel =
		      MING_TEA_QUOTA.tierName || MING_TEA_MODELS.tierName || (MING_TEA_QUOTA.tier === "paid" ? "付费" : MING_TEA_QUOTA.tier === "free" ? "免费" : "");
		    const available = MING_TEA_MODELS.list.filter((m) => m.available !== false);
		    const modelsLine = MING_TEA_MODELS.loaded
		      ? available.length === 0
		        ? "站点未返回可选模型（用 auto）"
		        : available.length === 1 && available[0].id === "auto"
		          ? "模型：Auto（站点自动选路，掉线自动切换）"
		          : `可用模型：${available.map((m) => m.id).join("、")}`
		      : "";
		    body.innerHTML =
		      `<p class="mt-account-name"></p><p class="mt-account-meta mt-account-quota"></p>` +
		      `<p class="mt-account-meta mt-account-tier"></p><p class="mt-account-meta mt-account-models"></p>`;
		    body.querySelector(".mt-account-name").textContent = String(name);
		    body.querySelector(".mt-account-quota").textContent =
		      `${mingTeaExpiryText(MING_TEA_ACCOUNT.expiresAt)} · ${quotaLine}`.trim();
		    body.querySelector(".mt-account-tier").textContent = tierLabel ? `档位：${tierLabel}` : "";
		    body.querySelector(".mt-account-models").textContent = modelsLine;
		    const upgrade = mingTeaUpgradeLine();
		    if (upgrade) {
		      const extra = document.createElement("p");
		      extra.className = "mt-account-meta mt-account-upgrade";
		      extra.textContent = upgrade;
		      body.appendChild(extra);
		    }
		    actions.innerHTML =
		      '<button type="button" class="mt-store-cancel" data-act="quota">刷新用量</button>' +
		      '<button type="button" class="mt-store-cancel" data-act="signout">退出登录</button>' +
		      '<button type="button" class="mt-store-ok" data-act="close">完成</button>';
		    return;
		  }

		  if (pending) {
		    // 待授权：正常情况浏览器已经打开了，这里只说状态 + 兜底入口
		    const opened = pending.opened !== false;
		    body.innerHTML =
		      `<p class="mt-account-meta mt-account-status">${
		        opened
		          ? "已在系统浏览器打开授权页 —— 账号已登录的话会自动完成授权（跨网络时需要你确认一次）。"
		          : "没能自动打开浏览器，请点下面的按钮或手动复制链接。"
		      }</p>` +
		      '<p class="mt-account-link"></p>' +
		      `<p class="mt-account-meta mt-account-expiry">授权链接 ${""}</p>` +
		      '<p class="mt-account-fallback"></p>';
		    body.querySelector(".mt-account-link").textContent = String(pending.verificationUrl);
		    body.querySelector(".mt-account-expiry").textContent = mingTeaExpiryText(pending.expiresAt);
		    body.querySelector(".mt-account-fallback").textContent = `手动授权码（备用）：${pending.userCode}`;
		    actions.innerHTML =
		      '<button type="button" class="mt-store-cancel" data-act="cancel">取消</button>' +
		      '<button type="button" class="mt-store-cancel" data-act="open">重新打开授权页</button>';
		    return;
		  }

		  body.innerHTML = '<p class="mt-account-meta"></p>';
		  body.querySelector(".mt-account-meta").textContent = MING_TEA_ACCOUNT.expired
		    ? "登录已过期（站点凭证 7 天有效）。重新授权即可继续使用站点模型与额度显示。"
		    : "登录后可直接使用站点模型（每月 100 次）并显示剩余额度；会打开系统浏览器完成授权，通常一步就好。";
		  actions.innerHTML =
		    '<button type="button" class="mt-store-cancel" data-act="close">关闭</button>' +
		    `<button type="button" class="mt-store-ok" data-act="start">${
		      MING_TEA_ACCOUNT.expired ? "重新登录" : "登录铭荼账号"
		    }</button>`;
		}

		function mingTeaAccountStopPolling() {
		  if (mingTeaAccountPoller !== null) {
		    clearInterval(mingTeaAccountPoller);
		    mingTeaAccountPoller = null;
		  }
		}

		/** 面板存在就同步重画一次（面板可能没开——轮询是后台的，不依赖面板）。 */
		function mingTeaAccountRepaint() {
		  const panel = document.querySelector(".mt-account-panel");
		  if (panel) mingTeaAccountRenderBody(panel);
		}

		/** 单次轮询：状态推进 + 落库后的界面更新。 */
		async function mingTeaAccountPollTick() {
		  const panel = document.querySelector(".mt-account-panel");
		  let poll;
		  try {
		    poll = await mingTeaHubRpc("auth.poll");
		  } catch (error) {
		    if (panel) mingTeaAccountNote(panel, `轮询失败：${String(error?.message ?? error)}`, "error");
		    return;
		  }
		  if (poll.status === "approved") {
		    mingTeaAccountStopPolling();
		    MING_TEA_ACCOUNT.signedIn = true;
		    MING_TEA_ACCOUNT.expired = false;
		    MING_TEA_ACCOUNT.user = poll.user ?? MING_TEA_ACCOUNT.user;
		    MING_TEA_ACCOUNT.expiresAt = poll.expiresAt;
		    MING_TEA_ACCOUNT.pending = null;
		    mingTeaRenderAccountName();
		    mingTeaAccountRepaint();
		    if (panel) mingTeaAccountNote(panel, "登录成功。", "ok");
		    await Promise.all([mingTeaRefreshQuota(), mingTeaRefreshModels()]);
		    mingTeaAccountRepaint();
		    return;
		  }
		  if (poll.status === "expired") {
		    mingTeaAccountStopPolling();
		    MING_TEA_ACCOUNT.pending = null;
		    mingTeaAccountRepaint();
		    if (panel) mingTeaAccountNote(panel, "设备码已过期，请重新开始登录。", "error");
		    return;
		  }
		  if (poll.status === "pending" && poll.userCode) {
		    MING_TEA_ACCOUNT.pending = poll;
		    mingTeaAccountRepaint();
		    if (panel) mingTeaAccountNote(panel, "等待浏览器里的授权完成…");
		  }
		}

		/** 后台轮询：**不依赖面板是否打开**。站点侧的授权结果是一次性的
		 * （`approved` 那次响应取走即删），所以面板一关就停止轮询会把用户的授权丢掉；
		 * 页面重载也一样，所以启动时若宿主仍有进行中的授权，也要自动续上。 */
		function mingTeaAccountStartPolling(intervalSeconds) {
		  if (mingTeaAccountPoller !== null) return;
		  const period = Math.max(2, Number(intervalSeconds) || 3) * 1000;
		  mingTeaAccountPoller = setInterval(() => void mingTeaAccountPollTick(), period);
		}

		/** 账户面板：页脚账户按钮与设置页「账户」项打开的是同一个面板。 */
		async function mingTeaOpenAccountPanel() {
		  document.querySelector(".mt-account-wrap")?.remove();
		  const wrap = document.createElement("div");
		  wrap.className = "mt-account-wrap mt-store-confirm";
		  wrap.innerHTML =
		    '<div class="mt-store-confirm-backdrop"></div>' +
		    '<div class="mt-store-confirm-panel mt-account-panel" role="dialog" aria-modal="true" aria-label="账户">' +
		    "<h3>账户</h3><div class='mt-account-body'></div><p class='mt-account-note'></p>" +
		    "<div class='mt-store-confirm-actions mt-account-actions'></div></div>";
		  document.body.appendChild(wrap);
		  wrap.dataset.open = "1";

		  const panel = wrap.querySelector(".mt-account-panel");
		  const onKey = (event) => {
		    if (event.key === "Escape") {
		      event.preventDefault();
		      close();
		    }
		  };
		  // 关闭只移除界面：轮询留在后台，避免丢掉「授权已完成」的那次一次性响应
		  const close = () => {
		    wrap.remove();
		    document.removeEventListener("keydown", onKey, true);
		  };
		  document.addEventListener("keydown", onKey, true);
		  wrap.querySelector(".mt-store-confirm-backdrop").addEventListener("click", close);

		  panel.addEventListener("click", (event) => {
		    const button = event.target.closest("button[data-act]");
		    if (!button) return;
		    const act = button.dataset.act;
		    if (act === "close") {
		      close();
		      return;
		    }
		    if (act === "start") {
		      button.disabled = true;
		      mingTeaAccountNote(panel, "正在申请设备码…");
		      void (async () => {
		        try {
		          const started = await mingTeaHubRpc("auth.start");
		          MING_TEA_ACCOUNT.pending = started;
		          MING_TEA_ACCOUNT.expired = false;
		          mingTeaAccountRenderBody(panel);
		          if (started.opened === false) {
		            // 宿主打不开系统浏览器时，退回页面内打开（可能被拦截，所以链接也留在面板上）
		            let opened = null;
		            try {
		              opened = window.open(started.verificationUrl, "_blank", "noopener");
		            } catch {
		              opened = null;
		            }
		            mingTeaAccountNote(
		              panel,
		              opened
		                ? "已在新标签页打开授权页。"
		                : "浏览器拦截了弹窗，请手动复制上面的链接打开。",
		              opened ? "" : "error",
		            );
		          } else {
		            mingTeaAccountNote(panel, "已在系统浏览器打开授权页，等待授权…");
		          }
		          mingTeaAccountStartPolling(started.interval);
		        } catch (error) {
		          button.disabled = false;
		          mingTeaAccountNote(panel, `申请设备码失败：${String(error?.message ?? error)}`, "error");
		        }
		      })();
		      return;
		    }
		    if (act === "open") {
		      void (async () => {
		        let result = { opened: false };
		        try {
		          result = await mingTeaHubRpc("auth.open");
		        } catch (error) {
		          result = { opened: false, error: String(error?.message ?? error) };
		        }
		        if (result.opened === true) {
		          mingTeaAccountNote(panel, "已在系统浏览器打开授权页。");
		          return;
		        }
		        const url = MING_TEA_ACCOUNT.pending?.verificationUrl ?? result.verificationUrl;
		        let popped = null;
		        try {
		          popped = url ? window.open(url, "_blank", "noopener") : null;
		        } catch {
		          popped = null;
		        }
		        mingTeaAccountNote(
		          panel,
		          popped
		            ? "已在新标签页打开授权页。"
		            : `打不开浏览器（${result.error ?? "未知原因"}），请手动复制上面的链接。`,
		          popped ? "" : "error",
		        );
		      })();
		      return;
		    }
		    if (act === "cancel") {
		      // 用户明确取消：这时才停轮询（宿主的 pending 也会随下一次 poll 清掉）
		      mingTeaAccountStopPolling();
		      MING_TEA_ACCOUNT.pending = null;
		      mingTeaAccountRenderBody(panel);
		      mingTeaAccountNote(panel, "");
		      return;
		    }
		    if (act === "quota") {
		      mingTeaAccountNote(panel, "正在读取额度…");
		      void (async () => {
		        await Promise.all([mingTeaRefreshQuota(), mingTeaRefreshModels({ sync: true })]);
		        mingTeaAccountRenderBody(panel);
		        mingTeaAccountNote(panel, MING_TEA_QUOTA.detail || MING_TEA_QUOTA.error || "站点未返回额度明细。");
		      })();
		      return;
		    }
		    if (act === "signout") {
		      button.disabled = true;
		      void (async () => {
		        try {
		          await mingTeaHubRpc("auth.signOut");
		          MING_TEA_ACCOUNT.signedIn = false;
		          MING_TEA_ACCOUNT.user = null;
		          MING_TEA_ACCOUNT.expiresAt = undefined;
		          MING_TEA_QUOTA.percent = null;
		          MING_TEA_QUOTA.detail = "";
		          MING_TEA_QUOTA.loaded = false;
		          MING_TEA_QUOTA.signedIn = false;
		          mingTeaRenderAccountName();
		          mingTeaRenderQuota();
		          mingTeaAccountRenderBody(panel);
		          mingTeaAccountNote(panel, "已退出登录；默认模型已回退。", "ok");
		        } catch (error) {
		          button.disabled = false;
		          mingTeaAccountNote(panel, `退出失败：${String(error?.message ?? error)}`, "error");
		        }
		      })();
		    }
		  });

		  mingTeaAccountRenderBody(panel);
		  mingTeaAccountNote(panel, "正在读取登录状态…");
		  try {
		    const status = await mingTeaHubRpc("auth.status");
		    MING_TEA_ACCOUNT.signedIn = status.signedIn === true;
		    MING_TEA_ACCOUNT.expired = status.expired === true;
		    MING_TEA_ACCOUNT.user = status.user ?? null;
		    MING_TEA_ACCOUNT.expiresAt = status.expiresAt;
		    MING_TEA_ACCOUNT.memberExpiresAt = mingTeaParseMemberExpiry(status.user);
		    MING_TEA_ACCOUNT.pending = status.pending ?? null;
		    mingTeaRenderAccountName();
		    mingTeaAccountRenderBody(panel);
		    mingTeaAccountNote(panel, "");
		    if (MING_TEA_ACCOUNT.pending) {
		      mingTeaAccountStartPolling(MING_TEA_ACCOUNT.pending.interval);
		      void mingTeaAccountPollTick();
		    }
		    await mingTeaRefreshTiers();
		    if (MING_TEA_ACCOUNT.signedIn) {
		      await Promise.all([mingTeaRefreshQuota(), mingTeaRefreshModels()]);
		      mingTeaAccountRenderBody(panel);
		    }
		  } catch (error) {
		    mingTeaAccountNote(panel, `读取登录状态失败：${String(error?.message ?? error)}`, "error");
		  }
		}

		/** 启动时读一次登录态（页脚显示用户名）并拉一次额度；失败静默，不打扰用户。 */
		async function mingTeaBootstrapAccount() {
		  if (mingTeaCtx === null) return;
		  try {
		    const status = await mingTeaHubRpc("auth.status");
		    MING_TEA_ACCOUNT.signedIn = status.signedIn === true;
		    MING_TEA_ACCOUNT.expired = status.expired === true;
		    MING_TEA_ACCOUNT.user = status.user ?? null;
		    MING_TEA_ACCOUNT.expiresAt = status.expiresAt;
		    MING_TEA_ACCOUNT.pending = status.pending ?? null;
		    mingTeaRenderAccountName();
		    mingTeaRenderQuota();
		    if (MING_TEA_ACCOUNT.pending) {
		      // 页面重载后自动续上：站点授权结果只投递一次，不能因为刷新就丢
		      mingTeaAccountStartPolling(MING_TEA_ACCOUNT.pending.interval);
		    }
		    if (MING_TEA_ACCOUNT.signedIn) await mingTeaRefreshQuota();
		  } catch {
		    /* 宿主未就绪：保持「账户」与「—」 */
		  }
		}

		/** 档位那一行的补充文字：
		 * - 免费档：列可升级档位（数据来自 /usage 的 available_tiers，不编造价格）
		 * - 付费档：**不再显示升级提示**，改显示档位自带的权益（并发上限、Auto 赠送次数）
		 *   —— 修一个真 bug：此前付费档的 available_tiers 为空 ⇒ 这行是空的，
		 *   用户看不到任何"我买到了什么"。 */
		function mingTeaUpgradeLine() {
		  const plan = MING_TEA_ACCOUNT.plan ?? MING_TEA_QUOTA.plan ?? {};
		  if (MING_TEA_QUOTA.tier === "paid") {
		    const perks = [];
		    if (typeof plan.autoFreeCalls === "number") perks.push(`含 Auto 赠送 ${plan.autoFreeCalls} 次/月`);
		    if (typeof plan.concurrency === "number") perks.push(`并发 ${plan.concurrency}`);
		    if (typeof plan.monthlyBudgetRmb === "number") perks.push(`额度 ¥${plan.monthlyBudgetRmb}/月`);
		    return perks.length > 0 ? `本档权益：${perks.join(" · ")}` : "";
		  }
		  if (MING_TEA_QUOTA.tier !== "free") return "";
		  // 优先用站点公开的档位目录（/api/ming-tea/tiers，免登录且字段最全：价格/额度/并发/Auto赠送），
		  // /usage 的 available_tiers 只是兜底（免费档才给）。
		  const tiers =
		    Array.isArray(MING_TEA_TIERS.list) && MING_TEA_TIERS.list.length > 0
		      ? MING_TEA_TIERS.list
		      : Array.isArray(MING_TEA_QUOTA.availableTiers)
		        ? MING_TEA_QUOTA.availableTiers
		        : [];
		  if (tiers.length === 0) return "";
		  const parts = tiers
		    .filter((tier) => typeof tier?.name === "string")
		    .map((tier) => {
		      const price = typeof tier.price_rmb === "number" ? `¥${tier.price_rmb}/月` : "";
		      const budget = typeof tier.monthly_budget_rmb === "number" ? `额度 ¥${tier.monthly_budget_rmb}` : "";
		      const auto = typeof tier.auto_free_calls === "number" ? `Auto ${tier.auto_free_calls} 次` : "";
		      const conc = typeof tier.concurrency === "number" ? `并发 ${tier.concurrency}` : "";
		      return [tier.name, price, budget, auto, conc].filter(Boolean).join(" ");
		    });
		  return parts.length > 0 ? `可升级：${parts.join(" ｜ ")}` : "";
		}

		/** 站点故障后的自愈：按退避重试「模型同步 + 额度」，成功后停止。
		 * 只在确实遇到站点故障（5xx / 超时 / 网络）时启动；登录过期不重试（那要用户重新登录）。 */
		function mingTeaScheduleRecovery(reason) {
		  const transient = ["hub/site-error", "hub/timeout", "hub/network", "hub/usage-failed", "hub/models-failed"];
		  if (!transient.includes(String(reason))) return;
		  if (mingTeaRecoveryTimer !== null) return; // 已在重试队列里
		  if (mingTeaRecoveryAttempt >= MING_TEA_RETRY_DELAYS_MS.length) return;
		  const delay = MING_TEA_RETRY_DELAYS_MS[mingTeaRecoveryAttempt];
		  mingTeaRecoveryAttempt += 1;
		  mingTeaRecoveryTimer = setTimeout(() => {
		    mingTeaRecoveryTimer = null;
		    void (async () => {
		      const models = await mingTeaRefreshModels({ sync: true });
		      const quota = await mingTeaRefreshQuota();
		      if (quota.error === "" && (models.error ?? "") === "") {
		        mingTeaRecoveryAttempt = 0;
		        mingTeaRenderQuota();
		        mingTeaAccountRepaint();
		        return;
		      }
		      mingTeaScheduleRecovery(reason);
		    })();
		  }, delay);
		}

		/** 拉一次站点公开档位目录（免登录）。失败静默——界面会退回到 /usage 的 available_tiers。 */
		async function mingTeaRefreshTiers() {
		  if (mingTeaCtx === null) return MING_TEA_TIERS;
		  try {
		    const result = await mingTeaHubRpc("tiers.get");
		    if (result && typeof result === "object") {
		      MING_TEA_TIERS.list = Array.isArray(result.tiers) ? result.tiers : [];
		      MING_TEA_TIERS.free = result.free ?? null;
		      MING_TEA_TIERS.contextCatalog = Array.isArray(result.contextCatalog) ? result.contextCatalog : [];
		      MING_TEA_TIERS.loaded = true;
		    }
		  } catch {
		    /* 免登录接口都拿不到时不影响其它功能 */
		  }
		  return MING_TEA_TIERS;
		}

		/** 拉一次「账号可用模型」（付费档会多出具体型号）；未登录直接清空。 */
		async function mingTeaRefreshModels({ sync = false } = {}) {
		  if (mingTeaCtx === null) return MING_TEA_MODELS;
		  try {
		    // sync：连路由配置一起重写（补上思维档位声明等结构变化）；普通刷新只读列表
		    const listed = await mingTeaHubRpc(sync ? "models.sync" : "models.list");
		    MING_TEA_MODELS.list = Array.isArray(listed.models) ? listed.models : [];
		    MING_TEA_MODELS.tierName = typeof listed.tierName === "string" ? listed.tierName : null;
		    MING_TEA_MODELS.loaded = true;
		    MING_TEA_MODELS.error = "";
		  } catch (error) {
		    MING_TEA_MODELS.error = String(error?.message ?? error);
		    if (sync) mingTeaScheduleRecovery(error?.code);
		  }
		  return MING_TEA_MODELS;
		}

		/** 「检查更新」独立页面：注册成官方设置页的一节（`settings.section`），
		 * 显示当前版本、站点最新版本、更新说明与下载入口。
		 * 只检查不安装：真正的下载安装归 Tauri 壳（需要签名与 capabilities）。 */
		function MING_TEA_UPDATE_PAGE({ close }) {
		  const React = mingTeaRequire("react");
		  const h = React.createElement;
		  if (!React) return null;
		  const [state, setState] = React.useState({ phase: "loading" });
		  const check = React.useCallback(() => {
		    setState({ phase: "loading" });
		    mingTeaHubRpc("update.check")
		      .then((result) => setState({ phase: "done", result }))
		      .catch((error) =>
		        setState({ phase: "error", message: String(error?.message ?? error), code: error?.code }),
		      );
		  }, []);
		  React.useEffect(() => {
		    check();
		  }, [check]);

		  const rows = [];
		  rows.push(h("h1", { key: "title", className: "mt-upd-title" }, "检查更新"));
		  rows.push(
		    h(
		      "p",
		      { key: "intro", className: "mt-upd-meta" },
		      "这里是铭荼桌面应用的更新检查。模型与应用商店的更新是另外两条通道，不在这里。",
		    ),
		  );
		  if (state.phase === "loading") {
		    rows.push(h("p", { key: "loading", className: "mt-upd-meta" }, "正在检查…"));
		  } else if (state.phase === "error") {
		    rows.push(
		      h("p", { key: "err", className: "mt-upd-error" }, `检查失败：${state.message}${state.code ? `（${state.code}）` : ""}`),
		    );
		  } else {
		    const result = state.result ?? {};
		    rows.push(
		      h(
		        "dl",
		        { key: "facts", className: "mt-upd-facts" },
		        h("div", null, h("dt", null, "当前版本"), h("dd", null, result.current ?? "开发环境（未打包）")),
		        h("dt", null, "站点最新版本"),
		        h("dd", null, result.latest ?? (result.reason === "unpublished" ? "尚未发布安装包" : "—")),
		        h("dt", null, "检查结果"),
		        h("dd", null, result.message ?? "—"),
		      ),
		    );
		    if (result.pubDate) {
		      rows.push(h("p", { key: "date", className: "mt-upd-meta" }, `发布时间：${result.pubDate}`));
		    }
		    if (result.notes) {
		      rows.push(
		        h("div", { key: "notes", className: "mt-upd-notes" }, h("h2", null, "更新内容"), h("p", null, result.notes)),
		      );
		    }
		    const actions = [];
		    actions.push(
		      h("button", { key: "recheck", type: "button", className: "mt-upd-secondary", onClick: check }, "重新检查"),
		    );
		    if (result.status === "available" && result.url) {
		      actions.push(
		        h(
		          "a",
		          { key: "download", className: "mt-upd-primary", href: result.url, target: "_blank", rel: "noopener" },
		          `下载 ${result.latest}`,
		        ),
		      );
		    }
		    rows.push(h("div", { key: "actions", className: "mt-upd-actions" }, actions));
		  }
		  if (typeof close === "function") {
		    rows.push(
		      h(
		        "button",
		        { key: "close", type: "button", className: "mt-upd-secondary", onClick: () => close() },
		        "返回应用",
		      ),
		    );
		  }
		  return h("section", { className: "mt-upd-page" }, rows);
		}

		/** 「用量看板」独立页面：注册成官方设置页的一节（`settings.section`）。
		 * 数据来自站点 `/usage`，由宿主整形成可直接渲染的模型（`lib/host/usage-view.mjs`，
		 * 那边有离线断言）；本页**只读** —— 列额度、给刷新，不提供购买/升级入口
		 * （下单与支付属于站点页面，这里不放一个假装能买的按钮）。 */
		function MING_TEA_USAGE_PAGE({ close }) {
		  const React = mingTeaRequire("react");
		  const h = React.createElement;
		  if (!React) return null;
		  const [state, setState] = React.useState({ phase: "loading", board: null });
		  const load = React.useCallback(() => {
		    setState((prev) => ({ phase: prev.board ? "refreshing" : "loading", board: prev.board }));
		    mingTeaHubRpc("usage.board")
		      .then((board) => {
		        // 同一次请求的结果同步给页脚圆环与账号徽标：两个界面永远同一个口径，
		        // 也不为了刷新看板再打一次 quota.get。
		        if (board && board.quota) {
		          mingTeaApplyQuota(board.quota);
		          mingTeaRenderAccountBadge();
		          mingTeaRenderQuota();
		        }
		        setState({ phase: "done", board });
		      })
		      .catch((error) =>
		        setState((prev) => ({
		          phase: "error",
		          board: prev.board,
		          message: String(error?.message ?? error),
		          code: error?.code,
		        })),
		      );
		  }, []);
		  React.useEffect(() => {
		    load();
		  }, [load]);

		  const rows = [];
		  rows.push(h("h1", { key: "title", className: "mt-upd-title" }, "用量看板"));
		  rows.push(
		    h(
		      "p",
		      { key: "intro", className: "mt-upd-meta" },
		      "这里显示铭荼账号的额度：Auto 赠送次数、付费层余额与当前档位。数据来自站点，只读展示。",
		    ),
		  );

		  const board = state.board;
		  if (state.phase === "error") {
		    rows.push(
		      h(
		        "p",
		        { key: "err", className: "mt-upd-error" },
		        `读取用量失败：${state.message}${state.code ? `（${state.code}）` : ""}`,
		      ),
		    );
		  }

		  if (board) {
		    if (board.heading) {
		      rows.push(h("p", { key: "heading", className: "mt-usage-heading" }, board.heading));
		    }
		    const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);
		    for (const meter of board.meters) {
		      const remaining = num(meter.remaining);
		      const limit = num(meter.limit);
		      const used = num(meter.used);
		      const figure =
		        limit === null
		          ? `剩余 ${remaining ?? "—"} ${meter.unit}`
		          : `剩余 ${remaining ?? "—"} / ${limit} ${meter.unit}`;
		      rows.push(
		        h(
		          "div",
		          { key: `meter-${meter.key}`, className: "mt-usage-meter" },
		          h(
		            "div",
		            { className: "mt-usage-meter-head" },
		            h("span", { className: "mt-usage-meter-label" }, meter.label),
		            h("span", { className: "mt-usage-meter-figure" }, figure),
		          ),
		          h(
		            "div",
		            {
		              className: "mt-usage-bar",
		              role: "img",
		              "aria-label": `${meter.label}：${
		                meter.percent === null ? "剩余占比未知" : `剩余 ${meter.percent}%`
		              }`,
		            },
		            h("span", {
		              className: "mt-usage-bar-fill",
		              "data-unknown": meter.percent === null ? "1" : "0",
		              style: { width: `${meter.percent ?? 0}%` },
		            }),
		          ),
		          h(
		            "p",
		            { className: "mt-upd-meta" },
		            [
		              used === null ? null : `已用 ${used} ${meter.unit}`,
		              meter.percent === null ? null : `剩余 ${meter.percent}%`,
		            ]
		              .filter(Boolean)
		              .join(" · "),
		          ),
		          meter.note ? h("p", { className: "mt-usage-note" }, meter.note) : null,
		        ),
		      );
		    }
		    if (board.meters.length === 0) {
		      rows.push(h("p", { key: "empty", className: "mt-upd-meta" }, board.detail || "站点本次没有返回额度数据。"));
		    }
		    if (board.facts.length > 0) {
		      rows.push(
		        h(
		          "dl",
		          { key: "facts", className: "mt-upd-facts" },
		          board.facts.map((fact) =>
		            h("div", { key: fact.key }, h("dt", null, fact.label), h("dd", null, fact.value)),
		          ),
		        ),
		      );
		    }
		    if (board.tiers.length > 0) {
		      rows.push(
		        h(
		          "div",
		          { key: "tiers", className: "mt-upd-notes" },
		          h("h2", null, "站点当前提供的档位"),
		          ...board.tiers.map((tier) =>
		            h(
		              "p",
		              { key: tier.key ?? tier.name },
		              `${tier.name}${
		                tier.monthlyBudgetRmb === null ? "" : ` · 额度 ¥${tier.monthlyBudgetRmb}`
		              }${tier.autoFreeCalls === null ? "" : ` · Auto 赠送 ${tier.autoFreeCalls} 次`}${
		                tier.concurrency === null ? "" : ` · 并发 ${tier.concurrency}`
		              }`,
		            ),
		          ),
		        ),
		      );
		    }
		    if (board.notes.length > 0) {
		      rows.push(
		        h(
		          "p",
		          { key: "notes", className: "mt-upd-meta" },
		          board.notes.join(" "),
		        ),
		      );
		    }
		  } else if (state.phase !== "error") {
		    rows.push(h("p", { key: "loading", className: "mt-upd-meta" }, "正在读取用量…"));
		  }

		  const actions = [];
		  actions.push(
		    h(
		      "button",
		      { key: "refresh", type: "button", className: "mt-upd-secondary", onClick: load, disabled: state.phase === "loading" },
		      state.phase === "loading" ? "读取中…" : "刷新",
		    ),
		  );
		  if (typeof close === "function") {
		    actions.push(
		      h("button", { key: "close", type: "button", className: "mt-upd-secondary", onClick: () => close() }, "返回应用"),
		    );
		  }
		  rows.push(h("div", { key: "actions", className: "mt-upd-actions" }, actions));
		  return h("section", { className: "mt-upd-page mt-usage-page" }, rows);
		}

		/** 取平台的 react（DSH 的种子模块表里有 react）。拿不到就跳过注册，不报错。 */
		function mingTeaRequire(name) {
		  try {
		    return window.__ModuleLoader__?.require?.(name) ?? (typeof require === "function" ? require(name) : null);
		  } catch {
		    return null;
		  }
		}

		function mingTeaRegisterUpdatePage(ctx) {
		  const React = mingTeaRequire("react");
		  if (!React || !ctx?.slots?.inject) return;
		  ctx.slots.inject("settings.section", () =>
		    ctx.slots.register(
		      {
		        name: "settings.section",
		        id: "ming-tea-update",
		        order: 90,
		        label: () => "检查更新",
		        locale: "ming-tea-ui",
		      },
		      MING_TEA_UPDATE_PAGE,
		    ),
		  );
		}

		/** 设置里的「用量看板」：注册成官方设置页的一节（与「检查更新」同一机制）。 */
		function mingTeaRegisterUsagePage(ctx) {
		  const React = mingTeaRequire("react");
		  if (!React || !ctx?.slots?.inject) return;
		  ctx.slots.inject("settings.section", () =>
		    ctx.slots.register(
		      {
		        name: "settings.section",
		        id: "ming-tea-usage",
		        order: 91,
		        label: () => "用量看板",
		        locale: "ming-tea-ui",
		      },
		      MING_TEA_USAGE_PAGE,
		    ),
		  );
		}

		/** 设置里的「检查更新」：只检查并提示，不下载安装（安装归 Tauri 壳，另做）。 */
		async function mingTeaCheckUpdate(anchor) {
		  mingTeaHint("正在检查更新…", anchor);
		  try {
		    const result = await mingTeaHubRpc("update.check");
		    mingTeaHint(
		      result.message || "已是最新",
		      anchor,
		      result.status === "available" && result.url ? { href: result.url } : {},
		    );
		  } catch (error) {
		    mingTeaHint(`检查更新失败：${String(error?.message ?? error)}`, anchor);
		  }
		}

		function mingTeaFillSettingsNav() {
		  // 「检查更新」已经是真正的设置页（settings.section: ming-tea-update），
		  // 这里不再注入同名导航项（否则会出现两个「检查更新」）；旧的注入项一并移除。
		  document.querySelectorAll(".mt-settings-update, .mt-settings-account").forEach((el) => el.remove());
		  return;
		}

		// eslint-disable-next-line no-unreachable -- 保留旧注入逻辑的形状，便于需要时回退
		function mingTeaFillSettingsNavLegacy() {
		  const links = Array.from(document.querySelectorAll(".dcu-settings-link"));
		  if (links.length === 0) return;
		  const host = links[links.length - 1].parentElement;
		  if (!host) return;
		  if (document.querySelector(".mt-settings-update")) return;
		  const item = document.createElement("button");
		  item.type = "button";
		  item.className = "dcu-settings-link mt-settings-update";
		  item.innerHTML = `${MING_TEA_ICONS.update}<span>${MING_TEA_TWEAKS.settingsExtra.label}</span>`;
		  item.addEventListener("click", (event) => {
		    event.preventDefault();
		    event.stopPropagation();
		    if (mingTeaCtx === null) {
		      mingTeaHint(MING_TEA_TWEAKS.settingsExtra.hint, item);
		      return;
		    }
		    void mingTeaCheckUpdate(item);
		  });
		  host.appendChild(item);
		}

		// 轻量提示气泡：替代空点击，明确告知"尚未接入"而不是假装成功
		function mingTeaHint(text, anchor, options = {}) {
		  const existing = document.querySelector(".mt-foot-hint");
		  if (existing) existing.remove();
		  const bubble = document.createElement("div");
		  bubble.className = "mt-foot-hint";
		  if (typeof options.href === "string" && options.href !== "") {
		    // 有下载地址时给一个真能点的链接（气泡因此需要接收指针事件）
		    bubble.dataset.interactive = "1";
		    bubble.textContent = `${text} `;
		    const link = document.createElement("a");
		    link.href = options.href;
		    link.target = "_blank";
		    link.rel = "noopener";
		    link.textContent = "前往下载";
		    bubble.appendChild(link);
		  } else {
		    bubble.textContent = text;
		  }
		  document.body.appendChild(bubble);
		  const rect = anchor.getBoundingClientRect();
		  bubble.style.left = `${Math.min(rect.left + rect.width / 2, window.innerWidth - 220)}px`;
		  bubble.style.top = `${rect.top - 10}px`;
		  requestAnimationFrame(() => (bubble.dataset.visible = "1"));
		  setTimeout(() => {
		    bubble.dataset.visible = "0";
		    setTimeout(() => bubble.remove(), 220);
		  }, 2600);
		}

		function startMingTeaTweaks() {
		  applyMingTeaTweaks();
		  // 商店安装确认：捕获阶段拦点击，只注册一次
		  document.addEventListener("click", mingTeaStoreClickGuard, true);
		  // 本站路由的删除按钮：即使在捕获阶段也要拦住
		  document.addEventListener("click", mingTeaProtectHubRouteClick, true);
		  let timer;
		  const observer = new MutationObserver(() => {
		    clearTimeout(timer);
		    timer = setTimeout(applyMingTeaTweaks, 180);
		  });
		  // characterData 必须一起监听（2026-10-01 在 0.2.0-rc.2 上实测踩到）：
		  // 像「深度求索中，用时 N 秒」这种**插值句**，React 只改已有文本节点的值、不加删节点，
		  // 只监听 childList 时我们的替换规则拿不到这次变更 —— 表现为同一串在状态标签上换掉了、
		  // 在消息元信息里却还是原文。规则本身是幂等的（换过就不再匹配），所以多跑一遍不会抖动。
		  observer.observe(document.body, { childList: true, characterData: true, subtree: true });
		  if (document.readyState === "loading") {
		    document.addEventListener("DOMContentLoaded", applyMingTeaTweaks, { once: true });
		  }
		}

		startMingTeaTweaks();

		// 官方一等公民通道：overrideTokens 把所有值写成 body 内联样式，
		// 必然胜过 body 上的官方声明（这正是早期 :root 覆盖失效的原因）。
		// 失败只记录、不抛出：样式已由上面注入的 CSS 块保证。
		function apply(ctx) {
			// 先把上下文交给定制层：首页场景卡需要 sessions / conversation 服务
			setMingTeaContext(ctx);
			mingTeaRegisterUpdatePage(ctx);
			mingTeaRegisterUsagePage(ctx);
			try {
				if (!ctx || !ctx.theme || typeof ctx.theme.overrideTokens !== "function") return;
				const dispose = ctx.theme.overrideTokens(PLUGIN_ID, TOKEN_OVERRIDES);
				if (typeof ctx.effect === "function" && typeof dispose === "function") {
					ctx.effect(() => dispose);
				}
			} catch (error) {
				const list = (window.__mingTeaThemeErrors = window.__mingTeaThemeErrors || []);
				list.push(String((error && error.message) || error));
			}
		}
		exports.apply = apply;
		exports.inject = ["theme", "sessions", "conversation", "connection", "slots"];
		return module.exports;
	}
});
