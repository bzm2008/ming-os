import { randomUUID } from "node:crypto";
import type { MingTeaEvent, SceneId } from "@ming-tea/protocol";
import { PermissionPolicy } from "./policy.js";
import { redactSecrets } from "./redaction.js";
import { SCENES } from "./scenes.js";

export type ThinkingLevel = "fast" | "balanced" | "deep";
export interface Session { sessionId: string; scene: SceneId; thinking: ThinkingLevel; createdAt: number; state: "active" | "cancelled" | "completed"; }
export type AuditWriter = (event: MingTeaEvent) => void | Promise<void>;

export class SessionEngine {
  private readonly sessions = new Map<string, Session>();
  constructor(private readonly policy = new PermissionPolicy(), private readonly audit: AuditWriter = () => undefined) {}

  createSession(scene: SceneId): Session {
    if (!SCENES[scene]) throw new Error(`Unknown scene: ${scene}`);
    const session = {sessionId: randomUUID(), scene, thinking: "balanced" as ThinkingLevel, createdAt: Date.now(), state: "active" as const};
    this.sessions.set(session.sessionId, session);
    this.emit({type: "session.created", sessionId: session.sessionId, scene});
    return {...session};
  }

  setThinking(sessionId: string, thinking: ThinkingLevel): Session {
    const session = this.sessions.get(sessionId);
    if (!session || session.state !== "active") throw new Error("Session is missing, cancelled, or ended");
    if (!["fast", "balanced", "deep"].includes(thinking)) throw new Error("Unknown thinking level");
    session.thinking = thinking;
    this.emit({type: "session.thinking.changed", sessionId, thinking});
    return {...session};
  }

  requestTool(sessionId: string, toolId: string, args: Record<string, unknown> = {}): MingTeaEvent[] {
    const session = this.sessions.get(sessionId);
    if (!session || session.state !== "active") throw new Error("Session is missing, cancelled, or ended");
    const safeArgs = redactSecrets(args) as Record<string, unknown>;
    const events = [this.emit({type: "tool.requested", sessionId, toolId, arguments: safeArgs})];
    const decision = this.policy.decide(toolId, safeArgs);
    if (decision === "deny") events.push(this.emit({type: "tool.failed", sessionId, toolId, error: "Tool is not allowed"}));
    else if (decision === "confirm") events.push(this.emit({type: "approval.requested", sessionId, toolId, approvalToken: this.policy.issueApproval(toolId), arguments: safeArgs}));
    else events.push(this.emit({type: "tool.started", sessionId, toolId}));
    return events;
  }

  consumeApproval(sessionId: string, token: string, toolId: string): boolean {
    return this.sessions.get(sessionId)?.state === "active" && this.policy.consumeApproval(token, toolId);
  }

  cancelSession(sessionId: string): boolean {
    const session = this.sessions.get(sessionId);
    if (!session || session.state !== "active") return false;
    session.state = "cancelled";
    this.policy.clear();
    this.emit({type: "session.completed", sessionId, status: "cancelled"});
    return true;
  }

  status(): {sessions: number; activeSessions: number} {
    const values = [...this.sessions.values()];
    return {sessions: values.length, activeSessions: values.filter((item) => item.state === "active").length};
  }

  private emit(event: Omit<MingTeaEvent, "timestamp">): MingTeaEvent {
    const safe = redactSecrets({...event, timestamp: Date.now()}) as MingTeaEvent;
    void this.audit(safe);
    return safe;
  }
}
