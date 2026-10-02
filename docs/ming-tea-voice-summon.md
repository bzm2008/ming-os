# 快捷键呼出的语音助手面板（summon）

按一个系统级快捷键，**无论铭荼是否在运行**，都能像手机助手一样弹出铭荼：先只出现宠物，你说的话变成一枚悬浮胶囊，助手的回答出现在胶囊下方。

## 怎么用

| 操作 | 结果 |
| --- | --- |
| `⌥ Space`（默认，可改） | 呼出面板；面板已显示时再按一次收起 |
| `Esc` | 收起面板 |
| 对着麦克风说话 | 胶囊出现并显示识别文字；说完自动发送 |
| 助手回答 | 出现在胶囊**下方**，宠物仍在最底部 |

- 纯宠物档**是可点的，不是点击穿透**（2026-10-01 用户实测后改的）：WKWebView 只认**真实用户手势**
  才肯开麦，合成点击不算，所以「点一下宠物开始说话」是开麦的唯一可靠入口 ——
  点击穿透会把这一下让给背后的窗口。面板窗口在宠物档是 220×220。
- 配置文件是 `~/Library/Application Support/铭荼/settings.json`（应用与守护进程共用同一份）：

  | 字段 | 作用 |
  | --- | --- |
  | `hotkey` | 快捷键，默认 `alt+space`。支持 `alt/option`、`ctrl/control`、`shift`、`cmd/command/super/meta` 加一个主键（`space`、字母、数字、`F1`-`F12`） |
  | `repoRoot` | DSH runtime 所在仓库根（用于找到 `.ming-tea/runtime/node_modules/.bin/dsh`） |
  | `dshBin` | 直接指定 `dsh` 可执行文件（优先于 `repoRoot`） |
  | `command` | **仅开发/测试**：按下热键时执行这条命令，而不是 `open mingtea://summon` |

  热键改动重启守护进程生效（或注销重登）。

  ⚠️ 为什么需要 `repoRoot`/`dshBin`：应用被 Dock 或 `open mingtea://summon` 拉起时**不继承 shell 环境**，
  `MING_TEA_REPO_ROOT` 这类变量拿不到；而 DSH runtime 目前**还没打进 .app**，所以要把位置写进配置。
  runtime 进包之后这两项就可以省掉。

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

### 热键没反应时怎么查（2026-10-01 补）

两个已知的「按了没反应」成因，以及对应的兜底：

1. **首选键被别的应用占了**。守护进程现在按**候选列表**注册：
   `hotkey` 写字符串时自动补上 `ctrl+alt+space`、`cmd+shift+space` 两个备用键，
   也可以自己写成数组（`"hotkey": ["ctrl+alt+space", "alt+space"]`）。
   第一个注册成功的生效，并写进 `~/Library/Application Support/铭荼/hotkey-state.json`：

   ```json
   { "active": "alt+space", "configured": ["alt+space","ctrl+alt+space","cmd+shift+space"], "pid": 6558 }
   ```

   应用的 `hotkey_status` 命令会读它，界面可以显示「按哪个键」。
2. **注册"成功"但按键仍不生效**：实测 macOS **允许两个进程都注册同一个热键**（我起了两个
   守护进程，两个都报「已注册 alt+space」）。所以「注册成功」不等于「事件一定到我们手里」——
   若某个启动器(Raycast/Alfred 之类)也占着同一个键，它可能先收到。
   判断办法：看 `~/Library/Logs/铭荼/hotkey.log` 里**有没有「触发」那一行**。
   有 = 键到了守护进程（问题在后续的 `open`）；没有 = 键被别的应用拿走了，换个键即可。

（这次的候选回退逻辑是用「首选键不可解析」验证的：日志出现
`快捷键 "not-a-real-key" 解析失败，跳过` → `已注册 ctrl+alt+space`，状态文件 active 同步更新 ✓。）

### 「按键到底有没有送达守护进程」—— 我自己测不了，只能你按一下（2026-10-02 记录）

截至 2026-10-02，**这条链路仍然没有真人按键的成功记录**（`hotkey.log` 里 0 条「触发」）。
我尝试用应用自己的电脑操作工具（CUA）发**合成**按键来自动化这一步，结论是**这条路走不通**，
过程与证据如下（免得下一个人重复踩）：

| 实验 | 结果 |
| --- | --- |
| 把守护进程切到无人占用的 `ctrl+alt+shift+f9`，用 CUA `hotkey`（`keys=["ctrl","alt","shift","f9"]`, `scope=desktop`）发一次 | 工具返回 `Pressed desktop hotkey ctrl+alt+shift+f9.`，**`hotkey.log` 无「触发」** |
| 同上的 `⌥Space`（合成） | 同样无「触发」；顺带看桌面截图，ChatGPT 也没有被唤起 |
| **对照**：同一个工具发 `⌘⇧3`（macOS 系统截图快捷键） | **成功** —— 桌面真的出现了 `截屏2026-10-02 12.27.34.png` |

所以：**CUA 的合成按键能进系统级热键（`⌘⇧3` 生效），却到不了我们注册的应用级 Carbon 热键。**
为排除「是不是我们的守护进程写错了」，我又写了一个**同构极简探针**
（`platform/ming-tea/apps/hotkey-daemon/examples/hotkey_probe.rs`：同一个 crate、同一个组合键、
同样在主线程跑 `CFRunLoopRun`），停掉守护进程后单独跑它 —— 合成按键**它也没收到**。
⇒ 结论是**测量手段无效**，不是「守护进程坏了」；真实按键的送达与否仍未验证。

**给你的排查顺序**（按一次键，看日志）：

```bash
tail -f ~/Library/Logs/铭荼/hotkey.log        # 按一次热键，看有没有「触发 …」这一行
```

- 有「触发」→ 键到了守护进程，接着看有没有 `已执行 open mingtea://summon`；那条链路已单独验证过。
- 没有「触发」→ 键没到我们手里。两种可能：①被别的应用（启动器）抢了 —— 本机装了
  **ChatGPT.app**，而 `⌥Space` 正是它的默认唤起键，这是首选嫌疑；②注册本身无效。
  区分办法：把 `~/Library/Application Support/铭荼/settings.json` 的 `hotkey` 换成
  `ctrl+alt+space`（或 `ctrl+alt+shift+f9`）后 `launchctl kickstart -k gui/$(id -u)/cn.mingos.mingtea.hotkey`，
  再按一次；仍然没有「触发」就换探针跑（见下），把「注册无效」和「被抢」分开。

```bash
# 探针：停守护进程 → 跑探针 → 按一次 ctrl+alt+shift+f9 → 看有没有 TRIGGERED
launchctl bootout gui/$(id -u)/cn.mingos.mingtea.hotkey
cd platform/ming-tea/apps/hotkey-daemon && cargo run --release --example hotkey_probe
# 看完恢复：
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cn.mingos.mingtea.hotkey.plist
```

#### 日志现在能区分三种「按了没反应」（2026-10-02 加的诊断）

守护进程原先只记「触发」，于是「按了没反应」分不清是**键没到**还是**到了但没匹配**。
现在**任何**到达的热键事件都会留一行：

| 日志里看到 | 含义 | 下一步 |
| --- | --- | --- |
| 只有「已注册 …」「进入事件循环」，按了也没有新行 | 键**根本没到**我们手里 | 换键；确认没有启动器抢（本机装了 ChatGPT.app，`⌥Space` 是它的默认唤起键） |
| `收到热键事件 id=…  state=Pressed —— 未匹配，未执行动作` | 事件到了但 `id` 和注册的不一致 | **我们的 bug**：注册 id 与事件 id 对不上，要改匹配逻辑 |
| `触发 alt+space` + `已执行 open mingtea://summon` | 键到了、动作也发了 | 剩下看应用侧有没有 `[summon] 面板已显示` |

#### 不靠截图判定「面板在不在屏幕上」：窗口探针

```bash
clang -O2 -framework CoreGraphics -framework CoreFoundation scripts/winlist.c -o /tmp/winlist
/tmp/winlist            # 只列在屏上的窗口
/tmp/winlist --all      # 连隐藏/其它 Space 的窗口一起列
/tmp/winlist --all | grep 铭荼
```

判读：summon 宠物档 = `layer=5`、约 `220x220`、标题「铭荼助手」；
`onscreen=0` 说明窗口存在但**不在当前屏幕上**（被隐藏或在别的 Space）——
这正是「壳日志说面板已显示、人却说没看见」的典型成因，也能用来断言档位几何。

**实测提醒（2026-10-02）**：如果应用是被**后台方式**拉起的（例如从脚本 `nohup` 启动），
它的窗口可能停在别的 Space / 处于隐藏态（`/tmp/winlist --all` 里 `onscreen=0`）。
`open -a "<铭荼.app>"` 能让主窗口回到当前屏幕（实测 `onscreen` 由 0 变 1）。
热键路径里面板显示后会 `set_focus()`，理论上也会把它带到前台；若用户报「按了没反应」，
先用这个探针看一眼——**面板可能真的开了，只是没开在他眼前的那个 Space 里**。

## 三态是怎么驱动的

1. 面板窗口在应用启动时就**预建好（隐藏）**，所以热键后立刻可见；DSH host 没就绪时先显示一张「正在唤醒…」的本地页（`apps/desktop/public/waking.html`）。
2. 我们插件在 `document-start` 通过壳注入的 `window.__MING_TEA_SUMMON` 得知自己处于 summon 模式，然后每轮（180ms 节流）判定档位：
   - 没有语音相态、没有文字 → `pet`（只有宠物）；
   - 出现官方语音相态 `[data-voice-activity]` 或输入框有字 → `listening`（胶囊）；
   - 出现 `[data-turn-process-answer]` → `answer`（胶囊 + 下方回答）。
3. 插件把档位通过**壳的本地上报口**告诉壳（`http://127.0.0.1:<port>/stage?t=<token>&stage=…`，只绑 loopback、带 token），壳据此改窗口尺寸/位置并切换点击穿透：

   | 档位 | 窗口 | 交互 |
   | --- | --- | --- |
   | `pet` | 220×220 | 可交互（**必须**：开麦要真实用户手势，见上文） |
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

1. **模型必须能看图 —— 而且要真的「声明」能看图**（2026-10-02 补上的一个真缺陷，见下一节）：
   看起来能读图的模型有 `auto`（默认模型，实测 4/4 正确）、`deepseek-v4.1-flash`、
   `mimo-v2.6-flash`、`glm-5.3`；`glm-5.2` 明确说不能看图。声明名单见下一节 ——
   **声明比能力本身更要紧**：没声明的模型即使能看图，截图也会在工具层被拦掉。
2. **权限**：屏幕录制（截图）、辅助功能（点击输入）；缺哪个面板会显示可点的提示条。
3. **审批**：进到「操纵电脑」这一步必须经过用户同意。

### 「模型不能看图」这个坑（2026-10-02 修复，我先前一条结论是错的）

**症状**：代理能点（像素坐标点得挺准），但**看不见**。实测里它一边点一边说
「There's no screenshot available to me (image unavailable — model doesn't support images)」，
只能靠猜坐标硬试。

**根因**：`read_image` / 电脑操作工具取快照时会被拦下：

```
cannot read "/…/calc_before.png" as an image:
model "deepseek-v4.1-flash" does not declare image input; switch to an image-capable model
```

DSH 判断「这个模型能不能收图」看的是模型条目上的 `inputModalities`；而 pi-ai 的模型条目
**缺省是 `["text"]`**（`DEFAULT_INPUT`）。我们的站点路由是**手写**的，只声明了
`id/name/contextWindow/reasoningEfforts`，没声明 `input` ⇒ 在工具层就被拒，
截图根本到不了模型（模型侧其实完全能读图 —— 站点 API 直连实测同一张截图，
`deepseek-v4.1-flash` 正确答出「计算器」与显示区「0」，见下方证据）。

**修法**（`lib/host/model-route.mjs`）：给站点实测能看图的模型显式声明
`input: ["text", "image"]`，其余一律 `["text"]`。名单是
**`auto`、`deepseek-v4.1-flash`、`mimo-v2.6-flash`、`glm-5.3`**。
`auto` 在名单里是**更正后的结论**：先前写「auto 传图返回空、所以别用 auto 看屏幕」，
2026-10-02 用同一张真实截图连测 4 次，auto **全部正确读图**
（上游分别落到 `gpt-5.4-nano` ×3、`claude-haiku-4-5` ×1），那句话没能复现、已撤回。
**这一点很关键**：免费层用户只有 `auto` 可选，若不给 auto 声明 image，
等于他们永远用不了「看屏幕」——而这是我们自己拦的，不是站点做不到。
改完打开一次页面即生效（`modelsSync` 会按内容差异重写路由配置，写入位置是
`.ming-tea/runtime/dsh-home/profiles/ming-tea/cordis.patch.yml` 的
`llm-pi-ai.providers.ming-tea-hub`，可用 `grep -A4 'id: auto'` 核对）。

**仍未实测的边界（如实）**：`auto` 的声明已落盘、也有离线断言，但
DSH 层的端到端只用 `deepseek-v4.1-flash` 完整跑过（两者走的是同一条声明→工具放行代码路径）；
`glm-5.3-flash`/`deepseek-v4-pro`/`deepseek-v4-flash`/`step-3.7-flash` 目前只声明文本
（未实测其视觉能力，保守起见不声明）。

**关于默认模型**：路由默认是 `auto`（免费额度走它）。既然 auto 自己就能看图，
**不需要**为了「看屏幕」去改默认值或钉死付费模型 —— 之前的顾虑建立在
「auto 不能看图」这个已被推翻的判断上。预设层本来就钉不了模型
（`@deepseek-ai/dsh-agent-preset` 里没有任何 model 字段），现在也不需要钉了。

**面板还会兜一层**：万一某个模型确实不能收图（例如用户手动选了名单外的模型），
面板会把那句英文工具错误**翻译成可操作的提示**（`mingTeaHumanizeError`，
只翻译确定的两种官方措辞，其余原样透出）：用户看到的是
「操作失败：当前模型不能看图，请在输入框旁换一个支持图片的模型
（例如 deepseek-v4.1-flash），再说一次。」而不是一句英文。
三种情况都在真实页面上验证过：`does not declare image input` ✓、
`does not support image input` ✓、无关错误（`bring_to_front … was not verified as frontmost`）
**原样透出** ✓（都是 tone=error、stage=answer）。
⚠️ 改了 `ui-tweaks.js` 一定要 `node scripts/build.mjs` 重新构建 `lib/client.js`，
否则宿主与页面继续跑旧包；`scripts/check_ming_tea_hub.mjs` 现在有一条**产物新鲜度**断言专门守这个。

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

（**注意这条旧证据的边界**：它只证明模型能拿到窗口/应用清单（那是无障碍树的**文本**），
**不能**证明它能看图 —— 当时的配置里模型并没有声明 `image`，截图其实被工具层拒了。
真正把「截图 → 决策 → 动手」跑通的是 2026-10-02 那次，见下一节。）

### 实测证据：截图 → 决策 → 点击（2026-10-02，修好 `input` 声明之后）

环境：打包版应用（壳监督的 DSH host）+ Playwright 驱动 host 页面；模型按钮显示
`deepseek-v4.1-flash`；五项权限探测**全部 available**（辅助功能/屏幕录制/麦克风/终端/诊断）。
任务只给一句：「把计算器窗口切到前台，先点 AC 清空，依次点 7、+、3、=，再用截图确认显示区」。

一次会话（1 轮 / 28 步 / 1.4M tok / 4 分 12 秒）里观察到：

- **模型在从画面读坐标**：思考里出现「the `%` button (center 145,185) roughly centered」
  这类描述，紧接着就是 `cua_driver_native__click · {…"x1":58,"y1":150,"x2":118,"y2":210}`；
- 显示区按 `7 → 7+ → 7+3 → 10` 推进，最终回答：「显示区结果：10（上方还保留算式 7+3）」；
- 它自己交代了两条真实限制：计算器的**无障碍树是空的**（`elements=0`，所以只能按截图坐标点）；
  「后台输入被拒（窗口不在当前 Space）」⇒ 走前台 CGEvent 投递；
  `bring_to_front` 无法独立验证 frontmost，靠每次点击后的截图（红黄绿灯点亮、显示区变化）判断；
- 审批行为符合设计：点过「本会话信任」后，**点击类不再问**，但
  「模拟键盘输入或读写剪贴板」这类**仍然每次都问**（本次会话共弹 5 次，其中 3 次是键盘输入）。

**独立复核（不靠它的自述）**：把上一轮留下的真实 macOS 截图
（`~/Documents/deepseek-harness/Default workspace/calc_before.png`）直接发给站点 API：

```
model=deepseek-v4.1-flash + 图片 → 「①计算器（Calculator） ②0」（prompt_tokens 253）
```

⇒ 站点模型确实能读真实截图（也说明我们的 `input:["text","image"]` 声明属实），
当时「0」正是点击前的显示值。

**证据边界（如实）**：最终「10」是**代理自己读它自己的截图**得出的结论，
我本人无法独立复核这一张 —— 本会话的主模型不接受图像输入，而从 bash 调
`osascript`/`screencapture` 因没有辅助功能/屏幕录制权限会卡住（实测挂起，已终止）。
可独立确认的是：计算器进程确实被拉起（`Calculator` pid 28387）、
截图能被站点模型正确读出内容。

## 响应速度（2026-10-01 实测）

| 场景 | 从按下热键到… | 实测 |
| --- | --- | --- |
| **预热**（应用在跑，面板已预建） | 面板被处理并显示/收起 | **132 ms** |
| **冷启动**（应用完全退出） | 出现宠物（唤醒页） | **2.9 s** |
| **冷启动** | 再等到 DSH host 就绪（面板能加载真界面） | **4.8 s**（总计） |

预热路径之所以快，靠两件事：面板窗口在应用启动时就**预建好（隐藏）**，热键只切可见性；
DSH host 早已就绪，不需要现起。冷启动那 3 秒里用户看到的是「正在唤醒…」那张本地页
（`apps/desktop/public/waking.html`），不是空白。

### 冷启动只弹面板：URL 路径也修好了（2026-10-02）

上面那张表里的冷启动是用 `--summon` 命令行参数测的，而**守护进程实际做的是
`open mingtea://summon`** —— 两条路不一样：macOS 不会把 URL 放进 argv（走 Apple Event），
所以判断「是不是被召唤启动」的那段 argv 逻辑对冷启动**不成立**，实测主窗口照样被弹了出来。
现在（都用 `scripts/winlist.c` + `~/Library/Logs/铭荼/app.log` 断言过）：

| 启动方式 | 行为 | 证据 |
| --- | --- | --- |
| `open mingtea://summon`（= 热键路径） | **只弹面板**，不显示主窗口；面板首帧就在底部 `220×220 @(850,720)` | 日志 `[setup] 被 summon URL 拉起（100ms 后确认）`、`[main]` 行数 = 0 |
| 普通启动（Dock / `open -a`） | 显示主窗口，不弹面板 | 日志 `[main] 显示主窗口` = 1 行，`只弹面板` = 0 |

三处具体改动：①主窗口在 `tauri.conf.json` 里改成 `visible: false`，由 `open_main_window` 决定何时显示，
这样不会先闪一下再消失；②setup 里异步等最多 1 秒读初始 URL —— 若事件比我们的 `on_open_url`
注册更早发出（实测会这样：`get_current()` 有值、回调没收到），就**自己补处理一次**
（用 `SUMMON_HANDLED` 原子标记避免重复 → 重复会变成「再按一次收起」）；
③摆位放到 `window.show()` **之后**：窗口没被 OS 映射时设的位置不生效，
原先冷启动会先按系统默认位置露一下（实测 `y=215`，本该 `720`）再跳过去；
顺带让 `set_focus()` 失败**不再中断**摆位（原先 `?` 提前返回，面板就停在错位置）。

### 打包版现在有日志可看了：`~/Library/Logs/铭荼/app.log`

打包版被 LaunchServices 拉起时 **stderr 无处可去** —— 实测
`log show --predicate 'process == "ming-tea-desktop"'` 里一条我们自己的行都没有
（只有 WebKit 噪音），于是「冷启动到底弹了哪个窗口」这类问题既没法验证、用户也拿不到现场。
现在启动第一件事就是把 fd 2 指向 `~/Library/Logs/铭荼/app.log`（`src/logging.rs` 里一行 `dup2`，
我们自己的 `eprintln`、panic、依赖库的警告全都会落进去；超过 2 MB 下次启动时重开）。
排障第一步就是看这个文件，例如：

```bash
tail -30 ~/Library/Logs/铭荼/app.log        # 壳的启动/召唤/宿主日志
tail -5  ~/Library/Logs/铭荼/hotkey.log     # 守护进程（热键）日志
```

**一个打包坑（我踩了）**：直接 `cp` 新二进制覆盖**已签名** `.app` 里的主可执行文件会**破坏签名**，
macOS 随后拒绝启动（`Launchd job spawn failed`）。要么走正规打包
（`pnpm exec tauri build --bundles app`），要么覆盖后 `codesign --force --deep --sign - 铭荼.app`。
顺带实测：重建导致的 ad-hoc 签名标识变化**没有**清掉 TCC 授权
（辅助功能/屏幕录制/麦克风五项探测重建后仍全部 `available`）。

## 语音：链路已就位，缺一个 runtime 依赖已补上（2026-10-01）

**两个真问题，一个已修、一个只是测试环境限制：**

### ① 更正：语音 worker **不需要** tsx（我先前的判断错了，已撤回）

我曾判断「语音 worker 是 `.ts`、runtime 缺 `tsx` 导致所有人语音准备不起来」，并据此把
`tsx` 钉进安装脚本。**这个判断是错的**，证据在 provider 自己的代码里：

```js
// @deepseek-ai/dsh-experimental-speech-to-text-sensevoice/lib/index.js:218
worker: fileURLToPath(new URL(import.meta.url.endsWith(".ts") ? "./worker.ts" : "./worker.js", import.meta.url))
```

发布包是 `.js` ⇒ 永远用 `lib/worker.js`，`--import tsx/esm` 那条分支根本不会走。
实测也确实如此：`speech.prepare` 成功后拉起的进程是
`node …/sensevoice/lib/worker.js {"providerId":"sensevoice-local",…}`。**tsx 已从安装脚本撤回。**

那当时为什么看起来卡住了？两个真实原因，都不是 tsx：
1. **我自己的 RPC bug**：`speech.config.defaultProvider` 是**响应式引用**不是字符串，
   直接传给 `prepare` 会被注册表当成 `"[object Object]"` → `provider is unavailable`。
   修掉之后 `prepare` 返回 `{ok:true, provider:"sensevoice-local"}` 并成功拉起 worker。
2. **测试环境没有音频输入设备**（见下条）。

保留的改动：面板呼出时主动调 `speech.prepare` 预热 provider（模型加载要时间，
等用户点麦克风再加载就是干等），以及 `speech.status` 便于排障。
**预热本身也修过一次**：早期版本在第一轮 tick 就调用、并且**提前把 `speechWarmed` 置位**，
而那时 `connection` 服务还没注入 ⇒ 报「宿主连接不可用」且永不重试，打包版里预热一直没生效
（实测）。现在改成「成功才算预热完成 + 有限次重试」，并把它写进
`data-ming-tea-speech-prepare` 便于排障。

### ② 只是测试环境限制：Playwright 的 Chromium 没有音频输入设备

实测该环境里 `navigator.mediaDevices.getUserMedia({audio:true})` 直接失败
（`Could not start audio source`），所以**浏览器采集**这一段自动化覆盖不到，必须真机真麦。

不过「模型能不能把中文语音转成文字」这一半**已经可以离线验证**（见下一节）——
所以现在真正缺的只剩「麦克风 → 浏览器编码成 WAV → 上传」这一段采集链路。

### ③ 语音识别本体：用合成语音离线验证（2026-10-02，`scripts/check_sensevoice_asr.mjs`）

**为什么能这样测**：官方 provider 的 `transcribe` 本质是「POST 到 worker 起的本地 HTTP 服务」
（`lib/worker.js` 的 `startRecognitionServer`），而那个服务收的就是
**规范 16 kHz 单声道 PCM16 WAV** —— 浏览器录音也是编码成这个格式再上传的。
于是「合成语音 → 识别」可以完全离线跑：macOS 自带 `say` 合成中文、`afconvert` 转格式、
直接 POST 给 worker。跑法：

```bash
node scripts/check_sensevoice_asr.mjs     # 退出码非 0 = 识别结果不符预期
```

实测结果（Tingting 语音，本机 Apple Silicon）：

| 说的 | 转写 | 模型耗时 |
| --- | --- | --- |
| 帮我看看屏幕上有什么 | 「我看看屏幕上有什么。」 | 0.07s |
| 把音量调大一点 | 「把音量调大一点。」 | 0.05s |

模型加载 0.7–0.8s（磁盘缓存已热），一次 2 秒语音的端到端往返约 **0.09s**，标点是模型自己加的
（SenseVoice 的逆文本规整）。第一条开头掉了一个「帮」，属 TTS 与 VAD 切分的正常损耗，
语义无损 —— 断言因此用「包含关键片段」而不是逐字相等。

**踩到的坑（值得记住）**：`afconvert` 产出的 WAV **不是**规范 44 字节头（会插 FLLR 等块），
而 worker 的 `validateWave` 逐字段校验（`data` 必须正好在偏移 36、`fmt` 块正好 16 字节、
各长度字段自洽），所以报 `Invalid speech WAV`。脚本里会把 PCM 取出来**重写规范头**再发。

### 已核实的部分（都不需要真麦克风）

- 语音三行在运行时里正常挂载：`speech-to-text`（`defaultProvider: sensevoice-local`）、
  `speech-to-text-sensevoice`、`api-speech-to-text`、`ui-voice-input`；
- `speech.status` 返回 `{available:true, providers:["sensevoice-local"]}`；
- `speech.prepare` 返回 `{ok:true, provider:"sensevoice-local"}`，并且**真的拉起了 worker 进程**；
- 模型文件齐全且 sha256 与官方清单一致（242 MB）；原生依赖 `sherpa-onnx-node@1.13.8`
  与平台包 `sherpa-onnx-darwin-arm64`（含 `sherpa-onnx.node`、`libonnxruntime.dylib`）都在；
- **真实音频转写**：`scripts/check_sensevoice_asr.mjs` 两条中文语音全部识别正确（见上一节）；
- **整条链路端到端**（`scripts/check_voice_pipeline.mjs`，假麦克风喂合成语音）：
  输入区录完文字落进输入框 ✅、面板胶囊显示转写 ✅、点太早时的重试 ✅（见下一节）；
- 面板侧：**点一下面板** → 官方麦克风按钮被点到 → 相态进入 `requesting`、
  面板切到 `listening` 档并显示「我在听…」（`tapResult=clicked`，实测）。

### ④ 整条语音链路端到端（假麦克风，2026-10-02）

**关键技巧**：Chromium 能把一个 WAV 文件当成麦克风 ——
`--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<file.wav>`。
于是 `getUserMedia` 不再报「没有音频输入设备」，而**后面每一环都是真的**：
真实采集、真实重采样与编码（麦克风报 48 kHz，上传前编码成 16 kHz 规范 WAV）、
真实上传、真实本地模型推理。脚本 `scripts/check_voice_pipeline.mjs` 三种模式都跑：

```bash
node scripts/check_voice_pipeline.mjs              # 三种模式
node scripts/check_voice_pipeline.mjs --mode panel # 只跑某一个
```

| 模式 | 做了什么 | 实测结果 |
| --- | --- | --- |
| `composer` | 点官方「开始录音」→ 喂 8 秒合成语音 → 停止 | 文字落进输入框：「我看看屏幕上有什么，帮我看看屏幕上有什么…」（文件在 8 秒里循环了几遍） |
| `panel` | summon 模式下点宠物 → 胶囊「我在听…」→ 停止 | **转写出现在胶囊里**（用户要的就是这个） |
| `early-tap` | 语音按钮还不可用时点宠物，随后恢复 | 胶囊立刻显示「正在准备麦克风…」，1 秒后自行进入录音 |

**踩到的两个坑（都写进脚本注释了）**：

1. **录音中官方会换按钮**：麦克风按钮从「开始录音」变成「取消」，旁边另起一个「停止并识别」。
   按 `aria-label*="录音"` 去找停止按钮会**点空** —— 我第一版脚本因此以为点了停止，其实那次是
   「自动停止」兜住的，结论差点错。正确做法是点 `button[aria-label="停止并识别"]`。
2. **summon 模式下输入区在屏幕外**（`left:-10000px` + `pointer-events:none`，这是设计），
   Playwright 的真实点击会报 `Element is outside of the viewport`。要停止录音必须用**页面内 JS 点击**
   （`page.evaluate(() => button.click())`）——插件的「点一下说话」本来就是这么点的。

**顺带修掉一个真缺陷：点得太早会「没反应」**。官方语音行是懒挂载的（宿主刚起时约 15 秒才出现），
而插件原先在点击时找不到麦克风按钮就**直接放弃**（只把 `data-ming-tea-tap-result` 记成 `no-trigger`）——
用户表现为「点了宠物什么都没发生」。现在改成：记下意图 + 立刻在胶囊里给一句
「正在准备麦克风…」+ 每轮 tick（180ms）重试，成功后置 `data-ming-tea-tap-result=clicked-after-wait`；
约 12.6 秒仍挂不上就如实显示「麦克风还没准备好，再点我一下」。三种状态都有断言覆盖。

### 更正一条我先前的错误结论

我曾在文档里写「语音行只存在于会话内、新任务落地页没有」——**这是错的**：
原因是①缺 tsx 时语音行根本不挂载，②我早期探针只等 13–14 秒，而语音行在模型就绪后
约 **15–17 秒**才挂载。现在落地页上「开始录音」按钮稳定出现。

（另记：语音行是**懒挂载**的，所以 summon 模式**不能**用 `visibility:hidden` 藏输入区 ——
已改为「移到屏幕外 + 全透明」，React 认为它正常可见，照常挂载。）

## 面板里能看到什么（三种语气）

summon 模式把官方对话区藏起来了，所以**工具活动与失败必须主动镜像**，否则用户只会觉得
「面板没反应」。回答区按这个优先级显示（实测四种状态都验证过）：

| 情况 | 显示 | 样式 |
| --- | --- | --- |
| 新一轮回答到达 | 回答正文 | 正常 |
| 工具失败且还没有新回答 | `操作失败：<站点/工具给的原因>` | 红边、浅红底 |
| 电脑操作工具正在跑（`cua_driver_native__` / `playwright-mcp` / `computer` 关键字） | `正在看屏幕…` | 斜体灰字 |
| 其余 | 最近一次回答 | 正常 |

**两个实测踩到的坑**：①「工具失败 → 模型随后解释」时，如果错误优先级恒定高于回答，
面板会一直停在旧错误上、用户看不到模型已经解释了 —— 所以**新回答到达要清掉挂起的错误**；
②`[data-turn-process-answer]` 是**上一条**回答也会一直在 DOM 里，所以判断必须用
「文本是否变化」而不是「是否存在」。

## 权限

面板与语音/看屏幕需要三类 macOS 权限，**都无法程序化授予**，第一次会弹系统窗口：

| 权限 | 用在哪 | 缺了会怎样 |
| --- | --- | --- |
| 麦克风 | 听你说话 | 语音按钮点了没反应 |
| 屏幕录制 | 「看屏幕」截图 | 助手看不到当前屏幕 |
| 辅助功能 | 点击/输入（操纵电脑） | 只能回答，不能动手 |

壳里有真实探测（`permissions.rs`：`AXIsProcessTrusted` / `CGPreflightScreenCaptureAccess` / `AVCaptureDevice`）与命令 `permission_status`、`permission_open_settings`，可以一键跳系统设置对应页。注意：`packages/agent/src/platform-status.ts` 里那份清单是**硬编码**的旧实现，新的以壳为准。

**当前真机状态（2026-10-02 实测，直接读壳的上报口）**：
`GET http://127.0.0.1:<beacon>/permissions?t=<token>`（token 只在 `MING_TEA_DEBUG_BEACON=1` 时打到 stderr）返回五项**全部 `available: true`**：
辅助功能、屏幕录制、麦克风、终端、诊断。探测是诚实的 —— 麦克风只有在
`AVAuthorizationStatus::Authorized` 时才报 available，`NotDetermined`（还没问过）会如实报
`available:false, permission_required:true` 并给出「第一次说话时会弹窗」的文案，
所以这个 `true` 不是「未询问」被误读。

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

## 打包（2026-10-01 打通，剩运行时一项）

```bash
cd platform/ming-tea/apps/desktop
pnpm build:hotkey                 # 构建守护进程并把二进制按 target triple 暂存到 src-tauri/binaries/
pnpm exec tauri build --bundles app   # 只打 .app（DMG 那一步在本机会失败，见下）
```

产物 `src-tauri/target/release/bundle/macos/铭荼.app` 里已经包含：

| 内容 | 位置 | 作用 |
| --- | --- | --- |
| 守护进程 | `Contents/MacOS/ming-tea-hotkey`（`bundle.externalBin`） | LaunchAgent 直接指向包内这份，用户不用另装 |
| URL scheme | `Info.plist` 的 `CFBundleURLTypes`（`mingtea`） | `open mingtea://summon` 能唤起应用 |
| 麦克风说明 | `Info.plist` 的 `NSMicrophoneUsageDescription` | 语音首次使用能正常弹权限（裸二进制做不到） |

**实测**：`open "mingtea://summon"` → 打包版应用收到 deep link 并显示面板；
LaunchAgent 的 plist 会从「开发机 repo 里的二进制」**自动刷新**成「.app 包内那份」
（应用启动时比对记录路径与当前解析路径，不一致就重写并重新 bootstrap —— 只判断「装没装过」
会导致热键指向一个不存在的位置）。

**还差的一项（分发阻塞）**：DSH runtime 本身**还没有进包**，所以打包版目前仍要
`MING_TEA_REPO_ROOT`（或 `MING_TEA_DSH_BIN`）才能找到 `dsh`。要把
`.ming-tea/runtime` 作为资源打进 `.app` 并让 `resolve_dsh()` 优先用包内路径，属于分发那一轮的事。

**DMG 那一步在本机会失败**（`bundle_dmg.sh` 报错，与 hdiutil / Finder 自动化有关，2026-09-26 就留下过失败的
`rw.*.dmg`），所以本地验收用 `--bundles app`；正式发行前要单独解决签名与 DMG。

## 已知限制 / 还没做

（2026-10-02 校订：下面几条是**当前仍然成立**的；已完成的旧条目不再留在这里，
避免把做过的说成没做。）

- **DSH runtime 未进包**：守护进程二进制已经通过 `bundle.externalBin` 进了 `.app`
  （`Contents/MacOS/ming-tea-hotkey`），URL scheme 也在 `.app` 的 Info.plist 里；
  但 `.ming-tea/runtime` 还没作为资源打进去，所以打包版仍要 `repoRoot`/`dshBin` 才能找到 `dsh`。
- **DMG 打包在本机失败**：`bundle_dmg.sh`（与 hdiutil / Finder 自动化有关，2026-09-26 起就存在），
  本地验收用 `--build --bundles app`。
- **部分模型的视觉能力未实测**：`auto`/`deepseek-v4.1-flash`/`mimo-v2.6-flash`/`glm-5.3`
  已声明 `image`（前两者有直接证据），`glm-5.2` 明确不能看图；
  但 `glm-5.3-flash`/`deepseek-v4-pro`/`deepseek-v4-flash`/`step-3.7-flash` 只声明了文本
  （没实测过它们能不能看图，保守不声明）。
- **`⌥Space` 可能与其它启动器冲突**：注册失败会写日志并按候选列表重试；
  当前生效的热键与候选列表写在 `~/Library/Application Support/铭荼/hotkey-state.json`。
- **单屏假设**：面板贴在**主显示器**底部；多屏且鼠标在副屏时不会跟过去。
- **只差「真人对着真麦克风说」这一个变量**：识别本体（`check_sensevoice_asr.mjs`）与
  采集→编码→上传→转写→落进输入框/胶囊（`check_voice_pipeline.mjs`，Chromium 用 WAV 文件当麦克风）
  都已端到端验证；剩下未验证的只是**物理麦克风这一个声源**（真人开麦）。
- **审批卡仍沿用官方版式**（已在三态面板里放行并钉到视口底部，有专门的 `approval` 档 520×600），
  没有做成铭荼自绘的胶囊风格紧凑条。

## 验证证据（2026-10-01）

- 壳侧端到端（日志）：`[single-instance] 第二次启动` → `[summon] 面板已显示` → `[summon] 档位 -> pet（220×220，interactive=true）`；用 curl 驱动上报口可依次看到 `answer（470×520，interactive=true）`、`listening（420×280）`、`pet`。
- 浏览器实测（Playwright，`addInitScript` 模拟壳注入）：`summon="1"`、初始 `stage=pet` 且**侧栏/对话区/输入框全部不可见、宠物可见**；注入语音相态 → `stage=listening` 且胶囊显示「我在听…」；注入 `[data-turn-process-answer]` → `stage=answer`、回答可见；几何断言 `answerBelowCapsule=true`、`answerAbovePet=true`、`petCentered=true`。
- 冷启动召唤：应用未运行时 `--summon` 启动**只弹面板不弹主窗口**，且**只起一个** DSH host。
- 宿主生命周期回归（两场景）：就绪后 SIGTERM、启动中途 SIGTERM，应用与 host 都一起退出、端口释放。
- 测试：vitest 37/37、`scripts/check_ming_tea_hub.mjs` 45/45。
- **用户实测确认**：冷启动召唤后屏幕上「只有宠物，没别的了」。

## 验证证据（2026-10-02 追加）

- `scripts/check_ming_tea_hub.mjs` **51/51**（新增 7 条「哪些模型声明 image」的断言）、
  vitest **37/37**（`platform/ming-tea` 全量 9 个测试文件）、`scripts/verify_presets.py` 退出码 0。
- 「截图 → 决策 → 点击」端到端：见上面《实测证据：截图 → 决策 → 点击》。
- 权限：壳上报口五项全部 `available: true`（见《权限》一节）。
- 加热键链路回归：Playwright 等插件加载完成后点宠物 → `tap=clicked`、`phase=recording`、
  `stage=listening`、胶囊「我在听…」。
  （**一条自我更正**：2026-10-02 早先我报过「插件在该次 Playwright 运行里根本没加载」——
  那是**测试脚本的时序问题**：它在朗读按钮出现的瞬间（约 3s）就检查层，而插件是按需异步加载的；
  四个对照变体（假麦克风标志 / 麦克风权限 / 260×260 小视口 / 三者叠加）全部正常加载，
  改成「先等 `__mingTeaHub` 与 `.mt-summon-layer` 出现再点」后稳定通过。）
