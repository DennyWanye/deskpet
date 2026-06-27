# S3 — 工具路由 MVP

**日期：** 2026-04-14
**分支：** `feat/slice-3-tools`
**前置：** S0（AgentProvider）+ S2（memory store）

---

## 1. 范围

### ✅ 范围内（MVP）
- `backend/tools/base.py`：`Tool` Protocol + `ToolSpec` 元数据 dataclass
- `backend/tools/registry.py`：`ToolRegistry`（按 name 查工具；列出所有工具给 LLM）
- `backend/tools/get_time.py`：一个 demo 工具（`get_current_time()` → ISO 时间字符串）
- `backend/agent/providers/tool_using.py`：`ToolUsingAgent` 包装任意 `AgentProvider`
  - 工具调用**文本协议**：LLM 在回复中输出 `<tool>get_time</tool>` 标记 → agent 拦截、执行、将结果注入后续对话
  - 简化：单轮执行（一次调用完成后再 yield 结果给用户；不做递归 tool calling）
- 不改 `SimpleLLMAgent`
- `main.py`：装配 `ToolUsingAgent(base=SimpleLLMAgent(...))` 注册到 `agent_engine`

### ❌ 非范围
- 实际接 Hermes（V5 §12 Phase 2）
- 多轮 tool calling（chain）
- 远程/并发工具
- LLM 参数解析的鲁棒性 — MVP 只认 `<tool>name</tool>` 标记，不解析参数（`get_time` 无参）

### 🔒 协调点（和 S2 衔接）
- `ToolUsingAgent` 的 `chat_stream` 内**必须**转调底层 `base_agent.chat_stream(messages, session_id=...)`
- 底层 agent 的历史读写 / 记忆持久化由 `SimpleLLMAgent` 完成，`ToolUsingAgent` 不碰 memory
- 但 tool 结果注入的是"第二次"LLM 调用——此时若再拉 memory 可能读到自己刚写入的半截——**决策**：第二次 LLM 调用**绕过** memory（把合成的 messages 直接喂 LLM，不经 `SimpleLLMAgent`）。ToolUsingAgent 同时持有 `LLMProvider` 引用做这件事。

---

## 2. 设计要点

### Tool 协议
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # 给 LLM 看的提示

class Tool(Protocol):
    spec: ToolSpec
    async def invoke(self, **kwargs) -> str: ...  # MVP: 返回字符串
```

### ToolRegistry
```python
class ToolRegistry:
    def register(self, tool: Tool) -> None
    def get(self, name: str) -> Tool | None
    def list_specs(self) -> list[ToolSpec]
    def prompt_hint(self) -> str  # 生成 system prompt 片段
```

### ToolUsingAgent 流程
```
chat_stream(messages, session_id):
  1. first_pass = ""
     async for tok in base_agent.chat_stream(messages, session_id):
         first_pass += tok
         yield tok                 # 先让用户看到文字

  2. tool_name = extract_tool_call(first_pass)   # 扫 <tool>xxx</tool>
     if not tool_name:
         return                    # 没触发工具,完毕

  3. tool = registry.get(tool_name)
     if not tool:
         yield "\n[tool not found]"
         return

  4. result = await tool.invoke()
     yield f"\n[tool:{tool_name}] {result}\n"   # 把结果也 stream 给用户
```

**简化点：** 不递归再喂 LLM；直接把工具结果 inline 到用户可见流。这是 MVP 权衡——保留 `<tool>` 触发框架，但先验证 E2E 路径能跑通。Phase 2 可以升级为 ReAct 式多轮。

### LLM prompt hinting
`ToolUsingAgent.__init__` 时构造 system prompt 片段（列出可用工具），在 `chat_stream` 入口如果传入的 messages 首条不是 system，则 prepend 一个。

### 不改 SimpleLLMAgent
S2 HANDOFF §"给 S3/S4"明确要求。ToolUsingAgent 外层包装即可。

---

## 3. 文件清单

### 新增
| 文件 | 估行 |
|---|---|
| `backend/tools/__init__.py` | 0 |
| `backend/tools/base.py` | ~25 |
| `backend/tools/registry.py` | ~35 |
| `backend/tools/get_time.py` | ~25 |
| `backend/agent/providers/tool_using.py` | ~70 |
| `backend/tests/test_tools.py` | ~70 |
| `backend/tests/test_tool_using_agent.py` | ~80 |

### 修改
| `backend/main.py` | +10 / -1 | 装配 registry + ToolUsingAgent |
| `backend/pyproject.toml` | packages += "tools" |

### 预算
- 生产：~165 行（tight but fits — 协议/注册表/工具/Agent 四件事）
- 测试：~150 行

---

## 4. 门控
- `pytest tests/ -v --ignore=tests/test_e2e_pipeline.py`：全绿（期望 ≥50 passed）
- 新测试覆盖：
  - ToolRegistry register/get/list
  - get_time 调用返回 ISO 字符串
  - ToolUsingAgent 在 stream 中检测 `<tool>x</tool>` 并触发
  - ToolUsingAgent 无标记时行为等价于 base agent
  - 不存在的 tool name → 输出 `[tool not found]`
- import smoke：`from agent.providers.tool_using import ToolUsingAgent`

---

## 5. 4 commits
1. `feat(backend): Tool protocol + ToolRegistry + get_time demo`
2. `feat(backend): ToolUsingAgent wraps AgentProvider with text-protocol routing`
3. `feat(backend): wire ToolUsingAgent in main.py`
4. `docs: add S3 plan + HANDOFF`
