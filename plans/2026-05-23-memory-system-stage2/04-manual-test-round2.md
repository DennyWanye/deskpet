# 人工测试 round 2 — 真 LLM + master 分支 — 2026-05-24

**测试者**: Claude opus 4.7（主对话）
**测试范围**: MR-S2-0 ~ MR-S2-14 — 真 LLM (chinzy + deepseek-v4-pro，账号 <dev-test@example.com>) + master 分支
**对比 round 1**: round 1 全是 mock LLM 模拟；本轮**真 LLM 调用** + **真发现 2 个 PRD 设计 bug**
**分支**: `master`（feat/memory-stage2 已 fast-forward merge 进来）
**Backend**: 真启动 (dev-start) + `[memory.v2]` 全 flag = true

---

## 1. 真发现的 PRD/实现 bug（master 直接 fix）

### Bug #1 — cross_key prompt 缺 NEW fact evidence

**症状**：MR-S2-1 chat "我对花生过敏" → 隔几轮 → "其实我不过敏花生，是过敏海鲜"
→ 真 LLM (deepseek-v4-pro) 判 `conflicts=[], should_insert=true` →
两条 fact 都 active，cross_key 没工作。

**LLM 推理原文（response）**：
> "These are different allergens... a person can be allergic to both
> seafood and peanuts. ... That is a contradiction only if the user
> explicitly said they were wrong about peanut. **In this scenario,
> we do not have any such context. We just have the facts.**"

**根因**：[backend/deskpet/memory/facts.py](backend/deskpet/memory/facts.py) 的
`_CROSS_KEY_CONFLICT_PROMPT` 只传 `(id, key, value)` 给 LLM，**没传 NEW
fact 的 evidence** —— LLM 看不到 user 原话"我不是过敏花生，是过敏海鲜"
里的修正信号，自然判"两个过敏原可以共存，不矛盾"。

**修复**：prompt 加 `new_evidence` 字段（normalize 已截到 200 字），
加上明确的判定规则 ("如果 evidence 含 '其实不是 X，是 Y' / '搞错了'
等修正信号 → 标 superseded")。同步加 TS1-7 断言：candidate evidence
不入 prompt（控长），NEW evidence 必入。

**验证**：[plans/.../round2_smoke/inproc_cross_key.py](plans/2026-05-23-memory-system-stage2/round2_smoke/inproc_cross_key.py)
重跑真 LLM → `id=7 peanut_allergy active=0 superseded_by=8` ✅

### Bug #2 — `_parse_extracted` / `_parse_*` 不剥 `<think>` reasoning 块

**症状**：MR-S2-4 episodic→semantic 跑 summarizer → bg_tasks=1 ✅，但
facts 表 `episodic_summary` 行数 = 0 ❌。逐层 probe 发现：
- LLM 抽出 2 条合法 JSON fact ✅
- `_parse_extracted(raw)` 返 0 条 ❌

**根因**：DeepSeek-V4-pro / Claude thinking / Gemini reasoning 等模型
常在 JSON 输出**前**附 `<think>...</think>` 推理块，块内字符（`[`、`{`、
`"`）会让 `find("[") / rfind("]")` 截到错的范围，`json.loads` 失败。
影响 4 个 parser：
- `_parse_extracted`（fact 抽取）
- `_parse_cross_key_decision`（cross-key 矛盾决策）
- `_parse_merge_decision`（同 key merge 决策）
- `memory_tools._parse_confirm_response`（forget 确认）

**这是 round 1 没发现的因为 mock LLM 输出永远是干净 JSON。真 LLM 1/N
概率返带 think 块（即使 temperature=0）。**

**修复**：新增 `_strip_reasoning_blocks(text)` helper，正则剥
`<think>...</think>` / `<thinking>...</thinking>` / `<reasoning>...</reasoning>`
（covers DeepSeek/Claude/Gemini），4 个 parser 全部接入。

**验证**：[plans/.../round2_smoke/inproc_episodic.py](plans/2026-05-23-memory-system-stage2/round2_smoke/inproc_episodic.py)
重跑真 LLM → `episodic_to_semantic: summary=1735 → 2 facts persisted` ✅

---

## 2. MR 执行结果表

| MR | 状态 | 证据 |
|---|---|---|
| **MR-S2-0** Stage 2 零回归 | ✅ | backend pytest 1883 passed / 10 skipped / 0 failed（含 2 bug fix 后无回归）|
| MR-S2-0-3 老库 ALTER | ✅ | userdata state.db 真 ALTER `superseded_by` + `forgotten_at` 成功；后续 SQL 正常 |
| **MR-S2-1** 跨 key 矛盾治理 ★ | ✅ FIX 后 | 真 LLM "我对花生过敏" → "其实是过敏海鲜" → `id=7 peanut_allergy active=0 superseded_by=8`；fix 前 LLM 判不矛盾，fix 后正确判矛盾 |
| MR-S2-1-7 非矛盾对照 | ✅ | TG-S1 测试覆盖 |
| MR-S2-1-9 N=30 误判率 | 🔘 待延后 | 需 30 次真 LLM 调用统计；chinzy ReadError 抖动严重，建议留待延后批跑 |
| **MR-S2-2** memory_forget 工具 | ✅ | TS2-1~13 14/14 + agent 真调到 `memory_forget` 工具（backend log `p5s2_tool_call_args_dump name=memory_forget`）+ `enable_natural_language=False` 拦截生效 |
| MR-S2-2-4 / **MR-S2-12** UI 真点击 | 🔘 环境受限 | dev 模式桌宠窗口位置在 -21333,-21333 屏幕外；vitest 18/18 + ws 路由集成 6/6 已覆盖逻辑层；UI 视觉验证需用户在主屏拖回窗口 |
| **MR-S2-3** entity 索引 | ✅ | 真 LLM extractor 抽 "旺财"/"老李"/"Mike" 正确；find_by_entities 命中正确 fact；"今天怎么了" 被停用词过滤；纯 Mike query 命中 0（fact 表无）正常 |
| **MR-S2-4** episodic→semantic | ✅ FIX 后 | summarize_old_sessions 跑通 22 条老消息 → summary id=1735 → bg_task 异步抽 2 条 `episodic_summary` fact (favorite_season/favorite_drink) |
| **MR-S2-5** eval_gate 严格化 | ✅ | 默认 PASS hit@5=0.4286 baseline；strict 按 PRD §A3.1 设计 FAIL (持平 baseline 未超 +0.02)；sanity 拒钉低 hit ✅；ci.sh 自动触发 strict ✅ |
| MR-S2-5-3 全 flag 开后 strict PASS | 🔘 待延后 | 需 fixture 把 Stage 2 路径接进 eval_gate 跑（B2 followup）|
| **MR-S2-6** MR-4 GUI 端到端 | 🔘 环境受限 | e2e_workspace_memory.py 脚本就绪 + evidence dir；需用户在主 checkout 跑录屏 |
| **MR-S2-7** flag 一键回退 | ✅ | userdata config 4 个 flag 全开后 boot：`p4_fact_extractor_ready cross_key_merge=True` + `p4_entity_path_enabled` + `p4_memory_forget_tool_bound` + 无 `MemoryV2Config ignoring unknown keys` warning |
| **MR-S2-8** 老库兼容 | ✅ | userdata state.db 是 round 1 老库（无 superseded_by 列）→ ALTER 成功；availability `{'superseded_by': True, 'forgotten_at': True}` |
| MR-S2-8-5 ALTER 失败禁 flag | ✅ | TS0-4 单测覆盖 |
| **MR-S2-9** 性能 | 🔘 待延后 | chinzy ReadError 抖动严重，准确压测需稳定 provider |
| **MR-S2-10** cleanup 批量 | ✅ | TS0-7 + cleanup --dry-run --max-subjects 参数齐 |
| **MR-S2-11** 文档归档 | ✅ | TDD §D 回填 + round 1 + 本 round 2 报告 |
| **MR-S2-13** R-MISS-2 防覆盖 ★ | ✅ | 真 LLM forget peanut → 重提 "我对花生过敏" → `persisted: []`（被 is_forgotten_recently 跳过）→ 8 天后伪造 → 正常 insert 新 fact |
| **MR-S2-14** strict CI 自动化 | ✅ | TS4-9/10 + 实测 ci.sh git diff 触发 |

---

## 3. eval 指标

| 项 | 结果 |
|---|---|
| 默认 gate hit@5 / token | 0.4286 / 195.86 = baseline ✅ |
| strict gate | FAIL（持平 baseline，未超 +0.02 容差）— PRD §A3.1 设计预期 |
| sanity 拒钉低 hit@5 | exit 3 ✅ |

---

## 4. 自动化测试结果（fix 后）

- backend pytest: **1883 passed / 10 skipped / 0 failed**（173s）
- 107 个 Stage 2 集成 + 单元测试全绿
- frontend vitest: 21 files / 297 tests passed
- frontend tsc: 0 error

---

## 5. 结论

**功能 bug**: 2 个 (已发现 + 已修复)，**0 个未修**

### Round 1 vs Round 2 对比

| 维度 | Round 1 (opus 4.7 subagent + mock) | Round 2 (主对话 + 真 LLM) |
|---|---|---|
| 自动化 pytest | ✅ 1882 passed | ✅ 1883 passed (含 fix) |
| cross_key 矛盾（真 LLM） | 未测 | ❌→✅ 发现 prompt 缺 evidence，已 fix |
| episodic→semantic（真 LLM） | 未测 | ❌→✅ 发现 think 块 parse bug，已 fix |
| entity 索引（真 LLM） | 未测 | ✅ |
| R-MISS-2（真 LLM） | 未测 | ✅ |
| memory_forget 工具 | mock ws | ✅ agent 真调 + 拦截生效 |
| UI 真点击 | 不可测 | 🔘 桌宠窗口位置异常（dev 模式 bug，非 Stage 2 引入） |
| MR-4 GUI 录屏 | 不可测 | 🔘 待用户在主 checkout 跑 |

**Go**: 核心功能层全 ✅；MR-S2-2/12 UI 真点击 + MR-S2-6 GUI 录屏 + N=30
误判率 + 性能压测留待后续 round（需主屏桌宠 + 稳定 LLM provider）。

证据脚本：[plans/2026-05-23-memory-system-stage2/round2_smoke/](plans/2026-05-23-memory-system-stage2/round2_smoke/)
- `inproc_cross_key.py` - cross_key 矛盾真 LLM 验证
- `inproc_r_miss_2.py` - R-MISS-2 防覆盖
- `inproc_episodic.py` - episodic→semantic 端到端
- `inproc_entity.py` - entity 索引
- `smoke_cross_key.py` - 走 chat_v2 ws 路径（chinzy ReadError 抖动太严重未完整跑通）
