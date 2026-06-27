---
name: run-deskpet
description: Engineering recipe for launching the real DeskPet (Tauri dev + backend injection + port isolation) for live GUI verification
triggers: [run deskpet, 启动桌宠, tauri dev, 真机启动, launch pet, run pet]
user-invocable: false
requires_script: false
---

# run-deskpet — 启动真桌宠做真机验证（给开发/测试 agent 用）

> ⚠️ 这是**工程 skill**，不是给终端用户的功能。用于 windows-mcp / 手测 agent
> 把改动跑进真实运行栈再验证。不要在普通用户对话里 /invoke。

## 黄金法则（照项目 CLAUDE.md 坑 7/8/9）

1. **不要手动起 backend**（坑 7）。Tauri 自己会 `spawn_once` 一个 backend 到
   `DESKPET_BACKEND_PORT`（默认 8100）。你手动 `python main.py` 占了端口 →
   Tauri 启动 `os error 10048` → 桌宠弹「启动失败」。**只给 Tauri 进程注入 env**，
   让它自管 backend 生命周期。

2. **跑 worktree 代码必须设 `DESKPET_BACKEND_DIR`**（坑 8）。否则 Tauri 跑
   bundle 的 frozen exe（旧构建，不含你的改动）= 白测。
   `backend_launch.rs::resolve_with` 优先级：
   - priority-1 = `DESKPET_BACKEND_DIR` env → 跑 `<dir>/.venv/Scripts/python.exe main.py`
     （`DESKPET_PYTHON` 可覆盖解释器）
   - priority-2 = bundled frozen exe（旧产物）
   日志里**必须**看到 `[backend_launch] Dev python=... backend_dir=<worktree>` 才对；
   看到 `[backend_launch] Bundled exe=...` 说明跑的是旧 frozen，测了白测。

3. **不要再手动起 vite**（坑 9）。`tauri.conf.json` 的 `beforeDevCommand`
   （= `npm run dev:relay`）已经会起 vite。你再 `npm run dev:relay` → 两个 vite
   抢同一个 `DESKPET_VITE_PORT`（strictPort 下第二个退出或漂端口 → devUrl 对不上
   → 白屏）。要么纯跑 `npx tauri dev`，要么 `--config '{"build":{"beforeDevCommand":""}}'`
   关掉自带 vite 后只手动起一个。二选一，别两个都起。

## 端口隔离（多 worktree 并行真测）

- main 树：backend=8100 / vite=5173（默认）。
- 其它 worktree：用 `scripts/dev-worktree.ps1` 注入 `DESKPET_BACKEND_PORT` /
  `DESKPET_VITE_PORT` 错开，避免互抢。

## 启动配方（PowerShell，worktree 真测）

```powershell
$env:DESKPET_BACKEND_DIR = "/path/to/deskpet\backend"        # 跑你的改动
$env:DESKPET_PYTHON      = "/path/to/deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_PORT = "8100"
$env:DESKPET_VITE_PORT    = "5173"
$env:DESKPET_DEV_MODE     = "1"
# 只起 Tauri，让它自己 spawn backend + 唯一 vite：
npx tauri dev
```

## 登录链路（需要真 LLM 时）

1. Tauri 启动 → 出 onboarding 登录窗。
2. 用 dev 账号登录（凭据在 gitignored `LOCAL-DEV-CREDENTIALS.md`，**勿截图账密**）。
3. 等 relay 下发 `key_xxx` → 写入 OS keychain。
4. 关 onboarding → 进桌宠主界面。backend 自动从 keychain 读 key → 真 LLM 可用。

## 收尾（坑 1：orphan 进程）

`TaskStop` 不会清 `deskpet.exe` + vite。停前必：
`taskkill /F /IM deskpet.exe` + 杀 vite 进程，否则下次重开报 8100 占用。

## 接力

跑通后去 `verify-deskpet` skill 做产物校验 SOP。
