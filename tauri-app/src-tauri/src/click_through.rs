use tauri::{command, AppHandle, Manager};

#[command]
pub fn set_click_through(app: AppHandle, enabled: bool) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    window
        .set_ignore_cursor_events(enabled)
        .map_err(|e| e.to_string())
}
