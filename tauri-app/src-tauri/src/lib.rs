// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

use tauri::Manager;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

mod artifact_ops;
mod backend_launch;
mod click_through;
mod commands;
mod crash_reports;
mod device;
mod diagnostics;
mod gpu_check;
mod job_object;
mod onboarding;
mod paths;
mod process_manager;
mod secrets;
mod user_data;
mod webview_permissions;
mod window_geometry;

use process_manager::BackendProcess;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Hook Rust panics before doing anything else so even early-init
    // failures leave a trace in crash_reports/.
    crash_reports::install_panic_hook();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        // P3-S2: dialog plugin for the "no NVIDIA GPU" fatal-error path.
        .plugin(tauri_plugin_dialog::init())
        // WI-T1.3 last-mile: clipboard for artifact_copy_path command.
        .plugin(tauri_plugin_clipboard_manager::init())
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
        .manage(window_geometry::ResizeDebouncer::new())
        .invoke_handler(tauri::generate_handler![
            click_through::set_click_through,
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
            // P3-S8: startup error surfacing for the frontend dialog.
            process_manager::get_startup_error,
            process_manager::clear_startup_error,
            // P3-S8/S9: user-data filesystem commands (open log dir,
            // open AppData dir, purge everything).
            // WI-T1.3 last-mile artifact actions (PRD §3 D3)
            artifact_ops::artifact_open,
            artifact_ops::artifact_show_in_folder,
            artifact_ops::artifact_copy_path,
            artifact_ops::artifact_save_as,
            user_data::open_log_dir,
            user_data::open_app_data_dir,
            user_data::purge_user_data,
            // 2026-05-21: data-dir relocator (Settings → 数据目录)
            user_data::get_data_dir_setting,
            user_data::set_data_dir_preference,
            user_data::move_data_dir_contents,
            // P2-1-S3: cloud LLM API key commands; UI invokes these from
            // SettingsPanel.
            secrets::set_cloud_api_key,
            secrets::get_cloud_api_key,
            secrets::delete_cloud_api_key,
            secrets::has_cloud_api_key,
            // W2 (relay integration): per-credential slots for the
            // closed-source RelayAuthAdapter. OSS build still registers
            // them — they're harmless if no UI ever invokes them.
            secrets::set_relay_access_token,
            secrets::get_relay_access_token,
            secrets::delete_relay_access_token,
            secrets::set_relay_refresh_token,
            secrets::get_relay_refresh_token,
            secrets::delete_relay_refresh_token,
            secrets::set_relay_device_key,
            secrets::get_relay_device_key,
            secrets::delete_relay_device_key,
            secrets::clear_all_relay_secrets,
            // W2: stable device id for the relay's X-Device-Id header.
            device::get_or_create_device_id,
            device::get_default_device_name,
            // P4-S21 #1: HTTP proxy from frontend to backend, bypasses
            // webview's https→http mixed-content block.
            commands::update_cloud_config,
            // P4-S21 #7: graceful Quit invoked by Toolbar ⏻ button.
            commands::app_exit,
            // P4-S22: native folder picker for Code mode entry.
            commands::open_directory_dialog,
            // P4-S23: second-window (code panel) open/close commands.
            commands::open_code_panel,
            commands::close_code_panel,
            // 2026-05-19: slim message panel as its own docked window.
            commands::open_message_panel,
            commands::close_message_panel,
            commands::dock_message_panel,
            commands::toggle_message_panel,
            // WI-01 (beta-100): first-run onboarding state.
            onboarding::onboarding_status,
            onboarding::onboarding_complete,
            // WI-02 (beta-100): diagnostic feedback bundle.
            diagnostics::build_diagnostic_bundle,
            // 2026-05-26: 桌宠主窗 resize 持久化。
            window_geometry::get_saved_window_geometry,
            window_geometry::set_window_geometry,
        ])
        .setup(|app| {
            // P3-S2: NVIDIA precheck. Phase-3 contract is CUDA-only, so
            // if we can't detect an NVIDIA GPU now we bail before the
            // Python backend ever spawns (otherwise faster-whisper would
            // crash silently during lifespan and the user would just see
            // a broken ASR).
            if let Err(e) = gpu_check::detect_nvidia_gpu() {
                eprintln!("[setup] gpu_check failed: {e:?}");
                let msg = gpu_check::format_user_message(&e);
                app.dialog()
                    .message(msg)
                    .title("DeskPet — 硬件不支持")
                    .kind(MessageDialogKind::Error)
                    .buttons(MessageDialogButtons::Ok)
                    .blocking_show();
                app.handle().exit(1);
                // Still return Ok — the exit above will terminate the
                // process; returning Err here would just print a panic
                // trace on top of the dialog the user already saw.
                return Ok(());
            }

            // Auto-grant microphone permission on the main WebView2 so
            // getUserMedia works inside the desktop-pet window.
            if let Some(win) = app.get_webview_window("main") {
                if let Err(e) = webview_permissions::grant_media_permissions(&win) {
                    eprintln!("[setup] grant_media_permissions failed: {e:?}");
                }
                // 2026-05-26: 应用上次拉的尺寸；2026-06-02 起也恢复位置 + 显示器。
                // 读不到（首启 / 损坏）就走 tauri.conf.json 默认，无副作用。
                window_geometry::apply_saved_geometry(&win);
            }

            // P4-S22+ portable mode: pre-create `<install>/userdata/`
            // so the user can drop files there from a fresh install
            // (browse to it via a "Open data folder" tray entry,
            // future feature). Backend's `paths.py` will also lazily
            // mkdir on first access if this fails — strictly best-
            // effort here.
            if let Ok(exe) = std::env::current_exe() {
                if let Some(install_dir) = exe.parent() {
                    let _ = std::fs::create_dir_all(install_dir.join("userdata"));
                }
            }

            // P4-S21 #7: system tray icon with Show/Hide/Quit menu.
            // Without this the only way to surface a hidden pet (or a
            // pet whose toolbar is offscreen) is to kill the process.
            let show_item = MenuItem::with_id(app, "show", "显示桌宠", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "隐藏桌宠", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出 DeskPet", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;
            let _tray = TrayIconBuilder::with_id("deskpet-tray")
                .icon(app.default_window_icon().cloned().expect("icon set in tauri.conf.json"))
                .tooltip("DeskPet")
                .menu(&tray_menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "hide" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                    "quit" => {
                        if let Some(state) = app.try_state::<BackendProcess>() {
                            state.kill_child();
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // P4-S23: when the user clicks the OS title-bar X on the
            // *code panel*, hide instead of destroy — keeps zustand
            // state, chat scrollback, the WebSocket and the URL
            // fragment alive across "I closed it by mistake" moments.
            // The pet (main) window keeps destroy-on-close because
            // closing the pet really is a quit signal.
            //
            // Reopen reliability is solved at the show() side via the
            // alwaysOnTop pulse in `commands::open_code_panel`, NOT by
            // destroying here — destroying breaks dev-mode hash routing
            // (`WebviewUrl::App` drops the `#/code-panel` fragment when
            // used dynamically, leaving the rebuilt webview blank).
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "code-panel" {
                    api.prevent_close();
                    let _ = window.hide();
                    return;
                }
            }
            // 2026-05-26: 桌宠主窗 resize → 防抖落盘。
            if let tauri::WindowEvent::Resized(size) = event {
                eprintln!("[window_geometry] Resized event label={} size={}x{}", window.label(), size.width, size.height);
                if window.label() == "main" {
                    if let Some(deb) = window.try_state::<window_geometry::ResizeDebouncer>() {
                        deb.on_resize(window, *size);
                    } else {
                        eprintln!("[window_geometry] ResizeDebouncer state NOT FOUND");
                    }
                }
            }
            // 2026-06-02: 桌宠主窗 move（用户拖动）→ 防抖落盘位置，
            // 让桌宠记住所在显示器，下次启动恢复（多屏副屏放置）。
            if let tauri::WindowEvent::Moved(_pos) = event {
                if window.label() == "main" {
                    if let Some(deb) = window.try_state::<window_geometry::ResizeDebouncer>() {
                        deb.on_move(window);
                    }
                }
            }
            if let tauri::WindowEvent::Destroyed = event {
                // Only the main pet's destroy means "quit the app";
                // any other window is a non-event for the supervisor.
                if window.label() != "main" {
                    return;
                }
                if let Some(state) = window.try_state::<BackendProcess>() {
                    state.kill_child();
                }
                // 关闭桌宠主窗 = 退出整个 app。否则启动时一并创建的隐藏窗口
                // （message-panel / code-panel）会让进程继续存活，python
                // backend 也跟着不退（即便有 job object 也只在进程真正退出时
                // 才触发清理），重开时撞 8100 端口占用。显式 exit → 进程结束
                // → job object 关闭 → backend 被 OS 连带终止。
                window.app_handle().exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
