import { describe, expect, it } from "vitest";
import { SCENES } from "../src/scenes.js";
import { PermissionPolicy } from "../src/policy.js";
import { SessionEngine } from "../src/session-engine.js";
import { redactSecrets } from "../src/redaction.js";

describe("shared session engine", () => {
  it("routes all three scenes to shared tools", () => {
    expect(Object.keys(SCENES).sort()).toEqual(["development", "learning", "office"]);
  });

  it("requires approval for privileged and destructive operations", () => {
    const policy = new PermissionPolicy();
    expect(policy.decide("terminal.run", {command: "sudo apt install demo"})).toBe("confirm");
    expect(policy.decide("file.delete", {path: "/tmp/demo"})).toBe("confirm");
    expect(policy.decide("file.read", {path: "/tmp/demo"})).toBe("allow");
    expect(policy.decide("unknown.tool", {})).toBe("deny");
  });

  it("consumes approvals once and binds them to the requested tool", () => {
    const policy = new PermissionPolicy();
    const token = policy.issueApproval("terminal.run", {command: "sudo true"});
    expect(policy.consumeApproval(token, "file.delete")).toBe(false);
    expect(policy.consumeApproval(token, "terminal.run")).toBe(true);
    expect(policy.consumeApproval(token, "terminal.run")).toBe(false);
  });

  it("emits approval events and stops accepting work after cancellation", () => {
    const engine = new SessionEngine();
    const session = engine.createSession("development");
    const events = engine.requestTool(session.sessionId, "terminal.run", {command: "sudo true"});
    expect(events.map((event) => event.type)).toEqual(["tool.requested", "approval.requested"]);
    expect(engine.cancelSession(session.sessionId)).toBe(true);
    expect(() => engine.requestTool(session.sessionId, "file.read", {})).toThrow(/ended|cancelled/i);
  });

  it("changes thinking for the next turn without changing the locked scene", () => {
    const engine = new SessionEngine();
    const session = engine.createSession("development");
    const changed = engine.setThinking(session.sessionId, "deep");
    expect(changed).toMatchObject({scene: "development", thinking: "deep", state: "active"});
    expect(() => engine.setThinking(session.sessionId, "invalid" as never)).toThrow(/Unknown thinking/);
  });

  it("redacts credentials in structured values and text", () => {
    const result = redactSecrets({api_key: "private", message: "Authorization: Bearer abc123 sk-secret123"});
    expect(JSON.stringify(result)).not.toMatch(/private|abc123|sk-secret123/);
  });
});
