import type { Request, Response, SceneId } from "@ming-tea/protocol";
import { createNamedPipeTransport, createUnixTransport, type JsonLineTransport } from "@ming-tea/protocol";
import { SessionEngine, type ThinkingLevel } from "./session-engine.js";
import { DshAdapter } from "./dsh-adapter.js";
import { LocalMemoryStore } from "./memory.js";
import { SessionLibrary } from "./session-library.js";
import { ExecutionDiscipline } from "./discipline.js";
import { communityAdapterStatuses } from "./community-adapters.js";
import { OtaBridge, type OtaAction } from "./ota-bridge.js";
import { PlatformStatusAdapter } from "./platform-status.js";
import { createDefaultProviderRegistry, type ModelProviderRegistry } from "./model-provider.js";

export interface AgentServerOptions { socketPath?: string; pipeName?: string; engine?: SessionEngine; dsh?: DshAdapter; memory?: LocalMemoryStore; sessions?: SessionLibrary; discipline?: ExecutionDiscipline; ota?: OtaBridge; platform?: PlatformStatusAdapter; providers?: ModelProviderRegistry; }
export interface AgentServer { listen(): Promise<void>; close(): Promise<void>; handle(request: Request): Promise<Response>; }

export function createAgentServer(options: AgentServerOptions = {}): AgentServer {
  const engine = options.engine ?? new SessionEngine();
  const dsh = options.dsh ?? new DshAdapter();
  const memory = options.memory ?? new LocalMemoryStore();
  const sessions = options.sessions ?? new SessionLibrary();
  const discipline = options.discipline ?? new ExecutionDiscipline();
  const ota = options.ota ?? new OtaBridge();
  const platform = options.platform ?? new PlatformStatusAdapter();
  const providers = options.providers ?? createDefaultProviderRegistry();
  let selectedModel = providers.select("ming-main");
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
        case "session.thinking.set": return {id: request.id, ok: true, result: engine.setThinking(String(request.payload.sessionId), String(request.payload.thinking) as ThinkingLevel)};
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
        case "model.providers": return {id: request.id, ok: true, result: providers.list()};
        case "model.select": {
          selectedModel = providers.select(String(request.payload.provider) as Parameters<ModelProviderRegistry["select"]>[0], request.payload.model ? String(request.payload.model) : undefined);
          return {id: request.id, ok: true, result: selectedModel};
        }
        case "discipline.status": return {id: request.id, ok: true, result: discipline.status()};
        case "platform.status": return {id: request.id, ok: true, result: await platform.status()};
        case "platform.permission.status": return {id: request.id, ok: true, result: await platform.status()};
        case "os.update.status":
        case "os.update.check":
        case "os.update.prepare":
        case "os.update.reboot": return {id: request.id, ok: true, result: await ota.run(request.action.slice("os.update.".length) as OtaAction)};
        default: return {id: request.id, ok: false, error: {code: "unknown_action", message: `Unknown action: ${request.action}`}};
      }
    } catch (error) {
      return {id: request.id, ok: false, error: {code: "request_failed", message: error instanceof Error ? error.message : String(error)}};
    }
  };
  return {listen: () => transport.listen(handle), close: async () => { await transport.close(); await dsh.stop(); }, handle};
}
