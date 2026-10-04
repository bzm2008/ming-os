//! 热键守护进程的随登录启动管理 —— **平台分发层**。
//!
//! 为什么需要守护进程：**进程完全退出后，任何快捷键都不可能生效**。
//! 所以热键由一个极小的常驻二进制持有（`platform/ming-tea/apps/hotkey-daemon`，
//! 用 global-hotkey 注册；global-hotkey 0.8 三平台都支持：macOS 走 Carbon、
//! Linux 走 x11rb、Windows 走 windows-sys）。
//!
//! 「随登录启动」的机制每个平台不同，本文件只做分发：
//! - macOS：`launch_agent_macos.rs` 的 LaunchAgent（plist + launchctl bootstrap）；
//! - Linux：ming-os 的桌面自启动（`/etc/xdg/autostart`，由系统构建的
//!   `modules/03_desktop.sh` 安装），运行期壳只**报告**状态、不重复安装；
//! - Windows：登录启动项（HKCU Run 键），由安装器/CI 打包阶段处理；
//!   运行期同样只报告。
//!
//! 非 macOS 的实现刻意**如实**：`installed/loaded` 反映真实状态而不是假装成功，
//! `detail` 说明由哪一层负责 —— 用户界面不能显示假信息。

use std::path::PathBuf;

/// 读守护进程落盘的生效热键（三个平台语义相同：state 文件由守护进程写）。
/// macOS 的 platform 模块 include 自带同名实现，此版本只服务非 macOS 平台。
#[cfg(not(target_os = "macos"))]
fn active_hotkey() -> Option<String> {
    let path = dirs::data_dir()?.join("铭荼").join("hotkey-state.json");
    let text = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value.get("active")?.as_str().map(|s| s.to_string())
}

/// 守护进程二进制的位置（三平台共用的查找顺序）：
/// 1. 环境变量 `MING_TEA_HOTKEY_BIN`（开发/CI 用）；
/// 2. 与主程序同目录的 `ming-tea-hotkey`（打包后 externalBin 就落在旁边）；
/// 3. 仓库里 `cargo build` 的 debug 产物（开发兜底）。
pub fn resolve_helper() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MING_TEA_HOTKEY_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let binary = if cfg!(target_os = "windows") {
                dir.join("ming-tea-hotkey.exe")
            } else {
                dir.join("ming-tea-hotkey")
            };
            if binary.is_file() {
                return Some(binary);
            }
        }
    }
    let mut found: Option<PathBuf> = None;
    for root in candidate_repo_roots() {
        for profile in ["release", "debug"] {
            let candidate = root
                .join("platform/ming-tea/apps/hotkey-daemon/target")
                .join(profile)
                .join("ming-tea-hotkey");
            if candidate.is_file() {
                found = Some(candidate);
                break;
            }
        }
        if found.is_some() {
            break;
        }
    }
    found
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

#[cfg(target_os = "macos")]
mod platform {
    // macOS 原实现（LaunchAgent）整体 include 进来，直接作为平台实现。
    // LaunchAgentStatus 定义在外层（平台无关层），这里 pub use 让平台模块重导出它。
    use super::LaunchAgentStatus;
    include!("launch_agent_macos.rs");
}

#[cfg(not(target_os = "macos"))]
mod platform {
    use super::{active_hotkey, resolve_helper, LaunchAgentStatus};
    use std::path::PathBuf;
    fn autostart_marker() -> Option<PathBuf> {
        if cfg!(target_os = "linux") {
            let system = PathBuf::from("/etc/xdg/autostart/ming-tea.desktop");
            if system.is_file() {
                return Some(system);
            }
            let user = dirs::home_dir()?.join(".config/autostart/ming-tea.desktop");
            if user.is_file() {
                return Some(user);
            }
            None
        } else {
            // Windows 的 Run 键这里读不到进程外的注册表（不引入 winreg），
            // 如实报告「由安装器负责」。
            None
        }
    }

    pub fn installed_helper_path() -> Option<PathBuf> {
        // 非 macOS 没有记录 helper 路径的 plist；返回 None 表示「无从核对」，
        // 调用方（install_hotkey_daemon）因此不会做 macOS 那种重写逻辑。
        None
    }

    pub fn status() -> LaunchAgentStatus {
        let marker = autostart_marker();
        let helper = resolve_helper().map(|p| p.display().to_string());
        let (installed, detail) = match marker {
            Some(path) => (true, format!("随登录启动（{}）", path.display())),
            None if cfg!(target_os = "linux") => (
                false,
                "尚未随登录启动：ming-tea.desktop 应由系统构建安装到 /etc/xdg/autostart".to_string(),
            ),
            None => (
                false,
                "随登录启动由 Windows 安装器负责（HKCU Run）；运行期不重复安装".to_string(),
            ),
        };
        LaunchAgentStatus {
            installed,
            loaded: installed,
            helper,
            active_hotkey: active_hotkey(),
            detail,
        }
    }

    pub fn install() -> Result<LaunchAgentStatus, String> {
        Err(if cfg!(target_os = "linux") {
            "Linux 的随登录启动由 ming-os 系统构建安装（/etc/xdg/autostart/ming-tea.desktop）；运行期不支持重复安装".to_string()
        } else {
            "Windows 的随登录启动由安装器写入 HKCU Run；运行期不支持重复安装".to_string()
        })
    }

    pub fn uninstall() -> Result<LaunchAgentStatus, String> {
        Err("随登录启动由系统层管理，运行期不修改".to_string())
    }
}

pub use platform::{install, installed_helper_path, status, uninstall};
// LaunchAgentStatus 定义在本文件的平台无关层（非 macOS 的 platform 模块与 macOS 实现
// 都返回它），不需要从 platform 重导出。
