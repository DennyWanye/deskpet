# Tasks — Async Image Generation

> 单 cohesive 特性，顺序 TDD（worker↔tool↔main 互相依赖，不并行拆）。勾选 = 单测绿 + 终验 real E2E 截图存 evidence/。

## Phase 1 — ImageGenerationWorker

- [ ] 1.1 写 `backend/tests/test_image_worker.py`（fake notifier + fake httpx，TDD 先行）：
  - enqueue 一个 job → _process → notifier 收到成功文案（含 filename/model/size）
  - chinzy 失败（断连/4xx）→ notifier 收到 graceful 文案（不编造）
  - dedup：同 (session,prompt,size) key 第二次 submit 不入队
  - 并发：Semaphore(max_concurrent) 上限生效
  - start/stop 幂等；stop 取消在途
  - run_coroutine_threadsafe 提交（sync 上下文）安全
- [ ] 1.2 实现 `backend/deskpet/memory/image_worker.py`：`ImageJob` + `ImageGenerationWorker`（start/stop/submit/_run_loop/_process）。retry/save/_open_file/endpoint-resolve 逻辑从 image_tools 平移（不重写）
- [ ] 1.3 worker 完成同时 append 完成消息到 SessionDB messages(role=assistant)（与正常回复一致，供 ChatHistoryPanel/L2）

## Phase 2 — image_tools 分流

- [ ] 2.1 调整 `backend/tests/test_generate_image_tool.py`：async_enabled=true → 断言秒返回 `{ok:true,status:"generating",job_id}` 且**未发 HTTP**；async_enabled=false → 现有同步行为（保留覆盖）
- [ ] 2.2 `_handle_generate_image` 分流：读 `[image].async_enabled`。true → 取 worker（service_context）+ `_session_id`（merged args）→ `worker.submit(job)` → 秒返回。false → 旧同步实现（重命名 `_handle_generate_image_sync` 保留）
- [ ] 2.3 同 prompt 在途 → 返回 `status:"already_generating"` 文案

## Phase 3 — main.py 接线

- [ ] 3.1 lifespan：构造 `ImageGenerationWorker(notifier=_image_notifier, max_concurrent=cfg)`；`service_context.register("image_worker", ...)`；`await worker.start()`；shutdown `await worker.stop()`（async_enabled=false 时不 start）
- [ ] 3.2 `_image_notifier(session_id, text)` 闭包：复用 `_control_connections` 广播（`_auto_resume_emit` 同款），发 `{"type":"chat_v2_final","payload":{"text":text}}`
- [ ] 3.3 chat handler 每轮给 image 工具 `set_session_context(_sid, {"_session_id": _sid})`（同 `_write_scope_root` 注入点）
- [ ] 3.4 `config.toml [image] async_enabled=true / max_concurrent=2`

## Phase 4 — 回归 + 验收

- [ ] 4.1 全量 `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q` 绿，0 回归
- [ ] 4.2 tsc（无前端改动，确认 chat_v2_final 路径未破）+ vitest 绿
- [ ] 4.3 **终验 real E2E**（computer-use，单干净栈）：companion 发复杂图"咒术回战 gacha UI" → 桌宠**秒回**"🎨 在画了" → 期间发另一句闲聊确认桌宠**不假死**能正常回 → 数十秒后**无需用户再说话**自动弹"✨ 画好了！" + 图被系统看图器打开 + `workspace/genimg_*.png` 新文件 + backend.log 显示 worker 处理。截图存 `evidence/`
- [ ] 4.4 回退验证：`async_enabled=false` → 退回同步阻塞旧行为单测
- [ ] 4.5 `openspec validate 2026-05-16-async-image-gen` 通过
