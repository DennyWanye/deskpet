# Long-Term Stability Test — 2026-05-14

## 测试参数
- 方法: 按 [docs/TEST-SOP-long-stability.md](../../../../../docs/TEST-SOP-long-stability.md) 执行
- 并发: 2 个 code session 同时跑（小说网站 + test-research-helper）
- 任务: 真实复杂代码生成（React 前端补全 / FastAPI 后端补全）
- 监控: 流式 `tail -fn0 + grep` + 每 10 分钟周期 metrics 快照
- 通过标准: **连续 6 个 10min 周期 0 真错误** = 1 小时无人值守稳定

## 逐周期 metrics

| Cycle | duration | tool_calls Δ | tool_calls 累计 | end_turns | auto_continued | alerts_silenced | mcp_timeouts | real_errors | result |
|---|---|---|---|---|---|---|---|---|---|
| C1 | 10 min | +58  | 58  | 0 | 0 | 0 | 0  | **0** | ✅ PASS |
| C2 | 10 min | +1   | 59  | 0 | 0 | 0 | 1  | **0** | ✅ PASS |
| C3 | 10 min | +61  | 120 | 0 | 0 | 0 | 6  | **0** | ✅ PASS |
| C4 | 10 min | +64  | 184 | 0 | 0 | 0 | 10 | **0** | ✅ PASS |
| C5 | 10 min | +83  | 267 | 0 | 0 | 0 | 12 | **0** | ✅ PASS |
| C6 | 10 min | +77  | 344 | 0 | 0 | 0 | 12 | **0** | ✅ PASS |
| **总计** | **60 min** | **344** | **344** | 0 | **0** | **0** | 12 | **0** | **6/6 PASS** |

## 关键观察

### 持续工作量
- **344 tool calls / 60 min = 5.7 tool/min** 持续吞吐
- 2 个 session 并行 → 真实负载场景
- end_turns=0 说明 agent 一直在 tool_use loop 中工作（没有提前自我宣布完成）

### supervisor 完全沉默（修复目标达成）
- `supervisor_ask_user_auto_continued: 0` — supervisor 全程不需要介入
- `supervisor_alert_silenced_auto_mode: 0` — 也没有静默化的告警（说明真没误判）
- 对比 R6-R10 历史：之前每个周期 supervisor 介入 1-4 次。本次 0 次。

### chinzy 服务健康
- `403 Forbidden: 0` / `ConnectTimeout: 0` / `all_providers_failed: 0`
- 之前测试期间出现的 chinzy outage 不再复现

### 0 真错误（关键稳定性指标）
排除模式: `args_malformed`(已被 args_repaired 兜底) / `IPC custom protocol`(Tauri 启动)
/ `ws_closed_midstream`(测试客户端断开) / `WebSocketDisconnect`(同上) / `test_p6`
/ `known_extras` / `chinese_diagnostic`

实际监控的真错误模式（全部 0）:
`Traceback / Exception: / circuit_breaker_open / all_providers_failed /
permanent_tool_error / UnboundLocalError / TypeError / KeyError /
AttributeError / chat_persist_.*_failed / error_max_turns /
error_wall_clock / reason=hallucination`

### 可观察性问题（NOT 稳定性 bug）

**`mcp_filesystem_write_file` 60s timeout 累计 12 次**：
- WARNING 级别（不是 ERROR）
- agent 每次都自动恢复（用 read_file 验证或 retry）
- 触发原因：MCP filesystem 工具响应慢（写大文件时）
- 建议跟进（独立任务，不阻塞稳定性）：
  - 调高 mcp_filesystem 单工具 timeout（60s → 120s）
  - 或让 agent 优先用 builtin `write_file` 而非 `mcp_filesystem_write_file`
  - 或诊断 MCP server 端写文件为什么慢

## 关键修复（让本次测试 PASS）

| Commit | 修复内容 |
|---|---|
| `bd63862` | max_tokens 2048 → 8192 (long write_file) |
| `056235f` | UnboundLocalError on supervisor button |
| `a08c494` | supervisor auto-decide (auto_mode bypass) |
| `fb434a5` | tool_budget 40→200 + strip auto-mode buttons |
| `1a8fb99` | ws-close mid-stream + write_file path hint |
| `8b1aa69` | supervisor provider outage no infinite loop |
| `cc94a4f` | run_shell self-destruction guard (deskpet's own ports) |
| `80eb7cb` | tool_call/result/empty-assistant persistence |
| `50b5929` | watchdog skip when user away >30min |
| `158873e` | silence supervisor UI bubble on auto-bypass |
| `062888f` | **disable time/count caps** (`wall_clock=None`, max_turns=tool_budget=10000) |
| `a16e957` | SOP documentation |

最后一条 `062888f` 是 enabling 本次 6/6 通过的关键——之前 wall_clock=600s/1800s
会强制中断 long-running 任务。现在只用 args-aware `per_tool_max_consecutive=8`
作为唯一真死循环防御。

## 结论

✅ **6/6 PASS**: 1 小时连续 0 真错误 + 0 supervisor 介入 + 0 用户骚扰。

deskpet code mode 在 auto-mode 下能**长时间无人值守跑真实代码任务**，
两个并发 session 持续吞吐 344 tool calls 全程稳定。

12 个 mcp_filesystem 60s timeout 是已知性能问题（agent 自动恢复，不影响
稳定性），独立 followup 跟进。
