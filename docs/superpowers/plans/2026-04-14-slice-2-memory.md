# S2 — 短期对话记忆注入

**日期：** 2026-04-14
**分支：** `feat/slice-2-memory`
**前置：** S0（AgentProvider）+ S1（session_id 已透传到 agent.chat_stream）

---

## 1. 范围

### ✅ 范围内（MVP）
- `backend/memory/base.py`：`MemoryStore` Protocol + `ConversationTurn` dataclass
- `backend/memory/conversation.py`：`SqliteConversationMemory` — 按 session_id 存/取最近 N 轮对话
- 扩写 `SimpleLLMAgent.chat_stream`：
  1. 调用前按 session_id 拉最近 N 轮历史，前置到 messages
  2. LLM 输出完毕后把 (user_query, assistant_reply) 持久化
- `main.py`：构造 `MemoryStore` 注入 `service_context.memory_store` + 传给 `SimpleLLMAgent`
- `config.toml` 已有 `[memory] db_path` 字段，接上即可
- 测试：memory store CRUD + agent 注入路径

### ❌ 非范围
- **语义检索（bge-m3 向量）**— V5 §4.5 的完整版，留给 S2.5 或独立 slice（模型 ~2GB，本 slice 不引入）
- 记忆摘要 / 压缩
- 跨 session 聚合

---

## 2. 设计要点

### MemoryStore Protocol
```python
class MemoryStore(Protocol):
    async def get_recent(self, session_id: str, limit: int = 10) -> list[ConversationTurn]: ...
    async def append(self, session_id: str, role: str, content: str) -> None: ...
    async def clear(self, session_id: str) -> None: ...  # 测试/重置用
```

### SqliteConversationMemory
- schema：`conversation(id INTEGER PK, session_id TEXT, role TEXT, content TEXT, created_at REAL)`
- index：`(session_id, created_at)` 快速按时间降序拉取
- 使用 `aiosqlite` —— async 友好（已在 pipeline 中有 async 惯例）
- 如果 aiosqlite 没装：fallback 到 sqlite3 + `run_in_executor`。先检查。

### SimpleLLMAgent 扩写
- `__init__` 增加可选 `memory: MemoryStore | None = None` 和 `history_limit: int = 6`
- `chat_stream`:
  1. 若 `memory` 存在：`history = await memory.get_recent(session_id, history_limit)` → 转换为 messages 格式前置
  2. 拼好的 `effective_messages` 喂 `self._llm.chat_stream`
  3. 收集完整 response_text（while yielding）
  4. stream 结束：`await memory.append(session_id, "user", user_msg); await memory.append(session_id, "assistant", response_text)`
- 降级：`memory is None` 时完全等价于 S0 版本（零行为变化）

### 注意的兼容性
- S1 VoicePipeline 的 `agent.chat_stream` 消费方期望收到字符串 token 流 —— S2 改造不能破坏此契约
- `test_agent_provider.py` 现有 4 个测试必须全绿（证明降级分支未变）

---

## 3. 文件清单

### 新增
| 文件 | 估行 |
|---|---|
| `backend/memory/base.py` | ~25 |
| `backend/memory/conversation.py` | ~80 |
| `backend/tests/test_memory.py` | ~90 |

### 修改
| 文件 | 估净变化 |
|---|---|
| `backend/agent/providers/simple_llm.py` | +25 / -3 |
| `backend/main.py` | +8 / -1 |
| `backend/pyproject.toml` | +1（aiosqlite） |

---

## 4. 门控

- `pytest tests/ -v --ignore=tests/test_e2e_pipeline.py`：全绿（期望 ≥35 passed）
- 新测试覆盖：store append+get_recent / 多 session 隔离 / agent 注入历史 / memory=None 行为等价
- import smoke：`from memory.conversation import SqliteConversationMemory`

---

## 5. 4 个 commits
1. `feat(backend): MemoryStore protocol + SqliteConversationMemory`
2. `feat(backend): SimpleLLMAgent injects session history via MemoryStore`
3. `feat(backend): wire memory_store in main.py`
4. `docs: add S2 plan + HANDOFF`
