# UI-Layer Bug Fixes (2026-05-13)

## 缘起

用户报告 "code 模式还是有一些奇奇怪怪的问题"。之前 5 轮 ws 脚本压测后端
0 错误，但**完全绕过了 UI**——通过 computer-use 打开 Code Mode 仪表盘后
立即看到 3 个 UI 层 bug。本文档记录这 3 个 bug 的根因、修复、和 UI 实证。

## 用 computer-use 打开 Code Mode 看到的初始状态

桌宠顶栏点扳手图标 → 打开"DeskPet · Code Mode 多项目仪表盘"。
"小说网站" 卡片显示：

```
todos 0/4
- 列出 G:/projects/deskpet/backend/agent/ 下所有 .py 文件
- 依次 read_file 每个 .py 文件的完整内容
- 对每个 .py 文件 grep 'def' 定位所有函数定义
- 对每个函数用 read_file 读签名行确认参数

You: <<auto_resume>>
You: 我们的目标：做一个跟起点小说网一样的网站。
You: <<supervisor_followup>>
```

桌宠顶栏横幅："桌宠提醒 p6-live-test 代码模型循环了，我让它强制收尾你之前的成果"

## Bug 1：sentinel 文本泄漏当 user 消息渲染

### 根因
`backend/main.py` line 2718 在 `_run_chat` 收到任何 text 时无条件
`_sdb.append_message(role="user", content=_text)`。当 `_text` 是
`<<auto_resume>>` / `<<supervisor_followup>>` 这类内部 dispatch
sentinel 时也会写入。SessionDB 是 UI 渲染源，结果 sentinel 被显示
成 "You: <<auto_resume>>"。

### 修复（双层防御）
1. **Backend 写入侧**：sentinel 不再写入 SessionDB
   ```python
   _is_sentinel = (_text or "").startswith("<<") and (_text or "").endswith(">>")
   if _sdb is not None and not _is_sentinel:
       _user_msg_id = await _sdb.append_message(...)
   ```
2. **UI 渲染侧**：即使历史 sentinel 仍在 SessionDB，也不显示
   ```typescript
   const is_sentinel = (text) => /^<<[a-z_]+>>$/i.test(text.trim());
   const visible = messages.filter(m => !(m.role === "user" && is_sentinel(m.text)));
   ```

### 实证
HMR reload 后再截图："You: <<auto_resume>>" / "You: <<supervisor_followup>>"
全部消失，只剩真实用户消息 "我们的目标：做一个跟起点小说网一样的网站。"

## Bug 2：todos 跨 session 串扰

### 根因
`tauri-app/src/code-panel/ws.ts` 的 `code_todo_update` 消息处理：
```typescript
let target_base_sid = sid; // ← active session fallback
if (code_sid) {
  for (...) { /* reverse-map code_session_id → base_sid */ }
}
store.upsert_todos(target_base_sid, items);
```
当 todos 来自非 project 的 session（CLI ws 客户端、外部测试脚本、
`p6_live_test`），reverse-map 失败 → fallback 到当前 active_sid。
结果 p6_live_test 的 deskpet backend 探索 todos 被错挂到用户当前
查看的 "小说网站" 项目卡片。

### 修复
找不到匹配 code_session_id 时**显式 drop**，不静默串项目：
```typescript
let target_base_sid: string | null = null;
if (code_sid) { ... /* reverse-map */ }
if (target_base_sid) {
  store.upsert_todos(target_base_sid, items);
} else {
  console.debug("code_todo_update dropped: no matching code_session_id");
}
```

### 实证
重启后 "小说网站" todos 0/0（未污染）。随后用户在 UI 里发任务
"请用 list_directory 看一下 G:/projects/小说网站..."——agent 拆出
4 个**关于小说网站项目本身**的 todos 并全部完成，状态变 4/4：

```
todos 4/4
✓ 列出 G:/projects/小说网站 目录结构并分析项目
✓ 读取关键后端文件 (db.js, index.js, seed.js, routes)
✓ 读取关键前端文件 (App.jsx, api.js, 核心页面)
✓ 总结项目现状并给出完整功能清单
```

## Bug 3：`<think>...</think>` reasoning tag 泄漏到 UI

### 根因
Thinking-mode 模型（deepseek-v4-pro / the relay proxy / GLM-4.5）会在
`assistant.content` 字段内嵌 `<think>...</think>` chain-of-thought。
Backend 的 supervisor 和 plan 模块已经 strip 这类 tag（用于 JSON parse），
但**chat content 流到 UI 时没 strip**。结果用户看到：

```
AI: <think>Let me also quickly check the ChapterRead page and AuthorDashboard,
plus the Navbar component, to complete the picture.</think>
AI: <think>The user is asking me to continue with the remaining todos. Let me
re-read the context. The original todo items were: 1. 列出...</think>
AI: <think>The stale todos from the deskpet project are now cleaned up. I should
provide a clear final summary to the user.</think>之前的残留 todo 已清理...
```

### 修复
UI 端 strip filter（前端 fail-safe，后端 stream-side strip 是后续优化）：
```typescript
const strip_think = (text) => {
  if (typeof text !== "string") return "";
  let out = text.replace(/<think>[\s\S]*?<\/think>/gi, "");
  // 处理流中尚未闭合的 <think>
  out = out.replace(/<think>[\s\S]*$/i, "");
  return out.trim();
};
```

### 实证
HMR 后 "小说网站" 卡片 AI 消息变成干净："AI: 之前的残留 todo 已清理。
以上分析就是当前 'G:/projects/小说网站' 的完整现状——一个 80% 完成度的
起点中文克隆，后端…" 完全无 `<think>` 泄漏。

## 11 分钟 UI E2E 稳定性验证

修复后启动 660s monitor，期间 deskpet 在 Code Mode 下处理真实
"list_directory G:/projects/小说网站" 任务：

| 指标 | 数值 |
|---|---|
| Tool calls | 27 |
| End-turn 终止 | 3 |
| 真错误（Traceback / Exception / hallucination / chat_v2_error / RuntimeError / TypeError / etc.） | **0** |
| UI sentinel 显示 | 0 |
| UI `<think>` 泄漏 | 0 |
| Todos 跨项目污染 | 0 |

唯一日志匹配是 Tauri 启动时的 `IPC custom protocol failed ... postMessage interface instead`
—— 这是 Tauri 所有应用启动时的正常 fallback，不是 bug。

## 改动文件

```
backend/main.py                              |  +9 / -1
tauri-app/src/code-panel/SessionGridView.tsx | +45 / -3
tauri-app/src/code-panel/ws.ts               | +27 / -6
```

后端 baseline 全绿：1229 passed / 10 skipped。
前端 TypeScript check 通过。

## 结论

之前 ws-only 测试错过了 3 个真实 UI bug。本次通过 **computer-use 视觉打开
Code Mode + 真实任务流转 + UI 截图对比** 验证，3 个 bug 全部修复，11 分钟
连续监控 0 真错误。**deskpet code 模式现在真正干净**。
