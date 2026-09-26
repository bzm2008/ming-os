use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

struct AgentChild(Mutex<Option<Child>>);

#[tauri::command]
fn agent_runtime_status(state: tauri::State<'_, AgentChild>) -> String {
    let running = state.0.lock().expect("agent lock").as_mut().map(|child| child.try_wait().map(|status| status.is_none()).unwrap_or(true)).unwrap_or(false);
    if running { "running".to_string() } else { "unavailable".to_string() }
}

#[tauri::command]
fn agent_endpoint() -> String {
    if cfg!(target_os = "windows") {
        r"\\.\pipe\ming-tea-agent".to_string()
    } else {
        std::env::var("MING_TEA_IPC_PATH").unwrap_or_else(|_| dirs::data_dir().unwrap_or_else(|| PathBuf::from("/tmp")).join("MingTea/run/agent.sock").display().to_string())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AgentChild(Mutex::new(None)))
        .setup(|app| {
            let resource = app.path().resource_dir()?.join("ming-tea-agent.mjs");
            if resource.is_file() {
                let endpoint = agent_endpoint();
                if let Some(parent) = PathBuf::from(&endpoint).parent() { std::fs::create_dir_all(parent)?; }
                if let Ok(child) = Command::new("node").arg(&resource).env("MING_TEA_IPC_PATH", &endpoint).spawn() {
                    app.state::<AgentChild>().0.lock().expect("agent lock").replace(child);
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![agent_endpoint, agent_runtime_status])
        .build(tauri::generate_context!())
        .map_err(|error| error.to_string())
        .and_then(|app| {
            app.run(|app_handle, event| {
                if let tauri::RunEvent::Exit = event {
                    if let Some(child) = app_handle.state::<AgentChild>().0.lock().expect("agent lock").as_mut() { let _ = child.kill(); }
                }
            });
            Ok(())
        })
        .expect("error while running 铭荼 desktop application");
}
