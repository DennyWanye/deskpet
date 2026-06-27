# P3-S8 ~ P3-S11 — 启动 UI + 卸载清理 + Installer 验收 + v0.5.0-phase3-rc1

**Date**: 2026-04-23
**Branch**: `p3-s8-s11-release`
**Scope**: 合并 roadmap 里的 P3-S8 / S9 / S10 / S11 四个 slice，一次性推到 "rc1 可打包发布" 的状态。

---

## 1. 背景

P3-S6+S7 已把模型 / 用户数据目录搞定（`%AppData%\deskpet\` + `%LocalAppData%\deskpet\models\`），
frozen bundle 1524 MB / 冷启 2.9s / pytest 322 green / UI E2E 通过。剩下的都是"交付动作"：

- **S8**：前端肉眼能看到的启动/错误反馈（splash + 错误 dialog + 打开日志目录按钮）
- **S9**：卸载保留 `%AppData%`，SettingsPanel 里给用户一个"完全卸载"按钮
- **S10**：端到端 installer smoke runbook + cold-boot 性能基线脚本
- **S11**：版本号推到 `0.5.0-phase3-rc1` + release notes；tag 推送 defer 给用户

按用户指令一次性 batch 执行，UI 手测合并到最后。

## 2. 验收门

| 验收项 | 目标 |
|---|---|
| start_backend 失败时弹中文 dialog + 不沉默 | ✅ |
| splash 覆盖层在 `secret==""` 期间显示"正在启动语音服务…" | ✅ |
| 8100 端口被占用 → 明确提示（不是"spawn failed"） | ✅ |
| SHARED_SECRET 等待有墙钟超时（30s 内必报错） | ✅ |
| Rust 暴露 `open_log_dir` / `open_app_data_dir` 命令 | ✅ |
| SettingsPanel 有"完全卸载（清除用户数据）"按钮 + 二次确认 | ✅ |
| NSIS/WiX 卸载默认不碰 `%AppData%\deskpet\`（默认行为天然如此） | ✅（验证） |
| `docs/P3-S10-installer-smoke-runbook.md` 完整可执行 | ✅ |
| `scripts/perf/cold_boot.py` 存在且可跑 | ✅ |
| 版本号统一到 `0.5.0-phase3-rc1`（tauri.conf.json / Cargo.toml / package.json / pyproject.toml） | ✅ |
| `docs/releases/v0.5.0-phase3-rc1.md` release notes | ✅ |
| pytest 全绿 / `cargo test` 全绿 | ✅ |
| frozen 重建成功 + self-smoke PASS | ✅ |
| 用户 UI E2E 确认 splash+dialog 行为 | 待用户手测 |

## 3. 改动清单

### 3.1 S8 —— 启动时序 + 错误 UI

**Rust (`tauri-app/src-tauri/src/`)**：

- `process_manager.rs`
  - 新增 `startup_error: Mutex<Option<String>>` 到 `BackendProcess`，记录最近一次启动失败的人类可读消息
  - `spawn_once` 先做 **8100 端口 precheck**（`TcpListener::bind` 试绑定再立即释放；失败给"端口 8100 已被其它程序占用"的中文错误）
  - SHARED_SECRET 读取循环加 **30s 墙钟超时**（`Instant::now()` + 每次 `read_line` 用非阻塞逻辑，这里偷懒：单独线程+channel，主线程 `recv_timeout`）
  - `start_backend` 失败时把 `format_user_message(&err)` 写进 `startup_error`，返回 Err 让前端 invoke 抛
  - 新增 `get_startup_error` / `clear_startup_error` command
- `paths.rs`（新）
  - Rust 侧镜像 `backend/paths.py` 的路径算法：
    `user_data_dir()` = `%AppData%\deskpet`，`user_log_dir()` = `%AppData%\deskpet\logs`，
    `user_models_dir()` = `%LocalAppData%\deskpet\models`，都带 env var override
  - 纯函数 + 单测
- `log_dir.rs`（新）—— 只含一个 `open_log_dir` command：用 `tauri-plugin-opener` 打开 `paths::user_log_dir()`
- `lib.rs`：注册新 command，**不再在 setup 里 `exit(1)`** — 把 GPU 缺失也改写进 `startup_error`，交给前端 dialog（GPU 目前仍然致命，但让前端有机会打开 dialog 再退）

**Frontend (`tauri-app/src/`)**：

- `App.tsx`
  - 新增 `bootState: "starting" | "ready" | "failed"` + `bootError: string | null`
  - bootstrap useEffect：start_backend 抛错时 invoke `open-dialog` 显示中文消息（带"打开日志目录"按钮走 `open_log_dir` command），state 转 `failed`
  - 挂 splash 覆盖层组件
- `components/StartupSplash.tsx`（新）
  - 半透明覆盖层 + "正在启动语音服务…" + 旋转 indicator
  - 只在 `bootState==="starting"` 且 `secret===""` 时渲染
- `components/StartupErrorDialog.tsx`（新）
  - inline overlay（不用 plugin-dialog 的 blocking_show，因为那会阻塞 Tauri 事件循环；改用原生 React modal）
  - 三个按钮：`重试` / `打开日志目录` / `退出`

**Capabilities**：`capabilities/default.json` 保持不变（`opener:default` 已允许打开本地文件夹）

### 3.2 S9 —— 卸载残留清理

**Rust**：
- `process_manager.rs` 加 `purge_user_data` command：递归删除 `paths::user_data_dir()` 和 `paths::user_models_dir()`（含 junction），然后 `app.exit(0)`
  - 默认只删 `%AppData%\deskpet\`，`%LocalAppData%\deskpet\` 由参数 `include_models: bool` 控制
- `lib.rs`：注册

**Frontend**：
- `SettingsPanel.tsx` 新增 `危险区` section（红色边框），包含：
  - 勾选框 "同时删除 %LocalAppData%\deskpet\models\（模型缓存 ~9GB）"
  - 按钮 "完全卸载（清除用户数据）"
  - 二次确认：`window.confirm("这将删除所有聊天历史/设置/日志，无法撤销。继续？")`
  - 调 `purge_user_data` → 捕获 error 弹提示

**NSIS/WiX**：
- `tauri.conf.json` 里 `bundle.windows.nsis` / `wix` 默认不碰 `%AppData%\deskpet\`；**默认行为即满足要求**，仅在 docs 写明

### 3.3 S10 —— installer smoke runbook

- `docs/P3-S10-installer-smoke-runbook.md`（新）
  - 全新 Windows 10/11 虚拟机 checklist：
    1. 下载 `.msi` 或 `.nsis.exe`
    2. 双击安装 → 记录耗时（T0）
    3. 启动 → 首次 splash 出现 → ready（T1 - T0 = 冷启动时长）
    4. 聊天 3 轮（文字 + 语音）
    5. 检查 `%AppData%\deskpet\logs\` 是否有日志
    6. 卸载 → 检查 `%AppData%\deskpet\` 是否保留
    7. SettingsPanel 完全卸载 → 检查 `%AppData%\deskpet\` 是否清空
- `scripts/perf/cold_boot.py`（若不存在则新增）
  - spawn frozen backend，`time.monotonic()` 打时间戳，直到 `/health == ok`
  - 打印 `cold_boot_seconds`，断言 ≤ 90s（P3-G1）

### 3.4 S11 —— v0.5.0-phase3-rc1 准备

- `tauri-app/src-tauri/tauri.conf.json` `version` → `"0.5.0-phase3-rc1"`
- `tauri-app/src-tauri/Cargo.toml` `version` → `"0.5.0-phase3-rc1"`
- `tauri-app/package.json` `version` → `"0.5.0-phase3-rc1"`
- `backend/pyproject.toml` `version` → `"0.5.0-phase3-rc1"`
- `docs/releases/v0.5.0-phase3-rc1.md`（新）
  - 声明"仅支持 NVIDIA GPU（CUDA 12.x 驱动）"
  - P3-G1..G6 门状态
  - 关键变更清单
  - 已知限制 / Phase 4 计划
- **不** `git tag` / **不** `git push tag`：留给用户 / CI

### 3.5 测试

- `tauri-app/src-tauri/src/paths.rs` 内 `#[cfg(test)]` mod：mock env 验证 AppData / LocalAppData 解析
- `process_manager.rs`：若已有 test mod 就补几个 `startup_error` round-trip 的小 test；port precheck 用 `TcpListener` 实跑
- `backend/tests/` 不动（S8-S11 基本没碰 backend Python）

## 4. 不做 / 推迟

- ❌ 真的在全新 Win10/11 VM 上跑 installer：写 runbook，执行交给用户或 Phase 4 CI
- ❌ `git tag` / GitHub Release 上传：人工触发
- ❌ Updater 签名验证端到端：已有 pubkey + endpoint，等 rc1 实际发布后再验
- ❌ macOS/Linux：仍旧只管 Windows
- ❌ 卸载时用 WiX Custom Action 强删 `%AppData%`：用户体验差，明确走"不删 by default + SettingsPanel 自助"路线
- ❌ GPU 缺失改走前端 dialog：P3-S2 的 native dialog 已可用，不重构

## 5. 合并顺序

```
smoke PASS + pytest green + cargo test green
  → 用户 UI E2E（splash + error dialog + SettingsPanel 完全卸载）
  → merge 到 master
  → （defer）用户或 CI 打 v0.5.0-phase3-rc1 tag
```
