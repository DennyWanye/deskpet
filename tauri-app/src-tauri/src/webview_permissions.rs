//! WebView2 permission handler — auto-grants microphone (and camera) to the
//! embedded WebView2 so `navigator.mediaDevices.getUserMedia` works inside the
//! desktop-pet window without any user prompt.
//!
//! Windows-only. No-op on other platforms.

#[cfg(target_os = "windows")]
pub fn grant_media_permissions(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_CAMERA,
        COREWEBVIEW2_PERMISSION_KIND_MICROPHONE, COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    };
    use webview2_com::PermissionRequestedEventHandler;

    window.with_webview(|webview| {
        unsafe {
            let controller = webview.controller();
            let core = match controller.CoreWebView2() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[permissions] failed to get CoreWebView2: {e:?}");
                    return;
                }
            };

            let handler =
                PermissionRequestedEventHandler::create(Box::new(|_sender, args| {
                    if let Some(args) = args {
                        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                        args.PermissionKind(&mut kind)?;
                        if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE
                            || kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA
                        {
                            args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                            eprintln!(
                                "[permissions] auto-granted media permission (kind={:?})",
                                kind
                            );
                        }
                    }
                    Ok(())
                }));

            let mut token: i64 = 0;
            if let Err(e) = core.add_PermissionRequested(&handler, &mut token) {
                eprintln!("[permissions] add_PermissionRequested failed: {e:?}");
            } else {
                eprintln!("[permissions] PermissionRequested handler installed");
            }
        }
    })?;
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn grant_media_permissions(_window: &tauri::WebviewWindow) -> tauri::Result<()> {
    Ok(())
}
