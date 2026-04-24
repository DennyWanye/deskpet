# Proposal: P4 Poseidon — Agent Harness + Long-term Memory

**Target Version**: `v0.6.0-phase4`
**Codename**: Poseidon
**Source PRD**: `docs/P4-agent-harness-prd.md`（10/10 决策已签字）
**Created**: 2026-04-24

## Why

当前桌宠 (`v0.5.0-phase3-rc1`) 是单向 pipeline（ASR → LLM → TTS），**无状态、无工具、无记忆**。每次开机从零开始，主人讲过的事一概忘记；只能闲聊，不能查文件、搜网、做多轮规划；全量 prompt 塞入 LLM，浪费 token 且稀释注意力。

本次改动把桌宠从"会说话的 UI"升级成**有记忆、有工具、有规划能力的自主 desktop agent**。通过 60% 拿 Hermes (MIT) 代码 + 20% Claude-Code-Best pattern 重写 + 20% 自研（ContextAssembler / BGE-M3 向量层 / 零成本 web_crawl），用 ~16 人日交付质的飞跃。

## What Changes

- **NEW**: DeskPetAgent ReAct 主循环（lift from Hermes `AIAgent.run_conversation`），支持工具调用、迭代预算、中断、prompt caching
- **NEW**: 三层记忆系统
  - L1 文件记忆（MEMORY.md + USER.md）— lift Hermes
  - L2 会话数据库（SQLite + FTS5 schema v8）— lift Hermes
  - L3 向量记忆（sqlite-vec + BGE-M3 INT8，本地部署 ~286MB）— 自研
  - 混合检索（RRF fusion：向量 + BM25 + recency + salience）
- **NEW**: ContextAssembler 智能上下文组装器（原创）
  - TaskClassifier：rule → embed → LLM 三层级联（D8 默认开 LLM）
  - ComponentRegistry：6 内置组件（memory/tool/skill/persona/time/workspace）
  - 声明式 YAML AssemblyPolicy + BudgetAllocator
  - ContextBundle 输出给 agent loop
- **NEW**: Tool framework（lift Hermes `tools/registry.py`）+ 16 内置工具（memory×3、todo×2、file×4、web×4、delegate、skill_invoke、mcp_call）
- **NEW**: 零成本 web 工具集（D5 决策）— `web_fetch` + `web_crawl` + `web_extract_article` + `web_read_sitemap`，依赖 `httpx + trafilatura + selectolax`，**不接任何付费搜索 API**
- **NEW**: Skill 系统（lift Hermes + agentskills.io 兼容）— markdown + YAML frontmatter，watchdog debounce 热加载（D3）
- **NEW**: MCP client（port Claude-Code-Best pattern to Python `mcp` SDK）— 支持 filesystem、weather 等 stdio server
- **NEW**: Multi-provider LLM 适配（anthropic / openai / gemini，D1）— **不含** local（D2）
- **NEW**: 前端 MemoryPanel + Context Trace 可观测视图
- **BREAKING**: 旧的 `/chat` HTTP endpoint 语义改变 — 不再是无状态单轮调用，而是进入 agent loop（但对前端保持请求格式兼容，只是响应内容多了 `tool_calls` / `memory_context` 字段）
- **BREAKING**: 配置文件 `config.toml` 新增 `[agent]` / `[memory]` / `[context.assembler]` / `[tools.web]` / `[mcp]` 段；旧版 config 需迁移
- **BREAKING**: 数据库 schema v8 → v9（新增 5 列：`embedding BLOB / salience REAL / decay_last_touch REAL / user_emotion TEXT / audio_file_path TEXT` + `messages_vec` 虚拟表）

## Capabilities

### New Capabilities

- `agent-loop`: DeskPetAgent ReAct 主循环、IterationBudget、中断机制、ContextEngine 事后压缩、多 LLM provider 适配
- `memory-system`: 三层记忆架构（L1 文件 / L2 SessionDB+FTS5 / L3 sqlite-vec+BGE-M3）、混合检索、记忆衰减与 salience、frozen snapshot 模式
- `context-assembler`: TaskClassifier（rule+embed+LLM 级联）、ComponentRegistry、AssemblyPolicy（YAML）、BudgetAllocator、ContextBundle 数据结构、decisions trace
- `tool-framework`: ToolRegistry 自动发现、OpenAI-format schemas、dispatch + 错误处理 + retry、ToolSearch 延迟加载（CCB pattern）
- `web-tools`: 零成本爬虫工具集（fetch/crawl/extract_article/sitemap）、robots.txt 尊重、rate limiting、preferred_sources 白名单
- `skill-system`: SKILL.md 加载器、YAML frontmatter、watchdog 热加载、agentskills.io 兼容
- `mcp-integration`: MCP client（stdio/SSE/HTTP stream）、连接池、崩溃重连、动态 tool dispatch
- `llm-providers`: Anthropic / OpenAI / Gemini 三 provider 适配、prompt caching breakpoint、fallback model、budget cap

### Modified Capabilities

(项目首个 OpenSpec change，无现存 spec 被修改。)

## Impact

**代码**（新增约 15,000 行，lift 约 9,000 行）：
- `backend/deskpet/agent/` — 主循环 + 各 adapter（新目录）
- `backend/deskpet/agent/assembler/` — ContextAssembler（新目录）
- `backend/deskpet/memory/` — 三层记忆（新目录）
- `backend/deskpet/tools/` — Tool registry + 16 工具（新目录）
- `backend/deskpet/skills/` — Skill loader + built-in skills（新目录）
- `backend/deskpet/mcp/` — MCP client（新目录）
- `backend/deskpet/main.py` — 接入 DeskPetAgent（修改）
- `tauri-app/src/MemoryPanel.tsx` + `ContextTracePanel.tsx` — 前端新组件

**数据与配置**：
- `state.db` schema v8 → v9（自动迁移，backfill embedding 后台任务）
- `config.toml` 新增 5 段（`[agent]` / `[memory]` / `[context.assembler]` / `[tools.web]` / `[mcp]`）
- `%LocalAppData%\deskpet\models\bge-m3-int8\` 新增（~286MB，打进 bundle）

**依赖**：
- Python：`sqlite-vec>=0.1`、`FlagEmbedding>=1.2`、`httpx>=0.25`、`trafilatura>=1.6`、`selectolax>=0.3`、`mcp>=1.0`、`watchdog>=3.0`
- Bundle 体积：rc1 1.5GB → 预估 1.8GB（BGE-M3 +286MB + 其它 +50MB）

**文档**：
- 新增 `openspec/specs/<各 capability>/spec.md` 8 份
- 更新 `docs/CODEMAPS/` 对应图
- 新增 `docs/P4-agent-harness-prd.md`（已 commit）

**性能目标**（与 P3 差异）：
- 首字延迟 p50：800ms → **1100ms**（D8 LLM classifier 引入的 +300ms，由 TTS 预播把感知延迟压到 <500ms）
- 冷启动：维持 P3-G1 ≤90s（BGE-M3 预热异步进行）
- 记忆召回：新目标 **<30ms p95**（10 万条规模）
- ContextAssembler：新目标 **<370ms p95**

**非目标**（scope out）：
- 消息网关（Telegram/Slack/微信等）
- Remote control / 手机端
- ACP 协议（IDE 集成）
- Cron scheduler（推 Phase 5）
- Multi-tenant
- Local LLM 打进 bundle（D2 明确排除）
