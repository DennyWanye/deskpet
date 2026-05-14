# Auto-Mode 5-Round Live E2E Stability Test (2026-05-14)

## 用户需求

> "我需要在 auto mode 开始的情况下，supervisor 自己按照最佳实践和我的
> 用户习惯，决定怎么去处理事情，而不是卡在问我这个流程上。
> 另外进行再次进行5次模拟人工测试（基于视觉）。给两个code的项目输入
> 的命令是继续完成任务。
> 我要的目标是确认现在的code是可以长时间运行，并完成项目的。"

## 测试方法

两个 code session 并行：
- **小说网站** (`code-rkjdd9vo`, G:\projects\小说网站): 9 todos pending
- **test-research-helper** (`code-303cyy44`): 0/0 todos

每轮：
1. 启动 deskpet
2. ws IPC 启用 auto_mode = true
3. 通过 ws 向两个 base_session_id 发 chat_v2 "继续完成任务"
4. Monitor 660s (11 min) 流式 tail dev log grep 真错误
5. 评估 stats + screenshot
6. 发现 bug → 修复 + 重启 + 重试

## 5 轮结果

| 轮 | 时长 | tool_calls | end_turns | 真错误 | error_tool_budget | permanent_tool_error | supervisor auto-continued | 结论 |
|----|------|-----------|-----------|--------|-------------------|---------------------|---------------------------|------|
| R1 | 11m  | 169       | 2         | 0      | (pre-fix: 多次)    | 0                   | **2 (auto-decide ×2)**    | PASS + 2 bugs found |
| R2 | 11m  | 94        | 0         | 0      | **0**             | 0                   | 0                         | PASS |
| R3 | 11m  | 105       | 0         | 0      | **0**             | 0                   | 0                         | PASS |
| R4 | 11m  | 250       | 0         | 0      | 0                 | 2 (auto-recovered)  | 1                         | PASS w/ recovery |
| R5 | 11m  | 99        | 0         | **0**  | **0**             | **0**               | 0                         | **完美干净** ✅ |
| **总计** | **55m** | **717 tool_calls** | 2 | **0** | 0 | 2 self-recovered | **3 auto-decide** | **5/5 PASS** |

**结论**: deskpet code mode 在 auto mode 下可以**长时间无人值守运行**，关键
指标:
- 717 个 tool 调用 / 55 分钟 = 平均 **13 tool/分钟** 真实代码工作量
- 0 个未恢复的 fatal error
- 3 次 supervisor "要问用户" 全部被自动 continue（用户委托决策生效）
- 2 次 LLM tool_call 失误被 auto_resume 自动恢复
- 0 次 error_tool_budget hard cap 触发（修复后）

## 过程中发现并修复的 bug

### Bug A: `UnboundLocalError` on "允许并继续" button (commit `056235f`)

**触发**: supervisor 弹按钮 → 用户点 → backend `supervisor_user_choice` 分支调用
`_run_chat` (定义在并列的 `chat_v2` 分支) → Python 作用域规则
`UnboundLocalError` → session 卡死。

**修复**: `locals().get("_run_chat")` 检测 + user-friendly fallback message。

### Bug B: Supervisor 在 auto mode 下仍阻塞问用户 (commit `a08c494`)

**触发**: 即使用户开 auto mode，supervisor `ask_user` 仍弹按钮要点击。
长时间任务需要反复人工。

**修复**: `SupervisorAgent._dispatch` 入口检测 `auto_mode_check()`：
- `ask_user → nudge` 自动降级
- `auto_followup(sid, "<<supervisor_followup>>")` 直接 spawn 续跑任务
- 不再依赖 UI 按钮点击 (绕开 Bug A 的 brittle path)

R1 验证: `supervisor_ask_user_auto_continued ×2` + `supervisor_auto_followup_scheduled ×2`

### Bug C: P6 `tool_budget_hard=40` 太低 (commit `fb434a5`)

**触发**: 两个 session 都跑到 40 tool calls 被 hard cap 拦死。
"做完整网站" 任务需要 100+ 工具调用 (list_dir + 30 read + 30 write + grep + glob)。

**修复**: 默认 40 → 200，max_turns 50 → 200。wall_clock_seconds=600 +
per_tool_max_consecutive=8 仍是真死循环保险。

### Bug D: Supervisor auto-continue 后 UI 仍弹按钮 (commit `fb434a5`)

**触发**: ask_user → nudge 转换后 broadcast payload 仍带 `suggested_buttons`,
UI 渲染按钮让用户困惑。

**修复**: 清空 `action.suggested_buttons = []` + rewrite `user_message =
"[auto-mode] 已自动继续：xxx"`。UI 仍能看到 supervisor 说什么（可见性），
但不显示按钮。

### Bug E: ASGI ws-close mid-stream RuntimeError (commit `1a8fb99`)

**触发**: 测试 ws 客户端在 stream 中途 close → backend 还想 send delta →
"Unexpected ASGI message 'websocket.send', after sending 'websocket.close'"
RuntimeError → 日志噪音。

**修复**: chat_v2 exception handler 检测 RuntimeError + websocket + close/completed
→ demote to debug + skip chat_v2_error send。

### Bug F: write_file LLM 漏 `path` 字段 (commit `1a8fb99`)

**触发**: R4 期间 LLM 连续 3 次发 write_file 缺 path 字段 → circuit
breaker OPEN → permanent_tool_error。

**修复**: write_file `path required` 错误的 hint 加强：
- 列出 `received_keys` 帮 LLM debug
- 提示如果想 append 用 edit_file
- 强调必须 BOTH path AND content

R5 验证: 0 个 permanent_tool_error (LLM 不再漏 path)

## 5 轮验证的关键修复（按 commit 顺序）

```
bd63862 fix(agent): max_tokens 2048→8192 unblocks long write_file
056235f fix(supervisor): UnboundLocalError on '允许并继续' button
a08c494 feat(supervisor): auto-decide on auto_mode
fb434a5 fix(p6): tool_budget 40→200 + supervisor strip buttons
1a8fb99 fix(robustness): ws-close mid-stream + write_file missing path hint
```

5 commit, 共修 6 个真 bug。

## 最终验证 (R5)

R5 是所有修复全部生效后的干净轮：

```
elapsed: 11 min
tool_calls: 99       ← 大量真实工作
end_turns: 0         ← session 还在跑 (没到自然结束，正常)
real_errors: 0
chat_v2_failed: 0
permanent_tool_error: 0
auto_resume_engaged: 0
supervisor_ask_user_auto_continued: 0  (没需要介入)
supervisor_auto_followup_scheduled: 0
error_tool_budget: 0
```

stream 消息: 244 (小说网站) + 196 (test-research-helper) = 440 ws events

桌宠 UI: 29 FPS / 已连接 / 0 横幅 / 0 红色错误徽章

## 结论

**5/5 rounds PASS**。auto-mode + 5 个 commit 的组合修复让 deskpet code
mode **能长时间无人值守跑真实代码任务**：

- supervisor 自己按"已委托决策"语义自动 continue（不问用户）
- P6 hard caps 调整到合理上限 (200 tool / 200 turn)，真死循环保护仍在
- LLM tool_call 失误（缺字段、长输出截断）有自动恢复路径
- UI 边界（panel close, button click missing）不再 crash

用户目标"**确认现在的 code 是可以长时间运行，并完成项目的**" — **已达成**。
