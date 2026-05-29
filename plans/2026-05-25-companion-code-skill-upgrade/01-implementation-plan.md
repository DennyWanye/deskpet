# Implementation Plan — Companion + Code 模式升级

**关联**: `00-PRD.md` v1

---

## A. Stage A — Slash Command（主线程亲做）

### A1 后端 `slash_command` WS handler

`backend/main.py` chat_v2 handler 旁边加：

```python
elif msg_type == "slash_command":
    cmd_name = payload.get("command", "").lstrip("/")
    args = payload.get("args", "")
    session_id = payload.get("session_id")
    try:
        from deskpet.commands import dispatch_slash_command
        result = await dispatch_slash_command(
            cmd_name, args, session_id,
            skill_loader=_skill_loader,
            session_goal_store=_session_goal_store,
        )
        await _ws.send_json({
            "type": "slash_command_result",
            "payload": {"command": cmd_name, "result": result},
        })
    except Exception as exc:
        await _ws.send_json({
            "type": "slash_command_result",
            "payload": {"command": cmd_name, "error": str(exc)},
        })
```

### A2 `backend/deskpet/commands/__init__.py`

```python
async def dispatch_slash_command(
    name: str, args: str, session_id: str, *,
    skill_loader=None, session_goal_store=None,
) -> dict:
    # builtin 命令
    if name == "goal":
        return await _handle_goal(args, session_id, session_goal_store)
    if name == "help":
        return await _handle_help(skill_loader)
    # skill 命令 — 查 skill_loader
    if skill_loader and skill_loader.has(name):
        skill_args = args.split() if args else []
        out = await skill_loader.invoke_script(name, args=skill_args)
        return {"type": "skill_result", "skill": name, "output": out}
    return {"type": "error", "message": f"unknown command: /{name}"}
```

### A3 前端 InputBar 解析 `/`

`tauri-app/src/components/InputBar.tsx`：

```tsx
const send = () => {
  const text = input.trim();
  if (!text) return;
  if (text.startsWith("/")) {
    const [cmd, ...rest] = text.slice(1).split(/\s+/);
    ws.send({ type: "slash_command", payload: {
      command: cmd, args: rest.join(" "), session_id: sid,
    }});
    setInput("");
    return;
  }
  // ... 现有 chat_v2 路径
};
```

加 autocomplete：键入 `/` 时 fetch `/api/skills/list` → 下拉显示候选。

### A4 `/api/skills/list` REST endpoint

```python
@app.get("/api/skills/list")
async def list_skills():
    skill_loader = service_context.get("skill_loader")
    if skill_loader is None:
        return {"skills": []}
    return {"skills": [
        {"name": s["name"], "description": s.get("description", "")}
        for s in skill_loader.list_skills()
    ]}
```

### A5 测试

`backend/tests/test_slash_commands.py`：

- `test_slash_help` — `/help` 返 skill 列表
- `test_slash_unknown` — `/notexist` 返 error
- `test_slash_goal_set` — `/goal text` 调 goal_store.set
- `test_slash_skill_invoke` — `/some-skill arg1 arg2` 调 skill_loader

---

## B. Stage B — /goal Command（派子代理 1）

### B1 SessionGoalStore

`backend/deskpet/agent/goal_store.py`：

```python
@dataclass
class SessionGoal:
    session_id: str
    text: str
    set_at: float
    max_iterations: int = 10
    iterations_used: int = 0
    done: bool = False

class SessionGoalStore:
    def __init__(self): self._goals: dict[str, SessionGoal] = {}
    def set(self, sid, text): ...
    def get(self, sid) -> Optional[SessionGoal]: ...
    def clear(self, sid): ...
    def mark_done(self, sid): ...
```

### B2 GoalChecker

`backend/deskpet/agent/goal_checker.py`：

```python
class GoalChecker:
    def __init__(self, llm_call: Callable[[str], Awaitable[str]]): ...

    async def check(self, goal_text: str, working_msgs: list[dict]) -> tuple[bool, str]:
        prompt = f"""Goal: {goal_text}
Recent assistant work:
{_recent_msgs_summary(working_msgs)}

Is the goal achieved? Output JSON: {{"done": bool, "hint": "what's missing"}}"""
        raw = await self.llm_call(prompt)
        # 解析 JSON
        return (done, hint)
```

### B3 AgentLoop 接电

`backend/agent/agent_loop.py` 末轮（行 947 verify_gate 同位置）加：

```python
if self.session_goal_store is not None:
    goal = self.session_goal_store.get(session_id)
    if goal and not goal.done and goal.iterations_used < goal.max_iterations:
        try:
            done, hint = await self.goal_checker.check(goal.text, working_messages)
            if not done:
                goal.iterations_used += 1
                working_messages.append({
                    "role": "system",
                    "content": f"[goal] 未达成（{goal.iterations_used}/{goal.max_iterations}）: {hint}\n继续工作直到目标完成。",
                })
                continue
            else:
                self.session_goal_store.mark_done(session_id)
        except Exception:
            pass  # safe-fail
```

### B4 命令处理

`/goal <text>` → `session_goal_store.set(sid, text)`
`/goal clear` → `session_goal_store.clear(sid)`

### B5 UI

`tauri-app/src/code-panel/GoalBar.tsx` — 顶部条显示 active goal + clear button

### B-tests

5 个测试：set/get/clear/checker JSON parse/integration with AgentLoop

---

## C. Stage C — Multi-agent 并行（派子代理 2）

### C1 agent_parallel 工具

`backend/deskpet/tools/code_tools/agent_parallel_tool.py`：

```python
_SCHEMA = {
    "name": "agent_parallel",
    "description": "并发派多个子代理处理独立子任务",
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "input_files": {"type": "array"},
                        "output_files": {"type": "array"},
                        "forbidden_files": {"type": "array"},
                    },
                    "required": ["task_id", "prompt"],
                },
                "maxItems": 4,
            },
        },
        "required": ["subagents"],
    },
}

async def _handle(args, task_id):
    subagents = args["subagents"]
    if len(subagents) > 4: return error("max 4")
    results = await asyncio.gather(*[
        _run_subagent(sa, task_id) for sa in subagents
    ], return_exceptions=True)
    return json.dumps({"results": [_format(r) for r in results]})
```

### C2 Sprint Contract 注入

子代理 prompt 自动 prepend：

```
# Sprint Contract — task_id=<id>
- input_files: [...]
- output_files: [...]
- forbidden_files: [...]
- success_criteria: <prompt 后半>
```

### C3 subagent_progress WS event

每个 _run_subagent 启动时 + 结束时 emit:
```python
await emit_ws_event(sid, "subagent_progress", {
    "task_id": ..., "status": "starting" / "completed", "ts": time.time()
})
```

### C4 UI SubagentProgressCard

`tauri-app/src/code-panel/SubagentProgressCard.tsx`

### C-tests

5 测试：并发 OK / max 4 限制 / 返结果聚合 / WS event 发出 / 错误恢复

---

## D. Stage D — Feature flag

### D1 FeaturesConfig

`backend/config.py`：

```python
@dataclass
class FeaturesConfig:
    slash_commands: bool = False
    goal_mode: bool = False
    agent_parallel: bool = False
```

### D2 启动期校验

`load_config` 后 log 当前 flag 状态。

---

## E. Stage E — 测试 + 文档

### E1 backend pytest

总计 ≥ 15 新增测试（A5 + B5 + C5）。

### E2 人工测试

`02-manual-test-cases.md` — MR-S-0 ~ MR-S-10：
- ★ MR-S-0 zero regression
- ★ MR-S-1 `/help` 在桌宠 UI 真触发
- ★ MR-S-2 `/goal` 在桌宠 UI 设置 + AgentLoop 真 continue

### E3 README

加 `### Companion + Code 升级 v1（2026-05-25）` 章节。

---

## 子代理派单（M2 + M3 并行）

### 子代理 1：Stage B GoalStore + Checker

- worktree: 无（主线程 master 直跑）
- 改动: backend/deskpet/agent/{goal_store.py, goal_checker.py}
- 测试: tests/test_goal_store.py + tests/test_goal_checker.py
- 不准动: stage A 范围 / stage C 范围

### 子代理 2：Stage C agent_parallel 工具

- worktree: 无
- 改动: backend/deskpet/tools/code_tools/agent_parallel_tool.py
- 测试: tests/test_agent_parallel.py
- 不准动: stage A/B 范围 / 现有 agent_tool.py

### 主线程：Stage A + D + E + 集成

A 是 hot path（前后端联调），主线程做。D + E + 派代理回填，主线程做。
