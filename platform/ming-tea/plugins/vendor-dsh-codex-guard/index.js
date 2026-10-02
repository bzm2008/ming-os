// dsh-codex-guard —— 上游 vendored 副本（铭荼适配版）。
//
// 来源：https://github.com/Akimiya-z/codex-guard ，路径 `dsh/`，commit
// 87e138cdcca2e2929073bb913212dc06769087fc（v1.9.0，MIT）。改动只此一处，见下。
//
// ⚠️ 铭荼适配（2026-10-02）：上游 `runGuard` 每次调用都走
// `npx --yes codex-guard`，也就是**运行时按需去 npm registry 拉一个包并执行**。
// 这与本产品的口径不符（依赖必须锁定、可审计、不引入运行时下载）。这里改成：
// CLI 作为本包自己的 dependency 固定为 `codex-guard@1.16.0`（MIT，
// integrity 见 assets/ming-tea-dsh-lock.json），直接以**当前 Node 执行它的 bin**，
// 全程不联网、不经 shell、不依赖 npx 的位置。找不到本地 CLI 时如实报错并提示重装，
// 而不是退回动态下载。
//
// 其余行为（工具名/描述/参数/输出契约、退出码 1 表示「有阻断项」属正常报告的语义）与上游一致。

import { createRequire } from 'node:module'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'

export const name = 'codex-guard'
export const inject = ['tools']

const requireFromHere = createRequire(import.meta.url)

/**
 * 解析本地钉住的 codex-guard CLI 入口；解析不到返回 null（调用方据此给出可操作错误）。
 *
 * 三级解析，按可靠性排序：
 *  1. `MING_TEA_CODEX_GUARD_CLI` —— 显式覆盖（排障/测试用）；
 *  2. `require.resolve('codex-guard/package.json')` —— 本包作为真实目录安装时成立；
 *     ⚠️ 但本产品用 `link:` 把本包链进 profile（pnpm 对 link/file 目标都只建软链、
 *     不装被链包自己的依赖），Node 又是按**真实路径**（仓库目录）向上解析，
 *     所以这一级在开发树里通常解析不到；
 *  3. `<DSH_HOME>/../node_modules/codex-guard` —— 运行时树。CLI 在
 *     `assets/ming-tea-dsh-lock.json` 里作为 **runtime 包**固定为 1.16.0（带 integrity），
 *     DSH 宿主进程由铭荼壳启动且一定带 `DSH_HOME`（`dsh_host.rs` 设置），所以这级最稳。
 */
export function resolveGuardCli() {
  const explicit = process.env.MING_TEA_CODEX_GUARD_CLI
  if (explicit && existsSync(explicit)) return explicit

  const fromBin = (manifestPath) => {
    try {
      const manifest = requireFromHere(manifestPath)
      const bin = typeof manifest.bin === 'string' ? manifest.bin : manifest?.bin?.['codex-guard']
      return bin ? join(dirname(manifestPath), bin) : null
    } catch {
      return null
    }
  }

  try {
    const resolved = fromBin(requireFromHere.resolve('codex-guard/package.json'))
    if (resolved && existsSync(resolved)) return resolved
  } catch {
    /* 落到运行时树那一级 */
  }

  const dshHome = process.env.DSH_HOME
  if (dshHome) {
    const manifestPath = join(dshHome, '..', 'node_modules', 'codex-guard', 'package.json')
    if (existsSync(manifestPath)) {
      const resolved = fromBin(manifestPath)
      if (resolved && existsSync(resolved)) return resolved
    }
  }
  return null
}

/**
 * Run the codex-guard pre-submit checks inside DeepSeek Harness.
 *
 * 检查本身由本地钉住的 CLI 执行（同一套确定性的 TODO/密钥/提交信息扫描），
 * 不发起网络请求。CLI 在**发现阻断项**时以退出码 1 结束 —— 那是正常报告，不是崩溃，
 * 因此这里对非零退出码不抛异常。
 */
function runGuard(ref, asJson) {
  const cli = resolveGuardCli()
  if (!cli) {
    return (
      'codex-guard failed: 找不到本地 CLI（codex-guard 依赖未安装）。' +
      '请重跑 scripts/install_ming_tea_plugins.sh（本产品不使用 npx 动态下载）。'
    )
  }
  const args = [cli, '--git']
  if (ref) args.push(ref)
  if (asJson) args.push('--json')
  const res = spawnSync(process.execPath, args, {
    cwd: process.cwd(),
    encoding: 'utf8',
  })
  if (res.error) return `codex-guard failed: ${res.error.message}`
  const out = (res.stdout || '').trim()
  if (out) return out
  return (res.stderr || '').trim() || `codex-guard exited with status ${res.status}`
}

export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'codex_guard',
      description:
        'Run pre-submit hygiene checks on the current repository: TODO/FIXME leftovers, hardcoded secrets, and non-conventional commit subjects. Exit code 0 means clean; a report is returned either way.',
      parameters: {
        ref: {
          type: 'string',
          description:
            'Git ref to diff against, e.g. origin/main. Omit to scan uncommitted changes.',
        },
        json: {
          type: 'boolean',
          description: 'Return the machine-readable JSON report.',
        },
      },
      output: {
        schema: { type: 'string' },
        render: (_args, value) => [{ type: 'text', text: value }],
      },
      async execute(args) {
        return runGuard(args.ref, args.json)
      },
    })
  )
}
