import type { Request, Response, SceneId } from "@ming-tea/protocol";
import { createNamedPipeTransport, createUnixTransport, type JsonLineTransport } from "@ming-tea/protocol";
import { SessionEngine } from "./session-engine.js";
import { DshAdapter } from "./dsh-adapter.js";

export interface AgentServerOptions { socketPath?: string; pipeName?: string; engine?: SessionEngine; dsh?: DshAdapter; }
export interface AgentServer { listen(): Promise<void>; close(): Promise<void>; handle(request: Request): Promise<Response>; }

export function createAgentServer(options: AgentServerOptions = {}): AgentServer {
  const engine = options.engine ?? new SessionEngine();
  const dsh = options.dsh ?? new DshAdapter();
  const transport: JsonLineTransport = options.pipeName
    ? createNamedPipeTransport(options.pipeName)
    : createUnixTransport(options.socketPath ?? process.env.MING_TEA_IPC_PATH ?? "/tmp/ming-tea-agent.sock");
  const handle = async (request: Request): Promise<Response> => {
    try {
      switch (request.action) {
        case "ping": return {id: request.id, ok: true, result: {service: "ming-tea-agent", protocol: "ming.tea.ipc.v1"}};
        case "status": return {id: request.id, ok: true, result: {...engine.status(), dsh: await dsh.start()}};
        case "session.create": return {id: request.id, ok: true, result: engine.createSession(request.payload.scene as SceneId)};
        case "session.cancel": return {id: request.id, ok: true, result: engine.cancelSession(String(request.payload.sessionId))};
        case "tool.request": return {id: request.id, ok: true, result: engine.requestTool(String(request.payload.sessionId), String(request.payload.toolId), (request.payload.arguments ?? {}) as Record<string, unknown>)};
        case "approval.consume": return {id: request.id, ok: true, result: engine.consumeApproval(String(request.payload.sessionId), String(request.payload.approvalToken), String(request.payload.toolId))};
        default: return {id: request.id, ok: false, error: {code: "unknown_action", message: `Unknown action: ${request.action}`}};
      }
    } catch (error) {
      return {id: request.id, ok: false, error: {code: "request_failed", message: error instanceof Error ? error.message : String(error)}};
    }
  };
  return {listen: () => transport.listen(handle), close: async () => { await transport.close(); await dsh.stop(); }, handle};
}
