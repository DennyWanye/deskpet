// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Enable WebView2 DevTools Protocol on a fixed port so automation tools
    // (Playwright/Puppeteer over CDP) can drive the real app UI via proper
    // DOM selectors instead of screen-pixel heuristics. Debug-only: release
    // builds skip this so production never exposes a debug surface.
    #[cfg(debug_assertions)]
    {
        use std::env;
        if env::var_os("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS").is_none() {
            env::set_var(
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                "--remote-debugging-port=9222 --remote-allow-origins=*",
            );
        }
    }

    deskpet_lib::run()
}
