# PRD — Pet Animation UX v2（"灵魂感"完整版）

| 项 | 值 |
|---|---|
| 作者 | 用户（PO） + Claude（架构师代笔） |
| 日期 | 2026-05-25 |
| 版本 | **v2**（应用 round-1 架构师评审 13 BLOCKER + 关键 MAJOR） |
| 状态 | Draft → 待 round-2 评审 |
| 关联 | `plans/2026-05-24-pet-animation-ux/` (v1, ship c2b9586+0ed4597) |
| 评审反馈 | `evidence/round-0/architect-review-r1.md` (NEEDS-MAJOR-REWORK, 5.2/10) |
| 预计 Sprint | **3 sprint**（前端+后端并行 lane）|

---

## 0. Scope 纪律（与 v1 决裂条款）

v1 PRD §2.2 用 "Out of Scope" 自作主张砍 13 项 — 此 PRD **严禁**任何形式的 scope drift。

**v2 round-1 评审揪出的"换皮 scope 砍"措辞 — 全部禁止**：
- ❌ "v2.1 备选" / "v2.1 推迟" / "推迟到 v3"
- ❌ "推荐先做选项 C 兜底" / "选项 A 在后端 ready 时启用"（被动语言）
- ❌ "Plan B 砍 [13 项任一]"
- ❌ "如 calibrated" / "如该 tag 已校准"（条件 deferred）
- ❌ "flag 默认 off"（除非用户隐私 + consent 强制要求且不影响默认体验）
- ❌ §2.x Non-Goals 里出现业界 16 项任一

**允许的语言**：
- ✅ "graceful degrade"（性能不够 → 降级实现路径，不砍 FR）
- ✅ "fallback 路径"（主路径失败 → 自动跑次路径，仍属 v2 scope）
- ✅ Plan B 调整"轮询频率 / 复杂度"（但不砍 FR）

任何想砍的项必须**先停下来回报用户**，由用户决定。

---

## 1. 背景与问题陈述

### 1.1 v1 已交付
v1 完成"底层活感 + 视线 + 鼠标事件 + 标签消费"3 项核心：Perlin/blink/saccade、gaze（hit-zone 内）、单/双击、motion 标签消费、双指标 latency。

### 1.2 v1 未覆盖的 13 项（本 PRD 全部纳入 scope）

| # | 业界项 | v1 状态 | v2 处理 |
|---|---|---|---|
| #4 | 拖拽 → being_held + physics3 | ❌ | **A1** (§3) |
| #5 | 用户输入中 → 微歪头 + 听音 | ❌ | **B1** (§3) |
| #6 | LLM 思考中 → 看天/思考 | ⚠️ 仅 head_tilt:+2° | **B2** (§3) — 接 first-chunk 退出 |
| #7 | TTS viseme lipsync | ⚠️ 仅音量包络 | **B3** (§3) — 双路径（后端原生 + 前端 phoneme 估计 fallback，**两条都做**）|
| #8 | 说完静默 fade | ❌ | **B4** (§3) — 含 800ms 兜底 |
| #9 | >5min idle → low-energy / yawn | ❌ | **C1** (§3) — motion calibration sub-task S2 内做 |
| #10 | 用户回归 → "想你了" | ❌ | **C2** (§3) — 含 escalation 表 |
| #11 | 整点/纪念日 | ❌ | **C3** (§3) — DND 抑制 hourly |
| #12 | 情感分类 → 表情 TTS 锁定 | ❌ | **D1** (§3) — 后端 LLM 输出 emotion + 前端正则**两条都做** |
| #13 | memory milestone | ❌ | **D2** (§3) — 后端 milestone.py 在 S2 并行 |
| #14 | 屏幕边缘 → 攀爬/挂边 | ❌ | **E1** (§3) — 旋转替代攀爬 (Hiyori 2D) |
| #15 | 窗口遮挡 → 主动挪开 | ❌ | **E2** (§3) — **默认 on + consent**, 不砍 |
| #16 | 全屏/打字/通话 → DND | ❌ | **F1** (§3) — 通用 audio session, 不 hardcode |

### 1.3 v1 已建好的基础设施（v2 复用）
- `pet-anim/index.ts` AnimationOverlay 6-step applyTo + FIFO 配对 + dispose
- `pet-anim/*` 9 模块（perlin/blink/saccade/gaze/motionPicker/motionScheduler/pointerReaction/metricsRing/featureFlags）
- `_probe_constants.ts` Hiyori 参数基线
- Live2DCanvas hit-zone 自适应 + window pointermove + ResizeObserver + HMR
- PetStateMachine 5 状态 + STATE_CONFIG + motion_tag_pool
- 386 单测 + vitest jsdom + coverage gate（line ≥80/branch ≥70）
- HiyoriMotionTuner（motion calibration UI，v1 已建可复用）
- cdp-runner.mjs CDP 真机测试驱动
- Win32 mouse_event PowerShell wrapper（round-3 真 OS 测试工具）

---

## 2. 目标 / 非目标

### 2.1 v2 目标（In Scope — 全部 13 项）
- **G1**：用户体感"灵魂"提升 — 不再是 v1 的"会眼神追随的 GIF"
- **G2**：16 项业界对照（含 v1 3 项）通过 ManualTest + 1+1 BLIND 选 v2 占优
- **G3**：v1 386 单测 + 27/27 OS 手测**零回归**（含 AC-3 4 条 snapshot test）
- **G4**：每个 FR 独立 flag + graceful degrade（不砍 FR）
- **G5**：跨层契约清晰（client_hello 版本握手 + ≤6 新 ws 消息 + 兼容旧 client/backend）

### 2.2 Non-Goals（v2 不做 — 与业界 16 项**无关**）

> ⚠️ 严禁列业界 16 项任一。下列纯粹是 v2 边界。

- ❌ 更换 Live2D 模型（继续用 Hiyori；**不补** expression3.json 资产 — 用参数组合替代 emotion）
- ❌ 移动端 / Web 端适配（仍仅 Tauri Windows）
- ❌ 多语言 viseme（仅中文 phoneme→viseme；英文 TTS 走 fallback）
- ❌ 新 LLM provider 集成（沿用 the relay）
- ❌ Sticker / 自定义表情包
- ❌ 后端持久化 anniversary（v2 用 localStorage；后端持久化是**配套优化**非 FR）
- ❌ i18n（bubble 文案 hardcode 中文）

### 2.3 用户故事（11 条，与 13 FR 对应）

US-1 拖动桌宠应有"被抓"反馈；US-2 输入框敲字 → 歪头；US-3 LLM 思考期 → 思考姿；US-4 TTS 嘴形对得上；US-5 TTS 说完嘴渐合；US-6 离开 30min → 睡，回来 → 想你了；US-7 生日 / 整点 → 庆祝；US-8 LLM 说"抱歉"不能笑；US-9 7 天 milestone → 庆祝；US-10 拖到边 → 挂边；US-11 全屏 / 通话 → DND。

---

## 3. 功能需求（FR）

下面每 FR 按统一格式：**触发 / 行为 / 关键参数 / 写入语义 / 验收 / 降级 / 跨层契约**。

---

### Layer A — 鼠标拖拽

#### A1：拖拽 → being_held 子状态 + 纯参数 wobble

**【BLOCKER B-10 解：明示 physics3 决策】**
v2 **不接 Hiyori physics3.json owner 切换** —— 因 (a) 工程复杂度高，(b) Hiyori physics3 已驱动头发/胸/裙子等被动物理，v2 不需要再夺主；(c) wobble 视觉效果用纯参数 sin 模拟可获得 80% 业界对照效果。决策权衡详见 evidence/round-N/A1-physics3-decision.md（v2 阶段实施时写）。

| 维度 | 内容 |
|---|---|
| **触发** | hit-zone pointerdown + 移动 > 5px → 进入 being_held |
| **行为** | (a) 调 `getCurrentWindow().startDragging()` 移窗；(b) 桌宠进 being_held：body wobble + face surprise；(c) pointerup → spring back 250ms |
| **关键参数** | `wobble_amplitude_deg=8`；`wobble_period_ms=300`；`spring_back_ms=250`；`wobble_decay_const_ms`（Day-0 视觉调参，候选 4000/6000/8000）；surprise: ParamMouthForm=-0.5, ParamEyeLOpen/ROpen=1.3, ParamBrowLY/RY=+0.5 |
| **写入语义** | (1) being_held 期间 **block** Perlin/saccade/gaze（applyTo step 2/3/4 早 return）；(2) Wobble = `wobble_amplitude_deg × sin(2π × (now_t - held_start_t) / wobble_period_ms) × exp(-(now_t - held_start_t) / wobble_decay_const_ms)` ADD 到 ParamBodyAngleZ；(3) surprise 参数 SET；(4) Spring back = ease-out 立方 lerp 从当前 wobble → 0 over spring_back_ms |
| **验收** | (a) drag 200ms 内桌宠 wobble 可见；(b) drag 期间手测视频可辨"被抓"姿；(c) pointerup 后 250ms 内回 idle；(d) 单测 reactor `being_held` 转换；(e) **drag 不破 v1 click**（pointerdown+pointerup 无移动 → 仍触发 onClick） |
| **降级** | flag `deskpet_anim_held=off` → 仅移窗；Tauri startDragging API 失败 → 不移窗但仍 wobble；wobble 与其他层参数冲突 → 见 §6.2 优先级矩阵 |
| **跨层** | 无后端依赖 |
| **v1 兼容 (B-13 解)** | v1 `pulseInteraction('click'/'double_click'/'hover_*')` 接口**保留**且不变；A1 新增 `setHeldState` 接口与 pulseInteraction **正交**（前者管 drag 视觉态，后者管 click 反应）。v2 实现：onPointerDown → 记录起点；onPointerMove > 5px → 启 startDragging + setHeldState('being_held')；onPointerUp 无移动 → onClick 正常触发（pulseInteraction），有移动 → 进 spring_back，不触发 onClick |

**新模块** `pet-anim/heldStateMachine.ts`（接口见 TDD §2.1）

---

### Layer B — 对话/语音

#### B1：用户输入中 → 微歪头 + 听音

**【BLOCKER B-1 子项解：ParamEar/HairFront hedge 处理】**
Hiyori cdi3.json 含 ParamHairFront / ParamHairBack（揺れ组），**但没有 ParamEar**。Day-0 Probe-D1 必须实测 ParamHairFront 是否可写 → 若 PASS，B1 用 ParamHairFront 微动模拟"听音"耳朵效果；若 FAIL，B1 用 ParamBustY 微动或忽略"听音"维度（但**歪头**仍生效）。

| 维度 | 内容 |
|---|---|
| **触发** | chat-input (App.tsx 内 `<input data-testid="chat-input">`) focus + 最近 500ms 有 onChange；从最后按键起 1.5s 内有新输入 = 持续输入 |
| **行为** | (a) ParamAngleZ += +3° 歪头（v2 调强：v1 评审 +2° 太弱）；(b) ParamEyeLOpen/ROpen *= 1.15（v2 调强：v1 评审 1.05 不可辨）；(c) ParamHairFront / ParamBustY 微动（基于 Day-0 探针选定）|
| **关键参数** | `tilt_deg=+3`；`eye_boost_mul=1.15`；`stop_after_idle_ms=1500`；`hair_oscillation_amp=0.2`；`hair_period_ms=600`；IME 中文输入合成事件兼容（compositionstart/end 不计活跃）|
| **写入语义** | step 6 ParamAngleZ += tilt_deg（叠加 base + held_wobble，详见 §6.2 优先级矩阵）；step 5 MULTIPLY ParamEyeLOpen/ROpen *= eye_boost_mul（在 blink 之前）；ADD ParamHairFront = hair_oscillation_amp × sin(2π × now_t / hair_period_ms) |
| **验收** | (a) 输入框 focused + 敲字 → 300ms 内歪头；(b) 停手 1500ms 后恢复；(c) blur 立即恢复；(d) IME 输入中（拼音合成期）不误触发；(e) `window.__deskpet_anim_debug.user_input_age_ms` 客观查询；(f) 录屏脸部 240fps 可辨 eye boost |
| **降级** | flag off → 不读 input；无 chat-input 元素 → silent skip；ParamHairFront FAIL → 仅 tilt + eye_boost |
| **跨层** | 无后端依赖 |

新模块 `pet-anim/userInputObserver.ts`（接口见 TDD §2.2）

App.tsx 改：chat-input 加 onFocus/onBlur/onChange/onCompositionStart/onCompositionEnd 回调进 AnimationOverlay。

---

#### B2：LLM 思考中 → 看天/思考表情

**【MAJOR M-1 解：first-chunk 退出】**
原 v2 v1 草稿说 chat_v2_final 退出 — 评审指出应 first chunk 退出（流式 UX）。

| 维度 | 内容 |
|---|---|
| **触发** | App.tsx 调 `sendChatV2()` 时 → 进入 thinking；收到**第一个** chat_v2 stream chunk（任一：tool_use_event / chat_v2_final.partial / text token）→ 退出 thinking |
| **行为** | (a) ParamEyeBallY = +0.6 (看上)；(b) ParamAngleX = +5° (头略仰)；(c) blink_hz = 0.15；(d) **saccade 仍跑**（不 block 眼眶 micro-movements 避免"僵眼"）；(e) ParamBrowLY/RY = +0.3 微挤眉（与 emotion 冲突时见 §6.2 优先级）|
| **关键参数** | `eyeball_up_norm=0.6`；`head_up_deg=5`；`blink_hz_thinking=0.15`；`max_thinking_duration_ms=90000`（v2 调大：v1 评审 30s 太短，the relay 偶尔卡 60s+）|
| **写入语义** | ADD ParamEyeBallY += eyeball_up_norm（叠加 saccade）；ADD ParamAngleX += head_up_deg；setBlinkHz override；SET ParamBrowLY/RY |
| **验收** | (a) 用户发消息 → 桌宠 300ms 内"思考"；(b) 收到 first chunk → 1s 内回 idle；(c) 90s 超时强制回 idle；(d) saccade 仍可见（不僵）|
| **降级** | flag off → 沿用 v1 working 态行为；无 chat_v2 信号 → 不进入 |
| **跨层** | 复用 `chat_v2_event` / `tool_use_event` / `chat_v2_final` 现有消息流（v1 已用） |

新模块 `pet-anim/thinkingObserver.ts`（接口见 TDD §2.3）

---

#### B3：TTS viseme lipsync — **双路径都做**

**【BLOCKER B-1/B-2 解：删 "v2.1 备选"；前端 phoneme 估计 fallback 必须在 v2 内做】**

**v2 实施策略**：
1. **主路径**：后端 TTS provider 输出 `tts_viseme { v, t_ms }` ws 消息流（与 audio chunk 并行或夹带）
2. **Fallback 路径（v2 内必做）**：若后端不支持 viseme，前端用 `phonemeEstimator` 基于 transcript + 估计时长均分 phoneme 估算

两条路径在 v2 ship 时**都必须可用** — flag `deskpet_anim_viseme='auto'` 时根据 Day-0 Probe-B3 自动选择。

| 维度 | 内容 |
|---|---|
| **触发** | TTS chunk 播放 → 主路径：viseme 流到达；fallback：transcript 到达 → 本地估算 viseme stream |
| **行为** | viseme → (mouthY, mouthForm) 映射（详见 §3 B3-fallback 完整 phoneme 表）；60ms blend 平滑切换 |
| **关键参数** | `viseme_blend_ms=60`（OQ-B3-2 改 ManualTest CASE-B3-blend-ab 调参）；6 viseme 主集 + 双元音 / 鼻音补全映射（见下） |
| **写入语义** | SET ParamMouthOpenY = blended.y；SET ParamMouthForm = blended.form（与 D1 emotion 冲突时 viseme 优先 — 见 §6.2）|
| **验收** | (a) "妈妈骑马" 嘴形可辨；(b) viseme 帧间无抖（blend 工作）；(c) **后端 viseme PASS 或 fallback PASS 二选一即满足**；(d) 朋友盲听判定 fallback 嘴形够用 |
| **降级** | 后端 viseme 流断 → 自动切 fallback；fallback 失败（如 transcript 不可用）→ 退 v1 音量包络（仅 flag deskpet_anim_viseme=off 时）|
| **跨层** | **后端协议（必）**：S2 后端 sprint 内做 — TTS provider 输出 viseme/phoneme 时间戳；**前端协议（必）**：S2 前端实现 phonemeEstimator |

**v2 phoneme→viseme 完整映射表（解 MAJOR M-2）**：

| 中文 phoneme | viseme | 备注 |
|---|---|---|
| a, ɑ | A | 单元音 |
| o | O | |
| e, ɤ | E | |
| i | I | |
| u | U | |
| ü, y | U | u 兼用 |
| ai | A→I (60ms blend) | 复合 |
| ei | E→I | |
| ao | A→O | |
| ou | O→U | |
| an, ang | A → silent (鼻音收) | 韵尾闭嘴 |
| en, eng, in, ing | E/I → silent | |
| un, ün | U → silent | |
| er | E | r 不计 |
| 辅音 (b/p/m/f/d/t/n/l/...) | silent or 极小 mouthY (0.1) | 60ms 内 |
| 停顿 / 标点 | silent | mouthY=0 |

**B3-fallback 子章节（解 BLOCKER B-2）**：前端 phoneme 估计器（`pet-anim/phonemeEstimator.ts`）输入 (transcript, total_duration_ms) 输出 `VisemeFrame[]`。

- 算法：(1) 用简化中文分词（按字 + 标点切分）；(2) 每字假设 200ms（中文平均语速 6 字/秒，可调）；(3) 字 → 拼音（用 minimal 内置 char→pinyin 表 + 兜底 silent，**不引入大字典依赖**）；(4) 拼音 → viseme 映射表（同上）
- 准确度目标：朋友盲听判定 "嘴形对得上字" → ≥ 70%（CASE-B3-07）
- 接口在 TDD §2.4-b

---

#### B4：说完静默 → 150-300ms fade + 800ms 兜底

**【MAJOR M-4 解：800ms 兜底】**

| 维度 | 内容 |
|---|---|
| **触发** | (a) 主：收到 `tts_end` 消息；(b) 兜底：TTS chunk 流停止 800ms 仍未收到 tts_end → 自动触发 fade（防后端漏发）|
| **行为** | mouthOpenY 从 current → 0 做 200ms ease-out 立方 lerp |
| **关键参数** | `fade_ms=200`（150-300 区间中位）；`silence_timeout_ms=800` |
| **写入语义** | AnimationOverlay 内 fadeMouthToZero(duration_ms, now_t)；每帧 applyTo step 1：if (fade_active) ParamMouthOpenY = lerp_ease_out(start_mouth, 0, t)；fade 仅作用于 mouthY，**emotion 的 mouthForm 在 fade 期间保持**（mouthForm 由 step 7 写）|
| **验收** | (a) tts_end 后录屏可见嘴渐合；(b) fade 期 viseme 新流到达 → 立即取消 fade 接 viseme；(c) 后端漏发 tts_end → 800ms 后自动 fade |
| **降级** | flag off → 沿用 v1 瞬切 |
| **跨层** | 无后端依赖 |

新模块 `pet-anim/mouthFader.ts`（接口见 TDD §2.5）

---

### Layer C — 时间/系统状态

#### C1：>5min idle → low-energy / yawn

**【BLOCKER B-3 解：删 "如 calibrated" + S2 motion calibration sub-task】**
**【MAJOR M-5 解：补 visibility/blur events】**
**【MAJOR M-6 解：PetStateMachine transition 图重画】**

| 维度 | 内容 |
|---|---|
| **触发** | 全局 input idle（事件集见下）≥ `low_energy_threshold_ms`（默认 5min） |
| **events 集** | `['keydown','mousemove','wheel','pointermove','visibilitychange','focus','blur']`（v2 加 visibility/focus/blur）|
| **行为** | (a) PetStateMachine 切 `low_energy`；(b) blink_hz=0.1；(c) 每 30-60s 触发 `yawn` motion（**S2 内 motion calibration sub-task 校准 yawn 到 m0X**，不接受 "如 calibrated" 跳过）；(d) ParamBreath 周期 ×1.5（Day-0 Probe-D1 必测 ParamBreath；若 FAIL 用 ParamBodyAngleY 微缓周期摆动替代，**不允许 silent skip**）|
| **关键参数** | `low_energy_threshold_ms=300000`（用户可改 SettingsPanel）；`yawn_interval_min/max_ms=30000/60000`；`blink_hz_low=0.1`；`breath_multiplier=1.5` |
| **写入语义** | setBlinkHz(0.1)；setMotionTagPool(['low-energy','slow','yawn'])；新增 setBreathRate(1.5) |
| **验收** | (a) 全部 7 类 events 任一停 5min → 1s 内 low_energy；(b) yawn motion 30-60s 周期触发（motion calibration 完成后）；(c) 任意 events 任一 → 立即退出 → C2 接管 |
| **降级** | flag off → 不监测 idle；motion calibration 未跑 → S2 内必跑（v2 实施纪律） |
| **跨层** | 无后端依赖 |

**PetStateMachine v2 transition 图**（解 MAJOR M-6）：

```
                    ┌─────────────┐
                    │   idle      │
                    └──┬────────┬─┘
              session 启 │        │ no events 5min
                         ▼        ▼
                    ┌─────────┐ ┌───────────┐
                    │ working │ │ low_energy│
                    └─┬─────┬─┘ └──┬────────┘
       severity yellow│     │ idle  │ event 任一
                      ▼     ▼      ▼
                  ┌───────┐  ┌─────────┐
                  │worried│  │ welcome │  (临时态 1.5s → idle)
                  └─┬─────┘  └─────────┘
       severity red │
                    ▼
                  ┌───────┐
                  │ alert │
                  └─┬─────┘
       nudge fired │
                   ▼
              ┌────────────┐
              │intervening │ (3s 后回 alert)
              └────────────┘
```

新增态：`low_energy`, `welcome`(临时态 1.5s)。

新模块 `pet-anim/idleWatcher.ts`（C1/C2 共用，接口见 TDD §2.6）

---

#### C2：用户回归 → "想你了" + escalation

**【MINOR m-1 解：Hiyori 2D 替代说明】**
**【MAJOR M-7 解：long-absence escalation】**

> 说明：Hiyori 是 2D 静态模型，无法"扑过来"。v2 用 TapBody motion + happy 表情参数组合 + blink burst 模拟"想你了"。

| 维度 | 内容 |
|---|---|
| **触发** | from `low_energy` → 检测到任一 input event |
| **行为 (escalation 表)** | low_energy 持续时长决定 welcome 强度：<br>**5-15min**: normal welcome — TapBody + happy 表情 + blink_hz=0.8 持续 1500ms<br>**15min-1h**: bubble + 上述 — bubble "好久不见~" 显 3s<br>**>1h**: intense — TapBody × 2（间隔 800ms）+ bubble "想你了，欢迎回来~" + happy 持续 3s |
| **关键参数** | `welcome_normal_ms=1500`；`welcome_bubble_threshold_ms=900000`（15min）；`welcome_intense_threshold_ms=3600000`（1h）；`welcome_cooldown_ms=60000`（1min cooldown 防短暂离开重触发）|
| **写入语义** | 复用 D1 happy 参数组（_intense_multiplier=1.3 for >1h 场景）；motion player call('TapBody', 0)；setBlinkHz(0.8) 临时；temporary bubble 通过 PetCelebrationBubble 组件 |
| **验收** | (a) low_energy 5-15min 后 input → 1s 内 TapBody + happy 1.5s；(b) 15min-1h → 同上 + bubble；(c) >1h → 双 TapBody + 强化 bubble；(d) welcome 触发后 cooldown 内不重触发 |
| **降级** | flag off → 仅退出 low_energy 无 welcome；bubble 失败 → 仅参数效果 |
| **跨层** | 无后端依赖 |

---

#### C3：整点/纪念日 → 限定动画

**【MAJOR M-8 解：SettingsPanel UI vs JSON 明示】**
**【MAJOR M-9 解：dnd_active 抑制 hourly】**
**【MINOR m-2 解：抑制说明】**

| 维度 | 内容 |
|---|---|
| **触发** | (a) 整点：每分钟轮询当前分钟数 == 0；(b) 纪念日：每天 00:00 检查 MM-DD ∈ anniversaries |
| **行为** | (a) **触发前 check dnd_active=false** — true 时 silent skip；(b) 整点：500ms happy + 可选 bubble "X 点啦~"（quietness 模式仅闪 happy 无 bubble）；(c) 纪念日：3s 庆祝 + bubble |
| **关键参数** | `anniversaries: Anniversary[]`（v2 用 localStorage `deskpet_anniversaries` JSON 持久化；用户手编辑 JSON 即可用 — **配置管理 UI 不在本 PRD scope（亦不在 13 项 FR 内）**，13 项行为本身在 v2 完整可用）；`hourly_celebration_ms=500`；`anniversary_celebration_ms=3000`；`quiet_mode_dnd_skip=true` |
| **写入语义** | 复用 D1 happy 参数组；motion player call('TapBody', 0)；bubble via PetCelebrationBubble |
| **验收** | (a) 12:00:00 → 1s 内 happy + bubble；(b) localStorage `deskpet_anniversaries=[{date:'05-25',message:'桌宠生日'}]` + clock 注 05-25 00:00 → 3s 庆祝；(c) DND 期 → 整点 silent skip（debug.last_celebration_skip=true） |
| **降级** | flag off → 跳过；DND 抑制 hourly 但 anniversary 仍触（用户重视）；clock 注入失败 → 用真实 Date |
| **跨层** | 无后端依赖（v2 localStorage 持久化是合理实现路径；**跨设备同步的后端持久化不在本 PRD scope（亦不在 13 项 FR 内）**——本 FR 行为完整可用）|

默认 v2 内置 anniversaries：`[{date: 'INSTALL_DATE', message: '桌宠安装周年~'}]`（OQ-C3 拍板）。

新模块 `pet-anim/timeCelebration.ts`（接口见 TDD §2.7）；UI: `PetCelebrationBubble.tsx`。

---

### Layer D — 语义

#### D1：回复情感分类 → 表情 + TTS 锁定 — **双路径都做**

**【BLOCKER B-4 解：删 "选项 A 后端 ready 再做" 被动语言；选项 A + C 同时实施】**
**【MAJOR M-11 解：用户打断 TTS 时锁释放】**
**【MAJOR M-12 解：投票分类器】**
**【MAJOR M-13 解：EmotionCode 预留扩展】**

**v2 实施策略**：
1. **主路径（选项 A）**：S2 后端 sprint 改 LLM system prompt 让 LLM 在 chat_v2_final 输出 `emotion: 'happy'|'sad'|'angry'|'surprised'|'neutral'` 字段（一律 optional, 旧 backend 不发即 null）
2. **Fallback 路径（选项 C, v2 内必做）**：前端 `emotionClassifier` 关键词正则**投票法**（不是单一顺序判断）—— 句子里每出现一个 happy 关键词 +1 票，sad +1, ... 最终取分高者
3. **未来扩展**：EmotionCode 预留 `disgust` / `fear` TODO 占位（参数表 TODO 不实现，但类型已扩 — 这样未来加入不破坏 API）

| 维度 | 内容 |
|---|---|
| **触发** | (a) chat_v2_final / transcript (assistant) 到达 → 分类（优先 backend emotion 字段，缺失走前端投票）→ setEmotion(emotion, now_t) → 锁定到 TTS 结束 |
| **行为** | 7 类 emotion 映射到参数组合（5 类实现 + 2 类 TODO 占位）：<br>**happy**: MouthForm=0.8, EyeLSmile/RSmile=0.7, Cheek=0.5, BrowLY/RY=0.2<br>**sad**: MouthForm=-0.6, EyeLOpenMul/EyeROpenMul=0.7, AngleY=-3°, BrowLAngle/RAngle=-0.5<br>**angry**: BrowLAngle/RAngle=1, MouthForm=-0.8, EyeLOpenMul/EyeROpenMul=1.2<br>**surprised**: EyeLOpenMul/EyeROpenMul=1.5, MouthOpenY=0.4, BrowLY/RY=0.6<br>**neutral**: 全部 baseline<br>**disgust** (TODO): 参数待定<br>**fear** (TODO): 参数待定 |
| **关键参数** | `emotion_lock_release_on: 'tts_end' OR 'tts_interrupt' OR 'next_chat_send'`（用户打断 TTS 立即释放，避免说 sad 内容时被新 chat 卡住）|
| **写入语义** | applyTo step 7（介于 saccade 和 blink 之间）SET 表情参数；step 8 ParamMouthForm 见 §6.2 优先级（viseme > emotion） |
| **验收** | (a) "好的没问题" → happy；(b) "抱歉做不到" → sad；(c) TTS 期间 emotion 不变；(d) 用户中断 → emotion 立即释放为 neutral；(e) backend 不发 emotion → 前端投票分类 PASS；(f) 投票分类 5 类各一句视觉可辨；(g) AC-10-01 一票否决：sad 内容不能误归 happy |
| **降级** | flag off → emotion=neutral；分类器异常 → neutral；TTS 不可用 → 锁定 3s 后自释 |
| **跨层** | **后端协议（S2 必做）**：chat_v2_final / transcript 加 optional emotion 字段；LLM system prompt 教学（the relay 加 "请在每次回复结束附 emotion JSON：{emotion: ...}"）；旧 backend 不发即 null → 前端走 fallback |

新模块 `pet-anim/emotionMapper.ts` + `pet-anim/emotionClassifier.ts`（接口见 TDD §2.8/2.9）

---

#### D2：memory milestone → 庆祝

**【BLOCKER B-5 解：S2 后端 milestone.py 并行 sub-task】**
**【MAJOR M-14 解：milestone 规则写进 PRD】**

| 维度 | 内容 |
|---|---|
| **触发** | 后端 memory 模块达到 milestone → 推 `pet_milestone { kind, message, achieved_at }` ws 消息 |
| **v2 milestone 规则（PRD 正文，不在 OQ）** | <br>(1) `streak_7d`：连续 7 天每天至少 1 条 user→assistant chat（跨 0 点算 1 天，时区 = 系统时区）<br>(2) `streak_30d`：连续 30 天<br>(3) `msgs_1000`：累计 1000 条 user→assistant chat<br>(4) `first_custom_prompt`：用户首次自定义 LLM system prompt 时触发<br>(5) `first_pet_naming`：用户首次给桌宠改名（SettingsPanel）|
| **行为** | 3s 庆祝（happy_intense + 双 TapBody 间隔 800ms + ParamHairFront 大幅摆动 + bubble）|
| **关键参数** | `milestone_celebration_ms=3000`；`happy_intense_multiplier=1.3`；`bubble_auto_dismiss_ms=5000`；多 milestone 同时触发 → FIFO 排队（不并发）|
| **写入语义** | 复用 D1 happy 参数 × intense_multiplier；motion player call('TapBody', 0) × 2；bubble via PetCelebrationBubble |
| **验收** | (a) 后端推 milestone → 1s 内庆祝；(b) 同日多 milestone 排队不并发；(c) 单测：触发逻辑 + bubble lifecycle |
| **降级** | flag off → 跳过；bubble 渲染失败 → 仅参数效果 + motion |
| **跨层** | **后端 S2 sub-task（必做）**：`backend/memory/milestone.py` 模块 + memory 表加 `milestone_achieved` 字段 + ws push；后端 spec 同步进 v2 |

新模块 `pet-anim/PetCelebrationBubble.tsx`（C3/D2 共用）

---

### Layer E — 物理边缘

#### E1：拖到屏幕边缘 → 旋转挂边 (Hiyori 2D 替代攀爬)

**【MINOR m-3 解：Hiyori 2D 替代说明】**
**【MAJOR M-15 解：snap preview 决策】**
**【MINOR m-4 解：currentMonitor vs 新 Rust command】**

> 说明：Hiyori 是 2D 静态模型，无攀爬骨骼/动画。v2 用 ParamAngleZ 旋转 + snap to edge + 可选 edge motion subset 模拟"挂边"。

| 维度 | 内容 |
|---|---|
| **触发** | A1 拖窗结束（pointerup）后，pet 窗中心点距 screen 任一边 < `edge_threshold_px`（默认 100）|
| **行为** | (a) snap：窗口贴边再缩 10px 出屏感（multi-monitor 时贴主显示器边，不跨屏）；(b) pose：贴右 ParamAngleZ=+90°（侧躺）/ 贴底 +180°（倒挂）/ 贴左 -90° / 贴顶 0°（正立靠顶）；(c) **不做 snap preview**（v2 决策：hover-near-edge 不预览，直接 release-on-edge 就 snap — 简化交互）；(d) 切换 motion subset 'edge' (S2 motion calibration 完成后)；(e) blink_hz=0.15 贴边休息感 |
| **关键参数** | `edge_threshold_px=100`；`snap_offset_px=10`；pose mapping table（左/右/上/下） |
| **写入语义** | Tauri `getCurrentWindow().setPosition()`；SET ParamAngleZ；setMotionTagPool(['edge'])；setBlinkHz(0.15) |
| **验收** | (a) 拖到右屏边松手 → 窗口贴边 + pet 侧躺；(b) 重新拖离边 1s 内回 idle；(c) 4 边各测一次 pose 正确；(d) multi-monitor 主屏边正常 snap 不跨屏 |
| **降级** | flag off → 不 snap；ParamAngleZ 写入失败 → 仅 snap 位置无旋转；motion calibration edge 标签未完成 → 仅旋转无 motion；多屏配置异常 → 不 snap |
| **跨层** | 用 Tauri `currentMonitor()` API（已有，无需新 Rust command）+ `getCurrentWindow().setPosition()` |

新模块 `pet-anim/edgeWatcher.ts`（接口见 TDD §2.10）

---

#### E2：被窗口遮挡 → 主动挪开

**【BLOCKER B-6/B-7/B-8 解：默认 on + consent；删 "Plan B 砍 E2" + 删 "v2.1 推迟"；改 graceful degrade】**
**【MAJOR M-17 解：findSafeSpot 候选集扩 grid sampling】**
**【MAJOR M-18 解：性能预算明示】**

| 维度 | 内容 |
|---|---|
| **触发** | 另一窗口 foreground 且 bbox 与 pet 窗 bbox 重叠 > 50%，持续 > `occlusion_grace_ms`（5s） |
| **行为** | pet 窗移到最近 safe 区 + 播 dodge motion (S2 calibration) + ParamMouthOpenY=0.3 transient (小惊讶) |
| **关键参数** | `occlusion_threshold_ratio=0.5`；`occlusion_grace_ms=5000`；`poll_interval_ms=1000`（1Hz）；`dodge_motion_ms=400` |
| **findSafeSpot 算法 (M-17 解)** | 候选集扩到 **grid sampling 8×6 = 48 点**（不是 4 角 + 4 边中点 = 8 点）；对每个候选 spot 计算与所有 other windows 的重叠率；返回首个重叠率 < 10% 的 spot；若全部 fail → 返回 null（pet 不动 + console.warn 一次）|
| **写入语义** | Tauri setPosition；motion player call('Idle', dodge_idx)；SET ParamMouthOpenY=0.3 transient |
| **验收** | (a) 浏览器全屏盖 pet 5s → pet 跑开；(b) 5s 内重叠消失 → pet 不动；(c) 无 safe spot → pet 不动 + warn；(d) pet 不被推到屏幕外或负坐标（AC-10-02 一票否决）|
| **降级** | flag `deskpet_anim_occlusion=on` **默认 on**（评审 B-6 解）+ 首次启用 SettingsPanel 弹 consent（NFR-8）；用户拒 consent → flag 自动 off；Win32 API 失败 → console.warn + functionality 自动 disable（不报错 + flag 仍可见 on，类似断网降级）；性能预算超 → 轮询频率 1Hz → 0.2Hz（不砍 FR，详 §8 Plan B） |
| **跨层** | **新 Rust command**: `enumerate_top_windows()` returns `Vec<TopWindowInfo>`；仅读 bbox + title + visibility，**不读窗口内容**（NFR-8 隐私） |
| **性能预算 (M-18 解)** | E2 1Hz 轮询的 enumerate_top_windows call 限定 ≤ 30ms；性能监控走 metricsRing 单独 ring；超过 → 触发 graceful degrade 降到 0.2Hz |

新模块 `pet-anim/occlusionWatcher.ts`（接口见 TDD §2.11）

---

### Layer F — 静默档

#### F1：全屏 / 重打字 / 通话 → DND

**【BLOCKER B-9 解：删 "Plan B 砍 call detection"；改 graceful degrade】**
**【MAJOR M-19 解：KPM 阈值调整】**
**【MAJOR M-20 解：通用 audio session（不 hardcode 进程名）】**
**【MAJOR M-21 解：Probe-F1 通用版】**
**【MINOR m-5 解：ZZZ badge UI spec】**

| 维度 | 内容 |
|---|---|
| **触发** | 任一满足：(a) foreground 窗口处于 fullscreen；(b) 最近 3min 键盘输入率 > 250 KPM（v2 调强：v1 评审 50 KPM 误触发）；(c) **任意进程**有 active audio capture session（v2 不 hardcode Teams/Zoom — 用 Win32 Audio Session API 通用枚举）|
| **行为** | DND 模式：(1) 关闭 Perlin/saccade/gaze（applyTo step 2/3/4 早 return）；(2) 抑制 pulseInteraction（即使收到 click 不触发 TapBody）；(3) C3 hourly silent skip（详 §3 C3）；(4) blink_hz=0.1，breath 不变；(5) ZZZ badge UI 显示；(6) **AC-10-03 一票否决：DND 不抑制 supervisor severity=red**（red alert 仍弹 bubble） |
| **关键参数** | `fullscreen_check_interval_ms=2000`；`heavy_typing_kpm_threshold=250`；`typing_window_ms=180000`（3min）；`call_check_interval_ms=5000`；`dnd_blink_hz=0.1` |
| **ZZZ badge UI (m-5 解)** | 位置：pet 窗右上角，偏移 (+8, -4) px；尺寸：14×14 px；颜色：`#94a3b8`（与 placeholder 一致）；opacity 0.4；font-family: emoji；pointer-events: none；z-index 1（最底层不抢交互） |
| **写入语义** | DND active → AnimationOverlay 多 flag 临时 force-off (saccade/perlin/gaze)；setBlinkHz(0.1)；C3 trigger callback check dnd_active；ZZZ badge 通过 React state 控制 mount |
| **验收** | (a) 全屏视频 5s → DND + ZZZ；(b) 3min 内打字 250+ KPM → DND；(c) Teams/Zoom/Discord/Slack/Lark/Wechat call → DND（通用 audio session, 不 hardcode）；(d) red alert 仍弹（AC-10-03）；(e) DND 退出 → 1s 内恢复 |
| **降级** | flag off → 不监测；任一检测器（fullscreen / typing / call）失败 → 退化为其他 2 项继续工作（不砍整 FR）；用户在 SettingsPanel 可单独关任一 trigger；audio session API 失败 → call detection 自动 off（其他 2 仍运行）|
| **跨层** | **新 Rust commands**: `is_foreground_fullscreen()` / `is_any_audio_capture_active()` — 后者通用枚举所有进程的 audio capture session，非 hardcode |

新模块 `pet-anim/dndDetector.ts`（接口见 TDD §2.12）；UI: `PetDNDBadge.tsx`

---

## 4. 非功能需求（NFR）

| 编号 | 类别 | 要求 |
|---|---|---|
| NFR-1 | 性能 | FPS ≥ 28；overlay.applyTo ms/call ≤ 0.7ms；CPU 增量 ≤ 7%；内存增量 ≤ 50MB |
| **NFR-1.1** | **性能预算分配（M-24 解）** | **0.7ms 分配**：Perlin 0.10ms + blink 0.05 + saccade 0.05 + gaze 0.10 + emotion (D1) **0.10** + viseme (B3) **0.10** + held wobble (A1) 0.05 + occlusion (E2 单独 ring，不在 applyTo) + 其他 0.15。每项超 budget 触发独立性能 alert |
| NFR-2 | 可降级 | 每 FR 独立 flag；总开关 v2_all hard kill；详见 §4.1 flag 表 |
| NFR-3 | 兼容性 | v1 单测 386/386 + 27/27 OS 手测**零回归**；新模块全 TS；不依赖 Hiyori 没有的参数 |
| NFR-4 | 可测试 | 纯函数无 DOM（除 occlusion/dnd 需 Win32）；时钟/随机/ws/Tauri 注入；mock 后端 ws |
| NFR-5 | 可观测/声噪 | render loop 每 100 帧最多 1 行 console；新增 console.warn ≤ 6；debug 字段：emotion / dnd_active / held_state / user_input_active / thinking_active / occluded / low_energy / user_input_age_ms / edge_attached |
| NFR-6 | 时钟可注入 | 所有 now_t = DOMHighResTimeStamp 同源（继承 v1 + FIX-R2-01 修复）|
| NFR-7 | 零回归 | v1 ship + FIX-R3 行为不变；详见 §6.11 AC-3 snapshot test 套件 |
| NFR-8 | 安全/隐私 | occlusion/DND call 首次启用 SettingsPanel 弹 consent；仅读 bbox + 进程名 + audio session metadata，**不读**窗口内容/音频流；consent 持久化 localStorage `deskpet_consent_*` |
| NFR-9 | 跨层契约 | 新 ws 消息 ≤ 6；unknown message silent skip；新字段 optional；client_hello 版本握手 |

### 4.1 Feature Flag 对照表

| Flag Key | 默认 | 影响 FR |
|---|---|---|
| `deskpet_animation_v2` | `on` | v2 总开关 hard kill；off 时退 v1 行为 |
| `deskpet_anim_held` | `on` | A1 |
| `deskpet_anim_user_input` | `on` | B1 |
| `deskpet_anim_thinking` | `on` | B2 |
| `deskpet_anim_viseme` | `auto` | B3（自动选主路径 or fallback）|
| `deskpet_anim_mouth_fade` | `on` | B4 |
| `deskpet_anim_low_energy` | `on` | C1 |
| `deskpet_anim_welcome` | `on` | C2 |
| `deskpet_anim_time_celebration` | `on` | C3 |
| `deskpet_anim_emotion` | `on` | D1 |
| `deskpet_anim_milestone` | `on` | D2 |
| `deskpet_anim_edge` | `on` | E1 |
| **`deskpet_anim_occlusion`** | **`on`** | **E2（v2 评审 B-6 解：默认 on + consent）** |
| `deskpet_anim_dnd` | `on` | F1 |
| `deskpet_anim_dnd_fullscreen` / `_typing` / `_call` | `on` | F1 子开关 |

---

## 5. 触发-动画对照表（v2 完整，含 v1）

详见 v1 PRD §5 + v2 13 项扩展（13 项见 §3）

---

## 6. 接口契约

### 6.1 Live2DHandle 扩展（基于 v1）

v1 接口全保留；v2 新增（13 setters，比 v1 草稿少 1 个—— setHeldState 改名 setDragState 以区分 v1 pulseInteraction）：

```ts
// 新增 v2 setters
setDragState(state: 'idle'|'being_held'|'spring_back', now_t: number): void;  // A1（取代 v1 草稿的 setHeldState 名字）
setUserInputActive(active: boolean, now_t: number): void;       // B1
setThinkingActive(active: boolean, now_t: number): void;        // B2
setVisemeFrame(viseme: VisemeCode, now_t: number): void;        // B3
fadeMouthToZero(duration_ms: number, now_t: number): void;      // B4
setLowEnergy(active: boolean, now_t: number): void;             // C1
triggerWelcome(intensity: 'normal'|'bubble'|'intense', now_t: number): void; // C2 with escalation
triggerCelebration(kind: 'hourly'|'anniversary'|'milestone', message: string, now_t: number): void; // C3/D2
setEmotion(emotion: EmotionCode, now_t: number): void;          // D1
setEdgeAttached(edge: Edge, now_t: number): void;               // E1
setDNDActive(active: boolean, reasons: DNDReason[], now_t: number): void;  // F1
setPhonemeEstimatorReady(stream: VisemeFrame[], now_t: number): void;      // B3 fallback
```

**v1 兼容（B-13 解）**：v1 `pulseInteraction('click'/'double_click'/'hover_enter'/'hover_leave')` **完全保留**。A1 新增 setDragState 与 pulseInteraction **正交**：
- click 走 pulseInteraction（mousedown+up 无移动）→ TapBody
- drag 走 setDragState（mousedown+移动 > 5px）→ wobble + spring back，**不触发 click**

### 6.2 多 FR 并发参数写入优先级矩阵（BLOCKER B-12 解 — v2 最关键架构债）

每个 ParamX 可被多 FR 写入。冲突时按下表决定（**强制约定**）：

| Param | A1 held | B1 user_input | B2 thinking | B3 viseme | B4 fade | C1 low_energy | C2 welcome | D1 emotion | E1 edge | DND active | 总优先 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ParamMouthOpenY | — | — | — | **SET** | SET (fade) | — | — | SET (D1 surprised 0.4) | — | force 0 | **DND > B4 > B3 > D1** |
| ParamMouthForm | — | — | — | **SET** | — | — | SET (C2 happy ×1.3 复用 D1 happy.MouthForm) | SET | — | — | **B3 > C2 > D1** (viseme 优先；C2 intense 复用 D1 happy) |
| ParamAngleX | — | — | ADD +5° | — | — | — | — | — | — | force 0 | **DND > B2** |
| ParamAngleY | — | — | — | — | — | — | — | ADD (D1 sad -3°) | — | force 0 | **DND > D1** |
| ParamAngleZ | ADD wobble | ADD +3° tilt | — | — | — | — | — | — | SET pose | force 0 | **DND > A1 wobble > E1 pose > B1 tilt > base** |
| ParamEyeLOpen / ROpen | — | MUL ×1.15 | — | — | — | — | — | MUL (D1 sad 0.7 / angry 1.2 / surprised 1.5) | — | MUL ×0.1 (blink) | **DND blink ≫ D1 mul ≫ B1 mul** (MUL 链式相乘) |
| ParamEyeBallX | — | — | — | — | — | — | — | — | — | — | gaze + saccade (v1) |
| ParamEyeBallY | — | — | ADD +0.6 up | — | — | — | — | — | — | — | gaze + saccade + B2 |
| ParamEyeLSmile / RSmile | — | — | — | — | — | — | SET (×1.3 复用 D1 happy) | SET (D1 happy 0.7) | — | — | C2 > D1 (C2 intense 时) |
| ParamCheek | — | — | — | — | — | — | SET (×1.3 复用 D1 happy) | SET (D1 happy 0.5) | — | — | C2 > D1 |
| ParamBrowLY / RY | — | — | SET +0.3 | — | — | — | — | SET (D1 happy 0.2 / surprised 0.6) | — | — | **B2 > D1** (思考姿盖 emotion) |
| ParamBrowLAngle / RAngle | — | — | — | — | — | — | — | SET (D1 sad -0.5 / angry 1) | — | — | D1 |
| ParamBodyAngleZ | ADD wobble | — | — | — | — | — | — | — | — | force 0 | **DND > A1** |
| ParamBreath | — | — | — | — | — | MUL 1.5x | — | — | — | — | C1 (Day-0 Probe-D1 FAIL → 用 ParamBodyAngleY 替代) |
| ParamHairFront | — | OSC sin | — | — | — | — | OSC (×1.5 intense) | — | — | — | C2 intense > B1 |

**写入顺序（applyTo step 1→10）**：
- step 1: ParamMouthOpenY（DND force 0 → B4 fade → B3 viseme → D1 surprised）
- step 2-4: Perlin/gaze/saccade（DND/held block 时早 return）
- step 5: blink MULTIPLY ParamEyeLOpen/ROpen（D1 emotion eye_mul 先做，blink × 后做 — 链式相乘语义）
- step 6: ParamAngleZ（DND force 0 → A1 wobble > E1 pose > B1 tilt > base，**按矩阵 L528 顺序**）
- step 7: ParamAngleX（DND force 0 → B2 ADD）；ParamAngleY（DND force 0 → D1 ADD）
- step 8: ParamEyeBall（gaze + saccade + B2）
- step 9: 表情参数 SET（D1 + B2 brow conflict 时 B2 优先；C2 intense 复用 D1 happy ×1.3）
- step 10: clamp + Hiyori 范围兜底

**ADD/MUL/SET 计算次序（同 Param 同 step 内的形式化规则）**：

1. **MUL 链式相乘**：所有 MUL 源按 step 顺序依次相乘到 baseline（不是 max-take）。例如 EyeOpen step 5：`final = baseline × D1_mul × B1_mul × blink_mul`
2. **ADD 累加**：所有 ADD 源累加到 baseline 之上。例如 AngleZ step 6：`final = baseline + A1_wobble + B1_tilt`（E1 SET pose 时此公式不用，直接 SET）
3. **SET 覆盖**：SET 源按"总优先"列的顺序优先，高优先级 SET 覆盖低优先级
4. **同 step 内顺序**：MUL → ADD → SET → clamp（先按这次序计算，最后 clamp 到 Hiyori 范围）
5. **跨 step**：严格按 step 1→10 顺序；后 step 可读 / 覆盖前 step 写入结果

**DND 全局规则**：active 时 force-block step 2/3/4（Perlin/gaze/saccade）；force ParamAngleX/Y/Z=0（覆盖所有 ADD/SET）；blink_hz=0.1；DND 不抑制 D1 emotion 的 ParamMouthForm（because emotion 表情不打扰用户）。

### 6.3 跨层 ws 协议变更（含 B-11 client_hello 版本握手）

**新增 client_hello 握手消息（B-11 解）**：
```ts
// client → backend (on ws connect)
{ type: 'client_hello', version: 'v2.0', supports: ['viseme','emotion','milestone','dnd','occlusion'] }

// backend → client (response)
{ type: 'server_hello', version: 'X.Y', features: ['viseme','emotion','milestone'] }
```

旧 backend 不识别 client_hello → silent ignore，新 client 收 0 features → 走 fallback（关键词 emotion / phoneme viseme）。

| 消息 | 方向 | Payload | FR | v2 改 |
|---|---|---|---|---|
| `client_hello` / `server_hello` | 双向 | version + supports | 协议 | 🔥 新 |
| `chat_v2_event` | b→c | `{kind: 'thinking_start'/'thinking_end'/'first_chunk', ...}` | B2 | 已有 + 'first_chunk' 子事件 |
| `tts_viseme` | b→c | `{v: VisemeCode, t_ms}` | B3 | 🔥 新（S2 后端 sub-task） |
| `tts_end` | b→c | 已有 | B4 | 不变 |
| `chat_v2_final` | b→c | + optional `emotion` | D1 | 🔥 字段 |
| `transcript` | b→c | + optional `emotion` | D1 | 🔥 字段 |
| `pet_milestone` | b→c | `{kind, message, achieved_at}` | D2 | 🔥 新（S2 后端 sub-task） |

### 6.4 新增 Tauri commands (Rust)

```rust
#[tauri::command] fn enumerate_top_windows() -> Vec<TopWindowInfo>;        // E2
#[tauri::command] fn is_foreground_fullscreen() -> bool;                   // F1
#[tauri::command] fn is_any_audio_capture_active() -> bool;                // F1 (通用版，M-20)
// 注：E1 用 Tauri 已有 currentMonitor()，无需新 command（m-4 解）
```

### 6.5 新增 pet-anim 模块（v2 内 13 + 共用 1 = 14 个）

| 文件 | 职责 | 对应 FR |
|---|---|---|
| `heldStateMachine.ts` | A1 wobble + spring back FSM | A1 |
| `userInputObserver.ts` | B1 input event 监听 | B1 |
| `thinkingObserver.ts` | B2 chat_v2 thinking 订阅 + first-chunk 退出 | B2 |
| `visemeLipsync.ts` | B3 viseme blend (主路径) | B3 |
| `phonemeEstimator.ts` | B3 前端 phoneme 估计 (fallback) | B3 |
| `mouthFader.ts` | B4 fade + 800ms timeout | B4 |
| `idleWatcher.ts` | C1/C2 全局 idle + wakeup | C1/C2 |
| `timeCelebration.ts` | C3 时钟轮询 | C3 |
| `emotionMapper.ts` | D1 emotion → 参数表 + lock | D1 |
| `emotionClassifier.ts` | D1 fallback 投票分类器 | D1 |
| `edgeWatcher.ts` | E1 edge 检测 | E1 |
| `occlusionWatcher.ts` | E2 窗口枚举 + safe spot | E2 |
| `dndDetector.ts` | F1 三 trigger 综合 | F1 |
| `PetCelebrationBubble.tsx` | C3/D2 共用气泡 UI | C3/D2 |
| `PetDNDBadge.tsx` | F1 ZZZ 角标 | F1 |

### 6.6 修改 Live2DCanvas.tsx

- 接入 7 个 observer：userInput / thinking / idle / occlusion / dnd / edge / time
- hit-zone drag detection → setDragState（取代 v2 草稿 setHeldState 名）
- A1 drag block Perlin/saccade/gaze 的 applyTo step gate
- Mount UI：CelebrationBubble / DNDBadge / consent dialog
- Cleanup: 7 observer stop() 在 useEffect 卸载

### 6.7 修改 App.tsx

- chat-input focus/blur/change/composition → setUserInputActive
- sendChatV2 / first_chunk / chat_v2_final → setThinkingActive
- tts_end / 800ms silence → fadeMouthToZero
- chat_v2_final / transcript with emotion → setEmotion
- pet_milestone → triggerCelebration('milestone', ...)
- tts_viseme → setVisemeFrame（主路径） OR phonemeEstimator 输出 → setPhonemeEstimatorReady（fallback）
- client_hello on ws 连接 + 收 server_hello → 决定 viseme/emotion 主/fallback

### 6.8 修改 PetStateMachine

- 加 `low_energy`, `welcome` 状态（详 §3 C1 transition 图）
- STATE_CONFIG.low_energy: blink_hz=0.1, motion_tag_pool=['low-energy','slow','yawn']
- STATE_CONFIG.welcome (临时态 1.5s): motion_tag_pool=['fast'], TapBody on enter

### 6.9 后端 S2 并行 lane（B-5 解）

| 后端 sub-task | FR | 工作量 |
|---|---|---|
| `backend/tts/viseme_provider.py` — TTS provider 输出 viseme 流 | B3 | 3d |
| `backend/llm/emotion_prompt.py` — the relay system prompt 教学 + chat_v2_final emotion 字段 | D1 | 1d |
| `backend/memory/milestone.py` — milestone 规则 + 检测器 + ws push | D2 | 2d |
| Tauri Rust commands × 3 (enumerate_top_windows / is_foreground_fullscreen / is_any_audio_capture_active) | E2/F1 | 2d |
| client_hello / server_hello 握手 | 协议 | 0.5d |

**S2 后端总工作量 ≈ 8.5 工作日**（需后端 1 个工程师 S2 内并行做）

### 6.10 Permission Consent UX（评审 §F 解）

E2 occlusion / F1 call detection 首次启用时弹 consent 对话框：

```ts
// SettingsPanel 内 / 首次启用时
{
  title: '需要权限：枚举顶层窗口（用于桌宠避让）',
  description: 'DeskPet 需要读取其他窗口的位置和标题（不读内容），以便桌宠在被遮挡时自动让位。这些数据仅在本机使用，不上传。',
  consent: localStorage['deskpet_consent_occlusion']  // 'allow'|'deny'
}
```

- 拒绝 → flag 自动 off，UI 显示 "已禁用：需用户允许枚举窗口权限"
- 允许 → 持久化 localStorage；以后启动不再弹
- 在 SettingsPanel 可随时撤销

### 6.11 AC-3 v1 零回归 snapshot test 套件（评审 §E 解）

| 测试 | 方法 | 验证 |
|---|---|---|
| AC-3.1 v2_all=off → 386 单测 | `pnpm test:anim` with localStorage `deskpet_animation_v2=off` | 386/386 PASS |
| AC-3.2 v2_all=off → v1 27/27 OS 手测 | round-N 子代理跑 v1 ManualTest §16 | 27/27 PASS |
| AC-3.3 v2_all=on + 所有 13 FR flag off → applyTo 输出 snapshot | TC-AC3-snapshot：固定 input → AnimationOverlay.applyTo 写参数序列 dump | snapshot diff = 0 与 v1 baseline |
| AC-3.4 v2_all=on + 单 FR on → 仅相关 param 变 | TC-AC3-singleFR：逐 FR 开 1 个 → snapshot diff 仅含该 FR 写入的 param | 13 个 sub-test PASS |

---

## 7. 验收标准

| ID | 检查项 | 验收方法 |
|---|---|---|
| AC-1 | 13 个 FR 各自验收行全过 | 单元 + 手测 |
| AC-2 | NFR-1 总性能 + NFR-1.1 各项预算分配 | DevTools + 任务管理器 |
| AC-3 | v1 零回归 4 条 (§6.11) | snapshot + 全套手测 |
| AC-4 | 新代码 vitest 覆盖率 ≥ 80%L / 70%B | `pnpm test:anim:cov` |
| AC-5 | `pnpm tsc --noEmit` | CI |
| AC-6 | `pnpm lint` 不引入新错 | CI |
| AC-7 | 所有 P0 case 截图/录屏归档 | evidence/round-N/ |
| AC-8 | BLIND v1 vs v2 2/2 → PASS / 1/2 → WARN | evidence/blind-test/ |
| AC-9 | 16 项业界对照全部"已实现" | 文档勾选表 |
| AC-10 | **4 个一票否决**：D1 sad 不误归 happy / E2 不超屏 / F1 不抑 red alert / A1 drag 不破 v1 click | 手测专项 + AC-10-01..04 |

---

## 8. 里程碑（3 sprint，含后端并行 lane）

| Sprint | 天 | 前端 | 后端（并行 lane，B-5 解）|
|---|---|---|---|
| **S1** | D0-D2 | Day-0 6 探针 (A1/B3 主/B3 fallback/D1 Hiyori/D1 后端/D2/E2/F1)；A1 + B1 + B2 + B4；motion calibration v1 review；CI 全绿 | client_hello 握手协议 |
| **S2** | D3-D5 | B3 主+fallback；D1 emotion 5 类；C1 low_energy；**Motion calibration sub-task**（HiyoriMotionTuner 跑 m01-m10 → 标 yawn/dodge/edge 三 tag，0.5d）；C2 welcome escalation | TTS viseme provider (B3) / LLM emotion prompt (D1) / memory milestone.py (D2) / Tauri commands × 3 (E2+F1)|
| **S3** | D6-D8 | C3 time + D2 milestone client + E1 edge + E2 occlusion + F1 DND；评审 + AC-3 snapshot + BLIND 录视频 | 调通后端 / Permission consent UX 联调 |

### Plan B（NFR 不达标时，**graceful degrade 不砍 FR**）

- NFR-1 性能不达 → E2 1Hz → 0.2Hz；B3 blend_ms 60 → 100；Perlin 频率降；**不砍 FR**
- AC-3 v1 回归 → 立即回滚相应 setter 到 noop；FR 默认 off + BLOCKER 报告
- AC-10 一票否决 fail → 该 FR 默认 off + 必须 BLOCKER 报告

---

## 9. 风险与缓解（v2 round-1 评审更新）

18 项风险表，关键项：

| # | 风险 | v2 缓解 |
|---|---|---|
| 1 | B3 viseme 后端不支持 | **不再"返工"** — fallback 路径 v2 内必做（B-1/B-2 已解）|
| 2 | D1 emotion 后端协议未实现 | 双路径都做（B-4 已解）|
| 3 | E2 occlusion 1Hz 性能拖累 | graceful degrade 到 0.2Hz（B-7 已解）|
| 4 | F1 audio session Win11 兼容 | 通用 audio session API（M-20 已解）+ 单独 fallback off（B-9 已解）|
| 5 | A1 wobble 与 Tauri startDragging 冲突 | Day-0 Probe-A1 + B-10 明示纯参数模拟决策 |
| 6 | C1 false-positive（看视频无鼠标）| visibility 加 events 集 + DND 抑制 hourly 双重保护（M-5 解）|
| 7 | D2 memory 表未准备 | S2 后端 milestone.py 并行做（B-5 解）|
| 8 | E1 snap 破 v1 拖动手感 | edge_threshold 可调 + 首次靠近 toast 提示 |
| 9 | **多 FR 并发参数冲突** | **10x10 优先级矩阵明示**（B-12 已解 — v2 最大架构债）|
| 10 | viseme blend 嘴形抖动 | 60ms 最低 blend + 中文 phoneme 完整表（M-2 已解）|
| 11 | 隐私顾虑 E2/F1 | NFR-8 consent + §6.10 Permission UX |
| 12 | 旧 backend 兼容 | client_hello 握手 + unknown silent skip（B-11 已解）|
| 13 | Hiyori 参数缺失 | Day-0 Probe-D1 实测每个参数 |
| 14 | 整点动画干扰 | DND 抑制 hourly (M-9 已解) |
| 15 | 13 FR 超预算 | Plan B graceful degrade（不砍 FR）|
| 16 | viseme 协议变更影响其他 | optional field + capability negotiation |
| 17 | C2 welcome 过频 | low_energy 5min + welcome cooldown 1min |
| 18 | TDD 多 FR 并行打乱 | 每 FR 独立 task；红绿循环纪律 |

---

## 10. 开放问题（**v2 已拍板**，OQ 收口）

| OQ | 之前状态 | v2 拍板 |
|---|---|---|
| OQ-A1 wobble decay 4000ms | open | Day-0 视觉调参，候选 4000/6000/8000；本 PRD 用 4000 占位 |
| OQ-B3 后端 viseme | open | 双路径都做（B-1/B-2 解）|
| OQ-B3-2 blend_ms | open | 默认 60；ManualTest CASE-B3-blend-ab 跑 A/B 调参 |
| OQ-C3 anniversary 来源 | open | v2 内置应用安装日；其他用户自填 localStorage JSON |
| OQ-D1 5 类 emotion 够否 | open | 5 类实现 + disgust/fear TODO 占位（M-13 解）|
| OQ-D1-2 emotion 主路径 | open | 双路径都做（B-4 解）|
| OQ-D2 milestone 用户可配 | open | v2 内置 5 条规则（PRD §3 D2 正文列出）；用户自定义规则的 UI 不在本 PRD scope（亦不在 13 项 FR 内） |
| OQ-E2 occlusion 默认 | open | **默认 on + consent**（B-6 解）|
| OQ-F1 red alert | open | **不抑制**（AC-10-03 一票否决）|

---

## 11. 引用

- v1 PRD/evidence/commits（详 §1.1 / §1.3）
- 评审反馈：`evidence/round-0/architect-review-r1.md`
- Hiyori cdi3.json：`tauri-app/public/assets/live2d/hiyori/Hiyori.cdi3.json`

---

## 12. 修订日志

### v3 (round-2 评审应用 — MINOR 整改 ~2.5h)
- F-1 PRD §3 C1 + TDD §0 Probe-D1 扩含 ParamBreath + ParamBodyAngleY fallback 明示
- F-2 §6.2 矩阵补 C2 happy 复用 D1 happy 列；step 6 AngleZ 顺序与矩阵列对齐（DND > A1 wobble > E1 > B1）；新增 ADD/MUL/SET 计算次序 5 条形式化规则；EyeOpen/EyeOpenMul 命名统一
- F-3 TDD §4.15-b 加 dedicated milestone.test.ts (7 case)
- F-4 TDD §4.16 加 AC-3.2 cross-ref 注释（OS 手测在 ManualTest CASE-AC3-02）
- F-5 PRD L318/L322/L738 "v2.x 后续/配套" 改语为"不在本 PRD scope（亦不在 13 项 FR 内）"
- F-6 §8 表 S2 行独立列出 "Motion calibration sub-task" sub-bullet
- 评审通过：7.6/10 → 整改后估算 8.5/10 (GO)

### v2 (round-1 评审应用)
- 应用 13 BLOCKER + 关键 MAJOR (M-1/2/4/5/6/7/8/9/11/12/13/14/15/17/18/19/20/21/24 + m-1/2/3/4/5)
- §0 Scope 纪律强化 — 列出 6 类禁止措辞
- §1.2 表加 v2 处理列
- §3 各 FR：
  - A1 加 physics3 决策行；B-13 解 v1 兼容；改名 setHeldState→setDragState 区分 pulseInteraction
  - B1 加 ParamEar fallback + tilt/eye_boost 调强 + IME 兼容
  - B2 改 first-chunk 退出 + max 30s→90s + saccade 不 block
  - B3 双路径都做 + 完整中文 phoneme 映射表 + B3-fallback 子章节
  - B4 加 800ms 兜底
  - C1 删 "如 calibrated" + S2 motion calibration sub-task + events 集补 visibility/blur + 转换图重画
  - C2 Hiyori 2D 替代说明 + escalation 表 (5min/15min/1h 三档)
  - C3 dnd 抑制 hourly + localStorage 路径明示
  - D1 双路径都做 + 投票分类器 + 锁释放 (用户打断) + EmotionCode TODO 扩展
  - D2 milestone 5 条规则进 PRD 正文 + S2 后端 sub-task
  - E1 Hiyori 2D 替代说明 + 不做 snap preview + currentMonitor API
  - E2 默认 on + consent + 删 "Plan B 砍 E2" + grid sampling 48 候选 + 性能预算
  - F1 删 "砍 call detection" + KPM 50→250 + 3min 持续 + 通用 audio session + ZZZ badge UI spec
- §4 NFR-1.1 性能预算分配明示
- §6.1 setHeldState → setDragState 重命名
- §6.2 加 10x10 多 FR 并发参数写入优先级矩阵 (B-12)
- §6.3 加 client_hello/server_hello 协议握手 (B-11)
- §6.9 后端 S2 并行 lane 明示
- §6.10 Permission consent UX 子章节
- §6.11 AC-3 v1 零回归 snapshot test 套件 4 条
- §8 加后端并行 lane；Plan B 改 graceful degrade
- §10 OQ 全部收口

### v1 (round-0)
- 初稿
