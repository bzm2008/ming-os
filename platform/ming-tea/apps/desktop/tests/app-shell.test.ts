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

  it("keeps scene and thinking selection in the pre-session gate", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("started: false");
    expect(source).toContain("state.started ? conversation() : modeGate()");
    expect(source).toContain('if (state.started) return; state.scene');
    expect(source).toContain('state.thinking = button.dataset.thinking as Thinking');
    expect(source).toContain('state.started = true');
    expect(source).toContain('data-action="start"');
    expect(source).toContain("已锁定");
  });

  it("uses the community-inspired Codex workspace sidebar and inspector", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    const css = readFileSync(join(__dirname, "..", "src", "theme.css"), "utf8");
    expect(source).toContain('<section class="workspace"><div class="workspace-sidebar">');
    expect(source).toContain("codex-sidebar");
    expect(source).toContain("历史项目");
    expect(source).toContain("本月额度");
    expect(source).toContain("铭荼宠物");
    expect(source).toContain('<aside class="inspector">');
    expect(css).toContain("grid-template-columns: 250px minmax(0, 1fr) 320px");
    expect(css).toContain(".codex-sidebar");
  });

  it("has a standalone Tauri host for Windows and macOS", () => {
    const config = JSON.parse(readFileSync(join(__dirname, "..", "src-tauri", "tauri.conf.json"), "utf8"));
    expect(config.productName).toBe("铭荼");
    expect(config.identifier).toBe("cn.mingos.mingtea");
    expect(config.app.windows[0].minWidth).toBeGreaterThanOrEqual(900);
    expect(config.bundle.macOS.minimumSystemVersion).toBe("11.0");
    expect(config.bundle.macOS.dmg.windowSize.width).toBe(720);
  });

  it("declares macOS automation usage text and least privilege entitlement", () => {
    const plist = readFileSync(join(__dirname, "..", "src-tauri", "Info.plist"), "utf8");
    const entitlements = readFileSync(join(__dirname, "..", "src-tauri", "entitlements.plist"), "utf8");
    expect(plist).toContain("NSAppleEventsUsageDescription");
    expect(entitlements).toContain("com.apple.security.network.client");
  });

  it("keeps mode and thinking selection in the pre-session gate", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("mode-gate");
    expect(source).toContain('data-mode="${id}"');
    expect(source).toContain('data-thinking="${id}"');
    expect(source).toContain("state.started ? conversation() : modeGate()");
    expect(source).toContain("if (state.started) return");
    expect(source).not.toContain("scene-rail");
    expect(source).not.toContain("rail-link");
  });

  it("keeps the locked mode while exposing thinking controls during a session", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("thinking-inline");
    expect(source).toContain("state.thinking = button.dataset.thinking as Thinking");
    expect(source).toContain("思考强度已更新 · 下一轮生效");
    expect(source).toContain("当前工作模式已锁定");
    expect(source).toContain("thinking-inline");
  });
});
