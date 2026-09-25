import { spawn, type ChildProcess } from "node:child_process";

export interface RuntimeStatus { available: boolean; version?: string; error?: string; }

export class DshAdapter {
  private process?: ChildProcess;
  constructor(private readonly command: string[] = ["dsh", "stdio", "--json"]) {}

  async start(): Promise<RuntimeStatus> {
    if (this.process && !this.process.killed) return {available: true};
    const child = spawn(this.command[0], this.command.slice(1), {stdio: ["pipe", "pipe", "pipe"]});
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
