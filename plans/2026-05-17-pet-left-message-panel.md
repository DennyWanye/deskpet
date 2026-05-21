# Plan — 桌宠窗左侧常驻消息面板 (2026-05-17)

## 用户已确认的需求
- 桌宠窗(`main` 窗,App.tsx)**左侧常驻**一个好看的消息面板。
- 内容:**复用 MessageStreamPanel 全套**(主线程聊天流 + 全部/对话/⚠/🚨 tab
  + supervisor 告警 inbox + 折叠能力),数据 = default/active 会话。
- 底部 DialogBar **保留共存**(仍作桌宠"说话气泡",显最新一句)。
- 显示方式:**常驻左侧**,窗口变宽,始终显示(默认展开)。
- 视觉:**全新精致设计**(给面板做一个打磨过的玻璃外框,内部复用组件)。

## 决策
- **复用** feat 分支的 `MessageStreamPanel.tsx`(`embedded` 模式)+ 其
  sessionsStore 依赖(`collect_inbox`/`supervisor_inbox`/`SupervisorAlertEntry`/
  `dismiss_alert`/`dismiss_all_alerts`)。master 上这些被 `d554105` revert 掉了,
  只在 `feat/2026-05-16-session-work`。`git diff master feat -- sessionsStore.ts`
  = +138/-10 纯加性(10 删=内联类型替换为 SupervisorAlertEntry,结构同),
  ws.ts(master)调 `apply_supervisor_alert` 形态兼容 → 可整文件恢复。
- 桌宠窗布局:root 改为横向 flex `[左面板 ~360px,可见玻璃面] [右=原桌宠壳,
  透明,position:relative,data-tauri-drag-region]`。**所有现有 absolute 覆盖层
  (DialogBar/输入条/气泡/弹窗)整体包进右壳** → 相对定位不变,零回归。
- Tauri `main` 窗加宽并左移,使 Hiyori 视觉位置不变:`width 400→768`,
  `x 2100→1732`(左移≈面板宽,右壳 flex:1 ≈ 原 400)。
- 接线照搬 CodePanelRoot 模式:`streamChat = messages.flatMap + forPet`;
  `warnings/errors = collect_inbox(sessions,"yellow"/"red")`;dismiss/dismissAll
  取自 store;`onChoice` 走 control channel(同 App.tsx 既有 handleBubbleChoice
  的 supervisor 选择消息);`onJumpToSession` 桌宠单主线程→打开历史面板兜底。
- 折叠:用户要常驻 → 默认 `collapsed=false`;仍接 onCollapse/onExpand
  (允许用户临时收起,不强制),不破坏组件契约。
- "历史回顾":左面板的滚动流即实时回顾;深度全量历史仍走现有
  ChatHistoryPanel(💬 按钮),不重复造。

## 文件改动(5)
1. 恢复 `tauri-app/src/components/MessageStreamPanel.tsx`(from feat)
2. 恢复 `tauri-app/src/stores/sessionsStore.ts` + `sessionsStore.test.ts`(from feat)
3. `tauri-app/src-tauri/tauri.conf.json` — main 窗 width 400→768 / x 2100→1732
4. `tauri-app/src/App.tsx` — import + 状态(streamCollapsed/streamFilter)+
   streamChat/warnings/errors/handlers + root 改 flex 双栏(左面板玻璃外框 +
   右壳包原内容)

## 不做
- 不动 Code Mode 窗(CodePanelRoot 已 revert,本 feature 仅桌宠窗)
- 不重写 MessageStreamPanel 视觉(复用;精致感放在桌宠窗左面板"外框")
- 不动 sanitizer / chinzy 端点 / 后端

## 验证
- `npx tsc --noEmit` EXIT=0;`npm test`(vitest)全绿,含恢复的
  sessionsStore.test.ts 新增 85 行用例,MessageStreamPanel 既有测试不回归
- 干净重启 stack → computer-use 看桌宠窗:左侧出现精致消息面板,显主线程
  聊天流;造一条 supervisor alert 验 ⚠/🚨 tab;底部 DialogBar 仍在;Hiyori
  视觉位置不变;截图存证
