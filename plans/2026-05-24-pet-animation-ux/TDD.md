# TDD — Pet Animation UX v1

| 项 | 值 |
|---|---|
| 关联 PRD | `PRD.md` v2 |
| 版本 | **v3**（应用 Round-2 评审反馈） |
| 测试框架 | vitest + @testing-library/react |
| 覆盖率 | lines ≥ 80%，branches ≥ 70% |
| v2→v3 变化 | §0 探针 DEV 守护 + 清理 checklist；§2.7 ReactorState 重命名 + 三元组 transition table；§2.10 setGazeTarget 加 now_t；§3.4 加 eye_yaw_deg→norm 单位转换；§3.6 step 4 公式补 norm；新增 §3.8 Latency Pairing Rule + TC-O-14b；TC-G-04 阈值 16°→14° 保守余量；§3.1 Perlin frequency 单位统一 |

---

## 0. Day-0 探针（开发首小时必跑，输出 evidence/round-0/probes.md）

> **v2 新增**：根据 Round-1 评审 BLOCKER B2 + 实战经验 #2/#3/#4，开发**第一件事**是这 4 个探针。任何一个失败 → 立即按对应降级路径调整或挂起 Sprint。
>
> **⚠️ v3 强制要求（解 MAJOR M'3）**：所有 Probe 代码必须用 `if (import.meta.env.DEV && !window.__probe_done) { ... ; window.__probe_done = true; }` 包裹；Day-0 探针完成后**必须 git revert 探针 commit**（或独立 branch 不合入 master）。验收 checklist：
> - [ ] prod build (`pnpm build`) 输出无 `[probe1..4]` 日志
> - [ ] git log master 最近 commit 不含 "probe" 相关代码
> - [ ] Live2DCanvas 内不存在 `data-pet-probe` 元素

### Probe-1：`coreModel.addParameterValueByIndex` 存在性 + ADD 持久性
```ts
// 在 Live2DCanvas init() 内 model load 完之后插入一次性日志
const core = (model as any).internalModel?.coreModel;
console.log('[probe1] has add?', typeof core?.addParameterValueByIndex);
const idx = core.getParameterIndex('ParamAngleX');
const before = core.getParameterValueByIndex(idx);
core.addParameterValueByIndex(idx, 5);
const after = core.getParameterValueByIndex(idx);
console.log('[probe1] before/after add:', before, after);
// 等 1 个 RAF
requestAnimationFrame(() => {
  const next_frame = core.getParameterValueByIndex(idx);
  console.log('[probe1] next frame:', next_frame);
});
```
- **PASS**：`typeof === 'function'`；after = before + 5；next_frame 是 motion3 新值（不是 after — 因为 motion3 会重写）
- **FAIL 分支 a**：`typeof === 'undefined'` → fallback `set(idx, get(idx) + delta)`，全 ADD 操作走 fallback 路径
- **FAIL 分支 b**：next_frame ≠ motion3 期望值 → 写入"消失"，说明 pixi-live2d-display 在 update() 后立即覆盖，**ADD 须在 motion update 之后调用**（确认 Live2DCanvas render loop 顺序）

### Probe-2：Hiyori model3.json Parameters 节点
```bash
# 直接 grep
grep -A 5 '"ParamEyeBallX"' tauri-app/public/assets/live2d/hiyori/Hiyori.model3.json
grep -A 5 '"ParamEyeBallY"' tauri-app/public/assets/live2d/hiyori/Hiyori.model3.json
```
- **PASS**：两参数都在 Parameters 节点，Default/Min/Max 字段存在
- **FAIL**：缺失任一 → FR-3 saccade、FR-4 eye 部分 silent skip（在 overlay.applyTo 内 idx=-1 已处理）
- **副产出**：拿到实际 Min/Max 写入 `pet-anim/_probe_constants.ts` 作为缩放基准

### Probe-3：window pointermove 在 ignore_cursor_events=true 下是否触发
```ts
// 在 main.tsx 入口加一次性
window.addEventListener('pointermove', (e) => {
  console.log('[probe3] pointermove triggered', e.clientX, e.clientY);
}, { once: true });
// 把鼠标在桌宠窗内移动一下，看 console
```
- **PASS**：能看到 log → FR-4 按 PRD §6.0 设计走（window 级监听）
- **FAIL**：完全收不到 → FR-4 降级为 hit-zone 内 pointermove（限定脸区域）

### Probe-4：hit-zone div 能否在 ignore_cursor_events=true 下吃 click
```tsx
// Live2DCanvas 临时加
<div data-pet-probe style={{
  position:'fixed', top:200, left:200, width:100, height:100,
  background:'red', pointerEvents:'auto', zIndex:9999,
}} onClick={() => console.log('[probe4] click received')}>PROBE</div>
```
- **PASS**：点击红方块 console 有 log → hit-zone 方案成立
- **FAIL**：点击穿透到桌面 → 需要 Tauri 侧 `set_ignore_cursor_events(false)` 配合，FR-6 走"按 Alt 暂关 ignore"降级路径

**探针报告必须输出到 `plans/2026-05-24-pet-animation-ux/evidence/round-0/probes.md`**，每条带 PASS/FAIL 截图。

---

## 1. 架构分层

```
                    ┌─────────────────────────────────┐
                    │ App.tsx (现有)                  │
                    │ ─ useLive2D ref                 │
                    │ ─ PetStateMachine.tick 调度     │
                    │ ─ result.state_changed → setMotionTagPool(force_switch_now=true) │
                    └─────────────┬───────────────────┘
                                  │ imperative API
                                  ▼
                ┌─────────────────────────────────────────┐
                │ Live2DCanvas.tsx (中改)                 │
                │ ─ render loop (30fps RAF timestamp)     │
                │ ─ window pointermove → overlay.setGazeTarget │
                │ ─ <div data-pet-hitzone> pointer events  │
                │ ─ overlay.applyTo(coreModel, t)         │
                │ ─ toBlob 回调 → metrics.visual.record   │
                └─────────────┬───────────────────────────┘
                              │
                              ▼
            ┌───────────────────────────────────────────┐
            │ pet-anim/index.ts (NEW)                   │
            │ AnimationOverlay class                    │
            │  ─ frame-level: applyTo(model, t)         │
            │  ─ state: setGazeTarget/clearGazeTarget   │
            │  ─ pulse: pulseInteraction(kind, t)       │
            │  ─ motion: setMotionTagPool(tags, opts)   │
            │  ─ metrics: getAnimationMetrics()         │
            │  ─ debug:   getAnimationDebug()           │
            └────┬────┬────┬────┬────┬────┬────┬───────┘
                 │    │    │    │    │    │    │
                 ▼    ▼    ▼    ▼    ▼    ▼    ▼
              [P]   [B]  [S]  [G]  [Pi] [Sc] [PR] [Mr] [Ff]
```
[P]=perlin, [B]=blink, [S]=saccade, [G]=gaze, [Pi]=motionPicker（纯选择）, [Sc]=motionScheduler（v2 拆出，时钟）, [PR]=pointerReaction, [Mr]=metricsRing, [Ff]=featureFlags

**核心原则**：
- `pet-anim/*` 全部不依赖 DOM 或 PixiJS（纯 TS）
- 所有"时间"通过参数 `t: number`（**DOMHighResTimeStamp，由 RAF 传入**）
- 随机注入 `rng: () => number`；测试用 seeded mulberry32
- `AnimationOverlay.applyTo` 是**唯一**接触 Live2D 的层
- **新增（v2）**：HMR 安全 — AnimationOverlay 提供 `dispose()` 清理所有内部 timer 引用

---

## 2. 模块接口规范

### 2.1 perlinNoise.ts
```ts
export interface PerlinOpts {
  seed?: number;            // default 1337
  amplitude?: number;       // default 1
  frequency?: number;       // default 0.3 (Hz；每秒周期数；时间单位 ms)
}
export function createPerlin1D(opts?: PerlinOpts): (t_ms: number) => number;
// 输出 [-amplitude, +amplitude]，同 seed 同 t 返回相同值
// 内部 phase = t_ms * frequency / 1000（即 t_ms * 0.0003 当 frequency=0.3）
// v3 统一：PRD §FR-1 "t * 0.0003" 等价 frequency=0.3 Hz
```

### 2.2 blinkScheduler.ts（v2 修订公式）
```ts
export interface BlinkOpts {
  blink_hz: number;
  rng?: () => number;
  close_duration_ms?: number;    // default 100
  double_blink_prob?: number;    // default 0.1
  sigma?: number;                // default 0.4
}
// 关键公式（v2）：mu = ln(1/blink_hz) - sigma²/2
// 这样 E[interval] = exp(mu + sigma²/2) = 1/blink_hz 准确（消除 8.3% 偏移）
export interface BlinkState {
  next_blink_t: number;
  in_blink: boolean;
  blink_start_t: number;
  pending_double: boolean;
}
export function createBlinkScheduler(opts: BlinkOpts): {
  init(now_t: number): BlinkState;
  tick(state: BlinkState, now_t: number): { state: BlinkState; eye_open_multiplier: number };
};
// eye_open_multiplier ∈ [0, 1]
// 写入语义：MULTIPLY 到 ParamEyeLOpen/ROpen（不是 SET）
// 即 final = motion3_val * eye_open_multiplier
```

### 2.3 saccadeScheduler.ts
```ts
export interface SaccadeOpts {
  min_interval_ms?: number;   // default 500
  max_interval_ms?: number;   // default 2000
  duration_ms?: number;       // default 45
  amplitude?: number;         // default 0.04（Probe-2 后按比例缩）
  rng?: () => number;
}
export function createSaccadeScheduler(opts?: SaccadeOpts): {
  init(now_t: number): SaccadeState;
  tick(state, now_t): { state; offset_x: number; offset_y: number };
};
// offset_x/y ∈ [-amplitude, +amplitude]
// 写入语义：ADD 到 ParamEyeBallX/Y（叠加在 FR-4 gaze 之后）
```

### 2.4 gazeTracking.ts（v2 修订 — face_radius 注入）
```ts
export interface GazeOpts {
  yaw_max_deg?: number;       // default 20
  pitch_max_deg?: number;     // default 15
  deadzone_deg?: number;      // default 5 (注意：角度，不是 px)
  lowpass_alpha?: number;     // default 0.15
  idle_recenter_ms?: number;  // default 10000
  head_follow_ratio?: number; // default 0.4
}
export interface GazeState {
  target_yaw: number;
  target_pitch: number;
  smoothed_yaw: number;
  smoothed_pitch: number;
  last_input_t: number;
  has_target: boolean;
  // v2 新增：face 中心 + 半径（动态注入）
  face_center_x: number;
  face_center_y: number;
  face_radius_css: number;
}
export function createGazeTracker(opts?: GazeOpts): {
  init(): GazeState;
  setFaceFrame(state, cx: number, cy: number, radius_css: number): GazeState;  // v2 新增
  setTarget(state, clientX: number, clientY: number, now_t: number): GazeState;
  clearTarget(state, now_t: number): GazeState;
  tick(state, now_t: number): { state; head_yaw: number; head_pitch: number; eye_yaw: number; eye_pitch: number };
};
// 角度计算：dx = clientX - face_center_x；归一化 dx/face_radius_css
// target_yaw_rad = atan2(normalized_dx, 1)
// head_yaw = eye_yaw * head_follow_ratio
// 死区：|target - prev_smoothed| < deadzone_deg → target = prev_smoothed
```

### 2.5 motionPicker.ts（v2 修订 — 纯选择，无时钟）
```ts
export type MotionTag = 'fast' | 'medium' | 'slow' | 'special';
export interface MotionPickerOpts {
  rng?: () => number;
}
export function createMotionPicker(opts?: MotionPickerOpts): {
  init(): { recent_idx: number[] };
  pick(state, candidates: number[]): { state; idx: number | null };
};
// 实现：
// 1) candidates.length === 0 → return null
// 2) candidates.length === 1 → 始终返回 idx（recent_idx 清空避免死循环）
// 3) else 从 candidates 中过滤 recent_idx 最后 3 个；如全过滤掉退回最早的；选中后 push 进 recent_idx，shift 至 ≤3
```

### 2.6 motionScheduler.ts（v2 新增 — 与 picker 拆分）
```ts
export interface MotionSchedulerOpts {
  switch_period_ms: number;
  jitter_ratio?: number;       // default 0.3
  rng?: () => number;
}
export function createMotionScheduler(opts: MotionSchedulerOpts): {
  init(now_t: number): { next_switch_t: number };
  shouldSwitch(state, now_t: number): boolean;
  scheduleNext(state, now_t: number): { state };  // 调用后更新 next_switch_t
  forceSwitchNow(state, now_t: number): { state }; // v2 新增：state_changed 时立即切
};
// next_switch_t = now_t + period_ms * (1 + (rng()*2-1) * jitter_ratio)
// shouldSwitch 仅基于 now_t >= next_switch_t
// forceSwitchNow 把 next_switch_t 设为 now_t（即"应立即切"），scheduleNext 之后正常 period
```

### 2.7 pointerReaction.ts（v3 修订 — 重命名 + 三元组 cell + 解 MAJOR M'1）

```ts
export type InteractionKind = 'hover_enter'|'hover_leave'|'click'|'double_click';

// v3 修订：ReactorState 去掉 'hovering'，避免与 boolean `is_hovering` 命名冲突
export type ReactorState =
  | 'rest'                    // 静默期（无 pending 事件、无 pulse）
  | 'pending_single'          // 单击后等 threshold 是否升级 double
  | 'in_click_pulse'          // click 反应中
  | 'in_double_pulse';        // double click 反应中

export interface ReactorContext {
  state: ReactorState;
  is_hovering: boolean;        // 独立 boolean，正交于 state
  pending_click_ts: number;    // pending_single 进入时间
  pulse_start_ts: number;      // in_*_pulse 进入时间
}

export interface PointerReactionOpts {
  double_click_threshold_ms?: number;  // default 300
  hover_debounce_ms?: number;          // default 50
  click_pulse_ms?: number;             // default 200
  double_pulse_ms?: number;            // default 400
}

// === Transition Table (v3 三元组 cell: (nextState, nextIsHovering, effect)) ===
//
// 当前 state \ 事件 | onPointerEnter             | onPointerLeave              | onClick (now_t)                   | tick (now_t)
// ----------------- | -------------------------- | --------------------------- | --------------------------------- | --------------------
// rest              | (rest, true, 'hover_enter')| (rest, false, null) ignore  | (pending_single, hover↑, null)    | (rest, hover↑, null)
// pending_single    | (pending_single, true, 'hover_enter') | (pending_single, false, 'hover_leave') | (in_double_pulse, hover↑, 'double_click') 清 pending | 超时 threshold → (in_click_pulse, hover↑, 'click')
// in_click_pulse    | (in_click_pulse, true, 'hover_enter' if not is_hovering else null) | (in_click_pulse, false, 'hover_leave' if is_hovering else null) | (in_double_pulse, hover↑, 'double_click') | pulse 结束 → (rest, hover↑, null)
// in_double_pulse   | (in_double_pulse, true, 'hover_enter' if not is_hovering else null) | (in_double_pulse, false, 'hover_leave' if is_hovering else null) | (in_double_pulse, hover↑, null) **吞**（v3：PRD 已声明为设计预期） | pulse 结束 → (rest, hover↑, null)

// 关键点：
// 1. is_hovering 永远跟随 enter/leave，独立于 state 演进
// 2. pulse 期间收到 enter/leave 不破坏 pulse 状态（只更新 is_hovering 和 emit hover 反馈）
// 3. is_hovering 已为 true 时收到 enter 不重发（防 debounce 内反复）
// 4. in_double_pulse 期间 onClick 被吞，符合 PRD §FR-6 "设计预期"

export function createPointerReactor(opts?: PointerReactionOpts): {
  init(): ReactorContext;
  onPointerEnter(ctx: ReactorContext, now_t: number): { ctx: ReactorContext; effect: InteractionKind | null };
  onPointerLeave(ctx: ReactorContext, now_t: number): { ctx: ReactorContext; effect: InteractionKind | null };
  onClick(ctx: ReactorContext, now_t: number): { ctx: ReactorContext; effect: InteractionKind | null };
  tick(ctx: ReactorContext, now_t: number): { ctx: ReactorContext; effect: InteractionKind | null };
};
```

### 2.8 metricsRing.ts（v2 修订）
```ts
export function createMetricsRing(capacity?: number /* 100 */): {
  record(latency_ms: number): void;
  snapshot(): { p50: number; p95: number; max: number; samples: ReadonlyArray<number> };
  reset(): void;  // v2 新增
};
// samples 返回 [...internal]（避免外部 mutate；mr-Round1-m6）
```

### 2.9 featureFlags.ts（v2 修订 — all=off hard kill）
```ts
export type AnimFlag = 'all' | 'perlin' | 'blink' | 'saccade' | 'gaze' | 'motionpool' | 'pointer';
const FLAG_KEYS: Record<AnimFlag, string> = {
  all: 'deskpet_animation_v1',
  perlin: 'deskpet_anim_perlin',
  blink: 'deskpet_anim_blink',
  saccade: 'deskpet_anim_saccade',
  gaze: 'deskpet_anim_gaze',
  motionpool: 'deskpet_anim_motionpool',
  pointer: 'deskpet_anim_pointer',
};
export function isEnabled(flag: AnimFlag, storage?: Storage): boolean;
// 规则：
//   - 如果 storage.getItem(FLAG_KEYS.all) === 'off' → 所有 flag 返回 false（hard kill）
//   - 否则查 individual flag：null（未设置）→ true；'on' → true；'off' → false
//   - storage throw → 返回 default（all 默认 on，individual 默认 on）
```

### 2.10 index.ts — AnimationOverlay
```ts
export interface CoreModelLike {
  getParameterIndex(name: string): number;
  setParameterValueByIndex(idx: number, val: number): void;
  addParameterValueByIndex?(idx: number, val: number): void;
  getParameterValueByIndex(idx: number): number;
}
export interface OverlayOpts {
  rng?: () => number;
  storage?: Storage;
  motionLabelsLoader?: () => Record<MotionTag, number[]> | null;  // v2 — 注入 source
}
export class AnimationOverlay {
  constructor(opts?: OverlayOpts);
  dispose(): void;                                                 // v2 — HMR / unmount 清理

  // Supervisor → overlay
  setBlinkHz(hz: number): void;
  setStateBaseHeadTilt(deg: number): void;                         // v2 — 持续值
  pulseHeadTiltDelta(deg: number, duration_ms: number, now_t: number): void; // v2 — 瞬态
  setMouthOpenY(v: number): void;

  // Motion
  setMotionTagPool(
    tags: MotionTag[],
    opts: { force_switch_now: boolean },
    now_t: number,
  ): void;
  setMotionPlayer(player: (group: string, idx: number) => void): void;

  // Gaze (v3：face frame 可多次更新，跟随窗口/模型变化)
  setFaceCenter(cx_css: number, cy_css: number, radius_css: number): void;
  setGazeTarget(clientX: number, clientY: number, now_t: number): void;
  clearGazeTarget(now_t: number): void;

  // Pointer reactions
  pulseInteraction(kind: InteractionKind, now_t: number): void;

  // Frame loop
  applyTo(model: CoreModelLike, now_t: number): void;

  // Latency
  recordInteractionEventTs(kind: 'click'|'double_click', now_t: number): void;  // v2 — visual 配对用
  recordVisualFrameTs(now_t: number): void;                                     // v2 — toBlob 回调里调
  getAnimationMetrics(): { interaction: SnapshotResult; visual: SnapshotResult };
  getAnimationDebug(): {
    gaze_target_yaw: number; gaze_smoothed_yaw: number;
    last_input_age_ms: number;
    current_motion_idx: number | null;
  };
}
```

---

## 3. 关键算法说明

### 3.1 1D Perlin（30 行实现）
- Improved Perlin 2002 改进梯度表（12 个梯度的 1D 投影）
- permutation table 由 seed 通过 mulberry32 生成（512 长，二倍包装）
- 输入 `t_ms`，内部 `t = t_ms * frequency / 1000`，取 floor 索引；smoothstep `6t⁵-15t⁴+10t³`

### 3.2 对数正态 blink 间隔（v2 修订 — 抵消均值偏移）
```
sigma = 0.4
mu = ln(1 / blink_hz) - (sigma² / 2)
Z = sqrt(-2 ln(u1)) * cos(2π u2)   // Box-Muller，u1∈(0,1], u2∈[0,1)
nextInterval_ms = 1000 * exp(mu + sigma * Z)
```
推导：lognormal 均值 `E[X] = exp(mu + sigma²/2)`。我们想 `E[X] = 1/blink_hz`，故 `mu = ln(1/blink_hz) - sigma²/2`。

### 3.3 双眨触发
每次 blink 结束：`if (rng() < 0.1) → pending_double=true; next_blink_t = now + 200ms`。

### 3.4 视线低通（v2 修订 — face_radius 动态）
```
dx = clientX - face_center_x
dy = clientY - face_center_y
norm_dx = dx / face_radius_css
norm_dy = dy / face_radius_css
target_yaw_deg   = clamp(atan2(norm_dx, 1) * 180/π, -yaw_max_deg, +yaw_max_deg)
target_pitch_deg = clamp(atan2(norm_dy, 1) * 180/π, -pitch_max_deg, +pitch_max_deg)
if |target_yaw - prev_smoothed_yaw| < deadzone_deg → target_yaw = prev_smoothed_yaw
smoothed_yaw = α * target_yaw + (1-α) * prev_smoothed_yaw
head_yaw_deg = smoothed_yaw * head_follow_ratio        // 写到 ParamAngleX（degrees）
eye_yaw_deg  = smoothed_yaw                            // 写到 ParamEyeBallX 前需归一化
```
负 clientX（多显示器、副屏在左）→ dx<0 → norm_dx<0 → atan2 返回负值 → clamp 到 -yaw_max。**不能用 abs**。

**v3 单位转换补丁（解 MINOR m'2）**：

ParamAngleX/Y 直接接受 degrees（Cubism 4 惯例），可直接 ADD。但 ParamEyeBallX/Y 是归一化 [-1, +1]（v3 Day-0 Probe-2 后从 `_probe_constants.ts` 读实际范围 `eyeball_max_x` / `eyeball_max_y`）：

```
eye_yaw_norm   = (eye_yaw_deg   / yaw_max_deg)   * eyeball_max_x   // 通常 1.0
eye_pitch_norm = (eye_pitch_deg / pitch_max_deg) * eyeball_max_y   // 通常 1.0
```

返回值 `{ head_yaw_deg, head_pitch_deg, eye_yaw_norm, eye_pitch_norm }`（v3 重命名以避免混用）。

### 3.5 motion 选择策略
- picker.pick(candidates)：candidates 已经是按 tag 筛过的 idx 数组；过滤 recent_idx 最近 3 个；返回选中 idx 同时更新 recent_idx
- scheduler.shouldSwitch(now_t)：仅判断 `now_t >= next_switch_t`
- 联动：每帧 `if (scheduler.shouldSwitch(t)) { idx = picker.pick(candidates); player(group, idx); scheduler.scheduleNext(t); }`

### 3.6 applyTo 写入顺序（v2 修订 — 头倾改 SET）

```
（motion3 在 Live2D 内部 update 写完 params）
step 1: setParameterValueByIndex(ParamMouthOpenY, mouth)               // SET 覆盖
step 2: ADD ParamAngleX/Y/BodyAngleX += perlin × 3 维                   // ADD（degrees）
step 3: ADD ParamAngleX += gaze.head_yaw_deg                            // ADD（degrees）
        ADD ParamAngleY += gaze.head_pitch_deg                          // ADD（degrees）
step 4: ADD ParamEyeBallX += gaze.eye_yaw_norm + saccade.offset_x       // ADD（已归一化，v3 §3.4 公式）
        ADD ParamEyeBallY += gaze.eye_pitch_norm + saccade.offset_y     // ADD（已归一化）
step 5: MULTIPLY ParamEyeLOpen *= blink.eye_open_multiplier             // set(get*mul)
        MULTIPLY ParamEyeROpen *= blink.eye_open_multiplier
step 6: SET ParamAngleZ = state_base_tilt + active_transient_delta     // SET（degrees）
step 7: clamp 全部到模型 max/min 范围（fallback ±20/±15/±15 degrees；EyeBall ±eyeball_max）
```

**MULTIPLY 实现**：因为 coreModel 没有原生 multiply API，统一用 `set(idx, get(idx) * mul)`。注意 get 必须在 motion3 已 update 之后调用（pixi-live2d-display 的 render loop 默认先 update 再让我们写）。

**单维 fallback**：每个 step 用 `getParameterIndex` 拿 idx，若 idx === -1 跳过该 step（silent skip + 不计错）。

### 3.7 失败模式与 fallback（v2 新增）

| 失败 | 检测 | Fallback |
|---|---|---|
| addParameterValueByIndex undefined | typeof check（init 时一次） | 切换到 `setParameterValueByIndex(idx, getParameterValueByIndex(idx) + delta)` |
| coreModel === undefined（模型未加载） | every-frame check | applyTo 直接 return（不抛） |
| ParamEyeBallX/Y 缺失 | idx === -1 | FR-3/FR-4 该维 silent skip |
| localStorage throw（quota / disabled） | try/catch | 视作未设置 → 默认 on |
| motionLabelsLoader 返回 null | check | candidates 退回默认（全 Idle），等同 PetStateMachine 现有行为 |
| motionPlayer 未注入 | check | scheduler.shouldSwitch 仍 tick 但不调 player；无副作用 |

### 3.8 Latency Pairing Rule（v3 新增 — 解 MAJOR M'2，配合 PRD §6.8）

AnimationOverlay 维护：
```ts
interface PendingClickEntry { kind: 'click'|'double_click'; event_ts: number; }
private pending_clicks: PendingClickEntry[] = [];   // FIFO，cap=20
```

接口：
```ts
recordInteractionEventTs(kind, event_ts: number):
  pending_clicks.push({kind, event_ts})
  if pending_clicks.length > 20: pending_clicks.shift()   // 防御性裁剪
  interaction_metrics.record(performance.now_alike(event_ts) - event_ts)  // 仅在 applyTo 触达写参数时记
  // 但 interaction 应该是 event→applyTo 调用的同步距离，由 Live2DCanvas 在调 pulseInteraction 时直接 record (now_t - event_ts)

recordVisualFrameTs(frame_ts: number):
  if pending_clicks.length === 0: return
  const head = pending_clicks.shift()           // FIFO 队首
  visual_metrics.record(frame_ts - head.event_ts)
```

**关键性质**（与 PRD §6.8 完全一致）：
- FIFO 队首匹配（不是 LIFO）
- 一个 event 只匹配一帧；匹配后移除
- 双击产生两个 event 入队（即使第二个被 reactor 升级 double_click，event_ts 也独立记录）
- 队列 cap=20 防御 hover/click 暴风
- visual_metrics 只记 frame_ts - head.event_ts（毫秒级正数；同 DOMHighResTimeStamp 基保证）

### 4.1 perlinNoise.test.ts
- TC-P-01 (P0) 同 seed 同 t 返回相同值
- TC-P-02 (P0) 输出范围 [-amplitude, +amplitude]
- TC-P-03 (P0) 不同 seed 在 1000 采样 Pearson |r| < 0.3
- TC-P-04 (P1) 1Hz 频率 1s 内至少 1 个零点穿越
- TC-P-05 (P1) 缺省值不抛
- TC-P-06 (P2) 1000 次 < 10ms

### 4.2 blinkScheduler.test.ts（v2 修订）
- TC-B-01 (P0) blink_hz=0.5、1000 次模拟，**均值间隔 ∈ [1700, 2300] ms（±15%，更紧）**
- TC-B-02 (P0) 相邻间隔差异方差 > 0
- TC-B-03 (P0) 闭眼时长 100±20ms（半正弦完整曲线）
- TC-B-04 (P0) blink_hz=0 时 eye_open_multiplier ≡ 1
- TC-B-05 (P1) seeded rng + 1000 次模拟，double-blink 比例 ∈ [7%, 13%]
- TC-B-06 (P1) tick 时间倒退（now_t < prev now_t）不崩，state 不变
- TC-B-07 (P0) **新增**：mu 公式正确性 — sigma=0.4、blink_hz=1 时，1000 次平均 ≈ 1000ms ± 30ms

### 4.3 saccadeScheduler.test.ts
- TC-S-01 (P0) 30s 模拟触发次数 ∈ [15, 60]
- TC-S-02 (P0) 期间 |offset| ≤ amplitude
- TC-S-03 (P0) 结束后 offset_x = offset_y = 0
- TC-S-04 (P1) seeded rng 可复现

### 4.4 gazeTracking.test.ts（v2 修订）
- TC-G-01 (P0) face_center=(960,540), face_radius=200, target=(960,540) → smoothed_yaw=0
- TC-G-02 (P0) target 远超 ±20°（dx=10000） → smoothed clamp 在 ±20°
- TC-G-03 (P0) 死区：|Δtarget| < deadzone → smoothed 不更新
- TC-G-04 (P0) 阶跃 0→20°，10 次 tick 后 smoothed > **14°**（v3 修订：考虑死区在收敛尾段抑制，留保守余量）
- TC-G-05 (P0) clearTarget 后 idle_recenter_ms 内 smoothed → 0
- TC-G-06 (P0) head_yaw = eye_yaw × head_follow_ratio 严格成立
- TC-G-07 (P0) **新增**：负 clientX（副屏在左，clientX=-300）→ smoothed_yaw < 0 且不为 NaN
- TC-G-08 (P1) setFaceFrame 改 face_radius=400 后，相同 clientX 产生角度减半

### 4.5 motionPicker.test.ts
- TC-MP-01 (P0) candidates=[1,2,3]，连 6 次 pick，每个 idx ≥ 1 次
- TC-MP-02 (P0) candidates=[] → null
- TC-MP-03 (P0) recent_idx 最近 3 个不重复（candidates ≥ 4）
- TC-MP-04 (P0) candidates=[5] → 始终返回 5
- TC-MP-05 (P1) candidates=[1,2] → 不死锁（每次都返回 1 或 2）

### 4.6 motionScheduler.test.ts（v2 新增）
- TC-MS-01 (P0) shouldSwitch 在 period * (1 - jitter) 之前永远 false
- TC-MS-02 (P0) shouldSwitch 在 period * (1 + jitter) 之后必为 true
- TC-MS-03 (P0) forceSwitchNow 后 shouldSwitch === true（立刻）
- TC-MS-04 (P0) scheduleNext 之后 next_switch_t 推进且带 jitter
- TC-MS-05 (P1) seeded rng 可复现 jitter 序列

### 4.7 pointerReaction.test.ts（v2 修订 — transition table）
- TC-PR-01 (P0) idle → onClick → state=pending_single, effect=null（推迟 emit）
- TC-PR-02 (P0) pending_single + threshold_ms 后 tick → in_click_pulse, effect='click'
- TC-PR-03 (P0) pending_single + onClick（threshold 内） → in_double_pulse, effect='double_click'
- TC-PR-04 (P0) 301ms+ 两次 click → 两次 'click'（合计 2 次 emit）
- TC-PR-05 (P0) hover_enter 与 hover_leave 顺序
- TC-PR-06 (P0) **新增**：hovering=true 期间 onClick → click 反应不打破 hovering（pulse 结束后 state 回到 hovering 而非 idle）
- TC-PR-07 (P0) **新增**：in_double_pulse 期间 onClick 被 ignore（effect=null，不触发三连击；ctx.state 保持 in_double_pulse）
- TC-PR-08 (P1) hover_debounce 50ms 内 enter→leave→enter 只 emit 一次
- TC-PR-09 (P0) **v3 新增**：is_hovering=true 状态下 in_click_pulse 期间收到 onPointerLeave → ctx.is_hovering=false + effect='hover_leave'，但 ctx.state 仍为 in_click_pulse（不打断 pulse）
- TC-PR-10 (P0) **v3 新增**：is_hovering=true 期间 onPointerEnter 重发 → effect=null（防 debounce 内反复）

### 4.8 metricsRing.test.ts
- TC-MR-01 (P0) record 100 → samples.length=100
- TC-MR-02 (P0) record 150 → 保留最后 100
- TC-MR-03 (P0) 1..100 → p50=50/51, p95=95/96
- TC-MR-04 (P0) 空 ring → 全 0
- TC-MR-05 (P0) **新增**：snapshot.samples 修改不影响内部状态（返回 readonly slice）
- TC-MR-06 (P1) reset() 后 snapshot 全 0

### 4.9 featureFlags.test.ts
- TC-F-01 (P0) localStorage 空 → 所有 isEnabled true
- TC-F-02 (P0) `deskpet_animation_v1='off'` → 所有 false（hard kill）
- TC-F-03 (P0) all=off + individual=on → 全 false（hard kill 不可覆盖）
- TC-F-04 (P0) `deskpet_anim_perlin='off'` 单独 → 只 perlin false
- TC-F-05 (P1) storage throw → 默认 on

### 4.10 overlay.test.ts（集成 — v2 大幅扩充）
> 用 stub coreModel（详见 §5.2）
- TC-O-01 (P0) applyTo 所有 param 都 missing 不抛
- TC-O-02 (P0) flag all=off → applyTo 早 return（无任何 set/add）
- TC-O-03 (P0) **新增**：addParameterValueByIndex undefined fallback 验证 — stub model 不实现 add，setLog 应该看到 set(idx, prev+delta)
- TC-O-04 (P0) setStateBaseHeadTilt(-5) + pulseHeadTiltDelta(+3, 200ms) → ParamAngleZ = -2（200ms 内）→ -5（200ms 后）
- TC-O-05 (P0) setBlinkHz(0.5) → 1000 帧后 ParamEyeLOpen 至少有一次 ≈ 0
- TC-O-06 (P0) setMotionTagPool(['fast'], {force_switch_now:true}) + loader 返回 fast=[1,3] → 立即 motionPlayer 收到 ('Idle', 1) or ('Idle', 3)
- TC-O-07 (P0) setMotionTagPool(['fast'], {force_switch_now:false}) + period 未到 → 不调 motionPlayer
- TC-O-08 (P0) **新增**：子集 size < 2 兜底 — fast=[1]、loader 也有 medium=[5] → candidates 自动并入成 [1, 5]
- TC-O-09 (P0) pulseInteraction('click') → 200ms 内 ParamAngleZ 出现 ±5° 摆动；**且 motionPlayer 在 ≤50ms 内被以 ('TapBody', 0) 调用一次（v3 新增 — 解一致性问题 #4）**
- TC-O-10 (P0) pulseInteraction('double_click') → ParamAngleZ ±10° + blink_hz 提升
- TC-O-11 (P0) setFaceCenter(960,540,200) + setGazeTarget(1160,540) → ParamEyeBallX > 0
- TC-O-12 (P0) clearGazeTarget → idle_recenter_ms 后 ParamEyeBallX 回 0
- TC-O-13 (P0) **新增**：recordInteractionEventTs('click', 100) + recordVisualFrameTs(180) → visual.samples 含 80
- TC-O-14b (P0) **v3 新增（解 MAJOR M'2）**：多事件配对 — recordInteractionEventTs('click', 100) → recordInteractionEventTs('click', 150) → recordVisualFrameTs(180) → recordVisualFrameTs(220) → visual.samples 含 [80, 70]（FIFO 队首匹配）；后续 recordVisualFrameTs(300) 时队列已空，不再 record
- TC-O-14 (P0) **新增**：getAnimationMetrics 返回结构正确（interaction/visual 两组）
- TC-O-15 (P0) **新增**：getAnimationDebug 字段齐全
- TC-O-16 (P0) **新增**：dispose() 后 applyTo 不抛（state 标 disposed → 早 return）
- TC-O-17 (P1) 1000 帧 applyTo 总 ms < 500（mean < 0.5ms/call —— 对应 NFR-1）
- TC-O-18 (P1) flag perlin=off → ParamAngleX 收到的 ADD delta 不含 perlin 分量

### 4.11 Live2DCanvas 集成测试（@testing-library + mock）
- TC-L-01 (P0) 挂载/卸载不泄漏 canvas（querySelectorAll 'canvas[data-pet-live2d]' 回零）
- TC-L-02 (P0) **新增**：挂载/卸载 hit-zone div 同步出现/消失
- TC-L-03 (P0) **新增**：window pointermove → overlay.setGazeTarget 被调用（spy）
- TC-L-04 (P0) **新增**：hit-zone pointerEnter → overlay.pulseInteraction('hover_enter')
- TC-L-05 (P1) HMR 模拟（dispose 老 overlay → 新 overlay）— 监听不重复（pointermove 触发后 spy.callCount = 1 而非 2）
- TC-L-06 (P0) **v3 新增（解 BLOCKER B'1）**：window resize 事件触发 → hit-zone div style.left/top/width/height 和 overlay.setFaceCenter 同源更新（spy on setFaceCenter 验证调用次数 + 参数与 hit-zone DOM 几何一致）

### 4.12 端到端 wire 测试（v2 新增 — 解 Round1 漏项）
> 用真实 PetStateMachine + AnimationOverlay + stub Live2DCanvas
- TC-E2E-01 (P0) 注入 supervisor red alert → PetStateMachine.tick → result.state_changed=true → App 调 setMotionTagPool(['slow','special'], force=true) → motionPlayer 立刻收到 idx ∈ {标了 slow 或 special 的 idx}
- TC-E2E-02 (P0) state 从 working 转 worried → motion 切换的 idx 在第一时间属于 slow/medium 子集（而非 fast）
- TC-E2E-03 (P1) localStorage 完全空 → 端到端不抛、走 PetStateMachine 现有行为

### 4.13 PetStateMachine 联动
- TC-PSM-01 (P0) STATE_CONFIG.worried.motion_tag_pool === ['slow','medium']
- TC-PSM-02 (P0) tick 返回 motion 字段包含 motion_tag_pool

---

## 5. 测试基础设施

### 5.1 时钟 / 随机 utils
```ts
// pet-anim/__tests__/_helpers.ts
export function fakeRng(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s + 0x6D2B79F5) | 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function fakeClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms: number) => { t += ms; } };
}

export function box_muller(rng: () => number): number {
  const u1 = Math.max(rng(), 1e-12);
  const u2 = rng();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}
```

### 5.2 Stub Live2D coreModel（v2 修订）
```ts
// pet-anim/__tests__/_stubModel.ts
export interface StubLog { name: string; op: 'set'|'add'|'get'; v?: number; idx: number }

export function makeStubCoreModel(
  params: string[],
  opts?: { withAdd?: boolean }   // v2: 控制是否模拟 add API 存在
) {
  const idx = new Map(params.map((p, i) => [p, i]));
  const vals = new Array(params.length).fill(0);
  const log: StubLog[] = [];
  const core: any = {
    getParameterIndex: (n: string) => idx.get(n) ?? -1,
    getParameterValueByIndex: (i: number) => {
      log.push({ name: params[i], op: 'get', idx: i });
      return vals[i] ?? 0;
    },
    setParameterValueByIndex: (i: number, v: number) => {
      log.push({ name: params[i], op: 'set', v, idx: i });
      vals[i] = v;
    },
  };
  if (opts?.withAdd !== false) {
    core.addParameterValueByIndex = (i: number, v: number) => {
      log.push({ name: params[i], op: 'add', v, idx: i });
      vals[i] += v;
    };
  }
  return {
    log,
    coreModel: core,
    snapshot: () => Object.fromEntries(params.map((p, i) => [p, vals[i]])),
  };
}
```

### 5.3 Window event stub（v2 新增）
```ts
// pet-anim/__tests__/_windowEvents.ts
export function makeWindowStub() {
  const listeners: Record<string, Function[]> = {};
  return {
    addEventListener: (type: string, fn: Function) => {
      (listeners[type] ||= []).push(fn);
    },
    removeEventListener: (type: string, fn: Function) => {
      const arr = listeners[type] || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    dispatch: (type: string, payload: any) => {
      (listeners[type] || []).forEach((fn) => fn(payload));
    },
    counts: () => Object.fromEntries(
      Object.entries(listeners).map(([k, v]) => [k, v.length])
    ),
  };
}
```

### 5.4 vitest 配置追加（vitest.config.ts）
```ts
test: {
  environment: 'jsdom',
  setupFiles: ['./src/pet-anim/__tests__/_setup.ts'], // clear localStorage
  coverage: {
    provider: 'v8',
    include: ['src/pet-anim/**', 'src/pet-state/**'],
    thresholds: { lines: 80, branches: 70 },
  },
},
```

`_setup.ts`：
```ts
import { beforeEach } from 'vitest';
beforeEach(() => { try { localStorage.clear(); } catch {} });
```

### 5.5 npm scripts
```json
"test:anim": "vitest run src/pet-anim",
"test:anim:cov": "vitest run src/pet-anim --coverage",
"test:e2e-wire": "vitest run src/pet-anim/__tests__/e2e_wire.test.ts"
```

---

## 6. Mock 策略汇总

| 对象 | 怎么 mock |
|---|---|
| `Date.now` / `performance.now` | **不 mock**；通过模块参数传 `now_t` |
| `Math.random` | 不 mock 全局；模块接受 `rng` opt |
| `localStorage` | jsdom 自带 + `_setup.ts` 每次 clear |
| pixi-live2d-display Live2DModel | `vi.mock('pixi-live2d-display/cubism4')` 返回 stub class |
| PIXI.Application | `vi.mock('pixi.js')` 返回 stub |
| pointer events | jsdom `dispatchEvent(new PointerEvent(...))` 或 `_windowEvents.ts` |
| HMR (`import.meta.hot`) | TC-L-05 通过手动调 overlay.dispose() + 新建 overlay 模拟 |

---

## 7. CI 入口

```bash
pnpm tsc --noEmit
pnpm lint
pnpm test:anim:cov   # 覆盖率门控
pnpm test:e2e-wire
```

CI 必须全过才能合并。GOAL 执行阶段 TDD 红绿循环也跑这套。

---

## 8. 不在测试范围内（手测覆盖）

| 项 | 原因 | 由谁覆盖 |
|---|---|---|
| 视觉"看起来活了" | 主观 | ManualTest CASE-BLIND-01 |
| 多显示器坐标 | jsdom 无法模拟 | ManualTest CASE-G-04 |
| Tauri 透明窗 pointer 穿透 | 仅运行时可验 | ManualTest CASE-PR-05 + Day-0 P3/P4 |
| GPU/CPU 占用 | 仅运行时 | ManualTest CASE-PERF-01/02 |
| FPS ≥ 28 | RAF jsdom 不真实 | ManualTest CASE-PERF-01 |
| applyTo ms/call ≤ 0.5ms | 单测仅总和；真实负载需 dev 实测 | ManualTest CASE-PERF-03 |

---

## 9. TDD 流程指引

1. **D0**：跑 Day-0 探针 §0；输出 evidence/round-0/probes.md
2. **红**：写 P0 case → 跑 → 应失败
3. **绿**：实现该 FR；P0 全过；不写 P1
4. **重构**：清理 + 加 P1；全测试不回归
5. **下 FR**：重复 2-4

**收口 checklist**（每 FR）：
- [ ] 该模块 P0 + P1 全过
- [ ] coverage lines ≥ 80% / branches ≥ 70%
- [ ] `pnpm tsc --noEmit` 无新错
- [ ] applyTo 涉及修改时跑 `test:anim:cov` 截图入 evidence/round-N/<FR>.png
- [ ] PRD §3 该 FR 验收行人工再核对一次

---

## 10. 实现注意点（v2 扩充）

1. **Live2D 参数 ADD vs SET**：见 §3.6 顺序；Day-0 P1 探针决定 fallback 路径
2. **pixi-live2d-display 版本 pin**：在 package.json 加 `"resolutions": { "pixi-live2d-display": "x.y.z" }`（lock 至 D0 当前版本）；或 pnpm `overrides`
3. **coreModel 路径回退**：`model.internalModel.coreModel`；若 undefined 尝试 `model.internalModel._model`（pixi-live2d-display 版本差异）；都 undefined 则 applyTo 直接 return + console.warn 一次（init 时）
4. **pointer event 在透明像素**：v1 接受 hit-zone bbox 矩形（含部分透明像素），v2 alpha hit-test
5. **performance.now() 来源**：必须从 RAF 回调的 `timestamp` 参数传入，不要在 overlay 内部读
6. **vitest Windows 路径**：测试 import 用相对路径，不用绝对
7. **localStorage 在 Tauri WebView2**：与 Chrome 一致
8. **HMR safety**：`import.meta.hot?.dispose(() => overlay.dispose())` 在 AnimationOverlay 所在模块顶层

---

## 11. 修订日志

### v3（Round-2 评审反馈）
- **§0** Day-0 探针强制 `import.meta.env.DEV` 守护 + 清理 checklist（解 MAJOR M'3）
- **§2.1** Perlin frequency 默认 0.3 Hz 明确单位（解 MINOR m'5）
- **§2.7** pointerReaction.ReactorState 重命名（删除 'hovering'，独立 is_hovering boolean）；transition table 改三元组 cell（解 MAJOR M'1）
- **§2.10** AnimationOverlay.setGazeTarget 加 now_t 参数与 PRD/Manual 一致（解一致性 #1）
- **§3.4** gaze 返回值 `eye_yaw_norm`/`eye_pitch_norm` 明确单位转换（解 MINOR m'2）
- **§3.6** step 4 公式标注 norm 已归一化（消除 step 4 单位混淆）
- **§3.8** 新增 Latency Pairing Rule + cap=20（解 MAJOR M'2）
- **§4.4 TC-G-04** 阈值 16°→14° 保守余量（解 MINOR m'3）
- **§4.7 TC-PR-09/10** 新增 hover ⨯ pulse 互斥 case（解 MAJOR M'1 测试覆盖）
- **§4.10 TC-O-14b** 多事件配对测试（解 MAJOR M'2 测试覆盖）
- **§4.10 TC-O-09** 加 motionPlayer('TapBody') 调用断言（解一致性 #4）
- **§4.11 TC-L-06** window resize → 同源双写测试（解 BLOCKER B'1 测试覆盖）

### v2 修订日志

- **§0** 新增 Day-0 4 个探针（解 BLOCKER B2 + 实战 #2/#3/#4）
- **§2.2** blink 公式补 mu 修正（解 BLOCKER B4）
- **§2.4** gaze 接 face_radius_css 注入（解 MAJOR M2）
- **§2.5/§2.6** motion 拆 picker + scheduler（解 MAJOR M1）
- **§2.7** pointerReaction 新增 transition table + ReactorState 枚举（解 MAJOR M6）
- **§2.10** AnimationOverlay 新接口：setStateBaseHeadTilt / pulseHeadTiltDelta（解 MAJOR M3）、setFaceCenter（解 MAJOR M2）、motionLabelsLoader 注入（解 MAJOR M10）、recordInteractionEventTs / recordVisualFrameTs（解 BLOCKER B3）、dispose（解漏项"HMR"）
- **§3.6** 写入顺序明确 SET / ADD / MULTIPLY 三种语义（解 BLOCKER B2）
- **§3.7** 新增失败模式 fallback 表
- **§4.10** overlay.test 大幅扩充 18 个 TC（含 fallback / 子集兜底 / dispose）
- **§4.11** 新增 TC-L-02/03/04/05（hit-zone / window pointermove 端到端）
- **§4.12** 新增端到端 wire 测试（解漏项"e2e wire"）
- **§5.2** stubModel 加 `withAdd?: boolean`（测 fallback）
- **§5.3** 新增 window event stub
- **§10** 增补 pixi 版本 pin / HMR safety 提示
