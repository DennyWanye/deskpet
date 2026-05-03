"""P4-S2 L3 embedding service — BGE-M3 + mock fallback.

封装 FlagEmbedding ``BGEM3FlagModel``，对外暴露统一 async API：
``warmup()`` / ``encode(texts)`` / ``is_ready()`` / ``is_mock()`` /
``close()``。

核心设计决定
-------------
* **Non-blocking warmup**：``warmup()`` 是 async 且在 executor 里跑同步
  模型加载；应用启动可 fire-and-forget。``is_ready()`` 在加载完成前返回
  False，其它 slice 的 ``check_fn`` 据此 gate 自己的工具可用性。
* **Mock fallback**：BGE-M3 权重 ~286MB，桌宠首装可能还没下载完。构造
  时 ``use_mock_when_missing=True`` 检测到目标目录不存在就切到 mock
  模式——用 ``hashlib.md5(text.encode()).digest()`` 作 seed 生成稳定
  dim=1024 float32 向量，保证测试可复现。
* **线程安全**：FlagEmbedding 的 BGEM3FlagModel **不是** 线程安全的，
  所以真模型模式下 ``encode`` 通过 ``run_in_executor`` 串行化到单一
  worker thread；mock 模式无此约束，走 asyncio.Lock 保持行为一致。
* **设备选择**：``device="auto"`` 时若 ``torch.cuda.is_available()`` 则
  ``cuda``，否则 ``cpu``。用户可通过构造参数或 config 硬覆写。

Not here（留给后续 slice）：
    * 向量写 ``messages_vec`` → ``vector_worker.py``
    * 混合召回 RRF → P4-S3 ``retriever.py``

Ref: spec "Vector Memory (L3) — sqlite-vec + BGE-M3" Scenario
     "Async embedding write" / tasks.md §4.1 §4.2 §4.4.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# 模型维度与 config.toml [memory.l3].vector_dim / messages_vec DDL 对齐。
# 改这个值等同改 schema —— 不要轻易动。
EMBEDDING_DIM = 1024


def _default_model_path() -> Path:
    """BGE-M3 weights 默认目录：``%LocalAppData%\\deskpet\\models\\bge-m3-int8``。

    沿用 ``backend/scripts/download_bge_m3.py`` 的约定（SUBDIR="bge-m3-int8"）。
    我们这里不 import ``deskpet.paths``（避免循环），直接用 platformdirs
    解析，和下载脚本保持一致。
    """
    try:
        import platformdirs
    except ImportError:
        # 极端情况：platformdirs 不可用 → 给一个相对路径，反正后续
        # exists() 会返回 False 触发 mock 降级。
        return Path("./models/bge-m3-int8")

    local_base = Path(
        platformdirs.user_data_dir("deskpet", appauthor=False, roaming=False)
    )
    return (local_base / "models" / "bge-m3-int8").resolve()


def _mock_vector(text: str, dim: int = EMBEDDING_DIM) -> np.ndarray:
    """从 md5(text) 生成稳定的 dim 维 float32 向量。

    同文本两次调用 → 完全相同的向量（np.random.RandomState 种子固定）。
    归一化到单位长度，这样 cosine(self, self)=1.0，对测试更友好。
    """
    # md5 digest = 16 bytes → 转 int 作种子。np.random.default_rng 接受 int。
    digest = hashlib.md5(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    # 单位化：mock 输出的分布和真 BGE-M3（L2 normalized）尽量贴近。
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


class Embedder:
    """BGE-M3 async embedder wrapper with mock fallback.

    典型用法（主启动路径）::

        embedder = Embedder()
        # fire-and-forget：不阻塞主 loop
        asyncio.create_task(embedder.warmup())
        # 别的 slice 暂时用 is_ready()=False gate 工具，
        # 模型加载完后自动变 True。

    测试路径（强制 mock）::

        embedder = Embedder(model_path=Path("/nonexistent"), use_mock_when_missing=True)
        await embedder.warmup()
        assert embedder.is_mock()
        vecs = await embedder.encode(["hi", "hello"])
        assert vecs.shape == (2, 1024)
    """

    def __init__(
        self,
        model_path: Path | None = None,
        device: str = "auto",
        *,
        use_mock_when_missing: bool = True,
        mode: str = "subprocess",
    ) -> None:
        """Construct an Embedder.

        Parameters
        ----------
        model_path:
            Absolute path to the BGE-M3 model dir.
        device:
            ``"auto"`` / ``"cuda"`` / ``"cpu"``.
        use_mock_when_missing:
            On model dir missing or load failure, fall back to mock embedder
            instead of raising.
        mode:
            P4-S19 isolation mode:

            - ``"subprocess"`` (default, recommended): spawn a Python
              subprocess that loads BGE-M3 and exchanges JSON-RPC over
              stdin/stdout. Avoids the PyTorch+ctranslate2 cross-thread
              CUDA segfault (P4-S18) since the worker doesn't import
              ctranslate2.
            - ``"inprocess"``: legacy in-process FlagEmbedding load with
              ThreadPoolExecutor. **Will segfault** when faster_whisper /
              ctranslate2 is also imported in the same process. Kept for
              standalone test scripts that don't have ctranslate2 around.
            - ``"mock"``: skip the real model entirely, use md5-hash
              vectors. Useful for unit tests + CI.
        """
        self._model_path = Path(model_path) if model_path else _default_model_path()
        self._device_pref = device  # "auto" | "cuda" | "cpu"
        self._use_mock_when_missing = use_mock_when_missing
        if mode not in ("subprocess", "inprocess", "mock"):
            raise ValueError(f"unknown mode {mode!r}")
        self._mode = mode

        self._model: Any = None  # FlagEmbedding BGEM3FlagModel or None (inprocess only)
        self._is_mock = False
        self._is_ready = False
        # 真模型加载 + encode 都跑在单线程 executor 里（FlagEmbedding 非线程安全）
        # subprocess mode 下用不到 executor。
        self._executor: ThreadPoolExecutor | None = None
        # async 路径的序列化：确保同时只有一个 encode 任务进 executor / 子进程，
        # 避免队列里塞一堆任务把 model 状态交叉污染。
        self._lock = asyncio.Lock()
        # warmup 幂等保护：多次调用只真正加载一次
        self._warmup_started = False
        # P4-S19 subprocess handle (only used when mode='subprocess')
        self._proc: Any = None  # asyncio.subprocess.Process or None
        self._next_request_id = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def warmup(self) -> None:
        """加载模型。幂等：多次调用只真正加载一次。

        * model_path 不存在 + use_mock_when_missing=True → mock 模式，
          is_ready 立即变 True。
        * model_path 存在但 FlagEmbedding 加载失败 + use_mock_when_missing=True
          → 降级 mock + log warning。
        * 加载失败 + use_mock_when_missing=False → raise。
        """
        if self._warmup_started:
            return
        self._warmup_started = True

        # 1. 检查模型目录是否就位
        if not self._model_path.exists():
            if self._use_mock_when_missing:
                log.warning(
                    "BGE-M3 model dir not found at %s; using mock embedder "
                    "(install model via backend/scripts/download_bge_m3.py)",
                    self._model_path,
                )
                self._is_mock = True
                self._is_ready = True
                return
            raise RuntimeError(
                f"BGE-M3 model not found at {self._model_path} and "
                "use_mock_when_missing=False"
            )

        # P4-S19: dispatch by mode.
        # - mock: skip real load entirely (unit tests, CI)
        # - subprocess (default): spawn embedder_worker.py for full isolation
        #   from ctranslate2/PyTorch cross-thread CUDA segfault (P4-S18).
        # - inprocess: legacy path, in-process FlagEmbedding load (will
        #   segfault when faster_whisper is also imported; kept for
        #   standalone scripts only).
        if self._mode == "mock":
            log.info("BGE-M3 mock mode (Embedder.mode='mock')")
            self._is_mock = True
            self._is_ready = True
            return

        if self._mode == "subprocess":
            try:
                await self._spawn_subprocess_worker()
                self._is_ready = True
                log.info(
                    "BGE-M3 subprocess worker ready (path=%s, device=%s)",
                    self._model_path,
                    self._resolved_device(),
                )
                return
            except Exception as exc:  # noqa: BLE001
                if self._use_mock_when_missing:
                    log.warning(
                        "BGE-M3 subprocess worker failed (%s); "
                        "falling back to mock embedder",
                        exc,
                    )
                    self._is_mock = True
                    self._is_ready = True
                    return
                raise

        # mode == "inprocess" — legacy. Will segfault if ctranslate2 also loaded.
        try:
            await self._load_real_model()
            self._is_ready = True
            log.info(
                "BGE-M3 embedder ready (in-process, path=%s, device=%s)",
                self._model_path,
                self._resolved_device(),
            )
        except Exception as exc:  # noqa: BLE001
            if self._use_mock_when_missing:
                log.warning(
                    "BGE-M3 in-process load failed (%s); falling back to mock embedder",
                    exc,
                )
                self._is_mock = True
                self._is_ready = True
                self._model = None
                if self._executor is not None:
                    self._executor.shutdown(wait=False)
                    self._executor = None
                return
            raise

    async def _load_real_model(self) -> None:
        """在线程池里 sync 加载 FlagEmbedding ``BGEM3FlagModel``。

        把阻塞的 torch/transformer 加载放 executor，主 event loop 可
        继续响应其它 coroutine（这是 "non-blocking warmup" 的关键）。
        """
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="embedder"
        )

        device = self._resolved_device()

        def _sync_load() -> Any:
            # Lazy import：模块级 import FlagEmbedding 会拖慢所有 mock
            # 场景的启动（transformers 很重）。只在真加载时 import。
            from FlagEmbedding import BGEM3FlagModel  # type: ignore

            # use_fp16 仅在 GPU 上开；CPU 用 fp16 反而更慢。
            use_fp16 = device == "cuda"
            model = BGEM3FlagModel(
                str(self._model_path),
                use_fp16=use_fp16,
                device=device,
            )
            return model

        loop = asyncio.get_running_loop()
        self._model = await loop.run_in_executor(self._executor, _sync_load)

    def _resolved_device(self) -> str:
        """把 "auto" 解析成 "cuda" / "cpu"。其它字符串原样返回。"""
        if self._device_pref != "auto":
            return self._device_pref
        try:
            import torch  # type: ignore

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            # torch 不可用：通常不会走到这里（FlagEmbedding 依赖 torch），
            # 但 mock 路径也可能调 _resolved_device。兜底 cpu。
            return "cpu"

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    async def encode(self, texts: list[str]) -> np.ndarray:
        """把一批文本转成 (N, EMBEDDING_DIM) float32 矩阵。

        * 空 list → 返回 shape=(0, EMBEDDING_DIM) 的空数组，**不** raise。
        * 还没 warmup → 内部自动触发一次 warmup（幂等），保证 encode
          不依赖调用方先手动 warmup。
        """
        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

        if not self._warmup_started:
            await self.warmup()

        async with self._lock:
            if self._is_mock:
                return self._encode_mock(texts)
            if self._mode == "subprocess":
                return await self._encode_subprocess(texts)
            return await self._encode_real(texts)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """``encode`` adapter for the classifier protocol (P4-S15).

        ``ContextAssembler.classifier.TaskClassifier`` was written against
        an ``embed(texts) -> list[list[float]]`` protocol (see
        ``tests/test_deskpet_context_assembler.py::FakeEmbedder``), while
        :class:`Retriever` uses the canonical ``encode(texts) -> ndarray``.
        Both protocols now coexist on the same object so production wiring
        passes the live :class:`Embedder` to either consumer without an
        extra adapter layer.
        """
        if not texts:
            return []
        arr = await self.encode(texts)
        return [list(map(float, row)) for row in arr]

    def _encode_mock(self, texts: list[str]) -> np.ndarray:
        """Mock: 每条文本 md5 种子生成稳定向量。"""
        rows = [_mock_vector(t, EMBEDDING_DIM) for t in texts]
        return np.stack(rows).astype(np.float32)

    async def _encode_real(self, texts: list[str]) -> np.ndarray:
        """Real: FlagEmbedding model.encode 跑在 executor 里串行。"""
        if self._model is None or self._executor is None:
            # 理论上 is_ready=True 时不会走到（warmup 要么 mock 要么
            # 真模型就绪），但 defensive check 防止被 close 后仍被调。
            raise RuntimeError("embedder not ready (model is None)")

        def _sync_encode() -> np.ndarray:
            # FlagEmbedding 返回 dict：{'dense_vecs': np.ndarray, ...}
            out = self._model.encode(
                texts,
                batch_size=8,  # 与 config.toml [memory.l3].batch_size 对齐
                max_length=512,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            vecs = out["dense_vecs"]
            # 确保 float32（BGE-M3 默认 fp32，但 fp16 模式下可能是 fp16）
            return np.asarray(vecs, dtype=np.float32)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, _sync_encode)

    # ------------------------------------------------------------------
    # P4-S19 subprocess worker (default isolation mode)
    # ------------------------------------------------------------------

    async def _spawn_subprocess_worker(self) -> None:
        """Spawn ``deskpet.memory.embedder_worker`` and ``ping`` it ready.

        Raises if the subprocess fails to load the model — caller falls
        back to mock if ``use_mock_when_missing=True``.
        """
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"BGE-M3 model dir not found at {self._model_path}"
            )

        device = self._resolved_device()
        # Use the same Python interpreter that's running the backend.
        # Inherit env so HF_HOME / CUDA_VISIBLE_DEVICES propagate.
        log.info(
            "Spawning BGE-M3 subprocess worker (path=%s device=%s)",
            self._model_path, device,
        )
        # P4-S19 NOTE: 不能同时 pipe stdout + stderr。Windows ProactorEventLoop
        # 在两个 PIPE 都 open 时会 race condition 导致 stdout.readline() 永远
        # 不返回（但 subprocess.Popen 同步模式正常）。验证：去掉 stderr=PIPE
        # 后 first ready line 6s 内到达。
        # 后果：worker stderr 直接继承父 backend 的 stderr — transformers 警告
        # 和 Python traceback 会出现在 backend log，反而方便排查。
        # P4-S19 LIMIT BUMP: readline() 默认 64KB；100 句 batch 的 base64-f32
        # JSON ≈ 540KB > 64KB 会 LimitOverrunError。设 16MB 留余量（够 batch
        # 3000 句）。
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-X", "utf8",  # force UTF-8 stdio so JSON of Chinese text round-trips
            "-m", "deskpet.memory.embedder_worker",
            "--model-path", str(self._model_path),
            "--device", device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # stderr=不 PIPE — 继承父进程
            limit=16 * 1024 * 1024,  # 16 MB stdout buffer per readline
        )

        # First read: heartbeat ("spawned"). Confirms child process started.
        try:
            spawn_line = await asyncio.wait_for(
                self._proc.stdout.readline(),  # type: ignore[union-attr]
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                "embedder worker did not emit spawn heartbeat in 10s"
            )
        if not spawn_line:
            raise RuntimeError(
                "embedder worker died before spawn heartbeat "
                "(check parent stderr for traceback)"
            )

        # Second read: "ready" / "fatal" envelope after model load.
        first_line = await asyncio.wait_for(
            self._proc.stdout.readline(),  # type: ignore[union-attr]
            timeout=120.0,  # BGE-M3 cold load can take ~5-10s, GPU warmup ~30s
        )
        if not first_line:
            raise RuntimeError(
                "embedder worker died during model load "
                "(spawn ok, but FlagEmbedding/torch import or .load crashed; "
                "check parent stderr for worker traceback)"
            )
        try:
            envelope = json.loads(first_line.decode("utf-8").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"embedder worker emitted non-JSON first line: {first_line!r} ({exc})"
            ) from exc

        if not envelope.get("ok"):
            err = envelope.get("error", "unknown")
            raise RuntimeError(f"embedder worker fatal at load: {err}")
        if not envelope.get("ready"):
            raise RuntimeError(f"embedder worker first line not 'ready': {envelope}")
        log.info(
            "BGE-M3 worker ready in %.1fms (device=%s)",
            envelope.get("load_elapsed_ms", 0.0),
            envelope.get("device", "?"),
        )

    async def _encode_subprocess(self, texts: list[str]) -> np.ndarray:
        """Send an ``encode`` request to the subprocess worker via JSON-RPC."""
        if self._proc is None or self._proc.returncode is not None:
            # Worker died — try one restart before giving up.
            log.warning("embedder subprocess gone; trying one respawn")
            await self._spawn_subprocess_worker()

        self._next_request_id += 1
        req_id = self._next_request_id
        request = {
            "id": req_id,
            "method": "encode",
            "texts": list(texts),
            "batch_size": 8,
            "max_length": 512,
        }
        line = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(line)  # type: ignore[union-attr]
            await self._proc.stdin.drain()  # type: ignore[union-attr]
            response_line = await asyncio.wait_for(
                self._proc.stdout.readline(),  # type: ignore[union-attr]
                timeout=60.0,
            )
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError) as exc:
            raise RuntimeError(f"embedder subprocess RPC failed: {exc}") from exc

        if not response_line:
            raise RuntimeError(
                "embedder subprocess returned empty response "
                "(check parent stderr for worker output)"
            )

        envelope = json.loads(response_line.decode("utf-8").strip())
        if envelope.get("id") != req_id:
            raise RuntimeError(
                f"embedder RPC id mismatch: sent={req_id} got={envelope.get('id')}"
            )
        if not envelope.get("ok"):
            raise RuntimeError(
                f"embedder encode failed: {envelope.get('error_type')}: "
                f"{envelope.get('error')}"
            )

        # P4-S19: prefer compact base64-f32 (~4KB/sentence). Fall back to
        # legacy list-of-floats payload for back-compat with older workers.
        if envelope.get("encoding") == "base64-f32":
            import base64

            shape = tuple(envelope["shape"])
            raw = base64.b64decode(envelope["vectors_b64"])
            return np.frombuffer(raw, dtype=np.float32).reshape(shape).copy()
        return np.asarray(envelope["vectors"], dtype=np.float32)

    # ------------------------------------------------------------------
    # Status / lifecycle
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """True 表示 encode 可用（mock 或 real 都算）。"""
        return self._is_ready

    def is_mock(self) -> bool:
        """True 表示当前走 mock 路径。"""
        return self._is_mock

    async def close(self) -> None:
        """释放 executor + model + subprocess worker。幂等。"""
        # P4-S19: 优雅停 subprocess worker
        if self._proc is not None and self._proc.returncode is None:
            try:
                # Try graceful shutdown command first
                if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                    self._proc.stdin.write(
                        (json.dumps({"method": "shutdown"}) + "\n").encode("utf-8")
                    )
                    try:
                        await asyncio.wait_for(self._proc.stdin.drain(), timeout=1.0)
                    except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                        pass
                # Wait briefly for it to exit on its own
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except Exception as exc:  # noqa: BLE001
                log.warning("embedder subprocess close failed: %s", exc)
            finally:
                self._proc = None

        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        # FlagEmbedding 没有显式 close；让 GC 回收 model。
        self._model = None
        self._is_ready = False
