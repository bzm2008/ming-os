//! summon 面板：一个透明、置顶、无边框、不进任务栏的小窗口。
//!
//! 时序（决定体感的关键）：
//! 1. 热键触发 → **立刻**显示唤醒页（`waking.html`），不等任何后端；
//! 2. 后台确保 DSH host 在跑（幂等，已就绪时几乎零成本）；
//! 3. host 就绪后把面板导航到 `<url>&ming-tea=summon&auto=1` —— 插件收到该参数进入紧凑模式。
//!
//! 面板与主窗口加载同一个 DSH URL，所以宠物、本地语音、审批、模型、工具全都复用现成实现。

use crate::dsh_host::DshHost;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::Arc;
use tauri::{AppHandle, LogicalSize, Manager, PhysicalPosition, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const SUMMON_LABEL: &str = "summon";
pub const MAIN_LABEL: &str = "main";
const PANEL_WIDTH: f64 = 420.0;
const PANEL_HEIGHT: f64 = 320.0;

/// 三态几何（用户要求：默认只有宠物 → 说话时出转录胶囊 → 回答出现在胶囊下方）。
/// 窗口**底边固定**，内容往上长，所以宠物始终停在原位，不会因为回答变长而跳动。
pub struct Stage {
    pub name: &'static str,
    width: f64,
    height: f64,
    /// 是否让这一档接收鼠标事件。**当前四档全为 true**：宠物档也要可交互
    /// （开麦依赖真实用户手势），保留这个字段是为了将来还能有纯展示档。
    interactive: bool,
}

pub const STAGES: [Stage; 4] = [
    // 宠物档**必须可交互**：WKWebView 要求真实用户手势才开麦，合成点击不算，
    // 所以「点一下宠物说话」是开麦的唯一可靠入口（点击穿透会把这下点击让给背后的窗口）。
    Stage { name: "pet", width: 220.0, height: 220.0, interactive: true },
    Stage { name: "listening", width: 420.0, height: 280.0, interactive: true },
    Stage { name: "answer", width: 470.0, height: 520.0, interactive: true },
    // 审批档：必须够大且可交互，否则审批卡在视口外点不到（实测「本会话信任」按钮点不到）
    Stage { name: "approval", width: 520.0, height: 600.0, interactive: true },
];

fn stage_by_name(name: &str) -> &'static Stage {
    STAGES.iter().find(|s| s.name == name).unwrap_or(&STAGES[0])
}

/// 面板页 → 壳 的**唯一**通道：一个只绑 127.0.0.1、带 token 的极简上报口。
///
/// 为什么要它：面板加载的是 DSH host 的页面（另一个端口、外部源），页面的 JS 拿不到
/// Tauri IPC（远端页面默认没有 capability），但壳必须知道「现在该显示哪一档」才能改窗口大小。
/// 于是反过来：壳起一个只认 token 的 GET 口，页面用 `<img>`/`fetch` 打一下就完事（无需 CORS 预检）。
pub struct Beacon {
    pub port: u16,
    pub token: String,
}

static BEACON: std::sync::OnceLock<Beacon> = std::sync::OnceLock::new();

fn new_token() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{:x}{:x}", nanos, std::process::id())
}

fn parse_query(target: &str) -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    if let Some(idx) = target.find('?') {
        for pair in target[idx + 1..].split('&') {
            let mut it = pair.splitn(2, '=');
            if let (Some(k), Some(v)) = (it.next(), it.next()) {
                map.insert(k.to_string(), v.to_string());
            }
        }
    }
    map
}

/// 启动上报口。失败只记日志，不影响面板本身能用（只是尺寸不会跟着变）。
pub fn start_beacon(app: AppHandle) {
    let listener = match TcpListener::bind("127.0.0.1:0") {
        Ok(l) => l,
        Err(error) => {
            eprintln!("[summon] 上报口启动失败（面板尺寸将不随之变化）：{error}");
            return;
        }
    };
    let port = match listener.local_addr() {
        Ok(addr) => addr.port(),
        Err(error) => {
            eprintln!("[summon] 上报口地址读取失败：{error}");
            return;
        }
    };
    let token = new_token();
    let _ = BEACON.set(Beacon { port, token: token.clone() });
    eprintln!("[summon] 上报口就绪 http://127.0.0.1:{port}");
    // 只有显式打开调试开关才打印 token：它是这个口唯一的鉴权，平时不该进日志
    if std::env::var("MING_TEA_DEBUG_BEACON").as_deref() == Ok("1") {
        eprintln!("[summon][debug] beacon token={token}");
    }

    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let mut buf = [0u8; 2048];
            let read = stream.read(&mut buf).unwrap_or(0);
            let request = String::from_utf8_lossy(&buf[..read]).to_string();
            let target = request
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1))
                .unwrap_or("")
                .to_string();
            let params = parse_query(&target);
            // 路径决定动作（早先的版本把 /permissions 当成查询参数判定了，实测 404 无响应）
            let path = target.split('?').next().unwrap_or("");
            if !params.get("t").map(|v| v == &token).unwrap_or(false) {
                eprintln!("[summon] 上报口收到无效 token，已忽略：{target}");
                let _ = stream.write_all(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                );
                continue;
            }
            match path {
                // 权限状态：面板据此显示「缺哪个权限 + 一键去开启」
                "/permissions" => {
                    let list = crate::permissions::probe();
                    let body = serde_json::json!({ "capabilities": list }).to_string();
                    let response = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    );
                    let _ = stream.write_all(response.as_bytes());
                    continue;
                }
                // 一键跳系统设置对应页
                "/open-settings" => {
                    if let Some(id) = params.get("id") {
                        if let Err(error) = crate::permissions::open_settings(id) {
                            eprintln!("[summon] 打开系统设置失败（{id}）：{error}");
                        }
                    }
                }
                // 档位与收起
                _ => {
                    if let Some(stage) = params.get("stage") {
                        apply_stage(&app, stage);
                    }
                    if params.contains_key("hide") {
                        hide(&app);
                    }
                }
            }
            let _ = stream.write_all(
                b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n",
            );
        }
    });
}

/// 拼到面板 URL 后面的参数（页面据此上报档位）。
fn beacon_params() -> String {
    match BEACON.get() {
        Some(beacon) => format!("&mt-shell=http://127.0.0.1:{}&mt-token={}", beacon.port, beacon.token),
        None => String::new(),
    }
}

/// 建面板（不显示）。预建是「热键后 300ms 内可见」的前提：省掉建窗口的那几百毫秒。
///
/// ⚠️ 关键：面板加载的是 DSH 的页面，而 **DSH 启动后会把 URL 的 query 清掉**
/// （2026-10-01 实测：6 秒内 `location.search` 变成空）。所以 summon 参数不能靠页面自己读 URL ——
/// 用 `initialization_script` 在 **document-start**（应用还没动手时）把参数抓进全局变量，
/// 我们插件的客户端半区再从这个全局读。
pub fn ensure_panel(app: &AppHandle) -> Result<WebviewWindow, String> {
    if let Some(existing) = app.get_webview_window(SUMMON_LABEL) {
        return Ok(existing);
    }
    let pet = stage_by_name("pet");
    WebviewWindowBuilder::new(app, SUMMON_LABEL, WebviewUrl::App("waking.html".into()))
        .title("铭荼助手")
        .inner_size(pet.width, pet.height)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .visible(false)
        .focused(false)
        .initialization_script(summon_init_script())
        .build()
        .map_err(|e| e.to_string())
}

/// 在每个新文档最开始执行：把 summon 标记与上报口**字面量**冻结到全局。
///
/// 为什么不能读 `location.search`：DSH host 对带 token 的入口 URL 会回 **303 重定向**到 `/`
/// （2026-10-01 实测：连 document-start 拿到的 `location.search` 都是空的），参数在 HTTP 层就被吃掉了。
/// 壳自己知道这些值，所以直接注入常量 —— 而且这个脚本只挂在面板窗口上，主窗口天然不会进入 summon 模式。
fn summon_init_script() -> String {
    let (shell, token) = match BEACON.get() {
        Some(beacon) => (format!("http://127.0.0.1:{}", beacon.port), beacon.token.clone()),
        None => (String::new(), String::new()),
    };
    let shell_json = serde_json::to_string(&shell).unwrap_or_else(|_| "\"\"".into());
    let token_json = serde_json::to_string(&token).unwrap_or_else(|_| "\"\"".into());
    format!(
        r#"(() => {{
  try {{
    window.__MING_TEA_SUMMON = {{ on: true, auto: true, shell: {shell_json}, token: {token_json} }};
  }} catch (error) {{
    /* 注入脚本绝不抛错影响页面 */
  }}
}})();
"#
    )
}

/// 贴屏幕底部居中偏上（手机助手的位置感）。**底边固定**，所以调用方只改尺寸也稳。
///
/// 两个实测细节（2026-10-02，用 `scripts/winlist.c` 采样窗口几何时抓到）：
/// 1. `current_monitor()` 可能返回 `None`（面板刚建好、还没落到任何屏幕时就会）——
///    早先这里直接 `return`，于是**位置停在旧值、只有宽高变了**，肉眼就是面板没居中；
///    现在退到 `primary_monitor()`，两个都没有才放弃并打一行日志。
/// 2. `set_size` 与 `set_position` 是两条独立的运行时分发（Tauri 2 的 `set_bounds`
///    只作用于内部 webview，不能用来一次设窗口），所以先把**位置**摆好再改尺寸：
///    中间态是「旧尺寸、新位置」，比「新尺寸、旧位置」更不容易看出跳动。
fn place_bottom_center(window: &WebviewWindow, width: f64, height: f64) {
    let monitor = match window.current_monitor() {
        Ok(Some(monitor)) => Some(monitor),
        _ => window.primary_monitor().ok().flatten(),
    };
    let Some(monitor) = monitor else {
        eprintln!("[summon] 取不到屏幕信息，面板位置未更新（请检查是否在没有显示器的会话里）");
        return;
    };
    let size = monitor.size();
    let scale = monitor.scale_factor();
    let w = width * scale;
    let h = height * scale;
    let x = (size.width as f64 - w) / 2.0;
    let y = size.height as f64 - h - 140.0 * scale;
    let _ = window.set_position(PhysicalPosition::new(x.max(0.0), y.max(0.0)));
}

/// 应用某一档：改尺寸 + 重新贴底 + 决定是否点击穿透。面板页通过本地上报通道驱动它。
pub fn apply_stage(app: &AppHandle, name: &str) {
    let Some(window) = app.get_webview_window(SUMMON_LABEL) else { return };
    let stage = stage_by_name(name);
    // 顺序有意义：先摆位置、再改尺寸（见 place_bottom_center 的说明）
    place_bottom_center(&window, stage.width, stage.height);
    let _ = window.set_size(LogicalSize::new(stage.width, stage.height));
    if let Err(error) = window.set_ignore_cursor_events(!stage.interactive) {
        eprintln!("[summon] 设置点击穿透失败：{error}");
    }
    eprintln!("[summon] 档位 -> {}（{}×{}，interactive={}）", stage.name, stage.width, stage.height, stage.interactive);
}

/// 把面板导航到 DSH URL（带上 summon 参数与上报口信息）。
fn navigate_to_dsh(window: &WebviewWindow, base: &str) {
    let separator = if base.contains('?') { '&' } else { '?' };
    let full = format!("{base}{separator}ming-tea=summon&auto=1{}", beacon_params());
    match tauri::Url::parse(&full) {
        Ok(url) => {
            if let Err(error) = window.navigate(url) {
                eprintln!("[summon] 导航到 DSH 失败：{error}");
            }
        }
        Err(error) => eprintln!("[summon] DSH URL 解析失败（{full}）：{error}"),
    }
}

/// 热键/URL 唤起面板。返回面板是否已显示（便于调用方做日志）。
pub fn summon(app: &AppHandle, host: Arc<DshHost>) -> Result<(), String> {
    let window = ensure_panel(app)?;
    // 已经有面板且可见 → 再按一次就收起（和手机助手的开关手感一致）
    if window.is_visible().unwrap_or(false) {
        eprintln!("[summon] 面板已可见，收起");
        hide(app);
        return Ok(());
    }
    let status = host.status();

    // 已经就绪：直接给真面板；否则先给唤醒页（用户立刻看到反馈）
    if let Some(url) = status.url.clone() {
        navigate_to_dsh(&window, &url);
    }
    window.show().map_err(|e| e.to_string())?;
    // 聚焦失败**不能**中断后面的摆位：冷启动时窗口刚映射，set_focus 可能失败，
    // 而位置没摆正是用户最容易看到的毛病（实测踩到：`?` 提前返回 ⇒ 面板停在 y=215）。
    if let Err(error) = window.set_focus() {
        eprintln!("[summon] 聚焦面板失败（继续摆位）：{error}");
    }
    // 每次呼出都从「只有宠物」这一档开始（用户要求：先只出现宠物）。
    // ⚠️ 顺序很重要：**必须放在 show() 之后**。窗口还没被 OS 映射时设的位置不生效，
    // 于是冷启动会先按系统默认位置露一下（实测 y=215，本该 720），等插件上报档位才跳到正确位置
    // —— 用户看到一次明显跳动（2026-10-02 实测）。show 之后再摆位就一次到位。
    apply_stage(app, "pet");
    eprintln!(
        "[summon] 面板已显示（host_ready={}, url={:?}）",
        status.url.is_some(),
        status.url
    );

    if status.url.is_some() {
        return Ok(());
    }

    // 后台把 host 拉起来，就绪后再导航（不阻塞热键响应）
    let app_for_thread = app.clone();
    std::thread::spawn(move || match host.ensure_started("ming-tea") {
        Ok(url) => {
            if let Some(window) = app_for_thread.get_webview_window(SUMMON_LABEL) {
                navigate_to_dsh(&window, &url);
                let _ = window.set_focus();
            }
        }
        Err(error) => {
            eprintln!("[summon] DSH host 启动失败：{error}");
            if let Some(window) = app_for_thread.get_webview_window(SUMMON_LABEL) {
                // 唤醒页自己会读这个标题显示错误，避免用户对着空白面板发呆
                let _ = window.set_title(&format!("铭荼助手 · 启动失败"));
                let _ = window.eval(format!(
                    "window.__mingTeaHostError = {};",
                    serde_json::to_string(&error).unwrap_or_else(|_| "\"启动失败\"".into())
                ));
            }
        }
    });
    Ok(())
}

pub fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(SUMMON_LABEL) {
        let _ = window.hide();
    }
}

/// 主窗口加载 DSH 界面（替代原来的自绘占位 UI）。
pub fn open_main_window(app: &AppHandle, host: Arc<DshHost>) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN_LABEL)
        .ok_or_else(|| "找不到主窗口".to_string())?;
    // 记一行：主窗口在 tauri.conf.json 里是 `visible: false`（好让 summon 冷启动不弹它），
    // 所以「到底有没有显示主窗口」只能靠日志断言（2026-10-02 加）。
    eprintln!("[main] 显示主窗口（启动方式见上面的 setup 行）");
    let app_for_thread = app.clone();
    std::thread::spawn(move || match host.ensure_started("ming-tea") {
        Ok(url) => {
            if let Some(window) = app_for_thread.get_webview_window(MAIN_LABEL) {
                match tauri::Url::parse(&url) {
                    Ok(parsed) => {
                        let _ = window.navigate(parsed);
                    }
                    Err(error) => eprintln!("[main] URL 解析失败：{error}"),
                }
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        Err(error) => eprintln!("[main] DSH host 启动失败：{error}"),
    });
    let _ = window.show();
    Ok(())
}
