//! DSH host 的生命周期管理。
//!
//! 桌面壳自己不实现 agent：真正的脑子是 `dsh --profile ming-tea` 起的本地 web host。
//! 这个模块负责把它拉起来、解析出带 token 的 loopback URL、监督它、并在退出时关掉它。
//!
//! 为什么面板与主窗口都加载这个 URL，而不是做一个私有 IPC 协议：
//! 宠物插件、本地 SenseVoice 语音、审批 UI、模型路由、电脑操作工具**全都长在 DSH web 层**，
//! 复用它比在壳里重写一遍省得多（详见 docs/ming-tea-voice-summon.md）。

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// 当前 host 子进程的 pid（0 = 没有）。信号处理器只能读这个原子值，
/// 不能碰 Mutex —— 见 `install_signal_handlers` 的说明。
static CHILD_PID: AtomicI32 = AtomicI32::new(0);

pub fn current_child_pid() -> i32 {
    CHILD_PID.load(Ordering::SeqCst)
}

#[cfg(unix)]
mod signal_ffi {
    extern "C" {
        pub fn signal(signum: i32, handler: usize) -> usize;
        pub fn kill(pid: i32, sig: i32) -> i32;
        pub fn _exit(code: i32) -> !;
    }
}

/// 应用被 SIGTERM/SIGINT 杀掉时（例如 `pkill`、系统退出），把 host 一起带走。
///
/// 为什么必须做：Rust/Tauri 默认不装信号处理器，进程直接被终止时
/// `RunEvent::Exit` 不会触发，实测会留下一个还在监听的 dsh 孤儿进程占着 profile。
#[cfg(unix)]
pub fn install_signal_handlers() {
    extern "C" fn on_signal(_sig: i32) {
        // 只需两个 async-signal-safe 的调用：kill 子进程 + _exit 自己
        let pid = CHILD_PID.load(Ordering::SeqCst);
        unsafe {
            if pid > 0 {
                signal_ffi::kill(pid, 15);
            }
            signal_ffi::_exit(0);
        }
    }
    unsafe {
        signal_ffi::signal(15, on_signal as usize); // SIGTERM
        signal_ffi::signal(2, on_signal as usize); // SIGINT
    }
}

#[cfg(not(unix))]
pub fn install_signal_handlers() {}

/// pidfile：崩溃/被杀之后，下次启动能认出并清掉上一次留下的 host。
fn pidfile_path() -> Option<PathBuf> {
    dirs::data_dir().map(|d| d.join("铭荼").join("dsh-host.json"))
}

#[derive(serde::Serialize, serde::Deserialize)]
struct HostRecord {
    pid: i32,
    port: u16,
    url: String,
}

fn write_pidfile(pid: i32, port: u16, url: &str) {
    let Some(path) = pidfile_path() else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let record = HostRecord { pid, port, url: url.to_string() };
    if let Ok(text) = serde_json::to_string_pretty(&record) {
        let _ = std::fs::write(path, text);
    }
}

fn clear_pidfile() {
    if let Some(path) = pidfile_path() {
        let _ = std::fs::remove_file(path);
    }
}

/// 启动时清理上一次残留的 host（应用被强杀、崩溃时留下的）。
pub fn cleanup_stale_host() {
    let Some(path) = pidfile_path() else { return };
    let Ok(text) = std::fs::read_to_string(&path) else { return };
    let Ok(record) = serde_json::from_str::<HostRecord>(&text) else {
        let _ = std::fs::remove_file(&path);
        return;
    };
    if record.pid > 0 && process_alive(record.pid) {
        eprintln!(
            "[dsh] 发现上次残留的 host（pid={}，端口={}），先关掉它",
            record.pid, record.port
        );
        terminate_pid(record.pid);
    }
    clear_pidfile();
}

fn process_alive(pid: i32) -> bool {
    #[cfg(unix)]
    {
        // kill(pid, 0) 只做存在性检查，不发送信号
        unsafe { signal_ffi::kill(pid, 0) == 0 }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        false
    }
}

/// 启动失败/超时：把登记清掉，免得下次启动去「清理」一个已经没了的 pid。
fn forget_child() {
    CHILD_PID.store(0, Ordering::SeqCst);
    clear_pidfile();
}

fn terminate_pid(pid: i32) {
    #[cfg(unix)]
    {
        unsafe {
            signal_ffi::kill(pid, 15);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if !process_alive(pid) {
                return;
            }
            thread::sleep(Duration::from_millis(200));
        }
        unsafe {
            signal_ffi::kill(pid, 9);
        }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
    }
}

#[derive(Clone, serde::Serialize)]
pub struct DshStatus {
    pub running: bool,
    pub url: Option<String>,
    pub port: Option<u16>,
    pub detail: String,
}

struct Inner {
    child: Option<Child>,
    url: Option<String>,
    port: Option<u16>,
    last_error: Option<String>,
}

pub struct DshHost {
    inner: Mutex<Inner>,
    /// 单飞锁：`ensure_started` 的「检查 → spawn → 等 URL」必须整体串行，
    /// 否则并发调用（召唤路径与主窗口路径同时触发）会各起一个 host。
    /// 2026-10-01 实测踩到：日志里出现两条 `host 就绪`，两个 host 抢同一个 DSH_HOME。
    single_flight: Mutex<()>,
    log: Arc<Mutex<Vec<String>>>,
}

fn push_line(log: &Arc<Mutex<Vec<String>>>, line: String) {
    if let Ok(mut buf) = log.lock() {
        buf.push(line);
        let len = buf.len();
        if len > 400 {
            buf.drain(0..len - 400); // 长跑进程只留尾部，避免无限增长
        }
    }
}

/// 推导 DSH_HOME（profile、会话、凭证都在它下面）。
///
/// 这是**必须显式传**的：dsh 默认用 `~/.dsh`，而铭荼的 profile 在自己的 home 里
/// （开发时是 `<repo>/.ming-tea/runtime/dsh-home`）。2026-10-01 实测漏掉它就会报
/// `profile "ming-tea" does not exist`，然后 40s 后才以「启动超时」冒出来。
///
/// 优先从 dsh 可执行文件的位置反推（`<runtime>/node_modules/.bin/dsh` → `<runtime>/dsh-home`），
/// 因为那是最可靠的：跟着 dsh 走，不会和仓库路径假设打架。
fn resolve_dsh_home(dsh: &PathBuf) -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MING_TEA_DSH_HOME") {
        let p = PathBuf::from(explicit);
        if p.is_dir() {
            return Some(p);
        }
    }
    // <runtime>/node_modules/.bin/dsh → 上溯三层拿到 <runtime>
    let runtime = dsh.parent()?.parent()?.parent()?;
    let candidate = runtime.join("dsh-home");
    if candidate.is_dir() {
        return Some(candidate);
    }
    for root in repo_roots() {
        let fallback = root.join(".ming-tea/runtime/dsh-home");
        if fallback.is_dir() {
            return Some(fallback);
        }
    }
    None
}

/// 找一个空闲的 loopback 端口。取到后立刻释放，交给 dsh 去绑——
/// 中间有极小的竞态窗口，但比固定端口撞车（本机已经跑着 19387/3080）好得多。
fn free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

/// 从 `settings.json` 读一个字段（守护进程读同一份文件里的 hotkey）。
/// 为什么要它：应用被 LaunchServices（Dock / `open mingtea://summon`）拉起时**不继承 shell 环境**，
/// `MING_TEA_REPO_ROOT` 这类变量拿不到；而 runtime 目前还没打进 .app，
/// 所以要让用户能在设置里写清 runtime 在哪。
fn settings_field(key: &str) -> Option<String> {
    let path = dirs::data_dir()?.join("铭荼").join("settings.json");
    let text = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value.get(key)?.as_str().map(|s| s.to_string()).filter(|s| !s.is_empty())
}

/// dsh 可执行文件的解析顺序：
/// 环境变量 → settings.json 的 `dshBin` → settings.json 的 `repoRoot` → 仓库 runtime → 未来随包资源。
pub fn resolve_dsh() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MING_TEA_DSH_BIN") {
        let p = PathBuf::from(explicit);
        if p.is_file() {
            return Some(p);
        }
    }
    if let Some(bin) = settings_field("dshBin") {
        let p = PathBuf::from(bin);
        if p.is_file() {
            return Some(p);
        }
        eprintln!("[dsh] settings.json 里的 dshBin 不存在：{}", p.display());
    }
    if let Some(root) = settings_field("repoRoot") {
        let candidate = PathBuf::from(root).join(".ming-tea/runtime/node_modules/.bin/dsh");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    for root in repo_roots() {
        let candidate = root.join(".ming-tea/runtime/node_modules/.bin/dsh");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn repo_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(explicit) = std::env::var("MING_TEA_REPO_ROOT") {
        roots.push(PathBuf::from(explicit));
    }
    // 开发时 exe 在 <repo>/platform/ming-tea/apps/desktop/src-tauri/target/debug/ 下，
    // 往上找就能碰到仓库根（标志是 .ming-tea/）。做成循环而不是写死层数，目录调整后不会失效。
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..8 {
            let Some(current) = dir else { break };
            if current.join(".ming-tea").is_dir() {
                roots.push(current.clone());
                break;
            }
            dir = current.parent().map(|p| p.to_path_buf());
        }
    }
    roots
}

impl DshHost {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner { child: None, url: None, port: None, last_error: None }),
            single_flight: Mutex::new(()),
            log: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn diagnostics(&self) -> Vec<String> {
        self.log.lock().map(|l| l.clone()).unwrap_or_default()
    }

    /// 读取一行里的 `http://127.0.0.1:<port>/?token=...`。
    /// dsh web 启动后会把这一行打到 stdout（2026-10-01 实测）。
    fn extract_url(line: &str) -> Option<(String, u16)> {
        let idx = line.find("http://127.0.0.1:")?;
        let rest = &line[idx..];
        let end = rest.find(|c: char| c.is_whitespace()).unwrap_or(rest.len());
        let url = rest[..end].to_string();
        let port: u16 = url
            .trim_start_matches("http://127.0.0.1:")
            .split('/')
            .next()
            .and_then(|p| p.parse().ok())?;
        Some((url, port))
    }

    /// 确保 host 在跑（幂等）。返回带 token 的 URL。
    pub fn ensure_started(&self, profile: &str) -> Result<String, String> {
        // 串行化整个「检查/启动/等待」过程；第二个调用者拿到锁后会看到已有 child 并直接复用
        let _flight = self.single_flight.lock().map_err(|e| e.to_string())?;
        {
            let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
            if let Some(child) = inner.child.as_mut() {
                match child.try_wait() {
                    Ok(None) => {
                        if let Some(url) = inner.url.clone() {
                            return Ok(url);
                        }
                    }
                    _ => {
                        inner.child = None;
                        inner.url = None;
                    }
                }
            }
        }

        let dsh = resolve_dsh().ok_or_else(|| {
            "找不到 dsh 可执行文件：请设置 MING_TEA_DSH_BIN，或先运行 scripts/install_ming_tea_plugins.sh"
                .to_string()
        })?;
        let port = free_port().map_err(|e| format!("找不到空闲端口：{e}"))?;
        let dsh_home = resolve_dsh_home(&dsh);

        let mut command = Command::new(&dsh);
        command
            .arg("--profile")
            .arg(profile)
            .arg("--port")
            .arg(port.to_string())
            .arg("--no-open")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(home) = dsh_home.as_ref() {
            command.env("DSH_HOME", home);
        } else {
            eprintln!("[dsh] 警告：推导不出 DSH_HOME，子进程会用 ~/.dsh，profile 很可能找不到");
        }

        let mut child = command
            .spawn()
            .map_err(|e| format!("启动 dsh 失败（{}）：{e}", dsh.display()))?;

        // **spawn 成功就立刻登记**：dsh 首次冷启动要十几到几十秒，如果只在「解析出 URL」之后
        // 才登记，那么启动中途被 SIGTERM/强杀就会留下孤儿（2026-10-01 实测踩到）。
        let spawned_pid = child.id() as i32;
        CHILD_PID.store(spawned_pid, Ordering::SeqCst);
        write_pidfile(spawned_pid, port, "");

        let (tx, rx): (mpsc::Sender<(String, u16)>, Receiver<(String, u16)>) = mpsc::channel();

        if let Some(out) = child.stdout.take() {
            let tx = tx.clone();
            let log = Arc::clone(&self.log);
            thread::spawn(move || {
                for line in BufReader::new(out).lines().map_while(Result::ok) {
                    if let Some((url, port)) = DshHost::extract_url(&line) {
                        let _ = tx.send((url, port));
                    }
                    push_line(&log, format!("[stdout] {line}"));
                }
            });
        }
        if let Some(err) = child.stderr.take() {
            let log = Arc::clone(&self.log);
            thread::spawn(move || {
                for line in BufReader::new(err).lines().map_while(Result::ok) {
                    push_line(&log, format!("[stderr] {line}"));
                }
            });
        }
        drop(tx);

        let deadline = Instant::now() + Duration::from_secs(40);
        let mut found = None;
        while Instant::now() < deadline {
            match rx.recv_timeout(Duration::from_millis(500)) {
                Ok(pair) => {
                    found = Some(pair);
                    break;
                }
                Err(RecvTimeoutError::Timeout) => {
                    if let Ok(Some(status)) = child.try_wait() {
                        let diag = self.diagnostics().join("\n");
                        forget_child();
                        return Err(format!("dsh host 提前退出（{status}）：\n{diag}"));
                    }
                }
                Err(RecvTimeoutError::Disconnected) => {
                    // 输出流关闭 = 子进程结束了；把真实状态带上，别报成「超时」
                    let status = child.try_wait().ok().flatten();
                    let diag = self.diagnostics().join("\n");
                    let message = match status {
                        Some(s) => format!("dsh host 提前退出（{s}）"),
                        None => "dsh host 输出流关闭（进程可能已退出）".to_string(),
                    };
                    if let Ok(mut inner) = self.inner.lock() {
                        inner.last_error = Some(message.clone());
                    }
                    forget_child();
                    return Err(format!("{message}\n{diag}"));
                }
            }
        }

        match found {
            Some((url, port)) => {
                let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
                let pid = child.id() as i32;
                inner.child = Some(child);
                inner.url = Some(url.clone());
                inner.port = Some(port);
                inner.last_error = None;
                // 供信号处理器与下次启动的残留清理使用
                CHILD_PID.store(pid, Ordering::SeqCst);
                write_pidfile(pid, port, &url); // 就绪后用真实 URL 覆盖启动时的占位记录
                // 同时打到 stderr：出问题时（孤儿、启动失败）这是唯一能从外面看到的线索
                eprintln!(
                    "[dsh] host 就绪 pid={pid} port={port} pidfile={:?}",
                    pidfile_path()
                );
                push_line(&self.log, format!("dsh host 就绪：{url}"));
                Ok(url)
            }
            None => {
                let _ = terminate(&mut child);
                forget_child();
                let diag = self.diagnostics().join("\n");
                let message = format!("dsh host 启动超时（40s，端口 {port}）");
                if let Ok(mut inner) = self.inner.lock() {
                    inner.last_error = Some(message.clone());
                }
                Err(format!("{message}\n{diag}"))
            }
        }
    }

    pub fn status(&self) -> DshStatus {
        let mut inner = match self.inner.lock() {
            Ok(i) => i,
            Err(e) => {
                return DshStatus { running: false, url: None, port: None, detail: e.to_string() }
            }
        };
        let running = match inner.child.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(None)),
            None => false,
        };
        if !running {
            inner.child = None;
        }
        DshStatus {
            running,
            url: inner.url.clone(),
            port: inner.port,
            detail: inner.last_error.clone().unwrap_or_default(),
        }
    }

    /// SIGTERM → 等 5s → SIGKILL。不用 `Child::kill()` 是因为那直接 SIGKILL，
    /// 会让 host 没机会 flush 会话与关掉它自己的子进程。
    pub fn stop(&self) {
        if let Ok(mut inner) = self.inner.lock() {
            if let Some(child) = inner.child.as_mut() {
                let _ = terminate(child);
            }
            inner.child = None;
            inner.url = None;
            inner.port = None;
        }
        CHILD_PID.store(0, Ordering::SeqCst);
        clear_pidfile();
    }
}

fn terminate(child: &mut Child) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let pid = child.id().to_string();
        let _ = Command::new("kill").arg("-TERM").arg(&pid).status();
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(200));
        }
    }
    child.kill()
}
