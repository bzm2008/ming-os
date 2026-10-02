//! 铭荼桌面壳。
//!
//! 职责（2026-10-01 起）：
//! 1. **持有全局热键的守护进程**（`platform/ming-tea/apps/hotkey-daemon`）随登录启动，
//!    这样即使本进程完全退出，快捷键仍能用 `mingtea://summon` 把应用拉起来；
//! 2. **启动并监督 DSH host**（`dsh --profile ming-tea --port <随机> --no-open`），
//!    主窗口与 summon 面板都加载它 —— 真脑子在 DSH，不在壳里；
//! 3. **summon 面板**（透明置顶小窗）+ 主窗口恢复；
//! 4. **系统权限的真实探测与引导**（辅助功能 / 屏幕录制 / 麦克风）。
//!
//! 历史说明：以前这里 spawn 的是 `resources/ming-tea-agent.mjs`（一个进程内桩：
//! 假会话、假 provider、工具策略），它虽然也 `dsh --profile ming-tea` 却从不与之通信。
//! 现在改由 `DshHost` 真正管理 DSH host；那个桩文件保留在仓库里，但不再启动。

mod dsh_host;
mod launch_agent;
mod logging;
mod permissions;
mod summon;

use dsh_host::{DshHost, DshStatus};
use std::sync::Arc;
use tauri::{AppHandle, Manager};

struct AppState {
    host: Arc<DshHost>,
}

#[tauri::command]
fn dsh_status(state: tauri::State<'_, AppState>) -> DshStatus {
    state.host.status()
}

#[tauri::command]
fn dsh_start(state: tauri::State<'_, AppState>) -> Result<String, String> {
    state.host.ensure_started("ming-tea")
}

#[tauri::command]
fn dsh_diagnostics(state: tauri::State<'_, AppState>) -> Vec<String> {
    state.host.diagnostics()
}

#[tauri::command]
fn summon_show(app: AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
    summon::summon(&app, Arc::clone(&state.host))
}

#[tauri::command]
fn summon_hide(app: AppHandle) {
    summon::hide(&app);
}

#[tauri::command]
fn main_open(app: AppHandle, state: tauri::State<'_, AppState>) -> Result<(), String> {
    summon::open_main_window(&app, Arc::clone(&state.host))
}

#[tauri::command]
fn permission_status() -> Vec<permissions::Capability> {
    permissions::probe()
}

#[tauri::command]
fn permission_open_settings(id: String) -> Result<(), String> {
    permissions::open_settings(&id)
}

#[tauri::command]
fn hotkey_status() -> launch_agent::LaunchAgentStatus {
    launch_agent::status()
}

#[tauri::command]
fn hotkey_install() -> Result<launch_agent::LaunchAgentStatus, String> {
    launch_agent::install()
}

#[tauri::command]
fn hotkey_uninstall() -> Result<launch_agent::LaunchAgentStatus, String> {
    launch_agent::uninstall()
}

/// 首次运行时把热键守护进程装成 LaunchAgent（幂等；装过就只报状态）。
///
/// 为什么由应用来装：`open mingtea://summon` 要在**应用没跑**时也能把应用拉起来，
/// 所以热键必须由一个随登录启动的常驻小进程持有（见 platform/ming-tea/apps/hotkey-daemon）。
fn install_hotkey_daemon() {
    // 开发/调试用：已经手动跑着守护进程时，别让应用再装一个（两个实例抢同一个热键，
    // 后注册的会失败并每 30 秒重试刷日志）。
    if std::env::var("MING_TEA_NO_HOTKEY_INSTALL").as_deref() == Ok("1") {
        eprintln!("[hotkey] MING_TEA_NO_HOTKEY_INSTALL=1，跳过 LaunchAgent 安装");
        return;
    }
    let status = launch_agent::status();
    let recorded = launch_agent::installed_helper_path().map(|path| path.display().to_string());
    if status.installed && status.loaded && status.helper == recorded {
        eprintln!(
            "[hotkey] LaunchAgent 已在位且指向当前 helper（helper={:?}）",
            status.helper
        );
        return;
    }
    if status.installed {
        // 装过但没加载、或路径变了（开发机 → .app 包内）都要重写：否则热键指向一个不存在的位置
        eprintln!(
            "[hotkey] 需要刷新 LaunchAgent（installed={}, loaded={}, 记录路径={:?}）",
            status.installed,
            status.loaded,
            launch_agent::installed_helper_path()
        );
    }
    if launch_agent::resolve_helper().is_none() {
        eprintln!(
            "[hotkey] 找不到守护进程二进制，跳过安装；先构建 platform/ming-tea/apps/hotkey-daemon（pnpm build:hotkey）"
        );
        return;
    }
    match launch_agent::install() {
        Ok(installed) => eprintln!(
            "[hotkey] 已安装随登录启动（loaded={}, helper={:?}）",
            installed.loaded, installed.helper
        ),
        Err(error) => eprintln!("[hotkey] 安装失败（可在设置里重试）：{error}"),
    }
}

/// 是否已经处理过一次 summon（冷启动时初始 URL 的 `RunEvent::Opened` 可能早于
/// 我们的 `on_open_url` 注册，setup 里的补偿逻辑据此避免重复处理 —— 重复会变成「再按一次收起」）。
static SUMMON_HANDLED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 处理 `mingtea://summon|open|quit`。
fn handle_deep_link(app: &AppHandle, raw: &str) {
    let lower = raw.to_ascii_lowercase();
    if lower.starts_with("mingtea://summon") {
        SUMMON_HANDLED.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Some(state) = app.try_state::<AppState>() {
            let _ = summon::summon(app, Arc::clone(&state.host));
        }
    } else if lower.starts_with("mingtea://open") {
        if let Some(state) = app.try_state::<AppState>() {
            let _ = summon::open_main_window(app, Arc::clone(&state.host));
        }
    } else if lower.starts_with("mingtea://quit") {
        app.exit(0);
    } else {
        eprintln!("[deep-link] 未识别的 URL：{raw}");
    }
}

/// 开发期用 `--summon` 直接试，不必依赖已注册的 URL scheme。
fn handle_argv(app: &AppHandle, argv: &[String]) {
    for arg in argv.iter().skip(1) {
        if arg == "--summon" {
            handle_deep_link(app, "mingtea://summon");
        } else if arg.starts_with("mingtea://") {
            handle_deep_link(app, arg);
        }
    }
}

fn install_tray(app: &AppHandle) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::TrayIconBuilder;

    let show_main = MenuItem::with_id(app, "show-main", "显示主窗口", true, None::<&str>)?;
    let summon_item = MenuItem::with_id(app, "summon", "呼出助手", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出铭荼", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_main, &summon_item, &quit])?;

    let mut builder = TrayIconBuilder::new()
        .menu(&menu)
        .tooltip("铭荼")
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show-main" => {
                if let Some(state) = app.try_state::<AppState>() {
                    let _ = summon::open_main_window(app, Arc::clone(&state.host));
                }
            }
            "summon" => {
                if let Some(state) = app.try_state::<AppState>() {
                    let _ = summon::summon(app, Arc::clone(&state.host));
                }
            }
            "quit" => app.exit(0),
            _ => {}
        });
    // 没有图标就跳过托盘（不因为缺图标让整个应用起不来）
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
        builder.build(app)?;
    } else {
        eprintln!("[tray] 没有默认窗口图标，跳过托盘注册");
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 第一件事：把 stderr 落到 ~/Library/Logs/铭荼/app.log。
    // 打包版被 LaunchServices 拉起时 stderr 无处可去（实测 `log show` 里一条我们自己的行都没有），
    // 冷启动这类问题因此无法验证、用户也拿不到现场。
    logging::redirect_stderr_to_file();

    let host = Arc::new(DshHost::new());
    let host_for_setup = Arc::clone(&host);
    let host_for_exit = Arc::clone(&host);

    tauri::Builder::default()
        // single-instance 必须最先注册：第二次启动只把 argv 交给已在跑的实例
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            eprintln!("[single-instance] 第二次启动，argv={argv:?}");
            handle_argv(app, &argv);
        }))
        .plugin(tauri_plugin_deep_link::init())
        .manage(AppState { host })
        .invoke_handler(tauri::generate_handler![
            dsh_status,
            dsh_start,
            dsh_diagnostics,
            summon_show,
            summon_hide,
            main_open,
            permission_status,
            permission_open_settings,
            hotkey_status,
            hotkey_install,
            hotkey_uninstall,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            // 被 pkill / 系统退出杀掉时把 host 一起带走（Rust 默认没有信号处理器）
            dsh_host::install_signal_handlers();
            // 上一次崩溃/强杀可能留下还在监听的 host，先清掉
            dsh_host::cleanup_stale_host();
            // 让快捷键在应用完全退出时也能用：装一个随登录启动的热键守护进程（幂等）
            install_hotkey_daemon();

            // 预建 summon 面板（隐藏）——热键后立刻可见的关键
            // 上报口必须**先于**面板内容导航就绪，页面才知道往哪儿报档位
            summon::start_beacon(handle.clone());
            if let Err(error) = summon::ensure_panel(&handle) {
                eprintln!("[setup] 创建 summon 面板失败：{error}");
            }

            #[cfg(desktop)]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let handle_for_url = handle.clone();
                app.deep_link().on_open_url(move |event| {
                    for url in event.urls() {
                        handle_deep_link(&handle_for_url, url.as_str());
                    }
                });
                // 开发期把 scheme 注册到当前二进制；打包后由 Info.plist 的 CFBundleURLTypes 负责
                if let Err(error) = app.deep_link().register_all() {
                    eprintln!("[deep-link] register_all 失败（打包后不需要）：{error}");
                }
            }

            if let Err(error) = install_tray(&handle) {
                eprintln!("[setup] 托盘注册失败：{error}");
            }

            // 是不是「被热键召唤」启动的：这种情况下**不要**弹主窗口，
            // 否则用户按一下快捷键会先被整个应用窗口糊一脸（手机助手不是这个体感）。
            let argv: Vec<String> = std::env::args().collect();
            let summon_launch = argv
                .iter()
                .any(|arg| arg == "--summon" || arg.to_ascii_lowercase().starts_with("mingtea://summon"));

            if summon_launch {
                eprintln!("[setup] 召唤启动：只弹面板，不显示主窗口");
                let host = Arc::clone(&host_for_setup);
                std::thread::spawn(move || {
                    if let Err(error) = host.ensure_started("ming-tea") {
                        eprintln!("[setup] 召唤启动时拉起 host 失败：{error}");
                    }
                });
            } else {
                // 也可能是**被 summon URL 拉起的**：macOS 上 `open mingtea://summon`
                // 不会把 URL 放进 argv（走的是 Apple Event → 插件的 `RunEvent::Opened`），
                // 所以上面那轮 argv 判断对冷启动不成立 —— 实测因此**主窗口也被弹了出来**，
                // 与「按一下快捷键先被整个应用窗口糊一脸」正是要避免的体感。
                //
                // 这个线程做两件事：
                // 1. 等初始 URL 出现（最多 1 秒；普通启动拿不到就按普通启动处理）；
                // 2. **如果事件比我们的监听更早发出，就自己补处理一次** —— 实测冷启动时
                //    `get_current()` 有值、但我们的 `on_open_url` 回调没收到（事件在注册监听前已发过），
                //    表现为「面板窗口存在但不显示、停在系统默认位置」。
                let handle_for_main = handle.clone();
                let host_for_main = Arc::clone(&host_for_setup);
                std::thread::spawn(move || {
                    use tauri_plugin_deep_link::DeepLinkExt;
                    let started = std::time::Instant::now();
                    let mut initial: Option<Vec<tauri::Url>> = None;
                    for _ in 0..10 {
                        std::thread::sleep(std::time::Duration::from_millis(100));
                        if let Ok(Some(urls)) = handle_for_main.deep_link().get_current() {
                            initial = Some(urls);
                            break;
                        }
                    }
                    let is_summon = |url: &tauri::Url| {
                        url.as_str().to_ascii_lowercase().starts_with("mingtea://summon")
                    };
                    match initial {
                        Some(urls) if urls.iter().any(is_summon) => {
                            eprintln!(
                                "[setup] 被 summon URL 拉起（{}ms 后确认）：只弹面板，不显示主窗口",
                                started.elapsed().as_millis()
                            );
                            if !SUMMON_HANDLED.load(std::sync::atomic::Ordering::SeqCst) {
                                if let Some(url) = urls.iter().find(|url| is_summon(url)) {
                                    eprintln!("[setup] 初始 URL 事件没到我们的监听，这里补处理一次");
                                    handle_deep_link(&handle_for_main, url.as_str());
                                }
                            }
                        }
                        Some(urls) => {
                            // 是别的 URL（例如 mingtea://open）：照常显示主窗口，并补处理该 URL
                            let _ = summon::open_main_window(&handle_for_main, host_for_main);
                            for url in urls {
                                handle_deep_link(&handle_for_main, url.as_str());
                            }
                        }
                        None => {
                            // 普通启动（没有初始 URL）
                            let _ = summon::open_main_window(&handle_for_main, host_for_main);
                        }
                    }
                });
            }

            // 首次启动时如果就是被 URL 拉起来的，这里补一次
            handle_argv(&handle, &argv);

            Ok(())
        })
        .build(tauri::generate_context!())
        .map_err(|error| error.to_string())
        .and_then(move |app| {
            let handle_for_events = app.handle().clone();
            let host_for_events = Arc::clone(&host_for_exit);
            app.run(move |_app_handle, event| match event {
                tauri::RunEvent::Exit => host_for_events.stop(),
                // 点 Dock 图标 / 从 Finder 重新打开：把主窗口找回来
                tauri::RunEvent::Reopen { .. } => {
                    let _ = summon::open_main_window(&handle_for_events, Arc::clone(&host_for_events));
                }
                _ => {}
            });
            Ok(())
        })
        .expect("error while running 铭荼 desktop application");
}
