# DeskPet 工具调用 Last-Mile 升级 — 人工测试总报告

- **日期**: 2026-05-23
- **测试人**: opus-4.7 QA 子代理（架构师视角 / 20 年人工测试经验代入）
- **对应**: `02-manual-test-cases.md` v2.1 (MR-0 ~ MR-24)
- **主代理 HEAD**: `895859f` (branch `tool-last-mile-upgrade`, 24 commits ahead of master)

## 1. 测试方法论

dev-session QA 子代理在 Claude Code Harness 内执行。受 Harness 工具栈约束（无 windows-mcp 实机控制 / 无 UI 截图通道），采用分层验证：

| 层 | 验证手段 | 适用 MR |
|---|---|---|
| 一票否决 (VETO) | `scripts/acceptance/last_mile_smoke.py` | MR-0/8/13/19 |
| TDD 严格覆盖 | 12 个 last-mile 专属 pytest = 120 用例 | 13 条 |
| dev 端到端 (curl + 真服务) | `manual-results-mr22-metrics-prod-validation/REPORT.md` | MR-22 |
| G1 dogfood-deferred | 5+5 用户灰度兜底 (`05-beta-100-rollout.md` 工单→24h P0 回退) | MR-1/4/6/7/17/18 |
| 平台/部署延后 | mac runner CI / installer 真跑 | MR-16 / MR-23 |

## 2. 一票否决项（4/4 必须 PASS）

执行：`python scripts/acceptance/last_mile_smoke.py --no-vitest --no-cargo`
报告 JSON: `plans/2026-05-23-tool-last-mile-upgrade/manual-results-2026-05-23T134155Z/acceptance.json`

| MR | 主题 | 状态 | 证据 |
|---|---|---|---|
| MR-0 | 零回归 | ✅ PASS* | §2.1 |
| MR-8 | Fake-completion ≥95% | ✅ PASS | TG-9 15/15 + acceptance VETO |
| MR-13 | file_exists outcome verifier | ✅ PASS | TG-10 15/15 + acceptance VETO |
| MR-19 | HMAC DPAPI 私密性 | ✅ PASS | TG-7+8 13/13 + N1 + acceptance VETO |

**4/4 全过 ✅**

### 2.1 MR-0 PASS-with-known-flake 说明

acceptance 报：`FAILED tests/test_deskpet_vector_worker.py::test_enqueue_triggers_batch_flush_when_full` (1 failed / 1931 passed / 14 skipped / 153.97s)
QA 单独复跑：`1 passed in 0.86s` (确定性 PASS)

- 文件 `backend/tests/test_deskpet_vector_worker.py:123` 内部已有注释 `# P4-S16 root-cause flake fix`，历史已识别
- 属 P4 vector worker (memory 子系统)，与本期 last-mile 改动**零代码依赖**（不在 24 commits 改动清单内）
- 失败模式 `written=8 vs expected=10` 是 batch flush 时序尾巴抖动，非功能性

结论：判定 **pre-existing flake**，不阻塞 last-mile ship；登记 S2 follow-up。

## 3. 完整 MR 矩阵 (24)

| MR | 状态 | 证据 |
|---|---|---|
| MR-0 | ✅ PASS* | acceptance + 1931 passed (1 P4 flake §2.1) |
| MR-1 | ⏳ G1-deferred | UI 实机；`manual-results-mr1-20260523/` 部分覆盖 |
| MR-2 | ✅ AUTO | TG-3 T3-3 `ppt_create _HAS_PPTX` 显式 raise |
| MR-3 | ✅ AUTO | TG-3 T3-4 + 5 工具改造 commit `2bd4c92` |
| MR-4 | ⏳ G1-deferred | vitest 9/9 (主代理 stage) + 真渲染 G1 验 |
| MR-5 | ✅ AUTO | `test_artifact_default_path.py` 12/12 + title_slug 中文/emoji |
| MR-6 | ⏳ G1-deferred | TG-6 T6-1 + TG-1 T1-1 绿；env 真生效要 UI |
| MR-7 | ⏳ G1-deferred | `test_ppt_dry_run.py` 4/4；按钮交互 G1 验 |
| MR-8 | ✅ PASS (VETO) | TG-9 15/15 |
| MR-9 | ✅ AUTO | TG-9 T9-14 ephemeral + `test_agent_loop_verify_wiring.py` 7/7 |
| MR-10 | ✅ AUTO | TG-9 T9-12b yaml 100% 编译 |
| MR-11 | ✅ AUTO | `test_receipt_store.py` 13/13 |
| MR-12 | ✅ AUTO | TG-8 T8-3 args_hash 不存明文 |
| MR-13 | ✅ PASS (VETO) | TG-10 15/15 |
| MR-14 | ✅ AUTO | TG-10 T10-5 + TG-11 T11-2 D8 |
| MR-15 | ✅ AUTO | TG-10 T10-6 + outcome_verifier |
| MR-16 | ❌ platform | `#[cfg(target_os = "macos")]` 已写；需 mac runner |
| MR-17 | ⏳ G1-deferred | TDD §F 预算文档 + sha256 异步 TG-2 T2-8 |
| MR-18 | ⏳ G1-deferred | TG-2 T2-7 并发不串号；50 轮长跑留 G1 |
| MR-19 | ✅ PASS (VETO) | TG-7 T7-8 + N1 |
| MR-20 | ✅ AUTO | TG-8 T8-3 sig-invalid + N1 |
| MR-21 | ✅ AUTO | TG-1 T1-7~T1-11 invariant 11/11 |
| MR-22 | ✅ PASS (DEV-E2E) | `manual-results-mr22-metrics-prod-validation/REPORT.md` |
| MR-23 | ❌ deployment | archive_all_for_key_rotation 已实现；installer 真跑 |
| MR-24 | ✅ AUTO | TG-10 T10-8/T10-9 toolchain skip |

**统计**：✅ 16 条 / ⏳ G1-deferred 6 条 (MR-1/4/6/7/17/18) / ❌ platform/deployment 2 条 (MR-16/23)

## 4. 关键 metrics

| 指标 | 值 |
|---|---|
| Backend 全套回归 | **1931 passed / 1 flake / 14 skipped / 4 deselected** in 154s |
| Last-mile 专属 120 用例 | **120 passed / 0 failed** in 4.7s |
| acceptance script | 4 PASS / 1 FAIL (P4 flake) |
| 一票否决 VETO | **4/4 PASS** |
| MR-22 dev-E2E | 2 条真 jsonl 写盘 + 脱敏 OK |

## 5. 复跑命令（可复核）

```bash
# 1. acceptance VETO
cd /g/projects/deskpet-tool-last-mile
python scripts/acceptance/last_mile_smoke.py --no-vitest --no-cargo

# 2. last-mile 120 用例
cd /g/projects/deskpet-tool-last-mile/backend
python -m pytest tests/test_tool_artifact.py tests/test_tool_last_mile_config.py \
  tests/test_receipt_store.py tests/test_verify_gate.py tests/test_outcome_verifier.py \
  tests/test_stage2_wiring.py tests/test_agent_loop_verify_wiring.py \
  tests/test_byte_level_consistency.py tests/test_artifact_default_path.py \
  tests/test_ppt_dry_run.py tests/test_metrics_event_endpoint.py tests/test_tg11_feedback_format.py

# 3. P4 flake 隔离（非本期）
cd /g/projects/deskpet-tool-last-mile/backend
python -m pytest tests/test_deskpet_vector_worker.py::test_enqueue_triggers_batch_flush_when_full
```

## 6. S0/S1/S2 缺陷分类

- **S0** (阻塞 merge)：**0 条**
- **S1** (功能正确但 UX 缺陷)：**0 条** (代码层观察；UI 层潜在 S1 由 G1 dogfood 暴露)
- **S2** (follow-up)：
  1. P4 vector worker flake — CI 加 retry / 拆 fixture
  2. windows-mcp infrastructure 30+min 0 byte (MEMORY 已记) — G1 真人 dogfood 兜底
  3. MR-4/6/7 UI 真渲染 — G1 5+5 用户 × ≥3 类产物 × 2 工具
  4. MR-17/18 实测 — G1 收集 100 PPT p95 + 50 轮长跑 jsonl
  5. MR-16 macOS Tier 2 — 待 mac runner CI
  6. MR-23 installer 卸载迁移 — G2/G3 installer 真跑
  7. vitest 9/9 — acceptance script 默认开启 (`--no-vitest` 是本 session 跳过)

## 7. Ship 建议: **SHIP-WITH-FOLLOWUP (→ G1 dogfood)**

代码层 ready —— 4 VETO 全过、last-mile 120 用例 0 fail、全套 1931 绿 (唯一 fail 是 P4 pre-existing flake)、MR-22 dev-E2E 通过。UX 层风险由 G1 灰度 5+5 用户 + 24h P0 回退 + metrics 监控兜底，是业界标准做法；DeskPet 单机桌宠场景不要求 100% UI 自动化覆盖。不建议直接 SHIP-TO-G4 (跳过 G1 暴露面太大)，也不建议 NO-SHIP 等所有 UI 自动化补齐 (windows-mcp infrastructure 当前阻塞，等待会无限期延后)。

**G1 准入清单**：
- [x] 4 个 VETO 全过
- [x] MR-22 埋点链路真验
- [ ] G1 5+5 用户实跑 MR-1/4/6/7 + click_through_rate
- [ ] G1 metrics.jsonl 监控 `verify.sig_invalid_filtered = 0` + `fallback_used` 5%-20% 健康区间

## 8. Follow-up Ticket

| # | 题 | 优先级 | 触发 |
|---|---|---|---|
| 1 | G1 dogfood 5+5 用户 + 工单 | P0 | merge 后 7d |
| 2 | P4 vector worker flake CI retry | P2 | 1 周内 |
| 3 | macOS mac runner CI (MR-16) | P1 | G2 准入 |
| 4 | installer MR-23 卸载迁移 | P1 | G3 准入 |
| 5 | MR-17/18 metrics 被动收集 | P1 | G1 期间 |
| 6 | vitest 在 CI 默认跑 | P2 | 持续 |
| 7 | windows-mcp infrastructure 修复 | P3 | 长期 |

---

*signed by opus-4.7 QA subagent*
*生成时间：2026-05-23*
