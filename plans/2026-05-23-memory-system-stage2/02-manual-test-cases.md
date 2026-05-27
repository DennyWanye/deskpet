# 人工测试用例 — DeskPet 记忆系统 Stage 2

**关联**: `00-PRD.md` / `01-TDD.md`（**第 2 版**）
**状态**: **第 2 版** —— 已过架构评审 round1，按评审意见修订
**执行**: windows-mcp / computer-use 实机操作 DeskPet dev 实例 + sqlite3 查表 + 后端日志
**定位**: 自动化测试（TDD）验证"代码行为正确"；本文验证"用户体感上 Stage 2 缺陷真的解决了 + Stage 1 零回归"。
**核心针对**：Stage 1 实测暴露的 5 个真实问题（PRD §1.1 P1-P5），每个对应至少一个 MR。

> ## 第 2 版修订要点
> 1. MR-S2-1-9 误判率统计 N=10 → **N=30**，目标 ≤ 20% → **≤ 15%**（评审 B2 statistical power）
> 2. MR-S2-2-7 改成验证**工具自身规则拦截**（不靠 LLM 二次确认抵御提示注入；评审 D-RISK-5）
> 3. MR-S2-8-1（老库 ALTER）列为 **M0 出门必跑**（不是 M5 才跑；评审 B4）
> 4. 新增 **MR-S2-12 facts view UI** 端到端测试（WI-S2.1b）
> 5. 新增 **MR-S2-13 R-MISS-2 被遗忘 fact 防覆盖** 专项测试
> 6. 新增 **MR-S2-14 strict CI 自动化触发** 验证

---

## 0. 测试环境

1. 在隔离 worktree 启动 dev 实例 —— `powershell -File scripts/dev-worktree.ps1 -Port 8201 -ViteForceNoUse`（**新端口 8201/5274** 避免与 Stage 1 worktree 8200 冲突）
2. 后端"已连接"（toolbar 绿色徽章）；需要**支持 function-calling 的 LLM**（cross-key merge / entity 抽取 / memory_forget 自然语言模式都依赖）
   - 推荐 the relay/deepseek-chat（已知通；其它 the relay 模型 503 见 followup B2）
3. DPI 150%：坐标换算 逻辑=物理/1.5；中文输入用剪贴板 `Set-Clipboard` + Ctrl+V
4. 查后端表：`sqlite3 .dev-userdata-stage2/data/state.db` 或后端日志
5. 每组用例标注所需 feature flag，测前在 `.dev-userdata-stage2` 的 `config.toml` 把对应 `[memory.v2]` flag 设好并重启后端
6. **Stage 1 flag 全开**（`feedback_loop / facts_extract / rerank / enhanced_retriever / chunking / query_rewrite / workspace_memory / reflection` 全 true）—— 因为 Stage 2 是 Stage 1 的增量，flag off 测试假设 Stage 1 全开

> 环境障碍（坐标/输入法/端口/LLM 不可用）绕过并记录，不算功能 bug。

---

## 1. 测试用例

### MR-S2-0 · Stage 2 零回归（对照组，Stage 2 flag 全关，Stage 1 全开）— 一票否决

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-0-1 | Stage 1 全开 + Stage 2 全关启动 | 后端正常起；boot 日志无 `cross_key_merge_enabled` 等 |
| MR-S2-0-2 | 跑 MR-1-1（"我对花生过敏"）→ MR-1-3（短消息）→ MR-2-2（推荐零食） | 行为与 Stage 1 实测第 4 轮**逐项一致**（facts 表行数、向量召回命中率） |
| MR-S2-0-3 | 跑老库 ALTER TABLE 加 superseded_by 列后再走 MR-S2-0-2 | 行为仍一致（superseded_by 是新列但不被读 / 写） |
| MR-S2-0-4 | 跑 `python -m scripts.eval_gate`（默认） | PASS（baseline 不变） |
| MR-S2-0-5 | backend pytest（flag 全关跑全套） | 1799+ 用例 0 回归（含 Stage 1 全部）|

**通过条件**：Stage 2 flag 全关时，记忆行为与 Stage 1 第 4 轮完成时**逐项一致**，无任何 Stage 2 副作用。

### MR-S2-1 · 跨 key 矛盾治理（flag: `cross_key_merge`）★★★ 用户最易感知

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-1-1 | flag on 启动；说"我对花生过敏" | facts 表插 fact A（subject=user, key 约 `allergy_peanut`, active）|
| MR-S2-1-2 | **隔 3-5 轮其他对话** | fact A 仍 active |
| MR-S2-1-3 | 说"其实我搞错了，不是花生，我对海鲜过敏" | 后端日志可见 `cross_key_conflict_detected old_id=<A.id>`；fact A `is_active=0, superseded_by=<B.id>`；新 fact B（key 约 `allergy_seafood`, active） |
| MR-S2-1-4 | sqlite3 查 `SELECT * FROM facts WHERE is_active=1 AND subject='user'` | 只看到 B；A 不出现 |
| MR-S2-1-5 | 问"给我推荐个零食" | 回复**避开海鲜**（不再避开花生）；说明 RRF 召回拿到的是 B 不是 A |
| MR-S2-1-6 | 反向验证：再说"其实我又不过敏海鲜了，是过敏麸质" | fact B 标 superseded，新 fact C 上位；A 仍是 inactive（不复活） |
| MR-S2-1-7 | **非矛盾对照**：说"我喜欢咖啡"再说"我也喜欢徒步" | 两条都 active，不互相 supersede（不同领域不应判矛盾）|
| MR-S2-1-8 | **fact 扩展对照**：说"我对花生过敏" 再说"对所有坚果都过敏（不只是花生）" | LLM 可能判扩展或 supersede，**任一都可接受**；不应**两条 active 并存且矛盾** |
| MR-S2-1-9 ★v2 | **误判抽查**：连续跑 MR-S2-1-1 ~ 8 共 **30 次**新 fact 写入（5 个矛盾对各跑 6 次），统计 cross-key 误判次数（不矛盾但被标 superseded） | 误判率 **≤ 15%**（30 次中 ≤ 4 次误判；提升 statistical power）|

### MR-S2-2 · memory_forget 工具 + UI（flag: `memory_forget`）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-2-1 | 让桌宠记一条 fact（"我家猫叫旺财"）| facts 表有该 fact，pet_name=旺财 |
| MR-S2-2-2 | 在 chat 里说"忘记我家猫" | agent 调 memory_forget 工具（query="我家猫"或类似）；LLM 二次确认；fact `is_active=0`；回复确认 |
| MR-S2-2-3 | 再问"我家猫叫什么" | 回复"不知道"或类似（fact 已忘）|
| MR-S2-2-4 | 打开 MemoryPanel；点某条 fact 的 🗑 按钮 | 该卡片即时移除；5 秒 undo 浮窗出现 |
| MR-S2-2-5 | undo 浮窗 5 秒内点撤销 | fact 重新 active；卡片恢复 |
| MR-S2-2-6 | 重复 MR-S2-2-4 但等 5 秒超时 | fact 永久 inactive；下次启动 panel 仍不显示 |
| MR-S2-2-7 ★v2 | **工具自身规则拦截**："忘记所有我说过的话"（query 长度足但过宽）| 工具自身规则拦截（命中 > 5 fact → status=skipped）；**不靠 LLM 二次确认**（评审 D-RISK-5 提示注入防护）|
| MR-S2-2-7b ★v2 | query 长度 < 6 字（"忘了"）| 工具自身规则拦截（query 长度过短 → status=skipped）|
| MR-S2-2-7c ★v2 | `enable_natural_language=false` 时 query 模式 | 走 `_forget_by_query` 仍 OK 但应在更上层（工具调用前）被禁；agent 拿不到工具 schema 的 query 描述 → 不会用 query 模式 |

### MR-S2-3 · entity 索引（flag: `entity_path`）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-3-1 | 建几条带实体的 fact："我家猫叫旺财（三岁，橘色）"、"老李上周给我推荐了一本书《XXX》" | facts 表有这几条 |
| MR-S2-3-2 | 隔几轮无关对话；问"旺财怎么样了" | 召回结果含旺财相关 fact（后端 trace 显示 entity 路命中） |
| MR-S2-3-3 | 问"老李上次说什么" | 召回结果含老李相关 fact |
| MR-S2-3-4 | 关 `entity_path` flag 重启；再问"旺财怎么样了" | 召回**可能不含** facts（语义召回靠 BGE-M3，但 entity 直命中没了）；对比 flag on 时的 trace 看 entity 路 hit 数差异 |
| MR-S2-3-5 | LLM 不可用时（kill 中转）问 entity-含 query | 降级 regex；regex 仍能提"旺财"、"老李"；entity 路仍工作 |
| MR-S2-3-6 | 纯英文 query "what about Mike" | regex 仍能提 Mike；entity 路命中 |

### MR-S2-4 · episodic → semantic 固化（flag: `episodic_to_semantic`）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-4-1 | 跑 ≥ 3 个旧 session（each ≥ 20 messages，含偏好类用户话语如"我喜欢冬天"）；伪造 created_at 到 31 天前（或调 `age_days=0` 临时）| `messages` 表有 60+ 条 |
| MR-S2-4-2 | flag on；触发 summarizer（IPC 或手动 CLI） | summarize_old_sessions 完成；`messages` 表新增 N 条 is_summary=1 系统消息；asyncio.create_task 异步派出 |
| MR-S2-4-3 | 等 LLM 抽取完成（30 秒+） | facts 表新增 ≥ 1 条 `category='episodic_summary'`，value 含"我喜欢冬天"或类似偏好 |
| MR-S2-4-4 | flag off 跑同 summarizer | summary 落表；facts 表**不新增** episodic_summary |
| MR-S2-4-5 | summary 抽出的 fact 与已有 user fact 矛盾时（人为构造："我喜欢咖啡" user fact 已存在，summary 里抽出"用户改喝茶了"） | `cross_key_merge=true` 配合下，老 fact 标 superseded；新 fact 上位 |
| MR-S2-4-6 | 抽出 fact 的 source_msg_id 字段 | 指向 summary message id（不是原始 user 消息）|

### MR-S2-5 · eval 门控严格化（脚本级，无 flag）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-5-1 | `python -m scripts.eval_gate`（默认） | PASS（当前 baseline 不回归）|
| MR-S2-5-2 | `python -m scripts.eval_gate --strict` 当前代码 | **FAIL**（hit@5 = baseline，不 strict 大于）；exit != 0 |
| MR-S2-5-3 | 实装 Stage 2 全 flag 后跑 `--strict` | hit@5 > baseline；PASS（说明 Stage 2 真的提升了召回质量）|
| MR-S2-5-4 | 手工把 baseline.json 的 hit@5 从 0.4286 改成 0.50（伪造提升），跑 `python -m scripts.eval_gate --update-baseline` | 拒绝写入；提示"加 --force"；exit 3 |
| MR-S2-5-5 | 同 MR-S2-5-4 加 `--force` | 写入成功；exit 0 |
| MR-S2-5-6 | 手工把 baseline.json 的 hit@5 从 0.4286 改成 0.30，跑普通 eval_gate | hit@5 当前 0.4286 > 0.30，PASS |
| MR-S2-5-7 | 改 baseline token_per_query 从 195 → 500，跑 `--update-baseline`（当前 token 195）| 拒绝写入（不能钉高 token）；exit 3 —— **注意：这条要看 sanity 逻辑是单向的还是双向，TDD §A3.2 暗示只防钉低 hit、钉高 token；token 钉低不阻止** |

### MR-S2-6 · MR-4 GUI 端到端联调（B1 收尾，主 checkout）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-6-1 | 主 checkout 起 backend + tauri dev（**主 checkout，不是 worktree**） | 桌宠正常出现；后端绿 |
| MR-S2-6-2 | 进 code mode；新建一个空目录 `D:\tmp\stage2-mr4\`；让 agent "在 D:\tmp\stage2-mr4 下读 README.md，基于内容生成 summary.md" | agent 完成；`workspace_state` 表新增 2 行（README.md read, summary.md write） |
| MR-S2-6-3 | **同 session** 让 agent "再看一下刚才那个 summary.md" | agent 的 system prompt 应含工作记忆段（log 抓证据）；agent **可能不重新 read_file**（直接基于工作记忆回复）|
| MR-S2-6-4 | flag off (`workspace_memory=false`) 起 fresh session 同任务跑 | 对比 file_read 调用数：flag on ≤ flag off |
| MR-S2-6-5 | 全程录屏；表 dump；调用计数表归档到 `evidence/2026-05-23-mr4-e2e/` | 证据完整 |

### MR-S2-7 · flag 一键回退（与 Stage 1 MR-7 同款）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-7-1 | 全 Stage 2 flag 开用一阵 → 把 `cross_key_merge` 改 false 重启 | 该能力干净关；FactExtractor 走旧路径；其他 Stage 2 flag 仍生效 |
| MR-S2-7-2 | 同上独立切 `entity_path` / `episodic_to_semantic` / `memory_forget` | 每个独立可关；不互相影响 |
| MR-S2-7-3 | 全 Stage 2 flag 关 + Stage 1 全开重启 | 完全回到 Stage 1 第 4 轮结束时的行为（同 MR-S2-0）|

### MR-S2-8 · 老库兼容性（schema migrator，**M0 出门必跑 ★v2**）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-8-1 ★v2 | **M0 第一刀**：拿 Stage 1 第 4 轮结束时的主 checkout `state.db` 真实副本（不是 fresh fixture）→ 启 Stage 2 backend | boot 日志可见 `v2_migrator: ALTER TABLE facts ADD superseded_by OK` 和 `ADD forgotten_at OK`；表结构含双新列 |
| MR-S2-8-2 | 重启后再 boot | `ensure_memory_v2_columns` 为 no-op；不报错 |
| MR-S2-8-3 | 老 fact 行的 superseded_by / forgotten_at | 自动为 NULL（默认值正确） |
| MR-S2-8-4 | 跑 `python -m scripts.facts_conflict_cleanup --max-subjects 5 --llm-budget 20 --dry-run` | 扫前 5 个 subject；最多消耗 20 次 LLM 调用；dry-run 不改表 |
| MR-S2-8-5 ★v2 | 模拟 ALTER 失败（如把 db 文件设只读） | boot 不崩；warning log；`cross_key_merge` 和 `memory_forget` flag 自动关闭；boot 日志含 "availability 缺 superseded_by → flag disabled" |
| MR-S2-8-6 ★v2 | cleanup 脚本断点续跑 | `--llm-budget 10` 跑完后退出（exit code 区分扫完/budget 耗尽）；再次跑带 resume token 从上次位置继续 |

### MR-S2-9 · 性能与延迟验证

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-9-1 | cross-key merge 开启时 user 发消息 | facts 抽取 + cross-key check 异步；user message → response 延迟与 Stage 1 同（≤ +200ms）|
| MR-S2-9-2 | entity 路开启时 recall() 延迟 | 加 entity 路后 recall 延迟 ≤ Stage 1 × 1.2（多一次 LLM/regex + LIKE）|
| MR-S2-9-3 | summarizer 跑完后 fact_extractor 异步延迟 | 不阻塞 summarizer 返回；后台 30-60 秒完成抽取 |
| MR-S2-9-4 | 跑 eval_gate `--strict` 实际耗时 | < 2 分钟（mock embedder + 35 条 fixture）|

### MR-S2-10 · facts_conflict_cleanup 老库批量清理

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-10-1 | 准备测试 DB：含 5 个 subject，每个有 2-3 条 cross-key 矛盾 facts（人工构造） | facts 表 ~12 行 active |
| MR-S2-10-2 | `python -m scripts.facts_conflict_cleanup --dry-run` | 输出"will mark superseded: ..."列表；DB 未变 |
| MR-S2-10-3 | `python -m scripts.facts_conflict_cleanup`（正式跑） | 矛盾 fact 标 superseded；list_active 缩减 |
| MR-S2-10-4 | 再次跑 cleanup（幂等性） | 输出"no conflicts found"；DB 不变 |
| MR-S2-10-5 | `--max-subjects 2` 限制 | 只扫前 2 个 subject；其他不动 |

### MR-S2-11 · 文档与 evidence 完整性

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-11-1 | TDD §D 实测结果回填 | TG-S0~S8 全标 ✅；接入日志清单列全 |
| MR-S2-11-2 | `plans/2026-05-23-memory-system-status.md` 更新 | A1/A2/A3/A4/B1 5 项标 ✅；followup 剩余只有 B2 / C.* |
| MR-S2-11-3 | `evidence/2026-05-23-mr4-e2e/` 含录屏 + dump + diff | 文件齐全 |
| MR-S2-11-4 | PR 描述含 eval_gate strict PASS 截图 | 截图清晰 |

### MR-S2-12 · MemoryPanel facts view UI（WI-S2.1b ★v2 新增）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-12-1 | 桌宠面板 → MemoryPanel → 切到 "事实" tab | facts view 渲染；显示 active facts 列表（按 updated_at 倒序）|
| MR-S2-12-2 | 列表中每条 fact 卡片 | 显示 `key: value`（突出）+ category 徽章 + updated_at 时间；右上角 🗑 按钮 |
| MR-S2-12-3 | 点击某条 fact 的 🗑 按钮 | 卡片立即移除；底部出现 5 秒倒数 undo 浮窗"已忘记 X，撤销？" |
| MR-S2-12-4 | undo 浮窗 5 秒内点"撤销" | 后端 `memory_forget_undo` ws；fact 恢复并 prepend 回列表 |
| MR-S2-12-5 | undo 浮窗等 5 秒超时（不点）| 浮窗自动消失；后端 `restore_from_undo` 已超窗口拒绝 |
| MR-S2-12-6 | 5 秒超时后伪造调用 undo（开发者控制台 dispatch）| 后端返 status=expired；前端不恢复 |
| MR-S2-12-7 | 快速连点 5 个不同 fact 的 🗑（≤ 1 秒）| 5 个 op_id；5 个 undo 浮窗叠加 / 或最新一条覆盖（UX 决策，二选一明确）|
| MR-S2-12-8 | 关闭 MemoryPanel 再打开（pendingForget 状态丢） | 重新拉 facts 列表；已 forgotten 的不显示 |

### MR-S2-13 · R-MISS-2 被遗忘 fact 防覆盖（★v2 专项）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-13-1 | 让桌宠记 "我对花生过敏" | fact 写入 |
| MR-S2-13-2 | 点 🗑 删除该 fact（确认 forgotten_at 落库） | fact `is_active=0, forgotten_at=now()` |
| MR-S2-13-3 | 1 分钟后再说 "我对花生过敏" | FactExtractor 触发 `is_forgotten_recently(within_days=7)` → True → **跳过插入**（log: `Skip fact: forgotten within 7 days`）|
| MR-S2-13-4 | sqlite3 查 facts 表 | 无新 active fact；老 fact 仍 inactive |
| MR-S2-13-5 | 8 天后（伪造时间戳）再说同样的话 | `is_forgotten_recently` 返 False → 正常插入 |

### MR-S2-14 · strict CI 自动化触发（★v2 D-RISK-3）

| 步骤 | 操作 | 预期 |
|---|---|---|
| MR-S2-14-1 | 本地跑 `bash backend/scripts/eval_gate_ci.sh`（HEAD 无召回改动）| 不加 strict；走默认 gate；输出 "未检测召回改动" |
| MR-S2-14-2 | 改 `backend/deskpet/memory/enhanced_retriever.py` 后再跑 | echo "召回相关改动检测 → --strict"；strict 模式执行 |
| MR-S2-14-3 | GitHub PR 创建（包 enhanced_retriever.py 改动） | `.github/workflows/eval-gate.yml` 触发；job 跑 `eval_gate_ci.sh` 加 strict；PR 状态显示 |
| MR-S2-14-4 | PR 仅改 README.md | workflow path filter 不触发；不跑 eval_gate |

---

## 2. 结果回报格式

```markdown
| 用例 | 通过 | 失败 | 环境受限未测 |
（MR-S2-0 ~ MR-S2-11 逐条）

功能 bug 列表（若有）：
- [MR-S2-x-y] 现象 / 复现 / 期望 vs 实际 / 证据(表内容/日志/截图)

eval 指标对比：
- 默认 gate: hit@5 / token vs baseline
- strict gate: hit@5 提升幅度

cross-key 矛盾误判率：
- N 次 fact 新写，误判 M 次 → M/N

环境障碍（已绕过，非 bug）：
- ...

结论: Go / No-Go
```

证据：facts/workspace_state 等表内容用 `sqlite3` dump；截图存
`plans/2026-05-23-memory-system-stage2/screenshots/`；MR-S2-6 录屏存
`evidence/2026-05-23-mr4-e2e/`。

---

## 3. 通过标准

- **MR-S2-0（Stage 2 零回归一票否决）必须通过 ★**
- **MR-S2-1（跨 key 矛盾）核心场景 1-3, 1-5 必须通过 ★**
- MR-S2-2 ~ MR-S2-10 全部通过，或仅因环境受限（无可用 LLM）未测且降级正确
- cross-key 矛盾误判率 ≤ 20%（MR-S2-1-9）
- entity-targeted query hit@5 ≥ 0.70（量化标准，可在 TDD TG-S7 跑）
- episodic_summary fact 数 ≥ summary 数 × 0.5
- eval_gate 默认 PASS；`--strict` 在 Stage 2 全 flag 开时 PASS
- 功能 bug = 0

---

## 4. 实测结果（动工后回填）

| 用例 | 结果 |
|---|---|
| MR-S2-0 Stage 2 零回归 | ⬜ |
| MR-S2-1 跨 key 矛盾治理 ★ | ⬜ |
| MR-S2-2 memory_forget 工具 + UI | ⬜ |
| MR-S2-3 entity 索引 | ⬜ |
| MR-S2-4 episodic → semantic | ⬜ |
| MR-S2-5 eval 门控严格化 | ⬜ |
| MR-S2-6 MR-4 GUI 端到端 | ⬜ |
| MR-S2-7 flag 一键回退 | ⬜ |
| MR-S2-8 老库兼容性（M0 必跑 ★）| ⬜ |
| MR-S2-9 性能与延迟 | ⬜ |
| MR-S2-10 facts_conflict_cleanup | ⬜ |
| MR-S2-11 文档与 evidence | ⬜ |
| MR-S2-12 facts view UI ★v2 | ⬜ |
| MR-S2-13 R-MISS-2 防覆盖 ★v2 | ⬜ |
| MR-S2-14 strict CI 自动化 ★v2 | ⬜ |

### 待动工。

---

## 5. 与 Stage 1 测试对照

| Stage 1 MR | 是否在 Stage 2 重测 | 原因 |
|---|---|---|
| MR-0 第一代零回归 | ✅ MR-S2-0-2/3/4 复用 | 一票否决必跑 |
| MR-1 facts 抽取 | ⚠️ MR-S2-1 / MR-S2-4 间接覆盖 | cross-key + episodic 都依赖 |
| MR-1-4 跨 key 矛盾 | ✅ MR-S2-1 全段（升级版） | Stage 2 真要修这个 |
| MR-1-6 临时信息防误抽 | ❌ 不重测 | Stage 1 已修 prompt（commit 03f3b15）|
| MR-2 facts 进召回 | ⚠️ MR-S2-3-1/2 间接 | entity 路是其补充 |
| MR-3 reranker | ❌ 不重测 | Stage 1 已验，无改动 |
| MR-4 工作记忆 | ✅ MR-S2-6 完整 GUI 联调 | followup B1 必跑 |
| MR-5 reflection | ❌ 不重测 | Stage 1 已通过；A4 与 reflection 独立 |
| MR-6 反馈回路 | ❌ 不重测 | 无改动 |
| MR-7 flag 一键回退 | ✅ MR-S2-7 | Stage 2 新增 4 flag 都要测 |
| MR-8 worktree 隔离 | ⚠️ 启动期已包含 | 新 worktree 端口 8201/5274 |
