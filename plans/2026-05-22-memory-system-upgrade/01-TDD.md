# TDD — DeskPet 记忆系统升级：技术设计 + 测试规格

**关联**: `00-PRD.md`（第 2 版）
**状态**: 第 2 版 —— 已过架构评审，按评审意见修订
**原则**: 测试先行。每个 WI 的实现以"让本文用例全绿"为完成标准。
**核心纪律**: Phase A-E 当初**单元测试全绿但功能零生效** —— 根因是只测了
"模块本身"、没测"接入后真实运行栈行为"。本次每个 WI **必须有集成测试**,
断言"wire 进 `main.py` 后,真实调用链里 X 确实发生了"。单元测试不可替代它。

> ## 第 2 版修订要点
> 1. facts 接入改为**组合 fanout callable**（`_on_message_written` 是单值）。
> 2. facts 召回改**向量召回**（不用 LIKE）；`EnhancedRetriever` 显式传
>    `facts_weight`。
> 3. reranker 拆为独立 WI，先于 facts 进 RRF。
> 4. workspace：`file_write`/`file_read`（无 `file_edit`）；handler 改 async；
>    新组件须进 policy `prefer`。
> 5. reflection 写 `facts` 表；`SkillMemoryStore` 独立。
> 6. 新增测试：facts 并发抽取竞态、合成 Hit 渲染链路、mock reranker bypass、
>    token/query 口径、facts 误抽。

---

## A. 技术设计

### A1. config schema（WI-M0.3）

`config.toml`：

```toml
[memory.v2]
feedback_loop      = false
facts_extract      = false
rerank             = false   # 独立、可先于 facts 开
enhanced_retriever = false   # facts 进 RRF（依赖 facts_extract）
chunking           = false
query_rewrite      = false
workspace_memory   = false
reflection         = false

[memory.v2.facts]
min_user_chars  = 8          # 字数采样门（取代 facts.py 硬编码 <8）
facts_weight    = 0.2        # facts 路进 RRF 的权重
model_override  = ""         # 留空 = 用主 LLM 的小模型
```

`config.py`：`MemoryV2Config` / `MemoryV2FactsConfig` dataclass，
`MemoryConfig` 加 `v2` 字段;缺段 → 全默认 false。
**注意**:`[memory.v2]` 是 `[memory]` 的子表,M0.3 第一步须验证
`_load_section` 能否递归解析嵌套 dataclass —— 不能则给 `MemoryConfig` 写
自定义 `from_toml`(参考 `BillingConfig`),不要假设"沿用 `_load_section`"。

**flag 依赖运行时校验**：`enhanced_retriever=true & facts_extract=false`
→ warn；`rerank=true` 但 reranker 模型缺失 → 自动 bypass + warn（见 A4）。

### A2. 死代码体检（WI-M0.1）

体检逻辑写成 `tests/test_memory_v2_smoke.py`（**固化为长期回归**，不只一次
性脚本）：对每个 Phase A-E 类做"构造 + 最小真实调用",捕获
`TypeError`/`AttributeError`（接口腐烂信号）；并验证 `ensure_memory_v2_tables()`
能在临时 DB 建出 7 表。

### A3. facts 接入（WI-M1.2）

**关键：`_on_message_written` 是单值 callable，不是 hook 列表，且当前只用
2 参数调用。** `session_db.py:101` 是 `Optional[OnMessageWritten]`;
`session_db.py:304` 实际调用是 `await self._on_message_written(msg_id,
content)` —— **只有 2 参数,无 `role`**;而 `FactExtractor.process_message`
强制要 `role=`（`facts.py:402`）。

**决定:扩 `_on_message_written` 签名为 3 参数 `(msg_id, content, role)`。**
改动:① `session_db.py` 的 `OnMessageWritten` 类型 + `:304` 调用处补传
`role`（`append_message` 本就持有 role）;② `main.py:957` 改为组合 fanout:

```python
async def _on_msg(mid, text, role):          # 与扩后签名一致
    await _vw.enqueue(mid, text)             # 原有,只取 mid/text
    if cfg.memory.v2.facts_extract:
        asyncio.create_task(                 # 异步,不阻塞 append_message
            _fact_extractor.process_message(
                message_id=mid, content=text, role=role))
_sdb._on_message_written = _on_msg
```
- 采样：`FactExtractor` 已对 `user`+`assistant` 抽、`tool`/系统跳过；
  字数门由 `min_user_chars` 驱动（去掉硬编码 `<8`）。
- shadow：本 WI 只到"写 `facts` 表"，不碰召回。
- **backfill**：`scripts/facts_backfill.py` 对历史 `messages` 批量抽取。

### A4. reranker（WI-M1.3，独立先上）

reranker 纯重排、不改召回集合，风险最低 → 先于 facts 进 RRF 上线。

- 接入：`EnhancedRetriever(base=老Retriever, facts_weight=0.0,
  reranker=BGEReranker(...))` —— **facts 路关闭，只启用 reranker**。
- `recall()` 在 RRF 之后、`top_k` 之前插 `reranker.rerank(query, cands)`。
- **mock bypass**：`BGEReranker` 缺模型时降级 `MockReranker`（hash 打分，
  会打乱顺序）。`rerank` flag 开但实际是 mock → **自动 bypass 重排** +
  warn 一次，绝不让 mock 污染线上召回顺序。

### A5. EnhancedRetriever 接管召回（WI-M1.4）

```
main.py 构造期：
  base_retriever = Retriever(session_db, embedder)              （老，永远在）
  if cfg.memory.v2.enhanced_retriever:
      retriever = EnhancedRetriever(
          base=base_retriever,
          facts_store=facts_store,
          facts_weight=cfg.memory.v2.facts.facts_weight,   # 必须显式传 0.2
          reranker=reranker if cfg.memory.v2.rerank else None,
          query_rewriter=qr if cfg.memory.v2.query_rewrite else None,
      )
  else:
      retriever = base_retriever
  MemoryManager(retriever=retriever, ...)
```

- **`facts_weight` 必须显式传**：默认 0.0 = facts 永不折叠进结果（静默 bug）。
- **facts 召回走向量召回** —— 具体落地设计见 **PRD §3.1**(facts 表加
  `embedding BLOB` 列、`FactsStore.upsert` 写时 embed、新增
  `vector_search`、`EnhancedRetriever.__init__` 加 `embedder` 参数、
  `_collect_fact_hits` 改调 `vector_search`、mock embedder 时降级 LIKE)。
  **当前 `_collect_fact_hits`（`enhanced_retriever.py:189`）写死调
  `.search()` LIKE,WI-M1.4 必须按 §3.1 改掉。**
- **facts 文本渲染**：`_collect_fact_hits` 产出的 `Hit` 已带
  `text="[fact] ..."`、`source="facts"`;`MemoryManager._safe_l3`
  （`manager.py:300`）经 `_to_dict` 透传,`MemoryComponent._render_l3_only`
  （`memory.py:175`）按 `Hit.text` 渲染 —— **机制上本就能进 prompt,无需
  "识别合成 ID"**。WI-M1.4 做端到端验证即可(见 T4-4)。

### A6. chunking + query rewriting（WI-M1.5）

- chunker（**写入侧**）：`VectorWorker` enqueue 前长消息经
  `MessageChunker.chunk_message(message_id=, content=)`，每 chunk 独立 embed
  进 `messages_chunks`；召回命中返回 **parent** message。
  需 shadow 窗 + chunk backfill（老消息无 chunk）。
- query_rewriter：`recall(query)` 入口短 query 经 `LLMQueryRewriter.rewrite`。

### A7. 工作记忆（WI-M1.6）

- **接入点**：`file_tools.py` 实际只有 `file_read/file_write/file_glob/
  file_grep`（无 `file_edit`）。在 `file_write` 成功分支记 `action="write"`、
  `file_read` 成功分支记 `action="read"`；glob/grep 不记。
- **handler 改 async**：`_handle_file_read`/`_handle_file_write`
  （`file_tools.py:124/189`）当前是 sync(跑 worker thread)。改为
  `async def` —— 依据是**工具 registry**(`deskpet/tools/registry.py`)的
  dispatch 用 `iscoroutinefunction` 分流(`registry.py:503-508`),async
  handler 直接 `await`,可直接 `await record_action(...)`。
- **新组件**：`backend/deskpet/agent/assembler/components/workspace_memory.py`
  （命名区分既有 `components/workspace.py`）。经 **assembler 的
  `ComponentRegistry.register(component)`**(`assembler/registry.py:43`,
  注意这是与上面工具 registry **不同的另一套 registry**)注册;**还须加进
  code task 对应 assembly policy 的 `prefer` 列表**
  （`assembler/policies/default.yaml` 的 `code` policy）—— 仅建文件不会被
  运行。WI-M1.6 DoD 含:确认 `build_default_assembler` 注册组件实例的扩展点,
  以及模块级 `_handle_file_write` 如何拿到 `WorkspaceMemoryStore` 实例。
- 新工具 `workspace_recall(query)`。

### A8. reflection + skill memory（WI-M1.7）

- `ReflectionWorker(db_path, facts_store, llm_call, *, window_hours,
  max_turns, subject)` —— 反思产物写进 **`facts` 表**（`category="reflection"`），
  **不是独立反思表**。
- `SkillMemoryStore` 是**独立类**，与 `ReflectionWorker` 无调用关系；
  procedural memory 存"反复问题→解法"。
- `main.py` lifespan 注册低频定时任务调 `ReflectionWorker.run_once()`；
  flag 关 → 不注册。
- LLM 来源：用 `local_llm`；用户离线/未配置 LLM → 跳过本次反思，不报错。

---

## B. 自动化测试规格

> **测试分层**：① 单元（模块本身，已有，按需补） ② **集成（接入后真实
> 运行栈行为，本次重点）** ③ eval 回归（指标门控） ④ 全套回归。

### TG-0 · 体检 smoke（WI-M0.1）

`test_memory_v2_smoke.py`

| # | 用例 | 断言 |
|---|---|---|
| T0-1 | 逐一构造 7 个 v2 模块 + 冒烟调用 | 无 `TypeError`/`AttributeError` |
| T0-2 | `ensure_memory_v2_tables()` 临时 DB | 7 表全建出 |

### TG-1 · config flag（WI-M0.3）

| # | 用例 | 断言 |
|---|---|---|
| T1-1 | 无 `[memory.v2]` 段 | 所有 flag False |
| T1-2 | 显式 `facts_extract=true` | 解析为 True |
| T1-3 | `[memory.v2.facts] min_user_chars=12 / facts_weight=0.3` | 解析正确；缺省 8 / 0.2 |
| T1-4 | `enhanced_retriever=true` 但 `facts_extract=false` | 构造时 warn 一次，不崩 |

### TG-2 · facts 接入（WI-M1.2，集成为主）

`test_memory_facts_integration.py` —— 真实 `SessionDB` + 真实 fanout callable。

| # | 用例 | 断言 |
|---|---|---|
| T2-1 | flag on，`append_message(user,"我对花生过敏")` → 等异步 | `facts` 表新增一行，结构合理 |
| T2-2 | `append_message(assistant, <一段承诺>)` | 也会抽（D4：assistant 也抽） |
| T2-3 | `append_message(tool, <JSON>)` / 短消息("嗯") | facts 表不新增 |
| T2-4 | **flag off** | `FactExtractor` 零调用 |
| T2-5 | 抽出与已有 fact 冲突的新事实（串行） | merge，旧 fact 更新而非重复插 |
| T2-6 | **并发**：同 session 连发两条相关消息 → 两个异步抽取 task | `facts` 表不出现两条本应 merge 的重复 active 行（评审缺口 2） |
| T2-7 | `append_message` 异步性 | 在抽取 LLM 返回前就 resolve |
| T2-8 | LLM 抽取失败（mock 抛错） | `append_message` 不受影响，记 warn |
| T2-9 | `facts_backfill.py` 对历史 messages 跑 | facts 表批量落行 |

### TG-3 · reranker（WI-M1.3，集成为主）

`test_memory_rerank_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| T3-1 | `rerank` on + 真实 reranker 模型 | `recall()` 候选经重排，top_k 来自重排后 |
| T3-2 | `rerank` on 但模型缺失（→ MockReranker） | **自动 bypass 重排** + warn；召回顺序 = RRF 原序，不被 mock 打乱 |
| T3-3 | `rerank` off | 跳过重排 |
| T3-4 | flag on 构造 | `MemoryManager` 持有的 retriever 是 `EnhancedRetriever`（facts_weight=0） |

### TG-4 · EnhancedRetriever 接管（WI-M1.4，集成为主）

`test_memory_enhanced_retriever_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| T4-1 | flag on 构造 | `MemoryManager` 持有 `EnhancedRetriever`，`facts_weight==0.2` |
| T4-2 | **flag off** | 持有裸 `Retriever`；`recall()` 与第一代字节级一致 |
| T4-3 | facts 表有"花生过敏"，`recall("我能吃什么零食")` | facts 经**向量召回**命中进结果（LIKE 子串匹配本会漏） |
| T4-4 | facts 命中后整条链路 | assembler bundle 的 system prompt 里**确实出现该 fact 文本** —— 验证 `Hit.text`（`[fact] ...`）经 `MemoryManager._safe_l3` → `MemoryComponent._render_l3_only` 渲染进 prompt（按 text 字段，非"识别合成 ID"） |
| T4-5 | `facts_weight` 漏传（默认 0.0） | 回归测试：显式验证生产构造传了 0.2，否则 fail |
| T4-6 | `enhanced_retriever` on 但 facts 表空 | 不报错，退化为老 RRF |

### TG-5 · chunking + query rewriting（WI-M1.5）

| # | 用例 | 断言 |
|---|---|---|
| T5-1 | `chunking` on，append 长消息 | `messages_chunks` 多行；召回命中返回 parent message |
| T5-2 | `chunking` off | 仍全消息单向量，`messages_chunks` 不写 |
| T5-3 | chunk backfill 脚本 | 历史长消息被切块入表 |
| T5-4 | `query_rewrite` on，短 query | rewriter 被调一次 |
| T5-5 | `query_rewrite` on，长 query | 不改写 |

### TG-6 · 工作记忆（WI-M1.6）

`test_memory_workspace_integration.py`

| # | 用例 | 断言 |
|---|---|---|
| T6-1 | `workspace_memory` on，调 **async** `file_write` 成功 | `workspace_state` 表落行，action="write" |
| T6-2 | `file_read` 同一 path | 同行 `last_action` 更新为 "read" |
| T6-3 | flag off | `record_action` 零调用 |
| T6-4 | `file_write`/`file_read` 改 async 后 | 全套既有工具测试回归 0 倒退（评审 R9） |
| T6-5 | code task 装配 | `WorkspaceMemoryComponent` 在该 task 组件列表里（已加进 policy `prefer`）；非 code task 不装 |
| T6-6 | `workspace_recall` 工具 | 查回本 session 改过的文件 |

### TG-7 · reflection + skill memory（WI-M1.7）

| # | 用例 | 断言 |
|---|---|---|
| T7-1 | `reflection` on，手动 `ReflectionWorker.run_once()` | 产物写进 **`facts` 表**（category="reflection"） |
| T7-2 | `SkillMemoryStore` 独立 CRUD | procedural 记录可写可查（与 ReflectionWorker 无耦合） |
| T7-3 | flag off | lifespan 不注册定时任务 |
| T7-4 | reflection 跑时无可用 LLM | 跳过本次，不报错 |

### TG-8 · eval 回归门控

| # | 用例 | 断言 |
|---|---|---|
| T8-1 | `eval build` | 从（主 checkout 拷贝的）真实库 + 中文 fixture 造回测集 |
| T8-2 | `eval run`（第一代 baseline） | 产出 hit@1/5/MRR + **token_per_query**（口径 = 召回渲染进 system prompt 的文本 token） |
| T8-3 | `eval run`（rerank 单开） | hit@5 不回归，期望可见提升 |
| T8-4 | `eval run`（facts+enhanced 全开） | hit@5 ≥ baseline +15% |
| T8-5 | `eval run`（全 flag 开） | token_per_query 增幅 ≤ +30% |
| T8-6 | `scripts/eval_gate.*` pre-merge 脚本 | 指标回归时非零退出（自动化门控） |

### TG-9 · 全套回归

| 套件 | 通过线 |
|---|---|
| backend pytest（flag 全关） | **0 回归**（含老三层用例） |
| backend pytest（flag 全开） | 全绿 |
| frontend vitest | 0 回归（仅 WI-M1.1 加 thumbs-up） |
| Rust cargo test | 0 回归 |

### B-末 · 完成定义

每个 WI = 对应 TG 用例全绿 + TG-9 回归 0 倒退 + flag 可独立开关 + PRD §7
达成。**集成测试（TG-2~TG-7）绿才算完成,单元测试绿不算。**

---

## C. 实施顺序

1. WI-M0.3 config → M0.1 体检(固化 smoke) → M0.2 baseline + 回测集 fixture。
2. WI-M1.1 反馈回路 + WI-M1.2 facts shadow(+ backfill)。**WI-M1.3 reranker
   与之并行**（不依赖 facts）。
3. 观察窗（真实聊天 + backfill + 人工抽查 facts 质量）。
4. WI-M1.4 EnhancedRetriever 接管（facts 向量召回）。
5. WI-M1.5 chunking + query rewrite / WI-M1.6 工作记忆（可与 M1.4 并行）/
   WI-M1.7 reflection。
6. 每步结束跑 TG-8 eval 门控 + TG-9 回归。

---

## D. 实测结果（2026-05-23）

### 自动化测试 —— 全绿

| 测试组 | 文件 | 结果 |
|---|---|---|
| TG-0 体检 smoke | test_memory_v2_smoke.py | 10/10 ✅ |
| TG-1 config flag | test_memory_v2_config.py | 5/5 ✅ |
| TG-2 facts 接入集成 | test_memory_facts_integration.py | 8/8 ✅ |
| TG-3 reranker 集成 | test_memory_rerank_integration.py | 4/4 ✅ |
| TG-4 EnhancedRetriever 集成 | test_memory_enhanced_retriever_integration.py | 5/5 ✅ |
| TG-5 chunking+rewrite 集成 | test_memory_chunk_query_integration.py | 6/6 ✅ |
| TG-6 工作记忆集成 | test_memory_workspace_integration.py | 7/7 ✅ |
| TG-7 reflection 集成 | test_memory_reflection_integration.py | 4/4 ✅ |
| TG-8 eval 门控 | test_memory_eval_gate.py | 4/4 ✅ |

### TG-9 全套回归

- **backend pytest（flag 全关）**: 1799 passed, 13 skipped, **0 回归** ✅
- **backend pytest（flag 全开）**: 集成测试 TG-2~8 全绿 + `main.py` flag 全开
  成功 boot（`EnhancedRetriever` 接管、facts/chunker/workspace 全部 wire）✅
- **frontend vitest**: 279 passed (20 files), 0 回归 ✅
- **frontend tsc --noEmit**: 0 error ✅
- **Rust cargo**: 环境受限 —— 构建脚本要求已打包后端产物
  `backend/dist-portable/deskpet-backend`（本 worktree 无）。本轮**零 Rust
  改动**，无回归可能。

### eval 门控（TG-8）

`python -m scripts.eval_gate` 连续跑结果一致、判定 **PASS**。baseline 钉在
`deskpet/memory/eval/zh_baseline.json`（hit@5=0.1143, token_per_query=198.23，
mock embedder + 中文 fixture，详见 STAGE0-baseline.md）。

### 接入确认（DoD：wire 进 main.py）

`import main` flag 全开 boot 日志确认：`p4_fact_extractor_ready`、
`p4_enhanced_retriever_ready rerank=True enhanced_retriever=True
query_rewrite=True chunking=True`、`p4_workspace_memory_ready`、
`p4_skill_memory_store_ready`、`p4_reflection_worker_scheduled`。
flag 全关时 `_retriever_for_mm` 为裸 `Retriever`，无任何 v2 表创建。
