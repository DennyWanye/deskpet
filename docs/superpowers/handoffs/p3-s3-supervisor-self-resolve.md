# P3-S3 Supervisor 自管 backend 路径 — HANDOFF

**Slice:** Phase 3 Sprint 3 — Rust-side backend path resolution
**Branch:** `worktree-p3-s3-supervisor-self-resolve` → pending merge to `master`
**Status:** Code + cargo test + pytest + tsc DONE. **Manual E2E + merge PENDING**
**Plan:** `docs/superpowers/plans/2026-04-22-p3s3-supervisor-self-resolve.md`
**Parent roadmap:** `docs/superpowers/plans/2026-04-21-phase3-roadmap.md`

## Goal recap

P3-S2 前罪证（`tauri-app/src/App.tsx:76-79`）：

```tsx
const secret = await core.invoke<string>("start_backend", {
  pythonPath: "G:/projects/deskpet/backend/.venv/Scripts/python.exe",
  backendDir: "G:/projects/deskpet/backend",
});
```

两条绝对路径在任何非作者本机上都是废的。P3-S3 把 "怎么找 backend"
的责任从前端搬到 Rust：

- Rust `backend_launch::resolve(&app)` 是**唯一权威源**
- 按 bundle → env → compile-time dev-root 三级 fallback 定位
- `start_backend` 命令签名去参
- 前端 `invoke("start_backend")` 无参数

这是 P3-S4（PyInstaller `--onedir`）的先决条件 —— 届时只需把
`deskpet-backend.exe` 放进 bundle resources，优先级 1 自动生效，
resolver 以外的代码一行不用改。

## Commits

| # | SHA | Title |
|---|-----|-------|
| 1 | `b0bed16` | docs(plan): P3-S3 supervisor self-resolve slice plan |
| 2 | `347ff1a` | feat(tauri): P3-S3 backend_launch resolver module |
| 3 | `fe7f3b9` | refactor(tauri,frontend): P3-S3 drop hardcoded backend paths |
| 4 | (this commit) | docs(P3-S3): PACKAGING §6 + HANDOFF + STATE |

(TDD red+green co-commit：tests 和 impl 放在同一个 module 文件里，用
`#[cfg(test)] mod tests` 隔离；`resolve_with_fs` 的 DI 签名保证测试
不碰真 env / FS。故未分 red / green 两条 commit。)

## What changed

### New: `tauri-app/src-tauri/src/backend_launch.rs` (~330 lines incl. tests)

```rust
pub enum BackendLaunch {
    Bundled { exe: PathBuf },
    Dev { python: PathBuf, backend_dir: PathBuf },
}
pub enum ResolveError {
    NoBackendFound { tried: Vec<String> },
    DevPythonMissing(PathBuf),
    BundleExeMissing(PathBuf),   // reserved for P3-S5
}
pub fn resolve(app: &AppHandle) -> Result<BackendLaunch, ResolveError>;
pub fn resolve_with(bundle_root, env_lookup) -> Result<..>;
pub fn resolve_with_fs(bundle_root, env_lookup, exists: fn(&Path)->bool) -> Result<..>;
pub fn format_user_message(err) -> String;   // 中文弹窗文案
```

优先级：

1. `<bundle_root>/backend/deskpet-backend.exe` 存在 → `Bundled`
2. `DESKPET_BACKEND_DIR` 非空 env → `Dev`（`DESKPET_PYTHON` 覆盖，
   否则默认 `<dir>/.venv/Scripts/python.exe`；python 不存在时返回
   `DevPythonMissing`）
3. `option_env!("DESKPET_DEV_ROOT")/backend/main.py` 存在 → `Dev`
4. 都不中 → `NoBackendFound { tried: [...] }`

### `tauri-app/src-tauri/build.rs`

从 `fn main() { tauri_build::build() }` 扩展为注入
`cargo:rustc-env=DESKPET_DEV_ROOT=<CARGO_MANIFEST_DIR/../..>`。
`option_env!` 编译期拿到，dev 构建不需要任何环境变量即可工作。

### `tauri-app/src-tauri/src/process_manager.rs`

- `BackendProcess`：删掉 `python_path: Mutex<Option<String>>` 和
  `backend_dir: Mutex<Option<String>>` 两个字段，合并成
  `launch: Mutex<Option<BackendLaunch>>`
- `spawn_once(launch: &BackendLaunch)`：`match` 分发
  - `Bundled { exe }` → `Command::new(exe)`，cwd = exe parent
  - `Dev { python, backend_dir }` → `Command::new(python).arg("main.py")`，cwd = backend_dir
- `start_backend(app, state)`：去掉 `python_path` / `backend_dir` 两个
  参数，内部 `backend_launch::resolve(&app)?` 拿 launch
- `install_supervisor(app, launch: BackendLaunch)`：签名同步；
  respawn 用同一个 launch clone

### `tauri-app/src/App.tsx`

```diff
- const secret = await core.invoke<string>("start_backend", {
-   pythonPath: "G:/projects/deskpet/backend/.venv/Scripts/python.exe",
-   backendDir: "G:/projects/deskpet/backend",
- });
+ const secret = await core.invoke<string>("start_backend");
```

TODO(bootstrap) 注释移除。

### `docs/PACKAGING.md` — 新增 §6 Backend 路径解析

4 级优先级表 + dev/release 使用说明 + env 覆盖用法。

## Test results

- Rust: **18 passed** (was 8 post-P3-S2)
  - +10 `backend_launch::tests`：
    - `bundle_hit_returns_bundled_variant`
    - `bundle_miss_falls_through_to_env`
    - `env_backend_dir_sets_dev_variant`
    - `env_python_override_used_when_set_and_exists`
    - `env_python_missing_file_returns_err`
    - `empty_env_values_treated_as_unset`
    - `nothing_matches_returns_no_backend_found`
    - `format_no_backend_lists_tried_paths`
    - `format_python_missing_mentions_path`
    - `format_bundle_missing_prompts_reinstall`
- Python: **298 passed, 4 skipped** (无回归，P3-S3 未改 Python)
- `cargo build --lib`: clean (仅保留 P3-S2 的 dead_code allow)
- `npx tsc -b`: clean

## Known gaps / out-of-scope

- **`deskpet-backend.exe` 真实产物** — P3-S4 才 PyInstaller build
- **`bundle.resources` 里放 exe** — P3-S5
- **Splash / dialog UI for startup_status** — P3-S8
- **AppData 迁移** — P3-S7
- `BundleExeMissing` 变体目前 `#[allow(dead_code)]`，P3-S5 真正 bundle
  时会被 `resolve()` 构造：bundle_root 在 release build 里非空但 exe
  文件丢失 → 返回这个错（vs. 现在 fall through 到 Dev 因 dev build 里
  `resource_dir` 指向 cargo target，exe 天生不存在）

## How to verify

1. **单元测试**
   ```bash
   cd tauri-app/src-tauri && cargo test --lib
   # → 18 passed
   ```

2. **build clean**
   ```bash
   cargo build --lib     # 仅 P3-S2 已知 allow 警告
   npx tsc -b            # silent
   ```

3. **pytest 回归**
   ```bash
   cd backend && pytest
   # → 298 passed, 4 skipped
   ```

4. **Dev happy path** — `npm run tauri:dev` 从仓库根跑：
   - 不设任何 env → `DESKPET_DEV_ROOT` 走优先级 3 → backend 正常起
   - `curl http://127.0.0.1:8100/health` → `status: "ok"`
   - 前端窗口出现 → WS 连上 → 对话往返

5. **Dev env override** — `DESKPET_BACKEND_DIR=D:\nonexistent npm run tauri:dev`：
   - resolver 返回 `Dev { backend_dir: D:\nonexistent, python: D:\nonexistent\.venv\Scripts\python.exe }`
   - `spawn_once` 尝试启动时失败 → start_backend 返回 `Err` → 前端
     bootstrap catch 打 `[bootstrap] start_backend:` warn（未来 P3-S8
     会升级成 splash dialog）

6. **无 NVIDIA GPU** — 与 P3-S2 同路径，弹窗 + exit(1)，走不到 S3

## Merge plan

`git merge --no-ff worktree-p3-s3-supervisor-self-resolve` 到 master，
message 列出 4 条 commit。拓扑保持一致（与 P3-S1 / P3-S2 同）。
合并后 `git push origin master`。
