# 记忆系统任务现状（截至 2026-05-23）

**统合源**: `2026-05-21-memory-system-survey.md` / `2026-05-22-memory-system-upgrade-plan.md` /
`2026-05-22-memory-system-upgrade/{00-PRD, 01-TDD, 02-manual-test-cases, STAGE0-baseline,
STAGE0-health-report}.md` / `2026-05-23-memory-v2-followup.md` + master 实际 commit 交叉验证。

**用途**: 一份纸把"做了什么 / 没做什么 / 不做了什么"讲清楚。后续看进度看这一份就够。

---

## TL;DR

```
第一代「三层 + RRF」 ─────────────────────────► 永久在跑（不下线）
                                                 │
                              [memory.v2] flag    │ 全关 → 回到第一代
                                                 │
memory-v2 Stage 0 + Stage 1（8 个 WI 全交付）── ✅ 已合并 master (PR #2)
                                                 │
                              ↓ followup 9 项 ────┤
                              ├ B3 PR 合并 ────── ✅ 完成 (68b1fe9)
                              ├ B4 BGE-M3 ─────── ✅ 完成 (a1ff958)
                              └ 其余 7 项 ──────── ⬜ 未完成

Stage 2 (S2.1-S2.4) ──────────────────────────── ⬜ PRD 已定义，未启动
Stage 3 (远期 roadmap) ───────────────────────── ⬜ 未启动

明确不做（PRD §2.2 非目标）─────────────────── ❌ 已废弃方向
```

---

## 一、✅ 已完成（master 上）

### Stage 0 — 接入前体检

| WI | 内容 | 证据 |
|---|---|---|
| **M0.1** 死代码体检 + smoke 回归 | 逐个 import + 构造 Phase A-E 类，固化为 `tests/test_memory_v2_smoke.py` 长期回归（10/10 ✅）；7 张 v2 表惰性 `CREATE IF NOT EXISTS` 验证通过 | `STAGE0-health-report.md` ✅ 全部 ✅ 可直接接入 |
| **M0.2** 第一代 baseline 度量 | 35 条 `(query, expected_msg_id)` 中文 fixture 入库（`zh_fixture.py`）；`EvalReport` 新增 `token_per_query` 字段；baseline 钉死在 `zh_baseline.json`；BGE-M3 重钉后再升级（见后） | `STAGE0-baseline.md` ✅ |
| **M0.3** config schema | `config.toml` 新增 `[memory.v2]` + `[memory.v2.facts]` 段（全 false 默认）；`MemoryV2Config` / `MemoryV2FactsConfig` dataclass；嵌套 TOML 解析验证 | TG-1 5/5 ✅ |

### Stage 1 — 激活 memory-v2（全部 8 个 WI）

| WI | 内容 | 关键改动 | 集成测试 |
|---|---|---|---|
| **M1.1** 评估反馈回路常驻 | `memory_thumbs_up` ws 命令 + 前端历史/消息面板 👍/👎；落 `memory_user_feedback` 表 | flag `feedback_loop` | TG-2 8/8 ✅ |
| **M1.2** facts 抽取（shadow 模式）| `_on_message_written` 扩签名为 `(mid, content, role)`；`main.py` 组合 fanout callable；`asyncio.create_task` 异步不阻塞；`min_user_chars` 由 config 驱动（去掉硬编码 `<8`）；`scripts/facts_backfill.py` 历史回填 | flag `facts_extract` | 同上 |
| **M1.3** reranker 独立先上 | `EnhancedRetriever(facts_weight=0, reranker=...)` 包老 Retriever；mock 模型时**自动 bypass + warn**（绝不让 mock 污染顺序） | flag `rerank` | TG-3 4/4 ✅ |
| **M1.4** EnhancedRetriever 接管召回 | facts 表加 `embedding BLOB` 列；`FactsStore.upsert` 写时 embed；`FactsStore.vector_search`（brute-force cosine）；`EnhancedRetriever.__init__` 加 `embedder` 参数；`_collect_fact_hits` 改调向量召回（不再 LIKE）；`facts_weight=0.2` 显式传 | flag `enhanced_retriever`（依赖 `facts_extract`） | TG-4 5/5 ✅ |
| **M1.5** chunking + query rewriting | `VectorWorker` enqueue 前 `MessageChunker.chunk_message` 切块进 `messages_chunks`（写入侧）；召回返回 parent；`recall()` 入口 `LLMQueryRewriter.rewrite` 短 query；chunk backfill 脚本 | flag `chunking` / `query_rewrite` | TG-5 6/6 ✅ |
| **M1.6** 工作记忆（code 任务） | `file_write`/`file_read` handler 改 **async** → 直接 `await record_action(...)`；新建 `assembler/components/workspace_memory.py`；进 `assembler/policies/default.yaml` 的 code policy `prefer` 列表；新工具 `workspace_recall(query)` | flag `workspace_memory` | TG-6 7/7 ✅ |
| **M1.7** reflection + skill memory | `ReflectionWorker.run_once()` 低频定时调（写进 `facts` 表 `category="reflection"`）；`SkillMemoryStore` 独立 CRUD 接入；无 LLM 时跳过本次不报错 | flag `reflection` | TG-7 4/4 ✅ |

### 后续 fix（4 轮人工测试 + bug fix）

| Commit | 内容 |
|---|---|
| `03f3b15` | facts value 强制与源消息同语言（deepseek 默认翻译成英文的 bug） |
| `ebd2928` | 第 3 轮 LLM-enabled 人工测试结果（the relay deepseek-chat 真测）|
| `a1ff958` | embedder PYTHONPATH 修复 + BGE-M3 baseline 重钉（hit@5 由 0.11 → 0.43）|
| `68b1fe9` | **PR #2 merged**：worktree-memory-upgrade → master |

### 关键 commit 主链路

```
e3e090b feat(memory-v2): Phase A-E recall system upgrade    ← 死代码基础写于此
       ↓
a15105f feat(memory-v2): 激活全部 8 个 WI（Stage 0 + Stage 1）  ← 本轮核心
       ↓
03f3b15 fix(facts): 强制 value 同语言
ebd2928 docs: 第 3 轮人工测试
       ↓
a1ff958 fix: embedder PYTHONPATH + BGE-M3 baseline 重钉
       ↓
68b1fe9 Merge PR #2 → master      ← 整批落地
```

### 自动化测试 — 全绿

```
TG-0 体检 smoke           : 10/10 ✅
TG-1 config flag          :  5/5  ✅
TG-2 facts 接入集成        :  8/8  ✅
TG-3 reranker 集成        :  4/4  ✅
TG-4 EnhancedRetriever    :  5/5  ✅
TG-5 chunking+rewrite     :  6/6  ✅
TG-6 工作记忆集成          :  7/7  ✅
TG-7 reflection 集成      :  4/4  ✅
TG-8 eval 门控            :  4/4  ✅

backend pytest:  1799 passed, 13 skipped, 0 fail  ✅
frontend vitest: 279 passed (20 files)            ✅
frontend tsc:    0 error                          ✅
eval_gate:       PASS                             ✅
```

### eval baseline 演化

| 指标 | mock baseline（早期）| **BGE-M3 真实 baseline（当前）** | 提升 |
|---|---|---|---|
| hit@1 | 0.0000 | **0.3429** | +∞ |
| hit@5 | 0.1143 | **0.4286** | ~3.75x |
| hit@10 | 0.2000 | **0.8286** | 4.14x |
| MRR | 0.0620 | **0.4253** | ~6.86x |
| token_per_query | 198.23 | 195.86 | 持平 |

`scripts.eval_gate` 现在门控的是 BGE-M3 真实基线，回归任一项即 FAIL。

### 人工测试覆盖（MR-0 ~ MR-8，共 4 轮）

| 用例 | 状态 |
|---|---|
| **MR-0 第一代零回归（一票否决）** | ✅✅✅✅ 四轮均通过 |
| MR-1 facts 抽取（含 MR-1-6 临时信息防误抽 ★）| ✅ 第 3+4 轮真 LLM 通过 |
| MR-2 facts 进召回 | ✅ 第 4 轮 BGE-M3 真向量召回通过（3 query × 10 hits 中 6 是 facts）|
| MR-3 reranker | ✅ 降级自动 bypass 验证 |
| MR-4 工作记忆 | ⚠️ store 层 ✅；agent-loop 端到端 Tauri GUI 联调未做（→ followup B1）|
| MR-5 reflection | ✅ 真 LLM 出合理中文元认知笔记 |
| MR-6 评估反馈回路 | ✅ thumbs 落表 + eval CLI 三子命令正常 |
| MR-7 flag 一键回退 | ✅ |
| MR-8 worktree 隔离 | ✅ |

---

## 二、⬜ 未完成（待做）

### 优先级排序（按 followup 文档 + 当下用户感知）

| # | 项 | 出处 | 优先级 | 工作量 | 阻塞解除 |
|---|---|---|---|---|---|
| 1 | **A1 跨 key 矛盾治理** | followup A1 / PRD §4.3 S2.3 | **P1（用户最易感知）** | 中（2-3 天）| 无 |
| 2 | **B1 MR-4 GUI 完整联调** | followup B1 | P1 | 小（半天）| 需主 checkout 跑 Tauri dev |
| 3 | A4 episodic→semantic 固化通路 | followup A4 / S2.4 | P2 | 中 | 无 |
| 4 | A3 token 效率双门控 | followup A3 / S2.2 | P2 | 小 | 无 |
| 5 | A2 entity-matching 检索路 | followup A2 / S2.1 | P3 | 中 | 无 |
| 6 | B2 中转账号 503 排查 | followup B2 | P3（账号级，非代码）| 外部 | 充值/换号 |
| 7 | Stage 3 远期 | followup C.* | P3+ | 大 | — |

### 详细：每一项是什么

#### A1. 跨 key 矛盾治理（"花生 + 海鲜并存" bug）★

**现象**：用户先说"对花生过敏"，再说"其实不过敏花生，是过敏海鲜"。第二句 LLM 抽成 **新 key** `seafood_allergy`，旧的 `peanut_allergy` **没被关掉** → 两条 active facts 矛盾并存。

**根因**：`FactExtractor._persist_extracted` 只在 `(subject, key)` **完全一致**时触发 merge LLM；跨 key 的逻辑矛盾完全盲区。这是 mem0-style merge 的固有局限。

**Stage 2 落地方向**：
- `facts` 表加 `superseded_by INTEGER REFERENCES facts(id)` 列
- 新事实写入前，merge LLM 多看一眼同 subject 下所有相关 active facts，判断"我跟谁矛盾？" → 把被推翻的标 inactive + 写 `superseded_by`
- 或：低频反思任务额外跑一次"同 subject 矛盾扫描"
- 配套：新增 `memory_forget(fact_id_or_pattern)` 工具/ws 命令让用户手动删

**复杂度**：中。需要新 LLM prompt 设计 + eval 集挂"矛盾检出率"指标。

#### A2. entity-matching 检索路

**现状**：召回三路（vec + FTS + recency + salience）经 RRF 融合，**没有按"实体"建独立索引**。问"旺财怎么样了"时无法直接靠 entity 索引秒命中"宠物名=旺财"的 fact。

**Stage 2 落地方向**（评审建议）：
- 不另建实体表，**直接复用 `facts` 表的 `subject`/`key` 作为天然实体索引**（每条 fact 本就是结构化 entity-attribute-value）
- `EnhancedRetriever` 新增 entity 路：query 经 NER 提取实体 → `facts.find_by_subject_or_key_substring(entities)` → 高 weight 进 RRF
- entity NER 用小模型 / 规则 / LLM 三档退化

#### A3. token 效率双门控

**现状**：eval 门控只看 `hit@5` 不回归 + `token_per_query` 单边不超 +30%。

**Stage 2 落地方向**：召回策略变更须**同时**满足：
- 准确率 ↑（hit@5 提升或不降）
- token/query 不爆（≤ baseline × 1.3）

把这条规则做成 `scripts/eval_gate.py` 内的复合 gate，回归任一项即 FAIL。

#### A4. episodic→semantic 显式固化通路

**现状**：现有的 `summarizer`（老对话 → 摘要）和 `FactExtractor`（消息 → facts）是两个独立通道，**没有 pipeline 关系**。

**Stage 2 落地方向**：summarizer 出的 session 级摘要 → FactExtractor 二次抽取 → 显式标 `category='episodic_summary'` 落 facts。把"短期对话 → 长期记忆"做成一条声明式 pipeline。

#### B1. MR-4 工作记忆完整 code-mode 联调

**现状**：store 层（`record_action` + `workspace_recall` 工具 + `WorkspaceMemoryComponent` 进 code policy）已完整验证；但**完整 code-mode agent 联调**（用户进 code mode → agent 自动调 `file_write` → 工作记忆自动落库 → 下一轮 agent 自动从工作记忆决策）需要 Tauri GUI 才能驱动。

**worktree 环境约束**：worktree 缺 `backend/.venv`，Tauri dev 模式 spawn 后端会找不到 python，GUI 跑不起来。

**后续落地**：在主 checkout（有 venv）跑 `tauri dev` 进 code mode 真实驱动一遍 agent loop，确认：
- agent 调用 `file_write` 后 `workspace_state` 表自动落行
- 同任务下重复任务，`read_file` 调用数对比 flag off 时是否下降
- `WorkspaceMemoryComponent` 注入的 prompt 段是否真被 LLM 利用

#### B2. 中转账号 503

**现状**：本轮 LLM 测试用的中转账号（<see LOCAL-DEV-CREDENTIALS.md>）跑 `gpt-5.5` / `claude-haiku-4-5` / `gpt-4o-mini` 三个模型全 503 Service Unavailable，只有 `deepseek-chat` 通。

**判断**：账号级配额/模型授权问题，**不是 DeskPet 代码缺陷**。

**后续**：用 ConsolePage 查这个账号对那些模型的访问权限 / 充值，或换主账号跑测试。

#### Stage 3 远期（followup C.*）

- **procedural memory 深化 / RL 优化**：当前 `SkillMemoryStore` 只做了 CRUD 接入。真正自动从对话提取"反复出现的问题→解法"模式 + 主动建议，需要单独 R&D
- **LoCoMo 风格中文回测集**：长程对话基准。本轮 fixture 35 条够回归，不够刷榜
- **embedding 模型升级评估**：跑过 BGE-M3 / Qwen3-embedding / m3e 之类的对比，选最优

---

## 三、❌ 已废弃 / 明确不做

### A. PRD §2.2 明确非目标（**永久不做**）

- 不重写第一代三层（永久保留为 fallback；用户实际跑的是它，flag 全关即回到这里）
- 不追求 LoCoMo / LongMemEval SOTA 分数
- 不在本轮做 procedural memory 的 RL 优化（Stage 3 远期才考虑）
- **不引入图数据库**（entity 索引用 SQLite 实现；facts 表突破百万级再考虑）

### B. 设计中作废的方案

| 作废方向 | 决策出处 | 取代方案 |
|---|---|---|
| facts 用 `FactsStore.search()` LIKE 子串匹配 | PRD D6（评审 P0：中文整句几乎不命中）| **改为向量召回**（PRD §3.1）|
| `enhanced_retriever.py:47` 注释里"MemoryComponent 识别合成 offset ID"机制 | PRD §3 §A5（过时死注释）| Hit.text `[fact] key: value` 直接渲染进 prompt |
| 老的 `config.memory.facts.enabled` flag | PRD §3.1 / facts.py 注释更正 | 用新的 `config.memory.v2.facts_extract` |
| `_on_message_written` 当 hook 列表挂多个 | PRD §4.2 WI-M1.2 评审修正（实为单值 callable）| **组合 fanout callable**（异步 `asyncio.create_task`）|
| file handler 在 worker thread `run_coroutine_threadsafe` | PRD D10（评审 P1：易死锁）| **改 async handler**（registry 已支持 `iscoroutinefunction` 分流）|

### C. 旧调研结论已被取代

- `2026-05-21-memory-system-survey.md` —— memory-v2 实施**之前**的旧调研。结论已被 `2026-05-22-memory-system-upgrade-plan.md` 的 Stage 0/1/2/3 路线图完全取代。**回看历史时参考，不再作为行动依据。**

---

## 四、流程教训（PRD 固化）

> **"模块写完 + 单元测试全绿"被误判为"功能已交付"**。漏掉 wire-in + 端到端验证，Phase A-E 对用户的价值是 0。
>
> ↓ 本轮 DoD 强制改为：
>
> **每个 WI = wire 进 `main.py` + 端到端集成测试绿 + flag 可独立开关 + eval 指标不回归 + 文档更新**。单元测试绿不算完成。
>
> ↓ eval 门控做成 `scripts/eval_gate.*` pre-merge 脚本自动化，**不靠"人工记得跑"**（正是 memory-v2 死代码的同类失误）。

---

## 五、文档地图

| 文件 | 用途 | 状态 |
|---|---|---|
| `2026-05-21-memory-system-survey.md` | 旧调研（memory-v2 实施前）| 已被本轮路线取代，归档参考 |
| `2026-05-22-memory-system-upgrade-plan.md` | 升级总路线（Stage 0/1/2/3）| 完成 Stage 0+1，剩 Stage 2/3 |
| `2026-05-22-memory-system-upgrade/00-PRD.md` | 详细需求与架构决策（v2 评审后）| **执行依据**（Stage 2 落地时复用）|
| `2026-05-22-memory-system-upgrade/01-TDD.md` | 测试规格 + 实测结果（全绿）| TG-0~9 全绿，含 §D 实测结果 |
| `2026-05-22-memory-system-upgrade/02-manual-test-cases.md` | 人工测试 MR-0~MR-8（4 轮 Go）| MR-0 一票否决四轮均通过 |
| `2026-05-22-memory-system-upgrade/STAGE0-baseline.md` | 第一代 baseline 度量 | mock + BGE-M3 两份 baseline |
| `2026-05-22-memory-system-upgrade/STAGE0-health-report.md` | 死代码体检报告 | 全部 ✅ 接口未腐烂 |
| `2026-05-23-memory-v2-followup.md` | Stage 0+1 收尾 backlog | B3+B4 已完成，剩 A1-A4 + B1+B2 + C.* |
| **`2026-05-23-memory-system-status.md`**（本文件）| **任务现状总览** | — |

---

## 六、下一步建议

**P1（影响用户体感）**
1. **A1 跨 key 矛盾治理** —— "花生 + 海鲜并存"是用户实际碰到的脏数据；2-3 天工作量；从 Stage 2 里挑这一项先做最划算
2. **B1 MR-4 GUI 联调** —— 主 checkout 跑半天 Tauri dev 验一遍即可

**P2（战略推进）**
3. A4 episodic→semantic（把 summarizer 与 FactExtractor 串成 pipeline）
4. A3 token 双门控（eval_gate 加一条 AND 条件即可）

**P3 / 远期**
5. A2 entity-matching、Stage 3 全部

**不必做 / 已废弃**
- 重写第一代、图数据库、procedural RL —— PRD §2.2 已锁定永久不做
