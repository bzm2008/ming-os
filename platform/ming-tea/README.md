# 铭荼跨平台运行时

这是 Windows、macOS 和 Ming OS 共用的 Agent service 与桌面壳基础。

## 本地验证

需要 Node.js 22+ 和 pnpm 11：

```bash
pnpm install --no-frozen-lockfile
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc --noEmit
```

如果 pnpm 提示 `esbuild` 构建脚本待审批，只允许 workspace 工具链中的 `esbuild`，不要为 DSH 或社区插件开启任意安装脚本。

## 启动 Agent service

macOS/Linux 使用 Unix socket：

```bash
MING_TEA_IPC_PATH=/tmp/ming-tea-agent.sock node packages/agent/src/cli.ts
```

Windows 使用 Named Pipe：

```powershell
$env:MING_TEA_PIPE_NAME = 'ming-tea-agent'
node packages/agent/src/cli.ts
```

桌面壳位于 `apps/desktop`，Tauri 2 需要 Rust/Cargo：

```bash
pnpm --filter @ming-tea/desktop tauri:dev
pnpm --filter @ming-tea/desktop tauri:build
```

## 平台能力

Agent service 只负责协议、场景、权限、审批、审计和 DSH 适配。Windows UI Automation、macOS Accessibility/Automation、浏览器和 Office 能力都必须通过平台 adapter 声明后接入。

模型提供方使用 `credentialRef`，不会把 API key 放进会话、IPC 或日志。内置提供方包括 Ming 主站、OpenAI-compatible 和 Kim。
