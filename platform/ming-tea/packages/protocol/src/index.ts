export type SceneId = "office" | "development" | "learning";

export type Action =
  | "ping"
  | "status"
  | "session.create"
  | "session.cancel"
  | "tool.request"
  | "approval.consume"
  | "memory.profile.get"
  | "memory.recall"
  | "memory.capture"
  | "session.search"
  | "session.recall"
  | "discipline.status";

export interface Request {
  id: string;
  action: Action;
  payload: Record<string, unknown>;
}

export interface Response {
  id: string;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string };
}

export type EventType =
  | "session.created"
  | "plan.updated"
  | "tool.requested"
  | "approval.requested"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "session.completed";

export interface MingTeaEvent {
  type: EventType;
  sessionId: string;
  timestamp: number;
  [key: string]: unknown;
}

export { decodeJsonLine, encodeJsonLine } from "./json-lines.js";
export { createNamedPipeTransport, createUnixTransport } from "./transport.js";
export type { JsonLineTransport, RequestHandler } from "./transport.js";
