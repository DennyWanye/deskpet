# P5-S2 Tasks (TDD-first ordering)

每个 phase 严格按 **red → green → refactor** 顺序：先写失败的测试，再写最少代码让它过，最后清理。**phase 之间必须实测验证（manual E2E + log 抓现场）才算闭环**，不允许"看起来应该能跑"就推进。

每完成一个 task 划掉一个。

---

## Phase 0 — Sensor + Remediation Hint（最高 ROI，120 行，应该挡 80% 循环）

### 0.1 工具错误响应 schema 扩展

- [ ] 0.1 写测试 `tests/test_p5s2_tool_error_schema.py::test_run_shell_missing_command_returns_hint` —— 调 `run_shell({})` 期望 `out["hint"]` 含 "command 字段必填" + `out["examples"]` 是 list 且至少 1 项；当前实现只返回 `{"error": "command required"}` → 红
- [ ] 0.2 修 `backend/deskpet/tools/os_tools/run_shell.py` —— missing command + missing cwd + timeout + OSError 各分支返回 `{ok: false, error, hint, examples}` → 绿
- [ ] 0.3 重复 0.1/0.2 套路给 `write_file.py` (missing path / missing content / overwrite blocked / OSError)
- [ ] 0.4 重复给 `edit_file.py` (missing path / not_unique / not_found / OSError)
- [ ] 0.5 重复给 `read_file.py` (missing path / file_not_found / binary_file / encoding_error)
- [ ] 0.6 重复给 `list_directory.py` (missing path / not_a_dir / permission_denied)
- [ ] 0.7 重复给 `desktop_create_file.py` / `web_fetch.py` / `glob.py` / `grep.py` (各自常见错误分支)
- [ ] 0.8 整合测试：`test_all_os_tools_error_have_hint_field` —— 遍历 `os_tools/__init__.py` 的所有 tool，故意触发 error，断言每个返回的 dict 都有 `hint`（非空 str）+ `examples`（list 即可）

### 0.2 Tool dispatch 层归一化

- [ ] 0.9 写测试 `tests/test_p5s2_tool_dispatch_hint_passthrough.py` —— mock 一个 tool handler 返回 `{"ok": false, "error": "x", "hint": "do y"}`，过 `registry_v2.execute_tool`，断言 dispatch 结果保留 `hint` 字段不被吃
- [ ] 0.10 检查 `registry_v2.py` 序列化 / formatting 路径是否会丢字段；如丢则修复

### 0.3 验收（必须真跑，不许跳）

- [ ] 0.11 启 deskpet code mode，发"用 write_file 创建 test.txt 内容 hello"指令；故意把指令写得让 LLM 容易漏 path（"创建 test.txt 内容 hello"）；观察日志：第一次失败时 tool_result 是否含 hint，LLM 第二次是否修对
- [ ] 0.12 截图 / 日志贴到 `openspec/changes/p5-s2-self-healing-harness/evidence/0.11-hint-recovery.md`

---

## Phase 1 — 诊断日志（10 行，定位 chinzy 截断 vs 模型自身 bug）

### 1.1 SSE tool_call args dump

- [ ] 1.1 写测试 `tests/test_p5s2_sse_diagnostic.py::test_tool_call_args_logged_with_length_and_parse_status` —— mock SSE 返回一个不完整的 tool_call args（`{"path": "fo`），断言 logger 调用包含 `args_len` / `args_preview` / `parse_ok=False` 三个字段
- [ ] 1.2 在 `providers/openai_compatible.py` 的 `_stream_one_attempt` 末尾，循环 `tool_buffers` 时打 INFO 级日志：`tool_call_args_dump idx=N name=X args_len=L args_preview=<前 100> parse_ok=true|false`
- [ ] 1.3 测试通过 → 实测：撞一次 50 iteration 循环（用之前的 vpn-tunnel 项目重现），抓 log，确认能看到具体哪条 tool_call 的 args 是空 / 半成品

### 1.2 验收

- [ ] 1.4 把抓到的 log 摘要 + 结论（chinzy 截断 / 模型自身 / 我们解析 bug）记录到 `evidence/1.3-args-dump.md`

---

## Phase 2 — Error 分类 + Permanent break-out（80 行）

### 2.1 Error 分类器

- [ ] 2.1 写测试 `tests/test_p5s2_error_taxonomy.py::test_classify_missing_param_is_permanent` —— `classify({"error": "missing required parameter: path"})` 返回 `PermanentToolError`
- [ ] 2.2 测试 `test_classify_timeout_is_transient` —— `{"error": "timeout"}` → TransientToolError
- [ ] 2.3 测试 `test_classify_circuit_open_is_permanent` —— `{"error": "circuit_open"}` → PermanentToolError
- [ ] 2.4 测试 `test_classify_unknown_defaults_transient` —— 没见过的 error 字符串默认 TransientToolError（保守：宁可重试也不假杀）
- [ ] 2.5 测试 `test_classify_hallucinated_tool` —— `{"error": "tool_not_found"}` → HallucinationError
- [ ] 2.6 创建 `backend/agent/errors.py` 实现三个异常类 + `classify(raw: dict | str | Exception) -> type` 函数 → 测试绿
- [ ] 2.7 关键字表用模块常量列出来（PERMANENT_KEYWORDS / TRANSIENT_KEYWORDS / HALLUCINATION_KEYWORDS），方便加新规则

### 2.2 AgentLoop permanent break-out

- [ ] 2.8 写测试 `tests/test_p5s2_agent_loop_permanent_break.py` —— scripted LLM 反复发 invalid tool_call，scripted tool 反复返回 `error: "missing required parameter: path"`；断言 agent_loop 在第 1 次 (不是第 50 次) 收到 PermanentError 后立刻 emit ErrorEvent + 停止
- [ ] 2.9 在 `agent_loop.py` 的 tool dispatch 后加：解析 tool_result，如果 classify 是 Permanent → emit `ErrorEvent(reason="permanent_tool_error", detail=...)` + return
- [ ] 2.10 同步 emit `tool_error_classified` 事件给 main.py（让 supervisor 能拿到结构化诊断）

### 2.3 验收

- [ ] 2.11 实测：重现 vpn-tunnel 50 iteration case，断言只跑 ≤3 iteration 就停 + 弹出"工具用法错误" toast（不是"max_iterations"）
- [ ] 2.12 evidence 写到 `evidence/2.11-permanent-break.md`

---

## Phase 3 — Per-tool Circuit Breaker + Same-signature 检测（120 行）

### 3.1 ToolCircuitBreaker 数据结构

- [ ] 3.1 写测试 `tests/test_p5s2_circuit_breaker.py::test_three_consecutive_failures_open` —— 同 (sid, "write_file") 连失败 3 次 → state OPEN，第 4 次 `can_call` 返回 False
- [ ] 3.2 测试 `test_success_resets_failure_count` —— 失败 2 次后成功 1 次 → 计数清零，仍 CLOSED
- [ ] 3.3 测试 `test_open_to_half_open_after_cooldown` —— OPEN 后 wait > cooldown_seconds → state HALF-OPEN，`can_call` 返回 True 一次
- [ ] 3.4 测试 `test_half_open_success_closes` —— HALF-OPEN 调用成功 → CLOSED + 计数重置
- [ ] 3.5 测试 `test_half_open_failure_reopens` —— HALF-OPEN 调用失败 → 立刻 OPEN（不需要 3 次）
- [ ] 3.6 测试 `test_per_tool_isolation` —— `write_file` OPEN 不影响 `run_shell`
- [ ] 3.7 测试 `test_per_session_isolation` —— sid="A" 的 write_file OPEN 不影响 sid="B"
- [ ] 3.8 创建 `backend/agent/circuit_breaker.py` 实现 → 测试全绿

### 3.2 接到 tool dispatch

- [ ] 3.9 测试 `tests/test_p5s2_dispatch_circuit_integration.py::test_dispatch_blocked_when_open` —— 故意造 OPEN 状态，调 `execute_tool("write_file", ...)`，断言返回 `{ok: false, error: "circuit_open", hint: "...", available_alternatives: [...]}` 且**没有真的调用** handler
- [ ] 3.10 测试 `test_dispatch_records_outcome` —— 真调成功一次，断言 breaker 记录 success；真调失败，记录 failure
- [ ] 3.11 在 `registry_v2.py` 的 dispatch 入口加 `await self._breaker.can_call(sid, name)` 检查 + 调用后 `record_call(sid, name, ok)`
- [ ] 3.12 `available_alternatives` 简单实现：从 registry 找同 toolset 下的其他工具（write_file 熔断 → 推荐 edit_file）

### 3.3 Same-signature 死循环检测（agent_loop 里）

- [ ] 3.13 测试 `tests/test_p5s2_agent_loop_signature_repeat.py` —— scripted LLM 连 3 次同样的 (tool_name, args)，断言 agent_loop 第 3 次跳过工具执行 + 注入 system msg "你重复 3 次同一调用..."
- [ ] 3.14 复用 `session_activity.tool_signature_window`（P5-S1 已经有），agent_loop 在 dispatch 前查最近 5 步是否 ≥3 次同 signature
- [ ] 3.15 注入的 system msg 模板放 module 常量好测试

### 3.4 验收

- [ ] 3.16 实测：手动构造让 LLM 死循环写文件的 prompt（"反复尝试创建 /sys/foo（注定失败）直到成功"），观察 ≤6 iteration 就熔断 + agent 收到 alternatives 提示后换思路
- [ ] 3.17 evidence: `evidence/3.16-circuit-recovery.md`

---

## Phase 4 — AutoResume Orchestrator（200 行，闭环核心）

### 4.1 触发器接入

- [ ] 4.1 测试 `tests/test_p5s2_auto_resume.py::test_max_iterations_triggers_supervisor` —— mock max_iterations event，断言 AutoResumeOrchestrator 调用 supervisor.diagnose
- [ ] 4.2 测试 `test_circuit_open_triggers_supervisor`
- [ ] 4.3 测试 `test_permanent_error_triggers_supervisor`
- [ ] 4.4 测试 `test_supervisor_action_nudge_spawns_new_task` —— supervisor 返回 `action=nudge` → orchestrator spawn 新 chat task（mock chat handler，验证调用 + hint 在 _msgs 里）
- [ ] 4.5 测试 `test_supervisor_action_ask_user_does_not_auto_spawn` —— supervisor 决定 ask_user → 走原弹窗路径，不自动 spawn
- [ ] 4.6 测试 `test_max_attempts_caps_resume` —— 连续 2 次 nudge 失败 → 第 3 次不自动重试，转 ask_user
- [ ] 4.7 测试 `test_session_disabled_blocks_resume` —— config `auto_resume_enabled=false` → orchestrator 直接走 ask_user

### 4.2 实现

- [ ] 4.8 创建 `backend/agent/auto_resume.py::AutoResumeOrchestrator` —— 注入 supervisor + chat_dispatcher (callable that re-runs chat handler with given session_id + injected hint) + config + audit
- [ ] 4.9 在 `main.py` 中：
  - 在 chat handler `_ErrEv` 处（reason in {max_iterations, permanent_tool_error, circuit_open}）调用 `orchestrator.handle_failure(sid, reason, snapshot)`
  - orchestrator 内部决定后回调 chat_dispatcher 或 emit `auto_resume_exhausted` ws event
- [ ] 4.10 把 chat handler 的核心逻辑抽出来 `_run_chat_iteration(sid, msgs, ws)`，让 orchestrator 能复用（不要复制粘贴 200 行 ws send）
- [ ] 4.11 audit 写 `supervisor_hints.action='auto_resumed'` + 累积 `auto_resume_attempts` 字段

### 4.3 ws 事件

- [ ] 4.12 测试 `test_emits_auto_resume_started_ws_event`
- [ ] 4.13 测试 `test_emits_auto_resume_succeeded_when_final`
- [ ] 4.14 测试 `test_emits_auto_resume_exhausted_after_max_attempts`
- [ ] 4.15 实现 ws emission

### 4.4 验收

- [ ] 4.16 实测：重现 50-iteration case，**应该看到"agent 自愈中..."toast 出现，agent 自动重跑 1-2 次，最终给出有效响应**，无需用户点任何按钮
- [ ] 4.17 evidence: `evidence/4.16-auto-resume-e2e.md`（含截图 + 日志摘要）

---

## Phase 5 — UI（30 行 + 8 vitest）

### 5.1 ToolResultCard hint 渲染

- [ ] 5.1 vitest `splitToolError.test.ts` —— 含 hint 字段的 error result 渲染时 hint 高亮
- [ ] 5.2 修 `MessageBubble.tsx::ToolResultCard` —— 检测 `result.hint` 加金黄色边框 + 提示

### 5.2 AutoResumeBanner

- [ ] 5.3 vitest `AutoResumeBanner.test.tsx` —— `auto_resume_started` 事件触发 banner，`succeeded` 触发消失
- [ ] 5.4 创建 `tauri-app/src/code-panel/AutoResumeBanner.tsx` + 接到 ws.ts dispatch

### 5.3 settings 开关

- [ ] 5.5 vitest 验证 toggle 状态序列化到 config
- [ ] 5.6 `SettingsPanel.tsx` 加 "auto-resume" toggle

### 5.4 验收

- [ ] 5.7 实测 UI 4.16 case 看 banner + hint 高亮 + 设置面板切换工作

---

## Phase 6 — 配置 + Watchdog 集成 + 文档

- [ ] 6.1 `config.toml` `[supervisor]` 段新增 5 个 key（auto_resume_enabled / max_auto_resume_attempts / circuit_breaker_threshold / circuit_breaker_cooldown_seconds / tool_signature_repeat_threshold），写默认值 + 注释
- [ ] 6.2 `main.py` 把这些 config 传给 orchestrator + circuit_breaker + agent_loop
- [ ] 6.3 watchdog `_should_trigger` 加 (d) 规则：检查 `tool_signature_window` 是否显示 ≥3 次同 signature → trigger
- [ ] 6.4 测试 `test_watchdog_triggers_on_tool_signature_repeat`
- [ ] 6.5 更新 `README.md` 简介自愈机制
- [ ] 6.6 archive：完成后跑 `openspec` 工具 archive 这个 change 到 `openspec/changes/archive/`

---

## 验收门控（每 phase 必过）

每个 phase 完成 = 满足以下三条**全部**：

1. **该 phase 所有单测绿**（phase 内所有 tasks 划掉）
2. **不引入 regression** —— `pytest tests/ -q` + `npm test` 都不掉测试数
3. **真机 E2E 验证 + evidence 文档存档** —— `evidence/<task>.md` 写明：场景、操作步骤、抓到的 log、截图、结论

不允许跳过 evidence；不允许"假装做完"。如果 phase N 实测发现 phase N-1 的实现有问题，回 N-1 修，不许"先记 followup 后面修"。

## 不做的事（Scope guard）

按 [feedback_no_sandbox_constraints](C:\Users\24378\.claude\projects\G--projects-deskpet\memory\feedback_no_sandbox_constraints.md):

- ❌ 不加任何"先弹窗确认"权限护栏
- ❌ 不做 dangerous_ops gate / 命令白名单 / SQL DROP 拦截 这类 Claude-Code 风格沙盒
- ❌ 不强制"必须跑测试才许说完成"hook
- ❌ 不引入 multi-worktree Lead-Expert 模式
- ✅ 但**保留**永久错误的 break-out（这是防手滑+省钱，不是限制）

## 时间估算

| Phase | 行数 | 估时 | 必要性 |
|---|---|---|---|
| 0 sensor hint | 120 | 2-3h | ⭐⭐⭐⭐⭐ 必做 |
| 1 诊断日志 | 10 | 0.5h | ⭐⭐⭐⭐ 必做（定位根因前提）|
| 2 error 分类 | 80 | 1.5h | ⭐⭐⭐⭐ 必做 |
| 3 circuit breaker | 120 | 2-3h | ⭐⭐⭐ 强烈建议 |
| 4 auto-resume | 200 | 4-5h | ⭐⭐⭐ 闭环关键 |
| 5 UI | 30 | 1h | ⭐⭐ polish |
| 6 配置 + 文档 | 30 | 0.5h | ⭐⭐ 收尾 |

**总 ~590 行，11-15h 一次性写完**。但**强烈建议拆做**：先 0+1+2（4-5h）实测验证后再决定 3+4+5+6 是否做、怎么做。
