// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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
use std::time::Duration;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

use crate::process_manager::BackendProcess;

fn backend_http_client() -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        // This client only talks to the loopback FastAPI backend. System
        // proxies can hijack 127.0.0.1 and return empty 502 responses.
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
}

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
    let client = backend_http_client().map_err(|e| format!("reqwest client init: {e}"))?;

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

#[cfg(test)]
mod tests {
    use super::backend_http_client;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;
    use std::time::Duration;

    fn serve_once(response: &'static str) -> std::io::Result<(u16, thread::JoinHandle<()>)> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let handle = thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
                        let mut buf = [0_u8; 1024];
                        let _ = stream.read(&mut buf);
                        let _ = stream.write_all(response.as_bytes());
                        return;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        if std::time::Instant::now() >= deadline {
                            return;
                        }
                        thread::sleep(Duration::from_millis(20));
                    }
                    Err(_) => return,
                }
            }
        });
        Ok((port, handle))
    }

    fn with_proxy_env<T>(proxy: &str, f: impl FnOnce() -> T) -> T {
        let old_http = std::env::var("HTTP_PROXY").ok();
        let old_https = std::env::var("HTTPS_PROXY").ok();
        let old_all = std::env::var("ALL_PROXY").ok();
        let old_no_proxy = std::env::var("NO_PROXY").ok();
        let old_no_proxy_lower = std::env::var("no_proxy").ok();

        unsafe {
            std::env::set_var("HTTP_PROXY", proxy);
            std::env::set_var("HTTPS_PROXY", proxy);
            std::env::set_var("ALL_PROXY", proxy);
            std::env::remove_var("NO_PROXY");
            std::env::remove_var("no_proxy");
        }

        let result = f();

        unsafe {
            restore_env("HTTP_PROXY", old_http);
            restore_env("HTTPS_PROXY", old_https);
            restore_env("ALL_PROXY", old_all);
            restore_env("NO_PROXY", old_no_proxy);
            restore_env("no_proxy", old_no_proxy_lower);
        }

        result
    }

    unsafe fn restore_env(key: &str, value: Option<String>) {
        match value {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn backend_http_client_bypasses_proxy_for_loopback_backend() {
        let target_response = concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: application/json\r\n",
            "Content-Length: 11\r\n",
            "\r\n",
            "{\"ok\":true}"
        );
        let proxy_response = concat!(
            "HTTP/1.1 502 Bad Gateway\r\n",
            "Content-Length: 0\r\n",
            "\r\n"
        );

        let (target_port, target_handle) = serve_once(target_response).expect("target server");
        let (proxy_port, proxy_handle) = serve_once(proxy_response).expect("proxy server");
        let proxy_url = format!("http://127.0.0.1:{proxy_port}");

        let body = with_proxy_env(&proxy_url, || {
            tauri::async_runtime::block_on(async {
                backend_http_client()
                    .expect("client")
                    .get(format!("http://127.0.0.1:{target_port}/health"))
                    .send()
                    .await
                    .expect("request")
                    .text()
                    .await
                    .expect("body")
            })
        });

        drop(TcpStream::connect(("127.0.0.1", target_port)));
        drop(TcpStream::connect(("127.0.0.1", proxy_port)));
        let _ = target_handle.join();
        let _ = proxy_handle.join();

        assert_eq!(body, "{\"ok\":true}");
    }
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
    // 打开时 PULSE alwaysOnTop（true→false）：瞬间把面板顶到最前（含盖过桌宠），
    // 但**不长期置顶** —— 用户要求面板和桌宠一样是普通窗口，被别的聚焦窗口覆盖
    // （issue #3）。Don't steal focus (the pet keeps interaction).
    let _ = panel.set_always_on_top(true);
    let _ = panel.set_always_on_top(false);
    emit_panel_visibility(&app, true);
    Ok(())
}

/// Hide the message panel without destroying it (state/scrollback +
/// hash routing survive — reopen is a cheap show()).
#[tauri::command]
pub fn close_message_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("message-panel") {
        w.hide().map_err(|e| e.to_string())?;
    }
    emit_panel_visibility(&app, false);
    Ok(())
}

/// Notify the pet (`main`) window whenever the panel's visibility
/// changes — from the pet's ▶消息 toggle OR the panel's own ◀ — so the
/// pet can hide its bottom DialogBar while the panel is open (no
/// redundant double surface).
fn emit_panel_visibility(app: &AppHandle, visible: bool) {
    use tauri::Emitter;
    let _ = app.emit_to("main", "message-panel-visibility", visible);
}

/// #1 — single source of truth for the pet's ▶消息 button: show+dock if
/// hidden, hide if visible. Returns the NEW visibility so the pet can
/// update its tab label/DialogBar without guessing.
#[tauri::command]
pub fn toggle_message_panel(app: AppHandle) -> Result<bool, String> {
    let panel = app
        .get_webview_window("message-panel")
        .ok_or("message-panel window missing")?;
    let now_visible = panel.is_visible().unwrap_or(false);
    if now_visible {
        panel.hide().map_err(|e| e.to_string())?;
        emit_panel_visibility(&app, false);
        Ok(false)
    } else {
        dock_message_panel_impl(&app)?;
        panel.show().map_err(|e| e.to_string())?;
        let _ = panel.unminimize();
        // PULSE（见 open_message_panel）：开面板瞬间顶到最前，不长期置顶（issue #3）。
        let _ = panel.set_always_on_top(true);
        let _ = panel.set_always_on_top(false);
        emit_panel_visibility(&app, true);
        Ok(true)
    }
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
