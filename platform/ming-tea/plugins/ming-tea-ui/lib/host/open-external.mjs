// 用**系统默认浏览器**打开授权页。
//
// 为什么必须由宿主打开、而不是页面里 window.open：
// 1. 站点会话在用户日常用的浏览器里；在嵌入式 webview/IAB 里打开，用户多半没登录站点，
//    授权页也就拿不到「已登录 → 自动授权」这条捷径。
// 2. 页面里的 window.open 会被弹窗拦截（尤其是 await 过网络请求之后再调用）。
//
// 纯 ESM + 注入 spawn，便于离线断言命令选择（见 scripts/check_ming_tea_hub.mjs）。

import { spawn } from "node:child_process";

/** 平台 → 打开外链的命令与参数。 */
export function openerCommand(platform = process.platform) {
  if (platform === "darwin") return { command: "open", args: [] };
  if (platform === "win32") return { command: "cmd", args: ["/c", "start", ""] };
  return { command: "xdg-open", args: [] };
}

/**
 * 尽力打开；失败只如实返回错误，不抛（打开失败不该让登录流程崩掉——界面会退回手动链接）。
 * @returns {{ok: boolean, command?: string, error?: string}}
 */
export function openInSystemBrowser(url, { platform = process.platform, spawnImpl = spawn } = {}) {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
    return { ok: false, error: "只能打开 http(s) 链接" };
  }
  const { command, args } = openerCommand(platform);
  try {
    const child = spawnImpl(command, [...args, url], { detached: true, stdio: "ignore" });
    child.on?.("error", () => {});
    child.unref?.();
    return { ok: true, command };
  } catch (error) {
    return { ok: false, error: String(error?.message ?? error) };
  }
}
