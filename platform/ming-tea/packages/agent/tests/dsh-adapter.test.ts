import { describe, expect, it } from "vitest";
import { ComputerUseGuard } from "../src/computer-use-guard.js";
import { PluginRegistry } from "../src/plugin-registry.js";

describe("DSH adapters", () => {
  it("loads official computer-use from the audited lock", () => {
    const plugins = new PluginRegistry().load("../../assets/ming-tea-dsh-lock.json");
    expect(plugins.some((plugin) => plugin.package === "@deepseek-ai/dsh-computer-use")).toBe(true);
  });

  it("requires approval for uploads and rejects unlisted windows", () => {
    const guard = new ComputerUseGuard({allowedWindows: ["Firefox"], maxActions: 20});
    expect(guard.check({kind: "click", window: "Firefox"})).toBe("allow");
    expect(guard.check({kind: "upload", window: "Firefox"})).toBe("confirm");
    expect(guard.check({kind: "click", window: "Password Manager"})).toBe("deny");
  });
});
