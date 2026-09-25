import { randomBytes } from "node:crypto";

export type Decision = "allow" | "confirm" | "deny";

export class PermissionPolicy {
  private readonly approvals = new Map<string, {toolId: string; issuedAt: number}>();
  private readonly autoTools = new Set(["file.read", "file.list", "system.status", "browser.read", "browser.open", "office.read", "diagnostic.run", "app.open"]);
  private readonly confirmTools = new Set(["file.delete", "file.upload", "browser.submit", "browser.upload", "office.write", "package.install", "system.modify", "account.modify"]);
  private readonly dangerousCommand = /(^|[;&|]\s*)(sudo|su|doas|pkexec|rm|mv|chmod|chown|dd|mkfs|shutdown|reboot|apt|apt-get|dpkg)\b/i;

  decide(toolId: string, args: Record<string, unknown> = {}): Decision {
    if (toolId === "terminal.run") return this.dangerousCommand.test(String(args.command ?? "")) ? "confirm" : "allow";
    if (this.autoTools.has(toolId)) return "allow";
    if (this.confirmTools.has(toolId)) return "confirm";
    return "deny";
  }

  issueApproval(toolId: string, _args: Record<string, unknown> = {}): string {
    const token = randomBytes(32).toString("base64url");
    this.approvals.set(token, {toolId, issuedAt: Date.now()});
    return token;
  }

  consumeApproval(token: string, toolId: string): boolean {
    const approval = this.approvals.get(token);
    if (!approval || approval.toolId !== toolId) return false;
    this.approvals.delete(token);
    return true;
  }

  clear(): void { this.approvals.clear(); }
}
