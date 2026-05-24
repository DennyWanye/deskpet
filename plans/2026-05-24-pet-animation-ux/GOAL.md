# GOAL — Pet Animation UX v1 实施指令

| 项 | 值 |
|---|---|
| 关联文档 | `PRD.md` v3、`TDD.md` v3、`ManualTest.md` v3 |
| 创建时间 | 2026-05-24 |
| 状态 | **存档，不立即执行**。用户拍板后复制本指令到新会话或 codingsys `/loop` 中触发 |
| 预计 Sprint | 5 天（含 Day-0 探针）|
| 终止条件 | 子代理手测全 PASS（含 Day-0 + 全 P0 + BLIND PASS/WARN） |

---

## 使用方法

将下面"指令正文"复制到新的 Claude Code 会话（推荐 Opus 4.7 + 长上下文）。本指令是自包含的，新会话无需上下文。

---

## 指令正文（复制此分隔线以下到新会话）

────────────────────────────────────────────────────────────

你的角色是 deskpet 桌宠项目的实现工程师 + 测试执行人。本次任务的全部规格都在以下三份文档里，请把它们当作合同：

- `G:\projects\deskpet\plans\2026-05-24-pet-animation-ux\PRD.md` — 产品需求（FR-1~FR-7、NFR、Flag 对照表、接口契约、Latency Pairing Rule、风险与里程碑）
- `G:\projects\deskpet\plans\2026-05-24-pet-animation-ux\TDD.md` — 技术设计（Day-0 探针、9 个 pet-anim/* 模块接口、关键算法、测试用例、Mock 策略）
- `G:\projects\deskpet\plans\2026-05-24-pet-animation-ux\ManualTest.md` — 手测脚本（D0 探针 + P0 case 全集 + 降级路径 + 性能 + 盲测）

### 阶段 1 — 实现（TDD 红绿循环）

按下述顺序，**严格遵守 TDD §9 流程**（红→绿→重构→下一 FR），完成 PRD 中所有 FR：

1. **Day-0 探针**（TDD §0）：实现 4 个探针、跑、写 evidence/round-0/probes.md、按 ManualTest CASE-D0-CLEANUP 清理探针代码（git revert + prod build 验证）
2. **D1**：实现 9 个 `pet-anim/*` 模块的所有 P0 + P1 单测（每个 FR 红→绿→重构）；按 TDD §4 章节顺序：perlinNoise → blinkScheduler → saccadeScheduler → gazeTracking → motionPicker → motionScheduler → pointerReaction → metricsRing → featureFlags
3. **D2**：拼装 AnimationOverlay（TDD §2.10 + §3.6 应用顺序 + §3.7 失败 fallback + §3.8 Latency Pairing Rule），接入 Live2DCanvas render loop；完成 FR-1/FR-2/FR-3 的视觉接入（应用 §6.0 hit-zone 自适应）
4. **D3**：FR-4 视线追随（用 window pointermove 监听，DOMHighResTimeStamp 时钟基同步） + FR-5 motion 标签消费（含 force_switch_now 联动 PetStateMachine）
5. **D4**：FR-6 hit-zone div + pointerReaction transition table + FR-7 双指标埋点（interaction_latency + visual_latency + FIFO 配对）
6. **D5**：Feature flag 收尾 + 准备手测

**实现纪律**：
- 每个 FR 必须先写 P0 单测让它失败（红），再实现到通过（绿），再加 P1（重构）；不允许"先写实现再补测试"
- 每个 FR 收口必须满足 TDD §9 checklist：P0/P1 全过 + coverage line ≥80% / branch ≥70% + `pnpm tsc --noEmit` 无新错
- 全程使用 `TodoWrite`（或当地 task 工具）跟踪进度，每个 FR 一个 task
- 严格按照 PRD §6 接口契约写代码；不允许擅自扩接口
- 严格按照 TDD §3.7 写 fallback 路径（add 不存在、参数 idx=-1、loader null、localStorage throw 等）
- 不允许跳过 Day-0 探针；任何探针 FAIL 必须按 PRD §6.0 / FR-3 / FR-6 的降级路径调整设计，并在 evidence/round-0/probes.md 记录
- 不允许提交 `import.meta.env.DEV` 探针代码到 master；CASE-D0-CLEANUP 是阻断性 case
- 全程不允许直接调用 `Date.now()`/`performance.now()`，所有 now_t 必须按 NFR-6 注入

**CI 门控**（每个 FR 收口前跑）：
```
pnpm tsc --noEmit
pnpm lint
pnpm test:anim:cov     # 覆盖率门控
pnpm test:e2e-wire     # 端到端 wire 测试
```
全过才能切下一 FR。

### 阶段 2 — 手测验收（子代理执行）

实现完成后，启动一个**子代理（Opus 4.7 模型）**执行手测。给子代理的 prompt 必须包含：

```
你是一位严格的 QA 工程师，专注桌面应用的视觉/交互/性能验收。请按以下文档执行手测：

- G:\projects\deskpet\plans\2026-05-24-pet-animation-ux\ManualTest.md

【硬性规则】
1. 必须按文档顺序执行：CASE-D0-01..04 + CASE-D0-CLEANUP → CASE-P → B → S → G（含 G-05-FALLBACK / G-06）→ MP → PR（含 PR-FALLBACK / PR-06）→ MET → PERF → REG → HMR → COLD → BLIND
2. 严禁跳过任何 P0 case
3. 严禁用"应该会 PASS"代替真实观察 — 必须真实启动 Tauri dev、真实操作、真实截图/录屏
4. 所有 PERF case 必须关闭 DevTools 后用任务管理器测
5. 所有 case 都要在 evidence/round-N/ 留下截图或录屏
6. FAIL 必须按 ManualTest §17 模板生成报告：实际现象、预期、复现步骤、window.debug() 输出、截图、Day-0 探针结果、可能根因、建议修复方向
7. 子代理不允许直接修改代码 — 只负责执行和报告

【启动步骤】
1. 跑 ManualTest §0 前置准备（清进程 + 启动后端前端 + DevTools helper）
2. 按 §2 跑 D0 探针；任何 FAIL 立即在 evidence/round-0/probes.md 标记并按 PRD 降级路径调整后续 case 选择
3. 按 §3-§14 顺序跑所有 P0 case
4. 终末出一份 evidence/round-N/SUMMARY.md，包含：通过的 case 列表、FAIL 的 case 列表（每个含 §17 报告链接）、最终判定（PASS / NEEDS-FIX）
5. 把 SUMMARY.md 全文回复给我

请开始执行。
```

### 阶段 3 — 修复循环

如果阶段 2 子代理返回 NEEDS-FIX：

1. 仔细读 SUMMARY.md 和每个 FAIL case 的 §17 报告
2. 用 TaskCreate 把每个 FAIL 转成一个 task
3. 按 FAIL 报告的"可能根因"和"建议修复方向"做最小修改（**不许做范围外重构**）
4. 修复后必须重跑相关单测确认不回归 + 跑 `pnpm test:anim:cov` 不掉
5. 提交一个修复 commit（一个 FAIL 一个 commit，message 引用 case id）

修复完成后**再启一个全新的子代理（Opus 4.7）**重跑整套手测（阶段 2 的 prompt 完全复用）。

### 阶段 4 — 直到 PASS

重复阶段 2 → 阶段 3，直到子代理返回 PASS（含全 P0 + BLIND-01 PASS 或 WARN）。

**终止条件（必须全部满足）**：
- Day-0 探针 4 项 PASS 或有合法降级 evidence
- ManualTest §16 列出的全部 P0 case PASS
- CASE-BLIND-01 PASS 或 WARN（不能 FAIL）
- 所有 PERF case 达标（FPS ≥28、applyTo ms/call ≤0.5ms、CPU 增量 ≤5%、RAM ≤30MB）
- CI 全绿：`pnpm tsc --noEmit && pnpm lint && pnpm test:anim:cov && pnpm test:e2e-wire`
- evidence/ 归档完整（每 P0 case 有截图/录屏；MET 有 console；PERF 有任务管理器截图）

**最终提交**：
- 写一份 `evidence/FINAL_REPORT.md`，包含：最终通过的 round-N 编号、Day-0 探针结果摘要、AC-1~AC-8 逐项 ✓、Plan B 是否被触发、与 PRD §AC-8 BLIND 结果
- 通过 commit message `feat(pet-anim): ship v1 (FR-1~FR-7) — see plans/2026-05-24-pet-animation-ux/evidence/FINAL_REPORT.md` 标记 Sprint 完成

────────────────────────────────────────────────────────────

## 触发本指令的方式（用户决策）

任选其一：

- **方式 A**（推荐，autonomous）：把"指令正文"分隔线之间的内容复制到新 Claude Code 会话，让它独立跑完
- **方式 B**：用 codingsys `/loop` skill，每 30 分钟让 agent 自检进度，遇 FAIL 自动进修复循环
- **方式 C**：分阶段触发 — 先让一个 agent 跑阶段 1（实现 + TDD），手动验证后再触发子代理跑阶段 2，逐次推进

## 责任与边界

- 实现 agent / 子代理**不允许**：
  - 修改 PRD/TDD/ManualTest（这三份是合同；若发现合同有问题应在 evidence 里写 BLOCKER 报告，停止开工等用户决策）
  - 跳过 Day-0 探针、跳过任何 P0 case、跳过 evidence 归档
  - 用 vitest 单测代替手测（vitest 测不了视觉/性能/穿透）
  - 直接 push 到 master（必须先在 worktree 或 feature branch 跑通，commit by commit review）

- 实现 agent / 子代理**应当**：
  - 严格遵守 TDD §9 红绿循环纪律
  - 遇阻立即在 evidence/round-N/blockers.md 记录、回报用户、不擅自绕过
  - 充分利用 NFR-6 时钟注入和 TDD §5 测试基础设施（fakeRng/fakeClock/stubModel/windowEvents）
  - 把 TDD §10 实现注意点（pixi 版本 pin、coreModel 路径回退、HMR safety）当 D0 / D1 必读

## 与 deskpet 已有规范的关系

- 遵守用户 CLAUDE.md "spec-first" — 本 GOAL 即是 spec
- 遵守 memory "feedback_simulate_manual_test" — 阶段 2 子代理是模拟人工测试的强制要求
- 遵守 memory "feedback_real_e2e_not_script_replay" — 不允许跑 unit test 当 E2E 证据
- 遵守 memory "feedback_deskpet_branch_strategy" — 尽量在 master 直接开发（除 worktree 隔离需求）
- 遵守 memory "feedback_no_sandbox_constraints" — 不加 Claude-Code 风格的过度护栏，但保留 git revert 等基本回退

## 附录 — 文件清单（实施会新增/修改的）

新增文件（9 + 配套 test）：
```
tauri-app/src/pet-anim/perlinNoise.ts            + __tests__/perlinNoise.test.ts
tauri-app/src/pet-anim/blinkScheduler.ts         + __tests__/blinkScheduler.test.ts
tauri-app/src/pet-anim/saccadeScheduler.ts       + __tests__/saccadeScheduler.test.ts
tauri-app/src/pet-anim/gazeTracking.ts           + __tests__/gazeTracking.test.ts
tauri-app/src/pet-anim/motionPicker.ts           + __tests__/motionPicker.test.ts
tauri-app/src/pet-anim/motionScheduler.ts        + __tests__/motionScheduler.test.ts
tauri-app/src/pet-anim/pointerReaction.ts        + __tests__/pointerReaction.test.ts
tauri-app/src/pet-anim/metricsRing.ts            + __tests__/metricsRing.test.ts
tauri-app/src/pet-anim/featureFlags.ts           + __tests__/featureFlags.test.ts
tauri-app/src/pet-anim/index.ts (AnimationOverlay) + __tests__/overlay.test.ts
tauri-app/src/pet-anim/__tests__/_helpers.ts
tauri-app/src/pet-anim/__tests__/_stubModel.ts
tauri-app/src/pet-anim/__tests__/_windowEvents.ts
tauri-app/src/pet-anim/__tests__/_setup.ts
tauri-app/src/pet-anim/__tests__/e2e_wire.test.ts
tauri-app/src/pet-anim/_probe_constants.ts       (从 Day-0 Probe-2 生成)
```

修改文件：
```
tauri-app/src/components/Live2DCanvas.tsx
  - render loop 用 overlay.applyTo(coreModel, timestamp) 替换现有参数代码
  - 新增 <div data-pet-hitzone> + pointer events
  - 新增 window pointermove → overlay.setGazeTarget
  - 接入 ResizeObserver + model load 完成时 overlay.setFaceCenter
  - toBlob 回调内 overlay.recordVisualFrameTs(timestamp)
  - HMR safety: import.meta.hot?.dispose

tauri-app/src/pet-state/PetStateMachine.ts
  - PetMotionConfig 加 motion_tag_pool?: Array<MotionTag>
  - STATE_CONFIG.working/worried/alert/intervening 填 motion_tag_pool

tauri-app/src/App.tsx
  - PetStateMachine.tick 后 if state_changed → setMotionTagPool(tags, {force_switch_now:true}, now_t)

tauri-app/package.json
  - 加 test:anim / test:anim:cov / test:e2e-wire scripts
  - 加 pixi-live2d-display 版本 lock（overrides / resolutions）

tauri-app/vitest.config.ts
  - environment: jsdom, setupFiles, coverage thresholds（详见 TDD §5.4）
```

## 备注

本指令的核心目标不是"快"，而是"按 spec 准确落地 + 全程可验证 + 失败可回退"。如果 Sprint 期间发现某个 FR 实际比预期复杂，**应当先暂停回报用户**，由用户决定是切 Plan B（PRD §8）还是延期，**不允许擅自降级验收标准**。

────────────────────────────────────────────────────────────

**用户决策点（待回答）**：
1. 何时触发本指令？现在 / 明天 / 下周？
2. 用方式 A、B、C 哪种？
3. 是否需要在新会话开启 Opus 4.7 + 长上下文模式（推荐）？
4. 是否允许 codex 参与（PRD/TDD 中描述的工程量约 1500-2000 行新代码，codex 适合接 perlinNoise / blinkScheduler / saccadeScheduler 三个纯算法模块；其他 motion 调度 / pointer transition / overlay 拼装建议 Claude 主驱）？
