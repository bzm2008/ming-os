import { spawn, type ChildProcess } from "node:child_process";
import { join } from "node:path";

export interface RuntimeStatus { available: boolean; version?: string; error?: string; }

export function resolveDshCommand(env: NodeJS.ProcessEnv = process.env): string[] {
  const runtime = env.MING_TEA_DSH_RUNTIME_DIR;
  const executable = env.MING_TEA_DSH_BIN ?? (runtime ? join(runtime, "node_modules", ".bin", process.platform === "win32" ? "dsh.cmd" : "dsh") : "dsh");
  return [executable, "--profile", env.MING_TEA_DSH_PROFILE ?? "ming-tea"];
}

export class DshAdapter {
  private process?: ChildProcess;
  constructor(private readonly command: string[] = resolveDshCommand(), private readonly env: NodeJS.ProcessEnv = process.env) {}

  async start(): Promise<RuntimeStatus> {
    if (this.process && !this.process.killed) return {available: true};
    const child = spawn(this.command[0], this.command.slice(1), {stdio: ["pipe", "pipe", "pipe"], env: this.env});
    this.process = child;
    return await new Promise((resolve) => {
      const fail = (error: Error) => resolve({available: false, error: error.message});
      child.once("error", fail);
      child.once("spawn", () => resolve({available: true}));
    });
  }

  async stop(): Promise<void> {
    if (!this.process || this.process.killed) return;
    this.process.kill();
    await new Promise<void>((resolve) => this.process?.once("close", () => resolve()));
    this.process = undefined;
  }
}
