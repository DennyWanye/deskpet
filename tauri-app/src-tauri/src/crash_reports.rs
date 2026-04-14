//! Rust panic → file-based crash report (V5 §7.2).
//!
//! When a panic escapes a Tauri thread, we want a durable copy of the
//! backtrace in `crash_reports/`. The panic itself is still printed to
//! stderr via the chained default hook so `cargo run` output is unchanged.
//!
//! Why not just rely on the default panic behaviour?
//! - On Windows the app often runs detached from any console, so stderr
//!   is a black hole for users.
//! - Supporting "send crash report" in the config panel later needs an
//!   on-disk artefact.

use std::fs;
use std::panic;
use std::path::PathBuf;
use std::time::SystemTime;

fn crash_dir() -> PathBuf {
    // The Tauri app is launched from `tauri-app/src-tauri/target/...` during
    // dev and from the install dir in prod. We resolve relative to the
    // current working directory (set by Tauri to the app root) and, on
    // failure, fall back to the temp dir so the write still succeeds.
    std::env::current_dir()
        .map(|p| p.join("crash_reports"))
        .unwrap_or_else(|_| std::env::temp_dir().join("deskpet_crash_reports"))
}

fn timestamp() -> String {
    match SystemTime::now().duration_since(SystemTime::UNIX_EPOCH) {
        Ok(d) => format!("{}", d.as_secs()),
        Err(_) => "unknown".to_string(),
    }
}

/// Install a panic hook that writes a report file and then delegates to the
/// previously installed hook (default or custom). Idempotent.
pub fn install_panic_hook() {
    let previous = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        let dir = crash_dir();
        // best-effort dir create; failure path still prints stderr
        let _ = fs::create_dir_all(&dir);
        let file = dir.join(format!("rust-{}.log", timestamp()));

        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "<unknown>".to_string());
        let payload = info
            .payload()
            .downcast_ref::<&'static str>()
            .copied()
            .or_else(|| {
                info.payload()
                    .downcast_ref::<String>()
                    .map(|s| s.as_str())
            })
            .unwrap_or("<no payload>");

        let body = format!(
            "panic at {}\npayload: {}\nbacktrace: (set RUST_BACKTRACE=1 for detail)\n",
            location, payload
        );
        let _ = fs::write(&file, body);
        // Still print to stderr via the chained hook so dev output is
        // unchanged.
        previous(info);
    }));
}
