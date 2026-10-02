// 铭荼的三个用户可见场景。
//
// 场景 = DSH 的 Agent preset（官方机制）：新对话开始前在首页选择，
// 决定该会话的工具组合与人格提示。用户只需要在这三个场景间选择。
//
// 关键设计：**按行 id 覆盖内置 preset 的 config，而不是停用内置再插入新 id**。
// 原因（2026-09-27 实测教训）：DSH 会把用户选择的默认 preset 持久化在
// `agent-presets` 设置命名空间里（`selectedDefault`）。若把内置 preset 停用、
// 换成新 id，旧引用会变成悬空，新建会话直接失败：
//   agent-preset/not-found: Unknown agent preset: standard
// 保留 `standard`/`ptc`/`minimal` 三个 id 并只替换其 config，既能满足
// “只给用户三个场景”，又不会打断任何已持久化的引用。
// （内置的第四个 `cordis` 是创造模式入口，本产品不用，单独停用。）
//
// 工具列表不在这里维护：scripts/build-presets.mjs 会从已安装的
// `@deepseek-ai/dsh-web-app` 里**该场景自己的 sourcePreset** 派生，避免与上游漂移。
//
// ⚠️ 2026-09-27 修正：此前所有场景都从 standard 派生，于是「开发模式」（id 为 ptc）
// 拿到的是 standard 的行表——workflow-ptc/tool-workflow 开着（官方 ptc 故意关掉），
// 又缺官方 ptc 的 tool-presentation（mode: ptc）。名字叫开发、能力却对不上。
// 现在按 preset id 对应的官方来源派生：办公→standard、开发→ptc、学习→minimal。
// 现在三个场景的工具组合不再相同：办公与学习不挂终端与后台任务行，开发保持全量
// （机制是 per-scene 的 dropRows —— 不挂那几行，而不是从工具层做过滤）。
// 三个场景共享的能力行：浏览器自动化 + 电脑（桌面）操作。
//
// 用户决定（2026-09-27）：办公、开发、辅助学习三个场景都要可用，不再只给开发场景。
// 需要知道的两点风险，已在 README 记录：
//   ① 官方层对这些工具**没有审批闸门**（无窗口白名单、无动作预算、无截图节流），
//      唯一约束是系统提示词；我们的 computer-use-guard.ts 目前未被接入，属死代码。
//   ② 桌面控制在 macOS 上需要给**启动 DSH 的宿主 App**授予 Accessibility + 屏幕录制
//      全量授权（归系统设置管，不是装包就能得到）；Linux/Ming OS 还需装对应原生包。
// 浏览器自动化用 launch + headless（独立会话），不接管用户已登录的浏览器。
//
// **必须包在带 isolate 的 group 里**（2026-09-27 实测教训）：
// 把这四行平铺进 preset 会让三个场景全部「加载失败」——
//   standard:  Preset services require isolate realms: browserUse, computerUse
//   ptc/minimal: service "browserUse" has been registered at <BrowserUseRegistry>
// 即服务型行在 preset 作用域里会和根作用域/其它预设的注册撞名，必须显式隔离。
// 官方在预设里挂服务型行也是同一手法（standard 预设的 planning / compaction /
// delegation 三个 group 都带 isolate），我们沿用这一机制。
const MING_TEA_ABILITY_GROUP = {
  id: "abilities",
  isolate: { browserUse: true, computerUse: true },
  rows: [
    { id: "browser-use", name: "@deepseek-ai/dsh-browser-use" },
    {
      id: "browser-use-playwright-mcp",
      name: "@deepseek-ai/dsh-experimental-browser-use-playwright-mcp",
      config: { mode: "launch", headless: true },
    },
    { id: "computer-use", name: "@deepseek-ai/dsh-computer-use" },
    { id: "computer-use-cua-driver-native", name: "@deepseek-ai/dsh-experimental-computer-use-cua-driver-native" },
  ],
};

export const scenes = [
  {
    // 复用内置 standard 行：历史默认值都指向它，保留最稳
    rowId: "preset-standard",
    presetId: "standard",
    sourcePreset: "standard",
    // 浏览器自动化与电脑操作（见文件顶部 MING_TEA_ABILITY_GROUP 的说明）
    extraGroups: [MING_TEA_ABILITY_GROUP],
    order: 1,
    name: "办公模式",
    description: "浏览器协作、文档表格演示文稿、文件整理；写入与对外发送先征求同意。",
    persona: [
      "你是铭荼，运行在“办公”场景。",
      "优先使用浏览器、文档/表格/演示文稿与文件整理能力完成任务；",
      "读取、浏览和整理可以自主进行，写入、上传、提交与对外发送必须先取得用户批准。",
      "用简体中文回复，先给可以直接使用的结论与步骤，再补充必要说明。",
    ].join(""),
    // 按场景裁剪工具：**不挂对应的行**，而不是用 ctx.tools.restrict。
    // 原因（2026-09-27 实测）：restrict 只作用于**全局**工具，且"作用域内的名字会失败"；
    // preset 自己注册的 tool-* 行属于该 preset 作用域，所以 restrict 在这里是错的机制。
    // 官方三个预设本来就用"挂哪些行"来区分能力，我们沿用同一机制。
    // 办公不挂终端与后台任务：普通用户误操作的代价最大。
    dropRows: ["tool-bash", "tool-pwsh", "tool-jobs"],
  },
  {
    rowId: "preset-ptc",
    presetId: "ptc",
    // 官方 ptc 是 DSH 的「程序化工具调用」开发模式：它**故意关掉** workflow-ptc/tool-workflow，
    // 改用 tool-presentation(mode: ptc) 呈现。我们从它派生，就等于继承这套选择。
    sourcePreset: "ptc",
    extraGroups: [MING_TEA_ABILITY_GROUP],
    order: 2,
    name: "开发模式",
    description: "阅读代码与日志、运行测试与构建、诊断工程问题；提权与系统变更先征求同意。",
    // 官方**没有任何预设挂载**的孤儿工具包：@deepseek-ai/dsh-tool-str-replace-editor
    // （str_replace_editor：查看 / 创建 / 字面量精确替换 / 按行插入）。
    // 这是 DSH 里唯一"不整文件重写就能改代码"的工具，对写程序直接有用，所以只给开发场景挂。
    //
    // 2026-10-02 新增两个社区插件的工具行，同样**只给开发场景**：
    //   - dsh-graphlint：graphlint_query / graphlint_build / graphlint_config（死代码检测；
    //     真正干活的是外部 graphlint CLI，机器上没装时工具会如实报错并给出安装提示）；
    //   - dsh-codex-guard：codex_guard（提交前卫生检查：TODO 残留 / 硬编码密钥 / 提交信息格式）。
    // 两个插件的**根行在 profile patch 里被禁用**（见 scripts/install_ming_tea_plugins.sh 的
    // ming-tea:dev-only-tools 段）—— 根行是 profile 级、会让工具对所有场景可见，
    // 而办公/学习场景面向普通用户，不该多出「跑死代码分析」「扫密钥」这类工具。
    extraRows: [
      { id: "str-replace-editor", name: "@deepseek-ai/dsh-tool-str-replace-editor" },
      { id: "dsh-graphlint", name: "dsh-graphlint" },
      { id: "codex-guard", name: "dsh-codex-guard" },
    ],
    // 官方默认关、这一场景要开的能力（见文件末尾说明）：
    // - 委派后端 codex / claude-code：需要机器上真的装了对应 CLI，否则调用会报错（文档已标注）
    // - ralph：新鲜上下文循环重构；上游注明「完成与否是工人自报、不是独立评估」
    rowToggles: {
      // 只开 codex：本机已装 codex CLI，实测可用。
      // **不开 claude-code**（用户判断：DSH 自带 subagent/subagent_fork，再挂一个外部
      // CLI 后端价值不大，而且本机没装 claude ⇒ 只会多一个必然失败的工具让模型误选）。
      "tool-subagent-codex": true,
      "tool-ralph": true,
      // ⚠️ 实测冲突（2026-09-27）：`tool-ralph` 硬注入 `workflowEngine`，而这个服务由
      // `workflow-ptc` 提供。官方 ptc 关掉 workflow-ptc/tool-workflow 的同时也没开 ralph，
      // 所以两者在官方那里从不共存；我们开了 ralph，就必须把它依赖的服务一起打开：
      // 否则 preset 挂载失败，整个开发场景显示「加载失败」
      // （实测报错原文：`tool-ralph: waiting for workflowEngine`）。
      // 取舍：保留 ralph（用户明确要），因此工作流引擎与 tool-workflow 一并开启 —— 这是
      // 与官方 ptc 的**有意分歧**，代价是模型多一个 workflow 工具。
      "workflow-ptc": true,
      "tool-workflow": true,
    },
    // 官方 ptc 有、standard 没有的行：PTC 呈现模式。我们从 ptc 派生已继承，
    // 若要显式保留可在此加 rowAdds；目前不需要。
    persona: [
      "你是铭荼，运行在“开发”场景，负责写程序与改软件。",
      // 以下纪律逐条对标 Codex 仓库的 AGENTS.md 与 Claude Code 官方最佳实践，
      // 只保留对「真的把软件写出来」有直接作用的部分（文档警告：冗长指令会被忽略）。
      "动手前先读懂相关文件与既有约定：先看目录结构、再读要改的文件、遵循项目原有写法；不要凭猜测改代码。",
      "改代码优先用精确编辑（str_replace_editor 的字面量替换/按行插入），不要整文件重写；只在新建文件时才写整份内容。",
      "非平凡任务先给计划（计划模式），写清要改什么、验收标准是什么，再动手；一句话能说清的改动直接做。",
      "声明完成前必须自己验证：跑相关的测试/构建/类型检查，并把命令与输出作为证据回报；没验证就说没验证，不要用“应该可以”。",
      "修根因不打补丁：不要为了让报错消失而吞异常、放宽断言或注释掉测试。",
      "多步任务维护待办列表，边做边更新，让用户随时知道进度。",
      "只改与任务相关的代码，不做顺手重构、不扩大范围。",
      "大范围检索与独立复核可以交给 subagent：它有自己的上下文，只把结论带回主对话。",
      "项目范围内的操作可以直接进行；提权、安装依赖和系统级变更必须先取得用户批准。",
      "用简体中文回复，先给结论，再给关键证据（涉及的文件与命令）。",
    ].join(""),
  },
  {
    rowId: "preset-minimal",
    presetId: "minimal",
    // ⚠️ 这里**不能**用官方 minimal 作来源：实测它只挂 persona + persistent-shell 两行
    // （官方那是个极简测试场景），派生出我们的学习场景会只剩 1 个工具行。
    // 学习场景要的是"功能齐全但只读"，所以来源仍是 standard，能力靠 dropRows + 只读策略裁剪。
    sourcePreset: "standard",
    // 浏览器自动化与电脑操作（见文件顶部 MING_TEA_ABILITY_GROUP 的说明）
    extraGroups: [MING_TEA_ABILITY_GROUP],
    order: 3,
    name: "辅助学习模式",
    description: "像老师一样讲清知识点、辅导作业思路、陪着复习；只读，不改动文件。",
    // 定位（2026-09-27 用户澄清）：服务**中小学生与大学生**，角色相当于一位老师。
    persona: [
      "你是铭荼，运行在“辅助学习”场景，服务的对象是中小学生与大学生。",
      "你的角色像一位耐心的老师：先弄清学生在学什么、卡在具体哪一步，再用他能听懂的语言把原理讲清楚；",
      "优先引导思考——提问、给思路、分步骤提示，而不是直接替学生做题；只有学生明确要求对照答案时才给出完整解法，并逐步解释每一步的理由。",
      "语气平和、不居高临下；用简体中文，必要时用生活化的例子或小实验帮助理解。",
      "本场景只读：不会改动学生电脑上的任何文件；需要记录的内容直接写在对话里。",
    ].join(""),
    // 学习场景同样不挂终端与后台任务；此外由 lib/index.mjs 在 agent 创建时
    // 进一步移除 write/edit（见那里的说明），使它成为真正的只读场景。
    // 注意边界：文件写入**做不到完全禁止** —— 官方 tool-fs 一次注册
    // read/write/edit 全套且没有关闭开关，所以写入仍可用，但按权限档
    // （工作区内修改 + 每次询问）逐次审批；场景人格也明确"以讲解为主、不代替动手"。
    dropRows: ["tool-bash", "tool-pwsh", "tool-jobs"],
  },
];

// ── 关于开发场景打开的三个能力（2026-09-27，用户确认）────────────────────
// 都在 rowToggles 里放开，仅开发场景生效。两点必须如实知道的边界：
//
// 1. `tool-subagent-codex`：调用本机的 codex CLI（**本机已装，实测可用**）。
//    刻意**不开** `tool-subagent-claude-code`：DSH 已有自带的 subagent / subagent_fork，
//    外部 CLI 后端属于重复能力；而且本机未装 claude，开了只会让模型挑到一个必然失败的工具。
//    若将来确实要用 Claude Code 当委派后端，把 "tool-subagent-claude-code": true 加回 rowToggles 并先装好 CLI。
// 2. `tool-ralph`：新鲜上下文循环重构（最多 64 轮）。上游自己在
//    dsh-base/cordis.patch.yml 的注释里写明：**完成与否是工人自报，不是独立评估**。
//    因此它适合"我知道要什么、需要它自己迭代"的任务；需要客观结论时用 subagent 复核。
//
// 3. **关于工作流：我们与官方 ptc 有意分歧。** 官方 ptc 关掉 workflow-ptc/tool-workflow，
//    但 `tool-ralph` 硬依赖 `workflowEngine`（由 workflow-ptc 提供）—— 开了 ralph 就必须开它，
//    否则整个场景挂载失败（实测 `tool-ralph: waiting for workflowEngine`）。
//    既然 ralph 是用户明确要的，工作流引擎与 tool-workflow 就一起开着。
//
// 3. 关于「学习场景为什么不用官方 minimal 作来源」：实测量过，官方 minimal 只挂
//    `persona` + `persistent-shell` 两组（它是给测试用的极简场景）。若从它派生，
//    我们的学习场景会只剩 1 个工具行，连读写文件都没有 —— 那不是产品要的东西。
//    所以学习场景保留 standard 作来源，只读靠 dropRows（去掉终端/后台任务）+ 宿主侧
//    的 agent 级 restrict 实现。**官方 persistent-shell 因此仍然缺席**，这是有意的取舍：
//    它的价值是"命令状态在多次调用间保持"，对只读的学习场景收益低于引入终端的风险。
