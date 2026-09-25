export type ComputerAction = {kind: "click" | "type" | "key" | "screenshot" | "upload" | "submit" | "delete" | "system"; window: string};
export type GuardDecision = "allow" | "confirm" | "deny";

export class ComputerUseGuard {
  private count = 0;
  private lastScreenshotAt = 0;
  constructor(private readonly options: {allowedWindows: string[]; maxActions: number; screenshotIntervalMs?: number}) {}

  check(action: ComputerAction): GuardDecision {
    if (!this.options.allowedWindows.includes(action.window)) return "deny";
    if (this.count >= this.options.maxActions) return "deny";
    if (action.kind === "screenshot") {
      const now = Date.now();
      if (now - this.lastScreenshotAt < (this.options.screenshotIntervalMs ?? 250)) return "deny";
      this.lastScreenshotAt = now;
    }
    this.count += 1;
    if (["upload", "submit", "delete", "system"].includes(action.kind)) return "confirm";
    return "allow";
  }
}
