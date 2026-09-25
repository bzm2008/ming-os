import type { SceneId } from "@ming-tea/protocol";
import { redactSecrets } from "./redaction.js";
import { LocalMemoryStore } from "./memory.js";
import { SessionLibrary } from "./session-library.js";
import { ExecutionDiscipline } from "./discipline.js";

export type MemoryScope = "profile" | "session" | "task";

export interface IdentityFact {
  scope: MemoryScope;
  key: string;
  value: unknown;
}

export interface IdentityContext {
  scope: MemoryScope;
  facts: Record<string, unknown>;
  source: "dsh-agent-identity";
}

const SENSITIVE_KEY = /(api[_-]?key|access[_-]?token|auth(?:orization)?|cookie|credential|password|secret|private[_-]?key)/i;

function sanitizeFact(key: string, value: unknown): unknown {
  if (SENSITIVE_KEY.test(key)) return "[已隐藏]";
  return redactSecrets(value);
}

/**
 * Local, scoped wrapper for dsh-agent-identity's profile files.
 * Persistent storage is delegated to LocalMemoryStore.
 */
export class IdentityMemoryAdapter {
  constructor(private readonly store = new LocalMemoryStore()) {}

  async remember(fact: IdentityFact): Promise<void> {
    const value = sanitizeFact(fact.key, fact.value);
    const kind = fact.scope === "profile" ? "profile" : "preference";
    await this.store.remember(
      JSON.stringify({key: fact.key, value}),
      kind,
      ["ming-scope:" + fact.scope, "identity:" + fact.key],
      "user",
    );
  }

  async context(scope: MemoryScope): Promise<IdentityContext> {
    const records = await this.store.recall("ming-scope:" + scope, 100);
    const facts: Record<string, unknown> = {};
    for (const record of records) {
      try {
        const parsed = JSON.parse(record.content) as {key?: string; value?: unknown};
        if (parsed.key) facts[parsed.key] = sanitizeFact(parsed.key, parsed.value);
      } catch {
        // Ignore legacy free-form memory records.
      }
    }
    return {scope, facts, source: "dsh-agent-identity"};
  }
}

export interface SessionRecord {
  sessionId: string;
  scene: SceneId;
  title: string;
  text: string;
  updatedAt?: number;
}

export interface SessionSearchResult extends SessionRecord {
  score: number;
}

/**
 * Read-only adapter for dsh-session-workbench backed by SessionLibrary.
 */
export class SessionWorkbenchAdapter {
  constructor(private readonly library = new SessionLibrary()) {}

  async index(record: SessionRecord): Promise<void> {
    const safe = redactSecrets(record) as SessionRecord;
    await this.library.upsert({
      sessionId: safe.sessionId,
      scene: safe.scene,
      title: safe.title,
      summary: safe.text,
      updatedAt: safe.updatedAt ?? Date.now(),
      archived: false,
    });
  }

  async search(query: string, options: {scene?: SceneId; limit?: number} = {}): Promise<SessionSearchResult[]> {
    const records = await this.library.search(query, options.limit ?? 20);
    return records
      .filter((record) => !options.scene || record.scene === options.scene)
      .map((record) => ({
        sessionId: record.sessionId,
        scene: record.scene as SceneId,
        title: record.title,
        text: record.summary,
        updatedAt: record.updatedAt,
        score: 1,
      }));
  }

  async reference(sessionId: string): Promise<{sessionId: string; source: "dsh-session-workbench"; mode: "read-only"} | undefined> {
    if ((await this.library.recall([sessionId])).length === 0) return undefined;
    return {sessionId, source: "dsh-session-workbench", mode: "read-only"};
  }
}

export interface ClevererDecision {
  allowed: boolean;
  attempt: number;
  reason?: "attempt_limit";
}

/**
 * Safe subset of cleverer-dsh: execution discipline and failure memory.
 * It delegates aggregate discipline state to ExecutionDiscipline and never
 * executes third-party scripts.
 */
export class ClevererDshAdapter {
  private readonly attempts = new Map<string, number>();
  private readonly failureLog = new Map<string, string[]>();
  private readonly maxAttempts: number;

  constructor(options: {maxAttempts?: number} = {}, private readonly discipline = new ExecutionDiscipline()) {
    this.maxAttempts = Math.max(1, options.maxAttempts ?? 3);
  }

  beforeTool(toolId: string): ClevererDecision {
    const attempt = (this.attempts.get(toolId) ?? 0) + 1;
    this.attempts.set(toolId, attempt);
    return attempt > this.maxAttempts ? {allowed: false, attempt, reason: "attempt_limit"} : {allowed: true, attempt};
  }

  recordFailure(toolId: string, message: string): void {
    this.discipline.observe(toolId, "failed");
    const failures = this.failureLog.get(toolId) ?? [];
    failures.push(String(redactSecrets(message)));
    this.failureLog.set(toolId, failures.slice(-this.maxAttempts));
  }

  recordSuccess(toolId: string): void {
    this.discipline.observe(toolId, "completed");
    this.attempts.delete(toolId);
  }

  failures(toolId: string): string[] {
    return [...(this.failureLog.get(toolId) ?? [])];
  }

  status(): ReturnType<ExecutionDiscipline["status"]> {
    return this.discipline.status();
  }

  reset(toolId?: string): void {
    if (toolId) {
      this.attempts.delete(toolId);
      this.failureLog.delete(toolId);
    } else {
      this.attempts.clear();
      this.failureLog.clear();
    }
  }
}

export interface EverosMemoryRecord {
  id?: string;
  key: string;
  value: unknown;
  scope?: MemoryScope;
}

export type EverosConnector = (record: EverosMemoryRecord) => Promise<EverosMemoryRecord>;

/** Optional EverOS loopback connector. Disabled unless the host explicitly enables it. */
export class EverosMemoryAdapter {
  private connector?: EverosConnector;

  enable(connector: EverosConnector): void {
    this.connector = connector;
  }

  disable(): void {
    this.connector = undefined;
  }

  status(): {enabled: boolean; available: boolean; source: "everos-memory"} {
    const enabled = Boolean(this.connector);
    return {enabled, available: enabled, source: "everos-memory"};
  }

  async capture(record: EverosMemoryRecord): Promise<EverosMemoryRecord> {
    if (!this.connector) throw new Error("EverOS memory adapter is disabled");
    const safe = {...record, value: sanitizeFact(record.key, record.value)};
    return this.connector(safe);
  }
}

export interface CommunityAdapterStatus {
  id: "dsh-agent-identity" | "dsh-session-workbench" | "cleverer-dsh" | "everos-memory";
  mode: "local" | "optional-loopback";
  permissions: string[];
}

export function communityAdapterStatuses(): CommunityAdapterStatus[] {
  return [
    {id: "dsh-agent-identity", mode: "local", permissions: ["prompt.identity.read", "prompt.memory.read"]},
    {id: "dsh-session-workbench", mode: "local", permissions: ["session.search", "session.read", "session.reference"]},
    {id: "cleverer-dsh", mode: "local", permissions: ["prompt.inject", "session.read", "diagnostic.run"]},
    {id: "everos-memory", mode: "optional-loopback", permissions: ["memory.read", "memory.capture", "network.loopback"]},
  ];
}
