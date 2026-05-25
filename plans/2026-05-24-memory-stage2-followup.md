# memory-v2 Stage 2 — 后续任务清单（followup）

**关联**: [PR #4 / origin/master](https://github.com/DennyWanye/deskpet/tree/master) Stage 2 已全部 ship
**关联交付**: commit chain `9fe628d` → `8ab1b76` → `28a93a7` → `3b1415d` → `4ca596c`
**关联文档**: [`plans/2026-05-23-memory-system-stage2/`](./2026-05-23-memory-system-stage2/) PRD/TDD/round 1-3 测试

Stage 2 PRD 验收已全 ✅、功能 bug=0，但 round 3 真测试时发现 2 个**不阻塞 ship 但应尽快修**的历史/量化问题。本文件列出两项作为独立 followup 处理。

---

## F1. assembler workspace fanout 1500ms timeout

### 现象

Stage 2 round 3 真 GUI 测试（windows-mcp 操作 Tauri Code Mode）发现：
- ✅ workspace_state 表真填充（Stage 2 round 3 fix 后）
- ✅ workspace_recall 工具可被 agent 调用
- ❌ **assembler fanout 1500ms timeout**：backend log 出现：
  ```
  WARNING:deskpet.agent.assembler.registry:event='assembler.fanout_timed_out'
    timeout_ms=1500.0 pending=['memory', 'persona', 'tool', 'time', 'workspace']
  ```
- 意味着 agent prompt 被组装时 workspace 组件 1.5s 内没返回 →
  **工作记忆段没真插入 agent 的 system prompt**，agent 第二轮被迫再 read_file（即使 workspace_state 表里已有该 README 的 summary）

### 根因（推测）

`backend/deskpet/agent/assembler/` 用并行 fanout 收集 N 个组件的 context segment，
1500ms 是全局 timeout。`workspace` 组件大概率在 `WorkspaceMemoryStore.get_recent_actions`
+ embedder 算相似度的链路上耗时超 1.5s（特别是 BGE-M3 真模型加载后第一次 embed 触发懒加载）。

**这是 Stage 1 性能问题，不是 Stage 2 引入** —— 但因为 Stage 2 真接通了
workspace 通路（fix bug #4），这个 timeout 真切影响 workspace_memory 收益。

### 排查方向

1. **先确认 root cause**（不要盲修 timeout）：
   - 给 assembler 各组件加 per-component 耗时 log（`fanout_component_done duration_ms=...`）
   - 用 round 3 同样的 code mode chat 跑一遍，看 workspace 组件具体卡哪一步
2. **可能原因 + 修复方向**：
   - a) embedder warmup 没完成 → workspace `_recall` 首次触发 model load 走 worker → 慢
     - 修：assembler startup 时主动 trigger 一次 embedder.encode("warmup") 强制 load
   - b) workspace store SQL 查询慢（无索引 / 表大）
     - 修：检查 `workspace_state` 索引 + 加 `LIMIT 20` 上限
   - c) 1500ms 本身就不够（fanout 是 LLM 调用前的 critical path）
     - 修：调到 3000ms（兼顾首 token 延迟 vs workspace 准确率）
3. **降级**：fanout timeout 时不应该静默丢 component，应该 fallback 到
   "只用文本概要而不用语义召回"（degraded mode），保证 prompt 至少有部分工作记忆段

### 验收标准

- backend log 不再出现 `fanout_timed_out pending=['workspace']`
  （或出现时降级到 degraded mode 而不是丢弃）
- 重跑 round 3 MR-S2-6 两轮 chat：第二轮 agent 用工作记忆段直接答（**不再** read_file 或最多 read_file with limit），workspace_state 1 row → recall 命中 → prompt 含 fact
- 新增 metric：`workspace_recall_hit_rate` 在 code mode 长跑 10 轮后 ≥ 60%

### 工作量预估

- 排查 + 加 per-component log: 0.5 天
- 修主要原因 (embedder warmup / SQL / timeout 调整): 0.5-1 天
- 降级 mode + 回归: 0.5 天
- **合计 1.5-2 天单人**

---

## F2. eval_gate fixture 接入 Stage 2 召回路径

### 现象

Stage 2 引入 4 个新召回相关能力：
- `cross_key_merge` 让 facts 表更干净（减少矛盾噪声）
- `entity_path` 让实体类 query 额外召回路
- `episodic_to_semantic` 让长程 summary 内容被抽成 fact 进召回
- `chunking` / 既有 enhanced_retriever

**但 `eval_gate.py` 跑的是裸 Retriever**，**不**经过 `EnhancedRetriever`，
更不带 Stage 2 任何 flag。所以：
- MR-S2-5-3 "Stage 2 全 flag 开 + `--strict` PASS" 验收**当前无法跑** —
  flag on/off eval 出来一样的 hit@5=0.4286
- Stage 2 "提升召回质量"的核心卖点**没有量化证据**
- strict gate 在召回相关 PR 被 CI 自动触发，但所有 Stage 2 类 PR 都会 FAIL
  （hit@5 持平 baseline 不 strict > +0.02 容差），变成"形同虚设"

### 当前状态详情

[`backend/scripts/eval_gate.py:62-92`](../backend/scripts/eval_gate.py#L62):
```python
async def run_eval(*, top_k: int = 20) -> dict:
    ...
    retriever = Retriever(session_db=sdb, embedder=embedder)  # 裸 Retriever
    runner = MetricsRunner(db_path, retriever, ...)
    report = await runner.run(top_k=top_k)
```

baseline (`backend/deskpet/memory/eval/zh_baseline.json`):
```json
{"hit@5": 0.4286, "token_per_query": 195.86}
```

### 修复方向

1. **eval_gate 加 `--stage` 参数**:
   - `--stage=stage1`（默认）：裸 Retriever，跟当前 baseline 比，向后兼容
   - `--stage=stage2`：用 `build_recall_retriever()` 构造完整 EnhancedRetriever + 注入 facts_store + reranker + chunk_store + entity_extractor + 4 flag 全开；跟独立的 `zh_baseline_stage2.json` 比
2. **新建 `zh_baseline_stage2.json`** + 跑一次 `--stage=stage2 --update-baseline` 钉死
3. **fixture 增强**:
   - 现有 35 条 fixture 几乎全是普通 message 召回，对 cross_key / entity / episodic
     新路径的覆盖差
   - 新增 ~15 条："对花生过敏" + "其实是海鲜" 矛盾对 / "旺财怎么样了" entity / summary 抽出的 episodic_summary 召回 — 让 hit@5 真能在 Stage 2 路径上显著高于 stage1
4. **CI `eval_gate_ci.sh` 改造**:
   - 现在召回类 PR 自动加 `--strict`；改成自动加 `--strict --stage=stage2`
5. **验收 hit@5 提升目标**:
   - stage2 baseline 应该 ≥ stage1 baseline + 0.10（绝对值）才算 Stage 2 真有收益
   - 如果 fixture 跑出来差不多 → 说明 Stage 2 召回提升其实没那么大，
     诚实更新 PRD §2.3 成功度量；如果显著高 → ship Stage 2 的卖点终于有数

### 验收标准

- `python -m scripts.eval_gate --stage=stage1` 与现状一致（向后兼容）
- `python -m scripts.eval_gate --stage=stage2` 跑出 hit@5 > stage1 baseline + 0.10
- `python -m scripts.eval_gate --stage=stage2 --strict` PASS（在 stage2 baseline 上严格大于自己）
- `eval_gate_ci.sh` 召回类 PR 自动用 `--stage=stage2 --strict`

### 工作量预估

- eval_gate.py 加 stage 参数 + EnhancedRetriever 构造: 0.5-1 天
- 15 条新 fixture 设计 + 钉 baseline: 0.5 天
- ci.sh + workflow 改造 + 文档更新: 0.5 天
- **合计 1.5-2 天单人**

---

## 优先级建议

| Followup | 优先级 | 阻塞性 | 原因 |
|---|---|---|---|
| **F1 fanout timeout** | 🔴 高 | 影响 workspace_memory 实际收益 → code mode 用户体感差 | round 3 已有 evidence 复现，越早修 workspace_memory 越早真正生效 |
| **F2 eval_gate stage2** | 🟡 中 | 不影响功能但 Stage 2 卖点无量化证据 | 没量化数据 Stage 3 决策没基线；strict CI 形同虚设需早做 |

**建议先做 F1**（用户体感直接影响），F2 可以攒到 Stage 3 启动时一起做（顺便钉 Stage 3 baseline）。

---

## 不在本文件范围

下列项已知但属于 Stage 3 远期或不阻塞 ship：
- forgotten_at 长期堆积 GC（90 天清理脚本）— Stage 3
- 多 session 并发 SQLite 锁压测 — Stage 3
- cross_key prompt 超 token limit 降级 — 实际 fact 数到 200+ 才会触发，先观察
- 跨平台（Linux/macOS）— PRD scope 是 Windows，OK
- procedural memory RL 优化 / LoCoMo 中文回测集 / embedding 模型升级 — Stage 3 PRD §8 已列
