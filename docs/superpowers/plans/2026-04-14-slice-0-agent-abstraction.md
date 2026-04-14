# Slice 0 — Agent 抽象层 + 关键残留清理

> **规模：** short-plan（不走 OpenSpec）
> **前置依赖：** 无
> **解锁：** S1-S5 所有后续 slice
> **分支：** `feat/slice-0-agent-abstraction`
> **MCP 调研：** 跳过（纯内部重构，无外部依赖引入）

---

## 1. 目标与动机

**目标：** 把 `main.py:122-141` 里写死的 `llm.chat_stream` 调用，提升为通过 `ServiceContext.agent_engine` 路由的 `AgentProvider` 抽象调用；并清理 3 个关键残留。

**动机（对齐 V5）：**
- V5 §2.3 明确要求 ServiceContext 含 `agent_engine: AgentProvider`，和 `llm_engine` 分层
- V5 §5 Pipeline 表把 "Agent Chat" 作为独立 stage，输入 "文本+上下文"，输出 "token 流"
- V5 §12 风险矩阵要求"Agent 抽象层 + SimpleLLMProvider 降级"作为 Hermes 不稳定的规避措施
- **本 slice 完成后，未来接入 Hermes / 记忆注入 / 工具路由都只需改 AgentProvider 实现，不动 WebSocket 层**

---

## 2. 范围 / 非范围

### ✅ 本 slice 做
- 定义 `AgentProvider` Protocol
- 实现 `SimpleLLMAgent`（薄包装，行为等价于当前直接调 LLMProvider）
- 把 `main.py` 里的 LLM 调用改走 `agent_engine`
- 清理残留：R1（DEV_MODE 硬编码）、R2（LLMConfig 默认值不一致）、R7（inline LLM 调用）
- 新增单元测试

### ❌ 本 slice 不做
- 不引入 tool use / memory retrieval（那是 S2/S3）
- 不接 Hermes（Phase 2）
- 不改 Pipeline 阶段结构（那是 S1）
- 不处理其他残留 R3-R6/R8-R11（留给对应 slice）

---

## 3. 设计

### 3.1 AgentProvider Protocol

**位置：** `backend/agent/providers/base.py`

```python
from __future__ import annotations
from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class AgentProvider(Protocol):
    """
    Agent 层抽象：管理会话级对话循环。
    当前 SimpleLLMAgent 只转发 LLMProvider；未来 HermesAgent / ToolUsingAgent
    在此层注入记忆检索、工具路由、迭代推理。
    """
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[str]: ...
```

**设计理由：**
- `session_id` 从一开始就暴露，S2 接记忆时无需改签名
- 不引入 `tools` / `memory` 参数 —— YAGNI，等 S3 真正需要时再扩展，届时通过 `**kwargs` 向后兼容
- 返回 token 流而非完整文本，匹配 V5 §5 Pipeline 输出契约

### 3.2 SimpleLLMAgent 实现

**位置：** `backend/agent/providers/simple_llm.py`

```python
class SimpleLLMAgent:
    """最小 Agent：直接代理 LLMProvider.chat_stream，无工具、无记忆。
    作为 V5 §12 '降级档'保留，也是当前默认实现。"""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",  # 当前忽略，S2 接记忆时用上
    ) -> AsyncIterator[str]:
        async for token in self._llm.chat_stream(messages):
            yield token
```

### 3.3 main.py 改动点

**A. 清理 R1（DEV_MODE 硬编码）**
```python
# 前:
DEV_MODE = True  # Set False for production

# 后:
DEV_MODE = os.getenv("DESKPET_DEV_MODE", "0") == "1"
```
本地开发：`export DESKPET_DEV_MODE=1`；生产默认关闭。

**B. 注册 agent_engine**
```python
from agent.providers.simple_llm import SimpleLLMAgent
agent = SimpleLLMAgent(ollama_llm)
service_context.register("agent_engine", agent)
```

**C. 改 control_channel LLM 调用（R7）**
```python
# 前 (main.py:126-136):
llm = service_context.llm_engine
if llm:
    async for token in llm.chat_stream([{"role": "user", "content": text}]):
        response_text += token

# 后:
agent = service_context.agent_engine
if agent:
    async for token in agent.chat_stream(
        [{"role": "user", "content": text}],
        session_id=session_id,
    ):
        response_text += token
```

### 3.4 config.py R2 清理

```python
# 前:
class LLMConfig:
    model: str = "qwen2.5:14b"

# 后（与 config.toml 一致）:
class LLMConfig:
    model: str = "gemma4:e4b"
```

---

## 4. 文件清单

### 新增（4 个）
- `backend/agent/__init__.py`（空）
- `backend/agent/providers/__init__.py`（空）
- `backend/agent/providers/base.py`（AgentProvider Protocol）
- `backend/agent/providers/simple_llm.py`（SimpleLLMAgent 实现）
- `backend/tests/test_agent_provider.py`（单测）

### 修改（3 个）
- `backend/main.py`（R1 环境变量 + 注册 agent + R7 改走 agent_engine）
- `backend/config.py`（R2 默认值同步）

### 产出
- `docs/superpowers/handoffs/S0-agent-layer.md`（完成后）

**总计：** 7 处代码变更 + 1 份 HANDOFF

---

## 5. 实施步骤

```
1. 新建分支 feat/slice-0-agent-abstraction
2. 新增 backend/agent/ 包 + base.py + simple_llm.py
3. 写单测 test_agent_provider.py 并跑通
4. 改 config.py (R2)
5. 改 main.py:
   - 顶部 import os
   - R1: DEV_MODE 读环境变量
   - 注册 agent_engine
   - 改 control_channel LLM 调用
6. 跑全量质量门控（见 §6）
7. 写 HANDOFF.md
8. 分组 commit（3-4 个语义化 commit）
9. 等用户批准 → push + open PR → merge 回 master
```

---

## 6. 质量门控

每一步修改后按顺序执行：

```bash
# Backend
cd G:/projects/deskpet/backend
uv run pytest tests/ -v                    # 现有 4 个测试 + 新增 1 个都必须过
uv run ruff check .                        # 如未配置则跳过但记录

# 验证 import 无误
uv run python -c "from agent.providers.simple_llm import SimpleLLMAgent; print('ok')"

# WS 冒烟（后端启动 5 秒确认不 crash）
uv run python main.py &
sleep 3
curl -s http://127.0.0.1:8100/health
kill %1
```

**前端本 slice 无改动，跳过前端门控。**

---

## 7. 验收标准

- [ ] `pytest` 全绿（5 个测试：context/providers/websocket/e2e_pipeline/agent_provider）
- [ ] `/health` 仍返回 `{"status":"ok",...}`
- [ ] WebSocket `/ws/control` 发 `chat` 消息仍收到 `chat_response`（行为完全等价）
- [ ] `service_context.agent_engine` 不再是 None，是 `SimpleLLMAgent` 实例
- [ ] `service_context.llm_engine` 仍然保留（供未来扩展），但 control_channel 不再直接用它
- [ ] `DESKPET_DEV_MODE` 未设时，`_validate_secret` 严格校验共享密钥
- [ ] `config.py` 默认 model 与 `config.toml` 一致
- [ ] 代码行净增 ≤ 100 行（超出说明过度设计）

---

## 8. 提交策略

分成 3 个语义化 commit：

```
1. refactor(backend): introduce AgentProvider abstraction (S0)
   - add backend/agent/providers/{base,simple_llm}.py
   - register SimpleLLMAgent as agent_engine in ServiceContext
   - main.py control channel routes via agent_engine

2. fix(backend): read DEV_MODE from env; sync LLMConfig default
   - R1: DESKPET_DEV_MODE=1 opt-in (was hardcoded True)
   - R2: LLMConfig.model default 'gemma4:e4b' matches config.toml

3. test(backend): add SimpleLLMAgent unit tests
```

HANDOFF 作为独立文件，不进 commit（或单独一个 `docs:` commit）。

---

## 9. 风险与对冲

| 风险 | 概率 | 对冲 |
|---|---|---|
| 重构引入行为偏差，现有 e2e_pipeline 测试挂 | 低 | 单测覆盖 + 保守改动（SimpleLLM 是纯 proxy） |
| `DEV_MODE=os.getenv` 导致本地开发启动后 WS 连不上 | 中 | README 明确写启动前 `set DESKPET_DEV_MODE=1`；或在 plan 执行时临时保留 True 并在 HANDOFF 标注需手动切换 |
| `session_id` 签名现在加了但没用 | 低 | 保留在接口签名，注释说明 S2 会用上 — 一次性到位比未来再破坏性修改好 |

---

## 10. HANDOFF 模板（完成后填）

- 实际变更文件与行数
- 跑过的门控命令与输出摘要
- 偏离本 plan 的地方（如有）
- 遗留的已知问题
- 对 S1-S5 的建议（任何发现）

---

**等待批准 → 开始执行。**
