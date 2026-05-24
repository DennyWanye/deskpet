# FIX-R3 — hit-zone × drag / DialogBar 文字选区 冲突修复

| 项 | 值 |
|---|---|
| 日期 | 2026-05-24 |
| 上下文 | 用户报告 Phase 1 改动**破坏了拖窗 + DialogBar 文字选区**两个原功能 |
| 状态 | **FIXED + 真 OS 实测 PASS** |

---

## 1. Bug 根因

### Bug A — 拖窗破坏

App.tsx:880 桌宠壳挂 `data-tauri-drag-region` → 整窗 mousedown 可拖。我加的 `<div data-pet-hitzone>` 在脸部 bbox：
- `pointer-events: auto`
- `z-index: 25` (远高于 DialogBar 10、input bar 20 不冲突，但比父壳 drag-region 的事件优先级)
- onClick React handler 截胡 mousedown

→ 点桌宠脸 → hit-zone 吃 mousedown → Tauri drag-region 收不到 → 不拖窗

### Bug B — DialogBar 文字选区破坏

DialogBar 位置：`bottom: 56`, `maxHeight: 96`, `z-index: 10` → Y 范围 ≈ 271-367 CSS (pet 窗内坐标)
Hit-zone：`top: 84.6`, `height: 254` → Y 范围 84.6 - 338.6

**Y 271-338 重叠**（DialogBar 上半被 hit-zone 覆盖），z:25 > z:10 → DialogBar 上半 mousedown 被 hit-zone 吃 → text selection 失败。

---

## 2. 修复方案

### Step 1: hit-zone z-index 25 → 5
新 stacking order（自上而下）：
1. PetDebugOverlay z:30
2. supervisor bubble, code panel, settings... (z:20+)
3. 底部 input bar z:20
4. DialogBar z:10
5. **hit-zone z:5**
6. pet `<img>` (pointer-events:none, 无 z)

DialogBar z:10 > hit-zone z:5 → 重叠区 DialogBar 在上层赢 mousedown，**文字选区恢复**。

### Step 2: 手动 drag detection (不用 `data-tauri-drag-region`)

**为什么不直接加 drag-region 属性**：实测加上 `data-tauri-drag-region` 后 React onClick **完全不触发**（interaction.samples 一直空）。原因——Tauri drag-region 在 mousedown 时立刻 SendMessage(WM_NCLBUTTONDOWN, HTCAPTION) 把光标移交 Win32 window manager，gesture 不再走 webview，click 事件被吞。

→ **改手动检测**：

```tsx
const dragStartRef = useRef<{x, y} | null>(null);
const startDraggingRef = useRef<() => Promise<unknown> | null>(null);

// Mount 时预加载 + 缓存
useEffect(() => {
  import("@tauri-apps/api/window").then(m => {
    startDraggingRef.current = () => m.getCurrentWindow().startDragging();
  });
}, []);

onPointerDown: dragStartRef = { clientX, clientY }
onPointerMove: dx² + dy² > 25 → call startDraggingRef() 同步 + clear ref
onPointerUp: clear ref
onClick: pulseInteraction (只在没拖动时触发)
```

**关键设计点 — sync vs async**：
首版用 `import("@tauri-apps/api/window").then(...)` 直接调 → **拖窗失效**。原因：dynamic import 异步 → 等 promise resolve 时 mouse button 已释放 → `WM_NCLBUTTONDOWN` 被 OS 拒（要求 button 仍按住）。
修：mount 时预加载 + 缓存 bound fn → onPointerMove 内**同步**调用 → drag 立即生效。

---

## 3. 真 OS 级回归测试（windows-mcp + Win32 mouse_event）

### Test A — 单击 hit-zone
```
[M]::Move(3293, 1354) [M]::DOWN [20ms] [M]::UP
```
**结果**：
- interaction.samples = [0] (1 sample)
- visual.samples = [11.8ms]
- window 位置不变 ✓ (无 false drag)

### Test B — 拖 hit-zone（drag 200px LEFT）
```
[M]::Move(3689, 1496) [M]::DOWN [50ms]
for $i (1..20) { [M]::Move($x-=10, 1496) [40ms] }
[M]::UP
```
**结果**：
- window 从 (1619, 491) → (1433, 424) CSS  ✓ (跟随光标方向移动)
- click 不触发（被 drag 取代）

### Test C — DialogBar 文字 drag-select（在 hit-zone × DialogBar 重叠区 Y=1500）
```
[M]::Move(3200, 1500) [M]::DOWN [50ms] [drag to 3450, 1500] [M]::UP
```
**结果**：
- `window.getSelection().toString()` = `"重启"` (length 2) ✓
- window 位置 (1433, 424) 不变 ✓ (无 false drag — DialogBar 的 onMouseDown stopPropagation 已阻止 drag-region 升级)

---

## 4. 验证 stacking 不破其他功能

| 元素 | z-index | 与 hit-zone z:5 关系 | 行为 |
|---|---|---|---|
| pet `<img>` | (auto, pointer-events:none) | 在 hit-zone 下 | 视觉显示 |
| **hit-zone div** | **5** | — | click/hover/drag detection |
| DialogBar | 10 | 在 hit-zone 上 | 文字选区 ✓ |
| input bar | 20 | 在 hit-zone 上 | 输入正常 |
| Toolbar / button | ≥20 | 在 hit-zone 上 | 点击正常 |
| PetDebugOverlay | 30 | 在 hit-zone 上 | debug 显示 |
| Bubble / Panel | 20-40 | 在 hit-zone 上 | UI 不被遮 |

✓ 所有原 UI 元素都在 hit-zone 之上，hit-zone 只在"empty face area"（没其他 UI 的脸部位置）抓事件。

---

## 5. CI 验证

- `npx tsc --noEmit`: clean
- `npx vitest run src/pet-anim`: 78/78 pass
- 全工程 vitest: 386/386 pass（fix 不回归任何单测）

---

## 6. 代码变更点

`tauri-app/src/components/Live2DCanvas.tsx`：
- 新增 `dragStartRef` + `startDraggingRef` 两个 useRef
- 新增 useEffect 预加载 `@tauri-apps/api/window` 并缓存 `startDragging` 函数
- hit-zone div 移除 z-index 25 → 改为 5
- hit-zone div 新增 onPointerDown / onPointerMove / onPointerUp 手动 drag detection

---

## 7. 结论

**已修**：
- ✅ 拖桌宠脸 → 拖窗
- ✅ DialogBar 文字 drag-select
- ✅ 单击桌宠脸 → TapBody 反应（FR-6 不回归）
- ✅ 双击桌宠脸 → 惊吓反应（FR-6 不回归）

**待 commit + ship 修订**。
