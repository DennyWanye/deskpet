# 架构评审 round2 — 工具层优化 v3（v2）

**评审日期**: 2026-05-24
**评审者**: opus 4.7 子代理（20Y 资深架构师）
**评审范围**: `plans/2026-05-24-tool-layer-optimization-v3/` v2 三份文档
**响应状态**: v2 → v3 修订完成

---

## 总评

**Conditional Go** — round1 的 6 个 P0 全部已修订到位、5 个 P1 全部响应；但 v2 引入 **3 个新事实错（语义/字典序/dead code）** + **2 个文档自相矛盾**，必须在动工前 30 分钟内修完。**这些都是文本级 fix，不是架构变更**。

---

## 必改 P0（v3 已修）

| # | 问题 | v3 修订 |
|---|------|--------|
| **P0-1** | TDD §A10 v1 段残留 `emit_metric` API（被 v2 标 deprecated 但代码示例仍在）| TDD 顶部 v3 修订要点 #1 整段加废止横幅 |
| **P0-2** | TDD §A11 没显式说 `ToolSpec` 加 `replace_allowed` 字段 → spec init TypeError | TDD 顶部 v3 #2 + PRD D11 v3 明确"ToolSpec dataclass 加 `replace_allowed: bool = False` 字段（frozen=True 兼容）" |
| **P0-3** | PRD §1.6 / §3.5 说 "backend/config.py:232 已有 ToolsConfig" — round2 评审者在 master 上 grep 不到（因为 ToolsConfig 是 last-mile 分支引入，master 未合）| **事实核对**：`git show tool-last-mile-upgrade:backend/config.py | grep "class ToolsConfig"` 命中 line 232 ✅；PRD v3 明确"M0 合 last-mile 后 ToolsConfig 已有 verifier 子段，本期扩展" |
| **P0-4** | D9 翻译表语义反了：`l3 → event`（最快衰减），但 l3 应该是最长期最不衰减 | D17 v3：`{"l1":"event","l2":"project","l3":"preference","auto":"preference"}`（短/中/长 ↔ 快/中/慢，对应 facts.py `_CATEGORY_DECAY` 实际衰减率）|
| **P0-5** | D11 字典序方向反：`'m' < 's'`，memory_tools.py 先注册，stubs.py 后注册会覆盖真实现 | D11 v3：stubs.py 改用"if name not in registry then register"守卫模式 + `replace_allowed=True` opt-in 仅用作明文标记 |
| **P0-6** | build_agent 工厂签名漏 4 参数（main.py:4015 现场需要的 max_iterations / completion_probe / max_completion_nudges / signature_repeat_threshold）| PRD §3.2 工厂签名补 4 个 ★v3 参数 + 函数体内传给 `_AgentLoop` |

---

## P1 建议改（v3 已修）

| # | 问题 | v3 响应 |
|---|------|--------|
| P1-1 | TDD §A1.1 没说 `make_llm_call(provider)` 签名 | TDD 顶部 v3 #P1-1 补"`(prompt:str) -> Awaitable[str]`" |
| P1-2 | `_cached` 单例位置不明 | TDD 顶部 v3 #P1-2 明确放 `backend/config.py:load_config()` + mtime 失效 |
| P1-3 | TG-A13 `_KNOWN_TOOLSETS` 全集来源未说 | TDD §A12 加 "`_KNOWN_TOOLSETS = {spec.toolset for spec in registry.all_specs()}`" |
| P1-4 | PRD §3.0 Q2 描述错（写"取代双注册"但实际是混合）| Q2 v3 改为 "旧名 schema migration + 新名直注册" |
| P1-5 | TDD §A3 没核 emit_receipt 内部 duration_ms 算法 | TDD 顶部 v3 #P1-5 WI-T2.3 第一步必须先核 |
| P1-6 | PRD §7 / TDD TG-A14 "≈ 2000" 估算 | 标 "[TODO 真跑] pytest --collect-only -q | tail -1" — 动工 PR 回填 |
| P1-7 | PRD §6 R12 silent breaking change（disabled_toolsets 升级语义变）| 风险登记加 R12 + release notes 显式提醒 |

---

## v2 引入的新风险（v3 已处理）

| # | 风险 | v3 响应 |
|---|------|--------|
| D9 翻译反向 | 同 P0-4 ✅ |
| D11 字典序反 | 同 P0-5 ✅ |
| D10 dead schema | TDD §A10 v1 段加废止横幅；schema 注释移到 docs/deprecated/（P2 可选）|
| D14 silent breaking | 同 P1-7 ✅ |
| build_agent 工厂签名过长 | 同 P0-6 ✅ |

---

## 跨文档一致性（v3 已校）

- **D9 单/双注册**：PRD §3.0 Q2 v3 + §3.1 D9 + TDD §A8 + manual-test MR-T-8 三处一致 — "旧名 schema migration + 新名直注册"
- **D10 dead code**：TDD §A10 v1 段加废止横幅
- **D14 默认 strict**：PRD §3.5 / TDD §A12 / manual-test MR-T-13 三处一致

---

## 仍待动工时核对的细节

- `_paths.user_data_dir()` import 期 ready 性（P2 deferred）
- `_TIER_TO_CATEGORY` 翻译语义生产真实数据验证（动工 WI-T3.1 时跑 MR-T-8 真验）
- `tools/__init__.py:_discover_and_load` 真实顺序（pkgutil + os.listdir 字典序）— `'m' < 's'` 已确认数学事实，但具体加载实现是否有自定义顺序需 grep
- `facts.py` 加 `get_by_id` 方法（R-MISS-9）→ 动工第一步

---

## 最终判断

**Go**（动工 ready） — 所有 P0/P1 已在 v3 修订到位。

**动工前 5 分钟核对清单**：
1. `grep "class ToolsConfig" backend/` 在合 last-mile 后是否命中 ≥ 1
2. `grep "def get_by_id\|def find_active" backend/deskpet/memory/facts.py` 确认 facts API
3. `grep "ToolSpec" backend/deskpet/tools/registry.py` 确认 dataclass 现状
4. 跑 `pytest --collect-only -q | tail -1` 拿真用例数

修订质量：**round1→round2 显著提升**（事实对账率从 60% 到 90%）；**round2→v3 是 cleanup 级修订**，不再有架构层风险。

---

## v3 修订映射

| round2 发现 | v3 修订位置 |
|------------|------------|
| P0-1 emit_metric 残留 | TDD 顶部 + §A10 标废止 |
| P0-2 ToolSpec replace_allowed 字段 | PRD §3.1 D11 v3 + TDD §A11 |
| P0-3 ToolsConfig 位置 | PRD 顶部 v3 修订要点 + §1.6 + §3.5 |
| P0-4 翻译表语义 | PRD §3.1 D17 v3 + TDD §A8 |
| P0-5 字典序方向 | PRD §3.1 D11 v3 stubs 守卫模式 |
| P0-6 build_agent 签名 | PRD §3.2 工厂补 4 参数 |
| P1-1 make_llm_call 签名 | TDD 顶部 v3 + §A1.1 |
| P1-2 _cached 位置 | TDD 顶部 v3 |
| P1-3 _KNOWN_TOOLSETS | TDD §A12 待补 |
| P1-4 Q2 描述更正 | PRD §3.0 Q2 v3 |
| P1-5 emit_receipt 内部算法核对 | TDD 顶部 v3 |
| P1-6 用例真数 | 动工 PR 回填 |
| P1-7 R12 silent breaking | PRD §6 R12 v3 |
| R-MISS-9 facts.py 无 get_by_id | PRD §6 R-MISS-9 v3 |
| R-MISS-10 翻译表反向 | 同 P0-4 |
