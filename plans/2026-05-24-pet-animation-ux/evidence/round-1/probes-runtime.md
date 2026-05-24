# Day-0 探针 runtime 实测 — round-1

| 项 | 值 |
|---|---|
| 日期 | 2026-05-24 |
| 执行子代理 | Opus 4.7 (1M) QA |
| 验收对象 commit | 3b1e78f |
| 结论 | 仅 D0-02 PASS（离线）；D0-01/03/04 BLOCKED-ENV |

---

## CASE-D0-01: addParameterValueByIndex 可用性

- **状态**：**BLOCKED-ENV**
- **原因**：
  - 需在 `Live2DCanvas.init()` model load 完之后注入 Probe-1 JS 代码并观察 Console
  - WebView2 无远程调试端口（见 `blockers.md` §B1）
  - 子代理无权直接改 src/ 注入探针（见 prompt 硬性规则 §7）
- **缓解**：实现已对两种结局都写好分支（PHASE1 报告 §5）
  - `AnimationOverlay` `add_native = typeof model.addParameterValueByIndex === 'function'` runtime 探测
  - ADD 不存在 → 一次性 console.warn + 走 set/get fallback
  - 即使 Probe-1 FAIL，主路径仍可工作
- **解锁方式**：见 blockers.md §4 选项 A/B
- **截图**：N/A

## CASE-D0-02: Hiyori EyeBall 参数范围

- **状态**：**PASS（离线）**
- **执行命令**：
  ```powershell
  Select-String -Path "G:\projects\deskpet\tauri-app\public\assets\live2d\hiyori\Hiyori.cdi3.json" -Pattern '"ParamEyeBall(X|Y)"' -Context 0,3
  ```
- **实际输出**：
  ```
  Hiyori.cdi3.json:45:    "Id": "ParamEyeBallX",
  Hiyori.cdi3.json:46:    "GroupId": "ParamGroupEyeballs",
  Hiyori.cdi3.json:47:    "Name": "目玉 X"
  Hiyori.cdi3.json:50:    "Id": "ParamEyeBallY",
  Hiyori.cdi3.json:51:    "GroupId": "ParamGroupEyeballs",
  Hiyori.cdi3.json:52:    "Name": "目玉 Y"
  ```
- **备注**：
  - ManualTest 写的是 `Hiyori.model3.json`，但 Cubism 4 参数实际在 `Hiyori.cdi3.json`（PHASE1 报告 §3 已说明）
  - cdi3.json 不含 Min/Max 数值字段（位于 `.moc3` 二进制内）；`_probe_constants.ts` 用约定默认值 ±1.0（EyeBall）/ ±30°（Angle）/ ±10°（BodyAngle）
  - saccadeScheduler / gazeTracking / AnimationOverlay clamp 全部从此单一来源读取
- **结论**：两参数齐全 → PASS；无需触发 FR-3/4 EyeBall 降级

## CASE-D0-03: window pointermove 在 ignore_cursor_events=true 下触发

- **状态**：**BLOCKED-ENV**
- **原因**：
  - 需在 deskpet 窗口内移动鼠标 + 在 WebView Console 读 `[probe3] pointermove triggered <x> <y>` 日志
  - 同 D0-01：无 Console 访问 + 不能干扰用户当前桌宠（B1/B2）
- **缓解**：实现已含降级路径（PHASE1 §5）
  - FR-4 默认监听 `window.addEventListener('pointermove')`
  - 若 D0-03 FAIL → 改挂 hit-zone div（1 行修改）+ ManualTest CASE-G-05-FALLBACK 覆盖

## CASE-D0-04: hit-zone div click

- **状态**：**BLOCKED-ENV**
- **原因**：
  - 需注入红色方块 + 验证 click 不穿透到桌面
  - 同 D0-01：无 Console 访问 + 不能改 src + 不能干扰用户实际桌面交互
- **缓解**：实现已含 Alt+click 降级路径占位（PHASE1 §5）

## CASE-D0-CLEANUP

- **状态**：N/A（未注入探针 → 无需清理）

---

## 结论 → 后续 case 路径选择

按 ManualTest §2 规则：
- D0-02 PASS → FR-3/4 EyeBall 主路径有效，**无需启用** _probe_constants.ts 中的 disabled 旁路
- D0-01/03/04 RUNTIME 未验证 → 实现已含双路径 fallback，**理论可工作**，但 ManualTest §16 要求 4 探针全 PASS 才能继续 P0
- 当前 round 因 BLOCKED-ENV 不能宣布"D0 PASS 进入 P0"

→ Phase 1 round-1 **不达 ShipIt 标准**；需主线程解锁环境后重跑 D0-01/03/04 + 所有 P0。
