# S3 — 工具路由 HANDOFF

**完成：** 2026-04-14 · 分支 `feat/slice-3-tools`
**Plan：** [plans/2026-04-14-slice-3-tools.md](../plans/2026-04-14-slice-3-tools.md)

---

## 做了什么

- ✅ `backend/tools/` 新包：`base.py`（Tool Protocol + ToolSpec）/ `registry.py` / `get_time.py`（demo 工具）
- ✅ `backend/agent/providers/tool_using.py`：`ToolUsingAgent` 包装任意 `AgentProvider`
  - 文本协议 `<tool>NAME</tool>` 触发；一次最多一个工具（MVP）
  - 工具结果 inline 附加 `[tool:NAME] RESULT\n` 到 user-facing stream
  - 异常捕获 `[tool error: NAME: EXC]`
  - 未知工具 `[tool not found: NAME]`
  - System prompt 自动注入工具列表（若 caller 未提供）
- ✅ `main.py` 装配栈：`ToolUsingAgent(base=SimpleLLMAgent(ollama, memory))`；注册 `get_time` 到 `tool_router` slot
- ✅ `pyproject.toml` packages 加 `tools`
- ✅ **不改 SimpleLLMAgent**（遵照 S2 HANDOFF §"给 S3"的建议）

---

## 门控

```
pytest tests/ -v --ignore=tests/test_e2e_pipeline.py
57 passed, 1 skipped in 5.66s
  - 8 new: test_tools.py(spec frozen / get_time ISO 合法 / registry CRUD / prompt_hint / 重复拒绝)
  - 9 new: test_tool_using_agent.py(pass-through / 触发 / 未知 / 异常 / system prompt 注入 / caller 已有 system 不覆盖 / session_id 传递 / Protocol 一致性)
  - 40 existing: 全绿

import smoke:
  import main → OK, type(agent) = ToolUsingAgent ✅
```

---

## 设计取舍记录

### T1 — 工具结果不写入 memory
`ToolUsingAgent` inline 注入的 `[tool:xxx]` 文本**不**进 SQLite 对话历史。原因：
- Memory 应反映 LLM 自己说的话，便于下一轮 LLM 读取历史保持语气一致
- Tool 结果是 post-hoc 增强，若进 memory 会被 LLM 误认为自己上一轮发言过
- 未来若要做 ReAct，应该由 LLM 的第二轮 reasoning 生成新文本，而不是直接把 raw result 当作对话

### T2 — 单轮工具（不递归）
MVP 只触发一次工具，不把结果塞回 LLM 求下一轮。V5 §12 对 Hermes 的期待是多轮 reasoning，那是 Phase 2 的事；当前框架已经留好了扩展位。

### T3 — 文本协议 vs Function Calling
选文本协议 `<tool>...</tool>` 是因为：
- Gemma / Ollama 没有 OpenAI function calling 的原生支持
- 协议显式、LLM 好学、用户在日志里一眼能看到触发
- Hermes 将来来了可以换成它的原生协议，`ToolUsingAgent` 替换即可

---

## 偏离 Plan

- **D1** — 增加了 tool_router 到 service_context 注册（plan §3 文件清单未显式写,但 context.py 早已有该 slot,顺手绑定便于 S4 观测）
- **D2** — 多了 "caller 已有 system prompt 时不覆盖" 的测试（plan §4 没列但实现时考虑到了,补测试是便宜的）
- **D3** — 继续不 push

---

## 行数
- 生产：tools/{base, registry, get_time} ~85 + tool_using.py ~75 + main.py +12 = **~170 行生产**
- 测试：test_tools.py 60 + test_tool_using_agent.py 140 = **~200 行测试**

生产超出 plan §3 预算（165）不多，可以接受。

---

## 给 S4

- `ToolUsingAgent.chat_stream` 是热路径的 Agent 入口；S4 加耗时 histogram 和"工具触发率"counter 应落在这里
- `tool.invoke()` 可能抛异常；S4 的 structured log 应捕获 tool_name + elapsed_ms + error
- `service_context.tool_router` 已经注册 —— S4 可能想做"通过 HTTP 动态注册工具"，需要先加鉴权（别暴露 `/tools/register` 给任意客户端）
