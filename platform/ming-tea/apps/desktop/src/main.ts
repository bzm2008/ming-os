import "./theme.css";

type SceneId = "office" | "development" | "learning";
type Inspector = "plan" | "memory" | "sessions";

const scenes: Array<[SceneId, string, string, string]> = [
  ["office", "办公模式", "文档、网页和日常任务", "▦"],
  ["development", "开发模式", "项目、终端和诊断", "⌘"],
  ["learning", "辅助学习模式", "解释、拆解和演示", "◇"],
];

const state: {scene: SceneId; inspector: Inspector; query: string; status: string; memoryCount: number; sent: boolean} = {
  scene: "office", inspector: "plan", query: "", status: "本地 Agent 已就绪", memoryCount: 3, sent: false,
};

const app = document.querySelector<HTMLDivElement>("#app")!;
function sceneLabel() { return scenes.find(([id]) => id === state.scene)?.[1] ?? "办公模式"; }

function inspectorContent(): string {
  if (state.inspector === "memory") return `
    <div class="panel-heading"><span>长期记忆</span><button class="icon-button" data-action="capture" title="记录一条记忆">＋</button></div>
    <div class="memory-summary"><strong>${state.memoryCount}</strong><span>条本地记忆</span></div>
    <div class="memory-row"><span class="memory-dot"></span><div><strong>偏好中文回复</strong><small>用户偏好 · 当前用户</small></div><button class="tiny-action">查看</button></div>
    <div class="memory-row"><span class="memory-dot"></span><div><strong>开发项目使用 pytest</strong><small>项目记忆 · 本地</small></div><button class="tiny-action">查看</button></div>
    <div class="memory-row"><span class="memory-dot"></span><div><strong>危险操作需要确认</strong><small>安全策略 · 铭荼</small></div><button class="tiny-action">查看</button></div>
    <div class="muted-note">记忆默认保存在本机。EverOS 和 Honcho 连接器需要单独启用。</div>`;
  if (state.inspector === "sessions") return `
    <div class="panel-heading"><span>会话工作台</span><button class="icon-button" data-action="filter" title="筛选会话">☷</button></div>
    <label class="search-box"><span>⌕</span><input data-session-search placeholder="搜索历史会话" value="${state.query}" /></label>
    <div class="session-filters"><button class="filter active">最近</button><button class="filter">已归档</button></div>
    <div class="session-row selected"><div><strong>整理 Ming OS 构建问题</strong><small>开发模式 · 2 分钟前</small></div><button class="tiny-action">引用</button></div>
    <div class="session-row"><div><strong>准备家庭报表</strong><small>办公模式 · 昨天</small></div><button class="tiny-action">引用</button></div>
    <div class="session-row"><div><strong>解释 systemd 日志</strong><small>辅助学习 · 3 天前</small></div><button class="tiny-action">引用</button></div>`;
  return `
    <div class="panel-heading"><span>任务计划</span><span class="plan-count">3 步</span></div>
    <div class="plan-item done"><span>✓</span><div><strong>理解任务目标</strong><small>已完成</small></div></div>
    <div class="plan-item active"><span>2</span><div><strong>检查本地项目文件</strong><small>正在准备工具调用</small></div></div>
    <div class="plan-item"><span>3</span><div><strong>整理结果并给出建议</strong><small>等待前一步完成</small></div></div>
    <div class="approval-card"><div class="approval-icon">!</div><div><strong>需要你的确认</strong><p>如果任务需要修改文件、上传或执行特权命令，铭荼会在这里暂停。</p></div></div>`;
}

function render() {
  app.innerHTML = `
    <main class="app-shell">
      <header class="topbar"><div class="brand-lockup"><span class="brand-mark">铭荼</span><span class="brand-sub">MING TEA</span></div><div class="topbar-actions"><button class="provider-pill" title="选择模型提供方"><span class="provider-dot"></span> Ming 主站 <span class="chevron">⌄</span></button><div class="connection"><span class="status-dot"></span>${state.status}</div><button class="icon-button" title="打开设置">⚙</button></div></header>
      <section class="workspace">
        <nav class="scene-rail"><div class="rail-label">工作场景</div>${scenes.map(([id, label, hint, icon]) => `<button class="scene ${id === state.scene ? "selected" : ""}" data-scene="${id}"><span class="scene-icon">${icon}</span><span class="scene-copy"><strong>${label}</strong><small>${hint}</small></span></button>`).join("")}<div class="rail-divider"></div><button class="rail-link" data-inspector="sessions"><span>◷</span> 会话工作台</button><button class="rail-link" data-inspector="memory"><span>✦</span> 长期记忆</button><div class="rail-footer"><span class="secure-mark">◈</span><span>本地优先<br><small>权限由铭荼管理</small></span></div></nav>
        <section class="conversation"><div class="conversation-head"><div><div class="eyebrow">当前场景 · ${sceneLabel()}</div><h1>把复杂的事，交给铭荼一起完成。</h1><p class="intro">铭荼会先理解目标、列出计划，再调用经过授权的工具。每一步都可查看、暂停和确认。</p></div><div class="scene-badge">${sceneLabel()}</div></div><div class="quick-actions"><button data-quick="整理当前项目">整理当前项目</button><button data-quick="解释这段错误日志">解释错误日志</button><button data-quick="准备一份报表">准备报表</button></div>${state.sent ? `<div class="message-card"><div class="message-avatar">你</div><div><strong>整理当前项目</strong><p>已收到。我会先读取项目结构和最近日志，再给出一份可确认的计划。</p></div></div>` : ""}<div class="composer"><textarea aria-label="任务输入" placeholder="描述一个任务…"></textarea><button class="send" data-action="send" aria-label="发送任务">发送 <span>↗</span></button></div><div class="timeline"><div class="timeline-title"><span>工具时间线</span><span class="timeline-state">等待任务</span></div><div class="timeline-line"><span class="timeline-node muted"></span><div><strong>尚未调用工具</strong><small>浏览器、终端、文件和 Office 操作会显示在这里</small></div></div></div></section>
        <aside class="inspector"><div class="inspector-tabs"><button class="tab ${state.inspector === "plan" ? "active" : ""}" data-inspector="plan">计划</button><button class="tab ${state.inspector === "memory" ? "active" : ""}" data-inspector="memory">记忆</button><button class="tab ${state.inspector === "sessions" ? "active" : ""}" data-inspector="sessions">会话</button></div><div class="inspector-body">${inspectorContent()}</div></aside>
      </section>
    </main>`;
  bindEvents();
}

function bindEvents() {
  document.querySelectorAll<HTMLElement>("[data-scene]").forEach((button) => button.addEventListener("click", () => { state.scene = button.dataset.scene as SceneId; render(); }));
  document.querySelectorAll<HTMLElement>("[data-inspector]").forEach((button) => button.addEventListener("click", () => { state.inspector = button.dataset.inspector as Inspector; render(); }));
  document.querySelectorAll<HTMLButtonElement>("[data-quick]").forEach((button) => button.addEventListener("click", () => { const input = document.querySelector<HTMLTextAreaElement>("textarea"); if (input) input.value = button.dataset.quick ?? ""; input?.focus(); }));
  document.querySelector<HTMLButtonElement>("[data-action=send]")?.addEventListener("click", () => { state.sent = true; state.status = "已创建任务计划"; render(); });
  document.querySelector<HTMLInputElement>("[data-session-search]")?.addEventListener("input", (event) => { state.query = (event.target as HTMLInputElement).value; });
}

render();
