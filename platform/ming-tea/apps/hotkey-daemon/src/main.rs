use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{mpsc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use global_hotkey::hotkey::{Code, HotKey, Modifiers};
use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use log::{error, info, warn, Level, LevelFilter, Log, Metadata, Record};
use serde::Deserialize;

const DEFAULT_HOTKEY: &str = "alt+space";

/// 主键被别的应用占用时依次尝试的备用键。
/// 为什么要有它：⌥Space 常被启动器（Raycast/Alfred 之类）占着，
/// 而「注册失败」在用户看来就是「按了没反应」——退到一个能用的键并**明确记录哪个生效**更实在。
const FALLBACK_HOTKEYS: [&str; 2] = ["ctrl+alt+space", "cmd+shift+space"];

/// macOS 的 Carbon 全局热键靠 **run loop** 派发：不跑 run loop，按键永远不会到达回调
/// （global-hotkey 的每个官方示例都在 tao/winit 事件循环里泵消息，2026-10-01 实测踩到：
/// 直接阻塞在 channel 上时，日志里连一条「触发」都没有）。
#[cfg(target_os = "macos")]
mod run_loop {
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        pub fn CFRunLoopRun();
    }
}

#[cfg(not(target_os = "macos"))]
mod run_loop {
    pub fn CFRunLoopRun() {}
}

/// `hotkey` 允许写成一个字符串，或一组候选（按顺序试直到注册成功）。
#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum HotkeySetting {
    One(String),
    Many(Vec<String>),
}

#[derive(Debug, Deserialize)]
struct Settings {
    hotkey: Option<HotkeySetting>,
    /// 触发时执行的命令。留空时走生产路径 `open mingtea://summon`；
    /// 开发/测试时可以填「可执行文件 + 参数」，这样不必先把 .app 打包并注册 URL scheme。
    command: Option<String>,
}

struct DualLogger {
    file: Mutex<File>,
}

impl Log for DualLogger {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        metadata.level() <= Level::Info
    }

    fn log(&self, record: &Record<'_>) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let line = format!("[{now}] {:<5} {}\n", record.level(), record.args());
        if let Ok(mut file) = self.file.lock() {
            let _ = file.write_all(line.as_bytes());
            let _ = file.flush();
        }
        let _ = std::io::stderr().write_all(line.as_bytes());
    }

    fn flush(&self) {}
}

fn app_config_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Library/Application Support/铭荼")
    } else {
        dirs::config_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("铭荼")
    }
}

fn settings_path() -> PathBuf {
    std::env::var_os("MING_TEA_SETTINGS")
        .map(PathBuf::from)
        .unwrap_or_else(|| app_config_dir().join("settings.json"))
}

fn log_path() -> PathBuf {
    if cfg!(target_os = "macos") {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Library/Logs/铭荼/hotkey.log")
    } else {
        app_config_dir().join("hotkey.log")
    }
}

fn init_logging() -> Result<(), String> {
    let path = log_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("cannot create log directory {}: {e}", parent.display()))?;
    }
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("cannot open log {}: {e}", path.display()))?;
    let logger: &'static DualLogger = Box::leak(Box::new(DualLogger { file: Mutex::new(file) }));
    log::set_logger(logger)
        .map_err(|e| format!("cannot install logger: {e}"))?;
    log::set_max_level(LevelFilter::Info);
    Ok(())
}

fn read_settings(path: &Path) -> (Vec<String>, Option<String>) {
    let fallback = (vec![DEFAULT_HOTKEY.to_string()], None);
    match fs::read_to_string(path) {
        Ok(contents) => match serde_json::from_str::<Settings>(&contents) {
            Ok(settings) => {
                // `hotkey` 可以是字符串，也可以是候选数组（按顺序试）
                let preferred: Vec<String> = match settings.hotkey {
                    Some(HotkeySetting::One(text)) if !text.trim().is_empty() => vec![text],
                    Some(HotkeySetting::Many(list)) => list.into_iter().filter(|s| !s.trim().is_empty()).collect(),
                    _ => vec![DEFAULT_HOTKEY.to_string()],
                };
                // 用户没写候选时，自动补上内置备用键（被占用也不至于整个功能不可用）
                let mut candidates = preferred;
                if candidates.len() == 1 {
                    for extra in FALLBACK_HOTKEYS {
                        if !candidates.iter().any(|c| c == extra) {
                            candidates.push(extra.to_string());
                        }
                    }
                }
                (candidates, settings.command.filter(|s| !s.trim().is_empty()))
            }
            Err(e) => {
                warn!("读取设置失败 {}: {e}; 使用默认快捷键 {DEFAULT_HOTKEY}", path.display());
                fallback
            }
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            info!("设置文件不存在 {}; 使用默认快捷键 {DEFAULT_HOTKEY}", path.display());
            fallback
        }
        Err(e) => {
            warn!("读取设置失败 {}: {e}; 使用默认快捷键 {DEFAULT_HOTKEY}", path.display());
            fallback
        }
    }
}

fn parse_hotkey(input: &str) -> Result<HotKey, String> {
    let parts: Vec<_> = input.split('+').map(|p| p.trim()).filter(|p| !p.is_empty()).collect();
    if parts.len() < 2 {
        return Err("需要至少一个修饰键和一个主键".into());
    }
    let mut modifiers = Modifiers::empty();
    for part in &parts[..parts.len() - 1] {
        match part.to_ascii_lowercase().as_str() {
            "alt" | "option" => modifiers |= Modifiers::ALT,
            "ctrl" | "control" => modifiers |= Modifiers::CONTROL,
            "shift" => modifiers |= Modifiers::SHIFT,
            "cmd" | "command" | "super" | "meta" => modifiers |= Modifiers::META,
            other => return Err(format!("未知修饰键 {other}")),
        }
    }
    let key = parts[parts.len() - 1];
    let normalized = key.to_ascii_lowercase();
    let code = match normalized.as_str() {
        "space" => Code::Space,
        "a" => Code::KeyA, "b" => Code::KeyB, "c" => Code::KeyC, "d" => Code::KeyD,
        "e" => Code::KeyE, "f" => Code::KeyF, "g" => Code::KeyG, "h" => Code::KeyH,
        "i" => Code::KeyI, "j" => Code::KeyJ, "k" => Code::KeyK, "l" => Code::KeyL,
        "m" => Code::KeyM, "n" => Code::KeyN, "o" => Code::KeyO, "p" => Code::KeyP,
        "q" => Code::KeyQ, "r" => Code::KeyR, "s" => Code::KeyS, "t" => Code::KeyT,
        "u" => Code::KeyU, "v" => Code::KeyV, "w" => Code::KeyW, "x" => Code::KeyX,
        "y" => Code::KeyY, "z" => Code::KeyZ,
        "0" => Code::Digit0, "1" => Code::Digit1, "2" => Code::Digit2, "3" => Code::Digit3,
        "4" => Code::Digit4, "5" => Code::Digit5, "6" => Code::Digit6, "7" => Code::Digit7,
        "8" => Code::Digit8, "9" => Code::Digit9,
        "f1" => Code::F1, "f2" => Code::F2, "f3" => Code::F3, "f4" => Code::F4,
        "f5" => Code::F5, "f6" => Code::F6, "f7" => Code::F7, "f8" => Code::F8,
        "f9" => Code::F9, "f10" => Code::F10, "f11" => Code::F11, "f12" => Code::F12,
        other => return Err(format!("不支持的主键 {other}")),
    };
    Ok(HotKey::new(Some(modifiers), code))
}

/// 把生效的热键写进 `hotkey-state.json`（应用侧读给用户看「到底哪个键生效」）。
fn write_state(active: &str, candidates: &[String]) {
    // 就写在铭荼自己的配置目录里（app_config_dir() 本身就是 ~/Library/Application Support/铭荼）
    let path = app_config_dir().join("hotkey-state.json");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let payload = format!(
        "{{\n  \"active\": {},\n  \"configured\": {},\n  \"pid\": {}\n}}\n",
        serde_json::to_string(active).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(candidates).unwrap_or_else(|_| "[]".into()),
        std::process::id()
    );
    let _ = std::fs::write(path, payload);
}

fn main() {
    if let Err(e) = init_logging() {
        eprintln!("初始化日志失败: {e}");
        return;
    }
    let settings = settings_path();
    let (candidates, custom_command) = read_settings(&settings);
    let parsed: Vec<(String, HotKey)> = candidates
        .iter()
        .filter_map(|text| match parse_hotkey(text) {
            Ok(key) => Some((text.clone(), key)),
            Err(e) => {
                warn!("快捷键 {text:?} 解析失败，跳过：{e}");
                None
            }
        })
        .collect();
    if parsed.is_empty() {
        error!("没有任何可用的快捷键候选，退出");
        return;
    }
    let manager = match GlobalHotKeyManager::new() {
        Ok(manager) => manager,
        Err(e) => {
            error!("创建全局快捷键管理器失败: {e}");
            return;
        }
    };
    let (stop_tx, stop_rx) = mpsc::channel();
    if let Err(e) = ctrlc::set_handler(move || { let _ = stop_tx.send(()); }) {
        warn!("安装 SIGINT/SIGTERM 处理器失败: {e}");
    }
    let receiver = GlobalHotKeyEvent::receiver();

    // 按候选顺序注册：第一个成功的生效；全失败则 30 秒后整体重试。
    let (hotkey_text, hotkey) = loop {
        let mut winner: Option<(String, HotKey)> = None;
        for (text, key) in &parsed {
            match manager.register(*key) {
                Ok(()) => {
                    info!("已注册 {text}");
                    winner = Some((text.clone(), *key));
                    break;
                }
                Err(e) => warn!("注册 {text} 失败（可能被其它应用占用）：{e}"),
            }
        }
        if let Some(found) = winner {
            break found;
        }
        error!("所有候选快捷键都注册失败；30 秒后重试");
        if stop_rx.recv_timeout(Duration::from_secs(30)).is_ok() {
            info!("收到退出信号，已退出");
            return;
        }
    };
    // 把「当前真正生效的键」落盘，应用侧可读给用户看
    write_state(&hotkey_text, &candidates);

    // 事件处理放工作线程：主线程要留给 run loop（见 run_loop 模块的说明）
    std::thread::spawn(move || loop {
        if stop_rx.try_recv().is_ok() {
            info!("收到退出信号，已退出");
            std::process::exit(0);
        }
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(event) if event.id() == hotkey.id() && event.state() == HotKeyState::Pressed => {
                info!("触发 {hotkey_text}");
                if let Some(command) = custom_command.as_deref() {
                    // 开发路径：settings.json 里显式给了命令，直接跑它
                    match Command::new("/bin/sh").arg("-c").arg(command).spawn() {
                        Ok(_) => info!("已执行自定义命令: {command}"),
                        Err(e) => error!("执行自定义命令失败: {e}"),
                    }
                } else {
                    #[cfg(target_os = "macos")]
                    match Command::new("open").arg("mingtea://summon").spawn() {
                        Ok(_) => info!("已执行 open mingtea://summon"),
                        Err(e) => error!("执行 open mingtea://summon 失败: {e}"),
                    }
                    #[cfg(not(target_os = "macos"))]
                    info!("非 macOS 平台且未配置自定义命令，仅记录触发");
                }
            }
            // 诊断（2026-10-02 加）：**任何**到达的热键事件都留一行痕迹。
            // 因为「按了没反应」有三种完全不同的成因，没有这行就分不开：
            //   ① 日志里连事件行都没有 → 键根本没到我们手里（被别的应用抢了，或注册无效）；
            //   ② 有事件行但 id/state 不匹配 → 是我们自己的 bug（注册 id 与事件 id 对不上）；
            //   ③ 有「触发」→ 键到了，问题在后面的 open/deep link。
            // 实测背景：`⌥Space` 从未在日志里留下过「触发」，而当时无法区分 ①②。
            Ok(event) => {
                info!(
                    "收到热键事件 id={}（期望 {}）state={:?} —— 未匹配，未执行动作",
                    event.id(),
                    hotkey.id(),
                    event.state()
                );
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {}
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                error!("全局快捷键事件通道已断开");
                std::process::exit(1);
            }
        }
    });

    info!("进入事件循环（等待快捷键）");
    // SAFETY: CFRunLoopRun 在当前线程上跑事件循环；这是 Carbon 热键回调派发的前提
    unsafe { run_loop::CFRunLoopRun() };
    info!("事件循环已退出");
}
