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

  it("keeps scene selection in the pre-session gate", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("started: false");
    expect(source).toContain("state.started ? conversation() : modeGate()");
    expect(source).toContain('if (state.started) return; state.scene');
    expect(source).toContain("thinkingLevels");
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

  it("packages the development Agent resource and uses the app-local build script", () => {
    const desktop = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8"));
    const config = JSON.parse(readFileSync(join(__dirname, "..", "src-tauri", "tauri.conf.json"), "utf8"));
    expect(desktop.scripts["build:agent"]).toContain("../..");
    expect(config.bundle.resources["resources/ming-tea-agent.mjs"]).toBe("ming-tea-agent.mjs");
  });

  it("exposes a native Agent runtime status command", () => {
    const rust = readFileSync(join(__dirname, "..", "src-tauri", "src", "lib.rs"), "utf8");
    // 2026-10-01 起壳不再启动进程内桩 agent（resources/ming-tea-agent.mjs 只在仓库里留档），
    // 改为真正监督 DSH host 并暴露它的状态；因此这里断言的是新的命令面。
    expect(rust).toContain("fn dsh_status");
    expect(rust).toContain("use dsh_host::{DshHost");
    expect(rust).toContain("fn dsh_start");
    expect(rust).toContain("fn summon_show");
    expect(rust).toContain("install_hotkey_daemon");
  });

  it("keeps mode and thinking selection in the pre-session gate", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("mode-gate");
    expect(source).toContain('data-mode="${id}"');
    expect(source).not.toContain("初始思考强度");
    expect(source).not.toContain("thinking-control");
    expect(source).toContain("state.started ? conversation() : modeGate()");
    expect(source).toContain("if (state.started) return");
    expect(source).not.toContain("scene-rail");
    expect(source).not.toContain("rail-link");
  });

  it("keeps the locked mode while exposing thinking controls during a session", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    expect(source).toContain("thinking-slider");
    expect(source).toContain('type="range"');
    expect(source).toContain('data-thinking-slider');
    expect(source).not.toContain("thinking-inline");
    expect(source).toContain("state.thinking = level");
    expect(source).toContain("思考强度已更新 · 下一轮生效");
    expect(source).toContain("当前工作模式已锁定");
    expect(source).toContain("balanced");
  });

  it("uses the audited community DSH Codex UI as the workspace baseline", () => {
    const source = readFileSync(join(__dirname, "..", "src", "main.ts"), "utf8");
    const lock = JSON.parse(readFileSync(join(__dirname, "../../../../../assets/ming-tea-dsh-lock.json"), "utf8"));
    expect(lock.plugins).toEqual(expect.arrayContaining([
      expect.objectContaining({package: "@michengai/dsh-codex-ui", version: "1.1.25", license: "Apache-2.0"}),
    ]));
    expect(source).toContain("DSH 社区工作台");
    expect(source).toContain("社区 Codex UI 负责会话和工作区");
    expect(source).not.toContain("OpenCode Zen");
    expect(source).not.toContain("big-pickle");
  });
});
