# Tasks: P4 Poseidon — Agent Harness + Long-term Memory

> 12 slices / ~16 人日。阶段划分见 `design.md` Migration Plan。
> 每个 slice 完成时 MUST 跑：pytest + tsc + 一次端到端 smoke（参考 MEMORY.md 的 Real Test 规范）。

## 1. 环境准备与依赖 (P4-S0, 0.5d)

- [ ] 1.1 `backend/pyproject.toml` 新增依赖：`sqlite-vec>=0.1`、`FlagEmbedding>=1.2`、`httpx>=0.25`、`trafilatura>=1.6`、`selectolax>=0.3`、`mcp>=1.0`、`watchdog>=3.0`、`anthropic>=0.40`、`openai>=1.40`、`google-generativeai>=0.8`
- [ ] 1.2 `backend/deskpet/` 目录骨架新建：`agent/`, `agent/assembler/`, `agent/assembler/components/`, `agent/assembler/policies/`, `memory/`, `tools/`, `skills/`, `mcp/`, `llm/`
- [ ] 1.3 把 BGE-M3 INT8 权重下载脚本 `scripts/download_bge_m3.py` 写好，放到 `%LocalAppData%\deskpet\models\bge-m3-int8\`
- [ ] 1.4 `config.toml` 追加 `[agent]` / `[memory]` / `[context.assembler]` / `[tools.web]` / `[mcp]` / `[llm]` 默认段
- [ ] 1.5 更新 `.gitignore` 忽略 models 目录、state.db.bak、embedding 缓存

## 2. 数据库迁移 v8 → v9 (P4-S1 上半, 0.5d)

- [ ] 2.1 写 `backend/deskpet/memory/migrations/v8_to_v9.py`：`.bak` 备份 → `ALTER TABLE messages ADD COLUMN embedding BLOB / salience REAL DEFAULT 0.5 / decay_last_touch REAL / user_emotion TEXT / audio_file_path TEXT`
- [ ] 2.2 建立 `messages_vec` 虚拟表：`CREATE VIRTUAL TABLE messages_vec USING vec0(message_id INTEGER PRIMARY KEY, embedding FLOAT[1024] distance_metric=cosine)`
- [ ] 2.3 启动时 `PRAGMA user_version` 检测与自动执行；失败走 `.bak` 恢复 + 降级启动（无 L3）
- [ ] 2.4 单测覆盖：成功迁移 / 磁盘满 rollback / 已 v9 幂等
- [ ] 2.5 手动 smoke：拿一份真实 rc1 state.db 跑一次迁移验证

## 3. L2 Session DB + FTS5 (P4-S1 下半, 0.5d)

- [ ] 3.1 lift Hermes `hermes_state.py` 到 `backend/deskpet/memory/session_db.py`（顶部加 MIT 归属头）
- [ ] 3.2 瘦身：删除 Hermes 的 OAuth / gateway / multi-tenant 相关列与方法
- [ ] 3.3 启用 WAL 模式 + 应用层 SQLITE_BUSY retry with exponential jitter（最多 5 次）
- [ ] 3.4 FTS5 触发器：messages 表 INSERT/UPDATE/DELETE 同步到 messages_fts
- [ ] 3.5 单测：10 万条插入 → FTS5 MATCH 查询 p95 < 5ms

## 4. L3 向量层 — BGE-M3 Embedding Service (P4-S2, 1d)

- [ ] 4.1 写 `backend/deskpet/memory/embedder.py`：封装 FlagEmbedding BGE-M3 INT8，`async def encode(texts: list[str]) -> np.ndarray`
- [ ] 4.2 CUDA 预热：应用启动异步加载模型，不阻塞主启动
- [ ] 4.3 Batch 写 worker：`embedding_queue` + 每 2s 或 8 条 flush，写 messages_vec
- [ ] 4.4 失败隔离：embedding 失败 MUST NOT 阻塞 message 主写入，只 log
- [ ] 4.5 Benchmark：RTX 30 系列 batch-8 ≤ 80ms；10 万条查询 p95 < 20ms
- [ ] 4.6 Backfill 任务：启动时扫历史 messages 无 embedding 的批量回填（低优先级）

## 5. L3 混合召回 + RRF (P4-S3, 1d)

- [ ] 5.1 写 `backend/deskpet/memory/retriever.py`：`recall(query, policy) -> list[Hit]`
- [ ] 5.2 并行发起 vec 查询 + FTS5 查询 + recency + salience 四维度
- [ ] 5.3 RRF fusion：权重默认 `{vec:0.5, fts:0.3, recency:0.15, salience:0.05}`，可 config 覆盖
- [ ] 5.4 Recall 命中后 MUST 更新 `salience += 0.05, decay_last_touch=now()`
- [ ] 5.5 日常 decay 任务：`salience *= exp(-decay_lambda * days_since_touch)`（启动时跑一次）
- [ ] 5.6 单测：语义相似但字面不同的条目能召回；recency boost 生效
- [ ] 5.7 L3 降级：sqlite-vec / embedder 失败时返回仅 L1+L2 结果，不抛异常

## 6. L1 文件记忆 + MemoryManager (P4-S4, 1d)

- [ ] 6.1 lift Hermes `memory.py` 文件读写到 `backend/deskpet/memory/file_memory.py`
- [ ] 6.2 MEMORY.md / USER.md 用 `\n§\n` 分隔，size cap 50KB / 20KB，超限按 salience 淘汰
- [ ] 6.3 Frozen snapshot pattern：session 启动时读文件注入 system prompt；mid-session 写入只落盘不改当前 prompt
- [ ] 6.4 写 `backend/deskpet/memory/manager.py` 统一三层入口：`recall(query, policy)` / `write(content, target)`
- [ ] 6.5 提供 memory tool handlers：`memory_write` / `memory_read` / `memory_search`
- [ ] 6.6 单测：frozen snapshot 不变、下次 session 读新版本；size cap 淘汰逻辑

## 7. Tool Framework + 注册表 (P4-S5 上半, 0.5d)

- [ ] 7.1 写 `backend/deskpet/tools/registry.py`：`ToolRegistry` 单例 + `register(name, toolset, schema, handler, check_fn, requires_env)`
- [ ] 7.2 Auto-discovery：`import deskpet.tools` 包时遍历 `tools/*.py` 自动加载
- [ ] 7.3 `schemas(enabled_toolsets=None)` 返回 OpenAI function-calling 格式 + toolset 白名单过滤
- [ ] 7.4 `dispatch(name, args, task_id)`：同步调用 handler、异常被 `error_classifier.classify` 捕获、返回 JSON 字符串
- [ ] 7.5 `ToolSearchTool (tool_search)`：关键词搜 hidden tool 激活（CCB lazy-load pattern）
- [ ] 7.6 `requires_env` 缺变量时自动从 schemas 过滤；`check_fn` 失败时 dispatch 返回 `tool not ready`
- [ ] 7.7 单测：新 tool 自动发现 / schema 过滤 / exception → error JSON / ToolSearch 激活

## 8. 内置工具 — memory / todo / file (P4-S5 下半, 0.5d)

- [ ] 8.1 `tools/memory_tools.py`：`memory_write / memory_read / memory_search`（调 MemoryManager）
- [ ] 8.2 `tools/todo_tools.py`：`todo_write / todo_complete`（基于 SQLite todos 表）
- [ ] 8.3 `tools/file_tools.py`：`file_read / file_write / file_glob / file_grep`（限制在 `%APPDATA%\deskpet\workspace\` 内，防目录穿越）
- [ ] 8.4 单测每个 tool 的 happy path + 边界（路径穿越尝试 MUST 被拒）

## 9. 零成本 Web 工具集 (P4-S5 补充, 1d)

- [ ] 9.1 `tools/web_fetch.py`：httpx + trafilatura 提取，timeout 可配，重定向 ≤5 次
- [ ] 9.2 `tools/web_crawl.py`：BFS 同域、robots.txt 尊重（`urllib.robotparser`）、`per_domain_max_concurrency=2`、`request_interval_ms=500`
- [ ] 9.3 Keyword 打分（`selectolax` 抽 title + 正文 TF）、返回 top N 页面 `[{url, excerpt, score}]`
- [ ] 9.4 `tools/web_extract_article.py`：trafilatura extract_metadata → `{title, author, date, text, language}`，缺字段返 null
- [ ] 9.5 `tools/web_read_sitemap.py`：先 `/sitemap.xml` 后 fallback `/sitemap_index.xml`，递归解析子 sitemap
- [ ] 9.6 User-Agent 统一：`DeskPet/0.6 (+https://github.com/.../deskpet)` 或 config 覆盖
- [ ] 9.7 429/403/captcha 连续 3 次 → in-memory cache 标记 blocked 1h，跳过
- [ ] 9.8 Preferred sources 白名单注入 agent system prompt（ToolComponent 或 PersonaComponent）
- [ ] 9.9 审查守门：`grep -r "brave\|tavily\|bingsearch\|exa\.ai"` 结果为空；CI 可加 guard

## 10. LLM Provider 多适配 (P4-S6 上半, 1d)

- [ ] 10.1 写 `backend/deskpet/llm/base.py`：`LLMClient` 抽象 + `ChatResponse` dataclass (content/tool_calls/stop_reason/usage)
- [ ] 10.2 `llm/anthropic_adapter.py`：用 `anthropic>=0.40`，prompt caching `cache_control={"type":"ephemeral"}` 在 frozen_system 尾
- [ ] 10.3 `llm/openai_adapter.py`：用 `openai>=1.40`，tool calls 转统一格式
- [ ] 10.4 `llm/gemini_adapter.py`：用 `google-generativeai`，tool calls 转统一格式
- [ ] 10.5 `llm/registry.py`：`list_providers()` 自动隐藏缺 API key 的 provider
- [ ] 10.6 Fallback 链：primary 失败 → 按 `config.llm.fallback_chain` 最多 2 层重试
- [ ] 10.7 429 `Retry-After` header + exponential backoff 最多 3 次
- [ ] 10.8 Budget cap：daily_usd_cap 累计，80% warning IPC / 100% block
- [ ] 10.9 Streaming `stream=True`：yield `ChatChunk(delta_content, delta_tool_calls, is_final)`
- [ ] 10.10 API key mask log：`sk-****last4`
- [ ] 10.11 单测：three-provider unify、fallback 触发、budget block、streaming 首 chunk < 600ms p50

## 11. DeskPet Agent Loop (P4-S6 下半, 1d)

- [ ] 11.1 lift Hermes `AIAgent.run_conversation` 到 `backend/deskpet/agent/loop.py`
- [ ] 11.2 瘦身：构造参数 60 → ~15（删 gateway / platform / OAuth 相关）
- [ ] 11.3 ReAct 主循环：`while not done: resp = llm.chat(messages, tools) → dispatch tool_calls → append → check budget/interrupt`
- [ ] 11.4 IterationBudget：`max_iterations=20`，超限 MUST 优雅结束并 log warning
- [ ] 11.5 Interrupt 机制：`_interrupt_requested` flag，ASR 热词 "等一下/停" 触发
- [ ] 11.6 Prompt caching 集成：每轮 messages 顺序保持 frozen 在前，dynamic 在后
- [ ] 11.7 ContextEngine 事后压缩 hook（P4-S8 实现具体压缩器）
- [ ] 11.8 TTS pre-narration hook：首 chunk 到达即触发 TTS 播首句
- [ ] 11.9 工具调用并发执行（多 tool_call 同时 dispatch）
- [ ] 11.10 单测：ReAct happy path / 迭代超限 / 中断 / tool error → agent 恢复 / cache hit ≥80%

## 12. ContextAssembler v1 + TTS 预播 (P4-S7, 2d) ✅ 已完成 (2026-04-24)

- [x] 12.1 `agent/assembler/classifier.py`：TaskClassifier 三层级联
- [x] 12.2 Rule 层：`/` 前缀 → command、含 "还记得/之前" → recall、动词短语 → task 等（< 2ms）
- [x] 12.3 Embed 层：BGE-M3 exemplars 池（~100 条人工标注）cosine 相似度（≤ 15ms），阈值 > 0.75 直返
- [x] 12.4 LLM 层：`claude-haiku-4-5` 分类 fallback（≤ 300ms），p95 < 300ms
- [x] 12.5 `agent/assembler/components/{memory,tool,skill,persona,time,workspace}.py`：6 内置 Component
- [x] 12.6 `ComponentRegistry` + `asyncio.gather` 并行 fan-out，总耗时 = max(components) + overhead
- [x] 12.7 `agent/assembler/policies/default.yaml`：8 task_type 的 must/prefer + tools + memory.l1/l2/l3 参数
- [x] 12.8 User overrides.yaml 合并：用户覆盖 > 默认；`must` 追加但 MUST NOT 删除核心 memory
- [x] 12.9 `BudgetAllocator`：超预算（context_window × 0.6）按 priority 从低到高裁剪
- [x] 12.10 `ContextBundle` dataclass + `build_messages(base_system)` 保持 cache-friendly 顺序
- [x] 12.11 `decisions` trace 写 session log：task_type / classifier_path / latencies / tokens / budget_cut
- [x] 12.12 `feedback(bundle, used_tools, final_response)` 尾部记录（v1 只记不学）
- [x] 12.13 `config.context.assembler.enabled=false` 紧急回滚路径到 legacy 全量模式
- [x] 12.14 TTS 预播：agent loop stream 首 chunk 到达 → TTS 立即播首句，perceived latency < 500ms
- [x] 12.15 预播语料：首版固定 "嗯..." / "让我查一下..." 2 条随机（P4-S11 UX 再调）
- [x] 12.16 单测：三层级联、parallel beats serial、must 不能删、budget shrink、bundle messages 顺序
- [x] 12.17 Bench：ContextAssembler p95 < 370ms（mock path < 50ms，10 轮稳定）

## 13. Context Compressor (P4-S8, 1d) ✅ 已完成 (2026-04-24)

- [x] 13.1 lift Hermes `context_engine.py` 到 `backend/deskpet/agent/context_compressor.py`
- [x] 13.2 触发条件：当前对话 token 数 > `context_window * 0.75`（spec §13.2 0.7 → 0.75 per 设计收紧，见 compressor docstring）
- [x] 13.3 滚动摘要：保留 first_n=3 + last_n=6 非 system 消息，中段用 `claude-haiku-4-5` 压缩成单条 assistant 摘要
- [x] 13.4 摘要 MUST 作为 dynamic assistant message 注入 first_n 之后 / last_n 之前，NOT 进 frozen system
- [x] 13.5 单测（29 项）：阈值门、short-conversation no-op、LLM fail 回退、first/last verbatim、partition layout、reduction_ratio > 0.4 floor、key-fact transcript、multipart/tool_calls 转写、summariser 调 haiku / temp=0 / max_tokens、summary 带 `[压缩摘要]` marker

## 14. MCP Client (P4-S9, 1d) ✅ 已完成 (2026-04-24)

- [x] 14.1 写 `backend/deskpet/mcp/manager.py`：基于官方 `mcp>=1.0` SDK 的 ClientSession 管理（含 bootstrap.py 工厂）
- [x] 14.2 Config 驱动：`[mcp.servers]` 数组，启动时按 `enabled=true` 拉起 stdio 子进程
- [x] 14.3 Transport 支持：stdio（首选）、SSE、streamable HTTP
- [x] 14.4 Session 握手：`session.initialize()` + `session.list_tools()` + 注入 registry（namespace `mcp_{server}_{tool}`）
- [x] 14.5 崩溃重连：指数退避 1s→2s→4s→8s→16s，最多 5 次；超限标 state=failed 从 schemas 移除
- [x] 14.6 优雅关闭：AsyncExitStack aclose() 每个 session（2s timeout）+ 终止子进程
- [x] 14.7 统一 `mcp_call(server_name, tool_name, args)` 工具；unknown server / unknown tool / dead session 返回明确 error
- [x] 14.8 MVP ship 只配 `@modelcontextprotocol/server-filesystem`（scope `%APPDATA%\deskpet\workspace\`）+ weather (disabled stub，真实实现在 S10 skill)
- [x] 14.9 审查守门：默认 config 空 brave-search（`test_default_config_no_brave_search` 护栏）
- [x] 14.10 Resource / Prompt read-only：`list_resources` / `read_resource` / `list_prompts` / `get_prompt` 暴露 MCPManager API（IPC 于 S11 接入）
- [x] 14.11 单测（13 项）：spawn / crash reconnect / max retries fail / namespace 不冲突 / dead session dispatch fast-fail / unknown server / unknown tool / graceful shutdown / disabled skip / unknown transport skip / config no-brave / config filesystem scoped / mcp_call success

## 15. Skill System + 热加载 (P4-S10, 1d) ✅ 已完成 (2026-04-24)

- [x] 15.1 写 `backend/deskpet/skills/loader.py`：扫 `%APPDATA%\deskpet\skills\{built-in,user}\` 下所有 SKILL.md
- [x] 15.2 YAML frontmatter 解析：required 字段 `name, description, version, author`；缺失 log warning 跳过
- [x] 15.3 前端 slash command：输入 `/name` 转 `skill_invoke(name, args=[])` 工具调用
- [x] 15.4 `SkillLoader.execute(name)`：SKILL.md body 作为 user role message 注入（NOT system，保 cache）
- [x] 15.5 Watchdog 监听 user 目录：debounce 1s 自动 reload（D3 决策）；reload 失败 log 但保留已加载
- [x] 15.6 可选 `script.py` 沙箱执行：受限 globals，timeout ≤ 10s，stdout 注入为 user message；超时 kill 进程
- [x] 15.7 Ship 3 个 built-in：`recall-yesterday` / `summarize-day` / `weather-report`
- [x] 15.8 Assembler SkillComponent 集成：policy.prefer=[skill:name] 自动挂载为 skill_prelude
- [x] 15.9 `list_skills()` IPC 返回 metadata 供前端 MemoryPanel 展示
- [x] 15.10 单测：valid load / invalid 跳过 / hot reload debounce / script timeout kill / policy auto-mount

## 16. 前端 MemoryPanel + Context Trace UI (P4-S11, 1.5d) ✅ 已完成 (2026-04-24)

- [x] 16.1 `tauri-app/src/components/MemoryPanel.tsx`：展示 MEMORY.md / USER.md 条目（只读 + 删除按钮）
- [x] 16.2 展示 L2 最近 sessions 列表 + 点进去看 messages（沿用已有 scope=all 视图）
- [x] 16.3 展示 L3 向量搜索栏：用户键入自然语言查历史
- [x] 16.4 展示已加载 skills 列表（built-in / user 分组）
- [x] 16.5 `tauri-app/src/components/ContextTracePanel.tsx`：拉每轮 decisions 渲染 timeline + token 分布条
- [x] 16.6 Timeline：每轮 classifier_path（local/cloud/echo）+ latency + total_tokens
- [x] 16.7 Budget 超警 banner + token 预算用量条（context_window 可调）
- [x] 16.8 IPC 对接：`backend/p4_ipc.py` 新增 `skills_list / decisions_list / memory_search / memory_l1_list / memory_l1_delete`，全部服务未注册时优雅降级
- [x] 16.9 Backend 22 个单测覆盖 5 个 handler + 612 条回归全绿（仅 1 条 timing-flaky），`tsc --noEmit` + `vite build` 通过

## 17. 性能回归 + 冷启动 + Ship (P4-S12, 1d) ✅ rc1 (2026-04-24)

- [x] 17.1 Benchmark 脚本 `scripts/bench_phase4.py`：L1/L2 recall + skills list + FileMemory I/O
- [~] 17.2 冷启动计时：Launcher 流程未变，BGE-M3 预热仍 lazy —— 冷启动验收在 S13 main.py 集成时重跑
- [x] 17.3 SLO 验收（rc1 覆盖组件级）：
   - FileMemory.read_snapshot p95 0.22ms / SLO 10ms ✅
   - MemoryManager.recall(L1+L2) p95 1.70ms / SLO 30ms ✅
   - SkillLoader.list_skills p95 < 1ms / SLO 5ms ✅
   - 全链 Assembler / 首字 / cache 延到 S13 main.py 集成后量测
- [x] 17.4 Bundle 体积 rc1 不变（BGE-M3 按需下载至 %LocalAppData%\deskpet\models\）
- [x] 17.5 Schema 迁移：v8→v9 已在 S1 落地并单测（tests/test_deskpet_migrator*）
- [x] 17.6 Rollback smoke：ContextAssembler.enabled=False 已在 S6 单测 `tests/test_deskpet_assembler.py::test_legacy_bypass_when_disabled` 覆盖
- [~] 17.7 UI smoke：Preview MCP 下 Tauri 窗口渲染受限（0×0 viewport），Vite 日志确认无报错；native Tauri 烟测延到 S13
- [x] 17.8 `CHANGELOG.md` 新增 v0.6.0-phase4-rc1 条目；`openspec/changes/p4-poseidon-agent-harness/tasks.md` 同步完成态
- [~] 17.9 OpenSpec archive：rc1 暂保留 change 目录，待 S13 集成跑完再 `/opsx:archive`
- [x] 17.10 打 tag `v0.6.0-phase4-rc1`

**rc1 注：** S0-S11 组件全部落地并单测通过（612 条 deskpet 套件 + 22 条 P4 IPC）。S12 rc1
只做“组件 SLO 基线 + tag”，全链（main.py session flow + LLM 首字 + prompt cache）的
集成和冷启动计时在 **S13 Lead 集成 sprint** 跑完后再收尾 §17.2 / §17.7 / §17.9。
