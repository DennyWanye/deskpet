"""ImageGenerationWorker — async fire-and-quick-return for slow image gen.

OpenSpec 2026-05-16-async-image-gen. `generate_image` was a synchronous
blocking tool: one call held the agent loop + chat stream +
ToolRegistry timeout for 60–240s (every image bug this week traced to
that). This worker decouples it:

  tool submit(job)  → returns a job_id immediately (agent turn unblocks)
  worker _process   → slow chinzy POST + retry + save + open (background)
  notifier(sid,txt) → pushes the result back to the pet out-of-band
                      (reuses main.py's control-ws chat_v2_final broadcast)

Mirrors `vector_worker.py` lifecycle (start/stop, asyncio.Queue, stop
event). NOT persistent — a desktop pet doesn't need durable jobs; a
backend restart loses in-flight gen and the user just re-asks.

The slow HTTP/save/open logic is NOT duplicated here — it's reused from
`deskpet.tools.image_tools` (`_generate_png` / `_save_image` /
`_open_file`), the single source of truth.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# notifier(session_id, text) — async; delivers the (success|error)
# message back to the pet. Injected by main.py (control-ws broadcast).
Notifier = Callable[[str, str], Awaitable[None]]


@dataclass
class ImageJob:
    session_id: str
    prompt: str
    size: str
    model: str
    job_id: str = field(default_factory=lambda: secrets.token_hex(6))

    def dedup_key(self) -> str:
        raw = f"{self.session_id}|{self.prompt}|{self.size}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()


class ImageGenerationWorker:
    def __init__(
        self,
        notifier: Notifier,
        *,
        max_concurrent: int = 2,
        queue_max: int = 64,
    ) -> None:
        self._notifier = notifier
        self._max_concurrent = max(1, int(max_concurrent))
        self._queue: asyncio.Queue[ImageJob] = asyncio.Queue(maxsize=queue_max)
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sema: Optional[asyncio.Semaphore] = None
        # in-flight dedup: dedup_key -> True. Added at submit, removed
        # when _process finishes (success or fail).
        self._inflight: set[str] = set()
        self._inflight_lock = asyncio.Lock()
        self._active: set[asyncio.Task] = set()

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return  # idempotent
        self._loop = asyncio.get_running_loop()
        self._sema = asyncio.Semaphore(self._max_concurrent)
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="image-worker"
        )
        log.info(
            "image_worker started (max_concurrent=%d)", self._max_concurrent
        )

    async def stop(self) -> None:
        self._stop_event.set()
        for t in list(self._active):
            t.cancel()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        log.info("image_worker stopped")

    # ---------------- submit (sync-safe) ----------------

    def submit(
        self, *, session_id: str, prompt: str, size: str, model: str
    ) -> tuple[str, str]:
        """Thread-safe submit from a SYNC tool handler.

        Returns (status, job_id). status ∈ {"queued",
        "already_generating", "unavailable"}. Never blocks on generation.
        """
        job = ImageJob(
            session_id=session_id, prompt=prompt, size=size, model=model
        )
        if self._loop is None or self._task is None or self._task.done():
            return "unavailable", ""
        key = job.dedup_key()
        # dedup is best-effort sync check against the set (set ops are
        # atomic under GIL); the authoritative add happens on the loop.
        if key in self._inflight:
            return "already_generating", ""
        self._inflight.add(key)
        # Production: sync tool handler runs in loop.run_in_executor →
        # a DIFFERENT thread than the loop → run_coroutine_threadsafe.
        # Tests / on-loop callers: same thread → put_nowait directly
        # (asyncio.Queue.put_nowait is safe from the loop thread).
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is self._loop:
                self._queue.put_nowait(job)
            else:
                fut = asyncio.run_coroutine_threadsafe(
                    self._enqueue(job), self._loop
                )
                fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001 — queue full / loop gone
            self._inflight.discard(key)
            log.warning("image_worker submit failed: %r", exc)
            return "unavailable", ""
        return "queued", job.job_id

    async def _enqueue(self, job: ImageJob) -> None:
        await self._queue.put(job)

    # ---------------- run loop ----------------

    async def _run_loop(self) -> None:
        assert self._sema is not None
        while not self._stop_event.is_set():
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            t = asyncio.create_task(self._process_guarded(job))
            self._active.add(t)
            t.add_done_callback(self._active.discard)

    async def _process_guarded(self, job: ImageJob) -> None:
        assert self._sema is not None
        try:
            async with self._sema:
                await self._process(job)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — loop must survive
            log.exception("image_worker _process crashed: %s", exc)
        finally:
            self._inflight.discard(job.dedup_key())

    async def _process(self, job: ImageJob) -> None:
        loop = asyncio.get_running_loop()
        # Slow blocking gen runs in executor (sync httpx). Reuse the
        # single copy of the logic from image_tools.
        from deskpet.tools.image_tools import (
            _generate_png,
            _open_file,
            _save_image,
        )

        png, err = await loop.run_in_executor(
            None, _generate_png, job.prompt, job.size, job.model
        )
        if png is None:
            await self._notify(
                job.session_id,
                f"😿 图没画成 —— {err or '未知错误'}",
            )
            return
        try:
            out = await loop.run_in_executor(None, _save_image, png)
        except Exception as exc:  # noqa: BLE001
            await self._notify(
                job.session_id, f"😿 图生成了但写入失败：{exc}"
            )
            return
        opened = await loop.run_in_executor(None, _open_file, out)
        opened_txt = "已自动打开给你看了" if opened else "（自动打开没成功，文件在 workspace）"
        await self._notify(
            job.session_id,
            f"✨ 画好啦！{opened_txt}\n"
            f"📁 {out.name}　🎨 {job.model}　📐 {job.size}",
        )

    async def _notify(self, session_id: str, text: str) -> None:
        try:
            await self._notifier(session_id, text)
        except Exception as exc:  # noqa: BLE001 — notify must never crash worker
            log.warning("image_worker notify failed: %s", exc)
