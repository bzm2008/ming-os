export interface ModelProvider {
  id: "ming-main" | "openai-compatible" | "kim";
  label: string;
  endpoint: string;
  model: string;
  credentialRef: string;
  capabilities: string[];
}

export interface ProviderOverrides {
  endpoint?: string;
  model?: string;
}

export class ModelProviderRegistry {
  private readonly providers: Map<ModelProvider["id"], ModelProvider>;

  constructor(overrides: Partial<Record<ModelProvider["id"], ProviderOverrides>> = {}) {
    this.providers = new Map([
      ["ming-main", {
        id: "ming-main", label: "Ming 主站", endpoint: overrides["ming-main"]?.endpoint ?? "https://api.ming-os.cn/v1", model: overrides["ming-main"]?.model ?? "ming-default", credentialRef: "credential:ming-main", capabilities: ["chat", "tool-use"],
      }],
      ["openai-compatible", {
        id: "openai-compatible", label: "OpenAI-compatible", endpoint: overrides["openai-compatible"]?.endpoint ?? "http://127.0.0.1:8000/v1", model: overrides["openai-compatible"]?.model ?? "default", credentialRef: "credential:openai-compatible", capabilities: ["chat", "tool-use"],
      }],
      ["kim", {
        id: "kim", label: "Kim", endpoint: overrides.kim?.endpoint ?? "https://api.kim.com/v1", model: overrides.kim?.model ?? "kim-default", credentialRef: "credential:kim", capabilities: ["chat", "tool-use"],
      }],
    ]);
  }

  resolve(id: ModelProvider["id"]): ModelProvider {
    const provider = this.providers.get(id);
    if (!provider) throw new Error(`Unknown model provider: ${id}`);
    return {...provider, capabilities: [...provider.capabilities]};
  }

  list(): ModelProvider[] {
    return [...this.providers.values()].map((provider) => ({...provider, capabilities: [...provider.capabilities]}));
  }
}

export function createDefaultProviderRegistry(overrides: Partial<Record<ModelProvider["id"], ProviderOverrides>> = {}): ModelProviderRegistry {
  return new ModelProviderRegistry(overrides);
}
