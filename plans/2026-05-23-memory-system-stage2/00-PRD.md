# PRD — DeskPet 记忆系统 Stage 2（跨 key 矛盾 + entity 索引 + 双门控严格化 + 固化通路）

**创建日期**: 2026-05-23
**作者**: 架构设计（20Y）
**状态**: **第 2 版** —— 已过架构评审（round1 / opus 4.7），按评审意见修订
**关联**:
- 上游路线：`plans/2026-05-22-memory-system-upgrade-plan.md`（Stage 0+1 已交付）
- 上游 PRD：`plans/2026-05-22-memory-system-upgrade/00-PRD.md` §4.3 Stage 2 框架
- 收尾清单：`plans/2026-05-23-memory-v2-followup.md`（剩余 7 项）
- 任务现状：`plans/2026-05-23-memory-system-status.md`
- 架构评审 r1：`03-architect-review-round1.md`（本文件夹）

**工作分支**: 建议新开 `worktree-memory-stage2`（隔离 worktree，端口 8201/5274 避开 stage1 的 8200/5273）

> ## 第 2 版修订说明（架构评审 round1 后）
> 第 1 版有 4 处事实性误判 + 5 项设计风险 + 3 项漏风险 + 工作量低估 30-40%，本版逐条已改正：
> 1. **E1/E2**：工具注册改为模块顶层 `registry.register(...)` 调用 + `bind()` 模式注入依赖（registry 无 `@register_tool` 装饰器、无 `_ctx` 注入）
> 2. **E3**：ws 路由改为扩 `p4_ipc.py` 路由表（不是不存在的 `ws_handlers.py`）
> 3. **E4**：MemoryPanel 无 facts view → WI-S2.1 拆为 **WI-S2.1a 后端**（含 cross-key + memory_forget 工具 + cleanup 脚本）+ **WI-S2.1b UI**（含 facts view + 🗑 + undo）
> 4. **E5**：`'episodic_summary'` 必须加进 `VALID_CATEGORIES` + `_CATEGORY_DECAY`
> 5. **D-RISK-1**：cross-key 视野改"最近 20 条 ∪ embedder 召回 top 10"
> 6. **D-RISK-2**：entity LIKE 只查 value 列；regex 加停用词集；entity_weight 0.15 → 0.1
> 7. **D-RISK-3**：`--strict` 改 CI 自动化触发（git diff 看 `*_retriever.py / *_extractor.py` 改动），不靠 PR template
> 8. **D-RISK-4**：`asyncio.create_task` 改 `background_tasks: set` + `add_done_callback` + shutdown gather
> 9. **D-RISK-5**：`memory_forget` 加 `permission_category="write_file"` + `dangerous=True`；自然语言模式默认禁用
> 10. **风险登记补 R-MISS-1/2/3**；R3/R8/R9 缓解强化
> 11. **工作量**：M1 4-5 天 → 6-7 天；M5 1 天 → 1.5-2 天；总 ~14 天单人 / 8-9 天三路并行

---

## 0. 一句话

Stage 1 把"已写未接入"的 memory-v2 真激活了；Stage 2 解决 Stage 1 在真实使用中**暴露的设计缺陷与质量天花板** —— 跨 key 矛盾治理（A1）、entity 索引（A2）、严格双门控（A3）、episodic→semantic 固化（A4） + 收尾一项 GUI 联调（B1）。**不做** Stage 3 远期 + 中转账号问题。

---

## 1. 背景与问题定义

### 1.1 Stage 1 暴露的真实问题（来自第 4 轮人工测试 + followup）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1 | **跨 key 矛盾盲区** | MR-1-4 实测："对花生过敏" → fact `peanut_allergy`；后说"其实过敏海鲜" → 新 key `seafood_allergy`。两条 active fact 并存，旧的 stale | 用户最易感知的脏数据；召回时"避花生 / 提花生"矛盾建议 |
| P2 | **entity 索引缺位** | 召回三路（vec + FTS + recency + salience）经 RRF，**没有按"实体"建独立索引**。问"旺财怎么样了"无法靠 entity 索引秒命中 fact `pet_name=旺财` | facts 表是天然实体-属性-值结构却没被利用 |
| P3 | **eval 门控强度不够** | 现 gate（`eval_gate.py:93-110`）：hit@5 不回归（容差 0.02） + token 增幅 ≤ 30%。**更新 baseline 时无约束** —— 可以把 baseline 钉低 | 召回质量随时间漂移而不被察觉 |
| P4 | **episodic↔semantic 隔离** | `summarizer.py` 把老对话压成 summary message 入 `messages` 表（role=system, is_summary=1），但**这些 summary 不会被 FactExtractor 二次抽取** | summarizer 把"我喜欢...""我决定..."压进 summary 后，事实层丢失；长期记忆衰减加速 |
| P5 | **MR-4 GUI 联调缺位** | store 层 + workspace_recall 工具已验证，但**完整 code-mode agent loop 端到端**（agent 调 file_write → 工作记忆落库 → 下一轮 agent 从工作记忆决策）未真机验证 | Stage 1 DoD 严格意义上不完整 |

### 1.2 当前代码现状（事实核对，v2 已交叉验证）

#### 1.2.1 facts 表 schema（`memory_v2_schema.py:76-94`）

```sql
CREATE TABLE IF NOT EXISTS facts (
    id, category, subject, key, value, confidence, source_msg_id,
    created_at, updated_at, evidence,
    is_active INTEGER NOT NULL DEFAULT 1,
    decay_rate, last_recalled, embedding BLOB
);
```

**没有 `superseded_by` 列、没有 `forgotten_at` 列**。Stage 2 必须加（D1）。

#### 1.2.2 schema 演进约束（`memory_v2_schema.py:1-19`）

> "The 008 migration is the last one tied to a `PRAGMA user_version` bump that backend tests pin to ``16``. Adding a 009 file changes the bump and forces edits to several hardcoded test assertions. Instead, we apply our additive tables at runtime via ``CREATE TABLE IF NOT EXISTS`` — idempotent."

但 `CREATE TABLE IF NOT EXISTS` **不能加列**。Stage 2 必须用 `PRAGMA table_info()` 检测列 → `ALTER TABLE ... ADD COLUMN` 兼容老库（详见 §3 D1）。

#### 1.2.3 跨 key 矛盾的代码盲点（`facts.py:531-598`）

```python
async def _persist_extracted(self, extracted, message_id):
    for fact in extracted:
        existing = await self._store.find_active(
            subject=fact.subject, key=fact.key   # ← 仅 same (subject, key)
        )
        if existing is None:
            await self._store.upsert(...)        # ← 跨 key 矛盾在此处不被发现
        else:
            decision = await self._decide_merge(fact, existing)
```

**`find_active` 只查同 `(subject, key)`** → 新 key 的事实永远走"insert 路径"，不进 merge LLM。A1 必须扩到 subject-level 矛盾扫描。

#### 1.2.4 FactExtractor.process_message 现签名（`facts.py:502-516`）

```python
async def process_message(
    self, *, message_id: int, content: str, role: str,
) -> list[dict]:
    if role not in ("user", "assistant"):   # ← role 白名单不含 system
        return []
    if not content or len(content.strip()) < self._min_chars:
        return []
    ...
```

A4 需扩 `source` 参数 + role/source 联合白名单。

#### 1.2.5 fact category 白名单（`facts.py:60`）

```python
VALID_CATEGORIES = {"profile", "preference", "project", "event", "reflection"}
```

**`'episodic_summary'` 不在白名单** —— 加 A4 时必须同步加进白名单 + `_CATEGORY_DECAY`（E5）。

#### 1.2.6 eval_gate 现状（`scripts/eval_gate.py:93-110`）

当前已是 AND（任一 fail 即整体 fail）+ baseline sanity 缺失：
```python
def _gate(current, baseline):
    failures = []
    if cur_hit5 < base_hit5 - _HIT_TOLERANCE:  # hit 不回归
        failures.append(...)
    if base_tok > 0 and cur_tok > base_tok * _TOKEN_GROWTH_MAX:
        failures.append(...)
    return (not failures), failures
```

A3 缺口在 ① `--update-baseline` 无 sanity，② 无 strict 模式（hit@5 严格大于）。**v2 修订**：strict 触发必须 CI 自动化（git diff），不靠 PR template。

#### 1.2.7 summarizer 输出（`summarizer.py:91-187,316-428`）

`summarize_old_sessions()` 落 `messages` 表（role=system, is_summary=1, summary_of=[原 id 列表 JSON]）。**没有任何下游消费 summary message 提取 facts**。事务边界在 `await conn.commit()` 处，之后 `await vector_worker.enqueue(...)`。A4 在 enqueue 同位置接入。

#### 1.2.8 EnhancedRetriever plugin 接口（`enhanced_retriever.py:78-103`）

```python
def __init__(self, base,
    *, facts_store=None, facts_weight=0.0,
    reranker=None, query_rewriter=None,
    embedder=None, chunk_store=None,
):
```

已有 6 个 plugin 槽位。A2 新增 `entity_extractor` + `entity_weight` 第 7、8 槽。

#### 1.2.9 工具注册机制（`tools/__init__.py:57-89` + `tools/registry.py:223-275`）

- 没有 `@register_tool` 装饰器
- 自动发现：`pkgutil.iter_modules` 走 `tools/*.py` 子模块 → `importlib.import_module` 触发模块**顶层**的 `registry.register(name, toolset, schema, handler, ...)` 调用
- handler 签名是 `Callable[[dict, str], str]`（args dict + task_id），无 `_ctx` 注入

A1 配套的 `memory_forget` 工具必须用 **module-level setter `bind(facts_store, embedder, llm_call)`**（main.py lifespan 调）+ 顶层 `registry.register` 注册。

#### 1.2.10 ws 路由位置

- `backend/main.py:2044-2264` elif 链分发：`memory_list / memory_delete / memory_clear / memory_export / memory_thumbs_up`
- `backend/p4_ipc.py:5-71` 路由表分发：`memory_search / memory_l1_list / memory_l1_delete / embedder_status`

**v2 决策**：新 `memory_facts_list` / `memory_forget` / `memory_forget_undo` 加进 `p4_ipc.py` 的路由表（更结构化）。

#### 1.2.11 MemoryPanel 现有 view（`tauri-app/src/components/MemoryPanel.tsx:48-83`）

4 个 view：`turns | l1 | search | skills`。**没有 facts view** —— WI-S2.1b 必须新加。

---

## 2. 目标与非目标

### 2.1 目标（G）

- **G1 跨 key 矛盾治理**：facts 表加 `superseded_by` + `forgotten_at`；merge 决策扩展到 subject-level（最近 20 条 ∪ embedder 召回 top 10 进 LLM 视野，去重）；冲突时旧 fact 标 inactive + 写 `superseded_by` 指向新 fact ID
- **G2 显式遗忘能力**：新增 `memory_forget(query | fact_id)` 工具（dangerous=true，UI 确认）+ ws 命令 + MemoryPanel facts view（新增 5th view）+ 🗑 按钮 + 5 秒 undo（op_id 标识）
- **G3 entity 索引**：复用 `facts.value` 作天然实体索引（不查 subject/key 避免噪声）；query 经 NER 提取实体 → entity 路 hits 进 RRF（weight 0.1）；regex 加停用词
- **G4 eval 门控严格化**：`--update-baseline` 时 sanity 检查（hit@5 不能往下钉、token 不能往上钉）；新增 `--strict` 模式 + **CI 自动化触发**（git diff 看 `*_retriever.py / *_extractor.py` 改动）
- **G5 episodic→semantic 固化通路**：`summarizer.summarize_old_sessions()` 完成后用 `background_tasks` set 收集 task 异步抽取 summary 文本，落 `category='episodic_summary'`（同时加进 VALID_CATEGORIES 白名单 + decay 表）
- **G6 收尾 B1**：主 checkout 完整跑 MR-4 GUI 端到端联调，固化为可重放脚本

### 2.2 非目标（NG，明确不做）

- **NG1 procedural memory RL 优化**（Stage 3 远期）
- **NG2 LoCoMo 风格中文回测集**（Stage 3 远期）
- **NG3 embedding 模型升级评估**（Stage 3 远期）
- **NG4 引入图数据库**（Stage 1 PRD §2.2 已锁定，永久）
- **NG5 重写第一代三层 RRF**（永久 fallback）
- **NG6 修复中转账号 503**（账号级，非代码；followup B2）
- **NG7 修改 `messages` 表 schema 加 `is_archived`**（summarizer 已用 is_summary=1）
- **NG8 把 reflection 与 summarizer 合并**（reflection 是反思元认知，summarizer 是事实压缩，保持独立 pipeline；A4 只在 summarizer 出口加 fact 抽取 hook）

### 2.3 成功度量

| 指标 | 目标 |
|---|---|
| 跨 key 矛盾检出率（eval 新增） | 在新增的"矛盾对" eval 集上 ≥ 70%（5 对中 ≥ 4 对被正确标 superseded） |
| 跨 key 误判率（非矛盾被错标） | ≤ 15%（30 次新写入抽样统计；v1 是 N=10 ≤ 20%，评审 B2 强化）|
| memory_forget 准确率 | 按 ID 删 100%；按自然语言 query 删 ≥ 90% 命中（自然语言→fact_id 匹配） |
| entity 路对实体类 query 的 hit@5 | 新增 entity-targeted 子集（10 条），hit@5 ≥ 0.65（v1 是 0.70，因 LIKE 只查 value 列后召回略降，保守目标） |
| 双门控严格化 | `--update-baseline` 携带 sanity 检查；strict 模式 CI 自动触发 |
| episodic→semantic 端到端 | summarizer 跑后 `facts` 表 category='episodic_summary' 行数 ≥ summary 数 × 0.5 |
| MR-4 GUI 联调 | 主 checkout 录屏 + workspace_state 表 dump 作为证据归档 |
| 第一代回归 | flag 全关时 backend pytest 0 回归（含老三层 + Stage 1 用例 1799+ 全绿）|

---

## 3. 关键架构决策（v2，动工前定稿）

| # | 决策点 | 决定 | 理由 |
|---|---|---|---|
| **D1** | facts 表加列的演进方式 | 新增 `backend/deskpet/memory/schema_v2_migrator.py`：`ensure_memory_v2_columns(db_path)` 用 `PRAGMA table_info(facts)` 检测；缺 `superseded_by` 则 `ALTER TABLE facts ADD COLUMN superseded_by INTEGER REFERENCES facts(id)`；缺 `forgotten_at` 则 `ALTER TABLE facts ADD COLUMN forgotten_at REAL`。在 `ensure_memory_v2_tables()` 结尾调用一次。**不引入 migration 文件，不 bump `user_version`**；**ALTER 失败时强制把 `cross_key_merge` 和 `memory_forget` flag 关掉**（R8 v2 加固）| 沿用 Strangler-Fig；幂等检测；兼容 Stage 1 库；ALTER 失败不让代码读不存在的列 |
| **D2** | 跨 key 矛盾检测的接入点 | 在 `FactExtractor._persist_extracted` 中，**新 fact 插入前**额外构造候选集（D3），喂给升级版 merge LLM。LLM 返回 `{conflicts: [{old_id, reason}], should_insert}` → 串行化执行（沿用 `_persist_lock`） | LLM 单次调用即可看 cross-key 矛盾；复用已有锁；不另建 worker |
| **D3 ★v2** | 跨 key 矛盾的候选集构造 | **混合视野**：① `list_active(subject, limit=20)` 取**最近 20 条**；② `vector_search(embedder.embed(new_fact.value), limit=10)` 取**语义最近 10 条**（限 same subject）；③ 合并去重，限 25 条上限 | v1 只看"最近 20 条" → MR-S2-1-6 反向验证时旧 fact 已掉出窗口必漏判；混合视野解决（评审 D-RISK-1）|
| **D4** | 跨 key 矛盾的 prompt 设计 | 三段式：① 新 fact ② 候选 N 条 active facts（带 `id/key/value/updated_at`，不放 evidence） ③ 输出 JSON `{conflicts, should_insert}`。**含"超过 25 条则只取最近 + 语义合并"的硬上限** | prompt 长度可控；过载交给 cleanup 脚本 |
| **D5 ★v2** | `memory_forget` 工具的口径 | 两种调用模式：① `memory_forget(fact_id=N)` 精确删（默认开放）② `memory_forget(query="...")` 自然语言删（**默认禁用**，需 `[memory.v2.memory_forget] enable_natural_language = true` 才开启）。**工具自身规则拦截**：query < 6 字 / 召回 fact 数 > 5 时强制 status=skipped；删的不是 `is_active=0` 而是写 `forgotten_at=now()` + `is_active=0`，可恢复 5 秒 undo | v1 只靠 LLM 二次确认 = 提示注入攻击面（D-RISK-5）；工具自身规则才不可绕过 |
| **D6 ★v2** | `memory_forget` 注册位置 | 新工具放 `backend/deskpet/tools/memory_tools.py`（**新文件**）；**模块顶层 `registry.register("memory_forget", "memory", _SCHEMA, _handler, permission_category="write_file", dangerous=True)`**（registry 无装饰器、无 `_ctx` 注入）；`memory_tools.bind(facts_store, embedder, llm_call)` 模块级 setter 在 main.py lifespan 调一次注入实例 | E1+E2+R-MISS-1：discovery 在 import 时跑，必须 lazy bind 避免 import 时拿不到 store；dangerous=True 让 registry 触发 UI 确认 |
| **D7 ★v2** | entity NER 选型 | **三档降级**：① `LLMEntityExtractor` 用轻量 LLM 抽实体 ② `RegexEntityExtractor` 降级（**加停用词集**：`{"我的", "我们", "今天", "这个", "那个", "什么", "怎么", "了吗", "的人", ...}`，正则匹配后过滤）③ `NoopEntityExtractor` 返回 `[]` | 沿用 Stage 1 模式；regex 停用词解决"了吗""这个"被当 entity 的噪声 |
| **D8 ★v2** | entity 路在 RRF 中的权重 | `[memory.v2.facts] entity_weight = 0.10` 默认（v1 是 0.15）| v1 偏激进；先保守 0.10 起步，A/B 跑 eval 后调；LIKE 噪声仍可能影响，权重低些更安全 |
| **D9 ★v2** | entity 路的实现位置 | `EnhancedRetriever.__init__` 新增 `entity_extractor` + `entity_weight` 参数；新增 `_collect_entity_hits(query)`：抽实体 → `facts_store.find_by_entities(entities, limit=10)` → 转 Hit。**`facts_store.find_by_entities` 实现：LIKE 只查 `value` 列**（不查 subject="user" 噪声大、不查 key=英文标识符）；entity 词长度 < 2 字跳过 | E4-1.2 D-RISK-2：subject/key LIKE 是噪声源；只查 value 命中率更精准 |
| **D10 ★v2** | eval_gate strict 模式触发 | `--strict` flag：hit@5 必须 **>** baseline（不只是不回归）。**CI 自动化触发**：`scripts/eval_gate_ci.sh` 看 git diff（与 main 对比）若含 `*_retriever.py / *_extractor.py / facts.py / enhanced_retriever.py` 改动 → 自动加 `--strict`；不靠 PR template 提醒 | v1 靠"PR template 提醒"重蹈 Stage 1 "靠人记得"的死代码雷（D-RISK-3）|
| **D11** | eval_gate baseline sanity | `--update-baseline` 默认开启 sanity：写入前对比磁盘已有 baseline，hit@5 不能比旧值低 > 容差、token_per_query 不能比旧值高 > 增长上限；需 `--force` 绕过 | 防止"无脑钉新值"绕过门控 |
| **D12 ★v2** | episodic→semantic 触发时机 | summarizer 在 `summarize_old_sessions` 事务**提交后**，把 task 加进 **`app.state.background_tasks: set[asyncio.Task]`** + `add_done_callback(background_tasks.discard)`；lifespan shutdown 时 `await asyncio.gather(*tasks, return_exceptions=True)` 回收 | v1 用 `asyncio.create_task` 不存引用 = Python 3.11+ GC 静默吞掉（D-RISK-4）；正确做法是收集 set |
| **D13 ★v2** | episodic_summary 的 fact category 注册 | **必须同时改 3 处**：① `facts.py:60` `VALID_CATEGORIES` 加 `'episodic_summary'`；② `facts.py` `_CATEGORY_DECAY` 加 `'episodic_summary': 0.01`（slower decay，长期保留）；③ `FactExtractor.process_message` 加 `source` 参数 + 联合白名单 `(role=user|assistant) OR (role=system AND source=summarizer)` | E5 + R-MISS-3：v1 漏掉这三处任何一个，TS5-2 跑不绿 |
| **D14** | summarizer → facts 的去重 | summary 二次抽出的 fact 也走 A1 的 cross-key 矛盾检测路径，与 user-level facts 共池；多了 `source_msg_id` 指向 summary 消息便于追溯 | 不另建 episodic facts 表；统一池子靠 category 区分 |
| **D15** | B1 GUI 联调的产物形态 | `tests/e2e_workspace_memory.py` 主 checkout 脚本（驱动 backend WebSocket + 录 `workspace_state` 表 dump + 截图）+ `evidence/2026-05-23-mr4-e2e/` 归档；**不引入 Playwright/Tauri E2E 框架** | 沿用 OpenSpec p5-s1 已有的 dump-based 验证 |
| **D16** | flag 粒度 | 4 个新 flag：`cross_key_merge` / `memory_forget` / `entity_path` / `episodic_to_semantic`，全收 `[memory.v2]`；额外 `[memory.v2.memory_forget] enable_natural_language=false`（默认禁用 NL 模式，D5）| 沿用 Stage 1 风格；独立回退 |
| **D17 ★v2** | 内嵌依赖关系 + 失败时降级 | `entity_path=true` 需 `facts_extract=true`（启动 warn）；`cross_key_merge=true` 需 `facts_extract=true`；`episodic_to_semantic=true` 不依赖其他；**`memory_forget=true` 但 `ensure_memory_v2_columns` ALTER `forgotten_at` 失败时 → 强制把 `memory_forget` flag 关掉 + warn** | Stage 1 D5 / 一致 + R8 v2 加固 |

### 3.1 跨 key 矛盾治理详细设计（关键 WI-S2.1a，v2 修订 D3 视野）

**问题示例**：
```
T0: user "我对花生过敏"     → fact (subject=user, key=allergy_peanut, value="对花生过敏", active)
T1: user "其实是过敏海鲜，不过敏花生" → LLM 抽出 (subject=user, key=allergy_seafood, value="过敏海鲜")
```

**Stage 2 v2 新流程**（D3 ★v2 混合视野）：
```python
async def _persist_extracted(self, extracted, message_id):
    for fact in extracted:
        # 旧路径：same (subject, key) merge
        existing = await self._store.find_active(subject=fact.subject, key=fact.key)
        if existing:
            decision = await self._decide_merge(fact, existing)
            ...
            continue

        # 新路径（A1）：cross-key conflict scan（v2 混合视野）
        if self._cross_key_merge_enabled:
            # ① 最近 20 条
            recent = await self._store.list_active(subject=fact.subject, limit=20)
            # ② 语义最近 10 条（限 same subject）
            semantic = []
            if self._embedder is not None:
                query_emb = await self._embedder.embed(fact.value)
                semantic = await self._store.vector_search_in_subject(
                    query_embedding=query_emb,
                    subject=fact.subject,
                    limit=10,
                )
            # ③ 合并去重，上限 25
            candidates = _merge_dedupe(recent, semantic, limit=25)
            if candidates:
                conflict_decision = await self._decide_cross_key_conflict(
                    new_fact=fact, candidates=candidates,
                )
                for old in conflict_decision.conflicts:
                    new_fid = await self._store.upsert(...)
                    await self._store.mark_superseded(
                        old_id=old["old_id"], superseded_by=new_fid,
                    )
                if conflict_decision.should_insert and not conflict_decision.conflicts:
                    await self._store.upsert(...)
                continue

        # fallback：旧 insert 路径（flag 关 / 无 same-subject facts）
        await self._store.upsert(...)
```

**新增 FactsStore 方法**：
- `mark_superseded(old_id, superseded_by)`：原子 `UPDATE facts SET is_active=0, superseded_by=? WHERE id=? AND is_active=1`
- `vector_search_in_subject(query_embedding, subject, limit)`：limit 到 subject 内的向量召回（复用现有 vector_search 逻辑 + WHERE subject=?）

### 3.2 memory_forget 工具详细设计（WI-S2.1a，v2 修订 D5/D6）

**`backend/deskpet/tools/memory_tools.py`**（模块顶层，import 时不依赖 store）：

```python
"""memory_forget tool — explicit forgetting of facts.

设计：
  * `bind()` 在 main.py lifespan 注入活实例（FactsStore/Embedder/LLM）—
    discovery 走 pkgutil.import 时无活实例，必须 lazy bind
  * 工具自身规则拦截（D5 v2）：
    - query 长度 < 6 字 → skipped
    - 命中 fact > 5 → skipped（防"忘记所有"灾难）
  * dangerous=True + permission_category="write_file" → registry 触发 UI 确认
  * 删的不是真 DELETE，是 `is_active=0 + forgotten_at=now()` 可恢复
"""
from deskpet.tools.registry import registry

_facts_store = None
_embedder = None
_llm_call = None

def bind(facts_store, embedder, llm_call):
    """main.py lifespan 调一次。"""
    global _facts_store, _embedder, _llm_call
    _facts_store = facts_store
    _embedder = embedder
    _llm_call = llm_call


async def _handle(args: dict, task_id: str) -> str:
    if _facts_store is None:
        return json.dumps({"status": "error", "reason": "memory tool not bound"})
    fact_id = args.get("fact_id")
    query = args.get("query")
    if fact_id is not None:
        return await _forget_by_id(int(fact_id))
    if query:
        return await _forget_by_query(str(query))
    return json.dumps({"status": "error", "reason": "需 fact_id 或 query 之一"})


_SCHEMA = {
    "name": "memory_forget",
    "description": "Forget a previously-remembered fact. Use when the user explicitly asks to forget.",
    "parameters": {
        "type": "object",
        "properties": {
            "fact_id": {"type": "integer"},
            "query": {"type": "string"},
        },
    },
}


# 模块顶层 register —— pkgutil discovery 触发时执行
registry.register(
    name="memory_forget",
    toolset="memory",
    schema=_SCHEMA,
    handler=_handle,
    permission_category="write_file",
    dangerous=True,
)
```

**`_forget_by_query` 规则拦截**（D5 v2）：
```python
async def _forget_by_query(query: str) -> str:
    if len(query.strip()) < 6:
        return json.dumps({"status": "skipped", "reason": "query 过短"})

    # 1. 向量召回 top 5
    candidates = await _facts_store.vector_search(
        await _embedder.embed(query), limit=5,
    )
    if not candidates:
        return json.dumps({"status": "not_found"})
    if len(candidates) > 5:
        return json.dumps({"status": "skipped", "reason": "过宽 query 命中 fact 数 > 5"})

    # 2. LLM 二次确认
    confirmed_ids = await _llm_confirm_forget(query, candidates)

    # 3. 仍要硬上限：单次最多删 3 条
    confirmed_ids = confirmed_ids[:3]

    # 4. 标 inactive + forgotten_at
    op_id = uuid.uuid4().hex
    now = time.time()
    for fid in confirmed_ids:
        await _facts_store.mark_forgotten(fid, op_id=op_id, ts=now)

    return json.dumps({
        "status": "ok",
        "op_id": op_id,      # ← UI undo 必须带它
        "forgotten_ids": confirmed_ids,
    })
```

**main.py lifespan 加**（约 L1300 附近）：
```python
# memory_forget 工具激活
if cfg.memory.v2.memory_forget:
    from deskpet.tools import memory_tools
    memory_tools.bind(_facts_store, _embedder, llm_call_func)
    log.info("p4_memory_forget_tool_bound")
```

### 3.3 entity 索引详细设计（WI-S2.2，v2 修订 D7/D8/D9）

**FactsStore.find_by_entities（v2 ★：只查 value 列）**：

```python
async def find_by_entities(
    self, entities: list[str], *, limit: int = 10,
) -> list[dict]:
    if not entities:
        return []
    seen: dict[int, dict] = {}
    async with aiosqlite.connect(self._db_path) as conn:
        conn.row_factory = aiosqlite.Row
        for e in entities[:5]:  # 上限 5 个实体
            if len(e.strip()) < 2:
                continue
            pat = f"%{e.strip()}%"
            cur = await conn.execute(
                "SELECT * FROM facts WHERE is_active = 1 "
                "AND value LIKE ? "          # ← v2 ★只查 value
                "ORDER BY updated_at DESC LIMIT ?",
                (pat, int(limit)),
            )
            for r in await cur.fetchall():
                seen.setdefault(r["id"], dict(r))
            await cur.close()
    rows = list(seen.values())
    rows.sort(key=lambda r: -float(r.get("updated_at") or 0))
    return rows[:limit]
```

**EntityExtractor 三档 + 停用词（v2 ★）**：

```python
_STOPWORDS = {
    "我的", "我们", "今天", "明天", "昨天", "这个", "那个", "什么",
    "怎么", "了吗", "的人", "的话", "的事", "有什么", "可以", "知道",
    "时候", "地方", "事情", "东西", "问题",
    # 英文
    "What", "When", "Where", "Why", "How", "The", "This", "That",
}


class RegexEntityExtractor:
    _CN = re.compile(r"[一-龥]{2,4}")
    _EN = re.compile(r"\b[A-Z][a-zA-Z]+\b")

    async def extract(self, query: str) -> list[str]:
        cn_hits = self._CN.findall(query)
        en_hits = self._EN.findall(query)
        candidates = {*cn_hits, *en_hits}
        # 过滤停用词
        filtered = [c for c in candidates if c not in _STOPWORDS]
        return filtered[:5]
```

**EnhancedRetriever 改造（v2 ★）**：

```python
def __init__(self, base, *,
    facts_store=None, facts_weight=0.0,
    reranker=None, query_rewriter=None,
    embedder=None, chunk_store=None,
    # 新增（A2 v2）
    entity_extractor=None, entity_weight=0.10,  # ← v2 默认 0.10
):
    ...
    self._entity_extractor = entity_extractor
    self._entity_weight = float(entity_weight)
```

### 3.4 episodic→semantic 详细设计（WI-S2.4，v2 修订 D12/D13）

**E5 + R-MISS-3 三处必须同时改**：

1. `facts.py:60` `VALID_CATEGORIES = {"profile", "preference", "project", "event", "reflection", "episodic_summary"}`
2. `facts.py` `_CATEGORY_DECAY = {..., "episodic_summary": 0.01}`（slower decay）
3. `FactExtractor.process_message` 加 `source` 参数 + 联合白名单

**summarizer 改造**：

```python
# main.py lifespan
app.state.background_tasks = set()


# summarizer.py 调用点
if cfg.memory.v2.episodic_to_semantic and fact_extractor is not None:
    task = asyncio.create_task(
        fact_extractor.process_message(
            message_id=summary_id,
            content=summary_text,
            role="system",
            source="summarizer",
        )
    )
    app.state.background_tasks.add(task)             # ← v2 ★保持引用
    task.add_done_callback(app.state.background_tasks.discard)


# main.py shutdown
await asyncio.gather(*app.state.background_tasks, return_exceptions=True)
```

### 3.5 eval 门控严格化详细设计（WI-S2.3，v2 修订 D10）

**v2 ★ CI 自动化触发**：

新建 `backend/scripts/eval_gate_ci.sh`：

```bash
#!/bin/bash
# 看 git diff，召回相关代码改动 → 自动 --strict
set -e

CHANGED=$(git diff --name-only origin/master...HEAD)

STRICT_FLAG=""
if echo "$CHANGED" | grep -qE "(enhanced_retriever|.*_retriever|.*_extractor|facts\.py|retriever\.py|reranker\.py)\.py$"; then
    echo "[eval_gate_ci] 检测到召回相关改动 → 启用 --strict 模式"
    STRICT_FLAG="--strict"
fi

cd "$(dirname "$0")/.."
python -m scripts.eval_gate $STRICT_FLAG
```

CI workflow（`.github/workflows/eval-gate.yml`）调它。Strict 不靠"PR 作者记得"，靠 git diff 客观判断。

---

## 4. 需求拆解（Work Items，v2 拆分 WI-S2.1）

> 每个 WI 的 DoD：**wire 进生产链路 + 端到端集成测试绿 + flag 可独立开关 + eval 不回归 + 文档更新**。

### 4.1 WI-S2.1a 跨 key 矛盾治理 + memory_forget 工具 + cleanup 脚本（P1 后端，v2 拆出 UI 部分）

**前置**：D1 schema migrator（含 `superseded_by` 和 `forgotten_at` 双列 ALTER）

**编码任务**：
1. `memory_v2_schema.py` 更新 `_DDL` 中 facts 表新建库带 `superseded_by INTEGER` + `forgotten_at REAL`
2. 新建 `schema_v2_migrator.py` 含 `ensure_memory_v2_columns(db_path)`，幂等 ALTER TABLE
3. `FactsStore` 新增方法：
   - `mark_superseded(old_id, superseded_by)`
   - `mark_forgotten(fact_id, op_id, ts)`
   - `restore_from_undo(op_id, max_age_seconds=5)`
   - `list_superseded_chain(fact_id)`
   - `vector_search_in_subject(query_embedding, subject, limit)`
4. `FactExtractor._persist_extracted` 加 cross-key 矛盾扫描分支（D3 v2 混合视野）
5. `FactExtractor._decide_cross_key_conflict` 新方法 + `_CROSS_KEY_CONFLICT_PROMPT` 常量
6. **`category='episodic_summary'` 加入 `VALID_CATEGORIES` + `_CATEGORY_DECAY`**（E5）
7. `FactExtractor.process_message` 扩 `source` 参数 + role/source 联合白名单
8. `scripts/facts_conflict_cleanup.py` 老库批量清理；含 `--llm-budget N` + resume token + `--dry-run` + `--max-subjects`
9. **memory_forget 工具**：新建 `backend/deskpet/tools/memory_tools.py`（D6 v2 顶层 register + bind 模式）
10. `main.py` lifespan 加 `memory_tools.bind(...)` + 加 `app.state.background_tasks: set` 初始化
11. `p4_ipc.py` 路由表加 `memory_facts_list` / `memory_forget` / `memory_forget_undo`（E3 v2）

**flag**：`[memory.v2] cross_key_merge` + `[memory.v2] memory_forget` + `[memory.v2.memory_forget] enable_natural_language=false`

**DoD**：
- `ensure_memory_v2_columns` 对老库幂等加双列；fresh 库 DDL 直接带；多次调用安全；ALTER 失败时自动关相关 flag
- `cross_key_merge=true` 时 MR-1-4 实测："对花生过敏" + "其实过敏海鲜" → 老 fact 标 `is_active=0, superseded_by=<新 fid>`
- `memory_forget` 工具被 agent 调用能精确删；自然语言模式 default 禁用；启用时单次最多删 3 条 + query<6 字拒
- ws 路由 `memory_forget` 端到端打通，op_id 返回给前端
- flag 关时与 Stage 1 行为字节级一致（含 facts.py 全套既有测试 0 回归）

### 4.2 WI-S2.1b MemoryPanel facts view + 🗑 + undo（P1 UI，v2 新增独立 WI）

**编码任务**：
1. 前端 `types/messages.ts` 加 `FactItem` 类型 + `MemoryFactsListResponse` / `MemoryForgetResponse` / `MemoryForgetUndoResponse`
2. `MemoryPanel.tsx` 加第 5 个 view "事实"（segTab 加 `facts`；渲染分支加 facts 列表）
3. 加载逻辑：进 facts view 触发 ws `memory_facts_list`（含 `is_active=1, limit=200, order_by=updated_at DESC`）
4. 每条 fact 卡片右上 🗑 按钮 → ws `memory_forget {fact_id}`
5. 收到 `memory_forget_response {op_id}` → 卡片立即移除 + 5 秒 undo 浮窗组件出现
6. undo 浮窗带"撤销"按钮 → ws `memory_forget_undo {op_id}` → 后端 5 秒窗口校验 + restore
7. 后端 5 秒窗口逻辑：`mark_forgotten` 记 `forgotten_at`；`restore_from_undo` 校验 `now - forgotten_at < 5` 才允许 restore
8. Vitest 测试：facts view 渲染 / 🗑 点击 / undo 显示 / undo 5 秒超时不允许 restore

**flag**：依赖 `memory_forget`

**DoD**：
- MemoryPanel 打开 → 进 facts view → 实时显示 facts 表内容
- 点 🗑 → 卡片即刻移除 + 5 秒 undo 浮窗
- 5 秒内点撤销 → fact 恢复 active
- 5 秒后点撤销（伪造延迟） → 后端拒 + 前端浮窗超时自动消失
- 工作量预估 1.5-2 天

### 4.3 WI-S2.2 entity 索引检索路（P3，v2 修订 LIKE 范围 + 停用词）

**编码任务**：
1. `FactsStore.find_by_entities(entities, limit)` 新方法 — **LIKE 只查 value 列**（D9 v2）
2. `backend/deskpet/memory/entity_extractor.py` 新文件：`LLMEntityExtractor` / `RegexEntityExtractor`（带停用词集 D7 v2）/ `NoopEntityExtractor` / `CompositeEntityExtractor`
3. `EnhancedRetriever.__init__` 加 `entity_extractor` + `entity_weight=0.10` 参数（D8 v2）
4. `EnhancedRetriever._collect_entity_hits(query)` 新方法
5. `EnhancedRetriever.recall()` 在 facts 路之后加 entity 路 RRF 融合
6. `main.py` lifespan：`cfg.memory.v2.entity_path=true` 时构造 `CompositeEntityExtractor(LLM, Regex)` 注入 EnhancedRetriever
7. eval fixture 加 10 条 entity-targeted query
8. boot 日志加 entity 路 hit 比例观测（评审 B5）

**flag**：`[memory.v2] entity_path`（依赖 `facts_extract`）

**DoD**：
- entity-targeted 子集 hit@5 ≥ 0.65（v2 保守值）
- flag 关时 EnhancedRetriever.recall 路径与 Stage 1 字节级一致
- LLM 不可用时降级 regex（含停用词过滤）；regex 提空 → 返回 `[]` 不报错

### 4.4 WI-S2.3 eval 门控严格化 + CI 自动化（P2）

**编码任务**：
1. `scripts/eval_gate.py` 加 `--strict` flag + `_gate_strict(current, baseline)` 函数
2. `--update-baseline` 默认开启 sanity 检查；加 `--force` 绕过
3. 新建 `backend/scripts/eval_gate_ci.sh`（D10 v2 自动化触发）
4. 新建 `.github/workflows/eval-gate.yml` 在 PR 时调 `eval_gate_ci.sh`

**flag**：无（脚本级开关）

**DoD**：
- `--strict` 当前 baseline = 当前结果时 fail（hit@5 not > baseline）
- `--update-baseline` 钉低 hit@5 时拒绝；`--force` 可绕
- CI 改 `enhanced_retriever.py` 时 PR job 自动加 `--strict`
- 不影响默认 gate 行为

### 4.5 WI-S2.4 episodic→semantic 固化通路（P2，v2 修订 background_tasks）

**编码任务**：
1. `FactExtractor.process_message` 加 `source: str = "user_message"` 参数 + 联合白名单（与 WI-S2.1a #7 整合）
2. `_EXTRACT_PROMPT` 针对 source="summarizer" 加一段提示
3. `summarizer.summarize_old_sessions` 加 `fact_extractor` + `episodic_to_semantic` 参数
4. **summary 后用 `app.state.background_tasks` set 收集 task + `add_done_callback(discard)`**（D12 v2）
5. lifespan shutdown 加 `await asyncio.gather(*background_tasks, return_exceptions=True)`
6. `main.py` 把 `_fact_extractor` 传给 `summarize_old_sessions` 的所有调用点

**flag**：`[memory.v2] episodic_to_semantic`

**DoD**：
- 跑 summarizer 处理 ≥ 3 session → ≥ 1 条 `category='episodic_summary'` 落 facts 表
- 抽出的 episodic_summary fact 走 cross-key 矛盾扫描（与 WI-S2.1a 整合）
- task 在 shutdown 时被回收，无 "Task was destroyed but it is pending" warning
- flag 关时 summarizer 行为字节级一致

### 4.6 WI-V.1 MR-4 GUI 端到端联调（P1，收尾，v2 工作量 1.5-2 天）

**前置**：主 checkout `backend/.venv` 就绪 + Tauri dev 能 spawn 后端（**v2 评审 P2-2 建议提前并行启动主 checkout venv 准备**）

**编码任务**：
1. `tests/e2e_workspace_memory.py` 主 checkout 脚本（详 v1 TDD §A5）
2. 录屏 + 表 dump + 计数对比表归档到 `evidence/2026-05-23-mr4-e2e/`

**flag**：无

**DoD**：
- 第二轮 agent 至少有一次 prompt 中含工作记忆段
- flag on 时 `file_read` 调用数 ≤ flag off
- 证据完整归档

---

## 5. 里程碑与排期（v2）

| 里程碑 | 内容 | 依赖 | 预估 |
|---|---|---|---|
| M0 | D1 schema migrator（双列 + Stage 1 真实库副本测试 + ALTER 失败兜底）| — | 0.5 天 |
| M1a | **WI-S2.1a 后端**（cross-key + memory_forget 工具 + cleanup 脚本）| M0 | 4-5 天 |
| M1b | **WI-S2.1b UI**（facts view + 🗑 + undo + Vitest）| M1a（后端 ws 就绪）| 1.5-2 天 |
| M2 | WI-S2.3 eval_gate 严格化 + CI（独立并行）| — | 1 天（v1 是 0.5 天，加 CI workflow 配置）|
| M3 | WI-S2.4 episodic→semantic（依赖 M1a cross-key + episodic_summary category）| M1a | 2 天 |
| M4 | WI-S2.2 entity 索引（独立可并行 M1/M3）| — | 3 天 |
| M5 | WI-V.1 MR-4 GUI 联调（主 checkout）| M1-M4 全 | **1.5-2 天**（v1 是 1 天，含 venv 安装）|
| M6 | 全套回归 + eval --strict 跑 + 文档化 | M5 | 0.5 天 |

**总计**：**~14 天单人**；可三路并行（M1a / M2 / M4），并行后压缩到 **~8-9 天**。

> **关键并行节奏**：M1a 启动同日，开发者 A 在主 checkout 启动 venv 准备（评审 P2-2）—— 装 torch / sqlite-vec 是异步等的，不堵主路径。

---

## 6. 风险登记（v2 新增 R-MISS-1/2/3 + R3/R8/R9 加固）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | LLM 看候选 N 条 facts prompt 长 | LLM 延迟 ↑ | D3 v2 硬上限 25 条；prompt 只放 `id/key/value/updated_at` 不放 evidence |
| R2 | cross-key merge LLM 误判 | 用户数据被错误降级 | 集成测试覆盖 12+ 矛盾对 + 8+ 非矛盾对；MR-S2-1-9 N=30 误判率 ≤ 15% |
| R3 ★v2 | facts_conflict_cleanup 脚本对老库扫太久 / LLM 抖动 | 卡 boot 或半夜跑挂 | 不进 boot；脚本设 `--max-subjects` / `--batch-size` / **`--llm-budget N`** / **resume token**；幂等 |
| R4 | entity NER 误差大 | entity 路噪声 | D7 v2 三档降级 + 停用词集；entity_weight 0.10 保守；mock 检测自动 bypass |
| R5 | entity 路过激召回旧 facts 把召回结果挤掉 ground truth | hit@5 回归 | 跑 eval gate 强制门控；entity 路 weight 可调 |
| R6 | summary 二次抽取出大量 episodic_summary facts 污染池 | facts 表过载 + cross-key 误判 | summary 比 user 消息少 1-2 数量级（30 天 1 次）；MR-S2-4 人工抽查质量 |
| R7 | summarizer ↔ fact_extractor 循环依赖 | import 时序问题 | `main.py` 在 fact_extractor 构造完毕后才传给 summarizer 调用点 |
| R8 ★v2 | `superseded_by` 老库 ALTER 失败 | 后续 `mark_superseded` SQL OperationalError | 启动期 `ensure_memory_v2_columns` 加 try/except + log；**ALTER 失败时强制把 cross_key_merge / memory_forget flag 关掉 + warn**（D1 v2 / D17 v2）|
| R9 ★v2 | memory_forget 误删 / undo 并发安全 | 用户记忆错误清空 / undo 浮窗叠加 | D5 v2 工具自身规则（query<6 字拒、>5 fact 拒、单次最多 3 条）；**每个 forget 返回 op_id，undo 必须带 op_id**；删的是 inactive+forgotten_at 非真 DELETE |
| R10 | MR-4 GUI 联调环境依赖 | 卡进度 | `e2e_workspace_memory.py` 能本地用 deepseek-chat 跑；主 checkout venv 提前并行准备 |
| R11 | strict gate 误伤无关 PR | 阻塞合并 | D10 v2 git diff 客观判断；不靠人记得；CI workflow 失败可在 PR 评论看原因 |
| R12 | 又"写完不接" | 重蹈覆辙 | 沿用 Stage 1 DoD 强制 + eval_gate.py 自动化 |
| **R-MISS-1** ★v2 | `tools/__init__.py` discovery 在 import 时跑 | 新 `memory_tools.py` import 时拿不到 FactsStore 实例 | D6 v2 lazy bind 模式：`memory_tools.bind(...)` 在 main.py lifespan 调；模块 import 时不依赖任何活实例 |
| **R-MISS-2** ★v2 | `memory_forget` 标 inactive 后，user 再提及 → FactExtractor 重新插覆盖遗忘 | 用户"忘记"指令被悄无声息撤销 | D5 v2 加 `forgotten_at` 时间戳 → FactExtractor 写入前查最近 N 天（默认 7 天）forgotten 记录跳过；MR-S2-2-8 专项测试 |
| **R-MISS-3** ★v2 | `category='episodic_summary'` 未加入白名单 | TS5-2 永远跑不绿 | D13 v2 三处同时改：VALID_CATEGORIES + _CATEGORY_DECAY + process_message source 参数 |

---

## 7. 验收标准（v2 整体 DoD）

1. **WI-S2.1a**：cross-key 矛盾检出率 ≥ 70%（eval 矛盾对子集）；误判率 ≤ 15%（N=30 抽样）；memory_forget 工具端到端走通（fact_id + 自然语言两种）；老库 ALTER 兼容 + 失败兜底
2. **WI-S2.1b**：MemoryPanel facts view 5th view 渲染；🗑 + 5 秒 undo + op_id 校验
3. **WI-S2.2**：entity-targeted 子集 hit@5 ≥ 0.65；entity 路独立 flag 可关
4. **WI-S2.3**：`--strict` + sanity 检查 + CI 自动化触发均按预期行为
5. **WI-S2.4**：summarizer 后 episodic_summary 落 facts 表；与 cross-key merge 整合无冲突；shutdown 无 task warning
6. **WI-V.1**：MR-4 GUI 联调主 checkout 跑通 + 证据归档
7. **回归**：flag 全关时 backend pytest 0 回归（含 Stage 1 全部 1799+ 用例）；flag 全开时 eval_gate PASS
8. **eval 门控**：跑 `--strict` 在装 Stage 2 全 flag 后 hit@5 严格 > baseline
9. **文档**：每 WI 在 01-TDD.md §D 实测结果回填；followup A1/A2/A3/A4/B1 在 status 文档标 ✅
10. **可发版**：worktree-memory-stage2 → master 的 PR 上的 eval_gate CI 绿 + backend pytest 全绿

---

## 8. 未来工作（Stage 3 远期，仅作 roadmap）

- **procedural memory 深化**：`SkillMemoryStore` 自动从 reflection 抽"反复问题 → 解法"
- **LoCoMo 风格中文回测集**：长程多轮 fixture，35 条 → 200+ 条
- **embedding 模型升级**：BGE-M3 vs Qwen3-embedding vs m3e-large 对比
- **memory pruning**：自动归档 90 天未召回的 facts（不删，标 archived）

本 PRD 不涵盖上述。

---

## 9. v2 修订摘要（动工前 checklist）

- [x] D1: 双列 ALTER（superseded_by + forgotten_at）+ 失败兜底
- [x] D3: cross-key 视野改"最近 20 ∪ 语义 10"混合
- [x] D5: memory_forget 自然语言模式默认禁用 + 工具规则拦截
- [x] D6: 顶层 register + bind 模式（无 @register_tool / 无 _ctx）
- [x] D7: regex 加停用词集
- [x] D8: entity_weight 0.15 → 0.10
- [x] D9: LIKE 只查 value 列
- [x] D10: strict CI 自动化（git diff 触发）
- [x] D12: background_tasks set + add_done_callback + shutdown gather
- [x] D13: episodic_summary 进 VALID_CATEGORIES + _CATEGORY_DECAY + process_message source
- [x] D17: ALTER 失败时关 flag
- [x] WI-S2.1 拆 a (后端) + b (UI)
- [x] M1 4-5 天 → 6-7 天 (M1a + M1b)
- [x] M5 1 天 → 1.5-2 天
- [x] 风险加 R-MISS-1/2/3
- [x] 风险加固 R3/R8/R9
