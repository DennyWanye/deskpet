# P3-S5 — Tauri bundle 吸纳冻结 backend 产物

**Date:** 2026-04-22
**Depends on:** P3-S3（supervisor 自管路径，留好 Bundled 分支）+ P3-S4（`backend/dist/deskpet-backend/` 可跑）
**Estimated:** ≤ 1 天
**Roadmap ref:** `docs/superpowers/plans/2026-04-21-phase3-roadmap.md` P3-S5 行

---

## 目标

让 `tauri build` 生成的 NSIS / MSI 安装包里**自带**整个
`backend/dist/deskpet-backend/` 目录（exe + `_internal/`），用户装完
双击 `DeskPet.exe`，Rust supervisor 的 `backend_launch::resolve` 走
**Bundled 分支** 直接起 `<resource_dir>/backend/deskpet-backend.exe`，
全程不依赖本地 Python 或环境变量。

P3-S3 已经把 Bundled 分支逻辑写好了，P3-S4 已经把 exe 准备好了。
P3-S5 就是**接线**——把两头接通。

## 非目标（不在本 slice）

- **Models bundle**：`backend/models/*` 超过 1 GB，走 P3-S6。本 slice
  的 UI E2E 靠 `DESKPET_MODEL_ROOT` 环境变量临时指向 dev repo 的
  `backend/models/`（junction 或 env）。
- **Installer 体积压缩**：P3-S6 之后总体积稳定了才讨论（LZMA 压缩、
  `updater.zst` 分发等）。
- **用户数据迁移**（`%AppData%\deskpet\`）：P3-S7。
- **Splash screen / 启动错误 UI**：P3-S8。

## 验收标准（本 slice 才算 ship）

1. `tauri dev` 模式下，Rust 侧日志打印 `backend_launch: Bundled exe=…`（走 Priority 1）
2. UI 层 E2E：启动 Tauri，点麦讲话，看到 ASR + LLM + TTS 完整回放
3. `tauri build --debug` 能跑通，`target/debug/bundle/` 下产出的 exe
   + installer 里包含 `resources/backend/deskpet-backend.exe`
4. 体积报告：installer 本体 < 800 MB（backend bundle 610 MB + Tauri
   frontend + 自身运行时），记录到 HANDOFF 做 P3-G2 追踪基线
5. smoke: `/health` 200 + `startup_errors: []`（从 Tauri spawn，不是
   脚本 spawn）

## 5 提交路线

### Commit 1 — `docs(plan): P3-S5 Tauri bundle backend slice plan`
- 本文件 + 在 master 上提交

### Commit 2 — `build(tauri): P3-S5 bundle.resources 吸纳冻结 backend`
修改 `tauri-app/src-tauri/tauri.conf.json`：

```jsonc
"bundle": {
  …
  "resources": {
    "../../backend/dist/deskpet-backend/**/*": "backend"
  },
  …
}
```

注意：
- 路径相对 `src-tauri/`，往上两级到 repo root
- `backend` 目标子目录对应 `backend_launch::resolve` 里
  `root.join("backend").join("deskpet-backend.exe")` 的第一段
- `/**/*` 递归 glob，把 `_internal/` 下 2973 个文件一并抓进来
- 如果 Tauri 的 resources 实现对大目录很慢，备选是用 array 形式
  `["../../backend/dist/deskpet-backend"]`（整个目录一次拷）

### Commit 3 — `scripts: P3-S5 e2e_frozen_tauri smoke`
新增 `scripts/e2e_frozen_tauri.ps1`：
- 校验 `backend/dist/deskpet-backend/deskpet-backend.exe` 存在（未跑 P3-S4 build 就报错）
- 校验 `backend/models/` 存在（或 env `DESKPET_MODEL_ROOT`）
- 启动 `npm run tauri dev` 在后台
- 等 30s 后 `curl http://127.0.0.1:8100/health`，断言 `status==ok`
- kill 进程（用户 memory：清理 deskpet.exe + Vite 孤儿进程）

这个脚本补位 `scripts/smoke_frozen_backend.py`——后者只测 exe，前者
测完整 Tauri→supervisor→exe 链路。

### Commit 4 — `refactor(tauri): tauri dev 下 resource_dir 的 fallback`
**（条件性提交）** 如果 `tauri dev` 下 `resource_dir()` 不指向
`target/debug/` 或不会自动 copy resources，就在 `backend_launch.rs`
里加个 dev fallback：

```rust
// P3-S5: `tauri dev` 下 resource_dir() 可能返回 cargo manifest dir
// 而不是 target/debug/，这时 Bundled 分支找不到 exe。加一条备选：
// 直接去 repo_root/backend/dist/deskpet-backend/ 找。
```

先实测再决定是否要这条。

### Commit 5 — `docs(P3-S5): PACKAGING §4 续 + HANDOFF + STATE`
- PACKAGING.md §4 加一个小节 "4.x Tauri 集成"，描述 `bundle.resources`
  映射、安装后路径、`externalBin` vs `resources` 选型理由
- `handoffs/p3-s5-tauri-bundle-backend.md`：走完的 E2E、体积数字、
  `tauri dev` resource 复制时间、Preview MCP UI 截图路径（用户 memory
  要求 Real Test）
- STATE.md 滚动到 P3-S5 行

## 风险 & 应对

| 风险 | 概率 | 应对 |
|------|------|------|
| `tauri dev` 每次启动 copy 2973 个文件 → dev loop 慢 | 高 | 实测 first-run 耗时；如果 > 10s，探索 symlink / junction workaround 或 dev-only 走 env 分支跳过 Bundled |
| `tauri build` resource 复制 OOM / 磁盘满 | 低 | CI 跑之前加磁盘空间检查；本地先 `tauri build --debug` 验证 |
| installer 体积超过 800 MB | 中 | 记录基线，P3-S6 之前不改 —— models 加进来才是大头 |
| `resource_dir()` 在 dev 和 prod 路径语义不一致 | 中 | 用 `Manager::path::resource_dir` 同一 API 实测两种模式，记录在 PACKAGING |
| Updater `.sig` 生成被 resources 打乱 | 低 | P2-S7 的 release CI 里 `createUpdaterArtifacts: true` 已经在，rebuild 后跑一次 `tauri signer verify` 确认 |
| WS 端口 8100 在 dev 被占用 | 低 | 用户 memory 里已有 taskkill 习惯 |

## Manual E2E checklist（ship 前必做）

- [ ] `powershell scripts/build_backend.ps1` 先跑一次（保证 dist 干净）
- [ ] `npm run tauri dev` 启动，观察 Rust 侧日志出现 `Bundled exe=…`
- [ ] app 窗口打开，点麦讲话"你好"，看到对话面板显示 ASR 结果 + AI 回复 + TTS 播放
- [ ] Preview MCP 截图保存到 `docs/superpowers/handoffs/p3-s5-screenshots/`
- [ ] `tauri build --debug` 产出 installer，体积记录
- [ ] 查 installer 里 `resources/backend/deskpet-backend.exe` 是否就绪

## 测试

- 没新的 cargo test（Bundled 分支测试在 P3-S3 已覆盖）
- 没新的 pytest（backend 逻辑无变更）
- 新的 script-level smoke（`e2e_frozen_tauri.ps1`）在 HANDOFF 里记录
  一次通过记录

---

**Ship gate：** UI E2E 截图 + installer 体积 + Bundled 分支日志三件
齐全才算 green。
