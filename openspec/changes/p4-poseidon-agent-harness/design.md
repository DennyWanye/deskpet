# Design: P4 Poseidon — Agent Harness + Long-term Memory

## Context

**当前状态**：桌宠 v0.5.0-phase3-rc1 是无状态单向 pipeline（ASR → LLM /chat HTTP → TTS → Live2D），每次对话独立，无记忆、无工具、无规划。

**约束**：
- **Windows x64 only**（Phase 3 已 lock，Linux/macOS 留更远期）
- **NVIDIA GPU 必需**（faster-whisper + BGE-M3 都要 CUDA）
- **单用户产品**（不做 multi-tenant、消息网关、远程控制）
- **Python 后端**（FastAPI + PyInstaller bundle）+ **Tauri 前端**（Rust + TS）
- **Bundle 体积预算**：P3-G2 ≤ 3.5 GB；rc1 实际 1.5 GB，Phase 4 预计 1.8 GB（+BGE-M3 286MB）
- **冷启动预算**：P3-G1 ≤ 90s，不能因 Phase 4 变慢
- **完整决策**：PRD §9 已签字 10/10 决策（详见 proposal.md 和 `docs/P4-agent-harness-prd.md`）

**关键 stakeholders**：
- Owner @denny（产品主导）— PRD 签字人
- DeskPet 后续用户 — 最终使用者，需要"桌宠有记忆"的体验飞跃

## Goals / Non-Goals

**Goals**:
- 把桌宠升级成 agentic companion：长期记忆、工具调用、多轮规划、可中断
- 通过 **拿来主义**（60% Hermes lift + 20% CCB pattern + 20% 自研）在 ~16 人日交付
- 性能优先：记忆召回 <30ms p95、工具往返 <50ms p50、感知首字延迟 <500ms（含 TTS 预播）
- 长期记忆本地化：记忆库、embedding 模型、LLM 会话全部落地主人机器
- 可观测性：ContextAssembler 每轮 decisions + MemoryPanel Context Trace UI

**Non-Goals**:
- 消息网关（Telegram / Slack / Discord / 微信）— 桌宠是本机产品
- 远程控制 / 手机端 — 桌宠绑定主人机器
- ACP 协议 / IDE 集成 — 桌宠不是 dev tool
- Cron scheduler — 推 Phase 5
- Multi-tenant — 永远 single-user
- 本地 LLM 打进 bundle — D2 明确排除，bundle 过大
- 付费搜索 API（Brave / Bing / Tavily / Exa）— D5 明确排除，走自研爬虫
- 记忆库默认加密 — D4 明确关闭，性能优先

## Decisions

### D-ARCH-1: 三层记忆而非单层 RAG

**选择**：L1 文件（markdown）+ L2 SessionDB（SQLite + FTS5）+ L3 向量（sqlite-vec + BGE-M3）三层共存

**替代**：
- 只用向量层（Chroma / LanceDB）
- 只用关系库（Postgres + pgvector）
- 只用文件（markdown + grep）

**理由**：
- **L1 给"我是谁 + 主人是谁"的身份感**（frozen snapshot 模式保 prompt cache）
- **L2 给结构化会话历史**（FTS5 字面匹配快）
- **L3 给语义召回**（"脚上穿的" 能匹配到"袜子"）
- 三层通过 MemoryManager 统一对外，插件可替换

### D-ARCH-2: ContextAssembler 作为 agent loop 前置层（原创）

**选择**：在 Hermes `run_conversation()` 前面插一层 preprocessing，做事前任务分类 + 组件挑选 + 预算分配

**替代**：只靠 Hermes 原有的 ContextEngine 事后压缩

**理由**：
- 事后压缩是"炸了再救"；事前组装是"从一开始就只带需要的"
- 减少每轮 prompt 中无关工具 schema 和无关记忆切片
- 可观测性：每轮 `decisions` 写 log + 前端 Context Trace UI 展示

**代价**：D8 决策 LLM classifier 默认开 → 首字延迟 800ms → 1100ms p50；靠 TTS 预播把感知延迟压到 <500ms。

### D-ARCH-3: Hermes MIT 代码直接 lift，不 fork

**选择**：复制 Hermes 的 `run_agent.py` / `hermes_state.py` / `agent/*.py` / `tools/*.py` 到 `backend/deskpet/agent/` 等目录，文件头加 MIT attribution

**替代**：
- 把 Hermes 作为 pip dependency
- Fork Hermes 仓库

**理由**：
- Hermes 的 `AIAgent` 类构造参数 60 个，大部分桌宠不需要（gateway / OAuth / platform）— **必须瘦身**
- Pip dep 会把不需要的代码也拖进来 + 依赖冲突风险
- Fork 导致后续 sync upstream 成本
- **直接 lift** 允许裁剪到桌宠需要的子集（参数收窄到 ~15 个），同时保留接口兼容方便未来手动吸收 Hermes 更新

### D-ARCH-4: Claude-Code-Best 只学 pattern，不复制代码

**选择**：CCB 无 license，只读其架构做 clean-room rewrite。MCP client 改用官方 `mcp` Python SDK。

**替代**：联系 CCB 作者澄清许可后直接用 TS 代码

**理由**：法律合规 + Python 同栈简化 + 官方 `mcp` SDK 是 Anthropic 维护更可靠

### D-IMPL-1: sqlite-vec 而非 LanceDB / Qdrant / FAISS

**选择**：sqlite-vec 扩展（原 sqlite-vss 后继）

**替代**：LanceDB（独立服务进程）、Qdrant（需 Docker）、FAISS（索引持久化麻烦）

**理由**：
- **嵌入式**，和会话表同库同事务，跨层 join 方便
- 无额外进程，桌宠追求极致轻量
- PyPI 有 prebuilt wheel（`win_amd64`）
- 10 万条量级性能足够（p95 < 20ms）

### D-IMPL-2: BGE-M3 INT8 本地部署

**选择**：BGE-M3 量化到 INT8 ~286MB，打进 bundle

**替代**：OpenAI `text-embedding-3-small`（API，需 key）、Qwen3-Embedding-0.6B（600MB）、gte-large-zh（325MB）

**理由**：
- **本地**，无 API 费、无延迟波动
- 多语言（100+），中英文都强（MTEB 中文榜前列）
- 1024 维，和 Hermes schema 的 embedding BLOB 列容量匹配
- 286MB 在 bundle 体积预算内
- 共享 ASR 的 CUDA context（同一块 GPU）

### D-IMPL-3: Python backend 不换 Rust

**选择**：沿用 Python + FastAPI 做 agent，不把核心搬到 Rust

**替代**：Rust 全量重写（借 Tauri 生态）

**理由**：
- Hermes 是 Python，lift 无成本
- `mcp` SDK 官方 Python 版
- BGE-M3 / faster-whisper / FlagEmbedding / trafilatura 全是 Python 生态
- Rust 收益小（主循环不是热点，LLM 网络调用才是）

### D-IMPL-4: TaskClassifier 级联：rule → embed → LLM

**选择**：三层递进，先便宜后贵，直到置信度达标（D8 决策默认开 LLM）

**替代**：
- 全 LLM 分类（每轮 +300ms 太贵）
- 全规则（覆盖率 <50%）
- rule + embed 不上 LLM（原建议，准确率 85%）

**理由**（D8 落地）：
- 用户明确选"默认开 LLM"，准确率 95% > 延迟代价
- TTS 预播（P4-S7 子任务）把感知延迟压回 <500ms
- 仍然是"级联"：rule 命中直出（2ms），embed 置信 >0.75 直出（15ms），只在必要时才调 LLM（300ms）
- LLM 模型选 `claude-haiku-4-5`（便宜、快）

### D-IMPL-5: AssemblyPolicy 用 YAML 声明式

**选择**：policies/default.yaml + user/overrides.yaml 两层合并

**替代**：Python 规则树、数据库表

**理由**：
- 用户无代码扩展（和 Skill 系统同源）
- 改 policy 不触发 re-deploy
- 仲裁规则（D9）：用户覆盖 > 默认；`must` 允许追加但禁止删除核心 memory

### D-IMPL-6: Web 工具集自研，零付费 API（D5）

**选择**：`web_fetch`（httpx + trafilatura）+ `web_crawl`（selectolax BFS + 关键词打分）+ `web_extract_article` + `web_read_sitemap`

**替代**：Brave API $3/千次、Tavily $5/千次、Exa $10/千次、DuckDuckGo 非官方 lib、纯 webfetch 放弃搜索

**理由**：
- 用户明确选"不付费"
- agent 自己判断该去哪个域名（preferred_sources 白名单给提示）
- 依赖全部开源（`httpx`、`trafilatura`、`selectolax`）
- 配合 `robots.txt` + `User-Agent: DeskPet/0.6` + 域名限流 + 500ms 间隔，避免被封

### D-IMPL-7: Prompt caching 兼容的 Assembler 输出顺序

**选择**：
```
[cacheable system: base + frozen_assembler(persona/time)]  ← 打 cache breakpoint
[dynamic system: memory_block]
[dynamic system: skill_prelude（如有）]
[conversation history]
[current user message]
```

**替代**：把所有 system 内容塞一条

**理由**：保 prompt cache 命中率（目标 >80%）。frozen 部分每轮稳定不变，dynamic 部分每轮变但不破坏 frozen 的 prefix。

### D-MIGRATE-1: schema v8 → v9 自动迁移

**选择**：`PRAGMA user_version=9` + 顺序 ALTER TABLE 脚本 + 后台异步 backfill embedding

**替代**：强制用户删库重装

**理由**：rc1 已有用户（内部 tester），不能清空记忆

## Risks / Trade-offs

### R1. sqlite-vec Windows ABI 兼容
- **Risk**：原生扩展 + PyInstaller 打包可能冲突
- **Mitigation**：用 PyPI prebuilt wheel（`sqlite-vec` 有 win_amd64），PyInstaller hook 显式包含 `.dll`
- **Fallback**：FAISS on-disk 降级（Phase 4 尾段准备）

### R2. BGE-M3 冷加载拖慢启动
- **Risk**：模型冷加载 8-15s，冲击 P3-G1 ≤90s 启动门
- **Mitigation**：模型打进 bundle；启动时预热（async task）；首轮若未 ready 降级到纯 FTS5

### R3. ContextAssembler 每轮 +370ms
- **Risk**：D8 默认开 LLM classifier → 首字延迟 800→1100ms
- **Mitigation**：TTS 预播（classifier 跑时先播"嗯..."）；感知延迟 <500ms；允许用户 disable Assembler 回退

### R4. Claude Code Best 无 license 法律风险
- **Risk**：复制其 TS 代码侵权
- **Mitigation**：clean-room rewrite only；MCP 改用官方 Python SDK；不拿源码

### R5. Prompt cache 失效拖慢 + 吃 token
- **Risk**：mid-session 写 MEMORY.md / 组件顺序抖动 → system prompt 变 → cache miss
- **Mitigation**：Frozen snapshot 模式（Hermes 原设计）；Assembler 保持 frozen 部分稳定；回归测试观察 `cache_read_tokens/input_tokens` >80%

### R6. 工具 schema 爆炸
- **Risk**：60+ tools 全塞 prompt 占 5k+ token
- **Mitigation**：ContextAssembler `ToolComponent` 只暴露 task_type 需要的子集；CCB 的 ToolSearch 延迟加载模式

### R7. ASR 打断对话混乱
- **Risk**：用户说到一半桌宠开始回复
- **Mitigation**：ASR VAD 静音 600ms 才 finalize；说话时热词 "等一下 / 停" 触发 `_interrupt_requested`

### R8. Hermes AIAgent 构造参数过多
- **Risk**：60 个构造参数直接 lift 维护难
- **Mitigation**：瘦身到 ~15 个；去掉 gateway / platform / OAuth

### R9. ContextAssembler 分类错误
- **Risk**：rule 过激 / embed 阈值过低 → task_type 错判
- **Mitigation**：三层级联 + 置信度阈值；fallback `chat` 默认策略；feedback 记录供离线聚类

### R10. 首字延迟超预算
- **Risk**：1100ms p50 仍可能破（classifier 第 3 层 LLM 抖动到 800ms）
- **Mitigation**：P4-S12 性能回归必须卡住 1100ms；Assembler 可 config disable 紧急回滚

### R11. Web crawl 被封 IP
- **Risk**：自爬无搜索 API 兜底，封了就瞎
- **Mitigation**：遵守 robots.txt + 域名限流 + UA 标识；preferred_sources 白名单是大站（不会一下封完）

### R12. 数据库迁移失败
- **Risk**：v8 → v9 ALTER TABLE 失败致 rc1 用户记忆丢失
- **Mitigation**：迁移前 `.bak` 自动备份；失败回滚；backfill embedding 异步不阻塞主流程

## Migration Plan

### 阶段 1: M1 Vanilla MVP（S1-S6，~7 人日）
1. P4-S1 SessionDB + FTS5（lift Hermes `hermes_state.py`）
2. P4-S2 Embedding service（BGE-M3 INT8 + CUDA batch）
3. P4-S3 sqlite-vec + 混合召回
4. P4-S4 MemoryManager + MEMORY.md/USER.md（lift）
5. P4-S5 ToolRegistry + 10 个内置 tool（含 web 工具集）
6. P4-S6 Agent loop（lift Hermes `run_conversation`）

**出口**：agent 能跑通一句 → 工具 → 带记忆回答（**不含** Assembler）

### 阶段 2: M2 Smart MVP（S7-S10，~6 人日）
7. P4-S7 ContextAssembler v1 + TTS 预播
8. P4-S8 Context compressor（lift Hermes）
9. P4-S9 MCP client（port pattern）
10. P4-S10 Skill system（lift + 热加载）+ Assembler SkillComponent

**出口**：智能组装 + 压缩 + MCP + Skill 全部上线

### 阶段 3: M3 Ship-ready（S11-S12，~3 人日）
11. P4-S11 前端 MemoryPanel + Context Trace UI
12. P4-S12 冷启动性能回归 + 整体 benchmark

**出口**：`v0.6.0-phase4-rc1` 可 ship

### 数据迁移
- 现有 rc1 用户 `state.db` schema v8 → v9 on first launch
- 迁移步骤：
  1. `.bak` 备份（带时间戳）
  2. `ALTER TABLE messages ADD COLUMN embedding BLOB`, `salience REAL DEFAULT 0.5`, `decay_last_touch REAL`, `user_emotion TEXT`, `audio_file_path TEXT`
  3. `CREATE VIRTUAL TABLE messages_vec USING vec0(...)`
  4. `PRAGMA user_version=9`
  5. 后台 task：对历史 messages 计算 embedding 回填（低优先级，不阻塞主流程）

### Rollback
- 若 v0.6.0 出重大问题：用户降级到 v0.5.x，从 `.bak` 恢复 state.db（删除 v9 新增列）
- Assembler 可 config `[context.assembler].enabled=false` 热关闭（不用重装）

## Open Questions

**所有 10 条 PRD §9 决策已签字**（见 PRD 最新版本）。仅剩实现期间可能冒出的技术细节：

1. **BGE-M3 推理库选型**：`FlagEmbedding` vs `sentence-transformers` vs ONNX Runtime — P4-S2 执行时 bench 决定
2. **TaskClassifier exemplars 初始集**：需要人工标注 ~100 条样本覆盖 8 个 task_type — P4-S7 执行时做
3. **TTS 预播的语料**：是固定一句"嗯..."/"让我查一下"还是随机多样 — P4-S7 UX 决定
4. **MCP 对 filesystem 的 workspace 默认路径**：`%AppData%\deskpet\workspace\` vs `Documents\deskpet-ws\` — P4-S9 决定
5. **前端 Context Trace UI 的触发方式**：默认展示 / 右键菜单打开 / settings 里切开关 — P4-S11 UX 决定

这些都不阻塞 proposal 通过，推到实现阶段各自 slice 的 explore 里。
