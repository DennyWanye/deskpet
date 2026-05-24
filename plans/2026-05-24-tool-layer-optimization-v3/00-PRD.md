# PRD — DeskPet 工具层优化 v3（last-mile 接电 + stubs 真实现 + spec gap + config 扩展）

**创建日期**: 2026-05-24
**最后更新**: 2026-05-24（round2 评审后修订）
**作者**: 架构设计（20Y）
**状态**: **第 3 版（v3，终版）** —— 已过两轮架构评审（round1 + round2），按评审意见修订

> ## 第 3 版修订说明（架构评审 round2 后）
> round1 修订引入 v2，但 round2 发现 **6 个 P0 新事实错** + 多项 P1。本版 v3 修订：
>
> **必改 P0**（round2 发现）：
> 1. **TDD §A10 v1 段残留 `emit_metric` 代码** → 加废止横幅
> 2. **TDD §A11 没显式说 `ToolSpec` 加 `replace_allowed` 字段** → 显式补
> 3. **`backend/config.py:232 ToolsConfig` 在 last-mile 分支真实存在**（master 未合）→ v3 明确"M0 合 last-mile 后 ToolsConfig 已有 verifier 子段，本期在此扩展"
> 4. **D9 翻译表语义反了**（l3 应该是最长期/最不衰减）→ v3 改为 `{"l1":"event","l2":"project","l3":"preference","auto":"preference"}`（短/中/长 ↔ 快/中/慢）
> 5. **D11 字典序方向反**（'m' < 's'，memory_tools.py 先 register，stubs.py 后注册会覆盖）→ stubs.py 改用 "if name not in registry then register" 守卫，不靠 replace_allowed
> 6. **build_agent 工厂签名漏 4 参数** → 补 `max_iterations / completion_probe / max_completion_nudges / signature_repeat_threshold`
>
> **P1 + P2**（详 04-architect-review-round2.md）：
> 7. TDD §A12 明确 `_cached` 单例放在 `backend/config.py:load_config()`
> 8. TDD §A3 第一步必须先核 `emit_receipt` 内部 duration_ms 算法
> 9. PRD §3.0 Q2 描述更正为"旧名 schema migration + 新名直注册"（不是纯 schema migration）
> 10. PRD §6 加 R12 disabled_toolsets silent breaking change
> 11. `facts.py` 真无 `get_by_id` 方法 → TDD §A8 改用 `find_active(subject, key)` 或加新方法
> 12. TDD §A1.1 加 `make_llm_call(provider)` 签名注释（`(prompt:str) -> Awaitable[str]`）

> ## 第 2 版修订说明（架构评审 round1 后）
> v1 引入了 **6 处致命事实错** + **5 项设计风险** + 5 个 default 决策被挑战，按 v1 动工立即"启动崩"。本版逐条修订：
>
> **必改 P0**（6 处）：
> 1. `emit_metric` API 不存在 → 改为 `record(event, detail)`，import 路径 `from observability.metrics_sink import record`
> 2. **`memory_tools.py` 已被 memory-stage2 占用注册 `memory_forget`** → TDD §A8 改为 append 现有文件 + bind() 签名合并
> 3. D11 `ToolNameConflictError` 与 stubs.py "register replaces" 设计正面冲突 + pkgutil 字典序 memory_tools.py < stubs.py → **加 `replace=True` opt-in 参数**
> 4. master `memory_write` 的 `tier` enum 真实是 `["l1", "l2", "l3", "auto"]`（不是 `preference/profile/...`）→ 加 tier→category 翻译表
> 5. `metrics_sink` / `llm_call_func` 变量名在 main.py 不存在（grep 0 命中）→ 改为 `get_default_sink()` + `make_llm_call(local_llm)`
> 6. `_config.py` 加 `ToolsConfig` 与 `backend/config.py:232 ToolsConfig` **同名碰撞** → 改在 `backend/config.py` 已有 ToolsConfig 加字段
>
> **P0-other**：
> - `mcp_call` 第三参字段 `args` → 真实 `arguments`
> - `delegate` 字段 `task` → 真实 `goal`
>
> **P1 设计风险**（5 项）：
> 1. `import main; reload` boot smoke 在 monolithic main.py 99% 翻车 → 改为 `build_agent(cfg) -> _AgentLoop` 工厂 + 直接 assertion
> 2. WI-T2.6 session TTL 是伪命题（70KB/周不是 leak） → 砍掉，降级为 "P3 deferred until leak observed"
> 3. D9 双注册不可救（tier `l1/l2/l3` vs category `preference/profile` 完全不同维度）→ 改为 schema migration helper（透明翻译）
> 4. D10 mcp_call/delegate **无真用户 caller** → 直接 unregister + 0-release 删，不 ship deprecation handler
> 5. D14 "schemas only filter" 默认是反模式（disabled 实际不 disable）→ 默认 strict
>
> **本版排期**：4 天 → **3 天单人 / 1.5 天并行**（WI-T2.6 砍掉 + D9 简化 + D10 简化）

**关联**:
- 上一轮迭代：`plans/2026-05-23-tool-layer-optimization/`（v1→v2 单轮评审）
- 上游 last-mile：`plans/2026-05-23-tool-last-mile-upgrade/`
- 本期 round1 评审：`03-architect-review-round1.md`

**工作分支**: `feat/tool-layer-optimization-v3`（基于 master 合 `tool-last-mile-upgrade` + memory-stage2 之后）

---

## 0. 一句话

`tool-last-mile-upgrade` 分支 21 WI 已完成 + QA SHIP-WITH-FOLLOWUP，但 main.py 漏接 verify_gate → fake-completion 生产抓获 0%。本 PRD 把 last-mile 真正落地（**Stage A**：合 master + 接电 + 修 3 个 P0 bug）+ 补 P1（**Stage B**，含 session TTL deferred）+ 替换 stubs（**Stage C**，含 schema migration + 直接删 mcp_call/delegate）+ 修 registry spec gap（**Stage D**，含 replace opt-in）+ 扩 `backend/config.py:ToolsConfig`（**Stage E**）+ 同步 OpenSpec（**Stage F**）。

---

## 1. 背景与问题定义

### 1.1 P0 致命问题（来自 last-mile second-opinion review）

| # | 问题 | 位置 | 后果 |
|---|------|------|------|
| **P0-1** | `VerifyGate` 没接进 `AgentLoop` ctor | `tool-last-mile-upgrade` 分支 `backend/main.py` 搜锚点 `_AgentLoop(` —— 全文 grep `verify_gate` 命中 0 次 | fake-completion 生产抓获率 **0%** |
| **P0-2** | `ReceiptStore` retention 截断 | 同分支 `backend/main.py` 搜 `min(retention, 7)` | 30 天硬压成 7 天 |
| **P0-3** | `emit_receipt` duration_ms 失真 | 同分支 `backend/deskpet/tools/registry.py` 搜 `started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc)` | p95 延迟监控失效 |

**关键事实（v2 严格核对）**：

`tool-last-mile-upgrade` 分支 `backend/agent/agent_loop.py`：
- L396-398：`AgentLoop.__init__(*, verify_gate, receipt_store, max_verify_nudges)` 已就位
- L423-425：已 set self.\*
- L947-969：完整 verify check + D8 rebound + ephemeral rescue
- `backend/tests/test_agent_loop_verify_wiring.py` 4 用例已绿

**唯一缺**：main.py AgentLoop ctor 调用没传 3 参数。WI-T2.1 ≈ ~20 行 + 1 个工厂测试 ≈ **2 小时**。

### 1.2 P1 风险项

| # | 项 | v2 状态 |
|---|---|---|
| P1-1 | Tauri `artifact_ops.rs` 377 行零 cargo test | 修（WI-T2.4）|
| P1-2 | vitest CI 默认 `--no-vitest` 跳过 | 修（WI-T2.5）|
| P1-3 | `_session_iteration` 无 TTL | **★v2 砍掉**（70KB/周非 leak） → P3 deferred until evidence |
| P1-4 | metrics 无 dashboard | 修（WI-T2.7）|

### 1.3 P1 stubs.py 现状（v2 严格 grep 核对）

`backend/deskpet/tools/stubs.py` 注册的 6 个 stub 的 **真实 schema 字段**：

| Stub tool | toolset | **真实 schema 字段**（v2 grep 核对）| 上游真实现 | 当前 |
|---|---|---|---|---|
| `memory_write` | memory | `text` (required) + `tier`（enum **`["l1", "l2", "l3", "auto"]`**）+ `salience` | ✅ `FactsStore.upsert` | stub |
| `memory_read` | memory | `memory_id` (required, integer) | ✅ `FactsStore.get_by_id` | stub |
| `memory_search` | memory | `query` (required) + `top_k` | ✅ `EnhancedRetriever.recall` | stub |
| `skill_invoke` | control | `name` (required) + `args` (object) | ✅ `SkillLoader.execute_skill` | stub |
| `mcp_call` | control | `server` + `tool` + **`arguments`** (object) | ⚠️ 无真 caller | 设计冲突 |
| `delegate` | control | **`goal`** (required, string) | ⚠️ code_tools 已有 `agent` | 设计冲突 |

**v2 修正关键**：
- `tier` enum 是 `l1/l2/l3/auto` —— 与 category `preference/profile/...` **完全不同维度**
- `mcp_call` 第三参 `arguments` 不是 `args`
- `delegate` 字段 `goal` 不是 `task`

### 1.4 P1 memory_tools.py 已被 memory-stage2 占用 ★v2 新增

memory-stage2 已在 master 上 commit `9fe628d` 创建 `backend/deskpet/tools/memory_tools.py`，注册 **`memory_forget`** 工具 + `bind(facts_store=, embedder=, llm_call=, enable_natural_language=)`。

**本期改动**：
- WI-T3.1 必须 **append 现有文件** 添加 4 个 memory_* 工具（write/read/search/v2 系列）
- `bind()` 签名合并：`bind(*, facts_store, embedder, llm_call, memory_manager=None, retriever=None, enable_natural_language=False)`
- 不能 Write 新文件（会覆盖 memory_forget 注册）

### 1.5 P2 tool-registry spec gap

| # | spec 要求 | 代码现状 |
|---|---|---|
| **D2-spec** | name conflict 抛 `ToolNameConflictError` | `registry.py` `register()` 当前覆盖 + warning |
| **D3-spec** | Plugin 工具自动加 `<plugin>:` 前缀 | 未实现 |

**v2 关键发现**：stubs.py:6-9 文档明文 *"Each stub is expected to be overridden by a proper registration (same name) when the owning slice merges — `registry.register` replaces on duplicate name."* —— **整个 stubs→真实现接力依赖"register 覆盖"**。D11 一刀切抛错会让 backend 启动崩。**v2 D11 加 `replace=True` opt-in**。

### 1.6 P2 _config.py 配置面窄

`backend/deskpet/tools/_config.py` 仅有 `WebToolsConfig + load_web_config`。但**`backend/config.py:232` 已有 `ToolsConfig` 含 verifier 子段**。v2 决策：扩展 `backend/config.py:ToolsConfig` 而非新建 `_config.py` ToolsConfig（避免命名碰撞）。

### 1.7 P2 OpenSpec 滞后

`openspec/changes/p4-poseidon-agent-harness/tasks.md` 86/161 勾，与代码不符。

---

## 2. 目标与非目标

### 2.1 目标（G）

- **G0** 合 last-mile + memory-stage2 到 master（M0；前置）
- **G1** 接电 VerifyGate：`build_agent(cfg, ...) -> _AgentLoop` 工厂；wiring test 复用 + 工厂 assertion
- **G2** 修 2 P0 bug：retention + duration_ms
- **G3** 补 P1：Tauri cargo / vitest CI / metrics dashboard（**session TTL deferred**）
- **G4** 替换 stubs：
  - 3 个 memory_* **schema migration helper**（透明翻译 + 不破老 prompt）
  - `skill_invoke` 接 SkillLoader
  - **`mcp_call` + `delegate` 直接 unregister**（无真 caller，不 ship deprecation）
- **G5** 修 spec gap：`ToolNameConflictError(replace_allowed=False)` 仅在显式不允许覆盖时抛 + plugin 前缀含 instance_id
- **G6** 扩 `backend/config.py:ToolsConfig`：加 `disabled_toolsets` / `dangerous_tools_allowlist` / `default_timeout_seconds` / `strict_unknown_toolset`（**默认 strict 关 = strict 关掉就是关掉**）
- **G7** 同步 OpenSpec：tasks 回填 + archive

### 2.2 非目标（NG）

- NG1 重写 last-mile
- NG2 cross-key merge（memory-stage2）
- NG3 加新工具
- NG4 LLM provider 切换 / agent loop 重写
- NG5 shadow → strict 升级（1-2 周 metric 后独立决策）
- NG6 真监控栈接入
- NG7 旧 memory_* schema 真删（保留兼容；下个 release 评估）
- NG8 ~~mcp_call/delegate deprecation~~（v2 改直接删）
- NG9 ~~session_iteration TTL clean~~（v2 deferred）

### 2.3 成功度量

| 指标 | 目标 |
|---|---|
| AgentLoop 接电 | `build_agent(cfg).verify_gate is not None`（flag ON 时）；wiring test + 工厂 test 全绿；metrics 出现 verify event |
| `verify.fake_completion_caught_rate`（shadow 1 周）| 真实非零 |
| receipt duration_ms p95 | 50-2000ms |
| retention 30 天 | 真按 30 天 cutoff 跑 |
| stubs 替换 | 4 真实现（schema migration）+ 2 直接删 |
| `ToolNameConflictError(replace_allowed=False)` | builtin/builtin 同名且未 opt-in replace → raise |
| `[tools]` 4 字段生效 | disabled_toolsets / dangerous_allowlist / default_timeout / strict_unknown_toolset |
| metrics dashboard | console + `--report-json` |
| 第一代回归 | flag 全关时 backend pytest 0 回归 |

---

## 3. 关键架构决策（v2，含 6 个 default revisited）

### 3.0 6 个动工前决策（v2 修订）

| # | 决策点 | v2 default | round1 反驳 + v2 响应 |
|---|---|---|---|
| **Q1** | last-mile 合 master 顺序 | 独立 PR 先合 + **加结构性 review: main.py 4015 处 ctor 三 kwargs 必传** | round1 反驳"回归过就行"是反模式（复刻 last-mile P0）→ M0 验收加结构性检查 |
| **Q2 ★v3** | memory_* schema | **旧名 schema migration + 新名直注册**（v3 round2 修正：不是纯 schema migration，仍是混合双注册）| 旧 memory_write/read 做 schema migration 兼容；新增 memory_v2_write/read 走显式新 schema |
| **Q3** | _session_iteration 清理 | **deferred until leak observed** | round1 反驳 70KB/周非 leak → 砍 WI-T2.6 |
| **Q4** | AgentLoop e2e 测试形态 | **build_agent 工厂 + 直接 assertion** | round1 反驳 `import main; reload` 99% 翻车 → 改 testability refactor |
| **Q5** | mcp_call/delegate | **直接 unregister + 0-release 删** | round1 反驳"无真用户 caller，1 release window 是想象出来的需求" |
| **Q6** | ToolsConfig disabled_toolsets | **默认 strict** + opt-in `_schema_only` 变体 | round1 反驳"schemas only filter 反模式（disabled 实际不 disable）" |

### 3.1 决策表（D）

| # | 决策点 | 决定（v2） | 理由 |
|---|---|---|---|
| **D0** | last-mile 合 master 顺序 | M0 独立 PR + **加结构性 review**（main.py 4015 ctor grep `verify_gate=`）；M0 顺序：① last-mile→master ② memory-stage2 rebase 后合并（memory_tools.py 已就位）③ 本期基于 ② 起分支 | Q1 v2 |
| **D1** | VerifyGate 构造时机 | **eager**：`build_agent(cfg, ...) -> _AgentLoop` 工厂内直接构造 verify_gate 并传入 ctor；构造失败 catch + warn + `verify_gate = None` | Q4 v2；testability refactor |
| **D2** | VerifyGate disabled | `cfg.tools.verifier.enabled=false` → verify_gate=None → AgentLoop 老路径（与 last-mile 现状一致）| Strangler-Fig |
| **D3** | retention 截断修复 | main.py 改 `retention_days=retention`；grep `ReceiptStore.cleanup_expired` 同步修内部截断（若有）| bug fix |
| **D4** | duration_ms 修复 | `registry.py:execute_tool` 包 timer + 传 `started_at/ended_at` 给 `emit_receipt`；**先核 emit_receipt 实现：duration_ms 字段是否从参数算还是内部 datetime.now()** | round1 P2 提醒 |
| **D5** | Tauri cargo test 范围 | 仅 `artifact_ops.rs` 4 用例；不引入 E2E 框架 | E2E 是另一条线 |
| **D6** | vitest CI 集成 | `last_mile_smoke.py` 默认 `--with-vitest` | 复用 acceptance |
| **D7** | ~~`_session_iteration` 清理~~ | **本期不做**（Q3 v2 deferred） | 70KB/周非 leak |
| **D8** | metrics dashboard | rich console + `--report-json` + `--watch` + `--alert` + `--since`；rich 不可用 → plain text | round1 P1-6 提醒 cache |
| **D9 ★v2** | memory_* schema | **schema migration helper**：在已有 `memory_tools.py` append 4 个 handler；旧 `memory_write/read/search` 沿用 master 真实字段名 + tier→category 翻译表（`{"l1":"preference","l2":"project","l3":"event","auto":"preference"}`）；新增 `memory_v2_write/read` 走显式 category；search 不需要双注册（query/top_k 字段一致）| Q2 v2 + P0-2 + P0-4 |
| **D10 ★v2** | mcp_call/delegate | **直接 unregister + 不 ship deprecation handler**；stubs.py 移除两个 register 行 + 加 DEPRECATED 注释；无 metric emission | Q5 v2 |
| **D11 ★v3** | `ToolNameConflictError` 抛错矩阵 + 字典序方向修正 | **v3 重要修正**：pkgutil 字典序 `'m' < 's'`，memory_tools.py **先**加载注册 → stubs.py **后**加载若同名注册会覆盖真实现。**v3 策略**：① `registry.register(..., replace_allowed=False)` 新增参数 ② 双 builtin 同名 + 双方都 opt-in `replace_allowed=True` → warn + 覆盖；任一方未 opt-in → raise ③ **stubs.py 改用守卫模式**：`if name not in registry._tools: register(name, ..., replace_allowed=True)`，避免覆盖真实现。④ ToolSpec dataclass 加 `replace_allowed: bool = False` 字段（frozen=True 兼容）| round2 P0-3+P0-5 |
| **D12** | plugin 前缀 | `source="plugin:<plugin_name>[:<instance_id>]"` 时 name 加 `<plugin_name>:` 前缀；instance_id 用于 reload 识别 | 同 v1 |
| **D13 ★v2** | config 段位置 | **扩 `backend/config.py:ToolsConfig` 加 4 字段**（不在 `tools/_config.py` 新建 ToolsConfig；避免命名碰撞）| P0-6 |
| **D14 ★v2** | `disabled_toolsets` 默认行为 | **默认 strict**（关掉就是关掉：schemas + execute_tool 双层挡）；opt-in `disabled_toolsets_schema_only` 给"仅 LLM 看不到，但程序内部仍调"的边缘场景 | Q6 v2 |
| **D15** | `dangerous_tools_allowlist` | 非空时仅 allowlist 中的 dangerous tool 可用 | 同 v1 |
| **D16** | OpenSpec 同步策略 | 仅回填 tasks.md + archive；不修 spec.md | 同 v1 |
| **D17 ★v3** | `tier → category` 翻译表 | **v3 修正语义对账**：tier 越大 = 越长期 = decay 越小（**l1/l2/l3 应该是 short/mid/long-term**）。`_TIER_TO_CATEGORY = {"l1":"event", "l2":"project", "l3":"preference", "auto":"preference"}`（短/中/长 ↔ 快/中/慢，对应 facts.py `_CATEGORY_DECAY`: event=0.05/project=0.01/preference=0.005）；未知 tier 退化 "preference" + log warn | round2 P0-4 修正 v2 反向语义 |

### 3.2 VerifyGate 接电（WI-T2.1）— v2 工厂模式

**新建/改 `backend/main.py` 内 `build_agent` 工厂**（v2 testability refactor）：

```python
def build_agent(
    cfg, *, llm_registry, tool_registry, context_manager,
    receipt_store_getter, llm_call_func,
    # ★v3 round2 补漏 4 个参数（main.py:4015 现场需要）
    max_iterations: int,
    completion_probe,
    max_completion_nudges: int,
    signature_repeat_threshold: int,
) -> "_AgentLoop":
    """工厂 — 让 verify_gate 接电可被单测断言。

    args:
        receipt_store_getter: callable() -> ReceiptStore（lazy）
        llm_call_func: 已经构造好的 LLM 调用函数（来自 make_llm_call(local_llm)）
    """
    from observability.metrics_sink import get_default_sink
    metrics_sink = get_default_sink()

    verify_gate = None
    if cfg.tools.verifier.enabled:
        try:
            from deskpet.agent.verify_gate import (
                VerifyGate, RegexExtractor, CascadeExtractor,
                load_claim_patterns,
            )
            patterns = load_claim_patterns(cfg.tools.verifier.claim_patterns_file)
            extractor: Any = RegexExtractor(patterns)
            if cfg.tools.verifier.cascade_with_llm:
                extractor = CascadeExtractor(
                    primary=extractor, llm_call=llm_call_func,
                )
            verify_gate = VerifyGate(
                extractor=extractor,
                mode=cfg.tools.verifier.verify_gate_mode,
                receipt_store=receipt_store_getter(),
                metrics_sink=metrics_sink,
            )
            logger.info(
                "p4_verify_gate_ready mode=%s extractor=%s patterns=%d",
                cfg.tools.verifier.verify_gate_mode,
                type(extractor).__name__, len(patterns),
            )
        except Exception as exc:
            logger.warning("verify_gate init failed; disabling: %s", exc)
            cfg.tools.verifier.enabled = False
            verify_gate = None

    return _AgentLoop(
        llm_registry=llm_registry,
        tool_registry=tool_registry,
        max_iterations=max_iterations,                       # ★v3
        completion_probe=completion_probe,                   # ★v3
        max_completion_nudges=max_completion_nudges,         # ★v3
        signature_repeat_threshold=signature_repeat_threshold,  # ★v3
        context_manager=context_manager,
        # ─── WI-T2.1 v2 接电 ───
        verify_gate=verify_gate,
        receipt_store=receipt_store_getter(),
        max_verify_nudges=cfg.tools.verifier.max_verify_nudges,
    )


# 原 main.py:4015 处改为：
_agent = build_agent(
    cfg,
    llm_registry=llm_registry,
    tool_registry=deskpet_tool_registry_v2,
    context_manager=context_manager,
    receipt_store_getter=_get_receipt_store,
    llm_call_func=make_llm_call(local_llm),  # v2 真实 API
)
```

**测试**（v2 工厂 + 复用 wiring test）：

```python
# backend/tests/test_build_agent_verify_wiring.py
def test_build_agent_passes_verify_gate_when_flag_on(test_cfg_flag_on):
    """直接 assertion，不 import main"""
    agent = build_agent(
        test_cfg_flag_on,
        llm_registry=Mock(), tool_registry=Mock(),
        context_manager=Mock(),
        receipt_store_getter=lambda: Mock(),
        llm_call_func=lambda *a, **kw: "",
    )
    assert agent.verify_gate is not None
    assert agent.receipt_store is not None
    assert agent.max_verify_nudges == 2


def test_build_agent_verify_gate_none_when_flag_off(test_cfg_flag_off):
    agent = build_agent(...)
    assert agent.verify_gate is None
```

### 3.3 stubs 替换（v2 详细设计）

#### WI-T3.1 memory_* 在已有 memory_tools.py append（v2 D9）

**append 到 `backend/deskpet/tools/memory_tools.py`**（已存在，memory-stage2 注册了 memory_forget）：

```python
# ─── v3 新增：tier → category 翻译表（D17 v2）───
_TIER_TO_CATEGORY: dict[str, str] = {
    "l1": "preference",    # short-term preference
    "l2": "project",       # mid-term project context
    "l3": "event",         # long-term event memory
    "auto": "preference",  # default
}


# ─── v3 新增：bind() 签名合并（保留 memory-stage2 + 加新参数）───
def bind(  # 覆盖现有 memory-stage2 的 bind
    *,
    facts_store=None,        # memory-stage2 旧参数
    embedder=None,           # memory-stage2 旧参数
    llm_call=None,           # memory-stage2 旧参数
    enable_natural_language=False,  # memory-stage2 旧参数
    # v3 新增：
    memory_manager=None,
    retriever=None,
):
    global _facts_store, _embedder, _llm_call, _enable_nl
    global _memory_manager, _retriever
    _facts_store = facts_store
    _embedder = embedder
    _llm_call = llm_call
    _enable_nl = enable_natural_language
    _memory_manager = memory_manager
    _retriever = retriever


# ─── v3 新增 handler ───
async def _handle_memory_write(args: dict, task_id: str) -> str:
    """旧 schema 兼容：text/tier/salience → upsert"""
    if _facts_store is None:
        return _err("memory_tools not bound")
    text = (args.get("text") or "").strip()
    if not text:
        return _err("text required")
    tier = args.get("tier", "auto")
    if tier not in _TIER_TO_CATEGORY:
        logger.warning("memory_write: unknown tier %r → preference", tier)
        category = "preference"
    else:
        category = _TIER_TO_CATEGORY[tier]
    auto_key = _slugify(text[:20])
    try:
        fid = await _facts_store.upsert(
            category=category, subject="user", key=auto_key,
            value=text, confidence=float(args.get("salience", 0.5)),
            source_msg_id=None,
            evidence=f"[tool-call:{task_id}][legacy_schema]",
        )
        return json.dumps({"ok": True, "fact_id": fid, "key": auto_key})
    except Exception as exc:
        return _err(f"write failed: {exc}")


# memory_v2_write / memory_v2_read 新 schema handlers（同 v1）
# memory_read 旧 schema（memory_id）handler
# memory_search 共用 handler

# ─── 顶层注册（用 replace_allowed=True opt-in，因为 stubs.py 也注册过同名）───
registry.register("memory_write", "memory", _OLD_SCHEMA_WRITE,
                  _handle_memory_write, replace_allowed=True)
registry.register("memory_read", "memory", _OLD_SCHEMA_READ,
                  _handle_memory_read, replace_allowed=True)
registry.register("memory_search", "memory", _OLD_SCHEMA_SEARCH,
                  _handle_memory_search, replace_allowed=True)
registry.register("memory_v2_write", "memory", _NEW_SCHEMA_WRITE,
                  _handle_memory_v2_write)  # 新名字，无冲突
registry.register("memory_v2_read", "memory", _NEW_SCHEMA_READ,
                  _handle_memory_v2_read)
```

**`stubs.py` 改动**（用 `replace_allowed=True` opt-in）：

```python
# stubs.py 中的 memory_* register 行改为：
registry.register(
    "memory_write", "memory", _MEMORY_WRITE_SCHEMA, _stub_handler("S4"),
    replace_allowed=True,  # ← v2 opt-in：欢迎被真实现替换
)
# 同款 memory_read / memory_search
```

`memory-stage2` 也要同步加 `replace_allowed=True`（如果其 memory_forget 不需被替换则不加）。

**main.py lifespan 接入**：

```python
if cfg.memory.v2.facts_extract:
    from deskpet.tools import memory_tools
    memory_tools.bind(
        # memory-stage2 参数
        facts_store=memory_manager.facts_store,
        embedder=embedder,
        llm_call=make_llm_call(local_llm),
        enable_natural_language=cfg.memory.v2.memory_forget_natural_language,
        # v3 新参数
        memory_manager=memory_manager,
        retriever=enhanced_retriever,
    )
```

#### WI-T3.2 skill_invoke 真实现

新建 `backend/deskpet/tools/skill_tools.py`，顶层注册时 `replace_allowed=True`（替换 stubs）。

#### WI-T3.3 mcp_call / delegate 直接删（v2 D10）

**改 `stubs.py`**：移除 mcp_call + delegate 的 register 行；加注释：

```python
# ─── WI-T3.3 v2 (D10): mcp_call / delegate 直接删 ───
# 这两个 stub 自 P4-S5 起从未有外部真 caller（grep skill .md + 历史 sessions = 0）
# 与 MCPManager 命名空间路径 (mcp__<server>__<tool>) / code_tools.agent
# 设计冲突。直接 unregister，不 ship deprecation handler（无人受影响）
#
# 历史 reference 保留 schema 定义在注释中：
#   _MCP_CALL_SCHEMA = {...}   # server / tool / arguments
#   _DELEGATE_SCHEMA = {...}   # goal
```

**回归验证**：跑 `grep -r "mcp_call\|delegate" plugins/ skill_packs/ docs/` 确认无外部引用；如有则改回 1-release deprecation。

### 3.4 ToolNameConflictError v2 (WI-T4.1) — replace_allowed opt-in

**改 `backend/deskpet/tools/registry.py`**：

```python
class ToolNameConflictError(Exception):
    def __init__(self, name, existing, new):
        super().__init__(
            f"tool name conflict: {name!r} already registered by "
            f"source={existing.source!r}; cannot register from "
            f"source={new.source!r}. "
            f"If intentional, set replace_allowed=True on BOTH register calls."
        )
        self.tool_name = name
        self.existing = existing
        self.new = new


def _extract_instance_id(source: str) -> str:
    parts = source.split(":")
    return parts[2] if len(parts) >= 3 else ""


# register 签名加 replace_allowed 参数：
def register(self, name, toolset, schema, handler, *,
             check_fn=None, requires_env=None,
             permission_category="read_file",
             source="builtin", dangerous=False,
             timeout_seconds=60.0,
             replace_allowed=False) -> None:  # ★v2 新增
    # plugin 前缀（同 v1）
    if source.startswith("plugin:"):
        parts = source.split(":")
        plugin_name = parts[1] if len(parts) > 1 else ""
        if plugin_name and not name.startswith(f"{plugin_name}:"):
            name = f"{plugin_name}:{name}"

    spec = ToolSpec(
        name=name, toolset=toolset, schema=schema, handler=handler,
        check_fn=check_fn, requires_env=list(requires_env or []),
        permission_category=permission_category, source=source,
        dangerous=dangerous, timeout_seconds=float(timeout_seconds),
        replace_allowed=replace_allowed,  # ★v2 存到 spec
    )

    with self._lock:
        if name in self._tools:
            existing = self._tools[name]
            # ★v2 抛错矩阵
            # 1. 双 builtin + 任一 replace_allowed=False → raise
            if existing.source == "builtin" and source == "builtin":
                if not (existing.replace_allowed and replace_allowed):
                    raise ToolNameConflictError(name, existing, spec)
            # 2. plugin/plugin 同前缀同名（不同 instance_id）→ raise
            if (
                existing.source.startswith("plugin:")
                and source.startswith("plugin:")
            ):
                existing_iid = _extract_instance_id(existing.source)
                new_iid = _extract_instance_id(source)
                if existing_iid != new_iid:
                    raise ToolNameConflictError(name, existing, spec)
                # 同 plugin reload → warn
            # warn + 覆盖（其他场景：plugin reload / MCP reconnect / replace opt-in）
            logger.warning(
                "tool %r re-registered; %s replaces %s",
                name, source, existing.source,
            )
        self._tools[name] = spec
```

### 3.5 `backend/config.py:ToolsConfig` 扩展（WI-T5.1 v2 D13）

**改 `backend/config.py`**（在已有 `ToolsConfig` 基础上加字段，**不在 `_config.py`**）：

```python
@dataclass
class ToolsConfig:
    """Top-level config.toml [tools] section."""

    verifier: ToolsVerifierConfig = field(default_factory=ToolsVerifierConfig)
    # ...其他已有字段（last_mile / web 等）

    # ─── v3 新增 ───
    disabled_toolsets: list[str] = field(default_factory=list)
    """禁用 toolset list — 默认 strict（schemas + execute_tool 双层挡）"""

    disabled_toolsets_schema_only: list[str] = field(default_factory=list)
    """opt-in 边缘场景：仅 LLM 看不到但 execute_tool 仍可调"""

    dangerous_tools_allowlist: list[str] = field(default_factory=list)
    """非空时仅 allowlist 中 dangerous tool 可用"""

    default_timeout_seconds: float = 60.0
    """tool spec 未指定 timeout 时的默认"""

    strict_unknown_toolset: bool = False
    """True → typo 触发 fail-fast"""
```

**`registry.schemas()` 加 filter**：

```python
def schemas(self, enabled_toolsets=None):
    from backend.config import get_config  # 或同款 lazy import
    cfg = get_config()
    out = []
    for spec in self._tools.values():
        if not _env_ok(spec.requires_env):
            continue
        if enabled_toolsets and spec.toolset not in enabled_toolsets:
            continue
        # ── D14 v2: disabled_toolsets 默认 strict ──
        if spec.toolset in cfg.tools.disabled_toolsets:
            continue
        if spec.toolset in cfg.tools.disabled_toolsets_schema_only:
            continue
        # D15: dangerous allowlist
        if (
            spec.dangerous and cfg.tools.dangerous_tools_allowlist
            and spec.name not in cfg.tools.dangerous_tools_allowlist
        ):
            continue
        out.append(spec.to_openai_schema())
    return out
```

**`registry.execute_tool()` 加 strict 拦截**：

```python
async def execute_tool(self, name, args, task_id):
    cfg = get_config()
    spec = self._tools.get(name)
    if spec is None:
        return _err(f"unknown tool {name!r}")
    # ── D14 v2 strict 拦截 ──
    if spec.toolset in cfg.tools.disabled_toolsets:
        return _err(
            f"toolset {spec.toolset!r} disabled by config [tools.disabled_toolsets]"
        )
    # disabled_toolsets_schema_only 不挡 execute_tool（这是这个 opt-in 的含义）
    ...
```

**`get_config()` 必须 `_cached` 单例 + mtime 检测**（避免每次 LLM 调用都 disk IO）。

### 3.6 metrics dashboard 路径修正

`metrics.jsonl` 的写入函数是 `record(event, detail)`（**不是** `emit_metric`）：

```python
# 正确：
from observability.metrics_sink import record
record("tool.execute", {"duration_ms": ...})
```

dashboard.py 实现同 v1（含 `--report-json`），但 event 名称要与生产代码一致（grep `record(` 拿真实事件名集）。

---

## 4. 需求拆解（Work Items v2）

### 4.0 M0 · last-mile + memory-stage2 合 master（前置）

操作：
1. 合 last-mile → master（merge-tree 验证 + 回归 + acceptance）
2. **结构性 review**：`grep "verify_gate=" backend/main.py` 必命中 ≥ 1（v2 加）
3. memory-stage2 rebase 后合 master
4. 本期基于 ② 起分支

### 4.1 Stage A — 接电 P0

- **WI-T2.1** `build_agent` 工厂 + 接电 + 工厂 assertion test
- **WI-T2.2** retention bug fix
- **WI-T2.3** duration_ms fix（**先核 emit_receipt 内部签名**）

### 4.2 Stage B — last-mile P1

- **WI-T2.4** Tauri cargo test
- **WI-T2.5** vitest CI 默认必跑
- ~~**WI-T2.6** session TTL~~ **v2 deferred to future**
- **WI-T2.7** metrics dashboard + `--report-json`（用真实 `record()` API）

### 4.3 Stage C — stubs 替换

- **WI-T3.1** memory_* schema migration 在已有 memory_tools.py append（含 tier→category 翻译表）
- **WI-T3.2** skill_invoke 真实现（`replace_allowed=True` opt-in）
- **WI-T3.3** mcp_call/delegate 直接 unregister（无 deprecation handler）

### 4.4 Stage D — spec gap

- **WI-T4.1** `ToolNameConflictError` + `replace_allowed=True` opt-in 参数；stubs.py 现有占位加 opt-in
- **WI-T4.2** plugin 前缀 + instance_id 区分

### 4.5 Stage E — config

- **WI-T5.1** 扩 `backend/config.py:ToolsConfig` 加 5 字段（含 `disabled_toolsets_schema_only` opt-in）+ `_cached` 单例 + mtime 检测

### 4.6 Stage F — OpenSpec

- **WI-T6.1** tasks 回填（每条加 verified comment）
- **WI-T6.2** archive

---

## 5. 里程碑与排期（v2）

| 里程碑 | 内容 | 依赖 | 预估 |
|---|---|---|---|
| **M0** | last-mile + memory-stage2 合 master + 结构性 review | — | 0.5 天 |
| **M1** | WI-T2.1（~2h，工厂方式）+ T2.2 + T2.3 | M0 | **0.5 天** |
| **M2** | WI-T2.4/5/7（Stage B；T2.6 deferred）| M1 | 1 天 |
| **M3** | WI-T3.1（append memory_tools）+ T3.2 + T3.3（unregister）| M1 | 1 天 |
| **M4** | WI-T4.1（replace_allowed）+ T4.2 | 独立 | 0.5 天 |
| **M5** | WI-T5.1（含 strict 默认 + cache）| 独立 | 0.5 天 |
| **M6** | WI-T6.1/2 | 独立 | 0.5 天 |
| **M7** | 全套回归 + PR | 全部 | 0.5 天 |

**总计**：**~3 天单人** / 三路并行（M2/M3/M5）→ **~1.5 天**

---

## 6. 风险登记（v2）

| # | 风险 | 缓解 |
|---|------|------|
| R1 | shadow→strict 漂移 | 收 1-2 周 metric 再决策 |
| R2 | retention 修后磁盘爆 | < 100MB/月可控 |
| R3 | duration_ms 修后 jsonl 体积 | 沿用 jsonl 滚动 + `--since` |
| R4 | memory_* 替换后 DB 写压 | FactsStore 已有 _persist_lock |
| **R5 ★v2** | ~~mcp_call/delegate deprecation~~ | **D10 v2 直接删** — 跑 `grep mcp_call skill_packs/ docs/ plugins/` 确认无 caller；有则改 1-release deprecation |
| **R6 ★v2** | ToolNameConflictError 启动崩 | **D11 v2 replace_allowed opt-in** — stubs.py 现有占位标 True |
| R7 | disabled_toolsets typo | strict_unknown_toolset opt-in |
| R8 | dashboard rich 不可用 | 降级 plain text |
| R9 | Tauri cargo windows runner | `cfg!(windows)` 分支 |
| R10 | OpenSpec tasks 回填勾错 | verified comment 锚 commit hash |
| R11 | 又"写完不接" | DoD 强制工厂 assertion + boot smoke 不允许 grep 当证据 |
| **R-MISS-1** ★v2 | memory_tools.py 已被 memory-stage2 占用 | D9 v2 改 append + bind() 签名合并 |
| **R-MISS-2** | AgentLoop e2e mock 难写 | Q4 v2 build_agent 工厂方式 |
| **R-MISS-3** | 三方 merge 顺序 | D0 v2 顺序锁定 |
| **R-MISS-4** | VerifyGate strict 漂移 | 同 R1 |
| **R-MISS-5** | unknown toolset 静默 | strict_unknown_toolset |
| **R-MISS-6 ★v2** | master schema 字段错（tier enum / arguments / goal）| 全文 grep 修；v2 D17 加 tier→category 翻译表 |
| **R-MISS-7 ★v2** | stubs.py "register replaces" 设计 vs D11 抛错冲突 | D11 v2 replace_allowed opt-in |
| **R-MISS-8 ★v2** | 无真 caller 还 ship deprecation 过工程 | D10 v2 直接删 |
| **R12 ★v3** | disabled_toolsets v2 默认 strict 升级 = silent breaking change | release notes 显式提醒；现有用户 `disabled_toolsets = ["computer_use"]` 升级后 LLM 不能调（之前能调）；变更要列入 CHANGELOG.md |
| **R-MISS-9 ★v3** | `facts.py` 真无 `get_by_id` 方法（v2 TDD §A8 假设有，错）| WI-T3.1 实施前先在 facts.py 加 `async def get_by_id(self, fact_id: int) -> Optional[dict]` 方法，或 handler 改走 `find_active(subject, key)` + 反查（性能差但功能 OK）|
| **R-MISS-10 ★v3** | tier 翻译表 v2 语义反了（l3 应该是最长期最不衰减，v2 翻成 event 最快衰减）| D17 v3 修正翻译表：`l1→event, l2→project, l3→preference` |

---

## 7. 验收标准（v2）

1. M0：合 master + **结构性 review**（grep verify_gate= 命中）+ 回归全绿
2. WI-T2.1：`build_agent(cfg).verify_gate is not None`；工厂 test + 复用 wiring test 全绿
3. WI-T2.2/2.3：retention 30 天真生效；duration_ms > 0
4. WI-T2.4/5/7：Tauri ≥ 4 / vitest CI / dashboard + report-json
5. WI-T3.1：memory_tools.py append 4 个 handler；tier→category 翻译；旧+新 schema 都跑通
6. WI-T3.2：skill_invoke 真接 SkillLoader
7. WI-T3.3：stubs.py 不再含 mcp_call / delegate register 行
8. WI-T4.1/2：replace_allowed opt-in；ToolNameConflictError 仅在双方未 opt-in 时 raise
9. WI-T5.1：4 字段 + strict 默认 + cache 单例
10. 回归：backend pytest 0 fail（真数 = `pytest --collect-only` 真跑）
11. 文档：每 WI 在 01-TDD §D 回填

---

## 8. 未来工作（roadmap）

- shadow → strict 升级（收 metric 后）
- 真监控栈
- Tauri E2E 框架
- outcome_verifier 真接 callers
- session_iteration TTL（若 evidence of leak）

---

## 9. v2 修订 checklist

- [x] P0-1: emit_metric → record API + 路径修正
- [x] P0-2: memory_tools.py append 现有文件 + bind() 签名合并
- [x] P0-3: D11 replace_allowed opt-in
- [x] P0-4: tier enum l1/l2/l3/auto + 翻译表（D17）
- [x] P0-5: metrics_sink / llm_call_func 真实 API
- [x] P0-6: backend/config.py:ToolsConfig 加字段（不在 _config.py）
- [x] P0-other: mcp_call arguments + delegate goal
- [x] P1-1: build_agent 工厂方式
- [x] P1-2: WI-T2.6 deferred
- [x] P1-3: schema migration helper（取代双注册）
- [x] P1-4: mcp_call/delegate 直接删
- [x] P1-5: disabled_toolsets 默认 strict
- [x] P1-6: load_tools_config _cached
- [x] R-MISS-1~8 全部加入风险登记
- [x] 排期 4 天 → 3 天单人 / 1.5 天并行

待 round2 评审。
