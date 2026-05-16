# 2026-05-16 — Async Image Generation (fire-and-quick-return worker)

> **Status**: Proposed · **Effort**: ~1 sprint · **Risk**: medium (new worker + sync→async boundary; mitigated by reusing vector_worker pattern + the proven auto_resume ws broadcast) · **Trigger**: user asked for the long-running-op best practice after repeated sync-blocking pain

## Why

`generate_image` is currently a **synchronous blocking tool**. One call holds the agent loop + chat stream + ToolRegistry `asyncio.wait_for` timeout hostage for 60–240s. Every bug we patched this week traces to this:

- registry timeout vs httpx timeout conflict (killed retries) — 2026-05-16
- pet appears frozen / idle for minutes during generation
- retries balloon total wall time past the registry budget
- connection held open longer → more chinzy `Server disconnected` exposure

**Long-running ops must not block the agent turn.** Industry best practice for slow tool calls in agent systems = async job + completion delivery (the agent turn returns immediately; the slow work runs out-of-band; the result is pushed back when ready). For a single-user desktop pet the right fit is a lightweight in-process async worker + an unsolicited completion message (NOT a full persistent job queue — that's over-engineered here).

## What Changes

- **NEW** `backend/deskpet/memory/image_worker.py` — `ImageGenerationWorker`: async, queue-based, `start()`/`stop()` lifecycle (mirrors `vector_worker.py`). Pulls jobs, runs the chinzy POST + transient-retry + save-to-workspace + `os.startfile` in a background `asyncio` loop. Bounded concurrency; same-(session,prompt,size) in-flight dedup.
- **MODIFIED** `backend/deskpet/tools/image_tools.py` — `_handle_generate_image` no longer blocks: validate args, submit job to the worker (thread-safe sync→async bridge), return **immediately** `{ok:true, status:"generating", job_id, message:"🎨 在画了，稍等~"}`. The slow HTTP/save/open logic moves into the worker (reused, not rewritten).
- **MODIFIED** `backend/main.py` — construct + `service_context.register("image_worker", ...)`, `await worker.start()` in lifespan startup / `stop()` in shutdown; wire the worker's completion notifier to the existing control-ws broadcast (the `_auto_resume_emit` closure pattern) so the worker can push a `chat_v2_final`-shaped event to the job's `session_id`. Inject `_session_id` into the image tool's session context (same mechanism as `_write_scope_root`) so the tool knows where to route completion.
- **REUSED, zero frontend change**: completion/error is delivered as a `chat_v2_final` ws event — the pet bubble's existing handler renders it; `petText` already strips any `<think>`/junk. Pet shows "画好了！已打开 X" or the graceful error, with NO user turn.

**Non-goals (explicit, follow-up)**: persistent/durable job queue (backend restart loses in-flight jobs — acceptable for a desktop pet, user re-asks); job cancellation ("算了不画了" → cancel) — v1 ships dedup + concurrency cap only; pet "drawing" Live2D motion as a working indicator (nice-to-have, separate).

## Capabilities

### New Capabilities

- `async-tools`: the fire-and-quick-return pattern for long-running tools — a tool submits work to an in-process async worker and returns immediately; the worker delivers the result back to the pet out-of-band via the existing control-ws broadcast. `generate_image` is the first adopter; the worker/notifier seam is written so future slow tools (video, long crawls) can reuse it.

### Modified Capabilities

- `agent-loop`: `generate_image` becomes non-blocking — the agent turn completes in <1s instead of waiting 60–240s; no registry-timeout pressure; pet stays responsive.

## Impact

### 代码影响
- 后端 ~300 行：`image_worker.py` ~180（worker + 队列 + 并发/去重 + retry，retry/save/open 逻辑从 image_tools 平移）+ image_tools 改造 ~50（submit + 秒返回）+ main.py 接线 ~40（register/start/stop + notifier + session_id 注入）
- 测试：worker 单测（enqueue→处理→notifier 调用、dedup、并发上限、retry 平移行为）、tool 秒返回单测、sync→async 提交线程安全单测；复用 image_tools 现有 10 测试调整
- 前端：**0 改动**（complete 走现有 `chat_v2_final` 渲染路径）
- 数据库：无（无持久化 — 单机桌宠不值这套）

### 运行时影响
- agent 回合：generate_image 从阻塞 60–240s → 秒返回；registry 超时压力消失
- worker：常驻一个 asyncio 任务循环（同 vector_worker，开销可忽略，空闲时 await 队列）
- 并发上限（默认 2）防同时太多出图打爆 chinzy；同 prompt 去重防误重复计费（$0.15/张）
- 完成推送：复用 `_auto_resume_emit` 广播，单条 ws 消息，非热路径

### 兼容性
- `config.toml [image].async_enabled`（默认 true）feature flag：false → 退回同步阻塞旧行为（Strangler-Fig，保留旧 `_handle_generate_image` sync 路径作回退）
- 工具名/schema 不变（agent 调用方式不变，capability_gate marker 仍命中）
- 完成消息走 `chat_v2_final` → 前端 + petText 既有处理，无需改
