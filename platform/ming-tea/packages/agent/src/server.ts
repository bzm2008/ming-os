import type { Request, Response, SceneId } from "@ming-tea/protocol";
import { createNamedPipeTransport, createUnixTransport, type JsonLineTransport } from "@ming-tea/protocol";
import { SessionEngine } from "./session-engine.js";
import { DshAdapter } from "./dsh-adapter.js";
import { LocalMemoryStore } from "./memory.js";
import { SessionLibrary } from "./session-library.js";
import { ExecutionDiscipline } from "./discipline.js";
import { communityAdapterStatuses } from "./community-adapters.js";

export interface AgentServerOptions { socketPath?: string; pipeName?: string; engine?: SessionEngine; dsh?: DshAdapter; memory?: LocalMemoryStore; sessions?: SessionLibrary; discipline?: ExecutionDiscipline; }
export interface AgentServer { listen(): Promise<void>; close(): Promise<void>; handle(request: Request): Promise<Response>; }

export function createAgentServer(options: AgentServerOptions = {}): AgentServer {
  const engine = options.engine ?? new SessionEngine();
  const dsh = options.dsh ?? new DshAdapter();
  const memory = options.memory ?? new LocalMemoryStore();
  const sessions = options.sessions ?? new SessionLibrary();
  const discipline = options.discipline ?? new ExecutionDiscipline();
  const transport: JsonLineTransport = options.pipeName
    ? createNamedPipeTransport(options.pipeName)
    : createUnixTransport(options.socketPath ?? process.env.MING_TEA_IPC_PATH ?? "/tmp/ming-tea-agent.sock");
  const handle = async (request: Request): Promise<Response> => {
    try {
      switch (request.action) {
        case "ping": return {id: request.id, ok: true, result: {service: "ming-tea-agent", protocol: "ming.tea.ipc.v1"}};
        case "status": return {id: request.id, ok: true, result: {...engine.status(), dsh: await dsh.start(), communityAdapters: communityAdapterStatuses()}};
        case "session.create": {
          const session = engine.createSession(request.payload.scene as SceneId);
          await sessions.upsert({sessionId: session.sessionId, scene: session.scene, title: `${session.scene} session`, summary: "", updatedAt: session.createdAt, archived: false});
          return {id: request.id, ok: true, result: session};
        }
        case "session.cancel": return {id: request.id, ok: true, result: engine.cancelSession(String(request.payload.sessionId))};
        case "tool.request": {
          const events = engine.requestTool(String(request.payload.sessionId), String(request.payload.toolId), (request.payload.arguments ?? {}) as Record<string, unknown>);
          for (const event of events) discipline.observe(String(event.toolId ?? "unknown"), event.type === "tool.failed" ? "failed" : event.type === "tool.started" ? "started" : "completed");
          return {id: request.id, ok: true, result: events};
        }
        case "approval.consume": return {id: request.id, ok: true, result: engine.consumeApproval(String(request.payload.sessionId), String(request.payload.approvalToken), String(request.payload.toolId))};
        case "memory.profile.get": return {id: request.id, ok: true, result: await memory.profile()};
        case "memory.recall": return {id: request.id, ok: true, result: await memory.recall(String(request.payload.query ?? ""), Number(request.payload.limit ?? 5))};
        case "memory.capture": return {id: request.id, ok: true, result: await memory.remember(String(request.payload.content ?? ""), (request.payload.kind as "profile" | "preference" | "fact" | "note") ?? "note", Array.isArray(request.payload.tags) ? request.payload.tags.map(String) : [], "user")};
        case "session.search": return {id: request.id, ok: true, result: await sessions.search(String(request.payload.query ?? ""), Number(request.payload.limit ?? 20))};
        case "session.recall": return {id: request.id, ok: true, result: await sessions.recall(Array.isArray(request.payload.sessionIds) ? request.payload.sessionIds.map(String).slice(0, 3) : [])};
        case "discipline.status": return {id: request.id, ok: true, result: discipline.status()};
        default: return {id: request.id, ok: false, error: {code: "unknown_action", message: `Unknown action: ${request.action}`}};
      }
    } catch (error) {
      return {id: request.id, ok: false, error: {code: "request_failed", message: error instanceof Error ? error.message : String(error)}};
    }
  };
  return {listen: () => transport.listen(handle), close: async () => { await transport.close(); await dsh.stop(); }, handle};
}
