# 二轮架构评审报告 v2 — DeskPet 工具调用 Last-Mile 升级

- **评审人**：opus-4.7 架构师
- **日期**：2026-05-23
- **评审对象**：v2 三份文档（已吸纳一轮报告全部 P0/P1/一致性问题）
- **总体结论**：**SHIP-WITH-FOLLOWUP**

## 一轮 P0 关闭验证（5 项）

| ID | 状态 | 证据（文件:章节） | 残留 |
|---|---|---|---|
| I3-1 ClaimPattern + LLM fallback | CLOSED | PRD D6 `CascadeExtractor`（L221-238）；TDD TG-9 T9-11；TG-9 T9-10 分桶 50 条 | fallback 仅 LLM 二级，未做 NLI 三级（O2 已 defer 至 M2，可接受） |
| I3-2 第3次失败前 ephemeral 救援 | CLOSED | PRD D6 三轮升级表 L241-246；TDD T9-14；手测 MR-9 9-1/9-2/9-3 | 见下方 N1 |
| I4-1 HMAC key DPAPI/Keychain | CLOSED | PRD D11 表 L348-352；TDD T7-5/T7-8；手测 MR-19 19-1/19-2/19-5 | 裸文件兜底链路保留（PRD L350、L360、MR-19 19-5），未被破坏 |
| I4-2 re2 + ReDoS + safe_load | CLOSED | PRD D6 L235；TDD §C.3 加载约束 L289-294；T9-12/T9-13/T9-15 | 见下方 N2（re2 拒收命名组的样例风险） |
| I2-2 UNC/mapped/8.3 路径白名单 | CLOSED | PRD D3 L122-135 六步标准化；TDD T4-7/T4-8/T4-9/T4-10 | — |

## 一轮 P1 关闭验证（8 项）

| ID | 状态 | 证据 |
|---|---|---|
| I1-2 N4 与 ephemeral 关系澄清 | CLOSED | PRD §2 N4 注释 L75、D6 末注 L247、附录 B |
| I1-4 度量盲点（点击率埋点） | CLOSED | PRD WI-T1.7 L412、§5 表 L441、TDD T5-7、手测 MR-22 |
| I2-1 ToolReceipt 字段对齐 12 项 | CLOSED | PRD D5 L168-182、TDD §C.2 required 12 项 L258-261 同源 |
| I2-3 sha256 异步化 | CLOSED | PRD D5 L184-189；TDD T7-7、T2-8、T2-9、T7-9、§F 性能预算 |
| I3-3 verifier 限 scope + toolchain skip | CLOSED | PRD D7 L268、L270-280；TDD T10-5/T10-8/T10-9；MR-24 |
| I4-3 HMAC key 切账号 / Roaming | CLOSED | PRD D11 L356-358；R11；MR-23 |
| I4-4 卸载迁移 | CLOSED | PRD R12；MR-23 |
| I4-5 acceptance.sh 脚本化 | CLOSED | PRD WI-T3.5 L433；附录 C O4 标记已采纳 |

## 一轮一致性问题关闭验证（5 项）

| ID | 状态 | 证据 |
|---|---|---|
| 术语漂移（3 次硬限） | CLOSED | PRD D6 L249-255 计数语义节；MR-9 顶部"计数语义统一"；TDD §C.4 VerifyOutcome.failure_count 注释 |
| D5 9 vs C.2 12 字段 | CLOSED | PRD D5 注释 L168 "12 个字段与 TDD §C.2 严格同源"；TDD §C.2 required 12 项 |
| 路径漂移（emoji slug） | CLOSED | PRD D4 L150-158 明确保留 emoji；TDD T6-5；MR-5 5-1 期望 `营销周报-📊` |
| error_class vs UnmatchedClaim.reason | CLOSED | PRD D5 末段 L198-202；TDD §C.4 L362-364 双注释 |
| 点击率无 MR/TG | CLOSED | TDD T5-7；手测 MR-22 |

## v2 新引入的问题

| 严重度 | 编号 | 描述 | 建议 | 文件:章节 |
|---|---|---|---|---|
| P1 | N1 | ephemeral_verifier_subagent 信任面：mock 测试只覆盖 pass/fail/异常，未防范"恶意/异常 receipt 注入 ledger 后被 ephemeral 误判 pass"。ledger 内 receipt 若 HMAC 验签失败应在喂给 ephemeral 前过滤；当前 D6/TDD 未明示 ephemeral 的输入是否经过 sig verify 闸门 | 在 PRD D6 ephemeral 章节加一行"输入 ledger 仅含 sig-valid receipts"；TDD 加 T9-14b：注入 sig_invalid receipt → ephemeral 不应据此判 pass | PRD L241-246；TDD T9-14 |
| P1 | N2 | TDD §C.3 默认样例 `claim_patterns.yaml`（L297-313）使用了**命名捕获组** `(?P<title>...)` / `(?P<path>...)`。`google-re2` Python binding 支持命名组，但 RE2 不支持 lookbehind/backreference；样例中的 `(?:...)` 非捕获组 + 命名捕获组在 re2 兼容范围内**可加载**——但风险点是 `(?:为您)?` 等可选组 + 中文 Unicode 类需 re2 编译时开 UTF-8。T9-12 仅断言"PCRE 特性 reject"，未跑"默认 yaml 100% 加载成功"的正向用例 | 加 T9-12b：启动期默认 `claim_patterns.yaml` 必须 100% re2 编译通过（防 ship 后默认 patterns 全 reject 静默失效） | TDD §C.3、T9-12 |
| P2 | N3 | `failure_count == 3` 时机仍微歧义：PRD L244 "第 3 次失败前调度" vs L253 "`failure_count == 3` 且 ephemeral 判 fail → 强退"。语义上 ephemeral 在 N=3 那次失败后立即调度（非第 4 次回灌），文档自洽，但 MR-9 9-1 "第 1/2 次回灌；**第 3 次失败前**调度"与计数语义"==3 时调度"措辞不一致——读起来像 N=2.5 触发 | 把 D6 三轮表"第 3 次失败前"改为"第 3 次失败 (failure_count→3) 时立即调度，不再回灌主 LLM"；MR-9 同步措辞 | PRD L241-246；MR-9 |
| P2 | N4 | `sha256_pending` receipt 的 verify gate 行为未定义：D5 允许超时后 receipt artifacts=[] 标 sha256_pending；但 VerifyGate 用 `sha256` 比对 claim（TDD T9-5），如果 receipt 处于 pending 状态，UnmatchedClaim.reason 应该是 `sha256_mismatch` 还是 `no_receipt`？还是放行等 patch？文档未明 | 在 D5 或 D6 加一行：sha256_pending receipt 在 VerifyGate 中视为"path/title 匹配即放行，sha256 校验 skip + warn"，patch event 落盘后异步重核 | PRD D5 / D6 |
| P2 | N5 | Flag invariant 反向未校：D10 表只列了"非法组合"，但 `verify_gate_mode="strict" + extractor_fallback_enabled=true + emit_receipts=true` 但 `ephemeral_subagent_model` 未配置/为空 → 当前 TDD T1-7/T1-8/T1-9/T1-10 未覆盖此场景；启动应给默认 haiku 还是拒 | PRD D10 加一行 `ephemeral_subagent_model` 缺失时默认填 `"haiku"` + warn；TDD 加 T1-11 | PRD L325、TDD TG-1 |

## ship 建议

P0 5/5 全部 CLOSED，P1 8/8 全部 CLOSED（100% 远超 75% 门槛），一致性 5/5 CLOSED。
v2 新引入 5 个新问题中无 P0，2 个 P1（N1/N2）涉及 ephemeral 信任面与默认 yaml 加载正向校验——可在 Stage 2 开工前补 TDD 用例修复，不阻 ship。

**残留追踪建议**：
1. N1/N2 列为 Stage 2 准入条件（开工前合入 TDD T9-12b/T9-14b）
2. N3/N4/N5 列为 Stage 0 文档微调 task，与 WI-T0.3 一起提交
3. ephemeral 救援触发率、`verify_extractor.fallback_used` 触发率两条 metric 需在 §5 度量表补"健康区间"上线监控阈值（已有 D6 内联说明，正式表格未列）

## 评分（v2）

- 维度 1 可执行性：9/10（v1 8/10）— WI 拆分细且新增 T1.7/T3.5；TDD 用例数从 ~70 升到 90+
- 维度 2 兼容性：9/10（v1 8/10）— 字节级 dict-key 校验（T2-5b）+ DPAPI 兜底链完整保留
- 维度 3 2026 对齐：9/10（v1 6/10）— DPAPI keystore、re2、ephemeral subagent、changed_files scope、acceptance 脚本化全部就位
- 维度 4 风险回退：8/10（v1 5/10）— 新增 R11/R12/R13，invariant 启动校验给了清晰回退路径；扣分点在 N1 ephemeral 信任面与 N4 sha256_pending 语义边界

## 简洁结语

v1 提出的全部 P0/P1/一致性问题均已闭环；v2 新增 5 个问题无 P0，2 个 P1 可在 Stage 2 开工前补齐。建议 SHIP-WITH-FOLLOWUP。

---

## 后记（v2.1 修订）

本报告发布后，v2.1 已吸纳 N1-N5 全部 5 项修订：

| ID | 状态（v2.1） | 落点 |
|---|---|---|
| N1 | CLOSED | PRD D6 ephemeral 信任面注 + TDD T9-14b + MR-9 9-5 |
| N2 | CLOSED | TDD T9-12b 正向用例 |
| N3 | CLOSED | PRD D6 三轮表措辞统一 + MR-9 顶部 |
| N4 | CLOSED | PRD D6 末段 + TDD T9-16 |
| N5 | CLOSED | PRD D10 invariant 反向 + TDD T1-11 |

v2.1 预估评分：9.5/9/9.5/9（v2 基础上 +1 ~ +0.5）。
