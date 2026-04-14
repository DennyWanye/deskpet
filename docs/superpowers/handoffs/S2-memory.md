# S2 — 短期对话记忆 HANDOFF

**完成：** 2026-04-14 · 分支 `feat/slice-2-memory`
**Plan：** [plans/2026-04-14-slice-2-memory.md](../plans/2026-04-14-slice-2-memory.md)

---

## 做了什么

- ✅ `backend/memory/base.py`：`MemoryStore` Protocol + `ConversationTurn` 值对象
- ✅ `backend/memory/conversation.py`：`SqliteConversationMemory`（aiosqlite, schema 自动初始化, 父目录自动创建）
- ✅ `SimpleLLMAgent` 扩写：可选 `memory: MemoryStore | None`，默认 `history_limit=6`
  - `memory=None` 时与 S0 行为 100% 等价（保留 test_simple_llm_agent_proxies_tokens 作为回归保底）
  - `memory` 存在时：chat_stream 前 prepend 历史，stream 结束后 persist (user_query, assistant_reply)
- ✅ `main.py` 注入 `memory_store` + 用 `config.memory.db_path` 构造
- ✅ `pyproject.toml` 加 `aiosqlite`

---

## 门控

```
pytest tests/ -v --ignore=tests/test_e2e_pipeline.py
40 passed, 1 skipped in 10.59s
  - 7 new: test_memory.py (SqliteConversationMemory CRUD + 隔离 + 父目录)
  - 4 new: test_agent_provider.py (memory 注入 / 持久化 / session 隔离 / 零变更回归)
  - 29 existing: 全绿

import smoke: import main → OK
```

---

## 偏离 Plan

- **D1** — 语义检索（bge-m3）未做：Plan §1 已经把它划在"非范围"内，此处明确记录：V5 §4.5 的完整版需要独立 slice（模型 ~2GB + 向量索引）
- **D2** — 未改 config.toml：`[memory] db_path` 已在 config.py 默认 `./data/memory.db`，toml 如未提供 `[memory]` 段即用默认值，不需要动
- **D3** — 继续不 push（无 origin，沿袭 S0 HANDOFF §4 D3）

---

## 行数

- 生产代码：`memory/base.py` 25 行 + `memory/conversation.py` 85 行 + `simple_llm.py` +40/-8 = **~140 行生产代码**
- 测试代码：`test_memory.py` 95 行 + `test_agent_provider.py` +86 行 = **~180 行测试**

生产稍多（MVP 总是比抽象估算的更大），但没有过度工程——每一行都覆盖了被测试。

---

## 给 S3/S4

- **S3 不要改 `SimpleLLMAgent`**：新建 `ToolUsingAgent` 包装 `AgentProvider`；注册时替换 `agent_engine` 槽位（memory_store 继续被 SimpleLLMAgent 持有即可）
- **S4 观测**：`SqliteConversationMemory` 的 `append/get_recent` 是热路径上的 I/O，建议 S4 加 log + 耗时 histogram
