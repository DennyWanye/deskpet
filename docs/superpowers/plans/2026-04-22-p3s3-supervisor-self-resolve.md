# P3-S3 — Supervisor 自管 backend 路径

- **Slice**: Phase 3 / S3
- **上游**: P3-S1 (模型目录收拢)，P3-S2 (CUDA 前置检查) —— 均已合并
- **估工**: 3 天
- **目标分支**: `p3-s3-supervisor-self-resolve`（worktree）
- **合并目标**: `master`

---

## 1. 背景 / Why

**当前罪证**（`tauri-app/src/App.tsx:76-79`）：

```tsx
const secret = await core.invoke<string>("start_backend", {
  pythonPath: "G:/projects/deskpet/backend/.venv/Scripts/python.exe",
  backendDir: "G:/projects/deskpet/backend",
});
```

这两个绝对路径在打包产物里 100% 失效：
- `G:/projects/deskpet/` 不存在于用户机器
- 用户没有 `.venv`（PyInstaller 产物是 `deskpet-backend.exe`，不需要 python 解释器）
- 硬编码还被代码里 `TODO(bootstrap)` 注释明确标记为临时 dev 桥接

`process_manager::start_backend(python_path, backend_dir)` 现在的签名把"怎么找 backend"责任甩给前端，打包后没办法工作。P3-S4（PyInstaller）要做的第一件事就是换 spawn 目标；所以必须先把 Rust 侧变成路径权威源。

## 2. 范围

### In Scope
- [ ] Rust 侧新增 `backend_launch.rs`：`BackendLaunch` 枚举（`Bundled` / `Dev`）+ `resolve(app) -> Result<BackendLaunch, _>` 路径探测
- [ ] `process_manager::spawn_once` 改成接受 `&BackendLaunch`，按变体走不同 `Command`
- [ ] `start_backend` 命令签名**去掉参数** → `async fn start_backend(app, state) -> Result<String, String>`；内部调 resolver
- [ ] 前端 `App.tsx` invoke 不再传 `pythonPath` / `backendDir`
- [ ] Dev 模式 env 覆盖：`DESKPET_BACKEND_DIR` + `DESKPET_PYTHON`（resolver 优先读）
- [ ] Dev 模式默认值：用 `CARGO_MANIFEST_DIR` 在 build.rs 注入 `env!("CARGO_MANIFEST_DIR")`，回退相对路径 `../../backend` + `../../backend/.venv/Scripts/python.exe`
- [ ] 打包模式默认值：`app.path().resource_dir()? / "backend" / "deskpet-backend.exe"`（此路径 P3-S5 才真落地，但本 slice 先写上 fallback 逻辑，文件不存在返回 NotFound）
- [ ] 结构化错误：resolver 返回具体错误类型（`BackendExecutableMissing` / `PythonNotFound` / `DevRootNotFound`），supervisor 把错误 emit 给前端（给 P3-S8 splash screen 用）
- [ ] Rust 单测：`resolve()` 对每个 env 组合的行为 + fallback 顺序
- [ ] 前端 TS 类型定义同步更新（`start_backend` 无参）
- [ ] `docs/PACKAGING.md` 加一节"Backend 路径解析优先级"

### Out of Scope
- PyInstaller 真实产物（P3-S4）
- `deskpet-backend.exe` 放进 `bundle.resources`（P3-S5）
- Splash / dialog UI（P3-S8）
- AppData 迁移（P3-S7）
- 端口占用 / 超时错误路径（P3-S8 一起做）

---

## 3. 技术方案

### 3.1 路径解析优先级

| # | 条件 | 返回 |
|---|---|---|
| 1 | `sys._MEIPASS` 风格的 bundle 目录存在 `deskpet-backend.exe` | `Bundled { exe: ... }` |
| 2 | 环境变量 `DESKPET_BACKEND_DIR` 非空 | `Dev { backend_dir: env, python: env(DESKPET_PYTHON) 或 推断 }` |
| 3 | Dev fallback：`CARGO_MANIFEST_DIR/../../backend` 存在 | `Dev { ... }` |
| 4 | 都不中 | `Err(ResolveError::NoBackendFound)` |

### 3.2 新模块 `src-tauri/src/backend_launch.rs`

```rust
#[derive(Debug, Clone)]
pub enum BackendLaunch {
    Bundled { exe: PathBuf },                // spawn deskpet-backend.exe
    Dev { python: PathBuf, backend_dir: PathBuf },  // spawn python main.py
}

#[derive(Debug)]
pub enum ResolveError {
    NoBackendFound { tried: Vec<String> },    // 全部 fallback 都 miss
    DevPythonMissing(PathBuf),                 // python_path 指向不存在
    BundleExeMissing(PathBuf),                 // resource_dir 找不到 exe
}

pub fn resolve(app: &AppHandle) -> Result<BackendLaunch, ResolveError>;
pub fn format_user_message(err: &ResolveError) -> String;  // 中文弹窗文案
```

### 3.3 `process_manager` 改动

- `spawn_once` 变签名：`fn spawn_once(launch: &BackendLaunch) -> Result<(Child, String), String>`
- 内部 `match launch`：
  - `Bundled { exe }` → `Command::new(exe)` (CWD = exe 所在目录)
  - `Dev { python, backend_dir }` → `Command::new(python).arg("main.py").current_dir(backend_dir)`
- `start_backend` 签名：

  ```rust
  #[command]
  pub async fn start_backend(app: AppHandle, state: State<'_, BackendProcess>)
      -> Result<String, String>
  {
      let launch = backend_launch::resolve(&app).map_err(|e| format!("{e:?}"))?;
      // ...existing idempotency check...
      let (child, secret) = tauri::async_runtime::spawn_blocking(...)?;
      *state.launch.lock()? = Some(launch.clone());  // supervisor respawn 用
      // ...
  }
  ```

- `BackendProcess` 里原来的 `python_path` / `backend_dir` Mutex 两个字段合并成 `launch: Mutex<Option<BackendLaunch>>`

### 3.4 前端改动（`App.tsx`）

```tsx
// BEFORE
const secret = await core.invoke<string>("start_backend", {
  pythonPath: "G:/projects/deskpet/backend/.venv/Scripts/python.exe",
  backendDir: "G:/projects/deskpet/backend",
});

// AFTER
const secret = await core.invoke<string>("start_backend");
```

TODO 注释移除。

### 3.5 Dev 模式下 `CARGO_MANIFEST_DIR` 注入

`build.rs`（新建或扩展现有）：
```rust
fn main() {
    println!("cargo:rustc-env=DESKPET_DEV_ROOT={}",
             std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                 .parent().unwrap()  // tauri-app/
                 .parent().unwrap()  // repo root
                 .display());
    tauri_build::build();
}
```

Rust 里通过 `env!("DESKPET_DEV_ROOT")` 拿到 repo 根（**编译时常量**，debug 构建包进二进制；release 构建如果也注入可以作为 dev-fallback，但正常打包走 Bundled 分支就不会用到）。

---

## 4. 文件改动清单

### 新增
- `tauri-app/src-tauri/src/backend_launch.rs`（~150 行含 tests）
- `tauri-app/src-tauri/build.rs`（如果已有则扩展；新建约 10 行）

### 修改
- `tauri-app/src-tauri/src/lib.rs` — register mod
- `tauri-app/src-tauri/src/process_manager.rs` — 签名换成 `BackendLaunch`，state 字段合并
- `tauri-app/src/App.tsx` — invoke 去参
- `tauri-app/src/hooks/*` — 如有 types 同步
- `docs/PACKAGING.md` — 加 §5.5（或新 §6）"Backend 路径解析"

---

## 5. 测试计划

### 5.1 Rust 单测

`backend_launch::tests` 覆盖：
- `resolve()` 优先级：mock bundle_dir 存在 → 返回 `Bundled`
- env `DESKPET_BACKEND_DIR=/foo` → 返回 `Dev { backend_dir: /foo, ... }`
- 两个 env 都设 → `Dev` 用显式 python，不走推断
- 都未设 + bundle 不存在 → `Dev` 用 `DESKPET_DEV_ROOT` fallback
- 所有路径都 miss → `Err(NoBackendFound)`
- `format_user_message` 对每个 `ResolveError` 变体给中文片段

Test fixture：用 `tempfile::TempDir` 创建假 exe / 假 python，用 `serial_test` crate 给 env 测试加串行（避免 env 并发竞态）—— 或者用函数签名 `resolve_with(bundle_root, env_lookup)` 把 env 注入参数化，测试直接传闭包，完全避免真 env。**倾向后者**，更干净。

### 5.2 集成 / E2E

- `cargo test --lib` 全绿
- `npm run tauri:dev` 手测：
  - 默认（无 env）：走 Dev fallback，正常起 backend
  - 设 `DESKPET_BACKEND_DIR=D:\somewhere` 指到不存在目录：error dialog 或 log 里有明确"找不到 backend"
- pytest 无影响（backend 代码没改）

### 5.3 真机验证

`npm run tauri:dev` 重启一次，backend 仍正常起；`/health` 返回 `ok`。前端窗口能连 WS，对话正常 —— 等价 P2-2 回归。

---

## 6. 风险

| 风险 | 缓解 |
|---|---|
| `CARGO_MANIFEST_DIR` 在 release build 里指向 CI 的 clone 目录，打包产物带上这条路径显得怪 | dev fallback 仅在 `#[cfg(debug_assertions)]` 分支启用；release build 的 Dev 分支只能通过 env 触发 |
| env 覆盖引入不确定性 | 文档里显式写出优先级；resolver 日志里 print 最终选中的 variant + 路径 |
| 前端去参后，如果有别的调用方还在传参会编译报错 | Grep 确认 `start_backend` 唯一调用点在 `App.tsx:76`；TS 类型更新后 tsc 兜底 |
| `BackendProcess` 状态字段改动可能影响 `stop_backend` / `is_backend_running` | 这两条都只读 `child` + `shared_secret`，不碰 `python_path`/`backend_dir`；改动范围可控 |

---

## 7. 验收标准

- [ ] `cargo test --lib` 全绿（Rust 单测 +≥6 条）
- [ ] `pytest backend/tests/` 无回归（仍 298 passed）
- [ ] `cargo build --lib` clean
- [ ] `npm run tauri:dev` 启动 → Tauri 窗口出现 → backend 正常起 → 对话往返
- [ ] `App.tsx` 里 `start_backend` invoke 无参数，无硬编码绝对路径
- [ ] HANDOFF `docs/superpowers/handoffs/p3-s3-supervisor-self-resolve.md` 就位
- [ ] `STATE.md` P3-S3 行更新

---

## 8. 提交策略

5-6 个原子 commit：
1. `docs(plan): P3-S3 supervisor self-resolve slice plan`（本文）
2. `test(tauri): backend_launch resolver tests (red)`
3. `feat(tauri): backend_launch module + resolver`
4. `refactor(tauri): process_manager uses BackendLaunch enum`
5. `refactor(frontend): drop hardcoded python/backend paths from start_backend invoke`
6. `docs(P3-S3): PACKAGING §backend-launch + HANDOFF + STATE`

Merge 用 `--no-ff`。
