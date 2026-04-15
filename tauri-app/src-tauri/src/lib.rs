use tauri::Manager;

mod click_through;
mod crash_reports;
mod process_manager;
mod secrets;
mod webview_permissions;

use process_manager::BackendProcess;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Hook Rust panics before doing anything else so even early-init
    // failures leave a trace in crash_reports/.
    crash_reports::install_panic_hook();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        // W5 (R17): self-update — endpoints + pubkey live in tauri.conf.json.
        // On first launch the plugin fetches latest.json; if it advertises a
        // newer version the built-in dialog prompts the user.
        .plugin(tauri_plugin_updater::Builder::new().build())
        // W5 (R17): opt-in login autostart. Pass an empty args slice so we
        // don't inject anything surprising into the user's shell.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(BackendProcess::new())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
            // P2-1-S3: cloud LLM API key commands; UI invokes these from
            // SettingsPanel.
            secrets::set_cloud_api_key,
            secrets::get_cloud_api_key,
            secrets::delete_cloud_api_key,
            secrets::has_cloud_api_key,
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
