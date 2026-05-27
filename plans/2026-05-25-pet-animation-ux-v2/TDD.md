# TDD — Pet Animation UX v2

| 项 | 值 |
|---|---|
| 关联 PRD | `PRD.md` v2 (本目录) |
| 版本 | **v2**（应用 round-1 评审 BLOCKER + 关键 MAJOR）|
| 测试框架 | vitest + @testing-library/react (继承 v1) |
| 覆盖率 | lines ≥ 80%，branches ≥ 70% |
| 复用 v1 | 9 模块 + AnimationOverlay + 386 单测 + cdp-runner + Win32 wrapper |

---

## 0. Day-0 探针（v2 评审后扩到 6 个）

### Probe-A1: Tauri startDragging + onPointerMove 共存

（同 v1 round-3 验证；FAIL 分支 a/b 见 PRD §3 A1）

### Probe-B3-后端: viseme 后端能力

```bash
cd backend && python -c "from providers.tts import get_tts; ..."
```

PASS → 走主路径；FAIL → 走前端 phoneme 估计 fallback。**两条都在 v2 scope** (B-1/B-2)。

### Probe-B3-前端: phoneme 估计器 viable

测试句子 → phoneme 估计 → 朋友盲听判定准确度 ≥ 70%。PASS → fallback OK；FAIL → BLOCKER 报告。

### Probe-D1-Hiyori: 表情参数实测（**F-1 round-2 整改**：扩含 ParamBreath + ParamBodyAngleY）

```ts
const params = [
  // emotion 用 (D1)
  'ParamEyeLSmile','ParamEyeRSmile','ParamCheek',
  'ParamBrowLY','ParamBrowRY','ParamBrowLAngle','ParamBrowRAngle',
  'ParamMouthForm','ParamMouthOpenY',
  // B1 user input 用
  'ParamHairFront',
  // C1 low-energy 用（v2 round-2 F-1 加）
  'ParamBreath','ParamBodyAngleY',
];
for (const p of params) {
  const idx = core.getParameterIndex(p);
  const oldval = core.getParameterValueByIndex(idx);
  core.setParameterValueByIndex(idx, 1.0);
  // ... visual check
}
```

**每参数 PASS/FAIL 处理**：
- emotion 系列 FAIL → emotionMapper 标 unusable，降级仅写有效参数
- ParamHairFront FAIL → B1 listening 用 ParamBustY 微动替代（不允许 silent skip）
- **ParamBreath FAIL → C1 low_energy breath 效果用 ParamBodyAngleY 微缓周期摆动替代**（period ×1.5 实现为 sin 周期 × 1.5）

每参数判 PASS/FAIL；**不允许任一参数 FAIL 后跳过对应 FR 效果** — 必须有 fallback 参数。

### Probe-D1-后端: LLM emotion 字段实证

```bash
cd backend && python -c "from llm import the relay; r = the relay.chat([...]); print(r.get('emotion'))"
```

PASS → 后端协议 ready；FAIL → S2 内修 the relay LLM system prompt + chat_v2_final 包装层。

### Probe-D2: memory 表 milestone 字段

```sql
SELECT column_name FROM memory_schema WHERE column_name='milestone_achieved';
```

PASS → schema 已 ready；FAIL → S2 内做 memory schema migration。

### Probe-E2: Win32 EnumWindows

（同 v1 round-1，Rust command 编译验证）

### Probe-F1-通用: audio session 枚举

测：任意进程开 audio capture → `is_any_audio_capture_active()` 返回 true。覆盖 Teams / Zoom / Discord / Slack / Wechat / Lark 6 个 app（M-20/M-21 解）。

**探针报告**：`evidence/round-0/probes.md`。任何 FAIL 按 PRD §8 Plan B 走 graceful degrade（**不砍 FR**）。

---

## 1. 架构分层（含 v1）

```
              App.tsx (v2 扩)
                  │ 7 observers: userInput/thinking/idle/occlusion/dnd/edge/time
                  │ + chat_v2 / tts_viseme / pet_milestone / client_hello
                  ▼
              Live2DCanvas.tsx (v2 扩)
                  │ hit-zone drag → setDragState (与 v1 pulseInteraction 正交)
                  │ Mount: CelebrationBubble / DNDBadge / Consent dialog
                  ▼
              AnimationOverlay (v2: applyTo 10 step)
                  │ multi-FR priority matrix (PRD §6.2)
                  ▼
       [A1][B1][B2][B3 主+fallback][B4][C1][C2][C3][D1 主+fallback][D2][E1][E2][F1]
```

---

## 2. 模块接口规范（按 FR 顺序）

### 2.1 heldStateMachine.ts (A1)

```ts
export type DragState = 'idle' | 'being_held' | 'spring_back'  // v2: renamed HeldState→DragState
export interface DragOpts {
  wobble_amplitude_deg?: number       // 8
  wobble_period_ms?: number            // 300
  wobble_decay_const_ms?: number       // 4000 (OQ-A1 Day-0 调参)
  spring_back_ms?: number               // 250
  surprise_duration_ms?: number         // 200
}
export interface DragContext { state, held_start_t, release_t }
export interface DragStep { ctx; wobble_delta; surprise_factor }
export function createDragStateMachine(opts?: DragOpts): {
  init(): DragContext
  onDragStart(ctx, now_t): DragStep
  onDragEnd(ctx, now_t): DragStep
  tick(ctx, now_t): DragStep
}
```

### 2.2 userInputObserver.ts (B1)

```ts
export interface UserInputOpts {
  stop_after_idle_ms?: number  // 1500
  input_selector?: string       // 'input,textarea'
  ime_aware?: boolean           // true: 忽略 compositionstart/end 期 keydown
}
export function createUserInputObserver(opts, callback: (active, now_t) => void)
```

### 2.3 thinkingObserver.ts (B2)

```ts
export interface ThinkingObserver {
  notifyStart(now_t): void
  notifyFirstChunk(now_t): void  // v2: first-chunk 退出 (M-1)
  notifyEnd(now_t): void          // tts_end 或 chat_v2_final / 用户 cancel
  isActive(now_t): boolean
}
export function createThinkingObserver(
  opts: { max_duration_ms?: number /* 90000, M-1 from 30s→90s */ },
  callback: (active, now_t) => void,
)
```

### 2.4 visemeLipsync.ts (B3 主路径)

```ts
export type VisemeCode = 'A' | 'I' | 'U' | 'E' | 'O' | 'silent'
export interface VisemeOpts { blend_ms?: number }  // 60
export function createVisemeLipsync(opts?): {
  push(frame: { v, t_ms }): void
  sample(now_t): { mouthY, mouthForm }
  flush(): void  // tts_end 时清队列
}
```

完整中文 phoneme→viseme 映射表见 PRD §3 B3 表。

### 2.4-b phonemeEstimator.ts (B3 fallback, **v2 内必做**)

```ts
export interface PhonemeEstimatorOpts {
  ms_per_char?: number  // 200 (中文平均 6 字/秒)
  pinyin_dict?: Record<string, string>  // 可注入；不注入用内置 minimal
}
export function createPhonemeEstimator(opts?): {
  estimate(transcript: string, total_duration_ms: number): VisemeFrame[]
}
```

算法详 PRD §3 B3-fallback。

### 2.5 mouthFader.ts (B4)

```ts
export interface MouthFader {
  start(from: number, duration_ms: number, now_t: number): void
  startWithTimeout(silence_timeout_ms: number, now_t: number): void  // v2: 800ms 兜底 (M-4)
  cancel(): void  // 收到新 viseme 时取消
  sample(now_t): number | null
}
```

### 2.6 idleWatcher.ts (C1/C2)

```ts
export interface IdleOpts {
  low_energy_threshold_ms?: number  // 300000
  wakeup_cooldown_ms?: number        // 60000
  events?: string[]  // 含 visibility/focus/blur (M-5)
}
export function createIdleWatcher(opts, {
  onLowEnergy: (now_t) => void
  onWakeup: (now_t, low_energy_duration_ms) => void  // v2: 传时长用于 escalation
})
```

### 2.7 timeCelebration.ts (C3)

```ts
export interface Anniversary { date: `MM-DD`; message: string }
export interface TimeCelebrationOpts {
  hourly_enabled?: boolean
  anniversaries?: Anniversary[]  // 从 localStorage 加载
  poll_interval_ms?: number  // 60000
  clock?: () => Date
  dnd_check?: () => boolean  // M-9: 整点前 check DND
}
export function createTimeCelebration(opts, callback: (kind, message, now_t) => void)
```

### 2.8 emotionMapper.ts (D1)

```ts
export type EmotionCode = 'happy'|'sad'|'angry'|'surprised'|'neutral'|'disgust'|'fear'  // M-13: 7 类，2 类 TODO
export interface EmotionParams {
  ParamMouthForm?, ParamEyeLSmile?, ParamEyeRSmile?, ParamCheek?,
  ParamEyeLOpenMul?, ParamEyeROpenMul?,
  ParamBrowLY?, ParamBrowRY?, ParamBrowLAngle?, ParamBrowRAngle?,
  ParamAngleY?, ParamMouthOpenY?
}
export const EMOTION_TABLE: Record<EmotionCode, EmotionParams> = {
  happy: { MouthForm:0.8, EyeLSmile:0.7, EyeRSmile:0.7, Cheek:0.5, BrowLY:0.2, BrowRY:0.2 },
  sad:   { MouthForm:-0.6, EyeLOpenMul:0.7, EyeROpenMul:0.7, AngleY:-3, BrowLAngle:-0.5, BrowRAngle:-0.5 },
  angry: { BrowLAngle:1, BrowRAngle:1, MouthForm:-0.8, EyeLOpenMul:1.2, EyeROpenMul:1.2 },
  surprised: { EyeLOpenMul:1.5, EyeROpenMul:1.5, MouthOpenY:0.4, BrowLY:0.6, BrowRY:0.6 },
  neutral: {},
  disgust: {},  // TODO M-13
  fear: {},     // TODO M-13
}
export function getEmotionParams(emotion: EmotionCode): EmotionParams
```

### 2.9 emotionClassifier.ts (D1 fallback, **投票法 M-12**)

```ts
const HAPPY_KEYWORDS = ['好的','没问题','哈','嘻','开心','棒','对','确认','赞','可以','成功','搞定','嗯']
const SAD_KEYWORDS = ['抱歉','对不起','遗憾','不好意思','不能','失败','错误','无法','糟','麻烦']
const ANGRY_KEYWORDS = ['不行','拒绝','警告','危险','立刻','马上','禁止']
const SURPRISED_KEYWORDS = ['突然','意外','惊','哎','啊','哦','咦','哦哟']

export function classifyEmotionVoting(text: string): EmotionCode {
  const scores = { happy: 0, sad: 0, angry: 0, surprised: 0 }
  for (const kw of HAPPY_KEYWORDS) if (text.includes(kw)) scores.happy += 1
  for (const kw of SAD_KEYWORDS) if (text.includes(kw)) scores.sad += 1
  for (const kw of ANGRY_KEYWORDS) if (text.includes(kw)) scores.angry += 1
  for (const kw of SURPRISED_KEYWORDS) if (text.includes(kw)) scores.surprised += 1
  const max = Math.max(...Object.values(scores))
  if (max === 0) return 'neutral'
  return Object.keys(scores).find((k) => scores[k] === max) as EmotionCode
}
```

### 2.10 edgeWatcher.ts (E1)

```ts
export type Edge = 'left'|'right'|'top'|'bottom'|null
export function createEdgeWatcher(opts: { edge_threshold_px?: number /*100*/ })
```

### 2.11 occlusionWatcher.ts (E2)

```ts
export interface WindowRect { x, y, w, h }
export interface TopWindowInfo { hwnd, title, rect: WindowRect, is_visible }
export interface OcclusionOpts {
  threshold_ratio?: number  // 0.5
  grace_ms?: number  // 5000
  poll_interval_ms?: number  // 1000 (graceful degrade 到 5000)
}
export function createOcclusionWatcher(opts, {
  fetchTopWindows: () => Promise<TopWindowInfo[]>,
  findSafeSpot: (petRect, screen, others) => Point | null,  // grid sampling 48 候选 (M-17)
  onOccluded, onClear,
})
```

### 2.12 dndDetector.ts (F1)

```ts
export type DNDReason = 'fullscreen'|'typing'|'call'
export interface DNDOpts {
  fullscreen_check_interval_ms?: number  // 2000
  typing_kpm_threshold?: number          // 250 (M-19: 50→250)
  typing_window_ms?: number              // 180000 (3min)
  call_check_interval_ms?: number         // 5000
  enabled_triggers?: DNDReason[]
}
export function createDNDDetector(opts, {
  fetchFullscreen, fetchCallActive, onChange,
})
```

---

## 3. AnimationOverlay v2 (applyTo 10 step + multi-FR priority matrix)

详见 PRD §6.2 矩阵。applyTo 顺序：

```
step 1: MouthOpenY → B4 fade → B3 viseme → D1 surprised → DND force 0
step 2: Perlin 3 维 → DND/A1 block 时早 return
step 3: gaze head → DND/A1 block
step 4: gaze eye + saccade → DND/A1 block (但 B2 thinking 时 saccade 仍跑)
step 5: blink MUL EyeLOpen/ROpen (先做 D1 eye_mul)
step 6: AngleZ = base + transient + B1 tilt + A1 wobble + E1 pose, DND force 0
step 7: AngleX (DND > B2)
        AngleY (DND > D1 sad)
step 8: EyeBall (gaze+saccade+B2)
step 9: 表情参数 SET (D1; B2 brow 与 D1 冲突时 B2 优先)
step 10: clamp + Hiyori 范围兜底
```

### AnimationOverlay v2 新增 setters

13 个，详见 PRD §6.1。

---

## 4. 测试用例

### 4.1 heldStateMachine.test.ts (A1, 6 cases)
TC-A1-01..05 同 v1 草稿；新增 TC-A1-06 setDragState != pulseInteraction 正交（drag 不破 click）

### 4.2 userInputObserver.test.ts (B1, 6 cases)
TC-B1-01..05 同 v1；新增 TC-B1-06 IME composition 期 keydown 不计活跃

### 4.3 thinkingObserver.test.ts (B2, 5 cases)
TC-B2-01..04 同 v1；新增 **TC-B2-05 first-chunk 退出**（notifyFirstChunk 立即 active=false）

### 4.4 visemeLipsync.test.ts (B3 主, 6 cases)
TC-B3-01..06 同 v1

### 4.4-b phonemeEstimator.test.ts (B3 fallback, 5 cases)
- TC-B3f-01 "妈妈" → A 系列 viseme frames
- TC-B3f-02 "你好" → I+A 系列
- TC-B3f-03 总时长保留：sum(frames durations) ≈ total_duration_ms
- TC-B3f-04 标点 → silent
- TC-B3f-05 未知字 → silent fallback (不抛)

### 4.5 mouthFader.test.ts (B4, 5 cases)
TC-B4-01..04 同 v1；**TC-B4-05 startWithTimeout(800) 真模拟 800ms 漏 tts_end 触发 fade**

### 4.6 idleWatcher.test.ts (C1/C2, 6 cases)
TC-C1-01..05 同 v1；**TC-C1-06 visibility/blur events 也算 idle reset (M-5)**

### 4.7 timeCelebration.test.ts (C3, 4 cases)
同 v1；新增 **TC-C3-04 dnd_check=true 时整点 silent skip (M-9)**

### 4.8 emotionMapper.test.ts (D1, 5 cases)
TC-D1-01..03 同 v1；**TC-D1-04 disgust/fear TODO 占位 (返回空对象不抛)**；**TC-D1-05 锁释放 'tts_interrupt'**

### 4.9 emotionClassifier.test.ts (D1 fallback, 7 cases — 投票法)
- TC-D1c-01 "好的" → happy
- TC-D1c-02 "抱歉" → sad
- TC-D1c-03 "不行" → angry
- TC-D1c-04 "突然" → surprised
- TC-D1c-05 "今天天气不错" → neutral
- **TC-D1c-06 投票法**: "抱歉，没问题" → sad（sad 1票 + happy 1 票 → 平票，但 sad 优先级排前 OR 取首个）
- **TC-D1c-07** "好的好的好" → happy 3 票 → happy

### 4.10 edgeWatcher.test.ts (E1, 3 cases) 同 v1

### 4.11 occlusionWatcher.test.ts (E2, 6 cases)
TC-E2-01..05 同 v1；**TC-E2-06 grid sampling findSafeSpot 48 候选 (M-17)** — 测多个 other windows 全填角落仍能找到中区 spot

### 4.12 dndDetector.test.ts (F1, 8 cases)
TC-F1-01..06 同 v1；**TC-F1-07 KPM 250 阈值 (M-19)** — 200 KPM 不触发，260 KPM 触发；**TC-F1-08 audio session 通用 (M-20)** — 6 个不同进程名 fake 都能 trigger

### 4.13 overlay v2 集成 (扩 v1 overlay.test.ts)
TC-O-v2-01..14 同 v1 草稿（v2 改 setHeldState→setDragState）；新增：
- **TC-O-v2-15 multi-FR 优先级冲突 (B-12)**: B1 active + D1 happy + DND active → ParamAngleZ 由 DND force 0；ParamMouthForm 由 D1 写 0.8（DND 不抑 emotion mouthForm）
- **TC-O-v2-16 B3 viseme > D1 emotion 优先**: 同时调用 setVisemeFrame('A') + setEmotion('happy') → MouthForm = 0（A 映射），EyeLSmile = 0.7（D1 非冲突 param 仍生效）

### 4.14 e2e_wire_v2.test.ts
同 v1 草稿 6 cases + 新增 **TC-E2E-v2-07 client_hello/server_hello 握手**

### 4.15 PetStateMachine v2 (扩 v1)
TC-PSM-v2-01/02 同 v1；新增 **TC-PSM-v2-03 low_energy → welcome → idle transition**

### 4.15-b milestone.test.ts (D2 dedicated, **F-3 round-2 整改**)
- TC-D2-01 (P0) streak_7d 规则：mock 7 天每天 ≥1 条 chat → 检测器触发
- TC-D2-02 (P0) streak_30d 规则
- TC-D2-03 (P0) msgs_1000 规则：累计 1000 条 user→assistant
- TC-D2-04 (P0) first_custom_prompt 规则
- TC-D2-05 (P0) first_pet_naming 规则
- TC-D2-06 (P0) 同日多 milestone 排队不并发：streak_7d + msgs_1000 同时达成 → FIFO 顺序触发
- TC-D2-07 (P1) milestone 跨 0 点判定：UTC vs system timezone（v2 用 system timezone）

### 4.16 AC-3 v1 零回归 snapshot test 套件（**v2 ship 准入硬条件 §6.11**）

```ts
// pet-anim/__tests__/ac3_snapshot.test.ts
describe('AC-3 v1 zero-regression', () => {
  it('AC-3.1 v2_all=off → 386 单测 PASS', () => {
    // localStorage.setItem('deskpet_animation_v2', 'off')
    // 跑 v1 套件 (perlin/blink/saccade/gaze/picker/scheduler/reactor/ring/flags/overlay)
    // 全 PASS
  })

  // AC-3.2 v2_all=off → v1 27/27 OS 手测 — **F-4 round-2 整改**
  // 此条不在 vitest 范围（OS 手测无法自动化）；
  // 由 ManualTest CASE-AC3-02 覆盖（子代理跑 v1 ManualTest §16 全 P0 case）

  it('AC-3.3 v2_all=on + 13 FR all off → applyTo snapshot diff = 0 vs v1 baseline', () => {
    // 固定 input (mouseX/Y, blink_hz, motion state)
    // 跑 100 帧 applyTo
    // dump 每帧 param write sequence
    // 与 v1 baseline.json compare
    // diff === 0
  })

  it('AC-3.4 单 FR on → 仅相关 param 变 (13 sub-tests)', () => {
    for (const fr of ['held','user_input','thinking','viseme','mouth_fade','low_energy','welcome','time_celebration','emotion','milestone','edge','occlusion','dnd']) {
      // localStorage 仅 fr on，其他 off
      // 跑 100 帧
      // diff vs v1 baseline 仅在该 FR 应写入的 param
    }
  })
})
```

---

## 5. Mock 策略

继承 v1 (vi.mock pixi/Tauri/ws/localStorage)；新增：
- `@tauri-apps/api/window` `getCurrentWindow` mock 含 `startDragging`, `setPosition`, `currentMonitor`
- `@tauri-apps/api/core` `invoke` mock 含 `enumerate_top_windows` / `is_foreground_fullscreen` / `is_any_audio_capture_active`
- viseme 流 mock：发送 fixed [{v:'A',t:0},{v:'I',t:80},{v:'silent',t:240}]
- emotion classifier 测试：注入 known 关键词样本

---

## 6. CI 入口

```bash
pnpm tsc --noEmit
pnpm lint
pnpm test:anim:cov          # v1 + v2 全部
pnpm test:e2e-wire-v2        # v2 e2e wire
pnpm test:ac3-snapshot       # AC-3 v1 零回归套件（v2 新加）
```

---

## 7. TDD 流程

每 FR 严格红→绿→重构（继承 v1）：
- D0 跑 6 探针；任 FAIL 触发 graceful degrade（不砍 FR）
- 红：先写 P0 case
- 绿：实现到 P0 过
- 重构：加 P1；coverage 80/70；tsc clean

---

## 8. 不在自动化测试范围（手测）

A1 wobble 视觉；B3 viseme 嘴形对得上；C1/C2 真等 5min；D1 emotion 视觉可辨；E2 真窗拖测；F1 真全屏 + 真打字 + 真 Teams；AC-3 OS 手测 27/27；BLIND v1 vs v2 1+1 盲选

---

## 9. 实现注意

1. **emotion vs viseme 冲突**：B3 viseme 优先写 mouthForm/mouthY；D1 emotion 仅生效"非嘴部"参数 + mouthForm 在 viseme 不活跃时 fallback。详 §6.2 优先级矩阵。
2. **DND 强制优先**：force-block step 2/3/4 但不抑 emotion mouthForm 或 red supervisor alert。
3. **A1 与 v1 click 正交**：setDragState 在 pointerdown + 移动 > 5px 才进 being_held；纯 click 仍走 pulseInteraction。
4. **multi-FR 参数写入**：严格按 §6.2 矩阵；不允许 case-by-case ad-hoc。
5. **C3 anniversary 持久化**：localStorage JSON，键 `deskpet_anniversaries`。后端持久化是配套优化非 FR。
6. **E2 graceful degrade**：1Hz → 5Hz 失败 → 0.2Hz；不砍整 FR。
7. **viseme 双路径**：根据 client_hello/server_hello 协议握手自动选；主路径优先，fallback 自动接管。
8. **F1 audio session 通用**：枚举所有进程 active audio capture，不 hardcode 进程名。

---

## 10. 修订日志

### v2
- 应用 round-1 评审：13 BLOCKER + 关键 MAJOR
- Day-0 探针 5→6（加 D1 后端 / D2 / F1 通用）
- B3 双路径都做 (主 + fallback)
- D1 双路径都做 + 投票分类器 + EmotionCode 扩展
- AC-3 snapshot test 套件 4 条进 TDD
- multi-FR 优先级矩阵明示
- KPM 50→250 + 3min 持续；通用 audio session

### v1
- 初稿
