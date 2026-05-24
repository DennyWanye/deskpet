# 架构评审 round1 — 工具层优化 v3（v1）

**评审日期**: 2026-05-24
**评审者**: opus 4.7 子代理（20Y 资深架构师）
**评审范围**: `plans/2026-05-24-tool-layer-optimization-v3/` v1 三份文档
**响应状态**: v1 → v2 修订进行中

---

## 总评

**Needs Major Revision** — v1 文档把"上一轮 v2 已修订过"的事实当成胜利、却同时引入 **4 处新的致命事实错** + **5 处设计自相矛盾**，按 v1 动工立即崩在 import 阶段。

---

## 必改 P0（6 项，动工前必须解决）

| # | 问题 | 位置 | v2 修订方向 |
|---|------|------|------------|
| **P0-1** | `emit_metric` API 不存在 | PRD §3.3 / TDD §A10 / §3.0 stubs 描述 | 全文档替换为 `record(event, detail)`，import 路径改 `from observability.metrics_sink import record`（无 `deskpet.` 前缀，无 `emit_` 前缀） |
| **P0-2** | `memory_tools.py` 已存在（memory-stage2 创建注册 `memory_forget`）；TDD §A8 用 Write 会覆盖 | TDD §A8 完整代码 | 改为在现有文件 **append**；`bind()` 函数签名合并：`bind(*, facts_store, embedder, llm_call, memory_manager=None, retriever=None, enable_natural_language=False)` —— 兼容 memory-stage2 + 新增 memory_manager/retriever 参数 |
| **P0-3** | D11 `ToolNameConflictError` 与 `stubs.py` 设计正面冲突；stubs.py:6-9 文档明文 "register replaces on duplicate"；pkgutil 字典序 memory_tools.py < stubs.py → builtin 同名 → 启动崩 | PRD §3.4 D11 / TDD §A11 | D11 加 `replace=True` opt-in 参数到 `registry.register()`；stubs.py 现有占位注册显式标 `replace=True`；不带 replace=True 的双 builtin 同名才 raise |
| **P0-4** | master `memory_write` schema 字段错 | PRD §3.0 stubs 表 / TDD §A8 / MR-T-8-1 | tier enum 实际是 `["l1", "l2", "l3", "auto"]`（不是 `preference/profile/...`）；不能直接 `category=tier` 映射 → 加 tier→category 翻译表（l1→preference / l2→project / l3→event / auto→preference）|
| **P0-5** | `metrics_sink` / `llm_call_func` 变量名在 `tool-last-mile-upgrade:main.py` 不存在（grep 0 命中）；实际是 `get_default_sink()` + `make_llm_call(local_llm)` | PRD §3.2 / TDD §A1.1 verify_gate 构造 snippet | `metrics_sink = get_default_sink()`；`llm_call_func` 改为 `make_llm_call(local_llm)` 模式或注入 _make_str_llm_call wrapper |
| **P0-6** | `_config.py` 加 `ToolsConfig` 与 `backend/config.py:232 ToolsConfig` 同名碰撞 | PRD §3.5 / TDD §A12 D13 | 二选一：(A) `backend/config.py:ToolsConfig` 加字段（嵌入 `[tools]` 段已有 verifier 等子段）；(B) `tools/_config.py` 用新名字 `ToolsRuntimeConfig` 不复用 ToolsConfig；推荐 (A) |

---

## P0-other（mcp_call / delegate schema 字段错）

| 工具 | v1 PRD 写 | master 真实 | 影响 |
|------|----------|-----------|------|
| `mcp_call` schema 第三参 | `args` (object) | `arguments` (object) — `stubs.py:147-160` | 沿用旧 schema 承诺破产 |
| `delegate` schema 字段 | `task` (string) | `goal` (string) — `stubs.py:111-120` | 同上 |

---

## P1 建议改（7 项）

| # | 问题 | v2 方向 |
|---|------|--------|
| P1-1 | TA1-1/2/3 `import main; reload` 在 monolithic main.py 99% 翻车（端口占用 / sqlite 锁 / 全局 state） | 改为 `build_agent(cfg, ...) -> _AgentLoop` 工厂；测试断言 `_agent.verify_gate is not None`；这是产线 testability refactor |
| P1-2 | WI-T2.6 session TTL 是无源之水（70KB/周不是 leak） | 砍掉或降级为 "P3 deferred until leak observed"（量化估算见评审 §二） |
| P1-3 | D9 双注册是伪命题（tier enum 完全不同维度） | 改为 schema migration helper：老 `memory_write(text=...)` 透明翻译到新 `memory_v2_write(key=auto_slug, value=text, category=tier_to_category(tier))` |
| P1-4 | D10 mcp_call/delegate deprecation 过度工程（无真用户 caller） | 直接 unregister + 0-release 删；不 ship deprecation handler；emit metric 路径直接删 |
| P1-5 | D14 默认"schemas only filter" 是反模式（disabled 实际不 disable）| 默认 strict（关掉就是关掉）；当前 schemas-only 当 opt-in 边缘选项 `disabled_toolsets_schema_only` |
| P1-6 | `load_tools_config()` 在 `schemas()` 内每次调用 = 每次 LLM 调用前都 disk IO | 加 `_cached` 单例 + 文件 mtime 检测失效 |
| P1-7 | TG-A14 "≈ 2000 用例" 估算 | 跑 `pytest --collect-only -q | tail -1` 拿真数 |

---

## P2 可优化（4 项）

- TA13-7 unknown toolset 检测需要先给 toolset 全集来源（grep 所有 `registry.register` 第二参数还是硬编码？）
- M0 验收"回归过就行"反模式 —— 加 "structural review: main.py 4015 行处 ctor 三 kwargs 必传" 显式 grep
- `_get_receipt_store()` eager 调用对 `_paths.user_data_dir()` import 期 ready 性需核
- duration_ms fix 路径与 `emit_receipt` 内部实现对账（fix 是否真起作用）

---

## 动工前必须先决（评审 §五）

1. **memory-stage2 已合并到 master 后 memory_tools.py 的接管方案** —— 最大执行风险
2. **stubs.py + memory_tools.py + skill_tools.py 三者注册重叠的 source 标识** —— 决定 ToolNameConflictError 判定矩阵
3. **WI-T2.1 测试形态最终选 `build_agent` 工厂 vs boot smoke subprocess** —— 直接影响 TG-A1 可信度
4. **mcp_call / delegate 是否有任何外部 caller 证据** —— 决定 D10 是删还是 deprecation

---

## 评审者最终结论

> 按当前 v1 动工 = **import 期 ToolNameConflictError + memory_tools.py 文本冲突 + emit_metric ImportError 三连崩**。round2 前必须先把这 6 项 P0 落实到 v2。

---

## v2 修订计划（响应映射）

| round1 发现 | v2 修订位置 |
|------------|------------|
| P0-1 emit_metric API | PRD §3.3 + TDD §A10 改用 `record(event, detail)` |
| P0-2 memory_tools.py 已存在 | TDD §A8 改为 append 现有文件 + bind() 签名合并 |
| P0-3 D11 + stubs 冲突 | PRD §3.4 D11 加 `replace=True` opt-in；TDD §A11 改 register 签名 |
| P0-4 memory schema 字段错 | PRD §1.3 表 + §3.3 D9 + TDD §A8 + MR-T-8 真实字段 |
| P0-5 metrics_sink / llm_call_func 错 | PRD §3.2 + TDD §A1.1 改真实 API |
| P0-6 ToolsConfig 命名碰撞 | PRD §3.5 D13 + TDD §A12 改为 backend/config.py ToolsConfig 加字段 |
| P0-other mcp_call/delegate 字段错 | PRD §3.0 + §3.3 D10 + TDD §A10 |
| P1-1 import main reload 改 build_agent 工厂 | TDD §A1.3 改测试形态 |
| P1-2 WI-T2.6 砍掉/降级 | PRD §4.2 + TDD §A6 改为 deferred |
| P1-3 D9 schema migration helper | PRD §3.3 D9 v2 |
| P1-4 D10 直接删 | PRD §3.3 D10 v2 + TDD §A10 删 deprecation |
| P1-5 D14 默认 strict | PRD §3.5 D14 v2 |
| P1-6 load_tools_config 加 cache | TDD §A12 |
| P1-7 用例数真数 | TDD §B TG-A14 |
