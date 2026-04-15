use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{command, AppHandle, Emitter, Manager, State};

/// How many times the supervisor will respawn a crashed backend before it
/// gives up. Hitting this likely means a config bug, not a transient fault.
const MAX_RESTARTS_PER_WINDOW: u32 = 5;

/// Sliding window for `MAX_RESTARTS_PER_WINDOW`. If uptime between crashes
/// exceeds this, the restart counter resets — so a daily intermittent crash
/// doesn't eventually trigger "give up".
const RESTART_WINDOW_SECS: u64 = 60;

/// Cooldown between a crash and the next spawn attempt. Kept short so we
/// meet the V5 §1.1 "crash self-heal within 10s" bar even after counting
/// the time it takes the child to actually exit.
const RESTART_COOLDOWN_MS: u64 = 2_000;

pub struct BackendProcess {
    child: Mutex<Option<Child>>,
    shared_secret: Mutex<Option<String>>,
    /// Persisted so the supervisor can respawn with the same args the
    /// user originally provided. Empty strings mean "no supervisor task
    /// installed yet".
    python_path: Mutex<Option<String>>,
    backend_dir: Mutex<Option<String>>,
    /// Set true by stop_backend / window-destroy. The supervisor reads
    /// this to distinguish "user asked to stop" from "process crashed".
    shutdown_requested: Arc<AtomicBool>,
    /// Incremented every time the supervisor respawns. Reset when the
    /// child has been up longer than RESTART_WINDOW_SECS.
    restart_count: Arc<AtomicU32>,
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            shared_secret: Mutex::new(None),
            python_path: Mutex::new(None),
            backend_dir: Mutex::new(None),
            shutdown_requested: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
        }
    }

    /// Fire-and-forget teardown used by the window-destroyed handler.
    /// Sets `shutdown_requested` so any in-flight supervisor bails.
    pub fn kill_child(&self) {
        self.shutdown_requested.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

/// Spawn the Python backend and read its SHARED_SECRET announcement.
/// Shared logic between the initial `start_backend` command and the
/// supervisor's respawn path.
///
/// P2-1-S3: before spawning we peek at the OS keychain via `secrets::`
/// and, if a cloud LLM API key is configured, inject it as
/// `DESKPET_CLOUD_API_KEY` so `backend/main.py::_resolve_cloud_api_key`
/// can find it. When nothing is saved the backend logs "cloud disabled"
/// and carries on local-only — that's the documented first-launch flow.
fn spawn_once(
    python_path: &str,
    backend_dir: &str,
) -> Result<(Child, String), String> {
    let mut cmd = Command::new(python_path);
    cmd.arg("main.py")
        .current_dir(backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    // Read key from keychain. Errors are logged to stderr but don't
    // block startup — if the keychain itself is busted, local-only is
    // still useful.
    match crate::secrets::get_cloud_api_key() {
        Ok(Some(key)) if !key.is_empty() => {
            cmd.env("DESKPET_CLOUD_API_KEY", key);
        }
        Ok(_) => {
            // Not configured — backend will skip cloud provider init.
        }
        Err(e) => {
            eprintln!(
                "[process_manager] warning: could not read cloud API key from keychain: {e}"
            );
        }
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn backend: {e}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "No stdout on spawned backend".to_string())?;

    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        match line {
            Ok(line) if line.starts_with("SHARED_SECRET=") => {
                let secret = line.trim_start_matches("SHARED_SECRET=").to_string();
                return Ok((child, secret));
            }
            Ok(_) => continue,
            Err(e) => return Err(format!("Failed to read backend stdout: {e}")),
        }
    }
    Err("Backend exited without printing SHARED_SECRET".into())
}

/// Spawn backend — runs in async context so it won't block the UI thread.
///
/// After the child process is up, installs a supervisor task that waits
/// on it. If the child exits without the user asking to stop, the
/// supervisor respawns with the same args after RESTART_COOLDOWN_MS.
/// The frontend is notified via the `backend-crashed` / `backend-restarted`
/// / `backend-dead` events so it can refresh its shared-secret cache.
#[command]
pub async fn start_backend(
    app: AppHandle,
    state: State<'_, BackendProcess>,
    python_path: String,
    backend_dir: String,
) -> Result<String, String> {
    {
        let guard = state.child.lock().map_err(|e| e.to_string())?;
        if guard.is_some() {
            return Err("Backend already running".into());
        }
    }

    // Clone the args up-front so the supervisor owns its own copies.
    let py = python_path.clone();
    let dir = backend_dir.clone();

    // Initial spawn runs on a blocking thread — the BufReader loop that
    // waits for SHARED_SECRET is blocking I/O.
    let (child, secret) = tauri::async_runtime::spawn_blocking(move || {
        spawn_once(&py, &dir)
    })
    .await
    .map_err(|e| format!("Task join error: {e}"))??;

    // Record args so the supervisor can respawn.
    *state.python_path.lock().map_err(|e| e.to_string())? = Some(python_path.clone());
    *state.backend_dir.lock().map_err(|e| e.to_string())? = Some(backend_dir.clone());
    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *state.child.lock().map_err(|e| e.to_string())? = Some(child);
    state.shutdown_requested.store(false, Ordering::SeqCst);
    state.restart_count.store(0, Ordering::SeqCst);

    // Install the supervisor. It keeps its own Arc handles to the shared
    // atomics and the BackendProcess state (via AppHandle::state()).
    install_supervisor(app, python_path, backend_dir);

    Ok(secret)
}

/// Background loop: wait for the current child to exit; if the user
/// didn't ask for a shutdown, respawn. Emits lifecycle events so the
/// frontend can re-fetch the secret and reconnect its WebSockets.
fn install_supervisor(app: AppHandle, python_path: String, backend_dir: String) {
    std::thread::spawn(move || {
        loop {
            // Wait for the current child to exit. We have to release the
            // mutex while waiting so stop_backend can still seize the lock.
            let exit_status = {
                let state = app.state::<BackendProcess>();
                let mut guard = match state.child.lock() {
                    Ok(g) => g,
                    Err(_) => return,
                };
                let Some(mut child) = guard.take() else {
                    // Someone already removed the child (stop_backend or
                    // window-destroy). Supervisor's job is done.
                    return;
                };
                drop(guard);
                child.wait()
            };

            let state = app.state::<BackendProcess>();
            if state.shutdown_requested.load(Ordering::SeqCst) {
                // Clean shutdown — don't respawn.
                return;
            }

            // The child died on its own. Tell the frontend so it can
            // drop its WS connections; the restart path will give it a
            // new secret to reconnect with.
            let reason = match &exit_status {
                Ok(status) => format!("exit:{status}"),
                Err(e) => format!("wait_err:{e}"),
            };
            let _ = app.emit("backend-crashed", reason);

            // Restart budget: if we've crashed too many times in the
            // sliding window, give up and let the user manually retry.
            let count = state.restart_count.fetch_add(1, Ordering::SeqCst) + 1;
            if count > MAX_RESTARTS_PER_WINDOW {
                let _ = app.emit("backend-dead", "restart budget exhausted");
                return;
            }

            std::thread::sleep(Duration::from_millis(RESTART_COOLDOWN_MS));

            // Respawn.
            let started = Instant::now();
            let spawn_result = spawn_once(&python_path, &backend_dir);
            match spawn_result {
                Ok((new_child, new_secret)) => {
                    if let Ok(mut guard) = state.shared_secret.lock() {
                        *guard = Some(new_secret.clone());
                    }
                    if let Ok(mut guard) = state.child.lock() {
                        *guard = Some(new_child);
                    }
                    let _ = app.emit("backend-restarted", new_secret);

                    // If the previous life lasted longer than the
                    // restart window, zero the counter — a sporadic
                    // crash months apart shouldn't accumulate.
                    if started.elapsed() > Duration::from_secs(RESTART_WINDOW_SECS) {
                        state.restart_count.store(0, Ordering::SeqCst);
                    }
                }
                Err(e) => {
                    let _ = app.emit("backend-dead", format!("respawn failed: {e}"));
                    return;
                }
            }
        }
    });
}

#[command]
pub fn stop_backend(state: State<'_, BackendProcess>) -> Result<(), String> {
    state.shutdown_requested.store(true, Ordering::SeqCst);
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
