import { describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import {
  ClevererDshAdapter,
  EverosMemoryAdapter,
  IdentityMemoryAdapter,
  SessionWorkbenchAdapter,
} from "../src/community-adapters.js";
import { LocalMemoryStore } from "../src/memory.js";
import { SessionLibrary } from "../src/session-library.js";

describe("community plugin adapters", () => {
  it("stores identity facts by scope and redacts credentials", async () => {
    const adapter = new IdentityMemoryAdapter(new LocalMemoryStore("/tmp/ming-tea-community-identity-" + randomUUID() + ".jsonl"));
    await adapter.remember({scope: "profile", key: "preferred_language", value: "zh-CN"});
    await adapter.remember({scope: "profile", key: "api_key", value: "sk-live-secret"});

    const context = await adapter.context("profile");
    expect(context).toMatchObject({facts: {preferred_language: "zh-CN", api_key: "[已隐藏]"}});
    expect(JSON.stringify(context)).not.toContain("sk-live-secret");
  });

  it("keeps session workbench search local and returns references", async () => {
    const adapter = new SessionWorkbenchAdapter(new SessionLibrary("/tmp/ming-tea-community-sessions-" + randomUUID() + ".json"));
    await adapter.index({sessionId: "s1", scene: "development", title: "修复构建脚本", text: "检查 npm 构建日志并运行 pytest"});
    await adapter.index({sessionId: "s2", scene: "office", title: "整理报表", text: "编辑 Excel 销售表格"});

    expect(await adapter.search("构建")).toMatchObject([{sessionId: "s1", title: "修复构建脚本"}]);
    await expect(adapter.reference("s1")).resolves.toEqual({sessionId: "s1", source: "dsh-session-workbench", mode: "read-only"});
  });

  it("limits cleverer execution retries and records failures", () => {
    const adapter = new ClevererDshAdapter({maxAttempts: 2});
    expect(adapter.beforeTool("terminal.run")).toEqual({allowed: true, attempt: 1});
    adapter.recordFailure("terminal.run", "network timeout");
    expect(adapter.beforeTool("terminal.run")).toEqual({allowed: true, attempt: 2});
    adapter.recordFailure("terminal.run", "network timeout");
    expect(adapter.beforeTool("terminal.run")).toEqual({allowed: false, attempt: 3, reason: "attempt_limit"});
    expect(adapter.failures("terminal.run")).toEqual(["network timeout", "network timeout"]);
  });

  it("keeps EverOS disabled until an explicit loopback connector is provided", async () => {
    const adapter = new EverosMemoryAdapter();
    expect(adapter.status()).toEqual({enabled: false, available: false, source: "everos-memory"});
    await expect(adapter.capture({key: "topic", value: "demo"})).rejects.toThrow(/disabled/i);

    adapter.enable(async (record) => ({...record, id: "everos-1"}));
    expect(adapter.status()).toMatchObject({enabled: true});
    await expect(adapter.capture({key: "topic", value: "demo"})).resolves.toMatchObject({id: "everos-1"});
  });
});
