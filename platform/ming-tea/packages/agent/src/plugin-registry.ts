import { readFileSync } from "node:fs";

export interface AuditedPlugin {
  package: string;
  version: string;
  license: string;
  integrity: string;
  capabilities: string[];
  risk: string;
}

export class PluginRegistry {
  load(lockPath: string): AuditedPlugin[] {
    const payload = JSON.parse(readFileSync(lockPath, "utf8")) as {plugins?: unknown};
    if (!Array.isArray(payload.plugins)) throw new Error("Plugin lock must contain plugins");
    return payload.plugins.map((raw) => {
      if (!raw || typeof raw !== "object") throw new Error("Plugin entry must be an object");
      const plugin = raw as Partial<AuditedPlugin>;
      if (!plugin.package || !plugin.version || !plugin.license || !plugin.integrity || !plugin.risk || !Array.isArray(plugin.capabilities) || plugin.capabilities.length === 0) {
        throw new Error("Plugin entry is missing audit fields");
      }
      if (!plugin.integrity.startsWith("sha512-")) throw new Error(`Invalid integrity for ${plugin.package}`);
      return plugin as AuditedPlugin;
    });
  }
}
