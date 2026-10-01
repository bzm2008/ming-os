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
  const permission = document.createElement("button");
  permission.type = "button";
  permission.className = "mt-summon-permission";
  permission.hidden = true;
  const capsule = document.createElement("div");
  capsule.className = "mt-summon-capsule";
  const answer = document.createElement("div");
  answer.className = "mt-summon-answer";
  layer.append(permission, capsule, answer);
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

/** 每轮（180ms 节流）跑一次：判定档位、更新胶囊与回答、按需开始听与发送。 */
function mingTeaSummonTick() {
  if (!MING_TEA_SUMMON.on) return;
  mingTeaSummonEnsureNodes();
  if (!MING_TEA_SUMMON.capsule || !MING_TEA_SUMMON.answer) return;

  mingTeaSummonCheckPermissions();
  // 点一下就开始说：WKWebView 惯例要求**真实用户手势**才允许开麦，
  // 我们合成的事件不算，所以「弹出即自动听」在 macOS 上不一定成立 —— 这一下点击是兜底。
  if (MING_TEA_SUMMON.layer && !MING_TEA_SUMMON.tapBound) {
    MING_TEA_SUMMON.tapBound = true;
    MING_TEA_SUMMON.layer.addEventListener(
      "click",
      () => {
        MING_TEA_SUMMON.listeningRequested = false; // 允许再点一次重试
        MING_TEA_SUMMON.listeningAttempts = 0;
        if (mingTeaSummonStartListening()) {
          MING_TEA_SUMMON.listeningRequested = true;
          MING_TEA_SUMMON.capsule.textContent = "我在听…";
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

  // 回答：镜像最后一个回答块；有内容才显示
  if (answerText && answerText !== MING_TEA_SUMMON.lastAnswer) {
    MING_TEA_SUMMON.lastAnswer = answerText;
    MING_TEA_SUMMON.answer.textContent = answerText;
  }

  // 档位：有审批=approval（要大而可点，否则审批卡在视口外点不到）；
  //       有回答=answer；在听或胶囊有字=listening；否则只有宠物
  const hasCapsule = (MING_TEA_SUMMON.capsule.textContent || "").trim() !== "";
  const approvalPending = document.querySelector("[data-approval-key], [data-question-key]") !== null;
  let stage = "pet";
  if (approvalPending) stage = "approval";
  else if (answerText !== "") stage = "answer";
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
async function mingTeaHubRpc(endpoint) {
  const ctx = mingTeaCtx;
  const rpc = ctx?.connection?.rpc;
  if (!rpc?.call) throw new Error("宿主连接不可用（connection 服务未注入）");
  const result = await rpc.call("/ming-tea", endpoint, {});
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

export function setMingTeaContext(ctx) {
  mingTeaCtx = ctx;
  // 调试钩子：便于在浏览器里核对宿主接入（只暴露我们自己的通道，不暴露整个 ctx）
  if (typeof window !== "undefined") {
    window.__mingTeaHub = { call: (endpoint) => mingTeaHubRpc(endpoint) };
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

export function startMingTeaTweaks() {
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
