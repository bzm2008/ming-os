export type ModelProviderId = "ming-main" | "openai-compatible" | "kim";
export interface ModelProvider {
  id: ModelProviderId;
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

export interface SelectedModel {
  provider: ModelProviderId;
  model: string;
  label: string;
  credentialRef: string;
}

export class ModelProviderRegistry {
  private readonly providers: Map<ModelProvider["id"], ModelProvider>;

  constructor(overrides: Partial<Record<ModelProviderId, ProviderOverrides>> = {}) {
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

  resolve(id: ModelProviderId): ModelProvider {
    const provider = this.providers.get(id);
    if (!provider) throw new Error(`Unknown model provider: ${id}`);
    return {...provider, capabilities: [...provider.capabilities]};
  }

  list(): ModelProvider[] {
    return [...this.providers.values()].map((provider) => ({...provider, capabilities: [...provider.capabilities]}));
  }

  select(id: ModelProviderId, model?: string): SelectedModel {
    const provider = this.resolve(id);
    const selectedModel = model ?? provider.model;
    return {provider: provider.id, model: selectedModel, label: provider.label, credentialRef: provider.credentialRef};
  }
}

export function createDefaultProviderRegistry(overrides: Partial<Record<ModelProviderId, ProviderOverrides>> = {}): ModelProviderRegistry {
  return new ModelProviderRegistry(overrides);
}
