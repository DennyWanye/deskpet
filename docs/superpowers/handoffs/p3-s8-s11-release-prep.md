# HANDOFF — P3-S8 ~ P3-S11（启动 UI + 卸载清理 + installer 验收 + rc1 准备）

**Date**: 2026-04-23
**Branch**: `p3-s8-s11-release`
**Status**: cargo 35/35 + pytest 317/321 + frozen smoke 4.3s + Tauri E2E PASS；待用户 UI 肉眼验收 splash / 错误对话框 / 完全卸载后 merge

---

## 1. 一句话

一次性把 Phase 3 尾段四个 slice（启动时序 UI / 卸载清理 / installer 验收 runbook / 版本号准备）推到"rc1 可发布"状态，等 VM 实测后即可 tag `v0.5.0-phase3-rc1`。

## 2. 改动清单

### S8 — 启动时序 + 错误 UI

**Rust (`tauri-app/src-tauri/src/`)**：

- **`paths.rs`（新）** — 镜像 `backend/paths.py` 的路径约定：
  - `user_data_dir_with(base, env)` / `user_log_dir_with` / `user_models_dir_with` 纯函数
  - `BaseDirs::from_env()` 读 `APPDATA` / `LOCALAPPDATA`；`cfg(test)` 下返回空以防渗入真 AppData
  - 便捷 `user_data_dir()` / `user_log_dir()` / `user_models_dir()` 公开函数
  - `ensure_dir(path)`
  - 8 条单测覆盖 env override / 空串 / 缺失基 dir 分支
- **`user_data.rs`（新）** — UI 可调用的 3 个命令：
  - `open_log_dir` → `tauri-plugin-opener` 打开 `%AppData%\deskpet\logs`
  - `open_app_data_dir` → 打开 `%AppData%\deskpet`
  - `purge_user_data(include_models: bool)` → 递归删 AppData（可选同时删 LocalAppData/models 的 junction），完事 `app.exit(0)`
  - **安全守卫** `looks_safe_to_delete()`: 路径组件 ≥ 4 且 basename ∈ {deskpet, models, logs}；拒绝 `C:\`、`C:\Users\X`、`…\Roaming` 等模糊目标
  - **Windows junction-aware 删除**：`symlink_metadata().file_type().is_symlink()` 判断，junction 走 `remove_dir` 不递归，避免删到 dev 的 `backend/models/`
  - 6 条单测：根、浅路径、AppData 根、deskpet、models、logs
- **`process_manager.rs`**：
  - 新增 `startup_error: Mutex<Option<String>>` 字段 + `set_startup_error` 方法
  - 新增 `check_port_free(8100)`：`TcpListener::bind` 试绑 + 立即 drop；失败返回中文"端口 8100 已被其它程序占用…"
  - `spawn_once` 先调 port precheck；失败早报错（比"Backend exited without printing SHARED_SECRET"具体 100 倍）
  - SHARED_SECRET 读取从主线程 blocking `read_line` 改为 **worker thread + mpsc channel + `recv_timeout(90s)`**：
    - 超时 → kill child + 返回中文错误"Backend 启动超时… 常见原因：CUDA / 模型加载失败"
    - worker 拿到 secret 后继续"静默排空" stdout，不关闭 pipe（Windows 上关了会让 Python 下次 print 抛 OSError[Errno 22]）
  - `start_backend` 所有失败路径都 `set_startup_error(Some(msg))`；成功时清零
  - 新增 `get_startup_error` / `clear_startup_error` command
  - 新增 3 条 `#[cfg(test)]`：port 空闲/占用、startup_error round-trip
- **`lib.rs`**：注册新 module + 5 个新 command

**Frontend (`tauri-app/src/`)**：

- **`components/StartupOverlay.tsx`（新）** — in-DOM 覆盖层（不用 plugin-dialog 的 blocking_show，避免阻塞 WebView2 + transparent 窗口导致黑屏）：
  - `bootState==="starting"` → 旋转 spinner + "正在启动语音服务…" + "首次启动需要 20–60 秒加载模型"
  - `bootState==="failed"` → 红色标题 "启动失败" + `<pre>` 错误消息 + 3 按钮：重试 / 打开日志目录 / 退出
  - `memo` 包装；zIndex 5000 盖住 Live2D canvas
- **`App.tsx`**：
  - 新增 `bootState`/`bootError`/`bootAttempt` 三态机
  - bootstrap useEffect 依赖 `bootAttempt`：抛错时 `invoke("get_startup_error")` 取 Rust 缓存的更友好消息，否则用 JS 侧 Error.message；state 转 `failed`
  - `handleBootRetry` / `handleBootOpenLog` / `handleBootExit` 三个 callback 绑定到覆盖层按钮
  - `useBackendLifecycle` 的 `dead` 事件也走统一错误卡片（之前只 console.warn）
  - Browser 环境（无 `@tauri-apps/api/core`）下直接 `setBootState("ready")` 不影响 vite dev

### S9 — 卸载残留清理

- 已在 `user_data.rs::purge_user_data` 完成（见上）
- **`components/SettingsPanel.tsx`**：末尾新增 `DangerZoneSection` 组件：
  - 红色 border-top
  - 复选框 "同时删除 `%LocalAppData%\deskpet\models`（模型缓存 ~9 GB）"
  - 按钮 "完全卸载（清除用户数据）" → `window.confirm` 两步确认 → `invoke("purge_user_data", { includeModels })`
  - 错误直接展示在面板；成功后 Rust 自动退出 app
- NSIS/WiX 默认行为已天然满足"只删 Program Files，不删 AppData"，仅 runbook 里确认

### S10 — Installer smoke runbook

- **`docs/P3-S10-installer-smoke-runbook.md`（新）** — T0-T8 端到端 checklist：
  - T0 VM 基线（系统/AppData 空）
  - T1 NSIS 安装耗时 ≤ 60s
  - T2 首启 splash ≤ 90s 至可见 Live2D（P3-G1）
  - T3 `scripts/perf/cold_boot.py --runs 3 --gate-sec 90`
  - T4 文字 + 语音对话
  - T5 **端口占用错误路径**（`python -m http.server 8100` 模拟）
  - T6 正常卸载（保留 AppData）
  - T7 完全卸载（SettingsPanel）
  - T8 MSI 变体重跑 T1-T7
- `scripts/perf/cold_boot.py` 已存在（P3-S4 阶段写的）

### S11 — 发布准备

- 版本号统一推到 `0.5.0-phase3-rc1`：
  - `tauri-app/src-tauri/tauri.conf.json`
  - `tauri-app/src-tauri/Cargo.toml`
  - `tauri-app/package.json`
  - `backend/pyproject.toml`
- **`docs/releases/v0.5.0-phase3-rc1.md`（新）** — release notes：
  - 醒目的 "仅支持 NVIDIA GPU / Windows 10+" 硬门槛
  - P3-G1..G6 门状态
  - vs v0.2.0 的变更清单（新增 / 改动 / 修复）
  - 升级说明（v0.2.0 的相对路径数据无自动迁移）
  - 已知限制 / Phase 4 TODO（首启 GUI 下载器、logrotate、macOS/Linux）
  - Checksums 占位待 VM smoke 后填
- **不打 git tag**（留给用户或 CI）

## 3. 验收结果

| 验收点 | 结果 |
|---|---|
| cargo test --lib | **35 passed, 0 failed** ✅（新增 17 条：paths 8 + user_data 6 + process_manager 3） |
| cargo clippy --lib -- -D warnings | clean ✅ |
| pytest | **317 passed, 4 skipped, 0 failed** ✅ |
| tsc --noEmit | clean ✅ |
| frozen backend 重建 | 145.8s，**1524.7 MB**（P3-G2 预算 3.5 GB）✅ |
| `smoke_frozen_backend.py` | boot **4.3s**，/health ok，startup_errors=[] ✅ |
| `e2e_frozen_tauri.ps1` | PASS；`[backend_launch] Bundled exe=...` 分支凭据、/health ok、Tauri 窗口存活 ✅ |
| 端口占用 → 红卡 | ⏳ 待用户手测（启动第二个 backend 或 `python -m http.server 8100`） |
| 完全卸载 | ⏳ 待用户手测（走 SettingsPanel → 危险区） |
| VM installer smoke | ⏳ 按 `docs/P3-S10-installer-smoke-runbook.md` 走 |

## 4. 用户待办

当前 Tauri dev 窗口应已开着（`e2e_frozen_tauri.ps1` PASS 后常驻）。请验证：

1. **Splash overlay**：窗口出现时是否先看到半透明黑底 + "正在启动语音服务…"，4–6s 后自动消失
2. **正常对话**：文字 "你好" Enter + 麦克风说话，验证 ASR/LLM/TTS 链路没被本 slice 打破
3. **错误路径（可选）**：关闭 Tauri，开一个终端跑 `python -m http.server 8100`，然后再启 Tauri 或 `scripts/e2e_frozen_tauri.ps1`，看红色错误卡片 + "打开日志目录"按钮是否弹 Explorer
4. **完全卸载（可选 + 破坏性）**：⚙ 打开设置 → 危险区 → 不勾选模型 → 按按钮 → 确认两次 → app 自动退出后检查 `%AppData%\deskpet\` 是否已空

确认无误即 merge：

```bash
git checkout master
git merge --no-ff p3-s8-s11-release
git push origin master
# 等 VM smoke runbook 跑完再 tag：
# git tag -a v0.5.0-phase3-rc1 -m "Phase 3 RC1"
# git push origin v0.5.0-phase3-rc1
```

## 5. 不做 / 推迟

- ❌ 真的在全新 Win10/11 VM 上跑 installer — 文档写完，操作交给用户或 Phase 4 CI
- ❌ `git tag` / GitHub Release 上传 — 人工触发
- ❌ Updater 端到端签名验证 — 等真正 upload 后再验
- ❌ macOS / Linux — Phase 3 明确只做 Windows
- ❌ pre-existing lint errors（UserBubble 的 ref-during-render 等） — 与本 slice 无关

## 6. 合并顺序

上面 "用户待办" 4 项基本验收通过 → merge 到 master。VM runbook 可以先 merge 再异步跑；只要没 regress cargo/pytest/smoke，rc1 功能面已齐。
