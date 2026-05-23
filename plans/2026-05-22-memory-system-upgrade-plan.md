# DeskPet 记忆系统 — 现状分析 + 升级计划

**日期**: 2026-05-22
**状态**: 计划稿（待评审）
**关联**: `plans/2026-05-21-memory-system-survey.md`（memory-v2 实施*之前*的旧调研）

---

## TL;DR

1. **用户现在实际跑的是第一代"三层 + RRF"记忆系统** —— L1 文件 / L2 SessionDB /
   L3 向量(BGE-M3 + FTS5 + recency + salience → RRF 融合 + session-affinity)。
   这套第一代工程质量很高(迁移护栏、降级容错、companion/code 隔离)。
2. **memory-v2(Phase A-E)整批是死代码。** 上一轮写了 facts 抽取、EnhancedRetriever、
   reranker、chunker、query_rewriter、workspace memory、reflection —— ~2000 行代码 +
   全套单元测试 —— 但**没有一个模块接进 `main.py` 的初始化链**,7 张 v2 表在生产库
   里都不存在。**对用户零生效。**
3. **所以本轮升级的最高杠杆不是"做新东西",而是"激活已经写好的东西"。**
4. 同时,Phase A-E 是按 2026-05 之前的设计做的;对照 2026 业界最新实践还差几块:
   entity-matching 检索、token 效率作为一等指标、memory staleness(高相关陈旧事实)、
   episodic→semantic 显式固化通路。这些列入升级第二阶段。

---

## 一、当前真实状态(代码追证)

> 来源:对 `backend/deskpet/memory/` + `main.py` + `assembler/components/memory.py`
> 的执行路径追踪。每条结论可追到 `文件:行号`。

### 1.1 真正在生产链路里跑的(第一代三层)

| 层 | 实现 | 接入证据 |
|---|---|---|
| **L1** 文件记忆 | `FileMemory` → `MEMORY.md` / `USER.md` | `main.py:566` 构造 → `MemoryManager` 持有 → `memory.py:58` 渲染进 system prompt frozen 块 |
| **L2** 会话 DB | `SessionDB` → `messages` 表 + FTS5 | `main.py:587` 构造 → `memory.py:93` 最近 N 条转成真实对话历史 `bundle.history` |
| **L3** 向量召回 | 老版 `Retriever`：vec(BGE-M3 INT8)+ FTS5 + recency + salience → RRF → session-affinity 降权 | `main.py:600` 构造**老版** `Retriever` → `main.py:613` 传入 `MemoryManager` → `retriever.py:210` `recall()` |

**写入链路**:`SessionDB.append_message()` 的 `on_message_written` hook
**只绑了** `VectorWorker.enqueue`(`main.py:957`)—— 写消息只触发向量化,别的什么都不触发。

### 1.2 代码存在、但完全没接进生产(dormant / dead code)

| 模块 | 设计意图 | 死因(证据) |
|---|---|---|
| `facts.py` FactExtractor/FactsStore | mem0 风格事实抽取 | `SessionDB.on_message_written` 没绑 facts hook;`config.memory.facts.enabled` 这个 flag 在 config.py 里没有读取点 |
| `enhanced_retriever.py` EnhancedRetriever | facts 进 RRF 的非侵入 wrapper | `main.py` 全程用老 `Retriever`,`EnhancedRetriever` 从未被 import/实例化 |
| `reranker.py` BGEReranker | RRF 后 cross-encoder 重排 | 依附 EnhancedRetriever,连带未接入 |
| `chunker.py` MessageChunker | 长消息切块 + parent retrieval | VectorWorker 仍是全消息单向量,chunker 从未被调用 |
| `query_rewriter.py` | 短查询改写 | 同 reranker,依附 EnhancedRetriever |
| `workspace.py` WorkspaceMemoryStore | code 任务的工作记忆(`workspace_state` 表) | file 工具 wrapper 里没有 `record_action` 调用;**注意**别和 `assembler/components/workspace.py`(枚举文件系统目录,在跑)混淆 |
| `reflection.py` ReflectionWorker/SkillMemoryStore | 每日反思 + procedural memory | lifespan 无任何调度 |
| `eval/` QASetBuilder/MetricsRunner | hit@k / MRR 离线评估 | 是可运行的 CLI 工具(`python -m deskpet.memory.eval`),但不在生产链路 —— 这个保持离线即可 |
| `memory_v2_schema.py` 7 张表 | facts / messages_chunks / workspace_state / skill_memory / 3 张 eval 表 | `ensure_memory_v2_tables()` 因上述模块从不被调用而从未执行 → **表在生产 state.db 里不存在** |

### 1.3 测试的盲区

`test_deskpet_memory_{facts,phase_c,workspace,phase_e}.py` 全绿,但**只测模块单元功能**,
没有一个测试验证"接进 `main.py` 后在真实运行栈里按预期工作"。**单元测试全绿 ≠
功能对用户生效** —— 这正是 memory-v2 的现状。

### 1.4 一个必须记住的流程教训

> Phase A-E 被当成"已完成"(代码 + 单测齐全),但因为**漏了 wire-in 这一步**,
> 交付物对用户的价值是 **0**。"模块写完 + 单测绿"不是完成,"**接进生产链路 +
> 端到端验证 + flag 打开**"才是。本计划把后者作为每一项的 Definition of Done。

---

## 二、业界 2026 记忆系统最佳实践(调研摘要)

| # | 实践 | 说明 |
|---|---|---|
| 1 | **检索 >> 写入** | LoCoMo 基准上检索方法差异带来 ~20 分准确率,写入策略只带 3-8 分。投入重点应在召回。 |
| 2 | 多信号检索融合 | semantic + BM25 + **entity matching** 三路归一化融合,再加可选 **reasoning-aware reranking**。 |
| 3 | **token 效率是一等指标** | mem0 2026:LoCoMo 92.5 @ ~6900 token/query(旧法 ~26000)。每次召回花多少 token 要被测。 |
| 4 | 记忆分仓 + 固化通路 | episodic(事件)/ semantic(事实)/ procedural(技能)。前沿是 episodic→semantic 的**显式 consolidation pathway**。 |
| 5 | **memory staleness 是未解难题** | 时间衰减只压得动"低相关"记忆;"高相关但陈旧"的事实(换工作后旧雇主仍被高频召回)压不动 —— 要把变化建模成**演化**。 |
| 6 | 工程规范 | 异步写入(不阻塞响应)、multi-scope 打标 + 自动排序(user > session > raw)、LoCoMo/LongMemEval 基准评估。 |

DeskPet 现状对照:第一代三层在工程稳定性上已属 top quartile;memory-v2 的设计覆盖了
事实抽取/reranker/chunker/workspace/reflection —— **但因为没接入,等于业界第二代能力
一个都没真正拥有**。

---

## 三、差距分析(三栏)

| 能力 | ① 生产在跑 | ② 代码已写未接入 | ③ 业界有、DeskPet 完全没有 |
|---|---|---|---|
| 三层 RRF 召回 | ✅ | | |
| 召回质量量化(hit@k/MRR) | | ✅ eval CLI | |
| 事实抽取 + 结构化 | | ✅ facts.py | |
| facts 进召回融合 | | ✅ EnhancedRetriever | |
| cross-encoder reranker | | ✅ reranker.py | |
| 长消息 chunking | | ✅ chunker.py | |
| 查询改写 | | ✅ query_rewriter.py | |
| 工作记忆(code 任务) | | ✅ workspace.py | |
| 反思 / procedural memory | | ✅ reflection.py(骨架) | |
| **entity-matching 检索** | | | ❌ |
| **token/query 作为评估指标** | | | ❌ |
| **memory staleness 处理** | 部分(decay) | facts 有 confidence/updated_at | ❌ 高相关陈旧事实的演化建模 |
| **episodic→semantic 显式固化** | 部分(summarizer 压缩) | | ❌ summarizer 与 facts 没串成 pipeline |

**结论**:②栏是"白捡的杠杆" —— 接进去就能拿到业界第二代能力。③栏是接完②之后的真增量。

---

## 四、升级计划

设计原则:**Strangler-Fig + eval-gated 渐进激活**。每一步都用 `eval/` 评估底座做
门控,指标不回归才推进下一步;每个模块都有真 feature flag,出问题能一键回退。

### Stage 0 · 接入前体检(1-2 天,阻塞后续)

memory-v2 单元测试绿,但"接进真实运行栈能不能跑"完全没验证过。先做最小接入冒烟:

- [ ] 在一个 dev 实例里手动构造 `EnhancedRetriever` + `FactExtractor`,跑一次真实
      `recall` / `extract`,确认不崩、表能建出来。
- [ ] 跑 `python -m deskpet.memory.eval build` 从 `messages_archive` 造回测集,
      `eval run` 出**当前第一代系统的 baseline**(hit@5 / MRR / token/query)。
- [ ] 产出一份"死代码体检报告":哪些模块接入即可用、哪些有接口腐烂需修。

### Stage 1 · 激活 memory-v2(核心,最高杠杆)

把已写好的 Phase A-E 逐个 wire 进 `main.py`。每步独立 feature flag(`config.toml`
新增 `[memory.v2]` 段),每步补**端到端集成测试**(验证"接入后真实运行栈行为"),
每步用 Stage 0 的 baseline 做指标门控。

- **S1.1 评估底座常驻** — `memory_thumbs_up` ws 命令接进前端(历史面板点反馈),
  FeedbackStore 真正收数据。eval CLI 保持离线。
- **S1.2 事实抽取上线** — `SessionDB.on_message_written` 加挂 `FactExtractor`(异步,
  不阻塞);config 加 `[memory.facts] enabled`。先 shadow 模式(只写 facts 表不进
  召回)跑几天看抽取质量。
- **S1.3 EnhancedRetriever 接管召回** — `main.py` 用 `EnhancedRetriever` 包住老
  `Retriever`,facts 进 RRF(权重先 0.2)。flag `[memory.v2] enhanced_retriever`。
  **门控**:eval hit@5 相对 baseline 不得回归;期望 ≥ +15%。
- **S1.4 reranker + chunker + query_rewriter** — 长消息切块、RRF 后 cross-encoder
  重排、短查询改写。各自 flag。门控同上。
- **S1.5 工作记忆** — `file_read/write/edit` 工具 wrapper 加 `record_action` hook;
  新增 `WorkspaceMemoryComponent` 在 code task 装载。**门控**:同任务下 `read_file`
  调用数下降。
- **S1.6 reflection / skill memory** — 每日空闲调度 `ReflectionWorker`;`SkillMemoryStore`
  接入。flag 默认关,先内部观察。

> 每步的 DoD = wire 进 main.py + 端到端集成测试绿 + flag 可开关 + eval 指标不回归。

### Stage 2 · 补 2026 业界 gap

- **S2.1 entity-matching 检索路** — 写入时抽取命名实体存平行集合,召回时 query 实体
  匹配加分,与 semantic/BM25 三路归一化融合(mem0 2026 做法)。
- **S2.2 token 效率纳入评估** — `eval/metrics.py` 增加 `token/query` 指标;每次召回
  策略变更同时看准确率**和** token 成本,避免单维度优化。
- **S2.3 memory staleness 治理** — facts 冲突解决从"替换"升级为"演化":新事实取代
  旧事实时,旧事实不删而是标 `superseded_by` + 召回降权;高相关但已 superseded 的
  事实不再浮到 top。
- **S2.4 episodic→semantic 固化通路** — 把 `summarizer`(压老对话)与 `FactExtractor`
  串成显式 pipeline:归档前先抽事实,事实进 semantic 仓,原文压缩进 episodic 摘要。

### Stage 3 · 远期

- procedural memory 深化:reflection 提取的"反复出现的问题→解法"做成可召回的 skill。
- 建一个 LoCoMo 风格的中文多轮回测集,定期对标。
- 评估换 embedding 模型(BGE-M3 → 更新的多语模型)的收益。

---

## 五、优先级与风险

| Stage | 收益 | 风险 | 建议 |
|---|---|---|---|
| **S0 体检** | 不验证就接 = 重蹈"写完没接"覆辙 | 极低 | **必做、先做** |
| **S1 激活 memory-v2** | 白捡业界第二代能力(代码已在) | 中:接口可能腐烂、多一次 LLM 调用的延迟/成本 | **核心**,渐进 + eval 门控 |
| **S2 补 2026 gap** | 召回质量再上台阶 | 中 | S1 站稳后做 |
| **S3 远期** | 桌宠"个性化"体感 | 高(设计未稳) | 留 V2 |

**关键风险点**:
- R1 接口腐烂 —— Phase A-E 写于 2026-05,期间 SessionDB / Retriever 可能已变。S0 体检专门查。
- R2 LLM 调用成本 —— 事实抽取每条消息一次 LLM。用小模型 + 异步 + 采样(不是每条都抽)。
- R3 召回回归 —— 任何召回链路改动都可能让某些 query 变差。eval 门控 + flag 兜底。
- R4 又写完不接 —— 用每步 DoD(wire + 集成测试 + flag)强制闭环。

## 六、给决策者的判断

- DeskPet 记忆系统的真实问题**不是"设计差"或"要重写"**,而是**"第二代代码写好了却没接通"**。
- 本轮最划算的事:Stage 0 + Stage 1 —— 用接入 + 验证的工作量,拿回 ~2000 行已测代码
  的全部价值。这比从零做新功能性价比高得多。
- Stage 2 才是真正的"新增量",对标 2026 业界;Stage 3 是锦上添花。
- 不要再发生"模块 + 单测 = 完成"的误判 —— 本计划每一项的完成定义都包含 wire-in +
  端到端验证 + flag。
