# Deskpet Code Mode 长期稳定性测试 SOP

## 目标

验证 deskpet code mode 在 auto-mode 下能持续无人值守跑真实复杂代码任务。
合格标准：**连续 6 个 10 分钟周期 0 真错误** (= 1 小时稳定)。

## 触发条件

每当：
- supervisor 行为修改后
- TerminationGate / agent_loop / SessionDB 等核心组件改动后
- 用户报告"奇奇怪怪问题"后
- 计划做大改动前作为 baseline

## 测试设计

### 🔒 硬性约束：必须用 UI 点击模拟人工（2026-05-15 加入）
- **禁止**用 ws 客户端（`websockets.connect()`）直接打后端发任务
- **必须**用 `mcp__computer-use__left_click` + `mcp__computer-use__type` 模拟人工：
  1. 点桌宠扳手图标打开 Code Mode 仪表盘
  2. 点项目卡片输入框
  3. type 任务文本
  4. 点"发送"按钮（或 Return 键）
- **理由**：ws 直打路径覆盖不到 UI 层 bug（panel rendering / button click / 输入框 typing / IPC pipe），而真实用户走的是 UI 层
- 历史教训：2026-05-14 的 stability-test 用 ws 跑过 6/6 PASS 但用户仍报 UI 层 bug — 因为 ws 测试根本没碰 UI 层

### 并发 2 个 code session
- 共享资源竞争最容易暴露 bug（PermissionGate / SessionDB write lock / WS broadcast）
- 单 session 测不到的并发问题

### 任务必须真复杂
- **避免**：简单 `list_directory` / `read_file × 5` 类探索任务（10 分钟就完，没压力）
- **要**：让 agent 走 30+ 次 tool_use loop，触发：
  - read_file / write_file 大文件
  - glob / grep
  - todo_write 拆步骤
  - 错误处理 / supervisor 介入 / auto_resume
  - 长上下文（context budget 边界）

### 监控方式：流式 + 周期摘要 双层

**流式层**（实时）：
- `tail -fn0 /path/to/deskpet/tauri-dev.log | grep --line-buffered 真错误模式`
- 任何匹配立即 alert，不等周期结束

**周期层**（每 10 分钟）：
- cat ws senders 输出（FINAL / msgs count）
- grep 周期内累计统计（tool_calls / end_turns / supervisor 介入 / persist 失败）
- screenshot 看 UI 健康（无 X 红色 error 徽章、无骚扰气泡）

## 错误等级

| 等级 | 模式 | 处理 |
|---|---|---|
| **🔴 真错误** | Traceback / Exception: / circuit_breaker_open / permanent_tool_error / all_providers_failed / UnboundLocalError / TypeError / KeyError / AttributeError / chat_persist_*_failed | 停 → 诊断 → 修 → 重启 → 计数清零 |
| **🟡 行为问题** | supervisor 频繁触发 / 反复同工具 / write_file 反复失败 / hallucination 多次 | 评估，可能需要 prompt / cap 调整 |
| **🟢 可忽略** | ws_closed_midstream / WebSocketDisconnect / IPC custom protocol failed / 单次 tool_use 失败被 auto_resume 接住 / args_malformed 后被 args_repaired | 不打断，记录到 cycle metrics |

## 修复优先级（2026-05-15 加入）

修复顺序：**code 执行层优先 → supervisor 层后修**

1. **Code 执行层** (agent_loop / TerminationGate / tool dispatch / SessionDB persistence / MCP tools / write_file 等)
   - 直接影响 agent 真正"能不能干活"
   - 例：tool_call args 解析失败、write_file 超时、SessionDB 没持久化、Termination cap 误杀
2. **Supervisor 层** (supervisor.py / watchdog / auto_resume / UI 提醒气泡)
   - 影响"卡了之后能不能恢复 / 用户是否被骚扰"
   - 但前提是 code 执行层稳定
   - 例：supervisor 频繁误触发、auto-continue 死循环、UI 气泡噪音

理由：supervisor 是 code 执行层的"消防员"，先确保不起火再优化消防响应。先修了 supervisor
但 code 执行层还在着火，那 supervisor 只是不停地报警。

## 标准任务模板

### 项目 1：小说网站 (`code-rkjdd9vo`)
```
继续完成网站前端：补全 BookDetailPage / ChapterReadPage / AuthorDashboard
三个页面，每页用 React + Tailwind 完整可运行的代码，含路由配置 + API
对接。每个文件 ≤3KB（避免 max_tokens 截断），如果太长就拆成子组件分多
次写。
```

### 项目 2：test-research-helper (`code-303cyy44`)
```
继续完成研究助手后端：补全 routes (plan/clarify/wizard)，含 SSE
streaming + LLM 集成 + SQLite schema 定义。每个文件 ≤3KB。
```

### 续命任务（session idle 时）
```
继续完成任务
```

## 执行流程

### 0. 启动前 prep
```bash
# 1. baseline
cd backend && python -m pytest tests/ -q --ignore=tests/test_deskpet_vector_worker.py

# 2. 重启 deskpet（如必要）
netstat -ano | grep "LISTENING" | grep -E ":(8100|5173)" | awk '{print $5}'  # PIDs
tasklist | grep -i deskpet.exe                                                   # deskpet pid
powershell -Command "Stop-Process -Id <pid1>,<pid2>... -Force"
sleep 3
cd tauri-app && DESKPET_BACKEND_DIR=/path/to/deskpet/backend npm run tauri:dev > /path/to/deskpet/tauri-dev.log 2>&1 &
until grep -qE "Application startup complete" /path/to/deskpet/tauri-dev.log; do sleep 5; done

# 3. 启用 auto-mode
SEC=$(grep "secret=" /path/to/deskpet/tauri-dev.log | tail -1 | grep -oE 'secret=[a-f0-9]+' | cut -d= -f2)
python -c "
import asyncio, json, websockets
async def m():
    ws = await websockets.connect(f'ws://127.0.0.1:8100/ws/control?secret=$SEC')
    await ws.send(json.dumps({'type':'permission_auto_mode_set','payload':{'enabled':True}}))
    async for raw in ws:
        if 'auto_mode_response' in raw: print(raw); break
    await ws.close()
asyncio.run(m())
"
```

### 1. 流式 monitor 启动
```python
Monitor(
    command="tail -fn0 /path/to/deskpet/tauri-dev.log | grep --line-buffered -iE 'Traceback|Exception:|hallucination|chat_v2_error|circuit_breaker_open|all_providers_failed|permanent_tool_error|UnboundLocalError|TypeError|KeyError|AttributeError|chat_persist_.*_failed|p4_mcp_manager_bootstrap_failed|error_max_turns|error_wall_clock|p5s2_tool_call_args_malformed'",
    timeout_ms=660000,  # 11 min
    persistent=False,
)
```

### 2. 并发发任务
```python
# /tmp/cycle_N_send.py
import asyncio, json, websockets
TASKS = {
    'code-rkjdd9vo': '<项目 1 任务文本>',
    'code-303cyy44': '<项目 2 任务文本>',
}
async def run(sid, text, secret):
    uri = f'ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={sid}'
    ws = await websockets.connect(uri)
    await ws.send(json.dumps({'type':'chat_v2','payload':{'text':text,'session_id':sid}}))
    print(f'[{sid}] sent', flush=True)
    end = asyncio.get_event_loop().time() + 600
    msg_count = 0
    async for raw in ws:
        msg_count += 1
        if asyncio.get_event_loop().time() > end: break
        try:
            m = json.loads(raw)
            if m.get('type') == 'chat_v2_final':
                print(f'[{sid}] FINAL text_len={len(m.get("payload",{}).get("text","") or "")}', flush=True)
                break
        except: pass
    print(f'[{sid}] msgs={msg_count}', flush=True)
    await ws.close()
async def main():
    await asyncio.gather(*(run(sid, txt, SECRET) for sid, txt in TASKS.items()))
asyncio.run(main())
```

### 3. 周期评估（每 10 min）
```bash
# real errors
grep -aE "(Traceback|Exception:|hallucination|chat_v2_error|circuit_breaker_open|all_providers_failed|UnboundLocalError|TypeError|KeyError|AttributeError|chat_persist_.*_failed)" /path/to/deskpet/tauri-dev.log | \
  grep -avE "test_p6|known_extras|chinese_diagnostic|args_malformed|IPC custom|ws_closed_midstream|WebSocketDisconnect" | wc -l

# activity metrics
echo "tool_calls: $(grep -ac p5s2_tool_call_args_dump /path/to/deskpet/tauri-dev.log)"
echo "end_turns:  $(grep -ac \"stop_reason='end_turn'\" /path/to/deskpet/tauri-dev.log)"
echo "auto_continued: $(grep -ac supervisor_ask_user_auto_continued /path/to/deskpet/tauri-dev.log)"
echo "alerts silenced: $(grep -ac supervisor_alert_silenced_auto_mode /path/to/deskpet/tauri-dev.log)"
echo "auto_resume_engaged: $(grep -ac auto_resume_engaged /path/to/deskpet/tauri-dev.log)"
echo "the relay 403: $(grep -ac '403 Forbidden' /path/to/deskpet/tauri-dev.log)"

# screenshot UI for visual evidence
# (mcp__computer-use__screenshot, save_to_disk=True)
```

### 4. 决策树
```
periodic_check:
  if real_errors > 0:
    → diagnose root cause from log
    → write fix
    → run pytest baseline (1236+ should pass)
    → restart deskpet
    → CLEAN_CYCLE_COUNT = 0
    → re-send tasks, restart Monitor
  elif clean_cycle_count >= 6:
    → DECLARE STABLE
    → write evidence report
    → commit fixes (if any)
    → done
  else:
    → CLEAN_CYCLE_COUNT += 1
    → check if any session idle → re-send "继续完成任务"
    → restart Monitor for next 10min
```

### 5. 完成判定
- **PASS**：6 个连续干净周期 = 1 小时无真错误
- **FAIL** (需要 escalate)：同类 bug 连续 3 个周期出现，自动 fix 解不掉 → 停下来设计更深层修复

## 输出物

每次测试运行后写入 `openspec/changes/archive/.../evidence/stability-test-<date>.md`：
- 周期数 / 累计 tool_calls / end_turns / 真错误
- 期间发现 + 修复的 bug 列表（commit hash）
- 最终结论 (PASS/FAIL)

## 历史记录

- 2026-05-13: 初版 5 轮测试（pre-auto-mode）— 5/5 PASS
- 2026-05-14: 5 轮 auto-mode 测试 — 5/5 PASS + 6 bugs fixed
- 2026-05-14: 7 轮深度测试 — 暴露 self-destruction / persistence / wall_clock 等问题
- 2026-05-14 v2: 新增 SOP 文档化 + 连续 6 周期目标 (本次)

## 已知历史 bug 类型（避免重复）

| 类型 | commit |
|---|---|
| max_tokens 截断 | bd63862 |
| sentinel UI 泄漏 | ee783a1 |
| supervisor UnboundLocal | 056235f |
| supervisor auto-decide | a08c494 |
| tool_budget=40 太低 | fb434a5 |
| ws-close mid-stream | 1a8fb99 |
| write_file 缺 path hint | 1a8fb99 |
| supervisor provider outage loop | 8b1aa69 |
| run_shell self-destruction | cc94a4f |
| tool_call/result 不持久化 | 80eb7cb |
| watchdog 误打扰 idle 用户 | 50b5929 |
| supervisor bubble 噪音 | 158873e |
| auto-mode 时间硬上限 | 062888f |
