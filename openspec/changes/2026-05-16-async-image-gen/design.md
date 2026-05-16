# Design — Async Image Generation

> 配套 proposal.md。关键决策 + 数据结构 + 集成点。探索依据见 proposal §Why。

## D1 — Worker 而非裸 detached task

**决策**：用常驻 `ImageGenerationWorker`（仿 `backend/deskpet/memory/vector_worker.py`），不用每次 `asyncio.create_task` 裸任务。

理由：vector_worker/summarizer 已是本仓库 proven 模式 → 一致性 > 取巧；worker 给天然的生命周期（start/stop）、并发闸、去重表、优雅停。裸 detached task 难追踪、难限并发、backend 关闭时悬空。

```
ImageGenerationWorker:
  __init__(notifier, *, max_concurrent=2)
  async start()  -> asyncio.create_task(self._run_loop(), name="image-worker")
  async stop()   -> set stop_event; cancel in-flight; await loop task
  submit(job: ImageJob) -> str(job_id)   # 线程安全，见 D3
  async _run_loop():  从 asyncio.Queue 取 job；asyncio.Semaphore(max_concurrent)
                      限并发；每 job → asyncio.create_task(self._process(job))
  async _process(job): chinzy POST + retry + save + open（逻辑从 image_tools
                       平移，不重写）→ 成功/失败都调 notifier
```

`ImageJob = {job_id, session_id, prompt, size, model, ts}`。

## D2 — 完成投递：复用 chat_v2_final 广播（零前端改动）

**决策**：worker 完成时，通过 main.py 已有的 control-ws 广播闭包（`_auto_resume_emit` 同款）发一条 **`chat_v2_final`** 形状的事件给该 job 的 `session_id`。

理由：前端 App.tsx `case "chat_v2_final"` 已把 payload.text 作为 assistant 消息推进 messages → 经 `petText` 过滤（剥 think/trace）→ 桌宠气泡显示。复用这条路径 = **0 前端改动**，且自动享受 petText 干净显示 + ChatHistoryPanel 完整保留。

notifier 注入（解耦 worker 与 ws）：

```python
# main.py 构造 worker 时传入 notifier 闭包
async def _image_notifier(session_id: str, text: str) -> None:
    # 复用 _auto_resume_emit 同款：遍历 _control_connections 找 session
    # 发 {"type":"chat_v2_final","payload":{"text": text}}
worker = ImageGenerationWorker(notifier=_image_notifier, max_concurrent=2)
```

成功文案：`✨ 画好了！已保存并打开：<filename>（model gpt-image-2，1024×1024）`
失败文案：复用现同步版 `_err` 的 graceful hint（chinzy 抽风/4xx 等），保持诚实不编造。

**也持久化到 SessionDB**：完成消息除 ws 推送外，append 到 messages（role=assistant），这样 ChatHistoryPanel/L2 召回有记录（和正常 assistant 回复一致）。

## D3 — sync→async 提交边界

**问题**：tool handler 是 sync `(args, task_id)->str`；worker 队列是 asyncio。

**决策**：worker 启动时捕获自身 loop（`self._loop = asyncio.get_running_loop()`）。`submit()` 是普通 sync 方法，内部
`asyncio.run_coroutine_threadsafe(self._queue.put(job), self._loop)`（execute_tool 把 sync handler 跑在 `loop.run_in_executor`，所以 handler 在 worker loop 的某个线程里，run_coroutine_threadsafe 是安全的标准做法）。submit 立即返回 job_id，不 await 处理。

## D4 — session_id 注入（路由完成消息）

**问题**：sync handler 不直接收 session_id（registry.execute_tool 有但不透传给 handler）。

**决策**：复用 `_write_scope_root` 同款 `set_session_context` 注入。chat handler 每轮给 image 工具注入 `{"_session_id": _sid}`；handler 从 merged args 取 `_session_id` 放进 ImageJob。worker 完成时 notifier 按该 session_id 路由（找 `_control_connections[sid]`，无则广播全部 control conn，与 auto_resume 行为一致）。

## D5 — 并发 + 去重

- `asyncio.Semaphore(max_concurrent=2)`：最多 2 个出图同时打 chinzy（防打爆 + 控成本）
- 去重：worker 维护 `_inflight: set[str]`，key = `sha1(f"{session_id}|{prompt}|{size}")`。submit 时若 key 在 _inflight → 不入队，job_id 返回特殊标记，tool 回 `{ok:true, status:"already_generating", message:"🎨 同样的图正在画了，稍等~"}`。_process 结束（成功/失败）从 _inflight 移除。
- 不做持久化/取消（proposal 明确 non-goal；取消是 follow-up）

## D6 — Strangler-Fig 回退

`config.toml [image].async_enabled`（默认 true）。false → `_handle_generate_image` 走保留的旧同步阻塞实现（现有 retry+timeout 那套，重命名 `_handle_generate_image_sync`，分流）。worker 不 start。保证一键回退。

## 集成点

| 文件 | 改动 |
|---|---|
| `backend/deskpet/memory/image_worker.py`(新) | ImageGenerationWorker + ImageJob；retry/save/open 逻辑从 image_tools 平移 |
| `backend/deskpet/tools/image_tools.py` | `_handle_generate_image` 分流：async→submit 秒返回；sync→旧实现保留。`_resolve_endpoint`/`_image_model`/`_workspace_dir`/`_open_file` 复用（worker import 它们或平移）|
| `backend/main.py` | register/start/stop worker；`_image_notifier` 闭包（复用 _control_connections 广播）；每轮给 image 工具 set_session_context `_session_id` |
| `config.toml` | `[image].async_enabled=true` + `max_concurrent=2` |

## 测试策略

- worker 单测（fake notifier + fake httpx）：enqueue→_process→notifier 收到成功文案；失败→notifier 收到 graceful 文案；dedup（同 key 第二次不入队）；并发 Semaphore 上限；start/stop 幂等
- tool 单测：async_enabled=true → 秒返回 `status:generating` + job_id，**不**做 HTTP；async_enabled=false → 走旧同步路径（现有 10 测试覆盖）
- sync→async 提交线程安全单测（run_coroutine_threadsafe 路径）
- 终验 real E2E（computer-use）：companion 发复杂图 → 桌宠秒回"在画了" → 数十秒后**无需用户再说话**自动弹"画好了！" + 图被打开 + workspace 有新 png；期间桌宠不假死、能继续聊别的。截图存 evidence。
