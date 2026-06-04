# DeskPet — 全局项目状态

> **最后更新**: 2026-06-04
> **维护方式**: 每完成一个里程碑 / 合并一个 worktree 后更新本文件。
> **用途**: 一页看清整个项目（所有并行工作流）的当前状态。新 session / 子代理
> 接手前先读这里。

---

## 1. 项目一句话

本地部署的桌面语音宠物：Live2D 桌宠 + 全本地语音管线（VAD → ASR → LLM → TTS）+
工具调用 + 长期记忆 + 技能系统。Tauri (Rust shell) + Python backend + React 前端。

---

## 2. 活跃工作流（git worktree 并行开发）

> 项目用多 worktree 并行开发，每个 worktree 独立分支 + 独立 dev 端口
> （见 `scripts/dev-worktree.ps1`）。下表为各分支当前状态。

| Worktree / 分支 | 负责模块 | 状态 | 文档 |
|---|---|---|---|
| **master** | 主线 — beta-100 ready 集成线 | ✅ 活跃，持续 merge | `README.md` |
| `feat/companion-code-v2` | Slash 命令 + /goal + 多 agent team + 工具 partition + prompt cache | ✅ 全套实现 + 真桌宠 E2E PASS | [plans/2026-05-25-companion-code-skill-upgrade/](../plans/2026-05-25-companion-code-skill-upgrade/) |
| `feat/fun-interactions-2026-05-31` | 12 个趣味交互（drag squash / tap burst / dizzy spin / time-of-day mood） | ✅ 已 merge 到 master (2f54960) | — |
| `fix/restore-ui-pack-2026-05-31` | UI 修复恢复（工作树 reset 丢失的 6 项） | ✅ 已 merge (fd55c9f) | — |
| `live2d-rewrite` | Live2D 渲染层重写 | 🟡 进行中 | — |
| `worktree-memory-upgrade` | 记忆系统 v2 升级 | 🟡 进行中（Stage 0/1 已合 PR #2） | [plans/2026-05-22-memory-system-upgrade/](../plans/2026-05-22-memory-system-upgrade/) |
| `feat/memory-stage2-followup-f1f2` | memory Stage 2 后续 F1/F2 + 真测挖出 F3/F4 | ✅ F1/F2/F3/F4 全修，单测全绿；**未 merge master** | [plans/2026-05-24-memory-stage2-followup.md](../plans/2026-05-24-memory-stage2-followup.md) · [F3/F4 缺陷](../plans/2026-05-31-memory-tools-flag-gating-bugs.md) |
| `feat/multi-provider-management` | 多 LLM provider 管理 | 🟡 进行中 | — |
| `tool-last-mile-upgrade` | 工具调用 last-mile（artifact + receipt + verify gate） | ✅ 已合 master（详 v3 优化） | [plans/2026-05-23-tool-last-mile-upgrade/](../plans/2026-05-23-tool-last-mile-upgrade/) |

**端口隔离**（`scripts/dev-worktree.ps1 -BackendPort N -VitePort M`）：
- master: 8100 / 5173（默认）
- 各 worktree: 8200+/5273+（手动指定，避免冲突）

---

## 3. 核心功能模块完成度

| 模块 | 状态 | 关键文档 |
|---|---|---|
| **语音管线** (VAD/ASR/LLM/TTS) | ✅ 生产可用 | `README.md` Quick Start |
| **桌宠 supervisor** (P5-S1) | ✅ 生产可用 | `README.md` §桌宠 supervisor |
| **长期记忆 + 自动总结** (P4-S20-D / memory-v2) | ✅ Stage 1/2 ship；F1-F5 全修；严测 4 Phase（33 用例）；**2026-06-02 审计修复 #1-#4**：FATAL-A 自动 backfill 兜底 + FATAL-B 静默降级告警 + MemEval 字面vs改写召回（改写 Recall@5=1.0 证 dense 真工作）+ **出厂点亮 facts_extract/enhanced_retriever/cross_key_merge 语义事实记忆栈**（真机 E2E 待跑）| `README.md` §长期记忆 + [memory-system-status](../plans/2026-05-23-memory-system-status.md) + [严测 spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) + [审计+最佳实践](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| **工具层** (registry + 权限 + 熔断 + last-mile + v3) | ✅ 生产可用 | [tool-layer-optimization-v3](../plans/2026-05-24-tool-layer-optimization-v3/) |
| **fake-completion VerifyGate** | ✅ 接电；出厂 off / dev **strict**（+9 claim patterns 含 code 场景）;strict 真机不误杀 + 单测 31/31 | [v3 §WI-T2.1](../plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md) + [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| **Code 模式工作流纪律** (superpowers 全套) | ✅ Layer 1A persona 3/3 E2E + plan 硬门 2/2 E2E + Layer 1B 偏好记忆 cosine 0.936 + verify strict;✅ **过夜并行(spec 3 轮 opus 评估锁定 → 子代理并行实现 → 完成度审计 11/11 ALL-COMPLETE)**：A1 意图记忆 + A2 /prefs(修 slash 结果静默丢 bug) + A4 plan 持久化三层 + A5 文档 + C2 边角 24 测 + B1 flaky closed + B3 merge companion-code-v2;单测 16 新文件全绿 + 全套 **2331 passed**;**真机 windows-mcp 待跑** | [05-LOCKED-spec](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) |
| **技能系统** (SkillLoader + 14 builtin) | ✅ 生产可用 | `docs/SKILLS.md` |
| **relay 登录集成** | ✅ ship | [relay-login-integration](../plans/2026-05-22-relay-login-integration/) |
| **Slash 命令 + /goal + 多 agent** (v2) | ✅ 实现 + 真测；**未 merge master** | [companion-code-skill-upgrade](../plans/2026-05-25-companion-code-skill-upgrade/) |
| **pet animation UX** | ✅ v1 ship；v2 进行中 | [pet-animation-ux](../plans/2026-05-24-pet-animation-ux/) |
| **Live2D 重写** | 🟡 进行中 | worktree `live2d-rewrite` |
| **OSS 开源准备** | 🟡 进行中（BUSL-1.1 + SPDX + sanitize） | [oss-prep-handoff](../plans/2026-05-27-oss-prep-handoff.md) |

---

## 4. 最近里程碑（倒序）

| 日期 | 里程碑 |
|---|---|
| 2026-06-04 | **工具层「极其严格」手工测试 — windows-mcp 真机 40/40 全 PASS ✅** — 按 `testcase/tool-layer-RIGOROUS-manual-test.md`(40 TC)逐 case 真机测：windows-mcp SendInput 真点击 + 剪贴板真输入 + CDP9333 定位/验证 + backend 行为日志三重证据。覆盖全部工具 + 中间件横切(权限门按category上色：截图证 write_file橙/shell红/desktop_write橙 + 熔断3次OPEN + last-mile ArtifactCard多产物 + receipt+HMAC+duration_ms非0 + verify gate off/shadow/strict)+ 6个config重启TC(disabled_toolsets双层门控★回归/dangerous_allowlist/default_timeout设计陷阱/strict_unknown_toolset潜在bug/artifact_envelope ON-OFF/verify shadow)+ 健壮性(优雅失败/越界写OS拦/大输出截断/并发tile隔离不串台)+ 已修bug回归(非法permission_category)。**执行期发现并修复 2 个真 bug**：①doc_create 嵌套 element 格式渲染成字面 dict 字符串(`backend/deskpet/tools/doc_tools.py:_add_element`)②金黄 hint 卡因 last-mile envelope 嵌套不触发(`tauri-app/src/code-panel/MessageBubble.tsx:splitToolError`);均修+复测通过。建可复用 harness(testcase/_cdp.py + _send.py + _approve.py + deskpet-input.ps1 SendInput圣杯)。详 [tool-layer-RESULTS.md](../testcase/tool-layer-RESULTS.md)。回归单测已补(doc_tools 2 + splitToolError 3,全绿)+ 已提交 commit 4e8f449。 |
| 2026-06-04 | **语音对话同步到消息框修复 — 真机 A/B 闭环 PASS ✅** — bug：桌宠主窗口语音不进「消息·主线程」（语音落库但消息框看不到、重开也不补）。**一次根因**：`VoicePipeline` 只 audio_ws point-to-point、从不广播；修复复用文字同款 `_broadcast_default_chat_peers` 发 chat_v2_user_echo/final、skip originator（主窗口已 audio 显示不重复）。**真机暴露二次根因**：`VoicePipeline.control_ws` 是 audio 连接期快照，backend respawn 后 audio 常先于 control 重连 → 快照=None → 旧守卫 `... and self.control_ws and ...` 挡掉广播（落盘诊断实锤 `control_ws_none=true`）。**二次修复**：守卫去掉 control_ws 依赖 + main.py audio_channel 注入实时解析 originator 的闭包（`_control_connections.get(session_id)`，对齐文字路径的实时 `_ws`）。**TDD 16 单测全绿**（含 `test_broadcasts_regardless_of_control_ws_snapshot` 根因守护：control_ws=None 仍广播）；architect 子代理逐行审计 P0/P1 → GO-with-changes；诊断证 fan-out `control_keys` 含 `message-panel-main`、`originator_in_values=true`。**真机 A/B**：修复前真人语音停在 id2152 不进消息框（[11 截图](../plans/manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png)）；修复后真人语音「提醒买菜」(id2157/2158) **实时进消息框**（用户截图确认）。详 [fix-spec v2](../plans/2026-06-03-voice-msgpanel-sync/00-fix-spec.md) |
| 2026-06-04 | **多屏跨 DPI 拖动 + Live2D 角色渲染修复（master 直提）** — 用户实测桌宠在双屏（Samsung 主屏 dpr 2.13 + Xiaomi 副屏 dpr 1.42，webview dpr 异常比显示器 scale 高 ~1.42×）：①拖不回小屏（抖动+弹回）②拖几次只显示一半 ③拖到小屏角色右半被裁。**7 处根因**：`window_geometry.rs` 移除 on_resize clamp（跨 DPI 振荡）+ `pin_size`（逻辑尺寸跨屏舍入漂移 375→360→657）；`App.tsx` 边缘吸附 250ms 防抖 + 显示器局部坐标转换（非主屏 pickEdge 误判甩回主屏）；`Live2DCanvas.tsx` 角色列宽 cap 在视口内 + dpr 纳入 size 状态/matchMedia 触发画布重渲 + `modelReady` 触发异步模型加载后重渲 + scale 用基础尺寸（避免读已缩放 model.width 致模型爆炸）+ **删除重复 resize effect**（旧版拖动时覆盖修好结果）。真机 SendInput 拖动 + 白板背景截图验证：三星/小米 boot + 拖动后角色均从头到脚完整居中；10 次跨屏尺寸 pin 360×600 零漂移。详 [手测报告](../plans/manual-results-2026-06-03-multimon-drag/REPORT.md) |
| 2026-06-02 | **记忆系统审计 + 修复 #1-#4（master 直提，4 commit）**：3 路交叉验证（我的代码审计 + silent-failure-hunter 子代理 + 最佳实践调研子代理）。**#1 FATAL-A**(`4b700dd`)：lifespan 从不调 backfill_missing → 任何 embedding 缺口永久无声（"刚说的话下次不记得"）→ 加启动自动 backfill 兜底 + 修正虚假注释。**#2 FATAL-B**(`30cbcdf`)：检索/嵌入静默降级只 log.debug → retriever FTS / facts embed / enhanced_retriever 降级点升 warning（vector_worker 已 warning 故不动）。**#3 MemEval**(`56c9c59`/`48d8549`)：~18 双语"字面vs改写"召回对照，真 BGE-M3 改写 Recall@5=**1.0**（证 dense 语义召回真工作、非吃 FTS 字面红利）+ 修模型路径脆弱性（用户迁 F 盘后 C: 硬编码致 model_required 整批 ERROR → 新增 resolver）。**#4**(`4d8de40`)：冲突消解机制（mem0 merge/supersede + Zep 软失效 + 时序链）早已建好且 37 测试全绿，用户决策出厂点亮 facts_extract+enhanced_retriever+cross_key_merge（dataclass 默认仍 False 保字节契约；**真机 E2E 待跑**）。详 [审计+最佳实践报告](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| 2026-06-02 | **superpowers ③ verify_gate strict + code claim patterns — 真机不误杀 + 单测 9/9 PASS**：决策3"硬卡"。claim_patterns.yaml 补 4 条 code 场景(已创建/已修改/测试通过 + en)5→9;dev 翻 `verify_gate_mode=strict`(出厂仍 off)。关键判断:registry.execute_tool 对每个工具都 emit_receipt → 真调过的任务 claim 命中放行、裸声明(fake)拦。真机:STRICT_CHECK.md 任务 write_file→无 verify_gate_nudge→放行完成(未误杀);单测证 fake 无 receipt→拦、未来时→不误判、shadow→不拦。出厂是否翻 shadow 待定。详 [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| 2026-06-02 | **superpowers Layer 1B 偏好记忆(BGE-M3) — 真机 E2E + 单测 7/7 PASS**：决策2 的"记下来后续相同直接做"。新组件 `preference_memory.py`（计划/意图两类 + BGE-M3 cosine + JSON 持久化 + list/clear）。计划记忆接 plan-confirm 门:用户点[执行]→record approved;相似任务 match 命中→自动确认跳过等待。真机:Task A(PREF_ALPHA)走门点[执行]记录→Task B(PREF_BETA 只改文件名)`plan_confirm_auto_approved score=0.936`无 awaiting 直接跑→文件创建。接线踩坑:ServiceContext register 有 allowlist(加字段+白名单)。flag 默认 OFF 出厂不构造。意图记忆(决策1)组件已支持待接线。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1b-preference-memory.md) |
| 2026-06-02 | **superpowers Layer ① plan-confirm 硬门 — GO+CANCEL 真机 E2E 2/2 PASS**：决策2 严格版——code 模式明确任务先出 plan + 等用户点[执行]再跑 ReAct。复用现成 `maybe_extract_plan`（plan.py 自标的 "future enhancement"），加确认门(后台 task await Future 不阻塞 recv loop，最小改动)+ `plan_confirm` WS + 前端 [执行]/[取消] 按钮。**调试挖出真问题**:grid tile 预览(SessionGridView)自己的 renderer 过滤掉了 "plan" 角色 → tile 内单独渲染确认栏修复。CDP 真机:GO→暂停(零 dispatch)→点[执行]→todo+list+write+read 执行→GATE_OK.md 建成;CANCEL→点[取消]→零执行+文件不创建。flag 默认 OFF 出厂字节级不变。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-plan-confirm-gate.md) |
| 2026-06-02 | **superpowers 工作流集成 code 模式 — Layer 1A 落地 + 真机 E2E 3/3 PASS**：用户反馈 auto 模式"问个问题就埋头乱改、不澄清、不验证就说完成"。调研发现根因是 code 模式纯 ReAct 无工作流骨架 + persona 通篇"优先使用工具"。**纠正一处诊断错误**：verify_gate/goal_checker 并非"孤儿代码"，agent_loop 早接好了，卡点是配置 flag 默认 off（本项目反复出现的模式）。**Layer 1A**：重写 `_CODE_MODE_PERSONA_TEMPLATE`（意图门→澄清→计划→执行→验证）+ dev 翻 `verify_gate_mode=shadow`+`emit_receipts`+`goal_mode`。**真机 CDP E2E**（test-research-helper tile，deepseek-v4-pro，全栈非注入）：TC-1 问模型→直答"deepseek-v4-pro"零工具(治#1)；TC-2 模糊派活→先澄清目标/范围/成功标准、零文件改动(治#2/#3)；TC-3 明确任务→todo拆步骤+写前看现状+**写后read_file读回验证**+报告校验结果(治#5)。决策：偏好记忆走 BGE-M3。Layer 1B(偏好记忆)+ shadow→strict 待做。详 [proposal](../plans/2026-06-02-superpowers-code-workflow/proposal.md) + [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1a.md) |
| 2026-06-01 | **工具层全功能手测（按 testcase 真桌宠 E2E）+ 修复 2 个真 bug**：A 类 5 例 CDP 真注入桌宠 WebView2（PPT/Excel/Word/web_fetch/能力门控，4 PASS + windows-mcp 真操作原生保存对话框）；B 类配置契约核对。**挖出并当日修复 2 bug**：① `doc_create` 生成空文档（element 格式契约 `{heading}` vs `{type,text}` 不匹配 → `doc_tools.py` 加归一）② `_load_tools` 漏读 `disabled_toolsets`/`dangerous_tools_allowlist`/`default_timeout_seconds` 等 WI-T5.1 字段（`config.py` 补读）→ 该 3 功能此前配了不生效。真桌宠闭环复测 disabled 生效（excel_create 0 调用，LLM 绕道）；pytest 59 passed。新增 testcase/ 手测体系。详 [tool-layer-test-report](../plans/manual-results-2026-06-01/tool-layer-test-report.md) |
| 2026-06-01 | **记忆系统严测（4 Phase 全收 / G1-G6 + 性能基线，33 新用例 master 直提）**：真机 GUI 终验推翻草率 PASS，挖出并**全修 F5**（facts.search/workspace.recall/find_by_entities 的 `LIKE '%整串%'` → 自然语言 query 永不命中）：① 分词 OR LIKE（`text_tokenize.py`）② memory_search 向量优先（真 BGE-M3）。G5 戳破 eval_gate hit@5 字面驱动（mock==real Δ=0）。G6 钉死 embedding 列真写入 + 写入并发不变量。性能基线：检索热路径 N=500 median 1-3.5ms（护栏非微基准）。CI 跑真 embedder。详 [memory-system-rigorous-test-spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) §8 |
| 2026-05-31 | memory Stage2 followup F1/F2 完成 + 真机 GUI 真测挖出并修复 F3（memory_search 误连坐 forget flag）/F4（code 工作记忆出厂默认开，保字节级契约）；单测全绿，未 merge |
| 2026-05-31 | companion-code v2（slash/goal/team/partition/cache）全套 + 真桌宠 WebView2 E2E PASS；fun-ux 12 交互 merge；dev-worktree.ps1 跑源码修复 |
| 2026-05-27 | OSS 开源准备（LICENSE / SPDX / 凭据脱敏 / CI 适配） |
| 2026-05-24 | 工具层优化 v3（VerifyGate 接电 + stubs 真实现 + ToolsConfig 扩展）；pet-animation UX |
| 2026-05-23 | 工具 last-mile 升级；memory-v2 Stage 2 |
| 2026-05-22 | beta-100 内测就绪；relay 登录集成；builtin skills |

---

## 5. 已知问题 / 测试纪律

- **dev 模式必须用 `scripts/dev-worktree.ps1`**（worktree）或 `dev-start.ps1`（主树）启动 —
  直接 `npm run tauri dev` 会用 stale 打包 exe（旧版本，缺新 endpoint）。详见脚本注释。
- **手工测试纪律**（CLAUDE.md HARD CONSTRAINT）：UI 改动必须 windows-mcp / CDP 真测，
  不能用单测 / 协议层替代。真桌宠 WebView2 测试用 CDP 9222（dev 默认开）注入真实输入。
- **DPI 坐标**：这台开发机 OS scale 150% + WebView dpr 2.13；SendInput 物理点击需正确
  换算（详 [16-sendinput-webview2-final-diagnosis](../plans/2026-05-25-companion-code-skill-upgrade/16-sendinput-webview2-final-diagnosis.md)）。
- 其它已知问题见 `README.md` §已知问题（Known Issues）+ `docs/beta/已知问题.md`。

---

## 6. 文档索引

- **架构 / 模块文档**: [`docs/INDEX.md`](../docs/INDEX.md)
- **手工测试用例索引**: [`testcase/index.md`](../testcase/index.md)
- **README**（用户 + 开发者入口）: [`README.md`](../README.md)
- **项目级开发笔记**: [`CLAUDE.md`](../CLAUDE.md)
- **迭代 plan 目录**: `plans/2026-*`（每个迭代一个文件夹，含 PRD/TDD/manual-test/report）
- **OSS 准备**: [`plans/2026-05-27-oss-prep-handoff.md`](../plans/2026-05-27-oss-prep-handoff.md)

---

## 7. 如何更新本文件（HARD 纪律）

> **铁律**：任何任务一旦"通过测试完成"（pytest/vitest/cargo/手工 E2E 全绿），
> **必须在同一次交付内**同步更新本文件 —— "跑过测试但没更新 STATUS" = 任务未完成。
> 详见 [`CLAUDE.md` §STATUS 更新纪律](../CLAUDE.md)。

完成以下任一事件后更新：
1. 一个 WI / slice / 功能模块跑通验收 → 更新 §3（🟡 → ✅ 或新增行）
2. 里程碑级完成 → 追加一行到 §4 最近里程碑
3. 一个 worktree 合并到 master → 更新 §2 表格状态
4. 发现新的项目级已知问题 / 测试纪律 → 更新 §5

每次更新都改顶部"最后更新"日期。保持一页能看完（细节放各 plan 文档，这里只给状态 + 链接）。
