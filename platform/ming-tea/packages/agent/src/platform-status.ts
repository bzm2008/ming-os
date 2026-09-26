import { access } from "node:fs/promises";

export interface CapabilityStatus { id: string; available: boolean; permissionRequired: boolean; error?: string; }
export interface PlatformStatus { platform: NodeJS.Platform; capabilities: CapabilityStatus[]; }

export class PlatformStatusAdapter {
  async status(): Promise<PlatformStatus> {
    const papyrusPaths = process.platform === "linux" ? ["/usr/bin/papyrus", "/opt/papyrus"] : [];
    const papyrusAvailable = await Promise.all(papyrusPaths.map(async (path) => access(path).then(() => true).catch(() => false)));
    const platformCapabilities: CapabilityStatus[] = process.platform === "darwin"
      ? [
        {id: "accessibility", available: false, permissionRequired: true, error: "请在系统设置 > 隐私与安全性 > 辅助功能中允许铭荼。"},
        {id: "automation", available: false, permissionRequired: true, error: "执行应用自动化前需要用户授予 Automation 权限。"},
      ]
      : process.platform === "win32"
        ? [{id: "ui-automation", available: false, permissionRequired: true, error: "Windows UI Automation adapter 尚未启用。"}]
        : [{id: "accessibility", available: true, permissionRequired: false}];
    return {
      platform: process.platform,
      capabilities: [
        {id: "terminal", available: true, permissionRequired: false},
        {id: "browser-bridge", available: false, permissionRequired: true, error: "Browser bridge adapter not configured"},
        {id: "diagnostics", available: true, permissionRequired: false},
        {id: "papyrus-reference", available: papyrusAvailable.some(Boolean), permissionRequired: false, error: papyrusAvailable.some(Boolean) ? undefined : "PAPYRUS reference runtime is not installed"},
        ...platformCapabilities,
      ],
    };
  }
}
