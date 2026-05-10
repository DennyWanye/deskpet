# Code Panel Followups (active list)

最近一次更新：2026-05-09

> P4-S24 已合并的（不再列入）：inline ✕ 退出 code 模式 + 删除项目带确认 +
> thinking-mode reasoning_content round-trip + transient retry。
> 详细 commit 看 `git log --grep="P4-S24"`。

下面 7 项分两组：**LLM/Agent 能力升级** 和 **Code Panel UX 改进**。
按 ROI 排，做完一个划掉一个。

---

## 组 A — LLM / Agent 能力升级

### A1. Streaming tool_call（最高优先）

**问题**
现在 chat 走的是 `chat_with_tools` non-streaming：发请求 → 等 5-30s（thinking
模式 60s+）→ 一次性出结果。期间画面静止，用户不知道 LLM 死没死。

**期望**
LLM 边算边吐字 / 工具调用边出现：
```
T=0.3s "我先" ← 流式
T=1s   [tool_call: read_file]  ← 工具卡片出现
T=1.2s 工具结果嵌入
T=1.5s "里面写了" ← 继续
```

**实现**
- 复用 [openai_compatible.py:60](../backend/providers/openai_compatible.py) 已有的 `chat_stream`
- 给它加 tool_call SSE buffer（参考 [openai_adapter.py:230-298](../backend/llm/openai_adapter.py)
  的 `_stream` —— 那边已经写好了正确的 tool_call 流式 buffer，照搬）
- agent_loop 改成 yield AssistantDeltaEvent / ToolCallEvent
- 前端 MessageStream 加 partial-message 渲染

**工作量** ~80 行 backend + ~50 行 frontend + e2e 验证

---

### A2. Plan / Replan 模式（code mode 默认开）

**问题**
现在 agent_loop 是纯 ReAct，每步局部决策，复杂任务（"做个 todo 应用"）容易
"看到啥做啥"打补丁，14 步才跑通还可能漏需求。

**期望**
两阶段 + 中途 replan：
- Phase 1 (Plan)：LLM 看完整需求 → 输出完整 plan（强制 JSON schema 框死格式，
  跟 A3 联动）→ 前端弹 PlanCard 让用户审/改
- Phase 2 (Execute)：按 plan 走 ReAct loop，每完成一步标 todo completed
- Replan 触发：执行中工具失败 / 发现新事实 → 回 Plan 重规划剩余步骤

**对比已有的**
deskpet 现在 todo_write 工具有点 plan 影子（鼓励 LLM 先开 todo），但**不强制**；
也没"plan 给用户审"的环节。Plan 模式是把这个流程化。

**实现**
- agent_loop 加 `plan_mode: bool` 参数
- 第一次 LLM call 强制 `response_format` 出 plan JSON
- emit 新事件 `plan_event` 给前端
- 新前端组件 `PlanCard.tsx`：渲染 plan 步骤 + "确认/修改/取消" 按钮
- 用户确认 → 进入 execute（现有 ReAct loop）
- 工具失败时 emit `replan_request` 让 LLM 重出 plan

**工作量** ~200 行 backend + 1 个 PlanCard 组件 + UX 调优

---

### A3. Structured Output / JSON Mode

**问题**
现在 LLM 出 JSON 全靠 prompt 里写"按 X 格式输出"。小模型经常翻车：
- gemma4:e4b 出 ` ```json {...} ``` ` 包裹（要正则剥皮）
- 多输出一段中文解释（要 split）
- 半个 JSON 就停（parse error）

实际坑过的：
- [classifier.py](../backend/deskpet/agent/assembler/classifier.py) 任务分 8 类靠关键词兜底
- todo_write 偶尔漏 `activeForm` 字段
- tool_call 参数解析翻车（[openai_compatible.py:233](../backend/providers/openai_compatible.py)）

**期望**
OpenAI / chinzy 用 `response_format: {"type": "json_schema", ...}` 强制合法 JSON。

**实现要点**
- [openai_compatible.py](../backend/providers/openai_compatible.py) 接受
  `response_format` 参数，透传到 payload
- Ollama 不支持 response_format 但支持 `format: "json"`（弱版）—— 分支处理
- 重写 classifier 用 enum schema → 准确率 85% → 99%
- todo_write 工具 schema 已经有了 OpenAI function calling 校验，**但**让 LLM
  输出 plan / 任意结构化结果时，response_format 仍能加层兜底

**工作量** ~50 行 + classifier 重测

---

### A4. Prompt Caching（顺手做）

**问题**
deskpet 已经按 frozen / dynamic 分仓（[bundle.py](../backend/deskpet/agent/assembler/bundle.py)），
设计意图就是 cache 友好，但**请求里没下发 `cache_control` 标记**，白浪费分仓。

**期望**
最后一个 frozen 段加 `"cache_control": {"type": "ephemeral"}`。
- Anthropic 直接生效 → 跨轮 cache_read 计费 0.1×，省 70%+ token 成本
- OpenAI gpt-4o 自动 cache（不用标记，无副作用）
- chinzy / Ollama 大概率忽略字段（无副作用）

**工作量** ~10 行 + 一组 e2e 验证

---

## 组 B — Code Panel UX 改进

### B1. 删除 "← chat" 按钮，sidebar 点击项目直接进 chat

**现象**
现在切换 chat ↔ dashboard 要点 header 上的 ⊞ 仪表盘 / ← chat 按钮。
sidebar 点项目只切 active_sid，不切 view。多此一举。

**期望**
- 删掉 [CodePanelRoot.tsx:101-110](../tauri-app/src/code-panel/CodePanelRoot.tsx) 的 `← chat` 按钮
- sidebar 点项目 → 既切 active_sid 又切 view='chat'
- 保留 `⊞ 仪表盘` 按钮作为"回到总览"的入口（或者放 sidebar header 那个 ⊞ 也行）

**实现**
- [SessionSidebar.tsx](../tauri-app/src/code-panel/SessionSidebar.tsx) `<li onClick>`
  接收一个 `onPick` 回调（CodePanelRoot 传进来 `(sid) => { set_active(sid); set_view('chat'); }`）
- 删掉 `← chat` 按钮 + 相关分支

**工作量** ~20 行

---

### B2. 仪表盘卡片支持完整 chat（todos + 输入框 + 状态）

**现象**
现在 dashboard 卡片只是只读快照（项目名 / 路径 / todos 计数 / 上一句 assistant）。
要进 chat 必须先点卡片切到 chat 视图，单 panel 单时刻只能跟一个项目对话。

**期望**
每张卡片都是一个**迷你 chat panel**：
- 完整 todos 列表（不只是计数，跟 sidebar 那个一样的 ⏳/✓/○ 渲染）
- 底部输入框 + 发送按钮
- 卡片点击不再切 view，就在 grid 里直接发指令收回复

这样可以**多项目同屏并行**：A 项目跑测试时 B 项目我已经发了下一条。

**实现**
- [SessionGridView.tsx Tile](../tauri-app/src/code-panel/SessionGridView.tsx) 扩展：
  - todos 子列表（复用 SessionSidebar 的渲染逻辑，抽个共享组件）
  - 底部 chat input + 发送按钮（参考 [InputBar.tsx](../tauri-app/src/code-panel/InputBar.tsx)）
  - 滚动 messages（精简版，只显示最近 N 条）
- 卡片点击行为重新设计：
  - 点空白处 ≠ 切 active；点输入框聚焦那张卡的 input
  - 双击或专门按钮 → 进单 chat 全屏视图（保留这条路）
- 注意**并发上限** — 已有的 [chatLimiter](../tauri-app/src/stores/sessionsStore.ts) max=2，
  超过的卡片排队（输入框可以先打字、按发送时进队列）

**工作量** ~150 行 + UX 调优 + 多项目压测

---

### B3. 发送/停止按钮联动；执行中可以打断

**现象**
任务执行中按钮一直显示"发送"，再按一次会取消旧任务并发新的（main.py:2180
`_prev_task.cancel()`），但用户没法**只停止不发新消息**。

**期望**
- 当前 session 在跑（`inflight=true` / `status="thinking"|"running"`）时：
  发送按钮变成 **停止** 按钮
  - 点停止 → 发 ws `chat_v2_interrupt` { session_id }
  - 后端 cancel 对应 _chat_inflight[sid] 任务
  - 按钮恢复成"发送"，输入框可重新输入
- 不在跑时：正常的 发送 行为

**实现**
- 后端：[main.py](../backend/main.py) 加 `chat_v2_interrupt` IPC handler：
  ```python
  elif msg_type == "chat_v2_interrupt":
      sid = raw.get("payload", {}).get("session_id") or session_id
      task = _chat_inflight.get(sid)
      if task and not task.done():
          task.cancel()
          await ws.send_json({
              "type": "chat_v2_interrupted",
              "payload": {"session_id": sid},
          })
  ```
- 前端：InputBar / Tile input 都改成根据 `session.inflight` 切换按钮文本 + handler
  - 已经有 `inflight` 字段（[sessionsStore.ts](../tauri-app/src/stores/sessionsStore.ts)），
    需要确认 ws 的 chat_v2_final / chat_v2_error / chat_v2_interrupted 都把它清回 false
- B2 的卡片输入框天然继承这个逻辑（共享同一组件）

**工作量** ~50 行 backend + ~40 行 frontend

---

### B4. Code 模式项目数据持久化（每次重启 deskpet 项目消失）

**现象**
每次重启 deskpet 后，code panel 里之前添加的项目 session 全丢了 —
sidebar 显示"暂无 Code 模式会话"，dashboard 显示 (0)。要重新 + 新项目。

**根因**（猜测，需验证）
[deskpet/code_mode/state.py](../backend/deskpet/code_mode/state.py) 的
`CodeModeManager` 只把 `self._states: dict[str, CodeModeState]` 放在内存里
（行 50），没任何持久化。SessionDB 里只有 `code_todos` 表存了 todos，
没有 `code_sessions` 表存项目登记 (base_session_id, project_root, project_name)。

进程一退就全丢，下次启动从空 dict 开始。

**期望**
重启 deskpet 后，之前添加过的项目自动恢复在 sidebar / dashboard 里，
点进去能继续聊（chat 历史本来就有，因为 SessionDB 里存了 messages 行，
只是 CodeModeManager 不知道有这些项目）。

**实现**
- 新 migration `005_p4s25_code_sessions.sql`：
  ```sql
  CREATE TABLE IF NOT EXISTS code_sessions (
      base_session_id  TEXT PRIMARY KEY,
      code_session_id  TEXT NOT NULL,
      project_root     TEXT NOT NULL,
      project_name     TEXT NOT NULL,
      created_at       REAL NOT NULL DEFAULT (julianday('now')),
      last_active_at   REAL NOT NULL DEFAULT (julianday('now'))
  );
  CREATE INDEX IF NOT EXISTS idx_code_sessions_active
      ON code_sessions(last_active_at DESC);
  PRAGMA user_version = 13;
  ```
- [session_db.py](../backend/deskpet/memory/session_db.py)：加 `upsert_code_session`,
  `delete_code_session`, `list_code_sessions` 三个方法
- [code_mode/state.py CodeModeManager](../backend/deskpet/code_mode/state.py)：
  - `enter()` 调 upsert 把项目存盘
  - `exit()` 不删 DB（保留登记，只清 enabled 内存态）—— 这样重启能复活
  - 新加 `delete()` 真正删 DB（对应 code_session_delete IPC）
  - 新加 `async load_persisted(sdb)` 启动时调
- [main.py](../backend/main.py)：lifespan 里 `await cmm.load_persisted(sdb)`
  把所有 DB 行恢复到内存
- `code_session_delete` IPC：除了 cmm.exit + 删 todos，还要删 code_sessions 行
- 前端：没改动 — `code_sessions_list_response` 已经是 backend 的源头，
  恢复后自动出现

**注意**
- 持久化不等于 enabled。重启时 `code_sessions` 行恢复到内存，但
  `state.enabled = True`（用户既然之前在这个项目里，恢复后默认还在）
- 多 panel 窗口同时打开时 (虽然不常见) 状态以 DB 为单一真理
- 老的 in-memory only 行为有人依赖吗？看下 tests，没的话直接换

**工作量** ~80 行 + migration + 一组测试

---

## 总优先级

按"对当前痛点的修复力度 × 工作量"排：

1. **B4** 项目持久化（每次重启都消失，用户已 explicit 反馈）— **新增最高优先**
2. **B3** 停止按钮 — 当前痛点（用户卡住没法救）
3. **A1** Streaming — 体感巨大，工作量适中
4. **B1** sidebar 直接进 chat — 小工作量、清掉已有摩擦
5. **B2** 卡片完整 chat — 工作量大但对多项目用户提升明显
6. **A2** Plan/Replan — code mode 复杂任务质量
7. **A3** Structured output — 配合 A2 顺势做
8. **A4** Prompt cache — 几行代码顺便加
