# 快捷键呼出的语音助手面板（summon）

按一个系统级快捷键，**无论铭荼是否在运行**，都能像手机助手一样弹出铭荼：先只出现宠物，你说的话变成一枚悬浮胶囊，助手的回答出现在胶囊下方。

## 怎么用

| 操作 | 结果 |
| --- | --- |
| `⌥ Space`（默认，可改） | 呼出面板；面板已显示时再按一次收起 |
| `Esc` | 收起面板 |
| 对着麦克风说话 | 胶囊出现并显示识别文字；说完自动发送 |
| 助手回答 | 出现在胶囊**下方**，宠物仍在最底部 |

- 纯宠物档是**点击穿透**的：一块看不见的 200×200 不会挡住你点别的东西。
- 热键在 `~/Library/Application Support/铭荼/settings.json` 里改：
  ```json
  { "hotkey": "alt+space" }
  ```
  支持 `alt/option`、`ctrl/control`、`shift`、`cmd/command/super/meta` 加一个主键（`space`、字母、数字、`F1`-`F12`）。改完重启守护进程生效（或注销重登）。

## 它由哪几块组成

```
热键守护进程 ming-tea-hotkey（随登录由 LaunchAgent 启动，无界面、不申请任何 TCC 权限）
   │ 按下快捷键 → open "mingtea://summon"
   ▼
铭荼.app（Tauri）
   │ 单实例转发 → 显示 summon 面板；同时监督 DSH host
   │ DSH host = dsh --profile ming-tea --port <随机> --no-open（只绑 127.0.0.1）
   ▼
DSH web 界面（面板与主窗口加载同一个 URL）
   ├─ 宠物插件（@michengai/dsh-codex-pet）——「只有宠物」那一档就是它
   ├─ 官方本地语音（experimental-voice-input-bundle / SenseVoice）
   └─ 我们的插件 ming-tea-ui：三态判定 + 胶囊 + 回答镜像 + 档位上报
```

### 三态是怎么驱动的

1. 面板窗口在应用启动时就**预建好（隐藏）**，所以热键后立刻可见；DSH host 没就绪时先显示一张「正在唤醒…」的本地页（`apps/desktop/public/waking.html`）。
2. 我们插件在 `document-start` 通过壳注入的 `window.__MING_TEA_SUMMON` 得知自己处于 summon 模式，然后每轮（180ms 节流）判定档位：
   - 没有语音相态、没有文字 → `pet`（只有宠物）；
   - 出现官方语音相态 `[data-voice-activity]` 或输入框有字 → `listening`（胶囊）；
   - 出现 `[data-turn-process-answer]` → `answer`（胶囊 + 下方回答）。
3. 插件把档位通过**壳的本地上报口**告诉壳（`http://127.0.0.1:<port>/stage?t=<token>&stage=…`，只绑 loopback、带 token），壳据此改窗口尺寸/位置并切换点击穿透：

   | 档位 | 窗口 | 交互 |
   | --- | --- | --- |
   | `pet` | 200×200 | 点击穿透 |
   | `listening` | 420×280 | 可交互 |
   | `answer` | 470×520 | 可交互 |
   | `approval` | 520×600 | 可交互（有审批时**必须**切到这档：审批卡原本挂在整页布局底部，小窗口里根本点不到） |

   窗口**底边固定**（距屏幕底 140px、水平居中），内容向上生长，宠物不会因回答变长而跳动。
4. 语音走官方本地 SenseVoice：**音频不出本机**，模型首次使用时下载到 `$DSH_HOME/speech-to-text/sensevoice`（那一次需要联网）。

## 看屏幕与「操纵电脑」（2026-10-01 实测通过）

「看一眼屏幕再决定做什么」不需要我们额外做截图管线：**DSH 的电脑操作工具自己带截图**
（Cua driver 的 `cua_driver_native__list_windows` / `get_accessibility_tree` / `get_window_state`…），
而 DSH 还会给模型注入一段 `GUIDANCE`（先确认应用与窗口、再取一次新鲜快照才动手）。
我们要做的只有三件事，现在都做了：

1. **模型必须能看图**：默认模型是 `deepseek-v4.1-flash`（实测能正确描述图片；
   `glm-5.2` 明确说不能看图、`auto` 会返回空 —— 所以**不要**把看屏幕的会话切到 `auto`）。
2. **权限**：屏幕录制（截图）、辅助功能（点击输入）；缺哪个面板会显示可点的提示条。
3. **审批**：进到「操纵电脑」这一步必须经过用户同意。

### 审批与「本会话信任」（用户选定的默认）

- 提示文案与闸门来自我们自己的宿主半区（`lib/index.mjs` 的 `installApprovalGate`）：
  它拦 `tools/pre-execute`，对自动化工具族（`cua_driver_native__*`、`mcp__playwright-mcp__*`）
  走审批，其余（只读类）放行。
- 审批卡上除了官方的「拒绝 / 允许一次」，我们**补了一个「本会话信任」**（`ui-tweaks.js`
  注入，主窗口与 summon 面板都有）。点它之后：
  - 同一会话内的**普通**操作（列窗口、取快照、点击、滚动）不再询问；
  - **输入/剪贴板、上传/提交、删除/安装/系统与终端命令仍然每次都问**（判定逻辑在
    `lib/host/summon-trust.mjs` 的 `shouldAutoAllow`，有 5 组离线断言）；
  - **只在本会话内有效**：内存态、不落盘、换会话或重启即失效。

### 拒绝路径（「拒绝」与「拒绝并停止」）

审批卡上除了官方的「拒绝 / 允许一次」与我们的「本会话信任」，还补了一个**「拒绝并停止」**。
为什么需要它：**只点官方「拒绝」的话，模型会换个工具再试**（实测：拒绝 `check_permissions` 之后
立刻又弹一次 `get_desktop_state`），用户会被反复打扰。「拒绝并停止」= 拒绝当前这次 + 调官方
`conversation.cancel()` 中止本轮。

实测（宿主策略日志）：

```
07:27:08 deny cua_driver_native__get_accessibility_tree（审批结果：cancelled）   ← cancel() 生效，挂起的请求被撤回
07:28:20 deny cua_driver_native__get_desktop_state（审批结果：rejected）        ← 「拒绝并停止」拒绝掉当前这次
（此后 3 分钟没有任何新的工具决策 —— 本轮真的停了，模型没有再换工具重试）
```

对比：只点「拒绝」而不用「拒绝并停止」时，模型会持续换工具重试。

### 实测证据（2026-10-01，真实站点模型 + 真实桌面）

一次会话里让它「看一下我现在屏幕上有什么」，观察到的完整链路：

```
模型决定看屏幕 → 调 cua_driver_native__list_windows / get_accessibility_tree
   → 弹审批（我们闸门的文案「将对电脑界面执行操作」）
   → 点「本会话信任」→ 之后 0 次新审批
   → 模型调 get_window_state 取到窗口快照
   → 回答：「主窗口是「铭荼」——ming-tea-desktop 的窗口（pid 85616），当前屏幕上唯一
     可见的窗口，其余应用（Safari、ChatGPT、DeepSeek Harness、访达、文本编辑等）
     都在后台运行、没有可见窗口。」
```

（对屏幕内容的描述准确，说明视觉链路真的通了，不是猜的。）

## 响应速度（2026-10-01 实测）

| 场景 | 从按下热键到… | 实测 |
| --- | --- | --- |
| **预热**（应用在跑，面板已预建） | 面板被处理并显示/收起 | **132 ms** |
| **冷启动**（应用完全退出） | 出现宠物（唤醒页） | **2.9 s** |
| **冷启动** | 再等到 DSH host 就绪（面板能加载真界面） | **4.8 s**（总计） |

预热路径之所以快，靠两件事：面板窗口在应用启动时就**预建好（隐藏）**，热键只切可见性；
DSH host 早已就绪，不需要现起。冷启动那 3 秒里用户看到的是「正在唤醒…」那张本地页
（`apps/desktop/public/waking.html`），不是空白。

## 权限

面板与语音/看屏幕需要三类 macOS 权限，**都无法程序化授予**，第一次会弹系统窗口：

| 权限 | 用在哪 | 缺了会怎样 |
| --- | --- | --- |
| 麦克风 | 听你说话 | 语音按钮点了没反应 |
| 屏幕录制 | 「看屏幕」截图 | 助手看不到当前屏幕 |
| 辅助功能 | 点击/输入（操纵电脑） | 只能回答，不能动手 |

壳里有真实探测（`permissions.rs`：`AXIsProcessTrusted` / `CGPreflightScreenCaptureAccess` / `AVCaptureDevice`）与命令 `permission_status`、`permission_open_settings`，可以一键跳系统设置对应页。注意：`packages/agent/src/platform-status.ts` 里那份清单是**硬编码**的旧实现，新的以壳为准。

## 隐私边界（必须知道）

- **音频只在本机识别**（SenseVoice），不发给站点。
- **截图会发给站点模型**（`sca-hub.cn` → 上游）——与「会话本来就走站点」一致，但这条要在产品层面明示。
- 截屏只在面板可见、且用户触发的那一轮发生，不做静默连续截屏。
- 电脑操作默认**每次都要确认**；点「本会话信任」后同一会话内不再问（内存态、不落盘、换会话失效），危险操作（删除/安装/系统修改/提权）始终确认。

## 开发与排障

```bash
# 1. 构建守护进程与壳
cd platform/ming-tea/apps/hotkey-daemon && cargo build          # 或 pnpm build:hotkey
cd platform/ming-tea/apps/desktop/src-tauri && cargo build

# 2. 开发期想直接试召唤（不必先打包 .app、不必注册 URL scheme）
#    settings.json 里给一条命令，守护进程按下热键时执行它：
cat > "$HOME/Library/Application Support/铭荼/settings.json" <<EOF
{ "hotkey": "alt+space",
  "command": "$PWD/target/debug/ming-tea-desktop --summon" }
EOF
./target/debug/ming-tea-hotkey &     # 守护进程

# 3. 起应用（跳过 LaunchAgent 安装，避免和上面这个守护进程抢热键）
MING_TEA_NO_HOTKEY_INSTALL=1 ./target/debug/ming-tea-desktop --summon
```

### 踩过的坑（都写进代码注释了）

1. **DSH host 会 303 重定向并吃掉 URL 参数**：带 token 的入口 URL `/?token=…&ming-tea=summon` 在 HTTP 层就被重定向到 `/`，连 `document-start` 都读不到 query。所以 summon 参数由壳用 `initialization_script` **以字面量**注入（而且只注入面板窗口，主窗口天然进不了 summon 模式）。
2. **DSH host 启动时缓存插件 bundle**：改完插件必须**重启 host**（或重启应用）才会生效，否则页面拿到的还是旧 bundle（表现为「改了没反应」）。
3. **macOS 的 Carbon 热键需要 run loop**：守护进程主线程必须跑 `CFRunLoopRun()`，事件处理放工作线程；否则按键永远不会到达回调（日志里一条「触发」都没有）。`global-hotkey` 的每个官方示例都在事件循环里泵消息，原因就在这里。
4. **隐藏元素上 `innerText` 是空串**：summon 模式把对话区设成 `visibility: hidden`，镜像回答必须用 `textContent`，否则回答永远不显示。
5. **宠物是内联定位**（`left/top` 写死）：窗口一大它就跑到左上角，summon 模式用 `!important` 把它钉在底部居中。
6. **`ensure_started` 必须单飞**：召唤路径与主窗口路径会并发触发，不串行化会起两个 host 抢同一个 `DSH_HOME`（实测日志里出现过两条 `host 就绪`）。
7. **被杀时要带走 host**：Rust 默认没有信号处理器，`pkill` 会让 DSH host 变孤儿；现在装了 SIGTERM/SIGINT 处理器 + pidfile 残留清理（清理前会用 `ps` 确认那个 pid 确实是我们的 dsh，避免 pid 复用误杀）。
8. **前端构建要用 `/Users/mac/.local/bin/node`（v26）**：用 runtime 自带的 node v24 跑 `vite build` 会因 rollup 原生模块 ABI 不匹配报 `ERR_DLOPEN_FAILED`。

## 已知限制 / 还没做

- **打包分发未完成**：守护进程二进制还没有通过 `bundle.externalBin` 打进 `.app`，URL scheme 也只有在**打包后的 .app** 里才注册。开发机走上面的 `command` 路径可以先跑通；正式分发前要补「守护进程入包 + 签名 + 公证」。
- **审批 UI 在三态面板里还没有专门版式**：目前沿用官方审批卡（`[data-approval-key]` / `[data-question-key]`，已在 CSS 里放行并置顶），没有做成胶囊风格的紧凑条。
- **「本会话信任」还没接进 DSH 的审批流**：当前 DSH 侧的审批仍按它自己的策略走；会话级信任需要在我们的宿主半区接一个 hook。
- **`⌥Space` 可能与其它启动器冲突**：注册失败会写日志并每 30 秒重试。
- **单屏假设**：面板贴在**主显示器**底部；多屏且鼠标在副屏时不会跟过去。
- **语音需要真人开麦**：自动化测试覆盖不到麦克风链路（我用浏览器假设备只验证过组件挂载与相态）。

## 验证证据（2026-10-01）

- 壳侧端到端（日志）：`[single-instance] 第二次启动` → `[summon] 面板已显示` → `[summon] 档位 -> pet（200×200，interactive=false）`；用 curl 驱动上报口可依次看到 `answer（470×520，interactive=true）`、`listening（420×280）`、`pet`。
- 浏览器实测（Playwright，`addInitScript` 模拟壳注入）：`summon="1"`、初始 `stage=pet` 且**侧栏/对话区/输入框全部不可见、宠物可见**；注入语音相态 → `stage=listening` 且胶囊显示「我在听…」；注入 `[data-turn-process-answer]` → `stage=answer`、回答可见；几何断言 `answerBelowCapsule=true`、`answerAbovePet=true`、`petCentered=true`。
- 冷启动召唤：应用未运行时 `--summon` 启动**只弹面板不弹主窗口**，且**只起一个** DSH host。
- 宿主生命周期回归（两场景）：就绪后 SIGTERM、启动中途 SIGTERM，应用与 host 都一起退出、端口释放。
- 测试：vitest 37/37、`scripts/check_ming_tea_hub.mjs` 45/45、Python 8/8。
- **用户实测确认**：冷启动召唤后屏幕上「只有宠物，没别的了」。
