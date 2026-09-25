import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("desktop shell", () => {
  it("contains the three native Ming Tea scenes and no DSH web entry", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("办公模式");
    expect(source).toContain("开发模式");
    expect(source).toContain("辅助学习模式");
    expect(source).not.toContain("127.0.0.1:3080");
  });

  it("has a standalone Tauri host for Windows and macOS", () => {
    const config = JSON.parse(readFileSync(join(__dirname, "..", "src-tauri", "tauri.conf.json"), "utf8"));
    expect(config.productName).toBe("铭荼");
    expect(config.identifier).toBe("cn.mingos.mingtea");
    expect(config.app.windows[0].minWidth).toBeGreaterThanOrEqual(900);
  });
});
