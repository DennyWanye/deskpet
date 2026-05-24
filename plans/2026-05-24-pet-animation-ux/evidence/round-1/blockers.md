# BLOCKER 报告 — Pet Animation UX v1 round-1 手测

| 项 | 值 |
|---|---|
| 日期 | 2026-05-24 |
| 执行子代理 | Opus 4.7 (1M) QA |
| 验收对象 commit | 3b1e78f |
| 状态 | **BLOCKED-ENV — 手测无法在当前会话内完成** |

---

## 1. 阻塞结论

子代理无法在当前 Claude Code 会话内执行 ManualTest v3 的 P0 手测，因为：

### B1. WebView2 DevTools 无远程调试端口
- Tauri/WebView2 默认不开 Chrome DevTools Protocol (CDP) — `deskpet.exe` (PID 5856) 无任何 listening port（`Get-NetTCPConnection -OwningProcess 5856` 空）
- `tauri.conf.json` 无 `--remote-debugging-port` 配置；Tauri dev 文档中开启 WebView2 远程调试需自定义 builder + env `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`，**项目未启用**
- 结果：windows-mcp / Claude_in_Chrome MCP 都无法附加到 deskpet 的 WebView 上下文。`window.metrics()`、`window.debug()`、`window.flagOn()` 这些 ManualTest §0.3 必需 helper **完全无法注入或读取**

### B2. 用户会话冲突 — pet 在使用中
- 截图确认 deskpet 窗口（右上角 Hiyori）就是用户当前正在使用的桌宠
- 用户 Claude Code 会话有未发送的 chat 消息（"亲跑一次 MR-T-0..."）和正在编辑的代码面板
- 任何 ManualTest P0 case 都涉及对 pet 真实操作（鼠标绕屏移动、单/双击 hit-zone、窗口 resize），会：
  - 触发桌宠 click → 可能误发消息或干扰 supervisor 状态
  - 鼠标绕屏 → 抢占用户光标
  - Window resize → 拉变用户实际工作窗口
- 强行执行会**污染用户实际工作环境**，违反"不破坏宿主"原则

### B3. Day-0 探针需要修改 src 注入
- CASE-D0-01 (Probe-1) 要求"在 Live2DCanvas init() 内 model load 完之后注入 TDD §0 Probe-1 代码"
- CASE-D0-03 (Probe-3) 要求"粘 TDD §0 Probe-3 代码到 Console"
- CASE-D0-04 (Probe-4) 要求"注入 TDD §0 Probe-4 临时红色方块"
- 系统指令明确禁止"直接修改 src/ 代码"且无 Console 访问（见 B1）
- 即使有 Console，注入后还要 `git revert` 探针 commit（CASE-D0-CLEANUP）— 子代理无 commit 权限

### B4. 性能 / 盲测类 case 本质需要人为/时间
- CASE-PERF-01..03 要求"关闭 DevTools 后用 Windows 任务管理器读 CPU/RAM 30s 均值"——可部分执行（PowerShell `Get-Process` 拿 PID 5856 的 CPU/Working Set），但缺 `flagAllOff` reload 对比基线（见 B1/B2）
- CASE-BLIND-01 明确 DEFERRED-HUMAN（1 周后 + 1 朋友盲选）— prompt 已说明
- CASE-REG-01 同样涉及 5 人盲选

### B5. 后端 token 真链路缺失
- `/health` 显示 `cloud_configured: false` — keychain 中无 cloud API key
- ManualTest 中虽不直接测 LLM，但 CASE-MP-04 触发 supervisor red alert 走的是真 LLM 调用链路 — 当前环境无法触发真 alert

---

## 2. 现有环境状态（已确认）

| 项 | 状态 |
|---|---|
| Git HEAD | `3b1e78f` = Phase 1 实现 commit ✅ |
| `tauri-app/src/pet-anim/` 9 模块 | 全部存在 ✅ |
| 后端 `/health` (8100) | 200 OK，`status: ok`，`startup_errors: []` ✅ |
| Vite (5173) | 监听中 ✅ |
| `deskpet.exe` (PID 5856) | 运行中，窗口可见 Hiyori ✅ |
| WebView2 DevTools 端口 | **无** ❌ |
| Cloud API key | **未配置** (`cloud_configured: false`) ⚠️ |
| 用户活跃使用中 | 是（截图见 chat 面板有未发消息 + 代码面板） ⚠️ |

---

## 3. 已完成的离线/读取式验收

下列项目无需操作 UI，已在本 round 完成（详见 SUMMARY.md 和 probes-runtime.md）：

| 项 | 结论 | 证据 |
|---|---|---|
| CASE-D0-02 ParamEyeBallX/Y 存在 | **PASS** | `Hiyori.cdi3.json:45,50` grep 命中 ParamEyeBallX/Y |
| PHASE1 实现完整性 | **PASS** | 9 模块齐全；386/386 vitest 绿；coverage ≥ 80% |
| 双路径 fallback 已实现 | **PASS** | `AnimationOverlay.applyTo` SET→ADD→…→SET 6 步 + `add_native` runtime check（TDD §3.7） |
| FIFO 配对 cap=20 | **PASS** | `pet-anim/index.ts` `pending_clicks` + `recordVisualFrameTs` shift（TDD §3.8） |
| hit-zone 自适应（CASE-G-06 静态对应） | **PASS** | `computeFaceFrame` 单一来源 + ResizeObserver + window resize 节流（PRD §6.0 v3） |
| state_changed 立即切（CASE-MP-04 静态对应） | **PASS** | `App.tsx` 将 `state_changed` 透传到 `force_switch_now` |
| Backend healthy | **PASS** | `/health` 200 |

---

## 4. 推荐解锁路径（给主 agent / 用户）

要完成 round-1 真机手测，必须满足以下任一条件：

### 选项 A — 独立环境（推荐）
1. 用户关闭当前 Claude Code 会话 → 在干净环境内再启动一个 Tauri dev
2. 启动时设置 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`
3. 子代理用 Claude_in_Chrome MCP 附加到 `http://127.0.0.1:9222` 拿 WebView 上下文
4. 才能执行 D0-01/03/04 + 所有 CASE-P/B/S/G/MP/PR/MET/PERF 的 Console 注入

### 选项 B — 人工辅助 (混合模式)
1. 用户本人开 WebView DevTools，把 `helper.js` 粘进 Console
2. 用户报实测结果回到子代理；子代理负责把 D0-02 / 源码静态 / 测试套件结果写 evidence
3. 仅完成"无需 UI 交互"的 P0 case 自动化部分；CASE-BLIND DEFERRED-HUMAN（已规定）

### 选项 C — 用 worktree 启独立 deskpet 实例
1. `scripts/dev-worktree.ps1` 已支持端口隔离（main=8100/5173, tool-last-mile=8300/5373）
2. 在 `tool-last-mile-upgrade` 工作树启第二个 deskpet 实例（不抢用户当前的）
3. 仍需解决 B1（WebView2 远程调试端口）

---

## 5. round-1 子代理交付（在当前阻塞下）

按 ManualTest §17 / GOAL §阶段 2 验收规则，**测试结果**：

- **CASE-D0-02**：PASS（离线）
- **CASE-D0-01/03/04 + CASE-P/B/S/G/MP/PR/MET/PERF/REG/HMR/COLD**：**BLOCKED-ENV**
- **CASE-BLIND-01**：DEFERRED-HUMAN（按 prompt 规定）
- **CASE-D0-CLEANUP**：N/A（探针未注入，无需 cleanup）

→ **最终判定：NEEDS-FIX（环境阻塞）** — 不能宣布 Phase 1 PASS；需主线程在选项 A/B/C 之一解锁后重跑

---

## 6. 何为"不能 fake PASS"

按 ManualTest §18 严格禁止条款：
> 严格禁止：跳过 Day-0 探针 / 跳过任何 P0 case / 用 vitest 单测代替手测（vitest 不能验视觉/性能/穿透）

vitest 已 386/386 绿（PHASE1 报告确认），但 prompt 和 ManualTest 明确指出"单测不能代替手测"。因此即便所有静态/单测全绿，本子代理仍**必须**报告 BLOCKED-ENV 而非伪造 PASS。

这与项目 CLAUDE.md 中已固化的反模式一致：
- `feedback_simulate_manual_test` — 改代码后只跑 unit test 不算完成
- `feedback_real_e2e_not_script_replay` — 不能用脚本回放当 E2E 证据
- `feedback_real_test` — 每 slice 必须有 UI 级 E2E + 截图
