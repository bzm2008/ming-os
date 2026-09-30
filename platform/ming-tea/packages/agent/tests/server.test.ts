import { describe, expect, it } from "vitest";
import { createAgentServer } from "../src/server.js";
import { LocalMemoryStore } from "../src/memory.js";
import { SessionLibrary } from "../src/session-library.js";

describe("agent server", () => {
  it("answers ping and creates an office session", async () => {
    const server = createAgentServer({socketPath: "/tmp/ming-tea-agent-test.sock", memory: new LocalMemoryStore("/tmp/ming-tea-memory-test.jsonl"), sessions: new SessionLibrary("/tmp/ming-tea-sessions-test.json")});
    expect(await server.handle({id: "1", action: "ping", payload: {}})).toMatchObject({ok: true});
    const response = await server.handle({id: "2", action: "session.create", payload: {scene: "office"}});
    expect(response).toMatchObject({ok: true, result: {scene: "office", state: "active"}});
    const sessionId = (response.result as {sessionId: string}).sessionId;
    expect(await server.handle({id: "3", action: "session.thinking.set", payload: {sessionId, thinking: "deep"}})).toMatchObject({ok: true, result: {thinking: "deep"}});
    await server.close();
  });

  it("exposes local memory and session search through the same IPC contract", async () => {
    const server = createAgentServer({socketPath: "/tmp/ming-tea-agent-test-2.sock", memory: new LocalMemoryStore("/tmp/ming-tea-memory-test-2.jsonl"), sessions: new SessionLibrary("/tmp/ming-tea-sessions-test-2.json")});
    const captured = await server.handle({id: "1", action: "memory.capture", payload: {kind: "preference", content: "用户偏好中文回复", tags: ["language"]}});
    expect(captured.ok).toBe(true);
    const recalled = await server.handle({id: "2", action: "memory.recall", payload: {query: "中文"}});
    expect(recalled).toMatchObject({ok: true, result: [{kind: "preference"}]});
    const found = await server.handle({id: "3", action: "session.search", payload: {query: "office"}});
    expect(found.ok).toBe(true);
    await server.close();
  });

  it("reports community adapters through the status contract", async () => {
    const server = createAgentServer({socketPath: "/tmp/ming-tea-agent-status-test.sock"});
    const response = await server.handle({id: "1", action: "status", payload: {}});
    expect(response).toMatchObject({
      ok: true,
      result: {communityAdapters: expect.arrayContaining([
        expect.objectContaining({id: "dsh-agent-identity", mode: "local"}),
        expect.objectContaining({id: "everos-memory", mode: "optional-loopback"}),
      ])},
    });
    await server.close();
  });

  it("lists and selects an audited provider without returning credentials", async () => {
    const server = createAgentServer({socketPath: "/tmp/ming-tea-agent-model-test.sock"});
    const listed = await server.handle({id: "1", action: "model.providers", payload: {}});
    expect(listed).toMatchObject({ok: true, result: expect.arrayContaining([
      expect.objectContaining({id: "ming-main", label: "Ming 主站"}),
    ])});
    expect(JSON.stringify(listed)).not.toContain("Authorization");
    const selected = await server.handle({id: "2", action: "model.select", payload: {provider: "ming-main", model: "ming-default"}});
    expect(selected).toMatchObject({ok: true, result: {provider: "ming-main", model: "ming-default", label: "Ming 主站"}});
    await server.close();
  });
});
