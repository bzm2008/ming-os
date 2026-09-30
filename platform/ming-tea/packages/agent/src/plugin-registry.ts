import { readFileSync } from "node:fs";

export interface AuditedPlugin {
  package: string;
  version: string;
  license: string;
  integrity?: string;
  source?: string;
  capabilities: string[];
  risk: string;
}

// 第一方包（随本仓库源码分发，锁文件里以 link: 声明）的完整性由版本控制背书，
// 没有 npm integrity；其余插件一律要求 sha512- 完整性，不得放宽。
function isFirstParty(plugin: Partial<AuditedPlugin>): boolean {
  return typeof plugin.source === "string" && plugin.source.startsWith("link:");
}

export class PluginRegistry {
  load(lockPath: string): AuditedPlugin[] {
    const payload = JSON.parse(readFileSync(lockPath, "utf8")) as {plugins?: unknown};
    if (!Array.isArray(payload.plugins)) throw new Error("Plugin lock must contain plugins");
    return payload.plugins.map((raw) => {
      if (!raw || typeof raw !== "object") throw new Error("Plugin entry must be an object");
      const plugin = raw as Partial<AuditedPlugin>;
      if (!plugin.package || !plugin.version || !plugin.license || !plugin.risk || !Array.isArray(plugin.capabilities) || plugin.capabilities.length === 0) {
        throw new Error("Plugin entry is missing audit fields");
      }
      if (!isFirstParty(plugin)) {
        if (!plugin.integrity) throw new Error(`Plugin entry is missing integrity for ${plugin.package}`);
        if (!plugin.integrity.startsWith("sha512-")) throw new Error(`Invalid integrity for ${plugin.package}`);
      }
      return plugin as AuditedPlugin;
    });
  }
}
