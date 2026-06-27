# P3-S2 CUDA 前置检查 & ASR 启动错误结构化 — HANDOFF

**Slice:** Phase 3 Sprint 2 — NVIDIA precheck + structured startup errors
**Branch:** `worktree-p3-s2-cuda-precheck` → pending merge to `master`
**Status:** Code + pytest + cargo test DONE. **Manual E2E + merge PENDING**
**Plan:** `docs/superpowers/plans/2026-04-22-p3s2-cuda-precheck.md`
**Parent roadmap:** `docs/superpowers/plans/2026-04-21-phase3-roadmap.md`

## Goal recap

Phase 3 发行底线是 NVIDIA-only。P3-S1 前，如果用户机器没装 NVIDIA 驱动：
- Rust 完全不检查，直接拉起 Python
- Python lifespan 里 `WhisperModel(device="cuda")` 抛异常
- 异常被 `logger.warning` 吞掉
- backend 自称 startup 完成
- 前端连上后所有 ASR 请求 500，没有任何渠道告诉用户"你缺驱动"

这个 slice 把两头都补齐：
1. **Tauri 侧**：启动前用 NVML 探测 GPU，没有就弹窗 + `exit(1)`，backend 根本不 spawn
2. **Backend 侧**：`engine.load()` 失败时 **结构化**记录到 `StartupErrorRegistry`，
   通过 `/health` 和 WS `/ws/control` 首帧暴露给前端

## Commits

| # | SHA | Title |
|---|-----|-------|
| 1 | `d52e136` | docs(plan): P3-S2 CUDA precheck slice plan |
| 2 | `9cc46d4` | test(backend): startup error registry tests (red) |
| 3 | `7ecf81c` | feat(backend): startup registry + /health + WS startup_status |
| 4 | `55d35fb` | feat(tauri): gpu_check + setup-hook NVIDIA precheck |
| 5 | (this commit) | docs(P3-S2): PACKAGING §5 + HANDOFF + STATE 更新 |

(Red-phase Rust tests were included directly in commit 4 rather than a
separate red commit — `gpu_check.rs` and its `#[cfg(test)] mod tests`
ship together because the test-mode stub `detect_nvidia_gpu()` is part
of the module's structure.)

## What changed

### New: `backend/observability/startup.py`

```python
_classify(exc) -> (error_code, user_message)   # pure
class StartupErrorRegistry: record / snapshot / is_degraded / clear
registry = StartupErrorRegistry()              # module singleton
```

Error codes: `CUDA_UNAVAILABLE`, `MODEL_DIR_MISSING`, `UNKNOWN`.
Classification checks "no such file or directory" first (CTranslate2
masquerading pattern), then `FileNotFoundError`, then "cuda" substring.

### `backend/main.py`

- Lifespan: `except Exception as exc: ... startup_errors.record(name, exc)`
- `/health`: adds `startup_errors[]`; `status` flips `ok → degraded` on any entry
- `/ws/control`: sends `{type: "startup_status", degraded, errors}` as the
  FIRST frame after handshake (before any client send). Existing WS tests
  had to learn to drain this frame.

### New: `tauri-app/src-tauri/src/gpu_check.rs`

```rust
pub enum GpuCheckError { NvmlInitFailed(String), NoDevices, DeviceQueryFailed(String) }
pub fn detect_nvidia_gpu() -> Result<GpuInfo, GpuCheckError>
pub fn format_user_message(err: &GpuCheckError) -> String   // pure, tested
```

Real impl uses `nvml-wrapper` crate. Under `#[cfg(test)]` the function is
a stub returning `Err(NvmlInitFailed("test stub"))` so CI doesn't depend
on the dev box's driver.

### `tauri-app/src-tauri/src/lib.rs`

Setup hook now starts with:

```rust
if let Err(e) = gpu_check::detect_nvidia_gpu() {
    let msg = gpu_check::format_user_message(&e);
    app.dialog().message(msg).title(...).kind(MessageDialogKind::Error)
        .buttons(Ok).blocking_show();
    app.handle().exit(1);
    return Ok(());
}
```

`tauri_plugin_dialog::init()` was added to the plugin chain.

### `tauri-app/src-tauri/Cargo.toml`

New dependencies:
- `nvml-wrapper = "0.10"` — NVIDIA Management Library wrapper
- `tauri-plugin-dialog = "2"` — blocking MessageDialog

## Test results

- Python: **298 passed, 4 skipped** (was 283/4 pre-slice)
  - +13 new `test_startup_errors.py` (classify + registry)
  - +5 new `test_health_startup_errors.py` (integration /health + WS)
  - Patched 3 WS test files to drain `startup_status` first frame
- Rust: **8 passed** (was 3 pre-slice)
  - +5 new `gpu_check::tests` (all 3 error variants + non-empty messages
    + test-stub returns Err)
- `cargo build --lib` clean (one allowed-by-allow-attr dead_code on
  `GpuInfo` fields, read by P3-S3 UI)

## Known gaps / out-of-scope

- **前端 UI banner** for `startup_status` frame → P3-S3 owns
- **nvidia-smi.exe fallback** for NvmlInitFailed → deferred to P3-S3 if
  ever hit in practice; NVML and nvidia-smi share `nvml.dll`, so unlikely
- **`recommend_asr_device()` CPU fallback** still exists; not changed
  here because Rust precheck blocks the no-GPU case first. If a machine
  passes Rust but fails Python CUDA load, the structured error surfaces
  via `startup_errors`
- Driver version gate (CUDA 12+) → P3-S3

## How to verify

1. `cd backend && pytest` — should be 298 passed / 4 skipped
2. `cd tauri-app/src-tauri && cargo test --lib` — 8 passed
3. `cargo build --lib` — clean
4. Manual happy path: `npm run tauri:dev`, Rust precheck passes,
   backend spawns normally, `/health` has `startup_errors == []`,
   WS first frame is `{type: "startup_status", degraded: false, errors: []}`
5. Manual sad path (simulation): temporarily edit
   `backend/providers/faster_whisper_asr.py::load()` to `raise
   RuntimeError("CUDA driver is not available")`, restart backend,
   curl `/health` → `status: "degraded"`, connect WS → first frame
   carries `error_code: "CUDA_UNAVAILABLE"`

## Merge plan

`git merge --no-ff worktree-p3-s2-cuda-precheck` on master, message
listing the 5 commits. Same topology-preserving pattern as P3-S1.
No tag yet — phase3-rc1 tag lands after P3-S11.
