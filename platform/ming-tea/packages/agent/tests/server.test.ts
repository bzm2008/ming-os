import { describe, expect, it } from "vitest";
import { createAgentServer } from "../src/server.js";

describe("agent server", () => {
  it("answers ping and creates an office session", async () => {
    const server = createAgentServer({socketPath: "/tmp/ming-tea-agent-test.sock"});
    expect(await server.handle({id: "1", action: "ping", payload: {}})).toMatchObject({ok: true});
    const response = await server.handle({id: "2", action: "session.create", payload: {scene: "office"}});
    expect(response).toMatchObject({ok: true, result: {scene: "office", state: "active"}});
    await server.close();
  });
});
