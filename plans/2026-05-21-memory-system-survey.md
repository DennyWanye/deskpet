# Deskpet 记忆系统调研 + 改造方向

日期：2026-05-21
作者：调研 by Claude
状态：调研稿（未实施）

---

## TL;DR

Deskpet 现有的记忆系统是一套**经典的"三层 + RAG 混合召回"架构**（L1 文件 / L2 SessionDB / L3 向量），整体设计比业界很多 demo 级 chatbot 都干净：
- 有迁移 + 备份 + 回滚护栏（`schema.py` + `migrator.py`）
- 有 FTS5 + sqlite-vec + RRF 融合 + salience 衰减（`retriever.py`）
- 有 30 天自动总结归档（`summarizer.py` + `messages_archive`）
- 有跨 session affinity 降权（companion vs code）
- 有失败降级契约（mock embedder / sqlite-vec 缺失 fallback / 层级隔离）

但拿 2025-26 业界主流"记忆系统第二代"做参考，下面 6 点是真有改进空间的：

1. **写入端没有"重要性 / 提取意图"判断** — 所有 message 一视同仁灌进 SessionDB + 向量库
2. **L1 (MEMORY.md / USER.md) 是手写文件，没有自动提取 → 不会自我成长**
3. **summarize 只压老对话，不做"事实抽取 → 结构化记忆"** — 缺 mem0/Letta 风格的"事实合并 + 冲突更新"
4. **召回质量没有量化评估** — 没有 hit@k / MRR / 用户标注回路，每次调参拍脑袋
5. **向量层是 BGE-M3 全句向量** — 没有 chunking、没有 multi-vector、没有 query rewriting
6. **没有"工作记忆 / 工具记忆"概念** — 长任务里 tool 调用结果都直接进 messages，搜不到也找不回

下文先把现状盘清楚，再对应每个痛点给出业界做法 + 落地建议。

---

## 一、现状盘点

### 1.1 三层架构（保留 hermes 设计血脉）

| 层 | 文件 | 介质 | 容量 | 失效模式 |
|---|---|---|---|---|
| **L1** 文件记忆 | `file_memory.py` | 本地两个 markdown：`MEMORY.md`（50KB）+ `USER.md`（20KB），`\n§\n` 分隔 | KB 级 | 文件损坏 / 容量满 → 按 salience 驱逐 |
| **L2** 会话 DB | `session_db.py` (`messages` 表 + `messages_fts`) | aiosqlite WAL + FTS5 trigram | 数百 MB 级 | sqlite 锁 → 5 次指数退避重试 |
| **L3** 向量层 | `embedder.py` + `vector_worker.py` + `messages_vec` (sqlite-vec) | BGE-M3 1024 维 INT8 + cosine | GB 级 | sqlite-vec DLL 缺失 → 跳过 vec 路；BGE-M3 没下载 → mock 向量降级 |

入口统一在 `MemoryManager.recall()` / `MemoryManager.write()`（`manager.py`），对外永远不抛。

### 1.2 召回链路（`retriever.py`）

**四路 fan-out → RRF 融合 → session-affinity 重排 → top_k 裁剪 → salience boost**

```
query (用户最新消息)
  │
  ├── _vec_recall      (BGE-M3 embed query → messages_vec MATCH k)
  ├── _fts_recall      (FTS5 trigram MATCH ... ORDER BY rank)
  ├── _recency_recall  (ORDER BY created_at DESC LIMIT k)
  └── _salience_recall (ORDER BY salience DESC LIMIT k)
       │
       └─→ _rrf_fuse(weights = 0.5 / 0.3 / 0.15 / 0.05, RRF k=60)
              │
              └─→ _apply_session_affinity (companion ← code 项目类 ×0.15)
                     │
                     └─→ top_k → _fetch_message_meta → Hit list
                            │
                            └─→ _boost_salience (+0.05 / hit, clamp 1.0)
```

**衰减**：`daily_decay()` 启动时跑一次，`salience *= exp(-0.02 * days_since_touch)` —— 30 天大约衰减到 55%。

**Session affinity**（OpenSpec 2026-05-16-companion-context-isolation §D1）：
- 同 session → ×1.0
- companion ← code 项目类（is_summary=1 / tool_calls 非空 / 含路径或代码特征）→ ×0.15
- companion ← code 人物类 → ×0.8
- code ← 其它 code → ×0.5
- 其它 → ×1.0

### 1.3 写入链路

```
ws chat_v2 / agent_loop
  │
  ├── SessionDB.append_message(session_id, role, content, tool_calls, reasoning_content)
  │     │
  │     ├── INSERT INTO messages (...)           ← 主表
  │     ├── trigger messages_ai → messages_fts   ← FTS5 自动同步
  │     └── on_message_written(msg_id, text)     ← hook
  │            │
  │            └─→ VectorWorker.enqueue(msg_id, text)   ← 异步 batch
  │                   │
  │                   └─→ embedder.encode(texts) → INSERT messages_vec
  │                                              + UPDATE messages SET embedding
  │
  ├── FileMemory.append("memory", text, salience)  ← L1 写入（agent 主动用 memory_write 工具触发）
  │     │
  │     ├── read 当前 MEMORY.md
  │     ├── 拼上新 entry + 嵌入 {{salience=...}}
  │     ├── 若超 50KB → 按 salience 升序驱逐至 fit
  │     └── 原子写回
  │
  └── (启动时 / 手动 IPC) summarizer.summarize_old_sessions
        │
        └─→ 30 天前的 session
              │
              ├── LLM 压缩成 1-3 句
              ├── INSERT 一条 role=system, is_summary=1 的总结消息
              ├── 原文 INSERT 进 messages_archive（archived_into_id=summary_id）
              ├── DELETE 原文 from messages
              └── vector_worker.enqueue(summary_id, summary_text)
```

### 1.4 装载到 prompt（`assembler/components/memory.py`）

每个 turn `MemoryComponent` 按 `task_type` 对应的 `MemoryPolicy` 拉记忆：

```yaml
chat: { l1: snapshot, l2_top_k: 5, l3_top_k: 5 }
recall: { l1: snapshot, l2_top_k: 10, l3_top_k: 10 }
task: { ..., l2_top_k: 5, l3_top_k: 3 }
code: { ..., l2_top_k: 3, l3_top_k: 5 }
emotion: { ..., l2_top_k: 5, l3_top_k: 5 }
command: { ..., l2_top_k: 2, l3_top_k: 0 }
```

- **L1** 注入 system prompt 的 frozen bucket（cache-friendly）
- **L2** 作为真正的 message history 拼进 `bundle.history`（OpenAI message turns）
- **L3** 渲染成 markdown "相关记忆片段" 块塞进 system prompt 的 dynamic bucket

这套结构本身是合理的：L1 = 长期不变的 persona，L2 = 短期上下文，L3 = 语义召回。

### 1.5 评估 / 自检

`grep -r memory_eval recall_eval hit@` 没有命中 —— **完全没有量化召回质量的工具**。
有的只是 unit test（`test_deskpet_memory_*`），覆盖功能正确性，不覆盖"我召回的就是我想要的"。

---

## 二、痛点详细诊断

### P1 — 写入端 "all-or-nothing" 哲学

**现象**：用户和 agent 的每一条消息都进 messages 表，都触发 embedding，都进入召回池。包括：
- 跑偏的探索（"先看下目录…哦不对，换一个"）
- 工具调用的中间步骤（"调用 list_directory，得到 50 个文件"）
- 没意义的礼貌寒暄
- agent 的 inner monologue（reasoning_content）

**后果**：
- 召回噪声大：你问"上次说的那个袜子"，结果返回一堆"list_directory 调用结果"
- 向量库膨胀：8K 条对话 → 8K 个 1024 维向量 = 32MB 索引，搜索还在 RAM 里
- salience 信号被稀释：所有消息默认 0.5，没人去主动把"用户告诉我他对花生过敏"标到 1.0

**业界做法**：
- **mem0** (2024-25) 在写入端跑一个轻量 LLM 调用："这条消息有没有事实/偏好可抽取？" 没有就丢
- **LangGraph + Letta** 把"事实抽取" 跟"对话存储"分离：messages 是 raw log（轮转可丢），facts 是结构化提取
- **MemGPT** 用 self-edit：agent 自己决定哪些进"core memory"，类似 deskpet 的 MEMORY.md 但是 agent 主动维护

### P2 — L1 是死的（不自动成长）

`FileMemory.append` 存在，但什么时候调？翻代码：只在 agent 主动调 `memory_write` 工具时才进 L1。

**问题**：
- 用户在闲聊里说 "我对花生过敏" → 进 L2/L3，但 agent **不会自动**写进 USER.md
- 用户改了名字 → MEMORY.md 里旧名字还在
- agent 没有"记忆维护"的自我意识

**业界做法**：
- **Letta** 有 `core_memory_replace` / `core_memory_append` 是 agent 必备工具（不是可选）
- **mem0** 写入时自动判断："这是新事实 / 是旧事实的更新 / 是冲突 / 是无关"，分别走不同分支
- ChatGPT memory：用户每次表态自动更新（透明）

### P3 — Summarize 只压缩，不结构化

`summarizer.py` 把 30 天前的 session 压成 1-3 句 LLM 摘要，存为 `role=system, is_summary=1` 的消息。原文搬进 `messages_archive` 不再召回。

**好的地方**：压缩比合理，节省 token，原文可恢复。

**不够好**：
- 摘要是**自然语言段落**，不是结构化事实
- 多个 session 的摘要之间没有关联（同一个事实在 5 个 session 都被提了 → 5 条独立摘要）
- 没有冲突解决（"我喜欢咖啡" → 2 个月后 "我现在改喝茶了" → 召回时两条都浮出来，LLM 自己判断）

**业界做法**：
- **mem0**：写入时跑 `add_with_facts_extraction()`，提取出 `{"name": "饮品偏好", "value": "茶", "confidence": 0.8, "updated_at": ...}`，再用 LLM 跟已有 fact 做冲突解决
- **MemoryBank** (清华 2024)：分主题图谱存事实，召回时按主题 + 时间衰减
- **学术界**："cognitive architecture for memory" 趋势 — episodic（事件）/ semantic（事实）/ procedural（如何做事）分仓存储

### P4 — 召回质量没量化（拍脑袋调参）

RRF 权重 `vec 0.5 / fts 0.3 / recency 0.15 / salience 0.05` 是从哪里来的？spec 写了，但**怎么验证它就是最优的？**
- 没有人工标注的"对于这个 query，正确答案是 message_id=42" 的回测集
- 没有 hit@k / MRR 指标
- 没有 A/B 框架可以"换 BGE-M3 → Qwen3-Embedding 看召回率涨没涨"

**业界做法**：
- **RAGAS** (开源)：自动生成 query-passage 对，跑 LLM-as-judge 算召回准确率
- **BEIR**: 公开 IR 评测集，用来对比 BGE 系列 vs OpenAI embeddings
- 工程实践：每次部署新 embedding model 都跑回测，回归不能掉超过 5%

### P5 — 向量化粒度是"整条 message"

`embedder.encode(texts)` 直接喂整条 message 的 content。问题：
- 一条 2000 字的长消息 → 1 个向量 → 关键信息被稀释
- 短消息（"好的"）也是 1 个向量 → 维度被浪费
- 跨消息的关联（"上面那个文件" → 上一条说的）丢失

**业界做法**（2024-25 趋势）：
- **chunking + parent-document retrieval**：消息切 256-512 token 块，召回时返回 parent
- **ColBERTv2 / multi-vector**：一句话编多个向量（per-token），召回精度提升 30-50%
- **HyDE** (Hypothetical Document Embeddings)：让 LLM 先伪造一个理想答案再 embed
- **query rewriting**：BGE-M3 dense + bge-reranker 二阶段

### P6 — 工具记忆 / 工作记忆缺失

长任务（code mode 跑 1 小时 700 tool calls）的情境：
- 第 200 步：agent `write_file` 写了一个 600 行的 `BookDetailPage.tsx`
- 第 500 步：agent 想知道 BookDetailPage 现在长什么样
- 现在的做法：read_file 一次 600 行重新读

理论上 message 表里有第 200 步的写入参数，但：
- args 是 JSON 字符串，FTS5 trigram 索引意义不大
- 向量库里那条记录是 raw JSON，召回出来 LLM 也看不懂
- L1 文件记忆是给"用户偏好"的，不是给"我刚写过什么文件"的

**业界做法**：
- **Cline / Claude Code** 自己的做法：维护一个 `workspace_snapshot` / `files_touched_this_session` 工作内存，跟 messages 隔离
- **CodeAct / SWE-agent**: 单独存"workspace state"，每个 tool 调用前后做 diff
- **MemGPT** scratchpad：agent 可以读写一个 "thoughts" 缓冲，跟"long-term memory"分开

---

## 三、对照表：现状 vs 业界第二代

| 维度 | Deskpet 现状 | 业界第二代（mem0 / Letta / MemoryBank） | 差距 |
|---|---|---|---|
| 写入过滤 | 一视同仁全写 | LLM 判定是否有事实可抽取 | ★★★ |
| 事实结构化 | 自然语言段落 | `{type, key, value, conf, updated_at}` 结构 | ★★★ |
| 冲突合并 | 无 | new vs old 做 LLM 比对 + dedupe | ★★ |
| 召回融合 | RRF (vec/fts/recency/salience) | RRF + reranker (bge-reranker-v2-m3) | ★ |
| 切块策略 | 整条 message | 句级 / 段落级 chunk + parent retrieval | ★★ |
| 查询改写 | 直接 embed user message | HyDE / multi-query / context-aware rewrite | ★★ |
| 工作记忆 | 无 | scratchpad / workspace_state / tool_memory | ★★ |
| 衰减 | exp decay + salience boost | 同上 + 访问频次 + 类型分别衰减 | ½ |
| 评估 | 0 | hit@k / MRR / 用户 thumbs-up 回路 | ★★★ |
| Procedural | 无 | skill / workflow as memory | ★ |
| 跨 session 隔离 | ✓ session affinity 矩阵 | 同等水平甚至更弱 | 0 |
| 降级容错 | ✓ 极强 | 多数项目还不如 deskpet | -1（领先） |

总结：**deskpet 在工程稳定性上已经超过大多数 demo，但在"记忆智能化"上还停在第一代 RAG**。

---

## 四、改造路线图（不推倒重来）

围绕"保留三层骨架 + SessionDB + 渐进式引入第二代能力"。

### Phase A：评估底座（不动现网，先有度量）

**目标**：能回答"我换了什么参数 / 模型，召回质量涨了还是跌了？"

1. 新增 `backend/deskpet/memory/eval/` 子模块
2. 用 LLM 自动从 messages_archive 生成 (query, expected_msg_id) 回测集，存 jsonl
3. 跑 `python -m deskpet.memory.eval recall` 产出 hit@5 / MRR 报告
4. CI 跑一次 baseline，新 PR 召回回归 >5% 阻断
5. 加一个轻量 ws 命令 `memory_thumbs_up { msg_id, query }` 让前端在 AlertCenter / 历史面板里点反馈

**验收**：连续跑 3 个 commit，能看到指标涨跌图

预估工作量：3-5 天，纯增量，零回归风险。

### Phase B：写入端事实抽取（mem0 风格）

**目标**：把"消息"和"事实"分离，召回质量飞跃。

1. 新增 `facts` 表：
   ```sql
   CREATE TABLE facts (
     id INTEGER PRIMARY KEY,
     category TEXT,     -- 'preference' | 'profile' | 'project' | 'event'
     subject TEXT,      -- 'user' | 'pet' | <project_name>
     key TEXT,          -- 'favorite_drink' | 'birthday' | 'mcp_filesystem_timeout'
     value TEXT,
     confidence REAL,
     source_msg_id INTEGER REFERENCES messages(id),
     created_at REAL,
     updated_at REAL,
     evidence TEXT      -- 触发抽取的原句
   );
   CREATE INDEX idx_facts_subject_key ON facts(subject, key);
   ```
2. 写入流水线加一步：
   ```
   message → SessionDB.append → (异步) FactExtractor.extract
     │
     ├── LLM 1 prompt: "本条消息是否含事实/偏好？输出 JSON 或 null"
     ├── 如果非空 → 跟现有 fact 做合并：
     │     LLM 2 prompt: "新 fact + 旧 fact，输出 merged/replaced/no_op"
     └── 写入或更新 facts 表
   ```
3. 召回时新增一路 `_facts_recall` 加入 RRF（权重先给 0.2 试探）
4. L1 改造：MEMORY.md / USER.md 由 `facts` 按 `subject` 渲染生成（每 N 分钟刷新或写入后刷），用户仍可手编，但抽取层有自动更新能力

**验收**：用 Phase A 的回测集对比，hit@5 涨 ≥15%

预估：1-2 周。需要好的小 LLM（gemma3:4b 量级够用，省 token）。

### Phase C：召回精度（reranker + chunk）

**目标**：让 RRF 后的前 20 候选经过 cross-encoder rerank 再交给 LLM。

1. 引入 `BAAI/bge-reranker-v2-m3` 模型（128MB，CPU 跑 100 候选 < 200ms）
2. `Retriever.recall()` 在 RRF 之后、`top_k` 之前插一步 rerank
3. 长 message（>500 字）embed 时做 chunk：句号切分 + window=3 句滑动，每 chunk 独立 vec，召回返回 parent message + 高亮命中 chunk
4. 引入 query rewriting：在 `recall(query)` 前调用 LLM "把这个查询改写成更利于检索的形式" — 仅在 user message < 20 字时启用（短问句最缺信息）

**验收**：Phase A 指标 + 用户主观盲测（"哪个回答更切题"）≥60% 选 Phase C

预估：1 周。

### Phase D：工作记忆 / 工具记忆

**目标**：长任务里 agent 能高效复用自己刚做过的事，不再每次 read_file 重读。

1. 新增 `workspace_state` 表（per code session）：
   ```sql
   CREATE TABLE workspace_state (
     session_id TEXT,
     path TEXT,
     last_action TEXT,   -- 'read' | 'write' | 'edit' | 'delete'
     last_action_ts REAL,
     content_hash TEXT,  -- 检测文件被外部修改
     content_summary TEXT, -- LLM 生成的 "这文件是干啥的" 1-2 句
     PRIMARY KEY (session_id, path)
   );
   ```
2. 在 `file_write` / `file_edit` / `file_read` 工具后 hook 入此表
3. 在 ContextAssembler 里加一个新组件 `WorkspaceMemoryComponent`，code task 默认装载，提供 "本次 session 改过的文件 + 摘要"
4. 工具 `workspace_recall(query)` 让 agent 主动查"我之前哪个文件干过类似的事"

**验收**：长任务里 `read_file` 调用数下降 ≥30%（同样的任务对比）

预估：1 周。

### Phase E：分类衰减 + procedural memory（远期）

**目标**：模仿人类记忆的多缓冲架构。

- 不同类型 fact 不同衰减：`profile`（用户名）永不衰减；`event`（昨天聊了啥）快速衰减
- 引入 procedural memory：把"用户反复问的事 + 解决方案"提取成"flow"，下次直接命中
- 引入 reflection：每天空闲时 LLM 反思昨天的对话，写一条 "metacognitive note"（学界 cognitive arch 思路）

预估：2-4 周，纯优化非必须。

---

## 五、建议优先级

| Phase | 收益 | 风险 | 建议 |
|---|---|---|---|
| **A 评估底座** | 不上指标永远拍脑袋 | 极低（纯增量） | **先做** |
| **B 事实抽取** | 召回质量主要瓶颈 | 中（多一次 LLM 调用） | 紧随 A |
| **C reranker + chunk** | 召回精度 | 低（模型固定） | 看 B 之后还需不需要 |
| **D 工作记忆** | code mode 体感最大 | 中（新表 + 工具 hook） | 跟 B 并行可独立做 |
| **E procedural** | 桌宠"个性化"体感 | 高（设计未稳） | 留到 V2 |

---

## 六、Quick Wins（一天能落地的 3 件事）

如果不想等 Phase A 那么大，先做这三件，能立刻看见效果：

1. **降低噪声入库**：在 `SessionDB.append_message` 前加一道"是不是值得留的对话"过滤
   - 简单规则：`role=tool` 且 `content` 是大段 JSON → salience=0.1（依然存，但召回基本不会浮）
   - 用户消息长度 ≤3 字符（"嗯"、"好"、"哈"）→ salience=0.2
2. **L1 写入自动化（最小版）**：每次 `chat_v2_final` 之后跑一个轻量 prompt "这一轮对话里用户透露了什么稳定事实？" → 真有就 `FileMemory.append("user", ...)`
3. **召回时去重 + 长度截断**：`_render_l3_only` 里发现 score 几乎相同的几条（>0.95 余弦相似），保留一条；过长 message 显示前 240 字（已有）+ "(还有 N 字)" 提示

这 3 个动起来就能感觉到记忆"变聪明了"，且不依赖任何新模型。

---

## 七、参考资料

- **mem0** — github.com/mem0ai/mem0（2024 起，事实抽取范式开源标杆）
- **Letta** (前 MemGPT) — github.com/letta-ai/letta（agent self-edit memory）
- **MemoryBank** — Zhong et al., AAAI 2024（主题图谱 + 时间衰减）
- **bge-reranker-v2-m3** — BAAI, 2024（多语 cross-encoder reranker, ~127MB）
- **RAGAS** — github.com/explodinggradients/ragas（自动 RAG 评估）
- **Cognitive Architectures for Language Agents** — Sumers et al., 2023（episodic/semantic/procedural 分仓理论）
- 你已有的 OpenSpec：`2026-05-16-companion-context-isolation`（session affinity 设计很扎实，本报告 §1.2 引用）

---

## 八、给当前开发者的判断

- **deskpet 的记忆系统不是"做得差"，而是"做完了第一代，第二代还没开始"**
- 第一代（hermes-style 三层 + RRF）的工程质量在桌宠 / 个人 agent 领域是 top quartile
- 第二代（mem0 / Letta 风格的"提取+合并+评估"）是 2024 才大规模工业化的，deskpet 还没走到
- **不需要重写**：所有改造都可以在现有 SessionDB / MemoryManager / Retriever 上加 hook、加表、加 component，老逻辑保留作为 fallback（Strangler-Fig 模式，跟你们 OpenSpec D1 一样的演进哲学）
