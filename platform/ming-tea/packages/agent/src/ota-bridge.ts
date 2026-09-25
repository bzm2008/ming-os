import { spawn } from "node:child_process";

export type OtaAction = "status" | "check" | "prepare" | "reboot";
export interface OtaResult { action: OtaAction; ok: boolean; output: unknown; error?: string; }
export type CommandRunner = (command: string, args: string[]) => Promise<{code: number; stdout: string; stderr: string}>;

const defaultRunner: CommandRunner = (command, args) => new Promise((resolve) => {
  const child = spawn(command, args, {stdio: ["ignore", "pipe", "pipe"]});
  let stdout = ""; let stderr = "";
  child.stdout.on("data", (data) => { stdout += data; }); child.stderr.on("data", (data) => { stderr += data; });
  child.on("error", (error) => resolve({code: 127, stdout, stderr: error.message}));
  child.on("close", (code) => resolve({code: code ?? 1, stdout, stderr}));
});

export class OtaBridge {
  constructor(private readonly runner: CommandRunner = defaultRunner, private readonly command = "ming-update", private readonly platform: NodeJS.Platform = process.platform) {}

  async run(action: OtaAction): Promise<OtaResult> {
    if (this.platform !== "linux") return {action, ok: false, output: {platform: this.platform}, error: "Ming OS OTA bridge is only available on Linux"};
    const result = await this.runner(this.command, [action, "--json"]);
    let output: unknown = result.stdout.trim();
    try { output = output ? JSON.parse(String(output)) : {}; } catch { output = {raw: output}; }
    return result.code === 0 ? {action, ok: true, output} : {action, ok: false, output, error: result.stderr.trim() || `ming-update exited with ${result.code}`};
  }
}
