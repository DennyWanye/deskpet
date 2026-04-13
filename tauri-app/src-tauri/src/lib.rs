use tauri::Manager;

mod click_through;
mod process_manager;

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            greet,
            click_through::set_click_through
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
