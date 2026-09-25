#[tauri::command]
fn agent_endpoint() -> String {
    if cfg!(target_os = "windows") {
        r"\\.\pipe\ming-tea-agent".to_string()
    } else {
        std::env::var("MING_TEA_IPC_PATH").unwrap_or_else(|_| "/tmp/ming-tea-agent.sock".to_string())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![agent_endpoint])
        .run(tauri::generate_context!())
        .expect("error while running 铭荼 desktop application");
}
