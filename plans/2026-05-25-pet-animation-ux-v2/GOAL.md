# GOAL — Pet Animation UX v2 实施指令

| 项 | 值 |
|---|---|
| 关联文档 | `PRD.md` v3 / `TDD.md` v2 / `ManualTest.md` v2 (本目录) |
| 创建时间 | 2026-05-25 |
| 状态 | **存档，不立即执行**。用户拍板后复制本指令到新会话或 codingsys `/loop` 中触发 |
| 预计 Sprint | 3 sprint（每 5-7 工作日，含后端并行 lane）|
| 评审记录 | round-1: 5.2/10 NEEDS-MAJOR-REWORK → round-2: 7.6/10 GO-WITH-MINOR-FIXES（MINOR 已在 v3 应用） |
| 终止条件 | 子代理手测全 PASS（D0 6 探针 + 13 FR P0 + AC-3 snapshot + AC-10 一票否决 + BLIND PASS/WARN） |

---

## 使用方法

复制"指令正文"分隔线之间内容到新 Claude Code 会话（推荐 Opus 4.7 + 长上下文）。本指令自包含。

---

## 指令正文（复制此分隔线以下）

────────────────────────────────────────────────────────────

你的角色：deskpet 桌宠项目的实现工程师 + 测试驱动者。

【合同文档 — 当作 spec 严格遵守】
- `G:\projects\deskpet\plans\2026-05-25-pet-animation-ux-v2\PRD.md` (v3)
- `G:\projects\deskpet\plans\2026-05-25-pet-animation-ux-v2\TDD.md` (v2)
- `G:\projects\deskpet\plans\2026-05-25-pet-animation-ux-v2\ManualTest.md` (v2)

【任务】
按 PRD/TDD 完成 Pet Animation UX v2 — 13 项业界对照 FR 全部纳入 scope。

13 FR 清单：
1. **A1** 拖拽 → being_held + 纯参数 wobble + spring back
2. **B1** 用户输入中 → 微歪头 + 听音 (IME 兼容)
3. **B2** LLM 思考中 → 看天 + first-chunk 退出
4. **B3** TTS viseme lipsync — **双路径都做**（主：后端 viseme；fallback：前端 phoneme 估计器）
5. **B4** 说完静默 fade + 800ms 兜底
6. **C1** >5min idle → low-energy + yawn (S2 motion calibration)
7. **C2** 用户回归 → 想你了 (5min/15min/1h 三档 escalation)
8. **C3** 整点/纪念日 (DND 抑制 hourly，anniversary 不抑)
9. **D1** 回复情感分类 → 表情 TTS 锁定 — **双路径都做**（主：后端 LLM emotion；fallback：前端投票分类）
10. **D2** memory milestone (5 条规则)
11. **E1** 屏幕边缘 → 旋转挂边
12. **E2** 窗口遮挡 → 主动挪开 (默认 on + consent)
13. **F1** 全屏/打字/通话 → DND (通用 audio session，不 hardcode)

【绝对纪律】
- ❌ **严禁 scope drift**：任何 "v3" / "推迟" / "如 calibrated" / "Plan B 砍 13 项" / "等后端 ready" 措辞 → 立即停下回报用户
- ❌ 不允许把 13 项任一推到未来 Sprint
- ✅ 允许 graceful degrade（性能差 → 降频，不砍 FR）
- ✅ 允许 fallback 路径（主路径 fail → 自动跑 fallback，仍属 v2 scope）

【阶段 1 — 实现（TDD 红绿循环 3 sprint）】

**Sprint 1（D0-D2）**
1. **Day-0 6 探针**（TDD §0）：Probe-A1 / B3-后端 / B3-前端 viable / D1-Hiyori (含 ParamBreath/HairFront) / D1-后端 / D2 / E2 / F1 通用 audio
2. 写 evidence/round-0/probes.md；任何 FAIL → graceful degrade 路径明示
3. **v3 round-2 MINOR F-2 矩阵补完**（已在 PRD §6.2 ready；实施期严格按矩阵 + 计算次序规则写 applyTo）
4. 前端 FR 顺序：A1 held + B1 user_input + B2 thinking + B4 mouth fade
5. **后端 S1 sprint（并行 lane）**：client_hello/server_hello 协议握手
6. CI 全绿（tsc + lint + test:anim:cov + test:e2e-wire-v2 + test:ac3-snapshot）

**Sprint 2（D3-D5）**
1. 前端：B3 viseme 主+fallback；D1 emotion (双路径) + 5 类视觉；C1 low_energy + transition 图；C2 welcome with escalation
2. **Motion calibration sub-task**：用 HiyoriMotionTuner 跑 m01-m10，给 yawn/dodge/edge 三 tag 校准
3. **后端 S2 sprint（并行）**：
   - `backend/tts/viseme_provider.py` — TTS provider 输出 viseme 流（3d）
   - `backend/llm/emotion_prompt.py` — LLM system prompt 教学 + chat_v2_final emotion 字段（1d）
   - `backend/memory/milestone.py` — 5 条 milestone 规则 + ws push（2d）
   - Tauri Rust commands × 3：enumerate_top_windows / is_foreground_fullscreen / is_any_audio_capture_active（2d）

**Sprint 3（D6-D8）**
1. 前端：C3 time celebration + D2 milestone client + E1 edge + E2 occlusion + F1 DND；UI: PetCelebrationBubble + PetDNDBadge + Permission Consent
2. AC-3 v1 零回归 snapshot test 套件实现（PRD §6.11，4 条）
3. 评审 + BLIND v1 vs v2 录视频
4. 后端：调通 + Permission consent UX 联调

【实现纪律】
- 严格按 PRD §3 每 FR 验收行 + §6.2 多 FR 优先级矩阵（10x10）+ §6.3 跨层 ws 协议
- 任 FR 收口必须满足：P0 + P1 全过 + coverage ≥80%L/70%B + tsc clean + lint 不新错
- 全程 NFR-6 时钟基同源（DOMHighResTimeStamp）— 禁直接调 Date.now()/performance.now() 不注入
- TDD §3.7 fallback 路径**全部实现**（不接受 silent skip）
- 用 TodoWrite 跟踪每 FR 进度

【阶段 2 — 手测验收（必须 Opus 4.7 子代理）】

实现完成后，启动**新的 Opus 4.7 子代理**执行 ManualTest。子代理 prompt 必须包含：

```
你是严格的 QA 工程师，按 G:\projects\deskpet\plans\2026-05-25-pet-animation-ux-v2\ManualTest.md v2 执行手测。

【硬性规则】
1. 按 §1 矩阵顺序：D0 → A1..F1 → PERF → AC-3 → AC-10 → BLIND
2. 严禁跳过任 P0
3. 严禁"应该会 PASS"代替真实观察 — 必须真启 Tauri dev + 真操作 + 真截图录屏
4. PERF case 必须关 DevTools 后用任务管理器测
5. 每 case 截图/录屏在 evidence/round-N/
6. FAIL 按 ManualTest §21 模板生成报告
7. 不允许直接改 src 代码

【启动步骤】
1. 读 G:\projects\deskpet\CLAUDE.md 获取登录测试账号（<dev-test@example.com> / <redacted-see-LOCAL-DEV-CREDENTIALS.md>）
2. 按 ManualTest §0 启 backend + tauri dev + CDP 9222
3. 跑 §2 CASE-D0-01..06 6 探针；任 FAIL 在 evidence/round-N/probes-runtime.md 标 + 按 PRD §8 graceful degrade（**不砍 FR**）
4. 按 §3-§19 顺序跑所有 P0 case
5. 终末生成 evidence/round-N/SUMMARY.md：通过/FAIL 列表 + 最终判定（PASS / NEEDS-FIX）
6. SUMMARY 全文回复主 agent

【可用工具】
- windows-mcp（真 OS 手测 / 真鼠标 / 真键盘）— 必备
- Claude_in_Chrome MCP（CDP 9222 attach）— 必备
- cdp-runner.mjs（复用 v1 round-2 工具）
- Win32 mouse_event PowerShell wrapper（复用 v1 round-3 工具）
- Bash / Read / Write

【交付】
1. evidence/round-N/probes-runtime.md (Day-0 6 探针)
2. evidence/round-N/case-*.png/webm (每 P0 case)
3. evidence/round-N/FAIL-*.md (每 FAIL 一份)
4. evidence/round-N/SUMMARY.md (最终)
5. evidence/blind-test/A.mp4 + B.mp4 + results.md (CASE-BLIND-v2-01)
```

【阶段 3 — 修复循环】

如子代理返回 NEEDS-FIX：
1. 仔细读 SUMMARY 和每 FAIL §21 报告
2. 用 TodoWrite 把每 FAIL 转一个 task
3. 按 FAIL "可能根因" 做最小修改（不许范围外重构）
4. 修复后必须重跑相关单测确认不回归 + `pnpm test:anim:cov` 不掉
5. 一 FAIL 一 commit（message 引用 case id）
6. 修复完成后**再启一个新的 Opus 4.7 子代理**重跑整套手测（prompt 完全复用上面那段）

【阶段 4 — 直到 PASS】

重复阶段 2→3，直到子代理返回 PASS。

【终止条件（必须全部满足）】
- Day-0 6 探针 PASS 或合法 graceful degrade（不砍 FR）
- 13 FR 所有 P0 case PASS
- AC-3 v1 零回归 4 条 PASS
- AC-10 4 个一票否决全过
- PERF 全达标（FPS≥28 / applyTo≤0.7ms/call / CPU+≤7% / RAM+≤50MB）
- CI 全绿（tsc + lint + test:anim:cov + test:e2e-wire-v2 + test:ac3-snapshot）
- CASE-BLIND-v2-01 PASS / WARN（不能 FAIL）
- evidence/ 归档完整

【最终交付】
写 evidence/FINAL_REPORT.md（round-N 编号、Day-0 摘要、13 FR 各 ✓、AC-1~AC-10 ✓、Plan B 是否触发、BLIND 结果）；commit："feat(pet-anim-v2): ship — see plans/2026-05-25-pet-animation-ux-v2/evidence/FINAL_REPORT.md"

【绝对禁止】
- 修改 PRD/TDD/ManualTest（合同；发现问题先 evidence/blockers.md 报告，停工等用户决策）
- 跳 Day-0 探针、跳 P0、跳 evidence
- 用 vitest 代替手测
- 直接 push 到 master 远端
- **scope drift（"留 v3" / "推迟" / "如 calibrated"）**

【现在开始】
1. 先读完 3 份 spec
2. 用 TodoWrite 列本 sprint 所有 task（按 §8 里程碑表 + S1/S2/S3 phase 分组）
3. 进 Day-0 6 探针

────────────────────────────────────────────────────────────

## 触发本指令的方式

任选其一：
- **方式 A**（推荐）：把"指令正文"分隔线之间内容复制到新 Claude Code 会话
- **方式 B**：用 codingsys `/loop` skill，每 30 分钟自检进度
- **方式 C**：分阶段触发 — 先 S1 实现 + 探针，手动验证后再进 S2

## 与 v1 GOAL 的差异

v1 GOAL（FR-1~FR-7 v1）与 v2 GOAL 关系：
- v1 已 ship at commit `c2b9586` + `0ed4597`（FIX-R3）
- v2 完全独立路径，**严守不破坏 v1**（AC-3 4 条 snapshot test）
- v2 与 v1 并存：v2_all=off 时退回 v1 完整行为；v2_all=on 时叠加 13 项

## 备注

本指令核心目标不是"快"，而是**"13 项全部完整实施 + 不留任何 scope drift"**。
若 Sprint 期间发现某项工程量超预算 → **立即停下回报用户**，由用户决定是 graceful degrade（降级实现路径）还是延期。**严禁自行砍 FR。**

────────────────────────────────────────────────────────────

**用户决策点**（待回答）：
1. 何时触发？
2. 用方式 A/B/C 哪种？
3. 后端工程师是否可投入 S2 sprint 8.5d 工作量（B3 viseme provider + D1 emotion prompt + D2 milestone + Rust commands）？若无后端资源，主路径退到 fallback（v2 内备齐）。
