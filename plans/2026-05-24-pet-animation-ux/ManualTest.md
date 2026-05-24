# ManualTest — Pet Animation UX v1

| 项 | 值 |
|---|---|
| 关联 PRD | `PRD.md` v2 |
| 关联 TDD | `TDD.md` v2 |
| 版本 | **v3**（应用 Round-2 评审反馈） |
| 环境 | Windows 11 + Tauri dev + Chrome DevTools |
| 准备 | 5min | 全套执行 | 55-70min |
| Evidence | `plans/2026-05-24-pet-animation-ux/evidence/round-N/` |
| v2→v3 变化 | Day-0 完成后加"清理 checklist"；新增 CASE-G-06 window resize 自适应；新增 CASE-G-05-FALLBACK / CASE-PR-FALLBACK 降级路径；新增 CASE-PR-06 Tuner z-index 不冲突；CASE-MET-02 改用 testClickPair 验证 FIFO 配对；AC-8/CASE-BLIND PASS 标准统一 |

---

## 0. 前置准备（每次手测开始前必做）

### 0.1 清理残留进程
```powershell
Get-Process | Where-Object { $_.ProcessName -in @('deskpet','node') } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 0.2 启动后端 + 前端
```powershell
# Term 1
cd G:\projects\deskpet\backend
.\.venv\Scripts\Activate.ps1
python -m deskpet_backend.server

# Term 2
cd G:\projects\deskpet\tauri-app
pnpm tauri dev
```

### 0.3 DevTools helper（v2 修订 — 用 flag 全名对照表，无 bug）
```js
// 粘到 Console 一次
const FLAG_KEYS = {
  all: 'deskpet_animation_v1',
  perlin: 'deskpet_anim_perlin',
  blink: 'deskpet_anim_blink',
  saccade: 'deskpet_anim_saccade',
  gaze: 'deskpet_anim_gaze',
  motionpool: 'deskpet_anim_motionpool',
  pointer: 'deskpet_anim_pointer',
};
window.metrics = () => window.__deskpet_anim_metrics?.();
window.debug = () => window.__deskpet_anim_debug;
window.flagOn = (k) => { localStorage.setItem(FLAG_KEYS[k], 'on'); location.reload(); };
window.flagOff = (k) => { localStorage.setItem(FLAG_KEYS[k], 'off'); location.reload(); };
window.flagAllOff = () => { localStorage.setItem(FLAG_KEYS.all, 'off'); location.reload(); };
window.flagDefault = () => {
  for (const key of Object.values(FLAG_KEYS)) localStorage.removeItem(key);
  location.reload();
};
window.testClickPair = (intervalMs) => {
  const el = document.querySelector('[data-pet-hitzone]');
  if (!el) { console.error('hit-zone not found'); return; }
  el.dispatchEvent(new PointerEvent('click', { bubbles: true, clientX: el.offsetLeft+50, clientY: el.offsetTop+50 }));
  setTimeout(() => el.dispatchEvent(new PointerEvent('click', { bubbles: true, clientX: el.offsetLeft+50, clientY: el.offsetTop+50 })), intervalMs);
};
```

### 0.4 Evidence 目录
```powershell
mkdir -Force plans\2026-05-24-pet-animation-ux\evidence\round-0
mkdir -Force plans\2026-05-24-pet-animation-ux\evidence\round-1
mkdir -Force plans\2026-05-24-pet-animation-ux\evidence\blind-test
```

### 0.5 关闭 DevTools（性能测试时）
所有 `CASE-PERF-*` 前必须**关闭 DevTools**，否则数据不可信。改用 Windows 任务管理器（Ctrl+Shift+Esc）查桌宠进程 CPU / 内存。

---

## 1. 测试矩阵总览

| Case ID | 涉及 FR | 类型 | 耗时 |
|---|---|---|---|
| **CASE-D0-01..04** | Day-0 探针 | DevTools + grep | 10min |
| CASE-P-01..04 | FR-1 Perlin (含 A/B/C 三档) | 视觉 | 6min |
| CASE-B-01..02 | FR-2 Blink | 视觉 + 测时 | 5min |
| CASE-S-01..02 | FR-3 Saccade | 视觉 | 3min |
| CASE-G-01..05 | FR-4 Gaze（含负坐标） | 多场景 | 12min |
| CASE-MP-01..04 | FR-5 Motion Pool（含 force_switch） | 状态触发 | 10min |
| CASE-PR-01..05 | FR-6 Pointer（hit-zone） | 鼠标 | 8min |
| CASE-MET-01..03 | FR-7 双指标 | Console | 5min |
| CASE-PERF-01..03 | NFR-1（含 applyTo bench） | 任务管理器 | 8min |
| CASE-REG-01..02 | NFR-7 零回归 | 对比 | 5min |
| CASE-HMR-01 | HMR 不破坏 overlay | 修改源码 | 3min |
| CASE-COLD-01 | 冷启动安全 | 全程观察 | 2min |
| CASE-BLIND-01 | AC-8 盲测 | 1+1 人 | 10-20min |

---

## 2. CASE-D0：Day-0 探针（必须最先执行）

> 这 4 个 case 直接对应 TDD §0 探针。任何一个 FAIL → 立即触发对应降级（写入 evidence/round-0/probes.md），不进入后续 case。

### CASE-D0-01: addParameterValueByIndex 可用性

- **步骤**：在 Live2DCanvas init() 内 model load 完之后（一次性）注入 TDD §0 Probe-1 代码，dev 服务器 reload 一次，看 console
- **预期日志**：`[probe1] has add? function` + `[probe1] before/after add: 0 5` + `[probe1] next frame: <motion3 value>`
- **PASS**：has add 是 function；after - before === 5
- **FAIL 处理**：把 evidence/round-0/probes.md 标 D0-01 FAIL；切换 AnimationOverlay 内部 add → set fallback；继续探针

### CASE-D0-02: Hiyori EyeBall 参数范围

- **步骤**：
```powershell
Select-String -Path "tauri-app\public\assets\live2d\hiyori\Hiyori.model3.json" -Pattern '"ParamEyeBall(X|Y)"' -Context 0,8
```
- **预期**：两参数都有，且能读到 Default/Minimum/Maximum 字段
- **PASS**：两参数齐全；记录实际 Min/Max（如 ±1.0）到 evidence/round-0/probes.md
- **FAIL**：参数缺失 → 修改 `pet-anim/_probe_constants.ts` 把 FR-3/FR-4 EyeBall 部分标 disabled（applyTo 已有 idx=-1 silent skip）

### CASE-D0-03: window pointermove 在 ignore_cursor_events=true 下触发

- **步骤**：粘 TDD §0 Probe-3 代码到 Console；在桌宠窗内慢慢移动鼠标
- **预期**：`[probe3] pointermove triggered <x> <y>` 至少出现一次
- **PASS**：能收到
- **FAIL**：完全收不到 → FR-4 降级为 hit-zone 内 pointermove；在 evidence 标记

### CASE-D0-04: hit-zone div click

- **步骤**：注入 TDD §0 Probe-4 临时红色方块；点击它
- **预期**：`[probe4] click received`
- **PASS**：能收到 + 红块不影响背后桌面其他窗口正常点击（拖个记事本到红块周围验证）
- **FAIL**：click 穿透到桌面 / 红块完全吃不到 click → 触发 FR-6 降级（Alt 暂关 ignore_cursor_events）；在 evidence 标记

**输出**：evidence/round-0/probes.md 必须 4 个 case 各一段，含 PASS/FAIL + 截图 + 后续应用的降级路径（如有）。

### CASE-D0-CLEANUP（v3 新增 — 解 MAJOR M'3）

D0-01..04 完成后必须执行：

- **步骤**：
  1. `git log --oneline -10` 找到注入探针的 commit
  2. `git revert <probe-commit-hash>` 或在独立 branch 上跑探针不合入 master
  3. `pnpm build` 跑一次 prod 构建
  4. 浏览器开 prod 包，确认 console 无 `[probe1..4]` 输出
  5. 全局搜源码 `grep -r 'data-pet-probe' tauri-app/src` → 应无结果
- **PASS**：4 项均符合
- **Evidence**：`evidence/round-0/cleanup-checklist.md` 记录 commit hash 和 revert hash

---

## 3. CASE-P：Perlin

### CASE-P-01: Perlin 微动可见

- **前置**：`flagDefault()`，关 gaze 和 saccade：`flagOff('gaze'); flagOff('saccade')` reload
- **步骤**：鼠标移出窗口；静观 60s；DevTools Performance 录 10s
- **预期**：肉眼能观察到头部和身体连续微动；无瞬移/抖动
- **PASS**：观察到连续微动
- **截图位**：`evidence/round-1/case-p-01.webm`

### CASE-P-02: Perlin 关闭对比

- **步骤**：`flagOff('perlin')` reload；静观 30s
- **预期**：除了 motion3 自带没额外漂移
- **PASS**：明显比 CASE-P-01 静

### CASE-P-03: Perlin 与 motion3 不冲突

- **步骤**：`flagDefault()`；单击桌宠播 TapBody
- **PASS**：TapBody 完整可见；姿态没被拉扯

### CASE-P-04: Perlin 幅度 A/B/C 盲选（v2 新增 — 解 OQ-3）

- **步骤**：
  1. 临时改 `pet-anim/perlinNoise.ts` 默认 amplitude，分别取 1.5°、2.0°、3.0° 各跑一遍
  2. 每次 reload 录 15s
  3. 命名 evidence/round-1/case-p-04-{A,B,C}.webm
  4. 1 周后自盲选（或问 1 位朋友）
- **PASS 标准**：选出最自然的档（v1 默认会用该档）

---

## 4. CASE-B：Blink

### CASE-B-01: 节律自然度

- **步骤**：`flagDefault()`；手机录像 60s
- **预期**：8–18 次 blink；间隔参差；偶发双眨
- **PASS**：间隔 max/min ≥ 2

### CASE-B-02: 闭合速度

- **步骤**：手机 240fps 慢动作录像
- **预期**：闭→开 ≈ 24 帧 ± 4（在 240fps 即 100ms ± 17ms）
- **PASS**：肉眼眨眼是"瞬时"的不拖沓

---

## 5. CASE-S：Saccade

### CASE-S-01: 可见

- **步骤**：`flagDefault()`；鼠标移出；静观眼球 30s
- **预期**：**≥20 次**（v2 修订）微眼跳
- **PASS**：能数到 ≥20 次

### CASE-S-02: 与 gaze 共存不抖

- **步骤**：缓慢绕圈移动鼠标 + 观察眼球
- **PASS**：跟随平滑 + 偶发微抖，无定格/卡顿

---

## 6. CASE-G：Gaze（v2 加入客观断言）

### CASE-G-01: 平滑度 + 方向客观断言

- **步骤**：
  1. `flagDefault()` reload
  2. 鼠标从屏幕左上慢扫到右下（3s）
  3. 录屏
  4. 鼠标停在左半屏中部 → Console 跑 `window.debug()` → 记录 `gaze_smoothed_yaw`
  5. 鼠标停在右半屏中部 → 同上
- **预期**：
  - 录屏中眼球平滑跟随
  - 左半屏 `gaze_smoothed_yaw < 0`
  - 右半屏 `gaze_smoothed_yaw > 0`
  - **方向不能反**（v2 关键 — 避免主观陷阱）
- **PASS**：录屏平滑 + 两次 debug 符号正确
- **截图位**：`evidence/round-1/case-g-01.webm` + console 截图 .png

### CASE-G-02: 死区 + 回正

- **步骤**：
  1. 鼠标静止在脸正前方 2s
  2. 快速到屏幕另一侧再回原位
  3. 鼠标移出 10+s
- **预期**：
  1. `gaze_smoothed_yaw` 几乎不变（死区生效）
  2. 回到原位视线归位
  3. 10s 后视线缓慢回正
- **PASS**：三子步骤全符合

### CASE-G-03: Clamp 极限

- **步骤**：鼠标移到屏幕四个边缘
- **预期**：`gaze_smoothed_yaw` ∈ [-20, +20]，`gaze_smoothed_pitch` ∈ [-15, +15]
- **PASS**：Console debug 显示 clamp 边界

### CASE-G-04: 多显示器负坐标（v2 修订）

- **步骤**（仅多显示器用户；副屏在主屏左侧）：
  1. 把鼠标移到副屏的远左侧（clientX 应为大负数，如 -800）
  2. Console 跑 `window.debug()`
- **预期**：`gaze_smoothed_yaw ≈ -20`（clamp 在极左），**不为 NaN**
- **PASS**：数值符号正确 + 不 NaN

### CASE-G-05: window pointermove 在 ignore=true 下工作

- **依赖**：CASE-D0-03 PASS（否则 FR-4 已降级，请跑 CASE-G-05-FALLBACK）
- **步骤**：把鼠标在桌宠窗范围内但**不在 hit-zone 内**（如左上角空白处）移动
- **预期**：眼球仍跟随
- **PASS**：跟随正常

### CASE-G-05-FALLBACK: 降级路径下 hit-zone 内才追随（v3 新增）

- **依赖**：CASE-D0-03 FAIL（FR-4 已降级到 hit-zone 内监听）
- **步骤**：
  1. 鼠标在 hit-zone 外（如左上角空白）移动 → 眼球应不动
  2. 鼠标移入 hit-zone（脸部）移动 → 眼球应追随
- **PASS**：两子步骤行为符合降级预期
- **Evidence**：录屏 + 标注 hit-zone 边界

### CASE-G-06: window resize 后 face 自适应（v3 新增 — 解 BLOCKER B'1）

- **目的**：验证 hit-zone 和 gaze face_center 在窗口尺寸变化后**同步**重算
- **步骤**：
  1. `flagDefault()` reload
  2. 鼠标停在初始的脸正前方，记录 `window.debug().gaze_smoothed_yaw ≈ 0`
  3. 把窗口宽度拖宽 200px
  4. 不动鼠标，等 200ms（节流），再读 `gaze_smoothed_yaw`
  5. 同时验证 hit-zone div：`document.querySelector('[data-pet-hitzone]').getBoundingClientRect()` 与新的窗口尺寸下的预期 bbox 一致
- **预期**：
  - resize 后 face_center_x 更新，鼠标相对脸的位移变化 → gaze_smoothed_yaw 不再为 0
  - hit-zone bbox 跟随重算
- **PASS**：两条同步更新

---

## 7. CASE-MP：Motion Pool

### CASE-MP-01: 未标注时正常

- **步骤**：`localStorage.removeItem('deskpet_motion_labels')` reload；触发 worried（手动注入 supervisor_severity=yellow 或改 store）
- **预期**：worried 状态正常进入；motion 走默认随机
- **PASS**：无 console error；动画切换正常

### CASE-MP-02: 标签生效

- **步骤**：
  1. Debug 模式打开 HiyoriMotionTuner
  2. 给 m01/m02 打 fast、m08/m09/m10 打 slow
  3. 触发 worried
  4. 观察 `window.debug().current_motion_idx` 一段时间
- **预期**：worried 期间 `current_motion_idx ∈ {8, 9, 10}` 或 medium（如有）
- **PASS**：连续 3 次切换全在 slow 子集 + medium

### CASE-MP-03: Round-robin 防重复

- **步骤**：在 CASE-MP-02 基础上观察连续 5 次切换的 idx
- **预期**：相邻不同；最近 3 个不重复（candidates ≥ 3）
- **PASS**：序列符合规则

### CASE-MP-04: state_changed 时立即切（v2 新增 — 解 M4）

- **步骤**：
  1. 处于 working 状态（fast 子集播 m01）
  2. 在 m01 播放到一半（≈ 5s 内）手动注入 supervisor red alert（让 PetStateMachine 进入 alert）
  3. 观察 `window.debug().current_motion_idx`
- **预期**：alert 状态进入后立即（< 1s 内）切到 slow/special 子集 idx；不等 working 的 15s 周期
- **PASS**：切换在 1s 内发生

---

## 8. CASE-PR：Pointer（v2 — hit-zone）

> 依赖：CASE-D0-04 PASS（否则 FR-6 已降级，CASE-PR 走降级流程）

### CASE-PR-01: Single click 反应

- **步骤**：
  1. `flagDefault()` reload
  2. 在 hit-zone（角色身体）上单击；间隔 > 1s；重复 5 次
- **预期**：每次都触发 TapBody；`window.metrics().visual.samples` 累计 ≥ 5
- **PASS**：5 次都触发

### CASE-PR-02: Double click 反应

- **步骤**：在 hit-zone 上双击（≤300ms）
- **预期**：触发一次"惊吓"，不是 TapBody×2；ParamAngleZ 出现 ±10° 抖动
- **PASS**：能区分单/双击效果

### CASE-PR-03: 单/双击边界精确（v2 修订 — 用 console eval）

- **步骤**：
  1. `window.testClickPair(250)` — 应识别为 double_click
  2. `window.testClickPair(400)` — 应识别为两次 click
  3. Console 观察 metrics.interaction.samples 是否分别记录 1 次 / 2 次（v2 注：每个 click event 都记 interaction_latency，但 double 只 emit 一次 effect）
- **PASS**：两种行为符合预期

### CASE-PR-04: Hover enter/leave

- **步骤**：鼠标缓慢进入 hit-zone 保持 1s → 移出
- **预期**：进入时 headTilt transient +3° + blink 稍快；离开后恢复
- **PASS**：能观察到差异

### CASE-PR-05: 桌面 hit-through 不破坏

- **步骤**：
  1. 把记事本拖到桌宠的透明区（hit-zone 之外）下方
  2. 点击记事本
- **预期**：能正常 focus / 点击记事本
- **PASS**：其他窗口可正常交互
- **FAIL 处理**：检查 hit-zone bbox 是否设错（占满整窗），或 Tauri set_ignore_cursor_events 状态被改

### CASE-PR-FALLBACK: D0-04 失败时的降级路径（v3 新增）

- **依赖**：CASE-D0-04 FAIL（FR-6 已降级到"按 Alt 暂关 ignore"）
- **步骤**：
  1. 在桌宠上点击 → 应无反应（ignore_cursor_events=true 状态）
  2. 按住 Alt 键的同时点击 → 应触发 TapBody
- **PASS**：两子步骤符合降级预期
- **Evidence**：录屏标注 Alt 按键

### CASE-PR-06: hit-zone 与 Tuner UI 不冲突（v3 新增 — 解 MINOR m'7）

- **目的**：在窄窗口下 hit-zone 不应遮挡 HiyoriMotionTuner 的按钮
- **步骤**：
  1. 缩窄主窗口至最小（让 Tuner 接近角色）
  2. 尝试点击 Tuner 的 Play 按钮
- **预期**：Tuner 按钮正常响应（即 Tuner UI z-index 高于 hit-zone）
- **PASS**：Tuner 可点击
- **FAIL 处理**：调整 hit-zone div z-index 至低于其他 UI 面板（PRD §6.0 已声明）

---

## 9. CASE-MET：反应延迟 SLO（v2 — 双指标）

### CASE-MET-01: interaction_latency

- **步骤**：
  1. 连续单击 hit-zone 20 次（间隔 > 1s）
  2. Console `window.metrics()` → 读 `interaction` 字段
- **预期**：
  - `p50 ≤ 30ms`
  - `p95 ≤ 50ms`
  - `max ≤ 120ms`
- **PASS**：达标
- **截图位**：`evidence/round-1/case-met-01.png`

### CASE-MET-02: visual_latency + FIFO 配对（v3 修订 — 解 MAJOR M'2）

- **步骤**：
  1. 普通连点：单击 hit-zone 20 次（间隔 > 1s）→ 读 `visual` 字段
  2. **FIFO 配对验证**：Console 跑 `window.testClickPair(200)` 一次 → 等 2s → 再 `testClickPair(200)` → 读 `metrics().visual.samples.slice(-4)`
- **预期**：
  - 普通：`p50 ≤ 150ms`、`p95 ≤ 300ms`、`max ≤ 600ms`
  - FIFO：最后 4 个样本对应 4 个 event，且第 1/2 个差异 ≈ 200ms（两 click 间隔），不会乱序
- **PASS**：两子条件均符合
- **FAIL 处理**：若 visual.p95 > 300 → 关 saccade 重测；仍 fail → Plan B 砍 FR-1 幅度

### CASE-MET-03: Ring 容量

- **步骤**：点击 110 次后查 `samples.length`
- **PASS**：interaction.samples.length === 100 && visual.samples.length === 100

---

## 10. CASE-PERF：性能（v2 — 关 DevTools + bench）

### CASE-PERF-01: FPS（关 DevTools）

- **步骤**：
  1. `flagDefault()` reload
  2. **关闭 DevTools**
  3. 重开 DevTools 仅录 Performance 30s（含 5 次 click + 鼠标绕圈）
  4. 录完再关 DevTools（Performance 录制完成后停止干扰）
- **预期**：FPS 直方图集中在 28-32
- **PASS**：30s 内 FPS < 24 的帧 ≤ 10%

### CASE-PERF-02: CPU/RAM 增量（任务管理器）

- **步骤**：
  1. **关闭 DevTools**
  2. 任务管理器 → 详细信息 → 找 deskpet.exe → 加列 CPU / 内存
  3. `flagDefault()` reload，记录稳定后 30s 均值
  4. `flagAllOff()` reload，记录
  5. 计算增量
- **PASS**：v1 vs all-off：CPU +≤5%、RAM +≤30MB

### CASE-PERF-03: applyTo ms/call bench（v2 新增 — 解 M7）

- **步骤**：在 Console 跑
```js
(() => {
  const t0 = performance.now();
  const N = 100000;
  // 反复调用 applyTo（通过 window.__deskpet_anim_bench 暴露）
  for (let i = 0; i < N; i++) {
    window.__deskpet_anim_bench?.applyToOnce(performance.now() + i * 33);
  }
  const dt = performance.now() - t0;
  console.log(`applyTo mean ms/call = ${(dt / N).toFixed(3)}`);
})();
```
> **v2 注**：AnimationOverlay 需提供 `window.__deskpet_anim_bench.applyToOnce(t)`，在 dev 模式下暴露；prod 不暴露。
- **预期**：mean < 0.5ms/call
- **PASS**：< 0.5ms
- **FAIL 处理**：触发 Plan B（砍 FR-3 saccade）

---

## 11. CASE-REG：零回归

### CASE-REG-01: 视觉

- **步骤**：
  1. `flagAllOff()` reload，录 30s
  2. `git stash && git checkout 0f69254 -- tauri-app/src` （或另一 worktree 跑 dev），录 30s
  3. **盲选**两段录屏
- **PASS**：5 人盲选 ≤ 1 人能分辨（v2：开发者本人 1 周后 + 1 位朋友盲选，仍要求"不能稳定分辨"）

### CASE-REG-02: 功能

- **步骤**：
  1. `flagAllOff()`
  2. 注入 supervisor red alert
  3. 验证 alert 正常 + TapBody 入场播放
- **PASS**：行为与 commit 0f69254 一致

---

## 12. CASE-HMR：HMR 安全（v2 新增）

### CASE-HMR-01: HMR 不重复 overlay

- **步骤**：
  1. `flagDefault()`，Console 跑 `window.debug()` 记录当前 `current_motion_idx`
  2. 编辑器随便改 `tauri-app/src/pet-anim/perlinNoise.ts`（如改注释），保存触发 HMR
  3. 等 reload 后再 `window.debug()`
- **预期**：
  - pet 不出现两个（DOM 检查 `document.querySelectorAll('[data-pet-hitzone]').length === 1`）
  - metrics.interaction.samples 已重置或保留单一 ring（取决于实现，不能有 2 倍）
  - 鼠标动 1 次 → window pointermove listener 应只触发 1 次（spy via console.count）
- **PASS**：DOM 唯一 + listener 不重复

---

## 13. CASE-COLD：冷启动（v2 新增）

### CASE-COLD-01: 模型未加载完时 pointer events 不抛

- **步骤**：
  1. `flagDefault()` reload
  2. 在 Live2D 模型 load 完成（看到 Hiyori 出现）**之前**就疯狂在窗口内移动鼠标 + 点击
- **预期**：模型 load 完成后正常工作；期间 console 无报错；overlay 在 model 未就绪时 applyTo 直接 return
- **PASS**：模型加载后 1s 内一切正常

---

## 14. CASE-BLIND-01: 盲测"活感"（v2 修订）

- **方法**：
  1. 录两段 30s：
     - A: `flagAllOff()` + 鼠标动 + 1 次 click
     - B: `flagDefault()` + 同样操作
  2. **开发者本人**：录完 1 周后再看，自盲选（中间不复看）
  3. 至少 1 位朋友：随机顺序播放 A/B，回答：
     - Q1: 哪个更有"活感"？
     - Q2: 哪个让你想多看一会儿？
- **PASS**（v3 与 PRD §AC-8 统一）：
  - **2/2 选 B** → PASS
  - **1/2 选 B** → WARN（记录但不阻断 Sprint 验收）
  - **0/2 选 B** → FAIL（应回头复查 FR-1~FR-4 是否真有可见效果）
- **Evidence**：`evidence/blind-test/A.mp4`、`B.mp4`、`results.md`

---

## 15. 差评反向验收（针对调研 §5）

| # | 差评 | 反向验收 | PASS 标准 |
|---|---|---|---|
| R-1 | 重复性高 | CASE-MP-03 + CASE-P-01 30 分钟观察 | 30 分钟不抓相同姿态 |
| R-2 | 打扰工作 | v2 跳过（静默档列入 v2） | N/A |
| R-3 | idle 太频繁 | window.debug() 查 motion 切换日志频率 | 同状态切换间隔 ≥ switch_period × 0.7 |
| R-4 | 表情和语气不符 | v2 跳过 | N/A |
| R-5 | 嘴形对不上 | v2 跳过 | N/A |
| R-6 | 拖拽穿模 | v2 跳过 | N/A |

---

## 16. 测试通过判定（最终验收 — v2 修订）

全部满足：

1. **Day-0 4 探针**全 PASS（如有 FAIL，必须有降级 evidence 且后续 case 走降级流程）
2. **所有 P0 case 全 PASS**：CASE-P-01/02、B-01、S-01、G-01/02/03、MP-02/03/04、PR-01/02/05、MET-01/02/03、PERF-01/02/03、REG-01、HMR-01、COLD-01
3. **CASE-BLIND-01 PASS**
4. **Evidence 归档完整**：每 P0 case 截图或录屏；MET 有 console 截图；PERF 有 Performance trace + 任务管理器截图；BLIND 有两段录屏 + results.md
5. **CI 全绿**：tsc + lint + vitest（覆盖率达标）+ test:e2e-wire

---

## 17. FAIL 时上报模板

```markdown
### FAIL: CASE-XX-YY

- **实际现象**：…
- **预期**：…
- **复现步骤**：…
- **window.debug() 输出**：…（v2 加入客观状态采集）
- **截图/录屏**：evidence/…
- **环境**：Windows 11 build XX、tauri dev、commit SHA、Day-0 探针结果（D0-01..04 各 PASS/FAIL）
- **可能根因**：…
- **建议修复方向**：…
```

放到 `evidence/round-N/FAIL-CASE-XX-YY.md`，等待修复进入 round-N+1。

---

## 18. 子代理执行约定（GOAL 阶段）

手测子代理（Opus 4.7）应：
1. 启动 Tauri dev（background）
2. 用 Preview MCP / computer-use 截图观察
3. **必须先跑 CASE-D0-01..04**，任何 FAIL 必须在 evidence/round-0/probes.md 标记并走降级
4. 然后按 case 顺序执行（CASE-P → B → S → G → MP → PR → MET → PERF → REG → HMR → COLD → BLIND）
5. 每 case 真实执行（**不能用"应该会 PASS"代替观察**）
6. 调用 `window.debug()` / `window.metrics()` 拿客观数据辅助判断
7. FAIL 时按 §17 模板生成报告

**严格禁止**：
- 跳过 Day-0 探针
- 跳过任何 P0 case
- 用 vitest 单测代替手测（vitest 不能验视觉/性能/穿透）
- BLIND case 跳过盲选环节
- 在 DevTools 打开状态下做 CASE-PERF-* 测试

---

## 19. 修订日志

### v3（Round-2 评审反馈）
- **§2** CASE-D0-CLEANUP 新增（解 MAJOR M'3 探针清理）
- **§6** CASE-G-05-FALLBACK 新增（D0-03 fail 时的降级测试）
- **§6** CASE-G-06 新增（window resize 自适应 — 解 BLOCKER B'1）
- **§8** CASE-PR-FALLBACK 新增（D0-04 fail 时的 Alt 关 ignore 降级）
- **§8** CASE-PR-06 新增（hit-zone 与 Tuner UI z-index 不冲突 — 解 MINOR m'7）
- **§9** CASE-MET-02 加 FIFO 配对验证子步骤（解 MAJOR M'2 落地）
- **§14** CASE-BLIND-01 PASS 标准与 PRD §AC-8 统一（2/2 PASS、1/2 WARN、0/2 FAIL — 解 MINOR m'1）

### v2 修订日志

- **§0.3** helper bug 修复（用 FLAG_KEYS 对照表）+ 加 `testClickPair` console 精确触发
- **§0.5** 加"性能测试时关 DevTools"指引（解 M8 / 实战坑 #14）
- **§2** 新增 CASE-D0-01..04 Day-0 探针（解 BLOCKER B1/B2/B4 全部对应手测）
- **§3** 新增 CASE-P-04 Perlin 幅度 A/B/C 三档（解 OQ-3）
- **§5** CASE-S-01 阈值从 ≥15 改 ≥20（与 saccade 1Hz 调研对齐）
- **§6** CASE-G-01 加客观断言（`window.debug()` gaze_smoothed_yaw 符号检查 — 解实战坑 #5）；CASE-G-04 用具体负数值；新增 CASE-G-05 window pointermove 验证
- **§7** 新增 CASE-MP-04 state_changed 立即切（解 MAJOR M4）
- **§9** MET 拆 interaction / visual 双指标（解 BLOCKER B3）
- **§10** PERF-01/02 加"关 DevTools"步骤；新增 CASE-PERF-03 applyTo bench（解 MAJOR M7）
- **§11** REG-01 改"开发者 1 周后自盲选 + 1 朋友"（解 MAJOR M5）
- **§12** 新增 CASE-HMR-01（解漏项"HMR"）
- **§13** 新增 CASE-COLD-01（解漏项"冷启动"）
- **§14** BLIND-01 改 1+1 人方案
- **§17** FAIL 模板加 `window.debug()` 输出 + Day-0 探针结果
- **§18** 子代理执行约定显式要求"必须跑 D0 + 不能跳过 P0 + 性能不能开 DevTools"
