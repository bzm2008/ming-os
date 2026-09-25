import { access } from "node:fs/promises";

export interface CapabilityStatus { id: string; available: boolean; permissionRequired: boolean; error?: string; }
export interface PlatformStatus { platform: NodeJS.Platform; capabilities: CapabilityStatus[]; }

export class PlatformStatusAdapter {
  async status(): Promise<PlatformStatus> {
    const papyrusPaths = process.platform === "linux" ? ["/usr/bin/papyrus", "/opt/papyrus"] : [];
    const papyrusAvailable = await Promise.all(papyrusPaths.map(async (path) => access(path).then(() => true).catch(() => false)));
    return {
      platform: process.platform,
      capabilities: [
        {id: "terminal", available: true, permissionRequired: false},
        {id: "browser-bridge", available: false, permissionRequired: true, error: "Browser bridge adapter not configured"},
        {id: "diagnostics", available: true, permissionRequired: false},
        {id: "papyrus-reference", available: papyrusAvailable.some(Boolean), permissionRequired: false, error: papyrusAvailable.some(Boolean) ? undefined : "PAPYRUS reference runtime is not installed"},
      ],
    };
  }
}
