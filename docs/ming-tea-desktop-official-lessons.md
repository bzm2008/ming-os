# 官方 DSH 桌面客户端调研：铭荼可借鉴什么

> **版本说明（2026-10-01 追记）**：本文写作时铭荼 runtime 锁 `0.1.7-rc.1`；此后已升到 **`0.2.0-rc.2`**。
> 文中「官方版本」的结论仍按当时的 `0.2.0-rc.2` 源码为准；文中提到在 `0.1.7-rc.1` 上实测过的那一条
> （`dsh-web-frontend/dist` 存在）**在 0.2.0-rc.2 上已复核，同样存在**（`index.html`、`assets/`、`favicon.svg`、
> `favicon-dark.svg`、`manifest.webmanifest`）。其余「未验证」项保持原样，不要把本文当作 0.2 的验收记录。

> **来源与折扣（先读这段）**：本文是对 `github.com/deepseek-ai/deepseek-harness` 的 `apps/desktop`
> （包名 `@deepseek-ai/dsh-desktop`，Electron 壳）源码与第一方设计笔记（`.agents/notes/implemented/architecture/`）的
> **只读阅读**，commit `639ed015`，该 commit 的 `package.json` 版本是 **`0.2.0-rc.2`（2026-09-29）**。
> 铭荼 runtime 锁的是 **`0.1.7-rc.1`**，仓库无 tag，因此**无法**逐行确认 0.1.7-rc.1 时壳代码是否相同。
> 凡写「官方这么做」，严格读作「0.2.0-rc.2 这么做」。本文**不含运行观测**（没有构建也没有启动官方 desktop）。
> 唯一在 0.1.7-rc.1 上实测过的条目已单独标注。

## 一、结论速览

| # | 结论 | 对铭荼的影响面 |
| --- | --- | --- |
| 1 | 官方**不用 stdout 抓 URL**：壳 spawn 私有 host 包，子进程用 Node IPC 信道 `process.send({type:'ready',url,injections})` 上报，再用 `fetch(url,{redirect:'manual'})` 换 `303 + Set-Cookie` 拿鉴权 cookie | `dsh-adapter.ts`、`lib.rs`、协议；**「抓 stdout 的 URL+token」这条计划要整体改掉** |
| 2 | 生命周期有**显式协议版本号** `DESKTOP_HOST_PROTOCOL_VERSION = 4`，写进 runtime 描述符，不匹配即拒绝启动 | Shell↔Agent 契约 |
| 3 | 关停是**分级升级 + 有确认的优雅退出**：`shutdown` → 10s 等退出 → SIGTERM → 5s → SIGKILL → 5s → 抛错；装更新时额外要求 graceful 且收到 `shutdown-complete` | `lib.rs` 进程管理（现在只有一句 `child.kill()`） |
| 4 | 控制类请求是**带 requestId + 死线**的请求/应答（更新前盘点 10s、退出前 2s），**超时/失败一律按「有任务在跑」处理**（保守侧） | 协议设计、退出确认 |
| 5 | profile 隔离靠**独占目录 + PID 锁文件**：`$DSH_HOME/profiles/desktop` 与 CLI 共享数据根、独占可执行包；`lock` 写 PID 用 `process.kill(pid,0)` 判活；**首启只写元数据、不跑 pnpm** | `.ming-tea` 布局、`install_ming_tea_plugins.sh` |
| 6 | 前端复用靠**自定义特权 scheme**：静态资源从 **dsh 包内 `dsh-web-frontend/dist`** 本地读，其余带 cookie 转发给 Host；WS 单独注入 cookie 并强制 `origin` | 嵌入官方 Web 前端 |
| 7 | 版本绑定靠**一份 runtime 描述符**：`release{version,hostProtocolVersion,nodeVersion,pnpmVersion}` + platform/arch + sharedPackages + 逐文件 sha256；**启动只校验描述符与 sharedPackages，全量哈希留给打包期** | OTA 版本策略 |
| 8 | 更新编排里**可迁移的是流程而不是库**：确认 → 锁准入 → 排空已准入请求 → 复检任务 → 优雅停 Host → 换包 → 重启 → 重新认证并**重载页面** | 自建 OTA（`electron-updater` 本身在 Tauri 下不成立） |

## 二、关键证据（文件:行）

**进程与协议**

- 起后端不是「自带 Node 二进制」，而是**用 GUI 可执行文件开 Node 模式**跑私有 host 包：
  `host-process.ts:188-201`（`spawn(this.node, ['--expose-internals', entry, runtimeDir, projectDir, …], {stdio:['ignore','pipe','pipe','ipc']})`）、
  `node-environment.ts:12-17`（`ELECTRON_RUN_AS_NODE=1`）。
- `--expose-internals` 的理由是 **Electron 44 特有**：`2026-09-11-desktop-electron-node-runtime.md:19`（Cordis loader 用 Node 内部 ESM loader）。
  → **自带标准 Node 的形态不需要这个 flag。**
- IPC 事件是判别联合 + 运行时校验：`host-process.ts:8-40`、`:53-91`（`platform-session` 逐字段白名单，拒绝 `authorization`/`host`/`content-length`、
  要求 header 名全小写、拒绝 CRLF、origin 必须 https 或 localhost http）。**姿态是「子进程不可信」，值得抄。**
- 端口被当成**会变**：`main.ts:545-547`（*"A replacement Host can have a new port, cookie, or boot injections even at the same URL."*）；
  端口占用的专门处理 `fatal-recovery.ts:73`（`/\blisten EADDRINUSE\b/u`）。
- 崩溃：子进程退出即失败（`host-process.ts:226-233`），stderr 只留**尾部 64KiB**（`:51`,`:204`）；
  **不做进程内自动重试**，进原生恢复对话框且**每进程只弹一次**（`fatal-recovery.ts:49`,`:65-104`）；
  第一方明确**不用超时判死**（`2026-09-15-desktop-native-fatal-recovery.md:13`）。
- 启动顺序：**先开窗放 loading 页、再起 Host**（`2026-09-09-desktop-immediate-window-and-direct-start.md:15`）；
  被否决的备选是「先健康检查再开窗」，理由是把插件初始化跑两遍。

**profile 与数据布局**

- `paths.ts:17-21`（独占 `profiles/desktop`）；`README.md:78`（*"CLI and Desktop share supported product data under `$DSH_HOME`,
  but never executable packages, plugin activation, lockfiles, or `node_modules`."*）。
- PID 锁可直接移植：`project-manager.ts:93-130`（`openSync(lock,'wx',0o600)` → EEXIST 时读 PID → `process.kill(pid,0)`，仅 `ESRCH` 算死）。
- 首启零安装：`project-manager.ts:83-91`、`:174-176`；`README.md:122`（*"startup never runs pnpm"*）。
- pnpm 路径**当 argv 传给 host**，不污染 agent shell 的 PATH：`host-process.ts:196`、`README.md:69`。
- `allowBuilds` 白名单决定依赖生命周期脚本能否执行（`project-manager.ts:38-49`）。

**前端复用（最该抄的一段）**

- 特权 scheme：`ipc.ts:90` + `main.ts:132-142`（`standard/secure/supportFetchAPI/corsEnabled/stream/codeCache`）。
- 三段式路由：`main.ts:662-677`（`shell` → 壳自身 renderer；`app` → 静态白名单命中就读
  `node_modules/@deepseek-ai/dsh-web-frontend/dist`，否则带 cookie 转发；其它 404）。
- 静态服务两个细节：路径穿越防护（`web-document.ts:18-36`，要求 `target.startsWith(directory+sep)` 否则 403）；
  往 `index.html` 的 `<head>` 注入 `globalThis.__DSH_BOOT_READY__ = Promise.withResolvers()`，
  让前端**不等 Host** 就能先画 loading（`:31-32`）。
- boot 数据**不走 URL**，走 preload IPC：`main.ts:685-690`（返回 `{injections, streamBaseUrl}`）、`preload-app.ts:85-88`。
  `streamBaseUrl` 与页面 origin 不同源 —— **这是壳内嵌 Web UI 最容易踩的坑。**
- 转发 header 纪律：`web-document.ts:58-92`（withheld 含 `set-cookie`/`content-encoding`/`content-length`/连接级头；
  `origin` 非白名单直接 403；删 `host`/`origin`/`cookie`/`sec-fetch-site` 后注入壳持有的 cookie；插件 bundle 强制 `no-store`）。
- WS 不走 `protocol.handle`，必须单独处理：`main.ts:710-721`（`onBeforeSendHeaders`，校验 `webContentsId` 与 `origin`，否则 `cancel`）。
- 安全设置：`main.ts:229-237`（`nodeIntegration:false`、`contextIsolation:true`、`sandbox:true`）；
  `assertDesktopSender`（`ipc.ts:97-104`）；`will-navigate` 白名单 + 外开（`main.ts:303-311`）；
  8 个分权 preload。
- 窗口/单实例/深链接：`single-instance.ts:16-25`（且**在碰 profile 之前**拿锁，`main.ts:1336`）；
  macOS `hiddenInset`+`vibrancy`（`main.ts:213-228`）、最小化时临时关 vibrancy（`:254-275`）；
  **关窗=隐藏不退出**（`:1072-1081`）；深链接只用于 focus、**不传凭据**（`:1228-1232`）。

**⭐ 在 0.1.7-rc.1 上实测过的一条**：`<repo>/.ming-tea/runtime/node_modules/@deepseek-ai/dsh-web-frontend/dist`
**已存在**，且文件集正好是官方路由白名单那几个（`index.html`、`assets/`、`favicon.svg`、`favicon-dark.svg`、`manifest.webmanifest`）。
→ **嵌官方 Web 前端不需要先升到 0.2.0-rc.2。**

**更新机制**

- 频道钉死 nightly、允许 prerelease、**禁降级**：`update-coordinator.ts:67-72`（`allowDowngrade = false`）。
- 元数据文件名 `nightly.yml`/`nightly-mac.yml` **不含版本号** → 「替换 feed 不能把已装的高版本降级」（`README.md:208`）。
- 下载与安装是**两次独立确认**，且确认载荷**只是版本字符串**（`update-coordinator.ts:129-145`；`ipc.ts:71`：
  渲染进程不能提供版本、包 URL 或安装授权）。
- `beforeRestart` 编排（最值得抄）：`main.ts:584-638` —— `idle` → 等 startup → `updateTasks('inspect')` →
  有任务则警告确认 → `updateTasks('lock')` **锁住新请求准入**并排空已准入 → 复检（任务从有变无 ⇒ 抛 `tasks-changed`）→
  关掉持有凭据的内嵌视图 → `requireCleanStop` 优雅停 → **停不干净就拒绝安装**（`stop-failed`）→
  失败路径解锁并**恢复当前版本 Host + 重载页面**（`:536-555`）。
- 轮询退避：基线 10min ±20% jitter，失败翻倍至 1h 封顶（`update-schedule.ts:20-26`,`:93-104`）。
- 更新流水账**只落盘白名单字段**（`update-journal.ts:20-30`，错误码分类含 `ERR_UPDATER_INVALID_SIGNATURE`、
  `ERR_UPDATER_CHECKSUM_MISMATCH`），**不写原始错误文本/URL**。
- 版本绑定：`release.ts:7-14`（注释原文 *"Exact version used by both Electron and `@deepseek-ai/dsh`"*），
  强制点 `runtime-tree.ts:172-176` + `electron-builder-config.mjs` 的 `extraMetadata.version` + `release.ts:16`。

**其它可落地细节**

- 首启：只要有登录**或** API key 就不进欢迎页（`welcome-api.ts:73-75`）；欢迎窗是独立窗口 + 独立 preload；
  API key 框用 `autocomplete="new-password"`；「稍后设置」**不写完成标记**。
- 审批/权限：官方**故意把插件管理交给共享 Web 前端**，壳里没有独立插件管理 UI（`2026-09-10-desktop-web-wrapper.md:23`）；
  原生只保留目录选择器与麦克风；内嵌浏览器权限**全否**（`browser-guests.ts:141-154`）。
  → 铭荼的审批 UI 应做在 Web 前端里，原生只留真正需要 OS 授权的。
- 错误上报：崩溃报告**先写盘再弹框**（最多等 1s），保留 10 份；原生对话框 detail 上限 1200 码元 + 最后 8 行；
  渲染进程只收 **error 级** console 尾部；shutdown 期间只落盘不弹框。
- **macOS GUI 环境变量坑（铭荼必然会踩）**：Dock/Finder 启动只继承 launchd 环境，`~/.zshrc` 的 PATH 全丢。
  官方在**第一次起 Host 前**跑一次登录 shell（`<shell> -ilc` + `env -0`，超时 10s 杀进程组，
  依次回退 `/bin/zsh`→`/bin/bash`→`/bin/sh`），Windows 跳过（`README.md:139`，实现 `login-shell-environment.ts`）。

## 三、不建议照搬（Tauri / 自建 OTA 下不成立）

| 官方做法 | 为什么 Tauri 下不成立 |
| --- | --- |
| `ELECTRON_RUN_AS_NODE=1` + `electronFuses.runAsNode` 拿 GUI 二进制当 Node | Tauri 是 Rust + WebView，**没有 Node**。必须自带（sidecar 打 node），否则依赖系统 node（**铭荼现状，最脆弱**）。注意 `--expose-internals` 不必抄。 |
| `electron-updater` + generic provider + `nightly.yml` + blockmap 差分 | Tauri 用 `tauri-plugin-updater` + 自己的 JSON 清单（`version`/`pub_date`/`platforms[].{url,signature}`），不认 electron-builder 的 YAML，也不支持 blockmap。 |
| `autoUpdater.quitAndInstall(true,true)` | Tauri 无「静默安装 + 自动重启」等价 API。可抄的是**职责划分**（安装交给安装器、应用只负责排空后退出），调用点必须换。 |
| `app-update.yml` 存在性判断 | Tauri 用 `tauri.conf.json` 的 `plugins.updater.endpoints` + pubkey。 |
| 签名/公证工具链（`@electron/osx-sign` patch、DMG 单独 staple、SafeNet `/kc`） | 全是 Electron/macOS bundle 结构特有。**能迁移的是要求本身**：macOS 必须 Developer ID + hardened runtime + 公证 + staple；Windows 必须带时间戳的代码签名。 |
| `asar` / `asarUnpack` / `extraResources` 清单 | Tauri 用 `bundle.resources`（铭荼已在用）。不要照搬 `asarUnpack` 的通配列表。 |
| 自定义特权 scheme（`registerSchemesAsPrivileged`） | Tauri **没有**这个 API（v2 用 `register_asynchronous_uri_scheme_protocol`，也没有那组开关）。**必须重写**，且 `Origin`/`sec-fetch-site` 实际取值会变 —— 官方那句 WS origin 校验要在 Tauri 下**重新实测**。 |
| 「Desktop 版本号 == dsh 版本号」字面绑定 | 官方能做是因为 **build 时物化整个依赖图**；铭荼是运行时装 dsh。硬抄会让每次 dsh 小版本升级都要发壳。**迁移语义而非字面**：把 dsh 版本写进壳的 release 描述符并在启动时校验，发布纪律上「壳 + runtime 一起发」，但允许版本号不同。 |
| `dsh-desktop-host` 私有包 | 官方能这么做是因为它随 Electron 资源一起签名分发且不发 npm。铭荼要等价物得自己写一个包在 `dsh --profile ming-tea` 外面负责 IPC 握手 + URL 上报。**别退回 stdout 解析。** |

## 四、我们的现状差距（读过的本地文件）

读过（只读）：`apps/desktop/src-tauri/src/{lib.rs,main.rs}`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml`、
`src-tauri/resources/ming-tea-agent.mjs`、`apps/desktop/src/main.ts`、`packages/agent/src/{dsh-adapter,server,plugin-registry,ota-bridge}.ts`、
`packages/protocol/src/index.ts`、`scripts/install_ming_tea_plugins.sh`、`assets/ming-tea-dsh-lock.json`、本目录 `docs/`。

**起后端（差距最大）**

| 维度 | 官方 | 铭荼 |
| --- | --- | --- |
| 运行时 | RunAsNode（自带） | `Command::new("node")` ⇒ **依赖系统 PATH 上的 node**（macOS Dock 启动必失败） |
| 启动失败 | 有 `fatal` 事件 + 原生恢复框 | `if let Ok(child)` —— **静默吞掉**，前端只看到 `unavailable` |
| 就绪握手 | `ready` 事件带 `url` + `injections` | **无**，spawn 完即返回 |
| stderr | 尾部 64KiB 进崩溃报告 | 管道**没人读**（可能写满阻塞） |
| 优雅关停 | 分级 + 等待 + 确认 | `lib.rs:45` 一句 `child.kill()`（SIGKILL 语义，不等不查） |
| 竞态 | 单 attempt + cancel 语义 | **无**（只存 `Option<Child>`） |
| 控制死线 | requestId + 2s/10s deadline | 协议有 `id` 但**无 deadline**（`packages/protocol/src/index.ts:26-37`） |

`dsh-adapter.ts:6-10` 只拼 `[dshBin, "--profile", env.MING_TEA_DSH_PROFILE ?? "ming-tea"]`；
`:16-25` **仅靠 `'spawn'` 事件判定 available**；`server.ts:33` 的 `status` action **惰性**触发 `dsh.start()`
（「问状态顺便启动」，没有独立启动阶段）。

**profile / 数据布局**

- `.ming-tea/runtime` 用 **npm** 装（`assets/ming-tea-dsh-lock.json` 的 `install.package_manager` 就是 `"npm"`），
  安装脚本另外把 `pnpm@11.19.0` 装进 runtime 供 `dsh plugin` 使用 —— **这个痛点在官方那里是用「pnpm 路径当 argv」解决的**。
- **缺 PID 锁**（官方那 38 行可移植）、**缺首启零安装**。
- **缺 runtime 描述符**：版本约束在 lock 文件里，但**运行时完全不校验**（`dsh-adapter.ts` 不读 lock、不比对 `dsh --version`）。

**前端复用**

- 现在是**铭荼自绘 UI**（`apps/desktop/src/main.ts` 79 行），而且**与后端完全没接线**：
  没有任何 `invoke` 调用，`sendTask()` 只改本地 state 就 `render()`；`lib.rs` 只暴露 `agent_endpoint`/`agent_runtime_status`。
- CSP（`tauri.conf.json:15`）只放行 `'self' ipc: http://ipc.localhost` ⇒ 要嵌官方 Web 前端并转发到本地 Host，
  **必须加 `http://127.0.0.1:*` 与 `ws://127.0.0.1:*`**。
- **无单实例锁、无深链接、无托盘**。
- ⚠️ **端口冲突可预判**：官方 README 说 Desktop 默认 `19387`，而本机 DSH Web GUI 正跑在 `127.0.0.1:19387`。
  铭荼嵌前端时要么分配独立端口，要么把 `EADDRINUSE` 当一等故障处理。
  （注：该端口归属**未在本仓库 runtime 里确认** —— 在 0.1.7-rc.1 的 runtime 里 grep `19387` 零命中，别当既定事实。）

**更新机制**

- `ota-bridge.ts:19`：非 Linux 直接返回 `error: "Ming OS OTA bridge is only available on Linux"` ⇒ 桌面壳 OTA 目前只有 Linux 路径。
- **Tauri updater 根本没接**：源码里 grep `updater` 零命中，`tauri.conf.json` 无 `plugins` 键，
  `apps/desktop/package.json` 无 `@tauri-apps/plugin-updater`。
  ⇒ 本目录 `docs/ming-tea-live-status.md` 里「endpoint 直接填 `…/api/ming-tea/update`」那几句描述的是**计划而非现状**。

## 五、如果只做三件事

1. **把「抓 stdout」改成 IPC 握手**：壳 spawn 后建立 IPC 信道，agent 侧上报 `{type:'ready',url,injections}`；
   URL 再用一次 `fetch(…,{redirect:'manual'})` 换 cookie。官方把 stdout 解析这条路废弃过。
2. **补进程卫生**：自带 Node（sidecar）替代 `Command::new("node")`；读 stderr 留尾；优雅关停分级 + 等待；
   单实例锁（在碰 profile 之前）；PID 锁文件。
3. **嵌前端照抄三段式路由**：`dsh-web-frontend/dist` 在**当前锁定的 0.1.7-rc.1 里就有**，不必先升级；
   同时给 CSP 放行 `http://127.0.0.1:*`/`ws://127.0.0.1:*`，并给壳分配一个不与现有 dsh web 相撞的端口。

## 六、没验证到的（如实列出）

1. **版本漂移未验证**：读的是 `0.2.0-rc.2`（`639ed015`），仓库无 tag，无法 diff 出 0.1.7-rc.1 时期的壳代码。
2. **没有构建或运行过官方 desktop**：本文是源码 + 第一方笔记阅读，**不含运行观测**。
3. **`dsh --profile ming-tea` 在 web 模式下往 stdout 打什么未实测**（且 `dsh-adapter.ts` 的 stdio 虽是 `pipe` 但没人读，
   现有代码里没有可参考样本）—— 这是嵌前端前必须先实测的一件事。
4. **端口 19387 的归属未确认**（见上）。
5. **`electron-updater` 的 feed YAML 字段未逐字段对照**（只读了官方自己的发布脚本与笔记）；
   「Tauri 要换成 JSON 清单」是基于两边格式差异的推断。
6. **官方强制更新（mandatory update）未读完**（`mandatory-update-window.ts` 312 行等）。若要做强制更新需单独一轮。
7. **`login-shell-environment.ts`（185 行）只读了 README 描述**，移植到 Rust 前需读实现。
8. 官方另有 5 篇 desktop 笔记未逐篇读（welcome-window-material、build-release-validation、cos-upload-transport、
   page-close-shortcuts、close-to-background-and-quit-confirmation）。
9. 本仓库 `docs/ming-tea-plugin-audit.md` 未完整阅读，本文未引用其结论。
