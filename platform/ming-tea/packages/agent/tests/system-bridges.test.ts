import { describe, expect, it } from "vitest";
import { OtaBridge } from "../src/ota-bridge.js";
import { PlatformStatusAdapter } from "../src/platform-status.js";

describe("system bridges", () => {
  it("maps Ming OTA actions to ming-update without a shell", async () => {
    const calls: string[][] = [];
    const bridge = new OtaBridge(async (command, args) => { calls.push([command, ...args]); return {code: 0, stdout: '{"state":"ready"}', stderr: ""}; }, "ming-update", "linux");
    const result = await bridge.run("status");
    expect(result).toMatchObject({action: "status", ok: true, output: {state: "ready"}});
    expect(calls).toEqual([["ming-update", "status", "--json"]]);
  });

  it("returns a readable unavailable result when OTA fails", async () => {
    const bridge = new OtaBridge(async () => ({code: 1, stdout: "{}", stderr: "signature invalid"}), "ming-update", "linux");
    await expect(bridge.run("prepare")).resolves.toMatchObject({ok: false, error: "signature invalid"});
  });

  it("reports PAPYRUS as a reference capability without executing it", async () => {
    const status = await new PlatformStatusAdapter().status();
    expect(status.capabilities.some((item) => item.id === "papyrus-reference")).toBe(true);
    expect(status.capabilities.some((item) => item.id === "browser-bridge")).toBe(true);
  });

  it("exposes macOS automation permission state without attempting to bypass TCC", async () => {
    const status = await new PlatformStatusAdapter().status();
    if (status.platform === "darwin") {
      expect(status.capabilities.find((item) => item.id === "accessibility")).toMatchObject({available: false, permissionRequired: true});
      expect(status.capabilities.find((item) => item.id === "automation")).toMatchObject({available: false, permissionRequired: true});
    }
  });
});
