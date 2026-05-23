# PRD — DeskPet 记忆系统升级（激活 memory-v2 + 补 2026 gap）

**创建日期**: 2026-05-22
**作者**: 架构设计
**状态**: 第 2 版 —— 已过架构评审，按评审意见修订
**关联**: `plans/2026-05-21-memory-system-survey.md`（旧调研）、
代码分析报告（memory-v2 接入状态追证）、2026 业界调研
**工作分支**: `worktree-memory-upgrade`（隔离 worktree，端口 8200/5273）

> ## 第 2 版修订说明（架构评审后）
> 第 1 版对代码库有 5 处事实性误判，本版已改正：
> 1. `file_edit` 工具不存在 —— 实际只有 `file_read/file_write/file_glob/file_grep`。
> 2. `SessionDB._on_message_written` 是**单一 callable**（整体赋值），不是
>    hook 列表 —— facts 接入需写一个组合 fanout callable。
> 3. assembler 组件路径是 `backend/deskpet/agent/assembler/components/`；
>    新组件还须加进 assembly policy 的 `prefer` 列表才会被运行。
> 4. `FactExtractor` 对 `user` + `assistant` 都抽（非 user-only），且字数门
>    `<8` 硬编码 —— 接入时要让 config 真正驱动它。
> 5. `WorkspaceMemoryStore.record_action` 是 async，而 file 工具 handler 是
>    sync 跑在 worker thread —— 存在 sync/async 桥接成本。
> 另：facts 召回当前是 LIKE 子串匹配（中文整句几乎不命中）→ 本版改为
> facts 走向量召回；reranker 风险最低，提前独立成 WI。

---

## 0. 一句话

DeskPet 上一轮写的 memory-v2（Phase A-E，~2000 行代码 + 全套单元测试）
**整批是死代码 —— 没有一个模块接进 `main.py` 的初始化链**。本次升级 = 用
"接入 + 端到端验证 + eval 指标门控"的工作量，把这批已写好的第二代能力
**真正激活并交付给用户**；再补齐对照 2026 业界实践的若干缺口。

---

## 1. 背景与问题定义

### 1.1 当前真实状态（代码追证，架构评审已复核属实）

- **用户实际在跑的 = 第一代"三层 + RRF"**：L1 `FileMemory`、L2 `SessionDB`、
  L3 老版 `Retriever`（BGE-M3 向量 + FTS5 + recency + salience → RRF →
  companion/code session-affinity 降权）。
- **memory-v2(Phase A-E)全部 dormant**：facts / enhanced_retriever /
  reranker / chunker / query_rewriter / workspace / reflection 均未接进
  `main.py`；`memory_v2_schema.py` 的 7 张表因从不被调用而在生产 state.db
  里不存在；`config.toml` 无任何 `[memory.v2]` 段。
- **写入链路**：`SessionDB.append_message()` 的 `_on_message_written`
  （`session_db.py:101`，单值 `Optional[OnMessageWritten]`）在 `main.py:957`
  被整体赋值为 `_vw.enqueue` —— 写消息只触发向量化。

### 1.2 核心问题

> **"模块写完 + 单元测试全绿" 被误判为 "功能已交付"。** 漏掉 wire-in +
> 端到端验证，Phase A-E 对用户的价值是 0。

### 1.3 业界 2026 对照（差距）

三层 RRF 已生产；事实抽取/召回融合/reranker/chunking/工作记忆/反思全部
"已写未接入"；entity-matching 检索、token/query 评估指标、memory staleness
治理、episodic→semantic 显式固化通路 —— 完全没有。

---

## 2. 目标与非目标

### 2.1 目标

- **G1 激活 memory-v2**：Phase A-E 各模块逐个 wire 进 `main.py`，每模块独立
  feature flag，每步有**端到端集成测试**验证"接入后真实运行栈行为"。
- **G2 度量先行**：升级前用 `eval/` 跑出第一代 baseline；此后召回链路每次
  改动用 eval 门控，指标不回归才推进。eval 跑分**做成 pre-merge 脚本自动化**，
  不靠"人工记得跑"。
- **G3 渐进可回退**：Strangler-Fig —— 每个 flag 关闭即回退第一代。
- **G4 补 2026 gap**：entity-matching、token 效率评估、memory staleness
  治理（Stage 2，本 PRD 定义、详细设计另出）。

### 2.2 非目标

- 不重写第一代三层（永久保留为 fallback）。
- 不追求 LoCoMo/LongMemEval SOTA 分数。
- 不在本轮做 procedural memory 的 RL 优化（Stage 3）。
- 不引入图数据库（entity 索引用 SQLite 实现）。

### 2.3 成功度量

| 指标 | 目标 |
|---|---|
| memory-v2 模块接入率 | 全模块 wire 进 `main.py` 且有集成测试 |
| 召回质量 hit@5 | 相对 baseline 不回归；reranker 单独应有可见提升；facts 全开后期望 ≥ +15% |
| token/query | 全开后增幅 ≤ +30%（指标口径见 §3 D11） |
| 第一代回归 | flag 全关时 backend pytest **0 回归** |
| 端到端验证覆盖 | 每个已接入模块有"接入后行为"集成测试 |

---

## 3. 关键架构决策（动工前定稿）

| # | 决策点 | 决定 | 理由 |
|---|---|---|---|
| D1 | feature flag 粒度 | 每个 v2 模块一个独立 flag，全收在 `[memory.v2]` 段；`config.py` 加 `MemoryV2Config` dataclass | 渐进激活、独立回退 |
| D2 | flag 默认值 | 全部默认 false | 升级期间用户不受影响 |
| D3 | facts 抽取上线方式 | 先 **shadow 模式**（只写 `facts` 表、不进召回），观察窗后再开召回 | 抽取质量未知时先观察 |
| D4 | facts 抽取的采样口径 | `FactExtractor` 现已抽 `user`+`assistant`、字数门 `<8` **硬编码**。**决定**：保留 user+assistant（assistant 的承诺/结论也有记忆价值）；字数门改由 `[memory.v2.facts] min_user_chars` 驱动；`role=tool`/系统消息跳过（代码已是） | 跟随代码现状 + 让 config 真正生效 |
| D5 | facts 进召回的方式 | `EnhancedRetriever(base=, facts_store=, facts_weight=0.2, reranker=, query_rewriter=)` 包住老 `Retriever`；**`facts_weight` 必须显式传**（默认 0.0 = facts 永不进结果，是静默 bug 源） | 非侵入 wrapper；评审指出的坑 |
| D6 | facts 召回检索方式 | **不用 `FactsStore.search` 的 LIKE 子串匹配**（中文整句几乎不命中）。具体落地设计见 §3.1 | 评审 P2/P0：LIKE 召回中文命中率近 0，直接威胁 +15% 目标 |
| D7 | v2 表的创建 | `ensure_memory_v2_tables()` 用 `CREATE TABLE IF NOT EXISTS` 惰性建；不 bump migration `user_version`；并发首调靠 `IF NOT EXISTS` 幂等 + `PRAGMA busy_timeout` | 评审 P3：真实风险是多连接并发写，不是建表本身 |
| D8 | eval 门控执行 | `eval/` 保持离线 CLI；**额外做成 `scripts/eval_gate.*` pre-merge 脚本**，纳入开发流程门控 | 评审建议：别靠"人工记得跑"（正是 memory-v2 死代码的同类失误） |
| D9 | 接口腐烂排查 | Stage 0 体检脚本**固化为 `tests/test_memory_v2_smoke.py`** 长期回归，不只一次性跑 | 评审建议：长期防 v2 模块再次腐烂 |
| D10 | workspace 的 sync/async 桥接 | file 工具 handler 是 sync、跑在 worker thread；`record_action` 是 async。**决定**：把 `file_write`/`file_read` handler 改为 **async**（`registry.register` 已支持 async handler），直接 `await record_action` | 评审 P1：worker thread 里 `run_coroutine_threadsafe` 易死锁；改 async 最干净 |
| D11 | token/query 指标口径 | 量的是**召回结果渲染进 system prompt 的文本 token**（L1+L3+facts 段），用与 agent 同款 tokenizer 估算；不含对话历史 | 评审缺口 4：门控指标口径必须明确 |
| D12 | 数据隐私 | facts 表存敏感事实 —— 不外传、不进诊断包（诊断包是 allow-list，facts 不在内）；`memory_forget` 能力列 Stage 2 | 个人桌宠的记忆即隐私 |

### 3.1 facts 向量召回 — 具体落地设计（评审 P0）

D6 决定 facts 走向量召回。当前代码现状(必须改)：
`EnhancedRetriever._collect_fact_hits`（`enhanced_retriever.py:189`）**写死调
`facts_store.search(query)`** —— 即 LIKE 子串匹配；`EnhancedRetriever.__init__`
无 `embedder` 参数；`facts` 表 DDL 无 embedding 列。

**落地方案（facts 表小，几百行级，无需 sqlite-vec，brute-force 即可）：**

1. **schema**：`memory_v2_schema.py` 的 `facts` 表 DDL 新增 `embedding BLOB`
   列。facts 表在生产中尚不存在(死代码),DDL 直接带该列即可,无需 ALTER。
2. **写入端**：`FactsStore.upsert()` 在写一条 active fact 时,对其规范文本
   `f"{key}: {value}"` 调 `Embedder.embed()`,把向量存进 `embedding` 列。
   `FactExtractor` 异步抽取链路天然异步,embed 不阻塞。
3. **召回端**：`FactsStore` 新增 `vector_search(query_embedding, top_k)` ——
   取所有 active fact 的 `embedding`,Python 内 brute-force cosine,返回 top_k。
   （facts 量级小,brute-force 单次召回 <1ms,不必上向量索引。）
4. **接线**：`EnhancedRetriever.__init__` **新增 `embedder` 参数**;
   `_collect_fact_hits` 改为 `facts_store.vector_search(embedder.embed(query))`,
   不再调 `.search()`。`main.py` 构造 `EnhancedRetriever` 时把现有
   `_embedder` 传进去。
5. **降级**：embedder 处于 mock 模式(BGE-M3 未下载)时,facts 向量召回退回
   `.search()` LIKE 兜底(质量差但不崩),并 warn。

> 这是 WI-M1.4 的真实工作量,不是"接上就行"。WI-M1.4 的 DoD 含上述 5 点。

---

## 4. 需求拆解（Work Items）

> 每个 WI 的完成定义（DoD）统一为：**wire 进 `main.py` + 端到端集成测试绿
> + feature flag 可独立开关 + eval 指标不回归 + 文档更新**。单元测试绿不算
> 完成。

### 4.1 Stage 0 — 接入前体检（阻塞后续，1-2 天）

#### WI-M0.1 · 死代码体检 + smoke 回归
- 逐个 import + 构造 Phase A-E 类，跑最小真实调用，确认对**当前**
  `SessionDB`/`Retriever`/`config`/`Embedder` 接口仍兼容；
  `ensure_memory_v2_tables()` 能建出 7 表。
- 体检逻辑**固化为 `tests/test_memory_v2_smoke.py`**（D9）—— 长期回归。
- 产出 `STAGE0-health-report.md`：逐模块 ✅可接入 / ⚠️接口腐烂（附不兼容点）。

#### WI-M0.2 · 第一代 baseline 度量
- **回测集来源**（评审 P4）：worktree 的 `.dev-userdata` 是空库，造不出
  有意义回测集。**决定**：从主 checkout 的真实 `state.db` 拷一份只读副本
  造回测集；并人工固化一份**中文回测集 fixture**（≥30 条 `(query,
  expected_msg_id)`）入库，作为可重复的稳定基准。
- 跑 `eval run` 出第一代 hit@1/hit@5/MRR。
- `eval/metrics.py` 的 `EvalReport` **新增 `token_per_query` 字段**（口径见
  D11）—— 当前 `EvalReport` 只有 hit/mrr/duration，确属缺口。
- 产出 `STAGE0-baseline.md`。

#### WI-M0.3 · config schema 落地
- `config.toml` 新增 `[memory.v2]` 段（所有 flag 默认 false）+
  `[memory.v2.facts]` 段（`min_user_chars`、`facts_weight` 等）。
- `config.py` 新增 `MemoryV2Config` / `MemoryV2FactsConfig` dataclass,
  `MemoryConfig` 增 `v2: MemoryV2Config` 字段,使 `cfg.memory.v2.facts_extract`
  可访问。
- **嵌套解析风险（评审 P1,M0.3 第一步必须先验证）**：`[memory.v2]` 是 TOML
  里 `[memory]` 的子表,`config.py:513` 是 `_load_section(MemoryConfig,
  raw["memory"])`。**若 `_load_section` 不递归解析嵌套 dataclass**,
  `cfg.memory.v2` 会拿到裸 dict 而非 `MemoryV2Config`。M0.3 动工第一步:
  读 `config.py` 的 `_load_section` 实现确认其嵌套能力 —— 支持则沿用;
  不支持则给 `MemoryConfig` 写自定义 `from_toml`(参考 `BillingConfig`
  的做法)。**不要假设"沿用 `_load_section` 就行"。**
- 顺手更正 `facts.py` 注释里过时的 `config.memory.facts.enabled` 引用。

### 4.2 Stage 1 — 激活 memory-v2

#### WI-M1.1 · 评估反馈回路常驻
- `FeedbackStore` 接 ws 命令 `memory_thumbs_up { msg_id, query, helpful }`，
  前端历史/消息面板加 👍/👎。
- flag：`feedback_loop`。
- **DoD**：前端点反馈 → `memory_user_feedback` 表落行 → eval CLI 能读到。

#### WI-M1.2 · facts 抽取上线（shadow 模式）
- **接入点（评审不符-2 + 复评修正）**：`SessionDB._on_message_written` 是
  **单一 callable**（`session_db.py:101`），不能"并列挂";且
  `session_db.py:304` 当前**只用 2 个参数**调用它:
  `await self._on_message_written(msg_id, content)` —— **没有 `role`**。
  而 `FactExtractor.process_message` 强制要求 `role=`(`facts.py:402`)。

  **决定**:**扩 `_on_message_written` 签名为 `(msg_id, content, role)`**。
  `append_message` 写消息时本就持有 `role`,在 `session_db.py:304` 调用处
  补传即可(1 行改动);现有 `_vw.enqueue` 只用前 2 个参数,扩签名对它无害
  (fanout 内只把 `mid,text` 转给它)。然后在 `main.py` lifespan 写**组合
  fanout callable**:

  ```python
  async def _on_msg(mid, text, role):           # 3 参数,与扩后签名一致
      await _vw.enqueue(mid, text)              # 原有,只取 mid/text
      if cfg.memory.v2.facts_extract:
          asyncio.create_task(_fact_extractor.process_message(
              message_id=mid, content=text, role=role))   # 异步不阻塞
  _sdb._on_message_written = _on_msg
  ```

  改动清单:① `session_db.py` 的 `OnMessageWritten` 类型签名 + `:304` 调用处
  补 `role`;② `main.py:957` 的赋值换成上面的 fanout。
- 采样口径按 D4：`FactExtractor` 已对 `user`+`assistant` 抽、`tool` 跳过；
  字数门改由 `min_user_chars` 驱动（去掉硬编码 `<8`）。
- **facts 存量 backfill（评审缺口 1）**：抽取只对新消息触发，历史对话不会
  被抽 → 上线初期 `facts` 表长期接近空。新增一次性脚本
  `scripts/facts_backfill.py`，对历史 `messages` 批量抽取。
- shadow：此 WI 只到"写 `facts` 表"，**不碰召回**。
- flag：`facts_extract`。
- **DoD**：开 flag 聊天 → `facts` 表有合理事实；`append_message` 不被异步
  抽取阻塞；关 flag → 零调用。

#### WI-M1.3 · reranker 独立先上（评审优化 1：提前）
- reranker 是纯重排、不改召回集合，**质量提升最确定、风险最低**。从原
  WI-M1.4 抽出，**先于 facts 进 RRF 独立上线**，先兑现一部分 hit@5。
- 接入：`EnhancedRetriever` 在 RRF 之后插 `BGEReranker.rerank()`。本 WI 可
  先用 `EnhancedRetriever(base=老Retriever, facts_weight=0.0, reranker=...)`
  —— facts 路关闭，只启用 reranker。
- **mock reranker 风险（评审缺口 6）**：`reranker.py` 缺模型时降级
  `MockReranker`，用 hash 打分会**主动打乱召回顺序**、可能让 eval 回归。
  `rerank` flag 开但模型缺失时 → 自动 bypass（不重排）+ warn，不让 mock
  污染线上。
- flag：`rerank`。
- **门控**：eval hit@5 不回归，期望可见提升。

#### WI-M1.4 · EnhancedRetriever 接管召回（facts 进 RRF）
- `main.py` 按 flag 用 `EnhancedRetriever(base=老Retriever,
  facts_store=..., facts_weight=0.2, reranker=..., query_rewriter=...)`
  包住老 `Retriever`，`MemoryManager` 持有它。**`facts_weight` 必须显式传
  0.2**（D5）。
- facts 召回走**向量召回**（见 §3.1 具体设计），不用 LIKE。
- **facts 文本渲染进 prompt（评审缺口 3 + 复评澄清）**：`EnhancedRetriever.
  _collect_fact_hits` 产出的 `Hit` 已设 `text="[fact] key: value"`、
  `source="facts"`;`MemoryManager._safe_l3`（`manager.py:300`）经 `_to_dict`
  把它透传成 `l3` 结果,`MemoryComponent._render_l3_only`（`memory.py:175`）
  按 `Hit.text` 字段渲染 —— **机制上 fact 文本本就能进 system prompt,无需
  "识别合成 ID"**（`enhanced_retriever.py:47` 那句"MemoryComponent 识别
  offset"是过时死注释,`components/memory.py` 里没有该逻辑）。WI-M1.4 要做的
  是**端到端验证**:facts 命中后 assembler bundle 的 system prompt 里确实
  出现该 fact 文本(见 TDD T4-4)。
- flag：`enhanced_retriever`（依赖 `facts_extract`；单独开会 warn）。
- **门控**：eval hit@5 不回归，期望 ≥ +15%。

#### WI-M1.5 · chunking + query rewriting
- **chunker 是写入侧改造（评审 P5）**：`VectorWorker` enqueue 前长消息切块
  进 `messages_chunks`。需要**自己的 shadow 窗口 + backfill**：先切新消息、
  积累 chunk 数据（可复用 `scripts/facts_backfill.py` 思路加一个 chunk
  backfill），再切召回走 chunks 返回 parent。
- query_rewriter：`recall()` 入口短 query 走 `LLMQueryRewriter.rewrite`。
- flag：`chunking` / `query_rewrite`。
- **门控**：每个 flag 单开 eval 不回归。

#### WI-M1.6 · 工作记忆（code 任务）
- **接入点（评审不符-1 修正）**：`file_edit` 工具不存在。实际只在
  `file_write` 成功分支记 `action="write"`、`file_read` 成功分支记
  `action="read"`；`file_glob`/`file_grep` 是只读浏览，**不记**。
- **sync/async 桥接（评审 P1 / D10 / 复评修正）**：把 `file_write`/
  `file_read` 的 handler 改为 **async `def`**。依据是**工具 registry**
  （`deskpet/tools/registry.py`）的 dispatch 用 `iscoroutinefunction`
  分流(`registry.py:503-508`)—— async handler 直接 `await`,不进 worker
  thread。这样可直接 `await WorkspaceMemoryStore.record_action(...)`,不必
  在 worker thread 里 `run_coroutine_threadsafe`（易死锁）。
  （注意:此处的"工具 registry"`tools/registry.py` 与下一条 assembler 的
  `ComponentRegistry` 是**两套不同的 registry**,勿混。）
- **组件接入（评审不符-3/-4 修正）**：新建
  `backend/deskpet/agent/assembler/components/workspace_memory.py`
  （与既有枚举文件系统的 `components/workspace.py` **区分命名**）;经
  assembler 的 `ComponentRegistry.register(component_实例)`
  （`assembler/registry.py:43`）注册;并把它加进 code task 对应 assembly
  policy 的 `prefer` 列表（`assembler/policies/default.yaml` 的 `code`
  policy 现有 `prefer: [persona, tool, workspace]`）—— **仅建文件不会被
  运行**。WI-M1.6 的 DoD 含:确认 `build_default_assembler` 在哪注册组件
  实例 + 模块级 `_handle_file_write` 如何拿到 `WorkspaceMemoryStore` 实例
  （现是无依赖注入的模块级函数,需一个获取实例的途径）。
- 新工具 `workspace_recall(query)`。
- flag：`workspace_memory`。
- **门控**：同任务下 `read_file` 调用数下降。

#### WI-M1.7 · reflection + skill memory
- **厘清两件事（评审 WI-M1.6 意见）**：`ReflectionWorker(db_path,
  facts_store, llm_call, ...)` 跑反思、产物写进 **`facts` 表**
  （`category="reflection"`）；`SkillMemoryStore` 是**独立的另一个类**，
  两者无调用关系。本 WI 分两小项：
  - M1.7a：`main.py` lifespan 注册低频定时任务调 `ReflectionWorker.run_once()`。
  - M1.7b：`SkillMemoryStore` 接入（procedural memory 存"反复问题→解法"）。
- **reflection 的 LLM 来源（评审缺口 5）**：定时跑 reflection 用主 LLM
  endpoint（`local_llm`）；用户离线/未配置 LLM 时**跳过本次反思**、不报错。
- flag：`reflection`，默认关，先内部观察产出质量。

### 4.3 Stage 2 — 补 2026 业界 gap（Stage 1 站稳后，详细设计另出）

- **S2.1 entity-matching 检索路**：评审建议 —— 不另建平行表，**直接复用
  `facts` 表的 `subject`/`key` 作为实体索引**（facts 本就是结构化
  实体-属性-值），评估"facts 表兼任 entity store"。
- **S2.2 token 效率双门控**：召回策略变更须"准确率↑ 且 token 不爆"。
- **S2.3 memory staleness 治理**：facts 冲突解决从"替换"升级为"演化"
  （旧 fact 标 `superseded_by` 而非删除）；新增 `memory_forget`。
- **S2.4 episodic→semantic 固化通路**：`summarizer` 与 `FactExtractor`
  串成显式 pipeline。

### 4.4 Stage 3 — 远期（roadmap）

procedural memory 深化、LoCoMo 风格中文回测集、embedding 模型升级评估。

---

## 5. 里程碑与排期

| 里程碑 | 内容 | 依赖 | 预估 |
|---|---|---|---|
| M0 | WI-M0.3 config → M0.1 体检(固化 smoke) → M0.2 baseline+回测集 fixture | — | 2-3 天 |
| M1-a | WI-M1.1 反馈回路 + WI-M1.2 facts shadow(+ backfill 脚本) | M0 | 4-5 天 |
| M1-b | **WI-M1.3 reranker 独立上线**（eval 门控） | M0 | 2 天 |
| M1-c | WI-M1.4 EnhancedRetriever 接管 + facts 向量召回（eval 门控） | M1-a + M1-b + 观察窗 | 3-4 天 |
| M1-d | WI-M1.5 chunking(shadow+backfill) + query rewrite | M1-c | 3-4 天 |
| M1-e | WI-M1.6 工作记忆（含 file handler 改 async） | M0（可与 M1-c 并行） | 3-4 天 |
| M1-f | WI-M1.7 reflection + skill memory | M1-a | 2-3 天 |
| M2 | Stage 2 gap（详细设计另出） | M1 全绿 | 后续 |

> facts shadow(M1-a)与"facts 进召回"(M1-c)之间留**观察窗**：真实聊天跑
> 几天 + 跑 backfill，人工抽查 `facts` 表质量达标再开 M1-c。
> reranker(M1-b)不依赖 facts，可与 M1-a 并行，先独立兑现召回提升。

---

## 6. 风险登记

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | Phase A-E 接口腐烂 | 接入即报错 | WI-M0.1 体检 + 固化为 smoke 回归 |
| R2 | facts 抽取 LLM 成本/延迟 | token 账单、变慢 | 小模型 + `asyncio.create_task` 异步不阻塞 + 字数门采样 |
| R3 | facts 抽取质量差 → 脏 facts 污染召回 | 召回变差 | shadow 模式观察窗 + 人工抽查；含"误抽临时信息为长期 fact"的盲查 |
| R4 | facts 召回中文命中率（LIKE 问题） | +15% 目标不成立 | D6：facts 改向量召回，不用 LIKE |
| R5 | `facts_weight` 漏传 → facts 静默不进结果 | 开了 flag 却无效 | D5：WI-M1.4 显式传 0.2，集成测试断言 facts 命中确实进 bundle |
| R6 | v2 表多连接并发写 SQLite | 写冲突 | `IF NOT EXISTS` 幂等 + `busy_timeout`；集成测试真起并发首调验证 |
| R7 | mock reranker 打乱召回顺序 | eval 回归 | `rerank` flag 开但模型缺失 → 自动 bypass + warn |
| R8 | chunker 写入侧改造、老消息无 chunk | 召回不一致 | chunker 自带 shadow 窗 + chunk backfill |
| R9 | file handler 改 async 的回归面 | 工具行为变化 | WI-M1.6 全套工具测试回归；async 化是 `registry` 已支持的路径 |
| R10 | 又"写完不接" | 重蹈覆辙 | 每个 WI 的 DoD 强制含 wire-in + 集成测试 + flag；eval 门控做成 pre-merge 脚本自动化 |
| R11 | reflection 定时任务影响空闲性能 / 无 LLM | 卡顿 / 报错 | 低频 + 默认 flag 关 + 无 LLM 时跳过本次 |

---

## 7. 验收标准（Definition of Done — 整体）

1. Stage 0：体检报告 + smoke 回归用例 + baseline（含 token/query）+ 中文
   回测集 fixture + `[memory.v2]` config schema。
2. Stage 1：7 个 WI 全部 wire 进 `main.py`，每个有端到端集成测试，每个 flag
   可独立开关。
3. eval：reranker 单独应见提升；facts + enhanced 全开后 hit@5 相对 baseline
   ≥ +15% 且 token/query 增幅 ≤ +30%。
4. 回归：flag 全关时 backend pytest **0 回归**；flag 全开时全套测试绿。
5. eval 门控做成 pre-merge 脚本，纳入开发流程。
6. 文档：每个 WI 完成后更新 `01-TDD.md` 实测结果。
7. 不再出现"模块 + 单测 = 完成"的误判 —— DoD 即闸门。
