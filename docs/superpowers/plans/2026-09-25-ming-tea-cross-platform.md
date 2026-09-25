# 铭荼跨平台桌面助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先交付 Windows 和 macOS 可运行的铭荼桌面助手共享运行时，再连接现有 Ming OS GTK4 外壳，并通过官方/社区 DSH 插件提供终端、浏览器、电脑控制和办公能力。

**Architecture:** 在 `platform/ming-tea` 建立独立 pnpm workspace。Node/TypeScript Agent service 负责场景、权限、审批、审计、模型提供方和 DSH 适配；Tauri 2 桌面壳只负责窗口和平台桥接；Ming OS Python/GTK 外壳通过同一 JSON-lines IPC 协议连接。官方 computer-use 和其他 DSH 插件通过固定版本清单加载，铭荼只增加目标白名单、动作预算、截图节流和审批包装。

**Tech Stack:** Node.js 22+, TypeScript, pnpm, Vitest, Tauri 2/Rust, JSON-lines IPC, Windows Named Pipe, Unix domain socket, DSH 0.1.6-alpha.2, Playwright/LibreOffice UNO adapters.

**Spec:** `docs/superpowers/specs/2026-09-25-ming-tea-cross-platform-design.md`

## Global Constraints

- 应用必须是独立桌面程序，不能依赖用户打开浏览器访问网页。
- DSH 只通过适配层运行，界面不能直接依赖 DSH 内部模块。
- 办公、开发、辅助学习共享一套 Agent。
- 安装、sudo、上传、提交、删除和系统配置修改必须经过审批。
- 模型支持 `ming-main`、`openai-compatible` 和 `kim`，API key 只保存为安全存储引用。
- DSH 和插件固定版本、许可证和完整性；默认安装脚本禁用。
- 官方 computer-use 由铭荼包装，不重写引擎。
- Windows/macOS x86_64 首发；不要求本地模型。

### Task 1: Create The Cross-Platform Workspace

**Files:**
- Create: `platform/ming-tea/package.json`
- Create: `platform/ming-tea/pnpm-workspace.yaml`
- Create: `platform/ming-tea/tsconfig.json`
- Create: `platform/ming-tea/packages/protocol/package.json`
- Create: `platform/ming-tea/packages/agent/package.json`
- Test: `platform/ming-tea/tests/workspace-contract.test.ts`

**Interfaces:**
- Produces package names `@ming-tea/protocol` and `@ming-tea/agent`.
- The workspace must use Node `>=22.19.0` and pin `pnpm` to `11.19.0`.

- [ ] **Step 1: Write the failing workspace contract test**

```ts
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('workspace contract', () => {
  it('pins the supported Node and pnpm versions', () => {
    const root = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8'));
    expect(root.engines.node).toBe('>=22.19.0');
    expect(root.packageManager).toBe('pnpm@11.19.0');
    expect(root.workspaces).toEqual(['packages/*', 'apps/*']);
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd platform/ming-tea && pnpm exec vitest run tests/workspace-contract.test.ts`

Expected: FAIL because the workspace files do not exist.

- [ ] **Step 3: Create the workspace manifests**

Set `package.json` to private workspace metadata with `packageManager: "pnpm@11.19.0"`, `engines.node: ">=22.19.0"`, scripts `test`, `typecheck`, and `build`, and workspaces `packages/*` and `apps/*`. Set both package manifests to private packages using the names above and `type: "module"`.

- [ ] **Step 4: Run the contract test and type check**

Run: `cd platform/ming-tea && pnpm install --lockfile-only && pnpm exec vitest run tests/workspace-contract.test.ts && pnpm exec tsc --noEmit`

Expected: PASS with zero TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea
git commit -m "feat: scaffold cross-platform ming tea workspace"
```

### Task 2: Define The IPC Protocol And Transport

**Files:**
- Create: `platform/ming-tea/packages/protocol/src/index.ts`
- Create: `platform/ming-tea/packages/protocol/src/json-lines.ts`
- Create: `platform/ming-tea/packages/protocol/src/transport.ts`
- Create: `platform/ming-tea/packages/protocol/tests/protocol.test.ts`
- Modify: `platform/ming-tea/packages/protocol/package.json`

**Interfaces:**
- `SceneId = 'office' | 'development' | 'learning'`.
- `Request` is `{ id: string; action: 'ping' | 'status' | 'session.create' | 'session.cancel' | 'tool.request' | 'approval.consume'; payload: Record<string, unknown> }`.
- `MingTeaEvent` contains `type`, `sessionId`, `timestamp`, and redacted payload fields.
- `encodeJsonLine(value: unknown): string` always appends exactly one newline.
- `decodeJsonLine(line: string): unknown` rejects blank, malformed, and non-object input.
- `createUnixTransport(path)` and `createNamedPipeTransport(name)` expose `listen(handler)` and `close()`.

- [ ] **Step 1: Write failing protocol tests**

```ts
it('round trips a session request as one JSON line', () => {
  const request = { id: '1', action: 'session.create', payload: { scene: 'office' } };
  expect(decodeJsonLine(encodeJsonLine(request))).toEqual(request);
});

it('rejects malformed and non-object messages', () => {
  expect(() => decodeJsonLine('{')).toThrow();
  expect(() => decodeJsonLine('[]')).toThrow();
});
```

- [ ] **Step 2: Run the protocol tests and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/protocol/tests/protocol.test.ts`

Expected: FAIL because the codec functions are not implemented.

- [ ] **Step 3: Implement codec, request/event types, and platform transport factories**

Use `JSON.parse`/`JSON.stringify` with an object guard. Use `node:net` for Unix sockets and `node:net` with a `\\.\\pipe\\` path for Windows named pipes. Keep the handler signature `handler(request: Request): Promise<Response>` and send one encoded response per request.

- [ ] **Step 4: Run protocol and type tests**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/protocol/tests/protocol.test.ts && pnpm exec tsc --noEmit`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/packages/protocol
git commit -m "feat: add ming tea cross-platform IPC protocol"
```

### Task 3: Implement Shared Scenes, Permissions, Approvals, And Audit

**Files:**
- Create: `platform/ming-tea/packages/agent/src/scenes.ts`
- Create: `platform/ming-tea/packages/agent/src/redaction.ts`
- Create: `platform/ming-tea/packages/agent/src/policy.ts`
- Create: `platform/ming-tea/packages/agent/src/session-engine.ts`
- Create: `platform/ming-tea/packages/agent/tests/session-engine.test.ts`

**Interfaces:**
- `SCENES` exposes the three scene prompts, tool order, and risk profile.
- `PermissionPolicy.decide(toolId, arguments): 'allow' | 'confirm' | 'deny'`.
- `PermissionPolicy.issueApproval(toolId, arguments): string` and one-use `consumeApproval(token, toolId): boolean`.
- `SessionEngine.createSession(scene): Session`.
- `SessionEngine.requestTool(sessionId, toolId, arguments): MingTeaEvent[]`.
- `SessionEngine.cancelSession(sessionId): boolean`.
- `AuditWriter.append(event): Promise<void>` writes JSONL with mode `0600` on Unix.

- [ ] **Step 1: Write failing tests for scene routing and approval gates**

```ts
it('routes all three scenes to shared tools', () => {
  expect(Object.keys(SCENES).sort()).toEqual(['development', 'learning', 'office']);
});

it('requires approval for sudo and destructive operations', () => {
  const policy = new PermissionPolicy();
  expect(policy.decide('terminal.run', { command: 'sudo apt install demo' })).toBe('confirm');
  expect(policy.decide('file.delete', { path: '/tmp/demo' })).toBe('confirm');
  expect(policy.decide('file.read', { path: '/tmp/demo' })).toBe('allow');
});
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/session-engine.test.ts`

Expected: FAIL because the shared engine does not exist.

- [ ] **Step 3: Implement the minimal scene, policy, redaction, and session engine**

Copy only the stable behavior from `assets/ming-tea-core.py`. Preserve event names from the spec. Clear all approval tokens on cancellation. Redact keys named `api_key`, `token`, `password`, `authorization` and strings matching `sk-*` or Bearer tokens before returning events.

- [ ] **Step 4: Run focused and regression tests**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/session-engine.test.ts && cd ../.. && PYTHONPYCACHEPREFIX=/tmp/ming-tea-pycache python3 -m unittest -q tests.test_ming_tea_core tests.test_ming_tea_integration`

Expected: all new and existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/packages/agent
git commit -m "feat: add shared ming tea session and permission engine"
```

### Task 4: Add Model Providers And Secure Credential References

**Files:**
- Create: `platform/ming-tea/packages/agent/src/model-provider.ts`
- Create: `platform/ming-tea/packages/agent/src/credentials.ts`
- Create: `platform/ming-tea/packages/agent/tests/model-provider.test.ts`

**Interfaces:**
- `ModelProvider` has `id`, `label`, `endpoint`, `model`, `credentialRef`, and `capabilities`.
- Built-ins are `ming-main`, `openai-compatible`, and `kim`.
- `CredentialStore.get(ref): Promise<string | undefined>` and `set(ref, secret): Promise<void>`.
- `ModelProviderRegistry.resolve(id): ModelProvider` never returns the secret value to UI events.

- [ ] **Step 1: Write failing tests for provider normalization and key redaction**

```ts
it('normalizes Ming, OpenAI-compatible, and Kim providers', () => {
  const registry = createDefaultProviderRegistry();
  expect(registry.resolve('ming-main').id).toBe('ming-main');
  expect(registry.resolve('openai-compatible').id).toBe('openai-compatible');
  expect(registry.resolve('kim').id).toBe('kim');
});

it('keeps credentials behind references', async () => {
  const provider = createDefaultProviderRegistry().resolve('kim');
  expect(provider).not.toHaveProperty('apiKey');
  expect(provider.credentialRef).toMatch(/^credential:/);
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/model-provider.test.ts`

Expected: FAIL because provider and credential modules do not exist.

- [ ] **Step 3: Implement provider registry and platform credential store interface**

Use environment variables only for development (`MING_TEA_API_KEY`, `KIM_API_KEY`); production stores implement Windows Credential Manager and macOS Keychain behind the same interface. Do not serialize secret values into session state, audit events, or IPC responses.

- [ ] **Step 4: Run tests and inspect serialized events**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/model-provider.test.ts packages/agent/tests/session-engine.test.ts`

Expected: PASS and no provider secret appears in JSON snapshots.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/packages/agent
git commit -m "feat: add model provider registry and credential references"
```

### Task 5: Adapt DSH Plugins And Optimize Official Computer Use

**Files:**
- Create: `platform/ming-tea/packages/agent/src/dsh-adapter.ts`
- Create: `platform/ming-tea/packages/agent/src/plugin-registry.ts`
- Create: `platform/ming-tea/packages/agent/src/computer-use-guard.ts`
- Create: `platform/ming-tea/packages/agent/tests/dsh-adapter.test.ts`
- Modify: `assets/ming-tea-dsh-lock.json`

**Interfaces:**
- `DshAdapter.start(): Promise<RuntimeStatus>` and `stop(): Promise<void>`.
- `PluginRegistry.load(lockPath): AuditedPlugin[]` rejects missing license, version, integrity, platform, or permissions.
- `ComputerUseGuard.check(action): 'allow' | 'confirm' | 'deny'` enforces target allowlists, action budgets, screenshot rate limits, and destructive-action approval.

- [ ] **Step 1: Write failing tests for pinned plugin loading and computer-use guards**

```ts
it('loads official computer-use only from the audited lock', () => {
  const registry = new PluginRegistry();
  const plugins = registry.load('assets/ming-tea-dsh-lock.json');
  expect(plugins.some((plugin) => plugin.package === '@deepseek-ai/dsh-computer-use')).toBe(true);
});

it('requires approval for upload and unlisted windows', () => {
  const guard = new ComputerUseGuard({ allowedWindows: ['Firefox'], maxActions: 20 });
  expect(guard.check({ kind: 'click', window: 'Firefox' })).toBe('allow');
  expect(guard.check({ kind: 'upload', window: 'Firefox' })).toBe('confirm');
  expect(guard.check({ kind: 'click', window: 'Password Manager' })).toBe('deny');
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/dsh-adapter.test.ts`

Expected: FAIL because the adapter and guard do not exist.

- [ ] **Step 3: Implement adapter, plugin audit loader, and computer-use wrapper**

Start the pinned DSH CLI as a child process. Use official `@deepseek-ai/dsh-computer-use` and browser bundles from the lock file. The guard must count actions per session, enforce a minimum screenshot interval, require confirmation for upload/submit/delete/system actions, and reject windows outside the allowlist. Never bypass DSH approval events.

- [ ] **Step 4: Run focused tests and package metadata validation**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/dsh-adapter.test.ts && python3 -m json.tool ../../assets/ming-tea-dsh-lock.json >/dev/null`

Expected: PASS and valid audited JSON.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/packages/agent assets/ming-tea-dsh-lock.json
git commit -m "feat: adapt audited dsh plugins and computer use"
```

### Task 6: Build The Local Agent Service

**Files:**
- Create: `platform/ming-tea/packages/agent/src/server.ts`
- Create: `platform/ming-tea/packages/agent/src/cli.ts`
- Create: `platform/ming-tea/packages/agent/tests/server.test.ts`
- Modify: `platform/ming-tea/packages/agent/package.json`

**Interfaces:**
- `createAgentServer(options): { listen(): Promise<void>; close(): Promise<void> }`.
- Server actions map to `SessionEngine`, provider registry, and DSH adapter without exposing secrets.
- `MING_TEA_IPC_PATH` selects Unix socket; `MING_TEA_PIPE_NAME` selects Windows pipe.

- [ ] **Step 1: Write failing IPC integration tests**

```ts
it('answers ping and creates an office session over JSON-lines IPC', async () => {
  const server = await startTestServer();
  expect(await request(server, { action: 'ping', payload: {} })).toMatchObject({ ok: true });
  expect(await request(server, { action: 'session.create', payload: { scene: 'office' } })).toMatchObject({ ok: true });
  await server.close();
});
```

- [ ] **Step 2: Run the integration test and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run packages/agent/tests/server.test.ts`

Expected: FAIL because the server is not implemented.

- [ ] **Step 3: Implement server dispatch and CLI lifecycle**

Map `ping`, `status`, `session.create`, `session.cancel`, `tool.request`, and `approval.consume` to the shared engine. Return structured errors for malformed requests, unknown actions, missing sessions, and unavailable DSH. Handle SIGINT/SIGTERM by closing the transport and DSH child process.

- [ ] **Step 4: Run all Node tests**

Run: `cd platform/ming-tea && pnpm test`

Expected: all protocol and agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/packages/agent
git commit -m "feat: add local ming tea agent service"
```

### Task 7: Add Windows And macOS Desktop Shells

**Files:**
- Create: `platform/ming-tea/apps/desktop/package.json`
- Create: `platform/ming-tea/apps/desktop/src-tauri/Cargo.toml`
- Create: `platform/ming-tea/apps/desktop/src-tauri/src/main.rs`
- Create: `platform/ming-tea/apps/desktop/src-tauri/tauri.conf.json`
- Create: `platform/ming-tea/apps/desktop/src/App.tsx`
- Create: `platform/ming-tea/apps/desktop/src/theme.css`
- Create: `platform/ming-tea/apps/desktop/tests/app-shell.test.tsx`

**Interfaces:**
- The shell starts the local Agent service as a sidecar and connects through the platform transport.
- UI exposes scenes, connection state, transcript, plan, tool timeline, and approval requests.
- UI uses Ming Tea design tokens and does not embed DSH Web UI.

- [ ] **Step 1: Write failing shell contract tests**

```tsx
it('renders the three Ming Tea scenes and an offline state', () => {
  render(<App initialStatus={{ dshAvailable: false }} />);
  expect(screen.getByText('办公模式')).toBeInTheDocument();
  expect(screen.getByText('开发模式')).toBeInTheDocument();
  expect(screen.getByText('辅助学习模式')).toBeInTheDocument();
  expect(screen.getByText('DSH 运行时不可用')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the shell test and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run apps/desktop/tests/app-shell.test.tsx`

Expected: FAIL because the desktop shell does not exist.

- [ ] **Step 3: Implement the Tauri sidecar, UI, and design tokens**

Use a compact three-column layout: scene rail, conversation/work area, and plan/approval panel. Use CSS variables for Ming Tea teal, ink, surface, warning, and danger states. Keep all external tool calls behind Agent events. Add tooltips to unfamiliar icon controls and use text only for clear commands.

- [ ] **Step 4: Run shell tests and build checks**

Run: `cd platform/ming-tea && pnpm exec vitest run apps/desktop/tests/app-shell.test.tsx && pnpm exec tsc --noEmit`

Expected: PASS. On a host with Rust/Tauri installed, also run `pnpm tauri build --debug` for the current platform.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea/apps/desktop
git commit -m "feat: add standalone ming tea desktop shell"
```

### Task 8: Connect Ming OS And Add Packaging Gates

**Files:**
- Create: `platform/ming-tea/packages/agent/src/ming-os-bridge.ts`
- Modify: `assets/ming-tea-runtime.py`
- Modify: `assets/ming-tea.py`
- Modify: `modules/03_desktop.sh`
- Modify: `build_onion_os.sh`
- Create: `platform/ming-tea/tests/packaging-contract.test.ts`

**Interfaces:**
- Ming OS Python runtime can proxy `ping`, `status`, session, tool, and approval requests to the Node Agent when present.
- Existing Python fallback remains usable when the Node Agent is absent.
- Build gates require the audited DSH lock, desktop launcher, and protocol assets.

- [ ] **Step 1: Write failing packaging and bridge tests**

```ts
it('requires the audited DSH lock and desktop launcher', () => {
  expect(fs.existsSync('assets/ming-tea-dsh-lock.json')).toBe(true);
  expect(fs.existsSync('platform/ming-tea/apps/desktop')).toBe(true);
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd platform/ming-tea && pnpm exec vitest run tests/packaging-contract.test.ts`

Expected: FAIL until the bridge and packaging references are added.

- [ ] **Step 3: Implement bridge and Ming OS build integration**

Add a Python IPC proxy that prefers the Node Agent socket when `MING_TEA_AGENT_SOCKET` is set and falls back to the existing Python runtime. Install the Node runtime manifest and lock file into the Ming OS application directory. Do not copy unreviewed `node_modules` into Git or the ISO.

- [ ] **Step 4: Run all verification gates**

Run: `cd platform/ming-tea && pnpm test && pnpm exec tsc --noEmit`; then from the repository root run `PYTHONPYCACHEPREFIX=/tmp/ming-tea-pycache python3 -m unittest -q tests.test_ming_tea_core tests.test_ming_tea_integration` and `bash -n modules/03_desktop.sh modules/07_finalize.sh build_onion_os.sh`.

Expected: all Node/Python tests and shell syntax checks pass.

- [ ] **Step 5: Commit**

```bash
git add platform/ming-tea assets modules build_onion_os.sh
git commit -m "feat: connect ming os to cross-platform ming tea agent"
```
