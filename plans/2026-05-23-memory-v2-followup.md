# memory-v2 升级 — 后续任务清单（followup）

**关联**: [worktree-memory-upgrade 分支 + PR #2](https://github.com/DennyWanye/deskpet/pull/2)
（Stage 0 + Stage 1 全部 8 个 WI 已交付）

本文件列出本轮交付**不在范围内、需要后续独立设计/落地**的项目。每一项都
注明根因、所属阶段、建议落地路径。

---

## A. 设计层 — Stage 2 范畴（PRD §4.3 明确另出详细设计）

### A1. 跨 key 矛盾 / memory staleness 治理（PRD §4.3 S2.3）

**现象**（人工测试 MR-1-4 实测）：
- 用户先说"我对花生过敏" → fact `peanut_allergy = "对花生过敏"` 落库
- 用户接着说"其实我不过敏花生，是过敏海鲜" → LLM 抽成新 key
  `seafood_allergy = "海鲜过敏"`，旧的 `peanut_allergy` **没被关掉**
- 结果两条 active facts 矛盾并存

**根因**：当前 `FactExtractor._persist_extracted` 只在 `(subject, key)` 完全
一致时触发 merge LLM；跨 key 的逻辑矛盾完全盲区。

**Stage 2 落地方向**：
- facts 表加 `superseded_by INTEGER REFERENCES facts(id)` 列
- 新事实写入前，让 merge LLM 多看一眼同 subject 下所有相关 active facts，
  判断"我跟谁矛盾？" → 把被推翻的标 inactive + 写 `superseded_by`
- 或：低频反思任务额外跑一次"同 subject 矛盾扫描"
- 配套：新增 `memory_forget(fact_id_or_pattern)` 工具/ws 命令让用户手动删

**复杂度**：中。需要新 LLM prompt 设计 + eval 集挂"矛盾检出率"指标。

---

### A2. entity-matching 检索路（PRD §4.3 S2.1）

**现状**：召回三路（vec + FTS + recency + salience）经 RRF 融合，**没有
按"实体"建独立索引**。问 "旺财怎么样了" 时无法直接靠 entity 索引秒命中
"宠物名=旺财" 的 fact。

**评审建议**：复用 facts 表的 `subject`/`key` 作为天然实体索引（每条 fact
本就是结构化 entity-attribute-value）。不另建表。

**Stage 2 落地方向**：
- `EnhancedRetriever` 新增 entity 路：query 经 NER 提取实体 →
  `facts.find_by_subject_or_key_substring(entities)` → 高 weight 进 RRF
- entity NER 用小模型 / 规则 / LLM 三档退化

---

### A3. token 效率双门控（PRD §4.3 S2.2）

**现状**：eval 门控只看 `hit@5` 不回归 + `token_per_query` 单边不超 +30%。

**Stage 2 落地方向**：召回策略变更须**同时**满足：
- 准确率 ↑（hit@5 提升或不降）
- token/query 不爆（≤ baseline × 1.3）

把这条规则做成 `scripts/eval_gate.py` 内的复合 gate，回归任一项即 FAIL。

---

### A4. episodic → semantic 显式固化通路（PRD §4.3 S2.4）

**现状**：现有的 `summarizer`（老对话 → 摘要）和 `FactExtractor`（消息 →
facts）是两个独立通道，没有 pipeline 关系。

**Stage 2 落地方向**：summarizer 出的 session 级摘要 → FactExtractor 二次
抽取 → 显式标 `category='episodic_summary'` 落 facts。把"短期对话 → 长期
记忆"做成一条声明式 pipeline。

---

## B. 实施层 — 环境/工具受限项

### B1. MR-4 工作记忆完整 code-mode 联调

**现状**：人工测试在 store 层（`record_action` + `workspace_recall` 工具
+ `WorkspaceMemoryComponent` 进 code policy）已完整验证；但**完整 code-mode
agent 联调**（用户进 code mode → agent 自动调 `file_write` → 工作记忆自动
落库 → 下一轮 agent 自动从工作记忆决策）需要 Tauri GUI 才能驱动。

**worktree 环境约束**：worktree 缺 `backend/.venv`，Tauri dev 模式 spawn
后端会找不到 python，GUI 跑不起来。

**后续落地**：在主 checkout（有 venv）跑 `tauri dev` 进 code mode 真实
驱动一遍 agent loop，确认：
- agent 调用 `file_write` 后 `workspace_state` 表自动落行
- 同任务下重复任务，`read_file` 调用数对比 flag off 时是否下降
- `WorkspaceMemoryComponent` 注入的 prompt 段是否真被 LLM 利用

### B2. 中转账号上 `gpt-5.5` / `claude-haiku-4-5` / `gpt-4o-mini` 503

**现状**：本轮 LLM 测试用的中转账号（<dev-test@example.com>）跑这三个模型
全 503 Service Unavailable，只有 `deepseek-chat` 通。

**判断**：账号级配额/模型授权问题，**不是 DeskPet 代码缺陷**。

**后续**：用 ConsolePage（https://chinzy.com/console/billing）查这个账号
对那些模型的访问权限 / 充值。或换主账号跑测试。

### B3. PR #2 合入 master 的冲突解决

**现状**：worktree-memory-upgrade 分支已 push，PR #2 已开。

**冲突**：master 的提交 `fa6a286 feat(ui): 全应用 UI 重设计` 改过
`tauri-app/src/components/MemoryPanel.tsx`，而本 PR 也改了同一文件（加
👍/👎 反馈按钮）。

**合并策略**：手动解 `MemoryPanel.tsx` —— 保留 master 的新视觉风格 +
本 PR 的 thumbs UI（在 turns view 的 row 里）+ `feedbackGiven` 状态 +
`memory_thumbs_up_response` 消息分发。约 30-50 行人工 merge。

### B4. eval baseline 用 mock embedder 数值偏低

**现状**：`zh_baseline.json` 的 hit@5=0.1143（mock embedder + 中文 FTS
trigram tokenizer 局限）。能做回归 gate 但绝对值低。

**后续**：装 BGE-M3 后跑 `python -m scripts.eval_gate --update-baseline`
重钉 baseline。新基线下 hit@5 应显著提升，eval gate 仍工作。

---

## C. 远期 — Stage 3 范畴（PRD §4.4）

- **procedural memory 深化 / RL 优化**：当前 `SkillMemoryStore` 只做了
  CRUD 接入。真正自动从对话提取"反复出现的问题→解法"模式 + 主动建议，
  需要单独 R&D。
- **LoCoMo 风格中文回测集**：长程对话基准。本轮 fixture 35 条够回归，
  不够刷榜。
- **embedding 模型升级评估**：跑过 BGE-M3 / Qwen3-embedding / m3e 之类
  的对比，选最优。
- **图数据库 entity 索引**：本轮明确不引入（PRD §2.2 非目标），后期如
  facts 表突破百万级再考虑。

---

## 优先级建议

| 项 | 建议优先级 | 工作量 |
|---|---|---|
| B3 PR #2 解冲突合 master | **P0**（解锁所有后续工作）| 小（半天） |
| B1 MR-4 code-mode 完整联调 | P1（验证已交付能力）| 小（半天） |
| B4 装 BGE-M3 + 重跑 eval baseline | P1 | 小（1h，主要等下载）|
| A1 跨 key 矛盾治理（S2.3）| P1（用户最容易感知到的缺陷）| 中（2-3 天）|
| A4 episodic→semantic 通路（S2.4）| P2 | 中 |
| A3 token 效率双门控（S2.2）| P2 | 小 |
| A2 entity-matching（S2.1）| P3 | 中 |
| B2 中转账号 503 排查 | P3（账号级，不影响代码）| 外部 |
| C.* Stage 3 远期 | P3+ | 大 |
