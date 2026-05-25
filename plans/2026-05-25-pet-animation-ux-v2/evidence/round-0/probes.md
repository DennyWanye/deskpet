# Day-0 8 探针报告 — Pet Animation UX v2

| 项 | 值 |
|---|---|
| 日期 | 2026-05-26 |
| 执行者 | Claude (主 agent / Sprint 1 D0) |
| 模式 | Offline static analysis + reuse v1 round-3 runtime evidence |
| 输出 | 8 探针结论 + graceful degrade 路径明示 |
| 关联 | TDD §0 / PRD §8 / GOAL.md Sprint 1 D0 |

---

## 总览

| Probe | 主路径状态 | 决策 | Sprint 归属 |
|---|---|---|---|
| **D0-01** A1 Tauri startDragging | PASS（v1 c2b9586+0ed4597 已 ship）| 复用 v1 | — |
| **D0-02** B3 后端 viseme | **FAIL** → 走 fallback | 前端 phonemeEstimator 主驱（v2 内必做）+ S2 后端 lane 补主路径 | S2 前端 + S2 后端 |
| **D0-03** B3 前端 phoneme 估计器 viable | DESIGN-VIABLE（算法 PRD §3 B3-fallback）| S2 内实现 + 朋友盲听 ≥70% | S2 前端 |
| **D0-04** D1 Hiyori 参数实测（含 ParamBreath/HairFront）| PASS（8/8 关键 param 静态确认）| 后续 runtime 渲染期再视觉复测 | S2 前端 |
| **D0-05** D1 后端 LLM emotion 字段 | **FAIL** → 走 fallback | 前端 emotionClassifier 投票法主驱 + S2 后端 lane 补主路径 | S2 前端 + S2 后端 |
| **D0-06** D2 memory milestone schema | **FAIL** | S2 后端 lane 必做 milestone.py + schema migration | S2 后端 |
| **D0-07** E2 Win32 enumerate_top_windows | **FAIL** | S3 后端 lane 必做 Rust command | S3 后端 |
| **D0-08** F1 通用 audio session 枚举 | **FAIL** | S3 后端 lane 必做 Rust command | S3 后端 |

**纪律确认**：FAIL = 主路径未实现 → 按 v2 PRD §8 + GOAL.md scope discipline **不砍 FR**，每 FAIL 都已在 v2 spec 中预定义 fallback / S2-S3 lane 补完路径。**13 FR 全部留在 v2 scope**。

---

## D0-01 — A1 Tauri startDragging + onPointerMove 共存

**目标**：验证 Tauri `getCurrentWindow().startDragging()` 与 React `onPointerMove` 共存 + 不破 v1 click。

**证据**：v1 round-3 FIX-R3（commit `0ed4597`）已落地：
- `tauri-app/src/components/Live2DCanvas.tsx`: hit-zone z-index 25→5，新增 `dragStartRef` + `onPointerDown` 记录起点 + `onPointerMove > 5px` 调 `startDragging()` + `onPointerUp` 无移动 → 触发 `onClick`
- v1 round-3 真机手测 27/27 PASS（含 CASE-A1 等价测试）

**结论**：**PASS**（不需重测；v2 在此基础上扩 heldStateMachine 并行写 wobble + spring back）

---

## D0-02 — B3 后端 viseme 能力

**目标**：检查 backend TTS provider 是否输出 `tts_viseme` ws 消息。

**方法**：
```
grep -ri "viseme\|phoneme" backend/
```

**结果**：**0 hits**。backend 现有 TTS pipeline（`backend/pipeline/voice_pipeline.py`）只输出音频 chunk，无 viseme 流。

**决策**：**FAIL → fallback 路径主驱**
- v2 内必做：前端 `pet-anim/phonemeEstimator.ts`（基于 transcript + 估计时长均分 phoneme → viseme stream）
- S2 后端 lane 并行：`backend/tts/viseme_provider.py`（3d）补主路径；ship 时两条路径都可用，`deskpet_anim_viseme='auto'` 通过 `client_hello/server_hello` 协议自动选

**Spec 对照**：PRD §3 B3 双路径都做（BLOCKER B-1/B-2 解，v3 强制条款）。

---

## D0-03 — B3 前端 phoneme 估计器 viable

**目标**：验证 phonemeEstimator 算法（char → pinyin minimal table → viseme map）可达到朋友盲听 ≥70% 准确度。

**方法**：算法已在 PRD §3 B3-fallback + TDD §2.4-b 明示：
1. 中文按字 + 标点切分
2. 每字 200ms（中文平均 6 字/秒）
3. 字 → 拼音（内置 minimal table + 兜底 silent；不引入大字典依赖）
4. 拼音 → viseme（B3 表 24 行映射）

**结论**：**DESIGN-VIABLE**
- 算法清晰且实施成本可控（≤300 行 TS + 内置 minimal pinyin table 约 500 常用字 ≈ 5KB）
- 真实准确度需 S2 实施后用 CASE-D0-03 + CASE-B3-07 朋友盲听验证
- 风险：minimal pinyin table 覆盖率不足 → fallback `silent` 已防御

**S2 任务**：实现 + 朋友盲听验证 ≥70%

---

## D0-04 — D1 Hiyori 表情参数实测（含 ParamBreath / HairFront）

**目标**：确认 Hiyori cdi3.json 含 D1/B1/C1 所需 13 个核心 param（v2 round-2 F-1 强化：含 ParamBreath + ParamBodyAngleY）。

**方法**：
```
grep -E "ParamBreath|ParamHairFront|ParamCheek|ParamEyeLSmile|ParamEyeRSmile|ParamMouthForm|ParamBodyAngleY|ParamBrowLAngle" \
  tauri-app/public/assets/live2d/hiyori/Hiyori.cdi3.json
```

**结果**：
| Param | 行号 | 用途 | 状态 |
|---|---|---|---|
| ParamCheek | 20 | D1 happy/sad | ✅ |
| ParamEyeLSmile | 30 | D1 happy | ✅ |
| ParamEyeRSmile | 40 | D1 happy | ✅ |
| ParamBrowLAngle | 75 | D1 sad/angry | ✅ |
| ParamMouthForm | 95 | D1 + B3 viseme | ✅ |
| ParamBodyAngleY | 110 | D1 sad + C1 fallback | ✅ |
| ParamBreath | 120 | C1 low-energy ×1.5 | ✅ |
| ParamHairFront | 185 | B1 user input 摇 | ✅ |

加上 v1 已验证 ParamMouthOpenY / EyeLOpen / EyeROpen / AngleX/Y/Z / EyeBallX/Y / BrowLY/RY = **13/13 PASS**。

**结论**：**PASS**（静态确认；runtime 渲染期还会在 S2 内做视觉复检覆盖每参数实际响应）

**纪律**：任一参数若 runtime 实测 FAIL → 按 TDD §0 已写明 fallback（ParamHairFront FAIL → ParamBustY；ParamBreath FAIL → ParamBodyAngleY 微缓周期摆动 ×1.5），**不允许 silent skip**。

---

## D0-05 — D1 后端 LLM emotion 字段

**目标**：验证 chinzy LLM 在 `chat_v2_final` 输出 optional `emotion` 字段。

**方法**：
```
grep -ri "emotion" backend/providers/ backend/llm/
grep -ri "chat_v2_final" backend/
```

**结果**：backend 17 文件含 "emotion" 字串 → **全部是 sentiment classifier for memory / assembler policy 用途，与 chat_v2_final 输出无关**。`backend/main.py` 含 `chat_v2_final` 但无 `emotion` 输出字段；`backend/providers/openai_compatible.py` 也无 emotion 注入。

**决策**：**FAIL → fallback 路径主驱**
- v2 内必做：前端 `pet-anim/emotionClassifier.ts` 投票法（HAPPY/SAD/ANGRY/SURPRISED 关键词分组打票 → 取分高者）
- S2 后端 lane 并行：`backend/llm/emotion_prompt.py`（1d）改 chinzy system prompt + chat_v2_final wrapper（旧 backend 不发即 null → 前端走 fallback）

**Spec 对照**：PRD §3 D1 双路径都做（BLOCKER B-4 解）。

---

## D0-06 — D2 memory milestone schema

**目标**：检查 memory 表是否含 `milestone_achieved` 字段 + 5 条 milestone 规则实现。

**方法**：
```
grep -ri "milestone" backend/
ls backend/deskpet/memory/migrations/
```

**结果**：**0 hits "milestone"**。现有 memory migrations 仅含 P4 初始 schema + P4S20 summarize + P4S24 reasoning。

**决策**：**FAIL → S2 后端 lane 必做**
- 新建 `backend/memory/milestone.py`（2d）实现 5 条规则：streak_7d / streak_30d / msgs_1000 / first_custom_prompt / first_pet_naming
- memory schema migration 加 `milestone_achieved` 字段
- ws push `pet_milestone { kind, message, achieved_at }`

**Spec 对照**：PRD §3 D2 / TDD §4.15-b 5 P0 cases。**前端组件 PetCelebrationBubble + milestone client 在 S3 实现**（不阻塞，可 mock 后端推送测试）。

---

## D0-07 — E2 Win32 enumerate_top_windows

**目标**：检查 Tauri Rust 是否实现 `enumerate_top_windows()` command（Win32 EnumWindows + GetWindowRect + IsWindowVisible 包装）。

**方法**：
```
grep -r "enumerate_top_windows\|EnumWindows" tauri-app/src-tauri/
```

**结果**：**0 hits**。现有 src-tauri 17 .rs 文件无窗口枚举相关代码。

**决策**：**FAIL → S3 后端 lane 必做**
- 新建 Rust command `enumerate_top_windows() -> Vec<TopWindowInfo>`（bbox + title + visibility，**不读窗口内容**符合 NFR-8 隐私）
- 性能预算：≤ 30ms / call（M-18 解）；超过 → graceful degrade 到 0.2Hz 轮询

**Spec 对照**：PRD §3 E2 / TDD §2.11 / NFR-8。

---

## D0-08 — F1 通用 audio session 枚举

**目标**：检查 Tauri Rust 是否实现 `is_any_audio_capture_active()` 通用版（不 hardcode 进程名 — M-20/M-21 解）。

**方法**：
```
grep -r "is_any_audio_capture_active\|IAudioSessionManager" tauri-app/src-tauri/
```

**结果**：**0 hits**。

**决策**：**FAIL → S3 后端 lane 必做**
- 新建 Rust command `is_any_audio_capture_active() -> bool`（用 Win32 Core Audio API `IAudioSessionManager2` 枚举所有进程 audio session，过滤 active capture）
- 覆盖目标：Teams / Zoom / Discord / Slack / Wechat / Lark 6 个 app 各测一次 PASS（CASE-F1-03）

**Spec 对照**：PRD §3 F1 / TDD §2.12。

---

## graceful degrade 总策略（与 PRD §8 / GOAL.md 一致）

| FAIL 探针 | 主路径补完 lane | Fallback / 当前 ship 行为 |
|---|---|---|
| D0-02 B3 后端 | S2 backend (3d) | 前端 phonemeEstimator 主驱 |
| D0-05 D1 后端 | S2 backend (1d) | 前端 emotionClassifier 投票法主驱 |
| D0-06 D2 后端 | S2 backend (2d) | 前端 mock 推送先打通客户端，S2 后端完成后无缝接 |
| D0-07 E2 Rust | S3 Rust (1d) | E2 主路径直接走 — Rust command 不实现则 FR fail；**S3 必做不延** |
| D0-08 F1 Rust | S3 Rust (1d) | F1 fullscreen + KPM 两条 trigger 可独立工作；call detection 在 Rust command 落地后启用 |

**13 FR 全部留在 v2 scope，零砍 FR**。所有 FAIL 都对应已规划的 sprint 任务，符合 GOAL.md "严禁 scope drift" 纪律。

---

## 下一步（S1-D1 红绿循环开始）

1. F-2 多 FR 优先级矩阵补完（PRD §6.2 严格按 ADD/MUL/SET + 10x10 矩阵实施）
2. A1 `pet-anim/heldStateMachine.ts` TDD 红绿（TDD §2.1 接口 + §4.1 6 cases）
3. B1 `pet-anim/userInputObserver.ts` TDD 红绿（TDD §2.2 + §4.2 6 cases）
4. Live2DCanvas v2 hit-zone drag → setDragState wiring（与 v1 pulseInteraction 正交）

D0 探针报告归档完毕，进入 S1-D1。
