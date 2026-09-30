import { describe, expect, it } from "vitest";
import { createDefaultProviderRegistry } from "../src/model-provider.js";
import { MemoryCredentialStore } from "../src/credentials.js";

describe("model providers", () => {
  it("normalizes Ming, OpenAI-compatible, and Kim providers", () => {
    const registry = createDefaultProviderRegistry();
    expect(registry.resolve("ming-main").id).toBe("ming-main");
    expect(registry.resolve("openai-compatible").id).toBe("openai-compatible");
    expect(registry.resolve("kim").id).toBe("kim");
    expect(registry.list()).toHaveLength(3);
  });

  it("keeps credentials behind references", async () => {
    const provider = createDefaultProviderRegistry().resolve("kim");
    expect(provider).not.toHaveProperty("apiKey");
    expect(provider.credentialRef).toMatch(/^credential:/);
    const store = new MemoryCredentialStore();
    await store.set(provider.credentialRef, "secret-value");
    expect(await store.get(provider.credentialRef)).toBe("secret-value");
    expect(JSON.stringify(provider)).not.toContain("secret-value");
  });
});
