import "./theme.css";

const scenes = [
  ["office", "办公模式", "文档、网页和日常任务"],
  ["development", "开发模式", "项目、终端和诊断"],
  ["learning", "辅助学习模式", "解释、拆解和演示"],
] as const;

document.querySelector<HTMLDivElement>("#app")!.innerHTML = `
  <main class="app-shell">
    <header class="topbar"><div class="brand-mark">铭荼</div><div class="connection"><span class="status-dot"></span>本地 Agent 已就绪</div></header>
    <section class="workspace">
      <nav class="scene-rail"><div class="rail-label">工作场景</div>${scenes.map(([id, label, hint], index) => `<button class="scene ${index === 0 ? "selected" : ""}" data-scene="${id}"><strong>${label}</strong><span>${hint}</span></button>`).join("")}</nav>
      <section class="conversation"><div class="eyebrow">当前场景 · 办公模式</div><h1>今天想让铭荼帮你完成什么？</h1><p class="intro">从资料整理到文档处理，铭荼会先列出计划，再按权限执行。</p><div class="composer"><textarea aria-label="任务输入" placeholder="描述一个任务…"></textarea><button class="send" aria-label="发送任务">发送</button></div></section>
      <aside class="inspector"><div class="panel-title">任务计划</div><div class="empty-state">尚未开始任务<br><small>工具调用和审批请求会显示在这里</small></div></aside>
    </section>
  </main>`;

document.querySelectorAll<HTMLButtonElement>(".scene").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".scene").forEach((item) => item.classList.remove("selected"));
  button.classList.add("selected");
  const label = button.querySelector("strong")?.textContent ?? "办公模式";
  document.querySelector(".eyebrow")!.textContent = `当前场景 · ${label}`;
}));
