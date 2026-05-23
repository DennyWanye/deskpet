# Beta 100 灰度发布计划 — DeskPet 工具调用 Last-Mile 升级

- **配套**：`00-PRD.md` (v2.1) §5 度量表 + §7 回退路径
- **日期**：2026-05-23
- **状态**：v1（准 ship 文档，等运维侧实际执行）
- **关联 commit 范围**：`tool-last-mile-upgrade` 分支 — 见 README.md / CLAUDE.md

---

## §1 灰度阶段三段式

按 PRD §8 "beta 100 灰度（10% → 50% → 100%）" 拆解：

| 阶段 | 用户比例 | 持续 | 准入条件 | 监控阈值 | 回滚触发 |
|---|---|---|---|---|---|
| **G1: 内部 dogfood** | 5 名核心团队 + 5 名 beta 早期用户 | 3 天 | acceptance script SHIP + 4 一票否决全过 | 0 个 S0 缺陷 | 任 1 S0 |
| **G2: 10% beta** | 10/100 用户随机 | 7 天 | G1 退出标准达成 + 0 个新 P0 | `verify.sig_invalid_filtered = 0` + `fake_completion_caught_rate ≥ 80%` (per fixture sample) + p95 端到端延迟增量 ≤ 800ms | sig_invalid > 0 / 延迟超预算 50% / 任 1 S0 |
| **G3: 50% beta** | 50/100 用户 | 7 天 | G2 退出标准达成 + 用户反馈面板 ≤ 3 条 P1 | fallback 触发率 5%~20%（健康区间）+ ephemeral_rescued < 3% + click_through_rate ≥ 50% on PPT/Excel/Doc | fallback > 30% / ephemeral > 5% / 用户反馈 ≥ 5 条 P1 |
| **G4: 100% beta** | 100/100 用户 | 14 天观察 | G3 退出标准达成 + click_through_rate ≥ 60% | 同 G3 健康区间 + 0 retention 退化 | 任 1 P0 / retention drop > 5% |

**总计观察期：3 + 7 + 7 + 14 = 31 天**（与 PRD §5 "beta 100 灰度 30 天工单 ≤ 1" 对齐）。

---

## §2 灰度技术实施

### 2.1 Flag 渐进激活（按用户 ID 哈希）

不是按全局 flag 切换，而是按 user_id 哈希进入灰度桶：

```python
# 伪代码 — 实际落到 main.py 启动期
def is_in_rollout_bucket(user_id: str, rollout_pct: int) -> bool:
    """user_id sha256 取前 4 bytes → 0~99 → 与 rollout_pct 比较。
    
    每个用户在不同阶段稳定属于同一桶（不会今天进灰度明天退出）。
    """
    h = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    return (h % 100) < rollout_pct
```

config.toml 加 `[tools.last_mile.rollout]` 段：

```toml
[tools.last_mile.rollout]
# 0 = OFF (全关 / G0)；5/10/50/100 = G1~G4 各阶段
artifact_envelope_pct = 0
frontend_artifact_card_pct = 0
tauri_artifact_ops_pct = 0
# verify_gate / receipts 单独控制（更敏感）
emit_receipts_pct = 0
verify_gate_mode_pct = 0   # 0 时 verify_gate_mode 等价于 "off"
```

`registry.execute_tool` / `AgentLoop.run` 读 user_id（从 session_id 反查），用 `is_in_rollout_bucket` 决定是否走新路径。**flag-off 用户继续走 main 字节级一致路径**（PRD G5）。

### 2.2 度量埋点（实时监控）

每个灰度阶段同时收集（已实现的 metric，PRD §5）：

| Metric | 含义 | 健康区间 | 告警 |
|---|---|---|---|
| `artifact_action.click_rate` | 4 按钮平均点击率 | ≥ 60% on PPT/Excel/Doc | < 40% → P2 |
| `verify_extractor.fallback_used` | 小 LLM fallback 触发率 | 5%~20% | < 5% 或 > 30% → P2 |
| `verify.ephemeral_rescued` | 第 3 次失败救援率 | < 3% | > 5% → P1 |
| `verify.sig_invalid_filtered` | sig-invalid receipt 剔除数 | **= 0** | > 0 → **P1 alert** |
| `verifier.skipped_due_to_missing_toolchain` | npm/pytest 缺失数 | 视用户群 | 用于 toolchain 安装引导 |
| `fake_completion_caught_rate` | 50 fixture 抓获率 | ≥ 95% in CI | < 80% on prod traces → P0 |

埋点 sink 已接电（commit `d8f0beb`），落地 `<user_data>/metrics.jsonl`。

### 2.3 Receipt 写盘量观察

每阶段监控 `<user_data>/receipts/*.jsonl` 平均大小：

- G2: 单用户 7 天 ≤ 5MB（按平均 100 调用/天 × 500B/receipt 估）
- G4: 整体回收触发率（cleanup_expired 7 天清理）≥ 95%
- 超阈值 → 触发 R3（artifact_dir 占盘失控）回退路径

---

## §3 退出标准（每阶段独立）

### G1 → G2

- [ ] 10 名 dogfood 用户全部能跑通 MR-1（生成 PPT + 点击「打开」）
- [ ] 0 个 S0 缺陷（一票否决项 MR-0/8/13/19 任 1 失败 = S0）
- [ ] backend 全套 1932 测试持续绿
- [ ] receipts/ 目录健康（无 sig_invalid_filtered 告警）

### G2 → G3

- [ ] 10% 用户 7 天累计 0 P0、≤ 2 P1
- [ ] `fake_completion_caught_rate` 在抽样回放上 ≥ 80%
- [ ] p95 端到端 PPT 延迟相比 main 增量 ≤ 800ms
- [ ] click_through_rate ≥ 40%（早期低正常，G3 会爬到 60%）

### G3 → G4

- [ ] 50% 用户 7 天累计 0 P0、≤ 3 P1
- [ ] click_through_rate ≥ 50%
- [ ] verify metric 全部在健康区间
- [ ] 用户反馈面板"找不到文件"工单 = 0

### G4 → SHIP（GA）

- [ ] 100% 用户 14 天累计 0 P0、≤ 1 P1
- [ ] click_through_rate ≥ 60%
- [ ] retention（DAU/MAU）无退化（相比 main baseline）
- [ ] verify metric 全部健康区间持续 14 天
- [ ] 用户反馈"找不到文件"工单 ≤ 1（PRD §5 度量表硬目标）

---

## §4 回退路径（按 PRD §7）

| 触发条件 | 操作 |
|---|---|
| 任一阶段出 S0 | `*_pct = 0` 立即关该阶段所有 flag；用户 ≤ 60s 内回到 main 字节级一致行为 |
| VerifyGate 误判 > 1% | `verify_gate_mode_pct = 0` 或 mode 降到 `shadow` |
| receipts/ 占盘失控 | `emit_receipts_pct = 0` + 触发 receipt_store.cleanup_expired |
| HMAC key 大面积失效 | `emit_receipts_pct = 0`，旧 receipts 自动归档（D11 已实现） |
| 整体灾难 | `git revert <merge-commit>` + 所有 *_pct = 0 |

每个回退动作有对应自动化脚本：`scripts/rollout/disable_<flag>.py`（**本期作为模板列在此，实际脚本由部署侧实现**）。

---

## §5 监控仪表盘（建议）

- **Grafana 面板**：6 个 metric 时序图 + 4 个一票否决项 health check
- **告警渠道**：P0 → 飞书机器人 + 短信；P1 → 飞书机器人；P2 → 仅 log
- **数据源**：`<user_data>/metrics.jsonl` 每日 rsync 到中央 ELK / Loki

详细面板配置由 SRE 团队 实现，本文档仅约定**应该监控什么 + 何时告警**。

---

## §6 灰度实施 owner 矩阵

| 阶段 | 主 owner | 旁观 | 决策签字 |
|---|---|---|---|
| G1 | 开发 leader | 产品 PM | 开发 leader |
| G2 | 产品 PM | 开发 + SRE | 产品 PM |
| G3 | SRE | 产品 + 客服 | SRE leader |
| G4 → GA | SRE | 全员 | CTO |

每阶段升级需主 owner 在内部群发"准入条件 ✅ 报告"+ 决策签字方批 GO。

---

## §7 已知不在本计划内的 deferred 项

- **T3.2 跨平台 mac Tier 2**：需 mac runner CI（已加 `#[cfg(target_os = "macos")]` 分支，等 mac dev 真机跑 cargo test 验证）
- **T4-8 mapped drive → UNC 反查 winapi binding**：现状靠 canonicalize 兜底（常见场景 OK，winapi binding 是加固）
- **SmallLLMExtractor + ephemeral_verifier_subagent 真 LLM 接通**：当前 stub（功能性占位），G2 阶段需要接通才能验证生产 fake-completion 抓获率
- **google-re2 加 prod 依赖**：当前用 Python re + 静态 nested quantifier 检测，G3 升 strict 模式前应换 re2

详见 `00-PRD.md` 附录 C deferred 项与 `04-architect-review-round2.md` follow-up 清单。

---

## §8 启动信号

本灰度计划在以下条件全部满足后才能从 G0（默认 OFF）走 G1：

- [x] 19 个 WI 全部 commit（已完成 — 21 commits on tool-last-mile-upgrade）
- [x] acceptance script DECISION = SHIP（已 verify 2026-05-23T13:18Z）
- [x] 4 一票否决项 MR-0/8/13/19 通过（已 verify via acceptance）
- [x] 两轮 opus-4.7 架构评审 SHIP-WITH-FOLLOWUP → 3 P0 全修
- [ ] windows-mcp 子代理 MR-1 真实 E2E 报告（**进行中**，等通知）
- [ ] PR review 通过 + merge 到 main
- [ ] G1 启动会议（产品 + 开发 + SRE 三方碰头）

**主代理职责到此为止**。G1 之后是部署侧的运维 cadence。
