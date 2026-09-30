import { describe, expect, it } from "vitest";
import { ComputerUseGuard } from "../src/computer-use-guard.js";
import { resolveDshCommand } from "../src/dsh-adapter.js";
import { PluginRegistry } from "../src/plugin-registry.js";

describe("DSH adapters", () => {
  it("boots the audited ming-tea profile when a local runtime is configured", () => {
    expect(resolveDshCommand({MING_TEA_DSH_RUNTIME_DIR: "/opt/ming-tea/runtime"})).toEqual([
      "/opt/ming-tea/runtime/node_modules/.bin/dsh", "--profile", "ming-tea",
    ]);
  });

  it("loads official computer-use from the audited lock", () => {
    const plugins = new PluginRegistry().load("../../assets/ming-tea-dsh-lock.json");
    expect(plugins.some((plugin) => plugin.package === "@deepseek-ai/dsh-computer-use")).toBe(true);
  });

  it("keeps sha512 integrity mandatory for registry packages only", () => {
    const plugins = new PluginRegistry().load("../../assets/ming-tea-dsh-lock.json");
    const firstParty = plugins.filter((plugin) => plugin.source?.startsWith("link:"));
    const registry = plugins.filter((plugin) => !plugin.source?.startsWith("link:"));
    expect(firstParty.map((plugin) => plugin.package)).toContain("@ming-tea/dsh-ui");
    expect(firstParty.every((plugin) => plugin.integrity === undefined)).toBe(true);
    expect(registry.length).toBeGreaterThan(0);
    expect(registry.every((plugin) => plugin.integrity?.startsWith("sha512-"))).toBe(true);
  });

  it("requires approval for uploads and rejects unlisted windows", () => {
    const guard = new ComputerUseGuard({allowedWindows: ["Firefox"], maxActions: 20});
    expect(guard.check({kind: "click", window: "Firefox"})).toBe("allow");
    expect(guard.check({kind: "upload", window: "Firefox"})).toBe("confirm");
    expect(guard.check({kind: "click", window: "Password Manager"})).toBe("deny");
  });
});
