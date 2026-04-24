# PRD: DeskPet Agent Harness + Long-term Memory

**代号**: Poseidon（海神，统帅桌宠所有"深水区"能力）
**目标版本**: `v0.6.0-phase4`
**作者角色**: 系统架构师
**日期**: 2026-04-24
**状态**: Draft — 待你 review 后转 OpenSpec propose

---

## 0. TL;DR（给决策者）

当前桌宠是 **pipeline**（ASR → LLM → TTS，无状态无工具无记忆）。本 PRD 把它升级成 **agentic desktop companion**：

- **Agent loop**：直接拿 `NousResearch/hermes-agent` 的 `AIAgent.run_conversation()` 循环
- **记忆三层**：文件记忆（MEMORY.md + USER.md）+ 会话库（SQLite + FTS5）+ 向量记忆（sqlite-vec）—— 前两层拿 Hermes 的，第三层自己加
- **工具系统**：拿 Hermes `tools/registry.py` 自动发现机制；拿 `claude-code-best/claude-code` 的工具分类（AgentTool / TaskCreate / TodoWrite / WebSearch / MCP）
- **MCP 支持**：拿 `claude-code-best/claude-code/packages/mcp-client` 的协议实现
- **Skill 系统**：兼容 `agentskills.io` 开放标准（Hermes / Claude Code 共用）
- **智能上下文组装器（ContextAssembler）**：**原创** —— 在进 agent loop 前先识别任务类型（闲聊 / 回忆 / 任务 / 代码 / 搜索 / 规划 / 情绪），按策略**动态挑选并组合**记忆切片、工具子集、skill、persona、时间/工作区等上下文组件，再把装好的 `ContextBundle` 交给大模型

**性能目标**：turn latency < 800ms p50、记忆召回 < 30ms p95、工具往返 < 50ms p50。

**预估工作量**：11 个 slice × 1-2d = **~15 人日**（纯实现，不含 spec + 验证）。

**拿来主义占比**：约 **60% 代码直接 lift**（Python 同语言的 Hermes 模块）+ **20% 架构模式照抄**（TypeScript 的 Claude Code Best 需要 port 到 Python）+ **20% 新代码**（桌宠专属：ASR/TTS 接线、Live2D 联动、向量层）。

---

## 1. 问题陈述

### 1.1 当前桌宠的限制（`v0.5.0-phase3-rc1`）

```
[User voice/text]
    ↓
[faster-whisper ASR]
    ↓
[LLM /chat HTTP 调用]   ← 无工具、无记忆、无多轮规划
    ↓
[TTS (GPT-SoVITS)]
    ↓
[Live2D 嘴型同步]
```

缺口（按严重度排序）：

1. **无长期记忆**：每次开机从零开始，主人讲过的事忘得一干二净
2. **无工具调用**：只会闲聊，不能查天气、打开文件、搜网、读本地笔记
3. **无多轮规划**：做不了"先搜再总结再提醒我明天"这类复合任务
4. **无 skill 系统**：所有"我会做什么"写死在 prompt，没法用户扩展
5. **无 MCP**：不能接入 Claude Code 生态（filesystem / web / docker 等）
6. **context 无压缩**：长对话必然炸 token 限制
7. **无子任务 / 并行**：想让它"边放音乐边写笔记"只能串行
8. **context 无组装**：每次对话都把全套工具 + 全套记忆塞进 prompt，既浪费 token 又稀释注意力 —— 该闲聊时不需要 `file_grep`，该查文件时不需要昨天的心情记录

### 1.2 目标

把桌宠升级成具备以下能力的 **自主 agent**：

- **长期记忆**：记住主人讲过的事、偏好、习惯；跨会话召回
- **工具调用**：本地文件 / 网页搜索 / 日历 / MCP servers
- **多轮规划**：ReAct 循环 + plan-mode（非破坏性规划）
- **Skill 扩展**：用户写 markdown 就能加新能力
- **可中断**：说一句就打断当前工具链
- **性能优先**：**本次重构不考虑实现难度和 token 消耗**，延迟和记忆质量优先于成本

### 1.3 非目标（明确 scope out）

- ❌ 消息网关（Telegram / Discord / Slack / 微信）—— 桌宠不是 bot，留在本机
- ❌ Remote Control / 手机端控制 —— 桌宠在主人机器上，不远程
- ❌ Batch runner / RL 训练环境（Atropos）—— 科研工具，桌宠不需要
- ❌ ACP 协议（IDE 集成）—— 非优先，桌宠不是 dev tool
- ❌ Cron scheduler —— Phase 5+ 再加
- ❌ Multi-tenant / 多用户 —— 桌宠是 single-user product

---

## 2. 拿来清单（核心决策）

### 2.1 上游项目

| 项目 | Stars | 语言 | License | 用途 |
|---|---|---|---|---|
| [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) | 113k | **Python** | **MIT** ✅ | **主拿来源** —— Python 同栈，直接 lift |
| [claude-code-best/claude-code](https://github.com/claude-code-best/claude-code) | 16.7k | TypeScript (Bun) | ⚠️ 无 license | **架构参考** —— TS 不能直接用，pattern 照搬 |

### 2.2 License 合规

- **Hermes** 是 MIT，文件顶部保留 copyright notice 即可直接 lift 源码
- **Claude Code Best** 仓库无 license 文件，**不可直接复制源码**。只能：
  1. 阅读学习架构 pattern
  2. 重新实现（clean-room rewrite）
  3. 或通过 issue 联系作者澄清许可

    本 PRD 的所有"拿 claude-code-best"均指 **学习 pattern 后 clean-room 重写**，不复制源码。

### 2.3 拿来映射表

| 功能模块 | 来自 | 具体文件 / 类 | 处理方式 |
|---|---|---|---|
| **Agent 主循环** | Hermes | `run_agent.py::AIAgent.run_conversation()` | **Lift**（~500 行） |
| **Token 预算追踪** | Hermes | `run_agent.py::IterationBudget` | **Lift** |
| **中断机制** | Hermes | `tools/interrupt.py` | **Lift** |
| **会话数据库** | Hermes | `hermes_state.py::SessionDB` (schema v8) | **Lift + 扩展向量列** |
| **文件记忆** | Hermes | `tools/memory_tool.py` (MEMORY.md + USER.md) | **Lift** |
| **Memory manager** | Hermes | `agent/memory_manager.py` | **Lift** |
| **Memory provider 抽象** | Hermes | `agent/memory_provider.py` | **Lift** |
| **Context 压缩** | Hermes | `agent/context_engine.py` + `context_compressor.py` | **Lift** |
| **Prompt 缓存优化** | Hermes | `agent/prompt_caching.py` | **Lift** |
| **Memory fencing** (`<memory-context>`) | Hermes | `agent/memory_manager.py::build_memory_context_block` | **Lift** |
| **Tool registry** | Hermes | `tools/registry.py` | **Lift** |
| **Skill loader** | Hermes | `agent/skill_commands.py` + `skills/` | **Lift** |
| **Todo / Task 工具** | Hermes | `tools/todo_tool.py` | **Lift** |
| **Web search 工具** | Hermes | `tools/web_search_tool.py` (if exists) | **Lift** or **Claude-Code-Best pattern 重写** |
| **File ops 工具** | Hermes | `tools/file_tools.py` | **Lift** |
| **Delegate / 子 agent** | Hermes | `tools/delegate_tool.py` | **Lift** |
| **Error 分类** | Hermes | `agent/error_classifier.py` | **Lift** |
| **重试策略** | Hermes | `agent/retry_utils.py` | **Lift** |
| **MCP client** | Claude-Code-Best | `packages/mcp-client` (TypeScript) | **Port to Python** (clean-room) |
| **AgentTool 模式** | Claude-Code-Best | `packages/builtin-tools/tools/AgentTool` | **Pattern 参考** |
| **TaskCreate / List / Update / Get** | Claude-Code-Best | `tools/TaskCreateTool` etc | **Pattern 参考** |
| **TodoWriteTool** (持久化 todo) | Claude-Code-Best | `tools/TodoWriteTool` | **Pattern 参考** |
| **Plan mode** | Claude-Code-Best | `tools/EnterPlanModeTool` + `ExitPlanModeV2Tool` | **Pattern 参考** |
| **AskUserQuestion 工具** | Claude-Code-Best | `tools/AskUserQuestionTool` | **Pattern 参考** |
| **ToolSearch**（按需加载工具） | Claude-Code-Best | `tools/ToolSearchTool` | **Pattern 参考** |
| **Feature flag 系统** | Claude-Code-Best | `feature('FLAG_NAME')` via `bun:bundle` | **Pattern 参考**，Python 用 env var + settings.json |
| **Prompt caching breakpoint** | Claude-Code-Best | `PROMPT_CACHE_BREAK_DETECTION` feature | **Pattern 参考** |
| **ContextAssembler**（智能上下文组装器）| 原创 | — | **新写**（参考 CCB `feature()` flag + Hermes `skill_commands.py` 动态加载的思想，但组件化 orchestrate 是 DeskPet 独创）|
| **TaskClassifier**（任务类型识别）| 原创 | — | **新写**（规则 + embedding 相似度 + 小 LLM 三层决策）|

---

## 3. 架构设计

### 3.1 高层架构

```
┌────────────────────────────────────────────────────────────────┐
│                    Tauri Frontend (Rust)                       │
│  Live2D + ChatPanel + SettingsPanel + MemoryPanel(new)         │
└────────────────────────────────────────────────────────────────┘
                              ↕ IPC (JSON over stdio + HTTP)
┌────────────────────────────────────────────────────────────────┐
│                    Python Backend (FastAPI)                    │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │           DeskPetAgent (lift from AIAgent)              │   │
│  │                                                         │   │
│  │  ┌──────────────────────────────────────────────────┐   │   │
│  │  │  ContextAssembler  (NEW —— 事前智能组装)          │   │   │
│  │  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │   │   │
│  │  │  │TaskClassi-  │ │Component    │ │Assembly     │  │   │   │
│  │  │  │fier         │→│Registry     │→│Policy +     │  │   │   │
│  │  │  │(<20ms)      │ │(memory/tool │ │Budget Alloc │  │   │   │
│  │  │  │             │ │ /skill/...) │ │             │  │   │   │
│  │  │  └─────────────┘ └─────────────┘ └──────┬──────┘  │   │   │
│  │  │                                          │         │   │   │
│  │  │                                ContextBundle       │   │   │
│  │  └──────────────────────────────────────────┼───────┘   │   │
│  │                                             ↓             │   │
│  │  ┌──────────────┐  ┌────────────────┐ ┌─────────────┐   │   │
│  │  │ Agent Loop   │  │ Context Engine │ │ Budget &    │   │   │
│  │  │ (ReAct)      │  │ (Compressor,   │ │ Interrupt   │   │   │
│  │  │              │  │  事后压缩)     │ │             │   │   │
│  │  └──────┬───────┘  └────────────────┘ └─────────────┘   │   │
│  │         │                                               │   │
│  │  ┌──────▼───────────────────────────────────────────┐   │   │
│  │  │              Memory Manager                       │  │   │
│  │  │  ┌──────────┐ ┌──────────────┐ ┌──────────────┐   │  │   │
│  │  │  │Builtin   │ │Vector        │ │Plugin        │   │  │   │
│  │  │  │(files+DB)│ │(sqlite-vec)  │ │(optional)    │   │  │   │
│  │  │  └──────────┘ └──────────────┘ └──────────────┘   │  │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  │         │                                               │   │
│  │  ┌──────▼───────────────────────────────────────────┐   │   │
│  │  │                Tool Registry                      │   │   │
│  │  │  builtin (15) + skills (dynamic) + MCP (dynamic)  │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                │
│  ┌──────────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────┐   │
│  │  ASR (f-w)   │ │ TTS      │ │ LLM       │ │ Embedding   │   │
│  │  whisper     │ │ GPT-     │ │ Client    │ │ BGE-M3      │   │
│  │  large-v3    │ │ SoVITS   │ │ (multi-   │ │ (local GPU) │   │
│  │              │ │          │ │  provider)│ │             │   │
│  └──────────────┘ └──────────┘ └───────────┘ └─────────────┘   │
└────────────────────────────────────────────────────────────────┘
                              ↕
┌────────────────────────────────────────────────────────────────┐
│  Storage (%AppData%\deskpet\ + %LocalAppData%\deskpet\)        │
│                                                                │
│  %AppData%\deskpet\                                            │
│    ├── state.db                  (SessionDB + FTS5 + vec)      │
│    ├── memories\                                               │
│    │   ├── MEMORY.md             (桌宠的观察)                  │
│    │   └── USER.md               (主人画像)                    │
│    ├── skills\                   (用户自定义)                  │
│    ├── config.toml                                             │
│    └── logs\                                                   │
│                                                                │
│  %LocalAppData%\deskpet\                                       │
│    └── models\                                                 │
│        ├── faster-whisper-large-v3-turbo\                      │
│        ├── bge-m3-int8\           (NEW — embedding)            │
│        └── llama-cpp-cache\       (可选本地 LLM)               │
└────────────────────────────────────────────────────────────────┘
                              ↕
┌────────────────────────────────────────────────────────────────┐
│               External MCP Servers (subprocess)                │
│  filesystem │ web-search(brave) │ weather │ calendar │ ...     │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 Agent 主循环（拿 Hermes 的）

伪代码（对应 `hermes run_agent.py::run_conversation` + 桌宠适配）：

```python
class DeskPetAgent(AIAgent):  # 继承或直接 lift
    def run_conversation(
        self,
        user_message: str,
        system_message: str = None,
        conversation_history: list = None,
        task_id: str = None,
    ) -> dict:
        # 1. 智能上下文组装（新增 —— 桌宠专属；见 §3.8）
        #    事前根据任务类型挑选和组合组件，再进入 loop
        bundle: ContextBundle = self._context_assembler.assemble(
            user_message=user_message,
            history=conversation_history,
            conversation_id=task_id,
        )
        messages = bundle.build_messages(system_message)  # 已含 memory_block / tool schemas / skill prelude

        # 2. （prefetch 现在由 Assembler 接管，不再独立调用）
        #    但下一轮预热仍由 MemoryManager 保留：
        self._memory_manager.queue_prefetch_all(user_message, hint=bundle.task_type)

        # 3. 进入 agent loop（lift from Hermes）
        api_call_count = 0
        while (
            api_call_count < self.max_iterations
            and self.iteration_budget.remaining > 0
        ) or self._budget_grace_call:
            if self._interrupt_requested:
                break

            # 4. Context 压缩（lift from Hermes，事后压缩，和 Assembler 互补）
            if self._context_engine.should_compress():
                messages = self._context_engine.compress(messages)

            # 5. LLM 调用（带 prompt caching breakpoint — 参考 claude-code-best）
            #    工具 schema 用 Assembler 挑选出来的子集，不是全量
            response = self._llm_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=bundle.tool_schemas,
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )

            self._context_engine.update_from_response(response.usage)

            # 6. 处理 tool calls（lift from Hermes）
            if response.tool_calls:
                for tc in response.tool_calls:
                    # 桌宠适配：TTS 提前说 "让我查一下..."（新增）
                    if self._should_voice_narrate(tc):
                        self._tts_queue.enqueue(f"让我{tc.narration}...")

                    result = handle_function_call(tc.name, tc.args, task_id)
                    messages.append(self._tool_result_message(tc.id, result))

                api_call_count += 1
            else:
                # 7. 最终回复 —— 同步异步记忆写入（新增）
                self._memory_manager.sync_all(user_message, response.content)
                # 给 Assembler 反馈实际调用的组件（用于策略学习）
                self._context_assembler.feedback(
                    bundle=bundle,
                    used_tools=[tc.name for tc in (response.tool_calls or [])],
                    final_response=response.content,
                )
                return {
                    "final_response": response.content,
                    "messages": messages,
                    "api_call_count": api_call_count,
                    "usage": self._context_engine.last_total_tokens,
                }

        # 超过迭代预算，兜底返回
        return {"final_response": "（到达迭代上限，请换种方式问我）", ...}
```

**关键适配点**（相对 Hermes 原版）：

1. 插入 **ContextAssembler**（第 1 步）—— 事前按任务类型智能挑选组件，生成 `ContextBundle`
2. 工具 schema 来自 `bundle.tool_schemas`（Assembler 挑的子集），而非全量 registry
3. 工具调用时可 **TTS 预语音化**（"让我查一下 ..."）→ 减感知延迟
4. 每轮结束 **异步写记忆** + **Assembler feedback**（不阻塞 TTS）
5. **中断**来自 Live2D 窗口检测到用户又开口

### 3.3 记忆系统（三层）

#### 3.3.1 L1 文件记忆（MEMORY.md + USER.md）—— Lift Hermes

**两个文件，两个角色**：

- `MEMORY.md`：桌宠自己的观察笔记（"主人晚上 9 点后不喜欢高音 TTS"）
- `USER.md`：主人画像（"主人叫 Denny，做 AI 开发，常用 Python"）

**入口机制**（lift Hermes `memory_tool.py`）：

```python
# Tool: memory(action, content, match)
# action: add | replace | remove | read
# 条目分隔符: "\n§\n"（section sign）
# 字符限制：MEMORY.md < 50KB, USER.md < 20KB
```

**System prompt 注入模式**（lift Hermes "frozen snapshot"）：
- **会话开始时**将当前 MEMORY.md + USER.md 快照注入 system prompt
- **会话中的写入**立即落盘但**不改** system prompt（保护 prompt cache）
- **下次会话开始**时读新版本

**为什么这样设计**（Hermes 原注释）：
> Frozen snapshot pattern: system prompt is stable, tool responses show live state.
> This preserves the prefix cache for the entire session.

#### 3.3.2 L2 会话数据库（SQLite + FTS5）—— Lift Hermes

**表结构**（直接 lift Hermes schema v8，稍做裁剪）：

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,              -- 'cli' | 'voice' | 'text' | 'task'
    user_id TEXT,                      -- 桌宠 single-user, 固定 'owner'
    model TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,            -- 压缩分裂链
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    title TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,                -- system | user | assistant | tool
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,                   -- JSON
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    reasoning TEXT,

    -- 桌宠扩展列（相对 Hermes 增加）
    embedding BLOB,                    -- 1024-d FP16 vector
    salience REAL DEFAULT 0.5,         -- 0-1, 重要度
    decay_last_touch REAL,             -- 上次被召回时间戳
    user_emotion TEXT,                 -- 'happy' | 'sad' | 'neutral' | 'angry' | ...
    audio_file_path TEXT               -- 对应的原始语音文件（可选）
);

-- Lift Hermes FTS5
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);
-- + 对应 3 个 trigger 保持同步

-- 桌宠新增：向量索引（sqlite-vec extension）
CREATE VIRTUAL TABLE messages_vec USING vec0(
    message_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
```

**设计要点**（沿用 Hermes）：
- **WAL mode**，读写分离
- **应用层 retry with jitter** 应对多进程竞争（TUI + backend + task worker 同时写）
- **Schema 迁移**：`schema_version` 表 + 顺序 SQL 升级脚本

#### 3.3.3 L3 向量记忆（sqlite-vec）—— 新增

**技术选型**：
- **sqlite-vec**（原 sqlite-vss 后继）—— 嵌入式、列式、无额外进程
- **不**用 LanceDB / Qdrant / Chroma —— 都要单独的 server 进程，桌宠要极致轻量
- **不**用 FAISS —— 持久化和增量更新都麻烦

**Embedding 模型**：
- **BGE-M3**（1024 维）本地 INT8 on CUDA
- 或 **Qwen3-Embedding-0.6B** 作为备选
- 复用 ASR 的 CUDA context（同一块 GPU）

**Embedding 时机**：
- 每条 user / assistant message **异步**（worker queue）计算 embedding
- 批量：每 8 条或每 2 秒 flush 一次
- 失败重试：LLM 响应写入和 embedding 解耦，embedding 失败不阻塞对话

**检索策略**（混合检索）：

```python
def recall(user_query: str, k: int = 5) -> list[Memory]:
    q_emb = embed(user_query)

    # 1. 向量召回 top-20
    vec_hits = db.execute("""
        SELECT message_id, distance
        FROM messages_vec
        WHERE embedding MATCH ?
        ORDER BY distance LIMIT 20
    """, (q_emb,)).fetchall()

    # 2. FTS5 召回 top-20
    fts_hits = db.execute("""
        SELECT rowid FROM messages_fts
        WHERE messages_fts MATCH ?
        ORDER BY rank LIMIT 20
    """, (user_query,)).fetchall()

    # 3. Reciprocal Rank Fusion + recency + salience 重排
    scored = fuse_and_rerank(
        vec_hits, fts_hits,
        weights={"vec": 0.5, "fts": 0.3, "recency": 0.15, "salience": 0.05},
    )
    return scored[:k]
```

**性能**（期望）：
- 10 万条规模，向量召回 p95 < 20ms
- FTS5 召回 < 5ms
- 融合重排 < 5ms
- 总 recall < 30ms p95 ✅

### 3.4 Tool registry（Lift Hermes）

**自动发现机制**（lift `tools/registry.py`）：

```python
# deskpet/tools/registry.py
class ToolRegistry:
    def register(self, name: str, toolset: str, schema: dict,
                 handler: Callable, check_fn: Callable = None,
                 requires_env: list[str] = None):
        ...

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        """Returns OpenAI-format tool schemas for LLM."""

    def dispatch(self, name: str, args: dict, task_id: str = None) -> str:
        """Invoke tool, return JSON string."""

registry = ToolRegistry()  # module-level singleton

# 自动发现：import deskpet.tools 时所有 tools/*.py 被加载，
# 每个文件顶部调用 registry.register(...) 登记自己
```

**MVP 工具清单**（15 个，分 4 类）：

| 类别 | 工具 | 来源 | 优先级 |
|---|---|---|---|
| **记忆** | `memory_write` / `memory_read` / `memory_search` | Hermes + 扩展 | P0 |
| **任务** | `todo_write` / `todo_complete` | Hermes + Claude-Code-Best pattern | P0 |
| **文件** | `file_read` / `file_write` / `file_glob` / `file_grep` | Hermes | P1 |
| **网络** | `web_search` (brave) / `web_fetch` | Claude-Code-Best pattern | P1 |
| **子任务** | `delegate` (spawn 子 agent) | Hermes `delegate_tool.py` | P1 |
| **Skill** | `skill_invoke` | Hermes `skill_commands.py` | P1 |
| **MCP** | `mcp_call` (动态 MCP 工具 dispatch) | Claude-Code-Best `mcp-client` pattern | P2 |

**Post-MVP（Phase 5）**：
- `cron_create` / `cron_list` / `cron_delete`
- `computer_use`（截图 + 键鼠，已有 MCP）
- `chrome_use`（浏览器控制，已有 MCP）
- `image_generate`（fal.ai / SD）
- `code_execute`（沙箱 Python）

### 3.5 Skill 系统（Lift Hermes）

**目录结构**（与 Hermes / Claude Code 兼容）：

```
%AppData%\deskpet\skills\
├── built-in\                    (仓库 ship)
│   ├── recall-yesterday\
│   │   ├── SKILL.md             (YAML frontmatter + body)
│   │   └── script.py            (可选，Python 脚本)
│   ├── summarize-day\
│   └── weather-report\
└── user\                        (用户放置的 skill，可热加载)
    └── <user-authored>\
```

**SKILL.md 格式**（兼容 agentskills.io 标准）：

```markdown
---
name: recall-yesterday
description: 回忆昨天和主人聊过的重要事
version: 1.0
author: DeskPet Team
---

# Recall Yesterday

## Instructions
1. 查询 messages 表 WHERE timestamp > 24h ago
2. 过滤 salience > 0.6 的条目
3. 按主题聚合后返回 3-5 条摘要
```

**调用方式**：
- 用户说 "/recall-yesterday"（前端识别为 slash command）
- LLM 调用 `skill_invoke(name="recall-yesterday")`
- 后端：load SKILL.md → 注入为 user message（非 system，保 cache）→ 执行

### 3.6 Context 压缩（Lift Hermes）

**触发条件**（默认，可配置）：
- prompt_tokens > 75% × context_length
- 保留 first_n=3（system + 最早 2 条）+ last_n=6（最近 6 条）
- 中间轮次 → 单条 summary assistant message

**ContextEngine 接口**（lift Hermes `agent/context_engine.py`）：

```python
class ContextEngine(ABC):
    @abstractmethod
    def update_from_response(self, usage: dict) -> None: ...
    @abstractmethod
    def should_compress(self, prompt_tokens: int = None) -> bool: ...
    @abstractmethod
    def compress(self, messages: list, current_tokens: int = None) -> list: ...

# 默认实现：ContextCompressor（lift Hermes）
# 插件点：plugins/context_engine/<name>/
```

### 3.7 MCP 客户端（拿 Claude-Code-Best 协议，port to Python）

**协议**：JSON-RPC over stdio / SSE / HTTP stream
**实现**：用 `mcp` 官方 Python SDK（`pip install mcp`）+ 参考 claude-code-best 的连接管理 pattern

**连接生命周期**：
- 启动时 spawn 配置中的 MCP servers（subprocess）
- 每个 server 一个 ClientSession，长连接
- 崩溃自动重连（retry with backoff）
- `mcp_call(server_name, tool_name, args)` 动态 dispatch

**桌宠 MVP 附带的 MCP servers**：
- `@mcp/filesystem` —— 本地文件访问（限定到 `%AppData%\deskpet\workspace\`）
- `@mcp/brave-search` —— 网页搜索（需 API key）
- `@mcp/weather` —— 天气（自研或用现成）
- （可选）`@ccd_session` —— Claude Code Session（如果主人在用）

### 3.8 ContextAssembler —— 智能上下文组装器（**原创**）

**定位**：agent loop **之前**的 preprocessing layer；输入 `user_message` + history，输出一个装好的 `ContextBundle`。

**与 ContextEngine 的区别**：
| | ContextAssembler | ContextEngine |
|---|---|---|
| 时机 | **事前**（进 loop 前一次性组装）| **事中**（prompt 膨胀后压缩）|
| 输入 | 用户原始消息 | 当前 messages list |
| 决策 | 挑哪些组件塞进去 | 挑哪些消息丢出来 |
| 性能预算 | <70ms（Classifier 20ms + Assembly 50ms）| 压缩时一次性 <2s |
| 能否跳过 | 不能 —— 每轮必过 | 能 —— 仅在阈值触发 |

两者**互补**，不重叠。

#### 3.8.1 核心数据流

```
user_message: "昨天你提到的那本书叫什么？"
            │
            ▼
┌─────────────────────────┐
│   TaskClassifier        │    rule match "昨天|之前|那个" → 命中 "recall"
│   (<20ms)               │    embedding sim with task exemplars → "recall" (0.87)
│                         │    optional tiny-LLM verify → "recall"
└────────┬────────────────┘
         │ task_type = "recall"
         ▼
┌─────────────────────────┐
│   AssemblyPolicy        │    lookup: recall → [
│   (yaml/py rules)       │      memory(L1=snapshot, L2=fts+recent 20, L3=vec top10),
│                         │      tools=[memory_read, memory_search],
│                         │      skills=[recall-yesterday if registered],
│                         │      persona=brief,
│                         │      time=current,
│                         │    ]
└────────┬────────────────┘
         │ plan: list[ComponentRequest]
         ▼
┌─────────────────────────┐
│   ComponentRegistry     │    并行调用各组件的 .provide(ctx):
│   (parallel fan-out)    │      MemoryComponent.provide() → 120 tokens
│                         │      ToolComponent.provide()   → 800 tokens
│                         │      SkillComponent.provide()  → 200 tokens
│                         │      PersonaComponent.provide() → 150 tokens
│                         │      TimeComponent.provide()    → 30 tokens
└────────┬────────────────┘
         │ raw materials
         ▼
┌─────────────────────────┐
│   BudgetAllocator       │    token_budget = ctx_window × 0.6
│                         │    若超：按 component.priority 砍次要 / 截断 memory.top_k
└────────┬────────────────┘
         │
         ▼
      ContextBundle:
        ├── frozen:  system_prompt + persona + time  ← 走 prompt cache
        ├── dynamic: memory_block + skill_prelude
        ├── tool_schemas: [...]  ← 精选子集（非全量 registry）
        └── meta:    task_type / decisions / cost_hint
```

#### 3.8.2 TaskClassifier（三层决策）

```python
class TaskClassifier:
    """三级递进，先便宜后贵，直到置信度达标"""

    def classify(self, user_message: str, history_tail: list) -> TaskType:
        # 第 1 层：规则（<2ms，命中率约 40%）
        if t := self._rule_match(user_message):
            return t  # e.g., "/" 开头 → command, "昨天/之前/记得吗" → recall

        # 第 2 层：embedding + cosine sim 到 task exemplars（<15ms，命中率约 85%）
        q_emb = self._embedder.embed_cached(user_message)
        t, score = self._nearest_exemplar(q_emb)
        if score > 0.75:
            return t

        # 第 3 层：tiny-LLM 投票（<300ms，兜底；默认关闭，可开启）
        #          用 claude-haiku 或本地 qwen-0.5b
        if self._config.enable_llm_classifier:
            return self._llm_classify(user_message, history_tail)

        return TaskType.CHAT  # 默认兜底
```

**任务类型枚举**（初版 8 类，可扩）：

| task_type | 识别信号 | 组件组合 | 工具子集 |
|---|---|---|---|
| `chat` | 默认 / 问候 / 情绪 | L1 snapshot + persona + recent 3 | memory_write |
| `recall` | "昨天/之前/记得/那个" | L1 + L2 FTS + L3 vec top-10 | memory_search, memory_read |
| `task` | "帮我/待会/提醒" | L1 + todo list + skill prelude | todo_write, todo_complete, delegate |
| `code` | 代码块 / ".py" / 路径 | workspace digest + L3 vec (code similar) | file_read/write/glob/grep |
| `web_search` | "搜一下/查查/新闻" | 时间 + L2 FTS (相关主题) | web_search, web_fetch |
| `plan` | "如何/步骤/怎么做" | L1 + L2 FTS top-5 | todo_write, delegate, ask_user |
| `emotion` | 情绪词 + 标点强度 | L1 + USER.md 放大 + recent 10 | memory_write |
| `command` | 以 `/` 开头 | skill body only | skill_invoke |

#### 3.8.3 ComponentRegistry

组件实现 `Component` 接口，自己声明成本和优先级：

```python
class Component(ABC):
    name: str
    priority: int       # 1-10, 被砍时从低到高
    est_tokens: int     # 估算成本

    @abstractmethod
    def provide(self, ctx: AssemblyContext) -> ComponentOutput:
        """返回 role + content + tokens 或 tool schemas。"""
```

MVP 内置 6 个组件：

| Component | 数据来源 | 组装产物 |
|---|---|---|
| `MemoryComponent` | MemoryManager L1/L2/L3 | `<memory-context>` 块 |
| `ToolComponent` | ToolRegistry + policy 白名单 | `tools: [schemas]` |
| `SkillComponent` | SkillLoader 按任务匹配 | SKILL.md body 作为 prelude user message |
| `PersonaComponent` | USER.md 摘要 + pet mood | 注入 system prompt |
| `TimeComponent` | wall clock + 今日日历 | 短 system note |
| `WorkspaceComponent` | 当前聚焦文件（可选）| 代码片段 + 路径 |

**插件点**：用户可在 `%AppData%\deskpet\components\<name>\` 放新组件（热加载，同 Skill 机制）。

#### 3.8.4 AssemblyPolicy（声明式策略）

策略用 **YAML** 描述（方便用户无代码修改）：

```yaml
# deskpet/agent/policies/default.yaml
policies:
  recall:
    must:   [memory, persona, time]
    prefer: [skill:recall-yesterday]
    tools:  [memory_read, memory_search]
    memory:
      l1: snapshot
      l2: { top_k: 20, mode: fts }
      l3: { top_k: 10, mode: vec }

  code:
    must:   [workspace, memory]
    tools:  [file_read, file_write, file_glob, file_grep]
    memory:
      l1: snapshot
      l2: { top_k: 5, mode: fts, filter: "role=assistant AND content LIKE '%```%'" }
      l3: { top_k: 15, mode: vec }

  chat:
    must:   [memory, persona]
    tools:  [memory_write]
    memory:
      l1: snapshot
      l2: { top_k: 3, mode: recent }
      l3: null   # 闲聊不查向量
```

**策略合成**：`default.yaml` + `user/overrides.yaml` 两层合并，用户可覆盖。

#### 3.8.5 BudgetAllocator

```python
# 伪代码
def allocate(bundle: PartialBundle, budget: int) -> ContextBundle:
    if bundle.total_tokens <= budget:
        return bundle.finalize()

    # 超预算：按 priority 从低到高砍 / 缩
    overrun = bundle.total_tokens - budget
    for comp in sorted(bundle.components, key=lambda c: c.priority):
        if comp.name == "memory":
            comp.shrink_memory_top_k()   # L3 top_k 10→5→3
        elif comp.name == "tool":
            comp.prune_rare_tools()      # 按上周使用频率砍
        elif comp.name in ("skill", "workspace"):
            comp.drop()                  # 直接扔
        overrun = recompute(bundle) - budget
        if overrun <= 0:
            break
    return bundle.finalize()
```

#### 3.8.6 ContextBundle 数据结构

```python
@dataclass
class ContextBundle:
    task_type: TaskType
    # Frozen 部分：组装结果稳定 → 参与 prompt cache
    frozen_system: str              # system_prompt + persona + time
    # Dynamic 部分：每轮变 → 不参与 cache
    memory_block: str | None        # <memory-context>...</memory-context>
    skill_prelude: list[dict]       # [{"role": "user", "content": "..."}]
    tool_schemas: list[dict]
    # Metadata
    decisions: dict                 # 挑了什么、为什么，用于可观测性
    cost_hint: dict                 # 预估 tokens / 真实 tokens 事后填

    def build_messages(self, base_system: str) -> list[dict]:
        msgs = [{"role": "system", "content": base_system + "\n\n" + self.frozen_system}]
        msgs.extend(self.skill_prelude)
        if self.memory_block:
            msgs.append({"role": "system", "content": self.memory_block})
        return msgs
```

#### 3.8.7 Prompt cache 兼容性

**关键：不能因为 Assembler 的引入破坏 prompt cache**：
- `frozen_system` 部分**每轮稳定**（只有策略变更 / 文件记忆刷新时才变）→ 走缓存
- `memory_block` / `skill_prelude` 作为**独立 system messages** 放在 frozen 之后 → 不影响 frozen 部分的 prefix cache
- 关键顺序（Claude prompt caching 规则）：
  ```
  [cacheable system (frozen)]  ← 打 breakpoint
  [cacheable system (persona)] ← 打 breakpoint
  [memory/skill (dynamic)]     ← 不打 breakpoint
  [conversation history]
  [current user message]
  ```

#### 3.8.8 可观测性

每轮 `bundle.decisions` 写进 session log：

```json
{
  "task_type": "recall",
  "classifier_path": "rule",
  "classifier_latency_ms": 1.2,
  "assembly_latency_ms": 38.7,
  "components": {
    "memory": {"l1": true, "l2_top_k": 20, "l3_top_k": 10, "tokens": 1180},
    "tool":   {"tools": ["memory_read", "memory_search"], "tokens": 420},
    "persona": {"tokens": 150}
  },
  "budget_cut": false,
  "total_tokens": 1750
}
```

前端 `MemoryPanel` 下面加一个 "Context Trace" 视图显示这个结构 → 用户能看到桌宠"为什么这样想"。

---

## 4. 数据模型

### 4.1 SQLite schema 完整版

见 §3.3.2，在 Hermes schema v8 基础上增加 `embedding` / `salience` / `decay_last_touch` / `user_emotion` / `audio_file_path` 五列，以及 `messages_vec` 虚拟表。

**迁移方案**：
- 新装：直接建 schema v9（= v8 + 桌宠扩展）
- 升级：从 v8 ALTER TABLE 加列 + 回填 embedding（后台任务）

### 4.2 config.toml 新增段

```toml
[agent]
max_iterations = 50             # Hermes 默认 90，桌宠减到 50
provider = "anthropic"          # anthropic | openai | gemini | local
model = "claude-sonnet-4-5"
fallback_model = "claude-haiku-4-5"  # 预算用尽后兜底
budget_cap_usd = 2.0            # 单次对话最大花费

[memory]
enabled = true
prefetch_top_k = 5
embedding_model = "bge-m3-int8"
salience_boost_on_recall = 0.05
decay_lambda = 0.02             # 每天衰减率
file_memory_max_chars_memory_md = 51200
file_memory_max_chars_user_md = 20480

[context]
engine = "compressor"           # compressor | lcm（预留）
threshold_percent = 0.75
protect_first_n = 3
protect_last_n = 6

[context.assembler]
enabled = true
policy_file = "agent/policies/default.yaml"
user_overrides = "%APPDATA%/deskpet/policies/overrides.yaml"
budget_ratio = 0.6              # ContextBundle 允许占 context_window 的比例
classifier_mode = "rule+embed"  # rule | rule+embed | rule+embed+llm
classifier_exemplars = "agent/policies/exemplars.jsonl"
llm_classifier_model = "claude-haiku-4-5"  # 仅当 mode 含 llm 时
fallback_task_type = "chat"
trace_enabled = true            # 每轮写 decisions 到 log

[tools]
enabled = ["memory", "todo", "file", "web", "delegate", "skill", "mcp"]
disabled = []
web_search_provider = "brave"

[mcp]
servers = [
    { name = "filesystem", command = "npx", args = ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/.../Documents/deskpet-ws"] },
    { name = "brave-search", command = "npx", args = ["-y", "@modelcontextprotocol/server-brave-search"], env = { BRAVE_API_KEY = "..." } },
]

[performance]
embedding_batch_size = 8
embedding_flush_interval_sec = 2
recall_p95_budget_ms = 30
tool_call_p50_budget_ms = 50
```

---

## 5. 性能目标与预算

| 指标 | 目标 | 当前（rc1） | 预算分解 |
|---|---|---|---|
| **首字延迟**（ASR 结束 → 首字 TTS）| **< 800ms p50** | ~2000ms | Assembler 70ms + LLM stream first token 330ms + TTS first chunk 400ms |
| **记忆召回** | **< 30ms p95**（10 万条） | N/A | 向量 20ms + FTS 5ms + 融合 5ms |
| **ContextAssembler** | **< 70ms p95** | N/A | Classifier 20ms + 并行 component.provide() 40ms + Budget alloc 10ms |
| ├─ TaskClassifier | **< 20ms p95** | N/A | rule 2ms / embed 15ms / llm 300ms（可选） |
| ├─ Component 并行组装 | **< 50ms p95** | N/A | MemoryComponent 30ms + ToolComponent 5ms + 其余 <5ms each |
| **工具调用往返** | **< 50ms p50**（本地） | N/A | JSON encode 5ms + dispatch 2ms + handler 30ms + encode back 10ms |
| **Context 压缩** | **< 2000ms**（触发时） | N/A | summarize 用 haiku 模型 |
| **Embedding 批量** | **< 80ms/batch-8** | N/A | BGE-M3 INT8 on RTX 30-series |
| **数据库写入** | **< 5ms p99**（WAL） | N/A | 单 row insert |
| **启动到第一句话** | **< 90s**（P3-G1 复用） | ~90s ✅ | 保持 |
| **记忆库上限** | **10 万条**稳态 / **100 万条**可扩展 | N/A | sqlite-vec HNSW |

**不做的性能权衡**：
- 不做 CPU-only 路径（BGE-M3 on CPU 慢 5×）—— 桌宠定位 NVIDIA GPU
- 不做"穷鬼模式"（跳过 embedding / 跳过 memory extract）—— 本次重构不吝啬 token

---

## 6. 实施拆分（Slice）

本改动属于 **架构级、跨模块** —— 按全局规范走 **OpenSpec propose → explore → apply → archive**。

拆分如下（11 个 slice，按依赖顺序）：

| Slice | 名称 | 依赖 | Est. | 验收 |
|---|---|---|---|---|
| **P4-S1** | SQLite schema + SessionDB（lift Hermes） | — | 1d | 单元测试：CRUD + FTS5 search |
| **P4-S2** | Embedding service（BGE-M3 INT8 + CUDA batch） | S1 | 1d | 性能测试：batch-8 < 80ms |
| **P4-S3** | 向量索引（sqlite-vec） + 混合召回 | S1, S2 | 1d | 10 万条压测：p95 < 30ms |
| **P4-S4** | Memory manager + MEMORY.md/USER.md（lift） | S1 | 1d | E2E：write → 下次 session 读到 |
| **P4-S5** | Tool registry + 10 个内置 tool（lift） | S1 | 1d | 单测 + LLM 调用真实 tool 串起来 |
| **P4-S6** | Agent loop（lift Hermes `run_conversation`，**不含** Assembler，先用全量 registry 跑通） | S1, S4, S5 | 2d | E2E：一句 → tool → 回复 |
| **P4-S7** | **ContextAssembler v1**（TaskClassifier rule+embed + ComponentRegistry + 6 内置组件 + BudgetAllocator + YAML policy） | S3, S4, S5, S6 | **2d** | 8 类任务分类准确率 > 85%；组装 latency p95 < 70ms；bundle trace 落 log |
| **P4-S8** | Context compressor（lift Hermes） | S6 | 1d | 长对话自动压缩不丢关键信息 |
| **P4-S9** | MCP client（port from TS） | S5 | 2d | 接入 filesystem + brave-search |
| **P4-S10** | Skill system（lift + 热加载）+ Assembler SkillComponent 接线 | S5, S7 | 1d | 加一个 skill → `/skill-name` 生效，且 Assembler 在匹配任务时自动挂载 |
| **P4-S11** | 前端 MemoryPanel + **Context Trace 视图** + `/agent` 模式入口 | S4-S10 | 2d | UI E2E + 截图；Trace 视图能看到每轮 task_type / components / tokens |
| **P4-S12** | 冷启动性能回归 + 整体 benchmark + Assembler 策略压测 | 全部 | 1d | P3-G1 保持 ≤90s，首字延迟 <800ms p50，Assembler <70ms p95 |

**总计 16 人日**，预计 4 周日历时间（考虑调试 + 真机测试 + OpenSpec 流程）。

**里程碑**：
- **M1**（S1-S6 完成）= Vanilla MVP，agent 能跑通一句话 → 全量工具 → 带记忆回答（无智能组装）
- **M2**（S7-S10 完成）= **Smart** MVP，ContextAssembler 接管 + 压缩 + MCP + Skill 全部就绪
- **M3**（S11-S12 完成）= Ship-ready，UI（含 Context Trace）+ 性能验收过

---

## 7. 代码布局（提议）

```
backend/
├── deskpet/
│   ├── agent/                              (NEW —— Phase 4 主目录)
│   │   ├── __init__.py
│   │   ├── loop.py                         ← lift Hermes run_conversation
│   │   ├── budget.py                       ← lift IterationBudget
│   │   ├── interrupt.py                    ← lift Hermes interrupt
│   │   ├── context_engine.py               ← lift
│   │   ├── context_compressor.py           ← lift
│   │   ├── prompt_caching.py               ← lift
│   │   ├── error_classifier.py             ← lift
│   │   ├── retry_utils.py                  ← lift
│   │   ├── assembler/                      (NEW —— 智能上下文组装器，§3.8)
│   │   │   ├── __init__.py
│   │   │   ├── assembler.py                ← ContextAssembler 主类
│   │   │   ├── classifier.py               ← TaskClassifier (rule/embed/llm 三层)
│   │   │   ├── bundle.py                   ← ContextBundle dataclass
│   │   │   ├── budget_allocator.py         ← BudgetAllocator
│   │   │   ├── components/                 ← 内置 6 组件
│   │   │   │   ├── base.py                 ← Component ABC
│   │   │   │   ├── memory_component.py
│   │   │   │   ├── tool_component.py
│   │   │   │   ├── skill_component.py
│   │   │   │   ├── persona_component.py
│   │   │   │   ├── time_component.py
│   │   │   │   └── workspace_component.py
│   │   │   ├── policies/                   ← 声明式策略 YAML
│   │   │   │   ├── default.yaml
│   │   │   │   └── exemplars.jsonl         ← classifier embedding 样本
│   │   │   └── trace.py                    ← decisions 落 log
│   │   └── providers/                      ← 多 provider 适配
│   │       ├── anthropic_adapter.py        ← lift
│   │       ├── openai_adapter.py
│   │       └── local_llama_adapter.py
│   │
│   ├── memory/                             (NEW)
│   │   ├── __init__.py
│   │   ├── manager.py                      ← lift Hermes MemoryManager
│   │   ├── provider.py                     ← lift abstract base
│   │   ├── file_memory.py                  ← lift memory_tool.py (MEMORY.md + USER.md)
│   │   ├── session_db.py                   ← lift hermes_state.py (SQLite + FTS5)
│   │   ├── vector_store.py                 ← NEW (sqlite-vec)
│   │   ├── embedder.py                     ← NEW (BGE-M3 client)
│   │   └── recall.py                       ← NEW (hybrid retrieval)
│   │
│   ├── tools/                              (NEW —— 与 Hermes 结构一致)
│   │   ├── __init__.py
│   │   ├── registry.py                     ← lift
│   │   ├── memory_tool.py                  ← lift
│   │   ├── todo_tool.py                    ← lift + Claude-Code-Best pattern
│   │   ├── file_tools.py                   ← lift
│   │   ├── web_search_tool.py              ← Claude-Code-Best pattern
│   │   ├── web_fetch_tool.py               ← Claude-Code-Best pattern
│   │   ├── delegate_tool.py                ← lift
│   │   ├── skill_invoke_tool.py            ← NEW
│   │   ├── mcp_call_tool.py                ← NEW
│   │   └── ask_user_tool.py                ← Claude-Code-Best AskUserQuestion pattern
│   │
│   ├── skills/                             (NEW)
│   │   ├── loader.py                       ← lift Hermes skill_commands
│   │   └── builtin/                        ← 随仓库 ship 的 skill
│   │       ├── recall-yesterday/
│   │       └── summarize-day/
│   │
│   ├── mcp/                                (NEW)
│   │   ├── client.py                       ← port Claude-Code-Best mcp-client
│   │   ├── connection_pool.py
│   │   └── servers_config.py
│   │
│   └── main.py                             (MODIFIED —— 接入 DeskPetAgent)
│
└── tests/
    ├── test_agent_loop.py
    ├── test_memory_recall.py
    ├── test_tool_registry.py
    └── ...
```

---

## 8. 风险与缓解

### R1. sqlite-vec 在 Windows 上的 ABI 兼容
- **风险**：需要编译原生扩展，与 PyInstaller 打包冲突
- **缓解**：用官方 prebuilt wheel（`sqlite-vec` 在 PyPI 有 win_amd64），PyInstaller hook 显式包含 `.dll`
- **Fallback**：退化到 FAISS on-disk（性能降但可用）

### R2. BGE-M3 首次加载慢
- **风险**：模型冷加载 8-15s，冲击 P3-G1 启动门
- **缓解**：
  - 模型打进 bundle（已有 `%LocalAppData%\deskpet\models\`）
  - 启动时 **预热**（async task 启动）
  - 首轮对话前若未 ready，fallback 纯 FTS5 召回

### R3. 每轮 prefetch 增加延迟
- **风险**：记忆召回 30ms + context 注入 20ms + 额外 input tokens
- **缓解**：
  - **queue_prefetch_all** 在上一轮结束时就预估下一轮问题类型预取（Hermes pattern）
  - salience filtering：只注入 top-K，不全量塞
  - input tokens 不吝啬 —— PRD 明确"性能优先不管 token"

### R4. Claude Code Best 无 license，架构 pattern 侵权风险
- **风险**：直接 copy 源码有法律风险
- **缓解**：
  - 仅**学习 pattern**，**clean-room rewrite**
  - MCP 用官方 `mcp` Python SDK，不抄 TS 代码
  - 如需具体实现细节，走 issue 联系作者 / 观察 NPM 包 dist 代码（已公开）

### R5. Prompt cache 失效（内存写入导致 system prompt 变）
- **风险**：每次记忆写入都改 system prompt → 整个 session cache miss
- **缓解**：**Frozen snapshot pattern**（Hermes 原设计）—— mid-session 写入不动 system prompt，下次会话开始才刷新

### R6. 工具爆炸导致 schema 过大
- **风险**：60+ tools schema 塞 prompt 占 5k+ tokens
- **缓解**：
  - 参考 Claude-Code-Best 的 **ToolSearch** 模式 —— 延迟加载，只暴露核心 10 个，其余按需 search
  - 按 toolset 分组，用户配置启用哪些

### R7. ASR 打断导致对话混乱
- **风险**：用户说到一半桌宠已经开始回复
- **缓解**：
  - ASR 用 **VAD 静音检测**，持续静音 600ms 才 finalize
  - 桌宠说话时，ASR 热词 "等一下 / 停 / 别说了" 立即打断
  - 打断信号从前端通过 IPC 触发 agent loop 的 `_interrupt_requested`

### R8. Hermes 的 AIAgent class 体积巨大（~12k LOC）
- **风险**：直接 lift 会带来 60 个构造参数，维护难度大
- **缓解**：
  - **瘦身版**：只 lift 会话循环 + budget + interrupt，去掉 gateway / platform / OAuth 等
  - 构造参数收窄到 ~15 个
  - 保留接口兼容，方便未来再吸收 Hermes 更新

### R9. ContextAssembler 分类错误导致错配组件
- **风险**：rule 层过激进 / embed 阈值过低 → task_type 错判 → 该查记忆时没查、该给工具时没给
- **缓解**：
  - **三层递进 + 置信度阈值**：rule 只打明确信号；embed 阈值 >0.75 才 commit；否则降级到 `chat` 默认策略
  - **Fallback**：分类置信度低时，策略默认"全量"（memory + 通用工具集），宁多不少
  - **可观测性**：每轮 `classifier_path` 写 log，用户可在 MemoryPanel 里看到并手动覆盖
  - **在线学习**：`feedback()` 记录 "分类 vs. 实际用了什么工具"，每周离线重算 exemplars 集

### R10. Assembler 引入的 per-turn latency 抵消收益
- **风险**：每轮多花 70ms 组装，用户端首字延迟显性变慢
- **缓解**：
  - 严格守 70ms p95 预算；超了必须在 P4-S12 回归里卡掉
  - Classifier 第 3 层 LLM 默认关闭（300ms 级别太贵）
  - Components 并行 fan-out（`asyncio.gather`），不是串行
  - 允许整体 **disable**（config flag `[context.assembler].enabled=false`）退化到全量工具 + 全量 prefetch，用于紧急回滚

### R11. Prompt cache 因组件变动而失效
- **风险**：Assembler 每轮产出不同顺序 / 不同内容 → frozen 部分不稳定 → cache miss
- **缓解**：
  - **Frozen 部分只含稳定组件**（persona、time 粗粒度、system prompt）；动态组件（memory、skill）放在 frozen **之后**的独立 system message
  - 打 prompt cache breakpoint 在 frozen 尾部（Claude 的 `cache_control`）
  - 回归测试：连续 5 轮 chat 任务，观察 `cache_read_tokens / input_tokens` 比率 > 80%

---

## 9. 开放问题（需你决策）

1. **LLM provider 选型**：默认用 Anthropic Claude（PRD 假设），还是允许多 provider 启动时选？
   - 建议：默认 Anthropic，但启动时 `/login` 可切换（Claude-Code-Best 模式）
2. **本地 LLM 支持**：是否要打进 bundle？
   - 本 PRD 建议：**Phase 4 不做**，留 provider 接口但默认云端。`llama-cpp-python` 打包复杂，推到 Phase 5
3. **Skill 热加载**：文件监听自动重载 vs 重启生效？
   - 建议：**watchdog 监听 + debounce 1s 自动重载**
4. **隐私默认值**：记忆库是否默认加密？
   - 建议：**不加密**（性能优先），但 `uninstall_user_data` 时彻底清空；如用户要加密，提供 SQLCipher 选项（Phase 5）
5. **Web 搜索默认 provider**：Brave / Bing / Google
   - 建议：**Brave**（有官方 MCP + API 便宜）
6. **子 agent 隔离**：delegate 工具 spawn 的子 agent 是否共享主 agent 记忆？
   - 建议：**只读共享**（子 agent 能检索但不能写），主 agent 负责回写
7. **Embedding 不可用时的降级**：首次启动 BGE-M3 没下载好
   - 建议：纯 FTS5 记忆 + "记忆模型加载中..." 状态提示，BGE-M3 ready 后异步回填
8. **TaskClassifier 第 3 层 LLM 默认开关**：
   - 建议：**默认关**（省 300ms），仅在 embed 置信度 < 0.5 且 3 秒内无多轮上下文时才调用；用户可在 settings 开启"精准模式"
9. **AssemblyPolicy 冲突时的仲裁**：用户 overrides.yaml 和 default.yaml 规则冲突时
   - 建议：用户覆盖 > 默认；必须字段（`must`）允许追加但不允许删除核心 memory 组件（防止用户误关记忆）
10. **Assembler feedback 是否真的做在线学习**：
    - 建议：**v1 只记录不学习**（P4-S7 落盘 decisions + 实际工具使用），离线每周跑一次 exemplars 聚类；真正的 online adaptive policy 放 Phase 5

---

## 10. 验收标准（面向 slice handoff）

每个 slice **同时**满足：

- [ ] 相关单元测试全过
- [ ] `pytest tests/` p0 + `pytest tests/integration/` p0 同时过
- [ ] 新增性能指标打点进入 `scripts/perf/` benchmark 脚本
- [ ] **UI-level E2E**（Preview MCP + Playwright）：至少 1 个截图证据（遵守你 MEMORY.md 的 `feedback_real_test.md`）
- [ ] HANDOFF 文档更新
- [ ] 更新 `docs/CODEMAPS/` 相关图

**整体 P4 验收**（M3）：
- [ ] 端到端 smoke：一句语音 → 桌宠用 web_search 查 → 记进 MEMORY.md → 下次会话召回
- [ ] 性能回归：对比 rc1 首字延迟不劣化
- [ ] 10 万条合成记忆压测：recall p95 < 30ms
- [ ] 至少 3 个用户自定义 skill 工作
- [ ] 至少 2 个 MCP server 联通（filesystem + brave-search）
- [ ] **ContextAssembler 分类准确率** ≥ 85%（在 100 条人工标注测试集上）
- [ ] **ContextAssembler latency p95 < 70ms**（benchmark `scripts/perf/assembler_bench.py`）
- [ ] **Prompt cache 命中率** ≥ 80%（连续 5 轮 chat，`cache_read_tokens/input_tokens`）
- [ ] **Context Trace UI** 能展示每轮 task_type / 挑选组件 / tokens 分布

---

## 11. 里程碑交付物

| 交付物 | 对应 Slice | 时间 |
|---|---|---|
| OpenSpec proposal 通过 | — | Week 0 |
| M1 demo：MVP 跑通 | S1-S6 | Week 2 end |
| M2 demo：Full feature | S7-S9 | Week 3 end |
| M3 release：`v0.6.0-phase4-rc1` | S10-S11 | Week 4 end |

---

## 12. 附录：拿来源码头部模板

所有从 Hermes lift 的文件头部统一加：

```python
"""
<original filename>
Adapted from NousResearch/hermes-agent (MIT License)
https://github.com/NousResearch/hermes-agent/blob/<commit>/<path>
Modifications for DeskPet: <brief description>
Copyright (c) 2025 Nous Research
Copyright (c) 2026 DeskPet Contributors
"""
```

`LICENSES/` 目录下保留 Hermes 的 `LICENSE.md` 原文。

---

## 13. 附录：关键架构决策记录（ADR 简表）

| ID | 决策 | 替代方案 | 理由 |
|---|---|---|---|
| ADR-1 | 同 repo 扩展 SQLite 而非 LanceDB | LanceDB | 嵌入式、无额外进程、和会话表同库事务 |
| ADR-2 | BGE-M3 而非 OpenAI embedding | OpenAI | 本地、无 API 费、中文强、复用 GPU |
| ADR-3 | Python 继续做 backend，不换 Rust | Rust 全量重写 | Hermes 同栈，lift 成本低；Rust 收益小 |
| ADR-4 | MCP 用官方 Python SDK，不 port TS | TS sidecar 进程 | 避免多进程、类型一致、官方维护 |
| ADR-5 | Memory 采用 "frozen snapshot" 模式 | 每次写都更新 system prompt | 保 prompt cache |
| ADR-6 | 子 agent 只读共享主 agent 记忆 | 完全隔离 | 避免重复召回、主 agent 统一回写 |
| ADR-7 | 无消息网关（Telegram/Slack 等） | 完整 Hermes gateway | 桌宠是 single-user 本机产品，不 scope |
| ADR-8 | **引入 ContextAssembler 做事前组装** | 只靠 ContextEngine 事后压缩 | 事后压缩是"炸了再救"，事前组装是"一开始就只带需要的"；在 token 预算已经很大（§1.2 性能优先）的前提下，质量和可解释性收益 > 70ms 延迟成本 |
| ADR-9 | **策略用 YAML 声明式，不写死在代码里** | Python 规则树 | 用户可无代码扩展；AssemblyPolicy 的改动不触发 re-deploy；与 Skill 系统同源设计 |
| ADR-10 | **TaskClassifier 默认 rule+embed，不用 LLM** | 全用 LLM 分类 | LLM 分类 300ms 抵消整个 Assembler 收益；rule+embed 已能做到 >85% 准确率；LLM 只作为"精准模式"兜底 |

---

**审阅人**: @owner
**下一步**: 你 review 后签字 → 走 `openspec propose` → 进入 explore 阶段写 design.md + tasks.md
