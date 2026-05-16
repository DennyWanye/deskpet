# Plan — Code Mode 窗口左侧「信息显示区域」(2026-05-16)

## 目标（用户已确认）
Code Mode 窗口（CodePanelRoot）chat 视图改 **3 列**：
`SessionSidebar | 信息显示区域 | 聊天(MessageStream+InputBar)`。
信息显示区域 = 复用桌宠窗的 `components/MessageStreamPanel`（聊天流 +
supervisor ⚠/🚨 inbox + 全部/对话/⚠/🚨 tab + 折叠），喂 code-panel 的
共享 zustand 数据。右侧主聊天 + 输入 + pet 不动。

## 决策
- **复用 MessageStreamPanel**（无状态 props 驱动），不另写。两窗 UI 统一、零重复。
- MessageStreamPanel 硬编 `position:absolute`（桌宠 overlay），3 列 flex 会破版
  → 加 **`embedded?: boolean`**（默认 false = 桌宠行为**零回归**；true =
  wrapper/collapsed 改 relative 填满 flex 格）。
- 数据全在共享 `useSessionsStore`：`sessions[active_sid].messages`（聊天）
  + `collect_inbox(sessions,"yellow"|"red")`（supervisor inbox，sessionsStore
  已导出）。
- chatMessages 用 App.tsx 同款 builder（`messages.flatMap` + `forPet` 滤
  think/tool trace）。**stripMarkdown 不搬**（App.tsx-local，搬动会碰前端 WIP
  纠缠）——信息区只 `forPet`，markdown 去除是纯装饰，可接受的小偏差。
- handlers：`dismiss_alert`/`dismiss_all_alerts` 直接取自 store（App 同款）；
  `onJumpToSession` → set active_sid + 切 chat 视图；`onChoice` → `codePanelWS.send`
  发 supervisor 选择（沿用 ws.ts 既有消息形态）。

## 文件改动（2）
1. `tauri-app/src/components/MessageStreamPanel.tsx`
   - Props 加 `embedded?: boolean`
   - `wrapperStyle`/`collapsedHandleStyle` 按 embedded 分支：
     embedded → `{position:"relative", height:"100%", width:"100%", ...视觉不变}`，
     折叠态为 flex 内的窄条（非 absolute）；
     默认 → 现有 absolute overlay（桌宠窗，**不变**）
2. `tauri-app/src/code-panel/CodePanelRoot.tsx`
   - chat 视图 body 改 3 列：Sidebar | `<div w=320 flex-col>` 装
     `<MessageStreamPanel embedded ... />` | 现有 chat 主区
   - 加 `streamCollapsed`/`streamFilter` useState（同 App）
   - `chatMessages` = messages.flatMap(forPet) builder（照抄 App.tsx 684-702，
     去掉 stripMarkdown）
   - `warnings/errors` = collect_inbox(sessions,"yellow"/"red")
   - dismiss/dismissAll 取自 store；jumpToSession=set_active_sid+set_view("chat")；
     onChoice=codePanelWS.send 选择消息（对齐 ws.ts supervisor 处理）

## 不做（明确）
- 不动桌宠窗 App.tsx（embedded 默认 false 保其零回归）
- 不动右侧 MessageStream/InputBar/SessionGridView/SessionSidebar
- 不搬 stripMarkdown（避免碰前端 WIP 纠缠）；信息区聊天只 forPet 清洗
- dashboard 视图不加信息区（仅 chat 视图 3 列）

## 验证
- tsc EXIT=0 + vitest 全绿（MessageStreamPanel 既有测试不得回归；
  embedded 默认分支 = 旧行为）
- 真机 E2E（computer-use）：开 Code Mode 窗口 → 左侧出现信息显示区域，
  显示该 session 聊天流 + supervisor 告警（造一条 supervisor alert 验
  ⚠/🚨 tab）；折叠/展开正常；右侧主聊天+输入不受影响；桌宠窗口信息面板
  行为不变（回归检查）。截图存证。
