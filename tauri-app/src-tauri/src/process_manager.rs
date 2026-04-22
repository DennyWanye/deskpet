use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{command, AppHandle, Emitter, Manager, State};

use crate::backend_launch::{self, BackendLaunch};

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
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            shared_secret: Mutex::new(None),
            launch: Mutex::new(None),
            shutdown_requested: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
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
fn spawn_once(launch: &BackendLaunch) -> Result<(Child, String), String> {
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

    let mut reader = BufReader::new(stdout);
    let mut secret_opt: Option<String> = None;
    // Read until SHARED_SECRET line. Can't just `return` with the reader
    // dropped — on Windows that closes the pipe read end, and Python's next
    // stdout write (structlog "preloading models..." etc.) raises
    // OSError[Errno 22] and crashes lifespan. Instead hand the reader off
    // to a background thread that keeps draining (and discarding) stdout
    // for the lifetime of the child.
    let mut line_buf = String::new();
    loop {
        line_buf.clear();
        match reader.read_line(&mut line_buf) {
            Ok(0) => break, // EOF — backend exited
            Ok(_) => {
                let trimmed = line_buf.trim_end_matches(['\r', '\n']);
                if trimmed.starts_with("SHARED_SECRET=") {
                    secret_opt = Some(
                        trimmed.trim_start_matches("SHARED_SECRET=").to_string(),
                    );
                    break;
                }
            }
            Err(e) => return Err(format!("Failed to read backend stdout: {e}")),
        }
    }

    let secret = secret_opt
        .ok_or_else(|| "Backend exited without printing SHARED_SECRET".to_string())?;

    // Keep draining stdout in a detached thread so the pipe stays open and
    // Python can keep logging. Exits naturally on EOF when the child dies.
    std::thread::spawn(move || {
        let mut sink = String::new();
        loop {
            sink.clear();
            match reader.read_line(&mut sink) {
                Ok(0) | Err(_) => return,
                Ok(_) => {}
            }
        }
    });

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
    let launch = backend_launch::resolve(&app)
        .map_err(|e| backend_launch::format_user_message(&e))?;

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
    let (child, secret) = tauri::async_runtime::spawn_blocking(move || {
        spawn_once(&launch_for_spawn)
    })
    .await
    .map_err(|e| format!("Task join error: {e}"))??;

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
