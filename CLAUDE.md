# CLAUDE.md — DeskPet 项目级 Claude 工作笔记

本文件给 Claude 子代理 / 助手用，记录本仓库特有的开发上下文（区别于全局 `~/.claude/CLAUDE.md`）。

> 📊 **接手前先读全局状态**: [`STATUS/status.md`](./STATUS/status.md) —
> 所有并行 worktree / 功能模块完成度 / 最近里程碑 / 已知问题，一页看清。

---

## ✅ STATUS 更新纪律（HARD — 不可妥协）

**任何任务一旦"通过测试完成"，必须同步更新 [`STATUS/status.md`](./STATUS/status.md)。**

- **触发条件**：一个 WI / slice / 功能模块跑通验收（pytest/vitest/cargo/手工 E2E 全绿）→ 视为"完成"。
- **强制动作**（完成的同一次交付内，不能拖到下次）：
  1. 更新 §3 模块完成度（🟡 进行中 → ✅，或新增行）
  2. 若是里程碑级 → 追加一行到 §4 最近里程碑（倒序）
  3. 若 worktree 合并到 master → 更新 §2 表格状态
  4. 改顶部"最后更新"日期
- **判定**："改了代码 / 跑过测试但没更新 STATUS" = 任务**未完成**。
- **粒度**：细节放各 plan 文档，STATUS 只记状态 + 链接，保持一页能看完。

---

## 🚀 测试阶段：能力即开即用，**不灰度**（HARD — 用户 2026-06-27 定调）

**当前处于测试阶段。开发完成（单测/验收通过）的能力要立即默认开启（flag = ON / 默认 True）投入使用，不做灰度 / shadow / 分批渐进。**

- **判定**："开发好了但 flag 还默认 OFF 没开起来用" = **不可接受**——测试阶段就是要把做好的能力全跑起来暴露问题。
- **强制动作**：一个能力一旦"开发完成"（单测绿 + 实现完整），同一交付内把它的出厂默认 flag 翻 **ON**，不要留 `default False` 等"灰度通过再开"。
- **例外（仍 OFF）**：① 实现**未完成 / 半成品**的（如 artifact 信封 last_mile 未实装）；② 明确危险/不可逆且无护栏的（如自然语言遗忘 `enable_natural_language`）；③ 与当前主线程无关的（如 code 模式专属）。这些标注原因，做完/补护栏后**立即**开启。
- **不要再写"shadow 先行 / 批 A 批 B 灰度 / 默认 OFF 等观测通过"这类渐进上线话术**——那是生产阶段的做法，现在不用。
- **对照**：已开发但默认 OFF 的能力清单见 [`plans/2026-06-26-agent-harness-alignment/00-PLAN.md`](./plans/2026-06-26-agent-harness-alignment/00-PLAN.md) §WI-0.0（全量点亮表）。

---

## 🔑 开发期登录测试账号（**仅 DEV 环境**）

DeskPet 的 LLM 调用走 中转站（默认 gpt-5.5）。用户首次启动时走 onboarding 登录流程：
1. Tauri 弹登录窗 → 用户输入账号密码
2. relay 反代 → 校验账号 → 下发 `tsk_xxx` access token + `key_xxx` device key
3. token 写入 OS keychain（Windows DPAPI / macOS Keychain）
4. backend 通过 `DESKPET_CLOUD_API_KEY` env 拿到 key 调 LLM

**测试凭据**：本仓库**不包含**。请从 `LOCAL-DEV-CREDENTIALS.md`（gitignored）读取，
模板见 [`LOCAL-DEV-CREDENTIALS.md.example`](./LOCAL-DEV-CREDENTIALS.md.example)。

### 子代理用法

跑 windows-mcp E2E（如 MR-1）需要真实 LLM 链路时：
1. 启动 Tauri 应用 → 出现 onboarding 登录窗
2. 用上面账号登录 → 等 relay 下发 key → keychain 写入
3. 关掉 onboarding → 进入桌宠主界面
4. backend 自动从 keychain 读 key → 真 LLM 调用可用
5. 此时对话"帮我生成 PPT" → LLM 调 `ppt_create` → 真生成 .pptx → ArtifactCard 渲染

### 安全约束

- ⚠️ **不要写进 .env 或 secrets/ 目录**（这两个会被 diagnostic bundle 收集）
- ⚠️ **不要在报告里截图账号密码**（截图前先关 onboarding 窗）
- ✅ 测试 keychain 由测试代码用 `monkeypatch.setattr("backend.secrets.get_cloud_api_key", lambda: "fake-sk-...")` mock，**生产代码永远从 OS keychain 读**

---

## 📁 分支 / 端口 / 关键文档

- **分支策略**：master 直接开发（`feedback_deskpet_branch_strategy`），不走长寿命 feature 分支。worktree 拓扑与各模块完成度见 [`STATUS/status.md`](./STATUS/status.md) §2。
- **端口隔离**（真测高频）：main 树 backend=**8100** / vite=**5173**（默认）；其他 worktree 经 `scripts/dev-worktree.ps1` 注入 `DESKPET_BACKEND_PORT`/`DESKPET_VITE_PORT` 错开。
- **关键 plan/文档清单**：见各 `plans/<date>-*/00-*.md` 与 [`STATUS/status.md`](./STATUS/status.md)。last-mile 升级 PRD/TDD/手测用例在 `plans/2026-05-23-tool-last-mile-upgrade/`。
- **last-mile 验收命令**：`python scripts/acceptance/last_mile_smoke.py`（期望 `DECISION: SHIP`）+ 对应 TG pytest 套件（命令清单见该 plan 目录）。

---

## 🚨 项目特有的"踩过的坑"

1. **Tauri dev 启动后留 orphan 进程**（feedback_tauri_dev_cleanup）—— `TaskStop` 不会清 `deskpet.exe` + Vite。stop 前必 `taskkill /F /IM deskpet.exe` + Vite 进程。
2. **改代码后只跑 unit test 不算完成**（feedback_simulate_manual_test）—— 必须 windows-mcp 走 end-to-end + 截图 + 抓日志。
3. **E2E ≠ 脚本回放**（feedback_real_e2e_not_script_replay）—— 不能用"再跑一遍 resolution 函数的脚本"当 E2E 证据；必须验证真实运行栈的实际出站行为。
4. **不要加沙箱护栏**（feedback_no_sandbox_constraints）—— deskpet 是单机桌宠，只防手滑级破坏。
5. **跨层契约漂移**（feedback_cross_layer_contract）—— pytest + tsc 都过但后端前端对字段单位 disagree → `scripts/e2e_*.py` live smoke 兜底。
6. **vector worker test_enqueue_small_batch_flushes_on_interval flaky**（time-based，已 spawn_task 跟踪修复）。
7. **不要手动起 backend 再起 Tauri（端口双占）★ 已踩多次** —— Tauri 自己会
   spawn 一个 backend 到 `DESKPET_BACKEND_PORT`（默认 8100，见
   `process_manager.rs::spawn_once` + `check_port_free`）。如果你为了"先验证
   backend"手动 `python main.py` 占了那个端口，Tauri 启动时 `os error 10048`
   端口被占用 → 桌宠弹"启动失败"对话框。**正确做法**：**不要**手动起 backend，
   只给 **Tauri 进程**注入 env（`DESKPET_BACKEND_PORT` / `DESKPET_USER_DATA_DIR`
   / `DESKPET_DEV_MODE` / `DESKPET_BACKEND_DIR`），让 Tauri 自己 spawn + 管理
   backend。要看 backend 日志：它 stdout 被 rust pipe（读完 SHARED_SECRET 后
   静默 drain），**structlog 全走 stderr → `Stdio::inherit()` → 落进 tauri dev
   的重定向 log**，所以抓 tauri dev 的 log 就能拿到完整 backend 日志。
8. **跑 worktree 的 backend 必须设 `DESKPET_BACKEND_DIR`（否则 Tauri 跑 frozen
   exe，没有你的改动）★** —— `backend_launch.rs::resolve_with` 优先级：
   **priority-1** = `DESKPET_BACKEND_DIR` env（设了就跑 `<dir>/.venv/Scripts/
   python.exe main.py`，`DESKPET_PYTHON` 可覆盖解释器）；**priority-2** = bundle
   `target/debug/backend/deskpet-backend.exe`（PyInstaller frozen，**主 checkout
   旧构建产物，不含 worktree 改动**）。所以真机测 worktree 代码必须
   `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<主.venv python>`，
   日志里确认出现 `[backend_launch] Dev python=... backend_dir=<worktree>` 才对；
   若看到 `[backend_launch] Bundled exe=...` 说明跑的是旧 frozen，测了等于白测。
9. **`tauri dev` 会自己跑 `beforeDevCommand`（= `npm run dev:relay`）起 vite ——
   不要再手动起一个 vite（双 vite 互抢 strictPort）★** —— `tauri.conf.json` 的
   `beforeDevCommand` 已经会启动 vite dev server。如果你为了"先确认前端"又手动
   `npm run dev:relay`，就会有两个 vite 抢同一个 `DESKPET_VITE_PORT`（strictPort
   下第二个直接退或漂到下一个端口，devUrl 对不上 → 白屏 / webview 连错）。**正确
   做法**：要么纯跑 `npx tauri dev`（让它自管唯一 vite），要么用
   `--config '{"build":{"beforeDevCommand":""}}'` 关掉自带 vite 后只手动起一个。
   二选一，别两个都起。

---

## 🔒 手工测试纪律（HARD CONSTRAINT — 不可妥协）

触发词："用 windows-mcp 测试" / "跑手工测试" / "模拟人工点击" / "真测" / "真 E2E" / `/goal` 设了相关 condition → 本约束强制生效。

**完整纪律（禁止清单 / workaround / 报告格式）见全局** `~/.claude/knowledge-base/windows-mcp-e2e.md`。本项目特有补充：

- **不允许**用 `ws://127.0.0.1:8100/*` WebSocket 直注、`pytest`/`last_mile_smoke.py`、`import` backend 查 registry、`cmdkey /list`/boot log grep 当 UI 测试证据 —— 全是协议层/脚本/间接证据，不替代真模拟点击。
- **中文输入 workaround**：STA Runspace + `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口先 Click 输入框聚焦再粘贴；用 backend log 确认消息真收到。
- **每个 case**：Snapshot/Screenshot → 真坐标点击/真输入 → 截图 → 日志判定；动作前 declare `坐标=(x,y)|动作=|期望=`；失败 retry ≥3 次不同 workaround 才能标"环境受限"；跳过须等用户确认。

**记住**：用户要的不是"PASS 数量"，是"真 E2E 证据"。绕过得来的 PASS 是负价值。
