# /goal 指令草稿 — 工具层优化 v3 实施

**用途**：用户可复制下方 `/goal` 指令文本直接发，触发完整自动化实施流程。
**注意**：本文档仅为**草稿**，**未立即执行**（按用户指示）。

---

## /goal 指令文本（可直接复制粘贴）

```
/goal 完成 plans/2026-05-24-tool-layer-optimization-v3/ 的所有需求 — 严格按 00-PRD.md v3 + 01-TDD.md v3 实施 M0~M7 各 WI（含 M0 合 last-mile 到 master + WI-T2.1 build_agent 工厂接电 VerifyGate + T2.2/T2.3 P0 bug 修 + T2.4/T2.5/T2.7 P1 + T3.1 memory_* schema migration 在 memory_tools.py append + T3.2 skill_invoke 真实现 + T3.3 mcp_call/delegate 直接 unregister + T4.1/T4.2 ToolNameConflictError 加 replace_allowed opt-in + T5.1 backend/config.py:ToolsConfig 加 5 字段 + T6.1/T6.2 OpenSpec 同步）；每完成一阶段跑 backend pytest（含 test_build_agent_verify_wiring.py + 复用 test_agent_loop_verify_wiring.py）+ frontend vitest + cargo test + last_mile_smoke.py 四套门控全绿；最终派 opus 4.7 子代理按 02-manual-test-cases.md v3 跑 MR-T-0~16 人工测试；根据反馈修复后再派 opus 4.7 复测；循环至 MR-T-0 + MR-T-1 + MR-T-8 三大 ★ 标志用例全 ✅ 且功能 bug=0 为止。绝不允许 grep 源码当接电证据，必须有 boot smoke + metrics.jsonl 真出现 verify_* event 才算 WI-T2.1 完成。
```

---

## 该 goal 触发的工作流（执行时主代理应遵循）

### Phase 1：动工前 5 分钟核对（防 round2 P0 残留）

```bash
# 1. ToolsConfig 真实位置（合 last-mile 后应有）
grep "class ToolsConfig" backend/config.py

# 2. facts.py get_by_id 是否存在
grep "def get_by_id\|def find_active" backend/deskpet/memory/facts.py

# 3. ToolSpec 当前字段（v3 D11 要加 replace_allowed）
grep -A 15 "class ToolSpec" backend/deskpet/tools/registry.py

# 4. 用例真数
cd backend && .venv/Scripts/python.exe -m pytest --collect-only -q | tail -1
```

### Phase 2：M0 合 master

按 TDD §A0 完整操作步骤。**关键结构性 review**：
```bash
grep "verify_gate=" backend/main.py  # 应命中 0 次（M0 完成 last-mile 合并后；本期 M1 后才加）
```

### Phase 3：M1 接电（核心）

按 TDD §A1.1 + §A1.2 + §A1.3 执行：
1. main.py 加 `build_agent(cfg, ...) -> _AgentLoop` 工厂（含 6+4=10 个参数）
2. main.py:4015 改为 `_agent = build_agent(cfg, ..., max_iterations=_max_iter, ...)`
3. 新建 `backend/tests/test_build_agent_verify_wiring.py`（4 用例：flag ON / flag OFF / patterns 缺失 / kwargs 传递）
4. 跑复用 `test_agent_loop_verify_wiring.py` + 新增 boot smoke

**DoD 红线**：
- `from main import build_agent; agent = build_agent(cfg_flag_on, ...); assert agent.verify_gate is not None` 真跑通
- 启 backend 30s 内 `metrics.jsonl` 真出现 verify_* event

### Phase 4：M1 其余 + M2/M3/M5 三路并行

详 PRD §5 排期。

### Phase 5：M4 + M6 收尾

ToolNameConflictError + plugin 前缀 + OpenSpec 同步

### Phase 6：M7 全套回归

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/ -x --maxfail=5  # ≥ 2000 用例 0 fail
.venv/Scripts/python.exe -m scripts.last_mile_smoke      # acceptance 全过
cd ../tauri-app
npm test -- --run                                          # vitest 0 fail
cargo test --manifest-path src-tauri/Cargo.toml --lib     # ≥ 4 新增 pass
```

### Phase 7：派 opus 4.7 跑手工测试循环

```python
# 伪代码
while True:
    report = Agent(
        model="opus",
        prompt="按 plans/2026-05-24-tool-layer-optimization-v3/02-manual-test-cases.md v3 跑 MR-T-0~16；"
               "MR-T-0 + MR-T-1 + MR-T-8 是 ★ 一票否决；"
               "返回 markdown 测试报告（每条标 ✅/❌/⚠️ + bug 列表 + 关键接电证据）",
    )
    if report.all_required_pass and report.bug_count == 0:
        break
    fix_bugs(report)  # 主代理根据 report 修代码
```

---

## 现实评估（用户应知）

**这是 ~3 天单人 / 1.5 天并行的工程**，一个 Claude session 不可能完成。

**实际执行预期**：
- 单个 session 推进：M0 + M1（接电）+ M2/M3 部分 = ~6-8 小时工作量 → 一个 session 推不完所有 stage
- 派 opus 4.7 跑手测每轮 ~5-15 分钟，根据 bug 修复迭代每轮 ~30 分钟
- 循环 2-3 轮直到 bug=0

**主代理策略**：
1. 按 M0→M7 顺序推进，每完成一个 milestone 就 git commit + 跑回归
2. 当 session 接近上下文限制时，commit 进度并写一个 handoff doc 给下个 session
3. 派子代理一定要给完整 self-contained context（子代理看不到本 conversation 历史）

---

## 不立即执行的原因

按用户明确指示："**请不要立即执行这个指令**"。

本 goal 指令仅作为**未来动工时的执行模板**保存在此。用户可在准备好时复制 `/goal ...` 文本发给主代理触发完整流程。

---

## 触发前 checklist

执行此 goal 之前用户应确认：

- [ ] PRD v3 文档已读且决策都同意
- [ ] 6 个动工前决策（Q1-Q6 default）都 confirm
- [ ] memory-stage2 已 rebase 到 master + 合并（避免 memory_tools.py 文件冲突）
- [ ] last-mile 分支已合 master（M0 前置）
- [ ] 有连续 ~6-8 小时的 session 时间窗（保证 M0+M1 推到一个稳定 checkpoint）
- [ ] backend/.venv 已激活；frontend npm install 已跑
- [ ] LLM 可用（the relay/deepseek-chat 推荐）— 派 opus 4.7 子代理需要

---

## 相关文档

- `00-PRD.md` v3 — 需求清单 + 决策 + 风险登记
- `01-TDD.md` v3 — 代码骨架 + 测试规格
- `02-manual-test-cases.md` v3 — MR-T-0~16 人工测试
- `03-architect-review-round1.md` — opus 4.7 round1 评审
- `04-architect-review-round2.md` — opus 4.7 round2 评审
- `05-goal-instruction-draft.md`（本文件）— /goal 草稿，不立即执行
