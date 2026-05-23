# 一轮架构评审报告 — DeskPet 工具调用 Last-Mile 升级

- **评审人**：opus-4.7 架构师子代理
- **日期**：2026-05-23
- **评审对象**：v1 三份文档（00-PRD.md / 01-TDD.md / 02-manual-test-cases.md）
- **总体结论**：**GO-WITH-CHANGES** — 设计成熟、与现有架构正交，但 ClaimPattern regex 路径在中英文混合 / 路径含正则元字符场景下有真实漏抓与注入风险，且 HMAC key 在 Windows 多用户 ACL、`build/test` verifier 在用户机的资源面、receipt 文件无限增长这 3 个高危项未在 §6 充分覆盖，必须先修后 merge。

---

## 维度 1 评分：8/10

### 通过项
- G1-G5 与 D1-D12 一一对应，§8 验收清单与 TDD/手测可追溯
- TG-0~TG-12 与 WI-T0.1~T3.4 在 TDD §E 显式编排，TDD 驱动可执行
- 手测 MR-0/MR-8/MR-19 一票否决与 §5 退出标准闭合

### 问题
| 严重度 | 编号 | 描述 | 建议修改 | 涉及文件:章节 |
|---|---|---|---|---|
| P0 | I1-1 | PRD D9 提到「prompt 引导 LLM 调 dry_run」，但 TDD 没有 TG 覆盖 prompt 注入路径 / 引导失效时的回退；MR-7 也只验"flag on 走通"不验"flag on 但 LLM 不听话" | TG-3 增加 T3-6：`outline_preview_default=true` 但 LLM 直接调 dry_run=false → 期望 system reminder 重述引导 | 01-TDD.md §B/TG-3、02 §3 MR-7 |
| P1 | I1-2 | PRD D5 ToolReceipt 字段 `artifacts: list[str]` 是 sha256 列表，但 TDD §C.2 Schema 与 D1 ToolArtifact 没说明 sha256 何时计算（写盘前？流式？）；大文件 PPT 算 sha256 阻塞主循环 | D5 加注「sha256 在工具 handler 返回前异步计算，>10MB 走线程池；receipt 等待 sha256 完成才签名」 | 00 §3 D5、01 §C.2 |
| P1 | I1-3 | MR-9 描述"第 4 轮强制 end_turn"，但 TG-9 T9-6 写"重试 ≥ 3 次"。"≥3" 与"第 4 轮"的语义边界（是 3 次失败后强退、还是允许第 3 次重试一次再退）模糊 | 统一为"失败计数到 3 即强退"，TDD T9-6 与手测 MR-9 9-1 同步改字面 | 01 §B TG-9、02 §3 MR-9 |
| P1 | I1-4 | TG-9 T9-9「中英文混合 claim 正确提取实体」只 1 条用例，强度不够支撑 G3 的 95% 抓获率 | T9-10 的 50 条 fixture 必须显式分桶：纯中文 / 纯英文 / 中英混合 / 同义改写 / 否定句各 ≥ 10 条 | 01 §B TG-9、§D fixtures |
| P1 | I1-5 | MR-3 只说"重复 MR-1"，缺 excel/doc/pdf/image 各自 mime/icon 期望表 | 加 4 行子矩阵：每种工具的 mime + 默认 actions 集合 | 02 §3 MR-3 |

## 维度 2 评分：8/10

### 通过项
- D1 明确"不破坏 result.ok/result.path，向后兼容 3 个版本"，D2 旧 ToolResultCard 回落
- 附录 A 与 memory-upgrade 的正交性说明清晰（receipt 写盘 ≠ facts I/O）
- T2-5/T5-5/T12-1 多层兜底字节级一致

### 问题
| 严重度 | 编号 | 描述 | 建议修改 | 涉及文件:章节 |
|---|---|---|---|---|
| P0 | I2-1 | 字节级一致 golden file 只对 `tool_result_*.json` 抓字段顺序，**不能挡住** `MessageBubble` 渲染分支变化（TG-5 T5-5 仅 snapshot test，覆盖面不等同字节级）；且没说 golden 在 `artifact_envelope=False` 时是否对 dataclass 加了 `artifacts` 默认空列表 | TDD §A.2 加 sub-bullet：flag-off 时 `to_dict()` 不得 emit `artifacts` 键（不是空数组，是缺键），TG-2 加 T2-5b 验证；MR-0 0-6 显式比对 keys()  | 00 §3 D1/D10、01 §A.2/§B TG-2 |
| P0 | I2-2 | Tauri 路径白名单（D3 + TG-4）写了 normcase + realpath + 拒绝符号链接，但 **没覆盖 UNC path（`\\server\share\...`）、mapped drive（`Z:` 指到 `\\server\share`）和 8.3 短文件名（`PROGRA~1`）**。Windows 上恶意/误生成路径可绕 | TG-4 加 T4-7/T4-8/T4-9：UNC 路径拒、mapped drive 解析到 UNC 拒、8.3 短名展开后比对；Rust 侧用 `GetFinalPathNameByHandleW` 而不仅 `canonicalize` | 00 §3 D3、01 §B TG-4 |
| P1 | I2-3 | `registry.execute_tool` 现是 async + 线程池兜底同步 handler；D5 说"由 registry 强制生成 receipt"——sha256 大文件 + HMAC sign 若同步在 async 上下文会阻塞事件循环 | TDD §F 加 budget 项：receipt 生成路径必须 `await loop.run_in_executor`，TG-7 加 T7-7 验证 100MB artifact sha256 不阻塞其他工具并发 | 00 §3 D5、01 §F |
| P1 | I2-4 | 附录 A 说"建议 memory-upgrade 先 merge"，但没说明 receipt jsonl 的 `<user_data>/receipts/` 与 memory-v2 的 facts vector backfill 是否共享同一 SQLite WAL/同一磁盘队列；同盘高 I/O 时延迟预算（< 5ms 写盘）会被拖 | 在 §F 性能预算加注：与 memory-v2 共存场景下写盘预算放宽到 < 20ms p95；或拆 receipts 到独立目录 + O_DIRECT 不走 WAL | 00 附录 A、01 §F |

## 维度 3 评分：6/10

### 通过项
- D5 ToolReceipt 含 `args_hash + started_at + ended_at + ok + artifacts(sha256)` 已覆盖 NABAOS 关键签证字段
- shadow → strict 渐进灰度符合 2026 共识
- D11 私密性与 diagnostic bundle 隔离是加分项

### 问题
| 严重度 | 编号 | 描述 | 建议修改 | 涉及文件:章节 |
|---|---|---|---|---|
| P0 | I3-1 | ClaimPattern 用 regex 提取在 2026 是**退步选项**——中英文混合 / 同义改写（"PPT 已就绪"/"文件出炉了"/"搞定 marketing.pptx"）/ 否定句（"还没生成"）regex 漏抓率会显著超过 5%。NABAOS 论文与 Anthropic harness-design 2026 都倾向 **NLI / 小 LLM 抽取 + 结构化 claim graph**；D6/TG-9 把 95% 抓获作为硬指标，但用纯 regex 几乎做不到 | D6 增加二级 fallback：regex 抽不到任何 claim 但 ledger 与 assistant_text 长度比异常（断言性长文本 + 0 receipt）时，调用本地小 LLM（或当前会话 LLM 的轻量 call）做"是否存在完成性 claim"判定。即"regex 为白盒，小 LLM 为兜底"两级；TG-9 加 T9-11 "无法被 regex 命中的同义/混合表达" 用例集 | 00 §3 D6、01 §B TG-9 |
| P0 | I3-2 | MR-9 / T9-6 "3 次失败硬退"是 **2025 思路**。2026 共识（Cognition / Anthropic）是"3 次仍失败 → context reset / handoff to fresh subagent"，而不是直接报错给用户。当前设计放弃了一次救援机会 | D6 增加 escalation：第 3 次 verify 失败时，调度一个 **ephemeral verifier subagent**（继承 ledger，丢弃中间 assistant 噪声）做最后一次断言，仍失败才报 `verify_exhausted`；与 N4 不冲突（这不是 peer-to-peer，是 orchestrator 内部短期子任务） | 00 §2 N4、§3 D6、02 §3 MR-9 |
| P1 | I3-3 | PRD 附录 B 承认"没触及 Spec Kit acceptance.sh 自动化"，但本 PRD 的 MR-0~MR-20 完全可以脚本化成 `scripts/acceptance.sh` 提供给 CI 一键跑；现状是手测条目散在 .md，没有可执行入口 | Stage 3 加 WI-T3.5：把 MR-0/MR-8/MR-11/MR-17/MR-19 自动化为 `scripts/acceptance/last_mile_*.py`，CI 在 PR 上跑（UI 部分用 Tauri test runner 或 windows-mcp 录像回放） | 00 §4 Stage 3、附录 B |
| P1 | I3-4 | D7 outcome verifier 在用户机器跑 `npm run build` / `pytest` 默认 off 是对的，但开 flag 后没有"本会话只跑改动文件 scoped"的强约束证据；TG-10 T10-5 用"注入 ts 错误后" 笼统 | TDD §C 加 D7 Schema：verifier 接受 `changed_files: list[str]` 入参，build/test 命令拼接时强制带文件级 filter；T10-5 验证未改动文件不被纳入 | 01 §B TG-10、§C |

## 维度 4 评分：5/10

### 通过项
- R1-R10 覆盖了 ClaimPattern 误判 / HMAC 丢失 / 磁盘占用 / I/O 拖慢主循环等明显项
- §7 每个 flag 单独回退路径清晰
- 一票否决 MR-0/MR-8/MR-19 选择合理

### 问题
| 严重度 | 编号 | 描述 | 建议修改 | 涉及文件:章节 |
|---|---|---|---|---|
| P0 | I4-1 | HMAC key 在 Windows 多用户机器（家用共享 PC、企业域账号切换）上仅靠 NTFS ACL "当前用户可读"是不够的：管理员/SYSTEM/其他高权限服务可读；用户切到管理员账号后旧 key 失效；roaming profile 漫游 key 进备份云 | R 表加 R11；D11 加注：key 派生用 DPAPI（Windows）/ Keychain（mac），不裸存文件；MR-19 19-1 改为验证 DPAPI 包裹而非 ACL | 00 §3 D11、§6、02 §3 MR-19 |
| P0 | I4-2 | ClaimPattern §C.3 的 regex 含 `[A-Z]:\\[^\s，。]+`——如果用户路径或用户名中含正则元字符（`(`、`+`、`.`、`$`），且 YAML 热加载允许用户自定义 pattern（MR-10 已暗示），存在 ReDoS（catastrophic backtracking）+ 加载用户输入 pattern 直接 eval 的风险 | TG-9 加 T9-12：热加载新 pattern 前用 `re2`（线性时间）编译；T9-13 注入 `(a+)+$` 类 ReDoS pattern 应被拒；YAML 加载走 safe_load + schema 校验 | 00 §3 D6、01 §C.3 |
| P0 | I4-3 | receipt jsonl 7 天保留只在 D5 一句话提及，但 **用户卸载/重装后 user_data 迁移**、**HMAC key 重生后旧 receipt sig_invalid 的归档处理**都没说。R2 只说"自动重生 + 标 sig_invalid"，但磁盘上一直累积 invalid 行 | D5 加注：每日启动时清理 ended_at < now-7d 的 receipt；HMAC key 重生时旧 jsonl 整文件归档到 `receipts/archived/` 而不留在主目录；R 表加 R12 卸载迁移 | 00 §3 D5、§6 |
| P1 | I4-4 | §7 flag 组合 hole：`emit_receipts=false` 但 `verify_gate_mode="strict"` 时，ledger 永远空，**所有 claim 都 unmatched，会无脑阻塞所有 end_turn** | D10 加 invariant 校验：启动时若 `verify_gate_mode != "off"` 且 `emit_receipts=false`，报 ValueError 拒启动；TG-1 加 T1-7 | 00 §3 D10、§7、01 §B TG-1 |
| P1 | I4-5 | `build/test` verifier 在用户机跑：用户机器没装 Node/pytest、`node_modules` 不存在、用户网络受限拉不到依赖 → verifier 自身报错被分类为 `build_error` 回灌，LLM 永远修不好 | T10-5 加前置检测：`which npm` / `which pytest` 缺失则 verifier 直接 PASS（不阻 end_turn），log skip 原因；R 表加 R13 | 00 §3 D7、§6、01 §B TG-10 |
| P1 | I4-6 | 一票否决项遗漏：**MR-13（file_exists verifier）**实际上是 G4 的核心，若失效则 outcome verification 整盘空转，应升一票否决；当前 MR-13 是非否决 | MR-13 升为一票否决；§5 退出标准同步 | 02 §1、§3 MR-13、§5 |

## 跨维度优化建议（架构级，非缺陷）

1. **[O1] 用 OpenTelemetry span 替代裸 receipt jsonl**（中工作量，~3 人日）：receipt 本质是 attested span，直接落 OTLP 既能签名又能进 trace 查看器；未来对接 Honeycomb / Grafana Tempo 零成本。代价是新依赖。
2. **[O2] VerifyGate 改为可插拔 strategy**（小，~1 人日）：当前 D6 把 regex 强耦合；抽 `ClaimExtractor` interface（regex/nli/llm 三实现），D6 默认 regex+llm 二级，未来切换零改动。
3. **[O3] ArtifactCard actions 走 capability negotiation**（小，~1 人日）：D2 现在前端按 kind 硬编码默认 actions；让工具在 ToolArtifact 显式 declare actions + Tauri 侧 declare 平台 capability，前端取交集渲染，Linux/Mac 降级零特判。
4. **[O4] 把 MR-* 编译成 `scripts/acceptance/last_mile.py` 单入口**（中，~2 人日）：对齐 Spec Kit；PR check 自动跑，免人工重复。
5. **[O5] HMAC key 走 OS keystore（DPAPI/Keychain）+ 启动时 sanity HMAC echo**（中，~2 人日）：彻底解决 I4-1；副作用：测试 fixture 需要伪 keystore。

## 必须修复才能 merge 的 P0 清单

1. **I3-1**：ClaimPattern 增加小 LLM 二级 fallback（regex 单层 95% 抓获率不可达）
2. **I3-2**：第 3 次 verify 失败前增加 ephemeral verifier subagent 救援
3. **I4-1**：HMAC key 走 DPAPI/Keychain，不裸存 ACL 文件
4. **I4-2**：YAML pattern 热加载走 re2 + ReDoS 拒绝 + safe_load
5. **I2-2**：Tauri 路径白名单补 UNC / mapped drive / 8.3 短名覆盖

## 建议但非阻塞的 P1 清单

1. **I1-2**：sha256 大文件异步化 + receipt 等待签名
2. **I1-4**：fake_claims_50.jsonl 显式分桶（中/英/混合/同义/否定 各 ≥ 10）
3. **I2-1**：MR-0 0-6 显式校验 `artifacts` 键缺失而非空数组
4. **I2-3**：receipt 生成路径全程 `run_in_executor`，TG-7 加 100MB 并发测试
5. **I3-3**：MR-* 自动化为 `scripts/acceptance/last_mile_*.py`
6. **I4-3**：receipt 启动自清理 + HMAC 重生时旧 jsonl 归档
7. **I4-4**：flag 组合 invariant 校验（emit_receipts=false + verify=strict 拒启动）
8. **I4-5**：build/test verifier 前置 `which` 检测，缺工具直接 skip

## 文档间一致性问题清单

1. **[术语漂移]** PRD D6 用「verify 重试 3 次硬限」，TDD T9-6 用「重试 ≥ 3 次仍失败」，手测 MR-9「第 4 轮强制 end_turn」——三处计数边界语义不一致
2. **[字段缺失]** PRD D5 ToolReceipt 列了 9 个字段，TDD §C.2 Schema 列了 12 个 required，多出 `error_class/artifacts/iteration`——PRD 应补齐或 TDD 注明"required 含 D5 全部 + 显式 nullable"
3. **[路径漂移]** PRD D4 默认路径 `<YYYY-MM-DD>/<tool_name>/<title_slug>-<short_hash>.<ext>`，MR-5 5-1 写成 `营销周报-📊-<hash>.pptx`——emoji 是否进 slug 未约定
4. **[字段命名]** PRD D8 错误分类用 `unmatched_claim`/`missing_file`/`build_error`/`test_error`/`hallucinated_claim`（5 个），TDD T11-2 写"5 个新增"对得上，但 PRD §6 R 表与手测 MR 文案混用「path_mismatch」「sha256_mismatch」（来自 VerifyOutcome.UnmatchedClaim.reason）——需明确"error_class（D8 分类）"与"unmatched reason（C.4 enum）"是两套不同枚举
5. **[一致性盲点]** PRD §5 度量"≥ 60% 工具产物点击率"，手测无对应 MR 验证埋点链路；TDD 也无 TG

## 简洁结语

设计骨架 2026 标准合格，与 memory-upgrade 正交清晰、字节级回归与 flag 拓扑稳健。但 ClaimPattern 单层 regex、HMAC key 裸 ACL、verify 失败硬退、Tauri 白名单 Windows 边界这 4 处是会在 beta 用户机器上真实出事的薄弱点，必须先修。修完后可 ship。
