//! 把壳的 stderr 落到文件 —— 图形方式启动的应用，stderr **无处可去**。
//!
//! 为什么必须做（2026-10-02 实测）：用 `open mingtea://summon`（= 热键守护进程做的事）冷启动应用后，
//! `log show --predicate 'process == "ming-tea-desktop"'` 里**一条 `[setup]`/`[summon]` 都没有**
//! （只有 WebKit 的噪音），于是「冷启动到底弹了哪个窗口」这类问题既没法验证、用户也拿不到现场。
//!
//! 做法：启动早期把 stderr 直接指向日志文件（追加）。
//! 一行 `dup2` 就能把之后**所有**输出（我们自己的 eprintln、panic、依赖库的警告）都留下，
//! 不必逐个改 `eprintln!`。文件超过 [`MAX_LOG_BYTES`] 就在启动时重开一次（简易轮转，不无限长）。
//!
//! 平台差异：
//! - macOS：`~/Library/Logs/铭荼/app.log`（与热键守护进程的 hotkey.log 同目录）；
//! - Linux：`${XDG_STATE_HOME:-~/.local/state}/ming-tea/app.log`（XDG 惯例，不写进用户家目录根部）；
//! - Windows：`%APPDATA%\ming-tea\app.log`（`dirs::data_dir`）。

use std::fs::{create_dir_all, metadata, remove_file, OpenOptions};
use std::path::PathBuf;

/// 超过这个大小就在下次启动时重开（不做精细轮转：桌面应用的启动日志不需要历史归档）。
const MAX_LOG_BYTES: u64 = 2 * 1024 * 1024;

pub fn log_file_path() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        // 与热键守护进程同一个目录（它写 hotkey.log），用户只需记住一个地方。
        Some(dirs::home_dir()?.join("Library/Logs/铭荼/app.log"))
    }
    #[cfg(target_os = "linux")]
    {
        let base = std::env::var("XDG_STATE_HOME")
            .ok()
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .or_else(|| dirs::home_dir().map(|home| home.join(".local/state")))?;
        Some(base.join("ming-tea/app.log"))
    }
    #[cfg(target_os = "windows")]
    {
        Some(dirs::data_dir()?.join("ming-tea").join("app.log"))
    }
}

/// 当前本地时间（`YYYY-MM-DD HH:MM:SS`）。用 libc 而不是引入 chrono —— 只为一个时间戳不值得。
#[cfg(unix)]
fn local_timestamp() -> String {
    // SAFETY: time/localtime_r/strftime 都是只读或写我们自己的栈上缓冲；
    // 缓冲区长度足够（`%Y-%m-%d %H:%M:%S` 最多 19 字符 + NUL）。
    unsafe {
        let mut now: libc::time_t = 0;
        libc::time(&mut now);
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&now, &mut tm).is_null() {
            return format!("epoch={now}");
        }
        let mut buffer = [0i8; 64];
        let format = c"%Y-%m-%d %H:%M:%S";
        if libc::strftime(buffer.as_mut_ptr(), buffer.len(), format.as_ptr(), &tm) == 0 {
            return format!("epoch={now}");
        }
        std::ffi::CStr::from_ptr(buffer.as_ptr()).to_string_lossy().into_owned()
    }
}

/// Windows 没有 localtime_r：`chrono` 就为一个时间戳不值得引，直接用标准库的秒级时间。
#[cfg(not(unix))]
fn local_timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    format!("epoch={now}")
}

/// 把 stderr 指向日志文件，并写一行启动标记。失败时静默退回 stderr（不能因为日志写不了就起不来）。
#[cfg(unix)]
pub fn redirect_stderr_to_file() {
    redirect_unix();
    eprintln!(
        "=== 铭荼 {} 启动（pid {}，日志 {}）===",
        local_timestamp(),
        std::process::id(),
        log_file_path().map(|path| path.display().to_string()).unwrap_or_default()
    );
}

#[cfg(not(unix))]
pub fn redirect_stderr_to_file() {
    // Windows 的 GUI 子系统没有 stderr 可重定向，Tauri 也不走 LaunchServices；
    // 保留 Unix 的文件日志语义留待后续用官方 tracing 方案，先不引入额外的 crate。
    eprintln!(
        "=== 铭荼 {} 启动（pid {}）===",
        local_timestamp(),
        std::process::id()
    );
}

#[cfg(unix)]
fn redirect_unix() {
    let Some(path) = log_file_path() else { return };
    if let Some(directory) = path.parent() {
        let _ = create_dir_all(directory);
    }
    if metadata(&path).map(|meta| meta.len() > MAX_LOG_BYTES).unwrap_or(false) {
        let _ = remove_file(&path);
    }
    let Ok(file) = OpenOptions::new().create(true).append(true).open(&path) else { return };
    use std::os::unix::io::AsRawFd;
    let descriptor = file.as_raw_fd();
    // SAFETY: dup2 把 fd 2 复制成这个文件的描述符。之后 file 被 forget：这个描述符要活到进程结束，
    // 由操作系统在退出时回收（写成 drop 会立刻关掉 fd，日志就没了）。
    unsafe {
        libc::dup2(descriptor, 2);
    }
    std::mem::forget(file);
}
