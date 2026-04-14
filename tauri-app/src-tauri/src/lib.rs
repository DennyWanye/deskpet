use tauri::Manager;

mod click_through;
mod process_manager;
mod webview_permissions;

use process_manager::BackendProcess;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(BackendProcess::new())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
        ])
        .setup(|app| {
            // Auto-grant microphone permission on the main WebView2 so
            // getUserMedia works inside the desktop-pet window.
            if let Some(win) = app.get_webview_window("main") {
                if let Err(e) = webview_permissions::grant_media_permissions(&win) {
                    eprintln!("[setup] grant_media_permissions failed: {e:?}");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<BackendProcess>() {
                    state.kill_child();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
