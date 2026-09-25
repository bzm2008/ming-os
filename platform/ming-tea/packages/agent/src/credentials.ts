export interface CredentialStore {
  get(ref: string): Promise<string | undefined>;
  set(ref: string, secret: string): Promise<void>;
  delete(ref: string): Promise<void>;
}

export class MemoryCredentialStore implements CredentialStore {
  private readonly values = new Map<string, string>();
  async get(ref: string): Promise<string | undefined> { return this.values.get(ref); }
  async set(ref: string, secret: string): Promise<void> { this.values.set(ref, secret); }
  async delete(ref: string): Promise<void> { this.values.delete(ref); }
}
