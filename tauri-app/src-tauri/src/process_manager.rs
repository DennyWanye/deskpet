use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{command, AppHandle, Emitter, Manager, State};

use crate::backend_launch::{self, BackendLaunch};

/// P3-S8: hard cap on how long we'll wait for the backend to print its
/// SHARED_SECRET line. Cold boot on a fresh machine (torch + CUDA init +
/// whisper load) has measured 30–45 s in P3-S4 profiling, so 90 s gives
/// generous headroom while still failing fast when the backend is
/// actually wedged (e.g. Python import error, missing DLL).
const SECRET_TIMEOUT_SECS: u64 = 90;

/// Backend FastAPI port. Hard-coded on both sides of the handshake;
/// we only use this constant for the pre-spawn "is it already taken?"
/// probe so a more useful error message reaches the user.
const BACKEND_PORT: u16 = 8100;

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
    /// P3-S3: single source of truth for *how* to spawn the backend,
    /// replacing the old `python_path` + `backend_dir` string pair.
    /// Populated by `start_backend` from `backend_launch::resolve(app)`;
    /// the supervisor reads it back to respawn with identical args.
    launch: Mutex<Option<BackendLaunch>>,
    /// Set true by stop_backend / window-destroy. The supervisor reads
    /// this to distinguish "user asked to stop" from "process crashed".
    shutdown_requested: Arc<AtomicBool>,
    /// Incremented every time the supervisor respawns. Reset when the
    /// child has been up longer than RESTART_WINDOW_SECS.
    restart_count: Arc<AtomicU32>,
    /// P3-S8: last human-readable start-up error so the frontend can
    /// render a dialog even if the Err from start_backend already got
    /// swallowed by a React effect retry.
    startup_error: Mutex<Option<String>>,
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            shared_secret: Mutex::new(None),
            launch: Mutex::new(None),
            shutdown_requested: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
            startup_error: Mutex::new(None),
        }
    }

    /// P3-S8: expose latest startup failure to the frontend.
    pub fn set_startup_error(&self, msg: Option<String>) {
        if let Ok(mut guard) = self.startup_error.lock() {
            *guard = msg;
        }
    }

    /// Fire-and-forget teardown used by the window-destroyed handler.
    /// Sets `shutdown_requested` so any in-flight supervisor bails, kills
    /// the child if we still have a direct handle, and clears
    /// `shared_secret` so the next `start_backend` invocation is treated
    /// as a fresh spawn rather than a no-op idempotent return.
    pub fn kill_child(&self) {
        self.shutdown_requested.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
        if let Ok(mut guard) = self.shared_secret.lock() {
            *guard = None;
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
/// P3-S8: quick precheck that 8100 is free. Returning Err early here
/// swaps the generic "Backend exited without printing SHARED_SECRET"
/// failure for the far more actionable "端口已被占用". We bind+drop on
/// 127.0.0.1:PORT; if another process has the port we get an OS error.
fn check_port_free(port: u16) -> Result<(), String> {
    match TcpListener::bind(("127.0.0.1", port)) {
        Ok(l) => {
            drop(l);
            Ok(())
        }
        Err(e) => Err(format!(
            "端口 {port} 已被其它程序占用（错误：{e}）。\n\
             请关闭其它 DeskPet 实例或占用该端口的程序后重试。"
        )),
    }
}

fn spawn_once(launch: &BackendLaunch) -> Result<(Child, String), String> {
    // P3-S8: port precheck. Have to do it here (not only in start_backend)
    // because the supervisor respawn path also goes through spawn_once —
    // if a zombie backend survived a window close, we want the friendly
    // message on every retry, not just the initial attempt.
    check_port_free(BACKEND_PORT)?;

    let mut cmd = match launch {
        BackendLaunch::Bundled { exe } => {
            let mut c = Command::new(exe);
            // cwd = exe's directory so the frozen PyInstaller onedir can
            // find its bundled _internal/ next to it.
            if let Some(parent) = exe.parent() {
                c.current_dir(parent);
            }
            c
        }
        BackendLaunch::Dev { python, backend_dir } => {
            let mut c = Command::new(python);
            c.arg("main.py").current_dir(backend_dir);
            c
        }
    };
    cmd.stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        // Windows 下 structlog 写入 piped stdout 时如果系统默认非 UTF-8
        // （GBK/CP936）会对中文日志抛 OSError[Errno 22]，backend 立刻崩在
        // lifespan 的 "preloading models..." 行。手动钉死 UTF-8 + 无缓冲，
        // 这样 SHARED_SECRET 也能被及时读到。
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1");

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

    // P3-S8: wall-clock timeout on SHARED_SECRET. The previous version
    // read_line'd on the main thread, which blocks forever if Python hangs
    // mid-import (we've seen this with missing CUDA DLLs). Move the reader
    // to a worker thread and recv_timeout from a channel — the worker
    // keeps the pipe open (crucial on Windows, see the long comment below)
    // and we flip it into "drain silently" mode after the secret arrives.
    //
    // Why we must NOT drop the reader: on Windows, closing the pipe read
    // end makes Python's next stdout write (structlog "preloading
    // models..." etc.) raise OSError[Errno 22] and crash lifespan. The
    // worker thread therefore owns the reader for the child's full life.
    let reader = BufReader::new(stdout);
    let (tx, rx) = mpsc::channel::<Result<String, String>>();
    std::thread::spawn(move || {
        let mut reader = reader;
        let mut line_buf = String::new();
        let mut secret_sent = false;
        loop {
            line_buf.clear();
            match reader.read_line(&mut line_buf) {
                Ok(0) => {
                    if !secret_sent {
                        let _ = tx.send(Err(
                            "Backend exited without printing SHARED_SECRET".into()
                        ));
                    }
                    return;
                }
                Ok(_) => {
                    let trimmed = line_buf.trim_end_matches(['\r', '\n']);
                    if !secret_sent && trimmed.starts_with("SHARED_SECRET=") {
                        let s = trimmed.trim_start_matches("SHARED_SECRET=").to_string();
                        let _ = tx.send(Ok(s));
                        secret_sent = true;
                        // Keep draining silently from here on.
                    }
                }
                Err(e) => {
                    if !secret_sent {
                        let _ = tx.send(Err(format!(
                            "Failed to read backend stdout: {e}"
                        )));
                    }
                    return;
                }
            }
        }
    });

    let secret = match rx.recv_timeout(Duration::from_secs(SECRET_TIMEOUT_SECS)) {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            let _ = child.kill();
            return Err(e);
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            return Err(format!(
                "Backend 启动超时（{SECRET_TIMEOUT_SECS}s 内未上报 SHARED_SECRET）。\n\
                 常见原因：CUDA / 模型加载失败。请打开日志目录排查。"
            ));
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            let _ = child.kill();
            return Err("Backend stdout pipe disconnected before SHARED_SECRET".into());
        }
    };

    Ok((child, secret))
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
) -> Result<String, String> {
    // P3-S3: Rust now resolves the backend path itself. Frontend no
    // longer passes python_path / backend_dir — those hardcoded values
    // were dev-box-only. See `backend_launch::resolve` for priority.
    let launch = match backend_launch::resolve(&app) {
        Ok(l) => l,
        Err(e) => {
            let msg = backend_launch::format_user_message(&e);
            state.set_startup_error(Some(msg.clone()));
            return Err(msg);
        }
    };

    // P3-S5: log which branch resolved so e2e smoke scripts can grep
    // the dev log to confirm Bundled vs Dev path was picked. Use stderr
    // because tauri dev redirects both streams to the same log file.
    match &launch {
        BackendLaunch::Bundled { exe } => {
            eprintln!("[backend_launch] Bundled exe={}", exe.display());
        }
        BackendLaunch::Dev { python, backend_dir } => {
            eprintln!(
                "[backend_launch] Dev python={} backend_dir={}",
                python.display(),
                backend_dir.display(),
            );
        }
    }

    // 幂等：以 shared_secret 作为"已有一个活着或正在重启中的 backend"的
    // 真实判据，而不是 state.child。原因：install_supervisor 在调 wait()
    // 之前会把 Child 从 Mutex 里 take() 出来持有在线程栈上，所以 backend
    // 活着的 99.99% 时间里 state.child 都是 None。只有 shared_secret 是
    // 在"启动 / respawn 成功"时 Some，"stop / kill_child / supervisor 放弃"
    // 时 None —— 这三条关闭路径都会显式清空它，语义一致。
    //
    // 效果：前端 F5 / StrictMode 重挂载里无脑 invoke start_backend 不会
    // 再次 spawn 出一个和现任 backend 抢 8100 端口的 Python。
    {
        let secret_guard = state.shared_secret.lock().map_err(|e| e.to_string())?;
        if let Some(existing) = secret_guard.as_ref() {
            return Ok(existing.clone());
        }
    }

    // Clone up-front so the supervisor owns its own copy.
    let launch_for_spawn = launch.clone();

    // Initial spawn runs on a blocking thread — the BufReader loop that
    // waits for SHARED_SECRET is blocking I/O.
    let spawn_result = tauri::async_runtime::spawn_blocking(move || {
        spawn_once(&launch_for_spawn)
    })
    .await
    .map_err(|e| format!("Task join error: {e}"))?;
    let (child, secret) = match spawn_result {
        Ok(pair) => pair,
        Err(e) => {
            state.set_startup_error(Some(e.clone()));
            return Err(e);
        }
    };
    // Clear any stale error from a prior failed attempt.
    state.set_startup_error(None);

    // Record launch so the supervisor can respawn.
    *state.launch.lock().map_err(|e| e.to_string())? = Some(launch.clone());
    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *state.child.lock().map_err(|e| e.to_string())? = Some(child);
    state.shutdown_requested.store(false, Ordering::SeqCst);
    state.restart_count.store(0, Ordering::SeqCst);

    // Install the supervisor. It keeps its own Arc handles to the shared
    // atomics and the BackendProcess state (via AppHandle::state()).
    install_supervisor(app, launch);

    Ok(secret)
}

/// Background loop: wait for the current child to exit; if the user
/// didn't ask for a shutdown, respawn. Emits lifecycle events so the
/// frontend can re-fetch the secret and reconnect its WebSockets.
fn install_supervisor(app: AppHandle, launch: BackendLaunch) {
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
                // 清空 secret —— 之后前端再 invoke start_backend 时
                // 幂等检查要能让它真的去 spawn 一个新 backend（手动自愈）。
                if let Ok(mut guard) = state.shared_secret.lock() {
                    *guard = None;
                }
                let _ = app.emit("backend-dead", "restart budget exhausted");
                return;
            }

            std::thread::sleep(Duration::from_millis(RESTART_COOLDOWN_MS));

            // Respawn.
            let started = Instant::now();
            let spawn_result = spawn_once(&launch);
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
                    // 同样的道理：respawn 失败等于这一生的 backend 到此
                    // 为止了，secret 得清掉好让后续手动重启能真的 spawn。
                    if let Ok(mut guard) = state.shared_secret.lock() {
                        *guard = None;
                    }
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
    // 让幂等检查恢复"无 backend"判断，好让之后的 start_backend 能真正
    // spawn，而不是返回这条已经被杀死的 stale secret。
    *state.shared_secret.lock().map_err(|e| e.to_string())? = None;
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

/// P3-S8 — returns the last classified startup error (human-readable
/// Chinese) or None. Frontend polls this after start_backend rejects
/// and also on window focus so crashes surfaced via `backend-dead` can
/// be re-displayed.
#[command]
pub fn get_startup_error(state: State<'_, BackendProcess>) -> Result<Option<String>, String> {
    Ok(state.startup_error.lock().map_err(|e| e.to_string())?.clone())
}

/// P3-S8 — clear the recorded startup error once the dialog is dismissed
/// so a successful retry doesn't re-show it.
#[command]
pub fn clear_startup_error(state: State<'_, BackendProcess>) -> Result<(), String> {
    *state.startup_error.lock().map_err(|e| e.to_string())? = None;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn port_precheck_reports_bound_port() {
        // Bind an ephemeral port, then assert check_port_free on that
        // port fails with a user-readable Chinese message.
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let err = check_port_free(port).expect_err("expected busy port to fail");
        assert!(err.contains(&port.to_string()), "error missing port: {err}");
        assert!(err.contains("占用"), "error not chinese: {err}");
    }

    #[test]
    fn port_precheck_passes_on_free_port() {
        // Pick a random high port, bind+drop to learn it's free, then
        // re-check. Race-y in theory; in practice fine for unit tests.
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert!(check_port_free(port).is_ok());
    }

    #[test]
    fn startup_error_round_trip() {
        let bp = BackendProcess::new();
        assert!(bp.startup_error.lock().unwrap().is_none());
        bp.set_startup_error(Some("boom".into()));
        assert_eq!(bp.startup_error.lock().unwrap().as_deref(), Some("boom"));
        bp.set_startup_error(None);
        assert!(bp.startup_error.lock().unwrap().is_none());
    }
}
