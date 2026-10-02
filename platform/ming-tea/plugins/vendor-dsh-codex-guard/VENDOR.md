# vendored：dsh-codex-guard

这是 **上游副本**，不是本仓库的原创代码。放进来是为了满足产品口径：`codex-guard` 只发布在
GitHub（npm 上没有 `dsh-codex-guard` 这个包），而我们的依赖必须**锁定 + 可审计 + 不走 git/URL 安装**。

| 项 | 值 |
| --- | --- |
| 上游仓库 | <https://github.com/Akimiya-z/codex-guard> |
| 取用路径 | 仓库内 `dsh/` 子树（DSH 插件半区） |
| 固定 commit | `87e138cdcca2e2929073bb913212dc06769087fc` |
| 包名 / 版本 | `dsh-codex-guard` / `1.9.0` |
| 许可 | MIT（`LICENSE` 原样保留；上游仓库根目录的 MIT 文本） |
| 同步方式 | **不追上游**。要更新就重新 vendor 一个 commit，并在本条记录新 commit 与差异 |

## 我改了哪里（相对上游，只有两处）

1. **`index.js`：把 `npx --yes codex-guard` 换成「本地钉住的 CLI」。**
   上游每次调用工具都会让 npx 去 npm registry **按需下载并执行**一个包（首次必需联网）。
   本产品不允许运行时下载依赖，因此改成执行本地副本。CLI 在
   `assets/ming-tea-dsh-lock.json` 里作为 **runtime 包**固定为 `codex-guard@1.16.0`
   （MIT，`sha512-mWWBwV9vstdB+0UFGpq0BY0IQnEZOTdFDSxlM8b6ML/3G7Z13+Sbwlqis+AcW6zG73lpOW0qoCVyAdZOGnaFHw==`），
   解析顺序是：`MING_TEA_CODEX_GUARD_CLI` 环境变量 → `require.resolve('codex-guard/package.json')` →
   `<DSH_HOME>/../node_modules/codex-guard`（运行时树）。不联网、不经 shell、不依赖 npx 位置；
   解析不到时**如实报错并提示重装**，不退回动态下载。
2. **`package.json`：新增 `dependencies: { "codex-guard": "1.16.0" }`**（配合上面那处；
   顺便说明：本包是以 `link:` 链进 profile 的，pnpm 对 `link:`/`file:` 目标**都只建软链、
   不会装被链包自己的依赖**（实测），所以 CLI 才需要单独作为 runtime 包钉住）。

工具的对外契约未动：工具名仍是 `codex_guard`，参数/输出/描述一致，退出码 1（有阻断项）仍视为正常报告。

## 审计结论（2026-10-02）

- **注册面**：`inject: ['tools']`，只注册一个工具 `codex_guard`。**不 hook 审批、不拦截其它工具**，
  因此不会与铭荼自己的审批闸门（`installApprovalGate`）形成双重拦截。
- **权限面**：以 `process.cwd()` 为工作目录执行 git 相关的只读扫描（`--git` 对比 ref），
  不做文件写入、不发网络请求（改动后）；它本身不碰凭据。
- **风险等级**：中（会执行一个外部 CLI 进程；改动后该进程来自锁定的本地依赖）。
- **DSH 兼容性**：peer 声明 `@deepseek-ai/cordis` / `@deepseek-ai/dsh-tools`
  `>=0.1.0-rc.1 <0.2.0 || >=0.2.0`。我们运行时是 `0.2.0-rc.2`（预发布版），严格 semver 下
  `0.2.0-rc.2 < 0.2.0` —— 是否需要 `versionExemption` 以 `dsh plugin add` 的预检实测为准
  （见 `docs/ming-tea-plugin-audit.md` 的批次记录）。
