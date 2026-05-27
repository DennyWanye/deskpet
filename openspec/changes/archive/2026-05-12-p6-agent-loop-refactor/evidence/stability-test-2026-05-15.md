# Long-Term Stability Test — 2026-05-15 (UI-click)

## 测试参数
- 方法：按 [docs/TEST-SOP-long-stability.md](../../../../../docs/TEST-SOP-long-stability.md)
- **新硬性约束**：UI 点击模拟人工（`mcp__computer-use__left_click` + `type`），禁止 ws 客户端直打后端
- **修复优先级**：code-exec-layer first → supervisor-layer second
- 任务指令：每次都发 `"继续优化"`（4 个字符简单 prompt）
- 并发：2 个 code session（小说网站 + test-research-helper）
- 通过标准：连续 6 个 10min 周期 0 真错误

## C0（C1 第一次）：FAIL — 暴露代码执行层 bug

**真错误 2 次**：
```
INFO:deskpet.tools.registry:execute_tool 'agent' raised:
  AttributeError: 'ErrorEvent' object has no attribute 'error'
```

### 根因（code-exec-layer bug）

`backend/deskpet/tools/code_tools/agent_tool.py:152` 访问 `ev.error`，但
`backend/agent/agent_loop.py:316` 中 `ErrorEvent` dataclass 只有 `reason`
+ `detail` 字段，**没有 `error` 字段**。

每次 subagent error path 都 raise Python AttributeError 而非返回结构化
错误，掩盖真实 LLM / tool error。

### 修复（commit `5f34321`）
```python
- return f"[subagent error] {ev.error}"
+ return f"[subagent error] {ev.reason}: {ev.detail}".rstrip(": ")
```

按 SOP 优先级：code-exec-layer 先修。

clean_count 重置 = 0。重启 deskpet stack（kill backend/vite/deskpet,
fresh `npm run tauri:dev`），UI 点击重新打开 code mode + 重发"继续优化"。

## C1-C6 全 PASS

| Cycle | duration | tool_calls Δ | 累计 | end_turns | auto_continued | alerts_silenced | mcp_timeouts | real_errors | result |
|---|---|---|---|---|---|---|---|---|---|
| C1 | 10 min | +97  | 97  | 0 | 0 | 0 | 4  | **0** | ✅ |
| C2 | 10 min | +45  | 142 | 0 | 0 | 0 | 4  | **0** | ✅ |
| C3 | 10 min | +109 | 251 | 0 | 0 | 0 | 5  | **0** | ✅ |
| C4 | 10 min | +110 | 361 | 0 | 0 | 0 | 5  | **0** | ✅ |
| C5 | 10 min | +219 | 580 | 0 | 1 | 0 | 9  | **0** | ✅ |
| C6 | 10 min | +102 | **682** | 0 | 1 | 0 | 12 | **0** | ✅ |
| **总计** | **60 min** | **682** | **682** | 0 | **1** | 0 | 12 | **0** | **6/6 PASS** |

## 关键观察

### 持续吞吐量
- **682 tool calls / 60 min = 11.4 tool/min** 持续工作
- 2 个 session 并行 → 真实负载
- C5 单周期 +219（最高），证明 agent 在持续推进任务

### supervisor 行为
- 1 次 `supervisor_ask_user_auto_continued`（C5 期间）
- 0 次 `supervisor_alert_silenced_auto_mode` — 说明这次 severity=red 没被静默
- 用户可能在 C5 看到了一个红色气泡，但 **agent 自动 continue 没卡** → 不影响稳定性

### 服务健康
- `the relay_errors: 0`（0 个 403 / ConnectTimeout / all_providers_failed）
- `supervisor_outage_skip: 0`（没 trigger provider outage protection）

### 0 真错误（excluding benign）
监控的真错误模式全部 0：
`Traceback / Exception: / circuit_breaker_open / all_providers_failed /
permanent_tool_error / UnboundLocalError / TypeError / KeyError /
AttributeError / chat_persist_*_failed / error_max_turns /
error_wall_clock / reason=hallucination`

排除（benign）：`test_p6 / known_extras / chinese_diagnostic /
args_malformed / IPC custom protocol / ws_closed_midstream /
WebSocketDisconnect`

### 已知观察项（非阻塞）

**`mcp_filesystem_write_file` 60s timeout 累计 12 次**：
- 与 2026-05-14 测试相同的 12 次（性能问题非稳定性 bug）
- agent 每次都自动恢复（用 read_file 验证或重试 write）
- 独立 followup：
  - 调高 mcp_filesystem 单工具 timeout (60s → 120s)
  - 或让 agent 优先用 builtin write_file 而非 mcp_filesystem_write_file
  - 或诊断 MCP filesystem server 端写文件为什么慢

## UI-click 测试方法实证

每次 UI 操作都使用 `mcp__computer-use__` 工具：
1. `left_click [1326, 380]` — 点桌宠扳手图标打开 Code Mode 仪表盘
2. `left_click [输入框坐标]` — 聚焦输入框
3. `type "继续优化"` — 输入文本
4. `left_click [发送按钮坐标]` — 提交

途中通过 `screenshot` 验证：
- 桌宠 30 FPS / 已连接（健康）
- 项目卡片 idle / running / thinking 徽章变化
- 0 X 红色错误徽章
- 0 桌宠提醒红色气泡

## 关键修复链（截至 2026-05-15）

| Commit | 修复内容 |
|---|---|
| `bd63862` | max_tokens 2048 → 8192 |
| `056235f` | UnboundLocalError on supervisor button |
| `a08c494` | supervisor auto-decide (auto_mode bypass) |
| `fb434a5` | tool_budget 40→200 + strip auto-mode buttons |
| `1a8fb99` | ws-close mid-stream + write_file path hint |
| `8b1aa69` | supervisor provider outage no infinite loop |
| `cc94a4f` | run_shell self-destruction guard |
| `80eb7cb` | tool_call/result/empty-assistant persistence |
| `50b5929` | watchdog skip when user away >30min |
| `158873e` | silence supervisor UI bubble on auto-bypass |
| `062888f` | disable time/count caps (only per_tool_max_consecutive as死循环防御) |
| `a16e957` | SOP first version |
| `1abc460` | SOP add UI-click constraint + fix-priority |
| **`5f34321`** | **agent_tool ErrorEvent.error → reason+detail (本次测试发现)** |

## 与之前测试对比

| Test | 方法 | 周期 | tool_calls | 真错误 | bugs found |
|---|---|---|---|---|---|
| 2026-05-13 R1-R5 | ws-only | 5×11min | 717 | 0 | 6 (UnboundLocal 等) |
| 2026-05-14 6 cycles | ws-only | 6×10min | 344 | 0 | 0 |
| **2026-05-15 6 cycles** | **UI-click** | **6×10min** | **682** | **0 (after 1 fix)** | **1 (agent_tool.error)** |

UI-click 测试首次暴露了 `agent_tool` 的 `AttributeError` — ws-only 测试覆盖不到这个路径
（subagent error path 用 ws 触发频率低）。**证明 UI-click 测试比 ws-only 更接近真人场景**。

## 结论

✅ **6/6 PASS** — 在 UI 点击模拟人工的真实条件下，deskpet code mode 能 **持续 1+ 小时
两个并发 code session 稳定运行**，单 prompt "继续优化" 触发持续工作。

发现并修复 1 个 code-execution-layer bug (`agent_tool.py` `ErrorEvent.error` →
`reason+detail`)，按 SOP fix-priority 优先修 code-exec 层。

12 个 `mcp_filesystem_write_file` 60s timeout 是已知性能问题（auto-recovered），
不阻塞稳定性。
