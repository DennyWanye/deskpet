# PRD — Pet Animation UX v1（"活感"基线）

| 项 | 值 |
|---|---|
| 作者 | 用户（PO） + Claude（架构师代笔） |
| 日期 | 2026-05-24 |
| 版本 | **v3**（应用 Round-2 架构师评审反馈） |
| 状态 | Final — 可进入 GOAL 实施阶段（WITH-FIXES 已落） |
| 目标 Sprint | 单 sprint (~5 天)；后续 v2 处理 viseme lipsync + 静默档 + 表情语义锁定 |
| 关联调研 | 本目录上游产出（5 章节业界研究） |
| 关联 followup | `plans/2026-05-14-supervisor-iteration.md`（motion pool 硬编码痛点） |
| v2→v3 变化 | §6.0 hit-zone 自适应（ResizeObserver + model scale 同步）；§6.1 setGazeTarget 加 now_t；NFR-6 明确 DOMHighResTimeStamp 同时钟基；§FR-6 三连击行为明确；AC-8 与 Manual PASS 标准统一；新增 Latency Pairing Rule（接口契约 §6.8） |

---

## 1. 背景与问题陈述

当前 deskpet 使用 Live2D Cubism 4 渲染 Hiyori 模型，状态机 `PetStateMachine`（[PetStateMachine.ts:148](tauri-app/src/pet-state/PetStateMachine.ts:148)）已经能根据 supervisor severity 在 idle/working/worried/alert/intervening 五态间切换，并通过 `setBlinkRate`/`setHeadTilt` 给到 Live2D（[Live2DCanvas.tsx:100-108](tauri-app/src/components/Live2DCanvas.tsx:100)）。

但是**用户旁观体感**还是"一张会切动画的图"，问题集中在 5 点：

1. **idle 循环感强** — 10 个 Idle motion 30~60s 轮转，姿态本身没有低频扰动
2. **眨眼是恒频方波** — `Live2DCanvas.tsx:267-281` 用 `timestamp % period` 计算，节律不自然
3. **眼神不动** — 没有 micro-saccade，没有视线追随光标
4. **鼠标交互几乎无反馈** — Hover/click/double-click/drag 都不会触发动画
5. **motion_pool 标签未消费** — HiyoriMotionTuner 已存 fast/medium/slow 标签，但 `STATE_CONFIG.motion_pool` 全是 `["Idle"]`

业界共识（详见调研报告）：**三层动画栈是分水岭**。本 PRD 聚焦"底层 + 视线 + 鼠标事件 + 标签消费"。

## 2. 目标 / 非目标

### 2.1 目标（In Scope）
- **G1**：用户旁观 5 分钟，能明显感到 Hiyori "活着不是 GIF"
- **G2**：用户 hover / click / double-click 角色身体时，事件→首次参数写入 ≤50ms、首次可见画面 ≤200ms
- **G3**：眼睛能在 ±20° 锥角内追随光标，带死区和阻尼，不"恐怖谷"
- **G4**：HiyoriMotionTuner 标签真正驱动状态机选 motion（worried 用 slow、working 用 fast）
- **G5**：所有新行为通过 feature flag 一键关闭，零回归风险

### 2.2 非目标（Out of Scope，留给 v2/v3）
- ❌ Viseme 时间戳级别的 TTS lipsync（沿用现有音量包络 `mouthOpenY`）
- ❌ 专注感知静默档（全屏检测、打字密度）
- ❌ 情感分类锁定（LLM 回复情感分析 → 表情）
- ❌ 拖拽物理 / 攀爬屏幕边缘 / 被窗口挤压
- ❌ 长时无操作 / 用户回归 / milestone 限定动画
- ❌ 补 Hiyori Expression 资产（rig expression3.json）
- ❌ 形状级（alpha）hit-test
- ❌ Cursor 进入桌宠窗外的追随

### 2.3 用户故事
- **US-1**：常驻屏幕的程序员希望"活感"不打扰工作
- **US-2**：鼠标移到桌宠脸上希望眼睛"看到我"，但不过激
- **US-3**：点击 / 双击希望立即有反馈
- **US-4**：标过 fast/medium/slow 的进阶用户希望标注真的有用

---

## 3. 功能需求（FR）

### FR-1 Perlin Noise 微动 baseline

| 维度 | 内容 |
|---|---|
| **触发** | 模型加载完成后每帧 |
| **行为** | 1D Perlin noise 叠加到 ParamAngleX / ParamAngleY / ParamBodyAngleX |
| **参数** | 各维独立 seed（1337/2741/4099）；时间缩放 `t * 0.0003`；幅度 **可调候选 ±1.5° / ±2° / ±3°**（v2 修订：见 §10 OQ-3 已 A/B/C 三档而非主观二选） |
| **写入方式** | **ADD**（不是 SET）— 累加到 motion3 写完的基线值；coreModel 若无 `addParameterValueByIndex` → fallback `set(idx, get(idx) + delta)` |
| **验收** | (a) 开启后 30s 内 ParamAngleX 序列方差 > 0；(b) 频谱主峰 0.1–0.3 Hz；(c) flag toggle 视觉可辨 |
| **降级** | flag off → 跳过；模型缺参 → silent skip |
| **冲突处理** | 与 motion3 ADD 关系；不动 ParamAngleZ（head_tilt 专属） |

### FR-2 对数正态分布 blink

| 维度 | 内容 |
|---|---|
| **触发** | 替换 `Live2DCanvas.tsx:267-281` 方波 blink |
| **行为** | 对数正态采样下一次 blink 间隔；闭眼用半正弦曲线 |
| **参数** | **mu = ln(1/blink_hz) - sigma²/2**（v2 修订：抵消对数正态均值偏移 8.3%）、sigma=0.4；close_duration_ms=100±20；双眨概率 10% |
| **写入方式** | **MULTIPLY** ParamEyeLOpen / ParamEyeROpen（`set(idx, get(idx) * eye_open)`）— 保留 motion3 自带眨眼基线，叠加 supervisor blink |
| **验收** | (a) 100 次间隔均值 ∈ `[0.85, 1.15] * 1/blink_hz`（v2 收紧）；(b) 相邻间隔方差 > 0；(c) 闭眼时长 100±20ms |
| **降级** | flag off → 退方波；blink_hz=0 → 不写 |

### FR-3 Micro-saccade

| 维度 | 内容 |
|---|---|
| **触发** | 模型加载后每帧；与 FR-4 视线叠加 |
| **行为** | 在当前 ParamEyeBallX/Y 注视点附近 1–3° 微偏移；偏移 30–60 ms 后回到注视点 |
| **参数** | 幅度 [-0.04, +0.04]（**v2 注：D0 探针确认 Hiyori EyeBallX/Y 范围后按实际范围 × 4% 缩放**）；触发间隔 Uniform(500, 2000) ms |
| **写入方式** | ADD 到 ParamEyeBallX/Y（叠加在 FR-4 gaze 之后） |
| **验收** | (a) 30s 内 ≥20 次（v2 修订：与 1Hz 调研一致）；(b) 结束后 offset=0；(c) 与 FR-4 共存不抖动 |
| **降级** | flag off / 模型缺 EyeBallX/Y → skip；**v1 Plan B**：若 NFR-1 性能不达标，saccade 作为第一弃车保帅候选 |

### FR-4 鼠标视线追随

| 维度 | 内容 |
|---|---|
| **触发** | **`window` 级 pointermove**（v2 修订：不挂 `<img>`，避开 §6.0 hit-through 矛盾） |
| **行为** | 鼠标 client coords → ParamAngleX/Y（头部 0.4×）+ ParamEyeBallX/Y（眼球 1.0×） |
| **关键参数** | (a) face_radius_css 由 Live2DCanvas 在 model 加载完成后传入 = `model.width * scale / 2`（v2 修订：动态而非写死 200）；(b) 水平 ±20° / 垂直 ±15° clamp；(c) 5° 死区；(d) 一阶低通 α=0.15；(e) idle_recenter 10s |
| **写入方式** | ADD 到 ParamAngleX/Y + ParamEyeBallX/Y |
| **验收** | (a) 鼠标左上→右下慢扫眼球平滑跟随；(b) 静止 2s 视线不漂；(c) 死区内不更新；(d) 超过 ±20° clamp；(e) **客观断言**：鼠标在左半屏 console 输出 `__deskpet_anim_debug.gaze_smoothed_yaw < 0`（v2 修订：避免主观验收陷阱） |
| **降级** | flag off → 不监听；鼠标 >10s 无活动 → 视线缓慢回正 + saccade |

### FR-5 Motion Pool 标签消费

| 维度 | 内容 |
|---|---|
| **触发** | PetStateMachine 进入 working/worried/alert/intervening 时，App 调 `setMotionTagPool(tags, opts)` |
| **行为** | 读 motion labels → 按状态选子集 → 通过 `motionPlayer('Idle', idx)` 播放 |
| **状态-标签映射** | working→['fast','medium'] / worried→['slow','medium'] / alert→['slow','special'] / intervening→['fast'] / idle→[]（不强制） |
| **关键变化（v2 修订 M4）** | `setMotionTagPool(tags, { force_switch_now })`；PetStateMachine `state_changed=true` 时传 true → 立即切，不等周期；其他时候 false → 走 round-robin |
| **子集兜底（v2 修订 OQ-4）** | candidates.size < 2 时，自动扩展（先并入 medium，再并入全集），保证不会"两个 motion 反复" |
| **labels source** | 由外部注入 `motionLabelsLoader: () => Record<MotionTag, number[]> \| null`，默认 = `get_calibrated_motion_pools`（PetStateMachine.ts 已实现，避免双解析漂移） |
| **验收** | (a) worried 实际播放 idx ∈ slow 子集；(b) 切换有 ±30% jitter；(c) 连续 3 次不重复（candidates≥3）；(d) state_changed 时立即切 |
| **降级** | flag off / 标签为空 → 走 PetStateMachine 现有逻辑 |

### FR-6 鼠标交互反馈（v2 修订：明确 hit-zone 路由）

| 维度 | 内容 |
|---|---|
| **触发** | **`<div data-pet-hitzone>` 覆盖角色脸+躯干 bbox**（v2 新增；非整 `<img>`）；详见 §6.0 |
| **行为** | hover：headTilt transient +3° + blink +0.1Hz 200ms；click：TapBody + headTilt 摆动；double click（≤300ms）：TapBody + ParamAngleZ ±10° 抖动 + blink burst |
| **互斥语义** | hover 期间 click 不破坏 hover；click 反应结束后仍 hover；double click 触发后清空 pending single；**v3 新增**：double pulse 期间（≤400ms）的第三次 click 被吞为"已设计的安静窗口"（避免与 double 反应叠加爆），用户视角应感觉不到（pulse 短），列入设计预期非 bug |
| **写入方式** | ParamAngleZ 改用 SET(state_base + transient_delta)（v2 修订 M3：避免和 STATE_CONFIG.head_tilt 累加爆） |
| **验收** | (a) click → setHeadTilt 调用 ≤50ms（interaction）；(b) click → 首次可见画面 ≤200ms（visual）；(c) double click 不触发两次单击 |
| **降级** | flag off → hit-zone div 不渲染；当 §6.0 Day-0 探针验证失败 → FR-6 整体降级为"按 Alt 暂关 ignore_cursor_events 才能点" |

### FR-7 反应延迟 SLO + 监控（v2 修订：拆双指标）

| 维度 | 内容 |
|---|---|
| **触发** | 持续运行 |
| **行为** | 两条独立指标，分别埋点 |
| **指标 A — interaction_latency_ms** | pointer event → 首次 `overlay.applyTo` 写参数 → 完成；SLO **p50≤30ms / p95≤50ms / max≤120ms** |
| **指标 B — visual_latency_ms** | pointer event → 下一次 `toBlob` 回调成功 `imgRef.current.src` swap；SLO **p50≤150ms / p95≤300ms / max≤600ms** |
| **暴露** | `window.__deskpet_anim_metrics()` → `{ interaction: {p50,p95,max,samples}, visual: {p50,p95,max,samples} }` |
| **额外 debug** | `window.__deskpet_anim_debug` 每帧更新（gaze_target_yaw/gaze_smoothed_yaw/last_input_age/state/current_motion_idx）— 供 ManualTest 客观断言 |
| **验收** | 手测 CASE-MET-01 / CASE-MET-02 通过 |
| **降级** | flag off → 不埋点 |

---

## 4. 非功能需求（NFR）

| 编号 | 类别 | 要求 |
|---|---|---|
| NFR-1 | 性能 | 启用 v1 全部 FR 后 FPS ≥ 28；**v2 收紧**：`overlay.applyTo` 单帧 ms/call ≤ 0.5ms（不含 toBlob）；CPU 增量 ≤ 5%；内存增量 ≤ 30MB |
| NFR-2 | 可降级 | 见下方 **flag 对照表** |
| NFR-3 | 兼容性 | Cubism 4 模型缺任意参数时单维度 silent skip；**v2 新增**：`pixi-live2d-display` 在 package.json 用 `"resolutions"`/`pnpm overrides` 锁版本（详见 §9 第 2 坑） |
| NFR-4 | 可测试 | 全部纯函数无 DOM 依赖；时钟/随机注入 |
| NFR-5 | 可观测/声噪 | **v2 修订**：render loop **每 100 帧最多 1 行 console**；其他日志走 `__deskpet_anim_debug`；新增/修改的 console.warn 不超过 4 条 |
| NFR-6 | 时钟可注入 | `now_t` 必须由调用方传入；模块内部禁止读 Date.now/performance.now。**v3 明确**：所有 now_t 必须是同一时钟基 — **DOMHighResTimeStamp**（即 `performance.now()` 与 `event.timeStamp` 同源；RAF callback 参数也同源）。混用 Date.now 会导致 latency 算成负数 |
| NFR-7 | 零回归 | flag 全关时与 commit `0f69254` 行为一致 |

### NFR-2 Feature Flag 对照表（v2 新增）

| Flag Key | 默认 | 影响 FR | 备注 |
|---|---|---|---|
| `deskpet_animation_v1` | `on` | 总开关，off 时所有 FR 全关；individual flag 不能覆盖 |
| `deskpet_anim_perlin` | `on` | FR-1 | |
| `deskpet_anim_blink` | `on` | FR-2 | off 时退回方波 |
| `deskpet_anim_saccade` | `on` | FR-3 | Plan B 第一弃车保帅候选 |
| `deskpet_anim_gaze` | `on` | FR-4 | |
| `deskpet_anim_motionpool` | `on` | FR-5 | off 时 motion 走 PetStateMachine 现有 ["Idle"] |
| `deskpet_anim_pointer` | `on` | FR-6 | off 时 hit-zone div 不渲染 |

**all=off 语义**（v2 明确）：`deskpet_animation_v1=off` 是 hard kill，individual on 不生效。理由：排障时易复现"已知好"状态。

---

## 5. 触发-动画 对照表

| 事件 | 优先级 | 动画 | FR |
|---|---|---|---|
| 每帧（无用户输入） | bg | Perlin + 对数正态 blink + saccade | FR-1/2/3 |
| window pointermove | low | 视线追随（不依赖 hit-zone） | FR-4 |
| hit-zone pointerenter | mid | headTilt transient +3°、blink +0.1Hz | FR-6 |
| hit-zone click | high | TapBody + headTilt 摆动 | FR-6 |
| hit-zone dblclick (≤300ms) | force | TapBody + ParamAngleZ 抖动 + blink burst | FR-6 |
| 状态机进入 working | mid | tag=['fast','medium']，force_switch_now=true | FR-5 |
| 状态机进入 worried | mid | tag=['slow','medium']，base head_tilt=-5° | FR-5 |
| 状态机进入 alert | high | tag=['slow','special']，tap_on_entry | FR-5 |
| 状态机进入 intervening | force | tag=['fast']，tap_on_entry | FR-5 |

---

## 6. 接口契约

### 6.0 窗口/事件路由总体设计（v2 新增 — 解 BLOCKER B1）

**核心矛盾**：现行 Live2DCanvas `<img pointerEvents:"none">` + Tauri `set_ignore_cursor_events(true)` 让整个桌宠窗"穿透点击"。FR-4（gaze）想知道鼠标位置 + FR-6（hover/click）想吃 click + 桌面其他窗口仍可点 — 三者不能同时由"`<img>` 上挂 auto"实现。

**v1 接案（v3 拍板）**：

1. **保持** Tauri `set_ignore_cursor_events(true)` 现状（桌宠窗整体穿透）。
2. **新增** `<div data-pet-hitzone>`，仅覆盖角色脸 + 躯干 bounding box；**v3 修订**：bbox 由 Live2DCanvas **自适应**计算（详见下方），订阅 `ResizeObserver(window)` + model scale 变化 + StrictMode 重建时**同步重算**。
3. **hit-zone div 自身** `pointer-events: auto`（局部不穿透）；其余区域 `pointer-events: none` 保持穿透；hit-zone z-index 必须**低于** HiyoriMotionTuner / 其他 UI 面板，避免遮挡。
4. **gaze 的 `pointermove` 监听挂在 `window` 上**（而非 hit-zone）— 即使 ignore_cursor_events=true，WebView2 内 JS 仍能收到 window-level pointermove 事件（Day-0 探针 P3 验证）。
5. **Tauri 主进程**保持现状，**不**调 `set_ignore_cursor_events(false)`。

**Day-0 探针失败时的降级路径**：

- P3 失败（window pointermove 收不到）→ FR-4 降级为"hit-zone 内 pointermove 触发 gaze"（鼠标必须在脸区域才追随）；**ManualTest 必须跑对应 CASE-G-05-FALLBACK 验证**。
- P4 失败（hit-zone click 穿透或被吃）→ FR-6 降级为"按 Alt 暂关 ignore_cursor_events 才能点"；**ManualTest 必须跑对应 CASE-PR-FALLBACK 验证**。

**hit-zone 自适应坐标系**（v3 修订 — 解 BLOCKER B'1）：

Live2DCanvas 维护一个 `face_frame_state: { left, top, width, height, face_center_x, face_center_y, face_radius_css }`，在以下时机**同步重算**并**双写**：
1. Live2D model load 完成（首次）
2. window `resize` 事件（节流 100ms）
3. ResizeObserver 触发的 Live2DCanvas 容器尺寸变化
4. 未来：用户从 Tuner 改 model scale 时（通过 prop 变化）

**单一计算源**（任何时候都用这一组）：
```ts
function computeFaceFrame(petWidth, innerHeight, innerWidth, modelScaleFactor = 1) {
  const left   = (innerWidth - petWidth) + petWidth * 0.25;
  const width  = petWidth * 0.5 * modelScaleFactor;
  const top    = innerHeight * 0.20;
  const height = innerHeight * 0.60 * modelScaleFactor;
  return {
    left, top, width, height,
    face_center_x: left + width / 2,
    face_center_y: top + height * 0.30,        // 脸约在 bbox 上 30%
    face_radius_css: Math.min(width, height) * 0.5,
  };
}
```

**双写**（必须同源）：
1. hit-zone `<div>` 的 `style.left/top/width/height`
2. `overlay.setFaceCenter(face_center_x, face_center_y, face_radius_css)`

**gaze coordinate mapping**：`(clientX - face_center_x, clientY - face_center_y)`，对 `face_radius_css` 归一化后 `atan2`。多显示器副屏负坐标安全（atan2 自然处理符号）。

### 6.1 Live2DHandle（v2 修订接口）
```ts
export interface Live2DHandle {
  // === 现有，保留 ===
  setExpression(name: string): void;
  playMotion(group: string): void;
  setBlinkRate(hz: number): void;
  setHeadTilt(degrees: number): void;   // v2 含义：state base head tilt（持续值）
  setIdleSubset(motionIds: string[]): void;

  // === v1 新增 ===
  setMotionTagPool(
    tags: Array<'fast'|'medium'|'slow'|'special'>,
    opts: { force_switch_now: boolean },
    now_t: number,                            // v3 新增（DOMHighResTimeStamp）
  ): void;
  /** v3 修订：3 参数；now_t 来自 pointermove event.timeStamp 或 RAF timestamp（同 DOMHighResTimeStamp 时钟基） */
  setGazeTarget(clientX: number, clientY: number, now_t: number): void;
  clearGazeTarget(now_t: number): void;
  pulseInteraction(kind: 'hover_enter'|'hover_leave'|'click'|'double_click'): void;
  getAnimationMetrics(): {
    interaction: { p50: number; p95: number; max: number; samples: number[] };
    visual: { p50: number; p95: number; max: number; samples: number[] };
  };
  // v2 新增（用于 ManualTest 客观断言）
  getAnimationDebug(): {
    gaze_target_yaw: number; gaze_smoothed_yaw: number;
    last_input_age_ms: number; current_state: string;
    current_motion_idx: number | null;
  };
}
```

### 6.2 PetMotionConfig 扩字段
```ts
export interface PetMotionConfig {
  motion_pool: string[];           // 保留兼容
  switch_period_seconds: number;
  blink_hz: number;
  head_tilt: number;
  tap_on_entry: boolean;
  motion_tag_pool?: Array<'fast'|'medium'|'slow'|'special'>;
}
```

填值：
```
idle:        motion_tag_pool: undefined
working:     ['fast', 'medium']
worried:     ['slow', 'medium']
alert:       ['slow', 'special']
intervening: ['fast']
```

### 6.3 新增模块（v2 修订 — 拆 MotionScheduler）

| 文件 | 职责 |
|---|---|
| `tauri-app/src/pet-anim/perlinNoise.ts` | 1D Perlin 纯函数 + seedable |
| `tauri-app/src/pet-anim/blinkScheduler.ts` | 下一次眨眼 / 当前 eyeOpen(t) |
| `tauri-app/src/pet-anim/saccadeScheduler.ts` | 当前 EyeBallX/Y offset(t) |
| `tauri-app/src/pet-anim/gazeTracking.ts` | client coords → angle，clamp/死区/低通 |
| `tauri-app/src/pet-anim/motionPicker.ts` | 子集 + round-robin idx 选择（纯函数） |
| `tauri-app/src/pet-anim/motionScheduler.ts` | **v2 新增**：调度时钟（period+jitter+force_switch），与 picker 分离 |
| `tauri-app/src/pet-anim/pointerReaction.ts` | hover/click/double-click 状态机（带 transition table） |
| `tauri-app/src/pet-anim/featureFlags.ts` | localStorage flag |
| `tauri-app/src/pet-anim/metricsRing.ts` | 100-slot ring + p50/p95（双指标各一） |
| `tauri-app/src/pet-anim/index.ts` | `AnimationOverlay` 聚合类 + frame-level applyTo |

### 6.4 修改 Live2DCanvas.tsx
- render loop 内 supervisor 参数三段逻辑 → 统一 `overlay.applyTo(coreModel, timestamp)`
- 新增 hit-zone `<div data-pet-hitzone>` 子元素（pointer-events: auto），监听 enter/leave/click/dblclick → 转 `overlay.pulseInteraction`
- 新增 `window.addEventListener('pointermove')` 转 `overlay.setGazeTarget(clientX, clientY)`；window.blur 时 `clearGazeTarget`
- model 加载完成回调内 `overlay.setFaceCenter(cx, cy, face_radius_css)` 一次性告知坐标
- pet `<img>` 仍 `pointerEvents: "none"`（v2 修正：不改 img，hit-zone 在另一个 div）
- visual_latency 在 toBlob 回调内记录：`metrics.visual.record(performance.now() - last_event_ts)`

### 6.5 修改 App.tsx
```ts
const result = petStateMachine.tick(input);
if (result.state_changed) {
  liveRef.current?.setMotionTagPool(
    result.motion.motion_tag_pool ?? [],
    { force_switch_now: true }
  );
} else if (/* 周期性 refresh */) {
  liveRef.current?.setMotionTagPool(
    result.motion.motion_tag_pool ?? [],
    { force_switch_now: false }
  );
}
```

### 6.6 生命周期（v2 新增 — 漏项）
- AnimationOverlay 是 **per-Live2DCanvas-instance** singleton，构造在 init() 内、销毁在 cleanupRef 内
- React StrictMode 双挂载时，依赖 Live2DCanvas.tsx:173 已有的 stale canvas purge 同步 purge hit-zone div
- HMR：通过 `import.meta.hot?.dispose` 在模块更新前清理 overlay 监听

### 6.7 LocalStorage Schema 版本号（v2 新增 — 漏项）
- HiyoriMotionTuner 写入时附加 `version: 1` 字段
- AnimationOverlay 读时检查；不匹配 → 当作空，触发引导 UI（v2 不做引导，仅 fallback default）
- **v3 明确**：默认 `motionLabelsLoader = get_calibrated_motion_pools` 内部检查 version 字段；不匹配返 null（与缺失等价）

### 6.8 Latency Pairing Rule（v3 新增 — 解 MAJOR M'2）

`visual_latency` 需要把 pointer event timestamp 配对到下一次"toBlob 回调成功 swap `<img>` src"的帧 timestamp，多事件交叠时必须有明确规则：

**配对算法**（在 AnimationOverlay 内部维护）：
```
clicks_queue: Array<{ kind: 'click'|'double_click', event_ts: number }>  // FIFO

recordInteractionEventTs(kind, event_ts):
  clicks_queue.push({ kind, event_ts })

recordVisualFrameTs(frame_ts):
  if (clicks_queue.length === 0) return     // 没有未匹配事件，忽略
  const head = clicks_queue.shift()         // FIFO — 队首匹配（反映"用户已等的最长时间"）
  visual_metrics.record(frame_ts - head.event_ts)
```

**关键性质**：
- FIFO（队首匹配），不是 LIFO
- 一个 click 事件只能匹配一帧；匹配后从队列移除
- 双击场景：两个 click event 都进队（即使第二个被升级为 double_click，原 event_ts 也保留为单独条目）；下两帧 toBlob 分别匹配它们
- 队列长度安全：UI 应保证 ≤ 10（人不可能 100ms 内点 10 次），用 cap=20 防御性裁剪（超过丢老的）

`interaction_latency` 不需要配对（事件→参数写入是同步路径，直接 record 单条 latency）。

ManualTest CASE-MET-02 必须用 `window.testClickPair()` 跑两次 200ms 间隔 click，验证 visual.samples 含两条不同 latency（且差异 ≈ 200ms）。

---

## 7. 验收标准

| ID | 检查项 | 验收方法 |
|---|---|---|
| AC-1 | 7 个 FR 各自验收行全过 | 单元 + 手测 |
| AC-2 | NFR-1 FPS ≥ 28 + applyTo ms/call ≤ 0.5ms | DevTools / ms bench |
| AC-3 | NFR-7 零回归 | vitest unit + 手测 CASE-REG-01/02 |
| AC-4 | 全部新代码 vitest 覆盖率 ≥ 80% lines / ≥ 70% branches | `pnpm test --coverage` |
| AC-5 | `pnpm tsc --noEmit` 通过 | CI |
| AC-6 | `pnpm lint` 通过 | CI |
| AC-7 | ManualTest 所有 P0 case 截图 + 录屏归档 | `evidence/round-N/` |
| AC-8 | **v3 修订**：开发者 1 周后自盲选 + 1 朋友盲选，**2/2 选 B 为 PASS；1/2 选 B 为 WARN**（记录但不阻断 Sprint） | evidence/blind-test/ |

---

## 8. 里程碑（v2 重排 — 解实战经验第八坑）

| Day | 内容 |
|---|---|
| D0 | **Day-0 探针**（30min–2hr）：P1 验证 `coreModel.addParameterValueByIndex` 存在 + ADD 持久性；P2 验证 Hiyori model3.json 的 ParamEyeBallX/Y 范围；P3 验证 ignore_cursor_events=true 下 window pointermove 是否收得到；P4 验证 hit-zone div 能否在 ignore=true 下吃 click |
| D1 | 9 个 `pet-anim/*` 模块单测红绿（含 motionScheduler 拆出来的）；FR-1/FR-2/FR-3 纯函数完成 |
| D2 | 拼装 AnimationOverlay；接入 Live2DCanvas render loop；FR-1/FR-2/FR-3 视觉验证；NFR-1 ms/call bench |
| D3 | FR-4 视线追随 + FR-5 motion 标签消费（含 force_switch_now 联动）；上下文：D3 不接 pointer 事件，集中处理"无用户输入下的活感"|
| D4 | FR-6 hit-zone + pointerReaction（含 transition table 测试）+ FR-7 双指标埋点；hover 反应若超时砍掉 |
| D5 | feature flag 收尾；手测两轮；evidence 归档；AC-8 录屏对照 |

**Plan B**（NFR-1 不达标）：依次砍 FR-3 saccade → FR-1 Perlin 改幅度 ±1° → FR-6 hover 反应。

---

## 9. 风险与缓解（v2 扩充 8 项实战坑）

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| 1 | toBlob 链路本身就是性能黑洞，FR-1~7 叠加易丢帧到 22-25fps | 高 | 高 | Plan B 砍 FR；NFR-1 加 applyTo ms/call ≤0.5ms 硬上限；CASE-PERF-03 独立 bench applyTo |
| 2 | pixi-live2d-display 大版本升级 internalModel 结构变 | 中 | 高 | package.json lock 至当前版本；Day-0 探针 P1 验证 |
| 3 | Hiyori model3.json ParamEyeBallX/Y 范围 ≠ ±1 | 中 | 中 | Day-0 探针 P2 检查；FR-3 amplitude 按实际范围 ×4% 缩放 |
| 4 | Tauri 透明窗 pointer 在 hit-zone 与 ignore_cursor 之间竞争 | 中 | 高 | Day-0 探针 P3/P4；失败时 FR-6 降级方案（Alt 暂关 ignore） |
| 5 | gaze 验收主观陷阱（方向反了也"看起来对"） | 高 | 中 | FR-4 加客观断言 `__deskpet_anim_debug.gaze_smoothed_yaw` 符号；CASE-G-01 必查 |
| 6 | motion tag 子集 size < 2 时退化为"两个反复" | 高 | 低 | FR-5 子集兜底逻辑：先并 medium 再并全集 |
| 7 | render loop console.warn 声噪冲掉其他 dev 日志 | 中 | 低 | NFR-5 每 100 帧最多 1 行；新增 console.warn ≤ 4 条 |
| 8 | 5 天里 FR-4+FR-5+FR-6 集中 D3-D4，超时 | 高 | 中 | §8 D3/D4 重排；hover 反应砍掉作为时间缓冲；Plan B |
| 9 | 单一 FR-7 SLO 定义自相矛盾 | 解决 | — | v2 拆双指标 interaction/visual |
| 10 | head_tilt 在 STATE_CONFIG（持续）和 FR-6 hover（瞬态）之间累加爆 | 解决 | — | `setHeadTilt` 改 state_base 持续；`pulseHeadTiltDelta` 瞬态；ParamAngleZ = SET(base + transient) |
| 11 | localStorage motion_labels 漂移 / schema 升级 | 低 | 中 | §6.7 加 version 字段；不匹配 silent fallback |
| 12 | HMR 残留 overlay 监听 → 双倍 pointer event 触发 | 中 | 中 | §6.6 HMR dispose 钩子 |
| 13 | StrictMode 双挂载致两个 overlay 实例 | 低 | 中 | per-instance singleton + cleanupRef 配套 |
| 14 | DevTools 打开影响性能测量准确性 | 中 | 低 | CASE-PERF-01/02 明确"关 DevTools 用任务管理器" |
| 15 | hit-zone bbox 写死，窗口缩放/模型变换错位 | 解决 | — | v3 §6.0 ResizeObserver + 同源双写 |
| 16 | visual_latency 多事件配对未明 | 解决 | — | v3 §6.8 FIFO 队首匹配规则 |
| 17 | Day-0 探针代码污染 prod | 中 | 中 | TDD §0 + ManualTest §2 加 `import.meta.env.DEV` 守护 + 完成后 git revert checklist |
| 18 | pointer event timestamp / RAF timestamp / Date.now 时钟基混用 → latency 算负 | 中 | 中 | NFR-6 v3 明确同 DOMHighResTimeStamp 基 |

---

## 10. 开放问题（评审时确认）

- **OQ-1** ✅ 已在 §6.0 拍板（hit-zone + window pointermove）
- **OQ-2**：double-click 阈值 300ms 是否符合用户习惯 → ManualTest CASE-PR-03 用 console eval 精确测
- **OQ-3** ✅ Perlin 幅度 A/B/C 三档（±1.5°/±2°/±3°）— ManualTest CASE-P-04 实测
- **OQ-4** ✅ 已在 FR-5 子集兜底拍板
- **OQ-5**：v1 仅 localStorage flag 不进 UI Settings；v2 再做

---

## 11. 引用

- 调研报告：本会话上游产出
- 现状代码：`tauri-app/src/components/Live2DCanvas.tsx`、`tauri-app/src/pet-state/PetStateMachine.ts`、`tauri-app/src/components/HiyoriMotionTuner.tsx`
- 模型资产：`tauri-app/public/assets/live2d/hiyori/Hiyori.model3.json`（Day-0 探针 P2 检查 Parameters）
- 依赖锁版本：`pixi-live2d-display@<lock-at-current>` （Day-0 D0 写入 package.json）
