//! 热键守护进程的随登录启动管理（LaunchAgent）。
//!
//! 为什么需要守护进程：**进程完全退出后，任何快捷键都不可能生效**。
//! 所以热键由一个极小的常驻二进制持有（`platform/ming-tea/apps/hotkey-daemon`，
//! 用 global-hotkey 注册、按下时执行 `open "mingtea://summon"`）。它不申请任何 TCC 权限：
//! Carbon 全局热键不需要辅助功能权限。
//!
//! 这个模块只做三件事：写 plist、bootstrap/bootout、查状态。

use std::path::PathBuf;
use std::process::Command;

const LABEL: &str = "cn.mingos.mingtea.hotkey";

fn plist_path() -> Option<PathBuf> {
    dirs::home_dir().map(|h| h.join("Library/LaunchAgents").join(format!("{LABEL}.plist")))
}

/// 守护进程二进制的位置：
/// 1. 环境变量 `MING_TEA_HOTKEY_BIN`（开发时用）；
/// 2. app bundle 内的 `Contents/MacOS/ming-tea-hotkey`（打包后与主程序同目录）；
/// 3. 仓库里 `cargo build` 的 debug 产物（开发兜底）。
pub fn resolve_helper() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MING_TEA_HOTKEY_BIN") {
        let p = PathBuf::from(explicit);
        if p.is_file() {
            return Some(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sibling = dir.join("ming-tea-hotkey");
            if sibling.is_file() {
                return Some(sibling);
            }
        }
    }
    for root in candidate_repo_roots() {
        for profile in ["debug", "release"] {
            let candidate = root
                .join("platform/ming-tea/apps/hotkey-daemon/target")
                .join(profile)
                .join("ming-tea-hotkey");
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn candidate_repo_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(explicit) = std::env::var("MING_TEA_REPO_ROOT") {
        roots.push(PathBuf::from(explicit));
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..8 {
            let Some(current) = dir else { break };
            if current.join(".ming-tea").is_dir() {
                roots.push(current);
                break;
            }
            dir = current.parent().map(|p| p.to_path_buf());
        }
    }
    roots
}

#[derive(Clone, serde::Serialize)]
pub struct LaunchAgentStatus {
    pub installed: bool,
    pub loaded: bool,
    pub helper: Option<String>,
    /// 守护进程实际注册成功的那个键（读它写的 hotkey-state.json）：
    /// 首选键被别的应用占用时会退到备用键，界面据此告诉用户「按哪个键」。
    pub active_hotkey: Option<String>,
    pub detail: String,
}

/// 读守护进程落盘的生效热键。
fn active_hotkey() -> Option<String> {
    let path = dirs::data_dir()?.join("铭荼").join("hotkey-state.json");
    let text = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value.get("active")?.as_str().map(|s| s.to_string())
}

/// 读 plist 里**实际写着**的 helper 路径（用于判断是否需要重写：
/// 例如从「开发机 repo 里的二进制」换成「.app 包内那份」）。
pub fn installed_helper_path() -> Option<PathBuf> {
    let path = plist_path()?;
    let text = std::fs::read_to_string(path).ok()?;
    // 只取 ProgramArguments 里的第一个 <string>：plist 很小，不值得引入解析库
    let start = text.find("<key>ProgramArguments</key>")?;
    let rest = &text[start..];
    let open = rest.find("<string>")? + "<string>".len();
    let close = rest[open..].find("</string>")? + open;
    Some(PathBuf::from(rest[open..close].trim()))
}

pub fn status() -> LaunchAgentStatus {
    let path = plist_path();
    let installed = path.as_ref().map(|p| p.is_file()).unwrap_or(false);
    let loaded = Command::new("launchctl")
        .arg("list")
        .arg(LABEL)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    let helper = resolve_helper().map(|p| p.display().to_string());
    let detail = match (&path, installed) {
        (Some(p), true) => format!("plist: {}", p.display()),
        (Some(p), false) => format!("尚未安装（将写入 {}）", p.display()),
        (None, _) => "找不到用户主目录".to_string(),
    };
    LaunchAgentStatus { installed, loaded, helper, active_hotkey: active_hotkey(), detail }
}

/// 安装（幂等）：写 plist → bootout 旧的（忽略失败）→ bootstrap。
pub fn install() -> Result<LaunchAgentStatus, String> {
    let helper = resolve_helper().ok_or_else(|| {
        "找不到热键守护进程二进制：先构建 platform/ming-tea/apps/hotkey-daemon，或设置 MING_TEA_HOTKEY_BIN"
            .to_string()
    })?;
    let path = plist_path().ok_or_else(|| "找不到用户主目录".to_string())?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    let logs = dirs::home_dir()
        .map(|h| h.join("Library/Logs/铭荼"))
        .ok_or_else(|| "找不到用户主目录".to_string())?;
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;

    let plist = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{helper}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{out}</string>
  <key>StandardErrorPath</key><string>{err}</string>
</dict>
</plist>
"#,
        helper = helper.display(),
        out = logs.join("hotkey.out.log").display(),
        err = logs.join("hotkey.err.log").display(),
    );
    std::fs::write(&path, plist).map_err(|e| e.to_string())?;

    let uid = unsafe { libc_getuid() };
    let domain = format!("gui/{uid}");
    // 先卸掉旧的（不存在时会失败，忽略）；再 bootstrap 新的。
    let _ = Command::new("launchctl").arg("bootout").arg(&domain).arg(&path).output();
    let out = Command::new("launchctl")
        .arg("bootstrap")
        .arg(&domain)
        .arg(&path)
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!(
            "launchctl bootstrap 失败：{}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(status())
}

pub fn uninstall() -> Result<LaunchAgentStatus, String> {
    if let Some(path) = plist_path() {
        let uid = unsafe { libc_getuid() };
        let domain = format!("gui/{uid}");
        let _ = Command::new("launchctl").arg("bootout").arg(&domain).arg(&path).output();
        if path.is_file() {
            std::fs::remove_file(&path).map_err(|e| e.to_string())?;
        }
    }
    Ok(status())
}

// 只需要 getuid 一个系统调用，为它引入 libc 依赖不值得。
extern "C" {
    #[link_name = "getuid"]
    fn libc_getuid() -> u32;
}
