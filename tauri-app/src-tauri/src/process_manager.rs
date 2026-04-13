use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{command, State};

pub struct BackendProcess {
    child: Mutex<Option<Child>>,
    shared_secret: Mutex<Option<String>>,
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            shared_secret: Mutex::new(None),
        }
    }

    pub fn kill_child(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

/// Spawn backend — runs in async context so it won't block the UI thread.
#[command]
pub async fn start_backend(
    state: State<'_, BackendProcess>,
    python_path: String,
    backend_dir: String,
) -> Result<String, String> {
    // Check if already running
    {
        let guard = state.child.lock().map_err(|e| e.to_string())?;
        if guard.is_some() {
            return Err("Backend already running".into());
        }
    }

    // Spawn process (this is quick, doesn't block)
    let mut child = Command::new(&python_path)
        .arg("main.py")
        .current_dir(&backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to spawn backend: {e}"))?;

    let stdout = child.stdout.take().ok_or("No stdout")?;

    // Read the SHARED_SECRET line in a blocking task so we don't freeze the UI
    let secret = tauri::async_runtime::spawn_blocking(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(line) if line.starts_with("SHARED_SECRET=") => {
                    return Ok(line.trim_start_matches("SHARED_SECRET=").to_string());
                }
                Ok(_) => continue,
                Err(e) => return Err(format!("Failed to read stdout: {e}")),
            }
        }
        Err("Backend exited without printing SHARED_SECRET".into())
    })
    .await
    .map_err(|e| format!("Task join error: {e}"))??;

    // Store child and secret
    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *state.child.lock().map_err(|e| e.to_string())? = Some(child);

    Ok(secret)
}

#[command]
pub fn stop_backend(state: State<'_, BackendProcess>) -> Result<(), String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = child_guard.take() {
        child.kill().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[command]
pub fn is_backend_running(state: State<'_, BackendProcess>) -> Result<bool, String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    match child_guard.as_mut() {
        Some(child) => match child.try_wait() {
            Ok(Some(_)) => {
                *child_guard = None;
                Ok(false)
            }
            Ok(None) => Ok(true),
            Err(e) => Err(e.to_string()),
        },
        None => Ok(false),
    }
}

#[command]
pub fn get_shared_secret(state: State<'_, BackendProcess>) -> Result<String, String> {
    state
        .shared_secret
        .lock()
        .map_err(|e| e.to_string())?
        .clone()
        .ok_or("No secret available (backend not started?)".into())
}
