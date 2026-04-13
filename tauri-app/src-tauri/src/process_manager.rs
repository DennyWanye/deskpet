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

    /// Kill the child process if it is running. Used during cleanup.
    pub fn kill_child(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

#[command]
pub fn start_backend(
    state: State<'_, BackendProcess>,
    python_path: String,
    backend_dir: String,
) -> Result<String, String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;

    if child_guard.is_some() {
        return Err("Backend already running".into());
    }

    let mut child = Command::new(&python_path)
        .arg("main.py")
        .current_dir(&backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to spawn backend: {e}"))?;

    let stdout = child.stdout.take().ok_or("No stdout")?;
    let reader = BufReader::new(stdout);

    let mut secret = String::new();
    for line in reader.lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.starts_with("SHARED_SECRET=") {
            secret = line.trim_start_matches("SHARED_SECRET=").to_string();
            break;
        }
    }

    if secret.is_empty() {
        let _ = child.kill();
        return Err("Failed to read shared secret from backend".into());
    }

    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *child_guard = Some(child);

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
