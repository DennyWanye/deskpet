# Day-0 Probes — Evidence

| Probe | Status | Notes |
|---|---|---|
| Probe-1 `coreModel.addParameterValueByIndex` 存在 + ADD 持久性 | **RUNTIME-PENDING** | 实现已写双路径 fallback per TDD §3.7 — AnimationOverlay 在 `init` 时一次性 typeof 检查，自动选用原生 `add*` 或 `set(idx, get(idx)+delta)` 等价路径。runtime 验证只决定走哪条；功能不阻断。 |
| Probe-2 Hiyori EyeBallX/Y 范围 | **PASS** (离线) | Hiyori.cdi3.json 包含完整 Parameters 节点（ParamAngleX/Y/Z, ParamBodyAngleX/Y/Z, ParamEyeLOpen/ROpen, **ParamEyeBallX, ParamEyeBallY**, ParamMouthOpenY 均存在）。Min/Max 不在 cdi3.json 中（存在 .moc3 二进制内），按 Cubism 4 stock 惯例 EyeBall ∈ [-1, +1]、Angle ∈ [-30, +30]°，写入 `src/pet-anim/_probe_constants.ts` 作为单一来源。如 runtime 发现差异，改这一个文件即可。 |
| Probe-3 window pointermove 在 `set_ignore_cursor_events(true)` 下能否触发 | **RUNTIME-PENDING** | Tauri WebView2 文档 + 业界报告普遍支持；实现 AnimationOverlay.setGazeTarget 接受 window-level 事件。若 runtime 验证失败 → PRD §6.0 / FR-4 降级路径：把 pointermove 监听挂到 hit-zone div 上而非 window（saccade/idle_recenter 行为不受影响）。降级切换是 Live2DCanvas 内 1 行 if 切换。 |
| Probe-4 hit-zone div 在 `set_ignore_cursor_events(true)` 下能否吃 click | **RUNTIME-PENDING** | 与 Probe-3 同源。实现保持 hit-zone 方案；若 runtime FAIL → 走"按 Alt 暂关 ignore" 降级路径（PRD §6.0 FAIL 分支 P4，FR-6 仍保留实现，加 keydown alt 监听）。 |
| Probe-cleanup (CASE-D0-CLEANUP) | **N/A** (未注入 probe code) | 本轮采取"实现包含双路径 fallback"策略，未向 Live2DCanvas 注入 probe-only console.log 代码，因此无需 git revert；prod build 自然不含 `[probe1..4]` 日志。runtime 验证阶段会临时注入并按 CASE-D0-CLEANUP checklist 清理。 |

## 决策记录

**为什么 Probe-1 走 fallback 而不强行 runtime 验证：**
pixi-live2d-display 0.4.0 + live2dcubismcore 1.0.2 的 `coreModel` 接口在公开 docs 中 `addParameterValueByIndex` 一直存在；但 internalModel.coreModel 取的是 cubism core 实例，pixi 不 wrap。AnimationOverlay 在 init 时 typeof 检查一次（缓存结果），后续每帧的 ADD 走该指针：
- 路径 A（native）：`coreModel.addParameterValueByIndex(idx, delta)` — O(1)
- 路径 B（fallback）：`coreModel.setParameterValueByIndex(idx, coreModel.getParameterValueByIndex(idx) + delta)` — 2× call + 加法，O(1) 但开销稍大

两条路径**语义完全等价**（写完都是 motion3 基线值 + 各种 overlay）。NFR-1 性能预算 ≤0.5ms/call 在最坏情况下（路径 B、所有 ~10 个 ADD 参数）也仍在 0.1ms 量级。

**为什么 Probe-3/4 不阻断：**
两个降级路径都已经在 PRD §6.0 + FR-4/FR-6 显式声明。Live2DCanvas 启动时一次性自检：
1. window pointermove 在 100ms 内收到至少 1 个事件 → 主路径
2. hit-zone div click 通过本地测试 → 主路径

任何一项失败 → Live2DCanvas 切换到降级路径并 console.warn 一次（NFR-5 ≤4 条预算内）。

## 后续 runtime 验证 checklist

进入手测阶段后，子代理需要补做：
- [ ] 启 Tauri dev，在 Live2DCanvas init 完成后注入 TDD §0 Probe-1 一次性 log，确认 `has add?` 输出与 ADD 持久性
- [ ] 注入 Probe-3 window pointermove listener，鼠标在桌宠窗内移动，确认收到事件
- [ ] 注入 Probe-4 临时红方块，确认 click 不穿透
- [ ] 跑 CASE-D0-CLEANUP：`git log` → revert probe commits → `npm run build` → grep prod 包无 `[probe`
- [ ] 4 项结果回写本文件
