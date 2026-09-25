export interface DisciplineState { toolFailures: Record<string, number>; planReminder: boolean; }

export class ExecutionDiscipline {
  private readonly failures = new Map<string, number>();
  observe(toolId: string, outcome: "started" | "completed" | "failed"): DisciplineState {
    if (outcome === "failed") this.failures.set(toolId, (this.failures.get(toolId) ?? 0) + 1);
    if (outcome === "completed") this.failures.delete(toolId);
    const failures = Object.fromEntries(this.failures);
    return {toolFailures: failures, planReminder: Object.values(failures).some((count) => count >= 2)};
  }
  status(): DisciplineState { return {toolFailures: Object.fromEntries(this.failures), planReminder: [...this.failures.values()].some((count) => count >= 2)}; }
}
