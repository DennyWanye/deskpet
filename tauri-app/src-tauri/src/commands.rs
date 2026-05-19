// P4-S21 commands — frontend↔backend IPC bridge for things webview
// can't do directly (mixed-content fetch, app exit).
//
// `update_cloud_config` is the dramatic one: in release builds the
// webview origin is `https://tauri.localhost`, and `fetch("http://...")`
// is silently blocked as mixed content. The `Settings → Save` UX broke
// because of this. Solution: route the POST through Rust, which has no
// mixed-content notion and already knows the SHARED_SECRET. Frontend
// gets a clean Promise back via `invoke()`.
//
// `app_exit` is trivial — the toolbar Quit button calls it. We can't
// use `window.close()` from the renderer because, well, the renderer's
// process is the app. Going through the AppHandle ensures backend
// supervisor teardown via the existing WindowEvent::Destroyed handler.

use serde_json::Value;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

use crate::process_manager::BackendProcess;

/// POST /config/cloud on behalf of the frontend.
///
/// We deliberately **don't** accept the secret as a parameter — Rust
/// already has it in `BackendProcess::shared_secret`, and exposing it
/// to the renderer just to round-trip back is pointless attack surface.
/// We also pin the URL to localhost so a compromised renderer can't
/// redirect the call to an attacker-controlled host.
#[tauri::command]
pub async fn update_cloud_config(
    state: State<'_, BackendProcess>,
    update: Value,
) -> Result<Value, String> {
    let secret = state
        .shared_secret_clone()
        .ok_or_else(|| "backend not running yet".to_string())?;
    let port = state.port();

    let url = format!("http://127.0.0.1:{}/config/cloud", port);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| format!("reqwest client init: {e}"))?;

    let resp = client
        .post(&url)
        .header("X-Shared-Secret", &secret)
        .json(&update)
        .send()
        .await
        .map_err(|e| format!("backend POST {url} failed: {e}"))?;

    let status = resp.status();
    let body: Value = resp
        .json()
        .await
        .map_err(|e| format!("backend response not JSON: {e}"))?;

    if !status.is_success() {
        // Surface the backend-rendered error so the frontend can show it.
        let err_text = body
            .get("detail")
            .and_then(|d| d.as_str())
            .map(String::from)
            .unwrap_or_else(|| body.to_string());
        return Err(format!("backend {}: {}", status.as_u16(), err_text));
    }

    Ok(body)
}

/// Quit the app gracefully. Closing the main window triggers the
/// existing WindowEvent::Destroyed handler in lib.rs which kills the
/// backend supervisor — `app.exit(0)` does the same end-to-end.
#[tauri::command]
pub fn app_exit(app: AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        state.kill_child();
    }
    app.exit(0);
}

/// P4-S23: show the secondary "code-panel" webview window.
///
/// Bug fix history:
///
/// V1 (original): close=hide, open=show+set_focus. User reported the
/// panel sometimes wouldn't reopen — `set_focus` errors propagated as
/// IPC rejections, and on Windows the panel could end up Z-ordered
/// BEHIND the alwaysOnTop pet, so users perceived it as "didn't open".
///
/// V2 (first attempt): close=destroy, open=rebuild. Fixed the visible
/// reopen but broke dev mode — `WebviewUrl::App(PathBuf)` drops the
/// `#/code-panel` URL fragment during dynamic creation (the static
/// `tauri.conf.json` config loader handles it differently), so the
/// rebuilt webview loaded `index.html` without the hash and rendered
/// the pet shell instead of the code panel.
///
/// V3 (current): keep close=hide so the static window survives intact,
/// but make show() reliable by force-fronting via an `always_on_top`
/// pulse. This brings the panel above the alwaysOnTop pet without
/// permanently changing its Z-policy. `set_focus` failures are now
/// non-fatal — even if the OS denies foreground activation, the
/// window is already visible from `show()`.
#[tauri::command]
pub fn open_code_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("code-panel") {
        w.show().map_err(|e| e.to_string())?;
        let _ = w.unminimize();
        let _ = w.set_focus();
        // Force the panel above alwaysOnTop windows (the pet) by
        // briefly toggling alwaysOnTop. Without this, on Windows
        // `ShowWindow` + `SetForegroundWindow` can leave the panel
        // behind the alwaysOnTop pet and users think it didn't open.
        let _ = w.set_always_on_top(true);
        let _ = w.set_always_on_top(false);
        return Ok(());
    }
    // Window was destroyed (rare — only happens if something else
    // closed it forcibly). Rebuild, but use the same URL pattern
    // tauri.conf.json uses so the hash routing survives.
    use tauri::{WebviewUrl, WebviewWindowBuilder};
    WebviewWindowBuilder::new(
        &app,
        "code-panel",
        WebviewUrl::App("index.html#/code-panel".into()),
    )
    .title("DeskPet · Code Mode")
    .inner_size(1024.0, 720.0)
    .min_inner_size(720.0, 540.0)
    .resizable(true)
    .decorations(true)
    .transparent(false)
    .visible(true)
    .focused(true)
    .build()
    .map_err(|e| format!("failed to recreate code-panel: {e}"))?;
    Ok(())
}

/// P4-S23: hide the code-panel without destroying it.
///
/// We deliberately `hide()` (not `close()`) so the underlying webview
/// stays alive — zustand state, scrollback, and the URL fragment all
/// survive across "I closed it by mistake" moments. Reopening is then
/// a cheap `show()` in `open_code_panel` instead of a full webview
/// rebuild (which has known dev-mode hash-routing quirks).
#[tauri::command]
pub fn close_code_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("code-panel") {
        w.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 2026-05-19 — slim message panel is its OWN window (separate from the
/// pet) so the pet window can stay exactly pet-sized & transparent (no
/// click-blocking dead area). This docks the message-panel flush to the
/// LEFT of the pet (`main`) window and keeps it glued there.
fn dock_message_panel_impl(app: &AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("message-panel")
        .ok_or("message-panel window missing")?;
    let main = app
        .get_webview_window("main")
        .ok_or("main window missing")?;
    let mpos = main.outer_position().map_err(|e| e.to_string())?;
    let psize = panel.outer_size().map_err(|e| e.to_string())?;
    // Panel's right edge == pet window's left edge; same Y as the pet.
    panel
        .set_position(tauri::PhysicalPosition::new(
            mpos.x - psize.width as i32,
            mpos.y,
        ))
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Reposition the message panel to stay attached to the pet. Called by
/// the pet window on every move (drag-follow). No-op if panel hidden.
#[tauri::command]
pub fn dock_message_panel(app: AppHandle) -> Result<(), String> {
    if let Some(p) = app.get_webview_window("message-panel") {
        if p.is_visible().unwrap_or(false) {
            dock_message_panel_impl(&app)?;
        }
    }
    Ok(())
}

/// Show the message panel docked to the pet's left, fronted above the
/// alwaysOnTop pet (same alwaysOnTop-pulse trick as open_code_panel).
#[tauri::command]
pub fn open_message_panel(app: AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("message-panel")
        .ok_or("message-panel window missing")?;
    dock_message_panel_impl(&app)?;
    panel.show().map_err(|e| e.to_string())?;
    let _ = panel.unminimize();
    // Pulse alwaysOnTop so it sits ABOVE the alwaysOnTop pet rather than
    // behind it. Don't steal focus (the pet keeps interaction).
    let _ = panel.set_always_on_top(true);
    Ok(())
}

/// Hide the message panel without destroying it (state/scrollback +
/// hash routing survive — reopen is a cheap show()).
#[tauri::command]
pub fn close_message_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("message-panel") {
        w.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// P4-S22: open a native folder picker; returns the absolute path the
/// user chose, or None if they cancelled. Used by the Code-mode entry
/// flow — the user picks "where my project lives" and we hand the path
/// to backend's ``code_mode_enter`` IPC.
#[tauri::command]
pub async fn open_directory_dialog(app: AppHandle) -> Result<Option<String>, String> {
    use std::sync::mpsc;
    use std::time::Duration;

    let (tx, rx) = mpsc::channel::<Option<String>>();

    app.dialog()
        .file()
        .pick_folder(move |path| {
            let s = path.and_then(|p| p.into_path().ok())
                .map(|p| p.to_string_lossy().to_string());
            let _ = tx.send(s);
        });

    // Block on the dialog response from the closure thread; cap at 5min
    // so a forgotten dialog doesn't pin the IPC handler forever.
    let result = rx
        .recv_timeout(Duration::from_secs(300))
        .map_err(|_| "directory picker timed out".to_string())?;
    Ok(result)
}
